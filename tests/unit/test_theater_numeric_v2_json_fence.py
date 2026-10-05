"""Actor output and history lookup accept the same single complete JSON fence as the Evaluator."""

import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_history as history
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_json import strip_single_json_fence
from tests.unit.test_theater_numeric_v2_history_lookup import _session


ACTOR_RESPONSE = json.dumps({
    "performance": "（抬眼）我听到了。",
    "transition_offered": False,
    "suggested_inputs": [
        "（我点头）我先听你说完。",
        "（我后退一步）我想先看看周围。",
    ],
}, ensure_ascii=False, indent=2)


@pytest.mark.parametrize("language", ["json", "", "JSON"])
def test_actor_output_accepts_single_complete_fence(language):
    plain = _parse_output(ACTOR_RESPONSE)
    fenced = _parse_output(f" \n```{language}\n{ACTOR_RESPONSE}\n```\n ")

    assert fenced == plain
    assert fenced["performance"] == "（抬眼）我听到了。"


@pytest.mark.parametrize("content", [
    f"结果如下：\n```json\n{ACTOR_RESPONSE}\n```",
    f"```json\n{ACTOR_RESPONSE}\n```\n可以继续。",
    f"```json\n{ACTOR_RESPONSE}\n```\n```json\n{ACTOR_RESPONSE}\n```",
    f"```python\n{ACTOR_RESPONSE}\n```",
    f"```json\n{ACTOR_RESPONSE}",
    f"```json\n{ACTOR_RESPONSE[:-1]}\n```",
])
def test_actor_output_fence_does_not_repair_or_extract_json(content):
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_actor_invalid_json"):
        _parse_output(content)


def test_fence_helper_leaves_unfenced_and_non_string_content_unchanged():
    assert strip_single_json_fence(ACTOR_RESPONSE) is ACTOR_RESPONSE
    assert strip_single_json_fence(None) is None
    assert strip_single_json_fence("```json\n{}\n```") == "{}"


@pytest.mark.asyncio
async def test_history_lookup_accepts_fenced_evidence_ids(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            rows = json.loads(messages[1].content)["records"]
            ids = [row["id"] for row in rows if "许可撤回" in row["text"]]
            return SimpleNamespace(content=f"```json\n{json.dumps({'evidence_ids': ids})}\n```")

    async def config(_):
        return {"model": "test", "base_url": "http://invalid.test"}

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(history, "_model_config", config)
    monkeypatch.setattr(history, "create_chat_llm_async", factory)
    result = await history.lookup_history(object(), _session(), "那项许可还有效吗？")

    assert result["status"] == "found"
    assert result["errors"] == []
    assert result["evidence"][0]["text"] == "许可撤回，日记保密。"
