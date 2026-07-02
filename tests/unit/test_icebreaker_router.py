import json
from types import SimpleNamespace

import pytest

from config import (
    ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS,
    ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS,
    ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS,
)
from config._runtime import register_truncate_to_tokens
from config.prompts.prompts_icebreaker import build_icebreaker_free_text_prompts
from main_routers import icebreaker_router, system_router
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from utils.icebreaker_free_text import parse_icebreaker_free_text_decision
from utils import icebreaker_route_state
from utils.game_route_state import _get_active_game_route_state
from utils.tokenize import count_tokens, truncate_to_tokens


class _FakeRequest:
    def __init__(self, payload, *, mutation_headers=True, path="/api/icebreaker/context"):
        self._payload = payload
        self.base_url = "http://127.0.0.1:8000/"
        self.url = SimpleNamespace(path=path)
        self.method = "POST"
        self.headers = {}
        if mutation_headers:
            self.headers = {
                "origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
            }

    async def json(self):
        return self._payload


class _FakeAppendContextManager:
    def __init__(self, result=None, error=None, speech_error=None):
        self.calls = []
        self.spoken = []
        self.language_updates = []
        self.user_language = "zh-CN"
        self.result = result or SimpleNamespace(appended=True, deduped=False, reason=None)
        self.error = error
        self.speech_error = speech_error

    def set_user_language(self, language):
        self.language_updates.append(language)
        self.user_language = language

    async def append_context(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    async def mirror_assistant_speech(self, line, **kwargs):
        self.spoken.append((line, kwargs))
        if self.speech_error is not None:
            raise self.speech_error
        return {"ok": True, "audio_sent": True}


class _FakeConfigManager:
    def __init__(self, characters=None):
        self._characters = characters or {"当前猫娘": "Lan"}

    def load_characters(self):
        return self._characters

    def get_model_api_config(self, model_type):
        assert model_type == "emotion"
        return {
            "model": "fake-emotion-model",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "fake-key",
            "provider_type": "openai_compatible",
        }


class _FakeLlm:
    def __init__(self, content):
        self.content = content
        self.messages = None
        self.closed = False

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.content)

    async def aclose(self):
        self.closed = True


def _allow_local_mutation(request, payload=None, **kwargs):
    return None


def _prompt_field(prompt: str, label: str) -> str:
    prefix = f"{label}："
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise AssertionError(f"missing prompt field: {label}")


async def _fake_cache_memory(**kwargs):
    return True, ""


@pytest.mark.asyncio
async def test_icebreaker_route_start_does_not_activate_game_route(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
    )

    assert result["ok"] is True
    assert result["state"]["icebreaker_active"] is True
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is not None
    assert _get_active_game_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_route_start_falls_back_to_current_character(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    monkeypatch.setattr(icebreaker_router, "get_config_manager", lambda: _FakeConfigManager({"当前猫娘": "YUI"}))

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"session_id": "icebreaker-day1"})
    )

    assert result["ok"] is True
    assert result["state"]["lanlan_name"] == "YUI"
    assert icebreaker_route_state._get_active_icebreaker_route_state("YUI") is not None


@pytest.mark.asyncio
async def test_icebreaker_route_start_requires_local_mutation_csrf(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"}, mutation_headers=False)
    )

    assert result.status_code == 403
    assert b"csrf_validation_failed" in result.body
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_context_endpoint_appends_session_history(monkeypatch):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
        })
    )

    assert result["ok"] is True
    assert result["method"] == "project_session_history"
    assert mgr.calls == [{
        "source": "icebreaker",
        "role": "assistant",
        "text": "教程看完啦？",
        "audience": "model",
        "timing": "when_ready",
        "lifetime": "session_family",
        "request_id": None,
        "ordering_key": "icebreaker-day1-test",
        "metadata": {
            "source": "new_user_icebreaker",
            "session_id": "icebreaker-day1-test",
        },
    }]
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "assistant",
        "text": "教程看完啦？",
    }]


@pytest.mark.asyncio
async def test_icebreaker_context_caches_user_choice_to_recent_memory(monkeypatch):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "user",
            "text": "可以，多陪一会儿",
            "session_id": "icebreaker-day1-test",
            "request_id": "choice-1",
        })
    )

    assert result["ok"] is True
    assert result["memory_cached"] is True
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "user",
        "text": "可以，多陪一会儿",
    }]


@pytest.mark.asyncio
async def test_icebreaker_context_cache_failure_does_not_block_context(monkeypatch, caplog):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return False, "timeout"

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
            "request_id": "line-1",
        })
    )

    assert result["ok"] is True
    assert result["memory_cached"] is False
    assert mgr.calls[0]["text"] == "教程看完啦？"
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "assistant",
        "text": "教程看完啦？",
    }]
    assert "icebreaker memory cache failed" in caplog.text


@pytest.mark.asyncio
async def test_icebreaker_context_deduped_response_keeps_memory_cached_field(monkeypatch):
    mgr = _FakeAppendContextManager(result=SimpleNamespace(appended=False, deduped=True, reason="duplicate"))
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
            "request_id": "line-1",
        })
    )

    assert result["ok"] is True
    assert result["deduped"] is True
    assert result["memory_cached"] is False
    assert memory_cache_calls == []


@pytest.mark.asyncio
async def test_icebreaker_context_falls_back_to_active_session_id(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", _fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "missing session still belongs to the active icebreaker",
        })
    )

    assert result["ok"] is True
    assert result["session_id"] == "active-session"
    assert mgr.calls[0]["ordering_key"] == "active-session"
    assert mgr.calls[0]["metadata"]["session_id"] == "active-session"


@pytest.mark.asyncio
async def test_icebreaker_context_memory_cache_uses_existing_cache_endpoint(monkeypatch):
    calls = []

    async def fake_post_memory_server(endpoint, lanlan_name, payload, *, timeout_s):
        calls.append({
            "endpoint": endpoint,
            "lanlan_name": lanlan_name,
            "payload": payload,
            "timeout_s": timeout_s,
        })
        return True, "", {"status": "cached", "count": 1}

    import main_logic.cross_server as cross_server

    monkeypatch.setattr(cross_server, "_post_memory_server", fake_post_memory_server)

    ok, err = await icebreaker_router._cache_icebreaker_context_memory(
        lanlan_name="Lan",
        role="user",
        text="可以，多陪一会儿",
    )

    assert ok is True
    assert err == ""
    assert calls == [{
        "endpoint": "cache",
        "lanlan_name": "Lan",
        "payload": [{
            "role": "user",
            "content": [{"type": "text", "text": "可以，多陪一会儿"}],
        }],
        "timeout_s": icebreaker_router.ICEBREAKER_MEMORY_CACHE_TIMEOUT_SECONDS,
    }]


def test_icebreaker_free_text_decision_parser_strips_markdown_and_watermark():
    decision = parse_icebreaker_free_text_decision(
        """```json
        {
          "action": "respond_and_keep_options",
          "choice": "",
          "reply": "嗯，本喵听见了。======以上为新用户破冰插话解释器系统提示======"
        }
        ```"""
    )

    assert decision == {
        "action": "respond_and_keep_options",
        "choice": "",
        "reply": "嗯，本喵听见了。",
        "topic_state": "on_topic",
    }


def test_icebreaker_free_text_decision_parser_rejects_invalid_choice():
    decision = parse_icebreaker_free_text_decision(
        '{"action":"choose","choice":"C","reply":"走 C。"}'
    )

    assert decision["action"] == "respond_and_keep_options"
    assert decision["choice"] == ""
    assert decision["reply"] == "走 C。"


def test_icebreaker_free_text_decision_parser_clears_choose_reply():
    decision = parse_icebreaker_free_text_decision(
        '{"action":"choose","choice":"A","reply":"我选 A 喵。","topic_state":"on_topic"}'
    )

    assert decision == {
        "action": "choose",
        "choice": "A",
        "reply": "",
        "topic_state": "on_topic",
    }


def test_icebreaker_free_text_decision_parser_normalizes_topic_state():
    decision = parse_icebreaker_free_text_decision(
        '{"action":"respond_and_keep_options","choice":"","reply":"我听见啦。","topic_state":"soft_derail"}'
    )

    assert decision == {
        "action": "respond_and_keep_options",
        "choice": "",
        "reply": "我听见啦。",
        "topic_state": "soft_derail",
    }


def test_icebreaker_free_text_prompt_keeps_exit_judgment_contextual():
    system_prompt, user_prompt = build_icebreaker_free_text_prompts(
        {
            "i18n_language": "zh-CN",
            "day": "3",
            "node_id": "1A",
            "assistant_line": "第三天，本喵想多争取五分钟。",
            "user_text": "你这句没听懂，能说人话吗",
            "free_text_derail_streak": 1,
            "recent_free_text_turns": [
                {
                    "user_text": "我们来聊赛伯朋克2077吧",
                    "action": "respond_and_keep_options",
                    "reply": "赛伯朋克2077超带感喵！不过先帮本喵选一下压住还是偷看？",
                },
                {
                    "user_text": "不是聊赛伯朋克吗",
                    "action": "respond_and_keep_options",
                    "reply": "赛伯朋克的话题可以续上，不过先看看这两个小选项？",
                },
            ],
        },
        [
            {"choice": "A", "label": "可以，先试五分钟"},
            {"choice": "B", "label": "别得意，稳住"},
        ],
        recent_turns=[
            {
                "user_text": "我们来聊赛伯朋克2077吧",
                "action": "respond_and_keep_options",
                "reply": "赛伯朋克的话题可以续上，不过先看看这两个小选项？",
            },
            {
                "user_text": "不是聊赛伯朋克吗",
                "action": "respond_and_keep_options",
                "reply": "赛伯朋克的话题可以续上，不过先看看这两个小选项？",
            },
        ],
        derail_streak=1,
    )

    assert "不要做关键词匹配式判断" in system_prompt
    assert "结合 YUI 当前台词、当前选项和用户自由输入的整体语义" in system_prompt
    assert "追问当前台词/选项、没听懂、吐槽、调侃、短寒暄" in system_prompt
    assert "最新一句的真实意图优先于次数统计" in system_prompt
    assert "追问当前台词/选项" in system_prompt
    assert "短寒暄" in system_prompt
    assert "当前问题的回答但还不够明确选 A/B" in system_prompt
    assert "soft_derail：用户抛出普通聊天/新任务/现实计划/其他作品或事件" in system_prompt
    assert "明确表达暂时不选、不想继续、随便你决定、要求转入普通聊天" in system_prompt
    assert "已经自然接住并轻轻带回过一次后" in system_prompt
    assert "用户第一次抛出新话题但没有明确拒绝当前选择时" in system_prompt
    assert "优先 respond_and_keep_options" in system_prompt
    assert "如果后续仍坚持新话题，再 release" in system_prompt
    assert "第一次明显转场也可以 release" not in system_prompt
    assert "不需要用户逐字说“退出/跳过”" in system_prompt
    assert "不要按词表或例句匹配" in system_prompt
    assert "是在帮助完成当前破冰选择，还是在发起普通聊天" in system_prompt
    assert "破冰先放一边/晚点再继续" in system_prompt
    assert "GTA6" not in system_prompt
    assert "???" not in system_prompt
    assert "不要反复用“先看选项”" in system_prompt
    assert "当前问题或两个选项的核心差异" in system_prompt
    assert "topic_state 只能是" in system_prompt
    assert "on_topic" in system_prompt
    assert "soft_derail" in system_prompt
    assert "hard_exit" in system_prompt
    assert "连续跑题状态" in user_prompt
    assert "连续跑题状态：1" in user_prompt
    assert "YUI 当前台词：第三天，本喵想多争取五分钟。" in user_prompt
    assert "近期自由输入记录" in user_prompt
    assert "我们来聊赛伯朋克2077吧" in user_prompt
    assert "不是聊赛伯朋克吗" in user_prompt
    assert "用户自由输入：你这句没听懂，能说人话吗" in user_prompt
    assert "choose|respond_and_keep_options|release" in user_prompt
    assert "topic_state" in user_prompt
    assert "那破冰先放一边" not in user_prompt


def test_icebreaker_free_text_prompt_uses_i18n_templates():
    base_payload = {
        "day": "1",
        "node_id": "1A",
        "assistant_line": "先从哪里开始？",
        "user_text": "I want to talk about cars",
        "free_text_derail_streak": 0,
        "recent_free_text_turns": [],
    }
    options = [
        {"choice": "A", "label": "Nicknames first"},
        {"choice": "B", "label": "Chat style first"},
    ]

    zh_system, zh_user = build_icebreaker_free_text_prompts(
        {**base_payload, "i18n_language": "zh-CN"},
        options,
        recent_turns=[],
        derail_streak=0,
    )
    en_system, en_user = build_icebreaker_free_text_prompts(
        {**base_payload, "i18n_language": "en-US"},
        options,
        recent_turns=[],
        derail_streak=0,
    )
    ja_system, ja_user = build_icebreaker_free_text_prompts(
        {**base_payload, "i18n_language": "ja-JP"},
        options,
        recent_turns=[],
        derail_streak=0,
    )
    tw_system, tw_user = build_icebreaker_free_text_prompts(
        {**base_payload, "i18n_language": "zh-TW"},
        options,
        recent_turns=[],
        derail_streak=0,
    )

    assert "你是 N.E.K.O 新用户破冰插话解释器" in zh_system
    assert "回复语言：zh" in zh_user
    assert "You are the N.E.K.O new-user icebreaker free-text interpreter" in en_system
    assert "Reply language: en" in en_user
    assert "回复语言：" not in en_user
    assert "N.E.K.O 新規ユーザー向けアイスブレイク" in ja_system
    assert "返信言語：ja" in ja_user
    assert "N.E.K.O 新使用者破冰插話解釋器" in tw_system
    assert "回覆語言：zh-TW" in tw_user
    assert "回复语言：" not in tw_user

    pt_system, pt_user = build_icebreaker_free_text_prompts(
        {**base_payload, "i18n_language": "pt-BR"},
        options,
        recent_turns=[],
        derail_streak=0,
    )
    assert "Você é o interpretador" in pt_system
    assert "Opções atuais" in pt_user
    assert "Idioma da resposta: pt" in pt_user


def test_icebreaker_free_text_prompt_token_caps_dynamic_fields():
    register_truncate_to_tokens(truncate_to_tokens)

    _, user_prompt = build_icebreaker_free_text_prompts(
        {
            "day": "1",
            "node_id": "1",
            "i18n_language": "zh-CN",
            "assistant_line": "猫" * 3000,
            "user_text": "狗" * 3000,
        },
        [
            {"choice": "A", "label": "甲" * 1200},
            {"choice": "B", "label": "乙" * 1200},
        ],
        recent_turns=[
            {"user_text": "前" * 1000, "action": "respond_and_keep_options", "reply": "回" * 1000}
            for _ in range(8)
        ],
        derail_streak=0,
    )

    assistant_line = _prompt_field(user_prompt, "YUI 当前台词")
    user_text = _prompt_field(user_prompt, "用户自由输入")
    options = json.loads(_prompt_field(user_prompt, "当前选项"))
    turns = json.loads(_prompt_field(user_prompt, "近期自由输入记录"))

    assert count_tokens(assistant_line) <= ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS
    assert count_tokens(user_text) <= ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS
    assert count_tokens(options[0]["label"]) <= ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS
    assert count_tokens(options[1]["label"]) <= ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS
    assert len(turns) <= ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS
    assert count_tokens(turns[0]["user_text"]) <= ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS
    assert count_tokens(turns[0]["reply"]) <= ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS


@pytest.mark.asyncio
async def test_icebreaker_free_text_interpreter_calls_emotion_llm_with_watermark(monkeypatch):
    fake_llm = _FakeLlm('{"action":"choose","choice":"A","reply":""}')
    create_calls = []

    async def fake_create_chat_llm_async(model, base_url, api_key, **kwargs):
        create_calls.append({
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "kwargs": kwargs,
        })
        return fake_llm

    monkeypatch.setattr(icebreaker_router, "get_config_manager", lambda: _FakeConfigManager())
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "create_chat_llm_async", fake_create_chat_llm_async, raising=False)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_free_text_interpret(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "icebreaker-day1-test",
            "day": "1",
            "node_id": "1",
            "i18n_language": "zh-CN",
            "assistant_line": "现在开始跟我聊天吧！",
            "options": [
                {"choice": "A", "label": "可以，先聊五分钟"},
                {"choice": "B", "label": "别得意，稳住"},
            ],
            "user_text": "可以，先试五分钟",
        }, path="/api/icebreaker/free-text/interpret")
    )

    assert result == {"ok": True, "action": "choose", "choice": "A", "reply": "", "topic_state": "on_topic"}
    assert create_calls[0]["model"] == "fake-emotion-model"
    assert create_calls[0]["kwargs"]["provider_type"] == "openai_compatible"
    assert create_calls[0]["kwargs"]["timeout"] == ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS
    assert create_calls[0]["kwargs"]["max_completion_tokens"] == ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS
    assert fake_llm.closed is True
    assert "======以上为新用户破冰插话解释器系统提示======" in fake_llm.messages[0].content
    assert "用户自由输入：可以，先试五分钟" in fake_llm.messages[1].content


@pytest.mark.asyncio
async def test_icebreaker_free_text_interpreter_rejects_missing_session_id(monkeypatch):
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_free_text_interpret(
        _FakeRequest({
            "lanlan_name": "Lan",
            "day": "1",
            "node_id": "1",
            "i18n_language": "zh-CN",
            "assistant_line": "现在开始跟我聊天吧！",
            "options": [
                {"choice": "A", "label": "可以，先聊五分钟"},
                {"choice": "B", "label": "别得意，稳住"},
            ],
            "user_text": "可以，先试五分钟",
        }, path="/api/icebreaker/free-text/interpret")
    )

    assert result["ok"] is False
    assert result["reason"] == "missing_session_id"


@pytest.mark.asyncio
async def test_icebreaker_free_text_interpreter_does_not_absorb_language_before_route_is_active(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)

    result = await icebreaker_router.icebreaker_free_text_interpret(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "icebreaker-day1-test",
            "day": "1",
            "node_id": "1",
            "i18n_language": "ja",
            "assistant_line": "今から話しましょう。",
            "options": [
                {"choice": "A", "label": "はい"},
                {"choice": "B", "label": "あとで"},
            ],
            "user_text": "こんにちは",
        }, path="/api/icebreaker/free-text/interpret")
    )

    assert result["ok"] is False
    assert result["reason"] == "route_not_active"
    assert mgr.language_updates == []


@pytest.mark.asyncio
async def test_icebreaker_context_rejects_stale_session(monkeypatch):
    mgr = _FakeAppendContextManager(error=AssertionError("stale context must not append"))
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "late line",
            "session_id": "old-session",
        })
    )

    assert result["ok"] is True
    assert result["skipped"] == "stale_session"
    assert result["reason"] == "session_id_mismatch"
    assert result["method"] == "project_session_history"
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_icebreaker_speak_uses_independent_project_tts(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
            "request_id": "icebreaker-tts-1",
            "mirror_text": False,
            "emit_turn_end": True,
            "interrupt_audio": True,
        })
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts"
    assert mgr.spoken == [("现在开始跟我聊天吧", {
        "metadata": {
            "source": "new_user_icebreaker",
            "kind": "new_user_icebreaker",
            "session_id": "icebreaker-day1-test",
            "mirror": {
                "kind": "new_user_icebreaker",
                "session_id": "icebreaker-day1-test",
                "event": {},
            },
        },
        "request_id": "icebreaker-tts-1",
        "mirror_text": False,
        "emit_turn_end_after": True,
        "interrupt_audio": True,
    })]


@pytest.mark.asyncio
async def test_icebreaker_speak_coerces_numeric_false_options(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
            "mirror_text": 0,
            "emit_turn_end": 0,
        })
    )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["mirror_text"] is False
    assert mgr.spoken[0][1]["emit_turn_end_after"] is False


@pytest.mark.asyncio
async def test_icebreaker_speak_falls_back_to_active_session_id(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
        })
    )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["metadata"]["session_id"] == "active-session"


@pytest.mark.asyncio
async def test_icebreaker_speak_returns_structured_failure_when_project_tts_fails(monkeypatch):
    mgr = _FakeAppendContextManager(speech_error=RuntimeError("tts down"))
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
        })
    )

    assert result["ok"] is False
    assert result["reason"] == "project_tts_failed"
    assert result["audio_sent"] is False
    assert result["method"] == "project_tts"


@pytest.mark.asyncio
async def test_icebreaker_route_end_clears_only_icebreaker_state(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_route_end(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "icebreaker-day1-test",
            "reason": "icebreaker_handoff",
        })
    )

    assert result["ok"] is True
    assert result["state"]["icebreaker_active"] is False
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is None
    assert _get_active_game_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_route_end_rejects_stale_session(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_route_end(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "old-session",
            "reason": "icebreaker_handoff",
        })
    )

    assert result["ok"] is False
    assert result["reason"] == "session_id_mismatch"
    assert result["method"] == "route_end"
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is not None
