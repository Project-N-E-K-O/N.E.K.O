const assert = require('node:assert/strict');
const test = require('node:test');

const { CommandRegistry } = require('./tutorial/core/command-registry.js');
const { TimelineEngine } = require('./tutorial/core/timeline-engine.js');

test('CommandRegistry dispatches registered timeline commands with shared context', async () => {
    const calls = [];
    const registry = new CommandRegistry();
    assert.equal(registry.register('cursor.move', (event, context) => {
        calls.push([event.command, event.target, context.scene.id, context.runToken.sceneId]);
        return 'moved';
    }), true);

    const result = await registry.dispatch({
        command: 'cursor.move',
        target: 'chat-capsule-input'
    }, {
        scene: { id: 'scene-a' },
        runToken: { sceneId: 'scene-a' }
    });

    assert.equal(result, 'moved');
    assert.deepEqual(calls, [
        ['cursor.move', 'chat-capsule-input', 'scene-a', 'scene-a']
    ]);
    assert.deepEqual(registry.getRegisteredCommands(), ['cursor.move']);
});

test('TimelineEngine triggers commands by timeline time and waits for blocking commands', async () => {
    let currentTime = 0;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'spotlight.show': (event) => {
                calls.push([event.command, currentTime]);
            },
            'cursor.move': (event) => {
                calls.push([event.command, currentTime]);
            },
            'operation.run': async (event) => {
                calls.push([event.command, currentTime, 'start']);
                await Promise.resolve();
                calls.push([event.command, currentTime, 'end']);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const result = await engine.playScene({
        id: 'scene-a',
        audio: { voiceKey: 'voice-a', minDurationMs: 1000 },
        timeline: [
            { id: 'spotlight', at: 0, command: 'spotlight.show' },
            { id: 'cursor', at: 120, command: 'cursor.move' },
            { id: 'operation', at: 180, command: 'operation.run', blocking: true }
        ]
    });

    assert.equal(result.completed, true);
    assert.deepEqual(result.triggered, ['spotlight', 'cursor', 'operation']);
    assert.deepEqual(calls, [
        ['spotlight.show', 0],
        ['cursor.move', 120],
        ['operation.run', 180, 'start'],
        ['operation.run', 180, 'end']
    ]);
});

test('TimelineEngine does not dispatch following commands until blocking command resolves', async () => {
    let currentTime = 0;
    let resolveBlocking;
    let resolveStarted;
    const startedPromise = new Promise((resolve) => {
        resolveStarted = resolve;
    });
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'operation.run': async () => {
                calls.push(['operation.run', currentTime, 'start']);
                resolveStarted();
                await new Promise((resolve) => {
                    resolveBlocking = resolve;
                });
                calls.push(['operation.run', currentTime, 'end']);
            },
            'cursor.move': () => {
                calls.push(['cursor.move', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const playPromise = engine.playScene({
        id: 'scene-blocking',
        timeline: [
            { id: 'operation', at: 10, command: 'operation.run', blocking: true },
            { id: 'cursor', at: 20, command: 'cursor.move' }
        ]
    });

    await startedPromise;
    assert.deepEqual(calls, [
        ['operation.run', 10, 'start']
    ]);

    resolveBlocking();
    const result = await playPromise;

    assert.equal(result.completed, true);
    assert.deepEqual(calls, [
        ['operation.run', 10, 'start'],
        ['operation.run', 10, 'end'],
        ['cursor.move', 20]
    ]);
});

test('TimelineEngine dispatches zero-time prelude commands before starting audio', async () => {
    let currentTime = 0;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'chat.message': () => {
                calls.push(['chat.message', currentTime]);
            },
            'spotlight.show': async () => {
                calls.push(['spotlight.show', currentTime]);
                await Promise.resolve();
                calls.push(['spotlight.ready', currentTime]);
            },
            'cursor.move': () => {
                calls.push(['cursor.move', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        audioRuntime: {
            play(voiceKey) {
                calls.push(['audio.play', voiceKey, currentTime]);
            },
            waitForEnd() {
                calls.push(['audio.end', currentTime]);
            }
        },
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const result = await engine.playScene({
        id: 'scene-a',
        audio: { voiceKey: 'voice-a', minDurationMs: 1000 },
        timeline: [
            { id: 'chat', at: 0, command: 'chat.message' },
            { id: 'spotlight', at: 0, command: 'spotlight.show' },
            { id: 'cursor', at: 120, command: 'cursor.move' }
        ]
    });

    assert.equal(result.completed, true);
    assert.deepEqual(calls, [
        ['chat.message', 0],
        ['spotlight.show', 0],
        ['spotlight.ready', 0],
        ['audio.play', 'voice-a', 0],
        ['cursor.move', 120],
        ['audio.end', 120]
    ]);
});

test('TimelineEngine starts explicit non-blocking zero-time commands without delaying audio', async () => {
    let currentTime = 0;
    let resolveMotion;
    let resolveMotionStarted;
    let resolveAudioEnded;
    const motionStartedPromise = new Promise((resolve) => {
        resolveMotionStarted = resolve;
    });
    const audioEndedPromise = new Promise((resolve) => {
        resolveAudioEnded = resolve;
    });
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'operation.run': async () => {
                calls.push(['motion.start', currentTime]);
                resolveMotionStarted();
                await new Promise((resolve) => {
                    resolveMotion = resolve;
                });
                calls.push(['motion.end', currentTime]);
            },
            'chat.message': () => {
                calls.push(['chat.message', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        audioRuntime: {
            play(voiceKey) {
                calls.push(['audio.play', voiceKey, currentTime]);
            },
            waitForEnd() {
                calls.push(['audio.end', currentTime]);
                resolveAudioEnded();
            }
        },
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const resultPromise = engine.playScene({
        id: 'scene-intro-motion',
        audio: { voiceKey: 'voice-a', minDurationMs: 1000 },
        timeline: [
            { id: 'motion', at: 0, command: 'operation.run', blocking: false },
            { id: 'chat', at: 0, command: 'chat.message' }
        ]
    });

    await motionStartedPromise;
    await audioEndedPromise;
    assert.deepEqual(calls.map((entry) => entry.slice()), [
        ['motion.start', 0],
        ['chat.message', 0],
        ['audio.play', 'voice-a', 0],
        ['audio.end', 0]
    ]);

    resolveMotion();
    const result = await resultPromise;

    assert.equal(result.completed, true);
    assert.deepEqual(result.triggered, ['motion', 'chat']);
    assert.deepEqual(calls, [
        ['motion.start', 0],
        ['chat.message', 0],
        ['audio.play', 'voice-a', 0],
        ['audio.end', 0],
        ['motion.end', 0]
    ]);
});

test('TimelineEngine shares audio runtime context with later commands', async () => {
    let currentTime = 0;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'petal.play': (event, context) => {
                calls.push(['petal.play', context.narrationStartedAt, context.scene.id]);
            }
        }
    });
    const sharedContext = {};
    const engine = new TimelineEngine({
        commandRegistry: registry,
        audioRuntime: {
            play() {
                sharedContext.narrationStartedAt = currentTime;
            }
        },
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const result = await engine.playScene({
        id: 'scene-with-petal',
        audio: { voiceKey: 'voice-a', minDurationMs: 1000 },
        timeline: [
            { id: 'petal', at: 120, command: 'petal.play', blocking: true }
        ]
    }, sharedContext);

    assert.equal(result.completed, true);
    assert.deepEqual(calls, [
        ['petal.play', 0, 'scene-with-petal']
    ]);
});

test('TimelineEngine schedules commands relative to narration end', async () => {
    let currentTime = 0;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'petal.play': () => {
                calls.push(['petal.play', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        audioRuntime: {
            getDurationMs() {
                return 10000;
            },
            play(voiceKey) {
                calls.push(['audio.play', voiceKey, currentTime]);
            }
        },
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const result = await engine.playScene({
        id: 'scene-with-petal',
        audio: { voiceKey: 'voice-a', minDurationMs: 1000 },
        timeline: [
            { id: 'petal', beforeAudioEndMs: 2600, command: 'petal.play', blocking: true }
        ]
    });

    assert.equal(result.completed, true);
    assert.deepEqual(calls, [
        ['audio.play', 'voice-a', 0],
        ['petal.play', 7400]
    ]);
});

test('TimelineEngine dispatches afterAudioEnd commands after narration settles', async () => {
    let currentTime = 0;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'cursor.move': () => {
                calls.push(['cursor.move', currentTime]);
            },
            'settingsPanel.close': async () => {
                calls.push(['settings.close:start', currentTime]);
                await Promise.resolve();
                calls.push(['settings.close:end', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        audioRuntime: {
            play(voiceKey) {
                calls.push(['audio.play', voiceKey, currentTime]);
            },
            waitForEnd() {
                calls.push(['audio.wait:start', currentTime]);
                currentTime += 900;
                calls.push(['audio.wait:end', currentTime]);
            }
        },
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        }
    });

    const result = await engine.playScene({
        id: 'scene-after-audio',
        audio: { voiceKey: 'voice-a', minDurationMs: 300 },
        timeline: [
            { id: 'cursor', at: 120, command: 'cursor.move' },
            { id: 'close', afterAudioEnd: true, command: 'settingsPanel.close', blocking: true }
        ]
    });

    assert.equal(result.completed, true);
    assert.deepEqual(result.triggered, ['cursor', 'close']);
    assert.deepEqual(calls, [
        ['audio.play', 'voice-a', 0],
        ['cursor.move', 120],
        ['audio.wait:start', 120],
        ['audio.wait:end', 1020],
        ['settings.close:start', 1020],
        ['settings.close:end', 1020]
    ]);
});

test('TimelineEngine pauses fallback clock while resistance pause is active', async () => {
    let currentTime = 0;
    let paused = true;
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'cursor.move': () => {
                calls.push(['cursor.move', currentTime]);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        now: () => currentTime,
        wait: async (delayMs) => {
            currentTime += delayMs;
        },
        isPaused: () => paused,
        waitUntilResumed: async () => {
            currentTime += 500;
            paused = false;
        }
    });

    const result = await engine.playScene({
        id: 'scene-paused',
        timeline: [
            { id: 'cursor', at: 120, command: 'cursor.move' }
        ]
    });

    assert.equal(result.completed, true);
    assert.deepEqual(calls, [
        ['cursor.move', 620]
    ]);
});

test('TimelineEngine cancels stale scene runs when a new run starts', async () => {
    let currentTime = 0;
    const waiters = [];
    const calls = [];
    const registry = new CommandRegistry({
        handlers: {
            'cursor.move': (event) => {
                calls.push(event.id);
            }
        }
    });
    const engine = new TimelineEngine({
        commandRegistry: registry,
        now: () => currentTime,
        wait: (delayMs) => new Promise((resolve) => {
            waiters.push({ delayMs, resolve });
        })
    });

    const firstRun = engine.playScene({
        id: 'old-scene',
        timeline: [
            { id: 'old-cursor', at: 120, command: 'cursor.move' }
        ]
    });

    assert.equal(waiters.length, 1);

    const secondRun = engine.playScene({
        id: 'new-scene',
        timeline: [
            { id: 'new-cursor', at: 0, command: 'cursor.move' }
        ]
    });

    const secondResult = await secondRun;
    assert.equal(secondResult.completed, true);
    assert.deepEqual(calls, ['new-cursor']);

    currentTime += waiters[0].delayMs;
    waiters[0].resolve();
    const firstResult = await firstRun;
    assert.equal(firstResult.cancelled, true);
    assert.deepEqual(calls, ['new-cursor']);
});
