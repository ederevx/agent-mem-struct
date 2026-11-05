"""Platform-specific process arguments shared by both judge spawn sites."""
from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
from typing import Any

# Conservatively exclude expansion and control syntax from batch commands.
BATCH_REFUSED = '"%!^&|<>()'
# The Windows directory the API reports is bounded by MAX_PATH in practice; a
# generous buffer keeps one call enough and a short return is detectable.
SYSTEM_DIRECTORY_CHARS = 32768


def system_command_processor() -> str | None:
    """The absolute `cmd.exe` under the system directory, or None.

    Only used when COMSPEC is absent: a process started without a console
    session - a shortcut, a detached service - inherits no COMSPEC, and a batch
    target cannot be started without one. The API answers where Windows itself
    keeps the processor, so nothing is resolved through PATH here.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(SYSTEM_DIRECTORY_CHARS)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError, ValueError):
        return None
    # Zero is a failure; a length at or beyond the buffer means it was too short
    # and the text is not trustworthy.
    if length <= 0 or length >= len(buffer):
        return None
    candidate = ntpath.join(buffer.value, "cmd.exe")
    if not ntpath.isabs(candidate):
        return None
    try:
        if not os.path.isfile(candidate):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def command_processor(environment: dict[str, str]) -> str:
    """The command processor for a batch target: stated, else the system one.

    A stated COMSPEC is used as given and never second-guessed: an empty or
    relative value is a broken launch environment, and silently replacing it
    would hide the misconfiguration instead of reporting it.
    """
    comspec = environment.get("COMSPEC")
    if comspec is not None:
        if not ntpath.isabs(comspec):
            raise FileNotFoundError("COMSPEC")
        return comspec
    derived = system_command_processor()
    if derived is None:
        raise FileNotFoundError("COMSPEC")
    return derived


def spawn_args(argv: list[str], environment: dict[str, str]) -> dict[str, Any]:
    """The `Popen` arguments that start `argv` in `environment`."""
    if os.name != "nt":
        return {"args": argv}
    # `which()` reads the unchanged PATHEXT inherited by both callers.
    qualified = bool(ntpath.dirname(argv[0]))
    if qualified and ntpath.splitext(argv[0])[1]:
        program = argv[0]
    else:
        if not environment.get("PATHEXT") or (
            not qualified and not environment.get("PATH")
        ):
            raise FileNotFoundError("Windows launch environment is incomplete")
        program = shutil.which(argv[0], path=environment.get("PATH", ""))
        if program is None:
            raise FileNotFoundError(argv[0])
    command = [ntpath.abspath(program), *argv[1:]]
    if ntpath.splitext(command[0])[1].lower() not in (".cmd", ".bat"):
        return {"args": command}
    comspec = command_processor(environment)
    for part in (comspec, *command):
        if any(ord(char) < 32 or char in BATCH_REFUSED for char in part):
            raise OSError("unsupported batch argument")
    # A raw command line, not a list: a list element holding the command would
    # be quoted a second time before the processor ever saw it. `/d` suppresses
    # AutoRun, `/v:off` keeps delayed expansion out of the text, `/s` strips
    # only the outer quote pair.
    line = (
        f"{subprocess.list2cmdline([comspec])} /d /v:off /s /c "
        f'"{subprocess.list2cmdline(command)}"'
    )
    return {"args": line, "executable": comspec}
