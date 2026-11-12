"""The draft one memory update is audited on, rendered from its recorded plan.

`DraftRenderer` shows every effect the plan records as the change it makes:
both whole states by size and digest, then every changed line with its context,
its line ranges and its section, so an unchanged body is not printed twice and a
small edit stays small however large the file is. A created leaf is shown by all
of its bytes, a deletion by all of its removed lines, and a non-UTF-8 side by
both sides encoded.

The rendering is the audit preview format: it is what the caller audits and
what a refusal hands back, never a summary of the change.
"""
from __future__ import annotations

import base64
import difflib
import json
from typing import Any

from rm_update_plan import DELETE, MOVE, RMDIR, WRITE, digest

CONTEXT_LINES = 3
HUNK_LEGEND = (
    "Each row below is a JSON [tag, string] pair whose string is exact: a line is\n"
    "shown with the characters it ends with, so a CRLF line ends in \"\\r\\n\" and a\n"
    "last line with no ending ends with none. \" \" is on both sides, \"-\" is the\n"
    "before line that is gone, and \"+\" is the after line that arrives."
)


class DraftRenderer:
    """Renders a recorded plan as the exact change it would make."""

    def effects(self, plan: Any) -> str:
        """Every change the plan records, each shown as the change it makes."""
        effects = plan.changes()
        if not effects:
            return "(no changes)"
        blocks: list[str] = []
        for effect in effects:
            if effect.kind == WRITE:
                blocks.append(self.changed(effect))
            elif effect.kind == DELETE:
                blocks.append(
                    f"### delete (removed content): {effect.path}\n"
                    f"{self.text(effect.before)}"
                )
            elif effect.kind == MOVE:
                blocks.append(
                    f"### move: {effect.path} -> {effect.destination}\n"
                    f"{self.text(effect.before)}"
                )
            elif effect.kind == RMDIR:
                blocks.append(f"### remove directory: {effect.path}")
            else:
                blocks.append(f"### {effect.kind}: {effect.path}")
        return "\n\n".join(blocks)

    @classmethod
    def changed(cls, effect: Any) -> str:
        """One write: both snapshots by size and digest, then the change itself."""
        if not (cls.textual(effect.before) and cls.textual(effect.after)):
            return (
                f"### write: {effect.path}\n"
                f"Before:\n{cls.text(effect.before)}\n"
                f"After:\n{cls.text(effect.after)}"
            )
        parts = [
            f"### write: {effect.path}",
            cls.snapshot("Before", effect.before),
            cls.snapshot("After", effect.after),
        ]
        hunks = cls.hunks(effect.before, effect.after)
        if not hunks:
            parts.append("Both sides have the same bytes, so this write changes nothing.")
            return "\n".join(parts)
        parts.append(HUNK_LEGEND)
        parts += [cls.render(hunk) for hunk in hunks]
        return "\n".join(parts)

    @staticmethod
    def snapshot(label: str, data: bytes | None) -> str:
        """One whole file state, identified rather than repeated."""
        if data is None:
            return f"{label}: (absent)"
        return f"{label}: {len(data)} bytes, sha256 {digest(data)}"

    @staticmethod
    def textual(data: bytes | None) -> bool:
        """Whether this side can be shown line by line rather than encoded."""
        if data is None:
            return True
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

    @classmethod
    def hunks(cls, before: bytes | None, after: bytes | None) -> list[dict[str, Any]]:
        """Every changed line with its context, as exact bytes with their ranges."""
        old = (before or b"").splitlines(keepends=True) if before else []
        new = (after or b"").splitlines(keepends=True) if after else []
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        hunks: list[dict[str, Any]] = []
        for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
            rows: list[tuple[str, bytes]] = []
            for tag, a0, a1, b0, b1 in group:
                if tag == "equal":
                    rows += [(" ", line) for line in old[a0:a1]]
                else:
                    rows += [("-", line) for line in old[a0:a1]]
                    rows += [("+", line) for line in new[b0:b1]]
            hunks.append(
                {
                    "before": [group[0][1], group[-1][2]],
                    "after": [group[0][3], group[-1][4]],
                    "section": cls.section(new, group[0][3]),
                    "rows": rows,
                }
            )
        return hunks

    @staticmethod
    def section(lines: list[bytes], index: int) -> str:
        """The nearest Markdown heading above a hunk, so its scope is visible."""
        for line in reversed(lines[:index]):
            text = line.decode("utf-8", "replace").rstrip("\r\n").strip()
            if text.startswith("#"):
                return text
        return "(no heading above it)"

    @classmethod
    def render(cls, hunk: dict[str, Any]) -> str:
        """One hunk: its two line ranges, its section, and its exact rows."""
        head = (
            f"@@ before lines [{hunk['before'][0]},{hunk['before'][1]}) "
            f"after lines [{hunk['after'][0]},{hunk['after'][1]}) "
            f"in section: {hunk['section']}"
        )
        rows = [head]
        for tag, line in hunk["rows"]:
            rows.append(json.dumps([tag, line.decode("utf-8")], ensure_ascii=False))
        return "\n".join(rows)

    @staticmethod
    def text(data: bytes | None) -> str:
        """One side of a change: its exact bytes, or an explicit encoding."""
        if data is None:
            return "(absent)"
        stamp = f"{len(data)} bytes, sha256 {digest(data)}"
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            encoded = base64.b64encode(data).decode("ascii")
            return f"(binary: {stamp}, base64)\n{encoded}"
        return f"<<<<<<<< {stamp}\n{body}\n>>>>>>>>"
