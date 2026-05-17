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
def test_import_image_without_mime_converts_to_jpeg_attachment(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    import_result = mock_page.evaluate(
        """async () => {
            const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Wj3sAAAAASUVORK5CYII=';
            const bytes = Uint8Array.from(atob(b64), (char) => char.charCodeAt(0));
            const file = new File([bytes], 'tiny-image', { type: '' });
            await window.appButtons.importImageFileToPendingList(file);
            const state = window.reactChatWindowHost.getState();
            return {
                attachmentCount: state.composerAttachments.length,
                attachmentUrl: state.composerAttachments[0] && state.composerAttachments[0].url
            };
        }"""
    )

    assert import_result["attachmentCount"] == 1
    attachment_url = import_result["attachmentUrl"]
    assert attachment_url.startswith("data:image/jpeg;base64,")


@pytest.mark.frontend
def test_import_rejects_canvas_data_url_encode_fallback(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    result = mock_page.evaluate(
        """async () => {
            const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Wj3sAAAAASUVORK5CYII=';
            const bytes = Uint8Array.from(atob(b64), (char) => char.charCodeAt(0));
            const file = new File([bytes], 'tiny-image', { type: '' });
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            let rejected = false;
            let errorMessage = '';
            try {
                HTMLCanvasElement.prototype.toDataURL = function () {
                    return 'data:,';
                };
                await window.appButtons.importImageFileToPendingList(file);
            } catch (error) {
                rejected = true;
                errorMessage = String(error && error.message ? error.message : error);
            } finally {
                HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
            }
            const state = window.reactChatWindowHost.getState();
            return {
                rejected,
                errorMessage,
                attachmentCount: state.composerAttachments.length
            };
        }"""
    )

    assert result["rejected"] is True
    assert result["errorMessage"] == "IMAGE_ENCODE_FAILED"
    assert result["attachmentCount"] == 0


@pytest.mark.frontend
def test_import_rejects_canvas_encode_throw(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    result = mock_page.evaluate(
        """async () => {
            const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Wj3sAAAAASUVORK5CYII=';
            const bytes = Uint8Array.from(atob(b64), (char) => char.charCodeAt(0));
            const file = new File([bytes], 'tiny-image', { type: '' });
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            let rejected = false;
            let errorMessage = '';
            try {
                HTMLCanvasElement.prototype.toDataURL = function () {
                    throw new Error('canvas encode exploded');
                };
                await window.appButtons.importImageFileToPendingList(file);
            } catch (error) {
                rejected = true;
                errorMessage = String(error && error.message ? error.message : error);
            } finally {
                HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
            }
            const state = window.reactChatWindowHost.getState();
            return {
                rejected,
                errorMessage,
                attachmentCount: state.composerAttachments.length
            };
        }"""
    )

    assert result["rejected"] is True
    assert result["errorMessage"] == "IMAGE_ENCODE_FAILED"
    assert result["attachmentCount"] == 0


@pytest.mark.frontend
def test_import_jpeg_under_limit_keeps_original_data(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    result = mock_page.evaluate(
        """async () => {
            const canvas = document.createElement('canvas');
            canvas.width = 2;
            canvas.height = 2;
            const context = canvas.getContext('2d');
            context.fillStyle = '#336699';
            context.fillRect(0, 0, 2, 2);
            const original = canvas.toDataURL('image/jpeg', 0.92);
            const b64 = original.split(',')[1];
            const bytes = Uint8Array.from(atob(b64), (char) => char.charCodeAt(0));
            const file = new File([bytes], 'tiny.jpg', { type: 'image/jpeg' });
            await window.appButtons.importImageFileToPendingList(file);
            const state = window.reactChatWindowHost.getState();
            if (state.composerAttachments.length !== 1) {
                throw new Error(`Expected one composer attachment, got ${state.composerAttachments.length}`);
            }
            return {
                original,
                attachmentCount: state.composerAttachments.length,
                imported: state.composerAttachments[0] && state.composerAttachments[0].url
            };
        }"""
    )

    assert result["attachmentCount"] == 1
    assert result["imported"] == result["original"]


@pytest.mark.frontend
def test_import_jpeg_over_limit_compresses_attachment(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    result = mock_page.evaluate(
        """async () => {
            const limitBytes = 10 * 1024 * 1024;
            const dataUrlBytes = (dataUrl) => {
                const b64 = String(dataUrl || '').split(',')[1] || '';
                const padding = b64.endsWith('==') ? 2 : (b64.endsWith('=') ? 1 : 0);
                return Math.max(0, Math.floor(b64.length * 3 / 4) - padding);
            };
            const makeNoisyJpeg = (size) => {
                const canvas = document.createElement('canvas');
                canvas.width = size;
                canvas.height = size;
                const context = canvas.getContext('2d');
                const imageData = context.createImageData(size, size);
                const pixels = imageData.data;
                for (let y = 0; y < size; y += 1) {
                    for (let x = 0; x < size; x += 1) {
                        const offset = (y * size + x) * 4;
                        const value = (x * 17 + y * 31 + ((x ^ y) * 13)) & 255;
                        pixels[offset] = value;
                        pixels[offset + 1] = (value * 7 + x) & 255;
                        pixels[offset + 2] = (value * 13 + y) & 255;
                        pixels[offset + 3] = 255;
                    }
                }
                context.putImageData(imageData, 0, 0);
                return canvas.toDataURL('image/jpeg', 1);
            };

            let original = makeNoisyJpeg(3072);
            if (dataUrlBytes(original) <= limitBytes) {
                original = makeNoisyJpeg(4096);
            }
            const originalBytes = dataUrlBytes(original);
            if (originalBytes <= limitBytes) {
                throw new Error(`Expected source JPEG to exceed 10MB, got ${originalBytes}`);
            }

            const response = await fetch(original);
            const blob = await response.blob();
            const file = new File([blob], 'big.jpg', { type: 'image/jpeg' });
            await window.appButtons.importImageFileToPendingList(file);
            const state = window.reactChatWindowHost.getState();
            if (state.composerAttachments.length !== 1) {
                throw new Error(`Expected one composer attachment, got ${state.composerAttachments.length}`);
            }
            const imported = state.composerAttachments[0] && state.composerAttachments[0].url;
            return {
                attachmentCount: state.composerAttachments.length,
                originalBytes,
                importedBytes: dataUrlBytes(imported),
                importedChanged: imported !== original,
                importedIsJpeg: String(imported || '').startsWith('data:image/jpeg;base64,')
            };
        }"""
    )

    assert result["attachmentCount"] == 1
    assert result["originalBytes"] > 10 * 1024 * 1024
    assert result["importedBytes"] <= 10 * 1024 * 1024
    assert result["importedChanged"] is True
    assert result["importedIsJpeg"] is True


@pytest.mark.frontend
def test_normalized_pending_image_clears_stale_avatar_position(
    mock_page: Page,
    running_server: str,
):
    _open_react_chat_page(mock_page, running_server)
    _install_chat_send_harness(mock_page)

    mock_page.evaluate(
        """() => {
            window.appButtons.addScreenshotToList(
                'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9Wj3sAAAAASUVORK5CYII=',
                { left: 10, top: 20, width: 30, height: 40 }
            );
        }"""
    )
    mock_page.wait_for_function(
        "() => document.querySelector('#screenshots-list')"
        " && document.querySelector('#screenshots-list').children.length === 1"
    )

    result = mock_page.evaluate(
        """async () => {
            const item = document.querySelector('#screenshots-list').children[0];
            const hadAvatarPositionBefore = Object.prototype.hasOwnProperty.call(item.dataset, 'avatarPosition');
            await window.appButtons.normalizeAllPendingComposerAttachments();
            const state = window.reactChatWindowHost.getState();
            return {
                attachmentCount: state.composerAttachments.length,
                hadAvatarPositionBefore,
                hasAvatarPositionAfter: Object.prototype.hasOwnProperty.call(item.dataset, 'avatarPosition'),
                attachmentUrl: state.composerAttachments[0] && state.composerAttachments[0].url
            };
        }"""
    )

    assert result["attachmentCount"] == 1
    assert result["hadAvatarPositionBefore"] is True
    assert result["hasAvatarPositionAfter"] is False
    assert result["attachmentUrl"].startswith("data:image/jpeg;base64,")


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
                imageBlocks: message && Array.isArray(message.blocks)
                    ? message.blocks.filter((block) => block.type === 'image').map((block) => block.url)
                    : [],
                composerAttachmentCount: state.composerAttachments.length,
                userDomRows: document.querySelectorAll('article[data-message-role="user"]').length,
                sentImages: window.__chatTest.sentPayloads
                    .filter((payload) => payload.action === 'stream_data' && payload.input_type === 'screen')
                    .map((payload) => payload.data)
            };
        }"""
    )

    assert snapshot["count"] == 1
    assert snapshot["status"] == "sent"
    assert snapshot["author"] == "Alice"
    assert snapshot["blockTypes"] == ["text", "image"]
    assert snapshot["textBlocks"] == ["Look at this"]
    assert len(snapshot["imageBlocks"]) == 1
    assert snapshot["imageBlocks"][0].startswith("data:image/jpeg;base64,")
    assert len(snapshot["sentImages"]) == 1
    assert snapshot["sentImages"][0].startswith("data:image/jpeg;base64,")
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
