import { useState } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import App from './App';
import CompactExportHistoryPanel from './CompactExportHistoryPanel';
import { ChatScreenVisibilityProvider } from './ChatScreenVisibility';
import { parseChatMessage } from './message-schema';

const message = (id: string, text: string, streaming = false) => parseChatMessage({
  id, role: 'assistant', author: 'Neko', time: '12:00',
  status: streaming ? 'streaming' : 'sent', blocks: [{ type: 'text', text }],
});

describe('visual-only chat clearing', () => {
  beforeEach(() => {
    localStorage.setItem('neko.reactChatWindow.compactExportHistoryOpen', 'true');
  });

  afterEach(() => {
    localStorage.removeItem('neko.reactChatWindow.compactExportHistoryOpen');
    vi.restoreAllMocks();
  });

  it.each(['compact', 'full'] as const)('hides existing %s rows without mutating messages or invoking host actions', mode => {
    const messages = [message('old', 'Existing conversation', true)];
    const original = JSON.stringify(messages);
    const onSubmit = vi.fn();
    const onAction = vi.fn();
    const onCleared = vi.fn();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    window.addEventListener('react-chat-window:messages-cleared', onCleared);
    const { container, rerender } = render(
      <App chatSurfaceMode={mode} messages={messages} onComposerSubmit={onSubmit} onMessageAction={onAction} />,
    );
    const history = () => container.querySelector(mode === 'full' ? '.message-list' : '.compact-export-history-scroll')!;
    expect(within(history() as HTMLElement).getByText('Existing conversation')).toBeInTheDocument();
    fetchSpy.mockClear();
    storageSpy.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    expect(within(history() as HTMLElement).queryByText('Existing conversation')).not.toBeInTheDocument();
    expect(JSON.stringify(messages)).toBe(original);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onAction).not.toHaveBeenCalled();
    expect(onCleared).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(storageSpy).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeDisabled();

    rerender(<App chatSurfaceMode={mode} messages={[
      message('old', 'A later streaming update'), message('new', 'New incoming message'),
    ]} />);
    expect(within(history() as HTMLElement).queryByText('A later streaming update')).not.toBeInTheDocument();
    expect(within(history() as HTMLElement).getByText('New incoming message')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    expect(within(history() as HTMLElement).queryByText('New incoming message')).not.toBeInTheDocument();
    window.removeEventListener('react-chat-window:messages-cleared', onCleared);
  });

  it('keeps rows hidden when switching surfaces and preserves the draft and sending', () => {
    const messages = [message('old', 'Surface switch message')];
    const onSubmit = vi.fn();
    const { container, rerender } = render(<App messages={messages} onComposerSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    rerender(<App chatSurfaceMode="full" messages={messages} onComposerSubmit={onSubmit} />);
    expect(container.querySelector('.message-list')).not.toHaveTextContent('Surface switch message');
    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'Continue the conversation' } });
    // A disabled clear is a no-op and leaves the composer draft untouched.
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    expect(input).toHaveValue('Continue the conversation');
    fireEvent.submit(input.closest('form')!);
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ text: 'Continue the conversation' }));
    rerender(<App messages={messages} />);
    expect(container.querySelector('.compact-export-history-scroll')).not.toHaveTextContent('Surface switch message');
  });

  it('restores visibility on a real host reset or a fresh page mount', () => {
    const messages = [message('reused-id', 'Original history')];
    const { container, unmount } = render(<App chatSurfaceMode="full" messages={messages} />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    act(() => window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared')));
    expect(container.querySelector('.message-list')).toHaveTextContent('Original history');
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    unmount();
    const fresh = render(<App chatSurfaceMode="full" messages={messages} />);
    expect(fresh.container.querySelector('.message-list')).toHaveTextContent('Original history');
  });

  it('keeps cleared rows hidden when the desktop restores chat history', () => {
    const old = message('old', 'Cleared history');
    const fresh = message('new', 'Message from the other window');
    const { container, rerender } = render(<App chatSurfaceMode="full" messages={[old]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    act(() => window.dispatchEvent(new CustomEvent('react-chat-window:messages-replaced', {
      detail: { conversationReset: false, historyRestore: true },
    })));
    rerender(<App chatSurfaceMode="full" messages={[old, fresh]} />);
    const history = container.querySelector('.message-list');
    expect(history).not.toHaveTextContent('Cleared history');
    expect(history).toHaveTextContent('Message from the other window');

    act(() => window.dispatchEvent(new CustomEvent('react-chat-window:messages-replaced', {
      detail: { conversationReset: true, historyRestore: true },
    })));
    expect(history).toHaveTextContent('Cleared history');
  });

  it('does not export an invisible preselected message after clearing', async () => {
    const messages = [message('export-original', 'Keep this in the export')];
    const buildPreview = vi.fn().mockResolvedValue({ previewKind: 'empty' });
    function ExportHarness() {
      const [previewOpen, setPreviewOpen] = useState(false);
      return <ChatScreenVisibilityProvider messages={messages}>
        <CompactExportHistoryPanel
          messages={messages} selectedIds={new Set(['export-original'])} selectedCount={1} selectableCount={1}
          autoScrollToBottom={false} previewOpen={previewOpen} controlsOpen choiceLayerAbove={false}
          failedStatusLabel="Failed" onAutoScrollToBottomChange={vi.fn()} onToggleMessage={vi.fn()}
          onSelectAll={vi.fn()} onClearSelection={vi.fn()} onInvertSelection={vi.fn()}
          onRequestPreview={() => setPreviewOpen(true)} onClosePreview={() => setPreviewOpen(false)}
          onBuildPreview={buildPreview} onCopyExport={vi.fn()} onDownloadExport={vi.fn()}
        />
      </ChatScreenVisibilityProvider>;
    }
    const { container } = render(<ExportHarness />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    expect(container.querySelector('.compact-export-history-scroll')).not.toHaveTextContent('Keep this in the export');
    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    await act(async () => {});
    expect(buildPreview).not.toHaveBeenCalled();
    expect(messages[0].id).toBe('export-original');
  });

  it('does not offer ordinary chat clearing during a theater performance', () => {
    render(<App messages={[message('old', 'Ordinary history')]} theaterPresentation={{
      active: true, phase: 'awaiting_player', history: [],
    }} />);
    expect(screen.queryByRole('button', { name: 'Clear screen' })).not.toBeInTheDocument();
  });

  it('exposes an accessible icon with temporary success feedback', () => {
    vi.useFakeTimers();
    try {
      render(<App chatSurfaceMode="full" messages={[message('one', 'Hello')]} />);
      const button = screen.getByRole('button', { name: 'Clear screen' });
      expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
      expect(button).toHaveTextContent('');
      expect(button).toHaveAttribute('data-neko-tooltip', expect.stringContaining('Conversation history and memory are kept.'));
      fireEvent.focusIn(button);
      expect(screen.getByRole('tooltip')).toHaveTextContent('Clear screen');
      fireEvent.click(button);
      expect(button).toHaveAttribute('data-cleared', 'true');
      expect(screen.getByRole('status')).toHaveTextContent('Screen cleared');
      act(() => vi.advanceTimersByTime(1000));
      expect(button).not.toHaveAttribute('data-cleared');
      expect(screen.getByRole('status')).toHaveTextContent('');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the compact capsule from replaying hidden streaming text', async () => {
    vi.useFakeTimers();
    try {
      const messages = [message('stream', 'X', true)];
      const { container, rerender } = render(<App messages={messages} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('X');
      fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent('X');
      rerender(<App messages={[message('stream', 'X hidden reply', true)]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent('X');
      expect(container.querySelector('.compact-export-history-scroll')).not.toHaveTextContent('X');
    } finally {
      vi.useRealTimers();
    }
  });

  it('disables compact clearing when its only message is a hidden topic hint', () => {
    const hint = parseChatMessage({ id: 'hint', role: 'system', author: 'Neko', time: '',
      blocks: [{ type: 'topic-hint', author: 'Neko' }] });
    const { rerender } = render(<App messages={[hint]} />);
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeDisabled();
    rerender(<App messages={[hint]} chatSurfaceMode="full" />);
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeEnabled();
  });
  it.each(['set-messages', 'messages-replaced'])('resets reused IDs on host %s', eventName => {
    const {container, rerender} = render(<App chatSurfaceMode="full" messages={[message('id', 'Old')]} />);
    fireEvent.click(screen.getByRole('button', {name:'Clear screen'}));
    act(() => window.dispatchEvent(new CustomEvent(`react-chat-window:${eventName}`, {detail:{messages:[]}})));
    rerender(<App chatSurfaceMode="full" messages={[message('id', 'Replacement')]} />);
    expect(container.querySelector('.message-list')).toHaveTextContent('Replacement');
  });

  it('hides caption events from the cleared turn but shows the next turn', async () => {
    vi.useFakeTimers();
    try {
      const {container} = render(<App messages={[message('host-id','Original',true)]} />);
      const caption = (turnId:string,text:string) => act(() => window.dispatchEvent(
        new CustomEvent('neko-compact-caption-update', {detail:{turnId,text}}),
      ));
      caption('turn1', 'X');
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('X');
      fireEvent.click(screen.getByRole('button', {name:'Clear screen'}));
      caption('turn1','XY');
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent('X');
      caption('turn2','Z');
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('Z');
    } finally { vi.useRealTimers(); }
  });

  it('resumes bottom-following after clearing a scrolled-up full history', () => {
    const {container, rerender} = render(<App chatSurfaceMode="full" messages={[message('old','Old')]} />);
    const list = container.querySelector('.message-list') as HTMLElement;
    Object.defineProperty(list, 'scrollHeight', {configurable:true, get:()=>1000});
    Object.defineProperty(list, 'clientHeight', {configurable:true, get:()=>100});
    list.scrollTop = 0;
    fireEvent.scroll(list);
    fireEvent.click(screen.getByRole('button', {name:'Clear screen'}));
    rerender(<App chatSurfaceMode="full" messages={[message('old','Old'), message('new','New')]} />);
    expect(list.scrollTop).toBe(1000);
  });

  it.each(['messages-cleared', 'messages-replaced', 'set-messages'])(
    'does not revive cleared caption or speech previews after %s', async eventName => {
      vi.useFakeTimers();
      try {
        const {container, rerender} = render(<App messages={[message('old','Y',true)]} />);
        act(() => window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail:{turnId:'old-turn',text:'X'},
        })));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('X');
        fireEvent.click(screen.getByRole('button', {name:'Clear screen'}));
        rerender(<App messages={[]} />);
        act(() => window.dispatchEvent(new CustomEvent(`react-chat-window:${eventName}`, {detail:{messages:[]}})));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent(/X|Y/);
        act(() => window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail:{turnId:'new-turn',text:'Z'},
        })));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('Z');
      } finally { vi.useRealTimers(); }
    },
  );

  it.each(['messages-cleared', 'messages-replaced', 'set-messages'])(
    'clears preserved message speech on %s without a caption event', async eventName => {
      vi.useFakeTimers();
      try {
        const {container, rerender} = render(<App messages={[message('old','Y',true)]} />);
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('Y');
        fireEvent.click(screen.getByRole('button', {name:'Clear screen'}));
        rerender(<App messages={[]} />);
        act(() => window.dispatchEvent(new CustomEvent(`react-chat-window:${eventName}`)));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent('Y');
      } finally { vi.useRealTimers(); }
    },
  );

  it.each([
    ['messages-cleared', undefined],
    ['set-messages', {messages:[]}],
    ['messages-replaced', {conversationReset:true}],
  ])('releases an unfinished turn on conversation reset %s', async (eventName, detail) => {
    vi.useFakeTimers();
    try {
      const {container} = render(<App messages={[]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      const preview = () => container.querySelector('.compact-chat-capsule-text')?.textContent;
      const emptyCopy = preview();
      expect(emptyCopy).toBeTruthy();
      act(() => window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {detail:{turnId:'old-turn'}})));
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(preview()).toBe('');
      act(() => window.dispatchEvent(new CustomEvent(`react-chat-window:${eventName}`, {detail})));
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(preview()).toBe(emptyCopy);
    } finally { vi.useRealTimers(); }
  });

  it('keeps the current turn guard during an ordinary nonempty replacement', async () => {
    vi.useFakeTimers();
    try {
      const stale = {...message('stale','X',true), turnId:'previous-turn'};
      const {container} = render(<App messages={[stale]} />);
      act(() => window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {detail:{turnId:'current-turn'}})));
      act(() => {
        window.dispatchEvent(new CustomEvent('react-chat-window:set-messages', {detail:{messages:[stale]}}));
        window.dispatchEvent(new CustomEvent('react-chat-window:messages-replaced', {detail:{conversationReset:false}}));
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(container.querySelector('.compact-chat-capsule-text')?.textContent).toBe('');
      act(() => window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {detail:{turnId:'current-turn',text:'Y'}})));
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('Y');
    } finally { vi.useRealTimers(); }
  });

  const sentence = (id: string, text: string, turnId?: string, streaming = false) => parseChatMessage({
    id, role: 'assistant', author: 'Neko', time: '12:00', status: streaming ? 'streaming' : 'sent', turnId,
    blocks: [{ type: 'text', text }],
  });
  const userMessage = (id: string, text: string) => parseChatMessage({
    id, role: 'user', author: 'Me', time: '12:00', status: 'sent', blocks: [{ type: 'text', text }],
  });

  it.each(['compact', 'full'] as const)('keeps later %s sentences of the cleared reply hidden', mode => {
    const { container, rerender } = render(
      <App chatSurfaceMode={mode} messages={[sentence('s1', 'First sentence', 'turn-a')]} />,
    );
    const history = () => container.querySelector(mode === 'full' ? '.message-list' : '.compact-export-history-scroll') as HTMLElement;
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    rerender(<App chatSurfaceMode={mode} messages={[
      sentence('s1', 'First sentence', 'turn-a'), sentence('s2', 'Second sentence', 'turn-a'),
    ]} />);
    expect(history()).not.toHaveTextContent('Second sentence');
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeDisabled();

    rerender(<App chatSurfaceMode={mode} messages={[
      sentence('s1', 'First sentence', 'turn-a'), sentence('s2', 'Second sentence', 'turn-a'),
      userMessage('u1', 'User follow-up'), sentence('s3', 'Next reply', 'turn-b'),
    ]} />);
    expect(history()).not.toHaveTextContent('Second sentence');
    expect(history()).toHaveTextContent('User follow-up');
    expect(history()).toHaveTextContent('Next reply');
    expect(screen.getByRole('button', { name: 'Clear screen' })).toBeEnabled();
  });

  it('hides the caption turn even before its sentences carry a turn id', () => {
    const { container, rerender } = render(
      <App chatSurfaceMode="full" messages={[sentence('old', 'Earlier reply')]} />,
    );
    act(() => window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', { detail: { turnId: 'turn-live' } })));
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    rerender(<App chatSurfaceMode="full" messages={[
      sentence('old', 'Earlier reply'), sentence('live-2', 'Rest of the live reply', 'turn-live'),
    ]} />);
    expect(container.querySelector('.message-list')).not.toHaveTextContent('Rest of the live reply');
  });

  it('keeps the compact capsule off later sentences of the cleared reply', async () => {
    vi.useFakeTimers();
    try {
      const startTurn = (turnId: string) => act(() => window.dispatchEvent(
        new CustomEvent('neko-assistant-turn-start', { detail: { turnId } }),
      ));
      const { container, rerender } = render(<App messages={[]} />);
      startTurn('turn-a');
      rerender(<App messages={[sentence('s1', 'Q', 'turn-a', true)]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('Q');
      fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
      rerender(<App messages={[sentence('s1', 'Q', 'turn-a'), sentence('s2', 'J', 'turn-a', true)]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent('J');
      startTurn('turn-b');
      rerender(<App messages={[
        sentence('s1', 'Q', 'turn-a'), sentence('s2', 'J', 'turn-a'), sentence('s3', 'K', 'turn-b', true),
      ]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent('K');
    } finally { vi.useRealTimers(); }
  });

  it('still shows a recommendation card that arrives later in the cleared reply', () => {
    const card = parseChatMessage({
      id: 'card', role: 'assistant', author: 'Neko', time: '12:00', status: 'sent', turnId: 'turn-a',
      blocks: [{ type: 'music_recommendation', recommendationId: '6f1c2f0a-7d7e-4a39-9a51-3b0b3c1d2e4f',
        state: 'pending', track: { name: 'Late card song', artist: 'Artist', url: 'https://example.com/a.mp3', cover: '' } }],
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="full" messages={[sentence('s1', 'First sentence', 'turn-a')]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    rerender(<App chatSurfaceMode="full" messages={[
      sentence('s1', 'First sentence', 'turn-a'), sentence('s2', 'Second sentence', 'turn-a'), card,
    ]} />);
    expect(container.querySelector('.message-list')).not.toHaveTextContent('Second sentence');
    expect(container.querySelector('.message-list')).toHaveTextContent('Late card song');
  });

  it('releases hidden reply turns on a host reset', () => {
    const { container, rerender } = render(
      <App chatSurfaceMode="full" messages={[sentence('s1', 'Old reply', 'turn-a')]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Clear screen' }));
    act(() => window.dispatchEvent(new CustomEvent('react-chat-window:messages-cleared')));
    rerender(<App chatSurfaceMode="full" messages={[sentence('s9', 'Restored reply', 'turn-a')]} />);
    expect(container.querySelector('.message-list')).toHaveTextContent('Restored reply');
  });
});
