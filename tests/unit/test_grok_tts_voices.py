"""Grok TTS provider configuration fallback contracts."""

from utils.tts.providers import grok as grok_provider


def test_partial_config_defaults_drive_grok_fallback_aliases(monkeypatch):
    catalog = {"configured-female": "Female", "configured-male": "Male"}
    monkeypatch.setattr(grok_provider, "_CFG", {"voices": catalog})
    monkeypatch.setattr(grok_provider, "GROK_TTS_VOICE_GENDERS", catalog)
    monkeypatch.setattr(
        grok_provider, "GROK_TTS_DEFAULT_VOICE", "configured-female"
    )
    monkeypatch.setattr(
        grok_provider, "GROK_TTS_DEFAULT_MALE_VOICE", "configured-male"
    )

    provider = grok_provider._create_provider()

    assert provider.normalize("female") == ("configured-female", True)
    assert provider.normalize("male") == ("configured-male", True)


def test_explicit_grok_aliases_keep_priority(monkeypatch):
    catalog = {"configured": "Female", "explicit": "Female"}
    monkeypatch.setattr(
        grok_provider,
        "_CFG",
        {"voices": catalog, "aliases": {"female": "explicit"}},
    )
    monkeypatch.setattr(grok_provider, "GROK_TTS_VOICE_GENDERS", catalog)
    monkeypatch.setattr(grok_provider, "GROK_TTS_DEFAULT_VOICE", "configured")

    provider = grok_provider._create_provider()

    assert provider.normalize("female") == ("explicit", True)
