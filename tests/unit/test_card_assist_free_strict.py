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


def test_card_assist_free_chat_prompt_is_schema_first():
    prompt = car._build_free_chat_system_prompt(
        "zh",
        "zh-CN",
        "YUI",
        '{"昵称":"阿特拉斯"}',
        "昵称 / 核心特质",
    )

    assert "只返回一个 JSON 对象" in prompt
    assert '"reply"' in prompt
    assert '"actions"' in prompt
    assert "actions 必须包含实际字段操作" in prompt


@pytest.mark.asyncio
async def test_free_full_rewrite_fields_are_converted_to_actions(monkeypatch):
    async def fake_invoke(prompt, **_kwargs):
        assert '"fields"' in prompt
        return (
            '收到 {"fields":{"昵称":"阿特拉斯·静光","核心特质":"清冷、孤独、守序"}}',
            None,
            True,
        )

    async def fake_refine(prompt):
        assert "目标字段名：行为特点" in prompt
        return "在图书馆角落安静巡逻，遇到噪音会轻轻皱眉", None

    monkeypatch.setattr(car, "_invoke_assist_detailed", fake_invoke)
    monkeypatch.setattr(car, "_invoke_assist", fake_refine)

    payload, err = await car._try_free_full_rewrite_response(
        lang="zh",
        locale_code="zh-CN",
        user_instruction="把所有可见字段重新写一遍",
        current_card={"昵称": "阿特拉斯", "核心特质": "清冷", "行为特点": "少言"},
        current_card_text='{"昵称":"阿特拉斯","核心特质":"清冷","行为特点":"少言"}',
        target_keys=["昵称", "核心特质", "行为特点"],
        target_keys_text="昵称 / 核心特质 / 行为特点",
    )

    assert err is None
    assert payload["success"] is True
    assert [a["field_key"] for a in payload["actions"]] == ["昵称", "核心特质", "行为特点"]
    assert payload["actions"][0]["value"] == "阿特拉斯·静光"
    assert payload["actions"][2]["value"] == "在图书馆角落安静巡逻，遇到噪音会轻轻皱眉"


def test_free_chat_empty_actions_retries_for_explicit_edit(monkeypatch):
    monkeypatch.setattr(car, "_reject_untrusted_card_assist", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(car, "_is_current_free_assist_api", lambda: False)

    calls = []

    async def fake_invoke(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if len(calls) == 1:
            transformed = kwargs["free_prompt_transform"](prompt)
            assert "只返回一个 JSON 对象" in transformed[0]["content"]
            return '{"reply":"好哒喵，我来改。","actions":[]}', None, True
        return (
            '{"reply":"改好啦喵。","actions":[{"type":"refine_field","field_key":"招牌台词","value":"星光会替我说话喵~","reason":"按用户要求换一句"}]}',
            None,
            True,
        )

    monkeypatch.setattr(car, "_invoke_assist_detailed", fake_invoke)

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
    assert body["reply"] == "改好啦喵。"
    assert body["actions"] == [
        {
            "type": "refine_field",
            "field_key": "招牌台词",
            "reason": "按用户要求换一句",
            "value": "星光会替我说话喵~",
        }
    ]
    assert len(calls) == 2
