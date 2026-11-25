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
        str(Path.home() / ".tmp" / "agent-mem-struct-tests"),
    )
)
TRAILER = (
    "Assisted-by: LLM\n"
    "Signed-off-by: Edrick Sinsuan <evcsinsuan@gmail.com>"
)
BAD_LINE = "word " * 30
MULTILINE_SUBJECT_MESSAGE = (
    "The 16 fixed sections are a quarter to two fifths of the host chain once the\n"
    "band shaper is in it, so a core-length kernel removes 16-26% of the per-sample\n"
    "cost although it is 2.4-2.7x cheaper than the sections. The DAR entry also\n"
    "carries a per-rate parallel bank beside the FIR, which neither a kernel nor\n"
    "the shipped sections model.\n"
    "\n"
    "Assisted-by: LLM\n"
    "Signed-off-by: Edrick Sinsuan <evcsinsuan@gmail.com>"
)
MULTILINE_SUBJECT_FINDING = (
    "the subject paragraph has 5 lines; expected one subject line followed by "
    "a blank line"
)


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
    (path / "RULES.md").write_text(
        "# Memory rules\n\n1. **Keep continuity.** Read the agent root header first.\n",
        encoding="utf-8",
    )
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


def update_preview(result: subprocess.CompletedProcess[str]) -> dict | None:
    """The plan a refused, unacknowledged memory update hands back."""
    try:
        text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    start = text.find("```json\n")
    if start < 0:
        return None
    start += len("```json\n")
    end = text.find("\n```", start)
    if end < 0:
        return None
    try:
        preview = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    return preview if isinstance(preview, dict) else None


def audited_call(
    agent: str, home: Path, event: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    """Submit a memory update the way an agent must: audit, then acknowledge."""
    event = dict(event)
    event.setdefault("session_id", "pi-session")
    event.setdefault("turn_id", "turn-1")
    first = invoke(agent, home, event)
    if update_preview(first) is None:
        return first
    return invoke(agent, home, {**event, "conventions_audited": True})


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

    def check(self, message: str) -> list[str]:
        return self.lint.violations(message)

    def test_clean_message_has_no_violations(self) -> None:
        self.assertEqual(
            self.check(f"subject line\n\nA body paragraph.\n\n{TRAILER}"), []
        )

    def test_the_column_limit_needs_a_breakable_token_to_flag(self) -> None:
        for label, line, flagged in (
            ("a breakable line over the limit", BAD_LINE, True),
            (
                "an unbreakable URL over the limit",
                "https://example.com/" + "a" * 100,
                False,
            ),
        ):
            with self.subTest(label):
                findings = self.check(f"subject\n\n{line}")
                self.assertEqual(
                    any("cols with no unbreakable token" in item for item in findings),
                    flagged,
                    label,
                )

    def test_the_body_limit_counts_nonblank_body_lines_only(self) -> None:
        over = [
            "the body is 11 nonblank lines before the trailer block, "
            "over 10"
        ]
        ten = "\n".join(f"body line {number}" for number in range(1, 11))
        eleven = "\n".join(f"body line {number}" for number in range(1, 12))
        spaced = "\n\n".join(f"body line {number}" for number in range(1, 11))
        references = "\n".join(f"Reference: {number}" for number in range(1, 13))
        for label, message, expected in (
            ("ten body lines", f"subject\n\n{ten}\n\n{TRAILER}", []),
            ("eleven body lines", f"subject\n\n{eleven}\n\n{TRAILER}", over),
            (
                "ten body lines separated by blank lines",
                f"subject\n\n{spaced}\n\n{TRAILER}",
                [],
            ),
            (
                "twelve reference trailers after one body line",
                f"subject\n\nbody\n\n{references}\n{TRAILER}",
                [],
            ),
            ("eleven body lines and no trailer block", f"subject\n\n{eleven}", over),
        ):
            with self.subTest(label):
                self.assertEqual(self.check(message), expected, label)

    def test_facts_measure_the_same_message_the_lint_judges(self) -> None:
        facts = self.lint.facts(f"subject\n\none\n\ntwo\n\n{TRAILER}")
        self.assertEqual(facts["subject"], "subject")
        self.assertEqual(facts["subject_columns"], len("subject"))
        self.assertEqual(facts["subject_limit"], 80)
        self.assertEqual(facts["body_nonblank_lines"], 2)
        self.assertEqual(facts["body_line_numbers"], [3, 5])
        self.assertEqual(facts["body_limit"], 10)
        self.assertEqual(
            facts["trailers"], TRAILER.split("\n")
        )
        self.assertEqual(facts["trailer_block_unresolved"], [])

    def test_facts_leave_the_length_unstated_without_git(self) -> None:
        from rm_lint import MessageLint

        facts = MessageLint().facts(f"subject\n\nbody\n\n{TRAILER}")
        self.assertIsNone(facts["body_nonblank_lines"])
        self.assertTrue(facts["trailer_block_unresolved"])

    def test_the_trailer_block_needs_exactly_one_blank_line_before_it(self) -> None:
        for label, message, fragment in (
            (
                "no blank line before it",
                "subject\n\nbody\nSigned-off-by: y",
                "exactly one blank line",
            ),
            (
                "two blank lines before it",
                "subject\n\nbody\n\n\nSigned-off-by: y",
                "exactly one blank line",
            ),
            (
                "a blank line between trailer lines",
                "subject\n\nSigned-off-by: y\n\n" + TRAILER,
                "sits between two trailer-like lines",
            ),
        ):
            with self.subTest(label):
                findings = self.check(message)
                self.assertTrue(
                    any(fragment in item for item in findings), label
                )

    def test_trailers_must_match_interpret_trailers(self) -> None:
        findings = self.check("subject\n\nAssisted-by:pi:m\nSigned-off-by: y")
        self.assertTrue(any("is not written as" in item for item in findings))

    def test_a_token_the_policy_does_not_allow_is_flagged(self) -> None:
        findings = self.check(f"subject\n\nLooks-good: yes\n{TRAILER}")
        self.assertTrue(any("policy does not allow" in item for item in findings))
        self.assertTrue(any("Looks-good" in item for item in findings))

    def test_prose_that_looks_like_a_trailer_is_not_one(self) -> None:
        self.assertEqual(
            self.check(
                "subject\n\nNote: this is body prose, not a trailer.\n\n" + TRAILER
            ),
            [],
        )

    def test_a_repeated_trailer_resolves_to_the_final_block(self) -> None:
        findings = self.check(
            "subject\n\ntext\n\nReference: identical\n\nReference: identical\n"
            + TRAILER
        )
        self.assertEqual(
            findings,
            [
                "blank line 6 sits between two trailer-like lines",
                "trailer-like line 5 is outside the final trailer block",
            ],
        )

    def test_a_repository_trailer_alias_cannot_change_the_verdict(self) -> None:
        from rm_git import GitWorktree
        from rm_lint import MessageLint

        repo = Path(tempfile.mkdtemp(prefix="ams-alias-", dir=SCRATCH_ROOT))
        git("init", "-b", "main", cwd=repo)
        git("config", "trailer.probe.key", "Signed-off-by", cwd=repo)
        message = f"subject\n\nprobe: Example\n{TRAILER}"
        aliased = MessageLint(GitWorktree(repo)).violations(message)
        self.assertEqual(aliased, self.check(message))
        self.assertTrue(any("probe:" in item for item in aliased))

    def test_a_global_trailer_alias_cannot_change_the_verdict(self) -> None:
        config = SCRATCH_ROOT / "global-trailer-config"
        config.write_text("[trailer \"probe\"]\n\tkey = Signed-off-by\n", encoding="utf-8")
        message = f"subject\n\nprobe: Example\n{TRAILER}"
        baseline = self.check(message)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.assertEqual(self.check(message), baseline)
        self.assertTrue(any("probe:" in item for item in baseline))

    def test_an_inherited_git_dir_cannot_change_the_verdict(self) -> None:
        repo = Path(tempfile.mkdtemp(prefix="ams-gitdir-", dir=SCRATCH_ROOT))
        git("init", "-b", "main", cwd=repo)
        git("config", "trailer.probe.key", "Signed-off-by", cwd=repo)
        message = f"subject\n\nprobe: Example\n{TRAILER}"
        baseline = self.check(message)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(repo / ".git")}):
            self.assertEqual(self.check(message), baseline)
        self.assertTrue(any("probe:" in item for item in baseline))

    def test_a_repository_above_the_parser_cannot_change_the_verdict(self) -> None:
        from rm_git import GitWorktree
        from rm_lint import MessageLint

        repo = Path(tempfile.mkdtemp(prefix="ams-above-", dir=SCRATCH_ROOT))
        git("init", "-b", "main", cwd=repo)
        git("config", "trailer.probe.key", "Signed-off-by", cwd=repo)
        scratch = repo / "scratch"
        scratch.mkdir()
        message = f"subject\n\nprobe: Example\n{TRAILER}"
        aliased = MessageLint(
            GitWorktree(REPO), scratch_root=scratch
        ).violations(message)
        self.assertEqual(aliased, self.check(message))
        self.assertTrue(any("probe:" in item for item in aliased))

    def test_an_unresolved_trailer_block_leaves_the_length_unstated_and_refuses(
        self,
    ) -> None:
        from rm_lint import MessageLint

        message = f"subject\n\n{TRAILER}"
        with self.subTest("no git to parse the block"):
            without_git = MessageLint()
            self.assertIsNone(without_git.facts(message)["body_nonblank_lines"])
            self.assertTrue(
                any(
                    "trailer validation is unavailable" in item
                    for item in without_git.violations(message)
                )
            )
        unparsed = "subject\n\nbody\n\nAssisted-by: LLM"
        with self.subTest("a block git does not parse"):
            with mock.patch.object(self.lint, "parsed_trailers", return_value=None):
                facts = self.lint.facts(unparsed)
                findings = self.check(unparsed)
            self.assertIsNone(facts["body_nonblank_lines"])
            self.assertTrue(
                any("trailer validation is unavailable" in item for item in findings)
            )

    def test_a_trailerless_body_is_counted_without_git(self) -> None:
        from rm_lint import MessageLint

        body = "\n".join(f"body line {number}" for number in range(1, 12))
        lint = MessageLint()
        self.assertEqual(lint.facts(f"subject\n\n{body}")["body_nonblank_lines"], 11)
        self.assertEqual(
            lint.violations(f"subject\n\n{body}"),
            [
                "the body is 11 nonblank lines before the trailer block, "
                "over 10"
            ],
        )

    def test_a_trailer_run_git_does_not_accept_is_malformed(self) -> None:
        from rm_lint import TRAILER_MALFORMED

        # One trailer beside one non-trailer line is not a block to git: the
        # run parses alone but not as part of the message.
        message = "subject\n\nbody\nAssisted-by: LLM"
        self.assertIsNone(self.lint.facts(message)["body_nonblank_lines"])
        self.assertIn(TRAILER_MALFORMED, self.check(message))

    def test_a_subject_only_message_passes(self) -> None:
        self.assertEqual(self.check("the subject alone"), [])

    def test_a_multiline_subject_paragraph_is_flagged_and_measured(self) -> None:
        self.assertIn(
            MULTILINE_SUBJECT_FINDING, self.check(MULTILINE_SUBJECT_MESSAGE)
        )
        facts = self.lint.facts(MULTILINE_SUBJECT_MESSAGE)
        self.assertEqual(facts["subject_columns"], 334)
        self.assertEqual(facts["subject_lines"], 5)
        self.assertFalse(facts["subject_single_line"])

    def test_a_short_multiline_subject_is_also_refused(self) -> None:
        self.assertIn(
            "the subject paragraph has 2 lines; "
            "expected one subject line followed by a blank line",
            self.check("short\ncontinuation"),
        )

    def test_subject_shape_preserves_accepted_messages(self) -> None:
        for message in (
            "the subject alone",
            "the subject alone\n",
            f"subject\n\nA body paragraph.\n\n{TRAILER}",
            f"subject\n\n{TRAILER}",
            "subject\n \t\nA body paragraph.",
        ):
            with self.subTest(message=message):
                self.assertEqual(self.check(message), [])
                facts = self.lint.facts(message)
                self.assertEqual(facts["subject_lines"], 1)
                self.assertTrue(facts["subject_single_line"])

    def test_an_absent_subject_has_no_paragraph(self) -> None:
        for message in ("", "\nbody", " \t\nbody"):
            with self.subTest(message=message):
                self.assertIn("the message states no subject line", self.check(message))
                facts = self.lint.facts(message)
                self.assertEqual(facts["subject"], "")
                self.assertEqual(facts["subject_columns"], 0)
                self.assertEqual(facts["subject_lines"], 0)
                self.assertFalse(facts["subject_single_line"])

    def test_a_message_without_a_subject_is_flagged(self) -> None:
        findings = self.check("\n\nbody only\n\nAssisted-by: LLM")
        self.assertIn("the message states no subject line", findings)
        self.assertIn("the message states no subject line", self.check(""))

    def test_a_trailer_line_outside_the_final_block_is_flagged(self) -> None:
        for label, message, outside in (
            (
                "the attribution block above more text",
                f"subject\n\n{TRAILER}\n\nmore text",
                True,
            ),
            (
                "a reference trailer above the block",
                "subject\n\nReference: https://github.com/example/project/tree/abc123"
                "\n\n" + TRAILER,
                True,
            ),
        ):
            with self.subTest(label):
                findings = self.check(message)
                self.assertEqual(
                    any("outside the final trailer block" in item for item in findings),
                    outside,
                    label,
                )

    def test_source_and_reference_trailers_in_the_final_block_pass(self) -> None:
        message = (
            "subject\n\nA body paragraph.\n\n"
            "Source: https://github.com/example/project/commit/abc123\n"
            "Reference: https://github.com/example/project/tree/abc123\n"
            "Reference: https://github.com/other/project/tree/def456\n"
            f"{TRAILER}"
        )
        self.assertEqual(self.check(message), [])

    def test_an_indented_line_above_the_trailer_block_is_flagged(self) -> None:
        findings = self.check("subject\n\n  indented body\n\nSigned-off-by: y")
        self.assertTrue(any("starts with whitespace" in item for item in findings))

    def test_a_printable_escape_is_flagged_and_ff_passes(self) -> None:
        self.assertTrue(
            any("escape of printable ASCII" in item for item in self.check(r"subject \x41"))
        )
        self.assertEqual(self.check(r"subject \xff"), [])

    def test_only_the_literal_llm_attribution_passes(self) -> None:
        # A session may be served by a virtual routing model or by any tier of a
        # failover chain, so a model id states something no trailer can verify.
        for label, value, flagged in (
            ("the literal `LLM`", "LLM", False),
            ("a virtual routing model", "pi:glm-5.3-flash", True),
            ("a codex model", "codex:gpt-5.4", True),
            ("a tier suffix", "LLM:extra", True),
            ("lower case", "llm", True),
        ):
            with self.subTest(label):
                findings = self.check(f"subject\n\nAssisted-by: {value}")
                self.assertEqual(
                    any("expected 'LLM'" in item for item in findings),
                    flagged,
                    label,
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
        report = PushLint(bare).check("", [])
        self.assertEqual(report.blocked, [])
        self.assertIn("remote could not be resolved", report.incomplete)

    def test_the_commit_cap_is_incomplete_not_a_block(self) -> None:
        import rm_lint

        commit_message(self.clone, "first\n\nbody")
        commit_message(self.clone, "second\n\nbody")
        with mock.patch.object(rm_lint, "PUSH_MAX_COMMITS", 1):
            report = rm_lint.PushLint(self.clone).check(
                "origin", ["main"]
            )
        self.assertEqual(report.blocked, [])
        self.assertIn("more than 1 commits", report.incomplete)

    def test_only_the_local_user_email_is_linted(self) -> None:
        from rm_lint import PushLint

        commit_message(self.clone, f"subject\n\n{BAD_LINE}", email="other@example.com")
        commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        report = PushLint(self.clone).check("origin", ["main"])
        self.assertEqual(len(report.blocked), 1)
        self.assertIn("cols with no unbreakable token", report.blocked[0])


class PushGateTests(unittest.TestCase):
    """The hook denies a push that would publish a non-conforming commit."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-pushgate-", dir=SCRATCH_ROOT))
        self.home, self.shared = make_home(self.temp / "home")
        self.clone = make_clone(self.temp)
        self.env = dict(os.environ)
        open_gate("pi", self.home, self.shared, environment=self.env)

    def push(
        self, command: str, *, cwd: Path | None = None, escape: bool = False
    ) -> subprocess.CompletedProcess[str]:
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(cwd or self.clone),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if escape:
            event["pi_gate_escape"] = True
        return invoke("pi", self.home, event, environment=self.env)

    def decision(self, result: subprocess.CompletedProcess[str]) -> dict:
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)["hookSpecificOutput"]

    def test_an_eleven_line_body_blocks_the_push(self) -> None:
        from rm_lint import PushLint

        body = "\n".join(f"body line {number}" for number in range(1, 12))
        sha = commit_message(self.clone, f"subject\n\n{body}")
        report = PushLint(self.clone).check("origin", ["main"])
        self.assertTrue(
            any(
                sha[:12] in item and "over 10" in item for item in report.blocked
            ),
            report.blocked,
        )

    def test_a_bad_commit_blocks_every_push_form(self) -> None:
        sha = commit_message(self.clone, f"subject\n\n{BAD_LINE}")
        with self.subTest("a plain push, with and without the gate escape"):
            for escape in (False, True):
                body = self.decision(self.push("git push origin main", escape=escape))
                self.assertEqual(
                    body.get("permissionDecision"), "deny", f"escape={escape}"
                )
                reason = body["permissionDecisionReason"]
                self.assertIn("message-lint: push blocked:", reason)
                self.assertIn(sha[:12], reason)
                self.assertIn("reword with one -F payload", reason)
        for label, command, cwd in (
            ("a `-C` push", f"git -C {self.clone} push origin main", self.home),
            ("a bare push", "git push", None),
            ("a bare `-C` push", f"git -C {self.clone} push", self.home),
            ("a chained push", "git push origin main && git status", None),
            (
                "a `cd` then a push",
                f"cd {self.clone} && git push origin HEAD:main",
                self.home,
            ),
        ):
            with self.subTest(label):
                result = self.push(command, cwd=cwd)
                reason = self.decision(result)["permissionDecisionReason"]
                self.assertIn("message-lint: push blocked:", reason, label)
                self.assertIn(sha[:12], reason, label)

    def test_a_push_in_the_call_that_creates_its_commits_is_denied(self) -> None:
        commit_message(self.clone, "subject\n\nA clean body.")
        result = self.push(
            "git commit -q --allow-empty -m 'another: change' && git push origin main"
        )
        body = self.decision(result)
        self.assertEqual(body.get("permissionDecision"), "deny")
        self.assertIn("creates its commits", body["permissionDecisionReason"])

    def test_a_push_that_publishes_no_nonconforming_commit_is_allowed(self) -> None:
        # The rows run in order on one clone: each push publishes only the
        # commit that row makes, so "nothing ahead" holds only when it is first.
        for label, message, email in (
            ("nothing ahead of the remote", None, None),
            ("one clean commit", "subject\n\nA conforming body.", None),
            (
                "a foreign author's nonconforming commit",
                f"subject\n\n{BAD_LINE}",
                "other@example.com",
            ),
        ):
            with self.subTest(label):
                if message is not None:
                    commit_message(self.clone, message, email=email)
                self.assertEqual(
                    self.push("git push origin main").stdout.strip(), "", label
                )

    def test_an_unresolvable_push_is_annotated(self) -> None:
        bare = self.temp / "bare-no-remote"
        bare.mkdir()
        git("init", "-b", "main", cwd=bare)
        result = self.push("git push", cwd=bare)
        body = self.decision(result)
        self.assertNotIn("permissionDecision", body)
        self.assertIn("push not fully linted:", body.get("additionalContext", ""))

    def test_a_model_naming_attribution_is_denied(self) -> None:
        commit_message(self.clone, "subject\n\nAssisted-by: pi:some-other-model")
        result = self.push("git push origin main")
        self.assertIn("expected 'LLM'", self.decision(result)["permissionDecisionReason"])


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
        return audited_call("pi", self.home, event)

    def text(self, result: subprocess.CompletedProcess[str]) -> str:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_a_refusal_leaves_every_planned_path_untouched(self) -> None:
        """The refusal lands before the mutation writes, not after it commits."""
        self.call(
            operation="create", path=str(self.shared), name="note", body="first",
            summary="Note.", commit_message="memory_update create: note\n\nAdds the note.",
        )
        before = {
            path: path.read_bytes()
            for path in (self.leaf, self.shared / "nodes" / "log" / "note.md",
                         self.shared / "nodes" / "MEMORY.md")
            if path.exists()
        }
        body = "\n".join(f"finding {number} on its own line" for number in range(1, 12))
        result = self.call(
            operation="set", path=str(self.leaf),
            body="# Note\nrewritten\n",
            commit_message=f"set: note\n\n{body}",
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("message-lint", self.text(result))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, f"{path} was written")

    def test_a_multiline_subject_paragraph_is_refused_before_write(self) -> None:
        created = self.call(
            operation="create",
            path=str(self.shared),
            name="note",
            body="first",
            summary="Note.",
            commit_message=f"Add a note\n\nRecord the current state.\n\n{TRAILER}",
        )
        self.assertEqual(created.returncode, 0, self.text(created))
        nodes = self.shared / "nodes"
        before = {
            path.relative_to(nodes): path.read_bytes()
            for path in nodes.rglob("*")
            if path.is_file()
        }
        head = git("rev-parse", "HEAD", cwd=self.shared).stdout.strip()
        result = self.call(
            operation="set",
            path=str(self.leaf),
            body="# Note\nrewritten\n",
            commit_message=MULTILINE_SUBJECT_MESSAGE,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("message-lint", self.text(result))
        self.assertIn(MULTILINE_SUBJECT_FINDING, self.text(result))
        preview = update_preview(result)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertEqual(preview["commit_message"], MULTILINE_SUBJECT_MESSAGE)
        facts = preview["message_facts"]
        self.assertEqual(facts["subject_columns"], 334)
        self.assertEqual(facts["subject_lines"], 5)
        self.assertFalse(facts["subject_single_line"])
        self.assertEqual(
            {
                path.relative_to(nodes): path.read_bytes()
                for path in nodes.rglob("*")
                if path.is_file()
            },
            before,
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.shared).stdout.strip(), head)

    def test_a_message_is_measured_only_where_publication_commits_it(self) -> None:
        """A plan that publishes nothing runs no executable to measure a message."""
        body = "\n".join(f"finding {number} on its own line" for number in range(1, 13))
        tip = git("rev-parse", "HEAD", cwd=self.shared).stdout.strip()
        result = self.call(
            operation="create",
            path=str(self.home / "memory" / "local"),
            name="local-note",
            body="first",
            summary="Note.",
            commit_message=f"create: local-note\n\n{body}",
        )
        self.assertEqual(result.returncode, 0, self.text(result))
        self.assertTrue((self.home / "memory" / "local" / "nodes" / "local-note.md").exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.shared).stdout.strip(), tip)

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

    def test_an_overlong_body_is_refused_at_assessment_with_its_own_numbers(self) -> None:
        """The message is measured in the assessment, before anything is written."""
        self.call(
            operation="create", path=str(self.shared), name="note", body="first",
            summary="Note.", commit_message="memory_update create: note\n\nAdds the note.",
        )
        body = "\n".join(f"finding {number} on its own line" for number in range(1, 14))
        message = f"commit: note\n\n{body}\n\nAssisted-by: LLM\nSigned-off-by: x"
        self.leaf.write_text("edited\n", encoding="utf-8")
        head = git("rev-parse", "HEAD", cwd=self.shared).stdout.strip()
        result = self.call(
            operation="commit", path=str(self.leaf), commit_message=message
        )
        self.assertEqual(result.returncode, 3)
        text = self.text(result)
        self.assertIn("the body is 13 nonblank lines before the trailer block", text)
        preview = update_preview(result)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertEqual(preview["commit_message"], message)
        self.assertEqual(preview["message_facts"]["body_nonblank_lines"], 13)
        self.assertEqual(preview["message_facts"]["body_limit"], 10)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.shared).stdout.strip(), head)


class MessageReviewTests(unittest.TestCase):
    """`CommitPublisher` publishes the reviewed message, or nothing."""

    def setUp(self) -> None:
        from rm_commit import CommitPublisher

        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-msgreview-", dir=SCRATCH_ROOT))
        self.clone = make_clone(self.temp)
        self.publisher = CommitPublisher(self.clone)
        self.file = self.clone / "f.txt"

    def commit(self, message: str) -> str:
        self.file.write_text(message, encoding="utf-8")
        return commit_message(self.clone, message)

    def test_the_reviewed_message_publishes(self) -> None:
        self.file.write_text("second\n", encoding="utf-8")
        outcome = self.publisher.publish(
            [self.file], "reviewed\n\nA body line.\n\nAssisted-by: LLM"
        )
        self.assertTrue(outcome.pushed)
        self.assertIsNone(outcome.conflict)

    def test_a_message_git_would_clean_is_published_as_written(self) -> None:
        """The commit stores the reviewed bytes, not git's cleanup of them."""
        self.file.write_text("second\n", encoding="utf-8")
        message = "reviewed \n\nA body line.\n\n\nAnother line.\n\nAssisted-by: LLM"
        outcome = self.publisher.publish([self.file], message)
        self.assertTrue(outcome.pushed, outcome.conflict)
        stored = git("log", "-1", "--format=%B", cwd=self.clone).stdout
        self.assertEqual(stored.rstrip("\n"), message)

    def test_a_stored_message_that_differs_is_not_pushed(self) -> None:
        reviewed = "subject\n\nA body line."
        sha = self.commit(reviewed)
        for label, stored, candidate, refused in (
            (
                "a message rewritten at review",
                None,
                "reviewed\n\nReviewed body.",
                True,
            ),
            ("leading whitespace", b"\n" + reviewed.encode("utf-8") + b"\n", reviewed, True),
            ("a trailing newline", None, reviewed + "\n", False),
        ):
            with self.subTest(label):
                if stored is None:
                    moved = self.publisher.message_moved(sha, candidate)
                else:
                    with mock.patch.object(
                        self.publisher, "stored_message", return_value=stored
                    ):
                        moved = self.publisher.message_moved(sha, candidate)
                self.assertEqual(
                    "does not carry the reviewed message" in moved, refused, label
                )

    def test_an_empty_review_is_refused(self) -> None:
        sha = self.commit("stored\n\nStored body.")
        self.assertEqual(
            self.publisher.message_moved(sha, ""), "the reviewed message is missing"
        )
        self.assertEqual(
            self.publisher.message_moved(sha, "   "),
            "the reviewed message is missing",
        )

    def test_an_unreadable_stored_message_is_refused(self) -> None:
        sha = self.commit("stored\n\nStored body.")
        with mock.patch.object(self.publisher, "stored_message", return_value=None):
            moved = self.publisher.message_moved(sha, "stored\n\nStored body.")
        self.assertIn("could not be read back", moved)

    def test_a_stored_message_that_breaks_the_convention_is_refused(self) -> None:
        body = "\n".join(f"line {number}" for number in range(1, 12))
        message = f"subject\n\n{body}"
        sha = commit_message(self.clone, message)
        moved = self.publisher.message_moved(sha, message)
        self.assertIn("breaks the mechanical convention", moved)

    def raw_message(self) -> bytes:
        """The message bytes in the commit object itself, without any conversion."""
        object_id = git("rev-parse", "HEAD", cwd=self.clone).stdout.strip()
        raw = subprocess.run(
            ["git", "-C", str(self.clone), "cat-file", "commit", object_id],
            capture_output=True,
            check=False,
        ).stdout
        return raw.partition(b"\n\n")[2]

    def test_the_stored_message_keeps_lf_bytes_on_every_host(self) -> None:
        """A message written here is stored with the bytes that were reviewed."""
        self.file.write_text("second\n", encoding="utf-8")
        message = f"reviewed subject\n\nA body line.\n\n{TRAILER}"
        outcome = self.publisher.publish([self.file], message)
        self.assertTrue(outcome.pushed, outcome.conflict)
        stored = self.raw_message()
        self.assertEqual(stored, message.encode("utf-8") + b"\n")
        self.assertNotIn(b"\r", stored)

    def test_a_crlf_stored_message_is_not_pushed(self) -> None:
        """A message stored with CRLF is not the reviewed one, byte for byte."""
        reviewed = "subject\n\nA body line."
        sha = self.commit(reviewed)
        with mock.patch.object(
            self.publisher,
            "stored_message",
            return_value=reviewed.encode("utf-8").replace(b"\n", b"\r\n") + b"\r\n",
        ):
            moved = self.publisher.message_moved(sha, reviewed)
        self.assertIn("does not carry the reviewed message", moved)

    def test_a_stored_message_without_its_final_newline_is_refused(self) -> None:
        reviewed = "subject\n\nA body line."
        sha = self.commit(reviewed)
        with mock.patch.object(
            self.publisher, "stored_message", return_value=reviewed.encode("utf-8")
        ):
            moved = self.publisher.message_moved(sha, reviewed)
        self.assertIn("does not carry the reviewed message", moved)

    def test_a_multiline_subject_paragraph_is_not_committed(self) -> None:
        self.file.write_text("second\n", encoding="utf-8")
        head = git("rev-parse", "HEAD", cwd=self.clone).stdout.strip()
        outcome = self.publisher.publish([self.file], MULTILINE_SUBJECT_MESSAGE)
        self.assertFalse(outcome.committed)
        self.assertFalse(outcome.pushed)
        self.assertIsNotNone(outcome.conflict)
        assert outcome.conflict is not None
        self.assertEqual(outcome.conflict["code"], "message-lint")
        self.assertIn(MULTILINE_SUBJECT_FINDING, outcome.conflict["message"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.clone).stdout.strip(), head)

    def test_a_stored_multiline_subject_paragraph_is_refused(self) -> None:
        sha = commit_message(self.clone, MULTILINE_SUBJECT_MESSAGE)
        moved = self.publisher.message_moved(sha, MULTILINE_SUBJECT_MESSAGE)
        self.assertIn("the stored message breaks the mechanical convention", moved)
        self.assertIn(MULTILINE_SUBJECT_FINDING, moved)

    def test_only_a_commit_object_carries_a_stored_message(self) -> None:
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.clone).stdout.strip()
        self.assertIsNone(self.publisher.stored_message(tree))
        self.assertIsNone(self.publisher.stored_message("0" * 40))


if __name__ == "__main__":
    unittest.main()
