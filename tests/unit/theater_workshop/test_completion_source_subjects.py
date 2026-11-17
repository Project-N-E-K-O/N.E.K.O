"""Choice facts retain their deciding subject through staged writing and compilation."""

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater.numeric_v2_runtime import NumericV2RuntimeError, validate_fact_candidates
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from .test_numeric_v2_generation import _generation_setup, _idea_outline
from .test_staged_generation import staged_reply


CASES = (
    ("player", "玩家单方", "player_input", "我已经选定目视核验。", "我知道你的选择了。"),
    ("shared", "双方共同", None, "我已经选定目视核验。", "我也选定目视核验。"),
    ("catgirl", "女主自主", "performance", "请你按自己的判断选择。", "我已经选定目视核验。"),
    ("npc", "保管员自主", "performance", "请保管员按自己的判断选择。", "保管员说：“我已选定目视核验。”"),
)


@pytest.fixture(scope="module", params=CASES, ids=[case[0] for case in CASES])
def generated_choice(request):
    name, subject, source, player_words, actor_words = request.param
    outline = _idea_outline(3)
    fact = {"id": "verification_choice", "description": subject + "选定的核对方式为目视核验或纸面拓印。",
            "value_type": "string", "target_value": "目视核验", "visibility": "public"}
    if source is not None:
        fact["evidence_source"] = source
    outline["mainline_chapters"][0]["completion_facts"].append(fact)
    prompts = []

    def model(messages, **options):
        if options["operation"] == "numeric_v2_chapter_generation":
            prompts.append(messages[0]["content"])
        return staged_reply(messages, options["operation"], outline)

    story = NumericV2Generator(model).generate_staged(title="选择来源", setup=_generation_setup())["story"]
    compiled = NumericV2Compiler().compile_v2_2(story)
    key = "scene:mainline_01:verification_choice"
    candidate = {"op": "set", "key": key, "value": "目视核验", "visibility": "public",
                 "confidence": "confirmed", "subject": subject, "action": "选定", "object": "核对方式",
                 "result": "目视核验", "evidence": []}
    return name, source, player_words, actor_words, compiled.story, prompts, candidate


def test_real_staged_request_scopes_specific_choice_sources_by_deciding_subject(generated_choice):
    _, _, _, _, _, prompts, _ = generated_choice
    assert len(prompts) == 3
    for prompt in prompts:
        assert "具体选项的证据来源沿用完成事实规则；仅玩家单方决定时标 evidence_source=player_input" in prompt
        assert "双方共同决定和固定展示须省略 evidence_source" in prompt
        assert "角色、配角或环境交付的结果须标 evidence_source=performance" in prompt
        assert "此字符串事实不列入共用出口的 trigger_fact_ids 或 entry_prerequisite_fact_ids" in prompt


def test_compiled_choice_fact_accepts_evidence_from_its_actual_deciding_subject(generated_choice):
    name, source, player_words, actor_words, story, _, candidate = generated_choice
    key = candidate["key"]
    definition = story["fact_contract"]["facts"][key]
    assert definition["value_type"] == "string" and definition["visibility"] == "public"
    if source is None:
        assert "evidence_source" not in definition
    else:
        assert definition["evidence_source"] == source
    evidence = []
    if name in {"player", "shared"}:
        evidence.append({"source": "player_input", "quote": player_words})
    if name != "player":
        evidence.append({"source": "actor_performance", "quote": actor_words})
    candidate = {**candidate, "evidence": evidence}
    operations, audit = validate_fact_candidates([candidate], fact_contract=story["fact_contract"],
        evidence_sources={"player_input": player_words, "actor_performance": actor_words})
    assert operations == ({"op": "set", "key": key, "value": "目视核验", "visibility": "public"},)
    assert audit[0]["evidence"] == evidence
    # The selected value is retained for later scenes without locking the shared exit to one option.
    node = story["nodes"][0]
    assert all(row.get("key") != key for row in node["completion_contract"]["all"])
    assert all(condition.get("key") != key for route in node["route_gates"]
               for condition in route["conditions"]["all"])


    if source is not None:
        wrong_source, wrong_words = (("actor_performance", actor_words) if source == "player_input"
                                     else ("player_input", player_words))
        with pytest.raises(NumericV2RuntimeError, match="story_fact_candidate_evidence_source_not_allowed"):
            validate_fact_candidates([{**candidate, "evidence": [{"source": wrong_source, "quote": wrong_words}]}],
                fact_contract=story["fact_contract"],
                evidence_sources={"player_input": player_words, "actor_performance": actor_words})
