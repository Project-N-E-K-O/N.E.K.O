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
          focusModeEnabled: false,
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
      // app-state.js binds these window mirrors to appState (its proactiveKeys
      // list, switches only).
      for (const key of [
        'proactiveChatEnabled', 'proactiveVisionEnabled', 'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled', 'proactiveCommunityChatEnabled', 'proactiveVideoChatEnabled',
        'proactivePersonalChatEnabled', 'proactiveMusicEnabled', 'proactiveMemeEnabled',
        'proactiveMiniGameInviteEnabled', 'proactiveTheaterInviteEnabled',
        'mergeMessagesEnabled', 'focusModeEnabled',
      ]) {
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
        P: sandbox.window.appProactive,
        win: sandbox.window,
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

    const changedKeysOfStored = () => JSON.parse(store.get('project_neko_settings'))
      ._sharedWriteMeta.changedKeys;

    // A value this window only RECEIVED from another window is not this
    // window's own write, even when this window edited the same key earlier.
    async function receivedValueNotResent() {
      const a = makeWindow('A');
      const b = makeWindow('B');
      await settle();
      a.deliver();
      b.deliver();
      // A edits the key once and that save lands: dirty in A, no longer pending.
      a.S.mergeMessagesEnabled = true;
      a.mod.saveSettings();
      await settle();
      b.deliver();
      await settle();
      assert(b.S.mergeMessagesEnabled === true, 'precondition: B applies A\\'s edit');
      // B turns it off; A receives that.
      b.S.mergeMessagesEnabled = false;
      b.mod.saveSettings();
      await settle();
      a.deliver();
      await settle();
      assert(a.S.mergeMessagesEnabled === false, 'A applies B\\'s change');
      // B turns it on again; its POST is still in flight and A has not
      // received the storage event yet.
      b.holdPosts = true;
      b.S.mergeMessagesEnabled = true;
      b.mod.saveSettings();
      await settle();
      // A saves something unrelated.
      const postsBefore = a.postBodies.length;
      a.S.focusModeEnabled = !a.S.focusModeEnabled;
      a.mod.saveSettings();
      const aKeys = changedKeysOfStored();
      await settle();
      assert(aKeys.indexOf('mergeMessagesEnabled') === -1,
        'a received value is not sent out as A\\'s explicit write: ' + JSON.stringify(aKeys));
      assert(aKeys.indexOf('focusModeEnabled') !== -1, 'A\\'s own edit is explicit');
      assert(a.postBodies.length > postsBefore, 'precondition: A posted its save');
      // B sees A's snapshot: its copy of the key is incidental and older than
      // B's own choice.
      b.deliver();
      await settle();
      assert(b.S.mergeMessagesEnabled === true,
        'A\\'s save must not overwrite B\\'s newer value in B');
      b.releasePosts();
      await settle();
      b.releasePosts();
      await settle();
      a.deliver();
      b.deliver();
      await settle();
      assert(serverSettings.mergeMessagesEnabled === true,
        'A must not overwrite B\\'s newer value on the server');
      assert(serverSettings.focusModeEnabled === a.S.focusModeEnabled, 'A\\'s own edit is saved');
      assert(a.S.mergeMessagesEnabled === true && b.S.mergeMessagesEnabled === true
        && JSON.parse(store.get('project_neko_settings')).mergeMessagesEnabled === true,
        'both windows converge on B\\'s newer value: A=' + a.S.mergeMessagesEnabled
          + ' B=' + b.S.mergeMessagesEnabled);

      // The pair: an edit A has not saved yet stays A's (the existing pending
      // rule, the same one save-time adoption follows). A's POST is held, B
      // saves something unrelated carrying its old copy of the key.
      a.holdPosts = true;
      a.S.mergeMessagesEnabled = false;
      a.mod.saveSettings();
      await settle();
      b.S.focusModeEnabled = !b.S.focusModeEnabled;
      b.mod.saveSettings();
      await settle();
      a.deliver();
      await settle();
      assert(a.S.mergeMessagesEnabled === false, 'A keeps its unsaved edit');
      a.S.focusModeEnabled = !a.S.focusModeEnabled;
      a.mod.saveSettings();
      const pendingKeys = changedKeysOfStored();
      assert(pendingKeys.indexOf('mergeMessagesEnabled') !== -1,
        'A\\'s unsaved edit is still its explicit write: ' + JSON.stringify(pendingKeys));
      a.releasePosts();
      await settle();
      a.releasePosts();
      await settle();
      b.deliver();
      a.deliver();
      await settle();
      assert(serverSettings.mergeMessagesEnabled === false && b.S.mergeMessagesEnabled === false,
        'A\\'s own later edit wins everywhere');
    }

    // Capture-side gates read the shared state: another window's revocation or
    // switch-off blocks here before its storage event arrives, and a revocation
    // in this window blocks even if "allowed" is what localStorage holds.
    async function captureGates() {
      const a = makeWindow('A');
      const b = makeWindow('B');
      await settle();
      a.deliver();
      b.deliver();
      // Screen Share is the only Proactive Chat mode (the vision-only branch).
      const otherModes = ['proactiveNewsChatEnabled', 'proactiveCommunityChatEnabled',
        'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled', 'proactiveMusicEnabled',
        'proactiveMemeEnabled', 'proactiveMiniGameInviteEnabled', 'proactiveTheaterInviteEnabled'];
      for (const w of [a, b]) {
        w.S.proactiveChatEnabled = true;
        w.S.proactiveVisionChatEnabled = true;
        for (const key of otherModes) w.S[key] = false;
        w.win.appScreen = {
          acquireOrReuseCachedStream: async () => null,
          fetchBackendScreenshot: async () => ({ dataUrl: 'FRAME' }),
          scheduleScreenCaptureIdleCheck() {},
        };
      }
      a.mod.saveSettings();
      await settle();
      b.deliver();
      await settle();

      const websocketSlice = (() => {
        const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
        const start = websocketSource.indexOf('(async function () {', branch);
        const end = websocketSource.indexOf('})();', start) + 5;
        return websocketSource.slice(start, end);
      })();
      // What B answers to the backend's request_screenshot. The capture
      // itself is stubbed to always return a frame, so only the reply's own
      // privacy check can decline.
      async function screenshotReply(win) {
        const sent = [];
        const appProactive = Object.assign({}, win.P, {
          captureProactiveChatScreenshotWithSource: async () => ({ dataUrl: 'FRAME', captureType: 'screen' }),
        });
        await vm.runInNewContext(websocketSlice, {
          window: { appProactive, isNekoHomeTutorialProactiveSuppressed: () => false },
          S: Object.assign({}, win.S, {
            socket: { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } },
          }),
          response: {}, WebSocket: { OPEN: 1 }, console: { warn() {} },
        });
        assert(sent.length === 1, 'one screenshot reply');
        return sent[0];
      }
      function textVisionDropped(win) {
        const body = { screenshot_data: 'FRAME', enabled_modes: ['vision', 'news'] };
        const seq = win.P.currentProactiveVisionRevocationSeq();
        return win.P.discardVisionRevokedSince(body, seq);
      }
      async function expectAllowed(label) {
        assert(b.P.canTriggerProactively() === true, label + ': B may trigger');
        const shot = await b.P.captureProactiveChatScreenshotWithSource();
        assert(shot.dataUrl === 'FRAME' && shot.declined !== true, label + ': B captures');
        const reply = await screenshotReply(b);
        assert(reply.data === 'FRAME' && reply.declined !== true, label + ': B replies with the frame');
        assert(textVisionDropped(b) === false, label + ': B keeps the text-chat screenshot');
      }
      async function restore() {
        a.S.proactiveVisionEnabled = true;
        a.S.proactiveChatEnabled = true;
        a.S.proactiveVisionChatEnabled = true;
        a.mod.saveSettings();
        await settle();
        b.deliver();
        a.deliver();
        await settle();
        b.S.proactiveVisionEnabled = true;
      }

      await expectAllowed('nothing revoked');

      // A turns Screen Share off; B has not received the event yet.
      a.S.proactiveVisionChatEnabled = false;
      a.mod.saveSettings();
      await settle();
      assert(b.S.proactiveVisionChatEnabled === true, 'precondition: B is stale');
      assert(b.P.canTriggerProactively() === false,
        'Screen Share off in another window blocks a vision-only trigger here');
      assert(textVisionDropped(b) === true, 'and drops the text-chat screenshot');
      await restore();
      await expectAllowed('Screen Share back on');

      // A turns Proactive Chat off; B has not received the event yet. Checked
      // with a second, non-vision mode on too, so the vision-only branch does
      // not answer for the switch.
      for (const withNews of [false, true]) {
        for (const w of [a, b]) w.S.proactiveNewsChatEnabled = withNews;
        a.mod.saveSettings();
        await settle();
        b.deliver();
        await settle();
        assert(b.P.canTriggerProactively() === true, 'precondition: B may trigger (news=' + withNews + ')');
        a.S.proactiveChatEnabled = false;
        a.mod.saveSettings();
        await settle();
        assert(b.S.proactiveChatEnabled === true, 'precondition: B is stale');
        assert(b.P.canTriggerProactively() === false,
          'Proactive Chat off in another window blocks a trigger here (news=' + withNews + ')');
        assert(textVisionDropped(b) === true, 'and drops the text-chat screenshot');
        await restore();
      }
      for (const w of [a, b]) w.S.proactiveNewsChatEnabled = false;
      a.mod.saveSettings();
      await settle();
      b.deliver();
      await settle();
      await expectAllowed('Proactive Chat back on');

      // A turns privacy mode on (consent revoked); B has not received it yet.
      a.S.proactiveVisionEnabled = false;
      a.mod.saveSettings();
      await settle();
      assert(b.S.proactiveVisionEnabled === true, 'precondition: B is stale');
      assert(b.P.canTriggerProactively() === false, 'a revocation elsewhere blocks a vision-only trigger');
      const declined = await b.P.captureProactiveChatScreenshotWithSource();
      assert(declined.declined === true && !declined.dataUrl, 'a revocation elsewhere declines capture');
      const declinedReply = await screenshotReply(b);
      assert(declinedReply.declined === true && declinedReply.data === '',
        'a revocation elsewhere declines request_screenshot');
      assert(textVisionDropped(b) === true, 'a revocation elsewhere drops the text-chat screenshot');
      await restore();
      await expectAllowed('consent back on');


      // B revokes, but "allowed" is what localStorage holds (B's own write
      // failed, or a stale window's copy landed over it). B still blocks.
      b.S.proactiveVisionEnabled = false;
      assert(JSON.parse(store.get('project_neko_settings')).proactiveVisionEnabled === true,
        'precondition: localStorage still says allowed');
      const ownDecline = await b.P.captureProactiveChatScreenshotWithSource();
      assert(ownDecline.declined === true && !ownDecline.dataUrl,
        'this window\\'s revocation declines capture');
      const ownReply = await screenshotReply(b);
      assert(ownReply.declined === true && ownReply.data === '',
        'this window\\'s revocation declines request_screenshot');
      assert(b.P.canTriggerProactively() === false, 'and blocks a vision-only trigger');
      assert(textVisionDropped(b) === true, 'and drops the text-chat screenshot');
    }

    // A stale "off" copied along in an unrelated save is not a switch-off:
    // the listener rejects it, so no later event corrects it, and the trigger
    // gate must not stop proactive chat on it. C turns the switch on; D,
    // before receiving that, saves something unrelated and copies its old
    // "off" back into localStorage. (An explicit "off" from another window
    // still blocks: see captureGates.)
    async function staleSwitchCopy(key) {
      const otherModes = ['proactiveNewsChatEnabled', 'proactiveCommunityChatEnabled',
        'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled', 'proactiveMusicEnabled',
        'proactiveMemeEnabled', 'proactiveMiniGameInviteEnabled', 'proactiveTheaterInviteEnabled'];
      const c = makeWindow('C');
      const d = makeWindow('D');
      await settle();
      c.deliver();
      d.deliver();
      // Set up in C only; D receives it (no edits of its own pending).
      c.S.proactiveChatEnabled = true;
      c.S.proactiveVisionChatEnabled = true;
      c.S.proactiveVisionEnabled = true;
      for (const other of otherModes) c.S[other] = false;
      c.S[key] = false;
      c.mod.saveSettings();
      await settle();
      d.deliver();
      c.deliver();
      await settle();
      assert(d.S[key] === false, 'precondition: D has C\\'s setup');
      c.S[key] = true;
      c.mod.saveSettings();
      await settle();
      d.holdPosts = true; // nothing rewrites localStorage while C checks
      d.S.focusModeEnabled = !d.S.focusModeEnabled;
      d.mod.saveSettings();
      await settle();
      const copied = JSON.parse(store.get('project_neko_settings'));
      assert(copied[key] === false && copied._sharedWriteMeta.changedKeys.indexOf(key) === -1,
        'precondition: a stale ' + key + ' "off" is in localStorage');
      c.deliver();
      await settle();
      assert(c.S[key] === true, 'C rejects the stale ' + key + ' copy');
      assert(JSON.parse(store.get('project_neko_settings'))[key] === false,
        'precondition: the stale ' + key + ' copy is still in localStorage');
      assert(c.P.canTriggerProactively() === true,
        'a stale ' + key + ' "off" does not stop proactive chat');
      d.deliver();
      await settle();
      assert(d.S[key] === true && d.P.canTriggerProactively() === true,
        'D adopts C\\'s ' + key + ' and may trigger');
    }

    async function main() {
      if (__MODE__ === 'single') {
        await singleWindow();
        console.log('HARNESS_OK');
        process.exitCode = 0;
        return;
      }
      if (__MODE__ === 'baseline') {
        await receivedValueNotResent();
        console.log('HARNESS_OK');
        process.exitCode = 0;
        return;
      }
      if (typeof __MODE__ === 'string' && __MODE__.indexOf('stale:') === 0) {
        await staleSwitchCopy(__MODE__.slice('stale:'.length));
        console.log('HARNESS_OK');
        process.exitCode = 0;
        return;
      }
      if (__MODE__ === 'gates') {
        await captureGates();
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

      // The 412 merge's own save (serverMerged) must adopt too. H's POST is
      // already in flight when G revokes, so the key is not protected by that
      // POST; it conflicts and merges the server's "allowed". Skipping the
      // adoption there would write "allowed" over G's revocation in
      // localStorage and report it to the backend.
      windows.length = 0;
      serverSettings = Object.assign({}, serverSettings, { proactiveVisionEnabled: true });
      serverRevision += 1;
      const g = makeWindow('G');
      const h = makeWindow('H');
      await settle();
      g.deliver();
      h.deliver();
      g.S.mergeMessagesEnabled = !g.S.mergeMessagesEnabled;
      g.mod.saveSettings();
      await settle();
      h.holdPosts = true;
      h.S.mergeMessagesEnabled = !h.S.mergeMessagesEnabled;
      h.mod.saveSettings();
      await settle();
      g.holdPosts = true;
      g.S.proactiveVisionEnabled = false;
      g.mod.saveSettings();
      await settle();
      h.consent.length = 0;
      h.releasePosts();
      await settle();
      assert(h.consent.length > 0, 'precondition: the 412 merge saved and reported');
      assert(h.consent.every((v) => v === false),
        'the 412 merge save must not re-grant consent: ' + JSON.stringify(h.consent));
      assert(stored() === false, 'the 412 merge save must not write "allowed" back');
      g.releasePosts();
      await settle();
      g.deliver();
      h.deliver();
      await settle();
      assert(g.S.proactiveVisionEnabled === false && h.S.proactiveVisionEnabled === false
        && serverSettings.proactiveVisionEnabled === false, 'G and H converge on revoked');

      serverSettings = Object.assign({}, serverSettings, { proactiveVisionEnabled: true });
      serverRevision += 1;
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

      // D has toggled the key itself before (it stays in the dirty set). When D
      // adopts C's next revocation, its snapshot must not claim that "false"
      // as D's own explicit write, now or on the following save.
      c.S.proactiveVisionEnabled = false;
      c.mod.saveSettings();
      await settle();
      // Read D's own snapshot right after each save (a later 412 merge may
      // replace it with a server-merge snapshot).
      const changedKeysOf = () => JSON.parse(store.get('project_neko_settings'))
        ._sharedWriteMeta.changedKeys;
      d.S.mergeMessagesEnabled = !d.S.mergeMessagesEnabled;
      d.mod.saveSettings();
      const firstKeys = changedKeysOf();
      await settle();
      assert(d.S.proactiveVisionEnabled === false, 'D adopts the revocation');
      assert(firstKeys.indexOf('proactiveVisionEnabled') === -1,
        'the adopted value is not an explicit write: ' + JSON.stringify(firstKeys));
      d.S.mergeMessagesEnabled = !d.S.mergeMessagesEnabled;
      d.mod.saveSettings();
      const nextKeys = changedKeysOf();
      await settle();
      assert(nextKeys.indexOf('proactiveVisionEnabled') === -1,
        'nor on the next save: ' + JSON.stringify(nextKeys));
      c.deliver();
      d.deliver();
      await settle();

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


def test_received_value_is_not_resent_as_own_write_harness():
    result = _run("baseline")
    assert result.returncode == 0, (
        "received-value baseline harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


def test_capture_gates_follow_cross_window_revocation_harness():
    result = _run("gates")
    assert result.returncode == 0, (
        "cross-window capture gate harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout


@pytest.mark.parametrize(
    "key",
    ["proactiveChatEnabled", "proactiveVisionChatEnabled", "proactiveVisionEnabled"],
)
def test_stale_switch_copy_does_not_stop_proactive_triggers_harness(key):
    result = _run("stale:" + key)
    assert result.returncode == 0, (
        f"stale {key} copy harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
