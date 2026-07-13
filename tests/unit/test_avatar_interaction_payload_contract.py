import pytest

from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_TOOL_CONTRACT,
    normalize_avatar_interaction_payload,
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
    ("tool_id", "action_id"),
    [
        ("lollipop", "offer"),
        ("lollipop", "tease"),
        ("lollipop", "tap_soft"),
        ("fist", "poke"),
        ("hammer", "bonk"),
    ],
)
def test_every_established_action_falls_back_from_invalid_intensity(tool_id, action_id):
    normalized = normalize_avatar_interaction_payload(
        {
            "interactionId": f"{tool_id}-{action_id}",
            "toolId": tool_id,
            "actionId": action_id,
            "target": "avatar",
            "timestamp": 1,
            "intensity": "unsupported-intensity",
        }
    )

    assert normalized is not None
    assert normalized["intensity"] == "normal"


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
    base = {
        "interactionId": "interaction-1",
        "target": "avatar",
        "pointer": {"clientX": 12, "clientY": 34},
        "timestamp": 1234,
        "intensity": "rapid",
        "touchZone": "head",
        "rewardDrop": True,
        "easterEgg": True,
    }

    lollipop = normalize_avatar_interaction_payload(
        {
            **base,
            "toolId": "lollipop",
            "actionId": "offer",
        }
    )
    assert lollipop is not None
    assert lollipop["intensity"] == "normal"
    assert lollipop["touch_zone"] == ""
    assert lollipop["reward_drop"] is False
    assert lollipop["easter_egg"] is False

    fist = normalize_avatar_interaction_payload(
        {
            **base,
            "toolId": "fist",
            "actionId": "poke",
        }
    )
    assert fist is not None
    assert fist["intensity"] == "rapid"
    assert fist["touch_zone"] == "head"
    assert fist["reward_drop"] is True
    assert fist["easter_egg"] is False

    hammer = normalize_avatar_interaction_payload(
        {
            **base,
            "toolId": "hammer",
            "actionId": "bonk",
            "intensity": "normal",
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
            "easterEgg": value,
        }
    )

    assert fist is not None and fist["reward_drop"] is True
    assert fist["easter_egg"] is False
    assert hammer is not None and hammer["easter_egg"] is True
    assert hammer["intensity"] == "easter_egg"
    assert hammer["reward_drop"] is False


@pytest.mark.unit
@pytest.mark.parametrize("value", [False, 0, 0.0, "false", "FALSE", "0", 2, "yes"])
def test_payload_normalizer_rejects_false_or_unsupported_boolean_encodings(value):
    normalized = normalize_avatar_interaction_payload(
        {
            "interactionId": "fist-bool-false",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
            "rewardDrop": value,
        }
    )

    assert normalized is not None
    assert normalized["reward_drop"] is False


@pytest.mark.unit
def test_payload_normalizer_handles_pointer_aliases_and_invalid_coordinates():
    snake_pointer = normalize_avatar_interaction_payload(
        {
            "interactionId": "pointer-snake",
            "toolId": "fist",
            "actionId": "poke",
            "target": "avatar",
            "timestamp": 1,
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
        },
        now_ms=4321,
    )

    assert normalized is not None
    assert normalized["timestamp"] == 4321


@pytest.mark.unit
def test_payload_normalizer_accepts_touch_zone_only_for_declared_tools():
    def normalize(tool_id, action_id, touch_zone):
        return normalize_avatar_interaction_payload(
            {
                "interactionId": f"{tool_id}-touch-zone",
                "toolId": tool_id,
                "actionId": action_id,
                "target": "avatar",
                "timestamp": 1,
                "touchZone": touch_zone,
            }
        )

    lollipop = normalize("lollipop", "offer", "head")
    fist = normalize("fist", "poke", " FACE ")
    hammer = normalize("hammer", "bonk", "tail")

    assert lollipop is not None and lollipop["touch_zone"] == ""
    assert fist is not None and fist["touch_zone"] == "face"
    assert hammer is not None and hammer["touch_zone"] == ""
