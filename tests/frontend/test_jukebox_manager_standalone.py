import re
from pathlib import Path

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_TEMPLATE = (REPO_ROOT / "templates" / "jukebox_manager.html").read_text(encoding="utf-8")
JUKEBOX_SCRIPT = (REPO_ROOT / "static" / "jukebox" / "Jukebox.js").read_text(encoding="utf-8")
JUKEBOX_STANDALONE_SCRIPT = (REPO_ROOT / "static" / "jukebox" / "jukebox-standalone.js").read_text(encoding="utf-8")

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


def setup_song_manager_page(mock_page: Page, songs: str, actions: str = "{}") -> None:
    mock_page.set_viewport_size({"width": 900, "height": 700})
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        f"""
        () => {{
          window.__batchDeleteRequests = [];
          window.__batchActionDeleteRequests = [];
          window.t = (key, fallback) => {{
            if (typeof fallback === 'string') return fallback;
            if (fallback && typeof fallback.defaultValue === 'string') return fallback.defaultValue;
            return key;
          }};
          const songs = {songs};
          const actions = {actions};
          window.fetch = async (url, options = {{}}) => {{
            if (String(url).endsWith('/config')) {{
              return {{
                ok: true,
                json: async () => ({{ songs, actions, bindings: {{}} }})
              }};
            }}
            if (String(url).endsWith('/songs/batch-delete')) {{
              const payload = JSON.parse(options.body);
              window.__batchDeleteRequests.push(payload);
              return {{
                ok: true,
                json: async () => ({{
                  success: true,
                  partial: false,
                  requestedCount: payload.songIds.length,
                  deletedCount: payload.songIds.filter(id => !songs[id].isBuiltin).length,
                  hiddenCount: payload.songIds.filter(id => songs[id].isBuiltin).length,
                  failedCount: 0,
                  deleted: payload.songIds
                    .filter(id => !songs[id].isBuiltin)
                    .map(id => ({{ songId: id, name: songs[id].name }})),
                  hidden: payload.songIds
                    .filter(id => songs[id].isBuiltin)
                    .map(id => ({{ songId: id, name: songs[id].name }})),
                  failed: []
                }})
              }};
            }}
            if (String(url).includes('/songs/') && options.method === 'DELETE') {{
              const songId = String(url).split('/songs/')[1];
              if (songs[songId]?.isBuiltin) {{
                songs[songId].visible = false;
                return {{
                  ok: true,
                  json: async () => ({{ success: true, hidden: true }})
                }};
              }}
              delete songs[songId];
              return {{
                ok: true,
                json: async () => ({{ success: true }})
              }};
            }}
            if (String(url).endsWith('/actions/batch-delete')) {{
              const payload = JSON.parse(options.body);
              window.__batchActionDeleteRequests.push(payload);
              return {{
                ok: true,
                json: async () => ({{
                  success: true,
                  partial: false,
                  requestedCount: payload.actionIds.length,
                  deletedCount: payload.actionIds.filter(id => !actions[id].isBuiltin).length,
                  hiddenCount: payload.actionIds.filter(id => actions[id].isBuiltin).length,
                  failedCount: 0,
                  deleted: payload.actionIds
                    .filter(id => !actions[id].isBuiltin)
                    .map(id => ({{ actionId: id, name: actions[id].name }})),
                  hidden: payload.actionIds
                    .filter(id => actions[id].isBuiltin)
                    .map(id => ({{ actionId: id, name: actions[id].name }})),
                  failed: []
                }})
              }};
            }}
            if (String(url).includes('/actions/') && String(url).endsWith('/visibility')) {{
              const actionId = String(url).split('/actions/')[1].split('/visibility')[0];
              actions[actionId].visible = options.body.get('visible') === 'true';
              return {{
                ok: true,
                json: async () => ({{ success: true }})
              }};
            }}
            if (String(url).includes('/songs/') && String(url).endsWith('/visibility')) {{
              const songId = String(url).split('/songs/')[1].split('/visibility')[0];
              songs[songId].visible = options.body.get('visible') === 'true';
              return {{
                ok: true,
                json: async () => ({{ success: true }})
              }};
            }}
            throw new Error('unexpected fetch: ' + url);
          }};
        }}
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.bindDragEvents = function() {};
          SAM.bindFileDropEvents = function() {};
          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);
          const panel = SAM.create();
          document.body.appendChild(panel);
          panel.style.display = 'flex';
        }
        """
    )
    mock_page.wait_for_selector(".sam-btn-song-danger", state="visible")


@pytest.mark.frontend
def test_jukebox_manager_standalone_uses_native_drag_regions():
    """
    Regression guard: manager dragging must use CSS `-webkit-app-region: drag`
    (native OS HTCAPTION) instead of JS mousedown plus setBounds. The historical
    bug was that JS setBounds could race with the Windows WS_THICKFRAME resize
    hot zone near the window edge, making the window grow when dragged.

    Reintroducing JS-driven dragging should make this test fail.
    """
    # The panel must stay no-drag so frameless resize edges and scroll areas are not claimed by HTCAPTION.
    assert re.search(r"\.jukebox-sam-panel\s*\{[\s\S]*?-webkit-app-region:\s*no-drag\s*!important", MANAGER_TEMPLATE)
    assert re.search(r"\.jukebox-sam-panel\s*\{[\s\S]*?padding:\s*0;", JUKEBOX_SCRIPT)
    assert re.search(r"\.sam-header\s*\{[\s\S]*?padding:\s*12px 12px 10px 16px;", JUKEBOX_SCRIPT)
    # The header must remain the drag region; whitespace is relaxed for CSS formatting tools.
    assert re.search(r"\.jukebox-sam-panel\s+\.sam-header\s*\{[\s\S]*?-webkit-app-region:\s*drag\s*!important", MANAGER_TEMPLATE)
    assert ".sam-drag-fill" in JUKEBOX_SCRIPT
    assert re.search(r"\.jukebox-sam-panel\s+\.sam-title,\s*\n\s*\.jukebox-sam-panel\s+\.sam-drag-fill\s*\{[\s\S]*?-webkit-app-region:\s*drag\s*!important", MANAGER_TEMPLATE)
    assert "body .jukebox-sam-panel" in MANAGER_TEMPLATE
    assert "width: 100vw !important" in MANAGER_TEMPLATE
    assert re.search(r"body\s+\.jukebox-sam-panel\s*\{[\s\S]*?padding:\s*0\s*!important", MANAGER_TEMPLATE)
    assert re.search(r"body\s+\.jukebox-sam-panel\s+\.sam-header\s*\{[\s\S]*?cursor:\s*grab\s*!important", MANAGER_TEMPLATE)
    assert "padding: 0 !important;" in MANAGER_TEMPLATE
    assert "padding: 12px 12px 10px 16px !important;" in MANAGER_TEMPLATE
    assert "document.head.appendChild(standaloneStyle)" in MANAGER_TEMPLATE
    assert "neko-jukebox-manager-bridge-drag" in MANAGER_TEMPLATE
    assert "function _selectManagerDragBridge()" in MANAGER_TEMPLATE
    assert "var candidates = [window.nekoJukeboxBridge, window.nekoJukeboxManagerBridge];" in MANAGER_TEMPLATE
    assert "typeof candidate.dragStart === 'function' && typeof candidate.dragStop === 'function'" in MANAGER_TEMPLATE
    assert "var bridge = _selectManagerDragBridge();" in MANAGER_TEMPLATE
    assert "bridge.dragStart(point.x, point.y)" in MANAGER_TEMPLATE
    assert "bridge.dragStop()" in MANAGER_TEMPLATE
    assert "_setManagerAppRegion(header, 'no-drag')" in MANAGER_TEMPLATE
    # Interactive elements must be no-drag or native dragging can consume clicks.
    assert re.search(r"\.jukebox-sam-panel\s+button\b[\s\S]*?\{[\s\S]*?-webkit-app-region:\s*no-drag\s*!important", MANAGER_TEMPLATE)
    assert re.search(r"\.jukebox-sam-panel\s+\.sam-close-btn\b[\s\S]*?\{[\s\S]*?-webkit-app-region:\s*no-drag\s*!important", MANAGER_TEMPLATE)
    # 不应再注册 JS mousedown 拖动（旧实现的标志函数）——模板和外部 JS 文件都要拦
    for source in (MANAGER_TEMPLATE, JUKEBOX_STANDALONE_SCRIPT, JUKEBOX_SCRIPT):
        assert "_bindManagerStandaloneDrag" not in source
        assert "neko-jukebox-manager-standalone-dragging" not in source


@pytest.mark.frontend
def test_jukebox_manager_tooltips_have_standalone_styles_and_binding():
    """
    Regression guard: the standalone manager window injects only
    SongActionManager styles, so its custom tooltip CSS must live there too.
    Otherwise data-tooltip elements bind mouse events but render invisible or
    unstyled in Electron.
    """
    assert ".jukebox-tooltip {" in JUKEBOX_SCRIPT
    assert ".jukebox-tooltip.visible" in JUKEBOX_SCRIPT
    assert "z-index: 100030;" in JUKEBOX_SCRIPT
    assert "Jukebox.bindTextTooltips(panel);" in JUKEBOX_SCRIPT
    assert "this.bindButtonTooltips(panel);" in JUKEBOX_SCRIPT
    assert 'data-tooltip="${Jukebox.escapeAttr(song.name)}"' in JUKEBOX_SCRIPT
    assert 'data-tooltip="${Jukebox.escapeAttr(action.name)}"' in JUKEBOX_SCRIPT
    assert "escapeAttr: function(text)" in JUKEBOX_SCRIPT
    assert "settingsBtn.dataset.tooltip = window.t('Jukebox.manager'" in JUKEBOX_SCRIPT
    assert "minBtn.dataset.tooltip = window.t('Jukebox.minimize'" in JUKEBOX_SCRIPT
    assert "closeBtn.dataset.tooltip = window.t('Jukebox.close'" in JUKEBOX_SCRIPT
    assert "samCloseBtn.dataset.tooltip = window.t('Jukebox.close'" in JUKEBOX_SCRIPT
    assert "settingsBtn.title = window.t('Jukebox.manager'" not in JUKEBOX_SCRIPT
    assert "minBtn.title = window.t('Jukebox.minimize'" not in JUKEBOX_SCRIPT
    assert "closeBtn.title = window.t('Jukebox.close'" not in JUKEBOX_SCRIPT
    assert "samCloseBtn.title = window.t('Jukebox.close'" not in JUKEBOX_SCRIPT


@pytest.mark.frontend
def test_jukebox_web_manager_drag_starts_only_from_header(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true }
        }
        """,
    )
    metrics = mock_page.evaluate(
        """
        () => {
          const panel = document.querySelector('.jukebox-sam-panel');
          panel.style.display = 'flex';
          panel.style.left = '100px';
          panel.style.top = '80px';
          window.Jukebox.bindPanelDrag(panel);

          const content = panel.querySelector('.sam-content');
          const contentRect = content.getBoundingClientRect();
          content.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true,
            cancelable: true,
            clientX: contentRect.left + 20,
            clientY: contentRect.top + 20
          }));
          const afterContentDown = document.body.classList.contains('sam-panel-dragging');

          const header = panel.querySelector('.sam-header');
          const headerRect = header.getBoundingClientRect();
          header.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true,
            cancelable: true,
            clientX: headerRect.left + 20,
            clientY: headerRect.top + 10
          }));
          const afterHeaderDown = document.body.classList.contains('sam-panel-dragging');
          document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
          const afterMouseUp = document.body.classList.contains('sam-panel-dragging');

          return {
            afterContentDown,
            afterHeaderDown,
            afterMouseUp,
            headerTopGap: headerRect.top - panel.getBoundingClientRect().top,
            panelCursor: getComputedStyle(panel).cursor,
            headerCursor: getComputedStyle(header).cursor,
            itemCursor: getComputedStyle(panel.querySelector('.sam-item')).cursor,
            titleCursor: getComputedStyle(panel.querySelector('.sam-title')).cursor,
            fillCursor: getComputedStyle(panel.querySelector('.sam-drag-fill')).cursor,
            tabsCursor: getComputedStyle(panel.querySelector('.sam-tabs')).cursor
          };
        }
        """
    )

    assert metrics["afterContentDown"] is False
    assert metrics["afterHeaderDown"] is True
    assert metrics["afterMouseUp"] is False
    assert metrics["headerTopGap"] <= 3
    assert metrics["panelCursor"] == "default"
    assert metrics["headerCursor"] == "grab"
    assert metrics["itemCursor"] == "default"
    assert metrics["titleCursor"] == "grab"
    assert metrics["fillCursor"] == "grab"
    assert metrics["tabsCursor"] == "grab"


@pytest.mark.frontend
def test_jukebox_manager_popup_requests_browser_resizable():
    assert "'width=480,height=600,resizable=yes'" in JUKEBOX_SCRIPT


@pytest.mark.frontend
def test_jukebox_web_resize_changes_size_without_zoom():
    resize_start = JUKEBOX_SCRIPT.find("bindResize: function(container)")
    assert resize_start >= 0
    resize_body = JUKEBOX_SCRIPT[resize_start:resize_start + 4000]
    assert ".style.zoom" not in resize_body
    assert "container.style.width" in resize_body
    assert "container.style.height" in resize_body


@pytest.mark.frontend
def test_jukebox_web_manager_uses_dom_resize_without_standalone(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          song2: { name: 'Song 2', artist: 'B', visible: true }
        }
        """,
    )
    metrics = mock_page.evaluate(
        """
        () => {
          const panel = document.querySelector('.jukebox-sam-panel');
          panel.style.left = '100px';
          panel.style.top = '80px';
          panel.style.width = '450px';
          panel.style.height = '420px';
          window.Jukebox.bindPanelResize(panel);

          const drag = (selector, moveX, moveY) => {
            const handle = panel.querySelector(selector);
            const rect = handle.getBoundingClientRect();
            const startX = rect.left + rect.width / 2;
            const startY = rect.top + rect.height / 2;
            handle.dispatchEvent(new MouseEvent('mousedown', {
              bubbles: true,
              cancelable: true,
              clientX: startX,
              clientY: startY
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
              bubbles: true,
              cancelable: true,
              clientX: startX + moveX,
              clientY: startY + moveY
            }));
            document.dispatchEvent(new MouseEvent('mouseup', {
              bubbles: true,
              cancelable: true
            }));
          };

          const before = panel.getBoundingClientRect();
          drag('.sam-resize-handle[data-dir="se"]', 90, 70);
          const afterSe = panel.getBoundingClientRect();
          drag('.sam-resize-handle[data-dir="w"]', -60, 0);
          const afterW = panel.getBoundingClientRect();
          const content = panel.querySelector('.sam-content').getBoundingClientRect();
          const footer = panel.querySelector('.sam-footer').getBoundingClientRect();

          return {
            handleCount: panel.querySelectorAll('.sam-resize-handle').length,
            beforeWidth: before.width,
            beforeHeight: before.height,
            afterSeWidth: afterSe.width,
            afterSeHeight: afterSe.height,
            afterWLeft: afterW.left,
            afterWWidth: afterW.width,
            contentBottom: content.bottom,
            footerTop: footer.top,
            minWidth: getComputedStyle(panel).minWidth,
            minHeight: getComputedStyle(panel).minHeight
          };
        }
        """
    )

    assert metrics["handleCount"] == 8
    assert metrics["afterSeWidth"] > metrics["beforeWidth"] + 50
    assert metrics["afterSeHeight"] > metrics["beforeHeight"] + 40
    assert metrics["afterWLeft"] < 100
    assert metrics["afterWWidth"] > metrics["afterSeWidth"] + 40
    assert metrics["contentBottom"] <= metrics["footerTop"] + 1
    assert metrics["minWidth"] == "420px"
    assert metrics["minHeight"] == "360px"


@pytest.mark.frontend
def test_jukebox_standalone_manager_does_not_render_web_resize_handles(mock_page: Page):
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.__NEKO_JUKEBOX_MANAGER_STANDALONE__ = true;
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = async () => ({
            ok: true,
            json: async () => ({ songs: {}, actions: {}, bindings: {} })
          });
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    handle_count = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);
          const panel = SAM.create();
          document.body.appendChild(panel);
          panel.style.display = 'flex';
          window.Jukebox.bindPanelResize(panel);
          return panel.querySelectorAll('.sam-resize-handle').length;
        }
        """
    )
    assert handle_count == 0


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
def test_jukebox_manager_song_delete_button_switches_between_clear_and_selected(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          song2: { name: 'Builtin Song', artist: 'B', visible: true, isBuiltin: true },
          hidden: { name: 'Hidden Song', artist: 'C', visible: false }
        }
        """,
    )

    danger_btn = mock_page.locator(".sam-btn-song-danger")
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert "3" in danger_btn.inner_text()

    mock_page.evaluate(
        """
        () => document.querySelector('.sam-btn-song-danger')
          .dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }))
        """
    )
    tooltip = mock_page.locator(".sam-danger-tooltip")
    assert tooltip.is_visible()
    assert "自定义" in tooltip.inner_text()
    assert "内置" in tooltip.inner_text()

    mock_page.locator('.sam-song-select[data-id="song1"]').click()
    assert danger_btn.get_attribute("data-mode") == "selected"
    assert "1" in danger_btn.inner_text()


@pytest.mark.frontend
def test_jukebox_manager_clear_visible_count_updates_when_show_hidden_toggles(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          song2: { name: 'Song 2', artist: 'B', visible: true },
          hidden: { name: 'Hidden Song', artist: 'C', visible: false }
        }
        """,
    )

    danger_btn = mock_page.locator(".sam-btn-song-danger")
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert "3" in danger_btn.inner_text()

    mock_page.locator(".sam-checkbox-right input").click()
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert "2" in danger_btn.inner_text()

    mock_page.locator(".sam-checkbox-right input").click()
    assert "3" in danger_btn.inner_text()


@pytest.mark.frontend
def test_jukebox_manager_delete_selected_uses_single_confirm(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          song2: { name: 'Song 2', artist: 'B', visible: true }
        }
        """,
    )

    mock_page.locator('.sam-song-select[data-id="song1"]').click()
    mock_page.evaluate("() => window.Jukebox.SongActionManager.confirmSongBatchDelete()")
    expect_title = mock_page.locator(".sam-danger-modal h3")
    assert "删除选中" in expect_title.inner_text()
    assert mock_page.evaluate(
        """
        () => {
          const backdrop = document.querySelector('.sam-danger-modal-backdrop');
          const panel = document.querySelector('.jukebox-sam-panel');
          return backdrop.parentElement === panel
            && getComputedStyle(backdrop).position === 'absolute'
            && getComputedStyle(backdrop).pointerEvents === 'auto';
        }
        """
    )
    mock_page.locator(".sam-danger-modal-confirm").click()

    mock_page.wait_for_function("() => window.__batchDeleteRequests.length === 1")
    assert mock_page.evaluate("window.__batchDeleteRequests[0].songIds") == ["song1"]
    assert mock_page.locator(".sam-danger-result-modal").is_visible()
    assert mock_page.evaluate(
        "() => document.querySelector('.sam-danger-result-backdrop').parentElement === document.querySelector('.jukebox-sam-panel')"
    )


@pytest.mark.frontend
def test_jukebox_manager_single_delete_builtin_song_hides_locally(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          builtin: { name: 'Builtin Song', artist: 'B', visible: true, isBuiltin: true }
        }
        """,
        """
        {
          action1: { name: 'Action 1', format: 'vmd', visible: true }
        }
        """,
    )
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data.bindings = { builtin: { action1: { offset: 0 } } };
        }
        """
    )

    mock_page.evaluate("() => window.Jukebox.SongActionManager.deleteSong('builtin')")
    mock_page.wait_for_function("() => window.Jukebox.SongActionManager.data.songs.builtin.visible === false")

    assert mock_page.evaluate("() => !!window.Jukebox.SongActionManager.data.songs.builtin")
    assert mock_page.evaluate("() => !!window.Jukebox.SongActionManager.data.bindings.builtin.action1")
    assert "sam-item-hidden" in (
        mock_page.locator('.songs-panel .sam-item[data-id="builtin"]').get_attribute("class") or ""
    )


@pytest.mark.frontend
def test_jukebox_manager_clear_visible_uses_second_confirm_and_escape_once(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          builtin: { name: 'Builtin Song', artist: 'B', visible: true, isBuiltin: true }
        }
        """,
    )

    mock_page.evaluate("() => window.Jukebox.SongActionManager.confirmSongBatchDelete()")
    assert "删除当前显示" in mock_page.locator(".sam-danger-modal h3").inner_text()
    mock_page.locator(".sam-danger-modal-confirm").click()
    assert "真..真的要删除当前显示吗.." in mock_page.locator(".sam-danger-modal p").first.inner_text()

    confirm_btn = mock_page.locator(".sam-danger-modal-confirm")
    assert mock_page.evaluate(
        "() => document.querySelector('.sam-danger-modal-backdrop').parentElement === document.querySelector('.jukebox-sam-panel')"
    )
    mock_page.evaluate("() => document.querySelector('.sam-danger-modal-confirm').click()")
    assert mock_page.evaluate("window.__batchDeleteRequests.length") == 0
    assert "sam-danger-confirm-escaped" in (confirm_btn.get_attribute("class") or "")
    assert confirm_btn.evaluate("el => getComputedStyle(el).pointerEvents") == "auto"
    mock_page.wait_for_function(
        "() => !document.querySelector('.sam-danger-modal-confirm').classList.contains('sam-danger-confirm-escaping')"
    )
    assert confirm_btn.get_attribute("data-escape-ready") == "true"

    mock_page.evaluate(
        """
        () => window.Jukebox.SongActionManager.showSongDeleteConfirmDialog(
          'clear-visible',
          ['song1', 'builtin'],
          2
        )
        """
    )
    confirm_btn = mock_page.locator(".sam-danger-modal-confirm")
    mock_page.evaluate(
        """
        () => {
          const zone = document.querySelector('.sam-danger-confirm-zone-final');
          const button = document.querySelector('.sam-danger-modal-confirm');
          const rect = button.getBoundingClientRect();
          zone.dispatchEvent(new MouseEvent('mouseenter', {
            clientX: rect.right + 2,
            clientY: rect.top + rect.height / 2
          }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => !document.querySelector('.sam-danger-modal-confirm').classList.contains('sam-danger-confirm-escaping')"
    )
    right_initial_x = int(confirm_btn.get_attribute("data-escape-initial-x") or "0")
    assert right_initial_x < 0

    mock_page.evaluate(
        """
        () => window.Jukebox.SongActionManager.showSongDeleteConfirmDialog(
          'clear-visible',
          ['song1', 'builtin'],
          2
        )
        """
    )
    confirm_btn = mock_page.locator(".sam-danger-modal-confirm")
    mock_page.evaluate(
        """
        () => {
          const zone = document.querySelector('.sam-danger-confirm-zone-final');
          const button = document.querySelector('.sam-danger-modal-confirm');
          const rect = button.getBoundingClientRect();
          zone.dispatchEvent(new MouseEvent('mouseenter', {
            clientX: rect.left - 2,
            clientY: rect.top + rect.height / 2
          }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => !document.querySelector('.sam-danger-modal-confirm').classList.contains('sam-danger-confirm-escaping')"
    )
    assert int(confirm_btn.get_attribute("data-escape-initial-x") or "0") > 0

    mock_page.evaluate(
        """
        () => {
          const zone = document.querySelector('.sam-danger-confirm-zone-final');
          const button = document.querySelector('.sam-danger-modal-confirm');
          const rect = button.getBoundingClientRect();
          zone.dispatchEvent(new MouseEvent('mouseenter', {
            clientX: rect.right + 2,
            clientY: rect.top + rect.height / 2
          }));
        }
        """
    )
    assert int(confirm_btn.get_attribute("data-escape-initial-x") or "0") > 0
    assert "sam-danger-confirm-escaped" in (confirm_btn.get_attribute("class") or "")

    confirm_btn.click()
    mock_page.wait_for_function("() => window.__batchDeleteRequests.length === 1")
    assert mock_page.evaluate("window.__batchDeleteRequests[0].songIds") == ["song1", "builtin"]


@pytest.mark.frontend
def test_jukebox_manager_actions_delete_current_display_matches_song_flow(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true }
        }
        """,
        """
        {
          action1: { name: 'Action 1', format: 'vmd' },
          builtinAction: { name: 'Builtin Action', format: 'vrma', isBuiltin: true }
        }
        """,
    )

    mock_page.locator('.sam-tab[data-tab="actions"]').click()
    danger_btn = mock_page.locator(".sam-btn-song-danger")
    assert danger_btn.get_attribute("data-resource") == "actions"
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert danger_btn.inner_text() == "删除当前显示(2)"

    mock_page.locator('.sam-action-select[data-id="action1"]').click()
    assert danger_btn.get_attribute("data-mode") == "selected"
    assert danger_btn.inner_text() == "删除选中(1)"

    mock_page.locator('.sam-action-select[data-id="action1"]').click()
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    mock_page.evaluate("() => window.Jukebox.SongActionManager.confirmManagerBatchDelete()")
    assert "删除当前显示" in mock_page.locator(".sam-danger-modal h3").inner_text()
    mock_page.locator(".sam-danger-modal-confirm").click()
    assert "真..真的要删除当前显示吗.." in mock_page.locator(".sam-danger-modal p").first.inner_text()

    confirm_btn = mock_page.locator(".sam-danger-modal-confirm")
    mock_page.evaluate("() => document.querySelector('.sam-danger-modal-confirm').click()")
    assert mock_page.evaluate("window.__batchActionDeleteRequests.length") == 0
    assert confirm_btn.get_attribute("data-escaped") == "true"
    mock_page.evaluate(
        """
        () => {
          const zone = document.querySelector('.sam-danger-confirm-zone-final');
          const button = document.querySelector('.sam-danger-modal-confirm');
          const rect = button.getBoundingClientRect();
          zone.dispatchEvent(new MouseEvent('mouseenter', {
            clientX: rect.right + 2,
            clientY: rect.top + rect.height / 2
          }));
        }
        """
    )
    mock_page.evaluate("() => document.querySelector('.sam-danger-modal-confirm').click()")
    assert mock_page.evaluate("window.__batchActionDeleteRequests.length") == 0
    mock_page.wait_for_function(
        "() => document.querySelector('.sam-danger-modal-confirm').dataset.escapeReady === 'true'"
    )
    confirm_btn.click()
    mock_page.wait_for_function("() => window.__batchActionDeleteRequests.length === 1")
    assert mock_page.evaluate("window.__batchActionDeleteRequests[0].actionIds") == ["action1", "builtinAction"]


@pytest.mark.frontend
def test_jukebox_manager_actions_visibility_filters_current_display(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true }
        }
        """,
        """
        {
          action1: { name: 'Action 1', format: 'vmd', visible: true },
          action2: { name: 'Hidden Action', format: 'vrma', visible: false },
          builtinAction: { name: 'Builtin Action', format: 'vmd', visible: true, isBuiltin: true }
        }
        """,
    )

    mock_page.locator('.sam-tab[data-tab="actions"]').click()
    danger_btn = mock_page.locator(".sam-btn-song-danger")
    assert danger_btn.inner_text() == "删除当前显示(3)"
    assert mock_page.locator('.actions-panel .sam-item[data-id="action2"]').count() == 1
    assert "sam-item-hidden" in (mock_page.locator('.actions-panel .sam-item[data-id="action2"]').get_attribute("class") or "")

    mock_page.locator(".actions-panel .sam-checkbox-right input").click()
    assert danger_btn.inner_text() == "删除当前显示(2)"
    assert mock_page.locator('.actions-panel .sam-item[data-id="action2"]').count() == 0

    mock_page.locator('.actions-panel .sam-item[data-id="action1"] .sam-visibility-btn').click()
    assert mock_page.evaluate("window.Jukebox.SongActionManager.data.actions.action1.visible") is False
    assert mock_page.locator('.actions-panel .sam-item[data-id="action1"]').count() == 0
    assert danger_btn.inner_text() == "删除当前显示(1)"

    mock_page.locator(".actions-panel .sam-checkbox-right input").click()
    assert danger_btn.inner_text() == "删除当前显示(3)"
    assert "sam-item-hidden" in (mock_page.locator('.actions-panel .sam-item[data-id="action1"]').get_attribute("class") or "")


@pytest.mark.frontend
def test_jukebox_manager_hidden_selection_does_not_drive_delete_selected(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          visibleSong: { name: 'Visible Song', artist: 'A', visible: true },
          hiddenSong: { name: 'Hidden Song', artist: 'B', visible: false }
        }
        """,
        """
        {
          visibleAction: { name: 'Visible Action', format: 'vmd', visible: true },
          hiddenAction: { name: 'Hidden Action', format: 'vmd', visible: false }
        }
        """,
    )

    danger_btn = mock_page.locator(".sam-btn-song-danger")
    mock_page.locator('.songs-panel .sam-item[data-id="hiddenSong"] .sam-song-select').click()
    assert danger_btn.get_attribute("data-mode") == "selected"
    assert danger_btn.inner_text() == "删除选中(1)"

    mock_page.locator(".songs-panel .sam-checkbox-right input").click()
    assert mock_page.locator('.songs-panel .sam-item[data-id="hiddenSong"]').count() == 0
    assert not mock_page.evaluate("() => window.Jukebox.SongActionManager.selectedSongs.has('hiddenSong')")
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert danger_btn.inner_text() == "删除当前显示(1)"

    mock_page.locator('.sam-tab[data-tab="actions"]').click()
    mock_page.locator('.actions-panel .sam-item[data-id="hiddenAction"] .sam-action-select').click()
    assert danger_btn.get_attribute("data-mode") == "selected"
    assert danger_btn.inner_text() == "删除选中(1)"

    mock_page.locator(".actions-panel .sam-checkbox-right input").click()
    assert mock_page.locator('.actions-panel .sam-item[data-id="hiddenAction"]').count() == 0
    assert not mock_page.evaluate("() => window.Jukebox.SongActionManager.selectedActions.has('hiddenAction')")
    assert danger_btn.get_attribute("data-mode") == "clear-visible"
    assert danger_btn.inner_text() == "删除当前显示(1)"


@pytest.mark.frontend
def test_jukebox_manager_hiding_song_prunes_selection(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          song1: { name: 'Song 1', artist: 'A', visible: true },
          song2: { name: 'Song 2', artist: 'B', visible: true }
        }
        """,
        """
        {
          action1: { name: 'Action 1', format: 'vmd', visible: true }
        }
        """,
    )

    mock_page.locator(".songs-panel .sam-checkbox-right input").click()
    mock_page.locator('.songs-panel .sam-item[data-id="song1"] .sam-song-select').click()
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.bindingSourceSongs.add('song1');
          SAM.bindingSelectedSongs.add('song1');
        }
        """
    )
    assert mock_page.locator(".sam-btn-song-danger").inner_text() == "删除选中(1)"

    mock_page.locator('.songs-panel .sam-item[data-id="song1"] .sam-visibility-btn').click()

    assert mock_page.locator('.songs-panel .sam-item[data-id="song1"]').count() == 0
    assert not mock_page.evaluate("() => window.Jukebox.SongActionManager.selectedSongs.has('song1')")
    assert not mock_page.evaluate("() => window.Jukebox.SongActionManager.bindingSourceSongs.has('song1')")
    assert not mock_page.evaluate("() => window.Jukebox.SongActionManager.bindingSelectedSongs.has('song1')")
    assert mock_page.locator(".sam-btn-song-danger").inner_text() == "删除当前显示(1)"


@pytest.mark.frontend
def test_jukebox_manager_song_tags_filter_hidden_actions(mock_page: Page):
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
    tag_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.bindDragEvents = function() {};
          SAM.bindFileDropEvents = function() {};
          SAM.data = {
            songs: {
              song1: {
                name: 'Song 1',
                artist: 'A',
                visible: true,
                defaultAction: 'hiddenAction'
              }
            },
            actions: {
              hiddenAction: { name: 'Hidden Action', format: 'vmd', visible: false },
              visibleAction: { name: 'Visible Action', format: 'vmd', visible: true }
            },
            bindings: {
              song1: {
                hiddenAction: { offset: 0 },
                visibleAction: { offset: 0 }
              }
            }
          };

          const panel = document.createElement('div');
          panel.className = 'songs-panel';
          document.body.appendChild(panel);

          SAM.showHiddenActions = false;
          SAM.showHiddenSongs = true;
          SAM.renderSongs(panel);
          const filtered = Array.from(panel.querySelectorAll('.sam-item-bindings .sam-binding-tag'))
            .map((node) => node.textContent.trim());

          SAM.showHiddenActions = true;
          SAM.renderSongs(panel);
          const withHidden = Array.from(panel.querySelectorAll('.sam-item-bindings .sam-binding-tag'))
            .map((node) => node.textContent.trim());

          return { filtered, withHidden };
        }
        """
    )

    assert tag_state == {
        "filtered": ["Visible Action"],
        "withHidden": ["★ Hidden Action", "Visible Action"],
    }


@pytest.mark.frontend
def test_jukebox_playback_ignores_hidden_bound_actions(mock_page: Page):
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'mmd' };
          window.fetch = async (url) => {
            if (!String(url).endsWith('/api/jukebox/config')) {
              throw new Error('unexpected fetch: ' + url);
            }
            return {
              ok: true,
              json: async () => ({
                songs: {
                  song1: {
                    name: 'Song 1',
                    artist: 'A',
                    visible: true,
                    defaultAction: 'hiddenAction'
                  },
                  song2: {
                    name: 'Song 2',
                    artist: 'B',
                    visible: true,
                    defaultAction: 'hiddenAction'
                  }
                },
                actions: {
                  hiddenAction: { name: 'Hidden Action', format: 'vmd', visible: false },
                  visibleAction: { name: 'Visible Action', format: 'vmd', visible: true }
                },
                bindings: {
                  song1: {
                    hiddenAction: { offset: 0 },
                    visibleAction: { offset: 0 }
                  },
                  song2: {
                    hiddenAction: { offset: 0 }
                  }
                }
              })
            };
          };
          document.body.innerHTML = '<table><tbody id="jukebox-song-list"></tbody></table>';
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    playback_state = mock_page.evaluate(
        """
        async () => {
          await window.Jukebox.loadSongs();
          const [song1, song2] = window.Jukebox.State.songs;
          return {
            song1BoundActionIds: song1.boundActions.map((action) => action.id),
            song1SelectedActionId: window.Jukebox.getActionForModel(song1)?.id || null,
            song2BoundActionIds: song2.boundActions.map((action) => action.id),
            song2SelectedActionId: window.Jukebox.getActionForModel(song2)?.id || null
          };
        }
        """
    )

    assert playback_state == {
        "song1BoundActionIds": ["visibleAction"],
        "song1SelectedActionId": "visibleAction",
        "song2BoundActionIds": [],
        "song2SelectedActionId": None,
    }


@pytest.mark.frontend
def test_jukebox_manager_add_binding_index_uses_visible_actions(mock_page: Page):
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
    binding_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data = {
            songs: {
              song1: { name: 'Song 1', artist: 'A', visible: true }
            },
            actions: {
              hiddenAction: { name: 'Hidden Action', format: 'vmd', visible: false },
              visibleAction: { name: 'Visible Action', format: 'vmd', visible: true }
            },
            bindings: {}
          };
          SAM.showHiddenActions = false;

          const host = document.createElement('div');
          host.innerHTML = '<button class="sam-add-binding-btn">+</button>';
          document.body.appendChild(host);
          const button = host.querySelector('button');

          let captured = null;
          SAM.bindSongToAction = (songId, actionId) => {
            captured = { songId, actionId };
          };

          SAM.showAddBindingInput(button, 'song1', 'song');
          const options = Array.from(document.querySelectorAll('.sam-add-binding-option'))
            .map((node) => ({
              id: node.dataset.id,
              index: node.querySelector('.sam-add-binding-option-index').textContent,
              name: node.querySelector('.sam-add-binding-option-name').textContent
            }));

          const input = document.querySelector('.sam-add-binding-input');
          input.value = '1';
          document.querySelector('.sam-add-binding-confirm').click();

          return { options, captured };
        }
        """
    )

    assert binding_state == {
        "options": [{"id": "visibleAction", "index": "1", "name": "Visible Action"}],
        "captured": {"songId": "song1", "actionId": "visibleAction"},
    }


@pytest.mark.frontend
def test_jukebox_manager_add_binding_index_uses_visible_songs(mock_page: Page):
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
    binding_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data = {
            songs: {
              hiddenSong: { name: 'Hidden Song', artist: 'A', visible: false },
              visibleSong: { name: 'Visible Song', artist: 'B', visible: true }
            },
            actions: {
              action1: { name: 'Action 1', format: 'vmd', visible: true }
            },
            bindings: {}
          };
          SAM.showHiddenSongs = false;

          const host = document.createElement('div');
          host.innerHTML = '<button class="sam-add-binding-btn">+</button>';
          document.body.appendChild(host);
          const button = host.querySelector('button');

          let captured = null;
          SAM.bindSongToAction = (songId, actionId) => {
            captured = { songId, actionId };
          };

          SAM.showAddBindingInput(button, 'action1', 'action');
          const options = Array.from(document.querySelectorAll('.sam-add-binding-option'))
            .map((node) => ({
              id: node.dataset.id,
              index: node.querySelector('.sam-add-binding-option-index').textContent,
              name: node.querySelector('.sam-add-binding-option-name').textContent
            }));

          const input = document.querySelector('.sam-add-binding-input');
          input.value = '1';
          document.querySelector('.sam-add-binding-confirm').click();

          return { options, captured };
        }
        """
    )

    assert binding_state == {
        "options": [{"id": "visibleSong", "index": "1", "name": "Visible Song"}],
        "captured": {"songId": "visibleSong", "actionId": "action1"},
    }


@pytest.mark.frontend
def test_jukebox_manager_bindings_filter_hidden_songs(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          visibleSong: { name: 'Visible Song', artist: 'A', visible: true },
          hiddenSong: { name: 'Hidden Song', artist: 'B', visible: false }
        }
        """,
        """
        {
          action1: { name: 'Action 1', format: 'vmd', visible: true }
        }
        """,
    )
    mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          SAM.data.bindings = {
            visibleSong: { action1: { offset: 0 } },
            hiddenSong: { action1: { offset: 0 } }
          };
          SAM.toggleShowHidden(false);
        }
        """
    )

    mock_page.locator('.sam-tab[data-tab="actions"]').click()
    assert mock_page.locator('.actions-panel .sam-binding-tag', has_text="Visible Song").count() == 1
    assert mock_page.locator('.actions-panel .sam-binding-tag', has_text="Hidden Song").count() == 0

    mock_page.locator('.sam-tab[data-tab="bindings"]').click()
    assert mock_page.locator('.bindings-panel .songs-for-drop [data-song-id="visibleSong"]').count() == 1
    assert mock_page.locator('.bindings-panel .songs-for-drop [data-song-id="hiddenSong"]').count() == 0
    assert mock_page.locator('.bindings-panel .actions-for-drop .sam-binding-tag-small', has_text="Visible Song").count() == 1
    assert mock_page.locator('.bindings-panel .actions-for-drop .sam-binding-tag-small', has_text="Hidden Song").count() == 0

    mock_page.locator("#select-all-binding-songs").click()
    selected_state = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          return {
            source: Array.from(SAM.bindingSourceSongs).sort(),
            selected: Array.from(SAM.bindingSelectedSongs).sort()
          };
        }
        """
    )
    assert selected_state == {
        "source": ["visibleSong"],
        "selected": ["visibleSong"],
    }


@pytest.mark.frontend
def test_jukebox_manager_long_song_name_scrolls_without_pushing_actions(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          longSong: {
            name: 'This is a very very very very very very very very very very long song name that should stay inside the name column',
            artist: 'A',
            visible: true
          }
        }
        """,
    )
    mock_page.set_viewport_size({"width": 430, "height": 500})
    mock_page.evaluate(
        """
        () => {
          const panel = document.querySelector('.jukebox-sam-panel');
          panel.style.width = '360px';
          panel.style.height = '420px';
        }
        """
    )

    metrics = mock_page.locator('.sam-item[data-id="longSong"]').evaluate(
        """
        (item) => {
          const header = item.querySelector('.sam-item-header');
          const name = item.querySelector('.sam-item-name');
          const actions = item.querySelector('.sam-item-actions');
          const before = name.getBoundingClientRect();
          name.focus();
          const after = name.getBoundingClientRect();
          return {
            headerRight: header.getBoundingClientRect().right,
            actionsRight: actions.getBoundingClientRect().right,
            nameClientWidth: name.clientWidth,
            nameScrollWidth: name.scrollWidth,
            beforeWidth: before.width,
            afterWidth: after.width,
          };
        }
        """
    )

    assert metrics["actionsRight"] <= metrics["headerRight"] + 1
    assert metrics["nameScrollWidth"] > metrics["nameClientWidth"]
    assert abs(metrics["beforeWidth"] - metrics["afterWidth"]) < 1


@pytest.mark.frontend
def test_jukebox_manager_long_text_uses_smooth_marquee_runtime(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          longSong: {
            name: '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
            artist: 'GARNiDELiA',
            visible: true
          }
        }
        """,
    )
    mock_page.set_viewport_size({"width": 430, "height": 500})
    mock_page.evaluate(
        """
        () => {
          const panel = document.querySelector('.jukebox-sam-panel');
          panel.style.width = '360px';
          panel.style.height = '420px';
          window.Jukebox.updateMarqueeText(panel);
        }
        """
    )

    mock_page.wait_for_function(
        """
        () => {
          const el = document.querySelector('.sam-item-name');
          return el && el.classList.contains('neko-marquee-active');
        }
        """
    )
    mock_page.wait_for_function(
        """
        () => {
          const el = document.querySelector('.sam-item-name');
          return el && el.scrollLeft > 0;
        }
        """,
        timeout=4000,
    )
    assert mock_page.locator(".sam-item-name").get_attribute("data-neko-marquee") == ""


@pytest.mark.frontend
def test_jukebox_manager_marquee_uses_conservative_one_way_speed(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          longSong: {
            name: '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
            artist: 'GARNiDELiA',
            visible: true
          }
        }
        """,
    )
    timing = mock_page.evaluate(
        """
        () => ({
          shortDuration: window.Jukebox.getMarqueeDuration(20),
          longDuration: window.Jukebox.getMarqueeDuration(1000),
          quarterProgress: window.Jukebox.easeInOutMarquee(0.25),
          halfwayProgress: window.Jukebox.easeInOutMarquee(0.5),
          threeQuarterProgress: window.Jukebox.easeInOutMarquee(0.75)
        })
        """
    )

    assert timing["shortDuration"] == 3000
    assert timing["longDuration"] == 60000
    assert timing["quarterProgress"] < 0.25
    assert abs(timing["halfwayProgress"] - 0.5) < 0.001
    assert timing["threeQuarterProgress"] > 0.75


@pytest.mark.frontend
def test_jukebox_manager_marquee_jumps_to_start_after_forward_pass(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          longSong: {
            name: '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
            artist: 'GARNiDELiA',
            visible: true
          }
        }
        """,
    )
    state = mock_page.evaluate(
        """
        () => {
          const el = document.querySelector('.sam-item-name');
          const maxScroll = Math.max(0, el.scrollWidth - el.clientWidth);
          const duration = window.Jukebox.getMarqueeDuration(maxScroll);
          window.Jukebox.State.marqueeItems.set(el, {
            phase: 'forward',
            phaseStart: performance.now() - duration - 1,
            duration,
            maxScroll
          });
          window.Jukebox.tickMarqueeText(performance.now());
          const item = window.Jukebox.State.marqueeItems.get(el);
          return { scrollLeft: el.scrollLeft, phase: item.phase };
        }
        """
    )

    assert state == {"scrollLeft": 0, "phase": "pauseStart"}


@pytest.mark.frontend
def test_jukebox_manager_editing_marquee_keeps_browser_scroll_control(mock_page: Page):
    setup_song_manager_page(
        mock_page,
        """
        {
          longSong: {
            name: '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
            artist: 'GARNiDELiA',
            visible: true
          }
        }
        """,
    )
    state = mock_page.evaluate(
        """
        () => {
          const el = document.querySelector('.sam-item-name');
          el.focus();
          const targetScroll = Math.min(40, Math.max(0, el.scrollWidth - el.clientWidth));
          el.scrollLeft = targetScroll;
          window.Jukebox.State.marqueeItems.set(el, {
            phase: 'forward',
            phaseStart: performance.now() - 10000,
            duration: 1,
            maxScroll: Math.max(0, el.scrollWidth - el.clientWidth)
          });
          window.Jukebox.tickMarqueeText(performance.now());
          return {
            scrollLeft: el.scrollLeft,
            targetScroll,
            overflowX: getComputedStyle(el).overflowX
          };
        }
        """
    )

    assert state["targetScroll"] > 0
    assert state["scrollLeft"] == state["targetScroll"]
    assert state["overflowX"] == "auto"


@pytest.mark.frontend
def test_jukebox_song_list_long_name_uses_same_marquee_runtime(mock_page: Page):
    mock_page.set_viewport_size({"width": 430, "height": 500})
    mock_page.set_content(
        """
        <!DOCTYPE html>
        <html>
        <head>
          <style>
            .song-name {
              display: block;
              width: 120px;
              white-space: nowrap;
              overflow: hidden;
            }
          </style>
        </head>
        <body><table><tbody id="jukebox-song-list"></tbody></table></body>
        </html>
        """
    )
    mock_page.evaluate("() => { window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key; }")
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    mock_page.evaluate(
        """
        () => {
          window.Jukebox.State.songs = [{
            id: 'longSong',
            name: '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
            artist: 'GARNiDELiA',
            visible: true
          }];
          window.Jukebox.State.songElements = {};
          window.Jukebox.renderList();
          window.Jukebox.updateMarqueeText(document);
        }
        """
    )

    mock_page.wait_for_function(
        """
        () => {
          const el = document.querySelector('.song-name');
          return el && el.classList.contains('neko-marquee-active');
        }
        """
    )
    mock_page.wait_for_function(
        """
        () => {
          const el = document.querySelector('.song-name');
          return el && el.scrollLeft > 0;
        }
        """,
        timeout=4000,
    )
    assert mock_page.locator(".song-name").get_attribute("data-neko-marquee") == ""


@pytest.mark.frontend
def test_jukebox_manager_binding_long_text_stays_inside_columns(mock_page: Page):
    mock_page.set_viewport_size({"width": 520, "height": 500})
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
    metrics = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          const longSong = '桃源恋歌-ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss';
          const longAction = '动作名-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);

          SAM.data = {
            songs: {
              song1: { name: longSong, artist: 'GARNiDELiA', visible: true },
              song2: { name: 'Ievan Polkka', artist: 'Otomania', visible: true }
            },
            actions: {
              action1: { name: longAction, format: 'vmd' },
              action2: { name: '甩葱歌', format: 'vrma' }
            },
            bindings: {
              song1: { action1: 0, action2: { offset: 0 } },
              song2: { action1: { offset: 0 } }
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
          panel.style.width = '460px';
          document.body.appendChild(panel);
          SAM.renderBindings(panel);
          window.Jukebox.updateMarqueeText(panel);

          const container = panel.querySelector('.sam-bindings-container');
          const sections = Array.from(panel.querySelectorAll('.sam-bindings-section'));
          const item = panel.querySelector('.sam-binding-item');
          const itemName = item.querySelector('.sam-binding-item-name');
          const tag = item.querySelector('.sam-binding-tag-small');
          const label = tag.querySelector('.sam-binding-tag-label');
          const actionOne = panel.querySelector('.sam-binding-item[data-action-id="action1"]');
          const actionOneTags = actionOne.querySelector('.sam-binding-item-tags');
          const actionOneTag = actionOne.querySelector('.sam-binding-tag-small');
          const actionOneAddButton = actionOne.querySelector('.sam-add-binding-btn');
          const actionOneTagLabels = Array.from(actionOne.querySelectorAll('.sam-binding-tag-label'))
            .map((node) => node.textContent);
          const actionOneTagsRect = actionOneTags.getBoundingClientRect();
          const actionOneTagRect = actionOneTag.getBoundingClientRect();
          const actionOneAddRect = actionOneAddButton.getBoundingClientRect();
          const actionRects = Array.from(panel.querySelectorAll('.actions-for-drop .sam-binding-item'))
            .map((node) => {
              const rect = node.getBoundingClientRect();
              const tagsRect = node.querySelector('.sam-binding-item-tags').getBoundingClientRect();
              return {
                top: rect.top,
                bottom: rect.bottom,
                height: rect.height,
                tagsTop: tagsRect.top,
                tagsBottom: tagsRect.bottom
              };
            });
          const containerRect = container.getBoundingClientRect();
          const itemRect = item.getBoundingClientRect();
          const tagRect = tag.getBoundingClientRect();
          return {
            containerClientWidth: container.clientWidth,
            containerScrollWidth: container.scrollWidth,
            sectionWidths: sections.map((section) => section.getBoundingClientRect().width),
            itemRight: itemRect.right,
            containerRight: containerRect.right,
            itemNameClientWidth: itemName.clientWidth,
            itemNameScrollWidth: itemName.scrollWidth,
            tagWidth: tagRect.width,
            tagLabelClientWidth: label.clientWidth,
            tagLabelScrollWidth: label.scrollWidth,
            tagActive: label.classList.contains('neko-marquee-active'),
            actionOneTagLabels,
            actionOneTagsHeight: actionOneTagsRect.height,
            actionOneTagWidth: actionOneTagRect.width,
            actionOneTagHeight: actionOneTagRect.height,
            actionOneTagTop: actionOneTagRect.top,
            actionOneTagBottom: actionOneTagRect.bottom,
            actionOneAddWidth: actionOneAddRect.width,
            actionOneAddHeight: actionOneAddRect.height,
            actionOneAddTop: actionOneAddRect.top,
            actionOneAddBottom: actionOneAddRect.bottom,
            actionRects
          };
        }
        """
    )

    assert metrics["containerScrollWidth"] <= metrics["containerClientWidth"] + 1
    assert all(width > 0 for width in metrics["sectionWidths"])
    assert metrics["itemRight"] <= metrics["containerRight"] + 1
    assert metrics["itemNameScrollWidth"] > metrics["itemNameClientWidth"]
    assert metrics["tagWidth"] <= 140
    assert metrics["tagLabelScrollWidth"] > metrics["tagLabelClientWidth"]
    assert metrics["tagActive"]
    assert any("桃源恋歌" in label for label in metrics["actionOneTagLabels"])
    assert metrics["actionOneTagsHeight"] >= 20
    assert metrics["actionOneTagWidth"] > 0
    assert metrics["actionOneTagHeight"] > 0
    assert metrics["actionOneAddWidth"] > 0
    assert metrics["actionOneAddHeight"] > 0
    assert abs(metrics["actionOneAddTop"] - metrics["actionOneTagTop"]) <= 1
    assert abs(metrics["actionOneAddBottom"] - metrics["actionOneTagBottom"]) <= 1
    for rect in metrics["actionRects"]:
        assert rect["height"] >= 60
        assert rect["tagsTop"] >= rect["top"]
        assert rect["tagsBottom"] <= rect["bottom"] + 1
    for current, following in zip(metrics["actionRects"], metrics["actionRects"][1:]):
        assert current["bottom"] <= following["top"] + 1


@pytest.mark.frontend
def test_jukebox_manager_binding_lists_fill_available_content_height(mock_page: Page):
    mock_page.set_viewport_size({"width": 1000, "height": 620})
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
    metrics = mock_page.evaluate(
        """
        () => {
          const SAM = window.Jukebox.SongActionManager;
          const style = document.createElement('style');
          style.textContent = SAM.getStyles();
          document.head.appendChild(style);

          const songs = {};
          const actions = {};
          const bindings = {};
          for (let i = 1; i <= 12; i += 1) {
            songs[`song${i}`] = { name: `Song ${i}`, artist: 'Artist', visible: true };
            actions[`action${i}`] = { name: `Action ${i}`, format: i % 2 ? 'vmd' : 'vrma' };
            bindings[`song${i}`] = { [`action${i}`]: { offset: 0 } };
          }
          SAM.data = { songs, actions, bindings };
          SAM.selectedSongs = new Set();
          SAM.selectedActions = new Set();
          SAM.bindingSelectedSongs = new Set();
          SAM.bindingSelectedActions = new Set();
          SAM.bindingSourceSongs = new Set();
          SAM.bindingSourceActions = new Set();
          SAM.updateSelectionInfo = function() {};

          const manager = SAM.create();
          document.body.appendChild(manager);
          manager.style.display = 'flex';
          manager.style.width = '940px';
          manager.style.height = '560px';
          manager.style.maxHeight = 'none';
          manager.querySelector('.sam-tab[data-tab="bindings"]').click();

          const content = manager.querySelector('.sam-content');
          const footer = manager.querySelector('.sam-footer');
          const container = manager.querySelector('.sam-bindings-container');
          const songsList = manager.querySelector('.songs-for-drop');
          const actionsList = manager.querySelector('.actions-for-drop');
          const contentRect = content.getBoundingClientRect();
          const footerRect = footer.getBoundingClientRect();
          const containerRect = container.getBoundingClientRect();
          const songsRect = songsList.getBoundingClientRect();
          const actionsRect = actionsList.getBoundingClientRect();
          return {
            contentHeight: contentRect.height,
            containerHeight: containerRect.height,
            songsHeight: songsRect.height,
            actionsHeight: actionsRect.height,
            contentBottom: contentRect.bottom,
            footerTop: footerRect.top,
            songsScrollHeight: songsList.scrollHeight,
            actionsScrollHeight: actionsList.scrollHeight
          };
        }
        """
    )

    assert metrics["contentHeight"] > 340
    assert metrics["containerHeight"] > 320
    assert metrics["songsHeight"] > 280
    assert metrics["actionsHeight"] > 280
    assert metrics["songsHeight"] > 200
    assert metrics["actionsHeight"] > 200
    assert metrics["contentBottom"] <= metrics["footerTop"] + 1
    assert metrics["songsScrollHeight"] > metrics["songsHeight"]
    assert metrics["actionsScrollHeight"] > metrics["actionsHeight"]


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
          panel.className = 'bindings-panel sam-panel active';
          panel.style.height = '260px';
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
          panel.className = 'bindings-panel sam-panel active';
          panel.style.height = '260px';
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
          panel.className = 'bindings-panel sam-panel active';
          panel.style.height = '260px';
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
