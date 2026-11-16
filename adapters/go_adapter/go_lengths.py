"""Compiled programs that read their flags with Go's standard flag package (goxpyriment's, for one),
seen from the scheduler: each program a task, its flags as parameters.

    <goxpyriment>/bin/MEG-localizer      runs as: bin/MEG-localizer -s=07 [the flags a block changes]
                                         (in <goxpyriment>, the folder above bin/)

Nothing of a program's source is needed: when the scheduler starts, each program is asked for its
usage (`program -h`; the flag package prints every flag and exits, before the program does anything
else), and its flags are read from that text, written in the flag package's format:

      -w	Windowed mode                                   no type: a checkbox
      -d int
        	Display ID (default -1)                         int, uint: a number box; float: a decimal box
      -ttl string
        	TTL device (default "parallel:/dev/parport0")   anything else (string, duration...): a text field

A flag shown without "(default ...)" has Go's zero value: off, 0, or empty. The settings of the PC
(SITE_FLAGS: display, TTL device and codes, photodiode, keys, screen geometry) are not offered: they
belong in the program's config file (goxpyriment's site.toml), as DBP_MEG's command_line_args.txt says.

A block passes only the flags it changes (as -name=value), so that defaults the program takes from
elsewhere still apply (goxpyriment's TOML config file sets any flag not on the command line; the card
shows the program's built-in defaults). The subject flag (FILLED: goxpyriment's -s) is not a
parameter: it gets the session's subject, in letters and digits (sub-07: 07), which also keeps
goxpyriment from opening its setup dialog.

What the program's source folder (`sources`/<program>/) adds:
- Runs: a program with a -run flag whose folder has numbered run files (stimuli/arc_stimuli_run2.csv: 2)
  offers "next" or one of those numbers (GoTask.run_number).
- Protocols: a table-driven program (goxpyriment's MEG-localizer) presents a protocol,
  protocols/<name>.tsv; its protocol flag (the one whose default names a table: -p) becomes a
  drop-down of the tables.
- Planned duration. A program does not say how long it runs; its sources may. In this order:
  - `lengths`, by program name: a function of its flags' defaults and its source folder (go_lengths.py,
    whose LENGTHS are used by default: the expected length of a run, a timed screen its length, a
    random one its average, a screen waiting for a key READING_S, a screen that ends on an answer or
    that a key ends early answer_s), or an expression over its flags ("lead-in" written lead_in) and
    its default run's file (rows: its number of rows; a column's name: the column's sum);
  - a table-driven program's default protocol, from its rows' onsets and durations.
"""

import ast
import csv
import fnmatch
import operator
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from model.tasks import Category, Task
import math

READING_S = 25          # a screen that waits for a key press with no limit (instructions, the trigger), typically
ANSWER_S = 3            # a typical answer, or press of a key that ends a screen early (answer_s caps it at the limit)
SCREEN_RATIO = 9 / 16   # the stimulus screen's height over its width, for movements as large as it allows

NEXT = "next"   # a run choice: the one after the earlier blocks' (see GoTask.run_number)
FILLED = ("s", "subject", "subj", "sub", "participant")    # a flag that gets the session's subject
SKIPPED = {"h", "help", "version"}                          # flags that are no block parameter
SITE_FLAGS = ("w", "resolution", "d", "exclusive-fullscreen", "no-realtime", "realtime-priority", "center-shift-*",
              "magnification", "config", "headless", "photodiode*", "marker-frames", "ttl", "ttl-*", "button-ttl*",
              "*-key", "screen-width-cm", "viewing-distance-cm")   # goxpyriment's settings of the PC, and TTL codes
HELP_TIMEOUT_S = 5      # a program still running after this, asked for -h, does not use the flag package

FLAG = re.compile(r"  -(\S+)(?: (\S+))?(?:\t(.*))?")      # "  -name type", or "  -x<TAB>usage"
DEFAULT = re.compile(r" \(default (.*)\)\Z", re.DOTALL)     # the end of a flag's usage
ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}   # what a `lengths` expression may use


#
# Reading here a Go program to extract durations from the code.
# Durations are estimated as the expected length of a run: a timed screen at its length, a random one
# (drawn uniformly) at its average, a screen that ends on an answer or that a key may end early
# answer_s, a screen that waits for a key with no limit READING_S. A slow participant runs over.
# The end or results screen that waits for a key once the run is over is not counted: the run's data
# are saved, and the operator moves on.
#

def rows(path: Path) -> list[dict]:
    """A .csv or .tsv table's rows."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter="\t" if path.suffix == ".tsv" else ","))


def run_files(source: Path | None) -> dict[int, Path]:
    """A program's numbered run files, .csv or .tsv anywhere in its source folder, by run number
    (stimuli/arc_stimuli_run2.csv: 2)."""
    found: dict[int, Path] = {}
    for path in sorted(source.rglob("*")) if source and source.is_dir() else ():
        match = re.search(r"run[_-]?(\d+)$", path.stem, re.IGNORECASE)
        if match and path.suffix in (".csv", ".tsv") and path.is_file():
            found.setdefault(int(match[1]), path)
    return dict(sorted(found.items()))


def protocol_s(path: Path) -> float:
    """A goxpyriment protocol table's length (MEG-localizer, MEG-localizer2's protocols/*.tsv): when its
    last row ends, in s. A row lasts its duration (ms); a stream (*_STREAM) the durations and gaps of
    its "~"-separated items ("file:ms:gap"; an item without its own ms takes the row's)."""
    end = 0
    for row in rows(path):
        ms = int(row["duration"])
        if row["type"].endswith("_STREAM"):
            items = [item.split(":") for item in row["stimuli"].split("~")]
            ms = sum(int(p[1] if len(p) > 1 else ms) + int(p[2] if len(p) > 2 else 0) for p in items)
        end = max(end, int(row["onset_time"]) + ms)
    return end / 1000


def answer_s(limit_ms: float) -> float:
    """A screen that ends on the participant's answer, or that a key press may end early, within
    limit_ms: a typical ANSWER_S, or the limit if shorter, in s."""
    return min(ANSWER_S, limit_ms / 1000)


def mean_ms(flags: dict, low: str, high: str = "") -> float:
    """The average of a duration drawn uniformly from the flag low to the flag high (by default
    low-max: "iti" and "iti-max"), in ms."""
    return (flags[low] + flags[high or f"{low}-max"]) / 2


def go_duration_s(text: str) -> float:
    """A Go duration flag's value ("1m30s", "90s", "1.5h"; "" or "0": none), in s."""
    units = {"h": 3600, "m": 60, "s": 1, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}
    return sum(float(n) * units[u] for n, u in re.findall(r"([\d.]+)(h|ms|us|µs|ns|m|s)", text or ""))


def gv_seconds(path: Path) -> float:
    """A .gv movie's length (goxpyriment's GPU video format, played to its end): its frame count
    over its frame rate, from its header (little-endian: width, height, frame count as uint32, fps
    as float32)."""
    with Path(path).open("rb") as f:
        _, _, frames, fps = struct.unpack("<IIIf", f.read(16))
    return frames / fps


# --- the programs -------------------------------------------------------------------------------------
def protocol_localizer(flags: dict, source: Path) -> float:
    """MEG-localizer, MEG-localizer2: the instruction screen and the operator's T (neither with
    -skip-wait; the "instructions" protocol has only the first), the protocol (-p), then 0.5 s of
    fixation."""
    waits = 0 if flags["skip-wait"] else 1 if flags["p"] == "instructions" else 2
    return waits * READING_S + protocol_s(source / "protocols" / f"{flags['p']}.tsv") + 0.5


def flash_localizer(flags: dict, source: Path) -> float:
    """MEG-localizer3, Retinotopy-multifocal, MEG-retinotopy: the instructions (not with -autostart),
    the trigger (-wait-trigger); the habituation flashes and the trials (-trials; 0: one period, -p),
    each 0.217-0.317 s, 0.267 s on average. (MEG-retinotopy's arrows and sounds come on trial onsets,
    inside the trials.)"""
    flashes = flags["habituation"] + (flags["trials"] or flags["p"])
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + flashes * 0.267


def meg_arc(flags: dict, source: Path) -> float:
    """MEG-ARC, its longest run file (a row: a trial; -n: only the first n): per trial two training
    screens until the done key (answer_s of -train), the test screen until the answer (answer_s of
    -max-rt) and the feedback (-fb), and the run file's four jitters (s); the lead-in, lead-out and
    run score (-run-fb, 0: none); the instructions (not with -autostart), the trigger (-wait-trigger)."""
    def run_s(trials: list[dict]) -> float:
        trials = trials[:flags["n"]] if flags["n"] else trials
        ms = flags["lead-in"] + len(trials) * flags["fb"] + flags["lead-out"] + flags["run-fb"]
        jitters = sum(float(t[c]) for t in trials for c in ("jitter1", "jitter2", "jitter3", "iti_jitter"))
        return ms / 1000 + len(trials) * (2 * answer_s(flags["train"]) + answer_s(flags["max-rt"])) + jitters
    files = run_files(source)
    if not files:
        raise FileNotFoundError(f"no run files in {source}")
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + max(run_s(rows(path)) for path in files.values())


def reading_sentences(flags: dict, source: Path) -> float:
    """MEG-Reading-Sentences: the instructions (not with -autostart), the trigger
    (-wait-trigger); its largest run (the sentences and the nonword strings dealt into -nruns runs;
    -practice: -practice-n of each): a 2 s lead-in, then per trial a blank, 12 words and the probe
    (5.9 s) and a fixation of at most 1.5 s."""
    if flags["practice"]:
        trials = 2 * flags["practice-n"]
    else:
        trials = sum(math.ceil(len(rows(source / "stimuli" / name)) / flags["nruns"])
                     for name in ("sentences_shuffled.csv", "nonwords_shuffled.csv"))
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + 2 + trials * (5.9 + 1.5)


def eye_movements(flags: dict, source: Path) -> float:
    """MEG-eye-movements: the instructions and a rest screen before each block (not with
    -autostart); per block (-blocks) 0.3 s of blank before and 0.2 s after, and its frames:
    saccades: 1 s at the centre, then -sacc-passes passes over the 9 grid points, each for
    -sacc-dwell-min-ms to -sacc-dwell-max-ms (their average); lines: -lines-repeats passes over 8
    directions, each a fixation (-pursuit-fix-ms), out, a hold (-pursuit-hold-ms) and back at
    -pursuit-speed-deg, and a last fixation; circles: 4 circles (two of the full radius, two of half),
    each a fixation and -circle-cycles turns at -pursuit-speed-deg; sines: 2 axes, each a fixation and
    -sine-cycles cycles of 1/-sine-freq-hz. The lines and circles reach as far as the screen allows,
    at most -pursuit-amplitude-deg: counted as half its height (SCREEN_RATIO of -screen-width-cm, seen
    from -viewing-distance-cm)."""
    blocks = [name.strip() for name in flags["blocks"].split(",")]
    fix, hold = flags["pursuit-fix-ms"] / 1000, flags["pursuit-hold-ms"] / 1000
    speed = flags["pursuit-speed-deg"]
    half_h = (flags["screen-width-cm"] * SCREEN_RATIO / 2
              / (flags["viewing-distance-cm"] * math.tan(math.radians(1))))      # in degrees at the centre
    amp = min(flags["pursuit-amplitude-deg"] or half_h, half_h)
    seconds = {
        "saccades": 1 + flags["sacc-passes"] * 9 * mean_ms(flags, "sacc-dwell-min-ms", "sacc-dwell-max-ms") / 1000,
        "lines": flags["lines-repeats"] * 8 * (fix + 2 * amp / speed + hold) + fix,
        "circles": 4 * fix + flags["circle-cycles"] * 2 * math.pi * (2 * amp + 2 * amp / 2) / speed,
        "sines": 2 * (fix + flags["sine-cycles"] / flags["sine-freq-hz"]),
    }
    waits = 0 if flags["autostart"] else len(blocks) + 1
    return waits * READING_S + sum(0.5 + seconds[name] for name in blocks)


def intuitive_physics(flags: dict, source: Path) -> float:
    """MEG-Intuitive-Physics: the instructions (not with -autostart), the trigger (-wait-trigger); a
    1 s fixation, then -n movies each shown once in each condition, in blocks of -block-size trials,
    each block opened by its question (-instr); per trial a fixation of -iti to -iti-max (their
    average), the movie (180 frames at 30 fps: 6 s) and the response screen, which lasts -resp
    whatever the answer."""
    trials = 2 * flags["n"]
    ms = (1000 + trials // flags["block-size"] * flags["instr"]
          + trials * (mean_ms(flags, "iti") + 6000 + flags["resp"]))
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + ms / 1000


def left_right(flags: dict, source: Path) -> float:
    """MEG-left-right: the instructions (not with -autostart), the trigger
    (-wait-trigger); the run itself is -duration seconds of frames, the arrows inside it."""
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + flags["duration"]


def mental_rotation(flags: dict, source: Path) -> float:
    """MEG-Mental-Rotation, MEG-visual-search: the instructions (not with -autostart), the trigger
    (-wait-trigger); -n trials, each a fixation of -iti to -iti-max (their average), the pair (the
    search display) until the answer (answer_s of -max-rt), kept up until the feedback (-fb-delay to
    -fb-delay-max, their average, from the answer or the end of the window) and the feedback (-fb)."""
    ms = flags["n"] * (mean_ms(flags, "iti") + mean_ms(flags, "fb-delay") + flags["fb"])
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + ms / 1000 + flags["n"] * answer_s(flags["max-rt"])


def motor_localizer(flags: dict, source: Path) -> float:
    """MEG-motor-localizer: the instructions (not with -autostart), the trigger (-wait-trigger); a
    rest of -rest to -rest-max (their average), then -reps blocks of each of the -conditions, each its
    cue (-cue), the movement (-move) and a rest of -rest to -rest-max (frame-locked, in ms)."""
    blocks = flags["reps"] * len(flags["conditions"].split(","))
    ms = mean_ms(flags, "rest") + blocks * (flags["cue"] + flags["move"] + mean_ms(flags, "rest"))
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + ms / 1000


def movie(flags: dict, source: Path) -> float:
    """MEG-movie: the instructions (not with -autostart), the trigger
    (-wait-trigger); 1 s of fixation, the clips (-clips: numbers and ranges, "1-4,7"; empty: all) of
    the 8 parts of the film, or of the 3 practice clips (-practice), each played to its end, -gap
    between two, then 1 s of fixation. The clips are read from -movies (a relative path is from the
    root of the repository, two folders above the source folder): not in the repository, they must
    have been converted on this PC."""
    names = (["training_01_romance", "training_02_romance", "training_03_romance"] if flags["practice"]
             else [f"romance_{k:02d}" for k in range(1, 9)])
    order = []
    for item in (flags["clips"] or f"1-{len(names)}").split(","):
        first, _, last = item.strip().partition("-")
        order += range(int(first), int(last or first) + 1)
    folder = Path(flags["movies"])
    if not folder.is_absolute():
        folder = source.parent.parent / folder
    film = sum(gv_seconds(folder / f"{names[k - 1]}.gv") for k in order)
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + 1 + film + (len(order) - 1) * flags["gap"] / 1000 + 1


def theory_of_mind(flags: dict, source: Path) -> float:
    """MEG-theory-of-mind: the instructions (not with -autostart), the trigger (-wait-trigger); every
    story of its table (stimuli/theory_of_mind.csv, or theory_of_mind_practice.csv with -practice; a
    row each), each a fixation of -iti to -iti-max (their average), the story (-story), the statement
    until the answer (answer_s of -max-rt), kept up until the feedback (-fb-delay to -fb-delay-max,
    their average, from the answer or the end of the window) and the feedback (-fb)."""
    table = "theory_of_mind_practice.csv" if flags["practice"] else "theory_of_mind.csv"
    trials = len(rows(source / "stimuli" / table))
    ms = trials * (mean_ms(flags, "iti") + flags["story"] + mean_ms(flags, "fb-delay") + flags["fb"])
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + ms / 1000 + trials * answer_s(flags["max-rt"])


def verbal_working_memory(flags: dict, source: Path) -> float:
    """MEG-verbal-working-memory, MEG-spatial-working-memory: the instructions (not with -autostart),
    the trigger (-wait-trigger); -n trials, each a fixation of -iti to -iti-max (their average), the
    empty grid (-pre; spatial only), the 4 steps (-step), a retention of -delay to -delay-max (their
    average), the choice until the answer (answer_s of -max-rt), kept up until the feedback (-fb-delay
    to -fb-delay-max, their average, from the answer or the end of the window) and the feedback (-fb)."""
    ms = flags["n"] * (mean_ms(flags, "iti") + flags.get("pre", 0) + 4 * flags["step"] + mean_ms(flags, "delay")
                       + mean_ms(flags, "fb-delay") + flags["fb"])
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + ms / 1000 + flags["n"] * answer_s(flags["max-rt"])


WCST_SORT_S = 1.2       # a card sort, typically: quicker than ANSWER_S (a timed run took 8:30 for 128 trials)


def wcst(flags: dict, source: Path) -> float:
    """Wisconsin-Card-Sorting-Task: the instructions (not with -autostart), the trigger
    (-wait-trigger); -n trials. With -soa, each lasts exactly -soa. Self-paced (the default), each is
    a blank of -iti to -iti-max (their average), the card until the answer (no time limit: a sort,
    WCST_SORT_S), kept up until the feedback (-fb-delay to -fb-delay-max, their average) and the
    feedback (-fb).
    -max_duration (a Go duration, "10m") stops the trials, the one under way when it expires finishing
    at most a blank, or a delay and a feedback (self-paced), or a trial (-soa) later."""
    if flags["soa"] > 0:
        trials_s = flags["n"] * flags["soa"] / 1000
        tail_s = (flags["soa"] - flags["iti"]) / 1000
    else:
        trials_s = flags["n"] * ((mean_ms(flags, "iti") + mean_ms(flags, "fb-delay") + flags["fb"]) / 1000
                                 + WCST_SORT_S)
        tail_s = max(flags["iti-max"], flags["fb-delay-max"] + flags["fb"]) / 1000
    if limit_s := go_duration_s(flags["max_duration"]):
        trials_s = min(trials_s, limit_s + tail_s)
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + trials_s


def vcs(flags: dict, source: Path) -> float:
    """MEG-VCS, its longest run file (a row: a trial; -n: only the first n): per trial the cards until
    the answer (answer_s of -max-rt), the run file's fixation (jitter, s), the feedback (-fb) and the
    run file's fixation between trials (iti_jitter, s); the lead-in, lead-out and run score (-run-fb,
    0: none); the instructions (not with -autostart), the trigger (-wait-trigger)."""
    def run_s(trials: list[dict]) -> float:
        trials = trials[:flags["n"]] if flags["n"] else trials
        ms = flags["lead-in"] + len(trials) * flags["fb"] + flags["lead-out"] + flags["run-fb"]
        jitters = sum(float(t[c]) for t in trials for c in ("jitter", "iti_jitter"))
        return ms / 1000 + len(trials) * answer_s(flags["max-rt"]) + jitters
    files = run_files(source)
    if not files:
        raise FileNotFoundError(f"no run files in {source}")
    waits = (0 if flags["autostart"] else 1) + flags["wait-trigger"]
    return waits * READING_S + max(run_s(rows(path)) for path in files.values())


LENGTHS = {
    "MEG-ARC": meg_arc,
    "MEG-eye-movements": eye_movements,
    "MEG-Intuitive-Physics": intuitive_physics,
    "MEG-left-right": left_right,
    "MEG-Mental-Rotation": mental_rotation,
    "MEG-motor-localizer": motor_localizer,
    "MEG-movie": movie,
    "MEG-retinotopy": flash_localizer,
    "MEG-spatial-working-memory": verbal_working_memory,
    "MEG-theory-of-mind": theory_of_mind,
    "MEG-VCS": vcs,
    "MEG-verbal-working-memory": verbal_working_memory,
    "MEG-visual-search": mental_rotation,
    "Wisconsin-Card-Sorting-Task": wcst,
    "MEG-localizer": protocol_localizer,
    "MEG-localizer2": protocol_localizer,
    "MEG-localizer3": flash_localizer,
    "Retinotopy-multifocal": flash_localizer,
    "MEG-Reading-Sentences": reading_sentences,
}