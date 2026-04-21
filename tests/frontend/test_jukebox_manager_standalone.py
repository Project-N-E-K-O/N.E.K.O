import re
from pathlib import Path

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_TEMPLATE = (REPO_ROOT / "templates" / "jukebox_manager.html").read_text(encoding="utf-8")
JUKEBOX_SCRIPT = (REPO_ROOT / "static" / "Jukebox.js").read_text(encoding="utf-8")
JUKEBOX_STANDALONE_SCRIPT = (REPO_ROOT / "static" / "jukebox-standalone.js").read_text(encoding="utf-8")

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


@pytest.mark.frontend
def test_jukebox_manager_standalone_uses_native_drag_regions():
    """
    回归保护：管理器的拖动必须走 CSS `-webkit-app-region: drag`（OS 原生 HTCAPTION），
    而不是 JS mousedown + setBounds。历史 bug：JS 设 setBounds 会和 Windows 边缘
    WS_THICKFRAME resize 热区并发触发，表现为"拖一下窗口变大一下"。

    改回 JS 驱动拖动会让这个测试失败。
    """
    # 面板本体必须声明为 drag 区域（放宽空白容忍 CSS 格式化工具）
    assert re.search(r"-webkit-app-region:\s*drag\s*!important", MANAGER_TEMPLATE)
    # 交互元素必须声明为 no-drag，否则点击会被原生拖动吃掉
    assert re.search(r"\.jukebox-sam-panel\s+button\b", MANAGER_TEMPLATE)
    assert re.search(r"\.jukebox-sam-panel\s+\.sam-close-btn\b", MANAGER_TEMPLATE)
    assert re.search(r"-webkit-app-region:\s*no-drag\s*!important", MANAGER_TEMPLATE)
    # 不应再注册 JS mousedown 拖动（旧实现的标志函数）——模板和外部 JS 文件都要拦
    for source in (MANAGER_TEMPLATE, JUKEBOX_STANDALONE_SCRIPT, JUKEBOX_SCRIPT):
        assert "_bindManagerStandaloneDrag" not in source
        assert "neko-jukebox-manager-standalone-dragging" not in source


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
def test_jukebox_manager_binding_selection_links_only_one_hop(mock_page: Page):
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
              song2: { name: 'Song 2', artist: 'B', visible: true },
              song3: { name: 'Song 3', artist: 'C', visible: true }
            },
            actions: {
              action1: { name: 'Action 1', format: 'vmd' },
              action2: { name: 'Action 2', format: 'vmd' }
            },
            bindings: {
              song1: { action1: { offset: 0 } },
              song2: { action1: { offset: 0 }, action2: { offset: 0 } }
            }
          };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set();
          SAM.bindingSourceActions = new Set();
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
    assert mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-action-id="action2"] input[type="checkbox"]').is_checked()

    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set();
          SAM.bindingSourceActions = new Set();
          SAM.renderBindings(document.querySelector('.bindings-panel'));
        }
        """
    )

    mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').click()
    assert mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-song-id="song1"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-action-id="action2"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-song-id="song3"] input[type="checkbox"]').is_checked()


@pytest.mark.frontend
def test_jukebox_manager_binding_select_all_links_only_one_hop(mock_page: Page):
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
              song1: { action1: { offset: 0 } }
            }
          };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set();
          SAM.bindingSourceActions = new Set();
          SAM.updateSelectionInfo = function() {};

          const panel = document.createElement('div');
          panel.className = 'bindings-panel';
          document.body.appendChild(panel);
          SAM.renderBindings(panel);
        }
        """
    )

    mock_page.click('#select-all-binding-songs')
    assert mock_page.locator('.sam-binding-item[data-song-id="song1"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-action-id="action2"] input[type="checkbox"]').is_checked()

    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set();
          SAM.bindingSourceActions = new Set();
          SAM.renderBindings(document.querySelector('.bindings-panel'));
        }
        """
    )

    mock_page.click('#select-all-binding-actions')
    assert mock_page.locator('.sam-binding-item[data-action-id="action1"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-action-id="action2"] input[type="checkbox"]').is_checked()
    assert mock_page.locator('.sam-binding-item[data-song-id="song1"] input[type="checkbox"]').is_checked()
    assert not mock_page.locator('.sam-binding-item[data-song-id="song2"] input[type="checkbox"]').is_checked()


@pytest.mark.frontend
def test_jukebox_manager_binding_export_selected_uses_one_hop(mock_page: Page):
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
    export_state = mock_page.evaluate(
        """
        async () => {
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
              song2: { action1: { offset: 0 }, action2: { offset: 0 } }
            }
          };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set(['song1']);
          SAM.bindingSourceActions = new Set();

          const element = document.createElement('div');
          element.innerHTML = '<button class="sam-tab active" data-tab="bindings"></button>';
          SAM.element = element;

          let capture = null;
          SAM.exportByIds = async (songIds, actionIds, filenamePrefix) => {
            capture = {
              songIds: [...songIds].sort(),
              actionIds: [...actionIds].sort(),
              filenamePrefix
            };
          };

          await SAM.exportSelected();
          return capture;
        }
        """
    )

    assert export_state == {
        "songIds": ["song1"],
        "actionIds": ["action1"],
        "filenamePrefix": "jukebox_binding_selected",
    }


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
