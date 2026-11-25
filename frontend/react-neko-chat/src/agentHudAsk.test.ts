// @ts-nocheck -- runs the page's own scripts from disk with Node's modules, which this package's types leave out
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The task HUD (static/common-ui-hud.js) with the question of a task that waits for the user
// drawn inside its card (static/app/app-nekopower-ask.js): the two scripts as the page loads them.
const hudSource = readFileSync(resolve(process.cwd(), '../../static/common-ui-hud.js'), 'utf8');
const askSource = readFileSync(resolve(process.cwd(), '../../static/app/app-nekopower-ask.js'), 'utf8');
// The page's own check of the tasks against the server (in `app-websocket.js`), cut out of its file:
// from where its state is declared to the next function after it.
const socketSource = readFileSync(resolve(process.cwd(), '../../static/app/app-websocket.js'), 'utf8');
const reconcileStart = socketSource.indexOf('    let _agentTaskReconcileTimer = null;');
const reconcileEnd = socketSource.indexOf('}, 5000);', reconcileStart);
const reconcileSource = socketSource.slice(reconcileStart, socketSource.indexOf('}', reconcileEnd + 9) + 1);

const permission = (id = 'q1', command = 'npm test') => ({
  kind: 'permission', id, category: 'run_command', options: ['always', 'session', 'once', 'deny'],
  detail: { command }, line: `run the command ${command}`, line_cut: false,
});
const task = (id, over = {}) => ({
  id, status: 'running', type: 'task_agent', start_time: '2026-11-26T00:00:00Z', lanlan_name: 'Lan',
  params: { description: `task ${id}` }, question: null, ...over,
});

let hud;
function show(tasks) {
  hud._latestTasksData = { success: true, tasks };
  hud._doUpdateAgentTaskHUD();
}
const card = id => document.querySelector(`.task-card[data-task-id="${id}"]`);
const order = () => [...document.querySelectorAll('.task-card')].map(node => node.dataset.taskId);

beforeEach(() => {
  document.body.innerHTML = '<div id="agent-task-hud"><div id="agent-task-list"><div id="agent-task-empty"></div></div></div>';
  delete window.AgentHUD;
  delete window.NekoPowerAsk;
  new Function(askSource)();
  new Function(hudSource)();
  hud = window.AgentHUD;
});
afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('the task HUD and what a task asks', () => {
  it('draws the question of a task that waits for the user in its card, and of no other', () => {
    show([task('c1', { status: 'waiting_user', question: permission() }), task('c2')]);
    expect(card('c1').querySelector('.task-ask-line')?.textContent).toBe('run the command npm test');
    expect(card('c1').querySelector('[data-choice="once"]')).toBeTruthy();
    expect(card('c2').querySelector('.task-ask')).toBeNull();
    // A plugin that does the work itself takes no sentence added to an approval.
    show([task('c1', { status: 'waiting_user', question: permission() }), task('c3', { type: 'user_plugin', status: 'waiting_user', question: permission('q7') })]);
    expect(card('c1').querySelector('.task-ask-input')).toBeTruthy();
    expect(card('c3').querySelector('[data-choice="once"]')).toBeTruthy();
    expect(card('c3').querySelector('.task-ask-input')).toBeNull();
  });

  it('leaves the line being typed into alone while the tasks are updated', () => {
    const waiting = task('c1', { status: 'waiting_user', question: permission() });
    show([waiting, task('c2')]);
    const input = card('c1').querySelector('.task-ask-input');
    const moved = vi.fn();
    new MutationObserver(moved).observe(document.getElementById('agent-task-list'), { childList: true });
    input.focus();
    input.value = 'use pn';
    // Another task moves on; this one is told again as it stands.
    show([{ ...waiting }, task('c2', { progress: 0.5 })]);
    show([{ ...waiting }, task('c2', { progress: 0.9 })]);
    expect(card('c1').querySelector('.task-ask-input')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe('use pn');
    // Not taken out and put back: that is what loses the focus, and breaks a word being composed.
    return Promise.resolve().then(() => expect(moved).not.toHaveBeenCalled());
  });

  it('leaves it alone as well when another task comes or goes', async () => {
    const waiting = task('c2', { status: 'waiting_user', question: permission() });
    show([task('c1'), waiting]);
    const input = card('c2').querySelector('.task-ask-input');
    const asking = card('c2');
    const taken = [];
    new MutationObserver(records => records.forEach(record => taken.push(...record.removedNodes)))
      .observe(document.getElementById('agent-task-list'), { childList: true });
    input.focus();
    input.value = 'use pn';
    // One more task, put after it; then one put before it (a plugin's sinks below the familiars').
    show([task('c1'), { ...waiting }, task('c3')]);
    show([task('c1'), { ...waiting }, task('c3'), task('p9', { type: 'user_plugin' })]);
    show([{ ...waiting }, task('c3'), task('p9', { type: 'user_plugin' })]);
    expect(order()).toEqual(['c2', 'c3', 'p9']);
    expect(card('c2')).toBe(asking);
    expect(document.activeElement).toBe(input);
    await Promise.resolve();
    // The card that is being typed into was never taken out of the list.
    expect(taken.includes(asking)).toBe(false);
  });

  it('takes what a task asks from the server when the word of it was missed', async () => {
    vi.useFakeTimers();
    const schedule = new Function(`${reconcileSource}
 return scheduleAgentTaskReconciliation;`)();
    const told = vi.fn();
    const hudUpdate = window.AgentHUD.updateAgentTaskHUD;
    window.AgentHUD.updateAgentTaskHUD = told;
    window._agentTaskMap = new Map([
      ['c1', task('c1', { status: 'waiting_user', question: permission('q1') })],
      ['c2', task('c2', { status: 'waiting_user', question: permission('q5') })],
      ['c3', task('c3')],
      ['c4', task('c4', { status: 'queued' })],
    ]);
    // The connection was away: c1 was answered and asks the next thing, c2 is back at work, c3 asks now.
    const server = [
      task('c1', { status: 'waiting_user', question: permission('q2', 'git push') }),
      task('c2', { status: 'running', question: null }),
      task('c3', { status: 'waiting_user', question: permission('q9') }),
      // Begun meanwhile, with nothing asked before or after.
      task('c4', { status: 'running' }),
    ];
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ tasks: server }) })));
    try {
      schedule();
      await vi.advanceTimersByTimeAsync(60000);
      const now = id => window._agentTaskMap.get(id);
      expect([now('c1').question.id, now('c2').status, now('c2').question, now('c3').question.id]).toEqual(['q2', 'running', null, 'q9']);
      expect(now('c4').status).toBe('running');
      expect(told).toHaveBeenCalled();
      // Nothing changed since: the HUD is not told again.
      told.mockClear();
      await vi.advanceTimersByTimeAsync(60000);
      expect(told).not.toHaveBeenCalled();
    } finally {
      window._agentTaskMap = new Map();
      await vi.advanceTimersByTimeAsync(60000);
      window.AgentHUD.updateAgentTaskHUD = hudUpdate;
      vi.unstubAllGlobals();
    }
  });

  it('still puts the cards in their order when that changed', () => {
    show([task('p1', { type: 'user_plugin' }), task('c2')]);
    expect(order()).toEqual(['c2', 'p1']);
    show([task('p1', { type: 'user_plugin' }), task('c2'), task('c3')]);
    expect(order()).toEqual(['c2', 'c3', 'p1']);
    // The same cards, told in another order: put so, though none came or went.
    show([task('c3'), task('c2'), task('p1', { type: 'user_plugin' })]);
    expect(order()).toEqual(['c3', 'c2', 'p1']);
    show([task('c3')]);
    expect(order()).toEqual(['c3']);
  });

  it('shows the next question in place of the last with its buttons held back, and none once the task is at work', () => {
    vi.useFakeTimers();
    show([task('c1', { status: 'waiting_user', question: permission() })]);
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(false);
    show([task('c1', { status: 'waiting_user', question: permission('q2', 'rm -rf dist') })]);
    expect(document.querySelectorAll('.task-ask').length).toBe(1);
    expect(card('c1').querySelector('.task-ask').dataset.questionId).toBe('q2');
    expect(card('c1').querySelector('.task-ask-line')?.textContent).toBe('run the command rm -rf dist');
    // A press on its way to the last question does not approve this one.
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(true);
    vi.advanceTimersByTime(window.NekoPowerAsk.SETTLE_MS);
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(false);
    show([task('c1', { status: 'running', question: null })]);
    expect(card('c1').querySelector('.task-ask')).toBeNull();
    // At work for a moment, shown so, and then the next question: held back all the same.
    show([task('c1', { status: 'waiting_user', question: permission('q3', 'git push') })]);
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(true);
    vi.advanceTimersByTime(window.NekoPowerAsk.SETTLE_MS);
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(false);
    // The same question told again after that: not held back anew.
    show([task('c1', { status: 'running', question: null })]);
    show([task('c1', { status: 'waiting_user', question: permission('q3', 'git push') })]);
    expect(card('c1').querySelector('[data-choice="once"]').disabled).toBe(false);
  });
});
