"""Attest one memory publish from the worktree it landed in.

One owner for the self-check a publish runs after it commits:
`PublishAttestation` reads the worktree through `rm_git.GitWorktree` and
reports whether the changed paths are clean, the commit sits on the
upstream it was pushed to, the committed paired logs carry the lines the
mutation appended, and the agent's own message shape is conventional. It is
strictly read-only: it never stages, commits, pushes, or writes, and returns
the report lines and conflicts for the caller to surface. Its git reads share
one fixed budget, and a worktree git cannot answer for is reported as an
incomplete check rather than a clean one. A durability failure is a conflict
the agent must fix; a message-shape deviation is only an advisory line,
because the tool assumes no convention about the message.
"""
from __future__ import annotations

import time
from pathlib import Path

from rm_git import GitWorktree

ATTEST_SECONDS = 8.0
MAX_COLS = 80
TRAILER_PREFIXES = (
    "Source:",
    "Change-Id:",
    "Assisted-by:",
    "Signed-off-by:",
    "Co-authored-by:",
    "Generated-by:",
    "Reviewed-by:",
)


class Attestation:
    """The report lines and the conflicts of one publish attestation."""

    def __init__(
        self,
        lines: list[str] | None = None,
        conflicts: list[dict[str, str]] | None = None,
    ) -> None:
        self.lines = lines or []
        self.conflicts = conflicts or []


class PublishAttestation:
    """Double-checks one committed shared publish through its worktree."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.stop: float | None = None
        self.worktree = GitWorktree(self.root, window=self.remaining)

    def attest(
        self, *, sha: str, changed: list[Path], appended: list[tuple[Path, str]]
    ) -> Attestation:
        """Attest the publish of `changed` and the logs in `appended` at `sha`."""
        report = Attestation()
        self.stop = time.monotonic() + ATTEST_SECONDS
        if not self._reached(sha):
            report.lines.append(
                f"attention: {self.root} was not verified: git did not answer within "
                f"{ATTEST_SECONDS:g}s"
            )
            return report
        sync = self._sync_target()
        self._check_clean(report, sha, changed, sync)
        self._check_sync(report, sha, sync)
        self._check_log(report, appended)
        self._check_message(report, sha)
        return report

    # -- checks --------------------------------------------------------------

    def _check_clean(self, report: Attestation, sha: str, changed: list[Path],
                    sync: tuple[bool, str, str]) -> None:
        leftover = self._dirty_leftovers(changed)
        if leftover:
            report.conflicts.append({
                "code": "unverified-dirty",
                "message": "still dirty after the publish: " + ", ".join(leftover),
                "fix": "commit or revert them before the turn ends",
            })
            return
        has_target, upstream, tip = sync
        if has_target and tip == sha:
            report.lines.append(
                f"verified: {self.root} clean, HEAD {sha[:12]} == {upstream}"
            )
        elif not has_target:
            report.lines.append(
                f"verified: {self.root} clean, HEAD {sha[:12]}; no upstream to compare"
            )

    def _check_sync(self, report: Attestation, sha: str,
                   sync: tuple[bool, str, str]) -> None:
        has_target, upstream, tip = sync
        if not has_target or tip == sha:
            return
        detail = f" (tip {tip[:12]})" if tip else ""
        report.conflicts.append({
            "code": "unverified-unpushed",
            "message": f"commit {sha[:12]} is not at {upstream or '@{u}'}{detail}",
            "fix": "push the shared commit before the turn ends",
        })

    def _check_log(self, report: Attestation,
                  appended: list[tuple[Path, str]]) -> None:
        if not appended:
            return
        missing: list[str] = []
        for path, text in appended:
            relative = self._relative(path)
            last = self._last_content_line(text)
            blob = self.worktree.head_blob(relative) if relative else None
            if last is None or blob is None or last not in blob:
                missing.append(relative or str(path))
        if missing:
            report.conflicts.append({
                "code": "unverified-log",
                "message": "HEAD is missing the appended line of: " + ", ".join(missing),
                "fix": "re-commit the paired log before the turn ends",
            })
        else:
            report.lines.append("verified: paired log carries the appended lines")

    def _check_message(self, report: Attestation, sha: str) -> None:
        message = self._message_body(sha)
        lines = message.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        subject = lines[0] if lines else ""
        attention: list[str] = []
        if not subject.strip():
            attention.append("attention: message has no subject")
        if not any(line.strip() for line in lines[1:]):
            attention.append("attention: message has no body (subject only)")
        attention.extend(self._lint_columns(lines))
        attention.extend(self._lint_trailers(message))
        if attention:
            report.lines.extend(attention)
            return
        body = [
            line
            for line in lines[1:self._trailer_block_start(lines)]
            if line.strip()
        ]
        trailers = self._parsed_trailers(message)
        report.lines.append(
            f"verified: message conforms (subject: {len(subject)} cols, "
            f"body: {len(body)} line{'s' if len(body) != 1 else ''}, "
            f"trailers: {len(trailers)})"
        )

    def _lint_columns(self, lines: list[str]) -> list[str]:
        flagged: list[str] = []
        for number, line in enumerate(lines, start=1):
            if len(line) <= MAX_COLS:
                continue
            if any(len(token) > MAX_COLS for token in line.split()):
                continue
            flagged.append(
                f"attention: line {number} is {len(line)} cols with no unbreakable token"
            )
        return flagged

    def _lint_trailers(self, message: str) -> list[str]:
        lines = message.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        start = self._trailer_block_start(lines)
        parsed = set(self._parsed_trailers(message))
        flagged: list[str] = []
        for index, line in enumerate(lines):
            if not self._trailer_like(line):
                continue
            if index < start or line not in parsed:
                flagged.append(
                    f"attention: trailer-like line {index + 1} is "
                    "outside the final trailer block"
                )
        return flagged

    # -- read-only git views -------------------------------------------------

    def _reached(self, sha: str) -> bool:
        """Whether git answered for this worktree and knows the commit."""
        probe = self.worktree.run("cat-file", "-e", f"{sha}^{{commit}}")
        return probe.returncode == 0

    def _sync_target(self) -> tuple[bool, str, str]:
        """Whether an upstream exists, its name, and its tip at this moment."""
        if not self.worktree.has_sync_target():
            return (False, "", "")
        upstream = self.worktree.run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        ).stdout.strip()
        tip = self.worktree.run("rev-parse", "@{u}").stdout.strip()
        return (True, upstream, tip)

    def _dirty_leftovers(self, changed: list[Path]) -> list[str]:
        dirty = set(self.worktree.dirty_paths())
        if not dirty:
            return []
        leftover: list[str] = []
        for path in changed:
            relative = self._relative(path)
            if relative and any(
                name == relative or name.startswith(relative + "/") for name in dirty
            ):
                leftover.append(relative)
        return leftover

    def _message_body(self, sha: str) -> str:
        result = self.worktree.run("show", "-s", "--format=%B", sha)
        return result.stdout if result.returncode == 0 else ""

    def _parsed_trailers(self, message: str) -> list[str]:
        result = self.worktree.run(
            "interpret-trailers", "--parse", input_text=message.rstrip("\n") + "\n"
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _trailer_block_start(self, lines: list[str]) -> int:
        """The first line of the final contiguous trailer-like block."""
        end = len(lines)
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        start = end
        while start > 0 and self._trailer_like(lines[start - 1]):
            start -= 1
        return start if start < end else end

    def _trailer_like(self, line: str) -> bool:
        return any(line.startswith(prefix) for prefix in TRAILER_PREFIXES)

    def _relative(self, path: Path) -> str | None:
        try:
            return path.resolve(strict=False).relative_to(
                self.root.resolve(strict=False)
            ).as_posix()
        except (OSError, ValueError):
            return None

    def _last_content_line(self, text: str) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else None

    def remaining(self) -> float:
        """The seconds left of this attestation's own budget."""
        if self.stop is None:
            return self.worktree.budget
        return max(0.5, self.stop - time.monotonic())
