"""fmri-gym, seen from the scheduler: its games as tasks, and its session scripts as a script format.

To the scheduler, a game is one more command to run (fmri-play). This file is the only one that
knows fmri-gym: task_definitions.py lists its tasks (GAME_TASKS = gym_to_task(...)), and main.py
gives its script format to the session (SessionScripts(...)).

- gym_to_task(root): reads an fmri-gym checkout and returns one GymTask per game family (its
  configs/curricula/<backend>/ folder), plus ScriptLine, for a line of a session script that is no
  fmri-play run.
- SessionScripts(root, paths=PATHS): the session as a session script in fmri-gym's format
  (fmri_gym/standalone/session_script.py), and back:

    #!/bin/sh
    # fmri-gym session: one line per run, in order.
    set -e
    FMRI_GYM_PATH=/home/me/fmri-gym
    DBP_MTB_PATH=/home/me/dbp_mtb
    SESSION_DIR=/home/me/data/sub-01/ses-002
    SUBJECT=sub-01
    RIG=configs/examples/rigs/laptop.json
    SES=${1:-002}
    (cd "$FMRI_GYM_PATH" && uv run fmri-play --curriculum configs/curricula/coom/all.json --subject "$SUBJECT" --ses "$SES" --run 1 --rig "$RIG" --duration 60 --loop --resume)
    mkdir -p "$SESSION_DIR/blocks/02_mtb" && (cd "$DBP_MTB_PATH" && . .venv/bin/activate && python run.py ...)  # scheduler: {...}

  One line per run, each run in its own folder, so that any line can be run on its own. The folders
  are set once at the top, by the names task_definitions.py gives them: to share the script, change
  those lines. A game is written as fmri-gym writes it, behind its `cd`. Any other run is written as
  what the scheduler runs, in its folder and environment, followed by a comment the shell ignores,
  from which read() gets the run back as it was. Read back, a line that is neither becomes a
  ScriptLine run. Commented-out runs are left out. Older scripts (one `cd` line of its own) read too.
"""

import argparse
import json
import re
import shlex
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from model.tasks import Category, Task

DEFAULT_RIG = "examples/rigs/laptop.json"   # under configs/: the rig offered first (keyboard, no scanner)
TEARDOWN_S = 5.0    # fmri-play's --duration is the block's minus this: time to close before the block ends
SWITCHES = (("loop", "--loop"), ("resume", "--resume"), ("dummy_trigger", "--dummy-trigger"),
            ("skip_load_games", "--skip-load-games"))   # fmri-play's, in the order fmri-gym writes them


def _ses(context: dict) -> int:
    return int(context["session"].removeprefix("ses-"))


# --- the games, as tasks -------------------------------------------------------------------------
@dataclass(frozen=True)
class GymTask(Task):
    """One game family: fmri-play, on one of the family's curricula."""
    backend: str = ""                   # its folder, configs/curricula/<backend>/

    def run_number(self, params: dict, before: list) -> int:
        """Which run of its curriculum this is (fmri-play's --run): 1 + the earlier runs of it."""
        return 1 + sum(b.task == self.key and b.params.get("curriculum") == params["curriculum"] for b in before)

    def play(self, params: dict, subject: str, ses: str, run: int, rig: str, block_s: float) -> list[str]:
        """fmri-play's arguments for one run of a block of `block_s` seconds."""
        p = {**self.defaults(), **params}
        words = ["--curriculum", f"configs/curricula/{self.backend}/{p['curriculum']}.json",
                 "--subject", subject, "--ses", ses, "--run", str(run), "--rig", rig]
        if not p["play_to_end"]:
            words += ["--duration", f"{max(block_s - TEARDOWN_S, 1):g}"]
        return words + [flag for name, flag in SWITCHES if p[name]]

    def argv(self, params: dict, **context) -> list[str]:
        p = {**self.defaults(), **params}
        return ["fmri-play", *self.play(p, context["subject"], str(_ses(context)), self.run_number(p, context["before"]),
                                        f"configs/{p['rig']}", float(context["duration_s"]))]


@dataclass(frozen=True)
class ScriptLine(Task):
    """A line of a session script that is no fmri-play run, run as the script runs it: in the fmri-gym
    folder, with $SUBJECT, $SES and $RIG set."""

    def argv(self, params: dict, **context) -> list[str]:
        p = {**self.defaults(), **params}
        return ["env", f"SUBJECT={context['subject']}", f"SES={_ses(context):03d}", f"RIG=configs/{p['rig']}",
                "sh", "-c", p["command"]]


def gym_to_task(root: Path | str, category: Category = Category.VIDEO_GAME, duration_s: float = 300,
                rig: str = DEFAULT_RIG, line_category: Category = Category.COGNITIVE_TASK_MEG) -> list[Task]:
    """The tasks of an fmri-gym checkout, as it is now. No checkout there: none.

    A GymTask's parameters:
    - curriculum: what its folder can play. Each single level ("levels/<name>") and each curriculum
      that plays several in a row ("<name>": "all", controller variants like "all-GMPD3-ABXY-nav").
    - rig: the rig files, configs/<team>/rigs/<rig>.json (`rig` first).
    - play_to_end: no --duration, fmri-play plays the curriculum to its end (the block's duration is
      then only the plan). Off: --duration is the block's duration minus TEARDOWN_S, so it has
      time to close before the block ends.
    - loop, resume, dummy_trigger, skip_load_games: fmri-play's switches (`fmri-play --help`).
    --subject and --ses are the session's; --run counts the earlier runs of the same curriculum, as
    fmri-gym's session scripts do. Inside a curriculum (phases, seeds, keys): fmri-gym's session-editor.
    """
    root = Path(root).expanduser()
    configs = root / "configs"
    rigs = sorted(str(p.relative_to(configs)) for p in configs.glob("*/rigs/*.json"))
    rigs = tuple(sorted(rigs, key=lambda r: r != rig))             # `rig` first, if there
    if not (configs / "curricula").is_dir() or not rigs:
        print(f"fmri-gym: no checkout with curricula and rig files at {root}: no game tasks", file=sys.stderr)
        return []
    tasks: list[Task] = []
    for folder in sorted(p for p in configs.glob("curricula/*") if p.is_dir()):
        files = [*sorted(folder.glob("*.json"), key=_natural), *sorted(folder.glob("levels/*.json"), key=_natural)]
        curricula = tuple(p.relative_to(folder).with_suffix("").as_posix() for p in files if _plays(p))
        if curricula:
            tasks.append(GymTask(
                f"fmrigym_{folder.name}", _title(folder), category, ("fmri-play",),
                {"curriculum": curricula, "rig": rigs, "play_to_end": False, "loop": False, "resume": False,
                 "dummy_trigger": False, "skip_load_games": False},
                duration_s=duration_s, project=root, backend=folder.name))
    tasks.append(ScriptLine("fmrigym_line", "Script line (fmri-gym)", line_category, ("sh", "-c"),
                            {"command": "", "rig": rigs}, duration_s=duration_s, project=root))
    return tasks


def _natural(path: Path) -> list:
    """Sorts game2 before game10, and "all" before its variants ("all-GMPD3-ABXY-nav")."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.stem)]


def _plays(path: Path) -> bool:
    """A curriculum fmri-play can be given: a JSON object with a "curriculum" list."""
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")).get("curriculum"), list)
    except (OSError, ValueError, AttributeError):
        return False


def _title(folder: Path) -> str:
    """The game family's name, from its README ("# Baba Is Auto curricula" -> "Baba Is Auto")."""
    for readme in (folder / "common" / "README.md", folder / "README.md"):
        if readme.is_file():
            first = readme.read_text(encoding="utf-8").splitlines()[:1]
            if first and first[0].startswith("# "):
                return first[0][2:].removesuffix(" curricula").strip()
    return folder.name


# --- the session, as a script ----------------------------------------------------------------------
HEADER = ["#!/bin/sh", "# fmri-gym session: one line per run, in order.", "set -e"]
PLAY = ["uv", "run", "fmri-play"]
HEAD_VARS = ('"$SUBJECT"', '"$SES"', '"$RIG"')      # written unquoted, for the shell to expand
MARK = "# scheduler:"
ASSIGN = re.compile(r"([A-Za-z_]\w*)=(.*)")
PINNED = re.compile(r"\$\{1:-(\d+)\}")              # SES=${1:-002}


class SessionScripts:
    """fmri-gym's session scripts: write(session) -> text, read(text) -> (design, note).
    `tasks`: all the tasks (task_definitions.TASKS), which a script's lines are read back as.
    `paths`: folders by name (task_definitions.PATHS; by default `root` as FMRI_GYM_PATH): a script sets
    those it uses at its top, with SESSION_DIR (the session's data folder), and its lines use them.

    Each line plays one run on its own, in its folder: (cd "$FMRI_GYM_PATH" && uv run fmri-play ...).
    fmri-gym's session editor reads those as commands; it edits only lines that start with uv run fmri-play."""

    def __init__(self, root: Path | str, tasks: list[Task], rig: str = DEFAULT_RIG, paths: dict | None = None):
        self.root, self.tasks, self.rig = Path(root).expanduser().absolute(), tasks, rig
        paths = paths or {"FMRI_GYM_PATH": self.root}
        self.paths = {name: Path(path).expanduser().absolute() for name, path in paths.items()}

    def _task(self, key: str) -> Task:
        return next(t for t in self.tasks if t.key == key)

    # writing
    def write(self, session) -> str:
        rigs = [b.params["rig"] for b in session.blocks if isinstance(self._task(b.task), GymTask)]
        rig = Counter(rigs).most_common(1)[0][0] if rigs else self.rig        # the script's RIG=
        paths = {**self.paths, "SESSION_DIR": session.session_dir.absolute()}
        used: dict[str, Path] = {}                       # the folders the lines use, set at the top
        word = lambda w: _shell_word(w, paths, used)     # noqa: E731
        lines = []
        for i, block in enumerate(session.blocks):
            task = self._task(block.task)
            if isinstance(task, GymTask):
                p = {**task.defaults(), **block.params}
                words = [*PLAY, *task.play(p, '"$SUBJECT"', '"$SES"', task.run_number(p, session.blocks[:i]),
                                           '"$RIG"' if p["rig"] == rig else f"configs/{p['rig']}",
                                           block.duration_ms / 1000)]
                play = " ".join(w if w in HEAD_VARS else shlex.quote(w) for w in words)
                lines.append(f"(cd {word(str(self.root))} && {play})")
            else:
                params = dict(block.params)
                if isinstance(task, ScriptLine):         # as typed, in fmri-gym's folder
                    line = f"(cd {word(str(self.root))} && {params.pop('command')})"
                else:
                    argv, outdir = session.command(i)
                    folder = Path(task.project or outdir).absolute()
                    venv = ". .venv/bin/activate && " if (folder / ".venv").is_dir() else ""
                    line = (f"mkdir -p {word(str(outdir))} && "
                            f"(cd {word(str(folder))} && {venv}{' '.join(word(w) for w in argv)})")
                mark = {"task": task.key, "duration_s": block.duration_ms / 1000, "parameters": params}
                lines.append(f"{line}  {MARK} {json.dumps(mark)}")
        head = [*HEADER, *(f"{name}={shlex.quote(str(path))}" for name, path in paths.items() if name in used),
                f"SUBJECT={shlex.quote(session.subject)}", f"RIG={shlex.quote('configs/' + rig)}",
                f"SES=${{1:-{session.session_number:03d}}}", "#RUN"]
        return "\n".join(head + lines) + "\n"

    # reading
    def read(self, text: str) -> tuple[dict, str]:
        """The runs of a script, as a design (what Session.load_design takes), and a note on what was
        left out. Variables it sets ($SUBJECT, $RIG, $RUN_DURATION...) are filled in; its subject and
        session are the scheduler's, not the script's."""
        values, runs, skipped, folder = {}, [], 0, self.root
        for line in (line.strip() for line in text.splitlines()):
            if not line or line in HEADER or line.startswith(("#!", "set ")):
                continue
            words = _words(line)
            if assign := ASSIGN.fullmatch(line):
                values[assign[1]] = _value(assign[2])
            elif line.startswith("#"):
                skipped += _play_args(_words(_unwrap(line[1:].strip(), values)[1]), values) is not None
            elif len(words) == 2 and words[0] == "cd":   # older scripts: where the next lines run
                folder = folder / _expand(words[1], values)
            elif (wrapped := _unwrap(line, values))[0] is not None and MARK not in line:
                here, command = wrapped                  # (cd FOLDER && command): this line only
                runs.append(self._run(command, _words(command), values, folder / here))
            else:
                runs.append(self._run(line, words, values, folder))
        return {"runs": runs}, f"{skipped} commented-out runs left out" if skipped else ""

    def _run(self, line: str, words: list[str], values: dict, folder: Path) -> dict:
        command, _, mark = line.partition(MARK)
        try:
            run = json.loads(mark) if mark else None
            task = self._task(run["task"])
        except (ValueError, KeyError, TypeError, StopIteration):
            run = task = None
        if task is not None:                             # written by write(): back as it was
            if isinstance(task, ScriptLine):             # without the (cd ... && ...) write() put it in
                command = command.strip()
                typed = _unwrap(command, values)[1] if command.startswith("(") else command
                run.setdefault("parameters", {})["command"] = typed
            return run
        args = _play_args(words, values)
        match = args and re.fullmatch(r"(?:\./)?configs/curricula/([^/]+)/(.+)\.json", args.curriculum)
        game = next((t for t in self.tasks if isinstance(t, GymTask) and match and t.backend == match[1]), None)
        if game and match[2] in game.choices("curriculum"):
            params = {"curriculum": match[2], "play_to_end": args.duration is None,
                      **{name: getattr(args, name) for name, _ in SWITCHES}}
            rig = (args.rig or "").removeprefix("./").removeprefix("configs/")
            if rig in game.choices("rig"):
                params["rig"] = rig                      # else (given at launch, or unknown): the default
            block_s = args.duration + TEARDOWN_S if args.duration else game.duration_s
            return {"task": game.key, "duration_s": block_s, "parameters": params}
        other = next((t for t in self.tasks if isinstance(t, ScriptLine)), None)
        if other is None:
            raise ValueError(f"no task for a line that is no fmri-gym run: {line}")
        here = "" if folder == self.root else f"cd {shlex.quote(str(folder))} && "
        rig = (values.get("RIG") or "").removeprefix("configs/")
        params = {"command": here + line, **({"rig": rig} if rig in other.choices("rig") else {})}
        return {"task": other.key, "duration_s": other.duration_s, "parameters": params}


def _shell_word(word: str, paths: dict[str, Path], used: dict) -> str:
    """`word` for the shell; a path in one of `paths` (the deepest that holds it) as "$NAME/...", for the
    shell to expand, NAME then added to `used`; also the value of a flag (-dir=PATH)."""
    flag, value = (word.split("=", 1) if re.fullmatch(r"-[\w-]+=.+", word) else ("", word))
    for name, path in sorted(paths.items(), key=lambda item: -len(str(item[1]))):
        root = str(path)
        if root != "/" and (value == root or value.startswith(root + "/")):
            used[name] = path
            rest = re.sub(r'([\\"$`])', r"\\\1", value[len(root):])
            return (shlex.quote(flag + "=") if flag else "") + f'"${name}{rest}"'
    return shlex.quote(word)


_WRAPPED = re.compile(r"cd (\"[^\"]*\"|'[^']*'|[^\s()]+) && (.+)")


def _unwrap(line: str, values: dict) -> tuple[Path | None, str]:
    """(folder, command) for a line run in a folder of its own, (cd FOLDER && command) or
    cd FOLDER && command, the folder's variables filled in; (None, line) for any other."""
    match = _WRAPPED.fullmatch(line[1:-1] if line[:1] + line[-1:] == "()" else line)
    folder = match and _expand((_words(match[1]) or [match[1]])[0], values)
    if not folder or "$" in folder:                     # a variable the script does not set
        return None, line
    return Path(folder), match[2]


def _expand(word: str, values: dict) -> str:
    """`word` with the variables the script set ($NAME, ${NAME}) filled in."""
    return re.sub(r"\$\{?(\w+)\}?", lambda m: values.get(m[1]) or m[0], word)


def _words(line: str) -> list[str]:
    try:
        return shlex.split(line)
    except ValueError:
        return []


def _value(raw: str) -> str | None:
    """A variable's value as the script sets it; None when it is given when the script is started."""
    if pinned := PINNED.fullmatch(raw):
        return pinned[1]
    if raw.startswith(("${", "$(")):
        return None
    return (_words(raw) or [raw])[0]


class _Parser(argparse.ArgumentParser):
    def error(self, message):                   # argparse would exit
        raise ValueError(message)


FLAGS = _Parser(add_help=False, allow_abbrev=False)
FLAGS.add_argument("--curriculum", required=True)
for _flag in ("--subject", "--ses", "--run", "--rig"):
    FLAGS.add_argument(_flag)
FLAGS.add_argument("--duration", type=float)
for _name, _flag in SWITCHES:
    FLAGS.add_argument(_flag, dest=_name, action="store_true")


def _play_args(words: list[str], values: dict) -> argparse.Namespace | None:
    """fmri-play's arguments, if the words play one run (`uv run fmri-play ...` or `fmri-play ...`)."""
    words = [_expand(word, values) for word in words]
    for prefix in (PLAY, PLAY[2:]):
        if words[:len(prefix)] == prefix:
            try:
                return FLAGS.parse_args(words[len(prefix):])
            except ValueError:
                return None
    return None