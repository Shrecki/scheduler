"""The timeline: one rectangle per run, to scale, scrolling horizontally. Each run is a widget holding
its task, duration and parameter inputs; a see-through layer on top draws the grey of played time,
the playhead, the end line and the drop marker. Emits what the user asks for; never edits the session.
"""

from PySide6.QtCore import QEvent, QLineF, QRectF, QTime, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)

from model.tasks import get_task, tasks_for
from .blocks import MIME, DragSource, fmt
from .theme import CATEGORY_COLORS, color

MIN_W, MAX_PX_PER_MS = 300, 300 / 30_000   # shortest run gets 300 px (runs under 30 s get less)
PAD, TOP, CARD_H, GAP = 16, 14, 272, 4
ZOOM_STEP, MAX_ZOOM = 2.0, 4.0              # per click (a wheel notch: its square root); up to 4x the default
ZERO = QTime(0, 0)


def tint(fill: str, amount: float = 0.16) -> str:
    f, b = QColor(fill), color("panel")
    mix = lambda x, y: round(x * amount + y * (1 - amount))
    return QColor(mix(f.red(), b.red()), mix(f.green(), b.green()), mix(f.blue(), b.blue())).name()


def param_editor(value, choices: tuple | None, changed) -> QWidget:
    """An input for one parameter, chosen from its default value; calls changed(new value)."""
    if choices:
        widget = QComboBox()
        widget.addItems([str(c) for c in choices])
        widget.setCurrentIndex(choices.index(value) if value in choices else 0)
        widget.currentIndexChanged.connect(lambda i: changed(choices[i]))
    elif isinstance(value, bool):
        widget = QCheckBox(checked=value)
        widget.toggled.connect(changed)
    elif isinstance(value, int):
        widget = QSpinBox(minimum=-10**6, maximum=10**6, value=value)
        widget.valueChanged.connect(changed)
    elif isinstance(value, float):
        widget = QDoubleSpinBox(minimum=-1e6, maximum=1e6, decimals=2, value=value)
        widget.valueChanged.connect(changed)
    else:
        widget = QLineEdit(str(value))
        widget.textChanged.connect(changed)
    return widget


class RunCard(DragSource):
    """One run: header (name, category, progress) and its inputs. Drag it by the header to move it."""

    def __init__(self, index: int, block, max_ms: int, timeline: "Timeline", parent: QWidget):
        super().__init__(parent)
        self.index, self.timeline = index, timeline
        fill, ink = CATEGORY_COLORS[block.category.name]
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(f"QWidget#card {{ background: {tint(fill)}; border: 1px solid {fill}; border-radius: 6px; }}")
        header = QFrame(objectName="head")
        header.setStyleSheet(f"QFrame#head {{ background: {fill}; border-radius: 5px; }} QLabel {{ color: {ink}; }}")
        self.status = QLabel()
        top = QHBoxLayout(header)
        top.setContentsMargins(10, 5, 10, 5)
        top.addWidget(QLabel(f"<b>run-{index + 1:02d}</b>&nbsp;&nbsp;{block.category.value}"), 1)
        top.addWidget(self.status)

        emit = timeline.paramChanged.emit
        tasks = tasks_for(block.category)
        task_box = QComboBox()
        task_box.addItems([t.label for t in tasks])
        task_box.setCurrentIndex([t.key for t in tasks].index(block.task))
        task_box.currentIndexChanged.connect(lambda i: timeline.taskChosen.emit(index, tasks[i].key))
        duration = QTimeEdit(displayFormat="mm:ss" if max_ms < 3_600_000 else "h:mm:ss")
        duration.setTimeRange(QTime(0, 0, 1), ZERO.addMSecs(max_ms))
        duration.setTime(ZERO.addMSecs(block.duration_ms))
        duration.timeChanged.connect(lambda t: timeline.durationChanged.emit(index, ZERO.msecsTo(t)))

        inputs = QWidget()
        inputs.setFixedWidth(MIN_W - 2 * GAP - 16)     # inputs stay compact in long runs
        form = QFormLayout(inputs)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Task", task_box)
        form.addRow("Duration", duration)
        task = get_task(block.task)
        for name, value in block.params.items():
            form.addRow(name, param_editor(value, task.choices(name), lambda v, name=name: emit(index, name, v)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(header)
        layout.addWidget(inputs)
        layout.addStretch()

    def drag_payload(self, pos):
        return (f"move:{self.index}", QRectF(self.rect())) if self.timeline.session.in_state("design") else (None, None)

    def contextMenuEvent(self, event):
        if self.timeline.session.in_state("design"):
            menu = QMenu(self)
            remove = menu.addAction(f"Remove run-{self.index + 1:02d}")
            if menu.exec(event.globalPos()) is remove:
                self.timeline.blockRemoved.emit(self.index)


class Overlay(QWidget):
    """Drawn over the runs: grey for played time, current-run outline, playhead, end line, drop marker."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.veils, self.current, self.lines, self.empty = [], None, [], False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        veil = color("surface")
        veil.setAlpha(150)
        for rect in self.veils:
            p.fillRect(rect, veil)
        if self.current is not None:
            p.setPen(QPen(color("accent"), 2))
            p.drawRoundedRect(self.current.adjusted(1, 1, -1, -1), 6, 6)
        for x, pen in self.lines:
            p.setPen(pen)
            p.drawLine(QLineF(x, TOP - 10, x, TOP + CARD_H + 10))
        if self.empty:
            p.setPen(color("muted"))
            p.drawText(self.rect(), Qt.AlignCenter, "Drag blocks here from the panel on the right")


class Content(QWidget):
    """The scrolled surface: runs are placed on it by Timeline.refresh(); drops land here."""

    def __init__(self, timeline: "Timeline"):
        super().__init__()
        self.timeline = timeline
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        self.timeline.drag_over(event)

    def dragMoveEvent(self, event):
        self.timeline.drag_over(event)

    def dragLeaveEvent(self, event):
        self.timeline.set_caret(None)

    def dropEvent(self, event):
        self.timeline.drop(event)


def status(block, now: int, waiting: bool) -> str:
    if block.ended_at:
        return f"played {fmt(block.played_ms(now))}" if block.started_at else "not started"
    if block.started_at:
        return f"{fmt(block.played_ms(now))} of {fmt(block.duration_ms)}"
    return "waiting to start" if waiting else ""


class Timeline(QScrollArea):
    blockDropped = Signal(str, int)     # category name, insert before this index
    blockMoved = Signal(int, int)       # from index, insert before this index
    blockRemoved = Signal(int)
    taskChosen = Signal(int, str)       # run index, task key
    paramChanged = Signal(int, str, object)   # run index, parameter name, value
    durationChanged = Signal(int, int)  # run index, ms

    def __init__(self, session):
        super().__init__(widgetResizable=False, frameShape=QFrame.NoFrame)
        self.session, self.shown, self.cards, self.rects = session, [], [], []
        self.caret = self.followed = None
        self.content = Content(self)
        self.overlay = Overlay(self.content)
        self.setWidget(self.content)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(TOP + CARD_H + 2 * PAD + self.horizontalScrollBar().sizeHint().height())

        # zoom: buttons (placed by the window), Ctrl + / Ctrl - / Ctrl 0, and Ctrl + wheel
        self.zoom, self.k, self.base, self.fit = 1.0, MAX_PX_PER_MS, MAX_PX_PER_MS, 0.0
        self.controls = QWidget()
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Zoom", objectName="hint"))
        self.zoom_out, self.zoom_in, self.zoom_reset = QPushButton("−"), QPushButton("+"), QPushButton("Reset")
        for button, factor, keys, tip in ((self.zoom_out, 1 / ZOOM_STEP, ["Ctrl+-"], "Zoom out"),
                                          (self.zoom_in, ZOOM_STEP, ["Ctrl++", "Ctrl+="], "Zoom in"),
                                          (self.zoom_reset, None, ["Ctrl+0"], "Back to the default scale")):
            button.setToolTip(f"{tip} ({keys[0]}; Ctrl + wheel zooms around the pointer)")
            button.clicked.connect(lambda _=False, f=factor: self.zoom_by(f))
            row.addWidget(button)
            for key in keys:
                QShortcut(QKeySequence(key), self, activated=lambda f=factor: self.zoom_by(f))
        self.viewport().installEventFilter(self)

    def zoom_by(self, factor: float | None, anchor: float | None = None):
        """Zoom in (factor > 1) or out, keeping the time under `anchor` (x in the viewport, default: its
        centre) where it is. None: back to the default scale."""
        anchor = self.viewport().width() / 2 if anchor is None else anchor
        bar = self.horizontalScrollBar()
        t = (bar.value() + anchor - PAD) / self.k
        shown = self.k / self.base                  # can exceed self.zoom when the whole session fits
        self.zoom = 1.0 if factor is None else min(max(shown * factor, self.fit / self.base), MAX_ZOOM)
        self.refresh()
        bar.setValue(round(PAD + t * self.k - anchor))

    def eventFilter(self, obj, event):
        """Ctrl + wheel zooms, also over a run's inputs (which would otherwise take the wheel)."""
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            x = self.viewport().mapFromGlobal(event.globalPosition().toPoint()).x()
            self.zoom_by(ZOOM_STEP ** (event.angleDelta().y() / 240), x)   # 120 per notch of a wheel
            return True
        return False

    def refresh(self):
        s = self.session
        if len(self.shown) != len(s.blocks) or any(a is not b for a, b in zip(self.shown, s.blocks)):
            self.rebuild()      # only when runs were added, removed, moved or given another task
        items, end, playhead = s.timeline()
        axis = max(s.total_ms, end, sum(n for _, _, n in items), 1)
        shortest = min((n for _, _, n in items), default=axis)
        self.fit = max(self.viewport().width() - 2 * PAD, 1) / axis        # px per ms to see it all
        self.base = min(max(MIN_W / shortest, self.fit), MAX_PX_PER_MS)    # default: shortest run readable
        k = self.k = max(self.base * self.zoom, self.fit)
        self.zoom_out.setEnabled(k > self.fit * 1.001)
        self.zoom_in.setEnabled(self.zoom < MAX_ZOOM)
        self.zoom_reset.setEnabled(abs(self.zoom - 1) > 1e-3)
        self.content.setFixedSize(int(2 * PAD + axis * k), TOP + CARD_H + 2 * PAD)
        self.overlay.setGeometry(self.content.rect())
        now = s.now()
        rects, veils = [], []
        for i, ((b, x, n), card) in enumerate(zip(items, self.cards)):
            rect = QRectF(PAD + x * k, TOP, max(n * k - GAP, 8), CARD_H)
            card.setGeometry(rect.toRect())
            card.setEnabled(s.editable(i))
            card.setToolTip(f"run-{i + 1:02d}: {b.label}, {fmt(b.duration_ms)}")   # readable when zoomed out
            played = 1.0 if b.ended_at else b.played_ms(now) / n
            if played > 0:
                veils.append(QRectF(rect.left(), rect.top(), rect.width() * played, rect.height()))
            card.status.setText(status(b, now, waiting=i == s.index and s.in_state("recording")))
            rects.append(rect)
        self.rects = rects
        recording = s.in_state("recording")
        o = self.overlay
        o.veils, o.empty = veils, not items
        o.current = rects[s.index] if recording and 0 <= s.index < len(rects) else None
        o.lines = [(PAD + end * k, QPen(color("ink"), 2))]
        if playhead is not None:
            o.lines.append((PAD + playhead * k, QPen(color("accent"), 2)))
        if self.caret is not None:
            o.lines.append((self.caret_x(), QPen(color("accent"), 3)))
        o.raise_()
        o.update()
        if recording and self.followed != s.index and o.current is not None:
            self.followed = s.index                      # a new run started: bring it into view
            self.ensureVisible(int(o.current.center().x()), 0, int(o.current.width() / 2) + PAD, 0)

    def rebuild(self):
        s = self.session
        self.shown = list(s.blocks)
        for card in self.cards:
            card.hide()             # at once: deleteLater() only takes effect on the next loop pass
            card.deleteLater()
        self.cards = [RunCard(i, b, s.max_block_ms, self, self.content) for i, b in enumerate(s.blocks)]
        for card in self.cards:
            card.show()
            for widget in card.findChildren(QWidget):
                widget.installEventFilter(self)     # for Ctrl + wheel

    # --- drag and drop (from the palette, or moving a run by its header) ------------------------------
    def index_at(self, x: float) -> int:
        return next((i for i, r in enumerate(self.rects) if x < r.center().x()), len(self.rects))

    def caret_x(self) -> float:
        if not self.rects:
            return PAD
        return self.rects[self.caret].left() - GAP / 2 if self.caret < len(self.rects) else self.rects[-1].right() + GAP / 2

    def set_caret(self, index):
        self.caret = index
        self.refresh()

    def drag_over(self, event):
        if self.session.in_state("design") and event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()
            self.set_caret(self.index_at(event.position().x()))

    def drop(self, event):
        index = self.caret
        self.set_caret(None)
        if index is None:
            return
        kind, value = bytes(event.mimeData().data(MIME)).decode().split(":")
        if kind == "new":
            self.blockDropped.emit(value, index)
        elif index not in (int(value), int(value) + 1):    # dropping a run in place does nothing
            self.blockMoved.emit(int(value), index)