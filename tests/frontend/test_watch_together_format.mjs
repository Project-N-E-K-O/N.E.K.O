import assert from 'node:assert/strict';
import {clock, when} from '../../static/game/games/watch-together/format.mjs';

assert.equal(clock(0), '0:00');
assert.equal(clock(9.9), '0:09');
assert.equal(clock(122), '2:02');
assert.equal(clock(3725), '1:02:05');
for (const value of [NaN, -1, null, undefined, '', Infinity]) assert.equal(clock(value), '', String(value));
assert.equal(when('now'), 'now', 'unparseable values are shown as given');
assert.equal(when(null), '');
assert.notEqual(when('2026-09-14T16:01:14+00:00', 'en'), '');
console.log('watch-together format: clock and timestamp helpers passed');
