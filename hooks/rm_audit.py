"""The conventions audit and the readings a memory update must carry.

An update is not only judged: before it may be applied its caller must have
read the governing sources again and audited the change it asks for. Neither
part is taken on trust.

The readings are compared with the reads the hook itself recorded, and those
recorded reads are consumed at the start of every attempt, so a refused attempt
can never be replayed: the retry needs a genuinely new read of every required
source. A submitted reading is a declaration of the bytes the agent saw, never
evidence that it saw them.

The audit is bound to the two digests the hook computes - the plan it recorded
and the inventory of governing sources and their bytes - and every rule it
cites must be an exact excerpt of the source it names. An audit reporting a
violation blocks the update and returns the fix it states; the corrected
request needs a new audit and new readings.

What no digest can prove is that the agent did the thinking. The independent
compliance judge, run after a structurally valid and violation-free audit, is
what stands between a copied declaration and a written change.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")
AUDIT_KEYS = frozenset(
    {"schema", "plan_digest", "documents_digest", "checks", "evidence"}
)
CHECK_KEYS = frozenset({"source", "rule", "status", "reasoning", "fix"})
STATUSES = frozenset({"pass", "violation", "not-applicable"})
RECORD_DIR = "memory-audits"

READ_FIX = (
    "Read every required present source entirely, repeat the audit, and re-call "
    "with current readings."
)
AUDIT_FIX = (
    "Correct the request to match the audit schema, reread the required sources, "
    "and re-call."
)
PREVIEW_FIX = (
    "Audit the computed plan shown below, reread the required sources, and "
    "re-call with its digests."
)
DOCUMENTS_FIX = (
    "Reread the current required sources, repeat the audit, and re-call with the "
    "current digests."
)


def canonical(value: Any) -> bytes:
    """One value as the exact bytes a digest is taken over."""
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    )


def source_key(path: Path | str) -> str:
    """The identity one governing source is known by in every binding."""
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def inventory_digest(rows: list[dict[str, Any]]) -> str:
    """The digest of the governing inventory: its paths and their bytes."""
    return hashlib.sha256(
        canonical([{"path": row["path"], "digest": row["digest"]} for row in rows])
    ).hexdigest()


def preview(plan: Any, inventory: list[dict[str, Any]], changes: str) -> str:
    """The computed change and its bindings, on every refusal after planning.

    It is what the agent audits: the exact plan digest, the document digest it
    must bind to, every required reading with the digest its bytes hold now, and
    the change itself in the words the judge reads.
    """
    return (
        "\n\ncomputed memory update: audit this exact change, then re-call with "
        "`readings` and `audit`:\n```json\n"
        + json.dumps(
            {
                "plan_digest": plan.audit_digest(),
                "documents_digest": inventory_digest(inventory),
                "required_readings": [
                    {"path": row["path"], "digest": row["digest"]}
                    for row in inventory
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n```\n\n## Every change this operation will make\n\n"
        + changes
    )


def _refusal(code: str, message: str, fix: str) -> dict[str, str]:
    return {"code": code, "message": message, "fix": fix}


def _invalid(detail: str) -> dict[str, str]:
    return _refusal("audit-invalid", f"Invalid conventions audit: {detail}.", AUDIT_FIX)


def _normalise(text: str) -> str:
    """Text with the line endings and normal form one comparison uses."""
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n"))


def _excerpt(rule: str) -> str:
    """One cited rule, trimmed the way it is compared and recorded."""
    return _normalise(rule).strip()


def _contains(text: str, rule: str) -> bool:
    """Whether the source states this exact excerpt."""
    return bool(rule) and rule in _normalise(text)


def row_digest(row: dict[str, Any]) -> str | None:
    """The digest an inventory row binds to, or None for a watched absence."""
    value = row.get("digest")
    return value if isinstance(value, str) else None


def _digest_or_none(value: Any) -> str | None | bool:
    """`value` as a digest, `None`, or `False` when it is neither."""
    if value is None:
        return None
    if isinstance(value, str) and DIGEST_RE.match(value):
        return value
    return False


def readings(
    raw: Any, inventory: list[dict[str, Any]], observed: dict[str, str], cwd: Path
) -> tuple[dict[str, str | None], list[dict[str, str]]]:
    """The declared readings, checked against the inventory and real reads."""
    declared: dict[str, str | None] = {}
    conflicts: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return {}, [_invalid("`readings` must be a list")]
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"path", "digest"}:
            conflicts.append(_invalid("a reading states exactly `path` and `digest`"))
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            conflicts.append(_invalid("a reading's `path` must be a nonempty string"))
            continue
        value = _digest_or_none(entry.get("digest"))
        if value is False:
            conflicts.append(_invalid(f"the reading for {path} is not a sha256 digest or null"))
            continue
        candidate = Path(path).expanduser()
        resolved = candidate if candidate.is_absolute() else cwd / candidate
        key = source_key(resolved)
        if key in declared:
            conflicts.append(_invalid(f"{path} is read more than once"))
            continue
        declared[key] = value
    if conflicts:
        return declared, conflicts
    required = {source_key(row["path"]): row for row in inventory}
    missing = sorted(
        row["path"] for key, row in required.items() if key not in declared
    )
    extra = sorted(key for key in declared if key not in required)
    if missing:
        conflicts.append(
            _invalid("the readings omit required sources: " + ", ".join(missing[:5]))
        )
    if extra:
        conflicts.append(
            _invalid("the readings name sources this update does not require: "
                     + ", ".join(extra[:5]))
        )
    if conflicts:
        return declared, conflicts
    stale: list[str] = []
    for key, row in required.items():
        claimed = declared.get(key)
        if row["digest"] is None:
            if claimed is not None:
                stale.append(row["path"])
            continue
        if claimed != row["digest"] or observed.get(key) != row["digest"]:
            stale.append(row["path"])
    if stale:
        conflicts.append(
            _refusal(
                "audit-readings-stale",
                "Required sources were not read again before this memory update, "
                "or the submitted readings do not match their current bytes: "
                + ", ".join(stale[:5]) + ".",
                READ_FIX,
            )
        )
    return declared, conflicts


def audit(
    raw: Any,
    plan: Any,
    inventory: list[dict[str, Any]],
    texts: dict[str, str],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """The agent's audit, checked in structure and in its bindings.

    A violation is not an error in the audit but a finding of it: each one is
    returned with the fix it states, so the agent corrects the change and
    re-requests instead of retrying the same one.
    """
    if raw is None:
        return None, [
            _refusal(
                "audit-missing",
                "A conventions audit is required for this memory update.",
                "Read the required sources again, audit the computed change, and "
                "re-call with readings and audit.",
            )
        ]
    if not isinstance(raw, dict):
        return None, [_invalid("`audit` must be an object")]
    unknown = sorted(set(raw) - AUDIT_KEYS)
    if unknown:
        return None, [_invalid("unknown audit keys: " + ", ".join(unknown))]
    if raw.get("schema") != 1:
        return None, [_invalid("`schema` must be 1")]
    bound: dict[str, str] = {}
    for key in ("plan_digest", "documents_digest"):
        value = raw.get(key)
        if not isinstance(value, str) or not DIGEST_RE.match(value):
            return None, [_invalid(f"`{key}` must be a sha256 digest")]
        bound[key] = value
    evidence = raw.get("evidence", [])
    if not isinstance(evidence, list) or any(
        not isinstance(item, str) or not item.strip() for item in evidence
    ):
        return None, [_invalid("`evidence` must be a list of nonempty strings")]
    checks = raw.get("checks")
    if not isinstance(checks, list) or not checks:
        return None, [_invalid("`checks` must be a nonempty list")]
    required = {source_key(row["path"]): row for row in inventory}
    present = {key for key, row in required.items() if row["digest"] is not None}
    covered: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    violations: list[dict[str, str]] = []
    for index, check in enumerate(checks):
        where = f"check {index + 1}"
        if not isinstance(check, dict) or set(check) != CHECK_KEYS:
            return None, [_invalid(f"{where} states exactly " + ", ".join(sorted(CHECK_KEYS)))]
        source = check.get("source")
        rule = check.get("rule")
        status = check.get("status")
        reasoning = check.get("reasoning")
        fix = check.get("fix")
        if not isinstance(source, str) or not source.strip():
            return None, [_invalid(f"{where} names no source")]
        if not isinstance(rule, str) or not rule.strip():
            return None, [_invalid(f"{where} cites no rule")]
        if status not in STATUSES:
            return None, [_invalid(f"{where} status must be pass, violation or not-applicable")]
        if not isinstance(reasoning, str) or not reasoning.strip():
            return None, [_invalid(f"{where} gives no reasoning")]
        if status == "violation":
            if not isinstance(fix, str) or not fix.strip():
                return None, [_invalid(f"{where} reports a violation without a fix")]
        elif fix is not None:
            return None, [_invalid(f"{where} states a fix without a violation")]
        key = source_key(source)
        if key not in required:
            return None, [_invalid(f"{where} names a source this update does not govern")]
        text = texts.get(key)
        if text is None:
            return None, [_invalid(f"{where} audits a source with no readable text")]
        if not _contains(text, _excerpt(rule)):
            return None, [
                _invalid(f"{where} cites a rule that is not in {required[key]['path']}")
            ]
        pair = (key, _excerpt(rule))
        if pair in pairs:
            return None, [_invalid(f"{where} repeats an earlier source and rule")]
        pairs.add(pair)
        if row_digest(required[key]) is not None:
            covered.add(key)
        if status == "violation":
            violations.append(
                _refusal(
                    "audit-violations",
                    f"{required[key]['path']}: {_excerpt(rule)}: {reasoning.strip()}",
                    fix.strip(),
                )
            )
    conflicts: list[dict[str, str]] = []
    uncovered = sorted(required[key]["path"] for key in present - covered)
    if uncovered:
        conflicts.append(
            _invalid("the audit leaves governing sources unchecked: " + ", ".join(uncovered[:5]))
        )
    if bound["plan_digest"] != plan.audit_digest():
        conflicts.append(
            _refusal(
                "audit-plan-mismatch",
                "The audit does not match the computed memory update.",
                PREVIEW_FIX,
            )
        )
    if bound["documents_digest"] != inventory_digest(inventory):
        conflicts.append(
            _refusal(
                "audit-documents-stale",
                "The audit's governing-document digest no longer matches the "
                "current inventory and bytes.",
                DOCUMENTS_FIX,
            )
        )
    conflicts.extend(violations)
    if conflicts:
        return None, conflicts
    return raw, []


class AuditStore:
    """Durable records of the readings and audit one attempt was admitted on."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    def directory(self) -> Path:
        return self.home / ".agent-mem-struct" / RECORD_DIR

    def record(
        self,
        *,
        plan: Any,
        inventory: list[dict[str, Any]],
        declared: dict[str, str | None],
        evidence: dict[str, Any],
    ) -> Path:
        """One attempt's evidence, written before the judge is asked.

        Identical plans do not overwrite each other: the name carries the time,
        so two attempts on the same change leave two records.
        """
        directory = self.directory()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{stamp}-{plan.audit_digest()[:12]}-{time.time_ns() % 1000000:06d}.json"
        path = directory / name
        payload = {
            "plan_digest": plan.audit_digest(),
            "documents_digest": inventory_digest(inventory),
            "documents": [
                {"path": row["path"], "digest": row["digest"]} for row in inventory
            ],
            "readings": [
                {"path": row["path"], "digest": declared.get(source_key(row["path"]))}
                for row in inventory
            ],
            "audit": evidence,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(text, encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


def bytes_field(value: bytes | None) -> dict[str, Any]:
    """A byte value as the JSON a digest is taken over, absence kept distinct."""
    if value is None:
        return {"absent": True}
    return {
        "size": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "base64": base64.b64encode(value).decode("ascii"),
    }
