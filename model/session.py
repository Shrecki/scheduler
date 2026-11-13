"""The session's data and rules. Pure Python: no Qt.

Changes reach the view through notify(): called by the state machine after every transition, and
by the update methods below (design edits, clock ticks), which do not involve the state machine.
"""

import itertools
import json
import random
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from .task_definitions import get_task, tasks_for
from .templates import Template, find_templates
from .tasks import Category

MIN = 60_000


def now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


class TimelineMode(Enum):
    TOTAL_RECORDING_TIME = 1    # the session ends when recording time is used up (pauses count)
    TOTAL_BLOCK_TIME = 2        # the session ends when time spent in blocks is used up


@dataclass
class Block:
    task: str
    category: Category
    duration_ms: int
    params: dict = field(default_factory=dict)
    started_at: int | None = None      # its play on the timeline (the first that started): replays
    ended_at: int | None = None        # do not move the timeline back, they are only in `plays`
    exit_code: int | None = None

    @property
    def label(self) -> str:
        return get_task(self.task).label

    def played_ms(self, now: int) -> int:
        if self.started_at is None:
            return 0
        return (self.ended_at or now) - self.started_at


@dataclass
class Play:
    """One launch of a run. A run played again (clicked, autoplay off) is a second Play of its block."""
    index: int                         # which block
    params: dict                       # as they were when it was launched
    started_at: int | None = None
    ended_at: int | None = None
    exit_code: int | None = None

    def played_ms(self, now: int) -> int:
        return 0 if self.started_at is None else (self.ended_at or now) - self.started_at


class Session:
    def __init__(self, processes, scripts=None):
        self.processes = processes      # the launcher: spawn(argv, outdir, grace_s, project) / stop()
        self.scripts = scripts          # a session-script format: write(session) -> text, read(text) -> design, note
        self.observers = []             # callables, called by notify()
        self.state = None               # written by the state machine
        self.subject, self.session_number = "sub-01", 1
        self.data_root = Path("data")
        self.total_ms = 100 * MIN
        self.mode = TimelineMode.TOTAL_RECORDING_TIME
        self.max_block_ms = 15 * MIN
        self.blocks: list[Block] = []
        self.index = -1                 # the block playing, or played last
        self.farthest = -1              # the farthest block played: the timeline's progress
        self.queued: int | None = None  # the block to play next, if clicked (None: the one after `farthest`)
        self.autoplay = True            # play the blocks one after the other; off: each when clicked
        self.plays: list[Play] = []     # every launch, replays included
        self.rec_start = self.rec_end = None
        self.rec_start_wall: datetime | None = None   # wall clock at rec_start, for acq_time
        self.notice = ""                # short message for the operator (e.g. a design that failed to load)
        self.template_folder: Path | None = None
        self.templates: list[Template] = []     # what the template folder holds (scan_templates)

    def notify(self):
        for observer in self.observers:
            observer()

    def in_state(self, name: str) -> bool:
        return name in self._states()

    @property
    def leaf_state(self) -> str:
        """The innermost active state, e.g. "block_running"."""
        return self._states()[-1]

    def _states(self) -> list[str]:
        return [self.state] if isinstance(self.state, str) else list(self.state or [""])

    @property
    def current(self) -> Block | None:
        return self.blocks[self.index] if 0 <= self.index < len(self.blocks) else None

    @property
    def session_dir(self) -> Path:
        return self.data_root / self.subject / f"ses-{self.session_number:02d}"

    # --- updates that do not involve the state machine (view -> controller -> here) ---------------
    # The timeline can only be edited in the design state.
    def new_block(self, task) -> Block:
        """A run of `task` with its default duration and parameters."""
        return Block(task.key, task.category, min(int(task.duration_s * 1000), self.max_block_ms), task.defaults())

    def add_block(self, category_name: str, index: int = -1):
        if self.in_state("design"):
            block = self.new_block(tasks_for(Category[category_name])[0])   # first task of the category
            self.blocks.insert(index if 0 <= index <= len(self.blocks) else len(self.blocks), block)
            self.notify()

    def editable(self, index: int) -> bool:
        """A run's task, duration and parameters can be changed in design, and while recording unless
        it is the run playing (e.g. by an adaptive algorithm). Adding/moving/removing: design only."""
        return self.in_state("design") or (self.in_state("recording") and not (self.playing() and index == self.index))

    def set_autoplay(self, on: bool):
        self.autoplay = on
        self.notify()

    def set_task(self, index: int, key: str):
        if self.editable(index):
            self.blocks[index] = self.new_block(get_task(key))
            self.notify()

    def set_param(self, index: int, name: str, value):
        if self.editable(index) and get_task(self.blocks[index].task).accepts(name, value):
            self.blocks[index].params[name] = value
            self.notify()

    def set_duration(self, index: int, ms: int):
        if self.editable(index):
            self.blocks[index].duration_ms = min(ms, self.max_block_ms)
            self.notify()

    def move_block(self, source: int, target: int):
        if self.in_state("design"):
            self.blocks.insert(target - (target > source), self.blocks.pop(source))
            self.notify()

    def remove_block(self, index: int):
        if self.in_state("design"):
            del self.blocks[index]
            self.notify()

    def repeat_blocks(self, indices: list[int]):
        """Copies of the runs at `indices`, in their order and over again, after the last run, for as
        long as the next one fits before the end of the session (its total time)."""
        if self.in_state("design") and indices:
            self.append_while_fits(itertools.cycle([self.blocks[i] for i in sorted(indices)]), "repeat")

    def shuffle_blocks(self, indices: list[int], rng: random.Random = random):
        """As repeat_blocks, in rounds that each have every run at `indices` once, in a new random order:
        the session keeps their proportions."""
        if self.in_state("design") and indices:
            pattern = [self.blocks[i] for i in sorted(indices)]
            rounds = (block for _ in itertools.count() for block in rng.sample(pattern, len(pattern)))
            self.append_while_fits(rounds, "shuffle")

    def append_while_fits(self, runs, verb: str):
        """Copies of `runs`, one after the other after the last run, until the next one does not fit."""
        free, added = self.free_ms(), 0
        for block in runs:
            if not 0 < block.duration_ms <= free:
                break
            self.blocks.append(self.copy_block(block))
            free, added = free - block.duration_ms, added + 1
        done = {"repeat": "Repeated", "shuffle": "Shuffled"}[verb]
        self.notice = (f"{done}: {added} run{'s' * (added > 1)} added, {_mmss(free)} left" if added
                       else f"No room to {verb}: {_mmss(free)} left")
        self.notify()

    def interleave_blocks(self, indices: list[int]):
        """A copy of the runs at `indices` after each run that follows them, for as long as the copies
        fit before the end of the session."""
        if self.in_state("design") and indices:
            pattern = [self.blocks[i] for i in sorted(indices)]
            size, free, added = sum(b.duration_ms for b in pattern), self.free_ms(), 0
            blocks = self.blocks[:max(indices) + 1]
            for block in self.blocks[max(indices) + 1:]:
                blocks.append(block)
                if size <= free:
                    blocks += [self.copy_block(b) for b in pattern]
                    free, added = free - size, added + 1
            self.blocks[:] = blocks
            self.notice = (f"Interleaved: {added} cop{'ies' if added > 1 else 'y'} added, {_mmss(free)} left" if added else
                           "No run after them to interleave with" if max(indices) == len(self.blocks) - 1 else
                           f"No room to interleave: {_mmss(free)} left")
            self.notify()

    def copy_block(self, block: Block) -> Block:
        """The same run again, not yet played."""
        return replace(block, params=dict(block.params), started_at=None, ended_at=None, exit_code=None)

    def free_ms(self) -> int:
        """The time left in the plan: the session's total time minus the runs' planned durations."""
        return self.total_ms - sum(b.duration_ms for b in self.blocks)

    def tick(self):
        self.notify()                   # time moved on: the clock and playhead need redrawing

    # --- time and rules (the guards of the state machine) -------------------------------------------
    def now(self) -> int:
        return self.rec_end or now_ms()

    def elapsed_ms(self) -> int:
        return self.now() - self.rec_start if self.rec_start else 0

    def block_time_ms(self) -> int:
        return sum(p.played_ms(self.now()) for p in self.plays)

    def remaining_ms(self) -> int:
        used = self.block_time_ms() if self.mode == TimelineMode.TOTAL_BLOCK_TIME else self.elapsed_ms()
        return self.total_ms - used

    def time_is_up(self) -> bool:
        return self.rec_start is not None and self.remaining_ms() <= 0

    def has_blocks(self) -> bool:
        return bool(self.blocks)

    def can_continue(self) -> bool:
        """There is a block after the farthest played, and time left to play it."""
        return self.farthest + 1 < len(self.blocks) and not self.time_is_up()

    def playable(self, index: int) -> bool:
        return 0 <= index < len(self.blocks) and not self.time_is_up()

    def playing(self) -> bool:
        """A block's task is launching, running or stopping."""
        return any(self.in_state(s) for s in ("next_block_await", "block_running", "quit_sequence"))

    def times_played(self, index: int) -> int:
        return sum(p.index == index for p in self.plays)

    def timeline(self) -> tuple[list[tuple[Block, int, int]], int, int | None]:
        """Blocks laid end to end: (block, start, length) in ms, plus the end line and the playhead.

        Every block keeps its planned length (longer if it ran over). The playhead moves through
        the farthest block played (playing an earlier one again does not move it back); the end line
        sits at playhead + remaining session time, so blocks past it are the ones that will be cut.
        """
        items, x, now, playhead = [], 0, self.now(), None
        for i, b in enumerate(self.blocks):
            length = max(b.duration_ms, b.played_ms(now))
            items.append((b, x, length))
            if i == self.farthest:
                playhead = x + (length if b.ended_at else b.played_ms(now))
            x += length
        end = self.total_ms if playhead is None else playhead + max(self.remaining_ms(), 0)
        return items, end, playhead

    def composition(self) -> list[tuple[Category, int]]:
        """Time per category in the timeline, in ms (as drawn: planned, or played if longer)."""
        items, _, _ = self.timeline()
        totals = {c: sum(n for b, _, n in items if b.category == c) for c in Category}
        return [(c, ms) for c, ms in totals.items() if ms]

    # --- actions, called by the state machine --------------------------------------------------------
    def command(self, index: int) -> tuple[list[str], Path]:
        """What run `index` executes: its command line, and its output folder."""
        block, task = self.blocks[index], get_task(self.blocks[index].task)
        again = f"_{n}" if (n := self.times_played(index)) > 1 else ""         # a run played again: its own folder
        outdir = (self.session_dir / "blocks" / f"{index + 1:02d}_{task.key}{again}").absolute()  # tasks run elsewhere
        argv = task.argv(block.params, duration_s=f"{block.duration_ms / 1000:g}", outdir=outdir,
                         subject=self.subject, session=f"ses-{self.session_number:02d}", ses=self.session_number,
                         before=self.blocks[:index])
        return argv, outdir

    def queue(self, index: int):
        self.queued = index

    def launch_next(self):
        """Launch the block clicked, or else the one after the farthest played."""
        index = self.farthest + 1 if self.queued is None else self.queued
        self.index, self.farthest, self.queued = index, max(self.farthest, index), None
        self.plays.append(Play(index, dict(self.current.params)))
        argv, outdir = self.command(index)
        task = get_task(self.current.task)
        self.processes.spawn(argv, outdir, task.stop_grace_s, task.project)

    def mark_started(self):
        block, self.plays[-1].started_at = self.current, now_ms()
        if block.started_at is None:                    # its first play that starts: the timeline's
            block.started_at, block.ended_at, block.exit_code = self.plays[-1].started_at, None, None

    def start_recording(self):
        self.rec_start, self.rec_start_wall = now_ms(), datetime.now()
        self.save_records()

    def mark_exited(self, exit_code):
        play, block = self.plays[-1], self.current
        play.ended_at, play.exit_code = now_ms(), exit_code
        if block.ended_at is None:
            block.ended_at, block.exit_code = play.ended_at, exit_code
        self.save_records()

    # --- files ------------------------------------------------------------------------------------------
    @property
    def prefix(self) -> Path:
        return self.session_dir / f"{self.subject}_ses-{self.session_number:02d}"

    def design(self, **extra) -> dict:
        """The design as a reusable template: runs in order, with their task, duration and parameters."""
        return {**extra, "total_s": self.total_ms / 1000, "timeline_mode": self.mode.name,
                "runs": [{"category": b.category.name, "task": b.task, "duration_s": b.duration_ms / 1000,
                          "parameters": b.params} for b in self.blocks]}

    def save_design(self, path: str | Path):
        try:
            write(path, json.dumps(self.design(), indent=2))
            self.notice = f"Saved {Path(path).name}"
        except OSError as exc:
            self.notice = f"Could not save {Path(path).name}: {exc}"
        self.notify()

    def save_script(self, path: str | Path):
        """The design as a session script, in the format given to the session (`scripts`)."""
        try:
            write(path, self.scripts.write(self))
            Path(path).chmod(0o755)
            self.notice = f"Exported {Path(path).name}"
        except OSError as exc:
            self.notice = f"Could not export {Path(path).name}: {exc}"
        self.notify()

    def load_script(self, path: str | Path):
        """Replace the runs with those of a session script, in the format given to the session."""
        try:
            design, note = self.scripts.read(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.notice = f"Could not load {Path(path).name}: {exc}"
            return self.notify()
        self.load_design(design, Path(path).name)
        if note and self.notice.startswith("Loaded"):
            self.notice += f" ({note})"
            self.notify()

    def load_design(self, source: str | Path | dict, name: str | None = None):
        """Replace the runs with a saved design (a file path, or its parsed content)."""
        if not self.in_state("design"):
            return
        name = name or ("the previous session's design" if isinstance(source, dict) else Path(source).name)
        try:
            design = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
            self.blocks = blocks = self.blocks_from(design["runs"])
            self.total_ms = int(float(design.get("total_s", self.total_ms / 1000)) * 1000)
            self.mode = TimelineMode[design.get("timeline_mode", self.mode.name)]
            self.notice = f"Loaded {name}: {len(blocks)} runs"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.notice = f"Could not load {name}: {exc}"
        self.notify()

    def blocks_from(self, runs: list[dict]) -> list[Block]:
        """A design's runs as blocks (an invalid parameter keeps its default)."""
        blocks = []
        for run in runs:
            task = get_task(run["task"])
            block = self.new_block(task)
            block.duration_ms = min(int(float(run["duration_s"]) * 1000), self.max_block_ms)
            block.params.update({k: v for k, v in run.get("parameters", {}).items() if task.accepts(k, v)})
            blocks.append(block)
        return blocks

    # --- the template library -----------------------------------------------------------------------
    def scan_templates(self, folder: str | Path):
        """Read the templates in `folder` and its subfolders (again)."""
        self.template_folder = Path(folder).expanduser()
        if not self.template_folder.is_dir():
            self.templates, self.notice = [], f"No template folder {self.template_folder}"
            return self.notify()
        self.templates, unreadable = find_templates(self.template_folder, self.scripts)
        n = len(self.templates)
        self.notice = f"{n} template{'s' * (n != 1)} in {self.template_folder.name}"
        if unreadable:
            self.notice += f"; could not read {', '.join(unreadable[:3])}{' and more' * (len(unreadable) > 3)}"
        self.notify()

    def add_template(self, number: int, index: int = -1):
        """Insert template `number`'s runs before run `index` (-1: at the end)."""
        if not self.in_state("design"):
            return
        template = self.templates[number]
        try:
            blocks = self.blocks_from(template.design["runs"])
        except (ValueError, KeyError, TypeError) as exc:
            self.notice = f"Could not add {template.name}: {exc}"
            return self.notify()
        at = index if 0 <= index <= len(self.blocks) else len(self.blocks)
        self.blocks[at:at] = blocks
        free = self.free_ms()
        room = f"{_mmss(free)} left" if free >= 0 else f"{_mmss(-free)} over the session's length"
        self.notice = f"Added {template.name}: {len(blocks)} run{'s' * (len(blocks) != 1)}, {room}"
        self.notify()

    def load_template(self, number: int):
        """Replace the runs with template `number`'s."""
        template = self.templates[number]
        self.load_design(template.design, template.name)

    def save_records(self):
        """The session's own files, rewritten at the start and after each run:
        - its design, as actually run (upcoming runs can still be edited while recording);
        - when each run started and stopped, as a BIDS-style table (tab-separated, n/a when missing):
          onsets and durations in seconds from the start of the recording; one row per play that started
          (a run played again has a row for each time)."""
        design = self.design(subject_name=self.subject, session_number=self.session_number)
        write(f"{self.prefix}_design.json", json.dumps(design, indent=2))
        rows = ["onset\tduration\ttrial_type\trun\tcategory\tacq_time\texit_code\tparameters"]
        for p in self.plays:
            if p.started_at is None:
                continue
            b = self.blocks[p.index]
            acq_time = self.rec_start_wall + timedelta(milliseconds=p.started_at - self.rec_start)
            rows.append("\t".join([f"{(p.started_at - self.rec_start) / 1000:.3f}",
                                   f"{p.played_ms(self.now()) / 1000:.3f}" if p.ended_at else "n/a",
                                   b.task, f"run-{p.index + 1:02d}", b.category.name,
                                   acq_time.isoformat(timespec="microseconds"),
                                   "n/a" if p.exit_code is None else str(p.exit_code),
                                   json.dumps(p.params) if p.params else "n/a"]))
        write(f"{self.prefix}_runs.tsv", "\n".join(rows) + "\n")
        write(f"{self.prefix}_runs.json", json.dumps(RUNS_SIDECAR, indent=2))


RUNS_SIDECAR = {   # BIDS: describes the columns of the runs table
    "onset": {"Description": "Launch of the run's task, from the start of the recording",
              "Units": "s"},
    "duration": {"Description": "How long the run played, until its task exited", "Units": "s"},
    "trial_type": {"Description": "Task key, as in the scheduler's task_definitions.py"},
    "run": {"Description": "Position of the run in the session's timeline (a run played again has a row each time)"},
    "category": {"Description": "Category of the task"},
    "acq_time": {"Description": "Wall-clock time (local) at which the run started"},
    "exit_code": {"Description": "Exit code of the task; negative: stopped by that signal; n/a: could not be launched"},
    "parameters": {"Description": "Task parameters used for the run (JSON)"},
}


def write(path: str | Path, text: str):
    """Write the whole file at once (a crash never leaves it half-written)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _mmss(ms: int) -> str:
    """5:03, 1:12:30; 0:00 for a plan already past its end."""
    s = max(ms, 0) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"