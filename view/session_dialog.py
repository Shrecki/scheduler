"""Startup dialog: subject, session number and, from session 02 on, the previous session's file."""

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox)


@dataclass
class SessionInfo:
    subject_name: str               # "sub-01"
    session_number: int
    previous: dict | None = None    # the previous session's file, parsed


class SessionDialog(QDialog):
    def __init__(self, data_root: Path):
        super().__init__(windowTitle="New session", minimumWidth=460)
        self.data_root, self.previous = Path(data_root), None
        self.subject = QLineEdit(placeholderText="01")
        self.subject.setValidator(QRegularExpressionValidator(QRegularExpression("[A-Za-z0-9]*")))  # BIDS label
        self.number = QSpinBox(minimum=1, maximum=99, prefix="ses-")
        self.number.textFromValue = lambda v: f"{v:02d}"
        self.file = QLineEdit(placeholderText="Configuration saved at the end of the previous session")
        browse = QPushButton("Browse…", clicked=self.browse)
        self.message = QLabel(wordWrap=True)
        self.ok = QPushButton("Continue", objectName="primary", default=True, clicked=self.accept)

        subject_row, file_row, buttons = QHBoxLayout(), QHBoxLayout(), QHBoxLayout()
        subject_row.addWidget(QLabel("sub-", objectName="hint"))
        subject_row.addWidget(self.subject)
        file_row.addWidget(self.file)
        file_row.addWidget(browse)
        buttons.addStretch()
        buttons.addWidget(QPushButton("Cancel", clicked=self.reject))
        buttons.addWidget(self.ok)
        form = QFormLayout(self)
        form.addRow("Subject", subject_row)
        form.addRow("Session", self.number)
        form.addRow("Previous session", file_row)
        form.addRow(self.message)
        form.addRow(buttons)
        self.form = form
        for field in (self.subject.textChanged, self.number.valueChanged, self.file.textChanged):
            field.connect(self.validate)
        self.validate()

    def validate(self) -> bool:
        level, text = self.check()
        self.message.setText(text)
        self.message.setProperty("level", level)
        self.message.style().polish(self.message)       # re-apply the style for the new level
        self.form.setRowVisible(2, self.number.value() > 1)
        self.ok.setEnabled(level != "error")
        return level != "error"

    def check(self) -> tuple[str | None, str]:
        """(level, message): an "error" blocks Continue, a "warning" does not."""
        self.previous = None
        if not self.subject.text():
            return "error", "Enter a subject label, for example 01."
        name, n = f"sub-{self.subject.text()}", self.number.value()
        if n > 1:
            path = Path(self.file.text()).expanduser()
            if not path.is_file():
                return "error", f"Choose the file saved at the end of session {n - 1:02d}."
            try:
                self.previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                return "error", f"This file cannot be read as JSON: {exc}"
            if self.previous.get("subject_name", name) != name:
                return "warning", f"This file belongs to {self.previous['subject_name']}."
        if (self.data_root / name / f"ses-{n:02d}").exists():
            return "warning", "This session folder already exists: its files may be overwritten."
        return None, ""

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Previous session", str(self.data_root), "JSON (*.json)")
        if path:
            self.file.setText(path)

    def accept(self):
        if self.validate():
            super().accept()

    def info(self) -> SessionInfo:
        return SessionInfo(f"sub-{self.subject.text()}", self.number.value(), self.previous)
