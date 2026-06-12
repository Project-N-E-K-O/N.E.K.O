/**
 * Neko Claudian — SSE Client
 * Handles Server-Sent Events for real-time updates.
 */

const NekoSSE = {
    eventSource: null,
    listeners: new Map(),

    connect() {
        if (this.eventSource) {
            this.eventSource.close();
        }

        this.eventSource = new EventSource('/neko_claudian/api/stream/*');

        this.eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.emit(data.type, data);
            } catch (e) {
                console.error('SSE parse error:', e);
            }
        };

        this.eventSource.onerror = () => {
            console.warn('SSE connection error, reconnecting...');
            setTimeout(() => this.connect(), 3000);
        };
    },

    disconnect() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    },

    on(eventType, callback) {
        if (!this.listeners.has(eventType)) {
            this.listeners.set(eventType, []);
        }
        this.listeners.get(eventType).push(callback);
    },

    off(eventType, callback) {
        const listeners = this.listeners.get(eventType);
        if (listeners) {
            const index = listeners.indexOf(callback);
            if (index > -1) {
                listeners.splice(index, 1);
            }
        }
    },

    emit(eventType, data) {
        const listeners = this.listeners.get(eventType) || [];
        listeners.forEach(callback => callback(data));

        // Also emit to wildcard listeners
        const wildcardListeners = this.listeners.get('*') || [];
        wildcardListeners.forEach(callback => callback(data));
    },
};

// Export for use
if (typeof module !== 'undefined') {
    module.exports = NekoSSE;
}
