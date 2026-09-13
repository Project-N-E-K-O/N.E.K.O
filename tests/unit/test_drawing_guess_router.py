import asyncio
import json
from pathlib import Path

import pytest

from config.prompts import prompts_drawing_guess as drawing_guess_prompts
from config.prompts.prompts_sys import VISION_WATERMARK
from main_routers.game_router import drawing_guess as dgr
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
    begin = drawing_guess_prompts.DRAWING_GUESS_CONTEXT_BEGIN
    end = drawing_guess_prompts.DRAWING_GUESS_CONTEXT_END
    assert raw.startswith(begin)
    assert raw.endswith(end)
    body = raw.removeprefix(begin).removesuffix(end)
    return json.loads(body.strip())


def _put_sdk_drawing_route(
    session_id: str,
    generation: str,
    *,
    memory_enabled: bool = False,
) -> dict:
    state = {
        "game_type": "drawing_guess",
        "game_route_active": True,
        "lanlan_name": "YUI",
        "session_id": session_id,
        "_sdk_route_instance_id": generation,
        "game_memory_enabled": memory_enabled,
        "last_state": {},
    }
    _game_route_states[_route_state_key("YUI", "drawing_guess")] = state
    return state


def _sample_drawing_plan(*, accent: str = "#f4cf45") -> dict:
    return {
        "version": 1,
        "width": 800,
        "height": 600,
        "background": "#fffdfa",
        "elements": [
            {
                "type": "ellipse",
                "cx": 400,
                "cy": 310,
                "rx": 190,
                "ry": 125,
                "fill": accent,
                "stroke": "#2f3b45",
                "stroke_width": 10,
            },
            {
                "type": "polyline",
                "points": [[270, 310], [345, 365], [455, 365], [530, 310]],
                "fill": "none",
                "stroke": "#2f3b45",
                "stroke_width": 8,
                "line_cap": "round",
                "line_join": "round",
            },
        ],
    }


def _png_data_url(width: int, height: int) -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _install_fake_character_llm(monkeypatch, output: str) -> None:
    class _FakeCharacterLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def ainvoke(self, _messages):
            return type("_Result", (), {"content": output})()

    async def fake_create_chat_llm_async(*_args, **_kwargs):
        return _FakeCharacterLLM()

    import utils.llm_client as llm_client

    monkeypatch.setattr(llm_client, "create_chat_llm_async", fake_create_chat_llm_async)
    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A playful companion.",
        "character_profile_prompt": "",
        "model": "test-model",
        "base_url": "https://model.example.test/v1",
        "api_key": "test-key",
    })


async def _begin_pending_plan_review(
    monkeypatch,
    *,
    session_id: str,
    generation: str | None = None,
    round_token: int = 1,
) -> tuple[dict, dict, dict]:
    if generation:
        _put_sdk_drawing_route(session_id, generation)
    identity = {
        "lanlan_name": "YUI",
        "session_id": session_id,
        "client_round_token": round_token,
    }
    if generation:
        identity["sdk_route_instance_id"] = generation
    started = await dgr.drawing_guess_round_start(_FakeRequest(identity))
    assert started["ok"] is True
    session = dgr._drawing_guess_sessions[f"YUI:{session_id}"]
    session["ai_word_id"] = "banana"
    drawing, reason = dgr._validated_drawing_from_plan(
        _sample_drawing_plan(),
        word=dgr._WORD_BY_ID["banana"],
        source="model_plan",
        sanitizer={"attempt": 1},
    )
    assert reason == "ok" and drawing is not None

    async def fake_generate(*_args, **_kwargs):
        return drawing

    async def fake_persona_line(**_kwargs):
        return "Try to guess my drawing.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_model_drawing", fake_generate)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    draw_result = await dgr.drawing_guess_ai_draw(_FakeRequest(identity))
    assert draw_result["ok"] is True
    assert draw_result["review_pending"] is True
    assert draw_result["drawing"]["review_pending"] is True
    return identity, session, draw_result


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
def test_drawing_guess_system_prompts_are_owned_by_prompt_module():
    router_source = Path(dgr.__file__).read_text(encoding="utf-8")
    prompt_source = Path(drawing_guess_prompts.__file__).read_text(encoding="utf-8")
    markers = (
        "You are drawing as the current character for a companion mini-game.",
        "You are a strict visual recognizability reviewer for a drawing-guess mini-game.",
        "The plan must use version 1, width 800, height 600",
        "Temporary mini-game premise:",
        "You classify one user message inside a companion drawing-guess game.",
        "Temporary mini-game task:",
        "======以上为开启上下文输入======",
    )
    for marker in markers:
        assert marker not in router_source
        assert marker in prompt_source


@pytest.mark.unit
def test_drawing_guess_review_prompt_keeps_free_endpoint_watermark():
    review_prompt = drawing_guess_prompts.build_drawing_guess_drawing_review_system_prompt()

    assert review_prompt.startswith(VISION_WATERMARK)


@pytest.mark.unit
def test_drawing_guess_round_timers_are_five_minutes():
    assert dgr.ROUND_GUESS_SECONDS == 5 * 60
    assert dgr.ROUND_DRAW_SECONDS == 5 * 60
    assert dgr.ROUND_AI_GUESS_SECONDS == 5 * 60
    assert dgr.VISION_GUESS_TIMEOUT_SECONDS == float(5 * 60)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_image_is_bounded_by_model_width_and_output_bytes():
    import base64
    from io import BytesIO

    from PIL import Image
    from utils.screenshot_utils import MODEL_IMAGE_MAX_WIDTH

    prepared = await dgr._prepare_vision_image_data_url(_png_data_url(4000, 400))

    assert prepared is not None
    assert len(prepared) <= dgr.VISION_GUESS_MAX_DATA_URL_CHARS
    raw = base64.b64decode(prepared.split(",", 1)[1], validate=True)
    with Image.open(BytesIO(raw)) as image:
        assert image.width == MODEL_IMAGE_MAX_WIDTH
        assert image.height <= 720


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_image_rejects_excessive_pixels_before_full_decode(monkeypatch):
    from utils import screenshot_utils

    monkeypatch.setattr(
        screenshot_utils,
        "_probe_image_profile",
        lambda _encoded: ("PNG", (dgr.VISION_GUESS_MAX_INPUT_PIXELS + 1, 1)),
    )

    def fail_full_decode(_image_bytes):
        raise AssertionError("pixel limit must run before full image decode")

    monkeypatch.setattr(screenshot_utils, "_validate_image_data", fail_full_decode)

    assert await dgr._prepare_vision_image_data_url(_png_data_url(1, 1)) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_image_rejects_oversized_compressed_output(monkeypatch):
    from utils import screenshot_utils

    oversized_raw_length = (dgr.VISION_GUESS_MAX_DATA_URL_CHARS * 3 // 4) + 256
    monkeypatch.setattr(
        screenshot_utils,
        "compress_screenshot",
        lambda *_args, **_kwargs: b"x" * oversized_raw_length,
    )

    assert await dgr._prepare_vision_image_data_url(_png_data_url(1, 1)) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("locale", "memory_fragment", "evaluation_fragment"),
    (
        ("en", "drawing guess round", "drawing"),
        ("ja", "お絵描き当てゲーム", "この絵"),
        ("ko", "그림 맞히기", "이 그림"),
        ("zh-CN", "画的是", "这张画"),
        ("zh-TW", "畫的是", "這張畫"),
        ("ru", "рисование", "рисунок"),
        ("pt", "desenho", "desenho"),
        ("es", "dibujar", "dibujo"),
    ),
)
def test_memory_and_evaluation_fallbacks_are_localized(
    locale,
    memory_fragment,
    evaluation_fragment,
):
    summary = dgr._build_drawing_guess_memory_summary(
        session={"ai_word_id": "train", "user_score": 1, "ai_score": 0},
        locale=locale,
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )

    assert memory_fragment in summary
    assert evaluation_fragment in dgr._summary_evaluation_fallback(locale, correct=False)
    assert len(summary) <= dgr.MEMORY_SUMMARY_MAX_CHARS


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
            "character_private_answer_label": "train",
            "generate_hint_from_answer": True,
            "do_not_derive_hint_from_wrong_guess": True,
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
    assert "ground every clue" in system_prompt
    assert "never derive the next clue" in system_prompt
    assert "Character persona excerpt" not in system_prompt
    assert payload["task"] == "free_in_character_game_reply"
    assert payload["premise"].startswith("The user's latest guess is not the answer")
    assert payload["public_details"]["guess_label"] == "backpack"
    assert payload["public_details"]["character_private_answer_label"] == "train"
    assert payload["public_details"]["generate_hint_from_answer"] is True
    assert payload["public_details"]["do_not_derive_hint_from_wrong_guess"] is True
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
def test_model_output_guard_matches_every_language_alias_and_negated_mentions():
    train = dgr._WORD_BY_ID["train"]

    for alias in dgr._word_aliases(train):
        assert dgr._model_output_mentions_word(f"Nope, not 「{alias}」.", train), alias

    assert dgr._model_output_mentions_word("It is definitely not a train.", train)
    assert dgr._model_output_mentions_word("答案不是火车哦。", train)


@pytest.mark.unit
def test_model_output_guard_keeps_short_alias_token_boundaries():
    car = dgr._WORD_BY_ID["car"]
    sun = dgr._WORD_BY_ID["sun"]

    assert dgr._model_output_mentions_word("看起来像车子，但我不确定。", car)
    assert dgr._model_output_mentions_word("答案不是车子。", car)
    assert not dgr._model_output_mentions_word("That scarlet curve is suspicious.", car)
    assert not dgr._model_output_mentions_word("Solo una pista pequeña.", sun)
    assert not dgr._model_output_mentions_word("我猜火车。", car)
    assert not dgr._model_output_mentions_word("我猜火車。", car)


@pytest.mark.unit
@pytest.mark.parametrize(("word_id", "alias"), [
    ("cat", "貓咪"),
    ("fish", "魚兒"),
    ("bird", "鳥兒"),
    ("cup", "馬克杯"),
    ("clock", "鬧鐘"),
    ("bus", "公共汽車"),
    ("train", "動車"),
    ("train", "电车"),
    ("door", "門口"),
    ("cake", "糕點"),
    ("pants", "牛仔褲"),
    ("lamp", "小燈"),
    ("sock", "短襪"),
])
def test_hidden_answer_guard_covers_common_simplified_traditional_compounds(word_id, alias):
    word = dgr._WORD_BY_ID[word_id]

    assert dgr._matches_word(alias, word)
    assert dgr._model_output_mentions_word(f"答案不是{alias}。", word)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["user_guess_wrong", "hint_request"])
async def test_persona_game_line_discards_hidden_answer_for_non_reveal_events(monkeypatch, event):
    _install_fake_character_llm(
        monkeypatch,
        '{"line":"No, it is not a train, but keep guessing."}',
    )
    session = {
        "session_id": "dg-answer-guard-game-line",
        "phase": "user_guessing",
        "ai_word_id": "train",
        "game_chat_history": [],
    }

    line, source = await dgr._generate_persona_game_line(
        session=session,
        locale="en",
        lanlan_name="YUI",
        event=event,
        fallback="Safe local fallback.",
        details={
            "character_private_answer_label": "train",
            "allow_answer_reveal": False,
        },
    )

    assert line == "Safe local fallback."
    assert source == "fallback"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persona_chat_discards_hidden_answer_alias(monkeypatch):
    _install_fake_character_llm(monkeypatch, '{"line":"Maybe the answer is 火车."}')
    session = {
        "session_id": "dg-answer-guard-chat",
        "phase": "user_guessing",
        "ai_word_id": "train",
        "game_chat_history": [],
    }

    line = await dgr._generate_persona_chat_line(
        session=session,
        locale="zh-CN",
        lanlan_name="YUI",
        user_text="tell me something else",
        event="guessing_chat",
    )

    assert line is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persona_game_line_allows_authorized_reveal_and_correct_visual_guess(monkeypatch):
    _install_fake_character_llm(monkeypatch, '{"line":"I think it is a train."}')
    guessing_session = {
        "session_id": "dg-answer-guard-visual-correct",
        "phase": "ai_guessing",
        "user_word_id": "train",
        "game_chat_history": [],
    }

    visual_line, visual_source = await dgr._generate_persona_game_line(
        session=guessing_session,
        locale="en",
        lanlan_name="YUI",
        event="ai_guess_attempt",
        fallback="Safe local fallback.",
        details={
            "guess_label": "train",
            "speak_as_visual_guess": True,
            "allow_answer_reveal": False,
        },
    )
    reveal_line, reveal_source = await dgr._generate_persona_game_line(
        session={
            "session_id": "dg-answer-guard-timeout",
            "phase": "user_guessing",
            "ai_word_id": "train",
            "game_chat_history": [],
        },
        locale="en",
        lanlan_name="YUI",
        event="user_guess_timeout",
        fallback="Safe local fallback.",
        details={"answer_label": "train", "allow_answer_reveal": True},
    )

    assert (visual_line, visual_source) == ("I think it is a train.", "persona_model")
    assert (reveal_line, reveal_source) == ("I think it is a train.", "persona_model")


@pytest.mark.unit
def test_guess_feedback_chat_knows_latest_guess_was_rejected():
    system_prompt, payload_raw = dgr._build_drawing_guess_chat_prompts(
        session={
            "phase": "ai_guess_feedback",
            "user_word_id": "banana",
            "last_ai_guess_word_id": "cup",
            "last_ai_guess_correct": False,
            "last_ai_guess_attempt": 1,
            "game_chat_history": [],
        },
        locale="zh-CN",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Playful companion.",
        user_text="是吃饭用的",
        event="guess_feedback_chat",
        character_profile_prompt="",
    )
    payload = json.loads(payload_raw)

    assert payload["event_roles"]["character_role"] == "guesser"
    assert payload["event_roles"]["user_role"] == "drawer"
    assert payload["public_details"]["character_is_guessing_user_drawing"] is True
    assert payload["public_details"]["last_character_guess_label"] == dgr._word_label(
        dgr._WORD_BY_ID["cup"], "zh-CN"
    )
    assert payload["public_details"]["last_character_guess_was_correct"] is False
    assert payload["public_details"]["last_character_guess_attempt"] == 1
    assert payload["public_details"]["backend_judgement_is_authoritative"] is True
    assert payload["public_details"]["must_not_defend_rejected_guess_as_answer"] is True
    assert "banana" not in payload_raw
    assert "香蕉" not in payload_raw
    assert "backend has already judged your latest guess wrong" in payload["premise"]
    assert "never insist" in system_prompt
    assert "may naturally make a new candidate guess" in system_prompt


@pytest.mark.unit
def test_ai_retry_hint_recognizes_chinese_usage_clue():
    assert dgr._is_ai_retry_hint("是吃饭用的") is True


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
    assert payload["rules"]["description_without_answer_word_is_not_a_guess_in_user_guessing"] is True
    assert payload["rules"]["feedback_description_of_drawn_object_is_hint"] is False


@pytest.mark.unit
def test_feedback_intent_prompt_treats_standalone_object_description_as_hint():
    system_prompt, payload_raw = dgr._build_game_input_intent_prompts(
        session={"phase": "ai_guess_feedback", "game_chat_history": []},
        locale="zh-CN",
        lanlan_name="YUI",
        master_name="Master",
        lanlan_prompt="Playful companion.",
        user_text="会吃骨头的",
        phase="ai_guess_feedback",
    )
    payload = json.loads(payload_raw)

    assert "'会吃骨头的' is a hint" in system_prompt
    assert payload["rules"]["description_without_answer_word_is_not_a_guess_in_user_guessing"] is False
    assert payload["rules"]["feedback_description_of_drawn_object_is_hint"] is True


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
def test_drawing_plan_sanitizer_and_serializer_accept_bounded_geometry():
    plan, reason = dgr._sanitize_drawing_plan(_sample_drawing_plan())

    assert reason == "ok"
    assert plan is not None
    assert plan["version"] == 1
    assert plan["width"] == 800
    assert plan["height"] == 600
    assert len(plan["elements"]) == 2
    assert plan["elements"][0]["line_cap"] == "round"
    assert plan["elements"][1]["points"][0] == [270, 310]

    svg = dgr._drawing_plan_to_svg(plan)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600"')
    assert '<rect width="800" height="600" fill="#fffdfa"/>' in svg
    assert '<ellipse cx="400" cy="310" rx="190" ry="125"' in svg
    assert 'points="270,310 345,365 455,365 530,310"' in svg
    assert "<text" not in svg.lower()
    assert "<script" not in svg.lower()
    assert "url(" not in svg.lower()


@pytest.mark.unit
@pytest.mark.parametrize(
    "wrap",
    (
        lambda payload: f"Here is the requested plan:\n{payload}\nDone.",
        lambda payload: f"Result follows.\n```json\n{payload}\n```\nNo explanation needed.",
        lambda payload: f"Discard this malformed example {{not json}}.\n{payload}",
    ),
)
def test_drawing_plan_parser_extracts_complete_json_from_model_prose(wrap):
    expected = _sample_drawing_plan()
    payload = json.dumps({"plan": expected})

    parsed = dgr._parse_model_drawing_plan_payload(wrap(payload))

    assert parsed == expected
    drawing, reason = dgr._validated_drawing_from_plan(
        parsed,
        word=dgr._WORD_BY_ID["banana"],
        source="model_plan",
        sanitizer={"attempt": 1},
    )
    assert reason == "ok"
    assert drawing is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "wrap",
    (
        lambda payload: payload,
        lambda payload: f"```json\n{payload}\n```",
        lambda payload: f"Here is the replacement plan:\n{payload}",
    ),
)
def test_drawing_plan_parser_accepts_complete_direct_plan_payload(wrap):
    expected = _sample_drawing_plan()

    assert dgr._parse_model_drawing_plan_payload(wrap(json.dumps(expected))) == expected


@pytest.mark.unit
def test_drawing_plan_parser_rejects_unrelated_or_incomplete_objects():
    incomplete_plan = _sample_drawing_plan()
    incomplete_plan.pop("background")

    assert dgr._parse_model_drawing_plan_payload(json.dumps({"svg": "<svg/>"})) is None
    assert dgr._parse_model_drawing_plan_payload(json.dumps(incomplete_plan)) is None


@pytest.mark.unit
def test_drawing_plan_parser_repairs_one_missing_outer_plan_wrapper_brace():
    expected = _sample_drawing_plan()
    payload = json.dumps({"plan": expected})[:-1]

    assert dgr._parse_model_drawing_plan_payload(payload) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    (
        json.dumps({"plan": _sample_drawing_plan()})[:-12],
        json.dumps(_sample_drawing_plan())[:-1],
        json.dumps({"plan": _sample_drawing_plan(), "metadata": {}})[:-1],
    ),
)
def test_drawing_plan_parser_still_rejects_other_truncated_or_ambiguous_json(payload):
    assert dgr._parse_model_drawing_plan_payload(payload) is None


@pytest.mark.unit
def test_repaired_plan_wrapper_still_passes_through_sanitizer():
    unsafe = _sample_drawing_plan()
    unsafe["elements"][0]["text"] = "banana"
    payload = json.dumps({"plan": unsafe})[:-1]

    parsed = dgr._parse_model_drawing_plan_payload(payload)
    drawing, reason = dgr._validated_drawing_from_plan(
        parsed,
        word=dgr._WORD_BY_ID["banana"],
        source="model_plan_revision",
        sanitizer={"attempt": 1, "revision": 1},
    )

    assert drawing is None
    assert reason == "drawing_plan_extra_element_fields:0"


@pytest.mark.unit
def test_drawing_plan_accepts_complex_curves_and_more_than_old_element_limit():
    raw_plan = _sample_drawing_plan()
    raw_plan["elements"] = [
        {
            "type": "path",
            "d": "M 150 320 C 210 90 590 90 650 320 Q 400 540 150 320 Z",
            "fill": "#f4cf45",
            "stroke": "#2f3b45",
            "stroke_width": 8,
        },
        *[
            {
                "type": "circle",
                "cx": 200 + index % 20 * 20,
                "cy": 200 + index // 20 * 20,
                "r": 4,
                "fill": "#ffffff",
                "stroke": "none",
                "stroke_width": 1,
            }
            for index in range(80)
        ],
    ]

    plan, reason = dgr._sanitize_drawing_plan(raw_plan)

    assert reason == "ok"
    assert plan is not None
    assert len(plan["elements"]) == 81
    assert plan["elements"][0]["d"] == "M 150 320 C 210 90 590 90 650 320 Q 400 540 150 320 Z"
    assert '<path d="M 150 320 C 210 90 590 90 650 320 Q 400 540 150 320 Z"' in dgr._drawing_plan_to_svg(plan)


@pytest.mark.unit
def test_drawing_plan_sanitizer_accepts_declared_transparent_paint():
    raw_plan = _sample_drawing_plan()
    raw_plan["elements"][0]["fill"] = "transparent"

    plan, reason = dgr._sanitize_drawing_plan(raw_plan)

    assert reason == "ok"
    assert plan is not None
    assert plan["elements"][0]["fill"] == "transparent"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    (
        (
            lambda plan: plan["elements"][0].update({"text": "banana"}),
            "drawing_plan_extra_element_fields:0",
        ),
        (
            lambda plan: plan["elements"][0].update({"fill": "url(https://example.test/a)"}),
            "drawing_plan_invalid_color:elements[0].fill",
        ),
        (
            lambda plan: plan["elements"][0].update({"cx": 790}),
            "drawing_plan_number_out_of_range:elements[0].cx",
        ),
        (
            lambda plan: plan["elements"][1].update({"points": [[20, 20]] * 257}),
            "drawing_plan_invalid_points:1",
        ),
        (
            lambda plan: plan["elements"].__setitem__(0, {
                "type": "path", "d": "M 20 20 L 40 40<script>",
                "fill": "none", "stroke": "#000", "stroke_width": 4,
            }),
            "drawing_plan_invalid_path:elements[0].d",
        ),
        (
            lambda plan: plan["elements"][0].update({"stroke_width": float("nan")}),
            "drawing_plan_number_out_of_range:elements[0].stroke_width",
        ),
    ),
)
def test_drawing_plan_sanitizer_rejects_unbounded_or_semantic_payloads(mutate, expected_reason):
    raw_plan = _sample_drawing_plan()
    mutate(raw_plan)

    plan, reason = dgr._sanitize_drawing_plan(raw_plan)

    assert plan is None
    assert reason == expected_reason


@pytest.mark.unit
def test_validated_drawing_plan_keeps_plan_and_safe_svg_compatibility():
    drawing, reason = dgr._validated_drawing_from_plan(
        _sample_drawing_plan(),
        word=dgr._WORD_BY_ID["banana"],
        source="model_plan",
        sanitizer={"attempt": 1},
    )

    assert reason == "ok"
    assert drawing is not None
    assert drawing["source"] == "model_plan"
    assert drawing["sanitizer"] == {"ok": True, "attempt": 1}
    assert drawing["plan"]["elements"][0]["type"] == "ellipse"
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in drawing["svg"]
    assert "<text" not in drawing["svg"].lower()


@pytest.mark.unit
def test_drawing_plan_prompt_requests_richer_art_without_old_complexity_cap():
    prompt = drawing_guess_prompts.build_drawing_guess_plan_system_prompt(
        lanlan_name="Lanlan", master_name="Player", lanlan_prompt="cute painter",
    )

    assert "do not deliberately simplify it into a minimal icon" in prompt
    assert "layered shapes, smooth curves, secondary objects, scenery" in prompt
    assert "path geometry" in prompt
    assert "no more than 70 elements" not in prompt
    assert '"elements":[...]' not in prompt
    assert '"elements":[{"type":"circle"' in prompt
    assert any(
        "never use ellipses or placeholder values" in rule
        for rule in drawing_guess_prompts.DRAWING_GUESS_PLAN_RETRY_RULES
    )


@pytest.mark.unit
def test_drawing_plan_revision_prompt_explicitly_requests_wrapped_complete_plan():
    _, user_prompt = dgr._build_drawing_guess_plan_revision_prompts(
        word=dgr._WORD_BY_ID["banana"],
        locale="en",
        lanlan_name="Lanlan",
        master_name="Player",
        lanlan_prompt="cute painter",
        original_plan=_sample_drawing_plan(),
        review={"guess_id": "apple", "confidence": 0.6, "issues": ["shape is ambiguous"]},
    )
    payload = json.loads(user_prompt)

    assert "top-level plan field" in payload["revision_rules"][0]
    assert "bare plan object" in payload["revision_rules"][0]


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
    assert dgr._matches_word("车子", dgr._WORD_BY_ID["car"])
    assert not dgr._matches_word("热狗", dgr._WORD_BY_ID["dog"])
    assert not dgr._matches_word("是火车吗", dgr._WORD_BY_ID["car"])
    assert not dgr._matches_word("火车子", dgr._WORD_BY_ID["car"])
    assert not dgr._matches_word("月球", dgr._WORD_BY_ID["ball"])


@pytest.mark.unit
@pytest.mark.parametrize("suffix", ["啦", "哦", "呗", "唄", "哟", "喲"])
def test_cjk_sentence_endings_accept_correct_guesses(suffix):
    assert dgr._matches_word(f"是猫{suffix}", dgr._WORD_BY_ID["cat"])
    # 扩展句末助词不能放松普通复合词的前后边界。
    assert not dgr._matches_word(f"是火车{suffix}", dgr._WORD_BY_ID["car"])


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
@pytest.mark.parametrize("text", [
    "\u4e0d\u662f\u732b\u5427\uff1f",
    "\u4e0d\u662f\u4e00\u53ea\u732b\u5427\uff1f",
    "\u8fd9\u4e0d\u50cf\u662f\u732b\u5427\uff1f",
    "\u9019\u4e0d\u50cf\u662f\u4e00\u96bb\u8c93\u5427\uff1f",
    "not cat?",
    "is not cat",
    "isn't cat",
    "I think it's not a cat?",
    "it's not a cat?",
    "I don't think it's a cat?",
    "I do not believe this is a cat?",
    "She doesn't think it's a cat?",
    "I didn't feel that is a cat?",
    "He did not believe it is a cat?",
    "\u6211\u4e0d\u89c9\u5f97\u662f\u4e00\u53ea\u732b\u5427\uff1f",
    "我不觉得那是猫吧？",
    "我不覺得那個是貓吧？",
    "我不觉得那个东西是猫吧？",
    "我不覺得這個東西是貓吧？",
    "\u044d\u0442\u043e \u043d\u0435 \u043a\u043e\u0442?",
    "n\u00e3o \u00e9 um gato?",
    "Será que não é um gato?",
    "no es un gato?",
    "\u306d\u3053\u3058\u3083\u306a\u3044?",
    "\uace0\uc591\uc774\uac00 \uc544\ub2c8\uc57c?",
    "\uace0\uc591\uc774\ub294 \uc544\ub2c8야?",
    "\ucc45\uc740 \uc544\ub2c8야?",
    "\uace0\uc591\uc774\ub294\uc544\ub2c8\uc57c?",
    "\ucc45\uc740\uc544\ub2c8\uc57c?",
    "고양이 아니에요?",
    "고양이가 아니에요?",
    "고양이는아니에요?",
    "고양이가 아니라 개인데?",
    "고양이가아니라개인데?",
    "고양이가 아닌 것 같아",
    "고양이가아닌것같아",
    "고양이는 아닌 것 같아",
    "고양이도 아닌 것 같아",
    "그건 고양이도 아니야?",
    "그건 고양이도아니야?",
    "고양이도 아니고 개도 아니야?",
    "고양이도아니고개도아니야?",
    "고양이지 않아?",
    "고양이지않아?",
    "고양이지는 않아?",
    "고양이지는않아?",
    "고양이지도 않아?",
    "고양이지도않아?",
    "고양이지 않는다?",
    "고양이지는 않는다?",
    "고양이지는않는다?",
    "고양이지도 않는다?",
    "고양이지도않는다?",
    "고양이지 않습니다.",
    "고양이지 않은데?",
    "not the cat?",
])
def test_user_guess_extraction_rejects_negated_aliases(text):
    assert dgr._extract_user_guess_word(text) is None


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "고양이 아닌가?",
    "고양이 아닌가요?",
    "고양이가 아닌가?",
    "고양이는아닌가요?",
])
def test_user_guess_extraction_accepts_korean_affirmative_questions(text):
    assert dgr._extract_user_guess_word(text).id == "cat"


@pytest.mark.unit
def test_user_guess_extraction_can_match_later_non_negated_alias():
    assert dgr._extract_user_guess_word("not dog, I guess cat").id == "cat"


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
async def test_round_start_ignores_debug_phase_override():
    result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-debug-word-pick",
        "i18n_language": "zh-CN",
        "debug_start_phase": "word_picking",
    }))

    assert result["ok"] is True
    assert result["state"]["phase"] == "ai_drawing"
    assert result["state"]["user_draw_answer"] is None
    assert "phase" not in result
    assert "user_draw_options" not in result
    assert "draw_seconds" not in result
    payload_text = str(result)
    assert "aliases" not in payload_text
    assert "forbidden" not in payload_text
    assert "ai_word" not in payload_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_round_start_rechecks_route_generation_after_lifecycle_lock():
    route_a = _put_sdk_drawing_route("dg-start-lock", "route-A")
    route_lock = dgr._get_route_lock("YUI", "drawing_guess")
    await route_lock.acquire()
    try:
        start_task = asyncio.create_task(dgr.drawing_guess_round_start(_FakeRequest({
            "lanlan_name": "YUI",
            "session_id": "dg-start-lock",
            "sdk_route_instance_id": "route-A",
            "client_round_token": "round-A",
        })))
        await asyncio.sleep(0)
        assert start_task.done() is False

        route_a["game_route_active"] = False
        _put_sdk_drawing_route("dg-start-lock", "route-B")
    finally:
        route_lock.release()

    result = await start_task

    assert result == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert "YUI:dg-start-lock" not in dgr._drawing_guess_sessions


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
async def test_legacy_round_start_normalizes_memory_consent():
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
async def test_sdk_round_start_uses_only_host_memory_policy():
    disabled_route = _put_sdk_drawing_route("dg-sdk-memory-disabled", "route-memory-disabled")
    disabled_result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-disabled",
        "sdk_route_instance_id": "route-memory-disabled",
        "game_memory_enabled": False,
        "memory_consent": "summary",
    }))

    enabled_route = _put_sdk_drawing_route(
        "dg-sdk-memory-enabled",
        "route-memory-enabled",
        memory_enabled=True,
    )
    enabled_result = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-enabled",
        "sdk_route_instance_id": "route-memory-enabled",
        "game_memory_enabled": True,
        "memory_consent": "none",
    }))

    assert disabled_result["ok"] is True
    assert enabled_result["ok"] is True
    assert dgr._drawing_guess_sessions["YUI:dg-sdk-memory-disabled"]["memory_consent"] == "none"
    assert dgr._drawing_guess_sessions["YUI:dg-sdk-memory-enabled"]["memory_consent"] == "summary"
    assert disabled_route["game_memory_archive_owner"] == "feature"
    assert enabled_route["game_memory_archive_owner"] == "feature"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_round_start_rejects_memory_policy_drift_from_active_route():
    _put_sdk_drawing_route("dg-sdk-memory-payload-only", "route-payload-only")
    payload_only = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-payload-only",
        "sdk_route_instance_id": "route-payload-only",
        "game_memory_enabled": True,
    }))

    _put_sdk_drawing_route(
        "dg-sdk-memory-route-only",
        "route-route-only",
        memory_enabled=True,
    )
    route_only = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-route-only",
        "sdk_route_instance_id": "route-route-only",
        "game_memory_enabled": False,
    }))

    assert payload_only["ok"] is True
    assert route_only["ok"] is True
    assert dgr._drawing_guess_sessions["YUI:dg-sdk-memory-payload-only"]["memory_consent"] == "none"
    assert dgr._drawing_guess_sessions["YUI:dg-sdk-memory-route-only"]["memory_consent"] == "none"


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
async def test_sdk_memory_summary_rechecks_disabled_active_route_before_post(monkeypatch):
    _put_sdk_drawing_route("dg-sdk-memory-revoked", "route-memory-revoked")
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-revoked",
        "sdk_route_instance_id": "route-memory-revoked",
        "game_memory_enabled": False,
    }))
    assert started["ok"] is True
    session = dgr._drawing_guess_sessions["YUI:dg-sdk-memory-revoked"]
    session["memory_consent"] = "summary"

    async def fail_post(*_args, **_kwargs):
        raise AssertionError("disabled SDK route must not write persistent memory")

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", fail_post)
    result = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session,
        locale="en",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )

    assert result == {"status": "skipped", "reason": "memory_consent_none"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_memory_summary_writes_with_matching_enabled_active_route(monkeypatch):
    _put_sdk_drawing_route(
        "dg-sdk-memory-authorized",
        "route-memory-authorized",
        memory_enabled=True,
    )
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-authorized",
        "sdk_route_instance_id": "route-memory-authorized",
        "game_memory_enabled": True,
    }))
    assert started["ok"] is True
    session = dgr._drawing_guess_sessions["YUI:dg-sdk-memory-authorized"]
    captured = []

    async def fake_post(lanlan_name, summary):
        captured.append((lanlan_name, summary))
        return {"status": "written", "source": "memory_server_cache", "count": 1}

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", fake_post)
    result = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session,
        locale="en",
        lanlan_name="YUI",
        correct=True,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["dog"],
        attempts=1,
    )

    assert result == {"status": "written", "source": "memory_server_cache", "count": 1}
    assert len(captured) == 1


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
            "timeout_kind": "user_guessing",
        }))
    finally:
        lock.release()

    assert result["ok"] is False
    assert result["reason"] == "session_busy"
    assert result["state"]["phase"] == "user_guessing"
    assert session["phase"] == "user_guessing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guess_timeout_retry_returns_cached_transition(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-timeout-retry",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-timeout-retry"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"

    async def fake_persona_line(**kwargs):
        return "Time is up.", "fallback"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    payload = {
        "lanlan_name": "YUI",
        "session_id": "dg-timeout-retry",
        "i18n_language": "en",
        "timeout_kind": "user_guessing",
    }
    first = await dgr.drawing_guess_timeout(_FakeRequest(payload))
    retried = await dgr.drawing_guess_timeout(_FakeRequest(payload))

    assert first["ok"] is True
    assert first["phase"] == "word_picking"
    assert retried == first
    assert session["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "text", "expected_kind", "expected_correct"),
    (
        ("dg-correct-transition-retry", "apple", "guess", True),
        ("dg-give-up-transition-retry", "i give up", "give_up", False),
    ),
)
async def test_user_guess_timeout_recovers_completed_input_transition(
    monkeypatch,
    session_id,
    text,
    expected_kind,
    expected_correct,
):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions[f"YUI:{session_id}"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]

    async def fake_persona_line(**_kwargs):
        return "The guessing turn is complete.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    identity = {
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "en",
    }
    completed = await dgr.drawing_guess_input(_FakeRequest({
        **identity,
        "text": text,
    }))

    assert completed["ok"] is True
    assert completed["kind"] == expected_kind
    assert completed["correct"] is expected_correct
    assert completed["state"]["phase"] == "word_picking"
    assert session["phase"] == "word_picking"
    assert session["user_guess_transition_result"] == completed

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        **identity,
        "timeout_kind": "user_guessing",
    }))

    assert recovered == completed
    assert recovered["answer"]["id"] == "apple"
    assert [option["id"] for option in recovered["user_draw_options"]] == [
        "cat",
        "dog",
        "fish",
    ]
    assert session["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "text", "expected_kind", "expected_correct", "fallback_key"),
    (
        ("dg-cancel-correct-transition", "apple", "guess", True, "user_correct"),
        ("dg-cancel-give-up-transition", "i give up", "give_up", False, "guess_timeout"),
    ),
)
async def test_user_guess_cancelled_during_persona_can_recover_transition(
    monkeypatch,
    session_id,
    text,
    expected_kind,
    expected_correct,
    fallback_key,
):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions[f"YUI:{session_id}"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]
    persona_started = asyncio.Event()

    async def blocking_persona_line(**_kwargs):
        persona_started.set()
        await asyncio.Future()

    monkeypatch.setattr(dgr, "_generate_persona_game_line", blocking_persona_line)
    task = asyncio.create_task(dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "en",
        "text": text,
    })))
    await asyncio.wait_for(persona_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session["phase"] == "word_picking"
    assert session["user_guess_transition_result"]["kind"] == expected_kind
    assert session["user_guess_transition_result"]["message"] == dgr._localized_line("en", fallback_key)
    assert dgr._get_session_lock(session).locked() is False
    assert session["game_chat_history"][-1]["text"] == dgr._localized_line("en", fallback_key)

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "zh-CN",
        "timeout_kind": "user_guessing",
    }))

    assert recovered["ok"] is True
    assert recovered["kind"] == expected_kind
    assert recovered["correct"] is expected_correct
    assert recovered["message"] == dgr._localized_line("zh-CN", fallback_key)
    assert recovered["message_source"] == "fallback"
    assert recovered["answer"] == dgr._word_public(dgr._WORD_BY_ID["apple"], "zh-CN")
    assert [option["id"] for option in recovered["user_draw_options"]] == ["cat", "dog", "fish"]
    assert recovered["state"]["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guess_timeout_cancelled_during_persona_can_recover_transition(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-cancel-user-timeout-transition",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-cancel-user-timeout-transition"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]
    persona_started = asyncio.Event()

    async def blocking_persona_line(**_kwargs):
        persona_started.set()
        await asyncio.Future()

    monkeypatch.setattr(dgr, "_generate_persona_game_line", blocking_persona_line)
    task = asyncio.create_task(dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-cancel-user-timeout-transition",
        "i18n_language": "en",
        "timeout_kind": "user_guessing",
    })))
    await asyncio.wait_for(persona_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session["phase"] == "word_picking"
    assert session["user_guess_transition_result"]["kind"] == "user_guess_timeout"
    assert dgr._get_session_lock(session).locked() is False

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-cancel-user-timeout-transition",
        "i18n_language": "zh-CN",
        "timeout_kind": "user_guessing",
    }))

    assert recovered["kind"] == "user_guess_timeout"
    assert recovered["message"] == dgr._localized_line("zh-CN", "guess_timeout")
    assert recovered["answer"] == dgr._word_public(dgr._WORD_BY_ID["apple"], "zh-CN")
    assert recovered["state"]["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_guess_timeout_builds_defensive_recovery_without_cache():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-user-transition-fallback",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-user-transition-fallback"]
    session["phase"] = "word_picking"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]
    session["user_score"] = 1

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-user-transition-fallback",
        "i18n_language": "zh-CN",
        "timeout_kind": "user_guessing",
    }))

    assert recovered["ok"] is True
    assert recovered["kind"] == "guess"
    assert recovered["correct"] is True
    assert recovered["message"] == dgr._localized_line("zh-CN", "user_correct")
    assert recovered["answer"] == dgr._word_public(dgr._WORD_BY_ID["apple"], "zh-CN")
    assert recovered["state"]["phase"] == "word_picking"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_user_guess_timeout_cannot_advance_user_drawing(monkeypatch):
    session_id = "dg-stale-user-guess-timeout"
    identity = {
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": "en",
    }
    await dgr.drawing_guess_round_start(_FakeRequest(identity))
    session = dgr._drawing_guess_sessions[f"YUI:{session_id}"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "apple"
    session["user_word_options"] = ["cat", "dog", "fish"]

    async def fake_persona_line(**_kwargs):
        return "Correct.", "persona_model"

    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    completed = await dgr.drawing_guess_input(_FakeRequest({
        **identity,
        "text": "apple",
    }))
    assert completed["correct"] is True
    assert session["phase"] == "word_picking"

    chosen = await dgr.drawing_guess_choose_word(_FakeRequest({
        **identity,
        "word_id": "cat",
    }))
    assert chosen["ok"] is True
    assert session["phase"] == "user_drawing"

    stale_timeout = await dgr.drawing_guess_timeout(_FakeRequest({
        **identity,
        "timeout_kind": "user_guessing",
    }))

    assert stale_timeout["ok"] is False
    assert stale_timeout["reason"] == "stale_timeout_phase"
    assert stale_timeout["state"]["phase"] == "user_drawing"
    assert session["phase"] == "user_drawing"
    assert session["user_word_id"] == "cat"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_choose_word_rejects_concurrent_session_request():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-choice-busy",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-choice-busy"]
    session["phase"] = "word_picking"
    options = dgr._user_word_options_public(session, "en")
    lock = dgr._get_session_lock(session)
    await lock.acquire()
    try:
        result = await dgr.drawing_guess_choose_word(_FakeRequest({
            "lanlan_name": "YUI",
            "session_id": "dg-choice-busy",
            "i18n_language": "en",
            "word_id": options[0]["id"],
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

    async def fake_call_drawing_guess_plan_model(**kwargs):
        prompts.append(kwargs["user_prompt"])
        if len(prompts) == 1:
            return '<svg viewBox="0 0 240 180"><script>alert(1)</script><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>'
        return '<svg viewBox="0 0 240 180"><circle cx="90" cy="90" r="35" fill="#f4cf45"/></svg>'

    monkeypatch.setattr(dgr, "_call_drawing_guess_plan_model", fake_call_drawing_guess_plan_model)

    drawing = await dgr._generate_model_drawing(dgr._WORD_BY_ID["banana"], "en", "YUI")

    assert drawing is not None
    assert drawing["source"] == "model_svg"
    assert drawing["sanitizer"] == {"ok": True, "attempt": 2}
    assert len(prompts) == 2
    assert "previous_rejection_reason" not in prompts[0]
    assert "disallowed_svg_tag:script" in prompts[1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_model_drawing_retries_invalid_plan_then_returns_plan_and_svg(monkeypatch):
    from main_routers import game_router

    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A playful companion who draws simple cute shapes.",
        "model": "test-model",
        "base_url": "",
        "api_key": "",
        "provider_type": "openai",
    })
    prompts = []

    async def fake_call_drawing_guess_plan_model(**kwargs):
        prompts.append(kwargs["user_prompt"])
        plan = _sample_drawing_plan()
        if len(prompts) == 1:
            plan["elements"][0]["text"] = "banana"
        return json.dumps({"plan": plan})

    monkeypatch.setattr(
        dgr,
        "_call_drawing_guess_plan_model",
        fake_call_drawing_guess_plan_model,
    )

    drawing = await dgr._generate_model_drawing(
        dgr._WORD_BY_ID["banana"],
        "en",
        "YUI",
    )

    assert drawing is not None
    assert drawing["source"] == "model_plan"
    assert drawing["sanitizer"] == {"ok": True, "attempt": 2}
    assert drawing["plan"]["width"] == 800
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in drawing["svg"]
    assert len(prompts) == 2
    assert "previous_rejection_reason" not in prompts[0]
    assert "drawing_plan_extra_element_fields:0" in prompts[1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_model_drawing_revision_retries_and_accepts_direct_plan(monkeypatch):
    monkeypatch.setattr(game_router, "_get_character_info", lambda lanlan_name: {
        "lanlan_name": lanlan_name,
        "master_name": "player",
        "lanlan_prompt": "A playful companion who draws detailed shapes.",
        "model": "test-model",
        "base_url": "",
        "api_key": "",
        "provider_type": "openai",
    })
    calls = []

    async def fake_call_drawing_guess_plan_model(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "I changed the confusing shapes as requested."
        return json.dumps(_sample_drawing_plan(accent="#f0b429"))

    monkeypatch.setattr(
        dgr,
        "_call_drawing_guess_plan_model",
        fake_call_drawing_guess_plan_model,
    )

    drawing = await dgr._generate_model_drawing_revision(
        word=dgr._WORD_BY_ID["banana"],
        locale="en",
        lanlan_name="YUI",
        original_plan=_sample_drawing_plan(),
        review={"guess_id": "apple", "confidence": 0.6, "issues": ["shape is ambiguous"]},
    )

    assert drawing is not None
    assert drawing["source"] == "model_plan_revision"
    assert drawing["sanitizer"] == {"ok": True, "attempt": 2, "revision": 1}
    assert drawing["plan"]["elements"][0]["fill"] == "#f0b429"
    assert len(calls) == 2
    assert {call["call_type"] for call in calls} == {"drawing_guess_drawing_revision"}
    retry_payload = json.loads(calls[1]["user_prompt"])
    assert retry_payload["task"] == "retry_revise_the_drawing_plan_after_visual_review"
    assert retry_payload["attempt"] == 2
    assert retry_payload["previous_rejection_reason"] == "model_payload_unparseable"
    assert retry_payload["original_plan"] == _sample_drawing_plan()


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
async def test_ai_drawing_review_accepts_and_caches_first_visual_result(monkeypatch):
    identity, session, draw_result = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-plan-review-accepted",
        round_token=17,
    )
    review_calls = 0

    async def fake_review(**kwargs):
        nonlocal review_calls
        review_calls += 1
        assert kwargs["session"] is session
        assert kwargs["image_data_url"] == "data:image/jpeg;base64,YWJj"
        return {
            "available": True,
            "accepted": True,
            "guess_id": "banana",
            "confidence": 0.91,
            "issues": [],
            "source": "vision_model",
        }

    async def fail_revision(**_kwargs):
        raise AssertionError("an accepted drawing must not request a revision")

    monkeypatch.setattr(dgr, "_review_ai_drawing", fake_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fail_revision)
    request_payload = {**identity, "image_data_url": "data:image/jpeg;base64,YWJj"}

    first = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))
    repeated = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))

    assert first == repeated
    assert review_calls == 1
    assert first["ok"] is True
    assert first["kind"] == "ai_drawing_review"
    assert first["phase"] == "user_guessing"
    assert first["review_pending"] is False
    assert first["review"] == {
        "status": "accepted",
        "accepted": True,
        "corrected": False,
        "unavailable": False,
        "reason": "recognized",
        "confidence": 0.91,
    }
    assert first["drawing"]["plan"] == draw_result["drawing"]["plan"]
    assert first["drawing"]["review_pending"] is False
    assert session[dgr._AI_DRAWING_REVIEW_KEY]["pending"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_drawing_review_applies_at_most_one_corrected_plan(monkeypatch):
    identity, session, _draw_result = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-plan-review-revised",
        round_token=18,
    )
    review_calls = 0
    revision_calls = 0

    async def fake_review(**_kwargs):
        nonlocal review_calls
        review_calls += 1
        return {
            "available": True,
            "accepted": False,
            "guess_id": "apple",
            "confidence": 0.78,
            "issues": ["The silhouette looks round rather than curved."],
            "source": "vision_model",
        }

    async def fake_revision(**kwargs):
        nonlocal revision_calls
        revision_calls += 1
        assert kwargs["word"].id == "banana"
        expected_original, expected_reason = dgr._sanitize_drawing_plan(_sample_drawing_plan())
        assert expected_reason == "ok"
        assert kwargs["original_plan"] == expected_original
        assert kwargs["review"]["guess_id"] == "apple"
        drawing, reason = dgr._validated_drawing_from_plan(
            _sample_drawing_plan(accent="#f0b429"),
            word=dgr._WORD_BY_ID["banana"],
            source="model_plan_revision",
            sanitizer={"attempt": 1, "revision": 1},
        )
        assert reason == "ok" and drawing is not None
        return drawing

    monkeypatch.setattr(dgr, "_review_ai_drawing", fake_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fake_revision)
    request_payload = {**identity, "image_data_url": "data:image/jpeg;base64,YWJj"}

    first = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))
    repeated = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))

    assert first == repeated
    assert review_calls == 1
    assert revision_calls == 1
    assert first["review"] == {
        "status": "revised",
        "accepted": False,
        "corrected": True,
        "unavailable": False,
        "reason": "not_recognized",
        "confidence": 0.78,
    }
    assert first["drawing"]["source"] == "model_plan_revision"
    assert first["drawing"]["plan"]["elements"][0]["fill"] == "#f0b429"
    assert first["drawing"]["review_pending"] is False
    assert session[dgr._AI_DRAWING_REVIEW_KEY]["revision_count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_drawing_review_continues_while_user_chats(monkeypatch):
    identity, session, _draw_result = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-plan-review-background-chat",
        round_token=181,
    )
    review_started = asyncio.Event()
    release_review = asyncio.Event()

    async def delayed_review(**_kwargs):
        review_started.set()
        await release_review.wait()
        return {
            "available": True,
            "accepted": False,
            "guess_id": "apple",
            "confidence": 0.78,
            "issues": ["The silhouette looks round rather than curved."],
            "source": "vision_model",
        }

    async def fake_revision(**_kwargs):
        drawing, reason = dgr._validated_drawing_from_plan(
            _sample_drawing_plan(accent="#f0b429"),
            word=dgr._WORD_BY_ID["banana"],
            source="model_plan_revision",
            sanitizer={"attempt": 1, "revision": 1},
        )
        assert reason == "ok" and drawing is not None
        return drawing

    async def fake_chat_intent(**_kwargs):
        return {"intent": "chat", "guess_text": "", "confidence": 0.95}

    monkeypatch.setattr(dgr, "_review_ai_drawing", delayed_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fake_revision)
    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_chat_intent)
    review_task = asyncio.create_task(dgr.drawing_guess_ai_draw_review(_FakeRequest({
        **identity,
        "image_data_url": "data:image/jpeg;base64,YWJj",
    })))
    await review_started.wait()

    chat_result = await asyncio.wait_for(dgr.drawing_guess_input(_FakeRequest({
        **identity,
        "text": "That looks cute.",
    })), timeout=1.0)

    assert chat_result["ok"] is True
    assert chat_result["kind"] == "chat"
    assert session[dgr._AI_DRAWING_REVIEW_KEY]["pending"] is True
    assert not review_task.done()

    release_review.set()
    review_result = await review_task

    assert review_result["review"]["status"] == "revised"
    assert review_result["drawing"]["plan"]["elements"][0]["fill"] == "#f0b429"
    assert session["phase"] == "user_guessing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_drawing_review_does_not_replace_drawing_after_correct_guess(monkeypatch):
    identity, session, draw_result = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-plan-review-background-correct",
        round_token=182,
    )
    review_started = asyncio.Event()
    release_review = asyncio.Event()

    async def delayed_review(**_kwargs):
        review_started.set()
        await release_review.wait()
        return {
            "available": True,
            "accepted": False,
            "guess_id": "apple",
            "confidence": 0.78,
            "issues": ["wrong silhouette"],
            "source": "vision_model",
        }

    async def fail_revision(**_kwargs):
        raise AssertionError("a completed guessing phase must not request a revision")

    monkeypatch.setattr(dgr, "_review_ai_drawing", delayed_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fail_revision)
    review_task = asyncio.create_task(dgr.drawing_guess_ai_draw_review(_FakeRequest({
        **identity,
        "image_data_url": "data:image/jpeg;base64,YWJj",
    })))
    await review_started.wait()

    guess_result = await asyncio.wait_for(dgr.drawing_guess_input(_FakeRequest({
        **identity,
        "text": "banana",
    })), timeout=1.0)

    assert guess_result["ok"] is True
    assert guess_result["kind"] == "guess"
    assert guess_result["correct"] is True
    assert session["phase"] == "word_picking"

    release_review.set()
    review_result = await review_task

    assert review_result["review"]["status"] == "draft_adopted"
    assert review_result["drawing"]["plan"] == draw_result["drawing"]["plan"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_drawing_review_unavailable_adopts_and_caches_original_plan(monkeypatch):
    identity, _session, draw_result = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-plan-review-unavailable",
        round_token=19,
    )
    review_calls = 0

    async def fake_review(**_kwargs):
        nonlocal review_calls
        review_calls += 1
        return {
            "available": False,
            "accepted": False,
            "reason": "no_vision_model",
            "source": "unavailable",
        }

    async def fail_revision(**_kwargs):
        raise AssertionError("an unavailable reviewer must not trigger correction")

    monkeypatch.setattr(dgr, "_review_ai_drawing", fake_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fail_revision)
    request_payload = {**identity, "image_data_url": "data:image/jpeg;base64,YWJj"}

    first = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))
    repeated = await dgr.drawing_guess_ai_draw_review(_FakeRequest(request_payload))

    assert first == repeated
    assert review_calls == 1
    assert first["review"] == {
        "status": "unavailable",
        "accepted": False,
        "corrected": False,
        "unavailable": True,
        "reason": "no_vision_model",
    }
    assert first["drawing"]["plan"] == draw_result["drawing"]["plan"]
    assert first["drawing"]["review_pending"] is False


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
    assert result["kind"] == "give_up"
    assert result["correct"] is False
    assert result["message_source"] == "persona_model"
    assert result["message"] == "自己问的喔，答案是香蕉。"
    assert result["answer"]["id"] == "banana"
    assert result["state"]["phase"] == "word_picking"
    assert len(result["user_draw_options"]) == dgr.USER_DRAW_OPTION_COUNT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("mi respuesta es gato", False),
        ("minha resposta é gato", False),
        ("мой ответ — кот", False),
        ("¿Cuál es la respuesta?", True),
        ("Qual é a resposta?", True),
        ("Я сдаюсь", True),
        ("정답 알려", True),
    ),
)
def test_localized_direct_answer_phrase_detection(text, expected):
    assert dgr._is_direct_answer_request(text) is expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "locale", "text"),
    (
        ("dg-local-guess-es", "es", "mi respuesta es gato"),
        ("dg-local-guess-pt", "pt", "minha resposta é gato"),
        ("dg-local-guess-ru", "ru", "мой ответ — кот"),
    ),
)
async def test_localized_answer_statement_is_a_guess_without_intent_model(
    monkeypatch,
    session_id,
    locale,
    text,
):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": locale,
    }))
    session = dgr._drawing_guess_sessions[f"YUI:{session_id}"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "cat"

    async def unavailable_intent(**_kwargs):
        return None

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "user_guess_correct"
        return "Correct.", "persona_model"

    monkeypatch.setattr(dgr, "_classify_game_input_intent", unavailable_intent)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": session_id,
        "i18n_language": locale,
        "text": text,
    }))

    assert result["ok"] is True
    assert result["kind"] == "guess"
    assert result["correct"] is True
    assert result["answer"]["id"] == "cat"
    assert result["state"]["phase"] == "word_picking"


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
        assert details["character_private_answer_label"] == "chair"
        assert details["generate_hint_from_answer"] is True
        assert details["do_not_derive_hint_from_wrong_guess"] is True
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
async def test_ai_guess_feedback_persona_chat_guess_is_formally_judged(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-chat-guess",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-chat-guess"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "dog"
    session["ai_guess_attempts"] = 1

    async def fake_intent(**kwargs):
        assert kwargs["user_text"] == "会吃骨头的"
        return {"intent": "chat", "guess_text": "", "confidence": 0.9}

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "guess_feedback_chat"
        return "喵呜~那是小狗吗？"

    async def fail_vision_guess(**_kwargs):
        raise AssertionError("a guess already present in the chat line must be judged directly")

    async def fake_game_line(**kwargs):
        assert kwargs["event"] == "ai_guess_attempt"
        assert kwargs["details"]["guess_label"] == "狗"
        return "喵呜~那是小狗吗？", "persona_model"

    async def no_evaluation(**_kwargs):
        return None, None

    async def no_memory(**_kwargs):
        return None

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_vision_guess", fail_vision_guess)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_game_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", no_evaluation)
    monkeypatch.setattr(dgr, "_maybe_write_drawing_guess_memory_summary", no_memory)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-chat-guess",
        "i18n_language": "zh-CN",
        "text": "会吃骨头的",
        "image_data_url": "data:image/png;base64,not-used",
    }))

    assert result["kind"] == "ai_guess"
    assert result["guess"]["id"] == "dog"
    assert result["correct"] is True
    assert result["source"] == "persona_chat_guess"
    assert result["state"]["phase"] == "summary"
    assert session["ai_guess_attempts"] == 2


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
async def test_summary_chat_only_does_not_mutate_active_round(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-summary-chat-only",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-summary-chat-only"]
    session["phase"] = "user_guessing"
    session["ai_word_id"] = "banana"

    async def fake_persona_line(**kwargs):
        assert kwargs["event"] == "summary_chat"
        return "We can talk about the finished game without changing the round."

    monkeypatch.setattr(dgr, "_generate_persona_chat_line", fake_persona_line)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-summary-chat-only",
        "i18n_language": "en",
        "text": "is it banana?",
        "summary_chat_only": True,
    }))

    assert result["ok"] is True
    assert result["kind"] == "chat"
    assert result["message"] == "We can talk about the finished game without changing the round."
    assert session["phase"] == "user_guessing"
    assert session["user_score"] == 0

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
async def test_ai_guess_feedback_intent_classifier_retries_at_confidence_threshold(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-soft-hint",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-feedback-soft-hint"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "dog"
    session["ai_guess_attempts"] = 1

    async def fake_intent(**kwargs):
        assert kwargs["phase"] == "ai_guess_feedback"
        assert kwargs["user_text"] == "会吃骨头的"
        return {
            "intent": "hint",
            "guess_text": "",
            "confidence": dgr.AI_GUESS_FEEDBACK_HINT_CONFIDENCE,
        }

    async def fake_vision_guess(**kwargs):
        assert kwargs["user_hint"] == "会吃骨头的"
        return {
            "word": dgr._WORD_BY_ID["dog"],
            "confidence": 0.8,
            "message": "那是小狗吗？",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_classify_game_input_intent", fake_intent)
    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)

    result = await dgr.drawing_guess_input(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-feedback-soft-hint",
        "i18n_language": "en",
        "text": "会吃骨头的",
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
    assert session["last_ai_guess_word_id"] == "banana"
    assert session["last_ai_guess_correct"] is True
    assert session["last_ai_guess_attempt"] == 1

    history_length = len(session["game_chat_history"])
    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-model",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }))
    assert recovered == result
    assert len(session["game_chat_history"]) == history_length

    translated = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-model",
        "i18n_language": "zh-CN",
        "timeout_kind": "ai_guessing",
    }))
    assert translated["kind"] == "ai_guess"
    assert translated["message"] == dgr._localized_line("zh-CN", "ai_correct")
    assert translated["message_source"] == "fallback"
    assert translated["evaluation"] == dgr._summary_evaluation_fallback("zh-CN", correct=True)
    assert translated["evaluation_source"] == "fallback"
    assert translated["guess"] == dgr._word_public(dgr._WORD_BY_ID["banana"], "zh-CN")
    assert translated["answer"] == dgr._word_public(dgr._WORD_BY_ID["banana"], "zh-CN")
    assert translated["state"]["user_draw_answer"] == dgr._word_public(
        dgr._WORD_BY_ID["banana"],
        "zh-CN",
    )
    assert len(session["game_chat_history"]) == history_length


@pytest.mark.unit
@pytest.mark.asyncio
async def test_completed_vision_guess_cancelled_during_evaluation_keeps_recoverable_summary(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-evaluation-cancelled",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-vision-evaluation-cancelled"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"
    evaluation_started = asyncio.Event()

    async def correct_vision_guess(**_kwargs):
        return {
            "word": dgr._WORD_BY_ID["banana"],
            "confidence": 0.9,
            "message": "That looks like a banana.",
            "source": "vision_model",
        }

    async def fake_persona_line(**_kwargs):
        return "My guess is banana.", "persona_model"

    async def blocking_summary_evaluation(**_kwargs):
        evaluation_started.set()
        await asyncio.Future()

    monkeypatch.setattr(dgr, "_generate_vision_guess", correct_vision_guess)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)
    monkeypatch.setattr(dgr, "_generate_summary_evaluation", blocking_summary_evaluation)
    task = asyncio.create_task(dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-evaluation-cancelled",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
    })))
    await asyncio.wait_for(evaluation_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session["phase"] == "summary"
    assert session["ai_guess_attempts"] == 1
    assert dgr._get_session_lock(session).locked() is False
    assert session["game_chat_history"][-1]["text"] == "My guess is banana."
    cached = session["ai_guess_transition_result"]
    assert cached["kind"] == "ai_guess"
    assert cached["guess"]["id"] == "banana"
    assert cached["evaluation"] == dgr._summary_evaluation_fallback("en", correct=True)

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-vision-evaluation-cancelled",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }))
    assert recovered == cached


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
async def test_vision_guess_shared_model_budget_cancels_text_and_uses_static_fallback(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-shared-model-budget",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-shared-model-budget"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"
    text_guess_started = asyncio.Event()
    text_guess_cancelled = False

    async def unavailable_vision_guess(**_kwargs):
        return None

    async def blocking_text_context_guess(**_kwargs):
        nonlocal text_guess_cancelled
        text_guess_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            text_guess_cancelled = True
            raise

    async def fake_persona_line(**_kwargs):
        return "I will make a fallback guess.", "persona_model"

    monkeypatch.setattr(dgr, "AI_GUESS_MODEL_BUDGET_SECONDS", 0.01)
    monkeypatch.setattr(dgr, "_generate_vision_guess", unavailable_vision_guess)
    monkeypatch.setattr(dgr, "_generate_text_context_guess", blocking_text_context_guess)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fake_persona_line)

    result = await asyncio.wait_for(dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-shared-model-budget",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
    })), timeout=1.0)

    assert text_guess_started.is_set()
    assert text_guess_cancelled is True
    assert result["ok"] is True
    assert result["source"] == "fallback_static"
    assert result["correct"] is False
    assert result["state"]["phase"] == "ai_guess_feedback"
    assert dgr._get_session_lock(session).locked() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_guess_external_cancellation_propagates_and_releases_session_lock(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-external-cancel",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-external-cancel"]
    session["phase"] = "user_drawing"
    session["user_word_id"] = "banana"
    vision_guess_started = asyncio.Event()
    vision_guess_cancelled = False
    text_guess_called = False

    async def blocking_vision_guess(**_kwargs):
        nonlocal vision_guess_cancelled
        vision_guess_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            vision_guess_cancelled = True
            raise

    async def unexpected_text_context_guess(**_kwargs):
        nonlocal text_guess_called
        text_guess_called = True
        return None

    monkeypatch.setattr(dgr, "AI_GUESS_MODEL_BUDGET_SECONDS", 60.0)
    monkeypatch.setattr(dgr, "_generate_vision_guess", blocking_vision_guess)
    monkeypatch.setattr(dgr, "_generate_text_context_guess", unexpected_text_context_guess)

    task = asyncio.create_task(dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-external-cancel",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
    })))
    await asyncio.wait_for(vision_guess_started.wait(), timeout=1.0)
    lock = dgr._get_session_lock(session)
    assert lock.locked() is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert vision_guess_cancelled is True
    assert text_guess_called is False
    assert lock.locked() is False
    assert session["ai_guess_attempts"] == 0

    async def retry_vision_guess(**_kwargs):
        return {
            "word": dgr._WORD_BY_ID["apple"],
            "confidence": 0.7,
            "message": "Maybe it is an apple.",
            "source": "vision_model",
        }

    monkeypatch.setattr(dgr, "_generate_vision_guess", retry_vision_guess)
    retried = await dgr.drawing_guess_vision_guess(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-external-cancel",
        "i18n_language": "en",
        "image_data_url": "data:image/png;base64,not-used-by-mock",
    }))

    assert retried["ok"] is True
    assert retried["attempt"] == 1
    assert retried["correct"] is False
    assert session["ai_guess_attempts"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_endpoint_uses_image_url_guess(monkeypatch):
    class _FakeConfigManager:
        async def aget_model_api_config(self, model_type):
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
async def test_ai_drawing_review_uses_blind_vision_candidates_and_accepts_match(monkeypatch):
    class _FakeConfigManager:
        async def aget_model_api_config(self, model_type):
            assert model_type == "vision"
            return {
                "model": "test-vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "test-key",
                "provider_type": "openai",
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
                "content": json.dumps({
                    "guess_id": "banana",
                    "confidence": 0.82,
                    "issues": [],
                })
            })()

    async def fake_create_chat_llm_async(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return _FakeVisionLLM()

    monkeypatch.setattr(dgr, "_prepare_vision_image_data_url", fake_prepare_image)

    import utils.config_manager as config_manager
    import utils.llm_client as llm_client

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _FakeConfigManager())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fake_create_chat_llm_async)

    result = await dgr._review_ai_drawing(
        session={
            "session_id": "dg-ai-review",
            "round_id": "round-7",
            "ai_word_id": "banana",
        },
        locale="en",
        lanlan_name="YUI",
        image_data_url="data:image/png;base64,ignored",
    )

    assert result == {
        "available": True,
        "accepted": True,
        "guess_id": "banana",
        "confidence": 0.82,
        "issues": [],
        "source": "vision_model",
    }
    assert calls[0]["kwargs"]["model"] == "test-vision-model"
    assert calls[0]["kwargs"]["provider_type"] == "openai"
    messages = calls[1]["messages"]
    assert messages[0].content.startswith(VISION_WATERMARK)
    assert messages[1].content[0]["image_url"]["url"] == "data:image/jpeg;base64,YWJj"
    review_payload = json.loads(messages[1].content[1]["text"])
    assert review_payload["task"] == "identify_the_single_canvas_drawing_for_quality_review"
    assert len(review_payload["candidates"]) == dgr.VISION_GUESS_MAX_CANDIDATES
    assert "answer_id" not in review_payload
    assert "answer_label" not in review_payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_drawing_review_invalid_image_is_unavailable_without_model_call(monkeypatch):
    async def fake_prepare_image(_value):
        return None

    monkeypatch.setattr(dgr, "_prepare_vision_image_data_url", fake_prepare_image)

    result = await dgr._review_ai_drawing(
        session={"session_id": "dg-ai-review-invalid", "ai_word_id": "banana"},
        locale="en",
        lanlan_name="YUI",
        image_data_url="not-an-image",
    )

    assert result == {
        "available": False,
        "accepted": False,
        "reason": "invalid_image",
        "source": "unavailable",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vision_endpoint_falls_back_when_payload_unparseable(monkeypatch):
    class _FakeConfigManager:
        async def aget_model_api_config(self, model_type):
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

    history_length = len(session["game_chat_history"])
    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-3",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }))
    assert recovered == third
    assert len(session["game_chat_history"]) == history_length


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

    history_length = len(session["game_chat_history"])
    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-time-expired-miss",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }))
    assert recovered == result
    assert len(session["game_chat_history"]) == history_length


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_advances_user_drawing_then_settles_ai_guessing_round(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-guessing-timeout",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-ai-guessing-timeout"]
    session["phase"] = "user_drawing"
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

    timeout_payload = {
        "lanlan_name": "YUI",
        "session_id": "dg-ai-guessing-timeout",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }
    advanced = await dgr.drawing_guess_timeout(_FakeRequest(timeout_payload))

    assert advanced["ok"] is True
    assert advanced["phase"] == "ai_guessing"
    assert advanced["state"]["phase"] == "ai_guessing"
    assert session["phase"] == "ai_guessing"

    result = await dgr.drawing_guess_timeout(_FakeRequest(timeout_payload))

    assert result["ok"] is True
    assert result["phase"] == "summary"
    assert result["kind"] == "ai_guess_timeout"
    assert result["answer"]["id"] == "banana"
    assert result["memory"] == {"ok": True, "stored": False}
    assert result["state"]["phase"] == "summary"
    assert session["phase"] == "summary"
    assert session["game_chat_history"][-1]["kind"] == "vision_guess"

    history_length = len(session["game_chat_history"])
    cached = await dgr.drawing_guess_timeout(_FakeRequest(timeout_payload))

    assert cached == result
    assert len(session["game_chat_history"]) == history_length
    assert session["phase"] == "summary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_timeout_recovers_summary_without_cached_transition():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-summary-recovery",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-ai-summary-recovery"]
    session["phase"] = "summary"
    session["user_word_id"] = "banana"
    session["last_ai_guess_correct"] = False

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-summary-recovery",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    }))

    assert recovered["ok"] is True
    assert recovered["phase"] == "summary"
    assert recovered["kind"] == "ai_guess_recovery"
    assert recovered["answer"]["id"] == "banana"
    assert recovered["evaluation"] == dgr._summary_evaluation_fallback("en", correct=False)
    assert recovered["state"]["phase"] == "summary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_timeout_cancelled_during_persona_can_recover_cached_summary(monkeypatch):
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-timeout-cancelled",
        "i18n_language": "en",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-ai-timeout-cancelled"]
    session["phase"] = "ai_guess_feedback"
    session["user_word_id"] = "banana"
    session["ai_guess_attempts"] = 1
    persona_started = asyncio.Event()

    async def blocking_persona_line(**_kwargs):
        persona_started.set()
        await asyncio.Future()

    monkeypatch.setattr(dgr, "_generate_persona_game_line", blocking_persona_line)
    task = asyncio.create_task(dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-timeout-cancelled",
        "i18n_language": "en",
        "timeout_kind": "ai_guessing",
    })))
    await asyncio.wait_for(persona_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session["phase"] == "summary"
    assert session["ai_guess_transition_result"]["kind"] == "ai_guess_timeout"
    assert dgr._get_session_lock(session).locked() is False
    assert session["game_chat_history"][-1]["text"] == dgr._localized_line("en", "ai_wrong")

    recovered = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-ai-timeout-cancelled",
        "i18n_language": "zh-CN",
        "timeout_kind": "ai_guessing",
    }))

    assert recovered["ok"] is True
    assert recovered["kind"] == "ai_guess_timeout"
    assert recovered["message"] == dgr._localized_line("zh-CN", "ai_wrong")
    assert recovered["evaluation"] == dgr._summary_evaluation_fallback("zh-CN", correct=False)
    assert recovered["answer"] == dgr._word_public(dgr._WORD_BY_ID["banana"], "zh-CN")
    assert recovered["state"]["user_draw_answer"] == dgr._word_public(
        dgr._WORD_BY_ID["banana"],
        "zh-CN",
    )


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_round_start_rejects_missing_and_stale_window_generation():
    _put_sdk_drawing_route("dg-sdk-window", "route-B")

    missing = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-window",
        "client_round_token": "round-missing",
    }))
    stale = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-window",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-stale",
    }))
    current = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-window",
        "sdk_route_instance_id": "route-B",
        "client_round_token": "round-current",
    }))

    assert missing == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert stale == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert current["ok"] is True
    session = dgr._drawing_guess_sessions["YUI:dg-sdk-window"]
    assert session["_sdk_route_instance_id"] == "route-B"
    assert session["client_round_token"] == "round-current"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "extra"),
    (
        ("drawing_guess_ai_draw", {}),
        ("drawing_guess_ai_draw_review", {"image_data_url": "data:image/jpeg;base64,YQ=="}),
        ("drawing_guess_input", {"text": "cat"}),
        ("drawing_guess_choose_word", {"word_id": "cat"}),
        ("drawing_guess_timeout", {}),
        ("drawing_guess_vision_guess", {"image_data_url": "data:image/jpeg;base64,YQ=="}),
    ),
)
@pytest.mark.parametrize("generation", (None, "route-A"))
async def test_sdk_round_endpoints_reject_missing_or_stale_generation(
    handler_name,
    extra,
    generation,
):
    _put_sdk_drawing_route("dg-sdk-endpoints", "route-B")
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-endpoints",
        "sdk_route_instance_id": "route-B",
        "client_round_token": "round-B",
    }))
    assert started["ok"] is True

    payload = {
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-endpoints",
        "client_round_token": "round-B",
        **extra,
    }
    if generation is not None:
        payload["sdk_route_instance_id"] = generation
    result = await getattr(dgr, handler_name)(_FakeRequest(payload))

    assert result == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert dgr._drawing_guess_sessions["YUI:dg-sdk-endpoints"]["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delayed_ai_draw_cannot_land_after_same_session_sdk_supersede(monkeypatch):
    route_a = _put_sdk_drawing_route("dg-sdk-delayed", "route-A")
    started_a = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-delayed",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
    }))
    assert started_a["ok"] is True
    session_a = dgr._drawing_guess_sessions["YUI:dg-sdk-delayed"]

    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_drawing(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {
            "svg": "<svg></svg>",
            "caption": "",
            "source": "model",
            "sanitizer": {"ok": True},
        }

    async def fail_persona_line(**_kwargs):
        raise AssertionError("stale AI draw must stop before the next model side effect")

    monkeypatch.setattr(dgr, "_generate_model_drawing", delayed_drawing)
    monkeypatch.setattr(dgr, "_generate_persona_game_line", fail_persona_line)
    pending = asyncio.create_task(dgr.drawing_guess_ai_draw(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-delayed",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
    })))
    await entered.wait()

    route_a["game_route_active"] = False
    route_b = _put_sdk_drawing_route("dg-sdk-delayed", "route-B")
    started_b = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-delayed",
        "sdk_route_instance_id": "route-B",
        "client_round_token": "round-B",
    }))
    assert started_b["ok"] is True
    session_b = dgr._drawing_guess_sessions["YUI:dg-sdk-delayed"]

    release.set()
    stale_result = await pending

    assert stale_result == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert session_a["phase"] == "ai_drawing"
    assert session_b["phase"] == "ai_drawing"
    assert route_b["last_state"]["client_round_token"] == "round-B"

    dgr._sync_active_route_state(session_a, "en")
    assert route_b["last_state"]["client_round_token"] == "round-B"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delayed_ai_drawing_review_cannot_land_after_sdk_route_supersede(monkeypatch):
    identity_a, session_a, _drawing = await _begin_pending_plan_review(
        monkeypatch,
        session_id="dg-sdk-delayed-review",
        generation="route-A",
        round_token=31,
    )
    route_a = _game_route_states[_route_state_key("YUI", "drawing_guess")]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_review(**_kwargs):
        entered.set()
        await release.wait()
        return {
            "available": True,
            "accepted": False,
            "guess_id": "apple",
            "confidence": 0.8,
            "issues": ["wrong silhouette"],
            "source": "vision_model",
        }

    async def fail_revision(**_kwargs):
        raise AssertionError("a stale review must stop before the correction model call")

    monkeypatch.setattr(dgr, "_review_ai_drawing", delayed_review)
    monkeypatch.setattr(dgr, "_generate_model_drawing_revision", fail_revision)
    pending = asyncio.create_task(dgr.drawing_guess_ai_draw_review(_FakeRequest({
        **identity_a,
        "image_data_url": "data:image/jpeg;base64,YWJj",
    })))
    await entered.wait()

    route_a["game_route_active"] = False
    route_b = _put_sdk_drawing_route("dg-sdk-delayed-review", "route-B")
    started_b = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-delayed-review",
        "sdk_route_instance_id": "route-B",
        "client_round_token": 32,
    }))
    assert started_b["ok"] is True
    session_b = dgr._drawing_guess_sessions["YUI:dg-sdk-delayed-review"]

    release.set()
    stale_result = await pending

    assert stale_result == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert session_a[dgr._AI_DRAWING_REVIEW_KEY]["pending"] is True
    assert session_b["phase"] == "ai_drawing"
    assert dgr._AI_DRAWING_REVIEW_KEY not in session_b
    assert route_b["last_state"]["client_round_token"] == 32


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_transcript_injects_backend_route_generation(monkeypatch):
    route_state = _put_sdk_drawing_route("dg-sdk-external", "route-trusted")
    route_state["memory_consent"] = "summary"
    route_state["last_state"]["memory_consent"] = "summary"
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-external",
        "sdk_route_instance_id": "route-trusted",
        "client_round_token": "round-trusted",
    }))
    assert started["ok"] is True
    captured = {}

    async def capture(data):
        captured.update(data)
        return {"ok": True, "handled": True}

    monkeypatch.setattr(dgr, "_handle_drawing_guess_input_payload", capture)
    result = await dgr.handle_external_drawing_guess_transcript(
        "YUI",
        "dg-sdk-external",
        "voice input",
        route_state=route_state,
        request_id="voice-sdk",
    )

    assert result["ok"] is True
    assert captured["sdk_route_instance_id"] == "route-trusted"
    assert "memory_consent" not in captured


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_identity_is_rechecked_after_waiting_for_round_lock(monkeypatch):
    route_a = _put_sdk_drawing_route("dg-sdk-lock", "route-A")
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-lock",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
    }))
    assert started["ok"] is True
    session_a = dgr._drawing_guess_sessions["YUI:dg-sdk-lock"]
    original_acquire = dgr._acquire_session_lock

    async def acquire_then_supersede(session, locale):
        lock, busy = await original_acquire(session, locale)
        route_a["game_route_active"] = False
        _put_sdk_drawing_route("dg-sdk-lock", "route-B")
        session_b = dict(session_a)
        session_b["_sdk_route_instance_id"] = "route-B"
        session_b["client_round_token"] = "round-B"
        session_b[dgr._SESSION_LOCK_KEY] = asyncio.Lock()
        dgr._drawing_guess_sessions["YUI:dg-sdk-lock"] = session_b
        return lock, busy

    monkeypatch.setattr(dgr, "_acquire_session_lock", acquire_then_supersede)
    result = await dgr.drawing_guess_timeout(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-lock",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
    }))

    assert result == {"ok": False, "reason": "route_instance_id_mismatch"}
    assert session_a["phase"] == "ai_drawing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_superseded_sdk_session_cannot_write_persistent_memory(monkeypatch):
    route_a = _put_sdk_drawing_route("dg-sdk-memory", "route-A", memory_enabled=True)
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
        "game_memory_enabled": True,
        "memory_consent": "summary",
    }))
    assert started["ok"] is True
    session_a = dgr._drawing_guess_sessions["YUI:dg-sdk-memory"]
    route_a["game_route_active"] = False
    _put_sdk_drawing_route("dg-sdk-memory", "route-B")

    async def fail_memory_write(*_args, **_kwargs):
        raise AssertionError("superseded round must not reach persistent memory")

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", fail_memory_write)
    result = await dgr._maybe_write_drawing_guess_memory_summary(
        session=session_a,
        locale="en",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    )

    assert result == {"status": "skipped", "reason": "stale_route_instance"}
    assert "memory_summary_result" not in session_a


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_memory_commit_is_serialized_with_route_supersede(monkeypatch):
    route_a = _put_sdk_drawing_route("dg-sdk-memory-lock", "route-A", memory_enabled=True)
    started = await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-sdk-memory-lock",
        "sdk_route_instance_id": "route-A",
        "client_round_token": "round-A",
        "game_memory_enabled": True,
        "memory_consent": "summary",
    }))
    assert started["ok"] is True
    session_a = dgr._drawing_guess_sessions["YUI:dg-sdk-memory-lock"]
    post_entered = asyncio.Event()
    release_post = asyncio.Event()
    superseded = asyncio.Event()

    async def delayed_post(_lanlan_name, _summary):
        post_entered.set()
        await release_post.wait()
        return {"status": "written", "source": "memory_server_cache", "count": 1}

    monkeypatch.setattr(dgr, "_post_drawing_guess_memory_summary", delayed_post)
    write_task = asyncio.create_task(dgr._maybe_write_drawing_guess_memory_summary(
        session=session_a,
        locale="en",
        lanlan_name="YUI",
        correct=False,
        answer=dgr._WORD_BY_ID["dog"],
        guessed_word=dgr._WORD_BY_ID["cat"],
        attempts=3,
    ))
    await post_entered.wait()

    async def supersede():
        async with dgr._get_route_lock("YUI", "drawing_guess"):
            route_a["game_route_active"] = False
            _put_sdk_drawing_route("dg-sdk-memory-lock", "route-B")
            superseded.set()

    supersede_task = asyncio.create_task(supersede())
    await asyncio.sleep(0)
    assert superseded.is_set() is False

    release_post.set()
    result = await write_task
    await supersede_task

    assert result == {"status": "written", "source": "memory_server_cache", "count": 1}
    assert superseded.is_set() is True
