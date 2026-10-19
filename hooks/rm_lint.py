"""Mechanical commit-message linting for the root-memory hook.

One owner for the message convention the protocol states: `MessageLint`
reports the mechanical violations of one commit message - column width, the
single blank line before the final trailer block, that block's agreement with
`git interpret-trailers`, a trailer outside that block, a token outside the
allowed set of `trailer-policy.json`, indented lines in it or the paragraph
above it, `\\xNN` escapes of printable ASCII, and the running agent's own
model on its `Assisted-by:` line - and `PushLint` applies it to the commits a
`git push` would publish. Both are read-only: nothing here
stages, commits, or writes, git stays behind `rm_git.GitWorktree`, and the
push scan is one `git log` inside a fixed window.

A push made inside a script the agent runs is not a `git push` tool call, so
it is never detected here and never linted.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from rm_git import GitWorktree
from rm_trailer_policy import TrailerPolicy

MAX_COLS = 80
PUSH_MAX_COMMITS = 200
PUSH_SECONDS = 3.0
TRAILER_HEADER_RE = re.compile(r"^(?P<token>[A-Za-z0-9][A-Za-z0-9-]*):(?!/)")
TRAILER_UNAVAILABLE = (
    "trailer validation is unavailable: git interpret-trailers did not answer"
)
TRAILER_MALFORMED = "the final trailer block is not what git interpret-trailers parsed"
PRINTABLE_ESCAPE_RE = re.compile(r"(?i)\\x(2[0-9a-f]|[3-6][0-9a-f]|7[0-9a-e])")
ATTRIBUTION_RE = re.compile(r"^Assisted-by:[ \t]*(?P<value>.*)$")
MODEL_FREE_ATTRIBUTION = "LLM"


class MessageLint:
    """The mechanical violations of one commit message, as report lines."""

    def __init__(
        self,
        worktree: GitWorktree | None = None,
        policy: TrailerPolicy | None = None,
        scratch_root: Path | None = None,
    ) -> None:
        self.worktree = worktree
        self.policy = policy if policy is not None else TrailerPolicy.load()
        self.scratch_root = scratch_root

    def violations(self, message: str, *, agent: str, model: str) -> list[str]:
        """Every mechanical violation of this message, in reading order."""
        lines = self.message_lines(message)
        block_start, findings = self.resolve_block(lines, message)
        findings.extend(self._columns(lines, block_start))
        findings.extend(self._trailer_spacing(lines, block_start))
        findings.extend(self._trailer_outside(lines, block_start))
        findings.extend(self._trailer_formatting(lines, block_start))
        findings.extend(self._unknown_trailers(lines, block_start))
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
    def header_token(line: str) -> str:
        """The token of a trailer-shaped line, or an empty string."""
        match = TRAILER_HEADER_RE.match(line)
        return match.group("token") if match else ""

    def trailer_like(self, line: str) -> bool:
        """Whether this line carries a trailer the conventions allow."""
        return self.policy.allows(self.header_token(line))

    def resolve_block(
        self, lines: list[str], message: str
    ) -> tuple[int | None, list[str]]:
        """The first line of the trailer block, and why one was not established.

        The block is the run of trailer-shaped lines ending the message, and
        only git decides that run is a trailer block: it has to parse to
        exactly what the whole message parses to. A run git did not accept, or
        reads differently, is reported instead of being handed to the checks as
        a boundary nobody established.
        """
        start = len(lines)
        while start > 0 and (
            self.header_token(lines[start - 1]) or lines[start - 1][:1] in (" ", "\t")
        ):
            start -= 1
        if start >= len(lines) or start == 0:
            return len(lines), []
        parsed = self.parsed_trailers(message)
        if parsed is None:
            return None, [TRAILER_UNAVAILABLE]
        block = lines[start:]
        if parsed == block:
            return start, []
        alone = self.parsed_trailers("subject\n\n" + "\n".join(block) + "\n")
        if alone != parsed:
            return None, [TRAILER_MALFORMED]
        return (start, []) if parsed else (len(lines), [])

    def parsed_trailers(self, message: str) -> list[str] | None:
        """What `git interpret-trailers --parse` returns, or None if it did not.

        No repository or host configuration reaches this call: it runs in a
        directory that is no worktree, outside system and global configuration,
        with only the parser settings below, so a `trailer.<name>.key` alias
        cannot rename a token behind the lint's back.
        """
        if self.worktree is None:
            return None
        with tempfile.TemporaryDirectory(
            prefix="rm-trailer-", dir=self.scratch_root
        ) as scratch:
            result = self.worktree.run(
                "-c",
                "trailer.where=end",
                "-c",
                "trailer.separators=:",
                "-c",
                "trailer.ifexists=addIfDifferent",
                "interpret-trailers",
                "--parse",
                input_text=message.rstrip("\n") + "\n",
                cwd=Path(scratch),
                env=self.parser_env(Path(scratch).parent),
            )
        if result.returncode != 0:
            return None
        return [line for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def parser_env(ceiling: Path) -> dict[str, str]:
        """The parser's environment: no repository or git configuration."""
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
            and name
            not in (
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_PARAMETERS",
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_COMMON_DIR",
            )
        }
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
        return env

    # -- checks --------------------------------------------------------------

    @staticmethod
    def _columns(lines: list[str], block_start: int | None) -> list[str]:
        findings: list[str] = []
        end = len(lines) if block_start is None else block_start
        for number, line in enumerate(lines[:end], start=1):
            if len(line) <= MAX_COLS:
                continue
            if any(len(token) > MAX_COLS for token in line.split()):
                continue
            findings.append(f"line {number} is {len(line)} cols with no unbreakable token")
        return findings

    def _trailer_spacing(self, lines: list[str], block_start: int | None) -> list[str]:
        findings: list[str] = []
        if block_start is not None and block_start < len(lines):
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

    def _trailer_outside(self, lines: list[str], block_start: int | None) -> list[str]:
        findings: list[str] = []
        end = len(lines) if block_start is None else block_start
        for index, line in enumerate(lines[:end]):
            if self.trailer_like(line):
                findings.append(
                    f"trailer-like line {index + 1} is outside the final trailer block"
                )
        return findings

    def _trailer_formatting(
        self, lines: list[str], block_start: int | None
    ) -> list[str]:
        """Inside an established block, each header is written `Token: value`."""
        if block_start is None or block_start >= len(lines):
            return []
        findings: list[str] = []
        for index in range(block_start, len(lines)):
            token = self.header_token(lines[index])
            if not token:
                continue
            rest = lines[index][len(token) + 1:]
            if rest and rest[:1] not in (" ", "\t"):
                findings.append(f"line {index + 1} is not written as '{token}: value'")
        return findings

    def _unknown_trailers(
        self, lines: list[str], block_start: int | None
    ) -> list[str]:
        if block_start is None or block_start >= len(lines):
            return []
        findings: list[str] = []
        for index in range(block_start, len(lines)):
            token = self.header_token(lines[index])
            if token and not self.policy.allows(token):
                findings.append(
                    f"line {index + 1} carries {token}:, a trailer the policy does not allow"
                )
        return findings

    def _indented(self, lines: list[str], block_start: int | None) -> list[str]:
        if block_start is None or block_start >= len(lines):
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
