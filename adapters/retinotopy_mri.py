"""The HCP 7T retinotopy (DBP-hcp-7T-retinotopy-mapping-Psychopy), seen from the scheduler: one task,
retinotopy_scanner.py playing one of its six runs.

    <retinotopy>/retinotopy_scanner.py --no-dialog --subject 7 --run 3 --rig <rig file> --controls controls/gmpd3.json

A run is a fixed schedule of 15 Hz steps, its column of assets/StimuliOrder/maskOrderRetinotopy.csv:
4,500 steps, 300 s, fixation baseline and instructions included. It starts on the scanner's trigger
(the rig's sync key; VPixx's DIN with --vpixx) or the operator's start key, once it has loaded its
images and measured the display's refresh: those seconds, and the wait for the scanner, come on top
(startup_s).
"""
import csv
import re
import sys
from pathlib import Path

from model.tasks import NEXT, SESSION_RIG, Category, Task

RUNS = ("RETBAR1", "RETBAR2", "RETCCW", "RETCW", "RETEXP", "RETCON")   # --run 1-6, as the program names them
RATE_HZ = 15                                                           # its steps per second


class RetinotopyTask(Task):
    """retinotopy_scanner.py, whose --subject is a number: the session subject's digits (sub-07: 7; none: 0)."""

    def argv(self, params: dict, **context) -> list[str]:
        digits = re.sub(r"\D", "", context["subject"])
        return super().argv(params, **context, subject_number=str(int(digits or 0)))


def retinotopy_to_task(root: Path | str, category: Category = Category.LOCALIZER_MRI, duration_s: float = 300,
                       startup_s: float = 0, rigs: tuple[str, ...] = ()) -> list[Task]:
    """The retinotopy of a checkout, as it is now: one task. No checkout there: none.

    Its parameters:
    - run: "next" (1 + the highest run of the earlier blocks), or 1-6: RETBAR1, RETBAR2, RETCCW, RETCW,
      RETEXP, RETCON (the scanner's RET1-RET6 are runs 3, 4, 5, 6, 1, 2: its README).
    - rig, given fmri-gym's rig files (`rigs`, task_definitions' RIGS): the session's unless the block
      picks another (--rig: the rig file, which sets the display, the screen's geometry, the scanner's
      key and the output folder, data_root).
    - controls: one of controls/*.json (--controls): "keyboard" first, the program's own default, which
      any rig can play; "gmpd3" needs a rig whose inputs have a GMPD3 (the program refuses it otherwise).
    - vpixx: start on VPixx's DIN trigger rather than the rig's key (--vpixx).
    The subject is the session's. --no-dialog: nothing to answer before the run. A block's planned
    duration is the longest run (`duration_s` if the schedule cannot be read) plus `startup_s`.
    """
    root = Path(root).expanduser()
    order = root / "assets" / "StimuliOrder" / "maskOrderRetinotopy.csv"
    if not (root / "retinotopy_scanner.py").is_file() or not order.is_file():
        print(f"retinotopy: no retinotopy_scanner.py and assets/StimuliOrder/ at {root}: no retinotopy task",
              file=sys.stderr)
        return []
    controls = tuple(sorted((p.stem for p in (root / "controls").glob("*.json")), key=lambda n: (n != "keyboard", n.lower())))
    params = {"run": (NEXT, 1, 2, 3, 4, 5, 6), **({"rig": (SESSION_RIG, *rigs)} if rigs else {}),
              **({"controls": controls} if controls else {}), "vpixx": False}
    command = ("python", "retinotopy_scanner.py", "--no-dialog", "--subject", "{subject_number}", "--run", "{run}",
               *(["--rig", "{rig_file}"] if rigs else []), *(["--controls", "controls/{controls}.json"] if controls else []),
               "{vpixx:--vpixx}")
    return [RetinotopyTask("retinotopy", "Retinotopy (MRI)", category, command, params,
                           duration_s=(_longest_s(order) or duration_s) + startup_s, project=root)]


def _longest_s(order: Path) -> float:
    """The longest run's length in s: its column's steps at RATE_HZ (0 if the schedule cannot be read)."""
    try:
        with order.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return max(sum(row.get(run) not in (None, "") for row in rows) for run in RUNS) / RATE_HZ
    except (OSError, ValueError):
        return 0