import re
from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-audio-capture.js"

pytestmark = pytest.mark.integration_serial


def test_independent_asr_toggle_awaits_server_sync_before_next_session():
    # Session start reads the SERVER-persisted independentAsrEnabled value
    # (asr_runtime.py _start_independent_asr_if_enabled), so the toggle must
    # not rely on the fire-and-forget POST inside saveSettings(): it persists
    # locally, runs the POST itself, and publishes the in-flight promise for
    # the session-start path to await.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    persist_block = capture_source.split(
        "function persistVoiceSettingChange() {", 1
    )[1].split("function markVoiceSettingsPending", 1)[0]
    toggle_block = capture_source.split(
        "var asrToggle = createVoiceSettingToggle(", 1
    )[1].split("asrRow.appendChild(asrCopy);", 1)[0]
    assert "persistVoiceSettingChange();" in toggle_block
    assert "window.appSettings.saveSettings({ skipServerSync: true });" in persist_block
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in persist_block
    assert "S.pendingSettingsSyncPromise = syncPromise;" in persist_block
    # Completion clears the gate only when it still owns it (a newer toggle
    # may have replaced the pending promise meanwhile).
    assert "if (S.pendingSettingsSyncPromise === syncPromise)" in persist_block
    assert "S.pendingSettingsSyncPromise = null;" in persist_block
    # Fallback when the settings module does not expose syncSettingsToServer.
    assert "window.appSettings.saveSettings();" in persist_block

    gate_block = websocket_source.split(
        "function ensureWebSocketOpen(timeoutMs = 5000)",
        1,
    )[1].split("function ensureWebSocketOpenNow(timeoutMs)", 1)[0]
    assert "S.pendingSettingsSyncPromise" in gate_block
    # Negative: only thenables gate; anything else falls through immediately.
    assert "typeof pendingSync.then === 'function'" in gate_block
    # The wait is bounded and never rejects, so a hung or failed POST cannot
    # block session starts or socket-dependent flows.
    assert "Promise.race([" in gate_block
    assert "SETTINGS_SYNC_GATE_TIMEOUT_MS" in gate_block
    assert "pendingSync.catch(" in gate_block
    assert "return ensureWebSocketOpenNow(timeoutMs);" in gate_block
    assert "var SETTINGS_SYNC_GATE_TIMEOUT_MS = 3000;" in websocket_source


def test_noise_reduction_toggle_uses_conversation_settings_cas_client():
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    save_block = capture_source.split(
        "function saveNoiseReductionSetting() {",
        1,
    )[1].split("function loadNoiseReductionSetting()", 1)[0]
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    assert "window.appSettings.saveSettings();" in save_block
    assert "fetch('/api/config/conversation-settings'" not in save_block
    assert "'noiseReductionEnabled'," in settings_source
    assert "noiseReductionEnabled: S.noiseReductionEnabled" in settings_source


def test_settings_hydration_marked_on_server_merge_and_user_change():
    # S.settingsHydrated must flip true on authoritative settings evidence:
    # events, and never merely at boot:
    #   (1) the conversation-settings GET succeeded (server values merged);
    #   (2) the user explicitly changed a setting — the independent-ASR toggle
    #       handler (app-audio-capture.js) runs saveSettings({skipServerSync})
    #       + syncSettingsToServer({ userInitiated: true }), so the synchronous
    #       marker inside syncSettingsToServer covers it even when the POST
    #       later fails (a user action is authoritative even pre-hydration);
    #   (3) a cross-window independent-ASR flip arrived via the 'storage'
    #       listener — the originating window's user action, pinned by
    #       test_cross_window_asr_flip_marks_hydration_and_asr_dirty.
    #   (4) a durable, explicit optimization decision survived a reload while
    #       its server synchronization is still pending.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")

    # (1) Server merge marks hydration only after the null-guard, i.e. only
    # when the GET actually returned a usable result.
    merge_block = settings_source.split(
        "loadSettingsFromServer().then(serverResult => {",
        1,
    )[1].split(".finally(", 1)[0]
    guard_index = merge_block.index("if (!serverResult) return;")
    hydrate_index = merge_block.index("S.settingsHydrated = true;")
    assert guard_index < hydrate_index, (
        "hydration must only be marked after the serverResult null-guard"
    )

    # (2) syncSettingsToServer marks hydration synchronously, before any
    # await, so a failed POST still leaves the user's choice authoritative
    # and the start_session handshake keeps carrying it — but ONLY for
    # userInitiated callers. The periodic timer passes no options and must
    # never mark hydration (pinned by
    # test_periodic_sync_skips_post_and_never_marks_hydration_while_unhydrated).
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split(
        "function startPeriodicSync()",
        1,
    )[0]
    assert sync_fn.count("S.settingsHydrated = true;") == 1
    user_initiated_gate = _block_after(sync_fn, "if (userInitiated) {")
    assert "S.settingsHydrated = true;" in user_initiated_gate, (
        "the hydration mark must sit inside the userInitiated gate"
    )
    assert sync_fn.index("S.settingsHydrated = true;") < sync_fn.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )

    # The independent-ASR toggle handler reaches that marker via
    # syncSettingsToServer({ userInitiated: true }); the saveSettings call it
    # makes skips the internal server sync, so the direct call is the seam.
    toggle_handler = capture_source.split(
        "var asrToggle = createVoiceSettingToggle(", 1
    )[1].split("asrRow.appendChild(asrCopy);", 1)[0]
    persist_block = capture_source.split(
        "function persistVoiceSettingChange() {", 1
    )[1].split("function markVoiceSettingsPending", 1)[0]
    assert "S.independentAsrEnabled = enabled;" in toggle_handler
    assert "persistVoiceSettingChange();" in toggle_handler
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in persist_block

    # Boot must NOT mark hydration: the first-launch initialization save goes
    # through saveSettings({ skipServerSync: true }) which bypasses
    # syncSettingsToServer, keeping the default-false value non-authoritative
    # until the GET resolves or the user acts.
    first_launch_block = settings_source.split(
        "console.log('未找到保存的设置，使用默认值');",
        1,
    )[1].split("} catch (error) {", 1)[0]
    assert "saveSettings({ skipServerSync: true });" in first_launch_block
    assert "S.settingsHydrated" not in first_launch_block
    # The only synchronous load-time hydration is guarded by a durable pending
    # optimization decision; ordinary boot defaults still cannot gain authority.
    sync_load_body = settings_source.split("function loadSettings()", 1)[1].split(
        "loadSettingsFromServer().then(serverResult => {",
        1,
    )[0]
    assert sync_load_body.count("S.settingsHydrated = true;") == 1
    assert "bootMeta.optimizationDecisionPendingSync" in sync_load_body
    assert (
        sync_load_body.index("bootMeta.optimizationDecisionPendingSync")
        < sync_load_body.index("S.settingsHydrated = true;")
    )


def test_periodic_sync_skips_post_and_never_marks_hydration_while_unhydrated():
    # Persistent GET failure: loadSettingsFromServer resolves null (or the
    # whole chain throws), yet BOTH failure paths still start the periodic
    # task (the .finally() after the merge callback, and the outer catch).
    # Before the userInitiated split, syncSettingsToServer's entry marked
    # S.settingsHydrated unconditionally, so the 60s tick (a) uploaded the
    # boot default independentAsrEnabled=false over the server-persisted
    # true and (b) falsely armed the start_session handshake with that
    # default. Pin the two-part fix.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # Both GET-failure paths do start the periodic task — that is exactly why
    # the tick itself must carry the guard.
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    finally_block = load_fn.split("}).finally(() => {", 1)[1].split(
        "});", 1
    )[0]
    assert "startPeriodicSync();" in finally_block
    load_catch_block = _block_after(
        settings_source, "console.error('服务器设置同步启动失败:', error);"
    )
    assert "startPeriodicSync();" in load_catch_block

    # (1) The tick refuses to POST while settings were never hydrated (no
    # successful GET and no user change), logging the skip only once.
    tick_body = settings_source.split("_syncTimerId = setInterval(() => {", 1)[1].split(
        "}, SYNC_INTERVAL_MS);",
        1,
    )[0]
    hydration_guard_index = tick_body.index("if (S.settingsHydrated !== true) {")
    sync_call_index = tick_body.index("syncSettingsToServer();")
    assert hydration_guard_index < sync_call_index, (
        "the unhydrated guard must run before the periodic POST"
    )
    guard_block = tick_body[hydration_guard_index:sync_call_index]
    assert "return;" in guard_block
    assert "_periodicSyncSkippedUnhydratedLogged" in guard_block

    # (2) The periodic caller passes no options, so even a tick that does run
    # (post-hydration, or if the guard ever regressed) can never be the event
    # that marks hydration — only userInitiated callers mark (pinned in
    # test_settings_hydration_marked_on_server_merge_and_user_change).
    assert "userInitiated" not in tick_body


def test_user_toggle_during_get_failure_marks_hydration_posts_and_stamps():
    # Round-10 semantics must survive the userInitiated split: an explicit
    # user change is an authoritative hydration source even while the settings
    # GET keeps failing. The independent-ASR toggle marks S.settingsHydrated
    # synchronously (before its POST awaits) and publishes the POST; the
    # start_session handshake then stamps the user's choice.
    capture_source = APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    # The toggle's direct sync call is user-initiated and still POSTs.
    toggle_block = capture_source.split(
        "var asrToggle = createVoiceSettingToggle(", 1
    )[1].split("asrRow.appendChild(asrCopy);", 1)[0]
    persist_block = capture_source.split(
        "function persistVoiceSettingChange() {", 1
    )[1].split("function markVoiceSettingsPending", 1)[0]
    assert "persistVoiceSettingChange();" in toggle_block
    assert "window.appSettings.syncSettingsToServer({ userInitiated: true })" in persist_block

    # saveSettings' full (non-skipServerSync) path is the other user seam —
    # the settings popup, subtitle toggles and chat-window toggles all route
    # through it — so it must pass userInitiated too.
    save_fn = settings_source.split("function saveSettings(options)", 1)[1].split(
        "function loadSettings()",
        1,
    )[0]
    assert "syncSettingsToServer({ userInitiated: true });" in save_fn
    # ... while the first-launch boot save keeps skipping the sync entirely,
    # so boot defaults still never mark hydration.
    assert "saveSettings({ skipServerSync: true });" in settings_source

    # And the handshake stamp keys off exactly that flag, so the toggle's
    # pre-hydration change reaches the backend on the next start_session.
    wrapper = websocket_source.split(
        "function attachStartSessionHandshake(ws)",
        1,
    )[1].split("function connectWebSocket()", 1)[0]
    assert "msg.action === 'start_session' && S.settingsHydrated === true" in wrapper
    assert "msg.independent_asr_enabled = S.independentAsrEnabled === true;" in wrapper


def test_settings_post_snapshot_waits_bounded_for_boot_get_merge():
    # Codex P2 (merge-before-post): a POST issued while the boot GET is still
    # pending used to snapshot pure local state, so unchanged fields carried
    # boot defaults. The queued runSync now awaits a bounded, never-rejecting
    # gate that settles when the GET's merge settled — the send-time snapshot
    # is therefore assembled AFTER the merge whenever the GET has resolved,
    # and unchanged fields carry server truth. If the GET outlives the bound
    # the POST proceeds with local state and the merge's writeback
    # saveSettings() converges the server afterwards.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    assert "let _settingsGetGate = Promise.resolve();" in settings_source
    assert "const SETTINGS_GET_GATE_TIMEOUT_MS = 3000;" in settings_source

    # The gate await sits inside the queued runSync, before the send-time
    # snapshot and the fetch.
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    run_sync_body = sync_fn.split("const runSync = async () =>", 1)[1]
    gate_index = run_sync_body.index("await _settingsGetGate;")
    snapshot_index = run_sync_body.index("const settings = getConversationSettings();")
    fetch_index = run_sync_body.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )
    assert gate_index < snapshot_index < fetch_index

    # The gate is armed at GET issue time as a race between the settled merge
    # chain (with a catch so it can never reject) and the bounded timeout.
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    assert "const mergeSettled = loadSettingsFromServer().then(serverResult => {" in load_fn
    gate_assign_index = load_fn.index("_settingsGetGate = Promise.race([")
    assert load_fn.index("startPeriodicSync();") < gate_assign_index
    gate_block = load_fn[gate_assign_index:].split("]);", 1)[0]
    assert "mergeSettled.catch(() => { })" in gate_block
    assert "setTimeout(resolve, SETTINGS_GET_GATE_TIMEOUT_MS)" in gate_block

    # Negative: the synchronous hydration/dirty marks stay at call time —
    # only the POST body waits for the merge, never the authority marks.
    assert sync_fn.index("S.settingsHydrated = true;") < sync_fn.index(
        "const runSync = async () =>"
    )
    assert sync_fn.index("_markUserDirtySettings();") < sync_fn.index(
        "const runSync = async () =>"
    )


def test_settings_posts_serialize_so_a_stale_body_cannot_win_persistence():
    # Codex P2 (round 13): flipping the ASR toggle twice before the first POST
    # completed used to start two independent syncSettingsToServer calls with
    # their own snapshots; the backend saves each in a separate
    # asyncio.to_thread, so the OLDER request could finish LAST and persist the
    # earlier toggle value. Pin the serialization fix: every sync queues behind
    # a module-level chain tail and builds its settings snapshot at SEND time
    # (inside the queued runSync), so at most one POST is in flight and the
    # last-issued request always carries the final local state.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # The chain tail starts resolved and is module-scoped (shared by the
    # toggle path AND the periodic tick, so those cannot race each other
    # either).
    assert "let _syncChainTail = Promise.resolve();" in settings_source

    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]

    # Chaining structure: the queued body is attached on BOTH fulfillment and
    # rejection so one failed sync cannot stall the tail, the tail advances to
    # the newly chained promise, and the caller gets that promise back (the
    # toggle handler publishes it as S.pendingSettingsSyncPromise for the
    # ensureWebSocketOpen gate).
    assert "const chained = _syncChainTail.then(runSync, runSync);" in sync_fn
    assert "_syncChainTail = chained;" in sync_fn
    assert "return chained;" in sync_fn

    # The settings snapshot is built inside the queued runSync — at send time,
    # after the predecessor completed — not at call time.
    run_sync_index = sync_fn.index("const runSync = async () =>")
    snapshot_index = sync_fn.index("const settings = getConversationSettings();")
    fetch_index = sync_fn.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )
    assert run_sync_index < snapshot_index < fetch_index

    # Negative: the synchronous hydration mark and dirty-key recording stay
    # at call time, BEFORE the queued body — deferring them would reopen the
    # stale-GET-merge window and the pre-hydration handshake gap.
    assert sync_fn.index("S.settingsHydrated = true;") < run_sync_index
    assert sync_fn.index("_markUserDirtySettings();") < run_sync_index


def test_cross_window_settings_posts_use_cas_and_persist_asr_decision_order():
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    sync_fn = _block_after(
        settings_source, "async function syncSettingsToServer(options) {"
    )

    assert "let _conversationSettingsEtag = null;" in settings_source
    assert "headers['If-Match'] = _conversationSettingsEtag;" in sync_fn
    assert "response.status === 412" in sync_fn
    assert "const preservedKeys = new Set(_pendingSettingsKeys);" in sync_fn
    assert "_conversationSettingsEtagKeyMutationVersions" in sync_fn
    assert "_crossWindowSettingsNewerThanEtag(" in sync_fn
    assert "etagKeyMutationVersionsAtSend" in sync_fn
    assert "_markEtagConfirmedSharedSettings(" in sync_fn
    assert "_settingsChangedSince(settings, mutationVersionAtSend).forEach" in sync_fn
    assert "_mergeConversationSettingsSnapshot(data, preservedKeys);" in sync_fn
    assert "_CONVERSATION_SETTINGS_MAX_ATTEMPTS" in sync_fn
    assert "headers['X-Conversation-Settings-ASR-Decision']" in sync_fn
    assert "JSON.stringify(requestDecision)" in sync_fn
    mark_signature = re.search(
        r"function _markEtagConfirmedSharedSettings\([^)]*\)\s*\{",
        settings_source,
    )
    assert mark_signature is not None
    mark_confirmed = _block_after(settings_source, mark_signature.group(0))
    assert "payloadWasFull" not in mark_confirmed
    assert "settingsAtSend[key] !== serverSettings[key]" in mark_confirmed

    # Both the state snapshot and the ASR token are rebuilt inside the retry
    # loop. An older window that loses the server decision comparison must not
    # resend its stale pre-conflict body.
    retry_loop = sync_fn.split(
        "for (let attempt = 0; attempt < _CONVERSATION_SETTINGS_MAX_ATTEMPTS;",
        1,
    )[1]
    assert retry_loop.index("const settings = getConversationSettings();") < retry_loop.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )
    assert retry_loop.index("const requestDecision = (") < retry_loop.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )


def test_cross_window_asr_flip_marks_hydration_and_asr_dirty():
    # Codex P2: a cross-window independent-ASR toggle arrives via the
    # 'storage' listener, which used to copy the value into S without marking
    # S.settingsHydrated or the key's authority. In the receiving
    # window that meant (a) the next start_session omitted the handshake field
    # (the stamp is gated on S.settingsHydrated, pinned by
    # test_start_session_handshake_omitted_until_settings_hydrated), so the
    # backend read the OLD persisted value while the originating window's POST
    # was still in flight, and (b) a still-pending settings GET later merged
    # the stale server snapshot over the flip and POSTed it back via
    # saveSettings(). Pin the fix: the flip is detected before the apply and
    # treated as an authoritative hydration event that marks the ASR key
    # dirty (so the field-level merge preserves it), with no POST from the
    # receiving window.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]

    # Flip detection: own-property guard plus strict inequality against S,
    # computed BEFORE applySharedRuntimeSettings mutates S.
    assert (
        "Object.prototype.hasOwnProperty.call(settings, 'independentAsrEnabled')"
        in listener_block
    )
    assert "S.independentAsrEnabled !== settings.independentAsrEnabled" in listener_block
    assert listener_block.index("const asrChangedByOtherWindow") < listener_block.index(
        "applySharedRuntimeSettings(incoming)"
    )

    # Hydration mark + ASR dirty mark sit inside the ASR-flip gate only.
    flip_gate = listener_block.split("if (asrChangedByOtherWindow) {", 1)[1].split(
        "}", 1
    )[0]
    assert "S.settingsHydrated = true;" in flip_gate
    assert "_dirtySettingsKeys.add('independentAsrEnabled');" in flip_gate
    optimization_gate = listener_block.split(
        "if (optimizationChangedByOtherWindow) {",
        1,
    )[1].split("}", 1)[0]
    assert "S.settingsHydrated = true;" in optimization_gate
    assert listener_block.count("S.settingsHydrated = true;") == 2
    assert listener_block.count("_dirtySettingsKeys.add('independentAsrEnabled');") == 1

    # No POST from the receiving window: the originating window owns
    # persistence, and a receiving-window POST would duplicate writes and
    # loop storage events between windows. (Assert on code lines only — the
    # in-source comment legitimately names saveSettings.)
    listener_code = "\n".join(
        line
        for line in listener_block.splitlines()
        if not line.strip().startswith("//")
    )
    assert "syncSettingsToServer" not in listener_code
    assert "saveSettings();" not in listener_code
    if "saveSettings({" in listener_code:
        assert "skipServerSync: true" in listener_code
    assert "syncSettingsToServer" not in listener_code
    assert "fetch(" not in listener_code

    # Negative: applySharedRuntimeSettings itself must stay authority-neutral —
    # other shared keys (and non-flip events) keep syncing values across
    # windows without marking hydration or dirtying keys, so a
    # first-launch boot-defaults write in another window can never arm this
    # window's periodic sync or handshake.
    apply_fn = settings_source.split(
        "function applySharedRuntimeSettings(settings) {", 1
    )[1].split("function isManualScreenShareActive()", 1)[0]
    assert "settingsHydrated" not in apply_fn
    assert "_dirtySettingsKeys" not in apply_fn
    assert "_markUserDirtySettings" not in apply_fn


def test_settings_get_gate_timeout_downgrades_post_to_dirty_keys_only():
    # Codex P2 (round 15): the bounded gate preserves liveness, but on timeout
    # it used to release a FULL boot snapshot — overwriting every preference
    # the user never touched. The backend resolves the telemetry branch BEFORE
    # reading the settings file (main_routers/config_router/preferences.py
    # get_conversation_settings), so a slow GET resumes by reading the file the
    # POST just overwrote and the field-level merge can no longer restore the
    # originals. Pin the fix: while the GET chain is unsettled the POST body is
    # restricted to the explicitly dirty keys, which is safe because the
    # backend MERGES partial payloads (utils/preferences.py
    # save_global_conversation_settings -> global_pref.update(filtered_settings)).
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # Round 16 (Codex P2 follow-up): the gate flag must track a SUCCESSFUL
    # merge, not merely "the GET attempt finished". It starts false (nothing
    # merged yet) and is never re-armed in loadSettings — a merge that already
    # happened stays valid.
    assert "let _settingsMergedFromServer = false;" in settings_source
    assert "_settingsGetSettled" not in settings_source
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    assert "_settingsMergedFromServer = false;" not in load_fn
    # It flips to true ONLY inside the merge callback, past the
    # `if (!serverResult) return;` guard, i.e. only when server values were
    # really applied — and before the merge writeback so that POST is full.
    merge_cb = load_fn.split("loadSettingsFromServer().then(serverResult => {", 1)[1].split(
        "}).finally(() => {", 1
    )[0]
    assert "if (!serverResult) return;" in merge_cb
    assert merge_cb.index("if (!serverResult) return;") < merge_cb.index(
        "_settingsMergedFromServer = true;"
    )
    assert merge_cb.index("_settingsMergedFromServer = true;") < merge_cb.index(
        "saveSettings({"
    )
    assert "serverAuthoritativeKeys: Object.keys(" in merge_cb
    # Negative: the failure paths must NOT re-enable full snapshots. The
    # finally runs for merged AND failed GETs, so it may not touch the flag;
    # neither may the synchronous-throw catch, where nothing was ever read.
    finally_block = load_fn.split("}).finally(() => {", 1)[1].split("});", 1)[0]
    assert "_settingsMergedFromServer =" not in finally_block
    assert "startPeriodicSync();" in finally_block
    startup_catch = load_fn.split("console.error('服务器设置同步启动失败:', error);", 1)[1]
    assert "_settingsMergedFromServer = true;" not in startup_catch

    # The send-time body: full snapshot only when server values were merged,
    # dirty-keys-only otherwise, and the fetch must post THAT body.
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    run_sync_body = sync_fn.split("const runSync = async () =>", 1)[1]
    assert (
        "const payload = _settingsMergedFromServer ? settings : _pickDirtySettings(settings);"
        in run_sync_body
    )
    assert "body: JSON.stringify(payload)" in run_sync_body
    assert "JSON.stringify(settings)" not in run_sync_body
    # Ordering: gate await -> snapshot -> payload choice -> fetch.
    assert (
        run_sync_body.index("await _settingsGetGate;")
        < run_sync_body.index("const settings = getConversationSettings();")
        < run_sync_body.index("const payload =")
        < run_sync_body.index("await _fetchConversationSettingsJsonWithTimeout(")
    )
    # An empty dirty set means nothing user-authoritative exists yet: skip the
    # POST entirely rather than write pre-merge values.
    assert "if (Object.keys(payload).length === 0) {" in run_sync_body
    assert run_sync_body.index("if (Object.keys(payload).length === 0) {") < run_sync_body.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )

    # The picker copies ONLY pending keys (negative: acknowledged or untouched
    # keys cannot be dragged back into the partial body).
    pick_fn = settings_source.split("function _pickDirtySettings(settings) {", 1)[1].split(
        "function applySharedRuntimeSettings", 1
    )[0]
    assert "_pendingSettingsKeys.forEach((key) => {" in pick_fn
    assert "Object.prototype.hasOwnProperty.call(settings, key)" in pick_fn
    assert "partial[key] = settings[key];" in pick_fn
    assert "Object.keys(settings)" not in pick_fn
    assert "Object.assign" not in pick_fn


def test_concurrent_asr_toggles_are_totally_ordered_not_swapped():
    # Codex P2. The intent tie-break added last round did not fix the real
    # failure: _lastAppliedSharedWriteId only records writes RECEIVED here, so a
    # window never orders its OWN pending toggle against a concurrent one from
    # another window. Two windows holding divergent values that both write
    # before observing each other therefore each adopt the other and stay
    # swapped -- and that needs no millisecond tie at all, a strictly older
    # foreign write still wins. Ordering must be against this window's own last
    # explicit decision, with a window-unique second key so both sides pick the
    # SAME winner.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # A window-unique writer id, minted per document load and stamped on writes.
    assert "const _SHARED_WRITER_ID" in settings_source
    assert "writerId: _SHARED_WRITER_ID," in settings_source
    # NOT sessionStorage: the browser copies it into a duplicated tab, which
    # would destroy the uniqueness the whole scheme rests on. Match the ACCESS
    # form -- the comment above deliberately names it.
    assert "sessionStorage." not in settings_source

    # Previous-build snapshots carry no writerId; it must fail low so an
    # untagged concurrent write cannot outrank this window's own choice.
    read_fn = settings_source.split("function _readSharedWriteMeta(", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "typeof meta.writerId === 'string' ? meta.writerId : ''" in read_fn

    # The comparison is (writeId, writerId) against the local decision.
    outranks = settings_source.split("function _settingWriteOutranksLocalChoice(", 1)[
        1
    ].split("\n    }", 1)[0]
    # Ordering is on the DECISION that produced the value, not on the id of the
    # write carrying it: a monotone dirty key makes every later unrelated save
    # re-declare the ASR key explicit with a fresh id, which would outrank a
    # genuinely newer toggle elsewhere (no race required).
    assert "decision.writeId > localDecision.writeId" in outranks
    assert "(decision.writerId || '') > localDecision.writerId" in outranks
    # A write with neither a decision tuple nor an explicit declaration is an
    # incidental copy and must never outrank a local choice.
    assert "if (!decision) return false;" in outranks
    # The decision must be DERIVED (tuple, else an explicit declaration), never
    # taken as the incoming write itself -- that is the bug being fixed.
    assert "const decision = meta[decisionKey]" in outranks
    assert "const decision = meta;" not in outranks

    # A window's OWN explicit write must be recorded, or it has nothing to
    # compare a concurrent foreign toggle against.
    write_fn = settings_source.split("function _writeSharedSettings(", 1)[1].split(
        "\n    }", 1
    )[0]
    asr_note = write_fn.split("_noteAsrDecision(", 1)[1].split(");", 1)[0]
    assert "_nextAsrDecisionWriteId(ownMeta.writeId)" in asr_note
    assert "ownMeta.writerId" in asr_note
    assert "snapshot.independentAsrEnabled" in asr_note

    # Refusing authority alone is not enough: applySharedRuntimeSettings copies
    # independentAsrEnabled unconditionally, so the losing write must also be
    # dropped from the apply set.
    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]
    assert "!asrOutranksLocalChoice" in listener_block.split("asrValueIsStale", 1)[1]
