from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager

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


@pytest.mark.asyncio
async def test_compressed_memo_wrapper_follows_detected_prompt_locale():
    from memory.recent import CompressedRecentHistoryManager
    from utils.language_utils import language_context
    from utils.llm_client import HumanMessage

    manager = object.__new__(CompressedRecentHistoryManager)
    manager.name_mapping = {"human": "Alice"}

    async def invoke(_prompt):
        return "The user enjoys quiet afternoons."

    async def read_anchor(_name):
        return None

    async def write_anchor(_name):
        return None

    manager._invoke_summary_llm = invoke
    manager._aread_last_past_block_update_at = read_anchor
    manager._awrite_last_past_block_update_at = write_anchor

    with language_context("zh-TW"):
        memo, summary = await manager.compress_history(
            [HumanMessage(content="I enjoy quiet afternoons at home.")],
            "Neko",
        )

    assert summary == "The user enjoys quiet afternoons."
    assert memo.content == (
        "Memo from prior conversations: The user enjoys quiet afternoons."
    )


def test_traditional_stale_summary_hint_uses_traditional_delimiters():
    hint = prompts_memory.get_summary_stale_hint("zh-TW", 24)

    assert "======以下為時間衰減提醒======" in hint
    assert "======以上為時間衰減提醒======" in hint
    assert "以下为時間衰減提醒" not in hint
    assert "以上为時間衰減提醒" not in hint


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
async def test_new_dialog_defers_locale_write_while_cloudsave_is_fenced(monkeypatch):
    from app.memory_server import locale_state, routes, runtime
    from utils.cloudsave_runtime import MaintenanceModeError

    class ReachedContextRead(RuntimeError):
        pass

    class ContextReadLock:
        async def __aenter__(self):
            raise ReachedContextRead

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def load_characters():
        return {"猫娘": {"Neko": {}}}

    def blocked_reservation(_name):
        raise MaintenanceModeError(
            "maintenance_readonly",
            operation="save",
            target="prompt_locale.json",
        )

    monkeypatch.setattr(runtime._config_manager, "aload_characters", load_characters)
    monkeypatch.setattr(
        locale_state,
        "reserve_character_prompt_locale_order",
        blocked_reservation,
    )
    monkeypatch.setattr(
        locale_state,
        "record_character_prompt_locale",
        lambda *_args, **_kwargs: pytest.fail(
            "record must be skipped after a fenced reservation"
        ),
    )
    monkeypatch.setattr(runtime, "_get_settle_lock", lambda _name: ContextReadLock())

    with pytest.raises(ReachedContextRead):
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


def test_scoped_prompt_locale_batch_persists_once_per_phase(monkeypatch, tmp_path):
    import json

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    subjects = [
        MemorySubject.group_participant("qq", "7788", "1001"),
        MemorySubject.group_participant("qq", "7788", "1002"),
    ]
    writes = []
    locale_path = tmp_path / "scoped_prompt_locales.json"
    locale_state._subject_locale_cache["BatchNeko"] = {}
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(locale_path),
    )
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )

    def capture_write(path, payload, **kwargs):
        writes.append((path, payload, kwargs))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=kwargs.get("ensure_ascii", True))

    monkeypatch.setattr(locale_state, "atomic_write_json", capture_write)

    orders = locale_state.reserve_subject_prompt_locale_orders(
        "BatchNeko",
        subjects,
    )
    assert len(orders) == 2
    assert len(writes) == 1

    assert locale_state.record_subject_prompt_locales(
        "BatchNeko",
        [
            (subjects[0], "zh-TW", orders[0]),
            (subjects[1], "en", orders[1]),
        ],
    ) == ["zh-TW", "en"]
    assert len(writes) == 2
    assert len(writes[-1][1]["subjects"]) == 2


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_inflight_write_cannot_replace_restored_sidecar(
    monkeypatch,
    tmp_path,
    scoped,
):
    import json

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "RestoreRaceNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    subject_key = locale_state._subject_locale_key(subject)
    old_state = {"language": "en", "order": 1, "reserved_order": 1}
    restored_state = {"language": "zh-TW", "order": 9, "reserved_order": 9}
    initial = {"subjects": {subject_key: old_state}} if scoped else old_state
    restored = (
        {"subjects": {subject_key: restored_state}}
        if scoped
        else restored_state
    )
    locale_path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path" if scoped else "_locale_path",
        lambda _name: str(locale_path),
    )
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )
    locale_state.invalidate_prompt_locale_caches()

    def restore_during_staged_write(path, payload, **_kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        locale_path.write_text(json.dumps(restored), encoding="utf-8")
        locale_state.invalidate_prompt_locale_caches()

    monkeypatch.setattr(
        locale_state,
        "atomic_write_json",
        restore_during_staged_write,
    )

    if scoped:
        locale_state.reserve_subject_prompt_locale_order(name, subject)
        selected = locale_state.get_subject_prompt_locale(name, subject)
    else:
        locale_state.reserve_character_prompt_locale_order(name)
        selected = locale_state.get_character_prompt_locale(name)

    assert selected == "zh-TW"
    assert json.loads(locale_path.read_text(encoding="utf-8")) == restored


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_reload_waits_for_inflight_write(
    monkeypatch,
    tmp_path,
    scoped,
):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "OrdinaryReloadRaceNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    subject_key = locale_state._subject_locale_key(subject)
    old_state = {"language": "en", "order": 1, "reserved_order": 1}
    initial = {"subjects": {subject_key: old_state}} if scoped else old_state
    locale_path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path" if scoped else "_locale_path",
        lambda _name: str(locale_path),
    )
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )
    locale_state.invalidate_prompt_locale_caches()

    started = threading.Event()
    release = threading.Event()
    invalidated = threading.Event()
    original_write = locale_state.atomic_write_json

    def delayed_write(*args, **kwargs):
        original_write(*args, **kwargs)
        started.set()
        assert release.wait(timeout=5)

    def write_locale():
        if scoped:
            return locale_state.record_subject_prompt_locale(
                name, subject, "zh-TW", order=2,
            )
        return locale_state.record_character_prompt_locale(
            name, "zh-TW", order=2,
        )

    def invalidate():
        locale_state.invalidate_prompt_locale_caches()
        invalidated.set()

    monkeypatch.setattr(locale_state, "atomic_write_json", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future = pool.submit(write_locale)
        assert started.wait(timeout=5)
        invalidate_future = pool.submit(invalidate)
        assert not invalidated.wait(timeout=0.1)
        release.set()
        assert write_future.result(timeout=5) == "zh-TW"
        invalidate_future.result(timeout=5)

    selected = (
        locale_state.get_subject_prompt_locale(name, subject)
        if scoped
        else locale_state.get_character_prompt_locale(name)
    )
    assert selected == "zh-TW"


def test_scoped_prompt_locale_forget_erases_row_and_rejects_late_record(
    monkeypatch,
    tmp_path,
):
    import json

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "ForgetLocaleNeko"
    target = MemorySubject.participant("qq", "1001")
    other = MemorySubject.participant("qq", "1002")
    locale_path = tmp_path / "scoped_prompt_locales.json"
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(locale_path),
    )
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )
    locale_state.invalidate_prompt_locale_caches()

    stale_order = locale_state.reserve_subject_prompt_locale_order(name, target)
    locale_state.record_subject_prompt_locale(
        name, target, "zh-TW", order=stale_order,
    )
    other_order = locale_state.reserve_subject_prompt_locale_order(name, other)
    locale_state.record_subject_prompt_locale(
        name, other, "zh-CN", order=other_order,
    )

    assert locale_state.forget_subject_prompt_locale(name, target) == 1
    rows = json.loads(locale_path.read_text(encoding="utf-8"))["subjects"]
    assert locale_state._subject_locale_key(target) not in rows
    assert locale_state._subject_locale_key(other) in rows
    assert locale_state.record_subject_prompt_locale(
        name, target, "zh-TW", order=stale_order,
    ) is None
    rows = json.loads(locale_path.read_text(encoding="utf-8"))["subjects"]
    assert locale_state._subject_locale_key(target) not in rows

    new_order = locale_state.reserve_subject_prompt_locale_order(name, target)
    assert locale_state.record_subject_prompt_locale(
        name, target, "zh-TW", order=new_order,
    ) == "zh-TW"


def test_prompt_locale_writes_honor_cloudsave_fence(monkeypatch):
    from app.memory_server import locale_state
    from memory.scopes import MemorySubject
    from utils.cloudsave_runtime import (
        ROOT_MODE_BOOTSTRAP_IMPORTING,
        MaintenanceModeError,
    )

    name = "FencedNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    character_before = ("zh-CN", 7, 7)
    subjects_before = {"sentinel": ("zh-CN", 8, 8)}
    locale_state._locale_cache[name] = character_before
    locale_state._subject_locale_cache[name] = dict(subjects_before)

    def blocked(target):
        raise MaintenanceModeError(
            ROOT_MODE_BOOTSTRAP_IMPORTING,
            operation="save",
            target=target,
        )

    monkeypatch.setattr(locale_state, "_assert_prompt_locale_writable", blocked)
    writes = (
        lambda: locale_state.reserve_character_prompt_locale_order(name),
        lambda: locale_state.record_character_prompt_locale(
            name,
            "zh-TW",
            order=9,
        ),
        lambda: locale_state.reserve_subject_prompt_locale_order(name, subject),
        lambda: locale_state.record_subject_prompt_locale(
            name,
            subject,
            "zh-TW",
            order=9,
        ),
    )

    for write in writes:
        with pytest.raises(MaintenanceModeError):
            write()

    assert locale_state._locale_cache[name] == character_before
    assert locale_state._subject_locale_cache[name] == subjects_before


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_writes_propagate_final_transaction_fence(
    monkeypatch,
    tmp_path,
    scoped,
):
    from app.memory_server import locale_state
    from memory.scopes import MemorySubject
    from utils.cloudsave_runtime import (
        ROOT_MODE_BOOTSTRAP_IMPORTING,
        MaintenanceModeError,
    )

    name = "FinalFenceNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    path_helper = "_subject_locale_path" if scoped else "_locale_path"
    monkeypatch.setattr(locale_state, path_helper, lambda _name: str(locale_path))
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )

    @contextmanager
    def blocked_transaction(target):
        raise MaintenanceModeError(
            ROOT_MODE_BOOTSTRAP_IMPORTING,
            operation="save",
            target=target,
        )
        yield

    monkeypatch.setattr(
        locale_state,
        "_prompt_locale_write_transaction",
        blocked_transaction,
    )
    locale_state.invalidate_prompt_locale_caches()

    with pytest.raises(MaintenanceModeError):
        if scoped:
            locale_state.record_subject_prompt_locale(
                name,
                subject,
                "zh-TW",
                order=1,
            )
        else:
            locale_state.record_character_prompt_locale(
                name,
                "zh-TW",
                order=1,
            )

    assert not locale_path.exists()


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_write_failure_does_not_publish_cache(
    monkeypatch,
    tmp_path,
    scoped,
):
    import json

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "FailedWriteNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    old_state = {"language": "en", "order": 1, "reserved_order": 1}
    subject_key = locale_state._subject_locale_key(subject)
    payload = {"subjects": {subject_key: old_state}} if scoped else old_state
    locale_path.write_text(json.dumps(payload), encoding="utf-8")
    path_helper = "_subject_locale_path" if scoped else "_locale_path"
    monkeypatch.setattr(locale_state, path_helper, lambda _name: str(locale_path))
    monkeypatch.setattr(
        locale_state,
        "_assert_prompt_locale_writable",
        lambda _target: None,
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(locale_state, "atomic_write_json", fail_write)
    locale_state.invalidate_prompt_locale_caches()

    if scoped:
        locale_state.reserve_subject_prompt_locale_order(name, subject)
        cached = locale_state._subject_locale_cache[name][subject_key]
    else:
        locale_state.reserve_character_prompt_locale_order(name)
        cached = locale_state._locale_cache[name]

    assert cached == ("en", 1, 1)


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

    reserved = []
    recorded = []
    monkeypatch.setattr(runtime, "fact_store", FactStore())
    monkeypatch.setattr(
        locale_state,
        "reserve_subject_prompt_locale_orders",
        lambda name, subjects: (
            reserved.append((name, [subject.key for subject in subjects]))
            or [41, 42]
        ),
    )
    monkeypatch.setattr(
        locale_state,
        "record_subject_prompt_locales",
        lambda name, updates: recorded.extend(
            (name, subject.key, language, order)
            for subject, language, order in updates
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
    assert reserved == [(
        "Neko",
        [
            "group_participant:qq:7788:1001",
            "group_participant:qq:7788:1002",
        ],
    )]
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

    dedup_text = FactDedupResolver._locale_text([("王", "李")])
    refine_text = MemoryRefineEngine._cluster_locale_text([{
        "id": "reflection.abcdef1234567890",
        "text": "怕貓",
        "relation_type": "preference",
        "temporal_scope": "pattern",
    }])
    promotion_line = '[persona.master.fedcba0987654321] "愛狗" (score=1)'
    promotion_text = PromotionMergeMixin._promotion_locale_text(
        {"id": "reflection.abcdef1234567890", "text": "怕貓"},
        [(promotion_line, "愛狗", promotion_line.index("愛狗"))],
        promotion_line,
    )
    synthesis_text = SynthesisMixin._synthesis_locale_text(
        [{"id": "abcdef1234567890", "text": "怕貓", "importance": 5}],
    )
    assert synthesis_text == "怕貓"

    for raw_text in (dedup_text, refine_text, promotion_text, synthesis_text):
        assert detect_prompt_language(raw_text, ui_language="zh-TW") == "zh-TW"


@pytest.mark.parametrize(
    ("ui_language", "text", "expected"),
    [
        ("es", "Me gusta el cafe", "es"),
        ("pt", "Eu gosto de cafe", "pt"),
        ("es", "I prefer quiet afternoons at home.", "en"),
        ("pt", "I prefer quiet afternoons at home.", "en"),
        ("zh-TW", "I like coffee", "en"),
    ],
)
def test_synthesis_keeps_ascii_ui_language(ui_language, text, expected):
    from memory.reflection.synthesis import _detect_synthesis_prompt_language
    from utils.language_utils import language_context

    with language_context(ui_language):
        assert _detect_synthesis_prompt_language(text) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("ui_language", "message_text", "expected"), [
    ("zh-TW", "I prefer quiet afternoons at home.", "en"),
    ("es", "Me gusta el cafe", "es"),
    ("pt", "Eu gosto de cafe", "pt"),
    ("ja", "仕事終了", "ja"),
    ("ja", "体調不良", "ja"),
])
async def test_fact_extractors_detect_prompt_locale_from_message_text(
    monkeypatch, ui_language, message_text, expected,
):
    from unittest.mock import AsyncMock

    from config.prompts import prompts_memory as prompt_module
    from memory import facts
    from utils.language_utils import language_context
    from utils.llm_client import HumanMessage

    selected = []

    def basic_prompt(lang):
        selected.append(("basic", lang))
        return "{CONVERSATION} {LANLAN_NAME} {MASTER_NAME}"

    def batch_prompt(lang):
        selected.append(("batch", lang))
        return "{SEGMENTS} {SEGMENT_NONCE} {LANLAN_NAME}"

    def aware_prompt(lang):
        selected.append(("aware", lang))
        return "{CONVERSATION} {KNOWN_POOL} {LANLAN_NAME} {MASTER_NAME}"

    class ConfigManager:
        async def aget_character_data(self):
            return (None, None, None, None, {"human": "Alice"}, None, None, None, None)

    store = object.__new__(facts.FactStore)
    store._config_manager = ConfigManager()
    store._allm_call_with_retries = AsyncMock(return_value=[])
    messages = [HumanMessage(content=message_text)]

    monkeypatch.setattr(facts, "get_fact_extraction_prompt", basic_prompt)
    monkeypatch.setattr(facts, "get_fact_extraction_batch_prompt", batch_prompt)
    monkeypatch.setattr(
        prompt_module,
        "get_fact_extraction_ai_aware_prompt",
        aware_prompt,
    )

    with language_context(ui_language):
        await store._allm_extract_facts("Neko", messages)
        await store._allm_extract_facts_batch(
            "Neko",
            [{"speaker_label": "Alice", "messages": messages}],
        )
        await store._allm_extract_facts_with_known_pool(
            "Neko",
            messages,
            [],
        )

    assert selected == [
        ("basic", expected),
        ("batch", expected),
        ("aware", expected),
    ]


def test_fact_locale_text_excludes_multimodal_markers():
    from types import SimpleNamespace

    from memory.facts import FactStore
    from utils.language_utils import detect_prompt_language

    messages = [SimpleNamespace(content=[
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
        {"type": "text", "text": "好"},
    ])]

    locale_text = FactStore._messages_locale_text(messages)
    assert locale_text == "好"
    assert detect_prompt_language(locale_text, ui_language="zh-TW") == "zh-TW"


def test_fact_batch_locale_uses_capped_visible_message_bodies(monkeypatch):
    from types import SimpleNamespace

    from memory import facts

    monkeypatch.setattr(facts, "SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS", 64)
    monkeypatch.setattr(facts, "SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS", 128)
    hidden_middle = " english content hidden from the prompt middle" * 200
    body = ("喜歡貓。" * 20) + hidden_middle + ("我很開心。" * 20)
    segments = [{
        "speaker_label": "Alice",
        "messages": [SimpleNamespace(type="human", content=body)],
    }]

    lang, rendered = facts.FactStore._format_speaker_segments_with_locale(
        segments,
        nonce="abcd1234",
        ui_lang="zh-TW",
    )

    assert lang == "zh-TW"
    assert hidden_middle not in rendered
    assert "喜歡貓" in rendered
    assert "我很開心" in rendered


def test_promotion_locale_detection_excludes_truncated_pool_tail():
    from memory.reflection.promotion_merge import PromotionMergeMixin
    from utils.language_utils import detect_prompt_language

    visible = '[persona.master.a] "愛狗" (score=1)'
    hidden = '[persona.master.b] "english words dominate hidden tail" (score=0)'
    raw_text = PromotionMergeMixin._promotion_locale_text(
        {"text": "怕貓"},
        [
            (visible, "愛狗", visible.index("愛狗")),
            (hidden, "english words dominate hidden tail", hidden.index("english")),
        ],
        visible,
    )

    assert raw_text == "怕貓\n愛狗"
    assert detect_prompt_language(raw_text, ui_language="zh-TW") == "zh-TW"


def test_review_locale_detection_ignores_ascii_speaker_labels():
    from memory.recent import _review_prompt_locale_text
    from utils.language_utils import detect_prompt_language

    messages = [{"type": "Alice", "content": "好"} for _ in range(3)]
    raw_text = _review_prompt_locale_text(messages)

    assert "Alice" not in raw_text
    assert detect_prompt_language(raw_text, ui_language="zh-TW") == "zh-TW"


def test_multimodal_locale_detection_uses_text_parts_only():
    from types import SimpleNamespace

    from memory.recent import (
        CompressedRecentHistoryManager,
        _review_prompt_locale_text,
    )
    from utils.language_utils import detect_prompt_language

    content = [
        {"type": "text", "text": "好"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + "A" * 400},
        },
    ]
    message = SimpleNamespace(content=content)
    manager = object.__new__(CompressedRecentHistoryManager)

    review_text = _review_prompt_locale_text([{"content": content}])
    summary_text = manager._summary_prompt_locale_text([message])

    assert review_text == "好"
    assert summary_text == "好"
    assert "|image_url|" in manager._render_message_content(message)
    assert detect_prompt_language(review_text, ui_language="zh-TW") == "zh-TW"
    assert detect_prompt_language(summary_text, ui_language="zh-TW") == "zh-TW"


def test_multimodal_locale_detection_accepts_all_prompt_text_part_types():
    from types import SimpleNamespace

    from memory.recent import (
        CompressedRecentHistoryManager,
        _PROMPT_TEXT_PART_TYPES,
        _review_prompt_locale_text,
    )
    from utils.language_utils import detect_prompt_language

    assert _PROMPT_TEXT_PART_TYPES == {
        None,
        "text",
        "input_text",
        "output_text",
    }
    content = [
        {"type": part_type, "text": "english words"}
        for part_type in _PROMPT_TEXT_PART_TYPES
    ]
    content.append({"type": "image_url", "text": "不應納入"})
    manager = object.__new__(CompressedRecentHistoryManager)

    review_text = _review_prompt_locale_text([{"content": content}])
    summary_text = manager._summary_prompt_locale_text([
        SimpleNamespace(content=content),
    ])

    assert review_text.count("english words") == len(_PROMPT_TEXT_PART_TYPES)
    assert summary_text == review_text
    assert "不應納入" not in review_text
    assert detect_prompt_language(review_text, ui_language="zh-TW") == "en"


def test_summary_locale_detection_uses_prompt_visible_truncation():
    from types import SimpleNamespace

    from memory.recent import CompressedRecentHistoryManager
    from utils.language_utils import detect_prompt_language

    traditional = "這是保留在提示詞中的繁體中文內容。" * 120
    hidden = "english words hidden from the prompt middle " * 500
    message = SimpleNamespace(content=traditional + hidden + traditional)
    manager = object.__new__(CompressedRecentHistoryManager)

    visible = manager._render_message_content(message)
    locale_text = manager._summary_prompt_locale_text([message])

    assert locale_text == visible
    assert "english words hidden" not in locale_text
    assert detect_prompt_language(locale_text, ui_language="zh-TW") == "zh-TW"


@pytest.mark.asyncio
async def test_memory_reload_invalidates_prompt_locale_caches(monkeypatch):
    from app.memory_server import locale_state, runtime

    locale_state._locale_cache["Neko"] = ("zh-CN", 1, 1)
    locale_state._subject_locale_cache["Neko"] = {
        "group": ("zh-CN", 1, 1),
    }
    invalidations = []
    original_invalidate = locale_state.invalidate_prompt_locale_caches

    def invalidate():
        invalidations.append(True)
        original_invalidate()

    def stop_after_invalidation():
        raise RuntimeError("stop after locale cache invalidation")

    monkeypatch.setattr(
        locale_state,
        "invalidate_prompt_locale_caches",
        invalidate,
    )
    monkeypatch.setattr(
        runtime,
        "CompressedRecentHistoryManager",
        stop_after_invalidation,
    )

    assert await runtime.reload_memory_components() is False
    assert invalidations == [True]
    assert locale_state._locale_cache == {}
    assert locale_state._subject_locale_cache == {}


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_cache_invalidation_rejects_inflight_stale_load(
    monkeypatch,
    tmp_path,
    scoped,
):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    name = "RaceNeko"
    subject = MemorySubject.group_chat("qq", "7788")
    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    subject_key = locale_state._subject_locale_key(subject)

    def payload(language):
        row = {"language": language, "order": 1, "reserved_order": 1}
        return {"subjects": {subject_key: row}} if scoped else row

    locale_path.write_text(json.dumps(payload("en")), encoding="utf-8")
    path_helper = "_subject_locale_path" if scoped else "_locale_path"
    monkeypatch.setattr(locale_state, path_helper, lambda _name: str(locale_path))
    locale_state.invalidate_prompt_locale_caches()

    started = threading.Event()
    release = threading.Event()
    load_count = 0
    target_thread_id = None
    original_load = locale_state.json.load

    def delayed_load(handle):
        nonlocal load_count
        loaded = original_load(handle)
        if threading.get_ident() != target_thread_id:
            return loaded
        load_count += 1
        if load_count == 1:
            started.set()
            assert release.wait(timeout=5)
        return loaded

    monkeypatch.setattr(locale_state.json, "load", delayed_load)

    def getter():
        nonlocal target_thread_id
        target_thread_id = threading.get_ident()
        if scoped:
            return locale_state.get_subject_prompt_locale(name, subject)
        return locale_state.get_character_prompt_locale(name)

    with open(locale_path, encoding="utf-8") as handle:
        assert locale_state.json.load(handle) == payload("en")
    assert load_count == 0

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(getter)
        assert started.wait(timeout=5)
        locale_path.write_text(json.dumps(payload("zh-TW")), encoding="utf-8")
        locale_state.invalidate_prompt_locale_caches()
        release.set()
        assert future.result(timeout=5) == "zh-TW"

    assert load_count == 2


def test_game_archive_prompt_language_falls_back_to_global_locale(monkeypatch):
    from main_routers.game_router import archive
    from utils.language_utils import language_context

    class SessionManager:
        @staticmethod
        def get(_name):
            return None

    monkeypatch.setattr(archive, "get_session_manager", SessionManager)

    with language_context("en"):
        assert archive._archive_prompt_language({}) == "en"
        assert archive._archive_prompt_language({"lanlan_name": "Neko"}) == "en"


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


def test_signal_loop_missing_locale_preserves_durable_session_locale(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import locale_state, signal_extraction

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    signal_extraction._signal_check_state.clear()
    locale_state.record_character_prompt_locale(
        "Neko",
        "zh-TW",
        order=100,
    )

    signal_extraction._signal_check_record_turn(
        "Neko",
        locale_order=200,
    )

    locale_state._locale_cache.clear()
    assert locale_state.get_character_prompt_locale("Neko") == "zh-TW"


@pytest.mark.asyncio
async def test_post_turn_counter_stays_on_loop_and_locale_persistence_offloads(
    monkeypatch,
):
    from app.memory_server import gates, post_turn, signal_extraction
    from utils.llm_client import HumanMessage

    calls = []
    events = []

    async def fake_to_thread(function, *args, **kwargs):
        assert events == []
        calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    class StopAfterLocale(Exception):
        pass

    async def stop_after_locale():
        raise StopAfterLocale

    monkeypatch.setattr(post_turn.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        signal_extraction,
        "_signal_check_record_turn",
        lambda *_args, **_kwargs: events.append("counter"),
    )
    monkeypatch.setattr(
        signal_extraction,
        "_signal_check_persist_locale",
        lambda *_args, **_kwargs: events.append("locale"),
    )
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", stop_after_locale)

    with pytest.raises(StopAfterLocale):
        await post_turn._run_post_turn_signals(
            [HumanMessage(content="我喜歡貓")],
            "Neko",
            language="zh-TW",
            locale_order=123,
        )

    assert calls == [
        (
            signal_extraction._signal_check_persist_locale,
            ("Neko",),
            {"language": "zh-TW", "locale_order": 123},
        ),
    ]
    assert events == ["locale", "counter"]


@pytest.mark.asyncio
async def test_post_turn_locale_failure_still_exposes_signal_counter(monkeypatch):
    from app.memory_server import gates, post_turn, signal_extraction
    from utils.llm_client import HumanMessage

    events = []

    async def fail_to_thread(*_args, **_kwargs):
        events.append("locale_failed")
        raise OSError("disk unavailable")

    class StopAfterLocale(Exception):
        pass

    async def stop_after_locale():
        raise StopAfterLocale

    monkeypatch.setattr(post_turn.asyncio, "to_thread", fail_to_thread)
    monkeypatch.setattr(
        signal_extraction,
        "_signal_check_record_turn",
        lambda *_args, **_kwargs: events.append("counter"),
    )
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", stop_after_locale)

    with pytest.raises(StopAfterLocale):
        await post_turn._run_post_turn_signals(
            [HumanMessage(content="我喜歡貓")],
            "Neko",
            language="zh-TW",
            locale_order=123,
        )

    assert events == ["locale_failed", "counter"]


@pytest.mark.asyncio
async def test_post_turn_without_language_restores_durable_locale(monkeypatch, tmp_path):
    from app.memory_server import gates, locale_state, post_turn
    from utils.language_utils import get_global_language_full, language_context

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")

    observed = []

    class StopAfterLocale(Exception):
        pass

    async def stop_after_locale():
        observed.append(get_global_language_full())
        raise StopAfterLocale

    monkeypatch.setattr(post_turn, "_extract_user_messages", lambda _messages: [])
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", stop_after_locale)

    with language_context("en"), pytest.raises(StopAfterLocale):
        await post_turn._run_post_turn_signals([], "Neko", language=None)

    assert observed == ["zh-TW"]


def test_legacy_settings_fallback_uses_traditional_labels():
    from app.memory_server import routes

    rendered = routes._format_legacy_settings_as_text(
        {"Alice": {"喜好": "貓"}},
        "Neko",
        "zh-TW",
    )
    empty = routes._format_legacy_settings_as_text({}, "Neko", "zh-TW")

    assert rendered == "Neko記得：\n關於Alice：\n- 喜好：貓"
    assert empty == "Neko記得：（暫無紀錄）"


@pytest.mark.asyncio
async def test_get_settings_uses_durable_character_locale(monkeypatch, tmp_path):
    from app.memory_server import locale_state, routes, runtime
    from utils.language_utils import get_global_language_full, language_context

    observed = []

    class ConfigManager:
        async def aload_characters(self):
            return {"猫娘": {"Neko": {}}}

    class ReflectionEngine:
        async def aupdate_suppressions(self, _name):
            return None

        async def aget_pending_reflections(self, _name):
            return []

        async def aget_confirmed_reflections(self, _name):
            return []

    class PersonaManager:
        async def arender_persona_markdown(self, _name, _pending, _confirmed):
            observed.append(get_global_language_full())
            return ""

    class SettingsManager:
        def get_settings(self, _name):
            return {"Alice": {"喜好": "貓"}}

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    monkeypatch.setattr(runtime, "_config_manager", ConfigManager())
    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    monkeypatch.setattr(runtime, "persona_manager", PersonaManager())
    monkeypatch.setattr(runtime, "settings_manager", SettingsManager())
    locale_state._locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-TW")

    with language_context("zh-CN"):
        rendered = await routes.get_settings("Neko")

    assert observed == ["zh-TW"]
    assert rendered == "Neko記得：\n關於Alice：\n- 喜好：貓"


@pytest.mark.asyncio
async def test_legacy_recall_placeholder_uses_traditional_locale():
    from app.memory_server import routes

    rendered = await routes.get_memory(
        query="貓",
        lanlan_name="Neko",
        language="zh-TW",
    )

    assert "語意記憶已下線，暫無相關記憶片段" in rendered
    assert "语义记忆已下线" not in rendered


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
async def test_scoped_maintenance_resolvers_receive_subject_locale(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import evidence_loops, locale_state, runtime
    from memory.scopes import MemorySubject

    subject = MemorySubject.group_chat("qq", "7788")
    observed = []

    class DedupResolver:
        async def aresolve(self, name, *, prompt_locale_resolver):
            observed.append((
                "dedup",
                name,
                await prompt_locale_resolver(subject),
            ))
            return 1

    class PersonaManager:
        async def resolve_corrections(self, name, *, prompt_locale_resolver):
            observed.append((
                "correction",
                name,
                await prompt_locale_resolver(subject),
            ))
            return 1

    locale_path = tmp_path / "prompt_locale.json"
    subject_locale_path = tmp_path / "prompt_locale_subjects.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(subject_locale_path),
    )
    monkeypatch.setattr(runtime, "fact_dedup_resolver", DedupResolver())
    monkeypatch.setattr(runtime, "persona_manager", PersonaManager())
    locale_state._locale_cache.clear()
    locale_state._subject_locale_cache.clear()
    locale_state.record_character_prompt_locale("Neko", "zh-CN")
    locale_state.record_subject_prompt_locale("Neko", subject, "zh-TW")

    await evidence_loops._resolve_fact_dedup_with_language("Neko")
    await evidence_loops._resolve_persona_corrections_with_language("Neko")

    assert observed == [
        ("dedup", "Neko", "zh-TW"),
        ("correction", "Neko", "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_post_turn_correction_resolver_receives_subject_locale(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import locale_state, post_turn, runtime
    from memory.scopes import MemorySubject

    subject = MemorySubject.group_chat("qq", "7788")
    observed = []

    class PersonaManager:
        async def resolve_corrections(self, name, *, prompt_locale_resolver):
            observed.append((
                name,
                await prompt_locale_resolver(subject),
            ))
            return 1

    subject_locale_path = tmp_path / "prompt_locale_subjects.json"
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(subject_locale_path),
    )
    monkeypatch.setattr(runtime, "persona_manager", PersonaManager())
    locale_state._subject_locale_cache.clear()
    locale_state.record_subject_prompt_locale("Neko", subject, "zh-TW")

    result = await post_turn._resolve_corrections_with_subject_locale("Neko")

    assert result == 1
    assert observed == [("Neko", "zh-TW")]


@pytest.mark.asyncio
async def test_reflection_refine_partitions_subject_locales(
    monkeypatch,
    tmp_path,
):
    from app.memory_server import locale_state, refine_loops, runtime
    from memory.scopes import MemorySubject
    from utils.language_utils import get_global_language_full, language_context

    subject = MemorySubject.group_chat("qq", "7788")
    reflections = [
        {"id": "legacy", "entity": "master"},
        {"id": "scoped", "entity": "master", **subject.as_entry_fields()},
    ]
    observed = []

    class ReflectionEngine:
        async def aload_reflections(self, _name, *, include_archived):
            assert include_archived is False
            return reflections

    async def run_batch(name, *, subject=None, max_clusters=None):
        observed.append((name, subject, get_global_language_full()))
        return 1

    subject_locale_path = tmp_path / "prompt_locale_subjects.json"
    monkeypatch.setattr(
        locale_state,
        "_subject_locale_path",
        lambda _name: str(subject_locale_path),
    )
    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    monkeypatch.setattr(
        refine_loops,
        "_run_reflection_refine_for_character",
        run_batch,
    )
    locale_state._subject_locale_cache.clear()
    locale_state.record_subject_prompt_locale("Neko", subject, "zh-TW")

    with language_context("zh-CN"):
        await refine_loops._run_reflection_refine_with_subject_locales("Neko")

    assert observed == [
        ("Neko", None, "zh-CN"),
        ("Neko", subject, "zh-TW"),
    ]


@pytest.mark.asyncio
async def test_reflection_refine_shares_budget_and_rotates_subjects(monkeypatch):
    from app.memory_server import refine_loops, runtime
    from config import MEMORY_REFINE_CLUSTERS_PER_PASS
    from memory.scopes import MemorySubject

    first = MemorySubject.group_chat("qq", "7788")
    second = MemorySubject.group_chat("qq", "9900")
    reflections = [
        {"id": "legacy", "entity": "master"},
        {"id": "first", "entity": "master", **first.as_entry_fields()},
        {"id": "second", "entity": "master", **second.as_entry_fields()},
    ]
    calls = []

    class ReflectionEngine:
        async def aload_reflections(self, _name, *, include_archived):
            assert include_archived is False
            return reflections

    async def run_batch(name, *, subject=None, max_clusters=None):
        calls.append((name, subject, max_clusters))
        return max_clusters

    async def no_subject_locale(_name, _subject):
        return None

    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    monkeypatch.setattr(
        refine_loops,
        "_run_reflection_refine_for_character",
        run_batch,
    )
    monkeypatch.setattr(
        refine_loops,
        "aget_subject_prompt_locale",
        no_subject_locale,
    )
    refine_loops._reflection_refine_subject_cursor.pop("BudgetNeko", None)

    await refine_loops._run_reflection_refine_with_subject_locales("BudgetNeko")
    await refine_loops._run_reflection_refine_with_subject_locales("BudgetNeko")

    assert calls == [
        ("BudgetNeko", None, MEMORY_REFINE_CLUSTERS_PER_PASS),
        ("BudgetNeko", first, MEMORY_REFINE_CLUSTERS_PER_PASS),
    ]


@pytest.mark.asyncio
async def test_reflection_refine_filters_candidate_pool_to_subject(monkeypatch):
    from app.memory_server import refine_loops, runtime
    from memory import refine as memory_refine
    from memory.scopes import MemorySubject

    subject = MemorySubject.group_chat("qq", "7788")
    other = MemorySubject.group_chat("qq", "9900")
    reflections = [
        {
            "id": "target-reflection",
            "entity": "master",
            "text": "好",
            **subject.as_entry_fields(),
        },
        {
            "id": "other-reflection",
            "entity": "master",
            "text": "嗯",
            **other.as_entry_fields(),
        },
    ]
    facts = [
        {
            "id": "target-fact",
            "entity": "master",
            "text": "好",
            "absorbed": True,
            **subject.as_entry_fields(),
        },
        {
            "id": "other-fact",
            "entity": "master",
            "text": "嗯",
            "absorbed": True,
            **other.as_entry_fields(),
        },
    ]
    captured = []

    class ReflectionEngine:
        async def aload_reflections(self, _name, *, include_archived):
            assert include_archived is False
            return reflections

        async def apply_refine_actions(self, *_args, **_kwargs):
            raise AssertionError("no apply expected")

        async def _abump_refine_attempts(self, *_args, **_kwargs):
            raise AssertionError("no failure expected")

    class FactStore:
        async def aload_facts(self, _name):
            return facts

    class RefineEngine:
        def __init__(self, _config_manager):
            pass

        async def refine_pass(self, candidates, **_kwargs):
            captured.append(candidates)
            return {
                "clusters_seen": 0,
                "clusters_skipped": 0,
                "clusters_resolved": 0,
                "clusters_failed": 0,
            }

    monkeypatch.setattr(runtime, "reflection_engine", ReflectionEngine())
    monkeypatch.setattr(runtime, "fact_store", FactStore())
    monkeypatch.setattr(runtime, "_config_manager", object())
    monkeypatch.setattr(memory_refine, "MemoryRefineEngine", RefineEngine)

    await refine_loops._run_reflection_refine_for_character(
        "Neko",
        subject=subject,
    )

    assert len(captured) == 1
    assert {
        item["id"]
        for item in captured[0]["master"]
    } == {"target-reflection", "target-fact"}


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

    monkeypatch.setattr(
        fusion,
        "detect_prompt_language_with_ascii_fallback",
        detect,
    )

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


@pytest.mark.parametrize("scoped", [False, True])
def test_prompt_locale_final_replace_holds_cloud_write_transaction(
    monkeypatch,
    tmp_path,
    scoped,
):
    from app.memory_server import locale_state
    from memory.scopes import MemorySubject

    locale_path = tmp_path / (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    path_helper = "_subject_locale_path" if scoped else "_locale_path"
    expected_target = (
        "scoped_prompt_locales.json" if scoped else "prompt_locale.json"
    )
    active = False
    original_replace = locale_state.os.replace

    @contextmanager
    def transaction(target):
        nonlocal active
        assert target == expected_target
        active = True
        try:
            yield
        finally:
            active = False

    def guarded_replace(source, target):
        if str(target) == str(locale_path):
            assert active
        return original_replace(source, target)

    monkeypatch.setattr(locale_state, path_helper, lambda _name: str(locale_path))
    monkeypatch.setattr(locale_state, "_assert_prompt_locale_writable", lambda _target: None)
    monkeypatch.setattr(locale_state, "_prompt_locale_write_transaction", transaction)
    monkeypatch.setattr(locale_state.os, "replace", guarded_replace)
    locale_state.invalidate_prompt_locale_caches()

    if scoped:
        locale_state.record_subject_prompt_locale(
            "Neko",
            MemorySubject.group_chat("qq", "7788"),
            "zh-TW",
        )
    else:
        locale_state.record_character_prompt_locale("Neko", "zh-TW")

    assert locale_path.exists()


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ui_language", "message_text"),
    [
        ("es", "No me gusta esto"),
        ("pt", "Eu gosto de cafe"),
    ],
)
async def test_reflection_feedback_keeps_ascii_ui_language(
    monkeypatch,
    ui_language,
    message_text,
):
    from config.prompts import prompts_memory
    from memory.reflection import surfacing
    from utils import llm_client
    from utils.language_utils import language_context

    selected = []

    def feedback_prompt(language):
        selected.append(language)
        return "{reflections} {messages}"

    class Response:
        content = "[]"

    class LLM:
        async def ainvoke(self, _prompt):
            return Response()

        async def aclose(self):
            return None

    async def create_llm(*_args, **_kwargs):
        return LLM()

    class ConfigManager:
        async def aget_model_api_config(self, _tier):
            return {
                "model": "test",
                "base_url": "http://test",
                "api_key": "test",
            }

    class Harness(surfacing.SurfacingMixin):
        def __init__(self):
            self._config_manager = ConfigManager()

        async def aload_surfaced(self, _name):
            return [{
                "reflection_id": "reflection.1",
                "text": "likes coffee",
                "feedback": None,
            }]

        async def asave_surfaced(self, _name, _surfaced):
            return None

    monkeypatch.setattr(
        prompts_memory,
        "get_reflection_feedback_prompt",
        feedback_prompt,
    )
    monkeypatch.setattr(llm_client, "create_chat_llm_async", create_llm)

    harness = Harness()
    with language_context(ui_language):
        await harness._check_feedback_locked("Neko", [message_text])
        await harness.check_feedback_for_confirmed(
            "Neko",
            [{"id": "reflection.1", "text": "likes coffee"}],
            [message_text],
        )

    assert selected == [ui_language, ui_language]


@pytest.mark.parametrize(
    ("ui_language", "text", "expected"),
    [
        ("es", "Me gusta el cafe", "es"),
        ("pt", "Eu gosto de cafe", "pt"),
        ("zh-TW", "I like coffee", "en"),
    ],
)
def test_persona_correction_keeps_ascii_ui_language(
    ui_language,
    text,
    expected,
):
    from memory.persona.corrections import _detect_correction_prompt_language

    pairs = [(0, {"old_text": text, "new_text": text})]
    assert _detect_correction_prompt_language(
        pairs,
        ui_language=ui_language,
    ) == expected


@pytest.mark.parametrize(
    ("ui_language", "text", "expected"),
    [
        ("es", "Me gusta el cafe", "es"),
        ("pt", "Eu gosto de cafe", "pt"),
        ("en", "I like coffee", "en"),
    ],
)
def test_persona_fusion_keeps_ascii_ui_language(
    ui_language,
    text,
    expected,
):
    from memory.persona.fusion import _detect_fusion_prompt_language
    from utils.language_utils import language_context

    with language_context(ui_language):
        assert _detect_fusion_prompt_language(text) == expected


@pytest.mark.parametrize(
    ("ui_language", "text", "expected"),
    [
        ("es", "Me gusta el cafe", "es"),
        ("pt", "Eu gosto de cafe", "pt"),
        ("zh-TW", "I like coffee", "en"),
    ],
)
def test_remaining_memory_mutations_keep_ascii_ui_language(
    ui_language,
    text,
    expected,
):
    from memory.fact_dedup import _detect_fact_dedup_prompt_language
    from memory.recent import _detect_recent_prompt_language
    from memory.refine import _detect_refine_prompt_language
    from memory.reflection.promotion_merge import (
        _detect_promotion_prompt_language,
    )
    from memory.scoped_refine import _detect_scoped_refine_prompt_language
    from utils.language_utils import language_context

    resolvers = (
        _detect_recent_prompt_language,
        _detect_refine_prompt_language,
        _detect_scoped_refine_prompt_language,
        _detect_promotion_prompt_language,
        lambda value: _detect_fact_dedup_prompt_language(
            value,
            ui_language=ui_language,
        ),
    )
    with language_context(ui_language):
        assert [resolver(text) for resolver in resolvers] == [expected] * len(
            resolvers
        )
