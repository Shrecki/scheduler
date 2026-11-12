""" 
This adapter is tailored to extract options from the DBP_MTB codebase and transform it into tasks with option menus for the session timeline creation.
"""
import csv
import re
import sys
from pathlib import Path
 
from model.tasks import Category, Task
 
NEXT = "next"   # a run choice: the one after the earlier blocks' (see MtbTask.run_number)
 
 
class MtbTask(Task):
    """One battery: run.py, playing one of its runs."""
 
    def run_number(self, params: dict, before: list) -> str:
        """`run`, or for "next": 1 + the highest run of the earlier blocks of this battery and order,
        so blocks left on "next" play run 01, 02, 03... and a block that plays a run again shifts nothing.
        Written as the run files write it (run_03.tsv: "03")."""
        if params["run"] != NEXT:
            return params["run"]
        last = 0
        for block in before:
            p = {**self.defaults(), **block.params}
            if block.task == self.key and p.get("order") == params.get("order"):
                last = max(last, last + 1 if p["run"] == NEXT else int(p["run"]))
        return next((run for run in self.params["run"][1:] if int(run) == last + 1), f"{last + 1:02d}")
 
    def argv(self, params: dict, **context) -> list[str]:
        p = {**self.defaults(), **params}
        return super().argv({**p, "run": self.run_number(p, context["before"])}, **context)
 
 
  def mtb_to_task(root: Path | str, category: Category = Category.LOCALIZER_MRI,
                  duration_s: float = 300, startup_s: float = 0) -> list[Task]:
    """The batteries of a dbp_mtb checkout, as it is now: one task per run_files/<battery>/ that has
    run files. No checkout there: none.
 
    A battery's parameters:
    - order: its block orders (order_<k>/ folders), the first by default; only for a battery that has several.
    - run: "next" (see MtbTask.run_number), or one of its run numbers (those every order has).
    - no_ttl: start without waiting for the scanner trigger (run.py's --no-ttl, for practice).
    The subject is the session's. A block's planned duration is the battery's longest run (`duration_s`
    if no run file says); the block's clock starts at launch, so the wait for the trigger comes on top.
    """
    root = Path(root).expanduser()
    if not (root / "run.py").is_file() or not (root / "run_files").is_dir():
        print(f"dbp_mtb: no run.py and run_files/ at {root}: no battery tasks", file=sys.stderr)
        return []
    tasks: list[Task] = []
    for battery in sorted(d for d in (root / "run_files").iterdir() if d.is_dir()):
        orders = sorted((d for d in battery.glob("order_*") if d.is_dir()), key=_natural)
        runs = [_runs(folder) for folder in orders or [battery]]        # {number: run file}, per order
        numbers = sorted(set.intersection(*(set(r) for r in runs)))
        if not numbers:
            continue
        params = {"order": tuple(o.name.removeprefix("order_") for o in orders)} if orders else {}
        params.update(run=(NEXT, *numbers), no_ttl=False)
        command = ("python", "run.py", "{subject}", battery.name, *(["{order}"] if orders else []),
                   "--run", "{run}", "{no_ttl:--no-ttl}")
        longest = max((_length_s(path) for r in runs for path in r.values()), default=0)
        tasks.append(MtbTask(f"mtb_{battery.name}", f"Multi-Task Battery: {battery.name}", category, command,
                             params, duration_s=(longest or duration_s) + startup_s, project=root))
    return tasks
 
 
def _runs(folder: Path) -> dict[str, Path]:
    """A folder's run files, by run number as their names write it (run_03.tsv: "03")."""
    found = (re.fullmatch(r"run_(\d+)\.tsv", p.name) for p in folder.glob("run_*.tsv"))
    return {m[1]: folder / m[0] for m in found if m}
 
 
def _length_s(path: Path) -> float:
    """A run's length in seconds: the end_time of its last row (0 if the file cannot be read)."""
    try:
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        return float(rows[-1]["end_time"])
    except (OSError, IndexError, KeyError, TypeError, ValueError):
        return 0
 
 
def _natural(path: Path) -> list:
    """Sorts order_2 before order_10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name)]
