"""The exact file changes one memory mutation would make, before any of them.

One owner for the evidence an audit is made on: `PlanRecorder` stands in for
the filesystem while `MemoryUpdater` runs an operation, so the same operation
code produces the changes it would write instead of writing them, and
`UpdatePlan` carries those changes with every path whose observed state the
operation depended on - file bytes, directory membership, and the Git facts
(HEAD, committed blob, mode) a publication will be checked against.

Nothing here decides an operation, renders a memory body, or writes a file:
the operation semantics stay in `rm_update.MemoryUpdater`. The recorder only
records - and, once the audit is admitted, re-records so a plan whose inputs
moved is refused rather than applied.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterable

WRITE = "write"
DELETE = "delete"
MKDIR = "mkdir"
RMDIR = "rmdir"
MOVE = "move"
READ = "read"

FILE = "file"
DIRECTORY = "directory"
ABSENT = "absent"
UNREADABLE = "unreadable"
# The absence of an argument, kept apart from an explicit "no prior bytes".
_UNOBSERVED: Any = object()


class PlanConflict(Exception):
    """A mutation that cannot be recorded without destroying unseen content."""


class FileEffect:
    """One change to the file system, with the bytes on both sides of it.

    `before` is the original the audit is shown, which for a publication is the
    last committed bytes rather than what the working file happens to hold;
    `observed` is that working state, the one an apply must still find.
    """

    def __init__(
        self,
        kind: str,
        path: Path,
        before: bytes | None = None,
        after: bytes | None = None,
        destination: Path | None = None,
        observed: Any = _UNOBSERVED,
    ) -> None:
        self.kind = kind
        self.path = path
        self.before = before
        self.after = after
        self.destination = destination
        self.observed = before if observed is _UNOBSERVED else observed

    def key(self) -> tuple[Any, ...]:
        return (
            self.kind,
            str(self.path),
            self.before,
            self.after,
            self.observed,
            str(self.destination) if self.destination is not None else None,
        )

    def display(self) -> str:
        if self.kind == MOVE:
            return f"{self.path} -> {self.destination}"
        return f"{self.kind} {self.path}"

class FileObservation:
    """The state of one path an operation read, so a plan can be re-proved."""

    def __init__(self, path: Path, digest: str, kind: str) -> None:
        self.path = path
        self.digest = digest
        self.kind = kind

    def key(self) -> tuple[str, str, str]:
        return (str(self.path), self.digest, self.kind)


def digest(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()


class PlanRecorder:
    """A write-free stand-in for the file system that records what it sees."""

    def __init__(self) -> None:
        self.effects: list[FileEffect] = []
        self.observations: dict[str, FileObservation] = {}
        self.planned: dict[str, bytes | None] = {}
        self.planned_paths: dict[str, Path] = {}
        self.directories: dict[str, str] = {}
        self.context: dict[str, str] = {}
        self.publish: list[Path] = []
        self._seen: dict[str, bytes | None] = {}

    # -- observation ---------------------------------------------------------

    def observe(self, path: Path) -> bytes | None:
        """The bytes at `path` as this plan sees them, recorded as a dependency."""
        key = self.key(path)
        if key in self.planned:
            return self.planned[key]
        self._observe(key, path)
        return self._seen[key]

    def state(self, path: Path) -> str:
        """Whether this path is a file, a directory, absent, or unreadable."""
        key = self.key(path)
        if key in self.planned:
            return ABSENT if self.planned[key] is None else FILE
        if key in self.planned_paths:
            return ABSENT if self.planned[key] is None else FILE
        if self.is_dir(path):
            return DIRECTORY
        self._observe(key, path)
        return self.observations[key].kind

    @staticmethod
    def key(path: Path) -> str:
        return os.path.normcase(str(path.resolve(strict=False)))

    def _observe(self, key: str, path: Path) -> None:
        """Read one path once per plan and record what that reading depended on."""
        if key in self.observations:
            return
        data, kind = self._read(path)
        if kind == DIRECTORY:
            data = None
        self._seen[key] = data
        self.observations[key] = FileObservation(
            path, digest(data) if data is not None else self._members(path), kind
        )

    @staticmethod
    def _members(path: Path) -> str:
        """A directory's membership digest, so a listing change is an input change."""
        try:
            names = sorted(os.path.normcase(child.name) for child in path.iterdir())
        except OSError:
            names = []
        return digest("\0".join(names).encode("utf-8"))

    @staticmethod
    def _read(path: Path) -> tuple[bytes | None, str]:
        """One path's bytes and its kind, never confusing a failure for absence."""
        try:
            if path.is_symlink():
                return None, UNREADABLE
            if path.is_dir():
                return None, DIRECTORY
            if not path.exists():
                return None, ABSENT
            return path.read_bytes(), FILE
        except OSError:
            return None, UNREADABLE

    def is_planned(self, path: Path) -> bool:
        """Whether this plan already decides the bytes of `path`."""
        return self.key(path) in self.planned

    def exists(self, path: Path) -> bool:
        key = self.key(path)
        if key in self.planned:
            return self.planned[key] is not None
        if self.is_dir(path):
            return True
        return self.state(path) == FILE

    def is_dir(self, path: Path) -> bool:
        key = self.key(path)
        if key in self.planned:
            return False
        if self.directories.get(key) == "planned":
            return True
        for item in self.planned_paths:
            if self.planned.get(item) is not None and item.startswith(key + os.sep):
                return True
        if key not in self.observations:
            self._observe(key, path)
        return self.observations[key].kind == DIRECTORY

    def entries(self, path: Path) -> list[Path]:
        """The children of a directory as this plan sees them."""
        key = self.key(path)
        self._observe(key, path)
        found: dict[str, Path] = {}
        try:
            for child in path.iterdir():
                found[self.key(child)] = child
        except OSError:
            pass
        for item, stored in self.planned_paths.items():
            if os.path.dirname(item) != key:
                continue
            if self.planned.get(item) is None:
                found.pop(item, None)
            else:
                found[item] = stored
        return [found[item] for item in sorted(found)]

    def files(self, root: Path, suffix: str) -> list[Path]:
        """Every file under `root` with this suffix, as this plan sees them."""
        found: dict[str, Path] = {}
        for base, directories, names in os.walk(root):
            self._observe(self.key(Path(base)), Path(base))
            directories[:] = [name for name in directories if name != ".git"]
            for name in names:
                child = Path(base) / name
                if child.suffix == suffix and child.is_file():
                    found[self.key(child)] = child
        for item, stored in self.planned_paths.items():
            if self.planned.get(item) is None or stored.suffix != suffix:
                continue
            if key_under(item, self.key(root)):
                found[item] = stored
        return [found[item] for item in sorted(found)]

    # -- effects -------------------------------------------------------------

    def write(
        self, path: Path, data: bytes | None, before: Any = _UNOBSERVED
    ) -> None:
        """Record the bytes `path` gains, and the bytes it holds now.

        `before` is what the audit is shown as the prior state, and it differs
        from `observed` exactly when a publication is audited against the
        committed bytes rather than an uncommitted working edit. Omitting it
        means "the current bytes", which is a real state distinct from
        `before=None`, the explicit absence of a path.
        """
        key = self.key(path)
        current = self.observe(path)
        if key in self.planned:
            before = self.planned[key]
        elif before is _UNOBSERVED:
            before = current
        kind = WRITE if data is not None else DELETE
        self.effects.append(FileEffect(kind, path, before, data, observed=current))
        self.planned[key] = data
        self.planned_paths[key] = path

    def mkdir(self, path: Path) -> None:
        key = self.key(path)
        if self.is_dir(path):
            return
        self.effects.append(FileEffect(MKDIR, path))
        self.directories[key] = "planned"

    def rmdir(self, path: Path) -> None:
        self.effects.append(FileEffect(RMDIR, path))
        self.directories[self.key(path)] = "removed"

    def rmtree(self, path: Path) -> None:
        """Remove one directory tree as the files it holds, then its directories."""
        for child, data in self._tree(path):
            self.effects.append(FileEffect(DELETE, child, data, None))
            self.planned[self.key(child)] = None
            self.planned_paths[self.key(child)] = child
        for directory in reversed(self._directories(path)):
            self.effects.append(FileEffect(RMDIR, directory))
            self.directories[self.key(directory)] = "removed"

    def rename(self, source: Path, destination: Path) -> None:
        """Move a file, or every file under a directory, effect by effect."""
        if self.state(destination) != ABSENT:
            raise PlanConflict(
                f"{destination} already exists, so the rename would replace "
                "content the audit was never shown"
            )
        if self.is_dir(source):
            for child, data in self._tree(source):
                target = destination / child.relative_to(source)
                self.effects.append(FileEffect(MOVE, child, data, data, destination=target))
                self.planned[self.key(target)] = data
                self.planned_paths[self.key(target)] = target
                self.planned[self.key(child)] = None
                self.planned_paths[self.key(child)] = child
            for directory in self._directories(source):
                target = (
                    destination if directory == source
                    else destination / directory.relative_to(source)
                )
                self.effects.append(FileEffect(MKDIR, target))
                self.directories[self.key(target)] = "planned"
                self.directories[self.key(directory)] = "removed"
            for directory in reversed(self._directories(source)):
                # Every file moved out, so the directory tree it held is gone:
                # a rename leaves no empty source tree behind.
                self.effects.append(FileEffect(RMDIR, directory))
            return
        before = self.observe(source)
        self.effects.append(FileEffect(MOVE, source, before, before, destination=destination))
        self.planned[self.key(destination)] = before
        self.planned_paths[self.key(destination)] = destination
        self.planned[self.key(source)] = None
        self.planned_paths[self.key(source)] = source

    def _tree(self, path: Path) -> list[tuple[Path, bytes | None]]:
        """Every file under one directory, with its bytes, deepest names sorted."""
        found: list[tuple[Path, bytes | None]] = []
        for base, directories, names in os.walk(path):
            directories.sort()
            for name in sorted(names):
                child = Path(base) / name
                self._observe(self.key(Path(base)), Path(base))
                found.append((child, self.observe(child)))
        return found

    def _directories(self, path: Path) -> list[Path]:
        """A directory and every directory under it, parents before children."""
        found: list[Path] = [path]
        for base, directories, _ in os.walk(path):
            directories.sort()
            found.extend(Path(base) / name for name in directories)
        return found


def key_under(key: str, root: str) -> bool:
    """Whether one normalized key is the root itself or lies under it."""
    return key == root or key.startswith(root + os.sep)


class UpdatePlan:
    """What one audited mutation would change, and what it depended on."""

    def __init__(
        self,
        operation: str,
        intent: dict[str, Any],
        recorder: PlanRecorder,
        result: Any,
        commit_message: str,
    ) -> None:
        self.operation = operation
        self.intent = intent
        self.effects = list(recorder.effects)
        self.observations = dict(recorder.observations)
        self.context = dict(recorder.context)
        self.publish_paths = list(recorder.publish)
        self.result = result
        self.commit_message = commit_message

    def changes(self) -> list[FileEffect]:
        """The effects the audit reads, in the order the operation made them."""
        return [effect for effect in self.effects if effect.kind != MKDIR]

    def matches(self, other: UpdatePlan) -> tuple[bool, str]:
        """Whether a second recording is the plan this audit was admitted on."""
        if self.operation != other.operation:
            return False, "the operation changed after audit capture"
        if self.intent != other.intent:
            return False, "the requested mutation changed after audit capture"
        if self.commit_message != other.commit_message:
            return False, "the commit message changed after audit capture"
        if self.publish_paths != other.publish_paths:
            return False, "the set of published paths changed after audit capture"
        mine = [effect.key() for effect in self.effects]
        theirs = [effect.key() for effect in other.effects]
        if mine != theirs:
            return False, "the planned changes differ from the audited draft"
        if self.context != other.context:
            changed = sorted(
                key for key in self.context if self.context.get(key) != other.context.get(key)
            ) + sorted(set(other.context) - set(self.context))
            return False, "these repository facts changed after audit capture: " + ", ".join(
                changed[:5]
            )
        read = {key: item.key() for key, item in self.observations.items()}
        reread = {key: item.key() for key, item in other.observations.items()}
        if read != reread:
            changed = sorted(
                detail[0]
                for key, detail in reread.items()
                if read.get(key) != detail
            )
            if not changed:
                changed = sorted({detail[0] for detail in read.values()} - {
                    detail[0] for detail in reread.values()
                })
            return False, "these inputs changed after audit capture: " + ", ".join(changed[:5])
        return True, ""

    def paths(self) -> list[Path]:
        seen: list[Path] = []
        for effect in self.effects:
            for path in (effect.path, effect.destination):
                if path is not None and path not in seen:
                    seen.append(path)
        return seen

    def payload(self) -> dict[str, Any]:
        """Everything this plan declares, as one canonical auditable value.

        The audit the agent returns is bound to this digest, so a change that
        differs in any byte, any observed input, or any commit line cannot be
        covered by an audit written for another change.
        """
        from rm_conventions import bytes_field

        return {
            "operation": self.operation,
            "intent": _plain(self.intent),
            "commit_message": self.commit_message,
            "publish": [str(path) for path in self.publish_paths],
            "context": _plain(self.context),
            "observations": {
                key: [_plain(item.path), item.digest, item.kind]
                for key, item in self.observations.items()
            },
            "effects": [
                {
                    "kind": effect.kind,
                    "path": str(effect.path),
                    "destination": (
                        str(effect.destination)
                        if effect.destination is not None
                        else None
                    ),
                    "before": bytes_field(effect.before),
                    "after": bytes_field(effect.after),
                    "observed": (
                        bytes_field(effect.observed)
                        if effect.observed is None or isinstance(effect.observed, bytes)
                        else _plain(effect.observed)
                    ),
                }
                for effect in self.effects
            ],
        }

    def audit_digest(self) -> str:
        """The digest the audit must name to describe exactly this change."""
        from rm_conventions import canonical

        return hashlib.sha256(canonical(self.payload())).hexdigest()


def _plain(value: Any) -> Any:
    """One recorded value as JSON: paths as text, tuples as lists."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def describe(effects: Iterable[FileEffect]) -> str:
    return ", ".join(effect.display() for effect in effects)
