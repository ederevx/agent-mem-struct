"""Compose and lint the message a memory mutation is published under.

One owner for the message half of publishing: `CommitMessage` commits the
agent's own words as written, supplies only the attribution lines the
conventions require - in their mandated order, reusing the agent's own
values and never duplicating a pair it already carries - wraps prose to 80
columns, and refuses a payload that does not end in its own parseable
trailer block.
"""
from __future__ import annotations

import re
import textwrap

from rm_git import GitWorktree

MAX_LINE = 80
TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
ATTRIBUTION_KEYS = ("Assisted-by", "Signed-off-by")


class CommitMessage:
    """One prepared commit payload, and the trailer block it must end with."""

    def __init__(self, worktree: GitWorktree, agent: str, model: str = "") -> None:
        self.worktree = worktree
        self.agent = agent
        self.model = model

    def compose(
        self, message: str, target: str, identity: tuple[str, str]
    ) -> tuple[str, str | None]:
        """The payload to commit under, or why it cannot be committed."""
        body, given = self.split(message.strip() or f"memory_update: {target}")
        block = self.complete(given, identity)
        payload = f"{self.wrap(body)}\n\n{block}\n"
        return payload, self.error(payload)

    def trailers(self, identity: tuple[str, str]) -> str:
        attribution = f"Assisted-by: {self.agent}" + (f":{self.model}" if self.model else "")
        return f"{attribution}\nSigned-off-by: {identity[0]} <{identity[1]}>"

    def complete(self, given: str, identity: tuple[str, str]) -> str:
        """The agent's trailer block, supplied with whatever pair it omits."""
        defaults = dict(zip(ATTRIBUTION_KEYS, self.trailers(identity).splitlines()))
        lines = given.splitlines()
        keys = [line.split(":", 1)[0].strip() if ":" in line else "" for line in lines]
        rest = [line for line, key in zip(lines, keys) if key not in defaults]
        attribution = [
            next(
                (line for line, key in zip(lines, keys) if key == wanted),
                defaults[wanted],
            )
            for wanted in ATTRIBUTION_KEYS
        ]
        return "\n".join([*rest, *attribution])

    def split(self, text: str) -> tuple[str, str]:
        """The agent's prose, and the attribution block it already ends with."""
        lines = text.splitlines()
        start = len(lines)
        while start and (
            not lines[start - 1].strip() or TRAILER_LINE_RE.match(lines[start - 1].strip())
        ):
            start -= 1
        block = "\n".join(line.strip() for line in lines[start:] if line.strip())
        keys = {line.split(":", 1)[0] for line in block.splitlines() if ":" in line}
        if block and keys & set(ATTRIBUTION_KEYS):
            return "\n".join(lines[:start]).strip(), block
        return text, ""

    def error(self, payload: str) -> str | None:
        """Lint the payload the way the commit conventions require."""
        parsed = self.worktree.run("interpret-trailers", "--parse", input_text=payload)
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
                            stripped,
                            width=MAX_LINE,
                            break_long_words=False,
                            break_on_hyphens=False,
                        )
                    )
            paragraphs.append("\n".join(lines))
        return "\n\n".join(paragraphs)
