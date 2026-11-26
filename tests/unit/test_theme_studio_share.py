import asyncio
import base64
import io
import json
import zipfile

from PIL import Image

from theme_studio.i18n import reset_request_locale, set_request_locale
from theme_studio.service import ThemeStudioService


def test_share_card_and_bundle_preserve_browser_download_and_metadata(tmp_path):
    studio = ThemeStudioService(data_dir=tmp_path)
    token = set_request_locale("en")
    try:
        item = studio._save_library_item(
            name="Aurora", vars_map={"--nts-accent": "#369"}, dark_map={},
            note="Bright", tags=["custom"], materials={"enabled": False},
        )
        card = asyncio.run(studio.export_share_card(theme_id=item["id"]))
        bundle = asyncio.run(studio.export_to_file(kind="zip", theme_id=item["id"]))
    finally:
        reset_request_locale(token)
    assert card.ok, card.error
    assert card.value["filename"].endswith(".png")
    assert card.value["mime"] == "image/png"
    assert "path" not in card.value
    with Image.open(io.BytesIO(base64.b64decode(card.value["data"]))) as image:
        assert image.format == "PNG" and image.size == (1200, 630)
    assert bundle.ok, bundle.error
    data = base64.b64decode(bundle.value["data"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert set(manifest["files"]) == set(archive.namelist())
        assert manifest["has_card"] and not manifest["theme"]["materials"]
        assert manifest["theme"]["note"] == "Bright"
        assert manifest["theme"]["tags"] == ["custom"]
        assert b"Aurora" in archive.read("README.txt")
        assert json.loads(archive.read("theme.json"))["materials"] == {"enabled": False}
    theme, fmt = studio._parse_archive(data, label="Aurora")
    assert fmt == "zip" and theme["note"] == "Bright"


def test_share_bundle_can_omit_unavailable_preview(tmp_path, monkeypatch):
    studio = ThemeStudioService(data_dir=tmp_path)

    def unavailable(*_):
        raise OSError("renderer unavailable")

    monkeypatch.setattr(studio, "_share_card_png", unavailable)
    _, payload = studio._build_archive(name="Plain", vars_map={}, dark_map={})
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert not manifest["has_card"]
        assert "card.png" not in archive.namelist()
        assert set(manifest["files"]) == set(archive.namelist())
