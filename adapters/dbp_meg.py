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
    whose LENGTHS are used by default: every screen as long as it can last, a screen waiting for a key
    READING_S), or an expression over its flags ("lead-in" written lead_in) and its default run's file
    (rows: its number of rows; a column's name: the column's sum);
  - a table-driven program's default protocol, from its rows' onsets and durations.
"""

import ast
import csv
import fnmatch
import operator
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from .go_adapter.go_lengths import LENGTHS, protocol_s, run_files, rows
from model.tasks import Category, Task
import math

READING_S = 30          # a screen that waits for a key press, counted this long at most

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
# Task definition and parsing!
#

@dataclass(frozen=True)
class GoTask(Task):
    """One program: its path, then its subject flag and the flags a block changes."""
    subject_flag: str = ""      # the flag that gets the session's subject ("s"), if it has one

    def in_project(self, argv: list[str]) -> list[str]:
        """A compiled program: run as it is, in its folder, never by uv (whatever Python environment
        the folder holds)."""
        return list(argv)

    def run_number(self, before: list) -> int:
        """For run "next": 1 + the highest run of the earlier blocks of this task, so blocks left on
        "next" play run 1, 2, 3... (a practice run 0 is chosen as such) and a run played again shifts nothing."""
        last = 0
        for block in before:
            if block.task == self.key:
                run = {**self.defaults(), **block.params}["run"]
                last = max(last, last + 1 if run == NEXT else int(run))
        return last + 1

    def argv(self, params: dict, **context) -> list[str]:
        p = {**self.defaults(), **params}
        words = list(self.command)
        if self.subject_flag:
            words.append(f"-{self.subject_flag}={_label(context['subject'])}")
        for name, default in self.defaults().items():
            value = p[name]
            if value == NEXT:
                value = self.run_number(context["before"])
            elif value == default:                  # only what the block changes
                continue
            words.append(f"-{name}={str(value).lower() if isinstance(value, bool) else value}")
        return words


def go_to_task(bin_dir: Path | str, names: list[str] | None = None, category: Category = Category.LOCALIZER_MEG,
               duration_s: float = 300, durations: dict | None = None, startup_s: float = 0,
               sources: Path | str | None = None, lengths: dict | None = None,
               hidden: tuple[str, ...] = SITE_FLAGS) -> list[Task]:
    """The programs in `bin_dir` (only `names`, if given), as they are now: one task each, run in the
    folder above bin/. Programs not there are left out, and said.

    Its key is the program's name in lower case with "_" for anything else than letters and digits
    ("MEG-localizer": "meg_localizer"), its label the program's name. `sources`: the folder of the
    programs' source folders (goxpyriment's examples/, DBP_MEG's cmd/). Its planned duration, plus
    `startup_s`: `durations` by key if given there; else its length, if it can be worked out: `lengths`
    by program name or key (go_lengths.LENGTHS, and yours, which win: functions or expressions); else its
    default protocol's length (table-driven programs); else `duration_s`. `hidden`:
    the flags not offered, as names or patterns ("ttl-*"); the program keeps its defaults for them.
    """
    bin_dir = Path(bin_dir).expanduser()
    if not bin_dir.is_dir():
        print(f"go programs: no folder {bin_dir}: no tasks from it", file=sys.stderr)
        return []
    if names is not None:
        programs = [bin_dir / name for name in names]
    else:
        programs = sorted(p for p in bin_dir.iterdir() if p.is_file() and os.access(p, os.X_OK))
    tasks: list[Task] = []
    for program in programs:
        if not (program.is_file() and os.access(program, os.X_OK)):
            print(f"go programs: no program {program}: left out", file=sys.stderr)
            continue
        usage = _usage(program, bin_dir.parent)
        if usage is None:
            print(f"go programs: {program.name} -h did not answer: its flags are not offered", file=sys.stderr)
        params = _flags(usage or "")
        subject = next((name for name in FILLED if name in params), "")
        params.pop(subject, None)
        key = re.sub(r"[^a-z0-9]+", "_", program.name.lower()).strip("_")
        folder = Path(sources).expanduser() / program.name if sources else None
        defaults = dict(params)                                                 # every flag, for lengths
        flags = {n.replace("-", "_"): v for n, v in params.items() if type(v) in (int, float)}  # for expressions
        params = {n: v for n, v in params.items() if not any(fnmatch.fnmatchcase(n, h) for h in hidden)}
        runs = run_files(folder)
        if runs and type(params.get("run")) is int:
            params["run"] = (NEXT, *runs)
        rule = next((r for table in (lengths or {}, LENGTHS) for n in (program.name, key) if (r := table.get(n))), None)
        if callable(rule):
            length = _worked_out(program.name, rule, defaults, folder)
        else:
            table = _table(runs.get(flags.get("run"), next(iter(runs.values()), None)))   # the default run's file
            length = _length(program.name, rule, {**table, **flags})
        protocol = _protocols(folder / "protocols", params) if folder else None
        run_s = (durations or {}).get(key) or length or protocol or duration_s
        tasks.append(GoTask(key, program.name, category, (str(program),), params, duration_s=run_s + startup_s,
                            project=bin_dir.parent, subject_flag=subject))
    if not tasks:
        print(f"go programs: none to offer in {bin_dir}", file=sys.stderr)
    return tasks


def _usage(program: Path, folder: Path) -> str | None:
    """What `program -h` prints (the flag package: on stderr, then it exits); None if it does not end."""
    try:
        done = subprocess.run([str(program), "-h"], cwd=folder, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=HELP_TIMEOUT_S, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout + done.stderr


def _flags(usage: str) -> dict:
    """The flags in a flag-package usage text, as parameters: {name: default}, in the program's order."""
    entries: list[list[str]] = []
    for line in usage.splitlines():
        if match := FLAG.fullmatch(line):
            entries.append([match[1], match[2] or "", match[3] or ""])
        elif entries and line.startswith("    \t"):             # the usage of the flag above
            entries[-1][2] += ("\n" if entries[-1][2] else "") + line[5:]
    params = {}
    for name, kind, text in entries:
        if name not in SKIPPED:
            default = DEFAULT.search(text)
            params[name] = _value(kind, default[1] if default else None)
    return params


def _value(kind: str, default: str | None):
    """A flag's default as a parameter of the right type ("" kind: a switch). None: Go's zero value."""
    try:
        if kind == "":
            return default == "true"
        if kind in ("int", "uint"):
            return int(default or 0)
        if kind == "float":
            return float(default or 0)
    except ValueError:
        return default or ""
    if default and default.startswith('"'):                     # strings are shown quoted, Go-style
        try:
            return str(ast.literal_eval(default))
        except (ValueError, SyntaxError):
            return default.strip('"')
    return default or ""


def _protocols(folder: Path, params: dict) -> float | None:
    """For a table-driven program (its protocols/*.tsv in `folder`): its protocol flag, the one whose
    default names a table, becomes a drop-down of the tables (default first); returns the default
    table's length in seconds. None (params unchanged) for any other program."""
    lengths = {path.stem: _or_none(protocol_s, path) for path in sorted(folder.glob("*.tsv"))}
    lengths = {name: seconds for name, seconds in lengths.items() if seconds}
    flag = next((name for name, value in params.items() if isinstance(value, str) and value in lengths), None)
    if flag is None:
        return None
    default = params[flag]
    params[flag] = (default, *(name for name in lengths if name != default))
    return lengths[default]


UNREADABLE = (OSError, KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, csv.Error)


def _or_none(length, *args) -> float | None:
    """A length, or None if it cannot be worked out (a table missing or broken...)."""
    try:
        return length(*args)
    except UNREADABLE:
        return None


def _worked_out(program: str, length, flags: dict, folder: Path | None) -> float | None:
    """A go_lengths function's value, in seconds; None, and said, if it cannot be worked out."""
    try:
        seconds = length(flags, folder)
    except UNREADABLE as exc:
        print(f"go programs: {program}: its length could not be worked out ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return None
    return seconds if seconds and seconds > 0 else None


def _table(path: Path | None) -> dict[str, float]:
    """A run file, for a `lengths` expression: rows (its number of rows), and the sum of each of its
    numeric columns, by name. Empty if there is none, or it cannot be read."""
    try:
        table = rows(path)
    except (TypeError, OSError, csv.Error):
        return {}
    sums = {}
    for column in table[0] if table else ():
        try:
            sums[column] = sum(float(row[column]) for row in table)
        except (TypeError, ValueError):
            pass
    return {**sums, "rows": len(table)}


def _length(program: str, expression: str | None, names: dict[str, float]) -> float | None:
    """A `lengths` expression's value, in seconds; None, and said, if it cannot be computed."""
    if not expression:
        return None
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        print(f"go programs: {program}: cannot read its length {expression!r} ({exc.msg})", file=sys.stderr)
        return None
    value = _number(tree.body, names)
    if value is None or value <= 0:
        unknown = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in names})
        print(f"go programs: {program}: its length cannot be computed"
              + (f" (no flag or run-file column {', '.join(unknown)})" if unknown else ""), file=sys.stderr)
        return None
    return value


def _number(node: ast.AST, names: dict[str, float]) -> float | None:
    """Arithmetic on numbers and known `names`; None for anything else (a name it does not know)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _number(node.operand, names)
        return None if value is None else -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC:
        left, right = _number(node.left, names), _number(node.right, names)
        try:
            return None if left is None or right is None else ARITHMETIC[type(node.op)](left, right)
        except ArithmeticError:
            return None
    return None


def _label(subject: str) -> str:
    """The subject as goxpyriment takes it: letters and digits only (sub-07: 07)."""
    return re.sub(r"[^A-Za-z0-9]", "", subject.removeprefix("sub-"))