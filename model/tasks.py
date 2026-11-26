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
stops as soon as the task ends. A shell function does it (Recording.function), defined once at the top of
a session script, each run a call to it; the scheduler runs that same function.
"""

import re
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
    FIELDMAP = "MRI Fieldmap (PA)"


@dataclass(frozen=True)
class Recording:
    """A recording made alongside a task's runs (an eyetracker's): started first, in its own project; the
    task starts once the recording's output says it is `ready`, not at all if the recording ended
    before; as soon as the task ends, the recording is stopped (SIGTERM: it saves). Its `command` is the
    same for every run, written once, in its shell function; each run gives it `args`, whose
    placeholders are the task's, plus {task} (the task's key as a BIDS label: bids_label) and {run} (the
    run the task plays, as its command's --run says; else its n-th run in the session)."""
    command: tuple[str, ...]            # the recorder, the same for every run (in its function)
    ready: str                          # what its output says once it records (or waits for the trigger)
    args: tuple[str, ...] = ()          # what each run tells it, after `command` (argv)
    name: str = "recording"             # its shell function: with_NAME (function)
    project: Path | str | None = None   # its own folder, where it runs, in the environment uv finds there
    log: str = "recording.log"          # its output, in the run's folder
    startup_s: float = 0                # time it takes to be ready: planned on top of each run, not the task's
    stop_grace_s: float = 5.0           # time it may take to save, on top of the task's stop_grace_s

    def argv(self, values: dict) -> list[str]:
        """One run's `args`, placeholders filled in from `values`."""
        values = {name: Switch(v) if isinstance(v, bool) else v for name, v in values.items()}
        return [arg for part in self.args if (arg := part.format(**values))]

    def recorder(self) -> list[str]:
        """Its command as it runs: given a project, always by uv in it, whatever environment uv finds for it
        there (uv run --directory PROJECT --no-sync: nothing installed); a wrong folder then fails loudly,
        instead of running the command outside it."""
        return list(uv(self.project, *self.command)) if self.project else list(self.command)

    def function(self, word=shlex.quote) -> str:
        """The shell function that runs a task with this recording: with_NAME DIR ARG... -- TASK..., DIR
        the run's folder (the recorder's output there: `log`), ARG... the run's arguments to the recorder
        (argv). `word` writes the recorder's words (a session script: its folders as its variables). A stop
        (SIGTERM to the run's processes, or Ctrl+C in a terminal) reaches the task and the recorder; the
        function then waits for the task, and stops the recorder (EXIT) once the task has ended."""
        return f"""with_{self.name}() (  # DIR ARG... -- TASK...: run TASK once the recorder (given ARG...) is armed; stop it when TASK ends
  log=$1/{shlex.quote(self.log)}; mkdir -p "$1" && : >"$log" || exit; shift
  (k=; for a; do shift; [ "$a" = -- ] && k=1; [ "$k" ] || set -- "$@" "$a"; done   # ARG... only
   exec {" ".join(map(word, self.recorder()))} "$@") >"$log" 2>&1 & rec=$!
  for a; do shift; [ "$a" = -- ] && break; done                                      # TASK... only
  trap 'kill $rec 2>/dev/null; wait $rec' EXIT; trap exit TERM INT
  until grep -qF {shlex.quote(self.ready)} "$log"; do
    kill -0 $rec 2>/dev/null || {{ echo "[{self.name}] ended before it was ready:"; cat "$log"; exit 1; }}; sleep 0.2
  done
  "$@"
)"""


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


class Switch:
    """A True/False parameter in a command: "{name:--flag}" is the flag when on, nothing when off."""

    def __init__(self, on: bool):
        self.on = on

    def __format__(self, flag: str) -> str:
        return (flag if self.on else "") if flag else str(self.on)


def bids_label(key: str) -> str:
    """`key` as a BIDS label (letters and digits only), in camelCase: "meg_eye_movements" -> "megEyeMovements"."""
    first, *rest = [word for word in re.split(r"[^A-Za-z0-9]+", key) if word] or [""]
    return first + "".join(word[0].upper() + word[1:] for word in rest)


def uv(project: Path | str, *command: str) -> tuple[str, ...]:
    """A command run by uv in `project`'s folder, in whatever environment uv finds for it there (no sync:
    nothing installed). Always, unlike a task's `project` (uv only if it sees PROJECT/.venv): for a
    program that exists only in that environment ("python", "eyetrack_calibration.py"); a wrong folder
    then fails loudly, instead of the command running outside it. Not with `project` as well."""
    return ("uv", "run", "--directory", str(Path(project).expanduser()), "--no-sync", *command)

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