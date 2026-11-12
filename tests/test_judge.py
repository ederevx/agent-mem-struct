#!/usr/bin/env python3
"""The compliance judge: configuration, prompt, answer contract, and boundary."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
TESTS = Path(__file__).resolve().parent
SCRATCH = Path(
    os.environ.get(
        "AGENT_MEM_STRUCT_TEST_TMP",
        str(Path.home() / "tmp" / "agent-mem-struct-tests"),
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)
LIMITS = {
    "timeout_s": 30,
    "max_input_bytes": 65536,
    "capture_bytes": 4096,
    "teardown_s": 2,
    "completion_reserve_s": 10,
}


def load(name: str):
    """Import one hook module once, so every test shares its classes."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    return importlib.import_module(name)


def integration():
    """The hook-level test helpers, for the end-to-end gating tests."""
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_pi_integration

    return test_pi_integration


judge = load("rm_judge")
policy = load("rm_judge_policy")
process = load("rm_judge_process")
plan_module = load("rm_update_plan")


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ams-judge-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.temp, True)

    def document(self, name: str, value: object) -> Path:
        path = self.temp / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def settings(self, limits: dict | None = None, pi_settings: Path | None = None) -> Path:
        """One settings document for the shipped policy."""
        document: dict[str, object] = {
            "schema": 3,
            "limits": {**LIMITS, **(limits or {})},
        }
        if pi_settings is not None:
            document["pi_settings"] = str(pi_settings)
        return self.document("memory-judge.json", document)

    def loaded(self, settings: Path | None = None):
        # The host process's own provider and model are not this agent's.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                policy.ENV_POLICY,
                policy.ENV_SETTINGS,
                "PI_PROVIDER",
                "PI_MODEL",
                "PI_CODING_AGENT_DIR",
            )
        }
        if settings is not None:
            environment[policy.ENV_SETTINGS] = str(settings)
        return policy.JudgePolicy.load(
            hook_dir=HOOKS, agent_home=self.temp, environ=environment
        )

    def launch(self, **limits: object):
        host = self.document(
            "pi-settings.json", {"defaultProvider": "test-provider", "defaultModel": "m"}
        )
        return self.loaded(self.settings(limits=dict(limits), pi_settings=host)).resolve()

    def memory(self, root: str | None = None) -> Path:
        memory = self.temp / "memory"
        memory.mkdir(exist_ok=True)
        if root is not None:
            (memory / "MEMORY.md").write_text(root, encoding="utf-8")
        return memory


class PolicyTests(Temp):
    """`JudgePolicy`: the shipped policy and one optional user document."""

    def test_shipped_policy_resolves_a_launcher_and_its_limits(self) -> None:
        launch = self.launch()
        self.assertEqual(launch.identity(), "test-provider/m")
        self.assertEqual(launch.argv[0], "pi")
        self.assertIn("--no-tools", launch.argv)
        self.assertIn("--no-extensions", launch.argv)
        self.assertEqual(launch.limit("timeout_s"), 30)

    def test_unknown_key_in_the_user_document_is_refused(self) -> None:
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.document("memory-judge.json", {"schema": 3, "review": "yes"}))
        self.assertIn("unknown keys", caught.exception.detail)

    def test_a_selected_document_that_is_missing_is_refused(self) -> None:
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.temp / "absent.json")
        self.assertIn("does not exist", caught.exception.detail)

    def test_duplicate_keys_are_refused(self) -> None:
        path = self.temp / "memory-judge.json"
        path.write_text('{"schema": 3, "schema": 3}', encoding="utf-8")
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(path)
        self.assertIn("duplicate key", caught.exception.detail)

    def test_a_limit_must_be_a_positive_finite_number(self) -> None:
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.settings(limits={"timeout_s": 0}))
        self.assertIn("positive finite", caught.exception.detail)

    def test_every_limit_must_be_stated(self) -> None:
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.document("memory-judge.json",
                                      {"schema": 3, "limits": {"timeout_s": 5}}))
        self.assertIn("is missing", caught.exception.detail)

    def test_a_missing_provider_or_model_refuses_the_launch(self) -> None:
        host = self.document("pi-settings.json", {})
        loaded = self.loaded(self.settings(pi_settings=host))
        with self.assertRaises(policy.JudgeError) as caught:
            loaded.resolve()
        self.assertIn("neither in the environment nor in", caught.exception.detail)

    def test_an_unreadable_policy_makes_the_judge_refuse_every_write(self) -> None:
        broken = self.document("broken-policy.json", {"schema": 2})
        with mock.patch.dict(os.environ, {policy.ENV_POLICY: str(broken)}):
            refusing = judge.load_judge(self.temp / "memory", self.temp)
        verdict = refusing.review(None)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-invalid-config")

    def launcher_document(self, extensions: list[str]) -> Path:
        host = self.document(
            "pi-settings.json", {"defaultProvider": "test-provider", "defaultModel": "m"}
        )
        return self.document(
            "memory-judge.json",
            {
                "schema": 3,
                "limits": LIMITS,
                "pi_settings": str(host),
                "launchers": {"pi": {"extensions": extensions}},
            },
        )

    def test_a_literal_extension_entry_is_loaded_before_the_provider_flag(self) -> None:
        entry = self.temp / "provider.ts"
        entry.write_text("export {};\n", encoding="utf-8")
        loaded = self.loaded(self.launcher_document([str(entry), str(entry)]))
        launch = loaded.resolve()
        self.assertEqual(launch.argv.count("-e"), 1)
        self.assertEqual(launch.argv[launch.argv.index("-e") + 1], str(entry.resolve()))

    def test_an_extension_that_is_not_a_regular_entry_file_is_refused(self) -> None:
        directory = self.temp / "provider-package"
        directory.mkdir()
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.launcher_document([str(directory)])).resolve()
        self.assertIn("entry file", caught.exception.detail)

    def test_a_relative_extension_is_refused(self) -> None:
        with self.assertRaises(policy.JudgeError) as caught:
            self.loaded(self.launcher_document(["hooks/provider.ts"])).resolve()
        self.assertIn("absolute path", caught.exception.detail)


class Plan:
    """A stand-in plan: one write whose body is the judged content."""

    def __init__(self, operation: str, body: str = "Current state.\n") -> None:
        self.operation = operation
        self.intent = {"operation": operation, "path": "/memory/local/nodes/x.md"}
        self.effects = [
            plan_module.FileEffect(
                plan_module.WRITE,
                Path("/memory/local/nodes/x.md"),
                b"",
                body.encode("utf-8"),
            )
        ]

    def changes(self):
        return list(self.effects)


class PromptTests(Temp):
    """`MemoryJudge`'s prompt: the requirement, the draft, and its bound."""

    def test_the_prompt_states_the_requirement_the_draft_and_the_contract(self) -> None:
        root = "# Root\n\nStructure-Version: v1"
        judged = judge.MemoryJudge(self.loaded(), self.memory(root))
        text = judged.prompt(self.launch(), Plan("set", "Sharpened.\n")).decode("utf-8")
        self.assertIn("## The requirement", text)
        self.assertIn("secrets", text)
        self.assertIn("Structure-Version: v1", text)
        self.assertIn('"operation": "set"', text)
        self.assertIn(f"### write: {Path('/memory/local/nodes/x.md')}", text)
        self.assertIn("Sharpened.", text)
        self.assertIn("exactly one word", text)

    def test_a_missing_required_document_refuses_the_judgment(self) -> None:
        judged = judge.MemoryJudge(self.loaded(), self.memory())
        with self.assertRaises(policy.JudgeError) as caught:
            judged.prompt(self.launch(), Plan("create"))
        self.assertEqual(caught.exception.code, "judge-missing-source")

    def test_the_rules_and_structure_documents_reach_the_prompt(self) -> None:
        root = "# Root\n"
        (self.temp / "RULES.md").write_text("# Rules\n\nVerify first.\n", encoding="utf-8")
        (self.temp / "STRUCTURE.md").write_text(
            "Structure-Version: v1\n", encoding="utf-8"
        )
        judged = judge.MemoryJudge(self.loaded(), self.memory(root), self.temp)
        text = judged.prompt(self.launch(), Plan("set")).decode("utf-8")
        self.assertIn("Verify first.", text)
        self.assertIn("Structure-Version: v1", text)

    def test_a_draft_over_the_input_limit_is_refused_not_truncated(self) -> None:
        judged = judge.MemoryJudge(self.loaded(), self.memory("# Root\n"))
        with self.assertRaises(policy.JudgeError) as caught:
            judged.prompt(self.launch(max_input_bytes=500), Plan("set", "x" * 1000))
        self.assertEqual(caught.exception.code, "judge-prompt-too-large")
        self.assertEqual(caught.exception.stage, "prompt")

    def test_a_removed_leaf_is_shown_by_its_content(self) -> None:
        judged = judge.MemoryJudge(self.loaded(), self.memory("# Root\n"))
        plan = Plan("retire")
        plan.effects[0].kind = plan_module.DELETE
        plan.effects[0].before = b"the old body\n"
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("### delete (removed content)", text)
        self.assertIn("the old body", text)

    def test_a_governing_group_memory_above_the_target_reaches_the_prompt(self) -> None:
        root = self.memory("# Root\n")
        group = root / "local" / "nodes" / "projects"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text(
            "# Projects\n\n## Mandatory conventions\n\n- Name the release first.\n",
            encoding="utf-8",
        )
        plan = Plan("create")
        plan.effects[0].path = group / "release.md"
        judged = judge.MemoryJudge(self.loaded(), root)
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("Name the release first.", text)

    def test_a_group_memory_beside_the_memory_root_is_left_to_the_context(self) -> None:
        root = self.memory("# Root\n")
        plan = Plan("create")
        plan.effects[0].path = root / "x.md"
        judged = judge.MemoryJudge(self.loaded(), root)
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertEqual(text.count("# Root"), 1)

    def test_a_move_is_judged_by_the_conventions_of_both_ends(self) -> None:
        root = self.memory("# Root\n")
        source = root / "local"
        target = root / "shared"
        target.mkdir(parents=True)
        (target / "MEMORY.md").write_text(
            "# Shared\n\n## Mandatory conventions\n\n- Cite the target group.\n",
            encoding="utf-8",
        )
        source.mkdir(parents=True, exist_ok=True)
        plan = Plan("move")
        plan.effects[0].path = source / "x.md"
        plan.effects[0].destination = target / "x.md"
        judged = judge.MemoryJudge(self.loaded(), root)
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("Cite the target group.", text)

    def test_an_attachment_is_judged_with_its_leaf_and_not_its_own_conventions(
        self,
    ) -> None:
        root = self.memory("# Root\n")
        nodes = root / "local" / "nodes"
        nodes.mkdir(parents=True)
        (nodes / "x.md").write_text("# X\n\nThe leaf's own text.\n", encoding="utf-8")
        attachment = nodes / "x"
        attachment.mkdir()
        (attachment / "MEMORY.md").write_text(
            "# Not a convention\n\n- Ignore me.\n", encoding="utf-8"
        )
        plan = Plan("attach", "")
        plan.effects[0].path = attachment / "figure.png"
        judged = judge.MemoryJudge(self.loaded(), root)
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("The leaf's own text.", text)
        self.assertNotIn("Ignore me.", text)

    def test_a_nested_attachment_keeps_its_own_tree_out_of_the_requirement(
        self,
    ) -> None:
        root = self.memory("# Root\n")
        nodes = root / "local" / "nodes"
        nodes.mkdir(parents=True)
        (nodes / "x.md").write_text("# X\n\nThe leaf's own text.\n", encoding="utf-8")
        nested = nodes / "x" / "assets"
        nested.mkdir(parents=True)
        (nested / "MEMORY.md").write_text(
            "# Not a convention\n\n- Ignore me.\n", encoding="utf-8"
        )
        plan = Plan("attach", "")
        plan.effects[0].path = nested / "figure.png"
        judged = judge.MemoryJudge(self.loaded(), root)
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("The leaf's own text.", text)
        self.assertNotIn("Ignore me.", text)

    def test_an_unreadable_context_document_refuses_the_judgment(self) -> None:
        root = self.memory("# Root\n")
        (root / "STRUCTURE.md").mkdir()
        judged = judge.MemoryJudge(self.loaded(), root)
        with self.assertRaises(policy.JudgeError) as caught:
            judged.prompt(self.launch(), Plan("set"))
        self.assertEqual(caught.exception.code, "judge-missing-source")

    def test_binary_content_is_shown_by_its_digest(self) -> None:
        judged = judge.MemoryJudge(self.loaded(), self.memory("# Root\n"))
        plan = Plan("attach", "")
        plan.effects[0].after = b"\xff\xfe\x00binary"
        text = judged.prompt(self.launch(), plan).decode("utf-8")
        self.assertIn("binary:", text)
        self.assertIn("base64", text)
        self.assertIn("sha256", text)


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


class PresentationTests(Temp):
    """A write is shown as its change, with both whole states bound."""

    def effect(self, before, after, path: str = "/memory/local/nodes/x.md"):
        return plan_module.FileEffect(plan_module.WRITE, Path(path), before, after)

    def changed(self, before, after) -> str:
        return judge.MemoryJudge.changed(self.effect(before, after))

    def test_every_changed_line_is_shown_and_the_hunks_rebuild_it(self) -> None:
        before = b"# Node\n\n## One\n\nalpha\nbeta\n"
        after = b"# Node\n\n## One\n\nalpha\nBETA\ngamma\n"
        text = self.changed(before, after)
        for row in (["-", "beta\n"], ["+", "BETA\n"], ["+", "gamma\n"]):
            self.assertIn(json.dumps(row), text)
        hunks = judge.MemoryJudge.hunks(before, after)
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
        self.assertEqual(replay(before, judge.MemoryJudge.hunks(before, after)), after)
        text = self.changed(before, after)
        self.assertIn("\\r\\n", text)
        self.assertIn(json.dumps(["-", "beta"]), text)

    def test_unicode_is_shown_and_rebuilt_exactly(self) -> None:
        before = "# N\u0153ud\n\n\u00e9tat \u2014 ancien\n".encode("utf-8")
        after = "# N\u0153ud\n\n\u00e9tat \u2014 nouveau\n".encode("utf-8")
        self.assertEqual(replay(before, judge.MemoryJudge.hunks(before, after)), after)
        self.assertIn("nouveau", self.changed(before, after))

    def test_a_separated_change_is_its_own_hunk_in_its_section(self) -> None:
        filler = b"a paragraph between the two sections\n" * 8
        before = b"# Node\n\n## A\n\nalpha\n\n" + filler + b"## B\n\nbeta\n"
        after = b"# Node\n\n## A\n\nALPHA\n\n" + filler + b"## B\n\nBETA\n"
        hunks = judge.MemoryJudge.hunks(before, after)
        self.assertEqual(len(hunks), 2)
        text = self.changed(before, after)
        self.assertIn("in section: ## A", text)
        self.assertIn(json.dumps([" ", "## B\n"]), text)
        self.assertEqual(replay(before, hunks), after)

    def test_a_created_leaf_is_shown_by_all_of_its_bytes(self) -> None:
        text = judge.MemoryJudge.changed(self.effect(None, b"# New\n\nbody\n"))
        self.assertIn("Before: (absent)", text)
        self.assertIn(json.dumps(["+", "# New\n"]), text)
        self.assertIn(json.dumps(["+", "body\n"]), text)

    def test_a_binary_side_encodes_both_sides_instead_of_lines(self) -> None:
        text = judge.MemoryJudge.changed(self.effect(b"text\n", b"\xff\xfe\x00binary"))
        self.assertIn("base64", text)
        self.assertIn("binary:", text)

    def test_a_write_with_identical_bytes_says_it_changes_nothing(self) -> None:
        self.assertIn("changes nothing", judge.MemoryJudge.changed(self.effect(b"s\n", b"s\n")))

    def test_a_large_leaf_and_its_log_are_shown_by_their_change_only(self) -> None:
        leaf = b"# Node\n\n## State\n\n" + b"a stored paragraph\n" * 3500
        log = b"# Log\n\n" + b"a log paragraph\n" * 6000
        plan = Writes(
            self.effect(leaf, leaf + b"a new line\n"),
            self.effect(log, log + b"## displaced\n\na moved line\n"),
        )
        rendered = judge.MemoryJudge(self.loaded(), self.memory("# Root\n")).effects(plan)
        self.assertLess(len(rendered), 4096)
        self.assertIn("a new line", rendered)
        self.assertIn("a moved line", rendered)
        self.assertEqual(rendered.count("a stored paragraph"), 3)

    def test_the_prompt_of_such_a_commit_stays_near_its_documents(self) -> None:
        leaf = b"# Node\n\n## State\n\n" + b"a stored paragraph\n" * 3500
        log = b"# Log\n\n" + b"a log paragraph\n" * 6000
        plan = Writes(
            self.effect(leaf, leaf + b"a new line\n"),
            self.effect(log, log + b"## displaced\n\na moved line\n"),
        )
        documents = "# Root\n\n" + "an index line of the store\n" * 800
        judged = judge.MemoryJudge(self.loaded(), self.memory(documents))
        prompt = judged.prompt(self.launch(max_input_bytes=1 << 20), plan)
        self.assertLess(len(prompt), len(documents) + 16000)
        self.assertIn(b"a moved line", prompt)


class AnswerTests(Temp):
    """`MemoryJudge.decide` and `parse`: only a clean `yes` approves."""

    def decide(self, output: bytes, returncode: int = 0, **result: object):
        judged = judge.MemoryJudge(self.loaded(), self.memory())
        result_object = process.ProcessResult(output, returncode, **result)
        return judged.decide(self.launch(), result_object)

    def test_a_clean_yes_approves(self) -> None:
        self.assertTrue(self.decide(b"yes\n").approved)

    def test_surrounding_whitespace_does_not_change_the_word(self) -> None:
        self.assertTrue(self.decide(b"  yes  ").approved)

    def test_a_no_refuses(self) -> None:
        verdict = self.decide(b"no")
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-refused")

    def test_prose_instead_of_one_word_is_refused(self) -> None:
        self.assertEqual(
            self.decide(b"Yes, this looks fine to me.").code, "judge-invalid-answer"
        )

    def test_a_json_answer_is_not_the_contracted_word(self) -> None:
        verdict = self.decide(b'{"verdict": "yes", "reason": "ok"}')
        self.assertEqual(verdict.code, "judge-invalid-answer")

    def test_a_capitalised_word_is_refused(self) -> None:
        self.assertEqual(self.decide(b"Yes").code, "judge-invalid-answer")

    def test_an_answer_that_is_not_utf8_is_refused(self) -> None:
        self.assertEqual(self.decide(b"\xff\xfe\x00").code, "judge-invalid-answer")

    def test_a_cut_off_answer_is_refused(self) -> None:
        verdict = self.decide(b"yes", overflow=True)
        self.assertEqual(verdict.code, "judge-oversized-response")

    def test_a_deadline_refuses_rather_than_waiting_forever(self) -> None:
        verdict = self.decide(b"", timed_out=True, stage="wait", detail="late")
        self.assertEqual(verdict.code, "judge-unavailable")
        self.assertIn("deadline", verdict.message)

    def test_a_failed_launch_refuses_with_its_stage(self) -> None:
        verdict = self.decide(b"", returncode=2, stage="spawn", detail="no such file")
        self.assertEqual(verdict.code, "judge-unavailable")
        self.assertIn("spawn", verdict.message)

    def test_a_nonzero_exit_is_not_a_decision(self) -> None:
        verdict = self.decide(b"yes", returncode=1, stage="capture")
        self.assertEqual(verdict.code, "judge-unavailable")


class Launcher:
    def __init__(self, argv: list[str], environment: dict[str, str] | None = None) -> None:
        self.argv = argv
        self.environment = environment


class ProcessTests(Temp):
    """`JudgeProcess`: bounded capture, a deadline, and a killable tree."""

    def script(self, name: str, body: str) -> Path:
        path = self.temp / name
        path.write_text(body, encoding="utf-8")
        return path

    def run_process(self, script: Path, *, capture: int = 4096, limit: float = 20.0):
        runner = process.JudgeProcess(capture, 1.0)
        launch = Launcher([sys.executable, str(script)])
        started = time.monotonic()
        result = runner.run(launch, b"prompt\n", deadline=time.monotonic() + limit)
        return result, time.monotonic() - started

    def test_a_clean_answer_is_returned_in_full(self) -> None:
        script = self.script(
            "answer.py",
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            'print("no")\n',
        )
        result, _ = self.run_process(script)
        self.assertTrue(result.complete)
        self.assertEqual(result.output.strip(), b"no")

    def test_an_answer_exactly_at_the_bound_is_not_an_overflow(self) -> None:
        script = self.script("exact.py", "import sys\nsys.stdout.write('x' * 8)\n")
        result, _ = self.run_process(script, capture=8)
        self.assertFalse(result.overflow)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.output), 8)

    def test_an_answer_over_the_bound_is_cut_off_and_reported(self) -> None:
        script = self.script("long.py", "import sys\nsys.stdout.write('x' * 10000)\n")
        result, _ = self.run_process(script, capture=100)
        self.assertTrue(result.overflow)
        self.assertEqual(result.stage, "capture")
        self.assertEqual(len(result.output), 100)

    def test_a_hung_judge_is_killed_at_its_deadline(self) -> None:
        script = self.script("hang.py", "import time\ntime.sleep(60)\n")
        result, elapsed = self.run_process(script, limit=1.0)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.stage, "wait")
        self.assertLess(elapsed, 15.0)

    def test_a_descendant_holding_the_answer_open_does_not_outlive_it(self) -> None:
        script = self.script(
            "grandchild.py",
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "sys.exit(0)\n",
        )
        result, elapsed = self.run_process(script, limit=30.0)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.stage, "")
        self.assertLess(elapsed, 15.0)

    def test_a_worker_blocked_past_its_budget_never_blocks_the_caller(self) -> None:
        script = self.script(
            "held.py",
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n",
        )
        runner = process.JudgeProcess(4096, 1.0)
        started = time.monotonic()
        runner.run(
            Launcher([sys.executable, str(script)]),
            b"prompt\n",
            deadline=time.monotonic() + 2.0,
        )
        self.assertLess(time.monotonic() - started, 15.0)

    def test_teardown_never_closes_a_stream_its_worker_still_holds(self) -> None:
        runner = process.JudgeProcess(4096, 0.05)

        class Stream:
            def close(self) -> None:
                raise AssertionError("a blocked worker's stream was closed")

        class Child:
            stdin = Stream()
            stdout = Stream()
            returncode = None

            def wait(self, timeout: float | None = None) -> int:
                return 0

        class Worker:
            def join(self, timeout: float | None = None) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        runner._kill = lambda child, job: None
        runner._sweep = lambda child: None
        runner._teardown(Child(), None, Worker(), Worker())

    def test_a_launcher_that_is_not_on_path_is_refused_before_spawning(self) -> None:
        runner = process.JudgeProcess(4096, 1.0)
        launch = Launcher(["definitely-not-a-command-ams", "--version"])
        result = runner.run(launch, b"", deadline=time.monotonic() + 5)
        self.assertEqual(result.stage, "resolve")
        self.assertIn("not found", result.detail)

    def test_a_passed_deadline_refuses_without_spawning(self) -> None:
        runner = process.JudgeProcess(4096, 1.0)
        launch = Launcher([sys.executable, "-c", "print(1)"])
        result = runner.run(launch, b"", deadline=time.monotonic() - 1)
        self.assertTrue(result.timed_out)

    def test_a_read_that_fails_after_a_yes_refuses_that_answer(self) -> None:
        class Broken:
            def __init__(self) -> None:
                self.chunks = [b"yes\n"]

            def read1(self, size: int) -> bytes:
                if self.chunks:
                    return self.chunks.pop(0)
                raise OSError("the pipe broke")

            def close(self) -> None:
                pass

        class Child:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.stdout = Broken()
                self.returncode = 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

        child = Child()
        runner = process.JudgeProcess(4096, 1.0)
        runner._start = lambda command, launch: (child, None, "")  # type: ignore
        runner._kill = lambda child, job: None  # type: ignore
        runner._sweep = lambda child: None  # type: ignore
        launch = Launcher([sys.executable, "-c", "pass"])
        result = runner.run(launch, b"prompt\n", deadline=time.monotonic() + 20)
        self.assertFalse(result.complete)
        self.assertEqual(result.stage, "capture")
        self.assertIn("pipe broke", result.detail)

    def test_a_batch_launcher_resolves_through_the_interpreter(self) -> None:
        if os.name != "nt":
            self.skipTest("batch launchers are a Windows concern")
        batch = self.temp / "fake-judge.cmd"
        batch.write_text("@echo off\r\necho {}\r\n", encoding="utf-8")
        command, error = process.resolve_command([str(batch)])
        self.assertEqual(error, "")
        self.assertIsInstance(command, str)
        self.assertIn(os.environ["COMSPEC"], command)
        self.assertIn("/c", command)

    def test_a_batch_argument_the_processor_would_expand_is_refused(self) -> None:
        if os.name != "nt":
            self.skipTest("batch launchers are a Windows concern")
        batch = self.temp / "fake-judge.cmd"
        batch.write_text("@echo off" + chr(13) + chr(10), encoding="utf-8")
        command, error = process.resolve_command([str(batch), "%PATH%"])
        self.assertIn("would expand", error)


class ApplyTests(Temp):
    """A plan applies only while the files on disk are still the judged ones."""

    def plan(self, effects: list[object]) -> object:
        return type("Plan", (), {"effects": effects})()

    def effect(self, path: Path, before: bytes, after: bytes):
        return integration().load_hook_module("rm_update_plan").FileEffect(
            "write", path, before, after
        )

    def test_a_file_changed_after_the_judgment_is_reported_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"changed\n")
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        stale = updater.stale_effect(self.plan([self.effect(target, b"judged\n", b"new\n")]))
        self.assertIn("leaf.md", stale)

    def test_a_file_still_holding_the_judged_bytes_is_not_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"judged\n")
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        stale = updater.stale_effect(self.plan([self.effect(target, b"judged\n", b"new\n")]))
        self.assertEqual(stale, "")

    def test_a_second_effect_on_one_path_is_not_stale(self) -> None:
        target = self.temp / "index.md"
        target.write_bytes(b"judged\n")
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        effects = [
            self.effect(target, b"judged\n", b"first\n"),
            self.effect(target, b"first\n", b"second\n"),
        ]
        self.assertEqual(updater.stale_effect(self.plan(effects)), "")

    def test_an_absent_path_that_was_absent_at_judgment_is_not_stale(self) -> None:
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        absent = self.effect(self.temp / "missing.md", None, b"new\n")
        self.assertEqual(updater.stale_effect(self.plan([absent])), "")

    def test_a_publication_reviews_the_committed_bytes_not_the_working_ones(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"edited by hand\n")
        plan_module = integration().load_hook_module("rm_update_plan")
        effect = plan_module.FileEffect(
            "write", target, b"committed\n", b"edited by hand\n",
            observed=b"edited by hand\n",
        )
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        self.assertEqual(updater.stale_effect(self.plan([effect])), "")

    def test_a_working_file_changed_since_the_recording_is_stale(self) -> None:
        target = self.temp / "leaf.md"
        target.write_bytes(b"a third edit\n")
        plan_module = integration().load_hook_module("rm_update_plan")
        effect = plan_module.FileEffect(
            "write", target, b"committed\n", b"edited by hand\n",
            observed=b"edited by hand\n",
        )
        updater = integration().load_hook_module("rm_update").MemoryUpdater
        self.assertIn("leaf.md", updater.stale_effect(self.plan([effect])))

    def test_an_absent_before_is_a_real_prior_state(self) -> None:
        target = self.temp / "new.md"
        target.write_bytes(b"untracked body\n")
        recorder = integration().load_hook_module("rm_update_plan").PlanRecorder()
        recorder.write(target, b"untracked body\n", before=None)
        effect = recorder.effects[0]
        self.assertIsNone(effect.before)
        self.assertEqual(effect.observed, b"untracked body\n")

    def test_an_omitted_before_is_the_observed_bytes(self) -> None:
        target = self.temp / "edited.md"
        target.write_bytes(b"working bytes\n")
        recorder = integration().load_hook_module("rm_update_plan").PlanRecorder()
        recorder.write(target, b"new bytes\n")
        effect = recorder.effects[0]
        self.assertEqual(effect.before, b"working bytes\n")


class LineEndingTests(Temp):
    """Only the CRLF-to-LF rewrite Git performs is accepted as the same content."""

    def converted(self, committed: bytes, approved: bytes) -> bool:
        return integration().load_hook_module("rm_commit").converted(committed, approved)

    def test_a_crlf_working_copy_matches_its_lf_commit(self) -> None:
        self.assertTrue(self.converted(b"one\ntwo\n", b"one\r\ntwo\r\n"))

    def test_a_lone_carriage_return_is_a_difference(self) -> None:
        self.assertFalse(self.converted(b"one\ntwo", b"one\rtwo"))

    def test_a_changed_word_is_a_difference(self) -> None:
        self.assertFalse(self.converted(b"one\ntwo\n", b"one\nother\r\n"))


class ReviewTests(Temp):
    """`MemoryJudge.review`: the answer only counts while the requirement stands."""

    def fake(self, act=None):
        """A judge process that answers `yes` and may run `act` while it does."""
        class FakeProcess:
            def __init__(self, capture, teardown) -> None:
                self.capture = capture

            def run(self, launch, prompt, deadline=None):
                if act is not None:
                    act()
                return process.ProcessResult(b"yes\n", 0)

        return mock.patch.object(judge, "JudgeProcess", FakeProcess)

    def judged(self, root: str = "# Root\n"):
        host = self.document(
            "pi-settings.json", {"defaultProvider": "test-provider", "defaultModel": "m"}
        )
        for name, body in (
            ("RULES.md", "# Rules\n"),
            ("STRUCTURE.md", "Structure-Version: v1\n"),
        ):
            path = self.temp / name
            if not path.exists():
                path.write_text(body, encoding="utf-8")
        return judge.MemoryJudge(
            self.loaded(self.settings(pi_settings=host)), self.memory(root), self.temp
        )

    @staticmethod
    def plan(judged):
        plan = Plan("set")
        plan.effects[0].path = judged.memory_root / "local" / "nodes" / "x.md"
        return plan

    def test_an_unchanged_requirement_is_approved(self) -> None:
        judged = self.judged()
        with self.fake():
            verdict = judged.review(self.plan(judged))
        self.assertTrue(verdict.approved, verdict.message)

    def test_a_document_edited_during_review_refuses_the_write(self) -> None:
        (self.temp / "RULES.md").write_text("# Rules\n", "utf-8")
        judged = self.judged()

        def change() -> None:
            (self.temp / "RULES.md").write_text("changed", "utf-8")

        with self.fake(change):
            verdict = judged.review(self.plan(judged))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-stale-draft")

    def test_a_governing_document_created_during_review_refuses_the_write(self) -> None:
        judged = self.judged()
        group = judged.memory_root / "local" / "nodes"

        def introduce() -> None:
            group.mkdir(parents=True, exist_ok=True)
            (group / "MEMORY.md").write_text("new conventions", "utf-8")

        with self.fake(introduce):
            verdict = judged.review(self.plan(judged))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-stale-draft")

    def test_an_absent_candidate_that_becomes_a_directory_refuses_the_write(
        self,
    ) -> None:
        judged = self.judged()
        candidate = judged.memory_root / "local" / "nodes" / "MEMORY.md"

        def replace() -> None:
            candidate.mkdir(parents=True, exist_ok=True)

        with self.fake(replace):
            verdict = judged.review(self.plan(judged))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-stale-draft")

    def test_an_absent_candidate_that_becomes_a_symlink_refuses_the_write(
        self,
    ) -> None:
        judged = self.judged()
        candidate = judged.memory_root / "local" / "nodes" / "MEMORY.md"
        target = self.temp / "elsewhere.md"
        target.write_text("elsewhere", encoding="utf-8")

        def link() -> None:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            try:
                candidate.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("this host does not allow creating a symlink")

        with self.fake(link):
            verdict = judged.review(self.plan(judged))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-stale-draft")

    def test_a_document_removed_during_review_refuses_the_write(self) -> None:
        (self.temp / "RULES.md").write_text("# Rules\n", "utf-8")
        judged = self.judged()
        with self.fake(lambda: (self.temp / "RULES.md").unlink()):
            verdict = judged.review(self.plan(judged))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.code, "judge-stale-draft")


class GatingTests(Temp):
    """The judge gates the write: a refusal writes nothing, an approval writes."""

    def home_with(self, verdict: str) -> tuple[Path, Path]:
        helpers = integration()
        home, _ = helpers.make_home(self.temp / "home")
        helpers.write_judge_settings(home, verdict)
        return home, home / "memory" / "local"

    def call(self, home: Path, **fields: object) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {"hook_event_name": "MemoryUpdate"}
        event.update(fields)
        command = [
            sys.executable, str(HOOKS / "root-memory-context.py"),
            "--agent", "pi", "--home", str(home), "--canonical-root", str(home),
        ]
        return subprocess.run(
            command, input=json.dumps(event), text=True, capture_output=True, check=False
        )

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def create(self, home: Path, local: Path, name: str) -> subprocess.CompletedProcess[str]:
        return self.call(
            home, operation="create", path=str(local), name=name,
            body="body", summary="A test node.",
        )

    def test_a_refused_write_leaves_memory_untouched(self) -> None:
        home, local = self.home_with("no")
        result = self.create(home, local, "note")
        self.assertEqual(result.returncode, 3, result.stderr)
        text = self.text(result)
        self.assertIn("judge-refused", text)
        self.assertFalse((local / "nodes" / "note.md").exists())

    def test_an_approved_write_is_applied(self) -> None:
        home, local = self.home_with("yes")
        result = self.create(home, local, "kept")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (local / "nodes" / "kept.md").read_text(encoding="utf-8"), "body\n"
        )

    def test_a_renamed_node_takes_its_attachment_directory_with_it(self) -> None:
        home, local = self.home_with("yes")
        self.assertEqual(self.create(home, local, "draft").returncode, 0)
        old = local / "nodes" / "draft"
        old.mkdir()
        (old / "evidence.txt").write_text("data\n", encoding="utf-8")
        result = self.call(
            home, operation="rename", path=str(local / "nodes" / "draft.md"), name="kept"
        )
        self.assertEqual(result.returncode, 0, self.text(result))
        self.assertFalse(old.exists())
        self.assertTrue((local / "nodes" / "kept" / "evidence.txt").is_file())

    def test_a_rename_onto_an_existing_log_writes_nothing(self) -> None:
        home, local = self.home_with("yes")
        self.assertEqual(self.create(home, local, "draft").returncode, 0)
        orphan = local / "nodes" / "log" / "kept.md"
        orphan.write_bytes(b"earlier history\n")
        result = self.call(
            home, operation="rename", path=str(local / "nodes" / "draft.md"), name="kept"
        )
        self.assertEqual(result.returncode, 3, self.text(result))
        self.assertIn("exists", self.text(result))
        self.assertTrue((local / "nodes" / "draft.md").is_file())
        self.assertEqual(orphan.read_bytes(), b"earlier history\n")

    def test_a_rename_onto_an_existing_attachment_directory_writes_nothing(self) -> None:
        home, local = self.home_with("yes")
        self.assertEqual(self.create(home, local, "draft").returncode, 0)
        kept = local / "nodes" / "kept"
        kept.mkdir()
        (kept / "evidence.txt").write_bytes(b"not this node's\n")
        result = self.call(
            home, operation="rename", path=str(local / "nodes" / "draft.md"), name="kept"
        )
        self.assertEqual(result.returncode, 3, self.text(result))
        self.assertTrue((local / "nodes" / "draft.md").is_file())
        self.assertEqual((kept / "evidence.txt").read_bytes(), b"not this node's\n")

    def test_an_unreachable_judge_refuses_the_write(self) -> None:
        home, local = self.home_with("yes")
        document = home / ".agent-mem-struct" / "memory-judge.json"
        settings = json.loads(document.read_text(encoding="utf-8"))
        settings["launchers"]["pi"]["command"] = ["definitely-not-a-judge"]
        settings["launchers"]["pi"]["arguments"] = []
        document.write_text(json.dumps(settings), encoding="utf-8")
        result = self.create(home, local, "gone")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("judge-unavailable", self.text(result))
        self.assertFalse((local / "nodes" / "gone.md").exists())


class PublicationGuardTests(unittest.TestCase):
    """The publication guard reads every unanswered query as a refusal."""

    def setUp(self) -> None:
        self.commit = load("rm_commit")
        self.git = load("rm_git")
        self.temp = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="ams-publish-guard-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.publisher = self.commit.CommitPublisher(self.temp)
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


if __name__ == "__main__":
    unittest.main()
