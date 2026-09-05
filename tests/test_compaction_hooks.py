#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"
CODEX_MANAGER = REPO / "hooks" / "codex" / "manage.py"
CLAUDE_MANAGER = REPO / "hooks" / "claude" / "manage.py"
SCRATCH_ROOT = Path(
    os.environ.get(
        "AGENT_MEM_STRUCT_TEST_TMP",
        str(Path.home() / "tmp" / "agent-mem-struct-tests"),
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


def invoke(
    agent: str,
    home: Path,
    event: dict[str, object],
    *,
    config_home: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(HOOK), "--agent", agent, "--home", str(home)]
    if config_home is not None:
        command.extend(("--config-home", str(config_home)))
    return subprocess.run(
        command,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
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
            # Codex labels the compaction trigger `trigger`, Claude `triggered_by`.
            "trigger": trigger,
            "triggered_by": trigger,
            "transcript_path": str(self.transcript),
        }

    def test_codex_compaction_restores_at_session_start(self) -> None:
        result = invoke("codex", self.home, self.event("PreCompact"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        checkpoint = (
            self.home
            / ".agent-mem-struct"
            / "compaction-checkpoints"
            / "session-1.json"
        )
        self.assertTrue(checkpoint.exists())

        event = self.event("SessionStart")
        event["source"] = "compact"
        context = json.loads(invoke("codex", self.home, event).stdout)[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("PRE-COMPACTION CONTINUITY CHECKPOINT", context)
        self.assertIn("Next action: install", context)
        self.assertNotIn("CONTINUITY WARNING", context)
        self.assertFalse(checkpoint.exists())

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

    def test_manual_precompact_blocks_when_checkpoint_cannot_be_built(self) -> None:
        # PreCompact honors no JSON decision field; only a non-zero exit blocks it.
        event = self.event("PreCompact", "manual")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        claude = invoke("claude", self.home, event)
        self.assertEqual(claude.returncode, 2)
        self.assertEqual(claude.stdout, "")
        self.assertIn("Compaction blocked", claude.stderr)
        codex = invoke("codex", self.home, event)
        self.assertEqual(codex.returncode, 2)
        self.assertIn("Compaction blocked", codex.stderr)
        payload = json.loads(codex.stdout)
        self.assertIs(payload["continue"], False)
        self.assertIn("checkpoint", payload["stopReason"])

    def test_manual_precompact_blocks_when_root_control_is_invalid(self) -> None:
        (self.home / "RULES.md").unlink()
        result = invoke("claude", self.home, self.event("PreCompact", "manual"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("root memory control is invalid", result.stderr)

    def test_automatic_precompact_warns_instead_of_stranding_the_session(self) -> None:
        # Blocking an automatic compaction pins the session at its context
        # ceiling, which loses more continuity than the missing checkpoint.
        event = self.event("PreCompact", "auto")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        for agent in ("claude", "codex"):
            result = invoke(agent, self.home, event)
            self.assertEqual(result.returncode, 0, result.stderr)
            message = json.loads(result.stdout)["systemMessage"]
            self.assertIn("without a continuity checkpoint", message)
            self.assertNotIn("blocked", message)

    def test_precompact_trigger_is_read_from_either_host_key(self) -> None:
        for key in ("trigger", "triggered_by"):
            event = self.event("PreCompact", "auto")
            event.pop("trigger")
            event.pop("triggered_by")
            event[key] = "auto"
            event["transcript_path"] = str(self.temp / "missing.jsonl")
            result = invoke("claude", self.home, event)
            self.assertEqual(result.returncode, 0, key)
            self.assertIn("systemMessage", json.loads(result.stdout))

    def test_unlabelled_precompact_trigger_is_treated_as_manual(self) -> None:
        event = self.event("PreCompact")
        event.pop("trigger")
        event.pop("triggered_by")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        self.assertEqual(invoke("claude", self.home, event).returncode, 2)

    def test_precompact_without_session_id_is_blocked_without_unknown_file(self) -> None:
        event = self.event("PreCompact", "manual")
        event.pop("session_id")
        result = invoke("codex", self.home, event)
        self.assertEqual(result.returncode, 2)
        self.assertIs(json.loads(result.stdout)["continue"], False)
        self.assertFalse(
            (self.home / ".agent-mem-struct" / "compaction-checkpoints" / "unknown.json").exists()
        )

    def test_codex_prompt_refresh_does_not_repeat_root_bodies(self) -> None:
        started = json.loads(
            invoke("codex", self.home, self.event("SessionStart")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        refreshed = json.loads(
            invoke("codex", self.home, self.event("UserPromptSubmit")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", started)
        self.assertIn("Keep continuity.", started)
        self.assertIn("ROOT MEMORY TURN CHECK", refreshed)
        self.assertNotIn("--- BEGIN ROOT memory/MEMORY.md ---", refreshed)
        self.assertNotIn("Keep continuity.", refreshed)
        self.assertIn("Codex native AGENTS.md instruction discovery remains active", refreshed)
        self.assertIn("generated memories are disabled", refreshed)
        subagent = json.loads(
            invoke("codex", self.home, self.event("SubagentStart")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("--- BEGIN ROOT memory/MEMORY.md ---", subagent)
        self.assertNotIn("Keep continuity.", subagent)
        self.assertIn("does not apply to subagents", subagent)

    def test_claude_prompt_refresh_does_not_repeat_root_bodies(self) -> None:
        started = json.loads(
            invoke("claude", self.home, self.event("SessionStart")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        refreshed = json.loads(
            invoke("claude", self.home, self.event("UserPromptSubmit")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", started)
        self.assertIn("Keep continuity.", started)
        self.assertIn("ROOT MEMORY TURN CHECK", refreshed)
        self.assertNotIn("--- BEGIN ROOT memory/MEMORY.md ---", refreshed)
        self.assertNotIn("Keep continuity.", refreshed)
        self.assertIn("read the shared scope", refreshed)

    def test_subagent_start_gets_short_deferral_not_full_block(self) -> None:
        # A subagent must never be told it owns memory reads/writes: the full
        # authoritative block reads as an out-of-scope directive inside a
        # bounded subagent task, and has been mistaken for prompt injection.
        for agent in ("claude", "codex"):
            subagent = json.loads(
                invoke(agent, self.home, self.event("SubagentStart")).stdout
            )["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("--- BEGIN ROOT memory/MEMORY.md ---", subagent)
            self.assertNotIn("--- BEGIN ROOT RULES.md ---", subagent)
            self.assertNotIn("Keep continuity.", subagent)
            self.assertIn("does not apply to subagents", subagent)
            self.assertIn("the session that spawned you owns all memory reads", subagent)
            self.assertIn("task prompt", subagent)

    def test_main_session_block_is_unchanged_by_the_subagent_scoping(self) -> None:
        # SessionStart/UserPromptSubmit must keep receiving byte-for-byte the
        # same full-block behavior as before subagents were scoped out.
        for agent in ("claude", "codex"):
            started = json.loads(
                invoke(agent, self.home, self.event("SessionStart")).stdout
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("ROOT MEMORY CONTROL", started)
            self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", started)
            self.assertIn("--- BEGIN ROOT RULES.md ---", started)
            self.assertIn("Keep continuity.", started)
            self.assertIn("Mandatory use: treat the injected files as current authority", started)

    def test_subagent_short_form_falls_back_to_full_block_on_error(self) -> None:
        # Fail-safe: if short-form generation ever breaks, the hook must fail
        # toward the full block, never crash and never emit nothing. Exercised
        # in-process (rather than via subprocess) so the failure can be forced.
        import contextlib
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location("root_memory_context_hook", HOOK)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        state = module.root_state(self.home)
        event = self.event("SubagentStart")

        original = module.subagent_context_text
        module.subagent_context_text = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                module.emit_context("claude", "SubagentStart", state, event)
        finally:
            module.subagent_context_text = original

        context = json.loads(buffer.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", context)
        self.assertIn("Keep continuity.", context)

    def test_claude_hook_ignores_an_inactive_config_profile(self) -> None:
        active = self.temp / "active-config"
        inactive = self.temp / "inactive-config"
        environment = dict(os.environ)
        environment["CLAUDE_CONFIG_DIR"] = str(active)
        result = invoke(
            "claude",
            self.home,
            self.event("SessionStart"),
            config_home=inactive,
            environment=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        result = invoke(
            "claude",
            self.home,
            self.event("SessionStart"),
            config_home=active,
            environment=environment,
        )
        self.assertIn("ROOT MEMORY CONTROL", result.stdout)

    def test_codex_hook_ignores_an_inactive_config_profile(self) -> None:
        active = self.temp / "active-codex"
        inactive = self.temp / "inactive-codex"
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(active)
        result = invoke(
            "codex",
            self.home,
            self.event("SessionStart"),
            config_home=inactive,
            environment=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        result = invoke(
            "codex",
            self.home,
            self.event("SessionStart"),
            config_home=active,
            environment=environment,
        )
        self.assertIn("ROOT MEMORY CONTROL", result.stdout)

    def test_pretool_fast_path_preserves_the_invalid_root_guard(self) -> None:
        invalid_home = self.temp / "invalid-home"
        harmless = self.event("PreToolUse")
        harmless.update({"tool_name": "Bash", "tool_input": {"command": "pwd"}})
        self.assertEqual(invoke("codex", invalid_home, harmless).stdout, "")

        mutation = self.event("PreToolUse")
        mutation.update({
            "tool_name": "apply_patch",
            "tool_input": {
                "command": f"*** Update File: {invalid_home}/memory/local/note.md"
            },
        })
        output = json.loads(invoke("codex", invalid_home, mutation).stdout)
        self.assertEqual(
            output["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_embedded_script_writes_are_guarded_across_heredoc_lines(self) -> None:
        invalid_home = self.temp / "invalid-home"
        target = invalid_home / "memory" / "local" / "note.md"
        for command in (
            f"python3 - <<'PY'\nopen('{target}', 'w').write('x')\nPY",
            f"python3 -c \"import shutil; shutil.rmtree('{target}')\"",
            f"node -e \"require('fs').writeFileSync('{target}', 'x')\"",
        ):
            event = self.event("PreToolUse")
            event.update({"tool_name": "Bash", "tool_input": {"command": command}})
            output = json.loads(invoke("codex", invalid_home, event).stdout)
            self.assertEqual(
                output["hookSpecificOutput"]["permissionDecision"], "deny", command
            )

    def test_embedded_script_reads_are_not_guarded(self) -> None:
        invalid_home = self.temp / "invalid-home"
        target = invalid_home / "memory" / "local" / "note.md"
        for command in (
            f"python3 -c \"print(open('{target}').read())\"",
            f"python3 - <<'PY'\nprint(open('{target}').read())\nPY",
        ):
            event = self.event("PreToolUse")
            event.update({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(invoke("codex", invalid_home, event).stdout, "", command)

    def test_checkpoint_state_tree_is_private_at_every_level(self) -> None:
        owned = self.home / ".agent-mem-struct"
        owned.mkdir()
        owned.chmod(0o755)
        invoke("codex", self.home, self.event("PreCompact"))
        if os.name != "nt":
            self.assertEqual(owned.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                (owned / "compaction-checkpoints").stat().st_mode & 0o777,
                0o700,
            )

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
        invoke("codex", self.home, self.event("SessionStart"))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse(temporary.exists())

    def test_checkpoint_directory_permissions_are_repaired(self) -> None:
        directory = self.home / ".agent-mem-struct" / "compaction-checkpoints"
        directory.mkdir(parents=True)
        directory.chmod(0o755)
        invoke("codex", self.home, self.event("PreCompact"))
        if os.name != "nt":
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
            json.dumps({
                "unrelated": True,
                "env": {"KEEP": "yes"},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "keep"}]}],
                    "PostCompact": [{
                        "hooks": [{
                            "type": "command",
                            "command": "legacy-owned",
                            "statusMessage": "agent-mem-struct root memory: legacy",
                        }]
                    }],
                },
            }),
            encoding="utf-8",
        )
        command = [sys.executable, str(manager), "install", "--home", str(home), *extra]
        if manager == CODEX_MANAGER:
            (home / "config.toml").write_text("model = \"keep-model\"\n", encoding="utf-8")
            (home / "AGENTS.md").write_text("Keep native instructions.\n", encoding="utf-8")
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=self.temp,
        )
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=self.temp,
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertTrue(data["unrelated"])
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "keep")
        if manager == CLAUDE_MANAGER:
            self.assertEqual(data["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "SubagentStart",
            "PreCompact",
            "PreToolUse",
        ):
            owned = [
                hook
                for group in data["hooks"][event]
                for hook in group.get("hooks", [])
                if str(hook.get("statusMessage", "")).startswith("agent-mem-struct root memory:")
            ]
            self.assertEqual(len(owned), 1, event)
            self.assertEqual(owned[0]["timeout"], 60 if event == "PreCompact" else 5, event)
            if manager in {CODEX_MANAGER, CLAUDE_MANAGER}:
                self.assertIn("--config-home", owned[0]["command"])
                self.assertIn(str(home), owned[0]["command"])
        self.assertNotIn("PostCompact", data["hooks"])
        if os.name != "nt":
            self.assertEqual(
                (home / ".agent-mem-struct").stat().st_mode & 0o777,
                0o700,
            )

        if manager == CODEX_MANAGER:
            codex_config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(codex_config["model"], "keep-model")
            self.assertIs(codex_config["features"]["memories"], False)
            self.assertEqual(
                (home / "AGENTS.md").read_text(encoding="utf-8"),
                "Keep native instructions.\n",
            )

        checkpoint_root = checkpoint_home or home
        checkpoint_dir = checkpoint_root / ".agent-mem-struct" / "compaction-checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "orphan.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, str(manager), "uninstall", "--home", str(home)],
            check=True,
            capture_output=True,
            text=True,
            cwd=self.temp,
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(set(data["hooks"]), {"Stop"})
        self.assertEqual(data["env"], {"KEEP": "yes"})
        self.assertFalse(checkpoint_dir.exists())
        self.assertFalse((home / ".agent-mem-struct").exists())
        self.assertFalse((checkpoint_root / ".agent-mem-struct").exists())
        if manager == CODEX_MANAGER:
            self.assertEqual(
                (home / "config.toml").read_text(encoding="utf-8"),
                "model = \"keep-model\"\n",
            )

    def test_codex_installer_is_additive_and_idempotent(self) -> None:
        self.check_manager(CODEX_MANAGER, "hooks.json", [])

    def test_codex_uninstall_restores_existing_native_memory_setting(self) -> None:
        for prior in ("true", "false"):
            with self.subTest(prior=prior):
                home = self.temp / f"codex-existing-memory-{prior}"
                home.mkdir()
                config = home / "config.toml"
                original = (
                    "model = \"keep\"\n\n[features]\n"
                    f"memories = {prior} # user choice\nhooks = true\n"
                )
                config.write_text(original, encoding="utf-8")
                command = [sys.executable, str(CODEX_MANAGER), "--home", str(home)]
                subprocess.run(
                    [command[0], command[1], "install", *command[2:]], check=True
                )
                installed = tomllib.loads(config.read_text(encoding="utf-8"))
                self.assertIs(installed["features"]["memories"], False)
                self.assertIs(installed["features"]["hooks"], True)
                subprocess.run(
                    [command[0], command[1], "uninstall", *command[2:]], check=True
                )
                self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_codex_uninstall_restores_missing_config_file(self) -> None:
        home = self.temp / "codex-missing-config"
        home.mkdir()
        config = home / "config.toml"
        command = [sys.executable, str(CODEX_MANAGER), "--home", str(home)]
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        self.assertIs(
            tomllib.loads(config.read_text(encoding="utf-8"))["features"]["memories"],
            False,
        )
        subprocess.run([command[0], command[1], "uninstall", *command[2:]], check=True)
        self.assertFalse(config.exists())
        self.assertFalse((home / ".agent-mem-struct").exists())

    def test_codex_restores_a_feature_section_without_final_newline(self) -> None:
        home = self.temp / "codex-no-final-newline"
        home.mkdir()
        config = home / "config.toml"
        original = "[features]\nhooks = true"
        config.write_text(original, encoding="utf-8")
        command = [sys.executable, str(CODEX_MANAGER), "--home", str(home)]
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        installed = tomllib.loads(config.read_text(encoding="utf-8"))
        self.assertIs(installed["features"]["memories"], False)
        subprocess.run([command[0], command[1], "uninstall", *command[2:]], check=True)
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_codex_uninstall_preserves_a_user_changed_managed_line(self) -> None:
        home = self.temp / "codex-user-changed-memory"
        home.mkdir()
        config = home / "config.toml"
        config.write_text("[features]\nmemories = true\n", encoding="utf-8")
        command = [sys.executable, str(CODEX_MANAGER), "--home", str(home)]
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        config.write_text("[features]\nmemories = true # changed later\n", encoding="utf-8")
        subprocess.run([command[0], command[1], "uninstall", *command[2:]], check=True)
        self.assertEqual(
            config.read_text(encoding="utf-8"),
            "[features]\nmemories = true # changed later\n",
        )

    def test_codex_uninstall_does_not_reparent_later_feature_settings(self) -> None:
        home = self.temp / "codex-extended-created-section"
        home.mkdir()
        config = home / "config.toml"
        command = [sys.executable, str(CODEX_MANAGER), "--home", str(home)]
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        installed = config.read_text(encoding="utf-8")
        config.write_text(installed + "hooks = true\n", encoding="utf-8")
        subprocess.run([command[0], command[1], "uninstall", *command[2:]], check=True)
        self.assertEqual(config.read_text(encoding="utf-8"), installed + "hooks = true\n")

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

    def test_claude_uninstall_restores_existing_auto_memory_setting(self) -> None:
        home = self.temp / "claude-existing-auto-memory"
        memory_home = self.temp / "claude-existing-memory"
        home.mkdir()
        make_home(memory_home)
        settings = home / "settings.json"
        settings.write_text(
            json.dumps({"env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"}}),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(CLAUDE_MANAGER),
            "--home",
            str(home),
            "--memory-home",
            str(memory_home),
        ]
        subprocess.run([command[0], command[1], "install", *command[2:]], check=True)
        installed = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(installed["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        subprocess.run([command[0], command[1], "uninstall", *command[2:]], check=True)
        restored = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(restored["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "0")


if __name__ == "__main__":
    unittest.main()
