"""Shared installer helpers for the root-memory hook integrations."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

OWNER_PREFIX = "agent-mem-struct root memory:"
EVENT_LABELS = {
    "SessionStart": "load root at session start",
    "UserPromptSubmit": "remind root each turn",
    "SubagentStart": "load root for subagent",
    "PreCompact": "checkpoint context before compaction",
    "PreToolUse": "guard memory mutation",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Refusing to modify invalid JSON at {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"Refusing to modify non-object JSON at {path}")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".agent-mem-struct.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_marker(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def backup_once(source: Path, backup: Path) -> None:
    if source.exists() and not backup.exists():
        shutil.copy2(source, backup)


def remove_checkpoints(home: Path) -> None:
    shutil.rmtree(
        home / ".agent-mem-struct" / "compaction-checkpoints",
        ignore_errors=True,
    )


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def strip_owned_hooks(settings: dict[str, Any]) -> None:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept = [
                handler
                for handler in group["hooks"]
                if not (
                    isinstance(handler, dict)
                    and str(handler.get("statusMessage", "")).startswith(OWNER_PREFIX)
                )
            ]
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event)
    if not hooks:
        settings.pop("hooks", None)


def hook_groups(
    agent: str,
    config_home: Path,
    memory_home: Path,
    hook: Path,
) -> dict[str, list[dict[str, Any]]]:
    command = (
        f"{_quote(sys.executable)} {_quote(str(hook))} --agent {agent} "
        f"--home {_quote(str(memory_home))} "
        f"--config-home {_quote(str(config_home))}"
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for event, label in EVENT_LABELS.items():
        handler = {
            "type": "command",
            "command": command,
            "timeout": 5,
            "statusMessage": f"{OWNER_PREFIX} {label}",
        }
        group: dict[str, Any] = {"hooks": [handler]}
        if event == "PreToolUse":
            group["matcher"] = "*"
        groups[event] = [group]
    return groups


def replace_owned_hooks(
    settings: dict[str, Any], groups: dict[str, list[dict[str, Any]]]
) -> None:
    strip_owned_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("Refusing to replace existing non-object `hooks` value")
    for event, additions in groups.items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise SystemExit(f"Refusing to replace existing non-array hooks.{event}")
        current.extend(additions)
