#!/usr/bin/env python3
"""Additive root-memory loader/guard for Codex, Claude Code, and Pi.

Thin entry point. It parses arguments, decides whether this config home is
active, reads the hook event, and composes the worker modules:

- rm_support  — shared primitives (guarded reads, containment, identity)
- rm_scan     — the tool-call mutation scanner behind the PreToolUse gate
- rm_control  — root memory/rules/structure validation and context rendering
- rm_receipts — convention acknowledgment receipts and the convention gate
- rm_checkpoints — pre-compaction continuity checkpoints

This hook does not create a second memory authority. It reads the existing
<agent-home>/memory/MEMORY.md control/index plus <agent-home>/RULES.md and
injects those exact sources into model context at supported lifecycle events.

On PreToolUse it blocks a subagent-attributed write into the agent memory tree,
fails closed for unknown or mutating actions when root control is invalid, and
requires hash-bound convention delivery and acknowledgment before action. A
stale Structure-Version is not hard-blocked for the owning session because
migration itself may require memory writes; instead the staleness is injected
as a mandatory migrate-first condition.

A spawned subagent receives the same root memory/rules context as the parent
at SubagentStart, read-only: it may read every source the parent reads, but
never owns a write, which PreToolUse enforces regardless of what the subagent
attempts.

On PreCompact a failed checkpoint refuses a manually requested compaction by
exit status, the only mechanism that event honors, and merely warns about an
automatic one so the session is never stranded at its context ceiling.
Stop applies the same convention receipt to read-only turns before completion.
"""
from __future__ import annotations

import argparse
import json
import os
import stat  # noqa: F401  (re-exported: the guard's Windows branch is probed via this module)
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from rm_checkpoints import CheckpointStore  # noqa: E402
from rm_control import RootControl, declared_shared  # noqa: E402,F401
from rm_events import EventDispatcher  # noqa: E402
from rm_receipts import ConventionGate  # noqa: E402
from rm_scan import MutationScanner  # noqa: E402

SUPPORTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PreToolUse",
    "Stop",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("codex", "claude", "pi"), required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--config-home")
    parser.add_argument("--canonical-root")
    return parser.parse_args()


def read_event() -> dict:
    try:
        value = json.load(sys.stdin)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def config_is_active(agent: str, config_home: Path | None) -> bool:
    if config_home is None:
        return True
    if agent == "pi":
        environment, default = "PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent"
    elif agent == "codex":
        environment, default = "CODEX_HOME", Path.home() / ".codex"
    else:
        environment, default = "CLAUDE_CONFIG_DIR", Path.home() / ".claude"
    configured = os.environ.get(environment)
    active = Path(configured).expanduser() if configured else default
    try:
        return os.path.normcase(str(active.resolve(strict=False))) == os.path.normcase(
            str(config_home.resolve(strict=False))
        )
    except OSError:
        return False


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve(strict=False)
    config_home = (
        Path(args.config_home).expanduser().resolve(strict=False)
        if args.config_home
        else None
    )
    canonical_root = (
        Path(args.canonical_root).expanduser().resolve(strict=False)
        if args.canonical_root
        else Path(__file__).resolve().parents[1]
    )
    if not config_is_active(args.agent, config_home):
        return 0
    event = read_event()
    event_name = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    if event_name not in SUPPORTED_EVENTS:
        return 0
    if event_name == "PreToolUse":
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not MutationScanner.tool_requires_acknowledgment(
            str(event.get("tool_name") or ""), tool_input
        ):
            return 0

    control = RootControl(home, canonical_root)
    state = control.load(args.agent)
    checkpoints = CheckpointStore(state)
    gate = ConventionGate(state)
    dispatcher = EventDispatcher(args.agent, control, checkpoints, gate)

    if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
        gate.prune_receipts()
        if not (event_name == "SessionStart" and event.get("source") == "compact"):
            gate.remove_receipt(event)
        if event_name != "UserPromptSubmit":
            keep = (
                checkpoints.path(event)
                if event_name == "SessionStart" and event.get("source") == "compact"
                else None
            )
            checkpoints.prune(keep=keep)
        dispatcher.emit_context(event_name, state, event)
        return 0
    if event_name == "PreCompact":
        return dispatcher.handle_precompact(event, state)
    if event_name == "PreToolUse":
        dispatcher.handle_pretool(event, state)
        return 0
    if event_name in {"Stop", "SubagentStop"}:
        dispatcher.handle_stop(event, state)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
