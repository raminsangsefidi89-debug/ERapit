from io import BytesIO
import base64
import pytest
from PIL import Image
from services import images


def test_image_boundaries(monkeypatch):
    def key(*args): raise AssertionError('No credentials should be looked up')
    monkeypatch.setattr(images.keyring, 'get_password', key)
    for endpoint,count in [('https://example.test/v1',11),('https://example.test/v1',0),('http://example.test/v1',1),('https://example.test/?redirect=',1)]:
        with pytest.raises(ValueError): images.generate(endpoint,'model','prompt','1024x1024',count,lambda p:None)


def test_image_api_returns_png(monkeypatch):
    buf = BytesIO(); Image.new('RGB',(12,10),'blue').save(buf,'PNG')
    encoded = base64.b64encode(buf.getvalue()).decode()
    monkeypatch.setattr(images.keyring,'get_password',lambda *args:'test-key')
    class Reply:
        status_code = 200
        def json(self): return {'data':[{'b64_json':encoded}]}
    def post(url, **kwargs):
        assert url == 'https://example.test/v1/images/generations'
        assert kwargs['json']['n'] == 1
        assert not kwargs['allow_redirects']
        return Reply()
    monkeypatch.setattr(images.requests,'post',post)
    result = images.generate('https://example.test/v1','model','prompt','1024x1024',1,lambda p:None)
    assert result[0].startswith(b'\x89PNG')
