import os

# Qt tests run headless; must be set before any QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
