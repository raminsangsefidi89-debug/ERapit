"""Independent PDF operations, returning new files as bytes."""
from io import BytesIO
from pathlib import Path
import pymupdf as fitz
import pikepdf
from pypdf import PdfReader, PdfWriter
from core.layout import page_range


def merge(paths: list[Path]) -> bytes:
    writer = PdfWriter()
    for path in paths:
        writer.append(str(path))
    result = BytesIO(); writer.write(result)
    return result.getvalue()


def select(data: bytes, spec: str, omit: bool = False) -> bytes:
    reader = PdfReader(BytesIO(data)); writer = PdfWriter()
    indexes = page_range(spec, len(reader.pages))
    if omit:
        indexes = [i for i in range(len(reader.pages)) if i not in indexes]
    if not indexes:
        raise ValueError('At least one page must remain.')
    for i in indexes:
        writer.add_page(reader.pages[i])
    result = BytesIO(); writer.write(result)
    return result.getvalue()


def split(data: bytes, size: int) -> list[bytes]:
    if size < 1:
        raise ValueError('Chunk size must be positive.')
    total = len(PdfReader(BytesIO(data)).pages)
    return [select(data, f'{i+1}-{min(i+size,total)}') for i in range(0,total,size)]


def optimize(data: bytes, level: int = 2, linear: bool = False, password: str = '', strip: bool = False) -> bytes:
    with pikepdf.open(BytesIO(data)) as pdf:
        if strip:
            if '/Metadata' in pdf.Root:
                del pdf.Root['/Metadata']
            del pdf.docinfo
        target = BytesIO()
        encryption = pikepdf.Encryption(owner=password, user=password, R=6) if password else False
        pdf.save(target, linearize=linear, recompress_flate=level >= 2,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate if level >= 3 else pikepdf.ObjectStreamMode.preserve,
                 encryption=encryption)
        return target.getvalue()


def modify(data: bytes, op: str, spec: str, value: str) -> bytes:
    with fitz.open(stream=data, filetype='pdf') as doc:
        if op == 'blank':
            doc.new_page()
        elif op == 'flatten':
            doc.bake()
        elif op == 'metadata':
            doc.set_metadata({'title': value})
        else:
            for i in page_range(spec, len(doc)):
                page = doc[i]
                if op == 'rotate':
                    angle = int(value)
                    if angle % 90:
                        raise ValueError('Rotation must be a multiple of 90.')
                    page.set_rotation((page.rotation + angle) % 360)
                elif op == 'crop':
                    left, top, right, bottom = map(float, value.split(','))
                    if min(left,top,right,bottom) < 0:
                        raise ValueError('Crop margins must be non-negative.')
                    box = page.cropbox
                    clipped = fitz.Rect(box.x0+left,box.y0+top,box.x1-right,box.y1-bottom)
                    if clipped.is_empty:
                        raise ValueError('Crop exceeds the page bounds.')
                    page.set_cropbox(clipped)
                elif op == 'redact':
                    if not value.strip():
                        raise ValueError('Enter text to redact.')
                    for rect in page.search_for(value):
                        page.add_redact_annot(rect, fill=(0,0,0))
                    page.apply_redactions()
                elif op == 'highlight':
                    if not value.strip():
                        raise ValueError('Enter text to highlight.')
                    for quad in page.search_for(value, quads=True):
                        page.add_highlight_annot(quad)
        return doc.tobytes(garbage=4, deflate=True)


def ocr(data: bytes, lang: str, progress) -> bytes:
    import pytesseract
    from PIL import Image
    with fitz.open(stream=data, filetype='pdf') as source, fitz.open() as output:
        for i, page in enumerate(source):
            progress(i * 100 // len(source))
            if page.get_text().strip():
                output.insert_pdf(source, from_page=i, to_page=i)
                continue
            pix = page.get_pixmap(dpi=250, alpha=False)
            image = Image.frombytes('RGB', (pix.width,pix.height),pix.samples)
            image.info['dpi'] = (250,250)
            with fitz.open(stream=pytesseract.image_to_pdf_or_hocr(image, lang=lang, extension='pdf', timeout=120), filetype='pdf') as recognized:
                output.insert_pdf(recognized)
        return output.tobytes(garbage=3, deflate=True)


def compare(first: bytes, second: bytes, progress) -> str:
    """Report changed page positions at 96 DPI, including graphic-only differences."""
    from hashlib import sha256
    with fitz.open(stream=first,filetype='pdf') as a, fitz.open(stream=second,filetype='pdf') as b:
        changed = []
        for i in range(min(len(a),len(b))):
            progress(i*100//max(len(a),len(b)))
            left,right = a[i],b[i]
            if left.rect != right.rect or sha256(left.get_pixmap(dpi=96).samples_mv).digest() != sha256(right.get_pixmap(dpi=96).samples_mv).digest():
                changed.append(str(i+1))
        return f'Pages: {len(a)} → {len(b)}\nChanged page positions: {", ".join(changed) or "—"}'
