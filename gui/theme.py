"""Modern light theme for the DataFab Qt app (Fusion style + stylesheet)."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"
BG = "#f4f6fa"
CARD = "#ffffff"
BORDER = "#e3e7ee"
TEXT = "#1f2937"
MUTED = "#6b7280"
SUCCESS = "#16a34a"
DANGER = "#dc2626"
SIDEBAR = "#111827"

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, #page {{ background: {BG}; }}

/* ---------- sidebar ---------- */
#sidebar {{ background: {SIDEBAR}; }}
#appTitle {{ color: white; font-size: 15pt; font-weight: 700; padding: 18px 16px 2px 16px; }}
#appSubtitle {{ color: #9ca3af; font-size: 8.5pt; padding: 0 16px 14px 16px; }}
#navButton {{
    color: #d1d5db; background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 10px 14px; margin: 2px 10px; font-size: 10.5pt;
}}
#navButton:hover {{ background: #1f2937; color: white; }}
#navButton:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}
#versionLabel {{ color: #6b7280; font-size: 8pt; padding: 12px 16px; }}

/* ---------- cards ---------- */
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
#cardTitle {{ font-size: 11pt; font-weight: 700; }}
#muted {{ color: {MUTED}; }}
#pageTitle {{ font-size: 17pt; font-weight: 700; }}
#bigValue {{ font-size: 22pt; font-weight: 700; color: {ACCENT}; }}
#statusPill {{
    background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; border-radius: 10px; padding: 3px 10px;
}}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QPlainTextEdit#console {{
    background: #0f172a; color: #e2e8f0; border: none; border-radius: 10px;
    font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; padding: 8px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #f3f4f6; }}
QPushButton:disabled {{ color: #9ca3af; background: #f9fafb; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #93c5fd; }}
QPushButton#success {{ background: {SUCCESS}; color: white; border: none; font-weight: 600; }}
QPushButton#success:hover {{ background: #15803d; }}
QPushButton#danger {{ background: {DANGER}; color: white; border: none; font-weight: 600; }}
QPushButton#danger:hover {{ background: #b91c1c; }}
QToolButton {{ border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 9px; background: white; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
QToolButton:hover {{ background: #f3f4f6; }}

/* ---------- tables ---------- */
QTableView {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: #eef1f5; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #f8fafc; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: 600; color: {MUTED};
}}
QTableCornerButton::section {{ background: #f8fafc; border: none; }}

QCheckBox::indicator {{ width: 16px; height: 16px; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #cbd5e1; border-radius: 4px; min-width: 30px; }}
QStatusBar {{ background: white; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: {SIDEBAR}; color: white; border: none; padding: 6px; border-radius: 6px; }}
QTabWidget::pane {{ border: none; }}
"""


def _arrow_files() -> dict:
    """Write tiny SVG arrows (stylesheets need image files for combo/spin arrows)."""
    import os
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "datafab-theme")
    os.makedirs(folder, exist_ok=True)
    shapes = {"down": "M2 4 L6 8 L10 4", "up": "M2 8 L6 4 L10 8"}
    paths = {}
    for name, d in shapes.items():
        p = os.path.join(folder, f"{name}.svg")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
               f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.6" '
               f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(svg)
        except OSError:
            pass
        paths[name] = p.replace("\\", "/")
    return paths


def _arrow_qss() -> str:
    a = _arrow_files()
    return f"""
QComboBox::down-arrow {{ image: url("{a['down']}"); width: 12px; height: 12px; }}
QAbstractSpinBox {{ padding-right: 20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: none; background: transparent;
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 3px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 3px; }}
QAbstractSpinBox::up-arrow {{ image: url("{a['up']}"); width: 9px; height: 9px; }}
QAbstractSpinBox::down-arrow {{ image: url("{a['down']}"); width: 9px; height: 9px; }}
"""


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.AlternateBase, QColor("#f8fafc"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor("white"))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor(SIDEBAR))
    pal.setColor(QPalette.ToolTipText, QColor("white"))
    app.setPalette(pal)
    font = QFont("Segoe UI")
    font.setStyleHint(QFont.SansSerif)
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET + _arrow_qss())
