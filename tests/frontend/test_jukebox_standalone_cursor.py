from pathlib import Path

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE_SCRIPT = (REPO_ROOT / "static" / "jukebox" / "jukebox-standalone.js").read_text(encoding="utf-8")

HARNESS_HTML = """
<!DOCTYPE html>
<html>
<head>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; }
    body.neko-jukebox-standalone-page .jukebox-header { cursor: grab; }
    body.neko-jukebox-standalone-page.jukebox-dragging .jukebox-header { cursor: grabbing; }
    .jukebox-wrapper { position: fixed; inset: 0; }
    .jukebox-container { position: relative; width: 480px; height: 360px; -webkit-app-region: no-drag !important; }
    .jukebox-header { height: 48px; display: flex; align-items: center; -webkit-app-region: drag !important; }
    .jukebox-header-left, .jukebox-header-drag-fill { -webkit-app-region: drag !important; }
    .jukebox-header-drag-fill { flex: 1; align-self: stretch; }
    .jukebox-header-buttons, .jukebox-header-buttons * { -webkit-app-region: no-drag !important; }
    .jukebox-content { height: 260px; }
  </style>
</head>
<body>
  <div class="jukebox-wrapper">
    <div class="jukebox-container">
      <div class="jukebox-header">
        <div class="jukebox-header-left">Header</div>
        <div class="jukebox-header-drag-fill"></div>
        <div class="jukebox-header-buttons"><button id="closeBtn">Close</button></div>
      </div>
      <div class="jukebox-content">Content</div>
    </div>
  </div>
</body>
</html>
"""


@pytest.mark.frontend
def test_jukebox_standalone_bridge_drag_keeps_cursor_region_no_drag(mock_page: Page):
    mock_page.set_viewport_size({"width": 800, "height": 600})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.__NEKO_JUKEBOX_STANDALONE__ = true;
          window.__bridgeLog = [];
          window.nekoJukeboxBridge = {
            getBounds() {
              return { x: 100, y: 120, width: 480, height: 360 };
            },
            setBounds(x, y, width, height) {
              window.__bridgeLog.push(['setBounds', x, y, width, height]);
            },
            getWorkArea() {
              return { x: 0, y: 0, width: 1920, height: 1080 };
            },
            dragStart(x, y) {
              window.__bridgeLog.push(['dragStart', x, y]);
            },
            dragStop() {
              window.__bridgeLog.push(['dragStop']);
            }
          };
          window.Jukebox = {
            State: {
              container: document.querySelector('.jukebox-wrapper'),
              isDragging: false,
              _dragGuard: {
                disconnect() {
                  window.__dragGuardCleared = true;
                }
              }
            }
          };
        }
        """
    )
    mock_page.add_script_tag(content=STANDALONE_SCRIPT)
    assert mock_page.evaluate("window.NekoJukeboxStandalonePage.mount()") is True

    metrics = mock_page.evaluate(
        """
        () => {
          const header = document.querySelector('.jukebox-header');
          const fill = document.querySelector('.jukebox-header-drag-fill');
          const close = document.querySelector('#closeBtn');
          return {
            bodyClass: document.body.className,
            headerRegion: getComputedStyle(header).webkitAppRegion,
            headerPriority: header.style.getPropertyPriority('-webkit-app-region'),
            fillRegion: getComputedStyle(fill).webkitAppRegion,
            closeRegion: getComputedStyle(close).webkitAppRegion,
            headerCursor: getComputedStyle(header).cursor,
            dragGuardCleared: window.__dragGuardCleared === true
          };
        }
        """
    )

    assert "neko-jukebox-bridge-drag" in metrics["bodyClass"]
    assert metrics["headerRegion"] == "no-drag"
    assert metrics["fillRegion"] == "no-drag"
    assert metrics["closeRegion"] == "no-drag"
    assert metrics["headerPriority"] == "important"
    assert metrics["headerCursor"] == "grab"
    assert metrics["dragGuardCleared"] is True

    header = mock_page.locator(".jukebox-header").bounding_box()
    assert header is not None
    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down()
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    log = mock_page.evaluate("window.__bridgeLog")
    assert any(entry[0] == "dragStart" for entry in log)
    assert ["dragStop"] in log
