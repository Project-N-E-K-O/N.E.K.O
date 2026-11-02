const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');
const frontendRequire = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'));
const { JSDOM } = frontendRequire('jsdom');
// Allows the same behavioral cases to run against the unmodified checkout.
const source = process.env.MUSIC_UI_REVISION
    ? require('node:child_process').execFileSync('git', ['show', `${process.env.MUSIC_UI_REVISION}:static/jukebox/music_ui.js`], {cwd:root, encoding:'utf8'})
    : fs.readFileSync(process.env.MUSIC_UI_SOURCE_PATH
        || path.join(root, 'static/jukebox/music_ui.js'), 'utf8');

async function flush() {
    for (let i = 0; i < 20; i++) await Promise.resolve();
}

function createSurface(t, options = {}) {
    const dom = new JSDOM(`<!doctype html><head>
        <link href="/static/libs/APlayer.min.css"><link href="/static/css/music_ui.css">
        </head><body><div class="app-shell" data-chat-surface-mode="compact">
        <div id="music-player-mount" data-music-player-mount="compact-surface"></div>
        </div></body>`, {
        url: 'http://localhost:48911/chat', runScripts: 'outside-only', pretendToBeVisual: true
    });
    const w = dom.window;
    const timers = new Map();
    const sent = [];
    const errors = [];
    const players = [];
    const trackedMaps = [];
    w.Map = class extends Map { constructor(...args) { super(...args); trackedMaps.push(this); } };
    let now = 100000;
    let timerId = 0;
    let relay = () => {};
    const RealDate = w.Date;
    w.Date = class extends RealDate { static now() { return now; } };
    Object.defineProperty(w.performance, 'now', { value: () => now });
    const schedule = (fn, ms = 0, interval = 0) => {
        const id = ++timerId;
        timers.set(id, { fn, at: now + Number(ms), interval });
        return id;
    };
    w.setTimeout = (fn, ms) => schedule(fn, ms);
    w.setInterval = (fn, ms) => schedule(fn, ms, ms);
    w.clearTimeout = w.clearInterval = (id) => timers.delete(id);
    w.requestAnimationFrame = (fn) => schedule(() => fn(now), 16);
    w.cancelAnimationFrame = w.clearTimeout;
    w.document.hasFocus = () => true;
    w.console = { log() {}, warn(...args) { errors.push(args); }, error(...args) { errors.push(args); } };
    w.fetch = async () => ({ ok: true, json: async () => ({ domains: [] }) });
    if (options.channelHub) {
        w.BroadcastChannel = class {
            constructor(name) {
                this.name = name;
                this.onmessage = null;
                options.channelHub.add(this);
            }
            postMessage(data) {
                sent.push(data);
                for (const peer of options.channelHub) {
                    if (peer !== this && peer.name === this.name && peer.onmessage) {
                        const deliver = () => peer.onmessage && peer.onmessage({data:JSON.parse(JSON.stringify(data))});
                        if (options.asyncTransport) w.queueMicrotask(deliver);
                        else deliver();
                    }
                }
            }
            close() { options.channelHub.delete(this); }
        };
    } else {
        w.nekoElectronMusicBridge = { send(event) { sent.push(event); relay(event); } };
    }
    w.addEventListener('error', (event) => { errors.push([event.error]); event.preventDefault(); });

    class Player {
        constructor(config) {
            this.container = config.container;
            this.autoplayRequested = config.autoplay;
            this.handlers = new Map();
            this.destroyed = false;
            this.audio = new w.EventTarget();
            Object.assign(this.audio, { src: config.audio[0].url, currentSrc: config.audio[0].url,
                currentTime: 0, duration: 60, paused: true, ended: false,
                readyState: options.mediaPending ? 0 : 4, error: null, volume: 0.2 });
            this.audio.play = () => {
                if (this.audio.ended) this.audio.currentTime = 0;
                this.audio.ended = false;
                this.audio.paused = false;
                this.emit('play');
                return Promise.resolve();
            };
            this.audio.pause = () => {
                if (options.pauseThrows) throw new Error('injected pause failure');
                if (!this.audio.paused) {
                    this.audio.paused = true;
                    this.emit('pause');
                }
            };
            this.list = { clear() {}, add() {}, switch() {} };
            players.push(this);
        }
        on(type, fn) {
            if (!this.handlers.has(type)) this.handlers.set(type, []);
            this.handlers.get(type).push(fn);
        }
        emit(type) { for (const fn of this.handlers.get(type) || []) fn(); }
        play() { return this.audio.play(); }
        pause() { this.audio.pause(); }
        toggle() { if (this.audio.paused) this.play(); else this.pause(); }
        seek(time) { this.audio.currentTime = time; this.audio.ended = false; }
        volume(value) { if (typeof value === 'number') this.audio.volume = value; return this.audio.volume; }
        destroy() {
            if (options.destroyThrows) throw new Error('injected destroy failure');
            this.destroyed = true;
            this.audio.paused = true;
            this.audio.src = this.audio.currentSrc = '';
        }
        finish() {
            this.audio.currentTime = this.audio.duration;
            this.audio.paused = true;
            this.audio.ended = true;
            this.emit('timeupdate');
            this.emit('pause');
            this.emit('ended');
        }
    }
    w.APlayer = Player;
    let completeInit;
    if (options.deferInit || options.factoryGlobals) {
        w.initializeAPlayer = (config) => new Promise((resolve, reject) => {
            const finishInit = () => {
                if (options.rejectInit) { reject(new Error('injected factory rejection')); return; }
                if (options.honorCancellation && config.isCurrentRequest && !config.isCurrentRequest()) {
                    resolve(null);
                    return;
                }
                const player = new Player(config);
                // The real factory installs global controls before resolving.
                w.aplayer = player;
                w.aplayerControls = { play: () => player.play() };
                resolve(player);
            };
            if (options.deferInit) completeInit = finishInit;
            else finishInit();
        });
        w.destroyAPlayer = () => {
            if (!w.aplayer) return true;
            try { w.aplayer.destroy(); return true; }
            finally { w.aplayer = null; delete w.aplayerControls; }
        };
    }
    w.eval(source);
    t.after(() => { w.dispatchEvent(new w.PageTransitionEvent('pagehide')); timers.clear(); w.close(); });
    return {
        w, sent, players, errors, timers, trackedMaps,
        setRelay(fn) { relay = fn; },
        completeInit() { assert.ok(completeInit, 'initialization reached'); completeInit(); },
        receive(event) { w.dispatchEvent(new w.CustomEvent('neko:electron-music-bridge', { detail: event })); },
        bar() { return w.document.getElementById('music-player-bar'); },
        clickClose() { assert.ok(this.bar(), 'bar exists before close'); this.bar().querySelector('.music-bar-close').click(); },
        async start(autoplay = false) {
            const promise = w.sendMusicMessageDetailed({name:'test', artist:'test', url:'https://music.163.com/test.mp3'}, autoplay);
            await flush();
            return { promise };
        },
        async advance(ms) {
            const end = now + ms;
            for (let n = 0; n < 20000; n++) {
                await flush();
                const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a,b) => a[1].at - b[1].at)[0];
                if (!next) { now = end; await flush(); return; }
                const [id, timer] = next;
                now = timer.at;
                if (timer.interval) timer.at += timer.interval; else timers.delete(id);
                timer.fn();
            }
            throw new Error('timer loop did not settle');
        }
    };
}

function pair(t, options = {}) {
    const owner = createSurface(t, options);
    const follower = createSurface(t, {channelHub:options.channelHub, asyncTransport:options.asyncTransport});
    const deliver = (surface, event) => {
        if (options.asyncTransport) surface.w.queueMicrotask(() => surface.receive(event));
        else surface.receive(event);
    };
    owner.setRelay(event => deliver(follower, event));
    follower.setRelay(event => deliver(owner, event));
    return { owner, follower };
}

const endedState = { track: {name:'finished', artist:'test'}, playbackId:'finished-1', paused:true,
    ended:true, currentTime:60, duration:60, volume:0.2 };
const remoteEvent = (type, payload) => ({ sender:'remote-owner', type, payload });

function offerRecommendation(s, awarenessState, options = {}) {
    const events = [], awareness = [], messages = new Map();
    const socket = awarenessState === undefined ? null : { readyState: awarenessState,
        send(value) { awareness.push(JSON.parse(value)); } };
    s.w.appState = { socket };
    s.w.lanlan_config = { lanlan_name: 'Neko' };
    s.w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({}) };
    s.w.AbortSignal.timeout = () => undefined;
    s.w.fetch = async (url, init) => {
        if (!init || !init.body) return { ok: true, json: async () => ({ domains: [] }) };
        if (options.capture && url.endsWith('/capture-context')) return { ok:true,
            json:async () => ({success:true,context:options.capture()}) };
        if (options.capture && url.endsWith('/dispatch')) return {ok:true,json:async () => ({success:true,dispatch_grant:true})};
        const event = JSON.parse(init.body); events.push(event);
        const telemetry = options.capture ? {event_id:event.event_id,
            payload_hash:require('node:crypto').createHash('sha256')
                .update(Buffer.from(init.headers['X-NEKO-Feedback-Canonical'], 'base64')).digest('hex'),
            status:'durable', ...(options.telemetry ? options.telemetry(event) : {})} : undefined;
        return { ok: true, status: 200, json: async () => ({ success: true, state: event.kind,
            playback_grant: event.kind === 'accepted', telemetry }) };
    };
    s.w.reactChatWindowHost = { appendMessage(m) { messages.set(m.id, m); },
        updateMessage(id, patch) { Object.assign(messages.get(id), patch); } };
    // Exercise the real consent controller AND dispatcher, not the smoke fixture shortcut.
    s.w.eval(fs.readFileSync(path.join(root, 'static/app/app-chat.js'), 'utf8'));
    s.w.eval(fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8'));
    const id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    s.w.nekoMusicRecommendations.offer({ id, state: 'pending', expires_at: s.w.Date.now()/1000+1800,
        ...(options.offerContext ? {offer_context:options.offerContext} : {}),
        track: { name: 'Recommended', artist: 'Neko', url: 'https://music.163.com/test.mp3', cover: '' } });
    const message = messages.values().next().value;
    const action = name => s.w.dispatchEvent(new s.w.CustomEvent('react-chat-window:action', { detail: { message,
        action: { action: name, payload: { recommendationId: id, kind: 'accepted' } } } }));
    return { events, awareness, socket, message,
        choose: () => action('music_recommendation_decision'), show: () => action('music_recommendation_shown') };
}

async function startRecommendation(s, awarenessState) {
    const r = offerRecommendation(s, awarenessState);
    r.choose();
    await s.advance(100);
    assert.equal(s.players.length, 1);
    assert.equal(s.players[0].audio.paused, false);
    assert.ok(r.events.some(event => event.kind === 'accepted'));
    assert.ok(r.events.some(event => event.kind === 'playing'));
    const audio = s.players[0].audio;
    audio.currentTime = 12;
    audio.played = { length: 1, start: () => 0, end: () => 12 };
    return r;
}

const feedbackBinding = owner => ({owner_key:owner,account_incarnation:'account-a',clear_generation:1,
    registration_id:'device-a',registration_version:1,binding_revision:1,anchor_id:'anchor-a',anchor_token:'proof-a'});

for (const failure of ['authentication_required', 'account_mismatch', 'session_changed']) {
    for (const stage of ['dispatch reply', 'player factory', 'delayed play']) {
        test(`known ${failure} retires original account playback across ${stage}`, async t => {
            const s = createSurface(t, {deferInit:stage === 'player factory'});
            const owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
            const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id},capture:()=>feedbackBinding(owner)});
            await flush();
            const base = s.w.fetch; let release, revoked = false;
            s.w.fetch = async (url, init) => {
                if (revoked && url.endsWith('/capture-context')) return {ok:false,status:503,
                    json:async()=>({success:false,error_code:failure})};
                const result = await base(url, init);
                if (url.endsWith('/dispatch') && stage === 'dispatch reply') return new Promise(resolve => {release = () => resolve(result);});
                return result;
            };
            r.choose(); await flush();
            if (stage === 'dispatch reply') assert.equal(typeof release, 'function', 'real dispatch reached the response barrier');
            revoked = true; await s.w.nekoFeedbackCapture.refresh();
            assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, undefined);
            if (stage === 'dispatch reply') release();
            if (stage === 'player factory') s.completeInit();
            await s.advance(100);
            assert.equal(r.events.filter(e => e.kind === 'playing').length, 0);
            assert.ok(s.players.every(player => player.audio.paused), 'no retired operation reaches actual audio play');
            assert.equal(s.w.isMusicPending(), false, 'the canceled dispatcher releases its own pending state');
            if (stage === 'dispatch reply') {
                assert.equal(s.players.length, 0);
                assert.equal(r.message.blocks[0].state, 'unavailable', 'a spent grant cannot leave the card preparing forever');
            }
        });
    }
}

test('switching to another verified owner and back cannot revive an old in-flight dispatch', async t => {
    const s = createSurface(t), original = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    let owner = original;
    const r = offerRecommendation(s, undefined, {offerContext:{owner_key:original,offer_id:id},capture:()=>feedbackBinding(owner)});
    await flush(); const base = s.w.fetch; let release;
    s.w.fetch = async (url, init) => {
        const result = await base(url, init);
        if (url.endsWith('/dispatch')) return new Promise(resolve => {release = () => resolve(result);});
        return result;
    };
    r.choose(); await flush(); assert.equal(typeof release, 'function');
    owner = 'b'.repeat(64); await s.w.nekoFeedbackCapture.refresh();
    owner = original; await s.w.nekoFeedbackCapture.refresh();
    release(); await s.advance(100);
    assert.equal(s.players.length, 0);
    assert.equal(r.message.blocks[0].state, 'unavailable');
});

for (const variant of ['401 HTML', '401 empty', '401 JSON', '503 HTML']) {
    test(`HTTP identity status is preserved independently of body decoding: ${variant}`, async t => {
        const s = createSurface(t), owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
        const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id},capture:()=>feedbackBinding(owner)});
        await flush(); const base = s.w.fetch; let release, revoked = false;
        s.w.fetch = async (url, init) => {
            if (revoked && url.endsWith('/capture-context')) {
                const body = variant === '401 JSON' ? JSON.stringify({success:false,error_code:'authentication_required'})
                    : variant === '401 empty' ? '' : '<html>Authentication required</html>';
                return new Response(body, {status:variant === '503 HTML' ? 503 : 401,
                    headers:{'Content-Type':variant === '401 JSON' ? 'application/json' : 'text/html'}});
            }
            const result = await base(url, init);
            if (url.endsWith('/dispatch')) return new Promise(resolve => {release = () => resolve(result);});
            return result;
        };
        r.choose(); await flush(); assert.equal(typeof release, 'function');
        revoked = true; await s.w.nekoFeedbackCapture.refresh(); release(); await s.advance(100);
        assert.equal(r.events.filter(event => event.kind === 'playing').length, variant === '503 HTML' ? 1 : 0);
        if (variant === '503 HTML') assert.equal(s.players[0].audio.paused, false);
        else { assert.equal(s.players.length, 0); assert.equal(r.message.blocks[0].state, 'unavailable'); }
    });
}

test('an old-revision HTTP 401 cannot revoke a new page lifecycle operation', async t => {
    const s = createSurface(t), owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id},capture:()=>feedbackBinding(owner)});
    await flush(); const base = s.w.fetch; let finishCapture, finishDispatch;
    s.w.fetch = async (url, init) => {
        if (url.endsWith('/capture-context')) return new Promise(resolve => {
            finishCapture = () => resolve(new Response('<html>Old authentication failure</html>', {status:401}));
        });
        const result = await base(url, init);
        if (url.endsWith('/dispatch')) return new Promise(resolve => {finishDispatch = () => resolve(result);});
        return result;
    };
    const oldRefresh = s.w.nekoFeedbackCapture.refresh(); await flush();
    assert.equal(typeof finishCapture, 'function');
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide'));
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pageshow'));
    r.choose(); await flush(); assert.equal(typeof finishDispatch, 'function');
    finishCapture(); await oldRefresh;
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, owner, 'the old response cannot invalidate current qualification');
    finishDispatch(); await s.advance(100);
    assert.equal(s.players.length, 1);
    assert.equal(s.players[0].audio.paused, false);
    assert.equal(r.events.filter(event => event.kind === 'playing').length, 1);
});

for (const condition of ['transient 503', 'upgrade 426', 'same-owner clear']) {
    test(`${condition} only changes statistics qualification and preserves a pending legitimate dispatch`, async t => {
        const s = createSurface(t), owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
        let generation = 1, failure = false;
        const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id},
            capture:()=>({...feedbackBinding(owner),clear_generation:generation,binding_revision:generation})});
        await flush(); const base = s.w.fetch; let release;
        s.w.fetch = async (url, init) => {
            if (failure && url.endsWith('/capture-context')) return {ok:false,status:condition === 'upgrade 426' ? 426 : 503,
                json:async()=>({success:false,error_code:condition === 'upgrade 426' ? 'upgrade_required' : 'cloud_unavailable'})};
            const result = await base(url, init);
            if (url.endsWith('/dispatch')) return new Promise(resolve => {release = () => resolve(result);});
            return result;
        };
        r.choose(); await flush(); assert.equal(typeof release, 'function');
        if (condition === 'same-owner clear') generation = 2; else failure = true;
        await s.w.nekoFeedbackCapture.refresh();
        release(); await s.advance(100);
        assert.equal(s.players.length, 1);
        assert.equal(s.players[0].audio.paused, false);
        assert.equal(r.events.filter(e => e.kind === 'playing').length, 1);
    });
}

test('an initial anonymous offer without any verified control still plays normally', async t => {
    const s = createSurface(t), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    const r = offerRecommendation(s, undefined, {offerContext:{owner_key:'anonymous-owner',offer_id:id},capture:()=>null});
    await flush(); r.choose(); await s.advance(100);
    assert.equal(s.players.length, 1);
    assert.equal(s.players[0].audio.paused, false);
    assert.equal(r.events.filter(e => e.kind === 'playing').length, 1);
});

for (const authenticatedOrigin of [false, true]) {
    test(`a late first authentication failure only retires a previously bound offer: ${authenticatedOrigin}`, async t => {
        const s = createSurface(t), owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
        const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id,
            ...(authenticatedOrigin ? {origin_context:feedbackBinding(owner)} : {})},capture:()=>null});
        const base = s.w.fetch; let finishCapture, finishDispatch;
        s.w.fetch = async (url, init) => {
            if (url.endsWith('/capture-context')) return new Promise(resolve => {
                finishCapture = () => resolve({ok:false,status:503,json:async()=>({success:false,error_code:'authentication_required'})});
            });
            const result = await base(url, init);
            if (url.endsWith('/dispatch')) return new Promise(resolve => {finishDispatch = () => resolve(result);});
            return result;
        };
        await flush(); assert.equal(typeof finishCapture, 'function');
        assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, undefined, 'no renderer control has ever been verified');
        r.choose(); await flush(); assert.equal(typeof finishDispatch, 'function');
        finishCapture(); await flush(); finishDispatch(); await s.advance(100);
        if (authenticatedOrigin) {
            assert.equal(s.players.length, 0, 'the original native binding still identifies the retired account operation');
            assert.equal(r.message.blocks[0].state, 'unavailable');
        } else {
            assert.equal(s.players.length, 1, 'an unknown or anonymous offer keeps its legitimate business grant');
            assert.equal(s.players[0].audio.paused, false);
        }
    });
}

test('explicit logout cancels the old grant while a new anonymous local choice remains usable', async t => {
    const s = createSurface(t), owner = 'a'.repeat(64), id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id},capture:()=>feedbackBinding(owner)});
    await flush(); const base = s.w.fetch; let release, revoked = false;
    s.w.fetch = async (url, init) => {
        if (revoked && url.endsWith('/capture-context')) return {ok:false,status:503,
            json:async()=>({success:false,error_code:'authentication_required'})};
        const result = await base(url, init);
        if (url.includes(id) && url.endsWith('/dispatch')) return new Promise(resolve => {release = () => resolve(result);});
        return result;
    };
    r.choose(); await flush(); assert.equal(typeof release, 'function');
    revoked = true; await s.w.nekoFeedbackCapture.refresh();
    const anonymousId = '29f3bbca-a275-43d4-923d-1b2ceee91943'; let message;
    const append = s.w.reactChatWindowHost.appendMessage;
    s.w.reactChatWindowHost.appendMessage = value => {message = value; append(value);};
    s.w.nekoMusicRecommendations.offer({id:anonymousId,state:'pending',expires_at:s.w.Date.now()/1000+1800,
        offer_context:{owner_key:'anonymous-owner',offer_id:anonymousId},
        track:{name:'Anonymous new choice',artist:'Neko',url:'https://music.163.com/anonymous.mp3'}});
    s.w.dispatchEvent(new s.w.CustomEvent('react-chat-window:action', {detail:{message,
        action:{action:'music_recommendation_decision',payload:{recommendationId:anonymousId,kind:'accepted'}}}}));
    await s.advance(100);
    assert.equal(s.players.length, 1);
    assert.equal(s.players[0].audio.paused, false);
    assert.equal(s.players[0]._musicPlaybackReportContext.recommendationId, anonymousId);
    release(); await s.advance(100);
    assert.equal(s.players.length, 1, 'releasing the old account response never takes over the new player');
    assert.equal(s.players[0]._musicPlaybackReportContext.recommendationId, anonymousId);
});

test('v2 real player callbacks checkpoint independent disjoint ranges without changing playback state', async t => {
    const s = createSurface(t), events = [];
    const offerContext = { owner_key: 'a'.repeat(64), offer_id: 'offer-v2', origin_context: { capture: { tick_ms: 1 } } };
    let generation = 1;
    s.w.nekoFeedbackCapture = { capture: () => ({ clear_generation: generation, capture: { tick_ms: Math.floor(s.w.performance.now()) } }) };
    s.w.nekoMusicRecommendations = { recordPlayback(id, kind, fields) { events.push(JSON.parse(JSON.stringify({ id, kind, fields }))); } };
    const playing = s.w.sendMusicMessageDetailed({name:'v2', url:'https://music.163.com/test.mp3'}, true,
        {recommendationId:'offer-v2', offerContext});
    await s.advance(100); await playing;
    const player = s.players[0], audio = player.audio;
    for (let i = 1; i <= 16; i++) { await s.advance(1000); audio.currentTime = i; player.emit('timeupdate'); }
    const checkpoint = events.find(e => e.kind === 'measurement');
    assert.ok(checkpoint, 'existing timeupdate produces a 15-second checkpoint');
    assert.equal(checkpoint.fields.measurement.version, 'playback_elapsed_observed_v2');
    assert.equal(checkpoint.fields.measurement.range_start_ms, 0);
    assert.equal(checkpoint.fields.measurement.range_end_ms, 15000);
    assert.equal(audio.paused, false);
    assert.equal(events.filter(e => e.kind === 'playing').length, 1, 'checkpoints do not fabricate playing transitions');
    audio.pause();
    const paused = events.findLast(e => e.kind === 'paused');
    assert.equal(paused.fields.measurement.range_start_ms, 15000);
    assert.equal(paused.fields.measurement.range_end_ms, 16000);
    offerContext.owner_key = 'mutated-after-dispatch';
    assert.equal(player._musicPlaybackReportContext.offerContext.owner_key, 'a'.repeat(64));
    generation = 2;
    s.w.dispatchEvent(new s.w.Event('neko:feedback-capture-changed'));
    await audio.play();
    for (let i = 1; i <= 2; i++) { await s.advance(1000); audio.currentTime = 16 + i; player.emit('timeupdate'); }
    audio.pause();
    const renewed = events.findLast(e => e.kind === 'paused').fields.measurement;
    assert.equal(renewed.range_start_ms, 0);
    assert.equal(renewed.range_end_ms, 2000);
    assert.notEqual(renewed.epoch_id, checkpoint.fields.measurement.epoch_id);
    assert.equal(renewed.origin_context.clear_generation, 2);
    assert.equal(checkpoint.fields.measurement.origin_context.clear_generation, 1);
    assert.equal(s.players.length, 1, 'control refresh does not restart the audio player');
    audio.ended = true; player.emit('ended');
    await audio.play();
    for (let i = 1; i <= 2; i++) { await s.advance(1000); audio.currentTime = i; player.emit('timeupdate'); }
    audio.pause();
    const replay = events.findLast(e => e.kind === 'measurement').fields.measurement;
    assert.equal(replay.range_start_ms, 2000);
    assert.equal(replay.range_end_ms, 4000, 'a short replay must flush on pause even after the business state became terminal');
    assert.equal(events.findLast(e => e.kind !== 'measurement').kind, 'ended', 'replay statistics do not rewrite the old terminal business state');
});

test('actual app capture bridge advances a playing audio after clear and holds observations during failed refresh', async t => {
    const s = createSurface(t);
    const id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    let generation = 1, failed = false;
    const owner = 'a'.repeat(64);
    const r = offerRecommendation(s, undefined, {offerContext:{owner_key:owner,offer_id:id,origin_context:{capture:{tick_ms:0}}},
        capture() {
            if (failed) throw new TypeError('synthetic network failure');
            return {owner_key:owner,account_incarnation:'account-a',clear_generation:generation,
                registration_id:'device-a',registration_version:1,binding_revision:generation,
                anchor_id:'anchor-'+generation,anchor_token:'proof-'+generation};
        }, telemetry(event) { return event.context.clear_generation < generation ? {status:'discarded',reason:'binding_changed'} : {}; } });
    await flush(); r.choose(); await s.advance(100);
    const player = s.players[0], audio = player.audio;
    generation = 2;
    for (let i = 1; i <= 16; i++) { await s.advance(1000); audio.currentTime = i; player.emit('timeupdate'); await flush(); }
    const retired = r.events.find(event => event.kind === 'measurement');
    assert.equal(retired.context.clear_generation, 1);
    assert.equal(s.w.nekoFeedbackCapture.capture().clear_generation, 2);
    assert.equal(audio.paused, false);
    assert.equal(s.players.length, 1);
    failed = true; await s.w.nekoFeedbackCapture.refresh();
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, undefined);
    for (let i = 17; i <= 18; i++) { await s.advance(1000); audio.currentTime = i; player.emit('timeupdate'); }
    audio.pause(); await flush();
    const held = r.events.findLast(event => event.measurement);
    assert.equal(held.context.owner_key, undefined);
    assert.equal(held.measurement.origin_context.owner_key, undefined);
    failed = false; generation = 3; await s.w.nekoFeedbackCapture.refresh();
    await audio.play();
    for (let i = 19; i <= 20; i++) { await s.advance(1000); audio.currentTime = i; player.emit('timeupdate'); }
    audio.pause(); await flush();
    const resumed = r.events.findLast(event => event.measurement);
    assert.equal(resumed.context.clear_generation, 3);
    assert.equal(resumed.measurement.origin_context.clear_generation, 3);
    assert.equal(resumed.measurement.range_start_ms, 0);
    assert.equal(resumed.measurement.range_end_ms, 2000);
    assert.equal(retired.context.clear_generation, 1);
    assert.equal(held.context.owner_key, undefined);
    assert.equal(s.players.length, 1, 'qualification changes never rebuild or replace audio');
    assert.equal(r.events.filter(event => event.kind === 'accepted').length, 1);
});

for (const stage of ['dispatcher', 'library', 'factory', 'delayed play', 'media wait', 'interaction unlock']) {
    for (const invalidation of ['hidden then restored', 'clear', 'character']) {
        test(`recommendation cancellation crosses ${stage} after ${invalidation}`, async t => {
            const s = createSurface(t, { deferInit: stage === 'factory', mediaPending: stage === 'media wait' });
            const r = offerRecommendation(s);
            const send = s.w.sendMusicMessage, detailed = s.w.sendMusicMessageDetailed, Player = s.w.APlayer;
            if (stage === 'dispatcher') { delete s.w.sendMusicMessage; delete s.w.sendMusicMessageDetailed; }
            if (stage === 'library') delete s.w.APlayer;
            r.show(); await s.advance(10000);
            assert.equal(r.events.filter(event => event.kind === 'accepted').length, 1);
            if (invalidation === 'hidden then restored') {
                let hidden = true;
                Object.defineProperty(s.w.document, 'visibilityState', { configurable: true, get: () => hidden ? 'hidden' : 'visible' });
                s.w.document.dispatchEvent(new s.w.Event('visibilitychange'));
                hidden = false;
                s.w.document.dispatchEvent(new s.w.Event('visibilitychange'));
            } else if (invalidation === 'clear') s.w.dispatchEvent(new s.w.Event('react-chat-window:messages-cleared'));
            else s.w.lanlan_config.lanlan_name = 'Other';
            if (stage === 'dispatcher') {
                s.w.sendMusicMessage = send; s.w.sendMusicMessageDetailed = detailed;
                s.w.dispatchEvent(new s.w.Event('music-ui-ready'));
            }
            if (stage === 'library') {
                s.w.APlayer = Player;
                const script = [...s.w.document.scripts].find(node => /APlayer/.test(node.src));
                assert.ok(script, 'library wait reached'); script.onload();
            }
            if (stage === 'factory') s.completeInit();
            if (stage === 'interaction unlock') s.w.document.body.click();
            await s.advance(101);
            assert.equal(r.events.filter(event => event.kind === 'playing').length, 0);
            assert.ok(s.players.every(player => player.audio.paused), 'no retired recommendation starts audio');
            assert.equal(s.w.isMusicPending(), false, 'the owned pending count is released');
            assert.equal(s.bar(), null, 'canceled initialization releases its own bar');
            if (invalidation === 'hidden then restored') {
                assert.equal(r.message.blocks[0].state, 'failed');
                assert.equal(r.events.filter(event => event.kind === 'failed').length, 1);
            }
        });
    }
}

test('explicit recommendation remains allowed through delayed play when hidden', async t => {
    const s = createSurface(t), r = offerRecommendation(s);
    r.choose(); await flush();
    Object.defineProperty(s.w.document, 'visibilityState', { get: () => 'hidden' });
    s.w.document.dispatchEvent(new s.w.Event('visibilitychange'));
    await s.advance(101);
    assert.equal(s.players[0].audio.paused, false);
    assert.equal(r.events.filter(event => event.kind === 'playing').length, 1);
});

test('retiring a recommendation does not destroy a replacement player', async t => {
    const s = createSurface(t), r = offerRecommendation(s);
    r.show(); await s.advance(10000);
    const old = s.players[0];
    const replacement = s.w.dispatchMusicPlayDetailed({ name: 'Recommended', artist: 'Neko', url: 'https://music.163.com/test.mp3' });
    await flush();
    s.w.dispatchEvent(new s.w.Event('react-chat-window:messages-cleared'));
    await s.advance(101);
    assert.equal((await replacement).ok, true);
    assert.equal(s.players.at(-1), old, 'same media can transfer to a new request on the existing player');
    assert.equal(old._musicPlaybackReportContext.recommendationId, '');
    assert.equal(s.players.at(-1).destroyed, false);
    assert.equal(s.players.at(-1).audio.paused, false);
});

test('hiding after music has started does not cancel playback while metadata is pending', async t => {
    const s = createSurface(t, { mediaPending: true }), r = offerRecommendation(s);
    r.show(); await s.advance(10100);
    const player = s.players[0];
    assert.equal(player.audio.paused, false);
    Object.defineProperty(s.w.document, 'visibilityState', { get: () => 'hidden' });
    s.w.document.dispatchEvent(new s.w.Event('visibilitychange'));
    player.audio.dispatchEvent(new s.w.Event('loadedmetadata'));
    await flush();
    assert.equal(player.destroyed, false);
    assert.equal(player.audio.paused, false);
    assert.equal(r.events.filter(event => event.kind === 'failed').length, 0);
});

for (const transport of ['IPC', 'BroadcastChannel']) {
    for (const awarenessState of [undefined, 1, 3]) {
        for (const paused of [false, true]) {
            test(`recommendation handoff preserves final listening facts (${transport}, socket=${awarenessState}, paused=${paused})`, async t => {
                const { owner, follower } = pair(t, { asyncTransport: true,
                    ...(transport === 'BroadcastChannel' ? { channelHub: new Set() } : {}) });
                const r = await startRecommendation(owner, awarenessState);
                const previousPlayer = owner.players[0];
                if (paused) previousPlayer.pause();
                await (await follower.start(true)).promise;
                await follower.advance(100);
                await owner.advance(1000);
                await flush();
                assert.equal(previousPlayer.destroyed, true, 'new owner retires the old player');
                assert.equal(follower.players[0].destroyed, false, 'new owner keeps its player');
                assert.equal(follower.players[0].audio.paused, false);
                const terminal = r.events.filter(event => ['stopped', 'ended', 'failed'].includes(event.kind));
                assert.equal(terminal.length, 1, 'handoff records exactly one terminal fact');
                assert.equal(terminal[0].kind, 'stopped');
                assert.equal(terminal[0].listened_seconds, 12);
                assert.equal(terminal[0].position_seconds, 12);
                assert.equal(terminal[0].playback_id, r.events.find(event => event.kind === 'playing').playback_id);
                const count = r.events.length;
                previousPlayer.emit('play'); previousPlayer.emit('pause'); previousPlayer.emit('ended');
                owner.w.destroyMusicPlayer(true, true, true);
                await flush();
                assert.equal(r.events.length, count, 'late callbacks cannot re-open the retired evidence');
            });
        }
    }
}

for (const [name, startState, endState] of [['missing', undefined, undefined], ['closed', 3, 3],
    ['open', 1, 1], ['reconnected', 3, 1], ['disconnected after playing', 1, 3]]) {
    test(`recommendation close reports its final evidence with ${name} awareness socket`, async t => {
        const s = createSurface(t), r = await startRecommendation(s, startState);
        if (r.socket) r.socket.readyState = endState;
        s.clickClose(); await flush();
        s.w.destroyMusicPlayer(true, true, true); await flush();
        const terminal = r.events.filter(event => ['stopped', 'ended', 'failed'].includes(event.kind));
        assert.equal(terminal.length, 1, 'close and repeated teardown produce exactly one terminal event');
        assert.equal(terminal[0].kind, 'stopped');
        assert.equal(terminal[0].listened_seconds, 12);
        assert.equal(terminal[0].position_seconds, 12);
        assert.equal(r.message.blocks[0].state, 'stopped');
        if (startState === 3 && endState === 3 || startState === undefined) assert.equal(r.awareness.length, 0);
    });
}

for (const state of ['ended', 'failed']) {
    test(`recommendation ${state} stays terminal after close without awareness socket`, async t => {
        const s = createSurface(t), r = await startRecommendation(s);
        if (state === 'ended') s.players[0].finish();
        else { s.players[0].audio.error = { code: 3 }; s.players[0].emit('error'); await s.advance(220); }
        await flush();
        s.clickClose(); await flush();
        assert.deepEqual(r.events.filter(event => ['stopped', 'ended', 'failed'].includes(event.kind)).map(event => event.kind), [state]);
        assert.equal(r.message.blocks[0].state, state);
    });
}

test('paused recommendation closes with final evidence when awareness never connected', async t => {
    const s = createSurface(t), r = await startRecommendation(s, 3);
    s.players[0].pause(); await flush();
    assert.equal(r.message.blocks[0].state, 'paused');
    s.clickClose(); await flush();
    assert.deepEqual(r.events.filter(event => ['paused', 'stopped'].includes(event.kind)).map(event => event.kind), ['paused', 'stopped']);
    assert.equal(r.events.at(-1).listened_seconds, 12);
});

test('replacing an offline-started recommendation finalizes only the old evidence identity', async t => {
    const s = createSurface(t), r = await startRecommendation(s);
    // Ordinary user intent can adopt the same media through the real dispatcher.
    const result = await s.w.dispatchMusicPlayDetailed({ name: 'Recommended', artist: 'Neko', url: 'https://music.163.com/test.mp3' });
    await flush();
    assert.equal(result.ok, true);
    s.clickClose(); await flush();
    const terminal = r.events.filter(event => ['stopped', 'ended', 'failed'].includes(event.kind));
    assert.equal(terminal.length, 1);
    assert.equal(terminal[0].kind, 'stopped');
    assert.equal(terminal[0].reason, 'replaced');
    assert.equal(terminal[0].listened_seconds, 12);
    assert.equal(terminal[0].playback_id, r.events.find(event => event.kind === 'playing').playback_id);
});

for (const transport of ['Electron IPC', 'web BroadcastChannel']) {
    test(`${transport} mirrors chat append/update and bookkeeping without echo`, t => {
        const options=transport==='web BroadcastChannel'?{channelHub:new Set()}:{};
        const {owner,follower}=pair(t,options);
        const events=[], messages=new Map();
        follower.w.reactChatWindowHost={appendMessage(m){messages.set(m.id,m);},
            updateMessage(id,patch){Object.assign(messages.get(id),patch);}};
        follower.w.addEventListener('neko:chat-mirror',e=>events.push(e.detail));
        const message={id:'consent',blocks:[{type:'music_recommendation',state:'pending'}]};
        owner.w.__nekoMirrorChatAppend(null,message);
        owner.w.__nekoMirrorChatUpdate(null,'consent',{blocks:[{type:'music_recommendation',state:'skipped'}]});
        assert.equal(messages.get('consent').blocks[0].state,'skipped');
        assert.equal(events.length,2);
        assert.equal(follower.sent.filter(e=>e.type==='chat_mirror'||e.type==='append'||e.type==='update').length,0);
        assert.equal(owner.players.length+follower.players.length,0);
    });
}

for (const stage of ['late player', 'canceled factory', 'rejected factory', 'media wait']) {
    test(`rejected replacement removes superseded bar after ${stage}`, async t => {
        const s = createSurface(t, {
            deferInit: stage !== 'media wait', mediaPending: stage === 'media wait',
            honorCancellation: stage === 'canceled factory', rejectInit: stage === 'rejected factory'
        });
        const {promise} = await s.start();
        const oldBar = s.bar();
        const mount = s.w.document.getElementById('music-player-mount');
        mount.remove();
        await flush();
        const replacement = s.w.sendMusicMessageDetailed({name:'rejected', url:'https://not-allowed.invalid/song.mp3'}, false);
        await s.advance(500);
        assert.equal((await replacement).reason, 'unsafe_url');
        if (stage !== 'media wait') s.completeInit();
        await flush();
        assert.equal((await promise).reason, 'superseded');
        s.w.document.querySelector('.app-shell').appendChild(mount);
        await s.advance(400);
        assert.equal(s.bar(), null, 'superseded bar must not be restored');
        assert.equal(oldBar.isConnected, false);
        assert.equal(s.w.getMusicPlayerInstance(), null);
        assert.equal(s.w.isMusicOccupied(), false);
        assert.ok(s.players.every(player => player.destroyed), 'canceled media must be released');
    });
}

for (const mode of ['duplicate', 'paused', 'resume']) {
    for (const readyEvent of [false, true]) {
        test(`same-track ${mode} retains adopted player with readiness event=${readyEvent}`, async t => {
            const s = createSurface(t, {mediaPending:true});
            const {promise} = await s.start();
            const player = s.players[0];
            const bar = s.bar();
            const identity = s.sent.find(event => event.type === 'bar_state').payload.playbackId;
            player.audio.readyState = 4;
            if (mode === 'duplicate') player.play();
            // Resolve the old wait without giving its continuation a turn, or
            // leave it pending so the new request cancels it itself.
            if (readyEvent) player.audio.dispatchEvent(new s.w.Event('canplay'));
            const replacement = s.w.sendMusicMessageDetailed(
                {name:'test', artist:'test', url:'https://music.163.com/test.mp3'}, mode !== 'paused');
            assert.equal((await replacement).reason, mode === 'duplicate' ? 'duplicate' : 'already_playing');
            assert.equal((await promise).reason, 'superseded');
            await s.advance(400);
            assert.equal(s.w.getMusicPlayerInstance(), player, 'new request owns the reused instance');
            assert.equal(s.bar(), bar);
            assert.equal(player.destroyed, false);
            assert.equal(player.audio.paused, mode === 'paused');
            assert.equal(s.sent.some(event => event.type === 'bar_destroyed' && event.payload.playbackId === identity), false);
            s.clickClose();
            await s.advance(400);
            assert.equal(s.bar(), null);
            assert.equal(player.destroyed, true);
        });
    }
}

test('superseded cleanup preserves a queued valid replacement request', async t => {
    const s = createSurface(t, {deferInit:true, honorCancellation:true});
    const {promise} = await s.start();
    const replacement = s.w.sendMusicMessageDetailed({name:'replacement', url:'https://music.163.com/next.mp3'}, false);
    await flush();
    s.completeInit();
    await flush();
    assert.equal((await promise).reason, 'superseded');
    s.completeInit();
    await flush();
    assert.equal((await replacement).ok, true);
    assert.equal(s.bar().querySelector('.music-bar-title-seg-primary').textContent, 'replacement');
    assert.equal(s.w.getMusicPlayerInstance().destroyed, false);
});

for (const transport of ['IPC', 'BroadcastChannel']) {
    for (const pending of [true, false]) {
        test(`${transport} close requires current playback identity while ${pending ? 'pending' : 'ready'}`, async t => {
            const s = createSurface(t, {deferInit:pending, asyncTransport:true,
                ...(transport === 'BroadcastChannel' ? {channelHub:new Set()} : {})});
            const {promise} = await s.start();
            if (!pending) await promise;
            const state = s.sent.find(event => event.type === (transport === 'IPC' ? 'bar_state' : 'state'));
            const playbackId = (state.payload || state).playbackId;
            assert.ok(playbackId);
            const sender = transport === 'BroadcastChannel' ? new s.w.BroadcastChannel('neko_music_bar') : null;
            if (sender) t.after(() => sender.close());
            const send = async (identity, ctrlId) => {
                const payload = {target:state.sender, action:'close', ctrlId, ...identity};
                if (sender) sender.postMessage({type:'ctrl', sender:'test-follower', ...payload});
                else s.receive({type:'bar_ctrl', sender:'test-follower', payload});
                await s.advance(400);
            };
            await send({}, 'missing');
            assert.ok(s.bar(), 'identity-less close must be ignored');
            await send({playbackId:'previous-playback'}, 'stale');
            assert.ok(s.bar(), 'stale identity must be ignored');
            await send({playbackId}, 'current');
            assert.equal(s.bar(), null, 'matching identity must still close');
            if (pending) { s.completeInit(); await flush(); await promise; }
            assert.equal(s.w.getMusicPlayerInstance(), null);
        });
    }
}

test('local close cancels a deferred initialization before its player can start', async t => {
    const s = createSurface(t, {deferInit:true});
    const { promise } = await s.start();
    assert.ok(s.bar());
    s.clickClose();
    await s.advance(350);
    assert.equal(s.bar(), null);
    s.completeInit();
    await flush();
    assert.equal((await promise).ok, false);
    assert.equal(s.w.getMusicPlayerInstance(), null);
    assert.equal(s.players[0].destroyed, true);
    assert.equal(s.w.aplayer, null, 'late initialization must release its global player');
    assert.equal(s.w.aplayerControls, undefined, 'late initialization must release global controls');
});

test('mirror close cancels its owner while initialization is pending', async t => {
    const {owner, follower} = pair(t, {deferInit:true});
    const {promise} = await owner.start();
    follower.clickClose();
    await owner.advance(350);
    await follower.advance(350);
    assert.equal(follower.bar(), null);
    owner.completeInit();
    await flush();
    assert.equal((await promise).ok, false);
    assert.equal(owner.w.getMusicPlayerInstance(), null);
});

for (const state of ['playing', 'paused', 'ended']) {
  for (const clickedSurface of ['owner', 'follower']) {
    test(`${clickedSurface} close works in ${state} state`, async t => {
        const {owner, follower} = pair(t);
        const {promise} = await owner.start();
        assert.equal((await promise).ok, true);
        const player = owner.players[0];
        player.play();
        if (state === 'paused') player.pause();
        if (state === 'ended') player.finish();
        (clickedSurface === 'owner' ? owner : follower).clickClose();
        await owner.advance(400);
        await follower.advance(400);
        assert.equal(owner.bar(), null);
        assert.equal(follower.bar(), null);
        assert.equal(player.destroyed, true);
        assert.equal(player.audio.paused, true);
        assert.equal(player.audio.src, '');
    });
  }
}

test('a finished song automatically removes owner and mirror after 21 seconds', async t => {
    const {owner, follower} = pair(t);
    const {promise} = await owner.start();
    await promise;
    owner.players[0].play();
    owner.players[0].finish();
    await owner.advance(20999);
    assert.ok(owner.bar());
    await owner.advance(401);
    await follower.advance(400);
    assert.equal(owner.bar(), null);
    assert.equal(follower.bar(), null);
});

test('destroy while the mirror mount is detached prevents a zombie on remount', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    await s.advance(20);
    const mount = s.w.document.getElementById('music-player-mount');
    mount.remove();
    await flush();
    s.receive(remoteEvent('bar_destroyed', {playbackId:endedState.playbackId, fullTeardown:true}));
    s.w.document.querySelector('.app-shell').appendChild(mount);
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('destroy clears a cached mirror state even before a mount exists', async t => {
    const s = createSurface(t);
    const mount = s.w.document.getElementById('music-player-mount');
    mount.remove();
    s.receive(remoteEvent('bar_state', endedState));
    s.receive(remoteEvent('bar_destroyed', {playbackId:endedState.playbackId, fullTeardown:true}));
    s.w.document.querySelector('.app-shell').appendChild(mount);
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('late state from a destroyed playback cannot revive the mirror', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    s.receive(remoteEvent('bar_destroyed', {playbackId:endedState.playbackId, fullTeardown:true}));
    await s.advance(400);
    assert.equal(s.bar(), null);
    s.receive(remoteEvent('bar_state', endedState));
    await s.advance(20);
    assert.equal(s.bar(), null);
    s.receive(remoteEvent('bar_state', {...endedState, playbackId:'new-song'}));
    assert.ok(s.bar(), 'a different playback must still appear');
});

test('close while media readiness is pending promptly releases the request', async t => {
    const s = createSurface(t, {mediaPending:true});
    const {promise} = await s.start();
    let result;
    promise.then(value => { result = value; });
    s.clickClose();
    await s.advance(400);
    assert.equal(s.bar(), null);
    assert.ok(result, 'close should settle the request without waiting for the 10s media timeout');
    assert.equal(result.ok, false);
    assert.equal(s.w.isMusicPending(), false);
});

test('a mirror remains closeable when its owner no longer responds', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    s.clickClose();
    await s.advance(400);
    assert.equal(s.bar(), null);
    assert.ok(s.sent.some(event => event.type === 'bar_ctrl' && event.payload.action === 'close'));
    s.receive(remoteEvent('bar_state', endedState));
    assert.equal(s.bar(), null, 'old owner state must not undo the user dismissal');
});

test('a new mirror playback replaces a closed bar during its fade', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    s.receive(remoteEvent('bar_destroyed', {playbackId:endedState.playbackId, fullTeardown:true}));
    s.receive(remoteEvent('bar_state', {...endedState, playbackId:'new-song', track:{name:'new-song'}}));
    await s.advance(400);
    assert.ok(s.bar());
    assert.equal(s.bar().querySelector('.music-bar-title-seg-primary').textContent, 'new-song');
    s.clickClose();
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('local destruction while its mount is detached prevents remounting', async t => {
    const s = createSurface(t);
    await (await s.start()).promise;
    const mount = s.w.document.getElementById('music-player-mount');
    mount.remove();
    await flush();
    s.w.destroyMusicPlayer(true, true, true);
    s.w.document.querySelector('.app-shell').appendChild(mount);
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('new local playback survives a previous close fade', async t => {
    const s = createSurface(t);
    await (await s.start()).promise;
    s.clickClose();
    const result = await (await s.start()).promise;
    await s.advance(400);
    assert.equal(result.ok, true);
    assert.ok(s.bar());
    assert.equal(s.w.getMusicPlayerInstance().destroyed, false);
    s.clickClose();
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('a stale close targeting a previous playback cannot stop the next one', async t => {
    const {owner, follower} = pair(t);
    await (await owner.start()).promise;
    follower.setRelay(() => {});
    follower.clickClose();
    const oldClose = follower.sent.find(event => event.type === 'bar_ctrl');
    owner.clickClose();
    await owner.advance(400);
    await (await owner.start()).promise;
    owner.receive(oldClose);
    await owner.advance(400);
    assert.ok(owner.bar());
    assert.equal(owner.w.getMusicPlayerInstance().destroyed, false);
});

for (const failure of ['pauseThrows', 'destroyThrows']) {
    test(`cleanup still removes the UI when the player throws: ${failure}`, async t => {
        const s = createSurface(t, {[failure]:true});
        await (await s.start()).promise;
        s.clickClose();
        await s.advance(400);
        assert.equal(s.bar(), null);
        assert.equal(s.w.getMusicPlayerInstance(), null);
        assert.equal(s.players[0].audio.paused, true);
        assert.equal(s.players[0].audio.src, '', 'failed library cleanup must still release the media source');
    });
}

test('browser BroadcastChannel close also cancels pending initialization', async t => {
    const {owner, follower} = pair(t, {deferInit:true, channelHub:new Set()});
    const {promise} = await owner.start();
    follower.clickClose();
    await owner.advance(400);
    await follower.advance(400);
    assert.equal(owner.bar(), null);
    assert.equal(follower.bar(), null);
    owner.completeInit();
    await flush();
    assert.equal((await promise).ok, false);
    assert.equal(owner.players[0].destroyed, true);
});

test('browser BroadcastChannel closes a finished song and rejects its late state', async t => {
    const hub = new Set();
    const {owner, follower} = pair(t, {channelHub:hub});
    await (await owner.start()).promise;
    owner.players[0].play();
    owner.players[0].finish();
    const oldState = owner.sent.filter(event => event.type === 'state').at(-1);
    follower.clickClose();
    await owner.advance(400);
    await follower.advance(400);
    for (const channel of hub) {
        if (channel.name === 'neko_music_bar') channel.onmessage({data:oldState});
    }
    await follower.advance(400);
    assert.equal(owner.bar(), null);
    assert.equal(follower.bar(), null);
});

test('closed playback tracking is bounded, expires, and releases on unload', async t => {
    const s = createSurface(t);
    for (let i = 0; i < 260; i++) {
        s.receive(remoteEvent('bar_destroyed', {playbackId:`closed-${i}`, fullTeardown:true}));
    }
    const populated = s.trackedMaps.filter(map => map.size > 0);
    assert.ok(populated.length, 'closed identities are tracked');
    for (const map of populated) assert.ok(map.size <= 128, 'resident identity tracking must be bounded');
    await s.advance(5 * 60 * 1000 + 1);
    s.receive(remoteEvent('bar_state', {...endedState, playbackId:'new-playback'}));
    for (const map of populated) assert.equal(map.size, 0, 'expired identities release on the next event');
    s.receive(remoteEvent('bar_destroyed', {playbackId:'new-playback', fullTeardown:true}));
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide'));
    for (const map of populated) assert.equal(map.size, 0, 'unload releases retained identities');
    assert.equal(s.bar(), null);
});

test('normal React mount replacement still relocates a live mirror', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    await s.advance(20);
    const bar = s.bar();
    const mount = s.w.document.getElementById('music-player-mount');
    const replacement = mount.cloneNode(false);
    mount.replaceWith(replacement);
    await s.advance(40);
    assert.equal(s.bar(), bar);
    assert.equal(bar.parentNode, replacement);
    s.receive(remoteEvent('bar_destroyed', {playbackId:endedState.playbackId, fullTeardown:true}));
    await s.advance(400);
    assert.equal(s.bar(), null);
});

test('a state update during mount replacement does not create duplicate mirror bars', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    await s.advance(20);
    const mount = s.w.document.getElementById('music-player-mount');
    mount.replaceWith(mount.cloneNode(false));
    await flush();
    s.receive(remoteEvent('bar_state', {...endedState, currentTime:30}));
    await s.advance(40);
    assert.equal(s.w.document.querySelectorAll('#music-player-bar').length, 1);
    s.clickClose();
    await s.advance(400);
    assert.equal(s.bar(), null);
});

for (const initializing of [false, true]) {
    test(`local bar relocates when an attribute mutation precedes removal (initializing=${initializing})`, async t => {
        const s = createSurface(t, {deferInit:initializing});
        const {promise} = await s.start();
        if (!initializing) await promise;
        await s.advance(20);
        const bar = s.bar();
        const mount = s.w.document.getElementById('music-player-mount');
        mount.classList.add('changing-layout');
        const replacement = mount.cloneNode(false);
        mount.replaceWith(replacement);
        await s.advance(40);
        assert.equal(s.bar(), bar);
        assert.equal(bar.parentNode, replacement);
        s.clickClose();
        if (initializing) { s.completeInit(); await promise; }
        await s.advance(400);
        assert.equal(s.bar(), null);
    });
}

test('detaching an active local bar does not lose music occupancy', async t => {
    const s = createSurface(t);
    await (await s.start()).promise;
    s.players[0].play();
    s.w.document.getElementById('music-player-mount').remove();
    assert.equal(s.w.isMusicOccupied(), true);
    s.w.destroyMusicPlayer(true, true, true);
    assert.equal(s.w.isMusicOccupied(), false);
});

test('requesting the same song while detached preserves its player', async t => {
    const s = createSurface(t);
    await (await s.start()).promise;
    s.players[0].play();
    s.w.document.getElementById('music-player-mount').remove();
    await (await s.start()).promise;
    assert.equal(s.players.length, 1);
    assert.equal(s.players[0].destroyed, false);
});

test('non-full teardown releases factory global controls', async t => {
    const s = createSurface(t, {factoryGlobals:true});
    await (await s.start()).promise;
    assert.ok(s.w.aplayerControls);
    s.w.destroyMusicPlayer(true, false, true);
    assert.equal(s.w.aplayerControls, undefined);
    assert.equal(s.w.aplayer, null);
    assert.equal(s.players[0].destroyed, true);
});

test('canceled deferred autoplay is never delegated to the constructor', async t => {
    const s = createSurface(t, {deferInit:true});
    const {promise} = await s.start(true);
    s.clickClose();
    s.completeInit();
    await promise;
    assert.equal(s.players[0].autoplayRequested, false);
    assert.equal(s.players[0].destroyed, true);
});

test('canceling beforeunload retains dismissed playback identities', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    s.clickClose();
    await s.advance(400);
    s.w.addEventListener('beforeunload', event => event.preventDefault());
    const event = new s.w.Event('beforeunload', {cancelable:true});
    assert.equal(s.w.dispatchEvent(event), false);
    s.receive(remoteEvent('bar_state', endedState));
    await s.advance(40);
    assert.equal(s.bar(), null);
});

test('real APlayer factory checks cancellation after library loading and before construction', async t => {
    const {pathToFileURL} = require('node:url');
    const s = createSurface(t);
    const saved = Object.fromEntries(['window', 'document', 'APlayer'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    t.after(() => {
        for (const [key, descriptor] of Object.entries(saved)) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
    globalThis.window = s.w;
    globalThis.document = s.w.document;
    delete globalThis.APlayer;
    const factory = await import(pathToFileURL(path.join(root, 'static/js/APlayer/main.js')).href);
    let current = true;
    let constructed = 0;
    const pending = factory.initializeAPlayer({isCurrentRequest:() => current, autoplay:true});
    current = false;
    globalThis.APlayer = class { constructor() { constructed++; throw new Error('canceled request reached constructor'); } };
    s.w.document.querySelector('script[src*="APlayer.min.js"]').onload();
    assert.equal(await pending, null);
    assert.equal(constructed, 0);
    assert.equal(s.w.aplayerControls, undefined);
});

test('caller supplies the factory cancellation check and treats null as superseded', async t => {
    const s = createSurface(t, {deferInit:true, honorCancellation:true});
    const {promise} = await s.start(true);
    s.clickClose();
    s.completeInit();
    const result = await promise;
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'superseded');
    assert.equal(s.players.length, 0);
});

test('valid autoplay is scheduled after initialization and can still be canceled', async t => {
    const s = createSurface(t);
    await (await s.start(true)).promise;
    assert.equal(s.players[0].autoplayRequested, false);
    await s.advance(100);
    assert.equal(s.players[0].audio.paused, false);
    s.clickClose();
    assert.equal(s.players[0].audio.paused, true);
});

for (const transport of ['IPC', 'BroadcastChannel']) {
    const options = () => ({asyncTransport:true, ...(transport === 'BroadcastChannel' ? {channelHub:new Set()} : {})});
    test(`asynchronous ${transport} close cancels pending initialization`, async t => {
        const {owner, follower} = pair(t, {...options(), deferInit:true, honorCancellation:true});
        const {promise} = await owner.start(true);
        follower.clickClose();
        await flush();
        owner.completeInit();
        assert.equal((await promise).ok, false);
        assert.equal(owner.players.length, 0);
        await owner.advance(400);
        await follower.advance(400);
        assert.equal(owner.bar(), null);
        assert.equal(follower.bar(), null);
    });

    test(`canceled beforeunload and BFCache preserve ${transport} control`, async t => {
        const {owner, follower} = pair(t, options());
        await (await owner.start()).promise;
        for (const s of [owner, follower]) {
            s.w.addEventListener('beforeunload', event => event.preventDefault());
            s.w.dispatchEvent(new s.w.Event('beforeunload', {cancelable:true}));
            s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide', {persisted:true}));
            s.w.dispatchEvent(new s.w.PageTransitionEvent('pageshow', {persisted:true}));
        }
        await flush();
        assert.ok(follower.bar());
        follower.clickClose();
        await owner.advance(400);
        await follower.advance(400);
        assert.equal(owner.players[0].destroyed, true);
        assert.equal(owner.bar(), null);
        assert.equal(follower.bar(), null);
    });
}

test('actual pagehide releases channels', async t => {
    const hub = new Set();
    const s = createSurface(t, {channelHub:hub});
    s.receive(remoteEvent('bar_state', endedState));
    assert.ok(hub.size > 0);
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide'));
    assert.equal(hub.size, 0);
});

test('BFCache pagehide retains terminal identities until actual departure', async t => {
    const s = createSurface(t);
    s.receive(remoteEvent('bar_state', endedState));
    s.clickClose();
    await s.advance(400);
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide', {persisted:true}));
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pageshow', {persisted:true}));
    s.receive(remoteEvent('bar_state', endedState));
    assert.equal(s.bar(), null);
    s.w.dispatchEvent(new s.w.PageTransitionEvent('pagehide'));
    for (const map of s.trackedMaps) assert.equal(map.size, 0);
});

test('non-full teardown releases the real keyboard shortcut before the next player', async t => {
    const {pathToFileURL} = require('node:url');
    const s = createSurface(t, {factoryGlobals:true});
    const initializeFromHarness = s.w.initializeAPlayer;
    const saved = Object.fromEntries(['window', 'document', 'Element'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    globalThis.window = s.w;
    globalThis.document = s.w.document;
    globalThis.Element = s.w.Element;
    const listeners = await import(pathToFileURL(path.join(root, 'static/js/APlayer/event_listeners.js')).href);
    const factory = await import(pathToFileURL(path.join(root, 'static/js/APlayer/main.js')).href);
    s.w.initializeAPlayer = initializeFromHarness;
    t.after(() => {
        listeners.removeKeyboardShortcuts();
        for (const [key, descriptor] of Object.entries(saved)) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
    s.w.destroyAPlayer = factory.destroyAPlayer;
    await (await s.start()).promise;
    const previous = s.players[0];
    let oldCalls = 0;
    previous.toggle = () => oldCalls++;
    listeners.setupKeyboardShortcuts(previous);
    const pressSpace = () => s.w.document.body.dispatchEvent(new s.w.KeyboardEvent('keydown', {code:'Space', bubbles:true}));
    pressSpace();
    assert.equal(oldCalls, 1);
    s.w.destroyMusicPlayer(true, false, true);
    pressSpace();
    assert.equal(oldCalls, 1, 'destroyed player must no longer receive shortcuts');
    await (await s.start()).promise;
    let newCalls = 0;
    const next = s.players.at(-1);
    next.toggle = () => newCalls++;
    listeners.setupKeyboardShortcuts(next);
    pressSpace();
    assert.equal(oldCalls, 1);
    assert.equal(newCalls, 1, 'replacement player must receive the shortcut');
});
