#!/usr/bin/env python3
"""The private touched-repo ledger and the session-start leftovers report."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
SCRATCH_ROOT = Path(
    os.environ.get(
        "AGENT_MEM_STRUCT_TEST_TMP",
        str(Path.home() / "tmp" / "agent-mem-struct-tests"),
    )
)

from rm_ledger import LEDGER_NAME, RepoLedger  # noqa: E402
from rm_leftovers import LeftoverReport  # noqa: E402


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False
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
        shared = make_shared(path.parent / f"{path.name} shared")
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


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git("init", "-b", "main", cwd=path)
    git("config", "user.email", "t@t", cwd=path)
    git("config", "user.name", "t", cwd=path)
    (path / "f.txt").write_text("seed\n", encoding="utf-8")
    git("add", "f.txt", cwd=path)
    git("commit", "-m", "seed", cwd=path)
    return path


def add_origin(repo: Path, origin: Path) -> None:
    origin.mkdir(parents=True)
    git("init", "--bare", "-b", "main", cwd=origin)
    git("remote", "add", "origin", str(origin), cwd=repo)
    git("push", "-u", "origin", "main", cwd=repo)


def invoke(
    agent: str,
    home: Path,
    event: dict[str, object],
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable, str(HOOK), "--agent", agent, "--home", str(home),
        "--canonical-root", str(home),
    ]
    return subprocess.run(
        command,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def open_gate(
    agent: str, home: Path, shared: Path, *, environment: dict[str, str] | None = None
) -> None:
    paths = (
        home / "memory" / "MEMORY.md",
        home / "RULES.md",
        home / "memory" / "local" / "MEMORY.md",
        shared / "MEMORY.md",
    )
    for path in paths:
        invoke(agent, home, {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(home),
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
        }, environment=environment)
    invoke(agent, home, {
        "hook_event_name": "PreToolUse",
        "session_id": "session-1",
        "cwd": str(home),
        "tool_name": "Bash",
        "tool_input": {"command": f'find "{shared}" -print'},
    }, environment=environment)


class RepoLedgerUnitTests(unittest.TestCase):
    """`rm_ledger.RepoLedger`: bounded, expiring, prunable private state."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-ledger-", dir=SCRATCH_ROOT))
        self.home = self.temp / "home"
        self.home.mkdir()
        self.state = types.SimpleNamespace(home=self.home)

    def test_record_and_entries_replace_one_path(self) -> None:
        ledger = RepoLedger(self.state)
        repo = self.temp / "repo"
        repo.mkdir()
        ledger.record(repo)
        ledger.record(repo)
        entries = ledger.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], str(repo.resolve()))

    def test_the_ledger_keeps_at_most_fifty_entries(self) -> None:
        ledger = RepoLedger(self.state)
        for index in range(55):
            path = self.temp / f"repo-{index}"
            path.mkdir()
            ledger.record(path)
        self.assertEqual(len(ledger.entries()), 50)

    def test_an_expired_entry_is_dropped(self) -> None:
        ledger = RepoLedger(self.state)
        path = ledger.path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps([{"path": str(self.temp / "gone"), "last_seen": 1}]) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(ledger.entries(), [])

    def test_prune_drops_only_the_named_paths(self) -> None:
        ledger = RepoLedger(self.state)
        keep = self.temp / "keep"
        drop = self.temp / "drop"
        keep.mkdir()
        drop.mkdir()
        ledger.record(keep)
        ledger.record(drop)
        ledger.prune([drop])
        self.assertEqual([entry["path"] for entry in ledger.entries()], [str(keep.resolve())])

    def test_the_state_file_is_private(self) -> None:
        ledger = RepoLedger(self.state)
        repo = self.temp / "repo"
        repo.mkdir()
        ledger.record(repo)
        self.assertEqual(oct(ledger.path().stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(ledger.path().parent.stat().st_mode & 0o777), "0o700")
        self.assertEqual(ledger.path().name, LEDGER_NAME)


class LeftoverReportUnitTests(unittest.TestCase):
    """`rm_leftovers.LeftoverReport`: the read-only scan and its budget."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-leftover-", dir=SCRATCH_ROOT))
        self.home = self.temp / "home"
        self.home.mkdir()
        self.scratch = self.temp / "scratch"
        self.scratch.mkdir()
        self.repo = make_repo(self.temp / "repo")
        self.state = types.SimpleNamespace(home=self.home)

    def report(self, *, budget: float = 2.0) -> LeftoverReport:
        return LeftoverReport(scratch_root=self.scratch, budget=budget)

    def test_an_empty_ledger_and_scratch_produce_no_report(self) -> None:
        self.assertEqual(self.report().text(RepoLedger(self.state)), "")

    def test_an_unpushed_commit_is_counted_with_its_sha(self) -> None:
        ledger = RepoLedger(self.state)
        ledger.record(self.repo)
        text = self.report().text(ledger)
        sha = git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()[:12]
        self.assertIn("1 local-only commit not on any remote", text)
        self.assertIn(sha, text)
        self.assertIn(str(self.repo), text)

    def test_a_clean_repo_is_pruned_silently(self) -> None:
        add_origin(self.repo, self.temp / "origin.git")
        ledger = RepoLedger(self.state)
        ledger.record(self.repo)
        self.assertEqual(self.report().text(ledger), "")
        self.assertEqual(ledger.entries(), [])

    def test_a_deleted_repo_is_pruned_silently(self) -> None:
        ledger = RepoLedger(self.state)
        ledger.record(self.repo)
        shutil.rmtree(self.repo)
        self.assertEqual(self.report().text(ledger), "")
        self.assertEqual(ledger.entries(), [])

    def test_a_stale_scratch_child_is_listed_and_a_fresh_one_is_not(self) -> None:
        stale = self.scratch / "stale-dir"
        fresh = self.scratch / "fresh-dir"
        stale.mkdir()
        fresh.mkdir()
        old = time.time() - 2 * 24 * 60 * 60
        os.utime(stale, (old, old))
        text = self.report().text(RepoLedger(self.state))
        self.assertIn(str(stale), text)
        self.assertIn("2 days old", text)
        self.assertNotIn(str(fresh), text)
        self.assertIn("never delete another agent's directory", text)

    def test_an_exhausted_budget_reports_incomplete(self) -> None:
        ledger = RepoLedger(self.state)
        ledger.record(self.repo)
        text = self.report(budget=0.0).text(ledger)
        self.assertIn("- leftover check incomplete:", text)


class LeftoverHookTests(unittest.TestCase):
    """The hook records a history-changing command and reports at session start."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-leftover-hook-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")
        self.scratch = self.temp / "scratch"
        self.scratch.mkdir()
        self.env = dict(os.environ, AMS_SCRATCH_ROOT=str(self.scratch))
        self.repo = make_repo(self.temp / "repo")
        self.ledger = self.home / ".agent-mem-struct" / LEDGER_NAME

    def record_commit(self) -> subprocess.CompletedProcess[str]:
        open_gate("codex", self.home, self.shared, environment=self.env)
        return invoke("codex", self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(self.home),
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {self.repo} commit --allow-empty -m x"},
        }, environment=self.env)

    def session_start(self, source: str = "startup") -> str:
        result = invoke("codex", self.home, {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
            "cwd": str(self.temp),
            "source": source,
        }, environment=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_a_pretool_git_commit_records_the_repository(self) -> None:
        result = self.record_commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.repo.resolve()), self.ledger.read_text(encoding="utf-8"))

    def test_a_chained_commit_records_the_repository(self) -> None:
        open_gate("codex", self.home, self.shared, environment=self.env)
        result = invoke("codex", self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(self.home),
            "tool_name": "Bash",
            "tool_input": {
                "command": f"cd {self.repo} && git add -A && git commit --allow-empty -m x"
            },
        }, environment=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.repo.resolve()), self.ledger.read_text(encoding="utf-8"))

    def test_an_unpushed_commit_is_reported_at_session_start(self) -> None:
        self.record_commit()
        sha = git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()[:12]
        context = self.session_start()
        self.assertIn("LEFTOVERS from earlier sessions", context)
        self.assertIn("1 local-only commit", context)
        self.assertIn(sha, context)

    def test_a_push_clears_the_report_and_prunes_the_entry(self) -> None:
        self.record_commit()
        add_origin(self.repo, self.temp / "origin.git")
        context = self.session_start()
        self.assertNotIn("LEFTOVERS", context)
        self.assertNotIn(str(self.repo.resolve()), self.ledger.read_text(encoding="utf-8"))

    def test_a_compact_session_start_has_no_report(self) -> None:
        self.record_commit()
        self.assertNotIn("LEFTOVERS", self.session_start(source="compact"))

    def test_a_stale_scratch_child_is_reported_at_session_start(self) -> None:
        stale = self.scratch / "stale-dir"
        stale.mkdir()
        old = time.time() - 2 * 24 * 60 * 60
        os.utime(stale, (old, old))
        context = self.session_start()
        self.assertIn(str(stale), context)
        self.assertIn("2 days old", context)


if __name__ == "__main__":
    unittest.main()
