"""GalGame 模式在语音对话下不空耗 token 的前端不变量。

galgame 模式默认开启（readGalgameModePreference 在无 localStorage 时返回
true）。「语音对话 / voice-only」= 用户没有打开 React 聊天窗口，此时
overlay.hidden 为真。每轮 assistant 回复结束会派发 `neko-assistant-turn-end`，
其 handler 必须因 overlay.hidden **同步早退**，不得 POST /api/galgame/options
—— 否则就是在没人能看到、点击选项的情况下白烧 summary 档 token。

参考 tests/frontend/test_avatar_reaction_bubble.py 的 turn-end 派发写法。
"""

import pytest
from playwright.sync_api import Page, Route


@pytest.mark.frontend
def test_galgame_options_gated_by_chat_window_visibility(
    mock_page: Page, running_server: str
):
    galgame_requests = []

    def _handle(route: Route):
        galgame_requests.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"success": true, "options": ['
                '{"label": "A", "text": "x"},'
                '{"label": "B", "text": "y"},'
                '{"label": "C", "text": "z"}]}'
            ),
        )

    mock_page.route("**/api/galgame/options", _handle)

    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.reactChatWindowHost)",
        timeout=10000,
    )

    pre = mock_page.evaluate(
        """
        () => {
            // 解除可能的首启教程锁，让 galgame 能真正启用 —— 否则测的是
            // 「galgame off 不发」而非「overlay hidden 不发」，失去意义。
            ['neko:tutorial-completed', 'neko:tutorial-skipped'].forEach((name) => {
                window.dispatchEvent(new CustomEvent(name, { detail: { page: 'home' } }));
            });
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false });
            // 注入一段以 assistant 结尾的历史 —— 这样「没发请求」不会被归因到
            // history 为空，而唯一归因到 overlay.hidden 这道关。
            host.setMessages([
                { id: 'u1', role: 'user', blocks: [{ type: 'text', text: 'hi' }] },
                { id: 'a1', role: 'assistant', blocks: [{ type: 'text', text: '在的呀' }] },
            ]);
            const overlay = document.getElementById('react-chat-window-overlay');
            return {
                galgameEnabled: host.isGalgameModeEnabled(),
                overlayHidden: !overlay || overlay.hidden,
            };
        }
        """
    )
    assert pre["galgameEnabled"] is True, "前置失败：galgame 未启用，测试无意义"
    assert pre["overlayHidden"] is True, "前置失败：overlay 应处于隐藏（voice-only）态"

    # —— 主张：voice-only（overlay hidden）下 turn-end 不拉取选项 ——
    mock_page.evaluate(
        """
        () => window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
            detail: { turnId: 'voice-turn-1', source: 'test', timestamp: Date.now() }
        }))
        """
    )
    # overlay.hidden 检查是同步的，但 fetch 走 microtask；给足时间确认请求确实没发。
    mock_page.wait_for_timeout(800)
    assert galgame_requests == [], (
        f"voice-only 路径不应触发 galgame 选项生成，实际发出: {galgame_requests}"
    )

    # —— 对照：揭开 overlay 后，同一个 turn-end 会拉取选项 ——
    # 证明上面那道关的开关确实是 overlay 可见性，而非别的原因导致没请求。
    mock_page.evaluate(
        """
        () => {
            // 直接揭开 overlay（不走完整 openWindow，避免 React bundle 异步 mount
            // 的时序）—— turn-end handler 只读 overlay.hidden。
            document.getElementById('react-chat-window-overlay').hidden = false;
            // 清空 realistic 字幕队列，让 waitForAssistantBubblesFlushed 立即放行。
            window._realisticGeminiQueue = [];
            window._isProcessingRealisticQueue = false;
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
                detail: { turnId: 'visible-turn-1', source: 'test', timestamp: Date.now() }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(1500)
    assert any("/api/galgame/options" in url for url in galgame_requests), (
        f"对照失败：overlay 可见时 turn-end 应触发选项生成，实际: {galgame_requests}"
    )
