#!/usr/bin/env python3
"""`DraftRenderer`: a recorded plan shown as the change it makes."""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"


def load(name: str):
    """Import one hook module once, so every test shares its classes."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    return importlib.import_module(name)


draft = load("rm_draft")
plan_module = load("rm_update_plan")


def replay(before: bytes | None, hunks: list[dict]) -> bytes:
    """The new bytes, rebuilt from the emitted hunks exactly as they are shown."""
    old = (before or b"").splitlines(keepends=True)
    out: list[bytes] = []
    cursor = 0
    for hunk in hunks:
        start, end = hunk["before"]
        assert start >= cursor, (start, cursor)
        out += old[cursor:start]
        index = start
        for tag, line in hunk["rows"]:
            if tag in ("-", " "):
                assert old[index] == line, (tag, line, old[index])
                index += 1
            if tag != "-":
                out.append(line)
        assert index == end, (index, end)
        cursor = end
    out += old[cursor:]
    return b"".join(out)


class Writes:
    """A stand-in plan that records only the effects a test gives it."""

    def __init__(self, *effects: object) -> None:
        self.operation = "commit"
        self.intent = {"operation": "commit"}
        self._effects = list(effects)

    def changes(self) -> list[object]:
        return list(self._effects)


class PresentationTests(unittest.TestCase):
    """A write is shown as its change, with both whole states bound."""

    def effect(self, before, after, path: str = "/memory/local/nodes/x.md"):
        return plan_module.FileEffect(plan_module.WRITE, Path(path), before, after)

    def changed(self, before, after) -> str:
        return draft.DraftRenderer.changed(self.effect(before, after))

    def test_every_changed_line_is_shown_and_the_hunks_rebuild_it(self) -> None:
        before = b"# Node\n\n## One\n\nalpha\nbeta\n"
        after = b"# Node\n\n## One\n\nalpha\nBETA\ngamma\n"
        text = self.changed(before, after)
        for row in (["-", "beta\n"], ["+", "BETA\n"], ["+", "gamma\n"]):
            self.assertIn(json.dumps(row), text)
        hunks = draft.DraftRenderer.hunks(before, after)
        self.assertEqual(replay(before, hunks), after)

    def test_the_two_whole_states_are_named_by_size_and_digest(self) -> None:
        before = b"# Node\n\nbody\n"
        after = b"# Node\n\nchanged body\n"
        text = self.changed(before, after)
        self.assertIn(f"Before: {len(before)} bytes, sha256 ", text)
        self.assertIn(f"After: {len(after)} bytes, sha256 ", text)
        self.assertIn(plan_module.digest(after), text)

    def test_line_endings_and_a_missing_final_newline_survive(self) -> None:
        before = b"# Node\r\n\r\nalpha\r\nbeta"
        after = b"# Node\r\n\r\nalpha\r\nBETA"
        self.assertEqual(
            replay(before, draft.DraftRenderer.hunks(before, after)), after
        )
        text = self.changed(before, after)
        self.assertIn("\\r\\n", text)
        self.assertIn(json.dumps(["-", "beta"]), text)

    def test_unicode_is_shown_and_rebuilt_exactly(self) -> None:
        before = "# N\u0153ud\n\n\u00e9tat \u2014 ancien\n".encode("utf-8")
        after = "# N\u0153ud\n\n\u00e9tat \u2014 nouveau\n".encode("utf-8")
        self.assertEqual(
            replay(before, draft.DraftRenderer.hunks(before, after)), after
        )
        self.assertIn("nouveau", self.changed(before, after))

    def test_a_separated_change_is_its_own_hunk_in_its_section(self) -> None:
        filler = b"a paragraph between the two sections\n" * 8
        before = b"# Node\n\n## A\n\nalpha\n\n" + filler + b"## B\n\nbeta\n"
        after = b"# Node\n\n## A\n\nALPHA\n\n" + filler + b"## B\n\nBETA\n"
        hunks = draft.DraftRenderer.hunks(before, after)
        self.assertEqual(len(hunks), 2)
        text = self.changed(before, after)
        self.assertIn("in section: ## A", text)
        self.assertIn(json.dumps([" ", "## B\n"]), text)
        self.assertEqual(replay(before, hunks), after)

    def test_a_created_leaf_is_shown_by_all_of_its_bytes(self) -> None:
        text = draft.DraftRenderer.changed(self.effect(None, b"# New\n\nbody\n"))
        self.assertIn("Before: (absent)", text)
        self.assertIn(json.dumps(["+", "# New\n"]), text)
        self.assertIn(json.dumps(["+", "body\n"]), text)

    def test_a_binary_side_encodes_both_sides_instead_of_lines(self) -> None:
        text = draft.DraftRenderer.changed(
            self.effect(b"text\n", b"\xff\xfe\x00binary")
        )
        self.assertIn("base64", text)
        self.assertIn("binary:", text)

    def test_a_write_with_identical_bytes_says_it_changes_nothing(self) -> None:
        self.assertIn(
            "changes nothing",
            draft.DraftRenderer.changed(self.effect(b"s\n", b"s\n")),
        )

    def test_a_removed_leaf_is_shown_by_its_content(self) -> None:
        effect = plan_module.FileEffect(
            plan_module.DELETE,
            Path("/memory/local/nodes/x.md"),
            b"the old body\n",
            None,
        )
        rendered = draft.DraftRenderer().effects(Writes(effect))
        self.assertIn("### delete (removed content)", rendered)
        self.assertIn("the old body", rendered)

    def test_binary_content_is_shown_by_its_digest(self) -> None:
        effect = plan_module.FileEffect(
            plan_module.WRITE,
            Path("/memory/local/nodes/figure.png"),
            None,
            b"\xff\xfe\x00binary",
        )
        rendered = draft.DraftRenderer().effects(Writes(effect))
        self.assertIn("binary:", rendered)
        self.assertIn("base64", rendered)
        self.assertIn("sha256", rendered)

    def test_a_large_leaf_and_its_log_are_shown_by_their_change_only(self) -> None:
        leaf = b"# Node\n\n## State\n\n" + b"a stored paragraph\n" * 3500
        log = b"# Log\n\n" + b"a log paragraph\n" * 6000
        plan = Writes(
            self.effect(leaf, leaf + b"a new line\n"),
            self.effect(log, log + b"## displaced\n\na moved line\n"),
        )
        rendered = draft.DraftRenderer().effects(plan)
        self.assertLess(len(rendered), 4096)
        self.assertIn("a new line", rendered)
        self.assertIn("a moved line", rendered)
        self.assertEqual(rendered.count("a stored paragraph"), 3)


if __name__ == "__main__":
    unittest.main()
