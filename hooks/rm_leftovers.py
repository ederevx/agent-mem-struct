"""The read-only session-start leftovers report.

One owner for the scan behind the session-start notice: `LeftoverReport`
counts, for every repository in the private ledger, the local-only commits no
remote carries, and lists the stale immediate children of the scratch root.
It writes nothing; it prunes clean or missing ledger repositories through
`rm_ledger.RepoLedger`, and reports its own incompleteness instead of
guessing when its fixed budget runs out.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from rm_git import GitWorktree
from rm_ledger import RepoLedger

LEFTOVER_SECONDS = 2.0
LEFTOVER_MAX_SCRATCH = 10
LEFTOVER_SAMPLES = 3
SCRATCH_MIN_AGE = 24 * 60 * 60
HEADER = "LEFTOVERS from earlier sessions (report only; resolve them or tell the user):"


class LeftoverReport:
    """The leftovers block for one ledger, or an empty string when clean."""

    def __init__(
        self, scratch_root: Path | None = None, budget: float = LEFTOVER_SECONDS
    ) -> None:
        self.scratch_root = scratch_root
        self.budget = budget
        self.stop: float | None = None
        self.incomplete = ""

    def text(self, ledger: RepoLedger) -> str:
        """The report block, or an empty string when nothing is left over."""
        self.stop = time.monotonic() + self.budget
        self.incomplete = ""
        lines = self._repos(ledger)
        lines.extend(self._scratch())
        if self.incomplete:
            lines.append(f"- leftover check incomplete: {self.incomplete}")
        if not lines:
            return ""
        return HEADER + "\n" + "\n".join(lines)

    # -- ledger repositories -------------------------------------------------

    def _repos(self, ledger: RepoLedger) -> list[str]:
        lines: list[str] = []
        clean: list[Path] = []
        for entry in ledger.entries():
            if self._expired():
                self.incomplete = self.incomplete or "the time budget ran out"
                break
            repo = Path(entry["path"])
            if not repo.is_dir():
                clean.append(repo)
                continue
            listing = self._local_only(repo)
            if listing is None:
                if self.incomplete:
                    break
                clean.append(repo)
                continue
            shas = listing
            if not shas:
                clean.append(repo)
                continue
            samples: list[str] = []
            for sha in shas[:LEFTOVER_SAMPLES]:
                if self._expired():
                    self.incomplete = self.incomplete or "the time budget ran out"
                    break
                samples.append(self._sample(repo, sha))
            lines.append(self._repo_line(repo, len(shas), samples))
        ledger.prune(clean)
        return lines

    def _local_only(self, repo: Path) -> list[str] | None:
        """The local-only SHAs of this repo, [] when clean, None when unreadable."""
        worktree = self._worktree(repo)
        result = worktree.run("rev-list", "--branches", "--not", "--remotes")
        if result.returncode != 0:
            if "did not finish within" in worktree.detail(result):
                self.incomplete = self.incomplete or "the time budget ran out"
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _sample(self, repo: Path, sha: str) -> str:
        result = self._worktree(repo).run("show", "-s", "--format=%s", sha)
        subject = result.stdout.strip() if result.returncode == 0 else ""
        return f"{sha[:12]} {subject}".rstrip()

    @staticmethod
    def _repo_line(repo: Path, count: int, samples: list[str]) -> str:
        noun = "commit" if count == 1 else "commits"
        return (
            f"- {repo}: {count} local-only {noun} not on any remote "
            f"({'; '.join(samples)})"
        )

    def _worktree(self, repo: Path) -> GitWorktree:
        return GitWorktree(repo, window=self._remaining)

    # -- scratch root --------------------------------------------------------

    def _scratch(self) -> list[str]:
        root = self.scratch_root
        if root is None:
            root = Path(os.environ.get("AMS_SCRATCH_ROOT") or "~/tmp").expanduser()
        try:
            children = list(root.iterdir())
        except OSError:
            return []
        now = time.time()
        stale: list[tuple[float, str]] = []
        for child in children:
            if self._expired():
                self.incomplete = self.incomplete or "the time budget ran out"
                break
            try:
                info = child.stat()
            except OSError:
                continue
            age = now - info.st_mtime
            if age > SCRATCH_MIN_AGE:
                stale.append((age, child.name))
        stale.sort(key=lambda item: item[0], reverse=True)
        lines = [
            self._scratch_line(root, age, name)
            for age, name in stale[:LEFTOVER_MAX_SCRATCH]
        ]
        if len(stale) > LEFTOVER_MAX_SCRATCH:
            lines.append(f"- (+{len(stale) - LEFTOVER_MAX_SCRATCH} more)")
        return lines

    @staticmethod
    def _scratch_line(root: Path, age: float, name: str) -> str:
        days = int(age // SCRATCH_MIN_AGE)
        return (
            f"- {root / name}: {days} days old (may belong to another agent; "
            "never delete another agent's directory)"
        )

    # -- budget --------------------------------------------------------------

    def _expired(self) -> bool:
        return self.stop is not None and time.monotonic() >= self.stop

    def _remaining(self) -> float:
        if self.stop is None:
            return self.budget
        return max(0.1, self.stop - time.monotonic())
