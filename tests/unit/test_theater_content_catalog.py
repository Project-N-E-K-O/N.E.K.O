"""验证动态内容目录的零模型授权、提交与恢复边界。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.theater import (
    branch_contracts,
    branch_lifecycle,
    branch_runtime,
    story_loader,
)
from utils.file_utils import atomic_write_json_async, read_json_async
from tests.utils.theater_story_fixture import (
    THEATER_TEST_ANCHOR_NODE_ID,
    THEATER_TEST_GOAL_ID,
    THEATER_TEST_SLOT_ID,
    THEATER_TEST_STORY_ID,
    THEATER_TEST_STORY_PATH,
)

CATALOG_CASES = (
    {
        "slot_id": "slot_stationery_sample",
        "fact_type": "ordinary_local_prop",
        "allowed_traits": ["stationery", "locally_available"],
        "forbidden_traits": ["relationship_commitment_symbol"],
        "content_id": "content_black_ink",
        "entity_kind": "prop",
        "label": "黑色墨水",
        "fact_object": "black_ink_bottle",
        "traits": ["stationery", "locally_available", "ordinary_gift"],
        "status": "selected",
    },
    {
        "slot_id": "slot_orbital_repair_sample",
        "fact_type": "ordinary_local_prop",
        "allowed_traits": ["repair_component", "non_weapon"],
        "forbidden_traits": ["weapon"],
        "content_id": "content_ceramic_fuse",
        "entity_kind": "prop",
        "label": "陶瓷熔断器",
        "fact_object": "ceramic_fuse_type_7",
        "traits": ["repair_component", "non_weapon", "hand_carryable"],
        "status": "available",
    },
    {
        "slot_id": "slot_archive_trace_sample",
        "fact_type": "observable_action",
        "allowed_traits": ["archival", "publicly_observable"],
        "forbidden_traits": ["private_thought"],
        "content_id": "content_watermarked_route_log",
        "entity_kind": "clue",
        "label": "受潮的航行记录",
        "fact_object": "watermarked_route_log",
        "traits": ["archival", "publicly_observable", "weathered"],
        "status": "discovered",
    },
)


def _catalog_item(case: dict[str, Any]) -> dict[str, Any]:
    """只返回作者目录成员的精确五字段结构。"""  # noqa: DOCSTRING_CJK
    return {
        "content_id": case["content_id"],
        "entity_kind": case["entity_kind"],
        "label": case["label"],
        "fact_object": case["fact_object"],
        "traits": list(case["traits"]),
    }


def _catalog_slot(case: dict[str, Any]) -> dict[str, Any]:
    """构造一个完全由作者数据定义、无需关键词判断的严格槽位。"""  # noqa: DOCSTRING_CJK
    return {
        "slot_id": case["slot_id"],
        "allowed_fact_type": case["fact_type"],
        "allowed_traits": list(case["allowed_traits"]),
        "forbidden_traits": list(case["forbidden_traits"]),
        "catalog_items": [_catalog_item(case)],
    }


async def _story_with_catalog(*cases: dict[str, Any]) -> dict[str, Any]:
    """复制中性测试 Story 作为合同基座，绝不接触正式内容目录。"""  # noqa: DOCSTRING_CJK
    story = deepcopy(await story_loader.load_story(THEATER_TEST_STORY_ID))
    story["world_contract"]["dynamic_content_slots"] = [
        _catalog_slot(case) for case in cases
    ]
    return story


def _runtime_patch(case: dict[str, Any], *, include_content_id: bool = True) -> dict:
    """构造能完成公开交换 Goal、且只在首条事实规则绑定目录成员的 Patch。"""  # noqa: DOCSTRING_CJK
    catalog_rule = {
        "fact_type": case["fact_type"],
        "fact_role": "player_selected_item",
        "content_slot_id": case["slot_id"],
    }
    if include_content_id:
        catalog_rule["content_id"] = case["content_id"]
    return {
        "origin_node_id": THEATER_TEST_ANCHOR_NODE_ID,
        "seed_intent": "使用作者开放的目录内容完成支线",
        "objective": "完成一次双方都公开参与的交换",
        "entry_callback": "双方仍在当前场景中，尚未完成交换。",
        "turn_budget": 4,
        "content_slot_ids": [case["slot_id"]],
        "allowed_new_facts": [
            catalog_rule,
            {
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_item",
                "content_slot_id": "",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "public_exchange_completed",
                "content_slot_id": "",
            },
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_select_catalog_content",
                "objective": "公开确认目录内容",
                "observable_action": "玩家公开确认选中的内容",
                "player_choice_label": "拿起选中的内容并确认",
                "exit_preparation": ["player_selected_item"],
            },
            {
                "beat_id": "beat_complete_exchange",
                "objective": "完成公开交换",
                "observable_action": "双方公开完成交换",
                "player_choice_label": "把选中的内容递给她",
                "exit_preparation": [
                    "catgirl_received_item",
                    "public_exchange_completed",
                ],
            },
        ],
        "exit_candidates": [
            {"kind": "converge", "goal_id": THEATER_TEST_GOAL_ID}
        ],
    }


def _fact_candidate(case: dict[str, Any], *, include_content_id: bool = True) -> dict:
    """构造目录事实候选；kind、label 和 object 均直接取自作者目录。"""  # noqa: DOCSTRING_CJK
    candidate = {
        "goal_id": THEATER_TEST_GOAL_ID,
        "fact_type": case["fact_type"],
        "fact_role": "player_selected_item",
        "subject": "player",
        "predicate": "selected_catalog_content",
        "object": case["fact_object"],
        "content_slot_id": case["slot_id"],
        "public_entity": {
            "kind": case["entity_kind"],
            "label": case["label"],
            "status": case["status"],
        },
    }
    if include_content_id:
        candidate["content_id"] = case["content_id"]
    return candidate


def _legacy_patch() -> dict:
    """保留升级前没有 content_id 的开放槽位 Patch。"""  # noqa: DOCSTRING_CJK
    case = {
        **CATALOG_CASES[0],
        "slot_id": THEATER_TEST_SLOT_ID,
        "fact_type": "ordinary_test_item",
    }
    return _runtime_patch(case, include_content_id=False)


def _legacy_fact_candidate() -> dict:
    """保留升级前只有自由 object 和 Board 标签的事实候选。"""  # noqa: DOCSTRING_CJK
    return {
        "goal_id": THEATER_TEST_GOAL_ID,
        "fact_type": "ordinary_test_item",
        "fact_role": "player_selected_item",
        "subject": "player",
        "predicate": "selected_item",
        "object": "unproven_test_item",
        "content_slot_id": THEATER_TEST_SLOT_ID,
        "public_entity": {
            "kind": "prop",
            "label": "未由作者目录证明的测试物件",
            "status": "selected",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CATALOG_CASES, ids=("stationery", "repair", "clue"))
async def test_loader_accepts_unrelated_author_catalogs_without_keyword_rules(
    tmp_path: Path,
    case: dict[str, Any],
):
    """文具、维修件和档案线索都只依赖同一份通用目录合同。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["dynamic_content_slots"] = [_catalog_slot(case)]
    path = tmp_path / f"{case['slot_id']}.json"
    await atomic_write_json_async(path, payload)

    validated = await story_loader.validate_story_file(path)

    assert validated["world_contract"]["dynamic_content_slots"] == [_catalog_slot(case)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_kind",
    (
        "duplicate_content_id",
        "missing_required_trait",
        "forbidden_trait",
        "bad_entity_kind",
        "bad_public_label",
        "natural_language_content_id",
        "natural_language_fact_object",
        "oversized_content_id",
        "oversized_fact_object",
        "too_many_traits",
        "oversized_trait",
        "too_many_items",
        "too_many_total_items",
        "too_many_slot_forbidden_traits",
        "oversized_slot_trait",
        "oversized_catalog_prompt",
    ),
)
async def test_loader_rejects_invalid_catalog_authority(
    tmp_path: Path,
    invalid_kind: str,
):
    """目录 ID、traits 与实体种类必须在加载 Story 时一次性收口。"""  # noqa: DOCSTRING_CJK
    case = deepcopy(CATALOG_CASES[0])
    slot = _catalog_slot(case)
    if invalid_kind == "duplicate_content_id":
        slot["catalog_items"].append(deepcopy(slot["catalog_items"][0]))
    elif invalid_kind == "missing_required_trait":
        slot["catalog_items"][0]["traits"].remove("locally_available")
    elif invalid_kind == "forbidden_trait":
        slot["catalog_items"][0]["traits"].append("relationship_commitment_symbol")
    elif invalid_kind == "bad_entity_kind":
        slot["catalog_items"][0]["entity_kind"] = "speaking_character"
    elif invalid_kind == "bad_public_label":
        slot["catalog_items"][0]["label"] = "internal_safe_item"
    elif invalid_kind == "natural_language_content_id":
        slot["catalog_items"][0]["content_id"] = "safe item"
    elif invalid_kind == "natural_language_fact_object":
        slot["catalog_items"][0]["fact_object"] = "safe item selected"
    elif invalid_kind == "oversized_content_id":
        slot["catalog_items"][0]["content_id"] = "content_" + ("x" * 64)
    elif invalid_kind == "oversized_fact_object":
        slot["catalog_items"][0]["fact_object"] = "object_" + ("x" * 64)
    elif invalid_kind == "too_many_traits":
        slot["catalog_items"][0]["traits"].extend(
            f"extra_trait_{index}" for index in range(10)
        )
    elif invalid_kind == "oversized_trait":
        slot["catalog_items"][0]["traits"].append("x" * 49)
    elif invalid_kind == "too_many_items":
        template = slot["catalog_items"][0]
        slot["catalog_items"] = [
            {
                **template,
                "content_id": f"content_item_{index}",
                "fact_object": f"catalog_object_{index}",
            }
            for index in range(17)
        ]
    elif invalid_kind == "too_many_slot_forbidden_traits":
        slot["forbidden_traits"] = [f"forbidden_trait_{index}" for index in range(13)]
    elif invalid_kind == "oversized_slot_trait":
        slot["forbidden_traits"] = ["x" * 49]
    elif invalid_kind == "oversized_catalog_prompt":
        template = slot["catalog_items"][0]
        slot["catalog_items"] = []
        for item_index in range(16):
            traits = list(slot["allowed_traits"])
            traits.extend(
                (f"trait_{item_index}_{trait_index}_" + ("x" * 30))[:48]
                for trait_index in range(10)
            )
            slot["catalog_items"].append(
                {
                    **template,
                    "content_id": (f"content_{item_index}_" + ("x" * 48))[:64],
                    "label": f"目录内容{item_index}" + ("甲" * 70),
                    "fact_object": (f"catalog_object_{item_index}_" + ("x" * 42))[:64],
                    "traits": traits,
                }
            )

    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    if invalid_kind == "too_many_total_items":
        slots = []
        for slot_index in range(3):
            catalog_slot = deepcopy(slot)
            catalog_slot["slot_id"] = f"slot_catalog_group_{slot_index}"
            template = catalog_slot["catalog_items"][0]
            catalog_slot["catalog_items"] = [
                {
                    **template,
                    "content_id": f"content_{slot_index}_{item_index}",
                    "fact_object": f"catalog_object_{slot_index}_{item_index}",
                }
                for item_index in range(11)
            ]
            slots.append(catalog_slot)
        payload["world_contract"]["dynamic_content_slots"] = slots
    else:
        payload["world_contract"]["dynamic_content_slots"] = [slot]
    path = tmp_path / f"invalid-{invalid_kind}.json"
    await atomic_write_json_async(path, payload)

    with pytest.raises(ValueError, match="catalog|content|trait|entity"):
        await story_loader.validate_story_file(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_kind", ("extra", "missing"))
async def test_loader_requires_exact_catalog_item_fields(
    tmp_path: Path,
    invalid_kind: str,
):
    """目录成员不能夹带模型提示或遗漏任何作者绑定字段。"""  # noqa: DOCSTRING_CJK
    slot = _catalog_slot(CATALOG_CASES[0])
    if invalid_kind == "extra":
        slot["catalog_items"][0]["model_hint"] = "根据名称自行猜类别"
    else:
        slot["catalog_items"][0].pop("fact_object")
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["dynamic_content_slots"] = [slot]
    path = tmp_path / f"catalog-{invalid_kind}-field.json"
    await atomic_write_json_async(path, payload)

    with pytest.raises(ValueError, match="catalog|content|field"):
        await story_loader.validate_story_file(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CATALOG_CASES, ids=("stationery", "repair", "clue"))
async def test_patch_and_fact_accept_exact_author_catalog_binding(
    case: dict[str, Any],
):
    """合法内容只靠 slot/content ID 精确查表，不依赖题材关键词或模型自报 traits。"""  # noqa: DOCSTRING_CJK
    story = await _story_with_catalog(case)
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(case),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )

    validated = branch_contracts.validate_branch_fact_candidate(
        _fact_candidate(case),
        story=story,
        patch=patch,
        publicly_observed=True,
    )

    assert validated["content_id"] == case["content_id"]
    assert validated["object"] == case["fact_object"]
    assert validated["public_entity"] == {
        "kind": case["entity_kind"],
        "label": case["label"],
        "status": case["status"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_id",
    ("", "content_unknown", CATALOG_CASES[1]["content_id"]),
    ids=("missing", "unknown", "cross-slot"),
)
async def test_catalog_patch_rejects_missing_unknown_or_cross_slot_content(
    content_id: str,
):
    """严格槽位的事实规则必须提前绑定本槽作者目录成员。"""  # noqa: DOCSTRING_CJK
    story = await _story_with_catalog(CATALOG_CASES[0], CATALOG_CASES[1])
    patch = _runtime_patch(CATALOG_CASES[0])
    patch["content_slot_ids"].append(CATALOG_CASES[1]["slot_id"])
    rule = patch["allowed_new_facts"][0]
    if content_id:
        rule["content_id"] = content_id
    else:
        rule.pop("content_id")

    with pytest.raises(ValueError, match="catalog content"):
        branch_contracts.validate_runtime_branch_patch(
            patch,
            story=story,
            current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
        )


@pytest.mark.asyncio
async def test_stationery_catalog_rejects_wedding_ring_substitution():
    """模型不能把文具槽替换成婚戒，再借自然语言声称它满足 traits。"""  # noqa: DOCSTRING_CJK
    case = CATALOG_CASES[0]
    story = await _story_with_catalog(case)
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(case),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    candidate = _fact_candidate(case)
    candidate.update(
        {
            "content_id": "content_wedding_ring",
            "object": "wedding_ring",
        }
    )
    candidate["public_entity"]["label"] = "结婚戒指"

    with pytest.raises(ValueError, match="active Patch|catalog"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper_kind",
    ("missing_content_id", "object", "entity_kind", "entity_label", "missing_entity"),
)
async def test_catalog_fact_rejects_missing_or_tampered_author_fields(
    tamper_kind: str,
):
    """Actor 不能改写作者绑定的 ID、事实对象或 Board 实体身份。"""  # noqa: DOCSTRING_CJK
    case = CATALOG_CASES[0]
    story = await _story_with_catalog(case)
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(case),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    candidate = _fact_candidate(case)
    if tamper_kind == "missing_content_id":
        candidate.pop("content_id")
    elif tamper_kind == "object":
        candidate["object"] = "wedding_ring"
    elif tamper_kind == "entity_kind":
        candidate["public_entity"]["kind"] = "clue"
        candidate["public_entity"]["status"] = "discovered"
    elif tamper_kind == "entity_label":
        candidate["public_entity"]["label"] = "结婚戒指"
    else:
        candidate.pop("public_entity")

    with pytest.raises(ValueError, match="active Patch|catalog|public entity"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
async def test_branch_runtime_rejects_mixed_catalog_candidates_atomically():
    """同回合只要一项篡改，整组事实、回合预算和非推进预算都不能变化。"""  # noqa: DOCSTRING_CJK
    case = CATALOG_CASES[0]
    story = await _story_with_catalog(case)
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(case),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    active_branch = branch_lifecycle.build_active_runtime_branch(
        patch,
        branch_id="branch_catalog_atomic",
        created_revision=2,
        return_anchor={
            "node_id": THEATER_TEST_ANCHOR_NODE_ID,
            "goal_id": THEATER_TEST_GOAL_ID,
        },
        max_nonprogress_turns=2,
    )
    invalid = _fact_candidate(case)
    invalid["object"] = "wedding_ring"
    original_branch = deepcopy(active_branch)
    existing_facts = [{"branch_id": "branch_catalog_atomic", "fact_id": "existing"}]
    original_facts = deepcopy(existing_facts)

    result = branch_runtime.apply_actor_turn(
        story=story,
        active_branch=active_branch,
        existing_facts=existing_facts,
        fact_candidates=[_fact_candidate(case), invalid],
        source_revision=3,
    )

    assert result == {"ok": False, "reason": "fact_candidate_invalid"}
    assert active_branch == original_branch
    assert existing_facts == original_facts
    assert active_branch["turns_used"] == 0
    assert active_branch["nonprogress_turns"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper_kind", ("content_id", "object", "entity_label"))
async def test_catalog_committed_fact_is_preserved_and_revalidated_with_story(
    tamper_kind: str,
):
    """Committed Fact 保留目录身份；恢复时用当前 Story 重验，不能只检查结构。"""  # noqa: DOCSTRING_CJK
    case = CATALOG_CASES[0]
    story = await _story_with_catalog(case)
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(case),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    candidate = branch_contracts.validate_branch_fact_candidate(
        _fact_candidate(case),
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    committed = branch_contracts.build_committed_branch_fact(
        candidate,
        branch_id="branch_catalog_restore",
        fact_id="branch_fact_catalog_restore",
        source_revision=3,
        public_entity_id="branch_entity_catalog_restore",
    )
    assert committed["content_id"] == case["content_id"]
    assert (
        branch_contracts.validate_committed_branch_fact_structure(
            committed,
            story=story,
        )
        == committed
    )

    tampered = deepcopy(committed)
    if tamper_kind == "content_id":
        tampered["content_id"] = "content_wedding_ring"
    elif tamper_kind == "object":
        tampered["object"] = "wedding_ring"
    else:
        tampered["public_entity"]["label"] = "结婚戒指"
    with pytest.raises(ValueError, match="catalog"):
        branch_contracts.validate_committed_branch_fact_structure(
            tampered,
            story=story,
        )


@pytest.mark.asyncio
async def test_active_patch_rejects_same_slot_catalog_member_rebinding():
    """活动支线存档不能把已提交事实从 Patch 选定成员整体改绑到同槽另一成员。"""  # noqa: DOCSTRING_CJK
    selected = deepcopy(CATALOG_CASES[0])
    replacement = {
        **deepcopy(selected),
        "content_id": "content_blue_ink",
        "label": "蓝色墨水",
        "fact_object": "blue_ink_bottle",
    }
    story = await _story_with_catalog(selected)
    story["world_contract"]["dynamic_content_slots"][0]["catalog_items"].append(
        _catalog_item(replacement)
    )
    patch = branch_contracts.validate_runtime_branch_patch(
        _runtime_patch(selected),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    candidate = branch_contracts.validate_branch_fact_candidate(
        _fact_candidate(selected),
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    committed = branch_contracts.build_committed_branch_fact(
        candidate,
        branch_id="branch_catalog_rebinding",
        fact_id="branch_fact_catalog_rebinding",
        source_revision=3,
        public_entity_id="branch_entity_catalog_rebinding",
    )
    tampered = deepcopy(committed)
    tampered["content_id"] = replacement["content_id"]
    tampered["object"] = replacement["fact_object"]
    tampered["public_entity"]["label"] = replacement["label"]

    # 两个成员都属于作者目录，单独查 Story 会通过；原 Patch 仍只授权最初选中的成员。
    branch_contracts.validate_committed_branch_fact_structure(tampered, story=story)
    with pytest.raises(ValueError, match="active Patch"):
        branch_contracts.validate_committed_branch_fact_against_patch(
            tampered,
            story=story,
            patch=patch,
        )


@pytest.mark.asyncio
async def test_legacy_slot_patch_fact_and_committed_fact_remain_compatible():
    """无目录旧 Story 继续走结构合同，但不会凭自由文本获得目录语义证明。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _legacy_patch(),
        story=story,
        current_node_id=THEATER_TEST_ANCHOR_NODE_ID,
    )
    candidate = branch_contracts.validate_branch_fact_candidate(
        _legacy_fact_candidate(),
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    assert "content_id" not in patch["allowed_new_facts"][0]
    assert "content_id" not in candidate

    committed = branch_contracts.build_committed_branch_fact(
        candidate,
        branch_id="branch_legacy_restore",
        fact_id="branch_fact_legacy_restore",
        source_revision=3,
        public_entity_id="branch_entity_legacy_restore",
    )
    assert "content_id" not in committed
    assert (
        branch_contracts.validate_committed_branch_fact_structure(committed)
        == committed
    )
    assert (
        branch_contracts.validate_committed_branch_fact_structure(
            committed,
            story=story,
        )
        == committed
    )
