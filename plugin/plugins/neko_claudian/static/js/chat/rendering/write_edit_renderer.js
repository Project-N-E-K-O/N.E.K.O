/**
 * Neko Claudian — Write/Edit Renderer
 * Ported from claudian/src/features/chat/rendering/WriteEditRenderer.ts
 */

const WriteEditRenderer = {
    /**
     * Create a write/edit block.
     */
    createWriteEditBlock(parentEl, toolCall, options = {}) {
        const el = document.createElement('div');
        el.className = 'neko-diff';

        const headerEl = document.createElement('div');
        headerEl.className = 'neko-diff-header';

        const nameEl = document.createElement('span');
        nameEl.className = 'neko-write-edit-name';
        nameEl.textContent = toolCall.input.file_path || toolCall.name;

        const statsEl = document.createElement('div');
        statsEl.className = 'neko-diff-stats';

        headerEl.appendChild(nameEl);
        headerEl.appendChild(statsEl);
        el.appendChild(headerEl);

        const contentEl = document.createElement('div');
        contentEl.className = 'neko-diff-content';
        el.appendChild(contentEl);

        parentEl.appendChild(el);

        return {
            wrapperEl: el,
            headerEl,
            statsEl,
            contentEl,
            isExpanded: options.initiallyExpanded || false,
        };
    },

    /**
     * Update write/edit with diff data.
     */
    updateWriteEditWithDiff(state, diffData) {
        if (!state || !diffData) return;

        // Update stats
        state.statsEl.innerHTML = `
            <span class="neko-diff-added">+${diffData.stats.added}</span>
            <span class="neko-diff-removed">-${diffData.stats.removed}</span>
        `;

        // Render diff lines
        state.contentEl.innerHTML = diffData.diffLines.map(line => {
            const cls = `neko-diff-line neko-diff-line-${line.type}`;
            return `<div class="${cls}">${this.escapeHtml(line.text)}</div>`;
        }).join('');
    },

    /**
     * Finalize write/edit block.
     */
    finalizeWriteEditBlock(state, isError) {
        if (!state) return;

        if (isError) {
            state.wrapperEl.classList.add('neko-diff-error');
        }
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

if (typeof module !== 'undefined') {
    module.exports = WriteEditRenderer;
}
