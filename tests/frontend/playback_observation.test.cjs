const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

// Exercise the actual neutral factory without loading the unrelated music UI.
const source = fs.readFileSync(path.join(__dirname, '../../static/jukebox/music_ui.js'), 'utf8');
const start = source.indexOf('    function createObservedPlaybackMeter(');
const end = source.indexOf('    function observeRecommendationPlayback(', start);
const create = vm.runInNewContext('(' + source.slice(start, end).trim() + ')');
const audio = () => ({ currentTime: 0, playbackRate: 1, paused: false, ended: false, readyState: 4, seeking: false });
const origin = () => ({ owner_key: 'original-owner', capture: { tick_ms: 0 } });

test('quantized media progress is accumulated by prefix, not clipped on every sample', () => {
    const a = audio(), m = create('epoch', origin());
    m.observe(a, 0, 'playing');
    for (let i = 1; i <= 100; i++) { a.currentTime = Math.floor(i / 2) * 0.2; m.observe(a, i * 100, 'progress'); }
    const range = m.take();
    assert.equal(range.range_start_ms, 0);
    assert.equal(range.range_end_ms, 10000);
    assert.equal(m.take(), null);
});

test('normal pause excludes paused clock time while preserving the same audio phase', () => {
    const a = audio(), m = create('epoch', origin());
    m.observe(a, 0, 'playing');
    for (let i = 1; i <= 10; i++) { a.currentTime = i / 10; m.observe(a, i * 100, 'progress'); }
    a.paused = true; m.observe(a, 1000, 'paused');
    a.paused = false; m.observe(a, 6000, 'playing');
    for (let i = 1; i <= 10; i++) { a.currentTime = 1 + i / 10; m.observe(a, 6000 + i * 100, 'progress'); }
    assert.equal(m.take().range_end_ms, 2000);
});

test('seek and repeat adds a second elapsed interval without counting the seek jump', () => {
    const a = audio(), m = create('epoch', origin());
    m.observe(a, 0, 'playing');
    for (let i = 1; i <= 10; i++) { a.currentTime = i / 10; m.observe(a, i * 100, 'progress'); }
    a.paused = true; m.observe(a, 1000, 'paused');
    a.currentTime = 0; m.observe(a, 1000, 'seeking'); m.observe(a, 1001, 'seeked');
    a.paused = false; m.observe(a, 1100, 'playing');
    for (let i = 1; i <= 10; i++) { a.currentTime = i / 10; m.observe(a, 1100 + i * 100, 'progress'); }
    assert.equal(m.take().range_end_ms, 2000);
});

test('double speed counts elapsed playback rather than the number of media seconds', () => {
    const a = audio(), m = create('epoch', origin()); a.playbackRate = 2;
    m.observe(a, 0, 'playing');
    for (let i = 1; i <= 10; i++) { a.currentTime = i / 5; m.observe(a, i * 100, 'progress'); }
    assert.equal(m.take().range_end_ms, 1000);
});

test('a discontinuous clock gap cannot become a large invented duration', () => {
    const a = audio(), m = create('epoch', origin());
    m.observe(a, 0, 'playing'); a.currentTime = 1; m.observe(a, 1000, 'progress');
    a.currentTime = 11; m.observe(a, 11000, 'progress');
    a.currentTime = 11.1; m.observe(a, 11100, 'progress');
    a.currentTime = 11.2; m.observe(a, 11200, 'progress');
    assert.ok(m.take().range_end_ms <= 1100);
});

test('checkpoint ranges are integer, disjoint prefixes and freeze their original context', () => {
    const a = audio(), original = origin(), m = create('epoch', original);
    m.observe(a, 0, 'playing');
    for (let i = 1; i <= 151; i++) { a.currentTime = i / 10; m.observe(a, i * 100, 'progress'); }
    const first = m.take(15000); original.owner_key = 'replacement';
    for (let i = 152; i <= 161; i++) { a.currentTime = i / 10; m.observe(a, i * 100, 'progress'); }
    const next = m.take();
    assert.equal(first.origin_context.owner_key, 'original-owner');
    assert.equal(next.range_start_ms, first.range_end_ms);
    assert.ok(Number.isSafeInteger(next.range_end_ms));
    assert.equal(first.epoch_id, next.epoch_id);
});
