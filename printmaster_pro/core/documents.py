"""Conversion adapters. This module never overwrites an input file."""
from io import BytesIO
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import pymupdf as fitz
from docx import Document
from PIL import Image, ImageOps
import img2pdf
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT = Path(__file__).resolve().parents[1] / 'assets' / 'Vazirmatn-Regular.ttf'
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}
SUPPORTED = IMAGES | {'.pdf', '.txt', '.docx', '.md', '.html', '.htm'}


def shape(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def decode(data: bytes) -> str:
    for codec in ('utf-8-sig', 'cp1256'):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    raise ValueError('Unsupported text encoding.')


def text_pdf(text: str, size: int = 12, margin: int = 42, leading: int = 20, rtl: bool = True) -> bytes:
    stream = BytesIO()
    pdfmetrics.registerFont(TTFont('Vazirmatn', str(FONT)))
    canvas = Canvas(stream, pagesize=(595,842))
    y = 842-margin
    for line in text.split('\n'):
        # Wrap on measured glyph width, including text with no spaces.
        rest = line
        while True:
            end = len(rest)
            if pdfmetrics.stringWidth(shape(rest), 'Vazirmatn', size) > 595-2*margin:
                lo, hi = 1, len(rest)
                while lo < hi:
                    mid = (lo+hi+1)//2
                    if pdfmetrics.stringWidth(shape(rest[:mid]), 'Vazirmatn', size) <= 595-2*margin:
                        lo = mid
                    else:
                        hi = mid-1
                end = rest.rfind(' ', 0, lo+1) or lo
                if end <= 0:
                    end = lo
            if y < margin:
                canvas.showPage()
                y = 842-margin
            canvas.setFont('Vazirmatn', size)
            (canvas.drawRightString if rtl else canvas.drawString)(595-margin if rtl else margin, y, shape(rest[:end]))
            y -= leading
            rest = rest[end:].lstrip()
            if not rest:
                break
    canvas.save()
    return stream.getvalue()


def images_pdf(paths: list[Path]) -> bytes:
    blobs = []
    for path in paths:
        with Image.open(path) as image:
            for i in range(getattr(image,'n_frames',1)):
                image.seek(i)
                frame = ImageOps.exif_transpose(image).convert('RGBA')
                background = Image.new('RGB',frame.size,'white'); background.paste(frame, mask=frame.getchannel('A'))
                buffer = BytesIO(); background.save(buffer, 'PNG'); blobs.append(buffer.getvalue())
    return img2pdf.convert(*blobs)


def libreoffice() -> str | None:
    return shutil.which('soffice') or next((str(p) for root in ('ProgramFiles','ProgramFiles(x86)') if (p := Path(os.environ.get(root, '/missing')) / 'LibreOffice/program/soffice.exe').is_file()), None)


def to_pdf(path: Path, password: str = '') -> bytes:
    path = path.resolve()
    ext = path.suffix.lower()
    if ext == '.pdf':
        with fitz.open(path) as doc:
            if doc.needs_pass and not doc.authenticate(password):
                raise ValueError('Incorrect PDF password.')
            return doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
    if ext in IMAGES:
        return images_pdf([path])
    if ext == '.txt':
        return text_pdf(decode(path.read_bytes()))
    if ext in {'.md','.html','.htm'}:
        from PySide6.QtGui import QTextDocument
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QPdfWriter
        class OfflineDocument(QTextDocument):
            def loadResource(self, kind, name):
                return None
        doc = OfflineDocument()
        (doc.setMarkdown if ext == '.md' else doc.setHtml)(decode(path.read_bytes()))
        buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
        writer = QPdfWriter(buf); doc.print_(writer); del writer
        return bytes(buf.data())
    if ext == '.docx':
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / (path.stem + '.pdf')
            if exe := libreoffice():
                subprocess.run([exe, '-env:UserInstallation='+(Path(tmp)/'profile').as_uri(), '--headless', '--convert-to', 'pdf', '--outdir', tmp, str(path)], check=True, capture_output=True, timeout=120)
            elif os.name == 'nt':
                import pythoncom
                from docx2pdf import convert
                pythoncom.CoInitialize()
                try:
                    convert(str(path), str(target))
                except Exception as exc:
                    raise RuntimeError('Install Microsoft Word or LibreOffice to convert DOCX.') from exc
                finally:
                    pythoncom.CoUninitialize()
            if target.exists():
                return target.read_bytes()
        raise RuntimeError('Install LibreOffice or Microsoft Word to convert DOCX to PDF.')
    raise ValueError(f'Unsupported format: {ext}')


def extract_text(path: Path) -> str:
    if path.suffix.lower() == '.docx':
        return '\n'.join(p.text for p in Document(path).paragraphs)
    if path.suffix.lower() == '.pdf':
        with fitz.open(path) as doc:
            return '\n'.join(p.get_text() for p in doc)
    return decode(path.read_bytes())


def save_new(path: Path, data: bytes) -> None:
    """Exclusive creation protects originals, including aliases and links."""
    with path.open('xb') as output:
        try:
            output.write(data)
        except BaseException:
            output.close()
            path.unlink(missing_ok=True)
            raise
