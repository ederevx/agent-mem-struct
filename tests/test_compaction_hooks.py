#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
                self.assertFalse(
                    (self.home / ".agent-mem-struct" / "compaction-checkpoints" / "session-1.json").exists()
                )

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
        self.assertFalse(
            (self.home / ".agent-mem-struct" / "compaction-checkpoints" / "session-1.json").exists()
        )

    def test_precompact_blocks_when_checkpoint_cannot_be_built(self) -> None:
        event = self.event("PreCompact")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        claude = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(claude["decision"], "block")
        codex = json.loads(invoke("codex", self.home, event).stdout)
        self.assertIs(codex["continue"], False)
        self.assertIn("checkpoint", codex["stopReason"])

    def test_precompact_without_session_id_is_blocked_without_unknown_file(self) -> None:
        event = self.event("PreCompact")
        event.pop("session_id")
        output = json.loads(invoke("codex", self.home, event).stdout)
        self.assertIs(output["continue"], False)
        self.assertFalse(
            (self.home / ".agent-mem-struct" / "compaction-checkpoints" / "unknown.json").exists()
        )

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

    def test_old_crash_checkpoint_is_scavenged_but_fresh_one_is_kept(self) -> None:
        directory = self.home / ".agent-mem-struct" / "compaction-checkpoints"
        directory.mkdir(parents=True)
        old = directory / "old.json"
        fresh = directory / "fresh.json"
        temporary = directory / "abandoned.json.tmp.42"
        old.write_text("{}\n", encoding="utf-8")
        fresh.write_text("{}\n", encoding="utf-8")
        temporary.write_text("partial", encoding="utf-8")
        expired = time.time() - 8 * 24 * 60 * 60
        os.utime(old, (expired, expired))
        temp_expired = time.time() - 2 * 60 * 60
        os.utime(temporary, (temp_expired, temp_expired))
        invoke("codex", self.home, self.event("UserPromptSubmit"))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse(temporary.exists())

    def test_checkpoint_directory_permissions_are_repaired(self) -> None:
        directory = self.home / ".agent-mem-struct" / "compaction-checkpoints"
        directory.mkdir(parents=True)
        directory.chmod(0o755)
        invoke("codex", self.home, self.event("PreCompact"))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        checkpoint = directory / "session-1.json"
        self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)

    def test_consumed_checkpoint_is_not_reinjected_again(self) -> None:
        invoke("claude", self.home, self.event("PreCompact"))
        event = self.event("SessionStart")
        event["source"] = "compact"
        first = json.loads(invoke("claude", self.home, event).stdout)
        self.assertIn(
            "PRE-COMPACTION CONTINUITY CHECKPOINT",
            first["hookSpecificOutput"]["additionalContext"],
        )
        second = json.loads(invoke("claude", self.home, event).stdout)
        context = second["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Fix the stuck CI session", context)
        self.assertIn("CONTINUITY WARNING", context)

    def test_checkpoint_count_is_hard_bounded(self) -> None:
        directory = self.home / ".agent-mem-struct" / "compaction-checkpoints"
        directory.mkdir(parents=True)
        for index in range(260):
            path = directory / f"old-{index}.json"
            path.write_text("{}\n", encoding="utf-8")
            os.utime(path, (time.time() - index, time.time() - index))
        invoke("codex", self.home, self.event("PreCompact"))
        self.assertLessEqual(len(list(directory.glob("*.json"))), 256)
        self.assertTrue((directory / "session-1.json").exists())


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="agent-mem-struct-install-test-", dir=SCRATCH_ROOT))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def check_manager(
        self,
        manager: Path,
        config_name: str,
        extra: list[str],
        checkpoint_home: Path | None = None,
    ) -> None:
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

        checkpoint_root = checkpoint_home or home
        checkpoint_dir = checkpoint_root / ".agent-mem-struct" / "compaction-checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "orphan.json").write_text("{}\n", encoding="utf-8")
        subprocess.run([sys.executable, str(manager), "uninstall", "--home", str(home)], check=True, capture_output=True, text=True)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(set(data["hooks"]), {"Stop"})
        self.assertFalse(checkpoint_dir.exists())

    def test_codex_installer_is_additive_and_idempotent(self) -> None:
        self.check_manager(CODEX_MANAGER, "hooks.json", [])

    def test_claude_installer_is_additive_and_idempotent(self) -> None:
        memory_home = self.temp / "claude-memory"
        make_home(memory_home)
        self.check_manager(
            CLAUDE_MANAGER,
            "settings.json",
            ["--memory-home", str(memory_home)],
            checkpoint_home=memory_home,
        )

    def test_claude_relocation_removes_old_checkpoint_tree(self) -> None:
        home = self.temp / "claude-relocation"
        old_memory = self.temp / "old-memory"
        new_memory = self.temp / "new-memory"
        home.mkdir()
        make_home(old_memory)
        make_home(new_memory)
        command = [sys.executable, str(CLAUDE_MANAGER), "install", "--home", str(home)]
        subprocess.run(
            [*command, "--memory-home", str(old_memory)],
            check=True,
            capture_output=True,
            text=True,
        )
        old_checkpoints = old_memory / ".agent-mem-struct" / "compaction-checkpoints"
        old_checkpoints.mkdir(parents=True)
        (old_checkpoints / "orphan.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(
            [*command, "--memory-home", str(new_memory)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertFalse(old_checkpoints.exists())


if __name__ == "__main__":
    unittest.main()
