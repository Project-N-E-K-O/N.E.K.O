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
    .jukebox-resize-handle { position: absolute; width: 24px; height: 24px; background: #333; z-index: 20; }
    .jukebox-resize-handle[data-dir="se"] { right: 0; bottom: 0; }
    .jukebox-resize-handle[data-dir="nw"] { left: 0; top: 0; }
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
      <div class="jukebox-resize-handle" data-dir="nw"></div>
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

    mock_page.evaluate("window.__bridgeLog = []")
    header = mock_page.locator(".jukebox-header").bounding_box()
    assert header is not None
    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down(button="right")
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up(button="right")
    mock_page.wait_for_timeout(50)
    assert mock_page.evaluate("window.__bridgeLog") == []

    mock_page.evaluate("window.__bridgeLog = []")
    handle = mock_page.locator('.jukebox-resize-handle[data-dir="se"]').bounding_box()
    assert handle is not None
    mock_page.mouse.move(handle["x"] + 10, handle["y"] + 10)
    mock_page.mouse.down(button="middle")
    mock_page.mouse.move(handle["x"] + 90, handle["y"] + 70)
    mock_page.mouse.up(button="middle")
    mock_page.wait_for_timeout(50)
    assert mock_page.evaluate("window.__bridgeLog") == []

    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down()
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up()

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

    mock_page.evaluate("window.__bridgeLog = []")
    nw_handle = mock_page.locator('.jukebox-resize-handle[data-dir="nw"]').bounding_box()
    assert nw_handle is not None
    mock_page.mouse.move(nw_handle["x"] + 10, nw_handle["y"] + 10)
    mock_page.mouse.down()
    mock_page.mouse.move(nw_handle["x"] - 190, nw_handle["y"] - 190)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    resize_log = mock_page.evaluate(
        """
        () => window.__bridgeLog.filter((entry) => entry[0] === 'setBounds')
        """
    )
    assert resize_log
    x, y, width, height = resize_log[-1][1:]
    assert (x, y) == (0, 0)
    assert (width, height) == (580, 480)

    mock_page.evaluate(
        """
        () => {
          document.body.classList.add('jukebox-dragging');
          window.Jukebox.State.isDragging = false;
          const header = document.querySelector('.jukebox-header');
          header.dispatchEvent(new TouchEvent('touchstart', {
            bubbles: true,
            cancelable: true,
            touches: [new Touch({
              identifier: 1,
              target: header,
              clientX: 50,
              clientY: 20,
              screenX: 150,
              screenY: 140
            })]
          }));
        }
        """
    )
    mock_page.wait_for_timeout(50)
    mock_page.evaluate("document.dispatchEvent(new TouchEvent('touchcancel', { bubbles: true, cancelable: true }))")
    mock_page.wait_for_timeout(50)
    assert "jukebox-dragging" not in mock_page.locator("body").get_attribute("class")

    mock_page.evaluate(
        """
        () => {
          const handle = document.querySelector('.jukebox-resize-handle[data-dir="se"]');
          handle.dispatchEvent(new TouchEvent('touchstart', {
            bubbles: true,
            cancelable: true,
            touches: [new Touch({
              identifier: 2,
              target: handle,
              clientX: 470,
              clientY: 350,
              screenX: 570,
              screenY: 470
            })]
          }));
        }
        """
    )
    mock_page.wait_for_timeout(50)
    mock_page.evaluate("document.dispatchEvent(new TouchEvent('touchcancel', { bubbles: true, cancelable: true }))")
    mock_page.wait_for_timeout(50)
    body_class = mock_page.locator("body").get_attribute("class") or ""
    assert "jukebox-resizing" not in body_class


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
          window.__setFallbackBounds = function(x, y, width, height) {
            sx = x;
            sy = y;
            ow = width;
            oh = height;
          };
        }
        """,
    )

    mock_page.evaluate("window.__fallbackLog = []")
    header = mock_page.locator(".jukebox-header").bounding_box()
    assert header is not None
    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down(button="right")
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up(button="right")
    mock_page.wait_for_timeout(50)
    assert mock_page.evaluate("window.__fallbackLog") == []

    mock_page.evaluate("window.__fallbackLog = []")
    handle = mock_page.locator('.jukebox-resize-handle[data-dir="se"]').bounding_box()
    assert handle is not None
    mock_page.mouse.move(handle["x"] + 10, handle["y"] + 10)
    mock_page.mouse.down(button="middle")
    mock_page.mouse.move(handle["x"] + 90, handle["y"] + 70)
    mock_page.mouse.up(button="middle")
    mock_page.wait_for_timeout(50)
    assert mock_page.evaluate("window.__fallbackLog") == []

    mock_page.mouse.move(header["x"] + 30, header["y"] + 20)
    mock_page.mouse.down()
    mock_page.mouse.move(header["x"] + 180, header["y"] + 80)
    mock_page.mouse.up()

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

    mock_page.evaluate(
        """
        () => {
          window.__fallbackLog = [];
          window.__setFallbackBounds(100, 120, 480, 360);
        }
        """
    )
    nw_handle = mock_page.locator('.jukebox-resize-handle[data-dir="nw"]').bounding_box()
    assert nw_handle is not None
    mock_page.mouse.move(nw_handle["x"] + 10, nw_handle["y"] + 10)
    mock_page.mouse.down()
    mock_page.mouse.move(nw_handle["x"] - 190, nw_handle["y"] - 190)
    mock_page.mouse.up()
    mock_page.wait_for_timeout(50)

    resize_log = mock_page.evaluate("window.__fallbackLog")
    resize_pairs = [entry for entry in resize_log if entry[0] in ('moveTo', 'resizeTo')]
    assert ['moveTo', 0, 0] in resize_pairs
    assert ['resizeTo', 580, 480] in resize_pairs
