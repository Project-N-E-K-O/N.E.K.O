"""Regression tests for visit_probe parent.js (F0567) and measure_bridge.mjs (F0570)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_JS = REPO_ROOT / "scripts" / "visit_probe" / "parent.js"
MEASURE_BRIDGE_MJS = REPO_ROOT / "scripts" / "visit_probe" / "measure_bridge.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node executable not available")


def _run_node(script: str) -> dict:
    proc = run_node_script(shutil.which("node") or "node", script, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_visit_probe_parent_hook_uses_inter_frame_interval_on_cold_start():
    script = f"""
    const fs = require('fs');
    const vm = require('vm');
    let nowMs = 0;
    let postRenderFn = null;
    const sink = {{ onFrame: () => ({{ blank: false }}) }};
    const app = {{
      stage: {{}},
      renderer: {{
        renderTexture: null,
        lastObjectRendered: null,
        gl: null,
        on: (ev, fn) => {{ if (ev === 'postrender') postRenderFn = fn; }},
        off: (ev, fn) => {{ if (ev === 'postrender' && postRenderFn === fn) postRenderFn = null; }},
      }},
      view: {{ width: 800, height: 600 }},
      ticker: {{ add: () => {{}}, remove: () => {{}} }},
    }};
    app.renderer.lastObjectRendered = app.stage;
    const lm = {{ pixi_app: app, _idleTickMode: false, _idleTickFps: 0 }};
    const ctx = {{
      window: {{
        live2dManager: lm,
        nekoFramePacing: {{ getDisplayRefreshHz: () => 60 }},
      }},
      document: {{ createElement: () => ({{}}), body: {{ appendChild: () => {{}} }} }},
      performance: {{ now: () => nowMs }},
      screen: {{ width: 1920, height: 1080 }},
      location: {{ href: 'http://127.0.0.1:48911/' }},
      setTimeout,
      clearTimeout,
      console,
    }};
    ctx.window.window = ctx.window;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync({json.dumps(str(PARENT_JS))}, 'utf8'), ctx);
    const P = ctx.window.__visitProbe;
    P.frames.probe = {{ contentWindow: {{ __nekoVisitFrameSink: sink }} }};

    // 60 Hz cold start for 1000 ms (60 frames): must capture ~30 frames, not 50-60.
    P.hook('probe');
    for (let i = 0; i < 60; i++) {{
      nowMs = i * (1000 / 60);
      postRenderFn();
    }}
    const caps60 = P.stat.captures;

    // 144 Hz cold start for 1000 ms (144 frames): must still capture ~30 frames.
    ctx.window.nekoFramePacing.getDisplayRefreshHz = () => 144;
    P.hook('probe');
    for (let i = 0; i < 144; i++) {{
      nowMs = 2000 + i * (1000 / 144);
      postRenderFn();
    }}
    const caps144 = P.stat.captures;

    // Timer 30 Hz idle-tick mode: initialHz resolves from lm._idleTickFps=30, so frame 1 captures immediately.
    lm._idleTickMode = true;
    lm._idleTickFps = 30;
    P.hook('probe');
    nowMs = 5000;
    postRenderFn();
    const firstFrameCap30 = P.stat.captures;
    const firstRenderFps30 = P.stat.renderFpsLast;

    console.log(JSON.stringify({{ version: P.version, caps60, caps144, firstFrameCap30, firstRenderFps30 }}));
    """
    out = _run_node(script)
    assert out["version"] == 18
    assert 29 <= out["caps60"] <= 31
    assert 29 <= out["caps144"] <= 31
    assert out["firstFrameCap30"] == 1
    assert out["firstRenderFps30"] == 30


def test_measure_bridge_tracks_rvfc_frames_and_removes_hidden_video():
    src = MEASURE_BRIDGE_MJS.read_text(encoding="utf-8")
    assert "track.frames" not in src
    assert "requestVideoFrameCallback" in src
    assert "trackAvgFps" in src

    syntax_check = (
        "const { execFileSync } = require('node:child_process');\n"
        f"execFileSync(process.execPath, ['--check', {json.dumps(str(MEASURE_BRIDGE_MJS))}], {{ encoding: 'utf8' }});\n"
    )
    proc = run_node_script(shutil.which("node") or "node", syntax_check, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout

    # Exercise the actual evaluate() body from measure_bridge.mjs against mocked browser/bridge objects.
    eval_start = src.index("const out = await evaluate(`") + len("const out = await evaluate(`")
    eval_end = src.index("`);\nconst s = out.samples;")
    eval_template = src[eval_start:eval_end]
    eval_body = eval_template.replace("${process.argv[4] === 'check' ? 'true' : 'false'}", "false")
    summary_tail = src[src.index("const s = out.samples;"):src.index("console.log(JSON.stringify(out")]

    script = f"""
    const assert = require('assert');
    let rvfcCallbacks = [];
    let presentedFrames = 100;
    let removed = false;
    let paused = false;
    const packCanvas = {{
      getContext: () => ({{
        getImageData: () => ({{ data: new Uint8ClampedArray([1, 2, 3, 4]) }})
      }})
    }};
    const track = {{ id: 'v1', readyState: 'live' }};
    const fakeVideo = {{
      muted: false, playsInline: false, autoplay: false, style: {{ cssText: '' }}, srcObject: null,
      requestVideoFrameCallback: (cb) => {{ rvfcCallbacks.push(cb); return rvfcCallbacks.length; }},
      play: async () => {{}},
      pause: () => {{ paused = true; }},
      remove: () => {{ removed = true; }},
    }};
    const ctl = {{
      link: {{ start: () => {{}} }},
      media: {{ publish: false }},
      announcePublish: () => {{}},
      packer: {{
        active: false, frames: 1, requestFrames: 0, packMsTotal: 0,
        dims: {{ cropW: 1, cropH: 1 }}, pack: packCanvas,
        onFrame: () => true,
        ensureTrack: () => track,
      }},
    }};
    const cw = {{
      NekoVisitTransport: {{ controller: ctl }},
      MediaStream: function(tracks) {{ this.tracks = tracks; }},
      document: {{
        createElement: (tag) => {{ assert.strictEqual(tag, 'video'); return fakeVideo; }},
        body: {{ appendChild: () => {{}} }},
      }},
    }};
    const frame = {{
      contentWindow: cw,
      style: {{ cssText: '' }},
      addEventListener: (ev, cb) => {{ if (ev === 'load') setTimeout(cb, 0); }},
    }};
    const bridge = {{
      _state: {{ frame, captureFps: 30, rect: [0, 0, 10, 10], cropMode: 'upper', savedFps: 60, hiddenSent: false }},
      onStateChange: () => {{}},
    }};
    global.window = {{
      NekoVisitParentBridge: {{ createParentBridge: () => bridge }},
      targetFrameRate: 60,
      live2dManager: {{ _visitCaptureActive: false }},
    }};
    global.document = {{ getElementById: () => null }};
    global.location = {{ href: 'http://127.0.0.1:48911/' }};
    global.devicePixelRatio = 1;
    global.innerWidth = 800;
    global.innerHeight = 600;
    const sleep = (ms) => new Promise((r) => setTimeout(r, 1));
    (async () => {{
      const evaluate = async (_expr) => {{
        const fn = new Function('setTimeout', 'performance', 'document', 'window', 'location', 'devicePixelRatio', 'innerWidth', 'innerHeight', 'return ' + {json.dumps(eval_body)});
        const promise = fn((cb, ms) => {{
          global.setTimeout(() => {{
            if (ms === 300) {{
              const pending = rvfcCallbacks.splice(0);
              for (const rcb of pending) rcb(0, {{ presentedFrames }});
            }} else if (ms === 1000) {{
              ctl.packer.requestFrames += 30;
              ctl.packer.packMsTotal += 15;
              presentedFrames += 30;
              const pending = rvfcCallbacks.splice(0);
              for (const rcb of pending) rcb(0, {{ presentedFrames }});
            }}
            cb();
          }}, 0);
        }}, {{ now: (() => {{ let t = 0; return () => (t += 1000); }})() }}, global.document, global.window, global.location, 1, 800, 600);
        return await promise;
      }};
      const label = 'test-run';
      const out = await evaluate();
      {summary_tail}
      assert.strictEqual(paused, true);
      assert.strictEqual(removed, true);
      assert.strictEqual(fakeVideo.srcObject, null);
      assert.ok(out.samples.every((row) => typeof row[3] === 'number'));
      assert.ok(out.summary.trackAvgFps > 0);
      console.log(JSON.stringify({{ ok: true, trackAvgFps: out.summary.trackAvgFps }}));
    }})().catch((e) => {{ console.error(e); process.exit(1); }});
    """
    out = _run_node(script)
    assert out["ok"] is True
    assert out["trackAvgFps"] == 30
