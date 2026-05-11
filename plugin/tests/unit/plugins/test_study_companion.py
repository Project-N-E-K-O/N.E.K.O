from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.study_ocr_pipeline import StudyOcrPipeline
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent, build_concept_explain_messages
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
async def test_study_plugin_starts_and_collects_entries(tmp_path: Path) -> None:
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
    await plugin.shutdown()
