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


class ConventionGate:
    """Owns receipt records and the convention acknowledgment gate."""

    def __init__(self, state: RootState) -> None:
        self.state = state

    def identity(self, event: dict[str, Any]) -> str | None:
        session = event.get("session_id") or event.get("sessionId")
        if self.state.agent == "claude":
            turn = event.get("prompt_id") or event.get("promptId")
        else:
            turn = event.get("turn_id") or event.get("turnId")
        if not isinstance(session, str) or not session.strip():
            return None
        if not isinstance(turn, str) or not turn.strip():
            return None
        agent = event.get("agent_id") or event.get("agentId") or "parent"
        host = self.state.agent or "unknown"
        raw = "\0".join(str(value) for value in (host, session, turn, agent))
        readable = "--".join(
            safe_identity(value)[:32] for value in (host, session, turn, agent)
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

        unique: list[Path] = []
        seen: set[str] = set()
        chunks: list[str] = []
        source_digests: dict[str, str] = {}
        for path in paths:
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            text, error = read_text(path)
            if error or text is None:
                return None, f"required convention source unavailable: {error or path}", unique, {}
            if path.name == "MEMORY.md" and "## Mandatory conventions" not in text:
                return None, f"required convention manifest is malformed: {path}", unique, {}
            unique.append(path)
            source_digests[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks.append(f"--- BEGIN {path} ---\n{text.rstrip()}\n--- END {path} ---")
            prerequisites, error = self.required_reads(path)
            if error:
                return None, error, unique, {}
            paths.extend(prerequisites)
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
        prior = self.read_receipt(event).get("sources")
        sources = dict(prior) if isinstance(prior, dict) else {}
        sources.update(source_digests)
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(
                json.dumps({
                    "shared_root": os.path.normcase(str(self.state.shared_resolved)),
                    "sources": sources,
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
