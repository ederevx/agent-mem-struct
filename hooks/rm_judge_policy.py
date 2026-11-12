"""Resolve the memory judge's launch from its policy and the user's document.

One owner for the judge's configuration: `JudgePolicy` reads the shipped
`judge-policy.json`, optionally merges one user-owned settings document, and
returns a `JudgeLaunch` - the exact argv, child environment, configured
provider and model, and the five resource limits - or raises `JudgeError` with
the stage and the field that refused it.

Nothing here is a model, provider, endpoint, path, timeout or size the code
knows by itself: every value comes from the shipped policy, from the user's
document, from the environment the host launched the hook with, or from the
host's own settings document. The judge's answer is a decision, so every
refusal is fail-closed: an unreadable, duplicated, unknown, or out-of-range
value never falls back to a default that would let a write through.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

POLICY_FILE = "judge-policy.json"
POLICY_SCHEMA = 2
SETTINGS_SCHEMA = 3
ENV_POLICY = "AMS_JUDGE_POLICY"
ENV_SETTINGS = "AMS_JUDGE_SETTINGS"
LIMIT_KEYS = (
    "timeout_s",
    "max_input_bytes",
    "capture_bytes",
    "teardown_s",
    "completion_reserve_s",
)
INTEGER_LIMITS = ("max_input_bytes", "capture_bytes")
LAUNCHER_KEYS = (
    "command",
    "arguments",
    "extension_flag",
    "provider_flag",
    "model_flag",
    "prompt",
    "answer",
    "extensions",
)
PLACEHOLDERS = ("{agent_home}", "{pi_agent_dir}")
TRANSPORTS = {"prompt": "stdin", "answer": "stdout"}

CONFIG = "config"


class JudgeError(Exception):
    """A refusal to judge, with the stage it reached and the field at fault."""

    def __init__(self, code: str, stage: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.stage = stage
        self.detail = detail

    def text(self) -> str:
        return self.detail


class JudgeLaunch:
    """One fully resolved judge invocation."""

    def __init__(
        self,
        argv: list[str],
        environment: dict[str, str],
        provider: str,
        model: str,
        limits: dict[str, float],
        sources: dict[str, str],
    ) -> None:
        self.argv = argv
        self.environment = environment
        self.provider = provider
        self.model = model
        self.limits = limits
        self.sources = sources

    def limit(self, key: str) -> float:
        return self.limits[key]

    def identity(self) -> str:
        """The configured judge, named the way a conflict line may state it."""
        return f"{self.provider}/{self.model}"


def _refuse(detail: str, *, stage: str = CONFIG) -> JudgeError:
    return JudgeError("judge-invalid-config", stage, detail)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def read_document(path: Path, *, what: str) -> tuple[dict[str, Any], str]:
    """Read one configuration document and return it with its text digest."""
    try:
        if path.is_symlink() or not path.is_file():
            raise _refuse(f"{what} is not a regular file: {path}")
        raw = path.read_bytes()
    except OSError as exc:
        raise _refuse(f"{what} could not be read: {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _refuse(f"{what} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise _refuse(f"{what} must be a JSON object: {path}")
    return document, hashlib.sha256(raw).hexdigest()


def _strict_keys(value: dict[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise _refuse(f"{where} has unknown keys: {', '.join(unknown)}")


def _schema(value: Any, expected: int, where: str) -> None:
    if isinstance(value, bool) or value != expected:
        raise _refuse(f"{where} must state schema {expected}")


def _text_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _refuse(f"{where} must be a list of strings")
    return list(value)


def _limit(key: str, value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse(f"{where}.{key} must be a number")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise _refuse(f"{where}.{key} must be a positive finite number")
    if key in INTEGER_LIMITS and not isinstance(value, int):
        raise _refuse(f"{where}.{key} must be a whole number of bytes")
    return float(value) if key not in INTEGER_LIMITS else int(value)


def _limits(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _refuse(f"{where} must be a JSON object")
    _strict_keys(value, LIMIT_KEYS, where)
    missing = [key for key in LIMIT_KEYS if key not in value]
    if missing:
        raise _refuse(f"{where} is missing: {', '.join(missing)}")
    return {key: _limit(key, value[key], where) for key in LIMIT_KEYS}


def _launcher(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _refuse(f"{where} must be a JSON object")
    _strict_keys(value, LAUNCHER_KEYS, where)
    for key in ("command", "arguments", "extensions"):
        if key in value:
            value = {**value, key: _text_list(value[key], f"{where}.{key}")}
    for key in ("extension_flag", "provider_flag", "model_flag"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise _refuse(f"{where}.{key} must be a non-empty string")
    for key, expected in TRANSPORTS.items():
        if key in value and value[key] != expected:
            raise _refuse(f"{where}.{key} must be {expected!r}")
    return value


def _expand(raw: str, where: str, values: Mapping[str, str]) -> Path:
    """Expand only the placeholders the policy names, never arbitrary ones."""
    text = raw
    for name, replacement in values.items():
        text = text.replace(name, replacement)
    if "{" in text or "}" in text:
        raise _refuse(f"{where} uses a placeholder this policy does not define: {raw}")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise _refuse(f"{where} must be an absolute path: {raw}")
    return path


class JudgePolicy:
    """The shipped policy merged with one user document."""

    def __init__(
        self,
        launchers: dict[str, dict[str, Any]],
        limits: dict[str, Any],
        settings_path: Path,
        settings_present: bool,
        pi_settings: Path,
        sources: dict[str, str],
        environ: Mapping[str, str],
    ) -> None:
        self.launchers = launchers
        self.limits = limits
        self.settings_path = settings_path
        self.settings_present = settings_present
        self.pi_settings = pi_settings
        self.sources = sources
        self.environ = dict(environ)
        self.host: dict[str, Any] | None = None

    @classmethod
    def load(
        cls,
        *,
        hook_dir: Path,
        agent_home: Path,
        environ: Mapping[str, str] | None = None,
    ) -> JudgePolicy:
        """Read the shipped policy and the optional user document.

        `agent_home` is the home the hook runs for: the optional judge document
        lives under it, and the host settings live in the Pi agent directory
        (`PI_CODING_AGENT_DIR`, which defaults to that same home) rather than in
        a path appended to it.
        """
        environment = os.environ if environ is None else environ
        agent_dir = environment.get("PI_CODING_AGENT_DIR") or str(agent_home)
        values = {"{agent_home}": str(agent_home), "{pi_agent_dir}": agent_dir}
        policy_path = Path(environment.get(ENV_POLICY) or hook_dir / POLICY_FILE)
        document, policy_digest = read_document(policy_path, what="judge policy")
        _strict_keys(
            document, ("schema", "settings", "pi_settings", "defaults", "launchers"),
            "judge policy",
        )
        _schema(document.get("schema"), POLICY_SCHEMA, "judge policy")
        if not isinstance(document.get("settings"), str):
            raise _refuse("judge policy must name its settings document")
        if not isinstance(document.get("pi_settings"), str):
            raise _refuse("judge policy must name the host settings document")
        defaults = _limits(document.get("defaults"), "judge policy defaults")
        shipped = document.get("launchers")
        if not isinstance(shipped, dict) or not shipped:
            raise _refuse("judge policy must name at least one launcher")
        launchers = {
            name: _launcher(value, f"judge policy launchers.{name}")
            for name, value in shipped.items()
        }
        settings_path = _expand(document["settings"], "judge policy settings", values)
        pi_settings = _expand(document["pi_settings"], "judge policy pi_settings", values)
        sources = {"policy": policy_digest}

        named = environment.get(ENV_SETTINGS)
        selected = Path(named) if named else settings_path
        present = selected.exists() or selected.is_symlink()
        limits = dict(defaults)
        if not present:
            if named:
                raise _refuse(
                    f"the selected judge settings document does not exist: {selected}"
                )
        else:
            user, digest = read_document(selected, what="judge settings")
            _strict_keys(user, ("schema", "limits", "launchers", "pi_settings"),
                         "judge settings")
            _schema(user.get("schema"), SETTINGS_SCHEMA, "judge settings")
            if "limits" in user:
                limits.update(_limits(user["limits"], "judge settings limits"))
            if "launchers" in user:
                overrides = user["launchers"]
                if not isinstance(overrides, dict):
                    raise _refuse("judge settings launchers must be a JSON object")
                for name, override in overrides.items():
                    if name not in launchers:
                        raise _refuse(
                            f"judge settings names an unknown launcher: {name}"
                        )
                    launchers[name] = {
                        **launchers[name],
                        **_launcher(override, f"judge settings launchers.{name}"),
                    }
            if "pi_settings" in user:
                pi_settings = _expand(
                    str(user["pi_settings"]), "judge settings pi_settings", values
                )
            sources["settings"] = digest
        return cls(
            launchers,
            limits,
            settings_path,
            present,
            pi_settings,
            sources,
            environment,
        )

    def resolve(self, *, environ: Mapping[str, str] | None = None) -> JudgeLaunch:
        """Resolve the Pi launcher into one runnable invocation."""
        environment = dict(self.environ if environ is None else environ)
        launcher = self.launchers.get("pi")
        if launcher is None:
            raise _refuse("judge policy has no pi launcher")
        command = launcher.get("command")
        if not command:
            raise _refuse("judge launcher has no command")
        provider = self.identity(
            "PI_PROVIDER", "defaultProvider", environment, "provider"
        )
        model = self.identity("PI_MODEL", "defaultModel", environment, "model")
        argv = [*command, *launcher.get("arguments", [])]
        flag = launcher.get("extension_flag")
        extensions = launcher.get("extensions") or []
        if extensions and not flag:
            raise _refuse("judge launcher has extensions but no extension_flag")
        for extension in self.extensions(extensions):
            argv.extend((flag, str(extension)))
        argv.extend((launcher.get("provider_flag", ""), provider))
        argv.extend((launcher.get("model_flag", ""), model))
        argv = [item for item in argv if item]
        return JudgeLaunch(
            argv,
            environment,
            provider,
            model,
            dict(self.limits),
            dict(self.sources),
        )

    @staticmethod
    def extensions(values: list[str]) -> list[Path]:
        """The extension entries this launch would load, or a refusal."""
        paths: list[Path] = []
        seen: set[str] = set()
        for raw in values:
            path = Path(raw)
            if not path.is_absolute():
                raise _refuse(f"judge extension must be an absolute path: {raw}")
            resolved = Path(os.path.abspath(path))
            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)
            if not resolved.is_file():
                raise _refuse(
                    f"judge extension is not a readable entry file: {raw}"
                )
            if not os.access(resolved, os.R_OK):
                raise _refuse(f"judge extension is not readable: {raw}")
            paths.append(resolved)
        return paths

    def identity(
        self,
        variable: str,
        fallback: str,
        environment: Mapping[str, str],
        what: str,
    ) -> str:
        """The configured provider or model: the live value, else the host's."""
        if variable in environment:
            value = environment[variable]
            if not value.strip():
                raise _refuse(f"the judge {what} is stated but empty: {variable}")
            return value.strip()
        return self.host_default(fallback, what)

    def host_default(self, key: str, what: str) -> str:
        """One host settings value, read only when the environment omits it.

        The document is read once per policy, so a provider and a model cannot
        come from two different versions of it.
        """
        if self.host is None:
            document, digest = read_document(self.pi_settings, what="host settings")
            self.sources.setdefault("pi_settings", digest)
            self.host = document
        value = self.host.get(key)
        if not isinstance(value, str) or not value.strip():
            raise _refuse(
                f"the judge {what} is neither in the environment nor in {self.pi_settings}"
            )
        return value.strip()
