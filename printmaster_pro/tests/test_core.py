from pathlib import Path
import pymupdf as fitz
import pytest
from PIL import Image
from core.documents import decode, save_new, text_pdf, to_pdf
from core.layout import Layout, booklet, compose, page_range
from core.pdf import merge, select, split, modify, optimize


def fixture_pdf(count=5):
    with fitz.open() as doc:
        for i in range(count):
            doc.new_page().insert_text((70,70),f'PAGE {i+1}')
        return doc.tobytes()


def pages(data):
    with fitz.open(stream=data,filetype='pdf') as doc:
        return [page.get_text().strip() for page in doc]


def test_ranges():
    assert page_range(' ۳،۱-۴,5 ',5,'odd') == [2,0,4]
    assert page_range('',4,'even') == [1,3]
    for invalid in ('0','4-2','1,','6','1.5'):
        with pytest.raises(ValueError): page_range(invalid,5)


def test_booklet():
    assert booklet([0,1,2,3,4]) == [None,0,1,None,None,2,3,4]
    assert len(pages(compose(fixture_pdf(), Layout(mode='booklet')))) == 4


def test_nup_and_poster():
    data = fixture_pdf()
    assert len(pages(compose(data, Layout(nup=4)))) == 2
    assert len(pages(compose(data, Layout(selection='1',mode='poster',percent=200)))) > 1


def test_split_merge_and_delete(tmp_path):
    data = fixture_pdf()
    paths = []
    for i,part in enumerate(split(data,2)):
        path = tmp_path/f'برگه {i}.pdf'; save_new(path,part); paths.append(path)
    assert [len(pages(part.read_bytes())) for part in paths] == [2,2,1]
    assert pages(merge(paths)) == pages(data)
    assert pages(select(data,'5,1')) == ['PAGE 5','PAGE 1']
    assert pages(select(data,'2-4',True)) == ['PAGE 1','PAGE 5']
    with pytest.raises(FileExistsError): save_new(paths[0],b'new')


def test_conversions(tmp_path):
    assert decode('فارسي'.encode('cp1256')) == 'فارسي'
    txt = tmp_path/'فارسی.txt'; txt.write_text('سلام\nPrintMaster Pro',encoding='utf-8')
    assert len(pages(to_pdf(txt))) == 1
    assert 'PrintMaster Pro' in pages(to_pdf(txt))[0]
    assert len(pages(text_pdf('A'*10000))) > 1
    img = tmp_path/'عکس.png'; Image.new('RGBA',(32,20),'red').save(img)
    assert len(pages(to_pdf(img))) == 1


def test_encryption_and_redaction(tmp_path):
    original = fixture_pdf(1)
    assert pages(optimize(original,strip=True)) == ['PAGE 1']
    encrypted = tmp_path/'private.pdf'; encrypted.write_bytes(optimize(original,password='test-pass'))
    with pytest.raises(ValueError): to_pdf(encrypted)
    assert pages(to_pdf(encrypted,'test-pass')) == ['PAGE 1']
    assert not pages(modify(original,'redact','1','PAGE 1'))[0]
