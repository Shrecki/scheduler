"""A session on paper, as a PDF to print or keep with the data (write_pdf). A4 landscape, in sections:
- the overview: whose session, when, its length and rig; the whole session on one time axis, its runs
  as planned and, once recorded, as they played; the time per category;
- the timeline: the runs as planned, to scale, in lines of a few minutes each, each run with its task,
  its curriculum if it has one, and its duration;
- the runs: each with its task, when it was planned, its parameters and, once recorded, each time it
  played (when, how long, exit code).
Drawn for paper: light colours, whatever the window's theme.
"""

import itertools
from datetime import datetime, timedelta
from functools import partial
from math import ceil
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter,
                           QPen, QTextLayout, QTextOption)

from model.session import TimelineMode
from model.task_definitions import get_task
from model.tasks import SESSION_RIG
from .blocks import fmt
from .theme import CATEGORY_COLORS

INK, MUTED, RULE, GRID, ERROR = (QColor(c) for c in ("#18212B", "#5A6675", "#C3CCD5", "#E3E7EB", "#B42318"))
PAGE_W, PAGE_H, MARGIN = 842, 595, 36           # A4 landscape, in points (1/72 in)
LEFT, RIGHT, BOTTOM = MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN - 24     # the footer below BOTTOM
CONTINUED_TOP = MARGIN + 22                     # where a page after the first goes on
LANE_H, GUTTER, PAD = 44, 50, 4                 # the overview's lanes, their names beside them; table cells
BAND, LINE_H = 50, 76                           # a line of the timeline: its runs, then with its times
LINE_MIN = (10, 15, 20, 30, 45, 60, 90, 120)    # minutes a line: the most that keeps the shortest run READABLE
READABLE, MAX_LINES = 64, 18                    # points: its number, task and duration; lines at most, if can be
STEPS_MIN = (1, 2, 5, 10, 15, 20, 30, 60, 120)  # time axes: the first step giving at most 10 ticks (6 on a line)
TITLE_H = 26                                    # a section's title
GAP = "   "                                     # between two parameters on a line
MIN = 60_000


def write_pdf(session, path: str | Path):
    """Draw `session` into a PDF at `path`, A4 landscape. OSError if the file cannot be written."""
    writer = QPdfWriter(str(path))
    writer.setResolution(72)                                    # 1 unit: 1 point
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Landscape, QMarginsF()))
    writer.setTitle(f"{session.subject} ses-{session.session_number:02d}")
    writer.setCreator("Block scheduler")
    report, p = Report(session, writer), QPainter()
    if not p.begin(writer):
        raise OSError("cannot write the file")
    try:
        report.draw(p, writer)
    finally:
        p.end()


def font(size: float, bold: bool = False) -> QFont:
    f = QFont()                                 # the application's font
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def step(span_ms: float, ticks: int) -> int:
    """The first of STEPS_MIN that cuts `span_ms` in at most `ticks` steps, in ms."""
    return next((m for m in STEPS_MIN if span_ms / (m * MIN) <= ticks), STEPS_MIN[-1]) * MIN


class Report:
    def __init__(self, session, device):
        s = self.s = session
        self.device, self.now, self.exported = device, session.now(), datetime.now()
        self.recorded = s.rec_start is not None
        self.fonts = {"title": font(17, True), "label": font(7), "value": font(9.5), "small": font(7),
                      "block": font(7), "bold": font(7, True), "box": font(7.5), "box_bold": font(7.5, True),
                      "body": font(8), "strong": font(8, True), "section": font(10.5, True)}
        self.metrics = {name: QFontMetricsF(f, device) for name, f in self.fonts.items()}
        self.starts = list(itertools.accumulate((b.duration_ms for b in s.blocks), initial=0))   # planned
        self.played = [(p, p.started_at - s.rec_start, p.played_ms(self.now))
                       for p in s.plays if p.started_at is not None] if self.recorded else []
        # the overview: the whole session on one axis
        span = max(s.total_ms, self.starts[-1], *(at + ms for _, at, ms in self.played), MIN)
        self.step = step(span, 10)
        self.span = ceil(span / self.step) * self.step
        self.x0 = LEFT + (GUTTER if self.recorded else 0)
        lanes = 2 if self.recorded else 1
        self.lanes_top = 124
        self.axis_y = self.lanes_top + lanes * LANE_H + (lanes - 1) * 8 + 3
        self.legend = self.legend_items()
        self.composition_top = self.axis_y + 30
        self.note_y = self.composition_top + 14 + (max((row for row, _, _ in self.legend), default=0) + 1) * 15
        failed = any(p.exit_code not in (0, None) for p, _, _ in self.played)
        self.note = "Outlined: runs whose task exited with a code other than 0 (see Exit in Runs)." if failed else ""
        self.flow_top = self.note_y + (16 if self.note else 0) + 18
        # the timeline: lines of line_ms, as many as the planned runs and the session's end take
        shortest = min((b.duration_ms for b in s.blocks if b.duration_ms >= MIN), default=MIN)
        readable = [m for m in LINE_MIN if (RIGHT - LEFT) * shortest / (m * MIN) >= READABLE] or LINE_MIN[:1]
        end = max(self.starts[-1], s.total_ms, MIN)
        self.line_ms = next((m * MIN for m in LINE_MIN if m >= readable[-1] and end / (m * MIN) <= MAX_LINES),
                            LINE_MIN[-1] * MIN)
        self.lines, self.tick = ceil(end / self.line_ms), step(self.line_ms, 6)
        # the runs
        self.columns = [("Run", 46), ("Task", 128), ("Category", 96), ("Planned at", 52), ("Length", 44), ("Parameters", 0)]
        if self.recorded:
            self.columns += [("Played at", 54), ("For", 44), ("Exit", 34)]
        flex = RIGHT - LEFT - sum(w for _, w in self.columns)
        self.columns = [(name, w or flex) for name, w in self.columns]

    # --- the overview ------------------------------------------------------------------------------------
    def x(self, ms: float) -> float:
        return self.x0 + (RIGHT - self.x0) * ms / self.span

    def facts(self) -> list[tuple[str, str]]:
        s, ran = self.s, sum(b.started_at is not None for b in self.s.blocks)
        length = f"{fmt(s.total_ms)} of {'runs' if s.mode == TimelineMode.TOTAL_BLOCK_TIME else 'recording'}, " \
                 f"{'strict' if s.strict else 'lenient'} end time"
        if self.recorded:
            start = s.rec_start_wall
            end = start + timedelta(milliseconds=(s.rec_end or self.now) - s.rec_start)
            when = f"{start:%a %d %b %Y, %H:%M} to {end:%H:%M}" if end.date() == start.date() else \
                   f"{start:%a %d %b %Y, %H:%M} to {end:%a %d %b, %H:%M}"
            facts = [("Recorded", when), ("Runs", f"{len(s.blocks)} planned, {ran} played")]
        else:
            facts = [("Status", "Plan, not recorded yet"), ("Runs", f"{len(s.blocks)}, {fmt(self.starts[-1])} in all")]
        return facts + [("Length", length)] + ([("Rig", s.rig)] if s.rigs else [])

    def header(self, p: QPainter):
        s = self.s
        self.text(p, LEFT, MARGIN, f"{s.subject}  /  ses-{s.session_number:02d}", "title")
        x = LEFT
        for label, value in self.facts():
            self.text(p, x, 72, label, "label", MUTED)
            self.text(p, x, 82, value, "value")
            x += max(self.metrics["label"].horizontalAdvance(label), self.metrics["value"].horizontalAdvance(value)) + 32

    def overview(self, p: QPainter):
        s, top, small = self.s, self.lanes_top, self.metrics["small"]
        lanes = [("Planned", top)] + ([("Played", top + LANE_H + 8)] if self.recorded else [])
        p.setPen(QPen(GRID, 0.5))
        for t in range(0, self.span + 1, self.step):                   # the grid, behind the runs
            p.drawLine(QPointF(self.x(t), top), QPointF(self.x(t), self.axis_y))
        for name, y in (lanes if self.recorded else []):
            self.text(p, LEFT, y + (LANE_H - small.height()) / 2, name, "small", MUTED)
        for i, b in enumerate(s.blocks):
            self.overview_box(p, QRectF(self.x(self.starts[i]), top, self.x(self.starts[i + 1]) - self.x(self.starts[i]),
                                        LANE_H), b, i, faded=self.starts[i] >= s.total_ms)     # past the end: cut
        for play, at, ms in self.played:
            self.overview_box(p, QRectF(self.x(at), lanes[1][1], self.x(at + ms) - self.x(at), LANE_H),
                              s.blocks[play.index], play.index, outlined=play.exit_code not in (0, None))
        p.setPen(QPen(RULE, 0.75))                                      # the time axis
        p.drawLine(QPointF(self.x0, self.axis_y), QPointF(RIGHT, self.axis_y))
        for t in range(0, self.span + 1, self.step):
            p.drawLine(QPointF(self.x(t), self.axis_y), QPointF(self.x(t), self.axis_y + 3))
            label = fmt(t)
            w = small.horizontalAdvance(label)
            self.text(p, min(max(self.x(t) - w / 2, self.x0), RIGHT - w), self.axis_y + 5, label, "small", MUTED)
        end = self.x(s.total_ms)                                        # the session's end
        p.setPen(QPen(INK, 0.9, Qt.DashLine))
        p.drawLine(QPointF(end, top - 4), QPointF(end, self.axis_y))
        label = f"end {fmt(s.total_ms)}"
        w = small.horizontalAdvance(label)
        self.text(p, end - w - 3 if end + w + 3 > RIGHT else end + 3, top - 14, label, "small")

    def shape(self, p: QPainter, rect: QRectF, fill: str, faded=False, outlined=False, cut=(False, False)) -> QRectF:
        """A run's rounded box, square where a line of the timeline cuts it; the box drawn. Its text goes
        in its category's label colour, or MUTED when faded."""
        rect = rect.adjusted(0, 0, 0 if cut[1] else -min(1.5, rect.width() / 3), 0)     # a gap after a run
        radius = min(3, rect.width() / 2)
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)                                # the square corners added, not cut out
        path.addRoundedRect(rect, radius, radius)
        for x, square in ((rect.left(), cut[0]), (rect.right() - radius, cut[1])):
            if square:
                path.addRect(QRectF(x, rect.top(), radius, rect.height()))
        colour = QColor(fill)
        if faded:                                                       # past the end: paler, still opaque
            colour = QColor.fromRgbF(*(0.35 * c + 0.65 for c in colour.getRgbF()[:3]))
        p.fillPath(path.simplified(), colour)
        if outlined:
            p.strokePath(path.simplified(), QPen(ERROR, 1.6))
        return rect

    def overview_box(self, p: QPainter, rect: QRectF, block, index: int, faded=False, outlined=False):
        fill, ink = CATEGORY_COLORS[block.category.name]
        rect = self.shape(p, rect, fill, faded, outlined)
        area, bold = rect.adjusted(4, 5, -3, -5), self.metrics["bold"]
        title = next((t for t in (f"run-{index + 1:02d}", f"{index + 1}") if bold.horizontalAdvance(t) <= area.width()), "")
        p.setPen(MUTED if faded else QColor(ink))
        if title:
            p.setFont(self.fonts["bold"])
            p.drawText(area, Qt.AlignLeft | Qt.AlignTop, title)
        task = self.metrics["block"].elidedText(block.label, Qt.ElideRight, area.width())
        if title.startswith("run") and len(task) > 1:
            p.setFont(self.fonts["block"])
            p.drawText(area, Qt.AlignLeft | Qt.AlignBottom, task)

    def legend_items(self) -> list[tuple[int, float, list]]:
        """The time per category, as (row, x, runs of text) laid out across the page."""
        parts = self.s.composition()
        total, items, row, x = sum(ms for _, ms in parts) or 1, [], 0, LEFT
        for category, ms in parts:
            share = "<1%" if ms / total < 0.005 else f"{ms / total:.0%}"
            time = f"{round(ms / MIN, 1):g} min" if ms >= MIN else f"{ms / 1000:.0f} s"
            runs = [(category.value, "body", INK), (share, "strong", INK), (time, "body", MUTED)]
            width = 11 + sum(self.metrics[f].horizontalAdvance(t) + 5 for t, f, _ in runs)
            if x + width > RIGHT and x > LEFT:
                row, x = row + 1, LEFT
            items.append((row, x, [(CATEGORY_COLORS[category.name][0], None, None)] + runs))
            x += width + 18
        return items

    def composition(self, p: QPainter):
        parts, top = self.s.composition(), self.composition_top
        total, x = sum(ms for _, ms in parts) or 1, LEFT
        p.setPen(Qt.NoPen)
        for category, ms in parts:                                      # a bar split in proportion
            width = (RIGHT - LEFT) * ms / total
            p.setBrush(QColor(CATEGORY_COLORS[category.name][0]))
            p.drawRoundedRect(QRectF(x, top, max(width - 1.5, 0.8), 6), 2, 2)
            x += width
        for row, x, runs in self.legend:
            y = top + 14 + row * 15
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(runs[0][0]))
            p.drawRoundedRect(QRectF(x, y + 2, 7, 7), 1.5, 1.5)
            x += 11
            for text, f, colour in runs[1:]:
                x += self.text(p, x, y, text, f, colour) + 5
        if self.note:
            self.text(p, LEFT, self.note_y, self.note, "small", MUTED)

    # --- the timeline ------------------------------------------------------------------------------------
    def timeline_line(self, p: QPainter, top: float, k: int):
        """Line k of the timeline: the planned runs from k line_ms on, to scale, with their times below."""
        s, small = self.s, self.metrics["small"]
        t0, t1 = k * self.line_ms, (k + 1) * self.line_ms
        x = lambda t: LEFT + (RIGHT - LEFT) * (t - t0) / self.line_ms          # noqa: E731
        p.setPen(QPen(GRID, 0.5))
        for t in range(t0, t1 + 1, self.tick):
            p.drawLine(QPointF(x(t), top), QPointF(x(t), top + BAND + 3))
        for i, b in enumerate(s.blocks):
            a, e = self.starts[i], self.starts[i + 1]
            if e > t0 and a < t1:
                self.timeline_box(p, QRectF(x(max(a, t0)), top, x(min(e, t1)) - x(max(a, t0)), BAND), b, i,
                                  faded=a >= s.total_ms, cut=(a < t0, e > t1))
        taken = None                                                    # the session's end, if on this line
        if t0 < s.total_ms <= t1 or s.total_ms == t0 == 0:
            end, label = x(s.total_ms), f"end {fmt(s.total_ms)}"
            w = self.metrics["bold"].horizontalAdvance(label)
            p.setPen(QPen(INK, 0.9, Qt.DashLine))
            p.drawLine(QPointF(end, top - 3), QPointF(end, top + BAND + 3))
            left = end - w / 2 if end + w / 2 <= RIGHT else RIGHT - w
            taken = (left - 6, left + w + 6)
            self.text(p, left, top + BAND + 5, label, "bold")
        for t in range(t0, t1 + 1, self.tick):
            label = fmt(t)
            w = small.horizontalAdvance(label)
            left = min(max(x(t) - w / 2, LEFT), RIGHT - w)
            if not taken or left + w < taken[0] or left > taken[1]:
                self.text(p, left, top + BAND + 5, label, "small", MUTED)

    def timeline_box(self, p: QPainter, rect: QRectF, block, index: int, faded=False, cut=(False, False)):
        """A run on the timeline: its number and duration, its task, its curriculum if it has one."""
        fill, ink = CATEGORY_COLORS[block.category.name]
        rect = self.shape(p, rect, fill, faded, cut=cut)
        area, bold, regular = rect.adjusted(5, 5, -4, -4), self.metrics["box_bold"], self.metrics["box"]
        title, duration = f"run-{index + 1:02d}", fmt(block.duration_ms)
        p.setPen(MUTED if faded else QColor(ink))
        if bold.horizontalAdvance(title) + 6 + regular.horizontalAdvance(duration) <= area.width():
            p.setFont(self.fonts["box"])
            p.drawText(area, Qt.AlignRight | Qt.AlignTop, duration)
        elif bold.horizontalAdvance(title) > area.width():
            title = f"{index + 1}" if bold.horizontalAdvance(f"{index + 1}") <= area.width() else ""
        p.setFont(self.fonts["box_bold"])
        p.drawText(area, Qt.AlignLeft | Qt.AlignTop, title)
        curriculum = {**get_task(block.task).defaults(), **block.params}.get("curriculum")
        y = area.top() + bold.lineSpacing() + 2
        for line in ([block.label] + ([str(curriculum)] if curriculum else [])) if title.startswith("run") else []:
            line = regular.elidedText(line, Qt.ElideRight, area.width())
            if len(line) > 1 and y + regular.height() <= area.bottom() + 1:
                p.setFont(self.fonts["box"])
                p.drawText(QPointF(area.left(), y + regular.ascent()), line)
            y += regular.lineSpacing()

    # --- the runs ----------------------------------------------------------------------------------------
    def rows(self) -> list[tuple[float, int, list]]:
        """Each run as (height, index, cells); a cell: a list of (line, colour)."""
        s, body, out = self.s, self.metrics["body"], []
        widths = [w - 2 * PAD for _, w in self.columns]
        for i, b in enumerate(s.blocks):
            cells = [[(f"run-{i + 1:02d}", INK)], [(line, INK) for line in self.wrap(b.label, widths[1])],
                     [(line, INK) for line in self.wrap(b.category.value, widths[2])],
                     [(fmt(self.starts[i]), INK)], [(fmt(b.duration_ms), INK)],
                     [(line, INK) for line in self.wrap_pairs(self.parameters(b), widths[5])]]
            if self.recorded:
                plays = [p for p in s.plays if p.index == i]
                cells += [[("could not launch" if p.started_at is None else fmt(p.started_at - s.rec_start),
                            MUTED if p.started_at is None else INK) for p in plays] or [("not played", MUTED)],
                          [(fmt(p.played_ms(self.now)) if p.started_at is not None else "", INK) for p in plays],
                          [("n/a" if p.exit_code is None else str(p.exit_code), INK if p.exit_code in (0, None) else ERROR)
                           for p in plays]]
            out.append((max(len(c) for c in cells) * body.lineSpacing() + 2 * PAD, i, cells))
        return out

    def parameters(self, block) -> list[str]:
        """`name: value` for each parameter; the session's rig named."""
        pairs = []
        for name, value in {**get_task(block.task).defaults(), **block.params}.items():
            if value == SESSION_RIG:
                value = f"session rig ({self.s.rig})"
            elif isinstance(value, bool):
                value = "on" if value else "off"
            if value not in ("", None):
                pairs.append(" ".join(f"{name}: {value}".split()))
        return pairs

    def wrap_pairs(self, pairs: list[str], width: float) -> list[str]:
        """`pairs` on lines `width` wide, each whole on one line unless it is longer than a line."""
        lines, advance = [], self.metrics["body"].horizontalAdvance
        for pair in pairs:
            if lines and advance(lines[-1] + GAP + pair) <= width:
                lines[-1] += GAP + pair
            else:
                lines += self.wrap(pair, width)
        return lines or [""]

    def wrap(self, text: str, width: float) -> list[str]:
        """`text` broken into lines `width` wide: between words, else anywhere."""
        layout = QTextLayout(text, self.fonts["body"], self.device)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        lines = []
        while (line := layout.createLine()).isValid():
            line.setLineWidth(width)
            lines.append(text[line.textStart():line.textStart() + line.textLength()].strip())
        layout.endLayout()
        return lines or [""]

    def table_head(self, p: QPainter, top: float):
        x = LEFT
        for name, w in self.columns:
            self.text(p, x + PAD, top + PAD, name, "label", MUTED)
            x += w
        top += self.metrics["label"].lineSpacing() + 2 * PAD
        p.setPen(QPen(RULE, 0.75))
        p.drawLine(QPointF(LEFT, top), QPointF(RIGHT, top))

    def table_row(self, p: QPainter, top: float, row: tuple):
        height, index, cells = row
        body, x = self.metrics["body"], LEFT
        for (name, w), cell in zip(self.columns, cells):
            swatch = 9 if name == "Run" else 0
            for n, (line, colour) in enumerate(cell):
                self.text(p, x + PAD + swatch, top + PAD + n * body.lineSpacing(), line, "body", colour)
            x += w
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(CATEGORY_COLORS[self.s.blocks[index].category.name][0]))
        p.drawRoundedRect(QRectF(LEFT + PAD, top + PAD + (body.height() - 6) / 2, 6, 6), 1.5, 1.5)
        p.setPen(QPen(GRID, 0.5))
        p.drawLine(QPointF(LEFT, top + height), QPointF(RIGHT, top + height))

    # --- the pages ---------------------------------------------------------------------------------------
    def section(self, p: QPainter, top: float, title: str, note: str = ""):
        width = self.text(p, LEFT, top, title, "section")
        if note:
            self.text(p, LEFT + width + 10, top + 3, note, "small", MUTED)

    def flow(self) -> list[list[tuple[float, object]]]:
        """Where the timeline and the runs go after the overview: (top, draw(painter, top)) on each page.
        A section's title stays with what follows it; the runs' column names start each page they are on."""
        head = self.metrics["label"].lineSpacing() + 2 * PAD + 2
        items = [("title", TITLE_H, partial(self.section, title="Timeline",
                                            note=f"as planned, {self.line_ms // MIN} min a line"))]
        items += [("line", LINE_H, partial(self.timeline_line, k=k)) for k in range(self.lines)]
        items += [("title", TITLE_H + head, lambda p, top: (self.section(p, top, "Runs"), self.table_head(p, top + TITLE_H)))]
        items += [("run", row[0], partial(self.table_row, row=row)) for row in self.rows()]
        pages, top = [[]], self.flow_top
        for n, (kind, height, draw) in enumerate(items):
            need = height + (items[n + 1][1] if kind == "title" and n + 1 < len(items) else 0)
            if top + need > BOTTOM and (pages[-1] or len(pages) == 1):
                pages.append([])
                top = CONTINUED_TOP
                if kind == "run":
                    pages[-1].append((top, self.table_head))
                    top += head
            pages[-1].append((top, draw))
            top += height
        return pages

    def footer(self, p: QPainter, page: int, pages: int):
        y = PAGE_H - MARGIN - self.metrics["small"].height()
        self.text(p, LEFT, y, f"Exported {self.exported:%d %b %Y, %H:%M} by the block scheduler", "small", MUTED)
        label = f"{page} / {pages}"
        self.text(p, RIGHT - self.metrics["small"].horizontalAdvance(label), y, label, "small", MUTED)

    def draw(self, p: QPainter, writer: QPdfWriter):
        p.setRenderHint(QPainter.Antialiasing)
        pages = self.flow()
        for n, placed in enumerate(pages, 1):
            if n == 1:
                self.header(p)
                self.overview(p)
                self.composition(p)
            else:
                writer.newPage()
                self.text(p, LEFT, MARGIN, f"{self.s.subject}  /  ses-{self.s.session_number:02d}, continued",
                          "body", MUTED)
            for top, draw in placed:
                draw(p, top)
            self.footer(p, n, len(pages))

    def text(self, p: QPainter, x: float, y: float, text: str, f: str, colour: QColor = INK) -> float:
        """`text` with its top left at (x, y); its width."""
        p.setFont(self.fonts[f])
        p.setPen(colour)
        p.drawText(QPointF(x, y + self.metrics[f].ascent()), text)
        return self.metrics[f].horizontalAdvance(text)