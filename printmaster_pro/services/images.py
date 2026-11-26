from pathlib import Path
from urllib.parse import urlsplit
import base64
import binascii
from io import BytesIO
import keyring
import requests
from PIL import Image

SERVICE = 'PrintMasterPro.ImageAPI'


def generate(endpoint: str, model: str, prompt: str, size: str, count: int, progress) -> list[bytes]:
    if not 1 <= count <= 10 or not prompt.strip() or not model.strip():
        raise ValueError('Enter a model and prompt; image count must be 1–10.')
    url = urlsplit(endpoint)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Configure an HTTPS API base URL without credentials, query, or fragment.')
    token = keyring.get_password(SERVICE, endpoint)
    if not token:
        raise ValueError('Save the API key for this endpoint in Settings.')
    progress(5)
    try:
        response = requests.post(endpoint.rstrip('/')+'/images/generations', json={'model':model,'prompt':prompt,'size':size,'n':count,'response_format':'b64_json'},headers={'Authorization':f'Bearer {token}'},timeout=(10,120),allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError('Image API unavailable. Check your internet connection and try again.') from None
    if response.status_code != 200:
        raise RuntimeError(f'Image API returned HTTP {response.status_code}. Check model, access, and size.')
    try:
        items = response.json()['data']
        if not isinstance(items,list) or len(items) != count:
            raise ValueError()
        output = []
        for i,item in enumerate(items):
            progress(10+i*85//count)
            raw = base64.b64decode(item['b64_json'],validate=True)
            with Image.open(BytesIO(raw)) as image:
                converted = BytesIO(); image.convert('RGB').save(converted,'PNG')
                output.append(converted.getvalue())
        return output
    except (KeyError,TypeError,ValueError,binascii.Error,OSError):
        raise RuntimeError('The API must return the requested number of valid base64 images.') from None
