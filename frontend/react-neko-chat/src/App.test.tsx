import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App, { COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS } from './App';
import {
  COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS,
  computeCompactHistoryEnterDelay,
  computeCompactHistoryExitDelay,
} from './CompactExportHistoryPanel';
import MessageList from './MessageList';
import { parseChatMessage, type CompactChatState } from './message-schema';

describe('App', () => {
  const COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY = 'neko.reactChatWindow.compactExportHistoryOpen';

  beforeEach(() => {
    window.localStorage.removeItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
  });

  const openCompactInputTools = async () => {
    try {
      vi.useFakeTimers();
      const fan = document.body.querySelector<HTMLElement>('.compact-input-tool-fan');
      if (fan?.getAttribute('data-compact-input-tool-fan-open') !== 'true') {
        fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
    } finally {
      vi.useRealTimers();
    }
    const fan = document.body.querySelector<HTMLElement>('.compact-input-tool-fan');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'true');
  };

  const clickCompactExportTool = async () => {
    await openCompactInputTools();
    const exportButton = document.body.querySelector<HTMLButtonElement>('.compact-input-tool-item-export');
    expect(exportButton).not.toBeNull();
    expect(exportButton).not.toBeDisabled();
    fireEvent.click(exportButton!);
    return exportButton!;
  };

  const waitForCompactHistoryDragLayerToClear = async () => {
    await waitFor(() => {
      expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toBeNull();
    });
  };

  const mockHoverCapableMatchMedia = (hoverCapable = true) => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: hoverCapable && query === '(hover: hover) and (pointer: fine)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  };

  const setupAvatarDropBounds = () => {
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    return () => {
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    };
  };

  const setupDesktopAvatarDropBounds = () => {
    const hostWindow = window as Window & {
      __nekoDesktopAvatarBounds?: unknown;
    };
    hostWindow.__nekoDesktopAvatarBounds = {
      left: 100,
      right: 200,
      top: 100,
      bottom: 200,
      width: 100,
      height: 100,
    };
    return () => {
      delete hostWindow.__nekoDesktopAvatarBounds;
    };
  };

  const renderInputApp = (
    props: React.ComponentProps<typeof App> = {},
  ) => render(<App compactChatState="input" {...props} />);

  it('renders compact subtitle capsule by default while keeping the tool button visible', () => {
    render(<App />);

    expect(screen.queryByPlaceholderText('Type a message...')).toBeNull();
    expect(document.body.querySelector('.compact-chat-stage-default')).not.toBeNull();
    expect(document.body.querySelector('.compact-chat-capsule-button')).not.toBeNull();
    expect(screen.getByRole('button', { name: '更多工具' })).toBeInTheDocument();
    expect(document.body.querySelector('.compact-input-tool-fan')).not.toBeNull();
  });

  it('enters compact input from the subtitle capsule when used uncontrolled', () => {
    const { container } = render(<App chatSurfaceMode="compact" />);

    // 未受控：初始是字幕胶囊，没有输入框
    expect(container.querySelector('.compact-chat-capsule-button')).not.toBeNull();
    expect(container.querySelector('.composer-input')).toBeNull();

    fireEvent.click(container.querySelector('.compact-chat-capsule-button') as HTMLButtonElement);

    // 点击胶囊后内部 state 兜底切到输入态，输入框出现
    expect(container.querySelector('.composer-input')).not.toBeNull();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');
  });

  it('exposes explicit surface mode state on the rendered shell', () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    const appShell = container.querySelector('.app-shell');
    const chatWindow = container.querySelector('.chat-window');
    const compactStage = container.querySelector('.compact-chat-stage');

    expect(appShell).toHaveAttribute('data-chat-surface-mode', 'compact');
    expect(appShell).toHaveAttribute('data-compact-chat-state', 'input');
    expect(chatWindow).toHaveClass('chat-surface-mode-compact');
    expect(compactStage).toHaveAttribute('data-compact-chat-state', 'input');
  });

  it('declares compact drag surface and no-drag controls in compact states only', () => {
    const { container, rerender } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    expect(container.querySelector('.compact-chat-surface-shell .compact-chat-drag-handle')).toBeNull();
    expect(container.querySelectorAll('.compact-chat-surface-shell .compact-chat-resize-handle')).toHaveLength(2);
    expect(container.querySelector('.compact-chat-surface-shell')).not.toHaveAttribute('data-compact-geometry-item');
    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).toHaveAttribute('data-compact-geometry-item', 'input');
    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(container.querySelector('[data-compact-drag-surface="true"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(container.querySelector('[data-compact-geometry-item="dragHandle"]')).toBeNull();
    expect(container.querySelector('.composer-input')).toHaveAttribute('data-compact-no-drag', 'true');
    expect(container.querySelector('.compact-input-tool-toggle')).toHaveAttribute('data-compact-no-drag', 'true');
    expect(container.querySelector('[data-compact-resize-side="left"]')).toHaveAttribute('data-compact-geometry-item', 'resizeHandle');
    expect(container.querySelector('[data-compact-resize-side="left"]')).toHaveAttribute('data-compact-no-drag', 'true');
    expect(container.querySelector('[data-compact-resize-side="right"]')).toHaveAttribute('data-compact-geometry-item', 'resizeHandle');
    expect(container.querySelector('[data-compact-resize-side="right"]')).toHaveAttribute('data-compact-no-drag', 'true');
    const stableSurfaceShell = container.querySelector('.compact-chat-surface-shell');
    const stableSurfaceFrame = container.querySelector('.compact-chat-surface-frame');

    rerender(<App chatSurfaceMode="compact" compactChatState="input" composerHidden />);
    expect(container.querySelector('.compact-chat-surface-shell .compact-chat-drag-handle')).toBeNull();
    expect(container.querySelectorAll('.compact-chat-surface-shell .compact-chat-resize-handle')).toHaveLength(2);
    expect(container.querySelector('.compact-chat-surface-shell')).not.toHaveAttribute('data-compact-geometry-item');
    expect(container.querySelector('[data-compact-geometry-part="capsuleBody"]')).toHaveAttribute('data-compact-geometry-item', 'capsule');
    expect(container.querySelector('[data-compact-geometry-part="capsuleBody"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(container.querySelector('[data-compact-drag-surface="true"]')).toHaveAttribute('data-compact-geometry-part', 'capsuleBody');
    expect(container.querySelector('.compact-chat-surface-shell')).toBe(stableSurfaceShell);
    expect(container.querySelector('.compact-chat-surface-frame')).toBe(stableSurfaceFrame);

    rerender(<App chatSurfaceMode="minimized" />);
    expect(container.querySelector('.compact-chat-drag-handle')).toBeNull();
    expect(container.querySelector('.compact-chat-resize-handle')).toBeNull();
    expect(container.querySelector('[data-compact-geometry-owner="surface"]')).toBeNull();
  });

  it('lets compact surface resize from the visible edges without collapsing input or firing tools', async () => {
    const onCompactChatStateChange = vi.fn();
    const onComposerImportImage = vi.fn();
    const resizeRequests: Array<{
      side: string;
      width: number;
      phase: string;
      screenRect?: { left: number; top: number; width: number; height: number; right: number; bottom: number };
    }> = [];
    const handleResizeRequest = (event: Event) => {
      resizeRequests.push((event as CustomEvent).detail);
    };
    window.addEventListener('neko:compact-surface-resize-request', handleResizeRequest);
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
        onComposerImportImage={onComposerImportImage}
      />,
    );

    try {
      const rightHandle = container.querySelector<HTMLDivElement>('[data-compact-resize-side="right"]');
      expect(rightHandle).not.toBeNull();
      fireEvent.pointerDown(rightHandle!, {
        pointerId: 21,
        clientX: 430,
        screenX: 430,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(rightHandle!, {
        pointerId: 21,
        clientX: 560,
        screenX: 560,
        buttons: 1,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('560px');
      });
      expect((container.querySelector('.compact-chat-surface-shell') as HTMLElement).style
        .getPropertyValue('--compact-surface-resize-width')).toBe('560px');
      expect(resizeRequests).toEqual([
        expect.objectContaining({
          side: 'right',
          width: 430,
          phase: 'start',
          screenRect: expect.objectContaining({ left: 0, width: 430, right: 430 }),
        }),
        expect.objectContaining({
          side: 'right',
          width: 560,
          phase: 'move',
          screenRect: expect.objectContaining({ left: 0, width: 560, right: 560 }),
        }),
      ]);

      fireEvent.pointerUp(rightHandle!, {
        pointerId: 21,
        clientX: 560,
        screenX: 560,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('');
      });
      expect((container.querySelector('.compact-chat-surface-shell') as HTMLElement).style
        .getPropertyValue('--compact-surface-resize-width')).toBe('');
      expect(resizeRequests).toEqual([
        expect.objectContaining({
          side: 'right',
          width: 430,
          phase: 'start',
          screenRect: expect.objectContaining({ left: 0, width: 430, right: 430 }),
        }),
        expect.objectContaining({
          side: 'right',
          width: 560,
          phase: 'move',
          screenRect: expect.objectContaining({ left: 0, width: 560, right: 560 }),
        }),
        expect.objectContaining({
          side: 'right',
          width: 560,
          phase: 'end',
          screenRect: expect.objectContaining({ left: 0, width: 560, right: 560 }),
        }),
      ]);

      fireEvent.pointerDown(rightHandle!, {
        pointerId: 22,
        clientX: 560,
        screenX: 560,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(rightHandle!, {
        pointerId: 22,
        clientX: 240,
        screenX: 240,
        buttons: 1,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('430px');
      });
      fireEvent.pointerUp(rightHandle!, {
        pointerId: 22,
        clientX: 240,
        screenX: 240,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('');
      });

      expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
      expect(onComposerImportImage).not.toHaveBeenCalled();
      expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();

      const leftHandle = container.querySelector<HTMLDivElement>('[data-compact-resize-side="left"]');
      expect(leftHandle).not.toBeNull();
      fireEvent.pointerDown(leftHandle!, {
        pointerId: 23,
        clientX: 180,
        screenX: 500,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(leftHandle!, {
        pointerId: 23,
        clientX: 180,
        screenX: 380,
        buttons: 1,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('550px');
      });
      expect(resizeRequests.slice(-2)).toEqual([
        expect.objectContaining({
          side: 'left',
          width: 430,
          phase: 'start',
          screenRect: expect.objectContaining({ left: 0, width: 430, right: 430 }),
        }),
        expect.objectContaining({
          side: 'left',
          width: 550,
          phase: 'move',
          screenRect: expect.objectContaining({ left: -120, width: 550, right: 430 }),
        }),
      ]);
      fireEvent.pointerUp(leftHandle!, {
        pointerId: 23,
        clientX: 180,
        screenX: 380,
        buttons: 0,
        pointerType: 'mouse',
      });
    } finally {
      window.removeEventListener('neko:compact-surface-resize-request', handleResizeRequest);
    }
  });

  it('defaults compact history open and preserves history controls through visibility toggles', async () => {
    const onExportConversationClick = vi.fn();
    const message = parseChatMessage({
      id: 'assistant-history-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'History should open inline.' }],
      status: 'sent',
    });

    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        onExportConversationClick={onExportConversationClick}
      />,
    );

    expect(onExportConversationClick).not.toHaveBeenCalled();
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-geometry-hit-scope', 'children');
    expect(container.querySelector('.compact-export-history-anchor')).not.toHaveAttribute('data-compact-hit-region');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region', 'true');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region-id', 'history:message:assistant-history-1');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region-kind', 'message');
    expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
    expect(container.querySelector('.composer-panel #music-player-mount')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
    expect(container.querySelector('.compact-export-history-music-mount')).toHaveAttribute('data-music-player-mount', 'compact-history');
    expect(container.querySelector('.compact-export-history-music-mount')).toHaveAttribute('data-compact-hit-region-id', 'history:music-player');
    expect(container.querySelector('.compact-export-history-music-mount')).toHaveAttribute('data-compact-hit-region-kind', 'music');
    expect(container.querySelector('.compact-export-history-controls')).toBeNull();
    expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('data-compact-geometry-item', 'historyHandle');
    expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.compact-export-history-message')).toHaveAttribute('role', 'listitem');
    expect(container.querySelector('.compact-export-history-message')).not.toHaveAttribute('aria-pressed');
    expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('role');
    expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('aria-pressed');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-disabled', 'true');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('tabindex', '-1');
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBeNull();

    const exportButton = await clickCompactExportTool();
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('role', 'button');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-pressed', 'false');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-disabled', 'false');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('tabindex', '0');
    expect(container.querySelector('.compact-export-history-controls')).toHaveAttribute('data-compact-hit-region-id', 'history:controls');
    expect(exportButton).toHaveAttribute('aria-pressed', 'true');

    vi.useFakeTimers();
    try {
      const scroll = container.querySelector<HTMLElement>('.compact-export-history-scroll')!;
      expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');
      fireEvent.mouseEnter(scroll);
      expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');
      fireEvent.wheel(scroll, { deltaY: 80 });
      expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS);
      });
      expect(scroll).not.toHaveAttribute('data-compact-scrollbar-visible');

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'false');
      expect(exportButton).toHaveAttribute('aria-pressed', 'false');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('role');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('aria-pressed');
      expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-disabled', 'true');
      expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('tabindex', '-1');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('data-compact-hit-region');
      expect(container.querySelector('.compact-export-history-music-mount')).not.toHaveAttribute('data-compact-hit-region');
      expect(container.querySelector('.compact-export-history-controls')).toHaveAttribute('aria-disabled', 'true');
      expect(container.querySelector('.compact-export-history-controls')).not.toHaveAttribute('data-compact-hit-region');
      container.querySelectorAll<HTMLButtonElement>('.compact-export-history-control').forEach((button) => {
        expect(button).toBeDisabled();
      });
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('false');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });

      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-open', 'false');
      expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
      expect(container.querySelector('.composer-panel #music-player-mount')).not.toBeNull();
      expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
      expect(container.querySelector('[data-compact-hit-region-id^="history:"]')).toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-open', 'true');
      expect(container.querySelector('.compact-export-history-music-mount')).toHaveAttribute('data-compact-hit-region-id', 'history:music-player');
      expect(container.querySelector('.compact-export-history-controls')).toHaveAttribute('data-compact-hit-region-id', 'history:controls');
      expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'true');
      expect(exportButton).toHaveAttribute('aria-pressed', 'true');
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');
    } finally {
      vi.useRealTimers();
    }

    await clickCompactExportTool();
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-controls')).toBeNull();
    expect(exportButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('restores compact inline history from persisted open state after remount', () => {
    const message = parseChatMessage({
      id: 'assistant-history-persisted',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Keep history open after refresh.' }],
      status: 'sent',
    });
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'true');

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-controls')).toBeNull();
    expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.compact-input-tool-item-export')).toHaveAttribute('aria-pressed', 'false');
  });

  it('toggles compact history visibility as soon as the handle is pressed', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    vi.useFakeTimers();

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" />,
      );

      const handle = container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle');
      expect(handle).not.toBeNull();
      expect(handle).toHaveAttribute('aria-expanded', 'false');
      expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
      expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
      expect(container.querySelector('.composer-panel #music-player-mount')).not.toBeNull();

      fireEvent.pointerDown(handle!, { pointerType: 'mouse', button: 0 });
      expect(handle).toHaveAttribute('aria-expanded', 'true');
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
      expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
      expect(container.querySelector('.compact-export-history-music-mount')).toHaveAttribute('data-music-player-mount', 'compact-history');
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');
      expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');

      fireEvent.click(handle!);
      expect(handle).toHaveAttribute('aria-expanded', 'true');

      fireEvent.pointerDown(handle!, { pointerType: 'mouse', button: 0 });
      expect(handle).toHaveAttribute('aria-expanded', 'false');
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('false');

      fireEvent.click(handle!);
      expect(handle).toHaveAttribute('aria-expanded', 'false');

      fireEvent.click(handle!);
      expect(handle).toHaveAttribute('aria-expanded', 'true');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });

      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact export history message actions read-only', async () => {
    const onMessageAction = vi.fn();
    const message = parseChatMessage({
      id: 'assistant-history-action-readonly',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [
        { type: 'text', text: 'Choose from history only.' },
        {
          type: 'buttons',
          buttons: [
            { id: 'invite', label: 'Invite', action: 'mini_game_invite', variant: 'primary' },
          ],
        },
      ],
      status: 'sent',
    });

    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        onMessageAction={onMessageAction}
      />,
    );

    await clickCompactExportTool();
    const actionButton = container.querySelector<HTMLButtonElement>('.compact-export-history-content .message-action-button');
    expect(actionButton).not.toBeNull();

    fireEvent.click(actionButton!);

    expect(onMessageAction).not.toHaveBeenCalled();
    expect(container.querySelector('.compact-export-history-message')).not.toHaveClass('is-selected');
  });

  it('keeps compact inline history open without an empty state when there are no messages', async () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" messages={[]} />);

    await clickCompactExportTool();

    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-empty')).toBeNull();
    expect(container).not.toHaveTextContent('There is no conversation to export yet.');
  });

  it('applies stable casual spacing tokens to compact inline history messages', async () => {
    const firstAssistant = parseChatMessage({
      id: 'assistant-history-casual-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'First old line.' }],
      status: 'sent',
    });
    const secondAssistant = parseChatMessage({
      id: 'assistant-history-casual-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'Same role should stay visually close.' }],
      status: 'sent',
    });
    const userMessage = parseChatMessage({
      id: 'user-history-casual',
      role: 'user',
      author: 'You',
      time: '10:02',
      createdAt: 3,
      blocks: [{ type: 'text', text: 'Role switch gets a little more air.' }],
      status: 'sent',
    });
    const imageMessage = parseChatMessage({
      id: 'assistant-history-casual-image',
      role: 'assistant',
      author: 'Neko',
      time: '10:03',
      createdAt: 4,
      blocks: [{ type: 'image', url: 'https://example.com/neko.png', alt: 'Neko memory' }],
      status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[firstAssistant, secondAssistant, userMessage, imageMessage]} />,
    );

    await clickCompactExportTool();

    const first = container.querySelector<HTMLElement>('[data-compact-export-history-message-id="assistant-history-casual-1"]');
    const second = container.querySelector<HTMLElement>('[data-compact-export-history-message-id="assistant-history-casual-2"]');
    const user = container.querySelector<HTMLElement>('[data-compact-export-history-message-id="user-history-casual"]');
    const image = container.querySelector<HTMLElement>('[data-compact-export-history-message-id="assistant-history-casual-image"]');
    expect(first).toHaveAttribute('data-compact-history-group', 'first');
    expect(second).toHaveAttribute('data-compact-history-group', 'same');
    expect(user).toHaveAttribute('data-compact-history-group', 'switch');
    expect(image).toHaveAttribute('data-compact-history-complexity', 'rich');
    expect(second?.style.getPropertyValue('--compact-history-bubble-max-ratio')).toMatch(/%$/);
    expect(second?.style.getPropertyValue('--compact-history-stagger-x')).toMatch(/px$/);
    expect(user?.style.getPropertyValue('--compact-history-stagger-x')).toMatch(/^-?\d+px$/);
    const initialHistoryMessageCount = 4;
    expect(first?.style.getPropertyValue('--compact-history-enter-delay')).toBe(
      computeCompactHistoryEnterDelay(0, initialHistoryMessageCount),
    );
    expect(image?.style.getPropertyValue('--compact-history-enter-delay')).toBe(
      computeCompactHistoryEnterDelay(3, initialHistoryMessageCount),
    );
    expect(first?.style.getPropertyValue('--compact-history-exit-delay')).toBe(computeCompactHistoryExitDelay(0));
    expect(image?.style.getPropertyValue('--compact-history-exit-delay')).toBe(computeCompactHistoryExitDelay(3));
    const stableFirstEnterDelay = first?.style.getPropertyValue('--compact-history-enter-delay');
    const stableImageEnterDelay = image?.style.getPropertyValue('--compact-history-enter-delay');
    const stableOffset = second?.style.getPropertyValue('--compact-history-stagger-x');
    const stableWidth = second?.style.getPropertyValue('--compact-history-bubble-max-ratio');
    const stableRotate = second?.style.getPropertyValue('--compact-history-rotate');

    const updatedSecondAssistant = parseChatMessage({
      ...secondAssistant,
      blocks: [{ type: 'text', text: 'Same id changes text but not the casual layout tokens.' }],
    });
    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[firstAssistant, updatedSecondAssistant, userMessage, imageMessage]}
      />,
    );

    const rerenderedSecond = container.querySelector<HTMLElement>('[data-compact-export-history-message-id="assistant-history-casual-2"]');
    expect(rerenderedSecond?.style.getPropertyValue('--compact-history-stagger-x')).toBe(stableOffset);
    expect(rerenderedSecond?.style.getPropertyValue('--compact-history-bubble-max-ratio')).toBe(stableWidth);
    expect(rerenderedSecond?.style.getPropertyValue('--compact-history-rotate')).toBe(stableRotate);

    const newAssistantMessage = parseChatMessage({
      id: 'assistant-history-casual-new',
      role: 'assistant',
      author: 'Neko',
      time: '10:04',
      createdAt: 5,
      blocks: [{ type: 'text', text: 'A fresh assistant message should not replay old history.' }],
      status: 'sent',
    });
    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[firstAssistant, updatedSecondAssistant, userMessage, imageMessage, newAssistantMessage]}
      />,
    );

    expect(container.querySelector<HTMLElement>(
      '[data-compact-export-history-message-id="assistant-history-casual-1"]',
    )?.style.getPropertyValue('--compact-history-enter-delay')).toBe(stableFirstEnterDelay);
    expect(container.querySelector<HTMLElement>(
      '[data-compact-export-history-message-id="assistant-history-casual-image"]',
    )?.style.getPropertyValue('--compact-history-enter-delay')).toBe(stableImageEnterDelay);
    expect(container.querySelector<HTMLElement>(
      '[data-compact-export-history-message-id="assistant-history-casual-new"]',
    )?.style.getPropertyValue('--compact-history-enter-delay')).toBe(
      computeCompactHistoryEnterDelay(4, 5),
    );
  });

  it('opens compact inline preview with disabled final actions when nothing is selected', async () => {
    const message = parseChatMessage({
      id: 'assistant-history-empty-preview',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Available but not selected.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    await clickCompactExportTool();
    fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-history-export')!);

    expect(container.querySelector('.compact-export-preview-region')).not.toBeNull();
    expect(container.querySelector('.compact-export-preview-empty')).toHaveTextContent('Select at least one message to export.');
    const actions = Array.from(container.querySelectorAll<HTMLButtonElement>('.compact-export-preview-action'));
    expect(actions).toHaveLength(2);
    expect(actions.every((button) => button.disabled)).toBe(true);
  });

  it('marks compact history as controls-collapsed so the scroll region receives the freed height', async () => {
    const message = parseChatMessage({
      id: 'assistant-history-controls',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Controls collapse should extend history.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    await clickCompactExportTool();
    const anchor = container.querySelector('.compact-export-history-anchor');
    expect(anchor).not.toHaveClass('controls-collapsed');

    await clickCompactExportTool();
    expect(anchor).toHaveClass('controls-collapsed');

    await clickCompactExportTool();
    expect(anchor).not.toHaveClass('controls-collapsed');
  });

  it('selects compact history bubbles and reuses the same selection in inline preview', async () => {
    const assistantMessage = parseChatMessage({
      id: 'assistant-history-select',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Pick this assistant message.' }],
      status: 'sent',
    });
    const userMessage = parseChatMessage({
      id: 'user-history-select',
      role: 'user',
      author: 'You',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'And this user message.' }],
      status: 'sent',
    });

    const exportWindow = window as typeof window & {
      appChatExport?: {
        buildCompactInlinePreview?: ReturnType<typeof vi.fn>;
      };
    };
    const previousBridge = exportWindow.appChatExport;
    exportWindow.appChatExport = {
      buildCompactInlinePreview: vi.fn().mockResolvedValue({
        previewKind: 'document',
        previewDocument: '<!doctype html><html><body>And this user message.</body></html>',
      }),
    };

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[assistantMessage, userMessage]} />,
      );

      const messages = container.querySelectorAll<HTMLElement>('.compact-export-history-message');
      const bubbles = container.querySelectorAll<HTMLElement>('.compact-export-history-bubble');
      fireEvent.click(bubbles[1]);

      expect(messages[1]).not.toHaveClass('is-selected');
      await clickCompactExportTool();
      fireEvent.click(bubbles[1]);
      expect(messages[1]).toHaveClass('is-selected');
      await clickCompactExportTool();
      expect(messages[1]).not.toHaveClass('is-selected');
      await clickCompactExportTool();
      fireEvent.click(bubbles[1]);
      expect(messages[1]).toHaveClass('is-selected');
      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-history-export')!);

      expect(container.querySelector('.compact-export-preview-region')).not.toBeNull();
      expect(container.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-hit-region', 'true');
      expect(container.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-hit-region-id', 'history:preview');
      expect(container.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-hit-region-kind', 'preview');

      await waitFor(() => {
        expect(container.querySelector<HTMLIFrameElement>('.compact-export-preview-frame')).not.toBeNull();
      });
      expect(exportWindow.appChatExport?.buildCompactInlinePreview).toHaveBeenCalledWith({
        messageIds: ['user-history-select'],
        format: 'markdown',
        imageStyle: 'neko',
        imageFormat: 'png',
      });
      const frame = container.querySelector<HTMLIFrameElement>('.compact-export-preview-frame');
      expect(frame?.getAttribute('srcdoc')).toContain('And this user message.');
      expect(frame?.getAttribute('srcdoc')).not.toContain('Pick this assistant message.');
    } finally {
      exportWindow.appChatExport = previousBridge;
    }
  });

  it('starts compact history image drag without selecting the source bubble', async () => {
    const imageMessage = parseChatMessage({
      id: 'assistant-history-image-drag',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'image', url: 'data:image/png;base64,aW1hZ2U=', alt: 'Memory image' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[imageMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;
    const imageBlock = container.querySelector<HTMLElement>('.message-block-image')!;
    vi.spyOn(bubble, 'getBoundingClientRect').mockReturnValue({
      left: 12,
      top: 18,
      right: 332,
      bottom: 138,
      width: 320,
      height: 120,
      x: 12,
      y: 18,
      toJSON: () => ({}),
    } as DOMRect);
    vi.spyOn(imageBlock, 'getBoundingClientRect').mockReturnValue({
      left: 40,
      top: 52,
      right: 120,
      bottom: 100,
      width: 80,
      height: 48,
      x: 40,
      y: 52,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.pointerDown(imageBlock, {
      pointerId: 31,
      clientX: 50,
      clientY: 62,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 31,
      clientX: 78,
      clientY: 74,
      buttons: 1,
      pointerType: 'mouse',
    });

    const dragLayer = document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]');
    expect(dragLayer).not.toBeNull();
    expect(dragLayer).toHaveAttribute('data-compact-drag-type', 'image');
    expect(dragLayer).toHaveAttribute('data-compact-drag-message-id', 'assistant-history-image-drag');
    expect(dragLayer).toHaveAttribute('data-compact-drag-block-index', '0');
    expect(dragLayer?.parentElement).toBe(document.body);
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-left')).toBe('68px');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-top')).toBe('64px');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-width')).toBe('80px');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-height')).toBe('48px');
    expect(message).toHaveAttribute('data-compact-history-drag-source', 'image');
    expect(message).not.toHaveClass('is-selected');

    fireEvent.pointerUp(bubble, {
      pointerId: 31,
      clientX: 78,
      clientY: 74,
      buttons: 0,
      pointerType: 'mouse',
    });
    fireEvent.click(bubble);

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-phase', 'returning');
    await waitForCompactHistoryDragLayerToClear();
    expect(message).not.toHaveClass('is-selected');
  });

  it('emits compact history drag geometry state for the desktop bridge', async () => {
    const onCompactHistoryDragStateChange = vi.fn();
    const imageMessage = parseChatMessage({
      id: 'assistant-history-drag-geometry',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'image', url: 'data:image/png;base64,aW1hZ2U=', alt: 'Memory image' }],
      status: 'sent',
    });

    const { container, rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[imageMessage]}
        onCompactHistoryDragStateChange={onCompactHistoryDragStateChange}
      />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;
    const imageBlock = container.querySelector<HTMLElement>('.message-block-image')!;
    vi.spyOn(message, 'getBoundingClientRect').mockReturnValue({
      left: 8,
      top: 12,
      right: 348,
      bottom: 156,
      width: 340,
      height: 144,
      x: 8,
      y: 12,
      toJSON: () => ({}),
    } as DOMRect);
    vi.spyOn(imageBlock, 'getBoundingClientRect').mockReturnValue({
      left: 40,
      top: 52,
      right: 120,
      bottom: 100,
      width: 80,
      height: 48,
      x: 40,
      y: 52,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.pointerDown(imageBlock, {
      pointerId: 42,
      clientX: 50,
      clientY: 62,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 42,
      clientX: 78,
      clientY: 74,
      buttons: 1,
      pointerType: 'mouse',
    });

    const activeState = onCompactHistoryDragStateChange.mock.calls
      .map(([payload]) => payload)
      .find(payload => payload.active === true);
    expect(activeState).toEqual(expect.objectContaining({
      active: true,
      phase: 'dragging',
      dragType: 'image',
      messageId: 'assistant-history-drag-geometry',
      blockIndex: 0,
      needsDesktopBounds: false,
    }));
    expect(activeState).toEqual(expect.objectContaining({
      sessionId: expect.stringMatching(/^compact-history-drag-/),
      seq: expect.any(Number),
      pointerClient: { clientX: 78, clientY: 74 },
      sourceFrameRect: expect.objectContaining({ left: 8, top: 12, width: 340, height: 144 }),
      connectionVisualRect: expect.objectContaining({ width: expect.any(Number), height: expect.any(Number) }),
      dragHitRect: expect.objectContaining({ left: 58, top: 54, width: 100, height: 68 }),
    }));
    expect(activeState?.dragVisualRect).toEqual(expect.objectContaining({ left: 68, top: 64 }));
    expect(activeState?.dragVisualRect.width).toBeGreaterThanOrEqual(80);
    expect(activeState?.dragVisualRect.height).toBeGreaterThanOrEqual(47);

    act(() => {
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-rebase', {
        detail: {
          deltaX: 30,
          deltaY: 20,
        },
      }));
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-rebase', {
        detail: {
          sessionId: 'compact-history-drag-other',
          deltaX: 30,
          deltaY: 20,
        },
      }));
    });
    expect(document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]')?.style.getPropertyValue('--compact-history-drag-left')).toBe('68px');
    expect(document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]')?.style.getPropertyValue('--compact-history-drag-top')).toBe('64px');

    act(() => {
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-rebase', {
        detail: {
          sessionId: activeState?.sessionId,
          deltaX: 30,
          deltaY: 20,
        },
      }));
    });

    const rebasedActiveStates = onCompactHistoryDragStateChange.mock.calls
      .map(([payload]) => payload)
      .filter(payload => payload.active === true);
    const rebasedState = rebasedActiveStates[rebasedActiveStates.length - 1];
    expect(rebasedState).toEqual(expect.objectContaining({
      pointerClient: { clientX: 108, clientY: 94 },
      sourceFrameRect: expect.objectContaining({ left: 38, top: 32, width: 340, height: 144 }),
      dragHitRect: expect.objectContaining({ left: 88, top: 74, width: 100, height: 68 }),
    }));
    expect(rebasedState?.dragVisualRect).toEqual(expect.objectContaining({ left: 98, top: 84 }));
    const dragLayer = document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-left')).toBe('98px');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-top')).toBe('84px');
    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[imageMessage]}
        onCompactHistoryDragStateChange={onCompactHistoryDragStateChange}
      />,
    );
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-left')).toBe('98px');
    expect(dragLayer?.style.getPropertyValue('--compact-history-drag-top')).toBe('84px');

    fireEvent.pointerUp(window, {
      pointerId: 42,
      clientX: 108,
      clientY: 94,
      buttons: 0,
      pointerType: 'mouse',
    });

    await waitFor(() => {
      expect(onCompactHistoryDragStateChange.mock.calls.some(([payload]) => (
        payload.active === false && payload.sessionId === activeState?.sessionId
      ))).toBe(true);
    });
  });

  it('starts compact history bubble drag without changing selection', async () => {
    const textMessage = parseChatMessage({
      id: 'assistant-history-bubble-drag',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Drag this whole bubble.' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[textMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

    fireEvent.pointerDown(bubble, {
      pointerId: 32,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 32,
      clientX: 74,
      clientY: 39,
      buttons: 1,
      pointerType: 'mouse',
    });

    const dragLayer = document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]');
    const anchor = container.querySelector<HTMLElement>('.compact-export-history-anchor')!;
    expect(dragLayer).not.toBeNull();
    expect(dragLayer).toHaveAttribute('data-compact-drag-type', 'bubble');
    expect(dragLayer).toHaveAttribute('data-compact-drag-message-id', 'assistant-history-bubble-drag');
    expect(dragLayer?.parentElement).toBe(document.body);
    expect(anchor.contains(dragLayer)).toBe(false);
    expect(message).toHaveAttribute('data-compact-history-drag-source', 'bubble');
    expect(message).not.toHaveClass('is-selected');

    fireEvent.pointerUp(bubble, {
      pointerId: 32,
      clientX: 74,
      clientY: 39,
      buttons: 0,
      pointerType: 'mouse',
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-phase', 'returning');
    await waitForCompactHistoryDragLayerToClear();
    expect(message).not.toHaveClass('is-selected');
  });

  it('clears compact history drag when the pointer is released outside the bubble', async () => {
    const textMessage = parseChatMessage({
      id: 'assistant-history-global-pointer-up',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Release this outside the bubble.' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[textMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

    fireEvent.pointerDown(bubble, {
      pointerId: 37,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 37,
      clientX: 78,
      clientY: 39,
      buttons: 1,
      pointerType: 'mouse',
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).not.toBeNull();

    fireEvent.pointerUp(window, {
      pointerId: 37,
      clientX: 96,
      clientY: 42,
      buttons: 0,
      pointerType: 'mouse',
    });
    fireEvent.click(bubble);

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-phase', 'returning');
    await waitForCompactHistoryDragLayerToClear();
    expect(message).not.toHaveClass('is-selected');
  });

  it('sends a compact history text bubble when dropped on the avatar range', async () => {
    const cleanupAvatar = setupAvatarDropBounds();
    const onCompactHistoryDrop = vi.fn();
    const textMessage = parseChatMessage({
      id: 'assistant-history-drop-text',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Send this memory again.' }],
      status: 'sent',
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[textMessage]}
          onCompactHistoryDrop={onCompactHistoryDrop}
        />,
      );

      await clickCompactExportTool();
      const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
      const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

      fireEvent.pointerDown(bubble, {
        pointerId: 38,
        clientX: 36,
        clientY: 36,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(bubble, {
        pointerId: 38,
        clientX: 150,
        clientY: 150,
        buttons: 1,
        pointerType: 'mouse',
      });

      const dragLayer = document.body.querySelector<HTMLElement>('[data-compact-drag-layer="true"]');
      expect(dragLayer).toHaveAttribute('data-compact-drag-over-target', 'true');

      fireEvent.pointerUp(window, {
        pointerId: 38,
        clientX: 150,
        clientY: 150,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(onCompactHistoryDrop).toHaveBeenCalledTimes(1);
      });
      expect(onCompactHistoryDrop).toHaveBeenCalledWith(expect.objectContaining({
        text: 'Send this memory again.',
        images: [],
        sourceMessageId: 'assistant-history-drop-text',
        dragType: 'bubble',
      }));
      expect(message).not.toHaveClass('is-selected');
      expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-phase', 'sending');
      await waitForCompactHistoryDragLayerToClear();
    } finally {
      cleanupAvatar();
    }
  });

  it('uses desktop avatar bounds for compact history drops in the Electron host', async () => {
    const cleanupAvatar = setupDesktopAvatarDropBounds();
    const onCompactHistoryDrop = vi.fn();
    const textMessage = parseChatMessage({
      id: 'assistant-history-desktop-drop-text',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Send this desktop memory.' }],
      status: 'sent',
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[textMessage]}
          onCompactHistoryDrop={onCompactHistoryDrop}
        />,
      );

      await clickCompactExportTool();
      const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

      fireEvent.pointerDown(bubble, {
        pointerId: 381,
        clientX: 36,
        clientY: 36,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(bubble, {
        pointerId: 381,
        clientX: 150,
        clientY: 150,
        buttons: 1,
        pointerType: 'mouse',
      });

      expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-over-target', 'true');

      fireEvent.pointerUp(window, {
        pointerId: 381,
        clientX: 150,
        clientY: 150,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(onCompactHistoryDrop).toHaveBeenCalledTimes(1);
      });
      expect(onCompactHistoryDrop).toHaveBeenCalledWith(expect.objectContaining({
        text: 'Send this desktop memory.',
        sourceMessageId: 'assistant-history-desktop-drop-text',
      }));
      await waitForCompactHistoryDragLayerToClear();
    } finally {
      cleanupAvatar();
    }
  });

  it('accepts desktop compact history drag target feedback from NEKO-PC', async () => {
    const onCompactHistoryDrop = vi.fn();
    const onCompactHistoryDragStateChange = vi.fn();
    const textMessage = parseChatMessage({
      id: 'assistant-history-desktop-feedback-drop',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Send through desktop feedback.' }],
      status: 'sent',
    });

    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[textMessage]}
        onCompactHistoryDrop={onCompactHistoryDrop}
        onCompactHistoryDragStateChange={onCompactHistoryDragStateChange}
      />,
    );

    await clickCompactExportTool();
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

    fireEvent.pointerDown(bubble, {
      pointerId: 382,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 382,
      clientX: 78,
      clientY: 39,
      buttons: 1,
      pointerType: 'mouse',
    });

    const activeState = onCompactHistoryDragStateChange.mock.calls
      .map(([payload]) => payload)
      .find(payload => payload.active === true);
    expect(activeState?.sessionId).toEqual(expect.stringMatching(/^compact-history-drag-/));

    act(() => {
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-desktop-target-change', {
        detail: {
          active: true,
          desktopOverAvatar: true,
          timestamp: Date.now(),
        },
      }));
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-desktop-target-change', {
        detail: {
          active: true,
          sessionId: 'compact-history-drag-other',
          desktopOverAvatar: true,
          timestamp: Date.now(),
        },
      }));
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-over-target', 'false');

    act(() => {
      window.dispatchEvent(new CustomEvent('neko:compact-history-drag-desktop-target-change', {
        detail: {
          active: true,
          sessionId: activeState?.sessionId,
          seq: activeState?.seq,
          desktopOverAvatar: true,
          timestamp: Date.now(),
        },
      }));
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-over-target', 'true');

    fireEvent.pointerUp(window, {
      pointerId: 382,
      clientX: 78,
      clientY: 39,
      buttons: 0,
      pointerType: 'mouse',
    });

    await waitFor(() => {
      expect(onCompactHistoryDrop).toHaveBeenCalledTimes(1);
    });
    expect(onCompactHistoryDrop).toHaveBeenCalledWith(expect.objectContaining({
      text: 'Send through desktop feedback.',
      sourceMessageId: 'assistant-history-desktop-feedback-drop',
    }));
    await waitForCompactHistoryDragLayerToClear();
  });

  it('does not send a compact history drag released outside the avatar range', async () => {
    const cleanupAvatar = setupAvatarDropBounds();
    const onCompactHistoryDrop = vi.fn();
    const textMessage = parseChatMessage({
      id: 'assistant-history-drop-miss',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Do not send this one.' }],
      status: 'sent',
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[textMessage]}
          onCompactHistoryDrop={onCompactHistoryDrop}
        />,
      );

      await clickCompactExportTool();
      const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
      const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

      fireEvent.pointerDown(bubble, {
        pointerId: 39,
        clientX: 36,
        clientY: 36,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(bubble, {
        pointerId: 39,
        clientX: 74,
        clientY: 39,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerUp(window, {
        pointerId: 39,
        clientX: 20,
        clientY: 20,
        buttons: 0,
        pointerType: 'mouse',
      });
      fireEvent.click(bubble);

      expect(onCompactHistoryDrop).not.toHaveBeenCalled();
      expect(message).not.toHaveClass('is-selected');
      expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toHaveAttribute('data-compact-drag-phase', 'returning');
      await waitForCompactHistoryDragLayerToClear();
    } finally {
      cleanupAvatar();
    }
  });

  it('sends compact history image and mixed bubble payloads through the drop callback', async () => {
    const cleanupAvatar = setupAvatarDropBounds();
    const onCompactHistoryDrop = vi.fn();
    const imageMessage = parseChatMessage({
      id: 'assistant-history-drop-image',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'image', url: 'data:image/png;base64,aW1hZ2U=', alt: 'Memory image', width: 80, height: 40 }],
      status: 'sent',
    });
    const mixedMessage = parseChatMessage({
      id: 'assistant-history-drop-mixed',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [
        { type: 'text', text: 'Look at this.' },
        { type: 'image', url: 'data:image/png;base64,bWl4ZWQ=', alt: 'Mixed image' },
      ],
      status: 'sent',
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[imageMessage, mixedMessage]}
          onCompactHistoryDrop={onCompactHistoryDrop}
        />,
      );

      await clickCompactExportTool();
      const bubbles = container.querySelectorAll<HTMLElement>('.compact-export-history-bubble');
      const imageBlock = container.querySelector<HTMLElement>('.message-block-image')!;

      fireEvent.pointerDown(imageBlock, {
        pointerId: 40,
        clientX: 36,
        clientY: 36,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(bubbles[0], {
        pointerId: 40,
        clientX: 150,
        clientY: 150,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerUp(window, {
        pointerId: 40,
        clientX: 150,
        clientY: 150,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(onCompactHistoryDrop).toHaveBeenCalledTimes(1);
      });
      expect(onCompactHistoryDrop).toHaveBeenLastCalledWith(expect.objectContaining({
        text: '',
        images: [expect.objectContaining({
          url: 'data:image/png;base64,aW1hZ2U=',
          alt: 'Memory image',
          width: 80,
          height: 40,
        })],
        dragType: 'image',
      }));

      fireEvent.pointerDown(bubbles[1], {
        pointerId: 41,
        clientX: 36,
        clientY: 80,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(bubbles[1], {
        pointerId: 41,
        clientX: 150,
        clientY: 150,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerUp(window, {
        pointerId: 41,
        clientX: 150,
        clientY: 150,
        buttons: 0,
        pointerType: 'mouse',
      });

      await waitFor(() => {
        expect(onCompactHistoryDrop).toHaveBeenCalledTimes(2);
      });
      expect(onCompactHistoryDrop).toHaveBeenLastCalledWith(expect.objectContaining({
        text: 'Look at this.',
        images: [expect.objectContaining({
          url: 'data:image/png;base64,bWl4ZWQ=',
          alt: 'Mixed image',
        })],
        sourceMessageId: 'assistant-history-drop-mixed',
        dragType: 'bubble',
      }));
    } finally {
      cleanupAvatar();
    }
  });

  it('treats compact history scroll as cancellation instead of selection or drag', async () => {
    const textMessage = parseChatMessage({
      id: 'assistant-history-scroll-cancel',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Scroll past this bubble.' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[textMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;
    const scroll = container.querySelector<HTMLElement>('.compact-export-history-scroll')!;

    fireEvent.pointerDown(bubble, {
      pointerId: 33,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'touch',
    });
    fireEvent.scroll(scroll);
    fireEvent.pointerUp(bubble, {
      pointerId: 33,
      clientX: 38,
      clientY: 72,
      buttons: 0,
      pointerType: 'touch',
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toBeNull();
    expect(message).not.toHaveClass('is-selected');
  });

  it('keeps compact history pointer movement between click and drag thresholds selectable', async () => {
    const textMessage = parseChatMessage({
      id: 'assistant-history-threshold-cancel',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Move below the drag threshold and still select.' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[textMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

    fireEvent.pointerDown(bubble, {
      pointerId: 34,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubble, {
      pointerId: 34,
      clientX: 43,
      clientY: 36,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerUp(bubble, {
      pointerId: 34,
      clientX: 43,
      clientY: 36,
      buttons: 0,
      pointerType: 'mouse',
    });

    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toBeNull();
    expect(message).toHaveClass('is-selected');
  });

  it('keeps compact history interactive blocks out of bubble drag', async () => {
    const action = vi.fn();
    const linkMessage = parseChatMessage({
      id: 'assistant-history-link-drag-ignore',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'link', url: 'https://example.com', title: 'Reference' }],
      status: 'sent',
    });
    const buttonMessage = parseChatMessage({
      id: 'assistant-history-button-drag-ignore',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{
        type: 'buttons',
        buttons: [{ id: 'act', label: 'Act', action: 'act' }],
      }],
      status: 'sent',
    });

    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[linkMessage, buttonMessage]}
        onMessageAction={action}
      />,
    );

    await clickCompactExportTool();
    const linkBlock = container.querySelector<HTMLElement>('.message-block-link')!;
    const actionButton = container.querySelector<HTMLButtonElement>('.message-action-button')!;
    const bubbles = container.querySelectorAll<HTMLElement>('.compact-export-history-bubble');

    fireEvent.pointerDown(linkBlock, {
      pointerId: 35,
      clientX: 36,
      clientY: 36,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubbles[0], {
      pointerId: 35,
      clientX: 74,
      clientY: 39,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toBeNull();

    fireEvent.pointerDown(actionButton, {
      pointerId: 36,
      clientX: 36,
      clientY: 60,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(bubbles[1], {
      pointerId: 36,
      clientX: 74,
      clientY: 63,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.body.querySelector('[data-compact-drag-layer="true"]')).toBeNull();
  });

  it('rebuilds compact inline preview when a selected message updates without changing id', async () => {
    const buildCompactInlinePreview = vi.fn().mockResolvedValue({
      previewKind: 'document',
      previewDocument: '<!doctype html><html><body>Preview</body></html>',
    });
    const exportWindow = window as typeof window & {
      appChatExport?: {
        buildCompactInlinePreview?: ReturnType<typeof vi.fn>;
      };
    };
    const previousBridge = exportWindow.appChatExport;
    exportWindow.appChatExport = { buildCompactInlinePreview };

    try {
      const baseMessage = parseChatMessage({
        id: 'assistant-history-streaming-preview',
        role: 'assistant',
        author: 'Neko',
        time: '10:00',
        createdAt: 1,
        blocks: [{ type: 'text', text: 'First preview text.' }],
        status: 'streaming',
      });
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[baseMessage]} />,
      );

      await clickCompactExportTool();
      fireEvent.click(container.querySelector<HTMLElement>('.compact-export-history-bubble')!);
      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-history-export')!);

      await waitFor(() => {
        expect(buildCompactInlinePreview).toHaveBeenCalledTimes(1);
      });

      const updatedMessage = parseChatMessage({
        ...baseMessage,
        blocks: [{ type: 'text', text: 'Updated preview text.' }],
      });
      rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[updatedMessage]} />);

      await waitFor(() => {
        expect(buildCompactInlinePreview).toHaveBeenCalledTimes(2);
      });
    } finally {
      exportWindow.appChatExport = previousBridge;
    }
  });

  it('runs compact inline export actions through the windowless export bridge', async () => {
    const assistantMessage = parseChatMessage({
      id: 'assistant-history-export-action',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Export this compact selection.' }],
      status: 'sent',
    });
    const exportWindow = window as typeof window & {
      appChatExport?: {
        buildCompactInlinePreview?: ReturnType<typeof vi.fn>;
        copyCompactInlineSelection?: ReturnType<typeof vi.fn>;
        downloadCompactInlineSelection?: ReturnType<typeof vi.fn>;
      };
    };
    const previousBridge = exportWindow.appChatExport;
    const buildCompactInlinePreview = vi.fn().mockResolvedValue({
      previewKind: 'document',
      previewDocument: '<!doctype html><html><body>Export this compact selection.</body></html>',
    });
    const copyCompactInlineSelection = vi.fn().mockResolvedValue(undefined);
    const downloadCompactInlineSelection = vi.fn().mockResolvedValue(undefined);
    exportWindow.appChatExport = {
      buildCompactInlinePreview,
      copyCompactInlineSelection,
      downloadCompactInlineSelection,
    };

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[assistantMessage]} />,
      );

      await clickCompactExportTool();
      fireEvent.click(container.querySelector<HTMLElement>('.compact-export-history-bubble')!);
      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-history-export')!);

      const preview = container.querySelector('.compact-export-preview-region');
      expect(preview).not.toBeNull();
      expect(preview).not.toHaveTextContent('Open In Window');
      await waitFor(() => {
        expect(buildCompactInlinePreview).toHaveBeenCalledWith({
          messageIds: ['assistant-history-export-action'],
          format: 'markdown',
          imageStyle: 'neko',
          imageFormat: 'png',
        });
      });

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-preview-action')!);
      await waitFor(() => {
        expect(copyCompactInlineSelection).toHaveBeenCalledWith({
          messageIds: ['assistant-history-export-action'],
          format: 'markdown',
          imageStyle: 'neko',
          imageFormat: 'png',
        });
      });

      fireEvent.click(screen.getByRole('button', { name: 'Image' }));
      fireEvent.click(screen.getByRole('button', { name: 'Fresh' }));
      fireEvent.click(screen.getByRole('button', { name: 'WebP' }));
      await waitFor(() => {
        expect(buildCompactInlinePreview).toHaveBeenCalledWith({
          messageIds: ['assistant-history-export-action'],
          format: 'image',
          imageStyle: 'poster',
          imageFormat: 'webp',
        });
      });
      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-preview-action-primary')!);

      await waitFor(() => {
        expect(downloadCompactInlineSelection).toHaveBeenCalledWith({
          messageIds: ['assistant-history-export-action'],
          format: 'image',
          imageStyle: 'poster',
          imageFormat: 'webp',
        });
      });
    } finally {
      exportWindow.appChatExport = previousBridge;
    }
  });

  it('does not select sending compact history messages', async () => {
    const sendingMessage = parseChatMessage({
      id: 'user-history-sending',
      role: 'user',
      author: 'You',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'Still sending.' }],
      status: 'sending',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[sendingMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message');
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble');
    expect(message).toHaveClass('is-disabled');
    fireEvent.click(bubble!);

    expect(message).not.toHaveClass('is-selected');
  });

  it('hides compact inline history outside compact mode and restores it when compact returns', async () => {
    const message = parseChatMessage({
      id: 'assistant-history-close',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Close me when full mode returns.' }],
      status: 'sent',
    });

    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    await clickCompactExportTool();
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();

    rerender(<App chatSurfaceMode="minimized" messages={[message]} />);

    expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
    expect(container.querySelector('[data-compact-hit-region-id^="history:"]')).toBeNull();
    expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
    expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBeNull();

    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />);

    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
  });

  it('uses compact options state while choices render over the subtitle capsule', () => {
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="default"
        choicePrompt={{
          source: 'mini_game_invite',
          options: [
            { choice: 'accept', label: 'Accept' },
            { choice: 'later', label: 'Later' },
          ],
        }}
      />,
    );

    expect(container.querySelector('.compact-chat-stage-options')).not.toBeNull();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'options');
    expect(document.body.querySelector('.compact-chat-choice-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-input-tool-toggle')).not.toBeNull();
  });

  it('marquees overflowing compact galgame option text only while hovered', () => {
    render(
      <App
        chatSurfaceMode="compact"
        galgameModeEnabled
        galgameOptions={[
          { label: 'A', text: 'This generated reply is intentionally much longer than the visible option row' },
          { label: 'B', text: 'Short reply' },
        ]}
      />,
    );

    const options = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.composer-galgame-option'));
    expect(options).toHaveLength(2);
    expect(options[0]).not.toHaveAttribute('title');
    expect(options[1]).not.toHaveAttribute('title');

    const longText = options[0].querySelector<HTMLElement>('.composer-galgame-option-text');
    const longInner = options[0].querySelector<HTMLElement>('.composer-galgame-option-text-inner');
    expect(longText).not.toBeNull();
    expect(longInner).not.toBeNull();
    Object.defineProperty(longText!, 'clientWidth', {
      configurable: true,
      value: 110,
    });
    Object.defineProperty(longInner!, 'scrollWidth', {
      configurable: true,
      value: 260,
    });

    fireEvent.mouseEnter(options[0]);

    expect(options[0]).toHaveAttribute('data-composer-option-marquee', 'true');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-distance')).toBe('178px');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-duration')).toBe('1855ms');

    fireEvent.mouseLeave(options[0]);

    expect(options[0]).not.toHaveAttribute('data-composer-option-marquee');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-distance')).toBe('');

    fireEvent.focus(options[0]);

    expect(options[0]).toHaveAttribute('data-composer-option-marquee', 'true');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-distance')).toBe('178px');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-duration')).toBe('1855ms');

    fireEvent.blur(options[0]);

    expect(options[0]).not.toHaveAttribute('data-composer-option-marquee');
    expect(options[0].style.getPropertyValue('--composer-option-marquee-distance')).toBe('');

    const shortText = options[1].querySelector<HTMLElement>('.composer-galgame-option-text');
    const shortInner = options[1].querySelector<HTMLElement>('.composer-galgame-option-text-inner');
    expect(shortText).not.toBeNull();
    expect(shortInner).not.toBeNull();
    Object.defineProperty(shortText!, 'clientWidth', {
      configurable: true,
      value: 180,
    });
    Object.defineProperty(shortInner!, 'scrollWidth', {
      configurable: true,
      value: 184,
    });

    fireEvent.mouseEnter(options[1]);

    expect(options[1]).not.toHaveAttribute('data-composer-option-marquee');

    fireEvent.focus(options[1]);

    expect(options[1]).not.toHaveAttribute('data-composer-option-marquee');
  });

  it('places compact galgame options below the surface when there is enough viewport space', async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 900,
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const appShell = container.querySelector('.app-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(appShell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();
      expect(container.querySelector('.composer-choice-layer')).toBeNull();
      expect(document.body.querySelectorAll('body > .compact-chat-choice-anchor')).toHaveLength(1);
      expect(choiceLayer).toHaveAttribute('data-compact-geometry-item', 'choice');
      expect(choiceLayer).toHaveAttribute('data-compact-geometry-owner', 'surface');

      Object.defineProperty(appShell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 100,
          left: 0,
          right: 420,
          bottom: 360,
          width: 420,
          height: 260,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 420,
          bottom: 112,
          width: 420,
          height: 112,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'below');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it('places compact galgame options above the surface when the lower viewport space is insufficient', async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 460,
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const shell = container.querySelector('.compact-chat-surface-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(shell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(shell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 100,
          left: 0,
          right: 420,
          bottom: 380,
          width: 420,
          height: 280,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 420,
          bottom: 112,
          width: 420,
          height: 112,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'above');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it('keeps compact galgame options on the current side near the placement threshold', async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 500,
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const appShell = container.querySelector('.app-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(appShell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(appShell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 96,
          left: 0,
          right: 420,
          bottom: 360,
          width: 420,
          height: 264,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 420,
          bottom: 112,
          width: 420,
          height: 112,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'above');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it('places desktop compact options below when the screen work area has room even if the compact window viewport is short', async () => {
    const originalInnerHeight = window.innerHeight;
    const desktopWindow = window as typeof window & { __nekoDesktopCompactLayout?: unknown };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 74,
    });
    desktopWindow.__nekoDesktopCompactLayout = {
      windowBounds: { x: 1043, y: 900, width: 446, height: 74 },
      workArea: { x: 0, y: 0, width: 1440, height: 1400 },
    };

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const appShell = container.querySelector('.app-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(appShell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(appShell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 8,
          left: 8,
          right: 438,
          bottom: 66,
          width: 430,
          height: 58,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 420,
          bottom: 112,
          width: 420,
          height: 112,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'below');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
    }
  });

  it('places desktop compact options above only when the screen work area below the surface is insufficient', async () => {
    const originalInnerHeight = window.innerHeight;
    const desktopWindow = window as typeof window & { __nekoDesktopCompactLayout?: unknown };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 74,
    });
    desktopWindow.__nekoDesktopCompactLayout = {
      windowBounds: { x: 1043, y: 1320, width: 446, height: 74 },
      workArea: { x: 0, y: 0, width: 1440, height: 1400 },
    };

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const appShell = container.querySelector('.app-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(appShell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(appShell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 8,
          left: 8,
          right: 438,
          bottom: 66,
          width: 430,
          height: 58,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 420,
          bottom: 112,
          width: 420,
          height: 112,
          toJSON: () => ({}),
        }),
      });
      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'above');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
    }
  });

  it('repositions compact galgame options when the compact surface moves after opening', async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 900,
    });

    let shellBottom = 360;
    const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;
    const getBoundingClientRectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function mockCompactChoiceRects(this: HTMLElement) {
        if (this.classList.contains('compact-chat-surface-shell')) {
          return {
            x: 0,
            y: shellBottom - 260,
            top: shellBottom - 260,
            left: 0,
            right: 420,
            bottom: shellBottom,
            width: 420,
            height: 260,
            toJSON: () => ({}),
          } as DOMRect;
        }
        if (this.classList.contains('compact-chat-choice-anchor')) {
          return {
            x: 0,
            y: 0,
            top: 0,
            left: 0,
            right: 420,
            bottom: 112,
            width: 420,
            height: 112,
            toJSON: () => ({}),
          } as DOMRect;
        }
        return originalGetBoundingClientRect.call(this);
      });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const shell = container.querySelector('.compact-chat-surface-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(shell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(choiceLayer!, 'scrollHeight', {
        configurable: true,
        value: 112,
      });

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'below');
      });

      shellBottom = 820;
      await act(async () => {
        window.dispatchEvent(new CustomEvent('neko:compact-surface-layout-change', {
          detail: { left: 0, top: shellBottom - 260, width: 420, height: 260 },
        }));
        await new Promise<void>(resolve => {
          window.requestAnimationFrame(() => resolve());
        });
      });

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'above');
      });
    } finally {
      Object.defineProperty(window, 'innerHeight', {
        configurable: true,
        value: originalInnerHeight,
      });
      getBoundingClientRectSpy.mockRestore();
    }
  });

  it('renders compact input without history or extra controls', () => {
    const message = parseChatMessage({
      id: 'assistant-compact-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '今天想让我陪你做什么呢？' }],
    });
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />);

    expect(container.querySelector('.compact-chat-stage-body-slot')).toHaveAttribute('data-compact-stage-fallback', 'message-list');
    expect(container.querySelector('.message-list')).toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toBeNull();
    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();
    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();
    expect(container.querySelector('.compact-chat-entry-button')).toBeNull();
    expect(container.querySelector('.compact-chat-tool-btn')).toBeNull();
  });

  it('does not request compact input for an already-input compact surface', () => {
    const onCompactChatStateChange = vi.fn();
    const message = parseChatMessage({
      id: 'assistant-compact-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '可以先说一句你今天想做什么' }],
    });

    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();

    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('input');
  });

  it('keeps revealing the final assistant tail after the same streaming message settles', async () => {
    vi.useFakeTimers();
    const fullStreamingText = '这是一段很长很长很长很长很长很长很长很长很长很长的正在说的话，不应该丢掉最后几个字';
    const streamingAssistantMessage = parseChatMessage({
      id: 'assistant-compact-streaming-tail-follow',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: fullStreamingText }],
      status: 'streaming',
    });
    const settledAssistantMessage = parseChatMessage({
      ...streamingAssistantMessage,
      status: 'sent',
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[streamingAssistantMessage]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const buttonBeforeSettle = container.querySelector('.compact-chat-capsule-button');
      expect(buttonBeforeSettle).not.toBeNull();
      expect(buttonBeforeSettle?.textContent?.length ?? 0).toBeGreaterThan(0);
      expect(buttonBeforeSettle?.textContent?.length ?? 0).toBeLessThan(fullStreamingText.length);

      rerender(
        <App chatSurfaceMode="compact" composerHidden messages={[settledAssistantMessage]} />,
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent(fullStreamingText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('focuses the compact textarea immediately after opening input mode', async () => {
    const message = parseChatMessage({
      id: 'assistant-compact-focus',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '点开就直接输入吧' }],
    });

    function CompactFocusHarness() {
      const [compactChatState, setCompactChatState] = useState<CompactChatState>('input');
      return (
        <App
          chatSurfaceMode="compact"
          compactChatState={compactChatState}
          messages={[message]}
          onCompactChatStateChange={setCompactChatState}
        />
      );
    }

    render(<CompactFocusHarness />);

    const input = await screen.findByPlaceholderText('Type a message...');
    await waitFor(() => {
      expect(input).toHaveFocus();
    });
  });

  it('does not use historical assistant or user messages as compact speech preview text', () => {
    const assistantMessage = parseChatMessage({
      id: 'assistant-compact-priority',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '先看我这边的引导内容' }],
    });
    const userMessage = parseChatMessage({
      id: 'user-compact-priority',
      role: 'user',
      author: 'You',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: '这是我刚刚发出的内容' }],
    });

    const { container } = render(
      <App chatSurfaceMode="compact" composerHidden messages={[assistantMessage, userMessage]} />,
    );

    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent('Chat content will appear here.');
    expect(container.querySelector('.compact-chat-capsule-button')).not.toHaveTextContent('先看我这边的引导内容');
    expect(container.querySelector('.compact-chat-capsule-button')).not.toHaveTextContent('这是我刚刚发出的内容');
  });

  it('does not reveal streaming compact text before speech playback starts', () => {
    const streamingText = '这是猫娘正在说的一整段内容，用来确认紧凑态显示当前流式消息时不会先把尾端省略掉。'.repeat(3);
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-full',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'true');
    expect(preview?.textContent ?? '').toBe('');
  });

  it('shows tutorial guide streaming text in the compact capsule immediately', () => {
    const initialText = '先点这里打开对话。';
    const updatedText = '先点这里打开对话，然后输入一句问候，后面这一长串教程台词也要自动向左滚动，让最新内容进入胶囊可视区域。';
    const initialMessage = parseChatMessage({
      id: 'yui-guide-chat-compact-input',
      role: 'assistant',
      author: 'YUI',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: initialText }],
      status: 'streaming',
    });
    const updatedMessage = parseChatMessage({
      ...initialMessage,
      blocks: [{ type: 'text', text: updatedText }],
    });

    const { container, rerender } = render(
      <App chatSurfaceMode="compact" composerHidden messages={[initialMessage]} />,
    );

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).not.toBeNull();
    Object.defineProperty(preview, 'scrollWidth', {
      configurable: true,
      value: 320,
    });
    Object.defineProperty(preview, 'clientWidth', {
      configurable: true,
      value: 100,
    });
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'false');
    expect(preview).toHaveAttribute('data-compact-preview-scrollable', 'true');
    expect(preview).toHaveTextContent(initialText);

    rerender(<App chatSurfaceMode="compact" composerHidden messages={[updatedMessage]} />);

    expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(updatedText);
    expect((container.querySelector('.compact-chat-capsule-text') as HTMLSpanElement).scrollLeft).toBe(320);
  });

  it('does not replay tutorial guide text animation when a later stream patch arrives', async () => {
    vi.useFakeTimers();
    try {
      const partialText = '这一句已经显示到中段。';
      const fullText = '这一句已经显示到中段。后面继续追加的教程台词应该直接接上当前流式文本，而不是先回到开头再快速滚动。';
      const partialMessage = parseChatMessage({
        id: 'yui-guide-progressive-compact-line',
        role: 'assistant',
        author: 'YUI',
        time: '10:01',
        createdAt: 2,
        blocks: [{ type: 'text', text: partialText }],
        status: 'streaming',
      });
      const fullMessage = parseChatMessage({
        ...partialMessage,
        blocks: [{ type: 'text', text: fullText }],
      });

      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[partialMessage]} />,
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(120);
      });
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(partialText);

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[fullMessage]} />);

      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(fullText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('falls back to revealing compact streaming text when playback state never arrives', async () => {
    vi.useFakeTimers();
    const streamingText = '主动搭话进入紧凑态时，即使语音播放状态没有及时到达，也应该显示这段文本。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-proactive-fallback',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1400);
      });

      const visibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(visibleLength).toBeGreaterThan(0);
      expect(visibleLength).toBeLessThan(streamingText.length);
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, visibleLength),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps proactive compact speech focused on the current turn instead of an old assistant reply', async () => {
    vi.useFakeTimers();
    const previousAssistantText = '上一轮猫娘已经说完的话，不应该混进这次主动搭话。';
    const currentProactiveText = '现在主动搭话正在说的新内容，紧凑框应该从这里开始显示。';
    const previousAssistantMessage = parseChatMessage({
      id: 'assistant-compact-previous-turn',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: previousAssistantText }],
      status: 'sent',
    });
    const currentProactiveMessage = parseChatMessage({
      id: 'assistant-compact-current-proactive',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 60000,
      blocks: [{ type: 'text', text: currentProactiveText }],
      status: 'streaming',
    });

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[previousAssistantMessage, currentProactiveMessage]} />,
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1400);
      });

      const previewText = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(previewText.length).toBeGreaterThan(0);
      expect(previewText).toBe(currentProactiveText.slice(0, previewText.length));
      expect(previewText).not.toContain(previousAssistantText.slice(0, 4));
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the compact caption moving forward when a new bubble joins the same turn instead of replaying it', async () => {
    vi.useFakeTimers();
    const firstBubbleText = '猫娘先说出来的第一段内容，紧凑输入条会逐字显示这一句。';
    const secondBubbleText = '紧接着猫娘又补了第二段，字幕应该接着往后追加而不是从头重播。';
    const makeBubble = (id: string, text: string, status: 'streaming' | 'sent', createdAt: number) =>
      parseChatMessage({
        id,
        role: 'assistant',
        author: 'Neko',
        time: '10:01',
        createdAt,
        blocks: [{ type: 'text', text }],
        status,
      });

    try {
      const firstStreaming = makeBubble('assistant-compact-turn-bubble-1', firstBubbleText, 'streaming', 2);
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreaming]} />,
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1400);
      });

      const revealedBefore = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedBefore.length).toBeGreaterThan(0);
      expect(firstBubbleText.startsWith(revealedBefore)).toBe(true);

      // Same turn (createdAt within the merge window): the merged preview re-keys
      // to the new bubble's id, but the caption must keep the already-revealed
      // prefix and continue, not rewind to empty and replay the whole turn.
      const firstSent = makeBubble('assistant-compact-turn-bubble-1', firstBubbleText, 'sent', 2);
      const secondStreaming = makeBubble('assistant-compact-turn-bubble-2', secondBubbleText, 'streaming', 5);
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent, secondStreaming]} />);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });

      const revealedAfter = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      const combinedText = `${firstBubbleText} ${secondBubbleText}`;
      // Did not replay from zero, and what stays on screen is a forward
      // continuation of what was already revealed.
      expect(revealedAfter.length).toBeGreaterThanOrEqual(revealedBefore.length);
      expect(revealedAfter.startsWith(revealedBefore)).toBe(true);
      expect(combinedText.startsWith(revealedAfter)).toBe(true);

      // And it keeps moving forward into the appended bubble — proving the
      // caption source switched to the merged turn rather than freezing on the
      // first bubble's revealed prefix.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
      });

      const revealedLater = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedLater.length).toBeGreaterThan(firstBubbleText.length);
      expect(combinedText.startsWith(revealedLater)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps appending when a new bubble joins a speech-revealed turn with no further playback signal', async () => {
    vi.useFakeTimers();
    const firstBubbleText = '第一段由语音播放揭示完。';
    const secondBubbleText = '第二段同 turn 追加，但这次没有任何播放状态或不可用事件到达。';
    const makeBubble = (id: string, text: string, status: 'streaming' | 'sent', createdAt: number) =>
      parseChatMessage({
        id,
        role: 'assistant',
        author: 'Neko',
        time: '10:01',
        createdAt,
        blocks: [{ type: 'text', text }],
        status,
      });

    try {
      const firstStreaming = makeBubble('assistant-compact-speech-turn-bubble-1', firstBubbleText, 'streaming', 2);
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreaming]} />,
      );

      // First bubble is revealed by real speech playback (not the fallback path).
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      // Playback ends — the first bubble settles to its full text.
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: false,
            audioContextTime: 1,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
      const revealedBefore = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedBefore.length).toBeGreaterThan(0);

      // Same-turn bubble arrives, but NO further playback state and NO
      // speech-unavailable event. The fallback safety net must still reveal the
      // appended text instead of freezing on the seeded prefix.
      const firstSent = makeBubble('assistant-compact-speech-turn-bubble-1', firstBubbleText, 'sent', 2);
      const secondStreaming = makeBubble('assistant-compact-speech-turn-bubble-2', secondBubbleText, 'streaming', 5);
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent, secondStreaming]} />);

      // The same-turn append should start moving immediately from the seeded
      // prefix instead of waiting for the fallback safety timer.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      const revealedImmediately = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedImmediately.length).toBeGreaterThan(revealedBefore.length);
      expect(`${firstBubbleText} ${secondBubbleText}`.startsWith(revealedImmediately)).toBe(true);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
      });

      const revealedLater = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedLater.length).toBeGreaterThan(firstBubbleText.length);
      expect(`${firstBubbleText} ${secondBubbleText}`.startsWith(revealedLater)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('uses compact caption events as the live same-turn display source without waiting for message bubbles', async () => {
    vi.useFakeTimers();
    const firstCaption = '第一句由紧凑字幕事件直接驱动显示。';
    const secondCaption = '第二句只通过同 turn 字幕事件追加，不等待历史气泡入队。';

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
          detail: {
            turnId: 'compact-caption-event-turn',
            source: 'test',
          },
        }));
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail: {
            turnId: 'compact-caption-event-turn',
            segmentId: 'compact-caption-event-turn:segment:1',
            text: firstCaption,
          },
        }));
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-caption-event-turn',
            playbackTurnId: 'compact-caption-event-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });

      const revealedBefore = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(revealedBefore.length).toBeGreaterThan(0);
      expect(firstCaption.startsWith(revealedBefore)).toBe(true);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail: {
            turnId: 'compact-caption-event-turn',
            segmentId: 'compact-caption-event-turn:segment:2',
            text: secondCaption,
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      const revealedAfter = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      const mergedCaption = `${firstCaption} ${secondCaption}`;
      expect(revealedAfter.length).toBeGreaterThan(revealedBefore.length);
      expect(revealedAfter.startsWith(revealedBefore)).toBe(true);
      expect(mergedCaption.startsWith(revealedAfter)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('replaces compact caption updates for the same segment instead of repeating prefixes', async () => {
    vi.useFakeTimers();
    const firstCaption = '第一句。';
    const secondPartialCaption = '第二句。';
    const secondFullCaption = '第二句话补全。';
    const mergedCaption = `${firstCaption} ${secondFullCaption}`;

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
          detail: {
            turnId: 'compact-caption-segment-turn',
            source: 'test',
          },
        }));
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail: {
            turnId: 'compact-caption-segment-turn',
            segmentId: 'compact-caption-segment-turn:segment:1',
            text: firstCaption,
          },
        }));
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail: {
            turnId: 'compact-caption-segment-turn',
            segmentId: 'compact-caption-segment-turn:segment:2',
            text: secondPartialCaption,
          },
        }));
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
          detail: {
            turnId: 'compact-caption-segment-turn',
            segmentId: 'compact-caption-segment-turn:segment:2',
            text: secondFullCaption,
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable', {
          detail: {
            turnId: 'compact-caption-segment-turn',
            source: 'test',
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });

      const previewText = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(previewText).toBe(mergedCaption);
      expect(previewText).not.toContain(`${secondPartialCaption} ${secondFullCaption}`);
    } finally {
      vi.useRealTimers();
    }
  });

  it('reveals compact streaming text when assistant speech is unavailable', async () => {
    vi.useFakeTimers();
    const streamingText = '语音不可用时，紧凑态仍然应该用文本速度显示猫娘正在说的内容。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-speech-unavailable',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable', {
          detail: {
            code: 'TTS_CONNECTION_FAILED',
            source: 'tts_status',
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(visibleLength).toBeGreaterThan(0);
      expect(visibleLength).toBeLessThan(streamingText.length);
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, visibleLength),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('reveals streaming compact text from actual speech playback at a readable clock', async () => {
    vi.useFakeTimers();
    const streamingText = '猫娘正在按语音播放进度显示这一整段内容。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-progress',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(visibleLength).toBeGreaterThanOrEqual(7);
      expect(visibleLength).toBeLessThanOrEqual(8);
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, visibleLength),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores speech playback state from a different assistant turn', async () => {
    vi.useFakeTimers();
    const streamingText = '当前 turn 的语音播放状态才能推动这段紧凑字幕。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-progress-turn',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      turnId: 'compact-current-playback-turn',
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-previous-playback-turn',
            playbackTurnId: 'compact-previous-playback-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 5,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-current-playback-turn',
            playbackTurnId: 'compact-current-playback-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(visibleLength).toBeGreaterThan(0);
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, visibleLength),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not flash the previous sentence when the next assistant sentence streams under a new turn id', async () => {
    vi.useFakeTimers();
    const firstSentence = '第一句话已经说完了，不能在第二句话开始时闪回来。';
    const secondSentence = '第二句话现在开始说，紧凑字幕只能显示这句的前缀。';
    const firstStreaming = parseChatMessage({
      id: 'assistant-compact-segment-first',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      turnId: 'compact-first-segment-turn',
      blocks: [{ type: 'text', text: firstSentence }],
      status: 'streaming',
    });
    const firstSent = parseChatMessage({
      ...firstStreaming,
      status: 'sent',
    });
    const secondStreaming = parseChatMessage({
      id: 'assistant-compact-segment-second',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 5,
      turnId: 'compact-second-segment-turn',
      blocks: [{ type: 'text', text: secondSentence }],
      status: 'streaming',
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreaming]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-first-segment-turn',
            playbackTurnId: 'compact-first-segment-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      const visibleBeforeFirstSettle = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(visibleBeforeFirstSettle.length).toBeGreaterThan(0);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-ending', {
          detail: {
            turnId: 'compact-first-segment-turn',
            source: 'test',
          },
        }));
      });
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent]} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe(visibleBeforeFirstSettle);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
          detail: {
            turnId: 'compact-second-segment-turn',
            source: 'test',
          },
        }));
      });
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent]} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent, secondStreaming]} />);
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-second-segment-turn',
            playbackTurnId: 'compact-second-segment-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });

      const previewText = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(previewText).not.toContain(firstSentence.slice(0, 6));
      expect(secondSentence.startsWith(previewText)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not use a stale streaming message from an older turn during a new-turn gap', async () => {
    vi.useFakeTimers();
    const previousTurnText = '上一轮残留的 streaming 气泡绝不能在新分句空窗里闪出来。';
    const firstSentence = '当前第一句话刚说完，等待第二句话。';
    const secondSentence = '当前第二句话开始后才可以显示这里。';
    const stalePreviousStreaming = parseChatMessage({
      id: 'assistant-compact-stale-previous-streaming',
      role: 'assistant',
      author: 'Neko',
      time: '09:59',
      createdAt: 1,
      turnId: 'compact-stale-previous-turn',
      blocks: [{ type: 'text', text: previousTurnText }],
      status: 'streaming',
    });
    const firstSent = parseChatMessage({
      id: 'assistant-compact-current-first-sent',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      turnId: 'compact-current-first-turn',
      blocks: [{ type: 'text', text: firstSentence }],
      status: 'sent',
    });
    const secondStreaming = parseChatMessage({
      id: 'assistant-compact-current-second-streaming',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 3,
      turnId: 'compact-current-second-turn',
      blocks: [{ type: 'text', text: secondSentence }],
      status: 'streaming',
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[stalePreviousStreaming, firstSent]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-ending', {
          detail: {
            turnId: 'compact-current-first-turn',
            source: 'test',
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent(previousTurnText);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
          detail: {
            turnId: 'compact-current-second-turn',
            source: 'test',
          },
        }));
      });
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[stalePreviousStreaming, firstSent]} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');
      expect(container.querySelector('.compact-chat-capsule-text')).not.toHaveTextContent(previousTurnText);

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[stalePreviousStreaming, firstSent, secondStreaming]} />);
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-current-second-turn',
            playbackTurnId: 'compact-current-second-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const previewText = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(previewText).not.toContain(previousTurnText.slice(0, 8));
      expect(secondSentence.startsWith(previewText)).toBe(true);
      expect(previewText.length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the same-turn compact caption merged after the turn-ending boundary', async () => {
    vi.useFakeTimers();
    const firstSentence = '同一轮第一句话已经结束。';
    const secondSentence = '同一轮第二句话延迟进入队列后才应该显示。';
    const firstStreaming = parseChatMessage({
      id: 'assistant-compact-same-turn-first-streaming',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      turnId: 'compact-same-delayed-turn',
      blocks: [{ type: 'text', text: firstSentence }],
      status: 'streaming',
    });
    const firstSent = parseChatMessage({
      ...firstStreaming,
      status: 'sent',
    });
    const secondStreaming = parseChatMessage({
      id: 'assistant-compact-same-turn-second-streaming',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 3,
      turnId: 'compact-same-delayed-turn',
      blocks: [{ type: 'text', text: secondSentence }],
      status: 'streaming',
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreaming]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-same-delayed-turn',
            playbackTurnId: 'compact-same-delayed-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      const visibleBeforeFirstSettle = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(visibleBeforeFirstSettle.length).toBeGreaterThan(0);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-turn-ending', {
          detail: {
            turnId: 'compact-same-delayed-turn',
            source: 'test',
          },
        }));
      });
      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent]} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe(visibleBeforeFirstSettle);

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSent, secondStreaming]} />);
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            turnId: 'compact-same-delayed-turn',
            playbackTurnId: 'compact-same-delayed-turn',
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 1,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const previewText = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      const mergedText = `${firstSentence} ${secondSentence}`;
      expect(mergedText.startsWith(previewText)).toBe(true);
      expect(previewText.length).toBeGreaterThan(0);
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-compact-same-turn-first-streaming"]')).not.toBeNull();
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-compact-same-turn-second-streaming"]')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores speech-unavailable events from a different assistant turn', async () => {
    vi.useFakeTimers();
    const streamingText = '当前 turn 的语音不可用事件才能触发紧凑字幕兜底。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-unavailable-turn',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      turnId: 'compact-current-unavailable-turn',
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable', {
          detail: {
            turnId: 'compact-previous-unavailable-turn',
            source: 'test',
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent ?? '').toBe('');

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable', {
          detail: {
            turnId: 'compact-current-unavailable-turn',
            source: 'test',
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(visibleLength).toBeGreaterThan(0);
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, visibleLength),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not move compact speech text backwards when the scheduled audio window grows', async () => {
    vi.useFakeTimers();
    const streamingText = '这段文字用于确认后续音频片段延长总播放窗口时，已经显示的文字不会倒退。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-monotonic',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const firstVisibleLength = container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0;
      expect(firstVisibleLength).toBeGreaterThan(0);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 1,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 20,
            updatedAt: Date.now(),
          },
        }));
      });

      expect(container.querySelector('.compact-chat-capsule-text')?.textContent?.length ?? 0)
        .toBeGreaterThanOrEqual(firstVisibleLength);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not reveal a long compact speech text too quickly during a short early audio window', async () => {
    const streamingText = '这是一段比较长的猫娘台词，用来确认音频刚开始只排程了很短一小段时，文字不会突然全部快速打出来。'.repeat(2);
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-short-window',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

    act(() => {
      window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
        detail: {
          active: true,
          audioContextTime: 0.2,
          playbackStartAudioTime: 0,
          playbackEndAudioTime: 0.2,
          updatedAt: Date.now(),
        },
      }));
    });

    const readableDuration = streamingText.length / 8;
    const expectedLength = Math.ceil(streamingText.length * (0.2 / readableDuration));
    await waitFor(() => {
      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(
        streamingText.slice(0, expectedLength),
      );
      expect(container.querySelector('.compact-chat-capsule-text')?.textContent?.length).toBeLessThan(streamingText.length);
    });
  });

  it('keeps the completed streaming text visible after speech playback ends', async () => {
    vi.useFakeTimers();
    const streamingText = '这句话已经跟随语音显示完成，语音结束后仍然应该留在紧凑对话框里。';
    const message = parseChatMessage({
      id: 'assistant-compact-streaming-finished',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: false,
            audioContextTime: 10,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });

      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(streamingText);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: false,
            audioContextTime: 10,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(streamingText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('combines consecutive streaming assistant messages as one compact speech text', async () => {
    vi.useFakeTimers();
    const firstStreamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-combined-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: '第一段不要被切走。' }],
      status: 'streaming',
    });
    const secondStreamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-combined-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 3,
      blocks: [{ type: 'text', text: '第二段应该接在后面。' }],
      status: 'streaming',
    });
    const combinedText = '第一段不要被切走。 第二段应该接在后面。';

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreamingMessage, secondStreamingMessage]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(combinedText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the settled first assistant sentence with the active streaming sentence in compact speech text', async () => {
    vi.useFakeTimers();
    const firstSettledMessage = parseChatMessage({
      id: 'assistant-compact-streaming-mixed-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: '第一句话已经先显示出来。' }],
      status: 'sent',
    });
    const secondStreamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-mixed-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 3,
      blocks: [{ type: 'text', text: '第二句话还在继续播报。' }],
      status: 'streaming',
    });
    const combinedText = '第一句话已经先显示出来。 第二句话还在继续播报。';

    try {
      const { container } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstSettledMessage, secondStreamingMessage]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(combinedText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact speech mode when the latest streaming tail settles in a multi-message turn', async () => {
    vi.useFakeTimers();
    const firstSettledMessage = parseChatMessage({
      id: 'assistant-compact-streaming-tail-settle-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: '第一句话已经稳定。' }],
      status: 'sent',
    });
    const secondStreamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-tail-settle-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 3,
      blocks: [{ type: 'text', text: '第二句话仍在播报，所以不能提前切回普通截断预览。'.repeat(3) }],
      status: 'streaming',
    });
    const secondSentMessage = parseChatMessage({
      ...secondStreamingMessage,
      status: 'sent',
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstSettledMessage, secondStreamingMessage]} />,
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleBeforeSettle = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(visibleBeforeSettle.length).toBeGreaterThan(0);

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[firstSettledMessage, secondSentMessage]} />);

      const preview = container.querySelector('.compact-chat-capsule-text');
      expect(preview).toHaveAttribute('data-compact-preview-streaming', 'true');
      expect(preview?.textContent).toBe(visibleBeforeSettle);
      expect(preview?.textContent?.endsWith('...')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not use a settled assistant message as compact speech preview text', () => {
    const settledText = '这是猫娘已经说完的一整段内容，用来确认紧凑态在非流式状态下仍然保持有限预览，不重新变成长聊天框。'.repeat(3);
    const message = parseChatMessage({
      id: 'assistant-compact-settled-bounded',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: settledText }],
      status: 'sent',
    });

    const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'false');
    expect(preview).toHaveTextContent('Chat content will appear here.');
    expect(preview).not.toHaveTextContent(settledText.slice(0, 20));
  });

  it('keeps compact speech display active when a playing message settles from streaming to sent', async () => {
    vi.useFakeTimers();
    const streamingText = '猫娘这一整句还在播报中，消息状态提前变成已发送时也不能闪回旧版普通预览。'.repeat(2);
    const streamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-settles',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });
    const sentMessage = parseChatMessage({
      ...streamingMessage,
      status: 'sent',
    });

    try {
      const { container, rerender } = render(<App chatSurfaceMode="compact" composerHidden messages={[streamingMessage]} />);

      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      const visibleBeforeSettle = container.querySelector('.compact-chat-capsule-text')?.textContent ?? '';
      expect(visibleBeforeSettle.length).toBeGreaterThan(0);

      rerender(<App chatSurfaceMode="compact" composerHidden messages={[sentMessage]} />);

      const preview = container.querySelector('.compact-chat-capsule-text');
      expect(preview).toHaveAttribute('data-compact-preview-streaming', 'true');
      expect(preview?.textContent).toBe(visibleBeforeSettle);
      expect(preview?.textContent?.endsWith('...')).toBe(false);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      expect(preview).toHaveTextContent(streamingText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the latest streaming tail visible when the compact preview grows', async () => {
    vi.useFakeTimers();
    const firstStreamingText = '前半段已经正常显示，后半段正在继续';
    const finalStreamingText = `${firstStreamingText}，最后几个字也要进入可视区域`;
    const firstStreamingMessage = parseChatMessage({
      id: 'assistant-compact-streaming-tail',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: firstStreamingText }],
      status: 'streaming',
    });
    const finalStreamingMessage = parseChatMessage({
      ...firstStreamingMessage,
      blocks: [{ type: 'text', text: finalStreamingText }],
    });

    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" composerHidden messages={[firstStreamingMessage]} />,
      );
      const preview = container.querySelector('.compact-chat-capsule-text') as HTMLSpanElement;
      expect(preview).not.toBeNull();
      Object.defineProperty(preview, 'scrollWidth', {
        configurable: true,
        value: 320,
      });

      rerender(
        <App chatSurfaceMode="compact" composerHidden messages={[finalStreamingMessage]} />,
      );
      act(() => {
        window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
          detail: {
            active: true,
            audioContextTime: 0,
            playbackStartAudioTime: 0,
            playbackEndAudioTime: 10,
            updatedAt: Date.now(),
          },
        }));
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(11000);
      });

      expect(preview.scrollLeft).toBe(320);
      expect(preview).toHaveTextContent(finalStreamingText);
    } finally {
      vi.useRealTimers();
    }
  });

  it('scrolls compact subtitle text with the mouse wheel', () => {
    const onCompactChatStateChange = vi.fn();
    const longText = '这是一条很长的紧凑字幕，需要通过滚轮横向查看被省略掉的后半段内容。';
    const message = parseChatMessage({
      id: 'assistant-compact-wheel-scroll',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: longText }],
      status: 'sent',
    });

    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="default"
        messages={[message]}
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );
    const preview = container.querySelector('.compact-chat-capsule-text') as HTMLSpanElement;
    expect(preview).not.toBeNull();
    Object.defineProperty(preview, 'scrollWidth', {
      configurable: true,
      value: 320,
    });
    Object.defineProperty(preview, 'clientWidth', {
      configurable: true,
      value: 100,
    });

    fireEvent.wheel(preview, { deltaY: 80 });
    expect(preview.scrollLeft).toBe(80);

    fireEvent.wheel(preview, { deltaX: 240 });
    expect(preview.scrollLeft).toBe(220);

    fireEvent.wheel(preview, { deltaY: -50 });
    expect(preview.scrollLeft).toBe(170);
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('input');
  });

  it('renders compact input as the same surface with one inline action button', () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toBeNull();
    expect(container.querySelector('.composer-bottom-bar')).toBeNull();
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
    const actionButton = screen.getByRole('button', { name: '更多工具' });
    expect(actionButton).toBeInTheDocument();
    expect(actionButton.querySelector('img')).toHaveAttribute('src', '/static/icons/dropdown_arrow.png');
    expect(actionButton.querySelector('img')).toHaveClass('compact-input-tool-toggle-icon');
  });

  it('keeps the compact chat surface visible while voice mode hides the composer input', () => {
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" composerHidden />,
    );

    expect(container.querySelector('.composer-panel')).not.toHaveStyle({ display: 'none' });
    expect(container.querySelector('.compact-chat-surface-shell')).not.toBeNull();
    expect(container.querySelector('.compact-chat-surface-frame')).toHaveAttribute('data-compact-geometry-item', 'capsule');
    expect(container.querySelector('.composer-input')).toBeNull();
  });

  it('does not expose compact galgame choices while voice mode hides the composer', () => {
    const onGalgameOptionSelect = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="options"
        composerHidden
        galgameModeEnabled
        galgameOptions={[
          { label: 'A', text: '语音模式下不应该点到这个选项' },
          { label: 'B', text: '这个也不应该出现' },
        ]}
        onGalgameOptionSelect={onGalgameOptionSelect}
      />,
    );

    expect(container.querySelector('.compact-chat-surface-shell')).not.toBeNull();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(container.querySelector('.composer-galgame-slot')).toBeNull();
    expect(container.querySelector('.composer-galgame-option')).toBeNull();
    expect(onGalgameOptionSelect).not.toHaveBeenCalled();
  });

  it('does not request compact input when the compact capsule is clicked in voice mode', () => {
    const onCompactChatStateChange = vi.fn();
    const message = parseChatMessage({
      id: 'assistant-compact-voice-no-input-entry',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '语音模式下不能进入输入态。' }],
      status: 'sent',
    });
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        composerHidden
        messages={[message]}
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const capsule = container.querySelector('.compact-chat-capsule-button');
    expect(capsule).toHaveTextContent('Chat content will appear here.');
    fireEvent.click(capsule as Element);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(container.querySelector('.composer-input')).toBeNull();
    expect(container.querySelector('.compact-input-tool-fan')).toBeNull();
    expect(onCompactChatStateChange).not.toHaveBeenCalled();
  });

  it('opens compact input tools from the subtitle capsule without entering input state', () => {
    const onCompactChatStateChange = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="default"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('input');
  });

  it('opens compact input tools from the same right-side button without submitting', () => {
    const onComposerSubmit = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onComposerSubmit={onComposerSubmit}
      />,
    );

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    fireEvent.click(actionButton);

    const fan = container.querySelector('.compact-input-tool-fan');
    const shell = container.querySelector('.compact-chat-surface-shell');
    const inlineInput = container.querySelector('[data-compact-geometry-part="inputBody"]');
    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(fan).not.toBeNull();
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(fan).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(fan).toHaveAttribute('data-compact-geometry-item', 'toolFan');
    expect(fan?.parentElement).toBe(shell);
    expect(inlineInput?.contains(fan)).toBe(false);
    expect(shell?.contains(fan)).toBe(true);
    expect(fan).not.toHaveAttribute('style');
    expect(fan?.querySelector('.compact-input-tool-fan-hit-region')).not.toBeNull();
    expect(fan?.querySelector('.compact-input-tool-wheel-charge')).not.toBeNull();
    expect(fan?.querySelectorAll('[data-compact-tool-wheel-slot="-2"], [data-compact-tool-wheel-slot="-1"], [data-compact-tool-wheel-slot="0"], [data-compact-tool-wheel-slot="1"], [data-compact-tool-wheel-slot="2"]')).toHaveLength(5);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="-2"], .compact-input-tool-item[data-compact-tool-wheel-slot="-1"], .compact-input-tool-item[data-compact-tool-wheel-slot="0"], .compact-input-tool-item[data-compact-tool-wheel-slot="1"], .compact-input-tool-item[data-compact-tool-wheel-slot="2"]')).toHaveLength(5);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-forward"]')).toHaveLength(1);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-backward"]')).toHaveLength(1);
    expect(fan?.querySelectorAll('[tabindex="0"]')).toHaveLength(5);
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
  });

  it('anchors compact avatar tool bubbles to the fan origin instead of the rotating tool item', async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));

      const fan = container.querySelector('.compact-input-tool-fan');
      const avatarToolItem = container.querySelector('.compact-input-tool-item-avatar');
      const popover = container.querySelector('#composer-tool-popover-compact');
      expect(fan).not.toBeNull();
      expect(avatarToolItem).not.toBeNull();
      expect(popover).not.toBeNull();
      expect(popover?.parentElement).toBe(fan);
      expect(avatarToolItem?.contains(popover)).toBe(false);
      expect(popover).toHaveClass('composer-icon-popover');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact avatar tool choices open after the pointer leaves the tool toggle', async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));

      const fan = container.querySelector('.compact-input-tool-fan');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(container.querySelector('#composer-tool-popover-compact')).not.toBeNull();

      fireEvent.pointerLeave(actionButton, { clientX: 96, clientY: 96, pointerType: 'mouse' });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(container.querySelector('#composer-tool-popover-compact')).not.toBeNull();

      fireEvent.pointerDown(screen.getByPlaceholderText('Type a message...'), {
        pointerId: 13,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('opens compact input tools on hover-capable pointer enter', () => {
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia();

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('opens compact input tools on mouse hover even when fine-hover media query is false', () => {
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia(false);

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      expect(actionButton).not.toBeNull();
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('closes compact input tools when a click follows hover open', () => {
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia();

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('allows hover reopen after a click close once the pointer moves outside the hover region', () => {
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia();

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(actionButton, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 48,
        bottom: 48,
        width: 48,
        height: 48,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });

      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      fireEvent.click(actionButton);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerMove(document.body, { clientX: 160, clientY: 160, pointerType: 'mouse' });
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('closes compact input tools when a tap follows hover open', async () => {
    vi.useFakeTimers();
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia();

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      // A quick tap (press + release) toggles the hover-opened fan shut.
      fireEvent.pointerDown(actionButton, { pointerId: 8, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(actionButton, { pointerId: 8, button: 0, pointerType: 'mouse' });
      fireEvent.click(actionButton);
      fireEvent.pointerLeave(actionButton, { clientX: 96, clientY: 96, pointerType: 'mouse' });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(220);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      window.matchMedia = originalMatchMedia;
      vi.useRealTimers();
    }
  });

  it('opens compact input tools on a primary pointer tap', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(actionButton, { pointerId: 9, button: 0, buttons: 1, pointerType: 'mouse' });
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    fireEvent.pointerUp(actionButton, { pointerId: 9, button: 0, pointerType: 'mouse' });
    fireEvent.click(actionButton);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });

  it('keeps compact tool actions disabled until the fan finishes opening', async () => {
    vi.useFakeTimers();
    const onComposerImportImage = vi.fn();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onComposerImportImage={onComposerImportImage}
        />,
      );

      const toggle = screen.getByRole('button', { name: '更多工具' });
      fireEvent.pointerDown(toggle, { pointerId: 9, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(toggle, { pointerId: 9, button: 0, pointerType: 'mouse' });
      fireEvent.click(toggle);
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const importButton = fan.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'false');
      fireEvent.click(importButton, { clientX: 140, clientY: 140 });
      expect(onComposerImportImage).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'true');
      fireEvent.click(importButton, { clientX: 140, clientY: 140 });
      expect(onComposerImportImage).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not open compact input tools from focus alone', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    fireEvent.focus(actionButton);

    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
  });

  it('keeps compact input tools attached to the compact surface shell when layout changes', async () => {
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: {
        surface?: { left: number; top: number; width: number; height: number };
        windowBounds?: { x: number; y: number; width: number; height: number };
      } | null;
    };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    try {
      desktopWindow.__nekoDesktopCompactLayout = {
        surface: { left: 24, top: 320, width: 420, height: 56 },
        windowBounds: { x: 10, y: 10, width: 460, height: 90 },
      };
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const actionButton = screen.getByRole('button', { name: '更多工具' });

      fireEvent.click(actionButton);
      const shell = container.querySelector('.compact-chat-surface-shell');
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      await waitFor(() => {
        expect(fan.parentElement).toBe(shell);
        expect(fan.style.left).toBe('');
        expect(fan.style.top).toBe('');
      });

      desktopWindow.__nekoDesktopCompactLayout = {
        surface: { left: 4, top: 280, width: 420, height: 56 },
        windowBounds: { x: 30, y: 50, width: 520, height: 220 },
      };
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change', {
          detail: desktopWindow.__nekoDesktopCompactLayout,
        }));
      });
      act(() => {
        window.dispatchEvent(new Event('resize'));
      });

      desktopWindow.__nekoDesktopCompactLayout = {
        surface: { left: 42, top: 330, width: 420, height: 56 },
        windowBounds: { x: 30, y: 50, width: 520, height: 220 },
      };
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change', {
          detail: desktopWindow.__nekoDesktopCompactLayout,
        }));
      });
      await waitFor(() => {
        expect(fan.parentElement).toBe(shell);
        expect(fan.style.left).toBe('');
        expect(fan.style.top).toBe('');
      });
    } finally {
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
    }
  });

  it('keeps compact tool buttons clickable and leaves the fan open after actions', async () => {
    vi.useFakeTimers();
    const onComposerImportImage = vi.fn();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onComposerImportImage={onComposerImportImage}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const actionButton = screen.getByRole('button', { name: '更多工具' });
      vi.spyOn(actionButton, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 48,
        bottom: 48,
        width: 48,
        height: 48,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 232,
        bottom: 232,
        width: 232,
        height: 232,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      const importButton = fan.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;

      fireEvent.pointerDown(importButton, { pointerId: 3, clientX: 55, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(importButton, { pointerId: 3, clientX: 55, buttons: 0, pointerType: 'mouse' });
      fireEvent.click(importButton, { clientX: 140, clientY: 140 });

      expect(onComposerImportImage).toHaveBeenCalledTimes(1);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps faded compact tool edge buttons focusable and actionable', async () => {
    vi.useFakeTimers();
    const onExportConversationClick = vi.fn();
    const onGalgameModeToggle = vi.fn();
    const message = parseChatMessage({
      id: 'assistant-edge-tool-history',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Edge tools should be reachable.' }],
      status: 'sent',
    });
    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[message]}
          onExportConversationClick={onExportConversationClick}
          onGalgameModeToggle={onGalgameModeToggle}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      const exportButton = fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement;
      const galgameButton = fan.querySelector('.compact-input-tool-item-galgame') as HTMLButtonElement;

      expect(exportButton).toHaveAttribute('data-compact-tool-wheel-slot', '-2');
      expect(exportButton).toHaveAttribute('tabindex', '0');
      expect(exportButton).toHaveAttribute('aria-hidden', 'false');
      expect(galgameButton).toHaveAttribute('data-compact-tool-wheel-slot', '2');
      expect(galgameButton).toHaveAttribute('tabindex', '0');
      expect(galgameButton).toHaveAttribute('aria-hidden', 'false');

      fireEvent.click(galgameButton, { clientX: 140, clientY: 140 });
      expect(onGalgameModeToggle).toHaveBeenCalledTimes(1);

      fireEvent.click(exportButton, { clientX: 140, clientY: 140 });
      expect(onExportConversationClick).not.toHaveBeenCalled();
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('rotates compact input tools by pointer dragging while keeping five visible buttons active', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const firstCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]');
    expect(firstCenter).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerDown(fan, { pointerId: 1, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 1, clientX: 60, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 1, clientX: 60, buttons: 0, pointerType: 'mouse' });

    const nextCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]');
    expect(nextCenter).toHaveClass('compact-input-tool-item-screenshot');
    expect(fan.querySelectorAll('[tabindex="0"]')).toHaveLength(5);
  });

  it('rotates compact input tools when dragging from a tool button without firing that button', () => {
    const onComposerImportImage = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onComposerImportImage={onComposerImportImage}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const importButton = fan.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;
    expect(importButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');

    fireEvent.pointerDown(importButton, { pointerId: 4, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(importButton, { pointerId: 4, clientX: 60, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(importButton, { pointerId: 4, clientX: 60, buttons: 0, pointerType: 'mouse' });
    fireEvent.click(importButton, { clientX: 140, clientY: 140 });

    expect(onComposerImportImage).not.toHaveBeenCalled();
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });

  it('anchors compact emoji choices above the compact wheel toggle', async () => {
    vi.useFakeTimers();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });

      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const avatarTool = fan.querySelector('.compact-input-tool-item-avatar') as HTMLDivElement;
      const emojiButton = avatarTool.querySelector('.composer-emoji-btn') as HTMLButtonElement;
      fireEvent.click(emojiButton);

      expect(avatarTool.querySelector('#composer-tool-popover-compact')).toBeNull();
      expect(fan.querySelector(':scope > #composer-tool-popover-compact')).not.toBeNull();
      expect(avatarTool).toHaveAttribute('data-compact-tool-active', 'true');
      expect(emojiButton).toHaveClass('is-active');

      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(avatarTool).toHaveAttribute('data-compact-tool-active', 'true');
      expect(avatarTool.querySelector('.composer-emoji-btn')).toHaveClass('is-active');
      expect(fan.querySelector('#composer-tool-popover-compact')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact input tools open while an active wheel drag leaves the fan range', async () => {
    vi.useFakeTimers();
    let restoreFanRect = () => {};
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });

      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 232,
        bottom: 232,
        width: 232,
        height: 232,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect);
      restoreFanRect = () => fanRectSpy.mockRestore();

      fireEvent.pointerDown(fan, {
        pointerId: 18,
        clientX: 174,
        clientY: 174,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerLeave(fan, {
        pointerId: 18,
        clientX: 520,
        clientY: 980,
        pointerType: 'mouse',
      });
      fireEvent.lostPointerCapture(fan, {
        pointerId: 18,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(window, {
        pointerId: 18,
        clientX: 520,
        clientY: 980,
        buttons: 1,
        pointerType: 'mouse',
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
      fireEvent.blur(window);
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-pointer-outside'));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.pointerUp(window, {
        pointerId: 18,
        clientX: 520,
        clientY: 980,
        buttons: 0,
        pointerType: 'mouse',
      });
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-pointer-outside'));
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      restoreFanRect();
      vi.useRealTimers();
    }
  });

  it('opens compact input tools from the larger toggle hover ring', () => {
    let restoreToggleRect = () => {};
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const toggleRectSpy = vi.spyOn(actionButton, 'getBoundingClientRect').mockReturnValue({
        left: 100,
        top: 100,
        right: 142,
        bottom: 142,
        width: 42,
        height: 42,
        x: 100,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect);
      restoreToggleRect = () => toggleRectSpy.mockRestore();

      fireEvent.pointerMove(window, {
        clientX: 87,
        clientY: 121,
        pointerType: 'mouse',
      });

      expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      restoreToggleRect();
    }
  });

  it('keeps compact input tools open inside the full circular hover range', async () => {
    vi.useFakeTimers();
    let restoreFanRect = () => {};
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 232,
        bottom: 232,
        width: 232,
        height: 232,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect);
      restoreFanRect = () => fanRectSpy.mockRestore();

      fireEvent.pointerLeave(fan, {
        clientX: 116,
        clientY: 8,
        pointerType: 'mouse',
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.pointerLeave(fan, {
        clientX: 116,
        clientY: -20,
        pointerType: 'mouse',
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      restoreFanRect();
      vi.useRealTimers();
    }
  });

  it('rotates compact input tools with wheel and vertical drag gestures', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.wheel(fan, { deltaY: 80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.wheel(fan, { deltaY: -80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.wheel(fan, { deltaY: 1 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.wheel(fan, { deltaY: -1 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerDown(fan, { pointerId: 7, clientX: 100, clientY: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 102, clientY: 132, buttons: 1, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 101, clientY: 100, buttons: 1, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerUp(fan, { pointerId: 7, clientX: 101, clientY: 100, buttons: 0, pointerType: 'mouse' });
  });

  it('stops compact input tool wheel motion on pointer release', async () => {
    vi.useFakeTimers();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

      fireEvent.pointerDown(fan, {
        pointerId: 21,
        clientX: 100,
        clientY: 100,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(16);
      });
      fireEvent.pointerMove(fan, {
        pointerId: 21,
        clientX: 76,
        clientY: 100,
        buttons: 1,
        pointerType: 'mouse',
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

      fireEvent.pointerUp(fan, {
        pointerId: 21,
        clientX: 76,
        clientY: 100,
        buttons: 0,
        pointerType: 'mouse',
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(900);
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
    } finally {
      vi.useRealTimers();
    }
  });

  it('charges compact input tool wheel after sustained one-way drag and releases backward', async () => {
    vi.useFakeTimers();
    const pointOnWheel = (angle: number) => ({
      clientX: 116 + Math.cos(angle) * 92,
      clientY: 116 + Math.sin(angle) * 92,
    });
    let restoreFanRect = () => {};
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 232,
        bottom: 232,
        width: 232,
        height: 232,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect);
      restoreFanRect = () => fanRectSpy.mockRestore();

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');
      const start = pointOnWheel(0);
      fireEvent.pointerDown(fan, {
        pointerId: 22,
        ...start,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      for (let index = 1; index <= 12; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 22,
          ...pointOnWheel(index * 0.42),
          buttons: 1,
          pointerType: 'mouse',
        });
      }
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');

      for (let index = 13; index <= 24; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 22,
          ...pointOnWheel(index * 0.42),
          buttons: 1,
          pointerType: 'mouse',
        });
      }

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-direction', 'forward');
      const beforeReleaseCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]')?.className;
      fireEvent.pointerUp(fan, {
        pointerId: 22,
        ...pointOnWheel(24 * 0.42),
        buttons: 0,
        pointerType: 'mouse',
      });

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'true');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')?.className).not.toBe(beforeReleaseCenter);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'false');
    } finally {
      restoreFanRect();
      vi.useRealTimers();
    }
  });

  it('reduces compact input tool wheel charge on opposite drag before switching direction', async () => {
    vi.useFakeTimers();
    const pointOnWheel = (angle: number) => ({
      clientX: 116 + Math.cos(angle) * 92,
      clientY: 116 + Math.sin(angle) * 92,
    });
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const charge = fan.querySelector('.compact-input-tool-wheel-charge') as HTMLDivElement;
    const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 232,
      bottom: 232,
      width: 232,
      height: 232,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);

    try {
      fireEvent.pointerDown(fan, {
        pointerId: 23,
        ...pointOnWheel(0),
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      for (let index = 1; index <= 20; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 23,
          ...pointOnWheel(index * 0.42),
          buttons: 1,
          pointerType: 'mouse',
        });
      }
      const chargedFirstAngle = Number.parseFloat(charge.style.getPropertyValue('--compact-tool-wheel-charge-first-angle'));
      const chargedSecondAngle = Number.parseFloat(charge.style.getPropertyValue('--compact-tool-wheel-charge-second-angle'));
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-direction', 'forward');

      fireEvent.pointerMove(fan, {
        pointerId: 23,
        ...pointOnWheel(19 * 0.42),
        buttons: 1,
        pointerType: 'mouse',
      });
      const reducedFirstAngle = Number.parseFloat(charge.style.getPropertyValue('--compact-tool-wheel-charge-first-angle'));
      const reducedSecondAngle = Number.parseFloat(charge.style.getPropertyValue('--compact-tool-wheel-charge-second-angle'));

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-direction', 'forward');
      expect(reducedFirstAngle + reducedSecondAngle).toBeLessThan(chargedFirstAngle + chargedSecondAngle);
      fireEvent.pointerUp(fan, {
        pointerId: 23,
        ...pointOnWheel(19 * 0.42),
        buttons: 0,
        pointerType: 'mouse',
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });
    } finally {
      fanRectSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('exposes a draggable left grip in compact input mode as part of the surface drag region', () => {
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" />,
    );
    const grip = container.querySelector('.compact-chat-input-drag-grip');
    expect(grip).not.toBeNull();
    // 握把属于本体拖拽区：自身不是 no-drag，祖先是 drag-surface，且不在任何 no-drag 子树里。
    expect(grip).not.toHaveAttribute('data-compact-no-drag');
    expect(grip!.closest('[data-compact-drag-surface="true"]')).not.toBeNull();
    expect(grip!.closest('[data-compact-no-drag="true"]')).toBeNull();
    // 仅输入态出现；胶囊态本体本身可拖，不需要单独握把。
    rerender(<App chatSurfaceMode="compact" compactChatState="default" />);
    expect(container.querySelector('.compact-chat-input-drag-grip')).toBeNull();
  });

  it('dispatches a compact surface drag-grab from the tool toggle when pressed and moved past threshold', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );
    const toggle = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    expect(toggle).not.toBeNull();
    const grabs: Array<Record<string, number>> = [];
    const onGrab = (event: Event) => grabs.push((event as CustomEvent).detail);
    window.addEventListener('neko:compact-surface-drag-grab', onGrab);
    try {
      fireEvent.pointerDown(toggle, {
        pointerId: 7, clientX: 100, clientY: 100, screenX: 300, screenY: 320,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerMove(toggle, {
        pointerId: 7, clientX: 122, clientY: 108, buttons: 1, pointerType: 'mouse',
      });
      // 拖动超阈值 → 派发一次抓取事件，锚点用按下点（不跳变）。
      expect(grabs).toHaveLength(1);
      expect(grabs[0]).toMatchObject({ clientX: 100, clientY: 100, screenX: 300, screenY: 320 });
      fireEvent.pointerUp(toggle, {
        pointerId: 7, clientX: 122, clientY: 108, buttons: 0, pointerType: 'mouse',
      });
      // 拖完补发的 click 被吞掉，不应展开轮盘。
      fireEvent.click(toggle);
      const fan = document.body.querySelector('.compact-input-tool-fan');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      window.removeEventListener('neko:compact-surface-drag-grab', onGrab);
    }
  });

  it('keeps origin drag click suppression armed across a slow drag (no timeout clear)', () => {
    vi.useFakeTimers();
    try {
      render(
        <App chatSurfaceMode="compact" compactChatState="input" />,
      );
      const toggle = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      fireEvent.pointerDown(toggle, {
        pointerId: 31, clientX: 100, clientY: 100, screenX: 300, screenY: 300,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerMove(toggle, {
        pointerId: 31, clientX: 130, clientY: 110, buttons: 1, pointerType: 'mouse',
      });
      // 慢速拖拽：跨过任何旧的固定时长窗口（曾经的 120ms 定时器会在此误清抑制标志）。
      vi.advanceTimersByTime(1000);
      fireEvent.pointerUp(toggle, {
        pointerId: 31, clientX: 130, clientY: 110, buttons: 0, pointerType: 'mouse',
      });
      // 释放后补发的 click 仍应被吞掉，轮盘不被误展开。
      fireEvent.click(toggle);
      const fan = document.body.querySelector('.compact-input-tool-fan');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('treats a stationary tap on the tool toggle as open (no drag-grab)', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );
    const toggle = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    const grabs: Event[] = [];
    const onGrab = (event: Event) => grabs.push(event);
    window.addEventListener('neko:compact-surface-drag-grab', onGrab);
    try {
      fireEvent.pointerDown(toggle, {
        pointerId: 8, clientX: 100, clientY: 100, screenX: 300, screenY: 320,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerUp(toggle, {
        pointerId: 8, clientX: 101, clientY: 100, buttons: 0, pointerType: 'mouse',
      });
      expect(grabs).toHaveLength(0);
      fireEvent.click(toggle);
      const fan = document.body.querySelector('.compact-input-tool-fan');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      window.removeEventListener('neko:compact-surface-drag-grab', onGrab);
    }
  });

  it('dispatches a drag-grab from the open wheel origin and collapses the wheel', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );
    const toggle = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    fireEvent.click(toggle);
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 232, bottom: 232, width: 232, height: 232, x: 0, y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const grabs: Array<Record<string, number>> = [];
    const onGrab = (event: Event) => grabs.push((event as CustomEvent).detail);
    window.addEventListener('neko:compact-surface-drag-grab', onGrab);
    try {
      // 在轮盘中心（origin）按下并拖动 → 移动文本框而非旋转轮盘。
      fireEvent.pointerDown(fan, {
        pointerId: 9, clientX: 10, clientY: 10, screenX: 210, screenY: 210,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 9, clientX: 32, clientY: 16, buttons: 1, pointerType: 'mouse',
      });
      expect(grabs).toHaveLength(1);
      expect(grabs[0]).toMatchObject({ clientX: 10, clientY: 10, screenX: 210, screenY: 210 });
      // 拖动是移动手势 → 轮盘收起。
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    } finally {
      window.removeEventListener('neko:compact-surface-drag-grab', onGrab);
      fanRectSpy.mockRestore();
    }
  });

  it('keeps angular wheel drag direction while crossing behind the center', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 232,
      bottom: 232,
      width: 232,
      height: 232,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);

    try {
      fireEvent.pointerDown(fan, {
        pointerId: 19,
        clientX: 31.43,
        clientY: 146.78,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 19,
        clientX: 26.05,
        clientY: 119.14,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 19,
        clientX: 29.46,
        clientY: 91.11,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerUp(fan, {
        pointerId: 19,
        clientX: 29.46,
        clientY: 91.11,
        buttons: 0,
        pointerType: 'mouse',
      });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-galgame');
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('only rotates compact input tools during an active pointer drag', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 40, buttons: 0, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerDown(fan, { pointerId: 7, clientX: 100, clientY: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 60, clientY: 102, buttons: 1, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.pointerUp(fan, { pointerId: 7, clientX: 60, clientY: 102, buttons: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 10, clientY: 102, buttons: 0, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
  });

  it('keeps compact toggle tools open and shows their active state after toggling', async () => {
    vi.useFakeTimers();
    function Harness() {
      const [galgameEnabled, setGalgameEnabled] = useState(false);
      return (
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          galgameModeEnabled={galgameEnabled}
          onGalgameModeToggle={() => setGalgameEnabled(enabled => !enabled)}
        />
      );
    }

    try {
      render(<Harness />);

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.pointerDown(fan, { pointerId: 1, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 1, clientX: 60, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(fan, { pointerId: 1, clientX: 60, buttons: 0, pointerType: 'mouse' });
      fireEvent.pointerDown(fan, { pointerId: 2, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 2, clientX: 60, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(fan, { pointerId: 2, clientX: 60, buttons: 0, pointerType: 'mouse' });

      const galgameButton = fan.querySelector('.compact-input-tool-item-galgame') as HTMLButtonElement;
      expect(galgameButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        fireEvent.click(galgameButton, { clientX: 140, clientY: 140 });
      });
      const activeGalgameButton = fan.querySelector('.compact-input-tool-item-galgame') as HTMLButtonElement;

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(activeGalgameButton).toHaveClass('is-active');
      expect(activeGalgameButton).toHaveAttribute('data-compact-tool-active', 'true');
      expect(activeGalgameButton).toHaveAttribute('aria-pressed', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes compact input tools on the second button click without leaving input state', () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    fireEvent.click(actionButton);
    fireEvent.click(actionButton);

    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    fireEvent.click(actionButton);
    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(document.body.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
  });

  it('reopens compact input tools after closing them from a tool toggle tap', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);

    const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(actionButton).not.toBeNull();

    const tapToggle = (pointerId: number) => {
      fireEvent.pointerDown(actionButton, { pointerId, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(actionButton, { pointerId, button: 0, pointerType: 'mouse' });
      fireEvent.click(actionButton);
    };

    tapToggle(21);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

    tapToggle(22);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    tapToggle(23);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });

  it('signals fan-open solid state to the desktop shell on open and close', () => {
    const openStates: Array<boolean | undefined> = [];
    const onOpenState = (event: Event) => openStates.push((event as CustomEvent).detail?.open);
    window.addEventListener('neko:compact-tool-fan-open-state-change', onOpenState);
    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;

      const tapToggle = (pointerId: number) => {
        fireEvent.pointerDown(actionButton, { pointerId, button: 0, buttons: 1, pointerType: 'mouse' });
        fireEvent.pointerUp(actionButton, { pointerId, button: 0, pointerType: 'mouse' });
        fireEvent.click(actionButton);
      };

      tapToggle(51);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(openStates).toContain(true);

      tapToggle(52);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
      expect(openStates[openStates.length - 1]).toBe(false);
    } finally {
      window.removeEventListener('neko:compact-tool-fan-open-state-change', onOpenState);
    }
  });

  it('uses hover for enter and leave while click only toggles compact input tools', async () => {
    vi.useFakeTimers();
    const originalMatchMedia = window.matchMedia;
    mockHoverCapableMatchMedia();

    try {
      render(<App chatSurfaceMode="compact" compactChatState="input" />);

      const actionButton = screen.getByRole('button', { name: '更多工具' });
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.focus(actionButton);
      fireEvent.pointerLeave(actionButton, { clientX: 96, clientY: 96, pointerType: 'mouse' });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.click(actionButton);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerMove(actionButton, { clientX: 24, clientY: 24, pointerType: 'mouse' });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      vi.spyOn(actionButton, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        top: 0,
        right: 48,
        bottom: 48,
        width: 48,
        height: 48,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      fireEvent.pointerLeave(fan, {
        clientX: 16,
        clientY: 16,
        pointerType: 'mouse',
        relatedTarget: actionButton,
      });
      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.click(actionButton);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.pointerLeave(actionButton, { clientX: 96, clientY: 96, pointerType: 'mouse' });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      fireEvent.pointerDown(document.body, {
        pointerId: 13,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

      fireEvent.pointerEnter(actionButton, { pointerType: 'mouse' });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      window.matchMedia = originalMatchMedia;
      vi.useRealTimers();
    }
  });

  it('closes compact input tools without firing a tool when the desktop fan layer covers the toggle origin', async () => {
    vi.useFakeTimers();
    const onCompactChatStateChange = vi.fn();
    const onJukeboxClick = vi.fn();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onCompactChatStateChange={onCompactChatStateChange}
          onJukeboxClick={onJukeboxClick}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      const jukeboxButton = fan.querySelector('.compact-input-tool-item-jukebox') as HTMLButtonElement;
      fireEvent.pointerDown(jukeboxButton, {
        pointerId: 12,
        clientX: 16,
        clientY: 16,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.click(jukeboxButton, { clientX: 16, clientY: 16 });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
      expect(onJukeboxClick).not.toHaveBeenCalled();
      expect(document.body.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();
      expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
    } finally {
      vi.useRealTimers();
    }
  });

  it('delays tool fan close then returns empty compact input to subtitle state when desktop pointer leaves native hit regions', async () => {
    vi.useFakeTimers();
    const onCompactChatStateChange = vi.fn();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onCompactChatStateChange={onCompactChatStateChange}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      fireEvent(window, new CustomEvent('neko:desktop-compact-pointer-outside'));

      expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(320);
      });

      expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'true');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(380);
      });

      expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
      expect(onCompactChatStateChange).toHaveBeenCalledWith('default');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact input open with draft text when desktop compact pointer leaves native hit regions', () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'draft' } });
    fireEvent(window, new CustomEvent('neko:desktop-compact-pointer-outside'));

    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
  });

  it('switches the compact action button back to send when text is entered', () => {
    const onComposerSubmit = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onComposerSubmit={onComposerSubmit}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'Test compact send' } });

    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    const sendButton = screen.getByRole('button', { name: 'Send' });
    expect(sendButton.querySelector('img')).toHaveAttribute('src', '/static/icons/send_new_icon.png');
    fireEvent.click(sendButton);

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'Test compact send' });
  });

  it('returns empty compact input to subtitle state when it loses focus', async () => {
    const onCompactChatStateChange = vi.fn();
    const outsideButton = document.createElement('button');
    document.body.appendChild(outsideButton);

    try {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const input = screen.getByPlaceholderText('Type a message...');
    input.focus();
    outsideButton.focus();
    fireEvent.blur(input, { relatedTarget: outsideButton });

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(onCompactChatStateChange).toHaveBeenCalledWith('default');
    } finally {
      outsideButton.remove();
    }
  });

  it('returns empty compact input to subtitle state on window blur even when focus remains in the compact shell', async () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const input = screen.getByPlaceholderText('Type a message...');
    input.focus();
    fireEvent(window, new Event('blur'));

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(onCompactChatStateChange).toHaveBeenCalledWith('default');
  });

  it('returns empty compact input to subtitle state when a document-level outside pointer starts', async () => {
    const onCompactChatStateChange = vi.fn();
    const outsideButton = document.createElement('button');
    document.body.appendChild(outsideButton);

    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onCompactChatStateChange={onCompactChatStateChange}
        />,
      );

      const input = screen.getByPlaceholderText('Type a message...');
      input.focus();
      fireEvent.pointerDown(outsideButton);

      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });

      expect(onCompactChatStateChange).toHaveBeenCalledWith('default');
    } finally {
      outsideButton.remove();
    }
  });

  it('keeps compact input open when blurred with unsent text', async () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'draft' } });
    input.focus();
    fireEvent.blur(input);

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
  });

  it('renders grouped assistant messages with a single visible avatar', () => {
    const firstMessage = parseChatMessage({
      id: 'assistant-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'First message' }],
    });
    const secondMessage = parseChatMessage({
      id: 'assistant-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'Second message' }],
    });

    const { container } = render(
      <MessageList
        messages={[firstMessage, secondMessage]}
        ariaLabel="Chat messages"
        failedStatusLabel="Failed"
      />,
    );

    expect(screen.getByText('First message')).toBeInTheDocument();
    expect(screen.getByText('Second message')).toBeInTheDocument();
    expect(container.querySelectorAll('.avatar-assistant').length).toBe(1);
    expect(container.querySelectorAll('.avatar-placeholder').length).toBe(1);
  });

  it('renders message status chips for streaming and failed messages', () => {
    const streamingMessage = parseChatMessage({
      id: 'streaming-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: 'Streaming message' }],
      status: 'streaming',
    });
    const failedMessage = parseChatMessage({
      id: 'failed-1',
      role: 'user',
      author: 'You',
      time: '10:01',
      blocks: [{ type: 'text', text: 'Failed message' }],
      status: 'failed',
    });

    render(
      <MessageList
        messages={[streamingMessage, failedMessage]}
        ariaLabel="Chat messages"
        failedStatusLabel="Failed"
      />,
    );

    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('submits composer text through the new submit callback', () => {
    const onComposerSubmit = vi.fn();
    renderInputApp({ onComposerSubmit });

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'Test send' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'Test send' });
  });

  it('disables composer submission while the home tutorial owns interaction', () => {
    const onComposerSubmit = vi.fn();
    renderInputApp({ composerDisabled: true, onComposerSubmit });

    const input = screen.getByPlaceholderText('Type a message...');
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { value: 'Blocked send' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('does not render a local optimistic user bubble before the host echoes messages', () => {
    const onComposerSubmit = vi.fn();
    renderInputApp({ onComposerSubmit });

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'No local optimistic bubble' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'No local optimistic bubble' });
    expect(screen.queryByText('No local optimistic bubble')).not.toBeInTheDocument();
    expect(screen.queryByText('You')).not.toBeInTheDocument();
  });

  it('renders composer tool buttons and calls the React callbacks', async () => {
    const onComposerImportImage = vi.fn();
    const onComposerScreenshot = vi.fn();

    renderInputApp({
      onComposerImportImage,
      onComposerScreenshot,
    });

    await openCompactInputTools();

    fireEvent.click(document.body.querySelector('.compact-input-tool-item-import')!);
    expect(onComposerImportImage).toHaveBeenCalledTimes(1);

    await openCompactInputTools();
    fireEvent.click(document.body.querySelector('.compact-input-tool-item-screenshot')!);
    expect(onComposerScreenshot).toHaveBeenCalledTimes(1);
  });

  it('renders pending composer attachments and removes them through callback', () => {
    const onComposerRemoveAttachment = vi.fn();

    const { container } = render(
      <App
        composerAttachments={[
          { id: 'img-1', url: 'data:image/png;base64,aaa', alt: 'Screenshot 1' },
        ]}
        onComposerRemoveAttachment={onComposerRemoveAttachment}
      />,
    );

    const viewport = container.querySelector('.composer-attachment-viewport');
    expect(viewport).toHaveClass('composer-attachment-viewport-compact');
    expect(viewport).toHaveAttribute('data-compact-geometry-item', 'attachments');
    expect(container.querySelector('.compact-chat-surface-shell .composer-attachment-viewport')).toBe(viewport);
    expect(container.querySelector('.composer-panel > .composer-attachments')).toBeNull();
    expect(screen.getByAltText('Screenshot 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove image: Screenshot 1' }));

    expect(onComposerRemoveAttachment).toHaveBeenCalledWith('img-1');
  });

  it('keeps pending composer attachments locked while the composer is disabled', () => {
    const onComposerRemoveAttachment = vi.fn();

    render(
      <App
        composerDisabled
        composerAttachments={[
          { id: 'img-1', url: 'data:image/png;base64,aaa', alt: 'Screenshot 1' },
        ]}
        onComposerRemoveAttachment={onComposerRemoveAttachment}
      />,
    );

    const removeButton = screen.getByRole('button', { name: 'Remove image: Screenshot 1' });
    expect(removeButton).toBeDisabled();
    fireEvent.click(removeButton);

    expect(onComposerRemoveAttachment).not.toHaveBeenCalled();
  });

  it('only emits avatar interactions when the pointer hits the avatar range', async () => {
    const onAvatarInteraction = vi.fn();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));

      fireEvent.pointerDown(window, { button: 0, clientX: 20, clientY: 20 });
      expect(onAvatarInteraction).not.toHaveBeenCalled();

      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });
      expect(onAvatarInteraction).toHaveBeenCalledTimes(1);
      expect(onAvatarInteraction).toHaveBeenCalledWith(expect.objectContaining({
        toolId: 'lollipop',
        actionId: 'offer',
        target: 'avatar',
        pointer: {
          clientX: 150,
          clientY: 150,
        },
      }));
      expect(onAvatarInteraction.mock.calls[0]?.[0]).not.toHaveProperty('touchZone');
    } finally {
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });

  it('derives different touch zones for different avatar hit areas', async () => {
    const onAvatarInteraction = vi.fn();
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.9);
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 110 });
      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });
      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 185 });

      expect(onAvatarInteraction.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
        toolId: 'fist',
        actionId: 'poke',
        touchZone: 'head',
      }));
      expect(onAvatarInteraction.mock.calls[1]?.[0]).toEqual(expect.objectContaining({
        toolId: 'fist',
        actionId: 'poke',
        touchZone: 'face',
      }));
      expect(onAvatarInteraction.mock.calls[2]?.[0]).toEqual(expect.objectContaining({
        toolId: 'fist',
        actionId: 'poke',
        touchZone: 'body',
      }));
    } finally {
      randomSpy.mockRestore();
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });

  it('escalates lollipop interactions from normal to burst on repeated in-range taps', async () => {
    const onAvatarInteraction = vi.fn();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));

      for (let index = 0; index < 6; index += 1) {
        fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });
      }

      expect(onAvatarInteraction).toHaveBeenCalledTimes(6);
      expect(onAvatarInteraction.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
        toolId: 'lollipop',
        actionId: 'offer',
        intensity: 'normal',
      }));
      expect(onAvatarInteraction.mock.calls[1]?.[0]).toEqual(expect.objectContaining({
        toolId: 'lollipop',
        actionId: 'tease',
        intensity: 'normal',
      }));
      expect(onAvatarInteraction.mock.calls[2]?.[0]).toEqual(expect.objectContaining({
        toolId: 'lollipop',
        actionId: 'tap_soft',
        intensity: 'rapid',
      }));
      expect(onAvatarInteraction.mock.calls[5]?.[0]).toEqual(expect.objectContaining({
        toolId: 'lollipop',
        actionId: 'tap_soft',
        intensity: 'burst',
      }));
    } finally {
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });

  it('keeps the lollipop avatar-range image through transient avatar bounds loss', async () => {
    vi.useFakeTimers();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    let boundsAvailable = true;
    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => (boundsAvailable
          ? {
            left: 100,
            right: 200,
            top: 100,
            bottom: 200,
            width: 100,
            height: 100,
          }
          : null),
      },
    });

    try {
      const { container } = renderInputApp();

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(90);
      });

      const avatarImage = () => container.querySelector('.avatar-cursor-overlay-image-lollipop');
      expect(avatarImage()).toHaveAttribute('src', '/static/icons/chat_sugar1.png');

      boundsAvailable = false;
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(90);
      });

      expect(avatarImage()).toHaveAttribute('src', '/static/icons/chat_sugar1.png');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(90);
      });

      expect(avatarImage()).toHaveAttribute('src', '/static/icons/chat_sugar1_cursor.png');
    } finally {
      vi.useRealTimers();
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });

  it('escalates fist interactions to rapid on repeated in-range taps', async () => {
    const onAvatarInteraction = vi.fn();
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.9);
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

      for (let index = 0; index < 4; index += 1) {
        fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });
      }

      expect(onAvatarInteraction).toHaveBeenCalledTimes(4);
      expect(onAvatarInteraction.mock.calls[3]?.[0]).toEqual(expect.objectContaining({
        toolId: 'fist',
        actionId: 'poke',
        intensity: 'rapid',
      }));
    } finally {
      randomSpy.mockRestore();
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });

  it('does not emit avatar interactions when compact UI overlaps the avatar hit range', async () => {
    const onAvatarInteraction = vi.fn();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    const compactButton = document.createElement('button');
    compactButton.className = 'live2d-floating-btn';
    document.body.appendChild(compactButton);

    const originalElementsFromPoint = document.elementsFromPoint;
    Object.defineProperty(document, 'elementsFromPoint', {
      configurable: true,
      value: () => [compactButton],
    });

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));
      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });

      expect(onAvatarInteraction).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(document, 'elementsFromPoint', {
        configurable: true,
        value: originalElementsFromPoint || (() => []),
      });
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      compactButton.remove();
      live2dContainer.remove();
    }
  });

  it('selects an avatar tool from the group and clears it from the active badge', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));

    expect(screen.getByRole('group', { name: 'Tool icons' })).toBeInTheDocument();

    const lollipopButton = screen.getByRole('button', { name: '棒棒糖' });
    expect(lollipopButton).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(lollipopButton);

    await openCompactInputTools();

    const activeBadgeButton = screen.getByRole('button', { name: 'Emoji: 棒棒糖' });
    expect(activeBadgeButton).toHaveClass('is-active');
    expect(screen.queryByRole('group', { name: 'Tool icons' })).not.toBeInTheDocument();

    fireEvent.click(activeBadgeButton);

    await openCompactInputTools();
    expect(screen.getByRole('button', { name: 'Emoji' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Tool icons' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Emoji: 棒棒糖' })).not.toBeInTheDocument();
  });

  it('clears the selected avatar tool from the icon badge', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    await openCompactInputTools();
    expect(screen.getByRole('button', { name: 'Emoji: 猫爪' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '恢复鼠标' }));

    await openCompactInputTools();
    expect(screen.getByRole('button', { name: 'Emoji' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Emoji: 猫爪' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复鼠标' })).not.toBeInTheDocument();
  });

  it('emits avatar tool state changes for desktop hosts', async () => {
    const onAvatarToolStateChange = vi.fn();
    renderInputApp({ onAvatarToolStateChange });

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      tool: null,
    }));

    onAvatarToolStateChange.mockClear();
    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '锤子' }));

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: true,
      toolId: 'hammer',
      variant: 'primary',
      tool: expect.objectContaining({
        id: 'hammer',
        cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
        cursorHotspotX: 50,
        cursorHotspotY: 54,
      }),
    }));
  });

  it('anchors the desktop cursor overlay to the current pointer when a tool is activated', async () => {
    const { container } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }), {
      clientX: 240,
      clientY: 320,
    });

    const overlay = container.querySelector('.avatar-cursor-overlay');
    expect(overlay).not.toBeNull();
    expect((overlay as HTMLDivElement).style.transform).toBe('translate3d(201px, 274px, 0)');
  });

  it('clears the tool cursor when the composer is hidden for voice mode', async () => {
    const { container, rerender } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App compactChatState="input" composerHidden />);

    expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('clears the tool cursor when the host issues a reset key', async () => {
    const { container, rerender } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App compactChatState="input" _toolCursorResetKey="voice-mode-reset-1" />);

    expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('preserves the outside-window cursor state when the host resets a tool cursor', async () => {
    const onAvatarToolStateChange = vi.fn();
    const { rerender } = renderInputApp({ onAvatarToolStateChange });

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    onAvatarToolStateChange.mockClear();
    fireEvent.blur(window);
    expect(onAvatarToolStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      toolId: 'fist',
      insideHostWindow: false,
    }));

    onAvatarToolStateChange.mockClear();
    rerender(<App compactChatState="input" onAvatarToolStateChange={onAvatarToolStateChange} _toolCursorResetKey="voice-mode-reset-2" />);

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      insideHostWindow: false,
    }));
  });

  it('marks the cursor back inside the host when clearing a tool from the composer', async () => {
    const onAvatarToolStateChange = vi.fn();
    renderInputApp({ onAvatarToolStateChange });

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));
    fireEvent.blur(window);

    onAvatarToolStateChange.mockClear();
    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: '恢复鼠标' }));

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      insideHostWindow: true,
    }));
  });

  it('restores the native cursor while desktop system UI owns focus', async () => {
    const { container } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    fireEvent.blur(window);

    expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');

    fireEvent.pointerMove(window, { clientX: 180, clientY: 260 });

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
  });

  it('uses the native cursor and clears it when leaving the Electron chat window', async () => {
    (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ = true;

    try {
      const { container } = renderInputApp();

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

      expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
      expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

      fireEvent.pointerOut(window, { relatedTarget: null, clientX: 160, clientY: 220 });
      expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
      expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

      fireEvent.pointerOut(window, { relatedTarget: null, clientX: -1, clientY: 220 });

      expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
      expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    } finally {
      delete (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__;
    }
  });

  it('shows the hammer secondary cursor asset on outside-range desktop clicks', async () => {
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);

    Object.assign(window, {
      live2dManager: {
        currentModel: {},
        getModelScreenBounds: () => ({
          left: 100,
          right: 200,
          top: 100,
          bottom: 200,
          width: 100,
          height: 100,
        }),
      },
    });

    try {
      const { container } = renderInputApp();

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
      fireEvent.click(screen.getByRole('button', { name: '锤子' }));

      const compactImageBefore = container.querySelector('.hammer-cursor-overlay-compact-image');
      expect(compactImageBefore).not.toBeNull();
      expect(compactImageBefore).toHaveAttribute('src', '/static/icons/chat_hammer1_cursor.png');

      fireEvent.pointerDown(window, { button: 0, clientX: 20, clientY: 20 });

      const compactImageAfter = container.querySelector('.hammer-cursor-overlay-compact-image');
      expect(compactImageAfter).not.toBeNull();
      expect(compactImageAfter).toHaveAttribute('src', '/static/icons/chat_hammer2_cursor.png');
    } finally {
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });
});
