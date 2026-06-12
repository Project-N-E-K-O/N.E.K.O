/**
 * Neko Claudian — Main Entry Point
 * Initializes the application and wires up event handlers.
 */

(async function() {
    'use strict';

    // DOM Elements
    const messagesEl = document.getElementById('neko-messages');
    const inputEl = document.getElementById('neko-input');
    const sendBtn = document.getElementById('neko-send-btn');
    const tabBarEl = document.getElementById('neko-tab-bar');
    const welcomeEl = document.getElementById('neko-welcome');
    const settingsBtn = document.getElementById('neko-settings-btn');

    // Initialize
    async function init() {
        // Load i18n
        await Nekoi18n.load('zh-CN');

        // Connect SSE
        NekoSSE.connect();

        // Setup SSE listeners
        setupSSEListeners();

        // Setup input handlers
        setupInputHandlers();

        // Load initial state
        await loadInitialState();

        console.log('Neko Claudian initialized');
    }

    // Setup SSE listeners
    function setupSSEListeners() {
        // Stream start
        NekoSSE.on('stream_start', () => {
            NekoState.setStreaming(true);
            showThinkingIndicator();
        });

        // Stream end
        NekoSSE.on('stream_end', () => {
            NekoState.setStreaming(false);
            hideThinkingIndicator();
        });

        // Text events
        NekoSSE.on('text', (data) => {
            hideThinkingIndicator();
            appendAssistantText(data.content);
        });

        // Thinking events
        NekoSSE.on('thinking', (data) => {
            hideThinkingIndicator();
            appendThinking(data.content);
        });

        // Tool use events
        NekoSSE.on('tool_use', (data) => {
            renderToolCall(data);
        });

        // Tool result events
        NekoSSE.on('tool_result', (data) => {
            updateToolResult(data);
        });

        // Done event
        NekoSSE.on('done', () => {
            finishStreaming();
        });

        // Error event
        NekoSSE.on('error', (data) => {
            hideThinkingIndicator();
            appendError(data.content);
        });

        // Stream event (raw data)
        NekoSSE.on('stream_event', (data) => {
            // Handle raw stream events
            console.log('Stream event:', data);
        });

        // User message (echo)
        NekoSSE.on('user_message', (data) => {
            // User message already added locally
        });

        // Neko-specific events
        NekoSSE.on('neko_inject_text', (data) => {
            inputEl.value = data.text;
        });

        NekoSSE.on('neko_click_send', () => {
            sendMessage();
        });
    }

    // Setup input handlers
    function setupInputHandlers() {
        // Send on button click
        sendBtn.addEventListener('click', () => {
            sendMessage();
        });

        // Send on Enter (Shift+Enter for newline)
        inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Auto-resize textarea
        inputEl.addEventListener('input', () => {
            inputEl.style.height = 'auto';
            inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
        });
    }

    // Load initial state
    async function loadInitialState() {
        try {
            const [tabs, settings] = await Promise.all([
                NekoAPI.getTabs(),
                NekoAPI.getSettings(),
            ]);

            NekoState.tabs = tabs;
            NekoState.settings = settings;

            renderTabs(tabs);
        } catch (e) {
            console.error('Failed to load initial state:', e);
        }
    }

    // Send message
    async function sendMessage() {
        const text = inputEl.value.trim();
        if (!text || NekoState.isStreaming) return;

        // Clear input
        inputEl.value = '';
        inputEl.style.height = 'auto';

        // Hide welcome
        if (welcomeEl) {
            welcomeEl.classList.add('hidden');
        }

        // Add user message to UI
        addMessageToUI({
            role: 'user',
            content: text,
            timestamp: Date.now(),
        });

        // Set streaming state
        NekoState.setStreaming(true);

        try {
            const result = await NekoAPI.sendMessage(text);
            if (!result.ok) {
                appendError(result.error || '发送失败');
                NekoState.setStreaming(false);
            }
            // Success - streaming will come via SSE
        } catch (e) {
            console.error('Failed to send message:', e);
            appendError('发送失败: ' + e.message);
            NekoState.setStreaming(false);
        }
    }

    // Add message to UI
    function addMessageToUI(message) {
        const messageEl = document.createElement('div');
        messageEl.className = `neko-message neko-message-${message.role}`;

        const contentEl = document.createElement('div');
        contentEl.className = 'neko-message-content';
        contentEl.textContent = message.content;

        messageEl.appendChild(contentEl);
        messagesEl.appendChild(messageEl);

        // Scroll to bottom
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Append assistant text
    function appendAssistantText(text) {
        let lastAssistant = messagesEl.querySelector('.neko-message-assistant:last-child');
        if (!lastAssistant) {
            lastAssistant = document.createElement('div');
            lastAssistant.className = 'neko-message neko-message-assistant';
            messagesEl.appendChild(lastAssistant);
        }

        let contentEl = lastAssistant.querySelector('.neko-message-content');
        if (!contentEl) {
            contentEl = document.createElement('div');
            contentEl.className = 'neko-message-content';
            lastAssistant.appendChild(contentEl);
        }

        contentEl.textContent += text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Append thinking
    function appendThinking(text) {
        // Thinking blocks are handled separately
        console.log('Thinking:', text);
    }

    // Render tool call
    function renderToolCall(data) {
        const toolEl = document.createElement('div');
        toolEl.className = 'neko-tool-call';
        toolEl.dataset.toolId = data.id;
        toolEl.innerHTML = `
            <div class="neko-tool-header">
                <span class="neko-tool-name">${data.name}</span>
                <span class="neko-tool-status">运行中...</span>
            </div>
        `;
        messagesEl.appendChild(toolEl);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Update tool result
    function updateToolResult(data) {
        const toolEl = messagesEl.querySelector(`[data-tool-id="${data.id}"]`);
        if (toolEl) {
            const statusEl = toolEl.querySelector('.neko-tool-status');
            if (statusEl) {
                statusEl.textContent = data.isError ? '错误' : '完成';
            }
        }
    }

    // Append error
    function appendError(text) {
        const errorEl = document.createElement('div');
        errorEl.className = 'neko-error';
        errorEl.textContent = `❌ ${text}`;
        messagesEl.appendChild(errorEl);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Finish streaming
    function finishStreaming() {
        NekoState.setStreaming(false);
        hideThinkingIndicator();
    }

    // Show thinking indicator
    function showThinkingIndicator() {
        let indicator = document.getElementById('neko-thinking-indicator');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'neko-thinking-indicator';
            indicator.className = 'neko-thinking';
            indicator.innerHTML = '<div class="neko-thinking-header">💭 思考中...</div>';
            messagesEl.appendChild(indicator);
        }
        indicator.style.display = 'block';
        scrollToBottom();
    }

    // Hide thinking indicator
    function hideThinkingIndicator() {
        const indicator = document.getElementById('neko-thinking-indicator');
        if (indicator) {
            indicator.style.display = 'none';
        }
    }

    // Scroll to bottom
    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Handle stream event
    function handleStreamEvent(data) {
        // Handle various stream events
        console.log('Stream event:', data);
    }

    // Render tabs
    function renderTabs(tabs) {
        tabBarEl.innerHTML = '';
        tabs.forEach(tab => {
            const tabEl = document.createElement('button');
            tabEl.className = `neko-tab ${tab.isActive ? 'active' : ''}`;
            tabEl.textContent = tab.title || '新对话';
            tabEl.onclick = () => NekoAPI.switchTab(tab.id);
            tabBarEl.appendChild(tabEl);
        });

        // Add new tab button
        const newTabBtn = document.createElement('button');
        newTabBtn.className = 'neko-tab neko-tab-new';
        newTabBtn.textContent = '+';
        newTabBtn.onclick = () => NekoAPI.createTab();
        tabBarEl.appendChild(newTabBtn);
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
