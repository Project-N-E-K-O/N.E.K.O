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

    manager = object.__new__(CompressedRecentHistoryManager)
    manager.name_mapping = {"human": "主人"}
    with language_context("zh-TW"):
        prompt = manager._build_summary_prompt("human | 今天想喝咖啡", False)

    assert "資訊豐富" in prompt
    assert "負面回饋" in prompt


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
async def test_qq_bootstrap_requests_forward_full_locale(monkeypatch):
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    calls = []

    class Response:
        text = "ok"

        def raise_for_status(self):
            return None

    class Client:
        async def get(self, _url, **kwargs):
            calls.append(("get", kwargs))
            return Response()

        async def post(self, _url, **kwargs):
            calls.append(("post", kwargs))
            return Response()

    monkeypatch.setattr(QQMemoryBridge, "_client", staticmethod(Client))
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

    assert calls[0][1]["params"] == {"language": "zh-TW"}
    assert calls[1][1]["json"]["language"] == "zh-TW"


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


def test_signal_loop_remembers_latest_session_locale(monkeypatch):
    from app.memory_server import locale_state, signal_extraction

    monkeypatch.setattr(
        locale_state,
        "record_character_prompt_locale",
        lambda _name, language, **_kwargs: language,
    )
    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn("Neko", language="zh-TW")

    assert signal_extraction._signal_check_state["Neko"]["language"] == "zh-TW"


def test_signal_loop_clears_stale_session_locale(monkeypatch):
    from app.memory_server import locale_state, signal_extraction

    monkeypatch.setattr(
        locale_state,
        "record_character_prompt_locale",
        lambda _name, language, **_kwargs: language,
    )
    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn("Neko", language="zh-TW")
    signal_extraction._signal_check_record_turn("Neko")

    assert signal_extraction._signal_check_state["Neko"]["language"] is None


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

    assert signal_extraction._signal_check_state["Neko"]["language"] == "zh-TW"
    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") == "zh-TW"

    # 升级前入队的旧任务没有顺序号，也不能覆盖已有的新状态。
    signal_extraction._signal_check_record_turn("Neko", language="ja")
    assert signal_extraction._signal_check_state["Neko"]["language"] == "zh-TW"


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


def test_persona_correction_locale_ignores_formatter_labels():
    from memory.persona.corrections import _detect_correction_prompt_language
    from utils.language_utils import language_context

    pairs = [(0, {"old_text": "A", "new_text": "B"})]
    with language_context("en"):
        assert _detect_correction_prompt_language(pairs) == "en"
