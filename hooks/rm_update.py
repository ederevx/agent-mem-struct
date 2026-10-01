"""Spec-shaped structural mutations of memory nodes and their counterparts.

One owner for the write half of the root-memory hook: `MemoryUpdater`
validates a requested mutation against the live root-control snapshot,
materializes every required counterpart (active leaf, paired log, nodes
index, group scaffolding), and returns the exact conflicts the agent must
fix before re-calling. A mutation that lands under the declared shared root
is then committed and pushed by `rm_commit.CommitPublisher` under the
`commit_message` the agent wrote, which the tool never composes or edits; a
mutation in the agent's own tree is only written. It never resolves a
conflict on its own, never force-pushes, and never writes outside the
agent's own memory tree or the declared shared root.

Operations: create (node or group), set (current state, moving only the
lines it displaces into the log unless `mechanical`), log (append
history), commit (publish hand-edited leaves the agent made with its own
editor), rename (leaf + log + attachment directory + inbound links),
retire (remove active, keep log), requires (frontmatter prerequisites),
attach and detach (the leaf's significant-file directory).
"""
from __future__ import annotations

import datetime
import difflib
import os
import re
import shutil
from pathlib import Path
from typing import Any

from rm_commit import CommitPublisher
from rm_edit import WorktreeEdits
from rm_git import GitWorktree
from rm_control import RootControl, RootState
from rm_support import under

LEAF_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
REQUIRES_LINE_RE = re.compile(r"(?m)^\s*-\s*(.+?)\s*$")
EMPTY_LOG_LINE = "No semantic history yet."
LOG_BODY = "# Log: {name}\n\n" + EMPTY_LOG_LINE + "\n"
MAX_LINK_FILES = 5000


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
    ) -> None:
        self.ok = ok
        self.operation = operation
        self.changed = changed or []
        self.created = created or []
        self.conflicts = conflicts or []
        self.note = note

    def text(self) -> str:
        if self.ok:
            lines = [f"memory_update {self.operation}: ok"]
            if self.created:
                lines.append("created: " + ", ".join(self.created))
            if self.changed:
                lines.append("changed: " + ", ".join(self.changed))
            if self.note:
                lines.append(self.note)
            return "\n".join(lines)
        lines = [
            f"memory_update {self.operation}: CONFLICTS - fix these, then re-call the tool:"
        ]
        for conflict in self.conflicts:
            fix = f" (fix: {conflict['fix']})" if conflict.get("fix") else ""
            lines.append(f"- {conflict['code']}: {conflict['message']}{fix}")
        return "\n".join(lines)


class MemoryUpdater:
    """Applies one validated memory mutation against a root snapshot."""

    def __init__(self, control: RootControl, state: RootState) -> None:
        self.control = control
        self.state = state

    # -- entry ---------------------------------------------------------------

    def run(self, request: dict[str, Any]) -> UpdateResult:
        operation = str(request.get("operation") or "").strip()
        if not operation:
            return self.conflict(operation, "bad-request", "`operation` is required.")
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
        if result.ok and operation != "commit":
            return self.publish(result, request, operation)
        return result

    def publish(
        self, result: UpdateResult, request: dict[str, Any], operation: str
    ) -> UpdateResult:
        """Commit and push what the mutation wrote when it landed in shared memory."""
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
        outcome = publisher.publish(written, self.commit_message(request))
        if outcome.conflict is not None:
            conflict = outcome.conflict
            return self.conflict(
                operation,
                conflict["code"],
                f"{conflict['message']} the mutation was written first; "
                f"paths: {', '.join(str(path) for path in written)}.",
                fix=conflict["fix"],
            )
        return self.ok(
            operation,
            created=result.created,
            changed=result.changed,
            note=" ".join(part for part in (result.note, outcome.note) if part),
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
        if leaf.exists() or log.exists():
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
        if group.exists():
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
        if not log.exists():
            return self.log_conflict("set", leaf)
        note = ""
        written = [str(leaf)]
        if not request.get("mechanical"):
            displaced = self.superseded_text(previous, body)
            if displaced:
                self.append_log(log, displaced, "superseded current state")
                written.append(str(log))
            else:
                note = "no displaced state."
        self.write(leaf, body)
        return self.ok(
            "set",
            changed=written,
            note=note,
        )

    def op_log(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "log")
        if error is not None:
            return error
        text = str(request.get("text") or "")
        if not text.strip():
            return self.conflict("log", "bad-request", "`text` is required.")
        log = leaf.parent / "log" / leaf.name
        if not log.exists():
            return self.log_conflict("log", leaf)
        self.append_log(log, text.rstrip() + "\n", str(request.get("summary") or "").strip())
        return self.ok("log", changed=[str(log)])

    def op_commit(self, request: dict[str, Any]) -> UpdateResult:
        """Publish leaves the agent edited by hand, logging what they displaced.

        The working tree is the source of truth: the target set is the named
        leaf plus its paired log, or every dirty path in the memory scopes.
        Displaced lines move into a paired log exactly as `op_set` would, and
        the paths are committed and pushed through `CommitPublisher`.
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
            leaf, error = self.resolve_leaf(request, "commit")
            if error is not None:
                return error
            if not edits.is_repo(leaf):
                return self.unbacked_commit(leaf)
            log = leaf.parent / "log" / leaf.name
            targets = [leaf]
            if log.exists() and self.dirty(log, dirty):
                targets.append(log)
        else:
            targets = [Path(item) for item in dirty]
        dirty_targets = [path for path in targets if self.dirty(path, dirty)]
        if not dirty_targets:
            return self.ok(
                "commit", note="nothing-to-commit: no target has working-tree changes."
            )
        active = [path for path in dirty_targets if self.is_active_leaf(path)]
        for leaf in active:
            if not (leaf.parent / "log" / leaf.name).exists():
                return self.log_conflict("commit", leaf)
        written: list[Path] = []
        for leaf in active:
            log = leaf.parent / "log" / leaf.name
            if self.dirty(log, dirty):
                continue
            previous = edits.previous_body(leaf)
            working, read_error = self.read(leaf)
            if previous is None or read_error is not None:
                continue
            displaced = self.superseded_text(previous, working)
            if displaced:
                self.append_log(log, displaced, "superseded current state")
                written.append(log)
        return self.publish_worktree(self.unique(targets + written), message, edits)

    def publish_worktree(
        self, paths: list[Path], message: str, edits: WorktreeEdits
    ) -> UpdateResult:
        """Commit each target in its own worktree under the agent's message."""
        groups: dict[str, list[Path]] = {}
        for path in paths:
            repo = edits.repo_root(path)
            if repo is None:
                return self.unbacked_commit(path)
            groups.setdefault(str(repo.resolve(strict=False)), []).append(path)
        changed: list[str] = []
        notes: list[str] = []
        for root, group in groups.items():
            outcome = CommitPublisher(Path(root)).publish(group, message)
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
        return self.ok("commit", changed=changed, note=" ".join(notes))

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

    def is_active_leaf(self, path: Path) -> bool:
        return (
            path.suffix == ".md"
            and path.parent.name != "log"
            and path.name != "MEMORY.md"
        )

    def dirty(self, path: Path, dirty: set[str]) -> bool:
        return str(path.resolve(strict=False)) in dirty

    def unique(self, paths: list[Path]) -> list[Path]:
        seen: list[Path] = []
        for path in paths:
            if path not in seen:
                seen.append(path)
        return seen

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
        if new_leaf.exists():
            return self.conflict("rename", "exists", f"{new_leaf} already exists.")
        log = leaf.parent / "log" / leaf.name
        new_log = leaf.parent / "log" / f"{name}.md"
        attachment = leaf.parent / leaf.stem
        changed = [str(new_leaf), str(new_log), str(leaf), str(log)]
        changed.extend(self.rewrite_links(leaf.parent.parent, leaf.stem, name))
        if log.exists():
            log.rename(new_log)
        leaf.rename(new_leaf)
        if attachment.is_dir():
            changed.append(str(attachment))
            changed.append(str(leaf.parent / name))
            attachment.rename(leaf.parent / name)
        return self.ok("rename", changed=changed)

    def op_retire(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "retire")
        if error is not None:
            return error
        changed = self.index_remove(leaf.parent, leaf.stem)
        if leaf.exists():
            leaf.unlink()
            changed.append(str(leaf))
        attachment = leaf.parent / leaf.stem
        if attachment.is_dir():
            shutil.rmtree(attachment)
            changed.append(str(attachment))
        return self.ok(
            "retire",
            changed=changed,
            note="the paired log is kept as history.",
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
        if not name or os.sep in name or (os.altsep and os.altsep in name) or name in (".", ".."):
            return self.conflict("attach", "bad-name", f"{name!r} is not a plain file name.")
        directory = leaf.parent / leaf.stem
        if (directory / "MEMORY.md").exists():
            return self.conflict(
                "attach",
                "attachment-conflict",
                f"{directory} is a node collection; a leaf cannot own it as an attachment.",
            )
        if (directory / name).exists() and not request.get("overwrite"):
            return self.conflict(
                "attach", "exists", f"{directory / name} already exists.", fix="pass overwrite"
            )
        content = request.get("attachment_content")
        source = str(request.get("attachment_source") or "").strip()
        if content is None and not source:
            return self.conflict(
                "attach", "bad-request", "pass `attachment_content` or `attachment_source`."
            )
        target = directory / name
        if content is not None:
            self.write(target, str(content))
        else:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(source).expanduser().read_bytes())
            except OSError as exc:
                return self.conflict("attach", "unreadable-source", f"{source}: {exc}")
        return self.ok("attach", created=[str(target)])

    def op_detach(self, request: dict[str, Any]) -> UpdateResult:
        leaf, error = self.resolve_leaf(request, "detach")
        if error is not None:
            return error
        name = str(request.get("attachment_name") or "").strip()
        target = leaf.parent / leaf.stem / name
        if not target.exists():
            return self.conflict("detach", "not-found", f"{target} does not exist.")
        target.unlink()
        changed = [str(target)]
        directory = target.parent
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            changed.append(str(directory))
        return self.ok("detach", changed=changed)

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

    def resolve_leaf(self, request: dict[str, Any], operation: str) -> tuple[Path, UpdateResult | None]:
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
        if not leaf.exists():
            return leaf, self.conflict(
                operation, "not-found", f"{leaf} does not exist.", fix="create it first"
            )
        return leaf, None

    def conflict(
        self, operation: str, code: str, message: str, fix: str = ""
    ) -> UpdateResult:
        return UpdateResult(False, operation, conflicts=[{"code": code, "message": message, "fix": fix}])

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
        nodes.mkdir(parents=True, exist_ok=True)
        index = nodes / "MEMORY.md"
        if not index.exists():
            created.append(self.write(index, "# Nodes\n\n"))
        log = nodes / "log" / "MEMORY.md"
        if not log.exists():
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
        if not index.exists():
            return []
        text, _ = self.read(index)
        kept = [
            line for line in text.splitlines()
            if not re.search(r"\[\[" + re.escape(name) + r"\]\]", line)
        ]
        if len(kept) == len(text.splitlines()):
            return []
        self.write(index, "\n".join(kept).rstrip("\n") + "\n")
        return [str(index)]

    def rewrite_links(self, root: Path, old: str, new: str) -> list[str]:
        pattern = re.compile(r"\[\[" + re.escape(old) + r"\]\]")
        changed: list[str] = []
        for path in sorted(root.rglob("*.md"))[:MAX_LINK_FILES]:
            if "log" in path.relative_to(root).parts or path.name in (f"{old}.md", f"{new}.md"):
                continue
            text, _ = self.read(path)
            updated = pattern.sub(f"[[{new}]]", text)
            if updated != text:
                self.write(path, updated)
                changed.append(str(path))
        return changed

    def superseded_text(self, previous: str, body: str) -> str:
        """The lines of `previous` that `body` drops, grouped by section."""
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
            elif index in displaced:
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
        stamp = datetime.date.today().isoformat()
        section = f"## {stamp}" + (f" - {heading}" if heading else "")
        self.write(log, f"{body}\n\n{section}\n\n{text.rstrip()}\n".lstrip("\n"))

    def prerequisite_ok(self, leaf: Path, raw: str) -> bool:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = leaf.parent / candidate
        resolved = candidate.resolve(strict=False)
        if "log" in resolved.parts or resolved.suffix != ".md" or not resolved.is_file():
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
        try:
            return path.read_text(encoding="utf-8"), None
        except OSError as exc:
            return "", str(exc)

    def write(self, path: Path, text: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return str(path)
