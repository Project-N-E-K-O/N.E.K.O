import pytest

from main_routers import drawing_guess_router as dgr


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_sessions():
    dgr._drawing_guess_sessions.clear()
    yield
    dgr._drawing_guess_sessions.clear()


@pytest.mark.unit
def test_drawing_guess_word_bank_has_40_easy_words_with_all_locales():
    assert len(dgr.WORDS) == 40
    for word in dgr.WORDS:
        assert set(word.labels) == set(dgr.SUPPORTED_LOCALES)
        for locale in dgr.SUPPORTED_LOCALES:
            assert word.labels[locale].strip()
            assert dgr._word_hint(word, locale).strip()


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
async def test_ai_draw_and_user_guess_advance_to_user_drawing_without_candidate_leak():
    await dgr.drawing_guess_round_start(_FakeRequest({
        "lanlan_name": "YUI",
        "session_id": "dg-2",
        "i18n_language": "zh-CN",
    }))
    session = dgr._drawing_guess_sessions["YUI:dg-2"]
    session["ai_word_id"] = "apple"
    session["user_word_id"] = "cat"

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
    assert guess["state"]["phase"] == "user_drawing"
    assert guess["state"]["scores"]["user"] == 1
    assert guess["user_draw_answer"]["id"] == "cat"
    assert "candidates" not in str(guess)


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
    assert result["state"]["phase"] == "user_drawing"


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

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)

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
    assert result["message"] == "That has to be a banana."


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

    monkeypatch.setattr(dgr, "_generate_vision_guess", fake_vision_guess)
    monkeypatch.setattr(dgr, "_generate_text_context_guess", fake_text_context_guess)

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
    assert result["message"] == "Then I will guess banana."


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
async def test_vision_guess_fallback_respects_three_attempt_contract(monkeypatch):
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
    assert second["correct"] is True
    assert second["state"]["phase"] == "summary"
    assert second["state"]["scores"]["neko"] == 1
