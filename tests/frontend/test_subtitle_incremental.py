from pathlib import Path

import pytest
from playwright.sync_api import Page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _open_subtitle_harness(mock_page: Page, body_class: str, body_html: str) -> None:
    mock_page.route(
        "**/subtitle-harness",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                "<!doctype html><html><head></head>"
                f"<body class=\"{body_class}\">{body_html}</body></html>"
            ),
        ),
    )
    mock_page.goto("http://neko.test/subtitle-harness")


@pytest.mark.frontend
def test_subtitle_incremental_translation_starts_when_sentence_punctuation_arrives(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 450));
            return {
                text: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests,
            };
        }
        """
    )

    assert result["text"] == "你好世界。"
    assert [request["text"] for request in result["requests"]] == ["Hello world."]


@pytest.mark.frontend
def test_subtitle_streaming_does_not_show_original_text_while_translation_is_pending(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__resolveTranslate = null;
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    await new Promise((resolve) => { window.__resolveTranslate = resolve; });
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 350));
            const beforeResolve = document.getElementById('subtitle-text').textContent;
            window.__resolveTranslate();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '你好世界。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('translated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                beforeResolve,
                afterResolve: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["beforeResolve"] == ""
    assert result["afterResolve"] == "你好世界。"


@pytest.mark.frontend
def test_subtitle_translation_failure_does_not_fall_back_to_original_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.fetch = async (url) => {
                const requestUrl = String(url);
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    return new Response(JSON.stringify({ success: false }), {
                        status: 500,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 450));
            await window.translateAndShowSubtitle('Hello world.');
            return document.getElementById('subtitle-text').textContent;
        }
        """
    )

    assert result == ""


@pytest.mark.frontend
def test_subtitle_window_height_uses_content_bounds_not_dropdown_height(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="20" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="dragAnywhere">整体拖动</span>
                    <label class="subtitle-settings-switch"><input type="checkbox" id="subtitle-drag-mode-toggle"><span class="subtitle-settings-track"></span></label>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="size">大小</span>
                    <div class="subtitle-size-group">
                        <button type="button" class="subtitle-size-btn" data-size="small">小</button>
                        <button type="button" class="subtitle-size-btn active" data-size="medium">中</button>
                        <button type="button" class="subtitle-size-btn" data-size="large">大</button>
                    </div>
                </div>
            </div>
            <div id="subtitle-drag-handle"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleSizes = [];
            window.localStorage.setItem('subtitleDragAnywhere', 'true');
            window.nekoSubtitle = {
                setSize: (width, height) => window.__subtitleSizes.push({ width, height }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const emptySize = window.__subtitleSizes[window.__subtitleSizes.length - 1];
            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    transcript: '这是一段很长很长的翻译字幕，用来测试窗口高度会按内容增长，但是不会超过中号字幕的最大高度。'.repeat(8),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const longSize = window.__subtitleSizes[window.__subtitleSizes.length - 1];
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const panelOpenSize = window.__subtitleSizes[window.__subtitleSizes.length - 1];
            const displayRect = document.getElementById('subtitle-display').getBoundingClientRect();
            const scrollRect = document.getElementById('subtitle-scroll').getBoundingClientRect();
            const settingsBtnRect = document.getElementById('subtitle-settings-btn').getBoundingClientRect();
            const dragHandleRect = document.getElementById('subtitle-drag-handle').getBoundingClientRect();
            const panelRect = document.getElementById('subtitle-settings-panel').getBoundingClientRect();
            const displayStyle = getComputedStyle(document.getElementById('subtitle-display'));
            const scrollStyle = getComputedStyle(document.getElementById('subtitle-scroll'));
            const scrollThumbStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-thumb');
            const scrollBarStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar');
            const textStyle = getComputedStyle(document.getElementById('subtitle-text'));
            return {
                emptySize,
                longSize,
                panelOpenSize,
                displayHeight: displayRect.height,
                scrollHeight: scrollRect.height,
                scrollRight: scrollRect.right,
                settingsBtnLeft: settingsBtnRect.left,
                dragHandleLeft: dragHandleRect.left,
                panelBottom: panelRect.bottom,
                overlapsVertically: panelRect.bottom > scrollRect.top && panelRect.top < scrollRect.bottom,
                displayOverflow: displayStyle.overflowY,
                scrollOverflow: scrollStyle.overflowY,
                scrollPointerEvents: scrollStyle.pointerEvents,
                scrollBarWidth: scrollStyle.scrollbarWidth,
                scrollBarColor: scrollStyle.scrollbarColor,
                scrollBarGutter: scrollStyle.scrollbarGutter,
                webkitScrollBarWidth: scrollBarStyle.width,
                scrollThumbBackground: scrollThumbStyle.backgroundColor,
                textMarginRight: textStyle.marginRight,
            };
        }
        """
    )

    assert result["emptySize"]["height"] == 40
    assert result["longSize"]["height"] <= 160
    assert result["longSize"]["height"] >= 40
    assert result["panelOpenSize"]["height"] >= result["panelBottom"]
    assert result["overlapsVertically"] is False
    assert result["displayOverflow"] == "visible"
    assert result["scrollOverflow"] == "auto"
    assert result["scrollPointerEvents"] == "auto"
    assert result["scrollRight"] <= result["settingsBtnLeft"] - 6
    assert result["scrollRight"] <= result["dragHandleLeft"] - 6
    assert result["scrollBarWidth"] == "thin"
    assert "rgba" in result["scrollBarColor"]
    assert result["scrollBarGutter"] == "stable"
    assert result["webkitScrollBarWidth"] == "4px"
    assert result["scrollThumbBackground"] != "rgba(0, 0, 0, 0)"
    assert result["textMarginRight"] == "8px"


@pytest.mark.frontend
def test_web_subtitle_settings_panel_does_not_overlap_subtitle_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="20" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
            <div id="subtitle-drag-handle"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleDragAnywhere', 'true');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({ host: 'web' });
            shared.applySubtitlePreset(document.getElementById('subtitle-display'), 'medium', { host: 'web' });
            document.getElementById('subtitle-text').textContent =
                'Hmph, you persistent idiot. You and now you are hooked, huh?';
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const scrollRect = document.getElementById('subtitle-scroll').getBoundingClientRect();
            const settingsBtnRect = document.getElementById('subtitle-settings-btn').getBoundingClientRect();
            const dragHandleRect = document.getElementById('subtitle-drag-handle').getBoundingClientRect();
            const panelRect = document.getElementById('subtitle-settings-panel').getBoundingClientRect();
            const displayStyle = getComputedStyle(document.getElementById('subtitle-display'));
            const scrollStyle = getComputedStyle(document.getElementById('subtitle-scroll'));
            const scrollThumbStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-thumb');
            const scrollBarStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar');
            const textStyle = getComputedStyle(document.getElementById('subtitle-text'));
            return {
                scrollTop: scrollRect.top,
                scrollBottom: scrollRect.bottom,
                scrollRight: scrollRect.right,
                settingsBtnLeft: settingsBtnRect.left,
                dragHandleLeft: dragHandleRect.left,
                panelTop: panelRect.top,
                panelBottom: panelRect.bottom,
                overlapsVertically: panelRect.bottom > scrollRect.top && panelRect.top < scrollRect.bottom,
                panelHidden: document.getElementById('subtitle-settings-panel').classList.contains('hidden'),
                displayOverflow: displayStyle.overflowY,
                scrollOverflow: scrollStyle.overflowY,
                scrollPointerEvents: scrollStyle.pointerEvents,
                scrollBarWidth: scrollStyle.scrollbarWidth,
                scrollBarColor: scrollStyle.scrollbarColor,
                scrollBarGutter: scrollStyle.scrollbarGutter,
                webkitScrollBarWidth: scrollBarStyle.width,
                scrollThumbBackground: scrollThumbStyle.backgroundColor,
                textMarginRight: textStyle.marginRight,
            };
        }
        """
    )

    assert result["panelHidden"] is False
    assert result["overlapsVertically"] is False
    assert result["displayOverflow"] == "visible"
    assert result["scrollOverflow"] == "auto"
    assert result["scrollPointerEvents"] == "auto"
    assert result["scrollRight"] <= result["settingsBtnLeft"] - 6
    assert result["scrollRight"] <= result["dragHandleLeft"] - 6
    assert result["scrollBarWidth"] == "thin"
    assert "rgba" in result["scrollBarColor"]
    assert result["scrollBarGutter"] == "stable"
    assert result["webkitScrollBarWidth"] == "4px"
    assert result["scrollThumbBackground"] != "rgba(0, 0, 0, 0)"
    assert result["textMarginRight"] == "8px"


@pytest.mark.frontend
def test_web_subtitle_drag_mode_shows_handle_and_accepts_pointer_events_when_enabled(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text">可拖动字幕</span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <label class="subtitle-settings-switch">
                <input type="checkbox" id="subtitle-drag-mode-toggle">
                <span class="subtitle-settings-track"></span>
            </label>
            <div id="subtitle-drag-handle"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleDragAnywhere', 'true');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const dragHandle = document.getElementById('subtitle-drag-handle');
            const displayPointerEventsWhenEnabled = getComputedStyle(display).pointerEvents;
            const dragHandleDisplayWhenEnabled = getComputedStyle(dragHandle).display;
            const handleRect = dragHandle.getBoundingClientRect();
            dragHandle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: handleRect.left + 4,
                clientY: handleRect.top + 4,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: handleRect.left + 20,
                clientY: handleRect.top + 20,
            }));
            const draggingAfterMove = display.classList.contains('dragging');
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            window.nekoSubtitleShared.updateSettings({ subtitleDragAnywhere: false }, {
                source: 'test-toggle-off',
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                dragAnywhere: display.classList.contains('drag-anywhere'),
                displayPointerEventsWhenEnabled,
                dragHandleDisplayWhenEnabled,
                draggingAfterMove,
                dragHandleDisplayWhenDisabled: getComputedStyle(dragHandle).display,
            };
        }
        """
    )

    assert result["dragHandleDisplayWhenEnabled"] != "none"
    assert result["displayPointerEventsWhenEnabled"] == "auto"
    assert result["draggingAfterMove"] is True
    assert result["dragAnywhere"] is False
    assert result["dragHandleDisplayWhenDisabled"] == "none"
