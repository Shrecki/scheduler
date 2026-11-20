"""What a task is: a command to run, with parameters. Which tasks exist: task_definitions.py.

Command placeholders: every parameter by name, plus {duration_s}, {outdir}, {subject}, {session}.
A True/False parameter is written {name:--flag}: the flag when it is on, nothing when it is off.
A parameter "rig" whose choices start with SESSION_RIG (task_definitions' RIG) follows the rig chosen
above the timeline (Session.rig), unless the block picks another: {rig} is its name under the rigs
folder ("examples/rigs/laptop.json"), {rig_file} its file.

A task that belongs to a project of its own sets `project`: it then runs in that folder and, if the
project has a Python environment (.venv), in it, by uv (Task.in_project: uv run --directory project),
so its command can call the project's programs directly. The scheduler runs exactly that, and a session
script writes it. Tasks never run in the scheduler's own environment.

A task can have a recording made alongside each of its runs (an eyetracker's: Recording, given to tasks
by with_recording): the recording starts first, the task once the recording is ready, and the recording
stops after it. Both go in one shell line (alongside), which the scheduler runs and a session script writes.
"""

import shlex
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).parents[1]
NEXT = "next"   # a "run" choice: the run after this task's earlier runs in the session (Task.next_run)
SESSION_RIG = "session rig"     # a "rig" choice: the rig chosen above the timeline (Session.rig)


class Category(Enum):                   # each needs a colour in view/theme.py (CATEGORY_COLORS)
    COGNITIVE_TASK_MRI = "Cognitive task fMRI"
    COGNITIVE_TASK_MEG = "Cognitive task MEG"
    RESTING_STATE = "Resting state"
    MOVIE_WATCHING = "Movie watching"
    VIDEO_GAME = "Video game"
    LOCALIZER_MEG = "MEG localizer"
    LOCALIZER_MRI = "MRI localizer"
    EYETRACKER_CALIBRATION = "Eyetracker calibration"


@dataclass(frozen=True)
class Recording:
    """A recording made alongside a task's runs (an eyetracker's): started first, in its own project; the
    task starts once the recording's output says it is `ready`, not at all if the recording ended
    before; after the task, the recording is stopped (SIGTERM: it saves) and its output added to the
    run's. Its command's placeholders are the task's, plus {task} (the task's key) and {run} (the run
    the task plays, as its command's --run says; else its n-th run in the session)."""
    command: tuple[str, ...]
    ready: str                          # what its output says once it records (or waits for the trigger)
    project: Path | str | None = None   # its own project folder, in whose Python environment it runs
    log: str = "recording.log"          # its output, in the run's folder
    startup_s: float = 0                # time it takes to be ready: planned on top of each run, not the task's
    stop_grace_s: float = 5.0           # time it may take to save, on top of the task's stop_grace_s

    def argv(self, values: dict) -> list[str]:
        """Its command line as it runs (in_project), placeholders filled in from `values`."""
        values = {name: Switch(v) if isinstance(v, bool) else v for name, v in values.items()}
        return in_project(self.project, [arg for part in self.command if (arg := part.format(**values))])


@dataclass(frozen=True)
class Task:
    key: str                          # unique; stored in session files
    label: str
    category: Category
    command: tuple[str, ...]
    params: dict = field(default_factory=dict)   # name: default value, or a tuple of choices (first = default)
    duration_s: float = 15 * 60
    stop_grace_s: float = 5.0         # after a stop request, force-kill after this long
    project: Path | str | None = None   # its own project folder: runs there, in its Python environment (in_project)
    recording: Recording | None = None  # made alongside each of its runs (with_recording)

    def defaults(self) -> dict:
        return {name: v[0] if isinstance(v, tuple) else v for name, v in self.params.items()}

    def choices(self, name: str) -> tuple | None:
        return self.params[name] if isinstance(self.params[name], tuple) else None

    def accepts(self, name: str, value) -> bool:
        """A valid value for a parameter: one of its choices, or a value of its default's type."""
        if name not in self.params:
            return False
        default = self.params[name]
        if isinstance(default, tuple):
            return value in default
        return type(value) is type(default) or (type(default) is float and type(value) is int)

    def next_run(self, before: list) -> int:
        """1 + the highest run of this task's earlier blocks (`before`), so blocks left on "next" play
        runs 1, 2, 3... and a block that plays a run again shifts nothing."""
        last = 0
        for block in before:
            if block.task == self.key:
                run = {**self.defaults(), **block.params}["run"]
                last = max(last, last + 1 if run == NEXT else int(run))
        return last + 1

    def argv(self, params: dict, **context) -> list[str]:
        """The command line, placeholders filled in; a part that comes out empty (a switch that is
        off) is left out. `context` also holds `before`, the session's runs before this one, for a
        run on "next", or a task whose command depends on them (an adapter's, which overrides this)."""
        values = {**self.defaults(), **params, **context}
        if values.get("run") == NEXT:
            values["run"] = self.next_run(context.get("before", []))
        values = {name: Switch(v) if isinstance(v, bool) else v for name, v in values.items()}
        return [arg for part in self.command if (arg := part.format(**values))]

    def in_project(self, argv: list[str]) -> list[str]:
        """argv as it runs, the same for the scheduler and a session script (in_project)."""
        return in_project(self.project, argv)

    def time_left(self, block_s: float) -> float:
        """Of a block of `block_s` seconds, the time for the task itself ({duration_s}): less its
        recording's startup."""
        return max(block_s - self.recording.startup_s, 1) if self.recording else block_s


def in_project(project: Path | str | None, argv: list[str]) -> list[str]:
    """argv as it runs, the same for the scheduler and a session script: if `project` has a Python
    environment (.venv), by uv in it, as it is (no sync: nothing installed or updated; --no-sync is for
    a uv project, with a pyproject.toml): uv run --directory PROJECT [--no-sync] argv. Else as it is,
    run in the project's folder."""
    project = Path(project).expanduser() if project else None
    if project is None or not (project / ".venv").is_dir():
        return list(argv)
    sync = ["--no-sync"] if (project / "pyproject.toml").is_file() else []
    return ["uv", "run", "--directory", str(project), *sync, *argv]


def with_recording(tasks: list[Task], recording: Recording | None) -> list[Task]:
    """`tasks`, each with `recording` made alongside its runs, planned its startup longer (None: as they are)."""
    if recording is None:
        return list(tasks)
    return [replace(t, recording=recording, duration_s=t.duration_s + recording.startup_s) for t in tasks]


def run_given(argv: list[str]) -> int | None:
    """The run a command line plays, as its --run (or -run) says, if it says one in numbers."""
    for i, word in enumerate(argv):
        flag, _, value = word.partition("=")
        if flag in ("--run", "-run"):
            value = value or (argv[i + 1] if i + 1 < len(argv) else "")
            return int(value) if value.isdigit() else None
    return None


def alongside(task: str, recording: str, ready: str, log: str) -> str:
    """One shell line that runs `task` with `recording` alongside (both shell text; `log`, a shell word: the
    recording's output): the recording in the background; once its output says `ready`, the task (not at
    all if the recording ended before); then the recording stopped (SIGTERM: it saves) and waited for,
    its output shown, and the task's exit code passed on.
    A stop (SIGTERM to the shell, or Ctrl+C in a terminal) goes to the task alone, once (to the recording
    if the task has not started): the task runs in the background so that the shell can pass it on."""
    return (f"{recording} > {log} 2>&1 & REC=$! TASK=; "
            f"trap 'if [ -n \"$TASK\" ]; then kill $TASK; else kill $REC; fi 2>/dev/null' TERM INT; "
            f"until grep -qF {shlex.quote(ready)} {log} 2>/dev/null || ! kill -0 $REC 2>/dev/null; do sleep 0.2; done; "
            f"if kill -0 $REC 2>/dev/null; then {task} & TASK=$!; "
            f"while wait $TASK; CODE=$?; kill -0 $TASK 2>/dev/null; do :; done; kill $REC 2>/dev/null; "
            f"else echo '[recording] it ended, or the run was stopped, before it was ready: the task did not start'; "
            f"CODE=1; fi; "
            f"while kill -0 $REC 2>/dev/null; do wait $REC; done; cat {log}; (exit $CODE)")


class Switch:
    """A True/False parameter in a command: "{name:--flag}" is the flag when on, nothing when off."""

    def __init__(self, on: bool):
        self.on = on

    def __format__(self, flag: str) -> str:
        return (flag if self.on else "") if flag else str(self.on)


# --- helpers for writing commands ----------------------------------------------------------------
def app(*command: str) -> tuple[str, ...]:
    """A program ended by `timeout` after the block's duration (exit code 124). --foreground: timeout
    stays in the task's process group (else it makes one of its own, which a force-kill would miss when
    uv runs it), and ends the program it runs, not that program's own children.
    Games are named by full path: Ubuntu keeps them in /usr/games, which not every PATH includes."""
    return ("timeout", "--foreground", "{duration_s}", *command)


def fake(key: str, *args: str) -> tuple[str, ...]:
    """fake_task.py: a stand-in that needs no display (used by the tests)."""
    return (sys.executable, str(ROOT / "fake_task.py"), "--task", key, "--duration", "{duration_s}",
            "--out", "{outdir}", *args)


def view(image: Path | str, *options: str) -> tuple[str, ...]:
    return ("eog", "--new-instance", *options, str(image))