#!/usr/bin/env python3
"""Install/uninstall agent-mem-struct root-memory integration for Pi.

Pi has no hooks configuration file; its extension surface is a managed
TypeScript file in <pi-home>/extensions/ discovered automatically. This
installer therefore deploys exactly two things: the managed bridge extension
(this repo's canonical checkout stays the hook's runtime, exactly as for the
other hosts) and the protected root documents under the memory home. The
agent's own settings.json is never read or written: Pi keeps model and
session state there that this integration does not own.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

HOOK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOK_ROOT))

from manage_common import (  # noqa: E402
    clear_install_state,
    marker_memory_home,
    read_marker,
    refresh_root_documents,
    remove_checkpoints,
    resolve_memory_home,
    save_json,
    secure_dir,
    warn_missing_root_memory,
)

MARKER_NAME = "pi-root-memory-hook.json"
PACKAGE_DOCUMENTS_MARKER = "pi-package-root-documents.json"
EXTENSION_NAME = "agent-mem-struct.ts"
TEMPLATE_PATH = HOOK_ROOT / "pi" / "root-memory-extension.ts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall", "sync-documents"))
    parser.add_argument(
        "--home",
        default=os.environ.get("PI_CODING_AGENT_DIR") or str(Path.home() / ".pi" / "agent"),
    )
    parser.add_argument("--refresh-root-documents", action="store_true")
    parser.add_argument(
        "--memory-home",
        help="Agent root containing memory/MEMORY.md, RULES.md, and STRUCTURE.md",
    )
    return parser.parse_args()


def marker_path(home: Path) -> Path:
    return home / ".agent-mem-struct" / MARKER_NAME


def _ts_string_literal(value: str) -> bytes:
    """Spell a path so the generated TS double-quoted literal keeps it intact.

    A raw Windows path embeds single backslashes (``C:\\Users\\...``) into the
    generated ``const X = "..."`` line. JavaScript treats an unknown escape
    such as ``\\U`` as the bare letter and silently drops the backslash, so
    the baked value resolves to a path that never existed: the bridge spawn
    fails with ENOENT and the memory gate falls open without enforcement.
    Forward slashes are accepted by every API the bridge reaches (spawn,
    execFile, and the Python hook's Path handling) and need no escaping, so
    the literal survives js/jiti parsing byte-for-byte on any host.
    """
    return value.replace(os.sep, "/").encode()


def render_extension(
    hook: Path, home: Path, memory_home: Path, canonical_root: Path
) -> bytes:
    template = TEMPLATE_PATH.read_bytes()
    if b"__AMS_PYTHON__" not in template:
        raise SystemExit(f"Extension template is missing its placeholders: {TEMPLATE_PATH}")
    replacements = {
        b"__AMS_PYTHON__": _ts_string_literal(str(sys.executable)),
        b"__AMS_HOOK__": _ts_string_literal(str(hook.resolve(strict=False))),
        b"__AMS_HOME__": _ts_string_literal(str(memory_home)),
        b"__AMS_CONFIG_HOME__": _ts_string_literal(str(home)),
        b"__AMS_CANONICAL_ROOT__": _ts_string_literal(str(canonical_root)),
    }
    for token, value in replacements.items():
        if not value:
            raise SystemExit(f"Refusing to bake an empty path for {token.decode()}")
        template = template.replace(token, value)
    if b"__AMS_" in template:
        raise SystemExit("Extension template still contains unresolved placeholders")
    return template


def deploy_extension(
    extension_path: Path, content: bytes, marker: dict[str, Any]
) -> dict[str, str]:
    """Install the managed bridge, preserving foreign or user-edited files."""
    recorded = marker.get("extension")
    if extension_path.is_symlink():
        raise SystemExit(f"Refusing to replace root symlink {extension_path}")
    if extension_path.exists():
        if not extension_path.is_file():
            raise SystemExit(f"Refusing to replace non-file extension {extension_path}")
        existing = extension_path.read_bytes()
        if existing == content:
            recorded_hash = {
                "path": str(extension_path),
                "sha256": hashlib.sha256(existing).hexdigest(),
            }
            return recorded_hash
        tracked = (
            isinstance(recorded, dict)
            and recorded.get("path") == str(extension_path)
        )
        if tracked and recorded.get("sha256") != hashlib.sha256(existing).hexdigest():
            raise SystemExit(
                f"Refusing to overwrite user-modified managed extension {extension_path}"
            )
        if not tracked and not existing.startswith(
            b"// agent-mem-struct root memory:"
        ):
            raise SystemExit(
                f"Refusing to overwrite foreign extension {extension_path}; remove or rename it first"
            )
    extension_path.parent.mkdir(parents=True, exist_ok=True)
    # A pid-unique temporary keeps concurrent installs from clobbering each
    # other's staging file, and the finally block keeps a failed write from
    # stranding an orphan beside the extension. The existing file's mode is
    # preserved across the atomic replacement.
    temporary = extension_path.with_name(
        f"{extension_path.name}.agent-mem-struct.{os.getpid()}.tmp"
    )
    try:
        temporary.write_bytes(content)
        if extension_path.exists():
            os.chmod(temporary, extension_path.stat().st_mode & 0o777)
        os.replace(temporary, extension_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(extension_path),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def install(home: Path, memory_home: Path, *, refresh_documents: bool = False) -> None:
    marker_file = marker_path(home)
    secure_dir(marker_file.parent)
    previous_marker = read_marker(marker_file)
    previous_memory_home = marker_memory_home(previous_marker)
    if previous_memory_home is not None and previous_memory_home != memory_home:
        remove_checkpoints(previous_memory_home)

    hook = HOOK_ROOT / "root-memory-context.py"
    if not hook.is_file():
        raise SystemExit(f"Shared hook not found: {hook}")
    canonical_root = HOOK_ROOT.parent
    content = render_extension(hook, home, memory_home, canonical_root)
    extension_record = deploy_extension(
        home / "extensions" / EXTENSION_NAME, content, previous_marker
    )
    root_documents = refresh_root_documents(
        memory_home, canonical_root, allow_refresh=refresh_documents,
        previous_documents=previous_marker.get("rootDocuments"),
    )
    save_json(
        marker_file,
        {
            "memoryHome": str(memory_home),
            "extension": extension_record,
            "rootDocuments": root_documents,
        },
    )

    warn_missing_root_memory(memory_home)
    print(f"Installed the agent-mem-struct Pi bridge at {extension_record['path']}")
    print(f"Root memory authority: {memory_home}")
    print("Restart Pi (or run /reload) and confirm the injected root memory.")


def sync_documents(
    home: Path, memory_home: Path, *, refresh_documents: bool = False
) -> None:
    """Deploy the protected root documents for a pi-package install.

    A package install has no managed bridge, so it owns no marker in the
    installer's file. This action reuses the installer's refresh policy under
    a separate package-owned marker: missing or identical copies are deployed
    or adopted, an unmodified copy this action previously tracked is refreshed
    to the canonical bytes, and a user-modified or foreign copy is refused
    rather than overwritten. It never deploys the bridge or the installer
    marker, so the package entry stays the sole registrant. The explicit
    refresh option matches the installer's bootstrap behavior.
    """
    canonical_root = HOOK_ROOT.parent
    state_file = home / ".agent-mem-struct" / PACKAGE_DOCUMENTS_MARKER
    previous = read_marker(state_file).get("rootDocuments")
    documents = refresh_root_documents(
        memory_home,
        canonical_root,
        allow_refresh=refresh_documents,
        previous_documents=previous if isinstance(previous, dict) else {},
    )
    secure_dir(state_file.parent)
    save_json(state_file, {"rootDocuments": documents})
    print(f"Root documents current at {memory_home}")


def uninstall(home: Path, memory_home: Path) -> None:
    marker_file = marker_path(home)
    marker = read_marker(marker_file)
    recorded = marker.get("extension")
    extension_path = home / "extensions" / EXTENSION_NAME
    if isinstance(recorded, dict) and extension_path.is_file() and not extension_path.is_symlink():
        if hashlib.sha256(extension_path.read_bytes()).hexdigest() == recorded.get("sha256"):
            extension_path.unlink()
        else:
            print(
                f"Preserving user-modified managed extension {extension_path}.",
                file=sys.stderr,
            )
    elif not isinstance(recorded, dict) and extension_path.exists():
        print(
            f"Preserving untracked extension {extension_path}; no ownership record.",
            file=sys.stderr,
        )

    clear_install_state(marker_file, marker, memory_home)
    print("Removed only the agent-mem-struct Pi bridge and its state.")


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    memory_home = resolve_memory_home(home, marker_path(home), args.memory_home)
    if args.action == "install":
        install(home, memory_home, refresh_documents=args.refresh_root_documents)
    elif args.action == "uninstall":
        uninstall(home, memory_home)
    else:
        sync_documents(home, memory_home, refresh_documents=args.refresh_root_documents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())