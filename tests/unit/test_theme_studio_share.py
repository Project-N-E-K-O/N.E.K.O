"""Theme share card: PNG rendering and its browser-download entry.

``export_share_card`` must hand the PNG back to the panel (base64 + mime +
filename, like ``export_to_file``) without writing export files, and the
renderer must survive machines without any of the candidate fonts and theme
names in any script.
"""

import base64
import io

import pytest
from PIL import Image

import theme_studio
from theme_studio import share_card
from theme_studio import service as ts_service
from theme_studio.i18n import reset_request_locale, set_request_locale


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_service_started", False)
    monkeypatch.setattr(theme_studio, "_app_exiting", False)
    monkeypatch.setattr(theme_studio, "_data_dir_override", tmp_path / "data")
    service = theme_studio.startup_service()
    yield service
    theme_studio.shutdown_service()


@pytest.fixture
def no_system_fonts(monkeypatch):
    """Point the font search at files that do not exist."""
    monkeypatch.setattr(
        share_card,
        "_FONT_CANDIDATES",
        (("/nonexistent/fonts/missing.ttf", 0), (r"Z:\nowhere\missing.ttc", 0)),
    )
    share_card.reset_font_cache()
    yield
    # Runs before monkeypatch restores the list, so the next lookup re-resolves it.
    share_card.reset_font_cache()


def _png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _assert_download(value: dict) -> Image.Image:
    payload = base64.b64decode(value["data"])
    assert len(payload) == value["bytes"]
    assert value["mime"] == "image/png"
    assert value["filename"].endswith(".png")
    assert "path" not in value
    image = _png(payload)
    assert image.format == "PNG"
    assert image.size == (share_card.CARD_W, share_card.CARD_H)
    assert (value["width"], value["height"]) == image.size
    return image


@pytest.mark.unit
def test_share_card_for_builtin_theme_is_a_browser_download(studio):
    result = theme_studio.run_entry_sync("export_share_card", {"theme_id": "amber-dusk"})
    assert result.ok, result.error
    _assert_download(result.value)
    assert result.value["kind"] == "png"
    assert not (studio.data_dir / "exports").exists()


@pytest.mark.unit
def test_share_card_for_current_theme_uses_its_name(studio):
    seeded = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk"})
    assert seeded.ok, seeded.error
    renamed = theme_studio.run_entry_sync("apply_theme", {"name": "樱花雾"})
    assert renamed.ok, renamed.error

    result = theme_studio.run_entry_sync("export_share_card", {})
    assert result.ok, result.error
    _assert_download(result.value)
    assert result.value["theme"] == "樱花雾"
    assert result.value["filename"] == "樱花雾.png"


@pytest.mark.unit
def test_share_card_rejects_non_string_name(studio):
    result = theme_studio.run_entry_sync("export_share_card", {"name": 123})
    assert not result.ok


@pytest.mark.unit
def test_share_card_unknown_theme_is_an_error(studio):
    result = theme_studio.run_entry_sync("export_share_card", {"theme_id": "no-such-theme"})
    assert not result.ok


@pytest.mark.unit
def test_share_card_renders_without_any_system_font(no_system_fonts):
    assert share_card._available_fonts() == ()
    for name in ("Aurora", "樱花雾 · 테마 · Тема ✨"):
        image = _png(
            share_card.render_share_card(
                name,
                {"--nts-accent": "#369", "--nts-chat-bg": "linear-gradient(#fff, rgba(0, 0, 0, .5))"},
                {},
                preset_label="Liquid",
                quality_label="High",
            )
        )
        assert image.size == (share_card.CARD_W, share_card.CARD_H)


@pytest.mark.unit
@pytest.mark.parametrize("locale", ["zh-CN", "ja", "ko", "ru", "en"])
def test_share_card_non_latin_text_does_not_crash(locale):
    token = set_request_locale(locale)
    try:
        payload = share_card.render_share_card(
            "テーマ 테마 Тема 主題 ✨ \U0001f431",
            {},
            {"--nts-accent": "rgb(300, -4, 20)", "--nts-ai-bubble-text": "#eeeeeeaa"},
        )
    finally:
        reset_request_locale(token)
    assert _png(payload).size == (share_card.CARD_W, share_card.CARD_H)


@pytest.mark.unit
def test_download_filename_keeps_png_extension():
    name = ts_service.ThemeStudioService._download_filename("樱花雾" * 20 + ".png")
    assert name.endswith(".png")
    assert len(name) <= 40 + len(".png")
