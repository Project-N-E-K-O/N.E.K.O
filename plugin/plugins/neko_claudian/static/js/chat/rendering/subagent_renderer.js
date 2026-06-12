/**
 * Neko Claudian — Subagent Renderer
 * Ported from claudian/src/features/chat/rendering/SubagentRenderer.ts
 */

const SubagentRenderer = {
    /**
     * Create a subagent block.
     */
    createSubagentBlock(parentEl, toolId, options = {}) {
        const el = document.createElement('div');
        el.className = 'neko-subagent';
        el.dataset.toolId = toolId;

        const headerEl = document.createElement('div');
        headerEl.className = 'neko-subagent-header';

        const iconEl = document.createElement('span');
        iconEl.className = 'neko-subagent-icon';
        iconEl.textContent = '🤖';

        const labelEl = document.createElement('span');
        labelEl.className = 'neko-subagent-label';
        labelEl.textContent = options.description || 'Agent';

        headerEl.appendChild(iconEl);
        headerEl.appendChild(labelEl);
        el.appendChild(headerEl);

        const contentEl = document.createElement('div');
        contentEl.className = 'neko-subagent-content';
        el.appendChild(contentEl);

        parentEl.appendChild(el);

        return {
            el,
            headerEl,
            labelEl,
            contentEl,
            info: {
                id: toolId,
                description: options.description || '',
                prompt: options.prompt || '',
                status: 'running',
                toolCalls: [],
            },
        };
    },

    /**
     * Finalize a subagent block.
     */
    finalizeSubagentBlock(state, result, isError) {
        if (!state) return;

        state.info.status = isError ? 'error' : 'completed';
        state.info.result = result;

        // Update UI
        state.el.classList.add(isError ? 'neko-subagent-error' : 'neko-subagent-complete');
        state.labelEl.textContent = `${state.info.description} - ${isError ? '错误' : '完成'}`;
    },
};

if (typeof module !== 'undefined') {
    module.exports = SubagentRenderer;
}
