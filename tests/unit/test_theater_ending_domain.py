"""验证用户 Story Package 的 Ending Domain 从合同证据到 Session 终结完整闭环。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy
import json

import pytest

from services.theater import (
    branch_contracts,
    branch_lifecycle,
    branch_runtime,
    runtime,
    session_store,
    story_loader,
)
from utils.file_utils import atomic_write_json_async


def _user_story(
    *, required_goal_ids: list[str] | None = None, include_conflict_domain: bool = False
) -> dict:
    """构造不依赖仓库示例剧情的最小用户 Story Package。"""  # noqa: DOCSTRING_CJK
    goals = []
    if required_goal_ids:
        goals = [
            {
                "goal_id": goal_id,
                "summary": "作者声明的公开目标",
                "completion_evidence": ["goal_publicly_completed"],
                "converge_to_node_id": "node_open",
                "fallback_convergence_callback": "双方回到当前公开场景。",
            }
            for goal_id in required_goal_ids
        ]
    domains = [
        {
            "ending_domain_id": "domain_mutual_departure",
            "required_goal_ids": list(required_goal_ids or []),
            "required_fact_types": ["observable_action"],
            "required_fact_roles": ["mutual_departure_confirmed"],
            "forbidden_fact_roles": ["unilateral_departure_declared"],
            "ending_id": "ending_authored_departure",
        }
    ]
    allowed_domains = ["domain_mutual_departure"]
    endings = [{"id": "ending_authored_departure", "label": "作者结局"}]
    if include_conflict_domain:
        domains.append(
            {
                "ending_domain_id": "domain_unilateral_departure",
                "required_goal_ids": [],
                "required_fact_types": ["observable_action"],
                "required_fact_roles": ["unilateral_departure_declared"],
                "forbidden_fact_roles": [],
                "ending_id": "ending_authored_unilateral",
            }
        )
        allowed_domains.append("domain_unilateral_departure")
        endings.append({"id": "ending_authored_unilateral", "label": "作者替代结局"})
    return {
        "id": "fixture_user_story",
        "story_revision": "fixture-user-story-v1",
        "title": "用户提供的短剧",
        "summary": "用于验证通用结局域，不代表任何正式剧本内容。",
        "background": "只有玩家与当前猫娘在安静房间中交谈。",
        "theme": "公开确认下一步行动",
        "restrictions": ["只有玩家与当前猫娘参与说话"],
        "initial_scene_id": "scene_open",
        "opening_dialogue": "我们可以从这里开始。",
        "scenes": [
            {
                "id": "scene_open",
                "phase": "setup",
                "title": "安静房间",
                "text": "双方仍在同一公开场景中。",
            }
        ],
        "narrative_nodes": [
            {
                "node_id": "node_open",
                "node_type": "seed",
                "belong_phase": "setup",
                "title": "公开起点",
                "summary": "双方尚未决定如何离开。",
                "suggestions": [],
            },
            {
                "node_id": "node_static_ending",
                "node_type": "ending",
                "belong_phase": "setup",
                "ending_id": "ending_static_path",
                "title": "作者静态落幕",
                "summary": "保证 Story Package 自身仍有无需动态支线的可达结局。",
                "scripted_dialogue": "这条作者路径已经完整结束。",
                "suggestions": [
                    {
                        "choice_id": "choice_static_ending",
                        "label": "沿作者静态路径结束本场演出",
                        "choice_mode": "action",
                        "callback": "双方沿作者声明的静态路径结束本场演出。",
                    }
                ],
            },
        ],
        "edges": [
            {
                "from_node": "node_open",
                "to_node": "node_static_ending",
                "behavior": "continue",
                "meaning": "author_static_ending",
            }
        ],
        "stage_props": [],
        "clues": [],
        "ending_attractors": endings,
        "narrative_goals": goals,
        "ending_domains": domains,
        "world_contract": {
            "speaking_roles": ["player", "active_catgirl"],
            "immutable_facts": [],
            "allowed_dynamic_fact_types": ["observable_action", "spoken_preference"],
            "dynamic_content_slots": [],
            # 通用夹具同样声明不可突破的作者边界，满足真实 Loader 的非空约束。
            "forbidden_changes": ["不得新增未声明的说话角色"],
            "branch_turn_budget": {"default": 3, "max": 3, "max_nonprogress_turns": 2},
            "branch_abort_policy": {
                "mode": "return_to_anchor",
                "neutral_callback": "双方暂停当前尝试，回到仍然公开成立的场景。",
            },
            "allowed_ending_domains": allowed_domains,
            "convergence_goal_ids": list(required_goal_ids or []),
        },
    }


def _domain_patch(
    *, include_conflict_domain: bool = False, fact_type: str = "observable_action"
) -> dict:
    """构造只引用作者结局域、事实角色和类型的 Runtime Branch Patch。"""  # noqa: DOCSTRING_CJK
    roles = ["mutual_departure_confirmed"]
    exits = [{"kind": "ending_domain", "ending_domain_id": "domain_mutual_departure"}]
    if include_conflict_domain:
        roles.append("unilateral_departure_declared")
        exits.append(
            {"kind": "ending_domain", "ending_domain_id": "domain_unilateral_departure"}
        )
    return {
        "origin_node_id": "node_open",
        "seed_intent": "公开确认接下来的共同动作",
        "objective": "只在双方公开确认后进入作者允许的结局",
        "entry_callback": "双方仍在房间中，尚未形成离场事实。",
        "turn_budget": 3,
        "content_slot_ids": [],
        "allowed_new_facts": [
            {"fact_type": fact_type, "fact_role": role, "content_slot_id": ""}
            for role in roles
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_confirm_public_action",
                "objective": "取得作者要求的公开行动证据",
                "observable_action": "双方明确说出并实施下一步行动",
                "exit_preparation": roles,
            }
        ],
        "exit_candidates": exits,
    }


def _goal_patch(goal_id: str) -> dict:
    """构造只通过作者 Goal 证据汇流的通用 Runtime Branch Patch。"""  # noqa: DOCSTRING_CJK
    return {
        "origin_node_id": "node_open",
        "seed_intent": "完成当前公开约定",
        "objective": "取得作者 Goal 要求的公开证据后汇流",
        "entry_callback": "双方仍在当前场景中确认约定。",
        "turn_budget": 3,
        "content_slot_ids": [],
        "allowed_new_facts": [
            {
                "fact_type": "observable_action",
                "fact_role": "goal_publicly_completed",
                "content_slot_id": "",
            }
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_complete_public_goal",
                "objective": "完成作者声明的公开目标",
                "observable_action": "双方公开完成当前约定",
                "exit_preparation": ["goal_publicly_completed"],
            }
        ],
        "exit_candidates": [{"kind": "converge", "goal_id": goal_id}],
    }


def _fact_candidate(
    role: str = "mutual_departure_confirmed",
    *,
    goal_id: str = "",
    predicate: str = "confirmed_next_action",
    object_value: str = "leave_current_room",
) -> dict:
    """构造不携带任何 Ending ID 或服务端身份的公开事实候选。"""  # noqa: DOCSTRING_CJK
    return {
        "goal_id": goal_id,
        "fact_type": "observable_action",
        "fact_role": role,
        "subject": "pair",
        "predicate": predicate,
        "object": object_value,
        "content_slot_id": "",
    }


def _active_branch(story: dict, patch: dict) -> dict:
    """先通过完整合同校验，再由服务端身份激活测试支线。"""  # noqa: DOCSTRING_CJK
    validated = branch_contracts.validate_runtime_branch_patch(
        patch,
        story=story,
        current_node_id="node_open",
    )
    # 测试必须复用正式生命周期构造器，避免手写 active 结构绕过服务端不变量。
    return branch_lifecycle.build_active_runtime_branch(
        validated,
        branch_id="branch_fixture_ending",
        created_revision=0,
        return_anchor={"node_id": "node_open", "goal_id": ""},
        max_nonprogress_turns=2,
    )


async def _continue_active_branch(**kwargs) -> dict:
    """让结局域夹具明确停留在当前支线，避免分类器技术降级遮蔽 Actor 合同。"""  # noqa: DOCSTRING_CJK
    user_message = str(kwargs.get("user_message") or "")
    performed_action = user_message in {
        "现在完成公开约定",
        "现在共同确认并行动",
    }
    return {
        "classification": "continue_branch",
        "intent_summary": "",
        "exit_evidence_excerpt": "",
        "next_evidence_excerpt": "",
        "confidence": 0.96,
        "response_focus": {
            "focus_type": "action" if performed_action else "attitude",
            "evidence_excerpt": user_message,
            "requires_state_change": performed_action,
        },
        "route_delivery": "accepted",
    }


@pytest.mark.asyncio
async def test_user_story_fixture_passes_real_loader_contract(tmp_path):
    """端到端夹具必须经过真实 Loader，不能用 monkeypatch 绕过 Story Package 约束。"""  # noqa: DOCSTRING_CJK
    story = _user_story(include_conflict_domain=True)
    await atomic_write_json_async(
        tmp_path / "fixture-user-story.json",
        story,
        ensure_ascii=False,
        indent=2,
    )
    loaded = await story_loader.list_stories(story_dir=tmp_path)
    assert [item["id"] for item in loaded] == [story["id"]]


def test_ending_domain_patch_must_be_able_to_produce_all_required_evidence():
    """缺少结局域事实角色或类型的 Patch 在激活前整体拒绝。"""  # noqa: DOCSTRING_CJK
    story = _user_story()
    missing_role = _domain_patch()
    missing_role["allowed_new_facts"] = []
    with pytest.raises(ValueError, match="missing ending domain roles"):
        branch_contracts.validate_runtime_branch_patch(
            missing_role,
            story=story,
            current_node_id="node_open",
        )

    wrong_type = _domain_patch(fact_type="spoken_preference")
    with pytest.raises(ValueError, match="missing ending domain fact types"):
        branch_contracts.validate_runtime_branch_patch(
            wrong_type,
            story=story,
            current_node_id="node_open",
        )


def test_ending_domain_checks_required_goals_and_forbidden_roles_deterministically():
    """必需 Goal 缺失时不能结束，禁止角色出现时只能选择仍满足证据的作者 Domain。"""  # noqa: DOCSTRING_CJK
    goal_id = "goal_public_promise"
    goal_story = _user_story(required_goal_ids=[goal_id])
    goal_branch = _active_branch(goal_story, _domain_patch())
    without_goal = branch_runtime.apply_actor_turn(
        story=goal_story,
        active_branch=goal_branch,
        existing_facts=[],
        fact_candidates=[_fact_candidate()],
        source_revision=1,
        completed_goal_ids=[],
    )
    with_goal = branch_runtime.apply_actor_turn(
        story=goal_story,
        active_branch=goal_branch,
        existing_facts=[],
        fact_candidates=[_fact_candidate()],
        source_revision=1,
        completed_goal_ids=[goal_id],
    )
    assert without_goal["ending_domain_id"] == ""
    assert without_goal["decision"] == {"action": "continue", "exit_kind": ""}
    assert with_goal["ending_domain_id"] == "domain_mutual_departure"

    conflict_story = _user_story(include_conflict_domain=True)
    conflict_branch = _active_branch(
        conflict_story,
        _domain_patch(include_conflict_domain=True),
    )
    conflict = branch_runtime.apply_actor_turn(
        story=conflict_story,
        active_branch=conflict_branch,
        existing_facts=[],
        fact_candidates=[
            _fact_candidate("mutual_departure_confirmed"),
            _fact_candidate("unilateral_departure_declared"),
        ],
        source_revision=1,
    )
    assert conflict["ending_domain_id"] == "domain_unilateral_departure"
    assert conflict["decision"]["action"] == "ending_domain"


def test_completed_branch_recall_skips_damaged_history_and_hides_authority_fields():
    """召回只采用合法 History 的精确事实引用，损坏记录和服务端身份都不能进入结果。"""  # noqa: DOCSTRING_CJK
    goal_id = "goal_public_promise"
    story = _user_story(required_goal_ids=[goal_id])
    candidate = _fact_candidate(
        "goal_publicly_completed",
        goal_id=goal_id,
        predicate="completed_public_agreement",
        object_value="shared_next_step",
    )
    committed = branch_contracts.build_committed_branch_fact(
        candidate,
        branch_id="branch_recall_fixture",
        fact_id="branch_fact_recall_fixture",
        source_revision=1,
    )
    valid_history = branch_contracts.validate_branch_history_entry(
        {
            "branch_id": "branch_recall_fixture",
            "completed_goal_ids": [goal_id],
            "key_fact_ids": ["branch_fact_recall_fixture"],
            "exit_kind": "goal_converged",
            "ended_revision": 1,
        },
        story=story,
        branch_facts=[committed],
    )
    damaged_history = deepcopy(valid_history)
    damaged_history["key_fact_ids"] = ["branch_fact_missing"]
    recalled = branch_runtime.completed_branch_recall(
        story=story,
        state={
            "branch_facts": [committed, {"fact_id": "malformed"}],
            "branch_history": [valid_history, damaged_history],
        },
    )
    assert recalled == [
        {
            "completed_goal_summaries": ["作者声明的公开目标"],
            "facts": [
                {
                    "subject": "pair",
                    "predicate": "completed_public_agreement",
                    "object": "shared_next_step",
                }
            ],
        }
    ]
    serialized = json.dumps(recalled, ensure_ascii=False)
    assert "branch_recall_fixture" not in serialized
    assert "branch_fact_recall_fixture" not in serialized
    assert "source_revision" not in serialized


@pytest.mark.asyncio
async def test_converged_user_branch_facts_reach_the_next_ordinary_actor(
    monkeypatch, tmp_path
):
    """Goal 汇流后的下一普通回合必须收到 History 精确召回，而不是依赖最近对白猜测。"""  # noqa: DOCSTRING_CJK
    goal_id = "goal_public_promise"
    story = _user_story(required_goal_ids=[goal_id])

    async def _load_fixture_story(_story_id=None, **_kwargs):
        """所有 Runtime 读取都固定到独立用户 Story Package。"""  # noqa: DOCSTRING_CJK
        return deepcopy(story)

    async def _branch_actor(**_kwargs):
        """用公开事实完成通用 Goal，不携带任何服务端权威字段。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "双方公开完成了当前约定。",
            "dialogue": "这件事已经一起完成了。",
            "fact_candidates": [
                _fact_candidate(
                    "goal_publicly_completed",
                    goal_id=goal_id,
                    predicate="completed_public_agreement",
                    object_value="shared_next_step",
                )
            ],
        }

    monkeypatch.setattr("services.theater.story_loader.load_story", _load_fixture_story)
    monkeypatch.setattr(
        "services.theater.llm.classify_active_branch_handoff_async",
        _continue_active_branch,
    )
    monkeypatch.setattr(
        "services.theater.llm.generate_branch_turn_async", _branch_actor
    )
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root,
        lanlan_name="测试猫娘",
        story_id=story["id"],
        client_start_id="start_fixture_recall",
    )
    session = await session_store.load_session(root, started["session_id"])
    session["story_state"]["active_runtime_branch"] = _active_branch(
        story, _goal_patch(goal_id)
    )
    await session_store.save_session(root, session)

    converged = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="现在完成公开约定",
        client_turn_id="turn_fixture_converge",
        base_revision=0,
    )
    assert converged["state_revision"] == 1

    ordinary_calls: list[dict] = []

    async def _idle_route(**_kwargs):
        """后续询问保持普通回合，避免测试引入新的支线规划。"""  # noqa: DOCSTRING_CJK
        return {
            "route_kind": "idle",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {},
        }

    async def _ordinary_actor(**kwargs):
        """捕获普通 Actor 输入，验证召回链已经接到真实 Turn Service。"""  # noqa: DOCSTRING_CJK
        ordinary_calls.append(kwargs)
        return {
            "narration": "",
            "dialogue": "我记得，我们已经一起完成那项约定了。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _idle_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _ordinary_actor)
    followed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="刚才完成的约定还记得吗？",
        client_turn_id="turn_fixture_recall",
        base_revision=1,
    )
    assert followed["ok"] is True
    assert len(ordinary_calls) == 1
    recall = ordinary_calls[0]["completed_branch_recall"]
    assert recall == [
        {
            "completed_goal_summaries": ["作者声明的公开目标"],
            "facts": [
                {
                    "subject": "pair",
                    "predicate": "completed_public_agreement",
                    "object": "shared_next_step",
                }
            ],
        }
    ]
    assert "branch_" not in json.dumps(recall, ensure_ascii=False)


@pytest.mark.asyncio
async def test_runtime_ending_domain_requires_committed_fact_and_uses_authored_ending(
    monkeypatch, tmp_path
):
    """证据提交、作者 Ending、History、终结快照、active 清理和 TTS 在同一闭环完成。"""  # noqa: DOCSTRING_CJK
    story = _user_story()

    async def _load_fixture_story(_story_id=None, **_kwargs):
        """所有 Runtime 读操作都返回隔离的用户 Story Package。"""  # noqa: DOCSTRING_CJK
        return deepcopy(story)

    actor_outputs = [
        {
            "narration": "双方仍在讨论，还没有公开确认共同动作。",
            "dialogue": "我们先把彼此的意思确认清楚。",
            "fact_candidates": [],
            "ending_id": "model_forged_ending",
        },
        {
            "narration": "双方公开确认后，一起走向门口。",
            "dialogue": "好，我们一起离开这里。",
            "fact_candidates": [_fact_candidate()],
            "ending_id": "model_forged_ending",
        },
    ]

    async def _branch_actor(**_kwargs):
        """模拟 Actor 先闲聊、后产生公开事实，并故意夹带无权威 Ending ID。"""  # noqa: DOCSTRING_CJK
        return actor_outputs.pop(0)

    monkeypatch.setattr("services.theater.story_loader.load_story", _load_fixture_story)
    monkeypatch.setattr(
        "services.theater.llm.classify_active_branch_handoff_async",
        _continue_active_branch,
    )
    monkeypatch.setattr(
        "services.theater.llm.generate_branch_turn_async", _branch_actor
    )

    root = tmp_path / "theater"
    started = await runtime.start_session(
        root,
        lanlan_name="测试猫娘",
        story_id=story["id"],
        client_start_id="start_fixture_ending",
    )
    session = await session_store.load_session(root, started["session_id"])
    session["story_state"]["active_runtime_branch"] = _active_branch(
        story, _domain_patch()
    )
    await session_store.save_session(root, session)

    insufficient = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先再确认一下",
        client_turn_id="turn_domain_insufficient",
        base_revision=0,
    )
    after_insufficient = await session_store.load_session(root, started["session_id"])
    assert insufficient["ending"]["should_end_session"] is False
    assert insufficient["state_revision"] == 1
    assert after_insufficient["story_state"]["active_runtime_branch"]["turns_used"] == 1
    assert after_insufficient["story_state"]["branch_facts"] == []
    assert (
        await session_store.get_active_session_id(root, "测试猫娘")
        == started["session_id"]
    )

    ended = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="现在共同确认并行动",
        client_turn_id="turn_domain_complete",
        base_revision=1,
    )
    saved = await session_store.load_session(root, started["session_id"])
    assert ended["ending"] == {
        "should_offer_ending": True,
        "should_end_session": True,
        "ending_id": "ending_authored_departure",
        "reason": "branch_ending_domain",
    }
    assert "model_forged_ending" not in json.dumps(ended, ensure_ascii=False)
    assert "domain_mutual_departure" not in json.dumps(ended, ensure_ascii=False)
    assert ended["can_resume"] is False
    assert ended["suggestion_options"] == []
    assert saved["ended_at"] is not None
    assert saved["end_reason"] == "branch_ending_domain"
    assert saved["story_state"]["active_runtime_branch"] == {}
    assert len(saved["story_state"]["branch_facts"]) == 1
    assert saved["story_state"]["branch_history"][-1]["exit_kind"] == "ending_domain"
    assert saved["story_state"]["branch_history"][-1]["key_fact_ids"] == [
        saved["story_state"]["branch_facts"][0]["fact_id"]
    ]
    assert await session_store.get_active_session_id(root, "测试猫娘") == ""

    restored = await runtime.get_state(root, started["session_id"])
    assert restored["ending"] == ended["ending"]
    assert restored["can_resume"] is False
    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=2,
    )
    replay = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=2,
    )
    assert claim["line"] == "好，我们一起离开这里。"
    assert replay["skipped"] == "already_spoken"
