const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../static/avatar/avatar-music-listening.js'), 'utf8');

function harness() {
    let now = 10000, audible = true, held = null;
    const listeners = {}, calls = [];
    const manager = {
        currentModel: {}, fileReferences: { Expressions: [{ File: 'expressions/listening.exp3.json' }] },
        _transientExpressionGeneration: 0,
        hasActiveActionMotion() { return this.motion || false; },
        hasActiveTransientExpression() { return this.expression || false; },
        _allowsAvatarOperation() { return !this.locked; },
        async playExpression(_, file, options) {
            calls.push('listen');
            this._transientExpressionGeneration++;
            if (held) await held;
            if (!options.isCurrent()) return false;
            options.onApplied();
            return true;
        },
        async smoothResetToInitialState() { calls.push('fade'); this._transientExpressionGeneration++; },
        async clearExpression() { calls.push('clear'); this._transientExpressionGeneration++; },
        async setEmotion(emotion) {
            if (this.motion) return false;
            calls.push(emotion); this._transientExpressionGeneration++;
        }
    };
    const window = {
        live2dManager: manager, lanlan_config: { model_type: 'live2d' },
        isMusicAudible: () => audible,
        addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
        setInterval() { return 1; }, clearInterval() {}
    };
    vm.runInNewContext(code, { window, AbortController, Date: { now: () => now }, console });
    const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
    return {
        manager, window, calls, flush,
        tick: () => window.nekoMusicListening.tick(),
        advance(ms) { now += ms; },
        music(value) { audible = value; },
        hold(value) { held = value; },
        chat: emotion => window.nekoMusicListening.applyChatEmotion(emotion),
        emit(type, turnId = 'turn1') { for (const fn of listeners[type] || []) fn({ detail: { turnId } }); }
    };
}

test('music starts once, pauses gracefully, resumes, and ends', async () => {
    const h = harness();
    await h.tick(); await h.tick();
    assert.deepEqual(h.calls, ['listen']);
    h.music(false); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'fade']);
    h.music(true); await h.tick();
    h.music(false); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'fade', 'listen', 'fade']);
});

test('diagnostics do not present a faded historical chat selection as an active expression', () => {
    const h = harness();
    h.manager.currentExpressionFile = 'expressions/chat-sad.exp3.json';
    h.manager.expression = false;
    assert.equal(h.window.nekoMusicListening.snapshot().expression, null);
    h.manager.expression = true;
    assert.equal(h.window.nekoMusicListening.snapshot().expression, 'expressions/chat-sad.exp3.json');
});

test('chat wins, speech holds the expression, listening resumes after the hold', async () => {
    const h = harness(); await h.tick();
    h.emit('neko-assistant-turn-start'); await h.flush();
    h.chat('happy'); await h.flush();
    h.emit('neko-assistant-speech-start');
    h.emit('neko-assistant-turn-end');
    h.advance(10000); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'fade', 'happy']);
    h.emit('neko-assistant-speech-end'); await h.flush();
    h.advance(3501); await h.tick();
    assert.equal(h.calls.at(-1), 'listen');
});

test('sad and angry replies keep their mood for 15 seconds', async () => {
    const h = harness(); await h.tick();
    h.chat('sad'); await h.flush(); h.advance(9000); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'fade', 'sad']);
    h.advance(6001); await h.tick(); assert.equal(h.calls.at(-1), 'listen');
});

test('music ending during a chat does not clear the chat emotion', async () => {
    const h = harness(); await h.tick(); h.chat('angry'); await h.flush();
    h.music(false); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'fade', 'angry']);
});

test('latest chat emotion waits for an existing action instead of being dropped', async () => {
    const h = harness(); h.manager.motion = true;
    h.chat('sad'); await h.flush(); h.chat('surprised'); await h.flush();
    assert.deepEqual(h.calls, []);
    h.manager.motion = false; await h.tick();
    assert.deepEqual(h.calls, ['surprised']);
});

test('a loading expression cannot resurrect after pause or chat', async () => {
    const h = harness(); let resolve;
    h.hold(new Promise(r => { resolve = r; }));
    const task = h.tick(); h.music(false); h.chat('happy'); resolve();
    await task; await h.tick();
    assert.deepEqual(h.calls, ['listen', 'happy']);
});

test('model changes and another expression owner are never reset by music', async () => {
    const h = harness(); await h.tick();
    h.manager._transientExpressionGeneration++; h.music(false); await h.tick();
    assert.deepEqual(h.calls, ['listen']);
    h.music(true); h.advance(8001); await h.tick();
    h.manager.currentModel = {}; h.music(false); await h.tick();
    assert.deepEqual(h.calls, ['listen', 'listen']);
});

test('unconfigured models and locked avatar sessions do not enter listening', async () => {
    const h = harness(); h.manager.locked = true; await h.tick();
    h.manager.locked = false; h.manager.fileReferences.Expressions = []; await h.tick();
    assert.equal(h.chat('happy'), false);
    assert.deepEqual(h.calls, []);
});

test('late speech-end from an old turn does not release current speech', async () => {
    const h = harness(); h.emit('neko-assistant-speech-start', 'new');
    h.emit('neko-assistant-speech-end', 'old'); h.advance(30000); await h.tick();
    assert.deepEqual(h.calls, []);
});

test('returning from chat fades its expression before entering enjoyment', async () => {
    const h = harness();
    h.manager.expression = true;
    const durations = [];
    h.manager.smoothResetToInitialState = async ms => {
        durations.push(ms); h.calls.push('fade');
        h.manager._transientExpressionGeneration++; h.manager.expression = false;
    };
    await h.tick();
    assert.deepEqual(h.calls, ['fade', 'listen']);
    assert.deepEqual(durations, [550]);
});

test('chat arriving during the return fade cancels the ambient handoff', async () => {
    const h = harness(); let finishFade;
    h.manager.expression = true;
    h.manager.smoothResetToInitialState = () => {
        h.calls.push('fade'); h.manager._transientExpressionGeneration++;
        return new Promise(resolve => { finishFade = resolve; });
    };
    const transition = h.tick();
    h.chat('surprised'); finishFade(); await transition; await h.tick();
    assert.deepEqual(h.calls, ['fade', 'surprised']);
});

// Controllable completion order, with observable committed pose rather than
// assertions only about which API was called. No real-time sleeps are needed.
function transitionHarness() {
    let now = 10000, audible = true, pose = 'neutral';
    const events = {}, commits = [], loads = [], fades = [], chats = [], appliedLoads = [];
    const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
    const manager = {
        currentModel: {}, fileReferences: { Expressions: [{ File: 'expressions/listening.exp3.json' }] },
        _transientExpressionGeneration: 0, expression: false,
        hasActiveActionMotion: () => false,
        hasActiveTransientExpression() { return this.expression; },
        _allowsAvatarOperation: () => true,
        async playExpression(_, file, options) {
            const gate = defer(), generation = ++this._transientExpressionGeneration;
            loads.push({ ...gate, options }); await gate.promise;
            if (options.signal.aborted || !options.isCurrent() || generation !== this._transientExpressionGeneration) return false;
            pose = 'listening'; this.expression = true; commits.push(pose); options.onApplied();
            if (this.holdAfterApply) {
                const afterApply = defer(); appliedLoads.push(afterApply); await afterApply.promise;
                if (options.signal.aborted) throw Object.assign(new Error('cancelled after install'), { name: 'AbortError' });
            }
            return true;
        },
        async smoothResetToInitialState(duration) {
            const gate = defer(), generation = ++this._transientExpressionGeneration;
            fades.push({ ...gate, duration }); await gate.promise;
            if (generation !== this._transientExpressionGeneration) return false;
            pose = 'neutral'; this.expression = false; commits.push(pose); return true;
        },
        async setEmotion(emotion, options = {}) {
            const gate = defer(), generation = ++this._transientExpressionGeneration;
            chats.push({ ...gate, emotion, options }); await gate.promise;
            if (options.signal?.aborted || (options.isCurrent && !options.isCurrent())
                || generation !== this._transientExpressionGeneration) return false;
            pose = emotion; this.expression = true; commits.push(pose); return true;
        },
        async clearExpression() { ++this._transientExpressionGeneration; pose = 'neutral'; this.expression = false; }
    };
    const window = {
        live2dManager: manager, lanlan_config: { model_type: 'live2d' }, isMusicAudible: () => audible,
        addEventListener(type, fn) { (events[type] ||= []).push(fn); }, setInterval() {}, clearInterval() {}
    };
    vm.runInNewContext(code, { window, AbortController, Date: { now: () => now }, console });
    const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
    const tick = () => window.nekoMusicListening.tick();
    return {
        manager, commits, loads, fades, chats, appliedLoads, flush, tick,
        pose: () => pose, advance(ms) { now += ms; },
        async music(value) { audible = value; void tick(); await flush(); },
        async chat(emotion) { window.nekoMusicListening.applyChatEmotion(emotion); await flush(); },
        async emit(type, id = 't1') { for (const f of events[type] || []) f({ detail: { turnId: id } }); await flush(); },
        async complete(gate) { assert.ok(gate, 'expected a pending operation'); gate.resolve(); await flush(); },
        async startListening() { void tick(); await flush(); await this.complete(loads.at(-1)); assert.equal(pose, 'listening'); },
        async settleChat(emotion = 'happy') { await this.chat(emotion); await this.complete(chats.at(-1)); assert.equal(pose, emotion); }
    };
}

test('deferred return fade cannot commit listening over a newer chat expression', async () => {
    const h = transitionHarness(); await h.settleChat('happy'); h.advance(8001);
    const returning = h.tick(); await h.flush(); const oldFade = h.fades.at(-1);
    await h.chat('surprised'); await h.complete(oldFade); await returning;
    assert.equal(h.loads.length, 0);
    void h.tick(); await h.flush(); await h.complete(h.chats.at(-1));
    await h.complete(oldFade); // a retired completion cannot replay the handoff
    assert.equal(h.pose(), 'surprised');
    assert.equal(h.commits.includes('listening'), false);
});

for (const stop of ['pause', 'ended']) {
    for (const phase of ['entry-load', 'return-fade', 'exit-fade']) {
        test(`${stop} during ${phase} leaves no listening pose or late revival`, async () => {
            const h = transitionHarness(); let transition, gate;
            if (phase === 'exit-fade') {
                await h.startListening(); await h.music(false); gate = h.fades.at(-1);
            } else if (phase === 'return-fade') {
                await h.settleChat(); h.advance(8001); transition = h.tick(); await h.flush(); gate = h.fades.at(-1);
            } else {
                transition = h.tick(); await h.flush(); gate = h.loads.at(-1);
            }
            await h.music(false); const cut = h.commits.length;
            await h.complete(gate); if (transition) await transition;
            h.advance(60000); await h.tick(); await h.tick();
            assert.notEqual(h.pose(), 'listening');
            assert.equal(h.commits.slice(cut).includes('listening'), false);
            assert.equal(h.loads.length, phase === 'return-fade' ? 0 : 1);
        });
    }
}

for (const playing of [true, false]) {
    test(`chat and speech finish: ${playing ? 'resume with music' : 'stay out when music stopped'}`, async () => {
        const h = transitionHarness();
        await h.emit('neko-assistant-turn-start'); await h.settleChat();
        await h.emit('neko-assistant-speech-start'); await h.emit('neko-assistant-turn-end');
        await h.music(playing); h.advance(20000); await h.tick();
        assert.equal(h.loads.length, 0, 'speech retains priority');
        await h.emit('neko-assistant-speech-end'); h.advance(3501);
        const returning = h.tick(); await h.flush();
        if (playing) {
            await h.complete(h.fades.at(-1)); await h.complete(h.loads.at(-1));
            await returning; assert.equal(h.pose(), 'listening');
        } else {
            await returning; assert.equal(h.pose(), 'happy'); assert.equal(h.loads.length, 0);
        }
    });
}

test('superseded delayed chat cannot commit after the latest chat has arrived', async () => {
    const h = transitionHarness(); await h.chat('sad'); const old = h.chats[0];
    await h.chat('happy'); await h.chat('angry');
    await h.music(false); await h.music(true); await h.music(false);
    const cut = h.commits.length;
    await h.complete(old); void h.tick(); await h.flush(); await h.complete(h.chats.at(-1));
    assert.deepEqual(h.commits.slice(cut), ['angry']);
    h.advance(60000); await h.tick(); assert.equal(h.pose(), 'angry');
});

test('a new turn retires the previous pending emotion before its result resolves', async () => {
    const h = transitionHarness(); await h.chat('sad'); const old = h.chats[0];
    await h.emit('neko-assistant-turn-start', 'new'); await h.complete(old);
    assert.equal(h.commits.includes('sad'), false);
    await h.chat('surprised'); await h.complete(h.chats.at(-1));
    assert.equal(h.pose(), 'surprised');
});

for (const finalPlaying of [false, true]) {
    test(`rapid chats and music toggles converge to ${finalPlaying ? 'listening' : 'latest chat'}`, async () => {
        const h = transitionHarness(); await h.startListening();
        await h.chat('sad'); const outgoing = h.fades.at(-1);
        await h.music(false); await h.chat('happy'); await h.music(true);
        await h.chat('surprised'); await h.music(false); await h.music(finalPlaying);
        await h.complete(outgoing); await h.complete(h.chats.at(-1));
        assert.equal(h.pose(), 'surprised');
        h.advance(9000); const transition = h.tick(); await h.flush();
        if (finalPlaying) {
            await h.complete(h.fades.at(-1)); await h.complete(h.loads.at(-1));
        }
        await transition; await h.tick();
        assert.equal(h.pose(), finalPlaying ? 'listening' : 'surprised');
        assert.equal(h.commits.includes('sad'), false);
        assert.equal(h.commits.includes('happy'), false);
    });
}

for (const stop of ['pause', 'ended']) {
    test(`${stop} after listening is installed but entry is unresolved cleans up the owned pose`, async () => {
        const h = transitionHarness(); h.manager.holdAfterApply = true;
        const entry = h.tick(); await h.flush(); await h.complete(h.loads[0]);
        assert.equal(h.pose(), 'listening');
        await h.music(false); const cut = h.commits.length;
        await h.complete(h.appliedLoads[0]); await entry;
        const exit = h.tick(); await h.flush(); await h.complete(h.fades.at(-1)); await exit;
        h.advance(60000); await h.tick();
        assert.equal(h.pose(), 'neutral');
        assert.equal(h.manager.hasActiveTransientExpression(), false);
        assert.equal(h.commits.slice(cut).includes('listening'), false);
        assert.equal(h.loads.length, 1);
    });
}

test('song mood maps only registered expressions and chat holds priority', async () => {
    const h = harness(); let category = 'sad';
    h.window.nekoMusicMood = { category: () => category, snapshot: () => ({result:null}) };
    h.manager.fileReferences.Expressions.push({File:'expressions/listening-sad.exp3.json'});
    await h.tick();
    assert.equal(h.window.nekoMusicListening.snapshot().expression, 'expressions/listening-sad.exp3.json');
    h.emit('neko-assistant-turn-start'); await h.flush(); h.chat('sad'); await h.flush();
    category = 'joyful'; h.emit('neko-music-mood-change'); await h.flush();
    h.advance(20000); await h.tick();
    assert.equal(h.calls.at(-1), 'sad');
    assert.equal(h.window.nekoMusicListening.snapshot().reason, 'chat_turn');
    h.emit('neko-assistant-turn-end'); h.advance(3501); await h.tick();
    assert.equal(h.window.nekoMusicListening.snapshot().expression, 'expressions/listening.exp3.json');
});
