"""The template library (left panel, design only): the session designs and scripts of a folder, by
subfolder, each drawn as a whole session at a glance. Drag one onto the timeline to insert its runs
there, double-click it to add them at the end, right-click it to replace the design with it.

The folder is remembered from one launch to the next (QSettings)."""

from PySide6.QtCore import QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .blocks import DragSource, fmt
from .theme import CATEGORY_COLORS, color

FOLDER_KEY = "templates/folder"
TOOLTIP_RUNS = 25       # runs listed in a template's tooltip


def settings() -> QSettings:
    return QSettings("block-scheduler", "scheduler")


def saved_folder() -> str | None:
    """The template folder chosen last time, if any."""
    return settings().value(FOLDER_KEY) or None


class TemplateItem(DragSource):
    """One template: its name and length, and its runs as a bar the width of the panel."""
    add = Signal(int)           # template number: its runs at the end
    replace = Signal(int)       # template number: the design replaced with it
    BAR_H, HEIGHT = 12, 46

    def __init__(self, number: int, template):
        super().__init__()
        self.number, self.template = number, template
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.OpenHandCursor)
        runs = [f"run-{i + 1:02d}   {label}   {fmt(seconds * 1000)}" for i, (_, label, seconds) in enumerate(template.parts)]
        more = len(runs) - TOOLTIP_RUNS
        self.setToolTip("\n".join([f"{template.name}  ({template.path.name})", *runs[:TOOLTIP_RUNS],
                                   *([f"... and {more} more"] if more > 0 else [])]))

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        frame = QPainterPath()
        frame.addRoundedRect(box, 6, 6)
        p.fillPath(frame, color("panel"))
        p.strokePath(frame, QPen(color("accent") if self.underMouse() else color("border"), 1))

        inner = box.adjusted(8, 5, -8, -7)
        bold, base = QFont(p.font()), p.font()
        bold.setBold(True)
        length = fmt(self.template.total_s * 1000)
        p.setFont(base)
        p.setPen(color("muted"))
        p.drawText(inner, Qt.AlignRight | Qt.AlignTop, length)
        room = inner.width() - QFontMetrics(base).horizontalAdvance(length) - 8
        p.setFont(bold)
        p.setPen(color("ink"))
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, QFontMetrics(bold).elidedText(self.template.name, Qt.ElideRight, int(room)))

        bar = QRectF(inner.left(), inner.bottom() - self.BAR_H, inner.width(), self.BAR_H)
        clip = QPainterPath()
        clip.addRoundedRect(bar, 3, 3)
        p.setClipPath(clip)
        p.fillRect(bar, color("surface"))
        x, scale = bar.left(), bar.width() / max(self.template.total_s, 1e-9)
        for category, _, seconds in self.template.parts:
            width = seconds * scale
            gap = 1 if width >= 3 else 0                     # a hairline between runs, if they have room
            p.fillRect(QRectF(x, bar.top(), max(width - gap, 0.5), bar.height()), QColor(CATEGORY_COLORS[category][0]))
            x += width

    def drag_payload(self, pos):
        return f"template:{self.number}", QRectF(self.rect())

    def mouseDoubleClickEvent(self, event):
        self.add.emit(self.number)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        add = menu.addAction("Add at the end")
        replace = menu.addAction("Replace the design with this")
        action = menu.exec(event.globalPos())
        if action is add:
            self.add.emit(self.number)
        elif action is replace:
            self.replace.emit(self.number)


class TemplatePanel(QFrame):
    folderChosen = Signal(str)
    add = Signal(int, int)          # template number, insert before this run (-1: at the end)
    replace = Signal(int)           # template number

    def __init__(self, session):
        super().__init__(objectName="templates")
        self.session, self.shown, self.collapsed = session, None, set()
        self.setFixedWidth(250)
        self.folder = QLabel(objectName="group")
        self.choose = QPushButton("Choose folder…", toolTip="The folder whose session designs (.json) and "
                                  "session scripts (.sh), subfolders included, become templates")
        self.reload = QPushButton("Reload", toolTip="Read the folder again (new or changed files)")
        self.choose.clicked.connect(self.ask_folder)
        self.reload.clicked.connect(lambda: self.folderChosen.emit(str(self.session.template_folder)))
        self.hint = QLabel(objectName="hint", wordWrap=True)
        self.list = QWidget(objectName="templateList")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 4, 0)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.list)
        buttons = QHBoxLayout()
        buttons.addWidget(self.choose, 1)
        buttons.addWidget(self.reload)
        layout = QVBoxLayout(self)
        layout.addWidget(self.folder)
        layout.addLayout(buttons)
        layout.addWidget(self.hint)
        layout.addWidget(scroll, 1)

    def ask_folder(self):
        start = str(self.session.template_folder or saved_folder() or "")
        path = QFileDialog.getExistingDirectory(self, "Template folder", start)
        if path:
            settings().setValue(FOLDER_KEY, path)
            self.folderChosen.emit(path)

    def refresh(self):
        """Rebuilt when the session holds other templates (a folder read); otherwise left as it is."""
        s = self.session
        if s.templates is self.shown:
            return
        self.shown = s.templates
        folder = s.template_folder
        self.folder.setText(folder.name if folder else "Templates")
        self.folder.setToolTip(str(folder or ""))
        self.choose.setText("Change folder…" if folder else "Choose folder…")
        self.reload.setVisible(folder is not None)
        self.hint.setText("Drag a template onto the timeline, or double-click it to add it at the end. "
                          "Right-click: replace the design with it." if s.templates else
                          "No session designs or scripts in this folder." if folder else
                          "Choose a folder: its session designs (.json) and session scripts (.sh), "
                          "subfolders included, become templates here.")
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget:
                widget.hide()           # at once: deleteLater() only takes effect on the next loop pass
                widget.deleteLater()
        groups: dict[str, list] = {}
        for number, template in enumerate(s.templates):
            groups.setdefault(template.group, []).append((number, template))
        for group, members in groups.items():
            self.rows.addWidget(self.section(group, members))
        self.rows.addStretch()

    def section(self, title: str, members: list) -> QWidget:
        """A subfolder: its name (click to fold it away) and its templates; those of the folder itself
        come first, without a name."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 6)
        column.setSpacing(4)
        body = QWidget()
        items = QVBoxLayout(body)
        items.setContentsMargins(0, 0, 0, 0)
        items.setSpacing(4)
        for number, template in members:
            item = TemplateItem(number, template)
            item.add.connect(lambda n: self.add.emit(n, -1))
            item.replace.connect(self.replace)
            items.addWidget(item)
        if not title:
            column.addWidget(body)
            return box
        header = QToolButton(objectName="group", checkable=True, toolButtonStyle=Qt.ToolButtonTextBesideIcon,
                             autoRaise=True, toolTip=title)
        bold = QFont(header.font())
        bold.setBold(True)
        count = f"  ({len(members)})"
        width = self.width() - 60 - QFontMetrics(bold).horizontalAdvance(count)
        header.setText(QFontMetrics(bold).elidedText(title, Qt.ElideMiddle, width) + count)

        def fold(folded: bool):
            body.setVisible(not folded)
            header.setArrowType(Qt.RightArrow if folded else Qt.DownArrow)
            (self.collapsed.add if folded else self.collapsed.discard)(title)
        header.toggled.connect(fold)
        header.setChecked(title in self.collapsed)
        fold(title in self.collapsed)
        column.addWidget(header)
        column.addWidget(body)
        return box