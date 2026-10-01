"""Lifecycle event handling for the root-memory hook.

One owner for the per-event behaviors: context emission, compaction gating,
the PreToolUse memory-mutation gate, and turn-completion enforcement. The
dispatcher composes the root control snapshot, the convention gate, and the
checkpoint store; it owns no persistent state of its own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from rm_checkpoints import CheckpointStore
from rm_control import RootState, RootControl
from rm_receipts import ConventionGate
from rm_scan import PATCH_HEADER_RE, MutationScanner
from rm_support import under
from rm_update import MemoryUpdater


def is_subagent_event(event: dict[str, Any]) -> bool:
    agent = event.get("agent_id") or event.get("agentId")
    return isinstance(agent, str) and bool(agent.strip())


def compaction_is_automatic(event: dict[str, Any]) -> bool:
    """Report whether the host, not the user, asked for this compaction."""
    trigger = (
        event.get("triggered_by") or event.get("triggeredBy") or event.get("trigger")
    )
    return str(trigger or "").lower() == "auto"


class EventDispatcher:
    """Applies one hook event against the validated root-control snapshot."""

    def __init__(
        self,
        agent: str,
        control: RootControl,
        checkpoints: CheckpointStore,
        gate: ConventionGate,
    ) -> None:
        self.agent = agent
        self.control = control
        self.checkpoints = checkpoints
        self.gate = gate

    @staticmethod
    def deny_pretool(reason: str, *, context: bool = False) -> None:
        output: dict[str, Any] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        if context:
            output["additionalContext"] = reason
        json.dump({"hookSpecificOutput": output}, sys.stdout, separators=(",", ":"))

    @staticmethod
    def emit_hook_context(event_name: str, context: str) -> None:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": context,
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )

    def emit_context(
        self, event_name: str, state: RootState, event: dict[str, Any]
    ) -> None:
        checkpoint = None
        after_compaction = event_name == "SessionStart" and event.get("source") == "compact"
        if after_compaction:
            checkpoint = self.checkpoints.load(event)
        if event_name == "SubagentStart":
            context = self.control.subagent_context_text(state)
        elif event_name == "UserPromptSubmit":
            context = self.control.turn_reminder_text(self.agent, state)
        elif self.agent == "pi":
            # Pi loads conventions through pre_memory; only the continuity
            # checkpoint still needs to reach the model at session start.
            context = self.continuity_context(
                state, checkpoint, base=self.control.pre_memory_pointer_text()
            )
        else:
            context = self.continuity_context(state, checkpoint)
        if after_compaction and checkpoint is None:
            context += (
                "\n\nCONTINUITY WARNING: no pre-compaction checkpoint was available for this session. "
                "Reconfirm the active objective, completed actions, blockers, and next action from the transcript or user."
            )
        self.emit_hook_context(event_name, context)
        if after_compaction:
            self.checkpoints.remove(event)

    def continuity_context(
        self, state: RootState, checkpoint: str | None, base: str | None = None
    ) -> str:
        text = base if base is not None else self.control.context_text(state)
        if checkpoint:
            text += (
                "\n\n--- BEGIN PRE-COMPACTION CONTINUITY CHECKPOINT ---\n"
                + checkpoint
                + "\n--- END PRE-COMPACTION CONTINUITY CHECKPOINT ---\n"
                "Use this bounded checkpoint only to restore the active objective, completed actions, tool outcomes, "
                "decisions, blockers, and next action. The transcript and current user instructions remain authoritative."
            )
        return text

    def pull_shared(self, state: RootState) -> tuple[bool, str]:
        """Fast-forward the declared shared worktree; report verified success."""
        try:
            completed = subprocess.run(
                ["git", "-C", str(state.shared_resolved), "pull", "--ff-only"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            return False, str(exc)
        if completed.returncode == 0:
            return True, ""
        detail = (completed.stderr or completed.stdout or "").strip()
        return False, detail or f"git pull exited {completed.returncode}"

    def handle_prememory(self, event: dict[str, Any], state: RootState) -> int:
        """Load, pull, acknowledge, and return the Pi convention catalog."""
        if state.errors:
            self.emit_hook_context(
                "PreMemory",
                "CONTROL ERROR: root memory control is invalid. "
                + " | ".join(state.errors)
                + " Repair the root control files before memory work.",
            )
            return 1
        if state.shared_git_backed:
            pulled, detail = self.pull_shared(state)
            if not pulled:
                self.emit_hook_context(
                    "PreMemory",
                    "CONTROL ERROR: the declared shared worktree could not be "
                    f"fast-forwarded ({detail}). Reconcile the shared history before "
                    "continuing; the shared conventions are unverified and no "
                    "acknowledgment was recorded.",
                )
                return 1
            # The pull may have advanced the shared conventions; reload so the
            # catalog the model reads matches what the receipt acknowledges.
            state = self.control.load(self.agent)
            if state.errors:
                self.emit_hook_context(
                    "PreMemory",
                    "CONTROL ERROR: root memory control is invalid after the shared "
                    "pull. " + " | ".join(state.errors),
                )
                return 1
        _digest, _body, _paths, error = self.gate.acknowledge_union(event)
        if error is not None:
            self.emit_hook_context("PreMemory", "CONTROL ERROR: " + error)
            return 1
        context = (
            self.control.context_text(state)
            + "\n\n"
            + self.control.pre_memory_feature_text()
        )
        self.emit_hook_context("PreMemory", context)
        return 0

    def handle_memory_update(self, event: dict[str, Any], state: RootState) -> int:
        """Apply one structural memory mutation, or report the conflicts.

        A subagent never writes; the shared worktree must fast-forward first;
        the mutation itself is validated and performed by MemoryUpdater. Exit 0
        is success, exit 3 is a model-facing conflict report.
        """
        if is_subagent_event(event):
            self.emit_hook_context(
                "MemoryUpdate",
                "memory_update: CONFLICTS - fix these, then re-call the tool:\n"
                "- subagent-readonly: subagents have read-only access to memory; "
                "route this change back to the parent session (fix: call it from the parent).",
            )
            return 3
        if not state.errors and state.shared_git_backed:
            pulled, detail = self.pull_shared(state)
            if not pulled:
                self.emit_hook_context(
                    "MemoryUpdate",
                    "memory_update: CONFLICTS - fix these, then re-call the tool:\n"
                    "- shared-diverged: the declared shared worktree could not be "
                    f"fast-forwarded ({detail}) (fix: reconcile the shared history first).",
                )
                return 3
            state = self.control.load(self.agent)
        result = MemoryUpdater(self.control, state).run(event)
        self.emit_hook_context("MemoryUpdate", result.text())
        return 0 if result.ok else 3

    def compact_error(self, event: dict[str, Any], cause: str) -> int:
        """Refuse a manual compaction; warn and continue an automatic one.

        PreCompact honors no JSON decision field, so only a non-zero exit blocks
        it. Blocking an automatic compaction strands the session at the context
        ceiling, which destroys more continuity than the missing checkpoint does,
        so an automatic run is warned about instead. An unlabelled trigger is
        treated as manual.
        """
        if compaction_is_automatic(event):
            json.dump(
                {
                    "systemMessage": (
                        "Automatic compaction is continuing without a continuity checkpoint: "
                        + cause
                        + ". Reconfirm the active objective after compaction."
                    )
                },
                sys.stdout,
                separators=(",", ":"),
            )
            return 0
        reason = f"Compaction blocked: {cause}. Repair this, then compact again."
        if self.agent == "codex":
            json.dump({"continue": False, "stopReason": reason}, sys.stdout, separators=(",", ":"))
        print(reason, file=sys.stderr)
        return 2

    def handle_precompact(self, event: dict[str, Any], state: RootState) -> int:
        if state.errors:
            return self.compact_error(
                event, "root memory control is invalid. " + " | ".join(state.errors)
            )
        try:
            self.checkpoints.save(event)
        except Exception as exc:
            return self.compact_error(
                event, f"continuity checkpoint could not be saved: {exc}"
            )
        # The checkpoint is durable state for the compact-sourced SessionStart.
        # PreCompact systemMessage is UI feedback, not model context.
        return 0

    def input_targets_memory(self, event: dict[str, Any], state: RootState) -> bool:
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return False
        cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
        memory_root: Path = state.memory_root
        shared_resolved: Path | None = state.shared_resolved

        for raw in MutationScanner.target_strings(tool_input):
            for candidate in MutationScanner.candidate_paths(raw, cwd):
                if under(candidate, memory_root) or (shared_resolved is not None and under(candidate, shared_resolved)):
                    return True
        return False

    def input_targets_only_repair_paths(self, event: dict[str, Any], state: RootState) -> bool:
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not state.repair_paths:
            return False
        cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
        allowed = {
            os.path.normcase(str(path.resolve(strict=False))) for path in state.repair_paths
        }
        found = False
        for raw in MutationScanner.target_strings(tool_input):
            candidates: list[Path] = []
            direct = MutationScanner.path_from_string(raw, cwd)
            if direct is not None:
                candidates.append(direct)
            for value in PATCH_HEADER_RE.findall(raw):
                candidate = MutationScanner.path_from_string(value, cwd)
                if candidate is not None:
                    candidates.append(candidate)
            for candidate in candidates:
                found = True
                if os.path.normcase(str(candidate.resolve(strict=False))) not in allowed:
                    return False
        return found

    def handle_pretool(self, event: dict[str, Any], state: RootState) -> None:
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return
        targets_memory = self.input_targets_memory(event, state)
        if targets_memory and is_subagent_event(event):
            self.deny_pretool(
                "Subagents have read-only access to memory/shared content. "
                "Route this addition back to the parent session instead of writing it directly."
            )
            return

        if state.errors and not self.input_targets_only_repair_paths(event, state):
            self.deny_pretool(
                "Mutation blocked because root memory control or mandatory shared conventions "
                "are unavailable, malformed, or differ from the canonical hook checkout. "
                + " | ".join(state.errors)
                + ". Repair/read the root control files first."
            )
            return

        if state.errors:
            return

        if self.agent == "pi":
            allowed, reason = self.gate.receipt_covers(event, scoped=targets_memory)
            if not allowed:
                self.deny_pretool(reason)
                return
        else:
            allowed, reason = self.gate.gate(event, scoped=targets_memory)
            if not allowed:
                self.deny_pretool(reason, context=True)
                return

        if state.stale:
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": (
                            f"Memory protocol is stale ({state.applied} -> {state.canonical}). "
                            f"The authoritative RULES.md requires applying {state.migration} before ordinary memory work. "
                            "If this tool call is part of that migration, continue according to MIGRATION.md; otherwise migrate first."
                        ),
                    }
                },
                sys.stdout,
                separators=(",", ":"),
            )

    def handle_stop(self, event: dict[str, Any], state: RootState) -> None:
        # Both hosts set this flag when re-entering Stop/SubagentStop after a hook
        # already continued the turn. A second block can form an unbounded loop,
        # including when root state or the event identity cannot be repaired.
        if event.get("stop_hook_active") is True or event.get("stopHookActive") is True:
            return
        if state.errors:
            reason = (
                "Turn completion blocked because root memory control or mandatory shared "
                "conventions are invalid. " + " | ".join(state.errors)
            )
            json.dump({"decision": "block", "reason": reason}, sys.stdout, separators=(",", ":"))
            return
        allowed, reason = self.gate.gate(event, scoped=False)
        if not allowed:
            json.dump({"decision": "block", "reason": reason}, sys.stdout, separators=(",", ":"))
