"""The governing documents one memory update is audited against.

`GoverningSources` is the one owner for source capture. It names every document
that binds a change - the owner's root documents, the declared shared root's
documents, each ancestor group's mandatory conventions, and the leaf an
attachment belongs to - and reads each of them once into an inventory row that
carries the raw bytes, the normalized text, and the digest of that text.

One read, one revision: the digest an audit binds to, the text its checks are
verified against and the bytes a final freshness check compares all derive from
the same capture, so no later read can silently describe a different revision.
An absent optional document is captured too, with a null digest, so creating it
during an update is a visible change rather than a silent one.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

ROOT_DOCUMENT = "MEMORY.md"
RULES_DOCUMENT = "RULES.md"
STRUCTURE_DOCUMENT = "STRUCTURE.md"


class SourceError(Exception):
    """A governing source that cannot be captured."""


class GoverningSources:
    """Every document a memory update is bound to, captured once."""

    def __init__(
        self,
        memory_root: Path,
        agent_home: Path,
        shared_root: Path | None = None,
    ) -> None:
        self.memory_root = Path(memory_root)
        self.agent_home = Path(agent_home) if agent_home is not None else None
        self.shared_root = Path(shared_root) if shared_root is not None else None

    def sources(self, plan: Any = None) -> list[tuple[str, Path, bool]]:
        """Every document that governs this change, and whether it is required."""
        entries: list[tuple[str, Path, bool]] = [
            (
                f"The owner's document ({ROOT_DOCUMENT})",
                self.memory_root / ROOT_DOCUMENT,
                True,
            )
        ]
        if self.agent_home is not None:
            entries += [
                (
                    f"The owner's document ({RULES_DOCUMENT})",
                    self.agent_home / RULES_DOCUMENT,
                    True,
                ),
                (
                    f"The owner's document ({STRUCTURE_DOCUMENT})",
                    self.agent_home / STRUCTURE_DOCUMENT,
                    True,
                ),
            ]
        if self.shared_root is not None:
            entries.append(
                (
                    f"The owner's document (shared {ROOT_DOCUMENT})",
                    self.shared_root / ROOT_DOCUMENT,
                    True,
                )
            )
        entries += [(label, path, False) for label, path in self.context_documents()]
        entries += self.group_documents(plan)
        return entries

    def inventory(self, plan: Any = None) -> list[dict[str, Any]]:
        """Every governing source with the exact bytes a reading binds to.

        It is the one list the audit challenge, the submitted readings and the
        final freshness check share: deduped by resolved path, in capture order,
        with an absent optional document bound by a null digest so its creation
        during the update is a visible change rather than a silent one. An
        unreadable or missing required source refuses here, so no change is ever
        audited against a requirement that could not be read.
        Each row is read once, so its digest, the text an audit renders and the
        bytes its recheck compares describe one revision.
        """
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for label, path, required in self.sources(plan):
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise SourceError(
                    f"the governing document {path} is not a readable regular file"
                )
            readable, data = self.state(path)
            if not readable:
                raise SourceError(
                    f"the governing document {path} is not a readable regular file"
                )
            if data is None and required:
                raise SourceError(f"the governing document {path} does not exist")
            try:
                text = (
                    None
                    if data is None
                    else data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                )
            except UnicodeDecodeError:
                raise SourceError(
                    f"the governing document {path} is not readable UTF-8 text"
                )
            # One read, one revision: the digest is taken over the text, exactly
            # as a read receipt hashes the bytes the read tool opened (CRLF and
            # LF are one document), and the bytes that text came from are what
            # the final freshness check compares. Reading twice would let the
            # audit and the recheck each describe a different revision.
            rows.append(
                {
                    "path": str(path),
                    "label": label,
                    "digest": (
                        None
                        if text is None
                        else hashlib.sha256(text.encode("utf-8")).hexdigest()
                    ),
                    "text": text or "",
                    "data": data,
                }
            )
        return rows

    @staticmethod
    def state(path: Path) -> tuple[bool, bytes | None]:
        """Whether `path` could be read, and its bytes, with None meaning absent.

        A symlink or an existing non-regular path is unreadable rather than
        absent, since it is the store's own choice between a document and
        nothing, not a document that happens not to exist yet.
        """
        if path.is_symlink():
            return False, None
        try:
            return True, path.read_bytes()
        except FileNotFoundError:
            return True, None
        except OSError:
            return False, None

    def group_documents(self, plan: Any) -> list[tuple[str, Path, bool]]:
        """Every document above the changed paths that binds a write there.

        A group's **Mandatory conventions** bind writes inside it and are
        inherited, so a write audited without them is audited against an
        incomplete requirement. The candidate set - present or not - is fixed
        here, so a document created during the update invalidates the audit
        rather than silently escaping it. An attachment is audited with the leaf
        it belongs to, and never with conventions of its own, since
        `rm_control` states attachments declare none.
        """
        if plan is None:
            return []
        roots = [root for root in (self.memory_root, self.shared_root) if root]
        found: dict[str, tuple[str, Path, bool]] = {}
        for anchor, owning in self.anchors(plan):
            if owning:
                found.setdefault(
                    os.path.normcase(str(anchor)),
                    ("The leaf this attachment belongs to", anchor, True),
                )
            for root in roots:
                current = anchor.parent
                while current != root:
                    try:
                        current.relative_to(root)
                    except ValueError:
                        break
                    if self.attachment_dir(current):
                        break
                    candidate = current / ROOT_DOCUMENT
                    present = candidate.is_symlink() or candidate.exists()
                    found.setdefault(
                        os.path.normcase(str(candidate)),
                        (
                            "The conventions an ancestor group declares",
                            candidate,
                            present,
                        ),
                    )
                    if current == current.parent:
                        break
                    current = current.parent
        return [found[key] for key in sorted(found)]

    def anchors(self, plan: Any) -> list[tuple[Path, bool]]:
        """Each changed path, or the leaf that owns it when it is an attachment.

        `MOVE` has two ends and both are captured. A path anywhere inside an
        attachment tree is anchored on the leaf that tree documents, so the leaf
        is shown and no document inside the attachment is taken for a
        convention; `rm_control` states attachments declare none.
        """
        anchors: list[tuple[Path, bool]] = []
        for effect in plan.changes():
            for path in (
                getattr(effect, "path", None),
                getattr(effect, "destination", None),
            ):
                if not path:
                    continue
                path = Path(path)
                leaf = self.attachment_owner(path)
                anchors.append((leaf, True) if leaf is not None else (path, False))
        return anchors

    def attachment_owner(self, path: Path) -> Path | None:
        """The nearest enclosing attachment directory's leaf, if any."""
        current = path.parent
        while True:
            leaf = self.attachment_dir(current)
            if leaf is not None:
                return leaf
            if current == current.parent:
                return None
            current = current.parent

    @staticmethod
    def attachment_dir(directory: Path) -> Path | None:
        """The leaf an attachment directory documents, when `directory` is one."""
        leaf = directory.parent / f"{directory.name}.md"
        return leaf if leaf.is_file() else None

    def context_documents(self) -> list[tuple[str, Path]]:
        """Further governing documents, whether or not they exist yet.

        Absent candidates are watched too: one created during the update
        changes the requirement, and the final check refuses rather than
        applying a change audited against an incomplete requirement.
        """
        candidates: list[tuple[str, Path]] = [
            (
                "The memory root's structure",
                self.memory_root / STRUCTURE_DOCUMENT,
            )
        ]
        if self.shared_root is not None:
            candidates += [
                ("The shared root's rules", self.shared_root / RULES_DOCUMENT),
                ("The shared root's structure", self.shared_root / STRUCTURE_DOCUMENT),
            ]
        return candidates

    def unchanged(self, inventory: list[dict[str, Any]]) -> bool:
        """Whether every captured document still reads exactly as it was read.

        A requirement edited, removed or introduced after the capture makes the
        audit meaningless, because the admitted audit describes text that is no
        longer there. A path that has become unreadable or is no longer a
        regular file counts as changed, never as an absent document. The
        comparison is always against the one captured inventory, never against
        a replacement capture.
        """
        for row in inventory:
            path = Path(row["path"])
            if path.is_symlink() or (path.exists() and not path.is_file()):
                return False
            readable, current = self.state(path)
            if not readable or current != row["data"]:
                return False
        return True
