#!/usr/bin/env python3
"""Pi bridge and installer tests for the root-memory integration."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"
PI_MANAGER = REPO / "hooks" / "pi" / "manage.py"
EXTENSION_TEMPLATE = REPO / "hooks" / "pi" / "root-memory-extension.ts"
PACKAGE_ENTRY = REPO / "extensions" / "agent-mem-struct.ts"
PACKAGE_MANIFEST = REPO / "package.json"


def js_safe(value: str) -> str:
    """The spelling the installer bakes into the TS bridge (forward slashes)."""
    return value.replace(os.sep, "/")
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


def make_home(path: Path, shared: Path | None = None) -> tuple[Path, Path]:
    if shared is None:
        shared = make_shared(path.parent / f"{path.name} shared memory")
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
    return path, shared


def git_run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False
    )


def load_hook_module(name: str):
    """Import one hook module directly for a deterministic unit-level call."""
    hooks = str(REPO / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    spec = importlib.util.spec_from_file_location(name, REPO / "hooks" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_git_shared(origin: Path, shared: Path) -> Path:
    """Build a Git-backed shared root with an origin the clone can pull from."""
    origin.mkdir(parents=True)
    git_run("init", "--bare", "-b", "main", cwd=origin)
    seed = origin.parent / f"{origin.name}-seed"
    seed.mkdir()
    git_run("init", "-b", "main", cwd=seed)
    (seed / "MEMORY.md").write_text(
        "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n",
        encoding="utf-8",
    )
    git_run("add", "MEMORY.md", cwd=seed)
    git_run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed", cwd=seed)
    git_run("remote", "add", "origin", str(origin), cwd=seed)
    git_run("push", "-u", "origin", "main", cwd=seed)
    git_run("clone", str(origin), str(shared), cwd=shared.parent)
    return shared.resolve()


def invoke_pi(home: Path, event: dict[str, object], *, canonical_root: Path | None = None,
              config_home: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(HOOK), "--agent", "pi", "--home", str(home)]
    command.extend(("--canonical-root", str(canonical_root or home)))
    if config_home is not None:
        command.extend(("--config-home", str(config_home)))
    return subprocess.run(
        command, input=json.dumps(event), text=True, capture_output=True, check=False,
    )


def read_conventions(
    home: Path, shared: Path, session: str = "pi-session", turn: str = "turn-1"
) -> None:
    """Open every required convention source the way the model must."""
    for path in (
        home / "memory" / "MEMORY.md",
        home / "RULES.md",
        home / "memory" / "local" / "MEMORY.md",
        shared / "MEMORY.md",
    ):
        invoke_pi(home, {
            "hook_event_name": "PreToolUse",
            "session_id": session,
            "turn_id": turn,
            "tool_name": "read",
            "tool_input": {"path": str(path)},
            "cwd": str(home),
        })


class PiHookTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pi-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout)

    def test_session_start_points_at_pre_memory_instead_of_the_bundle(self) -> None:
        result = invoke_pi(self.home, {
            "hook_event_name": "SessionStart",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "source": "startup",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.output(result)["hookSpecificOutput"]
        self.assertEqual(body["hookEventName"], "SessionStart")
        context = body["additionalContext"]
        # Only a one-line pointer; the conventions load through pre_memory.
        self.assertIn("pre_memory", context)
        self.assertNotIn("\n", context)
        for body_text in ("## Mandatory conventions", "Verify first.", "--- BEGIN"):
            self.assertNotIn(body_text, context)
        reminder = invoke_pi(self.home, {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "pi-session",
            "turn_id": "turn-2",
        })
        reminder_context = self.output(reminder)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(reminder_context, context)

    def test_turn_reminder_is_only_the_one_line_pointer(self) -> None:
        reminder = invoke_pi(self.home, {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        context = self.output(reminder)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("pre_memory", context)
        self.assertNotIn("\n", context)
        self.assertNotIn("git pull --ff-only", context)
        self.assertNotIn("Before settling", context)
        self.assertNotIn("Pi keeps no native memory store", context)

    def write(self, target: Path) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "tool_name": "write",
            "cwd": str(self.temp),
            "tool_input": {"path": str(target), "content": "x"},
        }

    def test_convention_gate_denies_without_pre_memory_then_allows(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        first = invoke_pi(self.home, self.write(target))
        self.assertEqual(first.returncode, 0, first.stderr)
        denial = self.output(first)["hookSpecificOutput"]
        self.assertEqual(denial["permissionDecision"], "deny")
        self.assertIn("call pre_memory first", denial["permissionDecisionReason"])
        # The denial must not inject the bundle; pre_memory is the only delivery.
        for body_text in ("## Mandatory conventions", "Verify first.", "--- BEGIN"):
            self.assertNotIn(body_text, denial["permissionDecisionReason"])

        loaded = invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        catalog = self.output(loaded)["hookSpecificOutput"]["additionalContext"]
        # The catalog names the sources and the rules, never the bodies.
        self.assertIn("ROOT MEMORY CATALOG", catalog)
        self.assertIn("Navigation rules", catalog)
        for path in (self.home / "memory" / "MEMORY.md", self.home / "RULES.md",
                     self.shared / "MEMORY.md"):
            self.assertIn(str(path), catalog)
        for body_text in ("## Mandatory conventions", "Verify first.", "--- BEGIN"):
            self.assertNotIn(body_text, catalog)

        # Acknowledgment is not complete until the sources are read.
        unread = self.output(invoke_pi(self.home, self.write(target)))["hookSpecificOutput"]
        self.assertEqual(unread["permissionDecision"], "deny")
        self.assertIn("Read these sources with the `read` tool",
                      unread["permissionDecisionReason"])
        self.assertIn(str(self.home / "memory" / "MEMORY.md"),
                      unread["permissionDecisionReason"])

        read_conventions(self.home, self.shared)
        second = invoke_pi(self.home, self.write(target))
        # An allowed call prints nothing; the host treats empty stdout as allow.
        self.assertEqual(second.stdout.strip(), "")

    def test_changed_convention_source_re_requires_pre_memory(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        loaded = invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        read_conventions(self.home, self.shared)
        self.assertEqual(invoke_pi(self.home, self.write(target)).stdout.strip(), "")

        shared = self.shared / "MEMORY.md"
        shared.write_text(
            shared.read_text(encoding="utf-8") + "- Recheck changed rules.\n",
            encoding="utf-8",
        )
        changed = self.output(invoke_pi(self.home, self.write(target)))["hookSpecificOutput"]
        self.assertEqual(changed["permissionDecision"], "deny")
        self.assertIn("call pre_memory first", changed["permissionDecisionReason"])

    def test_read_gate_tracks_each_required_source(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        # Reading an unrelated path is not acknowledgment evidence.
        invoke_pi(self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "tool_name": "read",
            "tool_input": {"path": str(self.temp / "unrelated.txt")},
            "cwd": str(self.home),
        })
        still = self.output(invoke_pi(self.home, self.write(target)))["hookSpecificOutput"]
        self.assertEqual(still["permissionDecision"], "deny")
        self.assertIn(str(self.home / "memory" / "MEMORY.md"),
                      still["permissionDecisionReason"])

        read_conventions(self.home, self.shared)
        self.assertEqual(invoke_pi(self.home, self.write(target)).stdout.strip(), "")

    def test_a_changed_source_re_requires_its_read(self) -> None:
        target = self.home / "memory" / "local" / "note.md"
        invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        read_conventions(self.home, self.shared)
        self.assertEqual(invoke_pi(self.home, self.write(target)).stdout.strip(), "")

        rules = self.home / "RULES.md"
        rules.write_text(rules.read_text(encoding="utf-8") + "# Rewritten.\n", encoding="utf-8")
        denied = self.output(invoke_pi(self.home, self.write(target)))["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("Read these sources with the `read` tool",
                      denied["permissionDecisionReason"])
        self.assertIn(str(rules), denied["permissionDecisionReason"])

        # Re-reading the changed source restores the hash-bound evidence.
        invoke_pi(self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "tool_name": "read",
            "tool_input": {"path": str(rules)},
            "cwd": str(self.home),
        })
        self.assertEqual(invoke_pi(self.home, self.write(target)).stdout.strip(), "")

    def test_pre_memory_pulls_shared_and_records_a_session_receipt(self) -> None:
        origin = self.temp / "origin.git"
        shared = make_git_shared(origin, self.temp / "git shared")
        home, _ = make_home(self.temp / "git-home", shared)
        writer = self.temp / "writer"
        self.assertEqual(
            subprocess.run(["git", "clone", str(origin), str(writer)],
                           text=True, capture_output=True, check=False).returncode,
            0,
        )
        with open(writer / "MEMORY.md", "a", encoding="utf-8") as handle:
            handle.write("- Pulled rule.\n")
        git_run("add", "MEMORY.md", cwd=writer)
        git_run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "change", cwd=writer)
        self.assertEqual(git_run("push", "origin", "main", cwd=writer).returncode, 0)

        result = invoke_pi(home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pulled rule.", (shared / "MEMORY.md").read_text(encoding="utf-8"))
        catalog = self.output(result)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("pre_memory", catalog)
        # The catalog lists the pulled source; its bytes are read on demand.
        self.assertIn(str(shared / "MEMORY.md"), catalog)
        self.assertNotIn("Pulled rule.", catalog)

        receipts = list((home / ".agent-mem-struct" / "convention-receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        # The receipt must hash the pulled bytes, not the pre-pull snapshot.
        recorded = json.loads(receipts[0].read_text(encoding="utf-8"))["sources"]
        pulled = hashlib.sha256((shared / "MEMORY.md").read_bytes()).hexdigest()
        self.assertEqual(recorded[os.path.normcase(str(shared / "MEMORY.md"))], pulled)
        # The receipt is session-scoped: a later turn reuses the same one.
        again = invoke_pi(home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-2",
        })
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(
            len(list((home / ".agent-mem-struct" / "convention-receipts").glob("*.json"))),
            1,
        )

    def test_pre_memory_pull_failure_blocks_and_records_no_receipt(self) -> None:
        origin = self.temp / "origin.git"
        shared = make_git_shared(origin, self.temp / "git shared")
        writer = self.temp / "writer"
        subprocess.run(["git", "clone", str(origin), str(writer)],
                       text=True, capture_output=True, check=False)
        with open(writer / "MEMORY.md", "a", encoding="utf-8") as handle:
            handle.write("- Raced rule.\n")
        git_run("add", "MEMORY.md", cwd=writer)
        git_run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "raced", cwd=writer)
        self.assertEqual(git_run("push", "origin", "main", cwd=writer).returncode, 0)
        (shared / "local.md").write_text("local\n", encoding="utf-8")
        git_run("add", "local.md", cwd=shared)
        git_run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "local", cwd=shared)
        home, _ = make_home(self.temp / "diverged-home", shared)
        result = invoke_pi(home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared worktree could not be", result.stdout)
        self.assertFalse((home / ".agent-mem-struct" / "convention-receipts").exists())

    def test_config_is_active_matches_pi_environment(self) -> None:
        spec = importlib.util.spec_from_file_location("pi_hook_module", HOOK)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        configured = self.temp / "pi-home"
        previous = os.environ.get("PI_CODING_AGENT_DIR")
        os.environ["PI_CODING_AGENT_DIR"] = str(configured)
        try:
            self.assertTrue(module.config_is_active("pi", configured))
            self.assertTrue(module.config_is_active("pi", None))
            self.assertFalse(module.config_is_active("pi", self.temp / "other"))
        finally:
            if previous is None:
                os.environ.pop("PI_CODING_AGENT_DIR", None)
            else:
                os.environ["PI_CODING_AGENT_DIR"] = previous

    def test_checkpoint_from_inline_session_entries(self) -> None:
        entries = [
            {"type": "custom_message", "customType": "x", "content": "injected context"},
            {"type": "message", "message": {"role": "user", "content": "Ship the pi bridge."}},
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Wrote the bridge and the installer."}
            ]}},
        ]
        saved = invoke_pi(self.home, {
            "hook_event_name": "PreCompact",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "triggered_by": "manual",
            "session_entries": entries,
        })
        self.assertEqual(saved.returncode, 0, saved.stderr)
        restored = invoke_pi(self.home, {
            "hook_event_name": "SessionStart",
            "session_id": "pi-session",
            "turn_id": "turn-2",
            "source": "compact",
        })
        self.assertEqual(restored.returncode, 0, restored.stderr)
        context = self.output(restored)["hookSpecificOutput"]["additionalContext"]
        # Silence is the failure mode: the checkpoint must actually appear.
        self.assertIn("SHIP THE PI BRIDGE", context.upper())
        self.assertIn("Wrote the bridge and the installer.", context)
        self.assertIn("PRE-COMPACTION CONTINUITY CHECKPOINT", context)

    def test_manual_compaction_blocked_without_anchors(self) -> None:
        blocked = invoke_pi(self.home, {
            "hook_event_name": "PreCompact",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "triggered_by": "manual",
            "session_entries": [{"type": "model_change"}],
        })
        self.assertEqual(blocked.returncode, 2, blocked.stdout)
        automatic = invoke_pi(self.home, {
            "hook_event_name": "PreCompact",
            "session_id": "pi-session",
            "turn_id": "turn-2",
            "triggered_by": "auto",
            "session_entries": [{"type": "model_change"}],
        })
        self.assertEqual(automatic.returncode, 0, automatic.stderr)
        self.assertIn("without a continuity checkpoint", automatic.stdout)


class PiInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pi-install-", dir=SCRATCH_ROOT))
        self.memory_home, self.shared = make_home(self.temp / "memory-home")
        # The installer deploys the canonical root documents; start from
        # byte-identical copies so the fresh install has nothing to refuse.
        (self.memory_home / "RULES.md").write_bytes((REPO / "RULES.md").read_bytes())
        (self.memory_home / "STRUCTURE.md").write_bytes((REPO / "STRUCTURE.md").read_bytes())
        self.pi_home = self.temp / "pi-home"

    def run_manager(self, action: str, home: Path, memory_home: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PI_MANAGER), action,
             "--home", str(home), "--memory-home", str(memory_home)],
            capture_output=True, text=True, check=False,
        )

    def extension_path(self, home: Path) -> Path:
        return home / "extensions" / "agent-mem-struct.ts"

    def run_sync(self, home: Path, memory_home: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PI_MANAGER), "sync-documents",
             "--home", str(home), "--memory-home", str(memory_home)],
            capture_output=True, text=True, check=False,
        )

    def test_sync_documents_deploys_missing_and_tracks(self) -> None:
        (self.memory_home / "RULES.md").unlink()
        (self.memory_home / "STRUCTURE.md").unlink()
        result = self.run_sync(self.pi_home, self.memory_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.memory_home / "RULES.md").read_bytes(), (REPO / "RULES.md").read_bytes()
        )
        self.assertEqual(
            (self.memory_home / "STRUCTURE.md").read_bytes(),
            (REPO / "STRUCTURE.md").read_bytes(),
        )
        marker = json.loads(
            (self.pi_home / ".agent-mem-struct" / "pi-package-root-documents.json").read_text()
        )
        self.assertEqual(
            marker["rootDocuments"]["RULES.md"]["sha256"],
            hashlib.sha256((REPO / "RULES.md").read_bytes()).hexdigest(),
        )
        # A second run is a no-op and installs no bridge or installer marker.
        again = self.run_sync(self.pi_home, self.memory_home)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(self.extension_path(self.pi_home).exists())
        self.assertFalse(
            (self.pi_home / ".agent-mem-struct" / "pi-root-memory-hook.json").exists()
        )

    def test_sync_documents_refreshes_a_tracked_unmodified_copy(self) -> None:
        self.assertEqual(self.run_sync(self.pi_home, self.memory_home).returncode, 0)
        marker_path = self.pi_home / ".agent-mem-struct" / "pi-package-root-documents.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        stale = "# Memory rules\n\nstale managed body\n"
        (self.memory_home / "RULES.md").write_text(stale, encoding="utf-8")
        # A tracked copy whose recorded hash still matches is unmodified, so it
        # is safe to refresh to the canonical bytes. Hash the copy's actual
        # bytes: text-mode writes translate newlines on Windows, so hashing the
        # source string would not match the file that lands on disk there.
        marker["rootDocuments"]["RULES.md"]["sha256"] = hashlib.sha256(
            (self.memory_home / "RULES.md").read_bytes()
        ).hexdigest()
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        refreshed = self.run_sync(self.pi_home, self.memory_home)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(
            (self.memory_home / "RULES.md").read_bytes(), (REPO / "RULES.md").read_bytes()
        )

    def test_sync_documents_refuses_a_user_edited_copy(self) -> None:
        self.assertEqual(self.run_sync(self.pi_home, self.memory_home).returncode, 0)
        rules = self.memory_home / "RULES.md"
        rules.write_text("# Memory rules\n\nuser edit\n", encoding="utf-8")
        refused = self.run_sync(self.pi_home, self.memory_home)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), "# Memory rules\n\nuser edit\n")

    def test_sync_documents_refuses_a_foreign_copy(self) -> None:
        rules = self.memory_home / "RULES.md"
        rules.unlink()
        rules.write_text("not ours\n", encoding="utf-8")
        refused = self.run_sync(self.pi_home, self.memory_home)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), "not ours\n")

    def test_install_deploys_baked_bridge_and_marker(self) -> None:
        result = self.run_manager("install", self.pi_home, self.memory_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        extension = self.extension_path(self.pi_home)
        self.assertTrue(extension.is_file())
        content_bytes = extension.read_bytes()
        content = content_bytes.decode("utf-8")
        self.assertNotIn("__AMS_", content)
        # The bridge bakes paths in a JS-safe (forward-slash) spelling on
        # every host; compare against that form, not the native one.
        self.assertIn(js_safe(str(HOOK)), content)
        self.assertIn(js_safe(str(self.memory_home)), content)
        self.assertIn(js_safe(str(self.pi_home)), content)
        marker = json.loads((self.pi_home / ".agent-mem-struct" / "pi-root-memory-hook.json").read_text())
        self.assertEqual(marker["memoryHome"], str(self.memory_home))
        self.assertEqual(
            marker["extension"]["sha256"],
            hashlib.sha256(content_bytes).hexdigest(),
        )
        self.assertTrue((self.memory_home / "RULES.md").is_file())
        # A refresh produces identical bytes: the deploy is idempotent.
        again = self.run_manager("install", self.pi_home, self.memory_home)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(extension.read_bytes(), content_bytes)

    def test_install_refuses_foreign_extension(self) -> None:
        self.pi_home.mkdir(parents=True)
        foreign = self.extension_path(self.pi_home)
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("export default function () {}\n", encoding="utf-8")
        result = self.run_manager("install", self.pi_home, self.memory_home)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreign extension", result.stderr)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "export default function () {}\n")

    def test_uninstall_removes_owned_bridge_and_preserves_edits(self) -> None:
        self.assertEqual(self.run_manager("install", self.pi_home, self.memory_home).returncode, 0)
        extension = self.extension_path(self.pi_home)
        self.run_manager("uninstall", self.pi_home, self.memory_home)
        self.assertFalse(extension.exists())
        self.assertFalse((self.pi_home / ".agent-mem-struct" / "pi-root-memory-hook.json").exists())

        self.run_manager("install", self.pi_home, self.memory_home)
        extension.write_text("// user edited\n", encoding="utf-8")
        kept = self.run_manager("uninstall", self.pi_home, self.memory_home)
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertTrue(extension.exists())
        self.assertEqual(extension.read_text(encoding="utf-8"), "// user edited\n")

    def test_baked_paths_survive_js_literal_semantics(self) -> None:
        """Baked bridge paths must evaluate, in JavaScript, to the real paths.

        Regression: the renderer baked raw Windows paths (single backslashes)
        into TS double-quoted literals, e.g. "C:\Python314\python.exe".
        JavaScript drops the backslash of every unknown escape, so the value
        became C:Python314python.exe, the bridge spawn failed with ENOENT,
        and the memory gate fell open silently on Windows. Decode each baked
        literal the way js/jiti would; any lone backslash makes the test fail
        with the offending constant named.
        """
        self.assertEqual(
            self.run_manager("install", self.pi_home, self.memory_home).returncode, 0
        )
        content = self.extension_path(self.pi_home).read_text(encoding="utf-8")
        expected = {
            "PYTHON": sys.executable,
            "HOOK": str(HOOK),
            "MEMORY_HOME": str(self.memory_home),
            "CONFIG_HOME": str(self.pi_home),
            "CANONICAL_ROOT": str(REPO),
        }
        for name, native in expected.items():
            match = re.search(rf'\bconst {name} = "([^"]*)"\s*;', content)
            self.assertIsNotNone(match, f"missing baked constant {name}")
            literal = match.group(1)
            # js only ever consumes a backslash as part of a \\ pair; any odd
            # run is a swallowed escape that corrupts the path.
            self.assertEqual(
                literal.count("\\") % 2, 0,
                f"{name}: lone backslash in baked literal would be eaten by JS",
            )
            decoded = literal.replace("\\\\", "\\")
            self.assertEqual(decoded, js_safe(native), name)

    def test_template_ownership_header_and_syntax(self) -> None:
        text = EXTENSION_TEMPLATE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// agent-mem-struct root memory:"))
        self.assertEqual(
            self.run_manager("install", self.pi_home, self.memory_home).returncode, 0
        )
        out_root = Path(tempfile.mkdtemp(prefix="ams-esbuild-", dir=SCRATCH_ROOT))
        for label, source in (
            ("template", EXTENSION_TEMPLATE),
            ("installed", self.extension_path(self.pi_home)),
        ):
            if not source.is_file():
                continue
            compiled = subprocess.run(
                [shutil.which("npx") or "npx", "--yes", "esbuild", str(source),
                 "--outfile=" + str(out_root / f"{label}.js")],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(compiled.returncode, 0, label + ": " + compiled.stderr)


class PiPackageTests(unittest.TestCase):
    """The pi-package deployment mode: manifest, entry, and runtime paths."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-package-", dir=SCRATCH_ROOT))
        self.install_bare_stubs()

    def install_bare_stubs(self) -> None:
        """Stub the bare imports the bridge needs so the bundle can execute.

        Pi resolves `typebox` and `@earendil-works/pi-tui` through its own
        loader aliases; a hermetic bundle test supplies tiny stand-ins.
        """
        typebox = self.temp / "node_modules" / "typebox"
        typebox.mkdir(parents=True)
        (typebox / "package.json").write_text(
            json.dumps({"name": "typebox", "version": "0.0.0", "type": "module", "main": "index.mjs"}),
            encoding="utf-8",
        )
        (typebox / "index.mjs").write_text(
            "const schema = (type, extra = {}) => ({ type, ...extra });\n"
            "export const Type = {\n"
            "  Object: (properties = {}) => ({ type: 'object', properties }),\n"
            "  String: (extra = {}) => schema('string', extra),\n"
            "  Number: () => schema('number'),\n"
            "  Boolean: () => schema('boolean'),\n"
            "  Array: (items) => schema('array', { items }),\n"
            "  Optional: (inner) => ({ optional: true, ...(inner || {}) }),\n"
            "  Union: (anyOf) => ({ anyOf }),\n"
            "  Literal: (value) => ({ const: value }),\n"
            "};\n",
            encoding="utf-8",
        )
        tui = self.temp / "node_modules" / "@earendil-works" / "pi-tui"
        tui.mkdir(parents=True)
        (tui / "package.json").write_text(
            json.dumps({
                "name": "@earendil-works/pi-tui",
                "version": "0.0.0",
                "type": "module",
                "main": "index.mjs",
            }),
            encoding="utf-8",
        )
        (tui / "index.mjs").write_text(
            "export class Text { constructor(text) { this.text = text; } }\n",
            encoding="utf-8",
        )

    def bundle(self, out_name: str = "entry.mjs") -> Path:
        out = self.temp / out_name
        compiled = subprocess.run(
            [shutil.which("npx") or "npx", "--yes", "esbuild", str(PACKAGE_ENTRY),
             "--bundle", "--format=esm", "--platform=node",
             "--external:typebox", "--external:@earendil-works/pi-tui",
             "--outfile=" + str(out)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        return out

    def run_driver(self, bundle: Path, mode: str, env: dict[str, str]) -> dict:
        stub = self.temp / "stub_hook.py"
        stub.write_text(
            "import json, sys\n"
            "print(json.dumps({'hookSpecificOutput': {"
            "'hookEventName': 'SessionStart', "
            "'additionalContext': 'PACKAGE-BRIDGE-STUB'}}))\n",
            encoding="utf-8",
        )
        driver = self.temp / "driver.mjs"
        driver.write_text(
            "import { pathToFileURL } from 'node:url';\n"
            "const mod = await import(pathToFileURL(process.argv[2]).href);\n"
            "const mode = process.argv[3];\n"
            "const handlers = {};\n"
            "const tools = [];\n"
            "const sent = [];\n"
            "const pi = { on: (n, f) => { handlers[n] = f; },\n"
            "  registerTool: (t) => tools.push(t.name),\n"
            "  sendMessage: (message, options) => sent.push({ message, options }),\n"
            "  sessionManager: { getSessionId: () => 's1' } };\n"
            "if (mode === 'paths') {\n"
            "  const paths = new mod.PackageBridgePaths({ AMS_PYTHON: 'P', AMS_HOOK: 'H',\n"
            "    AMS_MEMORY_HOME: 'M', AMS_CONFIG_HOME: 'C', AMS_CANONICAL_ROOT: 'R' });\n"
            "  process.stdout.write(JSON.stringify(paths.config()));\n"
            "} else {\n"
            "  mod.default(pi);\n"
            "  if (mode === 'register') {\n"
            "    const out = await handlers['before_agent_start']({ prompt: 'hi' });\n"
            "    process.stdout.write(JSON.stringify({\n"
            "      registered: Object.keys(handlers).sort(), tools: tools.sort(), out }));\n"
            "  } else if (mode === 'compact') {\n"
            "    await handlers['session_compact']({});\n"
            "    process.stdout.write(JSON.stringify({\n"
            "      registered: Object.keys(handlers).sort(), tools: tools.sort(), sent }));\n"
            "  } else {\n"
            "    process.stdout.write(JSON.stringify({ registered: Object.keys(handlers), tools: tools.sort() }));\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        full_env = dict(os.environ)
        full_env.update({
            "AMS_PYTHON": sys.executable,
            "AMS_HOOK": str(stub),
            "AMS_MEMORY_HOME": env.get("memory_home", str(self.temp / "memory")),
            "AMS_CONFIG_HOME": env["config_home"],
            "AMS_CANONICAL_ROOT": str(REPO),
        })
        run = subprocess.run(
            [shutil.which("node") or "node", str(driver), str(bundle), mode],
            capture_output=True, text=True, check=False, env=full_env,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_manifest_declares_a_pi_package_and_its_entry(self) -> None:
        manifest = json.loads(PACKAGE_MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("pi-package", manifest["keywords"])
        self.assertEqual(manifest["pi"]["extensions"], ["./extensions/agent-mem-struct.ts"])
        self.assertTrue(PACKAGE_ENTRY.is_file())
        self.assertEqual(manifest["license"], "MIT")
        # The bridge imports typebox, which pi bundles and resolves through its
        # own loader aliases. Declaring it optional lets a git install vendor it
        # without making it a hard requirement.
        self.assertEqual(manifest["peerDependencies"], {"typebox": "*"})
        self.assertTrue(manifest["peerDependenciesMeta"]["typebox"]["optional"])
        self.assertIn("hooks/", manifest["files"])

    def test_entry_compiles_and_shares_the_one_bridge_implementation(self) -> None:
        self.bundle()
        entry = PACKAGE_ENTRY.read_text(encoding="utf-8")
        template = EXTENSION_TEMPLATE.read_text(encoding="utf-8")
        # The package entry reuses the installer template's bridge rather than
        # carrying a second copy of the event mapping.
        self.assertIn('from "../hooks/pi/root-memory-extension.ts"', entry)
        self.assertIn("export function createRootMemoryBridge", template)
        self.assertIn("export class RootMemoryBridge", template)
        self.assertIn("managedCopyPresent", entry)

    def test_entry_stands_down_while_the_managed_copy_is_present(self) -> None:
        bundle = self.bundle()
        config_home = self.temp / "pi-home"
        (config_home / ".agent-mem-struct").mkdir(parents=True)
        (config_home / ".agent-mem-struct" / "pi-root-memory-hook.json").write_text("{}", encoding="utf-8")
        (config_home / "extensions").mkdir(parents=True)
        (config_home / "extensions" / "agent-mem-struct.ts").write_text("// managed\n", encoding="utf-8")
        sentinel = self.temp / "synced.txt"
        self.install_sync_stub(sentinel)
        result = self.run_driver(bundle, "guard", {"config_home": str(config_home)})
        self.assertEqual(result["registered"], [], "the package entry must not double-register")
        self.assertFalse(sentinel.exists(), "a managed copy must keep the package from syncing")

    def install_sync_stub(self, sentinel: Path) -> None:
        """A stand-in for hooks/pi/manage.py resolved beside the stub AMS_HOOK."""
        stub_dir = self.temp / "pi"
        stub_dir.mkdir(parents=True, exist_ok=True)
        (stub_dir / "manage.py").write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(sentinel)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
            encoding="utf-8",
        )

    def test_entry_syncs_root_documents_when_unmanaged(self) -> None:
        bundle = self.bundle()
        config_home = self.temp / "pi-home-sync"
        config_home.mkdir(parents=True)
        sentinel = self.temp / "synced.txt"
        self.install_sync_stub(sentinel)
        self.run_driver(bundle, "guard", {"config_home": str(config_home)})
        self.assertTrue(sentinel.is_file(), "an active package entry must deploy the root documents")
        self.assertIn("sync-documents", sentinel.read_text(encoding="utf-8"))

    def test_entry_registers_and_reaches_the_hook_when_unmanaged(self) -> None:
        bundle = self.bundle()
        config_home = self.temp / "pi-home-bare"
        config_home.mkdir(parents=True)
        result = self.run_driver(bundle, "register", {"config_home": str(config_home)})
        self.assertIn("before_agent_start", result["registered"])
        self.assertIn("tool_call", result["registered"])
        self.assertIn("session_before_compact", result["registered"])
        self.assertIn("session_compact", result["registered"])
        self.assertIn("pre_memory", result["tools"])
        self.assertIn("memory_update", result["tools"])
        self.assertIn("PACKAGE-BRIDGE-STUB", result["out"]["message"]["content"])

    def test_session_compact_injects_the_memory_pointer_immediately(self) -> None:
        # A mid-run compaction resumes without another before_agent_start, so
        # the bridge must steer the pointer into the ongoing context itself.
        bundle = self.bundle()
        config_home = self.temp / "pi-home-compact"
        config_home.mkdir(parents=True)
        result = self.run_driver(bundle, "compact", {"config_home": str(config_home)})
        self.assertEqual(len(result["sent"]), 1)
        sent = result["sent"][0]
        self.assertEqual(sent["options"]["deliverAs"], "steer")
        self.assertEqual(sent["message"]["customType"], "agent-mem-struct-root-memory")
        self.assertFalse(sent["message"]["display"])
        self.assertIn("PACKAGE-BRIDGE-STUB", sent["message"]["content"])

    def test_runtime_paths_honor_env_overrides(self) -> None:
        bundle = self.bundle()
        config = self.run_driver(bundle, "paths", {"config_home": str(self.temp)})
        self.assertEqual(config, {
            "python": "P", "hook": "H", "memoryHome": "M",
            "configHome": "C", "canonicalRoot": "R",
        })

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout)


class MemoryUpdateTests(unittest.TestCase):
    """`memory_update`: structural creation, pairing, and conflict reporting."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-update-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")
        self.local = self.home / "memory" / "local"

    def call(self, **fields: object) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {
            "hook_event_name": "MemoryUpdate",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        }
        event.update(fields)
        return invoke_pi(self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def create(self, name: str, **fields: object) -> subprocess.CompletedProcess[str]:
        return self.call(operation="create", path=str(self.local), name=name, **fields)

    def test_create_builds_leaf_log_and_index(self) -> None:
        result = self.create("warm-reset", body="# Warm reset\n\nCurrent state.",
                             summary="Warm reset investigation.")
        self.assertEqual(result.returncode, 0, result.stderr)
        leaf = self.local / "nodes" / "warm-reset.md"
        log = self.local / "nodes" / "log" / "warm-reset.md"
        self.assertEqual(leaf.read_text(encoding="utf-8"), "# Warm reset\n\nCurrent state.\n")
        self.assertIn("# Log: warm-reset", log.read_text(encoding="utf-8"))
        index = (self.local / "nodes" / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("[[warm-reset]]", index)
        self.assertIn("Warm reset investigation.", index)
        # The nodes index counterpart is created with the leaf's directory.
        self.assertTrue((self.local / "nodes" / "log" / "MEMORY.md").is_file())

    def test_create_group_scaffolds_the_minimum_shape(self) -> None:
        parent = self.local / "submemory"
        parent.mkdir()
        result = self.call(operation="create", kind="group", path=str(parent), name="kernel",
                           body="# Kernel\n\n**Scope:** kernel work.\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        group = parent / "kernel"
        for relative in ("MEMORY.md", "log/MEMORY.md", "nodes/MEMORY.md", "nodes/log/MEMORY.md"):
            self.assertTrue((group / relative).is_file(), relative)

    def test_set_moves_displaced_state_into_the_log(self) -> None:
        self.create("note", body="first")
        leaf = self.local / "nodes" / "note.md"
        log = self.local / "nodes" / "log" / "note.md"
        result = self.call(operation="set", path=str(leaf), body="second")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(leaf.read_text(encoding="utf-8"), "second\n")
        history = log.read_text(encoding="utf-8")
        self.assertIn("superseded current state", history)
        self.assertIn("first", history)
        self.assertNotIn("No semantic history yet.", history)
        # A mechanical edit must not add a second history entry.
        self.call(operation="set", path=str(leaf), body="second (typo fix)", mechanical=True)
        self.assertEqual(log.read_text(encoding="utf-8"), history)

    def test_set_logs_only_the_lines_it_displaces(self) -> None:
        self.create(
            "note",
            body="# Note\n\n## Alpha\n\n- keep this bullet\n- change this bullet"
                 "\n\n## Beta\n\n- untouched text",
        )
        leaf = self.local / "nodes" / "note.md"
        log = self.local / "nodes" / "log" / "note.md"
        result = self.call(
            operation="set",
            path=str(leaf),
            body="# Note\n\n## Alpha\n\n- keep this bullet\n- changed bullet"
                 "\n\n## Beta\n\n- untouched text",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        history = log.read_text(encoding="utf-8")
        self.assertIn("### Alpha", history)
        self.assertIn("- change this bullet", history)
        self.assertNotIn("- keep this bullet", history)
        self.assertNotIn("untouched text", history)

    def test_set_with_only_additions_leaves_the_log_untouched(self) -> None:
        self.create("note", body="first")
        leaf = self.local / "nodes" / "note.md"
        log = self.local / "nodes" / "log" / "note.md"
        before = log.read_text(encoding="utf-8")
        result = self.call(operation="set", path=str(leaf), body="first\n\nsecond")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text(encoding="utf-8"), before)
        self.assertIn("no displaced state.", self.text(result))

    def test_log_appends_history_without_touching_active(self) -> None:
        self.create("note", body="current")
        leaf = self.local / "nodes" / "note.md"
        result = self.call(operation="log", path=str(leaf), text="an earlier attempt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(leaf.read_text(encoding="utf-8"), "current\n")
        self.assertIn("an earlier attempt",
                      (self.local / "nodes" / "log" / "note.md").read_text(encoding="utf-8"))

    def test_rename_moves_the_log_and_rewrites_inbound_links(self) -> None:
        self.create("old-name", body="body")
        index = self.local / "nodes" / "MEMORY.md"
        index.write_text(index.read_text(encoding="utf-8") + "See [[old-name]] for detail.\n",
                         encoding="utf-8")
        leaf = self.local / "nodes" / "old-name.md"
        result = self.call(operation="rename", path=str(leaf), name="new-name")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(leaf.exists())
        self.assertTrue((self.local / "nodes" / "new-name.md").is_file())
        self.assertTrue((self.local / "nodes" / "log" / "new-name.md").is_file())
        rewritten = index.read_text(encoding="utf-8")
        self.assertIn("[[new-name]]", rewritten)
        self.assertNotIn("[[old-name]]", rewritten)

    def test_retire_removes_active_and_keeps_the_log(self) -> None:
        self.create("gone", body="body")
        leaf = self.local / "nodes" / "gone.md"
        result = self.call(operation="retire", path=str(leaf))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(leaf.exists())
        self.assertTrue((self.local / "nodes" / "log" / "gone.md").is_file())
        self.assertNotIn("[[gone]]", (self.local / "nodes" / "MEMORY.md").read_text(encoding="utf-8"))

    def test_requires_sets_then_clears_frontmatter(self) -> None:
        self.create("alpha", body="alpha")
        self.create("beta", body="beta")
        beta = self.local / "nodes" / "beta.md"
        result = self.call(operation="requires", path=str(beta), requires=["alpha.md"])
        self.assertEqual(result.returncode, 0, result.stderr)
        text = beta.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nrequires_read:\n  - alpha.md\n---\n"))
        self.assertIn("beta", text)
        self.call(operation="requires", path=str(beta), requires=[])
        self.assertEqual(beta.read_text(encoding="utf-8"), "beta\n")

    def test_attach_and_detach_the_leaf_directory(self) -> None:
        self.create("with-files", body="body")
        leaf = self.local / "nodes" / "with-files.md"
        result = self.call(operation="attach", path=str(leaf), attachment_name="repro.sh",
                           attachment_content="#!/bin/sh\necho hi\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        attachment = self.local / "nodes" / "with-files" / "repro.sh"
        self.assertTrue(attachment.is_file())
        detach = self.call(operation="detach", path=str(leaf), attachment_name="repro.sh")
        self.assertEqual(detach.returncode, 0, detach.stderr)
        self.assertFalse(attachment.exists())
        self.assertFalse((self.local / "nodes" / "with-files").exists())

    def test_conflicts_are_reported_and_never_resolved(self) -> None:
        outside = self.call(operation="create", path="/etc", name="nope")
        self.assertEqual(outside.returncode, 3)
        self.assertIn("outside-memory", self.text(outside))

        missing = self.call(operation="requires", path=str(self.local / "absent.md"),
                            requires=["nowhere.md"])
        self.assertEqual(missing.returncode, 3)
        self.assertIn("not-found", self.text(missing))

        self.create("dupe", body="body")
        again = self.create("dupe", body="body")
        self.assertEqual(again.returncode, 3)
        self.assertIn("exists", self.text(again))

        orphan = self.local / "nodes" / "orphan.md"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("orphan\n", encoding="utf-8")
        unpaired = self.call(operation="set", path=str(orphan), body="new")
        self.assertEqual(unpaired.returncode, 3)
        self.assertIn("missing-counterpart", self.text(unpaired))

        bad_requires = self.call(operation="requires", path=str(self.local / "nodes" / "dupe.md"),
                                 requires=["absent-prerequisite.md"])
        self.assertEqual(bad_requires.returncode, 3)
        self.assertIn("missing-prerequisite", self.text(bad_requires))

    def test_subagent_never_writes(self) -> None:
        result = self.call(agent_id="sub-1", operation="create", path=str(self.local),
                           name="nope")
        self.assertEqual(result.returncode, 3)
        self.assertIn("subagent-readonly", self.text(result))
        self.assertFalse((self.local / "nodes" / "nope.md").exists())

    def test_pretool_gate_denies_until_prememory_then_allows(self) -> None:
        leaf = str(self.local / "nodes" / "gate.md")
        def pretool() -> subprocess.CompletedProcess[str]:
            return invoke_pi(self.home, {
                "hook_event_name": "PreToolUse",
                "session_id": "pi-session",
                "turn_id": "turn-1",
                "tool_name": "memory_update",
                "tool_input": {"path": leaf},
                "cwd": str(self.home),
            })
        denied = pretool()
        self.assertEqual(denied.returncode, 0)
        self.assertEqual(self.output(denied)["hookSpecificOutput"]["permissionDecision"], "deny")

        invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        read_conventions(self.home, self.shared)
        allowed = pretool()
        self.assertEqual(allowed.returncode, 0)
        # An allowed call prints nothing; the host treats empty stdout as allow.
        self.assertEqual(allowed.stdout.strip(), "")

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout)


NOTE_MESSAGE = "memory_update create: note\n\nAdds the note node."


class MemoryUpdateCommitTests(unittest.TestCase):
    """`memory_update`: committing and pushing the paths a mutation wrote."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-commit-", dir=SCRATCH_ROOT))
        self.origin = self.temp / "origin.git"
        self.shared = make_git_shared(self.origin, self.temp / "git shared")
        self.home, _ = make_home(self.temp / "git-home", self.shared)
        self.leaf = self.shared / "nodes" / "note.md"

    def call(self, **fields: object) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {
            "hook_event_name": "MemoryUpdate",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        }
        event.update(fields)
        return invoke_pi(self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def create_note(self, body: str = "first", **fields: object) -> subprocess.CompletedProcess[str]:
        fields.setdefault("commit_message", NOTE_MESSAGE)
        return self.call(
            operation="create", path=str(self.shared), name="note",
            body=body, summary="Note.", **fields
        )

    def writer_clone(self) -> Path:
        writer = Path(tempfile.mkdtemp(prefix="writer-", dir=self.temp))
        subprocess.run(["git", "clone", str(self.origin), str(writer)],
                       text=True, capture_output=True, check=False)
        return writer

    def writer_push(self, relative: str, text: str, subject: str) -> None:
        writer = self.writer_clone()
        (writer / relative).write_text(text, encoding="utf-8")
        git_run("add", relative, cwd=writer)
        git_run("commit", "-m", subject, cwd=writer)
        self.assertEqual(git_run("push", "origin", "main", cwd=writer).returncode, 0)

    def test_shared_write_is_committed_and_pushed_narrowly(self) -> None:
        (self.shared / "unrelated.md").write_text("staged elsewhere\n", encoding="utf-8")
        git_run("add", "unrelated.md", cwd=self.shared)
        result = self.create_note()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed", self.text(result))
        self.assertIn("memory_update create: note",
                      git_run("log", "--format=%s", cwd=self.origin).stdout)
        self.assertEqual(
            git_run("log", "-1", "--format=%B", cwd=self.origin).stdout.rstrip("\n"),
            NOTE_MESSAGE,
        )
        files = git_run("show", "--name-only", "--format=", "HEAD", cwd=self.shared).stdout.split()
        self.assertIn("nodes/note.md", files)
        self.assertIn("nodes/log/note.md", files)
        self.assertNotIn("unrelated.md", files)
        self.assertIn("A  unrelated.md",
                      git_run("status", "--porcelain", cwd=self.shared).stdout)

    def test_shared_write_without_a_remote_commits_locally_only(self) -> None:
        git_run("remote", "remove", "origin", cwd=self.shared)
        result = self.create_note()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no upstream", self.text(result))
        self.assertEqual(git_run("rev-list", "--count", "HEAD", cwd=self.shared).stdout.strip(), "2")

    def test_a_racing_push_is_integrated_by_the_pre_mutation_pull(self) -> None:
        self.create_note()
        self.writer_push("MEMORY.md", "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n- Raced.\n", "writer change")
        result = self.call(operation="set", path=str(self.leaf), body="second",
                           commit_message="memory_update set: note\n\nShortens the note.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed", self.text(result))
        self.assertIn("Raced.", (self.shared / "MEMORY.md").read_text(encoding="utf-8"))
        subjects = git_run("log", "--format=%s", cwd=self.origin).stdout
        self.assertIn("writer change", subjects)
        self.assertIn("memory_update set: note", subjects)

    def test_a_racing_push_is_replayed_and_the_note_names_the_replayed_commit(self) -> None:
        self.create_note()
        self.writer_push("MEMORY.md", "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n- Raced.\n", "writer change")
        base = git_run("rev-parse", "HEAD", cwd=self.shared).stdout.strip()
        self.leaf.write_text("second\n", encoding="utf-8")
        publisher = load_hook_module("rm_commit").CommitPublisher(self.shared)
        outcome = publisher.publish([self.leaf], "racing-probe\n\nReplays the commit.")
        self.assertTrue(outcome.pushed)
        self.assertIsNone(outcome.conflict)
        subjects = git_run("log", "--format=%s", cwd=self.origin).stdout
        self.assertIn("writer change", subjects)
        self.assertIn("racing-probe", subjects)
        self.assertNotEqual(outcome.sha, base)
        self.assertEqual(outcome.sha, git_run("rev-parse", "HEAD", cwd=self.origin).stdout.strip())
        self.assertIn(outcome.sha[:12], outcome.note)

    def test_rename_with_an_attachment_dir_commits_both_sides(self) -> None:
        self.create_note()
        self.assertEqual(
            self.call(operation="attach", path=str(self.leaf), attachment_name="big.bin",
                      attachment_content="payload", commit_message=NOTE_MESSAGE).returncode,
            0,
        )
        renamed = self.call(operation="rename", path=str(self.leaf), name="renamed",
                            commit_message="memory_update rename: renamed")
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        tracked = git_run("ls-tree", "-r", "--name-only", "HEAD", cwd=self.origin).stdout.split()
        self.assertIn("nodes/renamed/big.bin", tracked)
        self.assertNotIn("nodes/note/big.bin", tracked)
        self.assertEqual(git_run("status", "--porcelain", cwd=self.shared).stdout, "")

    def test_a_later_create_commits_its_index_edit(self) -> None:
        self.call(operation="create", path=str(self.shared), name="note", body="first",
                  summary="Note.", commit_message=NOTE_MESSAGE)
        second = self.call(operation="create", path=str(self.shared), name="beta",
                           body="beta", summary="Beta.", commit_message="create beta")
        self.assertEqual(second.returncode, 0, second.stderr)
        index = git_run("show", "HEAD:nodes/MEMORY.md", cwd=self.origin).stdout
        self.assertIn("[[beta]]", index)
        self.assertEqual(git_run("status", "--porcelain", cwd=self.shared).stdout, "")

    def test_the_message_is_committed_exactly_as_written(self) -> None:
        written = (
            "subject with a very long unbroken run of words that the tool must not rewrap at "
            "eighty columns\n"
            "\n"
            "Body line one.\n"
            "Body line two.\n"
            "\n"
            "Assisted-by: some-other-agent:model\n"
            "Signed-off-by: Someone Else <other@example.com>"
        )
        result = self.create_note(commit_message=written)
        self.assertEqual(result.returncode, 0, result.stderr)
        message = git_run("log", "-1", "--format=%B", cwd=self.origin).stdout
        self.assertEqual(message.rstrip("\n"), written)
        self.assertEqual(message.count("Assisted-by:"), 1)
        self.assertNotIn("pi:", message)

    def test_a_write_without_a_commit_message_is_left_uncommitted(self) -> None:
        result = self.create_note(commit_message="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("left uncommitted", self.text(result))
        self.assertEqual(git_run("rev-list", "--count", "HEAD", cwd=self.shared).stdout.strip(), "1")
        self.assertEqual(git_run("rev-list", "--count", "main", cwd=self.origin).stdout.strip(), "1")
        self.assertTrue(git_run("status", "--porcelain", cwd=self.shared).stdout.strip())

    def test_sync_target_answers_no_instead_of_raising(self) -> None:
        module = load_hook_module("rm_git")
        worktree = module.GitWorktree(self.shared)
        with mock.patch.object(module.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 5)):
            self.assertFalse(worktree.has_sync_target())

    def test_a_diverging_write_leaves_the_commit_local_and_reports_it(self) -> None:
        self.create_note()
        self.writer_push("nodes/note.md", "writer body\n", "writer edit")
        # Reproduce the race window directly: the shared clone is behind, and the
        # mutation is written and committed before the racing push is noticed.
        self.leaf.write_text("second\n", encoding="utf-8")
        publisher = load_hook_module("rm_commit").CommitPublisher(self.shared)
        outcome = publisher.publish([self.leaf], "divergence-probe\n\nKept local.")
        self.assertTrue(outcome.committed)
        self.assertFalse(outcome.pushed)
        self.assertEqual(outcome.conflict["code"], "shared-diverged")
        self.assertIn("divergence-probe",
                      git_run("log", "--format=%s", "HEAD", cwd=self.shared).stdout)
        self.assertIn("writer edit", git_run("log", "--format=%s", cwd=self.origin).stdout)
        self.assertNotIn("divergence-probe",
                         git_run("log", "--format=%s", cwd=self.origin).stdout)
        self.assertFalse((self.shared / ".git" / "rebase-merge").exists())
        self.assertEqual(self.leaf.read_text(encoding="utf-8"), "second\n")

    def test_pre_memory_skips_the_pull_when_the_shared_has_no_remote(self) -> None:
        git_run("remote", "remove", "origin", cwd=self.shared)
        result = invoke_pi(self.home, {
            "hook_event_name": "PreMemory",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre_memory", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_a_shared_worktree_without_git_keeps_the_manual_note(self) -> None:
        plain = self.temp / "plain shared"
        plain.mkdir()
        (plain / "MEMORY.md").write_text(
            "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n",
            encoding="utf-8",
        )
        home, _ = make_home(self.temp / "plain-home", plain)
        result = invoke_pi(home, {
            "hook_event_name": "MemoryUpdate",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "operation": "create",
            "path": str(plain),
            "name": "note",
            "body": "first",
            "summary": "Note.",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not Git-backed", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])


class MemoryUpdateCommitOpTests(unittest.TestCase):
    """`memory_update commit`: publishing leaves the agent edited by hand."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-commit-op-", dir=SCRATCH_ROOT))
        self.origin = self.temp / "origin.git"
        self.shared = make_git_shared(self.origin, self.temp / "git shared")
        self.home, _ = make_home(self.temp / "git-home", self.shared)
        self.leaf = self.shared / "nodes" / "note.md"
        self.log = self.shared / "nodes" / "log" / "note.md"

    def call(self, **fields: object) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {
            "hook_event_name": "MemoryUpdate",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        }
        event.update(fields)
        return invoke_pi(self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def create_note(self, body: str = "first", **fields: object) -> subprocess.CompletedProcess[str]:
        fields.setdefault("commit_message", NOTE_MESSAGE)
        return self.call(
            operation="create", path=str(self.shared), name="note",
            body=body, summary="Note.", **fields
        )

    def test_a_hand_edit_moves_displaced_lines_into_the_log_and_publishes(self) -> None:
        self.create_note(body="alpha\nbeta\ngamma")
        self.leaf.write_text("alpha\ngamma\ndelta\n", encoding="utf-8")
        message = "commit: hand edit\n\nMoves the dropped line into the log."
        result = self.call(operation="commit", commit_message=message)
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("beta", logged)
        self.assertIn("superseded current state", logged)
        self.assertNotIn("delta", logged)
        stored = git_run("log", "-1", "--format=%B", cwd=self.origin).stdout
        self.assertEqual(stored.rstrip("\n"), message)
        files = git_run("show", "--name-only", "--format=", "HEAD", cwd=self.origin).stdout
        self.assertIn("nodes/note.md", files)
        self.assertIn("nodes/log/note.md", files)

    def test_a_clean_set_reports_nothing_to_commit(self) -> None:
        self.create_note()
        result = self.call(operation="commit", commit_message="commit: nothing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing-to-commit", self.text(result))

    def test_a_missing_commit_message_is_a_bad_request(self) -> None:
        self.create_note()
        self.leaf.write_text("edited\n", encoding="utf-8")
        result = self.call(operation="commit", path=str(self.leaf))
        self.assertEqual(result.returncode, 3)
        self.assertIn("bad-request", self.text(result))
        self.assertIn("supply the commit message you wrote", self.text(result))

    def test_a_dirty_leaf_without_a_log_is_a_missing_counterpart(self) -> None:
        self.create_note()
        self.log.unlink()
        self.leaf.write_text("edited\n", encoding="utf-8")
        result = self.call(operation="commit", path=str(self.leaf), commit_message="commit: note")
        self.assertEqual(result.returncode, 3)
        self.assertIn("missing-counterpart", self.text(result))

    def test_a_non_git_memory_tree_is_reported_as_not_git(self) -> None:
        orphan = self.home / "memory" / "local" / "nodes" / "orphan.md"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("orphan\n", encoding="utf-8")
        result = self.call(operation="commit", path=str(orphan), commit_message="commit: orphan")
        self.assertEqual(result.returncode, 3)
        self.assertIn("not-git", self.text(result))

    def test_a_dirty_non_memory_file_stays_unstaged(self) -> None:
        self.create_note()
        unrelated = self.shared / "unrelated.md"
        unrelated.write_text("outside\n", encoding="utf-8")
        self.leaf.write_text("edited\n", encoding="utf-8")
        result = self.call(
            operation="commit", path=str(self.leaf), commit_message="commit: note only"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        files = git_run("show", "--name-only", "--format=", "HEAD", cwd=self.shared).stdout
        self.assertIn("nodes/note.md", files)
        self.assertNotIn("unrelated.md", files)
        self.assertIn("?? unrelated.md",
                      git_run("status", "--porcelain", cwd=self.shared).stdout)

    def test_a_named_path_narrows_to_one_leaf(self) -> None:
        self.create_note()
        self.call(operation="create", path=str(self.shared), name="beta", body="beta",
                  summary="Beta.", commit_message="create beta")
        beta = self.shared / "nodes" / "beta.md"
        self.leaf.write_text("note edited\n", encoding="utf-8")
        beta.write_text("beta edited\n", encoding="utf-8")
        result = self.call(
            operation="commit", path=str(self.leaf),
            commit_message="commit: note only\n\nNarrows to one leaf.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git_run("show", "HEAD:nodes/note.md", cwd=self.origin).stdout,
                         "note edited\n")
        self.assertEqual(git_run("show", "HEAD:nodes/beta.md", cwd=self.origin).stdout, "beta\n")
        self.assertIn("nodes/beta.md",
                      git_run("status", "--porcelain", cwd=self.shared).stdout)

    def test_a_no_remote_worktree_commits_locally_and_reports_it(self) -> None:
        self.create_note()
        git_run("remote", "remove", "origin", cwd=self.shared)
        self.leaf.write_text("local only\n", encoding="utf-8")
        result = self.call(operation="commit", commit_message="commit: local\n\nNo remote.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no upstream", self.text(result))
        self.assertEqual(git_run("show", "HEAD:nodes/note.md", cwd=self.shared).stdout,
                         "local only\n")

    def test_a_dirty_log_suppresses_the_automatic_append(self) -> None:
        self.create_note()
        self.log.write_text("# Log: note\n\n## 2026-01-01 - manual\n\nby hand\n",
                            encoding="utf-8")
        self.leaf.write_text("rewritten\n", encoding="utf-8")
        result = self.call(operation="commit", commit_message="commit: manual log")
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("by hand", logged)
        self.assertNotIn("superseded current state", logged)

    def test_an_unbreakable_long_line_is_stored_byte_for_byte(self) -> None:
        self.create_note()
        self.leaf.write_text("edited\n", encoding="utf-8")
        message = "commit: note\n\n" + "x" * 120
        result = self.call(operation="commit", commit_message=message)
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = git_run("log", "-1", "--format=%B", cwd=self.origin).stdout
        self.assertEqual(stored.rstrip("\n"), message)


class MemoryUpdateAttestationTests(unittest.TestCase):
    """`memory_update`: a publish reports its own double-check."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-attest-", dir=SCRATCH_ROOT))
        self.origin = self.temp / "origin.git"
        self.shared = make_git_shared(self.origin, self.temp / "git shared")
        self.home, _ = make_home(self.temp / "git-home", self.shared)
        self.leaf = self.shared / "nodes" / "note.md"
        self.log = self.shared / "nodes" / "log" / "note.md"

    def call(self, **fields: object) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {
            "hook_event_name": "MemoryUpdate",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        }
        event.update(fields)
        return invoke_pi(self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def create_note(self, body: str = "first", **fields: object) -> subprocess.CompletedProcess[str]:
        fields.setdefault("commit_message", NOTE_MESSAGE)
        return self.call(
            operation="create", path=str(self.shared), name="note",
            body=body, summary="Note.", **fields
        )

    def head(self) -> str:
        return git_run("rev-parse", "HEAD", cwd=self.shared).stdout.strip()

    def test_a_shared_set_publish_names_clean_head_and_upstream(self) -> None:
        self.create_note(body="alpha\nbeta")
        result = self.call(
            operation="set", path=str(self.leaf), body="alpha\ngamma",
            commit_message="memory_update set: note\n\nDrops beta.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.text(result)
        sha = self.head()
        self.assertEqual(sha, git_run("rev-parse", "origin/main", cwd=self.shared).stdout.strip())
        self.assertIn(
            f"verified: {self.shared} clean, HEAD {sha[:12]} == origin/main", body
        )
        self.assertIn(sha[:12], body)

    def test_a_commit_reports_the_paired_log_carries_the_appended_lines(self) -> None:
        self.create_note(body="alpha\nbeta\ngamma")
        self.leaf.write_text("alpha\ngamma\ndelta\n", encoding="utf-8")
        result = self.call(
            operation="commit",
            commit_message="commit: hand edit\n\nMoves the dropped line into the log.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified: paired log carries the appended lines", self.text(result))

    def test_a_no_remote_publish_reports_no_upstream_to_compare(self) -> None:
        git_run("remote", "remove", "origin", cwd=self.shared)
        result = self.create_note()
        self.assertEqual(result.returncode, 0, result.stderr)
        sha = self.head()
        self.assertIn(
            f"verified: {self.shared} clean, HEAD {sha[:12]}; no upstream to compare",
            self.text(result),
        )

    def test_a_subject_only_message_is_advised_not_failed(self) -> None:
        result = self.create_note(commit_message="commit: note")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "attention: message has no body (subject only)", self.text(result)
        )

    def test_unit_a_dirty_target_is_an_unverified_dirty_conflict(self) -> None:
        self.create_note()
        self.leaf.write_text("dirty\n", encoding="utf-8")
        report = load_hook_module("rm_verify").PublishAttestation(self.shared).attest(
            sha=self.head(), changed=[self.leaf], appended=[]
        )
        self.assertEqual([item["code"] for item in report.conflicts], ["unverified-dirty"])
        self.assertIn("nodes/note.md", report.conflicts[0]["message"])

    def test_unit_an_unpushed_target_is_an_unverified_unpushed_conflict(self) -> None:
        self.create_note()
        self.leaf.write_text("local only\n", encoding="utf-8")
        git_run("add", "nodes/note.md", cwd=self.shared)
        git_run(
            "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "local",
            cwd=self.shared,
        )
        report = load_hook_module("rm_verify").PublishAttestation(self.shared).attest(
            sha=self.head(), changed=[self.leaf], appended=[]
        )
        self.assertEqual([item["code"] for item in report.conflicts], ["unverified-unpushed"])
        self.assertIn("origin/main", report.conflicts[0]["message"])

    def test_unit_a_missing_appended_line_is_an_unverified_log_conflict(self) -> None:
        self.create_note()
        report = load_hook_module("rm_verify").PublishAttestation(self.shared).attest(
            sha=self.head(),
            changed=[self.leaf],
            appended=[(self.log, "a line that was never committed\n")],
        )
        self.assertEqual([item["code"] for item in report.conflicts], ["unverified-log"])
        self.assertIn("nodes/log/note.md", report.conflicts[0]["message"])


if __name__ == "__main__":
    unittest.main()
