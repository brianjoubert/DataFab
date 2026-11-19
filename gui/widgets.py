"""Small reusable widgets."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class Card(QFrame):
    """White rounded panel with an optional title row."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(16, 14, 16, 16)
        self.outer.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("cardTitle")
            self.header.addWidget(lbl)
        self.header.addStretch(1)
        self.outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.outer.addLayout(self.body)


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def format_ohm(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.4g} MΩ"
    if a >= 1e3:
        return f"{v / 1e3:.4g} kΩ"
    return f"{v:.4g} Ω"
