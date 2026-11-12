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
from .tasks import ROOT, Category, Task, app, view

FMRI_GYM_PATH = Path("~/DBP/fmri-gym").expanduser()   # change to yours
DBP_MTB_PATH = Path("~/DBP/dbp_mtb").expanduser()
DBP_FMRI_PATH = Path("~/DBP/dbp_mri").expanduser()
GOXPY_PATH = Path("~/DBP/goxpyriment").expanduser()              # change to yours
EYETRACKER_PATH = Path("~/DBP/eyelink-track").expanduser()        # change to yours

# Every game of the fmri-gym checkout: one task per game family, its levels and curricula as choices.
# `rig`: the rig file each run starts with (under configs/); the card offers the others.
GAME_TASKS = gym_to_task(FMRI_GYM_PATH, rig="examples/rigs/laptop.json")

COG_TASKS_MRI: list[Task] = dbp_mtb_to_task(DBP_FMRI_PATH, Category.COGNITIVE_TASK_MRI)
COG_TASKS_MEG: list[Task] = []
COG_TASKS: list[Task] = COG_TASKS_MRI + COG_TASKS_MEG


LOCALIZERS_MEG: list[Task] = [
    Task("multimodal_localizer", "MEG-localizer (multimodal localizer)", Category.LOCALIZER_MEG,
         app("go", "run", "./examples/MEG-localizer", "-ttl", "parallel:/dev/parport0"), duration_s=120,
         project=GOXPY_PATH),
    Task("finger_tone_localizer", "MEG-localizer2 (finger and tone)", Category.LOCALIZER_MEG,
         app("go", "run", "./examples/MEG-localizer2", "-ttl", "parallel:/dev/parport0"), duration_s=120,
         project=GOXPY_PATH),
    Task("multifocal_retinotopy", "MEG-localizer3 (multifocal retinotopy)", Category.LOCALIZER_MEG,
         app("go", "run", "./examples/MEG-localizer3", "-ttl", "parallel:/dev/parport0"), duration_s=120,
         project=GOXPY_PATH),
    Task("retinotopy", "Retinotopy", Category.LOCALIZER_MEG,
         app("go", "run", "./examples/Retinotopy-multifocal", "-w", "-s", "1", "-ttl", "parallel:/dev/parport0"),
         duration_s=120, project=GOXPY_PATH),
    # Task("smooth_pursuit", "Smooth Pursuit localizer", Category.LOCALIZER_MEG, app("/usr/games/gnome-sudoku"), duration_s=120),
]

LOCALIZERS_MRI: list[Task] =  mtb_to_task(DBP_MTB_PATH, category=Category.LOCALIZER_MRI)

EYETRACKERS: list[Task] = [
    Task("eyetracker_calibration", "EyeTracker calibration", Category.EYETRACKER_CALIBRATION,
         app("python", "-c", "from eyelink_track.commands.gui import run; run()"), duration_s=120,
         project=EYETRACKER_PATH),
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

TASKS = TASKS_DUMMY + COG_TASKS + LOCALIZERS_MEG + LOCALIZERS_MRI + GAME_TASKS + EYETRACKERS


def tasks_for(category: Category) -> list[Task]:
    return [t for t in TASKS if t.category == category]


def get_task(key: str) -> Task:
    task = next((t for t in TASKS if t.key == key), None)
    if task is None:
        raise KeyError(f"no task '{key}' in task_definitions.py")
    return task