"""The conventions one memory update is assessed against, one by one.

`ConventionChecklist` is the one owner for both halves of that assessment: it
enumerates every convention an update is bound by from the sources already
captured for it, and it checks the caller's own checklist of those conventions.

Enumeration is mechanical. A numbered item of a rules document and a bullet of
a *Mandatory conventions* section is one convention each; everything else in a
governing document binds the writer without receiving an entry of its own. The
parser never guesses: unsupported markup, an unclosed code fence or content
that belongs to no convention is a refusal, because a convention that silently
falls out of the enumeration would never be assessed at all.

The checklist is bound to the exact plan and inventory it was written for, and
it is consumed and discarded: it is never written, recorded or repeated back,
and nothing of it outlives the evaluation it passed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")
ENVELOPE_KEYS = frozenset(
    {"schema", "plan_digest", "documents_digest", "separation", "checks"}
)
SEPARATION_KEYS = frozenset({"significant", "not_applicable"})
REFERENCE_KEYS = frozenset({"source", "ordinal"})
PASS_KEYS = frozenset({"source", "ordinal", "status", "reasoning"})
VIOLATION_KEYS = PASS_KEYS | {"fix"}
STATUSES = frozenset({"pass", "not-applicable"})

RULES_KIND = "rules"
MEMORY_KIND = "memory"
RULES_HEADING = "Memory rules"
CONVENTIONS_HEADING = "Mandatory conventions"
FENCE_RE = re.compile(r"\A[ \t]*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"\A(#{1,6})[ \t]+(.*?)[ \t]*\Z")
NUMBERED_RE = re.compile(r"\A(\d+)\.[ \t]+")
BULLET = "- "
FOOTER = "---"

CHECKLIST_FIX = (
    "Submit one assessment for every convention listed below, each with its "
    "source, ordinal, status and reasoning, and a separation that classifies "
    "every one of them."
)
SEPARATION_FIX = (
    "List every enumerated convention exactly once in significant or "
    "not_applicable, order significant by compliance priority, and make "
    "each assessment agree with its classification."
)
SCHEMA_FIX = (
    "Correct the checklist to match the documented schema and re-call with it."
)
PLAN_FIX = (
    "Assess the recomputed change below and re-call with its digests."
)
DOCUMENTS_FIX = (
    "Assess the current conventions listed below and re-call with their "
    "digests."
)
VIOLATION_FIX = "Correct the proposed update and submit a new checklist."


class ConventionError(Exception):
    """A convention region that cannot be enumerated safely."""


def canonical(value: Any) -> bytes:
    """One value as the exact bytes a digest is taken over."""
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


def source_key(path: Path | str) -> str:
    """The identity one governing source is known by in every binding."""
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def inventory_digest(rows: list[dict[str, Any]]) -> str:
    """The digest of the governing inventory: its paths and their bytes."""
    return hashlib.sha256(
        canonical(
            [{"path": row["path"], "digest": row["digest"]} for row in rows]
        )
    ).hexdigest()


def bytes_field(value: bytes | None) -> dict[str, Any]:
    """A byte value as the JSON a digest is taken over, absence kept apart."""
    if value is None:
        return {"absent": True}
    return {
        "size": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "base64": base64.b64encode(value).decode("ascii"),
    }


def _refusal(code: str, message: str, fix: str) -> dict[str, str]:
    return {"code": code, "message": message, "fix": fix}


def _invalid(detail: str) -> dict[str, str]:
    return _refusal(
        "checklist-invalid",
        f"Invalid convention checklist: {detail}.",
        SCHEMA_FIX,
    )


def _separation_missing() -> dict[str, str]:
    return _refusal(
        "checklist-separation-missing",
        "A convention separation is required for this memory update.",
        SEPARATION_FIX,
    )


def _separation_invalid() -> dict[str, str]:
    return _refusal(
        "checklist-separation-invalid",
        "Invalid convention separation: expected `significant` and "
        "`not_applicable` lists of `source` and `ordinal` references.",
        SEPARATION_FIX,
    )


def _separation_coverage() -> dict[str, str]:
    return _refusal(
        "checklist-separation-coverage",
        "The convention separation must classify every enumerated convention "
        "exactly once.",
        SEPARATION_FIX,
    )


def _separation_mismatch() -> dict[str, str]:
    return _refusal(
        "checklist-separation-mismatch",
        "The convention separation disagrees with the checklist assessments.",
        SEPARATION_FIX,
    )


def _indent(line: str) -> int:
    """How far one line is indented from the document's left margin."""
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))


def _trim(lines: list[str]) -> str:
    """One convention's lines as its exact text, trailing blanks dropped."""
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


class ConventionChecklist:
    """Every convention of one update, and the checklist that assesses them."""

    def enumerate(
        self, inventory: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Every convention of this update, in capture and document order.

        A source that is absent or carries no convention region contributes
        nothing; a source whose region cannot be read safely refuses the whole
        update, since the conventions that fell out cannot be assessed.
        """
        conventions: list[dict[str, Any]] = []
        for row in inventory:
            if row.get("digest") is None:
                continue
            kind = row.get("kind")
            if kind not in (RULES_KIND, MEMORY_KIND):
                continue
            try:
                items = self.items(str(row.get("text") or ""), kind)
            except ConventionError as failure:
                raise ConventionError(f"{row['path']}: {failure}") from None
            for ordinal, text in enumerate(items, start=1):
                conventions.append(
                    {
                        "source": source_key(row["path"]),
                        "ordinal": ordinal,
                        "text": text,
                    }
                )
        return conventions

    def items(self, text: str, kind: str) -> list[str]:
        """Every convention one governing document states, in its own order."""
        rows = self.scan(text)
        if kind == RULES_KIND:
            return self.numbered(rows)
        return self.bullets(self.section(rows))

    def scan(self, text: str) -> list[tuple[str, bool]]:
        """Every line of one document, with whether a code fence holds it."""
        rows: list[tuple[str, bool]] = []
        fence: str | None = None
        for line in text.split("\n"):
            marker = FENCE_RE.match(line)
            if fence is None:
                if marker is not None:
                    fence = marker.group(1)
                    rows.append((line, True))
                    continue
                rows.append((line, False))
                continue
            rows.append((line, True))
            stripped = line.strip()
            if (
                marker is not None
                and set(stripped) == {fence[0]}
                and len(stripped) >= len(fence)
            ):
                fence = None
        if fence is not None:
            raise ConventionError("a code fence is never closed")
        return rows

    def section(self, rows: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
        """The lines of the one *Mandatory conventions* section.

        The whole document is scanned before the region is taken, so a second
        section further down is a refusal rather than an omission: conventions
        that fell out of the enumeration would never be assessed.
        """
        starts = [
            index
            for index, (line, fenced) in enumerate(rows)
            if not fenced
            and (heading := HEADING_RE.match(line)) is not None
            and len(heading.group(1)) == 2
            and heading.group(2) == CONVENTIONS_HEADING
        ]
        if len(starts) > 1:
            raise ConventionError(
                f"more than one '{CONVENTIONS_HEADING}' section"
            )
        if not starts:
            return []
        start = starts[0] + 1
        for index in range(start, len(rows)):
            line, fenced = rows[index]
            heading = None if fenced else HEADING_RE.match(line)
            if heading is not None and len(heading.group(1)) <= 2:
                return rows[start:index]
        return rows[start:]

    def bullets(self, rows: list[tuple[str, bool]]) -> list[str]:
        """Every top-level bullet of one conventions section, as its text."""
        items: list[str] = []
        current: list[str] | None = None
        others: list[str] = []
        for line, fenced in rows:
            if not fenced and line.startswith(BULLET):
                if not line[len(BULLET):].strip():
                    raise ConventionError("a convention bullet states nothing")
                if current is not None:
                    items.append(_trim(current))
                current = [line]
                continue
            if current is not None and (
                not line.strip()
                or line.startswith("  ")
                or line.startswith("\t")
            ):
                current.append(line)
                continue
            if not line.strip():
                continue
            others.append(line)
        if current is not None:
            items.append(_trim(current))
        if others:
            allowed = [line for line in others if line.strip() != "(none)"]
            if allowed:
                raise ConventionError(
                    "content outside any convention bullet: "
                    + allowed[0].strip()[:60]
                )
            if items:
                raise ConventionError(
                    "the section mixes '(none)' with convention bullets"
                )
            return []
        return items

    def numbered(self, rows: list[tuple[str, bool]]) -> list[str]:
        """Every numbered item of one rules document, as its text."""
        first: int | None = None
        for index, (line, fenced) in enumerate(rows):
            if fenced:
                continue
            heading = HEADING_RE.match(line)
            if heading is None or len(heading.group(1)) != 1:
                continue
            if heading.group(2) != RULES_HEADING:
                raise ConventionError(
                    f"the first level-one heading is not '{RULES_HEADING}'"
                )
            first = index + 1
            break
        if first is None:
            raise ConventionError(f"no level-one '{RULES_HEADING}' heading")
        items: list[str] = []
        current: list[str] | None = None
        column = 0
        started = False
        for line, fenced in rows[first:]:
            heading = None if fenced else HEADING_RE.match(line)
            if heading is not None and len(heading.group(1)) == 1:
                break
            marker = None if fenced else NUMBERED_RE.match(line)
            if marker is not None:
                if not line[len(marker.group(0)):].strip():
                    raise ConventionError("a numbered rule states nothing")
                number = int(marker.group(1))
                expected = len(items) + (1 if current is not None else 0) + 1
                if number != expected:
                    raise ConventionError(
                        "the numbered rules are not consecutive: expected "
                        f"{expected}, found {number}"
                    )
                if current is not None:
                    items.append(_trim(current))
                current = [line]
                column = len(marker.group(0))
                started = True
                continue
            if not started:
                continue
            if not fenced and line.strip() == FOOTER and _indent(line) == 0:
                break
            if current is not None and (
                fenced or not line.strip() or _indent(line) >= column
            ):
                current.append(line)
                continue
            raise ConventionError(
                "content after the numbered rules that belongs to no rule: "
                + line.strip()[:60]
            )
        if current is not None:
            items.append(_trim(current))
        if not items:
            raise ConventionError("the rules document states no numbered rule")
        return items

    def preview(
        self,
        plan: Any,
        inventory: list[dict[str, Any]],
        conventions: list[dict[str, Any]],
        changes: str,
    ) -> str:
        """The computed change with every convention it must be assessed on.

        The payload is the checklist envelope the caller re-calls with: the two
        digests its assessments bind to, and the complete enumeration they must
        cover one entry at a time.
        """
        return (
            "\n\ncomputed memory update: assess and separate every convention "
            "below, then re-call with `checklist`:\n```json\n"
            + json.dumps(
                {
                    "schema": 1,
                    "plan_digest": plan.audit_digest(),
                    "documents_digest": inventory_digest(inventory),
                    "conventions": conventions,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n```\n\n## Every change this operation will make\n\n"
            + changes
        )

    def validate(
        self,
        raw: Any,
        *,
        plan_digest: str,
        documents_digest: str,
        conventions: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """The conflicts of one checklist, and nothing it carried.

        The submitted reasoning and fixes are read and dropped: a refusal names
        only a convention the hook itself enumerated, so a rejected checklist
        leaves no text of its own behind in the result.

        The order of the checks is the order of the news: a checklist bound to
        another plan or another document set is stale whatever it assessed, so
        those digests are compared before any entry is read, then a declared
        violation, then whether every enumerated convention got one entry, and
        last whether the separation classifies them in compliance order.
        """
        if raw is None:
            return [
                _refusal(
                    "checklist-missing",
                    "A convention checklist is required for this memory "
                    "update.",
                    CHECKLIST_FIX,
                )
            ]
        if not isinstance(raw, dict):
            return [_invalid("`checklist` must be an object")]
        unknown = sorted(set(raw) - ENVELOPE_KEYS)
        if unknown:
            return [_invalid("the checklist contains unsupported keys")]
        missing = sorted((ENVELOPE_KEYS - {"separation"}) - set(raw))
        if missing:
            return [_invalid("the checklist omits: " + ", ".join(missing))]
        if type(raw.get("schema")) is not int or raw.get("schema") != 1:
            return [_invalid("`schema` must be 1")]
        bound: dict[str, str] = {}
        for key in ("plan_digest", "documents_digest"):
            value = raw.get(key)
            if not isinstance(value, str) or not DIGEST_RE.match(value):
                return [_invalid(f"`{key}` must be a sha256 digest")]
            bound[key] = value
        checks = raw.get("checks")
        if not isinstance(checks, list):
            return [_invalid("`checks` must be a list")]
        stale: list[dict[str, str]] = []
        if bound["plan_digest"] != plan_digest:
            stale.append(
                _refusal(
                    "checklist-plan-mismatch",
                    "The checklist does not match the computed memory update.",
                    PLAN_FIX,
                )
            )
        if bound["documents_digest"] != documents_digest:
            stale.append(
                _refusal(
                    "checklist-documents-stale",
                    "The checklist's governing-document digest no longer "
                    "matches the current conventions.",
                    DOCUMENTS_FIX,
                )
            )
        if stale:
            return stale
        assessed: set[tuple[str, int]] = set()
        statuses: dict[tuple[str, int], str] = {}
        violations: list[dict[str, str]] = []
        for index, check in enumerate(checks):
            where = f"check {index + 1}"
            if not isinstance(check, dict):
                return [_invalid(f"{where} must be an object")]
            status = check.get("status")
            if status == "violation":
                if set(check) != VIOLATION_KEYS:
                    return [
                        _invalid(
                            f"{where} states exactly "
                            + ", ".join(sorted(VIOLATION_KEYS))
                        )
                    ]
            elif isinstance(status, str) and status in STATUSES:
                if set(check) != PASS_KEYS:
                    return [
                        _invalid(
                            f"{where} states exactly "
                            + ", ".join(sorted(PASS_KEYS))
                        )
                    ]
            else:
                return [
                    _invalid(
                        f"{where} status must be pass, violation or "
                        "not-applicable"
                    )
                ]
            source = check.get("source")
            ordinal = check.get("ordinal")
            reasoning = check.get("reasoning")
            if not isinstance(source, str) or not source.strip():
                return [_invalid(f"{where} names no source")]
            candidate = Path(source).expanduser()
            if not candidate.is_absolute():
                return [_invalid(f"{where} names a relative source")]
            if type(ordinal) is not int or ordinal < 1:
                return [
                    _invalid(f"{where} ordinal must be a positive integer")
                ]
            if not isinstance(reasoning, str) or not reasoning.strip():
                return [_invalid(f"{where} gives no reasoning")]
            if status == "violation":
                fix = check.get("fix")
                if not isinstance(fix, str) or not fix.strip():
                    return [
                        _invalid(
                            f"{where} reports a violation without a fix"
                        )
                    ]
                known = {
                    (item["source"], item["ordinal"]) for item in conventions
                }
                if (source_key(candidate), ordinal) not in known:
                    return [
                        _refusal(
                            "checklist-coverage",
                            "The checklist reports a violation on a "
                            "convention this update does not have.",
                            CHECKLIST_FIX,
                        )
                    ]
                violations.append(
                    _refusal(
                        "checklist-violations",
                        f"the convention {source_key(candidate)} number "
                        f"{ordinal} was reported as violated.",
                        VIOLATION_FIX,
                    )
                )
            identity = (source_key(candidate), ordinal)
            if identity in assessed:
                return [
                    _refusal(
                        "checklist-coverage",
                        "The checklist assesses one convention more than "
                        "once.",
                        CHECKLIST_FIX,
                    )
                ]
            assessed.add(identity)
            statuses[identity] = status
        conflicts: list[dict[str, str]] = list(violations)
        if conflicts:
            return conflicts
        expected = {(item["source"], item["ordinal"]) for item in conventions}
        if expected - assessed:
            missing_identities = sorted(expected - assessed)
            return [
                _refusal(
                    "checklist-coverage",
                    f"The checklist leaves {len(missing_identities)} of "
                    f"{len(expected)} conventions unassessed, starting at "
                    f"{missing_identities[0][0]} convention "
                    f"{missing_identities[0][1]}.",
                    CHECKLIST_FIX,
                )
            ]
        if assessed - expected:
            return [
                _refusal(
                    "checklist-coverage",
                    f"The checklist names {len(assessed - expected)} "
                    "conventions this update does not have.",
                    CHECKLIST_FIX,
                )
            ]
        if "separation" not in raw:
            return [_separation_missing()]
        separation = raw["separation"]
        if (
            not isinstance(separation, dict)
            or set(separation) != SEPARATION_KEYS
        ):
            return [_separation_invalid()]
        classified: dict[str, list[tuple[str, int]]] = {}
        for field in ("significant", "not_applicable"):
            listed = separation[field]
            if not isinstance(listed, list):
                return [_separation_invalid()]
            identities: list[tuple[str, int]] = []
            for reference in listed:
                if (
                    not isinstance(reference, dict)
                    or set(reference) != REFERENCE_KEYS
                ):
                    return [_separation_invalid()]
                named = reference.get("source")
                ordinal = reference.get("ordinal")
                if not isinstance(named, str) or not named.strip():
                    return [_separation_invalid()]
                if type(ordinal) is not int or ordinal < 1:
                    return [_separation_invalid()]
                try:
                    candidate = Path(named).expanduser()
                    if not candidate.is_absolute():
                        return [_separation_invalid()]
                    identity = (source_key(candidate), ordinal)
                except (OSError, RuntimeError, ValueError):
                    return [_separation_invalid()]
                identities.append(identity)
            classified[field] = identities
        significant = classified["significant"]
        remaining = classified["not_applicable"]
        every = significant + remaining
        if len(set(every)) != len(every) or set(every) != expected:
            return [_separation_coverage()]
        if any(statuses[identity] != "pass" for identity in significant):
            return [_separation_mismatch()]
        if any(
            statuses[identity] != "not-applicable" for identity in remaining
        ):
            return [_separation_mismatch()]
        return []
