"""Spec-shaped structural mutations of memory nodes and their counterparts.

One owner for the write half of the root-memory hook: `MemoryUpdater`
validates a requested mutation against the live root-control snapshot,
materializes every required counterpart (active leaf, paired log, nodes
index, group scaffolding), and returns the exact conflicts the agent must
fix before re-calling.

Nothing is written until the mutation has been assessed. `run` first records
the complete change set through `rm_update_plan.PlanRecorder` - no file system
write, every path the operation read captured as a dependency - then requires
the caller's own checklist of that exact plan, one assessment per enumerated
convention of one captured inventory of governing sources. Only an admitted
checklist reaches `apply`, which
re-records the plan first so a mutation whose inputs moved after the audit
capture is refused, rechecks the captured documents, then materializes exactly
the recorded bytes. A mutation that lands under the
declared shared root is then committed and pushed by `rm_commit.CommitPublisher`
under the `commit_message` the agent wrote, which the tool never composes or
edits but checks against the mechanical convention before committing; a
mutation in the agent's own tree is only written. A publish then attests the
shared worktree it committed through `rm_verify.PublishAttestation` and puts
its report lines in the same result, so the agent need not re-verify the
publish by hand. It never resolves a conflict on its own, never force-pushes,
and never writes outside the agent's own memory tree or the declared shared
root.

Operations: create (node or group), set (current state, moving only the
lines it displaces into the log unless `mechanical`), log (append
history), commit (publish hand-edited leaves the agent made with its own
editor), rename (leaf + log + attachment directory + inbound links),
retire (move the final state and the retirement account into the log,
remove active), requires (frontmatter prerequisites),
attach and detach (the leaf's significant-file directory).
"""
from __future__ import annotations

import datetime
import difflib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from rm_commit import CommitPublisher
from rm_conventions import (
    ConventionChecklist,
    ConventionError,
    inventory_digest,
)
from rm_draft import DraftRenderer
from rm_edit import WorktreeEdits
from rm_git import GitWorktree
from rm_control import RootControl, RootState
from rm_sources import GoverningSources, SourceError
from rm_support import under
from rm_update_plan import (
    ABSENT,
    DELETE,
    FILE,
    MKDIR,
    MOVE,
    RMDIR,
    UNREADABLE,
    WRITE,
    PlanConflict,
    PlanRecorder,
    UpdatePlan,
    digest,
)
from rm_verify import PublishAttestation

LEAF_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
REQUIRES_LINE_RE = re.compile(r"(?m)^\s*-\s*(.+?)\s*$")
EMPTY_LOG_LINE = "No semantic history yet."
LOG_BODY = "# Log: {name}\n\n" + EMPTY_LOG_LINE + "\n"
MAX_LINK_FILES = 5000
# The fields this tool documents. Anything else is a refusal, so a future field
# cannot change what runs without the audit ever seeing it.
REQUEST_FIELDS = frozenset({
    "operation", "path", "name", "body", "text", "requires", "summary",
    "kind", "mechanical", "commit_message", "attachment_name",
    "attachment_content", "attachment_source", "overwrite",
    "checklist",
})
# The request fields the convention checklist covers. The checklist is verified
# and consumed by the hook, so it never enters the plan's intent or its digest;
# that is the same exclusion `budget_ms` has as transport metadata.
INTENT_FIELDS = REQUEST_FIELDS - {"checklist"}
# Identity the host adds to every event; never part of an audited draft.
TRANSPORT_FIELDS = frozenset({
    "hook_event_name", "hookEventName", "session_id", "turn_id", "agent_id",
    "agentId", "cwd", "transcript_path", "stop_hook_active", "stopHookActive",
    "budget_ms",
})


def universal_newlines(text: str) -> str:
    """`text` with CRLF and CR line endings read as LF, as `read_text` does.

    A hand edit written on Windows carries CRLF; every comparison in this
    module is line-shaped, so the endings are normalised once at the read.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


class UpdateResult:
    """The outcome of one mutation: success, or the conflicts to fix."""

    def __init__(
        self,
        ok: bool,
        operation: str,
        *,
        changed: list[str] | None = None,
        created: list[str] | None = None,
        conflicts: list[dict[str, str]] | None = None,
        note: str = "",
        verified: list[str] | None = None,
        appended: list[tuple[str, str]] | None = None,
        preview: str = "",
    ) -> None:
        self.ok = ok
        self.operation = operation
        self.changed = changed or []
        self.created = created or []
        self.conflicts = conflicts or []
        self.note = note
        self.verified = verified or []
        self.appended = appended or []
        self.preview = preview

    def text(self) -> str:
        if self.ok:
            lines = [f"memory_update {self.operation}: ok"]
            if self.created:
                lines.append("created: " + ", ".join(self.created))
            if self.changed:
                lines.append("changed: " + ", ".join(self.changed))
            if self.note:
                lines.append(self.note)
            lines.extend(self.verified)
            return "\n".join(lines)
        lines = [
            f"memory_update {self.operation}: CONFLICTS - fix these, then re-call the tool:"
        ]
        for conflict in self.conflicts:
            fix = f" (fix: {conflict['fix']})" if conflict.get("fix") else ""
            lines.append(f"- {conflict['code']}: {conflict['message']}{fix}")
        if self.preview:
            lines.append(self.preview)
        return "\n".join(lines)


class MemoryUpdater:
    """Applies one validated memory mutation against a root snapshot."""

    def __init__(self, control: RootControl, state: RootState) -> None:
        self.control = control
        self.state = state
        self.sources = GoverningSources(
            state.memory_root, state.home, state.shared_resolved
        )
        self.draft = DraftRenderer()
        self.conventions = ConventionChecklist()
        self.tree = PlanRecorder()
        self.deadline: float | None = None
        self.date: str = datetime.date.today().isoformat()

    # -- entry ---------------------------------------------------------------

    def run(
        self, request: dict[str, Any], *, budget_s: float | None = None
    ) -> UpdateResult:
        """Record the mutation, take its checklist, then write what it assessed.

        `budget_s` is the hook's remaining time; the publication draws on it, so
        it never outlives the caller's own deadline. The log date is fixed here,
        at the start of the one update, so a draft recorded again is identical
        even across midnight.

        The checklist is consumed by the one evaluation that reads it: it is
        held here, never written, recorded or repeated back, and released before
        any write, so nothing of it is bound to the applied change.
        """
        self.date = datetime.date.today().isoformat()
        self.deadline = (
            None if budget_s is None else time.monotonic() + float(budget_s)
        )
        mutation = dict(request)
        submitted = mutation.pop("checklist", None)
        del request
        try:
            assessed = self.assess(mutation, submitted)
        finally:
            del submitted
        if isinstance(assessed, UpdateResult):
            return assessed
        prepared, inventory = assessed
        return self.commit(prepared, inventory, mutation)

    def assess(
        self, request: dict[str, Any], submitted: Any
    ) -> tuple[UpdatePlan, list[dict[str, Any]]] | UpdateResult:
        """Plan the change and take its checklist, writing nothing.

        Nothing is admitted until the checklist assesses every enumerated
        convention of this exact plan and inventory, one entry each, and reports
        no violation. A mutation that changes nothing needs no checklist, since
        there is no change to assess; its own result is returned instead.
        """
        operation = self.operation(request)
        prepared = self.prepare(request)
        if isinstance(prepared, UpdateResult):
            return prepared
        if not prepared.changes():
            return prepared.result

        try:
            inventory = self.sources.inventory(prepared)
        except SourceError as refusal:
            return self.conflict(
                operation,
                "conventions-missing-source",
                str(refusal) + ".",
                fix="restore the governing document and submit the checklist again",
            )
        try:
            conventions = self.conventions.enumerate(inventory)
        except ConventionError as refusal:
            return self.conflict(
                operation,
                "conventions-invalid",
                f"the governing conventions cannot be enumerated: {refusal}.",
                fix="repair the convention markup, then submit the checklist "
                    "again",
            )

        preview = self.conventions.preview(
            prepared, inventory, conventions, self.draft.effects(prepared)
        )
        conflicts = self.conventions.validate(
            submitted,
            plan_digest=prepared.audit_digest(),
            documents_digest=inventory_digest(inventory),
            conventions=conventions,
        )
        if conflicts:
            return UpdateResult(
                False, operation, conflicts=conflicts, preview=preview
            )
        return prepared, inventory

    def commit(
        self,
        prepared: UpdatePlan,
        inventory: list[dict[str, Any]],
        request: dict[str, Any],
    ) -> UpdateResult:
        """Apply the assessed plan, after proving its inputs did not move.

        The plan is recorded a second time and must say exactly what the first
        one did, so a mutation whose inputs moved after the checklist was
        written is refused rather than applied on a stale assessment.
        """
        operation = prepared.operation
        repeated = self.prepare(dict(request))
        if isinstance(repeated, UpdateResult):
            return self.conflict(
                operation,
                "stale-draft",
                "the assessed update could no longer be prepared; no planned "
                "changes applied. " + repeated.text(),
                fix="review the recomputed change and submit a new checklist",
            )
        matched, detail = prepared.matches(repeated)
        if not matched:
            return self.conflict(
                operation,
                "stale-draft",
                detail + "; no planned changes applied.",
                fix="review the recomputed change and submit a new checklist",
            )
        return self.apply(prepared, inventory=inventory)

    def prepare(self, request: dict[str, Any]) -> UpdatePlan | UpdateResult:
        """Record what this mutation would change, writing none of it."""
        self.tree = PlanRecorder()
        try:
            result = self.dispatch(request)
        except PlanConflict as refusal:
            return self.conflict(
                self.operation(request),
                "would-overwrite",
                f"{refusal}.",
                fix="choose a name that is free, or remove what is there first",
            )
        if not result.ok:
            return result
        return UpdatePlan(
            self.operation(request),
            self.intent(request),
            self.tree,
            result,
            self.commit_message(request),
        )

    def apply(
        self,
        plan: UpdatePlan,
        *,
        inventory: list[dict[str, Any]],
    ) -> UpdateResult:
        """Materialize the recorded changes, then publish what was assessed."""
        stale = self.stale_effect(plan)
        if stale:
            return self.conflict(
                plan.operation,
                "stale-draft",
                f"{stale} changed after the assessed plan was captured; "
                "no planned changes applied.",
                fix="review the recomputed change and submit a new checklist",
            )
        if not self.sources.unchanged(inventory):
            return self.conflict(
                plan.operation,
                "stale-documents",
                "a governing document changed after the checklist inventory was "
                "captured; no planned changes applied.",
                fix="review the governing conventions and submit a new checklist",
            )

        self.materialize(plan)
        return (
            self.publish_worktree(plan)
            if plan.operation == "commit"
            else self.publish(plan)
        )

    @staticmethod
    def stale_effect(plan: UpdatePlan) -> str:
        """The first path whose bytes on disk are no longer the audited ones.

        The comparison is with the state the plan observed, not with the
        original the audit was shown: a publication audits the last committed
        bytes while the working file already holds the hand edit it publishes.
        Only a path's first effect is compared, because a later effect on the
        same path starts from the bytes this plan itself would have written.
        """
        seen: set[str] = set()
        for effect in plan.effects:
            if effect.kind == MKDIR or effect.kind == RMDIR:
                continue
            key = os.path.normcase(str(effect.path))
            if key in seen:
                continue
            seen.add(key)
            try:
                current: bytes | None = effect.path.read_bytes()
            except OSError:
                current = None
            if current != effect.observed:
                return str(effect.path)
        return ""

    def materialize(self, plan: UpdatePlan) -> None:
        """Write exactly the recorded bytes, in the order they were recorded."""
        for effect in plan.effects:
            if effect.kind == WRITE:
                if effect.before == effect.after:
                    continue
                self.write_file(effect.path, effect.after or b"")
            elif effect.kind == DELETE:
                effect.path.unlink(missing_ok=True)
            elif effect.kind == MKDIR:
                effect.path.mkdir(parents=True, exist_ok=True)
            elif effect.kind == RMDIR:
                try:
                    effect.path.rmdir()
                except OSError:
                    pass
            elif effect.kind == MOVE and effect.destination is not None:
                # The approved bytes are written, never the source's live ones,
                # so a source that changed after the comparison cannot travel.
                effect.destination.parent.mkdir(parents=True, exist_ok=True)
                self.write_file(effect.destination, effect.after or b"")
                effect.path.unlink(missing_ok=True)

    def remaining(self) -> float | None:
        """The hook budget left for the next step, or None when unbounded."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def dispatch(self, request: dict[str, Any]) -> UpdateResult:
        """Validate the request and run its handler against the plan recorder."""
        operation = self.operation(request)
        if not operation:
            return self.conflict(operation, "bad-request", "`operation` is required.")
        unknown = sorted(
            set(request) - set(REQUEST_FIELDS) - set(TRANSPORT_FIELDS)
        )
        if unknown:
            return self.conflict(
                operation,
                "bad-request",
                "unknown request fields: " + ", ".join(unknown) + ".",
                fix="send only the fields this tool documents",
            )
        if self.state.errors:
            return self.conflict(
                operation,
                "control-invalid",
                "root memory control is invalid: " + " | ".join(self.state.errors),
            )
        if self.state.stale:
            return self.conflict(
                operation,
                "stale-protocol",
                f"memory protocol is stale ({self.state.applied} -> "
                f"{self.state.canonical}); apply {self.state.migration.name} first.",
            )
        handler = getattr(self, f"op_{operation}", None)
        if handler is None:
            return self.conflict(
                operation,
                "bad-request",
                f"unknown operation {operation!r}; expected create, set, log, "
                "commit, rename, retire, requires, attach, or detach.",
            )
        if self.state.shared_git_backed:
            unmerged = self.unmerged_shared_path()
            if unmerged is not None:
                return self.conflict(
                    operation,
                    "git-conflict",
                    f"the declared shared worktree has an unresolved merge at {unmerged}.",
                    fix="resolve the conflict and commit before re-calling",
                )
        result = handler(request)
        if not result.ok and not result.operation:
            result.operation = operation
        return result

    @staticmethod
    def operation(request: dict[str, Any]) -> str:
        return str(request.get("operation") or "").strip()

    @staticmethod
    def intent(request: dict[str, Any]) -> dict[str, Any]:
        """The operation fields the audit covers, without transport identity."""
        return {
            key: request[key]
            for key in sorted(request)
            if key in INTENT_FIELDS and request[key] not in (None, "", [], {})
        }

    def publish(self, plan: UpdatePlan) -> UpdateResult:
        """Commit and push what the mutation wrote when it landed in shared memory."""
        result, operation = plan.result, plan.operation
        shared = self.state.shared_resolved
        if shared is None:
            return result
        written = [Path(item) for item in (*result.created, *result.changed)]
        if not any(under(path, shared) for path in written):
            return result
        if not self.state.shared_git_backed:
            return self.ok(
                operation,
                created=result.created,
                changed=result.changed,
                note=" ".join(
                    part for part in (
                        result.note,
                        "the declared shared worktree is not Git-backed; commit and push "
                        "these paths by hand before the turn ends.",
                    ) if part
                ),
            )
        publisher = CommitPublisher(shared)
        outcome = publisher.publish(
            written,
            plan.commit_message,
            expected=self.expected(plan, shared),
            budget_s=self.remaining(),
        )
        if outcome.conflict is not None:
            conflict = outcome.conflict
            return self.conflict(
                operation,
                conflict["code"],
                f"{conflict['message']} the mutation was written first; "
                f"paths: {', '.join(str(path) for path in written)}.",
                fix=conflict["fix"],
            )
        verified: list[str] = []
        if outcome.sha:
            group = [path for path in written if under(path, shared)]
            lines, conflict = self.attest(
                shared,
                outcome.sha,
                group,
                [(raw, text) for raw, text in result.appended if under(Path(raw), shared)],
            )
            if conflict is not None:
                return self.conflict(
                    operation, conflict["code"], conflict["message"], fix=conflict["fix"]
                )
            verified.extend(lines)
        return self.ok(
            operation,
            created=result.created,
            changed=result.changed,
            note=" ".join(part for part in (result.note, outcome.note) if part),
            verified=verified,
            appended=result.appended,
        )

    def commit_message(self, request: dict[str, Any]) -> str:
        value = request.get("commit_message")
        return value.strip() if isinstance(value, str) else ""

    # -- operations ----------------------------------------------------------

    def op_create(self, request: dict[str, Any]) -> UpdateResult:
        kind = str(request.get("kind") or "node").strip()
        target, error = self.resolve(self.required(request, "path"))
        if error is not None:
            return error
        name = str(request.get("name") or "").strip()
        if not LEAF_RE.match(name):
            return self.conflict("create", "bad-name", f"{name!r} is not a kebab-case leaf stem.")
        if kind == "group":
            return self.create_group(target, name, request)
        if kind != "node":
            return self.conflict("create", "bad-request", f"unknown kind {kind!r}.")
        return self.create_node(target, name, request)

    def create_node(self, group: Path, name: str, request: dict[str, Any]) -> UpdateResult:
        nodes = group / "nodes"
        leaf = nodes / f"{name}.md"
        log = nodes / "log" / f"{name}.md"
        if self.tree.exists(leaf) or self.tree.exists(log):
            return self.conflict(
                "create", "exists", f"{leaf} or its log already exists.", fix="use set or rename"
            )
        created = self.ensure_nodes(nodes)
        created.append(self.write(leaf, self.leaf_body(str(request.get("body") or ""))))
        created.append(self.write(log, LOG_BODY.format(name=name)))
        changed = [item for item in self.index_add(nodes, name, str(request.get("summary") or "").strip()) if item not in created]
        return self.ok("create", created=created, changed=changed)

    def create_group(self, parent: Path, name: str, request: dict[str, Any]) -> UpdateResult:
        group = parent / name
        if self.tree.exists(group):
            return self.conflict("create", "exists", f"{group} already exists.")
        body = str(request.get("body") or "").strip()
        created: list[str] = []
        created.append(self.write(group / "MEMORY.md", (body or f"# {name}\n") + "\n"))
        created.append(self.write(group / "log" / "MEMORY.md", LOG_BODY.format(name="MEMORY")))
        created.extend(self.ensure_nodes(group / "nodes"))
        changed = [item for item in self.index_add(parent, name, str(request.get("summary") or "").strip()) if item not in created]
        return self.ok("create", created=created, changed=changed)

    def op_set(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "set")
        if error is not None:
            return error
        body = self.leaf_body(str(request.get("body") or ""))
        previous, read_error = self.read(leaf)
        if read_error is not None:
            return self.conflict("set", "not-found", read_error)
        if previous == body:
            return self.ok("set", note="current state already matches.")
        log = leaf.parent / "log" / leaf.name
        if not self.tree.exists(log):
            return self.log_conflict("set", leaf)
        note = ""
        written = [str(leaf)]
        appended: list[tuple[str, str]] = []
        if not request.get("mechanical"):
            displaced = self.superseded_text(previous, body)
            if displaced:
                self.append_log(log, displaced, "superseded current state")
                written.append(str(log))
                appended.append((str(log), displaced))
            else:
                note = "no displaced state."
        self.write(leaf, body)
        return self.ok(
            "set",
            changed=written,
            note=note,
            appended=appended,
        )

    def op_log(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_history(request, "log")
        if error is not None:
            return error
        text = str(request.get("text") or "")
        if not text.strip():
            return self.conflict("log", "bad-request", "`text` is required.")
        summary = str(request.get("summary") or "").strip()
        for field, value in (("text", text), ("summary", summary)):
            conflict = self.non_ascii_conflict("log", field, value)
            if conflict is not None:
                return conflict
        log = leaf.parent / "log" / leaf.name
        if not self.tree.exists(log):
            return self.log_conflict("log", leaf)
        self.append_log(log, text.rstrip() + "\n", summary)
        return self.ok("log", changed=[str(log)])

    def non_ascii_conflict(
        self, operation: str, field: str, text: str
    ) -> UpdateResult | None:
        """A conflict for non-ASCII text, or None when it is all ASCII.

        `log` history must stay ASCII. The conflict names the first five
        offending characters by line, column, and code point, so the agent can
        replace them and re-call; nothing is written on failure.
        """
        offenders: list[tuple[int, int, str]] = []
        for number, line in enumerate(text.split("\n"), start=1):
            for column, char in enumerate(line, start=1):
                if ord(char) > 127:
                    offenders.append((number, column, char))
        if not offenders:
            return None
        limit = 5
        described = ", ".join(
            f"line {number} col {column} U+{ord(char):04X} ({char!r})"
            for number, column, char in offenders[:limit]
        )
        extra = f" (+{len(offenders) - limit} more)" if len(offenders) > limit else ""
        return self.conflict(
            operation,
            "non-ascii",
            f"`{field}` must be ASCII: {described}{extra}.",
            fix="replace each with ASCII (`-`, `->`, `...`) and re-call",
        )

    def op_commit(self, request: dict[str, Any]) -> UpdateResult:
        """Prepare the leaves the agent edited by hand, logging what they displaced.

        The working tree is the source of truth: the target set is the named
        leaf plus its paired log, or every dirty path in the memory scopes.
        The hand-edited bytes are recorded as equality writes, so the audit
        reads exactly what a commit would publish and the apply stage rewrites
        nothing; the paired log's appended lines are recorded as real writes.
        """
        message = self.commit_message(request)
        if not message:
            return self.conflict(
                "commit",
                "bad-request",
                "`commit_message` is required; supply the commit message you wrote.",
            )
        edits = WorktreeEdits(self.state.memory_root, self.state.shared_resolved)
        dirty = {str(path) for path in edits.dirty_paths()}
        raw = self.required(request, "path")
        if raw:
            leaf, error = self.resolve_history(request, "commit", allow_missing=True)
            if error is not None:
                return error
            if not edits.is_repo(leaf):
                return self.unbacked_commit(leaf)
            log = leaf.parent / "log" / leaf.name
            known = self.exists_or_tracked(leaf, edits)
            if known is None:
                return self.unreadable_target(
                    "commit", leaf,
                    "Git would not report what it holds for it, so the target "
                    "set cannot be decided.",
                )
            targets = [leaf] if known else []
            if self.tree.exists(log) and self.dirty(log, dirty):
                targets.append(log)
        else:
            targets = [Path(item) for item in dirty]
        dirty_targets = sorted(
            (path for path in targets if self.dirty(path, dirty)), key=str
        )
        if not dirty_targets:
            return self.ok(
                "commit", note="nothing-to-commit: no target has working-tree changes."
            )
        removed = self.retire_required(dirty_targets, dirty, edits)
        if removed is not None:
            return removed
        active = [path for path in dirty_targets if self.is_active_leaf(path)]
        for leaf in active:
            if not self.tree.exists(leaf.parent / "log" / leaf.name):
                return self.log_conflict("commit", leaf)
        written: list[Path] = []
        appended: list[tuple[str, str]] = []
        for leaf in active:
            log = leaf.parent / "log" / leaf.name
            if self.dirty(log, dirty):
                continue
            previous = edits.previous_body(leaf)
            working, read_error = self.read(leaf)
            if previous is None or read_error is not None:
                continue
            self.pin(leaf, edits, previous)
            displaced = self.superseded_text(previous, working)
            if displaced:
                self.append_log(log, displaced, "superseded current state")
                written.append(log)
                appended.append((str(log), displaced))
        for path in dirty_targets:
            recorded = self.record_target(path, edits)
            if isinstance(recorded, UpdateResult):
                return recorded
        published = self.unique(dirty_targets + written)
        self.tree.publish = published
        return self.ok(
            "commit",
            changed=[str(path) for path in published],
            appended=appended,
        )

    def exists_or_tracked(self, path: Path, edits: WorktreeEdits) -> bool | None:
        """Whether the leaf is there to commit, or None when Git did not say."""
        if self.tree.exists(path):
            return True
        return self.tracked(path, edits)

    def tracked(self, path: Path, edits: WorktreeEdits) -> bool | None:
        """Whether HEAD holds `path`, or None when Git did not say."""
        target = self.git_target(path, edits)
        if target is None:
            return None
        worktree, relative = target
        found, entry = worktree.tree_entry("HEAD", relative)
        return None if not found else entry is not None

    def unreadable_target(self, operation: str, path: Path, detail: str) -> UpdateResult:
        """A path Git would not describe is never read as absent or empty."""
        return self.conflict(
            operation,
            "unreadable-target",
            f"{path} {detail}",
            fix="repair the repository, then re-call",
        )

    def retire_required(
        self, targets: list[Path], dirty: set[str], edits: WorktreeEdits
    ) -> UpdateResult | None:
        """A leaf deleted by hand is refused: only `retire` keeps its final state.

        Publishing the removal of a leaf whose paired log is not part of the
        same publication would drop the last state the protocol keeps, so the
        commit points at `retire` instead of committing the deletion.
        """
        for path in targets:
            if not self.is_active_leaf(path) or self.tree.state(path) != ABSENT:
                continue
            held = self.tracked(path, edits)
            if held is None:
                return self.unreadable_target(
                    "commit", path,
                    "is missing, and Git would not report what HEAD holds for it, "
                    "so its removal cannot be audited.",
                )
            if not held:
                continue
            log = path.parent / "log" / path.name
            if self.dirty(log, dirty):
                continue
            return self.conflict(
                "commit",
                "retire-required",
                f"{path} was deleted by hand and its paired log is not part of "
                "this publication, so the leaf's final state would be lost.",
                fix="restore the leaf, then call memory_update with operation "
                "`retire` so the final state reaches the paired log before the "
                "removal is published",
            )
        return None

    def record_target(self, path: Path, edits: WorktreeEdits) -> UpdateResult | None:
        """Record one dirty target as the equality write or deletion to publish.

        A path that cannot be read is a conflict rather than a deletion: the
        difference decides whether the audit describes a new body or a removal.
        """
        if self.tree.is_planned(path):
            return None
        state = self.tree.state(path)
        if state == UNREADABLE:
            return self.conflict(
                "commit",
                "unreadable-target",
                f"{path} could not be read, so its change cannot be audited.",
                fix="repair the file, then re-call",
            )
        if state == FILE:
            data = self.tree.observe(path)
            found, committed = self.head_blob(path, edits)
            if not found:
                return self.unreadable_target(
                    "commit", path,
                    "exists, but Git would not report what it holds in HEAD, so its "
                    "change cannot be audited.",
                )
            # The audit compares a publication with what is committed, not with
            # the working file that already holds the hand edit.
            self.tree.write(path, data, before=committed)
            self.pin(path, edits)
            return None
        found, committed = self.head_blob(path, edits)
        if not found:
            return self.unreadable_target(
                "commit", path,
                "is missing, but Git would not report what it held in HEAD, so its "
                "removal cannot be audited.",
            )
        self.tree.write(path, None, before=committed)
        self.pin(path, edits)
        return None

    def git_target(self, path: Path, edits: WorktreeEdits) -> tuple[GitWorktree, str] | None:
        """The worktree holding `path` and its repo-relative name, or None."""
        repo = edits.repo_root(path)
        if repo is None:
            return None
        root = repo.resolve(strict=False)
        try:
            relative = path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            return None
        return GitWorktree(root), relative

    def head_blob(self, path: Path, edits: WorktreeEdits) -> tuple[bool, bytes | None]:
        """What `path` held in HEAD, and whether Git could say.

        An untracked path is reported as `(True, None)` - genuinely absent - so
        the audit reads an addition rather than an unchanged file, while an
        unanswered query is `(False, None)` and refuses the publication. A path
        Git cannot even place in a worktree is unanswered too: failed repository
        discovery proves nothing about what HEAD holds.
        """
        target = self.git_target(path, edits)
        if target is None:
            return False, None
        worktree, relative = target
        found, entry = worktree.tree_entry("HEAD", relative)
        if not found:
            return False, None
        if entry is None:
            return True, None
        read, data = worktree.object_bytes(entry[1])
        return (True, data) if read else (False, None)

    def pin(
        self, path: Path, edits: WorktreeEdits, previous: str | None = None
    ) -> None:
        """Record the Git facts a publication of `path` is verified against."""
        target = self.git_target(path, edits)
        if target is None:
            return
        worktree, relative = target
        root = worktree.root
        head_key = f"repo:{root}:head"
        if head_key not in self.tree.context:
            self.tree.context[head_key] = worktree.head()
        self.tree.context[f"repo:{root}:mode:{relative}"] = (
            worktree.mode("HEAD", relative) or ""
        )
        if previous is not None:
            self.tree.context[f"repo:{root}:previous:{relative}"] = digest(
                previous.encode("utf-8")
            )

    def expected(
        self, plan: UpdatePlan, root: Path
    ) -> dict[str, bytes | None]:
        """The approved snapshot of one worktree: path -> bytes, or None when deleted."""
        resolved = root.resolve(strict=False)

        def relative(path: Path) -> str | None:
            if not under(path, resolved):
                return None
            return path.resolve(strict=False).relative_to(resolved).as_posix()

        expected: dict[str, bytes | None] = {}
        for effect in plan.effects:
            if effect.kind in (WRITE, DELETE):
                name = relative(effect.path)
                if name is not None:
                    expected[name] = effect.after
            elif effect.kind == MOVE and effect.destination is not None:
                source, target = relative(effect.path), relative(effect.destination)
                if source is not None:
                    expected[source] = None
                if target is not None:
                    expected[target] = effect.after
        return expected

    def publish_worktree(self, plan: UpdatePlan) -> UpdateResult:
        """Commit each published target in its own worktree, under the agent's message.

        Every worktree that produced a commit is then attested with its own
        changed paths and the appended logs that belong to it; the first
        verification conflict stops the report.
        """
        result = plan.result
        edits = WorktreeEdits(self.state.memory_root, self.state.shared_resolved)
        groups: dict[str, list[Path]] = {}
        for path in plan.publish_paths:
            repo = edits.repo_root(path)
            if repo is None:
                return self.unbacked_commit(path)
            groups.setdefault(str(repo.resolve(strict=False)), []).append(path)
        appended_groups: dict[str, list[tuple[str, str]]] = {}
        for raw, text in result.appended:
            repo = edits.repo_root(Path(raw))
            if repo is None:
                continue
            appended_groups.setdefault(str(repo.resolve(strict=False)), []).append(
                (raw, text)
            )
        changed: list[str] = []
        notes: list[str] = []
        verified: list[str] = []
        for root, group in groups.items():
            outcome = CommitPublisher(Path(root)).publish(
                group,
                plan.commit_message,
                expected=self.expected(plan, Path(root)),
                budget_s=self.remaining(),
            )
            if outcome.conflict is not None:
                conflict = outcome.conflict
                return self.conflict(
                    "commit",
                    conflict["code"],
                    f"{conflict['message']} paths: "
                    f"{', '.join(str(path) for path in group)}.",
                    fix=conflict["fix"],
                )
            changed.extend(str(path) for path in group)
            if outcome.note:
                notes.append(outcome.note)
            if outcome.sha:
                lines, conflict = self.attest(
                    Path(root), outcome.sha, group, appended_groups.get(root, [])
                )
                if conflict is not None:
                    return self.conflict(
                        "commit", conflict["code"], conflict["message"], fix=conflict["fix"]
                    )
                verified.extend(lines)
        return self.ok(
            "commit",
            changed=changed,
            note=" ".join(notes),
            verified=verified,
            appended=result.appended,
        )

    @staticmethod
    def is_active_leaf(path: Path) -> bool:
        return (
            path.suffix == ".md"
            and path.parent.name != "log"
            and path.name != "MEMORY.md"
        )

    @staticmethod
    def unique(paths: list[Path]) -> list[Path]:
        seen: list[Path] = []
        for path in paths:
            if path not in seen:
                seen.append(path)
        return seen

    def attest(
        self,
        root: Path,
        sha: str,
        changed: list[Path],
        appended: list[tuple[str, str]],
    ) -> tuple[list[str], dict[str, str] | None]:
        """Attest one committed worktree: its lines, or the first conflict."""
        report = PublishAttestation(root).attest(
            sha=sha,
            changed=changed,
            appended=[(Path(raw), text) for raw, text in appended],
        )
        if report.conflicts:
            conflict = dict(report.conflicts[0])
            conflict["message"] = f"{conflict['message']} (sha {sha[:12]})"
            return [], conflict
        return report.lines, None

    def unbacked_commit(self, path: Path) -> UpdateResult:
        """A target with no Git worktree: the existing manual note, or a conflict."""
        shared = self.state.shared_resolved
        if shared is not None and under(path, shared) and not self.state.shared_git_backed:
            return self.ok(
                "commit",
                note="the declared shared worktree is not Git-backed; commit and push "
                "these paths by hand before the turn ends.",
            )
        return self.conflict(
            "commit",
            "not-git",
            f"{path} is not inside a Git worktree.",
            fix="commit these paths by hand before the turn ends",
        )

    def dirty(self, path: Path, dirty: set[str]) -> bool:
        return str(path.resolve(strict=False)) in dirty

    def op_rename(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "rename")
        if error is not None:
            return error
        name = str(request.get("name") or "").strip()
        if not LEAF_RE.match(name):
            return self.conflict("rename", "bad-name", f"{name!r} is not a kebab-case leaf stem.")
        if name == leaf.stem:
            return self.ok("rename", note="already named " + name)
        new_leaf = leaf.parent / f"{name}.md"
        log = leaf.parent / "log" / leaf.name
        new_log = leaf.parent / "log" / f"{name}.md"
        attachment = leaf.parent / leaf.stem
        new_attachment = leaf.parent / name
        for existing in (new_leaf, new_log):
            if self.tree.exists(existing):
                return self.conflict("rename", "exists", f"{existing} already exists.")
        if self.tree.is_dir(new_attachment):
            return self.conflict(
                "rename", "exists", f"{new_attachment} already exists."
            )
        changed = [str(new_leaf), str(new_log), str(leaf), str(log)]
        changed.extend(self.rewrite_links(leaf.parent.parent, leaf.stem, name))
        if self.tree.exists(log):
            self.tree.rename(log, new_log)
        self.tree.rename(leaf, new_leaf)
        if self.tree.is_dir(attachment):
            changed.append(str(attachment))
            changed.append(str(new_attachment))
            self.tree.rename(attachment, new_attachment)
        return self.ok("rename", changed=changed)

    def op_retire(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "retire")
        if error is not None:
            return error
        text = str(request.get("text") or "")
        if not text.strip():
            return self.conflict(
                "retire", "bad-request", "`text` is required; supply the retirement account."
            )
        conflict = self.non_ascii_conflict("retire", "text", text)
        if conflict is not None:
            return conflict
        log = leaf.parent / "log" / leaf.name
        if not self.tree.exists(log):
            return self.log_conflict("retire", leaf)
        previous, read_error = self.read(leaf)
        if read_error is not None:
            return self.conflict("retire", "not-found", read_error)
        appended: list[tuple[str, str]] = []
        final = self.superseded_text(previous, "")
        if final:
            self.append_log(log, final, "final state before retirement")
            appended.append((str(log), final))
        account = text.rstrip() + "\n"
        self.append_log(log, account, "retirement")
        appended.append((str(log), account))
        changed = self.index_remove(leaf.parent, leaf.stem)
        changed.append(str(log))
        if self.tree.exists(leaf):
            self.delete(leaf)
            changed.append(str(leaf))
        attachment = leaf.parent / leaf.stem
        if self.tree.is_dir(attachment):
            self.tree.rmtree(attachment)
            changed.append(str(attachment))
        return self.ok(
            "retire",
            changed=changed,
            note="the paired log is kept as history.",
            appended=appended,
        )

    def op_requires(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "requires")
        if error is not None:
            return error
        requires = request.get("requires") or []
        if not isinstance(requires, list):
            return self.conflict("requires", "bad-request", "`requires` must be a list.")
        targets = [str(item).strip() for item in requires if str(item).strip()]
        missing = [item for item in targets if not self.prerequisite_ok(leaf, item)]
        if missing:
            return self.conflict(
                "requires",
                "missing-prerequisite",
                "these prerequisites are not readable active memory files: "
                + ", ".join(missing),
            )
        text, read_error = self.read(leaf)
        if read_error is not None:
            return self.conflict("requires", "not-found", read_error)
        body = FRONTMATTER_RE.sub("", text, count=1)
        front = ""
        if targets:
            front = "---\nrequires_read:\n" + "".join(f"  - {item}\n" for item in targets) + "---\n"
        self.write(leaf, front + body)
        return self.ok("requires", changed=[str(leaf)])

    def op_attach(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "attach")
        if error is not None:
            return error
        name = str(request.get("attachment_name") or "").strip()
        if not self.plain_name(name):
            return self.conflict("attach", "bad-name", f"{name!r} is not a plain file name.")
        directory = leaf.parent / leaf.stem
        if self.tree.exists(directory / "MEMORY.md"):
            return self.conflict(
                "attach",
                "attachment-conflict",
                f"{directory} is a node collection; a leaf cannot own it as an attachment.",
            )
        target = directory / name
        if self.tree.exists(target) and not request.get("overwrite"):
            return self.conflict(
                "attach", "exists", f"{target} already exists.", fix="pass overwrite"
            )
        content = request.get("attachment_content")
        source = str(request.get("attachment_source") or "").strip()
        if content is None and not source:
            return self.conflict(
                "attach", "bad-request", "pass `attachment_content` or `attachment_source`."
            )
        if content is not None:
            data = str(content).encode("utf-8")
        else:
            data = self.tree.observe(Path(source).expanduser())
            if data is None:
                return self.conflict(
                    "attach", "unreadable-source", f"{source} is not a readable file."
                )
        self.write_bytes(target, data)
        return self.ok("attach", created=[str(target)])

    def op_detach(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "detach")
        if error is not None:
            return error
        name = str(request.get("attachment_name") or "").strip()
        if not self.plain_name(name):
            return self.conflict("detach", "bad-name", f"{name!r} is not a plain file name.")
        directory = leaf.parent / leaf.stem
        target = directory / name
        if target.parent.resolve(strict=False) != directory.resolve(strict=False):
            return self.conflict(
                "detach", "bad-target", f"{target} is outside this leaf's attachment directory."
            )
        if not self.tree.exists(target):
            return self.conflict("detach", "not-found", f"{target} does not exist.")
        self.delete(target)
        changed = [str(target)]
        if self.tree.is_dir(directory) and not self.tree.entries(directory):
            self.tree.rmdir(directory)
            changed.append(str(directory))
        return self.ok("detach", changed=changed)

    @staticmethod
    def plain_name(name: str) -> bool:
        """Whether this is one plain file name, never a path or a relative step."""
        if not name or name in (".", ".."):
            return False
        if os.sep in name or (os.altsep and os.altsep in name):
            return False
        return not name.startswith("~")

    # -- resolution and conflicts -------------------------------------------

    def resolve(self, raw: str) -> tuple[Path, UpdateResult | None]:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.state.home / candidate
        resolved = candidate.resolve(strict=False)
        for root in filter(None, (self.state.memory_root, self.state.shared_resolved)):
            if under(resolved, root):
                return resolved, None
        return resolved, self.conflict(
            "",
            "outside-memory",
            f"{resolved} is outside this agent's memory tree and the declared shared root.",
        )

    def resolve_leaf(
        self,
        request: dict[str, Any],
        operation: str,
        *,
        allow_missing: bool = False,
    ) -> tuple[Path, UpdateResult | None]:
        """The named leaf, refused when it is not a leaf this operation edits.

        `allow_missing` is for `commit`, which is the one operation that may
        address a leaf already deleted from the working tree: only it can tell
        a tracked deletion from a path that never existed.
        """
        leaf, error = self.resolve(self.required(request, "path"))
        if error is not None:
            error.operation = operation
            return leaf, error
        if leaf.suffix != ".md":
            return leaf, self.conflict(operation, "bad-target", f"{leaf} is not a .md leaf.")
        if leaf.parent.name == "log":
            return leaf, self.conflict(
                operation,
                "log-target",
                "logs are history; use the log operation, not a direct edit.",
            )
        if not allow_missing and not self.tree.exists(leaf):
            return leaf, self.conflict(
                operation, "not-found", f"{leaf} does not exist.", fix="create it first"
            )
        return leaf, None

    def resolve_history(
        self,
        request: dict[str, Any],
        operation: str,
        *,
        allow_missing: bool = False,
    ) -> tuple[Path, UpdateResult | None]:
        """The leaf whose paired log `log` and `commit` address.

        A log outlives its leaf when the identity is retired, so a `log/` path
        is accepted once its active leaf is gone; a live leaf's history is
        still reached through the leaf itself.
        """
        log, error = self.resolve(self.required(request, "path"))
        if error is not None or log.parent.name != "log" or log.suffix != ".md":
            return self.resolve_leaf(request, operation, allow_missing=allow_missing)
        leaf = log.parent.parent / log.name
        if self.tree.exists(leaf):
            return leaf, self.conflict(
                operation,
                "log-target",
                f"{log} is the paired log of the active leaf {leaf}.",
                fix="pass the active leaf as `path`; a log path is accepted "
                "only once its leaf is retired",
            )
        if not self.tree.exists(log):
            return leaf, self.conflict(operation, "not-found", f"{log} does not exist.")
        return leaf, None

    def conflict(
        self, operation: str, code: str, message: str, fix: str = "", *, preview: str = ""
    ) -> UpdateResult:
        return UpdateResult(
            False,
            operation,
            conflicts=[{"code": code, "message": message, "fix": fix}],
            preview=preview,
        )

    def log_conflict(self, operation: str, leaf: Path) -> UpdateResult:
        return self.conflict(
            operation,
            "missing-counterpart",
            f"{leaf} has no paired log at {leaf.parent / 'log' / leaf.name}.",
            fix="create the leaf through memory_update so the pair stays together",
        )

    def ok(self, operation: str, **kwargs: Any) -> UpdateResult:
        return UpdateResult(True, operation, **kwargs)

    def required(self, request: dict[str, Any], key: str) -> str:
        value = request.get(key)
        return str(value).strip() if isinstance(value, str) else ""

    def unmerged_shared_path(self) -> str | None:
        """The first unmerged path in the shared worktree, or None.

        An unresolved merge makes every shared write unsafe; the agent must
        reconcile it first, so this precondition is checked before any file
        under the shared root is touched.
        """
        shared = self.state.shared_resolved
        if shared is None:
            return None
        completed = GitWorktree(shared).run(
            "diff", "--name-only", "--diff-filter=U", budget=5.0
        )
        for line in completed.stdout.splitlines():
            if line.strip():
                return line.strip()
        return None

    # -- counterparts --------------------------------------------------------

    def ensure_nodes(self, nodes: Path) -> list[str]:
        created: list[str] = []
        self.tree.mkdir(nodes)
        index = nodes / "MEMORY.md"
        if not self.tree.exists(index):
            created.append(self.write(index, "# Nodes\n\n"))
        log = nodes / "log" / "MEMORY.md"
        if not self.tree.exists(log):
            created.append(self.write(log, LOG_BODY.format(name="MEMORY")))
        return created

    def index_add(self, nodes: Path, name: str, summary: str) -> list[str]:
        index = nodes / "MEMORY.md"
        self.ensure_nodes(nodes)
        text, _ = self.read(index)
        if re.search(r"\[\[" + re.escape(name) + r"\]\]", text):
            return []
        line = f"- [[{name}]]"
        if summary:
            line += f" \u2014 {summary}"
        self.write(index, text.rstrip("\n") + "\n" + line + "\n")
        return [str(index)]

    def index_remove(self, nodes: Path, name: str) -> list[str]:
        index = nodes / "MEMORY.md"
        if not self.tree.exists(index):
            return []
        text, _ = self.read(index)
        lines = text.splitlines()
        kept: list[str] = []
        bullet: int | None = None
        for line in lines:
            indent = len(line) - len(line.lstrip())
            if bullet is not None and line.strip() and indent > bullet:
                continue
            bullet = None
            if re.search(r"\[\[" + re.escape(name) + r"\]\]", line):
                if re.match(r"\s*[-*+] ", line):
                    bullet = indent
                continue
            kept.append(line)
        if len(kept) == len(lines):
            return []
        self.write(index, "\n".join(kept).rstrip("\n") + "\n")
        return [str(index)]

    def rewrite_links(self, root: Path, old: str, new: str) -> list[str]:
        pattern = re.compile(r"\[\[" + re.escape(old) + r"\]\]")
        changed: list[str] = []
        for path in self.tree.files(root, ".md")[:MAX_LINK_FILES]:
            if "log" in path.relative_to(root).parts or path.name in (f"{old}.md", f"{new}.md"):
                continue
            text, _ = self.read(path)
            updated = pattern.sub(f"[[{new}]]", text)
            if updated != text:
                self.write(path, updated)
                changed.append(str(path))
        return changed

    def superseded_text(self, previous: str, body: str) -> str:
        """The lines of `previous` that `body` drops, grouped by section.

        Only a line that is gone counts as displaced: the alignment leaves a
        line unmatched when the same text reappears elsewhere in `body`.
        """
        old, new = previous.split("\n"), body.split("\n")
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        displaced = set(range(len(old)))
        for block in matcher.get_matching_blocks():
            displaced.difference_update(range(block.a, block.a + block.size))
        sections: dict[str, list[str]] = {}
        heading = ""
        for index, line in enumerate(old):
            if line.startswith("## "):
                heading = line[3:].strip()
            elif index in displaced and line.strip() and line not in new:
                sections.setdefault(heading, []).append(line)
        parts: list[str] = []
        for heading, lines in sections.items():
            quoted = "\n".join(lines).strip("\n").rstrip()
            if heading and quoted:
                parts.append(f"### {heading}\n\n{quoted}")
            elif heading:
                parts.append(f"### {heading}")
            elif quoted:
                parts.append(quoted)
        return "\n\n".join(parts) + "\n" if parts else ""

    def append_log(self, log: Path, text: str, heading: str) -> None:
        current, _ = self.read(log)
        body = current.replace(EMPTY_LOG_LINE + "\n", "").rstrip("\n")
        # The operation's own date, fixed when the update started: a second
        # prepare must record the same bytes even across midnight.
        stamp = self.date
        section = f"## {stamp}" + (f" - {heading}" if heading else "")
        self.write(log, f"{body}\n\n{section}\n\n{text.rstrip()}\n".lstrip("\n"))

    def prerequisite_ok(self, leaf: Path, raw: str) -> bool:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = leaf.parent / candidate
        resolved = candidate.resolve(strict=False)
        if "log" in resolved.parts or resolved.suffix != ".md" or not self.tree.exists(resolved):
            return False
        return any(
            root is not None and under(resolved, root)
            for root in (self.state.memory_root, self.state.shared_resolved)
        )

    # -- primitives ----------------------------------------------------------

    def leaf_body(self, body: str) -> str:
        text = body.strip("\n")
        return (text + "\n") if text else ""

    def read(self, path: Path) -> tuple[str, str | None]:
        data = self.tree.observe(path)
        if data is None:
            return "", f"{path} is not a readable file"
        try:
            return universal_newlines(data.decode("utf-8")), None
        except UnicodeDecodeError as exc:
            return "", f"{path} is not UTF-8 text: {exc}"

    def write(self, path: Path, text: str) -> str:
        return self.write_bytes(path, text.encode("utf-8"))

    def write_bytes(self, path: Path, data: bytes) -> str:
        """Plan one file write; nothing reaches the disk until the plan is applied."""
        self.tree.write(path, data)
        return str(path)

    def delete(self, path: Path) -> str:
        self.tree.write(path, None)
        return str(path)

    def write_file(self, path: Path, data: bytes) -> None:
        """Replace one file with exactly these bytes, as the applied plan requires."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
