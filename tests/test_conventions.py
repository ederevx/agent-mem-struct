#!/usr/bin/env python3
"""Convention enumeration: what one governing document states, one by one.

The parser is conservative on purpose: a convention that silently fell out of
the enumeration would never be assessed, so every unsupported shape refuses.
The validator never repeats text the caller submitted.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
SCRATCH = Path(
    os.environ.get(
        "AGENT_MEM_STRUCT_TEST_TMP",
        str(Path.home() / ".tmp" / "agent-mem-struct-tests"),
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)


def load(name: str):
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    return importlib.import_module(name)


conventions_module = load("rm_conventions")
sources_module = load("rm_sources")

RULES = conventions_module.RULES_KIND
MEMORY = conventions_module.MEMORY_KIND
PLAN = "a" * 64
DOCUMENTS = "b" * 64


def rows(*sources) -> list[dict]:
    """Inventory rows for (path, kind, text), the source absent if None."""
    inventory = []
    for path, kind, text in sources:
        inventory.append(
            {
                "path": path,
                "digest": None
                if text is None
                else hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text or "",
                "kind": kind,
            }
        )
    return inventory


def rules(*items: str) -> str:
    lines = ["# Memory rules", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item}")
        lines.append("")
    return "\n".join(lines) + "\n"


def section(*bullets: str) -> str:
    text = "# Notes\n\n## Mandatory conventions\n\n"
    for bullet in bullets:
        text += f"- {bullet}\n"
    return text


class ParserTests(unittest.TestCase):
    """One document's conventions, as the parser reads them, or its refusal."""

    def setUp(self) -> None:
        self.checklist = conventions_module.ConventionChecklist()

    def items(self, text: str, kind: str) -> list[str]:
        return self.checklist.items(text, kind)

    def refused(self, text: str, kind: str) -> str:
        with self.assertRaises(conventions_module.ConventionError) as raised:
            self.items(text, kind)
        return str(raised.exception)

    # -- numbered rules ------------------------------------------------------

    def test_each_numbered_rule_is_one_convention_in_order(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n2. Write second.\n"
        self.assertEqual(
            self.items(text, RULES), ["1. Read first.", "2. Write second."]
        )

    def test_a_rule_keeps_its_continuation_lines(self) -> None:
        text = (
            "# Memory rules\n\n1. Read first,\n   then write.\n"
            "2. Commit exactly.\n"
        )
        self.assertEqual(
            self.items(text, RULES),
            ["1. Read first,\n   then write.", "2. Commit exactly."],
        )

    def test_a_numbered_rule_that_states_nothing_refuses(self) -> None:
        self.assertIn(
            "states nothing",
            self.refused("# Memory rules\n\n1. \n", RULES),
        )

    def test_the_numbers_must_be_consecutive(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n3. Write third.\n"
        self.assertIn("not consecutive", self.refused(text, RULES))

    def test_the_rules_end_at_the_next_level_one_heading(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n\n# Elsewhere\n\nprose\n"
        self.assertEqual(self.items(text, RULES), ["1. Read first."])

    def test_the_rules_end_at_the_footer(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n\n---\n\nafterward\n"
        self.assertEqual(self.items(text, RULES), ["1. Read first."])

    def test_prose_after_the_rules_refuses(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n\nunattached prose\n"
        self.assertIn("belongs to no rule", self.refused(text, RULES))

    def test_a_document_with_no_rules_refuses(self) -> None:
        self.assertIn(
            "states no numbered rule",
            self.refused("# Memory rules\n\nprose only\n", RULES),
        )

    def test_the_first_level_one_heading_must_be_memory_rules(self) -> None:
        text = "# Something else\n\n1. Read first.\n"
        self.assertIn("first level-one heading", self.refused(text, RULES))

    def test_an_unclosed_fence_refuses(self) -> None:
        self.assertIn(
            "never closed",
            self.refused("# Memory rules\n\n```\n1. x\n", RULES),
        )

    def test_a_fenced_marker_is_not_a_rule(self) -> None:
        text = "# Memory rules\n\n1. Read first.\n\n```\n2. x\n```\n"
        self.assertEqual(
            self.items(text, RULES), ["1. Read first.\n\n```\n2. x\n```"]
        )

    def test_a_fenced_heading_does_not_start_the_rules(self) -> None:
        text = "```\n# Memory rules\n```\n# Memory rules\n\n1. Read first.\n"
        self.assertEqual(self.items(text, RULES), ["1. Read first."])

    # -- mandatory conventions ----------------------------------------------

    def test_each_top_level_bullet_is_one_convention(self) -> None:
        text = section("Read first.", "Write second.")
        self.assertEqual(
            self.items(text, MEMORY), ["- Read first.", "- Write second."]
        )

    def test_a_bullet_keeps_its_continuation_and_nested_list(self) -> None:
        text = (
            "## Mandatory conventions\n\n- Read first,\n  then write:\n"
            "  - nested\n  - nested\n"
        )
        self.assertEqual(
            self.items(text, MEMORY),
            ["- Read first,\n  then write:\n  - nested\n  - nested"],
        )

    def test_a_second_conventions_section_refuses(self) -> None:
        text = (
            "## Mandatory conventions\n\n- first\n\n## Nodes\n\n"
            "## Mandatory conventions\n\n- missed\n"
        )
        self.assertIn("more than one", self.refused(text, MEMORY))

    def test_an_empty_bullet_refuses(self) -> None:
        self.assertIn(
            "states nothing",
            self.refused("## Mandatory conventions\n\n- \n", MEMORY),
        )

    def test_a_section_that_says_none_has_no_conventions(self) -> None:
        self.assertEqual(
            self.items("## Mandatory conventions\n\n(none)\n", MEMORY), []
        )

    def test_none_mixed_with_bullets_refuses(self) -> None:
        text = "## Mandatory conventions\n\n- read\n\n(none)\n"
        self.assertIn("mixes", self.refused(text, MEMORY))

    def test_content_outside_a_bullet_refuses(self) -> None:
        text = "## Mandatory conventions\n\na paragraph\n\n- read\n"
        self.assertIn(
            "outside any convention bullet", self.refused(text, MEMORY)
        )

    def test_a_document_without_the_section_has_no_conventions(self) -> None:
        self.assertEqual(self.items("# Notes\n\nprose\n", MEMORY), [])

    def test_a_fenced_conventions_heading_is_ignored(self) -> None:
        text = "```\n## Mandatory conventions\n\n- fake\n```\n"
        self.assertEqual(self.items(text, MEMORY), [])

    def test_a_long_section_is_enumerated_in_order(self) -> None:
        bullets = [f"Convention number {number}." for number in range(1, 44)]
        self.assertEqual(len(self.items(section(*bullets), MEMORY)), 43)


class EnumerateTests(unittest.TestCase):
    """Which captured sources contribute conventions, and in what order."""

    def setUp(self) -> None:
        self.checklist = conventions_module.ConventionChecklist()

    def test_rules_and_conventions_join_in_capture_order(self) -> None:
        inventory = rows(
            ("/home/RULES.md", RULES, rules("Read first.", "Write second.")),
            ("/home/memory/MEMORY.md", MEMORY, section("Keep it small.")),
        )
        conventions = self.checklist.enumerate(inventory)
        self.assertEqual(
            [(row["ordinal"], row["text"]) for row in conventions],
            [
                (1, "1. Read first."),
                (2, "2. Write second."),
                (1, "- Keep it small."),
            ],
        )
        self.assertEqual(
            conventions[0]["source"],
            conventions_module.source_key("/home/RULES.md"),
        )

    def test_structure_and_leaf_sources_contribute_nothing(self) -> None:
        inventory = rows(
            ("/home/STRUCTURE.md", "structure", "# Structure\n\nprose\n"),
            ("/home/memory/leaf/MEMORY.md", "leaf", "- not a convention\n"),
        )
        self.assertEqual(self.checklist.enumerate(inventory), [])

    def test_an_absent_source_contributes_nothing(self) -> None:
        inventory = rows(("/home/memory/group/MEMORY.md", MEMORY, None))
        self.assertEqual(self.checklist.enumerate(inventory), [])

    def test_a_source_without_a_region_contributes_nothing(self) -> None:
        inventory = rows(
            ("/home/memory/MEMORY.md", MEMORY, "# Memory\n\nprose\n")
        )
        self.assertEqual(self.checklist.enumerate(inventory), [])

    def test_a_parser_refusal_names_the_source(self) -> None:
        inventory = rows(
            (
                "/home/memory/group/MEMORY.md",
                MEMORY,
                "## Mandatory conventions\n\n- one\n\n"
                "## Mandatory conventions\n\n- two\n",
            )
        )
        with self.assertRaises(conventions_module.ConventionError) as raised:
            self.checklist.enumerate(inventory)
        self.assertIn("/home/memory/group/MEMORY.md", str(raised.exception))

    def test_one_path_captured_under_two_kinds_refuses(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="ams-conventions-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, root, True)
        document = root / "MEMORY.md"
        document.write_text(section("Read first."), encoding="utf-8")

        class Twice(sources_module.GoverningSources):
            def sources(self, plan=None):
                return [
                    ("first", document, True, MEMORY),
                    ("second", document, True, RULES),
                ]

        with self.assertRaises(sources_module.SourceError) as raised:
            Twice(root, root).inventory()
        self.assertIn("both as", str(raised.exception))


class ValidateTests(unittest.TestCase):
    """The checklist admits one complete, bound, violation-free answer."""

    def setUp(self) -> None:
        self.checklist = conventions_module.ConventionChecklist()
        text = rules("Read first.", "Write second.")
        self.inventory = rows(("/home/RULES.md", RULES, text))
        self.conventions = self.checklist.enumerate(self.inventory)
        self.source = self.conventions[0]["source"]
        self.documents_digest = conventions_module.inventory_digest(
            self.inventory
        )

    def entry(self, ordinal: int = 1, **changes) -> dict:
        entry = {
            "source": self.source,
            "ordinal": ordinal,
            "status": "pass",
            "reasoning": "The change complies.",
        }
        entry.update(changes)
        return entry

    def reference(self, ordinal: int, **changes) -> dict:
        reference = {"source": self.source, "ordinal": ordinal}
        reference.update(changes)
        return reference

    def separation(self, checks: list[dict], **changes) -> dict:
        separation = {
            "significant": [
                self.reference(check["ordinal"])
                for check in checks
                if check["status"] != "not-applicable"
            ],
            "not_applicable": [
                self.reference(check["ordinal"])
                for check in checks
                if check["status"] == "not-applicable"
            ],
        }
        separation.update(changes)
        return separation

    def envelope(self, checks: list[dict], **changes) -> dict:
        envelope = {
            "schema": 1,
            "plan_digest": PLAN,
            "documents_digest": self.documents_digest,
            "separation": self.separation(checks),
            "checks": checks,
        }
        envelope.update(changes)
        return envelope

    def validate(self, raw) -> list[dict]:
        return self.checklist.validate(
            raw,
            plan_digest=PLAN,
            documents_digest=self.documents_digest,
            conventions=self.conventions,
        )

    def codes(self, raw) -> list[str]:
        return [conflict["code"] for conflict in self.validate(raw)]

    def test_a_complete_checklist_is_admitted(self) -> None:
        self.assertEqual(
            self.validate(self.envelope([self.entry(1), self.entry(2)])), []
        )

    def test_the_entries_may_come_in_any_order(self) -> None:
        self.assertEqual(
            self.validate(self.envelope([self.entry(2), self.entry(1)])), []
        )

    def test_a_missing_checklist_is_refused(self) -> None:
        self.assertEqual(self.codes(None), ["checklist-missing"])

    def test_an_empty_checklist_leaves_all_unassessed(self) -> None:
        conflicts = self.validate(self.envelope([]))
        self.assertEqual(
            [conflict["code"] for conflict in conflicts],
            ["checklist-coverage"],
        )
        self.assertIn("2 of 2", conflicts[0]["message"])

    def test_a_repeated_convention_is_refused(self) -> None:
        self.assertEqual(
            self.codes(
                self.envelope([self.entry(1), self.entry(1), self.entry(2)])
            ),
            ["checklist-coverage"],
        )

    def test_an_ordinal_this_update_does_not_have_is_refused(self) -> None:
        conflicts = self.validate(
            self.envelope([self.entry(1), self.entry(2), self.entry(3)])
        )
        self.assertEqual(
            [conflict["code"] for conflict in conflicts],
            ["checklist-coverage"],
        )
        self.assertIn("does not have", conflicts[0]["message"])

    def test_an_entry_from_another_source_is_refused(self) -> None:
        other = str(SCRATCH / "other" / "MEMORY.md")
        conflicts = self.validate(
            self.envelope([self.entry(1), self.entry(2, source=other)])
        )
        self.assertEqual(
            [conflict["code"] for conflict in conflicts],
            ["checklist-coverage"],
        )
        self.assertIn("unassessed", conflicts[0]["message"])

    def test_a_boolean_ordinal_is_refused(self) -> None:
        self.assertEqual(
            self.codes(self.envelope([self.entry(1), self.entry(True)])),
            ["checklist-invalid"],
        )

    def test_an_unknown_status_is_refused(self) -> None:
        self.assertEqual(
            self.codes(
                self.envelope([self.entry(1, status="passed"), self.entry(2)])
            ),
            ["checklist-invalid"],
        )

    def test_a_fix_on_a_passing_entry_is_refused(self) -> None:
        self.assertEqual(
            self.codes(
                self.envelope([self.entry(1, fix="x"), self.entry(2)])
            ),
            ["checklist-invalid"],
        )

    def test_a_source_with_no_conventions_needs_no_entry(self) -> None:
        inventory = rows(
            ("/home/RULES.md", RULES, rules("Read first.")),
            ("/home/memory/MEMORY.md", MEMORY, "# Memory\n\nprose\n"),
        )
        conventions = self.checklist.enumerate(inventory)
        documents = conventions_module.inventory_digest(inventory)
        conflicts = self.checklist.validate(
            self.envelope([self.entry(1)], documents_digest=documents),
            plan_digest=PLAN,
            documents_digest=documents,
            conventions=conventions,
        )
        self.assertEqual(conflicts, [])

    def test_a_stale_plan_is_reported_before_violations(self) -> None:
        violated = self.entry(1, status="violation", fix="Do better.")
        conflicts = self.validate(
            self.envelope([violated], plan_digest="c" * 64)
        )
        self.assertEqual(
            [conflict["code"] for conflict in conflicts],
            ["checklist-plan-mismatch"],
        )

    def test_stale_documents_outrank_a_repeated_convention(self) -> None:
        self.assertEqual(
            self.codes(
                self.envelope(
                    [self.entry(1), self.entry(1), self.entry(2)],
                    documents_digest="c" * 64,
                )
            ),
            ["checklist-documents-stale"],
        )

    def test_stale_documents_outrank_an_unknown_violation(self) -> None:
        removed = self.entry(9, status="violation", fix="Do better.")
        self.assertEqual(
            self.codes(
                self.envelope(
                    [removed, self.entry(1), self.entry(2)],
                    documents_digest="c" * 64,
                )
            ),
            ["checklist-documents-stale"],
        )

    def test_a_stale_document_set_is_reported(self) -> None:
        self.assertIn(
            "checklist-documents-stale",
            self.codes(
                self.envelope(
                    [self.entry(1), self.entry(2)], documents_digest="c" * 64
                )
            ),
        )

    def test_a_violation_blocks_the_update_with_generic_guidance(self) -> None:
        conflicts = self.validate(
            self.envelope(
                [
                    self.entry(1, status="violation", fix="SENTINEL-FIX"),
                    self.entry(2),
                ]
            )
        )
        self.assertEqual(
            [conflict["code"] for conflict in conflicts],
            ["checklist-violations"],
        )
        self.assertIn(self.source, conflicts[0]["message"])
        self.assertIn("1", conflicts[0]["message"])
        self.assertNotIn("SENTINEL-FIX", str(conflicts))
        self.assertNotIn("SENTINEL-FIX", json.dumps(conflicts))

    def test_submitted_text_never_appears_in_a_refusal(self) -> None:
        def refusal(raw) -> str:
            return json.dumps(self.validate(raw))

        # An unsupported envelope key is refused as such, not named.
        self.assertNotIn(
            "SENTINEL-KEY",
            refusal(
                self.envelope([self.entry(1), self.entry(2)], SENTINEL_KEY=1)
            ),
        )
        # A violation on a convention the hook never enumerated is a coverage
        # refusal that names neither the submitted path nor its reasoning.
        unknown = self.entry(
            9,
            source=str(Path("/sentinel/path/MEMORY.md")),
            status="violation",
            fix="SENTINEL-FIX",
            reasoning="SENTINEL-REASONING",
        )
        text = refusal(self.envelope([self.entry(1), self.entry(2), unknown]))
        for sentinel in ("SENTINEL", "/sentinel/path"):
            self.assertNotIn(sentinel, text)
        # Reasoning and fixes of admitted entries are read and dropped.
        text = refusal(
            self.envelope(
                [
                    self.entry(1, reasoning="SENTINEL-REASONING"),
                    self.entry(2, fix="SENTINEL-FIX"),
                ]
            )
        )
        self.assertNotIn("SENTINEL", text)

    # -- separation ----------------------------------------------------------

    def test_a_missing_separation_is_refused(self) -> None:
        envelope = self.envelope([self.entry(1), self.entry(2)])
        del envelope["separation"]
        self.assertEqual(
            self.codes(envelope), ["checklist-separation-missing"]
        )

    def test_the_separation_shape_is_strict(self) -> None:
        checks = [self.entry(1), self.entry(2)]
        malformed = {
            "null": None,
            "extra key": self.separation(checks, other=[]),
            "missing list": {"significant": self.reference(1)},
            "not a list": self.separation(checks, significant={}),
            "extra reference key": self.separation(
                checks, significant=[self.reference(1, note="x")]
            ),
            "relative source": self.separation(
                checks, significant=[self.reference(1, source="RELATIVE.md")]
            ),
            "boolean ordinal": self.separation(
                checks, significant=[self.reference(True)]
            ),
        }
        for name, separation in malformed.items():
            with self.subTest(name):
                self.assertEqual(
                    self.codes(
                        self.envelope(checks, separation=separation)
                    ),
                    ["checklist-separation-invalid"],
                )

    def test_the_separation_partitions_the_conventions_exactly(self) -> None:
        checks = [self.entry(1), self.entry(2)]
        malformed = {
            "omitted": self.separation(
                checks, significant=[self.reference(1)]
            ),
            "unknown": self.separation(
                checks,
                significant=[
                    self.reference(1), self.reference(2), self.reference(3)
                ],
            ),
            "repeated": self.separation(
                checks,
                significant=[
                    self.reference(1), self.reference(1), self.reference(2)
                ],
            ),
            "overlap": self.separation(
                checks, not_applicable=[self.reference(1)]
            ),
            "path alias": self.separation(
                checks,
                significant=[
                    self.reference(1),
                    self.reference(1, source=self.source + "/"),
                    self.reference(2),
                ],
            ),
        }
        for name, separation in malformed.items():
            with self.subTest(name):
                self.assertEqual(
                    self.codes(
                        self.envelope(checks, separation=separation)
                    ),
                    ["checklist-separation-coverage"],
                )

    def test_a_significant_convention_must_pass(self) -> None:
        checks = [self.entry(1, status="not-applicable"), self.entry(2)]
        separation = self.separation(
            checks,
            significant=[self.reference(1), self.reference(2)],
            not_applicable=[],
        )
        self.assertEqual(
            self.codes(self.envelope(checks, separation=separation)),
            ["checklist-separation-mismatch"],
        )

    def test_a_not_applicable_convention_must_not_pass(self) -> None:
        checks = [self.entry(1), self.entry(2)]
        separation = self.separation(
            checks,
            significant=[self.reference(1)],
            not_applicable=[self.reference(2)],
        )
        self.assertEqual(
            self.codes(self.envelope(checks, separation=separation)),
            ["checklist-separation-mismatch"],
        )

    def test_priority_order_is_independent_of_check_order(self) -> None:
        checks = [self.entry(1), self.entry(2)]
        separation = self.separation(
            checks, significant=[self.reference(2), self.reference(1)]
        )
        self.assertEqual(
            self.validate(self.envelope(checks, separation=separation)), []
        )

    def test_stale_digests_precede_a_missing_separation(self) -> None:
        envelope = self.envelope(
            [self.entry(1), self.entry(2)], plan_digest="c" * 64
        )
        del envelope["separation"]
        self.assertEqual(self.codes(envelope), ["checklist-plan-mismatch"])

    def test_a_violation_precedes_a_missing_separation(self) -> None:
        envelope = self.envelope(
            [self.entry(1, status="violation", fix="Do better."), self.entry(2)]
        )
        del envelope["separation"]
        self.assertEqual(self.codes(envelope), ["checklist-violations"])

    def test_a_coverage_gap_precedes_the_separation(self) -> None:
        envelope = self.envelope([self.entry(1)])
        del envelope["separation"]
        self.assertEqual(self.codes(envelope), ["checklist-coverage"])

    def test_an_empty_partition_is_admitted_when_nothing_is_enumerated(
        self,
    ) -> None:
        inventory = rows(
            ("/home/memory/MEMORY.md", MEMORY, "# Memory\n\nprose\n")
        )
        conventions = self.checklist.enumerate(inventory)
        documents = conventions_module.inventory_digest(inventory)
        envelope = {
            "schema": 1,
            "plan_digest": PLAN,
            "documents_digest": documents,
            "separation": {"significant": [], "not_applicable": []},
            "checks": [],
        }
        self.assertEqual(
            self.checklist.validate(
                envelope,
                plan_digest=PLAN,
                documents_digest=documents,
                conventions=conventions,
            ),
            [],
        )

    def test_the_separation_never_echoes_submitted_text(self) -> None:
        checks = [self.entry(1), self.entry(2)]
        separation = self.separation(
            checks,
            significant=[
                self.reference(1, source="/sentinel/path/MEMORY.md"),
                self.reference(2),
            ],
        )
        text = json.dumps(
            self.validate(self.envelope(checks, separation=separation))
        )
        self.assertEqual(text.count("checklist-separation-coverage"), 1)
        for sentinel in ("SENTINEL", "/sentinel/path"):
            self.assertNotIn(sentinel, text)


if __name__ == "__main__":
    unittest.main()
