"""The tasks a block can run. Edit this file to add or change tasks.

A task is a command to run (tasks.py says how to write one). A real task looks like:
    Task("mot", "MOT", Category.COGNITIVE_TASK, ("python", "run.py", "--speed", "{speed}", "--out", "{outdir}"),
         {"speed": 4.0}, project=Path.home() / "mot")
`project`: it runs in that folder, in the folder's Python environment if it has one (.venv, by uv:
tasks.py, Task.in_project), never in the scheduler's; a session script runs it the same way.

DUMMY TASKS: small programs that come with Ubuntu, standing in for the real tasks. app()'s `timeout`
ends each one after the block's planned duration (exit code 124), as a real task would end itself.
Missing a program? sudo apt install gnome-sudoku gnome-mines gnome-chess aisleriot eog
"""

import os
from pathlib import Path

from adapters.fmri_gym import gym_to_task, rig_names
from adapters.dbp_mtb import mtb_to_task
from adapters.dbp_fmri import dbp_fmri_to_task
from adapters.dbp_meg import go_to_task
from .tasks import ROOT, SESSION_RIG, Category, Recording, Task, app, uv, view, with_recording, NEXT
from adapters.retinotopy_mri import retinotopy_to_task

RETINOTOPY_PATH = Path("~/DBP/DBP-hcp-7T-retinotopy-mapping-Psychopy").expanduser()   # change to yours
FMRI_GYM_PATH = Path("~/DBP/fmri-gym").expanduser()   # change to yours
DBP_MTB_PATH = Path("~/DBP/dbp_mtb").expanduser()
DBP_FMRI_PATH = Path("~/DBP/dbp_mri").expanduser()
DBP_MEG_PATH = Path("~/DBP/dbp_meg").expanduser()
GOXPY_PATH = Path("~/DBP/goxpyriment").expanduser()              # change to yours
EYETRACKER_PATH = Path("~/DBP/eyelink-track").expanduser()        # change to yours
DATA_DIR = (ROOT / "output").absolute()                           # the eyetracker's data: change to yours

RIGS_DIR = (FMRI_GYM_PATH / "configs").absolute()
RIGS = rig_names(RIGS_DIR)
RIG = (SESSION_RIG, *RIGS)
DEFAULT_RIG = "examples/rigs/laptop.json"           # change to yours

RECORD_EYES = True
EYES = Recording(("python", "eyetrack_record.py", "--run-simultaneous"), ready="Waiting for the trigger",
                 args=("--subject", "{subject}", "--session", "{ses}", "--run", "{run}", "--task", "{task}",
                       "--directory", f"{DATA_DIR}/{{subject}}/ses-{{ses}}"),
                 name="recording", project=EYETRACKER_PATH, log="eyetracker.log",
                 startup_s=10, stop_grace_s=10) if RECORD_EYES else None

# Every game of the fmri-gym checkout: one task per game family, its levels and curricula as choices.
GAME_TASKS = with_recording(gym_to_task(FMRI_GYM_PATH), EYES)

# Every task of dbp_mri's run.py (ARC, VCST, WCST): its options as parameters, the rig and its
# controls/ (button mappings) included.
COG_TASKS_MRI: list[Task] = with_recording(dbp_fmri_to_task(DBP_FMRI_PATH, Category.COGNITIVE_TASK_MRI, startup_s=20, rigs=RIGS,
                                             length="MAX_RUN_DURATION + FINAL_FIXATION + RUN_FEEDBACK_DURATION"), EYES)

COG_TASKS_MRI = COG_TASKS_MRI + [Task("Syllogism", "Syllogism", Category.COGNITIVE_TASK_MRI,
         ("retinotopy", "--subject", "{subject}", "--session", "{ses:02d}", "--run", "{run:02d}", "--photodiode",
          "--screen", "{screen}"),
         {"run": (NEXT, 1, 2, 3, 4), "photodiode": False, "screen": 1},
         duration_s=420, project=EYETRACKER_PATH)]

COG_TASKS_MEG: list[Task] = with_recording(go_to_task(DBP_MEG_PATH / "bin", ["MEG-Reading-Sentences", "MEG-ARC", "MEG-Intuitive-Physics", "MEG-Mental-Rotation", "MEG-spatial-working-memory", "MEG-VCS", "MEG-verbal-working-memory", "MEG-visual-search", "MEG-WCS", "MEG-theory-of-mind"],
                                        category=Category.COGNITIVE_TASK_MEG, duration_s=120, sources=DBP_MEG_PATH / "cmd"), EYES)

MOVIE_MEG: list[Task] = with_recording(go_to_task(DBP_MEG_PATH / "bin", ["MEG-movie"],
                                        category=Category.MOVIE_WATCHING, duration_s=120, sources=DBP_MEG_PATH / "cmd"), EYES)

COG_TASKS: list[Task] = COG_TASKS_MRI + COG_TASKS_MEG


LOCALIZERS_MEG: list[Task] = with_recording(go_to_task(DBP_MEG_PATH / "bin", ["MEG-eye-movements", "MEG-left-right", "MEG-retinotopy", "MEG-motor-localizer", "MEG-localizer", "MEG-localizer2", "MEG-localizer3","Retinotopy-multifocal", "MEG-left-right", "MEG-button-press", "MEG-freeviewing"],
                                        category=Category.LOCALIZER_MEG, duration_s=120, sources=DBP_MEG_PATH / "cmd"), EYES)

LOCALIZERS_MEG = LOCALIZERS_MEG + with_recording(mtb_to_task(DBP_MTB_PATH, category=Category.LOCALIZER_MEG, rigs=RIGS), EYES)
LOCALIZERS_MRI: list[Task] =  with_recording(mtb_to_task(DBP_MTB_PATH, category=Category.LOCALIZER_MRI, rigs=RIGS), EYES)
LOCALIZERS_MRI = LOCALIZERS_MRI + with_recording(retinotopy_to_task(RETINOTOPY_PATH, Category.LOCALIZER_MRI, startup_s=20, rigs=RIGS), EYES)


EYETRACKERS: list[Task] = [
     Task("eyetrack_calibration_meg", "EyeTrack Calibration (MEG)", Category.EYETRACKER_CALIBRATION,
         uv(EYETRACKER_PATH, "python", "eyetrack_calibration.py", "--subject", "{subject}", "--session", "{ses:02d}",
            "--run", "{run:02d}", "--photodiode", "--screen", "{screen}"),
         {"run": (NEXT, 1, 2, 3, 4), "photodiode": True, "screen": 1},
         duration_s=120),
     Task("eyetrack_calibration_mri", "EyeTrack Calibration (MRI)", Category.EYETRACKER_CALIBRATION,
         uv(EYETRACKER_PATH, "python", "eyetrack_calibration.py", "--subject", "{subject}", "--session", "{ses:02d}",
            "--run", "{run:02d}", "--photodiode", "--screen", "{screen}"),
         {"run": (NEXT, 1, 2, 3, 4), "photodiode": False, "screen": 1},
         duration_s=120),
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

FIELDMAPS: list[Task] = [Task("fieldmap", "Fieldmap", Category.FIELDMAP, "", duration_s=30)]

TASKS = COG_TASKS + LOCALIZERS_MEG + LOCALIZERS_MRI + GAME_TASKS + EYETRACKERS + MOVIE_MEG + FIELDMAPS
PATHS = {name: value for name, value in dict(globals()).items()             # a session script's variables
         if (name.endswith("_PATH") or name == "DATA_DIR") and isinstance(value, Path)}

def tasks_for(category: Category) -> list[Task]:
    return [t for t in TASKS if t.category == category]


def get_task(key: str) -> Task:
    task = next((t for t in TASKS if t.key == key), None)
    if task is None:
        raise KeyError(f"no task '{key}' in task_definitions.py")
    return task