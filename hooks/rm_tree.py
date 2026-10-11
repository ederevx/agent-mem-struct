"""The structure of the declared shared-memory tree.

One owner for answering what the shared tree currently looks like. `SharedTree`
walks the declared shared directory once, skips Git internals, and produces the
relative layout that both the convention receipt's digest and the shipped
`rm_tree.py` command render. The command is the protocol's own bounded
alternative to an external `tree`; it reads the complete layout including
hidden entries while never following an internal symlink. Nothing here writes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

MAX_ENTRIES = 20000
SKIP_NAMES = frozenset({".git"})

TreeEntry = tuple[str, bool, str | None]


class TreeError(Exception):
    """A root that cannot be listed completely."""


class SharedTree:
    """The relative layout of one shared-memory directory."""

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

    def entries(self) -> list[TreeEntry]:
        """Every descendant as ``(relative path, is_dir, symlink target)``.

        Directories are emitted before files and each level is sorted, so the
        order is deterministic and doubles as the render order. `.git` is
        skipped everywhere, and an internal symlink is listed as a leaf rather
        than followed, so a link cannot expand the tree or escape it.
        """
        root = self.root.resolve(strict=False)
        if not root.is_dir():
            raise TreeError(f"not a directory: {self.root}")
        found: list[TreeEntry] = []

        def walk(directory: Path, parts: list[str]) -> None:
            try:
                names = sorted(os.listdir(directory))
            except OSError as exc:
                raise TreeError(f"cannot list {directory}: {exc}") from exc
            directories: list[tuple[str, Path]] = []
            leaves: list[tuple[str, Path]] = []
            for name in names:
                if name.lower() in SKIP_NAMES:
                    continue
                candidate = directory / name
                if candidate.is_symlink() or not candidate.is_dir():
                    leaves.append((name, candidate))
                else:
                    directories.append((name, candidate))
            for name, candidate in directories:
                relative = "/".join(parts + [name])
                found.append((relative, True, None))
                self._guard(found)
                walk(candidate, parts + [name])
            for name, candidate in leaves:
                relative = "/".join(parts + [name])
                link = os.readlink(candidate) if candidate.is_symlink() else None
                found.append((relative, False, link))
                self._guard(found)

        try:
            walk(root, [])
        except RecursionError as exc:
            raise TreeError("directory nesting is too deep") from exc
        return found

    @staticmethod
    def _guard(found: list[TreeEntry]) -> None:
        if len(found) > MAX_ENTRIES:
            raise TreeError(f"more than {MAX_ENTRIES} entries")

    @staticmethod
    def hash_entries(entries: list[TreeEntry]) -> str:
        """The digest of one snapshot: directories, and symlink targets, shown."""
        lines: list[str] = []
        for relative, is_dir, link in entries:
            text = relative + "/" if is_dir else relative
            if link:
                text += " -> " + link
            lines.append(text.replace("\\", "\\\\").replace("\n", "\\n"))
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def digest(self) -> str | None:
        """A stable digest of the tree's relative layout, or None when absent."""
        if not self.root.resolve(strict=False).is_dir():
            return None
        try:
            return self.hash_entries(self.entries())
        except TreeError:
            return None

    def render(self, entries: list[TreeEntry] | None = None) -> str:
        """The header plus an indented, lossless listing of the layout."""
        if entries is None:
            entries = self.entries()
        root = self.root.resolve(strict=False)
        header = (
            f"# shared: {root}  entries: {len(entries)}  "
            f"sha256: {self.hash_entries(entries)}"
        )
        lines = [header]
        for relative, is_dir, link in entries:
            depth = relative.count("/")
            name = relative.rsplit("/", 1)[-1]
            suffix = "/" if is_dir else ""
            arrow = f" -> {link}" if link else ""
            lines.append("  " * depth + name + suffix + arrow)
        return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rm_tree.py",
        description="Print the complete layout of a shared-memory directory.",
    )
    parser.add_argument("--list", action="store_true", help="print the layout (default)")
    parser.add_argument("root", nargs="?", help="the shared-memory directory to list")
    args = parser.parse_args(argv)
    if not args.root:
        print("rm_tree.py: a shared-memory root is required", file=sys.stderr)
        return 2
    tree = SharedTree(args.root)
    try:
        entries = tree.entries()
    except TreeError as exc:
        print(f"rm_tree.py: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(tree.render(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())