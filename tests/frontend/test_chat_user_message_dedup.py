import pytest
from playwright.sync_api import Page


def _open_react_chat_page(mock_page: Page, running_server: str) -> None:
    mock_page.add_init_script(
        "window.localStorage.setItem('neko_tutorial_settings', 'seen')"
    )
    mock_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost"
        " && window.appButtons"
        " && window.appChat"
        " && window.appState"
        " && typeof window.sendTextPayload === 'function'"
    )
    mock_page.evaluate("() => window.reactChatWindowHost.openWindow()")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted && window.reactChatWindowHost.isMounted()"
        " && !!document.querySelector('.composer-input')"
    )


def _install_chat_send_harness(
    mock_page: Page,
    *,
    fail_session_start: bool = False,
    resolve_delay_ms: int = 300,
) -> None:
    mock_page.evaluate(
        """({ failSessionStart, resolveDelayMs }) => {
            window.master_display_name = 'Alice';
            window.master_name = 'Alice';
            window.lanlan_config = window.lanlan_config || {};
            window.lanlan_config.master_display_name = 'Alice';
            window.lanlan_config.master_name = 'Alice';

            window.showStatusToast = () => {};
            window.hideVoicePreparingToast = () => {};
            window.resetProactiveChatBackoff = () => {};
            window.hasAnyChatModeEnabled = () => false;
            window.showCurrentModel = async () => {};
            window.checkAndUnlockFirstDialogueAchievement = () => {};
            window.appChat.ensureUserDisplayName = async () => 'Alice';

            window.__chatTest = {
                failSessionStart,
                resolveDelayMs,
                sentPayloads: [],
                fireSessionStart: null
            };

            window.appState.isTextSessionActive = false;
            window.appState.proactiveChatEnabled = false;
            window.appState.sessionStartedResolver = null;
            window.appState.sessionStartedRejecter = null;
            window.sessionTimeoutId = null;

            if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearMessages === 'function') {
                window.reactChatWindowHost.clearMessages();
            }
            if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerAttachments === 'function') {
                window.reactChatWindowHost.setComposerAttachments([]);
            }

            const socket = {
                readyState: WebSocket.OPEN,
                sent: [],
                send(payload) {
                    const parsed = JSON.parse(payload);
                    this.sent.push(parsed);
                    window.__chatTest.sentPayloads.push(parsed);
                    if (parsed.action === 'start_session') {
                        if (window.__chatTest.resolveDelayMs < 0) {
                            window.__chatTest.fireSessionStart = () => {
                                if (window.appState.sessionStartedResolver) {
                                    const resolver = window.appState.sessionStartedResolver;
                                    window.appState.sessionStartedResolver = null;
                                    window.appState.sessionStartedRejecter = null;
                                    resolver();
                                }
                            };
                            return;
                        }
                        setTimeout(() => {
                            if (window.__chatTest.failSessionStart) {
                                if (window.appState.sessionStartedRejecter) {
                                    const rejecter = window.appState.sessionStartedRejecter;
                                    window.appState.sessionStartedResolver = null;
                                    window.appState.sessionStartedRejecter = null;
                                    rejecter(new Error('session init failed'));
                                }
                                return;
                            }
                            if (window.appState.sessionStartedResolver) {
                                const resolver = window.appState.sessionStartedResolver;
                                window.appState.sessionStartedResolver = null;
                                window.appState.sessionStartedRejecter = null;
                                resolver();
                            }
                        }, window.__chatTest.resolveDelayMs);
                    }
                },
                close() {
                    this.readyState = WebSocket.CLOSED;
                }
            };

            window.appState.socket = socket;
            window.ensureWebSocketOpen = async () => {
                window.appState.socket = socket;
            };
        }""",
        {
            "failSessionStart": fail_session_start,
            "resolveDelayMs": resolve_delay_ms,
        },
    )


@pytest.mark.frontend
def test_react_composer_text_submit_uses_single_stable_user_message(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page, resolve_delay_ms=-1)

    composer = mock_page.locator(".composer-input")
    composer.fill("Hello from React")
    composer.press("Enter")

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.getState().messages.length === 1"
    )

    snapshot = mock_page.evaluate(
        """() => {
            const state = window.reactChatWindowHost.getState();
            const message = state.messages[0];
            return {
                count: state.messages.length,
                author: message && message.author,
                status: message && message.status,
                text: message && message.blocks && message.blocks[0] && message.blocks[0].text,
                hasYouAuthor: state.messages.some((entry) => entry.author === 'You'),
                userDomRows: document.querySelectorAll('article[data-message-role="user"]').length
            };
        }"""
    )

    assert snapshot["count"] == 1
    assert snapshot["author"] == "Alice"
    assert snapshot["status"] == "sending"
    assert snapshot["text"] == "Hello from React"
    assert snapshot["hasYouAuthor"] is False
    assert snapshot["userDomRows"] == 1

    mock_page.wait_for_function(
        "() => window.__chatTest && typeof window.__chatTest.fireSessionStart === 'function'"
    )
    mock_page.evaluate("() => window.__chatTest.fireSessionStart()")

    mock_page.wait_for_function(
        "() => {"
        "  const state = window.reactChatWindowHost.getState();"
        "  return state.messages.length === 1 && state.messages[0] && state.messages[0].status === 'sent';"
        "}"
    )

    after_send = mock_page.evaluate(
        """() => {
            const state = window.reactChatWindowHost.getState();
            return {
                count: state.messages.length,
                author: state.messages[0] && state.messages[0].author,
                status: state.messages[0] && state.messages[0].status,
                sentPayloads: window.__chatTest.sentPayloads
            };
        }"""
    )

    assert after_send["count"] == 1
    assert after_send["author"] == "Alice"
    assert after_send["status"] == "sent"
    assert "start_session" in [payload["action"] for payload in after_send["sentPayloads"]]
    assert any(
        payload["action"] == "stream_data"
        and payload.get("input_type") == "text"
        and payload.get("data") == "Hello from React"
        for payload in after_send["sentPayloads"]
    )


@pytest.mark.frontend
def test_react_composer_text_and_screenshot_submit_keeps_single_combined_message(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    mock_page.evaluate(
        """() => {
            window.appButtons.addScreenshotToList(
                'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Wj3sAAAAASUVORK5CYII='
            );
        }"""
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.getState().composerAttachments.length === 1"
    )

    composer = mock_page.locator(".composer-input")
    composer.fill("Look at this")
    composer.press("Enter")

    mock_page.wait_for_function(
        "() => {"
        "  const state = window.reactChatWindowHost.getState();"
        "  return state.messages.length === 1 && state.messages[0] && state.messages[0].status === 'sent';"
        "}"
    )

    snapshot = mock_page.evaluate(
        """() => {
            const state = window.reactChatWindowHost.getState();
            const message = state.messages[0];
            return {
                count: state.messages.length,
                status: message && message.status,
                blockTypes: message && Array.isArray(message.blocks)
                    ? message.blocks.map((block) => block.type)
                    : [],
                author: message && message.author,
                textBlocks: message && Array.isArray(message.blocks)
                    ? message.blocks.filter((block) => block.type === 'text').map((block) => block.text)
                    : [],
                composerAttachmentCount: state.composerAttachments.length,
                userDomRows: document.querySelectorAll('article[data-message-role="user"]').length
            };
        }"""
    )

    assert snapshot["count"] == 1
    assert snapshot["status"] == "sent"
    assert snapshot["author"] == "Alice"
    assert snapshot["blockTypes"] == ["text", "image"]
    assert snapshot["textBlocks"] == ["Look at this"]
    assert snapshot["composerAttachmentCount"] == 0
    assert snapshot["userDomRows"] == 1


@pytest.mark.frontend
def test_react_composer_send_failure_marks_same_message_failed(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page, fail_session_start=True, resolve_delay_ms=0)

    composer = mock_page.locator(".composer-input")
    composer.fill("This should fail")
    composer.press("Enter")

    mock_page.wait_for_function(
        "() => {"
        "  const state = window.reactChatWindowHost.getState();"
        "  return state.messages.length === 1 && state.messages[0] && state.messages[0].status === 'failed';"
        "}"
    )

    snapshot = mock_page.evaluate(
        """() => {
            const state = window.reactChatWindowHost.getState();
            const message = state.messages[0];
            return {
                count: state.messages.length,
                author: message && message.author,
                status: message && message.status,
                text: message && message.blocks && message.blocks[0] && message.blocks[0].text,
                userDomRows: document.querySelectorAll('article[data-message-role="user"]').length
            };
        }"""
    )

    assert snapshot["count"] == 1
    assert snapshot["author"] == "Alice"
    assert snapshot["status"] == "failed"
    assert snapshot["text"] == "This should fail"
    assert snapshot["userDomRows"] == 1
