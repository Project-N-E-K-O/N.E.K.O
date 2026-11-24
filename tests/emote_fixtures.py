"""Small original Spine assets shared by import and browser regression tests."""
import io
import json
import zipfile
from PIL import Image


def spine_zip(model="character", animations=2, *, invalid_image=False):
    png = io.BytesIO()
    Image.new("RGBA", (4, 4), (80, 150, 220, 255)).save(png, format="PNG")
    data = {
        "skeleton": {"spine": "4.3.13"}, "bones": [{"name": "root"}],
        "slots": [{"name": "body", "bone": "root", "attachment": "body"}],
        "skins": [{"name": "default", "attachments": {"body": {"body": {"width": 4, "height": 4}}}}],
        "animations": {f"idle{i}": {"bones": {"root": {"rotate": [{"value": 0}, {"time": 1, "value": 10 + i}]}}}
                       for i in range(animations)},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"assets/{model}.json", json.dumps(data))
        archive.writestr(f"assets/{model}.atlas", "page.png\nsize: 4,4\nfilter: Linear,Linear\npma: false\nbody\nbounds: 0,0,4,4\n")
        archive.writestr("assets/page.png", b"bad" if invalid_image else png.getvalue())
    return stream.getvalue()
