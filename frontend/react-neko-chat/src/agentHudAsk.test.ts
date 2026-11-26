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

  it('does not go by what the server said of a task that the connection updated while it was asked', async () => {
    vi.useFakeTimers();
    const schedule = new Function(`${reconcileSource}
 return scheduleAgentTaskReconciliation;`)();
    const hudUpdate = window.AgentHUD.updateAgentTaskHUD;
    window.AgentHUD.updateAgentTaskHUD = vi.fn();
    window._agentTaskMap = new Map([['c1', task('c1')], ['c2', task('c2')]]);
    let release = () => {};
    const held = new Promise(resolve => { release = resolve; });
    // Read before either of them asked anything; answered after the connection said c1 asks.
    vi.stubGlobal('fetch', vi.fn(async () => { await held; return { ok: true, json: async () => ({ tasks: [task('c1'), task('c2', { status: 'waiting_user', question: permission('q2') })] }) }; }));
    try {
      schedule();
      await vi.advanceTimersByTimeAsync(5000);
      window._agentTaskMap.set('c1', task('c1', { status: 'waiting_user', question: permission('q1') }));
      release();
      await vi.advanceTimersByTimeAsync(100);
      // c1 keeps what the connection said (the line being typed into is not taken away); c2 is brought up to date.
      expect(window._agentTaskMap.get('c1').question.id).toBe('q1');
      expect(window._agentTaskMap.get('c2').question.id).toBe('q2');
    } finally {
      window._agentTaskMap = new Map();
      await vi.advanceTimersByTimeAsync(60000);
      window.AgentHUD.updateAgentTaskHUD = hudUpdate;
      vi.unstubAllGlobals();
    }
  });

  it('says on the card of the task that a sentence added to an answer was not taken, after the question is gone', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ success: true, note_taken: false }) })));
    try {
      const waiting = task('c1', { status: 'waiting_user', question: permission() });
      show([waiting]);
      const input = card('c1').querySelector('.task-ask-input');
      input.value = 'use pnpm';
      card('c1').querySelector('[data-choice="deny"]').click();
      // The task goes on at once, and its question is taken off the card before the answer is back.
      show([task('c1', { status: 'running', question: null })]);
      await vi.advanceTimersByTimeAsync(0);
      expect(card('c1').querySelector('.task-ask')).toBeNull();
      expect(card('c1').querySelector('.task-ask-notice')?.textContent).toBe('回答送到了，补的那句没送到。');
      // Told again as it stands: still said. Not for good.
      show([task('c1', { status: 'running', question: null, progress: 0.4 })]);
      expect(card('c1').querySelector('.task-ask-notice')).toBeTruthy();
      await vi.advanceTimersByTimeAsync(15000);
      expect(card('c1').querySelector('.task-ask-notice')).toBeNull();
      show([task('c1', { status: 'running', question: null, progress: 0.6 })]);
      expect(card('c1').querySelector('.task-ask-notice')).toBeNull();

      // The task asks its next question before the last answer is back: saying the sentence was
      // lost leaves that question, and what is being typed into it, as they are.
      let back = () => {};
      vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => {
        back = () => resolve({ ok: true, status: 200, json: async () => ({ success: true, note_taken: false }) });
      })));
      show([task('c2', { status: 'waiting_user', question: permission('q1') })]);
      card('c2').querySelector('.task-ask-input').value = 'use pnpm';
      card('c2').querySelector('[data-choice="deny"]').click();
      await vi.advanceTimersByTimeAsync(0);
      show([task('c2', { status: 'waiting_user', question: permission('q2', 'git push') })]);
      const next = card('c2').querySelector('.task-ask-input');
      next.value = 'half a sen';
      back();
      await vi.advanceTimersByTimeAsync(0);
      expect(card('c2').querySelector('.task-ask').dataset.questionId).toBe('q2');
      expect(card('c2').querySelector('.task-ask-input')).toBe(next);
      expect(next.value).toBe('half a sen');
      expect(card('c2').querySelector('.task-ask-notice')?.textContent).toBe('回答送到了，补的那句没送到。');
    } finally {
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

// A task agent's own task agents are not shown as tasks: what they ask comes with the task shown
// above them (`sub_questions`, the one that asked first first), hung under its card by a line.
describe('what the task agents a task agent started ask', () => {
  const below = (id, question, over = {}) => ({ id, name: `agent-${id}`, title: `job ${id}`, question, ...over });
  const asking = (id, subs, over = {}) => task(id, { sub_questions: subs, ...over });
  const block = id => document.querySelector(`.task-sub-asks[data-task-id="${id}"]`);
  const row = (id, sub) => block(id)?.querySelector(`.task-sub-ask[data-sub-id="${sub}"]`);
  const subs = id => [...(block(id)?.querySelectorAll('.task-sub-ask') || [])].map(node => node.dataset.subId);
  const opened = (id, sub) => {
    const box = row(id, sub)?.querySelector('.task-ask');
    return box && box.style.display !== 'none' ? box : null;
  };
  const head = (id, sub) => row(id, sub).querySelector('.task-sub-ask-head');
  const list = () => [...document.getElementById('agent-task-list').children].filter(node => node.id !== 'agent-task-empty');

  it('hangs what one asks under the card of the task, outside it, while it waits', () => {
    show([asking('c1', [below('c2', permission('q2'))]), task('c5')]);
    // Not in the card, which goes on saying the task is at work: right under it.
    expect(card('c1').querySelector('.task-ask')).toBeNull();
    expect(card('c1').querySelector('.task-status-badge').textContent).toBe('运行中');
    expect(list()).toEqual([card('c1'), block('c1'), card('c5')]);
    // Who asks, and what, with the ways to answer.
    expect(row('c1', 'c2').querySelector('.task-sub-ask-who').textContent).toBe('job c2 · agent-c2');
    expect(row('c1', 'c2').querySelector('.task-sub-ask-badge').textContent).toBe('等你回话');
    expect(opened('c1', 'c2').querySelector('.task-ask-line').textContent).toBe('run the command npm test');
    expect(opened('c1', 'c2').querySelector('[data-choice="once"]').disabled).toBe(false);
    expect(block('c5')).toBeNull();

    // Answered (it asks no more): taken away. And with the task, once that is gone.
    show([asking('c1', []), task('c5')]);
    expect(block('c1')).toBeNull();
    show([asking('c1', [below('c2', permission('q2'))]), task('c5')]);
    show([task('c5')]);
    expect(document.querySelectorAll('.task-sub-asks').length).toBe(0);
    // Nothing a card can show is not drawn.
    show([asking('c1', [below('c3', null), { name: 'x' }, null])]);
    expect(block('c1')).toBeNull();
  });

  it('sends the answer to the task agent that asked, against its question', async () => {
    const sent = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      sent.push([url, JSON.parse(init.body)]);
      return { ok: true, status: 200, json: async () => ({ success: true, note_taken: true }) };
    }));
    try {
      show([asking('c1', [below('c2', permission('q2'))])]);
      opened('c1', 'c2').querySelector('.task-ask-input').value = 'use pnpm';
      opened('c1', 'c2').querySelector('[data-choice="once"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(sent).toEqual([['/api/agent/commissions/c2/answer', { question_id: 'q2', choice: 'once', note: 'use pnpm' }]]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('says on the card of the task that a sentence added to the answer was not taken', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ success: true, note_taken: false }) })));
    try {
      show([asking('c1', [below('c2', permission('q2'))])]);
      opened('c1', 'c2').querySelector('.task-ask-input').value = 'use pnpm';
      opened('c1', 'c2').querySelector('[data-choice="deny"]').click();
      // It goes on at once, and what it asked is taken away before the answer is back.
      show([asking('c1', [])]);
      await vi.advanceTimersByTimeAsync(0);
      expect(card('c1').querySelector('.task-ask-notice')?.textContent).toBe('回答送到了，补的那句没送到。');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('opens the one that asked first and folds the others into a line each, opened by a press', () => {
    show([asking('c1', [below('c2', permission('q2')), below('c3', permission('q3', 'git push')), below('c4', permission('q4'))])]);
    expect(subs('c1')).toEqual(['c2', 'c3', 'c4']);
    expect(opened('c1', 'c2')).toBeTruthy();
    // The others are a line each, nothing drawn in them until they are opened.
    expect(row('c1', 'c3').querySelector('.task-ask')).toBeNull();
    expect(row('c1', 'c4').querySelector('.task-ask')).toBeNull();
    expect([head('c1', 'c2'), head('c1', 'c3')].map(node => node.getAttribute('aria-expanded'))).toEqual(['true', 'false']);
    // The line goes on down past every one but the last.
    expect(['c2', 'c3', 'c4'].map(id => row('c1', id).querySelector('.task-sub-ask-trunk').style.display)).toEqual(['', '', 'none']);

    const typed = opened('c1', 'c2').querySelector('.task-ask-input');
    typed.value = 'half a sen';
    head('c1', 'c3').click();
    expect(opened('c1', 'c2')).toBeNull();
    expect(opened('c1', 'c3').querySelector('.task-ask-line').textContent).toBe('run the command git push');
    // Opened by a press on its line, not put in the place of another under the pointer: not held back.
    expect(opened('c1', 'c3').querySelector('[data-choice="once"]').disabled).toBe(false);
    // Told again as it stands: the one opened stays open.
    show([asking('c1', [below('c2', permission('q2')), below('c3', permission('q3', 'git push')), below('c4', permission('q4'))], { progress: 0.3 })]);
    expect(opened('c1', 'c3')).toBeTruthy();
    expect(document.querySelectorAll('.task-sub-ask .task-ask:not([style*="display: none"])').length).toBe(1);
    // Folded, not drawn anew: what was typed is still there.
    head('c1', 'c2').click();
    expect(opened('c1', 'c2').querySelector('.task-ask-input')).toBe(typed);
    expect(typed.value).toBe('half a sen');
  });

  it('takes what they ask from the server when the word of it was missed, the task above unchanged', async () => {
    vi.useFakeTimers();
    const schedule = new Function(`${reconcileSource}
 return scheduleAgentTaskReconciliation;`)();
    const told = vi.fn();
    const hudUpdate = window.AgentHUD.updateAgentTaskHUD;
    window.AgentHUD.updateAgentTaskHUD = told;
    window._agentTaskMap = new Map([
      ['c1', asking('c1', [])],
      ['c2', asking('c2', [below('c3', permission('q3'))])],
      ['c5', asking('c5', [below('c6', permission('q6'))])],
    ]);
    // The connection was away: under c1 one asks now, under c2 it asks no more, under c5 it asks the next thing.
    const server = [
      asking('c1', [below('c4', permission('q4'))]),
      asking('c2', []),
      asking('c5', [below('c6', permission('q7'))]),
    ];
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ tasks: server }) })));
    try {
      schedule();
      await vi.advanceTimersByTimeAsync(60000);
      const now = id => window._agentTaskMap.get(id).sub_questions.map(item => `${item.id}:${item.question.id}`);
      expect([now('c1'), now('c2'), now('c5')]).toEqual([['c4:q4'], [], ['c6:q7']]);
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

  it('says who asks by what it does, and by its name where that says more', () => {
    show([asking('c1', [below('c2', permission('q2'), { title: 'agent-c2' }), below('c3', permission('q3'), { title: '', name: '' })])]);
    expect(row('c1', 'c2').querySelector('.task-sub-ask-who').textContent).toBe('agent-c2');
    expect(row('c1', 'c3').querySelector('.task-sub-ask-who').textContent).toBe('使魔');
  });

  it('puts the next one up with its buttons held back once the one open is answered', async () => {
    vi.useFakeTimers();
    const two = [below('c2', permission('q2')), below('c3', permission('q3', 'git push'))];
    show([asking('c1', two)]);
    show([asking('c1', two.slice(1))]);
    // In the place of the one answered, under the pointer: a press on its way there does not approve this.
    expect(opened('c1', 'c3').querySelector('[data-choice="once"]').disabled).toBe(true);
    vi.advanceTimersByTime(window.NekoPowerAsk.SETTLE_MS);
    expect(opened('c1', 'c3').querySelector('[data-choice="once"]').disabled).toBe(false);

    // Drawn while it was open before, folded since, with words typed in it: shown as it was, held back the same,
    // from the keyboard as well (a button reached by Tab, Enter in the line).
    const sent = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ success: true, note_taken: true }) }));
    vi.stubGlobal('fetch', sent);
    try {
      const three = [below('c4', permission('q4')), below('c5', { kind: 'report', id: 'q5', question: 'Use the newest?' }), below('c6', permission('q6'))];
      show([asking('c1', three)]);
      head('c1', 'c5').click();
      opened('c1', 'c5').querySelector('.task-ask-input').value = 'half a sen';
      head('c1', 'c4').click();
      show([asking('c1', three.slice(1))]);
      const box = opened('c1', 'c5');
      const input = box.querySelector('.task-ask-input');
      expect(input.value).toBe('half a sen');
      expect(box.querySelector('[data-ask-send]').disabled).toBe(true);
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await vi.advanceTimersByTimeAsync(0);
      expect(sent).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(window.NekoPowerAsk.SETTLE_MS);
      expect(box.querySelector('[data-ask-send]').disabled).toBe(false);
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await vi.advanceTimersByTimeAsync(0);
      expect(sent.mock.calls.map(call => call[0])).toEqual(['/api/agent/commissions/c5/answer']);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('shows what one asks anew in place of what it asked with the buttons held back', () => {
    vi.useFakeTimers();
    show([asking('c1', [below('c2', permission('q2'))])]);
    show([asking('c1', [below('c2', permission('q7', 'rm -rf dist'))])]);
    expect(row('c1', 'c2').querySelectorAll('.task-ask').length).toBe(1);
    expect(opened('c1', 'c2').querySelector('.task-ask-line').textContent).toBe('run the command rm -rf dist');
    expect(opened('c1', 'c2').querySelector('[data-choice="once"]').disabled).toBe(true);
    vi.advanceTimersByTime(window.NekoPowerAsk.SETTLE_MS);
    // Back at work for a moment, and asking the next thing: held back all the same.
    show([asking('c1', [])]);
    show([asking('c1', [below('c2', permission('q8', 'git push'))])]);
    expect(opened('c1', 'c2').querySelector('[data-choice="once"]').disabled).toBe(true);
  });

  it('leaves the line being typed into alone while the tasks are updated', async () => {
    const two = [below('c2', permission('q2')), below('c3', permission('q3'))];
    show([asking('c1', two), task('c5')]);
    const input = opened('c1', 'c2').querySelector('.task-ask-input');
    const taken = [];
    const watch = new MutationObserver(records => records.forEach(record => taken.push(...record.removedNodes)));
    watch.observe(document.getElementById('agent-task-list'), { childList: true, subtree: true });
    input.focus();
    input.value = 'use pn';
    show([asking('c1', two, { progress: 0.5 }), task('c5', { progress: 0.4 })]);
    show([task('c6'), asking('c1', two, { progress: 0.6 }), task('c5', { progress: 0.4 })]);
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe('use pn');
    await Promise.resolve();
    expect(taken.includes(block('c1')) || taken.includes(row('c1', 'c2'))).toBe(false);
    watch.disconnect();
  });

  it('neither moves the HUD nor counts as a press on the card when pressed', () => {
    show([asking('c1', [below('c2', permission('q2'))])]);
    const pressed = vi.fn();
    document.getElementById('agent-task-list').addEventListener('mousedown', pressed);
    document.getElementById('agent-task-list').addEventListener('click', pressed);
    head('c1', 'c2').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    head('c1', 'c2').click();
    expect(pressed).not.toHaveBeenCalled();
  });
});
