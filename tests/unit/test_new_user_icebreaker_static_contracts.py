from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = ROOT / "static" / "tutorial" / "icebreaker" / "new-user-icebreaker.js"
ICEBREAKER_ASSISTANT_LOADING_PATH = ROOT / "static" / "tutorial" / "icebreaker" / "assistant-loading.js"
ICEBREAKER_FREE_TEXT_RUNTIME_PATH = ROOT / "static" / "tutorial" / "icebreaker" / "free-text-runtime.js"
SCRIPTS_PATH = ROOT / "static" / "tutorial" / "icebreaker" / "icebreaker_scripts.json"
LOCALE_PATH = ROOT / "static" / "tutorial" / "icebreaker" / "locales" / "zh-CN.json"
LOCALES_DIR = ROOT / "static" / "tutorial" / "icebreaker" / "locales"
CHAT_HOST_PATH = ROOT / "static" / "app-react-chat-window.js"
APP_WEBSOCKET_PATH = ROOT / "static" / "app-websocket.js"
APP_PROACTIVE_PATH = ROOT / "static" / "app-proactive.js"
APP_PROMPT_PATH = ROOT / "static" / "tutorial" / "core" / "app-prompt.js"
UNIVERSAL_TUTORIAL_MANAGER_PATH = ROOT / "static" / "tutorial" / "core" / "universal-manager.js"
APP_INTERPAGE_PATH = ROOT / "static" / "app-interpage.js"
INDEX_TEMPLATE_PATH = ROOT / "templates" / "index.html"
WEBSOCKET_ROUTER_PATH = ROOT / "main_routers" / "websocket_router.py"
GAME_ROUTER_PATH = ROOT / "main_routers" / "game_router.py"
ICEBREAKER_ROUTER_PATH = ROOT / "main_routers" / "icebreaker_router.py"
ICEBREAKER_PROMPTS_PATH = ROOT / "config" / "prompts" / "prompts_icebreaker.py"
ICEBREAKER_FREE_TEXT_UTILS_PATH = ROOT / "utils" / "icebreaker_free_text.py"
LIVE2D_CORE_PATH = ROOT / "static" / "live2d-core.js"
SUBTITLE_PATH = ROOT / "static" / "subtitle.js"


def assert_icebreaker_script_has_voice_keys_for_every_spoken_line(day_key: str):
    scripts = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))
    locale = json.loads(LOCALE_PATH.read_text(encoding="utf-8"))
    day = scripts["days"][day_key]

    # All seven days intentionally use the same lightweight 3-round tree:
    # root -> 2 branches -> 4 handoff leaves.
    expected_node_counts = {
        "1": 7,
        "2": 7,
        "3": 7,
        "4": 7,
        "5": 7,
        "6": 7,
        "7": 7,
    }
    assert len(day["nodes"]) == expected_node_counts[day_key]

    for node_id, node in day["nodes"].items():
        assert node.get("voiceKey"), node_id
        assert locale.get(node["lineKey"]), node["lineKey"]
        for option in node.get("options", []):
            assert locale.get(option["labelKey"]), option["labelKey"]
            if "handoffKey" in option:
                assert option.get("handoffVoiceKey"), f"{node_id}:{option.get('id')}"
                assert locale.get(option["handoffKey"]), option["handoffKey"]


def test_day1_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("1")


def test_day2_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("2")


def test_day3_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("3")


def test_day4_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("4")


def test_day5_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("5")


def test_day6_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("6")


def test_day7_icebreaker_script_has_voice_keys_for_every_spoken_line():
    assert_icebreaker_script_has_voice_keys_for_every_spoken_line("7")


def test_icebreaker_internal_branches_follow_binary_tree_targets():
    scripts = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))

    for day_key, day in scripts["days"].items():
        nodes = day["nodes"]
        for node_id, node in nodes.items():
            options = node.get("options", [])
            if node.get("complete") or any("handoffKey" in option for option in options):
                continue

            assert [option.get("id") for option in options] == ["A", "B"], f"day{day_key}.{node_id}"
            assert options[0]["next"] in nodes, f"day{day_key}.{node_id}.A"
            assert options[1]["next"] in nodes, f"day{day_key}.{node_id}.B"


def test_day1_icebreaker_locales_exist_and_have_aligned_keys():
    expected_locales = ["en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"]
    zh_cn = json.loads((LOCALES_DIR / "zh-CN.json").read_text(encoding="utf-8"))
    expected_keys = set(zh_cn)

    for locale in expected_locales:
        path = LOCALES_DIR / f"{locale}.json"
        assert path.exists(), locale
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == expected_keys, locale
        assert all(str(value).strip() for value in data.values()), locale


def test_day1_icebreaker_non_source_locales_are_translated_not_copied():
    zh_cn = json.loads((LOCALES_DIR / "zh-CN.json").read_text(encoding="utf-8"))

    for locale in ["en", "es", "ja", "ko", "pt", "ru", "zh-TW"]:
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert data != zh_cn, locale
        assert data["day1.1.line"] != zh_cn["day1.1.line"], locale
        assert data["day1.fallback.release"] != zh_cn["day1.fallback.release"], locale


def test_day2_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day2")


def test_day3_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day3")


def test_day4_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day4")


def test_day5_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day5")


def test_rewritten_icebreaker_fallbacks_stay_on_day_topics():
    topic_terms = {
        "day2": {
            "en": "flavor",
            "es": "sabor",
            "ja": "味",
            "ko": "맛",
            "pt": "sabor",
            "ru": "вкус",
            "zh-CN": "口味",
            "zh-TW": "口味",
        },
        "day3": {
            "en": "sunlight",
            "es": "sol",
            "ja": "日なた",
            "ko": "햇살",
            "pt": "sol",
            "ru": "солнце",
            "zh-CN": "阳光",
            "zh-TW": "陽光",
        },
        "day5": {
            "en": "tail",
            "es": "cola",
            "ja": "しっぽ",
            "ko": "꼬리",
            "pt": "cauda",
            "ru": "хвост",
            "zh-CN": "尾巴",
            "zh-TW": "尾巴",
        },
        "day6": {
            "en": "ears",
            "es": "orejas",
            "ja": "耳",
            "ko": "귀",
            "pt": "orelhas",
            "ru": "ушки",
            "zh-CN": "耳朵",
            "zh-TW": "耳朵",
        },
        "day7": {
            "en": "week",
            "es": "semana",
            "ja": "一週間",
            "ko": "일주일",
            "pt": "semana",
            "ru": "недели",
            "zh-CN": "星期",
            "zh-TW": "星期",
        },
    }
    stale_terms = {
        "day2": {
            "en": ("window",),
            "es": ("ventana",),
            "ja": ("窓",),
            "ko": ("창가",),
            "pt": ("janela",),
            "ru": ("окн",),
            "zh-CN": ("窗",),
            "zh-TW": ("窗",),
        },
        "day3": {
            "en": ("cake",),
            "es": ("pastel",),
            "ja": ("ケーキ",),
            "ko": ("케이크",),
            "pt": ("bolo",),
            "ru": ("торт",),
            "zh-CN": ("蛋糕",),
            "zh-TW": ("蛋糕",),
        },
        "day5": {
            "en": ("note",),
            "es": ("nota",),
            "ja": ("メモ",),
            "ko": ("쪽지",),
            "pt": ("nota", "notinha"),
            "ru": ("записк",),
            "zh-CN": ("纸条",),
            "zh-TW": ("紙條",),
        },
        "day6": {
            "en": ("habit", "ritual"),
            "es": ("hábito", "ritual"),
            "ja": ("習慣", "合図"),
            "ko": ("습관", "신호"),
            "pt": ("hábito", "ritual"),
            "ru": ("привыч", "ритуал"),
            "zh-CN": ("习惯", "仪式"),
            "zh-TW": ("習慣", "儀式"),
        },
        "day7": {
            "en": ("ribbon",),
            "es": ("cinta",),
            "ja": ("リボン",),
            "ko": ("리본",),
            "pt": ("fita",),
            "ru": ("ленточ",),
            "zh-CN": ("丝带",),
            "zh-TW": ("絲帶",),
        },
    }

    for day_prefix, locale_terms in topic_terms.items():
        for locale, topic_term in locale_terms.items():
            data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
            fallback_text = f"{data[f'{day_prefix}.fallback.redirect']} {data[f'{day_prefix}.fallback.release']}"
            assert topic_term.lower() in fallback_text.lower(), f"{locale}:{day_prefix}"
            for stale_term in stale_terms[day_prefix][locale]:
                assert stale_term.lower() not in fallback_text.lower(), f"{locale}:{day_prefix}:{stale_term}"


def test_day6_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day6")


def test_day7_icebreaker_non_source_locales_are_translated_not_copied():
    assert_icebreaker_non_source_locales_are_translated_not_copied("day7")


def assert_icebreaker_non_source_locales_are_translated_not_copied(day_prefix: str):
    zh_cn = json.loads((LOCALES_DIR / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))

    for locale in ["en", "es", "ja", "ko", "pt", "ru", "zh-TW"]:
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert data != zh_cn, locale
        assert data[f"{day_prefix}.1.line"] != zh_cn[f"{day_prefix}.1.line"], locale
        assert data[f"{day_prefix}.fallback.release"] != zh_cn[f"{day_prefix}.fallback.release"], locale
        if locale not in ("en", "zh-TW"):
            assert data[f"{day_prefix}.1.line"] != en[f"{day_prefix}.1.line"], locale
            assert data[f"{day_prefix}.fallback.release"] != en[f"{day_prefix}.fallback.release"], locale


def test_day2_latin_script_locales_do_not_contain_chinese_copy():
    for locale in ["en", "es", "pt", "ru"]:
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith("day2."):
                assert not re.search(r"[\u4e00-\u9fff]", value), f"{locale}:{key}"


def test_day3_latin_script_locales_do_not_contain_chinese_copy():
    assert_latin_script_locales_do_not_contain_chinese_copy("day3")


def test_day4_latin_script_locales_do_not_contain_chinese_copy():
    assert_latin_script_locales_do_not_contain_chinese_copy("day4")


def test_day5_latin_script_locales_do_not_contain_chinese_copy():
    assert_latin_script_locales_do_not_contain_chinese_copy("day5")


def test_day6_latin_script_locales_do_not_contain_chinese_copy():
    assert_latin_script_locales_do_not_contain_chinese_copy("day6")


def test_day7_latin_script_locales_do_not_contain_chinese_copy():
    assert_latin_script_locales_do_not_contain_chinese_copy("day7")


def assert_latin_script_locales_do_not_contain_chinese_copy(day_prefix: str):
    for locale in ["en", "es", "pt", "ru"]:
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith(f"{day_prefix}."):
                assert not re.search(r"[\u4e00-\u9fff]", value), f"{locale}:{key}"


def test_day1_icebreaker_script_does_not_hardcode_live2d_emotions():
    scripts = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))
    day1 = scripts["days"]["1"]

    for node_id, node in day1["nodes"].items():
        assert "emotion" not in node, node_id
        assert "expressionFile" not in node, node_id
        for option in node.get("options", []):
            if "handoffKey" in option:
                assert "handoffEmotion" not in option, f"{node_id}:{option.get('id')}"
                assert "handoffExpressionFile" not in option, f"{node_id}:{option.get('id')}"

    fallback = day1["fallback"]
    assert "redirectEmotion" not in fallback
    assert "releaseEmotion" not in fallback
    assert "redirectExpressionFile" not in fallback
    assert "releaseExpressionFile" not in fallback


def test_day1_icebreaker_copy_keeps_user_options_in_user_voice():
    locale = json.loads(LOCALE_PATH.read_text(encoding="utf-8"))

    forbidden_anywhere = ("主人", "调教")
    forbidden_in_options = ("本喵",)

    for key, value in locale.items():
        if not key.startswith("day1."):
            continue
        for term in forbidden_anywhere:
            assert term not in value, key
        assert "live2d" not in value.lower(), key
        assert "（）" not in value, key
        if ".options." in key:
            for term in forbidden_in_options:
                assert term not in value, key


def test_day1_icebreaker_fallback_redirect_is_node_agnostic():
    locale = json.loads(LOCALE_PATH.read_text(encoding="utf-8"))
    redirect = locale["day1.fallback.redirect"]

    assert "接住" in redirect
    assert "追得上" in redirect
    assert "选项" not in redirect
    assert "功能" not in redirect


def test_icebreaker_runtime_wires_choice_prompt_and_project_tts():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    chat_host = CHAT_HOST_PATH.read_text(encoding="utf-8")
    app_websocket = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    index_html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "new_user_icebreaker" in runtime
    assert "var ICEBREAKER_API_BASE = '/api/icebreaker'" in runtime
    assert "ICEBREAKER_API_BASE + '/speak'" in runtime
    assert "/api/game/new_user_icebreaker" not in runtime
    assert "encodeURIComponent(GAME_TYPE)" not in runtime
    assert "mirror_text: false" in runtime
    assert "interrupt_audio: true" in runtime
    assert "voiceKey" in runtime
    assert "handoffVoiceKey" in runtime
    assert "appendLlmContext" in runtime
    assert "applyAssistantTextEmotion" in runtime
    assert "resolveAssistantAvatarUrl" in runtime
    assert "window.appChatAvatar.getCurrentAvatarDataUrl()" in runtime
    assert "avatarUrl: role === 'assistant' ? resolveAssistantAvatarUrl() : undefined" in runtime
    assert "analyzeIcebreakerEmotion" not in runtime
    assert "emotionSequence" not in runtime
    assert "node.emotion" not in runtime
    assert "expressionFile" not in runtime
    assert "resolveLatestEndState(detail, eventType)" in runtime
    assert "synthesizeEndStateFromEvent(eventType, normalizedDetail)" in runtime
    assert "eventType === 'neko:tutorial-skipped'" in runtime
    assert "eventType === 'neko:tutorial-completed'" in runtime
    assert "normalizedDetail.day" in runtime
    assert "day = 1" not in runtime
    assert "playExpression(normalizedEmotion, normalizedExpressionFile)" not in runtime
    assert "bootstrapFromRecentEndState" in runtime
    assert "neko_avatar_floating_guide_v1" in runtime
    assert "resolveRecentPersistedEndState" in runtime
    assert "setIcebreakerChoicePrompt" in chat_host
    assert "clearIcebreakerChoicePrompt" in chat_host
    assert "neko:icebreaker-choice-selected" in chat_host
    assert "neko:icebreaker-free-text-submitted" in chat_host
    assert "resolveCurrentAssistantAvatarUrl" in chat_host
    assert "var baseAvatarUrl = message.baseAvatarUrl || message.avatarUrl" in chat_host
    assert "avatarUrl: resolveCurrentAssistantAvatarUrl(message.role, baseAvatarUrl)" in chat_host
    assert "refreshAssistantAvatarUrls" in chat_host
    assert "if (message.avatarUrl === avatarUrl && message.baseAvatarUrl === baseAvatarUrl) return message" in chat_host
    assert "baseAvatarUrl: baseAvatarUrl" in chat_host
    assert "window.addEventListener('chat-avatar-preview-updated', refreshAssistantAvatarUrls)" in chat_host
    assert "window.addEventListener('chat-avatar-preview-cleared', refreshAssistantAvatarUrls)" in chat_host
    assert "/static/tutorial/icebreaker/assistant-loading.js" in index_html
    assert "/static/tutorial/icebreaker/free-text-runtime.js" in index_html
    assert "/static/tutorial/icebreaker/new-user-icebreaker.js" in index_html
    assert index_html.index("/static/tutorial/icebreaker/assistant-loading.js") < index_html.index(
        "/static/tutorial/icebreaker/new-user-icebreaker.js"
    )
    assert index_html.index("/static/tutorial/icebreaker/free-text-runtime.js") < index_html.index(
        "/static/tutorial/icebreaker/new-user-icebreaker.js"
    )


def test_icebreaker_context_append_does_not_touch_shared_websocket_router():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    websocket_router = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")
    game_router = GAME_ROUTER_PATH.read_text(encoding="utf-8")
    icebreaker_router = ICEBREAKER_ROUTER_PATH.read_text(encoding="utf-8")

    assert "appendLlmContext(role, messageText" in runtime
    assert "ICEBREAKER_API_BASE + '/context'" in runtime
    assert "request_id: String(extra.requestId || '')" in runtime
    assert "request_id = str(data.get(\"request_id\") or event.get(\"request_id\") or \"\").strip()" in icebreaker_router
    assert "_icebreaker_context_seen_request_ids" not in icebreaker_router
    assert "append_context(" in icebreaker_router
    assert "source=\"icebreaker\"" in icebreaker_router
    assert "MAX_ICEBREAKER_CONTEXT_TEXT_LENGTH = 2000" in icebreaker_router
    assert "invalid_text_length" in icebreaker_router
    assert "append_icebreaker_context_async" not in game_router
    assert '@router.post("/{game_type}/context")' not in game_router
    assert '@router.post("/context")' in icebreaker_router
    assert "startIcebreakerRoute(nextSession).then(function (started) {" in runtime
    assert "ICEBREAKER_API_BASE + path" in runtime
    assert "postIcebreakerRoute('/route/start', session" in runtime
    assert "postIcebreakerRoute('/route/end', session" in runtime
    assert "postgameProactive: { enabled: false }" in runtime
    assert "action: 'icebreaker_context_append'" not in runtime
    assert 'action == "icebreaker_context_append"' not in websocket_router


def test_icebreaker_route_is_separate_from_game_route_active_state():
    game_router = GAME_ROUTER_PATH.read_text(encoding="utf-8")
    icebreaker_router = ICEBREAKER_ROUTER_PATH.read_text(encoding="utf-8")
    window_open_guard = game_router.split("mgr_for_ws = get_session_manager().get(lanlan_name)", 1)[1].split(
        "else:",
        1,
    )[0]

    assert '"reason": "not_a_game_route"' in game_router
    assert '"/api/icebreaker/route/start"' in game_router
    assert '"/api/icebreaker/speak"' in game_router
    assert '"/api/icebreaker/route/end"' in game_router
    assert "activate_icebreaker_route" in icebreaker_router
    assert "_get_active_game_route_state" not in icebreaker_router
    assert "game_window_state_change" not in icebreaker_router
    assert 'state.get("game_route_active")' in window_open_guard
    assert 'action="opened"' in game_router


def test_icebreaker_route_is_finalized_when_renderer_websocket_disconnects():
    websocket_router = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")
    cleanup_block = websocket_router.split('logger.info(f"Cleaning up WebSocket resources: {websocket.client}")', 1)[1].split(
        "if is_current and lanlan_name in session_manager:",
        1,
    )[0]

    assert "finalize_icebreaker_route" in websocket_router
    assert "get_active_icebreaker_route_session_id" in websocket_router
    assert "icebreaker_session_id = get_active_icebreaker_route_session_id(lanlan_name)" in cleanup_block
    assert "if is_current and icebreaker_session_id:" in cleanup_block
    assert "try:" in cleanup_block
    assert "finalize_icebreaker_route(" in cleanup_block
    assert "session_id=icebreaker_session_id" in cleanup_block
    assert 'reason="websocket_disconnect"' in cleanup_block
    assert "except Exception as exc:" in cleanup_block
    assert "_get_active_game_route_state" not in cleanup_block


def test_icebreaker_context_reuses_existing_session_context_paths():
    core = (ROOT / "main_logic" / "core.py").read_text(encoding="utf-8")

    assert "pending_icebreaker_context" not in core
    assert "_icebreaker_context_request_ids" not in core
    assert "def _flush_pending_icebreaker_context" not in core
    assert "def append_icebreaker_context_async" not in core
    assert "append_icebreaker_context_async(self, role: str, text: str, request_id" not in core
    assert "def append_icebreaker_context(" not in core
    assert "async def append_context(" in core
    assert "source: str" in core
    assert "audience: str = \"model\"" in core
    assert "lifetime: str = \"current_session\"" in core
    assert "_normalize_scripted_context" not in core
    assert "_append_scripted_context_to_new_session_cache" not in core
    assert "_conversation_history" in core
    assert "message_cache_for_new_session" in core
    assert "_CONTEXT_APPEND_BARE_PRIME_SOURCES" in core
    assert 'prime_text = content if source in _CONTEXT_APPEND_BARE_PRIME_SOURCES else f"{role}: {content}"' in core


def test_icebreaker_context_appends_are_serialized_before_chat_progression():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "var contextAppendPromise = Promise.resolve();" in runtime
    assert "contextAppendPromise = contextAppendPromise.catch(function () {}).then(function () {" in runtime
    assert "return contextAppendPromise;" in runtime

    append_message_block = runtime.split("function appendChatMessage(role, text, meta)", 1)[1].split(
        "function speakViaProjectTts",
        1,
    )[0]
    context_then = "return appendLlmContext(role, messageText, meta || {}).then(function () {"
    assert context_then in append_message_block
    assert "broadcastIcebreakerAppendMessage(message);" in append_message_block
    assert append_message_block.index("broadcastIcebreakerAppendMessage(message);") < append_message_block.index(
        context_then
    )
    assert append_message_block.index(context_then) < append_message_block.index(
        "return waitForChatHost(30000).then(function (host) {"
    )


def test_icebreaker_context_append_requires_successful_json_payload():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    append_context_block = runtime.split("function appendLlmContext(role, text, meta)", 1)[1].split(
        "function getIcebreakerMessageText(message)",
        1,
    )[0]

    assert "getLocalMutationHeaders().then(function (headers)" in append_context_block
    assert "'X-CSRF-Token'" in runtime
    assert "error_code === 'csrf_validation_failed'" in append_context_block
    assert "refreshLocalMutationHeaders()" in append_context_block
    assert "function parseContextResponse(response)" in append_context_block
    assert "return response.json().then(function (data)" in append_context_block
    assert "return !!(data && data.ok);" in append_context_block
    assert append_context_block.index("function parseContextResponse(response)") < append_context_block.index(
        "function postContextWithHeaders(headers, allowRetry)"
    )
    assert append_context_block.index("return response.json().then(function (data)") < append_context_block.index(
        "return !!(data && data.ok);"
    )
    assert "return parseContextResponse(response);" in append_context_block
    assert "return response;\n            }).then(function (response)" not in append_context_block
    assert "return true;" not in append_context_block


def test_icebreaker_assistant_messages_update_compact_caption_like_normal_chat():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    interpage_runtime = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "function syncIcebreakerAssistantCompactCaption(role, message)" in runtime
    assert "function finalizeIcebreakerAssistantSubtitleTranslation(role, message)" in runtime
    assert "function waitForIcebreakerChatHostMounted(host)" in runtime
    sync_block = runtime.split("function syncIcebreakerAssistantCompactCaption(role, message)", 1)[1].split(
        "function finalizeIcebreakerAssistantSubtitleTranslation(role, message)",
        1,
    )[0]
    assert "if (role !== 'assistant') return;" in sync_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-turn-start'" in sync_block
    assert "window.dispatchEvent(new CustomEvent('neko-compact-caption-update'" in sync_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable'" not in sync_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-turn-end'" not in sync_block
    assert "segmentId: turnId + ':icebreaker'" in sync_block
    assert "source: SOURCE" in sync_block
    assert "openSubtitleTranslationForIcebreakerAssistantMessage()" not in sync_block
    assert "setSubtitleEnabled(true" not in sync_block
    assert "setTranslateEnabled(true" not in sync_block

    subtitle_block = runtime.split("function finalizeIcebreakerAssistantSubtitleTranslation(role, message)", 1)[1].split(
        "function waitForIcebreakerChatHostMounted(host)",
        1,
    )[0]
    assert "if (role !== 'assistant') return;" in subtitle_block
    assert "window.subtitleBridge" in subtitle_block
    assert "bridge.beginTurn({ latch: false });" in subtitle_block
    assert "bridge.finalizeTurnWithTranslation(line)" in subtitle_block
    assert "console.warn('[NewUserIcebreaker] subtitle translation failed:'" in subtitle_block
    assert "setSubtitleEnabled(true" not in subtitle_block
    assert "setTranslateEnabled(true" not in subtitle_block

    assert "function syncIcebreakerAssistantCompactCaption(message)" in interpage_runtime
    assert "function finalizeIcebreakerAssistantSubtitleTranslation(message)" in interpage_runtime
    assert "function waitForIcebreakerChatHostMounted(host)" in interpage_runtime
    interpage_compact_block = interpage_runtime.split(
        "function syncIcebreakerAssistantCompactCaption(message)", 1
    )[1].split("function finalizeIcebreakerAssistantSubtitleTranslation(message)", 1)[0]
    assert "if (!isStandaloneChatPage() || !message || message.role !== 'assistant') return;" in interpage_compact_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-turn-start'" in interpage_compact_block
    assert "window.dispatchEvent(new CustomEvent('neko-compact-caption-update'" in interpage_compact_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable'" not in interpage_compact_block
    assert "window.dispatchEvent(new CustomEvent('neko-assistant-turn-end'" not in interpage_compact_block
    interpage_subtitle_block = interpage_runtime.split(
        "function finalizeIcebreakerAssistantSubtitleTranslation(message)", 1
    )[1].split("function waitForIcebreakerChatHostMounted(host)", 1)[0]
    assert "if (!isStandaloneChatPage() || !message || message.role !== 'assistant') return;" in interpage_subtitle_block
    assert "window.subtitleBridge" in interpage_subtitle_block
    assert "bridge.beginTurn({ latch: false });" in interpage_subtitle_block
    assert "bridge.finalizeTurnWithTranslation(line)" in interpage_subtitle_block
    assert "setSubtitleEnabled(true" not in interpage_subtitle_block
    assert "setTranslateEnabled(true" not in interpage_subtitle_block
    assert "return Promise.resolve(host.appendMessage(action.message)).then(function (result) {" in interpage_runtime
    assert "return waitForIcebreakerChatHostMounted(host).then(function () {" in interpage_runtime
    assert "syncIcebreakerAssistantCompactCaption(action.message);" in interpage_runtime
    assert "finalizeIcebreakerAssistantSubtitleTranslation(action.message);" in interpage_runtime
    assert interpage_runtime.index("return Promise.resolve(host.appendMessage(action.message)).then(function (result) {") < interpage_runtime.index(
        "return waitForIcebreakerChatHostMounted(host).then(function () {"
    ) < interpage_runtime.index(
        "syncIcebreakerAssistantCompactCaption(action.message);"
    ) < interpage_runtime.index(
        "finalizeIcebreakerAssistantSubtitleTranslation(action.message);"
    )
    assert "icebreaker_assistant_subtitle" not in runtime
    assert "icebreaker_assistant_subtitle" not in interpage_runtime


def test_icebreaker_assistant_message_does_not_auto_open_subtitle_translation_panel():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function openSubtitleTranslationForIcebreakerAssistantMessage()" not in runtime
    assert "new-user-icebreaker-auto-open" not in runtime
    assert "icebreakerSubtitlePanelOpenedSessionId" not in runtime
    assert "shouldOpenIcebreakerSubtitlePanelOnce" not in runtime

    start_block = runtime.split("return startIcebreakerRoute(nextSession).then(function (started)", 1)[1].split(
        "activeSession = nextSession;",
        1,
    )[0]
    assert "setSubtitleEnabled(true" not in start_block
    assert "setTranslateEnabled(true" not in start_block

    sync_block = runtime.split("function finalizeIcebreakerAssistantSubtitleTranslation(role, message)", 1)[1].split(
        "function appendChatMessage(role, text, meta)",
        1,
    )[0]
    assert "if (role !== 'assistant') return;" in sync_block
    assert "setSubtitleEnabled(true" not in sync_block
    assert "setTranslateEnabled(true" not in sync_block
    assert "bridge.finalizeTurnWithTranslation(line)" in sync_block

    append_message_block = runtime.split("function appendChatMessage(role, text, meta)", 1)[1].split(
        "function speakViaProjectTts",
        1,
    )[0]
    assert "return appendLlmContext(role, messageText, meta || {}).then(function () {" in append_message_block
    standalone_branch = append_message_block.split("if (!shouldRenderIcebreakerOnLocalChatHost()) {", 1)[1].split(
        "var chatHost = null;",
        1,
    )[0]
    assert "finalizeIcebreakerAssistantSubtitleTranslation(role, message);" in standalone_branch
    assert "syncIcebreakerAssistantCompactCaption(role, message);" not in standalone_branch
    assert "return host.appendMessage(message);" in append_message_block
    assert "return waitForIcebreakerChatHostMounted(chatHost).then(function () {" in append_message_block
    assert "syncIcebreakerAssistantCompactCaption(role, message);" in append_message_block
    assert "finalizeIcebreakerAssistantSubtitleTranslation(role, message);" in append_message_block
    assert append_message_block.index("return host.appendMessage(message);") < append_message_block.index(
        "return waitForIcebreakerChatHostMounted(chatHost).then(function () {"
    ) < append_message_block.rindex(
        "syncIcebreakerAssistantCompactCaption(role, message);"
    ) < append_message_block.rindex(
        "finalizeIcebreakerAssistantSubtitleTranslation(role, message);"
    )


def test_icebreaker_assistant_lines_show_fake_thinking_dots_before_text():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    loading_runtime = ICEBREAKER_ASSISTANT_LOADING_PATH.read_text(encoding="utf-8")
    assistant_append_block = runtime.split("function appendAssistantChatMessage(text, meta, session)", 1)[1].split(
        "function appendChatMessage(role, text, meta)",
        1,
    )[0]
    deliver_node_block = runtime.split("function deliverNode(nodeId)", 1)[1].split(
        "function completeWithHandoff(option)",
        1,
    )[0]
    handoff_block = runtime.split("function completeWithHandoff(option)", 1)[1].split(
        "function advanceWithChoice(session, option, choice, label, choiceNodeId)",
        1,
    )[0]
    free_text_block = runtime.split("function applyFreeTextInterpretation(session, interpretation, snapshot)", 1)[1].split(
        "function handleFreeText(detail)",
        1,
    )[0]

    assert "var DEFAULT_FAKE_LOADING_MS = 1100;" in loading_runtime
    assert "new CustomEvent('neko-focus-thinking'" in loading_runtime
    assert "detail: { active: active === true, source: String(source || '') }" in loading_runtime
    assert "function waitMs(ms)" in loading_runtime
    assert "waitMs(durationMs)" in loading_runtime
    assert "host.openWindow();" in loading_runtime
    assert "return waitForMounted(host).then(function () {" in loading_runtime
    assert "dispatchThinking(true, source);" in loading_runtime
    assert "dispatchThinking(false, source);" in loading_runtime
    assert "assistantLoading.showAssistantFakeLoading({" in runtime
    assert "return appendChatMessage('assistant', text, meta);" in assistant_append_block
    assert "}).then(function (message) {" in assistant_append_block
    assert "if (targetSession && activeSession !== targetSession) return null;" in assistant_append_block
    assert "return message;" in assistant_append_block
    assert "appendChatMessage('user', label" in runtime
    assert "appendAssistantChatMessage(text, {" in deliver_node_block
    assert "appendAssistantChatMessage(text, {" in handoff_block
    assert "appendAssistantChatMessage(releaseText, {" in free_text_block
    assert "appendAssistantChatMessage(replyText, {" in free_text_block


def test_icebreaker_project_tts_uses_local_mutation_headers():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    speak_block = runtime.split("function speakViaProjectTts(text, voiceKey, signal)", 1)[1].split(
        "function speakLine(text, voiceKey)",
        1,
    )[0]

    assert "getLocalMutationHeaders().then(function (headers)" in speak_block
    assert "headers: headers" in speak_block
    assert "if (signal) requestOptions.signal = signal;" in speak_block
    assert "if (error && error.name === 'AbortError') return false;" in speak_block
    assert "headers: { 'Content-Type': 'application/json' }" not in speak_block


def test_icebreaker_speak_line_waits_for_estimated_speech_duration():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    tts_wait_block = runtime.split("function waitForTtsRequest(text, voiceKey)", 1)[1].split(
        "function speakLine(text, voiceKey)",
        1,
    )[0]
    speak_line_block = runtime.split("function speakLine(text, voiceKey)", 1)[1].split(
        "function applyAssistantTextEmotion(text)",
        1,
    )[0]

    assert "var TTS_REQUEST_MAX_WAIT_MS = 12000;" in runtime
    assert "var controller = typeof AbortController === 'function' ? new AbortController() : null;" in tts_wait_block
    assert "if (controller) controller.abort();" in tts_wait_block
    assert "var timeoutId = window.setTimeout(function () {" in tts_wait_block
    assert "speakViaProjectTts(text, voiceKey, controller ? controller.signal : undefined)" in tts_wait_block
    assert "var speechDurationPromise = new Promise(function (resolve) {" in speak_line_block
    assert "window.setTimeout(resolve, estimateSpeechDurationMs(text));" in speak_line_block
    assert "var ttsRequestPromise = waitForTtsRequest(text, voiceKey);" in speak_line_block
    assert "return Promise.all([speechDurationPromise, ttsRequestPromise]).then(function () {});" in speak_line_block
    assert "return speakViaProjectTts(text, voiceKey).then(function () {" not in speak_line_block
    assert "if (ok) return;" not in speak_line_block
    assert speak_line_block.index("var speechDurationPromise = new Promise") < speak_line_block.index(
        "var ttsRequestPromise = waitForTtsRequest(text, voiceKey);"
    )
    assert speak_line_block.index("var ttsRequestPromise = waitForTtsRequest(text, voiceKey);") < speak_line_block.index(
        "return Promise.all([speechDurationPromise, ttsRequestPromise]).then(function () {});"
    )


def test_icebreaker_choice_submission_is_mutexed_and_restores_prompt_on_failure():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    handle_choice_block = runtime.split("function handleChoice(detail)", 1)[1].split(
        "function handleFreeText(detail)",
        1,
    )[0]
    advance_choice_block = runtime.split("function advanceWithChoice(session, option, choice, label, choiceNodeId)", 1)[1].split(
        "function handleChoice(detail)",
        1,
    )[0]

    assert "if (session.choiceInFlight || session.freeTextInFlight) return;" in handle_choice_block
    assert "session.choiceInFlight = true;" in handle_choice_block
    assert "clearChoicePrompt();" in handle_choice_block
    assert handle_choice_block.index("session.choiceInFlight = true;") < handle_choice_block.index("clearChoicePrompt();")
    assert handle_choice_block.index("clearChoicePrompt();") < handle_choice_block.index("appendChatMessage('user', label")
    assert "if (!message)" in handle_choice_block
    assert "if (activeSession !== session)" in handle_choice_block
    assert "return advanceWithChoice(session, option, choice, label, choiceNodeId);" in handle_choice_block
    assert "if (!session || activeSession !== session || !option) return Promise.resolve(null);" in advance_choice_block
    assert "return deliverNode(option.next);" in advance_choice_block
    assert "return completeWithHandoff(option);" in advance_choice_block
    assert "return Promise.resolve(false);" in advance_choice_block
    assert "session.choiceInFlight = false;" in handle_choice_block
    assert "setChoicePrompt(node, session.localeData);" in handle_choice_block


def test_icebreaker_reveals_next_choice_prompt_after_assistant_line_delay():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    deliver_node_block = runtime.split("function deliverNode(nodeId)", 1)[1].split(
        "function completeWithHandoff(option)",
        1,
    )[0]

    assert "var CHOICE_PROMPT_REVEAL_MIN_DELAY_MS = 700;" in runtime
    assert "function computeChoicePromptRevealDelay(text)" in runtime
    assert "var session = activeSession;" in deliver_node_block
    assert "if (!activeSession) return Promise.resolve(false);" in deliver_node_block
    assert "var previousNodeId = session.nodeId;" in deliver_node_block
    assert "var localeData = session.localeData;" in deliver_node_block
    assert "if (!node) return Promise.resolve(false);" in deliver_node_block
    assert "if (activeSession !== session || session.nodeId !== nodeId) return false;" in deliver_node_block
    assert "session.nodeId = previousNodeId;" in deliver_node_block
    append_failure_block = deliver_node_block.split("if (!didAppendChatMessage(message)) {", 1)[1].split(
        "markDay(session.day",
        1,
    )[0]
    assert append_failure_block.index("session.nodeId = previousNodeId;") < append_failure_block.index("return false;")
    # 揭示延迟改为「只扣视觉」：choicePrompt 立刻下发（绑定输入路由），延迟值随
    # revealDelayMs 交给 chat host 按 revealAt 延后露出按钮。deliverNode 不再用
    # promise 把 setChoicePrompt 整体往后拖，避免间隙内输入落到普通聊天。
    assert "waitBeforeChoicePromptReveal" not in runtime
    choice_prompt_call = "setChoicePrompt(node, localeData, computeChoicePromptRevealDelay(text))"
    assert choice_prompt_call in deliver_node_block
    assert "return true;" in deliver_node_block
    assert deliver_node_block.index("speakLine(text, node.voiceKey || '');") < deliver_node_block.index(
        choice_prompt_call
    )


def test_icebreaker_handoff_waits_for_context_append_before_route_end():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    handoff_block = runtime.split("function completeWithHandoff(option)", 1)[1].split(
        "function handleChoice(detail)",
        1,
    )[0]

    assert "var session = activeSession;" in handoff_block
    assert "function speakViaProjectTts(text, voiceKey, signal)" in runtime
    assert "function waitForTtsRequest(text, voiceKey)" in runtime
    assert "return Promise.all([speechDurationPromise, ttsRequestPromise]).then(function () {});" in runtime
    assert "var handoffSpeechPromise = Promise.resolve(false);" in handoff_block
    assert "handoffSpeechPromise = speakLine(text, option.handoffVoiceKey || '');" in handoff_block
    assert "return appendAssistantChatMessage(text" in handoff_block
    assert "if (!didAppendChatMessage(message)) return false;" in handoff_block
    assert "return endIcebreakerRoute(session, 'icebreaker_handoff');" in handoff_block
    assert "return Promise.resolve(handoffSpeechPromise).catch(function () {}).then(function () {" in handoff_block
    assert "}).then(function (completed) {" in handoff_block
    assert "if (!completed) return false;" in handoff_block
    assert handoff_block.index("return appendAssistantChatMessage(text") < handoff_block.index(
        "return endIcebreakerRoute(session, 'icebreaker_handoff');"
    )
    assert handoff_block.index("handoffSpeechPromise = speakLine") < handoff_block.index(
        "return endIcebreakerRoute(session, 'icebreaker_handoff');"
    )
    assert handoff_block.index("return Promise.resolve(handoffSpeechPromise)") < handoff_block.index(
        "dispatchIcebreakerEnded('handoff');"
    )
    assert handoff_block.index("return Promise.resolve(handoffSpeechPromise)") < handoff_block.index(
        "completed: true"
    )
    assert handoff_block.index("return Promise.resolve(handoffSpeechPromise)") < handoff_block.rindex(
        "if (!completed) return false;"
    ) < handoff_block.index("completed: true")
    assert handoff_block.index("completed: true") < handoff_block.index("dispatchIcebreakerEnded('handoff');")
    assert "if (activeSession === session) {" in handoff_block
    assert handoff_block.index("return endIcebreakerRoute(session, 'icebreaker_handoff');") < handoff_block.index(
        "activeSession = null;"
    )


def test_icebreaker_unload_ends_active_route_without_completing_day():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function endIcebreakerRouteOnPageExit(reason)" in runtime
    assert "navigator.sendBeacon" in runtime
    assert "keepalive: true" in runtime
    assert "window.addEventListener('pagehide', function () {" in runtime
    assert "window.addEventListener('beforeunload', function () {" in runtime
    assert "window.addEventListener('unload', function () {" in runtime
    assert "endIcebreakerRouteOnPageExit('icebreaker_pagehide')" in runtime
    assert "endIcebreakerRouteOnPageExit('icebreaker_beforeunload')" in runtime
    assert "endIcebreakerRouteOnPageExit('icebreaker_unload')" in runtime
    assert "icebreaker_visibility_hidden" not in runtime
    assert "document.addEventListener('visibilitychange'" not in runtime

    cleanup_block = runtime.split("function endIcebreakerRouteOnPageExit(reason)", 1)[1].split(
        "function loadScripts()",
        1,
    )[0]
    assert "markDay(" not in cleanup_block
    assert "completed" not in cleanup_block


def test_icebreaker_waits_long_enough_for_react_chat_host():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "waitForChatHost(30000)" in runtime


def test_icebreaker_defers_while_home_tutorial_is_active():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function isIcebreakerBlockerVisible(el)" in runtime
    assert "function hasVisibleTutorialBlocker(selectors)" in runtime
    assert "function isDay1SystrayIntroBlockingIcebreaker()" in runtime
    assert "function isTutorialBlockingIcebreaker()" in runtime
    assert "window.isInTutorial" in runtime
    assert "manager.isTutorialRunning" in runtime
    assert "manager._teardownPromise" in runtime
    assert "neko-day1-systray-intro-open" in runtime
    assert "#neko-day1-systray-intro-modal" in runtime
    assert ".neko-day1-systray-intro-modal" in runtime
    assert "startFromEndStateWhenTutorialIdle" in runtime
    assert "TUTORIAL_IDLE_RETRY_MS" in runtime
    assert "if (isTutorialBlockingIcebreaker())" in runtime
    assert "window.addEventListener('neko:day1-systray-intro-closed'" in runtime
    assert "return false;" in runtime
    assert "getEndStateTriggerDeadline(endState)" in runtime
    assert "retryCount >= TUTORIAL_IDLE_MAX_RETRIES" not in runtime


def test_icebreaker_ignores_hidden_tutorial_dom_after_teardown():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    assert "if (!el || el.hidden) return false" in runtime
    assert "style.display === 'none'" in runtime
    assert "style.visibility === 'hidden'" in runtime
    assert "style.opacity === '0'" in runtime
    assert "return !rect || rect.width > 0 || rect.height > 0" in runtime
    assert "if (isIcebreakerBlockerVisible(nodes[j])) return true" in runtime
    assert "return !!document.querySelector([" not in runtime


def test_icebreaker_tutorial_end_events_start_from_explicit_event_state():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"function handleGuideEndEvent\(event\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert "startFromEndState(resolveLatestEndState(detail, eventType))" not in body
    assert "var endState = resolveLatestEndState(detail, eventType);" in body
    assert "var pendingDay = markPendingStartFromEndState(endState);" in body
    assert "attemptStartFromGuideEndState(endState, pendingDay)" in body


def test_day1_systray_intro_close_releases_icebreaker_and_desktop_passthrough():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    manager = UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")

    assert "pendingGuideEndState = endState;" in runtime
    assert "attemptStartFromGuideEndState(pendingGuideEndState" in runtime
    assert "window.addEventListener('neko:day1-systray-intro-closed'" in runtime
    assert "window.dispatchEvent(new CustomEvent('neko:day1-systray-intro-closed'" in manager
    assert "document.body.classList.remove('neko-day1-systray-intro-open')" in manager


def test_icebreaker_keeps_pending_start_while_day1_systray_intro_is_open():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"function startFromEndStateWhenTutorialIdle\(endState\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert "if (isTutorialBlockingIcebreaker())" in body
    assert (
        "!isDay1SystrayIntroBlockingIcebreaker() && Date.now() >= getEndStateTriggerDeadline(endState)"
        in body
    )
    assert body.index("!isDay1SystrayIntroBlockingIcebreaker()") < body.index(
        "window.setTimeout(resolve, TUTORIAL_IDLE_RETRY_MS)"
    )


def test_icebreaker_deferred_start_promise_cleanup_has_no_unreachable_rejection_handler():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"function attemptStartFromGuideEndState\(endState, pendingDay\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert "}).catch(function (error) {" in body
    assert "}).then(function (started) {" in body
    assert "}, function (error) {" not in body
    assert "throw error;" not in body


def test_yui_guide_bridge_timestamp_helper_exists_for_cursor_relay():
    interpage = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "function getYuiGuideBridgeMessageTimestamp(message)" in interpage
    assert "timestamp: getYuiGuideBridgeMessageTimestamp(message)" in interpage
    assert "getYuiGuideBridgeMessageTimestamp is not defined" not in interpage


def test_icebreaker_does_not_bootstrap_from_persisted_end_state_on_cold_start():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"function bootstrapFromRecentEndState\(\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert "resolveRecentPersistedEndState" not in body
    assert "window.avatarFloatingGuideEndState" not in body
    assert "startFromEndStateWhenTutorialIdle" not in body


def test_icebreaker_avatar_guide_event_day_wins_over_stale_global_end_state():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"function resolveLatestEndState\(detail, eventType\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert body.index("synthesizeEndStateFromEvent(eventType, normalizedDetail)") < body.index(
        "window.avatarFloatingGuideEndState"
    )


def test_home_tutorial_release_events_carry_current_avatar_round_end_state():
    tutorial_manager = UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    reset_runtime = (ROOT / "static" / "tutorial" / "avatar" / "floating-guide-reset.js").read_text(encoding="utf-8")

    assert "avatarFloatingEndState = recordAvatarFloatingGuideEndState(" in tutorial_manager
    assert "day: avatarFloatingEndState ? avatarFloatingEndState.day : undefined" in tutorial_manager
    assert "endState: avatarFloatingEndState" in tutorial_manager
    assert "neko:avatar-floating-guide-skip" in tutorial_manager
    assert "neko:avatar-floating-guide-complete" in tutorial_manager
    assert "day: avatarFloatingEndState.day" in tutorial_manager
    assert "lastEndState" in tutorial_manager
    assert "lastEndState" in reset_runtime
    assert "state.lastEndState" in reset_runtime
    assert "state.lastEndState" in runtime

    generic_tutorial_branch = re.search(
        r"eventType === 'neko:tutorial-skipped'.*?outcome = 'skip';",
        runtime,
        re.DOTALL,
    )
    assert generic_tutorial_branch is not None
    assert "day = 1" not in generic_tutorial_branch.group(0)


def test_avatar_floating_angry_exit_skip_event_preserves_raw_end_state():
    tutorial_manager = UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    mark_match = re.search(
        r"markAvatarFloatingGuideRoundOutcome\(day, outcome, rawReason = outcome\) \{(?P<body>.*?)\n    \}",
        tutorial_manager,
        re.DOTALL,
    )
    assert mark_match is not None
    mark_body = mark_match.group("body")
    assert "const normalizedRawReason = typeof rawReason === 'string' && rawReason.trim()" in mark_body
    assert "rawReason: normalizedRawReason" in mark_body
    assert "isAngryExit: normalizedRawReason === 'angry_exit'" in mark_body
    assert "detail: { day: round, state, endState: state.lastEndState }" in mark_body

    assert re.search(
        r"markAvatarFloatingGuideRoundOutcome\(\s*avatarFloatingRound,\s*endMeta\.reason,\s*endMeta\.rawReason\s*\)",
        tutorial_manager,
    )
    assert re.search(
        r"markAvatarFloatingGuideRoundOutcome\(\s*1,\s*endMeta\.reason,\s*endMeta\.rawReason\s*\)",
        tutorial_manager,
    )

    synthesize_match = re.search(
        r"function synthesizeEndStateFromEvent\(eventType, detail\) \{(?P<body>.*?)\n    \}",
        runtime,
        re.DOTALL,
    )
    assert synthesize_match is not None
    synthesize_body = synthesize_match.group("body")
    assert "var rawReason = String(normalizedDetail.rawReason || normalizedDetail.reason || outcome || '')" in synthesize_body
    assert "isAngryExit: rawReason === 'angry_exit'" in synthesize_body


def test_icebreaker_uses_broadcast_channel_for_desktop_chat_window():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "broadcastIcebreakerAppendMessage" in runtime
    assert "broadcastIcebreakerChoicePrompt" in runtime
    assert "broadcastIcebreakerClearChoicePrompt" in runtime
    assert "shouldRenderIcebreakerOnLocalChatHost" in runtime
    assert "window.__NEKO_MULTI_WINDOW__ === true" in runtime
    assert "!/^\\/chat(?:\\/|$)/.test(path)" in runtime
    assert "if (!shouldRenderIcebreakerOnLocalChatHost())" in runtime
    assert "window.appInterpage" in runtime
    assert "action: 'icebreaker_append_chat_message'" in runtime
    assert "action: 'icebreaker_set_choice_prompt'" in runtime
    assert "action: 'icebreaker_clear_choice_prompt'" in runtime
    assert "lanlan_name: resolveLanlanName()" in runtime

    assert "handleIcebreakerBridgeData" in interpage
    assert "function isIcebreakerBridgeForCurrentLanlan(data)" in interpage
    assert "if (!isIcebreakerBridgeForCurrentLanlan(data)) return false;" in interpage
    assert "case 'icebreaker_append_chat_message'" in interpage
    assert "case 'icebreaker_set_choice_prompt'" in interpage
    assert "case 'icebreaker_clear_choice_prompt'" in interpage
    assert "case 'icebreaker_clear_choice_prompt_source'" in interpage
    assert "appendIcebreakerChatMessage(data.message)" in interpage
    assert "setIcebreakerChoicePromptFromBroadcast(data.prompt)" in interpage
    assert "clearIcebreakerChoicePromptFromBroadcast(data.sessionId)" in interpage
    assert "clearIcebreakerChoicePromptSourceFromBroadcast(data.source, data.reason)" in interpage
    icebreaker_flush_block = interpage.split("function flushPendingIcebreakerBridgeActions()", 1)[1].split(
        "function appendIcebreakerChatMessage",
        1,
    )[0]
    assert "shouldOpenHost = true" in icebreaker_flush_block
    assert "host.openWindow()" in icebreaker_flush_block
    assert "action.source === 'new_user_icebreaker'" in icebreaker_flush_block
    assert "case 'icebreaker_choice_selected'" in interpage
    assert "postIcebreakerBridgeEvent('icebreaker_choice_selected'" in interpage
    assert "case 'icebreaker_free_text_submitted'" in interpage
    assert "postIcebreakerBridgeEvent('icebreaker_free_text_submitted'" in interpage
    assert "action: 'icebreaker_clear_choice_prompt_source'" in runtime
    assert "window.addEventListener('neko:new-user-icebreaker-reset'" in runtime
    assert "nekoBroadcastChannel.postMessage(message)" in interpage
    assert "postInterpageMessage(message)" not in interpage


def test_icebreaker_desktop_bridge_has_storage_fallback():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "ICEBREAKER_BRIDGE_STORAGE_KEY" in runtime
    assert "localStorage.setItem(ICEBREAKER_BRIDGE_STORAGE_KEY" in runtime
    assert "localStorage.removeItem(ICEBREAKER_BRIDGE_STORAGE_KEY)" in runtime

    assert "ICEBREAKER_BRIDGE_STORAGE_KEY" in interpage
    assert "postIcebreakerBridgeEvent" in interpage
    assert "handleIcebreakerStorageBridgeEvent" in interpage
    assert "yuiGuideInterpageResources.addEventListener(window, 'storage', handleIcebreakerStorageBridgeEvent)" in interpage


def test_icebreaker_source_clear_bridge_cannot_clear_non_icebreaker_prompt():
    interpage = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    source_clear_block = interpage.split("function clearIcebreakerChoicePromptSourceFromBroadcast(source, reason)", 1)[1].split(
        "function getIcebreakerMessageText",
        1,
    )[0]

    assert "String(source || '') !== 'new_user_icebreaker'" in source_clear_block
    assert "mini_game_invite" not in source_clear_block


def test_icebreaker_page_exit_clears_choice_prompt_before_route_end():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    page_exit_block = runtime.split("function endIcebreakerRouteOnPageExit(reason)", 1)[1].split(
        "var body = {",
        1,
    )[0]

    assert "clearChoicePrompt();" in page_exit_block
    assert page_exit_block.index("clearChoicePrompt();") < page_exit_block.index("session.routeEnded = true;")


def test_yui_guide_chat_bridge_has_storage_queue_fallback():
    director = (ROOT / "static" / "tutorial" / "yui-guide" / "director.js").read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY" in director
    assert "enqueueYuiGuideChatBridgeMessage" in director
    assert "postYuiGuideChatBridgeMessage" in director
    assert "action: 'yui_guide_append_chat_message'" in director
    assert "action: 'yui_guide_update_chat_message'" in director
    assert "action: 'yui_guide_clear_chat_messages'" in director

    assert "YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY" in interpage
    assert "drainPendingYuiGuideChatBridgeQueue" in interpage
    assert "bindStandaloneChatIdleActivityRelay();" in interpage
    assert "drainPendingYuiGuideChatBridgeQueue();" in interpage
    assert "handleYuiGuideChatBridgeStorageEvent" in interpage
    assert "yuiGuideInterpageResources.addEventListener(window, 'storage', handleYuiGuideChatBridgeStorageEvent)" in interpage
    assert "clearYuiGuideChatMessages" in interpage
    assert "case 'yui_guide_clear_chat_messages':" in interpage


def test_yui_guide_native_relay_uses_defined_chat_helpers():
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")
    relay_block = interpage.split("function handleYuiGuideRelayedMessage(message)", 1)[1].split(
        "yuiGuideInterpageResources.addEventListener(window, 'neko:tutorial-overlay-relay'",
        1,
    )[0]

    assert "function ensureYuiGuideExternalChatExpanded()" in interpage
    assert "applyYuiGuideChatInputLocked(message.locked === true, message.reason || '')" in relay_block
    assert "applyYuiGuideAvatarToolMenuOpen(message.open === true, message.reason || '')" in relay_block
    assert "applyYuiGuideCompactHistoryOpen(message.open === true, message.reason || '')" in relay_block
    assert "applyYuiGuideCompactToolFanOpen(message.open === true, message.reason || '')" in relay_block
    assert "applyYuiGuideCompactToolWheelRotate(message)" in relay_block
    assert "applyYuiGuideCompactToolWheelIndex(message)" in relay_block
    assert "setYuiGuideChatInputLocked(" not in relay_block
    assert "setYuiGuideAvatarToolMenuOpen(" not in relay_block
    assert "setYuiGuideCompactHistoryOpen(" not in relay_block
    assert "setYuiGuideCompactToolFanOpen(" not in relay_block
    assert "rotateYuiGuideCompactToolWheel(" not in relay_block
    assert "setYuiGuideCompactToolWheelIndex(" not in relay_block


def test_icebreaker_free_text_uses_llm_interpreter_before_static_fallback():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    free_text_runtime = ICEBREAKER_FREE_TEXT_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "handleFreeText" in runtime
    assert "interpretFreeTextWithLlm" in runtime
    assert "FREE_TEXT_HISTORY_LIMIT" in free_text_runtime
    assert "TOPIC_ON_TOPIC" in free_text_runtime
    assert "TOPIC_SOFT_DERAIL" in free_text_runtime
    assert "TOPIC_HARD_EXIT" in free_text_runtime
    assert "freeTextRuntimeStateByKey" in free_text_runtime
    assert "function createRuntimeStateStore()" in free_text_runtime
    assert "clearForSession: clearForSession" in free_text_runtime
    assert "getRecentTurns: getRecentTurns" in free_text_runtime
    assert "recordTurn: recordTurn" in free_text_runtime
    assert "getDerailStreak: getDerailStreak" in free_text_runtime
    assert "setDerailStreak: setDerailStreak" in free_text_runtime
    assert "freeTextRuntime.createRuntimeStateStore()" in runtime
    assert "clearFreeTextRuntimeStateForSession(session)" in runtime
    assert "getRecentFreeTextTurns(session, nodeId)" in runtime
    assert "recordFreeTextTurn(session, {" in runtime
    assert "getFreeTextDerailStreak(session, nodeId)" in runtime
    assert "setFreeTextDerailStreak(session, nodeId, 0)" in runtime
    assert "free_text_derail_streak: getFreeTextDerailStreak(session, bodyNodeId)" in runtime
    assert "recent_free_text_turns: getRecentFreeTextTurns(session, bodyNodeId)" in runtime
    assert "session.freeTextTurns" not in runtime
    assert "session.freeTextDerailStreak" not in runtime
    assert "freeTextRuntimeStateByKey" not in runtime
    assert "postIcebreakerJson('/free-text/interpret', body)" in runtime
    assert "data && (data.reason || data.error_code)" in runtime
    assert "throw makeIcebreakerApiError(" in runtime
    assert "data.skipped === 'stale_session'" in runtime
    assert "isIcebreakerRouteInactiveError(error)" in runtime
    assert "applyFreeTextInterpretation" in runtime
    assert "action === 'choose'" in runtime
    assert "decision.topicState === FREE_TEXT_TOPIC_SOFT_DERAIL" in runtime
    assert "getFreeTextDerailStreak(session, nodeId) >= 1" in runtime
    assert "'respond_and_keep_options'" in runtime
    assert "action === 'release'" in runtime
    assert "var releaseText = decision.reply || getText(localeData, fallback.releaseKey);" in runtime
    assert "var releaseText = getText(localeData, fallback.releaseKey) || decision.reply;" not in runtime
    fallback_block = runtime.split("function fallbackFreeTextInterpretation(snapshot)", 1)[1].split(
        "function setChoicePrompt(node, localeData, revealDelayMs)",
        1,
    )[0]
    assert "topicState: FREE_TEXT_TOPIC_SOFT_DERAIL" in fallback_block
    assert "topicState: FREE_TEXT_TOPIC_ON_TOPIC" not in fallback_block
    assert "neko:icebreaker-free-text-submitted" in runtime


def test_icebreaker_free_text_llm_flow_uses_session_snapshot_after_async_append():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    free_text_block = runtime.split("function handleFreeText(detail)", 1)[1].split(
        "function canStartFromEndState",
        1,
    )[0]
    continuation_block = free_text_block.split("}).then(function (message) {", 1)[1]

    assert "var session = activeSession;" in free_text_block
    assert "var day = session.day;" in free_text_block
    assert "var nodeId = session.nodeId;" in free_text_block
    assert "var sessionId = session.sessionId;" in free_text_block
    assert "var localeData = session.localeData;" in free_text_block
    assert "if (!message)" in continuation_block
    assert "if (activeSession !== session) {" in continuation_block
    assert "return null;" in continuation_block
    assert "return interpretFreeTextWithLlm(session, text, {" in continuation_block
    assert "day: day" in continuation_block
    assert "nodeId: nodeId" in continuation_block
    assert "sessionId: sessionId" in continuation_block
    assert "localeData: localeData" in continuation_block
    assert "return applyFreeTextInterpretation(session, interpretation, {" in continuation_block
    assert "setChoicePrompt(currentNode, localeData, computeChoicePromptRevealDelay(replyText))" in runtime
    assert "if (result === false) {" in runtime
    assert "setChoicePrompt(currentNode, localeData);" in runtime
    assert "dispatchIcebreakerEnded('free_text_release');" in runtime
    assert "return Promise.resolve().then(function () {" in runtime
    assert "return speakLine(releaseText, releaseVoiceKey);" in runtime
    assert "}).catch(function () {}).then(function () {" in runtime
    assert "didAppendRelease" in runtime
    assert "var releaseAppend = releaseText ? appendAssistantChatMessage(releaseText, {" in runtime
    assert "}) : Promise.resolve(activeSession === session);" in runtime
    assert "if (!didAppendRelease || activeSession !== session) return false;" in runtime
    assert runtime.index("return speakLine(releaseText, releaseVoiceKey);") < runtime.index(
        "dispatchIcebreakerEnded('free_text_release');"
    )
    assert "Number(session.offTopicCount || 0) >= 1" not in free_text_block
    assert "fallback.redirectKey" not in free_text_block
    assert "activeSession.localeData" not in continuation_block
    assert "activeSession.day" not in continuation_block
    assert "activeSession.nodeId" not in continuation_block
    assert "activeSession.sessionId" not in continuation_block


def test_icebreaker_free_text_interpreter_router_has_prompt_watermark_and_limited_actions():
    icebreaker_router = ICEBREAKER_ROUTER_PATH.read_text(encoding="utf-8")
    prompts = ICEBREAKER_PROMPTS_PATH.read_text(encoding="utf-8")
    free_text_utils = ICEBREAKER_FREE_TEXT_UTILS_PATH.read_text(encoding="utf-8")

    assert '@router.post("/free-text/interpret")' in icebreaker_router
    assert "create_chat_llm_async" in icebreaker_router
    assert "build_icebreaker_free_text_prompts" in icebreaker_router
    assert "parse_icebreaker_free_text_decision" in icebreaker_router
    assert "SystemMessage(content=system_prompt)" in icebreaker_router
    assert "HumanMessage(content=user_prompt)" in icebreaker_router
    assert "# noqa: LLM_INPUT_BUDGET" not in icebreaker_router
    assert "ICEBREAKER_FREE_TEXT_WATERMARK" in prompts
    assert "ICEBREAKER_FREE_TEXT_WATERMARK" in free_text_utils
    assert "======以上为新用户破冰插话解释器系统提示======" in prompts
    assert "[:800]" not in prompts
    assert "[:200]" not in free_text_utils
    assert "[:ICEBREAKER_FREE_TEXT_HISTORY_TEXT_LENGTH]" not in free_text_utils
    assert "ICEBREAKER_FREE_TEXT_ACTIONS = {" in free_text_utils
    assert '"choose"' in free_text_utils
    assert '"respond_and_keep_options"' in free_text_utils
    assert '"release"' in free_text_utils
    assert "clean_icebreaker_interpreter_reply" in free_text_utils
    assert "route_not_active" in icebreaker_router


def test_icebreaker_start_dedupes_pending_tutorial_end_triggers():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    start_block = runtime.split("function startForDay(day, options)", 1)[1].split(
        "function startFromEndState(endState)",
        1,
    )[0]

    assert "var pendingStartDay = '';" in runtime
    assert "if (!force && pendingStartDay === dayKey) return Promise.resolve(false);" in start_block
    assert "pendingStartDay = dayKey;" in start_block
    assert "clearPendingStartDay(dayKey);" in start_block
    assert start_block.index("pendingStartDay = dayKey;") < start_block.index("return Promise.all")
    assert start_block.index("clearPendingStartDay(dayKey);") > start_block.index("return startIcebreakerRoute(nextSession)")


def test_home_tutorial_reset_also_resets_day1_icebreaker_state():
    reset_source = (ROOT / "static" / "tutorial" / "avatar" / "floating-guide-reset.js").read_text(encoding="utf-8")
    memory_browser_source = (ROOT / "static" / "js" / "memory_browser.js").read_text(encoding="utf-8")

    assert "neko.new_user_icebreaker.v1" in reset_source
    assert "resetIcebreakerDay(round)" in reset_source
    assert "delete store.days[key]" in reset_source
    assert "function resetAllIcebreakerDays()" in reset_source
    assert "resetAllAvatarFloatingGuideDays" in reset_source
    assert "state.completedRounds = []" in reset_source
    assert "state.skippedRounds = []" in reset_source
    assert "selection.pageKey === 'all'" in memory_browser_source
    assert "resetAllAvatarFloatingGuideDays({" in memory_browser_source
    home_all_block = memory_browser_source.split("if (selection.type === 'home-all') {", 1)[1].split(
        "if (selection.type === 'page'",
        1,
    )[0]
    home_day_block = memory_browser_source.split("if (selection.type === 'home-day') {", 1)[1].split(
        "if (selection.type === 'home-all'",
        1,
    )[0]
    prompt_reset_helper = memory_browser_source.split("async function resetHomeTutorialPromptState(", 1)[1].split(
        "async function resetSelectedTutorial()",
        1,
    )[0]
    assert "resetHomeTutorialPromptState('memory_browser_home_day_reset')" in home_day_block
    assert "resetHomeTutorialPromptState('memory_browser_home_all_reset')" in home_all_block
    assert "window.universalTutorialManager.resetHomeTutorialPromptState(" in prompt_reset_helper
    assert "resetHomeTutorialPromptStateViaApi(" in prompt_reset_helper
    assert "'/api/tutorial-prompt/reset'" in memory_browser_source


def test_react_chat_fallback_sort_key_stays_after_existing_timestamped_messages():
    chat_host = CHAT_HOST_PATH.read_text(encoding="utf-8")

    assert "getNextAppendSortKey" in chat_host
    assert "maxExistingSortKey" in chat_host
    assert "Math.max(_sortKeySeq, maxExistingSortKey + 1, Date.now())" in chat_host


def test_icebreaker_messages_use_monotonic_sort_keys_not_timestamp_ties():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "icebreakerSortKeySeq" in runtime
    assert "nextIcebreakerSortKey" in runtime
    assert "sortKey: nextIcebreakerSortKey()" in runtime
    assert "sortKey: Date.now()" not in runtime


def test_icebreaker_bridge_events_use_monotonic_timestamps_for_deduping():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "icebreakerBridgeTimestampSeq" in runtime
    assert "nextIcebreakerBridgeTimestamp" in runtime
    assert "timestamp: nextIcebreakerBridgeTimestamp()" in runtime
    assert "timestamp: Date.now()" not in runtime


def test_icebreaker_period_suppresses_only_active_or_recent_icebreaker():
    app_websocket = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    app_proactive = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    for source in (app_websocket, app_proactive):
        assert "NEW_USER_ICEBREAKER_STORAGE_KEY = 'neko.new_user_icebreaker.v1'" in source
        assert "NEW_USER_ICEBREAKER_BLOCKING_WINDOW_MS = 2 * 60 * 60 * 1000" in source
        assert "function isNewUserIcebreakerPeriodActive()" in source
        assert "function isRecentNewUserIcebreakerEntry(entry)" in source
        assert "Date.now() - latest <= NEW_USER_ICEBREAKER_BLOCKING_WINDOW_MS" in source
        assert "isRecentNewUserIcebreakerEntry(entry)" in source
        assert "days['7']" in source

        period_body = re.search(
            r"function isNewUserIcebreakerPeriodActive\(\) \{(?P<body>.*?)\n    \}",
            source,
            flags=re.S,
        ).group("body")
        if source is app_websocket:
            assert "isNewUserIcebreakerActiveForGreeting()" in period_body
            assert "isNewUserIcebreakerStorePeriodActive()" not in period_body
            assert "readNewUserIcebreakerStore()" not in period_body
            store_body = re.search(
                r"function isNewUserIcebreakerStorePeriodActive\(\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "readNewUserIcebreakerStore()" in store_body
            assert "isNewUserIcebreakerEntryBlocking(entry)" in store_body
            active_body = re.search(
                r"function isNewUserIcebreakerActiveForGreeting\(\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "return isNewUserIcebreakerStorePeriodActive();" in active_body
            assert "hasRuntimeState" not in active_body
            assert "return isNewUserIcebreakerActiveForGreeting();" in period_body
            entry_body = re.search(
                r"function isNewUserIcebreakerEntryBlocking\(entry\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "entry.completed !== true" in entry_body
            assert "isRecentNewUserIcebreakerEntry(entry)" in entry_body
            storage_body = store_body + entry_body
        else:
            assert "getActiveSession()" in period_body
            assert "isNewUserIcebreakerEntryBlocking(entry)" in period_body
            entry_body = re.search(
                r"function isNewUserIcebreakerEntryBlocking\(entry\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "entry.completed !== true" in entry_body
            assert "isRecentNewUserIcebreakerEntry(entry)" in entry_body
            retry_body = re.search(
                r"function getNewUserIcebreakerBlockingRetryMs\(\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "entry.completed === true" in retry_body
            delay_body = re.search(
                r"function getNewUserIcebreakerRetryDelayMs\(\) \{(?P<body>.*?)\n    \}",
                source,
                flags=re.S,
            ).group("body")
            assert "entry.completed === true" in delay_body
            assert period_body.index("readNewUserIcebreakerStore()") > period_body.index(
                "getActiveSession()"
            )
            storage_body = period_body + entry_body
        assert "if (!window.newUserIcebreaker" not in period_body
        assert "entry.started === true" not in storage_body
        assert "|| entry.triggeredAt" not in storage_body
        assert "|| entry.updatedAt" not in storage_body

    assert "isNewUserIcebreakerPeriodActive()" in app_proactive
    assert "[ProactiveChat] 新用户破冰期未结束，跳过主动搭话" in app_proactive

    assert "isNewUserIcebreakerBlockingGreeting(S._greetingCheckReason)" in app_websocket
    assert "function isNewUserIcebreakerActiveForGreeting()" in app_websocket
    assert "return isNewUserIcebreakerActiveForGreeting();" in app_websocket
    assert "function isTutorialReleaseGreetingReason(reason)" not in app_websocket
    assert "function markPendingStartFromEndState(endState)" in runtime
    assert "pendingGuideEndStateDay" in runtime
    assert "return !!(activeSession || pendingStartDay || pendingGuideEndStateDay);" in runtime
    assert "window.dispatchEvent(new CustomEvent('neko:new-user-icebreaker-ended'" in runtime


def test_react_chat_assets_use_react_chat_cache_version():
    index_html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    pages_router = (ROOT / "main_routers" / "pages_router.py").read_text(encoding="utf-8")

    react_chat_assets = [
        "/static/react/neko-chat/neko-chat-window.css",
        "/static/react/neko-chat/neko-chat-window.iife.js",
        "/static/app-react-chat-window.js",
        "/static/app-chat-adapter.js",
        "/static/app-buttons.js",
    ]

    for asset in react_chat_assets:
        assert f'{asset}?v={{{{ react_chat_asset_version }}}}' in index_html
        assert f'{asset}?v={{{{ react_chat_asset_version }}}}' in chat_html

    assert pages_router.count('_PROJECT_ROOT / "static/app-interpage.js"') == 1
