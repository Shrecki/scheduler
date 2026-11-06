"""Above the timeline: how much of it each category takes. A bar split in proportion, and for each
category its share and total time."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from model.tasks import Category
from .theme import CATEGORY_COLORS, color
from .timeline import PAD

MIN = 60_000


class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.parts = []                 # (category, ms)
        self.setFixedHeight(8)

    def paintEvent(self, event):
        total, x = sum(ms for _, ms in self.parts), 0.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for category, ms in self.parts:
            width = self.width() * ms / total
            p.setBrush(QColor(CATEGORY_COLORS[category.name][0]))
            p.drawRoundedRect(QRectF(x, 0, max(width - 2, 1), self.height()), 3, 3)   # 2 px between parts
            x += width


class Composition(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session, self.bar = session, Bar()
        self.labels = {category: QLabel() for category in Category}
        legend = QHBoxLayout()
        legend.setSpacing(28)
        for label in self.labels.values():
            label.setText("■<br>■")         # two lines: to size the widget, so it never changes height
            legend.addWidget(label)
        legend.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAD, 4, PAD, 0)     # aligned with the timeline's runs
        layout.addWidget(self.bar)
        layout.addLayout(legend)
        self.setFixedHeight(self.sizeHint().height())

    def refresh(self):
        parts = self.session.composition()
        total, times = sum(ms for _, ms in parts), dict(parts)
        muted = color("muted").name()
        for category, label in self.labels.items():
            ms = times.get(category, 0)
            label.setVisible(ms > 0)
            if ms:
                share = "&lt;1%" if ms / total < 0.005 else f"{ms / total:.0%}"
                time = f"{round(ms / MIN, 1):g} min" if ms >= MIN else f"{ms / 1000:.0f} s"
                label.setText(f'<span style="color:{CATEGORY_COLORS[category.name][0]}">■</span>&nbsp;'
                              f'{category.value}<br><b>{share}</b>&nbsp;&nbsp;<span style="color:{muted}">{time}</span>')
        self.bar.parts = parts
        self.bar.update()