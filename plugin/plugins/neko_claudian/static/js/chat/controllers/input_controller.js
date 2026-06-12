/**
 * Neko Claudian — Input Controller
 * Ported from claudian/src/features/chat/controllers/InputController.ts
 */

const InputController = {
    /**
     * Initialize the input controller.
     */
    init() {
        this.inputEl = document.getElementById('neko-input');
        this.sendBtn = document.getElementById('neko-send-btn');
        this.messagesEl = document.getElementById('neko-messages');

        this.setupEventListeners();
    },

    /**
     * Setup event listeners.
     */
    setupEventListeners() {
        // Send on button click
        this.sendBtn.addEventListener('click', () => this.sendMessage());

        // Send on Enter
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-resize
        this.inputEl.addEventListener('input', () => {
            this.inputEl.style.height = 'auto';
            this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 200) + 'px';
        });
    },

    /**
     * Send a message.
     */
    async sendMessage() {
        const text = this.inputEl.value.trim();
        if (!text || NekoState.isStreaming) return;

        // Clear input
        this.inputEl.value = '';
        this.inputEl.style.height = 'auto';

        // Hide welcome
        const welcomeEl = document.getElementById('neko-welcome');
        if (welcomeEl) welcomeEl.classList.add('hidden');

        // Add user message to UI
        this.addMessage({
            role: 'user',
            content: text,
            timestamp: Date.now(),
        });

        // Set streaming
        NekoState.setStreaming(true);

        try {
            await NekoAPI.sendMessage(text);
        } catch (e) {
            console.error('Failed to send:', e);
            this.addMessage({
                role: 'system',
                content: '发送失败: ' + e.message,
            });
        }
    },

    /**
     * Add a message to the UI.
     */
    addMessage(message) {
        const el = MessageRenderer.renderMessage(message);
        this.messagesEl.appendChild(el);
        this.scrollToBottom();
    },

    /**
     * Scroll to bottom.
     */
    scrollToBottom() {
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    /**
     * Set draft text.
     */
    setDraft(text) {
        this.inputEl.value = text;
    },

    /**
     * Click send button.
     */
    clickSend() {
        this.sendBtn.click();
    },
};

if (typeof module !== 'undefined') {
    module.exports = InputController;
}
