"""The main window. It reads the session to draw itself (refresh), and reports what the user asks for
as signals. It never changes the session or the state machine itself."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QVBoxLayout, QWidget)

from model.session import TimelineMode
from .blocks import Palette, fmt
from .composition import Composition
from .timeline import Timeline
from .session_dialog import SessionDialog


class MainWindow(QMainWindow):
    modeToggled = Signal()
    startClicked = Signal()
    skipClicked = Signal()
    stopClicked = Signal()
    blockDropped = Signal(str, int)     # category name, insert before this index (-1: at the end)
    blockMoved = Signal(int, int)
    blockRemoved = Signal(int)
    taskChosen = Signal(int, str)             # run index, task key
    paramChanged = Signal(int, str, object)   # run index, parameter name, value
    durationChanged = Signal(int, int)        # run index, ms
    loadDesign = Signal(str)                  # file path
    saveDesign = Signal(str)
    exportScript = Signal(str)
    importScript = Signal(str)
    autoplayToggled = Signal(bool)
    playRequested = Signal(int)               # run index
    repeatRequested = Signal(list)            # run indices: repeat them to the end of the session
    interleaveRequested = Signal(list)        # run indices: a copy after each run that follows them

    def __init__(self, sm):
        super().__init__(windowTitle="Block scheduler")
        self.sm, self.session = sm, sm.model
        self.resize(1200, 580)

        self.identity, self.title = QLabel(objectName="hint"), QLabel(objectName="title")
        self.clock = QLabel(objectName="title")
        self.timeline, self.palette = Timeline(self.session), Palette()
        self.composition = Composition(self.session)
        self.message = QLabel(objectName="hint")
        self.mode = QPushButton()
        self.start = QPushButton("Start recording", objectName="primary")
        self.skip = QPushButton("Skip block")
        self.stop = QPushButton("Stop recording")
        self.autoplay = QCheckBox("Autoplay", toolTip="On: each run starts when the one before ends.\n"
                                  "Off: click a run to play it, one played already included.")
        self.autoplay.toggled.connect(self.autoplayToggled)
        self.load, self.save = QPushButton("Load design…"), QPushButton("Save design…")
        self.load.clicked.connect(lambda: self.ask_file(QFileDialog.getOpenFileName, self.loadDesign))
        self.save.clicked.connect(lambda: self.ask_file(QFileDialog.getSaveFileName, self.saveDesign))
        self.export, self.import_ = QPushButton("Export script…"), QPushButton("Import script…")
        self.export.clicked.connect(lambda: self.ask_file(QFileDialog.getSaveFileName, self.exportScript,
                                                          "Session script", ".sh"))
        self.import_.clicked.connect(lambda: self.ask_file(QFileDialog.getOpenFileName, self.importScript,
                                                           "Session script", ".sh"))
        for button, signal in ((self.mode, self.modeToggled), (self.start, self.startClicked),
                               (self.skip, self.skipClicked), (self.stop, self.stopClicked)):
            button.clicked.connect(signal)
        self.timeline.blockDropped.connect(self.blockDropped)
        self.timeline.blockMoved.connect(self.blockMoved)
        self.timeline.blockRemoved.connect(self.blockRemoved)
        self.palette.add.connect(lambda name: self.blockDropped.emit(name, -1))
        self.timeline.taskChosen.connect(self.taskChosen)
        self.timeline.paramChanged.connect(self.paramChanged)
        self.timeline.durationChanged.connect(self.durationChanged)
        self.timeline.playRequested.connect(self.playRequested)
        self.timeline.repeatRequested.connect(self.repeatRequested)
        self.timeline.interleaveRequested.connect(self.interleaveRequested)

        header = QFrame(objectName="bar")
        titles = QVBoxLayout()
        titles.addWidget(self.identity)
        titles.addWidget(self.title)
        top = QHBoxLayout(header)
        top.addLayout(titles, 1)
        top.addWidget(self.clock)
        column = QVBoxLayout()
        column.addWidget(self.composition)
        column.addWidget(self.timeline)
        column.addWidget(self.timeline.controls, 0, Qt.AlignRight)
        column.addStretch()
        body = QHBoxLayout()
        body.addLayout(column, 1)
        body.addWidget(self.palette)
        bottom = QHBoxLayout()
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.autoplay)
        for button in (self.load, self.save, self.import_, self.export, self.mode, self.stop, self.skip, self.start):
            bottom.addWidget(button)
        central = QWidget()
        page = QVBoxLayout(central)
        page.addWidget(header)
        page.addLayout(body, 1)
        page.addLayout(bottom)
        self.setCentralWidget(central)

        self.session.observers.append(self.refresh)

    def ask_file(self, dialog, signal, kind="Session design", suffix=".json"):
        path, _ = dialog(self, kind, str(self.session.data_root), f"{kind} (*{suffix})")
        if path:
            signal.emit(path if path.endswith(suffix) else path + suffix)

    def ask_session(self, data_root):
        dialog = SessionDialog(data_root)
        return dialog.info() if dialog.exec() else None

    def refresh(self):
        """Redraw everything from the session. Only reads: never changes the session."""
        s, state, leaf = self.session, self.session.in_state, self.session.leaf_state
        allowed = {event.id for event in self.sm.allowed_events}
        if leaf in ("next_block_await", "block_running"):
            waiting = ", launching" if leaf == "next_block_await" else ""
            again = " (again)" if s.times_played(s.index) > 1 else ""
            title = f"run-{s.index + 1:02d}: {s.current.label}{again}{waiting}"
        elif leaf == "idle":
            title = "All runs played: click one to play it again" if s.autoplay else "Click a run to play it"
        else:
            title = {"design": "Timeline design", "execute": "Ready to record", "quit_sequence": "Stopping",
                     "finalize": "Session finished"}.get(leaf, "")
        absolute = s.rec_start is not None and s.mode == TimelineMode.TOTAL_BLOCK_TIME    # the clock shows block time
        recorded = f"  /  recording {fmt(s.elapsed_ms())}" if absolute else ""            # this one always runs
        self.identity.setText(f"{s.subject}  /  ses-{s.session_number:02d}{recorded}")
        self.title.setText(title)

        if s.rec_start is None:
            shown = sum(b.duration_ms for b in s.blocks)    # planned
        else:
            shown = s.block_time_ms() if s.mode == TimelineMode.TOTAL_BLOCK_TIME else s.elapsed_ms()
        self.clock.setText(f"{fmt(shown)} / {fmt(s.total_ms)}")

        ended = [p for p in s.plays if p.ended_at]
        if not ended:
            self.message.setText(s.notice if state("design") else "")
        elif ended[-1].started_at is None and ended[-1].exit_code is None:
            self.message.setText(f"run-{ended[-1].index + 1:02d} could not be launched: see its output.log")
        else:
            self.message.setText(f"run-{ended[-1].index + 1:02d} exited (code {ended[-1].exit_code})")

        self.palette.setVisible(state("design"))
        self.load.setVisible(state("design"))
        self.save.setVisible(state("design"))
        self.export.setVisible(state("design") and s.scripts is not None)
        self.import_.setVisible(state("design") and s.scripts is not None)
        self.mode.setVisible(state("design") or state("execute"))
        self.autoplay.setVisible(state("execute") or state("recording"))
        self.autoplay.blockSignals(True)            # show the session's value without reporting it back
        self.autoplay.setChecked(s.autoplay)
        self.autoplay.blockSignals(False)
        self.mode.setText("Back to design" if state("execute") else "Switch to execution")
        self.start.setVisible(state("execute"))
        self.start.setEnabled(s.has_blocks())
        self.skip.setVisible(state("recording"))
        self.skip.setEnabled("skip" in allowed)
        self.stop.setVisible(state("recording"))
        self.stop.setEnabled("end_session" in allowed)
        self.composition.refresh()
        self.timeline.refresh()

    def closeEvent(self, event):
        if self.session.in_state("recording"):
            event.ignore()              # closing would abandon the running task
            self.message.setText("Stop the recording before closing the window.")