#!/usr/bin/env python3
"""The judge's default command: run the originating host's own CLI.

One purpose: deliver the judge's request to the CLI the current harness is
already configured with, and print that CLI's answer unchanged. The judge
remains the only validator, so nothing here repairs, trims, or reinterprets
what the model wrote.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rm_judge_settings import JudgeError, JudgePolicy  # noqa: E402

INSTRUCTIONS = """\
Convention review. The input is one JSON object with these fields: schema,
chain, turns (an array of strings), rules (an array of [path, text]).

Judge every turn against the rules. Answer with no other text:
line 1: the chain value, character for character.
Then either the single word: pass
or one finding per line, each exactly:
<rule number><space><turn number><space><quoted evidence>
The two numbers are 1-based indices into rules and turns. The evidence is a
JSON string that occurs exactly once in that turn, and it is the smallest span
that shows the violation. No blank lines, no code fences, no explanation.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    return parser.parse_args()


def prompt(payload: str) -> str:
    return f"{INSTRUCTIONS}\n{payload}\n"


def run(descriptor: dict[str, Any], text: str, capture: int, cwd: Path) -> tuple[int, str]:
    """Run one host CLI, returning its exit code and bounded captured output."""
    argv = list(descriptor["command"])
    stdin_text = text
    with tempfile.TemporaryDirectory(prefix="judge-") as scratch:
        answer_file = None
        if descriptor["answer"] == "file":
            answer_file = str(Path(scratch) / "answer.txt")
            argv += ["-o", answer_file]
        if descriptor["prompt"] == "argv":
            argv.append(text)
            stdin_text = ""
        try:
            completed = subprocess.run(
                argv,
                input=stdin_text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(cwd),
                shell=False,
            )
        except OSError:
            return 127, ""
        if completed.returncode != 0:
            return completed.returncode, ""
        if answer_file is None:
            return 0, completed.stdout[:capture].decode("utf-8", "replace")
        try:
            with open(answer_file, "rb") as handle:
                return 0, handle.read(capture).decode("utf-8", "replace")
        except OSError:
            return 1, ""


def main() -> int:
    args = parse_args()
    payload = sys.stdin.read()
    if not payload:
        return 2
    try:
        policy = JudgePolicy.load()
        descriptor = policy.host(args.agent)
        capture = policy.capture_bytes()
    except JudgeError:
        return 2
    code, answer = run(descriptor, prompt(payload), capture, Path.cwd())
    if code != 0:
        return code
    sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
