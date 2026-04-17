import re
from pathlib import Path

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_TEMPLATE = (REPO_ROOT / "templates" / "jukebox_manager.html").read_text(encoding="utf-8")
INLINE_SCRIPTS = re.findall(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", MANAGER_TEMPLATE, flags=re.S)
MANAGER_SCRIPT = next(script for script in INLINE_SCRIPTS if "_bindManagerStandaloneDrag" in script)
JUKEBOX_SCRIPT = (REPO_ROOT / "static" / "Jukebox.js").read_text(encoding="utf-8")

HARNESS_HTML = """
<!DOCTYPE html>
<html>
<head>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; }
    body { background: #111; }
  </style>
</head>
<body></body>
</html>
"""


def _bootstrap_manager_page(page: Page) -> None:
    page.set_viewport_size({"width": 900, "height": 700})
    page.set_content(HARNESS_HTML)
    page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.i18n = { isInitialized: true };
          window.__managerLog = [];
          window.__closeClicks = 0;
          window.__bounds = { x: 100, y: 120, width: 640, height: 520 };
          window.nekoJukeboxWindow = {
            getBounds() {
              return new Promise((resolve) => {
                setTimeout(() => resolve({ ...window.__bounds }), 80);
              });
            },
            getWorkArea() {
              return new Promise((resolve) => {
                setTimeout(() => resolve({ x: 0, y: 0, width: 1920, height: 1080 }), 80);
              });
            },
            setBounds(x, y, width, height) {
              window.__bounds = { x, y, width, height };
              window.__managerLog.push(['setBounds', x, y, width, height]);
            }
          };

          window.Jukebox = {
            SongActionManager: {
              create() {
                const panel = document.createElement('div');
                panel.className = 'jukebox-sam-panel';
                panel.innerHTML = `
                  <div class="sam-header">
                    <div class="sam-title">Manager</div>
                    <div class="sam-tabs">
                      <button class="sam-tab active" type="button">Songs</button>
                    </div>
                    <button class="sam-close-btn" id="closeBtn" type="button">×</button>
                  </div>
                  <div class="sam-content">
                    <div class="sam-panel active">
                      <div class="sam-gap" id="contentGap"></div>
                      <div class="sam-item" id="songItem">
                        <button id="itemBtn" type="button">Action</button>
                      </div>
                    </div>
                  </div>
                  <div class="sam-footer">
                    <span class="sam-selection-info">Info</span>
                    <span class="sam-click-add" id="clickAdd">+ Add</span>
                  </div>
                `;

                panel.querySelector('#closeBtn').addEventListener('click', () => {
                  window.__closeClicks += 1;
                });

                this.element = panel;
                return panel;
              },
              getStyles() {
                return `
                  .jukebox-sam-panel {
                    position: fixed;
                    inset: 0;
                    display: flex;
                    flex-direction: column;
                    box-sizing: border-box;
                    padding: 16px;
                    background: rgba(20, 20, 20, 0.96);
                    color: #fff;
                  }
                  .sam-header {
                    height: 52px;
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    flex-shrink: 0;
                  }
                  .sam-tabs {
                    flex: 1;
                    display: flex;
                    justify-content: center;
                  }
                  .sam-content {
                    flex: 1;
                    min-height: 0;
                    padding: 12px 0;
                  }
                  .sam-panel.active {
                    display: block;
                    height: 100%;
                  }
                  .sam-gap {
                    height: 180px;
                    margin-bottom: 12px;
                    border-radius: 8px;
                    background: rgba(255, 255, 255, 0.06);
                  }
                  .sam-item {
                    padding: 12px;
                    border-radius: 8px;
                    background: rgba(255, 255, 255, 0.14);
                  }
                  .sam-footer {
                    height: 60px;
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    flex-shrink: 0;
                  }
                `;
              },
              hide() {}
            }
          };
        }
        """
    )
    page.add_script_tag(content=MANAGER_SCRIPT)
    page.evaluate("_tryInitManager()")
    page.wait_for_selector(".jukebox-sam-panel")


@pytest.mark.frontend
def test_jukebox_manager_standalone_fast_drag_survives_async_bounds(mock_page: Page):
    _bootstrap_manager_page(mock_page)

    gap = mock_page.locator("#contentGap").bounding_box()
    assert gap is not None

    mock_page.mouse.move(gap["x"] + 24, gap["y"] + 24)
    mock_page.mouse.down()
    mock_page.mouse.move(gap["x"] + 180, gap["y"] + 120)
    mock_page.mouse.up()

    body_class = mock_page.locator("body").get_attribute("class") or ""
    assert "neko-jukebox-manager-standalone-dragging" not in body_class

    mock_page.wait_for_timeout(250)
    drag_log = mock_page.evaluate("window.__managerLog")
    assert any(entry[0] == "setBounds" and (entry[1] != 100 or entry[2] != 120) for entry in drag_log)

    body_class = mock_page.locator("body").get_attribute("class") or ""
    assert "neko-jukebox-manager-standalone-dragging" not in body_class

    mock_page.evaluate("window.__managerLog = []")
    mock_page.click("#closeBtn")
    assert mock_page.evaluate("window.__closeClicks") == 1
    assert mock_page.evaluate("window.__managerLog") == []


@pytest.mark.frontend
def test_jukebox_manager_select_all_checkbox_toggles_state(mock_page: Page):
    mock_page.set_viewport_size({"width": 900, "height": 700})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = () => Promise.reject(new Error('fetch should not be called in this test'));
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data = {
            songs: {
              song1: { name: 'Song 1', artist: 'A', visible: true },
              song2: { name: 'Song 2', artist: 'B', visible: true }
            },
            actions: {},
            bindings: {}
          };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.showHiddenSongs = true;
          SAM.bindDragEvents = function() {};
          SAM.bindFileDropEvents = function() {};
          SAM.updateSelectionInfo = function() {};

          const panel = document.createElement('div');
          panel.className = 'songs-panel';
          document.body.appendChild(panel);
          SAM.renderSongs(panel);
        }
        """
    )

    mock_page.click("#select-all-songs")
    assert mock_page.locator("#select-all-songs").is_checked()
    assert mock_page.locator(".sam-song-select:checked").count() == 2

    mock_page.click("#select-all-songs")
    assert not mock_page.locator("#select-all-songs").is_checked()
    assert mock_page.locator(".sam-song-select:checked").count() == 0


@pytest.mark.frontend
def test_jukebox_manager_binding_selections_are_decoupled(mock_page: Page):
    mock_page.set_viewport_size({"width": 900, "height": 700})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = () => Promise.reject(new Error('fetch should not be called in this test'));
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data = {
            songs: {
              song1: { name: 'Song 1', artist: 'A', visible: true },
              song2: { name: 'Song 2', artist: 'B', visible: true }
            },
            actions: {
              action1: { name: 'Action 1', format: 'vmd' },
              action2: { name: 'Action 2', format: 'vmd' }
            },
            bindings: {
              song1: { action1: { offset: 0 } },
              song2: { action2: { offset: 0 } }
            }
          };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.updateSelectionInfo = function() {};

          const panel = document.createElement('div');
          panel.className = 'bindings-panel';
          document.body.appendChild(panel);
          SAM.renderBindings(panel);
        }
        """
    )

    mock_page.locator('.sam-binding-item[data-song-id="song1"] input[type="checkbox"]').click()
    assert mock_page.locator('.sam-binding-item[data-song-id="song1"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('#select-all-binding-actions').is_checked()

    mock_page.click('#select-all-binding-actions')
    assert mock_page.locator('#select-all-binding-actions').is_checked()
    assert mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-action-id="action2"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').count() == 1
    assert not mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').is_checked()


@pytest.mark.frontend
def test_jukebox_manager_song_selection_keeps_scroll_position(mock_page: Page):
    mock_page.set_viewport_size({"width": 900, "height": 700})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = () => Promise.reject(new Error('fetch should not be called in this test'));
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    scroll_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          const songs = {};
          for (let i = 1; i <= 40; i += 1) {
            songs[`song${i}`] = { name: `Song ${i}`, artist: `Artist ${i}`, visible: true };
          }

          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);

          SAM.data = { songs, actions: {}, bindings: {} };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.showHiddenSongs = true;
          SAM.bindDragEvents = function() {};
          SAM.bindFileDropEvents = function() {};

          const panel = document.createElement('div');
          panel.className = 'songs-panel';
          panel.style.height = '220px';
          panel.style.overflowY = 'auto';
          document.body.appendChild(panel);
          SAM.renderSongs(panel);

          panel.scrollTop = panel.scrollHeight;
          const before = panel.scrollTop;
          panel.querySelector('.sam-song-select[data-id="song40"]').click();

          return {
            before,
            after: panel.scrollTop,
            selectedCount: SAM.selectedSongs.size
          };
        }
        """
    )

    assert scroll_state["before"] > 0
    assert scroll_state["selectedCount"] == 1
    assert scroll_state["after"] >= scroll_state["before"] - 40


@pytest.mark.frontend
def test_jukebox_manager_binding_selection_keeps_nested_scroll_position(mock_page: Page):
    mock_page.set_viewport_size({"width": 900, "height": 700})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = () => Promise.reject(new Error('fetch should not be called in this test'));
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    scroll_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          const songs = {};
          const actions = {};
          const bindings = {};

          for (let i = 1; i <= 30; i += 1) {
            songs[`song${i}`] = { name: `Song ${i}`, artist: `Artist ${i}`, visible: true };
            actions[`action${i}`] = { name: `Action ${i}`, format: 'vmd' };
            bindings[`song${i}`] = { [`action${i}`]: { offset: 0 } };
          }

          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);

          SAM.data = { songs, actions, bindings };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindBindingDragEvents = function() {};

          const panel = document.createElement('div');
          panel.className = 'bindings-panel';
          document.body.appendChild(panel);
          SAM.renderBindings(panel);

          const songsList = panel.querySelector('.songs-for-drop');
          songsList.scrollTop = songsList.scrollHeight;
          const before = songsList.scrollTop;
          songsList.querySelector('.sam-binding-item[data-song-id="song30"] input[type="checkbox"]').click();

          return {
            before,
            after: panel.querySelector('.songs-for-drop').scrollTop,
            selectedCount: SAM.bindingSelectedSongs.size
          };
        }
        """
    )

    assert scroll_state["before"] > 0
    assert scroll_state["selectedCount"] == 1
    assert scroll_state["after"] >= scroll_state["before"] - 40
