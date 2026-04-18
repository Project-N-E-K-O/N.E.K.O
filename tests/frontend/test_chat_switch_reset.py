import pytest
from playwright.sync_api import Page


def _open_chat_page(mock_page: Page, running_server: str) -> None:
    mock_page.add_init_script(
        "window.localStorage.setItem('neko_tutorial_settings', 'seen')"
    )
    mock_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.handleCatgirlSwitch"
        " && window.connectWebSocket"
        " && window.reactChatWindowHost"
        " && typeof window.appendMessage === 'function'"
        " && typeof window.createGeminiBubble === 'function'"
        " && typeof window._tryFlushPendingHostMessages === 'function'"
        " && typeof window._resetReactChatSwitchState === 'function'"
        " && window.appState"
    )


@pytest.mark.frontend
def test_chat_switch_clears_dom_react_and_pending_adapter_messages(
    mock_page: Page,
    running_server: str,
):
    _open_chat_page(mock_page, running_server)

    result = mock_page.evaluate(
        """async () => {
            const resp = await fetch('/api/characters');
            const data = await resp.json();
            const names = Object.keys((data && data['猫娘']) || {});
            if (names.length < 2) {
                return { error: 'not_enough_catgirls', names };
            }

            const oldCatgirl = names[0];
            const newCatgirl = names.find((name) => name !== oldCatgirl) || '';

            window.lanlan_config = window.lanlan_config || {};
            window.lanlan_config.lanlan_name = oldCatgirl;
            window._currentCatgirl = oldCatgirl;
            window.currentCatgirl = oldCatgirl;

            if (window.appState.heartbeatInterval) {
                clearInterval(window.appState.heartbeatInterval);
                window.appState.heartbeatInterval = null;
            }
            if (window.appState.autoReconnectTimeoutId) {
                clearTimeout(window.appState.autoReconnectTimeoutId);
                window.appState.autoReconnectTimeoutId = null;
            }
            try {
                if (window.appState.socket && typeof window.appState.socket.close === 'function') {
                    window.appState.socket.close();
                }
            } catch (_) {}
            window.appState.socket = null;
            window.appState.isRecording = false;
            window.appState.isTextSessionActive = true;
            window.appState.incomingAudioEpoch = 7;
            window.appState.incomingAudioBlobQueue = [
                { epoch: 7, speechId: 'old-speech', blob: new Blob(['old-audio']) }
            ];
            window.appState.pendingAudioChunkMetaQueue = [
                { epoch: 7, speechId: 'old-speech' }
            ];

            window.__switchConnectCalls = 0;
            window.__switchClearAudioCalls = 0;
            window.showStatusToast = () => {};
            window.clearAudioQueue = async () => {
                window.__switchClearAudioCalls += 1;
            };
            window.connectWebSocket = () => {
                window.__switchConnectCalls += 1;
            };
            window.stopRecording = () => {};
            window.syncFloatingMicButtonState = () => {};
            window.syncFloatingScreenButtonState = () => {};
            window.invalidatePendingMusicSearch = () => {};

            window.reactChatWindowHost.clearMessages();
            window._resetReactChatSwitchState();

            const host = window.reactChatWindowHost;
            const originalAppendMessage = host.appendMessage;
            window.__postSwitchForcedFlushAppends = 0;
            host.appendMessage = (message) => {
                window.__postSwitchForcedFlushAppends += 1;
                return originalAppendMessage.call(host, message);
            };

            host.appendMessage({
                id: 'old-react-message',
                role: 'assistant',
                author: oldCatgirl,
                time: '12:00:00',
                blocks: [{ type: 'text', text: 'old react message' }],
                status: 'sent'
            });
            window.__postSwitchForcedFlushAppends = 0;

            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
                const oldBubble = document.createElement('div');
                oldBubble.className = 'message gemini';
                oldBubble.textContent = 'old dom bubble';
                chatContainer.appendChild(oldBubble);
            }

            window.reactChatWindowHost = null;
            const queuedRef = window.createGeminiBubble('queued stale message');
            window.reactChatWindowHost = host;
            const queuedMessageId = queuedRef && queuedRef.dataset
                ? queuedRef.dataset.reactChatMessageId || ''
                : '';
            const preSwitchReactMessages = host.getState().messages.length;

            await window.handleCatgirlSwitch(newCatgirl, oldCatgirl);
            window._tryFlushPendingHostMessages();
            await new Promise((resolve) => setTimeout(resolve, 0));
            host.appendMessage = originalAppendMessage;

            return {
                oldCatgirl,
                newCatgirl,
                currentCatgirl: window.lanlan_config.lanlan_name,
                queuedMessageId,
                preSwitchReactMessages,
                postSwitchForcedFlushAppends: window.__postSwitchForcedFlushAppends,
                reactMessages: window.reactChatWindowHost.getState().messages.length,
                domMessages: document.querySelectorAll('#chatContainer .message').length,
                connectCalls: window.__switchConnectCalls,
                clearAudioCalls: window.__switchClearAudioCalls,
                isTextSessionActive: window.appState.isTextSessionActive,
                incomingAudioEpoch: window.appState.incomingAudioEpoch,
                incomingAudioBlobQueue: window.appState.incomingAudioBlobQueue.length,
                pendingAudioChunkMetaQueue: window.appState.pendingAudioChunkMetaQueue.length
            };
        }"""
    )

    assert "error" not in result, result
    assert result["currentCatgirl"] == result["newCatgirl"]
    assert result["queuedMessageId"]
    assert result["preSwitchReactMessages"] == 1
    assert result["postSwitchForcedFlushAppends"] == 0
    assert result["reactMessages"] == 0
    assert result["domMessages"] == 0
    assert result["connectCalls"] == 1
    assert result["clearAudioCalls"] == 1
    assert result["isTextSessionActive"] is False
    assert result["incomingAudioEpoch"] == 8
    assert result["incomingAudioBlobQueue"] == 0
    assert result["pendingAudioChunkMetaQueue"] == 0

    mock_page.wait_for_timeout(350)
    after_retry = mock_page.evaluate(
        """() => ({
            reactMessages: window.reactChatWindowHost.getState().messages.length,
            domMessages: document.querySelectorAll('#chatContainer .message').length
        })"""
    )
    assert after_retry["reactMessages"] == 0
    assert after_retry["domMessages"] == 0


@pytest.mark.frontend
def test_stale_socket_onmessage_does_not_replay_old_messages_or_audio(
    mock_page: Page,
    running_server: str,
):
    _open_chat_page(mock_page, running_server)

    result = mock_page.evaluate(
        """async () => {
            const resp = await fetch('/api/characters');
            const data = await resp.json();
            const names = Object.keys((data && data['猫娘']) || {});
            if (names.length < 2) {
                return { error: 'not_enough_catgirls', names };
            }

            if (window.appState.heartbeatInterval) {
                clearInterval(window.appState.heartbeatInterval);
                window.appState.heartbeatInterval = null;
            }
            if (window.appState.autoReconnectTimeoutId) {
                clearTimeout(window.appState.autoReconnectTimeoutId);
                window.appState.autoReconnectTimeoutId = null;
            }
            try {
                if (window.appState.socket && typeof window.appState.socket.close === 'function') {
                    window.appState.socket.close();
                }
            } catch (_) {}
            window.appState.socket = null;

            window.reactChatWindowHost.clearMessages();
            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
            }

            window.__audioEnqueueCount = 0;
            window.enqueueIncomingAudioBlob = () => {
                window.__audioEnqueueCount += 1;
            };

            window.__wsInstances = [];
            function FakeWebSocket(url) {
                this.url = url;
                this.readyState = 0;
                this.sent = [];
            }
            FakeWebSocket.prototype.addEventListener = function () {};
            FakeWebSocket.prototype.send = function (payload) {
                this.sent.push(payload);
            };
            FakeWebSocket.prototype.close = function () {
                this.readyState = 3;
            };

            function FakeWebSocketCtor(url) {
                const ws = new FakeWebSocket(url);
                window.__wsInstances.push(ws);
                return ws;
            }
            FakeWebSocketCtor.OPEN = 1;
            FakeWebSocketCtor.CONNECTING = 0;
            FakeWebSocketCtor.CLOSING = 2;
            FakeWebSocketCtor.CLOSED = 3;
            FakeWebSocketCtor.prototype = FakeWebSocket.prototype;
            window.WebSocket = FakeWebSocketCtor;

            window.lanlan_config = window.lanlan_config || {};
            window.lanlan_config.lanlan_name = names[0];
            window.connectWebSocket();
            const staleSocket = window.__wsInstances[0];

            window.lanlan_config.lanlan_name = names[1];
            window.connectWebSocket();
            const liveSocket = window.__wsInstances[1];

            staleSocket.onmessage({
                data: JSON.stringify({
                    type: 'gemini_response',
                    text: 'stale should be ignored。',
                    isNewMessage: true
                })
            });
            staleSocket.onmessage({ data: new Blob(['stale-audio']) });

            liveSocket.onmessage({
                data: JSON.stringify({
                    type: 'gemini_response',
                    text: 'live should remain。',
                    isNewMessage: true
                })
            });
            liveSocket.onmessage({
                data: JSON.stringify({
                    type: 'system',
                    data: 'turn end'
                })
            });
            liveSocket.onmessage({ data: new Blob(['live-audio']) });

            const snapshot = window.reactChatWindowHost.getState();
            const firstMessage = snapshot.messages[0] || null;
            const firstText = firstMessage && Array.isArray(firstMessage.blocks)
                ? firstMessage.blocks
                    .map((block) => block && block.type === 'text' ? block.text : '')
                    .join('')
                : '';

            return {
                instances: window.__wsInstances.length,
                messageCount: snapshot.messages.length,
                firstText,
                audioEnqueueCount: window.__audioEnqueueCount,
                currentSocketUrl: window.appState.socket && window.appState.socket.url,
                staleSocketUrl: staleSocket.url,
                liveSocketUrl: liveSocket.url
            };
        }"""
    )

    assert "error" not in result, result
    assert result["instances"] == 2
    assert result["messageCount"] == 1
    assert result["firstText"] == "live should remain。"
    assert result["audioEnqueueCount"] == 1
    assert result["currentSocketUrl"] == result["liveSocketUrl"]
    assert result["currentSocketUrl"] != result["staleSocketUrl"]
