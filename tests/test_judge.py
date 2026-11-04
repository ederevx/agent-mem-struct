#!/usr/bin/env python3
"""The default-on convention judge: settings, protocol 2, and the windows."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
SCRATCH_ROOT = Path(
    os.environ.get("AGENT_MEM_STRUCT_TEST_TMP", str(Path.home() / "tmp" / "agent-mem-struct-tests"))
)

from rm_control import RootControl  # noqa: E402
from rm_judge import ConventionJudge, digest  # noqa: E402
from rm_judge_settings import JudgeError, JudgePolicy, JudgeSettings  # noqa: E402
from rm_receipts import ConventionGate  # noqa: E402

PASS = "import sys\ntext = sys.stdin.read()\nrequest = __import__('json').loads(text[text.index('{'):])\nsys.stdout.write(request['chain'] + '\\npass\\n')\n"


class JudgeCase(unittest.TestCase):
    """A scratch agent home, its shared tree, and one receipt per session."""

    agent = "claude"

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefixed(), dir=SCRATCH_ROOT))
        self.home = self.root / "agent"
        self.shared = self.root / "shared"
        self.shared.mkdir()
        (self.shared / "MEMORY.md").write_text(
            "# Shared\n\n## Mandatory conventions\n\n- Verify before claiming.\n", encoding="utf-8")
        (self.home / "memory").mkdir(parents=True)
        (self.home / "memory" / "MEMORY.md").write_text(
            f"Structure-Version: t\nStructure: ../STRUCTURE.md\nShared: {self.shared}\n\n# Root\n",
            encoding="utf-8")
        (self.home / "RULES.md").write_text(
            "# Rules\n\n## Mandatory conventions\n\n- Keep continuity.\n", encoding="utf-8")
        (self.home / "STRUCTURE.md").write_text("Structure-Version: t\n", encoding="utf-8")
        self.previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.root / "user")
        self.state = RootControl(self.home, REPO).load(self.agent)
        self.gate = ConventionGate(self.state)
        self.settings_path = self.root / "user" / ".agent-mem-struct" / "judge.json"

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.previous_home

    def stub(self, name: str, body: str) -> list[str]:
        path = self.root / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return [sys.executable, str(path)]

    def settings(self, document: dict | None) -> None:
        if document is None:
            self.settings_path.unlink(missing_ok=True)
            return
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(document), encoding="utf-8")

    def receipt(self, event: dict) -> None:
        sources = {
            os.path.normcase(str(path.resolve())): digest(path.read_text(encoding="utf-8"))
            for path in self.gate.required_read_paths(event, scoped=False)
        }
        path = self.gate.path(event)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.gate.payload(sources, {}), encoding="utf-8")

    def review(self, event: dict, judge: ConventionJudge | None = None) -> dict:
        judge = judge or ConventionJudge(self.state, self.gate)
        plan = judge.prepare(event)
        return judge.review({
            **event, "cursor": plan["cursor"], "config_revision": plan["revision"],
        })


def prefixed() -> str:
    return "judge-"


def assistant(uuid: str, parent: str | None, text: str) -> dict:
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "sessionId": "s1",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class JudgeReviewTests(JudgeCase):
    def setUp(self) -> None:
        super().setUp()
        self.transcript = self.root / "session.jsonl"
        self.event = {"session_id": "s1", "transcript_path": str(self.transcript),
                      "cwd": str(self.root)}

    def write(self, records: list[dict]) -> None:
        self.transcript.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def test_default_on_with_no_settings_document(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        plan = ConventionJudge(self.state, self.gate).prepare(self.event)
        self.assertTrue(plan["enabled"])
        self.assertIsNone(plan["error"])
        self.assertEqual(plan["limits"]["protocol"], 2)
        self.assertEqual(plan["revision"], "")
        self.assertEqual(plan["launch"]["argv"][-2:], ["--agent", "claude"])

    def test_first_window_is_a_baseline_and_a_pass_advances_the_watermark(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertEqual(body["error"], "baseline")
        self.assertEqual(body["notice"], "")
        self.settings({"schema": 2, "command": self.stub("pass.py", PASS)})
        self.write([assistant("a1", None, "first"), assistant("a2", "a1", "second")])
        body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        recorded = self.gate.read_receipt(self.event)["judge"]
        self.assertEqual(recorded["anchor"], "a2")
        self.assertEqual(recorded["seq"], 1)
        self.assertEqual(len(recorded["chain"]), 64)
        self.assertEqual(recorded["source"]["host"], "claude")

    def test_finding_resolves_to_offsets_in_the_supplied_turn(self) -> None:
        quoting = self.stub("quote.py",
                            "import json, sys\n"
                            "text = sys.stdin.read()\n"
                            "request = json.loads(text[text.index('{'):])\n"
                            "sys.stdout.write(request['chain'] + '\\n1 1 ' + json.dumps('Claimed') + '\\n')\n")
        self.settings({"schema": 2, "command": quoting})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.review(self.event)
        self.write([assistant("a1", None, "first"), assistant("a2", "a1", "Claimed it passed.")])
        body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        self.assertEqual(body["findings"][0]["turn"], "a2")
        self.assertEqual(body["findings"][0]["start"], 0)
        self.assertEqual(body["findings"][0]["end"], 7)
        self.assertIn("unmet convention finding", body["notice"])

    def test_reply_grammar_refusals(self) -> None:
        cases = {
            "wrong chain": "print('0' * 64 + '\\npass')\n",
            "pass plus finding": "print(c + '\\npass\\n1 1 \\\"x\\\"')\n",
            "no verdict": "print(c)\n",
            "bad index": "print(c + '\\n0 1 \\\"first\\\"')\n",
            "index beyond turns": "print(c + '\\n1 9 \\\"first\\\"')\n",
            "unquoted evidence": "print(c + '\\n1 1 first')\n",
            "trailing material": "print(c + '\\n1 1 \\\"first\\\" extra')\n",
            "two lines of findings out of order": (
                "print(c + '\\n2 1 \\\"first\\\"\\n1 1 \\\"first\\\"')\n"),
            "carriage return": "print(c + '\\r\\npass')\n",
        }
        for label, body in cases.items():
            with self.subTest(label):
                program = (
                    "import json, sys\n"
                    "text = sys.stdin.read()\n"
                    "request = json.loads(text[text.index('{'):])\n"
                    "c = request['chain']\n" + body
                )
                self.settings({"schema": 2, "command": self.stub("bad.py", program),
                               "max_turns": 32})
                self.write([assistant("a1", None, "first")])
                self.receipt(self.event)
                self.review(self.event)
                self.write([assistant("a1", None, "first"),
                            assistant("a2", "a1", "first and second")])
                refusal = self.review(self.event)
                self.assertEqual(refusal["error"], "invalid_response", (label, refusal))

    def test_oversized_input_leaves_the_watermark_alone(self) -> None:
        self.settings({"schema": 2, "command": self.stub("pass.py", PASS), "max_input_bytes": 200})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.review(self.event)
        self.write([assistant("a1", None, "first"), assistant("a2", "a1", "second")])
        body = self.review(self.event)
        self.assertEqual(body["error"], "oversized_input", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "a1")

    def test_rewritten_history_is_refused_rather_than_rebased(self) -> None:
        self.settings({"schema": 2, "command": self.stub("pass.py", PASS)})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.review(self.event)
        self.write([assistant("a1", None, "rewritten"), assistant("a2", "a1", "second")])
        body = self.review(self.event)
        self.assertEqual(body["error"], "invalid_window", body)

    def test_unknown_record_type_is_refused(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.write([assistant("a1", None, "first"), {"type": "mystery", "uuid": "b1"}])
        body = self.review(self.event)
        self.assertEqual(body["error"], "invalid_window", body)

    def test_reported_last_message_must_match_the_ancestry(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        body = self.review({**self.event, "last_assistant_message": "something else"})
        self.assertEqual(body["error"], "invalid_window", body)

    def test_disabled_settings_run_nothing(self) -> None:
        self.settings({"schema": 2, "enabled": False})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        plan = ConventionJudge(self.state, self.gate).prepare(self.event)
        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["error"], "disabled")
        body = self.review(self.event)
        self.assertEqual(body["error"], "disabled")

    def test_stale_config_between_prepare_and_review_runs_nothing(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        judge = ConventionJudge(self.state, self.gate)
        plan = judge.prepare(self.event)
        self.settings({"schema": 2, "command": self.stub("pass.py", PASS)})
        body = judge.review({**self.event, "cursor": plan["cursor"],
                             "config_revision": plan["revision"]})
        self.assertEqual(body["error"], "stale_config", body)


class CodexWindowTests(JudgeCase):
    agent = "codex"

    def test_ordinal_order_is_required(self) -> None:
        transcript = self.root / "rollout.jsonl"
        event = {"session_id": "c1", "transcript_path": str(transcript), "cwd": str(self.root)}
        records = [
            {"type": "session_meta", "ordinal": 0, "payload": {"session_id": "c1"}},
            {"type": "response_item", "ordinal": 1,
             "payload": {"type": "message", "role": "assistant", "content": "ok"}},
        ]
        transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        self.receipt(event)
        body = self.review(event)
        self.assertEqual(body["error"], "baseline", body)
        self.assertEqual(self.gate.read_receipt(event)["judge"]["anchor"], "1")
        broken = records + [{"type": "response_item", "ordinal": 1,
                             "payload": {"type": "message", "role": "assistant", "content": "again"}}]
        transcript.write_text("".join(json.dumps(r) + "\n" for r in broken), encoding="utf-8")
        self.assertEqual(self.review(event)["error"], "invalid_window")


class JudgeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="judge-settings-", dir=SCRATCH_ROOT))
        self.home = self.root / "agent"
        self.home.mkdir()
        self.previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.root / "user")
        self.state = type("State", (), {"home": self.home, "agent": "claude"})()
        self.settings = JudgeSettings(self.state)
        self.path = self.settings.path()

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.previous_home

    def test_absent_document_is_the_shipped_default(self) -> None:
        config = self.settings.read()
        self.assertEqual(config["source"], "default")
        self.assertTrue(config["enabled"])
        self.assertIsNone(config["command"])
        self.assertEqual(config["protocol"], 2)
        self.assertEqual(config["max_turns"], JudgePolicy.load().defaults()["max_turns"])

    def test_shipped_policy_names_the_defaults(self) -> None:
        defaults = JudgePolicy.load().defaults()
        self.assertEqual(sorted(defaults), ["max_input_bytes", "max_output_bytes", "max_turns",
                                            "protocol", "timeout_s"])

    def test_edits_are_transactional(self) -> None:
        self.settings.apply("set", "", "timeout_s", "12")
        revision = self.settings.read()["revision"]
        self.settings.apply("set", revision, "hosts.claude.extra_env", '["A"]')
        config = self.settings.read()
        self.assertEqual(config["timeout_s"], 12)
        self.assertEqual(config["hosts"]["claude"]["extra_env"], ["A"])
        with self.assertRaises(JudgeError) as stale:
            self.settings.apply("set", revision, "timeout_s", "5")
        self.assertEqual(str(stale.exception), "stale_config")
        self.assertEqual(self.settings.read()["timeout_s"], 12)

    def test_invalid_document_is_never_repaired_or_overwritten(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(JudgeError) as refused:
            self.settings.read()
        self.assertEqual(str(refused.exception), "invalid_config")
        for operation, key, value in (("reset", None, None), ("set", "enabled", "off"),
                                      ("unset", "timeout_s", None)):
            with self.assertRaises(JudgeError):
                self.settings.apply(operation, "", key, value)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")

    def test_unknown_keys_and_values_are_refused(self) -> None:
        for key, value in (("bogus", "1"), ("timeout_s", "soon"), ("enabled", "maybe"),
                           ("command", "not json"), ("protocol", "7")):
            with self.assertRaises(JudgeError) as refused:
                self.settings.apply("set", "", key, value)
            self.assertEqual(str(refused.exception), "invalid_settings_request", key)

    def test_legacy_document_keeps_its_protocol(self) -> None:
        legacy = {"command": ["x"], "timeout_s": 9, "max_turns": 2,
                  "max_input_bytes": 100, "max_output_bytes": 10}
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        config = self.settings.read()
        self.assertEqual(config["protocol"], 1)
        self.assertEqual(config["command"], ["x"])
        self.assertEqual(config["timeout_s"], 9)

    def test_legacy_document_is_imported_only_on_request(self) -> None:
        legacy = self.home / ".agent-mem-struct" / "judge.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"command": ["x"], "timeout_s": 9, "max_turns": 2,
                                     "max_input_bytes": 100, "max_output_bytes": 10}),
                          encoding="utf-8")
        self.assertEqual(self.settings.read()["source"], "default")
        self.settings.apply("import", "", None, None)
        imported = self.settings.read()
        self.assertEqual(imported["protocol"], 1)
        self.assertEqual(imported["timeout_s"], 9)
        self.assertEqual(imported["command"], ["x"])

    def test_reset_removes_numeric_overrides_only(self) -> None:
        self.settings.apply("set", "", "timeout_s", "12")
        self.settings.apply("set", self.settings.read()["revision"], "enabled", "off")
        self.settings.apply("reset", self.settings.read()["revision"], None, None)
        config = self.settings.read()
        self.assertFalse(config["enabled"])
        self.assertEqual(config["timeout_s"], JudgePolicy.load().defaults()["timeout_s"])

    def test_unwritable_directory_reports_a_write_failure(self) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(JudgeError) as refused:
                self.settings.apply("set", "", "timeout_s", "12")
            self.assertIn(str(refused.exception), ("settings_write_failed", "settings_busy"))
        finally:
            directory.chmod(0o700)

    def test_explicit_command_overrides_the_adapter(self) -> None:
        self.settings.apply("set", "", "command", '["my-judge", "--fast"]')
        launch = self.settings.launch(self.settings.read())
        self.assertEqual(launch["argv"], ["my-judge", "--fast"])
        self.assertTrue(launch["explicit"])


if __name__ == "__main__":
    unittest.main()
