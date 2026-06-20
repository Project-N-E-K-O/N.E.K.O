const assert = require('node:assert/strict');
const test = require('node:test');

const { OperationRegistry } = require('./tutorial/core/operation-registry.js');

test('OperationRegistry exposes configurable exact, prefix and predicate operation handlers', async () => {
    const calls = [];
    const registry = new OperationRegistry({
        waitForSceneDelay() {
            calls.push('wait');
            return Promise.resolve();
        }
    });

    registry.registerOperation('exact-op', function (context) {
        calls.push(['exact', context.operation, context.scene.id]);
        return 'exact-result';
    });
    registry.registerOperation({ prefix: 'prefix-op:' }, function (context) {
        calls.push(['prefix', context.operation]);
        return 'prefix-result';
    });
    registry.registerOperation((context) => context.scene && context.scene.usePredicate === true, function (context) {
        calls.push(['predicate', context.operation]);
        return 'predicate-result';
    });

    assert.equal(await registry.run({ id: 'scene-a', operation: 'exact-op' }, null, 10), 'exact-result');
    assert.equal(await registry.run({ id: 'scene-b', operation: 'prefix-op:item' }, null, 10), 'prefix-result');
    assert.equal(await registry.run({ id: 'scene-c', operation: 'unknown', usePredicate: true }, null, 10), 'predicate-result');
    assert.deepEqual(calls, [
        ['exact', 'exact-op', 'scene-a'],
        ['prefix', 'prefix-op:item'],
        ['predicate', 'unknown']
    ]);
});

test('OperationRegistry built-ins are registered declaratively', async () => {
    const registry = new OperationRegistry({
        openSettingsPanel() {
            return 'settings-opened';
        },
        waitForSceneDelay() {
            return Promise.resolve();
        },
        tourMiniGameChoiceButtons() {
            return Promise.resolve();
        }
    });

    assert.ok(Array.isArray(registry.operationHandlers));
    assert.ok(registry.operationHandlers.length > 10);
    assert.equal(await registry.run({ operation: 'day2-open-settings-personalization' }), 'settings-opened');
    assert.equal(await registry.run({ operation: 'cleanup' }), true);
    assert.equal(await registry.run({ operation: 'day1-managed-scene-settled:done' }), true);
    assert.equal(await registry.run({ id: 'day3_galgame_games' }), true);
});
