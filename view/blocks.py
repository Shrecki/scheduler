"""The block palette (right panel), and drag-and-drop helpers shared with the timeline.

Drag and drop carries a short text under a private MIME type: "new:VIDEO_GAME" from the palette,
"move:3" when a run is dragged along the timeline.
"""

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from model.tasks import Category, tasks_for
from .theme import CATEGORY_COLORS, color

MIME = "application/x-scheduler-block"


def fmt(ms: float) -> str:
    s = int(ms) // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def paint_block(p: QPainter, rect: QRectF, category: str, title: str, subtitle: str):
    """A rounded block in its category colour, with a title and a subtitle."""
    fill, ink = CATEGORY_COLORS[category]
    path = QPainterPath()
    path.addRoundedRect(rect, 6, 6)
    p.fillPath(path, QColor(fill))
    p.strokePath(path, QPen(color("ring"), 1))
    text, base = rect.adjusted(9, 6, -7, -6), p.font()
    bold = QFont(base)
    bold.setBold(True)
    p.setPen(QColor(ink))
    for font, line, align in ((bold, title, Qt.AlignTop), (base, subtitle, Qt.AlignBottom)):
        p.setFont(font)
        p.drawText(text, Qt.AlignLeft | align, line)
    p.setFont(base)


class DragSource(QWidget):
    """Starts a drag once the mouse has moved a few pixels with the button held."""
    press = None

    def mousePressEvent(self, event):
        self.press = event.position()

    def mouseMoveEvent(self, event):
        if self.press is not None and event.buttons() & Qt.LeftButton and \
                (event.position() - self.press).manhattanLength() >= QApplication.startDragDistance():
            payload, area = self.drag_payload(self.press)
            self.press = None
            if payload:
                mime = QMimeData()
                mime.setData(MIME, QByteArray(payload.encode()))
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.setPixmap(self.grab(area.toRect()))
                drag.exec(Qt.CopyAction | Qt.MoveAction)

    def drag_payload(self, pos: QPointF) -> tuple[str | None, QRectF | None]:
        raise NotImplementedError


class Chip(DragSource):
    """One category in the palette: drag it onto the timeline, or double-click to append it."""
    add = Signal(str)

    def __init__(self, category: Category):
        super().__init__()
        self.category, self.n_tasks = category, len(tasks_for(category))
        self.setFixedHeight(52)
        self.setEnabled(self.n_tasks > 0)
        self.setCursor(Qt.OpenHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_block(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.category.name, self.category.value,
                    f"{self.n_tasks} task{'s' * (self.n_tasks != 1)}")

    def drag_payload(self, pos):
        return f"new:{self.category.name}", QRectF(self.rect())

    def mouseDoubleClickEvent(self, event):
        self.add.emit(self.category.name)


class Palette(QFrame):
    add = Signal(str)

    def __init__(self):
        super().__init__(objectName="palette")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        hint = QLabel("Drag a block onto the timeline, or double-click it to add it at the end.", objectName="hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for category in Category:
            chip = Chip(category)
            chip.add.connect(self.add)
            layout.addWidget(chip)
        layout.addStretch()