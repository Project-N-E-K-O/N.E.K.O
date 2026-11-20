import json
import textwrap
from pathlib import Path

import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

STATIC_APP = Path(__file__).resolve().parents[3] / "static" / "app"
APP_SETTINGS_PATH = STATIC_APP / "app-settings.js"
APP_PROACTIVE_PATH = STATIC_APP / "app-proactive.js"
APP_WEBSOCKET_PATH = STATIC_APP / "app-websocket.js"

pytestmark = pytest.mark.frontend_contract


# Two windows share one localStorage. Window A turns privacy mode on; window B
# has not received A's storage event yet and saves an unrelated setting. B must
# not report "screen consent allowed" to the backend, must not write its stale
# copy back to localStorage or the server, and must still honour its own later
# explicit choice. A single window toggling normally keeps its old behaviour.
_HARNESS = textwrap.dedent(
    """
    const fs = require('node:fs');
    const vm = require('node:vm');
    const settingsSource = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');
    const proactiveSource = fs.readFileSync(__APP_PROACTIVE_PATH__, 'utf8');

    function assert(cond, msg) {
      if (!cond) throw new Error('ASSERT: ' + msg);
    }
    const tick = () => new Promise((resolve) => setImmediate(resolve));
    const settle = async () => { for (let i = 0; i < 6; i += 1) await tick(); };

    function response(ok, status, etag, data) {
      return {
        ok,
        status,
        headers: { get(name) { return name.toLowerCase() === 'etag' ? etag : null; } },
        json: async () => data,
      };
    }

    // One shared store; each write queues a storage event for every other
    // window, delivered only when the test says so (the stale-window gap).
    const store = new Map();
    let clock = Date.now();
    const windows = [];
    let serverRevision = 0;
    let serverSettings = { proactiveVisionEnabled: true, mergeMessagesEnabled: false };

    function makeWindow(name) {
      let storageListener = null;
      const inbox = [];
      const consent = [];
      const postBodies = [];
      let win = null;
      const sandbox = {
        // One shared clock that always moves forward, as in a real profile
        // where user actions are far apart: write ids never tie by accident.
        Date: class extends Date {
          static now() { clock += 5; return clock; }
        },
        console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
        setInterval() { return 0; },
        clearInterval() {},
        setTimeout(fn, ms) {
          const timer = setTimeout(fn, ms);
          if (timer && typeof timer.unref === 'function') timer.unref();
          return timer;
        },
        clearTimeout,
        localStorage: {
          getItem(key) { return store.has(key) ? store.get(key) : null; },
          setItem(key, value) {
            store.set(key, String(value));
            for (const other of windows) {
              if (other.name !== name) other.inbox.push({ key, newValue: String(value) });
            }
          },
          removeItem(key) { store.delete(key); },
        },
        document: {
          addEventListener() {},
          getElementById() { return null; },
          querySelector() { return null; },
          body: { classList: { contains() { return false; } }, getAttribute() { return null; } },
        },
        fetch(url, opts) {
          if (opts && opts.method === 'POST') {
            const body = JSON.parse(opts.body);
            postBodies.push(body);
            // The server applies a POST when it arrives, with If-Match CAS.
            // A held window's POSTs stay in flight until released.
            const arrive = () => {
              const current = '"conversation-settings-' + serverRevision + '"';
              const ifMatch = opts.headers && opts.headers['If-Match'];
              if (ifMatch && ifMatch !== current) {
                return response(false, 412, current, {
                  success: false, settings: serverSettings, revision: serverRevision,
                });
              }
              serverRevision += 1;
              serverSettings = Object.assign({}, serverSettings, body);
              const etag = '"conversation-settings-' + serverRevision + '"';
              return response(true, 200, etag, {
                success: true, settings: serverSettings, revision: serverRevision,
              });
            };
            if (win && win.holdPosts) {
              return new Promise((resolve) => win.heldPosts.push(() => resolve(arrive())));
            }
            return Promise.resolve(arrive());
          }
          const etag = '"conversation-settings-' + serverRevision + '"';
          return Promise.resolve(response(true, 200, etag, {
            success: true, settings: serverSettings, revision: serverRevision,
            telemetryBranch: null, decisions: {},
          }));
        },
      };
      sandbox.window = {
        appState: {
          proactiveVisionEnabled: true,
          mergeMessagesEnabled: false,
          independentAsrEnabled: false,
          settingsHydrated: false,
        },
        appConst: {},
        appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
        appWebSocket: {
          syncProactiveVisionConsent(enabled) { consent.push(enabled); },
        },
        addEventListener(type, fn) { if (type === 'storage') storageListener = fn; },
        removeEventListener() {},
        dispatchEvent() {},
      };
      // app-state.js binds these window mirrors to appState.
      for (const key of ['proactiveVisionEnabled', 'mergeMessagesEnabled']) {
        Object.defineProperty(sandbox.window, key, {
          get() { return sandbox.window.appState[key]; },
          set(v) { sandbox.window.appState[key] = v; },
          configurable: true,
          enumerable: true,
        });
      }
      vm.createContext(sandbox);
      vm.runInContext(proactiveSource, sandbox, { filename: 'app-proactive.js' });
      vm.runInContext(settingsSource, sandbox, { filename: 'app-settings.js' });
      assert(storageListener, name + ': storage listener registered');
      const runtime = { stoppedScreening: 0 };
      sandbox.window.stopScreening = () => { runtime.stoppedScreening += 1; };
      win = {
        name,
        holdPosts: false,
        heldPosts: [],
        releasePosts() {
          this.holdPosts = false;
          while (this.heldPosts.length) this.heldPosts.shift()();
        },
        runtime,
        inbox,
        consent,
        postBodies,
        S: sandbox.window.appState,
        mod: sandbox.window.appSettings,
        fire(event) { storageListener(event); },
        deliver() {
          while (inbox.length) {
            const event = inbox.shift();
            storageListener(event);
          }
        },
      };
      windows.push(win);
      return win;
    }

    const websocketSource = fs.readFileSync(__APP_WEBSOCKET_PATH__, 'utf8');
    const syncSlice = websocketSource.slice(
      websocketSource.indexOf('    function syncProactiveVisionConsent('),
      websocketSource.indexOf('    function ensureWebSocketOpen(')
    );
    // What the window would report on a WebSocket reconnect.
    function reconnectConsent(win) {
      const sent = [];
      const S = Object.assign({}, win.S, {
        socket: { readyState: 1, send(raw) { sent.push(JSON.parse(raw).enabled); } },
      });
      const mod = {};
      vm.runInNewContext(syncSlice, {
        window: { appSettings: win.mod }, S, mod, WebSocket: { OPEN: 1 }, console: { warn() {} },
      });
      mod.syncProactiveVisionConsent();
      return sent[0];
    }

    function stored() {
      return JSON.parse(store.get('project_neko_settings')).proactiveVisionEnabled;
    }
    const lastConsent = (win) => win.consent[win.consent.length - 1];

    async function singleWindow() {
      // Single window: plain toggles behave as before (fresh profile).
      const solo = makeWindow('solo');
      await settle();
      solo.deliver();
      solo.consent.length = 0;
      solo.S.proactiveVisionEnabled = false;
      solo.mod.saveSettings();
      await settle();
      solo.S.proactiveVisionEnabled = true;
      solo.mod.saveSettings();
      await settle();
      assert(JSON.stringify(solo.consent) === JSON.stringify([false, true]),
        'single-window toggles report exactly their value: ' + JSON.stringify(solo.consent));

    }

    async function main() {
      if (__MODE__ === 'single') {
        await singleWindow();
        console.log('HARNESS_OK');
        process.exitCode = 0;
        return;
      }
      const a = makeWindow('A');
      const b = makeWindow('B');
      await settle();
      a.deliver();
      b.deliver();
      assert(a.S.proactiveVisionEnabled === true && b.S.proactiveVisionEnabled === true,
        'both windows boot with screen consent allowed');

      // A: the user turns privacy mode on (consent revoked).
      a.S.proactiveVisionEnabled = false;
      a.mod.saveSettings();
      await settle();
      assert(lastConsent(a) === false, 'A reports the revocation');
      assert(stored() === false, 'A saved the revocation');

      // A then saves something unrelated: the latest snapshot no longer lists
      // the privacy key as changed, only its per-key write token remembers.
      a.S.mergeMessagesEnabled = !a.S.mergeMessagesEnabled;
      a.mod.saveSettings();
      await settle();
      assert(JSON.parse(store.get('project_neko_settings'))._sharedWriteMeta.changedKeys
        .indexOf('proactiveVisionEnabled') === -1,
        'precondition: the latest snapshot does not list the privacy key');

      // B: stale memory, A's storage events not delivered yet; unrelated save.
      assert(b.S.proactiveVisionEnabled === true, 'precondition: B is stale');
      b.S.mergeMessagesEnabled = true;
      b.mod.saveSettings();
      await settle();
      assert(b.consent.length > 0, 'B reported consent on save');
      assert(b.consent.every((v) => v === false),
        'stale B must not re-grant consent: ' + JSON.stringify(b.consent));
      assert(b.S.proactiveVisionEnabled === false, 'B adopts the unseen revocation');
      assert(stored() === false, 'B must not write its stale "allowed" copy back');
      const lastBody = b.postBodies[b.postBodies.length - 1];
      assert(lastBody && lastBody.proactiveVisionEnabled === false,
        'B must not POST its stale "allowed" copy');
      assert(lastBody.mergeMessagesEnabled === true, 'B still saves its own change');
      assert(serverSettings.proactiveVisionEnabled === false, 'server keeps consent revoked');

      // Late events arrive; nothing flips back.
      b.deliver();
      a.deliver();
      await settle();
      assert(a.S.proactiveVisionEnabled === false && b.S.proactiveVisionEnabled === false,
        'both windows converge on revoked');
      assert(lastConsent(a) === false && lastConsent(b) === false,
        'neither window re-grants after the late events');

      // B: the user now explicitly turns privacy mode off again. That is newer
      // intent and must still reach the backend.
      b.S.proactiveVisionEnabled = true;
      b.mod.saveSettings();
      await settle();
      assert(lastConsent(b) === true, 'an explicit re-enable in B is reported');
      assert(stored() === true, 'an explicit re-enable in B is saved');
      a.deliver();
      await settle();
      assert(a.S.proactiveVisionEnabled === true, 'A applies B\\'s explicit re-enable');
      assert(lastConsent(a) === true, 'A reports the re-enable once received');

      // A stale "revoked" copy is not a revocation: a stale window's
      // incidental false whose per-key token is no newer than B's own enable.
      store.set('project_neko_settings', JSON.stringify(Object.assign(
        JSON.parse(store.get('project_neko_settings')),
        {
          proactiveVisionEnabled: false,
          _sharedWriteMeta: Object.assign(
            {},
            JSON.parse(store.get('project_neko_settings'))._sharedWriteMeta,
            { writeId: Date.now() + 1000, writerId: 'zz-stale', changedKeys: [] }
          ),
        }
      )));
      b.S.mergeMessagesEnabled = false;
      b.mod.saveSettings();
      await settle();
      assert(b.S.proactiveVisionEnabled === true,
        'an incidental stale false must not override B\\'s consent');

      // Listener entry: D processes an older event from C while C's newer
      // revocation is already in localStorage. D must not report "allowed".
      windows.length = 0;
      const c = makeWindow('C');
      const d = makeWindow('D');
      await settle();
      c.deliver();
      d.deliver();
      assert(c.S.proactiveVisionEnabled === true && d.S.proactiveVisionEnabled === true,
        'pair boots with consent allowed');
      c.S.mergeMessagesEnabled = !c.S.mergeMessagesEnabled;
      c.mod.saveSettings();
      await settle();
      c.S.proactiveVisionEnabled = false;
      c.mod.saveSettings();
      await settle();
      const older = d.inbox.shift();
      assert(JSON.parse(older.newValue).proactiveVisionEnabled === true,
        'precondition: the first event predates the revocation');
      assert(reconnectConsent(d) === false,
        'a reconnect before the revocation event arrives does not re-grant');
      d.consent.length = 0;
      d.runtime.stoppedScreening = 0;
      d.fire(older);
      assert(d.runtime.stoppedScreening === 1,
        'D stops proactive screen sending as soon as the revocation is visible');
      assert(d.consent.length > 0, 'the listener reported consent');
      assert(d.consent.every((v) => v === false),
        'an older event must not re-grant consent: ' + JSON.stringify(d.consent));
      d.deliver();
      await settle();
      assert(d.S.proactiveVisionEnabled === false && lastConsent(d) === false,
        'D converges on revoked');

      // A fresh choice in D is newer than C's unseen writes: C turns consent
      // on and off again, then the user turns privacy mode off in D.
      c.S.proactiveVisionEnabled = true;
      c.mod.saveSettings();
      await settle();
      c.S.proactiveVisionEnabled = false;
      c.mod.saveSettings();
      await settle();
      d.S.proactiveVisionEnabled = true;
      d.mod.saveSettings();
      await settle();
      assert(d.S.proactiveVisionEnabled === true, 'D keeps its fresh choice');
      assert(lastConsent(d) === true, 'D reports its fresh choice');
      assert(stored() === true, 'D saves its fresh choice');

      // An incidental stale "false" is not a revocation on the listener and
      // reconnect paths either. C has not received D's re-enable and saves
      // something unrelated, copying its old "false" along. D rejects the
      // value by its write token and must keep reporting "allowed" without
      // stopping its screen sending.
      d.deliver(); // C's older explicit writes, superseded by D's choice
      await settle();
      assert(d.S.proactiveVisionEnabled === true, 'precondition: D kept its choice');
      assert(c.S.proactiveVisionEnabled === false, 'precondition: C is stale');
      c.holdPosts = true; // until its POST conflicts and merges, C's copy stays stale
      c.S.mergeMessagesEnabled = !c.S.mergeMessagesEnabled;
      c.mod.saveSettings();
      await settle();
      assert(stored() === false && JSON.parse(store.get('project_neko_settings'))
        ._sharedWriteMeta.changedKeys.indexOf('proactiveVisionEnabled') === -1,
        'precondition: an incidental stale false is in localStorage');
      d.consent.length = 0;
      d.runtime.stoppedScreening = 0;
      d.deliver();
      await settle();
      assert(d.S.proactiveVisionEnabled === true, 'D rejects the incidental false');
      assert(d.consent.length > 0 && d.consent.every((v) => v === true),
        'D keeps reporting "allowed": ' + JSON.stringify(d.consent));
      assert(d.runtime.stoppedScreening === 0, 'D does not stop its screen sending');
      assert(reconnectConsent(d) === true, 'a reconnect keeps reporting "allowed"');
      c.releasePosts();
      await settle();
      c.deliver();
      d.deliver();
      await settle();
      assert(c.S.proactiveVisionEnabled === true && d.S.proactiveVisionEnabled === true
        && stored() === true, 'C catches up with the re-enable from D');

      // 412 path, with a window that never touched the privacy key: E saves
      // something unrelated (lands, so F's ETag is now stale), then revokes
      // while that POST is still in flight. F adopts the revocation; its POST
      // gets 412 with the server still saying "allowed". The conflict merge
      // must keep the adopted revocation.
      windows.length = 0;
      serverSettings = Object.assign({}, serverSettings, { proactiveVisionEnabled: true });
      serverRevision += 1;
      const e = makeWindow('E');
      const f = makeWindow('F');
      await settle();
      e.deliver();
      f.deliver();
      assert(e.S.proactiveVisionEnabled === true && f.S.proactiveVisionEnabled === true,
        'precondition: consent allowed in both windows');
      e.S.mergeMessagesEnabled = !e.S.mergeMessagesEnabled;
      e.mod.saveSettings();
      await settle();
      e.holdPosts = true;
      e.S.proactiveVisionEnabled = false;
      e.mod.saveSettings();
      await settle();
      assert(serverSettings.proactiveVisionEnabled === true,
        'precondition: E\\'s revocation POST is in flight');
      f.consent.length = 0;
      f.S.mergeMessagesEnabled = !f.S.mergeMessagesEnabled;
      f.mod.saveSettings();
      await settle();
      assert(f.postBodies.length >= 2, 'precondition: F\\'s POST hit 412 and retried');
      assert(f.consent.every((v) => v === false),
        'a 412 merge must not re-grant consent: ' + JSON.stringify(f.consent));
      assert(f.S.proactiveVisionEnabled === false, 'F keeps the adopted revocation after 412');
      assert(stored() === false, 'the 412 merge must not save "allowed" back');
      assert(serverSettings.proactiveVisionEnabled === false, 'F\\'s retry persists the revocation');
      e.releasePosts();
      await settle();
      e.deliver();
      f.deliver();
      await settle();
      assert(e.S.proactiveVisionEnabled === false && f.S.proactiveVisionEnabled === false
        && serverSettings.proactiveVisionEnabled === false, 'all converge on revoked');

      console.log('HARNESS_OK');
      process.exitCode = 0;
    }

    main().catch((err) => {
      console.error(err && err.stack ? err.stack : String(err));
      process.exitCode = 1;
    });
    """
).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH))).replace(
    "__APP_PROACTIVE_PATH__", json.dumps(str(APP_PROACTIVE_PATH))
).replace("__APP_WEBSOCKET_PATH__", json.dumps(str(APP_WEBSOCKET_PATH)))


def _run(mode):
    return _run_settings_node_harness(_HARNESS.replace("__MODE__", json.dumps(mode)))


def test_single_window_toggle_reports_its_own_value_harness():
    result = _run("single")
    assert result.returncode == 0, (
        "single-window vision consent harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_stale_window_save_does_not_regrant_vision_consent_harness():
    result = _run("multi")
    assert result.returncode == 0, (
        "stale-window vision consent harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
