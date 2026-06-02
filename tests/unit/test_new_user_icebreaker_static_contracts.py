from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = ROOT / "static" / "icebreaker" / "new-user-icebreaker.js"
SCRIPTS_PATH = ROOT / "static" / "icebreaker" / "icebreaker_scripts.json"
LOCALE_PATH = ROOT / "static" / "icebreaker" / "locales" / "zh-CN.json"
LOCALES_DIR = ROOT / "static" / "icebreaker" / "locales"
CHAT_HOST_PATH = ROOT / "static" / "app-react-chat-window.js"
APP_WEBSOCKET_PATH = ROOT / "static" / "app-websocket.js"
APP_PROACTIVE_PATH = ROOT / "static" / "app-proactive.js"
UNIVERSAL_TUTORIAL_MANAGER_PATH = ROOT / "static" / "universal-tutorial-manager.js"
INDEX_TEMPLATE_PATH = ROOT / "templates" / "index.html"
WEBSOCKET_ROUTER_PATH = ROOT / "main_routers" / "websocket_router.py"
GAME_ROUTER_PATH = ROOT / "main_routers" / "game_router.py"
LIVE2D_CORE_PATH = ROOT / "static" / "live2d-core.js"


def assert_icebreaker_script_has_voice_keys_for_every_spoken_line(day_key: str):
    scripts = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))
    locale = json.loads(LOCALE_PATH.read_text(encoding="utf-8"))
    day = scripts["days"][day_key]

    assert len(day["nodes"]) == 31

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

    assert "眼前" in redirect
    assert "选项" in redirect
    assert "刚才那些好玩的功能" not in redirect


def test_icebreaker_runtime_wires_choice_prompt_and_project_tts():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    chat_host = CHAT_HOST_PATH.read_text(encoding="utf-8")
    app_websocket = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    index_html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "new_user_icebreaker" in runtime
    assert "var GAME_TYPE = 'new_user_icebreaker'" in runtime
    assert "'/api/game/' + encodeURIComponent(GAME_TYPE) + '/speak'" in runtime
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
    assert "resolveCurrentAssistantAvatarUrl(message.role) || message.avatarUrl" in chat_host
    assert "refreshAssistantAvatarUrls" in chat_host
    assert "if (!avatarUrl && !shouldClear) return" in chat_host
    assert "window.addEventListener('chat-avatar-preview-updated', refreshAssistantAvatarUrls)" in chat_host
    assert "window.addEventListener('chat-avatar-preview-cleared', refreshAssistantAvatarUrls)" in chat_host
    assert "/static/icebreaker/new-user-icebreaker.js" in index_html


def test_icebreaker_context_append_does_not_touch_shared_websocket_router():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    websocket_router = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")
    game_router = GAME_ROUTER_PATH.read_text(encoding="utf-8")

    assert "appendLlmContext(role, messageText" in runtime
    assert "'/api/game/' + encodeURIComponent(GAME_TYPE) + '/context'" in runtime
    assert "append_icebreaker_context(role, text)" in game_router
    assert '@router.post("/{game_type}/context")' in game_router
    assert "action: 'icebreaker_context_append'" not in runtime
    assert 'action == "icebreaker_context_append"' not in websocket_router


def test_icebreaker_waits_long_enough_for_react_chat_host():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "waitForChatHost(30000)" in runtime


def test_icebreaker_defers_while_home_tutorial_is_active():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function isIcebreakerBlockerVisible(el)" in runtime
    assert "function hasVisibleTutorialBlocker(selectors)" in runtime
    assert "function isTutorialBlockingIcebreaker()" in runtime
    assert "window.isInTutorial" in runtime
    assert "manager.isTutorialRunning" in runtime
    assert "manager._teardownPromise" in runtime
    assert "startFromEndStateWhenTutorialIdle" in runtime
    assert "TUTORIAL_IDLE_RETRY_MS" in runtime
    assert "if (isTutorialBlockingIcebreaker())" in runtime
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
    assert "startFromEndState(resolveLatestEndState(detail, eventType))" in body
    assert "startFromEndStateWhenTutorialIdle(resolveLatestEndState(detail, eventType))" not in body


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
    reset_runtime = (ROOT / "static" / "avatar-floating-guide-reset.js").read_text(encoding="utf-8")

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


def test_icebreaker_uses_broadcast_channel_for_desktop_chat_window():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "broadcastIcebreakerAppendMessage" in runtime
    assert "broadcastIcebreakerChoicePrompt" in runtime
    assert "broadcastIcebreakerClearChoicePrompt" in runtime
    assert "window.appInterpage" in runtime
    assert "action: 'icebreaker_append_chat_message'" in runtime
    assert "action: 'icebreaker_set_choice_prompt'" in runtime
    assert "action: 'icebreaker_clear_choice_prompt'" in runtime

    assert "handleIcebreakerBridgeData" in interpage
    assert "case 'icebreaker_append_chat_message'" in interpage
    assert "case 'icebreaker_set_choice_prompt'" in interpage
    assert "case 'icebreaker_clear_choice_prompt'" in interpage
    assert "appendIcebreakerChatMessage(data.message)" in interpage
    assert "setIcebreakerChoicePromptFromBroadcast(data.prompt)" in interpage
    assert "clearIcebreakerChoicePromptFromBroadcast(data.sessionId)" in interpage
    assert "case 'icebreaker_choice_selected'" in interpage
    assert "postIcebreakerBridgeEvent('icebreaker_choice_selected'" in interpage
    assert "case 'icebreaker_free_text_submitted'" in interpage
    assert "postIcebreakerBridgeEvent('icebreaker_free_text_submitted'" in interpage


def test_icebreaker_desktop_bridge_has_storage_fallback():
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "ICEBREAKER_BRIDGE_STORAGE_KEY" in runtime
    assert "localStorage.setItem(ICEBREAKER_BRIDGE_STORAGE_KEY" in runtime
    assert "localStorage.removeItem(ICEBREAKER_BRIDGE_STORAGE_KEY)" in runtime

    assert "ICEBREAKER_BRIDGE_STORAGE_KEY" in interpage
    assert "postIcebreakerBridgeEvent" in interpage
    assert "handleIcebreakerStorageBridgeEvent" in interpage
    assert "window.addEventListener('storage', handleIcebreakerStorageBridgeEvent)" in interpage


def test_yui_guide_chat_bridge_has_storage_queue_fallback():
    director = (ROOT / "static" / "yui-guide-director.js").read_text(encoding="utf-8")
    interpage = (ROOT / "static" / "app-interpage.js").read_text(encoding="utf-8")

    assert "YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY" in director
    assert "enqueueYuiGuideChatBridgeMessage" in director
    assert "postYuiGuideChatBridgeMessage" in director
    assert "action: 'yui_guide_append_chat_message'" in director
    assert "action: 'yui_guide_update_chat_message'" in director

    assert "YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY" in interpage
    assert "drainPendingYuiGuideChatBridgeQueue" in interpage
    assert "handleYuiGuideChatBridgeStorageEvent" in interpage
    assert "window.addEventListener('storage', handleYuiGuideChatBridgeStorageEvent)" in interpage


def test_icebreaker_free_text_uses_fallback_instead_of_llm():
    scripts = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))
    locale = json.loads(LOCALE_PATH.read_text(encoding="utf-8"))
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    fallback = scripts["days"]["1"]["fallback"]
    assert locale[fallback["redirectKey"]]
    assert locale[fallback["releaseKey"]]
    assert fallback["redirectVoiceKey"]
    assert fallback["releaseVoiceKey"]

    assert "handleFreeText" in runtime
    assert "offTopicCount" in runtime
    assert "fallback.redirectKey" in runtime
    assert "fallback.releaseKey" in runtime
    assert "neko:icebreaker-free-text-submitted" in runtime


def test_home_tutorial_reset_also_resets_day1_icebreaker_state():
    reset_source = (ROOT / "static" / "avatar-floating-guide-reset.js").read_text(encoding="utf-8")

    assert "neko.new_user_icebreaker.v1" in reset_source
    assert "resetIcebreakerDay(round)" in reset_source
    assert "delete store.days[key]" in reset_source


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


def test_icebreaker_period_suppresses_greeting_and_proactive_chat():
    app_websocket = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    app_proactive = APP_PROACTIVE_PATH.read_text(encoding="utf-8")

    for source in (app_websocket, app_proactive):
        assert "NEW_USER_ICEBREAKER_STORAGE_KEY = 'neko.new_user_icebreaker.v1'" in source
        assert "function isNewUserIcebreakerPeriodActive()" in source
        assert "days['7']" in source

    assert "isNewUserIcebreakerPeriodActive()" in app_proactive
    assert "[ProactiveChat] 新用户破冰期未结束，跳过主动搭话" in app_proactive

    assert "isNewUserIcebreakerBlockingGreeting()" in app_websocket
    assert "tutorial-completed" in app_websocket
    assert "tutorial-skipped" in app_websocket


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
        "/static/app-interpage.js",
    ]

    for asset in react_chat_assets:
        assert f'{asset}?v={{{{ react_chat_asset_version }}}}' in index_html
        assert f'{asset}?v={{{{ react_chat_asset_version }}}}' in chat_html

    assert '_PROJECT_ROOT / "static/app-interpage.js"' in pages_router
