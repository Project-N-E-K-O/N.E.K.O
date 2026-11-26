import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatScreenVisibilityProvider, useChatScreenVisibility } from './ChatScreenVisibility';
import CompactNekoPowerAskPrompt, {
  CompactAskOrRecommendationPrompt, NEKOPOWER_ASK_NOTICE_MS, pendingNekoPowerAsk,
} from './CompactNekoPowerAskPrompt';
import MessageBlockView from './MessageBlockView';
import { NEKOPOWER_ASK_SETTLE_MS, askView, useNekoPowerAskDrafts } from './NekoPowerAskBlock';
import { parseChatMessage } from './message-schema';
import type { ChatMessage, MessageAction } from './message-schema';

const LONG = `cd D:/blog && npm run build -- ${'--flag '.repeat(30)}--last`;
const permission = { kind: 'permission', id: 'q1', category: 'run_command', options: ['always', 'session', 'once', 'deny'],
  detail: { command: 'npm test' }, line: 'run the command npm test', line_cut: false };
const confirmation = { kind: 'confirmation', id: 'q2', question: 'Send it to the group?' };
const oneQuestion = { kind: 'questions', id: 'q3',
  questions: [{ id: 'theme', question: 'Which theme?', options: [{ label: 'Light', description: 'matches the logo' }, { label: 'Dark' }] }] };
const twoQuestions = { kind: 'questions', id: 'q4', questions: [
  { id: 'same', question: 'Deploy where?', options: ['Vercel', 'Not now'] },
  { id: 'same', question: 'Which tags?', options: ['a', 'b', 'Not now'], multi_select: true },
] };
const inWords = { kind: 'report', id: 'q5', question: 'Which file do I keep?', details: 'three have that name' };

function ask(question: Record<string, unknown>, over: Record<string, unknown> = {}, taskId = 'c1'): ChatMessage {
  return parseChatMessage({
    id: `nekopower-ask-${taskId}-${String(question.id)}`, role: 'system', author: 'NekoPower', time: '12:00',
    blocks: [{ type: 'nekopower_ask', taskId, questionId: String(question.id), title: 'build the site', question, state: 'pending', ...over }],
  });
}
function show(message: ChatMessage, presentation: 'card' | 'bubble' = 'card') {
  const given: Array<Record<string, unknown> | undefined> = [];
  const onAction = (_: ChatMessage, action: MessageAction) => {
    expect(action.action).toBe('nekopower_ask_answer');
    given.push(action.payload);
  };
  const view = render(<MessageBlockView block={message.blocks[0]} message={message} onAction={onAction} presentation={presentation} />);
  return { ...view, given, at: (selector: string) => view.container.querySelector(selector) as HTMLElement };
}

afterEach(() => { vi.useRealTimers(); });

describe('what a NekoPower commission asks, as a card of the chat', () => {
  it('reads the kinds of question, and when all of what it would do has to be opened first', () => {
    expect([permission, confirmation, oneQuestion, inWords].map(question => askView(question).shape))
      .toEqual(['permission', 'confirmation', 'questions', 'words']);
    expect(askView(permission).needsWhole).toBe(false);
    // Too long to read at a glance, more than one line, or cut in the line that says it.
    expect(askView({ ...permission, detail: { command: LONG } }).needsWhole).toBe(true);
    expect(askView({ ...permission, detail: { command: 'a\nb' } }).needsWhole).toBe(true);
    expect(askView({ ...permission, line_cut: true }).needsWhole).toBe(true);
    // By where each question stands: the model gave both the same id.
    expect(askView(twoQuestions).questions.map(item => [item.id, item.multi])).toEqual([['0', false], ['1', true]]);
    expect(askView(inWords).line).toBe('Which file do I keep? three have that name');
  });

  it('approves with a button, taking a sentence along and never the sentence alone', () => {
    const { given, at } = show(ask(permission));
    expect(at('.nekopower-ask-line').textContent).toBe('run the command npm test');
    expect([...at('.nekopower-ask').querySelectorAll('[data-choice]')].map(node => node.getAttribute('data-choice')))
      .toEqual(['always', 'session', 'once', 'deny']);
    fireEvent.change(at('[data-ask-input]'), { target: { value: 'use pnpm' } });
    // Words alone would be read as a refusal: there is no button for them, and Enter sends nothing.
    expect(at('[data-ask-send]')).toBeNull();
    fireEvent.submit(at('.nekopower-ask-say'));
    expect(given).toEqual([]);
    fireEvent.click(at('[data-choice="deny"]'));
    expect(given).toEqual([{ taskId: 'c1', questionId: 'q1', choice: 'deny', note: 'use pnpm' }]);
  });

  it('offers an approval that holds for more than this once only with all of the command in view', () => {
    const { at, given } = show(ask({ ...permission, detail: { command: LONG }, line: 'run the command cd D:/blog … --flag', line_cut: true }));
    const choices = () => [...at('.nekopower-ask').querySelectorAll('[data-choice]')].map(node => node.getAttribute('data-choice'));
    expect(choices()).toEqual(['once', 'deny']);
    expect(at('.nekopower-ask-whole')).toBeNull();
    expect(at('.nekopower-ask-hint')).toBeTruthy();
    fireEvent.click(at('[data-ask-whole]'));
    expect(at('.nekopower-ask-whole').textContent).toBe(LONG);
    expect(choices()).toEqual(['always', 'session', 'once', 'deny']);
    expect(at('.nekopower-ask-hint')).toBeNull();
    fireEvent.click(at('[data-choice="always"]'));
    expect(given[0]).toMatchObject({ choice: 'always' });
  });

  it('asks for the command to be opened above the composer, where its line is clamped, though it is whole on the card', () => {
    const uncut = { ...permission, detail: { command: LONG }, line: `run the command ${LONG}`, line_cut: false };
    const choices = (view: ReturnType<typeof show>) => [...view.container.querySelectorAll('[data-choice]')].map(node => node.getAttribute('data-choice'));
    const card = show(ask(uncut));
    expect(choices(card)).toEqual(['always', 'session', 'once', 'deny']);
    expect(card.at('[data-ask-whole]')).toBeNull();
    card.unmount();
    const bubble = show(ask(uncut), 'bubble');
    expect(choices(bubble)).toEqual(['once', 'deny']);
    fireEvent.click(bubble.at('[data-ask-whole]'));
    expect(choices(bubble)).toEqual(['always', 'session', 'once', 'deny']);
  });

  it('opens what a line left out of a request that names no command, and offers no wider approval where it cannot', () => {
    const handed = { kind: 'permission', id: 'q8', category: 'open_session', options: ['always', 'session', 'once', 'deny'],
      detail: { tool: 'hand_over', title: 'a very long title that the line cut' }, line: 'start another familiar: a very … cut', line_cut: true };
    const choices = (view: ReturnType<typeof show>) => [...view.container.querySelectorAll('[data-choice]')].map(node => node.getAttribute('data-choice'));
    const card = show(ask(handed));
    expect(choices(card)).toEqual(['once', 'deny']);
    fireEvent.click(card.at('[data-ask-whole]'));
    expect(card.at('.nekopower-ask-whole').textContent).toBe('a very long title that the line cut');
    expect(choices(card)).toEqual(['always', 'session', 'once', 'deny']);
    card.unmount();
    const bare = show(ask({ ...handed, detail: {} }));
    expect(choices(bare)).toEqual(['once', 'deny']);
    expect(bare.at('[data-ask-whole]')).toBeNull();
    expect(bare.at('.nekopower-ask-hint').textContent).toContain('NekoPower window');
    bare.unmount();
    // Above the composer as well, where the line is clamped besides.
    const floating = show(ask({ ...handed, detail: {} }), 'bubble');
    expect(choices(floating)).toEqual(['once', 'deny']);
    expect(floating.at('[data-ask-whole]')).toBeNull();
  });

  it('leaves out the line for a sentence where nothing could take one', () => {
    const { at } = show(ask(permission, { noNote: true }));
    expect(at('[data-ask-input]')).toBeNull();
    expect(at('[data-choice="once"]')).toBeTruthy();
  });

  it('answers a plugin that wants confirmation with yes or no, or in words', () => {
    const { given, at } = show(ask(confirmation));
    fireEvent.click(at('[data-confirm="yes"]'));
    fireEvent.change(at('[data-ask-input]'), { target: { value: 'only the first two' } });
    fireEvent.click(at('[data-confirm="no"]'));
    fireEvent.click(at('[data-ask-send]'));
    expect(given.map(each => [each?.choice, each?.text])).toEqual([['yes', ''], ['no', 'only the first two'], [undefined, 'only the first two']]);
  });

  it('answers one question that offers one pick by pressing it, and several by picking then sending', () => {
    const quick = show(ask(oneQuestion));
    expect(quick.at('[data-option="Light"]').textContent).toContain('matches the logo');
    fireEvent.click(quick.at('[data-option="Dark"]'));
    expect(quick.given).toEqual([{ taskId: 'c1', questionId: 'q3', selected: { 0: ['Dark'] }, text: '' }]);
    quick.unmount();

    const { given, at } = show(ask(twoQuestions));
    const option = (question: string, label: string) => at(`[data-question="${question}"] [data-option="${label}"]`);
    expect((at('[data-ask-send]') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(option('0', 'Vercel'));
    fireEvent.click(option('0', 'Not now'));
    fireEvent.click(option('1', 'a'));
    fireEvent.click(option('1', 'Not now'));
    fireEvent.click(option('1', 'a'));
    expect(given).toEqual([]);
    expect(option('0', 'Not now').getAttribute('aria-pressed')).toBe('true');
    expect(option('0', 'Vercel').getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(at('[data-ask-send]'));
    expect(given).toEqual([{ taskId: 'c1', questionId: 'q4', selected: { 0: ['Not now'], 1: ['Not now'] }, text: '' }]);
  });

  it('shows what is asked where the questions offer nothing to pick', () => {
    const plain = { kind: 'questions', id: 'q6', questions: [{ id: 'a', question: 'Which folder holds the photos?' }, { id: 'b', question: 'And the videos?' }] };
    const { at, given, container } = show(ask(plain));
    expect([...container.querySelectorAll('.nekopower-ask-line')].map(line => line.textContent))
      .toEqual(['1. Which folder holds the photos?', '2. And the videos?']);
    expect(at('[data-option]')).toBeNull();
    fireEvent.change(at('[data-ask-input]'), { target: { value: 'D:/Camera for both' } });
    fireEvent.click(at('[data-ask-send]'));
    expect(given).toEqual([{ taskId: 'c1', questionId: 'q6', text: 'D:/Camera for both' }]);
  });

  it('says how the answer fared, and takes no more once it is given or the question is over', () => {
    const sending = show(ask(permission, { state: 'sending' }));
    expect((sending.at('[data-choice="once"]') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(sending.at('[data-choice="once"]'));
    expect(sending.given).toEqual([]);
    sending.unmount();

    const answered = show(ask(permission, { state: 'answered', answer: 'Just this once', notice: 'The sentence you added was not delivered.' }));
    expect(answered.at('[data-choice]')).toBeNull();
    expect(answered.at('[data-ask-input]')).toBeNull();
    expect(answered.at('.nekopower-ask-result').textContent).toBe('Answered: Just this once');
    expect(answered.at('.nekopower-ask-notice').textContent).toBe('The sentence you added was not delivered.');
    answered.unmount();

    const closed = show(ask(oneQuestion, { state: 'closed' }));
    expect(closed.at('[data-option]')).toBeNull();
    expect(closed.at('.nekopower-ask-result').textContent).toBe('This question is over');
    closed.unmount();
    // Why it is over is said in its place, where there is something to say.
    const late = show(ask(oneQuestion, { state: 'closed', notice: 'Not delivered' }));
    expect(late.at('.nekopower-ask-result')).toBeNull();
    expect(late.at('.nekopower-ask-notice').textContent).toBe('Not delivered');
  });

  it('is not answered from a page that only shows it', () => {
    const message = ask(confirmation);
    const pressed = vi.fn();
    const view = render(<MessageBlockView block={message.blocks[0]} message={message} onAction={pressed} interactive={false} />);
    fireEvent.click(view.container.querySelector('[data-confirm="yes"]')!);
    expect(pressed).not.toHaveBeenCalled();
  });
});

describe('the question above the composer of the compact chat', () => {
  const other = parseChatMessage({ id: 'm1', role: 'assistant', author: 'Neko', time: '12:00', blocks: [{ type: 'text', text: 'hello' }] });

  it('is the latest one still to be answered', () => {
    const first = ask(permission);
    const second = ask(confirmation, {}, 'c2');
    expect(pendingNekoPowerAsk([other])).toBeNull();
    expect(pendingNekoPowerAsk([first, other, second])?.message.id).toBe(second.id);
    expect(pendingNekoPowerAsk([first, ask(confirmation, { state: 'answered' }, 'c2'), other])?.message.id).toBe(first.id);
    expect(pendingNekoPowerAsk([ask(permission, { state: 'closed' })])).toBeNull();
    // One whose answer is on its way stays where it is until that is in.
    expect(pendingNekoPowerAsk([ask(permission, { state: 'sending' })])?.block.state).toBe('sending');
  });

  it('holds the buttons of the next question back as well when there was a moment with none in between', () => {
    vi.useFakeTimers();
    const within = (messages: ChatMessage[]) => (
      <ChatScreenVisibilityProvider messages={messages}>
        <CompactAskOrRecommendationPrompt messages={messages} />
      </ChatScreenVisibilityProvider>
    );
    const view = render(within([ask(permission)]));
    const once = () => view.container.querySelector('[data-choice="once"]') as HTMLButtonElement;
    expect(once().disabled).toBe(false);
    // Answered: nothing floats. Then the task asks the next thing.
    view.rerender(within([ask(permission, { state: 'answered' })]));
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeNull();
    view.rerender(within([ask(permission, { state: 'answered' }), ask({ ...permission, id: 'q9' })]));
    expect(once().disabled).toBe(true);
    act(() => { vi.advanceTimersByTime(NEKOPOWER_ASK_SETTLE_MS); });
    expect(once().disabled).toBe(false);
  });

  it('keeps what there is to say of an answer in view for a while, where there is no list of messages to read it in', () => {
    vi.useFakeTimers();
    const within = (messages: ChatMessage[]) => (
      <ChatScreenVisibilityProvider messages={messages}>
        <CompactAskOrRecommendationPrompt messages={messages} />
      </ChatScreenVisibilityProvider>
    );
    const floating = () => view.container.querySelector('#compact-nekopower-ask-prompt');
    const view = render(within([ask(permission)]));
    // Taken, but the sentence added to it was not: said, with nothing left to press.
    view.rerender(within([ask(permission, { state: 'answered', answer: 'Just this once', notice: 'The sentence you added was not delivered.' })]));
    expect(floating()?.textContent).toContain('The sentence you added was not delivered.');
    expect(floating()?.querySelector('[data-choice]')).toBeNull();
    act(() => { vi.advanceTimersByTime(NEKOPOWER_ASK_NOTICE_MS - 100); });
    expect(floating()).toBeTruthy();
    act(() => { vi.advanceTimersByTime(200); });
    expect(floating()).toBeNull();

    // Too late: said as well. The next question takes its place at once.
    view.rerender(within([ask({ ...permission, id: 'q7' })]));
    view.rerender(within([ask({ ...permission, id: 'q7' }, { state: 'closed', notice: 'Not delivered' })]));
    expect(floating()?.textContent).toContain('Not delivered');
    view.rerender(within([ask({ ...permission, id: 'q7' }, { state: 'closed', notice: 'Not delivered' }), ask(confirmation)]));
    expect(floating()?.textContent).not.toContain('Not delivered');
    expect(floating()?.querySelector('[data-confirm="yes"]')).toBeTruthy();

    // Answered with nothing to say of it: gone at once, as before.
    view.rerender(within([ask(confirmation, { state: 'answered', answer: 'Yes' })]));
    expect(floating()).toBeNull();
  });

  it('keeps what was typed and picked for a question that gives way to another and comes back', () => {
    const within = (messages: ChatMessage[]) => (
      <ChatScreenVisibilityProvider messages={messages}>
        <CompactAskOrRecommendationPrompt messages={messages} />
      </ChatScreenVisibilityProvider>
    );
    const first = ask(twoQuestions, {}, 'd1');
    const view = render(within([first]));
    const at = (selector: string) => view.container.querySelector(selector) as HTMLElement;
    fireEvent.click(at('[data-question="1"] [data-option="b"]'));
    fireEvent.change(at('[data-ask-input]'), { target: { value: 'half a sentence' } });
    // Another task asks: its question floats instead, with nothing of the first one's in it.
    view.rerender(within([first, ask(inWords, {}, 'd2')]));
    expect((at('[data-ask-input]') as HTMLInputElement).value).toBe('');
    // Answered: the first one is back, as it was left.
    view.rerender(within([first, ask(inWords, { state: 'answered', answer: 'x' }, 'd2')]));
    expect((at('[data-ask-input]') as HTMLInputElement).value).toBe('half a sentence');
    expect(at('[data-question="1"] [data-option="b"]').getAttribute('aria-pressed')).toBe('true');
    // Sent, and another one's floats before the answer is back. The answer is not taken (it
    // failed), and the question waits again: with what was put into it.
    view.rerender(within([ask(twoQuestions, { state: 'sending' }, 'd1')]));
    view.rerender(within([ask(twoQuestions, { state: 'sending' }, 'd1'), ask(inWords, {}, 'd3')]));
    view.rerender(within([first, ask(inWords, { state: 'answered', answer: 'x' }, 'd3')]));
    expect((at('[data-ask-input]') as HTMLInputElement).value).toBe('half a sentence');
    expect(at('[data-question="1"] [data-option="b"]').getAttribute('aria-pressed')).toBe('true');
    // Once it is over (its card among the messages says so), nothing of it is kept for a card that is drawn again.
    view.rerender(within([]));
    show(ask(twoQuestions, { state: 'closed' }, 'd1')).unmount();
    view.rerender(within([first]));
    expect((at('[data-ask-input]') as HTMLInputElement).value).toBe('');
    expect(at('[data-question="1"] [data-option="b"]').getAttribute('aria-pressed')).toBe('false');
  });

  it('lets go of what was typed for a question that is over without its card being drawn so, or whose chat was emptied', () => {
    // The chat, which holds the messages: it says which questions are left.
    function Chat({ messages }: { messages: ChatMessage[] }) {
      useNekoPowerAskDrafts(messages);
      return (
        <ChatScreenVisibilityProvider messages={messages}>
          <CompactAskOrRecommendationPrompt messages={messages} />
        </ChatScreenVisibilityProvider>
      );
    }
    const first = ask(twoQuestions, {}, 'e1');
    const view = render(<Chat messages={[first]} />);
    const input = () => view.container.querySelector('[data-ask-input]') as HTMLInputElement;
    const type = (value: string) => fireEvent.change(input(), { target: { value } });
    // While it waits, giving way to another one and coming back keeps what was put into it.
    type('half a sentence');
    view.rerender(<Chat messages={[first, ask(inWords, {}, 'e2')]} />);
    view.rerender(<Chat messages={[first, ask(inWords, { state: 'answered', answer: 'x' }, 'e2')]} />);
    expect(input().value).toBe('half a sentence');
    // The same while its answer is on its way, which then is not taken.
    view.rerender(<Chat messages={[ask(twoQuestions, { state: 'sending' }, 'e1')]} />);
    view.rerender(<Chat messages={[ask(twoQuestions, { state: 'sending' }, 'e1'), ask(inWords, {}, 'e3')]} />);
    view.rerender(<Chat messages={[first, ask(inWords, { state: 'answered', answer: 'x' }, 'e3')]} />);
    expect(input().value).toBe('half a sentence');
    // Sent and taken: above the composer its card is taken away without being drawn as answered.
    view.rerender(<Chat messages={[ask(twoQuestions, { state: 'sending' }, 'e1')]} />);
    view.rerender(<Chat messages={[ask(twoQuestions, { state: 'answered', answer: 'x' }, 'e1')]} />);
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeNull();
    view.rerender(<Chat messages={[first]} />);
    expect(input().value).toBe('');
    type('half a sentence');
    // Over (taken back, answered elsewhere) with nothing to say of it: above the composer its card
    // is taken away and never drawn as closed.
    view.rerender(<Chat messages={[ask(twoQuestions, { state: 'closed' }, 'e1')]} />);
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeNull();
    view.rerender(<Chat messages={[first]} />);
    expect(input().value).toBe('');
    // The chat emptied: the same.
    type('once more');
    view.rerender(<Chat messages={[]} />);
    view.rerender(<Chat messages={[first]} />);
    expect(input().value).toBe('');
  });

  it('does not float a question the screen hides, and keeps nothing else from being shown for it', () => {
    function Clear() {
      const { clearScreen } = useChatScreenVisibility();
      return <button data-clear="true" onClick={clearScreen}>clear</button>;
    }
    const messages = [ask(permission)];
    const view = render(
      <ChatScreenVisibilityProvider messages={messages}>
        <Clear />
        <CompactAskOrRecommendationPrompt messages={messages} />
      </ChatScreenVisibilityProvider>,
    );
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeTruthy();
    fireEvent.click(view.container.querySelector('[data-clear]')!);
    // The screen was cleared: the question is not brought back above the composer.
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeNull();
  });

  it('holds its buttons back for a moment when another question takes the place of the one shown', () => {
    vi.useFakeTimers();
    const given: unknown[] = [];
    const onAction = (_: ChatMessage, action: MessageAction) => given.push(action.payload);
    const first = ask(permission);
    const view = render(<CompactNekoPowerAskPrompt messages={[first]} onAction={onAction} />);
    const once = () => view.container.querySelector('[data-choice="once"]') as HTMLButtonElement;
    // The first one shown is answered at once.
    expect(once().disabled).toBe(false);
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')?.getAttribute('data-compact-hit-region')).toBe('true');
    const next = ask({ ...permission, id: 'q9', line: 'run the command rm -rf dist' });
    view.rerender(<CompactNekoPowerAskPrompt messages={[ask(permission, { state: 'answered' }), next]} onAction={onAction} />);
    expect(view.container.querySelector('.nekopower-ask-line')?.textContent).toBe('run the command rm -rf dist');
    // The press that was on its way to the last question does not approve this one.
    expect(once().disabled).toBe(true);
    fireEvent.click(once());
    expect(given).toEqual([]);
    act(() => { vi.advanceTimersByTime(NEKOPOWER_ASK_SETTLE_MS); });
    expect(once().disabled).toBe(false);
    fireEvent.click(once());
    expect(given).toEqual([{ taskId: 'c1', questionId: 'q9', choice: 'once', note: '' }]);
    view.rerender(<CompactNekoPowerAskPrompt messages={[other]} onAction={onAction} />);
    expect(view.container.querySelector('#compact-nekopower-ask-prompt')).toBeNull();
  });
});
