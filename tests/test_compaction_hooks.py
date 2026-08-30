#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"
CODEX_MANAGER = REPO / "hooks" / "codex" / "manage.py"
CLAUDE_MANAGER = REPO / "hooks" / "claude" / "manage.py"
SCRATCH_ROOT = Path(
    os.environ.get(
        "AGENT_MEM_STRUCT_TEST_TMP",
        "/home/ederevx/Documents/Codex/2026-08-29/check-new-ci-claude-and-see",
    )
)


def make_home(path: Path) -> None:
    (path / "memory").mkdir(parents=True)
    (path / "memory" / "MEMORY.md").write_text(
        "Structure-Version: test-v1\nStructure: ../STRUCTURE.md\n\n# Root\n",
        encoding="utf-8",
    )
    (path / "RULES.md").write_text("# Rules\n\nKeep continuity.\n", encoding="utf-8")
    (path / "STRUCTURE.md").write_text("Structure-Version: test-v1\n", encoding="utf-8")


def make_transcript(path: Path) -> None:
    rows = [
        {"type": "last-prompt", "lastPrompt": "Fix the stuck CI session and prevent recurrence."},
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Diagnosed stale lifecycle state."}],
            }
        },
        {
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Next action: install and smoke-test hooks."}],
            }
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def invoke(agent: str, home: Path, event: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK), "--agent", agent, "--home", str(home)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
    )


class CompactionHookTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="agent-mem-struct-test-", dir=SCRATCH_ROOT))
        self.home = self.temp / "home"
        make_home(self.home)
        self.transcript = self.temp / "transcript.jsonl"
        make_transcript(self.transcript)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def event(self, name: str, trigger: str = "auto") -> dict[str, object]:
        return {
            "hook_event_name": name,
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.temp),
            "model": "test-model",
            "trigger": trigger,
            "transcript_path": str(self.transcript),
        }

    def test_codex_manual_and_auto_compaction_use_top_level_schema(self) -> None:
        for trigger in ("manual", "auto"):
            with self.subTest(trigger=trigger):
                result = invoke("codex", self.home, self.event("PreCompact", trigger))
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(set(output), {"systemMessage"})
                self.assertIn("Fix the stuck CI session", output["systemMessage"])

                result = invoke("codex", self.home, self.event("PostCompact", trigger))
                output = json.loads(result.stdout)
                self.assertEqual(set(output), {"systemMessage"})
                self.assertIn("Next action: install", output["systemMessage"])

    def test_claude_precompact_then_compact_session_start_reinjects(self) -> None:
        result = invoke("claude", self.home, self.event("PreCompact"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        event = self.event("SessionStart")
        event["source"] = "compact"
        result = invoke("claude", self.home, event)
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("PRE-COMPACTION CONTINUITY CHECKPOINT", context)
        self.assertIn("Fix the stuck CI session", context)

    def test_precompact_blocks_when_checkpoint_cannot_be_built(self) -> None:
        event = self.event("PreCompact")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        claude = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(claude["decision"], "block")
        codex = json.loads(invoke("codex", self.home, event).stdout)
        self.assertIs(codex["continue"], False)
        self.assertIn("checkpoint", codex["stopReason"])

    def test_existing_context_event_still_uses_hook_specific_output(self) -> None:
        result = invoke("codex", self.home, self.event("UserPromptSubmit"))
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("ROOT MEMORY CONTROL", output["hookSpecificOutput"]["additionalContext"])

    def test_compact_session_start_warns_when_precompact_did_not_run(self) -> None:
        event = self.event("SessionStart")
        event["session_id"] = "never-checkpointed"
        event["source"] = "compact"
        result = invoke("claude", self.home, event)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CONTINUITY WARNING", context)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="agent-mem-struct-install-test-", dir=SCRATCH_ROOT))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def check_manager(self, manager: Path, config_name: str, extra: list[str]) -> None:
        home = self.temp / manager.parent.name
        home.mkdir()
        config = home / config_name
        config.write_text(
            json.dumps({"unrelated": True, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep"}]}]}}),
            encoding="utf-8",
        )
        command = [sys.executable, str(manager), "install", "--home", str(home), *extra]
        subprocess.run(command, check=True, capture_output=True, text=True)
        subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertTrue(data["unrelated"])
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "keep")
        for event in ("SessionStart", "UserPromptSubmit", "SubagentStart", "PreCompact", "PostCompact", "PreToolUse"):
            owned = [
                hook
                for group in data["hooks"][event]
                for hook in group.get("hooks", [])
                if str(hook.get("statusMessage", "")).startswith("agent-mem-struct root memory:")
            ]
            self.assertEqual(len(owned), 1, event)

        subprocess.run([sys.executable, str(manager), "uninstall", "--home", str(home)], check=True, capture_output=True, text=True)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(set(data["hooks"]), {"Stop"})

    def test_codex_installer_is_additive_and_idempotent(self) -> None:
        self.check_manager(CODEX_MANAGER, "hooks.json", [])

    def test_claude_installer_is_additive_and_idempotent(self) -> None:
        memory_home = self.temp / "claude-memory"
        make_home(memory_home)
        self.check_manager(CLAUDE_MANAGER, "settings.json", ["--memory-home", str(memory_home)])


if __name__ == "__main__":
    unittest.main()
