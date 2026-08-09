'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const controllerSource = fs.readFileSync(
  path.join(__dirname, 'tutorial/core/skip-controller.js'),
  'utf8',
);

function createFakeClock() {
  let now = 0;
  let nextId = 1;
  const scheduled = new Map();

  function schedule(callback, delay, interval) {
    const id = nextId++;
    const normalizedDelay = Math.max(0, Number(delay) || 0);
    scheduled.set(id, {
      callback,
      dueAt: now + normalizedDelay,
      interval: interval ? normalizedDelay : 0,
    });
    return id;
  }

  function clear(id) {
    scheduled.delete(id);
  }

  return {
    window: {
      setTimeout(callback, delay) {
        return schedule(callback, delay, false);
      },
      clearTimeout: clear,
      setInterval(callback, delay) {
        return schedule(callback, delay, true);
      },
      clearInterval: clear,
    },
    now: () => now,
    tick(durationMs) {
      const target = now + durationMs;
      while (true) {
        const next = Array.from(scheduled.entries())
          .filter(([, task]) => task.dueAt <= target)
          .sort((left, right) => left[1].dueAt - right[1].dueAt || left[0] - right[0])[0];
        if (!next) break;
        const [id, task] = next;
        now = task.dueAt;
        if (task.interval > 0) {
          task.dueAt += task.interval;
        } else {
          scheduled.delete(id);
        }
        task.callback();
      }
      now = target;
    },
  };
}

function loadControllerApi() {
  const window = {};
  vm.runInNewContext(controllerSource, { window, console, Date, Promise });
  return window.TutorialSkipController;
}

function createPointerEvent(button = 0) {
  return {
    button,
    prevented: 0,
    stopped: 0,
    immediateStopped: 0,
    preventDefault() { this.prevented += 1; },
    stopPropagation() { this.stopped += 1; },
    stopImmediatePropagation() { this.immediateStopped += 1; },
  };
}

test('tutorial skip requires one uninterrupted second and resets after early release', () => {
  const api = loadControllerApi();
  const clock = createFakeClock();
  const progress = [];
  let completed = 0;
  const hold = api.createHoldController({
    window: clock.window,
    getNow: clock.now,
    durationMs: 1000,
    onProgress(value, active) {
      progress.push({ value, active });
    },
    onComplete() {
      completed += 1;
    },
  });

  const firstPress = createPointerEvent();
  assert.equal(hold.start(firstPress), true);
  clock.tick(650);
  assert.equal(completed, 0);
  assert.ok(progress.at(-1).value >= 0.64 && progress.at(-1).value <= 0.66);
  assert.equal(hold.cancel(createPointerEvent()), true);
  assert.deepEqual(progress.at(-1), { value: 0, active: false });
  clock.tick(1000);
  assert.equal(completed, 0);

  assert.equal(hold.start(createPointerEvent()), true);
  clock.tick(999);
  assert.equal(completed, 0);
  clock.tick(1);
  assert.equal(completed, 1);
  assert.deepEqual(progress.at(-1), { value: 1, active: false });
  assert.equal(hold.start(createPointerEvent()), false);
});

test('tutorial skip ignores non-primary mouse holds', () => {
  const api = loadControllerApi();
  const clock = createFakeClock();
  let completed = 0;
  const hold = api.createHoldController({
    window: clock.window,
    getNow: clock.now,
    onComplete() {
      completed += 1;
    },
  });
  const secondaryPress = createPointerEvent(2);

  assert.equal(hold.start(secondaryPress), false);
  clock.tick(1500);
  assert.equal(completed, 0);
  assert.equal(secondaryPress.prevented, 1);
});

test('tutorial skip does not absorb unrelated global release events', () => {
  const api = loadControllerApi();
  const clock = createFakeClock();
  const hold = api.createHoldController({
    window: clock.window,
    getNow: clock.now,
  });
  const unrelatedRelease = createPointerEvent();

  assert.equal(hold.cancel(unrelatedRelease), false);
  assert.equal(unrelatedRelease.prevented, 0);
  assert.equal(unrelatedRelease.stopped, 0);
  assert.equal(unrelatedRelease.immediateStopped, 0);

  assert.equal(hold.start(createPointerEvent()), true);
  const heldRelease = createPointerEvent();
  assert.equal(hold.cancel(heldRelease), true);
  assert.equal(heldRelease.prevented, 1);
  assert.equal(heldRelease.stopped, 1);
  assert.equal(heldRelease.immediateStopped, 1);
});

test('tutorial skip button renders a dedicated circular progress indicator', () => {
  assert.match(controllerSource, /DEFAULT_SKIP_HOLD_DURATION_MS = 1000/);
  assert.match(controllerSource, /class TutorialHoldProgressController/);
  assert.match(controllerSource, /neko-tutorial-skip-progress/);
  assert.match(controllerSource, /conic-gradient\(currentColor/);
  assert.match(controllerSource, /pointerleave', cancelHold/);
  assert.match(controllerSource, /touchcancel', cancelHold/);
  assert.match(controllerSource, /addListener\(button, 'click', absorbClick\)/);
  assert.doesNotMatch(controllerSource, /addListener\(button, 'click', completeSkipRequest\)/);
});
