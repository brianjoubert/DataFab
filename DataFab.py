"""DataFab — wafer database generator + wafer map with Keithley 2450 measurement (Qt)."""
import sys


# PyInstaller windowed builds have no console: give loguru (gdsfactory) somewhere to write.
class _NullWriter:
    def write(self, *a, **k): pass
    def flush(self): pass


if sys.stdout is None:
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()

import argparse
import os


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="datafab", description=__doc__)
    ap.add_argument("file", nargs="?", help="config (.json) or generated *_database.json to open")
    ap.add_argument("--simulate", action="store_true", help="simulate the Keithley (no instrument)")
    ap.add_argument("--resource", help="VISA resource of the Keithley 2450")
    ap.add_argument("--screenshot", metavar="PNG", help="save a screenshot of the window and quit")
    ap.add_argument("--page", choices=["generate", "wafer"], help="page to show (with --screenshot)")
    ap.add_argument("--self-test", action="store_true", help="start, open FILE if given, and exit (CI check)")
    ap.add_argument("--generate", metavar="CONFIG", help="generate the database from CONFIG without the GUI")
    ap.add_argument("--version", action="store_true")
    return ap.parse_known_args(argv)[0]


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    from version import __version__
    if args.version:
        print(f"DataFab {__version__}")
        return 0
    if args.generate:
        from database_4 import generate_from_config
        try:
            generate_from_config(os.path.abspath(args.generate))
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from gui.theme import apply_theme
    from gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("DataFab")
    app.setApplicationName("DataFab")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("datafab")
    apply_theme(app)

    win = MainWindow(simulate=args.simulate, resource=args.resource)
    if args.file:
        win.open_path(os.path.abspath(args.file))
    if args.page:
        win.show_page(0 if args.page == "generate" else 1)
    win.show()

    if args.self_test or args.screenshot:
        is_db = bool(args.file) and win.wafer.db is not None
        if is_db and win.wafer.model.rowCount() and win.wafer.model.n_res:
            win.wafer.table.setCurrentIndex(win.wafer.proxy.index(0, 1))  # select first cell
        if args.simulate:
            QTimer.singleShot(200, win.wafer.toggle_measurement)  # simulated live measurement

        def finish():
            checks = {"window": win.isVisible()}
            if args.file and "_database" in os.path.basename(args.file):
                checks["database"] = is_db and len(win.wafer.db.dies) > 0
            if args.simulate:
                checks["measurement"] = len(win.wafer.r_data) > 0
            if args.screenshot:
                win.grab().save(args.screenshot)
            ok = all(checks.values())
            print(f"DataFab {__version__} self-test: {'OK' if ok else 'FAILED'} {checks}")
            win.wafer.shutdown()
            app.exit(0 if ok else 1)
        QTimer.singleShot(3500, finish)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
