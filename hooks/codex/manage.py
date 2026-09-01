#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory hooks for Codex."""
from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
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
    replace_owned_hooks,
    save_json,
    secure_dir,
    strip_owned_hooks,
)

MEMORY_LINE = (
    "memories = false  # agent-mem-struct: native Codex memories disabled"
)
FEATURE_HEADER_RE = re.compile(r"^\s*\[features\]\s*(?:#.*)?$")
ANY_HEADER_RE = re.compile(r"^\s*\[")
MEMORY_KEY_RE = re.compile(r"^\s*memories\s*=")
DOT_MEMORY_KEY_RE = re.compile(r"^\s*features\.memories\s*=")
MARKER_NAME = "codex-root-memory-hook.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--home",
        default=os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"),
    )
    return parser.parse_args()


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".agent-mem-struct.tmp")
    temporary.write_text(text, encoding="utf-8")
    if path.exists():
        os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def marker_path(home: Path) -> Path:
    return home / ".agent-mem-struct" / MARKER_NAME


def disable_native_memory(
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
            lines[index] = f"features.{MEMORY_LINE}\n"
            return "".join(lines), {"style": "dotted", "line": line}

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
                previous_line = lines[index]
                lines[index] = MEMORY_LINE + "\n"
                return "".join(lines), {
                    "style": "section",
                    "line": previous_line,
                }
        separator = (
            "\n"
            if feature_end and not lines[feature_end - 1].endswith(("\n", "\r"))
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


def restore_native_memory(text: str, marker: dict[str, Any]) -> str:
    previous = marker.get("previousNativeMemories")
    if not isinstance(previous, dict):
        return text
    style = previous.get("style")
    prior_line = previous.get("line")
    if style == "dotted":
        managed = "features." + MEMORY_LINE + "\n"
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
        fragment = separator + "[features]\n" + MEMORY_LINE + "\n"
        return text[: -len(fragment)] if text.endswith(fragment) else text
    return text


def install(home: Path, hook: Path) -> None:
    hooks_path = home / "hooks.json"
    config_path = home / "config.toml"
    marker_file = marker_path(home)
    secure_dir(marker_file.parent)
    backup_once(hooks_path, marker_file.parent / "hooks.before-first-install.json")

    previous_marker = read_marker(marker_file)
    prior_presence = previous_marker.get("configWasPresent")
    config_was_present = (
        prior_presence if isinstance(prior_presence, bool) else config_path.exists()
    )
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config_text, previous_native_memory = disable_native_memory(
        config_text,
        previous_marker,
    )
    settings = load_json(hooks_path)
    replace_owned_hooks(settings, hook_groups("codex", home, home, hook))

    try:
        tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Refusing to write invalid TOML to {config_path}: {exc}")
    save_json(hooks_path, settings)
    save_text(config_path, config_text)
    save_json(
        marker_file,
        {
            "configWasPresent": config_was_present,
            "previousNativeMemories": previous_native_memory,
        },
    )
    print(f"Installed additive Codex root-memory hooks into {hooks_path}")
    print("Restart Codex and review/trust the new user hook in /hooks.")


def uninstall(home: Path) -> None:
    hooks_path = home / "hooks.json"
    marker_file = marker_path(home)
    marker = read_marker(marker_file)
    if hooks_path.exists():
        settings = load_json(hooks_path)
        strip_owned_hooks(settings)
        save_json(hooks_path, settings)

    config_path = home / "config.toml"
    if config_path.exists():
        current = config_path.read_text(encoding="utf-8")
        restored = restore_native_memory(current, marker)
        if restored != current:
            if not restored and marker.get("configWasPresent") is False:
                config_path.unlink()
            else:
                save_text(config_path, restored)
    marker_file.unlink(missing_ok=True)
    remove_checkpoints(home)
    print("Removed only agent-mem-struct Codex hook entries.")


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    hook = HOOK_ROOT / "root-memory-context.py"
    if not hook.exists():
        raise SystemExit(f"Shared hook not found: {hook}")
    if args.action == "install":
        install(home, hook)
    else:
        uninstall(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
