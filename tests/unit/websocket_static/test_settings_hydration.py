import json
import textwrap
from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract


def test_user_dirty_keys_survive_boot_get_merge_field_level():
    # Codex P2 (field-level authority): the conversation-settings GET may read
    # the server BEFORE a user change POSTs its new value, yet resolve AFTER
    # it. The earlier whole-merge-drop design discarded the ENTIRE server
    # merge as soon as ANY userInitiated change happened while the GET was in
    # flight — so changing one unrelated preference made the full local
    # snapshot (including a boot-default independentAsrEnabled) authoritative
    # and the POST clobbered the persisted ASR choice. Pin the replacement:
    # a dirty-key set records exactly which settings the user changed, and the
    # merge applies server values to NON-dirty keys while preserving dirty
    # ones.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # (1) Dirty keys are recorded only inside the userInitiated gate of
    # syncSettingsToServer, synchronously alongside the hydration mark and
    # before any await.
    sync_fn = settings_source.split(
        "async function syncSettingsToServer(options)", 1
    )[1].split("function startPeriodicSync()", 1)[0]
    assert sync_fn.count("_markUserDirtySettings();") == 1
    user_initiated_gate = _block_after(sync_fn, "if (userInitiated) {")
    assert "_markUserDirtySettings();" in user_initiated_gate
    assert sync_fn.index("_markUserDirtySettings();") < sync_fn.index(
        "await _fetchConversationSettingsJsonWithTimeout("
    )

    # (2) loadSettings snapshots the pre-GET settings as the diff baseline
    # before issuing the GET, so keys changed while it is pending diverge
    # from the snapshot and get marked dirty.
    load_fn = settings_source.split("function loadSettings()", 1)[1]
    snapshot_index = load_fn.index("_settingsBaseline = getConversationSettings();")
    get_index = load_fn.index("loadSettingsFromServer().then(serverResult => {")
    assert snapshot_index < get_index

    # (3) The merge is field-level: pending keys are always preserved, while
    # acknowledged dirty keys yield only to a newer server revision. Both
    # guards run before the S mutation, the subtitle-bridge mirrors carry the
    # same gating, and
    # the baseline is rolled to the merged state BEFORE the writeback
    # saveSettings() so server-applied values are never misattributed as
    # user-dirty by the writeback's own userInitiated diff.
    merge_block = settings_source.split(
        "loadSettingsFromServer().then(serverResult => {", 1
    )[1].split(".finally(", 1)[0]
    null_guard_index = merge_block.index("if (!serverResult) return;")
    hydrate_index = merge_block.index("S.settingsHydrated = true;")
    assert null_guard_index < hydrate_index
    pending_skip_index = merge_block.index(
        "if (_pendingSettingsKeys.has(key)) continue;"
    )
    dirty_guard_index = merge_block.index("if (_dirtySettingsKeys.has(key)")
    mutation_index = merge_block.index("S[key] = serverSettings[key];")
    assert pending_skip_index < dirty_guard_index < mutation_index
    assert "&& !serverSnapshotNewerThanCurrent) continue;" in merge_block
    assert "!_pendingSettingsKeys.has('subtitleEnabled')" in merge_block
    assert "!_pendingSettingsKeys.has('userLanguage')" in merge_block
    assert "serverSnapshotNewerThanCurrent" in merge_block
    roll_index = merge_block.index("_settingsBaseline = getConversationSettings();")
    assert pending_skip_index < roll_index < merge_block.index("saveSettings({")
    assert "serverAuthoritativeKeys: Object.keys(" in merge_block
    # The whole-merge drop is gone: no early return between the null-guard
    # and the hydration mark, and the old drop log no longer exists.
    after_null_guard = null_guard_index + len("if (!serverResult) return;")
    assert "return;" not in merge_block[after_null_guard:hydrate_index]
    assert "丢弃过期的服务器合并" not in settings_source
    assert "_localSettingsGeneration" not in settings_source

    # (4) Negative validation — non-user flows never dirty keys: the periodic
    # tick passes no options (its POST is not a user change), the boot-time
    # skipServerSync save bypasses syncSettingsToServer entirely, and the
    # boot-merge authority set is monotone so a toggle-and-back survives a
    # stale in-flight GET. The separate pending set is cleared only after a
    # successful POST and only while the acknowledged value is still current.
    tick_body = settings_source.split("_syncTimerId = setInterval(() => {", 1)[1].split(
        "}, SYNC_INTERVAL_MS);", 1
    )[0]
    assert "_markUserDirtySettings" not in tick_body
    assert "_dirtySettingsKeys" not in tick_body
    first_launch_block = settings_source.split(
        "console.log('未找到保存的设置，使用默认值');", 1
    )[1].split("} catch (error) {", 1)[0]
    assert "_dirtySettingsKeys" not in first_launch_block
    assert "saveSettings({ skipServerSync: true });" in first_launch_block
    assert "_dirtySettingsKeys.delete" not in settings_source
    assert "_dirtySettingsKeys.clear" not in settings_source
    clear_fn = settings_source.split(
        "function _clearAcknowledgedPendingSettings(payload) {", 1
    )[1].split("function applySharedRuntimeSettings", 1)[0]
    assert "current[key] === payload[key]" in clear_fn
    assert "_pendingSettingsKeys.delete(key);" in clear_fn
    assert "_clearAcknowledgedPendingSettings(payload);" in sync_fn


def test_boot_get_converges_on_a_peer_asr_flip_it_did_not_witness_harness():
    # Issue #2540 (residual 2 of #2345): window A's boot GET merged the stale
    # server value onto independentAsrEnabled just because the key was not in
    # _dirtySettingsKeys, and A then stayed on that value. The sibling test
    # above only covers the flip arriving DURING the in-flight GET, where the
    # flip gate marks the key dirty. Pin the two interleavings it does not
    # reach -- the flip already sitting in the boot snapshot, and the flip
    # arriving after the merge already landed -- so the decision-tuple ordering
    # that makes both converge cannot silently regress into dirty-mark-only
    # gating again.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext(bootSnapshot) {
          const postCalls = [];
          const getCalls = [];
          const listeners = [];
          const store = Object.create(null);
          if (bootSnapshot) {
            store['project_neko_settings'] = JSON.stringify(bootSnapshot);
          }
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            CustomEvent: class {
              constructor(type) { this.type = type; }
            },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              const t = setTimeout(fn, ms);
              if (t && typeof t.unref === 'function') t.unref();
              return t;
            },
            clearTimeout,
            localStorage: {
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(store, key)
                  ? store[key]
                  : null;
              },
              setItem(key, value) { store[key] = value; },
              removeItem(key) { delete store[key]; },
            },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: {
              independentAsrEnabled: false,
              independentAsrActive: true,
              voiceChatActive: true,
              voiceInputLifecycleState: 'active',
              voiceSessionStartEpoch: 10,
              voiceSettingsPendingUntilEpoch: null,
              pendingVoiceRouteIndependentAsr: null,
              settingsHydrated: false,
            },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener(type, fn) { listeners.push({ type, fn }); },
            removeEventListener() {},
            dispatchEvent() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          const storage = listeners.filter((entry) => entry.type === 'storage');
          assert(storage.length === 1, 'module must register exactly one storage listener');
          return {
            postCalls,
            getCalls,
            S: sandbox.window.appState,
            fireStorage(value) {
              storage[0].fn({
                key: 'project_neko_settings',
                newValue: JSON.stringify(value),
              });
            },
            postedAsrValues() {
              return postCalls.map((call) => {
                try { return JSON.parse(call.body).independentAsrEnabled; }
                catch (_) { return undefined; }
              });
            },
          };
        }

        const tick = () => new Promise((resolve) => setImmediate(resolve));
        const NOW = Date.now();
        // The peer toggled a second ago; the server snapshot this window is
        // about to read still carries the decision from a minute ago.
        const PEER_WRITE_ID = NOW - 1000;
        const SERVER_DECISION_ID = NOW - 60000;

        function peerFlipSnapshot(writeId, value) {
          return {
            independentAsrEnabled: value,
            _sharedWriteMeta: {
              writeId,
              writerId: 'peerwindow',
              changedKeys: ['independentAsrEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              pendingRecovery: false,
              asrDecision: { writeId, writerId: 'peerwindow', value },
            },
          };
        }

        function resolveStaleGet(getCall, options) {
          const withDecision = !(options && options.withoutDecisions);
          const body = {
            success: true,
            revision: 7,
            settings: { independentAsrEnabled: false },
            telemetryBranch: null,
          };
          if (withDecision) {
            body.decisions = {
              independentAsrEnabled: {
                writeId: SERVER_DECISION_ID,
                writerId: 'serverside',
                value: false,
              },
            };
          }
          getCall.resolve({
            ok: true,
            headers: {
              get(header) {
                return header === 'ETag' ? '"conversation-settings-7"' : null;
              },
            },
            json: async () => body,
          });
        }

        async function main() {
          // Scenario 1: the peer's flip reached localStorage before this window
          // loaded, so no storage event ever announces it and the key is not
          // dirty here. The restored decision tuple must still outrank the
          // server's older one.
          const ctx = makeContext(peerFlipSnapshot(PEER_WRITE_ID, true));
          assert(ctx.S.independentAsrEnabled === true, 'boot must load the peer flip');
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          resolveStaleGet(ctx.getCalls[0]);
          await tick();
          await tick();
          await tick();
          assert(
            ctx.S.independentAsrEnabled === true,
            'the stale boot GET must not overwrite a non-dirty key the peer already decided'
          );
          assert(
            ctx.postedAsrValues().every((value) => value !== false),
            'the stale value must not be POSTed back over the peer decision'
          );

          // Scenario 1 negative: the SAME shape with a decision OLDER than the
          // server's is a genuine hydration, not a conflict -- the merge must
          // still apply the server value, or scenario 1 would pass vacuously.
          const stale = makeContext(
            peerFlipSnapshot(SERVER_DECISION_ID - 1000, true)
          );
          assert(stale.S.independentAsrEnabled === true, 'boot must load the local value');
          resolveStaleGet(stale.getCalls[0]);
          await tick();
          await tick();
          await tick();
          assert(
            stale.S.independentAsrEnabled === false,
            'a local decision older than the server tuple must still hydrate from the server'
          );

          // Scenario 2: the merge lands first and the peer's flip only arrives
          // afterwards. Adopting the server tuple must not pin this window.
          const late = makeContext(null);
          resolveStaleGet(late.getCalls[0]);
          await tick();
          await tick();
          await tick();
          assert(
            late.S.independentAsrEnabled === false,
            'the clean boot must merge the server value'
          );
          late.fireStorage(peerFlipSnapshot(NOW, true));
          assert(
            late.S.independentAsrEnabled === true,
            'a peer flip newer than the adopted server tuple must win after the merge'
          );

          // Scenario 3: a server with no decisions block at all carries no
          // ordering evidence, so it must never displace a restored local one.
          const legacyServer = makeContext(peerFlipSnapshot(PEER_WRITE_ID, true));
          resolveStaleGet(legacyServer.getCalls[0], { withoutDecisions: true });
          await tick();
          await tick();
          await tick();
          assert(
            legacyServer.S.independentAsrEnabled === true,
            'a decision-less server snapshot must not overwrite a restored local decision'
          );

          console.log('HARNESS_OK');
          process.exitCode = 0;
        }

        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exitCode = 1;
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "boot-GET convergence harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_boot_merge_orders_asr_on_the_decision_tuple_not_only_the_dirty_mark():
    # Structural half of the #2540 pin. The behavioural harness above can only
    # observe the outcome; this asserts the boot path actually keeps the two
    # inputs the issue said were conflated -- "I never changed this key" and
    # "I never had an authoritative value for it" -- separate.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    load_fn = _block_after(settings_source, "function loadSettings() {")
    # The boot snapshot restores the decision tuple, so a flip a peer wrote
    # before this window loaded is still ordered against the server's.
    assert "bootMeta.asrDecision" in load_fn
    assert "_adoptAsrDecisionTuple(" in load_fn
    assert "const bootDecision = bootMeta.asrDecision || bootMeta;" in load_fn

    # The field-level merge skips the ASR key on decision ordering, which is
    # evaluated INDEPENDENTLY of _dirtySettingsKeys.
    assert (
        "if (key === 'independentAsrEnabled' && preserveLocalAsrDecision) continue;"
        in load_fn
    )
    preserve = load_fn.split("const preserveLocalAsrDecision =", 1)[1].split(
        ";", 1
    )[0]
    assert "_lastAsrDecision" in preserve
    assert "_asrDecisionOutranks(_lastAsrDecision, serverAsrDecision)" in preserve
    # A server snapshot with no decision tuple carries no ordering evidence:
    # the local decision must win rather than the absence counting as newer.
    assert "!serverAsrDecision" in preserve
    assert "_dirtySettingsKeys" not in preserve


def test_never_settling_get_posts_only_dirty_keys_harness():
    # Behavioral pin for the round-15 fix, driving the real module with a
    # controllable fetch AND a controllable gate timer. Scenario 1: the boot GET
    # never settles, the user changes ONE unrelated preference, and the bound
    # elapses — the POST must still go out (liveness) but must carry only the
    # changed key, so the server-persisted preferences this client never read
    # survive; when the slow GET finally lands, its (intact) values hydrate the
    # untouched keys and the writeback converges. On the pre-fix code the body
    # was the full boot snapshot and independentAsrEnabled=false clobbered the
    # persisted true. Scenario 2 (negative): no dirty keys -> no POST at all.
    # Scenario 3: the normal fast-GET flow still posts the full snapshot.
    # Scenario 4: the ASR toggle flow still persists the user's choice even
    # when the bound elapses.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext() {
          const postCalls = [];
          const getCalls = [];
          const timers = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              // Fully controllable: the bound never elapses on its own, so the
              // harness decides when the timeout wins the gate race (and no
              // pending timer can hold the process open).
              timers.push({ fn, ms });
              return { unref() {} };
            },
            clearTimeout() {},
            localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: { independentAsrEnabled: false, settingsHydrated: false },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          return {
            postCalls,
            getCalls,
            S: sandbox.window.appState,
            win: sandbox.window,
            mod: sandbox.window.appSettings,
            fireGateTimeout() {
              assert(timers.length === 1, 'exactly one bounded gate timer must be armed');
              assert(timers[0].ms === 3000, 'the gate bound must stay the 3s constant');
              timers.shift().fn();
            },
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // Scenario 1: slow (never-settling) GET + one unrelated user change.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');

          ctx.win.mergeMessagesEnabled = true; // the settings-popup mirror
          ctx.mod.saveSettings();              // full user path -> userInitiated POST
          assert(ctx.S.settingsHydrated === true, 'a user change still hydrates synchronously');
          await tick();
          assert(ctx.postCalls.length === 0, 'the POST waits for the gate while the GET is pending');

          ctx.fireGateTimeout();
          await tick();
          await tick();
          assert(ctx.postCalls.length === 1, 'the bound must still release the POST (liveness)');
          const body1 = JSON.parse(ctx.postCalls[0].body);
          assert(body1.mergeMessagesEnabled === true, 'the dirty key must be persisted');
          assert(
            Object.keys(body1).length === 1,
            'a timed-out gate must post ONLY the dirty keys, got: ' + JSON.stringify(body1)
          );
          assert(
            !('independentAsrEnabled' in body1),
            'the untouched ASR preference must not be overwritten by this boot default'
          );
          assert(
            !('proactiveChatEnabled' in body1),
            'no untouched preference may ride along in the timed-out body'
          );

          // The slow GET now lands. Because the partial POST left them alone,
          // the server values for untouched keys are still the persisted ones.
          ctx.postCalls[0].resolve(okPost);
          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: true, mergeMessagesEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx.S.independentAsrEnabled === true, 'the untouched key hydrates from the surviving server value');
          assert(ctx.S.mergeMessagesEnabled === true, 'the dirty key survives the late merge');
          assert(ctx.postCalls.length === 2, 'the merge writeback POST follows');
          const body2 = JSON.parse(ctx.postCalls[1].body);
          assert(
            Object.keys(body2).length > 1,
            'once the GET settled, full snapshots resume and converge the server'
          );
          assert(body2.independentAsrEnabled === true, 'the writeback carries the server ASR value');
          assert(body2.mergeMessagesEnabled === true, 'the writeback carries the user change');
          ctx.postCalls[1].resolve(okPost);
          await tick();

          // Scenario 2 (negative): nothing dirty while the GET is unsettled —
          // a periodic-style sync must not write pre-merge values at all, and
          // its promise must still resolve (never-rejecting sync contract).
          const ctx2 = makeContext();
          const pp = ctx2.mod.syncSettingsToServer();
          await tick();
          ctx2.fireGateTimeout();
          await tick();
          await tick();
          assert(ctx2.postCalls.length === 0, 'no dirty key means no POST while the GET is unsettled');
          await pp;

          // Scenario 3: the normal fast-GET flow is unchanged — the merge
          // settles before the bound, so the user POST carries the FULL
          // snapshot (server truth for untouched keys included).
          const ctx3 = makeContext();
          ctx3.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: true },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx3.postCalls.length === 1, 'the merge writeback POST goes out first');
          ctx3.postCalls[0].resolve(okPost);
          await tick();
          await tick();
          ctx3.win.mergeMessagesEnabled = true;
          ctx3.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx3.postCalls.length === 2, 'the fast-GET user POST goes out');
          const body3 = JSON.parse(ctx3.postCalls[1].body);
          assert(Object.keys(body3).length > 1, 'a settled GET keeps posting the full snapshot');
          assert(body3.independentAsrEnabled === true, 'the full snapshot carries the merged server value');
          assert(body3.mergeMessagesEnabled === true, 'the full snapshot carries the user change');

          // Scenario 4: the ASR toggle flow still persists the user's choice
          // when the bound elapses (the toggled key is dirty).
          const ctx4 = makeContext();
          ctx4.S.independentAsrEnabled = true;
          ctx4.mod.saveSettings({ skipServerSync: true });
          const p4 = ctx4.mod.syncSettingsToServer({ userInitiated: true });
          assert(ctx4.S.settingsHydrated === true, 'the toggle hydrates synchronously at call time');
          await tick();
          assert(ctx4.postCalls.length === 0, 'the toggle POST is gated behind the pending GET');
          ctx4.fireGateTimeout();
          await tick();
          await tick();
          assert(ctx4.postCalls.length === 1, 'the toggle POST goes out on the bound');
          const body4 = JSON.parse(ctx4.postCalls[0].body);
          assert(body4.independentAsrEnabled === true, 'the toggle POST carries the user choice');
          assert(
            Object.keys(body4).length === 1,
            'the toggle POST carries nothing else, got: ' + JSON.stringify(body4)
          );
          ctx4.postCalls[0].resolve(okPost);
          await p4;

          console.log('HARNESS_OK');
          // No live timers remain (the harness owns setTimeout), so the process
          // exits naturally once main() returns and piped stdout is flushed.
          process.exitCode = 0;
        }

        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exitCode = 1;
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "never-settling-GET dirty-only harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
