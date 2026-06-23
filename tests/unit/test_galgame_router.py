import asyncio
import json
import logging
import os
import sys
from types import SimpleNamespace

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.prompts.prompts_galgame import get_galgame_fallback_options
from main_routers import galgame_router


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeConfigManager:
    def __init__(self, summary_config):
        self._summary_config = summary_config
        self.calls = []

    async def aget_character_data(self):
        return "主人", "猫娘", None, None

    def get_model_api_config(self, model_type):
        self.calls.append(model_type)
        if model_type == "summary":
            return self._summary_config
        raise AssertionError(f"Unexpected model type: {model_type}")


def _decode_response(response):
    return json.loads(response.body.decode("utf-8"))


def _option_texts(data):
    return [item["text"] for item in data["options"]]


def _expected_llm_kwargs():
    return {
        "max_completion_tokens": galgame_router.GALGAME_OPTION_MAX_TOKENS,
        "provider_type": None,
        "timeout": galgame_router.GALGAME_OPTION_TIMEOUT_SECONDS,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_uses_summary_model_without_temperature(monkeypatch):
    captured = {}
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "options": [
                            {"label": "A", "text": "先确认你刚才说的重点。"},
                            {"label": "B", "text": "我在这里陪你慢慢说。"},
                            {"label": "C", "text": "那就把它变成月亮地图吧。"},
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return FakeLLM()

    monkeypatch.setattr(
        galgame_router,
        "get_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert "fallback" not in data
    assert data["options"][0]["text"] == "先确认你刚才说的重点。"
    assert captured["model"] == "local-summary"
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"
    assert captured["api_key"] == ""
    assert captured["kwargs"] == _expected_llm_kwargs()
    assert config_manager.calls == ["summary"]
    assert "刚才那件事你怎么看？" in captured["messages"][1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_option_generation_timeout_returns_fallback(monkeypatch):
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )
    captured = {}

    class SlowLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            captured["exit_exc_type"] = exc_type
            return None

        async def ainvoke(self, messages):
            await asyncio.sleep(1)
            return SimpleNamespace(content="[]")

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["kwargs"] = kwargs
        return SlowLLM()

    monkeypatch.setattr(galgame_router, "GALGAME_OPTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "What do you think?"}],
                "language": "en",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert data["error"] == "timeout"
    assert _option_texts(data) == list(get_galgame_fallback_options("en"))
    assert captured["kwargs"] == {
        "max_completion_tokens": galgame_router.GALGAME_OPTION_MAX_TOKENS,
        "provider_type": None,
        "timeout": 0.01,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_option_generation_init_error_returns_fallback(monkeypatch):
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    def fake_create_chat_llm(*_args, **_kwargs):
        raise RuntimeError("client init failed")

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "What do you think?"}],
                "language": "en",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert data["error"] == "client init failed"
    assert _option_texts(data) == list(get_galgame_fallback_options("en"))


@pytest.mark.parametrize(
    "model_output, expected",
    [
        # Shape A: top-level label-keyed dict
        (
            {"A": "先确认你刚才说的重点。", "B": "我在这里陪你慢慢说。", "C": "那就把它变成月亮地图吧。"},
            ["先确认你刚才说的重点。", "我在这里陪你慢慢说。", "那就把它变成月亮地图吧。"],
        ),
        # Shape B: nested label-keyed dict under "options"
        (
            {"options": {"A": "认真听。", "B": "陪着你。", "C": "幻想一下。"}},
            ["认真听。", "陪着你。", "幻想一下。"],
        ),
        # Shape C: mixed — top-level provides A only, nested list provides B+C.
        # All three sources must be merged (regression guard for the early-return bug).
        (
            {
                "A": "先确认你刚才说的重点。",
                "options": [
                    {"label": "B", "text": "我在这里陪你慢慢说。"},
                    {"label": "C", "text": "那就把它变成月亮地图吧。"},
                ],
            },
            ["先确认你刚才说的重点。", "我在这里陪你慢慢说。", "那就把它变成月亮地图吧。"],
        ),
        # Shape D: top-level wins on same-label conflicts.
        (
            {
                "A": "顶层 A 才是真正发送的。",
                "options": [
                    {"label": "A", "text": "嵌套 A 应当被忽略。"},
                    {"label": "B", "text": "嵌套 B 正常使用。"},
                    {"label": "C", "text": "嵌套 C 正常使用。"},
                ],
            },
            ["顶层 A 才是真正发送的。", "嵌套 B 正常使用。", "嵌套 C 正常使用。"],
        ),
    ],
    ids=[
        "top_level_label_map",
        "nested_label_map",
        "mixed_top_level_and_nested_list",
        "top_level_wins_on_conflict",
    ],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_accepts_dict_shaped_options(model_output, expected, monkeypatch):
    """Some models emit option maps instead of canonical lists. Don't discard them."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class MapLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps(model_output, ensure_ascii=False))

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **kw: MapLLM())

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert "fallback" not in data
    assert "partial" not in data
    assert _option_texts(data) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_duplicate_labeled_entry_does_not_leak_to_other_labels(monkeypatch):
    """A labeled entry whose slot is already filled must NOT be reused as positional
    fill for a different label — the model intended that text for one specific style,
    re-attributing it would mislabel the user-visible option."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class DupLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "A": "top",
                        "options": [{"label": "A", "text": "dup"}],
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **kw: DupLLM())

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "What do you think?"}],
                "language": "en",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["partial"] is True
    assert data["missing_labels"] == ["B", "C"]
    texts = _option_texts(data)
    fb = get_galgame_fallback_options("en")
    assert texts[0] == "top"
    # Critical: "dup" was labeled A — it must NEVER appear at B or C.
    assert "dup" not in texts[1]
    assert "dup" not in texts[2]
    # Missing labels should be filled from the canonical fallback set.
    assert texts[1] == fb[1]
    assert texts[2] == fb[2]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_partial_options_filled_from_fallback(monkeypatch):
    """Model returned only A and B — C must be filled from fallback, not the whole batch discarded."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    class PartialLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "options": [
                            {"label": "A", "text": "先确认你刚才说的重点。"},
                            {"label": "B", "text": "我在这里陪你慢慢说。"},
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **kw: PartialLLM())

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["partial"] is True
    assert data["missing_labels"] == ["C"]
    fb = get_galgame_fallback_options("zh")
    assert _option_texts(data) == ["先确认你刚才说的重点。", "我在这里陪你慢慢说。", fb[2]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_unparseable_output_returns_fallback(monkeypatch, caplog):
    """Garbage output → full fallback. INFO log must carry metadata only,
    never the raw model text (privacy: the raw output is generated from
    recent chat context)."""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )

    raw_content = "抱歉，我不太理解你的问题。"

    class GarbageLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            return SimpleNamespace(content=raw_content)

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **kw: GarbageLLM())

    # galgame_router.logger is a get_module_logger child whose N.E.K.O ancestor
    # gets configured with propagate=False once any service initializes logging
    # (e.g. the Memory logger during a full-suite import). caplog installs its
    # handler on root, so the INFO record never reaches it. Attach caplog's
    # handler directly to the emitting logger and silence propagation during the
    # call so the record is captured exactly once regardless of global state.
    galgame_logger = galgame_router.logger
    prev_propagate = galgame_logger.propagate
    prev_level = galgame_logger.level
    galgame_logger.addHandler(caplog.handler)
    galgame_logger.propagate = False
    galgame_logger.setLevel(logging.INFO)
    try:
        response = await galgame_router.generate_galgame_options(
            FakeRequest(
                {
                    "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                    "language": "zh-CN",
                }
            )
        )
    finally:
        galgame_logger.removeHandler(caplog.handler)
        galgame_logger.propagate = prev_propagate
        galgame_logger.setLevel(prev_level)

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert _option_texts(data) == list(get_galgame_fallback_options("zh"))

    # The INFO-level fallback log records parse_error + raw_len, but must NOT
    # leak the raw model output (it can carry conversational PII).
    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO and "unparseable" in record.getMessage()
    ]
    assert info_messages, "expected an INFO log entry on unparseable output"
    joined = " ".join(info_messages)
    assert "raw_len=" in joined
    assert "parse_error=" in joined
    assert raw_content not in joined


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_missing_model_base_url_returns_fallback(monkeypatch):
    monkeypatch.setattr(
        galgame_router,
        "get_config_manager",
        lambda: FakeConfigManager({"model": "local-summary", "base_url": "", "api_key": ""}),
    )
    monkeypatch.setattr(
        "utils.llm_client.create_chat_llm",
        lambda *args, **kwargs: pytest.fail("LLM should not be created without a base_url"),
    )

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert "error" not in data
    assert [item["text"] for item in data["options"]] == list(get_galgame_fallback_options("zh"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_options_skipped_when_session_takeover_active(monkeypatch):
    """game route 接管会话期间（语音输入被改路由进游戏逻辑），React composer
    的 galgame 面板不是当前活动界面。此时生成选项只会白烧 summary 档 token，
    所以端点必须短路到 fallback 且**不调用 LLM** —— 与 core.py 里 voice-proactive
    的 `_takeover_active` 守卫对称。"""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )
    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(
        "utils.llm_client.create_chat_llm",
        lambda *args, **kwargs: pytest.fail(
            "LLM must not be called while the session is taken over"
        ),
    )
    monkeypatch.setattr(
        galgame_router,
        "get_session_manager",
        lambda: {"猫娘": SimpleNamespace(_takeover_active=True)},
    )

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
                "lanlan_name": "猫娘",
            }
        )
    )

    data = _decode_response(response)
    assert data["success"] is True
    assert data["fallback"] is True
    assert data["reason"] == "session_takeover"
    assert _option_texts(data) == list(get_galgame_fallback_options("zh"))
    # 守卫必须在解析 summary 模型配置之前短路，summary 档配置不应被读取。
    assert "summary" not in config_manager.calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_galgame_options_generated_when_session_not_taken_over(monkeypatch):
    """守卫只在接管期间短路 —— mgr 存在但未接管时必须照常生成选项。不能因为
    「有活跃（语音）会话」就一刀切拦掉：用户开着聊天窗口用文字选项插话是合法
    用法。"""
    config_manager = FakeConfigManager(
        {
            "model": "local-summary",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "",
        }
    )
    called = {"llm": False}

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            called["llm"] = True
            return SimpleNamespace(
                content=json.dumps(
                    {"A": "认真听。", "B": "陪着你。", "C": "幻想一下。"},
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(galgame_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **k: FakeLLM())
    monkeypatch.setattr(
        galgame_router,
        "get_session_manager",
        lambda: {"猫娘": SimpleNamespace(_takeover_active=False)},
    )

    response = await galgame_router.generate_galgame_options(
        FakeRequest(
            {
                "messages": [{"role": "assistant", "text": "刚才那件事你怎么看？"}],
                "language": "zh-CN",
                "lanlan_name": "猫娘",
            }
        )
    )

    data = _decode_response(response)
    assert called["llm"] is True
    assert data["success"] is True
    assert "fallback" not in data
    assert _option_texts(data) == ["认真听。", "陪着你。", "幻想一下。"]
