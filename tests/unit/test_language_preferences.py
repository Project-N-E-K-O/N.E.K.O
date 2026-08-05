"""Contracts for independent UI languages and per-character language preferences."""

import asyncio
import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest

from main_routers.characters_router import language_preference as preference_router
from main_logic import cross_server
from main_logic.core.lifecycle import LifecycleMixin
from tests.node_harness import run_node_script


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
        assert re.search(rf'''label:\s*(["']){re.escape(label)}\1''', source)


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
    assert "const voicesLoadPromise = refreshVoiceCatalog" in form_source
    assert "void refreshVoiceCatalog(voiceSelect.value);" in form_source
    assert "form._voiceLocaleRefreshSequence !== refreshSequence" in form_source

    locale_handler = "function updateLocaleDependent()"
    locale_listener = "window.addEventListener('localechange', updateLocaleDependent);"
    locale_handler_start = subscriptions_source.find(locale_handler)
    locale_listener_start = subscriptions_source.find(locale_listener)
    assert 0 <= locale_handler_start < locale_listener_start
    assert (
        "renderCharaCardsView();"
        in subscriptions_source[locale_handler_start:locale_listener_start]
    )

    card_list_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-list-and-panel.js"
    ).read_text(encoding="utf-8")
    assert "img.alt = window.t ? window.t('steam.characterCardCover')" in card_list_source


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
    assert "delete select.dataset.i18nTitle;" in form_source
    assert ".language-preference-help-button:hover + .language-preference-tooltip" in css_source
    assert ".language-preference-help-button:focus-visible + .language-preference-tooltip" in css_source
    tooltip_rule = css_source.split(
        ".catgirl-panel-right .language-preference-tooltip {", 1
    )[1].split("}", 1)[0]
    assert "top: auto;" in tooltip_rule
    assert "bottom:" in tooltip_rule


@pytest.mark.unit
def test_language_preference_events_are_strictly_character_scoped():
    websocket_source = (
        PROJECT_ROOT / "static" / "app" / "app-websocket.js"
    ).read_text(encoding="utf-8")
    assert (
        "if (!detail.character_name || detail.character_name !== currentName) return;"
        in websocket_source
    )


@pytest.mark.unit
def test_proactive_language_fallbacks_continue_after_empty_preference():
    proactive_source = (
        PROJECT_ROOT / "static" / "app" / "app-proactive.js"
    ).read_text(encoding="utf-8")
    assert "if (!i18nLanguage && window.i18next" in proactive_source
    assert "if (!i18nLanguage && typeof localStorage" in proactive_source


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

        async def send_session_ended_by_server(self):
            calls.append(("notify_session_ended",))

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
    assert calls.index(("set_live_language", "ja")) < next(
        index for index, call in enumerate(calls) if call[0] == "end_session"
    )
    assert calls.index(("notify_session_ended",)) < next(
        index for index, call in enumerate(calls) if call[0] == "end_session"
    )
    assert next(
        index for index, call in enumerate(calls) if call[0] == "end_session"
    ) < calls.index(("clear_recent", config_manager, "Mimi"))


@pytest.mark.unit
def test_language_hydration_keeps_fallbacks_dynamic_and_import_uses_only_explicit_locale():
    websocket_source = (
        PROJECT_ROOT / "static" / "app" / "app-websocket.js"
    ).read_text(encoding="utf-8")
    memory_source = (
        PROJECT_ROOT / "static" / "js" / "memory_browser.js"
    ).read_text(encoding="utf-8")
    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")

    resolver = websocket_source.split(
        "function getConversationLanguageForCurrentCharacter()", 1
    )[1].split("function hydrateConversationLanguage", 1)[0]
    assert resolver.index("S.conversationLanguageHydrated === true") < resolver.index(
        "window.getConversationLanguagePreference"
    )

    hydration = websocket_source.split(
        "function hydrateConversationLanguage(characterName)", 1
    )[1].split("var SETTINGS_SYNC_GATE_TIMEOUT_MS", 1)[0]
    assert "explicitLanguage: explicitLanguage" in hydration
    assert "if (hydrated.explicitLanguage" in hydration
    assert "hydrated.explicitLanguage," in hydration

    assert "async function getExplicitConversationTemplateLanguage" in memory_source
    assert "payload.language.trim() || null" in memory_source
    assert "if (explicitLanguage) payload.language = explicitLanguage;" in memory_source
    assert "window.getConversationLanguagePreference" not in memory_source.split(
        "async function getExplicitConversationTemplateLanguage", 1
    )[1].split("async function buildExternalImportPayload", 1)[0]

    assert (
        "input, textarea, select:not(.conversation-language-select)"
        in form_source
    )


@pytest.mark.unit
def test_conversation_language_hydration_timeout_and_late_response_runtime():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping language hydration harness")

    websocket_source = (
        PROJECT_ROOT / "static" / "app" / "app-websocket.js"
    ).read_text(encoding="utf-8")
    hydration_signature = "function hydrateConversationLanguage(characterName)"
    hydration_end_anchor = "// Upper bound for the settings-sync gate below"
    hydration_start = websocket_source.find(hydration_signature)
    hydration_end = websocket_source.find(hydration_end_anchor, hydration_start)
    assert hydration_start >= 0, "语言水合函数签名锚点已失效，请同步更新测试"
    assert hydration_end > hydration_start, "语言水合结束锚点已失效，请同步更新测试"
    hydration_source = websocket_source[hydration_start:hydration_end]

    harness = textwrap.dedent(
        """
        const assert = require('node:assert/strict');

        let fallbackLanguage = 'en';
        const S = {
          _conversationLanguageHydrationId: 0,
          conversationLanguage: '',
          conversationLanguageHydrated: false
        };
        const fetches = [];
        const timers = [];
        const events = [];

        const window = {
          setConversationLanguagePreference(language, characterName, options) {
            events.push({ type: 'cache', language, characterName, options });
          }
        };

        function getConversationLanguageForCurrentCharacter() {
          return fallbackLanguage;
        }

        function fetch(url) {
          return new Promise((resolve, reject) => {
            fetches.push({ url, resolve, reject });
          });
        }

        function setTimeout(callback, delay) {
          timers.push({ callback, delay });
          return timers.length;
        }

        function _syncLanguageToBackend(language) {
          events.push({ type: 'sync', language });
        }

        function _sendGreetingCheckIfReady() {
          events.push({ type: 'greeting' });
        }

        function response(payload) {
          return { ok: true, json: () => Promise.resolve(payload) };
        }

        async function flushPromises() {
          for (let index = 0; index < 8; index += 1) {
            await Promise.resolve();
          }
        }

        __HYDRATION_SOURCE__

        (async () => {
          // A timeout must apply the fallback immediately, then a valid response
          // from the same hydration generation must replace it exactly once.
          const sameGeneration = hydrateConversationLanguage('Mimi');
          assert.equal(fetches.length, 1);
          assert.equal(timers[0].delay, 2500);
          timers[0].callback();
          assert.equal(await sameGeneration, 'en');
          assert.deepEqual(events, [
            { type: 'sync', language: 'en' },
            { type: 'greeting' }
          ]);

          fetches[0].resolve(response({
            success: true,
            language: 'ja',
            effective_language: 'en'
          }));
          await flushPromises();
          assert.equal(S.conversationLanguage, 'ja');
          assert.deepEqual(events.slice(2), [
            {
              type: 'cache',
              language: 'ja',
              characterName: 'Mimi',
              options: { dispatch: false, source: 'server' }
            },
            { type: 'sync', language: 'ja' },
            { type: 'greeting' }
          ]);

          // A late response from an older character/generation must not overwrite
          // the language selected by the newer hydration.
          events.length = 0;
          fallbackLanguage = 'pt';
          const oldGeneration = hydrateConversationLanguage('Old');
          timers[1].callback();
          assert.equal(await oldGeneration, 'pt');

          fallbackLanguage = 'ko';
          const currentGeneration = hydrateConversationLanguage('Current');
          timers[2].callback();
          assert.equal(await currentGeneration, 'ko');
          assert.equal(S.conversationLanguage, 'ko');

          const eventsBeforeStaleResponse = events.length;
          fetches[1].resolve(response({ success: true, language: 'ru' }));
          await flushPromises();
          assert.equal(S.conversationLanguage, 'ko');
          assert.equal(events.length, eventsBeforeStaleResponse);

          fetches[2].resolve(response({ success: true, language: 'zh-CN' }));
          await flushPromises();
          assert.equal(S.conversationLanguage, 'zh-CN');
          assert.deepEqual(events.slice(-3), [
            {
              type: 'cache',
              language: 'zh-CN',
              characterName: 'Current',
              options: { dispatch: false, source: 'server' }
            },
            { type: 'sync', language: 'zh-CN' },
            { type: 'greeting' }
          ]);

          // A failed request that settles before its timeout applies the fallback
          // once; the later timer cannot trigger a duplicate application.
          events.length = 0;
          fallbackLanguage = 'es';
          const failedGeneration = hydrateConversationLanguage('Failure');
          fetches[3].reject(new Error('network failed'));
          assert.equal(await failedGeneration, 'es');
          assert.deepEqual(events, [
            { type: 'sync', language: 'es' },
            { type: 'greeting' }
          ]);
          timers[3].callback();
          await flushPromises();
          assert.deepEqual(events, [
            { type: 'sync', language: 'es' },
            { type: 'greeting' }
          ]);

          process.stdout.write('ok');
        })().catch((error) => {
          console.error(error && error.stack ? error.stack : error);
          process.exitCode = 1;
        });
        """
    ).replace("__HYDRATION_SOURCE__", hydration_source)

    result = run_node_script(
        node_path,
        harness,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_language_preference_response_uses_http_200(monkeypatch):
    async def read_payload(_request):
        return {"language": "ja"}, None

    async def apply_language(_name, _language):
        return {
            "success": False,
            "partial_success": True,
            "language": "ja",
            "error": "近期上下文清理失败",
        }

    monkeypatch.setattr(preference_router, "_read_json_object_or_400", read_payload)
    monkeypatch.setattr(
        preference_router,
        "apply_character_language_preference",
        apply_language,
    )

    response = await preference_router.set_character_language_preference(
        "Mimi",
        object(),
    )

    assert response.status_code == 200
    assert json.loads(response.body)["partial_success"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_init_inactive_memory_barrier_waits_outside_session_lock():
    lock_states = []

    class SessionManager(LifecycleMixin):
        def __init__(self):
            self.lock = asyncio.Lock()
            self.is_active = True
            self.session = object()
            self._user_session_abandon_epoch = 0
            self._audio_stream_epoch = 0

        def _reset_tts_retry_state(self):
            pass

        def _reset_proactive_gate(self):
            pass

        async def _close_independent_asr(self, **_kwargs):
            pass

        async def _init_renew_status(self):
            self.is_active = False

        def _clear_audio_stream_queue(self, _reason):
            pass

        def _cancel_audio_stream_worker(self, _reason):
            pass

        def _reset_voice_echo_suppression_cache(self):
            pass

        def _queue_session_end_memory_barrier(self, _callback):
            return object()

        async def _wait_for_session_end_memory_barrier(
            self,
            _completion,
            _callback,
            *,
            timeout_seconds,
        ):
            assert timeout_seconds == 15.0
            lock_states.append(self.lock.locked())

    async def clear_recent():
        pass

    manager = SessionManager()
    await manager.end_session(
        by_server=True,
        after_memory_settlement=clear_recent,
    )

    assert lock_states == [False]


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
