"""The convention judge's settings.

One owner for the judge's configuration: the shipped policy that names its
defaults, the effective settings document, and the transactional edits
`/judge-settings` performs on it. The document is user-owned - the installers
never create, replace, or remove it - so a missing file is the shipped
default, not a refusal.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from manage_common import EVENT_TIMEOUTS
from rm_control import RootState
from rm_support import read_text

POLICY_NAME = "judge-policy.json"
SCHEMA = 2
HOSTS = ("pi", "claude", "codex")
# Every key a settings document may carry beyond `schema` and `hosts`.
POLICY_KEYS = (
    "enabled", "command", "protocol", "timeout_s", "max_turns",
    "max_input_bytes", "max_output_bytes",
)
NUMERIC_KEYS = ("timeout_s", "max_turns", "max_input_bytes", "max_output_bytes")
# The keys whose value is a duration: a deadline or a wait can hold a fraction,
# while a count of turns, bytes, or entries cannot.
FRACTIONAL_KEYS = frozenset({"timeout_s", "lock_timeout_s", "lock_poll_s",
                             "review_teardown_s"})
HOST_KEYS = ("command", "extra_env")
# A pre-schema document: exactly these five, all numbers or an argv.
LEGACY_KEYS = frozenset({"command", *NUMERIC_KEYS})
# What `reset` may remove: the numeric overrides, never the authorization.
RESET_KEEPS = frozenset({"schema", "enabled", "command", "protocol", "hosts"})
# How long a settings edit waits for its lock, and how often it retries, are
# policy values like every other bound here: `lock_timeout_s` and `lock_poll_s`
# in the policy document, never literals beside the loop that uses them.
# A review deadline has to fit inside the deadline the host installs for the
# event that runs it, or the host kills the hook before its own timeout can
# report anything. Neither number is restated here: the installed bound comes
# from `manage_common`, the margin from the policy document, which owns the
# room the receipt write, the notice, and process teardown need.


class JudgeError(Exception):
    """A local refusal code, never command-authored text."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object that refuses a repeated key instead of keeping the last."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key: {key}")
        seen.add(key)
    return dict(pairs)


def argv(value: Any) -> list[str]:
    """A nonempty list of nonempty strings, or a refusal."""
    if not isinstance(value, list) or not value:
        raise JudgeError("invalid_config")
    if not all(isinstance(part, str) and part for part in value):
        raise JudgeError("invalid_config")
    return list(value)


def launch_argv(value: Any) -> list[str]:
    """A shipped launch argv, where an empty element can be a real argument.

    A CLI that spells "no tools" as `--tools ""` needs the empty element, so
    the shipped descriptors accept one while a user-set `command` may not.
    """
    if not isinstance(value, list) or not value:
        raise JudgeError("invalid_config")
    if not all(isinstance(part, str) for part in value):
        raise JudgeError("invalid_config")
    return list(value)


def names(value: Any) -> list[str]:
    """A list of environment variable names, empty allowed."""
    if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
        raise JudgeError("invalid_config")
    return list(value)


def number(key: str, value: Any, *, maximum: float | None = None) -> int | float:
    """A positive finite representable number, whole except for the timing keys."""
    integer = key not in FRACTIONAL_KEYS
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        raise JudgeError("invalid_config")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        # An integer beyond the float range is finite in neither sense.
        raise JudgeError("invalid_config") from None
    if not finite or not value > 0:
        raise JudgeError("invalid_config")
    if integer and value > sys.maxsize:
        raise JudgeError("invalid_config")
    if maximum is not None and value > maximum:
        # A finite number is not automatically one the waiter or the host
        # timer can hold; refuse it here instead of failing after a spawn.
        raise JudgeError("invalid_config")
    return value


def protocol(value: Any) -> int:
    """A wire protocol this judge implements."""
    if isinstance(value, bool) or value not in (1, 2):
        raise JudgeError("invalid_config")
    return int(value)


def host_name(state: RootState) -> str:
    """The host this hook runs for, refused when the policy does not know it."""
    if state.agent not in HOSTS:
        raise JudgeError("unsupported_host")
    return state.agent


class JudgePolicy:
    """The shipped policy: defaults, the settings path, and the host launches."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    @classmethod
    def load(cls) -> "JudgePolicy":
        """Read the policy beside this module; a broken one is a local refusal."""
        text, error = read_text(Path(__file__).resolve().parent / POLICY_NAME)
        if error or text is None:
            raise JudgeError("invalid_config")
        try:
            document = json.loads(text, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_config") from None
        if not isinstance(document, dict) or set(document) != {
            "schema", "settings", "adapter", "capture_bytes", "review_teardown_s",
            "lock_timeout_s", "lock_poll_s", "defaults", "hosts"
        }:
            raise JudgeError("invalid_config")
        return cls(document)

    def settings_path(self) -> Path:
        """The one document every host reads, from an OS user-home template."""
        template = self.document["settings"]
        if not isinstance(template, str) or "{user_home}" not in template:
            raise JudgeError("invalid_config")
        return Path(template.replace("{user_home}", str(Path.home()))).expanduser()

    def adapter(self) -> Path:
        name = self.document["adapter"]
        if not isinstance(name, str) or not name:
            raise JudgeError("invalid_config")
        return Path(__file__).resolve().parent / name

    def teardown_margin(self) -> float:
        """The seconds a review leaves for the receipt write and teardown."""
        return float(number("review_teardown_s", self.document["review_teardown_s"]))

    def deadline_ceiling(self) -> float:
        """The largest review deadline the installed event bound allows.

        It is derived, never restated: the host's own `Stop` budget less the
        margin this document sets, and never more than the platform timers hold.
        A margin that leaves no room is a policy error, not a deadline of zero.
        """
        allowance = EVENT_TIMEOUTS["Stop"] - self.teardown_margin()
        if allowance <= 0:
            raise JudgeError("invalid_config")
        return min(allowance, threading.TIMEOUT_MAX, (2 ** 31 - 1) / 1000)

    def lock_timeout(self) -> float:
        """How long one settings edit may wait for its lock."""
        return float(number("lock_timeout_s", self.document["lock_timeout_s"]))

    def lock_poll(self) -> float:
        """How often a waiting settings edit retries its lock."""
        return float(number("lock_poll_s", self.document["lock_poll_s"],
                            maximum=self.lock_timeout()))

    def capture_bytes(self) -> int:
        """How much of a CLI's own output the adapter may hold before parsing."""
        return int(number("capture_bytes", self.document["capture_bytes"]))

    def defaults(self) -> dict[str, Any]:
        """The shipped values, validated exactly like a document's own."""
        raw = self.document["defaults"]
        if not isinstance(raw, dict) or set(raw) != {"protocol", "baseline_entries", *NUMERIC_KEYS}:
            raise JudgeError("invalid_config")
        return {
            "protocol": protocol(raw["protocol"]),
            "baseline_entries": number("baseline_entries", raw["baseline_entries"]),
            **{key: number(key, raw[key],
                           maximum=self.deadline_ceiling() if key == "timeout_s" else None)
               for key in NUMERIC_KEYS},
        }

    def host(self, name: str) -> dict[str, Any]:
        """One host's launch descriptor: argv, prompt delivery, and answer source."""
        hosts = self.document["hosts"]
        entry = hosts.get(name) if isinstance(hosts, dict) else None
        if not isinstance(entry, dict):
            raise JudgeError("unsupported_host")
        return self.descriptor(name, entry)

    @classmethod
    def descriptor(cls, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        """A launch descriptor, refused unless every field is one we implement."""
        if set(entry) != {"command", "prompt", "answer", "extra_env"}:
            raise JudgeError("invalid_config")
        if entry["prompt"] not in ("stdin", "argv") or entry["answer"] not in ("stdout", "file"):
            raise JudgeError("invalid_config")
        return {
            "name": name,
            "command": launch_argv(entry["command"]),
            "prompt": entry["prompt"],
            "answer": entry["answer"],
            "extra_env": names(entry["extra_env"]),
        }


class JudgeSettings:
    """The effective judge configuration and the only writer of its document."""

    def __init__(self, state: RootState, policy: JudgePolicy | None = None) -> None:
        self.state = state
        self.policy = policy or JudgePolicy.load()

    def path(self) -> Path:
        return self.policy.settings_path()

    def legacy_path(self) -> Path:
        """Where this host's pre-schema document would sit, if one was ever made."""
        return self.state.home / ".agent-mem-struct" / "judge.json"

    def read(self) -> dict[str, Any]:
        """The effective configuration; a missing document is the shipped default."""
        path = self.path()
        if not path.exists() and not path.is_symlink():
            return {
                **self.policy.defaults(), "enabled": True, "command": None, "hosts": {},
                "revision": "", "path": str(path), "source": "default", "stated": [],
            }
        text, error = read_text(path) if path.is_file() else (None, "not a file")
        if error or text is None:
            raise JudgeError("invalid_config")
        try:
            document = json.loads(text, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_config") from None
        # An omitted policy key inherits the shipped default; only what the
        # document states can override it.
        config = {**self.policy.defaults(), **self.validate(document)}
        config["revision"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        config["path"] = str(path)
        config["source"] = "file"
        config["stated"] = self._stated(document)
        return config

    @staticmethod
    def _stated(document: dict[str, Any]) -> list[str]:
        """Every key the document states, so a value equal to its default still
        reads as explicit. Provenance cannot be recovered by comparing values."""
        stated = [key for key in POLICY_KEYS if key in document]
        hosts = document.get("hosts")
        if isinstance(hosts, dict):
            for name, entry in hosts.items():
                if isinstance(entry, dict):
                    stated.extend(f"hosts.{name}.{key}" for key in sorted(entry))
        return stated

    @classmethod
    def validate(cls, document: Any) -> dict[str, Any]:
        """The whole-document validation: a legacy shape normalizes to schema 2."""
        if not isinstance(document, dict):
            raise JudgeError("invalid_config")
        if "schema" not in document:
            # A pre-schema document keeps every value it holds and its wire
            # protocol, so an upgrade never changes what a command receives.
            if set(document) != LEGACY_KEYS:
                raise JudgeError("invalid_config")
            command = document["command"]
            return {
                "enabled": True,
                "command": None if command is None else argv(command),
                "protocol": 1,
                **{key: number(key, document[key],
                               maximum=JudgePolicy.load().deadline_ceiling()
                               if key == "timeout_s" else None)
                   for key in NUMERIC_KEYS},
                "hosts": {},
            }
        if isinstance(document["schema"], bool) or document["schema"] != SCHEMA:
            raise JudgeError("invalid_config")
        if set(document) - set(POLICY_KEYS) - {"schema", "hosts"}:
            raise JudgeError("invalid_config")
        enabled = document.get("enabled", True)
        if not isinstance(enabled, bool):
            raise JudgeError("invalid_config")
        command = document.get("command")
        hosts = document.get("hosts", {})
        if not isinstance(hosts, dict) or not set(hosts) <= set(HOSTS):
            raise JudgeError("invalid_config")
        return {
            "enabled": enabled,
            "command": None if command is None else argv(command),
            **({"protocol": protocol(document["protocol"])} if "protocol" in document else {}),
            **{key: number(key, document[key],
                           maximum=JudgePolicy.load().deadline_ceiling()
                           if key == "timeout_s" else None)
               for key in NUMERIC_KEYS if key in document},
            "hosts": {name: JudgeSettings._override(entry) for name, entry in hosts.items()},
        }

    @staticmethod
    def _override(entry: Any) -> dict[str, Any]:
        """One host's stored overrides: its own argv and its extra env names."""
        if not isinstance(entry, dict) or not set(entry) <= set(HOST_KEYS):
            raise JudgeError("invalid_config")
        override: dict[str, Any] = {}
        if "command" in entry:
            override["command"] = argv(entry["command"])
        if "extra_env" in entry:
            override["extra_env"] = names(entry["extra_env"])
        return override

    # -- launch resolution -------------------------------------------------

    def launch(self, config: dict[str, Any]) -> dict[str, Any]:
        """The argv one review runs: an explicit command, else the shipped adapter."""
        host = host_name(self.state)
        if config["command"] is not None:
            return {"argv": list(config["command"]), "host": host, "explicit": True,
                    "extra_env": []}
        override = config["hosts"].get(host) or {}
        descriptor = self.policy.host(host)
        # A host override states the whole list: forwarding cannot be narrowed
        # by addition, so `extra_env: []` has to mean "forward nothing" rather
        # than "keep the shipped credentials".
        extra_env = override.get("extra_env")
        return {
            "argv": [sys.executable, str(self.policy.adapter()), "--agent", host],
            "host": host,
            "explicit": False,
            "host_command": override.get("command") or descriptor["command"],
            "prompt": descriptor["prompt"],
            "answer": descriptor["answer"],
            "extra_env": list(descriptor["extra_env"] if extra_env is None else extra_env),
        }

    # -- edits -------------------------------------------------------------

    def apply(self, operation: str, revision: str, key: str | None, value: str | None) -> None:
        """Perform one settings edit under the lock, or refuse with a local code."""
        # Validate the request itself before touching the document, so a bad
        # key or value never reads as a stale revision.
        prepared = self._prepare(operation, key, value)
        with self._locked():
            self.compare(revision)
            document = self._document()
            if operation == "reset":
                if document is not None:
                    self._replace({k: v for k, v in document.items() if k in RESET_KEEPS})
                return
            if operation == "import":
                self._import_legacy(document)
                return
            document = document or {"schema": SCHEMA}
            if operation == "unset":
                self._remove(document, key or "")
            else:
                self._assign(document, key or "", prepared)
            self._replace(document)

    def _prepare(self, operation: str, key: str | None, value: str | None) -> Any:
        """The parsed value one edit applies, or the refusal for its request."""
        if operation in ("reset", "import"):
            return None
        if operation not in ("set", "unset") or key is None or self._split(key) is None:
            raise JudgeError("invalid_settings_request")
        return self._value(key, value) if operation == "set" else None

    def compare(self, revision: str) -> None:
        """Refuse an edit composed against a document that has since changed."""
        current = self._stamp()
        if revision == current:
            return
        raise JudgeError("stale_config")

    def _stamp(self) -> str:
        text, error = read_text(self.path())
        return "" if error or text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _document(self) -> dict[str, Any] | None:
        """The stored document as schema 2, or None when there is none to edit."""
        path = self.path()
        if not path.exists() and not path.is_symlink():
            return None
        text, error = read_text(path) if path.is_file() else (None, "not a file")
        if error or text is None:
            raise JudgeError("invalid_config")
        try:
            document = json.loads(text, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_config") from None
        # An edit may never repair a document it cannot first validate: the
        # values it would keep are unknown, so writing would destroy them.
        config = self.validate(document)
        if "schema" in document:
            return document
        return {"schema": SCHEMA, "enabled": True, "command": config["command"],
                "protocol": config["protocol"],
                **{key: config[key] for key in NUMERIC_KEYS}}

    def _effective(self, config: dict[str, Any]) -> dict[str, Any]:
        """A validated document with the shipped defaults filled in.

        `validate` is sparse by design - it returns only what the document
        states - so provenance and comparison both need the effective value of
        every key, and a sparse document is the default for what it leaves out.
        """
        return {**self.policy.defaults(), "enabled": True, "command": None, **config}

    def _import_legacy(self, document: dict[str, Any] | None) -> None:
        """Copy this host's pre-schema document into the shared owner, once."""
        legacy = self.legacy_path()
        if legacy.resolve(strict=False) == self.path().resolve(strict=False):
            raise JudgeError("invalid_settings_request")
        text, error = read_text(legacy)
        if error or text is None:
            raise JudgeError("invalid_settings_request")
        try:
            # Both sides are filled from the shipped defaults, because either
            # document may state only a few keys: a sparse document is the
            # default for everything it leaves out.
            imported = self._effective(self.validate(json.loads(text, object_pairs_hook=unique_object)))
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_settings_request") from None
        if document is not None:
            current = self._effective(document)
            if any(current[key] != imported[key] for key in LEGACY_KEYS):
                raise JudgeError("settings_conflict")
            return
        self._replace({"schema": SCHEMA, "enabled": True, "command": imported["command"],
                       "protocol": imported["protocol"],
                       **{key: imported[key] for key in NUMERIC_KEYS}})

    @staticmethod
    def _split(key: str) -> tuple[str, ...] | None:
        """A settable key path, or None when the key is not the judge's to set."""
        if key in POLICY_KEYS:
            return (key,)
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "hosts" and parts[1] in HOSTS and parts[2] in HOST_KEYS:
            return tuple(parts)
        return None

    def _value(self, key: str, value: str | None) -> Any:
        path = self._split(key)
        if path is None:
            raise JudgeError("invalid_settings_request")
        leaf = path[-1]
        try:
            if leaf == "enabled":
                return self._flag(value)
            if leaf == "protocol":
                return protocol(self._integer(value))
            if leaf == "command":
                return None if value is None or value.strip() == "null" else argv(self._json(value))
            if leaf == "extra_env":
                return names(self._json(value))
            if leaf == "timeout_s":
                return number(leaf, self._decimal(value),
                              maximum=self.policy.deadline_ceiling())
            return number(leaf, self._integer(value))
        except JudgeError as refusal:
            raise JudgeError("invalid_settings_request") from refusal

    @staticmethod
    def _flag(value: str | None) -> bool:
        text = (value or "").strip().lower()
        if text in ("on", "true", "yes", "1"):
            return True
        if text in ("off", "false", "no", "0"):
            return False
        raise JudgeError("invalid_settings_request")

    @staticmethod
    def _decimal(value: str | None) -> float:
        """A positive decimal, as the document itself may hold for a deadline."""
        text = (value or "").strip()
        try:
            parsed = float(text)
        except ValueError:
            raise JudgeError("invalid_settings_request") from None
        if not text or not math.isfinite(parsed):
            raise JudgeError("invalid_settings_request")
        return parsed

    @staticmethod
    def _integer(value: str | None) -> int:
        text = (value or "").strip()
        if not text.isdigit():
            raise JudgeError("invalid_settings_request")
        return int(text)

    @staticmethod
    def _json(value: str | None) -> Any:
        try:
            return json.loads((value or "").strip(), object_pairs_hook=unique_object)
        except (json.JSONDecodeError, ValueError):
            raise JudgeError("invalid_settings_request") from None

    def _assign(self, document: dict[str, Any], key: str, value: Any) -> None:
        path = self._split(key)
        if path is None:
            raise JudgeError("invalid_settings_request")
        if len(path) == 1:
            document[path[0]] = value
            return
        hosts = document.setdefault("hosts", {})
        if not isinstance(hosts, dict):
            raise JudgeError("invalid_config")
        entry = hosts.setdefault(path[1], {})
        if not isinstance(entry, dict):
            raise JudgeError("invalid_config")
        entry[path[2]] = value

    def _remove(self, document: dict[str, Any], key: str) -> None:
        path = self._split(key)
        if path is None:
            raise JudgeError("invalid_settings_request")
        if len(path) == 1:
            document.pop(path[0], None)
            return
        hosts = document.get("hosts")
        entry = hosts.get(path[1]) if isinstance(hosts, dict) else None
        if isinstance(entry, dict):
            entry.pop(path[2], None)
            if not entry:
                hosts.pop(path[1])

    def _replace(self, document: dict[str, Any]) -> None:
        """Validate, then replace the document atomically under the lock."""
        self.validate(document)
        path = self.path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
        except OSError:
            raise JudgeError("settings_write_failed") from None
        temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(document, sort_keys=True, indent=2) + "\n")
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise JudgeError("settings_write_failed") from None

    def _locked(self) -> "_Lock":
        return _Lock(self.path().with_name(self.path().name + ".lock"),
                     self.policy.lock_timeout(), self.policy.lock_poll())


class _Lock:
    """A nonblocking OS lock on a stable sibling file that is never unlinked.

    A lock file removed after unlocking lets a competing writer lock a fresh
    inode while the previous holder still holds the old one.
    """

    def __init__(self, path: Path, timeout: float, poll: float) -> None:
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self.handle: Any = None
        self.windows = False

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + self.timeout
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.handle = open(self.path, "a+b")
        except OSError:
            raise JudgeError("settings_busy") from None
        while not self._acquire():
            if time.monotonic() >= deadline:
                self.handle.close()
                raise JudgeError("settings_busy")
            time.sleep(self.poll)
        return self

    def _acquire(self) -> bool:
        try:
            import fcntl
        except ImportError:
            self.windows = True
            return self._acquire_windows()
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _acquire_windows(self) -> bool:
        try:
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def __exit__(self, *_exc: Any) -> None:
        try:
            if self.windows:
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self.handle.close()
