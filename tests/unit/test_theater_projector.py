"""验证小剧场 Projector 只公开可安全展示的确定性派生状态。"""  # noqa: DOCSTRING_CJK

from services.theater import projector


def _committed_fact(
    *,
    entity_id: str,
    kind: str,
    label: str,
    status: str,
    revision: int,
) -> dict:
    """构造不依赖任何具体 Story Package 的已提交公开实体事实。"""  # noqa: DOCSTRING_CJK
    return {
        "fact_id": f"fact_{entity_id}_{revision}",
        "branch_id": "branch_generic",
        "source_revision": revision,
        "private_fact_payload": "must-not-leak",
        "public_entity": {
            "entity_id": entity_id,
            "kind": kind,
            "label": label,
            "status": status,
            "private_entity_payload": "must-not-leak",
        },
    }


def test_dynamic_entities_share_existing_board_shape_without_fact_leakage():
    """合法动态实体进入既有 Board 三组，公开结果不包含 Branch Fact 私有字段。"""  # noqa: DOCSTRING_CJK
    story = {
        "stage_props": [{"id": "static_prop", "label": "静态物件", "public_hint": "作者提示"}],
        "clues": [{"id": "static_clue", "title": "静态线索", "public_text": "作者说明"}],
    }
    state = {
        "available_prop_ids": ["static_prop"],
        "used_prop_ids": [],
        "clue_ids": ["static_clue"],
        "branch_facts": [
            _committed_fact(
                entity_id="entity_available",
                kind="prop",
                label="公开可用物件",
                status="selected",
                revision=2,
            ),
            _committed_fact(
                entity_id="entity_used",
                kind="prop",
                label="公开已用物件",
                status="used",
                revision=3,
            ),
            _committed_fact(
                entity_id="entity_clue",
                kind="clue",
                label="公开线索",
                status="discovered",
                revision=4,
            ),
        ],
    }

    board = projector.scenario_board(story, state)

    assert board["available_props"] == [
        {"id": "static_prop", "label": "静态物件", "public_hint": "作者提示"},
        {"id": "entity_available", "label": "公开可用物件", "public_hint": ""},
    ]
    assert board["used_props"] == [
        {"id": "entity_used", "label": "公开已用物件", "public_hint": ""},
    ]
    assert board["discovered_clues"] == [
        {"id": "static_clue", "title": "静态线索", "public_text": "作者说明"},
        {"id": "entity_clue", "title": "公开线索", "public_text": ""},
    ]
    assert "private" not in repr(board)
    assert "fact_id" not in repr(board)


def test_dynamic_board_ignores_uncommitted_invalid_and_stale_entity_versions():
    """缺少权威提交字段的实体不公开，同一实体只使用最新 revision 的合法状态。"""  # noqa: DOCSTRING_CJK
    stale = _committed_fact(
        entity_id="entity_same",
        kind="prop",
        label="同一公开物件",
        status="selected",
        revision=2,
    )
    latest = _committed_fact(
        entity_id="entity_same",
        kind="prop",
        label="同一公开物件",
        status="used",
        revision=5,
    )
    uncommitted = _committed_fact(
        entity_id="entity_uncommitted",
        kind="prop",
        label="未提交物件",
        status="selected",
        revision=6,
    )
    uncommitted.pop("fact_id")
    invalid_status = _committed_fact(
        entity_id="entity_invalid",
        kind="clue",
        label="非法状态线索",
        status="selected",
        revision=7,
    )

    board = projector.scenario_board(
        {"stage_props": [], "clues": []},
        {"branch_facts": [latest, uncommitted, invalid_status, stale]},
    )

    assert board["available_props"] == []
    assert board["used_props"] == [
        {"id": "entity_same", "label": "同一公开物件", "public_hint": ""},
    ]
    assert board["discovered_clues"] == []
