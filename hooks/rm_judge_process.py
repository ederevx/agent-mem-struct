"""Platform-specific process arguments shared by both judge spawn sites."""
from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
from typing import Any

# Conservatively exclude expansion and control syntax from batch commands.
BATCH_REFUSED = '"%!^&|<>()'


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
    comspec = environment.get("COMSPEC")
    if not comspec or not ntpath.isabs(comspec):
        raise FileNotFoundError("COMSPEC")
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
