from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE_SCRIPT = (REPO_ROOT / "static" / "jukebox-standalone.js").read_text(encoding="utf-8")
HARNESS_HTML = """
<!DOCTYPE html>
<html>
<head>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; }
    body.neko-jukebox-standalone-page .jukebox-drag-overlay { display: none !important; }
    .jukebox-wrapper { position: fixed; inset: 0; }
    .jukebox-container { position: relative; width: 480px; height: 360px; background: #ddd; }
    .jukebox-header { position: relative; height: 48px; display: flex; align-items: center; justify-content: space-between; background: #999; }
    .jukebox-header-left, .jukebox-header-buttons, .jukebox-content, .jukebox-controls-row, .jukebox-calibration-section, .jukebox-notice { position: relative; z-index: 1; }
    .jukebox-drag-overlay { position: absolute; inset: 0; background: rgba(255, 0, 0, 0.05); }
    .jukebox-content { position: relative; height: 312px; padding: 12px; }
    .jukebox-controls-row { margin-top: 12px; }
    .jukebox-resize-handle { position: absolute; width: 24px; height: 24px; right: 0; bottom: 0; background: #333; z-index: 20; }
  </style>
</head>
<body>
  <div class="jukebox-wrapper">
    <div class="jukebox-container">
      <div class="jukebox-header">
        <div class="jukebox-header-left">Header</div>
        <div class="jukebox-header-buttons"><button id="closeBtn">Close</button></div>
      </div>
      <div class="jukebox-drag-overlay"></div>
      <div class="jukebox-content">
        <div class="jukebox-controls-row"><button id="speakerBtn">Speaker</button></div>
        <div class="jukebox-calibration-section">Calibration</div>
        <div class="jukebox-notice">Notice</div>
      </div>
      <div class="jukebox-resize-handle" data-dir="se"></div>
    </div>
  </div>
</body>
</html>
"""


def _bootstrap_page(page: Page, stub_script: str) -> None:
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(HARNESS_HTML)
    page.evaluate(
        """
        () => {
          window.__NEKO_JUKEBOX_STANDALONE__ = true;
          window.__speakerClicks = 0;
          document.getElementById('speakerBtn').addEventListener('click', () => {
            window.__speakerClicks += 1;
          });
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
    page.evaluate(stub_script)
    page.add_script_tag(content=STANDALONE_SCRIPT)
    assert page.evaluate("window.NekoJukeboxStandalonePage.mount()") is True


@pytest.mark.frontend
def test_jukebox_standalone_bridge_fast_interactions(mock_page: Page):
    _bootstrap_page(
        mock_page,
        """
        () => {
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
        }
        """,
    )

    expect(mock_page.locator(".jukebox-drag-overlay")).to_be_hidden()
    mock_page.click("#speakerBtn")
    assert mock_page.evaluate("window.__speakerClicks") == 1
    assert mock_page.evaluate("window.__dragGuardCleared") is True

    header = mock_page.locator(".jukebox-header").bounding_box()
    assert header is not None
    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down()
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up()

    handle = mock_page.locator(".jukebox-resize-handle").bounding_box()
    assert handle is not None
    mock_page.mouse.move(handle["x"] + 10, handle["y"] + 10)
    mock_page.mouse.down()
    mock_page.mouse.move(handle["x"] + 90, handle["y"] + 70)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    log = mock_page.evaluate("window.__bridgeLog")
    assert ["dragStop"] in log
    assert any(entry[0] == "dragStart" for entry in log)
    assert any(entry[0] == "setBounds" for entry in log)

    mock_page.evaluate("window.__bridgeLog = []")
    content = mock_page.locator(".jukebox-content").bounding_box()
    assert content is not None
    mock_page.mouse.move(content["x"] + 150, content["y"] + 120)
    mock_page.mouse.down()
    mock_page.mouse.move(content["x"] + 240, content["y"] + 180)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    content_log = mock_page.evaluate("window.__bridgeLog")
    assert any(entry[0] == "dragStart" for entry in content_log)


@pytest.mark.frontend
def test_jukebox_standalone_fallback_fast_interactions(mock_page: Page):
    _bootstrap_page(
        mock_page,
        """
        () => {
          window.__fallbackLog = [];
          let sx = 100;
          let sy = 120;
          let ow = 480;
          let oh = 360;
          Object.defineProperty(window, 'screenX', { configurable: true, get() { return sx; } });
          Object.defineProperty(window, 'screenY', { configurable: true, get() { return sy; } });
          Object.defineProperty(window, 'outerWidth', { configurable: true, get() { return ow; } });
          Object.defineProperty(window, 'outerHeight', { configurable: true, get() { return oh; } });
          Object.defineProperty(window.screen, 'availLeft', { configurable: true, get() { return 0; } });
          Object.defineProperty(window.screen, 'availTop', { configurable: true, get() { return 0; } });
          Object.defineProperty(window.screen, 'availWidth', { configurable: true, get() { return 1920; } });
          Object.defineProperty(window.screen, 'availHeight', { configurable: true, get() { return 1080; } });
          window.moveTo = function(x, y) {
            sx = x;
            sy = y;
            window.__fallbackLog.push(['moveTo', x, y]);
          };
          window.resizeTo = function(width, height) {
            ow = width;
            oh = height;
            window.__fallbackLog.push(['resizeTo', width, height]);
          };
        }
        """,
    )

    header = mock_page.locator(".jukebox-header").bounding_box()
    assert header is not None
    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down()
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up()

    handle = mock_page.locator(".jukebox-resize-handle").bounding_box()
    assert handle is not None
    mock_page.mouse.move(handle["x"] + 10, handle["y"] + 10)
    mock_page.mouse.down()
    mock_page.mouse.move(handle["x"] + 90, handle["y"] + 70)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    log = mock_page.evaluate("window.__fallbackLog")
    assert any(entry[0] == "moveTo" and entry[1] != 100 for entry in log)
    assert any(entry[0] == "resizeTo" and entry[1] > 480 for entry in log)

    mock_page.evaluate("window.__fallbackLog = []")
    content = mock_page.locator(".jukebox-content").bounding_box()
    assert content is not None
    mock_page.mouse.move(content["x"] + 150, content["y"] + 120)
    mock_page.mouse.down()
    mock_page.mouse.move(content["x"] + 240, content["y"] + 180)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    content_log = mock_page.evaluate("window.__fallbackLog")
    assert any(entry[0] == "moveTo" and entry[1] != 100 for entry in content_log)
