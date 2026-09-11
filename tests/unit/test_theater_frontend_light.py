"""验证小剧场迁入 N.E.K.O 胶囊后的静态前端合同。"""  # noqa: DOCSTRING_CJK

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_theater_page_is_numeric_story_selector_only():
    html = _source("templates/theater.html")
    script = _source("static/js/theater_selector.js")

    assert "data-theater-selector-app" in html
    assert "/static/css/theater.css" not in html
    assert "/static/css/theater_selector.css" in html
    assert "/static/js/theater_selector.js" in html
    for element_id in (
        "theater-story-list",
        "theater-detail-background",
        "theater-detail-player",
        "theater-detail-catgirl",
        "theater-start-btn",
        "theater-continue-btn",
        "theater-end-btn",
        "theater-delete-btn",
    ):
        assert f'id="{element_id}"' in html

    assert "/api/theater/free/" not in script
    assert "theater-restart-btn" not in html
    assert "theater-restart-btn" not in script
    assert "RP-Hub" not in html
    assert "free_history" not in script


def test_selector_uses_two_stage_handoff_and_start_replacement():
    script = _source("static/js/theater_selector.js")

    assert "theater:launch-request" in script
    assert "theater:launch-ready" in script
    assert "theater:selector-ready" in script
    assert "theater:post-end" in script
    assert "theater:external-end" in script
    assert "async function endSession()" in script
    assert "开始新的演绎？" in script
    assert "async function beginSession()" in script
    assert "replace_existing: replaceExisting === true" in script
    assert "persistent: true" in script


def test_selector_targets_launch_to_opener_and_covers_host_wait_window():
    """有 opener 时启动请求不能广播给其他本体，等待时间必须覆盖宿主挂载窗口。"""  # noqa: DOCSTRING_CJK

    script = _source("static/js/theater_selector.js")
    post_start = script.index("function postMessage(message, preferOpener)")
    post_end = script.index("function setBusy(", post_start)
    handoff_start = script.index("async function handoff(")
    handoff_end = script.index("async function launchSnapshot(", handoff_start)

    assert "preferOpener === true && opener" in script[post_start:post_end]
    assert "opener.postMessage(payload, window.location.origin); return true;" in script[post_start:post_end]
    assert "postMessage(payload, true);" in script[handoff_start:handoff_end]
    assert "}, 40000);" in script[handoff_start:handoff_end]


def test_selector_binds_delayed_confirmations_to_original_selection():
    """排队确认框只能操作弹出时对应的剧本和 Session。"""  # noqa: DOCSTRING_CJK

    script = _source("static/js/theater_selector.js")
    end_block = script[
        script.index("async function endSession()"):
        script.index("async function beginSession()")
    ]
    begin_block = script[
        script.index("async function beginSession()"):
        script.index("async function deleteStory()")
    ]
    forget_block = script[
        script.index("async function forgetStoryMemory()"):
        script.index("async function importStory(")
    ]

    assert "selectedSessionMatches(targetStoryId, targetSessionId)" in end_block
    assert "story_id: targetStoryId" in end_block
    assert "selectedSessionMatches(targetStoryId, targetSessionId)" in begin_block
    assert "sessionKind() === targetSessionKind" in begin_block
    assert "targetCharacterEpoch !== characterEpoch" in forget_block
    assert "state.storyId !== targetStoryId" in forget_block
    assert "var targetCharacterId = state.characterId;" in forget_block
    assert "character_id: targetCharacterId" in forget_block


def test_capsule_runtime_separates_narration_and_dialogue_tts():
    runtime = _source("static/app/app-theater-runtime.js")
    buttons = _source("static/app/app-buttons.js")
    host_api = _source("static/app/app-react-chat-window/resize-drag-and-api.js")
    host_messages = _source(
        "static/app/app-react-chat-window/message-bundle-actions-and-prompts.js"
    )

    assert "/session/speak-block" in runtime
    assert "if (block.type === 'dialogue')" in runtime
    assert "typeBlock(historyId, block, token)" in runtime
    assert "playDialogue(group, item.block, item.blockIndex, revision, token)" in runtime
    assert "if (speechPromise && !await speechPromise) return" in runtime
    assert "theaterPresentation" in runtime
    assert "window.nekoTheaterRuntime" in runtime
    assert "var theaterRuntime = window.nekoTheaterRuntime" in buttons
    assert "theaterRuntime.isActive()" in buttons
    assert "handleComposerSubmit" in buttons
    assert "setOnTheaterSubmit" in runtime
    assert "submit(text, 'freeform')" in runtime
    assert "submit(text, 'suggestion')" in runtime
    assert "input_source: normalizedInputSource" in runtime
    assert "setOnTheaterSuggestedInputSelect(submitSuggestedFromHost)" in runtime
    assert "setOnTheaterSubmit" in host_api
    assert "I.handleTheaterSubmit" in host_messages
    theater_branch = buttons[buttons.index("if (theaterRuntime &&"):]
    assert theater_branch.index("if (hasExtraImages)") < theater_branch.index(
        "theaterRuntime.handleComposerSubmit(text)"
    )
    assert "if (hasScreenshots || hasExtraImages)" not in theater_branch[:600]


def test_capsule_runtime_restores_the_pre_theater_chat_surface():
    """小剧场退出时必须恢复进入前的聊天界面形态。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")

    assert "chatSurfaceModeRestore: null" in runtime
    assert "chatHost.getChatSurfaceMode()" in runtime
    assert "chatHost.setChatSurfaceMode(mode)" in runtime
    render = runtime.index("function render()")
    render_capture = runtime.index("captureChatSurfaceMode(chatHost);", render)
    force_compact = runtime.index("chatSurfaceMode: 'compact'", render)
    launch = runtime.index("async function performLaunch(message, launchToken)")
    capture = runtime.index("captureChatSurfaceMode(chatHost);", launch)
    launch_render = runtime.index("render();", capture)
    clear = runtime.index("function clear(reason)")
    restore = runtime.index("restoreChatSurfaceMode(chatHost);", clear)
    final_view = runtime.index("chatHost.setViewProps({", restore)

    assert render < render_capture < force_compact < launch < capture < launch_render < clear < restore < final_view


def test_capsule_runtime_pointer_only_survives_current_app_lifecycle():
    """演绎指针使用会话存储，完整退出程序后不得自动恢复小剧场。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")

    assert "window.sessionStorage.setItem(POINTER_KEY" in runtime
    assert "window.sessionStorage.getItem(POINTER_KEY)" in runtime
    assert "window.sessionStorage.removeItem(POINTER_KEY)" in runtime
    assert "window.localStorage.setItem(POINTER_KEY" not in runtime
    assert "window.localStorage.getItem(POINTER_KEY)" not in runtime


def test_desktop_pet_runtime_routes_theater_to_the_compact_chat_host():
    """桌面 Pet 不得在不可见宿主播放正文，启动只能交给紧凑胶囊。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")

    assert "function desktopRuntimeRole()" in runtime
    assert "function relayLaunchToDesktopChat(message)" in runtime
    assert "runtime_host_kind: 'compact'" in runtime
    assert "if (role === 'pet')" in runtime
    assert "if (role && role !== 'compact') return;" in runtime


def test_selector_does_not_publish_stale_story_after_archive_load():
    """异步归档返回后必须再次复验当前选择，再更新详情和地址栏。"""  # noqa: DOCSTRING_CJK

    script = _source("static/js/theater_selector.js")
    selection = script.index("async function selectStory(")
    active_await = script.index("result = await requestJson(api.active", selection)
    active_catch = script.index("} catch (_) {", active_await)
    active_failure = script.index("setStatus('theater.failed', '出错了');", active_catch)
    archive_await = script.index(
        "await loadMemoryArchives(storyId, selectionCharacterEpoch);",
        selection,
    )
    archive_catch = script.index("} catch (_) {", archive_await)
    archive_failure = script.index("setStatus('theater.failed', '出错了');", archive_catch)
    recheck = script.index(
        "if (state.storyId !== storyId || selectionCharacterEpoch !== characterEpoch) return;",
        archive_await,
    )
    replace_url = script.index("window.history.replaceState", recheck)

    assert active_await < active_catch < active_failure < archive_await
    assert archive_await < archive_catch < archive_failure < recheck < replace_url


def test_capsule_runtime_projects_narration_display_kind_from_performance_phase():
    """动作或场景由 performance 位置确定，前端不能读取正文关键词猜测。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    assert "if (sceneNarration) blocks.push" in runtime
    assert "sceneNarration === LEGACY_EMPTY_TRANSITION_BRIDGE" in runtime
    schema = _source("frontend/react-neko-chat/src/message-schema.ts")
    app = _source("frontend/react-neko-chat/src/App.tsx")

    assert "function narrationDisplayKind(phase)" in runtime
    assert "phase === 'ordinary' || phase === 'source_response'" in runtime
    assert "performance.transition_delivered ? 'transition_bridge' : 'ordinary'" in runtime
    assert "performanceHistoryGroups(session.opening_performance, 'opening')" in runtime
    assert "displayKind: z.enum(['action', 'scene']).optional()" in schema
    assert "function formatTheaterBlockText(" in app
    assert "`（${normalized}）`" in app


def test_capsule_runtime_streams_ordinary_performance_into_one_history_bubble():
    """普通微动作与对白共用猫娘消息，场景旁白仍使用独立气泡。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    schema = _source("frontend/react-neko-chat/src/message-schema.ts")
    app = _source("frontend/react-neko-chat/src/App.tsx")

    assert "function performanceHistoryGroups(performance, fallbackPhase)" in runtime
    assert "function mixedPerformanceBlocks(value, phase)" in runtime
    assert "block.displayText = rawText" in runtime
    assert "group.preserveSpacing ? '' : '\\n'" in runtime
    assert "block.type === 'narration' && block.displayKind === 'scene'" in runtime
    assert "groups.push({ type: 'narration'" in runtime
    assert "TYPEWRITER_INTERVAL_MS" in runtime
    assert "group.type === 'narration' ? 'scene' : undefined" in runtime
    assert "'streaming'" in runtime
    assert "completedEntry.status = 'sent'" in runtime
    assert "status: z.enum(['streaming', 'sent']).optional()" in schema
    assert "const compactMessagePreview = theaterActive" in app
    assert "const compactMessagePreview = theaterActive\n    ? null" in app
    assert "status: entry.status" in app


def test_capsule_runtime_shows_player_action_before_waiting_for_actor():
    """推荐输入和手动输入都应先进入历史，再等待模型返回。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    submit_start = runtime.index("async function submit(text, inputSource)")
    optimistic_history = runtime.index(
        "state.history.push(historyEntry(optimisticHistoryId",
        submit_start,
    )
    actor_request = runtime.index("result = await requestJson(api.input", submit_start)

    assert optimistic_history < actor_request
    assert "state.history = state.history.filter(function (entry)" in runtime
    assert "historyEntry(optimisticHistoryId, 'player_action', message, state.playerName)" in runtime
    assert "group.type === 'dialogue' ? state.catgirlName : undefined" in runtime
    assert "participants.player_name" in runtime
    assert "participants.catgirl_name" in runtime


def test_capsule_runtime_discards_turn_response_after_session_switch():
    """旧 Session 的迟到响应不能覆盖新剧本的胶囊状态。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    submit_start = runtime.index("async function submit(text, inputSource)")
    request = runtime.index("result = await requestJson(api.input", submit_start)
    stale_guard = runtime.index("state.storyId !== submittedStoryId", request)
    apply_snapshot = runtime.index("applySnapshot(result);", stale_guard)

    assert "var submittedSessionId = state.sessionId" in runtime[submit_start:request]
    assert "state.pendingTurn.id !== submittedTurnId" in runtime[request:apply_snapshot]
    assert request < stale_guard < apply_snapshot


def test_capsule_runtime_invalidates_pending_launch_when_turn_advances():
    """成功回合必须让此前发起的同 Session 启动快照失效。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    submit_start = runtime.index("async function submit(text, inputSource)")
    successful_turn = runtime.index("state.pendingTurn = null;", submit_start)
    invalidate_launch = runtime.index("launchEpoch += 1;", successful_turn)
    apply_snapshot = runtime.index("applySnapshot(result);", successful_turn)

    assert successful_turn < invalidate_launch < apply_snapshot


def test_theater_character_switch_invalidates_restore_and_selector_requests():
    """切换猫娘时，尚未完成的本体恢复和选剧页请求都不能发布旧角色状态。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    selector = _source("static/js/theater_selector.js")
    runtime_switch = runtime.index("else if (message.action === 'catgirl_switched')")
    runtime_invalidate = runtime.index("launchEpoch += 1;", runtime_switch)
    runtime_active_guard = runtime.index("if (state.active)", runtime_invalidate)
    selector_switch = selector.index("else if (message.action === 'catgirl_switched')")

    assert runtime_switch < runtime_invalidate < runtime_active_guard
    assert "var characterEpoch = 0;" in selector
    assert "selectionCharacterEpoch !== characterEpoch" in selector
    assert "loadCharacterEpoch !== characterEpoch" in selector
    assert "characterEpoch += 1;" in selector[selector_switch:]
    assert "state.characterId = '';" in selector[selector_switch:]
    assert "state.session = null;" in selector[selector_switch:]
    assert "loadStories(selectedStoryId, switchCharacterEpoch)" in selector[selector_switch:]
    stories_start = selector.index("async function loadStories(")
    stories_response = selector.index("var result = await requestJson(api.stories);", stories_start)
    stories_guard = selector.index("storiesCharacterEpoch !== characterEpoch", stories_response)
    stories_publish = selector.index("state.stories = result.stories;", stories_guard)
    character_publish = selector.index("state.characterId = String(result.character_id || '');", stories_publish)

    assert stories_response < stories_guard < stories_publish < character_publish


def test_capsule_runtime_revalidates_conflict_refresh_ownership():
    """revision 冲突刷新不能用旧 Session 快照覆盖较新的启动。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    submit_start = runtime.index("async function submit(text, inputSource)")
    conflict_start = runtime.index("result.reason === 'numeric_base_revision_mismatch'", submit_start)
    refresh = runtime.index("refreshed = await requestJson(", conflict_start)
    refresh_try = runtime.rindex("try {", conflict_start, refresh)
    refresh_catch = runtime.index("} catch (_)", refresh)
    refresh_error = runtime.index("state.errorMessage = t('theater.inputFailed'", refresh_catch)
    ownership_guard = runtime.index(
        "isCurrentLaunch(submittedLaunchEpoch, submittedStoryId, submittedSessionId)",
        refresh_error,
    )
    pending_guard = runtime.index("state.pendingTurn.id === submittedTurnId", ownership_guard)
    apply_snapshot = runtime.index("applySnapshot(refreshed);", pending_guard)

    assert "var submittedLaunchEpoch = launchEpoch;" in runtime[submit_start:conflict_start]
    assert refresh_try < refresh < refresh_catch < refresh_error < ownership_guard
    assert ownership_guard < pending_guard < apply_snapshot
    assert "chatHost.setOnTheaterSubmit(submitFromHost);" in runtime
    assert "void submit(text, 'freeform').catch" in runtime
    assert "void submit(text, 'suggestion').catch" in runtime


def test_selector_binds_session_start_to_displayed_character():
    """开始和重新开始都必须携带确认时展示角色的稳定 ID。"""  # noqa: DOCSTRING_CJK

    selector = _source("static/js/theater_selector.js")
    start = selector[
        selector.index("async function startSession("):
        selector.index("async function continueSession(")
    ]

    assert "!state.characterId" in start
    assert "var startCharacterId = state.characterId;" in start
    assert "character_id: startCharacterId" in start


def test_selector_reads_saved_performance_through_archive_detail_api():
    """选剧页只能通过身份校验后的详情接口展示完整演绎。"""  # noqa: DOCSTRING_CJK

    selector = _source("static/js/theater_selector.js")

    assert "memoryArchive: '/api/theater-numeric/memory/archive'" in selector
    assert "view.dataset.theaterViewSession" in selector
    assert "async function viewMemoryArchive(summary)" in selector
    assert "api.memoryArchive" in selector
    assert "function formatMemoryArchive(archive)" in selector
    assert "function formatStoredPerformance(entry, attributeLegacy)" in selector
    assert "formatStoredPerformance(opening, false)" in selector
    assert "formatStoredPerformance(turn, true)" in selector
    assert "part.kind === 'action' || part.kind === 'dialogue'" in selector
    assert "singleAction: true" in selector
    assert "transcript: true" in selector


def test_capsule_runtime_reasserts_composer_visibility_without_overwriting_restore_state():
    """剧场每次渲染都保持输入区可见，但只采集一次退出恢复快照。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    start = runtime.index("function claimComposerVisibility(chatHost)")
    end = runtime.index("function restoreComposerVisibility(chatHost)", start)
    claim = runtime[start:end]

    assert "if (!state.active || !chatHost) return;" in claim
    assert "if (!state.composerVisibilityRestore)" in claim
    assert "state.composerVisibilityRestore ||" not in claim
    assert "chatHost.setComposerHidden(false)" in claim
    assert "chatHost.setGoodbyeComposerHidden(false)" in claim


def test_selector_preserves_newer_end_receipt_during_memory_prompt():
    """旧回执归档完成时只能清除自身，并在串行闸门释放后继续处理新回执。"""  # noqa: DOCSTRING_CJK

    selector = _source("static/js/theater_selector.js")
    prompt_start = selector.index("async function maybePromptMemory()")
    prompt_end = selector.index("async function loadStories(", prompt_start)
    prompt = selector[prompt_start:prompt_end]

    assert "if (state.pendingEnd !== receipt)" in prompt
    assert "if (state.pendingEnd === receipt) state.pendingEnd = null;" in prompt
    assert "state.pendingEnd && state.pendingEnd !== receipt" in prompt
    assert "maybePromptMemory().catch" in prompt
    modal_await = prompt.index("var remember = await showModal(")
    current_guard = prompt.index("if (state.pendingEnd !== receipt)", modal_await)
    archive_request = prompt.index("result = await requestJson(", current_guard)

    assert modal_await < current_guard < archive_request


def test_end_receipt_uses_single_transport_and_stable_deduplication():
    """已知选剧页只接收一次结束事实，重复 ready 也不能替换正在处理的回执对象。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    selector = _source("static/js/theater_selector.js")
    send_start = runtime.index("function sendPendingEnd(target)")
    send_end = runtime.index("async function confirmEnd(", send_start)
    send_block = runtime[send_start:send_end]
    handler_start = selector.index("message.action === 'theater:post-end'")
    dedupe = selector.index(
        "state.pendingEnd.end_receipt_id === message.end_receipt_id",
        handler_start,
    )
    publish = selector.index("state.pendingEnd = message;", dedupe)

    assert "if (postDirect(target, directMessage)) return;" in send_block
    assert "postMessage(content);" in send_block
    assert "postMessage(Object.assign" not in send_block
    assert dedupe < publish


def test_capsule_runtime_requires_chat_host_before_launch_ready():
    """React 胶囊未挂载时不能发送启动成功回执或保留不可见运行态。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    launch_start = runtime.index("async function performLaunch(message, launchToken)")
    host_wait = runtime.index("var hostReady = await waitForHost();", launch_start)
    host_guard = runtime.index("if (!hostReady)", host_wait)
    clear_runtime = runtime.index("clear('launch-host-unavailable');", host_guard)
    launch_ready = runtime.index("action: 'theater:launch-ready'", clear_runtime)

    assert host_wait < host_guard < clear_runtime < launch_ready


def test_capsule_runtime_clears_pointer_restore_when_chat_host_is_unavailable():
    """启动指针恢复也必须在 React 胶囊真实可用后才能保留 active 状态。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    restore_start = runtime.index("async function restorePointer()")
    host_wait = runtime.index("var hostReady = await waitForHost();", restore_start)
    host_guard = runtime.index("if (!hostReady)", host_wait)
    clear_runtime = runtime.index("clear('pointer-host-unavailable');", host_guard)
    final_render = runtime.index("render();", clear_runtime)

    assert host_wait < host_guard < clear_runtime < final_render


def test_story_deletion_clears_matching_capsule_runtime():
    """删除剧本成功后，仍在本体展示该剧本的运行态必须同步释放。"""  # noqa: DOCSTRING_CJK

    selector = _source("static/js/theater_selector.js")
    runtime = _source("static/app/app-theater-runtime.js")
    delete_start = selector.index("async function deleteStory()")
    delete_end = selector.index("async function toggleArchivePin(", delete_start)
    delete_block = selector[delete_start:delete_end]
    runtime_handler = runtime.index("message.action === 'theater:story-deleted'")

    assert "var deletedStoryId = state.storyId;" in delete_block
    assert "postMessage({ action: 'theater:story-deleted', story_id: deletedStoryId });" in delete_block
    assert "message.story_id === state.storyId" in runtime[runtime_handler:]
    assert "clear('story-deleted')" in runtime[runtime_handler:]


def test_capsule_runtime_stops_old_audio_before_every_launch():
    """同一 Session 再次启动也必须先停止旧播放协程与语音。"""  # noqa: DOCSTRING_CJK

    runtime = _source("static/app/app-theater-runtime.js")
    launch_start = runtime.index("async function performLaunch(message, launchToken)")
    switch_guard = runtime.index("if (state.active)", launch_start)
    clear_audio = runtime.index("claimAudioPlayback();", switch_guard)
    loading = runtime.index(
        "state.active = true; state.phase = 'loading'",
        switch_guard,
    )

    assert switch_guard < clear_audio < loading


def test_capsule_runtime_confirms_end_without_clearing_on_cancel():
    runtime = _source("static/app/app-theater-runtime.js")
    geometry = _source("static/app/app-react-chat-window/geometry-and-messages.js")
    css = _source("static/css/index.css")
    compact_css = "".join(css.split())

    assert "typeof window.showConfirm === 'function'" in runtime
    assert "if (!confirmed || !isCurrentEndRequest()) return false" in runtime
    assert "if (!isCurrentEndRequest()) return false" in runtime
    assert "cancelText: t('common.cancel', '取消')" in runtime
    assert "skin: 'theater'" in runtime
    assert "onResolve: function (confirmed)" in runtime
    assert "preparedSelector = openSelector()" in runtime
    assert "returnToSelector(receipt, 'user-ended', preparedSelector)" in runtime
    assert "restoreSelectorWindow(selectorTarget)" in runtime
    assert "t('theater.endConnectionFailed'" in runtime
    assert "t('theater.endStateFailed'" in runtime
    assert "return returnToSelector(state.pendingEnd, 'natural-ending-return')" in runtime
    # 旧证据迁移接口已删除；结束动作只提交生命周期状态。
    assert "migrateEvidence" not in runtime
    assert "migrate-evidence" not in runtime
    natural_return = runtime.index("return returnToSelector(state.pendingEnd, 'natural-ending-return')")
    user_return = runtime.index("return returnToSelector(receipt, 'user-ended', preparedSelector)")
    assert natural_return < user_return
    assert "message.action === 'theater:external-end'" in runtime
    assert "modal-dialog-theater" in geometry
    assert ".modal-overlay.modal-overlay-theater{background:transparent!important" in compact_css
    assert ".modal-dialog-theater.modal-btn{min-width:112px;min-height:44px" in compact_css


def test_theater_assets_are_scoped_to_selector_and_main_chat_hosts():
    selector = _source("templates/theater.html")
    index = _source("templates/index.html")
    chat = _source("templates/chat.html")
    transport_path = "/static/js/theater_transport.js"

    assert "/static/js/theater_selector.js" in selector
    assert "/static/app/app-theater-runtime.js" not in selector
    assert "/static/app/app-theater-runtime.js" in index
    assert "/static/app/app-theater-runtime.js" in chat
    assert "/static/js/theater_selector.js" not in index
    assert "/static/js/theater_selector.js" not in chat
    # 三个宿主都必须先加载共享协议，避免业务脚本因依赖缺失而在页面初始化阶段退出。
    assert selector.index(transport_path) < selector.index("/static/js/theater_selector.js")
    assert index.index(transport_path) < index.index("/static/app/app-theater-runtime.js")
    assert chat.index(transport_path) < chat.index("/static/app/app-theater-runtime.js")


def test_theater_transport_owns_shared_request_and_message_protocol():
    """协议号、CSRF 重试和请求 ID 只能在共享层实现一次。"""  # noqa: DOCSTRING_CJK

    transport = _source("static/js/theater_transport.js")
    selector = _source("static/js/theater_selector.js")
    runtime = _source("static/app/app-theater-runtime.js")

    assert "window.nekoTheaterTransport = Object.freeze" in transport
    assert "async function requestJson" in transport
    assert "async function mutationHeaders" in transport
    assert "function createMessage" in transport
    assert "TURN_TIMEOUT_MS = 60000" in transport
    assert "START_TIMEOUT_MS = 45000" in transport
    assert "path === '/api/theater-numeric/session/input'" in transport
    assert "path === '/api/theater-numeric/session/start'" in transport
    assert "function createId" not in selector
    assert "async function requestJson" not in selector
    assert "function createId" not in runtime
    assert "async function requestJson" not in runtime


def test_theater_locales_remain_valid_and_aligned():
    locales = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")
    theater_keys = []
    for locale in locales:
        payload = json.loads(_source(f"static/locales/{locale}.json"))
        theater = payload["theater"]
        theater_keys.append(set(theater))
        for key in (
            "selectorTitle",
            "continueSession",
            "startAgainConfirmTitle",
            "startAgainConfirmBody",
            "rememberPerformanceTitle",
            "memorySaving",
            "inputFailed",
            "endConnectionFailed",
            "endStateFailed",
            "performanceHistory",
            "viewPerformance",
            "performanceArchiveTitle",
            "performanceArchiveOpening",
            "performanceArchiveTurn",
            "performanceArchiveLoadFailed",
        ):
            assert theater[key]
    assert all(keys == theater_keys[0] for keys in theater_keys[1:])


def test_theater_popup_entry_opens_story_selector():
    popup_config = _source("static/avatar/avatar-ui-popup-config.js")
    pngtuber = _source("static/pngtuber-core.js")
    assert popup_config.count("url: '/theater'") == 3
    assert pngtuber.count("url: '/theater'") == 1
    for source in (popup_config, pngtuber):
        assert "url: '/theater-home'" not in source
        assert "url: '/theater-numeric'" not in source
