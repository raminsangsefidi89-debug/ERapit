from threading import Event
import logging
from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class Cancelled(Exception):
    pass


class Events(QObject):
    progress = Signal(int)
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.events = Events()
        self.cancelled = Event()

    def progress(self, value: int) -> None:
        if self.cancelled.is_set():
            raise Cancelled()
        self.events.progress.emit(value)

    @Slot()
    def run(self):
        try:
            self.progress(0)
            result = self.action(self.progress)
            self.progress(100)
            self.events.done.emit(result)
        except Cancelled:
            pass
        except Exception as exc:
            logging.error('Job failed: %s', type(exc).__name__)
            self.events.failed.emit(str(exc))
        finally:
            self.events.finished.emit()
