import asyncio
from collections import deque

import pytest

from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_TOOL_CONTRACT,
    normalize_avatar_interaction_payload,
)
from config.prompts.prompts_avatar_interaction import (
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)


_RPS_CANONICAL_ROUNDS = (
    ("rock", "rock", "draw"),
    ("rock", "scissors", "user_win"),
    ("rock", "paper", "avatar_win"),
    ("scissors", "rock", "avatar_win"),
    ("scissors", "scissors", "draw"),
    ("scissors", "paper", "user_win"),
    ("paper", "rock", "user_win"),
    ("paper", "scissors", "avatar_win"),
    ("paper", "paper", "draw"),
)
_RPS_GESTURE_LABELS = {
    "zh": {"rock": "石头", "scissors": "剪刀", "paper": "布"},
    "zh-TW": {"rock": "石頭", "scissors": "剪刀", "paper": "布"},
    "en": {"rock": "rock", "scissors": "scissors", "paper": "paper"},
    "ja": {"rock": "グー", "scissors": "チョキ", "paper": "パー"},
    "ko": {"rock": "바위", "scissors": "가위", "paper": "보"},
    "ru": {"rock": "камень", "scissors": "ножницы", "paper": "бумага"},
    "es": {"rock": "piedra", "scissors": "tijera", "paper": "papel"},
    "pt": {"rock": "pedra", "scissors": "tesoura", "paper": "papel"},
}
_RPS_PROMPT_RESULT_MARKERS = {
    "zh": {
        "user_win": "Alice赢、YUI输",
        "avatar_win": "YUI赢、Alice输",
        "draw": "本局平手",
    },
    "zh-TW": {
        "user_win": "Alice贏、YUI輸",
        "avatar_win": "YUI贏、Alice輸",
        "draw": "這局平手",
    },
    "en": {
        "user_win": "Alice won while YUI lost",
        "avatar_win": "YUI won while Alice lost",
        "draw": "the round was a draw",
    },
    "ja": {
        "user_win": "Aliceの勝ち、YUIの負けでした",
        "avatar_win": "YUIの勝ち、Aliceの負けでした",
        "draw": "あいこでした",
    },
    "ko": {
        "user_win": "YUI는 이번 판에서 졌다",
        "avatar_win": "YUI는 이번 판에서 이겼다",
        "draw": "이번 판은 비겼다",
    },
    "ru": {
        "user_win": "победитель — Alice, проигравшая сторона — YUI",
        "avatar_win": "победитель — YUI, проигравшая сторона — Alice",
        "draw": "получилась ничья",
    },
    "es": {
        "user_win": "ganó Alice y perdió YUI",
        "avatar_win": "ganó YUI y perdió Alice",
        "draw": "la ronda terminó en empate",
    },
    "pt": {
        "user_win": "Alice venceu e YUI perdeu",
        "avatar_win": "YUI venceu e Alice perdeu",
        "draw": "a rodada terminou empatada",
    },
}
_RPS_MEMORY_NOTES = {
    "zh": {"user_win": "[和Alice猜拳，输了]", "avatar_win": "[和Alice猜拳，赢了]", "draw": "[和Alice猜拳，平手]"},
    "zh-TW": {"user_win": "[和Alice猜拳，輸了]", "avatar_win": "[和Alice猜拳，贏了]", "draw": "[和Alice猜拳，平手]"},
    "en": {"user_win": "[Lost to Alice at rock-paper-scissors]", "avatar_win": "[Beat Alice at rock-paper-scissors]", "draw": "[Drew with Alice at rock-paper-scissors]"},
    "ja": {"user_win": "[Aliceとのじゃんけんに負けた]", "avatar_win": "[Aliceとのじゃんけんに勝った]", "draw": "[Aliceとのじゃんけんはあいこだった]"},
    "ko": {"user_win": "[Alice 상대 가위바위보에서 짐]", "avatar_win": "[Alice 상대 가위바위보에서 이김]", "draw": "[Alice 상대 가위바위보에서 비김]"},
    "ru": {"user_win": "[Проигрыш Alice в игре «камень, ножницы, бумага»]", "avatar_win": "[Победа над Alice в игре «камень, ножницы, бумага»]", "draw": "[Ничья с Alice в игре «камень, ножницы, бумага»]"},
    "es": {"user_win": "[Perdiste contra Alice a piedra, papel o tijera]", "avatar_win": "[Ganaste a Alice a piedra, papel o tijera]", "draw": "[Empataste con Alice a piedra, papel o tijera]"},
    "pt": {"user_win": "[Perdeu para Alice no jogo de pedra, papel e tesoura]", "avatar_win": "[Venceu Alice no jogo de pedra, papel e tesoura]", "draw": "[Empatou com Alice no jogo de pedra, papel e tesoura]"},
}


@pytest.mark.unit
def test_avatar_interaction_contract_declares_three_action_tools_and_rps_round_facts():
    assert set(AVATAR_INTERACTION_TOOL_CONTRACT) == {"lollipop", "fist", "hammer", "rps"}
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
    assert AVATAR_INTERACTION_TOOL_CONTRACT["rps"] == {
        "actions": {},
        "touch_zone": False,
        "boolean_field": None,
        "round_choice": True,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user_gesture", "avatar_gesture", "round_result"),
    _RPS_CANONICAL_ROUNDS,
)
def test_rps_payload_normalizer_accepts_the_nine_canonical_rounds(
    user_gesture, avatar_gesture, round_result
):
    normalized = normalize_avatar_interaction_payload({
        "interactionId": f"rps-{user_gesture}-{avatar_gesture}",
        "toolId": "rps",
        "target": "avatar",
        "timestamp": 1,
        "userGesture": user_gesture,
        "avatarGesture": avatar_gesture,
        "roundResult": round_result,
    })
    assert normalized is not None
    assert normalized["user_gesture"] == user_gesture
    assert normalized["avatar_gesture"] == avatar_gesture
    assert normalized["round_result"] == round_result
    assert "action_id" not in normalized
    assert "intensity" not in normalized
    assert "touch_zone" not in normalized


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rps_payload_reaches_the_runtime_delivered_result_without_action_fields(
    monkeypatch,
):
    from main_logic.core import greeting

    class FakeOfflineClient:
        _is_responding = False

        def update_max_response_length(self, _max_length):
            return None

        async def prompt_ephemeral(self, *_args, **_kwargs):
            return True

    class RuntimeHarness(greeting.GreetingMixin):
        def __init__(self):
            self.is_active = True
            self.session = FakeOfflineClient()
            self.lanlan_name = "YUI"
            self.master_name = "Alice"
            self.user_language = "en"
            self._recent_avatar_interaction_ids = deque(maxlen=32)
            self._recent_avatar_interaction_id_set = set()
            self._last_avatar_interaction_at = 0
            self._last_avatar_interaction_speak_at = 0
            self.avatar_interaction_cooldown_ms = 0
            self.avatar_interaction_speak_cooldown_ms = 0
            self._proactive_write_lock = asyncio.Lock()
            self.lock = asyncio.Lock()
            self.current_speech_id = ""
            self._pending_turn_meta = None
            self.acks = []

        def _get_text_guard_max_length(self):
            return 1000

        async def send_avatar_interaction_ack(
            self, interaction_id, accepted, reason, **kwargs
        ):
            self.acks.append((interaction_id, accepted, reason, kwargs))

    monkeypatch.setattr(greeting, "OmniOfflineClient", FakeOfflineClient)
    runtime = RuntimeHarness()

    result = await runtime.handle_avatar_interaction({
        "interactionId": "rps-runtime-delivery",
        "toolId": "rps",
        "target": "avatar",
        "timestamp": 1,
        "userGesture": "rock",
        "avatarGesture": "scissors",
        "roundResult": "user_win",
    })

    assert result == {
        "accepted": True,
        "interaction_id": "rps-runtime-delivery",
    }
    assert runtime.acks == [(
        "rps-runtime-delivery",
        True,
        "delivered",
        {"turn_id": runtime.current_speech_id},
    )]


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_or_override",
    [
        {"roundResult": "avatar_win"},
        {"userGesture": "unknown"},
        {"userGesture": "unknown", "avatarGesture": "unknown", "roundResult": "draw"},
        {"avatarGesture": None},
        {"actionId": "play"},
        {"intensity": "normal"},
        {"touchZone": "head"},
        {"userVariant": "primary"},
    ],
)
def test_rps_payload_normalizer_rejects_incomplete_contradictory_or_extra_facts(
    extra_or_override,
):
    assert normalize_avatar_interaction_payload({
        "interactionId": "rps-strict",
        "toolId": "rps",
        "target": "avatar",
        "timestamp": 1,
        "userGesture": "rock",
        "avatarGesture": "scissors",
        "roundResult": "user_win",
        **extra_or_override,
    }) is None


@pytest.mark.unit
@pytest.mark.parametrize("locale", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_rps_prompt_and_memory_use_the_validated_round_in_all_locales(locale):
    for user_gesture, avatar_gesture, round_result in _RPS_CANONICAL_ROUNDS:
        payload = {
            "tool_id": "rps",
            "user_gesture": user_gesture,
            "avatar_gesture": avatar_gesture,
            "round_result": round_result,
        }
        instruction = _build_avatar_interaction_instruction(
            locale, "YUI", "Alice", payload
        )
        memory = _build_avatar_interaction_memory_meta(locale, payload, "Alice")

        assert "Alice" in instruction
        assert "YUI" in instruction
        assert _RPS_GESTURE_LABELS[locale][user_gesture] in instruction
        assert _RPS_GESTURE_LABELS[locale][avatar_gesture] in instruction
        assert _RPS_PROMPT_RESULT_MARKERS[locale][round_result] in instruction
        assert "\n" not in instruction

        assert memory["memory_note"] == _RPS_MEMORY_NOTES[locale][round_result]
        assert memory["memory_dedupe_key"] == "rps_round"
        assert memory["memory_dedupe_rank"] == 1

        forbidden_temporary_limits = (
            "based only on this round",
            "今回の事実だけ",
            "이 판의 사실만",
            "опираясь только на этот раунд",
            "basándote solo en esta ronda",
            "com base apenas nesta rodada",
        )
        assert not any(
            fragment in instruction for fragment in forbidden_temporary_limits
        )


@pytest.mark.unit
def test_rps_prompt_and_memory_use_localized_neutral_actor_when_name_is_empty():
    payload = {
        "tool_id": "rps",
        "user_gesture": "rock",
        "avatar_gesture": "scissors",
        "round_result": "user_win",
    }
    expected_actors = {
        "zh": ("对方", "对方"),
        "zh-TW": ("對方", "對方"),
        "en": ("The other person", "they"),
        "ja": ("相手", "相手"),
        "ko": ("상대가", "상대"),
        "ru": ("Собеседник", "собеседник"),
        "es": ("Esa persona", "esa persona"),
        "pt": ("A outra pessoa", "a outra pessoa"),
    }

    for locale, (prompt_actor, memory_actor) in expected_actors.items():
        instruction = _build_avatar_interaction_instruction(locale, "YUI", "", payload)
        memory = _build_avatar_interaction_memory_meta(locale, payload, "")

        assert prompt_actor in instruction
        assert memory_actor in memory["memory_note"]
        assert not any(
            forbidden in instruction or forbidden in memory["memory_note"]
            for forbidden in ("主人", "master", "ご主人", "주인", "Хозяин")
        )


@pytest.mark.unit
def test_rps_prompt_and_memory_reject_a_contradictory_round():
    payload = {
        "tool_id": "rps",
        "user_gesture": "rock",
        "avatar_gesture": "scissors",
        "round_result": "avatar_win",
    }
    with pytest.raises(ValueError, match="rps round facts"):
        _build_avatar_interaction_instruction("en", "YUI", "Alice", payload)
    with pytest.raises(ValueError, match="rps round facts"):
        _build_avatar_interaction_memory_meta("en", payload, "Alice")


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
