"""Publish the paths a memory mutation wrote as one commit, and push it.

One owner for turning written shared files into shared history:
`CommitPublisher` stages exactly the paths the mutation reported (never the
rest of the index), checks the caller's message against the mechanical
convention in `rm_lint.MessageLint`, commits it under that message and
nothing else, and pushes the shared branch. It never composes, rewrites, or
annotates that message. When
another agent pushed first, the local commit is replayed with `git pull
--rebase --autostash` and the push retried inside a fixed budget; a failed
replay is aborted and reported rather than forced. A worktree with no remote
or no upstream branch is detected and committed locally instead.
"""
from __future__ import annotations

import time
from pathlib import Path

from rm_git import GitWorktree
from rm_lint import MessageLint
from rm_support import under

PUBLISH_SECONDS = 15.0
PUSH_ATTEMPTS = 3


def converted(committed: bytes, approved: bytes) -> bool:
    """Whether one side is the other with CRLF line ends, and nothing else.

    The only rewrite `text`/`eol`/`core.autocrlf` performs on its own is the
    CRLF-LF pair, so a lone carriage return stays significant and a clean
    filter that changes any other byte leaves the two unequal.
    """
    return (
        committed.replace(b"\r\n", b"\n") == approved
        or approved.replace(b"\r\n", b"\n") == committed
    )


class CommitOutcome:
    """What became of one mutation's paths: a commit, a push, or a conflict."""

    def __init__(
        self,
        *,
        committed: bool = False,
        pushed: bool = False,
        sha: str = "",
        note: str = "",
        conflict: dict[str, str] | None = None,
    ) -> None:
        self.committed = committed
        self.pushed = pushed
        self.sha = sha
        self.note = note
        self.conflict = conflict


class CommitPublisher:
    """One owner for committing and pushing the shared paths a mutation wrote."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.stop: float | None = None
        self.worktree = GitWorktree(root, window=self.remaining)

    def publish(
        self,
        paths: list[Path],
        message: str,
        *,
        expected: dict[str, bytes | None] | None = None,
        budget_s: float | None = None,
    ) -> CommitOutcome:
        """Commit the paths this mutation wrote under the caller's own message.

        `expected` is the approved snapshot for this worktree - each
        repo-relative path and the bytes (None for a deletion) the judge saw -
        and nothing is pushed until the commit proves it holds exactly that.
        """
        self.stop = time.monotonic() + (
            PUBLISH_SECONDS if budget_s is None else max(0.5, float(budget_s))
        )
        can_push = self.worktree.has_sync_target()
        staged = self.stageable(paths)
        if not staged:
            return CommitOutcome(note="no shared path of this mutation is tracked by Git.")
        if not message.strip():
            return CommitOutcome(
                note="no `commit_message` was given, so the written shared paths were "
                "left uncommitted.",
            )
        if can_push:
            unpublished = self.unpublished()
            if unpublished:
                # A commit's ancestors travel with it, so publishing on top of
                # a commit that was never published would publish that one too.
                return CommitOutcome(
                    conflict=self.conflict(
                        "shared-unpushed",
                        unpublished,
                        "reconcile the shared worktree so its branch tip is the "
                        "published tip, then retry",
                    )
                )
        violations = MessageLint(self.worktree).violations(message)
        if violations:
            return CommitOutcome(
                conflict=self.conflict(
                    "message-lint",
                    "the commit message breaks the mechanical convention: "
                    + "; ".join(violations),
                    "rewrite commit_message and re-call",
                )
            )
        staged = self.stage(staged)
        if isinstance(staged, CommitOutcome):
            return staged
        commit = self.worktree.run(
            "commit", "-F", "-", "--", *staged, input_text=message.rstrip("\n") + "\n"
        )
        if commit.returncode != 0:
            if "nothing to commit" in commit.stdout + commit.stderr:
                return CommitOutcome(note="the shared worktree already held this state.")
            return CommitOutcome(
                conflict=self.conflict(
                    "git-failed",
                    f"committing {', '.join(staged)} failed: {self.worktree.detail(commit)}",
                    "commit the shared paths by hand before the turn ends",
                )
            )
        sha = self.worktree.head()
        mismatch = self.verify_snapshot(sha, expected)
        if mismatch:
            return self.mismatch(sha, mismatch)
        if not can_push:
            return CommitOutcome(
                committed=True,
                sha=sha,
                note=f"committed {sha[:12]} locally; the shared worktree has no upstream to push to.",
            )
        return self.push(sha, expected)

    def verify_snapshot(
        self, sha: str, expected: dict[str, bytes | None] | None
    ) -> str:
        """How one commit differs from the approved snapshot, or "" when it is it."""
        if expected is None:
            return ""
        changed = self.worktree.changed_paths(sha)
        if changed is None:
            return "the commit's changed paths could not be read back"
        if sorted(changed) != sorted(expected):
            extra = sorted(set(changed) - set(expected))
            missing = sorted(set(expected) - set(changed))
            detail = ", ".join(
                [*(f"unexpected {name}" for name in extra), *(f"unchanged {name}" for name in missing)]
            )
            return f"the commit changed a different path set ({detail})"
        for name, data in expected.items():
            found, entry = self.worktree.tree_entry(sha, name)
            if not found:
                return f"{name} could not be read back from the commit"
            if data is None:
                if entry is not None:
                    return f"{name} was approved for deletion but is still committed"
                continue
            if entry is None:
                return f"{name} is not committed"
            if entry[0] not in ("100644", "100755"):
                return f"{name} is not committed as a regular file"
            readable, committed = self.worktree.object_bytes(entry[1])
            if not readable or committed is None:
                return f"{name} could not be read back from the commit"
            if committed != data:
                rewrite = self.worktree.transformed(name)
                if rewrite is None:
                    return (
                        f"{name} could not be checked: the repository would not "
                        "say whether Git rewrites it"
                    )
                if not rewrite:
                    return f"{name} is not the approved bytes"
                # A line-ending conversion is the one rewrite that leaves the
                # content the judge approved intact. A declared clean filter is
                # not: it can turn approved text into anything, so a commit
                # made under one is refused rather than trusted.
                if not converted(committed, data):
                    return (
                        f"{name} is not the approved bytes; the repository's "
                        "clean filter rewrote them"
                    )
        return ""

    def revision(self, name: str) -> tuple[bool, str | None]:
        """One revision's object id, and whether Git answered at all.

        `(True, None)` is an answer too: Git answered that the revision does not
        exist. `(False, None)` is a query that failed or never came back, which
        is never read as absence.
        """
        result = self.worktree.run("rev-parse", "--verify", "--quiet", name)
        if result.returncode == 0:
            return True, result.stdout.strip() or None
        if result.returncode == 1 and not (result.stderr or "").strip():
            return True, None
        return False, None

    def published_tip(self) -> tuple[bool, str | None]:
        """The tip the shared branch is published at, and whether Git answered.

        `(True, None)` is a branch with nothing published: the upstream is named
        but no tip is available, which proves nothing about the commits the
        branch already holds.
        """
        published_ref = self.worktree.upstream_ref()
        if published_ref is None:
            return False, None
        return self.revision(published_ref)

    def parents(self, sha: str) -> list[str] | None:
        """One commit's parents, or None when Git did not report them."""
        result = self.worktree.run("rev-list", "--parents", "-n", "1", sha)
        if result.returncode != 0:
            return None
        fields = result.stdout.split()
        if not fields or fields[0] != sha:
            return None
        return fields[1:]

    def unpublished(self) -> str:
        """Why the branch holds commits the remote does not, or "" when it does not.

        A push sends the commit and every ancestor, so an earlier commit that
        failed its snapshot check would travel with a later, approved one.
        """
        target = self.worktree.upstream()
        if target is None:
            return ""
        answered, published = self.published_tip()
        if not answered:
            return (
                "Git would not report the published tip of the shared branch, so "
                "whether a push would carry an earlier commit cannot be ruled out"
            )
        answered, head = self.revision("HEAD")
        if not answered:
            return (
                "Git would not report the shared branch's HEAD, so whether a push "
                "would carry an earlier commit cannot be ruled out"
            )
        if head is None:
            return ""
        if published is None:
            return (
                "the shared branch has no published tip, so every commit on it "
                "would be published without having been reviewed against one"
            )
        if head == published:
            return ""
        return (
            "the shared branch holds commits the published tip does not, and a "
            "push would send them all"
        )

    def carries(self, sha: str) -> str:
        """Why publishing `sha` could send an earlier commit too, or "" when it could not.

        The branch can move between the preflight check and this commit - another
        writer may commit in the same worktree - so the commit's own parent is
        what proves it sits directly on the published tip.
        """
        answered, published = self.published_tip()
        if not answered:
            return (
                "Git would not report the published tip of the shared branch, so "
                "whether the push would carry an earlier commit cannot be ruled out"
            )
        parents = self.parents(sha)
        if parents is None:
            return (
                f"Git would not report the parents of commit {sha[:12]}, so whether "
                "the push would carry an earlier commit cannot be ruled out"
            )
        if published is None:
            return (
                "the branch has no published tip, so the push would publish earlier "
                "local history that was never reviewed against one"
            )
        if parents != [published]:
            based = ", ".join(parent[:12] for parent in parents) or "no commit"
            return (
                f"commit {sha[:12]} is based on {based} rather than the published "
                f"tip {published[:12]}"
            )
        return ""

    def carried(self, sha: str, reason: str) -> CommitOutcome:
        """A commit that would publish an unapproved ancestor is never pushed."""
        return CommitOutcome(
            committed=True,
            sha=sha,
            conflict=self.conflict(
                "shared-unpushed",
                f"commit {sha[:12]} holds the approved snapshot, but {reason}; it was "
                "not pushed.",
                "reconcile the shared worktree with the published tip, then "
                "rejudge the change",
            ),
        )

    def mismatch(self, sha: str, mismatch: str) -> CommitOutcome:
        """A local commit that does not hold the approved snapshot is never pushed."""
        return CommitOutcome(
            committed=True,
            sha=sha,
            conflict=self.conflict(
                "judge-publish-mismatch",
                f"commit {sha[:12]} does not hold the approved snapshot: {mismatch}; "
                "it was not pushed.",
                "inspect the shared worktree and rejudge the change before retrying",
            ),
        )

    def stage(self, staged: list[str]) -> list[str] | CommitOutcome:
        """Stage exactly these paths, or report why staging failed."""
        added = self.worktree.run("add", "--", *staged)
        if added.returncode == 0:
            return staged
        return CommitOutcome(
            conflict=self.conflict(
                "git-failed",
                f"staging {', '.join(staged)} failed: {self.worktree.detail(added)}",
                "resolve the shared worktree state, then re-call",
            )
        )

    def push(
        self, sha: str, expected: dict[str, bytes | None] | None = None
    ) -> CommitOutcome:
        """Push the verified local commit, replaying it onto the shared tip if it raced.

        The remote and branch are named explicitly, so a
        `branch.<name>.merge` the environment adds cannot make Git refuse the
        push or the replay as a multi-head one. The exact commit is pushed, not
        whatever `HEAD` happens to name, and a replay that changed the
        approved bytes is reported rather than pushed.
        """
        target = self.worktree.upstream()
        for _ in range(PUSH_ATTEMPTS):
            mismatch = self.verify_snapshot(sha, expected)
            if mismatch:
                return self.mismatch(sha, mismatch)
            carried = self.carries(sha)
            if carried:
                return self.carried(sha, carried)
            if target is None:
                # A bare `git push` would pick its own destination, so a
                # worktree with no upstream keeps its commit local and says so.
                return self.unpushed(
                    sha, "the worktree names no upstream to publish to."
                )
            refspec = (target[0], f"{sha}:{target[1]}")
            if self.worktree.run("push", *refspec).returncode == 0:
                return CommitOutcome(
                    committed=True,
                    pushed=True,
                    sha=sha,
                    note=f"committed {sha[:12]} and pushed.",
                )
            if self.expired():
                return self.unpushed(
                    sha, f"publishing it did not finish within {PUBLISH_SECONDS:g}s."
                )
            replay = self.worktree.run(
                "pull", "--rebase", "--autostash", *target
            )
            if replay.returncode != 0:
                self.abort_replay()
                return CommitOutcome(
                    committed=True,
                    sha=self.worktree.head() or sha,
                    conflict=self.conflict(
                        "shared-diverged",
                        f"commit {sha[:12]} is local: replaying it onto the shared tip "
                        f"failed ({self.worktree.detail(replay)}).",
                        "reconcile the shared worktree and push the local commit "
                        "before the turn ends",
                    ),
                )
            sha = self.worktree.head() or sha
        return self.unpushed(sha, f"the push was rejected {PUSH_ATTEMPTS} times.")

    def unpushed(self, sha: str, reason: str) -> CommitOutcome:
        return CommitOutcome(
            committed=True,
            sha=self.worktree.head() or sha,
            conflict=self.conflict(
                "shared-unpushed",
                f"commit {sha[:12]} is local: {reason}",
                "push the local shared commit before the turn ends",
            ),
        )

    def abort_replay(self) -> None:
        """Leave no rebase in progress, whether or not this attempt started one."""
        if self.replaying():
            self.worktree.run("rebase", "--abort", budget=5.0)

    def replaying(self) -> bool:
        path = self.worktree.run(
            "rev-parse", "--git-path", "rebase-merge", budget=5.0
        ).stdout.strip()
        return bool(path) and (self.root / path).exists()

    def stageable(self, paths: list[Path]) -> list[str]:
        """The mutation's paths inside this worktree, as repo-relative names."""
        root = self.root.resolve(strict=False)
        relative: list[str] = []
        for path in paths:
            if not under(path, self.root):
                continue
            name = path.resolve(strict=False).relative_to(root).as_posix()
            if name and name not in relative:
                relative.append(name)
        if not relative:
            return []
        tracked = [
            entry
            for entry in self.worktree.run("ls-files", "-z", "--", *relative).stdout.split("\0")
            if entry
        ]
        return [
            name
            for name in relative
            if (root / name).exists()
            or any(entry == name or entry.startswith(name + "/") for entry in tracked)
        ]

    def conflict(self, code: str, message: str, fix: str) -> dict[str, str]:
        return {"code": code, "message": message, "fix": fix}

    def expired(self) -> bool:
        return self.stop is not None and time.monotonic() >= self.stop

    def remaining(self) -> float:
        if self.stop is None:
            return self.worktree.budget
        return min(self.worktree.budget, max(0.5, self.stop - time.monotonic()))
