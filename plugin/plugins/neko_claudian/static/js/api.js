/**
 * Neko Claudian — API Client
 * Handles communication with the backend HTTP server.
 */

const NekoAPI = {
    baseUrl: '/neko_claudian/api',

    async request(method, path, body = null) {
        const options = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        const response = await fetch(`${this.baseUrl}${path}`, options);
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        return response.json();
    },

    // Health check
    async health() {
        return this.request('GET', '/health');
    },

    // Status
    async status() {
        return this.request('GET', '/status');
    },

    // Tabs
    async getTabs() {
        return this.request('GET', '/tabs');
    },

    async createTab(data = {}) {
        return this.request('POST', '/tab/new', data);
    },

    async switchTab(tabId) {
        return this.request('POST', `/tab/${tabId}/switch`);
    },

    async closeTab(tabId) {
        return this.request('POST', `/tab/${tabId}/close`);
    },

    // Messages
    async sendMessage(text, tabId = null) {
        const body = { text };
        if (tabId) body.tab_id = tabId;
        return this.request('POST', '/send', body);
    },

    async cancelStream() {
        return this.request('POST', '/cancel');
    },

    // Conversations
    async getConversations() {
        return this.request('GET', '/conversations');
    },

    async openConversation(conversationId) {
        return this.request('POST', `/conversation/${conversationId}/open`);
    },

    async createConversation() {
        return this.request('POST', '/conversation/new');
    },

    // Settings
    async getSettings() {
        return this.request('GET', '/settings');
    },

    async updateSettings(settings) {
        return this.request('POST', '/settings', settings);
    },

    // MCP
    async getMcpServers() {
        return this.request('GET', '/mcp/servers');
    },

    // Agents
    async getAgents() {
        return this.request('GET', '/agents');
    },
};

// Export for use
if (typeof module !== 'undefined') {
    module.exports = NekoAPI;
}
