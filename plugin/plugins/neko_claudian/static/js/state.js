/**
 * Neko Claudian — State Management
 * Client-side state management for the chat UI.
 */

const NekoState = {
    // Current state
    messages: [],
    currentTabId: null,
    tabs: [],
    isStreaming: false,
    usage: null,
    settings: {},

    // Listeners
    _listeners: new Map(),

    // Subscribe to state changes
    on(key, callback) {
        if (!this._listeners.has(key)) {
            this._listeners.set(key, []);
        }
        this._listeners.get(key).push(callback);
    },

    // Emit state change
    emit(key, value) {
        const listeners = this._listeners.get(key) || [];
        listeners.forEach(cb => cb(value));
    },

    // Update state
    set(key, value) {
        this[key] = value;
        this.emit(key, value);
    },

    // Add a message
    addMessage(message) {
        this.messages.push(message);
        this.emit('messages', this.messages);
    },

    // Clear messages
    clearMessages() {
        this.messages = [];
        this.emit('messages', this.messages);
    },

    // Set streaming state
    setStreaming(isStreaming) {
        this.isStreaming = isStreaming;
        this.emit('isStreaming', isStreaming);
    },

    // Update usage
    setUsage(usage) {
        this.usage = usage;
        this.emit('usage', usage);
    },
};

// Export for use
if (typeof module !== 'undefined') {
    module.exports = NekoState;
}
