"""Shared primitives for the root-memory hook modules.

One owner for the primitives every module needs: a guarded text read, a
path-containment test, and the stable event identity hash. Nothing here
carries state.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

# A stale staging temporary (".tmp.<pid>" in its name) from a crashed writer
# is scavenged far sooner than the real records it sits beside.
TEMP_MAX_AGE = 60 * 60


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


def safe_identity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))
    return text[:160] or "unknown"
