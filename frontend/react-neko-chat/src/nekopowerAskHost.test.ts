// @ts-nocheck -- reads the page's own script from disk with Node's modules, which this package's types leave out
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseChatMessage } from './message-schema';

const source = readFileSync(resolve(process.cwd(), '../../static/app/app-nekopower-ask.js'), 'utf8');

// The host side of the question cards (static/app/app-nekopower-ask.js): the script the main
// page loads, run here as it is. It draws the question inside a card of the task HUD, keeps the
// cards of the chat window, and sends every answer.

type Asks = {
  render: (options: Record<string, unknown>) => HTMLElement;
  receive: (frame: Record<string, unknown>) => void;
  sync: () => Promise<void>;
  view: (question: unknown) => Record<string, unknown>;
  SETTLE_MS: number;
};
type Message = { id: string; blocks: Array<Record<string, unknown>> };

const LONG = `cd D:/blog && npm run build -- ${'--flag '.repeat(30)}--last`;
const permission = { kind: 'permission', id: 'q1', category: 'run_command', options: ['always', 'session', 'once', 'deny'],
  detail: { command: 'npm test' }, line: 'run the command npm test', line_cut: false };
const confirmation = { kind: 'confirmation', id: 'q2', question: 'Send it to the group?' };
const twoQuestions = { kind: 'questions', id: 'q4', questions: [
  { id: 'same', question: 'Deploy where?', options: ['Vercel', 'Not now'] },
  { id: 'same', question: 'Which tags?', options: ['a', 'b', 'Not now'], multi_select: true },
] };

let asks: Asks;
let messages: Message[];
let calls: Array<{ url: string; body: Record<string, unknown> | null; headers: Record<string, string> }>;
let reply: (url: string) => { status: number; body: unknown };
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const block = (index = 0) => messages[index].blocks[0];
function press(node: Element | null) {
  expect(node).toBeTruthy();
  (node as HTMLElement).click();
}
function answerFromChat(payload: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent('react-chat-window:action', {
    detail: { message: messages[0], action: { action: 'nekopower_ask_answer', payload } },
  }));
}

beforeEach(() => {
  messages = [];
  calls = [];
  reply = () => ({ status: 200, body: { success: true } });
  const w = window as unknown as Record<string, unknown>;
  w.reactChatWindowHost = {
    appendMessage: (message: Message) => { messages.push(message); },
    updateMessage: (id: string, patch: Partial<Message>) => { Object.assign(messages.find(each => each.id === id)!, patch); },
  };
  w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'token' }) };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: { body?: string; headers?: Record<string, string> }) => {
    calls.push({ url, body: options?.body ? JSON.parse(options.body) : null, headers: options?.headers ?? {} });
    const answer = reply(url);
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body };
  }));
  delete w.NekoPowerAsk;
  // Each test its own copy: the script keeps which cards are open.
  new Function(source)();
  asks = w.NekoPowerAsk as Asks;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('the question inside a card of the task HUD', () => {
  const draw = (question: Record<string, unknown>, more: Record<string, unknown> = {}) => {
    const box = asks.render({ taskId: 'c1', question, ...more });
    document.body.append(box);
    return box;
  };
  const shownChoices = (box: Element) => [...box.querySelectorAll<HTMLElement>('[data-choice]')]
    .filter(node => node.style.display !== 'none').map(node => node.dataset.choice);

  it('approves with a button, a sentence going along, against the question that was shown', async () => {
    const box = draw(permission);
    expect(box.querySelector('.task-ask-line')?.textContent).toBe('run the command npm test');
    expect(shownChoices(box)).toEqual(['always', 'session', 'once', 'deny']);
    const input = box.querySelector('.task-ask-input') as HTMLInputElement;
    input.value = 'use pnpm';
    // Words alone would be read as a refusal: no button sends them, and Enter does nothing.
    expect(box.querySelector('[data-ask-send]')).toBeNull();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    expect(calls).toEqual([]);
    press(box.querySelector('[data-choice="deny"]'));
    await flush();
    expect(calls).toEqual([{
      url: '/api/agent/commissions/c1/answer', body: { question_id: 'q1', choice: 'deny', note: 'use pnpm' },
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'token' },
    }]);
    // It says so, and takes no second answer.
    expect(box.querySelector('.task-ask-status')?.textContent).toContain('拒绝');
    expect((box.querySelector('[data-choice="once"]') as HTMLButtonElement).disabled).toBe(true);
    press(box.querySelector('[data-choice="once"]'));
    await flush();
    expect(calls.length).toBe(1);
  });

  it('offers an approval that holds for more than this once only with all of the command opened', () => {
    const box = draw({ ...permission, detail: { command: LONG }, line: 'run the command cd D:/blog … --last', line_cut: true });
    expect(shownChoices(box)).toEqual(['once', 'deny']);
    expect((box.querySelector('.task-ask-whole') as HTMLElement).style.display).toBe('none');
    press(box.querySelector('[data-ask-whole]'));
    expect((box.querySelector('.task-ask-whole') as HTMLElement).style.display).toBe('block');
    expect(box.querySelector('.task-ask-whole')?.textContent).toBe(LONG);
    expect(shownChoices(box)).toEqual(['always', 'session', 'once', 'deny']);
    press(box.querySelector('[data-ask-whole]'));
    expect(shownChoices(box)).toEqual(['once', 'deny']);
  });

  it('has no line for a sentence where a plugin does the work, and holds back after taking another question\'s place', () => {
    vi.useFakeTimers();
    const box = draw(permission, { noNote: true, settle: true });
    expect(box.querySelector('.task-ask-input')).toBeNull();
    const once = box.querySelector('[data-choice="once"]') as HTMLButtonElement;
    expect(once.disabled).toBe(true);
    vi.advanceTimersByTime(asks.SETTLE_MS);
    expect(once.disabled).toBe(false);
  });

  it('answers yes or no, what was picked, or words; not while a word is being composed', async () => {
    const yesNo = draw(confirmation);
    (yesNo.querySelector('.task-ask-input') as HTMLInputElement).value = 'only two';
    press(yesNo.querySelector('[data-confirm="yes"]'));
    await flush();

    const picks = asks.render({ taskId: 'c2', question: twoQuestions });
    document.body.append(picks);
    const options = [...picks.querySelectorAll<HTMLElement>('[data-option]')];
    // Deploy where: the later press takes the first one's place. Which tags: both stay.
    [0, 1, 2, 4].forEach(index => press(options[index]));
    expect(options.map(option => option.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'true', 'false', 'true']);
    press(picks.querySelector('[data-ask-send]'));
    await flush();

    const words = asks.render({ taskId: 'c3', question: { kind: 'report', id: 'q5', question: 'Which file?' } });
    document.body.append(words);
    const input = words.querySelector('.task-ask-input') as HTMLInputElement;
    input.value = 'the newest';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
    await flush();
    expect(calls.length).toBe(2);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();

    expect(calls.map(call => call.body)).toEqual([
      { question_id: 'q2', choice: 'yes', text: 'only two' },
      { question_id: 'q4', selected: { 0: ['Not now'], 1: ['a', 'Not now'] } },
      { question_id: 'q5', text: 'the newest' },
    ]);
  });

  it('says of an answer that came too late that the question is another by now, and lets a failed one be sent again', async () => {
    reply = url => (url.includes('/answer') ? { status: 409, body: { detail: { code: 'not_taken' } } } : { status: 200, body: { questions: [] } });
    const late = draw(permission);
    press(late.querySelector('[data-choice="once"]'));
    await flush();
    expect(late.querySelector('.task-ask-status')?.textContent).toContain('没送到：这个问题已经答过了');
    expect((late.querySelector('[data-choice="once"]') as HTMLButtonElement).disabled).toBe(true);
    // What waits now is read anew.
    expect(calls.map(call => call.url)).toContain('/api/agent/questions');

    reply = () => ({ status: 502, body: { success: false } });
    const failed = draw(confirmation);
    press(failed.querySelector('[data-confirm="yes"]'));
    await flush();
    expect(failed.querySelector('.task-ask-status')?.textContent).toBe('没送到，再试一次吧。');
    expect((failed.querySelector('[data-confirm="yes"]') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('the cards of the chat window', () => {
  const frame = (question: Record<string, unknown> | null, more: Record<string, unknown> = {}) =>
    ({ type: 'nekopower_question', task_id: 'c1', title: 'build the site', question, question_id: '', ...more });

  it('shows a question as a message the chat can draw, once', () => {
    asks.receive(frame(permission, { route: 'plugin' }));
    asks.receive(frame(permission));
    expect(messages.length).toBe(1);
    expect(messages[0].id).toBe('nekopower-ask-c1-q1');
    expect(block()).toMatchObject({ type: 'nekopower_ask', taskId: 'c1', questionId: 'q1', title: 'build the site', state: 'pending', noNote: true });
    // The chat takes it as it is.
    expect(() => parseChatMessage(messages[0])).not.toThrow();
  });

  it('closes the card of the question that is over, and no other', () => {
    asks.receive(frame(permission));
    // Late word that an earlier question of the task is over: this one stays.
    asks.receive(frame(null, { question_id: 'q0' }));
    expect(block().state).toBe('pending');
    // The next question of the task: the last one is over.
    asks.receive(frame(confirmation));
    expect(messages.map(message => message.blocks[0].state)).toEqual(['closed', 'pending']);
    asks.receive(frame(null, { question_id: 'q2' }));
    expect(block(1).state).toBe('closed');
    // Nothing to close for a task with no card.
    asks.receive(frame(null, { task_id: 'c9' }));
    expect(messages.length).toBe(2);
  });

  it('sends the answer pressed on a card and says how it fared', async () => {
    asks.receive(frame(permission));
    reply = () => ({ status: 200, body: { success: true, note_taken: false } });
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'deny', note: 'use pnpm' });
    expect(block().state).toBe('sending');
    // Pressed again while it is on its way: one answer to a question.
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    expect(calls.map(call => call.body)).toEqual([{ question_id: 'q1', choice: 'deny', note: 'use pnpm' }]);
    expect(block()).toMatchObject({ state: 'answered', answer: '拒绝 · use pnpm', notice: '回答送到了，补的那句没送到。' });
    // The word that it does not wait any more changes nothing of a card that was answered here.
    asks.receive(frame(null, { question_id: 'q1' }));
    expect(block().state).toBe('answered');
  });

  it('takes no answer for another question than the one its card shows', async () => {
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q0', choice: 'always' });
    answerFromChat({ taskId: 'c7', questionId: 'q1', choice: 'always' });
    await flush();
    expect(calls).toEqual([]);
    expect(block().state).toBe('pending');
  });

  it('closes a card answered too late and shows what is asked now; a failed answer can be given again', async () => {
    asks.receive(frame(permission));
    reply = url => (url.includes('/answer')
      ? { status: 409, body: { detail: { code: 'not_taken' } } }
      : { status: 200, body: { questions: [{ task_id: 'c1', title: 'build the site', route: 'task_agent', question: confirmation }] } });
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    expect(block()).toMatchObject({ state: 'closed', notice: '没送到：这个问题已经答过了，或者它换了个新问题。' });
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q1', 'nekopower-ask-c1-q2']);

    reply = () => ({ status: 502, body: {} });
    messages = [messages[1]];
    answerFromChat({ taskId: 'c1', questionId: 'q2', choice: 'yes' });
    await flush();
    expect(block()).toMatchObject({ state: 'pending', notice: '没送到，再试一次吧。' });
  });

  it('reads what waits when the page connects: shows what it missed, closes what is over', async () => {
    asks.receive(frame(permission));
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    reply = () => ({ status: 200, body: { questions: [
      { task_id: 'c2', title: 'send it', route: 'task_agent', question: confirmation },
      { task_id: 'c3', title: 'look it up', route: 'task_agent', question: { kind: 'report', id: 'q5', question: 'Which file?' } },
    ] } });
    await asks.sync();
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([
      ['nekopower-ask-c1-q1', 'closed'], ['nekopower-ask-c2-q2', 'pending'], ['nekopower-ask-c3-q5', 'pending'],
    ]);
    // Could not be read: nothing is concluded from that.
    reply = () => ({ status: 502, body: {} });
    await asks.sync();
    expect(messages.map(message => message.blocks[0].state)).toEqual(['closed', 'pending', 'pending']);
  });

  it('keeps a question for a chat window that is not there yet', () => {
    vi.useFakeTimers();
    const w = window as unknown as Record<string, unknown>;
    const host = w.reactChatWindowHost;
    w.reactChatWindowHost = null;
    asks.receive(frame(permission));
    asks.receive(frame(confirmation));
    asks.receive(frame(permission, { task_id: 'c2' }));
    asks.receive(frame(null, { task_id: 'c2', question_id: 'q1' }));
    vi.advanceTimersByTime(600);
    expect(messages).toEqual([]);
    w.reactChatWindowHost = host;
    vi.advanceTimersByTime(600);
    // Only the latest of each task, and none of one that is over meanwhile.
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q2']);
  });
});
