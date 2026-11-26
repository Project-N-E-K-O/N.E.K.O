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
// Set to keep the page's requests from being answered until it is resolved.
let hold: Promise<void> | null;
const listening: Array<[string, EventListener]> = [];
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
  hold = null;
  (window as unknown as Record<string, unknown>).lanlan_config = { lanlan_name: 'Lan' };
  const w = window as unknown as Record<string, unknown>;
  w.reactChatWindowHost = {
    appendMessage: (message: Message) => { messages.push(message); },
    updateMessage: (id: string, patch: Partial<Message>) => { Object.assign(messages.find(each => each.id === id)!, patch); },
    removeMessage: (id: string) => { messages = messages.filter(each => each.id !== id); },
  };
  w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'token' }) };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: { body?: string; headers?: Record<string, string> }) => {
    calls.push({ url, body: options?.body ? JSON.parse(options.body) : null, headers: options?.headers ?? {} });
    if (hold) await hold;
    const answer = reply(url);
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body };
  }));
  delete w.NekoPowerAsk;
  // Each test its own copy: the script keeps which cards are open. What a copy listens for on the
  // window is taken off again after the test, or the copies of earlier tests would answer too.
  const listen = window.addEventListener.bind(window);
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, listener: EventListener, options?: unknown) => {
    listening.push([type, listener]);
    listen(type, listener, options as AddEventListenerOptions);
  });
  new Function(source)();
  asks = w.NekoPowerAsk as Asks;
});
afterEach(() => {
  vi.restoreAllMocks();
  listening.splice(0).forEach(([type, listener]) => window.removeEventListener(type, listener));
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

  it('opens what a line left out before a wider approval, and offers none where it cannot be shown', () => {
    // A request that names no command or path (handing work over), its title too long for the line.
    const handed = { kind: 'permission', id: 'q8', category: 'open_session', options: ['always', 'session', 'once', 'deny'],
      detail: { tool: 'hand_over', title: 'a very long title that the line cut' }, line: 'start another familiar: a very … cut', line_cut: true };
    const box = draw(handed);
    expect(shownChoices(box)).toEqual(['once', 'deny']);
    press(box.querySelector('[data-ask-whole]'));
    expect(box.querySelector('.task-ask-whole')?.textContent).toBe('a very long title that the line cut');
    expect(shownChoices(box)).toEqual(['always', 'session', 'once', 'deny']);
    // Cut, and nothing to show it whole by: only this once, or not, and it says where the rest is given.
    const bare = draw({ ...handed, id: 'q9', detail: {} });
    expect(shownChoices(bare)).toEqual(['once', 'deny']);
    expect(bare.querySelector('[data-ask-whole]')).toBeNull();
    expect(bare.textContent).toContain('请到喵能力界面里选');
  });

  it('asks whether the question still waits when the server gave up waiting for the answer to be taken', async () => {
    const rows = (questions: unknown[]) => (url: string) => (url.includes('/answer') ? { status: 504, body: {} } : { status: 200, body: { questions } });
    // It does not wait any more: over, by this answer or by something else (answered elsewhere
    // first, stopped). Not said to be answered, and not to be sent again.
    reply = rows([]);
    const taken = draw(permission);
    press(taken.querySelector('[data-choice="once"]'));
    await flush();
    expect(taken.querySelector('.task-ask-status')?.textContent).toBe('这个问题已经结束了；这次的回答有没有算上，不确定。');
    expect((taken.querySelector('[data-choice="once"]') as HTMLButtonElement).disabled).toBe(true);
    // The same question still waits: not taken, to be sent again.
    reply = rows([{ task_id: 'c1', question: permission }]);
    const waits = draw(permission);
    press(waits.querySelector('[data-choice="once"]'));
    await flush();
    expect(waits.querySelector('.task-ask-status')?.textContent).toBe('没送到，再试一次吧。');
    expect((waits.querySelector('[data-choice="once"]') as HTMLButtonElement).disabled).toBe(false);
    // Another question waits by now: this one is over just the same.
    reply = rows([{ task_id: 'c1', question: confirmation }]);
    const moved = draw(permission);
    press(moved.querySelector('[data-choice="once"]'));
    await flush();
    expect(moved.querySelector('.task-ask-status')?.textContent).toContain('不确定');
    expect(moved.querySelector('.task-ask-status')?.textContent).not.toContain('已回答');
  });

  it('goes on looking into it however long the server is away, less often after a while, and stops once the card is gone', async () => {
    vi.useFakeTimers();
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 502, body: {} });
    const box = draw(permission);
    const once = box.querySelector('[data-choice="once"]') as HTMLButtonElement;
    press(once);
    await vi.advanceTimersByTimeAsync(0);
    const reads = () => calls.filter(call => call.url === '/api/agent/questions').length;
    // The first looks, close together; none of them could read anything.
    await vi.advanceTimersByTimeAsync(asks.RECHECK_MS * asks.RECHECKS);
    const early = reads();
    expect(early).toBeGreaterThanOrEqual(asks.RECHECKS);
    expect(once.disabled).toBe(true);
    // Further apart from here on, and not given up.
    await vi.advanceTimersByTimeAsync(asks.RECHECK_SLOW_MS * 3);
    expect(reads() - early).toBe(3);
    // The server is back, the question still waits: the card can be answered again.
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c1', question: permission }] } });
    await vi.advanceTimersByTimeAsync(asks.RECHECK_SLOW_MS);
    expect(once.disabled).toBe(false);

    // A card that is gone from the page is not looked into any more.
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 502, body: {} });
    const gone = draw(confirmation);
    press(gone.querySelector('[data-confirm="yes"]'));
    await vi.advanceTimersByTimeAsync(0);
    gone.remove();
    const before = reads();
    await vi.advanceTimersByTimeAsync(asks.RECHECK_MS * 5);
    expect(reads()).toBe(before);
  });

  it('sends nothing more while it is not known whether an answer was taken, and looks into it until it is', async () => {
    vi.useFakeTimers();
    const tick = async () => { await vi.advanceTimersByTimeAsync(asks.RECHECK_MS); };
    // Neither the answer nor what waits can be read: not known, and not said to have failed.
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 502, body: {} });
    const unknown = draw(permission);
    const once = unknown.querySelector('[data-choice="once"]') as HTMLButtonElement;
    press(once);
    await vi.advanceTimersByTimeAsync(0);
    expect(unknown.querySelector('.task-ask-status')?.textContent).toBe('还不确定送到没有，过一会儿再看。');
    // Sent again now, it could be taken twice over.
    expect(once.disabled).toBe(true);
    press(once);
    await tick();
    expect(calls.filter(call => call.url.includes('/answer')).length).toBe(1);
    expect(once.disabled).toBe(true);
    // It can be read again, and the same question still waits: not taken, to be given again.
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c1', question: permission }] } });
    await tick();
    expect(unknown.querySelector('.task-ask-status')?.textContent).toBe('没送到，再试一次吧。');
    expect(once.disabled).toBe(false);

    // The same on a card of the chat, where it turns out the question is over.
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 502, body: {} });
    asks.receive({ type: 'nekopower_question', task_id: 'c5', title: 't', question: confirmation, question_id: '' });
    window.dispatchEvent(new CustomEvent('react-chat-window:action', {
      detail: { message: messages[0], action: { action: 'nekopower_ask_answer', payload: { taskId: 'c5', questionId: 'q2', choice: 'yes' } } },
    }));
    await vi.advanceTimersByTimeAsync(0);
    expect(block()).toMatchObject({ state: 'sending', notice: '还不确定送到没有，过一会儿再看。' });
    reply = () => ({ status: 200, body: { questions: [] } });
    await tick();
    expect(block()).toMatchObject({ state: 'closed', notice: '这个问题已经结束了；这次的回答有没有算上，不确定。' });
  });

  it('does not say an answer failed when the connection broke after it was sent', async () => {
    vi.useFakeTimers();
    // The request goes out and nothing comes back; what waits cannot be read either.
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push({ url, body: null, headers: {} });
      if (url.includes('/answer')) throw new TypeError('network');
      const answer = reply(url);
      return { ok: answer.status === 200, status: answer.status, json: async () => answer.body };
    }));
    reply = () => ({ status: 502, body: {} });
    const box = draw(permission);
    const once = box.querySelector('[data-choice="once"]') as HTMLButtonElement;
    press(once);
    await vi.advanceTimersByTimeAsync(0);
    expect(box.querySelector('.task-ask-status')?.textContent).toBe('还不确定送到没有，过一会儿再看。');
    expect(once.disabled).toBe(true);
    // Readable again, the same question still waits: it was not taken, and can be given again.
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c1', question: permission }] } });
    await vi.advanceTimersByTimeAsync(asks.RECHECK_MS);
    expect(box.querySelector('.task-ask-status')?.textContent).toBe('没送到，再试一次吧。');
    expect(once.disabled).toBe(false);
  });

  it('draws what an approval is asked for whole where nothing opens all of it, and keeps anything else to a few lines', () => {
    // A narrow HUD cuts three lines short sooner than the length of the line says, and the
    // wider approvals are offered at once here.
    const short = draw(permission).querySelector('.task-ask-line') as HTMLElement;
    expect(short.style.overflow).toBe('');
    // Cut short where all of it can be opened (the wider approvals wait for that), and what is no approval.
    const long = { ...permission, id: 'q8', detail: { command: 'x'.repeat(200) }, line: `run the command ${'x'.repeat(60)}…`, line_cut: true };
    expect((draw(long).querySelector('.task-ask-line') as HTMLElement).style.overflow).toBe('hidden');
    expect((draw(confirmation).querySelector('.task-ask-line') as HTMLElement).style.overflow).toBe('hidden');
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

  it('shows no card for a question of another character than the one the page is with', () => {
    // Sent to every page, the page of its own character not being open.
    asks.receive(frame(permission, { lanlan_name: 'Mio' }));
    expect(messages).toEqual([]);
    asks.receive(frame(permission, { lanlan_name: 'Lan' }));
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q1', 'nekopower-ask-c2-q2']);
    // That one is over is taken whoever it names.
    asks.receive(frame(null, { question_id: 'q1', lanlan_name: 'Mio' }));
    expect(block().state).toBe('closed');
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

  it('shows no card for a question that is over when its report arrives late', async () => {
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    asks.receive(frame(null, { task_id: 'c2', question_id: 'q2' }));
    // Closed before its report was shown at all.
    asks.receive(frame(null, { task_id: 'c3', question_id: 'q7' }));
    // The reports were on their way as the answers came, and arrive now.
    asks.receive(frame(permission));
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    asks.receive(frame({ ...permission, id: 'q7' }, { task_id: 'c3' }));
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([
      ['nekopower-ask-c1-q1', 'answered'], ['nekopower-ask-c2-q2', 'closed'],
    ]);
    // The next question of those tasks is shown as ever.
    asks.receive(frame({ ...permission, id: 'q8' }, { task_id: 'c3' }));
    expect(messages.length).toBe(3);
  });

  it('closes a card whose question is over after the server gave up waiting, without saying it was answered', async () => {
    asks.receive(frame(permission));
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 200, body: { questions: [] } });
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    expect(block()).toMatchObject({ state: 'closed', notice: '这个问题已经结束了；这次的回答有没有算上，不确定。' });
    expect(block().answer).toBeUndefined();
  });

  it('keeps the next question that waits for the chat window when a late report of the last one comes', () => {
    vi.useFakeTimers();
    const w = window as unknown as Record<string, unknown>;
    const host = w.reactChatWindowHost;
    w.reactChatWindowHost = null;
    // Over before the window was there; then the next question; then the report of the first, late.
    asks.receive(frame(null, { question_id: 'q1' }));
    asks.receive(frame(confirmation));
    asks.receive(frame(permission));
    w.reactChatWindowHost = host;
    vi.advanceTimersByTime(600);
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q2']);
  });

  it('does not open a card again that was closed while its answer was on its way', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    reply = () => ({ status: 502, body: {} });
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    asks.receive(frame(null, { question_id: 'q1' }));
    hold = null;
    release();
    await flush();
    // The answer failed, but there is nothing left to answer: the card stays closed. It was plainly
    // refused, so nothing is said of it not being known whether it counted.
    expect(block().state).toBe('closed');
    expect(block().notice || '').toBe('');

    // Taken, and the word that it is over came before the word that it was taken: answered, not just over.
    hold = new Promise(resolve => { release = resolve; });
    reply = () => ({ status: 200, body: { success: true } });
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    window.dispatchEvent(new CustomEvent('react-chat-window:action', {
      detail: { message: messages[1], action: { action: 'nekopower_ask_answer', payload: { taskId: 'c2', questionId: 'q2', choice: 'yes' } } },
    }));
    await flush();
    asks.receive(frame(null, { task_id: 'c2', question_id: 'q2' }));
    expect(block(1).state).toBe('closed');
    hold = null;
    release();
    await flush();
    expect(block(1).state).toBe('answered');
  });

  it('keeps the card of the next question answerable when it comes before the last answer is back', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    // Approved, and the task asks the next thing at once, before the page hears its answer was taken.
    asks.receive(frame(confirmation));
    hold = null;
    release();
    await flush();
    expect(messages.map(message => message.blocks[0].state)).toEqual(['answered', 'pending']);
    messages = [messages[1]];
    answerFromChat({ taskId: 'c1', questionId: 'q2', choice: 'yes' });
    await flush();
    expect(calls.map(call => call.body)).toEqual([{ question_id: 'q1', choice: 'once' }, { question_id: 'q2', choice: 'yes' }]);
    expect(block().state).toBe('answered');
  });

  it('shows the questions again after the chat was emptied, and only those of the character it is with', async () => {
    const rows = [
      { task_id: 'c1', title: 'build the site', route: 'task_agent', lanlan_name: 'Lan', question: permission },
      { task_id: 'c8', title: 'for another', route: 'task_agent', lanlan_name: 'Mio', question: confirmation },
    ];
    reply = () => ({ status: 200, body: { questions: rows } });
    asks.receive(frame(permission));
    // The character was switched and switched back: the chat was emptied, its cards with it.
    messages = [];
    window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared'));
    await flush();
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q1']);
    // With the other character, hers and not this one's.
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Mio';
    messages = [];
    window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared'));
    await flush();
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c8-q2']);
  });

  it('takes the last character\'s cards out again where the chat was emptied before the page knew whom it is with', async () => {
    const rows = [
      { task_id: 'c1', title: 'build the site', route: 'task_agent', lanlan_name: 'Lan', question: permission },
      { task_id: 'c8', title: 'for another', route: 'task_agent', lanlan_name: 'Mio', question: confirmation },
    ];
    reply = () => ({ status: 200, body: { questions: rows } });
    asks.receive(frame(permission, { lanlan_name: 'Lan' }));
    // As the page is turned to another character: the chat is emptied first, and what waits is
    // read while the page is still with the last one. Her question is back in the emptied chat.
    messages = [];
    window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared'));
    await flush();
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q1']);
    // Then the page is with the new one, and reads again as its connection opens.
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Mio';
    await asks.sync();
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c8-q2']);
    // Not over for that: turned back, the first one's question is shown again.
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Lan';
    await asks.sync();
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([['nekopower-ask-c1-q1', 'pending']]);

    // The same of one the connection tells of meanwhile, and of one that waited for the window.
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Mio';
    asks.receive(frame(permission, { lanlan_name: 'Lan' }));
    expect(messages).toEqual([]);
  });

  it('takes such a card out as well where its question is over by the time the page reads again', async () => {
    reply = () => ({ status: 200, body: { questions: [] } });
    asks.receive(frame(permission, { lanlan_name: 'Lan' }));
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Mio';
    await asks.sync();
    // Not left in the other one's chat as a question of hers that is over.
    expect(messages).toEqual([]);
  });

  it('asks for the page\'s token anew, once, where an answer was turned away for it', async () => {
    const w = window as unknown as Record<string, unknown>;
    let token = '';
    let asked = 0;
    w.nekoLocalMutationSecurity = {
      getMutationHeaders: async () => (token ? { 'X-CSRF-Token': token } : {}),
      refreshToken: async () => { asked += 1; token = 'fresh'; },
    };
    // The first read of it failed: the answer goes out without one and is turned away.
    reply = () => (calls[calls.length - 1].headers['X-CSRF-Token'] === 'fresh'
      ? { status: 200, body: { success: true } }
      : { status: 403, body: { success: false, error_code: 'csrf_validation_failed' } });
    expect(await asks.answer('c1', 'q1', { choice: 'once' })).toEqual({ ok: true, stale: false, noteLost: false });
    expect([asked, calls.length]).toEqual([1, 2]);
    // Turned away again with the new one: that is the answer, it is not sent a third time.
    token = '';
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({}), refreshToken: async () => { asked += 1; } };
    expect((await asks.answer('c1', 'q1', { choice: 'once' })).ok).toBe(false);
    expect([asked, calls.length]).toEqual([2, 4]);
    // Any other refusal is not sent again at all.
    reply = () => ({ status: 403, body: { detail: 'forbidden' } });
    expect((await asks.answer('c1', 'q1', { choice: 'once' })).ok).toBe(false);
    expect([asked, calls.length]).toEqual([2, 5]);
  });

  it('does not go by a read that is older than what the connection said meanwhile', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    asks.receive(frame(permission, { task_id: 'c2' }));
    // What the read will say: c1 asks nothing, c2 still asks. Both changed while it was on its way.
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c2', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: permission }] } });
    const reading = asks.sync();
    asks.receive(frame(confirmation));
    asks.receive(frame(null, { task_id: 'c2', question_id: 'q1' }));
    hold = null;
    release();
    await reading;
    await flush();
    // The new question is not closed for being missing from the older read, nor the closed one shown again.
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([
      ['nekopower-ask-c2-q1', 'closed'], ['nekopower-ask-c1-q2', 'pending'],
    ]);
  });

  it('still shows what a task asks now where all the connection said meanwhile is that an earlier question of it is over', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    // What the read will say: c1 asks q2 by now, c5 asks q7 (both put while the page was away).
    reply = () => ({ status: 200, body: { questions: [
      { task_id: 'c1', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: confirmation },
      { task_id: 'c5', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: { ...permission, id: 'q7' } },
    ] } });
    const reading = asks.sync();
    // Of c1: that q1 is over, which says nothing of q2. Of c5: that what it asked is over, whichever it was.
    asks.receive(frame(null, { question_id: 'q1' }));
    asks.receive(frame(null, { task_id: 'c5' }));
    hold = null;
    release();
    await reading;
    await flush();
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q2']);
  });

  it('keeps the question a task asks now where a late word that an earlier one is over follows it', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    // What the read will say is older than both: c1 asks q2.
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c1', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: confirmation }] } });
    const reading = asks.sync();
    // By the connection: it asks q3 now; and then, late, that q1 is over.
    asks.receive(frame({ ...permission, id: 'q3' }));
    asks.receive(frame(null, { question_id: 'q1' }));
    hold = null;
    release();
    await reading;
    await flush();
    // What it asks now stays, and is not given up for what the older read says.
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([['nekopower-ask-c1-q3', 'pending']]);
  });

  it('does not go on saying it will look again once a card that was being looked into is closed', async () => {
    vi.useFakeTimers();
    asks.receive(frame(permission));
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    // The server gave up waiting, and what waits cannot be read: looked into.
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 502, body: {} });
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await vi.advanceTimersByTimeAsync(0);
    expect(block().notice).toBe('还不确定送到没有，过一会儿再看。');
    asks.receive(frame(null, { question_id: 'q1' }));
    expect([block().state, block().notice]).toEqual(['closed', '这个问题已经结束了；这次的回答有没有算上，不确定。']);
    // One that was not: closed, with nothing more said.
    asks.receive(frame(null, { task_id: 'c2', question_id: 'q2' }));
    expect([block(1).state, block(1).notice || '']).toEqual(['closed', '']);
  });

  it('keeps a card whose answer is on its way through the chat being emptied, and does not offer its question anew', async () => {
    let back = () => {};
    (window as unknown as Record<string, { getState?: () => unknown }>).reactChatWindowHost.getState = () => ({ messages });
    const rows = [{ task_id: 'c1', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: permission }];
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      calls.push({ url, body: null, headers: {} });
      if (url.includes('/answer')) {
        return new Promise(resolve => { back = () => resolve({ ok: true, status: 200, json: async () => ({ success: true }) }); });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ questions: rows }) });
    }));
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    expect(block().state).toBe('sending');
    // The chat is emptied; read anew, the question still waits (the answer has not got there yet).
    messages = [];
    window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared'));
    await flush();
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([['nekopower-ask-c1-q1', 'sending']]);
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'deny' });
    await flush();
    expect(calls.filter(call => call.url.includes('/answer')).length).toBe(1);
    // What comes of the answer is said on the card that was put back.
    back();
    await flush();
    expect(block().state).toBe('answered');
  });

  it('reads once more when it was asked to while a read was on its way, or the character changed meanwhile', async () => {
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    reply = () => ({ status: 200, body: { questions: [] } });
    const reading = asks.sync();
    void asks.sync();
    hold = null;
    release();
    await reading;
    await flush();
    expect(calls.filter(call => call.url === '/api/agent/questions').length).toBe(2);
    // A read that began with one character and ends with another shows nothing of it, and is made again.
    calls = [];
    hold = new Promise(resolve => { release = resolve; });
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c1', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: permission }] } });
    const stale = asks.sync();
    (window as unknown as Record<string, { lanlan_name: string }>).lanlan_config.lanlan_name = 'Mio';
    hold = null;
    release();
    await stale;
    await flush();
    expect(calls.length).toBe(2);
    expect(messages).toEqual([]);
  });

  it('puts a card back that was pushed out of the chat while its question still waits', async () => {
    const w = window as unknown as Record<string, { getState?: () => unknown }>;
    w.reactChatWindowHost.getState = () => ({ messages });
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    // The chat keeps its latest messages only: many came since, and these two are gone from it.
    messages = [];
    reply = () => ({ status: 200, body: { questions: [{ task_id: 'c2', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: confirmation }] } });
    await asks.sync();
    // The one that still waits is back, as it stood; the one that was answered is not.
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([['nekopower-ask-c2-q2', 'pending']]);
    // Still there: not put in a second time.
    await asks.sync();
    asks.receive(frame(confirmation, { task_id: 'c2' }));
    expect(messages.length).toBe(1);
  });

  it('says on a card closed while its answer was on its way what is not known of that answer', async () => {
    const w = window as unknown as Record<string, { getState?: () => unknown }>;
    w.reactChatWindowHost.getState = () => ({ messages });
    let release = () => {};
    hold = new Promise(resolve => { release = resolve; });
    // The server gives up waiting; the question waits no more by the time that is looked into.
    reply = url => (url.includes('/answer') ? { status: 504, body: {} } : { status: 200, body: { questions: [] } });
    asks.receive(frame(permission));
    answerFromChat({ taskId: 'c1', questionId: 'q1', choice: 'once' });
    await flush();
    asks.receive(frame(null, { question_id: 'q1' }));
    asks.receive(frame(confirmation));
    hold = null;
    release();
    await flush();
    // Closed as it was, with what is not known said on it; the next question is as it was.
    expect(messages.map(message => [message.blocks[0].state, message.blocks[0].notice || ''])).toEqual([
      ['closed', '这个问题已经结束了；这次的回答有没有算上，不确定。'], ['pending', ''],
    ]);

    // A chat that was emptied meanwhile is not written to.
    hold = new Promise(resolve => { release = resolve; });
    answerFromChat({ taskId: 'c1', questionId: 'q2', choice: 'yes' });
    await flush();
    asks.receive(frame(null, { question_id: 'q2' }));
    const kept = messages;
    messages = [];
    hold = null;
    release();
    await flush();
    expect(messages).toEqual([]);
    expect(kept[1].blocks[0].notice || '').toBe('');
  });

  it('looks once at a time for the chat window, however many questions wait for it', () => {
    vi.useFakeTimers();
    const w = window as unknown as Record<string, unknown>;
    const host = w.reactChatWindowHost;
    w.reactChatWindowHost = null;
    for (let index = 0; index < 20; index += 1) asks.receive(frame({ ...permission, id: `q${index}` }, { task_id: `t${index}` }));
    // The window takes five seconds to be there: each question looking on its own would have used up the looks by then.
    vi.advanceTimersByTime(5000);
    expect(messages).toEqual([]);
    w.reactChatWindowHost = host;
    vi.advanceTimersByTime(600);
    expect(messages.length).toBe(20);
  });

  it('does not show what waited for the chat window once the page is with another character', () => {
    vi.useFakeTimers();
    const w = window as unknown as Record<string, unknown>;
    const host = w.reactChatWindowHost;
    w.reactChatWindowHost = null;
    asks.receive(frame(permission, { lanlan_name: 'Lan' }));
    asks.receive(frame(confirmation, { task_id: 'c2', lanlan_name: 'Lan' }));
    (w.lanlan_config as { lanlan_name: string }).lanlan_name = 'Mio';
    // Hers comes while the window is still not there: shown, the other one's are not.
    asks.receive(frame({ ...permission, id: 'q7' }, { task_id: 'c3', lanlan_name: 'Mio' }));
    w.reactChatWindowHost = host;
    vi.advanceTimersByTime(600);
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c3-q7']);
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

  it('lets go of what waited for the chat window once something newer is shown or it is over', async () => {
    vi.useFakeTimers();
    const w = window as unknown as Record<string, unknown>;
    const host = w.reactChatWindowHost;
    w.reactChatWindowHost = null;
    asks.receive(frame(permission));
    // The window is there, and the next question of the task comes before what waited is looked at.
    w.reactChatWindowHost = host;
    asks.receive(frame(confirmation));
    vi.advanceTimersByTime(600);
    // Not the older question over the newer one.
    expect(messages.map(message => [message.id, message.blocks[0].state])).toEqual([['nekopower-ask-c1-q2', 'pending']]);

    // One that waits for the window, and is over by the time what waits now is read.
    w.reactChatWindowHost = null;
    asks.receive(frame(permission, { task_id: 'c2' }));
    asks.receive(frame({ ...permission, id: 'q6' }, { task_id: 'c3' }));
    reply = () => ({ status: 200, body: { questions: [
      { task_id: 'c1', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: confirmation },
      { task_id: 'c3', title: 't', route: 'task_agent', lanlan_name: 'Lan', question: { ...permission, id: 'q6' } },
    ] } });
    // Told of before this read began, so the read is the newer word on them.
    await asks.sync();
    w.reactChatWindowHost = host;
    vi.advanceTimersByTime(600);
    // c2 asks nothing any more and is not shown; c3 still asks, and is.
    expect(messages.map(message => message.id)).toEqual(['nekopower-ask-c1-q2', 'nekopower-ask-c3-q6']);
  });
});
