#!/usr/bin/env python3
"""The convention judge: settings, protocol 2, windows, and its wiring."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
SCRATCH_ROOT = Path(
    os.environ.get("AGENT_MEM_STRUCT_TEST_TMP", str(Path.home() / "tmp" / "agent-mem-struct-tests"))
)

from manage_common import EVENT_TIMEOUTS  # noqa: E402
from rm_control import RootControl  # noqa: E402
from rm_events import EventDispatcher  # noqa: E402
from rm_judge import ConventionJudge, digest  # noqa: E402
from rm_judge_settings import (  # noqa: E402
    JudgeError,
    JudgePolicy,
    JudgeSettings,
)
from rm_judge_window import blocks_text, pi_window  # noqa: E402
from rm_receipts import ConventionGate  # noqa: E402

ADAPTER = HOOKS / "rm_judge_adapter.py"
# A stub that answers with the chain and `pass`, like a correct command.
PASS = (
    "import json, sys\n"
    "text = sys.stdin.read()\n"
    "request = json.loads(text[text.index('{'):])\n"
    "sys.stdout.write(request['chain'] + '\\npass\\n')\n"
)


# A stub answers like a POSIX CLI: text-mode stdout would end its lines
# with a carriage return, which the reply grammar refuses.
STUB_PROLOGUE = "import sys\nsys.stdout.reconfigure(newline='\\n')\n"


# A stub that names one finding: rule, turn, and a quote it must locate.
def finding(rule: int, turn: int, quote: str) -> str:
    return (
        "import json, sys\n"
        "text = sys.stdin.read()\n"
        "request = json.loads(text[text.index('{'):])\n"
        f"sys.stdout.write(request['chain'] + '\\n{rule} {turn} ' + json.dumps({quote!r}) + '\\n')\n"
    )


def assistant(uuid: str, parent: str | None, text: str) -> dict:
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "sessionId": "s1",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def pi_entry(identifier: str, parent: str | None, text: str) -> dict:
    return {"id": identifier, "parentId": parent, "message": {"content": text}}


# Path.home() reads USERPROFILE on Windows and HOME everywhere else, so a
# fixture that redirects only one of them still resolves the real user home.
HOME_VARIABLES = ("HOME", "USERPROFILE")


def set_home(home: Path) -> None:
    for name in HOME_VARIABLES:
        os.environ[name] = str(home)


def restore_home(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class JudgeCase(unittest.TestCase):
    """A scratch agent home, its shared tree, a receipt, and a passing command."""

    agent = "claude"

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="judge-", dir=SCRATCH_ROOT))
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
        self.previous_home = {name: os.environ.get(name)
                              for name in HOME_VARIABLES}
        set_home(self.root / "user")
        self.state = RootControl(self.home, REPO).load(self.agent)
        self.gate = ConventionGate(self.state)
        self.settings_path = self.root / "user" / ".agent-mem-struct" / "judge.json"
        # Every review spawns its command, so a test never reaches a real host
        # CLI: the default here is the same stub the individual cases reuse.
        self.command = self.stub("pass.py", PASS)
        self.settings({"schema": 2, "command": self.command})

    def tearDown(self) -> None:
        restore_home(self.previous_home)

    def stub(self, name: str, body: str) -> list[str]:
        path = self.root / name
        path.write_text("#!/usr/bin/env python3\n" + STUB_PROLOGUE + body, encoding="utf-8")
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


class JudgeReviewTests(JudgeCase):
    def setUp(self) -> None:
        super().setUp()
        self.transcript = self.root / "session.jsonl"
        self.event = {"session_id": "s1", "transcript_path": str(self.transcript),
                      "cwd": str(self.root)}

    def test_a_rewritten_earlier_record_is_a_refusal(self) -> None:
        # A window read from the whole transcript proves the history the
        # watermark passed, not only the anchor's own record.
        self.write([assistant("a1", None, "first"), assistant("a2", "a1", "second")])
        self.receipt(self.event)
        self.assertIsNone(self.review(self.event)["error"])
        self.write([assistant("a1", None, "rewritten"), assistant("a2", "a1", "second")])
        self.assertEqual(self.review(self.event)["error"], "invalid_window")

    def test_judge_route_leaves_the_activation_notice_to_the_bridge(self) -> None:
        # The bridge delivers the reply it holds, so a refusal recorded here as
        # well would reach the model twice, on two consecutive turns.
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        self.receipt(self.event)
        dispatcher = EventDispatcher("pi", None, None, self.gate)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            dispatcher.handle_judge(
                {"session_id": "s1", "cwd": str(self.root), "phase": "baseline",
                 "transcript_path": str(path)}, self.state)
        body = json.loads(captured.getvalue())["hookSpecificOutput"]["judge"]
        self.assertIn("invalid_window", body["notice"])
        self.assertEqual(self.gate.notification(self.event), "")

    def test_activation_refusal_survives_the_next_review(self) -> None:
        # The refusal belongs to the turn that produced it, so a later review
        # writing its own state may not swallow the notice before a prompt reads
        # it.
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        self.receipt(self.event)
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        dispatcher.baseline_judge({**self.event, "transcript_path": str(path)},
                                 self.state)
        self.write([assistant("a1", None, "first")])
        self.assertIsNone(self.review(self.event)["error"])
        self.assertIn("invalid_window", self.gate.notification(self.event))

    def test_status_of_an_empty_activation_still_has_a_watermark(self) -> None:
        # A session that starts with no turns has no anchor and is still
        # activated: the epoch, not the anchor, is what says so.
        self.receipt(self.event)
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        dispatcher.baseline_judge(self.event, self.state)
        status = ConventionJudge(self.state, self.gate).status(self.event)
        self.assertIsNone(status["anchor"])
        self.assertTrue(status["anchored"])
        self.assertFalse(status["reviewed"])

    def test_a_consumed_refusal_is_not_carried_again(self) -> None:
        # A prompt reads the receipt notice and clears it, so the refusal
        # activation queued reaches one turn instead of every later one.
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        self.receipt(self.event)
        EventDispatcher("claude", None, None, self.gate).baseline_judge(
            {**self.event, "transcript_path": str(path)}, self.state)
        self.assertIn("invalid_window", self.gate.notification(self.event))
        self.gate.record_notice(self.event, "")
        self.assertEqual(self.gate.notification(self.event), "")

    def test_status_of_a_refused_activation_names_no_watermark(self) -> None:
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        self.receipt(self.event)
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        dispatcher.baseline_judge({**self.event, "transcript_path": str(path)},
                                 self.state)
        status = ConventionJudge(self.state, self.gate).status(self.event)
        self.assertEqual(status["error"], "invalid_window")
        self.assertFalse(status["anchored"])
        self.assertFalse(status["reviewed"])

    def test_activation_refusal_reaches_the_next_prompt(self) -> None:
        # Recording a refusal in the receipt is not delivering it: the notice the
        # next prompt carries is the only channel the user ever sees.
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        self.receipt(self.event)
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        dispatcher.baseline_judge({**self.event, "transcript_path": str(path)},
                                 self.state)
        self.assertIn("invalid_window", self.gate.notification(self.event))

    def test_settings_report_the_last_review(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.assertIsNone(self.review(self.event)["error"])
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        body = dispatcher.judge_settings(
            {**self.event, "config_revision": None}, self.state)
        self.assertIsNone(body["error"], body)
        self.assertEqual(body["review"]["anchor"], "a1")
        self.assertEqual(body["review"]["findings"], 0)
        self.assertIsNone(body["review"]["error"])

    def test_activation_anchors_a_transcript_host_at_its_leaf(self) -> None:
        # Claude and Codex have no inline entries: activation must read the
        # transcript, or the whole history would be reviewed on the first Stop.
        self.write([assistant("a1", None, "first")])
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "config_revision": None})
        self.assertEqual(body["error"], "baseline", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "a1")

    def write(self, records: list[dict]) -> None:
        self.transcript.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def test_default_on_with_no_settings_document(self) -> None:
        self.settings(None)
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        plan = ConventionJudge(self.state, self.gate).prepare(self.event)
        self.assertTrue(plan["enabled"])
        self.assertIsNone(plan["error"])
        self.assertEqual(plan["limits"]["protocol"], 2)
        self.assertEqual(plan["revision"], "")
        self.assertEqual(plan["launch"]["argv"][-2:], ["--agent", "claude"])

    def test_first_review_covers_the_turn_that_triggered_it(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        recorded = self.gate.read_receipt(self.event)["judge"]
        self.assertEqual(recorded["anchor"], "a1")
        self.assertEqual(recorded["seq"], 1)
        self.assertEqual(len(recorded["chain"]), 64)
        self.assertEqual(recorded["source"]["host"], "claude")

    def test_finding_resolves_to_offsets_in_the_supplied_turn(self) -> None:
        self.settings({"schema": 2, "command": self.stub("quote.py", finding(1, 1, "Claimed"))})
        self.write([assistant("a1", None, "Claimed it passed.")])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        self.assertEqual(body["findings"][0]["turn"], "a1")
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
            "trailing whitespace": "print(c + '\\n1 1 \\\"first\\\" \\t')\n",
            "two lines of findings out of order": (
                "print(c + '\\n2 1 \\\"first\\\"\\n1 1 \\\"first\\\"')\n"),
            "two findings for one rule and turn": (
                "print(c + '\\n1 1 \\\"first\\\"\\n1 1 \\\"second\\\"')\n"),
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
                self.settings({"schema": 2, "command": self.stub("bad.py", program)})
                self.write([assistant("a1", None, "first and second")])
                self.receipt(self.event)
                refusal = self.review(self.event)
                self.assertEqual(refusal["error"], "invalid_response", (label, refusal))

    def test_a_turn_the_transport_dropped_cannot_be_cited(self) -> None:
        # The first turn fits, the second does not: the payload carries only the
        # first, so a finding that names the second names a turn nobody saw.
        # A correct answer cannot know its text, and an incorrect one must not
        # be accepted on the strength of a local lookup.
        self.settings({"schema": 2, "command": self.stub("cite.py", finding(1, 2, "SECRET")),
                       "max_input_bytes": 900})
        self.write([assistant("a1", None, "alpha"), assistant("a2", "a1", "SECRET" + "x" * 5000)])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertEqual(body["error"], "invalid_response", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["seq"], 0)

    def test_oversized_input_leaves_the_watermark_alone(self) -> None:
        self.settings({"schema": 2, "command": self.command, "max_input_bytes": 200})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertEqual(body["error"], "oversized_input", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], None)

    def test_rewritten_anchor_is_refused_rather_than_rebased(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.review(self.event)
        self.write([assistant("a1", None, "rewritten"), assistant("a2", "a1", "second")])
        body = self.review(self.event)
        self.assertEqual(body["error"], "invalid_window", body)

    def test_missing_anchor_rebaselines_instead_of_stranding_the_session(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        self.review(self.event)
        # The saved anchor is gone from the source entirely: that is a gap to
        # report, not a rewrite to refuse, or the session could never resume.
        self.write([assistant("b1", None, "second")])
        body = self.review(self.event)
        self.assertEqual(body["error"], "coverage_gap", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "b1")

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
        self.assertEqual(self.review(self.event)["error"], "disabled")

    def test_stale_config_between_prepare_and_review_runs_nothing(self) -> None:
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        judge = ConventionJudge(self.state, self.gate)
        plan = judge.prepare(self.event)
        self.settings({"schema": 2, "command": self.command, "timeout_s": 12})
        body = judge.review({**self.event, "cursor": plan["cursor"],
                             "config_revision": plan["revision"]})
        self.assertEqual(body["error"], "stale_config", body)

    def test_host_override_is_what_the_adapter_runs(self) -> None:
        # The override is the only way a user changes what runs, so it has to
        # reach the child's argv rather than being re-derived from the policy.
        marker = self.root / "argv.json"
        recorder = self.stub("record.py",
                             "import json, sys\n"
                             f"open({str(marker)!r}, 'w').write(json.dumps(sys.argv))\n" + PASS)
        self.settings({"schema": 2, "hosts": {"claude": {"command": recorder}}})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        self.assertIn(str(Path(recorder[1]).resolve()), json.loads(marker.read_text())[0])

    def test_the_adapter_receives_the_resolved_scratch_root(self) -> None:
        root = (self.root / "user" / "review-scratch").resolve()
        marker = self.root / "scratch-root.json"
        recorder = self.stub(
            "scratch-root.py",
            "import json, os\n"
            f"open({str(marker)!r}, 'w').write("
            "json.dumps(os.environ.get('AMS_SCRATCH_ROOT')))\n" + PASS)
        self.settings({"schema": 2, "hosts": {"claude": {"command": recorder}}})
        self.write([assistant("a1", None, "first")])
        self.receipt(self.event)
        with patch.dict(os.environ, {"AMS_SCRATCH_ROOT": "~/review-scratch"}):
            body = self.review(self.event)
        self.assertIsNone(body["error"], body)
        self.assertEqual(json.loads(marker.read_text()), str(root))
        self.assertTrue(root.is_dir())

    def test_adapter_capture_codes_become_local_refusals(self) -> None:
        for code, expected in ((3, "oversized_output"), (4, "invalid_response")):
            with self.subTest(code):
                self.settings({"schema": 2, "command": self.stub(
                    f"exit{code}.py", f"import sys\nsys.exit({code})\n")})
                self.write([assistant("a1", None, "first")])
                self.receipt(self.event)
                self.assertEqual(self.review(self.event)["error"], expected)


class PiWindowTests(JudgeCase):
    agent = "pi"

    def setUp(self) -> None:
        super().setUp()
        self.branch = [pi_entry("a", None, "one"), pi_entry("b", "a", "two")]
        self.event = {"session_id": "s1", "cwd": str(self.root)}
        self.receipt(self.event)

    def settle(self, entries: list[dict], leaf: str) -> dict:
        return self.review({**self.event, "session_entries": entries, "leaf_id": leaf,
                            "session_leaf_id": self.branch[-1]["id"]})

    def test_consecutive_settles_survive_a_bounded_window(self) -> None:
        # Each settle ships a window that starts at the saved anchor, so the
        # same anchor has to produce the same digest from a shorter window.
        body = self.settle([self.branch[0]], "a")
        self.assertIsNone(body["error"], body)
        c = pi_entry("c", "b", "three")
        body = self.settle([self.branch[0], self.branch[1]], "b")
        self.assertIsNone(body["error"], body)
        body = self.review({**self.event, "session_entries": [self.branch[1], c], "leaf_id": "c",
                            "session_leaf_id": "c"})
        self.assertIsNone(body["error"], body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "c")

    def test_anchor_outside_the_window_is_a_coverage_gap(self) -> None:
        self.settle([self.branch[0], self.branch[1]], "b")
        c = pi_entry("c", "b", "three")
        body = self.review({**self.event, "session_entries": [c], "leaf_id": "c",
                            "session_leaf_id": "c"})
        self.assertEqual(body["error"], "coverage_gap", body)

    def test_activation_baseline_anchors_before_the_first_turn(self) -> None:
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "session_entries": self.branch, "leaf_id": "b",
                               "session_leaf_id": "b"})
        self.assertEqual(body["error"], "baseline", body)
        recorded = self.gate.read_receipt(self.event)["judge"]
        self.assertEqual(recorded["anchor"], "b")
        c = pi_entry("c", "b", "three")
        body = self.settle([self.branch[1], c], "c")
        self.assertIsNone(body["error"], body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "c")

    def test_activation_refuses_a_source_it_cannot_read(self) -> None:
        # A resumed session whose transcript is malformed is neither history to
        # accept nor an empty origin: accepting it would claim a baseline nobody
        # could verify, and the next readable review would call it new work.
        path = self.root / "malformed.jsonl"
        path.write_text('{"type": "user", "uuid": "a1"}\nnot json\n')
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "transcript_path": str(path)})
        self.assertEqual(body["error"], "invalid_window", body)
        recorded = self.gate.read_receipt(self.event)["judge"]
        self.assertEqual(recorded["error"], "invalid_window")
        self.assertIsNone(recorded["epoch"])

    def test_activation_treats_an_opened_but_empty_transcript_as_the_origin(self) -> None:
        # A host creates the transcript before it writes to it, so an empty file
        # is a session with no history yet, not a source that could not be read.
        path = self.root / "empty.jsonl"
        path.write_text("")
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "transcript_path": str(path)})
        self.assertEqual(body["error"], "baseline", body)
        recorded = self.gate.read_receipt(self.event)["judge"]
        self.assertIsNotNone(recorded["epoch"])
        self.assertIsNone(recorded["anchor"])

    def test_activation_refuses_a_source_it_cannot_decode(self) -> None:
        # Bytes that are not UTF-8 are an unreadable transcript, not a blank one:
        # they must be refused like any other unreadable source, and not raise
        # through the hook where nothing can record them.
        path = self.root / "binary.jsonl"
        path.write_bytes(b"\xff\xfe\x00invalid")
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "transcript_path": str(path)})
        self.assertEqual(body["error"], "invalid_window", body)
        self.assertIsNone(self.gate.read_receipt(self.event)["judge"]["epoch"])

    def test_activation_treats_a_directory_path_as_a_refusal(self) -> None:
        # Only absence is the empty origin; a path that exists as something other
        # than a transcript is a source the window has to refuse.
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "transcript_path": str(self.root)})
        self.assertEqual(body["error"], "invalid_window", body)

    def test_status_does_not_report_activation_as_a_review(self) -> None:
        judge = ConventionJudge(self.state, self.gate)
        judge.baseline({**self.event, "session_entries": [], "leaf_id": ""})
        status = judge.status(self.event)
        self.assertIsNotNone(status)
        self.assertFalse(status["reviewed"], status)

    def test_activation_accepts_at_most_the_policy_bound(self) -> None:
        branch = [pi_entry("e0", None, "zero")]
        for index in range(1, 70):
            branch.append(pi_entry(f"e{index}", branch[-1]["id"], f"turn {index}"))
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "session_entries": branch,
                              "leaf_id": branch[-1]["id"],
                              "session_leaf_id": branch[-1]["id"]})
        self.assertEqual(body["error"], "baseline", body)
        self.assertEqual(self.gate.read_receipt(self.event)["judge"]["anchor"], "e69")

    def test_activation_without_history_starts_at_the_empty_origin(self) -> None:
        judge = ConventionJudge(self.state, self.gate)
        body = judge.baseline({**self.event, "session_entries": [], "leaf_id": ""})
        self.assertEqual(body["error"], "baseline", body)
        self.assertIsNone(self.gate.read_receipt(self.event)["judge"]["anchor"])

    def test_pi_tool_calls_are_part_of_the_turn(self) -> None:
        self.assertEqual(
            blocks_text([{"type": "toolCall", "name": "bash",
                          "arguments": {"command": "echo test"}}]),
            '[bash] {"command":"echo test"}',
        )
        self.assertEqual(
            blocks_text([{"type": "tool_use", "name": "Read", "input": {"p": 1}}]),
            '[Read] {"p":1}',
        )


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
        self.assertIsNone(body["error"], body)
        self.assertEqual(self.gate.read_receipt(event)["judge"]["anchor"], "1")
        broken = records + [{"type": "response_item", "ordinal": 1,
                             "payload": {"type": "message", "role": "assistant", "content": "again"}}]
        transcript.write_text("".join(json.dumps(r) + "\n" for r in broken), encoding="utf-8")
        self.assertEqual(self.review(event)["error"], "invalid_window")


class StopWiringTests(JudgeCase):
    def test_installed_deadline_exceeds_the_review_deadline(self) -> None:
        # A review the host kills mid-flight loses its notice, so the installed
        # Stop bound has to exceed the review bound with room to persist.
        self.assertGreaterEqual(EVENT_TIMEOUTS["Stop"],
                                JudgePolicy.load().deadline_ceiling() + 20)
        self.assertGreaterEqual(EVENT_TIMEOUTS["SubagentStop"],
                                JudgePolicy.load().deadline_ceiling() + 20)

    def test_configuration_refusal_is_visible_on_stop(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text("{not json", encoding="utf-8")
        event = {"session_id": "s1", "transcript_path": str(self.root / "t.jsonl"),
                 "cwd": str(self.root)}
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        with contextlib.redirect_stdout(io.StringIO()):
            dispatcher.review_on_stop(event, self.state)
        self.assertIn("did not run", self.gate.notification(event))

    def test_unwritable_receipt_falls_back_to_the_host_message(self) -> None:
        event = {"session_id": "s1", "transcript_path": str(self.root / "t.jsonl"),
                 "cwd": str(self.root)}
        dispatcher = EventDispatcher("claude", None, None, self.gate)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            dispatcher.deliver_notice(event, "convention judge: the review did not run (x).")
        self.assertEqual(output.getvalue(), "")


class AdapterCaptureTests(unittest.TestCase):
    """The adapter refuses its own capture instead of repairing it."""

    def run_adapter(self, command: list[str], answer: str = "stdout",
                    environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["AMS_JUDGE_LAUNCH"] = json.dumps(
            {"command": command, "prompt": "stdin", "answer": answer})
        env.update(environment or {})
        return subprocess.run(
            [sys.executable, str(ADAPTER), "--agent", "claude"],
            input=b'{"chain":"c"}', capture_output=True, env=env, timeout=120,
        )

    def program(self, body: str) -> list[str]:
        directory = Path(tempfile.mkdtemp(prefix="adapter-", dir=SCRATCH_ROOT))
        path = directory / "stub.py"
        path.write_text("#!/usr/bin/env python3\n" + STUB_PROLOGUE + body, encoding="utf-8")
        path.chmod(0o700)
        return [sys.executable, str(path)]

    def test_a_flood_is_refused_rather_than_buffered(self) -> None:
        flood = self.program(
            "import sys\n"
            "sys.stdin.read()\n"
            "sys.stdout.write('x' * (9 * 1024 * 1024))\n")
        self.assertEqual(self.run_adapter(flood).returncode, 3)

    def test_invalid_bytes_are_refused_rather_than_replaced(self) -> None:
        invalid = self.program("import sys\nsys.stdin.read()\nsys.stdout.buffer.write(b'\\xff\\xfe')\n")
        self.assertEqual(self.run_adapter(invalid).returncode, 4)

    def test_a_normal_answer_passes_through(self) -> None:
        answer = self.program("import sys\nsys.stdin.read()\nsys.stdout.write('c\\npass\\n')\n")
        completed = self.run_adapter(answer)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, b"c\npass\n")

    def test_a_host_cli_exit_code_is_never_read_as_a_capture_verdict(self) -> None:
        for code in (1, 3, 4):
            failing = self.program(f"import sys\nsys.stdin.read()\nsys.exit({code})\n")
            self.assertEqual(self.run_adapter(failing).returncode, 1, code)

    def test_scratch_lives_in_the_configured_root(self) -> None:
        # The system temporary directory is excluded by convention, and an absent
        # TMPDIR is what used to select it.
        root = Path(tempfile.mkdtemp(prefix="adapter-root-", dir=SCRATCH_ROOT))
        record = root / "answer-path.txt"
        program = self.program(
            "import pathlib, sys\n"
            "sys.stdin.read()\n"
            f"pathlib.Path({str(record)!r}).write_text(sys.argv[-1])\n"
            "pathlib.Path(sys.argv[-1]).write_text('c\\npass\\n')\n")
        completed = self.run_adapter(program, answer="file",
                                     environment={"AMS_SCRATCH_ROOT": str(root)})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(Path(record.read_text()).resolve().is_relative_to(root.resolve()))

    def test_an_unresolvable_scratch_home_is_a_failure(self) -> None:
        from rm_judge_adapter import scratch_root

        with patch.dict(os.environ, {"AMS_SCRATCH_ROOT": ""}):
            with patch.object(Path, "expanduser", side_effect=RuntimeError):
                self.assertIsNone(scratch_root())

    @unittest.skipIf(
        os.name == "nt",
        "Windows has no process groups for the hook to kill",
    )
    def test_the_command_stays_in_the_group_the_hook_kills(self) -> None:
        # The hook enforces the review deadline by killing the process group it
        # started, so a child in a session of its own would survive the deadline
        # and keep billing for an answer nobody reads.
        probe = self.program(
            "import os, sys\n"
            "sys.stdin.read()\n"
            "marker = os.path.join(os.path.dirname(sys.argv[0]), 'pgid.txt')\n"
            "open(marker, 'w').write(str(os.getpgrp()))\n"
            "sys.stdout.write('c\\npass\\n')\n")
        self.assertEqual(self.run_adapter(probe).returncode, 0)
        self.assertEqual(int((Path(probe[1]).parent / "pgid.txt").read_text()), os.getpgrp())


class JudgeLaunchTests(JudgeCase):
    """Program resolution belongs to both process-launch boundaries."""

    def test_windows_cmd_is_spawned_through_comspec(self) -> None:
        from rm_judge_adapter import run

        environment = {
            "PATH": r"C:\tools",
            "PATHEXT": ".EXE;.CMD;.BAT",
            "COMSPEC": r"C:\system tools\cmd.exe",
        }
        process = Mock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(b"c\npass\n")
        process.returncode = 0

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which",
                  return_value=r"C:\tool dir\pi.cmd") as which,
            patch("rm_judge_adapter.scratch_root", return_value=self.root),
            patch("rm_judge_adapter.subprocess.Popen",
                  return_value=process) as popen,
        ):
            result = run(
                {"command": ["pi", "-p", "", "two words"],
                 "prompt": "stdin", "answer": "stdout"},
                'instructions\n{"text":"%PATH% & !value!"}\n',
                4096,
                self.root,
            )

        self.assertEqual(result, (0, b"c\npass\n"))
        which.assert_called_once_with("pi", path=environment["PATH"])
        options = popen.call_args.kwargs
        self.assertEqual(
            options["args"],
            '"C:\\system tools\\cmd.exe" /d /v:off /s /c '
            '""C:\\tool dir\\pi.cmd" -p "" "two words""',
        )
        self.assertEqual(options["executable"], environment["COMSPEC"])
        self.assertFalse(options["shell"])
        self.assertEqual(options["cwd"], str(self.root))

    def test_windows_resolution_failure_keeps_adapter_127(self) -> None:
        from rm_judge_adapter import run

        environment = {
            "PATH": r"C:\tools",
            "PATHEXT": ".EXE;.CMD;.BAT",
            "COMSPEC": r"C:\system\cmd.exe",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which", return_value=None),
            patch("rm_judge_adapter.scratch_root", return_value=self.root),
            patch("rm_judge_adapter.subprocess.Popen") as popen,
        ):
            result = run(
                {"command": ["missing"], "prompt": "stdin",
                 "answer": "stdout"},
                "request",
                4096,
                self.root,
            )

        self.assertEqual(result, (127, b""))
        popen.assert_not_called()

    def test_posix_executable_argv_is_unchanged(self) -> None:
        from rm_judge_process import spawn_args

        command = ["pi", "-p", 'text\nwith "%PATH%" & !characters!']
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="posix")),
            patch("rm_judge_process.shutil.which") as which,
        ):
            options = spawn_args(command, {"PATH": "/configured/bin"})

        self.assertEqual(options, {"args": command})
        self.assertIs(options["args"], command)
        which.assert_not_called()

    def test_explicit_command_uses_shared_spawn_resolution(self) -> None:
        judge = ConventionJudge(self.state, self.gate)
        launch = {"argv": ["missing"], "explicit": True, "extra_env": []}
        with (
            patch.dict(os.environ, {"AMS_SCRATCH_ROOT": str(self.root / "scratch")}),
            patch("rm_judge.spawn_args",
                  side_effect=FileNotFoundError("missing")) as resolve,
            patch("rm_judge.subprocess.Popen") as popen,
        ):
            with self.assertRaises(JudgeError) as raised:
                judge._execute({}, launch, "request")

        self.assertEqual(str(raised.exception), "command_failed")
        self.assertEqual(resolve.call_args.args[0], ["missing"])
        popen.assert_not_called()
        record = json.loads(
            (self.root / "scratch" / "ams-judge" / "judge-last-run.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["error"], "command_failed")
        self.assertEqual(record["stage"], "spawn")
        self.assertEqual(record["exception"]["type"], "FileNotFoundError")
        self.assertIsNone(record["stderr_tail"])

    def test_windows_batch_refuses_expansion_and_control_syntax(self) -> None:
        from rm_judge_process import spawn_args

        environment = {
            "PATH": r"C:\tools",
            "PATHEXT": ".EXE;.CMD;.BAT",
            "COMSPEC": r"C:\system\cmd.exe",
        }
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which",
                  return_value=r"C:\tools\pi.cmd"),
        ):
            for argument in (
                "%PATH%", "!value!", '"quoted"', "a&b", "a|b",
                "a<b", "a>b", "a^b", "(value)", "a\nb", "a\rb", "\0",
            ):
                with self.subTest(argument=argument):
                    with self.assertRaises(OSError):
                        spawn_args(["pi", argument], environment)

    def test_windows_batch_falls_back_to_the_system_processor(self) -> None:
        # A process with no console session inherits no COMSPEC; the API answer
        # is used instead of resolving a processor through PATH.
        from rm_judge_process import spawn_args

        derived = r"C:\Windows\System32\cmd.exe"
        environment = {"PATH": r"C:\tools", "PATHEXT": ".EXE;.CMD"}
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which",
                  return_value=r"C:\tools\pi.cmd"),
            patch("rm_judge_process.system_command_processor",
                  return_value=derived) as derived_api,
        ):
            options = spawn_args(["pi", "-p"], environment)

        derived_api.assert_called_once_with()
        self.assertEqual(options["executable"], derived)
        self.assertIn(derived, options["args"])
        self.assertIn("/d /v:off /s /c", options["args"])

    def test_windows_batch_without_any_processor_is_refused(self) -> None:
        from rm_judge_process import spawn_args

        environment = {"PATH": r"C:\tools", "PATHEXT": ".EXE;.CMD"}
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which",
                  return_value=r"C:\tools\pi.cmd"),
            patch("rm_judge_process.system_command_processor", return_value=None),
        ):
            with self.assertRaises(FileNotFoundError):
                spawn_args(["pi"], environment)

    def test_windows_stated_comspec_is_never_replaced(self) -> None:
        # An empty or relative value is a broken environment, not a request for
        # the system processor: replacing it would hide the misconfiguration.
        from rm_judge_process import spawn_args

        for stated in ("", "cmd.exe", r"system32\cmd.exe"):
            with self.subTest(stated=stated):
                environment = {
                    "PATH": r"C:\tools", "PATHEXT": ".EXE;.CMD", "COMSPEC": stated,
                }
                with (
                    patch("rm_judge_process.os", SimpleNamespace(name="nt")),
                    patch("rm_judge_process.shutil.which",
                          return_value=r"C:\tools\pi.cmd"),
                    patch("rm_judge_process.system_command_processor") as derived_api,
                ):
                    with self.assertRaises(FileNotFoundError):
                        spawn_args(["pi"], environment)
                derived_api.assert_not_called()

    def test_windows_explicit_native_path_needs_no_lookup_environment(self) -> None:
        from rm_judge_process import spawn_args

        command = [r"C:\tools\python.exe", "%value%", "(argument)"]
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which") as which,
        ):
            options = spawn_args(command, {})

        self.assertEqual(options, {"args": command})
        which.assert_not_called()

    def test_windows_qualified_extensionless_command_uses_pathext(self) -> None:
        from rm_judge_process import spawn_args

        environment = {
            "PATHEXT": ".EXE;.CMD;.BAT",
            "COMSPEC": r"C:\system\cmd.exe",
        }
        with (
            patch("rm_judge_process.os", SimpleNamespace(name="nt")),
            patch("rm_judge_process.shutil.which",
                  return_value=r"C:\tools\pi.cmd") as which,
        ):
            options = spawn_args([r"C:\tools\pi", "-p"], environment)

        which.assert_called_once_with(r"C:\tools\pi", path="")
        self.assertEqual(options["executable"], environment["COMSPEC"])
        self.assertEqual(
            options["args"],
            r'C:\system\cmd.exe /d /v:off /s /c "C:\tools\pi.cmd -p"',
        )

    def test_shipped_pi_prompt_uses_stdin(self) -> None:
        self.assertEqual(JudgePolicy.load().host("pi")["prompt"], "stdin")


class JudgeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="judge-settings-", dir=SCRATCH_ROOT))
        self.home = self.root / "agent"
        self.home.mkdir()
        self.previous_home = {name: os.environ.get(name)
                              for name in HOME_VARIABLES}
        set_home(self.root / "user")
        self.state = type("State", (), {"home": self.home, "agent": "claude"})()
        self.settings = JudgeSettings(self.state)
        self.path = self.settings.path()

    def tearDown(self) -> None:
        restore_home(self.previous_home)

    def test_absent_document_is_the_shipped_default(self) -> None:
        config = self.settings.read()
        self.assertEqual(config["source"], "default")
        self.assertTrue(config["enabled"])
        self.assertIsNone(config["command"])
        self.assertEqual(config["protocol"], 2)
        self.assertEqual(config["stated"], [])
        self.assertEqual(config["max_turns"], JudgePolicy.load().defaults()["max_turns"])

    def test_shipped_policy_names_the_defaults(self) -> None:
        defaults = JudgePolicy.load().defaults()
        self.assertEqual(sorted(defaults), ["baseline_entries", "max_input_bytes",
                                            "max_output_bytes", "max_turns", "protocol",
                                            "timeout_s"])
        self.assertGreater(defaults["baseline_entries"], 0)
        # The room a review leaves for its receipt write is the policy's, and the
        # deadline ceiling is derived from it rather than restated.
        self.assertGreater(JudgePolicy.load().teardown_margin(), 0)
        self.assertLess(JudgePolicy.load().deadline_ceiling(), EVENT_TIMEOUTS["Stop"])
        # The lock's wait and retry interval are policy values as well.
        self.assertGreater(JudgePolicy.load().lock_timeout(), 0)
        self.assertLessEqual(JudgePolicy.load().lock_poll(),
                             JudgePolicy.load().lock_timeout())

    def test_every_path_reads_the_deadline_ceiling_from_one_policy(self) -> None:
        # The whole-document and single-edit paths must agree on the deadline,
        # so neither may reload the shipped policy while the other uses this
        # owner's.
        document = dict(JudgePolicy.load().document)
        document["review_teardown_s"] = EVENT_TIMEOUTS["Stop"] - 10
        policy = JudgePolicy(document)
        config = JudgeSettings(self.state, policy)
        with self.assertRaises(JudgeError):
            JudgeSettings.validate({"schema": 2, "timeout_s": 20},
                                   ceiling=policy.deadline_ceiling())
        with self.assertRaises(JudgeError):
            config.apply("set", "", "timeout_s", "20")

    def test_a_margin_that_leaves_no_review_room_is_invalid(self) -> None:
        document = dict(JudgePolicy.load().document)
        document["review_teardown_s"] = EVENT_TIMEOUTS["Stop"]
        with self.assertRaises(JudgeError):
            JudgePolicy(document).deadline_ceiling()

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

    def test_stated_keys_are_reported_even_at_default_values(self) -> None:
        self.settings.apply("set", "", "max_turns", "32")
        config = self.settings.read()
        self.assertIn("max_turns", config["stated"])
        self.assertEqual(config["max_turns"], JudgePolicy.load().defaults()["max_turns"])

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

    def test_a_deadline_beyond_the_installed_bound_is_refused(self) -> None:
        with self.assertRaises(JudgeError):
            self.settings.apply("set", "", "timeout_s",
                                str(JudgePolicy.load().deadline_ceiling() + 1))
        self.settings.apply("set", "", "timeout_s", "12.5")
        self.assertEqual(self.settings.read()["timeout_s"], 12.5)

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

    def test_import_into_a_sparse_document_refuses_instead_of_crashing(self) -> None:
        self.settings.apply("set", "", "max_turns", "8")
        legacy = self.home / ".agent-mem-struct" / "judge.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"command": ["x"], "timeout_s": 9, "max_turns": 2,
                                     "max_input_bytes": 100, "max_output_bytes": 10}),
                          encoding="utf-8")
        with self.assertRaises(JudgeError) as refused:
            self.settings.apply("import", self.settings.read()["revision"], None, None)
        self.assertEqual(str(refused.exception), "settings_conflict")

    def test_import_of_a_sparse_legacy_file_conflicts_instead_of_crashing(self) -> None:
        self.settings.apply("set", "", "max_turns", "8")
        legacy = self.home / ".agent-mem-struct" / "judge.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"schema": 2}), encoding="utf-8")
        with self.assertRaises(JudgeError) as refused:
            self.settings.apply("import", self.settings.read()["revision"], None, None)
        self.assertEqual(str(refused.exception), "settings_conflict")

    def test_reset_removes_numeric_overrides_only(self) -> None:
        self.settings.apply("set", "", "timeout_s", "12")
        self.settings.apply("set", self.settings.read()["revision"], "enabled", "off")
        self.settings.apply("reset", self.settings.read()["revision"], None, None)
        config = self.settings.read()
        self.assertFalse(config["enabled"])
        self.assertEqual(config["timeout_s"], JudgePolicy.load().defaults()["timeout_s"])

    @unittest.skipIf(
        os.name == "nt",
        "Windows chmod does not enforce directory write permissions",
    )
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

    def test_environment_override_replaces_the_shipped_names(self) -> None:
        self.assertEqual(self.settings.launch(self.settings.read())["extra_env"],
                         JudgePolicy.load().host("claude")["extra_env"])
        self.settings.apply("set", "", "hosts.claude.extra_env", "[]")
        self.assertEqual(self.settings.launch(self.settings.read())["extra_env"], [])

    def test_host_command_override_is_carried_to_the_adapter(self) -> None:
        self.settings.apply("set", "", "hosts.claude.command", '["my-cli", "-p"]')
        launch = self.settings.launch(self.settings.read())
        self.assertEqual(launch["host_command"], ["my-cli", "-p"])
        self.assertFalse(launch["explicit"])

    def pi_settings(self) -> JudgeSettings:
        return JudgeSettings(type("State", (), {"home": self.home, "agent": "pi"})())

    def extension(self, name: str) -> Path:
        path = self.root / name
        path.write_text("export {};\n", encoding="utf-8")
        return path

    def test_extension_paths_are_composed_onto_the_shipped_command(self) -> None:
        settings = self.pi_settings()
        path = self.extension("provider.ts")
        settings.apply("set", "", "hosts.pi.extensions", json.dumps([str(path)]))
        launch = settings.launch(settings.read())
        shipped = JudgePolicy.load().host("pi")["command"]
        self.assertEqual(launch["host_command"], [*shipped, "-e", str(path)])
        self.assertEqual(launch["extensions"], [str(path)])

    def test_extension_paths_follow_a_host_command_override(self) -> None:
        settings = self.pi_settings()
        path = self.extension("provider.ts")
        settings.apply("set", "", "hosts.pi.command", '["my-pi", "-p"]')
        settings.apply("set", settings.read()["revision"], "hosts.pi.extensions",
                       json.dumps([str(path)]))
        self.assertEqual(settings.launch(settings.read())["host_command"],
                         ["my-pi", "-p", "-e", str(path)])

    def test_extension_paths_must_be_absolute_and_distinct(self) -> None:
        settings = self.pi_settings()
        path = str((self.root / "provider.ts").resolve())
        for value in (
            json.dumps(["relative/index.ts"]),
            json.dumps([path, path]),
            json.dumps([""]),
            json.dumps(path),
            json.dumps([path + "\0"]),
        ):
            with self.assertRaises(JudgeError) as refused:
                settings.apply("set", "", "hosts.pi.extensions", value)
            self.assertEqual(str(refused.exception), "invalid_settings_request", value)
        self.assertEqual(settings.read()["hosts"], {})

    def test_a_host_without_an_extension_flag_cannot_load_one(self) -> None:
        # Claude's shipped descriptor states no flag, so a stored list never
        # becomes a launch rather than being silently dropped.
        path = self.extension("provider.ts").resolve()
        self.settings.apply("set", "", "hosts.claude.extensions",
                            json.dumps([str(path)]))
        with self.assertRaises(JudgeError) as refused:
            self.settings.launch(self.settings.read())
        self.assertEqual(str(refused.exception), "invalid_config")

    def test_a_stale_extension_path_refuses_the_launch_that_would_load_it(self) -> None:
        settings = self.pi_settings()
        gone = self.root / "gone.ts"
        settings.apply("set", "", "hosts.pi.extensions", json.dumps([str(gone)]))
        with self.assertRaises(JudgeError) as refused:
            settings.launch(settings.read())
        self.assertEqual(str(refused.exception), "invalid_config")


class FakeProcess:
    """A command that ends at once, writing the stderr it was handed."""

    def __init__(self, code: int, stdout: bytes = b"") -> None:
        self.code = code
        self.stdout = io.BytesIO(stdout)
        self.stdin = io.BytesIO()
        self.pid = 4242

    def wait(self, timeout: float | None = None) -> int:
        return self.code

    def kill(self) -> None:
        pass


class StalledProcess(FakeProcess):
    """A command whose output never arrives, so only the deadline ends it."""

    def __init__(self) -> None:
        super().__init__(0)
        self.stdout = self

    def read(self, size: int) -> bytes:
        time.sleep(2.0)
        return b""


class JudgeDiagnosticTests(JudgeCase):
    """One bounded record per run, so a silent failure is readable afterwards."""

    def setUp(self) -> None:
        super().setUp()
        self.scratch = self.root / "scratch"
        self.record_path = self.scratch / "ams-judge" / "judge-last-run.json"
        self.launch = {"argv": [sys.executable, "-c", "pass"],
                       "explicit": True, "extra_env": []}
        scoped = patch.dict(os.environ, {"AMS_SCRATCH_ROOT": str(self.scratch)})
        scoped.start()
        self.addCleanup(scoped.stop)

    def record(self) -> dict:
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def run_command(self, process, stderr: bytes = b"", timeout_s: float = 30.0) -> str:
        judge = ConventionJudge(self.state, self.gate)

        def spawn(**options):
            handle = options.get("stderr")
            if stderr and hasattr(handle, "write"):
                handle.write(stderr)
                handle.flush()
            return process

        with patch("rm_judge.subprocess.Popen", side_effect=spawn):
            with self.assertRaises(JudgeError) as raised:
                judge._execute(
                    {"max_output_bytes": 4096, "timeout_s": timeout_s},
                    self.launch, "payload")
        return str(raised.exception)

    def test_a_failing_child_keeps_its_own_last_words(self) -> None:
        error = self.run_command(FakeProcess(5), stderr=b"boom\n")

        self.assertEqual(error, "command_failed")
        record = self.record()
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["error"], "command_failed")
        self.assertEqual(record["stage"], "child_exit")
        self.assertEqual(record["returncode"], 5)
        self.assertEqual(record["stderr_tail"], "boom\n")
        self.assertFalse(record["stderr_truncated"])
        # The capture is a diagnostic, never litter: nothing is left beside it.
        self.assertEqual(
            [path.name for path in self.record_path.parent.iterdir()],
            [self.record_path.name])

    def test_a_clean_run_overwrites_the_record_and_keeps_no_output(self) -> None:
        judge = ConventionJudge(self.state, self.gate)
        with patch("rm_judge.subprocess.Popen",
                   side_effect=lambda **options: FakeProcess(0, stdout=b"answer\n")):
            answer = judge._execute(
                {"max_output_bytes": 4096, "timeout_s": 30}, self.launch, "payload")

        self.assertEqual(answer, "answer\n")
        record = self.record()
        self.assertEqual(record["outcome"], "success")
        self.assertIsNone(record["error"])
        self.assertEqual(record["returncode"], 0)
        self.assertIsNone(record["stderr_tail"])

    def test_a_flooding_child_leaves_a_bounded_tail_and_record(self) -> None:
        self.run_command(FakeProcess(2), stderr=b"x" * 200_000)

        record = self.record()
        self.assertEqual(record["stage"], "child_exit")
        self.assertEqual(record["returncode"], 2)
        self.assertEqual(len(record["stderr_tail"]), 8192)
        self.assertTrue(record["stderr_truncated"])
        self.assertLessEqual(self.record_path.stat().st_size, 32768)

    def test_a_silent_command_is_a_deadline_not_a_missing_result(self) -> None:
        error = self.run_command(StalledProcess(), timeout_s=0.05)

        self.assertEqual(error, "timeout")
        record = self.record()
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["error"], "timeout")
        self.assertEqual(record["stage"], "deadline")


if __name__ == "__main__":
    unittest.main()
