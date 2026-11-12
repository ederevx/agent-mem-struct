"""Run one judge command under a bounded deadline and a killable process tree.

One owner for the judge's process boundary: `JudgeProcess` resolves the
launcher to a real executable, starts it so the whole tree can be terminated,
writes the prompt to its standard input, collects a bounded standard output,
and reports the outcome as a `ProcessResult` - never raising, never leaving a
child or a grandchild running, and never reading more of the answer than the
configured bound.

Containment is the reason this is a class of its own. A child that keeps its
own children can outlive a leader-only kill and hold the answer pipe open, so
on POSIX the child leads its own session and its process group is killed, and
on Windows the child is created suspended inside a Job Object configured with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, before any of its code runs, and is only
then resumed. A job that cannot be configured is refused rather than run
uncontained: the tree is killed before the failure is reported.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

# Windows batch launchers are not executables; CreateProcess runs them through
# the command processor, so a .bat/.cmd launcher is resolved on PATH and handed
# to %COMSPEC%. Characters this set holds are the ones cmd.exe would expand,
# split, or escape on its own, so an argument holding one is refused instead of
# silently becoming a different command.
BATCH_SUFFIXES = (".bat", ".cmd")
BATCH_UNSAFE = frozenset('&|<>^()%!"\r\n')
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
RESOLVE = "resolve"
SPAWN = "spawn"
CONTAINMENT = "containment"
STDIN = "stdin"
CAPTURE = "capture"
WAIT = "wait"
TEARDOWN = "teardown"


class ProcessResult:
    """What one judge run produced, and the stage it stopped at."""

    def __init__(
        self,
        output: bytes = b"",
        returncode: int | None = None,
        *,
        overflow: bool = False,
        timed_out: bool = False,
        stage: str = "",
        detail: str = "",
    ) -> None:
        self.output = output
        self.returncode = returncode
        self.overflow = overflow
        self.timed_out = timed_out
        self.stage = stage
        self.detail = detail

    @property
    def complete(self) -> bool:
        """Whether the run finished cleanly enough for parsing to mean anything."""
        return (
            not self.detail
            and not self.timed_out
            and not self.overflow
            and self.returncode == 0
        )


def _windows_batch_prefix() -> str | None:
    """The command processor that can start a batch launcher, or None."""
    processor = os.environ.get("COMSPEC")
    if not processor:
        system = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot")
        if not system:
            return None
        processor = str(Path(system) / "System32" / "cmd.exe")
    return processor if Path(processor).is_file() else None


def _batch_command(processor: str, items: list[str]) -> tuple[str, str]:
    """One `cmd /d /s /c` command line, or a refusal for an expandable argument."""
    unsafe = sorted({character for item in items for character in item if character in BATCH_UNSAFE})
    if unsafe:
        return "", (
            "a batch launcher argument holds a character the command processor "
            "would expand: " + ", ".join(repr(character) for character in unsafe)
        )
    quoted = " ".join(f'"{item}"' for item in items)
    # /d skips AutoRun, and /s keeps the command line exactly as written below
    # instead of applying cmd.exe's own quote stripping to it.
    return f'"{processor}" /d /s /c "{quoted}"', ""


def resolve_command(argv: list[str]) -> tuple[list[str] | str, str]:
    """Resolve argv[0] the way a shell would; return the command and any failure."""
    command = list(argv)
    name = command[0]
    if Path(name).is_file():
        resolved = str(Path(name))
    else:
        found = shutil.which(name)
        if found is None:
            return command, f"{name} was not found on PATH"
        resolved = found
    command[0] = resolved
    if os.name == "nt" and resolved.lower().endswith(BATCH_SUFFIXES):
        processor = _windows_batch_prefix()
        if processor is None:
            return command, f"no command processor to start {resolved}"
        return _batch_command(processor, command)
    return command, ""


class JudgeProcess:
    """The judge's process boundary: one bounded, contained invocation."""

    def __init__(self, capture_bytes: int, teardown_s: float) -> None:
        self.capture_bytes = int(capture_bytes)
        self.teardown_s = float(teardown_s)

    def run(self, launch: Any, prompt: bytes, *, deadline: float) -> ProcessResult:
        """Run the launcher with `prompt` on stdin and return its answer."""
        command, error = resolve_command(launch.argv)
        if error:
            return ProcessResult(stage=RESOLVE, detail=error)
        if deadline - time.monotonic() <= 0:
            return ProcessResult(
                timed_out=True, stage=WAIT, detail="the deadline had already passed"
            )
        child, job, error = self._start(command, launch)
        if child is None:
            stage = CONTAINMENT if error.startswith("job") else SPAWN
            return ProcessResult(stage=stage, detail=error)
        state: dict[str, Any] = {"overflow": False, "error": "", "capture": ""}
        collected: list[bytes] = []

        def feed() -> None:
            try:
                if prompt:
                    child.stdin.write(prompt)
                    child.stdin.flush()
            except (OSError, ValueError) as exc:
                state["error"] = str(exc)
            finally:
                try:
                    child.stdin.close()
                except (OSError, ValueError):
                    pass

        def drain() -> None:
            total = 0
            try:
                while True:
                    # One byte past the remaining room is enough to know the
                    # answer ran over: the bound is never exceeded by reading.
                    room = self.capture_bytes - total
                    chunk = child.stdout.read1(room + 1)
                    if not chunk:
                        return
                    keep = chunk[: max(0, room)]
                    if keep:
                        collected.append(keep)
                        total += len(keep)
                    if len(chunk) > room:
                        state["overflow"] = True
                        self._kill(child, job)
                        return
            except (OSError, ValueError) as exc:
                state["capture"] = str(exc) or type(exc).__name__
            finally:
                # The reader is finished with the pipe, so it closes it. A
                # teardown that closed this stream from outside could deadlock
                # against this read; a reader that never returns leaves the
                # stream to the process's own exit.
                try:
                    child.stdout.close()
                except (OSError, ValueError):
                    pass

        writer = threading.Thread(target=feed, name="judge-stdin", daemon=True)
        reader = threading.Thread(target=drain, name="judge-stdout", daemon=True)
        writer.start()
        reader.start()
        timed_out = False
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            self._teardown(child, job, writer, reader)
        output = b"".join(collected)
        if reader.is_alive():
            return ProcessResult(
                output,
                child.returncode,
                timed_out=timed_out,
                stage=TEARDOWN,
                detail="a descendant held the answer open past the teardown budget",
            )
        if writer.is_alive() or state["error"]:
            return ProcessResult(
                b"",
                child.returncode,
                stage=STDIN,
                detail=state["error"] or "the judge did not read the whole prompt",
            )
        if state["capture"]:
            # A read that failed after some bytes arrived would otherwise let a
            # partial `yes` pass for the whole answer, so it refuses.
            return ProcessResult(
                b"",
                child.returncode,
                stage=CAPTURE,
                detail=f"the answer could not be read: {state['capture']}",
            )
        if timed_out:
            return ProcessResult(
                output, stage=WAIT, timed_out=True, detail="the judge ran past its deadline"
            )
        if state["overflow"]:
            return ProcessResult(output, stage=CAPTURE, overflow=True)
        return ProcessResult(output, child.returncode)

    def _teardown(self, child: Any, job: Any, writer: Any, reader: Any) -> None:
        """Kill the tree, wait it out, then close the pipes - inside one deadline."""
        end = time.monotonic() + self.teardown_s
        self._kill(child, job)
        try:
            child.wait(timeout=max(0.0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        for thread in (writer, reader):
            thread.join(max(0.0, end - time.monotonic()))
        # Closing the containment handle after the tree is dead is what makes
        # `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` a cleanup guarantee rather than a
        # second kill; a POSIX group sweep covers a leader that exited first.
        if job is not None:
            job.close()
        elif os.name != "nt":
            self._sweep(child)
        # A stream is only closed once the worker that owns it has stopped:
        # closing a buffered stream a blocked reader still holds can deadlock
        # inside the stream's own lock and defeat this deadline. A surviving
        # worker already refuses the run above, so the pipe is left to it.
        for stream, worker in ((child.stdin, writer), (child.stdout, reader)):
            if stream is None or worker.is_alive():
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    # -- platform boundaries -------------------------------------------------

    def _start(self, command: Any, launch: Any) -> tuple[Any, Any, str]:
        flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        options: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "env": launch.environment,
            "creationflags": flags,
        }
        if os.name != "nt":
            options["start_new_session"] = True
        try:
            child = subprocess.Popen(command, **options)
        except (OSError, ValueError) as exc:
            first = command[0] if isinstance(command, str) else str(command[0])
            return None, None, f"{first}: {exc}"
        if os.name != "nt":
            return child, None, ""
        job, error = _JobHandle.attach(child)
        if error:
            self._kill(child, None)
            try:
                child.wait(timeout=max(0.1, self.teardown_s))
            except subprocess.TimeoutExpired:
                pass
            return None, None, error
        return child, job, ""

    def _kill(self, child: Any, job: Any) -> None:
        """Kill the whole tree now; safe to call more than once."""
        if job is not None and job.terminate():
            return
        if os.name != "nt":
            # The child leads its own session, so its pid is its process group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
                return
            except OSError:
                pass
        try:
            child.kill()
        except OSError:
            pass

    @staticmethod
    def _sweep(child: Any) -> None:
        """Kill anything a POSIX leader left behind in its process group."""
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass


class _JobHandle:
    """A Windows Job Object: kill-on-close containment, terminated once."""

    def __init__(self, handle: Any, api: Any) -> None:
        self._handle = handle
        self._api = api
        self._lock = threading.Lock()

    @classmethod
    def attach(cls, child: Any) -> tuple["_JobHandle | None", str]:
        """Put the suspended child in a kill-on-close job and resume it."""
        api, error = _windows_api()
        if api is None:
            return None, f"job: {error}"
        job, error = api.create()
        if job is None:
            return None, f"job: {error}"
        handle = cls(job, api)
        error = api.assign(handle._handle, int(child._handle))
        if error:
            handle.close()
            return None, f"job: {error}"
        error = api.resume(int(child._handle))
        if error:
            handle.terminate()
            handle.close()
            return None, f"job: {error}"
        return handle, ""

    def terminate(self) -> bool:
        with self._lock:
            if self._handle is None:
                return True
            return self._api.terminate(self._handle)

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._api.terminate(self._handle)
                self._api.close(self._handle)
                self._handle = None


class _WindowsApi:
    """The few Job Object calls this module needs, with their signatures set."""

    def __init__(self, ctypes: Any, wintypes: Any, limits: Any) -> None:
        self.ctypes = ctypes
        self.wintypes = wintypes
        self.limits = limits
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        kernel32 = self.kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ntdll.NtResumeProcess.restype = ctypes.c_long
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def create(self) -> tuple[Any, str]:
        """Create a job that kills its processes when its last handle closes.

        The limit is required, not preferred: without it the tree survives this
        hook being killed by its own caller, which is exactly the case the
        containment exists for. A kernel that refuses it refuses the judge.
        """
        handle = self.kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None, f"CreateJobObject failed ({self.ctypes.get_last_error()})"
        limits = self.limits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = self.kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            self.ctypes.byref(limits),
            self.ctypes.sizeof(limits),
        )
        if not configured:
            error = self.ctypes.get_last_error()
            self.kernel32.CloseHandle(handle)
            return None, f"SetInformationJobObject failed ({error})"
        return handle, ""

    def assign(self, job: Any, process: int) -> str:
        if not self.kernel32.AssignProcessToJobObject(
            job, self.wintypes.HANDLE(process)
        ):
            return f"AssignProcessToJobObject failed ({self.ctypes.get_last_error()})"
        return ""

    def resume(self, process: int) -> str:
        status = self.ntdll.NtResumeProcess(self.wintypes.HANDLE(process))
        return "" if status == 0 else f"NtResumeProcess failed ({status})"

    def terminate(self, job: Any) -> bool:
        return bool(self.kernel32.TerminateJobObject(job, 1))

    def close(self, job: Any) -> None:
        self.kernel32.CloseHandle(job)


def _windows_api() -> tuple[_WindowsApi | None, str]:
    """Build the Job Object API for one run, or report why it is unavailable."""
    if os.name != "nt":
        return None, "not Windows"
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimits(ctypes.Structure):
            # The documented JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout: the
            # four trailing SIZE_T fields are part of the structure, and a
            # shorter buffer is rejected by SetInformationJobObject.
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        return _WindowsApi(ctypes, wintypes, ExtendedLimits), ""
    except (OSError, AttributeError) as exc:
        return None, str(exc)
