#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory hooks for Codex only."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

OWNER_PREFIX = "agent-mem-struct root memory:"
MEMORY_COMMENT = "# agent-mem-struct: native Codex memories disabled"
MEMORY_LINE = f"memories = false  {MEMORY_COMMENT}"
FEATURE_HEADER_RE = re.compile(r"^\s*\[features\]\s*(?:#.*)?$")
ANY_HEADER_RE = re.compile(r"^\s*\[")
MEMORY_KEY_RE = re.compile(r"^\s*memories\s*=")
DOT_MEMORY_KEY_RE = re.compile(r"^\s*features\.memories\s*=")


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


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".agent-mem-struct.tmp")
    tmp.write_text(text, encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def marker_data(home: Path) -> dict[str, Any]:
    marker = home / ".agent-mem-struct" / "codex-root-memory-hook.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def disable_native_memories(
    text: str, marker: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Refusing to modify invalid TOML in config.toml: {exc}")
    previous = marker.get("previousNativeMemories")
    if isinstance(previous, dict) and MEMORY_LINE in text:
        return text, previous

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if DOT_MEMORY_KEY_RE.match(line):
            previous = {"style": "dotted", "line": line}
            lines[index] = f"features.{MEMORY_LINE}\n"
            return "".join(lines), previous

    feature_start = next(
        (index for index, line in enumerate(lines) if FEATURE_HEADER_RE.match(line)),
        None,
    )
    if feature_start is not None:
        feature_end = next(
            (
                index
                for index in range(feature_start + 1, len(lines))
                if ANY_HEADER_RE.match(lines[index])
            ),
            len(lines),
        )
        for index in range(feature_start + 1, feature_end):
            if MEMORY_KEY_RE.match(lines[index]):
                previous = {"style": "section", "line": lines[index]}
                lines[index] = MEMORY_LINE + "\n"
                return "".join(lines), previous
        separator = (
            "\n"
            if feature_end > 0 and not lines[feature_end - 1].endswith(("\n", "\r"))
            else ""
        )
        lines.insert(feature_end, separator + MEMORY_LINE + "\n")
        return "".join(lines), {
            "style": "section",
            "line": None,
            "separator": separator,
        }

    if re.search(r"(?m)^\s*features\s*=", text):
        raise SystemExit("Refusing to replace unsupported inline `features` table")
    separator = (
        ""
        if not text or text.endswith("\n\n")
        else ("\n" if text.endswith("\n") else "\n\n")
    )
    return text + separator + f"[features]\n{MEMORY_LINE}\n", {
        "style": "new-section",
        "line": None,
        "separator": separator,
    }


def restore_native_memories(text: str, marker: dict[str, Any]) -> str:
    previous = marker.get("previousNativeMemories")
    if not isinstance(previous, dict):
        return text
    style = previous.get("style")
    prior_line = previous.get("line")
    if style == "dotted":
        managed = f"features.{MEMORY_LINE}\n"
        return text.replace(managed, prior_line, 1) if managed in text else text
    if style == "section":
        separator = previous.get("separator", "")
        if not isinstance(separator, str):
            return text
        managed = separator + MEMORY_LINE + "\n"
        replacement = prior_line if isinstance(prior_line, str) else ""
        return text.replace(managed, replacement, 1) if managed in text else text
    if style == "new-section":
        separator = previous.get("separator")
        if not isinstance(separator, str):
            return text
        fragment = separator + f"[features]\n{MEMORY_LINE}\n"
        if text.endswith(fragment):
            return text[: -len(fragment)]
    return text


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
    command = (
        f"{quote(sys.executable)} {quote(str(hook))} --agent codex "
        f"--home {quote(str(home))} --config-home {quote(str(home))}"
    )
    return {
        "SessionStart": [{"hooks": [handler(command, "load root at session start")]}],
        "UserPromptSubmit": [{"hooks": [handler(command, "remind root each turn")]}],
        "SubagentStart": [{"hooks": [handler(command, "load root for subagent")]}],
        "PreCompact": [{"hooks": [handler(command, "checkpoint context before compaction")]}],
        "PostCompact": [{"hooks": [handler(command, "verify checkpoint after compaction")]}],
        "PreToolUse": [{"matcher": "*", "hooks": [handler(command, "guard memory mutation")]}],
    }


def install(home: Path, hook: Path) -> None:
    hooks_path = home / "hooks.json"
    config_path = home / "config.toml"
    state_dir = home / ".agent-mem-struct"
    state_dir.mkdir(parents=True, exist_ok=True)
    backup = state_dir / "hooks.before-first-install.json"
    if hooks_path.exists() and not backup.exists():
        shutil.copy2(hooks_path, backup)

    previous_marker = marker_data(home)
    config_was_present = config_path.exists()
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config_text, previous_native_memories = disable_native_memories(
        config_text, previous_marker
    )
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
    try:
        tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Refusing to write invalid TOML to {config_path}: {exc}")
    save(hooks_path, data)
    save_text(config_path, config_text)
    (state_dir / "codex-root-memory-hook.json").write_text(
        json.dumps(
            {
                "hook": str(hook),
                "hooks_json": str(hooks_path),
                "config": str(config_path),
                "configWasPresent": config_was_present,
                "previousNativeMemories": previous_native_memories,
            },
            indent=2,
        )
        + "\n",
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
    installed_marker = marker_data(home)
    config_path = home / "config.toml"
    if config_path.exists():
        current = config_path.read_text(encoding="utf-8")
        restored = restore_native_memories(current, installed_marker)
        if restored != current:
            if not restored and installed_marker.get("configWasPresent") is False:
                config_path.unlink()
            else:
                save_text(config_path, restored)
    marker = home / ".agent-mem-struct" / "codex-root-memory-hook.json"
    if marker.exists():
        marker.unlink()
    shutil.rmtree(home / ".agent-mem-struct" / "compaction-checkpoints", ignore_errors=True)
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
