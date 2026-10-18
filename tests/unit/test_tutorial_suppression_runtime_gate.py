"""Tutorial suppression is runtime-only: it must never rewrite proactive settings memory.

Design (per maintainer review on PR #3388, following the mini-theater precedent
``lockProactiveChatForTheater``): the ten proactive-chat keys are read by
saveSettings persistence, cross-window storage broadcast, periodic server sync
and the whole CAS/provenance machinery. Any temporary rewrite of those mirrors
gets fossilized as user settings by the next autosave — that was the root cause
of "tutorial ends with every proactive toggle off". Therefore:

1. The two suppression layers (home-tutorial feature controller, avatar reload
   controller) and the director fallback only stop runtimes, register flags and
   dispatch ``neko:home-tutorial-features-suppressed``; they never assign to
   the ten keys, and no snapshot/restore/truth-chain machinery may come back.
2. Runtime behaviour is gated centrally by ``isHomeTutorialFeatureSuppressed``
   (chat trigger, proactive-vision start and per-frame tick, vision-stream
   retain paths) and by the exported
   ``window.isNekoHomeTutorialProactiveSuppressed`` for the settings-driven
   screen-stream retain/acquire sites in app-screen / app-buttons /
   app-audio-capture.
3. The avatar layer's ``proactiveRuntimeHold`` outlives the feature
   controller's end (it clears only after the async model restore), so the
   trigger gate cannot open while the temporary tutorial model is displayed;
   release dispatches the event that re-arms chat scheduling, proactive vision
   for an ongoing recording, and deferred context prompts.
4. ``app-settings.js`` carries no tutorial-specific machinery at all.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME_TUTORIAL_RUNTIME_PATH = ROOT / "static" / "tutorial" / "core" / "home-tutorial-runtime.js"
RELOAD_CONTROLLER_PATH = ROOT / "static" / "tutorial" / "avatar" / "reload-controller.js"
DIRECTOR_CORE_PATH = ROOT / "static" / "tutorial" / "yui-guide" / "director" / "director-core.js"
APP_PROACTIVE_PATH = ROOT / "static" / "app" / "app-proactive.js"
APP_CONTEXT_PROMPT_PATH = ROOT / "static" / "app" / "app-context-prompt.js"
APP_SETTINGS_PATH = ROOT / "static" / "app" / "app-settings.js"
APP_SCREEN_PATH = ROOT / "static" / "app" / "app-screen.js"
APP_BUTTONS_PATH = ROOT / "static" / "app" / "app-buttons.js"
APP_AUDIO_CAPTURE_PATH = ROOT / "static" / "app" / "app-audio-capture.js"

PROACTIVE_KEYS = (
    "proactiveChatEnabled",
    "proactiveVisionEnabled",
    "proactiveVisionChatEnabled",
    "proactiveNewsChatEnabled",
    "proactiveCommunityChatEnabled",
    "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled",
    "proactiveMusicEnabled",
    "proactiveMemeEnabled",
    "proactiveMiniGameInviteEnabled",
)

TUTORIAL_SUPPRESSION_SOURCES = (
    HOME_TUTORIAL_RUNTIME_PATH,
    RELOAD_CONTROLLER_PATH,
    DIRECTOR_CORE_PATH,
)

# 机制名黑名单：快照/恢复/真值链一旦被重新引入，污染类 bug 就会回归。
REMOVED_MACHINERY = (
    "snapshotProactiveState",
    "applyProactiveState",
    "buildDisabledProactiveState",
    "maybeRestartProactiveSchedule",
    "maybeRestartProactiveRuntime",
    "HOME_TUTORIAL_PROACTIVE_KEYS",
    "PROACTIVE_STATE_KEYS",
    "proactiveSnapshot",
    "resolveRestoredProactiveState",
    "getProactiveUserValues",
    "getSuppressedUserValues",
    "readPersistedProactiveSettings",
)


def _strip_comments(source: str) -> str:
    # 注释里允许出现键名与机制名（设计说明），赋值扫描只看代码。
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def test_tutorial_suppression_never_assigns_proactive_settings():
    for path in TUTORIAL_SUPPRESSION_SOURCES:
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for key in PROACTIVE_KEYS:
            # 任何形式的赋值都禁止：window.KEY =, appState.KEY =, S.KEY =, KEY: 不算
            assert not re.search(rf"\b{key}\s*=[^=]", code), (
                f"{path.name} 不得改写设置键 {key}"
            )
        # 泛型赋值循环（window[key] = / appState[key] = / S[key] =）同样禁止；
        # flags[key] 之类是 agent 标志 payload 构建，与设置镜像无关。
        assert not re.search(r"(?:window|appState|S)\[key\]\s*=[^=]", code), (
            f"{path.name} 不得经由键列表循环改写设置内存"
        )
        for machinery in REMOVED_MACHINERY:
            assert machinery not in code, (
                f"{path.name} 不得重新引入 {machinery}（快照/恢复/真值链机制已废弃）"
            )


def test_feature_controller_suppression_is_stop_and_event_only():
    source = HOME_TUTORIAL_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function stopProactiveRuntime()" in source
    stop_fn = source.split("function stopProactiveRuntime() {", 1)[1].split("\n    }", 1)[0]
    for stopper in (
        "'stopProactiveChatSchedule'",
        "'stopProactiveVisionDuringSpeech'",
        "'releaseProactiveVisionStream'",
    ):
        assert stopper in stop_fn

    begin_fn = source.split(
        "function beginHomeTutorialFeatureSuppression(reason) {", 1
    )[1].split("\n    }", 1)[0]
    assert "stopProactiveRuntime();" in begin_fn
    assert "snapshotGalgameState()" in begin_fn  # galgame/agent 机制保留

    end_fn = source.split(
        "function endHomeTutorialFeatureSuppression(reason) {", 1
    )[1].split("\n    }", 1)[0]
    # end 只恢复 galgame/agent 并派发事件；没有任何 proactive 设置恢复代码
    # （机制名黑名单已在文件级扫描里钉死，这里再钉行为面）。
    assert "setGalgameState(!!snapshot.galgameEnabled" in end_fn
    assert "neko:home-tutorial-features-suppressed" in end_fn
    assert "snapshot.proactive" not in end_fn
    assert "scheduleProactiveChat" not in end_fn


def test_avatar_reload_layer_holds_runtime_until_model_restore_settles():
    source = RELOAD_CONTROLLER_PATH.read_text(encoding="utf-8")

    assert "function dispatchTutorialSuppressionEvent(active)" in source
    assert "isProactiveRuntimeHeld()" in source
    held_fn = source.split("isProactiveRuntimeHeld() {", 1)[1].split("\n        }", 1)[0]
    assert "this.override.proactiveRuntimeHold === true" in held_fn

    begin_block = source.split("beginOverride(", 1)[1].split("restoreOverride()", 1)[0]
    assert begin_block.index("override.proactiveRuntimeHold = true;") < begin_block.index(
        "dispatchTutorialSuppressionEvent(true);"
    ) < begin_block.index("stopProactiveRuntime();")

    restore_block = source.split("restoreOverride()", 1)[1].split(
        "window.TutorialAvatarReloadController", 1
    )[0]
    reload_await = restore_block.index("await this.reloadModel(currentName, snapshotPayload || {});")
    # 标志解除与释放事件都在模型恢复完成之后。
    assert restore_block.index("override.proactiveRuntimeHold = false;") > reload_await
    assert restore_block.index("override.proactiveRuntimeHold = false;") < restore_block.index(
        "dispatchTutorialSuppressionEvent(false);"
    )


def test_director_fallback_stops_runtime_without_key_writes():
    source = DIRECTOR_CORE_PATH.read_text(encoding="utf-8")
    fallback = source.split(
        "enforceAvatarFloatingGuideFeatureSuppression(reason) {", 1
    )[1].split("async ensureAvatarFloatingGuideSurfaceReady", 1)[0]
    assert "'stopProactiveChatSchedule'" in fallback
    assert "'releaseProactiveVisionStream'" in fallback
    assert "neko:home-tutorial-features-suppressed" in fallback
    assert fallback.index("window.dispatchEvent(") < fallback.index(
        "'releaseProactiveVisionStream'"
    )
    assert "window[key] = false" not in fallback
    assert "appState[key] = false" not in fallback


def test_central_gate_and_vision_wiring_in_app_proactive():
    source = APP_PROACTIVE_PATH.read_text(encoding="utf-8")

    gate_fn = source.split("function isHomeTutorialFeatureSuppressed() {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "window.isNekoClickGuideActive === true" in gate_fn
    assert "_homeTutorialSuppressionSources.size > 0" in gate_fn
    assert "controller.isActive()" in gate_fn
    assert "reloadController.isProactiveRuntimeHeld()" in gate_fn
    assert "window.isNekoHomeTutorialInteractionLocked" in gate_fn

    # 触发闸在 canTriggerProactively 的第一道。
    trigger_fn = source.split("function canTriggerProactively() {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert trigger_fn.index("isHomeTutorialFeatureSuppressed()") < trigger_fn.index(
        "S.proactiveChatEnabled"
    )

    # 视觉启动与每帧 tick 双闸。
    start_fn = source.split(
        "function startProactiveVisionDuringSpeech() {", 1
    )[1].split("mod.startProactiveVisionDuringSpeech", 1)[0]
    assert start_fn.index("isHomeTutorialFeatureSuppressed()") < start_fn.index(
        "!isProactiveVisionEnabledNow() || !S.isRecording"
    )
    tick = start_fn.split("setInterval(async function () {", 1)[1]
    assert "|| isHomeTutorialFeatureSuppressed()) {" in tick

    # 释放流不得因「设置仍开着」而拒绝：抑制期同样放行释放。
    release_fn = source.split("function releaseProactiveVisionStream() {", 1)[1].split(
        "mod.releaseProactiveVisionStream", 1
    )[0]
    assert "var suppressionBlocksVision = isHomeTutorialFeatureSuppressed();" in release_fn
    assert release_fn.count("!privacyBlocksVision && !suppressionBlocksVision") == 2

    # 多源叠加时只在最后一个 active:false 补挂，另有 click-guide 释放边。
    assert "function rearmProactiveVisionAfterSuppression()" in source
    listener = source.split(
        "window.addEventListener('neko:home-tutorial-features-suppressed'", 1
    )[1].split("});", 1)[0]
    assert "_homeTutorialSuppressionSources.add(detail.source || 'default');" in listener
    assert "_homeTutorialSuppressionSources.delete(detail.source || 'default');" in listener
    assert listener.index("if (_homeTutorialSuppressionSources.size > 0) return;") < listener.index(
        "scheduleProactiveChat();"
    )
    assert listener.count("rearmProactiveVisionAfterSuppression();") == 1
    click_listener = source.split(
        "window.addEventListener('neko:click-guide-active'", 1
    )[1].split("});", 1)[0]
    assert "rearmProactiveVisionAfterSuppression();" in click_listener

    # 跨模块闸门导出。
    assert (
        "window.isNekoHomeTutorialProactiveSuppressed = isHomeTutorialFeatureSuppressed;"
        in source
    )


def test_settings_driven_stream_sites_consult_the_gate():
    for path, min_occurrences in (
        (APP_SCREEN_PATH, 2),
        (APP_BUTTONS_PATH, 1),
        (APP_AUDIO_CAPTURE_PATH, 1),
    ):
        source = path.read_text(encoding="utf-8")
        assert source.count(
            "window.isNekoHomeTutorialProactiveSuppressed"
        ) >= min_occurrences, f"{path.name} 缺少教程抑制闸门"

    screen = APP_SCREEN_PATH.read_text(encoding="utf-8")
    assert screen.count(
        "var proactiveVisionActive = !tutorialHoldsVision && S.proactiveVisionEnabled"
    ) == 2

    buttons = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    assert "if (S.proactiveVisionEnabled && !tutorialHoldsVision) {" in buttons

    audio = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    assert (
        "const shouldRestartProactiveVision = S.proactiveVisionEnabled && S.isRecording && !tutorialHoldsVision;"
        in audio
    )


def test_context_prompt_defers_while_tutorial_suppresses():
    # 抑制期不弹 modal（会压在导览上）：信号暂存不标已弹，全部释放边重放。
    source = APP_CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")

    gate_fn = source.split("function _isHomeTutorialSuppressing() {", 1)[1].split(
        "\n    }", 1
    )[0]
    # 按来源引用计数兜住 controller 缺失的 fallback 环境（与 app-proactive 的
    # _homeTutorialSuppressionSources 同一模式），且多源叠加时
    # 某一来源先释放不得提前放开闸门（petal 过渡里头像层先于 director 释放）。
    assert "const _tutorialSuppressionSources = new Set();" in source
    assert "_tutorialSuppressionSources.size > 0" in gate_fn
    assert "window.isNekoClickGuideActive === true" in gate_fn
    assert "controller.isActive()" in gate_fn
    assert "reloadController.isProactiveRuntimeHeld()" in gate_fn
    # 各抑制层事件可能早于 teardown 清全局锁到达：最终交互锁未放也不弹。
    assert "window.isNekoHomeTutorialInteractionLocked()" in gate_fn

    handle_fn = source.split("async function handle(context) {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert handle_fn.index("_isHomeTutorialSuppressing()") < handle_fn.index(
        "_isActionable(context)"
    )
    gate_branch = handle_fn.split("if (_isHomeTutorialSuppressing()) {", 1)[1].split(
        "}", 1
    )[0]
    assert "_pendingContexts.add(context);" in gate_branch
    assert "_markShown" not in gate_branch
    assert "return;" in gate_branch

    assert "function _deferActiveContextPromptForTutorial()" in source
    defer_fn = source.split(
        "function _deferActiveContextPromptForTutorial() {", 1
    )[1].split("\n    }", 1)[0]
    assert "_shownPlay = false;" in defer_fn
    assert "_shownWork = false;" in defer_fn
    assert "_pendingContexts.add(context);" in defer_fn

    listener = source.split(
        "window.addEventListener('neko:home-tutorial-features-suppressed'", 1
    )[1].split("});", 1)[0]
    assert "_deferActiveContextPromptForTutorial();" in listener
    # 按来源登记/注销；只有最后一个来源释放时才 drain（闸门已放开）。
    assert (
        "_tutorialSuppressionSources.add(detail.source || 'default');" in listener
    )
    assert (
        "_tutorialSuppressionSources.delete(detail.source || 'default');" in listener
    )
    assert 'if (_tutorialSuppressionSources.size === 0) {' in listener
    assert listener.index(
        "_tutorialSuppressionSources.delete(detail.source || 'default');"
    ) < listener.index("if (_tutorialSuppressionSources.size === 0) {") < listener.index(
        "_drainPending();"
    )

    click_listener = source.split(
        "window.addEventListener('neko:click-guide-active'", 1
    )[1].split("});", 1)[0]
    assert "_deferActiveContextPromptForTutorial();" in click_listener
    assert "_drainPending();" in click_listener

    # 最终交互锁解锁（teardown 清全局锁不经抑制层事件）：补最后一次重放。
    assert "window.addEventListener('neko:home-tutorial-lock-changed'" in source


def test_final_lock_release_rearms_scheduler():
    # teardown 直接清 isInTutorial/isTutorialRunning、不经过任何抑制层的 end：
    # 必须刷新交互锁并广播 locked:false——这是重排/补挂的最终释放边。
    # 各来源的 features-suppressed false 事件都可能早于最终解锁到达，而
    # scheduleProactiveChat 在闸门仍关时直接返回、不留定时器，早到的重排
    # 等于白白停摆，之后无人再排。
    manager = (ROOT / "static" / "tutorial" / "core" / "universal-manager.js").read_text(
        encoding="utf-8"
    )
    teardown = manager.split("_teardownTutorialUI() {", 1)[1].split(
        "const teardownPromise", 1
    )[0]
    assert teardown.index("window.isInTutorial = false;") < teardown.index(
        "refreshInteractionLock('tutorial-teardown')"
    )

    proactive = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    lock_listener = proactive.split(
        "window.addEventListener('neko:home-tutorial-lock-changed'", 1
    )[1].split("});", 1)[0]
    # 只认「先锁→后解」转变：模块加载期的 init 派发（locked:false）不是教程
    # 释放，提前武装会吃掉启动宽限、在 WebSocket 初始化前挂上定时器。
    assert "var _homeTutorialLockObserved = null;" in proactive
    assert "var wasLocked = _homeTutorialLockObserved === true;" in lock_listener
    assert "if (locked || !wasLocked) return;" in lock_listener
    # 首启的 pending-lock 超时转变早于调度器初始化：未初始化时不得提前武装
    # （否则吃掉启动宽限、在 WebSocket 就绪前挂上定时器）。
    assert "if (S._proactiveSchedulerInitialized !== true) return;" in lock_listener
    assert lock_listener.index(
        "if (S._proactiveSchedulerInitialized !== true) return;"
    ) < lock_listener.index("scheduleProactiveChat();")
    assert "scheduleProactiveChat();" in lock_listener
    assert "rearmProactiveVisionAfterSuppression();" in lock_listener


def test_vision_frame_send_rechecks_suppression_after_async_capture():
    # stop 停得掉 interval、停不掉已在飞的采集：发送前的守卫必须显式复查
    # 教程闸门——新方案下设置内存保持真值，旧守卫（只看设置+录音）拦不住。
    source = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    send_fn = source.split("async function sendOneProactiveVisionFrame(", 1)[1].split(
        "mod.sendOneProactiveVisionFrame", 1
    )[0]
    guard = send_fn.rindex("isHomeTutorialFeatureSuppressed()")
    assert guard < send_fn.index("S.socket.send(JSON.stringify(")
    assert guard < send_fn.rindex("visionDisabled || !S.isRecording")
    # 竞态中已存入的屏幕流也要在抑制命中时释放（手动共享仍受保护：
    # releaseProactiveVisionStream 自身的首个分支）。
    guard_branch = send_fn[guard:send_fn.index("S.socket.send(JSON.stringify(")]
    assert "stopProactiveVisionDuringSpeech();" in guard_branch
    assert "if (suppressedByTutorial || visionDisabled) releaseProactiveVisionStream();" in guard_branch


def test_app_settings_carries_no_tutorial_machinery():
    # 新方案的核心不变量：设置保存/同步链路不需要任何教程特例——内存从头
    # 到尾是用户真值，没有可污染之物。（存量的 runSync 跳过护栏
    # NekoHomeTutorialFeatureController.isActive 是 main 既有代码，新方案下
    # 冗余但无害，不在本契约管辖内。）
    source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "_tutorialProactiveHoldValues",
        "_TUTORIAL_HELD_PROACTIVE_KEYS",
        "_tutorialProactiveAcceptedValues",
        "_comparisonConversationSettings",
        "_acceptedSettingsDivergeFromPersisted",
        "tutorialProactiveRestore",
        "getProactiveUserTruth",
    ):
        assert forbidden not in source, f"app-settings.js 不得包含 {forbidden}"
