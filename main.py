import sys
from PySide6.QtWidgets import QApplication

from annotator.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ultralytics GUI Annotator (JSON)")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())