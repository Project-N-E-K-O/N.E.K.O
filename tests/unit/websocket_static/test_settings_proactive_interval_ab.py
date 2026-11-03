import json
import textwrap
from pathlib import Path

import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract


def test_proactive_interval_40s_branch_overrides_only_untouched_first_launch_default():
    # The proactive_interval_40s A/B arm changes the first-launch default from
    # the control 15s to 40s once the boot GET resolves the branch. It must not
    # touch the control branch, a server-persisted interval, an interval the
    # user changed while the GET was in flight, or upgrade installs that never
    # went through the first-launch path.
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        function makeContext(initialStorage) {
          const store = new Map(Object.entries(initialStorage || {}));
          const postCalls = [];
          const getCalls = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout() { return { unref() {} }; },
            clearTimeout() {},
            localStorage: {
              getItem(k) { return store.has(k) ? store.get(k) : null; },
              setItem(k, v) { store.set(k, String(v)); },
              removeItem(k) { store.delete(k); },
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
            appState: { proactiveChatInterval: 15, settingsHydrated: false },
            appConst: { DEFAULT_PROACTIVE_CHAT_INTERVAL: 15 },
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          return {
            store, postCalls, getCalls,
            S: sandbox.window.appState,
            win: sandbox.window,
            mod: sandbox.window.appSettings,
          };
        }

        const tick = () => new Promise((resolve) => setImmediate(resolve));

        async function resolveBoot(ctx, settings, telemetryBranch) {
          assert(ctx.getCalls.length === 1, 'boot must issue the settings GET');
          ctx.getCalls[0].resolve({
            ok: true,
            json: async () => ({ success: true, settings, telemetryBranch }),
          });
          await tick();
          await tick();
        }

        function lastPostedInterval(ctx) {
          assert(ctx.postCalls.length > 0, 'the branch resolution must push settings');
          return JSON.parse(ctx.postCalls[ctx.postCalls.length - 1].body).proactiveChatInterval;
        }

        async function main() {
          // Experiment arm, true first launch: 15 -> 40, persisted and pushed.
          const exp = makeContext();
          assert(exp.store.get('_neko_first_launch_branch_pending') === '1', 'first launch sets the marker');
          await resolveBoot(exp, null, 'proactive_interval_40s');
          assert(exp.S.proactiveChatInterval === 40, 'experiment arm defaults to 40s, got ' + exp.S.proactiveChatInterval);
          assert(exp.win.proactiveChatInterval === 40, 'the window mirror follows the override');
          assert(lastPostedInterval(exp) === 40, 'the override reaches the server');
          assert(!exp.store.has('_neko_first_launch_branch_pending'), 'branch resolution clears the marker');

          // Control arm keeps the 15s default.
          const ctl = makeContext();
          await resolveBoot(ctl, null, 'main');
          assert(ctl.S.proactiveChatInterval === 15, 'control arm keeps 15s');

          // A server-persisted interval wins over the experiment default.
          const srv = makeContext();
          await resolveBoot(srv, { proactiveChatInterval: 20 }, 'proactive_interval_40s');
          assert(srv.S.proactiveChatInterval === 20, 'server preference wins, got ' + srv.S.proactiveChatInterval);

          // A user change made while the GET is in flight wins.
          const usr = makeContext();
          usr.win.proactiveChatInterval = 25;
          usr.mod.saveSettings();
          await resolveBoot(usr, null, 'proactive_interval_40s');
          assert(usr.S.proactiveChatInterval === 25, 'user change wins, got ' + usr.S.proactiveChatInterval);

          // A server-persisted 15s is an explicit preference, not the default.
          const srv15 = makeContext();
          await resolveBoot(srv15, { proactiveChatInterval: 15 }, 'proactive_interval_40s');
          assert(srv15.S.proactiveChatInterval === 15, 'server 15s wins, got ' + srv15.S.proactiveChatInterval);

          // A user who moved the slider away and back to 15s during the GET keeps 15s.
          const back = makeContext();
          back.win.proactiveChatInterval = 25;
          back.mod.saveSettings();
          back.win.proactiveChatInterval = 15;
          back.mod.saveSettings();
          await resolveBoot(back, null, 'proactive_interval_40s');
          assert(back.S.proactiveChatInterval === 15, 'user-chosen 15s wins, got ' + back.S.proactiveChatInterval);

          // Upgrade install (saved settings, no first-launch marker) is untouched.
          const upg = makeContext({
            project_neko_settings: JSON.stringify({ proactiveChatEnabled: true, proactiveChatInterval: 15 }),
          });
          await resolveBoot(upg, null, 'proactive_interval_40s');
          assert(upg.S.proactiveChatInterval === 15, 'upgrade install keeps its interval');

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
        "proactive interval A/B harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
