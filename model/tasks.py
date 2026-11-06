"""
Catalogue of tasks which can be run.
This is where we can define all the tasks as subprograms (MTB, localizers, fmri-gym execution...).
A task is defined by the parameters it accepts and the way it should be ran.

Tasks are furthermore grouped by categories (cognitive task, resting state, movie watching, video game, localizer, eye tracking calibration).

DUMMY TASKS: small programs that come with Ubuntu, standing in for the real tasks. `timeout` ends
each one after the block's planned duration (exit code 124), as a real task would end itself.
With trigger=True, a dialog stands in for the scanner trigger: the block starts when you click OK.
Missing a program? sudo apt install gnome-sudoku gnome-mines gnome-chess aisleriot eog zenity
A real task looks like: Task("mot", "MOT", Category.COGNITIVE_TASK,
    ("apptainer", "run", "containers/mot.sif", "--speed", "{speed}", "--out", "{outdir}"), {"speed": 4.0})
"""

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import shutil
UV = shutil.which("uv") or str(Path.home() / ".local/bin/uv")

ROOT = Path(__file__).parents[1]


class Category(Enum):
    COGNITIVE_TASK = "Cognitive task"
    RESTING_STATE = "Resting state"
    MOVIE_WATCHING = "Movie watching"
    VIDEO_GAME = "Video game"
    LOCALIZER_MEG = "MEG localizer"
    LOCALIZER_MRI = "MRI localizer"
    EYETRACKER_CALIBRATION = "Eyetracking calibration"

@dataclass(frozen=True)
class Task:
    key: str                          # unique; stored in session files
    label: str
    category: Category
    command: tuple[str, ...]
    params: dict = field(default_factory=dict)   # name: default value, or a tuple of choices (first = default)
    duration_s: float = 15 * 60
    start_line: str | None = None
    project: Path | str | None = None # Optional venv path
    stop_grace_s: float = 5.0         # after a stop request, force-kill after this long
 
    def defaults(self) -> dict:
        return {name: v[0] if isinstance(v, tuple) else v for name, v in self.params.items()}
 
    def choices(self, name: str) -> tuple | None:
        return self.params[name] if isinstance(self.params[name], tuple) else None
 
    def accepts(self, name: str, value) -> bool:
        """A valid value for a parameter: one of its choices, or a value of its default's type."""
        if name not in self.params:
            return False
        default = self.params[name]
        if isinstance(default, tuple):
            return value in default
        return type(value) is type(default) or (type(default) is float and type(value) is int)
 
    def argv(self, params: dict, **context) -> list[str]:
        values = {**self.defaults(), **params, **context}
        return [part.format(**values) for part in self.command]
 
  
 
def app(*command: str) -> tuple[str, ...]:
    """An existing program, ended by `timeout` after the block's duration.
    Games are named by full path: Ubuntu keeps them in /usr/games, which not every PATH includes."""
    run = ("timeout", "{duration_s}", *command)
    return run
 
 
def fake(key: str, *args: str) -> tuple[str, ...]:
    """fake_task.py: a stand-in that needs no display (used by the tests)."""
    return (sys.executable, str(ROOT / "fake_task.py"), "--task", key, "--duration", "{duration_s}",
            "--out", "{outdir}", *args)
 
 
def view(image: Path | str, *options: str) -> tuple[str, ...]:
    return ("eog", "--new-instance", *options, str(image))



FMRI_GYM_PATH="~/DBP/fmri-gym/"
GOXPY_PATH = "~/DBP/goxpyriment/"
EYETRACKER_PATH =  "~/DBP/eyelink-track/"

GAME_TASKS: list[Task] = [
    Task("baba_is_you", "Baba is you", Category.VIDEO_GAME,
     app("fmri-play", "--dummy-trigger", "--subject", "{subject}", "--ses", "1", "--run", "1",
         "--rig", "configs/examples/rigs/laptop.json",
         "--curriculum", "configs/curricula/baba_auto/levels/baba_is_you.json"),
     duration_s=120, project=FMRI_GYM_PATH),
    Task("out_of_reach", "Out of reach", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/baba_auto/levels/out_of_reach.json"),duration_s=120),
    Task("volcano", "Volcano", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/baba_auto/levels/volcano.json"),duration_s=120),
    
    #Task("off_limits", "Off limits", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("grass_yard", "Grass yard", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("icy_waters", "Icy waters", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("turns", "Turns", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("affection", "Affection", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("pillar_yard", "Pillar Yard", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("brickwall", "Brickwall", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("lock", "Lock", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    #Task("novice_locksmith", "Novice locksmith", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("rush_hour", "Rush Hour", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/rushhour/levels/easy.json"),duration_s=120),
    
    Task("liquid_sort", "Liquid sort", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/aigamestore/levels/game1.json"),duration_s=120),
    
    #Task("obstacle_flying_game", "Obstacle Flying Game", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    #Task("tank_shooter", "Tank Shooter", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    #Task("bubble_pop", "Bubble Pop", Category.VIDEO_GAME, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("supertux_kart", "SuperTux Kart", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/stk/levels/cornfield_crossing.json"),duration_s=600),
    
    Task("vizdoom", "VizDoom", Category.VIDEO_GAME, app(f"cd {FMRI_GYM_PATH} && source .venv/bin/activate && uv run fmri-play --dummy-trigger --subject sub-01 --ses 1 --run 1 --rig configs/examples/rigs/laptop.json --curriculum configs/curricula/vizdoom/levels/deathmatch.json"),duration_s=600)
]

COG_TASKS: list[Task] = [
    Task("syllogism_meg", "Syllogism MEG", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("syllogism_mri", "Syllogism MRI", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("wisconsin_card_sort_meg", "Wisconsin Card Sort MEG", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("wisconsin_card_sort_mri", "Wisconsin Card Sort MRI", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("arc_meg", "ARC MEG", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
    
    Task("tom_bench", "ToMBench", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),duration_s=120),
]

LOCALIZERS_MEG: list[Task] = [
    Task("multimodal_localizer", "MEG-localizer (multimodal localizer)", Category.LOCALIZER_MEG, app(f"cd {GOXPY_PATH} && go run ./examples/MEG-localizer -ttl parallel:/dev/parport0"),duration_s=120),
    Task("finger_tone_localizer", "MEG-localizer2 (finger and tone)", Category.LOCALIZER_MEG, app(f"cd {GOXPY_PATH} && go run ./examples/MEG-localizer2 -ttl parallel:/dev/parport0"),duration_s=120),
    Task("multifocal_retinotopy", "MEG-localizer3 (multifocal retinotopy)", Category.LOCALIZER_MEG, app(f"cd {GOXPY_PATH} && go run ./examples/MEG-localizer3 -ttl parallel:/dev/parport0"),duration_s=120),
    Task("retinotopy", "Retinotopy", Category.LOCALIZER_MEG, app(f"cd {GOXPY_PATH} && go run ./examples/Retinotopy-multifocal -w -s 1 -ttl parallel:/dev/parport0"),duration_s=120),
    #Task("smooth_pursuit", "Smooth Pursuit localizer", Category.LOCALIZER_MEG, app("/usr/games/gnome-sudoku"),duration_s=120),
]

LOCALIZERS_MRI: list[Task] = [
    Task("mtb_localizer", "Multiple-Task-Battery MRI localizer", Category.LOCALIZER_MRI, app("/usr/games/gnome-sudoku"),duration_s=190),
]

EYETRACKERS: list[Task] = [
    Task("eyetracker_calibration", "EyeTracker calibration", Category.EYETRACKER_CALIBRATION, app(f"cd {EYETRACKER_PATH} && source .venv/bin/activate && python -c 'from eyelink_track.commands.gui import run; run()'"),duration_s=120),
]
 
 

TASKS_DUMMY = [
    Task("sudoku", "Sudoku", Category.COGNITIVE_TASK, app("/usr/games/gnome-sudoku"),
         duration_s=120),
    Task("mines", "Minesweeper", Category.COGNITIVE_TASK, app("/usr/games/gnome-mines", "--{size}"),
         {"size": ("small", "medium", "big")}, duration_s=120),
    Task("fixation", "Fixation cross", Category.RESTING_STATE,
         app(*view(ROOT / "assets" / "fixation.svg", "--fullscreen")), duration_s=60),
    Task("slideshow", "Wallpaper slideshow", Category.MOVIE_WATCHING,
         app(*view("{folder}", "--slide-show")), {"folder": "/usr/share/backgrounds"},
         duration_s=90),
    Task("chess", "Chess", Category.VIDEO_GAME, app("/usr/games/gnome-chess"), duration_s=120),
    Task("solitaire", "Solitaire", Category.VIDEO_GAME, app("/usr/games/sol"), duration_s=120),
    Task("checkerboard_meg", "Checkerboard", Category.LOCALIZER_MEG,
         app(*view(ROOT / "assets" / "checkerboard.gif", "--fullscreen")), duration_s=60),
    Task("checkerboard_mri", "Checkerboard", Category.LOCALIZER_MRI,
         app(*view(ROOT / "assets" / "checkerboard.gif", "--fullscreen")), duration_s=60,
         ),
]

TASKS = TASKS_DUMMY + COG_TASKS + LOCALIZERS_MEG + LOCALIZERS_MRI + GAME_TASKS + EYETRACKERS
 
def tasks_for(category: Category) -> list[Task]:
    return [t for t in TASKS if t.category == category]
 
 
def get_task(key: str) -> Task:
    task = next((t for t in TASKS if t.key == key), None)
    if task is None:
        raise KeyError(f"no task '{key}' in tasks.py")
    return task
