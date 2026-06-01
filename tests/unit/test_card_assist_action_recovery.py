import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import card_assist_router as car


def test_card_assist_lenient_json_extracts_embedded_object():
    parsed = car._loads_json_lenient(
        '好哒喵~ {"reply":"ok","actions":[{"type":"refine_field","field_key":"昵称","value":"阿特拉斯"}]} ✨'
    )

    assert parsed["reply"] == "ok"
    assert parsed["actions"][0]["field_key"] == "昵称"


def test_action_recovery_prompt_is_provider_agnostic():
    prompt = car._build_action_recovery_prompt(
        lang="zh",
        locale_code="zh-CN",
        user_instruction="招牌台词换一句",
        current_card_text='{"招牌台词":"旧台词"}',
        target_keys_text="招牌台词",
        assistant_reply="好哒喵，我来改。",
    )

    assert "动作恢复器" in prompt
    assert "不要回复用户" in prompt
    assert '"actions"' in prompt
    assert "免费" not in prompt
    assert "free" not in prompt.lower()


@pytest.mark.asyncio
async def test_card_assist_uses_agent_model_config_without_watermark(monkeypatch):
    captured = {}

    class FakeConfigManager:
        def __init__(self):
            self.model_types = []
            self.quota_sources = []

        def get_model_api_config(self, model_type):
            self.model_types.append(model_type)
            return {
                "model": "free-agent-model",
                "base_url": "https://www.lanlan.tech/text/v1",
                "api_key": "free-access",
            }

        async def aconsume_agent_daily_quota(self, source="", units=1):
            self.quota_sources.append((source, units))
            return True, {"used": 1, "limit": 500}

    class FakeResponse:
        content = '{"reply":"ok","actions":[]}'

    class FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            return FakeResponse()

        async def aclose(self):
            captured["closed"] = True

    fake_cm = FakeConfigManager()

    def fake_create_chat_llm(model, base_url, api_key, **kwargs):
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return FakeLLM()

    monkeypatch.setattr(car, "get_config_manager", lambda: fake_cm)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    content, err = await car._invoke_assist_detailed("hello")

    assert err is None
    assert content == '{"reply":"ok","actions":[]}'
    assert fake_cm.model_types == ["agent"]
    assert fake_cm.quota_sources == [("card_assist.invoke", 1)]
    assert captured["model"] == "free-agent-model"
    assert captured["base_url"] == "https://www.lanlan.tech/text/v1"
    assert captured["api_key"] == "free-access"
    assert captured["prompt"] == "hello"
    assert "安全水印" not in captured["prompt"]
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_card_assist_quota_exceeded_skips_llm_call(monkeypatch):
    captured = {"ainvoke": 0, "closed": 0}

    class FakeConfigManager:
        def get_model_api_config(self, model_type):
            assert model_type == "agent"
            return {
                "model": "free-agent-model",
                "base_url": "https://www.lanlan.tech/text/v1",
                "api_key": "free-access",
            }

        async def aconsume_agent_daily_quota(self, source="", units=1):
            assert source == "card_assist.invoke"
            assert units == 1
            return False, {"used": 500, "limit": 500}

    class FakeLLM:
        async def ainvoke(self, prompt):
            captured["ainvoke"] += 1
            raise AssertionError("quota failure should skip LLM call")

        async def aclose(self):
            captured["closed"] += 1

    monkeypatch.setattr(car, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda *a, **kw: FakeLLM())

    content, err = await car._invoke_assist_detailed("hello")

    assert content is None
    assert err["error"] == "AGENT_QUOTA_EXCEEDED"
    assert err["code"] == "AGENT_QUOTA_EXCEEDED"
    assert err["details"] == {"used": 500, "limit": 500}
    assert captured == {"ainvoke": 0, "closed": 1}


@pytest.mark.asyncio
async def test_recover_actions_from_reply_uses_original_reply_as_context(monkeypatch):
    async def fake_invoke(prompt):
        assert "上一轮助手回复" in prompt
        assert "好哒喵，我来改。" in prompt
        return (
            '{"actions":[{"type":"refine_field","field_key":"招牌台词","value":"星光会替我说话喵~","reason":"按用户要求换一句"}]}',
            None,
        )

    monkeypatch.setattr(car, "_invoke_assist", fake_invoke)

    actions = await car._recover_actions_from_reply(
        lang="zh",
        locale_code="zh-CN",
        user_instruction="招牌台词换一句",
        current_card_text='{"招牌台词":"旧台词喵~"}',
        target_keys_text="招牌台词",
        assistant_reply="好哒喵，我来改。",
    )

    assert actions == [{
        "type": "refine_field",
        "field_key": "招牌台词",
        "reason": "按用户要求换一句",
        "value": "星光会替我说话喵~",
    }]


@pytest.mark.asyncio
async def test_full_rewrite_completion_fills_missing_actions(monkeypatch):
    async def fake_refine(prompt):
        assert "目标字段名：行为特点" in prompt
        return "在图书馆角落安静巡逻，遇到噪音会轻轻皱眉", None

    monkeypatch.setattr(car, "_invoke_assist", fake_refine)

    actions = await car._complete_full_rewrite_actions(
        lang="zh",
        locale_code="zh-CN",
        actions=[{
            "type": "refine_field",
            "field_key": "昵称",
            "value": "阿特拉斯·静光",
        }],
        user_instruction="把所有可见字段重新写一遍",
        current_card={"昵称": "阿特拉斯", "行为特点": "少言"},
        current_card_text='{"昵称":"阿特拉斯","行为特点":"少言"}',
        target_keys=["昵称", "行为特点"],
    )

    assert [a["field_key"] for a in actions] == ["昵称", "行为特点"]
    assert actions[1]["value"] == "在图书馆角落安静巡逻，遇到噪音会轻轻皱眉"


def test_chat_empty_actions_recovers_actions_without_replacing_reply(monkeypatch):
    monkeypatch.setattr(car, "_reject_untrusted_card_assist", lambda *_args, **_kwargs: None)

    async def fake_invoke_detailed(prompt):
        assert isinstance(prompt, list)
        assert "猫娘助手" in prompt[0]["content"]
        assert "动作恢复器" not in prompt[0]["content"]
        return '{"reply":"好哒喵，我来改。","actions":[]}', None

    async def fake_invoke(prompt):
        assert "动作恢复器" in prompt
        return (
            '{"actions":[{"type":"refine_field","field_key":"招牌台词","value":"星光会替我说话喵~","reason":"按用户要求换一句"}]}',
            None,
        )

    monkeypatch.setattr(car, "_invoke_assist_detailed", fake_invoke_detailed)
    monkeypatch.setattr(car, "_invoke_assist", fake_invoke)

    app = FastAPI()
    app.include_router(car.router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/card-assist/chat",
            json={
                "messages": [{"role": "user", "content": "招牌台词换一句"}],
                "current_card": {"招牌台词": "旧台词喵~"},
                "target_field_keys": ["招牌台词"],
                "locale": "zh-CN",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["reply"] == "好哒喵，我来改。"
    assert body["actions"] == [{
        "type": "refine_field",
        "field_key": "招牌台词",
        "reason": "按用户要求换一句",
        "value": "星光会替我说话喵~",
    }]


def test_chat_action_only_json_does_not_echo_raw_json(monkeypatch):
    monkeypatch.setattr(car, "_reject_untrusted_card_assist", lambda *_args, **_kwargs: None)

    async def fake_invoke_detailed(prompt):
        return (
            '{"actions":[{"type":"refine_field","field_key":"招牌台词","value":"星光会替我说话喵~"}]}',
            None,
        )

    monkeypatch.setattr(car, "_invoke_assist_detailed", fake_invoke_detailed)

    app = FastAPI()
    app.include_router(car.router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/card-assist/chat",
            json={
                "messages": [{"role": "user", "content": "招牌台词换一句"}],
                "current_card": {"招牌台词": "旧台词喵~"},
                "target_field_keys": ["招牌台词"],
                "locale": "zh-CN",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["reply"] == ""
    assert body["actions"][0]["field_key"] == "招牌台词"
