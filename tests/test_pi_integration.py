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

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"
PI_MANAGER = REPO / "hooks" / "pi" / "manage.py"
EXTENSION_TEMPLATE = REPO / "hooks" / "pi" / "root-memory-extension.ts"


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


def make_home(path: Path) -> tuple[Path, Path]:
    shared = make_shared(path.parent / f"{path.name} shared memory")
    (path / "memory").mkdir(parents=True)
    (path / "memory" / "MEMORY.md").write_text(
        f"Structure-Version: test-v1\nStructure: ../STRUCTURE.md\nShared: {shared}\n\n# Root\n",
        encoding="utf-8",
    )
    (path / "RULES.md").write_text("# Rules\n\nKeep continuity.\n", encoding="utf-8")
    (path / "STRUCTURE.md").write_text("Structure-Version: test-v1\n", encoding="utf-8")
    return path, shared


def invoke_pi(home: Path, event: dict[str, object], *, canonical_root: Path | None = None,
              config_home: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(HOOK), "--agent", "pi", "--home", str(home)]
    command.extend(("--canonical-root", str(canonical_root or home)))
    if config_home is not None:
        command.extend(("--config-home", str(config_home)))
    return subprocess.run(
        command, input=json.dumps(event), text=True, capture_output=True, check=False,
    )


class PiHookTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pi-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout)

    def test_session_start_injects_root_context_with_turn_identity(self) -> None:
        result = invoke_pi(self.home, {
            "hook_event_name": "SessionStart",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "source": "startup",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.output(result)["hookSpecificOutput"]
        self.assertEqual(body["hookEventName"], "SessionStart")
        self.assertIn("Structure-Version: test-v1", body["additionalContext"])
        self.assertIn("Keep continuity.", body["additionalContext"])
        # The turn reminder names Pi's own memory boundary.
        reminder = invoke_pi(self.home, {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "pi-session",
            "turn_id": "turn-2",
        })
        self.assertIn(
            "Pi keeps no native memory store",
            self.output(reminder)["hookSpecificOutput"]["additionalContext"],
        )

    def test_turn_reminder_requires_a_verified_pull_and_a_settlement_record(self) -> None:
        reminder = invoke_pi(self.home, {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "pi-session",
            "turn_id": "turn-1",
        })
        context = self.output(reminder)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("git pull --ff-only", context)
        self.assertIn("Before settling", context)

    def test_convention_gate_denies_once_then_acknowledges(self) -> None:
        target = self.shared / "MEMORY.md"
        first = invoke_pi(self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "tool_name": "write",
            "cwd": str(self.temp),
            "tool_input": {"path": str(target), "content": "x"},
        })
        self.assertEqual(first.returncode, 0, first.stderr)
        denial = self.output(first)["hookSpecificOutput"]
        self.assertEqual(denial["permissionDecision"], "deny")
        self.assertIn("## Mandatory conventions", denial["permissionDecisionReason"])
        second = invoke_pi(self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "pi-session",
            "turn_id": "turn-1",
            "tool_name": "write",
            "cwd": str(self.temp),
            "tool_input": {"path": str(target), "content": "x"},
        })
        # An allowed call prints nothing; the host treats empty stdout as allow.
        self.assertEqual(second.stdout.strip(), "")

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


if __name__ == "__main__":
    unittest.main()
