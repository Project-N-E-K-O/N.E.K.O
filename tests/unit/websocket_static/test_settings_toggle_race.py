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

pytestmark = pytest.mark.integration_serial


def test_rapid_asr_toggle_double_flip_persists_final_state_harness():
    # Behavioral pin for the Codex P2 fix: drive the real syncSettingsToServer
    # with a controllable fetch and simulate the double-flip race. Before the
    # fix both POSTs were in flight together and completing them in reverse
    # order let the stale body be the backend's last save; now the second POST
    # must not even be issued until the first settles, and its body must carry
    # the final toggle state.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext() {
          // Module load runs loadSettings(), which issues a boot GET; the
          // harness settles it (as a failure) so the bounded settings-POST
          // gate opens without waiting for its timeout, and assertions below
          // look only at the POST calls.
          const postCalls = [];
          const getCalls = [];
          const timeoutCallbacks = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              // Unref'd so a pending gate timer cannot hold the process open;
              // the harness then exits naturally and stdout always flushes.
              const t = setTimeout(fn, ms);
              if (t && typeof t.unref === 'function') t.unref();
              timeoutCallbacks.push({ fn, ms, timer: t });
              return t;
            },
            clearTimeout,
            localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              return new Promise((resolve, reject) => {
                if (opts && opts.method === 'POST') {
                  postCalls.push({ url, body: opts.body, opts, resolve, reject });
                } else {
                  getCalls.push({ url, resolve, reject });
                }
              });
            },
          };
          sandbox.window = {
            appState: {
              independentAsrEnabled: false,
              slopFilterEnabled: false,
              focusModeEnabled: false,
              settingsHydrated: false,
            },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            slopFilterEnabled: false,
            focusModeEnabled: false,
            addEventListener() {},
            removeEventListener() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          // The harness drives hydration and the toggle state explicitly from
          // a clean baseline.
          sandbox.window.appState.settingsHydrated = false;
          return {
            postCalls,
            getCalls,
            timeoutCallbacks,
            S: sandbox.window.appState,
            mod: sandbox.window.appSettings,
          };
        }

        const okResponse = { ok: true, json: async () => ({ success: true }) };
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function settleBootGet(ctx) {
          // Fail the boot GET (null result: no merge, no hydration) so the
          // settings-POST gate settles deterministically.
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          ctx.getCalls[0].resolve({ ok: false });
          await tick();
          await tick();
        }

        async function main() {
          const ctx = makeContext();
          const { postCalls, S, mod } = ctx;
          await settleBootGet(ctx);
          assert(S.settingsHydrated === false, 'a failed boot GET must not mark hydration');

          // First flip: POST issued with the pre-second-flip snapshot.
          S.independentAsrEnabled = true;
          const p1 = mod.syncSettingsToServer({ userInitiated: true });
          assert(S.settingsHydrated === true, 'hydration must be marked synchronously at call time');
          await tick();
          assert(postCalls.length === 1, 'first sync must POST immediately');
          assert(JSON.parse(postCalls[0].body).independentAsrEnabled === true, 'first body snapshots true');

          // Second flip while the first POST is still in flight.
          S.independentAsrEnabled = false;
          const p2 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(postCalls.length === 1, 'second POST must be queued, not concurrent (reordered completions impossible)');

          // Only when the first settles does the second go out — carrying the
          // FINAL state because the snapshot is taken at send time.
          postCalls[0].resolve(okResponse);
          await tick();
          assert(postCalls.length === 2, 'queued sync must run after the predecessor completed');
          assert(JSON.parse(postCalls[1].body).independentAsrEnabled === false, 'last-issued body must carry the final toggle state');
          postCalls[1].resolve(okResponse);
          await p1;
          await p2;

          // Negative: a predecessor that fails (network reject) must neither
          // stall the chain nor reject the published promises.
          S.independentAsrEnabled = true;
          const p3 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          S.independentAsrEnabled = false;
          const p4 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(postCalls.length === 3, 'third sync in flight, fourth queued');
          postCalls[2].reject(new Error('network down'));
          await tick();
          assert(postCalls.length === 4, 'a failed predecessor must not stall the queued sync');
          assert(JSON.parse(postCalls[3].body).independentAsrEnabled === false, 'post-failure sync still carries the final state');
          postCalls[3].resolve(okResponse);
          await p3;
          await p4;

          // A transport that never settles must time out and release the
          // serialization tail so the later user state can still be sent.
          const timeoutCountBeforeStall = ctx.timeoutCallbacks.length;
          S.independentAsrEnabled = true;
          const p5 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          S.independentAsrEnabled = false;
          const p6 = mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(postCalls.length === 5, 'fifth sync in flight, sixth queued');
          const requestTimeout = ctx.timeoutCallbacks
            .slice(timeoutCountBeforeStall)
            .find(
              (timer) => timer.ms === 15000
          );
          assert(requestTimeout, 'the in-flight request must have a bounded timeout');
          requestTimeout.fn();
          await tick();
          await tick();
          assert(
            postCalls.length === 6,
            'a timed-out predecessor must release the queued sync'
          );
          assert(
            JSON.parse(postCalls[5].body).independentAsrEnabled === false,
            'the post-timeout sync must rebuild from the final state'
          );
          postCalls[5].resolve(okResponse);
          await p5;
          await p6;

          // Negative: a non-userInitiated (periodic-style) call never marks
          // hydration — and after a FAILED boot GET, with nothing the user
          // touched, it writes nothing at all (round 16: an attempt that
          // merged no server value must not license a full snapshot). Its
          // promise still resolves.
          const fresh = makeContext();
          await settleBootGet(fresh);
          const pp = fresh.mod.syncSettingsToServer();
          await tick();
          await tick();
          assert(fresh.S.settingsHydrated === false, 'periodic-style sync must not mark hydration');
          assert(
            fresh.postCalls.length === 0,
            'no merged server value and no dirty key means there is nothing safe to write'
          );
          await pp;

          // ... but once a real merge licensed full snapshots, the
          // periodic-style call still POSTs and still serializes behind an
          // in-flight sync (the chain itself is unchanged).
          const merged = makeContext();
          assert(merged.getCalls.length === 1, 'boot must issue the settings GET');
          merged.getCalls[0].resolve({
            ok: true,
            json: async () => ({
              success: true,
              settings: { independentAsrEnabled: true },
              telemetryBranch: null,
            }),
          });
          await tick();
          await tick();
          assert(merged.postCalls.length === 1, 'the merge writeback POST goes out first');
          const mp = merged.mod.syncSettingsToServer();
          await tick();
          assert(merged.postCalls.length === 1, 'the periodic-style sync queues behind it');
          merged.postCalls[0].resolve(okResponse);
          await tick();
          await tick();
          assert(merged.postCalls.length === 2, 'it goes out once the predecessor settled');
          assert(
            JSON.parse(merged.postCalls[1].body).independentAsrEnabled === true,
            'and carries the merged full snapshot'
          );
          merged.postCalls[1].resolve(okResponse);
          await mp;

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
        "settings sync harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_shared_settings_writes_carry_explicit_change_metadata():
    # Codex P2 (follow-up): saveSettings() writes independentAsrEnabled into
    # EVERY localStorage snapshot, so the receiving window could not tell a real
    # cross-window toggle from the incidental copy an unrelated save carries.
    # Pin the metadata contract: every shared write goes through
    # _writeSharedSettings, which stamps a monotonic write id, the keys the user
    # explicitly changed, and whether the writer had hydrated.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # No raw write of the shared key may bypass the metadata stamp.
    assert (
        "localStorage.setItem('project_neko_settings', JSON.stringify(settings))"
        not in settings_source
    )
    assert settings_source.count("_writeSharedSettings(") == 4  # 1 def + 3 writes
    save_fn = _block_after(settings_source, "function saveSettings(options) {")
    assert "serverMerged ? [] : _collectExplicitSharedKeys(settings)" in save_fn
    assert "const serverMerged = !!(options && options.serverMerged);" in save_fn
    assert "const pendingRecovery = !!(options && options.pendingRecovery);" in save_fn
    # The pre-hydration migration write is explicitly non-authoritative.
    assert "_writeSharedSettings(settings, []);" in settings_source

    write_fn = settings_source.split(
        "function _writeSharedSettings(", 1
    )[1].split("\n    }", 1)[0]
    assert "writeId: _nextSharedWriteId()," in write_fn
    assert "changedKeys: explicitKeys || []," in write_fn
    assert "hydrated: S.settingsHydrated === true" in write_fn
    assert "pendingRecovery: pendingRecovery === true" in write_fn
    assert "ownMeta.knownKeyWrites = _knownSharedKeyWritesSnapshot();" in write_fn
    assert "ownMeta.serverRevision = _conversationSettingsRevision;" in write_fn
    assert "serverAuthoritativeKeys.slice()" in write_fn
    assert (
        "_rememberSharedKeyWrites(serverAuthoritativeKeys || [], ownMeta)"
        not in write_fn
    )
    assert "localStorage.setItem('project_neko_settings', JSON.stringify(payload));" in write_fn

    # The write id must be strictly increasing within a window and comparable
    # across windows (one wall clock per browser profile).
    id_fn = settings_source.split("function _nextSharedWriteId() {", 1)[1].split("\n    }", 1)[0]
    assert "Date.now()" in id_fn
    # Floor the mint by the highest id ever APPLIED, not just the highest this
    # window minted: otherwise a window that already applied another window's
    # write can mint an id at or below it and have its own write read as
    # superseded, discarding a genuine cross-window toggle. This covers only
    # the already-OBSERVED case -- a genuinely concurrent same-millisecond tie
    # cannot be broken at mint time and is resolved by the listener's
    # explicit-intent rule instead (pinned below).
    assert "Math.max(_lastSharedWriteId, _lastAppliedSharedWriteId)" in id_fn
    assert "_lastSharedWriteId = now > idFloor ? now : idFloor + 1;" in id_fn

    # Explicit keys = still-pending writes PLUS divergence from the dirty-diff
    # baseline (the ASR toggle handler persists locally before its userInitiated
    # sync rolls that baseline). The monotone boot-merge dirty set must not mint
    # a new per-key token for an already-acknowledged value.
    collect_fn = settings_source.split("function _collectExplicitSharedKeys(snapshot) {", 1)[
        1
    ].split("\n    }", 1)[0]
    assert "_pendingSettingsKeys.has(key)" in collect_fn
    assert "_dirtySettingsKeys.has(key)" not in collect_fn
    assert "_settingsBaseline[key] !== snapshot[key]" in collect_fn
    # Negative: only shared keys may be claimed, never the whole snapshot.
    assert "_SHARED_SETTINGS_KEYS.forEach" in collect_fn
    assert "Object.keys(snapshot)" not in collect_fn

    # Metadata-less payloads (a window still running the previous build) parse
    # to null, which routes the listener back to the legacy fallback.
    read_fn = settings_source.split("function _readSharedWriteMeta(settings) {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "if (!meta || typeof meta !== 'object') return null;" in read_fn
    assert "if (!_isValidAsrWriteId(meta.writeId)) return null;" in read_fn
    id_validator = _block_after(
        settings_source,
        "function _isValidAsrWriteId(value, serverAuthoritative) {",
    )
    assert "Number.isSafeInteger(value)" in id_validator
    assert "Date.now() + _ASR_WRITE_ID_MAX_FUTURE_SKEW_MS" in id_validator
    assert "Number.MAX_SAFE_INTEGER - 1" in id_validator
    assert "if (serverAuthoritative === true) return true;" in id_validator
    assert "Array.isArray(meta.changedKeys) ? meta.changedKeys : []" in read_fn
    assert "knownKeyWritesPresent" in read_fn
    optimization_reader = read_fn.split(
        "optimizationDecision: (meta.optimizationDecision", 1
    )[1].split("optimizationDecisionPendingSync:", 1)[0]
    assert "_isValidAsrWriteId(" in optimization_reader
    assert "meta.optimizationDecision.writeId," in optimization_reader
    assert "Number.isInteger(meta.serverRevision)" in optimization_reader
    assert "isFinite(meta.optimizationDecision.writeId)" not in optimization_reader

    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]
    # Authority requires explicit intent + freshness + outranking this window's
    # own explicit choice; absent metadata falls back to today's
    # value-difference behaviour. The fourth term is load-bearing:
    # _lastAppliedSharedWriteId only records writes RECEIVED here, so freshness
    # alone cannot order this window's own pending toggle against a concurrent
    # one from another window, and the two swap values permanently.
    assert (
        "const asrChangedByOtherWindow = meta\n"
        "                ? (asrValueDiffers && asrMarkedExplicit && asrWriteIsNewer\n"
        "                    && asrOutranksLocalChoice)\n"
        "                : asrValueDiffers;" in listener_block
    )
    assert "meta.changedKeys.indexOf('independentAsrEnabled') !== -1" in listener_block
    assert "meta.writeId > _lastAppliedSharedWriteId" in listener_block
    # Freshness bookkeeping happens AFTER the authority decision, never before.
    assert listener_block.index("const asrChangedByOtherWindow") < listener_block.index(
        "_lastAppliedSharedWriteId = meta.writeId;"
    )
    assert listener_block.index("const asrValueIsStale") < listener_block.index(
        "_lastAppliedSharedWriteId = meta.writeId;"
    )
    # A stale/superseded ASR value is dropped from the apply set rather than
    # applied — and only that key, so other shared keys keep syncing.
    assert "delete incoming.independentAsrEnabled;" in listener_block
    assert "applySharedRuntimeSettings(incoming)" in listener_block
