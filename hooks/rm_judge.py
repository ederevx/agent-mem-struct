"""Bounded convention judge.

One owner for the reviewer that reads the settled turns after a watermark,
sends one bounded request to a configured command, and keeps a structured
answer: rule ids, turn ids and string offsets into the supplied text. The
judge never writes a receipt; it asks the convention gate to record the
state it computed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from rm_control import RootState
from rm_judge_settings import JudgeError, JudgeSettings
from rm_judge_window import canonical, window as build_window
from rm_receipts import ConventionGate, ReceiptTooLarge
from rm_support import read_text

SCHEMA = 2
ENVIRONMENT_ALLOWLIST = ("PATH", "HOME", "LANG")
# The section heading the protocol itself validates; a manifest without it is
# not a convention manifest and contributes no rule unit.
UNIT_HEADING = re.compile(r"(?m)^## Mandatory conventions[ \t]*$")
EMPTY_MARKER = "(none)"
# A pipe buffer's size, not a policy: reads return as soon as the command writes.
READ_CHUNK = 65536
FINDING = re.compile(r"^([1-9][0-9]*) ([1-9][0-9]*) (.*)$", re.S)


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


class ConventionJudge:
    """Applies one judge action against the validated root-control snapshot."""

    def __init__(
        self, state: RootState, gate: ConventionGate, settings: JudgeSettings | None = None
    ) -> None:
        self.state = state
        self.gate = gate
        self.settings = settings

    def configuration(self) -> tuple[JudgeSettings, dict[str, Any]]:
        """The settings owner and its current document, read once per action."""
        owner = self.settings or JudgeSettings(self.state)
        return owner, owner.read()

    def cursor(self, event: dict[str, Any]) -> dict[str, Any]:
        """The watermark this session's receipt holds, or the empty cursor."""
        return self._cursor_state(self.gate.read_receipt(event).get("judge"))

    @staticmethod
    def _cursor_state(judge: Any) -> dict[str, Any]:
        """The watermark a receipt holds; absent or uninitialized is the empty one.

        A state is initialized only by a valid window, which writes a nonempty
        epoch. A refusal never manufactures one, so a state that carries an
        error but no epoch stays uninitialized and the next valid window
        establishes it, rather than being read as a watermark that makes every
        later review find nothing to do. An initialized state with no anchor is
        an empty-origin baseline: it names the start of a session that had no
        turns yet, so the next settle reviews all of them.
        """
        empty: dict[str, Any] = {"epoch": None, "anchor": None, "seq": 0, "chain": None,
                                 "source": None}
        if judge is None:
            return dict(empty)
        if not isinstance(judge, dict):
            raise JudgeError("invalid_cursor")
        epoch, anchor, chain = judge.get("epoch"), judge.get("anchor"), judge.get("chain")
        seq = judge.get("seq", 0)
        source = judge.get("source")
        if epoch is None:
            if anchor is not None or chain not in (None, "") or seq not in (0, None) or isinstance(seq, bool):
                raise JudgeError("invalid_cursor")
            return dict(empty)
        if not isinstance(epoch, str) or not epoch:
            raise JudgeError("invalid_cursor")
        if anchor is not None and (not isinstance(anchor, str) or not anchor):
            raise JudgeError("invalid_cursor")
        if chain is not None and not isinstance(chain, str):
            raise JudgeError("invalid_cursor")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise JudgeError("invalid_cursor")
        if source is not None and not isinstance(source, dict):
            raise JudgeError("invalid_cursor")
        return {"epoch": epoch, "anchor": anchor, "seq": seq, "chain": chain, "source": source}

    def prepare(self, event: dict[str, Any]) -> dict[str, Any]:
        """Report the cursor and transport limits; never runs the command."""
        try:
            owner, config = self.configuration()
            cursor = self.cursor(event)
            revision = config["revision"]
        except JudgeError as refusal:
            return {"enabled": False, "error": str(refusal), "cursor": None, "limits": None,
                    "revision": None}
        if not config["enabled"]:
            return {"enabled": False, "error": "disabled", "cursor": cursor, "limits": None,
                    "revision": revision}
        try:
            launch = owner.launch(config)
        except JudgeError as refusal:
            return {"enabled": False, "error": str(refusal), "cursor": cursor, "limits": None,
                    "revision": revision}
        return {
            "enabled": True,
            "error": None,
            "cursor": cursor,
            "limits": {
                key: config[key]
                for key in ("timeout_s", "max_turns", "max_input_bytes", "max_output_bytes",
                            "protocol")
            },
            "revision": revision,
            "settings_path": config["path"],
            "launch": launch,
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
        owner, config = self.configuration()
        if not config["enabled"]:
            raise JudgeError("disabled")
        if "cursor" not in event:
            raise JudgeError("invalid_cursor")
        if event["cursor"] != self.cursor(event):
            raise JudgeError("stale_cursor")
        if event.get("config_revision") != config["revision"]:
            raise JudgeError("stale_config")
        launch = owner.launch(config)
        window = build_window(self.state, event)
        rules = self.rules(event)
        state = self._cursor_state(self.gate.read_receipt(event).get("judge"))
        if state["epoch"] is None:
            # First valid window: a baseline claims no historical review.
            self._store(event, self._baseline(window))
            return self.result("baseline", [])
        anchor = state["anchor"]
        if anchor is not None:
            self._continuity(window, state)
        delta = window.turns(anchor) if anchor is not None else self._all_turns(window)
        if delta is None:
            self._store(event, self._baseline(window, "coverage_gap"))
            return self.result("coverage_gap", [])
        if not delta:
            if event.get("session_leaf_id") and window.leaf() != event["session_leaf_id"]:
                # The window stops at the reviewed anchor while the session runs
                # past it, so the next turn did not fit the transport budget.
                raise JudgeError("oversized_input")
            return self.result("noop", [])
        delta = delta[: int(config["max_turns"])]
        request, payload = self._fit(config, window, state, delta, rules)
        findings = self._validate(
            config, self._execute(config, launch, payload), request, delta, rules
        )
        self._store(event, {
            "epoch": state["epoch"],
            "anchor": request["anchor"],
            "seq": request["seq"],
            "chain": request["chain"],
            "findings": findings,
            "error": None,
            "source": window.boundary(request["anchor"]),
        })
        return self.result(None, findings)

    @staticmethod
    def _all_turns(window: Any) -> list[dict[str, str]]:
        """Every reviewable turn of an empty-origin baseline's session."""
        return [{"id": entry_id, "text": window.reviewable[entry_id]}
                for entry_id in window.positions if entry_id in window.reviewable]

    def _continuity(self, window: Any, state: dict[str, Any]) -> None:
        """Refuse an anchor whose recorded history the source no longer holds."""
        recorded = state["source"]
        anchor = state["anchor"]
        if anchor is None:
            return
        if not isinstance(recorded, dict) or recorded.get("digest") is None:
            raise JudgeError("invalid_window")
        if recorded.get("host") != window.host or recorded.get("session") != window.session:
            raise JudgeError("invalid_window")
        if window.digest(anchor) != recorded.get("digest"):
            # The same identifier over different history is not a new branch;
            # it is a rewritten source this judge will not review silently.
            raise JudgeError("invalid_window")

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
            for unit in convention_units(text):
                index.append({"rule": digest(f"{key}\0{unit}"), "path": str(path), "text": unit})
        return index

    def _fit(
        self,
        config: dict[str, Any],
        window: Any,
        state: dict[str, Any],
        delta: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> tuple[dict[str, Any], str]:
        """The newest-anchored request that fits the byte budget, trimming turns."""
        while delta:
            request = self._request(config, window, state, delta, rules)
            payload = self._payload(config, request)
            if len(payload.encode("utf-8")) <= int(config["max_input_bytes"]):
                return request, payload
            delta = delta[:-1]
        raise JudgeError("oversized_input")

    def _request(
        self,
        config: dict[str, Any],
        window: Any,
        state: dict[str, Any],
        delta: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        """One request body plus the local identities its indices resolve to."""
        seq = int(state["seq"]) + 1
        anchor = delta[-1]["id"]
        request: dict[str, Any] = {
            "schema": 2,
            "seq": seq,
            "anchor": anchor,
            "turns": delta,
            "rules": rules,
        }
        if int(config["protocol"]) == 1:
            request["epoch"] = state["epoch"]
            request["chain"] = self._chain(state, seq, anchor, canonical(self._v1_body(request)))
            return request
        body = {
            "schema": 2,
            "turns": [turn["text"] for turn in delta],
            "rules": [[rule["path"], rule["text"]] for rule in rules],
        }
        identities = [[turn["id"] for turn in delta], [rule["rule"] for rule in rules]]
        request["chain"] = self._chain(
            state, seq, anchor, canonical([body, identities])
        )
        return request

    @staticmethod
    def _v1_body(request: dict[str, Any]) -> dict[str, Any]:
        """The pre-schema request shape, kept for a document that pins protocol 1."""
        return {
            "schema": 1,
            "epoch": request["epoch"],
            "seq": request["seq"],
            "anchor": request["anchor"],
            "turns": request["turns"],
            "rules": request["rules"],
        }

    @classmethod
    def _payload(cls, config: dict[str, Any], request: dict[str, Any]) -> str:
        """The exact bytes one command receives, in the protocol it speaks."""
        if int(config["protocol"]) == 1:
            return canonical(cls._v1_body(request) | {"chain": request["chain"]})
        return canonical({
            "schema": 2,
            "chain": request["chain"],
            "turns": [turn["text"] for turn in request["turns"]],
            "rules": [[rule["path"], rule["text"]] for rule in request["rules"]],
        })

    @staticmethod
    def _chain(state: dict[str, Any], seq: int, anchor: str, bound: str) -> str:
        """Bind one answer to one request: previous chain, position, anchor, bytes."""
        previous = state.get("chain") if isinstance(state.get("chain"), str) else ""
        return digest(canonical([previous, state.get("epoch"), seq, anchor, digest(bound)]))

    def _execute(self, config: dict[str, Any], launch: dict[str, Any], payload: str) -> str:
        """Run the configured argv without a shell, deadline-bound and byte-bounded."""
        environment = {
            name: os.environ[name] for name in ENVIRONMENT_ALLOWLIST if name in os.environ
        }
        for name in launch.get("extra_env") or ():
            if name in os.environ:
                environment[name] = os.environ[name]
        try:
            process = subprocess.Popen(
                launch["argv"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            raise JudgeError("command_failed") from None
        limit = int(config["max_output_bytes"])
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

        deadline = time.monotonic() + float(config["timeout_s"])
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
        config: dict[str, Any],
        raw: str,
        request: dict[str, Any],
        delta: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Accept only a well-formed answer bound to the request it answers."""
        if int(config["protocol"]) == 1:
            return self._validate_v1(raw, request, delta, rules)
        return self._validate_v2(raw, request["chain"], delta, rules)

    def _validate_v2(
        self,
        raw: str,
        chain: str,
        delta: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """The protocol-2 grammar: a chain line, then `pass` or finding lines."""
        if "\r" in raw:
            raise JudgeError("invalid_response")
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            # Exactly one optional terminal newline, and nothing after it.
            lines.pop()
        if not lines or lines[0] != chain:
            raise JudgeError("invalid_response")
        findings = lines[1:]
        if findings == ["pass"]:
            return []
        if not findings:
            raise JudgeError("invalid_response")
        texts = {turn["id"]: turn["text"] for turn in delta}
        turns = [turn["id"] for turn in delta]
        known = [rule["rule"] for rule in rules]
        resolved: list[dict[str, Any]] = []
        previous = (-1, -1, -1)
        for line in findings:
            match = FINDING.match(line)
            if match is None:
                raise JudgeError("invalid_response")
            rule_index, turn_index, quoted = int(match[1]), int(match[2]), match[3]
            if not (1 <= rule_index <= len(known) and 1 <= turn_index <= len(turns)):
                raise JudgeError("invalid_response")
            quote = self._string(quoted)
            if not quote.strip():
                raise JudgeError("invalid_response")
            text = texts[turns[turn_index - 1]]
            start = text.find(quote)
            if start < 0 or start != text.rfind(quote):
                raise JudgeError("invalid_response")
            order = (rule_index, turn_index, start)
            if order <= previous:
                # Strictly increasing order rejects replays and duplicates.
                raise JudgeError("invalid_response")
            previous = order
            resolved.append({
                "rule": known[rule_index - 1],
                "turn": turns[turn_index - 1],
                "start": start,
                "end": start + len(quote),
            })
        return resolved

    @staticmethod
    def _string(quoted: str) -> str:
        """One JSON string literal and nothing else."""
        try:
            value = json.loads(quoted)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_response") from None
        if not isinstance(value, str):
            raise JudgeError("invalid_response")
        return value

    def _validate_v1(
        self,
        raw: str,
        request: dict[str, Any],
        delta: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """The pre-schema JSON answer, kept for a document that pins protocol 1."""
        try:
            answer = json.loads(raw, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_response") from None
        if not isinstance(answer, dict) or set(answer) != {"schema", "chain", "verdict", "findings"}:
            raise JudgeError("invalid_response")
        if type(answer["schema"]) is not int or answer["schema"] != 1:
            raise JudgeError("invalid_response")
        if answer["chain"] != request["chain"] or not isinstance(answer["verdict"], str):
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
            resolved.append({"rule": rule, "turn": turn, "start": start,
                             "end": start + len(quote)})
        return resolved

    @staticmethod
    def _baseline(window: Any, error: str | None = None) -> dict[str, Any]:
        """A fresh epoch anchored at the window's own leaf, claiming no review."""
        leaf = window.leaf()
        return {
            "epoch": uuid.uuid4().hex,
            "anchor": leaf or None,
            "seq": 0,
            "chain": "",
            "findings": [],
            "error": error,
            "source": window.boundary(leaf) if leaf else None,
        }

    def _store(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        """Record one judge state; the receipt writer bounds and refuses it.

        A notice the previous prompt has not collected yet survives the write:
        it belongs to the turn that produced it, not to the state replacing it.
        """
        current = self.gate.read_receipt(event).get("judge")
        notice = current.get("notice") if isinstance(current, dict) else None
        if isinstance(notice, str) and notice:
            state = {**state, "notice": notice}
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
        if state["epoch"] is None or not isinstance(judge, dict):
            return self.result(code, [])
        recorded = dict(judge)
        recorded["error"] = code
        self._store(event, recorded)
        return self.result(code, [])

    def result(self, error: str | None, findings: list[dict[str, Any]]) -> dict[str, Any]:
        """The review outcome: local code, resolved findings, and one short notice."""
        return {
            "error": error,
            "findings": findings,
            "notice": self._notice(error, findings),
        }

    # Codes where the command may have produced a result that cannot be accepted.
    UNAVAILABLE = frozenset({"invalid_response", "oversized_output", "timeout", "command_failed"})
    # Codes where the outcome is known locally but could not be persisted.
    UNSAVED = frozenset({"persistence_failed", "oversized_receipt"})
    # Codes where the review did not run, because no command was invoked.
    NOT_RUN = frozenset({
        "disabled", "invalid_config", "invalid_cursor", "invalid_window", "stale_cursor",
        "stale_config", "unreadable_rules", "stale_rules", "oversized_input",
        "command_required", "unsupported_host", "settings_busy",
        "invalid_settings_request", "settings_write_failed", "settings_conflict",
    })

    @classmethod
    def _notice(cls, error: str | None, findings: list[dict[str, Any]]) -> str:
        """A short, judge-independent notice naming only locally resolved evidence."""
        if error in ("baseline", "noop"):
            # A baseline claims no review and a quiet window changes nothing;
            # neither belongs in model context.
            return ""
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
        if error in cls.NOT_RUN:
            return f"convention judge: the review did not run ({error})."
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
