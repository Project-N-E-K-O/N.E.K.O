from __future__ import annotations

import re
from collections import Counter

import pytest

from config.prompts import prompts_memory


_FORMAT_FIELD_RE = re.compile(r"(?<!{){([A-Za-z_][A-Za-z0-9_]*)}(?!})")
_PERCENT_FIELD_RE = re.compile(r"%(?:\([A-Za-z_][A-Za-z0-9_]*\))?[a-zA-Z]")


def _localized_tables() -> list[tuple[str, dict[str, str]]]:
    found: list[tuple[str, dict[str, str]]] = []
    seen: set[int] = set()

    def visit(path: str, value: object) -> None:
        if not isinstance(value, dict) or id(value) in seen:
            return
        seen.add(id(value))
        if (
            isinstance(value.get("zh"), str)
            and isinstance(value.get("en"), str)
        ):
            found.append((path, value))
        for key, child in value.items():
            visit(f"{path}.{key}", child)

    for name, value in vars(prompts_memory).items():
        if name.isupper():
            visit(name, value)
    return found


_LOCALIZED_TABLES = _localized_tables()


def _placeholder_signature(text: str) -> Counter[str]:
    return Counter(
        _FORMAT_FIELD_RE.findall(text)
        + _PERCENT_FIELD_RE.findall(text)
    )


@pytest.mark.parametrize(
    ("path", "table"),
    _LOCALIZED_TABLES,
    ids=[path for path, _ in _LOCALIZED_TABLES],
)
def test_every_memory_locale_table_has_traditional_chinese(
    path: str,
    table: dict[str, str],
):
    assert "zh-TW" in table, f"{path} is missing zh-TW"
    assert isinstance(table["zh-TW"], str) and table["zh-TW"].strip()


@pytest.mark.parametrize(
    ("path", "table"),
    _LOCALIZED_TABLES,
    ids=[path for path, _ in _LOCALIZED_TABLES],
)
def test_each_traditional_entry_is_caught_by_deletion_mutation(
    path: str,
    table: dict[str, str],
):
    mutant = dict(table)
    mutant.pop("zh-TW", None)
    with pytest.raises(AssertionError, match=re.escape(path)):
        assert "zh-TW" in mutant, f"{path} is missing zh-TW"


@pytest.mark.parametrize(
    ("path", "table"),
    _LOCALIZED_TABLES,
    ids=[path for path, _ in _LOCALIZED_TABLES],
)
def test_traditional_templates_preserve_simplified_placeholders(
    path: str,
    table: dict[str, str],
):
    assert _placeholder_signature(table["zh-TW"]) == _placeholder_signature(
        table["zh"]
    ), path


@pytest.mark.parametrize("locale", ["zh-TW", "zh-Hant", "zh-HK", "tchinese"])
def test_memory_getters_keep_traditional_locale_aliases(locale: str):
    prompt = prompts_memory.get_recent_history_manager_prompt(locale)
    assert "資訊豐富" in prompt
    assert prompt != prompts_memory.get_recent_history_manager_prompt("zh")


@pytest.mark.parametrize(
    "mainland_term",
    [
        "置信度",
        "搜索",
        "实时",
        "设备",
        "运行",
        "返回",
        "信号",
        "感叹号",
        "心里",
        "诶",
    ],
)
def test_traditional_templates_avoid_known_mainland_terms(mainland_term: str):
    traditional = "\n".join(table["zh-TW"] for _, table in _LOCALIZED_TABLES)
    assert mainland_term not in traditional


def test_emotion_prompt_keeps_fixed_expert_preamble():
    prompt = prompts_memory.get_emotion_analysis_prompt("zh-TW")
    assert prompt.startswith("你是一个情感分析专家。")
    assert "使用者" in prompt
    assert "信賴度" in prompt
    assert "回傳" in prompt


def test_summary_prompt_uses_request_scoped_traditional_locale():
    from memory.recent import CompressedRecentHistoryManager
    from utils.language_utils import language_context
    from utils.llm_client import AIMessage, HumanMessage

    manager = object.__new__(CompressedRecentHistoryManager)
    manager.name_mapping = {"human": "Alice"}
    messages = [
        HumanMessage(content="好"),
        AIMessage(content="嗯"),
    ]
    rendered = manager._render_messages_to_text(messages, "Neko")
    locale_text = manager._summary_prompt_locale_text(messages)
    with language_context("zh-TW"):
        prompt = manager._build_summary_prompt(
            rendered,
            False,
            locale_text=locale_text,
        )

    assert locale_text == "好\n嗯"
    assert rendered == "Alice | 好\nNeko | 嗯"
    assert "資訊豐富" in prompt
    assert "負面回饋" in prompt


@pytest.mark.asyncio
async def test_compressed_memo_wrapper_keeps_traditional_locale():
    from config.prompts.prompts_sys import MEMORY_MEMO_WITH_SUMMARY
    from memory.recent import CompressedRecentHistoryManager
    from utils.language_utils import language_context
    from utils.llm_client import HumanMessage

    manager = object.__new__(CompressedRecentHistoryManager)
    manager.name_mapping = {"human": "Alice"}

    async def invoke(_prompt):
        return "使用者喜歡貓。"

    async def read_anchor(_name):
        return None

    async def write_anchor(_name):
        return None

    manager._invoke_summary_llm = invoke
    manager._aread_last_past_block_update_at = read_anchor
    manager._awrite_last_past_block_update_at = write_anchor

    with language_context("zh-TW"):
        memo, summary = await manager.compress_history(
            [HumanMessage(content="我喜歡貓")],
            "Neko",
        )

    assert summary == "使用者喜歡貓。"
    assert memo.content == "先前對話的備忘錄：使用者喜歡貓。"
    mutant = dict(MEMORY_MEMO_WITH_SUMMARY)
    mutant.pop("zh-TW")
    with pytest.raises(AssertionError):
        assert "zh-TW" in mutant


def test_persona_renderer_localizes_all_traditional_headers():
    from memory.persona.manager import PersonaManager
    from utils.language_utils import language_context

    master = {"text": "Alice 喜歡貓"}
    neko = {"text": "Neko 喜歡音樂"}
    relationship = {"text": "兩人常一起聊天"}
    suppressed = {"text": "不要主動提旅行", "suppress": True}
    persona = {
        "master": {"facts": [master, suppressed]},
        "neko": {"facts": [neko]},
        "relationship": {"facts": [relationship]},
    }
    renderer = object.__new__(PersonaManager)

    with language_context("zh-TW"):
        rendered = renderer._compose_markdown_from_trimmed(
            "Neko",
            persona,
            {"human": "Alice"},
            [
                ("master", master),
                ("neko", neko),
                ("relationship", relationship),
            ],
            [],
            {},
            [{"text": "可能喜歡散步"}],
            [{"text": "確定喜歡咖啡"}],
        )

    assert "### 關於Alice" in rendered
    assert "### 關於Neko" in rendered
    assert "### 關係動態" in rendered
    assert "Neko最近的印象（還不太確定）" in rendered
    assert "Neko比較確定的印象" in rendered
    assert "### 暫不主動提及的內容" in rendered
    assert "关系动态" not in rendered
    assert "还不太确定" not in rendered
    assert "暂不主动提及" not in rendered


def test_holiday_context_uses_taiwan_calendar_for_traditional_locale(
    monkeypatch,
):
    from datetime import date

    from utils import holiday_cache

    today = date.today()
    period = holiday_cache.HolidayPeriod(
        "Holiday",
        "臺灣假日",
        today,
        today,
    )
    monkeypatch.setitem(
        holiday_cache._period_cache,
        ("TW", today.year),
        [period],
    )

    assert holiday_cache._LANG_TO_COUNTRY["zh-TW"] == "TW"
    assert holiday_cache.get_holiday_context_line("zh-TW") == "臺灣假日"


@pytest.mark.parametrize(
    ("month", "day", "expected"),
    [(2, 14, "情人節"), (12, 25, "聖誕節")],
)
def test_traditional_global_holiday_names_have_mutation_guard(
    month,
    day,
    expected,
):
    from utils import holiday_cache

    names = next(
        names
        for entry_month, entry_day, names in holiday_cache._GLOBAL_EXTRA_HOLIDAYS
        if (entry_month, entry_day) == (month, day)
    )
    assert names["zh-TW"] == expected
    mutant = dict(names)
    mutant.pop("zh-TW")
    with pytest.raises(AssertionError):
        assert "zh-TW" in mutant


def test_builtin_recall_schema_uses_session_traditional_locale(monkeypatch):
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.tool_calling import ToolRegistry

    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    manager = object.__new__(ToolCallingMixin)
    manager.user_language = "zh-TW"
    manager.tool_registry = ToolRegistry()

    manager._register_builtin_tools()

    recall = manager.tool_registry.get("recall_memory")
    assert recall is not None
    assert "使用者偏好" in recall.description
    assert "關鍵字" in recall.parameters["properties"]["query"]["description"]
    assert "搜尋" in recall.parameters["properties"]["time"]["description"]


@pytest.mark.asyncio
async def test_memory_post_prefers_live_session_locale(monkeypatch):
    from main_logic import cross_server

    calls: list[dict] = []

    class Response:
        status_code = 200
        text = '{"status":"cached"}'

    class Client:
        async def post(self, _url, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(cross_server, "get_internal_http_client", Client)

    ok, _, _ = await cross_server._post_memory_server(
        "cache",
        "Neko",
        [],
        timeout_s=1,
        language="zh-TW",
    )

    assert ok is True
    assert calls[0]["json"]["language"] == "zh-TW"


@pytest.mark.asyncio
async def test_new_dialog_request_forwards_session_locale(monkeypatch):
    from main_logic.core.lifecycle import LifecycleMixin
    from utils import internal_http_client

    calls: list[dict] = []

    class Response:
        is_success = True
        text = "ok"

    class Client:
        async def get(self, _url, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)
    manager = object.__new__(LifecycleMixin)
    manager.user_language = "zh-TW"

    result = await manager._start_session_fetch_new_dialog("Neko", 48912)

    assert result == "ok"
    assert calls[0]["params"] == {"language": "zh-TW"}


@pytest.mark.asyncio
async def test_game_archive_writer_forwards_full_session_locale(monkeypatch):
    from main_routers.game_router import archive
    from utils import internal_http_client

    calls = []

    class Response:
        content = b"{}"
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {}

    class Client:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    async def highlights(_archive):
        return {}

    monkeypatch.setattr(archive, "_ensure_game_archive_memory_highlights", highlights)
    monkeypatch.setattr(
        archive,
        "_build_game_archive_memory_messages",
        lambda _archive: [{"role": "user", "content": "好"}],
    )
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)

    result = await archive._submit_game_archive_to_memory({
        "lanlan_name": "Neko",
        "session_id": "game-1",
        "game_type": "soccer",
        "user_language": "zh-TW",
        "soccer_game_memory_archive_enabled": True,
    })

    assert result["ok"] is True
    assert calls[0][1]["json"]["language"] == "zh-TW"


@pytest.mark.asyncio
async def test_new_dialog_persists_explicit_session_locale(monkeypatch):
    from app.memory_server import locale_state, routes, runtime

    class Recorded(RuntimeError):
        pass

    async def load_characters():
        return {"猫娘": {"Neko": {}}}

    def record(name, language, *, order):
        assert (name, language) == ("Neko", "zh-TW")
        assert isinstance(order, int)
        raise Recorded

    monkeypatch.setattr(runtime._config_manager, "aload_characters", load_characters)
    monkeypatch.setattr(
        locale_state,
        "reserve_character_prompt_locale_order",
        lambda _name: 42,
    )
    monkeypatch.setattr(locale_state, "record_character_prompt_locale", record)

    with pytest.raises(Recorded):
        await routes._new_dialog("Neko", "zh-TW")


@pytest.mark.asyncio
async def test_scoped_context_activates_request_locale(monkeypatch):
    from app.memory_server import routes
    from utils.language_utils import get_global_language_full

    observed = []

    async def render(name, req):
        observed.append((name, req.language, get_global_language_full()))
        return "ok"

    monkeypatch.setattr(routes, "_get_scoped_context", render)
    request = routes.ScopedContextRequest(
        subjects=[{
            "subject_kind": "group_chat",
            "subject_id": "qq:7788",
        }],
        language="zh-TW",
    )

    assert await routes.get_scoped_context("Neko", request) == "ok"
    assert observed == [("Neko", "zh-TW", "zh-TW")]


@pytest.mark.asyncio
async def test_scoped_history_activates_request_locale(monkeypatch):
    from app.memory_server import routes
    from utils.language_utils import get_global_language_full

    observed = []

    async def process(name, req):
        observed.append((name, req.language, get_global_language_full()))
        return {"status": "ok"}

    monkeypatch.setattr(routes, "_process_scoped_history", process)
    request = routes.ScopedHistoryRequest(
        input_history="[]",
        subject={
            "subject_kind": "group_chat",
            "subject_id": "qq:7788",
        },
        language="zh-TW",
    )

    assert await routes.process_scoped_history("Neko", request) == {
        "status": "ok",
    }
    assert observed == [("Neko", "zh-TW", "zh-TW")]


def test_scoped_prompt_locale_survives_restart_and_rejects_stale_write(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    locale_path = tmp_path / "scoped_prompt_locales.json"
    subject = MemorySubject.group_chat("qq", "7788")
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(locale_path),
    )
    locale_state._subject_locale_cache.clear()

    newer = locale_state.reserve_subject_prompt_locale_order("Neko", subject)
    locale_state.record_subject_prompt_locale(
        "Neko",
        subject,
        "zh-TW",
        order=newer,
    )
    locale_state.record_subject_prompt_locale(
        "Neko",
        subject,
        "en",
        order=newer - 1,
    )
    locale_state._subject_locale_cache.clear()

    assert locale_state.get_subject_prompt_locale("Neko", subject) == "zh-TW"


@pytest.mark.asyncio
async def test_scoped_history_persists_subject_locale(monkeypatch):
    import json

    from app.memory_server import locale_state, routes, runtime

    class FactStore:
        async def extract_facts(self, *_args, **_kwargs):
            return []

    recorded = []
    monkeypatch.setattr(runtime, "fact_store", FactStore())
    monkeypatch.setattr(
        locale_state,
        "reserve_subject_prompt_locale_order",
        lambda _name, _subject: 42,
    )
    monkeypatch.setattr(
        locale_state,
        "record_subject_prompt_locale",
        lambda name, subject, language, *, order: recorded.append(
            (name, subject.key, language, order)
        ),
    )
    request = routes.ScopedHistoryRequest(
        input_history=json.dumps([{"role": "user", "content": "喜歡貓"}]),
        subject={
            "subject_kind": "group_chat",
            "subject_id": "qq:7788",
        },
        language="zh-TW",
    )

    result = await routes.process_scoped_history("Neko", request)

    assert result["status"] == "processed"
    assert recorded == [
        ("Neko", "group_chat:qq:7788", "zh-TW", 42),
    ]


@pytest.mark.asyncio
async def test_scoped_history_batch_persists_each_completed_subject_locale(
    monkeypatch,
):
    import json

    from app.memory_server import locale_state, routes, runtime

    class FactStore:
        async def extract_facts_batch(self, _segments, _name):
            return [
                {"status": "ok", "created": []},
                {"status": "failed", "created": []},
            ]

        @staticmethod
        def sanitize_speaker_label(label):
            return label

    orders = iter([41, 42])
    recorded = []
    monkeypatch.setattr(runtime, "fact_store", FactStore())
    monkeypatch.setattr(
        locale_state,
        "reserve_subject_prompt_locale_order",
        lambda _name, _subject: next(orders),
    )
    monkeypatch.setattr(
        locale_state,
        "record_subject_prompt_locale",
        lambda name, subject, language, *, order: recorded.append(
            (name, subject.key, language, order)
        ),
    )
    request = routes.ScopedHistoryRequest(
        segments=[
            {
                "input_history": json.dumps([
                    {"role": "user", "content": "喜歡貓"},
                ]),
                "subject": {
                    "subject_kind": "group_participant",
                    "subject_id": "qq:7788:1001",
                },
                "speaker_label": "Alice",
            },
            {
                "input_history": json.dumps([
                    {"role": "user", "content": "喜歡狗"},
                ]),
                "subject": {
                    "subject_kind": "group_participant",
                    "subject_id": "qq:7788:1002",
                },
                "speaker_label": "Bob",
            },
        ],
        language="zh-TW",
    )

    result = await routes.process_scoped_history("Neko", request)

    assert [item["status"] for item in result["segments"]] == [
        "ok",
        "failed",
    ]
    assert recorded == [
        ("Neko", "group_participant:qq:7788:1001", "zh-TW", 41),
    ]


@pytest.mark.asyncio
async def test_deferred_scoped_synthesis_restores_subject_locale(monkeypatch):
    from memory.reflection import synthesis
    from memory.scopes import MemorySubject
    from utils.language_utils import get_global_language_full, language_context

    subject = MemorySubject.group_chat("qq", "7788")
    fact = {
        "id": "fact-1",
        "text": "喜歡貓",
        "importance": 9,
        **subject.as_entry_fields(),
    }
    observed = []

    class FactStore:
        async def aload_facts(self, _name):
            return [fact]

    class Harness(synthesis.SynthesisMixin):
        _fact_store = FactStore()

        async def synthesize_reflections(self, name, *, subject):
            observed.append((name, subject.key, get_global_language_full()))
            return [{"id": "reflection-1"}]

    async def resolve(name, resolved_subject):
        assert (name, resolved_subject.key) == (
            "Neko",
            "group_chat:qq:7788",
        )
        return "zh-TW"

    monkeypatch.setattr(synthesis, "MIN_FACTS_FOR_REFLECTION", 1)
    with language_context("en"):
        result = await Harness().synthesize_scoped_reflections(
            "Neko",
            subject_locale_resolver=resolve,
        )

    assert result == [{"id": "reflection-1"}]
    assert observed == [
        ("Neko", "group_chat:qq:7788", "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_qq_bootstrap_requests_forward_full_locale(monkeypatch):
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from utils import language_utils

    calls = []

    class Response:
        text = "ok"

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok"}

    class Client:
        async def get(self, _url, **kwargs):
            calls.append(("get", kwargs))
            return Response()

        async def post(self, _url, **kwargs):
            calls.append(("post", kwargs))
            return Response()

    monkeypatch.setattr(QQMemoryBridge, "_client", staticmethod(Client))
    monkeypatch.setattr(
        language_utils,
        "get_global_language_full",
        lambda: "zh-TW",
    )
    bridge = QQMemoryBridge(object())
    await bridge.fetch_bootstrap_memory("Neko", language="zh-TW")
    await bridge.fetch_scoped_bootstrap_memory(
        "Neko",
        subjects=[{
            "subject_kind": "group_chat",
            "subject_id": "qq:7788",
        }],
        language="zh-TW",
    )
    await bridge.post_scoped_memory_history(
        "Neko",
        [{"role": "user", "content": "喜歡貓"}],
        subject={
            "subject_kind": "group_chat",
            "subject_id": "qq:7788",
        },
    )
    await bridge.post_scoped_memory_history_batch(
        "Neko",
        [{
            "messages": [{"role": "user", "content": "喜歡貓"}],
            "subject": {
                "subject_kind": "group_participant",
                "subject_id": "qq:7788:1001",
            },
            "speaker_label": "Alice",
        }],
    )

    assert calls[0][1]["params"] == {"language": "zh-TW"}
    assert calls[1][1]["json"]["language"] == "zh-TW"
    assert calls[2][1]["json"]["language"] == "zh-TW"
    assert calls[3][1]["json"]["language"] == "zh-TW"


def test_memory_prompt_locale_detection_ignores_formatter_metadata():
    from memory.fact_dedup import FactDedupResolver
    from memory.refine import MemoryRefineEngine
    from memory.reflection.promotion_merge import PromotionMergeMixin
    from memory.reflection.synthesis import SynthesisMixin
    from utils.language_utils import detect_prompt_language

    dedup_text = FactDedupResolver._locale_text([{
        "candidate_text": "王",
        "existing_text": "李",
        "candidate_id": "abcdef1234567890",
        "existing_id": "fedcba0987654321",
    }])
    refine_text = MemoryRefineEngine._cluster_locale_text([{
        "id": "reflection.abcdef1234567890",
        "text": "怕貓",
        "relation_type": "preference",
        "temporal_scope": "pattern",
    }])
    promotion_text = PromotionMergeMixin._promotion_locale_text(
        {"id": "reflection.abcdef1234567890", "text": "怕貓"},
        [("master", {"id": "fedcba0987654321", "text": "愛狗"})],
        [],
    )
    synthesis_text = SynthesisMixin._synthesis_locale_text(
        [{"id": "abcdef1234567890", "text": "怕貓", "importance": 5}],
        ["愛狗"],
    )

    for raw_text in (dedup_text, refine_text, promotion_text, synthesis_text):
        assert detect_prompt_language(raw_text, ui_language="zh-TW") == "zh-TW"


def test_review_locale_detection_ignores_ascii_speaker_labels():
    from memory.recent import _review_prompt_locale_text
    from utils.language_utils import detect_prompt_language

    messages = [{"type": "Alice", "content": "好"} for _ in range(3)]
    raw_text = _review_prompt_locale_text(messages)

    assert "Alice" not in raw_text
    assert detect_prompt_language(raw_text, ui_language="zh-TW") == "zh-TW"


@pytest.mark.asyncio
async def test_pregame_history_request_forwards_session_locale(monkeypatch):
    from main_routers.game_router import pregame
    from utils import internal_http_client

    calls: list[dict] = []

    class Response:
        is_success = True
        text = "history"

    class Client:
        async def get(self, _url, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)

    history, error = await pregame._fetch_recent_history_for_pregame(
        "Neko",
        language="zh-TW",
    )

    assert history == "history"
    assert error == ""
    assert calls[0]["params"] == {"language": "zh-TW"}


@pytest.mark.asyncio
async def test_external_import_commit_forwards_ui_locale(monkeypatch):
    from main_routers import memory_router
    from utils import config_manager, internal_http_client

    forwarded: list[dict] = []
    analysis = {
        "source_format": "openclaw",
        "files": ["MEMORY.md"],
        "candidates": [{"text": "Uses Python"}],
        "warnings": [],
    }

    class Request:
        async def json(self):
            return {"character_name": "Neko", "language": "zh-TW"}

    class Response:
        status_code = 200

        def json(self):
            return {
                "status": "success",
                "source_format": "openclaw",
                "added_persona": 0,
                "added_facts": 1,
                "skipped_duplicates": 0,
                "warning_count": 0,
            }

    class Client:
        async def post(self, _url, **kwargs):
            forwarded.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(
        memory_router,
        "_prepare_external_import",
        lambda _payload: ("Neko", analysis),
    )
    monkeypatch.setattr(memory_router, "assert_cloudsave_writable", lambda *_a, **_k: None)
    monkeypatch.setattr(config_manager, "get_config_manager", object)
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)

    result = await memory_router.commit_external_memory_import(Request())

    assert result["success"] is True
    assert forwarded[0]["language"] == "zh-TW"


def test_signal_loop_records_latest_session_locale(monkeypatch):
    from app.memory_server import locale_state, signal_extraction

    recorded = []
    monkeypatch.setattr(
        locale_state,
        "record_character_prompt_locale",
        lambda name, language, **kwargs: recorded.append(
            (name, language, kwargs["order"])
        ),
    )
    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn(
        "Neko",
        language="zh-TW",
        locale_order=123,
    )

    assert recorded == [("Neko", "zh-TW", 123)]


def test_signal_loop_records_missing_session_locale(monkeypatch):
    from app.memory_server import locale_state, signal_extraction

    recorded = []
    monkeypatch.setattr(
        locale_state,
        "record_character_prompt_locale",
        lambda name, language, **kwargs: recorded.append(
            (name, language, kwargs["order"])
        ),
    )
    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn("Neko")

    assert recorded == [("Neko", None, None)]


@pytest.mark.asyncio
async def test_idle_maintenance_uses_latest_session_locale(monkeypatch, tmp_path):
    from app.memory_server import evidence_loops, locale_state, signal_extraction
    from utils.language_utils import get_global_language_full

    observed = []

    async def operation(name):
        observed.append((name, get_global_language_full()))
        return 1

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    signal_extraction._signal_check_record_turn("Neko", language="zh-TW")
    signal_extraction._signal_check_state.clear()
    locale_state._locale_cache.clear()

    result = await evidence_loops._run_with_character_language("Neko", operation)

    assert result == 1
    assert observed == [("Neko", "zh-TW")]

    locale_state.record_character_prompt_locale("Neko", None)
    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") is None


@pytest.mark.asyncio
async def test_periodic_promotion_uses_durable_character_locale(monkeypatch, tmp_path):
    from app.memory_server import evidence_loops, locale_state, runtime
    from utils.language_utils import get_global_language_full

    observed = []

    class ReflectionEngine:
        async def aauto_promote_stale(self, name):
            observed.append((name, get_global_language_full()))
            return 1

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")
    locale_state._locale_cache.clear()

    result = await evidence_loops._auto_promote_character("Neko", True)

    assert result == 1
    assert observed == [("Neko", "zh-TW")]


@pytest.mark.asyncio
async def test_idle_history_tasks_use_durable_character_locale(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import evidence_loops, locale_state, review, runtime
    from utils.language_utils import get_global_language_full

    observed = []

    class RecentHistoryManager:
        async def update_history(
            self,
            messages,
            name,
            *,
            detailed,
            on_compress_done,
        ):
            observed.append((
                "compress",
                messages,
                name,
                detailed,
                on_compress_done,
                get_global_language_full(),
            ))

    async def maybe_spawn_review(name):
        observed.append(("review", name, get_global_language_full()))

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    monkeypatch.setattr(runtime, "recent_history_manager", RecentHistoryManager())
    monkeypatch.setattr(review, "maybe_spawn_review", maybe_spawn_review)
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")
    locale_state._locale_cache.clear()

    await evidence_loops._compress_recent_history("Neko")
    await evidence_loops._spawn_review_with_character_language("Neko")

    assert observed == [
        (
            "compress",
            [],
            "Neko",
            True,
            review._on_compress_done,
            "zh-TW",
        ),
        ("review", "Neko", "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_persona_fusion_detects_locale_from_candidate_body(monkeypatch):
    from memory.persona import fusion
    from utils.language_utils import language_context

    observed = []

    def detect(text, *, ui_language):
        observed.append((text, ui_language))
        return "zh-TW"

    class ConfigManager:
        async def aget_character_data(self):
            return (None, None, None, None, {}, None, None, None, None)

        async def aget_model_api_config(self, _tier, *, core_config=None):
            raise RuntimeError("stop after prompt construction")

    class Harness(fusion.ExternalFusionMixin):
        def __init__(self):
            self._config_manager = ConfigManager()

    monkeypatch.setattr(fusion, "detect_prompt_language", detect)

    with language_context("zh-TW"):
        result = await Harness()._allm_call_fusion(
            "Neko",
            "master",
            [{"source_section": "Preferences", "text": "喜歡貓"}],
            600,
        )

    assert result is None
    assert observed == [("喜歡貓", "zh-TW")]


@pytest.mark.asyncio
async def test_signal_loop_uses_durable_locale_instead_of_stale_cache(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import locale_state, signal_extraction
    from utils.language_utils import get_global_language_full

    observed = []

    async def operation(name):
        observed.append((name, get_global_language_full()))
        return 1

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")
    locale_state._locale_cache.clear()
    signal_extraction._signal_check_state["Neko"] = {
        "turns_since": 1,
        "last_check_ts": None,
        "language": "en",
    }

    result = await signal_extraction._run_signal_check_with_character_locale(
        "Neko",
        operation,
    )

    assert result == 1
    assert observed == [("Neko", "zh-TW")]


def test_signal_loop_rejects_stale_locale_worker(monkeypatch, tmp_path):
    from app.memory_server import locale_state, signal_extraction

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    signal_extraction._signal_check_state.clear()

    signal_extraction._signal_check_record_turn(
        "Neko",
        language="zh-TW",
        locale_order=200,
    )
    signal_extraction._signal_check_record_turn(
        "Neko",
        language="en",
        locale_order=100,
    )

    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") == "zh-TW"

    # 升级前入队的旧任务没有顺序号，也不能覆盖已有的新状态。
    signal_extraction._signal_check_record_turn("Neko", language="ja")
    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") == "zh-TW"


def test_locale_order_reservation_survives_clock_rollback(monkeypatch, tmp_path):
    import json

    from app.memory_server import locale_state

    locale_path = tmp_path / "prompt_locale.json"
    future_order = 10**30
    locale_path.write_text(
        json.dumps({
            "language": "zh-CN",
            "order": future_order,
            "reserved_order": future_order,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()

    reserved = locale_state.reserve_character_prompt_locale_order("Neko")

    assert reserved == future_order + 1
    locale_state.record_character_prompt_locale(
        "Neko",
        "zh-TW",
        order=reserved,
    )
    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") == "zh-TW"


@pytest.mark.asyncio
async def test_periodic_rebuttal_uses_durable_character_locale(monkeypatch, tmp_path):
    from app.memory_server import evidence_loops, locale_state, runtime
    from utils.language_utils import get_global_language_full

    observed = []

    class ReflectionEngine:
        async def check_feedback_for_confirmed(
            self,
            name,
            confirmed,
            user_msgs,
        ):
            observed.append(
                (name, confirmed, user_msgs, get_global_language_full())
            )
            return []

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    order = locale_state.reserve_character_prompt_locale_order("Neko")
    locale_state.record_character_prompt_locale("Neko", "zh-TW", order=order)
    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())

    result = await evidence_loops._check_feedback_for_confirmed(
        "Neko",
        [{"id": "r1"}],
        ["我不同意"],
    )

    assert result == []
    assert observed == [
        ("Neko", [{"id": "r1"}], ["我不同意"], "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_reflect_endpoint_uses_durable_character_locale(monkeypatch, tmp_path):
    from app.memory_server import gates, locale_state, routes, runtime
    from utils.language_utils import get_global_language_full

    observed = []

    class ReflectionEngine:
        async def reflect(self, name):
            observed.append(("reflect", name, get_global_language_full()))
            return {"created": 1}

        async def aauto_promote_stale(self, name):
            observed.append(("promote", name, get_global_language_full()))

    async def powerful_enabled():
        return True

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", powerful_enabled)
    monkeypatch.setattr(
        runtime,
        "_spawn_background_task",
        lambda coroutine: coroutine.close(),
    )
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")
    locale_state._locale_cache.clear()

    result = await routes.api_reflect("Neko")
    await routes._safe_auto_promote("Neko")

    assert result["reflection"] == {"created": 1}
    assert observed == [
        ("reflect", "Neko", "zh-TW"),
        ("promote", "Neko", "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_plugin_bootstraps_forward_full_locale(monkeypatch):
    import httpx

    from plugin.plugins.bilibili_dm import BiliDMPlugin
    from plugin.plugins.wechat_integration import WechatIntegrationPlugin
    from utils import language_utils

    calls = []

    class Response:
        is_success = True
        text = "memory"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    class Logger:
        def info(self, *_args):
            return None

        def warning(self, *_args):
            return None

    class Harness:
        logger = Logger()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(language_utils, "get_global_language", lambda: "zh")
    monkeypatch.setattr(
        language_utils,
        "get_global_language_full",
        lambda: "zh-TW",
    )

    await BiliDMPlugin._build_session_instructions(
        Harness(),
        "Neko",
        "Master",
        "character",
        {},
        "admin",
        "123",
        "Master",
    )
    assert await WechatIntegrationPlugin._fetch_memory_context("Neko") == "memory"

    assert [kwargs["params"] for _url, kwargs in calls] == [
        {"language": "zh-TW"},
        {"language": "zh-TW"},
    ]


@pytest.mark.asyncio
async def test_plugin_memory_query_forwards_full_locale(monkeypatch):
    from plugin.server.application.messages import memory_query_service

    calls = []

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"items": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(
        memory_query_service.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(
        memory_query_service,
        "get_global_language_full",
        lambda: "zh-TW",
    )

    result = await memory_query_service.MemoryQueryService().query_memory(
        lanlan_name="Neko",
        query="喜歡貓",
        timeout=5,
    )

    assert result == {"result": {"items": []}}
    assert calls[0][1]["params"] == {"language": "zh-TW"}


def test_persona_correction_locale_ignores_formatter_labels():
    from memory.persona.corrections import _detect_correction_prompt_language
    from utils.language_utils import language_context

    pairs = [(0, {"old_text": "A", "new_text": "B"})]
    with language_context("en"):
        assert _detect_correction_prompt_language(pairs) == "en"
