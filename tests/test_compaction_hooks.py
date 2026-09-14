#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
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
from unittest import mock


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


def make_shared(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "MEMORY.md").write_text(
        "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n",
        encoding="utf-8",
    )
    return path.resolve()


def make_home(path: Path, shared: Path | None = None) -> Path:
    shared = make_shared(shared or path.parent / f"{path.name} shared memory")
    (path / "memory").mkdir(parents=True)
    (path / "memory" / "MEMORY.md").write_text(
        f"Structure-Version: test-v1\nStructure: ../STRUCTURE.md\nShared: {shared}\n\n# Root\n",
        encoding="utf-8",
    )
    (path / "RULES.md").write_text("# Rules\n\nKeep continuity.\n", encoding="utf-8")
    (path / "STRUCTURE.md").write_text("Structure-Version: test-v1\n", encoding="utf-8")
    local = path / "memory" / "local"
    local.mkdir()
    (local / "MEMORY.md").write_text(
        "# Local\n\n**Scope:** this agent\n\n## Mandatory conventions\n\n(none)\n",
        encoding="utf-8",
    )
    return shared


def make_install_memory_home(path: Path) -> None:
    make_home(path)
    (path / "RULES.md").write_bytes((REPO / "RULES.md").read_bytes())
    (path / "STRUCTURE.md").write_bytes((REPO / "STRUCTURE.md").read_bytes())


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
    canonical_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(HOOK), "--agent", agent, "--home", str(home)]
    command.extend(("--canonical-root", str(canonical_root or home)))
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
        self.shared = make_home(self.home)
        self.transcript = self.temp / "transcript.jsonl"
        make_transcript(self.transcript)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def event(
        self, name: str, trigger: str = "auto", *, agent: str = "codex"
    ) -> dict[str, object]:
        event: dict[str, object] = {
            "hook_event_name": name,
            "session_id": "session-1",
            "cwd": str(self.temp),
            "model": "test-model",
            # Codex labels the compaction trigger `trigger`, Claude `triggered_by`.
            "trigger": trigger,
            "triggered_by": trigger,
            "transcript_path": str(self.transcript),
        }
        event["prompt_id" if agent == "claude" else "turn_id"] = (
            "prompt-1" if agent == "claude" else "turn-1"
        )
        return event

    def write_root_memory(
        self, *shared_lines: str, body: str = "# Root\n"
    ) -> None:
        header = ["Structure-Version: test-v1", "Structure: ../STRUCTURE.md"]
        header.extend(shared_lines)
        (self.home / "memory" / "MEMORY.md").write_text(
            "\n".join(header) + "\n\n" + body,
            encoding="utf-8",
        )

    def mutation(self, target: Path, *, agent: str = "codex") -> dict[str, object]:
        event = self.event("PreToolUse", agent=agent)
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        return event

    def test_declared_external_shared_directory_is_loaded_for_both_agents(self) -> None:
        self.assertFalse((self.home / "memory" / "shared").exists())
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                context = json.loads(
                    invoke(agent, self.home, self.event("SessionStart", agent=agent)).stdout
                )["hookSpecificOutput"]["additionalContext"]
                self.assertIn(str(self.shared), context)
                self.assertIn("--- BEGIN SHARED MEMORY.md ---", context)
                self.assertIn("Verify first.", context)
                self.assertNotIn("CONTROL ERROR", context)

    def test_shared_declaration_must_be_one_literal_native_external_directory(self) -> None:
        missing = self.temp / "missing shared"
        file_target = self.temp / "shared-file"
        file_target.write_text("not a directory\n", encoding="utf-8")
        device = r"\\.\NUL" if os.name == "nt" else "/dev/null"
        foreign = "/tmp/foreign-shared" if os.name == "nt" else r"C:\foreign-shared"
        cases = {
            "blank": ("Shared:",),
            "relative": ("Shared: nearby/shared",),
            "quoted": (f'Shared: "{self.shared}"',),
            "tilde": ("Shared: ~/shared",),
            "environment": ("Shared: %TEMP%\\shared" if os.name == "nt" else "Shared: $TMPDIR/shared",),
            "uri": (f"Shared: file://{self.shared}",),
            "device": (f"Shared: {device}",),
            "foreign-platform": (f"Shared: {foreign}",),
            "filesystem-root": (f"Shared: {Path(self.home.anchor)}",),
            "private-memory-root": (f"Shared: {self.home / 'memory'}",),
            "private-memory-child": (f"Shared: {self.home / 'memory' / 'local'}",),
            "missing-directory": (f"Shared: {missing}",),
            "regular-file": (f"Shared: {file_target}",),
            "duplicate": (f"Shared: {self.shared}", f"Shared: {self.shared}"),
        }
        for name, declarations in cases.items():
            with self.subTest(case=name):
                self.write_root_memory(*declarations)
                context = json.loads(
                    invoke("codex", self.home, self.event("SessionStart")).stdout
                )["hookSpecificOutput"]["additionalContext"]
                self.assertIn("CONTROL ERROR", context)
                self.assertIn("root memory Shared", context)
                self.assertNotIn("--- BEGIN SHARED MEMORY.md ---", context)

    def test_shared_declaration_in_body_is_not_a_control_header(self) -> None:
        self.write_root_memory(body=f"# Root\n\nShared: {self.shared}\n")
        context = json.loads(
            invoke("codex", self.home, self.event("SessionStart")).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn("requires exactly one", context)
        self.assertNotIn("--- BEGIN SHARED MEMORY.md ---", context)

    def test_invalid_shared_declaration_uses_only_canonical_discovery_hint(self) -> None:
        legacy = self.home / "memory" / "shared"
        make_shared(legacy)
        (legacy / "MEMORY.md").write_text(
            "# Legacy fallback must not load\n\n## Mandatory conventions\n\n- Legacy.\n",
            encoding="utf-8",
        )
        self.write_root_memory()
        canonical = self.temp / "canonical checkout"
        canonical.mkdir()
        (canonical / "RULES.md").write_bytes((self.home / "RULES.md").read_bytes())
        (canonical / "STRUCTURE.md").write_bytes((self.home / "STRUCTURE.md").read_bytes())
        context = json.loads(
            invoke(
                "codex",
                self.home,
                self.event("SessionStart"),
                canonical_root=canonical,
            ).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(canonical / ".shared"), context)
        self.assertNotIn("Legacy fallback must not load", context)
        self.assertNotIn(str(legacy), context)

    def test_invalid_declaration_authorizes_only_parent_root_memory_repair(self) -> None:
        missing = self.temp / "undeclared missing shared"
        self.write_root_memory(f"Shared: {missing}")

        self.assertEqual(
            invoke(
                "codex",
                self.home,
                self.mutation(self.home / "memory" / "MEMORY.md"),
            ).stdout,
            "",
        )
        denied = json.loads(
            invoke("codex", self.home, self.mutation(missing / "MEMORY.md")).stdout
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("root memory Shared", denied["hookSpecificOutput"]["permissionDecisionReason"])

        subagent = self.mutation(self.home / "memory" / "MEMORY.md")
        subagent["agent_id"] = "worker-repair"
        denied = json.loads(invoke("codex", self.home, subagent).stdout)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("read-only", denied["hookSpecificOutput"]["permissionDecisionReason"])

    def test_parent_can_repair_missing_or_malformed_declared_shared_manifest(self) -> None:
        manifest = self.shared / "MEMORY.md"
        for case in ("missing", "malformed"):
            with self.subTest(case=case):
                if case == "missing":
                    manifest.unlink(missing_ok=True)
                else:
                    manifest.write_text(
                        "# Shared\n\n## Mandatory conventions (almost)\n",
                        encoding="utf-8",
                    )
                self.assertEqual(
                    invoke("codex", self.home, self.mutation(manifest)).stdout,
                    "",
                )
                subagent = self.mutation(manifest)
                subagent["agent_id"] = f"worker-{case}"
                denied = json.loads(invoke("codex", self.home, subagent).stdout)
                self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("read-only", denied["hookSpecificOutput"]["permissionDecisionReason"])

    def test_terminal_shared_alias_is_rejected_without_windows_privileges(self) -> None:
        spec = importlib.util.spec_from_file_location("test_declared_shared_hook", HOOK)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        memory_text = (
            "Structure-Version: test-v1\nStructure: ../STRUCTURE.md\n"
            f"Shared: {self.shared}\n\n# Root\n"
        )
        with mock.patch.object(Path, "is_symlink", autospec=True, return_value=True):
            declared, error = module.declared_shared(memory_text, self.home / "memory")
        self.assertIsNone(declared)
        self.assertIn("symlink or junction/reparse alias", error)

        if os.name == "nt":
            fake_stat = mock.Mock(st_file_attributes=module.stat.FILE_ATTRIBUTE_REPARSE_POINT)
            with (
                mock.patch.object(Path, "is_symlink", autospec=True, return_value=False),
                mock.patch.object(Path, "exists", autospec=True, return_value=True),
                mock.patch.object(Path, "lstat", autospec=True, return_value=fake_stat),
            ):
                declared, error = module.declared_shared(memory_text, self.home / "memory")
            self.assertIsNone(declared)
            self.assertIn("junction/reparse alias", error)

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
        result = invoke(
            "claude", self.home, self.event("PreCompact", agent="claude")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        event = self.event("SessionStart", agent="claude")
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
        claude_event = self.event("PreCompact", "manual", agent="claude")
        claude_event["transcript_path"] = event["transcript_path"]
        claude = invoke("claude", self.home, claude_event)
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
        result = invoke(
            "claude",
            self.home,
            self.event("PreCompact", "manual", agent="claude"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("root memory control is invalid", result.stderr)

    def test_automatic_precompact_warns_instead_of_stranding_the_session(self) -> None:
        # Blocking an automatic compaction pins the session at its context
        # ceiling, which loses more continuity than the missing checkpoint.
        event = self.event("PreCompact", "auto")
        event["transcript_path"] = str(self.temp / "missing.jsonl")
        for agent in ("claude", "codex"):
            result = invoke(
                agent,
                self.home,
                self.event("PreCompact", "auto", agent=agent)
                | {"transcript_path": event["transcript_path"]},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            message = json.loads(result.stdout)["systemMessage"]
            self.assertIn("without a continuity checkpoint", message)
            self.assertNotIn("blocked", message)

    def test_precompact_trigger_is_read_from_either_host_key(self) -> None:
        for key in ("trigger", "triggered_by"):
            event = self.event("PreCompact", "auto", agent="claude")
            event.pop("trigger")
            event.pop("triggered_by")
            event[key] = "auto"
            event["transcript_path"] = str(self.temp / "missing.jsonl")
            result = invoke("claude", self.home, event)
            self.assertEqual(result.returncode, 0, key)
            self.assertIn("systemMessage", json.loads(result.stdout))

    def test_unlabelled_precompact_trigger_is_treated_as_manual(self) -> None:
        event = self.event("PreCompact", agent="claude")
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
        self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", subagent)
        self.assertIn("Keep continuity.", subagent)
        self.assertIn("granted equally to subagents", subagent)

    def test_claude_prompt_refresh_does_not_repeat_root_bodies(self) -> None:
        started = json.loads(
            invoke(
                "claude", self.home, self.event("SessionStart", agent="claude")
            ).stdout
        )["hookSpecificOutput"]["additionalContext"]
        refreshed = json.loads(
            invoke(
                "claude",
                self.home,
                self.event("UserPromptSubmit", agent="claude"),
            ).stdout
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", started)
        self.assertIn("Keep continuity.", started)
        self.assertIn("ROOT MEMORY TURN CHECK", refreshed)
        self.assertNotIn("--- BEGIN ROOT memory/MEMORY.md ---", refreshed)
        self.assertNotIn("Keep continuity.", refreshed)
        self.assertIn("read the shared scope", refreshed)

    def test_claude_session_start_before_first_prompt_needs_no_prompt_id(self) -> None:
        event = self.event("SessionStart", agent="claude")
        event.pop("prompt_id")
        result = invoke("claude", self.home, event)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ROOT MEMORY CONTROL", context)

    def test_subagent_start_gets_full_read_only_context(self) -> None:
        # A subagent reads the same authoritative sources as the parent; the
        # read-only boundary is stated explicitly since a subagent never owns
        # a write (enforced separately by handle_pretool, not by this text).
        for agent in ("claude", "codex"):
            subagent = json.loads(
                invoke(
                    agent,
                    self.home,
                    self.event("SubagentStart", agent=agent),
                ).stdout
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", subagent)
            self.assertIn("--- BEGIN ROOT RULES.md ---", subagent)
            self.assertIn("Keep continuity.", subagent)
            self.assertIn("granted equally to subagents", subagent)
            self.assertIn("reserved for the parent session", subagent)
            self.assertIn("report it back to the parent", subagent)

    def test_subagent_memory_access_is_read_only(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        cases = (
            ("Read", {"file_path": str(target)}, True),
            ("Grep", {"path": str(target), "pattern": "note"}, True),
            ("Glob", {"path": str(target), "pattern": "*.md"}, True),
            ("Bash", {"command": f"cat {target}"}, True),
            ("Bash", {"command": f"sed -n '1,20p' {target}"}, True),
            ("exec_command", {"cmd": f"sed -n '1,20p' {target}"}, True),
            ("Write", {"file_path": str(target), "content": "x"}, False),
            ("Bash", {"command": f"sed -ni '1,20p' {target}"}, False),
            ("Bash", {"command": f"sed -n '1,20p' {target} -i"}, False),
            ("Bash", {"command": f"sed -n '1,20p' {target} --in-place"}, False),
            ("Bash", {"command": f"sed -e '1e id' {target}"}, False),
            ("Bash", {"command": f"sed -n '1,20p' {target} -e '1e id'"}, False),
            ("Bash", {"command": f"sed -n 'w {target}' input"}, False),
            ("Bash", {"command": f"printf x > {target}"}, False),
            ("OpaqueExecutor", {"target": str(target)}, False),
        )
        for agent in ("claude", "codex"):
            for tool_name, tool_input, allowed in cases:
                with self.subTest(agent=agent, tool=tool_name, input=tool_input):
                    event = self.event("PreToolUse", agent=agent)
                    event.update({
                        "agent_id": "worker-1",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    })
                    result = invoke(agent, self.home, event)
                    if allowed:
                        self.assertEqual(result.stdout, "")
                    else:
                        output = json.loads(result.stdout)
                        self.assertEqual(
                            output["hookSpecificOutput"]["permissionDecision"], "deny"
                        )
                        self.assertIn(
                            "read-only",
                            output["hookSpecificOutput"]["permissionDecisionReason"],
                        )

    def test_non_subagent_writes_into_valid_memory_are_not_blocked_here(self) -> None:
        # The owning session receives the exact convention bundle once. Its
        # retry acknowledges that digest and may proceed.
        target = self.home / "memory" / "local" / "note.md"
        event = self.event("PreToolUse")
        event.update({
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "x"},
        })
        first = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Convention acknowledgment required", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(invoke("codex", self.home, event).stdout, "")

    def test_changed_convention_digest_requires_a_new_acknowledgment(self) -> None:
        event = self.event("PreToolUse")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(self.temp / "result.txt")}})
        self.assertIn("permissionDecision", invoke("codex", self.home, event).stdout)
        self.assertEqual(invoke("codex", self.home, event).stdout, "")
        shared = self.shared / "MEMORY.md"
        shared.write_text(shared.read_text(encoding="utf-8") + "- Recheck changed rules.\n", encoding="utf-8")
        changed = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(changed["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_changed_declared_shared_target_invalidates_receipt_even_with_same_content(self) -> None:
        event = self.mutation(self.temp / "result.txt")
        self.assertIn("permissionDecision", invoke("codex", self.home, event).stdout)
        self.assertEqual(invoke("codex", self.home, event).stdout, "")

        original = self.shared
        replacement = self.temp / "replacement shared memory"
        make_shared(replacement)
        self.write_root_memory(f"Shared: {replacement.resolve()}")
        changed = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(changed["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(invoke("codex", self.home, event).stdout, "")

        self.write_root_memory(f"Shared: {original}")
        changed_back = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(changed_back["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_new_prompt_resets_even_a_reused_turn_receipt(self) -> None:
        event = self.event("PreToolUse")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(self.temp / "result.txt")}})
        self.assertIn("permissionDecision", invoke("codex", self.home, event).stdout)
        self.assertEqual(invoke("codex", self.home, event).stdout, "")
        invoke("codex", self.home, self.event("UserPromptSubmit"))
        reset = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(reset["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_scoped_memory_mutation_loads_ancestor_conventions(self) -> None:
        local = self.home / "memory" / "local"
        child = local / "submemory" / "project"
        child.mkdir(parents=True)
        (local / "MEMORY.md").write_text(
            "# Local\n\n## Mandatory conventions\n\n- Keep local.\n", encoding="utf-8"
        )
        (child / "MEMORY.md").write_text(
            "# Project\n\n## Mandatory conventions\n\n- Test project.\n", encoding="utf-8"
        )
        event = self.event("PreToolUse")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(child / "note.md")}})
        reason = json.loads(invoke("codex", self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("Keep local.", reason)
        self.assertIn("Test project.", reason)

    def test_declared_shared_scopes_manifests_and_required_reads(self) -> None:
        group = self.shared / "submemory" / "project"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text(
            "# Project\n\n## Mandatory conventions\n\n- Keep declared project.\n",
            encoding="utf-8",
        )
        prerequisite = group / "prerequisite.md"
        prerequisite.write_text("# Required\n\nRead declared prerequisite.\n", encoding="utf-8")
        target = group / "note with spaces.md"
        target.write_text(
            "---\nrequires_read:\n  - prerequisite.md\n---\n\n# Note\n",
            encoding="utf-8",
        )
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                reason = json.loads(
                    invoke(agent, self.home, self.mutation(target, agent=agent)).stdout
                )["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("Verify first.", reason)
                self.assertIn("Keep declared project.", reason)
                self.assertIn("Read declared prerequisite.", reason)

    def test_quoted_shell_path_with_spaces_is_scoped_to_declared_shared(self) -> None:
        group = self.shared / "submemory" / "shell project"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text(
            "# Shell project\n\n## Mandatory conventions\n\n- Quote shared targets.\n",
            encoding="utf-8",
        )
        target = group / "note with spaces.md"
        for agent, tool, command in (
            (
                "claude",
                "Bash",
                f"python3 -c \"open(r'{target}', 'w').write('x')\"",
            ),
            (
                "codex",
                "exec_command",
                f'powershell -Command "Set-Content -LiteralPath \'{target}\' -Value x"',
            ),
        ):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event.update({"tool_name": tool, "tool_input": {"command" if tool == "Bash" else "cmd": command}})
                reason = json.loads(invoke(agent, self.home, event).stdout)[
                    "hookSpecificOutput"
                ]["permissionDecisionReason"]
                self.assertIn("Quote shared targets.", reason)

    def test_apply_patch_headers_preserve_declared_shared_paths_with_spaces(self) -> None:
        group = self.shared / "submemory" / "patch project"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text(
            "# Patch project\n\n## Mandatory conventions\n\n- Guard patch headers.\n",
            encoding="utf-8",
        )
        outside = self.temp / "outside.md"
        cases = {
            "Add File": f"*** Add File: {group / 'added note.md'}\n+# Added\n",
            "Update File": f"*** Update File: {group / 'updated note.md'}\n@@\n-old\n+new\n",
            "Delete File": f"*** Delete File: {group / 'deleted note.md'}\n",
            "Move to": (
                f"*** Update File: {outside}\n"
                f"*** Move to: {group / 'moved note.md'}\n"
                "@@\n-old\n+new\n"
            ),
        }
        for index, (header, patch) in enumerate(cases.items()):
            with self.subTest(header=header):
                subagent = self.event("PreToolUse")
                subagent["turn_id"] = f"patch-subagent-{index}"
                subagent.update(
                    {
                        "agent_id": "worker-patch",
                        "tool_name": "apply_patch",
                        "tool_input": {"patch": patch},
                    }
                )
                denied = json.loads(invoke("codex", self.home, subagent).stdout)
                self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("read-only", denied["hookSpecificOutput"]["permissionDecisionReason"])

                parent = dict(subagent)
                parent.pop("agent_id")
                parent["turn_id"] = f"patch-parent-{index}"
                reason = json.loads(invoke("codex", self.home, parent).stdout)[
                    "hookSpecificOutput"
                ]["permissionDecisionReason"]
                self.assertIn("Guard patch headers.", reason)

    def test_declared_shared_parent_and_subagent_access_boundaries(self) -> None:
        target = self.shared / "note with spaces.md"
        target.write_text("# Shared note\n", encoding="utf-8")
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                read_event = self.event("PreToolUse", agent=agent)
                read_event.update(
                    {
                        "agent_id": "worker-read",
                        "tool_name": "Read",
                        "tool_input": {"file_path": str(target)},
                    }
                )
                self.assertEqual(invoke(agent, self.home, read_event).stdout, "")

                write_event = self.mutation(target, agent=agent)
                write_event["agent_id"] = "worker-write"
                denied = json.loads(invoke(agent, self.home, write_event).stdout)
                self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("read-only", denied["hookSpecificOutput"]["permissionDecisionReason"])

                parent = self.mutation(target, agent=agent)
                reason = json.loads(invoke(agent, self.home, parent).stdout)[
                    "hookSpecificOutput"
                ]["permissionDecisionReason"]
                self.assertIn("Verify first.", reason)

    def test_historical_log_uses_active_group_conventions_only(self) -> None:
        local = self.home / "memory" / "local"
        group = local / "submemory" / "project"
        log = group / "nodes" / "log"
        log.mkdir(parents=True)
        (local / "MEMORY.md").write_text(
            "# Local\n\n## Mandatory conventions\n\n- Keep local.\n", encoding="utf-8"
        )
        (group / "MEMORY.md").write_text(
            "# Project\n\n## Mandatory conventions\n\n- Keep project.\n",
            encoding="utf-8",
        )
        (group / "nodes" / "MEMORY.md").write_text(
            "# Project nodes\n\n- [[note]]\n", encoding="utf-8"
        )
        (log / "MEMORY.md").write_text(
            "# Log: MEMORY\n\n---\nrequires_read:\n  - missing.md\n---\n",
            encoding="utf-8",
        )
        target = log / "note.md"
        target.write_text(
            "---\nrequires_read:\n  - missing.md\n---\n\n# Historical note\n",
            encoding="utf-8",
        )
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event.update({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("Keep local.", reason)
                self.assertIn("Keep project.", reason)
                self.assertNotIn("Project nodes", reason)
                self.assertNotIn("missing.md", reason)
                self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_log_cwd_does_not_make_log_manifest_authoritative(self) -> None:
        local = self.home / "memory" / "local"
        log = local / "log"
        log.mkdir()
        (local / "MEMORY.md").write_text(
            "# Local\n\n## Mandatory conventions\n\n- Keep local.\n", encoding="utf-8"
        )
        (log / "MEMORY.md").write_text("# Log: MEMORY\n", encoding="utf-8")
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event["cwd"] = str(log)
                event.update({"tool_name": "Write", "tool_input": {"file_path": "note.md"}})
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("Keep local.", reason)
                self.assertNotIn("required convention manifest is malformed", reason)
                self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_git_log_from_declared_shared_log_keeps_active_manifest(self) -> None:
        (self.shared / "MEMORY.md").write_text(
            "# Shared\n\n## Mandatory conventions\n\n- Keep shared.\n", encoding="utf-8"
        )
        log = self.shared / "log"
        log.mkdir()
        (log / "MEMORY.md").write_text("# Log: MEMORY\n", encoding="utf-8")
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event["cwd"] = str(self.shared)
                event.update({
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": "git log --oneline --graph --date-order --all; true"
                    },
                })
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("Keep shared.", reason)
                self.assertNotIn("required convention manifest is malformed", reason)
                self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_nested_node_collections_never_become_group_manifests(self) -> None:
        local = self.home / "memory" / "local"
        collection = local / "nodes" / "submemory" / "topic"
        collection.mkdir(parents=True)
        (collection / "MEMORY.md").write_text("# Topic index\n", encoding="utf-8")
        target = collection / "note.md"
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event.update({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertNotIn("required convention manifest is malformed", reason)
                self.assertNotIn("Topic index", reason)
                self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_active_node_prerequisites_and_group_validation_remain_strict(self) -> None:
        local = self.home / "memory" / "local"
        node = local / "nodes" / "note.md"
        node.parent.mkdir(exist_ok=True)
        node.write_text(
            "---\nrequires_read:\n  - missing.md\n---\n\n# Note\n", encoding="utf-8"
        )
        group = local / "submemory" / "broken"
        group.mkdir(parents=True)
        (group / "MEMORY.md").write_text("# Broken\n", encoding="utf-8")
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                event = self.event("PreToolUse", agent=agent)
                event.update({"tool_name": "Write", "tool_input": {"file_path": str(node)}})
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("requires_read is not an active memory Markdown file", reason)

                event["tool_input"] = {"file_path": str(group / "note.md")}
                reason = json.loads(invoke(agent, self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("required convention manifest is malformed", reason)

    def test_stop_requires_acknowledgment_then_allows_retry(self) -> None:
        event = self.event("Stop")
        first = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(first["decision"], "block")
        self.assertIn("Verify first.", first["reason"])
        self.assertEqual(invoke("codex", self.home, event).stdout, "")

    def test_claude_uses_prompt_id_for_receipts(self) -> None:
        event = self.event("PreToolUse", agent="claude")
        event.update({
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.temp / "result.txt")},
        })
        first = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(
            first["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertEqual(invoke("claude", self.home, event).stdout, "")
        event["prompt_id"] = "prompt-2"
        renewed = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(
            renewed["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_codex_receipts_remain_turn_scoped(self) -> None:
        event = self.event("PreToolUse")
        event.update({
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.temp / "result.txt")},
        })
        self.assertIn("permissionDecision", invoke("codex", self.home, event).stdout)
        self.assertEqual(invoke("codex", self.home, event).stdout, "")
        event["turn_id"] = "turn-2"
        self.assertIn("permissionDecision", invoke("codex", self.home, event).stdout)

    def test_claude_stop_uses_prompt_id_and_allows_retry(self) -> None:
        event = self.event("Stop", agent="claude")
        first = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(first["decision"], "block")
        self.assertEqual(invoke("claude", self.home, event).stdout, "")
        event["prompt_id"] = "prompt-2"
        event["stop_hook_active"] = True
        self.assertEqual(invoke("claude", self.home, event).stdout, "")

    def test_subagent_stop_is_convention_gated(self) -> None:
        for agent in ("codex", "claude"):
            event = self.event("SubagentStop", agent=agent)
            event.update({"agent_id": "worker-1", "stop_hook_active": False})
            first = json.loads(invoke(agent, self.home, event).stdout)
            self.assertEqual(first["decision"], "block")
            self.assertEqual(invoke(agent, self.home, event).stdout, "")
            identity_key = "prompt_id" if agent == "claude" else "turn_id"
            event[identity_key] = "second-attempt"
            event["stop_hook_active"] = True
            self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_subagent_receipts_are_isolated_by_agent_id(self) -> None:
        for agent in ("codex", "claude"):
            first = self.event("SubagentStop", agent=agent)
            first["agent_id"] = "worker-1"
            second = dict(first)
            second["agent_id"] = "worker-2"
            self.assertEqual(
                json.loads(invoke(agent, self.home, first).stdout)["decision"],
                "block",
            )
            self.assertEqual(invoke(agent, self.home, first).stdout, "")
            self.assertEqual(
                json.loads(invoke(agent, self.home, second).stdout)["decision"],
                "block",
            )

    def test_repeated_stop_without_host_identity_does_not_loop_forever(self) -> None:
        event = self.event("Stop", agent="claude")
        event.pop("prompt_id")
        first = json.loads(invoke("claude", self.home, event).stdout)
        self.assertEqual(first["decision"], "block")
        event["stop_hook_active"] = True
        self.assertEqual(invoke("claude", self.home, event).stdout, "")

    def test_repeated_stop_never_blocks_when_root_state_is_invalid(self) -> None:
        (self.home / "RULES.md").unlink()
        for agent in ("codex", "claude"):
            event = self.event("SubagentStop", agent=agent)
            event.update({"agent_id": "worker-1", "stopHookActive": True})
            self.assertEqual(invoke(agent, self.home, event).stdout, "")

    def test_completion_outputs_use_only_supported_control_keys(self) -> None:
        for agent in ("codex", "claude"):
            for event_name in ("Stop", "SubagentStop"):
                event = self.event(event_name, agent=agent)
                if event_name == "SubagentStop":
                    event["agent_id"] = "worker-schema"
                output = json.loads(invoke(agent, self.home, event).stdout)
                self.assertEqual(set(output), {"decision", "reason"})
                self.assertEqual(output["decision"], "block")

    def test_pretool_deny_output_uses_supported_control_keys(self) -> None:
        for agent in ("codex", "claude"):
            event = self.event("PreToolUse", agent=agent)
            event.update({
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.temp / f"{agent}.txt")},
            })
            output = json.loads(invoke(agent, self.home, event).stdout)
            self.assertEqual(set(output), {"hookSpecificOutput"})
            specific = output["hookSpecificOutput"]
            self.assertEqual(
                set(specific),
                {
                    "hookEventName",
                    "permissionDecision",
                    "permissionDecisionReason",
                    "additionalContext",
                },
            )
            self.assertEqual(specific["hookEventName"], "PreToolUse")
            self.assertEqual(specific["permissionDecision"], "deny")

    def test_canonical_document_drift_blocks_mutation(self) -> None:
        canonical = self.temp / "canonical"
        canonical.mkdir()
        (canonical / "RULES.md").write_text("# Canonical rules\n", encoding="utf-8")
        (canonical / "STRUCTURE.md").write_text("Structure-Version: test-v2\n", encoding="utf-8")
        event = self.event("PreToolUse")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(self.temp / "result.txt")}})
        output = json.loads(invoke("codex", self.home, event, canonical_root=canonical).stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("differs from canonical", output["hookSpecificOutput"]["permissionDecisionReason"])

        event["tool_input"] = {"file_path": str(self.home / "memory" / "MEMORY.md")}
        root_output = json.loads(
            invoke("codex", self.home, event, canonical_root=canonical).stdout
        )
        self.assertEqual(root_output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_requires_read_cannot_escape_memory_roots(self) -> None:
        secret = self.temp / "secret.txt"
        secret.write_text("DO-NOT-INJECT", encoding="utf-8")
        target = self.home / "memory" / "local" / "note.md"
        target.write_text(
            f"---\nrequires_read:\n  - {secret}\n---\n\n# Note\n",
            encoding="utf-8",
        )
        event = self.event("PreToolUse")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        reason = json.loads(invoke("codex", self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("escapes the active memory roots", reason)
        self.assertNotIn("DO-NOT-INJECT", reason)

    def test_missing_turn_identity_fails_closed(self) -> None:
        event = self.event("PreToolUse")
        event.pop("turn_id")
        event.update({"tool_name": "Write", "tool_input": {"file_path": str(self.temp / "result.txt")}})
        reason = json.loads(invoke("codex", self.home, event).stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("lacks stable session_id and turn_id", reason)

    def test_missing_claude_prompt_identity_fails_closed(self) -> None:
        event = self.event("PreToolUse", agent="claude")
        event.pop("prompt_id")
        event.update({
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.temp / "result.txt")},
        })
        reason = json.loads(invoke("claude", self.home, event).stdout)[
            "hookSpecificOutput"
        ]["permissionDecisionReason"]
        self.assertIn("lacks stable session_id and prompt_id", reason)

    def test_unknown_action_tool_is_convention_gated(self) -> None:
        event = self.event("PreToolUse")
        event.update({"tool_name": "OpaqueExecutor", "tool_input": {"target": str(self.temp / "result.txt")}})
        output = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_main_session_block_is_unchanged_by_the_subagent_scoping(self) -> None:
        # SessionStart/UserPromptSubmit must keep receiving byte-for-byte the
        # same full-block behavior as before subagents were scoped out.
        for agent in ("claude", "codex"):
            started = json.loads(
                invoke(
                    agent, self.home, self.event("SessionStart", agent=agent)
                ).stdout
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("ROOT MEMORY CONTROL", started)
            self.assertIn("--- BEGIN ROOT memory/MEMORY.md ---", started)
            self.assertIn("--- BEGIN ROOT RULES.md ---", started)
            self.assertIn("Keep continuity.", started)
            self.assertIn("Mandatory use: treat the injected files as current authority", started)

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

    def test_node_attachment_writes_are_guarded(self) -> None:
        invalid_home = self.temp / "invalid-home"
        attachment = (
            invalid_home
            / "memory"
            / "local"
            / "nodes"
            / "warm-reset"
            / "reproduce.sh"
        )
        event = self.event("PreToolUse")
        event.update({
            "tool_name": "apply_patch",
            "tool_input": {"patch": f"*** Update File: {attachment}"},
        })
        output = json.loads(invoke("codex", invalid_home, event).stdout)
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

    def test_embedded_script_reads_require_convention_acknowledgment(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        for command in (
            f"python3 -c \"print(open('{target}').read())\"",
            f"python3 - <<'PY'\nprint(open('{target}').read())\nPY",
        ):
            event = self.event("PreToolUse")
            event["turn_id"] = "turn-" + str(abs(hash(command)))
            event.update({"tool_name": "Bash", "tool_input": {"command": command}})
            first = json.loads(invoke("codex", self.home, event).stdout)
            self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(invoke("codex", self.home, event).stdout, "", command)

    def test_interpreter_indirect_mutation_is_convention_gated(self) -> None:
        event = self.event("PreToolUse")
        event.update({
            "tool_name": "Bash",
            "tool_input": {"command": "python3 -c \"import os; os.system('touch result')\""},
        })
        output = json.loads(invoke("codex", self.home, event).stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

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
        event = self.event("SessionStart", agent="claude")
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
        invoke("claude", self.home, self.event("PreCompact", agent="claude"))
        event = self.event("SessionStart", agent="claude")
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
                    "SubagentStop": [{
                        "hooks": [{"type": "command", "command": "keep-subagent"}]
                    }],
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
        if checkpoint_home is not None:
            (checkpoint_home / "RULES.md").write_bytes((REPO / "RULES.md").read_bytes())
            (checkpoint_home / "STRUCTURE.md").write_bytes((REPO / "STRUCTURE.md").read_bytes())
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
        self.assertEqual(
            data["hooks"]["SubagentStop"][0]["hooks"][0]["command"],
            "keep-subagent",
        )
        if manager == CLAUDE_MANAGER:
            self.assertEqual(data["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "SubagentStart",
            "SubagentStop",
            "PreCompact",
            "PreToolUse",
            "Stop",
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
        self.assertEqual(
            (checkpoint_root / "RULES.md").read_bytes(),
            (REPO / "RULES.md").read_bytes(),
        )
        self.assertEqual(
            (checkpoint_root / "STRUCTURE.md").read_bytes(),
            (REPO / "STRUCTURE.md").read_bytes(),
        )
        checkpoint_dir = checkpoint_root / ".agent-mem-struct" / "compaction-checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "orphan.json").write_text("{}\n", encoding="utf-8")
        receipt_dir = checkpoint_root / ".agent-mem-struct" / "convention-receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "orphan.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, str(manager), "uninstall", "--home", str(home)],
            check=True,
            capture_output=True,
            text=True,
            cwd=self.temp,
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(set(data["hooks"]), {"Stop", "SubagentStop"})
        self.assertEqual(
            data["hooks"]["SubagentStop"][0]["hooks"][0]["command"],
            "keep-subagent",
        )
        self.assertEqual(data["env"], {"KEEP": "yes"})
        self.assertFalse(checkpoint_dir.exists())
        self.assertFalse(receipt_dir.exists())
        self.assertFalse((home / ".agent-mem-struct").exists())
        self.assertFalse((checkpoint_root / ".agent-mem-struct").exists())
        if manager == CODEX_MANAGER:
            self.assertEqual(
                (home / "config.toml").read_text(encoding="utf-8"),
                "model = \"keep-model\"\n",
            )

    def test_codex_installer_is_additive_and_idempotent(self) -> None:
        self.check_manager(CODEX_MANAGER, "hooks.json", [])

    def test_codex_installer_refuses_foreign_root_documents(self) -> None:
        home = self.temp / "foreign-root"
        home.mkdir()
        rules = home / "RULES.md"
        rules.write_text("# Personal rules\n\nKeep this.\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(CODEX_MANAGER), "install", "--home", str(home)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Root document differs from canonical", result.stderr)
        self.assertEqual(rules.read_text(encoding="utf-8"), "# Personal rules\n\nKeep this.\n")
        self.assertFalse((home / "hooks.json").exists())

    def test_codex_installer_refreshes_only_with_explicit_flag(self) -> None:
        home = self.temp / "managed-stale-root"
        home.mkdir()
        (home / "RULES.md").write_text(
            "# Memory rules\n\nOld.\n\n© 2026 Edrick Sinsuan\n", encoding="utf-8"
        )
        (home / "STRUCTURE.md").write_text(
            "Structure-Version: old\n\n# Memory structure\n\n© 2026 Edrick Sinsuan\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(CODEX_MANAGER),
            "install",
            "--home",
            str(home),
            "--refresh-root-documents",
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertEqual((home / "RULES.md").read_bytes(), (REPO / "RULES.md").read_bytes())
        self.assertEqual((home / "STRUCTURE.md").read_bytes(), (REPO / "STRUCTURE.md").read_bytes())

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
        make_install_memory_home(old_memory)
        make_install_memory_home(new_memory)
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
        make_install_memory_home(memory_home)
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


class ManagedRootDocumentTests(unittest.TestCase):
    """Exercise both manager markers against an isolated canonical checkout."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="root-document-test-", dir=SCRATCH_ROOT))
        self.canonical = self.temp / "canonical"
        self.canonical.mkdir()
        for name, heading in (("RULES.md", "rules"), ("STRUCTURE.md", "structure")):
            (self.canonical / name).write_text(
                f"# Memory {heading}\n\nVersion one.\n\n© 2026 Edrick Sinsuan\n",
                encoding="utf-8",
            )
        self.managers = {}
        for agent, path in (("codex", CODEX_MANAGER), ("claude", CLAUDE_MANAGER)):
            spec = importlib.util.spec_from_file_location(f"test_{agent}_manager", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.managers[agent] = module
        self.common = sys.modules["manage_common"]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def install(
        self, agent: str, *, home: Path | None = None,
        memory_home: Path | None = None, refresh: bool = False,
    ) -> Path:
        home = home or self.temp / agent
        module = self.managers[agent]
        with (
            mock.patch.object(module, "HOOK_ROOT", self.canonical / "hooks"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            hook = self.canonical / "hooks" / "root-memory-context.py"
            if agent == "codex":
                module.install(home, hook, refresh_documents=refresh)
            else:
                module.install(home, memory_home or home, hook, refresh_documents=refresh)
        return home

    def marker(self, agent: str) -> dict:
        return json.loads(self.managers[agent].marker_path(self.temp / agent).read_text(encoding="utf-8"))

    def revise_canonical(self) -> None:
        for source in self.canonical.glob("*.md"):
            source.write_bytes(source.read_bytes() + b"\nVersion two.\n")

    def test_fresh_install_and_reinstall_use_tracked_regular_copies(self) -> None:
        with mock.patch.object(Path, "symlink_to", side_effect=AssertionError("Root documents must never be symlinks")):
            for agent in self.managers:
                with self.subTest(agent=agent):
                    home = self.install(agent)
                    original_marker = self.marker(agent)
                    mtimes = {}
                    for name in ("RULES.md", "STRUCTURE.md"):
                        target, source = home / name, self.canonical / name
                        self.assertTrue(target.is_file())
                        self.assertFalse(target.is_symlink())
                        self.assertFalse(target.samefile(source))
                        self.assertEqual(target.read_bytes(), source.read_bytes())
                        mtimes[name] = target.stat().st_mtime_ns
                        self.assertEqual(original_marker["rootDocuments"][name], {
                            "path": str(target.absolute()), "source": str(source.resolve()),
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        })
                    self.install(agent)
                    self.assertEqual(self.marker(agent), original_marker)
                    for name, mtime in mtimes.items():
                        self.assertEqual((home / name).stat().st_mtime_ns, mtime)

    def test_unchanged_owned_copies_refresh_on_ordinary_reinstall(self) -> None:
        for agent in self.managers:
            self.install(agent)
        self.revise_canonical()
        for agent in self.managers:
            with self.subTest(agent=agent):
                home = self.install(agent)
                for name in ("RULES.md", "STRUCTURE.md"):
                    self.assertEqual((home / name).read_bytes(), (self.canonical / name).read_bytes())
                    self.assertEqual(self.marker(agent)["rootDocuments"][name]["sha256"],
                                     hashlib.sha256((home / name).read_bytes()).hexdigest())

    def test_reinstall_adopts_current_copy_when_marker_hash_is_stale(self) -> None:
        for agent in self.managers:
            home = self.install(agent)
            stale_marker = self.marker(agent)
            self.revise_canonical()
            # Simulate interruption after the first atomic copy but before the
            # installation marker was saved.
            (home / "RULES.md").write_bytes((self.canonical / "RULES.md").read_bytes())
            self.assertEqual(self.marker(agent), stale_marker)

            with self.subTest(agent=agent):
                self.install(agent)
                for name in ("RULES.md", "STRUCTURE.md"):
                    target = home / name
                    self.assertEqual(target.read_bytes(), (self.canonical / name).read_bytes())
                    self.assertEqual(
                        self.marker(agent)["rootDocuments"][name]["sha256"],
                        hashlib.sha256(target.read_bytes()).hexdigest(),
                    )

    def test_edited_owned_copy_refuses_even_explicit_refresh_before_any_replacement(self) -> None:
        for agent in self.managers:
            self.install(agent)
        self.revise_canonical()
        for agent in self.managers:
            home = self.temp / agent
            changed = home / "STRUCTURE.md"
            changed.write_bytes(changed.read_bytes() + b"User edit.\n")
            protected = {path: path.read_bytes() for path in (
                home / "RULES.md", changed, self.managers[agent].marker_path(home),
                home / ("hooks.json" if agent == "codex" else "settings.json"),
            )}
            for refresh in (False, True):
                with self.subTest(agent=agent, refresh=refresh):
                    with self.assertRaisesRegex(SystemExit, "user-modified managed"):
                        self.install(agent, refresh=refresh)
                    for path, content in protected.items():
                        self.assertEqual(path.read_bytes(), content)

    def test_identical_legacy_copies_are_adopted_then_automatically_refreshed(self) -> None:
        for agent in self.managers:
            home = self.temp / agent
            home.mkdir()
            for source in self.canonical.glob("*.md"):
                (home / source.name).write_bytes(source.read_bytes())
            self.install(agent)
        self.revise_canonical()
        for agent in self.managers:
            home = self.install(agent)
            self.assertEqual((home / "RULES.md").read_bytes(), (self.canonical / "RULES.md").read_bytes())

    def test_stale_legacy_copies_require_explicit_refresh_then_are_tracked(self) -> None:
        for agent in self.managers:
            home = self.temp / agent
            home.mkdir()
            for source in self.canonical.glob("*.md"):
                (home / source.name).write_bytes(source.read_bytes() + b"Old legacy content.\n")
            with self.subTest(agent=agent):
                with self.assertRaisesRegex(SystemExit, "Root document differs from canonical"):
                    self.install(agent)
                self.install(agent, refresh=True)
                self.assertIn("rootDocuments", self.marker(agent))
        self.revise_canonical()
        for agent in self.managers:
            home = self.install(agent)
            self.assertEqual((home / "RULES.md").read_bytes(), (self.canonical / "RULES.md").read_bytes())

    def test_foreign_copies_refuse_even_explicit_refresh(self) -> None:
        for agent in self.managers:
            home = self.temp / agent
            home.mkdir()
            rules = home / "RULES.md"
            rules.write_bytes(b"# Personal rules\nKeep me.\n")
            with self.subTest(agent=agent):
                with self.assertRaisesRegex(SystemExit, "foreign root document"):
                    self.install(agent, refresh=True)
                self.assertEqual(rules.read_bytes(), b"# Personal rules\nKeep me.\n")
                self.assertFalse(self.managers[agent].marker_path(home).exists())

    @contextlib.contextmanager
    def simulated_links(self, destinations: dict[Path, Path]):
        # Windows CI cannot create file symlinks without extra privileges. Mock
        # only link metadata; replacement and copied bytes still use real files.
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve
        with (
            mock.patch.object(Path, "is_symlink", autospec=True,
                              side_effect=lambda path: path in destinations or original_is_symlink(path)),
            mock.patch.object(Path, "resolve", autospec=True,
                              side_effect=lambda path, **kwargs: original_resolve(
                                  destinations.get(path, path), **kwargs)),
        ):
            yield

    def test_matching_legacy_links_are_replaced_by_copies(self) -> None:
        for agent in self.managers:
            with self.subTest(agent=agent):
                home = self.temp / agent
                home.mkdir()
                destinations = {
                    home / name: self.canonical / name
                    for name in ("RULES.md", "STRUCTURE.md")
                }
                if os.name == "nt":
                    for target in destinations:
                        target.write_bytes(b"legacy link placeholder")
                    with self.simulated_links(destinations):
                        self.install(agent)
                else:
                    for target, source in destinations.items():
                        target.symlink_to(source)
                    self.install(agent)
                for target, source in destinations.items():
                    self.assertFalse(target.is_symlink())
                    self.assertEqual(target.read_bytes(), source.read_bytes())
                    self.assertIn(target.name, self.marker(agent)["rootDocuments"])

    def test_mismatched_and_dangling_links_are_refused(self) -> None:
        for agent in self.managers:
            home = self.temp / agent
            home.mkdir()
            target = home / "RULES.md"
            target.write_bytes(b"unrelated link placeholder")
            for exists in (False, True):
                unrelated = self.temp / f"unrelated-{agent}-{exists}.md"
                if exists:
                    unrelated.write_bytes(b"unrelated source")
                with self.subTest(agent=agent, destination_exists=exists):
                    with self.simulated_links({target: unrelated}):
                        with self.assertRaisesRegex(SystemExit, "Refusing to replace root symlink"):
                            self.install(agent, refresh=True)
                    self.assertEqual(target.read_bytes(), b"unrelated link placeholder")

    def test_claude_marker_cannot_authorize_refresh_in_a_different_memory_home(self) -> None:
        old = self.temp / "old-memory"
        self.install("claude", memory_home=old)
        new = self.temp / "new-memory"
        new.mkdir()
        for name in ("RULES.md", "STRUCTURE.md"):
            (new / name).write_bytes((old / name).read_bytes())
        self.revise_canonical()
        with self.assertRaisesRegex(SystemExit, "Root document differs from canonical"):
            self.install("claude", memory_home=new)
        self.assertEqual((new / "RULES.md").read_bytes(), (old / "RULES.md").read_bytes())

    def test_install_refuses_to_replace_canonical_documents_themselves(self) -> None:
        for agent in self.managers:
            with self.subTest(agent=agent):
                with self.assertRaisesRegex(SystemExit, "canonical root document itself"):
                    self.install(agent, home=self.canonical)
                self.assertFalse((self.canonical / "RULES.md").is_symlink())

    def test_bare_reinstall_preserves_installed_memory_home(self) -> None:
        """Omitting --memory-home must not move an installed memory home."""
        import importlib.util as _ilu

        pi_spec = _ilu.spec_from_file_location(
            "ams_pi_manager", REPO / "hooks" / "pi" / "manage.py"
        )
        assert pi_spec and pi_spec.loader
        pi_manager = _ilu.module_from_spec(pi_spec)
        pi_spec.loader.exec_module(pi_manager)
        for agent, manager, marker_name in (
            ("claude", CLAUDE_MANAGER, "claude-root-memory-hook.json"),
            ("pi", REPO / "hooks" / "pi" / "manage.py", "pi-root-memory-hook.json"),
        ):
            with self.subTest(agent=agent):
                agent_home = self.temp / f"{agent}-agent-home"
                memory_home = self.temp / f"{agent}-memory-home"
                agent_home.mkdir()
                memory_home.mkdir()
                make_install_memory_home(memory_home)
                subprocess.run(
                    [sys.executable, str(manager), "install",
                     "--home", str(agent_home), "--memory-home", str(memory_home)],
                    check=True, capture_output=True, text=True, cwd=self.temp,
                )
                # A bare reinstall must keep the installed memory home instead
                # of silently defaulting to the agent home.
                subprocess.run(
                    [sys.executable, str(manager), "install", "--home", str(agent_home)],
                    check=True, capture_output=True, text=True, cwd=self.temp,
                )
                marker = json.loads(
                    (agent_home / ".agent-mem-struct" / marker_name).read_text()
                )
                self.assertEqual(marker["memoryHome"], str(memory_home))
                if agent == "claude":
                    settings = json.loads((agent_home / "settings.json").read_text())
                    commands = [
                        hook["command"]
                        for groups in settings["hooks"].values()
                        for group in groups
                        for hook in group["hooks"]
                        if "root-memory" in hook["command"]
                    ]
                    self.assertTrue(commands)
                    self.assertIn(str(memory_home), commands[0])
                else:
                    bridge = (
                        agent_home / "extensions" / "agent-mem-struct.ts"
                    ).read_text()
                    self.assertIn(str(memory_home), bridge)


    def test_uninstall_leaves_installed_root_documents(self) -> None:
        for agent, module in self.managers.items():
            home = self.install(agent)
            with contextlib.redirect_stdout(io.StringIO()):
                module.uninstall(home) if agent == "codex" else module.uninstall(home, home)
            for name in ("RULES.md", "STRUCTURE.md"):
                self.assertFalse((home / name).is_symlink())
                self.assertEqual((home / name).read_bytes(), (self.canonical / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
