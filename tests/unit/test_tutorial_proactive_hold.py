"""Tutorial suppression must never turn the temporary proactive-chat off state into persisted user settings.

Regression background (three holes used to reinforce each other):

* ``NekoHomeTutorialFeatureController.begin()`` snapshotted live memory that the
  avatar reload layer had already disabled, so ``end()`` "restored" all-off.
* ``saveSettings`` / send-time POST snapshots / ``_markUserDirtySettings`` read
  the suppressed mirrors, so any autosave during suppression persisted the off
  state, marked it as an explicit user edit (the boot merge then discarded
  server truth for those keys) and the 60s periodic sync pushed it upstream.
* Once localStorage held the off state, every later tutorial snapshot read it
  back as "user truth", making the damage self-perpetuating.

Invariants pinned here:

1. Both suppression layers snapshot the persisted settings
   (``project_neko_settings``) first; live memory is only a per-key fallback.
2. While suppression is ACTUALLY applied, localStorage writes, send-time POST
   snapshots and dirty marking substitute the persisted values for the ten
   proactive keys. The reload layer therefore exposes precise tracking
   (``isProactiveSuppressed``) instead of ``hasActiveOverride``: the async
   override-setup window (override created, memory not yet disabled) must not
   hold, or a concurrent boot server-merge would be clobbered by stale local
   values.
3. Restoring does NOT re-persist: an unconditional recovery write could roll
   back a concurrent sibling-window settings change and stamp it as an explicit
   edit, so no ``persistRestoredProactiveState`` may exist in either layer.
4. Keys the persistence layer is missing fall back to the suppression layer's
   begin snapshot (exposed via ``getSuppressedUserValues`` /
   ``getProactiveUserValues``), so a temporary off state can never become the
   persisted value for a missing key; and server-authoritative keys accepted by
   a settings merge during suppression bypass the hold, so a fresh server value
   is never rolled back to the stale local copy.
5. The hold reads this window's last ACCEPTED truth (recorded where
   provenance-checked values land: ``applySharedRuntimeSettings`` and
   ``saveSettings``), preferring it over the raw localStorage snapshot that may
   hold a provenance-rejected stale value; per-key fallback continues with the
   persisted value and then the MERGED layer snapshots (avatar-reload layer
   first, since it snapshots before the feature controller and therefore holds
   untainted values for keys the persistence layer is missing); and the 412
   conflict comparisons use the same held view (``_comparisonConversationSettings``).
6. ``endHomeTutorialFeatureSuppression`` defers the proactive restore while the
   avatar reload layer still reports suppression: its ``restoreOverride`` owns
   the single hand-back (restore + reschedule) after the async model reload, so
   the scheduler cannot come up against the temporary tutorial model.
7. Restoring re-reads the accepted-truth chain per key (``resolveRestoredProactiveState``):
   values a sibling window changed and this window accepted during the tutorial
   win over the begin snapshot; and ``app-context-prompt.js`` defers its prompts
   while suppression is active instead of letting the hold silently roll back an
   accepted choice — replaying them on every release edge: the feature
   controller's end event, the avatar layer's own post-model-restore event, and
   the click-guide's release event. The avatar layer restores memory and clears
   its flag BEFORE the async model reload (settings edits in that unlocked
   teardown window persist normally), restarting the scheduler and dispatching
   the release only after the model is back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME_TUTORIAL_RUNTIME_PATH = ROOT / "static" / "tutorial" / "core" / "home-tutorial-runtime.js"
RELOAD_CONTROLLER_PATH = ROOT / "static" / "tutorial" / "avatar" / "reload-controller.js"
APP_SETTINGS_PATH = ROOT / "static" / "app" / "app-settings.js"
APP_CONTEXT_PROMPT_PATH = ROOT / "static" / "app" / "app-context-prompt.js"

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


def _function_block(source: str, marker: str) -> str:
    return source.split(marker, 1)[1].split("\n    }", 1)[0]


def test_both_tutorial_layers_snapshot_persisted_user_truth_first():
    for path in (HOME_TUTORIAL_RUNTIME_PATH, RELOAD_CONTROLLER_PATH):
        source = path.read_text(encoding="utf-8")
        assert "function readPersistedProactiveSettings()" in source, path

        read_fn = _function_block(source, "function readPersistedProactiveSettings() {")
        # app-settings 的接受真值优先，原始持久层只做未加载时的回退。
        assert "getProactiveUserTruth" in read_fn, path
        assert "getItem('project_neko_settings')" in read_fn, path
        assert read_fn.index("getProactiveUserTruth") < read_fn.index(
            "getItem('project_neko_settings')"
        ), path

        snapshot_fn = _function_block(source, "function snapshotProactiveState() {")
        # 持久化真值优先，内存镜像只做缺失键的兜底。
        assert "typeof persisted[key] === 'boolean'" in snapshot_fn, path
        assert snapshot_fn.index("readPersistedProactiveSettings()") < snapshot_fn.index(
            "typeof window[key] !== 'undefined'"
        ), path


def test_reload_controller_tracks_proactive_suppression_precisely():
    source = RELOAD_CONTROLLER_PATH.read_text(encoding="utf-8")

    # 精确跟踪：只有内存真正被关闭后才算抑制（setup 异步窗口期不算）。
    assert "isProactiveSuppressed()" in source
    flag_fn = _function_block(source, "isProactiveSuppressed() {")
    assert "this.override.proactiveSuppressed === true" in flag_fn

    begin_block = source.split("beginOverride(", 1)[1].split("restoreOverride()", 1)[0]
    assert "override.proactiveSuppressed = true;" in begin_block
    # 置位必须发生在实际关闭内存之后，且立即派发抑制事件（撤回展示中的
    # 情境弹窗——此期间「接受」会被持久化护栏回滚）。
    assert begin_block.index("applyProactiveState(buildDisabledProactiveState());") < begin_block.index(
        "override.proactiveSuppressed = true;"
    ) < begin_block.index("dispatchTutorialSuppressionEvent(true);")

    restore_block = source.split("restoreOverride()", 1)[1].split(
        "window.TutorialAvatarReloadController", 1
    )[0]
    # 内存恢复+标志释放前置到模型 await 之前：teardown 已解锁设置面板，
    # 该窗口内用户的显式改动必须正常落盘、不被 hold 回滚（review R8-4）。
    assert (
        "const resolvedProactive = resolveRestoredProactiveState(proactiveSnapshot);"
        in restore_block
    )
    assert restore_block.index("applyProactiveState(resolvedProactive);") < restore_block.index(
        "override.proactiveSuppressed = false;"
    ) < restore_block.index("await this.reloadModel(currentName, snapshotPayload || {});")
    # 调度器重启与释放事件留在模型恢复完成之后（不对着临时教程模型开口；
    # 释放事件补放被 end() 提前重放、又因本层仍抑制而重新入队的弹窗）。
    assert restore_block.index(
        "maybeRestartProactiveRuntime(readCurrentProactiveState());"
    ) > restore_block.index("await this.reloadModel(currentName, snapshotPayload || {});")
    assert "dispatchTutorialSuppressionEvent(false);" in restore_block
    assert restore_block.index("maybeRestartProactiveRuntime(readCurrentProactiveState());") < restore_block.index(
        "dispatchTutorialSuppressionEvent(false);"
    )

    # 持久化护栏缺键回退用的用户真值 getter：仅本层确在抑制期间有效。
    assert "getProactiveUserValues()" in source
    values_fn = _function_block(source, "getProactiveUserValues() {")
    assert "this.override.proactiveSuppressed !== true" in values_fn
    assert "this.override.proactiveSnapshot" in values_fn


def test_feature_controller_exposes_suppressed_user_values():
    source = HOME_TUTORIAL_RUNTIME_PATH.read_text(encoding="utf-8")
    assert "getSuppressedUserValues: function () {" in source
    getter = source.split("getSuppressedUserValues: function () {", 1)[1].split(
        "\n        },", 1
    )[0]
    assert "!suppression.active" in getter
    assert "suppression.snapshot.proactive" in getter


def test_controller_end_defers_restore_while_avatar_layer_suppresses():
    # skip/angry-exit 收口时 end() 先于异步的 restoreTutorialAvatarOverride
    # 执行；头像重载层仍持有抑制时 end() 不得抢先恢复+重排调度，否则模型
    # 恢复完成前的窗口里主动搭话可能对着临时教程模型开口。恢复职责由
    # 重载层 restoreOverride 的 finally 统一承担（其快照更早、缺键更纯净）。
    source = HOME_TUTORIAL_RUNTIME_PATH.read_text(encoding="utf-8")
    assert "function isAvatarReloadProactiveSuppressed()" in source
    helper = _function_block(source, "function isAvatarReloadProactiveSuppressed() {")
    assert "reloadController.isProactiveSuppressed()" in helper

    end_fn = _function_block(
        source, "function endHomeTutorialFeatureSuppression(reason) {"
    )
    assert "if (snapshot.proactive && !isAvatarReloadProactiveSuppressed()) {" in end_fn
    assert end_fn.index("!isAvatarReloadProactiveSuppressed()") < end_fn.index(
        "applyProactiveState(restoredProactive);"
    )


def test_tutorial_restore_does_not_repersist_settings():
    # 恢复即回写会把兄弟窗口在教程期间的并发改动回滚成旧快照、并被
    # _collectExplicitSharedKeys 标成显式修改扩散出去；护栏落地后回写
    # 已无修复对象，两层都不允许再出现恢复回写。
    for path in (HOME_TUTORIAL_RUNTIME_PATH, RELOAD_CONTROLLER_PATH):
        source = path.read_text(encoding="utf-8")
        assert "persistRestoredProactiveState" not in source, path
        assert "tutorialProactiveRestore" not in source, path


def test_settings_hold_helper_covers_both_suppression_layers():
    source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    for key in PROACTIVE_KEYS:
        assert f"'{key}'" in source.split(
            "const _TUTORIAL_HELD_PROACTIVE_KEYS = [", 1
        )[1].split("];", 1)[0]

    active_fn = _function_block(
        source, "function _isTutorialProactiveSuppressionActive() {"
    )
    # feature controller 抑制（begin/end 同步翻转，isActive 即内存已关）……
    assert "window.NekoHomeTutorialFeatureController" in active_fn
    assert "controller.isActive()" in active_fn
    # ……以及头像重载层"确已关闭内存"的精确标志；不得用 hasActiveOverride，
    # 否则 override 的异步 setup 窗口期会误 hold，压掉 boot merge 拿到的服务器新值。
    assert "window.universalTutorialManager" in active_fn
    assert "reloadController.isProactiveSuppressed()" in active_fn
    assert "reloadController.hasActiveOverride()" not in active_fn

    hold_fn = _function_block(source, "function _tutorialProactiveHoldValues() {")
    assert "_isTutorialProactiveSuppressionActive()" in hold_fn
    assert "return _tutorialProactiveUserTruth();" in hold_fn

    layer_fn = _function_block(source, "function _tutorialProactiveLayerUserValues() {")
    assert "reloadController.getProactiveUserValues()" in layer_fn
    assert "controller.getSuppressedUserValues()" in layer_fn
    # 逐键合并、头像重载层优先：它的快照早于 feature controller（prelude
    # 先 beginAvatarOverride 后 beginTakingOver），对持久层缺失的键保有
    # 未被污染的内存真值；controller 快照只补缺键。
    assert layer_fn.index(
        "sources.push(reloadController.getProactiveUserValues());"
    ) < layer_fn.index("sources.push(controller.getSuppressedUserValues());")
    assert "typeof merged[key] !== 'boolean'" in layer_fn


def test_hold_prefers_accepted_truth_and_conflict_comparison_is_hold_aware():
    source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # 接受真值登记表：入口只有 applySharedRuntimeSettings（storage 监听 /
    # boot merge / 412 和解三条接受路径的共同落点，出处校验已在上游完成）
    # 与 saveSettings 落盘（本次写盘值即本窗口最新真值）。
    assert "const _tutorialProactiveAcceptedValues = {};" in source
    apply_fn = source.split(
        "function applySharedRuntimeSettings(settings) {", 1
    )[1].split("function isManualScreenShareActive()", 1)[0]
    assert "_recordTutorialProactiveAcceptedValues(settings);" in apply_fn
    save_fn = source.split("function saveSettings(options)", 1)[1].split(
        "function loadSettings()", 1
    )[0]
    assert "_recordTutorialProactiveAcceptedValues(settings);" in save_fn

    # boot 播种：loadSettings 直接给 S 赋值、不经 applySharedRuntimeSettings，
    # 登记表必须在发出 boot GET 前用本地视图播种，否则首次 save/merge 前
    # hold/快照会回退读原始 localStorage（可能含被出处校验拒绝的陈旧值）。
    load_fn = source.split("function loadSettings()", 1)[1]
    assert "_recordTutorialProactiveAcceptedValues(getConversationSettings());" in load_fn
    assert load_fn.index(
        "_recordTutorialProactiveAcceptedValues(getConversationSettings());"
    ) < load_fn.index("loadSettingsFromServer().then(serverResult => {")

    truth_fn = _function_block(source, "function _tutorialProactiveUserTruth() {")
    # 三级优先：接受真值 > 持久值 > 抑制层合并快照（缺键的唯一真值来源）。
    assert "_tutorialProactiveAcceptedValues[key] === 'boolean'" in truth_fn
    assert "_tutorialProactiveLayerUserValues()" in truth_fn
    assert truth_fn.index("_tutorialProactiveAcceptedValues[key]") < truth_fn.index(
        "persisted[key]"
    ) < truth_fn.index("layerValues[key]")
    assert "mod.getProactiveUserTruth = _tutorialProactiveUserTruth;" in source

    # 412 冲突比较与 pending 确认的「当前值」必须与发送时快照同口径：
    # 直接读 getConversationSettings 会把抑制中的临时 false 误判成
    # 「发送后被改过」，冲突合并拒收服务器新值、重试写回旧值，CAS 失效。
    assert "function _comparisonConversationSettings()" in source
    comparison_fn = _function_block(
        source, "function _comparisonConversationSettings() {"
    )
    assert "_tutorialProactiveHoldValues()" in comparison_fn
    for marker in (
        "function _settingsChangedSince(snapshot, mutationVersion) {",
        "function _clearAcknowledgedPendingSettings(payload) {",
        # 确认路径同样必须同口径：held 发送值在抑制期间要与「当前值」
        # 相等才能盖上 confirmedRevision，否则后续 server-authoritative
        # 快照会被 localToken && !confirmedRevision 判旧拒收。
        "function _confirmSharedKeyWrites(",
    ):
        fn = _function_block(source, marker)
        assert "_comparisonConversationSettings();" in fn
        assert "getConversationSettings();" not in fn


def test_settings_persistence_paths_apply_tutorial_hold():
    source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # 落盘：saveSettings 在 _writeSharedSettings 之前还原这十个键；
    # server merge 刚接受的权威键必须绕过 hold（不得回滚成旧持久值）。
    save_fn = source.split("function saveSettings(options)", 1)[1].split(
        "function loadSettings()", 1
    )[0]
    assert "const tutorialProactiveHold = _tutorialProactiveHoldValues();" in save_fn
    assert "serverAuthoritativeKeys.indexOf(key) === -1" in save_fn
    assert save_fn.index(
        "settings[key] = tutorialProactiveHold[key];"
    ) < save_fn.index("_writeSharedSettings(")
    assert "tutorialProactiveRestore" not in save_fn

    # POST：发送时快照同样过护栏（头像重载层单独抑制时 guard 不生效）。
    sync_fn = source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    assert sync_fn.index("const settings = getConversationSettings();") < sync_fn.index(
        "const sendHold = _tutorialProactiveHoldValues();"
    ) < sync_fn.index("await _fetchConversationSettingsJsonWithTimeout(")

    # dirty 标记：抑制不是用户意图，diff 前还原持久化真值。
    mark_fn = _function_block(source, "function _markUserDirtySettings() {")
    assert "const tutorialProactiveHold = _tutorialProactiveHoldValues();" in mark_fn
    assert mark_fn.index(
        "if (tutorialProactiveHold) Object.assign(current, tutorialProactiveHold);"
    ) < mark_fn.index("if (_settingsBaseline) {")

    # 优化决策确认的直接落盘路径也带护栏。
    ack_index = source.index("_writeSharedSettings(ackSettings, []);")
    ack_block = source[ack_index - 400:ack_index]
    assert "const ackHold = _tutorialProactiveHoldValues();" in ack_block
    assert "if (ackHold) Object.assign(ackSettings, ackHold);" in ack_block


def test_restore_rereads_accepted_truth_over_begin_snapshot():
    # 教程期间兄弟窗口经出处校验接受的新改动比 begin 快照更新：
    # 两层恢复都必须逐键重读真值链，begin 快照只兜缺失键。
    for path, keys_const in (
        (HOME_TUTORIAL_RUNTIME_PATH, "HOME_TUTORIAL_PROACTIVE_KEYS"),
        (RELOAD_CONTROLLER_PATH, "PROACTIVE_STATE_KEYS"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "function resolveRestoredProactiveState(snapshot)" in source, path
        resolve_fn = _function_block(
            source, "function resolveRestoredProactiveState(snapshot) {"
        )
        assert "getProactiveUserTruth" in resolve_fn, path
        assert keys_const in resolve_fn, path
        assert resolve_fn.index("truth[key] === 'boolean'") < resolve_fn.index(
            "snapshot[key] === 'boolean'"
        ), path

    runtime = HOME_TUTORIAL_RUNTIME_PATH.read_text(encoding="utf-8")
    end_fn = _function_block(
        runtime, "function endHomeTutorialFeatureSuppression(reason) {"
    )
    assert (
        "const restoredProactive = resolveRestoredProactiveState(snapshot.proactive);"
        in end_fn
    )
    assert "applyProactiveState(restoredProactive);" in end_fn
    assert "maybeRestartProactiveSchedule(restoredProactive);" in end_fn


def test_context_prompt_defers_while_tutorial_suppresses():
    # 抑制期间「接受」会被持久化护栏回滚：情境弹窗必须延迟（暂存信号、
    # 不标已弹），收口事件后重放；已展示的弹窗撤回并恢复未弹状态。
    source = APP_CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")

    assert "function _isHomeTutorialSuppressing()" in source
    gate_fn = _function_block(source, "function _isHomeTutorialSuppressing() {")
    assert "window.isNekoClickGuideActive === true" in gate_fn
    assert "NekoHomeTutorialFeatureController" in gate_fn
    assert "controller.isActive()" in gate_fn
    assert "reloadController.isProactiveSuppressed()" in gate_fn

    handle_fn = source.split("async function handle(context) {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "_isHomeTutorialSuppressing()" in handle_fn
    # 延迟必须发生在弹窗展示与 _isActionable 消费信号之前，且入队暂存。
    assert handle_fn.index("_isHomeTutorialSuppressing()") < handle_fn.index(
        "_isActionable(context)"
    )
    suppress_gate = handle_fn.split("_isHomeTutorialSuppressing()", 1)[1]
    assert suppress_gate.lstrip().startswith(") {") or "_pendingContexts.add(context);" in suppress_gate[:120]

    assert "function _deferActiveContextPromptForTutorial()" in source
    defer_fn = _function_block(
        source, "function _deferActiveContextPromptForTutorial() {"
    )
    assert "_shownPlay = false;" in defer_fn
    assert "_shownWork = false;" in defer_fn
    assert "_pendingContexts.add(context);" in defer_fn

    listener = source.split(
        "window.addEventListener('neko:home-tutorial-features-suppressed'", 1
    )[1].split("});", 1)[0]
    assert "_deferActiveContextPromptForTutorial();" in listener
    assert "_drainPending();" in listener

    # click-guide 是闸门的另一来源，但派发自己的事件：解除时同样要重放，
    # 否则其抑制期暂存的提示会卡死（后端信号是一次性的）。
    click_listener = source.split(
        "window.addEventListener('neko:click-guide-active'", 1
    )[1].split("});", 1)[0]
    assert "_drainPending();" in click_listener
