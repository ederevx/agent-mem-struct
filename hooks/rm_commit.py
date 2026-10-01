"""Publish the paths a memory mutation wrote as one commit, and push it.

One owner for turning written shared files into shared history:
`CommitPublisher` stages exactly the paths the mutation reported (never the
rest of the index), commits them with the message the agent supplied - its
prose as written, with the attribution block the conventions require only
when the agent did not carry one - and pushes the shared branch. When
another agent pushed first, the local commit is replayed with `git pull
--rebase --autostash` and the push retried a bounded number of times; a
rebase conflict aborts the replay and is reported rather than forced. A
worktree without an upstream is committed locally and reported as unpushed.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
import time
from pathlib import Path

from rm_support import under

MAX_LINE = 80
GIT_TIMEOUT = 20
PUBLISH_SECONDS = 20
SYNCABLE_TIMEOUT = 5
PUSH_ATTEMPTS = 3
TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
ATTRIBUTION_KEYS = ("Assisted-by", "Signed-off-by")


def syncable(root: Path) -> bool:
    """Whether `root` has a remote and an upstream branch to pull and push.

    A failed or slow check answers no rather than raising: the caller only
    decides between pushing and committing locally, so an unusable answer
    must never abort a memory mutation.
    """
    queries = (
        ("remote",),
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
    )
    for args in queries:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                check=False,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                timeout=SYNCABLE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if completed.returncode != 0:
            return False
        if args == ("remote",) and not completed.stdout.strip():
            return False
    return True


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

    def __init__(self, root: Path, agent: str, model: str = "") -> None:
        self.root = root
        self.agent = agent
        self.model = model
        self.stop: float | None = None

    def publish(self, paths: list[Path], message: str, target: str) -> CommitOutcome:
        """Commit the paths this mutation wrote, then push them."""
        self.stop = time.monotonic() + PUBLISH_SECONDS
        can_push = syncable(self.root)
        staged = self.stageable(paths)
        if not staged:
            return CommitOutcome(note="no shared path of this mutation is tracked by Git.")
        identity = self.identity()
        if identity is None:
            return CommitOutcome(
                conflict=self.conflict(
                    "missing-identity",
                    "the shared worktree has no `user.name`/`user.email` to sign the commit with.",
                    "set both in the shared worktree, then re-call",
                )
            )
        payload, block = self.message(message, target, identity)
        invalid = self.message_error(payload)
        if invalid is not None:
            return CommitOutcome(
                conflict=self.conflict(
                    "bad-message", invalid, "correct `commit_message` and re-call"
                )
            )
        added = self.git("add", "--", *staged)
        if added.returncode != 0:
            return CommitOutcome(
                conflict=self.conflict(
                    "git-failed",
                    f"staging {', '.join(staged)} failed: {self.detail(added)}",
                    "resolve the shared worktree state, then re-call",
                )
            )
        commit = self.git("commit", "-F", "-", "--", *staged, input_text=payload)
        if commit.returncode != 0:
            if "nothing to commit" in commit.stdout + commit.stderr:
                return CommitOutcome(note="the shared worktree already held this state.")
            return CommitOutcome(
                conflict=self.conflict(
                    "git-failed",
                    f"committing {', '.join(staged)} failed: {self.detail(commit)}",
                    "commit the shared paths by hand before the turn ends",
                )
            )
        sha = self.head()
        if not can_push:
            return CommitOutcome(
                committed=True,
                sha=sha,
                note=f"committed {sha[:12]} locally; the shared worktree has no upstream to push to.",
            )
        return self.push(sha)

    def push(self, sha: str) -> CommitOutcome:
        """Push the local commit, replaying it onto the shared tip if it raced."""
        for _ in range(PUSH_ATTEMPTS):
            if self.git("push").returncode == 0:
                published = self.head() or sha
                return CommitOutcome(
                    committed=True, pushed=True, sha=published,
                    note=f"committed {published[:12]} and pushed.",
                )
            if self.expired():
                return CommitOutcome(
                    committed=True, sha=self.head() or sha,
                    conflict=self.conflict(
                        "shared-unpushed",
                        f"commit {sha[:12]} is local: publishing it did not finish within "
                        f"{PUBLISH_SECONDS}s.",
                        "push the local shared commit before the turn ends",
                    ),
                )
            replay = self.git("pull", "--rebase", "--autostash")
            if replay.returncode != 0:
                self.abort_replay()
                return CommitOutcome(
                    committed=True,
                    sha=self.head() or sha,
                    conflict=self.conflict(
                        "shared-diverged",
                        f"commit {sha[:12]} is local: replaying it onto the shared tip "
                        f"failed ({self.detail(replay)}).",
                        "reconcile the shared worktree and push the local commit before the turn ends",
                    ),
                )
        return CommitOutcome(
            committed=True,
            sha=self.head() or sha,
            conflict=self.conflict(
                "shared-unpushed",
                f"commit {sha[:12]} is local: the push was rejected {PUSH_ATTEMPTS} times.",
                "push the local shared commit before the turn ends",
            ),
        )

    def abort_replay(self) -> None:
        """Leave no rebase in progress, whether or not this attempt started one."""
        if not self.replaying():
            return
        self.git("rebase", "--abort", budget=5.0)

    def replaying(self) -> bool:
        path = self.git("rev-parse", "--git-path", "rebase-merge").stdout.strip()
        return bool(path) and (self.root / path).exists()

    def message(self, message: str, target: str, identity: tuple[str, str]) -> tuple[str, str]:
        """The commit payload: the agent's own message, plus the trailers it needs.

        The message is the agent's discretion: its prose is committed as
        written, and a trailer block it already carries is kept as-is. Only
        the attribution block the conventions require is added when absent.
        """
        body, given = self.split_trailers(message.strip() or f"memory_update: {target}")
        block = self.complete_trailers(given, identity)
        return f"{self.wrap(body)}\n\n{block}\n", block

    def complete_trailers(self, given: str, identity: tuple[str, str]) -> str:
        """The agent's trailer block, in the required attribution order.

        The agent's own values win; only a missing one is supplied, and the
        pair always closes the block in its mandated order.
        """
        defaults = dict(zip(ATTRIBUTION_KEYS, self.trailers(identity).splitlines()))
        given_lines = given.splitlines()
        keys = [line.split(":", 1)[0].strip() if ":" in line else "" for line in given_lines]
        rest = [line for line, key in zip(given_lines, keys) if key not in defaults]
        return "\n".join(
            [
                *rest,
                *[
                    next((line for line, key in zip(given_lines, keys) if key == wanted), defaults[wanted])
                    for wanted in ATTRIBUTION_KEYS
                ],
            ]
        )

    def split_trailers(self, text: str) -> tuple[str, str]:
        """The agent's prose, and the attribution block it already ends with."""
        lines = text.splitlines()
        start = len(lines)
        while start and (not lines[start - 1].strip() or TRAILER_LINE_RE.match(lines[start - 1].strip())):
            start -= 1
        block = "\n".join(line.strip() for line in lines[start:] if line.strip())
        keys = {line.split(":", 1)[0] for line in block.splitlines() if ":" in line}
        if block and keys & set(ATTRIBUTION_KEYS):
            return "\n".join(lines[:start]).strip(), block
        return text, ""

    def trailers(self, identity: tuple[str, str]) -> str:
        attribution = f"Assisted-by: {self.agent}" + (f":{self.model}" if self.model else "")
        return f"{attribution}\nSigned-off-by: {identity[0]} <{identity[1]}>"

    def message_error(self, payload: str) -> str | None:
        """Lint the payload the way the commit conventions require before committing."""
        parsed = self.git("interpret-trailers", "--parse", input_text=payload)
        if parsed.returncode != 0 or not parsed.stdout.strip():
            return "the prepared commit message does not carry a parseable trailer block."
        if not payload.rstrip().endswith(parsed.stdout.strip()):
            return "the prepared commit message does not end in its own trailer block."
        for line in payload.splitlines():
            if len(line) > MAX_LINE and " " in line[:MAX_LINE]:
                return f"the prepared commit message has a wrappable line over {MAX_LINE} columns."
        return None

    def wrap(self, text: str) -> str:
        paragraphs: list[str] = []
        for block in re.split(r"\n\s*\n", text.strip()):
            lines: list[str] = []
            for line in block.splitlines():
                stripped = line.strip()
                if stripped:
                    lines.extend(
                        textwrap.wrap(
                            stripped, width=MAX_LINE,
                            break_long_words=False, break_on_hyphens=False,
                        )
                    )
            paragraphs.append("\n".join(lines))
        return "\n\n".join(paragraphs)

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
            for entry in self.git("ls-files", "-z", "--", *relative).stdout.split("\0")
            if entry
        ]
        return [
            name
            for name in relative
            if (root / name).exists()
            or any(entry == name or entry.startswith(name + "/") for entry in tracked)
        ]

    def identity(self) -> tuple[str, str] | None:
        name = self.git("config", "user.name").stdout.strip()
        email = self.git("config", "user.email").stdout.strip()
        return (name, email) if name and email else None

    def conflict(self, code: str, message: str, fix: str) -> dict[str, str]:
        return {"code": code, "message": message, "fix": fix}

    def detail(self, completed: subprocess.CompletedProcess[str]) -> str:
        text = (completed.stderr or completed.stdout or "").strip()
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else f"git exited {completed.returncode}"

    def head(self) -> str:
        return self.git("rev-parse", "HEAD", budget=5.0).stdout.strip()

    def expired(self) -> bool:
        return self.stop is not None and time.monotonic() >= self.stop

    def remaining(self) -> float:
        if self.stop is None:
            return GIT_TIMEOUT
        return min(GIT_TIMEOUT, max(1.0, self.stop - time.monotonic()))

    def git(
        self, *args: str, input_text: str | None = None, budget: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.root), *args],
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                timeout=budget if budget is not None else self.remaining(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess([*args], 1, "", str(exc))
