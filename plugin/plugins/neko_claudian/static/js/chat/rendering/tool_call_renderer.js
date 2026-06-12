/**
 * Neko Claudian — Tool Call Renderer
 * Ported from claudian/src/features/chat/rendering/ToolCallRenderer.ts
 */

const ToolCallRenderer = {
    /**
     * Render a tool call.
     */
    renderToolCall(parentEl, toolCall, toolCallElements, options = {}) {
        const el = document.createElement('div');
        el.className = 'neko-tool-call';
        el.dataset.toolId = toolCall.id;

        // Header
        const headerEl = document.createElement('div');
        headerEl.className = 'neko-tool-header';

        const nameEl = document.createElement('span');
        nameEl.className = 'neko-tool-name';
        nameEl.textContent = this.getToolName(toolCall.name, toolCall.input);

        const statusEl = document.createElement('span');
        statusEl.className = 'neko-tool-status';
        statusEl.textContent = this.getStatusText(toolCall.status);

        headerEl.appendChild(nameEl);
        headerEl.appendChild(statusEl);
        el.appendChild(headerEl);

        // Body (collapsible)
        if (options.initiallyExpanded || toolCall.isExpanded) {
            const bodyEl = document.createElement('div');
            bodyEl.className = 'neko-tool-body';
            bodyEl.textContent = JSON.stringify(toolCall.input, null, 2);
            el.appendChild(bodyEl);
        }

        parentEl.appendChild(el);
        toolCallElements.set(toolCall.id, el);

        return el;
    },

    /**
     * Update tool call result.
     */
    updateToolCallResult(toolId, toolCall, toolCallElements) {
        const el = toolCallElements.get(toolId);
        if (!el) return;

        // Update status
        const statusEl = el.querySelector('.neko-tool-status');
        if (statusEl) {
            statusEl.textContent = this.getStatusText(toolCall.status);
        }

        // Add result
        let resultEl = el.querySelector('.neko-tool-result');
        if (!resultEl) {
            resultEl = document.createElement('div');
            resultEl.className = 'neko-tool-result';
            el.appendChild(resultEl);
        }

        if (toolCall.isError) {
            resultEl.classList.add('neko-tool-error');
        }

        resultEl.textContent = toolCall.result || '';
    },

    /**
     * Get display name for a tool.
     */
    getToolName(name, input) {
        const names = {
            'Bash': () => `Bash: ${(input.command || '').substring(0, 50)}`,
            'Read': () => `Read: ${input.file_path || ''}`,
            'Write': () => `Write: ${input.file_path || ''}`,
            'Edit': () => `Edit: ${input.file_path || ''}`,
            'Glob': () => `Glob: ${input.pattern || ''}`,
            'Grep': () => `Grep: ${input.pattern || ''}`,
            'Agent': () => `Agent: ${input.description || ''}`,
        };

        const formatter = names[name];
        return formatter ? formatter() : name;
    },

    /**
     * Get status text.
     */
    getStatusText(status) {
        const texts = {
            'running': '运行中...',
            'completed': '完成',
            'error': '错误',
            'blocked': '已阻止',
        };
        return texts[status] || status;
    },

    /**
     * Set tool icon.
     */
    setToolIcon(el, toolName) {
        const icons = {
            'Bash': '💻',
            'Read': '📄',
            'Write': '✏️',
            'Edit': '✏️',
            'Glob': '🔍',
            'Grep': '🔍',
            'Agent': '🤖',
        };
        el.textContent = icons[toolName] || '🔧';
    },
};

if (typeof module !== 'undefined') {
    module.exports = ToolCallRenderer;
}
