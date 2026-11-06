"""The session's data and rules. Pure Python: no Qt.

Changes reach the view through notify(): called by the state machine after every transition, and
by the update methods below (design edits, clock ticks), which do not involve the state machine.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from .tasks import Category, get_task, tasks_for

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
    started_at: int | None = None      # facts recorded during the session
    ended_at: int | None = None
    exit_code: int | None = None

    @property
    def label(self) -> str:
        return get_task(self.task).label

    def played_ms(self, now: int) -> int:
        if self.started_at is None:
            return 0
        return (self.ended_at or now) - self.started_at


class Session:
    def __init__(self, processes):
        self.processes = processes      # the launcher: spawn(argv, outdir, start_line, grace_s) / stop()
        self.observers = []             # callables, called by notify()
        self.state = None               # written by the state machine
        self.subject, self.session_number = "sub-01", 1
        self.data_root = Path("data")
        self.total_ms = 100 * MIN
        self.mode = TimelineMode.TOTAL_RECORDING_TIME
        self.max_block_ms = 15 * MIN
        self.blocks: list[Block] = []
        self.index = -1                 # current block
        self.rec_start = self.rec_end = None
        self.rec_start_wall: datetime | None = None   # wall clock at rec_start, for acq_time
        self.notice = ""                # short message for the operator (e.g. a design that failed to load)

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
        """A run's task, duration and parameters can be changed in design, and while recording as long
        as the run has not started (e.g. by an adaptive algorithm). Adding/moving/removing: design only."""
        return self.in_state("design") or (self.in_state("recording") and index > self.index)

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

    def tick(self):
        self.notify()                   # time moved on: the clock and playhead need redrawing

    # --- time and rules (the guards of the state machine) -------------------------------------------
    def now(self) -> int:
        return self.rec_end or now_ms()

    def elapsed_ms(self) -> int:
        return self.now() - self.rec_start if self.rec_start else 0

    def block_time_ms(self) -> int:
        return sum(b.played_ms(self.now()) for b in self.blocks)

    def remaining_ms(self) -> int:
        used = self.block_time_ms() if self.mode == TimelineMode.TOTAL_BLOCK_TIME else self.elapsed_ms()
        return self.total_ms - used

    def time_is_up(self) -> bool:
        return self.rec_start is not None and self.remaining_ms() <= 0

    def has_blocks(self) -> bool:
        return bool(self.blocks)

    def can_continue(self) -> bool:
        return self.index + 1 < len(self.blocks) and not self.time_is_up()

    def timeline(self) -> tuple[list[tuple[Block, int, int]], int, int | None]:
        """Blocks laid end to end: (block, start, length) in ms, plus the end line and the playhead.

        Every block keeps its planned length (longer if it ran over). The playhead moves through
        the current block; the end line sits at playhead + remaining session time, so blocks past
        it are the ones that will be cut.
        """
        items, x, now, playhead = [], 0, self.now(), None
        for i, b in enumerate(self.blocks):
            length = max(b.duration_ms, b.played_ms(now))
            items.append((b, x, length))
            if i == self.index:
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
    def launch_next(self):
        self.index += 1
        block, task = self.current, get_task(self.current.task)
        outdir = self.session_dir / "blocks" / f"{self.index + 1:02d}_{task.key}"
        argv = task.argv(block.params, duration_s=f"{block.duration_ms / 1000:g}", outdir=outdir,
                         subject=self.subject, session=f"ses-{self.session_number:02d}")
        self.processes.spawn(argv, outdir, task.stop_grace_s, task.project)

    def mark_started(self):
        self.current.started_at = now_ms()

    def start_recording(self):
        self.rec_start, self.rec_start_wall = now_ms(), datetime.now()
        self.save_records()

    def mark_exited(self, exit_code):
        self.current.ended_at, self.current.exit_code = now_ms(), exit_code
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

    def load_design(self, source: str | Path | dict):
        """Replace the runs with a saved design (a file path, or its parsed content)."""
        if not self.in_state("design"):
            return
        name = "the previous session's design" if isinstance(source, dict) else Path(source).name
        try:
            design = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
            blocks = []
            for run in design["runs"]:
                task = get_task(run["task"])
                block = self.new_block(task)
                block.duration_ms = min(int(float(run["duration_s"]) * 1000), self.max_block_ms)
                block.params.update({k: v for k, v in run.get("parameters", {}).items()   # invalid ones:
                                     if task.accepts(k, v)})                              # default kept
                blocks.append(block)
            self.blocks = blocks
            self.total_ms = int(float(design.get("total_s", self.total_ms / 1000)) * 1000)
            self.mode = TimelineMode[design.get("timeline_mode", self.mode.name)]
            self.notice = f"Loaded {name}: {len(blocks)} runs"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.notice = f"Could not load {name}: {exc}"
        self.notify()

    def save_records(self):
        """The session's own files, rewritten at the start and after each run:
        - its design, as actually run (upcoming runs can still be edited while recording);
        - when each run started and stopped, as a BIDS-style table (tab-separated, n/a when missing):
          onsets and durations in seconds from the start of the recording; one row per run that started."""
        design = self.design(subject_name=self.subject, session_number=self.session_number)
        write(f"{self.prefix}_design.json", json.dumps(design, indent=2))
        rows = ["onset\tduration\ttrial_type\trun\tcategory\tacq_time\texit_code\tparameters"]
        for i, b in enumerate(self.blocks):
            if b.started_at is None:
                continue
            acq_time = self.rec_start_wall + timedelta(milliseconds=b.started_at - self.rec_start)
            rows.append("\t".join([f"{(b.started_at - self.rec_start) / 1000:.3f}",
                                   f"{b.played_ms(self.now()) / 1000:.3f}" if b.ended_at else "n/a",
                                   b.task, f"run-{i + 1:02d}", b.category.name,
                                   acq_time.isoformat(timespec="microseconds"),
                                   "n/a" if b.exit_code is None else str(b.exit_code),
                                   json.dumps(b.params) if b.params else "n/a"]))
        write(f"{self.prefix}_runs.tsv", "\n".join(rows) + "\n")
        write(f"{self.prefix}_runs.json", json.dumps(RUNS_SIDECAR, indent=2))


RUNS_SIDECAR = {   # BIDS: describes the columns of the runs table
    "onset": {"Description": "Start of the run (its start line, e.g. the scanner trigger), from the start of the recording",
              "Units": "s"},
    "duration": {"Description": "How long the run played, until its task exited", "Units": "s"},
    "trial_type": {"Description": "Task key, as in the scheduler's tasks.py"},
    "run": {"Description": "Position of the run in the session's timeline"},
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