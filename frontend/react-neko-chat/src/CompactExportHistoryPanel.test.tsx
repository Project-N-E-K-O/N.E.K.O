import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  it('shows the history height resize bar only outside preview and wires its hit-region', () => {
    const { container, rerender } = renderPanel({ previewOpen: false, visibilityState: 'open' });
    const bar = container.querySelector('.compact-export-history-resize-bar');
    expect(bar).not.toBeNull();
    expect(bar?.getAttribute('data-compact-hit-region-id')).toBe('history:resize');
    expect(bar?.getAttribute('data-compact-hit-region-kind')).toBe('resize');
    expect(bar?.getAttribute('data-compact-no-drag')).toBe('true');

    rerender(<CompactExportHistoryPanel {...createPanelProps({ previewOpen: true, visibilityState: 'open' })} />);
    expect(container.querySelector('.compact-export-history-resize-bar')).toBeNull();
  });

  it('marks the history resize bar active while dragging', () => {
    const { container } = renderPanel({ previewOpen: false, visibilityState: 'open', historyResizeActive: true });
    expect(container.querySelector('.compact-export-history-resize-bar.is-active')).not.toBeNull();
  });

  it('flags the anchor as resizing so content height locks to max (no reflow on shrink)', () => {
    const { container, rerender } = renderPanel({ previewOpen: false, visibilityState: 'open', historyResizeActive: false });
    const anchor = container.querySelector('.compact-export-history-anchor');
    expect(anchor?.getAttribute('data-compact-export-history-resizing')).toBe('false');
    rerender(<CompactExportHistoryPanel {...createPanelProps({ previewOpen: false, visibilityState: 'open', historyResizeActive: true })} />);
    expect(anchor?.getAttribute('data-compact-export-history-resizing')).toBe('true');
  });

  it('re-pins the history list to the bottom on geometry refresh only while shrinking and auto-following', () => {
    // 方向化钉底：只有「缩小」（可视窗口高度变小）才强制把可视窗口锚回下端；「增高」方向交给浏览器
    // 自然 clamp（每帧强写 scrollTop 会与正被揭出的上方新气泡重排打架→展开抖动）；非 auto-following 一律不动。
    const scrollTopValues: number[] = [];
    const scrollTopByElement = new WeakMap<HTMLElement, number>();
    // 可控的可视窗口高度，模拟拖动 resize bar 时 clientHeight 的逐帧变化。
    let mockClientHeight = 300;
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
    const scrollTopDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');

    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      // resizing 态内容锚定在 max，scrollHeight 在一次拖动里保持不变。
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 640 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? mockClientHeight : 0;
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
      // auto-following off：即便缩小也不该把视口从用户当前滚动位置拽走。
      const { rerender } = renderPanel({ previewOpen: false, visibilityState: 'open', autoScrollToBottom: false });
      scrollTopValues.length = 0;
      mockClientHeight = 200; // 缩小
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
      });
      expect(scrollTopValues).not.toContain(440);

      // auto-following on，但方向是「增高」：不强制钉底，交给浏览器自然 clamp（消除展开抖动）。
      // 上一次 refresh 已把方向基线（lastGeometryClientHeight）更新到 200，这里从 200 → 360 是增高。
      rerender(<CompactExportHistoryPanel {...createPanelProps({ previewOpen: false, visibilityState: 'open', autoScrollToBottom: true })} />);
      scrollTopValues.length = 0;
      mockClientHeight = 360; // 增高
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
      });
      expect(scrollTopValues).toHaveLength(0);

      // auto-following on，方向是「缩小」：钉底到 scrollHeight - clientHeight = 640 - 200 = 440。
      // 上一帧 refresh 已把基线更新到 360，这里 360 → 200 是缩小。
      scrollTopValues.length = 0;
      mockClientHeight = 200; // 缩小
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
      });
      expect(scrollTopValues).toContain(440);
    } finally {
      if (scrollHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', scrollHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
      }
      if (clientHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
      }
      if (scrollTopDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollTop', scrollTopDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollTop');
      }
    }
  });

  it('drops the history resize bar hit-region when a choice prompt sits above', () => {
    const { container } = renderPanel({ previewOpen: false, visibilityState: 'open', choiceLayerAbove: true });
    const bar = container.querySelector('.compact-export-history-resize-bar');
    expect(bar).not.toBeNull();
    expect(bar?.getAttribute('data-compact-hit-region-id')).toBeNull();
    expect(bar?.getAttribute('data-compact-hit-region')).toBeNull();
    expect(bar?.getAttribute('data-compact-hit-region-kind')).toBeNull();
  });

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

  it('shows the compact history scrollbar while the desktop cursor is over the history area', () => {
    const { container } = renderPanel({
      previewOpen: false,
      visibilityState: 'open',
    });

    const scroll = container.querySelector('.compact-export-history-scroll');
    expect(scroll).not.toBeNull();
    expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');

    fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
      detail: { active: true },
    }));
    expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');

    fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
      detail: { active: false },
    }));
    expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');
  });

  it('keeps the scrollbar visible while the desktop cursor remains over transparent history', () => {
    vi.useFakeTimers();

    try {
      const { container } = renderPanel({
        previewOpen: false,
        visibilityState: 'open',
      });

      const scroll = container.querySelector('.compact-export-history-scroll');
      expect(scroll).not.toBeNull();

      fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
        detail: { active: true },
      }));
      expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');

      fireEvent.wheel(scroll!, { deltaY: 12 });
      act(() => {
        vi.advanceTimersByTime(1200);
      });
      expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');

      fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
        detail: { active: false },
      }));
      expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not render the scrollbar hit area when the history cannot scroll', () => {
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');

    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 240 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 240 : 0;
      },
    });

    try {
      const { container } = renderPanel({
        previewOpen: false,
        visibilityState: 'open',
      });

      const scroll = container.querySelector('.compact-export-history-scroll');
      expect(scroll).not.toBeNull();

      fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
        detail: { active: true },
      }));
      expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');
      expect(container.querySelector('.compact-export-history-scrollbar-hit')).toBeNull();
    } finally {
      if (scrollHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', scrollHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
      }
      if (clientHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
      }
    }
  });

  it('scrolls the compact history list when the visible scrollbar hit area is dragged', () => {
    const scrollTopByElement = new WeakMap<HTMLElement, number>();
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
    const scrollTopDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');

    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 1000 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 250 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
      configurable: true,
      get() {
        return scrollTopByElement.get(this) ?? 0;
      },
      set(value: number) {
        scrollTopByElement.set(this, value);
      },
    });

    try {
      const { container } = renderPanel({
        previewOpen: false,
        visibilityState: 'open',
      });
      const scroll = container.querySelector<HTMLElement>('.compact-export-history-scroll');
      expect(scroll).not.toBeNull();

      fireEvent(window, new CustomEvent('neko:compact-history-hover-state-change', {
        detail: { active: true },
      }));
      const hit = container.querySelector<HTMLElement>('.compact-export-history-scrollbar-hit');
      expect(hit).not.toBeNull();

      fireEvent.pointerDown(hit!, {
        pointerId: 1,
        pointerType: 'mouse',
        button: 0,
        clientY: 20,
      });
      fireEvent.pointerMove(hit!, {
        pointerId: 1,
        pointerType: 'mouse',
        clientY: 70,
      });
      fireEvent.pointerUp(hit!, {
        pointerId: 1,
        pointerType: 'mouse',
        clientY: 70,
      });

      expect(scroll!.scrollTop).toBeGreaterThan(0);
    } finally {
      if (scrollHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', scrollHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
      }
      if (clientHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeightDescriptor);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
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
