"""The structure of the declared shared-memory tree.

One owner for answering what the shared tree currently looks like:
`SharedTree` walks the declared shared directory, skips Git internals, and
returns a stable digest of the relative path layout. The convention gate
uses that digest to require every agent to have read the shared structure
with the `tree` command; the digest is bookkeeping, not a memory source, and
this class never writes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

MAX_ENTRIES = 20000
SKIP_DIRECTORIES = frozenset({".git"})


class SharedTree:
    """The relative path layout of one shared-memory directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def names_root(self, candidate: Path) -> bool:
        """Whether a candidate directory is exactly this shared root."""
        try:
            return os.path.normcase(str(candidate.resolve(strict=False))) == os.path.normcase(
                str(self.root.resolve(strict=False))
            )
        except (OSError, ValueError):
            return False

    def digest(self) -> str | None:
        """A stable digest of the tree's relative layout, or None when absent."""
        root = self.root.resolve(strict=False)
        if not root.is_dir():
            return None
        entries: list[str] = []
        for directory, subdirectories, files in os.walk(root):
            subdirectories[:] = sorted(
                name for name in subdirectories if name not in SKIP_DIRECTORIES
            )
            base = Path(directory)
            for name in subdirectories:
                entries.append((base / name).relative_to(root).as_posix())
            for name in sorted(files):
                entries.append((base / name).relative_to(root).as_posix())
            if len(entries) >= MAX_ENTRIES:
                entries = entries[:MAX_ENTRIES]
                break
        return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()