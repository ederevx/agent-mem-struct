"""Compaction continuity checkpoints.

One owner for the pre-compaction checkpoint store: transcript parsing, the
bounded checkpoint body, and the durable, atomically written checkpoint
records under the agent state directory. Stale and surplus records are
pruned deterministically on every save.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from rm_control import RootState
from rm_support import TEMP_MAX_AGE, safe_identity

CHECKPOINT_TEXT_LIMIT = 3500
CHECKPOINT_MAX_AGE = 7 * 24 * 60 * 60
CHECKPOINT_RESTORE_MAX_AGE = 24 * 60 * 60
CHECKPOINT_MAX_FILES = 256


class CheckpointStore:
    """Owns the checkpoint records for one agent home."""

    def __init__(self, state: RootState) -> None:
        self.state = state

    def dir(self) -> Path:
        return self.state.home / ".agent-mem-struct" / "compaction-checkpoints"

    def path(self, event: dict[str, Any]) -> Path:
        session = event.get("session_id") or event.get("sessionId")
        identity = safe_identity(session)
        agent = event.get("agent_id") or event.get("agentId")
        if agent:
            identity += "--" + safe_identity(agent)
        return self.dir() / f"{identity}.json"

    def remove(self, event: dict[str, Any]) -> None:
        path = self.path(event)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def prune(self, keep: Path | None = None) -> None:
        directory = self.dir()
        now = time.time()
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        survivors: list[tuple[float, Path]] = []
        for entry in entries:
            try:
                if entry == keep or entry.is_symlink() or not entry.is_file():
                    continue
                age = now - entry.stat().st_mtime
                limit = TEMP_MAX_AGE if ".tmp." in entry.name else CHECKPOINT_MAX_AGE
                if age > limit:
                    entry.unlink()
                elif entry.suffix == ".json":
                    survivors.append((entry.stat().st_mtime, entry))
            except OSError:
                continue
        survivors.sort(key=lambda item: item[0], reverse=True)
        survivor_limit = CHECKPOINT_MAX_FILES - (1 if keep is not None and keep.exists() else 0)
        for _, entry in survivors[max(0, survivor_limit):]:
            if entry == keep:
                continue
            try:
                entry.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass

    @staticmethod
    def content_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for block in value:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    kind = str(block.get("type") or "")
                    if kind in {"text", "input_text", "output_text"}:
                        parts.append(str(block.get("text") or ""))
                    elif kind == "tool_use":
                        parts.append(f"tool call: {block.get('name') or 'unknown'}")
                    elif kind == "tool_result":
                        result = CheckpointStore.content_text(block.get("content"))
                        parts.append("tool result: " + result[:500])
            return "\n".join(part for part in parts if part)
        return ""

    @staticmethod
    def transcript_entry(record: dict[str, Any]) -> str | None:
        if record.get("type") == "last-prompt" and record.get("lastPrompt"):
            return "LATEST USER OBJECTIVE: " + str(record["lastPrompt"])

        kind = record.get("type")
        if kind == "custom_message" and record.get("content"):
            return "CONTEXT: " + str(record["content"])
        if kind == "compaction" and record.get("summary"):
            return "COMPACTION SUMMARY: " + str(record["summary"])

        message = record.get("message")
        if isinstance(message, dict) and message.get("role") in {"user", "assistant"}:
            text = CheckpointStore.content_text(message.get("content"))
            if text:
                return f"{str(message['role']).upper()}: {text}"

        payload = record.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
                text = CheckpointStore.content_text(payload.get("content"))
                if text:
                    return f"{str(payload['role']).upper()}: {text}"
            if payload.get("type") in {"user_message", "agent_message"} and payload.get("message"):
                role = "USER" if payload.get("type") == "user_message" else "ASSISTANT"
                return f"{role}: {payload['message']}"
            if payload.get("type") in {"custom_tool_call", "function_call"}:
                return "TOOL CALL: " + str(payload.get("name") or "unknown")
            if payload.get("type") in {"custom_tool_call_output", "function_call_output"}:
                output = str(payload.get("output") or "")
                return "TOOL RESULT: " + output[:500] if output else None
        return None

    @staticmethod
    def _select(entries: deque[str]) -> str:
        """Pack the newest bounded tail of entry texts into one checkpoint body."""
        selected: list[str] = []
        used = 0
        for entry in reversed(entries):
            clipped = entry[:1200]
            if selected and used + len(clipped) + 1 > CHECKPOINT_TEXT_LIMIT:
                continue
            selected.append(clipped)
            used += len(clipped) + 1
            if used >= CHECKPOINT_TEXT_LIMIT:
                break
        selected.reverse()
        return "\n".join(selected)

    @staticmethod
    def _collect_inline(raw_entries: Any) -> deque[str]:
        # A host that hands its session entries to the hook directly (the Pi
        # bridge serializes the compaction event's branchEntries) needs no
        # transcript file, and may not have one at all.
        entries: deque[str] = deque(maxlen=80)
        for record in raw_entries:
            if not isinstance(record, dict):
                continue
            entry = CheckpointStore.transcript_entry(record)
            if entry:
                entries.append(re.sub(r"\s+", " ", entry).strip())
        return entries

    @staticmethod
    def _collect_transcript(raw_path: str) -> deque[str]:
        path = Path(raw_path).expanduser()
        entries: deque[str] = deque(maxlen=80)
        with path.open(encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                entry = CheckpointStore.transcript_entry(record)
                if entry:
                    entries.append(re.sub(r"\s+", " ", entry).strip())
        return entries

    def build(self, event: dict[str, Any]) -> str:
        raw_entries = event.get("session_entries")
        if isinstance(raw_entries, list) and raw_entries:
            entries = self._collect_inline(raw_entries)
            if not entries:
                raise ValueError("no continuity anchors found in session_entries")
            return self._select(entries)
        raw_path = event.get("transcript_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("compaction event did not provide transcript_path")
        entries = self._collect_transcript(raw_path)
        if not entries:
            raise ValueError(f"no continuity anchors found in transcript {raw_path}")
        return self._select(entries)

    def save(self, event: dict[str, Any]) -> None:
        session_id = event.get("session_id") or event.get("sessionId")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("compaction event did not provide a valid session_id")
        path = self.path(event)
        checkpoint = self.build(event)
        data: dict[str, Any] = {
            "saved_at": int(time.time()),
            "checkpoint": checkpoint,
        }
        # mkdir applies its mode to the leaf only, so secure the owned tree itself.
        for directory in (path.parent.parent, path.parent):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            # A failed save must not strand an orphan beside the target.
            temporary.unlink(missing_ok=True)
        self.prune(keep=path)

    def load(self, event: dict[str, Any]) -> str | None:
        path = self.path(event)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        checkpoint = data.get("checkpoint")
        try:
            age = time.time() - float(data.get("saved_at", 0))
        except (TypeError, ValueError):
            age = CHECKPOINT_RESTORE_MAX_AGE + 1
        if (
            not isinstance(checkpoint, str)
            or not checkpoint
            or age > CHECKPOINT_RESTORE_MAX_AGE
        ):
            self.remove(event)
            return None
        return checkpoint