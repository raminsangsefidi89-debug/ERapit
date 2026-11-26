from dataclasses import dataclass
from pathlib import Path
import os
import pymupdf as fitz
from PySide6.QtCore import QRectF, QMarginsF
from PySide6.QtGui import QImage, QPainter, QPageSize, QPageLayout
from PySide6.QtCore import QSizeF
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo


@dataclass(frozen=True)
class PrintSettings:
    name: str
    copies: int = 1
    collate: bool = True
    dpi: int = 300
    duplex: str = 'none'
    pass_number: int = 0


def printer_status(name: str) -> str:
    if os.name != 'nt' or not name:
        return ''
    import win32print
    handle = win32print.OpenPrinter(name)
    try:
        info = win32print.GetPrinter(handle, 2)
        status = info['Status']
        labels = [label for flag,label in [(1,'Paused'),(2,'Error'),(8,'Paper jam'),(16,'Paper out'),(128,'Offline'),(1024,'Printing'),(4096,'Unavailable'),(256,'I/O active')] if status & flag]
        return ', '.join(labels) or 'No reported error'
    finally:
        win32print.ClosePrinter(handle)


def send(data: bytes, settings: PrintSettings, title: str, progress) -> None:
    if not settings.name or settings.name not in QPrinterInfo.availablePrinterNames():
        raise ValueError('Select an installed printer.')
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(settings.name)
    printer.setDocName(Path(title).name)
    printer.setResolution(settings.dpi)
    printer.setDuplex({'none': QPrinter.DuplexMode.DuplexNone,'long': QPrinter.DuplexMode.DuplexLongSide,'short': QPrinter.DuplexMode.DuplexShortSide}[settings.duplex])
    with fitz.open(stream=data,filetype='pdf') as doc:
        page = doc[0].rect
        size = QPageSize(QSizeF(page.width,page.height), QPageSize.Unit.Point, '', QPageSize.SizeMatchPolicy.ExactMatch)
        printer.setPageLayout(QPageLayout(size,QPageLayout.Orientation.Portrait,QMarginsF(0,0,0,0)))
        printer.setFullPage(True)
        order = list(range(len(doc)))
        if settings.pass_number == 1:
            order = order[::2]
        elif settings.pass_number == 2:
            order = order[1::2][::-1]
        order = order * settings.copies if settings.collate else [i for i in order for _ in range(settings.copies)]
        if not order:
            raise ValueError('No sides in this print pass.')
        progress(0)
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError('Could not start the printer.')
        try:
            for index, p in enumerate(order):
                progress(index*100//len(order))
                if index and not printer.newPage():
                    raise RuntimeError('Printer refused a new page.')
                pix = doc[p].get_pixmap(dpi=min(settings.dpi,600),alpha=False)
                image = QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format.Format_RGB888)
                painter.drawImage(QRectF(0,0,printer.width(),printer.height()),image)
        except BaseException:
            printer.abort()
            raise
        finally:
            painter.end()
