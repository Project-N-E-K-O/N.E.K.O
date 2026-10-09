'use strict';
const assert = require('node:assert/strict');
const { createVoiceManagerServer } = require('./remote_voice_manager_server.cjs');
const bounded = (promise, label) => {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Error('Timeout: ' + label)), 5000);
    })]).finally(() => clearTimeout(timer));
};
const deferred = () => {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
};

// Real chunked transport: the test observes partial delivery before releasing the body.
function createVoicePreviewServer() {
    const { server, state } = createVoiceManagerServer();
    state.voices['preview-body'] = { local_ref: 'preview-body', origin: 'import', source: 'clone', provider: 'cosyvoice', availability: 'available', display_name: 'Controlled preview' };
    const productHandler = server.listeners('request')[0];
    server.removeAllListeners('request');
    const requests = [], arrivals = new Map();
    server.on('request', (request, response) => {
        if (!request.url.startsWith('/api/characters/voice_preview?')) return productHandler(request, response);
        const delivered = deferred(), closed = deferred(), gate = deferred();
        const item = { url: request.url, closed: closed.promise, delivered: delivered.promise,
            release: () => gate.resolve(), wasClosed: false };
        const index = requests.push(item) - 1;
        response.on('close', () => { item.wasClosed = true; closed.resolve(); });
        response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        response.write('{"success":true,"audio":"', () => delivered.resolve());
        arrivals.get(index)?.resolve(item);
        gate.promise.then(() => { if (!response.destroyed) response.end('TkVX"}'); });
    });
    return { server, state, requests,
        async request(index) {
            if (!requests[index]) {
                if (!arrivals.has(index)) arrivals.set(index, deferred());
                await bounded(arrivals.get(index).promise, 'preview request ' + index);
            }
            await bounded(requests[index].delivered, 'partial response ' + index);
            return requests[index];
        },
        async assertClosed(index) {
            const item = await this.request(index);
            await bounded(item.closed, 'cancelled response connection ' + index);
            assert.equal(item.wasClosed, true);
        },
        async close() {
            requests.forEach(item => item.release());
            server.closeAllConnections();
            await new Promise(resolve => server.close(resolve));
        }
    };
}
module.exports = { createVoicePreviewServer, bounded };
