const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const vm = require('node:vm');

function loadAppButtons(options = {}) {
  const context = {
    console: {
      debug() {},
      error() {},
      log() {},
      warn() {},
    },
    window: {
      appConst: {},
      appState: {},
      appUtils: {},
      ...(options.window || {}),
    },
    ...(options.globals || {}),
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, 'app/app-buttons.js'), 'utf8'),
    context,
  );
  return context.window.appButtons;
}

function createLifecycleHarness(windowOverrides = {}) {
  let now = 100_000;
  let nextTimerId = 0;
  const timers = new Map();
  const listeners = new Map();
  const sent = [];
  const socket = {
    readyState: 1,
    send(payload) {
      sent.push(JSON.parse(payload));
    },
  };
  class FakeDate extends Date {
    static now() {
      return now;
    }
  }
  const window = {
    appConst: {},
    appState: { socket },
    appUtils: {},
    addEventListener(type, listener) {
      const current = listeners.get(type) || [];
      current.push(listener);
      listeners.set(type, current);
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) || []).filter(item => item !== listener));
    },
    setTimeout(callback, delayMs) {
      const id = ++nextTimerId;
      timers.set(id, { callback, dueAt: now + Number(delayMs || 0) });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    ensureWebSocketOpen: async () => {},
    ...windowOverrides,
  };
  const appButtons = loadAppButtons({
    window,
    globals: {
      Date: FakeDate,
      WebSocket: { OPEN: 1 },
    },
  });
  appButtons.ensureAvatarInteractionTextContinuationLifecycle();

  function dispatch(type, detail) {
    for (const listener of listeners.get(type) || []) listener({ detail });
  }

  function advance(ms) {
    now += ms;
    while (true) {
      const due = Array.from(timers.entries())
        .filter(([, timer]) => timer.dueAt <= now)
        .sort((left, right) => left[1].dueAt - right[1].dueAt)[0];
      if (!due) break;
      timers.delete(due[0]);
      due[1].callback();
    }
  }

  return { advance, appButtons, dispatch, sent };
}

function interactionPayload(interactionId) {
  return {
    interactionId,
    toolId: 'lollipop',
    actionId: 'offer',
    target: 'avatar',
    pointer: { clientX: 10, clientY: 20 },
    timestamp: 100,
    intensity: 'normal',
  };
}

function interactionTurnMeta(interactionId) {
  return {
    kind: 'avatar_interaction',
    interaction_id: interactionId,
  };
}

test('assistant lifecycle forwards the backend response meta unchanged', () => {
  const websocketSource = fs.readFileSync(
    path.join(__dirname, 'app/app-websocket.js'),
    'utf8',
  );
  assert.match(
    websocketSource,
    /ensureAssistantTurnStarted\(\s*'gemini_response_first_chunk',\s*response\.turn_id,\s*response\.meta\s*\)/,
  );
  assert.match(
    websocketSource,
    /emitAssistantLifecycleEvent\('neko-assistant-turn-end', \{[\s\S]*?meta: response\.meta[\s\S]*?\}\);/,
  );
});

test('avatar interaction host contract is limited to the three established tools', () => {
  const { avatarInteractionContract: contract } = loadAppButtons();
  assert.deepEqual(Object.keys(contract.tools).sort(), ['fist', 'hammer', 'lollipop']);
  assert.deepEqual(Object.keys(contract.tools.lollipop.actions).sort(), ['offer', 'tap_soft', 'tease']);
  assert.deepEqual(Array.from(contract.tools.lollipop.actions.offer), ['normal']);
  assert.deepEqual(Array.from(contract.tools.lollipop.actions.tap_soft), ['rapid', 'burst']);
  assert.deepEqual(Array.from(contract.tools.fist.actions.poke), ['normal', 'rapid']);
  assert.deepEqual(
    Array.from(contract.tools.hammer.actions.bonk),
    ['normal', 'rapid', 'burst', 'easter_egg'],
  );
});

test('avatar interaction host and backend contracts stay in parity', () => {
  const { avatarInteractionContract: hostContract } = loadAppButtons();
  const repoRoot = path.resolve(__dirname, '..');
  const backendScript = String.raw`
import json
from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES,
    AVATAR_INTERACTION_TOOL_CONTRACT,
)

print(json.dumps({
    "touchZones": sorted(AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES),
    "tools": {
        tool_id: {
            "actions": {
                action_id: sorted(intensities)
                for action_id, intensities in tool_contract["actions"].items()
            },
            "acceptsTouchZone": tool_contract["touch_zone"],
            "booleanField": tool_contract["boolean_field"],
        }
        for tool_id, tool_contract in AVATAR_INTERACTION_TOOL_CONTRACT.items()
    },
}, sort_keys=True))
`;
  const backendContract = JSON.parse(execFileSync(
    'uv',
    ['run', 'python', '-c', backendScript],
    { cwd: repoRoot, encoding: 'utf8' },
  ));
  const normalizedHostContract = {
    touchZones: Array.from(hostContract.touchZones).sort(),
    tools: Object.fromEntries(Object.entries(hostContract.tools).map(([toolId, tool]) => [
      toolId,
      {
        actions: Object.fromEntries(Object.entries(tool.actions).map(([actionId, values]) => [
          actionId,
          Array.from(values).sort(),
        ])),
        acceptsTouchZone: tool.acceptsTouchZone,
        booleanField: tool.booleanField && tool.booleanField.output,
      },
    ])),
  };

  assert.deepEqual(normalizedHostContract, backendContract);
  for (const tool of Object.values(hostContract.tools)) {
    if (!tool.booleanField) continue;
    const expectedInput = tool.booleanField.output.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
    assert.equal(tool.booleanField.input, expectedInput);
  }
});

test('avatar interaction host normalizer isolates each tool special fields', () => {
  const { normalizeAvatarInteractionPayload: normalize } = loadAppButtons();
  const base = {
    interactionId: 'interaction-1',
    actionId: 'offer',
    target: 'avatar',
    pointer: { clientX: 12, clientY: 34 },
    timestamp: 1234,
    intensity: 'rapid',
    touchZone: 'head',
    rewardDrop: true,
    easterEgg: true,
  };

  const lollipop = normalize({ ...base, toolId: 'lollipop' });
  assert.equal(lollipop.intensity, 'normal');
  assert.equal(lollipop.touch_zone, undefined);
  assert.equal(lollipop.reward_drop, undefined);
  assert.equal(lollipop.easter_egg, undefined);

  const fist = normalize({
    ...base,
    toolId: 'fist',
    actionId: 'poke',
  });
  assert.equal(fist.intensity, 'rapid');
  assert.equal(fist.touch_zone, 'head');
  assert.equal(fist.reward_drop, true);
  assert.equal(fist.easter_egg, undefined);

  const hammer = normalize({
    ...base,
    toolId: 'hammer',
    actionId: 'bonk',
    intensity: 'normal',
  });
  assert.equal(hammer.intensity, 'easter_egg');
  assert.equal(hammer.touch_zone, 'head');
  assert.equal(hammer.reward_drop, undefined);
  assert.equal(hammer.easter_egg, true);
});

test('avatar interaction host normalizer rejects unsupported tools and preserves wire facts', () => {
  const { normalizeAvatarInteractionPayload: normalize } = loadAppButtons();
  assert.equal(normalize({
    interactionId: 'unsupported-1',
    toolId: 'unsupported-tool',
    actionId: 'unknown-action',
    target: 'avatar',
    timestamp: 1,
  }), null);

  const normalized = normalize({
    interactionId: 'hammer-1',
    toolId: 'hammer',
    actionId: 'bonk',
    target: 'avatar',
    pointer: { clientX: 8.5, clientY: 9.25 },
    timestamp: 99,
    intensity: 'easter_egg',
    textContext: ` ${'x'.repeat(90)} `,
  });
  assert.equal(normalized.action, 'avatar_interaction');
  assert.equal(normalized.interaction_id, 'hammer-1');
  assert.equal(normalized.intensity, 'easter_egg');
  assert.equal(normalized.easter_egg, true);
  assert.equal(normalized.text_context.length, 80);
  assert.equal(normalized.pointer.clientX, 8.5);
  assert.equal(normalized.pointer.clientY, 9.25);
});

test('avatar interaction host normalizer rejects invalid identity fields', () => {
  const { normalizeAvatarInteractionPayload: normalize } = loadAppButtons();
  assert.equal(normalize({
    interactionId: 'invalid-action',
    toolId: 'fist',
    actionId: 'bonk',
    target: 'avatar',
  }), null);
  assert.equal(normalize({
    interactionId: 'invalid-target',
    toolId: 'fist',
    actionId: 'poke',
    target: 'canvas',
  }), null);
  assert.equal(normalize({
    interactionId: '   ',
    toolId: 'fist',
    actionId: 'poke',
    target: 'avatar',
  }), null);
});

test('avatar interaction host normalizer falls back every action invalid intensity', () => {
  const {
    avatarInteractionContract: contract,
    normalizeAvatarInteractionPayload: normalize,
  } = loadAppButtons();
  for (const [toolId, tool] of Object.entries(contract.tools)) {
    for (const actionId of Object.keys(tool.actions)) {
      const normalized = normalize({
        interactionId: `${toolId}-${actionId}`,
        toolId,
        actionId,
        target: 'avatar',
        timestamp: 1,
        intensity: 'unsupported-intensity',
      });
      assert.equal(normalized.intensity, 'normal', `${toolId}/${actionId}`);
    }
  }
});

test('avatar interaction host normalizer protects touch zone, pointer, timestamp, text and bool boundaries', () => {
  const { normalizeAvatarInteractionPayload: normalize } = loadAppButtons();
  const lollipop = normalize({
    interactionId: 'lollipop-boundaries',
    toolId: 'lollipop',
    actionId: 'offer',
    target: 'avatar',
    timestamp: 0,
    touchZone: 'head',
    pointer: { clientX: 1, clientY: Number.POSITIVE_INFINITY },
    text_context: 'snake text',
    rewardDrop: true,
    easterEgg: true,
  });
  assert.ok(lollipop.timestamp > 0);
  assert.equal(lollipop.touch_zone, undefined);
  assert.equal(lollipop.pointer, undefined);
  assert.equal(lollipop.text_context, 'snake text');
  assert.equal(lollipop.reward_drop, undefined);
  assert.equal(lollipop.easter_egg, undefined);

  const fist = normalize({
    interactionId: 'fist-boundaries',
    toolId: 'fist',
    actionId: 'poke',
    target: 'avatar',
    timestamp: 1,
    touch_zone: ' FACE ',
    rewardDrop: false,
  });
  assert.equal(fist.touch_zone, 'face');
  assert.equal(fist.reward_drop, undefined);
});

test('avatar interaction host and backend normalizers preserve the same alias facts', () => {
  const { normalizeAvatarInteractionPayload: normalize } = loadAppButtons();
  const cases = [
    {
      interactionId: 'camel-fist',
      toolId: 'fist',
      actionId: 'poke',
      target: 'avatar',
      pointer: { clientX: 12.5, clientY: 34 },
      timestamp: 11,
      intensity: 'rapid',
      touchZone: 'face',
      rewardDrop: true,
      textContext: 'camel text',
    },
    {
      interaction_id: 'snake-fist',
      tool_id: 'fist',
      action_id: 'poke',
      target: 'avatar',
      pointer: { client_x: '8.5', client_y: 9.25 },
      timestamp: 12.9,
      intensity: 'rapid',
      touch_zone: 'ear',
      reward_drop: 'TRUE',
      text_context: 'snake text',
    },
    {
      interaction_id: 'snake-hammer',
      interactionId: 'camel-hammer',
      tool_id: 'hammer',
      toolId: 'fist',
      action_id: 'bonk',
      actionId: 'poke',
      target: 'avatar',
      pointer: { client_x: null, clientX: 4, client_y: null, clientY: 5 },
      timestamp: 13,
      intensity: 'normal',
      touch_zone: 'body',
      touchZone: 'head',
      easter_egg: '1',
      easterEgg: false,
      text_context: '',
      textContext: 'camel text must not win',
    },
    {
      interactionId: 'lollipop-isolation',
      toolId: 'lollipop',
      actionId: 'offer',
      target: 'avatar',
      pointer: { clientX: 1, clientY: 2 },
      timestamp: 14,
      intensity: 'burst',
      touch_zone: 'head',
      reward_drop: true,
      easter_egg: true,
    },
  ];
  const backendScript = String.raw`
import json
import sys
from config.prompts.avatar_interaction_contract import normalize_avatar_interaction_payload

def sanitize(value):
    text = str(value or '').strip()
    return text[:80].rstrip()

print(json.dumps([
    normalize_avatar_interaction_payload(payload, sanitize_text_context=sanitize)
    for payload in json.loads(sys.argv[1])
], sort_keys=True))
`;
  const backend = JSON.parse(execFileSync(
    'uv',
    ['run', 'python', '-c', backendScript, JSON.stringify(cases)],
    { cwd: path.resolve(__dirname, '..'), encoding: 'utf8' },
  ));

  function canonicalHost(payload) {
    const result = normalize(payload);
    return result && {
      interaction_id: result.interaction_id,
      tool_id: result.tool_id,
      action_id: result.action_id,
      target: result.target,
      timestamp: result.timestamp,
      intensity: result.intensity,
      reward_drop: result.reward_drop === true,
      easter_egg: result.easter_egg === true,
      touch_zone: result.touch_zone || '',
      text_context: result.text_context || '',
      pointer: result.pointer
        ? { client_x: result.pointer.clientX, client_y: result.pointer.clientY }
        : null,
    };
  }

  assert.deepEqual(cases.map(canonicalHost), backend);
});

test('avatar interaction host sends without owning model emotion state', async () => {
  const appliedEmotions = [];
  const harness = createLifecycleHarness({
    LanLan1: {
      setEmotion(emotion) {
        appliedEmotions.push(emotion);
      },
    },
    live2dManager: { currentEmotion: 'neutral' },
  });

  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('no-host-emotion')),
    true,
  );
  assert.equal(harness.sent.length, 1);
  assert.deepEqual(appliedEmotions, []);

  harness.dispatch('neko-assistant-emotion-ready', {
    turnId: 'assistant-turn',
    emotion: 'happy',
  });
  harness.advance(2200);
  assert.deepEqual(appliedEmotions, []);
});

test('avatar interaction continuation keeps slow replies pending until a late ack', async () => {
  const harness = createLifecycleHarness();
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('slow-1')), true);
  assert.equal(harness.sent.length, 1);

  harness.advance(9000);
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('slow-2')), false);
  assert.equal(harness.sent.length, 1);

  harness.dispatch('neko-avatar-interaction-ack', {
    interaction_id: 'slow-1',
    accepted: true,
    reason: 'delivered',
  });
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('slow-2')), true);
  assert.equal(harness.sent.length, 2);
});

test('avatar interaction continuation waits briefly for final ack after the matching turn ends', async () => {
  const harness = createLifecycleHarness();
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('turn-1')), true);
  harness.dispatch('neko-assistant-turn-start', {
    turn_id: 'assistant-turn-1',
    meta: interactionTurnMeta('turn-1'),
  });
  harness.advance(60001);
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('turn-2')), false);

  harness.dispatch('neko-assistant-turn-end', {
    turn_id: 'unrelated-turn',
    meta: interactionTurnMeta('turn-1'),
  });
  harness.advance(2000);
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('turn-2')), false);

  harness.dispatch('neko-assistant-turn-end', {
    turn_id: 'assistant-turn-1',
    meta: interactionTurnMeta('turn-1'),
  });
  harness.advance(1999);
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('turn-2')), false);
  harness.dispatch('neko-avatar-interaction-ack', {
    interactionId: 'turn-1',
    accepted: true,
    reason: 'delivered',
  });
  assert.equal(await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('turn-2')), true);
});

test('avatar interaction continuation has a separate active-turn safety timeout', async () => {
  const harness = createLifecycleHarness();
  await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('active-timeout-1'));
  harness.dispatch('neko-assistant-turn-start', {
    turnId: 'active-timeout-turn',
    meta: interactionTurnMeta('active-timeout-1'),
  });

  harness.advance(599999);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('active-timeout-2')),
    false,
  );
  harness.advance(1);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('active-timeout-2')),
    true,
  );
});

test('avatar interaction continuation releases after final-ack grace or no-signal timeout', async () => {
  const turnHarness = createLifecycleHarness();
  await turnHarness.appButtons.sendAvatarInteractionPayload(interactionPayload('grace-1'));
  turnHarness.dispatch('neko-assistant-turn-start', {
    turnId: 'turn-grace',
    meta: interactionTurnMeta('grace-1'),
  });
  turnHarness.dispatch('neko-assistant-turn-end', {
    turnId: 'turn-grace',
    meta: interactionTurnMeta('grace-1'),
  });
  turnHarness.advance(2000);
  assert.equal(await turnHarness.appButtons.sendAvatarInteractionPayload(interactionPayload('grace-2')), true);

  const timeoutHarness = createLifecycleHarness();
  await timeoutHarness.appButtons.sendAvatarInteractionPayload(interactionPayload('timeout-1'));
  timeoutHarness.advance(59999);
  assert.equal(await timeoutHarness.appButtons.sendAvatarInteractionPayload(interactionPayload('timeout-2')), false);
  timeoutHarness.advance(1);
  assert.equal(await timeoutHarness.appButtons.sendAvatarInteractionPayload(interactionPayload('timeout-2')), true);
});

test('unrelated assistant turns do not advance or release the pending interaction', async () => {
  const harness = createLifecycleHarness();
  await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('unrelated-1'));

  harness.dispatch('neko-assistant-turn-start', {
    turnId: 'ordinary-turn',
    meta: { kind: 'ordinary', interaction_id: 'unrelated-1' },
  });
  harness.dispatch('neko-assistant-turn-end', {
    turnId: 'ordinary-turn',
    meta: { kind: 'ordinary', interaction_id: 'unrelated-1' },
  });
  harness.dispatch('neko-assistant-turn-start', {
    turnId: 'other-avatar-turn',
    meta: interactionTurnMeta('some-other-interaction'),
  });
  harness.dispatch('neko-assistant-turn-end', {
    turnId: 'other-avatar-turn',
    meta: interactionTurnMeta('some-other-interaction'),
  });

  harness.advance(2000);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('unrelated-2')),
    false,
  );
  harness.advance(57999);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('unrelated-2')),
    false,
  );
  harness.advance(1);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('unrelated-2')),
    true,
  );
});

test('matching turn end advances without tagged start and duplicate or late signals are idempotent', async () => {
  const harness = createLifecycleHarness();
  await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('idempotent-1'));

  harness.dispatch('neko-assistant-turn-start', { turnId: 'idempotent-turn' });
  harness.dispatch('neko-assistant-turn-end', {
    turnId: 'idempotent-turn',
    meta: interactionTurnMeta('idempotent-1'),
  });
  harness.advance(1000);
  harness.dispatch('neko-assistant-turn-start', {
    turnId: 'idempotent-turn',
    meta: interactionTurnMeta('idempotent-1'),
  });
  harness.dispatch('neko-assistant-turn-end', {
    turnId: 'idempotent-turn',
    meta: interactionTurnMeta('idempotent-1'),
  });
  harness.advance(1000);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('idempotent-2')),
    true,
  );

  harness.dispatch('neko-assistant-turn-start', {
    turnId: 'idempotent-turn',
    meta: interactionTurnMeta('idempotent-1'),
  });
  harness.dispatch('neko-assistant-turn-end', {
    turnId: 'idempotent-turn',
    meta: interactionTurnMeta('idempotent-1'),
  });
  harness.dispatch('neko-avatar-interaction-ack', {
    interaction_id: 'idempotent-1',
    accepted: true,
    reason: 'delivered',
  });
  harness.advance(601);
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('idempotent-3')),
    false,
  );

  harness.dispatch('neko-avatar-interaction-ack', {
    interaction_id: 'idempotent-2',
    accepted: false,
    reason: 'busy',
  });
  assert.equal(
    await harness.appButtons.sendAvatarInteractionPayload(interactionPayload('idempotent-3')),
    true,
  );
});
