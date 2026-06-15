from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion.doc_exporter import DocExporter
from plugin.plugins.study_companion.entry_notebook import _NotebookEntriesMixin
from plugin.plugins.study_companion.models import DocExportConfig
from plugin.plugins.study_companion.store import StudyStore
from plugin.plugins.study_companion.store_notebook import NotebookStore, _strip_markdown
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
        self.calls: list[dict[str, str]] = []
        self.message_texts: list[str] = []

    async def _call_model(self, messages, *, operation):
        # Mirrors main's TutorLLMAgent._call_model signature, which routes by
        # `operation` and does not accept a model-group override.
        self.calls.append({"operation": operation})
        self.message_texts.append(str(messages[-1]["content"]))
        if operation == "expand_note":
            return "> [!ai]\n> 补充一个例子。"
        return "# 标题\n\n## 要点\n\n- A\n\n### 细节\n\nB"


def _make_store(tmp_path) -> tuple[StudyStore, NotebookStore, _Logger]:
    logger = _Logger()
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", logger)
    store.open()
    return store, NotebookStore(store), logger


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


def test_notebook_list_and_search_omit_full_content(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        note = notebooks.create_note(
            title="Long note",
            content="# Heading\n\n" + "body paragraph. " * 50,
            topic_ids=["calculus"],
        )
        listed = notebooks.list_notes()[0]
        # List/search rows ship only a snippet; the full body stays behind get_note.
        assert listed.content == ""
        assert listed.content_plain == ""
        assert listed.snippet
        searched = notebooks.search_all("body")["notes"][0]
        assert searched["content"] == ""
        assert searched["snippet"]
        full = notebooks.get_note(note.id)
        assert "body paragraph" in full.content
        # JSON backups must still carry the full body despite the list projection.
        backup = store.export_json()
        backup_note = next(n for n in backup["notes"] if n["id"] == note.id)
        assert "body paragraph" in backup_note["content"]
    finally:
        store.close()


def test_notebook_search_merges_fts_and_like_substring_matches(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        exact = notebooks.create_note(title="导数", content="calculus term")
        substring = notebooks.create_note(
            title="变化率",
            content="导数描述瞬时变化率",
        )

        results = notebooks.list_notes(search_query="导数", limit=10)

        result_ids = [note.id for note in results]
        assert exact.id in result_ids
        assert substring.id in result_ids
        assert len(result_ids) == len(set(result_ids))
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

        listed = await harness.study_note_list(notebook_id=notebook_id)
        assert isinstance(listed, Ok)
        assert listed.value["notes"][0]["id"] == note_id

        searched = await harness.study_note_search_all(query="Force")
        assert isinstance(searched, Ok)
        assert searched.value["notes"][0]["title"] == "Force"
    finally:
        store.close()


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
        assert notebooks.list_notes(notebook_filter="specific", notebook_id=None) == []
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


def test_notebook_like_search_treats_wildcards_as_literals(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        percent = notebooks.create_note(title="Rate 100%", content="literal percent")
        notebooks.create_note(title="Rate 1000", content="plain digits")
        underscore = notebooks.create_note(title="alpha_beta", content="literal underscore")
        notebooks.create_note(title="alphaXbeta", content="plain letters")
        slash = notebooks.create_note(title=r"path C:\tmp", content="literal slash")

        with store._lock:
            conn = store._require_conn()
            conn.execute("DROP TABLE notes_fts")
            conn.commit()

        assert [item.id for item in notebooks.list_notes(search_query="100%")] == [
            percent.id
        ]
        assert [item.id for item in notebooks.list_notes(search_query="alpha_beta")] == [
            underscore.id
        ]
        assert [item.id for item in notebooks.list_notes(search_query=r"C:\tmp")] == [
            slash.id
        ]
    finally:
        store.close()


def test_notebook_global_session_search_treats_wildcards_as_literals(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        store.ensure_session(session_id="session_100%", mode="companion")
        store.ensure_session(session_id="session_1000", mode="companion")

        results = notebooks.search_all("100%", limit=10)

        assert [item["id"] for item in results["sessions"]] == ["session_100%"]
    finally:
        store.close()


def test_notes_fts_initialization_failure_keeps_store_bootstrappable() -> None:
    import sqlite3

    from plugin.plugins.study_companion.store_schema import _init_notes_fts

    class _Store:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, object]] = []

        def _log_warning(self, message: str, *args: object) -> None:
            self.warnings.append((message, args[0] if args else ""))

    class _Conn:
        def execute(self, sql: str):
            if "CREATE VIRTUAL TABLE" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return None

    store = _Store()

    _init_notes_fts(store, _Conn())  # type: ignore[arg-type]

    assert store.warnings
    assert "FTS unavailable" in store.warnings[0][0]


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
async def test_note_ai_expand_rejects_missing_content_without_model_call(tmp_path) -> None:
    class _ExpandAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def expand_note(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(reply="", degraded=False, diagnostic="")

    store, notebooks, _logger = _make_store(tmp_path)
    harness = _EntryHarness(notebooks)
    agent = _ExpandAgent()
    harness._agent = agent
    try:
        result = await harness.study_note_ai_expand()

        assert isinstance(result, Err)
        assert result.error.code == "MISSING_TEXT"
        assert agent.calls == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_notebook_llm_operations_use_expected_operations() -> None:
    agent = _FakeNotebookAgent()

    expanded = await expand_note(agent, "原始内容", topic_context="topic")
    summarized = await summarize_to_note(agent, "source text", source_type="session")

    assert expanded.reply.startswith("原始内容")
    assert "> [!ai]" in expanded.reply
    assert summarized.payload["title"] == "标题"
    # Notebook ops route by `operation` through main's default agent model;
    # there is no separate tutor/summary model tier on main.
    assert agent.calls == [
        {"operation": "expand_note"},
        {"operation": "summarize_to_note"},
    ]


@pytest.mark.asyncio
async def test_note_upsert_omitted_keeps_filing_explicit_empty_unfiles(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    harness = _EntryHarness(notebooks)
    try:
        nb = await harness.study_notebook_create(name="Algebra")
        notebook_id = nb.value["notebook"]["id"]
        saved = await harness.study_note_upsert(
            notebook_id=notebook_id, title="T", content="C"
        )
        note_id = saved.value["note"]["id"]

        # omitted notebook_id (partial edit) must keep the existing filing,
        # and omitted title-only edit must not wipe the stored content.
        edited = await harness.study_note_upsert(note_id=note_id, title="T2")
        assert edited.value["note"]["notebook_id"] == notebook_id
        assert edited.value["note"]["title"] == "T2"
        assert edited.value["note"]["content"] == "C"

        # an explicit "" is an intentional unfile
        unfiled = await harness.study_note_upsert(note_id=note_id, notebook_id="")
        assert unfiled.value["note"]["notebook_id"] is None
    finally:
        store.close()


def test_note_upsert_schema_has_no_destructive_update_defaults() -> None:
    # A generated form must not pre-fill ""/[]/false for update fields, or a
    # single-field edit would silently overwrite the rest. note_id/source_type
    # keep their defaults (identity + provenance, not user content).
    meta = getattr(_NotebookEntriesMixin.study_note_upsert, "__neko_event_meta__")
    props = meta.input_schema["properties"]
    for field in ("title", "content", "topic_ids", "tags", "is_ai_generated", "notebook_id"):
        assert "default" not in props[field], field
    assert props["note_id"]["default"] == ""


def test_strip_markdown_keeps_fenced_code_text_searchable() -> None:
    plain = _strip_markdown("intro paragraph\n```python\nwidgetfactory()\n```\nouttro")
    # the fenced block's inner code must survive into content_plain (it feeds
    # search / FTS), only the ``` fences + lang tag are dropped
    assert "widgetfactory" in plain
    assert "```" not in plain


def test_strip_markdown_keeps_comparisons_and_generics_but_drops_tags() -> None:
    plain = _strip_markdown("if a < b and x > 0 then List<T> works <div>tag</div>")
    # math comparisons must stay searchable; only real tags are removed
    assert "a < b" in plain
    assert "x > 0" in plain
    assert "tag" in plain
    assert "<div>" not in plain and "</div>" not in plain


def test_strip_markdown_keeps_intraword_underscores() -> None:
    plain = _strip_markdown("call widget_factory then _emphasis_ done")
    # underscores inside identifiers stay searchable; only emphasis _..._ is dropped
    assert "widget_factory" in plain
    assert "emphasis" in plain
    assert "_emphasis_" not in plain


def test_build_notes_markdown_escapes_metadata(tmp_path) -> None:
    store, notebooks, _logger = _make_store(tmp_path)
    try:
        note = notebooks.create_note(
            title="# Not A Heading [x]",
            content="body text",
            tags=["c#", "a`b"],
        )
        md = notebooks.build_notes_markdown([note.id], title="My # Export")
        # the note title must not start a real markdown heading inside the body
        assert "## \\# Not A Heading \\[x\\]" in md
        assert "\\# Export" in md
        assert "c\\#" in md and "a\\`b" in md
        # the note content stays verbatim
        assert "body text" in md
    finally:
        store.close()


@pytest.mark.asyncio
async def test_note_ai_expand_preserves_full_long_original() -> None:
    long_original = "原始内容开头。" + "中间的正文段落需要完整保留。" * 40

    class _PrefixEchoAgent:
        def __init__(self) -> None:
            self._config = SimpleNamespace(language="zh-CN")

        async def _call_model(self, messages, *, operation):
            # Model echoes only a leading slice plus the required callout — the
            # rest of the user's note must NOT be dropped.
            return long_original[:120] + "\n\n> [!ai]\n> 补充说明。"

    expanded = await expand_note(_PrefixEchoAgent(), long_original, topic_context="t")

    assert long_original in expanded.reply


@pytest.mark.asyncio
async def test_notebook_summary_headings_follow_language() -> None:
    agent = _FakeNotebookAgent()
    agent._config.language = "en"

    summarized = await summarize_to_note(agent, "source text", source_type="manual")

    assert "## Key Points" in summarized.reply
    assert "### Details" in summarized.reply


@pytest.mark.asyncio
async def test_notebook_expand_fallback_follows_language() -> None:
    class _BrokenNotebookAgent(_FakeNotebookAgent):
        async def _call_model(self, messages, *, operation):
            raise RuntimeError("model unavailable")

    agent = _BrokenNotebookAgent()
    agent._config.language = "en"

    expanded = await expand_note(agent, "Original note")

    assert expanded.degraded is True
    assert "The model is currently unavailable for expansion" in expanded.reply
    assert "暂时无法连接模型扩写" not in expanded.reply


@pytest.mark.asyncio
async def test_notebook_llm_operations_truncate_long_sources() -> None:
    agent = _FakeNotebookAgent()

    await expand_note(agent, "x" * 8500)
    await summarize_to_note(agent, "y" * 12500)

    assert "...[truncated 500 chars]" in agent.message_texts[0]
    assert "...[truncated 500 chars]" in agent.message_texts[1]
