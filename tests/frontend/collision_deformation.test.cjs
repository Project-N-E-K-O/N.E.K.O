const test = require('node:test');
const assert = require('node:assert/strict');

const {
  computeCollisionDeformation,
  beginCollisionDeformation,
  renderCollisionDeformation,
  clearCollisionDeformation,
} = require(
  '../../static/avatar/avatar-ui-buttons/collision-deformation.js'
);

function buttonFixture() {
  const classes = new Set();
  const values = new Map();
  return {
    classList: {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
    },
    style: {
      setProperty: (name, value) => values.set(name, String(value)),
      removeProperty: name => values.delete(name),
      getPropertyValue: name => values.get(name) || '',
    },
  };
}

const sample = (overrides = {}) => computeCollisionDeformation({
  direction: 'down',
  impactVelocityChange: 600,
  elapsedMs: 0,
  ...overrides,
});

test('vertical collisions compress vertically around the contact side', () => {
  const up = sample({ direction: 'up' });
  const down = sample({ direction: 'down' });

  assert.equal(up.active, true);
  assert.ok(up.scaleX > 1);
  assert.ok(up.scaleY < 1);
  assert.deepEqual([up.originX, up.originY], [50, 0]);
  assert.deepEqual([down.originX, down.originY], [50, 100]);
});

test('horizontal collisions compress horizontally around the contact side', () => {
  const left = sample({ direction: 'left' });
  const right = sample({ direction: 'right' });

  assert.ok(left.scaleX < 1);
  assert.ok(left.scaleY > 1);
  assert.deepEqual([left.originX, left.originY], [0, 50]);
  assert.deepEqual([right.originX, right.originY], [100, 50]);
});

test('visible GIF hull anchors deformation on the painted contact edge', () => {
  const hull = { left: 0.18, top: 0.10, right: 0.22, bottom: 0.04 };
  const down = sample({ direction: 'down', visibleInsetRatios: hull });
  const left = sample({ direction: 'left', visibleInsetRatios: hull });

  assert.deepEqual([down.originX, down.originY], [48, 96]);
  assert.deepEqual([left.originX, left.originY], [18, 53]);
});

test('equal-speed impacts use the same amplitude on either axis', () => {
  const vertical = sample({ direction: 'down', impactVelocityChange: 800 });
  const horizontal = sample({ direction: 'left', impactVelocityChange: 800 });

  assert.ok(Math.abs((1 - vertical.scaleY) - (1 - horizontal.scaleX)) < 1e-12);
  assert.ok(Math.abs((vertical.scaleX - 1) - (horizontal.scaleY - 1)) < 1e-12);
});

test('every faster finite impact produces stronger deformation toward a safe limit', () => {
  const slow = sample({ impactVelocityChange: 100 });
  const medium = sample({ impactVelocityChange: 700 });
  const fast = sample({ impactVelocityChange: 1700 });
  const faster = sample({ impactVelocityChange: 1800 });
  const extreme = sample({ impactVelocityChange: 5000 });

  assert.ok(medium.scaleY < slow.scaleY);
  assert.ok(fast.scaleY < medium.scaleY);
  assert.ok(faster.scaleY < fast.scaleY);
  assert.ok(extreme.scaleY < faster.scaleY);
  assert.ok(extreme.scaleX < 1.38);
  assert.ok(extreme.scaleY > 0.62);
});

test('deformation holds briefly, then recovers within a short fixed duration', () => {
  const initial = sample({ elapsedMs: 0 });
  const held = sample({ elapsedMs: 70 });
  const recovering = sample({ elapsedMs: 120 });
  const almostDone = sample({ elapsedMs: 159 });
  const done = sample({ elapsedMs: 160 });

  assert.deepEqual(held, initial);
  assert.ok(recovering.scaleY > held.scaleY && recovering.scaleY < 1);
  assert.equal(almostDone.active, true);
  assert.deepEqual(done, { active: false, scaleX: 1, scaleY: 1, originX: 50, originY: 50 });
});

test('resting, invalid, expired, and reduced-motion samples stay unchanged', () => {
  const identity = { active: false, scaleX: 1, scaleY: 1, originX: 50, originY: 50 };

  assert.deepEqual(sample({ impactVelocityChange: 38 }), identity);
  assert.deepEqual(sample({ direction: 'diagonal' }), identity);
  assert.deepEqual(sample({ impactVelocityChange: Number.NaN }), identity);
  assert.deepEqual(sample({ elapsedMs: -1 }), identity);
  assert.deepEqual(sample({ reducedMotion: true }), identity);
});

test('results are immutable so renderers cannot corrupt later samples', () => {
  assert.equal(Object.isFrozen(sample()), true);
  assert.equal(Object.isFrozen(sample({ reducedMotion: true })), true);
});

test('component integration survives art changes and clears itself after recovery', () => {
  const button = buttonFixture();
  assert.equal(beginCollisionDeformation(button, {
    direction: 'down', impactVelocityChange: 600, startedAt: 1000,
  }), true);
  assert.equal(button.classList.contains('is-neko-collision-deforming'), true);
  assert.equal(button.style.getPropertyValue('--neko-collision-origin-y'), '100%');
  const peak = Number(button.style.getPropertyValue('--neko-collision-scale-y'));

  assert.equal(renderCollisionDeformation(button, 1100), true);
  assert.ok(Number(button.style.getPropertyValue('--neko-collision-scale-y')) > peak);
  assert.equal(renderCollisionDeformation(button, 1160), false);
  assert.equal(button.classList.contains('is-neko-collision-deforming'), false);
  assert.equal(button.style.getPropertyValue('--neko-collision-scale-y'), '');
});

test('a new impact restarts from its own direction and reduced motion clears it', () => {
  const button = buttonFixture();
  beginCollisionDeformation(button, { direction: 'down', impactVelocityChange: 600, startedAt: 1000 });
  beginCollisionDeformation(button, { direction: 'left', impactVelocityChange: 900, startedAt: 1040 });
  assert.equal(button.style.getPropertyValue('--neko-collision-origin-x'), '0%');
  assert.ok(Number(button.style.getPropertyValue('--neko-collision-scale-x')) < 1);

  assert.equal(renderCollisionDeformation(button, 1050, true), false);
  assert.equal(button.classList.contains('is-neko-collision-deforming'), false);
  assert.equal(clearCollisionDeformation(button), false);
});
