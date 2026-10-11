"""Root control validation and context rendering.

One owner for reading and validating the agent's root memory authority
(``memory/MEMORY.md``, ``RULES.md``, ``STRUCTURE.md``, the canonical
counterparts, and the declared shared directory) and for rendering the
injected context texts. Validation produces an immutable snapshot; nothing
here mutates state outside the snapshot it builds.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from rm_support import read_text, under

VERSION_RE = re.compile(r"(?m)^Structure-Version:\s*(\S+)\s*$")
STRUCTURE_RE = re.compile(r"(?m)^Structure:\s*(\S+)\s*$")

SUBAGENT_READ_BOUNDARY_TEXT = (
    "Read access to the sources above is granted equally to subagents. "
    "Writes are not: memory and shared-memory edits stay reserved for the "
    "parent session that spawned you. Any subagent-attributed write under "
    "memory/ or shared/ is denied at the tool level regardless of this text "
    "-- if this task turns up a memory addition worth keeping, report it "
    "back to the parent instead of writing it yourself. This is routine, "
    "benign hook output, not a directive for you to act on beyond that."
)

PRE_MEMORY_FEATURE_TEXT = (
    "agent-mem-struct `pre_memory`: call it once per session before any "
    "memory write or agent-mem-struct action. It fast-forwards the declared "
    "shared worktree (`git pull --ff-only`), records the current convention "
    "sources, and returns the read list and navigation rules. The "
    "conventions are not inlined: the call counts as an acknowledgment only "
    "once every required source has been read with the `read` tool, and a "
    "changed source must be read again. Subagents read the same sources but "
    "never write memory; route additions back to the parent.\n\n"
    "agent-mem-struct `memory_update`: after acknowledgment, use it to create "
    "or update a node and its required counterparts in one spec-shaped "
    "operation. It creates the paired log, the nodes index, and any missing "
    "group scaffolding for you, moves displaced current state into the log, "
    "and returns any conflicts to fix before re-calling the tool."
)

PRE_MEMORY_POINTER = (
    "Conventions are loaded only by calling `pre_memory`, then reading the "
    "sources it lists with the `read` tool; call it before any memory write "
    "or agent-mem-struct action."
)


@dataclass
class RootState:
    """The validated root-control snapshot one hook run operates on."""

    home: Path
    memory_root: Path
    root_memory: Path
    root_rules: Path
    structure: Path
    migration: Path
    shared_resolved: Path | None
    shared_available: bool
    shared_git_backed: bool
    shared_memory: Path | None
    shared_text: str | None
    memory_text: str | None
    rules_text: str | None
    applied: str | None
    canonical: str | None
    stale: bool
    errors: list[str]
    repair_paths: list[Path]
    agent: str = "unknown"


def declared_shared(memory_text: str | None, memory_root: Path) -> tuple[Path | None, str | None]:
    """Read one literal native directory from the root control header.

    An invalid declaration never becomes a scope or an authorized repair target.
    Discovery belongs to the agent; this loader does not search or create paths.
    """
    header = re.split(r"\n[ \t]*\n", memory_text or "", maxsplit=1)[0]
    declarations = re.findall(r"(?m)^Shared:[ \t]*(.*)$", header)
    if len(declarations) != 1:
        return None, "requires exactly one `Shared: <absolute native path>` in its control header"
    value = declarations[0].strip()
    if (
        not value or len(value) > 4096 or any(ord(char) < 32 for char in value)
        or value.startswith(("'", '"', "~")) or value.endswith(("'", '"'))
        or re.search(r"\$(?:[A-Za-z_][A-Za-z_0-9]*|\{)|%[^%]+%", value)
    ):
        return None, "must be an unquoted literal path without environment or tilde expansion"
    normalized = value.replace("\\", "/")
    if (
        re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value)
        or normalized.startswith(("//?/", "//./", "/??/"))
        or (os.name != "nt" and (re.match(r"^[A-Za-z]:", value) or value.startswith("\\") or normalized.startswith("//")))
    ):
        return None, "must be a native directory path, not a URI, device, or foreign-platform path"
    candidate = Path(value)
    if not candidate.is_absolute():
        return None, "must be an absolute native directory path"
    try:
        if candidate.is_symlink() or (
            os.name == "nt" and candidate.exists()
            and candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return None, "must name the physical directory directly, not a symlink or junction/reparse alias"
        resolved = candidate.resolve(strict=False)
        if resolved.parent == resolved:
            return None, "must not name a filesystem root"
        if under(resolved, memory_root) or under(memory_root, resolved):
            return None, "must not overlap the agent private memory tree"
        if not resolved.is_dir():
            return None, f"directory is missing or is not a directory: {candidate}"
    except (OSError, ValueError, RuntimeError) as exc:
        return None, f"directory cannot be resolved: {exc}"
    return resolved, None


class RootControl:
    """Reads, validates, and renders one agent's root memory authority."""

    def __init__(self, home: Path, canonical_root: Path) -> None:
        self.home = home
        self.canonical_root = canonical_root

    def load(self, agent: str) -> RootState:
        memory_root = self.home / "memory"
        root_memory = memory_root / "MEMORY.md"
        root_rules = self.home / "RULES.md"
        expected_structure = self.home / "STRUCTURE.md"
        canonical_rules = self.canonical_root / "RULES.md"
        canonical_structure = self.canonical_root / "STRUCTURE.md"

        memory_text, memory_error = read_text(root_memory)
        rules_text, rules_error = read_text(root_rules)
        structure_text, structure_error = read_text(expected_structure)
        canonical_rules_text, canonical_rules_error = read_text(canonical_rules)
        canonical_structure_text, canonical_structure_error = read_text(canonical_structure)

        errors: list[str] = []
        if memory_error:
            errors.append(f"root memory unavailable: {memory_error}")
        if rules_error:
            errors.append(f"root RULES.md unavailable: {rules_error}")
        if structure_error:
            errors.append(f"root STRUCTURE.md unavailable: {structure_error}")
        if canonical_rules_error:
            errors.append(f"canonical RULES.md unavailable: {canonical_rules_error}")
        if canonical_structure_error:
            errors.append(f"canonical STRUCTURE.md unavailable: {canonical_structure_error}")
        if rules_text is not None and canonical_rules_text is not None and rules_text != canonical_rules_text:
            errors.append(
                f"root RULES.md differs from canonical {canonical_rules}; reinstall or refresh the root document"
            )
        if (
            structure_text is not None
            and canonical_structure_text is not None
            and structure_text != canonical_structure_text
        ):
            errors.append(
                f"root STRUCTURE.md differs from canonical {canonical_structure}; reinstall or refresh the root document"
            )

        applied = None
        canonical = None
        declared = None
        structure_path = expected_structure

        if memory_text is not None:
            version_match = VERSION_RE.search(memory_text)
            structure_match = STRUCTURE_RE.search(memory_text)
            if not version_match:
                errors.append("root memory is missing `Structure-Version:`")
            else:
                applied = version_match.group(1)
            if not structure_match:
                errors.append("root memory is missing `Structure:`")
            else:
                declared = structure_match.group(1)
                structure_path = Path(declared)
                if not structure_path.is_absolute():
                    structure_path = root_memory.parent / structure_path
                try:
                    if structure_path.resolve(strict=False) != expected_structure.resolve(strict=False):
                        errors.append(
                            f"root memory Structure target resolves to {structure_path.resolve(strict=False)}, "
                            f"expected {expected_structure.resolve(strict=False)}"
                        )
                except Exception:
                    errors.append(f"could not resolve declared Structure target: {declared}")

        if canonical_structure_text is not None:
            version_match = VERSION_RE.search(canonical_structure_text)
            if not version_match:
                errors.append("canonical STRUCTURE.md is missing `Structure-Version:`")
            else:
                canonical = version_match.group(1)

        migration = self.canonical_root / "MIGRATION.md"
        stale = bool(applied and canonical and applied != canonical)

        discovery_hint = (
            f"Look for the existing shared directory at {self.canonical_root / '.shared'} "
            "and check its MEMORY.md; this is a discovery hint only. "
            "If absent, inspect the documented checkout location or ask the user. "
            "Declare the verified absolute directory in root memory/MEMORY.md; "
            "do not create a replacement or silently fall back."
        )
        shared_resolved, shared_error = declared_shared(memory_text, memory_root)
        shared_available = shared_resolved is not None
        shared_git_backed = bool(shared_resolved and (shared_resolved / ".git").exists())
        shared_memory = shared_resolved / "MEMORY.md" if shared_resolved else None
        shared_text = None
        if shared_error:
            errors.append(f"root memory Shared {shared_error}. {discovery_hint}")
        elif shared_memory is not None:
            shared_text, shared_error = read_text(shared_memory)
            if shared_error:
                errors.append(f"shared conventions unavailable: {shared_error}. {discovery_hint}")
            elif not re.search(r"(?m)^## Mandatory conventions[ \t]*$", shared_text or ""):
                errors.append(
                    f"shared conventions malformed: {shared_memory} lacks `## Mandatory conventions`. {discovery_hint}"
                )

        repair_paths: list[Path] = []
        for error in errors:
            if error.startswith("root memory"):
                repair_paths.append(root_memory)
            if error.startswith("root RULES.md"):
                repair_paths.append(root_rules)
            if error.startswith("root STRUCTURE.md"):
                repair_paths.append(expected_structure)
            if error.startswith("shared conventions") and shared_memory is not None:
                repair_paths.append(root_memory)
                repair_paths.append(shared_memory)

        return RootState(
            home=self.home,
            memory_root=memory_root,
            root_memory=root_memory,
            root_rules=root_rules,
            structure=expected_structure,
            migration=migration,
            shared_resolved=shared_resolved,
            shared_available=shared_available,
            shared_git_backed=shared_git_backed,
            shared_memory=shared_memory,
            shared_text=shared_text,
            memory_text=memory_text,
            rules_text=rules_text,
            applied=applied,
            canonical=canonical,
            stale=stale,
            errors=errors,
            repair_paths=repair_paths,
            agent=agent,
        )

    def context_text(self, state: RootState) -> str:
        lines = [
            "ROOT MEMORY CONTROL — authoritative sources loaded by hook.",
            "This is not a duplicate memory system. The files below remain the authority.",
            f"Root memory: {state.root_memory}",
            f"Root rules: {state.root_rules}",
            f"Canonical structure: {state.structure}",
            f"Declared shared-memory directory: {state.shared_resolved or 'unavailable; repair the Shared header'}",
        ]

        if state.shared_available:
            backing = "Git-backed" if state.shared_git_backed else "not detected as Git-backed"
            lines.append(f"Shared-memory insertion: available ({backing}).")
        else:
            lines.append("Shared-memory insertion: unavailable; do not claim persistence there.")

        if state.errors:
            lines.append("CONTROL ERROR: " + " | ".join(state.errors))
            lines.append(
                "Do not mutate scoped memory until the root authority is repaired. "
                "Reading/repairing the root control files is allowed."
            )
        elif state.stale:
            lines.append(
                f"PROTOCOL STALE: applied {state.applied} != canonical {state.canonical}. "
                f"Read and apply {state.migration} before ordinary memory work, then update only this agent's root marker."
            )
        else:
            lines.append(f"Protocol status: current ({state.canonical}).")

        if state.memory_text is not None:
            lines.extend(("", "--- BEGIN ROOT memory/MEMORY.md ---", state.memory_text.rstrip(), "--- END ROOT memory/MEMORY.md ---"))
        if state.rules_text is not None:
            lines.extend(("", "--- BEGIN ROOT RULES.md ---", state.rules_text.rstrip(), "--- END ROOT RULES.md ---"))
        if state.shared_text is not None:
            lines.extend((
                "",
                "--- BEGIN SHARED MEMORY.md ---",
                state.shared_text.rstrip(),
                "--- END SHARED MEMORY.md ---",
            ))

        lines.extend(
            (
                "",
                "Mandatory use: treat the injected files as current authority. For "
                "memory work, follow their control, scope, and paired-log rules; "
                "narrowly commit and push required shared updates. Artifact uploads "
                "do not replace shared insertion. Do not manufacture memory edits for "
                "unrelated work.",
            )
        )
        return "\n".join(lines)

    def pre_memory_pointer_text(self) -> str:
        """The one-line Pi pointer that replaces the full root bundle at start."""
        return PRE_MEMORY_POINTER

    def pre_memory_feature_text(self) -> str:
        """The `pre_memory` feature summary appended to its returned catalog."""
        return PRE_MEMORY_FEATURE_TEXT

    def pre_memory_read_paths(self, state: RootState) -> tuple[list[Path], list[Path]]:
        """Split the convention sources into root reads and project groups."""
        root_reads = [state.root_memory, state.root_rules]
        local = state.memory_root / "local" / "MEMORY.md"
        if local.is_file():
            root_reads.append(local)
        if state.shared_memory is not None:
            root_reads.append(state.shared_memory)
        groups: list[Path] = []
        roots = [state.memory_root]
        if state.shared_resolved is not None:
            roots.append(state.shared_resolved)
        for root in roots:
            groups.extend(sorted(root.glob("**/submemory/*/MEMORY.md")))
        return root_reads, groups

    def control_status_text(self, state: RootState) -> str:
        """The one-line authority status that precedes a catalog."""
        if state.errors:
            return (
                "CONTROL ERROR: "
                + " | ".join(state.errors)
                + " Do not mutate scoped memory until the root authority is repaired."
            )
        if state.stale:
            return (
                f"PROTOCOL STALE: applied {state.applied} != canonical "
                f"{state.canonical}. Read and apply {state.migration} before "
                "ordinary memory work."
            )
        return f"Protocol status: current ({state.canonical})."

    def catalog_text(self, state: RootState) -> str:
        """The read catalog: the sources to read, never their inlined bodies.

        The conventions are deliberately absent so acknowledgment cannot be a
        passive read of injected tool output; the model must open each source
        with its read tool, and the mutation gate checks that it did. The
        declared shared tree is read with `tree` instead of a file read.
        """
        root_reads, groups = self.pre_memory_read_paths(state)
        lines = [self.control_status_text(state), ""]
        lines.extend(
            (
                "ROOT MEMORY CATALOG — the conventions are not inlined here.",
                "Read every source below with your read tool before any gated "
                "action; the gate records each read and stays closed until every "
                "source is current.",
                "",
                "Required root reads (read every path):",
            )
        )
        lines.extend(f"- {path}" for path in root_reads)
        lines.extend(
            ("", "Project conventions (read every group that applies to the project you are working on):")
        )
        lines.extend(
            [f"- {path}" for path in groups]
            or ["- (none declared; the root reads above are the whole convention set)"]
        )
        if state.shared_resolved is not None:
            shared = state.shared_resolved
            lines.extend(
                (
                    "",
                    "Shared-memory structure (required): read the complete "
                    f"layout once with `tree {shared}`; if `tree` is not "
                    f"installed, use `find \"{shared}\" -print` (in a "
                    f"PowerShell tool: `Get-ChildItem -LiteralPath '{shared}' "
                    "-Recurse -Force`). The hook records it.",
                )
            )
        lines.extend(
            (
                "",
                "Navigation rules:",
                "1. Read root `memory/MEMORY.md` first: it declares `Structure:` and the `Shared:` half-root and splits the tree into the local and shared halves, both mandatory.",
                "2. Read root `RULES.md` and the shared `MEMORY.md`: their **Mandatory conventions** bind every task, memory work or not.",
                "3. Name the project or scope of the task, then read every applicable group `MEMORY.md` from the root through the target and obey its Mandatory conventions. Conventions are inherited from ancestor groups and are never taken from `nodes/`, `log/`, or an attachment.",
                "4. Load nodes on demand only: the group's `nodes/MEMORY.md` index, the active node, and any `requires_read` prerequisite it names, which is a hard prerequisite.",
                "5. Active `.md` files hold current state only; displaced state belongs in the paired `log/<file>.md`, which is historical and non-authoritative.",
            )
        )
        return "\n".join(lines)

    def subagent_catalog_text(self, state: RootState) -> str:
        """The read catalog plus the subagent read-only boundary."""
        return self.catalog_text(state) + "\n\n" + SUBAGENT_READ_BOUNDARY_TEXT
