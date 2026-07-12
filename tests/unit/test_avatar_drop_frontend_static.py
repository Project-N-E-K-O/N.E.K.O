from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_BUTTONS_PATH = REPO_ROOT / "static" / "app-buttons.js"
APP_AUDIO_CAPTURE_PATH = REPO_ROOT / "static" / "app-audio-capture.js"
APP_WEBSOCKET_PATH = REPO_ROOT / "static" / "app-websocket.js"
CORE_PACKAGE_PATH = REPO_ROOT / "main_logic" / "core"
CROSS_SERVER_PATH = REPO_ROOT / "main_logic" / "cross_server.py"
INDEX_TEMPLATE_PATH = REPO_ROOT / "templates" / "index.html"
INTAKE_PATH = REPO_ROOT / "static" / "avatar" / "avatar-drop-intake.js"
MAIN_SERVER_PATH = REPO_ROOT / "app" / "main_server.py"
OMNI_OFFLINE_PATH = REPO_ROOT / "main_logic" / "omni_offline_client.py"
PARSER_PATH = REPO_ROOT / "static" / "avatar" / "avatar-drop-parser.js"
WEBSOCKET_ROUTER_PATH = REPO_ROOT / "main_routers" / "websocket_router.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_package(path: Path) -> str:
    """Concatenated source of every module in a package directory (the
    ``main_logic.core`` package equivalent of the old single-file read)."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(path.glob("*.py")))


def _js_function_block(source: str, function_name: str) -> str:
    marker = f"function {function_name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing JS function {function_name}")
    brace = source.find("{", start)
    if brace < 0:
        raise AssertionError(f"missing opening brace for JS function {function_name}")

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JS function {function_name}")


@pytest.mark.unit
def test_avatar_drop_parser_declares_supported_formats_and_limits():
    source = _read(PARSER_PATH)

    assert "var MAX_FILES = 6;" in source
    assert "var MAX_TEXT_BYTES = 1024 * 1024;" in source
    assert "var MAX_TEXT_CHARS = 32000;" in source
    assert "var MAX_TOTAL_TEXT_CHARS = 90000;" in source
    assert "var MAX_IMAGE_BYTES = 10 * 1024 * 1024;" in source
    assert "var MAX_DOCUMENT_BYTES = 16 * 1024 * 1024;" in source
    assert "var MAX_IMAGE_PIXELS = 24000000;" in source
    assert "var MAX_IMAGE_SIDE = 1280;" in source
    assert "var MAX_IMAGE_DATA_URL_BYTES = 1200 * 1024;" in source
    assert "function getImageHeaderSize(bytes, kind)" in source
    assert "async function getImageHeaderSizeForFile(file, kind, prefix)" in source
    assert "count = Math.min(maxBytes, count * 2);" in source
    assert "var headerSize = await getImageHeaderSizeForFile(file, kind, prefix);" in source
    assert source.index("var headerSize = await getImageHeaderSizeForFile(file, kind, prefix);") < source.index("var image = await loadImage(file);")

    assert "var DOCUMENT_EXTENSIONS = ['pdf', 'docx', 'xlsx', 'pptx'];" in source
    for extension in ("'txt'", "'md'", "'json'", "'csv'", "'tsx'", "'py'", "'ps1'", "'sql'"):
        assert extension in source
    for kind in ("'jpeg'", "'png'", "'gif'", "'webp'", "'bmp'"):
        assert kind in source

    assert "startsWith(bytes, [0x25, 0x50, 0x44, 0x46])" in source
    assert "mime === 'image/svg+xml' || ext === 'svg'" in source
    assert "macro_document_unsupported" in source
    assert "legacy_office_unsupported" in source
    assert "if (i >= MAX_FILES)" in source
    assert "var overLimitCount = 0;" in source
    assert "overLimitCount + ' more files'" in source
    assert "accepted.length >= MAX_FILES" not in source
    assert "reason: 'too_many_files',\n                count: overLimitCount" in source
    assert "JSON.parse(text)" in source
    assert "garbled_text" in source
    assert "control_chars" in source
    assert "bidi_controls" in source
    assert "function hasKnownTextType(file)" in source
    assert "function sniffStrictUtf8Text(prefix)" in source
    assert "decodeWith('utf-8', prefix, true) !== null" in source
    assert "requireStrictDecode" in source
    assert "return parseTextFile(file, 'text', { requireStrictDecode: strictSniffedText });" in source


@pytest.mark.unit
def test_avatar_drop_intake_hides_bubble_when_drag_leaves_model_hit_area():
    source = _read(INTAKE_PATH)
    chat_surface_target = _js_function_block(source, "isChatSurfaceDropTarget")
    get_event_target = _js_function_block(source, "getEventTarget")
    drag_over = _js_function_block(source, "handleDragOver")
    drop = _js_function_block(source, "handleDrop")
    hide_overlay = _js_function_block(source, "hideOverlay")
    hide_overlay_now = _js_function_block(source, "hideOverlayNow")

    assert "document.getElementById('react-chat-window-shell')" in chat_surface_target
    assert "document.getElementById('text-input-area')" in chat_surface_target
    assert "document.getElementById('textInputBox')" in chat_surface_target
    assert "'.composer-panel'" in chat_surface_target
    assert "'.composer-input-shell'" in chat_surface_target
    assert "'[data-compact-geometry-owner=\"surface\"]'" in chat_surface_target
    assert "isChatSurfaceDropTarget(event)" in get_event_target
    assert get_event_target.index("isChatSurfaceDropTarget(event)") < get_event_target.index("getDropTargetAtPoint")
    assert "if (isChatSurfaceDropTarget(event))" in drag_over
    assert drag_over.index("if (isChatSurfaceDropTarget(event))") < drag_over.index("if (busy)")
    assert "if (isChatSurfaceDropTarget(event))" in drop
    assert drop.index("if (isChatSurfaceDropTarget(event))") < drop.index("if (busy)")
    assert "options.allowRecentTarget === true" in get_event_target
    assert "Date.now() - lastTargetAt < 180" in get_event_target
    assert "if (busy)" in drag_over
    assert "event.dataTransfer.dropEffect = 'none';" in drag_over
    assert "var target = getEventTarget(event);" in drag_over
    assert "getEventTarget(event, { allowRecentTarget: true })" not in drag_over
    assert "hideOverlay(0);" in drag_over
    assert "if (busy)" in drop
    assert "if (!target) return;" in drop
    assert "getEventTarget(event, { allowRecentTarget: true })" in drop
    assert "hideOverlayNow();" in hide_overlay
    assert "window.setTimeout(hideOverlayNow, wait)" in hide_overlay
    assert "window.cancelAnimationFrame(bubbleRaf)" in hide_overlay_now
    assert "if (!accepted.length && !rejected.length)" in drop
    assert "rejected: rejected" in drop
    assert "if (!accepted.length && rejected.length > 0)" in drop


@pytest.mark.unit
def test_avatar_drop_payload_sends_full_prompt_but_records_memory_summary_only():
    source = _read(APP_BUTTONS_PATH)
    build_prompt = _js_function_block(source, "buildAvatarDropPrompt")
    voice_active = _js_function_block(source, "isAvatarDropVoiceSessionActive")
    wait_teardown = _js_function_block(source, "waitForAvatarDropVoiceTeardown")
    prepare_text_mode = _js_function_block(source, "prepareAvatarDropTextMode")
    send_payload = _js_function_block(source, "sendAvatarDropPayload")
    audio_capture_source = _read(APP_AUDIO_CAPTURE_PATH)
    stop_recording = _js_function_block(audio_capture_source, "stopRecording")
    app_websocket_source = _read(APP_WEBSOCKET_PATH)

    assert "<<<TEXT_FILE_" in build_prompt
    assert "String(item.content || '').trim()" in build_prompt
    assert "item.documentType" in build_prompt
    assert "item.type === 'image'" in build_prompt
    assert "item.animated" in build_prompt
    assert "用户刚把以下内容递给你" in build_prompt
    assert "资料" not in build_prompt
    assert "直接承认这些文件现在读不了" in build_prompt
    assert "这些文件也被递给你了，但现在读不了" in build_prompt
    assert "读不了的文件" in build_prompt
    assert "无法读取的文件" not in build_prompt
    assert "item.reason" not in build_prompt

    assert "var rejected = getAvatarDropRejected(payload);" in send_payload
    assert "if (!items.length && !rejected.length) return false;" in send_payload
    assert "var gameRouteBlocksImages = !!(S && S.gameRouteActive);" in send_payload
    assert "reason: 'game_route_image_unsupported'" in send_payload
    assert "var imageDataUrls = gameRouteBlocksImages ? [] : items" in send_payload
    assert "var prompt = buildAvatarDropPrompt({ items: items, rejected: rejected });" in send_payload
    assert "var displayText = formatAvatarDropDisplayText({ items: items, rejected: rejected });" in send_payload
    assert "if (!await prepareAvatarDropTextMode()) return false;" in send_payload
    assert send_payload.index("prepareAvatarDropTextMode()") < send_payload.index("sendTextPayload(prompt")
    assert "displayText: displayText" in send_payload
    assert "memoryText: displayText" in send_payload
    assert "memoryText: prompt" not in send_payload
    assert "extraImageDataUrls: imageDataUrls" in send_payload
    assert "input_type: 'avatar_drop_image',\n                                request_id: requestId" in source
    assert "var messageSource = typeof options.source === 'string' ? options.source.trim() : '';" in source
    assert "extraMessage.source = messageSource" in source
    assert "textMessage.source = messageSource" in source
    assert "forceReactOptimisticMessage: true" in send_payload
    assert "ignoreComposerAttachments: true" in send_payload

    assert "S.isRecording || S.voiceChatActive || S.voiceStartPending" in voice_active
    assert "window.isMicStarting" in voice_active
    assert "window.stopRecording({ notifyServer: false })" in prepare_text_mode
    assert "S.socket.send(JSON.stringify({ action: 'end_session' }))" in prepare_text_mode
    assert "await waitForAvatarDropVoiceTeardown(1500)" in prepare_text_mode
    assert "const notifyServer = options.notifyServer !== false;" in stop_recording
    assert "if (notifyServer && S.socket && S.socket.readyState === WebSocket.OPEN)" in stop_recording
    assert "window.addEventListener('neko:session-ended-by-server', finish, { once: true });" in wait_teardown
    assert "window.addEventListener('neko:character-left', finish, { once: true });" in wait_teardown
    assert "window.clearAudioQueue" in prepare_text_mode
    assert "S.isTextSessionActive = false;" in prepare_text_mode
    assert "window.syncVoiceChatComposerHidden(false)" in prepare_text_mode
    assert "window.dispatchEvent(new CustomEvent('neko:session-ended-by-server', { detail: response }))" in app_websocket_source
    assert "window.dispatchEvent(new CustomEvent('neko:character-left', { detail: response }))" in app_websocket_source


@pytest.mark.unit
def test_avatar_drop_scripts_and_backend_routes_are_wired():
    index_source = _read(INDEX_TEMPLATE_PATH)
    main_server_source = _read(MAIN_SERVER_PATH)

    assert index_source.index("/static/app-buttons.js") < index_source.index("/static/avatar/avatar-drop-parser.js")
    assert index_source.index("/static/avatar/avatar-drop-parser.js") < index_source.index("/static/avatar/avatar-drop-intake.js")
    assert "from main_routers.avatar_drop_router import router as avatar_drop_router" in main_server_source
    assert "app.include_router(avatar_drop_router)" in main_server_source


@pytest.mark.unit
def test_avatar_drop_image_and_memory_override_are_routed_as_text_session_inputs():
    core_source = _read_package(CORE_PACKAGE_PATH)
    cross_server_source = _read(CROSS_SERVER_PATH)
    offline_source = _read(OMNI_OFFLINE_PATH)
    websocket_source = _read(WEBSOCKET_ROUTER_PATH)

    assert '_TEXT_SESSION_INPUT_TYPES = frozenset({"text", "avatar_drop_image", "user_image"})' in core_source
    assert '_IMAGE_INPUT_TYPES = frozenset({"screen", "camera", "avatar_drop_image", "user_image"})' in core_source
    assert "memory_text = self._clean_frontend_memory_text(message.get(\"memory_text\"))" in core_source
    assert "record_data = memory_text or data" in core_source
    assert "openclaw_magic_command = self._normalize_explicit_openclaw_magic_command(data)" in core_source
    assert "_should_handoff_text_to_openclaw" not in core_source
    assert "input_transcript_callback" in core_source
    assert 'stream_text_kwargs["history_replacement_text"] = memory_text' in core_source
    assert "self._next_text_transcript_memory_text" not in core_source
    assert "memory_override_text" not in core_source
    assert "record_transcript_text = transcript_text" in core_source
    assert '"text": record_transcript_text' in core_source
    assert "msg_input_type in _TEXT_SESSION_INPUT_TYPES" in core_source
    assert "input_type in _IMAGE_INPUT_TYPES" in core_source
    assert '_USER_IMAGE_INPUT_TYPES = frozenset({"screen", "camera", "avatar_drop_image", "user_image"})' in cross_server_source
    assert "input_type in _USER_IMAGE_INPUT_TYPES" in cross_server_source
    assert "message[\"data\"].get(\"has_image\")" in cross_server_source
    assert "input_transcript_callback: Optional[Callable[[str], Awaitable[None]]] = None" in offline_source
    assert "history_replacement_text: str | None = None" in offline_source
    assert "self._conversation_history[history_replacement_index] = HumanMessage" in offline_source
    assert "transcript_callback = input_transcript_callback or self.on_input_transcript" in offline_source
    assert '_SESSION_INPUT_TYPES = frozenset({"audio", "screen", "camera", "text", "avatar_drop_image", "user_image"})' in websocket_source
    assert '_TEXT_SESSION_INPUT_TYPES = frozenset({"text", "avatar_drop_image", "user_image"})' in websocket_source
    assert '_ORDERED_STREAM_INPUT_TYPES = frozenset({"audio", "avatar_drop_image", "user_image"})' in websocket_source
    assert "input_type in _SESSION_INPUT_TYPES" in websocket_source
    assert "if input_type in _TEXT_SESSION_INPUT_TYPES:" in websocket_source
    assert "mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'" in websocket_source
    assert 'elif action == "stream_data":\n                input_type = message.get("input_type")' in websocket_source
    assert "if input_type in _ORDERED_STREAM_INPUT_TYPES:" in websocket_source
