"""
This adapter is tailored to the DBP_fMRI codebase (Python-based).
As is, the DBP_fMRI codebase is a repository of scripts, launched through [folder of task]/[task_name]_run.py --session [sessionNbr] --subject [subjectID] --run [runNbr].
This adapter extracts the arguments from the run scripts from which to build the parameters of experimental blocks automatically.
"""

import ast
import operator
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


from model.tasks import Category, Task

NEXT = "next"   # a run choice: the one after the earlier blocks' (see ScriptTask.run_number)
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
    """One run script: python -m <folder>.<prefix>_run, followed by its options."""
    options: tuple = ()     # (name, flag or "" for a positional, kind, filled-in field or ""), in the script's order

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
            value = context[field] if field else p[name]
            if kind in ("on", "off"):                       # store_true: the flag when ticked; store_false: when not
                words += [flag] if bool(value) == (kind == "on") else []
            elif value not in ("", None):
                words += [flag] if flag else []
                words += shlex.split(str(value)) if kind == "many" else [str(value)]
        return words


def dbp_mtb_to_task(root: Path | str, category: Category = Category.LOCALIZER_MRI, duration_s: float = 300,
                    durations: dict | None = None, length: str | tuple[str, ...] = (),
                    startup_s: float = 0) -> list[Task]:
    """The run scripts of a checkout, as it is now: one task per <folder>/<prefix>_run.py. None there: none.

    Its key is the prefix in lower case ("vcst"), its label the folder's name ("VCST"; "WCST (wcst_meg)"
    when the prefix is not the folder's name). `durations`: planned durations in seconds by key, for
    the scripts that differ from `duration_s`.
    """
    root = Path(root).expanduser()
    scripts = sorted(p for p in root.glob("*/*_run.py") if not p.parent.name.startswith((".", "_")))
    if not scripts:
        print(f"run scripts: no <folder>/<prefix>_run.py in {root}: no tasks from it", file=sys.stderr)
        return []
    tasks: list[Task] = []
    for script in scripts:
        folder, prefix = script.parent, script.stem.removesuffix("_run")
        if not (folder.name.isidentifier() and script.stem.isidentifier()):
            print(f"run scripts: {script.relative_to(root)} cannot be run with python -m (its name): left out",
                  file=sys.stderr)
            continue
        try:
            tree = ast.parse(script.read_text(encoding="utf-8"), str(script))
            options, params = _options(tree)
        except (OSError, SyntaxError, ValueError) as exc:
            print(f"run scripts: could not read {script.relative_to(root)} ({exc}): left out", file=sys.stderr)
            continue
        if "run" in params and not isinstance(params["run"], tuple) and (numbers := _run_numbers(folder)):
            params["run"] = (NEXT, *numbers)
        key = prefix.lower()
        label = folder.name if key == folder.name.lower() else f"{folder.name} ({prefix})"
        command = ("python", "-m", f"{folder.name}.{script.stem}")
        run_s = (durations or {}).get(key) or _length(tree, length) or duration_s
        tasks.append(DBPfMRITask(key, label, category, command, params,
                                duration_s=run_s + startup_s, project=root,
                                options=tuple(options)))
    return tasks


def _options(tree: ast.Module) -> tuple[list, dict]:
    """A script's argparse options, from its add_argument(...) calls in the order they come:
    (options as ScriptTask keeps them, the parameters a block gets)."""
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
        choices, nargs = _literal(given.get("choices")), _literal(given.get("nargs"))
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


def _literal(node, missing=None):
    """A value written as is in the source ("easy", 40, ["a", "b"]); `missing` for anything computed."""
    if node is None:
        return missing
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