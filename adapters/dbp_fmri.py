"""dbp_mri (DBP's fMRI cognitive tasks, in Python), seen from the scheduler: its tasks as tasks.

dbp_mri runs every task through one script, run.py, which names them in its `task` argument:

    python run.py ARC --run 01 --subject sub-07 --session ses-02 [--rig <rig file> --controls <controls file>]

Nothing of dbp_mri is imported or run to find them: run.py's argparse options are read from its
source, each one a block parameter or a value the session fills in (FILLED: --subject, --session), and
each choice of its `task` argument (a positional with choices: ["ARC", "VCST", "WCST"]) is a task. Its
module, <Folder>/<task>_run.py (ARC/arc_run.py, VCS/vcst_run.py, WCS/wcst_run.py), gives
- its runs: "next" or one of the run numbers in its folder's file names (DBPfMRITask.run_number);
- its planned duration: `length`, an expression the module's constants compute
  ("MAX_RUN_DURATION + FINAL_FIXATION + RUN_FEEDBACK_DURATION").
Rig and controls: given fmri-gym's rig files (`rigs`, task_definitions' RIGS) and dbp_mri's controls/
folder, a block passes --rig (the session's rig unless it picks another: the rig file) and --controls
(its button mapping, one of controls/<task>*.json: arc.json, arc-GMPD3-ABXY-nav.json; the plainest
first), which run.py takes both or neither. Without them, run.py keeps config.py's screen and keys.
"""

import ast
import operator
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from model.tasks import SESSION_RIG, Category, Task

NEXT = "next"   # a run choice: the one after the earlier blocks' (see DBPfMRITask.run_number)
FILLED = {      # options the session fills in, by name: the scheduler's value they get
    "subject": "subject", "subj": "subject", "subj_id": "subject", "sub": "subject", "participant": "subject",
    "session": "session", "ses": "session",                       # as "ses-02"
    "outdir": "outdir", "out_dir": "outdir", "output_dir": "outdir",
    "duration": "duration_s",
}
SKIPPED = {"help", "version", "count", "store_const", "append_const"}   # actions that take no value from a block
ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}   # what a script's constants may use


@dataclass(frozen=True)
class DBPfMRITask(Task):
    """One task of run.py: python run.py <TASK>, followed by its options."""
    options: tuple = ()     # (name, flag or "" for a positional, kind, filled-in field or ""), in run.py's order

    def run_number(self, before: list) -> int:
        """For run "next": 1 + the highest run of the earlier blocks of this task, so blocks left on
        "next" play run 1, 2, 3... and a block that plays a run again shifts nothing."""
        last = 0
        for block in before:
            if block.task == self.key:
                run = {**self.defaults(), **block.params}["run"]
                last = max(last, last + 1 if run == NEXT else int(run))
        return last + 1

    def argv(self, params: dict, **context) -> list[str]:
        p = {**self.defaults(), **params}
        if p.get("run") == NEXT:                            # written as the choices are ("01")
            n = self.run_number(context["before"])
            p["run"] = next((run for run in self.params["run"][1:] if int(run) == n), f"{n:02d}")
        words = list(self.command)
        for name, flag, kind, field in self.options:
            value = (context[field] if field else context["rig_file"] if name == "rig"   # the block's rig (Session)
                     else f"controls/{p[name]}.json" if name == "controls" else p[name])
            if kind in ("on", "off"):                       # store_true: the flag when ticked; store_false: when not
                words += [flag] if bool(value) == (kind == "on") else []
            elif value not in ("", None):
                words += [flag] if flag else []
                words += shlex.split(str(value)) if kind == "many" else [str(value)]
        return words


def dbp_fmri_to_task(root: Path | str, category: Category = Category.LOCALIZER_MRI, duration_s: float = 300,
                     durations: dict | None = None, length: str | tuple[str, ...] = (), startup_s: float = 0,
                     rigs: tuple[str, ...] = ()) -> list[Task]:
    """The tasks of a dbp_mri checkout, as it is now: one per choice of run.py's `task` argument. No
    run.py there: none.

    Its key is the task's name in lower case ("vcst"), its label the name ("VCST"). `durations`:
    planned durations in seconds by key, for the tasks whose `length` the constants cannot compute
    (else `duration_s`); `startup_s` is added to each.
    """
    root = Path(root).expanduser()
    try:
        tree = ast.parse((root / "run.py").read_text(encoding="utf-8"), str(root / "run.py"))
        options, params = _options(tree, _assigned(tree))
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"dbp_mri: could not read {root / 'run.py'} ({exc}): no tasks from it", file=sys.stderr)
        return []
    which = next((name for name, flag, _, _ in options if not flag and isinstance(params.get(name), tuple)), None)
    if which is None:
        print(f"dbp_mri: {root / 'run.py'} has no task argument (a positional with choices): no tasks", file=sys.stderr)
        return []
    names = [name for name in params.pop(which) if name]
    options = [option for option in options if option[0] != which]       # the task's name: in its command
    controls = sorted(p.stem for p in (root / "controls").glob("*.json"))
    tasks: list[Task] = []
    for name in names:
        key, task_params, task_options = name.lower(), dict(params), list(options)
        module = next(iter(sorted(root.glob(f"*/{key}_run.py"))), None)
        if "run" in task_params and not isinstance(task_params["run"], tuple) and module and (numbers := _run_numbers(module.parent)):
            task_params["run"] = (NEXT, *numbers)
        mine = sorted((c for c in controls if c == key or c.startswith(key + "-")), key=lambda c: (c != key, c.lower()))
        if rigs and mine and {"rig", "controls"} <= set(task_params):
            task_params.update(rig=(SESSION_RIG, *rigs), controls=tuple(mine))
        else:                                               # both or neither: run.py's config.py instead
            if rigs and {"rig", "controls"} <= set(task_params):
                print(f"dbp_mri: no controls/{key}*.json at {root}: {name} runs without --rig", file=sys.stderr)
            task_params = {n: v for n, v in task_params.items() if n not in ("rig", "controls")}
            task_options = [o for o in task_options if o[0] not in ("rig", "controls")]
        try:
            run_s = (durations or {}).get(key) or (module and _length(ast.parse(module.read_text(encoding="utf-8")), length))
        except (OSError, SyntaxError):
            run_s = None
        tasks.append(DBPfMRITask(key, name, category, ("python", "run.py", name), task_params,
                                 duration_s=(run_s or duration_s) + startup_s, project=root, options=tuple(task_options)))
    return tasks


def _assigned(tree: ast.Module) -> dict:
    """run.py's top-level names given a value written as is (tasks = ["ARC", "VCST", "WCST"])."""
    return {node.targets[0].id: value for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and (value := _literal(node.value)) is not None}


def _options(tree: ast.Module, known: dict | None = None) -> tuple[list, dict]:
    """A script's argparse options, from its add_argument(...) calls in the order they come:
    (options as DBPfMRITask keeps them, the parameters a block gets). `known`: values its options may
    name (choices=tasks)."""
    calls = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"),
                   key=lambda node: (node.lineno, node.col_offset))
    options, params = [], {}
    for call in calls:
        names = [_literal(arg) for arg in call.args]
        given = {k.arg: k.value for k in call.keywords if k.arg}
        action = _literal(given.get("action"), "store")
        if not names or not all(isinstance(n, str) for n in names) or action in SKIPPED:
            continue
        flag = next((n for n in names if n.startswith("--")), names[0])
        positional = not flag.startswith("-")
        name = _literal(given.get("dest")) or flag.lstrip("-").replace("-", "_")
        default = _literal(given.get("default"))                    # None: none, or set in code
        choices, nargs = _literal(given.get("choices"), names=known), _literal(given.get("nargs"))
        number = given["type"].id if isinstance(given.get("type"), ast.Name) else None
        if action in ("store_true", "store_false"):
            kind, value = ("on", False) if action == "store_true" else ("off", True)
        else:
            kind = "many" if nargs not in (None, "?", 1) else "one"
            if kind == "one" and isinstance(choices, (list, tuple)) and choices:
                choices = tuple(choices)
                value = ((default, *(c for c in choices if c != default)) if default in choices
                         else choices if positional else ("", *choices))
            elif kind == "one" and isinstance(default, (int, float)) and not isinstance(default, bool):
                value = float(default) if number == "float" else default
            elif isinstance(default, (list, tuple)):
                value = shlex.join(map(str, default))
            else:
                value = "" if default is None else str(default)
        field = FILLED.get(name, "") if kind in ("one", "many") else ""
        options.append((name, "" if positional else flag, kind, field))
        if not field:
            params[name] = value
    return options, params


def _literal(node, missing=None, names: dict | None = None):
    """A value written as is in the source ("easy", 40, ["a", "b"]), or a name of `names`; `missing` for
    anything computed."""
    if node is None:
        return missing
    if isinstance(node, ast.Name) and node.id in (names or {}):
        return names[node.id]
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return missing


def _length(tree: ast.Module, expressions: str | tuple[str, ...]) -> float | None:
    """A run's length in seconds: the first of `expressions` the script's constants can compute."""
    constants = _constants(tree)
    for expression in [expressions] if isinstance(expressions, str) else expressions:
        value = _number(ast.parse(expression, mode="eval").body, constants)
        if value is not None and value > 0:
            return value
    return None


def _constants(tree: ast.Module) -> dict[str, float]:
    """The script's numeric constants, as its top-level assignments compute them, in order
    (FINAL_FIXATION = 5.0; MAX_RUN_DURATION = 7 * 60.0 - FINAL_FIXATION: 415.0). A name given a value
    computed any other way (a call, a config value) is left out."""
    values: dict[str, float] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name):
            number = _number(value, values)
            if number is None:
                values.pop(target.id, None)
            else:
                values[target.id] = number
    return values


def _number(node: ast.AST, names: dict[str, float]) -> float | None:
    """Arithmetic on numbers and known `names` (7 * 60.0 - FINAL_FIXATION); None for anything else."""
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
        except ArithmeticError:         # a division by zero, say
            return None
    return None


def _run_numbers(folder: Path) -> list[str]:
    """The run numbers in the folder's file names (resources/vcst_stimuli_run2.csv: 2), data/ aside."""
    found = (re.search(r"run[_-]?(\d+)", path.stem, re.IGNORECASE) for path in folder.rglob("*")
             if "data" not in path.relative_to(folder).parts and path.is_file())
    return sorted({m[1] for m in found if m}, key=int)