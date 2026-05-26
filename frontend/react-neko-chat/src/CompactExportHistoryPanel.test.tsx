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

function renderPanel(overrides: Partial<Parameters<typeof CompactExportHistoryPanel>[0]> = {}) {
  return render(
    <CompactExportHistoryPanel
      messages={[message]}
      selectedIds={new Set([message.id])}
      selectedCount={1}
      selectableCount={1}
      autoScrollToBottom={false}
      previewOpen
      choiceLayerAbove={false}
      failedStatusLabel="Failed"
      onAutoScrollToBottomChange={vi.fn()}
      onToggleMessage={vi.fn()}
      onSelectAll={vi.fn()}
      onClearSelection={vi.fn()}
      onInvertSelection={vi.fn()}
      onRequestPreview={vi.fn()}
      onClosePreview={vi.fn()}
      onBuildPreview={vi.fn().mockResolvedValue({
        previewKind: 'document',
        previewDocument: '<!doctype html><html><body>Preview</body></html>',
      })}
      onCopyExport={vi.fn()}
      onDownloadExport={vi.fn()}
      {...overrides}
    />,
  );
}

describe('CompactExportHistoryPanel', () => {
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
        format: 'markdown',
        imageStyle: 'neko',
        imageFormat: 'png',
      });
      expect(consoleError).toHaveBeenCalled();
      expect(copyButton).not.toBeDisabled();
    } finally {
      consoleError.mockRestore();
    }
  });
});
