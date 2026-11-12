"""Starts one task at a time with subprocess; reports on Qt's main thread when it launched and exited.

A worker thread per block does the waiting (blocking on the task's output, then on its exit) and
only emits signals, which Qt delivers on the main thread. Works on Linux, macOS and Windows.
The task runs in its own process group, so a stop reaches whatever it started too, and in its own
Python environment: never the scheduler's (see environment()).

Each run's command, as a line you can paste in a shell, its output and its exit code go to the run's
output.log and, with ECHO, to the terminal the scheduler was started from, marked with the run.
"""

import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal

SETTLE_S = 1.0      # pause before each launch, so the previous task's screen and devices are released
BIN = "Scripts" if sys.platform == "win32" else "bin"      # where a virtual env keeps its programs
ECHO = True         # also show each run's command and output in the terminal

if sys.platform == "win32":
    GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def _taskkill(proc, *flags):        # /T: the whole process tree; without /F: ask windows to close
        subprocess.Popen(["taskkill", *flags, "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    def polite_stop(proc):
        with suppress(OSError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)    # console programs
        _taskkill(proc)                                   # GUI programs: like clicking the close button

    def force_kill(proc):
        _taskkill(proc, "/F")
else:
    GROUP = {"start_new_session": True}

    def polite_stop(proc):
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)

    def force_kill(proc):
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


def environment(project: Path | None) -> dict[str, str]:
    """The scheduler's environment variables without its own virtual env, plus the project's .venv
    if it has one: what `source .venv/bin/activate` does, without a shell."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONHOME")}
    own = Path(sys.prefix, BIN) if sys.prefix != sys.base_prefix else None    # the scheduler's venv
    path = [p for p in env.get("PATH", "").split(os.pathsep) if p and Path(p) != own]
    venv = Path(project, ".venv") if project else None
    if venv and venv.is_dir():
        env["VIRTUAL_ENV"] = str(venv)
        path.insert(0, str(venv / BIN))
    env["PATH"] = os.pathsep.join(path)
    return env


class Launcher(QObject):
    launched = Signal()         # the task was launched (waiting for the scanner is up to the task)
    exited = Signal(object)     # exit code; None if it was never launched

    _launched = Signal()         # emitted by the worker thread, re-emitted on the main thread
    _exited = Signal(object)

    def __init__(self):
        super().__init__()
        self._launched.connect(self.launched, Qt.ConnectionType.QueuedConnection)
        self._exited.connect(self.exited, Qt.ConnectionType.QueuedConnection)
        self._proc = None
        self._cancel = threading.Event()
        self._kill_timer = QTimer(self, singleShot=True, timeout=self._force_kill)

    def spawn(self, argv: list[str], outdir: Path, grace_s: float, project: Path | None = None):
        """Run argv in the project's folder and environment (none: in outdir, in a plain environment)."""
        self._kill_timer.stop()                         # a pending kill was for the previous task
        self._proc, self._cancel, self._grace_s = None, threading.Event(), grace_s
        threading.Thread(target=self._run, args=(argv, Path(outdir), project, self._cancel), daemon=True).start()

    def stop(self):
        """Ask the task to stop, force-kill it after its grace period. Its exit is reported as usual."""
        self._cancel.set()                              # not launched yet: it never will be
        if self._proc is not None:
            polite_stop(self._proc)
        self._kill_timer.start(int(self._grace_s * 1000))

    def _force_kill(self):
        if self._proc is not None and self._proc.poll() is None:
            force_kill(self._proc)

    def _run(self, argv, outdir, project, cancel):  # worker thread: only emits signals
        if cancel.wait(SETTLE_S):
            return self._exited.emit(None)
        outdir.mkdir(parents=True, exist_ok=True)
        env, folder = environment(project), Path(project or outdir)
        program = shutil.which(argv[0], path=env["PATH"]) or argv[0]     # found in the task's environment
        with open(outdir / "output.log", "wb") as log:
            def report(text: str):                     # to output.log, and to the terminal
                log.write(text.encode())
                log.flush()
                if ECHO:
                    sys.stderr.write(f"[{outdir.name}] {text}")
                    sys.stderr.flush()

            venv = ". .venv/bin/activate && " if "VIRTUAL_ENV" in env else ""
            report(f"[scheduler] $ (cd {shlex.quote(str(folder))} && {venv}{shlex.join(argv)})\n")
            if program == argv[0] and not os.path.isabs(program):
                report(f"[scheduler] {argv[0]} is not on the task's PATH\n")
            try:
                proc = subprocess.Popen([program, *argv[1:]], cwd=folder, env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, **GROUP)
            except OSError as exc:
                report(f"[scheduler] could not launch {argv[0]}: {exc}\n")
                return self._exited.emit(None)
            self._proc = proc
            if cancel.is_set():                         # stop() came while we were launching
                polite_stop(proc)
            self._launched.emit()
            for line in proc.stdout:                    # ends when the task (and any child) closes it
                report(line.decode(errors="replace"))
            code = proc.wait()
            report(f"[scheduler] exited with code {code}\n")
            self._exited.emit(code)