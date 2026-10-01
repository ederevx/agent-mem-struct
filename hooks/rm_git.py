"""Run bounded, non-interactive git commands in one worktree.

One owner for invoking git on behalf of the root-memory hook: `GitWorktree`
pins the credential prompt off, applies a budget to every call, and turns a
timed-out or missing git into a failed result instead of an exception, so a
hook always reaches a report. The shared fast-forward, the unmerged-index
check, and publishing a mutation all go through this class rather than
spelling out their own subprocess call.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

DEFAULT_BUDGET = 20.0
QUERY_BUDGET = 5.0


class GitWorktree:
    """One worktree's git invocations, bounded and never interactive.

    `window` lets an owner that has its own deadline supply the budget for
    every call it makes, evaluated per call so a sequence of calls shares
    one window rather than each taking a fresh one.
    """

    def __init__(
        self, root: Path, budget: float = DEFAULT_BUDGET, window: Callable[[], float] | None = None
    ) -> None:
        self.root = root
        self.budget = budget
        self.window = window

    def limit(self, budget: float | None = None, default: float | None = None) -> float:
        """The seconds one call may take: explicit, else this owner's window.

        `default` caps a query when no window applies, so a caller without a
        deadline of its own still bound its cheap checks.
        """
        if budget is not None:
            return budget
        if self.window is not None:
            return max(0.5, self.window())
        return self.budget if default is None else default

    def run(
        self, *args: str, input_text: str | None = None, budget: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run one git subcommand, or return its failure as a result."""
        limit = self.limit(budget)
        try:
            return subprocess.run(
                ["git", "-C", str(self.root), *args],
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return self.failed(args, f"git {args[0]} did not finish within {limit:g}s")
        except OSError as exc:
            return self.failed(args, str(exc))

    def failed(self, args: tuple[str, ...], detail: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([*args], 1, "", detail)

    def detail(self, completed: subprocess.CompletedProcess[str]) -> str:
        """The one line of a failed git result worth reporting."""
        text = (completed.stderr or completed.stdout or "").strip()
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else f"git exited {completed.returncode}"

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").stdout.strip()

    def toplevel(self) -> Path | None:
        """The worktree root Git attributes to this path, or None."""
        result = self.run(
            "rev-parse", "--show-toplevel", budget=self.limit(default=QUERY_BUDGET)
        )
        value = result.stdout.strip()
        return Path(value) if result.returncode == 0 and value else None

    def dirty_paths(self) -> list[str]:
        """Repo-relative paths whose worktree or index differs from HEAD.

        Porcelain v1 with NUL separators so a path can hold any byte; a
        rename or copy entry carries the original path in the following
        field, which is consumed rather than parsed as another path.
        """
        result = self.run(
            "status", "--porcelain", "-z", "--untracked-files=all",
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return []
        fields = result.stdout.split("\0")
        paths: list[str] = []
        index = 0
        while index < len(fields):
            entry = fields[index]
            index += 1
            if not entry:
                continue
            status = entry[:2]
            paths.append(entry[3:])
            if "R" in status or "C" in status:
                index += 1
        return paths

    def head_blob(self, relative: str) -> str | None:
        """The committed content of one repo-relative path at HEAD, or None."""
        result = self.run(
            "show", f"HEAD:{relative}", budget=self.limit(default=QUERY_BUDGET)
        )
        return result.stdout if result.returncode == 0 else None

    def has_sync_target(self, budget: float | None = None) -> bool:
        """Whether this worktree has a remote and an upstream branch to sync."""
        remote = self.run("remote", budget=self.limit(budget, QUERY_BUDGET))
        if remote.returncode != 0 or not remote.stdout.strip():
            return False
        upstream = self.run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
            budget=self.limit(budget, QUERY_BUDGET),
        )
        return upstream.returncode == 0
