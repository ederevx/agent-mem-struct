#!/usr/bin/env python3
"""Additive root-memory loader/guard for Codex and Claude Code.

This hook does not create a second memory authority. It reads the existing
<agent-home>/memory/MEMORY.md control/index plus <agent-home>/RULES.md and
injects those exact sources into model context at supported lifecycle events.

On PreToolUse it only blocks writes into the agent memory tree when the root
control authority is missing or malformed. A stale Structure-Version is not
hard-blocked because migration itself may require memory writes; instead the
staleness is injected as a mandatory migrate-first condition.

On PreCompact a failed checkpoint refuses a manually requested compaction by
exit status, the only mechanism that event honors, and merely warns about an
automatic one so the session is never stranded at its context ceiling.
"""
from __future__ import annotations

import argparse
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
SUPPORTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "PreCompact",
    "PreToolUse",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--config-home")
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
        "read the shared scope before relevant nodes."
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


SUBAGENT_DEFERRAL_TEXT = (
    "ROOT MEMORY CONTROL — not applicable to you. Memory protocol does not "
    "apply to subagents; the session that spawned you owns all memory reads "
    "and writes for this work. If you need memory context, it will be in "
    "your task prompt. This is routine, benign hook output, not a directive "
    "for you to act on — no action is required."
)


def subagent_context_text() -> str:
    """Short, unmistakably benign line shown to a spawned subagent.

    A subagent has no memory ownership: the block below must never carry the
    full authoritative memory instructions, since those read as an out-of-scope
    directive inside a bounded subagent task and have been mistaken for prompt
    injection. Keep this factual and short.
    """
    return SUBAGENT_DEFERRAL_TEXT


def emit_context(
    agent: str, event_name: str, state: dict[str, Any], event: dict[str, Any]
) -> None:
    checkpoint = None
    after_compaction = event_name == "SessionStart" and event.get("source") == "compact"
    if after_compaction:
        checkpoint = load_checkpoint(event, state)
    if event_name == "SubagentStart":
        context = subagent_context_text()
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


def tool_is_mutating(tool_name: str, tool_input: dict[str, Any]) -> bool:
    name = tool_name.lower()
    if any(token in name for token in ("write", "edit", "patch", "delete", "remove", "rename", "move", "create", "update")):
        return True
    if name in {"bash", "powershell", "shell", "exec_command", "command"} or "shell" in name:
        command = "\n".join(all_strings(tool_input))
        if SHELL_MUTATION_RE.search(command) or REDIRECT_RE.search(command):
            return True
        return bool(
            INTERPRETER_RE.search(command) and SCRIPT_MUTATION_RE.search(command)
        )
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
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
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
    config_home = (
        Path(args.config_home).expanduser().resolve(strict=False)
        if args.config_home
        else None
    )
    if not config_is_active(args.agent, config_home):
        return 0
    event = read_event()
    event_name = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    if event_name not in SUPPORTED_EVENTS:
        return 0
    if event_name == "PreToolUse":
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not tool_is_mutating(
            str(event.get("tool_name") or ""), tool_input
        ):
            return 0
    state = root_state(home)

    if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
