const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../..');
const vrmExpressionSource = fs.readFileSync(
  path.join(repoRoot, 'static/vrm/vrm-expression.js'),
  'utf8',
);

function createExpressionHarness(availableExpressions = ['neutral', 'happy', 'relaxed', 'sad', 'angry', 'surprised']) {
  const applied = Object.create(null);
  const expressionManager = {
    expressions: availableExpressions.map((name) => ({ name })),
    setValue(name, value) {
      applied[name] = value;
    },
  };
  const manager = {
    currentModel: {
      vrm: {
        expressionManager,
      },
    },
  };
  const sandbox = {
    window: {},
    console: { log() {}, warn() {}, error() {} },
    setTimeout,
    clearTimeout,
    fetch: async () => ({ ok: false, status: 404 }),
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(vrmExpressionSource, context);
  const realmObjectProto = vm.runInContext('Object.prototype', context);
  const expression = new context.window.VRMExpression(manager);
  return { expression, applied, availableExpressions, realmObjectProto };
}

test('F0499: VRMExpression rejects prototype-chain keys and invalid types without throwing', () => {
  const { expression, availableExpressions, realmObjectProto } = createExpressionHarness();

  for (const dangerousKey of ['constructor', 'toString', 'valueOf', '__proto__', 'prototype', null, 123, {}]) {
    const resolved = expression._resolveMoodWeights(dangerousKey, availableExpressions);
    assert.equal(Object.keys(resolved).length, 0);
    assert.equal(Object.getPrototypeOf(resolved), realmObjectProto);
    expression.setMood(dangerousKey);
    assert.equal(expression.currentMood, 'neutral');
    expression.update(0.016);
  }

  for (const dangerousExpr of ['constructor', 'toString', '__proto__', 'prototype', 123, {}]) {
    expression.autoChangeMood = true;
    expression.setBaseExpression(dangerousExpr);
    assert.equal(expression.currentMood, 'neutral');
    assert.equal(expression.autoChangeMood, true);
    expression.update(0.016);
  }

  // setMoodMap and loadMoodMap ignore __proto__/constructor/prototype while preserving valid keys
  const maliciousMap = Object.create(null);
  Object.defineProperty(maliciousMap, '__proto__', { value: ['pwn'], enumerable: true });
  maliciousMap.constructor = ['pwn'];
  maliciousMap.prototype = ['pwn'];
  maliciousMap.happy = ['joy'];

  expression.setMoodMap(maliciousMap);
  assert.equal(({}).pwn, undefined);
  assert.equal(Object.prototype.hasOwnProperty.call(expression.moodMap, '__proto__'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(expression.moodMap, 'constructor'), false);
  assert.deepEqual(Array.from(expression.getMoodMap().happy), ['joy']);
  assert.equal(Object.getPrototypeOf(expression.getMoodMap()), realmObjectProto);
  assert.equal(Object.getPrototypeOf(expression._resolveMoodWeights('happy', ['joy'])), realmObjectProto);

  // Valid mood and valid model expression still work
  expression.autoReturnToNeutral = false;
  expression.setMood('happy');
  assert.equal(expression.currentMood, 'happy');

  const customHarness = createExpressionHarness(['neutral', 'happy', 'custom_expr']);
  customHarness.expression.autoReturnToNeutral = false;
  customHarness.expression.setBaseExpression('custom_expr');
  assert.equal(customHarness.expression.currentMood, 'custom_expr');
  assert.equal(customHarness.expression.autoChangeMood, false);

  // When model is still loading (expressionManager unavailable), defer valid direct expressions while still blocking forbidden keys
  const loadingHarness = createExpressionHarness([]);
  loadingHarness.expression.manager.currentModel = null;
  loadingHarness.expression.autoReturnToNeutral = false;
  loadingHarness.expression.setBaseExpression('pending_model_expr');
  assert.equal(loadingHarness.expression.currentMood, 'pending_model_expr');
  assert.equal(loadingHarness.expression.autoChangeMood, false);
  loadingHarness.expression.setBaseExpression('__proto__');
  assert.equal(loadingHarness.expression.currentMood, 'pending_model_expr');
  loadingHarness.expression.setBaseExpression('toString');
  assert.equal(loadingHarness.expression.currentMood, 'pending_model_expr');
});

test('F0499: loadMoodMap and setMoodMap reject all Object.prototype shadowing keys', async () => {
  const { expression } = createExpressionHarness(['neutral', 'happy', 'sad', 'angry']);
  expression.autoReturnToNeutral = false;

  const prototypeKeys = [
    ...Object.getOwnPropertyNames(Object.prototype),
    'prototype',
  ];

  const payload = {
    toString: ['happy'],
    valueOf: 'sad',
    hasOwnProperty: 'angry',
    isPrototypeOf: 'happy',
    propertyIsEnumerable: 'sad',
    toLocaleString: 'angry',
    __defineGetter__: 'happy',
    __defineSetter__: 'sad',
    __lookupGetter__: 'angry',
    __lookupSetter__: 'happy',
    constructor: 'sad',
    prototype: 'angry',
    happy: ['happy'],
  };

  // setMoodMap must ignore all Object.prototype keys
  expression.setMoodMap(payload);
  for (const key of prototypeKeys) {
    assert.equal(Object.prototype.hasOwnProperty.call(expression.moodMap, key), false, `setMoodMap leaked ${key}`);
    assert.equal(expression.customMoodKeys.has(key), false);
  }
  assert.doesNotThrow(() => String(expression.moodMap));
  assert.equal(String(expression.moodMap), '[object Object]');
  assert.equal(expression.moodMap.hasOwnProperty('happy'), true);
  expression.setMood('toString');
  assert.equal(expression.currentMood, 'neutral');
  expression.setBaseExpression('toString');
  assert.equal(expression.currentMood, 'neutral');

  // loadMoodMap must also reject all Object.prototype keys (including JSON-parsed __proto__)
  const rawJson = JSON.stringify(payload).replace('{', '{"__proto__":["happy"],');
  const parsedPayload = JSON.parse(rawJson);
  const fetchSandbox = {
    window: {},
    console: { log() {}, warn() {}, error() {} },
    setTimeout,
    clearTimeout,
    fetch: async () => ({
      ok: true,
      status: 200,
      async json() {
        return { success: true, config: parsedPayload };
      },
    }),
  };
  const fetchCtx = vm.createContext(fetchSandbox);
  vm.runInContext(vrmExpressionSource, fetchCtx);
  const exprFromFetch = new fetchCtx.window.VRMExpression({
    currentModel: {
      vrm: {
        expressionManager: {
          expressions: [{ name: 'neutral' }, { name: 'happy' }],
          setValue() {},
        },
      },
    },
  });
  exprFromFetch.autoReturnToNeutral = false;

  await exprFromFetch.loadMoodMap('test-model');
  for (const key of prototypeKeys) {
    assert.equal(Object.prototype.hasOwnProperty.call(exprFromFetch.moodMap, key), false, `loadMoodMap leaked ${key}`);
    assert.equal(exprFromFetch.customMoodKeys.has(key), false);
  }
  assert.doesNotThrow(() => String(exprFromFetch.moodMap));
  assert.equal(String(exprFromFetch.moodMap), '[object Object]');
  exprFromFetch.setMood('toString');
  assert.equal(exprFromFetch.currentMood, 'neutral');
});
