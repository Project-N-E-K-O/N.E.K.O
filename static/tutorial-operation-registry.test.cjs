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
    assert.equal(await registry.run({ operation: 'day3-open-settings-personalization' }), 'settings-opened');
    assert.equal(await registry.run({ operation: 'cleanup' }), true);
    assert.equal(await registry.run({ operation: 'day1-managed-scene-settled:done' }), true);
    assert.equal(await registry.run({ id: 'day2_galgame_games' }), true);
});

test('OperationRegistry routes daily intro greeting to generic performance only', async () => {
    const calls = [];
    const registry = new OperationRegistry({
        runDailyIntroGreetingPerformance(scene) {
            calls.push(scene.id);
            return Promise.resolve('daily-intro-complete');
        },
        runIntroGiftHeartPerformance() {
            calls.push('gift-heart');
            return Promise.resolve();
        },
        waitForSceneDelay() {
            return Promise.resolve();
        }
    });

    const result = await registry.run({
        id: 'day2_intro_context',
        operation: 'daily-intro-greeting-performance'
    });

    assert.equal(result, 'daily-intro-complete');
    assert.deepEqual(calls, ['day2_intro_context']);
});

test('OperationRegistry routes daily intro avatar motion presets through the director', async () => {
    const calls = [];
    const revealPrepared = () => 'revealed';
    const registry = new OperationRegistry({
        runDailyIntroAvatarPerformance(scene, day, options) {
            calls.push([
                scene.id,
                scene.introAvatarPerformance && scene.introAvatarPerformance.preset,
                options && options.revealPrepared
            ]);
            return Promise.resolve('avatar-motion-complete');
        },
        waitForSceneDelay() {
            return Promise.resolve();
        }
    });

    const result = await registry.run({
        id: 'day5_character_settings',
        operation: 'daily-intro-avatar-performance',
        introAvatarPerformance: {
            preset: 'top-peek'
        }
    }, null, 0, null, { revealPrepared });

    assert.equal(result, 'avatar-motion-complete');
    assert.deepEqual(calls, [['day5_character_settings', 'top-peek', revealPrepared]]);
});
