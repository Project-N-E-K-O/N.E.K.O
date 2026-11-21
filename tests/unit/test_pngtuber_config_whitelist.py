"""The server rebuilds the pngtuber config as a fresh dict, so any field not copied by
`_normalize_pngtuber_config` is silently dropped. `position_anchor` was exactly such a
field: the emote maker wrote it, the UI let you set it, and it vanished on upload.

These guard the whitelist itself — a regression here is invisible in the UI until someone
repositions an avatar and it jumps.
"""

from __future__ import annotations

from main_routers.pngtuber_router import _normalize_pngtuber_config


def _anchor(raw_pngtuber: dict) -> object:
    result = _normalize_pngtuber_config(
        "probe", {"model_type": "pngtuber", "pngtuber": raw_pngtuber}
    )
    return result.get("position_anchor")


def test_position_anchor_survives_upload_normalisation() -> None:
    assert _anchor({"idle_image": "idle.png", "position_anchor": "bottom_right"}) == "bottom_right"
    assert _anchor({"idle_image": "idle.png", "position_anchor": "center"}) == "center"


def test_unknown_anchor_value_is_not_passed_through() -> None:
    # 运行时 normalizeConfig 只认这两个值，其它值应当由它自己退回推导，
    # 而不是我们塞一个它不认识的字符串进去。
    assert _anchor({"idle_image": "idle.png", "position_anchor": "top_left"}) is None
    assert _anchor({"idle_image": "idle.png", "position_anchor": 42}) is None


def test_absent_anchor_stays_absent() -> None:
    assert _anchor({"idle_image": "idle.png"}) is None


def test_other_placement_fields_were_not_regressed() -> None:
    result = _normalize_pngtuber_config("probe", {"model_type": "pngtuber", "pngtuber": {
        "idle_image": "idle.png", "scale": 1.6, "offset_x": -80, "offset_y": 40,
        "mirror": True, "position_anchor": "bottom_right",
    }})
    assert result["scale"] == 1.6
    assert result["offset_x"] == -80
    assert result["offset_y"] == 40
    assert result["mirror"] is True
    assert result["position_anchor"] == "bottom_right"
    # 图片路径必须被改成 /user_pngtuber/<folder>/ 下的绝对 URL
    assert result["idle_image"] == "/user_pngtuber/probe/idle.png"


def _priority(raw_pngtuber: dict) -> object:
    result = _normalize_pngtuber_config(
        "probe", {"model_type": "pngtuber", "pngtuber": raw_pngtuber}
    )
    return result.get("state_priority")


def test_state_priority_survives_upload_normalisation() -> None:
    """Preserve state priority through upload normalization instead of silently falling back to defaults."""
    assert _priority({"idle_image": "idle.png", "state_priority": "emotion"}) == "emotion"
    assert _priority({"idle_image": "idle.png", "state_priority": "  EMOTION  "}) == "emotion"
    assert _priority({"idle_image": "idle.png", "state_priority": "talking"}) == "talking"


def test_a_bogus_priority_is_not_passed_through() -> None:
    # 运行时尚不认的值不能原样透传，否则 model.json 里会存出一个没人实现的选项
    assert _priority({"idle_image": "idle.png", "state_priority": "loud"}) is None
    assert _priority({"idle_image": "idle.png", "state_priority": 1}) is None
    assert _priority({"idle_image": "idle.png"}) is None
