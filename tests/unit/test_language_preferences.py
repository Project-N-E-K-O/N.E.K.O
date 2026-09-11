"""Contracts for independent UI languages and per-character language preferences."""

import asyncio
import json
import re
import shutil
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_routers.characters_router import language_preference as preference_router
from main_routers.characters_router import crud as characters_crud
from main_routers.config_router import language as config_language_router
from main_routers.system_router import _shared as system_router_shared
from main_logic import cross_server
from main_logic.core.lifecycle import LifecycleMixin
from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
HYDRATION_START_ANCHOR = "function hydrateConversationLanguage(characterName)"
HYDRATION_END_ANCHOR = "// Upper bound for the settings-sync gate below"
LANGUAGE_LISTENERS_START_ANCHOR = (
    "window.addEventListener('neko:conversation-language-changed'"
)
LANGUAGE_LISTENERS_END_ANCHOR = (
    "window.addEventListener('neko:new-user-icebreaker-ended'"
)


def _allow_ui_language_mutation(monkeypatch):
    monkeypatch.setattr(
        config_language_router,
        "_validate_local_mutation_request",
        lambda *_args, **_kwargs: None,
    )


def _slice_between(source: str, start_anchor: str, end_anchor: str, label: str) -> str:
    start = source.find(start_anchor)
    assert start >= 0, f"{label} 起始锚点已失效，请同步更新测试"
    end = source.find(end_anchor, start + len(start_anchor))
    assert end > start, f"{label} 结束锚点已失效，请同步更新测试"
    return source[start:end]
@pytest.fixture(scope="module")
def node_path():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("node is required for browser runtime harnesses")
    return executable


def _assert_node_ok(node_path: str, harness: str, *, timeout: int = 30) -> None:
    result = run_node_script(
        node_path,
        harness,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "ok"


def _fresh_character_config_lock(monkeypatch, *modules):
    lock = asyncio.Lock()
    for module in modules:
        monkeypatch.setattr(module, "character_config_mutation_lock", lock)
    return lock


@pytest.fixture
def run_connector(monkeypatch):
    async def run(
        *,
        explicit_provider,
        render_provider,
        messages=None,
        after_memory=False,
        analyze_result=True,
    ):
        events = []

        async def post_memory(
            endpoint,
            name,
            payload,
            *,
            timeout_s,
            language=None,
            render_language=None,
        ):
            assert timeout_s > 0
            events.append(
                ("memory", endpoint, name, payload, language, render_language)
            )
            return True, "", {}

        async def publish_analyze(*_args, **kwargs):
            events.append(("analyze", kwargs["trigger"], kwargs.get("language")))
            return analyze_result

        async def after_settlement():
            events.append(("after",))

        monkeypatch.setattr(cross_server, "_post_memory_server", post_memory)
        monkeypatch.setattr(
            cross_server,
            "_publish_analyze_request_with_fallback",
            publish_analyze,
        )
        queue = asyncio.Queue()
        completion = asyncio.get_running_loop().create_future()
        connector = asyncio.create_task(
            cross_server.run_sync_connector(
                queue,
                "Mimi",
                config={"monitor": False, "bullet": False},
                user_language_provider=explicit_provider,
                render_language_provider=render_provider,
            )
        )
        for message in messages or [
            {"type": "user", "data": {"data": "hello", "input_type": "transcript"}}
        ]:
            queue.put_nowait(message)
        ending = {
            "type": "system",
            "data": "session end",
            "_memory_settlement_done": completion,
        }
        if after_memory:
            ending["_after_memory_settlement"] = after_settlement
        queue.put_nowait(ending)
        try:
            await asyncio.wait_for(completion, timeout=1.0)
        finally:
            connector.cancel()
            await asyncio.gather(connector, return_exceptions=True)
        return events

    return run


class _LifecycleHarness(LifecycleMixin):
    def __init__(self, *, renew=None):
        self.lock = asyncio.Lock()
        self.is_active = True
        self.session = object()
        self._starting_session_count = 0
        self._user_session_abandon_epoch = 0
        self._audio_stream_epoch = 0
        self._renew = renew

    def _reset_tts_retry_state(self):
        pass

    def _reset_proactive_gate(self):
        pass

    def clear_speech_playback_gains(self):
        pass

    async def _close_independent_asr(self, **_kwargs):
        pass

    async def _init_renew_status(self):
        self.is_active = False
        if self._renew:
            await self._renew(self)

    def _clear_audio_stream_queue(self, _reason):
        pass

    def _cancel_audio_stream_worker(self, _reason):
        pass

    def _reset_voice_echo_suppression_cache(self):
        pass


@pytest.mark.unit
def test_language_preference_copy_exists_in_all_supported_locales():
    locale_dir = PROJECT_ROOT / "static" / "locales"
    locale_files = {path.stem: path for path in locale_dir.glob("*.json")}
    assert set(locale_files) == SUPPORTED_LOCALES

    required_keys = {
        "languagePreference",
        "languagePreferenceDescription",
        "languagePreferenceSaved",
        "languagePreferencePartiallySaved",
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
def test_character_language_control_static_contract():
    i18n_source = (PROJECT_ROOT / "static" / "i18n-i18next.js").read_text(
        encoding="utf-8"
    )
    manager_dir = PROJECT_ROOT / "static" / "js" / "character_card_manager"
    form_source = (manager_dir / "card-form-and-actions.js").read_text(encoding="utf-8")
    css_source = (
        PROJECT_ROOT / "static" / "css" / "character_card_manager.css"
    ).read_text(encoding="utf-8")

    labels = {
        "简体中文", "繁體中文", "English", "日本語",
        "한국어", "Русский", "Español", "Português",
    }
    assert all(
        re.search(rf'''label:\s*(["']){re.escape(label)}\1''', i18n_source)
        for label in labels
    )
    assert "form.appendChild(languagePreferenceWrapper);" in form_source
    for contract in (
        "language-preference-custom-select",
        "language-preference-help-button",
        "language-preference-tooltip",
        "languageHelpButton.setAttribute('aria-describedby', languageTooltip.id)",
        "languageTooltip.setAttribute('role', 'tooltip')",
    ):
        assert contract in form_source

    for selector in (
        ".language-preference-help-button:hover + .language-preference-tooltip",
        ".language-preference-help-button:focus-visible + .language-preference-tooltip",
    ):
        assert selector in css_source
    tooltip_anchor = (
        ".settings-form-layout.panel-tab-settings .language-preference-tooltip {"
    )
    tooltip_start = css_source.find(tooltip_anchor)
    tooltip_end = css_source.find("}", tooltip_start + len(tooltip_anchor))
    tooltip_rule = css_source[tooltip_start:tooltip_end]
    assert 0 <= tooltip_start < tooltip_end
    assert "top: auto;" in tooltip_rule and "bottom:" in tooltip_rule



@pytest.mark.unit
def test_language_preference_events_are_strictly_character_scoped():
    websocket_source = (
        PROJECT_ROOT / "static" / "app" / "app-websocket.js"
    ).read_text(encoding="utf-8")
    assert (
        "if (!detail.character_name || detail.character_name !== currentName) return;"
        in websocket_source
    )
    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    assert "if (!detail.character_name || detail.character_name !== name) return;" in form_source


@pytest.mark.unit
def test_game_voice_stt_follows_interface_locale_not_character_template_preference():
    source = (
        PROJECT_ROOT / "static" / "app" / "app-audio-capture.js"
    ).read_text(encoding="utf-8")
    recognition_locale_source = _slice_between(
        source,
        "recognition.lang = (function ()",
        "recognition.continuous = true",
        "speech recognition locale",
    )

    assert "window.i18next" in recognition_locale_source
    assert "navigator.language" in recognition_locale_source
    assert "getConversationLanguagePreference" not in recognition_locale_source


@pytest.mark.unit
def test_character_language_event_invalidates_inflight_form_hydration(node_path):
    source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    hydration_source = _slice_between(
        source,
        "function _nextCharacterLanguageHydrationId(select)",
        "async function _saveCharacterLanguagePreference(name, select, selectUi)",
        "character language form hydration",
    )
    harness = textwrap.dedent(
        r"""
        const assert = require('node:assert/strict');
        const CHARACTER_LANGUAGE_OPTIONS = [
          'zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'
        ].map(code => ({ code }));
        const CHARACTER_LANGUAGE_HYDRATION_TIMEOUT_MS = 2500;
        const cached = [], cleared = [], pending = [], timers = [];
        const _cacheCharacterLanguagePreference = (...args) => cached.push(args);
        const _characterLanguageT = key => key;
        let explicit = 'en';
        const window = {
          getExplicitConversationLanguagePreference: () => explicit,
          clearConversationLanguagePreference(name) { cleared.push(name); explicit = ''; }
        };
        class AbortController {
          constructor() { this.signal = {}; }
          abort() { if (this.signal.reject) this.signal.reject(new Error('AbortError')); }
        }
        const setTimeout = callback => (timers.push(callback), timers.length);
        const clearTimeout = () => {};
        const fetch = (_url, { signal }) => new Promise((resolve, reject) => {
          signal.reject = reject;
          pending.push({ resolve, reject });
        });

        __HYDRATION_SOURCE__

        const response = payload => ({
          ok: true, status: 200, json: async () => payload
        });
        const control = () => ({
          select: {
            value: 'en', disabled: true, title: '',
            dataset: { previousValue: 'en' }
          },
          ui: {
            disabled: true, refreshCount: 0,
            setDisabled(value) { this.disabled = value; },
            refresh() { this.refreshCount += 1; }
          }
        });
        async function hydrate(state) {
          const promise = _hydrateCharacterLanguagePreference(
            'Mimi', state.select, state.ui
          );
          await Promise.resolve();
          return promise;
        }

        (async () => {
          let state = control();
          let task = hydrate(state);
          _applyCharacterLanguagePreferenceEvent(state.select, state.ui, 'ja');
          pending.at(-1).resolve(response({
            success: true, language: 'zh-TW', effective_language: 'en'
          }));
          await task;
          assert.deepEqual(
            [state.select.value, state.select.dataset.previousValue, cached.length],
            ['ja', 'ja', 0]
          );

          state = control();
          task = hydrate(state);
          pending.at(-1).resolve(response({
            success: true, language: '', effective_language: 'ja'
          }));
          await task;
          assert.equal(state.select.value, 'ja');
          assert.deepEqual(cleared, ['Mimi']);

          for (const payload of [
            { success: true, language: '', effective_language: 'en' },
            { success: true, language: 'en', effective_language: 'en' }
          ]) {
            state = control();
            explicit = 'en';
            task = hydrate(state);
            explicit = 'ja';
            pending.at(-1).resolve(response(payload));
            await task;
            assert.deepEqual(
              [state.select.value, state.select.dataset.previousValue],
              ['ja', 'ja']
            );
          }
          assert.deepEqual(cleared, ['Mimi']);
          assert.deepEqual(cached, []);

          state = control();
          explicit = 'en';
          task = hydrate(state);
          timers.at(-1)();
          await task;
          assert.equal(state.ui.disabled, false);
          assert.equal(state.ui.refreshCount, 1);
          assert.equal(state.select.title, 'character.languagePreferenceLoadFailed');
          assert.deepEqual(cached, []);
          process.stdout.write('ok');
        })().catch(error => {
          console.error(error && error.stack ? error.stack : error);
          process.exitCode = 1;
        });
        """
    ).replace("__HYDRATION_SOURCE__", hydration_source)
    _assert_node_ok(node_path, harness)


@pytest.mark.unit
def test_browser_language_cache_survives_storage_failures_and_events(node_path):
    source = (PROJECT_ROOT / "static" / "i18n-i18next.js").read_text(encoding="utf-8")
    preference_source = _slice_between(
        source,
        "function normalizeSupportedLanguageCode(rawLanguage)",
        "// 从后端获取语言设置",
        "browser language preference cache",
    )
    harness = textwrap.dedent(
        r"""
        const assert = require('node:assert/strict');
        const SUPPORTED_LANGUAGES = [
          'zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'
        ];
        const values = new Map();
        const listeners = {};
        let storageMode = 'unavailable';
        const window = {
          addEventListener(type, handler) { listeners[type] = handler; },
          dispatchEvent() {}
        };
        const localStorage = {
          getItem(key) {
            if (storageMode === 'unavailable') throw new Error('unavailable');
            return values.has(key) ? values.get(key) : null;
          },
          setItem(key, value) {
            if (
              storageMode === 'unavailable'
              || (storageMode === 'language-write-fails'
                  && key.startsWith('nekoConversationLanguage:'))
            ) throw new Error('quota');
            values.set(key, String(value));
          },
          removeItem(key) {
            if (storageMode === 'unavailable') throw new Error('unavailable');
            values.delete(key);
          }
        };
        const navigator = { language: 'ja-JP', userLanguage: '' };
        const getLanguageFromQuery = () => '';
        class CustomEvent {
          constructor(type, options) { this.type = type; this.detail = options.detail; }
        }

        __PREFERENCE_SOURCE__

        assert.equal(
          window.setConversationLanguagePreference('zh-TW', 'Mimi', { dispatch: false }),
          true
        );
        const firstMimiRevision = window.getConversationLanguagePreferenceRevision('Mimi');
        assert.ok(firstMimiRevision > 0);
        assert.equal(window.getExplicitConversationLanguagePreference('Mimi'), 'zh-TW');
        assert.equal(window.markConversationLanguagePreferenceUntrusted('Mimi'), true);
        assert.equal(window.getExplicitConversationLanguagePreference('Mimi'), '');
        assert.equal(window.getConversationLanguagePreference('Mimi'), 'zh-TW');
        window.setConversationLanguagePreference('en', 'Mimi', { dispatch: false });
        window.markConversationLanguagePreferenceUntrusted('Mimi');
        assert.equal(window.clearConversationLanguagePreference(
          'Mimi', { dispatch: false }
        ), true);
        assert.ok(window.getConversationLanguagePreferenceRevision('Mimi') > firstMimiRevision);
        assert.deepEqual([
          window.getExplicitConversationLanguagePreference('Mimi'),
          window.getConversationLanguagePreference('Mimi'),
          window.getExplicitConversationLanguagePreference('Other'),
          window.getConversationLanguagePreference('Other')
        ], ['', 'ja', '', 'ja']);
        assert.equal(
          window.setConversationLanguagePreference('en', '', { dispatch: false }),
          false
        );

        storageMode = 'normal';
        window.setConversationLanguagePreference('en', 'Quota', { dispatch: false });
        const quotaRevision = window.getConversationLanguagePreferenceRevision('Quota');
        assert.equal(window.getExplicitConversationLanguagePreference('Quota'), 'en');
        values.delete('nekoConversationLanguage:Quota');
        listeners.storage({ key: 'nekoConversationLanguage:Quota', newValue: null });
        assert.ok(window.getConversationLanguagePreferenceRevision('Quota') > quotaRevision);
        assert.deepEqual([
          window.getExplicitConversationLanguagePreference('Quota'),
          window.getConversationLanguagePreference('Quota')
        ], ['', 'ja']);
        values.set('nekoConversationLanguage:Quota', 'ko');
        listeners.storage({ key: 'nekoConversationLanguage:Quota', newValue: 'ko' });
        assert.equal(window.getExplicitConversationLanguagePreference('Quota'), 'ko');

        storageMode = 'unavailable';
        window.setConversationLanguagePreference('pt', 'Marker', { dispatch: false });
        window.markConversationLanguagePreferenceUntrusted('Marker');
        assert.equal(window.getExplicitConversationLanguagePreference('Marker'), '');
        listeners.storage({
          key: 'nekoConversationLanguageUntrusted:Marker', newValue: null
        });
        assert.equal(window.getExplicitConversationLanguagePreference('Marker'), 'pt');

        storageMode = 'normal';
        values.set('nekoConversationLanguage:Persisted', 'ja');
        values.set('nekoConversationLanguageUntrusted:Persisted', '1');
        storageMode = 'language-write-fails';
        window.setConversationLanguagePreference('ko', 'Persisted', { dispatch: false });
        assert.equal(values.get('nekoConversationLanguage:Persisted'), 'ja');
        assert.equal(values.get('nekoConversationLanguageUntrusted:Persisted'), '1');
        assert.equal(window.getConversationLanguagePreference('Persisted'), 'ja');
        assert.equal(window.getExplicitConversationLanguagePreference('Persisted'), '');
        process.stdout.write('ok');
        """
    ).replace("__PREFERENCE_SOURCE__", preference_source)
    _assert_node_ok(node_path, harness)


@pytest.mark.unit
def test_character_manager_mutation_security_and_language_save_fences(node_path):
    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    slices = {
        "SECURITY": _slice_between(
            form_source, "let _characterLanguageCsrfToken = '';",
            "function _nextCharacterLanguageHydrationId",
            "character language mutation security",
        ),
        "SAVE": _slice_between(
            form_source,
            "async function _saveCharacterLanguagePreference(name, select, selectUi)",
            "function buildCatgirlDetailForm(name, rawData, isNew, container)",
            "character language save",
        ),
        "DISABLED": _slice_between(
            form_source, "let restoreFocusAfterEnable = false;",
            "function selectOptionValue(value)", "language dropdown focus",
        ),
    }
    harness = textwrap.dedent(
        r"""
        const assert = require('node:assert/strict');
        let sharedToken = 'shared-token', refreshCalls = 0, pageConfigCalls = 0;
        let requests = [], outcomes = [];
        const cached = [], messages = [], alerts = [];
        const window = { nekoLocalMutationSecurity: {
          async getMutationHeaders() { return { 'X-CSRF-Token': sharedToken }; },
          async refreshToken() { refreshCalls += 1; sharedToken = 'fresh-token'; }
        }};
        const response = (status, body) => ({
          status, ok: status >= 200 && status < 300,
          clone: () => ({ json: async () => body }), json: async () => body
        });
        const fetch = async (url, options) => {
          if (url === '/api/config/page_config') {
            pageConfigCalls += 1;
            return response(200, { autostart_csrf_token: 'standalone-token' });
          }
          requests.push({ url, options });
          return outcomes.shift();
        };
        const _cacheCharacterLanguagePreference = (...args) => cached.push(args);
        const _characterLanguageT = key => key;
        const showMessage = (...args) => messages.push(args);
        const showAlert = async message => alerts.push(message);
        console.error = () => {};

        __SECURITY__
        __SAVE__

        const state = (value = 'ja') => ({
          select: { value, dataset: { previousValue: 'en' }, disabled: false },
          ui: {
            disabled: false, refreshCount: 0,
            setDisabled(value) { this.disabled = value; },
            refresh() { this.refreshCount += 1; }
          }
        });
        const save = current => _saveCharacterLanguagePreference(
          'Mimi', current.select, current.ui
        );
        const assertRequest = (request, method, body, token) => {
          assert.equal(request.options.method, method);
          assert.equal(request.options.headers['Content-Type'], 'application/json');
          assert.equal(request.options.headers['X-CSRF-Token'], token);
          assert.deepEqual(JSON.parse(request.options.body), body);
        };
        const deferred = () => {
          let reject;
          return {
            promise: new Promise((_resolve, rejectValue) => { reject = rejectValue; }),
            reject: error => reject(error)
          };
        };

        async function securityScenarios() {
          outcomes = [response(200, { success: true, language: 'ja' })];
          let current = state();
          await save(current);
          assert.equal(current.select.dataset.previousValue, 'ja');
          assertRequest(requests[0], 'PUT', { language: 'ja' }, 'shared-token');

          requests = [];
          sharedToken = 'stale-token';
          outcomes = [
            response(403, { error_code: 'csrf_validation_failed' }),
            response(200, { success: true, language: 'ko' })
          ];
          current = state('ko');
          await save(current);
          assert.deepEqual([refreshCalls, requests.length], [1, 2]);
          assertRequest(requests[0], 'PUT', { language: 'ko' }, 'stale-token');
          assertRequest(requests[1], 'PUT', { language: 'ko' }, 'fresh-token');

          requests = []; sharedToken = 'retry-limit-stale';
          outcomes = [response(403, { error_code: 'csrf_validation_failed' }), response(403, { error_code: 'csrf_validation_failed' })];
          const retryLimited = await _characterLanguageMutationFetch('/retry-limit', { method: 'PUT' });
          assert.deepEqual([retryLimited.status, requests.length, refreshCalls], [403, 2, 2]);
          requests = [];
          outcomes = [response(403, {
            error_code: 'permission_denied', error: 'denied'
          })];
          current = state('ru');
          await save(current);
          assert.deepEqual([
            requests.length, refreshCalls, current.select.value, alerts.length
          ], [1, 2, 'en', 1]);
          delete window.nekoLocalMutationSecurity;
          _characterLanguageCsrfToken = '';
          requests = [];
          outcomes = [
            response(200, { success: true, language: 'es' }),
            response(200, { success: true, language: 'pt' })
          ];
          for (const [language, previous] of [['es', 'en'], ['pt', 'es']]) {
            current = state(language);
            current.select.dataset.previousValue = previous;
            await save(current);
          }
          assert.deepEqual([pageConfigCalls, requests.length], [1, 2]);
          assertRequest(requests[0], 'PUT', { language: 'es' }, 'standalone-token');
          assertRequest(requests[1], 'PUT', { language: 'pt' }, 'standalone-token');
          assert.deepEqual(cached, [
            ['Mimi', 'ja', 'character-card-manager'],
            ['Mimi', 'ko', 'character-card-manager'],
            ['Mimi', 'es', 'character-card-manager'],
            ['Mimi', 'pt', 'character-card-manager']
          ]);
        }

        async function saveFenceScenarios() {
          requests = [];
          cached.length = messages.length = alerts.length = 0;
          _characterLanguageMutationFetch = (url, options) => {
            requests.push({ url, options });
            return outcomes.shift();
          };

          let current = state();
          outcomes = [response(200, {
            success: false, partial_success: true, language: 'ja',
            error: 'cleanup failed'
          })];
          await save(current);
          assert.deepEqual([
            current.select.value, current.select.dataset.previousValue,
            cached.length, messages.length, alerts.length
          ], ['ja', 'ja', 1, 1, 0]);

          current.select.value = 'en';
          outcomes = [response(500, {
            success: false, partial_success: true, language: 'en',
            error: 'server failure'
          })];
          await save(current);
          assert.deepEqual([current.select.value, alerts.length], ['ja', 1]);

          current = state();
          let pending = deferred();
          outcomes = [pending.promise];
          let task = save(current);
          pending.reject(new Error('offline'));
          await task;
          assert.deepEqual(
            [current.select.value, current.ui.refreshCount, alerts.length, current.ui.disabled],
            ['en', 1, 2, false]
          );

          current = state();
          pending = deferred();
          outcomes = [pending.promise];
          task = save(current);
          current.select.value = 'ru';
          pending.reject(new Error('offline'));
          await task;
          assert.deepEqual(
            [current.select.value, current.ui.refreshCount, alerts.length],
            ['ru', 0, 2]
          );

          current = state();
          const stale = deferred(), latest = deferred();
          outcomes = [stale.promise, latest.promise];
          const staleTask = save(current);
          current.select.value = 'ko';
          const latestTask = save(current);
          stale.reject(new Error('stale offline'));
          await staleTask;
          assert.deepEqual(
            [current.select.value, current.ui.refreshCount, alerts.length, current.ui.disabled],
            ['ko', 0, 2, true]
          );
          latest.reject(new Error('latest offline'));
          await latestTask;
          assert.deepEqual(
            [current.select.value, current.ui.refreshCount, alerts.length, current.ui.disabled],
            ['en', 1, 3, false]
          );
        }

        function focusScenario() {
          const option = {}, outside = {};
          const document = { activeElement: option };
          let focusCount = 0;
          const selectEl = { disabled: false };
          const header = {
            disabled: false, isConnected: true, setAttribute() {},
            focus() { focusCount += 1; document.activeElement = header; }
          };
          const container = {
            contains: element => element === option || element === header,
            classList: { toggle() {} }
          };
          const closeDropdown = () => {};

          __DISABLED__

          setDisabled(true);
          document.activeElement = outside;
          setDisabled(false);
          assert.deepEqual([focusCount, document.activeElement], [1, header]);
          document.activeElement = outside;
          setDisabled(true);
          setDisabled(false);
          assert.deepEqual([focusCount, document.activeElement], [1, outside]);
        }

        (async () => {
          await securityScenarios();
          await saveFenceScenarios();
          focusScenario();
          process.stdout.write('ok');
        })().catch(error => {
          console.error(error && error.stack ? error.stack : error);
          process.exitCode = 1;
        });
        """
    )
    for marker, source in slices.items():
        harness = harness.replace(f"__{marker}__", source)
    _assert_node_ok(node_path, harness)



@pytest.mark.unit
def test_proactive_language_keeps_dynamic_fallback_render_only():
    proactive_source = (
        PROJECT_ROOT / "static" / "app" / "app-proactive.js"
    ).read_text(encoding="utf-8")
    i18n_source = (
        PROJECT_ROOT / "static" / "i18n-i18next.js"
    ).read_text(encoding="utf-8")

    assert "window.getExplicitConversationLanguagePreference" in i18n_source
    assert (
        "window.getExplicitConversationLanguagePreference(lanlanName)"
        in proactive_source
    )
    assert "if (explicitConversationLanguage)" in proactive_source
    assert "payload.i18n_language = explicitConversationLanguage;" in proactive_source
    assert "window.getConversationLanguagePreference(lanlanName)" in proactive_source
    assert "payload.render_language = renderConversationLanguage;" in proactive_source
    proactive_language_block = _slice_between(
        proactive_source,
        "function _applyProactiveLanguagePayload(payload, lanlanName) {",
        "function _isChatInputElement(element) {",
        "主动搭话语言参数",
    )
    assert "window.getConversationLanguagePreference" in proactive_language_block
    assert "payload.i18n_language = renderConversationLanguage;" not in proactive_language_block
    assert "window.i18next" not in proactive_language_block
    assert "i18nextLng" not in proactive_language_block
    assert "navigator.language" not in proactive_language_block
    assert proactive_source.count(
        "_applyProactiveLanguagePayload(voiceProactivePayload, lanlanName);"
    ) == 1
    assert proactive_source.count(
        "_applyProactiveLanguagePayload(requestBody, lanlanName);"
    ) == 1
    voice_payload = _slice_between(
        proactive_source,
        "var voiceProactivePayload = {",
        "async function _sendVoiceProactive() {",
        "语音主动搭话语言参数",
    )
    assert "voice_mode: true" in voice_payload
    assert "JSON.stringify(voiceProactivePayload)" in voice_payload


@pytest.mark.unit
def test_personality_locale_refresh_keeps_the_opened_character_scope():
    source = (
        PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"
    ).read_text(encoding="utf-8")
    refresh_source = _slice_between(
        source,
        "async refreshForLocaleChange()",
        "ensureOverlay()",
        "角色性格预设语言刷新",
    )
    show_source = _slice_between(
        source,
        "showOverlay()",
        "hideOverlay()",
        "角色性格预设重新打开",
    )

    assert "getCurrentLanguage(this.currentCharacterName)" in refresh_source
    assert "getCurrentLanguage(this.currentCharacterName)" in show_source


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle_state", ["active", "idle", "starting"])
async def test_character_language_change_respects_session_lifecycle_owner(
    monkeypatch,
    lifecycle_state,
):
    calls = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        calls.append(("load", name))
        return config_manager, {"当前猫娘": name, "猫娘": {name: {}}}

    _durable = {}

    async def persist_locale(method, name, *, language=None):
        calls.append(("persist", method, name, language))
        if method == "GET":
            return {
                "success": True,
                "language": _durable.get("language"),
                "order": _durable.get("order"),
            }
        _durable["order"] = (_durable.get("order") or 0) + 1
        _durable["language"] = language
        return {
            "success": True,
            "language": language,
            "order": _durable["order"],
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(manager, name, *, expected_generation):
        assert expected_generation
        calls.append(("clear_recent", manager, name))

    class SessionManager:
        is_active = lifecycle_state == "active"
        is_starting = lifecycle_state == "starting"
        session = object() if is_active else None

        def set_user_language(self, language):
            calls.append(("set_live_language", language))

        async def send_session_ended_by_server(self):
            calls.append(("notify_session_ended",))

        async def end_session(self, **kwargs):
            calls.append(("end_session", kwargs))
            await kwargs["after_memory_settlement"]()

        async def settle_session_memory_if_idle(self, callback):
            calls.append(("settle_idle",))
            await callback()
            return True

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
    assert calls.count(("clear_recent", config_manager, "Mimi")) == 1
    # Full tuples, not just the call kind: PUT and GET are both "persist", so a
    # kind-only comparison would accept a stray extra PUT or a missing freshness
    # GET -- exactly the two things this sequence is meant to pin down.
    assert calls[-2:] == [("persist", "GET", "Mimi", None), ("load", "Mimi")], (
        "收尾必须是不带语言参数的新鲜度 GET，随后是最终身份复核"
    )
    assert [call for call in calls if call[0] == "persist" and call[1] == "PUT"] == [
        ("persist", "PUT", "Mimi", "ja")
    ], "durable 写入必须恰好一次且带归一化后的目标语言"

    if lifecycle_state != "active":
        assert result["session_reset"] is False
        expected_calls = [
            ("load", "Mimi"),
            ("persist", "PUT", "Mimi", "ja"),
            ("set_live_language", "ja"),
        ]
        if lifecycle_state == "idle":
            expected_calls.append(("settle_idle",))
        # The clear callback runs outside the caller's transaction now, so it
        # re-validates the character identity and re-checks that this write
        # still owns the durable locale *before* the destructive clear; the
        # final persist is the post-reconciliation freshness GET that refuses to
        # report success for a preference something else has since replaced.
        expected_calls.extend([
            ("load", "Mimi"),
            ("persist", "GET", "Mimi", None),
            ("clear_recent", config_manager, "Mimi"),
            ("persist", "GET", "Mimi", None),
            ("load", "Mimi"),
        ])
        assert calls == expected_calls
        return

    assert result["session_reset"] is True
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
@pytest.mark.asyncio
async def test_unchanged_language_reconciliation_isolates_the_live_session(monkeypatch):
    calls = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        calls.append(("load", name))
        return config_manager, {"当前猫娘": name, "猫娘": {name: {}}}

    _durable = {}

    async def persist_locale(method, name, *, language=None):
        calls.append(("persist", method, name, language))
        if method == "GET":
            return {
                "success": True,
                "language": _durable.get("language"),
                "order": _durable.get("order"),
            }
        _durable["order"] = (_durable.get("order") or 0) + 1
        _durable["language"] = language
        return {
            "success": True,
            "language": language,
            "order": _durable["order"],
            "previous_language": language,
            "changed": False,
        }

    class Manager:
        user_language = "en"
        _user_language_explicit = False
        is_active = True
        session = object()
        fail_after_update = False

        def set_user_language(self, language):
            calls.append(("set_live_language", language))
            self.user_language = language
            self._user_language_explicit = True
            if self.fail_after_update:
                raise RuntimeError("tool refresh failed after locale update")

        async def end_session(self, **kwargs):
            calls.append(("end_session", kwargs))
            await kwargs["after_memory_settlement"]()

        async def send_session_ended_by_server(self):
            calls.append(("notify_session_ended",))

        def reset_session_start_circuit(self):
            calls.append(("reset_circuit",))

    manager = Manager()

    class SessionManager:
        def get(self, name):
            assert name == "Mimi"
            return manager

    async def clear_recent(manager_config, name, *, expected_generation):
        assert expected_generation
        calls.append(("clear_recent", manager_config, name))

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", persist_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result == {
        "success": True,
        "language": "ja",
        "previous_language": "ja",
        "changed": False,
        "recent_history_cleared": True,
        "session_reset": True,
    }
    assert calls[:3] == [
        ("load", "Mimi"),
        ("persist", "PUT", "Mimi", "ja"),
        ("set_live_language", "ja"),
    ]
    assert ("notify_session_ended",) in calls
    assert any(call[0] == "end_session" for call in calls)
    assert any(call[0] == "clear_recent" and call[2] == "Mimi" for call in calls)
    assert ("reset_circuit",) in calls
    assert manager.user_language == "ja"
    assert manager._user_language_explicit is True

    side_effect_count = len(calls)
    repeated = await preference_router.apply_character_language_preference("Mimi", "ja")
    assert repeated["changed"] is False
    assert repeated["recent_history_cleared"] is False
    assert repeated["session_reset"] is False
    assert calls[side_effect_count:] == [
        ("load", "Mimi"),
        ("persist", "PUT", "Mimi", "ja"),
    ]

    manager._user_language_explicit = False
    promotion_start = len(calls)
    promoted = await preference_router.apply_character_language_preference("Mimi", "ja")
    assert promoted["changed"] is False
    assert promoted["recent_history_cleared"] is False
    assert promoted["session_reset"] is False
    assert calls[promotion_start:] == [
        ("load", "Mimi"),
        ("persist", "PUT", "Mimi", "ja"),
        ("set_live_language", "ja"),
    ]
    assert manager._user_language_explicit is True

    manager.user_language = "en"
    manager._user_language_explicit = False
    manager.fail_after_update = True
    partial_start = len(calls)
    partial = await preference_router.apply_character_language_preference("Mimi", "ja")
    assert partial["changed"] is False
    assert partial["success"] is False
    assert partial["partial_success"] is True
    assert partial["recent_history_cleared"] is True
    assert partial["session_reset"] is True
    partial_calls = calls[partial_start:]
    assert partial_calls[:3] == [
        ("load", "Mimi"),
        ("persist", "PUT", "Mimi", "ja"),
        ("set_live_language", "ja"),
    ]
    assert ("notify_session_ended",) in partial_calls
    assert any(call[0] == "end_session" for call in partial_calls)
    assert any(call[0] == "clear_recent" for call in partial_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_language_changes_are_serialized_through_side_effects(monkeypatch):
    """Concurrent preference writes must not interleave their durable section.

    The transaction now covers the existence check plus the memory-server write
    rather than the whole request (live-session reconciliation runs unlocked),
    so the serialization point observed here is the durable write itself.
    """
    _fresh_character_config_lock(monkeypatch, preference_router)
    entered = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def persist_locale(_method, name, *, language=None):
        if _method == "GET":
            return {"success": True, "language": language or "ja", "order": 1}
        entered.append((name, language))
        if language == "ja":
            first_entered.set()
            await release_first.wait()
        return {
            "success": True,
            "language": language,
            "order": 1,
            "previous_language": language,
            "changed": False,
        }

    monkeypatch.setattr(
        preference_router,
        "_request_memory_prompt_locale",
        persist_locale,
    )
    monkeypatch.setattr(preference_router, "get_session_manager", dict)
    monkeypatch.setattr(
        preference_router,
        "_load_existing_character",
        lambda _name: asyncio.sleep(
            0,
            result=(SimpleNamespace(memory_dir="unused"), {}),
        ),
    )

    first = asyncio.create_task(
        preference_router.apply_character_language_preference("ConcurrentMimi", "ja")
    )
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second = asyncio.create_task(
        preference_router.apply_character_language_preference("ConcurrentMimi", "en")
    )
    await asyncio.sleep(0)
    assert entered == [("ConcurrentMimi", "ja")]
    assert not second.done()
    release_first.set()
    await asyncio.gather(first, second)
    assert entered == [
        ("ConcurrentMimi", "ja"),
        ("ConcurrentMimi", "en"),
    ]


# ``_InlineCharacterMutationDrain`` and its test were removed together: the
# drain only existed so a connector callback could finish under the caller's
# still-held config transaction.  The request now releases that transaction
# before the connector round-trip, so the callback simply takes the lock itself
# (see test_session_reconciliation_runs_without_the_config_transaction in
# tests/unit/test_language_preference_lock_scope.py).


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_kind", ["delete", "rename"])
@pytest.mark.parametrize("save_first", [True, False])
async def test_language_save_and_identity_mutation_share_global_lock(
    monkeypatch,
    mutation_kind,
    save_first,
):
    _fresh_character_config_lock(monkeypatch, preference_router, characters_crud)
    state = {"exists": True}
    calls = []
    save_entered = asyncio.Event()
    mutation_entered = asyncio.Event()
    release_first = asyncio.Event()
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        calls.append(("load", name))
        if not state["exists"]:
            raise LookupError("角色不存在")
        return config_manager, {"猫娘": {name: {}}}

    async def persist_locale(_method, name, *, language=None):
        if _method == "GET":
            return {"success": True, "language": language or "ja", "order": 1}
        calls.append(("save", name, language))
        save_entered.set()
        if save_first:
            await release_first.wait()
        return {
            "success": True,
            "language": language,
            "order": 1,
            "previous_language": language,
            "changed": False,
        }

    async def delete_serialized(name):
        calls.append(("delete", name))
        state["exists"] = False
        mutation_entered.set()
        if not save_first:
            await release_first.wait()
        return {"success": True}

    async def rename_serialized(old_name, new_name):
        calls.append(("rename", old_name, new_name))
        state["exists"] = False
        mutation_entered.set()
        if not save_first:
            await release_first.wait()
        return {"success": True}

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(
        preference_router,
        "_request_memory_prompt_locale",
        persist_locale,
    )
    monkeypatch.setattr(preference_router, "get_session_manager", dict)
    monkeypatch.setattr(
        characters_crud, "_delete_catgirl_by_name_serialized", delete_serialized
    )
    monkeypatch.setattr(
        characters_crud, "_rename_catgirl_serialized", rename_serialized
    )

    async def mutate():
        if mutation_kind == "delete":
            return await characters_crud._delete_catgirl_by_name("Race")
        return await characters_crud.rename_catgirl(
            "Race",
            SimpleNamespace(
                json=lambda: asyncio.sleep(0, result={"new_name": "Renamed"})
            ),
        )

    if save_first:
        save_task = asyncio.create_task(
            preference_router.apply_character_language_preference("Race", "ja")
        )
        await asyncio.wait_for(save_entered.wait(), timeout=1)
        mutation_task = asyncio.create_task(mutate())
        await asyncio.sleep(0)
        assert not mutation_entered.is_set()
        release_first.set()
        save_result, mutation_result = await asyncio.wait_for(
            asyncio.gather(save_task, mutation_task), timeout=1
        )
        assert save_result["success"] is True
        assert mutation_result["success"] is True
        assert calls.index(("save", "Race", "ja")) < next(
            index for index, call in enumerate(calls) if call[0] == mutation_kind
        )
    else:
        mutation_task = asyncio.create_task(mutate())
        await asyncio.wait_for(mutation_entered.wait(), timeout=1)
        save_task = asyncio.create_task(
            preference_router.apply_character_language_preference("Race", "ja")
        )
        await asyncio.sleep(0)
        assert not save_task.done()
        assert not save_entered.is_set()
        release_first.set()
        assert (await mutation_task)["success"] is True
        with pytest.raises(LookupError, match="角色不存在"):
            await save_task
        assert not save_entered.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("identity_change", [None, "delete", "rename", "reuse"])
async def test_late_language_clear_is_fenced_by_recent_identity(
    monkeypatch,
    tmp_path,
    identity_change,
):
    _fresh_character_config_lock(monkeypatch, preference_router)
    name = "Race"
    config_manager = SimpleNamespace(memory_dir=tmp_path)
    recent_path = tmp_path / name / "recent.json"
    recent_path.parent.mkdir(parents=True)
    recent_path.write_text("[]", encoding="utf-8")
    late_callback = None
    identity_changed = False

    async def load_character(_name):
        if identity_changed:
            raise LookupError("角色不存在")
        return config_manager, {"猫娘": {name: {}}}

    _durable = {}

    async def persist_locale(method, _name, *, language=None):
        if method == "GET":
            return {
                "success": True,
                "language": _durable.get("language"),
                "order": _durable.get("order"),
            }
        _durable["order"] = (_durable.get("order") or 0) + 1
        _durable["language"] = language
        return {
            "success": True,
            "language": language,
            "order": _durable["order"],
            "previous_language": "en",
            "changed": True,
        }

    clear_calls = []

    async def clear_recent(manager, character_name, *, expected_generation):
        from utils.recent_file import recent_file_access

        with recent_file_access(
            Path(manager.memory_dir) / character_name / "recent.json",
            expected_generation=expected_generation,
        ):
            clear_calls.append(character_name)

    class Manager:
        user_language = "en"
        _user_language_explicit = False
        is_active = False
        session = None

        def set_user_language(self, language):
            self.user_language = language
            self._user_language_explicit = True

        async def settle_session_memory_if_idle(self, callback):
            nonlocal late_callback
            late_callback = callback
            return True

        def reset_session_start_circuit(self):
            pass

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", persist_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", lambda: {name: Manager()})

    result = await preference_router.apply_character_language_preference(name, "ja")
    assert result["recent_history_cleared"] is True
    assert clear_calls == [name]
    assert callable(late_callback)

    if identity_change == "delete":
        identity_changed = True
        from utils.recent_file import fence_recent_deletions_and_clear_redirects
        fence_recent_deletions_and_clear_redirects([recent_path])
    elif identity_change == "rename":
        # Rename fences the late callback through the authoritative character
        # re-load even when this old recent path has no delete generation mark.
        identity_changed = True
    elif identity_change == "reuse":
        from utils.recent_file import activate_recent_paths
        activate_recent_paths([recent_path])

    await late_callback()
    assert clear_calls == ([name, name] if identity_change is None else [name])


@pytest.mark.unit
def test_external_import_defers_locale_resolution_and_preserves_language_select():
    memory_source = (
        PROJECT_ROOT / "static" / "js" / "memory_browser.js"
    ).read_text(encoding="utf-8")
    builder = _slice_between(
        memory_source,
        "async function buildExternalImportPayload",
        "function broadcastExternalMemoryEdited",
        "外部记忆导入请求构建函数",
    )
    assert "getExplicitConversationTemplateLanguage" not in memory_source
    assert "/language-preference" not in builder
    assert "payload.language" not in builder

    form_source = (
        PROJECT_ROOT / "static" / "js" / "character_card_manager"
        / "card-form-and-actions.js"
    ).read_text(encoding="utf-8")
    assert "input, textarea, select:not(.conversation-language-select)" in form_source



@pytest.mark.unit
def test_greeting_render_language_stays_out_of_explicit_language_path():
    router_source = (
        PROJECT_ROOT / "main_routers" / "websocket_router.py"
    ).read_text(encoding="utf-8")
    greeting_source = (
        PROJECT_ROOT / "main_logic" / "core" / "greeting.py"
    ).read_text(encoding="utf-8")
    memory_source = (
        PROJECT_ROOT / "app" / "memory_server" / "routes.py"
    ).read_text(encoding="utf-8")

    assert 'message.get("render_language")' in router_source
    assert "set_render_language" in router_source
    assert "set_user_language(render_language)" not in router_source
    assert "render_language=render_language" in router_source
    assert "self.user_language or render_language" in greeting_source
    assert "render_language: str | None = None" in memory_source
    assert "else render_language" in memory_source


@pytest.mark.unit
def test_conversation_language_hydration_timeout_and_late_response_runtime(node_path):
    websocket_source = (
        PROJECT_ROOT / "static" / "app" / "app-websocket.js"
    ).read_text(encoding="utf-8")
    slices = {
        "RESOLVERS": _slice_between(
            websocket_source, "function getLiveRendererLanguage()",
            HYDRATION_START_ANCHOR, "conversation language resolvers",
        ),
        "HYDRATION": _slice_between(
            websocket_source, HYDRATION_START_ANCHOR, HYDRATION_END_ANCHOR,
            "conversation language hydration",
        ),
        "HANDSHAKE": _slice_between(
            websocket_source, "function attachStartSessionHandshake(ws)",
            "function connectWebSocket()", "start session handshake",
        ),
        "CLEAR_SYNC": _slice_between(
            websocket_source,
            "function _syncClearedLanguageToBackend(lng, characterName)",
            "if (window.i18next && typeof window.i18next.on === 'function')",
            "cleared language sync",
        ),
        "LISTENERS": _slice_between(
            websocket_source, LANGUAGE_LISTENERS_START_ANCHOR,
            LANGUAGE_LISTENERS_END_ANCHOR, "language listeners",
        ),
    }
    harness = textwrap.dedent(
        """
        const assert = require('node:assert/strict');
        let fallback = 'en', cached = '', untrusted = false, character = 'Mimi';
        const preferenceRevisions = new Map();
        const S = {
          _conversationLanguageHydrationId: 0,
          _conversationLanguageClearPending: null,
          conversationLanguage: '', conversationLanguageExplicit: '',
          conversationLanguageHydrated: false
        };
        const fetches = [], timers = [], events = [], listeners = {};
        const window = {
          i18next: {},
          addEventListener(type, callback) { listeners[type] = callback; },
          setConversationLanguagePreference(language, characterName, options) {
            untrusted = false;
            preferenceRevisions.set(characterName, (preferenceRevisions.get(characterName) || 0) + 1);
            events.push({ type: 'cache', language, characterName, options });
          },
          clearConversationLanguagePreference(characterName, options) {
            untrusted = false;
            preferenceRevisions.set(characterName, (preferenceRevisions.get(characterName) || 0) + 1);
            events.push({ type: 'clear-cache', characterName, options });
          },
          getConversationLanguagePreferenceRevision: name => preferenceRevisions.get(name) || 0,
          getConversationLanguagePreference: () => fallback,
          getCachedConversationLanguagePreference: () => cached,
          getExplicitConversationLanguagePreference: () => untrusted ? '' : cached,
          markConversationLanguagePreferenceUntrusted() {
            untrusted = true;
            return true;
          }
        };
        Object.defineProperty(window.i18next, 'language', { get: () => fallback });
        const localStorage = { getItem: () => null };
        const navigator = { language: 'en' };
        const getWebSocketLanlanName = () => character;
        const fetch = url => new Promise((resolve, reject) => {
          fetches.push({ url, resolve, reject });
        });
        const setTimeout = (callback, delay) => (
          timers.push({ callback, delay }), timers.length
        );
        const _syncLanguageToBackend = language => events.push({ type: 'sync', language });
        const _syncRenderLanguageToBackend = language => (
          events.push({ type: 'render-sync', language })
        );
        const WebSocket = { OPEN: 1 };
        S.socket = {
          readyState: WebSocket.OPEN,
          send(data) { events.push({ type: 'socket-send', payload: JSON.parse(data) }); }
        };
        const _sendGreetingCheckIfReady = () => events.push({ type: 'greeting' });
        const response = payload => ({ ok: true, json: async () => payload });
        const flush = async () => {
          for (let index = 0; index < 8; index += 1) await Promise.resolve();
        };

        __RESOLVERS__
        __HYDRATION__
        __HANDSHAKE__
        __CLEAR_SYNC__
        __LISTENERS__

        const state = () => [S.conversationLanguage, S.conversationLanguageExplicit];
        const kinds = () => events.map(event => event.type);
        const resetEvents = () => { events.length = 0; };
        const start = (name, language) => {
          character = name;
          fallback = language;
          return hydrateConversationLanguage(name);
        };
        const timeout = async task => {
          assert.equal(timers.at(-1).delay, 2500);
          timers.at(-1).callback();
          return task;
        };
        const resolveLate = async (index, payload) => {
          fetches[index].resolve(response(payload));
          await flush();
        };

        (async () => {
          let task = start('Mimi', 'en');
          assert.equal(await timeout(task), 'en');
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds(), ['greeting']);
          await resolveLate(0, {
            success: true, language: 'ja', effective_language: 'en'
          });
          assert.deepEqual(state(), ['ja', 'ja']);
          assert.deepEqual(kinds(), ['greeting', 'cache', 'sync', 'greeting']);

          resetEvents();
          const oldTask = start('Old', 'pt');
          assert.equal(await timeout(oldTask), 'pt');
          const currentTask = start('Current', 'ko');
          assert.equal(await timeout(currentTask), 'ko');
          const beforeOld = events.length;
          await resolveLate(1, { success: true, language: 'ru' });
          assert.equal(events.length, beforeOld);
          await resolveLate(2, {
            success: true, language: '', effective_language: 'ko'
          });
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds().slice(-3), ['clear-cache', 'socket-send', 'greeting']);
          assert.deepEqual(events.at(-2).payload, {
            action: 'language_update', clear_language_preference: true,
            render_language: 'ko'
          });

          resetEvents();
          cached = 'ja';
          task = start('Failure', 'es');
          fetches[3].reject(new Error('network failed'));
          assert.equal(await task, 'es');
          assert.deepEqual(state(), ['ja', '']);
          assert.equal(untrusted, true);
          const frames = [];
          const socket = { send: data => frames.push(JSON.parse(data)) };
          attachStartSessionHandshake(socket);
          socket.send(JSON.stringify({ action: 'start_session' }));
          assert.deepEqual(frames, [{ action: 'start_session', render_language: 'ja' }]);
          assert.deepEqual(kinds(), ['greeting']);
          timers[3].callback();
          await flush();
          assert.deepEqual(kinds(), ['greeting']);
          cached = '';

          for (const scenario of [
            {
              event: 'neko:conversation-language-changed',
              detail: { character_name: 'Mimi', language: 'ja' },
              expected: 'ja', server: 'ru'
            },
            {
              event: 'storage',
              detail: { key: 'nekoConversationLanguage:Mimi', newValue: 'ko' },
              expected: 'ko', server: 'zh-CN'
            }
          ]) {
            resetEvents();
            task = start('Mimi', 'en');
            await timeout(task);
            listeners[scenario.event](scenario.event === 'storage'
              ? scenario.detail : { detail: scenario.detail });
            const before = events.length;
            await resolveLate(fetches.length - 1, {
              success: true, language: scenario.server
            });
            assert.deepEqual(state(), [scenario.expected, scenario.expected]);
            assert.equal(events.length, before);
          }

          resetEvents();
          cached = 'ko';
          Object.assign(S, {
            conversationLanguage: 'ko', conversationLanguageExplicit: 'ko',
            conversationLanguageHydrated: true
          });
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: '1'
          });
          assert.deepEqual(state(), ['ko', 'ko']);
          assert.deepEqual(kinds(), ['cache']);

          resetEvents();
          task = hydrateConversationLanguage('Mimi');
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: '1'
          });
          assert.deepEqual(events, []);
          fetches.at(-1).resolve(response({
            success: true, language: 'zh-TW', effective_language: 'en'
          }));
          assert.equal(await task, 'zh-TW');
          assert.deepEqual(state(), ['zh-TW', 'zh-TW']);
          assert.deepEqual(kinds(), ['cache', 'sync', 'greeting']);

          resetEvents();
          Object.assign(S, {
            conversationLanguage: '', conversationLanguageExplicit: '',
            conversationLanguageHydrated: true
          });
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: '1'
          });
          assert.deepEqual(state(), ['ko', '']);
          assert.deepEqual(kinds(), ['render-sync', 'greeting']);
          resetEvents();
          untrusted = false;
          cached = 'ru';
          const fetchCount = fetches.length;
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: null
          });
          assert.deepEqual(state(), ['ru', 'ru']);
          assert.deepEqual(kinds(), ['sync', 'greeting']);
          assert.equal(fetches.length, fetchCount);

          resetEvents();
          cached = '';
          task = hydrateConversationLanguage('Mimi');
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: null
          });
          fetches.at(-1).resolve(response({
            success: true, language: 'pt', effective_language: 'en'
          }));
          assert.equal(await task, 'pt');
          assert.deepEqual(state(), ['pt', 'pt']);
          assert.deepEqual(kinds(), ['cache', 'sync', 'greeting']);

          resetEvents();
          cached = 'ja';
          task = start('Mimi', 'en');
          await timeout(task);
          untrusted = false;
          listeners.storage({
            key: 'nekoConversationLanguageUntrusted:Mimi', newValue: null
          });
          assert.deepEqual(state(), ['ja', 'ja']);
          await resolveLate(fetches.length - 1, {
            success: true, language: 'zh-CN', effective_language: 'en'
          });
          assert.deepEqual(state(), ['zh-CN', 'zh-CN']);
          assert.deepEqual(kinds().slice(-3), ['cache', 'sync', 'greeting']);
          cached = '';

          // A successful empty response uses the renderer locale that is live
          // when it applies, not the backend's stale effective fallback.
          resetEvents();
          task = start('Renderer', 'en');
          const rendererFetch = fetches.at(-1);
          fallback = 'ja';
          rendererFetch.resolve(response({
            success: true, language: '', effective_language: 'ru'
          }));
          assert.equal(await task, 'ja');
          assert.deepEqual(state(), ['', '']);
          assert.equal(events.at(-2).payload.render_language, 'ja');

          // Shared storage can expose a newer explicit value before this
          // document receives its storage event. Preserve only values that
          // appeared while the request was in flight.
          resetEvents();
          cached = '';
          task = start('SiblingWrite', 'en');
          const siblingFetch = fetches.at(-1);
          cached = 'ja';
          siblingFetch.resolve(response({
            success: true, language: '', effective_language: 'ru'
          }));
          assert.equal(await task, 'ja');
          assert.deepEqual(state(), ['ja', 'ja']);
          assert.deepEqual(kinds(), ['cache', 'sync', 'greeting']);

          resetEvents();
          cached = 'ko';
          task = start('StartupCache', 'en');
          fetches.at(-1).resolve(response({
            success: true, language: '', effective_language: 'ru'
          }));
          assert.equal(await task, 'en');
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds(), ['clear-cache', 'socket-send', 'greeting']);

          resetEvents();
          cached = 'ko';
          task = start('RevisionClear', 'en');
          window.clearConversationLanguagePreference('RevisionClear', {
            dispatch: false, source: 'server'
          });
          cached = '';
          resetEvents();
          fetches.at(-1).resolve(response({ success: true, language: 'ko' }));
          assert.equal(await task, 'en');
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds(), ['greeting']);

          resetEvents();
          cached = 'ja';
          task = start('RevisionTimeout', 'en');
          window.setConversationLanguagePreference('ja', 'RevisionTimeout', {
            dispatch: false, source: 'server'
          });
          resetEvents();
          assert.equal(await timeout(task), 'ja');
          assert.equal(untrusted, false);
          assert.deepEqual(state(), ['ja', 'ja']);
          assert.deepEqual(kinds(), ['greeting']);
          await resolveLate(fetches.length - 1, {
            success: true, language: 'ko'
          });
          assert.equal(untrusted, false);
          assert.deepEqual(state(), ['ja', 'ja']);
          assert.deepEqual(kinds(), ['greeting', 'greeting']);
          cached = '';
          character = 'Mimi';

          resetEvents();
          fallback = 'pt';
          listeners.storage({
            key: 'nekoConversationLanguage:Mimi', newValue: null
          });
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds(), ['clear-cache', 'socket-send', 'greeting']);
          assert.equal(events[1].payload.render_language, 'pt');

          resetEvents();
          fallback = 'es';
          Object.assign(S, {
            conversationLanguage: 'ja', conversationLanguageExplicit: 'ja'
          });
          listeners['neko:conversation-language-cleared']({
            detail: { character_name: 'Mimi', source: 'server' }
          });
          assert.deepEqual(state(), ['', '']);
          assert.deepEqual(kinds(), ['socket-send', 'greeting']);
          assert.equal(events[0].payload.render_language, 'es');

          resetEvents();
          S._conversationLanguageClearPending = null;
          _syncClearedLanguageToBackend('ru', 'Other');
          assert.equal(S._conversationLanguageClearPending, null);
          assert.deepEqual(events, []);
          S.socket.readyState = 0;
          _syncClearedLanguageToBackend('ru', 'Mimi');
          assert.deepEqual(S._conversationLanguageClearPending, {
            characterName: 'Mimi'
          });
          S.socket.readyState = WebSocket.OPEN;
          const pending = S._conversationLanguageClearPending;
          _syncClearedLanguageToBackend(
            getConversationLanguageForCurrentCharacter(), pending.characterName
          );
          assert.equal(S._conversationLanguageClearPending, null);
          assert.deepEqual(kinds(), ['socket-send']);
          assert.equal(events[0].payload.render_language, 'es');
          process.stdout.write('ok');
        })().catch(error => {
          console.error(error && error.stack ? error.stack : error);
          process.exitCode = 1;
        });
        """
    )
    for marker, source in slices.items():
        harness = harness.replace(f"__{marker}__", source)
    _assert_node_ok(node_path, harness)


@pytest.mark.unit
def test_character_form_retranslates_locale_dependent_content():
    source = (
        PROJECT_ROOT
        / "static/js/character_card_manager/card-form-and-actions.js"
    ).read_text(encoding="utf-8")

    assert "window.removeEventListener('localechange', previousForm._localeChangeHandler)" in source
    assert "window.addEventListener('localechange', form._localeChangeHandler)" in source
    assert "form._voiceLocaleRefreshSequence !== refreshSequence" in source
    assert "form._voicesLoadPromise = refreshVoiceCatalog(voiceSelect.value);" in source
    assert "data-i18n=\"character.personalitySelect\"" in source
    assert "data-i18n=\"character.personalityClear\"" in source
    assert "defaultOption.dataset.i18n = 'character.voiceNotSet'" in source


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_character_language_preference_requires_verified_local_mutation(
    monkeypatch, verified
):
    apply_calls = []

    class Request:
        method = "PUT"
        base_url = "http://localhost:48911/"
        url = SimpleNamespace(
            path="/api/characters/character/Mimi/language-preference"
        )
        headers = (
            {
                "origin": "http://localhost:48911",
                "X-CSRF-Token": "test-token",
            }
            if verified
            else {}
        )

        async def json(self):
            return {"language": "ja"}

    async def apply_language(name, language):
        apply_calls.append((name, language))
        return {"success": True, "language": language}

    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", "test-token")
    monkeypatch.setattr(
        system_router_shared,
        "AUTOSTART_ALLOWED_ORIGINS",
        ("http://localhost:48911",),
    )
    monkeypatch.setattr(
        preference_router, "apply_character_language_preference", apply_language
    )

    response = await preference_router.set_character_language_preference(
        "Mimi", Request()
    )
    body = json.loads(response.body)
    assert response.status_code == (200 if verified else 403)
    assert apply_calls == ([("Mimi", "ja")] if verified else [])
    if verified:
        assert body["success"] is True
    else:
        assert body["success"] is False
        assert body["error_code"] == "csrf_validation_failed"



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
        "_validate_local_mutation_request",
        lambda *_args, **_kwargs: None,
    )
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
    manager = _LifecycleHarness()
    manager._queue_session_end_memory_barrier = lambda _callback: object()

    async def wait(_completion, _callback, *, timeout_seconds):
        assert timeout_seconds == 15.0
        lock_states.append(manager.lock.locked())

    manager._wait_for_session_end_memory_barrier = wait
    await manager.end_session(
        by_server=True,
        after_memory_settlement=lambda: asyncio.sleep(0),
    )
    assert lock_states == [False]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idle_memory_barrier_rechecks_start_after_lock_wait():
    effects = []
    manager = _LifecycleHarness()
    manager.is_active = False
    manager.session = None
    manager._queue_session_end_memory_barrier = lambda _callback: effects.append("queued")

    await manager.lock.acquire()
    settlement = asyncio.create_task(
        manager.settle_session_memory_if_idle(lambda: effects.append("cleared"))
    )
    await asyncio.sleep(0)
    assert not settlement.done()
    manager._starting_session_count = 1
    manager.lock.release()

    assert await settlement is False
    assert effects == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_init_stale_session_does_not_clear_replacement_context():
    effects = []
    old_session = object()
    replacement_session = object()

    async def replace(manager):
        manager.session = replacement_session

    manager = _LifecycleHarness(renew=replace)
    manager.session = old_session
    manager._clear_audio_stream_queue = lambda reason: effects.append(("clear", reason))
    manager._cancel_audio_stream_worker = lambda reason: effects.append(("cancel", reason))
    manager._reset_voice_echo_suppression_cache = lambda: effects.append(("echo",))
    manager._queue_session_end_memory_barrier = lambda _callback: effects.append(("queue",))

    await manager.end_session(
        by_server=True,
        expected_session=old_session,
        after_memory_settlement=lambda: asyncio.sleep(0),
    )
    assert manager.session is replacement_session
    assert effects == []



@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_end_memory_barrier_clears_after_unsynced_tail(run_connector):
    def reject_render_fallback():
        raise AssertionError("explicit language must short-circuit render provider")

    events = await run_connector(
        explicit_provider=lambda: "ja",
        render_provider=reject_render_fallback,
        after_memory=True,
        analyze_result=False,
    )
    memory = next(event for event in events if event[0] == "memory")
    assert memory[1:3] == ("cache", "Mimi")
    assert memory[3][0]["role"] == "user"
    assert memory[4:] == ("ja", None)
    assert events[-1] == ("after",)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_mode", "expected"),
    [("render_only", (None, "ja")), ("invalid", (None, None))],
)
async def test_connector_memory_locale_provenance(run_connector, provider_mode, expected):
    def explicit_provider():
        if provider_mode == "invalid":
            raise RuntimeError("session state unavailable")
        return "invalid"

    events = await run_connector(
        explicit_provider=explicit_provider,
        render_provider=lambda: "ja" if provider_mode == "render_only" else "estonian",
        analyze_result=False,
    )
    memory = next(event for event in events if event[0] == "memory")
    assert memory[1:3] == ("process", "Mimi")
    assert memory[3][0]["role"] == "user"
    assert memory[4:] == expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_mode", "expected_analyze", "expected_memory"),
    [
        ("render_only", "ja", (None, "ja")),
        ("explicit", "ko", ("ko", None)),
        ("invalid", None, (None, None)),
        ("exception", None, (None, None)),
    ],
)
async def test_connector_analyzer_uses_visible_locale_without_promoting_render_hint(
    run_connector,
    provider_mode,
    expected_analyze,
    expected_memory,
):
    render_provider_calls = 0

    def explicit_provider():
        if provider_mode == "explicit":
            return "ko"
        if provider_mode == "exception":
            raise RuntimeError("explicit session state unavailable")
        return "invalid"

    def render_provider():
        nonlocal render_provider_calls
        render_provider_calls += 1
        if provider_mode == "exception":
            raise RuntimeError("render session state unavailable")
        return "estonian" if provider_mode == "invalid" else "ja"

    events = await run_connector(
        explicit_provider=explicit_provider,
        render_provider=render_provider,
        messages=[
            {"type": "user", "data": {
                "data": "first turn", "input_type": "transcript",
            }},
            {"type": "json", "data": {
                "type": "gemini_response", "text": "first reply",
                "request_id": "request-1",
            }},
            {"type": "system", "data": "turn end"},
            {"type": "user", "data": {
                "data": "second turn", "input_type": "transcript",
            }},
        ],
    )
    assert [event[1:] for event in events if event[0] == "analyze"] == [
        ("turn_end", expected_analyze),
        ("session_end", expected_analyze),
    ]
    memory = [event for event in events if event[0] == "memory"]
    assert [event[1] for event in memory] == ["cache", "process"]
    assert [event[4:] for event in memory] == [expected_memory, expected_memory]
    if provider_mode == "explicit":
        assert render_provider_calls == 0
    else:
        assert render_provider_calls > 0




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


class _UiLanguageRequest:
    base_url = "http://localhost:48911/"
    method = "PUT"
    url = SimpleNamespace(path="/api/config/ui-language")

    def __init__(self, language="ja", headers=None):
        self.language = language
        self.headers = headers or {}

    async def json(self):
        return {"language": self.language}


def _ui_payload(result):
    return result if isinstance(result, dict) else json.loads(result.body)


def _install_ui_language_runtime(
    monkeypatch,
    *,
    apply_language,
    save_language,
    previous="en",
    current="Mimi",
):
    class ConfigManager:
        async def aload_characters(self):
            characters = {current: {}} if current else {}
            return {"当前猫娘": current, "猫娘": characters}

    async def load_language():
        return previous

    monkeypatch.setattr(config_language_router, "aload_ui_language_override", load_language)
    monkeypatch.setattr(config_language_router, "asave_ui_language_override", save_language)
    monkeypatch.setattr(config_language_router, "get_config_manager", lambda: ConfigManager())
    monkeypatch.setattr(
        preference_router,
        "apply_character_language_preference",
        apply_language,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ui_language_mutation_requires_local_request_auth(monkeypatch):
    saved = []

    async def save_language(language):
        saved.append(language)
        return True

    async def apply_language(_name, language):
        return {"success": True, "language": language}

    _install_ui_language_runtime(
        monkeypatch,
        apply_language=apply_language,
        save_language=save_language,
        current="",
    )
    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", "token")
    monkeypatch.setattr(
        system_router_shared,
        "AUTOSTART_ALLOWED_ORIGINS",
        ("http://localhost:48911",),
    )

    denied = await config_language_router.set_ui_language_api(_UiLanguageRequest())
    assert denied.status_code == 403
    assert saved == []

    accepted = await config_language_router.set_ui_language_api(
        _UiLanguageRequest(headers={
            "X-CSRF-Token": "token",
            "origin": "http://localhost:48911",
        })
    )
    assert accepted["success"] is True
    assert accepted["language"] == "ja"
    assert saved == ["ja"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "status", "saved", "ui_language", "partial"),
    [
        ("partial", None, ["ja"], None, False),
        ("rollback", 500, ["ja", "en"], "en", False),
        ("rollback_false", 500, ["ja", "en"], "ja", True),
        ("exception", 503, ["ja", "en"], "ja", True),
    ],
)
async def test_ui_language_sync_reports_durable_outcome(
    monkeypatch,
    mode,
    status,
    saved,
    ui_language,
    partial,
):
    _allow_ui_language_mutation(monkeypatch)
    save_calls = []

    async def save_language(language):
        save_calls.append(language)
        if len(save_calls) > 1 and mode == "rollback_false":
            return False
        if len(save_calls) > 1 and mode == "exception":
            raise OSError("disk unavailable")
        return True

    async def apply_language(_name, language):
        if mode == "partial":
            return {
                "success": False,
                "partial_success": True,
                "language": language,
                "error": "recent context cleanup failed",
            }
        if mode == "exception":
            raise RuntimeError("memory server unavailable")
        return {"success": False, "error": "character sync failed"}

    _install_ui_language_runtime(
        monkeypatch,
        apply_language=apply_language,
        save_language=save_language,
    )
    result = await config_language_router.set_ui_language_api(_UiLanguageRequest())
    payload = _ui_payload(result)

    assert (getattr(result, "status_code", None)) == status
    assert save_calls == saved
    if mode == "partial":
        assert payload["success"] is True
        assert payload["partial_success"] is True
        assert payload["conversation_sync"]["language"] == "ja"
    else:
        assert payload["success"] is False
        assert payload["ui_language"] == ui_language
        assert payload["ui_language_rollback_succeeded"] is (not partial)
        assert payload.get("partial_persistence", False) is partial


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ui_language_sync_serializes_rollback_before_newer_write(monkeypatch):
    _allow_ui_language_mutation(monkeypatch)
    current = {"value": "en"}
    saved = []
    first_sync_started = asyncio.Event()
    release_first_sync = asyncio.Event()

    async def load_language():
        return current["value"]

    async def save_language(language):
        saved.append(language)
        current["value"] = language
        return True

    async def apply_language(_name, language):
        if language == "ja":
            first_sync_started.set()
            await release_first_sync.wait()
            return {"success": False, "error": "sync failed"}
        return {"success": True, "language": language}

    _install_ui_language_runtime(
        monkeypatch,
        apply_language=apply_language,
        save_language=save_language,
    )
    monkeypatch.setattr(config_language_router, "aload_ui_language_override", load_language)

    first = asyncio.create_task(
        config_language_router.set_ui_language_api(_UiLanguageRequest("ja"))
    )
    await asyncio.wait_for(first_sync_started.wait(), timeout=1)
    second = asyncio.create_task(
        config_language_router.set_ui_language_api(_UiLanguageRequest("ko"))
    )
    await asyncio.sleep(0)
    assert saved == ["ja"]
    assert not second.done()

    release_first_sync.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert getattr(first_result, "status_code", None) == 500
    assert second_result["language"] == "ko"
    assert saved == ["ja", "en", "ko"]
    assert current["value"] == "ko"
