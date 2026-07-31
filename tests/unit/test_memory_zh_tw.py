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
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: object())
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)

    result = await memory_router.commit_external_memory_import(Request())

    assert result["success"] is True
    assert forwarded[0]["language"] == "zh-TW"


def test_signal_loop_remembers_latest_session_locale():
    from app.memory_server import signal_extraction

    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn("Neko", language="zh-TW")

    assert signal_extraction._signal_check_state["Neko"]["language"] == "zh-TW"


def test_signal_loop_clears_stale_session_locale():
    from app.memory_server import signal_extraction

    signal_extraction._signal_check_state.clear()
    signal_extraction._signal_check_record_turn("Neko", language="zh-TW")
    signal_extraction._signal_check_record_turn("Neko")

    assert signal_extraction._signal_check_state["Neko"]["language"] is None
