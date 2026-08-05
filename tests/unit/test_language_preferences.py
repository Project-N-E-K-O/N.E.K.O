"""Contracts for independent UI languages and per-character language preferences."""

import asyncio
import json
from pathlib import Path

import pytest

from main_routers.characters_router import language_preference as preference_router
from main_logic import cross_server
from main_logic.core.lifecycle import LifecycleMixin


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}


@pytest.mark.unit
def test_language_preference_copy_exists_in_all_supported_locales():
    locale_dir = PROJECT_ROOT / "static" / "locales"
    locale_files = {path.stem: path for path in locale_dir.glob("*.json")}
    assert set(locale_files) == SUPPORTED_LOCALES

    required_keys = {
        "languagePreference",
        "languagePreferenceDescription",
        "languagePreferenceSaved",
        "languagePreferenceSaveFailed",
        "languagePreferenceLoadFailed",
    }
    expected_labels = {
        "en": "Language Preference",
        "es": "Preferencia de idioma",
        "ja": "言語の好み",
        "ko": "언어 선호",
        "pt": "Preferência de idioma",
        "ru": "Языковые предпочтения",
        "zh-CN": "语言偏好",
        "zh-TW": "語言偏好",
    }
    for locale, path in locale_files.items():
        character = json.loads(path.read_text(encoding="utf-8"))["character"]
        assert required_keys <= set(character), locale
        assert character["languagePreference"] == expected_labels[locale]
        assert len(character["languagePreferenceDescription"].strip()) >= 20, locale


@pytest.mark.unit
def test_language_options_use_native_names_instead_of_translated_labels():
    source = (
        PROJECT_ROOT / "static" / "i18n-i18next.js"
    ).read_text(encoding="utf-8")
    expected = {
        "简体中文",
        "繁體中文",
        "English",
        "日本語",
        "한국어",
        "Русский",
        "Español",
        "Português",
    }
    for label in expected:
        assert f"label: '{label}'" in source


@pytest.mark.unit
def test_character_language_control_is_grouped_with_runtime_preferences():
    source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")

    personality_mount = source.index("form.appendChild(personalityWrapper);")
    language_mount = source.index("form.appendChild(languagePreferenceWrapper);")
    voice_mount = source.index("form.appendChild(voiceWrapper);")
    assert personality_mount < language_mount < voice_mount


@pytest.mark.unit
def test_character_language_control_reuses_voice_dropdown_and_hot_refreshes():
    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    subscriptions_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "subscriptions-and-scan.js"
    ).read_text(encoding="utf-8")

    assert "_panelCreateVoiceSelectUi(languageSelect)" in form_source
    assert "language-preference-custom-select" in form_source
    assert "window.addEventListener('localechange', form._localeChangeHandler)" in form_source
    assert "_loadPanelVoices(voiceSelect, selectedVoiceId)" in form_source
    assert "renderCharaCardsView();" in subscriptions_source


@pytest.mark.unit
def test_language_preference_has_accessible_explanatory_tooltip():
    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    css_source = (
        PROJECT_ROOT / "static" / "css" / "character_card_manager.css"
    ).read_text(encoding="utf-8")

    assert "language-preference-help-button" in form_source
    assert "language-preference-tooltip" in form_source
    assert "languageHelpButton.setAttribute('aria-describedby', languageTooltip.id)" in form_source
    assert "languageTooltip.setAttribute('role', 'tooltip')" in form_source
    assert ".language-preference-help-button:hover + .language-preference-tooltip" in css_source
    assert ".language-preference-help-button:focus-visible + .language-preference-tooltip" in css_source
    assert "bottom: calc(100% + 9px);" in css_source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_language_change_clears_only_recent_context_and_resets_session(monkeypatch):
    calls = []
    config_manager = object()

    async def load_character(name):
        calls.append(("load", name))
        return config_manager, {"当前猫娘": name, "猫娘": {name: {}}}

    async def persist_locale(method, name, *, language=None):
        calls.append(("persist", method, name, language))
        return {
            "success": True,
            "language": language,
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(manager, name):
        calls.append(("clear_recent", manager, name))

    class SessionManager:
        is_active = True
        session = object()

        def set_user_language(self, language):
            calls.append(("set_live_language", language))

        async def end_session(self, **kwargs):
            calls.append(("end_session", kwargs))
            await kwargs["after_memory_settlement"]()

        def reset_session_start_circuit(self):
            calls.append(("reset_circuit",))

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", persist_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(
        preference_router,
        "get_session_manager",
        lambda: {"Mimi": SessionManager()},
    )

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is True
    assert result["language"] == "ja"
    assert result["recent_history_cleared"] is True
    assert result["session_reset"] is True
    assert ("clear_recent", config_manager, "Mimi") in calls
    assert not any("durable" in str(call).lower() for call in calls)
    assert calls.index(("set_live_language", "ja")) < next(
        index for index, call in enumerate(calls) if call[0] == "end_session"
    )
    assert next(
        index for index, call in enumerate(calls) if call[0] == "end_session"
    ) < calls.index(("clear_recent", config_manager, "Mimi"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_end_memory_barrier_clears_after_unsynced_tail(monkeypatch):
    events = []
    queue = asyncio.Queue()
    completion = asyncio.get_running_loop().create_future()

    async def post_memory(endpoint, name, payload, *, timeout_s, language=None):
        events.append(("settle", endpoint, name, payload, language))
        return True, "", {}

    async def skip_analyzer(*_args, **_kwargs):
        return False

    async def clear_recent():
        events.append(("clear_recent",))

    monkeypatch.setattr(cross_server, "_post_memory_server", post_memory)
    monkeypatch.setattr(
        cross_server,
        "_publish_analyze_request_with_fallback",
        skip_analyzer,
    )

    connector = asyncio.create_task(
        cross_server.run_sync_connector(
            queue,
            "Mimi",
            config={"monitor": False, "bullet": False},
            user_language_provider=lambda: "ja",
        )
    )
    queue.put_nowait({
        "type": "user",
        "data": {"data": "hello", "input_type": "transcript"},
    })
    queue.put_nowait({
        "type": "system",
        "data": "session end",
        "_after_memory_settlement": clear_recent,
        "_memory_settlement_done": completion,
    })

    try:
        await asyncio.wait_for(completion, timeout=1.0)
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)

    assert events[0][0:3] == ("settle", "cache", "Mimi")
    assert events[0][3][0]["role"] == "user"
    assert events[0][4] == "ja"
    assert events[-1] == ("clear_recent",)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_barrier_timeout_keeps_late_cleanup_armed():
    calls = []
    completion = asyncio.get_running_loop().create_future()

    async def clear_recent():
        calls.append("clear")

    manager = type("Manager", (), {"lanlan_name": "Mimi"})()
    await LifecycleMixin._wait_for_session_end_memory_barrier(
        manager,
        completion,
        clear_recent,
        timeout_seconds=0.1,
    )
    assert calls == ["clear"]

    await cross_server._complete_session_end_memory_barrier(
        {
            "_after_memory_settlement": clear_recent,
            "_memory_settlement_done": completion,
        },
        "Mimi",
    )
    assert calls == ["clear", "clear"]
