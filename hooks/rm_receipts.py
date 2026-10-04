"""Convention acknowledgment receipts.

One owner for the convention-gate state: the per-turn event identity, the
receipt records that prove a bundle was acknowledged, the scoped convention
bundle (manifests and required reads), and the gate that refuses the first
mutation or turn completion for a new bundle digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from rm_control import RootState
from rm_scan import MutationScanner
from rm_support import TEMP_MAX_AGE, read_text, safe_identity, under

RECEIPT_MAX_AGE = 7 * 24 * 60 * 60
RECEIPT_MAX_FILES = 512
PREMEMORY_DENIAL = (
    "Convention acknowledgment required: call pre_memory first "
    "(it pulls the shared worktree and lists the conventions to read)."
)
READ_DENIAL = (
    "Convention acknowledgment incomplete: the conventions are not inlined. "
    "Read these sources with the `read` tool, then retry: "
)


class ConventionGate:
    """Owns receipt records and the convention acknowledgment gate."""

    def __init__(self, state: RootState) -> None:
        self.state = state

    def identity(self, event: dict[str, Any]) -> str | None:
        session = event.get("session_id") or event.get("sessionId")
        agent = event.get("agent_id") or event.get("agentId") or "parent"
        host = self.state.agent or "unknown"
        if not isinstance(session, str) or not session.strip():
            return None
        if self.state.agent == "pi":
            # Pi acknowledges once per session: pre_memory owns the pull and the
            # receipt, and a changed source is caught by the digest comparison.
            identity_parts = (host, session, agent)
        else:
            if self.state.agent == "claude":
                turn = event.get("prompt_id") or event.get("promptId")
            else:
                turn = event.get("turn_id") or event.get("turnId")
            if not isinstance(turn, str) or not turn.strip():
                return None
            identity_parts = (host, session, turn, agent)
        raw = "\0".join(str(value) for value in identity_parts)
        readable = "--".join(
            safe_identity(value)[:32] for value in identity_parts
        )
        return readable + "--" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def dir(self) -> Path:
        return self.state.home / ".agent-mem-struct" / "convention-receipts"

    def path(self, event: dict[str, Any]) -> Path:
        identity = self.identity(event)
        if identity is None:
            expected = "prompt_id" if self.state.agent == "claude" else "turn_id"
            raise ValueError(f"hook event lacks a stable session_id and {expected}")
        return self.dir() / f"{identity}.json"

    def remove_receipt(self, event: dict[str, Any]) -> None:
        try:
            self.path(event).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def prune_receipts(self) -> None:
        directory = self.dir()
        now = time.time()
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        survivors: list[tuple[float, Path]] = []
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                modified = entry.stat().st_mtime
                age = now - modified
                if age > (TEMP_MAX_AGE if ".tmp." in entry.name else RECEIPT_MAX_AGE):
                    entry.unlink()
                elif entry.suffix == ".json":
                    survivors.append((modified, entry))
            except OSError:
                continue
        survivors.sort(key=lambda item: item[0], reverse=True)
        for _, entry in survivors[RECEIPT_MAX_FILES:]:
            try:
                entry.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass

    def manifest_chain(self, candidate: Path) -> list[Path]:
        resolved = candidate.resolve(strict=False)
        roots = (
            (self.state.shared_resolved, self.state.shared_resolved),
            ((self.state.memory_root / "local").resolve(strict=False), self.state.memory_root / "local"),
        )
        for resolved_root, display_root in roots:
            if resolved_root is None or not under(resolved, resolved_root):
                continue
                continue
            relative = resolved.relative_to(resolved_root)
            directory_parts = relative.parts[:-1] if candidate.suffix else relative.parts
            manifests = [display_root / "MEMORY.md"]
            current = display_root
            index = 0
            # Only a contiguous submemory/<name> chain denotes scoped groups.
            # Once traversal enters nodes/, log/, or an attachment, any MEMORY.md
            # there is a routing index or historical counterpart, not authority.
            while index + 1 < len(directory_parts) and directory_parts[index] == "submemory":
                current /= directory_parts[index]
                current /= directory_parts[index + 1]
                manifest = current / "MEMORY.md"
                if manifest.exists():
                    manifests.append(manifest)
                index += 2
            return manifests
        return []

    def required_reads(self, path: Path) -> tuple[list[Path], str | None]:
        resolved = path.resolve(strict=False)
        for root in (self.state.memory_root, self.state.shared_resolved):
            if root is None:
                continue
            root_resolved = root.resolve(strict=False)
            if under(resolved, root_resolved) and "log" in resolved.relative_to(root_resolved).parts:
                # Logs are non-authoritative history and cannot add prerequisites.
                return [], None
        if path.suffix.lower() != ".md" or not path.exists():
            return [], None
        text, error = read_text(path)
        if error or text is None or not text.startswith("---\n"):
            return [], None
        end = text.find("\n---\n", 4)
        if end < 0:
            return [], None
        frontmatter = text[4:end]
        match = re.search(r"(?ms)^requires_read:\s*\n((?:\s+-\s+[^\n]+\n?)+)", frontmatter)
        if not match:
            return [], None
        result: list[Path] = []
        for value in re.findall(r"(?m)^\s+-\s+(.+?)\s*$", match.group(1)):
            required = Path(value.strip().strip("'\""))
            candidate = required if required.is_absolute() else path.parent / required
            resolved = candidate.resolve(strict=False)
            active_root = next(
                (
                    root.resolve(strict=False)
                    for root in (self.state.memory_root, self.state.shared_resolved)
                    if root is not None and under(resolved, root)
                ),
                None,
            )
            if active_root is None:
                return [], f"requires_read escapes the active memory roots: {candidate}"
            if "log" in resolved.relative_to(active_root).parts:
                return [], f"requires_read points to non-authoritative log memory: {candidate}"
            if candidate.suffix.lower() != ".md" or not candidate.is_file():
                return [], f"requires_read is not an active memory Markdown file: {candidate}"
            result.append(candidate)
        return result, None

    def bundle(
        self, event: dict[str, Any], *, scoped: bool
    ) -> tuple[str | None, str | None, list[Path], dict[str, str]]:
        if self.state.shared_memory is None:
            return None, "shared directory declaration is invalid", [], {}
        paths = [self.state.shared_memory]
        prerequisite_error = None
        if scoped:
            tool_input = event.get("tool_input")
            cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
            if isinstance(tool_input, dict):
                for raw in MutationScanner.target_strings(tool_input):
                    for candidate in MutationScanner.candidate_paths(raw, cwd):
                        paths.extend(self.manifest_chain(candidate))
                        prerequisites, error = self.required_reads(candidate)
                        paths.extend(prerequisites)
                        prerequisite_error = prerequisite_error or error

        if prerequisite_error:
            return None, prerequisite_error, [], {}
        return self._digest(paths)

    def union_paths(self) -> list[Path]:
        """Every convention source a Pi session acknowledges with pre_memory."""
        paths: list[Path] = []
        if self.state.shared_memory is not None:
            paths.append(self.state.shared_memory)
        paths.append(self.state.root_memory)
        paths.append(self.state.root_rules)
        local = self.state.memory_root / "local" / "MEMORY.md"
        if local.is_file():
            paths.append(local)
        roots = [self.state.memory_root]
        if self.state.shared_resolved is not None:
            roots.append(self.state.shared_resolved)
        for root in roots:
            paths.extend(sorted(root.glob("**/submemory/*/MEMORY.md")))
        return paths

    def acknowledge_union(
        self, event: dict[str, Any]
    ) -> tuple[str | None, str | None, list[Path], str | None]:
        """Build the session convention union, acknowledge it, and return it."""
        digest, body, paths, source_digests = self._digest(self.union_paths())
        if digest is None or body is None:
            return None, None, [], body or "required convention bundle could not be built"
        try:
            self.acknowledge_receipt(event, source_digests)
        except (OSError, ValueError) as exc:
            return None, None, [], f"Convention acknowledgment could not be recorded safely: {exc}"
        return digest, body, paths, None

    def receipt_covers(self, event: dict[str, Any], *, scoped: bool) -> tuple[bool, str]:
        """Whether this session already acknowledged and read current sources.

        Pi acknowledges in two steps: `pre_memory` records the current source
        digests, then the model must open every required source with the read
        tool. A missing read is denied by name so the model can correct it.
        """
        digest, body, paths, source_digests = self.bundle(event, scoped=scoped)
        if digest is None or body is None:
            return False, body or "required convention bundle could not be built"
        if not self.receipt_is_current(event, source_digests):
            return False, PREMEMORY_DENIAL
        missing = self.missing_reads(event, scoped=scoped)
        if missing:
            listing = ", ".join(str(path) for path in missing)
            return False, READ_DENIAL + listing + "."
        return True, ""

    def _is_convention_source(self, resolved: Path) -> bool:
        """Whether a read opened a convention source worth recording."""
        controls = {
            path.resolve(strict=False)
            for path in (self.state.root_memory, self.state.root_rules, self.state.structure)
        }
        if resolved in controls:
            return True
        roots = [self.state.memory_root, self.state.shared_resolved]
        return any(
            root is not None and under(resolved, root.resolve(strict=False))
            for root in roots
        )

    @staticmethod
    def _source_digest(path: Path) -> str | None:
        """The sha256 of one source's bytes, or None when unreadable."""
        text, error = read_text(path)
        if error or text is None:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def required_read_paths(self, event: dict[str, Any], *, scoped: bool) -> list[Path]:
        """The sources the model must open before this action is allowed."""
        paths = [self.state.root_memory, self.state.root_rules]
        local = self.state.memory_root / "local" / "MEMORY.md"
        if local.is_file():
            paths.append(local)
        if self.state.shared_memory is not None:
            paths.append(self.state.shared_memory)
        if scoped:
            tool_input = event.get("tool_input")
            cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
            if isinstance(tool_input, dict):
                for raw in MutationScanner.target_strings(tool_input):
                    for candidate in MutationScanner.candidate_paths(raw, cwd):
                        paths.extend(self.manifest_chain(candidate))
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            if path.is_file():
                unique.append(path)
        return unique

    def missing_reads(self, event: dict[str, Any], *, scoped: bool) -> list[Path]:
        """Required sources whose current bytes this session has not read."""
        reads = self.read_receipt(event).get("reads")
        recorded = reads if isinstance(reads, dict) else {}
        missing: list[Path] = []
        for path in self.required_read_paths(event, scoped=scoped):
            digest = self._source_digest(path)
            key = os.path.normcase(str(path.resolve(strict=False)))
            if digest is None or recorded.get(key) != digest:
                missing.append(path)
        return missing

    def record_read(self, event: dict[str, Any]) -> None:
        """Record the memory sources opened by this read-tool call.

        Best-effort evidence only: a read under the memory roots is hashed
        into the session receipt so the mutation gate can prove it happened.
        """
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return
        cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser()
        current = self.read_receipt(event)
        prior = current.get("reads")
        reads = dict(prior) if isinstance(prior, dict) else {}
        changed = False
        for raw in MutationScanner.target_strings(tool_input):
            for candidate in MutationScanner.candidate_paths(raw, cwd):
                resolved = candidate.resolve(strict=False)
                if not self._is_convention_source(resolved):
                    continue
                digest = self._source_digest(resolved)
                if digest is None:
                    continue
                key = os.path.normcase(str(resolved))
                if reads.get(key) != digest:
                    reads[key] = digest
                    changed = True
        if changed:
            sources = current.get("sources")
            self._store_receipt(
                event,
                sources if isinstance(sources, dict) else {},
                reads,
            )

    def _digest(
        self, paths: list[Path]
    ) -> tuple[str | None, str | None, list[Path], dict[str, str]]:
        """Read, validate, and hash the given sources and their prerequisites."""
        unique: list[Path] = []
        seen: set[str] = set()
        chunks: list[str] = []
        source_digests: dict[str, str] = {}
        pending = list(paths)
        while pending:
            path = pending.pop(0)
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            text, error = read_text(path)
            if error or text is None:
                return None, f"required convention source unavailable: {error or path}", unique, {}
            if (
                path.name == "MEMORY.md"
                and path != self.state.root_memory
                and "## Mandatory conventions" not in text
            ):
                return None, f"required convention manifest is malformed: {path}", unique, {}
            unique.append(path)
            source_digests[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks.append(f"--- BEGIN {path} ---\n{text.rstrip()}\n--- END {path} ---")
            prerequisites, error = self.required_reads(path)
            if error:
                return None, error, unique, {}
            pending.extend(prerequisites)
        body = "\n\n".join(chunks)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return digest, body, unique, source_digests

    def read_receipt(self, event: dict[str, Any]) -> dict[str, Any]:
        path = self.path(event)
        try:
            if path.parent.is_symlink() or not under(path.parent, self.state.home):
                return {}
            if path.is_symlink() or (path.exists() and not path.is_file()):
                return {}
            if path.exists() and path.stat().st_size > 1024 * 1024:
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        shared_root = os.path.normcase(str(self.state.shared_resolved))
        return data if isinstance(data, dict) and data.get("shared_root") == shared_root else {}

    def receipt_is_current(
        self, event: dict[str, Any], source_digests: dict[str, str]
    ) -> bool:
        acknowledged = self.read_receipt(event).get("sources")
        return isinstance(acknowledged, dict) and all(
            acknowledged.get(path) == digest for path, digest in source_digests.items()
        )

    def acknowledge_receipt(self, event: dict[str, Any], source_digests: dict[str, str]) -> None:
        prior = self.read_receipt(event)
        prior_sources = prior.get("sources")
        sources = dict(prior_sources) if isinstance(prior_sources, dict) else {}
        sources.update(source_digests)
        prior_reads = prior.get("reads")
        reads = dict(prior_reads) if isinstance(prior_reads, dict) else {}
        self._store_receipt(event, sources, reads)

    def _store_receipt(
        self, event: dict[str, Any], sources: dict[str, str], reads: dict[str, str]
    ) -> None:
        """Atomically write one session receipt; the only receipt writer."""
        directory = self.dir()
        if directory.is_symlink() or not under(directory, self.state.home):
            raise OSError(f"unsafe convention receipt directory: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(directory.parent, 0o700)
            os.chmod(directory, 0o700)
        except OSError:
            pass
        path = self.path(event)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise OSError(f"unsafe convention receipt path: {path}")
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(
                json.dumps({
                    "shared_root": os.path.normcase(str(self.state.shared_resolved)),
                    "sources": sources,
                    "reads": reads,
                }, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            # A failed acknowledgment must not strand an orphan beside the target.
            temporary.unlink(missing_ok=True)

    def gate(self, event: dict[str, Any], *, scoped: bool) -> tuple[bool, str]:
        if self.identity(event) is None:
            expected = "prompt_id" if self.state.agent == "claude" else "turn_id"
            return False, (
                "Convention acknowledgment cannot be recorded: hook event lacks stable "
                f"session_id and {expected}."
            )
        digest, body, paths, source_digests = self.bundle(event, scoped=scoped)
        if digest is None or body is None:
            return False, body or "required convention bundle could not be built"
        if self.receipt_is_current(event, source_digests):
            return True, ""
        try:
            self.acknowledge_receipt(event, source_digests)
        except (OSError, ValueError) as exc:
            return False, f"Convention acknowledgment could not be recorded safely: {exc}"
        listing = ", ".join(str(path) for path in paths)
        reason = (
            "Convention acknowledgment required before continuing. The authoritative "
            f"sources for this action are now injected ({listing}). Read and obey them, "
            "then retry the same action; that retry is the explicit acknowledgment of "
            f"this exact bundle (sha256:{digest}).\n\n{body}"
        )
        return False, reason
