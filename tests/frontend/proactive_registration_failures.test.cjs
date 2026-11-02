const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM, VirtualConsole } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const proactiveSource = fs.readFileSync(path.join(root, 'static/app/app-proactive.js'), 'utf8');
const controllerSource = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');

function responseFor(category, registered = false) {
    const music = category === 'music';
    const screen = category === 'screen';
    const url = category === 'meme' ? 'https://i.imgflip.com/fixture.jpg' : 'https://example.com/material';
    const body = { success: true, action: 'chat', source_mode: screen ? 'vision' : category,
        turn_id: 'committed-turn', response_text: 'Already delivered dialogue',
        source_links: screen ? [] : [{ title: 'Material', source: category === 'meme' ? 'Imgflip' : 'Fixture',
            type: category === 'meme' ? 'meme' : 'link', url, ...(music ? { artist: 'Artist' } : {}) }] };
    if (screen) body.screen_observation = { text: 'Screen observation' };
    if (registered) {
        const record = { id: 'registered-material', state: 'pending', expires_at: Date.now() / 1000 + 1800 };
        if (music) body.music_recommendation = { ...record, track: { name: 'Material', artist: 'Artist', url } };
        else body.proactive_recommendations = [{ ...record, category: screen ? 'vision' : category,
            content: { title: screen ? 'Screen observation' : 'Material', source: 'Fixture', url: screen ? '' : url } }];
    }
    return body;
}

// Also used by the isolated HTTP/SQLite probe: the returned body goes through
// the production trigger and recommendation controller, never a substitute offer.
async function exerciseResponse(body, options = {}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://127.0.0.1:48911/' + (options.realHost ? 'chat' : ''),
        runScripts: 'outside-only', virtualConsole: new VirtualConsole() });
    try {
        const w = dom.window, messages = [], calls = [], actions = [], timers = [];
        if (!options.realHost) {
            w.setTimeout = fn => { timers.push(fn); return timers.length; };
            w.clearTimeout = () => {};
        }
        w.AbortSignal.timeout = () => undefined;
        w.appState = { proactiveChatEnabled: true, proactiveMemeEnabled: true,
            proactiveNewsChatEnabled: true, proactiveMusicEnabled: true,
            proactiveChatInterval: 20, isRecording: !!options.voice, dom: {} };
        w.appConst = {};
        w.lanlan_config = { lanlan_name: 'Neko' };
        w.realisticGeminiCurrentTurnId = options.lateTurn ? 'previous-turn' : body.turn_id;
        let parts;
        let host = { appendMessage: message => messages.push(message), updateMessage() {} };
        if (options.realHost) {
            for (const file of ['bootstrap-state-and-geometry.js', 'geometry-and-messages.js', 'message-bundle-actions-and-prompts.js']) {
                w.eval(fs.readFileSync(path.join(root, 'static/app/app-react-chat-window', file), 'utf8'));
            }
            parts = w.__appReactChatWindowParts;
            parts.renderWindow = () => {};
            host = Object.assign(w.reactChatWindowHost, { appendMessage: parts.appendMessage,
                updateMessage: parts.updateMessage, removeMessage: parts.removeMessage, clearMessages: parts.clearMessages });
            w.eval(fs.readFileSync(path.join(root, 'static/app/app-character.js'), 'utf8'));
            w.invalidatePendingMusicSearch = () => {};
        }
        if (!options.lateHost) w.reactChatWindowHost = host;
        w.dispatchMusicPlayDetailed = async () => { actions.push('play'); return { ok: true }; };
        w.open = () => { actions.push('open'); };
        w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'fixture' }) };
        w.fetch = async (url, init) => {
            if (options.realHost && url !== '/api/proactive_chat') {
                return { ok: true, json: async () => url === '/api/characters'
                    ? { '猫娘': { Second: { model_type: 'live2d' } } } : { domains: [] } };
            }
            calls.push({ url, body: init?.body && JSON.parse(init.body) });
            assert.equal(url, '/api/proactive_chat', 'no decision or material request before a user action');
            // The HTTP request has started with the original offer context.
            if (options.invalidate === 'clear') host.clearMessages();
            if (options.invalidate === 'switch') {
                await w.handleCatgirlSwitch('Second', 'Neko');
                assert.equal(w.lanlan_config.lanlan_name, 'Second');
                assert.equal(w.appState.isSwitchingCatgirl, false);
            }
            return { ok: true, status: 200, json: async () => body };
        };
        if (!options.noController) w.eval(controllerSource);
        w.eval(options.proactiveSource || proactiveSource);
        assert.equal(await w.appProactive.triggerProactiveChat(), true);
        assert.equal(calls.length, 1);
        assert.equal(!!calls[0].body.voice_mode, !!options.voice);
        // A late turn/host must not flush previously withheld raw attachments.
        w.reactChatWindowHost = host;
        w.realisticGeminiCurrentTurnId = body.turn_id;
        w.appProactive._flushProactiveAttachments(body.turn_id);
        for (const callback of timers.splice(0)) await callback();
        const displayed = parts ? parts.state.messages : messages;
        const expected = options.noController || options.lateHost || options.invalidate ? 0
            : (body.proactive_recommendations || []).length + (body.music_recommendation ? 1 : 0);
        assert.equal(displayed.length, expected, 'only registered cards may be displayed in their original chat context');
        const blocks = displayed.flatMap(message => message.blocks);
        assert.ok(blocks.every(block => ['proactive_recommendation', 'music_recommendation'].includes(block.type)),
            'raw images/source links cannot bypass the durable choice');
        assert.ok(blocks.every(block => block.state === 'pending'));
        assert.deepEqual(actions, []);
        assert.ok(!w._proactiveAttachmentBuffer || !Object.values(w._proactiveAttachmentBuffer)
            .some(value => value.memes?.length || value.links?.length));
        return { cards: blocks.length, requests: calls.length, actions: actions.length };
    } finally { dom.window.close(); }
}

module.exports = { exerciseResponse };

if (require.main === module) {
    for (const category of ['meme', 'news', 'community', 'video', 'personal', 'web', 'vision', 'window', 'screen', 'music']) {
        test(`${category}: failed registration never falls back to raw attachments`, async () => {
            const body = responseFor(category);
            for (const options of [{}, { lateTurn: true }, { voice: true }]) await exerciseResponse(body, options);
        });
        test(`${category}: a registered offer keeps its confirmation card`, async () => {
            const body = responseFor(category, true);
            await exerciseResponse(body);
            if (category !== 'music') await exerciseResponse(body, { voice: true });
        });
        test(`${category}: missing controller or late host does not restore automatic display`, async () => {
            const body = responseFor(category, true);
            for (const options of [{ noController: true }, { lateHost: true }, { lateHost: true, lateTurn: true }]) {
                await exerciseResponse(body, options);
            }
        });
    }
    test('ordinary dialogue has no placeholder card or evidence', async () => {
        await exerciseResponse({ success: true, action: 'chat', turn_id: 'committed-turn',
            source_mode: 'chat', response_text: 'Already delivered dialogue', source_links: [] });
    });
    for (const invalidate of ['clear', 'switch']) {
        for (const category of ['meme', 'screen', 'music']) {
            test(`${category}: real host ${invalidate} retires an in-flight HTTP offer`, async () => {
                const body = responseFor(category, true);
                await exerciseResponse(body, { realHost: true, invalidate });
                if (category !== 'music') await exerciseResponse(body, { realHost: true, invalidate, voice: true });
            });
        }
    }
    test('the registration bound never exposes extra sources as unconfirmed attachments', async () => {
        const body = responseFor('meme', true);
        for (let index = 1; index < 3; index++) {
            const url = `https://i.imgflip.com/registered-${index}.jpg`;
            body.proactive_recommendations.push({ ...body.proactive_recommendations[0], id: `registered-${index}`,
                content: { title: 'Registered material', source: 'Imgflip', url } });
            body.source_links.push({ title: 'Registered material', type: 'meme', url });
        }
        body.source_links.push({ title: 'Unregistered fourth source', type: 'meme', url: 'https://i.imgflip.com/fourth.jpg' });
        await exerciseResponse(body);
    });
}
