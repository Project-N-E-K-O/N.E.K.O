const assert = require('node:assert/strict');
const test = require('node:test');

const { normalizeTutorialScene } = require('./tutorial/core/script-normalizer.js');

test('normalizeTutorialScene maps legacy capsule spotlight and wobble cursor into timeline commands', () => {
    const scene = normalizeTutorialScene({
        id: 'day1_capsule_drag_hint',
        textKey: 'tutorial.avatarFloating.day1.capsuleDragHint',
        voiceKey: 'day1_capsule_drag_hint',
        target: 'chat-capsule-input',
        cursorAction: 'wobble',
        emotion: 'happy'
    });

    assert.equal(scene.id, 'day1_capsule_drag_hint');
    assert.equal(scene.audio.voiceKey, 'day1_capsule_drag_hint');
    assert.deepEqual(scene.timeline.map((event) => event.command), [
        'chat.message',
        'emotion.set',
        'spotlight.show',
        'cursor.move',
        'cursor.wobble'
    ]);
    assert.deepEqual(scene.timeline.find((event) => event.command === 'spotlight.show'), {
        id: 'day1_capsule_drag_hint:spotlight',
        at: 0,
        command: 'spotlight.show',
        key: 'day1_capsule_drag_hint',
        target: 'chat-capsule-input',
        persistent: '',
        secondary: ''
    });
    assert.equal(scene.timeline.find((event) => event.command === 'cursor.move').target, 'chat-capsule-input');
});

test('normalizeTutorialScene maps click scenes to cursor click and blocking operation commands', () => {
    const scene = normalizeTutorialScene({
        id: 'day2_avatar_tools',
        voiceKey: 'avatar_floating_day2_avatar_tools_intro',
        target: 'chat-tool-toggle',
        cursorAction: 'click',
        operation: 'open-compact-tool-fan',
        cursorMoveDurationMs: 640
    });

    const click = scene.timeline.find((event) => event.command === 'cursor.click');

    assert.deepEqual(scene.timeline.map((event) => event.command), [
        'chat.message',
        'spotlight.show',
        'cursor.move',
        'cursor.click'
    ]);
    assert.equal(click.at, 860);
    assert.equal(click.target, 'chat-tool-toggle');
    assert.equal(click.blocking, true);
    assert.deepEqual(click.onStart, [{
        id: 'day2_avatar_tools:operation',
        command: 'operation.run',
        operation: 'open-compact-tool-fan',
        trigger: 'onClickStart',
        blocking: true
    }]);
});

test('normalizeTutorialScene maps hold scenes to explicit cursor hold without move or operation commands', () => {
    const scene = normalizeTutorialScene({
        id: 'day2_galgame_choices',
        voiceKey: 'avatar_floating_day2_galgame_choices',
        persistent: 'chat-tool-toggle',
        target: 'chat-galgame',
        cursorAction: 'hold',
        cursorHoldFreezePoint: true,
        cursorHoldSettleMs: 260
    });

    assert.deepEqual(scene.timeline.map((event) => event.command), [
        'chat.message',
        'spotlight.show',
        'cursor.hold',
        'cursor.hold'
    ]);
    assert.deepEqual(scene.timeline.find((event) => event.command === 'cursor.hold'), {
        id: 'day2_galgame_choices:cursor-hold',
        at: 0,
        command: 'cursor.hold',
        target: 'chat-galgame',
        freezePoint: true
    });
    assert.deepEqual(scene.timeline.find((event) => event.id === 'day2_galgame_choices:cursor-hold-settle'), {
        id: 'day2_galgame_choices:cursor-hold-settle',
        at: 260,
        command: 'cursor.hold',
        target: 'chat-galgame',
        freezePoint: true
    });
    assert.equal(scene.timeline.some((event) => event.command === 'cursor.move'), false);
    assert.equal(scene.timeline.some((event) => event.command === 'operation.run'), false);
});

test('normalizeTutorialScene can freeze cursor after a move scene', () => {
    const scene = normalizeTutorialScene({
        id: 'day4_wrap',
        voiceKey: 'avatar_floating_day4_wrap',
        target: 'chat-input',
        cursorAction: 'move',
        freezeCursorAfterMove: true,
        preserveExternalizedChatGuideTarget: true,
        operation: 'cleanup'
    });

    const move = scene.timeline.find((event) => event.command === 'cursor.move');
    const operation = scene.timeline.find((event) => event.command === 'operation.run');

    assert.equal(move.target, 'chat-input');
    assert.equal(move.freezePoint, true);
    assert.equal(operation.preserveExternalizedChatGuideTarget, true);
    assert.equal(scene.timeline.some((event) => event.command === 'cursor.hold'), false);
});

test('normalizeTutorialScene maps day3 galgame wheel rotation to a dedicated timeline command', () => {
    const scene = normalizeTutorialScene({
        id: 'day2_galgame_entry',
        voiceKey: 'avatar_floating_day2_galgame_intro',
        persistent: 'chat-tool-toggle',
        target: 'chat-galgame',
        cursorAction: 'move',
        operation: 'rotate-galgame-tool-into-center',
        cursorMoveDurationMs: 640
    });

    assert.deepEqual(scene.timeline.map((event) => event.command), [
        'chat.message',
        'spotlight.show',
        'cursor.move',
        'compactToolWheel.rotateGalgameIntoCenter'
    ]);
    assert.deepEqual(scene.timeline.find((event) => event.command === 'compactToolWheel.rotateGalgameIntoCenter'), {
        id: 'day2_galgame_entry:galgame-wheel-rotation',
        at: 860,
        command: 'compactToolWheel.rotateGalgameIntoCenter',
        target: 'chat-galgame',
        blocking: true
    });
    assert.equal(scene.timeline.some((event) => event.command === 'operation.run'), false);
});

test('normalizeTutorialScene does not duplicate galgame rotation for click scenes', () => {
    const scene = normalizeTutorialScene({
        id: 'day2_galgame_entry_click',
        target: 'chat-galgame',
        cursorAction: 'click',
        operation: 'rotate-galgame-tool-into-center'
    });

    assert.equal(
        scene.timeline.filter((event) => event.command === 'compactToolWheel.rotateGalgameIntoCenter').length,
        1
    );
    assert.equal(scene.timeline.some((event) => event.command === 'operation.run'), false);
    assert.equal(
        scene.timeline.some((event) => Array.isArray(event.onStart) && event.onStart.some((nested) => (
            nested.command === 'operation.run'
        ))),
        false
    );
});

test('normalizeTutorialScene preserves explicit timeline scenes and adds audio metadata', () => {
    const scene = normalizeTutorialScene({
        id: 'explicit-scene',
        voiceKey: 'voice-a',
        timeline: [
            { at: 0, command: 'spotlight.show', target: 'chat-input' },
            { id: 'custom', atRatio: 0.7, command: 'petal.play', blocking: true }
        ]
    });

    assert.equal(scene.audio.voiceKey, 'voice-a');
    assert.deepEqual(scene.timeline, [
        { id: 'explicit-scene:cmd:0', at: 0, command: 'spotlight.show', target: 'chat-input' },
        { id: 'custom', atRatio: 0.7, command: 'petal.play', blocking: true }
    ]);
});

test('normalizeTutorialScene can disable timeline audio for flow-owned narration', () => {
    const scene = normalizeTutorialScene({
        id: 'day4_chat_settings',
        voiceKey: 'avatar_floating_day4_chat_settings',
        textKey: 'tutorial.avatarFloating.day4.chatSettings',
        text: 'line',
        timelineAudio: false,
        timeline: [
            { at: 0, command: 'settingsTour.play', blocking: true }
        ]
    });

    assert.deepEqual(scene.audio, {});
    assert.equal(scene.legacyScene.voiceKey, 'avatar_floating_day4_chat_settings');
    assert.deepEqual(scene.timeline, [
        {
            id: 'day4_chat_settings:cmd:0',
            at: 0,
            command: 'settingsTour.play',
            blocking: true
        }
    ]);
});

test('normalizeTutorialScene maps petal transition to explicit channel cleanup command', () => {
    const scene = normalizeTutorialScene({
        id: 'day7_wrap',
        voiceKey: 'avatar_floating_day7_wrap',
        target: 'chat-capsule-input',
        cursorAction: 'move',
        operation: 'cleanup',
        petalTransition: true
    });

    const petal = scene.timeline.find((event) => event.command === 'petal.play');
    assert.deepEqual(petal.clear, ['cursor', 'spotlights']);
    assert.equal(petal.beforeAudioEndMs, 2600);
    assert.equal(petal.atRatio, undefined);
    assert.equal(petal.blocking, true);
});

test('normalizeTutorialScene appends petal transition for explicit timeline scenes', () => {
    const scene = normalizeTutorialScene({
        id: 'day4_wrap',
        voiceKey: 'avatar_floating_day4_wrap',
        timeline: [
            { at: 0, command: 'chat.message' },
            { at: 1240, command: 'operation.run', operation: 'cleanup', blocking: true }
        ],
        petalTransition: true
    });

    const petalEvents = scene.timeline.filter((event) => event.command === 'petal.play');
    assert.equal(petalEvents.length, 1);
    assert.equal(petalEvents[0].beforeAudioEndMs, 2600);
    assert.equal(petalEvents[0].blocking, true);
});
