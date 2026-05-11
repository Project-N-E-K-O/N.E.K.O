from __future__ import annotations

import json
import os
import shutil
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
from plugin.plugins.study_companion.llm_prompts import build_concept_explain_messages
from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.study_ocr_pipeline import StudyCaptureProfile, StudyOcrPipeline
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent
from plugin.server.application.plugins.ui_query_service import _build_surfaces_sync
from plugin.sdk.plugin import Ok


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
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
        self.inputs: list[tuple[str, dict[str, object]]] = []

    async def concept_explain(self, text: str, context: dict[str, object] | None = None) -> TutorReply:
        self.inputs.append((text, dict(context or {})))
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=f"explained: {text}",
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


def test_study_companion_i18n_bundles_are_present() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    locales = ["zh-CN", "en", "ja", "ko", "ru", "zh-TW", "es", "pt"]
    for locale in locales:
        bundle_path = plugin_dir / "i18n" / f"{locale}.json"
        assert bundle_path.is_file()
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert "plugin.name" in bundle
        assert "ui.title" in bundle
        assert "ui.surface.study_panel" in bundle
        assert "ui.button.explain" in bundle

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
    zh_surfaces, zh_warnings = _build_surfaces_sync("study_companion", meta, locale="zh-CN")
    en_surfaces, en_warnings = _build_surfaces_sync("study_companion", meta, locale="en")
    assert zh_warnings == []
    assert en_warnings == []
    zh_study_panel = next(surface for surface in zh_surfaces if surface["id"] == "study-panel")
    en_study_panel = next(surface for surface in en_surfaces if surface["id"] == "study-panel")
    assert zh_study_panel["title"] == "伴学面板"
    assert en_study_panel["title"] == "Study Panel"

    index_html = (plugin_dir / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    assert "./i18n.js" in index_html
    assert "data-i18n=\"ui.title\"" in index_html
    assert "I18n.init" in main_js


def test_study_companion_static_ui_smoke_with_mocked_runs() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
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
window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') {
    return Response.json(enBundle);
  }
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = body.entry_id === 'study_explain_text' ? 'run-explain' : 'run-status';
    runEntries.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (url === '/runs/run-status') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-explain') {
    return Response.json({ status: 'succeeded' });
  }
  if (url === '/runs/run-status/export') {
    return Response.json({
      items: [{ type: 'json', json: { success: true, data: { status: 'ready', active_mode: 'concept_explain' } } }],
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

await waitFor(() => document.getElementById('statusLine').textContent.includes('ready'), 'ready status');
if (document.title !== 'Study Companion') {
  throw new Error(`unexpected title: ${document.title}`);
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
    assert "study_explain_text: 60000" in source
    assert "const deadline = Date.now() + timeoutForEntry(entryId);" in source
    assert "for (let i = 0; i < 40; i += 1)" not in source
    assert "async function refresh(signal?: AbortSignal, options: { updateReply?: boolean } = {})" in source
    assert "await refresh(controller.signal, { updateReply: false });" in source


def test_study_companion_ui_export_failures_are_not_silent_successes() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted_source = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    static_source = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")

    assert "RUN_EXPORT_RETRY_COUNT = 3" in hosted_source
    assert "throw new Error(`Run export failed: HTTP ${lastStatus}`);" in hosted_source
    assert "const exported = exportResp.ok ? await exportResp.json() : {};" not in hosted_source
    assert "return item?.json?.data || {};" not in hosted_source

    assert "RUN_EXPORT_RETRY_COUNT = 3" in static_source
    assert "throw new Error(tf('ui.error.run_export_failed'" in static_source
    assert "if (!response.ok) {\n    return {};" not in static_source


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
        assert plugin._agent.inputs == [("photosynthesis", {"source": "ocr_snapshot"})]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_tutor_agent_prompt_and_reply_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = build_concept_explain_messages(
        text="A derivative measures instantaneous rate of change.",
        language="en",
        context={"source": "unit-test"},
    )
    assert messages[0]["role"] == "system"
    assert "unit-test" in messages[1]["content"]

    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages):
        return "A derivative is the slope at one point."

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    reply = await agent.concept_explain("derivative")

    assert reply.operation == "concept_explain"
    assert reply.reply == "A derivative is the slope at one point."
    assert reply.degraded is False


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
    assert "llm unavailable" in fallback.diagnostic
    assert "photosynthesis converts light" in fallback.reply


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
    assert "old-key" not in repr(agent._llm_cache)
    assert "new-key" not in repr(agent._llm_cache)


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
    status = await plugin.study_status()
    assert isinstance(status, Ok)
    assert status.value["status"] == "ready"
    assert (runtime_root / "plugins" / "study_companion" / "data" / "study_companion.db").is_file()
    assert not (tmp_path / "data" / "study_companion.db").exists()
    await plugin.shutdown()
