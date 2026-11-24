import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DRIVE_PATH = path.join(ROOT, 'scripts', 'visit_probe', 'drive.mjs');

function extractPhaseT2Harness(overrides = {}) {
  const source = fs.readFileSync(DRIVE_PATH, 'utf8');
  const start = source.indexOf('async function phaseT2(c) {');
  const end = source.indexOf('\nasync function phaseT5(c) {', start);
  assert.notEqual(start, -1, 'phaseT2 not found in drive.mjs');
  assert.notEqual(end, -1, 'phaseT5 boundary not found in drive.mjs');
  const fnSource = source.slice(start, end);

  const savedSnapshots = [];
  const results = {
    runs: {
      t2: { runId: 'r1', status: 'running', dataFromRunId: null },
    },
  };

  const ctx = vm.createContext({
    results,
    save: () => savedSnapshots.push(JSON.parse(JSON.stringify(results))),
    sleep: async () => {},
    log: () => {},
    measureWindow: async () => ({ fps: 30 }),
    blankRun: async (_c, label) => ({
      label,
      framesChecked: 300,
      complete: true,
      blankFrames: 0,
      blankDiag: [],
    }),
    ...overrides,
  });

  vm.runInContext(`${fnSource}\nglobalThis.__phaseT2 = phaseT2;`, ctx);
  return { phaseT2: ctx.__phaseT2, results, savedSnapshots };
}

test('F0340: phaseT2 throws and preserves partialData when a blankRun times out incomplete', async () => {
  const { phaseT2, results, savedSnapshots } = extractPhaseT2Harness({
    blankRun: async (_c, label) => ({
      label,
      framesChecked: 0,
      complete: false,
      blankFrames: 0,
      blankDiag: [],
    }),
  });

  const fakeCdp = {
    eval: async (code) => {
      if (code.includes('env()')) return { refreshHz: 60 };
      if (code.includes('computeCrop')) return { ok: true };
      if (code.includes('setMode')) return { idleTickMode: 'timer', idleTickFps: 30, tickerMaxFPS: 30 };
      return true;
    },
  };

  await assert.rejects(
    () => phaseT2(fakeCdp),
    /timer30-sync: only 0\/300 frames verified before timeout; not a valid blank check/
  );

  assert.equal(results.t2, undefined, 'incomplete phaseT2 must not promote results.t2');
  assert.ok(results.runs.t2.partialData, 'incomplete phaseT2 must keep partialData');
  assert.equal(results.runs.t2.partialData.blank.length, 1);
  assert.equal(results.runs.t2.partialData.blank[0].complete, false);
  assert.ok(savedSnapshots.length >= 1);
});

test('F0340: phaseT2 promotes results.t2 and clears partialData when all blank runs complete', async () => {
  const { phaseT2, results } = extractPhaseT2Harness();

  const fakeCdp = {
    eval: async (code) => {
      if (code.includes('env()')) return { refreshHz: 60 };
      if (code.includes('computeCrop')) return { ok: true };
      if (code.includes('setMode')) return { idleTickMode: 'timer', idleTickFps: 60, tickerMaxFPS: 60 };
      if (code.includes('window.__visitProbe.stat')) return { captureTimes: [100, 1200], postrender: 1 };
      return true;
    },
  };

  await phaseT2(fakeCdp);
  assert.ok(results.t2);
  assert.equal(results.t2.blank.length, 4);
  assert.ok(results.t2.blank.every((b) => b.complete === true));
  assert.equal(results.runs.t2.partialData, undefined);
});
