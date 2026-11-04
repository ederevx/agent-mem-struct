"""The judge's session window.

One owner for turning a host's session source into the ordered window the
judge reviews: which records are positions, which of them are reviewable
turns, and the continuity evidence that lets a later settle prove the saved
anchor still names the same history. The judge keeps every decision about
what to review; this module only reports what the source holds.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rm_control import RootState
from rm_judge_settings import JudgeError

# Bumped when a projection changes, so an old anchor cannot silently match.
FORMAT_VERSION = 1

# Claude records that carry conversation, and records that are known and not
# submitted. An unlisted type is a refusal, not a guess.
CLAUDE_REVIEWABLE = frozenset({"user", "assistant", "attachment", "queue-operation"})
CLAUDE_KNOWN = CLAUDE_REVIEWABLE | {
    "last-prompt", "cost-state", "atis-latch", "custom_message", "compaction",
    "summary",
}
# Codex response_item payloads that carry conversation.
CODEX_REVIEWABLE = frozenset({
    "message", "reasoning", "function_call", "custom_tool_call",
    "function_call_output", "custom_tool_call_output",
})
CODEX_KNOWN = CODEX_REVIEWABLE | {
    "item_completed", "task_started", "task_complete", "token_count",
    "agent_message", "user_message", "turn_aborted",
}
CODEX_RECORD_TYPES = frozenset({
    "session_meta", "event_msg", "response_item", "turn_context", "world_state",
    "token_usage_record", "inter_agent_communication_metadata",
})


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Window:
    """One host session's ordered source: positions, reviewable turns, evidence."""

    host: str
    session: str
    positions: tuple[str, ...]
    reviewable: dict[str, str]
    records: dict[str, Any]

    def leaf(self) -> str:
        return self.positions[-1] if self.positions else ""

    def turns(self, after: str) -> list[dict[str, str]] | None:
        """The reviewable turns after one position, or None when it is gone."""
        if after not in self.records:
            return None
        seen = False
        turns: list[dict[str, str]] = []
        for position in self.positions:
            if position == after:
                seen = True
                continue
            if seen and position in self.reviewable:
                turns.append({"id": position, "text": self.reviewable[position]})
        return turns

    def digest(self, anchor: str) -> str:
        """Continuity evidence for one anchor: the anchor record itself.

        A transport bound can make the window start at the anchor instead of at
        the branch root, so evidence over "everything before the anchor" cannot
        be recomputed from the window a later settle supplies. The anchor record
        alone is present in every window that still contains the anchor, so it is
        the part of the history both sides can always compare: a rewritten
        anchor is detected, and the turns before it are outside the judge's scope
        because the watermark already passed them.
        """
        if anchor not in self.records:
            raise JudgeError("invalid_window")
        return hashlib.sha256(
            canonical([FORMAT_VERSION, self.host, self.session, anchor,
                       self.records[anchor]]).encode("utf-8")
        ).hexdigest()

    def boundary(self, anchor: str) -> dict[str, Any]:
        """The continuity record one receipt keeps for a reviewed boundary."""
        return {
            "host": self.host,
            "session": self.session,
            "anchor": anchor,
            "digest": self.digest(anchor),
            "format": FORMAT_VERSION,
        }


def window(state: RootState, event: dict[str, Any]) -> Window:
    """The supplied window for this host, refused unless it is one ordered chain."""
    builder = {
        "pi": pi_window,
        "claude": claude_window,
        "codex": codex_window,
    }.get(state.agent)
    if builder is None:
        raise JudgeError("unsupported_host")
    return builder(event)


def session_id(event: dict[str, Any]) -> str:
    value = event.get("session_id") or event.get("sessionId")
    if not isinstance(value, str) or not value:
        raise JudgeError("invalid_window")
    return value


def transcript_path(event: dict[str, Any]) -> Path:
    value = event.get("transcript_path")
    if not isinstance(value, str) or not value:
        raise JudgeError("invalid_window")
    return Path(value).expanduser()


def records(path: Path) -> list[dict[str, Any]]:
    """Every JSONL record of one transcript, refusing an unreadable or torn line.

    An incomplete final line is not skipped: at a settle it can hold the very
    turn being reviewed, so reading past it would review a shorter history.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise JudgeError("invalid_window") from None
    parsed: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise JudgeError("invalid_window") from None
        if not isinstance(record, dict):
            raise JudgeError("invalid_window")
        parsed.append(record)
    if not parsed:
        raise JudgeError("invalid_window")
    return parsed


def blocks_text(value: Any) -> str:
    """Every model-visible part of one content value, unabridged."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            parts.append(block_text(block))
    return "\n".join(part for part in parts if part)


def block_text(block: dict[str, Any]) -> str:
    """One content block's model-visible text, across the hosts' block shapes.

    Each host names a tool invocation differently - `tool_use` with `input` in
    Claude's, `toolCall` with `arguments` in Pi's - so the name and the
    arguments identify one, not the type string. A block that carries either is
    a call, and dropping it would hide a tool call from the judge.
    """
    if isinstance(block.get("text"), str):
        return block["text"]
    kind = str(block.get("type") or "")
    arguments = block.get("input")
    if arguments is None:
        arguments = block.get("arguments")
    if arguments is not None or kind in ("tool_use", "toolCall"):
        return f"[{block.get('name') or 'tool'}] {canonical(arguments if arguments is not None else {})}"
    if "content" in block:
        return blocks_text(block["content"])
    return ""


def pi_window(event: dict[str, Any]) -> Window:
    """The branch snapshot Pi supplies inline, one contiguous chain to its end."""
    raw = event.get("session_entries")
    leaf = event.get("leaf_id")
    session_leaf = event.get("session_leaf_id")
    if session_leaf is not None and (not isinstance(session_leaf, str) or not session_leaf):
        raise JudgeError("invalid_window")
    if not isinstance(raw, list) or not raw or not isinstance(leaf, str):
        raise JudgeError("invalid_window")
    positions: list[str] = []
    reviewable: dict[str, str] = {}
    kept: dict[str, Any] = {}
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise JudgeError("invalid_window")
        if not entry["id"] or entry["id"] in kept:
            raise JudgeError("invalid_window")
        positions.append(entry["id"])
        kept[entry["id"]] = entry
        reviewable[entry["id"]] = entry_text(entry)
    for previous, entry in zip(raw, raw[1:]):
        if entry.get("parentId") != previous["id"]:
            raise JudgeError("invalid_window")
    if positions[-1] != leaf:
        raise JudgeError("invalid_window")
    return Window("pi", session_id(event), tuple(positions), reviewable, kept)


def entry_text(entry: dict[str, Any]) -> str:
    """Every model-visible part of one Pi session entry, tool calls included."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else entry.get("content")
    if isinstance(content, str):
        return content
    return blocks_text(content) if isinstance(content, list) else ""


def claude_window(event: dict[str, Any]) -> Window:
    """The ancestry of the transcript's tip, walked by parentUuid.

    File adjacency is not parentage: a fork's sibling can sit in the file
    without belonging to this branch, so only the walked ancestry is used.
    The tip is the last record carrying a uuid, never a trailing bookkeeping
    record.
    """
    source = records(transcript_path(event))
    session = session_id(event)
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in source:
        if record.get("sessionId") not in (None, session):
            raise JudgeError("invalid_window")
        kind = record.get("type")
        if kind not in CLAUDE_KNOWN:
            raise JudgeError("invalid_window")
        identifier = record.get("uuid")
        if identifier is None:
            continue
        if not isinstance(identifier, str) or not identifier or identifier in by_id:
            raise JudgeError("invalid_window")
        by_id[identifier] = record
        order.append(identifier)
    if not order:
        raise JudgeError("invalid_window")
    ancestry: list[str] = []
    current: str | None = order[-1]
    while current is not None:
        if current in ancestry or current not in by_id:
            raise JudgeError("invalid_window")
        ancestry.append(current)
        parent = by_id[current].get("parentUuid")
        if parent in ("", None):
            current = None
        elif isinstance(parent, str):
            current = parent
        else:
            raise JudgeError("invalid_window")
    ancestry.reverse()
    corroborate(ancestry, by_id, event)
    positions = tuple(ancestry)
    reviewable = {
        position: claude_text(by_id[position])
        for position in positions
        if by_id[position].get("type") in CLAUDE_REVIEWABLE
    }
    return Window("claude", session, positions, {k: v for k, v in reviewable.items() if v},
                  {position: by_id[position] for position in positions})


def corroborate(ancestry: list[str], by_id: dict[str, Any], event: dict[str, Any]) -> None:
    """Check the tip against the assistant text the host reported, when it did.

    The payload's own last message cannot create an anchor; it can only show
    that the transcript read and the host disagree, which is a refusal.
    """
    reported = event.get("last_assistant_message")
    if not isinstance(reported, str) or not reported.strip():
        return
    for position in reversed(ancestry):
        record = by_id[position]
        if record.get("type") == "assistant":
            if blocks_text((record.get("message") or {}).get("content")).strip() != reported.strip():
                raise JudgeError("invalid_window")
            return
    raise JudgeError("invalid_window")


def claude_text(record: dict[str, Any]) -> str:
    """One Claude record's model-visible text."""
    message = record.get("message")
    if isinstance(message, dict):
        text = blocks_text(message.get("content"))
        if text:
            return text
    for key in ("content", "attachment", "lastPrompt"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            text = blocks_text(value.get("content") or value)
            if text:
                return text
        if isinstance(value, list):
            text = blocks_text(value)
            if text:
                return text
    return ""


def codex_window(event: dict[str, Any]) -> Window:
    """The rollout's records in ordinal order, contiguity proven by the ordinals.

    A rollout has no parent links, so the order is the source's own log order
    and the continuity digest covers it. Ordinals must strictly increase over
    the captured segment: gaps are allowed, a repeat or a regression is not.
    """
    parsed = records(transcript_path(event))
    session = session_id(event)
    seen_meta = False
    previous = -1
    positions: list[str] = []
    reviewable: dict[str, str] = {}
    kept: dict[str, Any] = {}
    for record in parsed:
        kind = record.get("type")
        if kind not in CODEX_RECORD_TYPES:
            raise JudgeError("invalid_window")
        if kind == "session_meta":
            payload = record.get("payload")
            identifier = payload.get("session_id") if isinstance(payload, dict) else None
            if identifier not in (None, session):
                raise JudgeError("invalid_window")
            seen_meta = True
        ordinal = record.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal <= previous:
            raise JudgeError("invalid_window")
        previous = ordinal
        position = str(ordinal)
        positions.append(position)
        kept[position] = record
        text = codex_text(record)
        if text:
            reviewable[position] = text
    if not seen_meta or not positions:
        raise JudgeError("invalid_window")
    return Window("codex", session, tuple(positions), reviewable, kept)


def codex_text(record: dict[str, Any]) -> str:
    """One rollout record's model-visible text, or empty for metadata."""
    if record.get("type") != "response_item":
        return ""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise JudgeError("invalid_window")
    kind = payload.get("type")
    if kind not in CODEX_KNOWN:
        raise JudgeError("invalid_window")
    if kind not in CODEX_REVIEWABLE:
        return ""
    if kind == "message":
        return blocks_text(payload.get("content"))
    if kind == "reasoning":
        parts = payload.get("summary") or payload.get("content") or payload.get("text") or ""
        return blocks_text(parts) if isinstance(parts, list) else str(parts)
    call = payload.get("name") or ""
    if kind.endswith("_output"):
        output = payload.get("output")
        return blocks_text(output) if isinstance(output, list) else str(output or "")
    arguments = payload.get("arguments") or payload.get("input")
    return f"[{call}] {canonical(arguments if arguments is not None else {})}"
