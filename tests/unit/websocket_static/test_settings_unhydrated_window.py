import json
import textwrap
from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.integration_serial


def test_unrelated_save_from_unhydrated_window_is_not_an_asr_toggle_harness():
    # Behavioral pin for the Codex P2 follow-up, driven end-to-end across two
    # real module instances: the WRITER's actual localStorage payload is fed to
    # the RECEIVER's storage listener, so the metadata contract is exercised,
    # not mocked.
    #
    # Scenario 1 (the bug): an unhydrated window saves one unrelated preference.
    # Its snapshot carries the boot-default independentAsrEnabled, which the
    # receiving window had already merged as `true` from the server. On the
    # pre-fix code the value difference alone read as an explicit toggle: the
    # receiver adopted `false`. Now the writer's metadata says only the
    # unrelated key changed, so the ASR value is ignored.
    # Scenario 2 (negative, dirty marking): same stale snapshot delivered to a
    # window whose boot GET is still unsettled — the ASR key must NOT enter the
    # dirty set, observable because unsettled POST bodies carry dirty keys only.
    # Scenario 3: a genuine cross-window toggle stays authoritative (hydration
    # marked, key dirtied so a stale merge cannot revert it).
    # Scenario 4: an already-superseded (older write id) snapshot is ignored.
    # Scenario 5: a metadata-less legacy payload keeps today's behaviour.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext(initialSettings = null) {
          const postCalls = [];
          const getCalls = [];
          const listeners = [];
          const timers = [];
          const writes = [];
          let savedShared = initialSettings ? JSON.stringify(initialSettings) : null;
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              // Fully controllable: no pending timer can hold the process open.
              timers.push({ fn, ms });
              return { unref() {} };
            },
            clearTimeout() {},
            localStorage: {
              getItem(key) {
                return key === 'project_neko_settings' ? savedShared : null;
              },
              setItem(key, value) {
                if (key === 'project_neko_settings') savedShared = value;
                writes.push({ key, value });
              },
              removeItem() {},
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
            appState: { independentAsrEnabled: false, settingsHydrated: false },
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
            win: sandbox.window,
            mod: sandbox.window.appSettings,
            sharedWrites() {
              return writes.filter((w) => w.key === 'project_neko_settings');
            },
            lastSharedWrite() {
              const shared = writes.filter((w) => w.key === 'project_neko_settings');
              assert(shared.length > 0, 'the module must persist the shared settings snapshot');
              return shared[shared.length - 1].value;
            },
            fireStorage(newValue) {
              storage[0].fn({ key: 'project_neko_settings', newValue });
            },
            fireGateTimeout() {
              assert(timers.length >= 1, 'a bounded gate timer must be armed');
              timers.shift().fn();
            },
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function hydrateFromServer(ctx, settings, decisions = null) {
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings,
              decisions: decisions || {},
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx.S.settingsHydrated === true, 'a successful GET must hydrate the window');
          while (ctx.postCalls.length) {
            ctx.postCalls.shift().resolve(okPost);
            await tick();
          }
        }

        async function main() {
          // ---- Scenario 1: unrelated save from an UNHYDRATED window ----
          const receiver = makeContext();
          await hydrateFromServer(receiver, {
            independentAsrEnabled: true,
            voiceInputResourceOptimizationEnabled: false,
          });
          assert(receiver.S.independentAsrEnabled === true, 'receiver merged the server ASR value');
          assert(
            receiver.S.voiceInputResourceOptimizationEnabled === false,
            'receiver merged the server optimization value'
          );

          const writer = makeContext();      // boot GET left pending -> unhydrated
          writer.win.mergeMessagesEnabled = true;
          writer.mod.saveSettings();         // an UNRELATED preference
          const stalePayload = writer.lastSharedWrite();
          const staleParsed = JSON.parse(stalePayload);
          assert(
            staleParsed.independentAsrEnabled === false,
            'saveSettings still copies the ASR key into every snapshot (that is the trap)'
          );
          assert(
            staleParsed.voiceInputResourceOptimizationEnabled === true,
            'saveSettings still copies the optimization key into every snapshot (that is the trap)'
          );
          const receiverPostsBefore = receiver.postCalls.length;
          receiver.fireStorage(stalePayload);
          assert(
            receiver.S.independentAsrEnabled === true,
            'the hydrated ASR value must survive an unrelated save from an unhydrated window'
          );
          assert(
            receiver.S.voiceInputResourceOptimizationEnabled === false,
            'the hydrated optimization value must survive an unrelated save from an unhydrated window'
          );
          assert(
            receiver.S.mergeMessagesEnabled === true,
            'every other shared key must still sync across windows'
          );
          assert(
            receiver.postCalls.length === receiverPostsBefore,
            'the receiving window must never POST from the storage listener'
          );

          // The metadata that made the decision possible.
          const staleMeta = staleParsed._sharedWriteMeta;
          assert(staleMeta && typeof staleMeta.writeId === 'number', 'the write must carry metadata');
          assert(
            staleMeta.changedKeys.indexOf('mergeMessagesEnabled') !== -1,
            'the explicitly changed key must be declared'
          );
          assert(
            staleMeta.changedKeys.indexOf('independentAsrEnabled') === -1,
            'an unrelated save must NOT declare the ASR key as user-changed'
          );
          assert(
            staleMeta.changedKeys.indexOf('voiceInputResourceOptimizationEnabled') === -1,
            'an unrelated save must NOT declare the optimization key as user-changed'
          );
          assert(staleMeta.hydrated === false, 'the writer had not merged the server settings yet');

          // ---- Scenario 2 (negative): the ASR key must not be dirtied ----
          // Observability: while the boot GET is unsettled the POST body is
          // restricted to the user-dirty keys, so a wrongly dirtied ASR key
          // would show up there.
          const pending = makeContext();     // boot GET stays pending
          pending.win.mergeMessagesEnabled = true;
          pending.mod.saveSettings();        // hydrates this window, dirties ONE key
          assert(pending.S.settingsHydrated === true, 'a user change hydrates synchronously');
          pending.fireGateTimeout();
          await tick();
          await tick();
          assert(pending.postCalls.length === 1, 'the bounded gate must release the POST');
          const dirtyBody1 = JSON.parse(pending.postCalls[0].body);
          assert(
            !('independentAsrEnabled' in dirtyBody1),
            'baseline: the untouched ASR key is not dirty yet'
          );
          pending.postCalls[0].resolve(okPost);
          await tick();
          // This window already holds the authoritative ASR value; its own GET
          // has not landed, so set the state the listener reads directly.
          pending.S.independentAsrEnabled = true;

          pending.fireStorage(stalePayload);
          assert(
            pending.S.independentAsrEnabled === true,
            'the stale snapshot must not overwrite the authoritative value here either'
          );
          const pp = pending.mod.syncSettingsToServer();  // periodic-style: no dirty marking
          await tick();
          await tick();
          assert(
            pending.postCalls.length === 1,
            'the acknowledged local key and incidental ASR copy leave no pending POST'
          );
          await pp;

          // ---- Scenario 3: a genuine cross-window toggle stays authoritative ----
          const toggler = makeContext();
          await hydrateFromServer(toggler, { independentAsrEnabled: false });
          // Mirror app-audio-capture.js: local persist first, then the POST.
          toggler.S.independentAsrEnabled = true;
          toggler.mod.saveSettings({ skipServerSync: true });
          const togglePayload = toggler.lastSharedWrite();
          const toggleMeta = JSON.parse(togglePayload)._sharedWriteMeta;
          assert(
            toggleMeta.changedKeys.indexOf('independentAsrEnabled') !== -1,
            'a real toggle must declare the ASR key as explicitly changed'
          );

          const receiver2 = makeContext();   // boot GET still pending
          receiver2.fireStorage(togglePayload);
          assert(receiver2.S.independentAsrEnabled === true, 'a real toggle must be applied');
          assert(
            receiver2.S.settingsHydrated === true,
            'a real toggle must arm the start_session handshake stamp'
          );
          assert(receiver2.postCalls.length === 0, 'still no POST from the receiving window');
          // The key must be dirty: the stale server merge cannot revert it.
          receiver2.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            receiver2.S.independentAsrEnabled === true,
            'the flipped key must be dirty so the stale merge preserves it'
          );

          // ---- Scenario 4: a superseded (older) write is ignored ----
          const replay = JSON.parse(togglePayload);
          replay.independentAsrEnabled = false;
          replay._sharedWriteMeta = {
            writeId: toggleMeta.writeId - 1,
            changedKeys: ['independentAsrEnabled'],
            hydrated: true,
          };
          receiver2.fireStorage(JSON.stringify(replay));
          assert(
            receiver2.S.independentAsrEnabled === true,
            'an already-superseded write must not re-flip the route'
          );

          // ---- Scenario 5: metadata-less legacy payload keeps today's behaviour ----
          const legacy = makeContext();
          legacy.fireStorage(JSON.stringify({ independentAsrEnabled: true }));
          assert(legacy.S.independentAsrEnabled === true, 'legacy payloads still apply the value');
          assert(
            legacy.S.settingsHydrated === true,
            'legacy payloads keep the value-difference authority fallback'
          );
          assert(legacy.postCalls.length === 0, 'legacy fallback still never POSTs from the listener');

          // ---- Scenario 6: an authoritative server-merge broadcast updates
          // the decision tuple even though changedKeys is intentionally empty.
          const sibling = makeContext();
          await hydrateFromServer(sibling, { independentAsrEnabled: false });
          sibling.S.independentAsrEnabled = true;
          sibling.mod.saveSettings({ skipServerSync: true });
          const localToggleMeta = JSON.parse(sibling.lastSharedWrite())._sharedWriteMeta;
          const serverDecision = {
            writeId: localToggleMeta.asrDecision.writeId + 10,
            writerId: 'server-legacy',
            value: false,
          };
          sibling.fireStorage(JSON.stringify({
            independentAsrEnabled: false,
            _sharedWriteMeta: {
              writeId: serverDecision.writeId,
              writerId: 'server-window',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
              asrDecision: serverDecision,
            },
          }));
          assert(
            sibling.S.independentAsrEnabled === false,
            'the authoritative server broadcast must replace the old local toggle'
          );
          sibling.win.mergeMessagesEnabled = true;
          sibling.mod.saveSettings({ skipServerSync: true });
          const resharedMeta = JSON.parse(sibling.lastSharedWrite())._sharedWriteMeta;
          assert(
            JSON.stringify(resharedMeta.asrDecision) === JSON.stringify(serverDecision),
            'an unrelated save must retain the accepted server decision tuple'
          );

          // ---- Scenario 7: a persisted server-merge tuple survives reload.
          // changedKeys is intentionally empty because this is authority from
          // the server, not a new browser user action.
          const bootDecision = {
            writeId: Date.now() + 1000,
            writerId: 'server-ahead',
            value: false,
          };
          const reloaded = makeContext({
            independentAsrEnabled: false,
            _sharedWriteMeta: {
              writeId: bootDecision.writeId - 1,
              writerId: 'merging-window',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
              asrDecision: bootDecision,
            },
          });
          reloaded.win.mergeMessagesEnabled = true;
          reloaded.mod.saveSettings({ skipServerSync: true });
          const afterReloadMeta = JSON.parse(reloaded.lastSharedWrite())._sharedWriteMeta;
          assert(
            JSON.stringify(afterReloadMeta.asrDecision) === JSON.stringify(bootDecision),
            'reload must restore and retain a matching server-merge ASR tuple'
          );
          reloaded.S.independentAsrEnabled = true;
          reloaded.mod.saveSettings({ skipServerSync: true });
          const nextLocalDecision = JSON.parse(
            reloaded.lastSharedWrite()
          )._sharedWriteMeta.asrDecision;
          assert(
            nextLocalDecision.writeId > bootDecision.writeId,
            'the first local toggle after reload must supersede the persisted server tuple'
          );

          // ---- Scenario 8: pending recovery writes terminate instead of
          // bouncing between two windows with different pending keys.
          const pendingA = makeContext();
          pendingA.win.focusModeEnabled = true;
          pendingA.mod.saveSettings();
          const pendingAPayload = pendingA.lastSharedWrite();

          const pendingB = makeContext({
            independentAsrEnabled: false,
            focusModeEnabled: false,
          });
          pendingB.win.mergeMessagesEnabled = true;
          pendingB.mod.saveSettings();
          const pendingBPayload = pendingB.lastSharedWrite();

          pendingA.fireStorage(pendingBPayload);
          const recoveryPayload = pendingA.lastSharedWrite();
          const recoveryMeta = JSON.parse(recoveryPayload)._sharedWriteMeta;
          assert(
            recoveryMeta.pendingRecovery === true,
            'the reasserted full snapshot must be marked as pending recovery'
          );
          const writesBeforeRecovery = pendingB.sharedWrites().length;
          pendingB.fireStorage(recoveryPayload);
          assert(
            pendingB.sharedWrites().length === writesBeforeRecovery,
            'a pending window must not answer a recovery with another recovery write'
          );
          assert(
            pendingB.S.mergeMessagesEnabled === true,
            'the receiving window must still preserve its pending runtime value'
          );
          assert(
            JSON.parse(pendingAPayload)._sharedWriteMeta.pendingRecovery !== true,
            'ordinary user writes must not be marked as recovery'
          );

          // ---- Scenario 9: a merge envelope minted later does not make a
          // stale field newer than an explicit edit the sender never observed.
          const editor = makeContext({
            independentAsrEnabled: false,
            focusModeEnabled: false,
          });
          editor.win.focusModeEnabled = true;
          editor.mod.saveSettings({ skipServerSync: true });
          const explicitPayload = editor.lastSharedWrite();
          const explicitMeta = JSON.parse(explicitPayload)._sharedWriteMeta;

          const observer = makeContext({
            independentAsrEnabled: false,
            focusModeEnabled: false,
          });
          observer.fireStorage(explicitPayload);
          assert(observer.S.focusModeEnabled === true, 'observer accepts the explicit edit');

          const staleMerger = makeContext({
            independentAsrEnabled: false,
            focusModeEnabled: false,
          });
          staleMerger.mod.saveSettings({
            skipServerSync: true,
            serverMerged: true,
          });
          const staleMerge = JSON.parse(staleMerger.lastSharedWrite());
          staleMerge._sharedWriteMeta.writeId = explicitMeta.writeId + 100;
          assert(
            Object.keys(staleMerge._sharedWriteMeta.knownKeyWrites).length === 0,
            'the stale sender must declare that it never observed the edit'
          );
          observer.fireStorage(JSON.stringify(staleMerge));
          assert(
            observer.S.focusModeEnabled === true,
            'a high envelope id must not roll back a newer per-key edit'
          );

          const mergeAfterEdit = makeContext({
            independentAsrEnabled: false,
            focusModeEnabled: false,
          });
          mergeAfterEdit.fireStorage(explicitPayload);
          mergeAfterEdit.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: false, focusModeEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            mergeAfterEdit.S.focusModeEnabled === true,
            'a boot GET started before the cross-window edit must preserve that edit'
          );

          // ---- Scenario 10: a late recovery envelope cannot roll back a
          // newer explicit decision for the same key.
          const newerEditor = makeContext(JSON.parse(explicitPayload));
          newerEditor.win.focusModeEnabled = false;
          newerEditor.mod.saveSettings({ skipServerSync: true });
          const newerPayload = newerEditor.lastSharedWrite();
          const newerMeta = JSON.parse(newerPayload)._sharedWriteMeta;
          observer.fireStorage(newerPayload);
          assert(observer.S.focusModeEnabled === false, 'observer accepts the newer edit');

          const lateRecovery = JSON.parse(explicitPayload);
          lateRecovery._sharedWriteMeta = {
            ...lateRecovery._sharedWriteMeta,
            writeId: newerMeta.writeId + 100,
            changedKeys: [],
            pendingRecovery: true,
          };
          observer.fireStorage(JSON.stringify(lateRecovery));
          assert(
            observer.S.focusModeEnabled === false,
            'a fresh recovery envelope must not outrank its older per-key provenance'
          );

          // ---- Scenario 11: a genuine optimization toggle still propagates ----
          const optimizationWriter = makeContext();
          await hydrateFromServer(optimizationWriter, {
            independentAsrEnabled: false,
            voiceInputResourceOptimizationEnabled: true,
          });
          optimizationWriter.S.voiceInputResourceOptimizationEnabled = false;
          optimizationWriter.mod.saveSettings({ skipServerSync: true });
          const optimizationPayload = optimizationWriter.lastSharedWrite();
          const optimizationMeta = JSON.parse(optimizationPayload)._sharedWriteMeta;
          assert(
            optimizationMeta.changedKeys.indexOf(
              'voiceInputResourceOptimizationEnabled'
            ) !== -1,
            'a real optimization toggle must be declared explicitly'
          );

          const optimizationReceiver = makeContext();
          await hydrateFromServer(optimizationReceiver, {
            independentAsrEnabled: false,
            voiceInputResourceOptimizationEnabled: true,
          });
          optimizationReceiver.fireStorage(optimizationPayload);
          assert(
            optimizationReceiver.S.voiceInputResourceOptimizationEnabled === false,
            'a real optimization toggle must apply across windows'
          );

          // ---- Scenario 7: concurrent optimization toggles converge ----
          // Each window writes before observing the other. Freshness against
          // received writes cannot order either window's own pending choice;
          // the per-key decision tuple must select the same winner on both.
          const optimizationA = makeContext();
          await hydrateFromServer(optimizationA, {
            independentAsrEnabled: false,
            voiceInputResourceOptimizationEnabled: true,
          });
          optimizationA.S.voiceInputResourceOptimizationEnabled = false;
          optimizationA.mod.saveSettings({ skipServerSync: true });
          const optimizationPayloadA = optimizationA.lastSharedWrite();

          const optimizationB = makeContext();
          await hydrateFromServer(optimizationB, {
            independentAsrEnabled: false,
            voiceInputResourceOptimizationEnabled: false,
          });
          optimizationB.S.voiceInputResourceOptimizationEnabled = true;
          optimizationB.mod.saveSettings({ skipServerSync: true });
          const optimizationPayloadB = optimizationB.lastSharedWrite();

          const parsedA = JSON.parse(optimizationPayloadA);
          const parsedB = JSON.parse(optimizationPayloadB);
          const decisionA = parsedA._sharedWriteMeta.optimizationDecision;
          const decisionB = parsedB._sharedWriteMeta.optimizationDecision;
          assert(decisionA && decisionB, 'each explicit optimization write must carry its decision tuple');
          const aWins = decisionA.writeId > decisionB.writeId
            || (
              decisionA.writeId === decisionB.writeId
              && decisionA.writerId > decisionB.writerId
            );
          const winningValue = aWins
            ? parsedA.voiceInputResourceOptimizationEnabled
            : parsedB.voiceInputResourceOptimizationEnabled;

          optimizationA.fireStorage(optimizationPayloadB);
          optimizationB.fireStorage(optimizationPayloadA);
          assert(
            optimizationA.S.voiceInputResourceOptimizationEnabled === winningValue,
            'window A must converge on the winning optimization choice'
          );
          assert(
            optimizationB.S.voiceInputResourceOptimizationEnabled === winningValue,
            'window B must converge on the winning optimization choice'
          );

          // ---- Scenario 8: a real return to the restored value is fresh ----
          const restoredDecision = {
            writeId: 1,
            writerId: 'restored-writer',
            value: false,
          };
          const rebound = makeContext({
            independentAsrEnabled: false,
            voiceInputResourceOptimizationEnabled: false,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'restored-writer',
              changedKeys: [
                'independentAsrEnabled',
                'voiceInputResourceOptimizationEnabled',
              ],
              asrDecision: restoredDecision,
              optimizationDecision: restoredDecision,
              optimizationDecisionPendingSync: false,
            },
          });
          await hydrateFromServer(rebound, {
            independentAsrEnabled: true,
            voiceInputResourceOptimizationEnabled: true,
          }, {
            independentAsrEnabled: {
              writeId: 2,
              writerId: 'server-winner',
              value: true,
            },
          });
          assert(
            rebound.S.independentAsrEnabled === true
              && rebound.S.voiceInputResourceOptimizationEnabled === true,
            'server decisions must apply before testing a return to the restored value'
          );
          rebound.S.independentAsrEnabled = false;
          rebound.S.voiceInputResourceOptimizationEnabled = false;
          rebound.mod.saveSettings({ skipServerSync: true });
          const reboundMeta = JSON.parse(
            rebound.lastSharedWrite()
          )._sharedWriteMeta;
          assert(
            reboundMeta.asrDecision.writeId === reboundMeta.writeId
              && reboundMeta.asrDecision.writerId === reboundMeta.writerId,
            'returning to a restored ASR value is a fresh user decision'
          );
          assert(
            reboundMeta.optimizationDecision.writeId === reboundMeta.writeId
              && reboundMeta.optimizationDecision.writerId === reboundMeta.writerId,
            'returning to a restored optimization value is a fresh user decision'
          );

          // ---- Scenario 9: an invalid optimization decision id cannot poison
          // later local choices by permanently outranking the browser clock.
          const poisonedOptimization = makeContext({
            voiceInputResourceOptimizationEnabled: false,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'poisoned-writer',
              changedKeys: ['voiceInputResourceOptimizationEnabled'],
              optimizationDecision: {
                writeId: Number.MAX_SAFE_INTEGER,
                writerId: 'poisoned-writer',
                value: false,
              },
              optimizationDecisionPendingSync: false,
            },
          });
          poisonedOptimization.S.voiceInputResourceOptimizationEnabled = true;
          poisonedOptimization.mod.saveSettings({ skipServerSync: true });
          const recoveredOptimizationMeta = JSON.parse(
            poisonedOptimization.lastSharedWrite()
          )._sharedWriteMeta;
          assert(
            recoveredOptimizationMeta.optimizationDecision
              && recoveredOptimizationMeta.optimizationDecision.value === true
              && recoveredOptimizationMeta.optimizationDecision.writeId
                === recoveredOptimizationMeta.writeId,
            'invalid optimization decision ids must not block a fresh local choice'
          );

          console.log('HARNESS_OK');
          // Every sandbox timer is harness-controlled, so the process exits
          // naturally once main() returns and piped stdout is fully flushed.
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
        "unrelated-save-from-unhydrated-window harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
