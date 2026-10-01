"""Read-only Git evidence about the edits an agent made in its worktrees.

One owner for answering what an agent has edited by hand: `WorktreeEdits`
scopes the agent's memory tree and the declared shared root, reports the
paths inside them that differ from HEAD, returns a leaf's committed body,
and tells whether a target is Git-backed. Git plumbing lives in
`rm_git.GitWorktree`; this class only composes it, so it mutates nothing and
never commits, appends, or stages.
"""
from __future__ import annotations

from pathlib import Path

from rm_git import GitWorktree
from rm_support import under


class WorktreeEdits:
    """The working-tree edits visible inside the memory scopes."""

    def __init__(self, memory_root: Path, shared_root: Path | None = None) -> None:
        self.memory_root = Path(memory_root)
        self.shared_root = Path(shared_root) if shared_root is not None else None

    def scopes(self) -> list[Path]:
        """The memory tree and the declared shared root, resolved and deduped."""
        scopes: list[Path] = []
        for root in (self.memory_root, self.shared_root):
            if root is None:
                continue
            resolved = root.resolve(strict=False)
            if resolved not in scopes:
                scopes.append(resolved)
        return scopes

    def repo_root(self, path: Path) -> Path | None:
        """The Git worktree that encloses `path`, or None when it has none."""
        probe = path if path.is_dir() else path.parent
        return GitWorktree(probe).toplevel()

    def is_repo(self, path: Path) -> bool:
        return self.repo_root(path) is not None

    def dirty_paths(self) -> list[Path]:
        """Every dirty path inside the scopes, as absolute paths.

        A scope whose worktree is not Git-backed contributes nothing; a repo
        that encloses a scope is narrowed back to the scope, so a dirty file
        beside the memory tree is never reported.
        """
        scopes = self.scopes()
        dirty: list[Path] = []
        for root in scopes:
            repo = GitWorktree(root).toplevel()
            if repo is None:
                continue
            base = repo.resolve(strict=False)
            for relative in GitWorktree(repo).dirty_paths():
                absolute = (base / relative).resolve(strict=False)
                if not any(under(absolute, scope) for scope in scopes):
                    continue
                if absolute not in dirty:
                    dirty.append(absolute)
        return dirty

    def previous_body(self, path: Path) -> str | None:
        """The content committed at HEAD for `path`, or None when untracked."""
        repo = self.repo_root(path)
        if repo is None:
            return None
        root = repo.resolve(strict=False)
        relative = path.resolve(strict=False).relative_to(root).as_posix()
        return GitWorktree(root).head_blob(relative)
