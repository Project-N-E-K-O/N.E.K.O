const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

// Reuse the established controller surface while keeping this independent
// regression out of its implementation author's assertions.
const harnessPath = path.join(__dirname, 'recommendation_decision_recovery.test.cjs');
const source = fs.readFileSync(harnessPath, 'utf8');
const { surface, settle } = new Function('require', '__dirname',
    source.slice(0, source.indexOf('\ntest(')) + '\nreturn { surface, settle };'
)(require, __dirname);

for (const protocol of [1, 2]) test(`expired v${protocol} other-account decisions cannot permanently block current-account rejection`, async t => {
    const prefix = 'neko.recommendation-decision.v1.';
    const storage = Array.from({ length: 256 }, (_, i) => [prefix + i, JSON.stringify({
        id: 'old-' + i, decision: true, character: 'Previous', expiresAt: 190000,
        event: { event_id: 'old-' + i, kind: 'skipped', occurred_at: 1,
            ...(protocol === 2 ? { context: { owner_key: 'a'.repeat(64) } } : {}),
        },
    })]);
    const s = surface(t, { storage, character: 'Current', respond: async (event, id) =>
        id.startsWith('old-') ? { success: false, error_code: 'recommendation_not_found' }
            : { success: true, state: event.kind },
    });
    for (let i = 0; i < 100 && new Set(s.events.map(item => item.id)).size < 256; i++) await settle();
    assert.equal(new Set(s.events.map(item => item.id)).size, 256,
        'the initial old-owner pass reached every original event');
    await settle();
    assert.equal(s.storage().length, 256, 'unexpired choices may wait for their original owner');
    await s.advance(1000000);
    s.offer('current');
    s.choose('skipped', 'current');
    await settle();
    assert.equal(s.events.filter(item => item.id === 'current').length, 1,
        'expired unrecoverable decisions must free capacity for the current explicit choice');
    assert.equal(s.message('current').blocks[0].state, 'skipped');
});

test('expired choices are removed while playback evidence and unexpired other-owner choices survive', async t => {
    const prefix = 'neko.recommendation-decision.v1.';
    const evidencePrefix = 'neko.music-recommendation-event.v1.';
    const s = surface(t, { character: 'Current', storage: [
        [prefix + 'expired', JSON.stringify({ id: 'expired', decision: true, character: 'Previous', expiresAt: 90000,
            event: { event_id: 'expired', kind: 'skipped', occurred_at: 1 } })],
        [prefix + 'valid', JSON.stringify({ id: 'valid', decision: true, character: 'Previous', expiresAt: 190000,
            event: { event_id: 'valid', kind: 'skipped', occurred_at: 1 } })],
        [evidencePrefix + 'played', JSON.stringify({ id: 'played', expiresAt: 90000,
            event: { event_id: 'played', kind: 'ended', occurred_at: 1, playback_id: 'playback' } })],
    ], respond: async () => ({ success: false, error_code: 'recommendation_not_found' }) });
    await settle();
    const retained = s.storage().map(([, raw]) => JSON.parse(raw).event.event_id).sort();
    assert.deepEqual(retained, ['played', 'valid']);
    assert.equal(s.events.some(item => item.id === 'expired'), false);
});

test('a queued decision expiring during an owner-mismatch request is retired after that response', async t => {
    let release;
    const s = surface(t, { storage: [['neko.recommendation-decision.v1.late', JSON.stringify({ id: 'late',
        decision: true, character: 'Previous', expiresAt: 101000,
        event: { event_id: 'late', kind: 'skipped', occurred_at: 1 } })],
        ['neko.recommendation-decision.v1.next', JSON.stringify({ id: 'next', decision: true,
            character: 'Previous', expiresAt: 101000,
            event: { event_id: 'next', kind: 'skipped', occurred_at: 2 } })]],
        respond: async () => { await new Promise(resolve => { release = resolve; });
            return { success: false, error_code: 'recommendation_not_found' }; },
    });
    await settle();
    assert.equal(typeof release, 'function');
    await s.advance(1000);
    release();
    await settle();
    assert.equal(s.storage().length, 0);
    assert.equal(s.events.some(item => item.id === 'next'), false);
});

test('acceptance acknowledged in the background expires on the original still-open page', async t => {
    let online = false;
    let accepted = false;
    let s;
    s = surface(t, { respond: async event => {
        if (!online) throw Error('offline before server receipt');
        // Match v1's real first-decision expiry and idempotent-result contract.
        if (!accepted && s.w.Date.now() >= 1900000) {
            return { success: false, error_code: 'recommendation_expired' };
        }
        accepted = true;
        return { success: true, state: 'accepted', playback_grant: event.kind === 'accepted' };
    } });
    s.offer();
    s.choose('accepted');
    await settle();
    assert.equal(accepted, false);
    online = true;
    s.w.dispatchEvent(new s.w.Event('online'));
    for (let i = 0; i < 20 && !accepted; i++) await settle();
    await settle();
    assert.equal(accepted, true, 'background retry commits the original acceptance while valid');
    assert.equal(s.plays.length, 0);
    assert.equal(s.message().blocks[0].state, 'pending');
    await s.advance(1801000);
    s.choose('accepted');
    await settle();
    assert.equal(s.plays.length, 0, 'a fresh click after offer expiry cannot dispatch recovered consent');
    assert.equal(s.message().blocks[0].state, 'unavailable');
});
