#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory hooks for Codex only."""
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
    p.add_argument("--home", default=os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
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


def protocol_groups(home: Path, hook: Path) -> dict[str, list[dict[str, Any]]]:
    command = f"{quote(sys.executable)} {quote(str(hook))} --agent codex --home {quote(str(home))}"
    return {
        "SessionStart": [{"hooks": [handler(command, "load root at session start")]}],
        "UserPromptSubmit": [{"hooks": [handler(command, "refresh root each turn")]}],
        "SubagentStart": [{"hooks": [handler(command, "load root for subagent")]}],
        "PreToolUse": [{"matcher": "*", "hooks": [handler(command, "guard memory mutation")]}],
    }


def install(home: Path, hook: Path) -> None:
    hooks_path = home / "hooks.json"
    state_dir = home / ".agent-mem-struct"
    state_dir.mkdir(parents=True, exist_ok=True)
    backup = state_dir / "hooks.before-first-install.json"
    if hooks_path.exists() and not backup.exists():
        shutil.copy2(hooks_path, backup)

    data = load(hooks_path)
    strip_owned(data)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("Refusing to replace existing non-object `hooks` value")
    for event, groups in protocol_groups(home, hook).items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise SystemExit(f"Refusing to replace existing non-array hooks.{event}")
        current.extend(groups)
    save(hooks_path, data)
    (state_dir / "codex-root-memory-hook.json").write_text(
        json.dumps({"hook": str(hook), "hooks_json": str(hooks_path)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Installed additive Codex root-memory hooks into {hooks_path}")
    print("Restart Codex and review/trust the new user hook in /hooks if your build requires hook trust.")


def uninstall(home: Path) -> None:
    hooks_path = home / "hooks.json"
    if hooks_path.exists():
        data = load(hooks_path)
        strip_owned(data)
        save(hooks_path, data)
    marker = home / ".agent-mem-struct" / "codex-root-memory-hook.json"
    if marker.exists():
        marker.unlink()
    print("Removed only agent-mem-struct Codex hook entries.")


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    hook = Path(__file__).resolve().parents[1] / "root-memory-context.py"
    if not hook.exists():
        raise SystemExit(f"Shared hook not found: {hook}")
    if args.action == "install":
        install(home, hook)
    else:
        uninstall(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
