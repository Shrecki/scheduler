"""Block scheduler: python main.py"""

import sys

import PySide6
from PySide6.QtWidgets import QApplication

# PySide6 6.12.0 frees Python's None after a few thousand drawing calls on Python < 3.12 (crash
# "none_dealloc: deallocating None"). Python 3.12+ never frees None, so it is safe there.
if PySide6.__version_info__[:3] == (6, 12, 0) and sys.version_info < (3, 12):
    sys.exit('PySide6 6.12.0 crashes on Python < 3.12. Install another version: pip install "PySide6<6.12"')

from adapters.fmri_gym import SessionScripts
from controller.controller import Controller
from controller.launcher import Launcher
from model.session import Session
from model.task_definitions import FMRI_GYM_PATH, TASKS
from model.statechart import Scheduler
from view.theme import apply_theme
from view.window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    launcher = Launcher()
    scripts = SessionScripts(FMRI_GYM_PATH, TASKS)           # session scripts in fmri-gym's format
    sm = Scheduler(model=Session(processes=launcher, scripts=scripts))
    view = MainWindow(sm)
    controller = Controller(sm, view, launcher)
    return app.exec() if controller.run() else 0


if __name__ == "__main__":
    sys.exit(main())