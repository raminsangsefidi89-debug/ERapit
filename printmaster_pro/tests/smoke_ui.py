"""Run with python -m tests.smoke_ui; does not need a physical printer."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication
from ui.window import Window


def main() -> int:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication([])
    with TemporaryDirectory() as tmp:
        app.setApplicationName('PrintMasterProSmoke-' + Path(tmp).name)
        QStandardPaths.setTestModeEnabled(True)
        window = Window()
        assert window.layoutDirection().name == 'RightToLeft'
        window.show()
        path = Path(tmp)/'آزمایش.txt'
        path.write_text('سلام\nWindows smoke test', encoding='utf-8')
        def fail(text):
            print(text, flush=True)
            app.exit(1)
        window.error = fail
        window.open_paths([path])
        def verify():
            tab = window.current()
            if tab and tab.output and not tab.image.pixmap().isNull():
                print(f'Rendered {tab.path.name}: {tab.image.pixmap().size()}')
                app.exit(0)
            else:
                QTimer.singleShot(50, verify)
        QTimer.singleShot(100, verify)
        QTimer.singleShot(12000, lambda: fail('Timed out waiting for the preview.'))
        result = app.exec()
        window.document_pool.waitForDone()
        return result


if __name__ == '__main__':
    raise SystemExit(main())
