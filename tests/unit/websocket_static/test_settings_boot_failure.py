import json
import textwrap
from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract


def test_failed_boot_get_keeps_posts_dirty_only_harness():
    # Codex P2 (round 16): the round-15 flag was released in the merge chain's
    # `finally`, which also runs when the GET resolved to null (HTTP error,
    # network error, success:false, unparsable body). The settings view was
    # then marked "settled" without a single server value having been merged,
    # so the next user edit POSTed the FULL boot/localStorage snapshot and a
    # POST succeeding after a transient GET failure overwrote every untouched
    # persisted preference — independentAsrEnabled included.
    #
    # Pin the split: "the GET attempt finished" and "server values were merged"
    # are different facts, and only the latter licenses full snapshots.
    # Scenario 1: HTTP-failed GET + later unrelated user edits -> each new
    # pending edit POST carries only that key, while an acknowledged key is not
    # resent and an idle periodic pass sends nothing. Scenario 2: application-level failure
    # (success:false) + ASR toggle -> the toggle IS persisted. Scenario 3:
    # network-error GET -> still pending-only, and the periodic timer does not
    # re-fetch or resend an acknowledged key. The recovery model remains "stay
    # partial-write-only for this session" — safe because the backend merges partial
    # payloads per key. Scenario 4 (recovery): a GET that fails the bound but
    # eventually SUCCEEDS flips back to full snapshots on its merge.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext(initialSettings) {
          const postCalls = [];
          const getCalls = [];
          const timers = [];
          const intervals = [];
          const storage = {};
          if (initialSettings) {
            storage.project_neko_settings = JSON.stringify(initialSettings);
          }
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval(fn, ms) { intervals.push({ fn, ms }); return 1; },
            clearInterval() {},
            setTimeout(fn, ms) { timers.push({ fn, ms }); return { unref() {} }; },
            clearTimeout() {},
            localStorage: {
              getItem(key) { return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; },
              setItem() {},
              removeItem(key) { delete storage[key]; },
            },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, headers: opts.headers, resolve, reject });
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
            firePeriodicTick() {
              assert(intervals.length === 1, 'exactly one periodic sync timer must be armed');
              intervals[0].fn();
            },
          };
        }

        const okPost = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function main() {
          // ---- Scenario 1: HTTP-failed boot GET, then unrelated user edits.
          const ctx = makeContext();
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          ctx.getCalls[0].resolve({ ok: false, status: 500 });
          await tick();
          await tick();
          assert(
            ctx.S.settingsHydrated === false,
            'a failed GET must not hydrate the handshake view'
          );
          assert(ctx.postCalls.length === 0, 'a failed GET must not POST anything by itself');

          ctx.win.mergeMessagesEnabled = true;   // settings-popup mirror
          ctx.mod.saveSettings();                // user path -> userInitiated POST
          assert(ctx.S.settingsHydrated === true, 'the user change hydrates synchronously');
          await tick();
          await tick();
          assert(ctx.postCalls.length === 1, 'the user edit must still be persisted (liveness)');
          const body1 = JSON.parse(ctx.postCalls[0].body);
          assert(body1.mergeMessagesEnabled === true, 'the dirty key must be persisted');
          assert(
            !('independentAsrEnabled' in body1),
            'a failed GET must NOT license a full snapshot: the untouched ASR preference '
              + 'would clobber the persisted value, got: ' + JSON.stringify(body1)
          );
          assert(
            Object.keys(body1).length === 1,
            'only the dirty key may travel, got: ' + JSON.stringify(body1)
          );
          ctx.postCalls[0].resolve(okPost);
          await tick();

          // The restriction does not decay: a LATER, second edit is still
          // pending-only. The first key was acknowledged and must not be resent.
          ctx.win.focusModeEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx.postCalls.length === 2, 'the second user edit is persisted too');
          const body2 = JSON.parse(ctx.postCalls[1].body);
          assert(body2.focusModeEnabled === true, 'the newly dirtied key is persisted');
          assert(!('mergeMessagesEnabled' in body2), 'the acknowledged key is no longer pending');
          assert(
            Object.keys(body2).length === 1,
            'only the new pending key travels, got: ' + JSON.stringify(body2)
          );
          ctx.postCalls[1].resolve(okPost);
          await tick();

          // ... and the periodic sync (no userInitiated) neither widens nor
          // resends an already acknowledged body, and never rejects.
          const pp = ctx.mod.syncSettingsToServer();
          await tick();
          await tick();
          assert(ctx.postCalls.length === 2, 'the periodic-style sync has no pending keys to write');
          await pp;

          // ---- Scenario 2: application-level failure + the ASR toggle.
          const ctx2 = makeContext();
          ctx2.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: false, error: 'boom' }),
          });
          await tick();
          await tick();
          ctx2.S.independentAsrEnabled = true;
          ctx2.mod.saveSettings({ skipServerSync: true });   // toggle handler persists locally
          const p2 = ctx2.mod.syncSettingsToServer({ userInitiated: true });
          assert(
            ctx2.S.settingsHydrated === true,
            'the user toggle is authoritative for the handshake even without a merge'
          );
          await tick();
          await tick();
          assert(ctx2.postCalls.length === 1, 'the ASR toggle must be persisted after a failed GET');
          const asrBody = JSON.parse(ctx2.postCalls[0].body);
          assert(asrBody.independentAsrEnabled === true, 'the toggle carries the user choice');
          assert(
            Object.keys(asrBody).length === 1,
            'the toggle POST carries nothing else, got: ' + JSON.stringify(asrBody)
          );
          ctx2.postCalls[0].resolve(okPost);
          await p2;

          // ---- Scenario 3: network-error GET; the periodic timer only POSTs.
          const ctx3 = makeContext();
          ctx3.getCalls[0].reject(new Error('offline'));
          await tick();
          await tick();
          ctx3.win.mergeMessagesEnabled = true;
          ctx3.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx3.postCalls.length === 1, 'the edit is persisted after a network-error GET');
          assert(
            Object.keys(JSON.parse(ctx3.postCalls[0].body)).length === 1,
            'a rejected GET keeps POSTs dirty-only'
          );
          ctx3.postCalls[0].resolve(okPost);
          await tick();
          ctx3.firePeriodicTick();
          await tick();
          await tick();
          assert(
            ctx3.getCalls.length === 1,
            'no path re-fetches the settings GET, so dirty-only must be permanently safe '
              + 'rather than a temporary state (recovery is a fresh page load)'
          );
          assert(ctx3.postCalls.length === 1, 'the periodic tick does not resend an acknowledged key');

          // ---- Scenario 4 (recovery): the bound elapses, the POST goes out
          // dirty-only, and the GET LATER succeeds -> full snapshots resume.
          const ctx4 = makeContext();
          ctx4.win.mergeMessagesEnabled = true;
          ctx4.mod.saveSettings();
          await tick();
          ctx4.fireGateTimeout();
          await tick();
          await tick();
          assert(ctx4.postCalls.length === 1, 'the bound releases the POST');
          assert(
            Object.keys(JSON.parse(ctx4.postCalls[0].body)).length === 1,
            'an unmerged view posts dirty keys only'
          );
          ctx4.postCalls[0].resolve(okPost);
          ctx4.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: true, mergeMessagesEnabled: false },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(ctx4.S.independentAsrEnabled === true, 'the untouched key hydrates from the server');
          assert(ctx4.S.mergeMessagesEnabled === true, 'the dirty key survives the late merge');
          assert(ctx4.postCalls.length === 2, 'the merge writeback POST follows');
          const recovered = JSON.parse(ctx4.postCalls[1].body);
          assert(
            Object.keys(recovered).length > 2,
            'a real merge restores full snapshots, got: ' + JSON.stringify(recovered)
          );
          assert(recovered.independentAsrEnabled === true, 'the writeback carries the server value');
          ctx4.postCalls[1].resolve(okPost);
          await tick();
          ctx4.win.focusModeEnabled = true;
          ctx4.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx4.postCalls.length === 3, 'the post-recovery user edit POSTs');
          const afterRecovery = JSON.parse(ctx4.postCalls[2].body);
          assert(
            Object.keys(afterRecovery).length > 2,
            'post-recovery edits keep using full snapshots, got: ' + JSON.stringify(afterRecovery)
          );
          assert(
            afterRecovery.independentAsrEnabled === true,
            'the full snapshot carries the merged server value, not the boot default'
          );
          ctx4.postCalls[2].resolve(okPost);
          await tick();

          // ---- Scenario 5: a legacy/mangled delayed GET omits revision. Once
          // a POST established a comparable revision, its ETag must not be
          // downgraded by the unversioned response.
          const ctxMissingRevision = makeContext();
          ctxMissingRevision.win.mergeMessagesEnabled = true;
          ctxMissingRevision.mod.saveSettings();
          await tick();
          ctxMissingRevision.fireGateTimeout();
          await tick();
          await tick();
          ctxMissingRevision.postCalls[0].resolve({
            ok: true,
            status: 200,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-1"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: true,
              },
              revision: 1,
              decisions: {},
            }),
          });
          await tick();
          await tick();
          ctxMissingRevision.getCalls[0].resolve({
            ok: true,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-0"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: false,
              },
              decisions: {},
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            ctxMissingRevision.S.mergeMessagesEnabled === true,
            'an unversioned late GET must preserve the acknowledged local key'
          );
          ctxMissingRevision.win.focusModeEnabled = true;
          ctxMissingRevision.mod.saveSettings();
          await tick();
          await tick();
          assert(
            ctxMissingRevision.postCalls[1].headers['If-Match']
              === '"conversation-settings-1"',
            'an unversioned late GET must not downgrade the confirmed ETag'
          );
          ctxMissingRevision.postCalls[1].resolve(okPost);
          await tick();

          // ---- Scenario 6: the delayed boot GET is newer than the POST that
          // acknowledged a local edit. The newer server snapshot must win.
          const ctxNewer = makeContext();
          ctxNewer.win.mergeMessagesEnabled = true;
          ctxNewer.mod.saveSettings();
          await tick();
          ctxNewer.fireGateTimeout();
          await tick();
          await tick();
          assert(ctxNewer.postCalls.length === 1, 'the bound releases the local edit');
          ctxNewer.postCalls[0].resolve({
            ok: true,
            status: 200,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-1"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: true,
              },
              revision: 1,
              decisions: {},
            }),
          });
          await tick();
          await tick();
          ctxNewer.getCalls[0].resolve({
            ok: true,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-2"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: false,
              },
              revision: 2,
              decisions: {},
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            ctxNewer.S.mergeMessagesEnabled === false,
            'a boot GET newer than the acknowledged POST must win'
          );
          assert(ctxNewer.postCalls.length === 2, 'the newer merge writes back once');
          ctxNewer.postCalls[1].resolve(okPost);
          await tick();

          // ---- Scenario 7: a timeout-released partial POST advances the server
          // revision before the captured boot GET returns. The older GET must
          // not roll back even fields that were never locally dirty.
          const ctxOlder = makeContext();
          ctxOlder.win.mergeMessagesEnabled = true;
          ctxOlder.mod.saveSettings();
          await tick();
          ctxOlder.fireGateTimeout();
          await tick();
          await tick();
          ctxOlder.postCalls[0].resolve({
            ok: true,
            status: 200,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-2"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: true,
                slopFilterEnabled: true,
              },
              revision: 2,
              decisions: {},
            }),
          });
          await tick();
          await tick();
          assert(ctxOlder.S.slopFilterEnabled === true, 'the POST response hydrates rev2');
          ctxOlder.getCalls[0].resolve({
            ok: true,
            headers: {
              get(name) {
                return name.toLowerCase() === 'etag'
                  ? '"conversation-settings-1"'
                  : null;
              },
            },
            json: async () => ({
              success: true,
              settings: {
                independentAsrEnabled: false,
                mergeMessagesEnabled: false,
                slopFilterEnabled: false,
              },
              revision: 1,
              decisions: {},
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            ctxOlder.S.mergeMessagesEnabled === true
              && ctxOlder.S.slopFilterEnabled === true,
            'a delayed older GET must not merge any stale settings fields'
          );
          assert(
            ctxOlder.postCalls.length === 1,
            'discarding an older GET must not trigger a stale writeback'
          );

          // ---- Scenario 8: a newer explicit localStorage ASR decision arrives
          // before its origin window's POST. The boot GET is older and must not
          // overwrite either the local value or the tuple that will accompany
          // the next save.
          const ctx5 = makeContext({
            independentAsrEnabled: true,
            _sharedWriteMeta: {
              writeId: 20,
              writerId: 'window-b',
              changedKeys: ['independentAsrEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              asrDecision: { writeId: 20, writerId: 'window-b', value: true },
            },
          });
          ctx5.getCalls[0].resolve({
            ok: true,
            headers: { get(name) { return name.toLowerCase() === 'etag' ? '"conversation-settings-3"' : null; } },
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: false, mergeMessagesEnabled: false },
              decisions: {
                independentAsrEnabled: {
                  writeId: 10,
                  writerId: 'window-a',
                  value: false,
                },
              },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(
            ctx5.S.independentAsrEnabled === true,
            'an older boot GET must not overwrite the newer local ASR choice'
          );
          assert(ctx5.postCalls.length === 0, 'preserving the local winner needs no merge writeback');
          ctx5.win.focusModeEnabled = true;
          ctx5.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx5.postCalls.length === 1, 'a later unrelated edit is persisted');
          const afterLocalWinner = JSON.parse(ctx5.postCalls[0].body);
          assert(
            afterLocalWinner.independentAsrEnabled === true,
            'the later full snapshot keeps the newer local ASR value'
          );
          const decisionHeader = JSON.parse(
            ctx5.postCalls[0].headers['X-Conversation-Settings-ASR-Decision']
          );
          assert(
            decisionHeader.writeId === 20
              && decisionHeader.writerId === 'window-b'
              && decisionHeader.value === true,
            'the later POST carries the newer local ASR decision tuple'
          );
          ctx5.postCalls[0].resolve(okPost);
          await tick();

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
        "failed-boot-GET dirty-only harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
