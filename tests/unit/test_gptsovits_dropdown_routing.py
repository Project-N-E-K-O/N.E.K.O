"""Dispatch-selection regression tests for GPT-SoVITS after the enabled signal
collapsed onto a single source of truth.

The enabled signal is now ttsModelProvider=='gptsovits'. The config_manager
snapshot derives GPTSOVITS_ENABLED from the dropdown (plus the pre-#1830 legacy
gptsovitsEnabled switch as a backfill), and dispatch's ``_gptsovits_is_selected``
reads only GPTSOVITS_ENABLED plus the tts_custom slot's is_custom — it no longer
raw-loads core_config.json to read ttsModelProvider itself.

The snapshot derivation itself (legacy-flag-only / dropdown-only / neither /
switch-away-does-not-stick) is covered by
tests/unit/test_api_config_manager.py::TestGptsovitsEnabledDerivation; this file
only checks the dispatch selection once GPTSOVITS_ENABLED is already derived.
"""

from main_logic import tts_client


class _FakeConfigManager:
    def __init__(self, core_config, *, is_custom=False):
        self._core_config = core_config
        self._is_custom = is_custom

    def get_core_config(self):
        return self._core_config

    def load_json_config(self, name, default=None):
        # gptsovits 选中已不再 raw load core_config.json；保留此方法只为别的
        # provider（vllm_omni 等）共用 ctx 时不抛属性错。
        return default if default is not None else {}

    def get_model_api_config(self, model_type):
        return {"is_custom": self._is_custom, "base_url": "", "model": "", "api_key": ""}

    def get_voices_for_current_api(self, for_listing=False):
        return {}


def _base_core_config(**overrides):
    cfg = {
        "CORE_API_TYPE": "qwen",
        "DISABLE_TTS": False,
        "ENABLE_CUSTOM_API": False,
        "GPTSOVITS_ENABLED": False,
    }
    cfg.update(overrides)
    return cfg


def test_enabled_snapshot_selects_gptsovits(monkeypatch):
    """With GPTSOVITS_ENABLED=True (derived from the dropdown or the legacy flag)
    and tts_custom.is_custom, dispatch selects GPT-SoVITS."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(GPTSOVITS_ENABLED=True),
        is_custom=True,
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key_override, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert worker is tts_client.gptsovits_tts_worker
    assert api_key_override is None
    assert provider_key == "gptsovits"


def test_enabled_but_not_custom_does_not_select_gptsovits(monkeypatch):
    """GPTSOVITS_ENABLED=True but the tts_custom slot is unconfigured
    (is_custom=False, e.g. dropdown picked gptsovits but no URL was filled): do
    not select — fall through gracefully instead of grabbing a worker that would
    immediately report an invalid URL."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(GPTSOVITS_ENABLED=True),
        is_custom=False,
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert provider_key != "gptsovits"


def test_disabled_snapshot_does_not_select_gptsovits(monkeypatch):
    """With GPTSOVITS_ENABLED=False (dropdown picked another provider, or neither
    dropdown nor legacy flag is set), GPT-SoVITS must not be selected even when
    tts_custom.is_custom=True."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(GPTSOVITS_ENABLED=False),
        is_custom=True,
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert provider_key != "gptsovits"


def test_string_falsey_enabled_not_misread_as_truthy(monkeypatch):
    """A raw, un-normalized string "false" must not be treated as truthy
    (defensive _as_bool alignment)."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(GPTSOVITS_ENABLED="false"),
        is_custom=True,
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert provider_key != "gptsovits"
