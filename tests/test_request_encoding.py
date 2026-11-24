#!/usr/bin/env python3
"""`read_event`: the hook decodes its request as UTF-8, whatever the console says."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "root-memory-context.py"


def read_event(payload: bytes) -> dict:
    spec = importlib.util.spec_from_file_location("root_memory_context", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252")
    with mock.patch.object(sys, "stdin", stream):
        return module.read_event()


class RequestEncodingTests(unittest.TestCase):
    """A multi-byte memory body must arrive as written."""

    def test_a_multibyte_body_survives_a_non_utf8_console(self) -> None:
        event = {"body": "a band of 70–85% and p50 15,195 µs"}
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        read = read_event(payload)
        self.assertEqual(read.get("body"), event["body"])
        self.assertNotIn("Â", read.get("body", ""))

    def test_an_ascii_request_is_unchanged(self) -> None:
        self.assertEqual(
            read_event(json.dumps({"operation": "commit"}).encode("utf-8")),
            {"operation": "commit"},
        )

    def test_a_malformed_request_reads_as_empty(self) -> None:
        self.assertEqual(read_event(b"{not json"), {})

    def test_an_empty_request_reads_as_empty(self) -> None:
        self.assertEqual(read_event(b""), {})


if __name__ == "__main__":
    unittest.main()
