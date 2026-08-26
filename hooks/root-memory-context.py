#!/usr/bin/env python3
"""Additive root-memory loader/guard for Codex and Claude Code.

This hook does not create a second memory authority. It reads the existing
<agent-home>/memory/MEMORY.md control/index plus <agent-home>/RULES.md and
injects those exact sources into model context at supported lifecycle events.

On PreToolUse it only blocks writes into the agent memory tree when the root
control authority is missing or malformed. A stale Structure-Version is not
hard-blocked because migration itself may require memory writes; instead the
staleness is injected as a mandatory migrate-first condition.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

VERSION_RE = re.compile(r"(?m)^Structure-Version:\s*(\S+)\s*$")
STRUCTURE_RE = re.compile(r"(?m)^Structure:\s*(\S+)\s*$")
SHELL_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"rm\b|mv\b|cp\b|mkdir\b|rmdir\b|touch\b|"
    r"sed\s+-i\b|perl\s+-pi\b|patch\b|tee\b|"
    r"git\s+(?:apply|checkout|reset|clean)\b|"
    r"python(?:3)?\b[^\n]*(?:write_text|write_bytes)|"
    r"powershell\b[^\n]*(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item)"
    r")",
    re.IGNORECASE,
)
REDIRECT_RE = re.compile(r"(?:^|[^<])>{1,2}\s*[^&]", re.MULTILINE)
TARGET_KEY_TOKENS = ("path", "file", "target", "dest", "command", "cmd", "patch", "cwd")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    parser.add_argument("--home", required=True)
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


def root_state(home: Path) -> dict[str, Any]:
    memory_root = home / "memory"
    root_memory = memory_root / "MEMORY.md"
    root_rules = home / "RULES.md"
    expected_structure = home / "STRUCTURE.md"

    memory_text, memory_error = read_text(root_memory)
    rules_text, rules_error = read_text(root_rules)
    structure_text, structure_error = read_text(expected_structure)

    errors: list[str] = []
    if memory_error:
        errors.append(f"root memory unavailable: {memory_error}")
    if rules_error:
        errors.append(f"root RULES.md unavailable: {rules_error}")
    if structure_error:
        errors.append(f"root STRUCTURE.md unavailable: {structure_error}")

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

    if structure_text is not None:
        version_match = VERSION_RE.search(structure_text)
        if not version_match:
            errors.append("canonical STRUCTURE.md is missing `Structure-Version:`")
        else:
            canonical = version_match.group(1)

    canonical_dir = expected_structure.resolve(strict=False).parent
    migration = canonical_dir / "MIGRATION.md"
    stale = bool(applied and canonical and applied != canonical)

    shared = memory_root / "shared"
    shared_resolved = shared.resolve(strict=False)
    shared_available = shared_resolved.is_dir()
    shared_git_backed = (shared_resolved / ".git").exists()

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
        "memory_text": memory_text,
        "rules_text": rules_text,
        "applied": applied,
        "canonical": canonical,
        "stale": stale,
        "errors": errors,
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

    lines.extend(
        (
            "",
            "Mandatory use: treat the injected root MEMORY.md and RULES.md as current context. "
            "For any memory task, follow their control/version/scope rules before reading or changing scoped nodes. "
            "When the user or an applicable scoped convention requires durable cross-agent capture and shared-memory "
            "insertion is available, update the resolved shared tree directly during the turn, preserve paired semantic "
            "logs, and narrowly commit and push the change. Artifact uploads do not substitute for memory insertion; "
            "keep raw dumps and full logs in artifact storage. For non-memory tasks, use root memory only when relevant; "
            "do not manufacture memory edits.",
        )
    )
    return "\n".join(lines)


def emit_context(event_name: str, state: dict[str, Any]) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": context_text(state),
            }
        },
        sys.stdout,
        separators=(",", ":"),
    )


def tool_is_mutating(tool_name: str, tool_input: dict[str, Any]) -> bool:
    name = tool_name.lower()
    if any(token in name for token in ("write", "edit", "patch", "delete", "remove", "rename", "move", "create", "update")):
        return True
    if name in {"bash", "powershell", "shell", "exec_command", "command"} or "shell" in name:
        command = "\n".join(all_strings(tool_input))
        return bool(SHELL_MUTATION_RE.search(command) or REDIRECT_RE.search(command))
    return False


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


def handle_pretool(event: dict[str, Any], state: dict[str, Any]) -> None:
    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict) or not tool_is_mutating(tool_name, tool_input):
        return
    targets_memory, root_only = input_targets_memory(event, state)
    if not targets_memory:
        return

    if state["errors"] and not root_only:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Memory mutation blocked because root memory control is unavailable or malformed. "
                        + " | ".join(state["errors"])
                        + ". Repair/read the root control files first."
                    ),
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


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    event = read_event()
    event_name = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    state = root_state(home)

    if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
        emit_context(event_name, state)
        return 0
    if event_name == "PreToolUse":
        handle_pretool(event, state)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
