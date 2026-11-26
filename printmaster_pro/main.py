import sys
from pathlib import Path
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from ui.window import Window


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('PrintMaster Pro')
    app.setOrganizationName('PrintMaster')
    font = Path(__file__).resolve().parent/'assets'/'Vazirmatn-Regular.ttf'
    font_id = QFontDatabase.addApplicationFont(str(font))
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    window = Window()
    window.show()
    window.open_paths([Path(p) for p in sys.argv[1:] if Path(p).is_file()])
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
