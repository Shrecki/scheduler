"""The main window. It reads the session to draw itself (refresh), and reports what the user asks for
as signals. It never changes the session or the state machine itself."""

from PySide6.QtCore import QEvent, QPoint, QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPushButton, QSizePolicy, QStackedWidget, QToolTip, QVBoxLayout, QWidget)

from model.session import TimelineMode
from .blocks import Palette, fmt
from .composition import Composition
from .timeline import Timeline
from .session_dialog import SessionDialog
from .templates import TemplatePanel, saved_folder


def set_level(widget: QWidget, level: str):
    """Colour a widget by its level ("", "warning", "error"), as the theme says."""
    if widget.property("level") != level:
        widget.setProperty("level", level)
        widget.style().polish(widget)


class TotalEdit(QStackedWidget):
    """The session's length, after the clock: click it to type another (HH:MM). Enter or a click
    elsewhere sets it; Esc leaves it as it was."""
    changed = Signal(int)                       # the new length, in ms

    def __init__(self):
        super().__init__()
        self.ms = self.minimum = 0
        self.editable, self.typed_from = False, ""
        self.label = QLabel(objectName="total")
        self.edit = QLineEdit(objectName="total", placeholderText="HH:MM", alignment=Qt.AlignRight,
                              validator=QRegularExpressionValidator(QRegularExpression(r"\d{1,2}:[0-5]\d")))
        self.edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # as wide as the label shown
        self.edit.editingFinished.connect(self.commit)                          # Enter, or focus lost
        self.edit.installEventFilter(self)
        self.addWidget(self.label)
        self.addWidget(self.edit)

    def show_total(self, ms: int, editable: bool, minimum: int = 0):
        """The length shown; `minimum`: what it must stay above (the time used while recording)."""
        self.ms, self.minimum, self.editable = ms, minimum, editable
        self.label.setText(fmt(ms))
        self.label.setProperty("editable", editable)
        self.label.setCursor(Qt.PointingHandCursor if editable else Qt.ArrowCursor)
        self.label.setToolTip("Click to change the session's length (HH:MM)" if editable else "")
        if not editable:
            self.close_edit()

    def mousePressEvent(self, event):
        if self.editable and self.currentWidget() is self.label:
            h, m = divmod(self.ms // 60_000, 60)
            self.typed_from = f"{h:02d}:{m:02d}"
            self.edit.setText(self.typed_from)
            set_level(self.edit, "")
            self.edit.setMinimumWidth(self.edit.fontMetrics().horizontalAdvance("00:00") + 24)   # wider than "45:00"
            self.setCurrentWidget(self.edit)
            self.edit.setFocus()
            self.edit.selectAll()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Escape:
            event.accept()                              # Esc is the field's, not a shortcut's (the timeline's)
            return True
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.edit.setText(self.typed_from)          # as it was
            self.close_edit()
            return True
        if event.type() == QEvent.FocusOut:
            self.commit(leaving=True)
        return False

    def commit(self, leaving: bool = False):
        """Set what was typed, if it changed and is a length; too short: said, and still open (Enter)
        or left as it was (focus lost)."""
        if self.currentWidget() is not self.edit:
            return
        text = self.edit.text()
        if text != self.typed_from and self.edit.hasAcceptableInput():
            h, m = map(int, text.split(":"))
            ms = (h * 60 + m) * 60_000
            if ms <= self.minimum and not leaving:
                set_level(self.edit, "error")
                QToolTip.showText(self.edit.mapToGlobal(QPoint(0, self.edit.height())),
                                  f"More than {fmt(self.minimum)}: the time used already", self.edit)
                return
            if ms > self.minimum:
                self.changed.emit(ms)
        self.close_edit()

    def close_edit(self):
        if self.currentWidget() is self.edit:
            self.typed_from = self.edit.text()          # the field loses focus as it goes: nothing more to set
            self.setCurrentWidget(self.label)
            self.edit.setMinimumWidth(0)


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
    strictToggled = Signal(bool)              # on: at the end time, stop the run playing
    totalChanged = Signal(int)                # the session's length, in ms
    autoplayToggled = Signal(bool)
    playRequested = Signal(int)               # run index
    repeatRequested = Signal(list)            # run indices: repeat them to the end of the session
    shuffleRequested = Signal(list)           # run indices: the same, in a new random order for each round
    interleaveRequested = Signal(list)        # run indices: a copy after each run that follows them
    templateFolderChosen = Signal(str)        # folder path: read its templates
    templateDropped = Signal(int, int)        # template number, insert its runs before this run (-1: at the end)
    templateReplaced = Signal(int)            # template number: the design replaced with it

    def __init__(self, sm):
        super().__init__(windowTitle="Block scheduler")
        self.sm, self.session = sm, sm.model
        self.resize(1440, 580)

        self.identity, self.title = QLabel(objectName="hint"), QLabel(objectName="title")
        self.clock = QLabel(objectName="title")
        self.total = TotalEdit()
        self.total.changed.connect(self.totalChanged)
        self.timeline, self.palette = Timeline(self.session), Palette()
        self.templates = TemplatePanel(self.session)
        self.composition = Composition(self.session)
        self.message = QLabel(objectName="hint")
        self.mode = QPushButton()
        self.start = QPushButton("Start recording", objectName="primary")
        self.skip = QPushButton("Skip block")
        self.stop = QPushButton("Stop recording")
        self.autoplay = QCheckBox("Autoplay", toolTip="On: each run starts when the one before ends.\n"
                                  "Off: click a run to play it, one played already included.")
        self.autoplay.toggled.connect(self.autoplayToggled)
        self.strict = QCheckBox("Strict end time", toolTip="On: at the end time, the run playing is stopped and "
                                "the session ends.\nOff: a run playing at the end time goes on until it ends by "
                                "itself; no other run starts after it.")
        self.strict.toggled.connect(self.strictToggled)
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
        self.templates.folderChosen.connect(self.templateFolderChosen)
        self.templates.add.connect(self.templateDropped)
        self.templates.replace.connect(self.templateReplaced)
        self.timeline.templateDropped.connect(self.templateDropped)
        self.timeline.taskChosen.connect(self.taskChosen)
        self.timeline.paramChanged.connect(self.paramChanged)
        self.timeline.durationChanged.connect(self.durationChanged)
        self.timeline.playRequested.connect(self.playRequested)
        self.timeline.repeatRequested.connect(self.repeatRequested)
        self.timeline.shuffleRequested.connect(self.shuffleRequested)
        self.timeline.interleaveRequested.connect(self.interleaveRequested)

        header = QFrame(objectName="bar")
        titles = QVBoxLayout()
        titles.addWidget(self.identity)
        titles.addWidget(self.title)
        top = QHBoxLayout(header)
        top.addLayout(titles, 1)
        top.addWidget(self.clock)
        top.addWidget(self.total)
        column = QVBoxLayout()
        column.addWidget(self.composition)
        column.addWidget(self.timeline)
        column.addWidget(self.timeline.controls, 0, Qt.AlignRight)
        column.addStretch()
        body = QHBoxLayout()
        body.addWidget(self.templates)
        body.addLayout(column, 1)
        body.addWidget(self.palette)
        bottom = QHBoxLayout()
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.strict)
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

    def template_folder(self) -> str | None:
        """The template folder chosen at the last launch, if any."""
        return saved_folder()

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
        self.clock.setText(f"{fmt(shown)} /")
        set_level(self.clock, "warning" if s.rec_start is not None and s.remaining_ms() < 0 else "")  # past the end
        self.total.show_total(s.total_ms, editable=not state("finalize"), minimum=s.used_ms() if s.rec_start else 0)

        ended = [p for p in s.plays if p.ended_at]
        if not ended:
            self.message.setText(s.notice if state("design") else "")
        elif ended[-1].started_at is None and ended[-1].exit_code is None:
            self.message.setText(f"run-{ended[-1].index + 1:02d} could not be launched: see its output.log")
        else:
            self.message.setText(f"run-{ended[-1].index + 1:02d} exited (code {ended[-1].exit_code})")

        self.palette.setVisible(state("design"))
        self.templates.setVisible(state("design"))
        self.templates.refresh()
        self.load.setVisible(state("design"))
        self.save.setVisible(state("design"))
        self.export.setVisible(state("design") and s.scripts is not None)
        self.import_.setVisible(state("design") and s.scripts is not None)
        self.mode.setVisible(state("design") or state("execute"))
        self.autoplay.setVisible(state("execute") or state("recording"))
        self.strict.setVisible(not state("finalize"))
        for box, value in ((self.autoplay, s.autoplay), (self.strict, s.strict)):
            box.blockSignals(True)                  # show the session's value without reporting it back
            box.setChecked(value)
            box.blockSignals(False)
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