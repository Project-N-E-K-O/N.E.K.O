import pytest

from utils import language_utils


@pytest.fixture(autouse=True)
def reset_language_cache():
    language_utils.reset_global_language()
    yield
    language_utils.reset_global_language()


@pytest.mark.parametrize(
    ("value", "expected_short", "expected_full"),
    [
        ("en", "en", "en"),
        ("en-US", "en", "en"),
        ("english", "en", "en"),
        ("ja-JP", "ja", "ja"),
    ],
)
def test_language_env_overrides_steam_and_system(
    monkeypatch, value, expected_short, expected_full
):
    monkeypatch.setenv("NEKO_LANGUAGE", value)
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: "zh")
    monkeypatch.setattr(language_utils, "_get_system_language", lambda: "zh")

    assert language_utils.initialize_global_language() == expected_short
    assert language_utils.get_global_language_full() == expected_full


def test_language_env_survives_late_steam_refresh(monkeypatch):
    monkeypatch.setenv("NEKO_LANGUAGE", "en")

    assert language_utils.initialize_global_language() == "en"
    assert language_utils.refresh_global_language("schinese") is False
    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_language_full() == "en"


def test_language_and_region_overrides_are_independent(monkeypatch):
    monkeypatch.setenv("NEKO_LANGUAGE", "en")
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", "false")

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_region() == "non-china"


def test_invalid_language_env_falls_back_to_detected_language(monkeypatch):
    monkeypatch.setenv("NEKO_LANGUAGE", "not-a-language")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_get_system_language", lambda: "zh")

    assert language_utils.initialize_global_language() == "zh"
