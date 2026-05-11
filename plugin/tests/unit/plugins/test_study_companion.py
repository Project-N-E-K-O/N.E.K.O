from __future__ import annotations

import json
from pathlib import Path

import pytest
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.study_ocr_pipeline import StudyOcrPipeline
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent, build_concept_explain_messages
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


def test_ocr_pipeline_handles_empty_text_repeats_and_errors() -> None:
    cfg = StudyConfig()
    empty = StudyOcrPipeline(logger=_Logger(), config=cfg, ocr_backend=_FakeOcrBackend(""))
    assert empty.snapshot_from_image(object()).status == "empty"

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
