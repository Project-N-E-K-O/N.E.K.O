import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin
from tests.static_app_parts import read_js_parts

from tests.unit.avatar_ui_buttons_source import read_avatar_ui_buttons_source


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"


def _read_avatar_ui_buttons_source() -> str:
    return read_avatar_ui_buttons_source()


LIVE2D_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-ui-buttons.js"
MODEL_DISPLAY_PATH = PROJECT_ROOT / "static" / "app" / "app-ui" / "model-display.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def _run_node_harness(script: str):
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")
    return run_node_stdin(
        node_executable,
        script,
        capture_output=True,
        cwd=PROJECT_ROOT,
        timeout=10,
        check=False,
    )


def _source_slice_between(source, start_marker, end_marker, block_name):
    start = source.find(start_marker)
    assert start != -1, f"{block_name} start marker not found: {start_marker}"
    end = source.find(end_marker, start + len(start_marker))
    assert end != -1, f"{block_name} end marker not found after start: {end_marker}"
    assert start < end, f"{block_name} start marker must precede end marker"
    return source[start:end]


def test_avatar_floating_button_rows_keep_fixed_height_when_aux_controls_toggle():
    avatar_source = _read_avatar_ui_buttons_source()
    live2d_source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    wrapper_block = _source_slice_between(
        avatar_source,
        "const btnWrapper = document.createElement('div');",
        "const stopWrapperEvent = (e) => { e.stopPropagation(); };",
        "floating button wrapper styles",
    )

    # Live2D positions the toolbar from five 48px rows plus four 12px gaps.
    # The row wrapper must keep that height even when the voice-session quick
    # controls or popup trigger are shown, otherwise the vertical toolbar visibly contracts.
    assert "const LIVE2D_FLOATING_BUTTON_SIZE = 48;" in live2d_source
    assert "const LIVE2D_FLOATING_BUTTON_GAP = 12;" in live2d_source
    assert "const LIVE2D_FLOATING_BUTTON_COUNT = 5;" in live2d_source
    assert "const LIVE2D_BASE_TOOLBAR_HEIGHT =" in live2d_source
    assert "height: '48px'" in wrapper_block
    assert "minHeight: '48px'" in wrapper_block
    assert "flex: '0 0 48px'" in wrapper_block
    assert "boxSizing: 'border-box'" in wrapper_block

    quick_controls_rail_block = _source_slice_between(
        avatar_source,
        "const rail = document.createElement('div');",
        "btnWrapper.appendChild(rail);",
        "voice-session quick controls rail",
    )
    assert "position: 'absolute'" in quick_controls_rail_block
    assert "left: '-24px'" in quick_controls_rail_block
    assert "width: '22px'" in quick_controls_rail_block
    assert "height: '48px'" in quick_controls_rail_block
    assert "flexDirection: 'column'" in quick_controls_rail_block
    assert "gap: '4px'" in quick_controls_rail_block

    quick_control_slot_block = _source_slice_between(
        avatar_source,
        "const slot = document.createElement('div');",
        "slot.appendChild(button);",
        "voice-session quick control slot",
    )
    assert "width: '22px'" in quick_control_slot_block
    assert "height: '0'" in quick_control_slot_block
    assert "flex: '0 0 0px'" in quick_control_slot_block
    assert "overflow: 'hidden'" in quick_control_slot_block
    assert "pointerEvents: 'none'" in quick_control_slot_block

    mute_button_block = _source_slice_between(
        avatar_source,
        "Object.assign(muteBtn.style, {",
        "const stopMuteEvent = (e) => { e.stopPropagation(); };",
        "mic mute button styles",
    )
    assert "width: '22px'" in mute_button_block
    assert "height: '22px'" in mute_button_block
    assert "position: 'relative'" in mute_button_block
    assert "position: 'absolute'" not in mute_button_block


def test_live2d_lock_icon_tracks_the_floating_toolbar_scale():
    source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    lock_icon_block = _source_slice_between(
        source,
        "Live2DManager.prototype.setupHTMLLockIcon = function(model) {",
        "Live2DManager.prototype.setupFloatingButtons = function(model) {",
        "Live2D lock icon setup",
    )
    floating_buttons_block = source[source.find(
        "Live2DManager.prototype.setupFloatingButtons = function(model) {"
    ):]

    scale_call = "getLive2DFloatingControlScale(modelHeight, LIVE2D_BASE_TOOLBAR_HEIGHT)"
    assert scale_call in lock_icon_block
    assert scale_call in floating_buttons_block
    assert "lockIcon.style.transform = nextTransform;" in lock_icon_block
    assert "const actualLockIconSize = baseLockIconSize * scale;" in lock_icon_block
    assert "window.getNekoYuiGuideLockIconMaxTop(defaultMaxLockTop, actualLockIconSize)" in lock_icon_block
    assert "right: clampedLeft + actualLockIconSize" in lock_icon_block
    assert "bottom: clampedTop + actualLockIconSize" in lock_icon_block


def test_live2d_lock_icon_repair_detaches_an_orphaned_ticker():
    script = f"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync({json.dumps(str(LIVE2D_UI_BUTTONS_PATH))}, 'utf8');
const start = source.indexOf('Live2DManager.prototype.setupHTMLLockIcon = function(model) {{');
const end = source.indexOf('Live2DManager.prototype.setupFloatingButtons = function(model) {{', start);
const setupSource = source.slice(start, end);
const elements = new Map();
const makeElement = (tagName) => ({{
  tagName,
  dataset: {{}},
  style: {{}},
  appendChild() {{}},
  addEventListener() {{}},
  remove() {{ if (this.id) elements.delete(this.id); }},
}});
const canvas = makeElement('canvas');
const document = {{
  body: {{ appendChild(element) {{ elements.set(element.id, element); }} }},
  createElement: makeElement,
  getElementById(id) {{
    if (id === 'live2d-canvas') return canvas;
    if (id === 'chat-container') return {{}};
    return elements.get(id) || null;
  }},
  querySelectorAll() {{ return []; }},
}};
const removed = [];
const added = [];
const oldTicker = () => {{}};
function Live2DManager() {{}}
const window = {{ isViewerMode: false }};
vm.runInNewContext(setupSource, {{ Live2DManager, document, window, console, Date, Object }});

const manager = new Live2DManager();
manager.isLocked = false;
manager._lockIconTicker = oldTicker;
manager.pixi_app = {{ ticker: {{
  remove(ticker) {{ removed.push(ticker); }},
  add(ticker) {{ added.push(ticker); }},
}} }};
manager.setLocked = () => {{}};
manager.setupHTMLLockIcon({{ parent: true }});

if (removed.length !== 1 || removed[0] !== oldTicker) {{
  throw new Error('the orphaned Live2D lock ticker was not removed');
}}
if (added.length !== 1 || manager._lockIconTicker !== added[0] || added[0] === oldTicker) {{
  throw new Error('the replacement Live2D lock ticker was not installed exactly once');
}}
if (!document.getElementById('live2d-lock-icon')) {{
  throw new Error('the Live2D lock icon was not rebuilt');
}}
"""
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr or result.stdout


def test_model_display_rebuilds_a_missing_lock_icon_when_the_toolbar_survives():
    source = MODEL_DISPLAY_PATH.read_text(encoding="utf-8")
    for prefix in ("live2d", "vrm", "mmd", "pngtuber"):
        assert f"I.ensureActiveModelOverlayControls('{prefix}')" in source

    script = f"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync({json.dumps(str(MODEL_DISPLAY_PATH))}, 'utf8');
const elements = new Map([
  ['live2d-floating-buttons', {{ id: 'live2d-floating-buttons' }}],
  ['vrm-floating-buttons', {{ id: 'vrm-floating-buttons' }}],
  ['mmd-floating-buttons', {{ id: 'mmd-floating-buttons' }}],
  ['pngtuber-floating-buttons', {{ id: 'pngtuber-floating-buttons' }}],
]);
const calls = {{ live2dToolbar: 0, live2dLock: 0, vrm: 0, mmd: 0, pngToolbar: 0, pngLock: 0 }};
const document = {{
  getElementById(id) {{ return elements.get(id) || null; }},
}};
const window = {{
  appUi: {{}},
  __appUiParts: {{ mod: {{}} }},
  live2dManager: {{
    getCurrentModel() {{ return {{ destroyed: false }}; }},
    setupFloatingButtons() {{ calls.live2dToolbar += 1; }},
    setupHTMLLockIcon() {{
      calls.live2dLock += 1;
      elements.set('live2d-lock-icon', {{ id: 'live2d-lock-icon' }});
    }},
  }},
  vrmManager: {{
    currentModel: {{ destroyed: false }},
    setupFloatingButtons() {{
      calls.vrm += 1;
      elements.set('vrm-lock-icon', {{ id: 'vrm-lock-icon' }});
    }},
  }},
  mmdManager: {{
    currentModel: {{ destroyed: false }},
    setupFloatingButtons() {{
      calls.mmd += 1;
      elements.set('mmd-lock-icon', {{ id: 'mmd-lock-icon' }});
    }},
  }},
  pngtuberManager: {{
    image: {{ destroyed: false }},
    setupFloatingButtons() {{ calls.pngToolbar += 1; }},
    setupHTMLLockIcon() {{
      calls.pngLock += 1;
      elements.set('pngtuber-lock-icon', {{ id: 'pngtuber-lock-icon' }});
    }},
  }},
}};
window.window = window;
window.document = document;
vm.runInNewContext(source, {{ window, document, console, Object, String }});

for (const prefix of ['live2d', 'vrm', 'mmd', 'pngtuber']) {{
  const controls = window.__appUiParts.ensureActiveModelOverlayControls(prefix);
  if (!controls.floatingButtons || !controls.lockIcon) {{
    throw new Error(prefix + ' overlay controls were not repaired');
  }}
}}
for (const prefix of ['live2d', 'vrm', 'mmd', 'pngtuber']) {{
  const controls = window.__appUiParts.ensureActiveModelOverlayControls(prefix);
  if (!controls.floatingButtons || !controls.lockIcon) {{
    throw new Error(prefix + ' repaired overlay controls were not retained');
  }}
}}
if (calls.live2dToolbar !== 0 || calls.pngToolbar !== 0) {{
  throw new Error('an intact toolbar should not be rebuilt');
}}
if (calls.live2dLock !== 1 || calls.vrm !== 1 || calls.mmd !== 1 || calls.pngLock !== 1) {{
  throw new Error('repeated checks must not accumulate overlay rebuilds: ' + JSON.stringify(calls));
}}
"""
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr or result.stdout


def test_model_lock_icons_ignore_pointer_input_while_avatar_overlays_overlap():
    renderer_contracts = [
        (
            "static/live2d/live2d-ui-buttons.js",
            "lockIcon.style.pointerEvents = nextPointerEvents;",
        ),
        (
            "static/vrm/vrm-ui-buttons.js",
            "lockIcon.style.pointerEvents = isLockOverlapped ? 'none' : 'auto';",
        ),
        (
            "static/mmd/mmd-ui-buttons.js",
            "lockIcon.style.pointerEvents = isLockOverlapped ? 'none' : 'auto';",
        ),
        (
            "static/pngtuber-core.js",
            "lockIcon.style.pointerEvents = isOverlapped ? 'none' : 'auto';",
        ),
    ]

    for relative_path, pointer_guard in renderer_contracts:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert pointer_guard in source

    live2d_source = (PROJECT_ROOT / renderer_contracts[0][0]).read_text(encoding="utf-8")
    assert "const nextPointerEvents = isPointerOverlapped ? 'none' : 'auto';" in live2d_source
    assert "this._lockIconLastPointerEvents = undefined;" in live2d_source
    assert live2d_source.index("const isPointerOverlapped =") < live2d_source.index(
        "if (!this._lockIconLastOverlapScanAt"
    )

    popup_common_source = (
        PROJECT_ROOT / "static/avatar/avatar-popup-common.js"
    ).read_text(encoding="utf-8")
    assert "isRectOverlappedByVisibleOverlay" in popup_common_source
    assert '`[id^="${ownerPrefix}-popup-"]`' in popup_common_source
    assert '`[data-neko-sidepanel-owner^="${ownerPrefix}-popup-"]`' in popup_common_source

    for renderer, prefix in (("vrm", "vrm"), ("mmd", "mmd")):
        source = (
            PROJECT_ROOT / f"static/{renderer}/{renderer}-ui-buttons.js"
        ).read_text(encoding="utf-8")
        layout_start = source.index("const visibleCount = getVisibleButtonCount();")
        mobile_branch = source.index("if (isMobile) {", layout_start)
        common_visibility_guard = source.index(
            "const isLockVisible = !this._isInReturnState", mobile_branch
        )
        shared_guard = source.index(
            f"popupUi.isRectOverlappedByVisibleOverlay(lockRect, '{prefix}')",
            common_visibility_guard,
        )
        assert common_visibility_guard > source.index("} else {", mobile_branch)
        assert shared_guard < source.index(
            "buttonsContainer.style.transform = `scale(${scale})`;", shared_guard
        )
        assert f'[data-neko-sidepanel-owner^="{prefix}-popup-"]' in source

    pngtuber_source = (PROJECT_ROOT / "static/pngtuber-core.js").read_text(
        encoding="utf-8"
    )
    popup_source = (
        PROJECT_ROOT / "static/avatar/avatar-ui-popup.js"
    ).read_text(encoding="utf-8")
    overlay_event = "neko-avatar-overlay-visibility-changed"
    assert f"window.addEventListener('{overlay_event}'" in pngtuber_source
    assert "this.updateLockIconPosition();" in pngtuber_source
    assert f"new CustomEvent('{overlay_event}'" in popup_source
    assert "dispatchAvatarSidePanelVisibilityChanged(container);" in popup_source
    assert popup_source.count(
        "scheduleAvatarSidePanelVisibilitySettled(container, visibilityRevision);"
    ) == 2
    assert popup_source.count(
        "container.style.display = 'none';\n"
        "                dispatchAvatarSidePanelVisibilityChanged(container);"
    ) == 2
    assert "if (panel._visibilitySettledTimer)" in popup_source
    interval_control = popup_source[popup_source.index("function createIntervalControl") :]
    interval_collapse = interval_control[interval_control.index("container._collapse = () => {") :]
    assert interval_collapse.index("container.style.pointerEvents = 'none';") < (
        interval_collapse.index("container.style.opacity = '0';")
    )


def test_shared_avatar_overlay_overlap_detects_owned_sidepanels_by_geometry():
    source = (PROJECT_ROOT / "static/avatar/avatar-popup-common.js").read_text(
        encoding="utf-8"
    )
    script = rf"""
const assert = require('node:assert/strict');
const source = {json.dumps(source)};
const lockRect = {{ left: 10, top: 10, right: 42, bottom: 42 }};
const vrmPopup = {{
  style: {{ display: 'flex', visibility: 'visible', opacity: '0' }},
  computedStyle: {{ display: 'flex', visibility: 'visible', opacity: '0' }},
  getBoundingClientRect: () => ({{ left: 0, top: 0, right: 50, bottom: 50, width: 50, height: 50 }})
}};
const vrmSidePanel = {{
  style: {{ display: 'flex', visibility: 'visible', opacity: '1' }},
  computedStyle: {{ display: 'flex', visibility: 'visible', opacity: '1' }},
  getBoundingClientRect: () => ({{ left: 30, top: 30, right: 80, bottom: 80, width: 50, height: 50 }})
}};
const overlaysByPrefix = {{ vrm: [vrmPopup, vrmSidePanel], mmd: [] }};
const queriedSelectors = [];
global.document = {{
  querySelectorAll(selector) {{
    queriedSelectors.push(selector);
    if (selector.includes('vrm-popup-')) return overlaysByPrefix.vrm;
    if (selector.includes('mmd-popup-')) return overlaysByPrefix.mmd;
    return [];
  }},
  getElementById() {{ return null; }},
  querySelector() {{ return null; }}
}};
global.window = {{
  getComputedStyle(element) {{ return element.computedStyle || element.style; }},
  innerWidth: 1920,
  innerHeight: 1080
}};
eval(source);
assert.equal(window.AvatarPopupUI.isRectOverlappedByVisibleOverlay(lockRect, 'vrm'), true);
assert.equal(window.AvatarPopupUI.isRectOverlappedByVisibleOverlay(lockRect, 'mmd'), false);
assert.match(queriedSelectors[0], /\[id\^="vrm-popup-"\]/);
assert.match(queriedSelectors[0], /data-neko-sidepanel-owner\^="vrm-popup-"/);
vrmSidePanel.style.opacity = '0';
vrmSidePanel.computedStyle.opacity = '1';
assert.equal(window.AvatarPopupUI.isRectOverlappedByVisibleOverlay(lockRect, 'vrm'), true);
vrmSidePanel.computedStyle.opacity = '0';
assert.equal(window.AvatarPopupUI.isRectOverlappedByVisibleOverlay(lockRect, 'vrm'), false);
"""
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr or result.stdout


def test_interpage_restore_keeps_floating_button_containers_in_flex_layout():
    source = read_js_parts(APP_INTERPAGE_PATH)
    restore_block = _source_slice_between(
        source,
        "restoringFloatingEls.forEach(function (el) {",
        "delete el.dataset.nekoPreHideDisplay;",
        "interpage floating button restore block",
    )

    assert "var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));" in restore_block
    assert "el.style.display = isFloatingButtons ? 'flex' : restoreDisplay;" in restore_block
    assert "el.style.display = restoreDisplay;" not in restore_block


def test_interpage_hide_records_css_fallback_floating_button_display_as_flex():
    source = read_js_parts(APP_INTERPAGE_PATH)
    hide_block = _source_slice_between(
        source,
        "document.querySelectorAll(\n                '#live2d-floating-buttons",
        "el.style.display = 'none';",
        "interpage floating button hide snapshot block",
    )

    assert "var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));" in hide_block
    assert "isFloatingButtons && !el.style.display && computedDisplay === 'none'" in hide_block
    assert "? 'flex'" in hide_block


def test_css_fallback_keeps_visible_floating_button_containers_as_flex():
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    fallback_block = _source_slice_between(
        css_source,
        "#live2d-floating-buttons,",
        "body.neko-game-active #live2d-container,",
        "floating button display fallback css",
    )

    assert "#vrm-floating-buttons," in fallback_block
    assert "#mmd-floating-buttons," in fallback_block
    assert "#pngtuber-floating-buttons" in fallback_block
    assert "display: flex;" in fallback_block
    assert "flex-direction: column;" in fallback_block
    assert "gap: 12px;" in fallback_block
