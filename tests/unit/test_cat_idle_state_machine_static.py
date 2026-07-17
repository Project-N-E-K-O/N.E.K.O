import json
import shutil
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAT_MIND_PATH = PROJECT_ROOT / "static" / "app" / "app-cat-mind.js"
CAT_MIND_DEBUG_PATH = PROJECT_ROOT / "static" / "app" / "app-cat-mind-debug.js"
APP_AUTO_GOODBYE_PATH = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"
APP_WEBSOCKET_PATH = PROJECT_ROOT / "static" / "app" / "app-websocket.js"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"
PAGES_ROUTER_PATH = PROJECT_ROOT / "main_routers" / "pages_router.py"
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"


def _read(path: Path) -> str:
    if path.is_dir():
        part_paths = tuple(sorted(path.glob("*.js")))
        assert part_paths, f"avatar UI button parts not found: {path}"
        return "\n".join(part.read_text(encoding="utf-8") for part in part_paths)
    return path.read_text(encoding="utf-8")


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        raise AssertionError("node is required to run cat mind harness tests")

    return subprocess.run(
        [node_path, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cat_mind_contract_script_loads_before_behavior_modules():
    index_source = _read(INDEX_TEMPLATE_PATH)
    chat_source = _read(CHAT_TEMPLATE_PATH)
    script_tag = '<script src="/static/app/app-cat-mind.js?v={{ static_asset_version }}"></script>'
    debug_script_tag = '<script src="/static/app/app-cat-mind-debug.js?v={{ static_asset_version }}"></script>'

    assert script_tag in index_source
    assert script_tag in chat_source
    assert debug_script_tag in index_source
    assert debug_script_tag in chat_source

    assert index_source.index('/static/app/app-state.js') < index_source.index('/static/app/app-cat-mind.js')
    assert index_source.index('/static/app/app-cat-mind.js') < index_source.index('/static/app/app-cat-mind-debug.js')
    first_app_ui_part = '/static/app/app-ui/bootstrap-goodbye-and-toasts.js'
    last_app_ui_part = '/static/app/app-ui/wrapup-final-guards.js'
    assert index_source.index('/static/app/app-cat-mind-debug.js') < index_source.index(first_app_ui_part)
    assert index_source.index(first_app_ui_part) < index_source.index(last_app_ui_part)
    assert index_source.index(last_app_ui_part) < index_source.index('/static/app/app-chat-text-utils.js')
    assert index_source.index('/static/app/app-cat-mind.js') < index_source.index('/static/app/app-websocket.js')
    assert index_source.index('/static/app/app-websocket.js') < index_source.index('/static/app/app-auto-goodbye.js')
    assert chat_source.index('/static/app/app-state.js') < chat_source.index('/static/app/app-cat-mind.js')
    assert chat_source.index('/static/app/app-cat-mind.js') < chat_source.index('/static/app/app-cat-mind-debug.js')
    assert chat_source.index('/static/app/app-cat-mind-debug.js') < chat_source.index(first_app_ui_part)
    assert chat_source.index(first_app_ui_part) < chat_source.index(last_app_ui_part)
    assert chat_source.index(last_app_ui_part) < chat_source.index('/static/app/app-chat-text-utils.js')


def test_cat_mind_contract_is_in_static_asset_version_inputs():
    source = _read(PAGES_ROUTER_PATH)

    assert '_PROJECT_ROOT / "static/app/app-cat-mind.js"' in source
    assert '_PROJECT_ROOT / "static/app/app-cat-mind-debug.js"' in source


def test_cat_mind_phase0_contract_defines_events_debug_control_and_payload_shape():
    source = _read(CAT_MIND_PATH)

    for event_name in (
        "neko:cat-mind:observation",
        "neko:cat-mind:state-change",
        "neko:cat-mind:action-request",
        "neko:cat-mind:action-result",
        "neko:cat-mind:return-summary",
    ):
        assert event_name in source

    for field in ("type", "source", "tier", "timestamp", "detail"):
        assert f"'{field}'" in source

    assert "neko.catMind.debug" in source
    for removed_runtime_flag in (
        "neko.catMind.enabled",
        "neko.catMind.selector.enabled",
        "neko.catMind.takeover.v1Actions",
        "neko.catMind.actions.socialPing",
        "neko.catMind.actions.eatSnack",
        "neko.catMind.actions.smallMove",
        "neko.catMind.actions.playYarn",
        "neko.catMind.actions.sleepFeedback",
    ):
        assert removed_runtime_flag not in source


def test_cat_mind_phase3_defaults_only_keep_debug_control():
    source = _read(CAT_MIND_PATH)
    assert "var DEBUG_SETTING_KEY = 'neko.catMind.debug';" in source
    assert "DEFAULT_FEATURE_FLAGS" not in source


def test_cat_mind_runtime_keeps_dom_free_observation_and_request_boundaries():
    source = _read(CAT_MIND_PATH)

    assert "window.NekoCatMindContract" in source
    assert "window.nekoCatMind = Object.freeze({" in source
    for api_name in (
        "getState",
        "getRecentEvents",
        "getReturnSummaryDraft",
        "consumeReturnSummaryDraft",
        "getDebugSnapshot",
        "recordDecision",
        "acknowledgeActionRequest",
        "observe",
        "reset",
    ):
        assert f"{api_name}: {api_name}" in source

    assert "addEventListener('live2d-goodbye-click'" in source
    assert "addEventListener('neko:auto-goodbye:state-change'" in source
    assert "addEventListener('neko:return-ball-manual-move'" in source
    assert "addEventListener('neko:idle-return-ball-state'" in source
    assert "addEventListener('neko:thought-bubble-pop'" in source
    assert "addEventListener(EVENT_NAMES.OBSERVATION" in source

    assert "function dispatchRuntimeEvent" in source
    assert "EVENT_NAMES.STATE_CHANGE" in source
    assert "EVENT_NAMES.RETURN_SUMMARY" in source
    assert "function startAutonomousClock" in source
    assert "function emitAutonomousTimeObservations" in source
    assert "emitAutonomousTimeObservations(generation);" in source
    assert "function clearAutonomousClock" in source
    assert "function scheduleDecision" in source
    assert "window.setTimeout(evaluateQueuedDecision, 0);" in source
    assert "querySelector" not in source
    assert "getElementById" not in source
    assert "_playNekoIdle" not in source
    assert "ACTION_REQUEST" in source
    assert "dispatchRuntimeEvent(EVENT_NAMES.ACTION_REQUEST" in source
    assert "TAKEOVER_V1_ACTIONS" not in source
    assert "function isDebugEnabled()" in source
    assert "FEATURE_FLAGS" not in source
    assert "getFeatureFlag" not in source
    assert "selectAction" not in source
    assert "utilityScore" in source
    assert "scoreAction" not in source


def test_cat_mind_phase0_declares_first_action_and_observation_ids():
    source = _read(CAT_MIND_PATH)

    for action_id in (
        "cat1_social_ping",
        "cat1_eat_snack",
        "cat1_play_yarn",
        "cat2_nap_feedback",
        "cat3_sleep_feedback",
        "quiet",
        "stay_idle",
    ):
        assert action_id in source

    for observation_type in (
        "drag_start",
        "drag_end",
        "drag_cancelled",
        "rapid_drag",
        "cat_hover_reaction",
        "thought_bubble_pop",
        "return_click",
        "cat1_walk_done_near_chat",
        "cat1_compact_top_edge_done",
        "chat_minimized_moved_far",
        "chat_idle_docked_near_cat",
        "desktop_occlusion_or_layer_change",
        "tier_changed",
        "tier_demoted_by_drag",
        "action_interrupted_by_return",
    ):
        assert observation_type in source


def test_cat_mind_thought_bubble_click_only_records_the_pop_fact():
    source = _read(AVATAR_UI_BUTTONS_PATH)
    click_block = source.split("function _handleNekoIdleThoughtBubbleClick(button, event)", 1)[1].split(
        "function _showNekoIdleThoughtBubbleForSound",
        1,
    )[0]

    assert "_popNekoIdleThoughtBubble(button" in click_block
    assert "_playNekoIdleCat1EatAction(button);" not in click_block


def test_cat_mind_phase1_avatar_emits_canonical_observation_facts_only():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    assert "const _NEKO_CAT_IDLE_OBSERVATION_SOURCE_EVENT = 'neko:cat-mind:observation';" in source
    assert "function _dispatchNekoCatIdleObservationSource(type, detail = {})" in source
    assert "window.dispatchEvent(new CustomEvent(_NEKO_CAT_IDLE_OBSERVATION_SOURCE_EVENT" in source

    for observation_type in (
        "rapid_drag",
        "cat_hover_reaction",
        "cat1_walk_done_near_chat",
        "cat1_stretch_done_near_chat",
        "cat1_compact_top_edge_done",
        "cat1_compact_top_edge_drop",
        "edge_peek_after_drag",
    ):
        assert observation_type in source

    helper_block = source.split("function _sanitizeNekoCatIdleObservationDetail(detail = {})", 1)[1].split(
        "function _dispatchNekoCatIdleObservationSource(type, detail = {})",
        1,
    )[0]
    for skipped_key in ("button", "container", "target", "originalEvent"):
        assert f"key === '{skipped_key}'" in helper_block


def test_cat_mind_phase2_avatar_providers_stay_read_only_before_phase3_adapter():
    source = _read(AVATAR_UI_BUTTONS_PATH)
    cat_mind_source = _read(CAT_MIND_PATH)

    assert "const _NEKO_CAT_MIND_ACTION_RESULT_EVENT = 'neko:cat-mind:action-result';" in source
    assert "window.NekoCatMindActionProviders = Object.freeze({" in source
    assert "dryRun: _dryRunNekoCatMindActionProvider" in source
    assert "function _isNekoIdleReturnDragActionBlocking(button)" in source
    assert "dragging === 'pending' || dragging === 'true'" in source
    assert "const _NEKO_CAT_MIND_ACTION_REQUEST_EVENT = 'neko:cat-mind:action-request';" in source
    assert "function _dryRunNekoCatMindActionProvider" in source
    assert "dispatchRuntimeEvent(EVENT_NAMES.ACTION_REQUEST" in cat_mind_source

    for action_id in (
        "cat1_social_ping",
        "cat1_eat_snack",
        "cat1_play_yarn",
        "cat2_nap_feedback",
        "cat3_sleep_feedback",
    ):
        assert action_id in source

    provider_block = source.split("function _dryRunNekoCatMindCat1ButtonProvider", 1)[1].split(
        "function _acknowledgeNekoCatMindActionRequest",
        1,
    )[0]
    for forbidden in (
        "dispatchEvent",
        "_playNekoIdleSound",
        "_setNekoIdleReturnArtSource",
        "_setNekoIdleCat1PlayYarnHidden",
        "_postNekoIdleCat1PlayYarnVisibilityState",
        "setTimeout",
        "setInterval",
    ):
        assert forbidden not in provider_block


def test_cat_mind_runners_report_results_without_legacy_entries():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    remove_block = source.split("function _removeFloatingButtonsElement(el)", 1)[1].split(
        "function _setupFloatingButtonsEntranceHooks",
        1,
    )[0]
    eat_cancel_block = source.split("function _cancelNekoIdleCat1EatAction(button, options = {})", 1)[1].split(
        "function _finishNekoIdleCat1EatAction",
        1,
    )[0]
    eat_play_block = source.split("function _playNekoIdleCat1EatAction(button)", 1)[1].split(
        "function _getNekoIdleCat1PlayActionState",
        1,
    )[0]
    play_cancel_block = source.split("function _cancelNekoIdleCat1PlayAction(button, options = {})", 1)[1].split(
        "function _finishNekoIdleCat1PlayAction",
        1,
    )[0]
    play_block = source.split("function _playNekoIdleCat1PlayAction(button)", 1)[1].split(
        "function _clearNekoIdleThoughtBubble",
        1,
    )[0]
    social_block = source.split("function _playNekoIdleCat1AmbientSound(token)", 1)[1].split(
        "function _stopNekoIdleCat1AmbientSound",
        1,
    )[0]
    social_stop_block = source.split("function _stopNekoIdleCat1AmbientSoundAudio(options = {})", 1)[1].split(
        "function _pickNekoIdleCat1AmbientSoundUrl",
        1,
    )[0]
    sleep_block = source.split("function _playNekoIdleSleepSound(tier, token)", 1)[1].split(
        "function _syncNekoIdleSleepSoundForTier",
        1,
    )[0]
    sleep_stop_block = source.split("function _stopNekoIdleSleepSoundAudio(options = {})", 1)[1].split(
        "function _stopNekoIdleSleepSound(options = {})",
        1,
    )[0]

    assert "_stopNekoIdleSleepSound({ reason: 'container-removed' });" in remove_block
    assert "_stopNekoIdleCat1AmbientSound({ reason: 'container-removed' });" in remove_block

    assert "_beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK" in eat_play_block
    assert "_reportNekoCatMindStateActionResult(" in eat_cancel_block
    assert "cat1-eat-action-finished" in eat_cancel_block

    assert "_beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN" in play_block
    assert "_setNekoIdleCat1PlayYarnHidden(" in play_cancel_block
    assert "_reportNekoCatMindStateActionResult(" in play_cancel_block
    assert "cat1-play-action-finished" in play_cancel_block

    assert "_NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING" in social_block
    assert "_playNekoIdleSound(" in social_block
    assert "const run = _beginNekoCatMindStateAction(" in social_block
    assert "_reportNekoCatMindStateActionRunResult(" in social_block
    assert "_showNekoIdleThoughtBubbleForSound(_NEKO_IDLE_TIER_CAT1, audio)" in social_block
    assert "_playNekoIdleCat1SoundReaction();" in social_block
    assert "_notifyNekoCatMindRunnerStarted(catMindRunOptions, run);" in social_block

    assert "_NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK" in sleep_block
    assert "_NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK" in sleep_block
    assert "const run = _beginNekoCatMindStateAction(_nekoIdleSleepSoundState" in sleep_block
    assert "_reportNekoCatMindStateActionRunResult(" in sleep_block
    assert "_showNekoIdleThoughtBubbleForSound(tier, audio)" in sleep_block
    assert "_notifyNekoCatMindRunnerStarted(catMindRunOptions, run);" in sleep_block

    assert social_stop_block.index("_stopNekoIdleSoundAudio(_nekoIdleCat1AmbientSoundState);") < social_stop_block.index(
        "_reportNekoCatMindStateActionResult("
    )
    assert sleep_stop_block.index("_stopNekoIdleSoundAudio(_nekoIdleSleepSoundState);") < sleep_stop_block.index(
        "_reportNekoCatMindStateActionResult("
    )
    assert "_clearNekoIdleThoughtBubbleForTier(tier);" in sleep_stop_block
    social_ended_block = social_block.split("audio.addEventListener('ended', () => {", 1)[1].split(
        "}, { once: true });",
        1,
    )[0]
    assert "_isNekoCatMindStateActionRunCurrent(_nekoIdleCat1AmbientSoundState, run, audio)" in social_ended_block
    assert social_ended_block.index("_isNekoCatMindStateActionRunCurrent") < social_ended_block.index(
        "_clearNekoIdleThoughtBubbleForTier(_NEKO_IDLE_TIER_CAT1);"
    )
    assert social_ended_block.index("_clearNekoIdleThoughtBubbleForTier(_NEKO_IDLE_TIER_CAT1);") < social_ended_block.index(
        "_reportNekoCatMindStateActionRunResult("
    )

    near_chat_block = source.split("function _isNekoCatMindCat1NearChat(button)", 1)[1].split(
        "function _canNekoCatMindControlPlayYarn",
        1,
    )[0]
    assert "_isNekoIdleCat1SettledOnMinimizedSide(journey, profile)" in near_chat_block

    assert "Math.random() < _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY" in source
    assert "Math.random() < _NEKO_IDLE_CAT1_PAIR_MOVE_PLAY_PROBABILITY" not in source
    assert "function _scheduleNekoIdleCat1AmbientSoundInterval" not in source
    assert "function _scheduleNekoIdleSleepSoundInterval" not in source


def test_cat_mind_phase3_avatar_has_one_request_adapter_and_preserves_runner_ownership():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    adapter_block = source.split("function _runNekoCatMindActionRequest(request)", 1)[1].split(
        "function _isNekoCatMindAudioActionActive",
        1,
    )[0]
    assert "_dryRunNekoCatMindActionProvider(actionId" in adapter_block
    assert "_isNekoCatMindEnabled()" not in adapter_block
    assert "_acknowledgeNekoCatMindActionRequest(request, 'rejected'" in adapter_block
    assert "_playNekoIdleCat1EatAction(" in adapter_block
    assert "_startNekoIdleCat1PairMove(" in adapter_block
    assert "_playNekoIdleCat1PlayAction(" in adapter_block
    assert "_playNekoIdleCat1AmbientSound(" in adapter_block
    assert "_playNekoIdleSleepSound(" in adapter_block
    assert "source: 'cat_mind'" in adapter_block
    assert "requestId: request.requestId" in adapter_block
    assert "onAccepted(run)" in adapter_block
    assert "onStarted(run)" in adapter_block
    assert "_dispatchNekoCatMindActionResult(" not in adapter_block

    request_listener = "window.addEventListener(_getNekoCatMindActionRequestEventName()"
    assert source.count(request_listener) == 1
    assert "_runNekoCatMindActionRequest(event && event.detail);" in source

    result_block = source.split("function _dispatchNekoCatMindActionResult", 1)[1].split(
        "function _reportNekoCatMindStateActionResult",
        1,
    )[0]
    assert "requestId: detail.requestId || ''" in result_block
    report_block = source.split("function _reportNekoCatMindStateActionResult", 1)[1].split(
        "function _isNekoCatMindStateActionRunCurrent",
        1,
    )[0]
    assert "requestId: detail.requestId || state.catMindRequestId" in report_block

    pair_move_finish_block = source.split("function _finishNekoIdleCat1PairMove(button)", 1)[1].split(
        "function _stepNekoIdleCat1PairMove",
        1,
    )[0]
    assert "_NEKO_CAT_MIND_ACTION_RESULTS.DONE" in pair_move_finish_block
    assert "restored: true" in pair_move_finish_block
    assert pair_move_finish_block.index("_setNekoIdleReturnArtSource(") < pair_move_finish_block.index(
        "_reportNekoCatMindStateActionResult("
    )

    cancel_pair_move_block = source.split("function _cancelNekoIdleCat1PairMove(state, options = {})", 1)[1].split(
        "function _interruptNekoIdleCat1PairMoveForRetarget",
        1,
    )[0]
    assert "options.reason || 'cat1-small-move-cancelled'" in cancel_pair_move_block
    cancel_journey_block = source.split("function _cancelNekoIdleReturnSubactionState(state, options = {})", 1)[1].split(
        "function _cancelNekoIdleCat1Journey",
        1,
    )[0]
    assert "_cancelNekoIdleCat1PairMove(state, { reason: options.reason });" in cancel_journey_block


def test_cat_mind_phase3_removes_legacy_action_dispatchers():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    for removed_legacy_entry in (
        "function _isNekoCatMindTakeoverV1ActionsEnabled()",
        "function _dispatchNekoCatMindSchedulerWakeup(actionId, detail = {})",
        "function _queueNekoCatMindWakeupAfterRuntimeGateRelease(reason)",
        "function _scheduleNekoIdleSleepSoundInterval",
        "function _scheduleNekoIdleCat1AmbientSoundInterval",
        "function _scheduleNekoIdleCat1PairMove(button)",
    ):
        assert removed_legacy_entry not in source

    click_block = source.split("function _handleNekoIdleThoughtBubbleClick(button, event)", 1)[1].split(
        "function _showNekoIdleThoughtBubbleForSound",
        1,
    )[0]
    assert "_popNekoIdleThoughtBubble(button" in click_block
    assert "_playNekoIdleCat1EatAction(button)" not in click_block

    walk_finish_block = source.split("function _finishNekoIdleCat1Walk", 1)[1].split(
        "function _finishNekoIdleCat1CompactTopEdgeWalk",
        1,
    )[0]
    assert "_dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT" in walk_finish_block
    assert "CAT1_PLAY_YARN_WAKEUP" not in walk_finish_block
    assert "Math.random() < _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY" in walk_finish_block
    assert "_playNekoIdleCat1PlayAction(button, {" in walk_finish_block
    assert "source: 'cat1-journey-local'" in walk_finish_block
    assert "CAT1_LOCAL_PLAY_DONE" in walk_finish_block
    assert "CAT1_LOCAL_PLAY_CANCELLED" in walk_finish_block

    pair_move_block = source.split("function _startNekoIdleCat1PairMove(button)", 1)[1].split(
        "function _refreshNekoIdleCat1Observer",
        1,
    )[0]
    assert "if (!isCatMindRun) return false;" in pair_move_block
    assert "_playNekoIdleCat1PlayAction(button)" not in pair_move_block


def test_cat_mind_walk_journey_tail_observations_reduce_without_waking_selector():
    """The old walk tail is visible state, not a second autonomous action source."""
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const timers = [];
        const requests = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        assert.equal(timers.length, 1);
        timers.shift()();
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_social_ping'
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_for_this_test' }};
          }}
        }};

        const beforeWalk = win.nekoCatMind.getState().fields;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat1_walk_done_near_chat', source: 'journey-test', tier: 'cat1', timestamp: now += 10, detail: {{}} }}
        }}));
        const afterWalk = win.nekoCatMind.getState().fields;
        assert.ok(afterWalk.social_need > beforeWalk.social_need);
        assert.equal(win.nekoCatMind.getRecentEvents().at(-1).type, 'cat1_walk_done_near_chat');
        assert.equal(timers.length, 0);
        assert.equal(requests.length, 0);

        const beforeStretch = win.nekoCatMind.getState().fields;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat1_stretch_done_near_chat', source: 'journey-test', tier: 'cat1', timestamp: now += 10, detail: {{}} }}
        }}));
        const afterStretch = win.nekoCatMind.getState().fields;
        assert.ok(afterStretch.energy < beforeStretch.energy);
        assert.equal(win.nekoCatMind.getRecentEvents().at(-1).type, 'cat1_stretch_done_near_chat');
        assert.equal(timers.length, 0);
        assert.equal(requests.length, 0);

        // Ordinary interaction remains a later asynchronous decision source,
        // but a popped bubble now satisfies need and must not force an action.
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'thought_bubble_pop', source: 'journey-test', tier: 'cat1', timestamp: now += 80, detail: {{}} }}
        }}));
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 0);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'below_action_threshold');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat1_walk_finish_resolves_one_local_tail_per_approach():
    """A duplicated finish callback may neither re-roll nor append the other tail."""
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const avatarPartsDir = {json.dumps(str(AVATAR_UI_BUTTONS_PATH))};
        const avatarSource = fs.readdirSync(avatarPartsDir)
          .filter((name) => name.endsWith('.js'))
          .sort()
          .map((name) => fs.readFileSync(`${{avatarPartsDir}}/${{name}}`, 'utf8'))
          .join('\\n');
        const start = avatarSource.indexOf('function _finishNekoIdleCat1Walk');
        const end = avatarSource.indexOf('function _finishNekoIdleCat1CompactTopEdgeWalk', start);
        assert.ok(start >= 0 && end > start);
        const finishSource = avatarSource.slice(start, end);
        let state = null;
        let playSucceeds = false;
        let randomCalls = 0;
        let playCalls = 0;
        let stretchCalls = 0;
        let observationCalls = 0;
        let randomValue = 0;
        const math = Object.create(Math);
        math.random = () => {{ randomCalls += 1; return randomValue; }};
        const context = {{
          Math: math,
          Date: {{ now: () => 1234 }},
          _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
          _NEKO_IDLE_TIER_CAT1: 'cat1',
          _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY: 0.25,
          _NEKO_CAT_IDLE_OBSERVATION_TYPES: {{ CAT1_WALK_DONE_NEAR_CHAT: 'cat1_walk_done_near_chat' }},
          _getNekoIdleCat1Journey: () => state,
          _cancelNekoIdleCat1Frame: () => {{}},
          _clearNekoIdleCat1WalkApproachSide: () => {{}},
          _getNekoIdleReturnContainerFromButton: () => null,
          _dispatchNekoIdleCat1MotionInputRegionState: () => {{}},
          _resetNekoIdleCat1WalkSpeed: () => {{}},
          _dispatchNekoCatIdleObservationSource: () => {{ observationCalls += 1; }},
          _playNekoIdleCat1PlayAction: () => {{ playCalls += 1; return playSucceeds; }},
          _setNekoIdleCat1Classes: () => {{}},
          _setNekoIdleCat1Substate: (_button, substate) => {{ stretchCalls += 1; state.substate = substate; }},
        }};
        vm.createContext(context);
        vm.runInContext(finishSource, context);
        const button = {{}};
        function reset(value, canPlay) {{
          state = {{
            targetKind: 'minimized-side',
            target: null,
            lastStepAt: 0,
            actionSettled: false,
            walkFinishResolution: '',
            substate: 'walking',
            profile: {{ idleSubstate: 'idle', finishingSubstate: 'stretch' }},
          }};
          randomValue = value;
          playSucceeds = canPlay;
          randomCalls = 0;
          playCalls = 0;
          stretchCalls = 0;
          observationCalls = 0;
        }}

        reset(0.1, true);
        context._finishNekoIdleCat1Walk(button);
        context._finishNekoIdleCat1Walk(button);
        assert.equal(state.walkFinishResolution, 'play');
        assert.equal(randomCalls, 1);
        assert.equal(observationCalls, 1);
        assert.equal(playCalls, 1);
        assert.equal(stretchCalls, 0);

        reset(0.9, true);
        context._finishNekoIdleCat1Walk(button);
        context._finishNekoIdleCat1Walk(button);
        assert.equal(state.walkFinishResolution, 'stretch');
        assert.equal(randomCalls, 1);
        assert.equal(observationCalls, 1);
        assert.equal(playCalls, 0);
        assert.equal(stretchCalls, 1);

        // A runner rejection remains the same arrival's deterministic stretch
        // fallback; it cannot cause a later callback to roll play again.
        reset(0.1, false);
        context._finishNekoIdleCat1Walk(button);
        context._finishNekoIdleCat1Walk(button);
        assert.equal(state.walkFinishResolution, 'stretch');
        assert.equal(randomCalls, 1);
        assert.equal(observationCalls, 1);
        assert.equal(playCalls, 1);
        assert.equal(stretchCalls, 1);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase3_provider_gates_are_stronger_than_legacy_entries():
    source = _read(AVATAR_UI_BUTTONS_PATH)
    debug_source = _read(CAT_MIND_DEBUG_PATH)

    runtime_gate_block = source.split("function _getNekoCatMindRuntimeGateSnapshot", 1)[1].split(
        "function _sanitizeNekoCatIdleObservationDetail",
        1,
    )[0]
    assert "edgePeekActive: tier === _NEKO_IDLE_TIER_CAT1 && _isNekoIdleCat1EdgePeekActive(button)" in runtime_gate_block

    generic_provider_block = source.split("function _dryRunNekoCatMindCat1ButtonProvider", 1)[1].split(
        "function _dryRunNekoCatMindSocialPingProvider",
        1,
    )[0]
    assert "return_pending" in generic_provider_block
    assert "transition_active" in generic_provider_block
    assert "active_independent_action" in generic_provider_block
    assert "compact_surface_dragging" in generic_provider_block
    assert "_isNekoCatMindAudioActionActive()" in generic_provider_block

    provider_evaluator_block = source.split("function _evaluateNekoCatMindActionProvider", 1)[1].split(
        "function _dryRunNekoCatMindCat1ButtonProvider",
        1,
    )[0]
    assert "edgePeekActive: tier === _NEKO_IDLE_TIER_CAT1 && _isNekoIdleCat1EdgePeekActive(button)" in provider_evaluator_block
    assert "else if (facts.edgePeekActive) reason = 'edge_peek_active';" in provider_evaluator_block
    assert "edge_peek_active: '猫咪正在屏幕边缘探头'" in debug_source
    assert "edge_peek_inactive: '猫咪未在屏幕边缘探头'" in debug_source

    social_provider_block = source.split("function _dryRunNekoCatMindSocialPingProvider", 1)[1].split(
        "function _dryRunNekoCatMindPlayYarnProvider",
        1,
    )[0]
    assert "_isNekoIdleCompactSurfaceDragging()" in social_provider_block
    assert "compact_surface_dragging" in social_provider_block
    assert "_dryRunNekoCatMindCat1ButtonProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING" in social_provider_block

    play_provider_block = source.split("function _dryRunNekoCatMindPlayYarnProvider", 1)[1].split(
        "function _dryRunNekoCatMindSleepFeedbackProvider",
        1,
    )[0]
    assert "_isNekoIdleCompactSurfaceDragging()" in play_provider_block
    assert "_isNekoCatMindChatTransitionActive()" in play_provider_block
    assert "_dryRunNekoCatMindCat1ButtonProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN" in play_provider_block
    assert "_isNekoCatMindCat1NearChat(button)" in play_provider_block
    assert "near_chat_unavailable" in play_provider_block
    assert "_canNekoCatMindControlPlayYarn()" in play_provider_block
    assert "play_yarn_unavailable" in play_provider_block

    sleep_provider_block = source.split("function _dryRunNekoCatMindSleepFeedbackProvider", 1)[1].split(
        "function _dryRunNekoCatMindActionProvider",
        1,
    )[0]
    assert "_isAnyNekoCatMindReturnPending()" in sleep_provider_block
    assert "_isNekoCatMindTransitionActive()" in sleep_provider_block
    assert "return_pending" in sleep_provider_block
    assert "transition_active" in sleep_provider_block
    assert "compact_surface_dragging" in sleep_provider_block
    assert "_isNekoCatMindAudioActionActive()" in sleep_provider_block

    dispatch_block = source.split("function _dryRunNekoCatMindActionProvider", 1)[1].split(
        "if (typeof window !== 'undefined')",
        1,
    )[0]
    assert "normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK" in dispatch_block
    assert "decision = _dryRunNekoCatMindPlayYarnProvider(context);" in dispatch_block
    assert "normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN" in dispatch_block
    assert "return _attachNekoCatMindProviderDiagnostics(normalizedActionId, decision, context);" in dispatch_block
    assert "_reportNekoCatMindStateActionResult" not in generic_provider_block + social_provider_block + play_provider_block + sleep_provider_block



def test_cat_mind_phase2_action_result_event_becomes_observation():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}

        let now = 1000;
        const win = new EventTargetLike();
        let actionRequestCount = 0;
        win.addEventListener('neko:cat-mind:action-request', () => {{ actionRequestCount += 1; }});
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ autoGoodbye: false, source: 'manual-goodbye', reason: 'manual-goodbye', timestamp: 1000 }}
        }}));
        const appetiteBefore = win.nekoCatMind.getState().fields.appetite;
        const stimulationBefore = win.nekoCatMind.getState().fields.stimulation_need;

        now = 1100;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: 'cat1_eat_snack',
            result: 'done',
            source: 'unit-runner',
            tier: 'cat1',
            timestamp: 1100,
            reason: 'cat1-eat-action-finished',
            detail: {{ runId: 'eat:1', durationMs: 100 }}
          }}
        }}));
        now = 1200;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: 'cat1_play_yarn',
            result: 'done',
            source: 'unit-runner',
            tier: 'cat1',
            timestamp: 1200,
            reason: 'cat1-play-action-finished',
            detail: {{ runId: 'play:1', durationMs: 100 }}
          }}
        }}));
        const sleepinessBeforeSleepFeedback = win.nekoCatMind.getState().fields.sleepiness;
        now = 1300;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: 'cat3_sleep_feedback',
            result: 'done',
            source: 'unit-runner',
            tier: 'cat3',
            timestamp: 1300,
            reason: 'audio-ended',
            detail: {{ runId: 'sleep:1', durationMs: 100 }}
          }}
        }}));
        now = 1400;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: 'cat1_play_yarn',
            result: 'interrupted',
            source: 'unit-runner',
            tier: 'cat1',
            timestamp: 1400,
            reason: 'return-ball-drag-start',
            detail: {{ runId: 'play:2', durationMs: 20 }}
          }}
        }}));

        const events = win.nekoCatMind.getRecentEvents();
        const eventTypes = events.map((event) => event.type);
        assert.ok(!eventTypes.includes('eat_done'));
        assert.ok(!eventTypes.includes('play_done'));
        assert.ok(!eventTypes.includes('sleep_feedback_done'));
        assert.ok(!eventTypes.includes('action_interrupted_by_drag'));
        assert.equal(win.nekoCatMind.getState().fields.appetite, appetiteBefore);
        assert.equal(win.nekoCatMind.getState().fields.stimulation_need, stimulationBefore);
        assert.equal(win.nekoCatMind.getState().fields.sleepiness, sleepinessBeforeSleepFeedback);
        assert.equal(actionRequestCount, 0);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase2_social_ping_runner_ignores_stale_audio_callbacks():
    script = (
        r"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const avatarPartsDir = __AVATAR_PATH__;
        const avatarSource = fs.readdirSync(avatarPartsDir)
          .filter((name) => name.endsWith('.js'))
          .sort()
          .map((name) => fs.readFileSync(`${avatarPartsDir}/${name}`, 'utf8'))
          .join('\n');
        const catMindSource = fs.readFileSync(__CAT_MIND_PATH__, 'utf8');

        class EventTargetLike {
          constructor() { this.listeners = new Map(); }
          addEventListener(type, handler, options) {
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push({ handler, once: !!(options && options.once) });
          }
          removeEventListener(type, handler) {
            const next = (this.listeners.get(type) || []).filter((item) => item.handler !== handler);
            this.listeners.set(type, next);
          }
          dispatchEvent(event) {
            event.target = event.target || this;
            for (const item of (this.listeners.get(event.type) || []).slice()) {
              item.handler.call(this, event);
              if (item.once) this.removeEventListener(event.type, item.handler);
            }
            return true;
          }
        }
        class CustomEventLike { constructor(type, init = {}) { this.type = type; this.detail = init.detail || {}; } }
        class EventLike { constructor(type) { this.type = type; } }
        class ClassListLike {
          constructor() { this.items = new Set(); }
          add(...names) { names.forEach((name) => this.items.add(name)); }
          remove(...names) { names.forEach((name) => this.items.delete(name)); }
          contains(name) { return this.items.has(name); }
          toggle(name, force) {
            const next = force === undefined ? !this.items.has(name) : !!force;
            if (next) this.items.add(name);
            else this.items.delete(name);
            return next;
          }
        }
        class ElementLike extends EventTargetLike {
          constructor(tag = 'div') {
            super();
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.style = {};
            this.attributes = new Map();
            this.classList = new ClassListLike();
            this.id = '';
            this.isConnected = true;
          }
          setAttribute(name, value) {
            this.attributes.set(name, String(value));
            if (name === 'id') this.id = String(value);
            if (name === 'class') String(value).split(/\s+/).filter(Boolean).forEach((item) => this.classList.add(item));
          }
          getAttribute(name) {
            if (name === 'id') return this.id || null;
            return this.attributes.has(name) ? this.attributes.get(name) : null;
          }
          removeAttribute(name) { this.attributes.delete(name); }
          appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
          remove() {
            this.isConnected = false;
            if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
          }
          matches(selector) {
            if (selector === '.neko-idle-return-btn') return this.classList.contains('neko-idle-return-btn');
            if (selector === '[id$="-return-button-container"]') return /-return-button-container$/.test(this.id);
            if (selector.includes('#live2d-btn-return')) return this.id === 'live2d-btn-return';
            if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
            return false;
          }
          closest(selector) {
            let node = this;
            while (node) {
              if (node.matches && node.matches(selector)) return node;
              node = node.parentNode;
            }
            return null;
          }
          querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
          querySelectorAll(selector) {
            const result = [];
            const visit = (node) => {
              for (const child of node.children) {
                if (child.matches && child.matches(selector)) result.push(child);
                visit(child);
              }
            };
            visit(this);
            return result;
          }
          getBoundingClientRect() { return { left: 0, top: 0, right: 64, bottom: 64, width: 64, height: 64 }; }
        }
        class DocumentLike extends EventTargetLike {
          constructor() {
            super();
            this.body = new ElementLike('body');
            this.head = new ElementLike('head');
            this.hidden = false;
          }
          createElement(tag) { return new ElementLike(tag); }
          querySelectorAll(selector) {
            return [...this.body.querySelectorAll(selector), ...this.head.querySelectorAll(selector)];
          }
          querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        }
        class AudioLike extends EventTargetLike {
          constructor(src) {
            super();
            this.src = src;
            this.paused = false;
            this.ended = false;
            this.currentTime = 0;
            this.duration = 1;
            AudioLike.instances.push(this);
          }
          play() { this.paused = false; return undefined; }
          pause() { this.paused = true; }
        }
        AudioLike.instances = [];

        const document = new DocumentLike();
        const container = new ElementLike('div');
        container.id = 'live2d-return-button-container';
        container.style.display = 'block';
        container.setAttribute('data-dragging', 'false');
        container.setAttribute('data-neko-idle-tier', 'cat1');
        const button = new ElementLike('button');
        button.id = 'live2d-btn-return';
        button.classList.add('neko-idle-return-btn');
        button.setAttribute('data-neko-idle-tier', 'cat1');
        const art = new ElementLike('img');
        art.classList.add('neko-idle-return-art');
        const bubble = new ElementLike('span');
        bubble.classList.add('neko-idle-thought-bubble');
        const bubbleBg = new ElementLike('img');
        bubbleBg.classList.add('neko-idle-thought-bubble-bg');
        const bubbleItem = new ElementLike('img');
        bubbleItem.classList.add('neko-idle-thought-bubble-item');
        bubble.appendChild(bubbleBg);
        bubble.appendChild(bubbleItem);
        button.appendChild(art);
        button.appendChild(bubble);
        container.appendChild(button);
        document.body.appendChild(container);

        const timers = [];
        const timerApi = {
          setTimeout: (callback) => { timers.push(callback); return timers.length; },
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          requestAnimationFrame: () => 1,
          cancelAnimationFrame: () => {},
        };
        const win = new EventTargetLike();
        Object.assign(win, {
          document,
          CustomEvent: CustomEventLike,
          Event: EventLike,
          Audio: AudioLike,
          Image: class {},
          location: { origin: 'http://localhost' },
          NekoDebug: { log() {} },
          ...timerApi,
        });
        const context = {
          window: win,
          document,
          CustomEvent: CustomEventLike,
          Event: EventLike,
          Audio: AudioLike,
          Image: win.Image,
          console,
          Date,
          Math,
          performance: { now: () => 0 },
          ...timerApi,
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(catMindSource, context);
        vm.runInContext(avatarSource, context);

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {
          detail: { autoGoodbye: false, source: 'manual-goodbye', tier: 'cat1', timestamp: 1000 }
        }));

        container.setAttribute('data-dragging', 'pending');
        const pendingDecision = win.NekoCatMindActionProviders.dryRun('cat1_social_ping', { button });
            assert.equal(pendingDecision.allowed, false);
            assert.equal(pendingDecision.reason, 'return_ball_drag_active');
            container.setAttribute('data-dragging', 'false');
            vm.runInContext(`_nekoIdleCat1AmbientSoundState.active = true;`, context);
            button.classList.add('is-cat1-edge-peek-left');
            assert.equal(win.NekoCatMindActionProviders.getRuntimeGateSnapshot().edgePeekActive, true);
            for (const actionId of ['cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move', 'cat1_play_yarn']) {
              const edgeDecision = win.NekoCatMindActionProviders.dryRun(actionId, { button });
              assert.equal(edgeDecision.allowed, false);
              assert.equal(edgeDecision.reason, 'edge_peek_active');
            }
            button.setAttribute('data-neko-idle-tier', 'cat2');
            assert.equal(win.NekoCatMindActionProviders.getRuntimeGateSnapshot().edgePeekActive, false);
            assert.notEqual(win.NekoCatMindActionProviders.dryRun('cat2_nap_feedback', { button }).reason, 'edge_peek_active');
            button.setAttribute('data-neko-idle-tier', 'cat1');
            button.classList.remove('is-cat1-edge-peek-left');
            assert.equal(win.NekoCatMindActionProviders.getRuntimeGateSnapshot().edgePeekActive, false);
            assert.equal(win.NekoCatMindActionProviders.dryRun('cat1_social_ping', { button }).allowed, true);
        vm.runInContext(`_nekoIdleCompactSurfaceDragging = true;`, context);
        const compactEatDecision = win.NekoCatMindActionProviders.dryRun('cat1_eat_snack', { button });
        assert.equal(compactEatDecision.allowed, false);
        assert.equal(compactEatDecision.reason, 'compact_surface_dragging');
        vm.runInContext(`_nekoIdleCompactSurfaceDragging = false; _nekoIdleCat1AmbientSoundState.catMindActionId = 'other-run';`, context);
        const activeAudioEatDecision = win.NekoCatMindActionProviders.dryRun('cat1_eat_snack', { button });
        assert.equal(activeAudioEatDecision.allowed, false);
        assert.equal(activeAudioEatDecision.reason, 'active_independent_action');
        vm.runInContext(`_nekoIdleCat1AmbientSoundState.catMindActionId = '';`, context);

        vm.runInContext(`
          const liveButton = document.querySelector('#live2d-btn-return');
          liveButton.__nekoIdleReturnSubactionState = {
            targetKind: 'minimized-side',
            substate: 'walking',
            actionSettled: false,
            profile: _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW
          };
        `, context);
        const walkingPlayDecision = win.NekoCatMindActionProviders.dryRun('cat1_play_yarn', { button });
        assert.equal(walkingPlayDecision.allowed, false);
        assert.equal(walkingPlayDecision.reason, 'near_chat_unavailable');
        vm.runInContext(`document.querySelector('#live2d-btn-return').__nekoIdleReturnSubactionState = null;`, context);

        vm.runInContext(`
          _nekoIdleCat1AmbientSoundState.active = true;
          _nekoIdleCat1AmbientSoundState.token = 1;
          _playNekoIdleCat1AmbientSound(1);
          _nekoIdleCat1AmbientSoundState.active = true;
          _nekoIdleCat1AmbientSoundState.token = 2;
          _playNekoIdleCat1AmbientSound(2);
        `, context);
        assert.equal(AudioLike.instances.length, 2);

        AudioLike.instances[0].ended = true;
        AudioLike.instances[0].dispatchEvent(new EventLike('ended'));
        const afterOldAudio = win.nekoCatMind.getRecentEvents().filter((event) => event.category === 'action_result');
        assert.equal(afterOldAudio.length, 0);
        assert.equal(button.__nekoIdleThoughtBubbleTier, 'cat1');

        AudioLike.instances[1].ended = true;
        AudioLike.instances[1].dispatchEvent(new EventLike('ended'));
        const afterCurrentAudio = win.nekoCatMind.getRecentEvents().filter((event) => event.category === 'action_result');
        assert.equal(afterCurrentAudio.length, 0);
        """
        .replace("__AVATAR_PATH__", json.dumps(str(AVATAR_UI_BUTTONS_PATH)))
        .replace("__CAT_MIND_PATH__", json.dumps(str(CAT_MIND_PATH)))
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_runtime_observes_without_dispatching_actions():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{
            this.listeners = new Map();
          }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            event.target = this;
            const handlers = this.listeners.get(event.type) || [];
            for (const handler of handlers.slice()) {{
              handler.call(this, event);
            }}
            return true;
          }}
        }}

        class CustomEventLike {{
          constructor(type, init = {{}}) {{
            this.type = type;
            this.detail = init.detail;
          }}
        }}

        let now = 1000;
        const win = new EventTargetLike();
        win.__NEKO_CAT_MIND_DEBUG__ = true;
        const context = {{
          window: win,
          CustomEvent: CustomEventLike,
          Date: {{ now: () => now }},
          console,
        }};
        vm.createContext(context);
        vm.runInContext(source, context);

        const stateChanges = [];
        const returnSummaries = [];
        let actionRequests = 0;
        win.addEventListener('neko:cat-mind:state-change', (event) => stateChanges.push(event.detail));
        win.addEventListener('neko:cat-mind:return-summary', (event) => returnSummaries.push(event.detail));
        win.addEventListener('neko:cat-mind:action-request', () => {{ actionRequests += 1; }});

        assert.ok(win.NekoCatMindContract);
        assert.ok(win.nekoCatMind);
        assert.equal(typeof win.nekoCatMind.getState, 'function');
        assert.equal(typeof win.nekoCatMind.getRecentEvents, 'function');
        assert.equal(typeof win.nekoCatMind.observe, 'function');
        assert.equal(typeof win.nekoCatMind.reset, 'function');

        assert.equal(win.nekoCatMind.getState().active, false);

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ autoGoodbye: true, source: 'auto-goodbye', reason: 'idle-timeout' }}
        }}));
        let state = win.nekoCatMind.getState();
        assert.equal(state.active, true);
        assert.equal(state.entry, 'auto');
        assert.equal(state.tier, 'cat1');
        for (const field of ['appetite', 'sleepiness', 'energy', 'social_need', 'stimulation_need']) {{
          assert.equal(typeof state.fields[field], 'number');
        }}
        assert.equal(JSON.stringify(win.nekoCatMind.getRecentEvents().map((event) => event.type)), JSON.stringify(['cat_entered']));

        now = 2000;
        win.dispatchEvent(new CustomEventLike('neko:auto-goodbye:state-change', {{
          detail: {{ type: 'visual-tier', tier: 'cat2', source: 'timer', reason: 'elapsed', timestamp: 2000 }}
        }}));
        state = win.nekoCatMind.getState();
        assert.equal(state.tier, 'cat2');
        assert.ok(state.fields.sleepiness >= 0.55);

        win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
          detail: {{ reason: 'return-ball-drag-start', timestamp: 2100 }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
          detail: {{ reason: 'return-ball-drag-active', timestamp: 2150 }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
          detail: {{ reason: 'return-ball-drag-end', movedDistancePx: 42, timestamp: 2200 }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:thought-bubble-pop', {{
          detail: {{ source: 'click', tier: 'cat2', button: {{ nodeType: 1 }}, originalEvent: {{ type: 'click' }}, timestamp: 2300 }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{
            type: 'cat_hover_reaction',
            source: 'return-ball-hover',
            tier: 'cat2',
            timestamp: 2400,
            detail: {{ reason: 'mouseenter' }}
          }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{
            type: 'cat1_walk_done_near_chat',
            source: 'cat1-journey',
            tier: 'cat1',
            timestamp: 2500,
            detail: {{ reason: 'walk-finish', targetKind: 'minimized-side' }}
          }}
        }}));

        const eventTypes = win.nekoCatMind.getRecentEvents().map((event) => event.type);
        assert.ok(eventTypes.includes('drag_start'));
        assert.ok(eventTypes.includes('drag_end'));
        assert.ok(eventTypes.includes('thought_bubble_pop'));
        assert.ok(eventTypes.includes('cat_hover_reaction'));
        assert.ok(eventTypes.includes('cat1_walk_done_near_chat'));
        assert.equal(eventTypes.filter((type) => type === 'drag_start').length, 1);
        const bubbleEvent = win.nekoCatMind.getRecentEvents().find((event) => event.type === 'thought_bubble_pop');
        assert.equal(Object.prototype.hasOwnProperty.call(bubbleEvent.detail, 'button'), false);
        assert.equal(Object.prototype.hasOwnProperty.call(bubbleEvent.detail, 'originalEvent'), false);

        const decision = win.nekoCatMind.recordDecision({{
          trigger: 'unit-test',
          outcome: 'cat1_eat_snack',
          reason: 'candidate-ready',
          candidates: [
            {{ actionId: 'cat1_eat_snack', score: 12.5, allowed: true }},
            {{ actionId: 'cat1_play_yarn', score: null, allowed: false, reason: 'near_chat_unavailable' }}
          ]
        }});
        assert.equal(decision.outcome, 'cat1_eat_snack');
        const debugSnapshot = win.nekoCatMind.getDebugSnapshot();
        assert.equal(debugSnapshot.lastDecision.candidates[0].score, 12.5);
        assert.equal(debugSnapshot.lastDecision.candidates[1].reason, 'near_chat_unavailable');
        assert.ok(stateChanges.some((change) => change.reason === 'observation'));
        assert.ok(stateChanges.some((change) => change.reason === 'decision'));

        win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        state = win.nekoCatMind.getState();
        assert.equal(state.active, false);
        assert.ok(state.returnSummaryDraft);
        assert.equal(state.returnSummaryDraft.entry, 'auto');
        assert.equal(state.returnSummaryDraft.final_tier, 'cat2');
        assert.equal(state.tier, 'none');
        assert.equal(win.nekoCatMind.getRecentEvents().length, 0);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision, null);
        assert.equal(returnSummaries.length, 1);
        assert.equal(returnSummaries[0].summary.final_tier, 'cat2');
        assert.equal(actionRequests, 0);

        win.nekoCatMind.reset('unit-test');
        assert.equal(win.nekoCatMind.getState().active, false);
        assert.equal(win.nekoCatMind.getRecentEvents().length, 0);

        now = 3000;
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ startupDefaultForm: 'cat', source: 'startup-default-form', reason: 'startup-default-cat' }}
        }}));
        state = win.nekoCatMind.getState();
        assert.equal(state.active, true);
        assert.equal(state.entry, 'auto');
        assert.equal(state.fields.appetite, 0.32);
        assert.equal(state.fields.sleepiness, 0.22);
        assert.equal(state.fields.energy, 0.66);
        assert.equal(state.fields.social_need, 0.32);
        assert.equal(state.fields.stimulation_need, 0.42);
        const startupEntry = win.nekoCatMind.getRecentEvents()[0];
        assert.equal(startupEntry.source, 'startup-default-form');
        assert.equal(startupEntry.detail.entry, 'auto');
        assert.equal(startupEntry.detail.reason, 'startup-default-cat');
        assert.equal(startupEntry.detail.startupDefaultForm, 'cat');
        now += 1000;
        win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        assert.equal(win.nekoCatMind.getReturnSummaryDraft().entry, 'auto');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_debug_overlay_is_opt_in_and_selector_ready():
    source = _read(CAT_MIND_DEBUG_PATH)

    assert "neko.catMind.debug" in source
    assert "getDebugSnapshot" in source
    assert "lastDecision" in source
    assert "动作评分（需求余量＋节奏＋短时意图－本动作冷却）" in source
    assert "理论基础分：" in source
    assert "需求余量：" in source
    assert "统一节奏分：" in source
    assert "可比效用：" in source
    assert "本轮可用分（仅 provider 允许后计算）：" in source
    assert "动作条件依据：" in source
    assert "现场还不满足：" in source
    assert "not_compact_top_edge" in source
    assert "hover_inactive" in source
    assert "五维状态（0–100）" in source
    assert "回归经历归并（仅本地调试）" in source
    assert "返回预览：" in source
    assert "闭环记录（仅调试，保留最近" in source
    assert "状态机请求" in source
    assert "动作回执" in source
    assert "回归摘要" in source
    assert "问候附件" in source
    assert "阈值：" in source
    assert "本轮候选：" in source
    assert "neko:cat-mind:action-request" not in source


def test_cat_mind_debug_setting_can_use_query_local_storage_or_explicit_global():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          addEventListener() {{}}
          dispatchEvent() {{ return true; }}
        }}
        const win = new EventTargetLike();
        win.location = {{ search: '' }};
        win.localStorage = {{ getItem(name) {{ return name === 'neko.catMind.debug' ? 'true' : null; }} }};
        const context = {{ window: win, CustomEvent: class {{}}, Date, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        assert.equal(win.NekoCatMindContract.DEBUG_SETTING_KEY, 'neko.catMind.debug');
        assert.equal(win.NekoCatMindContract.DEBUG_QUERY_PARAM, 'cat_mind_debug');
        assert.equal(win.NekoCatMindContract.isDebugEnabled(), true);
        win.__NEKO_CAT_MIND_DEBUG__ = false;
        assert.equal(win.NekoCatMindContract.isDebugEnabled(), false);
        win.location.search = '?cat_mind_debug=1';
        assert.equal(win.NekoCatMindContract.isDebugEnabled(), true);
        win.location.search = '?cat_mind_debug=0';
        assert.equal(win.NekoCatMindContract.isDebugEnabled(), false);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_debug_overlay_is_absent_by_default_and_renders_when_enabled():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const mindSource = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        const debugSource = fs.readFileSync({json.dumps(str(CAT_MIND_DEBUG_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        class ElementLike {{
          constructor(tag) {{ this.tagName = tag; this.children = []; this.style = {{}}; this.textContent = ''; this.id = ''; }}
          appendChild(child) {{ this.children.push(child); return child; }}
          setAttribute() {{}}
          addEventListener(type, handler) {{ this['on' + type] = handler; }}
        }}
        class DocumentLike extends EventTargetLike {{
          constructor() {{ super(); this.readyState = 'complete'; this.head = new ElementLike('head'); this.body = new ElementLike('body'); }}
          createElement(tag) {{ return new ElementLike(tag); }}
        }}
        function run(debugEnabled) {{
          const win = new EventTargetLike();
          const document = new DocumentLike();
          win.location = {{ search: '' }};
          win.localStorage = {{ getItem(name) {{ return name === 'neko.catMind.debug' && debugEnabled ? 'true' : null; }} }};
          win.setInterval = () => 7;
          win.clearInterval = () => {{}};
          const context = {{ window: win, document, CustomEvent: CustomEventLike, Date, console }};
          vm.createContext(context);
          vm.runInContext(mindSource, context);
          vm.runInContext(debugSource, context);
          return {{ win, document }};
        }}
        const disabled = run(false);
        assert.equal(disabled.document.body.children.length, 0);
        const enabled = run(true);
        assert.equal(enabled.document.body.children.length, 1);
        const panel = enabled.document.body.children[0];
        assert.equal(panel.id, 'neko-cat-mind-debug-panel');
        enabled.win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{ detail: {{ source: 'manual-goodbye' }} }}));
        const panelBody = panel.children[2];
        assert.match(panelBody.textContent, /五维状态/);
        assert.match(panelBody.textContent, /状态：猫形态运行中/);
        assert.match(panelBody.textContent, /调度状态：/);
        assert.match(panelBody.textContent, /动作评分（需求余量＋节奏＋短时意图－本动作冷却）/);
        assert.match(panelBody.textContent, /【CAT1 吃零食】/);
        assert.match(panelBody.textContent, /需求余量：/);
        assert.match(panelBody.textContent, /统一节奏分：/);
        assert.match(panelBody.textContent, /短时意图：/);
        assert.match(panelBody.textContent, /可比效用：/);
        assert.match(panelBody.textContent, /动作条件依据/);
        assert.match(panelBody.textContent, /回归经历归并/);
        assert.match(panelBody.textContent, /返回预览：当前没有可带回的经历/);
        const hideButton = panel.children[0].children[0];
        assert.equal(hideButton.textContent, '隐藏');
        hideButton.onclick();
        assert.equal(panel.style.display, 'none');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_selector_defers_provider_checks_and_requests_only_after_gates():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        let now = 1000;
        const timers = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        let providerCalls = 0;
        let actionRequests = 0;
        let actionResults = 0;
        let activeHardGate = '';
        win.addEventListener('neko:cat-mind:action-request', () => {{ actionRequests += 1; }});
        win.addEventListener('neko:cat-mind:action-result', () => {{ actionResults += 1; }});
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{ detail: {{ source: 'manual-goodbye' }} }}));
        assert.equal(providerCalls, 0);
        assert.equal(timers.length, 1);
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            const gates = {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false, edgePeekActive: false }};
            if (activeHardGate === 'returnBallInvisible') gates.returnBallVisible = false;
            else if (activeHardGate === 'invalidCatRuntime') gates.validCatRuntime = false;
            else if (activeHardGate) gates[activeHardGate] = true;
            return gates;
          }},
          dryRun(actionId) {{
            providerCalls += 1;
            return actionId === 'cat1_play_yarn'
              ? {{ allowed: false, reason: 'near_chat_unavailable' }}
              : {{ allowed: true, reason: 'allowed' }};
            }}
        }};
        now += 60 * 60 * 1000;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat_elapsed', source: 'cat-mind-clock', tier: 'cat1', timestamp: now,
            detail: {{ elapsedMs: 60 * 60 * 1000 }} }}
        }}));
        timers.shift()();
        const decision = win.nekoCatMind.getDebugSnapshot().lastDecision;
        assert.equal(providerCalls, 4);
            assert.equal(decision.reason, 'action_request_dispatched');
        assert.ok(decision.candidates.some((item) => item.actionId === 'cat1_eat_snack' && item.reason === 'allowed'));
        assert.ok(decision.candidates.some((item) => item.actionId === 'cat1_play_yarn' && item.reason === 'near_chat_unavailable'));
        assert.ok(decision.candidates.some((item) => item.actionId === 'cat2_nap_feedback' && item.reason === 'tier_not_allowed'));
        const rejectedPlay = decision.candidates.find((item) => item.actionId === 'cat1_play_yarn');
        assert.equal(rejectedPlay.score, null);
        assert.equal(rejectedPlay.baseScore, null);
        assert.equal(Object.keys(rejectedPlay.providerDetail).length, 0);
            assert.equal(actionRequests, 1);
            assert.equal(actionResults, 0);
            const firstRequest = win.nekoCatMind.getState().pendingActionRequest;
            assert.ok(firstRequest);
            assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
              requestId: firstRequest.requestId,
              actionId: firstRequest.actionId,
              status: 'rejected',
              reason: 'unit-no-adapter',
              timestamp: now
            }}), true);
        for (const [gate, reason] of [
          ['returnPending', 'return_pending'],
          ['dragPending', 'drag_pending'],
          ['dragging', 'dragging'],
          ['edgePeekActive', 'edge_peek_active'],
          ['transitionActive', 'transition_active'],
          ['activeIndependentAction', 'active_independent_action'],
          ['returnBallInvisible', 'return_ball_not_visible'],
          ['invalidCatRuntime', 'invalid_cat_runtime'],
          ['chatSurfaceDragging', 'chat_surface_dragging']
        ]) {{
          activeHardGate = gate;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
            detail: {{ type: gate === 'edgePeekActive' ? 'edge_peek_after_drag' : 'cat_hover_reaction',
              source: 'unit-test', tier: 'cat1', timestamp: ++now, detail: {{ reason: gate }} }}
          }}));
          assert.equal(timers.length, 1);
          timers.shift()();
          assert.equal(providerCalls, 4);
          assert.equal(actionRequests, 1);
          const gated = win.nekoCatMind.getDebugSnapshot().lastDecision;
          assert.equal(gated.outcome, 'quiet');
          assert.equal(gated.reason, reason);
        }}
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_deduplicates_same_timestamp_observations():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => 1000 }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'poll', minimized: true, timestamp: 1234, screenRect: {{ left: 1, top: 2, width: 3, height: 4 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'poll', minimized: true, timestamp: 1234, screenRect: {{ left: 1, top: 2, width: 3, height: 4 }} }}
        }}));
        const events = win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_minimized_visible');
        assert.equal(events.length, 1);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_treats_unchanged_desktop_polls_as_heartbeats():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => 1000 }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
        const minimized = (timestamp, left = 1) => win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'poll', minimized: true, timestamp, screenRect: {{ left, top: 2, width: 80, height: 80 }} }}
        }}));
        const expanded = (timestamp) => win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'poll', minimized: false, timestamp }}
        }}));

        // The first desktop state is still meaningful; only its repeats are heartbeats.
        expanded(1050);
        const afterInitialExpanded = win.nekoCatMind.getState().fields;
        expanded(1075);
        assert.deepEqual(win.nekoCatMind.getState().fields, afterInitialExpanded);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_expanded').length, 1);

        minimized(1100);
        const afterFirstMinimized = win.nekoCatMind.getState().fields;
        minimized(2100);
        minimized(3100);
        assert.deepEqual(win.nekoCatMind.getState().fields, afterFirstMinimized);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_minimized_visible').length, 1);

        // The native bridge and BroadcastChannel can use different reasons
        // for the same rect. Neither a pointer sync nor its forwarded copy is
        // a new window experience.
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'neko-pc', reason: 'pointer', minimized: true, timestamp: 3200, screenRect: {{ left: 1, top: 2, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', via: 'broadcast-channel', reason: 'pointer', minimized: true, timestamp: 3250, screenRect: {{ left: 1, top: 2, width: 80, height: 80 }} }}
        }}));
        assert.deepEqual(win.nekoCatMind.getState().fields, afterFirstMinimized);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_minimized_visible').length, 1);

        // A changed rect remains a real observation, even when delivered by poll.
        minimized(4100, 4);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_minimized_visible').length, 2);

        expanded(5100);
        const afterFirstExpanded = win.nekoCatMind.getState().fields;
        expanded(6100);
        assert.deepEqual(win.nekoCatMind.getState().fields, afterFirstExpanded);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_expanded').length, 2);

        // Explicit dock entry remains an observation; it is not a poll heartbeat.
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'idle-dock-enter', minimized: true, timestamp: 7100, screenRect: {{ left: 4, top: 2, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'neko-pc', reason: 'idle-dock-enter', minimized: true, timestamp: 7150, screenRect: {{ left: 4, top: 2, width: 80, height: 80 }} }}
        }}));
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_idle_docked_near_cat').length, 1);
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'neko-pc', reason: 'idle-dock-exit', minimized: true, timestamp: 7200, screenRect: {{ left: 4, top: 2, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'idle-dock-enter', minimized: true, timestamp: 7300, screenRect: {{ left: 4, top: 2, width: 80, height: 80 }} }}
        }}));
        assert.equal(win.nekoCatMind.getRecentEvents().filter((event) => event.type === 'chat_idle_docked_near_cat').length, 2);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_filters_noisy_or_unknown_observations():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => 1000 }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
        win.dispatchEvent(new CustomEventLike('neko:thought-bubble-pop', {{
          detail: {{ source: 'click', timestamp: 1100 }}
        }}));
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'duplicate', timestamp: 1200 }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'made_up_event', source: 'unit-test', timestamp: 1300, detail: {{ reason: 'unknown' }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-compact-surface-state', {{
          detail: {{ source: 'compact-surface', visible: true, heartbeat: true, timestamp: 1400, screenRect: {{ left: 1, top: 2, width: 300, height: 200 }} }}
        }}));
        const events = win.nekoCatMind.getRecentEvents();
        assert.ok(events.some((event) => event.type === 'thought_bubble_pop'));
        assert.equal(events.filter((event) => event.type === 'cat_entered').length, 1);
        assert.equal(events.some((event) => event.type === 'made_up_event'), false);
        assert.equal(events.some((event) => event.type === 'chat_compact_surface_visible'), false);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_maps_desktop_observation_edges():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => 1000 }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'poll', minimized: true, timestamp: 1100, screenRect: {{ left: 0, top: 0, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'pointer', minimized: true, timestamp: 1200, screenRect: {{ left: 100, top: 0, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-chat-minimized-state', {{
          detail: {{ source: 'chat-window', reason: 'idle-dock-enter', minimized: true, timestamp: 1300, screenRect: {{ left: 110, top: 0, width: 80, height: 80 }} }}
        }}));
        win.dispatchEvent(new CustomEventLike('neko:idle-return-ball-state', {{
          detail: {{ source: 'pet-window', reason: 'visual-tier', visible: true, tier: 'cat2', timestamp: 1400, screenRect: {{ left: 10, top: 10, width: 64, height: 64 }} }}
        }}));
        const events = win.nekoCatMind.getRecentEvents();
        const types = events.map((event) => event.type);
        assert.ok(types.includes('chat_minimized_visible'));
        assert.ok(types.includes('chat_minimized_moved_far'));
        assert.ok(types.includes('chat_idle_docked_near_cat'));
        assert.ok(types.includes('desktop_occlusion_or_layer_change'));
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase1_pngtuber_return_click_is_observation_only():
    source = _read(CAT_MIND_PATH)

    assert "'pngtuber-return-click'" in source

    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        const summaries = [];
        win.addEventListener('neko:cat-mind:return-summary', (event) => summaries.push(event.detail));
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ autoGoodbye: false, source: 'manual-goodbye', reason: 'manual-goodbye' }}
        }}));
        now = 2000;
        win.dispatchEvent(new CustomEventLike('pngtuber-return-click'));
        const state = win.nekoCatMind.getState();
        assert.equal(state.active, false);
        assert.equal(state.returnSummaryDraft, null);
        assert.equal(win.nekoCatMind.consumeReturnSummaryDraft(), null);
        assert.equal(win.nekoCatMind.getRecentEvents().length, 0);
        assert.equal(summaries.length, 1);
        assert.equal(summaries[0].source, 'pngtuber-return-click');
        assert.equal(summaries[0].summary.entry, 'manual');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_selector_dispatches_one_async_request_and_records_started_lifecycle():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }} }}
        let now = 1000;
        const timers = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        let providerAllowsEat = false;
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_eat_snack' && providerAllowsEat
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_enabled_for_test' }};
          }}
        }};
        const requests = [];
        let actionResults = 0;
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
        win.addEventListener('neko:cat-mind:action-result', () => {{ actionResults += 1; }});
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        assert.equal(requests.length, 0);
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 0);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest, null);
        assert.equal(win.nekoCatMind.getState().activeAction, null);
        assert.equal(Object.keys(win.nekoCatMind.getState().actionCooldowns).length, 0);
        assert.equal(actionResults, 0);

        providerAllowsEat = true;
        now += 60 * 60 * 1000;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat_elapsed', source: 'cat-mind-clock', tier: 'cat1', timestamp: now,
            detail: {{ elapsedMs: 60 * 60 * 1000 }} }}
        }}));
        assert.equal(requests.length, 0);
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 1);
        const first = requests[0];
        assert.equal(first.source, 'cat_mind');
        assert.equal(first.actionId, 'cat1_eat_snack');
        assert.equal(first.tier, 'cat1');
        assert.equal(typeof first.requestId, 'string');
        assert.equal(first.detail.button, undefined);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest.requestId, first.requestId);
        assert.equal(Object.keys(win.nekoCatMind.getState().actionCooldowns).length, 0);

        now += 50;
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: first.requestId,
          actionId: first.actionId,
          status: 'rejected',
          reason: 'provider_changed_before_run',
          timestamp: now,
        }}), true);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest, null);
        assert.equal(Object.keys(win.nekoCatMind.getState().actionCooldowns).length, 0);
        assert.equal(actionResults, 0);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.execution.state, 'rejected');

        now += 100;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'thought_bubble_pop', source: 'unit-test', tier: 'cat1', timestamp: now, detail: {{}} }}
        }}));
        assert.equal(requests.length, 1);
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 2);
        const second = requests[1];

        now += 100;
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: second.requestId,
          actionId: second.actionId,
          status: 'started',
          reason: 'missing_run_id',
          timestamp: now,
        }}), false);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest.requestId, second.requestId);
        assert.equal(Object.keys(win.nekoCatMind.getState().actionCooldowns).length, 0);
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: second.requestId,
          actionId: second.actionId,
          status: 'started',
          reason: 'started_before_accepted',
          runId: 'unit-run-1',
          timestamp: now,
        }}), false);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest.requestId, second.requestId);
        assert.equal(Object.keys(win.nekoCatMind.getState().actionCooldowns).length, 0);
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: second.requestId,
          actionId: second.actionId,
          status: 'accepted',
          reason: 'runner_bound',
          runId: 'unit-run-1',
          timestamp: now,
        }}), true);
        assert.equal(win.nekoCatMind.getState().pendingActionRequest.runId, 'unit-run-1');
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: second.requestId,
          actionId: second.actionId,
          status: 'started',
          reason: 'runner_started',
          runId: 'unit-run-1',
          timestamp: now,
        }}), true);
        const started = win.nekoCatMind.getState();
        assert.equal(started.activeAction.requestId, second.requestId);
        assert.equal(started.actionCooldowns.cat1_eat_snack.startedAt, now);

        now += 50;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat_hover_reaction', source: 'unit-test', tier: 'cat1', timestamp: now, detail: {{}} }}
        }}));
        assert.equal(requests.length, 2);
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 2);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'active_action_pending');

        now += 50;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: second.actionId,
            result: 'done',
            source: 'cat_mind',
            tier: 'cat1',
            timestamp: now,
            reason: 'stale_result',
            detail: {{ requestId: second.requestId, runId: 'stale-run' }}
          }}
        }}));
        assert.equal(win.nekoCatMind.getState().activeAction.requestId, second.requestId);
        assert.equal(win.nekoCatMind.getRecentEvents().some((item) => item.type === 'eat_done'), false);
        assert.equal(win.nekoCatMind.getDebugSnapshot().scheduler.lastIgnoredActionResult.reason, 'unmatched_or_nonterminal_result');
        now += 50;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: second.actionId,
            result: 'started',
            source: 'cat_mind',
            tier: 'cat1',
            timestamp: now,
            reason: 'non_terminal',
            detail: {{ requestId: second.requestId, runId: 'unit-run-1' }}
          }}
        }}));
        assert.equal(win.nekoCatMind.getState().activeAction.requestId, second.requestId);
        assert.equal(win.nekoCatMind.getRecentEvents().some((item) => item.type === 'eat_done'), false);

        now += 50;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: second.actionId,
            result: 'done',
            source: 'cat_mind',
            tier: 'cat1',
            timestamp: now,
            reason: 'restore_complete',
            detail: {{ requestId: second.requestId, runId: 'unit-run-1' }}
          }}
        }}));
        assert.equal(win.nekoCatMind.getState().activeAction, null);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.execution.state, 'result');
        assert.equal(win.nekoCatMind.getRecentEvents().filter((item) => item.type === 'eat_done').length, 1);

        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'post_action_settle');
        now += 100;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat_hover_reaction', source: 'unit-test', tier: 'cat1', timestamp: now, detail: {{}} }}
        }}));
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 2);
        const cooledCandidate = win.nekoCatMind.getDebugSnapshot().lastDecision.candidates.find(
          (item) => item.actionId === 'cat1_eat_snack'
        );
        assert.equal(cooledCandidate.cooldownApplied, true);
        assert.ok(cooledCandidate.cooldownPenalty > 0);
        assert.ok(Math.abs(cooledCandidate.utilityScore - (
          cooledCandidate.needContribution + cooledCandidate.cadenceAdjustment +
            cooledCandidate.intentContribution - cooledCandidate.cooldownPenalty
        )) < 0.02);
        assert.ok(Math.abs(cooledCandidate.score - (
          cooledCandidate.threshold + cooledCandidate.utilityScore
        )) < 0.02);

        now += 60 * 60 * 1000;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'cat_elapsed', source: 'cat-mind-clock', tier: 'cat1', timestamp: now,
            detail: {{ elapsedMs: 60 * 60 * 1000 }} }}
        }}));
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 3);
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: requests[2].requestId,
          actionId: requests[2].actionId,
          status: 'rejected',
          reason: 'test_cleanup',
          timestamp: now,
        }}), true);

        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_small_move_uses_started_lifecycle_and_completion_feedback():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const timers = [];
        const intervals = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        win.setInterval = (callback, delayMs) => {{ intervals.push({{ callback, delayMs }}); return intervals.length; }};
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_small_move'
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_enabled_for_test' }};
          }}
        }};
        const requests = [];
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        assert.equal(intervals.length, 1);
        timers.shift()();
        assert.equal(requests.length, 0);

        now += 7.5 * 60 * 1000;
        intervals[0].callback();
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 1);
        const request = requests[0];
        assert.equal(request.actionId, 'cat1_small_move');
        assert.equal(win.nekoCatMind.getState().actionCooldowns.cat1_small_move, undefined);

        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: request.requestId,
          actionId: request.actionId,
          status: 'accepted',
          runId: 'small-move-run',
          timestamp: now,
        }}), true);
        assert.equal(win.nekoCatMind.getState().actionCooldowns.cat1_small_move, undefined);
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: request.requestId,
          actionId: request.actionId,
          status: 'started',
          runId: 'small-move-run',
          timestamp: now,
        }}), true);
        const started = win.nekoCatMind.getState();
        assert.equal(started.activeAction.actionId, 'cat1_small_move');
        assert.ok(started.actionCooldowns.cat1_small_move);
        const beforeDone = started.fields;

        now += 600;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{
            actionId: 'cat1_small_move', result: 'done', source: 'cat_mind', tier: 'cat1',
            timestamp: now, reason: 'restore_complete',
            detail: {{ requestId: request.requestId, runId: 'small-move-run', restored: true,
              activityId: 'small-move-run', pathDistancePx: 96, distancePx: 96,
              durationMs: 1320, plannedDurationMs: 1320 }}
          }}
        }}));
        const finished = win.nekoCatMind.getState();
        assert.equal(finished.activeAction, null);
        assert.ok(finished.actionCooldowns.cat1_small_move);
        assert.equal(win.nekoCatMind.getRecentEvents().filter((item) => item.type === 'small_move_done').length, 1);
        const physicalLoad = 0.6 * 0.6 * (3 - 2 * 0.6);
        assert.ok(Math.abs(finished.fields.stimulation_need - Math.max(0, beforeDone.stimulation_need - 0.14)) < 1e-9);
        assert.ok(Math.abs(finished.fields.appetite - Math.min(1, beforeDone.appetite + 0.04 * physicalLoad)) < 1e-9);
        assert.ok(Math.abs(finished.fields.energy - Math.max(0, beforeDone.energy - 0.045 * physicalLoad)) < 1e-9);
        assert.ok(Math.abs(finished.fields.sleepiness - Math.min(1, beforeDone.sleepiness + 0.02 * physicalLoad)) < 1e-9);
        assert.equal(finished.fields.social_need, beforeDone.social_need);

        win.nekoCatMind.reset('next-entry');
        timers.length = 0;
        now += 1000;
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'auto-goodbye', autoGoodbye: true, timestamp: now }}
        }}));
        timers.shift()();
        now += 5.5 * 60 * 1000;
        intervals[1].callback();
        timers.shift()();
        assert.equal(requests.length, 2);
        assert.equal(requests[1].actionId, 'cat1_small_move');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_autonomous_clock_uses_elapsed_time_thresholds_and_stops_on_return():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const timers = [];
        const intervals = new Map();
        const clearedIntervals = [];
        let nextIntervalId = 1;
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        win.setInterval = (callback, delayMs) => {{
          const id = nextIntervalId++;
          intervals.set(id, {{ callback, delayMs }});
          return id;
        }};
        win.clearInterval = (id) => {{ clearedIntervals.push(id); intervals.delete(id); }};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_social_ping'
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_enabled_for_test' }};
          }}
        }};
        const requests = [];
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        assert.equal(intervals.size, 1);
        const clock = [...intervals.values()][0];
        assert.equal(clock.delayMs, 30000);
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 0);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'below_action_threshold');

        now = 1100;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'thought_bubble_pop', source: 'unit-test', tier: 'cat1', timestamp: now, detail: {{}} }}
        }}));
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 0);
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'below_action_threshold');

        // A clicked bubble satisfies need instead of boosting another response.
        // With every other provider disabled, the shared cadence score can
        // still make the remaining legal action eligible after real idle time.
        now = 421100;
        clock.callback();
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 1);
        const state = win.nekoCatMind.getState();
        assert.ok(state.fields.social_need < 0.5);
        assert.equal(win.nekoCatMind.getRecentEvents().some((item) => item.type === 'cat_elapsed'), false);
        const decision = win.nekoCatMind.getDebugSnapshot().lastDecision;
        assert.ok(decision.triggerTypes.includes('cat_elapsed'));
        assert.ok(decision.triggerTypes.includes('inactive_elapsed'));
        assert.ok(decision.triggerTypes.includes('since_last_action'));
        const social = decision.candidates.find((item) => item.actionId === 'cat1_social_ping');
        assert.equal(social.allowed, true);
        assert.ok(social.score >= social.threshold);

        win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        assert.equal(win.nekoCatMind.getState().active, false);
        assert.equal(clearedIntervals.length, 1);
        const timersBeforeStaleClock = timers.length;
        clock.callback();
        assert.equal(timers.length, timersBeforeStaleClock);

        timers.length = 0;
        now = 500000;
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        const secondClock = [...intervals.values()][0];
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 1);
        now += 12 * 60 * 1000;
        secondClock.callback();
        assert.equal(timers.length, 1);
        timers.shift()();
        assert.equal(requests.length, 2);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_cat1_score_feedback_forms_a_bounded_near_chat_cycle():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}

        const RUNNER_TIMINGS = Object.freeze({{
          cat1_social_ping: 2040,
          cat1_eat_snack: 4170,
          cat1_small_move: 1320,
          cat1_play_yarn: 3900,
        }});
        function resolveRunnerDuration(actionId, profile) {{
          if (actionId === 'cat1_social_ping' && profile.socialDurationMs) return profile.socialDurationMs;
          if (actionId === 'cat1_small_move' && profile.smallMoveDurationMs) return profile.smallMoveDurationMs;
          return Number(profile.runnerDurations && profile.runnerDurations[actionId]) ||
            Number(RUNNER_TIMINGS[actionId]) || 1000;
        }}

        function simulate(nearChat, profile = {{}}) {{
          let now = 1000;
          const entryAt = now;
          const timers = [];
          const intervals = [];
          const win = new EventTargetLike();
          win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
          win.setInterval = (callback, delayMs) => {{ intervals.push({{ callback, delayMs }}); return intervals.length; }};
          win.clearInterval = () => {{}};
          const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
          vm.createContext(context);
          vm.runInContext(source, context);
          const runtimeGates = {{ returnPending: false, dragPending: false, dragging: false,
            transitionActive: false, activeIndependentAction: false, returnBallVisible: true,
            validCatRuntime: true, chatSurfaceDragging: false }};
          let providerNearChatAvailable = nearChat;
          win.NekoCatMindActionProviders = {{
            getRuntimeGateSnapshot() {{
              return {{ ...runtimeGates }};
            }},
            dryRun(actionId) {{
              const allowed = providerNearChatAvailable ||
                actionId === 'cat1_social_ping' || actionId === 'cat1_eat_snack';
              return {{ allowed, reason: allowed ? 'allowed' : 'near_chat_required' }};
            }},
          }};
          const counts = {{}};
          const starts = [];
          const pendingResults = [];
          const terminals = [];
          const bubblePops = [];
          let runNumber = 0;
          let dragActivityNumber = 0;
          let currentDragWasRapid = false;
          const observe = (type, timestamp = now, suppliedDetail = null) => {{
            if (type === 'drag_start') currentDragWasRapid = false;
            if (type === 'rapid_drag') currentDragWasRapid = true;
            let detail = suppliedDetail || {{}};
            if ((type === 'drag_end' || type === 'drag_cancelled') && !suppliedDetail) {{
              dragActivityNumber += 1;
              detail = {{
                activityId: 'interaction-matrix-drag-' + dragActivityNumber,
                pathDistancePx: currentDragWasRapid ? 160 : 110,
                displacementPx: currentDragWasRapid ? 125 : 85,
                durationMs: currentDragWasRapid ? 1050 : 850,
              }};
              currentDragWasRapid = false;
            }}
            return win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
              detail: {{ type, source: 'interaction-matrix', tier: 'cat1', timestamp, detail }},
            }}));
          }};
          const reportActionResult = (request, runId, timestamp, result, reason) => {{
            runtimeGates.activeIndependentAction = false;
            const resultDetail = {{ requestId: request.requestId, runId }};
            if (result === 'done' && request.actionId === 'cat1_small_move') {{
              Object.assign(resultDetail, {{
                activityId: runId,
                pathDistancePx: 96,
                distancePx: 96,
                durationMs: 1320,
                plannedDurationMs: 1320,
              }});
            }}
            win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
              detail: {{ actionId: request.actionId, result, reason, source: 'cat_mind', tier: 'cat1',
                timestamp, detail: resultDetail }},
            }}));
            terminals.push({{
              actionId: request.actionId,
              result,
              reason,
              timestamp,
              minute: (timestamp - entryAt) / (60 * 1000),
              runId,
            }});
          }};
          const completeAction = (request, runId, timestamp) => reportActionResult(
            request, runId, timestamp, 'done', 'simulation-runner-finished'
          );
          win.addEventListener('neko:cat-mind:action-request', (event) => {{
            const request = event.detail;
            counts[request.actionId] = (counts[request.actionId] || 0) + 1;
            runNumber += 1;
            const runId = 'simulation-' + runNumber;
            starts.push({{ actionId: request.actionId, minute: (now - entryAt) / (60 * 1000), runId }});
            assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
              requestId: request.requestId, actionId: request.actionId, status: 'accepted', runId, timestamp: now,
            }}), true);
            assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
              requestId: request.requestId, actionId: request.actionId, status: 'started', runId, timestamp: now,
            }}), true);
            const durationMs = resolveRunnerDuration(request.actionId, profile);
            runtimeGates.activeIndependentAction = true;
            pendingResults.push({{ kind: 'result', request, runId, dueAt: now + durationMs }});
            const requestMinute = (now - entryAt) / (60 * 1000);
            const popActive = (!profile.activeFromMinute || requestMinute >= profile.activeFromMinute) &&
              (!profile.activeUntilMinute || requestMinute <= profile.activeUntilMinute);
            if (profile.popDuringAudio && popActive && request.actionId === 'cat1_social_ping') {{
              pendingResults.push({{
                kind: 'bubble_pop',
                request,
                runId,
                dueAt: now + Math.max(1, Math.min(600, durationMs - 100)),
                startedAt: now,
              }});
            }}
          }});
          const flush = () => {{
            let remaining = 1000;
            while (timers.length && remaining-- > 0) timers.shift()();
            assert.ok(remaining > 0, 'scheduler must not synchronously loop');
          }};
          win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
            detail: {{ source: 'manual-goodbye', timestamp: now }},
          }}));
          flush();
          const clock = intervals[0];
          const advanceTo = (target) => {{
            assert.ok(target >= now, 'simulation clock must move monotonically');
            let remaining = 1000;
            while (remaining-- > 0) {{
              let nextIndex = -1;
              let nextDueAt = Infinity;
              for (let index = 0; index < pendingResults.length; index += 1) {{
                if (pendingResults[index].dueAt < nextDueAt) {{
                  nextIndex = index;
                  nextDueAt = pendingResults[index].dueAt;
                }}
              }}
              if (nextIndex < 0 || nextDueAt > target) break;
              const [pending] = pendingResults.splice(nextIndex, 1);
              now = pending.dueAt;
              if (pending.kind === 'bubble_pop') {{
                bubblePops.push({{
                  runId: pending.runId,
                  timestamp: pending.dueAt,
                  startedAt: pending.startedAt,
                }});
                observe('thought_bubble_pop', pending.dueAt);
              }} else {{
                completeAction(pending.request, pending.runId, pending.dueAt);
              }}
              flush();
            }}
            assert.ok(remaining > 0, 'runner completions must not synchronously loop');
            now = target;
          }};
          const cancelActiveRunnerForDrag = () => {{
            const activeIndex = pendingResults.findIndex((pending) => pending.kind === 'result');
            if (activeIndex < 0) return;
            assert.equal(pendingResults.filter((pending) => pending.kind === 'result').length, 1,
              'only one production runner may be active');
            const [pending] = pendingResults.splice(activeIndex, 1);
            for (let index = pendingResults.length - 1; index >= 0; index -= 1) {{
              if (pendingResults[index].runId === pending.runId) pendingResults.splice(index, 1);
            }}
            reportActionResult(
              pending.request,
              pending.runId,
              now,
              'interrupted',
              'return-ball-drag-active'
            );
            flush();
          }};
          if (Array.isArray(profile.burstTimeline)) {{
            for (const item of profile.burstTimeline) {{
              advanceTo(entryAt + item.atMs);
              if (item.phase === 'settled') {{
                runtimeGates.returnPending = false;
                providerNearChatAvailable = nearChat;
                if (item.type) {{
                  observe(item.type);
                  flush();
                }}
                continue;
              }}
              if (item.phase === 'drag_start') {{
                runtimeGates.dragPending = true;
                runtimeGates.dragging = false;
                providerNearChatAvailable = false;
              }} else if (item.phase === 'drag_active') {{
                runtimeGates.dragPending = false;
                runtimeGates.dragging = true;
                cancelActiveRunnerForDrag();
              }} else if (item.phase === 'drag_end') {{
                runtimeGates.dragPending = false;
                runtimeGates.dragging = false;
                runtimeGates.returnPending = true;
              }}
              if (!item.type) continue;
              const startsBeforeObservation = starts.length;
              observe(item.type);
              flush();
              if (runtimeGates.dragPending || runtimeGates.dragging || runtimeGates.returnPending) {{
                assert.equal(starts.length, startsBeforeObservation,
                  'each drag/return gate must block its own observation turn');
              }}
            }}
          }}
          let idleTickCount = 0;
          for (let index = 0; index < 120; index += 1) {{
            const step = index + 1;
            advanceTo(entryAt + step * 30 * 1000);
            const minute = step / 2;
            const startsBeforeStep = starts.length;
            const interactionActive = (!profile.activeFromMinute || minute >= profile.activeFromMinute) &&
              (!profile.activeUntilMinute || minute <= profile.activeUntilMinute);
            if (interactionActive && profile.hoverEveryMinutes && step % (profile.hoverEveryMinutes * 2) === 0) {{
              observe('cat_hover_reaction');
            }}
            if (interactionActive && profile.chatEveryMinutes && step % (profile.chatEveryMinutes * 2) === 0) {{
              const phase = Math.floor(minute / profile.chatEveryMinutes) % 4;
              observe(['chat_minimized_visible', 'chat_minimized_moved_far',
                'chat_idle_docked_near_cat', 'chat_expanded'][phase]);
            }}
            if (interactionActive && profile.dragEveryMinutes && step % (profile.dragEveryMinutes * 2) === 0) {{
              observe('drag_start');
              if (profile.rapidEveryMinutes && step % (profile.rapidEveryMinutes * 2) === 0) {{
                observe('rapid_drag');
              }}
              observe('drag_end');
            }}
            clock.callback();
            flush();
            if (starts.length === startsBeforeStep) idleTickCount += 1;
          }}
          const fields = win.nekoCatMind.getState().fields;
          const scores = Object.fromEntries(win.nekoCatMind.getDebugSnapshot().actionScores.map(
            (item) => [item.actionId, item]
          ));
          win.dispatchEvent(new CustomEventLike('live2d-return-click', {{
            detail: {{ source: 'live2d-return-click', timestamp: now }},
          }}));
          const returnSummary = win.nekoCatMind.getReturnSummaryDraft();
          return {{
            counts,
            starts,
            idleTickCount,
            fields,
            scores,
            returnSummary,
            terminals,
            bubblePops,
          }};
        }}

        const far = simulate(false);
        assert.equal(far.counts.cat1_play_yarn || 0, 0);
        assert.equal(far.counts.cat1_small_move || 0, 0);
        assert.ok((far.counts.cat1_social_ping || 0) > 0);
        assert.ok((far.counts.cat1_social_ping || 0) <= 8, 'social ping must not fill every cooldown gap');
        assert.ok(far.idleTickCount >= 100, 'near-chat unavailable should leave genuine no-start ticks');

        const near = simulate(true);
        assert.ok((near.counts.cat1_play_yarn || 0) >= 2, 'near-chat cycle should reach play');
        assert.ok((near.counts.cat1_eat_snack || 0) >= 1, 'play feedback should later make eating possible');
        assert.ok((near.counts.cat1_small_move || 0) >= 1, 'small move remains an occasional candidate');
        const nearNonSocial = (near.counts.cat1_play_yarn || 0) + (near.counts.cat1_eat_snack || 0) + (near.counts.cat1_small_move || 0);
        assert.ok((near.counts.cat1_social_ping || 0) < nearNonSocial, 'social ping must not dominate a near-chat cycle');

        // Stress the selector with the same production observation types at
        // four interaction intensities. These are deterministic envelopes,
        // not a claim that one cadence represents every real user.
        const profiles = {{
          none: {{}},
          low: {{ hoverEveryMinutes: 10, dragEveryMinutes: 20, chatEveryMinutes: 15 }},
          normal: {{ hoverEveryMinutes: 2, dragEveryMinutes: 6, rapidEveryMinutes: 18,
            chatEveryMinutes: 6, popDuringAudio: true }},
          high: {{ hoverEveryMinutes: 0.5, dragEveryMinutes: 1, rapidEveryMinutes: 2,
            chatEveryMinutes: 2, popDuringAudio: true, activeUntilMinute: 12 }},
          highNoPop: {{ hoverEveryMinutes: 0.5, dragEveryMinutes: 1, rapidEveryMinutes: 2,
            chatEveryMinutes: 2, activeUntilMinute: 12 }},
          sustainedHigh: {{ hoverEveryMinutes: 0.5, dragEveryMinutes: 1, rapidEveryMinutes: 2,
            chatEveryMinutes: 2, popDuringAudio: true }},
        }};
        // Production compresses a short interaction into a small number of
        // facts: one mouseenter, one start/end pair, and at most one rapid fact
        // for a continuous rapid drag. The fake clock follows those facts;
        // each drag independently crosses the real drag/return gates, and the
        // explicit settled step restores the selected near/far provider fixture
        // after 120 ms. Uninterrupted runners use measured production durations
        // (including the observed fast/median/slow audio range); a later real
        // drag cancels an overlapping runner through the existing avatar
        // lifecycle instead of reporting false done.
        const shortBurstProfiles = {{
          none: {{}},
          low: {{ burstTimeline: [
            {{ atMs: 1000, type: 'cat_hover_reaction' }},
          ] }},
          normal: {{ burstTimeline: [
            {{ atMs: 500, type: 'cat_hover_reaction' }},
            {{ atMs: 1500, type: 'drag_start', phase: 'drag_start' }},
            {{ atMs: 1700, phase: 'drag_active' }},
            {{ atMs: 2500, type: 'drag_end', phase: 'drag_end' }},
            {{ atMs: 2620, type: 'cat1_stretch_done_near_chat', phase: 'settled' }},
          ] }},
          rapidOnly: {{ burstTimeline: [
            {{ atMs: 1000, type: 'drag_start', phase: 'drag_start' }},
            {{ atMs: 1200, phase: 'drag_active' }},
            {{ atMs: 1750, type: 'rapid_drag' }},
            {{ atMs: 2200, type: 'drag_end', phase: 'drag_end' }},
            {{ atMs: 2320, type: 'cat1_stretch_done_near_chat', phase: 'settled' }},
          ] }},
          high: {{ burstTimeline: [
            {{ atMs: 500, type: 'cat_hover_reaction' }},
            {{ atMs: 1500, type: 'drag_start', phase: 'drag_start' }},
            {{ atMs: 1700, phase: 'drag_active' }},
            {{ atMs: 2250, type: 'rapid_drag' }},
            {{ atMs: 2700, type: 'drag_end', phase: 'drag_end' }},
            {{ atMs: 2820, type: 'cat1_stretch_done_near_chat', phase: 'settled' }},
            {{ atMs: 4500, type: 'cat_hover_reaction' }},
            {{ atMs: 5500, type: 'drag_start', phase: 'drag_start' }},
            {{ atMs: 5700, phase: 'drag_active' }},
            {{ atMs: 6500, type: 'drag_end', phase: 'drag_end' }},
            {{ atMs: 6620, type: 'cat1_stretch_done_near_chat', phase: 'settled' }},
          ] }},
        }};
        const total = (result) => Object.values(result.counts).reduce((sum, count) => sum + count, 0);
        const startsInWindow = (result, startMinute) => result.starts.filter(
          (item) => item.minute > startMinute && item.minute <= startMinute + 15
        );
        const startsByMinute = (result, endMinute) => result.starts.filter(
          (item) => item.minute <= endMinute
        );
        const gaps = (result) => result.starts.slice(1).map(
          (item, index) => item.minute - result.starts[index].minute
        );
        const average = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;
        const maxRun = (result) => {{
          let longest = 0;
          let current = 0;
          let previous = '';
          for (const item of result.starts) {{
            current = item.actionId === previous ? current + 1 : 1;
            previous = item.actionId;
            longest = Math.max(longest, current);
          }}
          return longest;
        }};
        const none = simulate(true, profiles.none);
        const low = simulate(true, profiles.low);
        const normal = simulate(true, profiles.normal);
        const high = simulate(true, profiles.high);
        const highNoPop = simulate(true, profiles.highNoPop);
        const sustainedHigh = simulate(true, profiles.sustainedHigh);
        const shortNone = simulate(true, shortBurstProfiles.none);
        const shortLow = simulate(true, shortBurstProfiles.low);
        const shortNormal = simulate(true, shortBurstProfiles.normal);
        const shortRapidOnly = simulate(true, shortBurstProfiles.rapidOnly);
        const shortHigh = simulate(true, shortBurstProfiles.high);
        const timingVariants = [
          {{ socialDurationMs: 936, smallMoveDurationMs: 720 }},
          {{ socialDurationMs: 2040, smallMoveDurationMs: 1320 }},
          {{ socialDurationMs: 7184, smallMoveDurationMs: 1951 }},
        ].map((timing) => simulate(true, {{ ...shortBurstProfiles.high, ...timing }}));
        const farShortNone = simulate(false, shortBurstProfiles.none);
        const farShortLow = simulate(false, shortBurstProfiles.low);
        const farShortNormal = simulate(false, shortBurstProfiles.normal);
        const farShortRapidOnly = simulate(false, shortBurstProfiles.rapidOnly);
        const farShortHigh = simulate(false, shortBurstProfiles.high);
        const startsBetween = (result, fromMinute, toMinute) => result.starts.filter(
          (item) => item.minute > fromMinute && item.minute <= toMinute
        );
        const actionKinds = (items) => new Set(items.map((item) => item.actionId)).size;
        const maxActionShare = (result) => Math.max(...Object.values(result.counts)) / total(result);
        const bucketCounts = (result) => [0, 15, 30, 45].map(
          (minute) => startsBetween(result, minute, minute + 15).length
        );
        const assertLifecycle = (result) => {{
          const startIds = new Set(result.starts.map((item) => item.runId));
          const terminalIds = new Set(result.terminals.map((item) => item.runId));
          assert.equal(startIds.size, result.starts.length, 'each start must have one unique runner id');
          assert.equal(terminalIds.size, result.terminals.length, 'a runner must emit at most one terminal');
          assert.ok(result.terminals.every((item) => startIds.has(item.runId)));
          assert.ok(result.terminals.length >= result.starts.length - 1,
            'only a runner started on the final clock tick may remain pending');
          for (const pop of result.bubblePops) {{
            const terminal = result.terminals.find((item) => item.runId === pop.runId);
            assert.ok(terminal, 'a recorded bubble pop must belong to a terminal audio runner');
            assert.ok(pop.timestamp > pop.startedAt && pop.timestamp < terminal.timestamp,
              'bubble pop feedback must be observed while that audio runner is active');
          }}
        }};

        // No, low, normal, and dense user interaction all return through the
        // same bounded completed-action episode. Raw observations, five-field
        // values, and event text never enter the model-facing draft.
        for (const result of [none, low, normal, high, highNoPop, sustainedHigh]) {{
          assert.equal(result.returnSummary.duration_seconds, 60 * 60);
          assert.equal(result.returnSummary.entry, 'manual');
          assert.equal(result.returnSummary.final_tier, 'cat1');
          assert.equal(result.returnSummary.has_started_autonomous_action, true);
          assert.deepEqual(JSON.parse(JSON.stringify(result.returnSummary.episode)), {{ kind: 'activity' }});
          assert.equal(Object.prototype.hasOwnProperty.call(result.returnSummary, 'events'), false);
          assert.equal(Object.prototype.hasOwnProperty.call(result.returnSummary, 'fields'), false);
          assert.equal(Object.prototype.hasOwnProperty.call(result.returnSummary, 'text'), false);
        }}

        // With gates/providers continuously available, no/low interaction keeps
        // the pre-state-machine product rhythm without a quota or timed action.
        // Validate stable quarter-hour samples and the overall mean instead of
        // turning every five-minute sliding boundary into a hidden quota.
        for (const result of [none, low]) {{
          assert.ok(bucketCounts(result).every((count) => count >= 3 && count <= 5),
            'no/low interaction must retain roughly 3-5 starts per quarter hour');
          assert.ok(average(gaps(result)) >= 3 && average(gaps(result)) <= 5);
          assert.ok(actionKinds(startsByMinute(result, 15)) >= 3);
          assert.ok(maxRun(result) <= 2, 'score competition, not a hard ban, must prevent long same-action runs');
          const nonSocial = total(result) - (result.counts.cat1_social_ping || 0);
          assert.ok((result.counts.cat1_social_ping || 0) < nonSocial, 'low/no interaction must not become a bubble loop');
          assertLifecycle(result);
        }}

        // A short high-interaction burst must receive more autonomous answers
        // during that burst. Generic hover/drag facts act only through the five
        // needs; there is no event-to-action mapping or burst branch.
        assert.ok(startsInWindow(normal, 0).length >= 5 && startsInWindow(normal, 0).length <= 7);
        assert.ok(startsByMinute(high, 12).length >= 6 && startsByMinute(high, 12).length <= 9);
        assert.ok(high.starts[0].minute <= 2,
          'a repeated hover/drag stream must respond by its first explicit rapid gesture');
        assert.ok(startsByMinute(high, 12).length > startsByMinute(none, 12).length);
        assert.ok(startsByMinute(high, 12).length > startsByMinute(low, 12).length);
        assert.ok(startsByMinute(high, 12).length > startsByMinute(normal, 12).length);
        assert.ok(actionKinds(startsByMinute(high, 12)) >= 4,
          'the burst response must rotate across multiple actions');
        assert.ok(startsByMinute(highNoPop, 12).length > startsByMinute(normal, 12).length,
          'the response increase must not depend on the user popping every bubble');
        const highNoPopNonSocial = total(highNoPop) - (highNoPop.counts.cat1_social_ping || 0);
        assert.ok((highNoPop.counts.cat1_social_ping || 0) < highNoPopNonSocial,
          'the shared cooldown curve must suppress bubble repetition even without pop feedback');
        assert.ok(total(none) <= total(low) && total(low) < total(normal));
        assert.ok(total(normal) <= 24);
        assert.ok(total(high) <= 24, 'a short burst must not turn every observation into an action');
        assert.ok(total(highNoPop) <= 24);
        assert.ok(new Set(normal.starts.map((item) => item.actionId)).size >= 3);
        assert.ok(new Set(high.starts.map((item) => item.actionId)).size >= 4);
        assert.ok(maxRun(normal) <= 2);
        assert.ok(maxRun(high) <= 2);
        assert.ok(maxRun(highNoPop) <= 2);
        assert.ok(high.fields.energy < none.fields.energy, 'the interaction burst must consume real energy');
        assert.ok(high.scores.cat1_play_yarn.score < high.scores.cat1_play_yarn.threshold,
          'low energy must eventually suppress more yarn play even when stimulation is high');
        assert.ok(high.bubblePops.length > 0 && highNoPop.bubblePops.length === 0);
        assert.ok(Math.abs(total(high) - total(highNoPop)) <= 1,
          'popping a visible bubble must satisfy need, not inject another action');
        for (const result of [normal, high, highNoPop]) assertLifecycle(result);

        // Sustained dense interaction stays bounded but remains visibly above
        // the normal curve beyond the opening burst. No single action may own
        // more than half of the hour, even though all choices still come from
        // the shared scoring/cooldown path.
        assert.ok(total(sustainedHigh) >= 20 && total(sustainedHigh) <= 26);
        assert.ok(startsByMinute(sustainedHigh, 15).length >= startsByMinute(normal, 15).length + 1);
        assert.ok(bucketCounts(sustainedHigh).every((count) => count >= 5 && count <= 8));
        assert.ok(actionKinds(sustainedHigh.starts) >= 4);
        assert.ok(maxActionShare(sustainedHigh) <= 0.5,
          'sustained interaction must not collapse into one repeated response');
        assert.ok(maxRun(sustainedHigh) <= 2);
        assert.ok(sustainedHigh.fields.social_need < 1 && sustainedHigh.fields.stimulation_need < 1,
          'bounded overflow leakage must keep repeated evidence below hard saturation');
        assertLifecycle(sustainedHigh);

        // Realistic facts must produce a visible response curve in the first
        // few minutes. This is still one scoring path: no burst flag reaches
        // Cat Mind and generic interaction facts are not mapped to an action.
        assert.ok(shortNone.starts[0].minute >= 3 && shortNone.starts[0].minute <= 4);
        assert.ok(shortLow.starts[0].minute >= 3 && shortLow.starts[0].minute <= 4);
        assert.ok(shortNormal.starts[0].minute >= 2.5 && shortNormal.starts[0].minute <= 3.5);
        assert.ok(shortHigh.starts[0].minute <= 0.5,
          'a compressed high-interaction burst must receive a response within 30 seconds');
        assert.equal(startsByMinute(shortNone, 3).length, 0);
        assert.equal(startsByMinute(shortLow, 3).length, 0);
        assert.ok(startsByMinute(shortNormal, 3).length >= 1);
        assert.ok(startsByMinute(shortRapidOnly, 1).length >= 1,
          'one sustained rapid drag must receive a scored response within 60 seconds');
        assert.ok(startsByMinute(shortRapidOnly, 3).length >= 2);
        assert.ok(startsByMinute(shortHigh, 3).length >= 3 && startsByMinute(shortHigh, 3).length <= 5);
        assert.ok(startsByMinute(shortNone, 10).length <= startsByMinute(shortLow, 10).length,
          'one easy hover may preserve the quiet count instead of forcing an extra action');
        assert.ok(startsByMinute(shortLow, 10).length <= startsByMinute(shortNormal, 10).length);
        assert.ok(shortNormal.starts[0].minute < shortLow.starts[0].minute,
          'normal interaction must advance the first response even when the ten-minute counts tie');
        assert.ok(startsByMinute(shortNormal, 10).length < startsByMinute(shortHigh, 10).length);
        assert.ok(actionKinds(startsByMinute(shortHigh, 3)) >= 3);
        assert.ok(actionKinds(startsByMinute(shortHigh, 10)) >= 4);
        const postBurstProfiles = [shortNone, shortLow, shortNormal, shortRapidOnly, shortHigh];
        for (const result of postBurstProfiles) {{
          assert.ok(bucketCounts(result).slice(1).every((count) => count >= 3 && count <= 5),
            'a short burst must return to the ordinary quarter-hour rhythm');
        }}
        const postBurstTailCounts = postBurstProfiles.map((result) => startsBetween(result, 15, 60).length);
        assert.ok(Math.max(...postBurstTailCounts) - Math.min(...postBurstTailCounts) <= 1,
          'a short interaction burst must converge back to the ordinary tail rhythm');
        for (const result of [shortNone, shortLow, shortNormal, shortRapidOnly, shortHigh]) {{
          assert.ok(maxRun(result) <= 2);
          assertLifecycle(result);
        }}

        // Real runner duration spread must not change the response envelope or
        // create duplicate terminals. In the final curve the first high-burst
        // response is play, so the second physical drag interrupts that real
        // runner; later fast/median/slow social audio all finish normally.
        for (const result of timingVariants) {{
          assert.ok(result.starts[0].minute <= 0.5);
          assert.ok(startsByMinute(result, 3).length >= 3 && startsByMinute(result, 3).length <= 5);
          assert.ok(actionKinds(startsByMinute(result, 3)) >= 3);
          assert.ok(total(result) >= 18 && total(result) <= 21);
          assertLifecycle(result);
        }}
        for (const result of timingVariants) {{
          assert.equal(result.terminals[0].actionId, 'cat1_play_yarn');
          assert.equal(result.terminals[0].result, 'interrupted');
          assert.ok(result.terminals
            .filter((item) => item.actionId === 'cat1_social_ping')
            .every((item) => item.result === 'done'));
        }}

        // Far-chat remains a provider constraint. Interaction can accelerate
        // legal social/eat responses, but cannot manufacture play or movement.
        assert.ok(farShortHigh.starts[0].minute <= 0.5);
        assert.ok(farShortRapidOnly.starts[0].minute <= 2.5,
          'without near-chat play/move providers, one rapid gesture may wait for the legal social response');
        assert.ok(farShortNormal.starts[0].minute < farShortLow.starts[0].minute);
        assert.ok(farShortLow.starts[0].minute <= farShortNone.starts[0].minute);
        assert.ok(startsByMinute(farShortNormal, 10).length >= startsByMinute(farShortLow, 10).length,
          'provider-limited far-chat may tie in count, but normal interaction still advances its first response');
        assert.ok(startsByMinute(farShortHigh, 15).length >= startsByMinute(farShortNormal, 15).length);
        assert.ok(farShortHigh.starts[0].minute < farShortNormal.starts[0].minute,
          'dense interaction must still advance the first legal far-chat response');
        for (const result of [farShortNone, farShortLow, farShortNormal, farShortRapidOnly, farShortHigh]) {{
          assert.equal(result.counts.cat1_play_yarn || 0, 0);
          assert.equal(result.counts.cat1_small_move || 0, 0);
          assert.ok(maxRun(result) <= 2,
            'far-chat must alternate the legal actions instead of becoming a bubble loop');
          assertLifecycle(result);
        }}

        for (const profile of Object.values(profiles)) {{
          const farProfile = simulate(false, profile);
          assert.equal(farProfile.counts.cat1_play_yarn || 0, 0);
          assert.equal(farProfile.counts.cat1_small_move || 0, 0);
        }}
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_all_registered_actions_use_the_same_soft_cooldown_curve():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        function verify(actionId, tier, cooldownMinutes) {{
          let now = 1000;
          const timers = [];
          const requests = [];
          let providerEnabled = false;
          const win = new EventTargetLike();
          win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
          win.setInterval = () => 1;
          win.clearInterval = () => {{}};
          const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
          vm.createContext(context);
          vm.runInContext(source, context);
          win.NekoCatMindActionProviders = {{
            getRuntimeGateSnapshot() {{
              return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
                activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
                chatSurfaceDragging: false }};
            }},
            dryRun(candidateId) {{
              return providerEnabled && candidateId === actionId
                ? {{ allowed: true, reason: 'allowed' }}
                : {{ allowed: false, reason: 'not_for_soft_cooldown_test' }};
            }}
          }};
          win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
          const flush = () => {{
            let remaining = 100;
            while (timers.length && remaining-- > 0) timers.shift()();
            assert.ok(remaining > 0);
          }};
          win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
            detail: {{ source: 'manual-goodbye', timestamp: now }}
          }}));
          flush();
          if (tier !== 'cat1') {{
            now += 1;
            win.dispatchEvent(new CustomEventLike('neko:auto-goodbye:state-change', {{
              detail: {{ type: 'visual-tier', tier, source: 'soft-cooldown-test', timestamp: now }}
            }}));
            flush();
          }}
          providerEnabled = true;
          now += 15 * 60 * 1000;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{ detail: {{
            type: 'cat_elapsed', source: 'cat-mind-clock', tier, timestamp: now,
            detail: {{ elapsedMs: 15 * 60 * 1000 }}
          }} }}));
          flush();
          assert.equal(requests.length, 1, actionId + ' must become eligible through the shared score');
          const request = requests[0];
          const runId = 'soft-cooldown-' + actionId;
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId, status: 'accepted', runId, timestamp: now
          }}), true);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId, status: 'started', runId, timestamp: now
          }}), true);
          const score = () => win.nekoCatMind.getDebugSnapshot().actionScores.find(
            (item) => item.actionId === actionId
          );
          const close = (actual, expected) => assert.ok(Math.abs(actual - expected) <= 0.011,
            actionId + ': ' + actual + ' !== ' + expected);
          const expectedCurve = (elapsedProgress) => 1 - Math.pow(elapsedProgress, 1.9);
          const fullPenalty = 36 * 4.2;
          const started = score();
          assert.equal(started.cooldownApplied, true);
          close(started.cooldownPenalty, fullPenalty);
          assert.equal(started.cooldownCurveFactor, 1);
          assert.equal(started.cadenceAdjustment, -58);
          now += cooldownMinutes * 60 * 1000 / 4;
          const quarter = score();
          close(quarter.cooldownPenalty, fullPenalty * expectedCurve(0.25));
          assert.equal(quarter.cooldownRecoveryFactor, 0.75);
          close(quarter.cooldownCurveFactor, expectedCurve(0.25));
          now += cooldownMinutes * 60 * 1000 / 4;
          const halfway = score();
          assert.equal(halfway.cooldownApplied, true);
          close(halfway.cooldownPenalty, fullPenalty * expectedCurve(0.5));
          assert.equal(halfway.cooldownRecoveryFactor, 0.5);
          close(halfway.cooldownCurveFactor, expectedCurve(0.5));
          now += cooldownMinutes * 60 * 1000 / 4;
          const threeQuarters = score();
          close(threeQuarters.cooldownPenalty, fullPenalty * expectedCurve(0.75));
          assert.equal(threeQuarters.cooldownRecoveryFactor, 0.25);
          close(threeQuarters.cooldownCurveFactor, expectedCurve(0.75));
          now += cooldownMinutes * 60 * 1000 / 4 + 1;
          const recovered = score();
          assert.equal(recovered.cooldownApplied, false);
          assert.equal(recovered.cooldownPenalty, 0);
        }}

        verify('cat1_social_ping', 'cat1', 8);
        verify('cat1_eat_snack', 'cat1', 8);
        verify('cat1_small_move', 'cat1', 8);
        verify('cat1_play_yarn', 'cat1', 10);
        verify('cat2_nap_feedback', 'cat2', 10);
        verify('cat3_sleep_feedback', 'cat3', 12);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_scores_use_five_dimensions_shared_cadence_and_own_soft_cooldown():
    source = _read(CAT_MIND_PATH)

    assert "RECENT_SCORE_WINDOWS_MS" not in source
    assert "contextAdjustment" not in source
    assert "var ACTION_SCORE_POLICY = Object.freeze({" in source
    assert "var needSurplus = baseScore - threshold;" in source
    assert "var needContribution = getNeedContribution(needSurplus);" in source
    assert "return normalized * normalized * (3 - 2 * normalized);" in source
    assert "cadenceRecoveryMs: 4.85 * 60 * 1000" in source
    assert "cooldownRecoveryExponent: 1.9" in source
    assert "var cooldownPenalty = cooldown.penalty * (1 - intentCooldownRelief);" in source
    assert "var utilityScore = needContribution + cadence.adjustment + intent.contribution - cooldownPenalty;" in source
    assert "positiveNeedCurveTailCeiling: 20" in source
    assert "positiveNeedCurveTailScale: 8" in source
    assert "maxContribution: 84" in source
    assert "cooldownPenaltyRelief: 0.5" in source
    assert "freshHoldMs: 30 * 1000" in source
    assert "halfLifeMs: 90 * 1000" in source
    assert "score: threshold + utilityScore" in source
    assert "right.utilityScore - left.utilityScore" in source
    assert "negativeNeedCurveRange: 14" in source
    assert "positiveNeedCurveRange: 8" in source
    assert "positiveNeedCurveCeiling: 78" in source
    assert "cadenceFloor: -58" in source
    assert "cadenceCeiling: 18" in source
    assert "cooldownMultiplier: 4.2" in source
    assert "hardCooldown" not in source
    assert "hard_cooldown_active" not in source
    assert "cat1_social_ping: Object.freeze({ threshold: 36, cooldownMs: 8 * 60 * 1000 })" in source
    assert "cat1_eat_snack: Object.freeze({ threshold: 40, cooldownMs: 8 * 60 * 1000 })" in source
    assert "cat2_nap_feedback: Object.freeze({ threshold: 54, cooldownMs: 10 * 60 * 1000 })" in source
    assert "cat3_sleep_feedback: Object.freeze({ threshold: 50, cooldownMs: 12 * 60 * 1000 })" in source


def test_cat_mind_need_and_cadence_use_the_shared_monotonic_response_curve():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const sourceText = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        const closeIndex = sourceText.lastIndexOf('}})();');
        assert.ok(closeIndex > 0);
        const source = sourceText.slice(0, closeIndex) +
          'window.__testCatMindNeedContribution = getNeedContribution;' +
          sourceText.slice(closeIndex);
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        const scoreSnapshot = () => win.nekoCatMind.getDebugSnapshot().actionScores;
        const smoothstep = (value) => value * value * (3 - 2 * value);
        const expectedNeed = (surplus) => {{
          if (surplus < 0) {{
            if (surplus <= -14) return surplus;
            return -14 * smoothstep(Math.abs(surplus) / 14);
          }}
              if (surplus <= 8) return 78 * smoothstep(surplus / 8);
              return 78 + 20 * (1 - Math.exp(-(surplus - 8) / 8));
            }};
            for (const [surplus, expected] of [[0, 0], [1, 3.3515625], [2, 12.1875], [4, 39],
              [6, 65.8125], [8, 78], [12, 78 + 20 * (1 - Math.exp(-0.5))]]) {{
          assert.ok(Math.abs(win.__testCatMindNeedContribution(surplus) - expected) <= 1e-9);
        }}
        const start = scoreSnapshot();
        for (const score of start) {{
          assert.ok(Math.abs(score.needContribution - expectedNeed(score.needSurplus)) <= 0.011);
              assert.equal(score.cadenceAdjustment, -58);
          assert.equal(score.cadenceCurveProgress, 0);
          assert.equal(score.cadenceCurveFactor, 0);
        }}
        now += 4.85 * 60 * 1000 / 2;
        const halfway = scoreSnapshot()[0];
            assert.equal(halfway.cadenceAdjustment, -20);
        assert.equal(halfway.cadenceCurveProgress, 0.5);
        assert.equal(halfway.cadenceCurveFactor, 0.5);
        now += 4.85 * 60 * 1000 / 2;
        const recovered = scoreSnapshot()[0];
        assert.equal(recovered.cadenceAdjustment, 18);
        assert.equal(recovered.cadenceCurveProgress, 1);
        assert.equal(recovered.cadenceCurveFactor, 1);

        now += 1;
        win.dispatchEvent(new CustomEventLike('neko:auto-goodbye:state-change', {{ detail: {{
          type: 'visual-tier', tier: 'cat3', source: 'curve-test', timestamp: now
        }} }}));
        const saturated = scoreSnapshot().find((score) => score.actionId === 'cat3_sleep_feedback');
        assert.ok(saturated.needSurplus > 8);
            assert.ok(saturated.needContribution > 78 && saturated.needContribution < 98);
            assert.ok(Math.abs(saturated.needContribution - expectedNeed(saturated.needSurplus)) <= 0.011);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_explicit_yarn_intent_respects_provider_and_started_lifecycle():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const timers = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        let allowYarn = false;
        let yarnDragActive = false;
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false, yarnDragActive, yarnSettling: false }};
          }},
          dryRun(actionId) {{
            if (actionId === 'cat1_play_yarn') {{
              return {{ allowed: allowYarn, reason: allowYarn ? 'allowed' : 'near_chat_unavailable' }};
            }}
            return {{ allowed: false, reason: 'not_enabled_for_intent_test' }};
          }},
        }};
        const requests = [];
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
        const flush = () => {{
          let remaining = 100;
          while (timers.length && remaining-- > 0) timers.shift()();
          assert.ok(remaining > 0);
        }};
        const observe = (type, detail = {{}}) => {{
          now += 1;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
            detail: {{ type, source: 'yarn-intent-test', tier: 'cat1', timestamp: now, detail }},
          }}));
          flush();
        }};
        const strongOffer = () => observe('chat_yarn_drag_completed', {{
          userInitiated: true,
          startedFarFromCat: true,
          endedNearCat: true,
          startDistanceToCatPx: 320,
          endDistanceToCatPx: 20,
          directApproachDistancePx: 300,
          pathDistancePx: 310,
          movementThresholdPx: 24,
        }});

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }},
        }}));
        flush();
        yarnDragActive = true;
        observe('chat_minimized_visible');
        assert.equal(win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'chat_yarn_dragging');
        yarnDragActive = false;
        strongOffer();
        assert.equal(requests.length, 0, 'intent must not bypass the near-chat provider');
        let evidence = win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn;
        assert.equal(evidence.level, 0.9);
        assert.ok(evidence.contribution > 81 && evidence.contribution < 82);
        assert.equal(evidence.reason, 'yarn_offer_far_to_near');

        allowYarn = true;
        observe('chat_minimized_visible');
        assert.equal(requests.length, 1);
        assert.equal(requests[0].actionId, 'cat1_play_yarn');
        let candidate = win.nekoCatMind.getDebugSnapshot().lastDecision.candidates.find(
          (item) => item.actionId === 'cat1_play_yarn'
        );
        assert.equal(candidate.intentLevel, 0.9);
        assert.ok(candidate.intentContribution > 81);

        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: requests[0].requestId, actionId: requests[0].actionId,
          status: 'rejected', reason: 'provider_changed_before_run', timestamp: ++now,
        }}), true);
        assert.equal(win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn.level, 0.9,
          'adapter rejection must not consume intent');

        observe('chat_compact_surface_visible');
        assert.equal(requests.length, 2);
        const request = requests[1];
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: request.requestId, actionId: request.actionId,
          status: 'accepted', runId: 'yarn-intent-run', timestamp: ++now,
        }}), true);
        assert.equal(win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn.level, 0.9,
          'accepted is not a real start');
        assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
          requestId: request.requestId, actionId: request.actionId,
          status: 'started', runId: 'yarn-intent-run', timestamp: ++now,
        }}), true);
        assert.equal(win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn, undefined);
        assert.equal(win.nekoCatMind.getState().actionCooldowns.cat1_play_yarn.startedAt, now);

        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{ detail: {{
          actionId: request.actionId, result: 'done', source: 'cat_mind', tier: 'cat1',
          timestamp: ++now, reason: 'yarn-finished',
          detail: {{ requestId: request.requestId, runId: 'yarn-intent-run' }},
        }} }}));
        flush();
        strongOffer();
        assert.equal(requests.filter((item) => item.actionId === 'cat1_play_yarn').length, 2,
          'a fresh own cooldown must still dominate a new strong invitation');
        candidate = win.nekoCatMind.getDebugSnapshot().lastDecision.candidates.find(
          (item) => item.actionId === 'cat1_play_yarn'
        );
        assert.equal(candidate.cooldownApplied, true);
        assert.equal(candidate.intentLevel, 0.9);
        assert.equal(candidate.allowed, false);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_yarn_intent_decays_and_repeated_near_offers_saturate():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const timers = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        let allowYarn = false;
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false, yarnDragActive: false, yarnSettling: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_play_yarn' && allowYarn
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_available' }};
          }},
        }};
        const requests = [];
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));
        const flush = () => {{ while (timers.length) timers.shift()(); }};
        const offer = (detail) => {{
          now += 1;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{ detail: {{
            type: 'chat_yarn_drag_completed', source: 'near-offer-test', tier: 'cat1', timestamp: now, detail,
          }} }}));
          flush();
        }};
        const nearOffer = () => offer({{
          userInitiated: true, startedFarFromCat: false, endedNearCat: true,
          startDistanceToCatPx: 100, endDistanceToCatPx: 90,
          directApproachDistancePx: 10, pathDistancePx: 80, movementThresholdPx: 24,
        }});

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }},
        }}));
        flush();
        nearOffer();
        let evidence = win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn;
        assert.equal(evidence.level, 0.62);
        now += 30 * 1000;
        evidence = win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn;
        assert.ok(Math.abs(evidence.level - 0.62) < 0.0001, 'the first 30 seconds hold the evidence');
        now += 90 * 1000;
        evidence = win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn;
        assert.ok(Math.abs(evidence.level - 0.31) < 0.0001, 'one half-life follows the hold');

        offer({{
          userInitiated: true, startedFarFromCat: false, endedNearCat: false,
          startDistanceToCatPx: 20, endDistanceToCatPx: 220,
          directApproachDistancePx: -200, pathDistancePx: 200, movementThresholdPx: 24,
        }});
        assert.equal(win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn, undefined,
          'moving the yarn away clears stale offer evidence');

        nearOffer();
        assert.equal(requests.length, 0);
        allowYarn = true;
        nearOffer();
        evidence = win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn;
        assert.ok(Math.abs(evidence.level - 0.8556) < 0.0001);
        assert.equal(requests.length, 1);
        assert.equal(requests[0].actionId, 'cat1_play_yarn');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_physical_activity_is_terminal_curve_and_deduplicated():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        const observe = (type, detail = {{}}) => {{
          now += 1;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{ detail: {{
            type, source: 'physical-curve-test', tier: 'cat1', timestamp: now, detail,
          }} }}));
        }};
        const physical = () => {{
          const fields = win.nekoCatMind.getState().fields;
          return {{ appetite: fields.appetite, energy: fields.energy, sleepiness: fields.sleepiness }};
        }};
        const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9,
          actual + ' !== ' + expected);

        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }},
        }}));
        const initial = physical();
        observe('drag_start');
        observe('rapid_drag');
        assert.deepEqual(physical(), initial, 'start/rapid facts must not double-charge physical work');
        assert.deepEqual(JSON.parse(JSON.stringify(
          win.nekoCatMind.getDebugSnapshot().actionIntentEvidence
        )), {{}}, 'generic drag intensity must remain in the five needs');

        const halfLoad = {{ activityId: 'drag-a', pathDistancePx: 80, durationMs: 1100 }};
        observe('drag_end', halfLoad);
        let fields = physical();
        close(fields.appetite, initial.appetite + 0.02);
        close(fields.energy, initial.energy - 0.0225);
        close(fields.sleepiness, initial.sleepiness + 0.01);
        assert.deepEqual(JSON.parse(JSON.stringify(
          win.nekoCatMind.getDebugSnapshot().actionIntentEvidence
        )), {{}}, 'a completed ordinary drag must not target social_ping');
        const afterFirst = {{ ...fields }};
        observe('drag_cancelled', halfLoad);
        assert.deepEqual(physical(), afterFirst, 'the same terminal activityId settles once');

        observe('small_move_done', {{ activityId: 'move-b', pathDistancePx: 80, durationMs: 1100 }});
        fields = physical();
        close(fields.appetite, initial.appetite + 0.04);
        close(fields.energy, initial.energy - 0.045);
        close(fields.sleepiness, initial.sleepiness + 0.02);
        observe('cat1_walk_done_near_chat', {{ activityId: 'walk-c', pathDistancePx: 80, durationMs: 1100 }});
        fields = physical();
        close(fields.appetite, initial.appetite + 0.06);
        close(fields.energy, initial.energy - 0.0675);
        close(fields.sleepiness, initial.sleepiness + 0.03);
        observe('drag_cancelled', {{ activityId: 'zero-d', pathDistancePx: 0, durationMs: 500 }});
        assert.deepEqual(physical(), fields, 'zero-distance terminal facts have no physical cost');
        observe('drag_end', {{ pathDistancePx: 160, durationMs: 2200 }});
        assert.deepEqual(physical(), fields, 'unidentified terminal facts cannot be safely deduplicated');

        const beforeCancelledMove = win.nekoCatMind.getState().fields;
        observe('small_move_cancelled', {{
          activityId: 'move-cancelled-e', pathDistancePx: 80, durationMs: 1100,
          completed: false, cancelled: true,
        }});
        let afterTerminal = win.nekoCatMind.getState().fields;
        close(afterTerminal.appetite, beforeCancelledMove.appetite + 0.02);
        close(afterTerminal.energy, beforeCancelledMove.energy - 0.0225);
        close(afterTerminal.sleepiness, beforeCancelledMove.sleepiness + 0.01);
        close(afterTerminal.social_need, beforeCancelledMove.social_need);
        close(afterTerminal.stimulation_need, beforeCancelledMove.stimulation_need);

        const beforeCancelledWalk = afterTerminal;
        observe('cat1_walk_done_near_chat', {{
          activityId: 'walk-cancelled-f', pathDistancePx: 80, durationMs: 1100,
          completed: false, cancelled: true,
        }});
        afterTerminal = win.nekoCatMind.getState().fields;
        close(afterTerminal.appetite, beforeCancelledWalk.appetite + 0.02);
        close(afterTerminal.energy, beforeCancelledWalk.energy - 0.0225);
        close(afterTerminal.sleepiness, beforeCancelledWalk.sleepiness + 0.01);
        close(afterTerminal.social_need, beforeCancelledWalk.social_need);
        close(afterTerminal.stimulation_need, beforeCancelledWalk.stimulation_need);

        const beforeCompactWalk = afterTerminal;
        observe('cat1_compact_top_edge_done', {{
          activityId: 'compact-walk-g', pathDistancePx: 80, durationMs: 1100,
        }});
        afterTerminal = win.nekoCatMind.getState().fields;
        close(afterTerminal.appetite, beforeCompactWalk.appetite + 0.02);
        close(afterTerminal.energy, beforeCompactWalk.energy - 0.0525);
        close(afterTerminal.sleepiness, beforeCompactWalk.sleepiness + 0.01);
        close(afterTerminal.social_need, beforeCompactWalk.social_need + 0.04);
        close(afterTerminal.stimulation_need, beforeCompactWalk.stimulation_need - 0.06);

        observe('cat_hover_reaction');
        assert.deepEqual(JSON.parse(JSON.stringify(
          win.nekoCatMind.getDebugSnapshot().actionIntentEvidence
        )), {{}}, 'ordinary hover must also remain in the five needs');
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_user_and_completed_action_feedback_updates_only_its_defined_fields():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        const equal = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, actual + ' !== ' + expected);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{ detail: {{ source: 'manual-goodbye', timestamp: now }} }}));
        const observe = (type, tier = 'cat1', detail = {{}}) => {{
          now += 1;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
            detail: {{ type, source: 'unit-feedback', tier, timestamp: now, detail }},
          }}));
        }};
        const expected = {{ ...win.nekoCatMind.getState().fields }};
        const clamp = (value) => Math.max(0, Math.min(1, value));
        const add = (delta) => Object.entries(delta).forEach(([field, value]) => {{
          expected[field] = clamp(expected[field] + value);
        }});
        const merge = (field, dose) => {{
          const current = expected[field];
          expected[field] = clamp(current + dose * (1 - current) * (1 + 0.8 * current));
        }};
        const assertFields = () => {{
          const actual = win.nekoCatMind.getState().fields;
          assert.deepEqual(Object.keys(actual).sort(), [
            'appetite', 'energy', 'sleepiness', 'social_need', 'stimulation_need'
          ]);
          Object.keys(expected).forEach((field) => equal(actual[field], expected[field]));
        }};

        observe('thought_bubble_pop');
        expected.social_need *= 0.72;
        expected.stimulation_need *= 0.82;
        assertFields();

        observe('cat_hover_reaction');
        merge('social_need', 0.006);
        merge('stimulation_need', 0.018);
        add({{ energy: -0.001 }});
        assertFields();

        observe('drag_start');
        observe('rapid_drag');
        observe('drag_end', 'cat1', {{ activityId: 'feedback-drag-1', pathDistancePx: 160, durationMs: 2200 }});
        merge('social_need', 0.01);
        merge('stimulation_need', 0.035);
        merge('social_need', 0.08);
        merge('stimulation_need', 0.4);
        merge('social_need', 0.03);
        merge('stimulation_need', 0.12);
        add({{ appetite: 0.04, sleepiness: 0.02, energy: -0.045 }});
        assertFields();

        const beforeWindowFact = {{ ...win.nekoCatMind.getState().fields }};
        observe('chat_minimized_visible');
        Object.entries(beforeWindowFact).forEach(([field, value]) => {{
          equal(win.nekoCatMind.getState().fields[field], value);
        }});

        observe('play_done');
        add({{ stimulation_need: -0.28, energy: -0.08, appetite: 0.07, sleepiness: 0.05 }});
        assertFields();

        observe('eat_done');
        add({{ appetite: -0.34, energy: 0.14, stimulation_need: 0.02, sleepiness: -0.01 }});
        assertFields();

        observe('tier_changed', 'cat2');
        expected.sleepiness = Math.max(expected.sleepiness, 0.55);
        expected.energy = Math.min(expected.energy, 0.45);
        observe('sleep_feedback_done', 'cat2');
        add({{ sleepiness: -0.3, energy: 0.16, stimulation_need: 0.02, appetite: 0.02 }});
        assertFields();
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_bubble_pop_satisfies_need_instead_of_feeding_an_action_loop():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{ detail: {{ source: 'manual-goodbye', timestamp: now }} }}));
        const scores = () => Object.fromEntries(win.nekoCatMind.getDebugSnapshot().actionScores.map((item) => [item.actionId, item.score]));
        for (const type of ['cat_hover_reaction', 'drag_end']) {{
          now += 1;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
            detail: {{ type, source: 'unit-mixed-user-interaction', tier: 'cat1', timestamp: now, detail: {{}} }},
          }}));
        }}
        const beforePopFields = win.nekoCatMind.getState().fields;
        const beforePopScores = scores();
        now += 1;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
          detail: {{ type: 'thought_bubble_pop', source: 'unit-mixed-user-interaction', tier: 'cat1', timestamp: now, detail: {{}} }},
        }}));
        const afterPopFields = win.nekoCatMind.getState().fields;
        const afterPopScores = scores();
        assert.ok(afterPopFields.social_need < beforePopFields.social_need);
        assert.ok(afterPopFields.stimulation_need < beforePopFields.stimulation_need);
        assert.equal(afterPopFields.appetite, beforePopFields.appetite);
        assert.equal(afterPopFields.energy, beforePopFields.energy);
        assert.ok(afterPopScores.cat1_social_ping < beforePopScores.cat1_social_ping);
        assert.ok(afterPopScores.cat1_small_move < beforePopScores.cat1_small_move);
        assert.ok(afterPopScores.cat1_play_yarn < beforePopScores.cat1_play_yarn);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase4_return_episode_uses_only_strict_completed_chapters():
    """The return episode is bounded local state, never a recent-event summary."""
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        let runNumber = 0;
        let allowedAction = '';
        const timers = [];
        const requests = [];
        const win = new EventTargetLike();
        win.setTimeout = (callback) => {{ timers.push(callback); return timers.length; }};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === allowedAction
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_enabled_for_test' }};
          }}
        }};
        win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

        function flushDecision() {{
          assert.ok(timers.length > 0, 'expected queued Cat Mind decision');
          timers.shift()();
        }}
        function observe(type, tier) {{
          now += 10;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
            detail: {{ type, source: 'phase4-unit', tier: tier || win.nekoCatMind.getState().tier,
              timestamp: now, detail: {{ elapsedMs: 0 }} }}
          }}));
          flushDecision();
        }}
        function start() {{
          allowedAction = '';
          now += 10;
          win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
            detail: {{ source: 'manual-goodbye', timestamp: now }}
          }}));
          flushDecision();
        }}
        function setTier(tier, source) {{
          now += 10;
          win.dispatchEvent(new CustomEventLike('neko:auto-goodbye:state-change', {{
            detail: {{ type: 'visual-tier', tier, source: source || 'phase4-unit', timestamp: now }}
          }}));
          flushDecision();
        }}
        function beginAction(actionId) {{
          allowedAction = actionId;
          now += 15 * 60 * 1000;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{ detail: {{
            type: 'cat_elapsed', source: 'cat-mind-clock', tier: win.nekoCatMind.getState().tier,
            timestamp: now, detail: {{ elapsedMs: 15 * 60 * 1000 }}
          }} }}));
          flushDecision();
          const request = requests[requests.length - 1];
          assert.ok(request, 'expected action request');
          assert.equal(request.actionId, actionId);
          const runId = 'phase4-run-' + (++runNumber);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId, status: 'accepted', runId, timestamp: now
          }}), true);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId, status: 'started', runId, timestamp: now
          }}), true);
          return {{ request, runId }};
        }}
        function acceptOnly(actionId) {{
          allowedAction = actionId;
          now += 15 * 60 * 1000;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{ detail: {{
            type: 'cat_elapsed', source: 'cat-mind-clock', tier: win.nekoCatMind.getState().tier,
            timestamp: now, detail: {{ elapsedMs: 15 * 60 * 1000 }}
          }} }}));
          flushDecision();
          const request = requests[requests.length - 1];
          assert.ok(request, 'expected action request');
          assert.equal(request.actionId, actionId);
          const runId = 'phase4-accepted-' + (++runNumber);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId, status: 'accepted', runId, timestamp: now
          }}), true);
          return {{ request, runId }};
        }}
        function finishAction(actionId, lifecycle, result, reason) {{
          now += 10;
          win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
            detail: {{
              actionId,
              result: result || 'done',
              source: 'cat_mind',
              tier: win.nekoCatMind.getState().tier,
              timestamp: now,
              reason: reason || 'phase4-unit',
              detail: {{ requestId: lifecycle.request.requestId, runId: lifecycle.runId }}
            }}
          }}));
          allowedAction = '';
          flushDecision();
        }}
        function complete(actionId) {{
          const lifecycle = beginAction(actionId);
          finishAction(actionId, lifecycle, 'done');
        }}
        function primeInteraction(count) {{
          for (let index = 0; index < count; index += 1) observe('cat_hover_reaction');
        }}
        function returnSummary() {{
          now += 10;
          win.dispatchEvent(new CustomEventLike('live2d-return-click'));
          const summary = win.nekoCatMind.getState().returnSummaryDraft;
          // The mocked timer is associated with the pre-return state and would
          // be a no-op in the browser after runtime reset. Do not let it bleed
          // into the next independent test chapter.
          timers.length = 0;
          return summary;
        }}
        function restart() {{
          win.nekoCatMind.reset('phase4-unit');
          timers.length = 0;
          start();
        }}

        // Public observations and legacy runner results cannot manufacture an
        // episode. Only a matching Cat Mind lifecycle can write it.
        start();
        observe('play_done');
        now += 10;
        win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {{
          detail: {{ actionId: 'cat1_play_yarn', result: 'done', source: 'cat1-journey',
            tier: 'cat1', timestamp: now, detail: {{ requestId: 'legacy', runId: 'legacy' }} }}
        }}));
        let episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter.activityKinds)), []);
        assert.equal(episodeDebug.preview, null);
        const legacySummary = returnSummary();
        assert.equal(Object.prototype.hasOwnProperty.call(legacySummary, 'episode'), false);
        assert.equal(Object.prototype.hasOwnProperty.call(legacySummary, 'has_started_autonomous_action'), false);

        // Adapter acceptance alone is not a real action entry and cannot
        // open the short-return delivery gate.
        start();
        primeInteraction(16);
        acceptOnly('cat1_small_move');
        const acceptedOnlySummary = returnSummary();
        assert.equal(Object.prototype.hasOwnProperty.call(acceptedOnlySummary, 'episode'), false);
        assert.equal(Object.prototype.hasOwnProperty.call(acceptedOnlySummary, 'has_started_autonomous_action'), false);

        // Matching terminal failures do settle their runner but never count as
        // a completed activity. They do preserve the separate verified-start
        // delivery gate. This covers all non-done terminal outcomes.
        for (const result of ['failed', 'cancelled', 'interrupted']) {{
          restart();
          primeInteraction(16);
          const lifecycle = beginAction('cat1_small_move');
          finishAction('cat1_small_move', lifecycle, result, 'phase4-' + result);
          episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
          assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter.activityKinds)), []);
          assert.equal(episodeDebug.preview, null);
          const interruptedSummary = returnSummary();
          assert.equal(Object.prototype.hasOwnProperty.call(interruptedSummary, 'episode'), false);
          assert.equal(interruptedSummary.has_started_autonomous_action, true);
        }}

        // Repeated successful kinds stay bounded; mixed kinds deliberately
        // remove the highlight rather than inventing a dominant action.
        restart();
        primeInteraction(16);
        complete('cat1_small_move');
        now += 80 * 1000;
        primeInteraction(16);
        complete('cat1_small_move');
        episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter.activityKinds)), ['cat1_small_move']);
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.preview)), {{ kind: 'activity', highlight: 'small_move' }});
        complete('cat1_play_yarn');
        episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter.activityKinds)), [
          'cat1_small_move', 'cat1_play_yarn'
        ]);
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.preview)), {{ kind: 'activity' }});
        const mixedSummary = returnSummary();
        assert.deepEqual(JSON.parse(JSON.stringify(mixedSummary.episode)), {{ kind: 'activity' }});
        assert.equal(mixedSummary.has_started_autonomous_action, true);
        assert.equal(Object.prototype.hasOwnProperty.call(mixedSummary, 'dominant_state'), false);
        assert.equal(Object.prototype.hasOwnProperty.call(mixedSummary, 'events'), false);

        // Window noise can push old entries out of recentEvents, but it is not
        // part of the episode and cannot change a real completed activity.
        start();
        primeInteraction(16);
        complete('cat1_small_move');
        for (let index = 0; index < 45; index += 1) {{
          now += 10;
          win.dispatchEvent(new CustomEventLike('neko:idle-return-ball-state', {{
            detail: {{ source: 'desktop-window', tier: 'cat1', timestamp: now, visible: true }}
          }}));
        }}
        assert.equal(win.nekoCatMind.getState().recentEventCount, 40);
        assert.deepEqual(JSON.parse(JSON.stringify(win.nekoCatMind.getDebugSnapshot().returnEpisode.preview)), {{
          kind: 'activity', highlight: 'small_move'
        }});
        assert.deepEqual(JSON.parse(JSON.stringify(returnSummary().episode)), {{
          kind: 'activity', highlight: 'small_move'
        }});

        // CAT2 then CAT3 feedback keeps one rest-after-activity chapter even
        // when a presentation-only drag demotion happens afterwards.
        start();
        primeInteraction(16);
        complete('cat1_small_move');
        setTier('cat2');
        complete('cat2_nap_feedback');
        setTier('cat3');
        complete('cat3_sleep_feedback');
        setTier('cat1', 'return-ball-drag-demotion');
        episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter)), {{ interactionSeen: false, activityKinds: [] }});
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.lastRest)), {{
          hadActivityBeforeRest: true, highlight: 'cat1_small_move'
        }});
        assert.deepEqual(JSON.parse(JSON.stringify(returnSummary().episode)), {{
          kind: 'rest_after_activity', highlight: 'small_move'
        }});

        // A rest followed only by a later interaction must suppress the old
        // rest. A following real sleep feedback starts a fresh, unqualified
        // rested chapter instead of reviving the old activity relation.
        start();
        setTier('cat2');
        complete('cat2_nap_feedback');
        observe('cat_hover_reaction');
        assert.equal(Object.prototype.hasOwnProperty.call(returnSummary(), 'episode'), false);

        start();
        setTier('cat2');
        complete('cat2_nap_feedback');
        observe('cat_hover_reaction');
        setTier('cat3');
        complete('cat3_sleep_feedback');
        assert.deepEqual(JSON.parse(JSON.stringify(returnSummary().episode)), {{ kind: 'rested' }});

        // A new activity/rest pair replaces an older pair, so only the final
        // trustworthy natural chapter is carried back.
        start();
        primeInteraction(16);
        complete('cat1_small_move');
        setTier('cat2');
        complete('cat2_nap_feedback');
        setTier('cat1');
        complete('cat1_social_ping');
        setTier('cat3');
        complete('cat3_sleep_feedback');
        assert.deepEqual(JSON.parse(JSON.stringify(returnSummary().episode)), {{
          kind: 'rest_after_activity', highlight: 'social_ping'
        }});

        // A new completed activity after rest wins immediately, even if no
        // later sleep feedback arrives to close another rest chapter.
        start();
        primeInteraction(16);
        complete('cat1_small_move');
        setTier('cat2');
        complete('cat2_nap_feedback');
        setTier('cat1');
        complete('cat1_social_ping');
        assert.deepEqual(JSON.parse(JSON.stringify(returnSummary().episode)), {{
          kind: 'activity', highlight: 'social_ping'
        }});

        // Interaction, tier, and presentation observations remain evidence for
        // Cat Mind itself, but never create a return episode on their own.
        start();
        observe('cat_hover_reaction');
        assert.equal(Object.prototype.hasOwnProperty.call(returnSummary(), 'episode'), false);
        start();
        setTier('cat2');
        assert.equal(Object.prototype.hasOwnProperty.call(returnSummary(), 'episode'), false);
        start();
        now += 10;
        win.dispatchEvent(new CustomEventLike('neko:idle-return-ball-state', {{
          detail: {{ source: 'desktop-window', tier: 'cat1', timestamp: now, visible: true }}
        }}));
        flushDecision();
        assert.equal(Object.prototype.hasOwnProperty.call(returnSummary(), 'episode'), false);

        // Reset/new entry always starts with an empty local accumulator.
        restart();
        episodeDebug = win.nekoCatMind.getDebugSnapshot().returnEpisode;
        assert.deepEqual(JSON.parse(JSON.stringify(episodeDebug.activeChapter)), {{ interactionSeen: false, activityKinds: [] }});
        assert.equal(episodeDebug.lastRest, null);
        assert.equal(episodeDebug.preview, null);
        const cleanSummary = returnSummary();
        assert.equal(Object.prototype.hasOwnProperty.call(cleanSummary, 'has_started_autonomous_action'), false);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase4_return_summary_draft_is_consumed_once():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const source = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        const win = new EventTargetLike();
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        const context = {{ window: win, CustomEvent: CustomEventLike, Date: {{ now: () => now }}, console }};
        vm.createContext(context);
        vm.runInContext(source, context);
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        now += 1000;
        win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        const beforeConsume = win.nekoCatMind.getReturnSummaryDraft();
        assert.ok(beforeConsume);
        assert.equal(Object.prototype.hasOwnProperty.call(beforeConsume, 'has_started_autonomous_action'), false);
        const consumed = win.nekoCatMind.consumeReturnSummaryDraft();
        assert.deepEqual(JSON.parse(JSON.stringify(consumed)), JSON.parse(JSON.stringify(beforeConsume)));
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);
        assert.equal(win.nekoCatMind.getState().returnSummaryDraft, null);
        assert.equal(win.nekoCatMind.consumeReturnSummaryDraft(), null);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cat_mind_phase4_return_attaches_once_then_preserves_existing_silent_fallbacks():
    """Load the real listener order: Cat Mind → websocket handler → auto-goodbye."""
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const catMindSource = fs.readFileSync({json.dumps(str(CAT_MIND_PATH))}, 'utf8');
        const autoGoodbyeSource = fs.readFileSync({json.dumps(str(APP_AUTO_GOODBYE_PATH))}, 'utf8');
        const websocketSource = fs.readFileSync({json.dumps(str(APP_WEBSOCKET_PATH))}, 'utf8');
        const websocketGateStart = websocketSource.indexOf('const CAT_GREETING_SILENT_BELOW_SECONDS');
        const websocketGateEnd = websocketSource.indexOf('\\n', websocketGateStart) + 1;
        const websocketStart = websocketSource.indexOf("window.addEventListener('neko:cat-greeting-check'");
        const websocketEnd = websocketSource.indexOf('// ========================  Export module', websocketStart);
        assert.ok(websocketGateStart >= 0 && websocketGateEnd > websocketGateStart);
        assert.ok(websocketStart >= 0 && websocketEnd > websocketStart);
        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          removeEventListener() {{}}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{ constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail || {{}}; }} }}
        let now = 1000;
        let sendThrows = false;
        let allowStartedAction = false;
        let actionRunNumber = 0;
        const sentMessages = [];
        const selectorTimers = [];
        const win = new EventTargetLike();
        const doc = new EventTargetLike();
        const classList = {{ contains() {{ return false; }}, add() {{}}, remove() {{}} }};
        doc.readyState = 'complete';
        doc.body = {{ classList }};
        doc.getElementById = (id) => id === 'resetSessionButton' || id === 'screenButton'
          ? {{ classList, style: {{}}, getBoundingClientRect() {{ return {{ width: 32, height: 32 }}; }} }}
          : null;
        doc.querySelectorAll = () => [];
        doc.addEventListener = EventTargetLike.prototype.addEventListener.bind(doc);
        doc.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(doc);
        win.document = doc;
        win.location = {{ pathname: '/' }};
        win.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
        win.appConst = {{}};
        win.appState = {{ socket: {{
          readyState: 1,
          send(payload) {{
            if (sendThrows) throw new Error('send failed');
            sentMessages.push(JSON.parse(payload));
          }}
        }} }};
        win.live2dManager = {{ _goodbyeClicked: false }};
        win.vrmManager = {{ _goodbyeClicked: false }};
        win.mmdManager = {{ _goodbyeClicked: false }};
        win._agentTaskMap = new Map();
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        win.getComputedStyle = () => ({{ display: 'block', visibility: 'visible', opacity: '1' }});
        const context = {{
          window: win,
          document: doc,
          CustomEvent: CustomEventLike,
          WebSocket: {{ OPEN: 1 }},
          Date: {{ now: () => now }},
          Math,
          Promise,
          Map,
          Set,
          Array,
          localStorage: win.localStorage,
          navigator: {{ language: 'en-US' }},
          console,
        }};
        vm.createContext(context);
        vm.runInContext(catMindSource, context);
        vm.runInContext(
          'var S = window.appState;\\n' +
          websocketSource.slice(websocketGateStart, websocketGateEnd) +
          websocketSource.slice(websocketStart, websocketEnd),
          context
        );
        vm.runInContext(autoGoodbyeSource, context);
        const greetingEvents = [];
        win.addEventListener('neko:cat-greeting-check', (event) => greetingEvents.push(event.detail));
        win.NekoCatMindActionProviders = {{
          getRuntimeGateSnapshot() {{
            return {{ returnPending: false, dragPending: false, dragging: false, transitionActive: false,
              activeIndependentAction: false, returnBallVisible: true, validCatRuntime: true,
              chatSurfaceDragging: false }};
          }},
          dryRun(actionId) {{
            return actionId === 'cat1_social_ping' && allowStartedAction
              ? {{ allowed: true, reason: 'allowed' }}
              : {{ allowed: false, reason: 'not_for_return_listener_test' }};
          }}
        }};
        win.addEventListener('neko:cat-mind:action-request', (event) => {{
          const request = event.detail;
          const runId = 'return-listener-run-' + (++actionRunNumber);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId: request.actionId, status: 'accepted', runId, timestamp: now
          }}), true);
          assert.equal(win.nekoCatMind.acknowledgeActionRequest({{
            requestId: request.requestId, actionId: request.actionId, status: 'started', runId, timestamp: now
          }}), true);
        }});

        function enterAndReturn(durationMs) {{
          win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
            detail: {{ source: 'manual-goodbye', timestamp: now }}
          }}));
          now += durationMs;
          win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        }}
        function catGreetingMessages() {{
          return sentMessages.filter((message) => message && message.action === 'cat_greeting_check');
        }}
        function startStrictCatMindAction() {{
          // Normal return tests deliberately have no selector timer. Enable a
          // controlled one only here, then obtain the real accepted→started
          // lifecycle before returning without a terminal result.
          win.setTimeout = (callback) => {{ selectorTimers.push(callback); return selectorTimers.length; }};
          allowStartedAction = true;
              now += 15 * 60 * 1000;
              win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {{
                detail: {{ type: 'cat_elapsed', source: 'cat-mind-clock', tier: 'cat1', timestamp: now,
              detail: {{ elapsedMs: 15 * 60 * 1000 }} }}
          }}));
          assert.equal(selectorTimers.length, 1);
          selectorTimers.shift()();
          assert.ok(win.nekoCatMind.getState().activeAction);
          allowStartedAction = false;
        }}

        enterAndReturn(190 * 1000);
        let greetings = catGreetingMessages();
        assert.equal(greetings.length, 1);
        assert.ok(greetings[0].cat_memory_summary);
        assert.equal(greetings[0].cat_memory_summary.entry, 'manual');
        assert.equal(Object.prototype.hasOwnProperty.call(greetings[0].cat_memory_summary, 'has_started_autonomous_action'), false);
        assert.equal(greetingEvents.length, 1);
        assert.deepEqual(
          JSON.parse(JSON.stringify(greetingEvents[0].catMemorySummary)),
          JSON.parse(JSON.stringify(greetings[0].cat_memory_summary))
        );
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);
        assert.equal(win.nekoCatMind.consumeReturnSummaryDraft(), null);

        // A short return stays silent, but its one-shot draft is still
        // consumed by this return and cannot leak to the next one.
        now += 10;
        enterAndReturn(1000);
        assert.equal(catGreetingMessages().length, 1);
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);

        // A strictly started action opens the short-return delivery gate, but
        // without a terminal done result it contributes no narrative episode.
        now += 10;
        win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
          detail: {{ source: 'manual-goodbye', timestamp: now }}
        }}));
        startStrictCatMindAction();
        now += 1000;
        win.dispatchEvent(new CustomEventLike('live2d-return-click'));
        greetings = catGreetingMessages();
        assert.equal(greetings.length, 2);
        assert.equal(greetings[1].cat_memory_summary.has_started_autonomous_action, true);
        assert.equal(Object.prototype.hasOwnProperty.call(greetings[1].cat_memory_summary, 'episode'), false);
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);

        // A closed socket and a send failure keep the old silent/failure path;
        // neither is allowed to retain a summary for the next return.
        now += 10;
        win.appState.socket.readyState = 0;
        enterAndReturn(190 * 1000);
        assert.equal(catGreetingMessages().length, 2);
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);

        now += 10;
        win.appState.socket.readyState = 1;
        sendThrows = true;
        enterAndReturn(190 * 1000);
        assert.equal(catGreetingMessages().length, 2);
        assert.equal(win.nekoCatMind.getReturnSummaryDraft(), null);
        sendThrows = false;

        // If Cat Mind is unavailable, the original cat greeting event and
        // websocket payload still work without an extra field.
        now += 10;
        win.nekoCatMind = null;
        enterAndReturn(190 * 1000);
        greetings = catGreetingMessages();
        assert.equal(greetings.length, 3);
        assert.equal(Object.prototype.hasOwnProperty.call(greetings[2], 'cat_memory_summary'), false);

        now += 10;
        win.nekoCatMind = {{
          consumeReturnSummaryDraft() {{ throw new Error('consumer unavailable'); }}
        }};
        enterAndReturn(190 * 1000);
        greetings = catGreetingMessages();
        assert.equal(greetings.length, 4);
        assert.equal(Object.prototype.hasOwnProperty.call(greetings[3], 'cat_memory_summary'), false);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout
