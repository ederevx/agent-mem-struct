#!/usr/bin/env python3
"""Additive root-memory loader/guard for Codex and Claude Code.

This hook does not create a second memory authority. It reads the existing
<agent-home>/memory/MEMORY.md control/index plus <agent-home>/RULES.md and
injects those exact sources into model context at supported lifecycle events.

On PreToolUse it blocks a subagent-attributed write into the agent memory tree,
fails closed for unknown or mutating actions when root control is invalid, and
requires hash-bound convention delivery and acknowledgment before action. A
stale Structure-Version is not hard-blocked for the owning session because
migration itself may require memory writes; instead the staleness is injected
as a mandatory migrate-first condition.

A spawned subagent receives the same root memory/rules context as the parent
at SubagentStart, read-only: it may read every source the parent reads, but
never owns a write, which PreToolUse enforces regardless of what the subagent
attempts.

On PreCompact a failed checkpoint refuses a manually requested compaction by
exit status, the only mechanism that event honors, and merely warns about an
automatic one so the session is never stranded at its context ceiling.
Stop applies the same convention receipt to read-only turns before completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

VERSION_RE = re.compile(r"(?m)^Structure-Version:\s*(\S+)\s*$")
STRUCTURE_RE = re.compile(r"(?m)^Structure:\s*(\S+)\s*$")
SHELL_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"rm\b|mv\b|cp\b|mkdir\b|rmdir\b|touch\b|"
    r"sed\s+-i\b|perl\s+-pi\b|patch\b|tee\b|"
    r"git\s+(?:apply|checkout|reset|clean)\b|"
    r"powershell\b[^\n]*(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item)"
    r")",
    re.IGNORECASE,
)
REDIRECT_RE = re.compile(r"(?:^|[^<])>{1,2}\s*[^&]", re.MULTILINE)
# An embedded script writes without a shell redirect, and a heredoc body sits on
# lines after the interpreter, so the two halves are matched over the whole
# command rather than one line. A write primitive is required, which keeps a
# read-only one-liner out of the guard.
INTERPRETER_RE = re.compile(
    r"(?:^|[;&|(]\s*)(?:python(?:3)?|perl|ruby|node)\b", re.IGNORECASE | re.MULTILINE
)
SCRIPT_MUTATION_RE = re.compile(
    r"write_text|write_bytes|writeFileSync|appendFileSync|unlinkSync|rmSync|"
    r"open\s*\([^)\n]*,\s*['\"][rwxab+]*[wxa+][rwxab+]*['\"]|"
    r"os\.(?:remove|unlink|rename|replace|mkdir|makedirs|rmdir)|"
    r"shutil\.(?:copy|copy2|copyfile|move|rmtree)|"
    r"File\.(?:write|delete|rename)|FileUtils\.",
    re.IGNORECASE,
)
TARGET_KEY_TOKENS = ("path", "file", "target", "dest", "command", "cmd", "patch", "cwd")
CHECKPOINT_TEXT_LIMIT = 3500
CHECKPOINT_MAX_AGE = 7 * 24 * 60 * 60
CHECKPOINT_RESTORE_MAX_AGE = 24 * 60 * 60
CHECKPOINT_TEMP_MAX_AGE = 60 * 60
CHECKPOINT_MAX_FILES = 256
RECEIPT_MAX_AGE = 7 * 24 * 60 * 60
RECEIPT_MAX_FILES = 512
SUPPORTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PreToolUse",
    "Stop",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--config-home")
    parser.add_argument("--canonical-root")
    return parser.parse_args()


def read_event() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except Exception as exc:
        return None, f"{path}: {exc}"


def under(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except Exception:
        return False


def all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


def target_strings(value: Any) -> Iterable[str]:
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        key_lower = str(key).lower()
        if isinstance(item, str) and any(token in key_lower for token in TARGET_KEY_TOKENS):
            yield item
        elif isinstance(item, dict):
            yield from target_strings(item)
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, dict):
                    yield from target_strings(child)


def path_from_string(value: str, cwd: Path) -> Path | None:
    text = value.strip().strip("'\"")
    if not text or "\n" in text or len(text) > 4096:
        return None
    text = os.path.expandvars(os.path.expanduser(text))
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate


def root_state(home: Path, canonical_root: Path) -> dict[str, Any]:
    memory_root = home / "memory"
    root_memory = memory_root / "MEMORY.md"
    root_rules = home / "RULES.md"
    expected_structure = home / "STRUCTURE.md"
    canonical_rules = canonical_root / "RULES.md"
    canonical_structure = canonical_root / "STRUCTURE.md"

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

    canonical_dir = canonical_root
    migration = canonical_dir / "MIGRATION.md"
    stale = bool(applied and canonical and applied != canonical)

    shared = memory_root / "shared"
    shared_resolved = shared.resolve(strict=False)
    shared_available = shared_resolved.is_dir()
    shared_git_backed = (shared_resolved / ".git").exists()
    shared_memory = shared_resolved / "MEMORY.md"
    shared_text, shared_error = read_text(shared_memory)
    if shared_error:
        errors.append(f"shared conventions unavailable: {shared_error}")
    elif "## Mandatory conventions" not in shared_text:
        errors.append(
            f"shared conventions malformed: {shared_memory} lacks `## Mandatory conventions`"
        )

    repair_paths: list[Path] = []
    for error in errors:
        if error.startswith("root memory"):
            repair_paths.append(root_memory)
        if error.startswith("root RULES.md"):
            repair_paths.append(root_rules)
        if error.startswith("root STRUCTURE.md"):
            repair_paths.append(expected_structure)
        if error.startswith("shared conventions"):
            repair_paths.append(shared_memory)

    return {
        "home": home,
        "memory_root": memory_root,
        "root_memory": root_memory,
        "root_rules": root_rules,
        "structure": expected_structure,
        "migration": migration,
        "shared": shared,
        "shared_resolved": shared_resolved,
        "shared_available": shared_available,
        "shared_git_backed": shared_git_backed,
        "shared_memory": shared_memory,
        "shared_text": shared_text,
        "memory_text": memory_text,
        "rules_text": rules_text,
        "applied": applied,
        "canonical": canonical,
        "stale": stale,
        "errors": errors,
        "repair_paths": repair_paths,
    }


def context_text(state: dict[str, Any]) -> str:
    lines = [
        "ROOT MEMORY CONTROL — authoritative sources loaded by hook.",
        "This is not a duplicate memory system. The files below remain the authority.",
        f"Root memory: {state['root_memory']}",
        f"Root rules: {state['root_rules']}",
        f"Canonical structure: {state['structure']}",
        f"Direct shared-memory alias: {state['shared']}",
        f"Resolved shared-memory target: {state['shared_resolved']}",
    ]

    if state["shared_available"]:
        backing = "Git-backed" if state["shared_git_backed"] else "not detected as Git-backed"
        lines.append(f"Shared-memory insertion: available ({backing}).")
    else:
        lines.append("Shared-memory insertion: unavailable; do not claim persistence there.")

    if state["errors"]:
        lines.append("CONTROL ERROR: " + " | ".join(state["errors"]))
        lines.append(
            "Do not mutate scoped memory until the root authority is repaired. "
            "Reading/repairing the root control files is allowed."
        )
    elif state["stale"]:
        lines.append(
            f"PROTOCOL STALE: applied {state['applied']} != canonical {state['canonical']}. "
            f"Read and apply {state['migration']} before ordinary memory work, then update only this agent's root marker."
        )
    else:
        lines.append(f"Protocol status: current ({state['canonical']}).")

    if state["memory_text"] is not None:
        lines.extend(("", "--- BEGIN ROOT memory/MEMORY.md ---", state["memory_text"].rstrip(), "--- END ROOT memory/MEMORY.md ---"))
    if state["rules_text"] is not None:
        lines.extend(("", "--- BEGIN ROOT RULES.md ---", state["rules_text"].rstrip(), "--- END ROOT RULES.md ---"))
    if state["shared_text"] is not None:
        lines.extend((
            "",
            "--- BEGIN SHARED MEMORY.md ---",
            state["shared_text"].rstrip(),
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


def turn_reminder_text(agent: str, state: dict[str, Any]) -> str:
    """Keep task boundaries current without duplicating root bodies."""
    lines = [
        "ROOT MEMORY TURN CHECK — the hook-loaded authority remains in force.",
    ]
    if state["errors"]:
        lines.extend((
            "CONTROL ERROR: " + " | ".join(state["errors"]),
            "Do not mutate scoped memory until root control is repaired.",
        ))
    elif state["stale"]:
        lines.append(
            f"PROTOCOL STALE: applied {state['applied']} != canonical "
            f"{state['canonical']}; apply {state['migration']} before memory work."
        )
    lines.append(
        "For each substantive new task, follow the already-loaded root rules and "
        "read the shared scope before relevant nodes. The first mutation or turn "
        "completion for a new convention digest is intentionally refused once; "
        "read the injected bundle and retry to acknowledge it."
    )
    if agent == "codex":
        lines.append(
            "Codex native AGENTS.md instruction discovery remains active. Its "
            "generated memories are disabled for this integration; do not treat "
            "$CODEX_HOME/memories/ as a second persistence authority."
        )
    else:
        lines.append(
            "Claude native auto memory is disabled for this integration; do not "
            "treat its storage directory as a second authority."
        )
    return "\n".join(lines)


def config_is_active(agent: str, config_home: Path | None) -> bool:
    if config_home is None:
        return True
    environment = "CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"
    default = ".codex" if agent == "codex" else ".claude"
    configured = os.environ.get(environment)
    active = Path(configured).expanduser() if configured else Path.home() / default
    try:
        return os.path.normcase(str(active.resolve(strict=False))) == os.path.normcase(
            str(config_home.resolve(strict=False))
        )
    except OSError:
        return False


def safe_identity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))
    return text[:160] or "unknown"


def checkpoint_dir(state: dict[str, Any]) -> Path:
    return state["home"] / ".agent-mem-struct" / "compaction-checkpoints"


def convention_receipt_dir(state: dict[str, Any]) -> Path:
    return state["home"] / ".agent-mem-struct" / "convention-receipts"


def event_identity(event: dict[str, Any], state: dict[str, Any]) -> str | None:
    session = event.get("session_id") or event.get("sessionId")
    if state.get("agent") == "claude":
        turn = event.get("prompt_id") or event.get("promptId")
    else:
        turn = event.get("turn_id") or event.get("turnId")
    if not isinstance(session, str) or not session.strip():
        return None
    if not isinstance(turn, str) or not turn.strip():
        return None
    agent = event.get("agent_id") or event.get("agentId") or "parent"
    host = state.get("agent") or "unknown"
    raw = "\0".join(str(value) for value in (host, session, turn, agent))
    readable = "--".join(
        safe_identity(value)[:32] for value in (host, session, turn, agent)
    )
    return readable + "--" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def convention_receipt_path(event: dict[str, Any], state: dict[str, Any]) -> Path:
    identity = event_identity(event, state)
    if identity is None:
        expected = "prompt_id" if state.get("agent") == "claude" else "turn_id"
        raise ValueError(f"hook event lacks a stable session_id and {expected}")
    return convention_receipt_dir(state) / f"{identity}.json"


def remove_convention_receipt(event: dict[str, Any], state: dict[str, Any]) -> None:
    try:
        convention_receipt_path(event, state).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def prune_convention_receipts(state: dict[str, Any]) -> None:
    directory = convention_receipt_dir(state)
    now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    survivors: list[tuple[float, Path]] = []
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            modified = entry.stat().st_mtime
            age = now - modified
            if age > (CHECKPOINT_TEMP_MAX_AGE if ".tmp." in entry.name else RECEIPT_MAX_AGE):
                entry.unlink()
            elif entry.suffix == ".json":
                survivors.append((modified, entry))
        except OSError:
            continue
    survivors.sort(key=lambda item: item[0], reverse=True)
    for _, entry in survivors[RECEIPT_MAX_FILES:]:
        try:
            entry.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def manifest_chain(candidate: Path, state: dict[str, Any]) -> list[Path]:
    resolved = candidate.resolve(strict=False)
    roots = (
        (state["shared_resolved"], state["shared_resolved"]),
        ((state["memory_root"] / "local").resolve(strict=False), state["memory_root"] / "local"),
    )
    for resolved_root, display_root in roots:
        if not under(resolved, resolved_root):
            continue
        relative = resolved.relative_to(resolved_root)
        directory_parts = relative.parts[:-1] if candidate.suffix else relative.parts
        manifests = [display_root / "MEMORY.md"]
        current = display_root
        index = 0
        # Only a contiguous submemory/<name> chain denotes scoped groups.
        # Once traversal enters nodes/, log/, or an attachment, any MEMORY.md
        # there is a routing index or historical counterpart, not authority.
        while index + 1 < len(directory_parts) and directory_parts[index] == "submemory":
            current /= directory_parts[index]
            current /= directory_parts[index + 1]
            manifest = current / "MEMORY.md"
            if manifest.exists():
                manifests.append(manifest)
            index += 2
        return manifests
    return []


def required_reads(path: Path, state: dict[str, Any]) -> tuple[list[Path], str | None]:
    resolved = path.resolve(strict=False)
    for root in (state["memory_root"], state["shared_resolved"]):
        root_resolved = root.resolve(strict=False)
        if under(resolved, root_resolved) and "log" in resolved.relative_to(root_resolved).parts:
            # Logs are non-authoritative history and cannot add prerequisites.
            return [], None
    if path.suffix.lower() != ".md" or not path.exists():
        return [], None
    text, error = read_text(path)
    if error or text is None or not text.startswith("---\n"):
        return [], None
    end = text.find("\n---\n", 4)
    if end < 0:
        return [], None
    frontmatter = text[4:end]
    match = re.search(r"(?ms)^requires_read:\s*\n((?:\s+-\s+[^\n]+\n?)+)", frontmatter)
    if not match:
        return [], None
    result: list[Path] = []
    for value in re.findall(r"(?m)^\s+-\s+(.+?)\s*$", match.group(1)):
        required = Path(value.strip().strip("'\""))
        candidate = required if required.is_absolute() else path.parent / required
        resolved = candidate.resolve(strict=False)
        active_root = next(
            (
                root.resolve(strict=False)
                for root in (state["memory_root"], state["shared_resolved"])
                if under(resolved, root)
            ),
            None,
        )
        if active_root is None:
            return [], f"requires_read escapes the active memory roots: {candidate}"
        if "log" in resolved.relative_to(active_root).parts:
            return [], f"requires_read points to non-authoritative log memory: {candidate}"
        if candidate.suffix.lower() != ".md" or not candidate.is_file():
            return [], f"requires_read is not an active memory Markdown file: {candidate}"
        result.append(candidate)
    return result, None


def convention_bundle(
    event: dict[str, Any], state: dict[str, Any], *, scoped: bool
) -> tuple[str | None, str | None, list[Path], dict[str, str]]:
    paths = [state["shared_memory"]]
    prerequisite_error = None
    if scoped:
        tool_input = event.get("tool_input")
        cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
        if isinstance(tool_input, dict):
            for raw in target_strings(tool_input):
                for candidate in candidate_paths(raw, cwd):
                    paths.extend(manifest_chain(candidate, state))
                    prerequisites, error = required_reads(candidate, state)
                    paths.extend(prerequisites)
                    prerequisite_error = prerequisite_error or error

    if prerequisite_error:
        return None, prerequisite_error, [], {}

    unique: list[Path] = []
    seen: set[str] = set()
    chunks: list[str] = []
    source_digests: dict[str, str] = {}
    for path in paths:
        key = os.path.normcase(str(path.resolve(strict=False)))
        if key in seen:
            continue
        seen.add(key)
        text, error = read_text(path)
        if error or text is None:
            return None, f"required convention source unavailable: {error or path}", unique, {}
        if path.name == "MEMORY.md" and "## Mandatory conventions" not in text:
            return None, f"required convention manifest is malformed: {path}", unique, {}
        unique.append(path)
        source_digests[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks.append(f"--- BEGIN {path} ---\n{text.rstrip()}\n--- END {path} ---")
        prerequisites, error = required_reads(path, state)
        if error:
            return None, error, unique, {}
        paths.extend(prerequisites)
    body = "\n\n".join(chunks)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return digest, body, unique, source_digests


def read_receipt(event: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    path = convention_receipt_path(event, state)
    try:
        if path.parent.is_symlink() or not under(path.parent, state["home"]):
            return {}
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return {}
        if path.exists() and path.stat().st_size > 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def receipt_is_current(
    event: dict[str, Any], state: dict[str, Any], source_digests: dict[str, str]
) -> bool:
    acknowledged = read_receipt(event, state).get("sources")
    return isinstance(acknowledged, dict) and all(
        acknowledged.get(path) == digest for path, digest in source_digests.items()
    )


def acknowledge_receipt(
    event: dict[str, Any], state: dict[str, Any], source_digests: dict[str, str]
) -> None:
    directory = convention_receipt_dir(state)
    if directory.is_symlink() or not under(directory, state["home"]):
        raise OSError(f"unsafe convention receipt directory: {directory}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(directory.parent, 0o700)
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = convention_receipt_path(event, state)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"unsafe convention receipt path: {path}")
    prior = read_receipt(event, state).get("sources")
    sources = dict(prior) if isinstance(prior, dict) else {}
    sources.update(source_digests)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps({"sources": sources}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def convention_gate(
    event: dict[str, Any], state: dict[str, Any], *, scoped: bool
) -> tuple[bool, str]:
    if event_identity(event, state) is None:
        expected = "prompt_id" if state.get("agent") == "claude" else "turn_id"
        return False, (
            "Convention acknowledgment cannot be recorded: hook event lacks stable "
            f"session_id and {expected}."
        )
    digest, body, paths, source_digests = convention_bundle(event, state, scoped=scoped)
    if digest is None or body is None:
        return False, body or "required convention bundle could not be built"
    if receipt_is_current(event, state, source_digests):
        return True, ""
    try:
        acknowledge_receipt(event, state, source_digests)
    except (OSError, ValueError) as exc:
        return False, f"Convention acknowledgment could not be recorded safely: {exc}"
    listing = ", ".join(str(path) for path in paths)
    reason = (
        "Convention acknowledgment required before continuing. The authoritative "
        f"sources for this action are now injected ({listing}). Read and obey them, "
        "then retry the same action; that retry is the explicit acknowledgment of "
        f"this exact bundle (sha256:{digest}).\n\n{body}"
    )
    return False, reason


def checkpoint_path(event: dict[str, Any], state: dict[str, Any]) -> Path:
    session = event.get("session_id") or event.get("sessionId")
    identity = safe_identity(session)
    agent = event.get("agent_id") or event.get("agentId")
    if agent:
        identity += "--" + safe_identity(agent)
    return checkpoint_dir(state) / f"{identity}.json"


def remove_checkpoint(event: dict[str, Any], state: dict[str, Any]) -> None:
    path = checkpoint_path(event, state)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


def prune_checkpoints(state: dict[str, Any], keep: Path | None = None) -> None:
    directory = checkpoint_dir(state)
    now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    survivors: list[tuple[float, Path]] = []
    for entry in entries:
        try:
            if entry == keep or entry.is_symlink() or not entry.is_file():
                continue
            age = now - entry.stat().st_mtime
            limit = CHECKPOINT_TEMP_MAX_AGE if ".tmp." in entry.name else CHECKPOINT_MAX_AGE
            if age > limit:
                entry.unlink()
            elif entry.suffix == ".json":
                survivors.append((entry.stat().st_mtime, entry))
        except OSError:
            continue
    survivors.sort(key=lambda item: item[0], reverse=True)
    survivor_limit = CHECKPOINT_MAX_FILES - (1 if keep is not None and keep.exists() else 0)
    for _, entry in survivors[max(0, survivor_limit):]:
        if entry == keep:
            continue
        try:
            entry.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                kind = str(block.get("type") or "")
                if kind in {"text", "input_text", "output_text"}:
                    parts.append(str(block.get("text") or ""))
                elif kind == "tool_use":
                    parts.append(f"tool call: {block.get('name') or 'unknown'}")
                elif kind == "tool_result":
                    result = content_text(block.get("content"))
                    parts.append("tool result: " + result[:500])
        return "\n".join(part for part in parts if part)
    return ""


def transcript_entry(record: dict[str, Any]) -> str | None:
    if record.get("type") == "last-prompt" and record.get("lastPrompt"):
        return "LATEST USER OBJECTIVE: " + str(record["lastPrompt"])

    message = record.get("message")
    if isinstance(message, dict) and message.get("role") in {"user", "assistant"}:
        text = content_text(message.get("content"))
        if text:
            return f"{str(message['role']).upper()}: {text}"

    payload = record.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            text = content_text(payload.get("content"))
            if text:
                return f"{str(payload['role']).upper()}: {text}"
        if payload.get("type") in {"user_message", "agent_message"} and payload.get("message"):
            role = "USER" if payload.get("type") == "user_message" else "ASSISTANT"
            return f"{role}: {payload['message']}"
        if payload.get("type") in {"custom_tool_call", "function_call"}:
            return "TOOL CALL: " + str(payload.get("name") or "unknown")
        if payload.get("type") in {"custom_tool_call_output", "function_call_output"}:
            output = str(payload.get("output") or "")
            return "TOOL RESULT: " + output[:500] if output else None
    return None


def build_checkpoint(event: dict[str, Any]) -> str:
    raw_path = event.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("compaction event did not provide transcript_path")
    path = Path(raw_path).expanduser()
    entries: deque[str] = deque(maxlen=80)
    with path.open(encoding="utf-8", errors="replace") as transcript:
        for line in transcript:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            entry = transcript_entry(record)
            if entry:
                entries.append(re.sub(r"\s+", " ", entry).strip())

    if not entries:
        raise ValueError(f"no continuity anchors found in transcript {path}")
    selected: list[str] = []
    used = 0
    for entry in reversed(entries):
        clipped = entry[:1200]
        if selected and used + len(clipped) + 1 > CHECKPOINT_TEXT_LIMIT:
            continue
        selected.append(clipped)
        used += len(clipped) + 1
        if used >= CHECKPOINT_TEXT_LIMIT:
            break
    selected.reverse()
    return "\n".join(selected)


def save_checkpoint(event: dict[str, Any], state: dict[str, Any]) -> None:
    session_id = event.get("session_id") or event.get("sessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("compaction event did not provide a valid session_id")
    path = checkpoint_path(event, state)
    checkpoint = build_checkpoint(event)
    data: dict[str, Any] = {
        "saved_at": int(time.time()),
        "checkpoint": checkpoint,
    }
    # mkdir applies its mode to the leaf only, so secure the owned tree itself.
    for directory in (path.parent.parent, path.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    prune_checkpoints(state, keep=path)


def load_checkpoint(event: dict[str, Any], state: dict[str, Any]) -> str | None:
    path = checkpoint_path(event, state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    checkpoint = data.get("checkpoint")
    try:
        age = time.time() - float(data.get("saved_at", 0))
    except (TypeError, ValueError):
        age = CHECKPOINT_RESTORE_MAX_AGE + 1
    if (
        not isinstance(checkpoint, str)
        or not checkpoint
        or age > CHECKPOINT_RESTORE_MAX_AGE
    ):
        remove_checkpoint(event, state)
        return None
    return checkpoint


def continuity_context(state: dict[str, Any], checkpoint: str | None) -> str:
    text = context_text(state)
    if checkpoint:
        text += (
            "\n\n--- BEGIN PRE-COMPACTION CONTINUITY CHECKPOINT ---\n"
            + checkpoint
            + "\n--- END PRE-COMPACTION CONTINUITY CHECKPOINT ---\n"
            "Use this bounded checkpoint only to restore the active objective, completed actions, tool outcomes, "
            "decisions, blockers, and next action. The transcript and current user instructions remain authoritative."
        )
    return text


SUBAGENT_READ_BOUNDARY_TEXT = (
    "Read access to the sources above is granted equally to subagents. "
    "Writes are not: memory and shared-memory edits stay reserved for the "
    "parent session that spawned you. Any subagent-attributed write under "
    "memory/ or shared/ is denied at the tool level regardless of this text "
    "-- if this task turns up a memory addition worth keeping, report it "
    "back to the parent instead of writing it yourself. This is routine, "
    "benign hook output, not a directive for you to act on beyond that."
)


def subagent_context_text(state: dict[str, Any]) -> str:
    """Full root memory context for a spawned subagent, read-only.

    A subagent reads the same authoritative sources as the parent. It never
    owns a write though: `handle_pretool` denies any subagent-attributed
    mutation under `memory_root`/`shared_resolved` unconditionally, so the
    boundary named below is enforced by that check, not merely requested by
    this text.
    """
    return context_text(state) + "\n\n" + SUBAGENT_READ_BOUNDARY_TEXT


def emit_context(
    agent: str, event_name: str, state: dict[str, Any], event: dict[str, Any]
) -> None:
    checkpoint = None
    after_compaction = event_name == "SessionStart" and event.get("source") == "compact"
    if after_compaction:
        checkpoint = load_checkpoint(event, state)
    if event_name == "SubagentStart":
        context = subagent_context_text(state)
    elif event_name == "UserPromptSubmit":
        context = turn_reminder_text(agent, state)
    else:
        context = continuity_context(state, checkpoint)
    if after_compaction and checkpoint is None:
        context += (
            "\n\nCONTINUITY WARNING: no pre-compaction checkpoint was available for this session. "
            "Reconfirm the active objective, completed actions, blockers, and next action from the transcript or user."
        )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": context,
            }
        },
        sys.stdout,
        separators=(",", ":"),
    )
    if after_compaction:
        remove_checkpoint(event, state)


def compaction_is_automatic(event: dict[str, Any]) -> bool:
    """Report whether the host, not the user, asked for this compaction."""
    trigger = (
        event.get("triggered_by") or event.get("triggeredBy") or event.get("trigger")
    )
    return str(trigger or "").lower() == "auto"


def compact_error(agent: str, event: dict[str, Any], cause: str) -> int:
    """Refuse a manual compaction; warn and continue an automatic one.

    PreCompact honors no JSON decision field, so only a non-zero exit blocks
    it. Blocking an automatic compaction strands the session at the context
    ceiling, which destroys more continuity than the missing checkpoint does,
    so an automatic run is warned about instead. An unlabelled trigger is
    treated as manual.
    """
    if compaction_is_automatic(event):
        json.dump(
            {
                "systemMessage": (
                    "Automatic compaction is continuing without a continuity checkpoint: "
                    + cause
                    + ". Reconfirm the active objective after compaction."
                )
            },
            sys.stdout,
            separators=(",", ":"),
        )
        return 0
    reason = f"Compaction blocked: {cause}. Repair this, then compact again."
    if agent == "codex":
        json.dump({"continue": False, "stopReason": reason}, sys.stdout, separators=(",", ":"))
    print(reason, file=sys.stderr)
    return 2


def handle_precompact(agent: str, event: dict[str, Any], state: dict[str, Any]) -> int:
    if state["errors"]:
        return compact_error(
            agent, event, "root memory control is invalid. " + " | ".join(state["errors"])
        )
    try:
        save_checkpoint(event, state)
    except Exception as exc:
        return compact_error(
            agent, event, f"continuity checkpoint could not be saved: {exc}"
        )
    # The checkpoint is durable state for the compact-sourced SessionStart.
    # PreCompact systemMessage is UI feedback, not model context.
    return 0


READ_ONLY_TOOL_TOKENS = ("read", "view", "get", "list", "search", "find", "status")
SHELL_TOOL_NAMES = {"bash", "powershell", "shell", "exec_command", "command"}
SHELL_READ_ONLY_RE = re.compile(
    r"^\s*(?:pwd|ls|dir|cat|head|tail|stat|where|which|rg|grep|find|"
    r"Get-Content|Get-ChildItem|Select-String|Test-Path|Resolve-Path|"
    r"git\s+(?:status|diff|log|show|grep|rev-parse|branch\s+--list))\b",
    re.IGNORECASE,
)


def tool_requires_acknowledgment(tool_name: str, tool_input: dict[str, Any]) -> bool:
    name = tool_name.lower()
    if any(token in name for token in ("write", "edit", "patch", "delete", "remove", "rename", "move", "create", "update")):
        return True
    if name in SHELL_TOOL_NAMES or "shell" in name:
        command = "\n".join(all_strings(tool_input))
        if SHELL_MUTATION_RE.search(command) or REDIRECT_RE.search(command):
            return True
        if INTERPRETER_RE.search(command) and SCRIPT_MUTATION_RE.search(command):
            return True
        if re.search(r"[;&|`]|\$\(|\r|\n", command):
            return True
        return not bool(SHELL_READ_ONLY_RE.match(command))
    return not any(token in name for token in READ_ONLY_TOOL_TOKENS)


def candidate_paths(raw: str, cwd: Path) -> Iterable[Path]:
    direct = path_from_string(raw, cwd)
    if direct is not None:
        yield direct
    for token in re.split(r"[\s,;(){}\[\]|&<>]+", raw):
        candidate = path_from_string(token, cwd)
        if candidate is not None:
            yield candidate


def input_targets_memory(event: dict[str, Any], state: dict[str, Any]) -> tuple[bool, bool]:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return False, False
    cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
    memory_root: Path = state["memory_root"]
    root_memory: Path = state["root_memory"]
    shared_resolved: Path = state["shared_resolved"]

    found_memory = False
    found_nonroot = False
    for raw in target_strings(tool_input):
        for candidate in candidate_paths(raw, cwd):
            if under(candidate, memory_root) or under(candidate, shared_resolved):
                found_memory = True
                if candidate.resolve(strict=False) != root_memory.resolve(strict=False):
                    found_nonroot = True

    return found_memory, found_memory and not found_nonroot


def input_targets_only_repair_paths(event: dict[str, Any], state: dict[str, Any]) -> bool:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict) or not state["repair_paths"]:
        return False
    cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
    allowed = {
        os.path.normcase(str(path.resolve(strict=False))) for path in state["repair_paths"]
    }
    found = False
    for raw in target_strings(tool_input):
        candidates: list[Path] = []
        direct = path_from_string(raw, cwd)
        if direct is not None:
            candidates.append(direct)
        for value in re.findall(r"(?m)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", raw):
            candidate = path_from_string(value, cwd)
            if candidate is not None:
                candidates.append(candidate)
        for candidate in candidates:
            found = True
            if os.path.normcase(str(candidate.resolve(strict=False))) not in allowed:
                return False
    return found


def is_subagent_event(event: dict[str, Any]) -> bool:
    agent = event.get("agent_id") or event.get("agentId")
    return isinstance(agent, str) and bool(agent.strip())


def handle_pretool(event: dict[str, Any], state: dict[str, Any]) -> None:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    targets_memory, _ = input_targets_memory(event, state)
    if targets_memory and is_subagent_event(event):
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Subagents have read-only access to memory/shared content. "
                        "Route this addition back to the parent session instead of "
                        "writing it directly."
                    ),
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        return

    if state["errors"] and not input_targets_only_repair_paths(event, state):
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Mutation blocked because root memory control or mandatory shared conventions "
                        "are unavailable, malformed, or differ from the canonical hook checkout. "
                        + " | ".join(state["errors"])
                        + ". Repair/read the root control files first."
                    ),
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        return

    if state["errors"]:
        return

    allowed, reason = convention_gate(event, state, scoped=targets_memory)
    if not allowed:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                    "additionalContext": reason,
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        return

    if state["stale"]:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        f"Memory protocol is stale ({state['applied']} -> {state['canonical']}). "
                        f"The authoritative RULES.md requires applying {state['migration']} before ordinary memory work. "
                        "If this tool call is part of that migration, continue according to MIGRATION.md; otherwise migrate first."
                    ),
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )


def handle_stop(event: dict[str, Any], state: dict[str, Any]) -> None:
    # Both hosts set this flag when re-entering Stop/SubagentStop after a hook
    # already continued the turn. A second block can form an unbounded loop,
    # including when root state or the event identity cannot be repaired.
    if event.get("stop_hook_active") is True or event.get("stopHookActive") is True:
        return
    if state["errors"]:
        reason = (
            "Turn completion blocked because root memory control or mandatory shared "
            "conventions are invalid. " + " | ".join(state["errors"])
        )
        json.dump({"decision": "block", "reason": reason}, sys.stdout, separators=(",", ":"))
        return
    allowed, reason = convention_gate(event, state, scoped=False)
    if not allowed:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, separators=(",", ":"))


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    config_home = (
        Path(args.config_home).expanduser().resolve(strict=False)
        if args.config_home
        else None
    )
    canonical_root = (
        Path(args.canonical_root).expanduser().resolve(strict=False)
        if args.canonical_root
        else Path(__file__).resolve().parents[1]
    )
    if not config_is_active(args.agent, config_home):
        return 0
    event = read_event()
    event_name = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    if event_name not in SUPPORTED_EVENTS:
        return 0
    if event_name == "PreToolUse":
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not tool_requires_acknowledgment(
            str(event.get("tool_name") or ""), tool_input
        ):
            return 0
    state = root_state(home, canonical_root)
    state["agent"] = args.agent

    if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
        prune_convention_receipts(state)
        if not (event_name == "SessionStart" and event.get("source") == "compact"):
            remove_convention_receipt(event, state)
        if event_name != "UserPromptSubmit":
            keep = (
                checkpoint_path(event, state)
                if event_name == "SessionStart" and event.get("source") == "compact"
                else None
            )
            prune_checkpoints(state, keep=keep)
        emit_context(args.agent, event_name, state, event)
        return 0
    if event_name == "PreCompact":
        return handle_precompact(args.agent, event, state)
    if event_name == "PreToolUse":
        handle_pretool(event, state)
        return 0
    if event_name in {"Stop", "SubagentStop"}:
        handle_stop(event, state)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
