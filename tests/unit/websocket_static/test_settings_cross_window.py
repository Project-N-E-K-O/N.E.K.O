import json
import textwrap
from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract


def test_cross_window_asr_flip_authoritative_over_pending_get_harness():
    # Behavioral pin for the cross-window Codex P2 fix: drive the real module
    # in a vm sandbox, deliver a 'storage' event carrying another window's
    # independent-ASR flip while this window's boot GET is still pending, then
    # resolve that GET with the stale pre-flip server value. The flip must mark
    # hydration (arming the start_session handshake stamp), the field-level
    # merge must preserve the flipped key (marked dirty by the flip gate), and
    # the receiving window must never POST. Negative: a storage event that
    # does NOT flip the toggle stays non-authoritative and the pending GET
    # still merges normally.
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
          const listeners = [];
          const dispatchedEvents = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            CustomEvent: class {
              constructor(type) { this.type = type; }
            },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              // Unref'd so a pending gate timer cannot hold the process open;
              // the harness then exits naturally and stdout always flushes.
              const t = setTimeout(fn, ms);
              if (t && typeof t.unref === 'function') t.unref();
              return t;
            },
            clearTimeout,
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
            dispatchEvent(event) { dispatchedEvents.push(event.type); },
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          const storage = listeners.filter((entry) => entry.type === 'storage');
          assert(storage.length === 1, 'module must register exactly one storage listener');
          return {
            postCalls,
            getCalls,
            dispatchedEvents,
            S: sandbox.window.appState,
            fireStorage(newValue) {
              storage[0].fn({ key: 'project_neko_settings', newValue });
            },
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // Scenario 1: cross-window ASR flip while the boot GET is pending.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          assert(ctx.S.settingsHydrated === false, 'boot alone must not mark hydration');

          ctx.fireStorage(JSON.stringify({ independentAsrEnabled: true }));
          assert(ctx.S.independentAsrEnabled === true, 'the flip must be applied to S');
          assert(ctx.S.settingsHydrated === true, 'the flip must arm the start_session handshake stamp');
          assert(ctx.S.voiceSettingsPendingUntilEpoch === 11, 'the flip must target the next voice-session epoch');
          assert(ctx.S.pendingVoiceRouteIndependentAsr === true, 'the pending summary must preserve the active route');
          assert(
            ctx.dispatchedEvents.includes('neko:voice-settings-pending-changed'),
            'the flip must notify an already-open microphone popover'
          );
          assert(ctx.postCalls.length === 0, 'the receiving window must not POST (originating window owns persistence)');

          // The GET now resolves with the server value read BEFORE the other
          // window's POST landed: the flipped key is dirty, so the field-level
          // merge must preserve it.
          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: true, settings: { independentAsrEnabled: false }, telemetryBranch: null }),
          });
          await tick();
          await tick();
          assert(ctx.S.independentAsrEnabled === true, 'the stale GET merge must not overwrite the cross-window flip');
          assert(ctx.postCalls.length === 0, 'the dropped merge must not POST the stale value back');

          // Scenario 2 (negative): a storage event without an ASR flip is
          // non-authoritative — other shared keys still sync, hydration stays
          // unmarked, and the pending GET then merges exactly as before.
          const ctx2 = makeContext();
          ctx2.fireStorage(JSON.stringify({ independentAsrEnabled: false, mergeMessagesEnabled: true }));
          assert(ctx2.S.mergeMessagesEnabled === true, 'other shared keys must still sync across windows');
          assert(ctx2.S.settingsHydrated === false, 'no ASR flip means no hydration mark');
          assert(ctx2.S.voiceSettingsPendingUntilEpoch === null, 'no flip means no pending voice-session marker');
          assert(ctx2.dispatchedEvents.length === 0, 'no flip means no popover notification');
          assert(ctx2.postCalls.length === 0, 'a non-flip storage event must not POST either');

          ctx2.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: true, settings: { independentAsrEnabled: true }, telemetryBranch: null }),
          });
          await tick();
          await tick();
          assert(ctx2.S.independentAsrEnabled === true, 'the normal server merge must still apply');
          assert(ctx2.S.settingsHydrated === true, 'the normal server merge must still mark hydration');
          assert(ctx2.postCalls.length === 1, 'the same-window merge write-back POST must be unchanged');
          ctx2.postCalls[0].resolve(okPost);

          console.log('HARNESS_OK');
          // Timers in the sandbox are unref'd, so the process exits naturally
          // once main() returns and piped stdout is fully flushed.
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
        "cross-window ASR flip harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_unrelated_change_during_pending_get_preserves_server_asr_harness():
    # Behavioral pin for the field-level authority fix (Codex P2): with the
    # old whole-merge-drop, changing ANY unrelated preference while the boot
    # settings GET was pending made the full saveSettings() POST authoritative
    # — the entire server merge was discarded and the POST (built from local
    # state including the boot-default independentAsrEnabled=false) overwrote
    # the persisted ASR choice. Drive the real module: the user POST must be
    # gated until the GET settles, the merge must hydrate the untouched ASR
    # key from the server while preserving the user's dirty key, and every
    # POST body must then carry the server's ASR value. On the pre-fix code
    # these assertions fail (the POST fires immediately with ASR=false and the
    # merge is dropped wholesale). Second scenario: the ASR-toggle-while-
    # pending flow is unchanged — the toggled key stays authoritative over the
    # stale merge and its POST carries the user's choice.
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
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              // Unref'd so a pending gate timer cannot hold the process open;
              // the harness then exits naturally and stdout always flushes.
              const t = setTimeout(fn, ms);
              if (t && typeof t.unref === 'function') t.unref();
              return t;
            },
            clearTimeout,
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
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // Scenario 1: unrelated preference changed while the boot GET is
          // pending; the server holds independentAsrEnabled=true, this boot
          // only has the default false.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');

          ctx.win.mergeMessagesEnabled = true; // the settings-popup mirror
          ctx.mod.saveSettings();              // full user path -> userInitiated POST
          assert(ctx.S.settingsHydrated === true, 'a user change still hydrates synchronously');
          await tick();
          assert(ctx.postCalls.length === 0, 'the user POST must wait (bounded) for the pending GET, not fire with boot defaults');

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

          assert(ctx.S.independentAsrEnabled === true, 'the untouched ASR key must hydrate from the server, not stay a boot default');
          assert(ctx.S.mergeMessagesEnabled === true, 'the user-changed (dirty) key must survive the merge');
          assert(ctx.postCalls.length === 1, 'the gated user POST goes out once the GET settled');
          const body1 = JSON.parse(ctx.postCalls[0].body);
          assert(body1.independentAsrEnabled === true, 'the send-time snapshot must carry the SERVER ASR value, not the boot default');
          assert(body1.mergeMessagesEnabled === true, 'the send-time snapshot must carry the user change');

          // The merge writeback POST (queued behind the user POST) carries the
          // same merged state, converging the server.
          ctx.postCalls[0].resolve(okPost);
          await tick();
          await tick();
          assert(ctx.postCalls.length === 2, 'the merge writeback POST follows the user POST');
          const body2 = JSON.parse(ctx.postCalls[1].body);
          assert(body2.independentAsrEnabled === true, 'the writeback keeps the server ASR value');
          assert(body2.mergeMessagesEnabled === true, 'the writeback keeps the user change');
          ctx.postCalls[1].resolve(okPost);
          await tick();

          // Scenario 2: the ASR-toggle-while-GET-pending flow is unchanged —
          // the toggled key is dirty, so the stale merge cannot revert it and
          // its POST carries the user's choice.
          const ctx2 = makeContext();
          ctx2.S.independentAsrEnabled = true;
          ctx2.mod.saveSettings({ skipServerSync: true });
          const p = ctx2.mod.syncSettingsToServer({ userInitiated: true });
          assert(ctx2.S.settingsHydrated === true, 'the toggle must hydrate synchronously at call time');
          await tick();
          assert(ctx2.postCalls.length === 0, 'the toggle POST is gated behind the pending GET too');

          ctx2.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx2.S.independentAsrEnabled === true, 'the stale merge must not revert the user toggle');
          assert(ctx2.postCalls.length === 1, 'a merge that only skipped the dirty key must not add a writeback POST');
          assert(JSON.parse(ctx2.postCalls[0].body).independentAsrEnabled === true, 'the toggle POST carries the user choice');
          ctx2.postCalls[0].resolve(okPost);
          await p;

          console.log('HARNESS_OK');
          // Timers in the sandbox are unref'd, so the process exits naturally
          // once main() returns and piped stdout is fully flushed.
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
        "unrelated-change-during-pending-GET harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_unsynced_optimization_decision_survives_reload_until_posted_harness():
    """A persisted explicit choice stays authoritative until its POST succeeds."""
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');
        const optimizationKey = 'voiceInputResourceOptimizationEnabled';

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext(initialSnapshot) {
          let stored = initialSnapshot ? JSON.stringify(initialSnapshot) : null;
          const postCalls = [];
          const getCalls = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
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
                return key === 'project_neko_settings' ? stored : null;
              },
              setItem(key, value) {
                if (key === 'project_neko_settings') stored = value;
              },
              removeItem() {},
            },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ body: opts.body, resolve, reject });
                } else {
                  getCalls.push({ resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: {
              independentAsrEnabled: true,
              settingsHydrated: false,
              independentAsrAuthoritative: false,
              voiceInputResourceOptimizationEnabled: true,
              voiceInputResourceOptimizationAuthoritative: false,
            },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          return {
            getCalls,
            postCalls,
            S: sandbox.window.appState,
            mod: sandbox.window.appSettings,
            snapshot() { return JSON.parse(stored); },
          };
        }

        const tick = () => new Promise((resolve) => setImmediate(resolve));
        const okPost = { ok: true, json: async () => ({ success: true }) };

        async function main() {
          // This is a snapshot written by the previous PR head: it records the
          // explicit decision but predates the durable pending-sync marker.
          const legacyPendingSnapshot = {
            [optimizationKey]: false,
            _sharedWriteMeta: {
              writeId: 41,
              writerId: 'window-a',
              changedKeys: [optimizationKey],
              hydrated: true,
              asrAuthoritative: false,
              optimizationDecision: {
                writeId: 41,
                writerId: 'window-a',
                value: false,
              },
            },
          };

          // If the boot GET also fails, pre-merge sync must still retry the
          // durable decision. `_pickDirtySettings()` reads only the pending
          // set, so restoring just dirty membership would produce no POST.
          const offline = makeContext(legacyPendingSnapshot);
          offline.getCalls[0].resolve({ ok: false });
          await tick();
          await tick();
          const offlineSync = offline.mod.syncSettingsToServer();
          await tick();
          assert(
            offline.postCalls.length === 1,
            'failed boot GET must not forget the pending optimization POST'
          );
          assert(
            JSON.parse(offline.postCalls[0].body)[optimizationKey] === false,
            'dirty-only retry must carry the durable optimization choice'
          );
          offline.postCalls[0].resolve(okPost);
          await offlineSync;

          const ctx = makeContext(legacyPendingSnapshot);
          assert(ctx.S[optimizationKey] === false, 'boot must load the local choice');
          assert(ctx.S.settingsHydrated === true, 'pending choice must hydrate the handshake');
          assert(
            ctx.S.voiceInputResourceOptimizationAuthoritative === true,
            'pending choice must be authoritative for the next start handshake'
          );

          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { [optimizationKey]: true },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            ctx.S[optimizationKey] === false,
            'stale server GET must not overwrite the unsynced local choice'
          );

          const sync = ctx.mod.syncSettingsToServer();
          await tick();
          assert(ctx.postCalls.length === 1, 'pending choice must be POSTed after reload');
          assert(
            JSON.parse(ctx.postCalls[0].body)[optimizationKey] === false,
            'POST must carry the pending local choice'
          );
          ctx.postCalls[0].resolve(okPost);
          await sync;
          const syncedSnapshot = ctx.snapshot();
          assert(
            syncedSnapshot._sharedWriteMeta.optimizationDecisionPendingSync === false,
            'successful POST must durably clear the pending marker'
          );

          // Once synchronization is durable, a later reload may accept newer
          // server truth instead of pinning the old local choice forever.
          const reloaded = makeContext(syncedSnapshot);
          reloaded.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { [optimizationKey]: true },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            reloaded.S[optimizationKey] === true,
            'synced decision must no longer block server truth on a later reload'
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
        "unsynced-optimization-reload harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
