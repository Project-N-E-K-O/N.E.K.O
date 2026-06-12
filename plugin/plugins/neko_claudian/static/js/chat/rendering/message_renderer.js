/**
 * Neko Claudian — Message Renderer
 * Ported from claudian/src/features/chat/rendering/MessageRenderer.ts
 */

const MessageRenderer = {
    /**
     * Render a message to the DOM.
     * @param {Object} message - Message object
     * @returns {HTMLElement} Message element
     */
    renderMessage(message) {
        const el = document.createElement('div');
        el.className = `neko-message neko-message-${message.role}`;
        el.dataset.messageId = message.id;

        const contentEl = document.createElement('div');
        contentEl.className = 'neko-message-content';
        contentEl.textContent = message.content;
        el.appendChild(contentEl);

        // Add timestamp
        if (message.timestamp) {
            const timeEl = document.createElement('div');
            timeEl.className = 'neko-message-time';
            timeEl.textContent = new Date(message.timestamp).toLocaleTimeString();
            el.appendChild(timeEl);
        }

        return el;
    },

    /**
     * Render markdown content.
     * @param {HTMLElement} el - Target element
     * @param {string} content - Markdown content
     */
    renderContent(el, content) {
        // Simple markdown rendering
        let html = content;

        // Code blocks
        html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
            return `<div class="neko-code-block">
                <div class="neko-code-header">
                    <span class="neko-code-lang">${lang || ''}</span>
                    <button class="neko-code-copy" onclick="navigator.clipboard.writeText(this.closest('.neko-code-block').querySelector('.neko-code-content').textContent)">复制</button>
                </div>
                <pre class="neko-code-content"><code>${this.escapeHtml(code)}</code></pre>
            </div>`;
        });

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code class="neko-inline-code">$1</code>');

        // Bold
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

        el.innerHTML = html;
    },

    /**
     * Escape HTML special characters.
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * Add a copy button to a text block.
     */
    addTextCopyButton(el, content) {
        const btn = document.createElement('button');
        btn.className = 'neko-code-copy';
        btn.textContent = '复制';
        btn.onclick = () => {
            navigator.clipboard.writeText(content);
            btn.textContent = '已复制';
            setTimeout(() => btn.textContent = '复制', 2000);
        };
        el.appendChild(btn);
    },

    /**
     * Remove a message by ID.
     */
    removeMessage(messageId) {
        const el = document.querySelector(`[data-message-id="${messageId}"]`);
        if (el) el.remove();
    },

    /**
     * Clear all messages.
     */
    clearMessages() {
        const container = document.getElementById('neko-messages');
        if (container) {
            container.innerHTML = '';
        }
    },
};

if (typeof module !== 'undefined') {
    module.exports = MessageRenderer;
}
