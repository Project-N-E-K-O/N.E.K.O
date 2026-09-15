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
        """() => !!(
            window.appState
            && window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
        )""",
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
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            // 注入一段以 assistant 结尾的历史 —— 这样「没发请求」不会被归因到
            // history 为空，而唯一归因到 overlay.hidden 这道关。
            host.setMessages([
                { id: 'u1', role: 'user', blocks: [{ type: 'text', text: 'hi' }] },
                { id: 'a1', role: 'assistant', blocks: [{ type: 'text', text: '在的呀' }] },
            ]);
            const overlay = document.getElementById('react-chat-window-overlay');
            if (overlay) overlay.hidden = true;
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
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            window.reactChatWindowHost.setGalgameModeEnabled(true, {
                persist: false,
                force: true
            });
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


@pytest.mark.frontend
def test_completed_icebreaker_handoff_seeds_visible_galgame_options(
    mock_page: Page, running_server: str
):
    galgame_payloads = []

    def _handle(route: Route):
        galgame_payloads.append(route.request.post_data_json)
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
        """() => !!(
            window.appState
            && window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
        )""",
        timeout=10000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            host.setMessages([
                {
                    id: 'ordinary-before-icebreaker',
                    role: 'assistant',
                    blocks: [{ type: 'text', text: '不应进入请求的旧对话' }]
                },
                {
                    id: 'icebreaker-user-before-final',
                    role: 'user',
                    blocks: [{ type: 'text', text: '不应进入请求的破冰对话' }]
                },
                {
                    id: 'icebreaker-assistant-final',
                    role: 'assistant',
                    blocks: [{ type: 'text', text: '破冰收尾台词' }]
                }
            ]);
            host.setIcebreakerChoicePrompt({
                sessionId: 'icebreaker-session-1',
                options: [{ choice: 'A', label: '最后一个选择' }]
            });
            document.getElementById('react-chat-window-overlay').hidden = false;
            window._realisticGeminiQueue = [];
            window._isProcessingRealisticQueue = false;
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
                detail: { source: 'new_user_icebreaker', timestamp: Date.now() }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(500)
    assert galgame_payloads == [], "普通破冰 turn-end 仍应被 GalGame 隔离"

    mock_page.evaluate(
        """
        () => {
            // 页面其余首启脚本可能在 domcontentloaded 后才真正拉起教程；在主张前
            // 再释放一次，避免测试把异步教程锁误判成交接失败。
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            window.reactChatWindowHost.setGalgameModeEnabled(true, {
                persist: false,
                force: true
            });
            document.getElementById('react-chat-window-overlay').hidden = false;
            window.dispatchEvent(new CustomEvent('neko:icebreaker-galgame-handoff', {
                detail: {
                    sessionId: 'icebreaker-session-1',
                    messageId: 'icebreaker-assistant-final'
                }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(1200)

    assert len(galgame_payloads) == 1
    assert galgame_payloads[0]["messages"] == [
        {"role": "assistant", "text": "破冰收尾台词"}
    ]


@pytest.mark.frontend
def test_full_chat_electron_bridge_waits_for_handoff_bubble_before_galgame(
    mock_page: Page, running_server: str
):
    galgame_payloads = []

    def _handle(route: Route):
        galgame_payloads.append(route.request.post_data_json)
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
    mock_page.goto(f"{running_server}/chat_full", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        """() => !!(
            window.appState
            && window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
            && window.__nekoIcebreakerBridgeReady
        )""",
        timeout=10000,
    )

    mock_page.evaluate(
        """
        () => {
            window.appState.lanlan_name = 'yui';
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            document.getElementById('react-chat-window-overlay').hidden = false;

            const timestamp = Date.now();
            const finalMessage = {
                id: 'icebreaker-assistant-full-final',
                role: 'assistant',
                blocks: [{ type: 'text', text: '完整聊天框破冰收尾台词' }],
                icebreaker: {
                    source: 'new_user_icebreaker',
                    sessionId: 'full-chat-session-1',
                    handoff: true
                }
            };
            window.dispatchEvent(new CustomEvent('neko:electron-icebreaker-bridge', {
                detail: {
                    action: 'icebreaker_append_chat_message',
                    lanlan_name: 'yui',
                    message: finalMessage,
                    timestamp
                }
            }));
            // Electron IPC sends this immediately after the append message. The
            // Full Chat bridge must delay it until appendMessage has committed.
            window.dispatchEvent(new CustomEvent('neko:electron-icebreaker-bridge', {
                detail: {
                    action: 'icebreaker_galgame_handoff',
                    lanlan_name: 'yui',
                    detail: {
                        sessionId: 'full-chat-session-1',
                        messageId: 'icebreaker-assistant-full-final'
                    },
                    timestamp: timestamp + 1
                }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(1500)

    assert len(galgame_payloads) == 1
    assert galgame_payloads[0]["messages"] == [
        {"role": "assistant", "text": "完整聊天框破冰收尾台词"}
    ]

    mock_page.evaluate(
        """
        () => {
            const timestamp = Date.now();
            const channel = new BroadcastChannel('neko_page_channel');
            window.__icebreakerTestBroadcastChannel = channel;
            channel.postMessage({
                action: 'icebreaker_append_chat_message',
                lanlan_name: 'yui',
                message: {
                    id: 'icebreaker-assistant-broadcast-final',
                    role: 'assistant',
                    blocks: [{ type: 'text', text: 'BroadcastChannel 收尾台词' }],
                    icebreaker: { source: 'new_user_icebreaker', handoff: true }
                },
                timestamp
            });
            channel.postMessage({
                action: 'icebreaker_galgame_handoff',
                lanlan_name: 'yui',
                detail: {
                    sessionId: 'broadcast-session-1',
                    messageId: 'icebreaker-assistant-broadcast-final'
                },
                timestamp: timestamp + 1
            });
        }
        """
    )
    mock_page.wait_for_timeout(1200)

    assert len(galgame_payloads) == 2
    assert galgame_payloads[1]["messages"] == [
        {"role": "assistant", "text": "BroadcastChannel 收尾台词"}
    ]


@pytest.mark.frontend
def test_hidden_icebreaker_handoff_is_consumed_when_chat_reopens(
    mock_page: Page, running_server: str
):
    galgame_payloads = []

    def _handle(route: Route):
        galgame_payloads.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "options": [{"label": "A", "text": "x"}]}',
        )

    mock_page.route("**/api/galgame/options", _handle)
    mock_page.goto(f"{running_server}/chat_full", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        """() => !!(
            window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
        )""",
        timeout=10000,
    )

    mock_page.evaluate(
        """
        () => {
            window.appState.lanlan_name = 'yui';
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            host.setMessages([{
                id: 'icebreaker-assistant-hidden-final',
                role: 'assistant',
                blocks: [{ type: 'text', text: '隐藏期间的破冰收尾' }]
            }]);
            document.getElementById('react-chat-window-overlay').hidden = true;
            window._realisticGeminiQueue = [];
            window._isProcessingRealisticQueue = false;
            window.dispatchEvent(new CustomEvent('neko:icebreaker-galgame-handoff', {
                detail: {
                    sessionId: 'icebreaker-hidden-session',
                    messageId: 'icebreaker-assistant-hidden-final'
                }
            }));
            host.appendMessage({
                id: 'yui-guide-after-hidden-handoff',
                role: 'assistant',
                source: 'yui_guide',
                blocks: [{ type: 'text', text: '不应使交接失效的引导消息' }]
            });
        }
        """
    )
    mock_page.wait_for_timeout(300)
    assert galgame_payloads == []

    mock_page.evaluate("() => window.reactChatWindowHost.openWindow()")
    mock_page.wait_for_timeout(1200)

    assert [payload["messages"] for payload in galgame_payloads] == [[
        {"role": "assistant", "text": "隐藏期间的破冰收尾"}
    ]]

    mock_page.evaluate("() => window.reactChatWindowHost.closeWindow()")
    mock_page.wait_for_timeout(350)
    mock_page.evaluate("() => window.reactChatWindowHost.openWindow()")
    mock_page.wait_for_timeout(1200)

    assert [payload["messages"] for payload in galgame_payloads] == [
        [{"role": "assistant", "text": "隐藏期间的破冰收尾"}],
        [{"role": "assistant", "text": "隐藏期间的破冰收尾"}],
    ]

    mock_page.evaluate(
        """() => window.dispatchEvent(new CustomEvent(
            'neko:electron-icebreaker-bridge',
            { detail: {
                action: 'icebreaker_reset_session_state',
                timestamp: Date.now(),
                reason: 'storage-maintenance-reload'
            } }
        ))"""
    )
    mock_page.evaluate("() => window.reactChatWindowHost.closeWindow()")
    mock_page.wait_for_timeout(350)
    mock_page.evaluate("() => window.reactChatWindowHost.openWindow()")
    mock_page.wait_for_timeout(1200)

    assert [payload["messages"] for payload in galgame_payloads] == [
        [{"role": "assistant", "text": "隐藏期间的破冰收尾"}],
        [{"role": "assistant", "text": "隐藏期间的破冰收尾"}],
    ]


@pytest.mark.frontend
def test_delayed_icebreaker_handoff_does_not_cross_a_newer_turn(
    mock_page: Page, running_server: str
):
    galgame_payloads = []

    def _handle(route: Route):
        galgame_payloads.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body='{"options": []}')

    mock_page.route("**/api/galgame/options", _handle)
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function("() => !!window.reactChatWindowHost", timeout=10000)
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            host.setMessages([
                {
                    id: 'icebreaker-assistant-delayed-final',
                    role: 'assistant',
                    blocks: [{ type: 'text', text: '已经过时的收尾' }]
                },
                {
                    id: 'ordinary-user-after-handoff',
                    role: 'user',
                    blocks: [{ type: 'text', text: '后续消息' }]
                }
            ]);
            document.getElementById('react-chat-window-overlay').hidden = false;
            window._realisticGeminiQueue = [];
            window._isProcessingRealisticQueue = false;
            window.dispatchEvent(new CustomEvent('neko:icebreaker-galgame-handoff', {
                detail: {
                    sessionId: 'icebreaker-delayed-session',
                    messageId: 'icebreaker-assistant-delayed-final'
                }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(700)

    assert galgame_payloads == []


@pytest.mark.frontend
def test_inflight_icebreaker_handoff_is_aborted_by_newer_assistant(
    mock_page: Page, running_server: str
):
    mock_page.goto(f"{running_server}/chat_full", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        """() => !!(
            window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
        )""",
        timeout=10000,
    )

    mock_page.evaluate(
        """() => {
            window.appState.lanlan_name = 'yui';
            window.__resolveIcebreakerOptions = null;
            const nativeFetch = window.fetch.bind(window);
            window.fetch = (url, options) => {
                if (!String(url).includes('/api/galgame/options')) {
                    return nativeFetch(url, options);
                }
                return new Promise((resolve) => {
                    window.__resolveIcebreakerOptions = resolve;
                });
            };
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            host.setMessages([{
                id: 'icebreaker-assistant-inflight-final',
                role: 'assistant',
                blocks: [{ type: 'text', text: '即将过时的破冰收尾' }]
            }]);
            document.getElementById('react-chat-window-overlay').hidden = false;
            window._realisticGeminiQueue = [];
            window._isProcessingRealisticQueue = false;
            window.dispatchEvent(new CustomEvent('neko:icebreaker-galgame-handoff', {
                detail: {
                    sessionId: 'icebreaker-inflight-session',
                    messageId: 'icebreaker-assistant-inflight-final'
                }
            }));
        }"""
    )
    mock_page.wait_for_function("() => typeof window.__resolveIcebreakerOptions === 'function'")
    mock_page.evaluate(
        """() => {
            window.reactChatWindowHost.appendMessage({
                id: 'ordinary-assistant-after-handoff',
                role: 'assistant',
                blocks: [{ type: 'text', text: '新的主动对话' }]
            });
            window.__resolveIcebreakerOptions({
                ok: true,
                json: () => Promise.resolve({
                    options: [{ label: 'A', text: '不应出现的旧选项' }]
                })
            });
        }"""
    )
    mock_page.wait_for_timeout(300)

    assert mock_page.locator(".composer-galgame-option").count() == 0
    assert "不应出现的旧选项" not in mock_page.locator("body").inner_text()


@pytest.mark.frontend
@pytest.mark.parametrize("append_failure", ["throw", "reject", "null"])
def test_full_chat_bridge_drops_handoff_when_final_bubble_append_fails(
    mock_page: Page, running_server: str, append_failure: str
):
    galgame_payloads = []

    def _handle(route: Route):
        galgame_payloads.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body='{"options": []}')

    mock_page.route("**/api/galgame/options", _handle)
    mock_page.goto(f"{running_server}/chat_full", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        """() => !!(
            window.reactChatWindowHost
            && window.reactChatWindowHost.isMounted
            && window.reactChatWindowHost.isMounted()
            && window.__nekoIcebreakerBridgeReady
        )""",
        timeout=10000,
    )

    mock_page.evaluate(
        """(failure) => {
            window.appState.lanlan_name = 'yui';
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' }
            }));
            const host = window.reactChatWindowHost;
            host.setGalgameModeEnabled(true, { persist: false, force: true });
            document.getElementById('react-chat-window-overlay').hidden = false;
            host.appendMessage = () => {
                if (failure === 'throw') throw new Error('sync append failure');
                if (failure === 'reject') return Promise.reject(new Error('async append failure'));
                return null;
            };
            const messageId = `icebreaker-assistant-failed-${failure}`;
            const timestamp = Date.now();
            window.dispatchEvent(new CustomEvent('neko:electron-icebreaker-bridge', {
                detail: {
                    action: 'icebreaker_append_chat_message',
                    lanlan_name: 'yui',
                    message: {
                        id: messageId,
                        role: 'assistant',
                        blocks: [{ type: 'text', text: '不会成功追加' }]
                    },
                    timestamp
                }
            }));
            window.dispatchEvent(new CustomEvent('neko:electron-icebreaker-bridge', {
                detail: {
                    action: 'icebreaker_galgame_handoff',
                    lanlan_name: 'yui',
                    detail: { sessionId: 'failed-session', messageId },
                    timestamp: timestamp + 1
                }
            }));
        }""",
        append_failure,
    )
    mock_page.wait_for_timeout(700)

    assert galgame_payloads == []
