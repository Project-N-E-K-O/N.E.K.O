from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent


def read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_exit_retention_tts_uses_current_character_voice_with_bounded_text():
    router = read_repo_file("main_routers/characters_router.py")

    assert "@router.post('/exit_retention_tts')" in router
    assert "async def synthesize_exit_retention_tts" in router
    assert "characters.get('当前猫娘'" in router
    assert "get_reserved(" in router
    assert "current_catgirl_payload" in router
    assert "'voice_id'" in router
    assert "await _resolve_exit_retention_voice_id(" in router
    assert "EXIT_RETENTION_TTS_TEXT_MAX_CHARS" in router
    assert "text[:EXIT_RETENTION_TTS_TEXT_MAX_CHARS]" in router
    assert "return await get_voice_preview(" in router
    assert "text=text" in router


def test_exit_retention_tts_falls_back_to_current_tts_route_when_character_voice_is_empty():
    router = read_repo_file("main_routers/characters_router.py")

    assert "async def _resolve_exit_retention_voice_id(" in router
    assert "config_manager.validate_voice_id(character_voice_id)" in router
    assert "free_preset_mismatches_route" in router
    assert "logger.info(\"退出挽留 TTS 跳过当前角色不可用音色" in router
    assert "core_config = await config_manager.aget_core_config()" in router
    assert "realtime_config = config_manager.get_model_api_config('realtime')" in router
    assert "realtime_config.get('api_type'" in router
    assert "core_config.get('TTS_VOICE_ID')" in router
    assert "core_config.get('ENABLE_CUSTOM_API')" in router
    assert "get_stepfun_tts_default_voice" in router
    assert '"Momo"' in router
    assert "CURRENT_CATGIRL_VOICE_MISSING" not in router


def test_exit_retention_tts_applies_sad_reluctant_voice_style_to_supported_providers():
    router = read_repo_file("main_routers/characters_router.py")
    voice_clone = read_repo_file("utils/voice_clone.py")

    assert 'EXIT_RETENTION_TTS_STYLE = "sad_reluctant"' in router
    assert "voice_style=EXIT_RETENTION_TTS_STYLE" in router
    assert "voice_style: str | None = None" in router

    assert "EXIT_RETENTION_TTS_GEMINI_STYLE_INSTRUCTION" in router
    assert "style_instruction=style_instruction" in router
    assert "EXIT_RETENTION_TTS_QWEN_STYLE_INSTRUCTION" in router
    assert "qwen_style_instruction = EXIT_RETENTION_TTS_QWEN_STYLE_INSTRUCTION" in router
    assert "style_instruction=qwen_style_instruction" in router
    assert 'session["instructions"] = style_instruction' in router

    assert "EXIT_RETENTION_TTS_ELEVENLABS_STYLE" in router
    assert '"style": style' in router

    assert "EXIT_RETENTION_TTS_PROVIDER_EMOTION" in router
    assert 'create_data["emotion"] = emotion' in router
    assert 'voice_setting["emotion"] = emotion' in voice_clone
    assert "emotion=provider_emotion" in router


def test_qwen_exit_retention_preview_ignores_non_json_websocket_binary_frames():
    router = read_repo_file("main_routers/characters_router.py")
    qwen_preview = router.split("async def _synthesize_qwen_voice_preview(", 1)[1].split("async def _synthesize_gemini_native_voice_preview(", 1)[0]

    assert qwen_preview.count("if isinstance(raw, bytes):") >= 2
    assert "continue" in qwen_preview
    assert "event = json.loads(raw)" in qwen_preview


def test_voice_preview_only_accepts_explicit_text_for_exit_retention_style():
    router = read_repo_file("main_routers/characters_router.py")

    assert "async def get_voice_preview(" in router
    assert "text: str | None = None" in router
    assert "is_exit_retention_style = str(voice_style or '').strip() == EXIT_RETENTION_TTS_STYLE" in router
    assert "explicit_text = str(text or '').strip() if is_exit_retention_style else ''" in router
    assert "if explicit_text:" in router
    assert "preview_line = explicit_text or _loc(VOICE_PREVIEW_TEXTS, preview_language)" in router


def test_qwen_direct_preview_is_limited_to_exit_retention_style():
    router = read_repo_file("main_routers/characters_router.py")
    voice_preview = router.split("async def get_voice_preview(", 1)[1]

    assert "and is_exit_retention_style" in voice_preview
    assert "realtime_config_for_preview = _config_manager.get_model_api_config('realtime')" in voice_preview
    assert "realtime_config_for_preview.get('api_type')" in voice_preview
    assert "qwen_style_instruction = EXIT_RETENTION_TTS_QWEN_STYLE_INSTRUCTION" in voice_preview


def test_exit_retention_tts_rejects_invalid_or_non_object_json_before_reading_fields():
    router = read_repo_file("main_routers/characters_router.py")
    endpoint = router.split("async def synthesize_exit_retention_tts(request: Request):", 1)[1].split("async def get_voice_preview(", 1)[0]

    assert "data, error_response = await _read_json_object_or_400(request)" in endpoint
    assert "if error_response:" in endpoint
    assert "return error_response" in endpoint
    assert "await request.json()" not in endpoint


if __name__ == "__main__":
    test_exit_retention_tts_uses_current_character_voice_with_bounded_text()
    test_exit_retention_tts_falls_back_to_current_tts_route_when_character_voice_is_empty()
    test_exit_retention_tts_applies_sad_reluctant_voice_style_to_supported_providers()
    test_qwen_exit_retention_preview_ignores_non_json_websocket_binary_frames()
    test_voice_preview_only_accepts_explicit_text_for_exit_retention_style()
    test_qwen_direct_preview_is_limited_to_exit_retention_style()
    test_exit_retention_tts_rejects_invalid_or_non_object_json_before_reading_fields()
