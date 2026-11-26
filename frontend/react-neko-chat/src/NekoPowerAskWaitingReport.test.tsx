import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { ChatScreenVisibilityProvider, useChatScreenVisibility } from './ChatScreenVisibility';
import { NekoPowerAskWaitingReport } from './CompactNekoPowerAskPrompt';
import { parseChatMessage, parseChatWindowProps } from './message-schema';
import type { ChatMessage } from './message-schema';

// Folded into a ball, the chat shows no question of a commission: the host marks the ball when
// one waits for an answer, as the root of the chat tells it (onNekoPowerAskWaitingChange).

const permission = { kind: 'permission', id: 'q1', category: 'run_command', options: ['always', 'session', 'once', 'deny'],
  detail: { command: 'npm test' }, line: 'run the command npm test', line_cut: false };
const other = parseChatMessage({ id: 'm1', role: 'assistant', author: 'Neko', time: '12:00', blocks: [{ type: 'text', text: 'hello' }] });

function ask(over: Record<string, unknown> = {}): ChatMessage {
  return parseChatMessage({
    id: 'nekopower-ask-c1-q1', role: 'system', author: 'NekoPower', time: '12:00',
    blocks: [{ type: 'nekopower_ask', taskId: 'c1', questionId: 'q1', title: 'build the site', question: permission, state: 'pending', ...over }],
  });
}

function Clear() {
  const { clearScreen } = useChatScreenVisibility();
  return <button data-clear="true" onClick={clearScreen}>clear</button>;
}

function report(messages: ChatMessage[], onChange?: (waiting: boolean) => void) {
  return (
    <ChatScreenVisibilityProvider messages={messages}>
      <Clear />
      <NekoPowerAskWaitingReport messages={messages} onChange={onChange} />
    </ChatScreenVisibilityProvider>
  );
}

describe('whether the ball the chat folds into is marked', () => {
  it('is told while a question waits to be answered, or its answer is on its way, and once it is not', () => {
    const onChange = vi.fn();
    const view = render(report([other], onChange));
    expect(onChange.mock.calls).toEqual([[false]]);
    view.rerender(report([other, ask()], onChange));
    expect(onChange.mock.calls).toEqual([[false], [true]]);
    view.rerender(report([other, ask({ state: 'sending' })], onChange));
    view.rerender(report([other, ask({ state: 'answered', answer: 'once' })], onChange));
    expect(onChange.mock.calls).toEqual([[false], [true], [false]]);
    view.rerender(report([other, ask({ state: 'closed' })], onChange));
    expect(onChange.mock.calls).toEqual([[false], [true], [false]]);
  });

  it('is told once for each change, however often the chat is drawn and its callback wrapped anew', () => {
    const first = vi.fn();
    const later = vi.fn();
    const view = render(report([ask()], first));
    view.rerender(report([ask(), other], later));
    view.rerender(report([ask(), other], later));
    expect(first.mock.calls).toEqual([[true]]);
    expect(later).not.toHaveBeenCalled();
    view.rerender(report([other], later));
    expect(later.mock.calls).toEqual([[false]]);
  });

  it('is told once there is someone to tell, and that nothing waits when the chat is taken down', () => {
    const onChange = vi.fn();
    const view = render(report([ask()]));
    view.rerender(report([ask()], onChange));
    expect(onChange.mock.calls).toEqual([[true]]);
    view.unmount();
    expect(onChange.mock.calls).toEqual([[true], [false]]);
  });

  it('does not count a question the screen was cleared of, as the compact chat would not show it', () => {
    const onChange = vi.fn();
    const messages = [ask()];
    const view = render(report(messages, onChange));
    expect(onChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(view.container.querySelector('[data-clear]')!);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it('reaches the host from the root of the chat through the props it hands over, folded or not', () => {
    for (const chatSurfaceMode of ['minimized', 'compact', 'full'] as const) {
      const onNekoPowerAskWaitingChange = vi.fn();
      const props = parseChatWindowProps({ messages: [other, ask()], chatSurfaceMode, onNekoPowerAskWaitingChange });
      const view = render(<App {...props} />);
      expect(onNekoPowerAskWaitingChange, chatSurfaceMode).toHaveBeenLastCalledWith(true);
      view.unmount();
    }
  });
});
