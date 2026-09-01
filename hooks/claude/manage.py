#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory hooks for Claude Code."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

HOOK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOK_ROOT))

from manage_common import (  # noqa: E402
    backup_once,
    hook_groups,
    load_json,
    read_marker,
    remove_checkpoints,
    remove_install_backup,
    replace_owned_hooks,
    save_json,
    secure_dir,
    strip_owned_hooks,
)

AUTO_MEMORY_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
MARKER_NAME = "claude-root-memory-hook.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--home",
        default=os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"),
    )
    parser.add_argument(
        "--memory-home",
        help="Agent root containing memory/MEMORY.md, RULES.md, and STRUCTURE.md",
    )
    return parser.parse_args()


def marker_path(home: Path) -> Path:
    return home / ".agent-mem-struct" / MARKER_NAME


def marker_memory_home(marker: dict[str, Any]) -> Path | None:
    value = marker.get("memoryHome")
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve(strict=False)


def disable_native_memory(
    settings: dict[str, Any], marker: dict[str, Any]
) -> dict[str, Any]:
    environment = settings.setdefault("env", {})
    if not isinstance(environment, dict):
        raise SystemExit("Refusing to replace existing non-object `env` value")
    previous = marker.get("previousAutoMemoryDisable")
    if not isinstance(previous, dict) or "present" not in previous:
        previous = {
            "present": AUTO_MEMORY_ENV in environment,
            "value": environment.get(AUTO_MEMORY_ENV),
        }
    environment[AUTO_MEMORY_ENV] = "1"
    return previous


def restore_native_memory(settings: dict[str, Any], marker: dict[str, Any]) -> None:
    previous = marker.get("previousAutoMemoryDisable")
    environment = settings.get("env")
    if (
        not isinstance(previous, dict)
        or not isinstance(environment, dict)
        or environment.get(AUTO_MEMORY_ENV) != "1"
    ):
        return
    if previous.get("present") is True:
        environment[AUTO_MEMORY_ENV] = previous.get("value")
    else:
        environment.pop(AUTO_MEMORY_ENV, None)
    if not environment:
        settings.pop("env", None)


def install(home: Path, memory_home: Path, hook: Path) -> None:
    settings_path = home / "settings.json"
    marker_file = marker_path(home)
    secure_dir(marker_file.parent)
    backup_once(settings_path, marker_file.parent / "settings.before-first-install.json")

    previous_marker = read_marker(marker_file)
    previous_memory_home = marker_memory_home(previous_marker)
    if previous_memory_home is not None and previous_memory_home != memory_home:
        remove_checkpoints(previous_memory_home)

    settings = load_json(settings_path)
    previous_auto_memory = disable_native_memory(settings, previous_marker)
    replace_owned_hooks(settings, hook_groups("claude", home, memory_home, hook))
    save_json(settings_path, settings)
    save_json(
        marker_file,
        {
            "memoryHome": str(memory_home),
            "previousAutoMemoryDisable": previous_auto_memory,
        },
    )

    if settings.get("disableAllHooks") is True:
        print(
            "WARNING: disableAllHooks=true; installed hooks will not run.",
            file=sys.stderr,
        )
    root_memory = memory_home / "memory" / "MEMORY.md"
    if not root_memory.exists():
        print(f"WARNING: root memory is unavailable at {root_memory}.", file=sys.stderr)
    print(f"Installed additive Claude root-memory hooks into {settings_path}")
    print(f"Root memory authority: {memory_home}")
    print("Restart Claude Code and use /context to verify the injected root memory.")


def uninstall(home: Path, memory_home: Path) -> None:
    settings_path = home / "settings.json"
    marker_file = marker_path(home)
    marker = read_marker(marker_file)
    if settings_path.exists():
        settings = load_json(settings_path)
        strip_owned_hooks(settings)
        restore_native_memory(settings, marker)
        save_json(settings_path, settings)

    checkpoint_homes = {memory_home, marker_memory_home(marker)}
    marker_file.unlink(missing_ok=True)
    for checkpoint_home in checkpoint_homes:
        if checkpoint_home is not None:
            remove_checkpoints(checkpoint_home)
    remove_install_backup(home, "settings.before-first-install.json")
    print("Removed only agent-mem-struct Claude hook entries.")


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    memory_home = (
        Path(args.memory_home).expanduser().resolve(strict=False)
        if args.memory_home
        else home
    )
    hook = HOOK_ROOT / "root-memory-context.py"
    if not hook.exists():
        raise SystemExit(f"Shared hook not found: {hook}")
    if args.action == "install":
        install(home, memory_home, hook)
    else:
        uninstall(home, memory_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
