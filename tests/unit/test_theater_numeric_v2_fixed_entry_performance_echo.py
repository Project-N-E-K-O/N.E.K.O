"""Only an exact original prefix before an independent role reply is redundant."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_performance import performance_content_blocks
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case


ORIGINAL = "文件原文：入库时间为昨日下午三点，未办理出库。"
OTHER = "后续原文：登记员确认上一份登记保持原样，未新增任何出库记录。"
REPLY = "（抬眼看向你）这就是记录的全部内容。你想先核对哪一项？"


def _segment(body, *, position="before", originals=None):
    return {"phase": "target_opening", "scene_narration": "", "performance": body,
            "fixed_narrations": [{"node_id": "target", "id": str(index), "text": text,
                "position": position, "bindings": {"catgirl_name": "小岚", "player_name": "你"}}
                for index, text in enumerate(originals or [ORIGINAL])]}


@pytest.mark.parametrize("reply", [REPLY, "这就是记录的全部内容。", "(抬眼看向你) 这就是记录的全部内容。"])
def test_exact_original_newline_prefix_keeps_only_independent_role_reply(reply):
    target = _segment(ORIGINAL + "\n" + reply)
    pieces = deepcopy(target["fixed_narrations"])
    actor._trim_entry_narration_echo(target)
    assert target["performance"] == reply
    assert target["fixed_narrations"] == pieces
    assert [row["text"] for row in performance_content_blocks(target)].count(ORIGINAL) == 1
    assert not actor._replays_entry_original(target)


def test_ordered_multiple_original_prefixes_are_removed_atomically():
    target = _segment(ORIGINAL + "\n" + OTHER + "\n" + REPLY, originals=[ORIGINAL, OTHER])
    pieces = json.dumps(target["fixed_narrations"], ensure_ascii=False).encode()
    actor._trim_entry_narration_echo(target)
    assert target["performance"] == REPLY
    assert json.dumps(target["fixed_narrations"], ensure_ascii=False).encode() == pieces
    assert [row["text"] for row in performance_content_blocks(target)][:2] == [ORIGINAL, OTHER]


@pytest.mark.parametrize("body", [
    "“" + ORIGINAL + "”\n" + REPLY,
    "我想起了这段话：" + ORIGINAL + "\n" + REPLY,
    ORIGINAL[:-1] + "\n" + REPLY,
    ORIGINAL.replace("，", ",") + "\n" + REPLY,
    ORIGINAL + REPLY,
    ORIGINAL + "\n",
    ORIGINAL + "\n（点头）",
    ORIGINAL + "\n(点头)",
    ORIGINAL + "\n“" + ORIGINAL + "”",
    ORIGINAL + "\n" + OTHER,
    ORIGINAL + "\n" + OTHER + "\n",
])
def test_nonqualifying_repeats_are_left_for_existing_guard_and_whole_body_review(body):
    target = _segment(body, originals=[ORIGINAL, OTHER])
    before = deepcopy(target)
    actor._trim_entry_narration_echo(target)
    assert target == before


def test_after_position_original_is_not_used_to_normalize_role_reply():
    target = _segment(ORIGINAL + "\n" + REPLY, position="after")
    before = deepcopy(target)
    actor._trim_entry_narration_echo(target)
    assert target == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [REPLY, "（目光停在备注栏）记录已经显示出来了，我们继续核对。"])
async def test_normalization_uses_one_provider_call_without_changing_source_or_session(monkeypatch, reply):
    topic = (*CASES[1][:2], ORIGINAL, *CASES[1][3:])
    engine, session, outcome, _ = _case(topic)
    source = {"performance": "那就过去吧。"}
    before = deepcopy((session.to_dict(), outcome.session.to_dict(), source))
    calls = []
    raw = json.dumps({"bridge_scene_narration": "门外的灯亮着。", "target_scene_narration": "",
        "target_entry_trigger": CASES[1][3], "target_performance": ORIGINAL + "\n" + reply,
        "suggested_inputs": ["我先看看这份记录。"]}, ensure_ascii=False)

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            return SimpleNamespace(content=raw)

    async def factory(*args, **kwargs): return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    writer = actor.NumericV2Actor(object())
    result = await writer.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input="好，我们过去。", character_profile="沉着。", transition_source=source)
    assert writer.provider_call_count == len(calls) == 1
    target = result["segments"][2]
    assert target["performance"] == reply.replace("(", "（").replace(")", "）")
    texts = [row["text"] for row in performance_content_blocks(result)]
    assert texts.count(ORIGINAL) == 1
    assert texts.index(CASES[1][3]) < texts.index(ORIGINAL)
    assert result["segments"][0]["performance"] == source["performance"]
    assert (session.to_dict(), outcome.session.to_dict(), source) == before


def test_source_delivery_prompt_keeps_program_stage_labels_out_of_role_time_and_speech():
    system = actor._system_prompt(catgirl_name="小岚", phase="turn", source_delivery_only=True)
    assert "本段不提前执行后续行动；作者资料的本幕/下一幕仅标记程序边界，不作剧中时间或对白。" in system
    assert "也不向玩家解释分段" not in system
