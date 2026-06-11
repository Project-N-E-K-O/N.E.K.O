from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion.doc_exporter import DocExporter
from plugin.plugins.study_companion.entry_notebook import _NotebookEntriesMixin
from plugin.plugins.study_companion.models import DocExportConfig
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.store_notebook import NotebookStore
from plugin.plugins.study_companion.store_notebook import NotebookSearchError
from plugin.plugins.study_companion.store_notebook import _normalize_string_list
from plugin.plugins.study_companion.tutor_llm_agent_notebook import (
    expand_note,
    summarize_to_note,
)
from plugin.sdk.plugin import Err, Ok

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))
        return None

    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _BrokenWarningLogger(_Logger):
    def warning(self, *args, **kwargs):
        raise RuntimeError("logger broken")


class _EntryHarness(_NotebookEntriesMixin):
    def __init__(self, notebook_store: NotebookStore) -> None:
        self._notebook_store = notebook_store
        self._agent = None
        self._state = SimpleNamespace(active_mode="companion", last_ocr_text="")
        self._lock = _AsyncNoopLock()
        self.logger = _Logger()


class _AsyncNoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeNotebookAgent:
    def __init__(self) -> None:
        self._config = SimpleNamespace(language="zh-CN")
        self.calls: list[dict[str, object]] = []
        self.message_texts: list[str] = []

    async def _call_model(
        self,
        messages,
        *,
        operation,
        model_group_override=None,
        timeout=None,
    ):
        self.calls.append(
            {
                "operation": operation,
                "model_group": str(model_group_override or ""),
                "timeout": timeout,
            }
        )
        self.message_texts.append(str(messages[-1]["content"]))
        if operation == "expand_note":
            return "> [!ai]\n> 补充一个例子。"
        return "# 标题\n\n## 要点\n\n- A\n\n### 细节\n\nB"


class _FailingNotebookAgent(_FakeNotebookAgent):
    async def _call_model(self, *args, **kwargs):
        raise RuntimeError("model unavailable")


class _PartialOriginalNotebookAgent(_FakeNotebookAgent):
    def __init__(self, raw: str) -> None:
        super().__init__()
        self.raw = raw

    async def _call_model(self, *args, **kwargs):
        self.calls.append(
            {
                "operation": kwargs.get("operation", ""),
                "model_group": str(kwargs.get("model_group_override") or ""),
                "timeout": kwargs.get("timeout"),
            }
        )
        return self.raw


def _make_store(tmp_path) -> tuple[StudyStore, NotebookStore, _Logger]:
    logger = _Logger()
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", logger)
    store.open()
    return store, NotebookStore(store), logger


def test_notebook_warn_falls_back_to_stderr_when_logger_fails(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = _BrokenWarningLogger()
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", logger)
    store.open()
    notebooks = NotebookStore(store)
    try:
        notebooks._warn("study notebook warning fallback: {}", "broken")
        captured = capsys.readouterr()
        assert "study notebook warning fallback: broken" in captured.err
    finally:
        store.close()


def test_notebook_store_crud_search_and_topic_counts(tmp_path) -> None:
    store, notebooks, logger = _make_store(tmp_path)
    try:
        store.ensure_topic(topic_id="closure", name="Closure", subject="cs")
        notebook = notebooks.create_notebook(name="JavaScript", sort_order=2)
        note = notebooks.create_note(
            notebook_id=notebook.id,
            title="Closure",
            content="# Closure\n\n**闭包** keeps outer variables.",
            source_type="session",
            source_ref="session-1",
            topic_ids=["closure"],
            tags=["js", "function"],
        )

        assert note.content_plain == "Closure 闭包 keeps outer variables."
        assert note.snippet.startswith("Closure")
        assert notebooks.list_notes(search_query="闭包")[0].id == note.id
        assert notebooks.get_notes_by_topic("closure")[0].id == note.id
        assert notebooks.count_notes_by_topic() == {"closure": 1}

        updated = notebooks.upsert_note(
            note_id=note.id,
            notebook_id=notebook.id,
            title="Closure updated",
            content="closure update",
            is_ai_generated=True,
            source_type="topic",
            source_ref="closure",
            topic_ids=["closure"],
            tags=["updated"],
        )
        assert updated.source_type == "session"
        assert updated.source_ref == "session-1"
        assert updated.tags == ["updated"]
        warnings = notebooks.consume_last_warnings()
        assert any("source_type" in warning for warning in warnings)
        assert any("source_ref" in warning for warning in warnings)
        assert any(
            "source_type update ignored" in str(args[0])
            for args, _kwargs in logger.warnings
        )
        assert updated.is_ai_generated is True

        partial = notebooks.upsert_note(note_id=note.id, title="Closure final")
        assert partial.title == "Closure final"
        assert partial.notebook_id == notebook.id
        assert partial.content == "closure update"
        assert partial.topic_ids == ["closure"]
        assert partial.tags == ["updated"]
        assert partial.is_ai_generated is True
        assert notebooks.consume_last_warnings() == []

        manual = notebooks.upsert_note(note_id=note.id, is_ai_generated=False)
        assert manual.is_ai_generated is False

        deleted = notebooks.delete_notebook(notebook.id)
        assert deleted == {"deleted": 1, "notes_unlinked": 1}
        assert notebooks.get_note(note.id).notebook_id is None
    finally:
        store.close()


def test_notebook_search_all_and_note_id_export(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        note = notebooks.create_note(
            title="Derivative",
            content="## 要点\n\n导数描述瞬时变化率。",
            topic_ids=["calculus"],
            tags=["math"],
        )

        result = notebooks.search_all("导数")
        assert result["notes"][0]["id"] == note.id

        exported = DocExporter(
            store, config=DocExportConfig(enabled=True)
        ).export(fmt="markdown", note_ids=[note.id], title="Selected Notes")
        assert "# Selected Notes" in exported.markdown
        assert "## Derivative" in exported.markdown
        assert "导数描述瞬时变化率" in exported.markdown
    finally:
        store.close()


def test_notebook_like_search_escapes_wildcards(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        percent_note = notebooks.create_note(
            title="Literal percent",
            content="Progress is 100% complete.",
            tags=["ratio"],
        )
        underscore_note = notebooks.create_note(
            title="snake_case",
            content="Use snake_case names.",
            tags=["style"],
        )
        notebooks.create_note(
            title="Plain note",
            content="No wildcard tokens here.",
            tags=["plain"],
        )
        store.ensure_session(session_id="session_1", mode="companion")
        store.ensure_session(session_id="session-2", mode="companion")

        percent_results = notebooks.list_notes(search_query="%", limit=10)
        underscore_results = notebooks.list_notes(search_query="_", limit=10)
        session_results = notebooks.search_all("_", limit=10)["sessions"]

        assert [item.id for item in percent_results] == [percent_note.id]
        assert [item.id for item in underscore_results] == [underscore_note.id]
        assert [item["id"] for item in session_results] == ["session_1"]
    finally:
        store.close()


def test_notebook_search_merges_fts_and_like_substring_matches(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        title_match = notebooks.create_note(
            title="变化",
            content="函数变化。",
        )
        substring_match = notebooks.create_note(
            title="Derivative",
            content="导数描述瞬时变化率。",
        )

        results = notebooks.list_notes(search_query="变化", limit=10)

        assert {item.id for item in results} == {title_match.id, substring_match.id}
        assert results[0].id == title_match.id
    finally:
        store.close()


@pytest.mark.asyncio
async def test_notebook_entries_serialize_dataclasses(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    harness = _EntryHarness(notebooks)
    try:
        created = await harness.study_notebook_create(name="Physics")
        assert isinstance(created, Ok)
        notebook_id = created.value["notebook"]["id"]

        saved = await harness.study_note_upsert(
            notebook_id=notebook_id,
            title="Force",
            content="F = ma",
            topic_ids=["mechanics"],
            tags=["formula"],
        )
        assert isinstance(saved, Ok)
        note_id = saved.value["note"]["id"]

        conflict = await harness.study_note_upsert(
            note_id=note_id,
            title="Force",
            content="F = ma",
            source_type="session",
            source_ref="session-1",
        )
        assert isinstance(conflict, Ok)
        assert any("source_type" in warning for warning in conflict.value["warnings"])

        listed = await harness.study_note_list(notebook_id=notebook_id)
        assert isinstance(listed, Ok)
        assert listed.value["notes"][0]["id"] == note_id

        searched = await harness.study_note_search_all(query="Force")
        assert isinstance(searched, Ok)
        assert searched.value["notes"][0]["title"] == "Force"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_notebook_highlight_memory_card_reports_unavailable_store(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    harness = _EntryHarness(notebooks)
    try:
        result = await harness.study_note_highlight_action(
            action="create_memory_card",
            selected_text="Force equals mass times acceleration.",
        )

        assert isinstance(result, Err)
        assert result.error.code == "MEMORY_NOT_AVAILABLE"
    finally:
        store.close()


def test_normalize_string_list_rejects_non_iterable_objects() -> None:
    with pytest.raises(TypeError, match="expected string or iterable"):
        _normalize_string_list(object())


def test_notebook_filter_semantics_are_explicit(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        notebook = notebooks.create_notebook(name="Filed")
        filed = notebooks.create_note(notebook_id=notebook.id, title="Filed", content="a")
        unfiled = notebooks.create_note(title="Unfiled", content="b")

        assert {note.id for note in notebooks.list_notes(notebook_id=None)} == {
            filed.id,
            unfiled.id,
        }
        assert [
            note.id
            for note in notebooks.list_notes(
                notebook_filter="unfiled", notebook_id=None
            )
        ] == [unfiled.id]
        assert [
            note.id
            for note in notebooks.list_notes(notebook_id=notebook.id)
        ] == [filed.id]
    finally:
        store.close()


def test_notebook_search_falls_back_to_like_when_fts_errors(tmp_path) -> None:
    store, notebooks, logger = _make_store(tmp_path)
    try:
        note = notebooks.create_note(title="Fallback", content="LIKE fallback target")
        with store._lock:
            conn = store._require_conn()
            conn.execute("DROP TABLE notes_fts")
            conn.commit()

        results = notebooks.list_notes(search_query="fallback")

        assert [item.id for item in results] == [note.id]
        assert any("FTS search failed" in str(args[0]) for args, _ in logger.warnings)
    finally:
        store.close()


def test_notebook_search_raises_when_fts_and_like_both_fail(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        notebooks.create_note(title="Broken", content="corruption target")
        with store._lock:
            conn = store._require_conn()
            conn.execute("DROP TABLE notes_fts")
            conn.commit()

        def fail_like(**_kwargs):
            raise RuntimeError("LIKE fallback failed")

        monkeypatch.setattr(notebooks, "_list_notes_like", fail_like)

        with pytest.raises(NotebookSearchError, match="notebook search failed"):
            notebooks.list_notes(search_query="corruption")
    finally:
        store.close()


def test_delete_notebook_rejects_empty_id(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="notebook_id is required"):
            notebooks.delete_notebook("")
    finally:
        store.close()


def test_notebook_store_serializes_concurrent_upserts(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        notebook = notebooks.create_notebook(name="Concurrent")

        def create(index: int) -> str:
            return notebooks.create_note(
                notebook_id=notebook.id,
                title=f"Note {index}",
                content=f"content {index}",
            ).id

        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(create, range(12)))

        assert len(set(ids)) == 12
        assert len(notebooks.list_notes(notebook_id=notebook.id, limit=20)) == 12
    finally:
        store.close()


def test_session_note_source_filters_records_before_limit(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        store.ensure_topic(topic_id="algebra", name="Algebra", subject="math")
        store.ensure_session(session_id="older", mode="companion")
        store.ensure_session(session_id="recent", mode="companion")
        store.add_qa_record(
            session_id="older",
            topic_id="algebra",
            question={"question": "older session question"},
            user_answer="older answer",
            eval_result={},
            mode="companion",
        )
        for index in range(3):
            store.add_qa_record(
                session_id="recent",
                topic_id="algebra",
                question={"question": f"recent question {index}"},
                user_answer="recent answer",
                eval_result={},
                mode="companion",
            )

        source = notebooks.source_text_for_session("older", limit=1)

        assert "older session question" in source
        assert "recent question" not in source
    finally:
        store.close()


@pytest.mark.asyncio
async def test_notebook_llm_operations_use_expected_model_tiers() -> None:
    agent = _FakeNotebookAgent()

    expanded = await expand_note(agent, "原始内容", topic_context="topic")
    summarized = await summarize_to_note(agent, "source text", source_type="session")

    assert expanded.reply.startswith("原始内容")
    assert "> [!ai]" in expanded.reply
    assert summarized.payload["title"] == "标题"
    assert agent.calls == [
        {"operation": "expand_note", "model_group": "tutor", "timeout": 60.0},
        {
            "operation": "summarize_to_note",
            "model_group": "summary",
            "timeout": 60.0,
        },
    ]


@pytest.mark.asyncio
async def test_notebook_summary_headings_follow_language() -> None:
    agent = _FakeNotebookAgent()
    agent._config.language = "en"

    summarized = await summarize_to_note(agent, "source text", source_type="manual")

    assert "## Key Points" in summarized.reply
    assert "### Details" in summarized.reply


@pytest.mark.asyncio
async def test_notebook_expand_fallback_follows_language() -> None:
    agent = _FailingNotebookAgent()
    agent._config.language = "en"

    expanded = await expand_note(agent, "source note")

    assert expanded.degraded is True
    assert "Unable to connect to the model" in expanded.reply


@pytest.mark.asyncio
async def test_notebook_expand_preserves_full_original_when_model_returns_prefix() -> None:
    original = (
        "".join(f"section-{index:02d};" for index in range(24))
        + "\n\nThis tail was not visible in the model response."
    )
    prefix = original[:120]
    agent = _PartialOriginalNotebookAgent(f"{prefix}\n\n> [!ai]\n> Added detail.")

    expanded = await expand_note(agent, original)

    assert expanded.reply.startswith(original)
    assert "This tail was not visible in the model response." in expanded.reply
    assert "> [!ai]" in expanded.reply
    assert "> Added detail." in expanded.reply
    assert expanded.reply.count(prefix) == 1


@pytest.mark.asyncio
async def test_notebook_llm_operations_truncate_long_sources() -> None:
    agent = _FakeNotebookAgent()

    await expand_note(agent, "x" * 8500)
    await summarize_to_note(agent, "y" * 12500)

    assert "...[truncated 500 chars]" in agent.message_texts[0]
    assert "...[truncated 500 chars]" in agent.message_texts[1]
