"""Run bounded, non-interactive git commands in one worktree.

One owner for invoking git on behalf of the root-memory hook: `GitWorktree`
pins the credential prompt off, applies a budget to every call, and turns a
timed-out or missing git into a failed result instead of an exception, so a
hook always reaches a report. The shared fast-forward, the unmerged-index
check, and publishing a mutation all go through this class rather than
spelling out their own subprocess call.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Mapping

DEFAULT_BUDGET = 20.0
QUERY_BUDGET = 5.0


class GitWorktree:
    """One worktree's git invocations, bounded and never interactive.

    `window` lets an owner that has its own deadline supply the budget for
    every call it makes, evaluated per call so a sequence of calls shares
    one window rather than each taking a fresh one.
    """

    def __init__(
        self, root: Path, budget: float = DEFAULT_BUDGET, window: Callable[[], float] | None = None
    ) -> None:
        self.root = root
        self.budget = budget
        self.window = window

    def limit(self, budget: float | None = None, default: float | None = None) -> float:
        """The seconds one call may take: explicit, else this owner's window.

        `default` caps a query when no window applies, so a caller without a
        deadline of its own still bound its cheap checks.
        """
        if budget is not None:
            return budget
        if self.window is not None:
            return max(0.5, self.window())
        return self.budget if default is None else default

    def run(
        self,
        *args: str,
        input_text: str | None = None,
        budget: float | None = None,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one git subcommand, or return its failure as a result.

        `cwd` replaces the worktree this object was built for and `env`
        replaces the inherited environment, so a caller that must not be
        affected by a repository or a host configuration can leave both.
        """
        limit = self.limit(budget)
        environment = dict(os.environ if env is None else env)
        environment.setdefault("GIT_TERMINAL_PROMPT", "0")
        try:
            return subprocess.run(
                ["git", "-C", str(cwd or self.root), *args],
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                check=False,
                env=environment,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return self.failed(args, f"git {args[0]} did not finish within {limit:g}s")
        except OSError as exc:
            return self.failed(args, str(exc))

    def run_bytes(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        budget: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one git subcommand whose output must keep its exact bytes.

        `run` decodes with surrogateescape, which is lossy for a blob that is
        not this locale's text, so content comparisons ask for bytes instead.
        """
        limit = self.limit(budget)
        environment = dict(os.environ)
        environment.setdefault("GIT_TERMINAL_PROMPT", "0")
        try:
            return subprocess.run(
                ["git", "-C", str(self.root), *args],
                input=input_bytes,
                capture_output=True,
                check=False,
                env=environment,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                [*args], 1, b"", f"git {args[0]} did not finish within {limit:g}s".encode()
            )
        except OSError as exc:
            return subprocess.CompletedProcess([*args], 1, b"", str(exc).encode())

    def failed(self, args: tuple[str, ...], detail: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([*args], 1, "", detail)

    def tree_entry(
        self, sha: str, relative: str
    ) -> tuple[bool, tuple[str, str] | None]:
        """One path's tree entry at `sha`, with the query result kept apart.

        The first value says whether Git answered at all, so a failed or
        timed-out lookup is never read as an absent path - a deletion is
        verified by absence, and an unanswered query proves nothing. The second
        is `(mode, object id)` when the path is in the tree.
        """
        result = self.run(
            "ls-tree", "-z", sha, "--", relative,
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return False, None
        for record in result.stdout.split("\0"):
            meta, _, name = record.partition("\t")
            if name != relative:
                continue
            fields = meta.split()
            if len(fields) == 3:
                return True, (fields[0], fields[2])
        return True, None

    def object_bytes(self, object_id: str) -> tuple[bool, bytes | None]:
        """One object's exact bytes, with unreadability kept apart from empty."""
        result = self.run_bytes(
            "cat-file", "blob", object_id, budget=self.limit(default=QUERY_BUDGET)
        )
        if result.returncode != 0:
            return False, None
        return True, result.stdout

    def blob_id(self, sha: str, relative: str) -> str | None:
        """The blob Git stored for `relative` at `sha`, or None when it is not one."""
        found, entry = self.tree_entry(sha, relative)
        if not found or entry is None or entry[0] not in ("100644", "100755"):
            return None
        return entry[1]

    def blob_bytes(self, sha: str, relative: str) -> bytes | None:
        """The exact bytes Git committed for `relative` at `sha`, or None.

        None covers both an absent path and an unreadable one, so a caller that
        must tell them apart asks `tree_entry` first.
        """
        found, entry = self.tree_entry(sha, relative)
        if not found or entry is None:
            return None
        read, data = self.object_bytes(entry[1])
        return data if read else None

    def transformed(self, relative: str) -> bool | None:
        """Whether Git rewrites this path's bytes on its way into a commit.

        A declared `text`, `filter` or `eol` attribute - or a `core.autocrlf` or
        `core.eol` setting, which rewrites line endings on its own - makes the
        committed blob a function of the working bytes rather than those bytes
        themselves. None means the question went unanswered, which a caller
        treats as a failed check rather than as permission.
        """
        result = self.run(
            "check-attr", "-z", "text", "filter", "eol", "--", relative,
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return None
        values = result.stdout.split("\0")[2::3]
        if any(value not in ("unspecified", "unset") for value in values):
            return True
        # `core.autocrlf` and `core.eol` rewrite line endings even where no
        # attribute names a transformation, so they count as one here too.
        autocrlf = self.run(
            "config", "--get", "core.autocrlf", budget=self.limit(default=QUERY_BUDGET)
        ).stdout.strip().lower()
        if autocrlf in ("true", "input"):
            return True
        eol = self.run(
            "config", "--get", "core.eol", budget=self.limit(default=QUERY_BUDGET)
        ).stdout.strip().lower()
        return eol in ("lf", "crlf")

    def mode(self, sha: str, relative: str) -> str | None:
        """One path's Git mode at `sha` (`100644`, `100755`, ...), or None."""
        result = self.run(
            "ls-tree", "-z", sha, "--", relative,
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return None
        for entry in result.stdout.split("\0"):
            fields = entry.split(None, 3)
            if len(fields) >= 2 and fields[0] and fields[1] == "blob":
                return fields[0]
        return None

    def changed_paths(self, sha: str) -> list[str] | None:
        """Every repo-relative path one commit changed, or None on a failed read."""
        result = self.run(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "-z", sha,
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return None
        return [entry for entry in result.stdout.split("\0") if entry]

    def detail(self, completed: subprocess.CompletedProcess[str]) -> str:
        """The one line of a failed git result worth reporting."""
        text = (completed.stderr or completed.stdout or "").strip()
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else f"git exited {completed.returncode}"

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").stdout.strip()

    def toplevel(self) -> Path | None:
        """The worktree root Git attributes to this path, or None."""
        result = self.run(
            "rev-parse", "--show-toplevel", budget=self.limit(default=QUERY_BUDGET)
        )
        value = result.stdout.strip()
        return Path(value) if result.returncode == 0 and value else None

    def dirty_paths(self) -> list[str]:
        """Repo-relative paths whose worktree or index differs from HEAD.

        A query git does not answer reads as no dirty path here, so a caller
        that must not confuse dirt with a failed read asks `status_paths`
        instead.
        """
        return self.status_paths()[1]

    def status_paths(self) -> tuple[bool, list[str]]:
        """Whether git answered the status query, and the paths it named.

        Porcelain v1 with NUL separators so a path can hold any byte; a
        rename or copy entry carries the original path in the following
        field, which is consumed rather than parsed as another path.
        """
        result = self.run(
            "status", "--porcelain", "-z", "--untracked-files=all",
            budget=self.limit(default=QUERY_BUDGET),
        )
        if result.returncode != 0:
            return (False, [])
        fields = result.stdout.split("\0")
        paths: list[str] = []
        index = 0
        while index < len(fields):
            entry = fields[index]
            index += 1
            if not entry:
                continue
            status = entry[:2]
            paths.append(entry[3:])
            if "R" in status or "C" in status:
                index += 1
        return (True, paths)

    def head_blob(self, relative: str) -> str | None:
        """The committed content of one repo-relative path at HEAD, or None."""
        result = self.run(
            "show", f"HEAD:{relative}", budget=self.limit(default=QUERY_BUDGET)
        )
        return result.stdout if result.returncode == 0 else None

    def has_sync_target(self, budget: float | None = None) -> bool:
        """Whether this worktree has a remote and an upstream branch to sync."""
        remote = self.run("remote", budget=self.limit(budget, QUERY_BUDGET))
        if remote.returncode != 0 or not remote.stdout.strip():
            return False
        upstream = self.run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
            budget=self.limit(budget, QUERY_BUDGET),
        )
        return upstream.returncode == 0

    def upstream(self, budget: float | None = None) -> tuple[str, str] | None:
        """The current branch's remote and merge ref, from this repo's own config.

        Read with `--local` so a `branch.<name>.merge` added through the
        environment (`GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`) or a
        redirected global config cannot name a second head. None when HEAD is
        detached or the local config does not name exactly one upstream.
        """
        head = self.run(
            "symbolic-ref", "-q", "HEAD", budget=self.limit(budget, QUERY_BUDGET)
        ).stdout.strip()
        if not head.startswith("refs/heads/"):
            return None
        branch = head[len("refs/heads/"):]
        values: list[str] = []
        for key in ("remote", "merge"):
            found = self.run(
                "config", "--local", "--get-all", f"branch.{branch}.{key}",
                budget=self.limit(budget, QUERY_BUDGET),
            )
            lines = found.stdout.splitlines()
            if found.returncode != 0 or len(lines) != 1 or not lines[0] or lines[0].startswith("-"):
                return None
            values.append(lines[0])
        return values[0], values[1]

    def upstream_ref(self, budget: float | None = None) -> str | None:
        """The full local ref Git maps the current branch's upstream to.

        Git owns the mapping, so a renamed remote namespace or a custom fetch
        refspec needs no second implementation here. None when the upstream is
        unresolvable or does not name exactly one ref.
        """
        result = self.run(
            "rev-parse", "--symbolic-full-name", "@{u}",
            budget=self.limit(budget, QUERY_BUDGET),
        )
        lines = result.stdout.splitlines()
        if result.returncode != 0 or len(lines) != 1 or not lines[0].startswith("refs/"):
            return None
        return lines[0]
