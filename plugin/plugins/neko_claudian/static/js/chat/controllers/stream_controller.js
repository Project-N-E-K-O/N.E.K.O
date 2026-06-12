/**
 * Neko Claudian — Stream Controller
 * Ported from claudian/src/features/chat/controllers/StreamController.ts
 */

const StreamController = {
    currentContentEl: null,
    currentTextEl: null,
    currentTextContent: '',
    currentThinkingState: null,

    /**
     * Handle a stream chunk.
     */
    async handleStreamChunk(chunk, msg) {
        switch (chunk.type) {
            case 'text':
                await this.appendText(chunk.content);
                break;

            case 'thinking':
                await this.appendThinking(chunk.content);
                break;

            case 'tool_use':
                this.handleToolUse(chunk, msg);
                break;

            case 'tool_result':
                this.handleToolResult(chunk, msg);
                break;

            case 'error':
                this.appendError(chunk.content);
                break;

            case 'done':
                this.finishStreaming();
                break;
        }
    },

    /**
     * Append text.
     */
    async appendText(text) {
        if (!this.currentTextEl) {
            this.currentTextEl = document.createElement('div');
            this.currentTextEl.className = 'neko-message-content';
            this.currentContentEl.appendChild(this.currentTextEl);
        }

        this.currentTextContent += text;
        this.currentTextEl.textContent = this.currentTextContent;
        this.scrollToBottom();
    },

    /**
     * Append thinking.
     */
    async appendThinking(text) {
        // Handle thinking blocks
        console.log('Thinking:', text);
    },

    /**
     * Handle tool use.
     */
    handleToolUse(chunk, msg) {
        ToolCallRenderer.renderToolCall(
            this.currentContentEl || document.getElementById('neko-messages'),
            {
                id: chunk.id,
                name: chunk.name,
                input: chunk.input,
                status: 'running',
            },
            new Map()
        );
    },

    /**
     * Handle tool result.
     */
    handleToolResult(chunk, msg) {
        // Update tool call in UI
        const toolEl = document.querySelector(`[data-tool-id="${chunk.id}"]`);
        if (toolEl) {
            const statusEl = toolEl.querySelector('.neko-tool-status');
            if (statusEl) {
                statusEl.textContent = chunk.isError ? '错误' : '完成';
            }
        }
    },

    /**
     * Append error.
     */
    appendError(text) {
        const el = document.createElement('div');
        el.className = 'neko-error';
        el.textContent = '❌ ' + text;
        document.getElementById('neko-messages').appendChild(el);
        this.scrollToBottom();
    },

    /**
     * Finish streaming.
     */
    finishStreaming() {
        NekoState.setStreaming(false);
        this.currentContentEl = null;
        this.currentTextEl = null;
        this.currentTextContent = '';
    },

    /**
     * Scroll to bottom.
     */
    scrollToBottom() {
        const messagesEl = document.getElementById('neko-messages');
        if (messagesEl) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    },
};

if (typeof module !== 'undefined') {
    module.exports = StreamController;
}
