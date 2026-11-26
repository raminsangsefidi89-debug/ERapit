"""Sheet composition shared by preview, PDF export, and physical printing."""
from dataclasses import dataclass
from math import ceil
import re
import pymupdf as fitz

PAPER = {'A3': (842, 1191), 'A4': (595, 842), 'A5': (420, 595), 'Letter': (612, 792), 'Legal': (612, 1008), 'Custom': (595, 842)}
GRID = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 9: (3, 3), 16: (4, 4)}


def page_range(spec: str, total: int, parity: str = 'all') -> list[int]:
    spec = spec.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩،', '01234567890123456789,'))
    pages = []
    for token in (spec.strip() or f'1-{total}').split(','):
        match = re.fullmatch(r'\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?', token)
        if not match:
            raise ValueError(f'Invalid page selection: {token}')
        a, b = int(match[1]), int(match[2] or match[1])
        if not 1 <= a <= b <= total:
            raise ValueError(f'Page range must be between 1 and {total}.')
        pages.extend(n - 1 for n in range(a, b + 1) if parity == 'all' or n % 2 == (parity == 'odd'))
    if not pages:
        raise ValueError('No pages selected.')
    return list(dict.fromkeys(pages))


def booklet(pages: list[int]) -> list[int | None]:
    padded = [*pages, *([None] * (-len(pages) % 4))]
    result = []
    for i in range(0, len(padded) // 2, 2):
        result.extend((padded[-i-1], padded[i], padded[i+1], padded[-i-2]))
    return result


@dataclass(frozen=True)
class Layout:
    selection: str = ''
    parity: str = 'all'
    paper: str = 'A4'
    landscape: bool = False
    width_mm: int = 210
    height_mm: int = 297
    overlap: int = 18
    nup: int = 1
    mode: str = 'normal'
    margin: float = 18
    gutter: float = 0
    scale: str = 'fit'
    percent: int = 100
    rtl: bool = False
    grayscale: bool = False
    numbers: bool = False
    watermark: str = ''


def compose(data: bytes, options: Layout, progress=lambda _: None) -> bytes:
    w, h = PAPER[options.paper]
    if options.paper == 'Custom':
        w, h = options.width_mm * 72 / 25.4, options.height_mm * 72 / 25.4
    if options.landscape or options.mode == 'booklet':
        w, h = h, w
    cols, rows = (2, 1) if options.mode == 'booklet' else GRID[options.nup]
    m = options.margin
    cw, ch = (w - 2*m - options.gutter) / cols, (h - 2*m) / rows
    if min(cw, ch) <= 0:
        raise ValueError('Margins exceed paper size.')
    with fitz.open(stream=data, filetype='pdf') as src, fitz.open() as out:
        selected = page_range(options.selection, len(src), options.parity)
        if options.mode == 'poster':
            return poster(src, out, selected, w, h, options, progress)
        src.bake()
        pages = booklet(selected) if options.mode == 'booklet' else selected
        for i, n in enumerate(pages):
            progress(int(i * 100 / len(pages)))
            slot = i % (cols * rows)
            if slot == 0:
                sheet = out.new_page(width=w, height=h)
            if n is None or not src[n].get_contents():
                continue
            col = slot % cols
            if options.rtl:
                col = cols - 1 - col
            x, y = m + options.gutter + col*cw, m + slot//cols*ch
            rect = src[n].rect
            fit = min(cw / rect.width, ch / rect.height)
            ratio = {'fit': fit, 'shrink': min(fit, 1), 'actual': 1, 'percent': options.percent / 100}[options.scale]
            # Clip oversized content to its slot instead of bleeding into neighbors.
            clip = fitz.Rect((rect.width-min(rect.width,cw/ratio))/2, (rect.height-min(rect.height,ch/ratio))/2,
                             (rect.width+min(rect.width,cw/ratio))/2, (rect.height+min(rect.height,ch/ratio))/2)
            a, b = clip.width*ratio, clip.height*ratio
            dest = fitz.Rect(x+(cw-a)/2,y+(ch-b)/2,x+(cw+a)/2,y+(ch+b)/2)
            if options.grayscale:
                sheet.insert_image(dest, pixmap=src[n].get_pixmap(matrix=fitz.Matrix(2,2), clip=clip, colorspace=fitz.csGRAY))
            else:
                sheet.show_pdf_page(dest, src, n, clip=clip)
        for i, sheet in enumerate(out):
            if options.numbers:
                sheet.insert_text((w/2, h-7), str(i+1), fontsize=8)
            if options.watermark:
                from core.documents import shape, FONT
                sheet.insert_textbox(fitz.Rect(20,h/2,w-20,h/2+40), shape(options.watermark), fontname='vazir', fontfile=str(FONT), fontsize=22, align=1, fill_opacity=.28, color=(.35,.35,.35), overlay=True)
        return out.tobytes(garbage=3, deflate=True)


def poster(src, out, selected, w, h, options: Layout, progress) -> bytes:
    src.bake()
    m = max(12, options.margin)
    cw, ch = w-2*m, h-2*m
    step_x, step_y = cw-options.overlap, ch-options.overlap
    if min(step_x,step_y) <= 0:
        raise ValueError('Poster overlap exceeds printable area.')
    scale = options.percent / 100
    count = 0
    for i, n in enumerate(selected):
        page = src[n]
        pw, ph = page.rect.width * scale, page.rect.height * scale
        cols, rows = max(1,ceil((pw-options.overlap)/step_x)), max(1,ceil((ph-options.overlap)/step_y))
        count += cols * rows
        if count > 2000:
            raise ValueError('Poster would exceed 2,000 tiles.')
        for y in range(rows):
            for x in range(cols):
                progress(i*100//len(selected))
                left, top = x*step_x, y*step_y
                right, bottom = min(left+cw,pw), min(top+ch,ph)
                clip = fitz.Rect(left/scale,top/scale,right/scale,bottom/scale)
                tile = out.new_page(width=w,height=h)
                dest = fitz.Rect(m,m,m+right-left,m+bottom-top)
                if page.get_contents():
                    if options.grayscale:
                        tile.insert_image(dest,pixmap=page.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,colorspace=fitz.csGRAY))
                    else:
                        tile.show_pdf_page(dest,src,n,clip=clip)
                for xx in (dest.x0,dest.x1):
                    for yy in (dest.y0,dest.y1):
                        tile.draw_line((xx-5,yy),(xx+5,yy),width=.35)
                        tile.draw_line((xx,yy-5),(xx,yy+5),width=.35)
                tile.insert_text((m,h-3),f'{n+1}  |  {y+1}/{rows} x {x+1}/{cols}',fontsize=7)
    return out.tobytes(garbage=3,deflate=True)
