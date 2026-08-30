#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory hooks for Claude Code only."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

OWNER_PREFIX = "agent-mem-struct root memory:"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("install", "uninstall"))
    p.add_argument("--home", default=os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
    p.add_argument(
        "--memory-home",
        default=None,
        help=(
            "Agent root holding memory/MEMORY.md, RULES.md, and STRUCTURE.md. "
            "Defaults to --home. Set this when Claude's config home and the "
            "memory root are different directories."
        ),
    )
    return p.parse_args()


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Refusing to modify invalid JSON at {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"Refusing to modify non-object JSON at {path}")
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".agent-mem-struct.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def owned(handler: Any) -> bool:
    return isinstance(handler, dict) and str(handler.get("statusMessage", "")).startswith(OWNER_PREFIX)


def strip_owned(settings: dict[str, Any]) -> None:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept = [handler for handler in handlers if not owned(handler)]
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                kept_groups.append(new_group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)


def handler(command: str, label: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": command,
        "timeout": 5,
        "statusMessage": f"{OWNER_PREFIX} {label}",
    }


def protocol_groups(memory_home: Path, hook: Path) -> dict[str, list[dict[str, Any]]]:
    command = f"{quote(sys.executable)} {quote(str(hook))} --agent claude --home {quote(str(memory_home))}"
    return {
        "SessionStart": [{"hooks": [handler(command, "load root at session start")]}],
        "UserPromptSubmit": [{"hooks": [handler(command, "refresh root each turn")]}],
        "SubagentStart": [{"hooks": [handler(command, "load root for subagent")]}],
        "PreCompact": [{"hooks": [handler(command, "checkpoint context before compaction")]}],
        "PostCompact": [{"hooks": [handler(command, "record compaction result")]}],
        "PreToolUse": [{"matcher": "*", "hooks": [handler(command, "guard memory mutation")]}],
    }


def checkpoint_home_from_marker(home: Path) -> Path | None:
    marker = home / ".agent-mem-struct" / "claude-root-memory-hook.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("memoryHome") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve(strict=False)


def remove_checkpoints(memory_home: Path) -> None:
    shutil.rmtree(
        memory_home / ".agent-mem-struct" / "compaction-checkpoints",
        ignore_errors=True,
    )


def install(home: Path, memory_home: Path, hook: Path) -> None:
    settings_path = home / "settings.json"
    state_dir = home / ".agent-mem-struct"
    state_dir.mkdir(parents=True, exist_ok=True)
    backup = state_dir / "settings.before-first-install.json"
    if settings_path.exists() and not backup.exists():
        shutil.copy2(settings_path, backup)

    previous_memory_home = checkpoint_home_from_marker(home)
    if previous_memory_home is not None and previous_memory_home != memory_home:
        remove_checkpoints(previous_memory_home)

    data = load(settings_path)
    strip_owned(data)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("Refusing to replace existing non-object `hooks` value")
    for event, groups in protocol_groups(memory_home, hook).items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise SystemExit(f"Refusing to replace existing non-array hooks.{event}")
        current.extend(groups)
    save(settings_path, data)
    (state_dir / "claude-root-memory-hook.json").write_text(
        json.dumps(
            {"hook": str(hook), "settings": str(settings_path), "memoryHome": str(memory_home)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if data.get("disableAllHooks") is True:
        print("WARNING: disableAllHooks=true is already configured; this installer preserved it, so hooks will not run.", file=sys.stderr)
    root_memory = memory_home / "memory" / "MEMORY.md"
    if not root_memory.exists():
        print(
            f"WARNING: no root memory found at {root_memory}. "
            "The hook will report a control error until it exists; pass --memory-home to point at the agent root.",
            file=sys.stderr,
        )
    print(f"Installed additive Claude root-memory hooks into {settings_path}")
    print(f"Root memory authority: {memory_home}")
    print("Restart Claude Code and use /context to verify the injected root memory/rules are visible during a turn.")


def uninstall(home: Path, memory_home: Path) -> None:
    settings_path = home / "settings.json"
    if settings_path.exists():
        data = load(settings_path)
        strip_owned(data)
        save(settings_path, data)
    marker = home / ".agent-mem-struct" / "claude-root-memory-hook.json"
    checkpoint_homes = {memory_home}
    installed_memory_home = checkpoint_home_from_marker(home)
    if installed_memory_home is not None:
        checkpoint_homes.add(installed_memory_home)
    if marker.exists():
        marker.unlink()
    for checkpoint_home in checkpoint_homes:
        remove_checkpoints(checkpoint_home)
    print("Removed only agent-mem-struct Claude hook entries.")


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    memory_home = Path(args.memory_home).expanduser().resolve(strict=False) if args.memory_home else home
    hook = Path(__file__).resolve().parents[1] / "root-memory-context.py"
    if not hook.exists():
        raise SystemExit(f"Shared hook not found: {hook}")
    if args.action == "install":
        install(home, memory_home, hook)
    else:
        uninstall(home, memory_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
