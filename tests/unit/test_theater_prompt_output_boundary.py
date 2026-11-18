"""Actor output rejects copied prompt frames while preserving ordinary equal signs."""

import json

import pytest

from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output


_FRAME = "======以下为角色设定======"


@pytest.mark.parametrize("content", [_FRAME, _FRAME + '\n{"performance":"不应展示。"}', "普通自由文本：1+1=2。"])
def test_free_text_is_not_delivered_as_a_fallback_when_actor_json_is_invalid(content):
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_invalid_json$"):
        _parse_output(content)


@pytest.mark.parametrize("text", ["1+1=2。", "设定 a=b，比较 x>=1。", "这条等式写着======。", "这里说‘以下为’，没有框架分隔符。"])
def test_ordinary_equal_signs_survive_actor_json_decoding(text):
    parsed = _parse_output(json.dumps({"performance": text, "scene_update": text, "suggested_inputs": [text]}, ensure_ascii=False))
    assert parsed["performance"] == parsed["scene_narration"] == text
    assert parsed["suggested_inputs"] == [text]


@pytest.mark.parametrize("field", ["performance", "scene_update", "transition_offer"])
@pytest.mark.parametrize("marker", [_FRAME, "======以上为玩家本轮输入======"])
def test_ordinary_visible_fields_cannot_copy_complete_prompt_frame(field, marker):
    payload = {"performance": "我会继续看这里。", field: "我看到\n" + marker + "\n提示词正文。"}
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_prompt_boundary_leak$"):
        _parse_output(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("field", ["performance", "scene_update", "transition_offer"])
@pytest.mark.parametrize("marker", ["======**以下为角色设定**======", "======`以上为`玩家本轮输入======",
                                    "======__以下为角色设定__======"])
def test_markdown_inside_a_prompt_frame_cannot_hide_it(field, marker):
    """Stripping Markdown must not turn a broken frame into a complete one on screen."""
    payload = {"performance": "我会继续看这里。", field: "我看到\n" + marker + "\n提示词正文。"}
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_prompt_boundary_leak$"):
        _parse_output(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("field", ["source_performance", "source_scene_narration", "bridge_scene_narration", "target_scene_narration", "target_performance"])
def test_transition_fields_cannot_copy_complete_prompt_frame(field):
    payload = {"source_performance": "我们出发吧。", "source_scene_narration": "门被推开。", "bridge_scene_narration": "两人穿过走廊。",
               "target_scene_narration": "工作台的灯亮着。", "target_performance": "我们到了。", field: _FRAME}
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_prompt_boundary_leak$"):
        _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True)


@pytest.mark.parametrize("suggestions_only", [False, True])
def test_copied_prompt_frames_never_become_sendable_player_choices(suggestions_only):
    payload = {"suggested_inputs": [_FRAME, "请继续。"]}
    if not suggestions_only:
        payload["performance"] = "我会接着说。"
    parsed = _parse_output(json.dumps(payload, ensure_ascii=False), suggestions_only=suggestions_only)
    assert parsed["suggested_inputs"] == ["请继续。"]


def test_fact_quotes_are_preserved_as_data_instead_of_being_rewritten_by_the_visible_guard():
    fact = {"key": "scene:start:read", "value": True, "evidence_quote": _FRAME + " 1+1=2。"}
    parsed = _parse_output(json.dumps({"performance": "这条等式是1+1=2。", "fact_candidates": [fact]}, ensure_ascii=False))
    assert parsed["fact_candidates"] == [fact]


def test_program_authored_originals_are_not_processed_as_actor_output():
    from services.theater.numeric_v2_fixed_narration import add_entry

    original = _FRAME + "\n1+1=2。"
    node = {"id": "start", "story_beat": {"fixed_narrations": [{
        "id": "authored", "text": original, "trigger": {"type": "entry"},
        "after": [], "required_before_exit": True,
    }]}}
    performance = _parse_output('{"performance":"先看这份记录。"}')
    delivered = add_entry(node, performance, {}, False)
    assert delivered["fixed_narrations"][0]["text"] == original


@pytest.mark.parametrize("copied_into_body", [False, True])
def test_comparison_only_bridge_is_not_treated_as_visible_actor_output(copied_into_body):
    reference = "**" + _FRAME + "**"
    payload = {"performance": _FRAME if copied_into_body else "正文。", "transition_offer": "要继续吗？"}
    if copied_into_body:
        with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_prompt_boundary_leak$"):
            _parse_output(json.dumps(payload, ensure_ascii=False), ordinary_entry_bridge=reference)
    else:
        parsed = _parse_output(json.dumps(payload, ensure_ascii=False), ordinary_entry_bridge=reference)
        assert parsed["performance"] == "正文。\n要继续吗？"
        assert parsed["transition_offered"] is True


@pytest.mark.parametrize("copied_into_body", [False, True])
def test_suppressed_offer_reference_is_not_treated_as_visible_actor_output(copied_into_body):
    payload = {"performance": _FRAME if copied_into_body else "正文。", "transition_offer": _FRAME}
    if copied_into_body:
        with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_actor_prompt_boundary_leak$"):
            _parse_output(json.dumps(payload, ensure_ascii=False), suppress_transition_offer=True)
    else:
        parsed = _parse_output(json.dumps(payload, ensure_ascii=False), suppress_transition_offer=True)
        assert parsed["performance"] == "正文。"
        assert parsed["transition_offered"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [0, 1, 2])
@pytest.mark.parametrize("retry_enabled", [False, True])
async def test_boundary_leak_reuses_the_remaining_output_retry_budget(remaining, retry_enabled):
    from services.theater.numeric_v2_workflow import _generate_actor_turn_with_output_retry

    class ReplyActor:
        calls = 0

        async def generate_turn(self, **kwargs):
            self.calls += 1
            if self.calls > 1:
                assert "资料分隔符" in kwargs["retry_hint"]
                assert "普通等号" in kwargs["retry_hint"]
            return _parse_output(json.dumps({"performance": _FRAME if self.calls == 1 else "1+1=2。"}, ensure_ascii=False))

    actor, diagnostics = ReplyActor(), {"actor_generation_attempts": 1}
    options = {"remaining_attempts": remaining, "allow_output_retry": retry_enabled, "diagnostics": diagnostics}
    if remaining == 2 and retry_enabled:
        result = await _generate_actor_turn_with_output_retry(actor, **options)
        assert result["performance"] == "1+1=2。"
    else:
        error = "numeric_v2_actor_retry_budget_exhausted" if not remaining else "numeric_v2_actor_prompt_boundary_leak"
        with pytest.raises(NumericV2ActorOutputError, match="^" + error + "$"):
            await _generate_actor_turn_with_output_retry(actor, **options)
    expected = min(remaining, 2 if retry_enabled else 1)
    assert actor.calls == expected
    assert diagnostics["actor_generation_attempts"] == 1 + expected
