"""Mutation-surface and acquisition scanning for the root-memory tool gate.

One owner for every decision about what a tool call touches: the shell and
interpreter mutation patterns, target extraction from tool input, the
acknowledgment fast path, the read/acquisition commands that open a
convention source, and the `tree` invocations that read the shared structure.
The scanner is stateless; all methods are static.
"""
from __future__ import annotations

import os
import re
import shutil
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
READ_TOOL_NAMES = {"read", "read_file", "view"}
SHELL_TOOL_NAMES = {"bash", "powershell", "shell", "exec_command", "command"}
SHELL_READ_ONLY_RE = re.compile(
    r"^\s*(?:(?:pwd|ls|dir|cat|head|tail|stat|where|which|rg|grep|find|tree|"
    r"Get-Content|Get-ChildItem|gci|Select-String|Test-Path|Resolve-Path|"
    r"git\s+(?:status|diff|log|show|grep|rev-parse|branch\s+--list))\b|"
    r"sed\s+-n\s+(['\"]?)\d+(?:,\d+)?p\1\s+(?:--\s+)?"
    r"(?!-)\S+(?:\s+(?!-)\S+)*\s*$)",
    re.IGNORECASE,
)
# The protocol's own lister is invoked as `INTERPRETER SCRIPT [--list] [--] ROOT`.
INTERPRETER_NAME_RE = re.compile(r"^(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?$", re.IGNORECASE)
INTERPRETER_FLAGS = frozenset({"-I", "-B", "-E", "-S", "-u"})
# find's own mutators do not run through a shell operator, so they need their
# own pattern. Quoting is stripped first, because `-de'lete'` is the same
# command to the shell.
FIND_MUTATION_RE = re.compile(
    r"(?:^|[;&|(]\s*)find\b(?:(?!;|&|\|).)*?\s-(?:delete|exec|execdir|ok|okdir|"
    r"fprint0|fprintf|fprint|fls)\b",
    re.IGNORECASE | re.DOTALL,
)
# A `tree` invocation, optionally wrapped in `cmd /c` or `env`, at the start of
# a shell segment. The rest of the segment is the option/path list.
TREE_TOKEN_RE = re.compile(
    r"^\s*(?:cmd(?:\.exe)?\s+(?://|/)[cC]\s+|env\s+)?tree(?:\.com|\.exe)?\b(?P<rest>.*)$",
    re.IGNORECASE,
)
TREE_ALLOWED_FLAGS = frozenset({
    "-a", "-F", "-C", "-n", "-f", "-i", "-q", "-s", "-h", "-u",
    "-g", "-D", "-p", "-v", "--noreport",
})
TREE_REJECTED_FLAGS = frozenset({
    "-L", "-d", "-P", "--prune", "--filelimit", "--fromfile", "--fromtabfile",
})
SED_PRINT_RANGE_RE = re.compile(r"^(?P<start>\d+)\s*,\s*(?P<end>\d+|\$)\s*p$")
PY_PRINT_OPEN_RE = re.compile(
    r"print\s*\(\s*open\s*\(\s*(?P<quote>['\"])(?P<path>.+?)(?P=quote)"
)
QUOTED_TOKEN_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_count(path: Path) -> int | None:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return None


def _hides_output(text: str) -> bool:
    """Whether a shell line pipes, redirects, or hides the read output."""
    return any(token in text for token in ("|", ">", "<", "$(", "`"))


def _interpreter_available(token: str, base: Path) -> bool:
    """Whether a python-like interpreter token actually resolves to a program.

    A relative token such as `./python` must exist and be executable; a bare
    name is looked up on PATH. This keeps a nonexistent shim from earning the
    structure credit for a lister that never ran.
    """
    path = Path(token)
    if path.is_absolute() or os.sep in token or (os.altsep and os.altsep in token):
        candidate = path if path.is_absolute() else base / path
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(token) is not None


def _tokens(text: str) -> list[str]:
    return [
        quoted or quoted2 or bare
        for quoted, quoted2, bare in QUOTED_TOKEN_RE.findall(text)
    ]


class MutationScanner:
    """Static single-purpose probes over one tool call's read/write surface."""

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
        dequoted = command.replace("'", "").replace('"', "")
        if FIND_MUTATION_RE.search(dequoted):
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
    def tool_is_read(tool_name: str, tool_input: dict[str, Any]) -> bool:
        """Whether this call opens one or more memory sources with a read tool."""
        if str(tool_name).lower() not in READ_TOOL_NAMES:
            return False
        return bool(list(MutationScanner.target_strings(tool_input)))

    @staticmethod
    def tool_requires_acknowledgment(tool_name: str, tool_input: dict[str, Any]) -> bool:
        name = tool_name.lower()
        # pre_memory is the acknowledgment itself; it runs on PreMemory, and an
        # accidental PreToolUse for it must never be gated.
        if name == "pre_memory":
            return False
        if any(token in name for token in ("write", "edit", "patch", "delete", "remove", "rename", "move", "create", "update")):
            return True
        if name in SHELL_TOOL_NAMES or "shell" in name:
            command = "\n".join(MutationScanner.all_strings(tool_input))
            if MutationScanner.command_is_mutating(command):
                return True
            if MutationScanner._acquisition_in_command(command, Path.cwd()):
                # A shell line that only opens sources with a read primitive
                # (cat, a complete sed -n range, print(open(...).read())) is a
                # read, never a gated action.
                return False
            if re.search(r"[;&|`]|\$\(|\r|\n", command):
                return True
            return not bool(SHELL_READ_ONLY_RE.match(command))
        return not any(token in name for token in READ_ONLY_TOOL_TOKENS)

    # -- acquisition (reads) -------------------------------------------------

    @staticmethod
    def _shell_commands(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
        """The shell command lines a tool call carries, if it is a shell tool."""
        name = str(tool_name).lower()
        if name not in SHELL_TOOL_NAMES and "shell" not in name and "exec" not in name:
            return []
        commands: list[str] = []
        if isinstance(tool_input, dict):
            for key, value in tool_input.items():
                if isinstance(value, str) and str(key).lower() in COMMAND_KEY_TOKENS:
                    commands.append(value)
        return commands

    @staticmethod
    def _read_covers_file(path: Path, offset: int | None, limit: int | None) -> bool:
        """Whether a Read call shows the whole file, not a windowed slice."""
        if offset is None and limit is None:
            return True
        lines = _line_count(path)
        if lines is None:
            return False
        start = 1 if offset is None else offset
        if start > 1:
            return False
        if limit is None:
            return True
        return start + limit - 1 >= lines

    @staticmethod
    def _cat_targets(segment: str, cwd: Path) -> list[Path]:
        match = re.match(r"\s*cat\b(?P<rest>.*)$", segment, re.IGNORECASE)
        if not match:
            return []
        targets: list[Path] = []
        for token in _tokens(match.group("rest")):
            if token.startswith("-") or token in ("--",):
                continue
            candidate = MutationScanner.path_from_string(token, cwd)
            if candidate is not None:
                targets.append(candidate)
        return targets

    @staticmethod
    def _sed_targets(segment: str, cwd: Path) -> list[Path]:
        match = re.match(r"\s*sed\b(?P<rest>.*)$", segment, re.IGNORECASE)
        if not match:
            return []
        if SED_MUTATION_RE.search(segment):
            return []
        tokens = _tokens(match.group("rest"))
        script_index = None
        for index, token in enumerate(tokens):
            if SED_PRINT_RANGE_RE.match(token):
                script_index = index
                break
        if script_index is None:
            return []
        range_match = SED_PRINT_RANGE_RE.match(tokens[script_index])
        start = int(range_match.group("start"))
        end = range_match.group("end")
        line_limited = start > 1
        targets: list[Path] = []
        for token in tokens[script_index + 1:]:
            if token.startswith("-"):
                continue
            candidate = MutationScanner.path_from_string(token, cwd)
            if candidate is None:
                continue
            if line_limited:
                continue
            if end != "$":
                lines = _line_count(candidate)
                if lines is None or int(end) < lines:
                    continue
            targets.append(candidate)
        return targets

    @staticmethod
    def _python_read_targets(segment: str, cwd: Path) -> list[Path]:
        targets: list[Path] = []
        for match in PY_PRINT_OPEN_RE.finditer(segment):
            candidate = MutationScanner.path_from_string(match.group("path"), cwd)
            if candidate is not None:
                targets.append(candidate)
        return targets

    @staticmethod
    def acquisition_targets(
        tool_name: str, tool_input: dict[str, Any], cwd: Path
    ) -> list[Path]:
        """The convention-source candidates a tool call fully opens.

        A read tool yields its file_path when the read shows the whole file; a
        shell line yields bare `cat` operands, complete `sed -n` ranges, and
        `print(open(...).read())` paths. A piped or redirected line is excluded
        because it may truncate the output the model sees.
        """
        name = str(tool_name).lower()
        targets: list[Path] = []
        if name in READ_TOOL_NAMES:
            offset = _int_or_none(tool_input.get("offset"))
            limit = _int_or_none(tool_input.get("limit"))
            for raw in MutationScanner.target_strings(tool_input):
                candidate = MutationScanner.path_from_string(raw, cwd)
                if candidate is not None and MutationScanner._read_covers_file(
                    candidate, offset, limit
                ):
                    targets.append(candidate)
            return MutationScanner._unique(targets)
        for command in MutationScanner._shell_commands(tool_name, tool_input):
            processed = MutationScanner._acquisition_in_command(command, cwd)
            targets.extend(processed)
        return MutationScanner._unique(targets)

    @staticmethod
    def _acquisition_in_command(command: str, cwd: Path) -> list[Path]:
        if _hides_output(command):
            # Pipes/redirects can truncate or hide the content; only a bare
            # acquisition line proves the model saw the source.
            return []
        targets: list[Path] = []
        for segment in re.split(r"&&|;", command):
            segment = segment.strip()
            if not segment:
                continue
            targets.extend(MutationScanner._cat_targets(segment, cwd))
            targets.extend(MutationScanner._sed_targets(segment, cwd))
            targets.extend(MutationScanner._python_read_targets(segment, cwd))
        return targets

    @staticmethod
    def _unique(paths: list[Path]) -> list[Path]:
        seen: set[str] = set()
        unique: list[Path] = []
        for path in paths:
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    # -- tree acquisition ----------------------------------------------------

    @staticmethod
    def shell_structure_read(
        tool_name: str, tool_input: dict[str, Any], cwd: Path
    ) -> tuple[bool, Path | None, bool]:
        """Whether a shell call reads a complete directory structure.

        Recognizes `tree` and its fallbacks: `find`, `ls -aR`, PowerShell
        `Get-ChildItem -Recurse -Force`. The first element is True for any
        recognized listing so the gate never blocks the command it asks for;
        the second is the resolved directory when the listing is complete and
        unfiltered; the third is True only for a clean listing - no pipe,
        redirect, substitution, or mutation - which is the only form the
        caller may exempt from the gate.
        """
        name = str(tool_name).lower()
        powershell = "powershell" in name or "pwsh" in name
        for command in MutationScanner._shell_commands(tool_name, tool_input):
            found = MutationScanner._structure_in_command(command, cwd, powershell)
            if found is not None:
                recognized, target = found
                clean = not _hides_output(command) and not MutationScanner.command_is_mutating(command)
                return (recognized, target, clean)
        return (False, None, False)

    @staticmethod
    def lister_invocation(
        tool_name: str, tool_input: dict[str, Any], cwd: Path, script_path: Path
    ) -> Path | None:
        """The shared root a trusted `rm_tree.py` invocation names, or None.

        Only the protocol's own lister counts: a python-like interpreter, the
        exact hook script, a single segment with no pipe or redirect, and
        exactly one root operand. The root is never guessed from the working
        directory, so an accidental call cannot credit the wrong tree.
        """
        script = Path(script_path).resolve(strict=False)
        for command in MutationScanner._shell_commands(tool_name, tool_input):
            if _hides_output(command):
                continue
            if re.search(r"(?<!&)&(?!&)", command):
                # A background `&` is a second command, not a clean listing.
                continue
            base = cwd
            cd_match = re.match(
                r"\s*cd\s+(?P<dir>'[^']*'|\"[^\"]*\"|\S+)\s*&&\s*(?P<rest>.+)$",
                command, re.IGNORECASE | re.DOTALL,
            )
            if cd_match:
                directory = MutationScanner.path_from_string(cd_match.group("dir"), cwd)
                if directory is not None:
                    base = directory
                command = cd_match.group("rest")
            segments = [s for s in re.split(r"\|\||&&|;|&|\n", command) if s.strip()]
            if len(segments) != 1:
                continue
            tokens = _tokens(segments[0])
            if not tokens or not INTERPRETER_NAME_RE.match(Path(tokens[0]).name):
                continue
            if not _interpreter_available(tokens[0], base):
                continue
            name = Path(tokens[0]).name
            lowered = name[:-4].lower() if name.lower().endswith(".exe") else name.lower()
            index = 1
            while index < len(tokens) and tokens[index] in INTERPRETER_FLAGS:
                index += 1
            if lowered == "py" and index < len(tokens) and re.match(
                r"^-\d(?:\.\d+)?$", tokens[index]
            ):
                index += 1
            if index >= len(tokens):
                continue
            candidate = MutationScanner.path_from_string(tokens[index], base)
            index += 1
            if candidate is None:
                continue
            try:
                same = os.path.samefile(candidate, script)
            except OSError:
                same = candidate.resolve(strict=False) == script
            if not same:
                continue
            root_token = None
            valid = True
            while index < len(tokens):
                token = tokens[index]
                if token == "--list":
                    index += 1
                    continue
                if token == "--":
                    index += 1
                    if index < len(tokens) and root_token is None:
                        root_token = tokens[index]
                        index += 1
                    continue
                if token.startswith("-") or root_token is not None:
                    valid = False
                    break
                root_token = token
                index += 1
            if not valid or root_token is None:
                continue
            root = MutationScanner.path_from_string(root_token, base)
            if root is not None:
                return root
        return None

    @staticmethod
    def _structure_in_command(
        command: str, cwd: Path, powershell: bool
    ) -> tuple[bool, Path | None] | None:
        """Report one structure listing, or None when the line is not one.

        Only a single command segment counts (after an optional `cd DIR &&`
        prefix): a compound line is gated as the ordinary command it is, so a
        mutating segment cannot ride in behind a listing. A piped or redirected
        listing is recognized but records nothing.
        """
        base = cwd
        cd_match = re.match(
            r"\s*cd\s+(?P<dir>'[^']*'|\"[^\"]*\"|\S+)\s*&&\s*(?P<rest>.+)$",
            command, re.IGNORECASE | re.DOTALL,
        )
        if cd_match:
            directory = MutationScanner.path_from_string(cd_match.group("dir"), cwd)
            if directory is not None:
                base = directory
            command = cd_match.group("rest")
        segments = [
            segment
            for segment in re.split(r"\|\||&&|;|&|\n", command)
            if segment.strip()
        ]
        if len(segments) != 1:
            return None
        segment = segments[0].strip()
        matches_tree = TREE_TOKEN_RE.match(segment)
        if matches_tree:
            if _hides_output(segment):
                return (True, None)
            windows_tree = bool(
                re.search(r"tree(?:\.com|\.exe)\b", segment, re.IGNORECASE)
                or re.match(r"\s*cmd(?:\.exe)?\b", segment, re.IGNORECASE)
            )
            target, complete = MutationScanner._tree_operand(
                matches_tree.group("rest"), base, windows_tree
            )
            return (True, target if complete else None)
        parser = (
            MutationScanner._powershell_structure
            if powershell
            else MutationScanner._bash_structure
        )
        parsed = parser(segment, base)
        if parsed is None:
            return None
        target, complete = parsed
        if _hides_output(segment):
            return (True, None)
        return (True, target if complete else None)

    @staticmethod
    def _bash_structure(segment: str, base: Path) -> tuple[Path | None, bool] | None:
        """A `find`, `ls -aR`, or cmd `dir /s /a` listing on a POSIX shell."""
        for parser in (
            MutationScanner._find_structure,
            MutationScanner._ls_structure,
        ):
            parsed = parser(segment, base)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _powershell_structure(segment: str, base: Path) -> tuple[Path | None, bool] | None:
        """A `Get-ChildItem -Recurse -Force` listing in a PowerShell tool."""
        match = re.match(r"\s*(?:get-childitem|gci|dir|ls)\b(?P<rest>.*)$", segment, re.IGNORECASE)
        if not match:
            return None
        tokens = _tokens(match.group("rest"))
        recurse = force = False
        complete = True
        operand_tokens: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            lowered = token.lower()
            if lowered in ("-recurse", "/recurse"):
                recurse = True
            elif lowered in ("-force", "/force"):
                force = True
            elif lowered in ("-literalpath", "-path", "/path"):
                following = tokens[index + 1] if index + 1 < len(tokens) else ""
                if following:
                    operand_tokens.append(following)
                    index += 1
                else:
                    complete = False
            elif lowered.startswith(("-depth", "-filter", "-include", "-exclude",
                                     "-directory", "-file", "-attributes", "-name",
                                     "-erroraction", "-outfile")):
                complete = False
            elif token.startswith("-"):
                complete = False
            else:
                operand_tokens.append(token)
            index += 1
        if not recurse or not force or len(operand_tokens) > 1:
            complete = False
        target = (
            MutationScanner.path_from_string(operand_tokens[0], base)
            if operand_tokens
            else base
        )
        return (target, complete and target is not None)

    @staticmethod
    def _find_structure(segment: str, base: Path) -> tuple[Path | None, bool] | None:
        """A `find DIR [-print]` listing; a mutating find is not a read."""
        match = re.match(r"\s*find\b(?P<rest>.*)$", segment, re.IGNORECASE)
        if not match:
            return None
        tokens = _tokens(match.group("rest"))
        mutators = {
            "-delete", "-exec", "-execdir", "-ok", "-okdir",
            "-fprint", "-fprint0", "-fprintf", "-fls",
        }
        if any(token.replace("'", "").replace('"', "").lower() in mutators for token in tokens):
            return None
        complete = True
        operand_tokens: list[str] = []
        for token in tokens:
            lowered = token.lower()
            if lowered in ("-h", "-l", "-print"):
                continue
            if token.startswith(("-", "!", "(", ")")):
                complete = False
                continue
            operand_tokens.append(token)
        if len(operand_tokens) != 1:
            complete = False
        if not operand_tokens:
            return (base, False)
        target = MutationScanner.path_from_string(operand_tokens[0], base)
        if (
            target is not None
            and target.is_symlink()
            and not operand_tokens[0].endswith(("/", "\\"))
        ):
            complete = False
        return (target, complete and target is not None)

    @staticmethod
    def _ls_structure(segment: str, base: Path) -> tuple[Path | None, bool] | None:
        """An `ls -aR DIR` listing; a missing -R or hidden-entry flag fails it."""
        match = re.match(r"\s*ls\b(?P<rest>.*)$", segment, re.IGNORECASE)
        if not match:
            return None
        tokens = _tokens(match.group("rest"))
        recursive = hidden = False
        complete = True
        operand_tokens: list[str] = []
        allowed_flags = set("aARlh1F")
        for token in tokens:
            if token == "--":
                continue
            if token.startswith("--"):
                if token in ("--all", "--recursive"):
                    hidden = hidden or token == "--all"
                    recursive = recursive or token == "--recursive"
                else:
                    complete = False
                continue
            if token.startswith("-") and len(token) > 1:
                letters = token[1:]
                if not set(letters) <= allowed_flags:
                    complete = False
                    continue
                recursive = recursive or ("R" in letters)
                hidden = hidden or ("a" in letters or "A" in letters)
                continue
            operand_tokens.append(token)
        if not recursive or not hidden or len(operand_tokens) > 1:
            complete = False
        target = (
            MutationScanner.path_from_string(operand_tokens[0], base)
            if operand_tokens
            else base
        )
        if (
            target is not None
            and operand_tokens
            and target.is_symlink()
            and not operand_tokens[0].endswith(("/", "\\"))
        ):
            # `ls -R LINK` prints the link itself, not the tree it names.
            complete = False
        return (target, complete and target is not None)

    @staticmethod
    def _tree_operand(
        rest: str, base: Path, windows_tree: bool = False
    ) -> tuple[Path | None, bool]:
        """The directory a tree invocation names, and whether it is complete.

        Options are recognized wherever they appear, and every non-flag token
        is part of the path (an unquoted directory may contain spaces). A
        rejected or unknown flag marks the read incomplete, so it records
        nothing. A Windows `tree`/`tree.com` lists directories only unless `/F`
        is given, so `/F` is required there; on a POSIX shell `/a` and `/f` are
        ordinary absolute paths.
        """
        tokens = _tokens(rest)
        complete = True
        saw_win_full = False
        path_tokens: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            lowered = token.lower()
            if windows_tree and lowered in ("/f", "/a"):
                saw_win_full = saw_win_full or lowered == "/f"
                index += 1
                continue
            if token in TREE_REJECTED_FLAGS or token.startswith(
                ("--filelimit", "--prune", "--fromfile", "--fromtabfile")
            ):
                complete = False
                index += 1
                continue
            if token.startswith("--charset="):
                index += 1
                continue
            if token.startswith("--ignore="):
                if token.split("=", 1)[1].strip("./") != "git":
                    complete = False
                index += 1
                continue
            if token in TREE_ALLOWED_FLAGS:
                index += 1
                continue
            if token in ("-I", "--ignore"):
                following = tokens[index + 1] if index + 1 < len(tokens) else ""
                if following.strip("./") != "git":
                    complete = False
                index += 2
                continue
            if token.startswith("-") and len(token) > 1:
                # An unknown flag may hide entries; do not count the read.
                complete = False
                index += 1
                continue
            path_tokens.append(token)
            index += 1
        if windows_tree and not saw_win_full:
            complete = False
        joined = " ".join(path_tokens) if path_tokens else ""
        if path_tokens:
            target = MutationScanner.path_from_string(joined, base)
        else:
            target = base
        if (
            target is not None
            and target.is_symlink()
            and joined
            and not joined.endswith(("/", "\\"))
        ):
            # `tree LINK` prints the link itself, not the tree it names.
            complete = False
        return (target, complete and target is not None)
