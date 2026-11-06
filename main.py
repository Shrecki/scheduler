"""Block scheduler: python main.py"""

import sys

from PySide6.QtWidgets import QApplication

from controller.controller import Controller
from controller.launcher import Launcher
from model.session import Session
from model.statechart import Scheduler
from view.theme import apply_theme
from view.window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    launcher = Launcher()
    sm = Scheduler(model=Session(processes=launcher))
    view = MainWindow(sm)
    controller = Controller(sm, view, launcher)
    return app.exec() if controller.run() else 0


if __name__ == "__main__":
    sys.exit(main())
