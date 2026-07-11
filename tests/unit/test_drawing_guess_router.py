import json

import pytest

from main_routers import drawing_guess_router as dgr
from main_routers import game_router
from utils.game_route_state import _game_route_states, _route_state_key


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_sessions():
    dgr._drawing_guess_sessions.clear()
    _game_route_states.clear()
    yield
    dgr._drawing_guess_sessions.clear()
    _game_route_states.clear()


def _load_drawing_guess_context_payload(raw: str) -> dict:
    assert raw.startswith(dgr._DRAWING_GUESS_CONTEXT_BEGIN)
    assert raw.endswith(dgr._DRAWING_GUESS_CONTEXT_END)
    body = raw.removeprefix(dgr._DRAWING_GUESS_CONTEXT_BEGIN).removesuffix(dgr._DRAWING_GUESS_CONTEXT_END)
    return json.loads(body.strip())


@pytest.mark.unit
def test_drawing_guess_word_bank_has_60_easy_words_with_all_locales():
    assert len(dgr.WORDS) == 60
    assert dgr.VISION_GUESS_MAX_CANDIDATES == 60
    for word in dgr.WORDS:
        assert set(word.labels) == set(dgr.SUPPORTED_LOCALES)
        for locale in dgr.SUPPORTED_LOCALES:
            assert word.labels[locale].strip()
            assert dgr._word_hint(word, locale).strip()


@pytest.mark.unit
def test_drawing_guess_round_timers_are_five_minutes():
    assert dgr.ROUND_GUESS_SECONDS == 5 * 60
    assert dgr.ROUND_DRAW_SECONDS == 5 * 60
    assert dgr.ROUND_AI_GUESS_SECONDS == 5 * 60
    assert dgr.VISION_GUESS_TIMEOUT_SECONDS == float(5 * 60)


@pytest.mark.unit
def test_safe_llm_error_summary_redacts_image_data_and_api_key():
    err = RuntimeError("400 data:image/jpeg;base64,abcdEFG123== api_key='secret-token' tail")
    summary = dgr._safe_llm_error_summary(err)

    assert "abcdEFG123" not in summary
    assert "secret-token" not in summary
    assert "<redacted>" in summary


@pytest.mark.unit
def test_persona_game_line_prompt_gives_premise_for_free_reply():
    system_prompt, payload_raw = dgr._build_drawing_guess_game_line_prompts(
        session={"phase": "user_guessing", "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Teasing but warm companion who calls the user Partner.",
        event="user_guess_wrong",
        details={
            "guess_label": "backpack",
            "judgement": {
                "actor": "user",
                "guess_label": "backpack",
                "is_correct": False,
                "answer_revealed": False,
            },
            "allow_answer_reveal": False,
        },
        character_profile_prompt="- Speech style: YUI calls herself YUI and teases Partner with hearts.",
    )
    payload = json.loads(payload_raw)

    assert system_prompt.startswith("Teasing but warm companion who calls the user Partner.")
    assert "Character card profile fields" in system_prompt
    assert "YUI calls herself YUI" in system_prompt
    assert "stronger than the mini-game premise" in system_prompt
    assert "Temporary mini-game premise" in system_prompt
    assert "Do not invent generic mascot tropes" in system_prompt
    assert "backend-scored result" in system_prompt
    assert "Character persona excerpt" not in system_prompt
    assert payload["task"] == "free_in_character_game_reply"
    assert payload["premise"].startswith("The user's latest guess is not the answer")
    assert payload["public_details"]["guess_label"] == "backpack"
    assert payload["public_details"]["judgement"]["is_correct"] is False
    assert payload["public_details"]["judgement"]["answer_revealed"] is False
    assert payload["output"]["backend_judgement_is_authoritative"] is True
    assert "event_intent" not in payload
    assert "style" not in payload


@pytest.mark.unit
def test_persona_game_line_prompt_marks_ai_guess_roles_unambiguously():
    system_prompt, payload_raw = dgr._build_drawing_guess_game_line_prompts(
        session={"phase": "summary", "game_chat_history": []},
        locale="zh-CN",
        lanlan_name="Companion",
        master_name="Player",
        lanlan_prompt="Companion teases Player.",
        event="ai_guess_correct",
        details={"guess_label": "apple", "allow_answer_reveal": False},
        character_profile_prompt="",
    )
    payload = json.loads(payload_raw)

    assert "Follow event_roles exactly" in system_prompt
    assert "current guess" in system_prompt
    assert payload["premise"].startswith("The character is making a visual guess")
    assert payload["event_roles"]["character_role"] == "guesser"
    assert payload["event_roles"]["user_role"] == "drawer"
    assert payload["public_details"]["guess_label"] == "apple"
    assert "answer_label" not in payload["public_details"]
    assert "用户猜对了" in payload["event_roles"]["must_not_say"]


@pytest.mark.unit
def test_persona_game_line_prompt_hides_ai_guess_outcome_until_feedback():
    system_prompt, payload_raw = dgr._build_drawing_guess_game_line_prompts(
        session={"phase": "ai_guessing", "game_chat_history": []},
        locale="en",
        lanlan_name="Companion",
        master_name="Player",
        lanlan_prompt="Companion teases Player.",
        event="ai_guess_attempt",
        details={"guess_label": "cup", "allow_answer_reveal": False, "guess_feedback_pending": True},
        character_profile_prompt="",
    )
    payload = json.loads(payload_raw)

    assert "Do not say whether it is correct or wrong" in system_prompt
    assert payload["premise"].startswith("The character is making a visual guess")
    assert "has not told the character whether the guess is correct yet" in payload["premise"]
    assert payload["event_roles"]["character_role"] == "guesser"
    assert payload["public_details"]["guess_label"] == "cup"
    assert payload["public_details"]["guess_feedback_pending"] is True
    assert "answer_label" not in payload["public_details"]
    assert "guess_is_correct" not in payload["public_details"]


@pytest.mark.unit
def test_persona_game_line_prompt_marks_user_correct_as_user_draw_transition():
    system_prompt, payload_raw = dgr._build_drawing_guess_game_line_prompts(
        session={"phase": "word_picking", "game_chat_history": []},
        locale="zh-CN",
        lanlan_name="Companion",
        master_name="Player",
        lanlan_prompt="Companion teases Player.",
        event="user_guess_correct",
        details={"answer_label": "树", "allow_answer_reveal": True},
        character_profile_prompt="",
    )
    payload = json.loads(payload_raw)
    roles = payload["event_roles"]

    assert "keep the turn transition clear" in system_prompt
    assert "transition to the next turn" in payload["premise"]
    assert roles["completed_turn"]["character_role"] == "drawer"
    assert roles["completed_turn"]["user_role"] == "guesser"
    assert roles["next_turn"]["character_role"] == "guesser"
    assert roles["next_turn"]["user_role"] == "drawer"
    assert "role_boundary" in roles
    assert "must_not_say" not in roles


@pytest.mark.unit
def test_persona_chat_prompt_gives_premise_for_free_reply():
    system_prompt, payload_raw = dgr._build_drawing_guess_chat_prompts(
        session={"phase": "summary", "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Soft-spoken companion who dislikes stiff game host lines.",
        user_text="that ending was funny",
        event="summary_chat",
        character_profile_prompt="- 输出内容提示词: answer as a relaxed companion, never as a host.",
    )
    payload = json.loads(payload_raw)

    assert system_prompt.startswith("Soft-spoken companion who dislikes stiff game host lines.")
    assert "Character card profile fields" in system_prompt
    assert "answer as a relaxed companion" in system_prompt
    assert "Temporary mini-game premise" in system_prompt
    assert "Do not invent generic mascot tropes" in system_prompt
    assert "Character persona excerpt" not in system_prompt
    assert payload["task"] == "free_in_character_reply"
    assert payload["premise"].startswith("The round is over")
    assert "event_intent" not in payload
    assert "persona_style" not in payload


@pytest.mark.unit
def test_user_guessing_chat_context_gives_private_answer_without_forcing_reveal():
    system_prompt, payload_raw = dgr._build_drawing_guess_chat_prompts(
        session={"phase": "user_guessing", "ai_word_id": "banana", "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Playful companion.",
        user_text="one more?",
        event="guessing_chat",
        character_profile_prompt="",
    )
    payload = json.loads(payload_raw)

    assert "character knows it as the answer to their own drawing" in system_prompt
    assert "do not use a fixed hint template" in system_prompt.lower()
    assert payload["premise"].startswith("The user is in their guessing turn")
    assert payload["public_details"]["character_knows_own_hidden_answer"] is True
    assert payload["public_details"]["character_private_answer_label"] == "banana"
    assert payload["public_details"]["allow_character_drawing_answer_reveal"] is False
    assert payload["safety"]["do_not_reveal_hidden_answers"] is True


@pytest.mark.unit
def test_vision_guess_prompt_keeps_character_setting_first_and_wraps_context():
    system_prompt, payload_raw = dgr._build_vision_guess_prompt_parts(
        session={"phase": "ai_guessing", "ai_guess_attempts": 1, "game_chat_history": []},
        locale="zh-CN",
        lanlan_name="Companion",
        master_name="Player",
        lanlan_prompt="Companion teases Player lightly but takes the game seriously.",
        user_hint="线条有点歪",
        character_profile_prompt="- Self-reference rule: Companion must refer to themself as Companion.",
    )
    payload = _load_drawing_guess_context_payload(payload_raw)

    assert system_prompt.startswith("Companion teases Player")
    assert "Character card profile fields" in system_prompt
    assert "Companion must refer to themself as Companion" in system_prompt
    assert "Temporary mini-game task" in system_prompt
    assert "Stay in character" in system_prompt
    assert "Character persona excerpt" not in system_prompt
    assert payload["task"] == "guess_user_drawing"
    assert payload["user_hint"] == "线条有点歪"


@pytest.mark.unit
def test_text_context_guess_prompt_keeps_character_setting_first_and_wraps_context():
    system_prompt, payload_raw = dgr._build_text_context_guess_prompts(
        session={"phase": "ai_guessing", "ai_guess_attempts": 2, "game_chat_history": []},
        locale="zh-CN",
        lanlan_name="Companion",
        master_name="Player",
        lanlan_prompt="Companion teases Player lightly but takes the game seriously.",
        user_hint="有一条尾巴",
        character_profile_prompt="- Speech habit: occasionally uses a configured verbal tic.",
    )
    payload = _load_drawing_guess_context_payload(payload_raw)

    assert system_prompt.startswith("Companion teases Player")
    assert "Character card profile fields" in system_prompt
    assert "configured verbal tic" in system_prompt
    assert "The image reader is unavailable" in system_prompt
    assert "Do not claim that you can see the image" in system_prompt
    assert "Character persona excerpt" not in system_prompt
    assert payload["task"] == "guess_user_drawing_from_text_context"
    assert payload["user_hint"] == "有一条尾巴"


@pytest.mark.unit
def test_input_intent_prompt_requires_explicit_guess_word():
    system_prompt, payload_raw = dgr._build_game_input_intent_prompts(
        session={"phase": "user_guessing", "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Playful companion.",
        user_text="something flying in the sky",
        phase="user_guessing",
    )
    payload = json.loads(payload_raw)

    assert "Do not infer a candidate answer from attributes or descriptions" in system_prompt
    assert "another clue" in system_prompt
    assert payload["rules"]["guess_text_must_be_explicitly_present_in_user_text"] is True
    assert payload["rules"]["descriptions_without_answer_words_are_chat"] is True


@pytest.mark.unit
def test_word_picking_chat_context_reveals_neko_answer_without_card_options():
    system_prompt, payload_raw = dgr._build_drawing_guess_chat_prompts(
        session={
            "phase": "word_picking",
            "ai_word_id": "apple",
            "user_word_options": ["cat", "dog", "fish"],
            "game_chat_history": [],
        },
        locale="en",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Playful companion.",
        user_text="what was the answer just now?",
        event="word_picking_chat",
    )
    payload = json.loads(payload_raw)
    payload_text = json.dumps(payload, ensure_ascii=False)

    assert "user card options" not in system_prompt.lower()
    assert payload["premise"].startswith("The user already guessed your drawing")
    assert payload["public_details"]["character_drawing_answer_label"] == "apple"
    assert payload["public_details"]["allow_character_drawing_answer_reveal"] is True
    assert payload["public_details"]["user_is_privately_choosing_drawing_card"] is True
    assert payload["safety"]["do_not_reveal_or_infer_user_card_options"] is True
    assert "cat" not in payload_text
    assert "dog" not in payload_text
    assert "fish" not in payload_text


@pytest.mark.unit
def test_game_character_profile_prompt_formats_effective_profile_fields():
    profile = {
        "档案名": "水水",
        "输出内容提示词": "你将扮演{{char}}，称呼{{user}}为主人。",
        "语癖强调": "{{char}}会用喵。",
        "voice_id": "private-voice",
        "_field_order": ["语癖强调", "输出内容提示词", "voice_id"],
    }

    text = game_router._format_game_character_profile_prompt(
        profile,
        lanlan_name="水水",
        master_name="主人",
    )

    assert "- 语癖强调:" in text
    assert text.index("语癖强调") < text.index("输出内容提示词")
    assert "{{char}}" not in text
    assert "{{user}}" not in text
    assert "水水会用喵" in text
    assert "voice_id" not in text


@pytest.mark.unit
def test_model_svg_sanitizer_accepts_safe_geometry():
    word = dgr._WORD_BY_ID["apple"]
    svg, reason = dgr._sanitize_model_svg(
        '<svg viewBox="0 0 240 180"><rect x="20" y="30" width="90" height="70" rx="8" fill="#e85d5d"/><path d="M50 50 C80 20 120 60 90 110" stroke="#24303a" stroke-width="4" fill="none" stroke-linecap="round"/></svg>',
        word,
    )

    assert reason == "ok"
    assert svg is not None
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in svg
    assert "<text" not in svg.lower()
    assert "onload" not in svg.lower()


@pytest.mark.unit
def test_model_svg_sanitizer_repairs_unclosed_shape_tags():
    svg, reason = dgr._sanitize_model_svg(
        '<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35" fill="#f4cf45"></svg>',
        dgr._WORD_BY_ID["banana"],
    )

    assert reason == "ok_repaired_xml"
    assert svg is not None
    assert '<circle cx="90" cy="90" r="35" fill="#f4cf45"/>' in svg


@pytest.mark.unit
def test_model_svg_payload_parser_accepts_bare_svg_response():
    parsed = dgr._parse_model_svg_payload(
        'Sure, here is the SVG:\n<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>'
    )

    assert parsed == {
        "svg": '<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>',
        "caption": "",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_svg", "expected_reason"),
    (
        ('<svg viewBox="0 0 240 180"><script>alert(1)</script><circle cx="80" cy="80" r="30"/></svg>', "disallowed_svg_tag:script"),
        ('<svg viewBox="0 0 240 180"><text x="10" y="10">apple</text><circle cx="80" cy="80" r="30"/></svg>', "svg_answer_leak"),
        ('<svg viewBox="0 0 240 180"><circle cx="80" cy="80" r="30" onload="alert(1)"/></svg>', "svg_event_attr_disallowed"),
    ),
)
def test_model_svg_sanitizer_rejects_unsafe_or_leaking_svg(raw_svg, expected_reason):
    svg, reason = dgr._sanitize_model_svg(raw_svg, dgr._WORD_BY_ID["apple"])

    assert svg is None
    assert reason == expected_reason


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_svg",
    (
        '<svg viewBox="0 0 240 180"><circle cx="80" cy="80" r="30" fill="url(https://example.test/a)" stroke="#24303a"/></svg>',
        '<svg viewBox="0 0 240 180"><circle cx="80" cy="80" r="30" fill="url(https://example.test/a)" onclick="alert(1)" stroke="#24303a"/></svg>',
        '<svg viewBox="0 0 240 180"><image href="https://example.test/a.png" width="20" height="20"/><circle cx="80" cy="80" r="30" fill="#f4cf45"/></svg>',
        '<svg viewBox="0 0 240 180"><defs><linearGradient id="g"><stop offset="0%" stop-color="#fff"/></linearGradient></defs><circle cx="80" cy="80" r="30" fill="url(#g)" stroke="#24303a"/></svg>',
    ),
)
def test_model_svg_sanitizer_repairs_external_references(raw_svg):
    svg, reason = dgr._sanitize_model_svg(raw_svg, dgr._WORD_BY_ID["apple"])

    assert reason == "ok_repaired_external_reference"
    assert svg is not None
    assert "url(" not in svg
    assert "href" not in svg.lower()
    assert "https:" not in svg.lower()
    assert "onclick" not in svg.lower()
    assert "alert" not in svg.lower()
    assert "<image" not in svg.lower()
    assert "<defs" not in svg.lower()


@pytest.mark.unit
def test_model_svg_repair_does_not_bypass_disallowed_tags():
    svg, reason = dgr._sanitize_model_svg(
        '<svg viewBox="0 0 240 180"><script>alert(1)<circle cx="80" cy="80" r="30"></svg>',
        dgr._WORD_BY_ID["apple"],
    )

    assert svg is None
    assert reason == "disallowed_svg_tag:script"


@pytest.mark.unit
def test_vision_guess_payload_parser_accepts_natural_language_guess():
    parsed = dgr._parse_vision_guess_payload("我觉得这个看起来像 banana，弯弯的。", "en")

    assert parsed is not None
    assert parsed["guess_id"] == "banana"
    assert parsed["short_line"]


@pytest.mark.unit
def test_vision_guess_payload_parser_requires_word_boundaries():
    assert dgr._parse_vision_guess_payload("I think it is a pineapple.", "en") is None
    assert dgr._parse_vision_guess_payload("looks like a carpet to me", "en") is None


@pytest.mark.unit
def test_alias_boundary_covers_cyrillic_word_characters():
    assert dgr._contains_alias_with_guess_boundary("это кот?", "кот") is True
    assert dgr._contains_alias_with_guess_boundary("скот?", "кот") is False
    assert dgr._contains_alias_with_guess_boundary("КОТ", "кот") is True


@pytest.mark.unit
def test_cjk_modifier_prefixes_accept_correct_guesses_without_compound_false_hits():
    assert dgr._matches_word("是小猫咪吗？", dgr._WORD_BY_ID["cat"])
    assert dgr._matches_word("小白兔", dgr._WORD_BY_ID["rabbit"])
    assert dgr._matches_word("大乌龟", dgr._WORD_BY_ID["turtle"])
    assert not dgr._matches_word("热狗", dgr._WORD_BY_ID["dog"])
    assert not dgr._matches_word("是火车吗", dgr._WORD_BY_ID["car"])
    assert not dgr._matches_word("月球", dgr._WORD_BY_ID["ball"])


@pytest.mark.unit
def test_word_matching_accepts_synonyms_and_multilingual_variants():
    assert dgr._matches_word("bunny?", dgr._WORD_BY_ID["rabbit"])
    assert dgr._matches_word("\u6708\u7403", dgr._WORD_BY_ID["moon"])
    assert dgr._matches_word("avion", dgr._WORD_BY_ID["airplane"])
    assert dgr._matches_word("ma\u00e7\u00e3", dgr._WORD_BY_ID["apple"])


@pytest.mark.unit
def test_user_guess_extraction_uses_alias_boundaries():
    assert dgr._extract_user_guess_word("Is it cat?").id == "cat"
    assert dgr._extract_user_guess_word("\u8fd9\u662f\u72d7\u5417\uff1f").id == "dog"
    assert dgr._extract_user_guess_word("\u6c34\u676f").id == "cup"
    assert dgr._extract_user_guess_word("Is it concatenate?") is None
    assert dgr._extract_user_guess_word("is that it?") is None
    assert dgr._extract_user_guess_word("is it pineapple?") is None
    assert dgr._extract_user_guess_word("is it scar?") is None
    assert dgr._extract_user_guess_word("\u8fd9\u662f\u70ed\u72d7\u5417\uff1f") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_round_start_does_not_expose_candidates_or_hidden_ai_answer():
    result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-1",
        "i18n_language": "zh-CN",
    }))

    assert result["ok"] is True
    assert result["state"]["phase"] == "ai_drawing"
    assert result["state"]["user_draw_answer"] is None
    payload_text = str(result)
    assert "candidates" not in payload_text
    assert "aliases" not in payload_text
    assert "forbidden" not in payload_text
    assert "ai_word" not in payload_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_debug_round_start_can_begin_at_word_picking_without_hidden_ai_answer():
    result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-debug-word-pick",
        "i18n_language": "zh-CN",
        "debug_start_phase": "word_picking",
    }))

    assert result["ok"] is True
    assert result["phase"] == "word_picking"
    assert result["state"]["phase"] == "word_picking"
    assert result["state"]["user_draw_answer"] is None
    assert len(result["user_draw_options"]) == dgr.USER_DRAW_OPTION_COUNT
    assert result["draw_seconds"] == dgr.ROUND_DRAW_SECONDS
    payload_text = str(result)
    assert "aliases" not in payload_text
    assert "forbidden" not in payload_text
    assert "ai_word" not in payload_text


@pytest.mark.unit
def test_word_cycle_starts_with_two_random_pools_and_rolls_over_at_six_left():
    cycle = dgr._new_word_cycle_state()
    first_cycle_pool = set(cycle["pool1"])
    second_cycle_pool = set(cycle["pool2"])

    assert len(first_cycle_pool) == dgr.WORD_DEDUP_POOL_SIZE
    assert len(second_cycle_pool) == len(dgr.WORDS) - dgr.WORD_DEDUP_POOL_SIZE
    assert not first_cycle_pool & second_cycle_pool
    assert first_cycle_pool | second_cycle_pool == {word.id for word in dgr.WORDS}
    assert cycle["active_pool"] == "pool1"

    first_draw_count = dgr.WORD_DEDUP_POOL_SIZE - dgr.WORD_DEDUP_ROLLOVER_REMAINING
    first_drawn = dgr._draw_word_ids_from_cycle(cycle, first_draw_count)
    first_leftovers = first_cycle_pool - set(first_drawn)
    assert len(first_drawn) == first_draw_count
    assert len(first_leftovers) == dgr.WORD_DEDUP_ROLLOVER_REMAINING
    assert set(first_drawn) <= first_cycle_pool
    assert set(cycle["pool1"]) == set(first_drawn)
    assert first_leftovers <= set(cycle["pool2"])
    assert second_cycle_pool <= set(cycle["pool2"])
    assert cycle["active_pool"] == "pool2"

    second_draw_count = len(cycle["pool2"]) - dgr.WORD_DEDUP_ROLLOVER_REMAINING
    second_drawn = dgr._draw_word_ids_from_cycle(cycle, second_draw_count)
    second_leftovers = (second_cycle_pool | first_leftovers) - set(second_drawn)
    assert len(second_drawn) == second_draw_count
    assert len(second_leftovers) == dgr.WORD_DEDUP_ROLLOVER_REMAINING
    assert set(cycle["pool2"]) == set(second_drawn)
    assert set(cycle["pool1"]) == set(first_drawn) | second_leftovers
    assert cycle["active_pool"] == "pool1"

    recycled = dgr._draw_word_ids_from_cycle(cycle, 1)
    assert len(recycled) == 1
    assert recycled[0] in set(first_drawn) | second_leftovers
    assert cycle["active_pool"] == "pool1"


@pytest.mark.unit
def test_user_word_options_do_not_exclude_until_choice_is_confirmed():
    cycle = dgr._new_word_cycle_state()
    before_remaining = list(cycle["remaining_ids"])

    options = dgr._pick_user_word_options(cycle)
    option_ids = [word.id for word in options]

    assert len(option_ids) == dgr.USER_DRAW_OPTION_COUNT
    assert set(option_ids) <= set(before_remaining)
    assert cycle["remaining_ids"] == before_remaining

    chosen_id = option_ids[0]
    dgr._exclude_word_id_from_cycle(cycle, chosen_id)

    assert chosen_id not in cycle["remaining_ids"]
    assert set(option_ids[1:]) <= set(cycle["remaining_ids"])


@pytest.mark.unit
def test_public_round_state_exposes_word_cycle_counts_without_word_ids():
    cycle = dgr._new_word_cycle_state()
    drawn_ids = dgr._draw_word_ids_from_cycle(cycle, 2)
    session = {
        "round_id": "round-1",
        "phase": "ai_drawing",
        "word_cycle": cycle,
    }

    public_state = dgr._public_round_state(session, "en")
    word_cycle = public_state["word_cycle"]

    assert word_cycle["active_pool"] == "pool1"
    assert word_cycle["pools"]["pool1"] == {
        "remaining_count": dgr.WORD_DEDUP_POOL_SIZE - len(drawn_ids),
        "locked": False,
    }
    assert word_cycle["pools"]["pool2"] == {
        "remaining_count": len(dgr.WORDS) - dgr.WORD_DEDUP_POOL_SIZE,
        "locked": True,
    }
    assert word_cycle["request_locked"] is False
    assert word_cycle["rollover_remaining"] == dgr.WORD_DEDUP_ROLLOVER_REMAINING
    serialized = json.dumps(word_cycle)
    assert "remaining_ids" not in serialized
    assert all(word_id not in serialized for word_id in drawn_ids)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_round_state_marks_word_cycle_request_lock():
    session = {
        "round_id": "round-1",
        "phase": "ai_drawing",
        "word_cycle": dgr._new_word_cycle_state(),
    }
    lock = dgr._get_session_lock(session)
    await lock.acquire()
    try:
        public_state = dgr._public_round_state(session, "en")
        assert public_state["word_cycle"]["request_locked"] is True
    finally:
        lock.release()


@pytest.mark.unit
def test_every_drawing_guess_word_has_non_heart_static_fallback():
    heart_svg = dgr._fallback_svg("heart")
    for word in dgr.WORDS:
        if word.id == "heart":
            continue
        assert dgr._fallback_svg(word.id) != heart_svg, word.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_round_start_normalizes_memory_consent():
    default_result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-memory-default",
        "i18n_language": "zh-CN",
        "memory_consent": "saved",
    }))
    summary_result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-memory-summary",
        "i18n_language": "zh-CN",
        "memory_consent": "summary",
    }))

    assert default_result["ok"] is True
    assert summary_result["ok"] is True
    assert dgr._drawing_guess_sessions["YUI:dg-memory-default"]["memory_consent"] == "none"
    assert dgr._drawing_guess_sessions["YUI:dg-memory-summary"]["memory_consent"] == "summary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_summary_skips_without_consent(monkeypatch):
    async def fail_post(*_args, **_kwargs):
        raise AssertionError("memory should not be written without explicit summary consent")

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", fail_post)
    session = {
        "lanlan_name": "YUI",
        "session_id": "dg-memory-skip",
        "memory_consent": "none",
        "ai_word_id": "apple",
        "user_score": 1,
        "ai_score": 0,
    }

    result = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session,
        locale="zh-CN",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )

    assert result == {"status": "skipped", "reason": "memory_consent_none"}
    assert session["memory_summary_result"] == result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_summary_posts_sanitized_compact_result_once(monkeypatch):
    captured: list[tuple[str, str]] = []

    async def fake_post(lanlan_name, summary):
        captured.append((lanlan_name, summary))
        return {"status": "written", "source": "memory_server_cache", "count": 1}

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", fake_post)
    session = {
        "lanlan_name": "YUI",
        "session_id": "dg-memory-write",
        "memory_consent": "summary",
        "ai_word_id": "apple",
        "user_score": 1,
        "ai_score": 0,
        "game_chat_history": [
            {
                "role": "user",
                "kind": "chat",
                "text": "data:image/png;base64,abcdef <svg><text>secret</text></svg>",
            }
        ],
    }

    result = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session,
        locale="zh-CN",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )
    second = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session,
        locale="zh-CN",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )

    assert result == {"status": "written", "source": "memory_server_cache", "count": 1}
    assert second == result
    assert len(captured) == 1
    assert captured[0][0] == "YUI"
    summary = captured[0][1]
    assert "YUI" in summary
    assert "苹果" in summary
    assert "狗" in summary
    assert "猫" in summary
    assert "data:image" not in summary
    assert "<svg" not in summary
    assert "secret" not in summary
    assert len(summary) <= dgr.MEMORY_SUMMARY_MAX_CHARS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_client_round_token_is_rejected_before_mutating_session():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-stale-token",
        "i18n_language": "zh-CN",
        "client_round_token": 2,
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-stale-token"]
    assert session["phase"] == "ai_drawing"

    result = await dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-stale-token",
        "i18n_language": "zh-CN",
        "client_round_token": 1,
    }))

    assert result == {"ok": False, "reason": "stale_round_flow"}
    assert session["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_client_round_token_is_stale_when_session_has_token():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-missing-token",
        "i18n_language": "zh-CN",
        "client_round_token": 2,
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-missing-token"]

    result = await dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-missing-token",
        "i18n_language": "zh-CN",
    }))

    assert result == {"ok": False, "reason": "stale_round_flow"}
    assert session["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_round_start_syncs_client_round_token_to_active_route_state():
    route_state = {
        "game_route_active": True,
        "session_id": "dg-route-token-sync",
        "last_state": {},
    }
    _game_route_states[_route_state_key("YUI", "drawing_guess")] = route_state

    result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-route-token-sync",
        "i18n_language": "en",
        "client_round_token": "round-2",
    }))

    assert result["ok"] is True
    assert result["state"]["client_round_token"] == "round-2"
    assert route_state["client_round_token"] == "round-2"
    assert route_state["last_state"]["client_round_token"] == "round-2"
    assert route_state["last_state"]["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_transcript_uses_active_session_token_when_route_state_is_stale(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-external-stale-route-token",
        "i18n_language": "en",
        "client_round_token": "new-token",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-external-stale-route-token"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "drawing_chat"
        return "Keep sketching that shape."

    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-external-stale-route-token",
        "still drawing",
        route_state={
            "last_state": {
                "phase": "user_drawing",
                "i18n_language": "en",
                "client_round_token": "old-token",
            },
            "client_round_token": "old-token",
        },
        request_id="voice-stale-route-token",
        kind="user-text",
    )

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["message"] == "Keep sketching that shape."
    assert session["phase"] == "user_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_draw_rejects_concurrent_session_request():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-busy",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-busy"]
    lock = dgr._get_session_lock(session)
    await lock.acquire()
    try:
        result = await dgr.drawing_guess_ai_draw(_FakeRequest({
            "lanlan_name": "YUI",
            "session_id": "dg-busy",
            "i18n_language": "zh-CN",
        }))
    finally:
        lock.release()

    assert result["ok"] is False
    assert result["reason"] == "session_busy"
    assert result["state"]["phase"] == "ai_drawing"
    assert session["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_rejects_concurrent_session_request():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-timeout-busy",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-timeout-busy"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"
    lock = dgr._get_session_lock(session)
    await lock.acquire()
    try:
        result = await dgr.drawing_guess_timeout(_FakeRequest({
            "lanlan_name": "YUI",
            "session_id": "dg-timeout-busy",
            "i18n_language": "en",
        }))
    finally:
        lock.release()

    assert result["ok"] is False
    assert result["reason"] == "session_busy"
    assert result["state"]["phase"] == "user_guessing"
    assert session["phase"] == "user_guessing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_choose_word_rejects_concurrent_session_request():
    start = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-choice-busy",
        "i18n_language": "en",
        "debug_start_phase": "word_picking",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-choice-busy"]
    lock = dgr._get_session_lock(session)
    await lock.acquire()
    try:
        result = await dgr.drawing_guess_choose_word(_FakeRequest({
            "lanlan_name": "YUI",
            "session_id": "dg-choice-busy",
            "i18n_language": "en",
            "word_id": start["user_draw_options"][0]["id"],
        }))
    finally:
        lock.release()

    assert result["ok"] is False
    assert result["reason"] == "session_busy"
    assert result["state"]["phase"] == "word_picking"
    assert session.get("user_word_id") is None
    assert session["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_draw_and_user_guess_advance_to_word_pick_before_user_drawing():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-2",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-2"]
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]

    drawing = await dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-2",
        "i18n_language": "zh-CN",
    }))
    assert drawing["ok"] is True
    assert drawing["phase"] == "user_guessing"
    assert "<svg" in drawing["drawing"]["svg"]
    assert "<text" not in drawing["drawing"]["svg"].lower()
    assert drawing["drawing"]["source"] in {"model_svg", "fallback_static"}

    guess = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-2",
        "i18n_language": "zh-CN",
        "text": "是苹果吧",
    }))
    assert guess["ok"] is True
    assert guess["correct"] is True
    assert guess["state"]["phase"] == "word_picking"
    assert guess["state"]["scores"]["user"] == 1
    assert guess["state"]["user_draw_answer"] is None
    assert [word["id"] for word in guess["user_draw_options"]] == ["cat", "dog", "fish"]
    assert "aliases" not in str(guess)

    choice = await dgr.drawing_guess_choose_word(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-2",
        "i18n_language": "zh-CN",
        "word_id": "dog",
    }))
    assert choice["ok"] is True
    assert choice["state"]["phase"] == "user_drawing"
    assert choice["user_draw_answer"]["id"] == "dog"
    assert session["user_word_id"] == "dog"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_word_choice_rejects_words_outside_dealt_options():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-invalid-choice",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-invalid-choice"]
    session["phase"] = "word_picking"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]

    choice = await dgr.drawing_guess_choose_word(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-invalid-choice",
        "i18n_language": "en",
        "word_id": "banana",
    }))

    assert choice["ok"] is False
    assert choice["reason"] == "invalid_word_choice"
    assert session.get("user_word_id") is None
    assert choice["state"]["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_model_drawing_retries_after_rejected_svg(monkeypatch):
    from main_routers import game_router

    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A playful companion who draws simple cute shapes.",
        "model": "test-model",
        "base_url": "",
        "api_key": "",
    })
    prompts = []

    async def fake_call_drawing_guess_svg_model(**kwargs):
        prompts.append(kwargs["user_prompt"])
        if len(prompts) == 1:
            return '<svg viewBox="0 0 240 180"><script>alert(1)</script><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>'
        return '<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>'

    monkeypatch.setattr(dgr, "_call_drawing_guess_svg_model", fake_call_drawing_guess_svg_model)

    drawing = await dgr._generate_model_drawing(dgr._WORD_BY_ID["banana"], "en", "YUI")

    assert drawing is not None
    assert drawing["source"] == "model_svg"
    assert drawing["sanitizer"] == {"ok": True, "attempt": 2}
    assert len(prompts) == 2
    assert "previous_rejection_reason" not in prompts[0]
    assert "disallowed_svg_tag:script" in prompts[1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_draw_uses_sanitized_model_svg_when_available(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-model-svg",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-model-svg"]
    session["ai_word_id"] = "banana"

    async def fake_generate(word, locale, lanlan_name):
        assert word.id == "banana"
        assert locale == "en"
        assert lanlan_name == "YUI"
        return {
            "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 180" role="img" aria-hidden="true"><path d="M40 80 C90 140 170 120 200 60" stroke="#d7a629" stroke-width="8" fill="none"/></svg>',
            "caption": "curved yellow snack",
            "source": "model_svg",
            "sanitizer": {"ok": True},
        }

    monkeypatch.setattr(dgr, "_generate_model_drawing", fake_generate)

    drawing = await dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-model-svg",
        "i18n_language": "en",
    }))

    assert drawing["ok"] is True
    assert drawing["drawing"]["source"] == "model_svg"
    assert drawing["drawing"]["caption"] == "curved yellow snack"
    assert "<script" not in drawing["drawing"]["svg"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_draw_returns_persona_game_line(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-line",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-ai-line"]
    session["ai_word_id"] = "banana"

    async def fake_generate(word, locale, lanlan_name):
        assert word.id == "banana"
        assert locale == "en"
        assert lanlan_name == "YUI"
        return {
            "svg": '<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35"/></svg>',
            "caption": "",
            "source": "model_svg",
            "sanitizer": {"ok": True, "attempt": 1},
        }

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "ai_drawing_ready"
        assert kwargs["fallback"]
        return "I finished it. Guess before I get smug.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_model_drawing", fake_generate)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    result = await dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-line",
        "i18n_language": "en",
    }))

    assert result["ok"] is True
    assert result["message"] == "I finished it. Guess before I get smug."
    assert result["message_source"] == "persona_model"
    assert session["game_chat_history"][-1]["kind"] == "game_line"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_drawing_chat_uses_persona_reply(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-chat"]
    session["phase"] = "user_drawing"

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "drawing_chat"
        assert kwargs["user_text"] == "this part is tricky"
        return "I am watching that little corner closely."

    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-chat",
        "i18n_language": "en",
        "text": "this part is tricky",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["source"] == "persona_model"
    assert result["message"] == "I am watching that little corner closely."
    assert session["game_chat_history"][-2]["role"] == "user"
    assert session["game_chat_history"][-1]["role"] == "assistant"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_voice_user_drawing_with_canvas_runs_live_vision_guess(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-live-voice",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-live-voice"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 0

    async def fake_vision_guess(**kwargs):
        assert kwargs["image_data_url"] == "data:image/png;base64,abc"
        assert kwargs["user_hint"] == "what does it look like now"
        assert kwargs["session"]["ai_guess_attempts"] == 1
        return {
            "word": dgr._WORD_BY_ID["apple"],
            "confidence": 0.5,
            "message": "I am going to say apple for now.",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)

    result = await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-live-voice",
        "what does it look like now",
        route_state={
            "last_state": {"phase": "user_drawing", "i18n_language": "en"},
            "last_canvas_image_data_url": "data:image/png;base64,abc",
        },
        request_id="voice-live-1",
    )

    assert result["ok"] is True
    assert result["kind"] == "ai_guess"
    assert result["source"] == "vision_model"
    assert result["live_preview"] is True
    assert result["correct"] is False
    assert result["state"]["phase"] == "user_drawing"
    assert session["phase"] == "user_drawing"
    assert session["ai_guess_attempts"] == 0
    assert session["live_voice_guess_attempts"] == 1
    assert session["game_chat_history"][-2]["kind"] == "live_voice_hint"
    assert session["game_chat_history"][-1]["kind"] == "vision_guess"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_drawing_typed_chat_with_canvas_does_not_trigger_live_vision(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-typed-canvas-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-typed-canvas-chat"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"

    async def fail_vision_guess(**_kwargs):
        raise AssertionError("typed drawing chat should not trigger live vision")

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "drawing_chat"
        assert kwargs["user_text"] == "this part is tricky"
        return "Keep drawing, I am watching."

    monkeypatch.setattr(dgr, "_generate_vision_guess", fail_vision_guess)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-typed-canvas-chat",
        "i18n_language": "en",
        "text": "this part is tricky",
        "input_kind": "user-text",
        "image_data_url": "data:image/png;base64,abc",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["message"] == "Keep drawing, I am watching."
    assert session["phase"] == "user_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_plain_chat_does_not_count_as_guess(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-guess-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-guess-chat"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "cat"

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "guessing_chat"
        assert kwargs["user_text"] == "the cat energy in this drawing is cute"
        return "I will accept that as a compliment, not a guess."

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "user_guessing"
        return {"intent": "chat", "guess_text": "", "confidence": 0.92}

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-guess-chat",
        "i18n_language": "en",
        "text": "the cat energy in this drawing is cute",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["source"] == "persona_model"
    assert result["message"] == "I will accept that as a compliment, not a guess."
    assert session["phase"] == "user_guessing"
    assert session["user_score"] == 0
    assert session["game_chat_history"][-2]["kind"] == "chat"
    assert session["game_chat_history"][-1]["kind"] == "chat_reply"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_word_picking_chat_does_not_expose_user_card_options(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-word-pick-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-word-pick-chat"]
    session["phase"] = "word_picking"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "word_picking_chat"
        assert kwargs["user_text"] == "what was your drawing again?"
        return "It was apple, and your next card is your own little secret."

    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-word-pick-chat",
        "i18n_language": "en",
        "text": "what was your drawing again?",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["message"] == "It was apple, and your next card is your own little secret."
    assert session["phase"] == "word_picking"
    assert "user_draw_options" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_hint_request_uses_persona_game_line(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-hint-line",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-hint-line"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"
    hint_details: list[dict] = []

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "hint_request"
        details = kwargs["details"]
        assert "safe_hint" not in details
        assert "previous_safe_hints" not in details
        assert "indirect_hint_levels" not in details
        assert "safe_hints_exhausted" not in details
        assert "answer_label" not in details
        assert "hint_number" not in details
        assert details["character_private_answer_label"] == "banana"
        assert details["allow_answer_reveal"] is False
        assert details["generate_hint_from_answer"] is True
        assert details["do_not_use_fixed_hint_template"] is True
        hint_details.append(dict(details))
        return f"fresh clue {len(hint_details)}", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-hint-line",
        "i18n_language": "en",
        "text": "hint please",
    }))

    assert result["ok"] is True
    assert result["kind"] == "hint"
    assert result["message_source"] == "persona_model"
    assert result["message"] == "fresh clue 1"

    second = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-hint-line",
        "i18n_language": "en",
        "text": "another hint please",
    }))

    assert second["ok"] is True
    assert second["kind"] == "hint"
    assert second["message"] == "fresh clue 2"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_user_hint_requests_keep_answer_private(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-repeated-hint",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-repeated-hint"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"
    hint_details: list[dict] = []

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "hint_request"
        details = kwargs["details"]
        assert "safe_hint" not in details
        assert "direct_hint" not in details
        assert "previous_safe_hints" not in details
        assert "indirect_hint_levels" not in details
        assert "safe_hints_exhausted" not in details
        assert "answer_label" not in details
        assert "hint_number" not in details
        assert details["character_private_answer_label"] == "banana"
        assert details["allow_answer_reveal"] is False
        assert details["generate_hint_from_answer"] is True
        assert details["do_not_use_fixed_hint_template"] is True
        hint_details.append(dict(details))
        return f"fresh repeated clue {len(hint_details)}", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-repeated-hint",
        "i18n_language": "en",
        "text": "one more hint please",
    }))

    assert result["ok"] is True
    assert result["kind"] == "hint"
    assert result["message_source"] == "persona_model"
    assert result["message"] == "fresh repeated clue 1"

    second = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-repeated-hint",
        "i18n_language": "en",
        "text": "another hint please",
    }))

    assert second["ok"] is True
    assert second["kind"] == "hint"
    assert second["message_source"] == "persona_model"
    assert second["message"] == "fresh repeated clue 2"
    assert len(hint_details) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_answer_request_can_reveal_without_fixed_template(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-direct-answer",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-direct-answer"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "hint_request"
        details = kwargs["details"]
        assert "safe_hint" not in details
        assert "direct_hint" not in details
        assert "previous_safe_hints" not in details
        assert "indirect_hint_levels" not in details
        assert "safe_hints_exhausted" not in details
        assert details["character_private_answer_label"] == "香蕉"
        assert details["answer_label"] == "香蕉"
        assert details["allow_answer_reveal"] is True
        assert details["generate_hint_from_answer"] is True
        assert details["do_not_use_fixed_hint_template"] is True
        return "自己问的喔，答案是香蕉。", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-direct-answer",
        "i18n_language": "zh-CN",
        "text": "不猜了，直接告诉我答案",
    }))

    assert result["ok"] is True
    assert result["kind"] == "hint"
    assert result["message_source"] == "persona_model"
    assert result["message"] == "自己问的喔，答案是香蕉。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_explicit_guess_still_counts():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-explicit-guess",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-explicit-guess"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-explicit-guess",
        "i18n_language": "en",
        "text": "is it a banana?",
    }))

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is True
    assert result["answer"]["id"] == "banana"
    assert result["state"]["phase"] == "word_picking"
    assert len(result["user_draw_options"]) == dgr.USER_DRAW_OPTION_COUNT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_wrong_guess_sends_backend_judgement(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-backend-judgement-wrong",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-backend-judgement-wrong"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "chair"

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "user_guess_wrong"
        details = kwargs["details"]
        assert details["guess_label"] == "backpack"
        assert details["allow_answer_reveal"] is False
        assert "answer_label" not in details
        assert details["judgement"] == {
            "actor": "user",
            "guess_label": "backpack",
            "is_correct": False,
            "answer_revealed": False,
        }
        return "Not that one yet.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-backend-judgement-wrong",
        "i18n_language": "en",
        "text": "backpack",
    }))

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is False
    assert result["message"] == "Not that one yet."
    assert session["phase"] == "user_guessing"
    assert session["user_score"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_synonym_counts_as_correct_guess():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-synonym-guess",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-synonym-guess"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "rabbit"

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-synonym-guess",
        "i18n_language": "en",
        "text": "maybe it's a bunny?",
    }))

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is True
    assert result["answer"]["id"] == "rabbit"
    assert result["state"]["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_intent_classifier_allows_mixed_guess(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-mixed-guess",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-mixed-guess"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "user_guessing"
        assert kwargs["user_text"] == "banana maybe, but your line is so dramatic"
        return {"intent": "guess", "guess_text": "banana", "confidence": 0.86}

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-mixed-guess",
        "i18n_language": "en",
        "text": "banana maybe, but your line is so dramatic",
    }))

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is True
    assert result["answer"]["id"] == "banana"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guessing_description_inference_stays_chat(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-description-chat",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-description-chat"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "kite"

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "user_guessing"
        assert kwargs["user_text"] == "飞在天上的"
        return {"intent": "guess", "guess_text": "风筝", "confidence": 0.95}

    async def fake_game_line(**_kwargs):
        raise AssertionError("description-only text should not be scored as a guess")

    async def fake_chat_line(**kwargs):
        assert kwargs["event"] == "guessing_chat"
        assert kwargs["user_text"] == "飞在天上的"
        return "可以先继续聊，想认真猜的时候再把答案词说出来。"

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_chat_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-description-chat",
        "i18n_language": "zh-CN",
        "text": "飞在天上的",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert "answer" not in result
    assert session["phase"] == "user_guessing"
    assert session["user_score"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_guess_feedback_plain_chat_does_not_trigger_retry(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-chat"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1

    async def fail_vision_guess(**_kwargs):
        raise AssertionError("plain feedback chat should not trigger a vision retry")

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "guess_feedback_chat"
        assert kwargs["user_text"] == "that was a funny guess"
        return "I had confidence for about half a second."

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "ai_guess_feedback"
        return {"intent": "chat", "guess_text": "", "confidence": 0.9}

    monkeypatch.setattr(dgr, "_generate_vision_guess", fail_vision_guess)
    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-chat",
        "i18n_language": "en",
        "text": "that was a funny guess",
        "image_data_url": "data:image/png;base64,not-used",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["source"] == "persona_model"
    assert session["phase"] == "ai_guess_feedback"
    assert session["ai_guess_attempts"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_guess_feedback_guess_intent_still_stays_chat(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-guess-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-guess-chat"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1

    async def fail_vision_guess(**_kwargs):
        raise AssertionError("guess-intent feedback chat should not force a vision retry")

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "guess_feedback_chat"
        return "I can chat about that guess before trying again."

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "ai_guess_feedback"
        return {"intent": "guess", "guess_text": "banana", "confidence": 0.99}

    monkeypatch.setattr(dgr, "_generate_vision_guess", fail_vision_guess)
    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-guess-chat",
        "i18n_language": "en",
        "text": "banana? that guess was funny",
        "image_data_url": "data:image/png;base64,not-logged",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["message"] == "I can chat about that guess before trying again."
    assert session["phase"] == "ai_guess_feedback"
    assert session["ai_guess_attempts"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_phase_still_accepts_persona_chat(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-summary-chat",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-summary-chat"]
    session["phase"] = "summary"

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "summary_chat"
        assert kwargs["user_text"] == "that ending was funny"
        return "I am absolutely counting that as dramatic teamwork."

    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-summary-chat",
        "i18n_language": "en",
        "text": "that ending was funny",
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["source"] == "persona_model"
    assert result["message"] == "I am absolutely counting that as dramatic teamwork."
    assert session["phase"] == "summary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_guess_feedback_hint_triggers_retry(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-hint",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-hint"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1

    async def fake_vision_guess(**kwargs):
        assert kwargs["user_hint"] == "hint: it is yellow"
        return {
            "word": dgr._WORD_BY_ID["banana"],
            "confidence": 0.9,
            "message": "Then I will switch to banana.",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-hint",
        "i18n_language": "en",
        "text": "hint: it is yellow",
        "image_data_url": "data:image/png;base64,not-used",
    }))

    assert result["ok"] is True
    assert result["kind"] == "ai_guess"
    assert result["source"] == "vision_model"
    assert result["correct"] is True
    assert result["state"]["phase"] == "summary"
    assert session["ai_guess_attempts"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_guess_feedback_intent_classifier_can_trigger_retry(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-soft-hint",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-soft-hint"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "ai_guess_feedback"
        assert kwargs["user_text"] == "closer to breakfast than a vehicle"
        return {"intent": "hint", "guess_text": "", "confidence": 0.82}

    async def fake_vision_guess(**kwargs):
        assert kwargs["user_hint"] == "closer to breakfast than a vehicle"
        return {
            "word": dgr._WORD_BY_ID["banana"],
            "confidence": 0.8,
            "message": "Breakfast clue received.",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-soft-hint",
        "i18n_language": "en",
        "text": "closer to breakfast than a vehicle",
        "image_data_url": "data:image/png;base64,not-used",
    }))

    assert result["ok"] is True
    assert result["kind"] == "ai_guess"
    assert result["correct"] is True
    assert session["ai_guess_attempts"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_guess_uses_model_structured_guess(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-model",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-vision-model"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"

    async def fake_vision_guess(**kwargs):
        assert kwargs["user_hint"] == "yellow and curved"
        return {
            "word": dgr._WORD_BY_ID["banana"],
            "confidence": 0.91,
            "message": "That has to be a banana.",
            "source": "vision_model",
        }

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "ai_guess_attempt"
        details = kwargs["details"]
        assert details["guess_label"] == "banana"
        assert details["allow_answer_reveal"] is False
        assert details["guess_feedback_pending"] is True
        assert details["speak_as_visual_guess"] is True
        assert details["do_not_imply_prior_knowledge"] is True
        assert "guess_is_correct" not in details
        assert "answer_label" not in details
        return "I guessed banana from that curve.", "persona_model"

    async def fake_summary_evaluation(**kwargs):
        assert kwargs["correct"] is True
        assert kwargs["answer"].id == "banana"
        assert kwargs["guessed_word"].id == "banana"
        return "This drawing has a smug little banana curve.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", fake_summary_evaluation)

    result = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-model",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
        "user_hint": "yellow and curved",
    }))

    assert result["ok"] is True
    assert result["source"] == "vision_model"
    assert result["confidence"] == 0.91
    assert result["correct"] is True
    assert result["state"]["phase"] == "summary"
    assert result["state"]["scores"]["neko"] == 1
    assert result["message"] == "I guessed banana from that curve."
    assert result["evaluation"] == "This drawing has a smug little banana curve."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_guess_uses_text_context_model_when_vision_unavailable(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-text-model",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-text-model"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"
    dgr._append_game_chat(session, "user", "I am drawing something curved.", kind="chat")

    async def fake_vision_guess(**kwargs):
        assert kwargs["user_hint"] == "yellow food"
        return None

    async def fake_text_context_guess(**kwargs):
        assert kwargs["user_hint"] == "yellow food"
        assert any(item["kind"] == "hint" for item in dgr._recent_drawing_context_payload(kwargs["session"]))
        return {
            "word": dgr._WORD_BY_ID["banana"],
            "confidence": 0.66,
            "message": "Then I will guess banana.",
            "source": "text_context_model",
        }

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "ai_guess_attempt"
        details = kwargs["details"]
        assert details["guess_label"] == "banana"
        assert details["allow_answer_reveal"] is False
        assert details["guess_feedback_pending"] is True
        assert details["speak_as_visual_guess"] is True
        assert details["do_not_imply_prior_knowledge"] is True
        assert "guess_is_correct" not in details
        assert "answer_label" not in details
        return "Then I will lock in banana.", "persona_model"

    async def fake_summary_evaluation(**kwargs):
        assert kwargs["correct"] is True
        assert kwargs["answer"].id == "banana"
        assert kwargs["guessed_word"].id == "banana"
        return "The curved little thing reads clearly enough.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)
    monkeypatch.setattr(dgr, "_generate_text_context_guess", fake_text_context_guess)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", fake_summary_evaluation)

    result = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-text-model",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
        "user_hint": "yellow food",
    }))

    assert result["ok"] is True
    assert result["source"] == "text_context_model"
    assert result["confidence"] == 0.66
    assert result["correct"] is True
    assert result["state"]["phase"] == "summary"
    assert result["message"] == "Then I will lock in banana."
    assert result["evaluation"] == "The curved little thing reads clearly enough."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_endpoint_uses_image_url_guess(monkeypatch):
    class _FakeConfigManager:
        def get_model_api_config(self, model_type):
            assert model_type == "vision"
            return {
                "model": "test-vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "test-key",
            }

    async def fake_prepare_image(_value):
        return "data:image/jpeg;base64,YWJj"

    calls = []

    class _FakeVisionLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def ainvoke(self, messages):
            calls.append({"messages": messages, "mode": "invoke"})
            return type("_Result", (), {
                "content": '{"guess_id":"banana","confidence":0.82,"short_line":"Looks like banana."}'
            })()

    async def fake_create_chat_llm_async(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return _FakeVisionLLM()

    monkeypatch.setattr(dgr, "_prepare_vision_image_data_url", fake_prepare_image)

    import utils.config_manager as config_manager
    import utils.llm_client as llm_client
    from main_routers import game_router

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _FakeConfigManager())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fake_create_chat_llm_async)
    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A warm companion.",
    })

    result = await dgr._generate_vision_guess(
        session={"session_id": "dg-vision", "ai_guess_attempts": 1, "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        image_data_url="data:image/png;base64,ignored",
        user_hint="yellow",
    )

    assert result is not None
    assert result["word"].id == "banana"
    assert result["confidence"] == 0.82
    assert result["message"] == "Looks like banana."
    assert result["source"] == "vision_model"
    assert calls[0]["kwargs"]["model"] == "test-vision-model"
    assert calls[0]["kwargs"]["base_url"] == "https://vision.example.test/v1"
    assert calls[0]["kwargs"]["api_key"] == "test-key"
    assert "streaming" not in calls[0]["kwargs"]
    assert calls[1]["mode"] == "invoke"
    vision_messages = calls[1]["messages"]
    assert vision_messages[1].content[0]["type"] == "image_url"
    assert vision_messages[1].content[0]["image_url"]["url"] == "data:image/jpeg;base64,YWJj"
    assert vision_messages[1].content[1]["type"] == "text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_endpoint_falls_back_when_payload_unparseable(monkeypatch):
    class _FakeConfigManager:
        def get_model_api_config(self, model_type):
            assert model_type == "vision"
            return {
                "model": "test-vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "test-key",
            }

    async def fake_prepare_image(_value):
        return "data:image/jpeg;base64,YWJj"

    class _FakeVisionLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def ainvoke(self, _messages):
            return type("_Result", (), {"content": "not json"})()

    async def fake_create_chat_llm_async(*_args, **_kwargs):
        return _FakeVisionLLM()

    monkeypatch.setattr(dgr, "_prepare_vision_image_data_url", fake_prepare_image)

    import utils.config_manager as config_manager
    import utils.llm_client as llm_client
    from main_routers import game_router

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _FakeConfigManager())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fake_create_chat_llm_async)
    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A warm companion.",
    })

    result = await dgr._generate_vision_guess(
        session={"session_id": "dg-vision", "ai_guess_attempts": 1, "game_chat_history": []},
        locale="en",
        lanlan_name="YUI",
        image_data_url="data:image/png;base64,abc",
        user_hint="yellow",
    )

    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_guess_revalidates_phase_after_session_lock(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-stale-phase",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-vision-stale-phase"]
    session["phase"] = "ai_guessing"
    session["user_word_id"] = "banana"

    class _FakeLock:
        def release(self):
            pass

    async def fake_acquire(session_arg, _locale):
        session_arg["phase"] = "summary"
        return _FakeLock(), None

    async def fail_vision_turn(**_kwargs):
        raise AssertionError("stale phase should not run vision turn")

    monkeypatch.setattr(dgr, "_acquire_session_lock", fake_acquire)
    monkeypatch.setattr(dgr, "_run_drawing_guess_vision_turn", fail_vision_turn)

    result = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-stale-phase",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used",
    }))

    assert result["ok"] is True
    assert result["handled"] is False
    assert result["reason"] == "not_ai_guessing"
    assert result["state"]["phase"] == "summary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_guess_fallback_does_not_force_success_by_attempt_count(monkeypatch):
    async def no_text_context_guess(**_kwargs):
        return None

    monkeypatch.setattr(dgr, "_generate_text_context_guess", no_text_context_guess)

    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-3",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-3"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"

    first = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-3",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-logged",
    }))
    assert first["ok"] is True
    assert first["correct"] is False
    assert first["attempt"] == 1
    assert first["can_retry"] is True
    assert first["state"]["phase"] == "ai_guess_feedback"

    second = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-3",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-logged",
        "user_hint": "maybe it is food",
    }))
    assert second["ok"] is True
    assert second["attempt"] == 2
    assert second["max_attempts"] == 3
    assert second["correct"] is False
    assert second["can_retry"] is True
    assert second["state"]["phase"] == "ai_guess_feedback"

    third = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-3",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-logged",
        "user_hint": "maybe it is food",
    }))
    assert third["ok"] is True
    assert third["attempt"] == 3
    assert third["max_attempts"] == 3
    assert third["correct"] is False
    assert third["can_retry"] is False
    assert third["state"]["phase"] == "summary"
    assert third["state"]["scores"]["neko"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_time_expired_user_drawing_settles_after_first_missed_ai_guess(monkeypatch):
    async def wrong_vision_guess(**_kwargs):
        return {
            "word": dgr._WORD_BY_ID["apple"],
            "confidence": 0.6,
            "message": "I am going with apple.",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_generate_vision_guess", wrong_vision_guess)

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "ai_guess_attempt"
        details = kwargs["details"]
        assert details["guess_label"] == "apple"
        assert details["allow_answer_reveal"] is False
        assert details["guess_feedback_pending"] is True
        assert "answer_label" not in details
        return "I am going with apple.", "persona_model"

    async def fake_summary_evaluation(**kwargs):
        assert kwargs["correct"] is False
        assert kwargs["answer"].id == "banana"
        assert kwargs["guessed_word"].id == "apple"
        return "This drawing kept its little secret pretty well.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", fake_summary_evaluation)

    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-time-expired-miss",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-time-expired-miss"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"

    result = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-time-expired-miss",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-logged",
        "settle_on_miss": True,
    }))

    assert result["ok"] is True
    assert result["correct"] is False
    assert result["attempt"] == 1
    assert result["can_retry"] is False
    assert result["answer"]["id"] == "banana"
    assert result["state"]["phase"] == "summary"
    assert result["state"]["scores"]["neko"] == 0
    assert result["message"] == "I am going with apple."
    assert result["evaluation"] == "This drawing kept its little secret pretty well."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_settles_ai_guessing_round(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-guessing-timeout",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-ai-guessing-timeout"]
    session["phase"] = "ai_guessing"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "ai_guess_final_miss"
        assert kwargs["details"]["answer_label"] == "banana"
        assert kwargs["details"]["attempt"] == 1
        return "I ran out of guessing time.", "persona_model"

    async def fake_summary_evaluation(**kwargs):
        assert kwargs["correct"] is False
        assert kwargs["answer"].id == "banana"
        assert kwargs["guessed_word"] is None
        assert kwargs["attempts"] == 1
        return "The answer stayed hidden this time.", "persona_model"

    async def fake_memory_summary(**kwargs):
        assert kwargs["correct"] is False
        assert kwargs["answer"].id == "banana"
        assert kwargs["guessed_word"] is None
        assert kwargs["attempts"] == 1
        return {"ok": True, "stored": False}

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", fake_summary_evaluation)
    monkeypatch.setattr(dgr, "_maybe_write_drawing_guess_memory_summary", fake_memory_summary)

    result = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-guessing-timeout",
        "i18n_language": "en",
    }))

    assert result["ok"] is True
    assert result["phase"] == "summary"
    assert result["kind"] == "ai_guess_timeout"
    assert result["answer"]["id"] == "banana"
    assert result["memory"] == {"ok": True, "stored": False}
    assert result["state"]["phase"] == "summary"
    assert session["phase"] == "summary"
    assert session["game_chat_history"][-1]["kind"] == "vision_guess"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_voice_transcript_reuses_drawing_guess_input_logic(monkeypatch):
    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "user_guess_correct"
        details = kwargs["details"]
        assert details["answer_label"] == "cat"
        assert details["guess_label"] == "cat"
        assert details["allow_answer_reveal"] is True
        assert details["judgement"] == {
            "actor": "user",
            "guess_label": "cat",
            "is_correct": True,
            "answer_revealed": True,
        }
        return "Correct, nicely done.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)

    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-external-voice",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-external-voice"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "cat"

    result = await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-external-voice",
        "cat",
        route_state={"last_state": {"phase": "user_guessing", "i18n_language": "en"}},
        request_id="voice-1",
    )

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is True
    assert result["state"]["phase"] == "word_picking"
    assert result["message"] == "Correct, nicely done."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_voice_canvas_context_only_used_in_visible_canvas_phases(monkeypatch):
    captured: list[dict] = []

    async def fake_handle(data):
        captured.append(dict(data))
        return {"ok": True}

    monkeypatch.setattr(dgr, "_handle_drawing_guess_input_payload", fake_handle)

    await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-canvas-policy",
        "hello",
        route_state={
            "last_state": {"phase": "word_picking", "i18n_language": "en"},
            "last_canvas_image_data_url": "data:image/png;base64,abc",
        },
        source="external_text_route",
        kind="user-text",
    )
    assert "image_data_url" not in captured[-1]
    assert captured[-1]["source"] == "external_text_route"
    assert captured[-1]["input_kind"] == "user-text"

    await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-canvas-policy",
        "try again",
        route_state={
            "last_state": {"phase": "ai_guess_feedback", "i18n_language": "en"},
            "last_canvas_image_data_url": "data:image/png;base64,abc",
        },
    )
    assert captured[-1]["image_data_url"] == "data:image/png;base64,abc"
