"""The tasks a block can run. Edit this file to add or change tasks.

A task is a command to run (tasks.py says how to write one). A real task looks like:
    Task("mot", "MOT", Category.COGNITIVE_TASK, ("python", "run.py", "--speed", "{speed}", "--out", "{outdir}"),
         {"speed": 4.0}, project=Path.home() / "mot")
`project`: it runs in that folder, with the folder's .venv activated, never in the scheduler's environment.

DUMMY TASKS: small programs that come with Ubuntu, standing in for the real tasks. `timeout` ends
each one after the block's planned duration (exit code 124), as a real task would end itself.
Missing a program? sudo apt install gnome-sudoku gnome-mines gnome-chess aisleriot eog
"""

import os
from pathlib import Path

from adapters.fmri_gym import gym_to_task
from adapters.dbp_mtb import mtb_to_task
from adapters.dbp_fmri import dbp_mtb_to_task
from adapters.dbp_meg import go_to_task
from .tasks import ROOT, Category, Task, app, view, NEXT

FMRI_GYM_PATH = Path("~/DBP/fmri-gym").expanduser()   # change to yours
DBP_MTB_PATH = Path("~/DBP/dbp_mtb").expanduser()
DBP_FMRI_PATH = Path("~/DBP/dbp_mri").expanduser()
DBP_MEG_PATH = Path("~/DBP/dbp_meg").expanduser()
GOXPY_PATH = Path("~/DBP/goxpyriment").expanduser()              # change to yours
EYETRACKER_PATH = Path("~/DBP/eyelink-track").expanduser()        # change to yours

# Every game of the fmri-gym checkout: one task per game family, its levels and curricula as choices.
# `rig`: the rig file each run starts with (under configs/); the card offers the others.
GAME_TASKS = gym_to_task(FMRI_GYM_PATH, rig="examples/rigs/laptop.json")

COG_TASKS_MRI: list[Task] = dbp_mtb_to_task(DBP_FMRI_PATH, Category.COGNITIVE_TASK_MRI,startup_s=20,
                               length="MAX_RUN_DURATION + FINAL_FIXATION + RUN_FEEDBACK_DURATION")


COG_TASKS_MEG: list[Task] = go_to_task(DBP_MEG_PATH / "bin", ["MEG-Reading-Sentences", "MEG-ARC", "MEG-Intuitive-Physics", "MEG-Mental-Rotation", "MEG-spatial-working-memory", "MEG-VCS", "MEG-verbal-working-memory", "MEG-visual-search", "Wisconsin-Card-Sorting-Task", "MEG-theory-of-mind"],
                                        category=Category.COGNITIVE_TASK_MEG, duration_s=120, sources=DBP_MEG_PATH / "cmd")


COG_TASKS: list[Task] = COG_TASKS_MRI + COG_TASKS_MEG


LOCALIZERS_MEG: list[Task] = go_to_task(DBP_MEG_PATH / "bin", ["MEG-eye-movements", "MEG-left-right", "MEG-retinotopy"],
                                        category=Category.LOCALIZER_MEG, duration_s=120, sources=DBP_MEG_PATH / "cmd")

LOCALIZERS_MRI: list[Task] =  mtb_to_task(DBP_MTB_PATH, category=Category.LOCALIZER_MRI)


EYETRACKERS: list[Task] = [
    Task("eyetrack_calibration_meg", "EyeTrack Calibration (MEG)", Category.EYETRACKER_CALIBRATION,
         ("eyetrack_calibration", "--subject", "{subject}", "--session", "{ses:02d}", "--run", "{run:02d}",
          "{mock:--mock}", "{no_lpt:--no-lpt}", "--loopback", "{loopback}", "--photodiode",
          "--screen", "{screen}"),
         {"run": (NEXT, 1, 2, 3, 4), "mock": False, "no_lpt": False, "loopback": 3, "photodiode": True, "screen": 1},
         duration_s=120, project=EYETRACKER_PATH),
    Task("eyetrack_calibration_meg_mri", "EyeTrack Calibration (MRI)", Category.EYETRACKER_CALIBRATION,
             ("eyetrack_calibration", "--subject", "{subject}", "--session", "{ses:02d}", "--run", "{run:02d}",
              "{mock:--mock}", "{no_lpt:--no-lpt}", "--loopback", "{loopback}",
              "--screen", "{screen}"),
             {"run": (NEXT, 1, 2, 3, 4), "mock": False, "no_lpt": False, "loopback": 3, "photodiode": True, "screen": 1},
             duration_s=120, project=EYETRACKER_PATH),
]

TASKS_DUMMY: list[Task] = [
    Task("sudoku", "Sudoku", Category.COGNITIVE_TASK_MRI, app("/usr/games/gnome-sudoku"), duration_s=120),
    Task("mines", "Minesweeper", Category.COGNITIVE_TASK_MEG, app("/usr/games/gnome-mines", "--{size}"),
         {"size": ("small", "medium", "big")}, duration_s=120),
    Task("fixation", "Fixation cross", Category.RESTING_STATE,
         app(*view(ROOT / "assets" / "fixation.svg", "--fullscreen")), duration_s=60),
    Task("slideshow", "Wallpaper slideshow", Category.MOVIE_WATCHING,
         app(*view("{folder}", "--slide-show")), {"folder": "/usr/share/backgrounds"}, duration_s=90),
    Task("checkerboard_meg", "Checkerboard", Category.LOCALIZER_MEG,
         app(*view(ROOT / "assets" / "checkerboard.gif", "--fullscreen")), duration_s=60),
]

TASKS = COG_TASKS + LOCALIZERS_MEG + LOCALIZERS_MRI + GAME_TASKS + EYETRACKERS
PATHS = {name: value for name, value in dict(globals()).items() if name.endswith("_PATH") and isinstance(value, Path)}

def tasks_for(category: Category) -> list[Task]:
    return [t for t in TASKS if t.category == category]


def get_task(key: str) -> Task:
    task = next((t for t in TASKS if t.key == key), None)
    if task is None:
        raise KeyError(f"no task '{key}' in task_definitions.py")
    return task