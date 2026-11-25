// @ts-nocheck -- runs the page's own scripts from disk with Node's modules, which this package's types leave out
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The task HUD (static/common-ui-hud.js) with the question of a task that waits for the user
// drawn inside its card (static/app/app-nekopower-ask.js): the two scripts as the page loads them.
const hudSource = readFileSync(resolve(process.cwd(), '../../static/common-ui-hud.js'), 'utf8');
const askSource = readFileSync(resolve(process.cwd(), '../../static/app/app-nekopower-ask.js'), 'utf8');

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

  it('still puts the cards in their order when that changed', () => {
    show([task('p1', { type: 'user_plugin' }), task('c2')]);
    expect(order()).toEqual(['c2', 'p1']);
    show([task('p1', { type: 'user_plugin' }), task('c2'), task('c3')]);
    expect(order()).toEqual(['c2', 'c3', 'p1']);
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
  });
});
