#!/usr/bin/env python3
"""`CommitPublisher.stageable`: a deletion is publishable when it was tracked."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from rm_commit import CommitOutcome  # noqa: E402
from test_message_lint import SCRATCH_ROOT, git, make_clone  # noqa: E402

MESSAGE = (
    "Remove a tracked path\n\n"
    "Keep the tree free of the file nothing reads.\n\n"
    "Assisted-by: LLM\n"
    "Signed-off-by: Edrick Sinsuan <evcsinsuan@gmail.com>"
)


def load_publisher(root: Path):
    from rm_commit import CommitPublisher

    return CommitPublisher(root)


class StageableTests(unittest.TestCase):
    """Which of a mutation's paths the publisher may commit."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-deletion-", dir=SCRATCH_ROOT))
        self.clone = make_clone(self.temp)
        self.publisher = load_publisher(self.clone)

    def stageable(self, *paths: Path) -> list[str]:
        answer = self.publisher.stageable(list(paths))
        if isinstance(answer, CommitOutcome):
            self.fail(f"stageable refused: {answer.conflict}")
        return answer

    def test_a_staged_deletion_is_stageable(self) -> None:
        git("rm", "f.txt", cwd=self.clone)
        self.assertEqual(self.stageable(self.clone / "f.txt"), ["f.txt"])

    def test_a_worktree_deletion_is_stageable(self) -> None:
        (self.clone / "f.txt").unlink()
        self.assertEqual(self.stageable(self.clone / "f.txt"), ["f.txt"])

    def test_an_untracked_absent_path_is_not_stageable(self) -> None:
        self.assertEqual(self.stageable(self.clone / "absent.txt"), [])

    def test_a_directory_with_a_staged_deletion_beneath_it_is_stageable(self) -> None:
        (self.clone / "dir").mkdir()
        (self.clone / "dir" / "leaf.md").write_text("x\n", encoding="utf-8")
        git("add", "dir/leaf.md", cwd=self.clone)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "leaf",
            cwd=self.clone)
        git("push", "origin", "main", cwd=self.clone)
        git("rm", "-r", "dir", cwd=self.clone)
        self.assertEqual(self.stageable(self.clone / "dir"), ["dir"])

    def test_a_failed_index_query_is_reported_not_read_as_empty(self) -> None:
        refused = git  # keep the helper importable in this module's namespace
        del refused
        real = self.publisher.worktree.run

        def answer(*args: str, **kwargs):
            if args and args[0] == "ls-files":
                return mock.Mock(returncode=128, stdout="", stderr="fatal: nope")
            return real(*args, **kwargs)

        with mock.patch.object(self.publisher.worktree, "run", answer):
            outcome = self.publisher.stageable([self.clone / "f.txt"])
        self.assertIsInstance(outcome, CommitOutcome)
        self.assertEqual(outcome.conflict["code"], "git-failed")
        self.assertIn("indexed shared paths", outcome.conflict["message"])

    def test_a_failed_committed_query_is_reported(self) -> None:
        real = self.publisher.worktree.run

        def answer(*args: str, **kwargs):
            if args and args[0] == "ls-tree":
                return mock.Mock(returncode=128, stdout="", stderr="fatal: bad object")
            return real(*args, **kwargs)

        with mock.patch.object(self.publisher.worktree, "run", answer):
            outcome = self.publisher.stageable([self.clone / "f.txt"])
        self.assertIsInstance(outcome, CommitOutcome)
        self.assertEqual(outcome.conflict["code"], "git-failed")
        self.assertIn("committed shared paths", outcome.conflict["message"])

    def test_a_failed_head_query_is_reported(self) -> None:
        with mock.patch.object(self.publisher, "revision", return_value=(False, None)):
            outcome = self.publisher.stageable([self.clone / "f.txt"])
        self.assertIsInstance(outcome, CommitOutcome)
        self.assertEqual(outcome.conflict["code"], "git-failed")
        self.assertIn("HEAD", outcome.conflict["message"])


class DeletionPublicationTests(unittest.TestCase):
    """The whole publish path for a removal, committed and pushed."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="ams-delpub-", dir=SCRATCH_ROOT))
        self.clone = make_clone(self.temp)
        self.publisher = load_publisher(self.clone)

    def test_a_staged_deletion_is_committed_and_pushed(self) -> None:
        git("rm", "f.txt", cwd=self.clone)
        outcome = self.publisher.publish([self.clone / "f.txt"], MESSAGE)
        self.assertIsNone(outcome.conflict)
        self.assertTrue(outcome.committed)
        self.assertTrue(outcome.pushed)
        paths = git("show", "--name-status", "--format=", "HEAD", cwd=self.clone).stdout
        self.assertEqual(paths.split("\n")[0], "D\tf.txt")
        published = git(
            "ls-tree", "-r", "--name-only", "origin/main", cwd=self.clone
        ).stdout.split()
        self.assertNotIn("f.txt", published)

    def test_a_mixed_mutation_commits_only_its_own_paths(self) -> None:
        (self.clone / "kept.txt").write_text("kept\n", encoding="utf-8")
        (self.clone / "other.txt").write_text("other\n", encoding="utf-8")
        git("add", "kept.txt", "other.txt", cwd=self.clone)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "both",
            cwd=self.clone)
        git("push", "origin", "main", cwd=self.clone)
        (self.clone / "kept.txt").write_text("changed\n", encoding="utf-8")
        git("rm", "other.txt", cwd=self.clone)
        outcome = self.publisher.publish(
            [self.clone / "kept.txt", self.clone / "other.txt"], MESSAGE
        )
        self.assertIsNone(outcome.conflict)
        paths = git("show", "--name-status", "--format=", "HEAD", cwd=self.clone).stdout
        self.assertEqual(sorted(paths.split("\n")[:2]), ["D\tother.txt", "M\tkept.txt"])

    def test_an_unrelated_staged_change_stays_out(self) -> None:
        (self.clone / "unrelated.txt").write_text("u\n", encoding="utf-8")
        git("add", "unrelated.txt", cwd=self.clone)
        git("rm", "f.txt", cwd=self.clone)
        outcome = self.publisher.publish([self.clone / "f.txt"], MESSAGE)
        self.assertIsNone(outcome.conflict)
        paths = git("show", "--name-only", "--format=", "HEAD", cwd=self.clone).stdout
        self.assertEqual(paths.split(), ["f.txt"])
        still_staged = git("diff", "--cached", "--name-only", cwd=self.clone).stdout
        self.assertEqual(still_staged.split(), ["unrelated.txt"])

    def test_an_unborn_branch_publishes_an_added_file(self) -> None:
        fresh = self.temp / "unborn"
        fresh.mkdir()
        git("init", "-b", "main", cwd=fresh)
        git("config", "user.email", "author@example.com", cwd=fresh)
        git("config", "user.name", "Author", cwd=fresh)
        (fresh / "added.md").write_text("x\n", encoding="utf-8")
        outcome = load_publisher(fresh).publish([fresh / "added.md"], MESSAGE)
        self.assertIsNone(outcome.conflict)
        self.assertTrue(outcome.committed)
        paths = git("show", "--name-only", "--format=", "HEAD", cwd=fresh).stdout
        self.assertEqual(paths.split(), ["added.md"])


if __name__ == "__main__":
    unittest.main()
