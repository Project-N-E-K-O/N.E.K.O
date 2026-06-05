import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CompactExportHistoryPanel from './CompactExportHistoryPanel';
import { parseChatMessage } from './message-schema';

const message = parseChatMessage({
  id: 'compact-export-message',
  role: 'assistant',
  author: 'Neko',
  time: '10:00',
  createdAt: 1,
  blocks: [{ type: 'text', text: 'Export me.' }],
  status: 'sent',
});

function createPanelProps(overrides: Partial<Parameters<typeof CompactExportHistoryPanel>[0]> = {}) {
  return {
    messages: [message],
    selectedIds: new Set([message.id]),
    selectedCount: 1,
    selectableCount: 1,
    autoScrollToBottom: false,
    previewOpen: true,
    controlsOpen: true,
    choiceLayerAbove: false,
    failedStatusLabel: 'Failed',
    onAutoScrollToBottomChange: vi.fn(),
    onToggleMessage: vi.fn(),
    onSelectAll: vi.fn(),
    onClearSelection: vi.fn(),
    onInvertSelection: vi.fn(),
    onRequestPreview: vi.fn(),
    onClosePreview: vi.fn(),
    onBuildPreview: vi.fn().mockResolvedValue({
      previewKind: 'document',
      previewDocument: '<!doctype html><html><body>Preview</body></html>',
    }),
    onCopyExport: vi.fn(),
    onDownloadExport: vi.fn(),
    ...overrides,
  };
}

function renderPanel(overrides: Partial<Parameters<typeof CompactExportHistoryPanel>[0]> = {}) {
  return render(<CompactExportHistoryPanel {...createPanelProps(overrides)} />);
}

describe('CompactExportHistoryPanel', () => {
  it('pins the history list to bottom when returning from preview', () => {
    const scrollTopValues: number[] = [];
    const scrollTopByElement = new WeakMap<HTMLElement, number>();
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    const scrollTopDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');

    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 640 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
      configurable: true,
      get() {
        return scrollTopByElement.get(this) ?? 0;
      },
      set(value: number) {
        scrollTopByElement.set(this, value);
        if (this.classList.contains('compact-export-history-scroll')) {
          scrollTopValues.push(value);
        }
      },
    });

    try {
      const props = createPanelProps({
        autoScrollToBottom: true,
        previewOpen: true,
      });
      const { container, rerender } = render(<CompactExportHistoryPanel {...props} />);

      expect(screen.getByText('Export Preview')).toBeInTheDocument();
      expect(container.querySelector('.compact-export-history-scroll')).toBeNull();

      rerender(<CompactExportHistoryPanel {...props} previewOpen={false} />);

      expect(container.querySelector('.compact-export-history-scroll')).not.toBeNull();
      expect(scrollTopValues).toContain(640);
    } finally {
      if (scrollHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', scrollHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
      }
      if (scrollTopDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollTop', scrollTopDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollTop');
      }
    }
  });

  it('handles synchronous preview build failures in the preview error state', async () => {
    renderPanel({
      onBuildPreview: vi.fn(() => {
        throw new Error('sync preview failed');
      }),
    });

    await waitFor(() => {
      expect(screen.getByText('Failed to build the preview.')).toBeInTheDocument();
    });
  });

  it('handles rejected export actions without leaving the action pending', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onCopyExport = vi.fn().mockRejectedValue(new Error('copy failed'));

    try {
      renderPanel({ onCopyExport });

      await waitFor(() => {
        expect(screen.getByTitle('Export Preview')).toBeInTheDocument();
      });

      const copyButton = screen.getByRole('button', { name: 'Copy to Clipboard' });
      fireEvent.click(copyButton);

      await waitFor(() => {
        expect(screen.getByText('Export failed. Please try again.')).toBeInTheDocument();
      });
      expect(onCopyExport).toHaveBeenCalledWith({
        messageIds: [message.id],
        format: 'image',
        imageStyle: 'neko',
        imageFormat: 'png',
      });
      expect(consoleError).toHaveBeenCalled();
      expect(copyButton).not.toBeDisabled();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('clears rejected export action errors when the preview closes', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onCopyExport = vi.fn().mockRejectedValue(new Error('copy failed'));
    const props = createPanelProps({ onCopyExport });

    try {
      const { rerender } = render(<CompactExportHistoryPanel {...props} />);

      await waitFor(() => {
        expect(screen.getByTitle('Export Preview')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Copy to Clipboard' }));

      await waitFor(() => {
        expect(screen.getByText('Export failed. Please try again.')).toBeInTheDocument();
      });

      rerender(<CompactExportHistoryPanel {...props} previewOpen={false} />);
      rerender(<CompactExportHistoryPanel {...props} previewOpen />);

      await waitFor(() => {
        expect(screen.getByTitle('Export Preview')).toBeInTheDocument();
      });
      expect(screen.queryByText('Export failed. Please try again.')).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});
