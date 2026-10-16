"""The private ledger of repositories this agent touched.

One owner for the durable record behind the session-start leftovers report:
`RepoLedger` reads, records, and prunes the private `touched-repos.json`
state file, one entry per repository top level with the epoch it was last
seen. It is bounded (`LEDGER_MAX_ENTRIES`), expires an entry after
`LEDGER_MAX_AGE`, and writes only inside its own agent state directory, with
a `0700` directory and a `0600` file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from rm_control import RootState

LEDGER_NAME = "touched-repos.json"
LEDGER_MAX_ENTRIES = 50
LEDGER_MAX_AGE = 14 * 24 * 60 * 60


class RepoLedger:
    """Owns the private touched-repository ledger for one agent home."""

    def __init__(self, state: RootState) -> None:
        self.state = state

    def path(self) -> Path:
        return self.state.home / ".agent-mem-struct" / LEDGER_NAME

    def entries(self) -> list[dict[str, Any]]:
        """The live entries, newest first, with expired records dropped."""
        now = time.time()
        entries: list[dict[str, Any]] = []
        for item in self._read():
            path = item.get("path")
            last_seen = self._epoch(item.get("last_seen"))
            if not isinstance(path, str) or not path or last_seen is None:
                continue
            if now - last_seen > LEDGER_MAX_AGE:
                continue
            entries.append({"path": path, "last_seen": last_seen})
        entries.sort(key=lambda entry: entry["last_seen"], reverse=True)
        return entries

    def record(self, repo: Path) -> None:
        """Note that this repository was touched now, and keep the ledger bounded."""
        key = str(Path(repo).resolve(strict=False))
        entries = [entry for entry in self.entries() if entry["path"] != key]
        entries.insert(0, {"path": key, "last_seen": time.time()})
        self._write(entries)

    def prune(self, paths: Iterable[Path]) -> None:
        """Drop every named repository from the ledger."""
        dropped = {str(Path(path).resolve(strict=False)) for path in paths}
        entries = self.entries()
        kept = [entry for entry in entries if entry["path"] not in dropped]
        if len(kept) == len(entries):
            return
        self._write(kept)

    # -- persistence ---------------------------------------------------------

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        path = self.path()
        directory = path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(
                json.dumps(entries[:LEDGER_MAX_ENTRIES], ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _epoch(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
