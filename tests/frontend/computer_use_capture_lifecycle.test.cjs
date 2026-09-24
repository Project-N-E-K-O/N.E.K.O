const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(
  path.resolve(__dirname, '../../static/app/app-websocket.js'), 'utf8'
);
const start = source.indexOf('window.prepareComputerUseCapture = function ()');
const end = source.indexOf('window.releaseComputerUseCapture = releaseComputerUseCapture;', start);
assert.ok(start >= 0 && end > start);
const prepareSource = source.slice(start, end);

function makeHarness(getDisplayMedia, owner = async () => ({ success: true })) {
  let _computerUseStream = null;
  let _computerUseStreamPending = null;
  let _computerUseDisplayRequestPending = null;
  let _computerUseStreamGeneration = 0;
  let _computerUseStreamOwnerToken = null;
  let _computerUseCaptureFailure = '';
  const timers = [];
  const window = { screen: { isExtended: false } };
  const navigator = { mediaDevices: { getDisplayMedia } };
  const S = { selectedScreenSourceId: 'screen:1' };
  const provider = {
    captureComputerUseScreen() {},
    getComputerUseDisplayCount: async () => 1,
    setComputerUseStreamOwner: owner,
  };
  const resolveDesktopCaptureProvider = () => provider;
  const setTimeout = (callback) => { timers.push(callback); return timers.length; };
  const clearTimeout = () => {};
  const releaseComputerUseCapture = () => {};
  eval(prepareSource);
  return { window, timers };
}

function makeStream(onStop) {
  const track = {
    readyState: 'live',
    getSettings: () => ({ displaySurface: 'monitor' }),
    addEventListener() {},
    stop: onStop,
  };
  return {
    active: true,
    getVideoTracks: () => [track],
    getTracks: () => [track],
  };
}

test('a timed out portal request blocks overlapping permission prompts', async () => {
  let resolveFirst;
  let requests = 0;
  let stopped = 0;
  const { window, timers } = makeHarness(() => {
    requests += 1;
    return new Promise((resolve) => { if (requests === 1) resolveFirst = resolve; });
  });

  const first = window.prepareComputerUseCapture();
  timers[0]();
  assert.equal(await first, false);
  assert.equal(await window.prepareComputerUseCapture(), false);
  assert.equal(requests, 1);

  resolveFirst(makeStream(() => { stopped += 1; }));
  await new Promise(setImmediate);
  assert.equal(stopped, 1);
  window.prepareComputerUseCapture();
  assert.equal(requests, 2);
});

test('ownership rejection stops the acquired screen stream', async () => {
  let stopped = 0;
  const { window } = makeHarness(
    () => Promise.resolve(makeStream(() => { stopped += 1; })),
    async () => { throw new Error('owner IPC unavailable'); }
  );

  assert.equal(await window.prepareComputerUseCapture(), false);
  assert.equal(stopped, 1);
});

test('successful task polling retires a task missing from the server twice', async () => {
  const reconcileStart = source.indexOf('function scheduleAgentTaskReconciliation()');
  const reconcileEnd = source.indexOf('window.computerUseNeedsCaptureStream = function ()', reconcileStart);
  assert.ok(reconcileStart >= 0 && reconcileEnd > reconcileStart);

  let _agentTaskReconcileTimer = null;
  let _agentTaskReconcileInFlight = false;
  const _agentTaskMissingCounts = new Map();
  const window = {
    _agentTaskMap: new Map([['task-1', { id: 'task-1', status: 'running' }]]),
    AgentHUD: { updateAgentTaskHUD() {} },
  };
  let tick;
  const setInterval = (callback) => { tick = callback; return 1; };
  const clearInterval = () => {};
  const fetch = async () => ({ ok: true, json: async () => ({ tasks: [] }) });
  eval(source.slice(reconcileStart, reconcileEnd));

  scheduleAgentTaskReconciliation();
  await tick();
  assert.equal(window._agentTaskMap.has('task-1'), true);
  await tick();
  assert.equal(window._agentTaskMap.has('task-1'), false);
});
