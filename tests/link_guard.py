#!/usr/bin/env python3
"""No test source may create a link.

House rule (the owner's, stated twice): symlinks are prohibited anywhere in
this repository, junctions and other reparse points included, so a test that
needs an alias proves the decision that refuses one by handing in its answer
- never by making the alias on the host first. A fixture that creates a link
is also a fixture that needs a privilege Windows does not grant by default,
which turns a real property into a skip.

Hard links are not symlinks and are outside this rule; a fixture reaching for
one is a review question, not something this check answers.

Python is read as a syntax tree, so a comment, a message, or a `mock.patch`
that *refuses* a link is never a finding, and a command handed to
`subprocess` or `os.system` is read from its arguments. TypeScript,
JavaScript, shell, and PowerShell are read line by line with a string and
comment masker, so text inside a string is not mistaken for a call. What this
cannot see is an operation built at run time from pieces this check does not
resolve (a command name read from a file, say): it is a static barrier and a
regression guard, not a proof that no reparse point can ever be created.

    python3 tests/link_guard.py            # scan this repository's tests/

Exit 0 when nothing creates a link, 1 with a `file: line: what` finding for
every site that does.
"""

import ast
import os
import re
import sys
import warnings

SUFFIXES = (".py", ".ts", ".mts", ".cts", ".cjs", ".mjs", ".js",
            ".sh", ".bash", ".bat", ".cmd", ".ps1")
SHELL_SUFFIXES = (".sh", ".bash", ".bat", ".cmd", ".ps1")
PRUNE = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".vendor"}

# Python: `os.symlink`, `Path.symlink_to`, and any alias of either.
PY_ATTRS = {"symlink", "symlink_to"}
PY_MODULES = {"os", "pathlib"}
PY_IMPORTED = {"symlink", "symlink_to"}
# Python: a link made by a command this file runs.
PY_RUNNERS = {"run", "Popen", "call", "check_call", "check_output", "system",
              "popen", "spawnv", "spawnl"}
PY_SUBPROCESS = {"subprocess"}
PY_SYSTEM = {"system", "popen"}

# TypeScript/JavaScript: node:fs's symlink and symlinkSync.
JS_FS_MODULES = ("fs", "node:fs", "fs/promises", "node:fs/promises")
JS_FS_IMPORT = re.compile(
    r"import\s+(?:\*\s+as\s+([A-Za-z_$][\w$]*)|\{([^}]*)\}|([A-Za-z_$][\w$]*))"
    r"\s+from\s*[\"']([^\"']+)[\"']")
JS_FS_REQUIRE = re.compile(
    r"(?:\*\s+as\s+([A-Za-z_$][\w$]*)|const\s+([A-Za-z_$][\w$]*)\s*=|"
    r"const\s*\{([^}]*)\}\s*=)\s*require\(\s*[\"']([^\"']+)[\"']\s*\)")
JS_LINK_NAMES = ("symlink", "symlinkSync")

# Shell and PowerShell: ln -s / --symbolic, mklink, New-Item -ItemType.
SHELL_ITEMTYPE = re.compile(r"-itemtype\s+[\"']?(?:symboliclink|junction)",
                            re.IGNORECASE)
SHELL_SHELLS = {"bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh",
                "pwsh.exe", "cmd.exe"}
SHELL_COMMAND_FLAGS = {"-c", "/c", "//c", "-command", "-encodedcommand"}
SHELL_SPLIT = re.compile(r"\|\||&&|;|\|")
LITERAL = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")


class LinkCreationGuard:
    """Find a test source that would make a link on the host."""

    def __init__(self, root):
        self.root = root
        self.findings = []

    def check(self):
        """Every finding, as `tests/relative/path: line N: what`."""
        for path in self.sources():
            relative = os.path.relpath(path, self.root).replace(os.sep, "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError as failure:
                # Fail closed: a test source nobody could read is a test
                # source nobody vouched for.
                self.findings.append(f"{relative}: unreadable: {failure}")
                continue
            if path.endswith(".py"):
                sites = LinkCreationGuard.python_sites(text)
            else:
                sites = LinkCreationGuard.text_sites(
                    text, path.endswith(SHELL_SUFFIXES))
            for line, what in sites:
                self.findings.append(f"{relative}: line {line}: {what}")
        return self.findings

    def sources(self):
        """Every test source under the repository's tests directory."""
        base = os.path.join(self.root, "tests")
        found = []

        def unreadable(failure):
            self.findings.append(
                f"tests: unreadable directory: {getattr(failure, 'filename', failure)}")

        for dirpath, dirs, files in os.walk(base, onerror=unreadable):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE)
            for name in sorted(files):
                if name.endswith(SUFFIXES):
                    found.append(os.path.join(dirpath, name))
        return found

    @staticmethod
    def python_sites(text):
        """(line, what) for every link-creating call, from the syntax tree."""
        try:
            with warnings.catch_warnings():
                # A test source with an unrecognised escape is a lint
                # finding elsewhere, not a link finding here.
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except SyntaxError as failure:
            return [(failure.lineno or 1,
                     f"does not parse, so it cannot be cleared: {failure.msg}")]
        modules = set()
        functions = set()
        runners = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    head = name.name.split(".")[0]
                    if head in PY_MODULES:
                        modules.add(name.asname or head)
                    if head in PY_SUBPROCESS:
                        runners.add(name.asname or head)
            elif isinstance(node, ast.ImportFrom) and node.module:
                for name in node.names:
                    if node.module in PY_MODULES and name.name in PY_IMPORTED:
                        functions.add(name.asname or name.name)
                    if node.module in PY_SUBPROCESS and name.name in PY_RUNNERS:
                        runners.add(name.asname or name.name)
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr in PY_ATTRS:
                receiver = target.value
                if isinstance(receiver, ast.Name) and receiver.id in modules:
                    sites.append((node.lineno, f"{receiver.id}.{target.attr} creates a link"))
                elif target.attr == "symlink_to":
                    sites.append((node.lineno, "symlink_to creates a link"))
            elif isinstance(target, ast.Name) and target.id in functions:
                sites.append((node.lineno, f"{target.id} creates a link"))
            sites.extend(LinkCreationGuard.command_sites(node, modules, runners))
        return sorted(set(sites))

    @staticmethod
    def command_sites(node, modules, runners):
        """A link made by a command a Python call runs."""
        target = node.func
        runner = False
        if isinstance(target, ast.Attribute):
            receiver = target.value
            if isinstance(receiver, ast.Name) and receiver.id in modules \
                    and target.attr in PY_SYSTEM:
                runner = True
            elif isinstance(receiver, ast.Name) and receiver.id in runners \
                    and target.attr in PY_RUNNERS:
                runner = True
        elif isinstance(target, ast.Name) and target.id in runners:
            runner = True
        if not runner or not node.args:
            return []
        for candidate in LinkCreationGuard.commands(node.args[0]):
            sites = LinkCreationGuard.shell_sites(candidate, node.lineno)
            if sites:
                return [(node.lineno, f"a command run from the test creates a link: {sites[0][1]}")]
        return []

    @staticmethod
    def commands(node):
        """The commands an argument names, one per spelling."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.List, ast.Tuple)):
            pieces = [item.value for item in node.elts
                      if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            return [" ".join(pieces)] if pieces else []
        return []

    @staticmethod
    def text_sites(text, shell):
        """(line, what) for every link-creating call in TS/JS or shell source."""
        handlers = (LinkCreationGuard.js_names(text) if not shell
                    else (set(), set()))
        imported, namespaces = handlers
        sites = []
        for lineno, raw in enumerate(text.splitlines(), 1):
            masked = LinkCreationGuard.masked(raw, shell)
            if shell:
                for candidate in [masked] + LinkCreationGuard.quoted(raw):
                    sites.extend(LinkCreationGuard.shell_sites(candidate, lineno))
                continue
            sites.extend(LinkCreationGuard.js_sites(masked, lineno,
                                                    imported, namespaces))
        return sorted(set(sites))

    @staticmethod
    def js_names(text):
        """The link names a module imports, and its node:fs namespaces."""
        imported = set()
        namespaces = set()
        for match in JS_FS_IMPORT.finditer(text):
            namespaces_part, named, default, source = match.groups()
            if source not in JS_FS_MODULES:
                continue
            if namespaces_part:
                namespaces.add(namespaces_part)
            if default:
                namespaces.add(default)
            for piece in (named or "").split(","):
                parts = [part.strip() for part in piece.split(" as ")]
                if parts and parts[-1] and parts[0] in JS_LINK_NAMES:
                    imported.add(parts[-1])
        for match in JS_FS_REQUIRE.finditer(text):
            namespaces_part, default, named, source = match.groups()
            if source not in JS_FS_MODULES:
                continue
            if namespaces_part:
                namespaces.add(namespaces_part)
            if default:
                namespaces.add(default)
            for piece in (named or "").split(","):
                parts = [part.strip() for part in piece.split(":")]
                if parts and parts[-1] and parts[0] in JS_LINK_NAMES:
                    imported.add(parts[-1])
        return imported, namespaces

    @staticmethod
    def js_sites(line, lineno, imported, namespaces):
        """(line, what) for one masked JS/TS line."""
        sites = []
        for name in sorted(imported):
            if re.search(r"(?<![\w$.])" + re.escape(name) + r"\s*\(", line):
                sites.append((lineno, f"{name} creates a link"))
        for namespace in sorted(namespaces):
            pattern = (r"(?<![\w$])" + re.escape(namespace)
                       + r"\s*(?:\.\s*promises)?\s*\.\s*(symlinkSync|symlink)\s*\(")
            match = re.search(pattern, line)
            if match:
                sites.append((lineno, f"{namespace}.{match.group(1)} creates a link"))
        return sites

    @staticmethod
    def shell_sites(line, lineno=None):
        """(line, what) for one masked shell or PowerShell command line."""
        sites = []
        for command in SHELL_SPLIT.split(line):
            words = command.split()
            while words and words[0] in ("sudo", "env"):
                words = words[1:]
            if not words:
                continue
            if words[0] in SHELL_SHELLS and len(words) > 1:
                # A shell wrapper: read the command it wraps, whether the
                # rest is quoted (its own candidate) or bare.
                for index, word in enumerate(words[1:], 1):
                    if word.lower() in SHELL_COMMAND_FLAGS:
                        sites.extend(LinkCreationGuard.shell_sites(
                            " ".join(words[index + 1:]), lineno))
                        break
                continue
            name = os.path.basename(words[0]).lower()
            if name in ("ln", "ln.exe"):
                flags = [word for word in words[1:] if word.startswith("-")]
                if any("s" in flag for flag in flags) \
                        or any(flag == "--symbolic" for flag in flags):
                    sites.append((lineno, "ln with a symbolic flag creates a link"))
            elif name in ("mklink", "mklink.exe"):
                sites.append((lineno, "mklink creates a link"))
            elif name in ("new-item", "ni") and SHELL_ITEMTYPE.search(command):
                sites.append((lineno, "a PowerShell reparse point is created"))
        return sites

    @staticmethod
    def masked(raw, shell):
        """The line with its comments removed and its strings blanked."""
        out = []
        index = 0
        quote = None
        while index < len(raw):
            char = raw[index]
            nxt = raw[index + 1] if index + 1 < len(raw) else ""
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
                out.append(" ")
                index += 1
                continue
            if char in ("\"", "'", "`"):
                quote = char
                out.append(" ")
                index += 1
                continue
            if char == "/" and nxt == "/":
                break
            if char == "/" and nxt == "*":
                end = raw.find("*/", index + 2)
                if end == -1:
                    break
                out.append(" ")
                index = end + 2
                continue
            if shell and char == "#" and (not out or out[-1].isspace()):
                break
            out.append(char)
            index += 1
        return "".join(out)

    @staticmethod
    def quoted(raw):
        """The string literals of a line: a command may hide in one."""
        return [left or right for left, right in LITERAL.findall(raw)]

    @staticmethod
    def self_test():
        """Detector cases in memory: no file, no scratch, and no link."""
        python = LinkCreationGuard.python_sites
        js = lambda text: LinkCreationGuard.js_sites(  # noqa: E731
            LinkCreationGuard.masked(text, False), 1,
            *LinkCreationGuard.js_names(text))
        script = lambda text: LinkCreationGuard.shell_sites(  # noqa: E731
            text)
        cases = [
            (lambda text: python(text),
             "import os\nos.symlink(a, b)\n", 1, "os.symlink is found"),
            (lambda text: python(text),
             "import os as operating\noperating.symlink(a, b)\n", 1,
             "an aliased module is found"),
            (lambda text: python(text),
             "from os import symlink as link\nlink(a, b)\n", 1,
             "an aliased import is found"),
            (lambda text: python(text),
             "from pathlib import Path\nPath(b).symlink_to(a)\n", 1,
             "Path.symlink_to is found"),
            (lambda text: python(text),
             "import subprocess\nsubprocess.run(['ln', '-sfn', 'a', 'b'])\n", 1,
             "a subprocess command is found"),
            (lambda text: python(text),
             "import os\nos.system('ln -s a b')\n", 1,
             "an os.system command is found"),
            (lambda text: python(text),
             "subprocess.run(['git', 'status'])\n", 0,
             "an ordinary command is silent"),
            (lambda text: python(text),
             "# os.symlink(a, b)\nmessage = 'a symlink is refused'\n", 0,
             "a comment and a message are silent"),
            (lambda text: python(text),
             "mock.patch.object(Path, 'symlink_to', side_effect=refuse)\n", 0,
             "refusing a link is not creating one"),
            (lambda text: python(text),
             "def broken(:\n", 1, "an unparsable source is a finding"),
            (lambda text: js(text),
             "import { symlinkSync } from 'node:fs';\nsymlinkSync(a, b);\n", 1,
             "symlinkSync is found"),
            (lambda text: js(text),
             "import { symlinkSync as makeLink } from 'node:fs';\nmakeLink(a, b);\n",
             1, "a renamed import is found"),
            (lambda text: js(text),
             "import * as fs from 'node:fs';\nfs.symlink(a, b);\n", 1,
             "an fs namespace is found"),
            (lambda text: js(text),
             "import * as io from 'node:fs';\nio.promises.symlink(a, b);\n", 1,
             "fs.promises.symlink is found"),
            (lambda text: js(text),
             "const fs = require('node:fs');\nfs.symlinkSync(a, b);\n", 1,
             "a require'd fs namespace is found"),
            (lambda text: js(text),
             "const message = 'symlinkSync(a, b)';\n", 0,
             "a JavaScript string is silent"),
            (lambda text: js(text),
             "// symlinkSync(a, b) is forbidden here\n", 0,
             "a comment about a link is silent"),
            (lambda text: js(text),
             "const value = 1; /* symlinkSync(a, b) */\n", 0,
             "an inline block comment is silent"),
            (lambda text: script(text),
             "ln -sfn target link\n", 1, "a combined flag cluster is found"),
            (lambda text: script(text),
             "ln -s target link\n", 1, "a separate -s is found"),
            (lambda text: script(text),
             "ln --symbolic target link\n", 1, "the long flag is found"),
            (lambda text: script(text),
             "ln target link\n", 0, "a plain ln is not a link creation"),
            (lambda text: script(text),
             "grep --symbolic file\n", 0, "the long flag outside ln is silent"),
            (lambda text: script(text),
             "# ln -s target link\n", 0, "a shell comment is silent"),
            (lambda text: script(text),
             "mklink link target\n", 1, "mklink is found"),
            (lambda text: script(text),
             "cmd //c mklink link target\n", 1, "a wrapped mklink is found"),
            (lambda text: script(text),
             "New-Item -ItemType SymbolicLink -Path link\n", 1,
             "a PowerShell symbolic item is found"),
            (lambda text: script(text),
             "echo 'ln -s a b' > notes.txt\n", 0,
             "a quoted example is silent on its own"),
            (lambda text: script(LinkCreationGuard.quoted(text)[0]),
             'bash -c "ln -s a b"\n', 1, "a hidden shell command is found"),
        ]
        problems = []
        for detect, text, expected, label in cases:
            try:
                count = len(detect(text))
            except Exception as failure:  # a detector that raises proves nothing
                problems.append(f"self-test: {label}: raised {failure!r}")
                continue
            if count != expected:
                problems.append(
                    f"self-test: {label}: {count} findings, expected {expected}")
        return problems


def root_of():
    """The repository root this file belongs to: its tests/ parent."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else root_of()
    problems = LinkCreationGuard.self_test()
    for problem in problems:
        print(f"link guard: {problem}", file=sys.stderr)
    findings = LinkCreationGuard(root).check()
    for finding in findings:
        print(f"link guard: {finding}", file=sys.stderr)
    if findings or problems:
        return 1
    print("link guard: no test source creates a link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
