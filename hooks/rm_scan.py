"""Mutation-surface scanning for the root-memory tool gate.

One owner for every decision about whether a tool call can write: the shell
and interpreter mutation patterns, target extraction from tool input, and the
acknowledgment fast path. The scanner is stateless; all methods are static.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

SHELL_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"rm\b|mv\b|cp\b|mkdir\b|rmdir\b|touch\b|"
    r"perl\s+-pi\b|patch\b|tee\b|"
    r"git\s+(?:apply|checkout|reset|clean)\b|"
    r"powershell\b[^\n]*(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item)"
    r")",
    re.IGNORECASE,
)
# sed's mutation surface (in-place flags anywhere in its arguments, or a `w`/`e`
# one-letter script command) doesn't fit the single command-start anchor above,
# so it gets its own pattern, still anchored to a `sed` invocation and bounded
# to that one chained command.
SED_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)sed\b(?:(?!;|&|\|).)*?(?:"
    r"(?:^|[\s,])-[a-zA-Z0-9]*i[a-zA-Z0-9]*(?:\s|=|$)|"
    r"--in-place\b|"
    r"(?:^|[;\n'\"]|,|\$|[0-9])\s*[we]\s+\S"
    r")",
    re.IGNORECASE,
)
REDIRECT_RE = re.compile(r"(?:^|[^<])>{1,2}\s*[^&]", re.MULTILINE)
# An embedded script writes without a shell redirect, and a heredoc body sits on
# lines after the interpreter, so the two halves are matched over the whole
# command rather than one line. A write primitive is required, which keeps a
# read-only one-liner out of the guard.
INTERPRETER_RE = re.compile(
    r"(?:^|[;&|(]\s*)(?:python(?:3)?|perl|ruby|node)\b", re.IGNORECASE | re.MULTILINE
)
SCRIPT_MUTATION_RE = re.compile(
    r"write_text|write_bytes|writeFileSync|appendFileSync|unlinkSync|rmSync|"
    r"open\s*\([^)\n]*,\s*['\"][rwxab+]*[wxa+][rwxab+]*['\"]|"
    r"os\.(?:remove|unlink|rename|replace|mkdir|makedirs|rmdir)|"
    r"shutil\.(?:copy|copy2|copyfile|move|rmtree)|"
    r"File\.(?:write|delete|rename)|FileUtils\.",
    re.IGNORECASE,
)
TARGET_KEY_TOKENS = ("path", "file", "target", "dest", "patch", "cwd")
COMMAND_KEY_TOKENS = ("command", "cmd")
PATCH_HEADER_RE = re.compile(
    r"(?m)^\*\*\* (?:Add File|Update File|Delete File|Move to):[ \t]*(.*?)[ \t]*\r?$"
)
READ_ONLY_TOOL_TOKENS = (
    "read", "view", "get", "list", "search", "find", "status", "grep", "glob",
)
SHELL_TOOL_NAMES = {"bash", "powershell", "shell", "exec_command", "command"}
SHELL_READ_ONLY_RE = re.compile(
    r"^\s*(?:(?:pwd|ls|dir|cat|head|tail|stat|where|which|rg|grep|find|"
    r"Get-Content|Get-ChildItem|Select-String|Test-Path|Resolve-Path|"
    r"git\s+(?:status|diff|log|show|grep|rev-parse|branch\s+--list))\b|"
    r"sed\s+-n\s+(['\"]?)\d+(?:,\d+)?p\1\s+(?:--\s+)?"
    r"(?!-)\S+(?:\s+(?!-)\S+)*\s*$)",
    re.IGNORECASE,
)


class MutationScanner:
    """Static single-purpose probes over one tool call's mutation surface."""

    @staticmethod
    def all_strings(value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from MutationScanner.all_strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from MutationScanner.all_strings(item)

    @staticmethod
    def command_is_mutating(command: str) -> bool:
        if SHELL_MUTATION_RE.search(command) or SED_MUTATION_RE.search(command) or REDIRECT_RE.search(command):
            return True
        if INTERPRETER_RE.search(command) and SCRIPT_MUTATION_RE.search(command):
            return True
        return False

    @staticmethod
    def target_strings(value: Any) -> Iterable[str]:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            key_lower = str(key).lower()
            if isinstance(item, str):
                if any(token in key_lower for token in COMMAND_KEY_TOKENS):
                    # A command/cmd value is a whole shell line, not a single path:
                    # only its mutating primitives (redirects, rm/mv/sed -i, ...)
                    # can name a write target. Scanning every token unconditionally
                    # treats flags, subcommands, and arguments as candidate paths
                    # too, which false-positives on any read-only command once cwd
                    # sits inside the tree being guarded, since every relative
                    # token then resolves "under" it by construction.
                    if MutationScanner.command_is_mutating(item):
                        yield item
                elif any(token in key_lower for token in TARGET_KEY_TOKENS):
                    yield item
            elif isinstance(item, dict):
                yield from MutationScanner.target_strings(item)
            elif isinstance(item, list):
                for child in item:
                    if isinstance(child, dict):
                        yield from MutationScanner.target_strings(child)

    @staticmethod
    def path_from_string(value: str, cwd: Path) -> Path | None:
        text = value.strip().strip("'\"")
        if not text or "\n" in text or len(text) > 4096:
            return None
        text = os.path.expandvars(os.path.expanduser(text))
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        return candidate

    @staticmethod
    def candidate_paths(raw: str, cwd: Path) -> Iterable[Path]:
        direct = MutationScanner.path_from_string(raw, cwd)
        if direct is not None:
            yield direct
        # apply_patch paths are line-delimited rather than shell arguments. Extract
        # the complete header value before the fallback token scan splits spaces.
        for value in PATCH_HEADER_RE.findall(raw):
            candidate = MutationScanner.path_from_string(value, cwd)
            if candidate is not None:
                yield candidate
        # Preserve complete quoted arguments (including Windows backslashes). The
        # broad token scan below still handles unquoted paths and patch headers.
        # shlex's POSIX escaping would corrupt native Windows paths.
        for pattern in (r'"([^"\r\n]*)"', r"'([^'\r\n]*)'"):
            for value in re.findall(pattern, raw):
                candidate = MutationScanner.path_from_string(value, cwd)
                if candidate is not None:
                    yield candidate
        for token in re.split(r"[\s,;(){}\[\]|&<>]+", raw):
            candidate = MutationScanner.path_from_string(token, cwd)
            if candidate is not None:
                yield candidate

    @staticmethod
    def tool_requires_acknowledgment(tool_name: str, tool_input: dict[str, Any]) -> bool:
        name = tool_name.lower()
        if any(token in name for token in ("write", "edit", "patch", "delete", "remove", "rename", "move", "create", "update")):
            return True
        if name in SHELL_TOOL_NAMES or "shell" in name:
            command = "\n".join(MutationScanner.all_strings(tool_input))
            if MutationScanner.command_is_mutating(command):
                return True
            if re.search(r"[;&|`]|\$\(|\r|\n", command):
                return True
            return not bool(SHELL_READ_ONLY_RE.match(command))
        return not any(token in name for token in READ_ONLY_TOOL_TOKENS)
