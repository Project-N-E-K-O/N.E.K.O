'use strict';
// Controlled API for browser/Electron regression; all product assets are served unchanged.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
function createVoiceManagerServer() {
    const definition = JSON.parse(fs.readFileSync(path.join(root, 'config/api_providers.json'), 'utf8'));
    const state = { voices: {}, imports: [], updates: [], settings: [], listQueries: [], binding: '', status: 'unknown' };
    const config = { success: true, coreApi: 'free', assistApi: 'free', api_key: 'free-access',
        enableCustomApi: false, assistApiKeyQwen: '__NEKO_SECRET_MASKED__', assistApiKeyMinimax: '__NEKO_SECRET_MASKED__',
        assistApiKeyDoubaoTts: '__NEKO_SECRET_MASKED__', doubaoVoiceManagementAccessKey: '__NEKO_SECRET_MASKED__',
        doubaoVoiceManagementSecretKey: '__NEKO_SECRET_MASKED__', doubaoVoiceManagementAppId: 'test-app',
        doubaoVoiceManagementProjectName: '', ttsModelProvider: 'follow_core' };
    const capabilities = { list: true, details: true, manual_import: true, overwrite: true };
    const management = provider => ({ ...capabilities, overwrite: ['cosyvoice', 'cosyvoice_intl', 'doubao_tts'].includes(provider) });
    const json = (response, value, status = 200) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); };
    const read = request => new Promise(resolve => { let bytes = ''; request.on('data', chunk => { bytes += chunk; }); request.on('end', () => resolve(bytes)); });
    const server = http.createServer(async (request, response) => {
        const url = new URL(request.url, 'http://127.0.0.1');
        const provider = url.searchParams.get('provider') || 'minimax';
        if (url.pathname === '/api/config/steam_language') return json(response, { success: true, steam_language: 'schinese', ui_language: 'zh-CN', i18n_language: 'zh-CN', ip_country: 'CN', is_mainland_china: true });
        if (url.pathname === '/api/config/page_config') return json(response, { success: true, lanlan_name: 'Test', language: 'zh-CN' });
        if (url.pathname === '/api/config/api_providers') return json(response, {
            success: true, api_key_registry: definition.api_key_registry,
            core_api_providers: Object.values(definition.core_api_providers), core_api_providers_full: definition.core_api_providers,
            assist_api_providers: Object.values(definition.assist_api_providers), assist_api_providers_full: definition.assist_api_providers,
            keybook_api_providers_full: definition.keybook_api_providers,
            tts_providers: ['cosyvoice', 'minimax', 'doubao_tts', 'elevenlabs', 'glm_tts'].map(key => ({ key,
                capabilities: ['clone'], aliases: key === 'minimax' ? ['minimax_intl'] : [], voice_management: management(key) }))
        });
        if (url.pathname === '/api/config/core_api') {
            if (request.method === 'POST') { const body = JSON.parse(await read(request)); state.settings.push(body); return json(response, { success: true }); }
            return json(response, config);
        }
        if (url.pathname === '/api/characters') return json(response, { '猫娘': { Test: { voice_id: state.binding } } });
        if (url.pathname.startsWith('/api/characters/catgirl/voice_id/')) { state.binding = JSON.parse(await read(request)).voice_id; return json(response, { success: true }); }
        if (url.pathname === '/api/characters/voices') return json(response, { success: true, voices: state.voices });
        if (url.pathname === '/api/characters/remote_voices/context') return json(response, { success: true, provider, configured: true,
            context_token: 'controlled-context', capabilities: management(provider), required_fields: provider.startsWith('cosyvoice') ? [{ key: 'clone_model', required: true, label_key: 'voice.remote.model', default_value: 'cosyvoice-v3-plus' }] : [] });
        if (url.pathname === '/api/characters/remote_voices') {
            const query = url.searchParams.get('query') || '';
            const cursor = url.searchParams.get('cursor');
            state.listQueries.push({ query, cursor });
            const pages = state.remotePages || [[
                { voice_id: 'ExistingVoice123', name: '已有克隆音色', created_at: '2026-07-10T10:00:00Z', status: 'ready', can_overwrite: management(provider).overwrite, imported: Object.values(state.voices).some(voice => voice.provider === provider && voice.remote_voice_id === 'ExistingVoice123') },
                { voice_id: 'Missing-date', name: '', created_at: null, status: 'unknown' }
            ]];
            const page = cursor === null ? 0 : Number(cursor);
            if (!Number.isInteger(page) || page < 0 || page >= pages.length) return json(response, { success: false, code: 'INVALID_CURSOR' }, 400);
            return json(response, { success: true, provider, context_token: 'controlled-context',
                voices: pages[page].filter(voice => (voice.voice_id + ' ' + (voice.name || '')).toLocaleLowerCase().includes(query.toLocaleLowerCase())),
                next_cursor: page + 1 < pages.length ? String(page + 1) : null });
        }
        if (url.pathname === '/api/characters/voices/import') {
            const body = JSON.parse(await read(request)); state.imports.push(body);
            const ref = 'voice_' + state.imports.length.toString(16).padStart(32, '0');
            const data = { local_ref: ref, remote_voice_id: body.remote_voice_id, display_name: body.display_name || '已有克隆音色', prefix: body.display_name || '已有克隆音色',
                source: 'clone', provider: body.provider, availability: 'available', origin: 'import', verification: 'verified', remote_revision: 'controlled-revision-1', clone_model: body.metadata.clone_model || (body.provider.startsWith('cosyvoice') ? 'cosyvoice-v3-plus' : ''), can_overwrite: management(body.provider).overwrite, created_at: new Date().toISOString() };
            state.voices[ref] = data;
            if (state.beforeImportResponse) await state.beforeImportResponse();
            return json(response, { success: true, voice_id: ref, voice_data: data, created: true, verification: data.verification });
        }
        if (url.pathname === '/api/characters/voice_preview') {
            if (state.beforePreviewResponse) await state.beforePreviewResponse();
            return json(response, { success: true, audio: Buffer.from('controlled-audio').toString('base64') });
        }
        if (request.method === 'DELETE' && url.pathname.startsWith('/api/characters/voices/')) {
            delete state.voices[url.pathname.split('/')[4]];
            return json(response, { success: true });
        }
        if (url.pathname.endsWith('/overwrite')) {
            state.updates.push(await read(request)); const ref = url.pathname.split('/')[4];
            if (state.voices[ref]) state.voices[ref].overwrite_status = 'unknown';
            return json(response, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN' }, 504);
        }
        if (url.pathname.endsWith('/overwrite_status')) {
            const ref = url.pathname.split('/')[4]; if (state.voices[ref]) state.voices[ref].overwrite_status = 'completed';
            return json(response, { success: true, status: 'completed', voice_data: state.voices[ref] });
        }
        if (url.pathname === '/voice_clone' || url.pathname === '/' || url.pathname === '/api_key') {
            const name = url.pathname === '/api_key' ? 'api_key_settings.html' : 'voice_clone.html';
            response.writeHead(200, { 'Content-Type': 'text/html;charset=utf-8' });
            return response.end(fs.readFileSync(path.join(root, 'templates', name), 'utf8').replace(/\{\{[^}]+\}\}/g, 'controlled'));
        }
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
            if (!file.startsWith(path.join(root, 'static') + path.sep)) return json(response, {}, 403);
            try { response.writeHead(200, { 'Content-Type': { '.js': 'application/javascript', '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(file)] || 'application/octet-stream' }); return response.end(fs.readFileSync(file)); }
            catch (_) { return response.end(); }
        }
        return json(response, { success: true, models: [] });
    });
    return { server, state };
}
module.exports = { createVoiceManagerServer };
if (require.main === module) {
    const { server } = createVoiceManagerServer();
    server.listen(48911, '127.0.0.1', () => console.log('REMOTE_VOICE_CONTROLLED_PREVIEW http://127.0.0.1:48911'));
}
