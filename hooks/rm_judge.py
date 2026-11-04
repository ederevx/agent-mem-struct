"""Bounded convention judge.

One owner for the opt-in reviewer that reads the settled turns after a
watermark, sends one bounded request to a configured command, and keeps a
structured answer: rule ids, turn ids and string offsets into the supplied text.
The judge never writes a receipt; it asks the convention gate to record the
state it computed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from rm_control import RootState
from rm_receipts import ConventionGate, ReceiptTooLarge
from rm_support import read_text

CONFIG_NAME = "judge.json"
SCHEMA = 1
ENVIRONMENT_ALLOWLIST = ("PATH", "HOME", "LANG")
# The section heading the protocol itself validates; a manifest without it is
# not a convention manifest and contributes no rule unit.
UNIT_HEADING = re.compile(r"(?m)^## Mandatory conventions[ \t]*$")
EMPTY_MARKER = "(none)"
CONFIGURATION_KEYS = frozenset({
    "command", "timeout_s", "max_turns", "max_input_bytes", "max_output_bytes",
})
# A pipe buffer's size, not a policy: reads return as soon as the command writes.
READ_CHUNK = 65536
# `threading` refuses a longer wait and a host timer cannot hold a longer
# millisecond delay, so this is the largest `timeout_s` either side accepts.
TIMEOUT_MAX_S = min(threading.TIMEOUT_MAX, (2 ** 31 - 1) / 1000)


class JudgeError(Exception):
    """A local refusal code, never command-authored text."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object that refuses a repeated key instead of keeping the last."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key: {key}")
        seen.add(key)
    return dict(pairs)


def convention_section(text: str) -> str | None:
    """The complete mandatory-convention section, or None when it declares none."""
    match = UNIT_HEADING.search(text)
    if not match:
        return None
    lines: list[str] = []
    for line in text[match.end():].splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def convention_units(text: str) -> list[str]:
    """One unit per manifest: the whole section, so no convention text is lost."""
    section = convention_section(text)
    if section is None or section in ("", EMPTY_MARKER):
        return []
    return [section]


def entry_text(entry: dict[str, Any]) -> str:
    """Every model-visible part of one session entry, tool calls included."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else entry.get("content")
    if isinstance(content, str):
        return content
    return _blocks_text(content) if isinstance(content, list) else ""


def _blocks_text(blocks: list[Any]) -> str:
    """The text of one content list, naming each call it carries."""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
            continue
        name = block.get("name") if isinstance(block.get("name"), str) else block.get("toolName")
        if isinstance(name, str):
            # Pi names call arguments `input`, some hosts `arguments`.
            arguments = block.get("input") if "input" in block else block.get("arguments")
            parts.append(f"[{name}] {canonical(arguments if arguments is not None else {})}")
            continue
        nested = block.get("content")
        if isinstance(nested, str):
            parts.append(nested)
        elif isinstance(nested, list):
            parts.append(_blocks_text(nested))
    return "\n".join(part for part in parts if part)


class ConventionJudge:
    """Applies one judge action against the validated root-control snapshot."""

    def __init__(self, state: RootState, gate: ConventionGate) -> None:
        self.state = state
        self.gate = gate

    def config_path(self) -> Path:
        return self.state.home / ".agent-mem-struct" / CONFIG_NAME

    def settings(self) -> dict[str, Any]:
        """The configured judge; an absent file disables it, an invalid one refuses."""
        path = self.config_path()
        if not path.exists() and not path.is_symlink():
            raise JudgeError("disabled")
        text, error = read_text(path) if path.is_file() else (None, "not a file")
        if error or text is None:
            raise JudgeError("invalid_config")
        try:
            config = json.loads(text, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_config") from None
        if not isinstance(config, dict) or set(config) != CONFIGURATION_KEYS:
            raise JudgeError("invalid_config")
        command = config["command"]
        if (
            not isinstance(command, list) or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise JudgeError("invalid_config")
        return {
            "command": list(command),
            "timeout_s": self._number(
                config, "timeout_s", integer=False, maximum=TIMEOUT_MAX_S
            ),
            "max_turns": self._number(config, "max_turns", integer=True),
            "max_input_bytes": self._number(config, "max_input_bytes", integer=True),
            "max_output_bytes": self._number(config, "max_output_bytes", integer=True),
        }

    @staticmethod
    def _number(
        config: dict[str, Any],
        key: str,
        *,
        integer: bool,
        maximum: float | None = None,
    ) -> int | float:
        """A positive finite representable number of the configured kind."""
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
            raise JudgeError("invalid_config")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            # An integer beyond the float range is finite in neither sense.
            raise JudgeError("invalid_config") from None
        if not finite or not value > 0:
            raise JudgeError("invalid_config")
        if integer and value > sys.maxsize:
            raise JudgeError("invalid_config")
        if maximum is not None and value > maximum:
            # A finite number is not automatically one the waiter or the host
            # timer can hold; refuse it here instead of failing after a spawn.
            raise JudgeError("invalid_config")
        return value

    def cursor(self, event: dict[str, Any]) -> dict[str, Any]:
        """The watermark this session's receipt holds, or the empty cursor."""
        return self._cursor_state(self.gate.read_receipt(event).get("judge"))

    @staticmethod
    def _cursor_state(judge: Any) -> dict[str, Any]:
        """The watermark a receipt holds; absent or uninitialized is the empty one.

        A state is initialized only by a valid window, which writes a nonempty
        epoch and anchor together. A refusal never manufactures one, so a state
        that carries an error but no anchor stays uninitialized and the next
        valid window establishes it, rather than being read as a watermark that
        makes every later review find nothing to do.
        """
        empty: dict[str, Any] = {"epoch": None, "anchor": None, "seq": 0, "chain": None}
        if judge is None:
            return dict(empty)
        if not isinstance(judge, dict):
            raise JudgeError("invalid_cursor")
        epoch, anchor, chain = judge.get("epoch"), judge.get("anchor"), judge.get("chain")
        seq = judge.get("seq", 0)
        if anchor is None:
            if chain not in (None, "") or seq not in (0, None) or isinstance(seq, bool):
                raise JudgeError("invalid_cursor")
            return dict(empty)
        if not isinstance(epoch, str) or not epoch:
            raise JudgeError("invalid_cursor")
        if not isinstance(anchor, str) or not anchor:
            raise JudgeError("invalid_cursor")
        if chain is not None and not isinstance(chain, str):
            raise JudgeError("invalid_cursor")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise JudgeError("invalid_cursor")
        return {"epoch": epoch, "anchor": anchor, "seq": seq, "chain": chain}

    def prepare(self, event: dict[str, Any]) -> dict[str, Any]:
        """Report the cursor and transport limits; never runs the command."""
        try:
            settings = self.settings()
            cursor = self.cursor(event)
        except JudgeError as refusal:
            return {"enabled": False, "error": str(refusal), "cursor": None, "limits": None}
        return {
            "enabled": True,
            "error": None,
            "cursor": cursor,
            "limits": {
                key: settings[key]
                for key in ("timeout_s", "max_turns", "max_input_bytes", "max_output_bytes")
            },
        }

    def review(self, event: dict[str, Any]) -> dict[str, Any]:
        """Judge the turns after the watermark, or record why it did not.

        Never raises: a refusal that cannot even be recorded is still reported.
        """
        try:
            return self._review(event)
        except JudgeError as refusal:
            code = str(refusal)
        except ReceiptTooLarge:
            code = "oversized_receipt"
        except (OSError, ValueError):
            code = "persistence_failed"
        if code == "disabled":
            return self.result(code, [])
        try:
            return self._refused(event, code)
        except (JudgeError, OSError, ValueError):
            # The refusal itself could not be recorded, so the code is reported
            # as unsaved instead of being presented as a stored one.
            return self.result("persistence_failed", [])

    def _review(self, event: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        if "cursor" not in event:
            raise JudgeError("invalid_cursor")
        if event["cursor"] != self.cursor(event):
            raise JudgeError("stale_cursor")
        entries = self.window(event)
        leaf = str(event.get("leaf_id") or "")
        session_leaf = event.get("session_leaf_id")
        # A baseline claims no review, so it belongs at the session's own leaf.
        # Anchoring it at a bounded prefix's end would review the turns between
        # that end and the leaf, which predate the activation.
        baseline_at = str(session_leaf or leaf)
        state = self._cursor_state(self.gate.read_receipt(event).get("judge"))
        if state["anchor"] is None:
            # First valid window: a baseline claims no historical review.
            self._store(event, self._baseline(baseline_at, None))
            return self.result(None, [])
        delta = self.delta(entries, state["anchor"], int(settings["max_turns"]))
        if delta is None:
            self._store(event, self._baseline(baseline_at, "coverage_gap"))
            return self.result("coverage_gap", [])
        if not delta:
            if session_leaf is not None and session_leaf != leaf:
                # The window stops at the reviewed anchor while the session runs
                # past it, so the next turn did not fit the transport budget.
                raise JudgeError("oversized_input")
            return self.result(None, [])
        rules = self.rules(event)
        request, payload = self._fit(event, state, delta, rules, int(settings["max_input_bytes"]))
        findings = self._validate(
            self._execute(settings, payload), request["chain"], request["turns"], rules
        )
        self._store(event, {
            "epoch": state["epoch"],
            "anchor": request["anchor"],
            "seq": request["seq"],
            "chain": request["chain"],
            "findings": findings,
            "error": None,
        })
        return self.result(None, findings)

    def window(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """The supplied branch snapshot, refused unless it is one chain to its end.

        `leaf_id` is the end of the supplied window, which a bounded prefix may
        place before the session's own leaf; `session_leaf_id`, when the host
        reports it, names that larger branch and never has to match.
        """
        raw = event.get("session_entries")
        leaf = event.get("leaf_id")
        session_leaf = event.get("session_leaf_id")
        if session_leaf is not None and (not isinstance(session_leaf, str) or not session_leaf):
            raise JudgeError("invalid_window")
        if not isinstance(raw, list) or not raw or not isinstance(leaf, str):
            raise JudgeError("invalid_window")
        ids: list[str] = []
        for entry in raw:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise JudgeError("invalid_window")
            if not entry["id"] or entry["id"] in ids:
                raise JudgeError("invalid_window")
            ids.append(entry["id"])
        for previous, entry in zip(raw, raw[1:]):
            if entry.get("parentId") != previous["id"]:
                raise JudgeError("invalid_window")
        if ids[-1] != leaf:
            raise JudgeError("invalid_window")
        return raw

    def delta(
        self, entries: list[dict[str, Any]], anchor: str | None, max_turns: int
    ) -> list[dict[str, Any]] | None:
        """The oldest turns after the anchor, or None when it fell out of view."""
        if anchor is None:
            return []
        position = next((index for index, entry in enumerate(entries) if entry["id"] == anchor), None)
        if position is None:
            return None
        return [
            {"id": entry["id"], "text": entry_text(entry)}
            for entry in entries[position + 1:position + 1 + max_turns]
        ]

    def rules(self, event: dict[str, Any]) -> list[dict[str, str]]:
        """The rule index, refused when a source changed since it was read."""
        recorded = self.gate.read_receipt(event).get("sources")
        sources = recorded if isinstance(recorded, dict) else {}
        index: list[dict[str, str]] = []
        for path in self.gate.required_read_paths(event, scoped=False):
            text, error = read_text(path)
            if error or text is None:
                raise JudgeError("unreadable_rules")
            key = os.path.normcase(str(path.resolve(strict=False)))
            if sources.get(key) != digest(text):
                raise JudgeError("stale_rules")
            units = convention_units(text)
            for unit in units:
                index.append({
                    "rule": digest(f"{key}\0{unit}"),
                    "path": str(path),
                    "text": unit,
                })
        return index

    def _fit(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
        delta: list[dict[str, Any]],
        rules: list[dict[str, str]],
        limit: int,
    ) -> tuple[dict[str, Any], str]:
        """The newest-anchored request that fits the byte budget, trimming turns."""
        while delta:
            request = self._request(event, state, delta, rules)
            payload = canonical(request)
            if len(payload.encode("utf-8")) <= limit:
                return request, payload
            delta = delta[:-1]
        raise JudgeError("oversized_input")

    def _request(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
        delta: list[dict[str, Any]],
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "schema": SCHEMA,
            "epoch": state.get("epoch"),
            "seq": int(state.get("seq") or 0) + 1,
            "anchor": delta[-1]["id"],
            "turns": delta,
            "rules": rules,
        }
        request["chain"] = self.chain(state, request["anchor"], canonical(request))
        return request

    @staticmethod
    def chain(state: dict[str, Any], anchor: str, payload: str) -> str:
        """Bind one answer to one request: previous chain, position, anchor, bytes."""
        previous = state.get("chain") if isinstance(state.get("chain"), str) else ""
        return digest(canonical([
            previous,
            state.get("epoch"),
            int(state.get("seq") or 0) + 1,
            anchor,
            digest(payload),
        ]))

    def _execute(self, settings: dict[str, Any], payload: str) -> str:
        """Run the configured argv without a shell, deadline-bound and byte-bounded."""
        environment = {
            name: os.environ[name] for name in ENVIRONMENT_ALLOWLIST if name in os.environ
        }
        try:
            process = subprocess.Popen(
                settings["command"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            raise JudgeError("command_failed") from None
        limit = int(settings["max_output_bytes"])
        collected = bytearray()
        closed = threading.Event()
        overflow = threading.Event()

        # Reading off the main thread is what lets the configured deadline cover
        # a silent command: a blocked read in this thread could never be timed out.
        def drain() -> None:
            while True:
                chunk = process.stdout.read(READ_CHUNK)
                if not chunk:
                    break
                collected.extend(chunk)
                if len(collected) > limit:
                    # Stop reading a flood rather than waiting for it to finish.
                    overflow.set()
                    break
            closed.set()

        def feed() -> None:
            try:
                process.stdin.write(payload.encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError):
                pass

        def supervise() -> None:
            # The leader exiting does not close pipes a descendant inherited,
            # so its exit is what ends the command's session.
            process.wait()
            self._terminate(process)

        deadline = time.monotonic() + float(settings["timeout_s"])
        threading.Thread(target=drain, daemon=True).start()
        threading.Thread(target=feed, daemon=True).start()
        threading.Thread(target=supervise, daemon=True).start()

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        try:
            if not closed.wait(timeout=remaining()):
                raise JudgeError("timeout")
            if overflow.is_set():
                # The output is already rejected; its exit code cannot change that.
                raise JudgeError("oversized_output")
            if process.wait(timeout=remaining()) != 0:
                raise JudgeError("command_failed")
        except subprocess.TimeoutExpired:
            raise JudgeError("timeout") from None
        finally:
            self._terminate(process)
        try:
            return bytes(collected).decode("utf-8")
        except UnicodeDecodeError:
            raise JudgeError("invalid_response") from None

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        """Kill the command's whole session, not only its leader.

        A descendant that inherited the pipes outlives the leader and would
        otherwise hold this hook open past the deadline.
        """
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()

    def _validate(
        self,
        raw: str,
        chain: str,
        delta: list[dict[str, Any]],
        rules: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Accept only a well-formed, bound answer whose evidence resolves exactly."""
        try:
            answer = json.loads(raw, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_response") from None
        if not isinstance(answer, dict) or set(answer) != {"schema", "chain", "verdict", "findings"}:
            raise JudgeError("invalid_response")
        if type(answer["schema"]) is not int or answer["schema"] != SCHEMA:
            raise JudgeError("invalid_response")
        if answer["chain"] != chain or not isinstance(answer["verdict"], str):
            raise JudgeError("invalid_response")
        findings = answer["findings"]
        if not isinstance(findings, list):
            raise JudgeError("invalid_response")
        if (answer["verdict"], bool(findings)) not in {("pass", False), ("fail", True)}:
            raise JudgeError("invalid_response")
        known = {rule["rule"] for rule in rules}
        turns = {turn["id"]: turn["text"] for turn in delta}
        resolved: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != {"rule", "turn", "quote"}:
                raise JudgeError("invalid_response")
            rule, turn, quote = finding["rule"], finding["turn"], finding["quote"]
            if not all(isinstance(value, str) for value in (rule, turn, quote)):
                raise JudgeError("invalid_response")
            if rule not in known or turn not in turns or not quote.strip():
                raise JudgeError("invalid_response")
            start = turns[turn].find(quote)
            if start < 0 or start != turns[turn].rfind(quote):
                raise JudgeError("invalid_response")
            if (rule, turn, start) in seen:
                raise JudgeError("invalid_response")
            seen.add((rule, turn, start))
            resolved.append({
                "rule": rule,
                "turn": turn,
                "start": start,
                "end": start + len(quote),
            })
        return resolved

    @staticmethod
    def _baseline(leaf: str, error: str | None) -> dict[str, Any]:
        """A fresh epoch anchored at the current leaf, claiming no review."""
        return {
            "epoch": uuid.uuid4().hex,
            "anchor": leaf or None,
            "seq": 0,
            "chain": "",
            "findings": [],
            "error": error,
        }

    def _store(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        """Record one judge state; the receipt writer bounds and refuses it."""
        self.gate.record_judge(event, state)

    def _refused(self, event: dict[str, Any], code: str) -> dict[str, Any]:
        """Record one local refusal without moving the watermark or losing findings.

        A session with no watermark has nothing to annotate, and a refusal is
        not evidence of one: that state is left uninitialized for the next
        valid window to establish.
        """
        judge = self.gate.read_receipt(event).get("judge")
        try:
            state = self._cursor_state(judge)
        except JudgeError:
            return self.result(code, [])
        if state["anchor"] is None or not isinstance(judge, dict):
            return self.result(code, [])
        recorded = dict(judge)
        recorded["error"] = code
        self._store(event, recorded)
        return self.result(code, [])

    def result(
        self,
        error: str | None,
        findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The review outcome: local code, resolved findings, and one short notice."""
        return {
            "error": error,
            "findings": findings,
            "notice": self._notice(error, findings),
        }

    # Codes where the command may have produced a result that cannot be accepted.
    UNAVAILABLE = frozenset(
        {"invalid_response", "oversized_output", "timeout", "command_failed"}
    )
    # Codes where the outcome is known locally but could not be persisted.
    UNSAVED = frozenset({"persistence_failed", "oversized_receipt"})

    @classmethod
    def _notice(cls, error: str | None, findings: list[dict[str, Any]]) -> str:
        """A short, judge-independent notice naming only locally resolved evidence."""
        if error == "coverage_gap":
            return (
                "convention judge: the reviewed window no longer reaches the last "
                "settled turn; review restarted at the current turn and the skipped "
                "turns were NOT reviewed."
            )
        if error in cls.UNSAVED:
            return (
                f"convention judge: the review state could not be saved ({error}); "
                "the previous review state stands and no finding from this review "
                "was recorded."
            )
        if error in cls.UNAVAILABLE:
            return (
                f"convention judge: the review result is unavailable ({error}); "
                "no finding from this review was recorded."
            )
        if error is not None:
            return f"convention judge: the review did not run ({error})."
        if not findings:
            return ""
        cited = "; ".join(
            f"{finding['rule']} at {finding['turn']} [{finding['start']}:{finding['end']}]"
            for finding in findings
        )
        return (
            f"convention judge: {len(findings)} unmet convention finding(s): {cited}. "
            "Fix them or record why they do not apply."
        )
