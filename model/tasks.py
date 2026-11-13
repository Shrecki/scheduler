"""What a task is: a command to run, with parameters. Which tasks exist: task_definitions.py.

Command placeholders: every parameter by name, plus {duration_s}, {outdir}, {subject}, {session}.
A True/False parameter is written {name:--flag}: the flag when it is on, nothing when it is off.

A task that belongs to a project of its own sets `project`: it then runs in that folder, with the
project's .venv activated if it has one (as `cd project && . .venv/bin/activate` would), so its
command can call the project's programs directly. Tasks never run in the scheduler's own environment.
"""

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).parents[1]
NEXT = "next"   # a "run" choice: the run after this task's earlier runs in the session (Task.next_run)



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
class Task:
    key: str                          # unique; stored in session files
    label: str
    category: Category
    command: tuple[str, ...]
    params: dict = field(default_factory=dict)   # name: default value, or a tuple of choices (first = default)
    duration_s: float = 15 * 60
    stop_grace_s: float = 5.0         # after a stop request, force-kill after this long
    project: Path | str | None = None   # its own project folder: runs there, in the project's .venv

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


class Switch:
    """A True/False parameter in a command: "{name:--flag}" is the flag when on, nothing when off."""

    def __init__(self, on: bool):
        self.on = on

    def __format__(self, flag: str) -> str:
        return (flag if self.on else "") if flag else str(self.on)


# --- helpers for writing commands ----------------------------------------------------------------
def app(*command: str) -> tuple[str, ...]:
    """A program ended by `timeout` after the block's duration (exit code 124).
    Games are named by full path: Ubuntu keeps them in /usr/games, which not every PATH includes."""
    return ("timeout", "{duration_s}", *command)


def fake(key: str, *args: str) -> tuple[str, ...]:
    """fake_task.py: a stand-in that needs no display (used by the tests)."""
    return (sys.executable, str(ROOT / "fake_task.py"), "--task", key, "--duration", "{duration_s}",
            "--out", "{outdir}", *args)


def view(image: Path | str, *options: str) -> tuple[str, ...]:
    return ("eog", "--new-instance", *options, str(image))