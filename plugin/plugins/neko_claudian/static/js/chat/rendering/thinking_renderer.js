/**
 * Neko Claudian — Thinking Block Renderer
 * Ported from claudian/src/features/chat/rendering/ThinkingBlockRenderer.ts
 */

const ThinkingBlockRenderer = {
    /**
     * Create a thinking block.
     */
    createThinkingBlock(parentEl, renderContent) {
        const el = document.createElement('div');
        el.className = 'neko-thinking';

        const headerEl = document.createElement('div');
        headerEl.className = 'neko-thinking-header';
        headerEl.textContent = '💭 思考中...';
        el.appendChild(headerEl);

        const contentEl = document.createElement('div');
        contentEl.className = 'neko-thinking-content';
        el.appendChild(contentEl);

        parentEl.appendChild(el);

        return {
            el,
            contentEl,
            content: '',
            startTime: Date.now(),
        };
    },

    /**
     * Finalize a thinking block.
     */
    finalizeThinkingBlock(state) {
        if (!state) return 0;

        const duration = Math.floor((Date.now() - state.startTime) / 1000);

        // Update header with duration
        const headerEl = state.el.querySelector('.neko-thinking-header');
        if (headerEl) {
            headerEl.textContent = `💭 思考了 ${duration}秒`;
        }

        return duration;
    },
};

if (typeof module !== 'undefined') {
    module.exports = ThinkingBlockRenderer;
}
