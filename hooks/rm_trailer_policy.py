"""Which trailer tokens a commit message may carry.

Parsing is git's job; this answers only whether a token the parser found is one
the conventions name, so allowing a new trailer is a data edit in
`trailer-policy.json` rather than a code change and a release.
"""
from __future__ import annotations

import json
from pathlib import Path

POLICY_PATH = Path(__file__).with_name("trailer-policy.json")


class TrailerPolicy:
    """The allowed trailer tokens, loaded from the shipped policy file."""

    def __init__(self, tokens: frozenset[str]) -> None:
        self.tokens = tokens

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> "TrailerPolicy":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(frozenset(str(token) for token in data["trailers"]))

    def allows(self, token: str) -> bool:
        return token in self.tokens
