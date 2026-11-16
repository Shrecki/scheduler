"""The template library (left panel, design only): the session designs and scripts of a folder, as a
tree of its subfolders (click a folder to open or close it; right-click it to open or close all inside),
each drawn as a whole session at a glance. Drag one onto the timeline to insert its runs there,
double-click it to add them at the end, right-click it to replace the design with it.

The folder is remembered from one launch to the next (QSettings); which folders are open, across Reload."""

from dataclasses import dataclass, field
from pathlib import PurePath

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .blocks import DragSource, fmt
from .theme import CATEGORY_COLORS, color

FOLDER_KEY = "templates/folder"
TOOLTIP_RUNS = 25       # runs listed in a template's tooltip
INDENT = 12             # px, a folder's contents under its name


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
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, QFontMetrics(bold).elidedText(self.template.name, Qt.ElideMiddle, int(room)))

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


@dataclass
class Folder:
    """A folder of the tree: its subfolders by name, and its templates as (number, template)."""
    folders: dict = field(default_factory=dict)
    templates: list = field(default_factory=list)

    def count(self) -> int:
        return len(self.templates) + sum(folder.count() for folder in self.folders.values())

    def paths(self, path: str):
        """`path` and those of every folder inside, for opening or closing them all."""
        yield path
        for name, folder in self.folders.items():
            yield from folder.paths(f"{path}/{name}" if path else name)


class Branch(QWidget):
    """A folder's contents, indented under its name, with a line down their side."""
    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QPen(color("border"), 1))
        p.drawLine(QPointF(5.5, 0), QPointF(5.5, self.height()))


class TemplatePanel(QFrame):
    folderChosen = Signal(str)
    add = Signal(int, int)          # template number, insert before this run (-1: at the end)
    replace = Signal(int)           # template number

    def __init__(self, session):
        super().__init__(objectName="templates")
        self.session, self.shown = session, None
        self.open, self.open_in = {""}, None    # the folders open (paths from the chosen one), and in which
        self.headers: dict[str, QToolButton] = {}
        self.items: list[TemplateItem] = []
        self.setFixedWidth(280)
        self.choose = QPushButton("Choose folder…")
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
        self.shown, folder = s.templates, s.template_folder
        if folder != self.open_in:              # another folder: only its top open
            self.open, self.open_in = {""}, folder
        self.choose.setText("Change folder…" if folder else "Choose folder…")
        self.choose.setToolTip(f"Templates from {folder}" if folder else "The folder whose session designs (.json) "
                               "and session scripts (.sh), subfolders included, become templates")
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
        self.headers, self.items = {}, []
        tree = Folder()
        for number, template in enumerate(s.templates):
            node = tree
            for name in PurePath(template.group).parts:
                node = node.folders.setdefault(name, Folder())
            node.templates.append((number, template))
        if s.templates:
            self.rows.addWidget(self.branch(folder.name, "", tree, depth=0))
        self.rows.addStretch()

    def branch(self, name: str, path: str, node: Folder, depth: int) -> QWidget:
        """A folder: its name (click: open or close it) above its subfolders, then its templates."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        header = QToolButton(objectName="group", checkable=True, toolButtonStyle=Qt.ToolButtonTextBesideIcon,
                             autoRaise=True, toolTip=str(self.session.template_folder / path),
                             contextMenuPolicy=Qt.CustomContextMenu)
        self.headers[path] = header              # top-down, as shown
        bold = QFont(header.font())
        bold.setBold(True)
        count = f"  ({node.count()})"
        width = self.width() - 50 - depth * INDENT - QFontMetrics(bold).horizontalAdvance(count)
        header.setText(QFontMetrics(bold).elidedText(name, Qt.ElideMiddle, width) + count)
        body = Branch()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(INDENT, 0, 0, 4)
        rows.setSpacing(4)
        for sub in sorted(node.folders, key=str.lower):
            rows.addWidget(self.branch(sub, f"{path}/{sub}" if path else sub, node.folders[sub], depth + 1))
        for number, template in node.templates:
            item = TemplateItem(number, template)
            item.add.connect(lambda n: self.add.emit(n, -1))
            item.replace.connect(self.replace)
            rows.addWidget(item)
            self.items.append(item)

        def toggle(open_: bool):
            body.setVisible(open_)
            header.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
            (self.open.add if open_ else self.open.discard)(path)
        header.toggled.connect(toggle)
        header.customContextMenuRequested.connect(lambda pos: self.open_all(header, node, path, pos))
        header.setChecked(path in self.open)
        toggle(path in self.open)
        column.addWidget(header)
        column.addWidget(body)
        return box

    def open_all(self, header: QToolButton, node: Folder, path: str, pos):
        """A folder's menu: open or close it and every folder inside."""
        menu = QMenu(self)
        opened = menu.addAction("Open all inside")
        closed = menu.addAction("Close all inside")
        action = menu.exec(header.mapToGlobal(pos))
        if action in (opened, closed):
            for inner in node.paths(path):
                self.headers[inner].setChecked(action is opened)