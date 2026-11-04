import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

  it('keeps the original messages available for export after clearing their rows', async () => {
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
    await waitFor(() => expect(buildPreview).toHaveBeenCalledWith(expect.objectContaining({
      messageIds: ['export-original'],
    })));
  });

  it('does not offer ordinary chat clearing during a theater performance', () => {
    render(<App messages={[message('old', 'Ordinary history')]} theaterPresentation={{
      active: true, phase: 'awaiting_player', history: [],
    }} />);
    expect(screen.queryByRole('button', { name: 'Clear screen' })).not.toBeInTheDocument();
  });
});
