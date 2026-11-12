"""Colours and the Qt style sheet (QSS, Qt's CSS). apply_theme(app) once at startup."""

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

TOKENS = {
    "light": dict(surface="#EDF0F3", panel="#FFFFFF", ink="#18212B", muted="#5A6675", border="#C3CCD5",
                  accent="#0C6A78", on_accent="#FFFFFF", error="#B42318", warning="#8A5300",
                  disabled="#D6DCE2", ring="#1F000000"),
    "dark": dict(surface="#1C2229", panel="#252D36", ink="#E4E9EF", muted="#9AA5B2", border="#3A4551",
                 accent="#4DB2C0", on_accent="#0D1A1D", error="#F0877D", warning="#E2B25E",
                 disabled="#2F3842", ring="#2EFFFFFF"),
}

# Block colours (fill, label) per Category name, the same in both themes. Validated as a set: every
# pair stays distinct for colour-blind viewers (the weakest pair, ochre/green, goes to the MEG and MRI
# localizers, which never share a session) and every label has >= 5.2:1 contrast.
CATEGORY_COLORS = {
    "COGNITIVE_TASK_MRI": ("#0C57C9", "#FFFFFF"), "COGNITIVE_TASK_MEG": ("#0CC9BF", "#000000"), 
    "RESTING_STATE": ("#7988FC", "#000000"),
    "MOVIE_WATCHING": ("#82477E", "#FFFFFF"), "VIDEO_GAME": ("#DB55A9", "#000000"),
    "LOCALIZER_MEG": ("#B5820C", "#000000"), "LOCALIZER_MRI": ("#307C1B", "#FFFFFF"),
    "EYETRACKER_CALIBRATION": ("#7C1B77", "#FFFFFF")
}

QSS = Template("""
QWidget { color: $ink; }
QMainWindow, QDialog { background: $surface; }
QFrame#bar { background: $panel; border-color: $border; border-style: solid; border-width: 0 0 1px 0; }
QFrame#palette { background: $panel; border-left: 1px solid $border; }
QLabel#title { font-size: 18pt; }
QLabel#hint { color: $muted; }
QLabel[level="error"] { color: $error; }
QLabel[level="warning"] { color: $warning; }
QLineEdit, QPushButton { background: $panel; border: 1px solid $border; border-radius: 6px; padding: 6px 10px; }
QAbstractSpinBox QLineEdit { border: none; padding: 0; background: transparent; }  /* a spin box's inner field */
QLineEdit:focus, QPushButton:focus { border-color: $accent; }
QPushButton:disabled { color: $muted; background: $disabled; }
QPushButton#primary { background: $accent; color: $on_accent; border-color: $accent; font-weight: 600; }
QPushButton#primary:disabled { background: $disabled; color: $muted; border-color: $disabled; }
""")

_tokens = TOKENS["light"]


def color(name: str) -> QColor:
    return QColor(_tokens[name])


def apply_theme(app, mode: str = "auto"):
    global _tokens
    if mode == "auto":
        mode = "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    _tokens = TOKENS[mode]
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Ubuntu", "Segoe UI", ".AppleSystemUIFont", "Noto Sans", "DejaVu Sans"])
    font.setPointSize(10)
    app.setFont(font)
    palette = QPalette()            # for what QSS does not reach (file dialogs, menus, scroll bars)
    for role, key in ((QPalette.Window, "surface"), (QPalette.WindowText, "ink"), (QPalette.Base, "panel"),
                      (QPalette.Text, "ink"), (QPalette.Button, "panel"), (QPalette.ButtonText, "ink"),
                      (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "on_accent")):
        palette.setColor(role, color(key))
    app.setPalette(palette)
    app.setStyleSheet(QSS.substitute(_tokens))
