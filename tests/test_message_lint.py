#!/usr/bin/env python3
"""Commit-message lint and the `git push` gate for the root-memory hook."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
TRAILER = (
    "Assisted-by: pi:deepseek/deepseek-v4.1-flash\n"
    "Signed-off-by: Edrick Sinsuan <evcsinsuan@gmail.com>"
)
BAD_LINE = "word " * 30


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


def make_git_shared(origin: Path, shared: Path) -> Path:
    origin.mkdir(parents=True)
    git("init", "--bare", "-b", "main", cwd=origin)
    seed = origin.parent / f"{origin.name}-seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    (seed / "MEMORY.md").write_text(
        "# Shared\n\n**Scope:** *\n\n## Mandatory conventions\n\n- Verify first.\n",
        encoding="utf-8",
    )
    git("add", "MEMORY.md", cwd=seed)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-u", "origin", "main", cwd=seed)
    git("clone", str(origin), str(shared), cwd=shared.parent)
    return shared.resolve()


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
    if agent == "pi":
        invoke(agent, home, {
            "hook_event_name": "PreMemory",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }, environment=environment)
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


def make_clone(base: Path) -> Path:
    """A clone of a fresh bare origin, ready to be pushed from."""
    origin = base / "clone-origin.git"
    origin.mkdir(parents=True)
    git("init", "--bare", "-b", "main", cwd=origin)
    seed = base / "clone-seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    (seed / "f.txt").write_text("seed\n", encoding="utf-8")
    git("add", "f.txt", cwd=seed)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-u", "origin", "main", cwd=seed)
    clone = base / "clone"
    git("clone", str(origin), str(clone), cwd=base)
    git("config", "user.email", "author@example.com", cwd=clone)
    git("config", "user.name", "Author", cwd=clone)
    return clone


def commit_message(repo: Path, message: str, *, email: str | None = None) -> str:
    file = repo / "message.txt"
    file.write_text(message + "\n", encoding="utf-8")
    args = ["commit", "--allow-empty", "-F", "message.txt"]
    if email is not None:
        args = ["-c", f"user.email={email}", "-c", "user.name=Other", *args]
    git(*args, cwd=repo)
    file.unlink()
    return git("rev-parse", "HEAD", cwd=repo).stdout.strip()


class MessageLintUnitTests(unittest.TestCase):
    """`rm_lint.MessageLint`: each mechanical check in isolation."""

    def setUp(self) -> None:
        from rm_git import GitWorktree
        from rm_lint import MessageLint

        self.lint = MessageLint(GitWorktree(REPO))
        self.model = "deepseek/deepseek-v4.1-flash"

    def check(self, message: str, *, agent: str = "pi", model: str | None = None) -> list[str]:
        return self.lint.violations(
            message, agent=agent, model=self.model if model is None else model
        )

    def test_clean_message_has_no_violations(self) -> None:
        self.assertEqual(
            self.check(f"subject line\n\nA body paragraph.\n\n{TRAILER}"), []
        )

    def test_a_long_breakable_line_is_flagged(self) -> None:
        findings = self.check(f"subject\n\n{BAD_LINE}")
        self.assertTrue(any("cols with no unbreakable token" in item for item in findings))

    def test_a_long_unbreakable_url_line_passes(self) -> None:
        self.assertEqual(
            self.check("subject\n\nhttps://example.com/" + "a" * 100), []
        )

    def test_a_missing_blank_line_before_the_trailer_block_is_flagged(self) -> None:
        findings = self.check("subject\n\nbody\nSigned-off-by: y")
        self.assertTrue(any("exactly one blank line" in item for item in findings))

    def test_two_blank_lines_before_the_trailer_block_are_flagged(self) -> None:
        findings = self.check("subject\n\nbody\n\n\nSigned-off-by: y")
        self.assertTrue(any("exactly one blank line" in item for item in findings))

    def test_a_blank_line_between_trailer_lines_is_flagged(self) -> None:
        findings = self.check("subject\n\nSigned-off-by: y\n\n" + TRAILER)
        self.assertTrue(any("sits between two trailer-like lines" in item for item in findings))

    def test_trailers_must_match_interpret_trailers(self) -> None:
        findings = self.check("subject\n\nAssisted-by:pi:m\nSigned-off-by: y")
        self.assertTrue(any("git interpret-trailers" in item for item in findings))

    def test_a_trailer_line_outside_the_final_block_is_flagged(self) -> None:
        findings = self.check(f"subject\n\n{TRAILER}\n\nmore text")
        self.assertTrue(any("outside the final trailer block" in item for item in findings))

    def test_an_indented_line_above_the_trailer_block_is_flagged(self) -> None:
        findings = self.check("subject\n\n  indented body\n\nSigned-off-by: y")
        self.assertTrue(any("starts with whitespace" in item for item in findings))

    def test_a_printable_escape_is_flagged_and_ff_passes(self) -> None:
        self.assertTrue(
            any("escape of printable ASCII" in item for item in self.check(r"subject \x41"))
        )
        self.assertEqual(self.check(r"subject \xff"), [])

    def test_a_foreign_or_llm_attribution_passes(self) -> None:
        self.assertEqual(self.check(f"subject\n\nAssisted-by: LLM"), [])
        self.assertEqual(self.check(f"subject\n\nAssisted-by: codex:{self.model}"), [])

    def test_the_agents_own_model_must_match(self) -> None:
        self.assertEqual(
            self.check(f"subject\n\nAssisted-by: pi:{self.model}"), []
        )
        findings = self.check("subject\n\nAssisted-by: pi:another-model")
        self.assertTrue(any("not PI_MODEL" in item for item in findings))

    def test_the_model_check_is_skipped_when_unset(self) -> None:
        self.assertEqual(
            self.check("subject\n\nAssisted-by: pi:another-model", model=""), []
        )


class PushLintUnitTests(unittest.TestCase):
    """`rm_lint.PushLint`: the bounded read-only scan."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pushlint-", dir=SCRATCH_ROOT))
        self.clone = make_clone(self.temp)

    def test_unresolvable_remote_is_incomplete_not_a_block(self) -> None:
        from rm_lint import PushLint

        bare = self.temp / "no-remote"
        bare.mkdir()
        git("init", "-b", "main", cwd=bare)
        report = PushLint(bare, agent="pi", model="").check("", [])
        self.assertEqual(report.blocked, [])
        self.assertIn("remote could not be resolved", report.incomplete)

    def test_the_commit_cap_is_incomplete_not_a_block(self) -> None:
        import rm_lint

        commit_message(self.clone, "first\n\nbody")
        commit_message(self.clone, "second\n\nbody")
        with mock.patch.object(rm_lint, "PUSH_MAX_COMMITS", 1):
            report = rm_lint.PushLint(self.clone, agent="pi", model="").check(
                "origin", ["main"]
            )
        self.assertEqual(report.blocked, [])
        self.assertIn("more than 1 commits", report.incomplete)

    def test_only_the_local_user_email_is_linted(self) -> None:
        from rm_lint import PushLint

        commit_message(self.clone, f"subject\n\n{BAD_LINE}", email="other@example.com")
        commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        report = PushLint(self.clone, agent="pi", model="").check("origin", ["main"])
        self.assertEqual(len(report.blocked), 1)
        self.assertIn("cols with no unbreakable token", report.blocked[0])


class PushGateTests(unittest.TestCase):
    """The hook denies a push that would publish a non-conforming commit."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pushgate-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")
        self.clone = make_clone(self.temp)
        self.env = dict(os.environ, PI_MODEL="")
        open_gate("pi", self.home, self.shared, environment=self.env)

    def push(
        self, command: str, *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return invoke("pi", self.home, {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(cwd or self.clone),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }, environment=self.env)

    def decision(self, result: subprocess.CompletedProcess[str]) -> dict:
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)["hookSpecificOutput"]

    def test_a_bad_commit_blocks_a_plain_push(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        result = self.push("git push origin main")
        body = self.decision(result)
        self.assertEqual(body.get("permissionDecision"), "deny")
        reason = body["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)
        self.assertIn("reword with one -F payload", reason)

    def test_a_bad_commit_blocks_a_dash_c_push(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        result = self.push(f"git -C {self.clone} push origin main", cwd=self.home)
        reason = self.decision(result)["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)

    def test_a_bad_commit_blocks_a_bare_push(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        reason = self.decision(self.push("git push"))["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)

    def test_a_bad_commit_blocks_a_bare_dash_c_push(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        result = self.push(f"git -C {self.clone} push", cwd=self.home)
        reason = self.decision(result)["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)

    def test_a_chained_push_is_still_linted(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        result = self.push("git push origin main && git status")
        reason = self.decision(result)["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)

    def test_a_bad_commit_blocks_a_cd_then_push(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        result = self.push(
            f"cd {self.clone} && git push origin HEAD:main", cwd=self.home
        )
        reason = self.decision(result)["permissionDecisionReason"]
        self.assertIn("message-lint: push blocked:", reason)
        self.assertIn(sha[:12], reason)

    def test_a_foreign_author_bad_commit_is_allowed(self) -> None:
        commit_message(self.clone, f"subject\n\n{BAD_LINE}", email="other@example.com")
        result = self.push("git push origin main")
        self.assertEqual(result.stdout.strip(), "")

    def test_a_clean_commit_is_allowed(self) -> None:
        commit_message(self.clone, "subject\n\nA conforming body.")
        result = self.push("git push origin main")
        self.assertEqual(result.stdout.strip(), "")

    def test_nothing_ahead_is_allowed(self) -> None:
        result = self.push("git push origin main")
        self.assertEqual(result.stdout.strip(), "")

    def test_an_unresolvable_push_is_annotated(self) -> None:
        bare = self.temp / "bare-no-remote"
        bare.mkdir()
        git("init", "-b", "main", cwd=bare)
        result = self.push("git push", cwd=bare)
        body = self.decision(result)
        self.assertNotIn("permissionDecision", body)
        self.assertIn("push not fully linted:", body.get("additionalContext", ""))

    def test_wrong_model_passes_when_pi_model_is_unset(self) -> None:
        commit_message(self.clone, "subject\n\nAssisted-by: pi:some-other-model")
        result = self.push("git push origin main")
        self.assertEqual(result.stdout.strip(), "")


class MemoryCommitLintTests(unittest.TestCase):
    """`memory_update commit` refuses a message that breaks the convention."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-memlint-", dir=SCRATCH_ROOT))
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
        return invoke("pi", self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_a_bad_commit_message_is_blocked_without_committing(self) -> None:
        self.call(
            operation="create", path=str(self.shared), name="note", body="first",
            summary="Note.", commit_message="memory_update create: note\n\nAdds the note.",
        )
        head = git("rev-parse", "HEAD", cwd=self.shared).stdout.strip()
        self.leaf.write_text("edited\n", encoding="utf-8")
        result = self.call(
            operation="commit", path=str(self.leaf),
            commit_message=f"commit: note\n\n{BAD_LINE}",
        )
        self.assertEqual(result.returncode, 3)
        body = self.text(result)
        self.assertIn("message-lint", body)
        self.assertIn("rewrite commit_message and re-call", body)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.shared).stdout.strip(), head)


if __name__ == "__main__":
    unittest.main()
