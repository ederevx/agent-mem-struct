"""Mechanical commit-message linting for the root-memory hook.

One owner for the message convention the protocol states: `MessageLint`
reports the mechanical violations of one commit message - column width, the
single blank line before the final trailer block, that block's agreement with
`git interpret-trailers`, trailer-like lines outside it, indented lines in it
or the paragraph above it, `\\xNN` escapes of printable ASCII, and the running
agent's own model on its `Assisted-by:` line - and `PushLint` applies it to
the commits a `git push` would publish. Both are read-only: nothing here
stages, commits, or writes, git stays behind `rm_git.GitWorktree`, and the
push scan is one `git log` inside a fixed window.

A push made inside a script the agent runs is not a `git push` tool call, so
it is never detected here and never linted.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from rm_git import GitWorktree

MAX_COLS = 80
PUSH_MAX_COMMITS = 200
PUSH_SECONDS = 3.0
TRAILER_PREFIXES = (
    "Source:",
    "Reference:",
    "Change-Id:",
    "Assisted-by:",
    "Signed-off-by:",
    "Co-authored-by:",
    "Generated-by:",
    "Reviewed-by:",
)
PRINTABLE_ESCAPE_RE = re.compile(r"(?i)\\x(2[0-9a-f]|[3-6][0-9a-f]|7[0-9a-e])")
ATTRIBUTION_RE = re.compile(r"^Assisted-by:[ \t]*(?P<value>.*)$")
MODEL_FREE_ATTRIBUTION = "LLM"


class MessageLint:
    """The mechanical violations of one commit message, as report lines."""

    def __init__(self, worktree: GitWorktree | None = None) -> None:
        self.worktree = worktree

    def violations(self, message: str, *, agent: str, model: str) -> list[str]:
        """Every mechanical violation of this message, in reading order."""
        lines = self.message_lines(message)
        block_start = self.trailer_block_start(lines)
        parsed = self.parsed_trailers(message)
        findings: list[str] = []
        findings.extend(self._columns(lines, block_start))
        findings.extend(self._trailer_spacing(lines, block_start))
        findings.extend(self._trailer_agreement(lines, block_start, parsed))
        findings.extend(self._trailer_outside(lines, block_start))
        findings.extend(self._indented(lines, block_start))
        findings.extend(self._escapes(lines))
        findings.extend(self._attribution(lines, agent, model))
        return findings

    @staticmethod
    def message_lines(message: str) -> list[str]:
        """The message lines with its trailing blank lines removed."""
        lines = message.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    @staticmethod
    def trailer_like(line: str) -> bool:
        return any(line.startswith(prefix) for prefix in TRAILER_PREFIXES)

    @classmethod
    def trailer_block_start(cls, lines: list[str]) -> int:
        """The first line of the final contiguous trailer-like block."""
        end = len(lines)
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        start = end
        while start > 0 and cls.trailer_like(lines[start - 1]):
            start -= 1
        return start if start < end else end

    def parsed_trailers(self, message: str) -> list[str]:
        """What `git interpret-trailers --parse` returns, or an empty list."""
        if self.worktree is None:
            return []
        result = self.worktree.run(
            "interpret-trailers", "--parse", input_text=message.rstrip("\n") + "\n"
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    # -- checks --------------------------------------------------------------

    @staticmethod
    def _columns(lines: list[str], block_start: int) -> list[str]:
        findings: list[str] = []
        for number, line in enumerate(lines[:block_start], start=1):
            if len(line) <= MAX_COLS:
                continue
            if any(len(token) > MAX_COLS for token in line.split()):
                continue
            findings.append(f"line {number} is {len(line)} cols with no unbreakable token")
        return findings

    def _trailer_spacing(self, lines: list[str], block_start: int) -> list[str]:
        findings: list[str] = []
        if block_start < len(lines):
            preceding = lines[block_start - 1] if block_start else ""
            if (
                not block_start
                or preceding.strip()
                or (block_start >= 2 and not lines[block_start - 2].strip())
            ):
                findings.append("the final trailer block needs exactly one blank line before it")
        for index, line in enumerate(lines):
            if line.strip():
                continue
            before = self._content_before(lines, index - 1)
            after = self._content_after(lines, index + 1)
            if before and after and self.trailer_like(before) and self.trailer_like(after):
                findings.append(f"blank line {index + 1} sits between two trailer-like lines")
        return findings

    def _trailer_agreement(
        self, lines: list[str], block_start: int, parsed: list[str]
    ) -> list[str]:
        if self.worktree is None:
            return []
        block = lines[block_start:] if block_start < len(lines) else []
        if not block:
            return []
        if parsed == block:
            return []
        return ["the final trailer block is not what git interpret-trailers parsed"]

    def _trailer_outside(self, lines: list[str], block_start: int) -> list[str]:
        findings: list[str] = []
        for index, line in enumerate(lines):
            if index < block_start and self.trailer_like(line):
                findings.append(
                    f"trailer-like line {index + 1} is outside the final trailer block"
                )
        return findings

    def _indented(self, lines: list[str], block_start: int) -> list[str]:
        if block_start >= len(lines):
            return []
        paragraph: list[int] = []
        index = block_start - 1
        while index >= 0 and not lines[index].strip():
            index -= 1
        while index >= 0 and lines[index].strip():
            paragraph.append(index)
            index -= 1
        findings: list[str] = []
        for position in [*reversed(paragraph), *range(block_start, len(lines))]:
            if lines[position][:1] in (" ", "\t"):
                findings.append(
                    f"line {position + 1} starts with whitespace inside or above "
                    "the final trailer block"
                )
        return findings

    @staticmethod
    def _escapes(lines: list[str]) -> list[str]:
        findings: list[str] = []
        for number, line in enumerate(lines, start=1):
            match = PRINTABLE_ESCAPE_RE.search(line)
            if match:
                findings.append(
                    f"line {number} uses {match.group(0)}, an escape of printable ASCII"
                )
        return findings

    @staticmethod
    def _attribution(lines: list[str], agent: str, model: str) -> list[str]:
        if not agent or not model:
            return []
        findings: list[str] = []
        prefix = f"{agent}:"
        for number, line in enumerate(lines, start=1):
            match = ATTRIBUTION_RE.match(line)
            if not match:
                continue
            value = match.group("value").strip()
            if value == MODEL_FREE_ATTRIBUTION or not value.startswith(prefix):
                continue
            actual = value[len(prefix):].strip()
            if actual != model:
                findings.append(
                    f"line {number} names model {actual!r}, not PI_MODEL {model!r}"
                )
        return findings

    @staticmethod
    def _content_before(lines: list[str], index: int) -> str:
        while index >= 0 and not lines[index].strip():
            index -= 1
        return lines[index] if index >= 0 else ""

    @staticmethod
    def _content_after(lines: list[str], index: int) -> str:
        while index < len(lines) and not lines[index].strip():
            index += 1
        return lines[index] if index < len(lines) else ""


class PushLintReport:
    """What a push lint found: the blocked commit lines, and any gap."""

    def __init__(
        self, blocked: list[str] | None = None, incomplete: str = ""
    ) -> None:
        self.blocked = blocked or []
        self.incomplete = incomplete


class PushLint:
    """Lints the commits one `git push` would publish, read-only."""

    def __init__(self, root: Path, agent: str = "", model: str | None = None) -> None:
        self.root = Path(root)
        self.agent = agent
        self.model = os.environ.get("PI_MODEL", "") if model is None else model
        self.stop: float | None = None
        self.worktree = GitWorktree(self.root, window=self.remaining)
        self.lint = MessageLint(self.worktree)

    def check(self, remote: str, sources: list[str]) -> PushLintReport:
        """Lint the commits these sources would add to this remote.

        Only commits authored by the local `user.email` are linted, the scan
        is one `git log` inside `PUSH_SECONDS`, and more than
        `PUSH_MAX_COMMITS` candidates is an incomplete check, never a block.
        """
        report = PushLintReport()
        self.stop = time.monotonic() + PUSH_SECONDS
        target = remote or self.default_remote()
        if not target:
            report.incomplete = "the push remote could not be resolved"
            return report
        email = self.worktree.run("config", "user.email").stdout.strip()
        if not email:
            report.incomplete = "the local user.email could not be read"
            return report
        result = self.worktree.run(
            "log", "--format=%H%x00%ae%x00%B%x1e", *sources, "--not",
            f"--remotes={target}",
        )
        if result.returncode != 0:
            report.incomplete = f"git log did not answer: {self.worktree.detail(result)}"
            return report
        records = [item for item in result.stdout.split("\x1e") if item.strip("\n")]
        if len(records) > PUSH_MAX_COMMITS:
            report.incomplete = (
                f"more than {PUSH_MAX_COMMITS} commits would be pushed"
            )
            return report
        for record in records:
            sha, _, rest = record.partition("\x00")
            author, _, message = rest.partition("\x00")
            if author.strip() != email:
                continue
            subject = self.subject(message)
            for violation in self.lint.violations(
                message, agent=self.agent, model=self.model
            ):
                report.blocked.append(f"{sha.strip()[:12]} {subject}: {violation}")
        return report

    def default_remote(self) -> str:
        """The remote of this worktree's upstream branch, or an empty string."""
        upstream = self.worktree.run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        )
        name = upstream.stdout.strip() if upstream.returncode == 0 else ""
        return name.split("/", 1)[0] if "/" in name else ""

    @staticmethod
    def subject(message: str) -> str:
        lines = message.split("\n")
        return lines[0].strip() if lines and lines[0].strip() else "(no subject)"

    def remaining(self) -> float:
        if self.stop is None:
            return PUSH_SECONDS
        return max(0.1, self.stop - time.monotonic())
