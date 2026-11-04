#!/usr/bin/env python3
"""The judge's default command: run the originating host's own CLI.

One purpose: deliver the judge's request to the CLI the current harness is
already configured with, and print that CLI's answer unchanged. The judge
remains the only validator, so nothing here repairs, trims, or reinterprets
what the model wrote.

The launch descriptor arrives from the hook in `AMS_JUDGE_LAUNCH`, because the
hook has already validated it against the effective settings, host override
included. Reading the shipped policy again here would silently ignore a user's
`hosts.<host>.command`, which is the only way to change what runs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rm_judge_settings import JudgeError, JudgePolicy  # noqa: E402

# What the hook turns into a refusal code rather than `command_failed`.
OVERFLOW = 3
UNDECODABLE = 4
READ_CHUNK = 65536
INSTRUCTIONS = """\
Convention review. The input is one JSON object with these fields: schema,
chain, turns (an array of strings), rules (an array of [path, text]).

Judge every turn against the rules. Answer with no other text:
line 1: the chain value, character for character.
Then either the single word: pass
or one finding per line, exactly:
<rule number><space><turn number><space><quoted evidence>
The two numbers are 1-based indices into rules and turns. Findings must be in
strictly ascending order by those two numbers. The evidence is a JSON string
that occurs exactly once in that turn, and it is the smallest span that shows
the violation. No blank lines, no code fences, no trailing spaces, no
explanation.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    return parser.parse_args()


def descriptor_from_environment() -> dict[str, Any] | None:
    """The hook's validated launch descriptor, or None when it sent none."""
    raw = os.environ.get("AMS_JUDGE_LAUNCH")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        raise JudgeError("invalid_config") from None
    if not isinstance(value, dict) or set(value) != {"command", "prompt", "answer"}:
        raise JudgeError("invalid_config")
    command = value["command"]
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) for part in command
    ):
        raise JudgeError("invalid_config")
    if value["prompt"] not in ("stdin", "argv") or value["answer"] not in ("stdout", "file"):
        raise JudgeError("invalid_config")
    return value


def prompt(payload: str) -> str:
    return f"{INSTRUCTIONS}\n{payload}\n"


def bounded(stream: Any, limit: int) -> bytes | None:
    """Read output up to one byte past the limit; None when it overflowed.

    Reading everything and slicing afterwards would let a flooding command
    decide how much memory this process holds, so the bound applies while
    reading and the extra byte is what proves the overflow.
    """
    collected = bytearray()
    while True:
        chunk = stream.read(READ_CHUNK)
        if not chunk:
            return bytes(collected)
        collected.extend(chunk)
        if len(collected) > limit:
            return None


def run(descriptor: dict[str, Any], text: str, limit: int, cwd: Path) -> tuple[int, bytes]:
    """Run one host CLI, returning its exit code and bounded captured output.

    The CLI is left in this process's group on purpose: the hook enforces the
    review deadline by killing the group it started, so a child that began its
    own session would outlive the deadline it was given and keep billing for an
    answer nobody will read.
    """
    argv = list(descriptor["command"])
    stdin_text = text.encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="judge-") as scratch:
        answer_file = None
        if descriptor["answer"] == "file":
            answer_file = Path(scratch) / "answer.txt"
            argv += ["-o", str(answer_file)]
        if descriptor["prompt"] == "argv":
            argv.append(text)
            stdin_text = b""
        try:
            completed = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE if answer_file is None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(cwd),
                shell=False,
            )
        except OSError:
            return 127, b""

        def feed() -> None:
            try:
                completed.stdin.write(stdin_text)
                completed.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        output = b""
        if answer_file is None:
            captured = bounded(completed.stdout, limit)
            if captured is None:
                # Stop reading a flood rather than waiting for it to end.
                completed.kill()
                completed.wait()
                return OVERFLOW, b""
            output = captured
        completed.wait()
        if completed.returncode != 0:
            return completed.returncode, b""
        if answer_file is not None:
            try:
                with open(answer_file, "rb") as handle:
                    captured = bounded(handle, limit)
            except OSError:
                return 1, b""
            if captured is None:
                return OVERFLOW, b""
            output = captured
        return 0, output


def main() -> int:
    args = parse_args()
    payload = sys.stdin.read()
    if not payload:
        return 2
    try:
        descriptor = descriptor_from_environment()
        limit = JudgePolicy.load().capture_bytes()
        if descriptor is None:
            descriptor = JudgePolicy.load().host(args.agent)
    except JudgeError:
        return 2
    code, answer = run(descriptor, prompt(payload), limit, Path.cwd())
    if code != 0:
        return code
    try:
        answer.decode("utf-8")
    except UnicodeDecodeError:
        # A command that answered with bytes no text protocol can carry is a
        # result the judge must refuse, not one it repairs into acceptance.
        return UNDECODABLE
    # The judge reads this answer verbatim, so text-mode newline translation
    # here would turn every line into a carriage return it refuses.
    sys.stdout.buffer.write(answer)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
