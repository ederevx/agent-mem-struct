"""Shared installer helpers for the root-memory hook integrations."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

OWNER_PREFIX = "agent-mem-struct root memory:"
DEFAULT_TIMEOUT = 5
# Building a checkpoint streams the whole transcript, and a timed-out PreCompact
# is a silently skipped checkpoint. The event is not latency-sensitive.
EVENT_TIMEOUTS = {"PreCompact": 60}
EVENT_LABELS = {
    "SessionStart": "load root at session start",
    "UserPromptSubmit": "remind root each turn",
    "SubagentStart": "load root for subagent",
    "SubagentStop": "verify subagent convention acknowledgment",
    "PreCompact": "checkpoint context before compaction",
    "PreToolUse": "guard memory mutation",
    "Stop": "verify convention acknowledgment",
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
    # A pid-unique temporary keeps concurrent installs from clobbering each
    # other's staging file, and the finally block keeps a failed write from
    # stranding an orphan beside the target.
    temporary = path.with_name(f"{path.name}.agent-mem-struct.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def secure_dir(path: Path) -> None:
    """Own the state directory privately; it holds a settings backup."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


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
    state = home / ".agent-mem-struct"
    shutil.rmtree(state / "compaction-checkpoints", ignore_errors=True)
    shutil.rmtree(state / "convention-receipts", ignore_errors=True)
    try:
        state.rmdir()
    except OSError:
        pass


def remove_install_backup(home: Path, name: str) -> None:
    state = home / ".agent-mem-struct"
    (state / name).unlink(missing_ok=True)
    try:
        state.rmdir()
    except OSError:
        pass


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def refresh_root_documents(
    home: Path,
    canonical_root: Path,
    *,
    allow_refresh: bool = False,
    previous_documents: Any = None,
) -> dict[str, dict[str, str]]:
    """Install regular copies on every platform without overwriting local edits.

    Validate both documents before replacing either. Hashes in the installation
    marker authorize later refreshes only at the same destination and source.
    The explicit refresh option bootstraps recognized pre-marker copies; it
    never overrides the edit protection of an already tracked copy.
    """
    previous_documents = (
        previous_documents if isinstance(previous_documents, dict) else {}
    )
    documents: dict[str, dict[str, str]] = {}
    replacements: list[tuple[Path, Path, bytes]] = []
    for name in ("RULES.md", "STRUCTURE.md"):
        source = canonical_root / name
        target = home / name
        if not source.is_file():
            raise SystemExit(f"Canonical root document is unavailable: {source}")
        if target.parent.resolve() == source.parent.resolve():
            raise SystemExit(f"Refusing to replace the canonical root document itself: {target}")
        content = source.read_bytes()
        document = {
            "path": str(target.absolute()),
            "source": str(source.resolve()),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        documents[name] = document
        if target.is_symlink():
            if target.resolve(strict=False) != source.resolve(strict=False):
                raise SystemExit(
                    f"Refusing to replace root symlink {target}; it resolves outside {source}"
                )
            replacements.append((target, source, content))
            continue
        if target.exists():
            if not target.is_file():
                raise SystemExit(f"Refusing to replace non-file root document {target}")
            existing_bytes = target.read_bytes()
            matches = existing_bytes == content
            # A prior install may have atomically replaced this file and then
            # been interrupted before saving the new marker. Current canonical
            # bytes are safe to adopt regardless of the marker's stale hash.
            if matches:
                continue
            previous = previous_documents.get(name)
            tracked = (
                isinstance(previous, dict)
                and previous.get("path") == document["path"]
                and previous.get("source") == document["source"]
            )
            if tracked and previous.get("sha256") != hashlib.sha256(existing_bytes).hexdigest():
                raise SystemExit(f"Refusing to overwrite user-modified managed root document {target}")
            if not matches and not tracked and not allow_refresh:
                raise SystemExit(
                    f"Root document differs from canonical: {target}. Re-run with "
                    "--refresh-root-documents only if this is a managed agent-mem-struct copy."
                )
            if not matches and not tracked:
                existing = existing_bytes.decode("utf-8", errors="replace")
                heading = "# Memory rules" if name == "RULES.md" else "# Memory structure"
                if heading not in existing or "© 2026 Edrick Sinsuan" not in existing:
                    raise SystemExit(
                        f"Refusing to overwrite foreign root document {target}; move it or configure the correct memory home"
                    )
        replacements.append((target, source, content))

    for target, source, content in replacements:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".agent-mem-struct-", dir=target.parent) as directory:
            temporary = Path(directory) / target.name
            temporary.write_bytes(content)
            os.replace(temporary, target)
    return documents


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
        f"--config-home {_quote(str(config_home))} "
        f"--canonical-root {_quote(str(hook.resolve(strict=False).parents[1]))}"
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for event, label in EVENT_LABELS.items():
        handler = {
            "type": "command",
            "command": command,
            "timeout": EVENT_TIMEOUTS.get(event, DEFAULT_TIMEOUT),
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
