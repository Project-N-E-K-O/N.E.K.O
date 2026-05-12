from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.llm_prompts import _compact_prompt_value, build_concept_explain_messages
from plugin.plugins.study_companion.mode_manager import (
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    ModeManager,
    build_transition_phrase,
    handle_user_intent,
    mode_label,
    normalize_mode,
)
from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply, build_config
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.study_ocr_pipeline import StudyCaptureProfile, StudyOcrPipeline
from plugin.plugins.study_companion import service as study_service
from plugin.plugins.study_companion.screen_classifier import classify_screen_from_ocr
from plugin.plugins.study_companion.service import _available_tesseract_languages
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent, _JSONCorrector
from plugin.plugins.study_companion.ui_api import build_open_ui_payload
from plugin.server.application.plugins.ui_query_service import _build_surfaces_sync
from plugin.sdk.plugin import Ok


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "study_companion"
    metadata = {}
    bus = None
    run_id = ""

    def __init__(self, plugin_dir: Path, config: dict[str, object]) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text("[plugin]\nid='study_companion'\n", encoding="utf-8")
        self._config = config
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self.status_updates: list[dict[str, object]] = []
        self.run_updates: list[dict[str, object]] = []
        self.pushed_messages: list[dict[str, object]] = []

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        self._config = {**self._config, **dict(updates or {})}
        return {"config": self._config}

    async def query_plugins(self, filters, timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        return {}

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        self.run_updates.append(dict(kwargs))
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}

    def update_status(self, status):
        self.status_updates.append(dict(status))


class _FakeOcrBackend:
    def __init__(self, result):
        self.result = result

    def extract_text(self, image):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeCaptureBackend:
    def __init__(self, image):
        self.image = image
        self.calls: list[tuple[object, object]] = []

    def capture_frame(self, target, profile):
        self.calls.append((target, profile))
        return self.image


class _FakeStudyOcrPipeline:
    def __init__(self, snapshot: OcrSnapshot) -> None:
        self.snapshot = snapshot

    def capture_snapshot(self) -> OcrSnapshot:
        return self.snapshot


class _FakeTutorAgent:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, dict[str, object], str]] = []
        self.evaluations: list[tuple[str, str, str, dict[str, object], str]] = []

    def update_config(self, config: StudyConfig) -> None:
        self._config = config

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.inputs.append((text, dict(context or {}), mode))
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=f"explained[{mode}]: {text}",
            created_at="2026-05-11T00:00:00Z",
        )

    async def answer_evaluate(
        self,
        *,
        question: str = "",
        answer: str = "",
        expected_answer: str = "",
        mode: str = MODE_COMPANION,
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.evaluations.append((question, answer, expected_answer, dict(context or {}), mode))
        return TutorReply(
            operation="answer_evaluate",
            input_text=answer,
            reply="evaluated",
            payload={
                "verdict": "partial",
                "score": 50,
                "error_type": "unknown",
                "feedback": "evaluated",
                "next_action": "review",
            },
            created_at="2026-05-11T00:00:00Z",
        )

    async def shutdown(self) -> None:
        return None


def test_study_store_round_trip_and_export(tmp_path: Path) -> None:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    config = StudyConfig(language="en", history_limit=2)
    state = build_initial_state(mode=config.mode)
    state.last_ocr_text = "photosynthesis"

    store.save_config(config)
    store.save_state(state)
    store.append_interaction(kind="concept_explain", input_text="a", output_text="b", history_limit=2)
    store.append_interaction(kind="concept_explain", input_text="c", output_text="d", history_limit=2)
    store.append_interaction(kind="concept_explain", input_text="e", output_text="f", history_limit=2)

    assert store.load_config(StudyConfig()).language == "en"
    assert store.load_state(build_initial_state()).last_ocr_text == "photosynthesis"
    assert [item["input_text"] for item in store.list_interactions(limit=10)] == ["e", "c"]
    exported = store.export_json()
    assert exported["config"]["language"] == "en"
    store.close()


def test_build_tutor_payload_preserves_structured_summary() -> None:
    reply = TutorReply(
        operation="summarize_session",
        input_text="session",
        reply="## Summary\n\nThe learner reviewed photosynthesis.",
        payload={
            "summary": "The learner reviewed photosynthesis.",
            "markdown": "## Summary\n\nThe learner reviewed photosynthesis.",
            "highlights": ["Generated one question"],
        },
        created_at="2026-05-11T00:00:00Z",
    )

    payload = study_service.build_tutor_payload(reply)

    assert payload["summary"] == "The learner reviewed photosynthesis."
    assert payload["markdown"] == "## Summary\n\nThe learner reviewed photosynthesis."
    assert payload["reply"] == "## Summary\n\nThe learner reviewed photosynthesis."


def test_study_mode_manager_intent_switch_rules() -> None:
    assert normalize_mode("concept_explain") == MODE_COMPANION
    assert "already in" in build_transition_phrase(MODE_COMPANION, language="en-GB", outcome="same")
    assert "already in" in build_transition_phrase(MODE_COMPANION, language="eng", outcome="same")
    pure = handle_user_intent("教我")
    assert pure["mode"] == MODE_TEACHING
    assert pure["pure_switch"] is True

    short_with_text = handle_user_intent("教我微分")
    assert short_with_text["mode"] == MODE_TEACHING
    assert short_with_text["pure_switch"] is False
    assert short_with_text["remaining_text"] == "微分"

    explained = handle_user_intent("解释光合作用")
    assert explained["kind"] == "concept_explain"
    assert explained["mode"] == "concept_explain"
    assert explained["remaining_text"] == "光合作用"

    with_text = handle_user_intent("教我光合作用")
    assert with_text["mode"] == MODE_TEACHING
    assert with_text["pure_switch"] is False
    assert with_text["remaining_text"] == "光合作用"

    english = handle_user_intent(r"\teaching mode photosynthesis", language="en")
    assert english["mode"] == MODE_TEACHING
    assert english["keyword"] == "teaching mode"
    assert english["remaining_text"] == "photosynthesis"

    cross_mode = handle_user_intent("教我互动模式 光合作用")
    assert cross_mode["mode"] == MODE_INTERACTIVE
    assert cross_mode["keyword"] == "互动模式"
    assert mode_label(MODE_TEACHING, language="ja") == "指導"
    assert mode_label(MODE_TEACHING, language="pt-BR") == "Ensino"
    assert "教学" not in build_transition_phrase(MODE_TEACHING, language="ja", outcome="changed")

    manager = ModeManager(current_mode=MODE_COMPANION)
    first = manager.switch_to(MODE_INTERACTIVE, "unit", now=1000.0)
    assert first["changed"] is True
    same = manager.switch_to(MODE_INTERACTIVE, "unit", now=1010.0)
    assert same["changed"] is False
    assert same["new_mode"] == MODE_INTERACTIVE
    dwell = manager.switch_to(MODE_TEACHING, "unit", now=1010.0)
    assert dwell["changed"] is False
    assert dwell["lock_reason"] == "minimum_dwell"
    rate_limited = manager.switch_to(MODE_COMPANION, "unit", now=1020.0)
    assert rate_limited["changed"] is False
    assert rate_limited["lock_reason"] == "mode_lock"
    assert rate_limited["new_mode"] == MODE_INTERACTIVE
    assert rate_limited["lock_until"] > 1020.0

    manager = ModeManager(current_mode=MODE_COMPANION)
    assert manager.switch_to(MODE_INTERACTIVE, "unit", now=1000.0)["changed"] is True
    manager.mode_started_at = 0.0
    assert manager.switch_to(MODE_TEACHING, "unit", now=1010.0)["changed"] is True
    manager.mode_started_at = 0.0
    locked = manager.switch_to(MODE_COMPANION, "unit", now=1020.0)
    assert locked["changed"] is True
    assert locked["lock_until"] > 1020.0
    blocked = manager.switch_to(MODE_INTERACTIVE, "unit", now=1030.0)
    assert blocked["changed"] is False
    assert blocked["lock_reason"] == "mode_lock"


def test_study_config_and_state_legacy_mode_migration(tmp_path: Path) -> None:
    legacy = build_config({"study": {"default_mode": "concept_explain"}})
    assert legacy.mode == MODE_COMPANION
    assert legacy.default_mode == MODE_COMPANION

    llm_timeout = build_config({"llm": {"call_timeout_seconds": 42}})
    assert llm_timeout.llm_call_timeout_seconds == 42
    llm_section_legacy_timeout = build_config({"llm": {"llm_call_timeout_seconds": 84}})
    assert llm_section_legacy_timeout.llm_call_timeout_seconds == 84

    interactive = build_config({"study": {"default_mode": MODE_INTERACTIVE}})
    assert interactive.mode == MODE_INTERACTIVE
    assert interactive.default_mode == MODE_INTERACTIVE

    invalid = build_config({"study": {"default_mode": "not_a_mode"}})
    assert invalid.mode == MODE_COMPANION
    assert invalid.default_mode == MODE_COMPANION

    direct = StudyConfig(mode="not_a_mode", default_mode="invalid", history_limit=0, llm_temperature=9.0, llm_max_tokens=0)
    assert direct.mode == MODE_COMPANION
    assert direct.default_mode == MODE_COMPANION
    assert direct.history_limit == 1
    assert direct.llm_temperature == 2.0
    assert direct.llm_max_tokens == 1

    generic_llm = build_config({"llm": {"temperature": 0.6, "max_tokens": 333}})
    assert generic_llm.llm_limits_for_operation("unsupported") == (0.6, 333)
    assert generic_llm.llm_limits_for_operation("question_generate") == (0.6, 333)

    specific_llm = build_config({"llm": {"temperature": 0.6, "max_tokens": 333, "temperature_question_generate": 0.3, "max_tokens_question_generate": 444}})
    assert specific_llm.llm_limits_for_operation("question_generate") == (0.3, 444)

    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    try:
        store.set_raw("state", {"status": "ready", "active_mode": "concept_explain", "last_ocr_text": "legacy"})
        loaded = store.load_state(build_initial_state())
        assert loaded.active_mode == MODE_COMPANION
        assert loaded.last_ocr_text == "legacy"
        assert loaded.recent_mode_switches == []
        assert loaded.suggestion_cooldowns == {}
        assert loaded.session_suggestions == []
    finally:
        store.close()


def test_study_screen_classifier_routes_summary_keywords_to_summary() -> None:
    english = classify_screen_from_ocr("## Summary\n\nThe learner reviewed photosynthesis.")
    assert english.screen_type == "summary"
    assert "summary" in english.signals.get("summary_hits", [])

    chinese = classify_screen_from_ocr("本节总结\n光合作用的主要过程包括光反应和暗反应。")
    assert chinese.screen_type == "summary"
    assert "总结" in chinese.signals.get("summary_hits", [])


def test_compact_prompt_value_stops_at_max_depth() -> None:
    nested = {"a": {"b": {"c": {"d": "bottom"}}}}

    compacted = _compact_prompt_value(nested, list_limit=5, string_limit=50, max_depth=2)

    assert compacted == {"a": {"b": "...[max depth reached]"}}


def test_study_open_ui_payload_returns_message_key() -> None:
    payload = build_open_ui_payload(plugin_id="study_companion", available=True)
    assert payload["available"] is True
    assert payload["path"] == "/plugin/study_companion/ui/"
    assert payload["message_key"] == "ui.open.available"
    assert "message" not in payload


def test_study_companion_i18n_bundles_are_present() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    locales = ["zh-CN", "en", "ja", "ko", "ru", "zh-TW", "es", "pt"]
    phase3_keys = [
        "ui.label.screen",
        "ui.label.question",
        "ui.label.answer",
        "ui.label.classification",
        "ui.label.history",
        "ui.button.generate_question",
        "ui.button.evaluate_answer",
        "ui.button.summarize_session",
        "ui.status.generating_question",
        "ui.status.evaluating_answer",
        "ui.status.summarizing_session",
        "ui.status.screen.idle",
        "ui.status.screen.reading",
        "ui.status.screen.question",
        "ui.status.screen.answering",
        "ui.status.screen.review",
        "ui.status.screen.notes",
        "ui.status.screen.summary",
        "ui.error.missing_question",
        "ui.error.missing_answer",
    ]
    bundles: dict[str, dict[str, str]] = {}
    for locale in locales:
        bundle_path = plugin_dir / "i18n" / f"{locale}.json"
        assert bundle_path.is_file()
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundles[locale] = bundle
        assert "plugin.name" in bundle
        assert "ui.title" in bundle
        assert "ui.surface.study_panel" in bundle
        assert "ui.button.explain" in bundle
        assert "status.mode.companion" in bundle
        assert "status.mode.interactive" in bundle
        assert "status.mode.teaching" in bundle
        assert "ui.status.mode_switching" in bundle
        assert "ui.error.mode_switch_failed" in bundle

    en_bundle = json.loads((plugin_dir / "i18n" / "en.json").read_text(encoding="utf-8"))
    for locale in locales:
        assert set(bundles[locale]) == set(en_bundle)
    for locale in [item for item in locales if item != "en"]:
        bundle = bundles[locale]
        assert any(bundle[key] != en_bundle[key] for key in phase3_keys)
    assert bundles["ja"]["entries.open_ui.name"] != en_bundle["entries.open_ui.name"]
    assert bundles["ja"]["entries.download_rapidocr_models.description"] != en_bundle["entries.download_rapidocr_models.description"]

    with (plugin_dir / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)
    plugin_ui = normalize_plugin_ui_manifest(config, plugin_id="study_companion")
    assert plugin_ui is not None
    meta = {
        "id": "study_companion",
        "config_path": str(plugin_dir / "plugin.toml"),
        "plugin_ui": plugin_ui,
        "i18n": config["plugin"]["i18n"],
    }
    surfaces, warnings = _build_surfaces_sync("study_companion", meta)
    assert warnings == []
    assert any(surface["id"] == "study-panel" and surface["available"] is True for surface in surfaces)

    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    assert "./i18n.js" in index_html
    assert "data-i18n=\"ui.title\"" in index_html
    assert "I18n.init" in main_js


def test_study_companion_static_ui_smoke_with_mocked_runs() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if shutil.which("node") is None:
        pytest.skip("node is not installed; frontend/plugin-manager happy-dom smoke test requires node")
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const mainJs = fs.readFileSync(path.join(staticDir, 'main.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(i18nDir, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();

const runEntries = new Map();
let activeMode = 'companion';
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = body.entry_id === 'study_explain_text'
      ? 'run-explain'
      : body.entry_id === 'study_set_mode'
        ? 'run-mode'
        : 'run-status';
    runEntries.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (url === '/runs/run-status') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-mode') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-explain') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-status/export') {
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { status: 'ready', active_mode: activeMode } } }],
    });
  }
  if (url === '/runs/run-mode/export') {
    const run = runEntries.get('run-mode') || {};
    activeMode = run.args.mode || activeMode;
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data: {
            changed: true,
            old_mode: 'companion',
            new_mode: activeMode,
            transition_phrase: `${activeMode} mode enabled`,
            reply: `${activeMode} mode enabled`,
          },
        },
      }],
    });
  }
  if (url === '/runs/run-explain/export') {
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { reply: 'A derivative is slope at one point.', degraded: false } } }],
    });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(mainJs);

async function waitFor(predicate, label) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

  await waitFor(() => document.getElementById('statusLine').textContent.includes('Ready'), 'ready status');
if (document.title !== 'Study Companion') {
  throw new Error(`unexpected title: ${document.title}`);
}

document.getElementById('modeInteractiveBtn').click();
await waitFor(() => document.getElementById('statusLine').textContent.includes('Interactive'), 'interactive mode');
if (!runEntries.get('run-mode') || runEntries.get('run-mode').args.mode !== 'interactive') {
  throw new Error(`mode run args mismatch: ${JSON.stringify(runEntries.get('run-mode'))}`);
}

document.getElementById('studyInput').value = 'Explain derivative';
document.getElementById('explainBtn').click();
await waitFor(() => document.getElementById('replyText').textContent === 'A derivative is slope at one point.', 'explain reply');

const explainRun = runEntries.get('run-explain');
if (!explainRun || explainRun.args.text !== 'Explain derivative') {
  throw new Error(`explain run args mismatch: ${JSON.stringify(explainRun)}`);
}
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_STATIC_DIR": str(plugin_dir / "static"),
        "STUDY_COMPANION_I18N_DIR": str(plugin_dir / "i18n"),
    }
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_companion_hosted_panel_uses_long_running_entry_poll_budget() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    assert "ENTRY_TIMEOUT_MS" in source
    assert "study_set_mode: 15000" in source
    assert "study_explain_text: 60000" in source
    assert "const deadline = Date.now() + timeoutForEntry(entryId);" in source
    assert "for (let i = 0; i < 40; i += 1)" not in source
    assert "async function refresh(signal?: AbortSignal, options: { updateReply?: boolean } = {})" in source
    assert "await refresh(controller.signal, { updateReply: false });" in source
    assert "const appliedMode = String(" in source
    assert "setStatus((prev) => ({" in source
    assert "active_mode: appliedMode," in source
    assert "mode: appliedMode," in source
    assert "study-panel__modes" in source
    assert "study_set_mode" in source
    assert "status.mode.companion" in source


def test_study_companion_ui_export_failures_are_not_silent_successes() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted_source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    assert "RUN_EXPORT_RETRY_COUNT = 3" in hosted_source
    assert "throw new Error(`Run export failed: HTTP ${lastStatus}`);" in hosted_source
    assert "const exported = exportResp.ok ? await exportResp.json() : {};" not in hosted_source
    assert "return item?.json?.data || {};" not in hosted_source
    assert "study_set_mode" in hosted_source

    assert "RUN_EXPORT_RETRY_COUNT = 3" in static_source
    assert "throw new Error(tf('ui.error.run_export_failed'" in static_source
    assert "if (!response.ok) {\n    return {};" not in static_source
    assert "callPlugin('study_set_mode'" in static_source


def test_study_companion_static_mode_switch_uses_applied_mode() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    set_mode_start = static_source.index("async function setMode(mode)")
    set_mode_end = static_source.index("function bindButton", set_mode_start)
    set_mode = static_source[set_mode_start:set_mode_end]

    assert "async function setMode(mode)" in set_mode
    assert "if (mode === currentMode)" in set_mode
    assert "callPlugin('study_set_mode'" in set_mode
    assert "data.new_mode" in set_mode
    assert "data && data.changed === false" in set_mode
    assert "setModeButtons(currentMode, false)" in set_mode
    assert "answerInput.value = data.answer;" in static_source
    assert "answerInput.value = '';" not in static_source


def test_study_companion_static_panel_keeps_mode_highlight_when_status_refresh_fails() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const html = `<!doctype html><html><head><title>Study Companion</title></head><body>
  <div id="statusLine"></div>
  <div id="replyText"></div>
  <textarea id="studyInput"></textarea>
  <button id="refreshBtn"></button>
  <button id="ocrBtn"></button>
  <button id="explainBtn"></button>
  <button id="modeCompanionBtn" data-mode="companion"></button>
  <button id="modeInteractiveBtn" data-mode="interactive"></button>
  <button id="modeTeachingBtn" data-mode="teaching"></button>
</body></html>`;

const i18nJs = fs.readFileSync(process.env.STUDY_COMPANION_I18N_JS, 'utf8');
const mainJs = fs.readFileSync(process.env.STUDY_COMPANION_STATIC_JS, 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(process.env.STUDY_COMPANION_I18N_DIR, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();

const runEntries = new Map();
let activeMode = 'companion';
let failStatusExport = false;
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = body.entry_id === 'study_set_mode'
      ? 'run-mode'
      : 'run-status';
    runEntries.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (url === '/runs/run-status') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-mode') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-status/export') {
    if (failStatusExport) {
      return new Response('boom', { status: 500 });
    }
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { status: 'ready', active_mode: activeMode } } }],
    });
  }
  if (url === '/runs/run-mode/export') {
    const run = runEntries.get('run-mode') || {};
    activeMode = run.args.mode || activeMode;
    return Response.json({
      items: [{
        type: 'json',
        json: {
          success: true,
          data: {
            changed: true,
            old_mode: 'companion',
            new_mode: activeMode,
            transition_phrase: `${activeMode} mode enabled`,
            reply: `${activeMode} mode enabled`,
          },
        },
      }],
    });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(mainJs);

async function waitFor(predicate, label) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

await waitFor(() => document.getElementById('statusLine').textContent.includes('Ready'), 'ready status');

failStatusExport = true;
document.getElementById('modeTeachingBtn').click();
await waitFor(() => document.getElementById('statusLine').textContent.includes('Error'), 'status error');

const teachingButton = document.querySelector('[data-mode="teaching"]');
if (!teachingButton || teachingButton.getAttribute('aria-pressed') !== 'true') {
  throw new Error(`teaching mode not highlighted: ${teachingButton && teachingButton.outerHTML}`);
}
if (document.querySelector('[data-mode="interactive"]').getAttribute('aria-pressed') !== 'false') {
  throw new Error('interactive mode still highlighted after failed refresh');
}
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_STATIC_JS": str(plugin_dir / "static" / "main.js"),
        "STUDY_COMPANION_I18N_JS": str(plugin_dir / "static" / "i18n.js"),
        "STUDY_COMPANION_I18N_DIR": str(plugin_dir / "i18n"),
    }
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_companion_i18n_prefers_traditional_chinese_bundle() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.env.STUDY_COMPANION_I18N_JS, 'utf8');

globalThis.window = globalThis;
globalThis.document = { documentElement: { lang: '' } };
globalThis.location = { search: '?locale=zh-TW', pathname: '/plugin/study_companion/ui/' };
Object.defineProperty(globalThis, 'navigator', {
  value: { languages: ['zh-TW', 'zh-CN'], language: 'zh-TW' },
  configurable: true,
});
globalThis.console = console;

let bundleRequests = [];
globalThis.fetch = async (url) => {
  const href = String(url);
  if (href.includes('/ui-api/i18n/')) {
    bundleRequests.push(href);
  }
  if (href.endsWith('/zh-TW.json')) {
    return { ok: true, json: async () => ({ 'ui.title': '繁體中文' }) };
  }
  if (href.endsWith('/zh-CN.json')) {
    return { ok: true, json: async () => ({ 'ui.title': '简体中文' }) };
  }
  return { ok: false, json: async () => ({}) };
};

eval(source);

(async () => {
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected lang: ${window.I18n.lang()}`);
  }
  if (document.documentElement.lang !== 'zh-TW') {
    throw new Error(`unexpected document lang: ${document.documentElement.lang}`);
  }
  if (window.I18n.t('ui.title', 'fallback') !== '繁體中文') {
    throw new Error(`unexpected bundle text: ${window.I18n.t('ui.title', 'fallback')}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected query locale request order: ${JSON.stringify(bundleRequests)}`);
  }

  bundleRequests = [];
  window.I18n._bundle = {};
  window.I18n.setLang('zh-CN');
  location.search = '';
  navigator.languages = ['zh-TW', 'zh-CN'];
  navigator.language = 'zh-TW';
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected browser lang: ${window.I18n.lang()}`);
  }
  if (window.I18n.t('ui.title', 'fallback') !== '繁體中文') {
    throw new Error(`unexpected browser bundle text: ${window.I18n.t('ui.title', 'fallback')}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected browser locale request order: ${JSON.stringify(bundleRequests)}`);
  }

  bundleRequests = [];
  window.I18n._bundle = {};
  window.I18n.setLang('zh-CN');
  location.search = '?locale=zh-Hant-HK';
  navigator.languages = ['zh-Hant-HK', 'zh-CN'];
  navigator.language = 'zh-Hant-HK';
  await window.I18n.init('study_companion');
  if (window.I18n.lang() !== 'zh-TW') {
    throw new Error(`unexpected hant lang: ${window.I18n.lang()}`);
  }
  if (!bundleRequests[0] || !bundleRequests[0].endsWith('/zh-TW.json')) {
    throw new Error(`unexpected hant locale request order: ${JSON.stringify(bundleRequests)}`);
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    env = {
        **os.environ,
        "STUDY_COMPANION_I18N_JS": str(plugin_dir / "static" / "i18n.js"),
    }
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=plugin_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_ocr_pipeline_uses_local_capture_profile() -> None:
    capture = _FakeCaptureBackend(image=object())
    ocr = _FakeOcrBackend("captured text")
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(
            ocr_left_inset_ratio=0.11,
            ocr_right_inset_ratio=0.12,
            ocr_top_ratio=0.13,
            ocr_bottom_inset_ratio=0.14,
        ),
        ocr_backend=ocr,
        capture_backend=capture,
    )

    snapshot = pipeline.capture_snapshot(target=object())

    assert snapshot.status == "ok"
    assert snapshot.text == "captured text"
    assert len(capture.calls) == 1
    profile = capture.calls[0][1]
    assert isinstance(profile, StudyCaptureProfile)
    assert profile.left_inset_ratio == 0.11
    assert profile.right_inset_ratio == 0.12
    assert profile.top_ratio == 0.13
    assert profile.bottom_inset_ratio == 0.14


def test_study_companion_does_not_import_galgame_ocr_reader_directly() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    for path in plugin_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "plugin.plugins.galgame_plugin.ocr_reader" not in source


def test_ocr_pipeline_handles_empty_text_repeats_and_errors() -> None:
    cfg = StudyConfig()
    empty = StudyOcrPipeline(logger=_Logger(), config=cfg, ocr_backend=_FakeOcrBackend(""))
    assert empty.snapshot_from_image(object()).status == "empty"
    assert empty.snapshot_from_image(None).diagnostic == "no image supplied"

    disabled = StudyOcrPipeline(logger=_Logger(), config=StudyConfig(ocr_enabled=False))
    disabled_snapshot = disabled.capture_snapshot()
    assert disabled_snapshot.status == "disabled"

    repeated = StudyOcrPipeline(
        logger=_Logger(),
        config=cfg,
        ocr_backend=_FakeOcrBackend(["Alpha", "Alpha", "Beta"]),
    )
    snapshot = repeated.snapshot_from_image(object())
    assert snapshot.status == "ok"
    assert snapshot.text == "Alpha Alpha Beta"

    broken = StudyOcrPipeline(
        logger=_Logger(),
        config=cfg,
        ocr_backend=_FakeOcrBackend(RuntimeError("ocr boom")),
    )
    failed = broken.snapshot_from_image(object())
    assert failed.status == "ocr_failed"
    assert "ocr boom" in failed.diagnostic


def test_ocr_pipeline_reports_fullscreen_capture_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _capture_boom():
        raise RuntimeError("capture boom")

    monkeypatch.setattr(StudyOcrPipeline, "_capture_fullscreen", staticmethod(_capture_boom))
    pipeline = StudyOcrPipeline(logger=_Logger(), config=StudyConfig())

    snapshot = pipeline.capture_snapshot()

    assert snapshot.status == "capture_failed"
    assert "capture boom" in snapshot.diagnostic


def test_available_tesseract_languages_logs_timeout_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    detected = tmp_path / "tesseract.exe"
    target_dir = tmp_path / "install"
    tessdata = target_dir / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_text("stub", encoding="utf-8")

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=[str(detected), "--list-langs"], timeout=5.0)

    monkeypatch.setattr(subprocess, "run", _timeout)
    with caplog.at_level("WARNING", logger=study_service._LOGGER.name):
        languages = _available_tesseract_languages(detected, target_dir)

    assert languages == {"eng"}
    assert any("timed out" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_study_ocr_snapshot_preserves_last_text_when_capture_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        with plugin._lock:
            plugin._state.last_ocr_text = "photosynthesis"
            plugin._state.last_ocr_at = "2026-05-10T00:00:00Z"
        plugin._ocr_pipeline = _FakeStudyOcrPipeline(
            OcrSnapshot(
                status="capture_failed",
                captured_at="2026-05-11T00:00:00Z",
                diagnostic="capture boom",
            )
        )
        plugin._agent = _FakeTutorAgent()

        snapshot_result = await plugin.study_ocr_snapshot()
        assert isinstance(snapshot_result, Ok)
        assert snapshot_result.value["status"] == "capture_failed"
        assert snapshot_result.value["text"] == ""

        with plugin._lock:
            assert plugin._state.last_ocr_text == "photosynthesis"
            assert plugin._state.last_ocr_at == "2026-05-10T00:00:00Z"

        stored_state = plugin._store.load_state(build_initial_state())
        assert stored_state.last_ocr_text == "photosynthesis"

        explain_result = await plugin.study_explain_text()
        assert isinstance(explain_result, Ok)
        assert explain_result.value["input_text"] == "photosynthesis"
        assert plugin._agent.inputs[0][0] == "photosynthesis"
        assert plugin._agent.inputs[0][2] == MODE_COMPANION
        assert plugin._agent.inputs[0][1]["source"] == "ocr_snapshot"
        assert plugin._agent.inputs[0][1]["mode"] == MODE_COMPANION
        assert plugin._agent.inputs[0][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[0][1]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_detects_mode_intent_and_continues_when_content_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        pure = await plugin.study_explain_text("教我")
        assert isinstance(pure, Ok)
        assert pure.value["new_mode"] == MODE_TEACHING
        assert pure.value["reply"]
        assert "教学模式" in pure.value["reply"]
        assert plugin._cfg.mode == MODE_TEACHING
        assert plugin._cfg.default_mode == MODE_COMPANION
        assert plugin._store.load_config(StudyConfig()).default_mode == MODE_COMPANION

        explain_only = await plugin.study_explain_text("解释光合作用")
        assert isinstance(explain_only, Ok)
        assert explain_only.value["intent"]["kind"] == "concept_explain"
        assert "mode_switch" not in explain_only.value
        assert explain_only.value["reply"] == "explained[teaching]: 光合作用"

        with plugin._lock:
            plugin._state.last_ocr_text = "细胞呼吸"
            plugin._state.last_ocr_at = "2026-05-12T00:00:00Z"
        explain_latest_ocr = await plugin.study_explain_text("解释一下")
        assert isinstance(explain_latest_ocr, Ok)
        assert explain_latest_ocr.value["intent"]["kind"] == "concept_explain"
        assert explain_latest_ocr.value["input_text"] == "细胞呼吸"
        assert explain_latest_ocr.value["reply"] == "explained[teaching]: 细胞呼吸"
        assert plugin._agent.inputs[-1][0] == "细胞呼吸"
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "ocr_snapshot"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]

        with plugin._lock:
            plugin._state.active_mode = MODE_COMPANION
            plugin._state.mode_started_at = 0.0
            plugin._state.mode_lock_until = 0.0
            plugin._state.recent_mode_switches = []
            plugin._cfg.mode = MODE_COMPANION
            plugin._cfg.default_mode = MODE_COMPANION
        plugin._mode_manager.restore(
            {
                "current_mode": MODE_COMPANION,
                "mode_started_at": 0.0,
                "recent_mode_switches": [],
                "suggestion_cooldowns": {},
                "session_suggestions": [],
                "mode_lock_until": 0.0,
            }
        )

        explained = await plugin.study_explain_text("教我光合作用")
        assert isinstance(explained, Ok)
        assert explained.value["intent"]["mode"] == MODE_TEACHING
        assert explained.value["mode_switch"]["changed"] is True
        assert explained.value["reply"] == f'explained[teaching]: {explained.value["input_text"]}'
        assert plugin._agent.inputs[-1][0] == explained.value["input_text"]
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is True
        assert "screen_classification" in plugin._agent.inputs[-1][1]

        short_explained = await plugin.study_explain_text("??????")
        assert isinstance(short_explained, Ok)
        assert short_explained.value.get("intent", {}).get("pure_switch", False) is False
        assert short_explained.value["reply"] == f'explained[teaching]: {short_explained.value["input_text"]}'
        assert plugin._agent.inputs[-1][0] == short_explained.value["input_text"]
        assert plugin._agent.inputs[-1][2] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_TEACHING
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_explain_intent_without_content_keeps_empty_input_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)

    try:
        with plugin._lock:
            plugin._state.last_ocr_text = ""
        explain_empty = await plugin.study_explain_text("explain")
        assert isinstance(explain_empty, Ok)
        assert explain_empty.value["input_text"] == ""
        assert explain_empty.value["diagnostic"] == "empty_input"
        assert explain_empty.value["intent"]["kind"] == "concept_explain"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_explain_text_continues_when_mode_switch_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "zh-CN", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    plugin._agent = _FakeTutorAgent()

    try:
        lock_until = time.time() + 300.0
        with plugin._lock:
            plugin._state.active_mode = MODE_COMPANION
            plugin._state.mode_started_at = 0.0
            plugin._state.mode_lock_until = lock_until
            plugin._state.recent_mode_switches = []
            plugin._cfg.mode = MODE_COMPANION
            plugin._cfg.default_mode = MODE_COMPANION
        plugin._mode_manager.restore(
            {
                "current_mode": MODE_COMPANION,
                "mode_started_at": 0.0,
                "recent_mode_switches": [],
                "suggestion_cooldowns": {},
                "session_suggestions": [],
                "mode_lock_until": lock_until,
            }
        )

        explained = await plugin.study_explain_text("教我光合作用")
        assert isinstance(explained, Ok)
        assert explained.value["intent"]["mode"] == MODE_TEACHING
        assert explained.value["mode_switch"]["changed"] is False
        assert explained.value["mode_switch"]["locked"] is True
        assert plugin._agent.inputs[-1][0] == "光合作用"
        assert plugin._agent.inputs[-1][2] == MODE_COMPANION
        assert plugin._agent.inputs[-1][1]["source"] == "manual"
        assert plugin._agent.inputs[-1][1]["mode"] == MODE_COMPANION
        assert plugin._agent.inputs[-1][1]["mode_switch"] is False
        assert "screen_classification" in plugin._agent.inputs[-1][1]
        assert explained.value["reply"].startswith("explained[companion]: 光合作用")
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_evaluate_answer_does_not_reuse_old_expected_answer_for_custom_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    fake_agent = _FakeTutorAgent()
    plugin._agent = fake_agent

    try:
        with plugin._lock:
            plugin._state.current_question = {
                "question": "What process converts light to chemical energy?",
                "answer": "Photosynthesis",
            }

        evaluated = await plugin.study_evaluate_answer(
            question="What organelle stores genetic material?",
            answer="The nucleus.",
        )

        assert isinstance(evaluated, Ok)
        assert fake_agent.evaluations[-1][0] == "What organelle stores genetic material?"
        assert fake_agent.evaluations[-1][2] == ""
        assert fake_agent.evaluations[-1][3]["expected_answer"] == ""
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_tutor_agent_prompt_and_reply_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = build_concept_explain_messages(
        text="A derivative measures instantaneous rate of change.",
        language="en",
        mode=MODE_INTERACTIVE,
        context={"source": "unit-test", "mode": MODE_INTERACTIVE},
    )
    assert messages[0]["role"] == "system"
    assert "unit-test" in messages[1]["content"]
    assert "Mode: interactive" in messages[1]["content"]

    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages):
        return "A derivative is the slope at one point."

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await agent.concept_explain("derivative", mode=MODE_INTERACTIVE)

    assert reply.operation == "concept_explain"
    assert reply.reply == "A derivative is the slope at one point."
    assert reply.degraded is False


@pytest.mark.asyncio
async def test_tutor_agent_teaching_prefix_is_applied_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    teaching_prefix = build_transition_phrase(MODE_TEACHING, language="en", outcome="changed")

    async def _fake_call_model(_messages):
        return f"{teaching_prefix}\n\nA derivative is the slope at one point."

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await agent.concept_explain("derivative", mode=MODE_TEACHING)

    assert reply.operation == "concept_explain"
    assert reply.reply.count(teaching_prefix) == 1
    assert reply.reply.startswith(teaching_prefix)


@pytest.mark.asyncio
async def test_tutor_agent_handles_empty_and_model_failures() -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    empty = await agent.concept_explain(" ")
    assert empty.degraded is True
    assert empty.diagnostic == "empty_input"

    async def _broken_call_model(_messages):
        raise RuntimeError("llm unavailable")

    agent._call_model = _broken_call_model  # type: ignore[method-assign]
    fallback = await agent.concept_explain("photosynthesis converts light")

    assert fallback.degraded is True
    assert fallback.diagnostic == "llm_call_failed"
    assert "photosynthesis converts light" in fallback.reply

    zh_agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))
    zh_empty = await zh_agent.concept_explain(" ")
    assert zh_empty.diagnostic == "empty_input"
    assert "请先提供文本" in zh_empty.reply

    zh_agent._call_model = _broken_call_model  # type: ignore[method-assign]
    zh_fallback = await zh_agent.concept_explain("光合作用")
    assert zh_fallback.diagnostic == "llm_call_failed"
    assert "关键文本：光合作用" in zh_fallback.reply

    ja_agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="ja"))
    ja_empty = await ja_agent.concept_explain(" ")
    assert "テキスト" in ja_empty.reply

    ja_agent._call_model = _broken_call_model  # type: ignore[method-assign]
    ja_fallback = await ja_agent.concept_explain("微分")
    assert ja_fallback.diagnostic == "llm_call_failed"
    assert "重要なテキスト：微分" in ja_fallback.reply


def test_json_corrector_parses_plain_json() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object('{"answer": "42"}') == {"answer": "42"}


def test_json_corrector_parses_code_fenced_json() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object('```json\n{"question": "What is it?"}\n```') == {"question": "What is it?"}


def test_json_corrector_recovers_json_from_surrounding_text() -> None:
    corrector = _JSONCorrector(logger=_Logger())
    assert corrector.parse_json_object('noise before {"topic": "biology", "score": 90} noise after') == {
        "topic": "biology",
        "score": 90,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_name, kwargs, response_json, expected_field, expected_value",
    [
        (
            "question_generate",
            {"text": "Photosynthesis converts light to chemical energy.", "mode": MODE_INTERACTIVE, "context": {"screen_classification": {"screen_type": "reading"}}},
            {
                "question": "What process converts light to chemical energy?",
                "answer": "Photosynthesis",
                "hint": "Look for the named process.",
                "difficulty": 2,
                "topic": "biology",
                "screen_type": "reading",
            },
            "question",
            "What process converts light to chemical energy?",
        ),
        (
            "answer_evaluate",
            {
                "question": "What process converts light to chemical energy?",
                "answer": "It is photosynthesis.",
                "expected_answer": "Photosynthesis",
                "mode": MODE_COMPANION,
                "context": {"screen_classification": {"screen_type": "answering"}},
            },
            {
                "verdict": "correct",
                "score": 95,
                "error_type": "none",
                "feedback": "Correct.",
                "next_action": "Move on.",
                "screen_type": "answering",
            },
            "verdict",
            "correct",
        ),
        (
            "knowledge_track",
            {"mode": MODE_COMPANION, "context": {"screen_classification": {"screen_type": "review"}, "session_summary_seed": {"weak_points": ["definition"]}}},
            {
                "topic": "photosynthesis",
                "mastery_delta": 0.1,
                "confidence": 0.8,
                "weak_points": ["definition"],
                "next_steps": ["Review the definition"],
                "session_summary_seed": {"weak_points": ["definition"]},
                "screen_type": "review",
            },
            "topic",
            "photosynthesis",
        ),
        (
            "summarize_session",
            {
                "history": [{"kind": "question_generate", "output_text": "What process converts light to chemical energy?"}],
                "mode": MODE_TEACHING,
                "context": {"screen_classification": {"screen_type": "summary"}},
            },
            {
                "summary": "The learner reviewed photosynthesis.",
                "highlights": ["Generated one question"],
                "weak_points": ["definition"],
                "next_actions": ["Review the definition"],
                "markdown": "## Summary\n\nThe learner reviewed photosynthesis.",
                "screen_type": "summary",
            },
            "summary",
            "The learner reviewed photosynthesis.",
        ),
    ],
)
async def test_tutor_agent_structured_operations_normal_path(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    kwargs: dict[str, object],
    response_json: dict[str, object],
    expected_field: str,
    expected_value: object,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages, *, operation: str = "concept_explain"):
        assert operation == operation_name
        return json.dumps(response_json)

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await getattr(agent, operation_name)(**kwargs)

    assert reply.operation == operation_name
    assert reply.degraded is False
    assert reply.payload[expected_field] == expected_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_name, kwargs",
    [
        ("question_generate", {"text": "Photosynthesis converts light to chemical energy.", "context": {"screen_classification": {"screen_type": "reading"}}}),
        ("answer_evaluate", {"question": "What process converts light to chemical energy?", "answer": "It is photosynthesis.", "expected_answer": "Photosynthesis", "context": {"screen_classification": {"screen_type": "answering"}}}),
        ("knowledge_track", {"context": {"screen_classification": {"screen_type": "review"}}}),
        ("summarize_session", {"history": [{"kind": "question_generate", "output_text": "What process converts light to chemical energy?"}], "context": {"screen_classification": {"screen_type": "summary"}}}),
    ],
)
async def test_tutor_agent_structured_operations_degrade_with_generic_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    kwargs: dict[str, object],
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _broken_call_model(_messages, *, operation: str = "concept_explain"):
        raise RuntimeError("secret api endpoint https://example.invalid/v1 key=sk-123")

    monkeypatch.setattr(agent, "_call_model", _broken_call_model)
    reply = await getattr(agent, operation_name)(**kwargs)

    assert reply.operation == operation_name
    assert reply.degraded is True
    assert reply.diagnostic == "llm_call_failed"
    assert "secret api endpoint" not in reply.reply


@pytest.mark.asyncio
async def test_tutor_agent_llm_cache_distinguishes_rotated_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from utils import config_manager, llm_client

    class _ConfigManager:
        def __init__(self) -> None:
            self.api_key = "old-key"

        def get_model_api_config(self, _group: str):
            return {
                "base_url": "https://llm.example.test/v1",
                "model": "study-model",
                "api_key": self.api_key,
            }

    class _FakeLLM:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        async def ainvoke(self, _messages):
            return SimpleNamespace(content=f"reply from {self.api_key}")

    cfg_mgr = _ConfigManager()
    created_keys: list[str] = []

    def _create_chat_llm(*, api_key: str, **_kwargs):
        created_keys.append(api_key)
        return _FakeLLM(api_key)

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: cfg_mgr)
    monkeypatch.setattr(llm_client, "create_chat_llm", _create_chat_llm)

    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    first = await agent._call_model([{"role": "user", "content": "one"}])
    cfg_mgr.api_key = "new-key"
    second = await agent._call_model([{"role": "user", "content": "two"}])

    assert first == "reply from old-key"
    assert second == "reply from new-key"
    assert created_keys == ["old-key", "new-key"]
    assert "old-key" not in repr(agent._client_cache._cache)
    assert "new-key" not in repr(agent._client_cache._cache)


@pytest.mark.asyncio
async def test_study_plugin_starts_and_collects_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    result = await plugin.startup()

    assert isinstance(result, Ok)
    entries = plugin.collect_entries()
    assert "study_status" in entries
    assert "study_explain_text" in entries
    assert "study_ocr_snapshot" in entries
    assert "study_set_mode" in entries
    assert "study_detect_mode_intent" in entries
    status = await plugin.study_status()
    assert isinstance(status, Ok)
    assert status.value["status"] == "ready"
    assert status.value["active_mode"] == MODE_COMPANION
    assert "mode_started_at" in status.value
    assert "recent_mode_switches" in status.value
    assert (runtime_root / "plugins" / "study_companion" / "data" / "study_companion.db").is_file()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_restores_runtime_mode_without_overwriting_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en", "default_mode": MODE_COMPANION},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)
    state = build_initial_state(mode=MODE_TEACHING)
    plugin._store.open()
    try:
        plugin._store.save_config(StudyConfig(mode=MODE_COMPANION, default_mode=MODE_COMPANION, language="en"))
        plugin._store.save_state(state)
    finally:
        plugin._store.close()

    result = await plugin.startup()

    try:
        assert isinstance(result, Ok)
        assert plugin._state.active_mode == MODE_TEACHING
        assert plugin._cfg.mode == MODE_TEACHING
        assert plugin._cfg.default_mode == MODE_COMPANION
        assert plugin._store.load_config(StudyConfig()).default_mode == MODE_COMPANION
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_study_plugin_startup_failure_cleans_partial_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))

    async def _fail_persist(_self) -> None:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(StudyCompanionPlugin, "_persist_state", _fail_persist)
    ctx = _Ctx(
        tmp_path,
        {
            "study": {"language": "en"},
            "ocr_reader": {"enabled": True},
            "rapidocr": {"lang_type": "ch"},
        },
    )
    plugin = StudyCompanionPlugin(ctx)

    result = await plugin.startup()

    assert not isinstance(result, Ok)
    assert plugin._agent is None
    assert plugin._ocr_pipeline is None
    assert plugin._store._conn is None
    assert plugin.get_static_ui_config() is None
    assert plugin.get_list_actions() == []
