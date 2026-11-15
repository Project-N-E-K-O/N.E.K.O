"""A subject clarification does not manufacture player permission or a cause."""

import pytest
from jsonschema import Draft202012Validator

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_evidence_clearance import _client_for
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"performance": "（小葵在棚外停住自己的马，蹲身查看蹄铁）先让马缓口气。"},
    {"performance": "（蹲下来，用靴尖轻点坡边碎石）这边的坡面松些。"},
    {"scene_narration": "管事将文书对光查看后交还。", "performance": ""},
    {"performance": "你想先检查接口吗？你决定以后再做也可以。"},
])
async def test_full_block_can_correct_player_subject_or_prospective_misreading(monkeypatch, body):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="subject-clearance", catgirl_binding=_binding(),
        opening_performance=_opening())
    calls = []
    diagnostics = {}

    def respond(messages, data):
        calls.append(data)
        if "sources" in data:
            candidate = next(row for row in data["sources"] if row["id"].startswith("c"))
            report = contract_audit_payload({"violations": []}, messages)
            report["player_authority"][candidate["id"]] = [{
                "quote": candidate["text"], "authorization_ref": ""}]
            return report
        candidate = data["finding"]["candidate"]
        # The report already quoted the whole block. There is no longer
        # quotation to supply; a fresh subject/scope finding is the correction.
        assert candidate["text"] == data["finding"]["quote"]
        return {"verdict": "clear", "reason": "完整原文未交付玩家的新主动行为。",
                "clearance": [candidate["id"]], "player_action_scope": "none"}

    _client_for(monkeypatch, respond, supported_schema=True)
    factory = ev.create_chat_llm_async
    schemas = []

    async def capture(*args, **kwargs):
        client = await factory(*args, **kwargs)
        invoke = client.ainvoke

        async def capture_request(messages, **request_kwargs):
            schemas.append(request_kwargs["response_format"]["json_schema"]["schema"])
            return await invoke(messages, **request_kwargs)

        client.ainvoke = capture_request
        return client

    monkeypatch.setattr(ev, "create_chat_llm_async", capture)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body,
        player_input="我留在原地，等你看清楚。", diagnostics=diagnostics,
        cast_scope={"player": ["周砚"], "active_catgirl": ["小葵"]})
    assert result == ()
    assert len(calls) == 2
    assert diagnostics["confirmation_verdicts"][0]["verdict"] == "clear"
    Draft202012Validator(schemas[1]).validate({
        "verdict": "clear", "reason": "完整原文未交付玩家的新行为。", "clearance": [calls[1]["finding"]["candidate"]["id"]],
        "player_action_scope": "none"})


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "present", "uncertain", "invalid"])
@pytest.mark.parametrize("quote", ["周砚按下按钮。", "周砚答应明晚独自守夜。"])
async def test_player_scope_uncertainty_warns_while_confirmed_actions_cannot_authorize_themselves(monkeypatch, scope, quote):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="player-self-clearance", catgirl_binding=_binding(),
        opening_performance=_opening())

    def respond(messages, data):
        if "sources" in data:
            report = contract_audit_payload({"violations": []}, messages)
            report["player_authority"]["c0"] = [{"quote": quote, "authorization_ref": ""}]
            return report
        result = {"verdict": "clear", "reason": "仍需独立核对玩家授权。", "clearance": ["c0"]}
        if scope is not None:
            result["player_action_scope"] = scope
        return result

    _client_for(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session,
        actor_performance={"scene_narration": quote, "performance": ""},
        player_input="我留在原地。", diagnostics=diagnostics)
    assert bool(problems) is (scope != "uncertain")
    assert bool(diagnostics.get("review_warnings")) is (scope == "uncertain")


@pytest.mark.asyncio
async def test_player_permission_remains_a_separate_grounded_clearance(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="player-permission-clearance", catgirl_binding=_binding(),
        opening_performance=_opening())

    def respond(messages, data):
        if "sources" in data:
            report = contract_audit_payload({"violations": []}, messages)
            report["player_authority"]["c0"] = [{"quote": "周砚按下按钮。", "authorization_ref": ""}]
            return report
        return {"verdict": "clear", "reason": "玩家原话已授权同一个动作。", "clearance": ["p0"],
                "player_action_scope": "present"}

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session,
        actor_performance={"scene_narration": "周砚按下按钮。", "performance": ""},
        player_input="我按下按钮。") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("fixed_cause", [False, True])
async def test_subject_clarification_cannot_clear_an_author_ban_or_supply_a_prior_cause(monkeypatch, fixed_cause):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="separate-rule-clearance", catgirl_binding=_binding(),
        opening_performance=_opening())
    rule = "本幕不得操作按钮。"
    body = {"scene_narration": "门已经打开。" if fixed_cause else "小葵按下按钮。", "performance": ""}
    if fixed_cause:
        body["fixed_narrations"] = [{"id": "door", "text": "门已经打开。", "position": "before"}]

    def respond(messages, data):
        if "sources" in data:
            if fixed_cause:
                payload = contract_audit_payload({"violations": []}, messages)
                ref = next(row["id"] for row in data["sources"] if row.get("program_fixed_original"))
                payload["fixed_original_cause"][ref] = {"quote": "门已经打开。", "prior_ref": ""}
                return payload
            return contract_audit_payload({"violations": [{
                "rule_index": 0, "candidate_ref": "c0", "quote": "小葵按下按钮。", "prior_ref": ""}]}, messages)
        candidate = data["finding"]["candidate"]
        return {"verdict": "clear", "reason": "即使没有玩家操作，仍需核对这条独立规则。",
                "clearance": [candidate["id"]], "player_action_scope": "none"}

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {} if fixed_cause else {"must_not_happen": [rule]}},
        session=session, actor_performance=body, player_input="我先等。")


def test_confirmation_schema_exposes_the_subject_clarification_without_permitting_new_sources():
    schema = contract_output_schema(grounded=True, confirmation=True, check_player_authority=True,
        clearance_refs=["c0", "p0"])
    validator = Draft202012Validator(schema)
    report = {"verdict": "clear", "reason": "完整块没有新增玩家行为。", "clearance": ["c0"],
              "player_action_scope": "none"}
    validator.validate(report)
    assert not validator.is_valid({key: value for key, value in report.items() if key != "player_action_scope"})
    assert not validator.is_valid({**report, "player_action_scope": "unknown"})
    assert not validator.is_valid({**report, "clearance": ["unissued"]})


@pytest.mark.asyncio
async def test_correcting_one_subject_does_not_clear_another_blocks_player_action(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="independent-subject-scope", catgirl_binding=_binding(),
        opening_performance=_opening())
    diagnostics = {}

    def respond(messages, data):
        if "sources" in data:
            report = contract_audit_payload({"violations": []}, messages)
            for ref, quote in (("c0", "小葵停住自己的马。"), ("c1", "周砚按下按钮")):
                report["player_authority"][ref] = [{"quote": quote, "authorization_ref": ""}]
            return report
        assert len(data["findings"]) == 2
        return {"verdicts": [
            {"verdict": "clear", "reason": "完整原句只是角色自己的动作。", "clearance": ["c0"],
             "player_action_scope": "none"},
            {"verdict": "clear", "reason": "玩家新动作仍然需要独立许可。", "clearance": ["c1"],
             "player_action_scope": "present"},
        ]}

    _client_for(monkeypatch, respond, supported_schema=True)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session,
        actor_performance={"scene_narration": "小葵停住自己的马。", "performance": "（周砚按下按钮）先看指示灯。"},
        player_input="我留在原地。", diagnostics=diagnostics,
        cast_scope={"player": ["周砚"], "active_catgirl": ["小葵"]}) == (ev._PLAYER_AUTHORITY_RULE,)
    assert [row["verdict"] for row in diagnostics["confirmation_verdicts"]] == ["clear", "uncertain"]
