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
