#!/usr/bin/env python3
"""`GoverningSources`: which documents bind a change, and one capture of them."""
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
from unittest import mock

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
    """Import one hook module once, so every test shares its classes."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    return importlib.import_module(name)


sources_module = load("rm_sources")
plan_module = load("rm_update_plan")


class Plan:
    """A stand-in plan: one write whose target the source capture reads."""

    def __init__(self, operation: str, path: Path, body: str = "Current state.\n") -> None:
        self.operation = operation
        self.intent = {"operation": operation, "path": str(path)}
        self.effects = [
            plan_module.FileEffect(
                plan_module.WRITE,
                path,
                b"body\n",
                body.encode("utf-8"),
            )
        ]

    def changes(self):
        return list(self.effects)


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ams-sources-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.temp, True)
        (self.temp / "RULES.md").write_text("# Rules\n", encoding="utf-8")
        (self.temp / "STRUCTURE.md").write_text(
            "Structure-Version: v1\n", encoding="utf-8"
        )

    def memory(self, root: str | None = None) -> Path:
        memory = self.temp / "memory"
        memory.mkdir(exist_ok=True)
        if root is not None:
            (memory / "MEMORY.md").write_text(root, encoding="utf-8")
        return memory

    def governed(
        self, root: str | None = "# Root\n", plan_path: Path | None = None
    ) -> tuple[object, Plan]:
        """One captured source set over a plausible agent home."""
        for name, body in (
            ("RULES.md", "# Rules\n"),
            ("STRUCTURE.md", "Structure-Version: v1\n"),
        ):
            path = self.temp / name
            if not path.exists():
                path.write_text(body, encoding="utf-8")
        memory = self.memory(root)
        target = plan_path if plan_path is not None else memory / "local" / "nodes" / "x.md"
        sources = sources_module.GoverningSources(memory, self.temp)
        return sources, Plan("set", target)


class InventoryTests(Temp):
    """`inventory`: one read per document, hashed as it was read."""

    def test_a_crlf_document_is_hashed_as_its_lf_text(self) -> None:
        sources, plan = self.governed()
        (self.temp / "RULES.md").write_bytes(b"# Rules\r\n\r\nOne.\r\n")
        row = next(
            r for r in sources.inventory(plan) if r["path"].endswith("RULES.md")
        )
        self.assertEqual(
            row["digest"], hashlib.sha256(b"# Rules\n\nOne.\n").hexdigest()
        )
        self.assertEqual(row["text"], "# Rules\n\nOne.\n")
        # The capture keeps the bytes on disk, so the recheck compares the file.
        self.assertEqual(row["data"], b"# Rules\r\n\r\nOne.\r\n")

    def test_an_absent_candidate_has_no_digest_and_no_bytes(self) -> None:
        sources, plan = self.governed()
        row = next(
            r
            for r in sources.inventory(plan)
            if Path(r["path"]).name == "MEMORY.md"
            and Path(r["path"]).parent.name == "nodes"
        )
        self.assertIsNone(row["digest"])
        self.assertIsNone(row["data"])
        self.assertEqual(row["text"], "")

    def test_a_duplicated_source_is_bound_once(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        paths = [
            os.path.normcase(str(Path(row["path"]).resolve())) for row in inventory
        ]
        self.assertEqual(len(paths), len(set(paths)))

    def test_a_non_utf8_document_refuses_the_inventory(self) -> None:
        sources, plan = self.governed()
        (self.temp / "RULES.md").write_bytes(b"# Rules\n\xff\n")
        with self.assertRaises(sources_module.SourceError) as raised:
            sources.inventory(plan)
        self.assertIn("not readable UTF-8 text", str(raised.exception))

    def test_a_required_document_that_is_gone_refuses_the_inventory(self) -> None:
        sources, plan = self.governed()
        (self.temp / "RULES.md").unlink()
        with self.assertRaises(sources_module.SourceError) as raised:
            sources.inventory(plan)
        self.assertIn("does not exist", str(raised.exception))

    def test_a_missing_root_document_refuses_the_inventory(self) -> None:
        sources, plan = self.governed(root=None)
        with self.assertRaises(sources_module.SourceError) as raised:
            sources.inventory(plan)
        self.assertIn("does not exist", str(raised.exception))

    def test_an_unreadable_context_document_refuses_the_inventory(self) -> None:
        sources, plan = self.governed()
        (sources.memory_root / "STRUCTURE.md").mkdir()
        with self.assertRaises(sources_module.SourceError) as raised:
            sources.inventory(plan)
        self.assertIn("not a readable regular file", str(raised.exception))


class SourceDiscoveryTests(Temp):
    """Every document that binds a change is captured, and no other."""

    def rows(self, sources, plan) -> list[str]:
        return [row["path"] for row in sources.inventory(plan)]

    def test_the_owner_documents_reach_the_inventory(self) -> None:
        sources, plan = self.governed()
        (self.temp / "RULES.md").write_text("# Rules\n\nVerify first.\n", "utf-8")
        rows = sources.inventory(plan)
        texts = " ".join(row["text"] for row in rows)
        self.assertIn("Verify first.", texts)
        self.assertIn("Structure-Version: v1", texts)

    def test_a_governing_group_memory_above_the_target_is_captured(self) -> None:
        memory = self.memory("# Root\n")
        group = memory / "local" / "nodes" / "projects"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text(
            "# Projects\n\n## Mandatory conventions\n\n- Name the release first.\n",
            encoding="utf-8",
        )
        plan = Plan("create", group / "release.md")
        sources = sources_module.GoverningSources(memory, self.temp)
        texts = " ".join(row["text"] for row in sources.inventory(plan))
        self.assertIn("Name the release first.", texts)

    def test_a_group_memory_beside_the_memory_root_is_left_to_the_context(self) -> None:
        memory = self.memory("# Root\n")
        plan = Plan("create", memory / "x.md")
        sources = sources_module.GoverningSources(memory, self.temp)
        root_rows = [
            row
            for row in sources.inventory(plan)
            if Path(row["path"]) == memory / "MEMORY.md"
        ]
        self.assertEqual(len(root_rows), 1)
        self.assertEqual(root_rows[0]["text"].count("# Root"), 1)

    def test_a_move_is_captured_by_the_conventions_of_both_ends(self) -> None:
        memory = self.memory("# Root\n")
        source = memory / "local"
        target = memory / "shared"
        target.mkdir(parents=True)
        (target / "MEMORY.md").write_text(
            "# Shared\n\n## Mandatory conventions\n\n- Cite the target group.\n",
            encoding="utf-8",
        )
        source.mkdir(parents=True, exist_ok=True)
        plan = Plan("move", source / "x.md")
        plan.effects[0].destination = target / "x.md"
        sources = sources_module.GoverningSources(memory, self.temp)
        texts = " ".join(row["text"] for row in sources.inventory(plan))
        self.assertIn("Cite the target group.", texts)

    def test_an_attachment_is_captured_with_its_leaf_and_not_its_conventions(
        self,
    ) -> None:
        memory = self.memory("# Root\n")
        nodes = memory / "local" / "nodes"
        nodes.mkdir(parents=True)
        (nodes / "x.md").write_text("# X\n\nThe leaf's own text.\n", encoding="utf-8")
        attachment = nodes / "x"
        attachment.mkdir()
        (attachment / "MEMORY.md").write_text(
            "# Not a convention\n\n- Ignore me.\n", encoding="utf-8"
        )
        plan = Plan("attach", attachment / "figure.png")
        sources = sources_module.GoverningSources(memory, self.temp)
        texts = " ".join(row["text"] for row in sources.inventory(plan))
        self.assertIn("The leaf's own text.", texts)
        self.assertNotIn("Ignore me.", texts)

    def test_a_nested_attachment_keeps_its_own_tree_out_of_the_requirement(
        self,
    ) -> None:
        memory = self.memory("# Root\n")
        nodes = memory / "local" / "nodes"
        nodes.mkdir(parents=True)
        (nodes / "x.md").write_text("# X\n\nThe leaf's own text.\n", encoding="utf-8")
        nested = nodes / "x" / "assets"
        nested.mkdir(parents=True)
        (nested / "MEMORY.md").write_text(
            "# Not a convention\n\n- Ignore me.\n", encoding="utf-8"
        )
        plan = Plan("attach", nested / "figure.png")
        sources = sources_module.GoverningSources(memory, self.temp)
        texts = " ".join(row["text"] for row in sources.inventory(plan))
        self.assertIn("The leaf's own text.", texts)
        self.assertNotIn("Ignore me.", texts)


class CapturedRevisionTests(Temp):
    """The inventory is one capture, and the freshness check compares it."""

    def test_the_inventory_reads_each_source_once_and_derives_rows_from_it(
        self,
    ) -> None:
        sources, plan = self.governed()
        original = sources_module.GoverningSources.state
        calls: list[Path] = []

        def counted(path: Path):
            calls.append(Path(path))
            return original(path)

        with mock.patch.object(sources, "state", side_effect=counted):
            inventory = sources.inventory(plan)
        keys = [
            os.path.normcase(str(path.resolve(strict=False))) for path in calls
        ]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(calls), len(inventory))
        for row in inventory:
            if row["data"] is None:
                self.assertIsNone(row["digest"])
                self.assertEqual(row["text"], "")
                continue
            text = (
                row["data"].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            )
            self.assertEqual(row["text"], text)
            self.assertEqual(
                row["digest"], hashlib.sha256(text.encode("utf-8")).hexdigest()
            )

    def test_an_unchanged_inventory_still_reads_as_it_was_captured(self) -> None:
        sources, plan = self.governed()
        self.assertTrue(sources.unchanged(sources.inventory(plan)))

    def test_a_document_edited_after_the_capture_is_a_change(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        (self.temp / "RULES.md").write_text("changed\n", encoding="utf-8")
        self.assertFalse(sources.unchanged(inventory))

    def test_a_document_removed_after_the_capture_is_a_change(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        (self.temp / "RULES.md").unlink()
        self.assertFalse(sources.unchanged(inventory))

    def test_an_absent_candidate_created_after_the_capture_is_a_change(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        candidate = sources.memory_root / "local" / "nodes" / "MEMORY.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("new conventions\n", encoding="utf-8")
        self.assertFalse(sources.unchanged(inventory))

    def test_an_absent_candidate_that_becomes_a_directory_is_a_change(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        candidate = sources.memory_root / "local" / "nodes" / "MEMORY.md"
        candidate.mkdir(parents=True, exist_ok=True)
        self.assertFalse(sources.unchanged(inventory))

    def test_an_absent_candidate_that_becomes_a_symlink_is_a_change(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        candidate = sources.memory_root / "local" / "nodes" / "MEMORY.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        target = self.temp / "elsewhere.md"
        target.write_text("elsewhere", encoding="utf-8")
        try:
            candidate.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("this host does not allow creating a symlink")
        self.assertFalse(sources.unchanged(inventory))

    def test_the_freshness_check_never_replaces_the_captured_rows(self) -> None:
        sources, plan = self.governed()
        inventory = sources.inventory(plan)
        captured = [dict(row) for row in inventory]
        (self.temp / "RULES.md").write_text("edited\n", encoding="utf-8")
        self.assertFalse(sources.unchanged(inventory))
        self.assertEqual(inventory, captured)


class RemovedJudgePathTests(unittest.TestCase):
    """The model judge is gone from the packaged, executable source set."""

    OBSOLETE = (
        "hooks/rm_judge.py",
        "hooks/rm_judge_policy.py",
        "hooks/rm_judge_process.py",
        "hooks/judge-policy.json",
    )
    FORBIDDEN = (
        "MemoryJudge",
        "rm_judge",
        "load_judge",
        "JudgePolicy",
        "JudgeLaunch",
        "JudgeProcess",
        "RefusingJudge",
        "AMS_JUDGE_",
        "judge-",
    )

    def executable_sources(self) -> list[Path]:
        found: list[Path] = []
        for pattern in ("*.py", "*.ts", "*.mjs"):
            found.extend(REPO.glob(f"hooks/**/{pattern}"))
            found.extend(REPO.glob(f"extensions/**/{pattern}"))
            found.extend(REPO.glob(f"scripts/**/{pattern}"))
        return sorted(
            path
            for path in found
            if "__pycache__" not in path.parts and path.is_file()
        )

    def test_the_obsolete_modules_and_policy_are_absent(self) -> None:
        manifest = json.loads(
            (REPO / "package.json").read_text(encoding="utf-8")
        )
        self.assertIn("hooks/", manifest["files"])
        for relative in self.OBSOLETE:
            self.assertFalse((REPO / relative).exists(), relative)

    def test_no_executable_source_names_the_removed_judge(self) -> None:
        sources = self.executable_sources()
        self.assertIn(REPO / "hooks" / "rm_update.py", sources)
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for token in self.FORBIDDEN:
                self.assertNotIn(
                    token, text, f"{path.relative_to(REPO)} names {token}"
                )


if __name__ == "__main__":
    unittest.main()
