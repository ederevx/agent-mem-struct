"""Publish the paths a memory mutation wrote as one commit, and push it.

One owner for turning written shared files into shared history:
`CommitPublisher` stages exactly the paths the mutation reported (never the
rest of the index), commits them under the message the caller wrote and
nothing else, and pushes the shared branch. It never composes, rewrites, or
annotates that message, and assumes no convention about its content. When
another agent pushed first, the local commit is replayed with `git pull
--rebase --autostash` and the push retried inside a fixed budget; a failed
replay is aborted and reported rather than forced. A worktree with no remote
or no upstream branch is detected and committed locally instead.
"""
from __future__ import annotations

import time
from pathlib import Path

from rm_git import GitWorktree
from rm_support import under

PUBLISH_SECONDS = 15.0
PUSH_ATTEMPTS = 3


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

    def publish(self, paths: list[Path], message: str) -> CommitOutcome:
        """Commit the paths this mutation wrote under the caller's own message."""
        self.stop = time.monotonic() + PUBLISH_SECONDS
        can_push = self.worktree.has_sync_target()
        staged = self.stageable(paths)
        if not staged:
            return CommitOutcome(note="no shared path of this mutation is tracked by Git.")
        if not message.strip():
            return CommitOutcome(
                note="no `commit_message` was given, so the written shared paths were "
                "left uncommitted.",
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
        if not can_push:
            return CommitOutcome(
                committed=True,
                sha=sha,
                note=f"committed {sha[:12]} locally; the shared worktree has no upstream to push to.",
            )
        return self.push(sha)

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

    def push(self, sha: str) -> CommitOutcome:
        """Push the local commit, replaying it onto the shared tip if it raced."""
        for _ in range(PUSH_ATTEMPTS):
            if self.worktree.run("push").returncode == 0:
                published = self.worktree.head() or sha
                return CommitOutcome(
                    committed=True,
                    pushed=True,
                    sha=published,
                    note=f"committed {published[:12]} and pushed.",
                )
            if self.expired():
                return self.unpushed(
                    sha, f"publishing it did not finish within {PUBLISH_SECONDS:g}s."
                )
            replay = self.worktree.run("pull", "--rebase", "--autostash")
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
