import pytest

from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_TOOL_CONTRACT,
    normalize_avatar_interaction_payload,
)
from config.prompts.prompts_avatar_interaction import (
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)


@pytest.mark.unit
def test_avatar_interaction_contract_is_limited_to_the_three_established_tools():
    assert set(AVATAR_INTERACTION_TOOL_CONTRACT) == {"lollipop", "fist", "hammer"}
    assert AVATAR_INTERACTION_TOOL_CONTRACT["lollipop"]["actions"] == {
        "offer": frozenset({"normal"}),
        "tease": frozenset({"normal"}),
        "tap_soft": frozenset({"rapid", "burst"}),
    }
    assert AVATAR_INTERACTION_TOOL_CONTRACT["fist"]["actions"] == {
        "poke": frozenset({"normal", "rapid"}),
    }
    assert AVATAR_INTERACTION_TOOL_CONTRACT["hammer"]["actions"] == {
        "bonk": frozenset({"normal", "rapid", "burst", "easter_egg"}),
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_id", "action_id", "touch_zone"),
    [
        ("lollipop", "offer", None),
        ("lollipop", "tease", None),
        ("lollipop", "tap_soft", None),
        ("fist", "poke", "head"),
        ("hammer", "bonk", "head"),
    ],
)
def test_every_established_action_rejects_missing_or_invalid_intensity(
    tool_id, action_id, touch_zone
):
    for intensity in (None, "unsupported-intensity"):
        payload = {
            "interactionId": f"{tool_id}-{action_id}-{intensity}",
            "toolId": tool_id,
            "actionId": action_id,
            "target": "avatar",
            "timestamp": 1,
        }
        if intensity is not None:
            payload["intensity"] = intensity
        if touch_zone is not None:
            payload["touchZone"] = touch_zone

        assert normalize_avatar_interaction_payload(payload) is None


@pytest.mark.unit
@pytest.mark.parametrize("intensity", [None, "unsupported-intensity"])
def test_prompt_and_memory_builders_reject_missing_or_invalid_intensity(intensity):
    payload = {
        "tool_id": "lollipop",
        "action_id": "tap_soft",
    }
    if intensity is not None:
        payload["intensity"] = intensity

    with pytest.raises(ValueError, match="intensity"):
        _build_avatar_interaction_instruction("en", "Neko", "User", payload)
    with pytest.raises(ValueError, match="intensity"):
        _build_avatar_interaction_memory_meta("en", payload, "User")


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {
            "interactionId": "invalid-action",
            "toolId": "fist",
            "actionId": "bonk",
            "target": "avatar",
        },
        {
            "interactionId": "invalid-target",
            "toolId": "fist",
            "actionId": "poke",
            "target": "canvas",
        },
        {
            "interactionId": "   ",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
        },
    ],
)
def test_avatar_interaction_payload_normalizer_rejects_invalid_identity(payload):
    assert normalize_avatar_interaction_payload(payload, now_ms=1) is None


@pytest.mark.unit
def test_avatar_interaction_payload_normalizer_isolates_special_fields():
    common = {
        "interactionId": "interaction-1",
        "target": "avatar",
        "pointer": {"clientX": 12, "clientY": 34},
        "timestamp": 1234,
        "rewardDrop": True,
        "easterEgg": True,
    }

    lollipop = normalize_avatar_interaction_payload(
        {
            **common,
            "toolId": "lollipop",
            "actionId": "offer",
            "intensity": "normal",
        }
    )
    assert lollipop is not None
    assert lollipop["intensity"] == "normal"
    assert lollipop["touch_zone"] == ""
    assert lollipop["reward_drop"] is False
    assert lollipop["easter_egg"] is False

    fist = normalize_avatar_interaction_payload(
        {
            **common,
            "toolId": "fist",
            "actionId": "poke",
            "intensity": "rapid",
            "touchZone": "head",
        }
    )
    assert fist is not None
    assert fist["intensity"] == "rapid"
    assert fist["touch_zone"] == "head"
    assert fist["reward_drop"] is True
    assert fist["easter_egg"] is False

    hammer = normalize_avatar_interaction_payload(
        {
            **common,
            "toolId": "hammer",
            "actionId": "bonk",
            "intensity": "easter_egg",
            "touchZone": "head",
        }
    )
    assert hammer is not None
    assert hammer["intensity"] == "easter_egg"
    assert hammer["touch_zone"] == "head"
    assert hammer["reward_drop"] is False
    assert hammer["easter_egg"] is True


@pytest.mark.unit
def test_avatar_interaction_payload_normalizer_rejects_unsupported_tool():
    assert (
        normalize_avatar_interaction_payload(
            {
                "interactionId": "unsupported-1",
                "toolId": "unsupported-tool",
                "actionId": "unknown-action",
                "target": "avatar",
                "timestamp": 1,
            }
        )
        is None
    )


@pytest.mark.unit
def test_snake_case_values_take_precedence_over_camel_case_aliases():
    normalized = normalize_avatar_interaction_payload(
        {
            "interaction_id": "snake-id",
            "interactionId": "camel-id",
            "tool_id": "fist",
            "toolId": "hammer",
            "action_id": "poke",
            "actionId": "bonk",
            "target": "avatar",
            "timestamp": 1,
            "text_context": "snake text",
            "textContext": "camel text",
            "reward_drop": False,
            "rewardDrop": True,
            "touch_zone": "ear",
            "touchZone": "head",
            "intensity": "normal",
        }
    )

    assert normalized is not None
    assert normalized["interaction_id"] == "snake-id"
    assert normalized["tool_id"] == "fist"
    assert normalized["action_id"] == "poke"
    assert normalized["text_context"] == "snake text"
    assert normalized["reward_drop"] is False
    assert normalized["touch_zone"] == "ear"


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, 1, 1.0, "true", "TRUE", "1"])
def test_payload_normalizer_accepts_established_true_boolean_encodings(value):
    fist = normalize_avatar_interaction_payload(
        {
            "interactionId": "fist-bool",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "rewardDrop": value,
        }
    )
    hammer = normalize_avatar_interaction_payload(
        {
            "interactionId": "hammer-bool",
            "toolId": "hammer",
            "actionId": "bonk",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "easter_egg",
            "touchZone": "head",
            "easterEgg": value,
        }
    )

    assert fist is not None and fist["reward_drop"] is True
    assert fist["easter_egg"] is False
    assert hammer is not None and hammer["easter_egg"] is True
    assert hammer["intensity"] == "easter_egg"
    assert hammer["reward_drop"] is False


@pytest.mark.unit
def test_hammer_payload_and_builders_reject_contradictory_easter_egg_facts():
    wire = {
        "interactionId": "hammer-contradiction",
        "toolId": "hammer", "actionId": "bonk", "target": "avatar",
        "timestamp": 1, "touchZone": "head",
    }
    for facts in (
        {"intensity": "normal", "easterEgg": True},
        {"intensity": "easter_egg"},
    ):
        assert normalize_avatar_interaction_payload({**wire, **facts}) is None
    internal = {
        "tool_id": "hammer", "action_id": "bonk", "intensity": "normal",
        "easter_egg": True, "touch_zone": "head",
    }
    with pytest.raises(ValueError, match="easter_egg"):
        _build_avatar_interaction_instruction("en", "Neko", "User", internal)
    with pytest.raises(ValueError, match="easter_egg"):
        _build_avatar_interaction_memory_meta("en", internal, "User")


@pytest.mark.unit
@pytest.mark.parametrize("value", [False, 0, 0.0, "false", "FALSE", "0"])
def test_payload_normalizer_accepts_explicit_false_boolean_encodings(value):
    normalized = normalize_avatar_interaction_payload(
        {
            "interactionId": "fist-bool-false",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "rewardDrop": value,
        }
    )

    assert normalized is not None
    assert normalized["reward_drop"] is False


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, 2, "yes", [], {}])
def test_payload_normalizer_rejects_present_invalid_boolean_encodings(value):
    assert normalize_avatar_interaction_payload(
        {
            "interactionId": "fist-bool-invalid",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "rewardDrop": value,
        }
    ) is None


@pytest.mark.unit
def test_payload_normalizer_handles_pointer_aliases_and_invalid_coordinates():
    snake_pointer = normalize_avatar_interaction_payload(
        {
            "interactionId": "pointer-snake",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "pointer": {
                "client_x": None,
                "clientX": "12.5",
                "client_y": None,
                "clientY": 34,
            },
        }
    )
    partial_pointer = normalize_avatar_interaction_payload(
        {
            "interactionId": "pointer-partial",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "pointer": {"clientX": 12},
        }
    )
    non_finite_pointer = normalize_avatar_interaction_payload(
        {
            "interactionId": "pointer-infinite",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": "head",
            "pointer": {"clientX": float("inf"), "clientY": 34},
        }
    )

    assert snake_pointer is not None
    assert snake_pointer["pointer"] == {"client_x": 12.5, "client_y": 34.0}
    assert partial_pointer is not None and partial_pointer["pointer"] is None
    assert non_finite_pointer is not None and non_finite_pointer["pointer"] is None


@pytest.mark.unit
@pytest.mark.parametrize("timestamp", [None, "not-a-time", float("inf"), 0, -1])
def test_payload_normalizer_uses_supplied_clock_for_invalid_timestamp(timestamp):
    normalized = normalize_avatar_interaction_payload(
        {
            "interactionId": "timestamp-fallback",
            "toolId": "lollipop",
            "actionId": "offer",
            "target": "avatar",
            "timestamp": timestamp,
            "intensity": "normal",
        },
        now_ms=4321,
    )

    assert normalized is not None
    assert normalized["timestamp"] == 4321


@pytest.mark.unit
def test_payload_normalizer_requires_touch_zone_only_for_declared_tools():
    def normalize(tool_id, action_id, touch_zone):
        payload = {
            "interactionId": f"{tool_id}-touch-zone-{touch_zone}",
            "toolId": tool_id,
            "actionId": action_id,
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
        }
        if touch_zone is not None:
            payload["touchZone"] = touch_zone
        return normalize_avatar_interaction_payload(payload)

    lollipop = normalize("lollipop", "offer", "head")
    fist = normalize("fist", "poke", " FACE ")
    hammer = normalize("hammer", "bonk", "tail")
    fist_without_zone = normalize("fist", "poke", None)
    lollipop_without_zone = normalize("lollipop", "offer", None)
    lollipop_with_null_zone = normalize_avatar_interaction_payload(
        {
            "interactionId": "lollipop-null-touch-zone",
            "toolId": "lollipop",
            "actionId": "offer",
            "target": "avatar",
            "timestamp": 1,
            "intensity": "normal",
            "touchZone": None,
        }
    )

    assert lollipop is None
    assert fist is not None and fist["touch_zone"] == "face"
    assert hammer is None
    assert fist_without_zone is None
    assert lollipop_without_zone is not None
    assert lollipop_without_zone["touch_zone"] == ""
    assert lollipop_with_null_zone is None
