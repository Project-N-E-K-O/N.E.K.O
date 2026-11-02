"""Standalone visit persona window: the panel opens only after i18n is ready."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PERSONA_PANEL = PROJECT_ROOT / "static" / "app" / "visit-persona-panel.js"


def _run(body: str) -> dict:
    node_path = shutil.which("node")
    if not node_path:
        raise AssertionError("node is required to run the visit persona window harness")
    script = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync(PANEL_PATH, 'utf8');

        function makeWindow(opts) {
          const listeners = new Map();
          const timers = [];
          const opened = [];
          const win = {
            __NEKO_VISIT_PERSONA_STANDALONE__: true,
            location: { search: '?catgirl=Neko&request_id=r1', origin: 'http://127.0.0.1:48911' },
            addEventListener(type, fn) {
              if (!listeners.has(type)) listeners.set(type, []);
              listeners.get(type).push(fn);
            },
            removeEventListener(type, fn) {
              listeners.set(type, (listeners.get(type) || []).filter((f) => f !== fn));
            },
            dispatch(type) { (listeners.get(type) || []).slice().forEach((fn) => fn({ type })); },
            listenerCount(type) { return (listeners.get(type) || []).length; },
            setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
            runTimers() { timers.splice(0).forEach((t) => t.fn()); },
            timers,
            opened,
          };
          if (opts.tReady) win.t = (key) => key;
          // The panel's first DOM write marks it as opened; stop right there.
          win.document = {
            readyState: 'complete',
            addEventListener() {},
            createElement(tag) { opened.push(typeof win.t === 'function'); throw new Error('panel-opened'); },
          };
          win.URLSearchParams = URLSearchParams;
          win.window = win;
          return win;
        }

        function load(opts) {
          const win = makeWindow(opts);
          const ctx = vm.createContext(win);
          safely(() => vm.runInContext(source, ctx));
          return win;
        }

        function safely(fn) { try { fn(); } catch (e) { if (String(e && e.message) !== 'panel-opened') throw e; } }

        (async () => {
          const report = {};
          BODY
          process.stdout.write(JSON.stringify(report));
        })().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
        """
    ).replace("PANEL_PATH", json.dumps(str(PERSONA_PANEL))).replace("BODY", body)
    result = run_node_script(
        node_path, script, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_standalone_persona_window_waits_for_i18n_before_opening_the_panel():
    report = _run(
        """
          {
            // i18n still loading: nothing opens until localechange, and the timeout fallback is then a no-op
            const win = load({ tReady: false });
            report.before = win.opened.length;
            report.timeoutMs = win.timers.map((t) => t.ms);
            win.t = (key) => key;
            safely(() => win.dispatch('localechange'));
            report.afterLocale = win.opened.slice();
            report.listenersLeft = win.listenerCount('localechange');
            safely(() => win.runTimers());
            report.afterTimeout = win.opened.length;
          }
          {
            // i18n never settles: the timeout still opens the panel exactly once
            const win = load({ tReady: false });
            safely(() => win.runTimers());
            report.stuck = win.opened.slice();
          }
          {
            // i18n already ready (cached): open straight away, no waiting
            const win = load({ tReady: true });
            report.ready = win.opened.slice();
            report.readyTimers = win.timers.length;
          }
        """
    )
    assert report["before"] == 0
    assert report["timeoutMs"] == [2000]
    assert report["afterLocale"] == [True]
    assert report["listenersLeft"] == 0
    assert report["afterTimeout"] == 1
    assert report["stuck"] == [False]
    assert report["ready"] == [True]
    assert report["readyTimers"] == 0
