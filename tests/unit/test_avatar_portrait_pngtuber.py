import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_PORTRAIT_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-portrait.js"


@pytest.mark.unit
def test_avatar_portrait_captures_pngtuber_layered_snapshot():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    source = AVATAR_PORTRAIT_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

class FakeCanvas {{
  constructor(width = 1, height = 1, hasVisiblePixels = true) {{
    this.tagName = 'CANVAS';
    this.width = width;
    this.height = height;
    this.clientWidth = width;
    this.clientHeight = height;
    this.hidden = false;
    this.style = {{ display: '' }};
    this.classList = {{ contains: () => false }};
    this.drawCalls = [];
    this.hasVisiblePixels = hasVisiblePixels;
  }}
  getContext() {{
    return {{
      save() {{}}, restore() {{}}, beginPath() {{}}, rect() {{}}, clip() {{}},
      arc() {{}}, arcTo() {{}}, moveTo() {{}}, closePath() {{}}, fillRect() {{}},
      drawImage: (source, ...args) => {{
        this.drawCalls.push([source, ...args]);
        this.hasVisiblePixels = source?.hasVisiblePixels !== false;
      }},
      getImageData: () => ({{
        data: new Proxy({{}}, {{
          get: (_target, key) => {{
            const index = Number(key);
            if (!Number.isInteger(index)) return undefined;
            return this.hasVisiblePixels && index % 4 === 3 ? 255 : 0;
          }},
        }}),
      }}),
    }};
  }}
  getBoundingClientRect() {{ return {{ width: this.width, height: this.height }}; }}
  toDataURL() {{ return 'data:image/png;base64,AAAA'; }}
}}

const runtimeCanvas = new FakeCanvas(512, 512);
runtimeCanvas.hidden = true;
runtimeCanvas.style.display = 'none';
const layeredSnapshot = new FakeCanvas(1200, 1800);
const document = {{
  createElement: (tag) => tag === 'canvas' ? new FakeCanvas() : null,
  getElementById: () => null,
}};
const window = {{
  document,
  location: {{ href: 'http://127.0.0.1:48911/', origin: 'http://127.0.0.1:48911' }},
  lanlan_config: {{ model_type: 'pngtuber' }},
  requestAnimationFrame: () => {{ throw new Error('capture must not depend on animation frames'); }},
  getComputedStyle: (drawable) => ({{
    display: drawable.style.display || '',
    visibility: drawable.hidden ? 'hidden' : 'visible',
  }}),
  pngtuberManager: {{
    image: runtimeCanvas,
    ensureContainer() {{}},
    renderLayeredSnapshotCanvas: () => layeredSnapshot,
  }},
}};
const context = {{ window, document, console, URL, Promise, Array, setTimeout, clearTimeout }};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'avatar-portrait.js' }});

(async () => {{
  assert.equal(window.avatarPortrait.normalizeModelType(), 'pngtuber');
  const result = await window.avatarPortrait.capture({{
    modelType: 'pngtuber', width: 768, height: 1024, includeDataUrl: true,
  }});
  assert.equal(result.modelType, 'pngtuber');
  assert.equal(result.canvas.width, 768);
  assert.equal(result.canvas.height, 1024);
  assert.equal(result.sourceCanvas.width, 1200);
  assert.equal(result.sourceCanvas.height, 1800);
  assert.equal(result.dataUrl, 'data:image/png;base64,AAAA');

  const highResolutionImage = {{
    tagName: 'IMG', width: 320, height: 480, clientWidth: 320, clientHeight: 480,
    naturalWidth: 1600, naturalHeight: 2400, complete: true, hidden: false,
    style: {{ display: '' }}, classList: {{ contains: () => false }},
    currentSrc: '/user_pngtuber/high-resolution.png',
  }};
  window.pngtuberManager = {{ image: highResolutionImage, ensureContainer() {{}} }};
  const intrinsicResult = await window.avatarPortrait.capture({{
    modelType: 'pngtuber', width: 768, height: 1024,
  }});
  assert.equal(intrinsicResult.sourceCanvas.width, 1600);
  assert.equal(intrinsicResult.sourceCanvas.height, 2400);

  const loadingImage = {{
    tagName: 'IMG', width: 0, height: 0, clientWidth: 0, clientHeight: 0,
    naturalWidth: 0, naturalHeight: 0, complete: false, hidden: false,
    style: {{ display: '' }}, classList: {{ contains: () => false }},
    currentSrc: '/user_pngtuber/loading.png',
    addEventListener(type, callback) {{
      if (type !== 'load') return;
      setTimeout(() => {{
        this.complete = true;
        this.naturalWidth = 900;
        this.naturalHeight = 1200;
        callback();
      }}, 0);
    }},
    removeEventListener() {{}},
  }};
  window.pngtuberManager = {{ image: loadingImage, ensureContainer() {{}} }};
  const loadedResult = await window.avatarPortrait.capture({{ modelType: 'pngtuber' }});
  assert.equal(loadedResult.sourceCanvas.width, 900);
  assert.equal(loadedResult.sourceCanvas.height, 1200);

  window.pngtuberManager = {{
    image: runtimeCanvas,
    ensureContainer() {{}},
    renderLayeredSnapshotCanvas: () => new FakeCanvas(1200, 1800, false),
  }};
  await assert.rejects(
    () => window.avatarPortrait.capture({{ modelType: 'pngtuber' }}),
    /PNGTuber 画面尚未就绪/,
  );

  const brokenImage = {{
    tagName: 'IMG', width: 512, height: 512, clientWidth: 512, clientHeight: 512,
    naturalWidth: 0, naturalHeight: 0, complete: true, hidden: false,
    style: {{ display: '' }}, classList: {{ contains: () => false }},
  }};
  window.pngtuberManager = {{ image: brokenImage, ensureContainer() {{}} }};
  await assert.rejects(
    () => window.avatarPortrait.capture({{ modelType: 'pngtuber' }}),
    /PNGTuber 图片加载失败/,
  );
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""

    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)
