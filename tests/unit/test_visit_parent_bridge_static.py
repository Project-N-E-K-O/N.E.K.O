"""Static gates of the visit parent bridge (design section 5 PR-11 and the general static gates 1 and 5)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VISIT = ROOT / "static" / "visit"
BRIDGE = VISIT / "parent-bridge.js"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)//[^\n]*$", "", src)


def test_parent_side_has_no_frame_loop_socket_or_visibility_listener():
    for path in VISIT.glob("*.js"):
        code = _code(path.read_text(encoding="utf-8"))
        assert "requestAnimationFrame(" not in code, path.name
        assert "new WebSocket(" not in code, path.name
    for path in VISIT.rglob("*.js"):
        assert "visibilitychange" not in path.read_text(encoding="utf-8"), path.name


def test_postrender_guards_and_fps_save_restore():
    code = _code(BRIDGE.read_text(encoding="utf-8"))
    assert "renderer.on('postrender', onPostrender)" in code
    assert "renderer.lastObjectRendered !== app.stage" in code
    assert "renderer.renderTexture && renderer.renderTexture.current" in code
    assert "state.savedFps = target;" in code and "applyFrameRate(saved)" in code
    assert "dispatch('neko-frame-rate-changed', { fps: fps, source: 'visit' })" in code
    assert "win.targetFrameRate" in code
    settings = _read("static/app/app-settings.js")
    assert "window.targetFrameRate === targetFrameRateHold.heldFps" in settings
    assert "? targetFrameRateHold.userFps" in settings
    assert "requestPacedFrame(" in code


def test_host_frame_never_takes_pointer_events_and_sits_below_the_model():
    code = _code(BRIDGE.read_text(encoding="utf-8"))
    m = re.search(r"var HOST_FRAME_STYLE = ('[^']*'(?:\s*\+\s*'[^']*')*);", code)
    assert m
    style = "".join(re.findall(r"'([^']*)'", m.group(1))).replace(" ", "")
    assert "pointer-events:none" in style
    z = int(re.search(r"z-index:(\d+)", style).group(1))
    assert z < 10
    css = _read("static/css/index.css")
    rule = re.search(r"#visit-frame\s*\{([^}]*)\}", css).group(1).replace(" ", "")
    assert "pointer-events:none" in rule and int(re.search(r"z-index:(\d+)", rule).group(1)) < 10
    live2d_z = int(re.search(r"#live2d-container\s*\{[^}]*?z-index:\s*(\d+)", css).group(1))
    assert z < live2d_z
    # Non-Pet mobile layouts lower the model layer to 5; the visitor layer must remain below it.
    mobile_z = min(int(v) for v in re.findall(
        r"#live2d-container\s*\{[^}]*?z-index:\s*(\d+)", css))
    assert z < mobile_z and int(re.search(r"z-index:(\d+)", rule).group(1)) < mobile_z
    assert "f.className = 'transparent-overlay'" in code
    # .minimized controls opacity when the user minimizes the model.
    for cid in ("live2d-container", "vrm-container", "mmd-container", "pngtuber-container"):
        assert f"#{cid}.visiting-away:not(.minimized)" in css


def test_one_line_hooks_in_the_renderers():
    assert _read("static/live2d/live2d-core.js").count("_visitCaptureActive") == 1
    assert "if (this._visitCaptureActive) return true;" in _read("static/live2d/live2d-core.js")
    assert _read("static/vrm/vrm-manager.js").count("onExternalRender") == 1
    assert _read("static/mmd/mmd-core.js").count("onExternalRender") == 1
    mmd = _read("static/mmd/mmd-core.js")
    assert mmd.index("onExternalRender") < mmd.index("this._flushRenderWaiters();", mmd.index("onExternalRender") - 400)


def test_only_index_loads_the_bridge_after_the_websocket_script():
    index = _read("templates/index.html")
    ws = index.index("/static/app/app-websocket.js")
    geo = index.index("/static/visit/crop-geometry.js")
    bridge = index.index("/static/visit/parent-bridge.js")
    assert ws < geo < bridge
    chat = _read("templates/chat.html")
    assert "parent-bridge.js" not in chat and "crop-geometry.js" not in chat


def test_websocket_dispatch_skips_the_chat_follower_window():
    # Since visit PR-12, visit_* frames share a dispatch block: only non-chat follower windows call the parent frame bridge (shared helper).
    src = _read("static/app/app-websocket.js")
    i = src.index("if (response.type === 'visit_state_change') {")
    block = src[i:i + 500]
    assert "if (!_isVisitChatFollowerWindow() && window.nekoVisitParentBridge" in block
    assert "nekoVisitParentBridge.onStateChange(response)" in block
    j = src.index("function _isVisitChatFollowerWindow()")
    helper = src[j:j + 300]
    assert re.search(r"return window\.__NEKO_MULTI_WINDOW__ === true\s*\n", helper)
    assert "/^\\/chat(?:_full)?(?:\\/|$)/" in helper


def test_avatar_portrait_is_not_modified_for_visits():
    src = _read("static/avatar/avatar-portrait.js")
    assert "nekoVisit" not in src and "visit-frame" not in src and "suspendCapture" not in src


def test_badges_are_localized_in_every_locale():
    import json

    for path in (ROOT / "static" / "locales").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        frame = data.get("visitFrame") or {}
        assert frame.get("peerAway") and frame.get("visitingAway"), path.name


def test_fps_popup_dispatches_before_saving():
    src = (ROOT / "static" / "avatar" / "avatar-ui-popup.js").read_text(encoding="utf-8")
    i = src.index("fpsSlider.addEventListener('change'")
    block = src[i:src.index("});", i)]
    # Dispatch removal of the temporary visit frame rate first; save only the user's newly selected value.
    assert block.index("neko-frame-rate-changed") < block.index("saveNEKOSettings")
