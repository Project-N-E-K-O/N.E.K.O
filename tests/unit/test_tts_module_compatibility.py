"""Compatibility contracts for the TTS package migration."""

import importlib
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "canonical_name"),
    [
        ("utils.native_voice_registry", "utils.tts.native_voice_registry"),
        ("utils.tts_provider_registry", "utils.tts.provider_registry"),
        ("utils.gemini_tts_voices", "utils.tts.providers.gemini"),
        ("utils.grok_tts_voices", "utils.tts.providers.grok"),
        ("utils.stepfun_tts_voices", "utils.tts.providers.stepfun"),
        ("utils.mimo_tts_voices", "utils.tts.providers.mimo"),
        ("utils.elevenlabs_tts_voices", "utils.tts.providers.elevenlabs"),
    ],
)
def test_legacy_module_is_canonical_module(legacy_name: str, canonical_name: str) -> None:
    assert importlib.import_module(legacy_name) is importlib.import_module(canonical_name)


def test_fresh_process_native_registry_bootstraps_builtin_providers() -> None:
    script = textwrap.dedent(
        """
        import importlib

        legacy = importlib.import_module("utils.native_voice_registry")
        canonical = importlib.import_module("utils.tts.native_voice_registry")

        assert legacy is canonical
        assert legacy._PROVIDERS is canonical._PROVIDERS
        assert set(canonical.list_providers()) == {
            "gemini", "step", "free", "free_intl", "grok"
        }
        """
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_legacy_monkeypatch_seam_updates_canonical_module(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = importlib.import_module("utils.gemini_tts_voices")
    canonical = importlib.import_module("utils.tts.providers.gemini")
    sentinel = object()

    monkeypatch.setattr(legacy, "GEMINI_TTS_MODEL", sentinel)

    assert canonical.GEMINI_TTS_MODEL is sentinel


def test_stepfun_legacy_public_voice_labels_remain_observable() -> None:
    legacy = importlib.import_module("utils.stepfun_tts_voices")
    canonical = importlib.import_module("utils.tts.providers.stepfun")

    assert legacy.STEPFUN_TTS_VOICE_LABELS is canonical.STEPFUN_TTS_VOICE_LABELS
    assert legacy.STEPFUN_TTS_VOICE_LABELS == {
        "elegantgentle-female": "高雅女声",
        "livelybreezy-female": "活力女声",
        "qingchunshaonv": "青春少女",
        "wenrounansheng": "温柔男声",
    }
