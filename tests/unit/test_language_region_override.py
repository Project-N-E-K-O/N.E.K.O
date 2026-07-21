import pytest

from utils import language_utils


@pytest.fixture(autouse=True)
def reset_language_cache():
    language_utils.reset_global_language()
    yield
    language_utils.reset_global_language()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_region_env_truthy_forces_china(monkeypatch, value):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", value)
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: ("English_United States", "1252"))

    assert language_utils._is_china_region() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_region_env_falsy_forces_non_china(monkeypatch, value):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", value)
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: ("Chinese (Simplified)_China", "936"))

    assert language_utils._is_china_region() is False


def test_invalid_region_env_falls_back_to_locale(monkeypatch):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", "unexpected")
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: ("Chinese (Simplified)_China", "936"))

    assert language_utils._is_china_region() is True


def test_region_env_populates_public_region_cache(monkeypatch):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", "false")
    assert language_utils.get_global_region() == "non-china"
    assert language_utils.is_china_region() is False
