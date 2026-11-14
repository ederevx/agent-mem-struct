#!/usr/bin/env python3
"""Plan freshness, working-tree staleness, line endings, and publication guards."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import gc
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
TESTS = Path(__file__).resolve().parent
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


def integration():
    """The hook-level test helpers, for a home and Git repository to work on."""
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_pi_integration

    return test_pi_integration


plan_module = load("rm_update_plan")
update_module = load("rm_update")
commit_module = load("rm_commit")
control_module = load("rm_control")
conventions_module = load("rm_conventions")


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ams-plan-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.temp, True)


class StaleEffectTests(Temp):
    """A plan applies only while the files on disk are still the captured ones."""

    def plan(self, effects: list[object]) -> object:
        return type("Plan", (), {"effects": effects})()

    def effect(self, path: Path, before: bytes, after: bytes):
        return plan_module.FileEffect("write", path, before, after)

    def test_a_file_changed_after_the_plan_is_reported_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"changed\n")
        stale = update_module.MemoryUpdater.stale_effect(
            self.plan([self.effect(target, b"captured\n", b"new\n")])
        )
        self.assertIn("leaf.md", stale)

    def test_a_file_still_holding_the_captured_bytes_is_not_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"captured\n")
        stale = update_module.MemoryUpdater.stale_effect(
            self.plan([self.effect(target, b"captured\n", b"new\n")])
        )
        self.assertEqual(stale, "")

    def test_a_second_effect_on_one_path_is_not_stale(self) -> None:
        target = self.temp / "index.md"
        target.write_bytes(b"captured\n")
        effects = [
            self.effect(target, b"captured\n", b"first\n"),
            self.effect(target, b"first\n", b"second\n"),
        ]
        self.assertEqual(
            update_module.MemoryUpdater.stale_effect(self.plan(effects)), ""
        )

    def test_an_absent_path_that_was_absent_at_capture_is_not_stale(self) -> None:
        absent = self.effect(self.temp / "missing.md", None, b"new\n")
        self.assertEqual(
            update_module.MemoryUpdater.stale_effect(self.plan([absent])), ""
        )

    def test_a_publication_compares_the_committed_bytes_not_the_working_ones(
        self,
    ) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"edited by hand\n")
        effect = plan_module.FileEffect(
            "write",
            target,
            b"committed\n",
            b"edited by hand\n",
            observed=b"edited by hand\n",
        )
        self.assertEqual(
            update_module.MemoryUpdater.stale_effect(self.plan([effect])), ""
        )

    def test_a_working_file_changed_since_the_recording_is_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"a third edit\n")
        effect = plan_module.FileEffect(
            "write",
            target,
            b"committed\n",
            b"edited by hand\n",
            observed=b"edited by hand\n",
        )
        self.assertIn(
            "leaf.md",
            update_module.MemoryUpdater.stale_effect(self.plan([effect])),
        )

    def test_an_absent_before_is_a_real_prior_state(self) -> None:
        target = self.temp / "new.md"
        target.write_bytes(b"untracked body\n")
        recorder = plan_module.PlanRecorder()
        recorder.write(target, b"untracked body\n", before=None)
        effect = recorder.effects[0]
        self.assertIsNone(effect.before)
        self.assertEqual(effect.observed, b"untracked body\n")

    def test_an_omitted_before_is_the_observed_bytes(self) -> None:
        target = self.temp / "edited.md"
        target.write_bytes(b"working bytes\n")
        recorder = plan_module.PlanRecorder()
        recorder.write(target, b"new bytes\n")
        effect = recorder.effects[0]
        self.assertEqual(effect.before, b"working bytes\n")


class LineEndingTests(Temp):
    """Only the CRLF-to-LF rewrite Git performs is accepted as the same content."""

    def converted(self, committed: bytes, approved: bytes) -> bool:
        return commit_module.converted(committed, approved)

    def test_a_crlf_working_copy_matches_its_lf_commit(self) -> None:
        self.assertTrue(self.converted(b"one\ntwo\n", b"one\r\ntwo\r\n"))

    def test_a_lone_carriage_return_is_a_difference(self) -> None:
        self.assertFalse(self.converted(b"one\ntwo", b"one\rtwo"))

    def test_a_changed_word_is_a_difference(self) -> None:
        self.assertFalse(self.converted(b"one\ntwo\n", b"one\nother\r\n"))


class PublicationGuardTests(unittest.TestCase):
    """The publication guard reads every unanswered query as a refusal."""

    def setUp(self) -> None:
        self.git = load("rm_git")
        self.temp = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="ams-publish-guard-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.publisher = commit_module.CommitPublisher(self.temp)
        self.worktree = mock.Mock()
        self.publisher.worktree = self.worktree

    @staticmethod
    def answer(returncode: int = 0, stdout: str = "", stderr: str = ""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def queries(
        self, published: str | None, parents: list[str] | None, head: str | None = "c" * 40
    ) -> None:
        """Answer the guard's queries, or fail the ones named None.

        An empty `published` is Git answering that the ref exists nowhere in
        this repository, which is how an upstream with no published tip reads.
        """
        def run(*args, **_kwargs):
            if args[0] == "rev-parse":
                named = head if args[-1] == "HEAD" else published
                if named is None:
                    return self.answer(128, "", "fatal: not a repository")
                if not named:
                    return self.answer(1, "", "")
                return self.answer(0, named + "\n", "")
            if args[0] == "rev-list":
                if parents is None:
                    return self.answer(1, "", "boom")
                return self.answer(0, " ".join([args[-1], *parents]) + "\n", "")
            return self.answer(1, "", "unexpected query")

        self.worktree.run.side_effect = run
        self.worktree.upstream.return_value = ("origin", "refs/heads/main")
        self.worktree.upstream_ref.return_value = "refs/remotes/origin/main"

    def test_a_commit_based_elsewhere_than_the_published_tip_is_refused(self) -> None:
        self.queries("a" * 40, ["b" * 40])
        self.assertIn("based on", self.publisher.carries("c" * 40))

    def test_a_commit_on_the_published_tip_is_allowed(self) -> None:
        self.queries("a" * 40, ["a" * 40])
        self.assertEqual(self.publisher.carries("b" * 40), "")

    def test_an_unanswered_parent_query_is_refused(self) -> None:
        self.queries("a" * 40, None)
        self.assertIn("would not report the parents", self.publisher.carries("b" * 40))

    def test_an_unanswered_published_tip_is_refused(self) -> None:
        self.queries(None, ["a" * 40])
        self.assertIn("would not report the published tip", self.publisher.carries("b" * 40))
        self.assertIn("would not report the published tip", self.publisher.unpublished())

    def test_a_commit_with_no_published_tip_is_refused(self) -> None:
        self.queries("", ["a" * 40])
        self.assertIn("no published tip", self.publisher.carries("b" * 40))

    def test_history_with_no_published_tip_is_refused(self) -> None:
        self.queries("", ["a" * 40], head="c" * 40)
        self.assertIn("no published tip", self.publisher.unpublished())

    def test_a_failed_tree_lookup_is_not_an_absent_path(self) -> None:
        worktree = self.git.GitWorktree(REPO)
        with mock.patch.object(worktree, "run", return_value=self.answer(1, "", "boom")):
            self.assertEqual(worktree.tree_entry("HEAD", "RULES.md"), (False, None))


class InProcess(Temp):
    """One `MemoryUpdater` over an isolated agent home, with no harness around it."""

    def setUp(self) -> None:
        super().setUp()
        self.home, self.shared = integration().make_home(self.temp / "home")
        self.local = self.home / "memory" / "local"
        self.nodes = self.local / "nodes"
        self.nodes.mkdir(parents=True)
        (self.nodes / "MEMORY.md").write_text("# Nodes\n\n", encoding="utf-8")
        (self.nodes / "log").mkdir()
        (self.nodes / "log" / "MEMORY.md").write_text(
            "# Log: MEMORY\n\nNo semantic history yet.\n", encoding="utf-8"
        )
        (self.nodes / "log" / "note.md").write_text(
            "# Log: note\n\nNo semantic history yet.\n", encoding="utf-8"
        )
        self.leaf = self.nodes / "note.md"
        self.leaf.write_text("# Note\n\nfirst state\n", encoding="utf-8")
        self.git("init", "-b", "main", cwd=self.home / "memory")
        self.git("add", "-A", cwd=self.home / "memory")
        self.git(
            "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed",
            cwd=self.home / "memory",
        )

    def git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return integration().git_run(*args, cwd=cwd)

    def updater(self):
        state = control_module.RootControl(self.home, self.home).load("pi")
        self.assertFalse(state.errors, state.errors)
        self.assertFalse(state.stale, "the test home must not be stale")
        return update_module.MemoryUpdater(None, state)

    # -- requests ------------------------------------------------------------

    def create_request(self, name: str = "fresh") -> dict[str, object]:
        return {
            "operation": "create",
            "path": str(self.local),
            "name": name,
            "body": "body",
            "summary": "A test node.",
        }

    def set_request(self) -> dict[str, object]:
        return {
            "operation": "set",
            "path": str(self.leaf),
            "body": "# Note\n\nsecond state\n",
        }

    def attach_request(self, source: Path) -> dict[str, object]:
        return {
            "operation": "attach",
            "path": str(self.leaf),
            "attachment_name": "figure.txt",
            "attachment_source": str(source),
        }

    def commit_request(self) -> dict[str, object]:
        return {
            "operation": "commit",
            "commit_message": "memory: publish the hand edit\n\nPublishes the edit.",
        }

    # -- the checklist protocol ----------------------------------------------

    def checks(self, conventions: list[dict]) -> list[dict[str, object]]:
        return [
            {
                "source": row["source"],
                "ordinal": row["ordinal"],
                "status": "pass",
                "reasoning": "The change complies with this convention.",
            }
            for row in conventions
        ]

    def event(self, updater, request: dict) -> dict[str, object]:
        """The request as an assessed call, bound to the plan it computes."""
        plan = updater.prepare(request)
        self.assertIsInstance(
            plan, plan_module.UpdatePlan, getattr(plan, "text", lambda: "")()
        )
        inventory = updater.sources.inventory(plan)
        conventions = conventions_module.ConventionChecklist().enumerate(inventory)
        event = dict(request)
        event["checklist"] = {
            "schema": 1,
            "plan_digest": plan.audit_digest(),
            "documents_digest": conventions_module.inventory_digest(inventory),
            "separation": {
                "significant": [
                    {"source": row["source"], "ordinal": row["ordinal"]}
                    for row in conventions
                ],
                "not_applicable": [],
            },
            "checks": self.checks(conventions),
        }
        return event

    def admit(self, request: dict, mutate=None):
        """Run one assessed update whose `mutate` lands after the checklist."""
        updater = self.updater()
        event = self.event(updater, request)
        if mutate is not None:
            validated = updater.conventions.validate

            def validated_then_mutate(*args, **kwargs):
                conflicts = validated(*args, **kwargs)
                mutate()
                return conflicts

            updater.conventions.validate = validated_then_mutate  # type: ignore[method-assign]
        return updater.run(event)

    @staticmethod
    def code(result) -> str:
        return result.conflicts[0]["code"] if result.conflicts else ""


class PlanFreshnessTests(InProcess):
    """A second preparation must be the plan the checklist was assessed on."""

    def test_an_unchanged_plan_matches_its_second_preparation(self) -> None:
        updater = self.updater()
        request = self.set_request()
        first = updater.prepare(request)
        second = updater.prepare(request)
        self.assertEqual(first.matches(second), (True, ""))

    def test_the_submitted_checklist_is_not_bound_to_the_applied_change(self) -> None:
        updater = self.updater()
        event = self.event(updater, self.create_request())

        class Envelope(dict):
            """A submitted checklist that can be watched outlive its evaluation."""

        submitted = Envelope(event["checklist"])
        event["checklist"] = submitted
        reference = weakref.ref(submitted)
        result = updater.run(event)
        self.assertTrue(result.ok, result.text())
        # The updater keeps no copy: once the caller's own request is gone and
        # the written state is read back, nothing of the checklist survives.
        event.clear()
        del event, submitted
        gc.collect()
        self.assertIsNone(reference(), "the checklist outlived its assessment")

    def test_a_changed_target_is_refused_and_never_materialized(self) -> None:
        def mutate() -> None:
            self.leaf.write_text("# Note\n\nan edit after the checklist\n", encoding="utf-8")

        result = self.admit(self.set_request(), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-draft")
        self.assertIn(
            "an edit after the checklist", self.leaf.read_text(encoding="utf-8")
        )

    def test_a_changed_governing_document_is_refused_and_never_materialized(
        self,
    ) -> None:
        def mutate() -> None:
            rules = self.home / "RULES.md"
            rules.write_text(
                rules.read_text(encoding="utf-8").replace(
                    "Keep continuity.", "Keep continuity always."
                ),
                encoding="utf-8",
            )

        result = self.admit(self.create_request(), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-documents")
        self.assertFalse((self.nodes / "fresh.md").exists())

    def test_changed_directory_membership_is_refused_and_never_materialized(
        self,
    ) -> None:
        def mutate() -> None:
            (self.nodes / "other.md").write_text("# Other\n\n", encoding="utf-8")

        result = self.admit(self.create_request(), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-draft")
        self.assertFalse((self.nodes / "fresh.md").exists())

    def test_a_changed_read_only_dependency_is_refused_and_never_materialized(
        self,
    ) -> None:
        source = self.temp / "figure.txt"
        source.write_text("captured bytes\n", encoding="utf-8")

        def mutate() -> None:
            source.write_text("other bytes\n", encoding="utf-8")

        result = self.admit(self.attach_request(source), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-draft")
        self.assertFalse((self.nodes / "note" / "figure.txt").exists())

    def test_a_changed_publication_path_set_is_refused(self) -> None:
        self.leaf.write_text("# Note\n\nhand edit\n", encoding="utf-8")

        def mutate() -> None:
            (self.local / "MEMORY.md").write_text(
                "# Local\n\n**Scope:** this agent\n\n"
                "## Mandatory conventions\n\n(none)\n\nanother line\n",
                encoding="utf-8",
            )

        result = self.admit(self.commit_request(), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-draft")
        self.assertIn("set of published paths", result.conflicts[0]["message"])

    def test_changed_repository_facts_are_refused(self) -> None:
        self.leaf.write_text("# Note\n\nhand edit\n", encoding="utf-8")

        def mutate() -> None:
            self.git(
                "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "--allow-empty", "-m", "an unrelated commit",
                cwd=self.home / "memory",
            )

        result = self.admit(self.commit_request(), mutate)
        self.assertFalse(result.ok)
        self.assertEqual(self.code(result), "stale-draft")
        self.assertIn("repository facts", result.conflicts[0]["message"])


class PrivateMemoryWithoutSubprocessTests(InProcess):
    """A private-memory update needs no executable, provider, or model at all."""

    def forbid_subprocesses(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("a private memory update created a subprocess")

        patches = [
            mock.patch.object(subprocess, "Popen", forbidden),
            mock.patch.object(subprocess, "run", forbidden),
            mock.patch.object(os, "system", forbidden),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_valid_assessed_update_succeeds_with_no_subprocess(self) -> None:
        self.forbid_subprocesses()
        result = self.admit(self.create_request())
        self.assertTrue(result.ok, result.text())
        self.assertEqual(
            (self.nodes / "fresh.md").read_text(encoding="utf-8"), "body\n"
        )
        self.assertFalse(
            (self.home / ".agent-mem-struct" / "memory-audits").exists()
        )

    def test_an_obsolete_judge_document_is_ignored_and_preserved(self) -> None:
        document = self.home / ".agent-mem-struct" / "memory-judge.json"
        document.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps(
            {
                "schema": 3,
                "limits": {"timeout_s": 30},
                "launchers": {"pi": {"command": ["definitely-not-a-judge-ams"]}},
            }
        )
        document.write_text(original, encoding="utf-8")
        self.forbid_subprocesses()
        result = self.admit(self.create_request())
        self.assertTrue(result.ok, result.text())
        self.assertEqual(document.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
