import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App, {
  COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS,
  COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS,
  COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC,
  getCompactToolWheelReboundVisualIntensity,
  playCompactToolWheelDetentSound,
  playCompactToolWheelReboundSound,
  resetCompactToolWheelDetentAudioForTests,
} from './App';
import {
  COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS,
  computeCompactHistoryEnterDelay,
  computeCompactHistoryExitDelay,
} from './CompactExportHistoryPanel';
import MessageList from './MessageList';
import { ACTIVE_AVATAR_TOOLS_STORAGE_KEY } from './avatarTools';
import { getChatCompanionEmptyStateFallback, getChatEmptyStateFallback } from './chat-copy';
import { parseChatMessage, type CompactChatState } from './message-schema';
import compactChatStyles from './styles.css?raw';

describe('App', () => {
  const COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY = 'neko.reactChatWindow.compactExportHistoryOpen';
  const COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY = 'neko.experiment.compactHistoryDefault';
  const COMPACT_HISTORY_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.compactHistorySlotHeight';
  const COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY = 'neko.reactChatWindow.compactInputToolWheelIndex';
  const DEFAULT_CHAT_EMPTY_STATE_FALLBACK = getChatEmptyStateFallback('en');
  const DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK = getChatCompanionEmptyStateFallback('en');
  const LOCAL_STORAGE_KEYS_TO_RESET = [
    COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY,
    COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY,
    COMPACT_HISTORY_HEIGHT_STORAGE_KEY,
    COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY,
    ACTIVE_AVATAR_TOOLS_STORAGE_KEY,
  ];

  beforeEach(() => {
    LOCAL_STORAGE_KEYS_TO_RESET.forEach(key => {
      window.localStorage.removeItem(key);
    });
    // 历史 UI 用例需要历史区默认展开：A/B 变体默认值现改为「教程完全结束后」才异步套用，测试里直接给一个
    // 显式持久化「开」偏好，让历史区同步展开、与实验门控解耦（实验/门控行为另有专门用例覆盖）。
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'true');
    delete window.__NEKO_REACT_CHAT_ASSET_VERSION__;
    delete (window as Window & { NekoGameSystem?: unknown }).NekoGameSystem;
    delete (window as Window & { live2dManager?: unknown }).live2dManager;
    delete (window as Window & { __nekoDesktopCompactLayout?: unknown }).__nekoDesktopCompactLayout;
    resetCompactToolWheelDetentAudioForTests();
    document.body.style.pointerEvents = '';
    document.body.classList.remove('electron-chat-window');
    document.body.classList.remove('yui-guide-chat-buttons-disabled');
    document.body.classList.remove('yui-guide-standalone-input-shield-active');
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
    const fan = document.body.querySelector<HTMLDivElement>('.compact-input-tool-fan');
    const exportButton = document.body.querySelector<HTMLButtonElement>('.compact-input-tool-item-export');
    expect(fan).not.toBeNull();
    expect(exportButton).not.toBeNull();
    for (let index = 0; index < 7 && exportButton!.getAttribute('data-compact-tool-wheel-slot') !== '0'; index += 1) {
      fireEvent.wheel(fan!, { deltaY: 80 });
    }
    expect(exportButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');
    expect(exportButton).not.toBeDisabled();
    fireEvent.click(exportButton!);
    return exportButton!;
  };

  const rotateCompactToolToCenter = (tool: HTMLElement) => {
    const fan = document.body.querySelector<HTMLDivElement>('.compact-input-tool-fan');
    expect(fan).not.toBeNull();
    for (let index = 0; index < 7 && tool.getAttribute('data-compact-tool-wheel-slot') !== '0'; index += 1) {
      fireEvent.wheel(fan!, { deltaY: 80 });
    }
    expect(tool).toHaveAttribute('data-compact-tool-wheel-slot', '0');
  };

  const mockCompactToolFanRect = (fan: HTMLElement) => vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
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

  const compactToolWheelPoint = (angleRad: number, radius = 92) => ({
    clientX: 116 + Math.cos(angleRad) * radius,
    clientY: 116 + Math.sin(angleRad) * radius,
  });

  it('selects locale-aware chat empty state fallbacks', () => {
    expect(getChatEmptyStateFallback('zh-CN')).toBe('现在开始跟我聊天吧！');
    expect(getChatEmptyStateFallback('zh-TW')).toBe('現在開始跟我聊天吧！');
    expect(getChatEmptyStateFallback('zh-HK')).toBe('現在開始跟我聊天吧！');
    expect(getChatEmptyStateFallback('zh-MO')).toBe('現在開始跟我聊天吧！');
    expect(getChatEmptyStateFallback('zh-Hant')).toBe('現在開始跟我聊天吧！');
    expect(getChatEmptyStateFallback('en-US')).toBe('Start chatting with me now!');
    expect(getChatCompanionEmptyStateFallback('zh-CN')).toBe('（我就在这陪着你哦）');
    expect(getChatCompanionEmptyStateFallback('zh-TW')).toBe('（我就在這陪著你喔）');
    expect(getChatCompanionEmptyStateFallback('zh-HK')).toBe('（我就在這陪著你喔）');
    expect(getChatCompanionEmptyStateFallback('zh-MO')).toBe('（我就在這陪著你喔）');
    expect(getChatCompanionEmptyStateFallback('zh-Hant')).toBe('（我就在這陪著你喔）');
    expect(getChatCompanionEmptyStateFallback('en-US')).toBe("(I'm right here with you.)");
  });

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

  const mockMobileMatchMedia = (mobile = true) => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: mobile && query === '(max-width: 820px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  };

  type DesktopCompactLayoutForTest = {
    windowBounds: { x: number; y: number; width: number; height: number };
    workArea: { x: number; y: number; width: number; height: number };
  };

  const installDesktopCompactLayout = (
    layout: DesktopCompactLayoutForTest,
    viewport: { width: number; height: number },
  ) => {
    const originalMatchMedia = window.matchMedia;
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: DesktopCompactLayoutForTest | null;
    };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;

    mockMobileMatchMedia(false);
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: viewport.width });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: viewport.height });
    desktopWindow.__nekoDesktopCompactLayout = layout;

    return {
      setLayout(nextLayout: DesktopCompactLayoutForTest) {
        desktopWindow.__nekoDesktopCompactLayout = nextLayout;
      },
      get layout() {
        return desktopWindow.__nekoDesktopCompactLayout;
      },
      restore() {
        desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
        window.matchMedia = originalMatchMedia;
        Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
        Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
      },
    };
  };

  const renderInputApp = (
    props: React.ComponentProps<typeof App> = {},
  ) => render(<App compactChatState="input" {...props} />);
  const queryAvatarCursorOverlay = () => document.body.querySelector<HTMLElement>('.avatar-cursor-overlay');
  const queryHammerCursorCompactImage = () => document.body.querySelector<HTMLImageElement>('.hammer-cursor-overlay-compact-image');
  const installLive2dBoundsMock = () => {
    const testWindow = window as Window & { live2dManager?: unknown };
    const hadLive2dManager = Object.prototype.hasOwnProperty.call(testWindow, 'live2dManager');
    const previousLive2dManager = testWindow.live2dManager;

    testWindow.live2dManager = {
      currentModel: {},
      getModelScreenBounds: () => ({
        left: 100,
        right: 200,
        top: 100,
        bottom: 200,
        width: 100,
        height: 100,
      }),
    };

    return () => {
      if (hadLive2dManager) {
        testWindow.live2dManager = previousLive2dManager;
      } else {
        delete testWindow.live2dManager;
      }
    };
  };

  it('renders compact subtitle capsule by default while keeping the tool button visible', () => {
    render(<App />);

    expect(screen.queryByPlaceholderText('Type a message...')).toBeNull();
    expect(document.body.querySelector('.compact-chat-stage-default')).not.toBeNull();
    expect(document.body.querySelector('.compact-chat-capsule-button')).not.toBeNull();
    expect(document.body.querySelector('.compact-chat-capsule-button')).toHaveTextContent(DEFAULT_CHAT_EMPTY_STATE_FALLBACK);
    expect(screen.getByRole('button', { name: '更多工具' })).toBeInTheDocument();
    expect(document.body.querySelector('.compact-input-tool-fan')).not.toBeNull();
  });

  it('dispatches the frozen legacy full surface for chatSurfaceMode="full"', () => {
    // The dispatcher routes `full` to the isolated FullChatSurface, which shows
    // the full history list + full composer instead of the compact surface.
    const message = parseChatMessage({
      id: 'm-full',
      role: 'assistant',
      author: 'Neko',
      time: '12:00',
      blocks: [{ type: 'text', text: 'hello from full' }],
    });
    const { container } = render(<App chatSurfaceMode="full" messages={[message]} />);

    // Full surface: history list + full composer with the persistent textarea.
    expect(container.querySelector('.message-list')).not.toBeNull();
    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();
    expect(container.querySelector('.composer-bottom-bar')).not.toBeNull();

    // Compact surface must NOT mount — the two subtrees are mutually exclusive.
    expect(container.querySelector('.compact-chat-surface-shell')).toBeNull();
    expect(container.querySelector('.compact-chat-stage')).toBeNull();
  });

  it('hides the full composer and tools when composerHidden is true', () => {
    const message = parseChatMessage({
      id: 'm-full-hidden',
      role: 'assistant',
      author: 'Neko',
      time: '12:01',
      blocks: [{ type: 'text', text: 'goodbye state' }],
    });
    const { container } = render(<App chatSurfaceMode="full" composerHidden messages={[message]} />);

    expect(container.querySelector('.message-list')).not.toBeNull();
    expect(screen.queryByPlaceholderText('Type a message...')).toBeNull();
    expect(container.querySelector('.composer-panel')).toBeNull();
    expect(container.querySelector('.composer-bottom-tools')).toBeNull();
    expect(container.querySelector('.send-button-circle')).toBeNull();
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

  it('keeps compact capsule clicks from entering input while the tutorial locks chat buttons', () => {
    act(() => {
      document.body.classList.add('yui-guide-standalone-input-shield-active');
    });
    const onCompactChatStateChange = vi.fn();
    const { container } = render(
      <App chatSurfaceMode="compact" onCompactChatStateChange={onCompactChatStateChange} />,
    );

    fireEvent.click(container.querySelector('.compact-chat-capsule-button') as HTMLButtonElement);

    expect(container.querySelector('.composer-input')).toBeNull();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(onCompactChatStateChange).not.toHaveBeenCalled();
  });

  it('keeps compact text input and keyboard submit locked while the tutorial shield is active', async () => {
    const onComposerSubmit = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onComposerSubmit={onComposerSubmit}
      />,
    );

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'Keyboard bypass attempt' } });
    expect(input).toHaveValue('Keyboard bypass attempt');

    act(() => {
      document.body.classList.add('yui-guide-standalone-input-shield-active');
    });
    await waitFor(() => {
      expect(input).toHaveAttribute('readonly');
    });

    fireEvent.change(input, { target: { value: 'Changed while locked' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(input).toHaveValue('Keyboard bypass attempt');
    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
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
        expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('180px');
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

  it('starts compact input resize from the visible frame width instead of the carrier shell width', () => {
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
    const desktopWindow = window as typeof window & { __nekoDesktopCompactLayout?: unknown };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    desktopWindow.__nekoDesktopCompactLayout = {
      windowBounds: { x: 0, y: 0, width: 760, height: 80 },
      surfaceScreenRect: { left: 0, top: -80, right: 720, bottom: -26, width: 720, height: 54 },
    };
    const getBoundingClientRectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function mockCompactSurfaceStartWidth(this: HTMLElement) {
        if (this.classList.contains('compact-chat-surface-shell')) {
          return {
            x: 0,
            y: 0,
            top: 0,
            left: 0,
            right: 720,
            bottom: 60,
            width: 720,
            height: 60,
            toJSON: () => ({}),
          } as DOMRect;
        }
        if (this.classList.contains('compact-chat-surface-frame')) {
          return {
            x: 0,
            y: 0,
            top: 12,
            left: 0,
            right: 430,
            bottom: 66,
            width: 430,
            height: 54,
            toJSON: () => ({}),
          } as DOMRect;
        }
        return {
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          width: 0,
          height: 0,
          toJSON: () => ({}),
        } as DOMRect;
      });
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    try {
      const leftHandle = container.querySelector<HTMLDivElement>('[data-compact-resize-side="left"]');
      expect(leftHandle).not.toBeNull();
      fireEvent.pointerDown(leftHandle!, {
        pointerId: 41,
        clientX: 0,
        screenX: 0,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });

      expect(document.documentElement.style.getPropertyValue('--compact-surface-resize-width')).toBe('');
      expect(resizeRequests).toEqual([
        expect.objectContaining({
          side: 'left',
          width: 430,
          phase: 'start',
          screenRect: expect.objectContaining({
            left: 0,
            top: 12,
            width: 430,
            height: 54,
            right: 430,
            bottom: 66,
          }),
        }),
      ]);
      fireEvent.pointerUp(leftHandle!, {
        pointerId: 41,
        clientX: 0,
        screenX: 0,
        buttons: 0,
        pointerType: 'mouse',
      });
    } finally {
      getBoundingClientRectSpy.mockRestore();
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
      window.removeEventListener('neko:compact-surface-resize-request', handleResizeRequest);
    }
  });

  it('keeps compact history collapsed by default and only applies the open variant after the tutorial ends', () => {
    window.localStorage.removeItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    window.localStorage.setItem(COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY, 'open');
    const message = parseChatMessage({
      id: 'assistant-gate-1', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'text', text: 'hi' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    // 无显式偏好 → 初始折叠（即便 variant=open，也要等教程结束才展开）
    expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
    // 教程完成 → 套用 open 变体 → 展开
    act(() => {
      window.dispatchEvent(new Event('neko:tutorial-completed'));
    });
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
  });

  it('does not allocate the history experiment variant when starting on a full surface (keeps compact A/B clean)', () => {
    window.localStorage.removeItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    window.localStorage.removeItem(COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY);
    const message = parseChatMessage({
      id: 'assistant-full-gate', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'text', text: 'hi' }], status: 'sent',
    });
    render(<App chatSurfaceMode="full" messages={[message]} />);
    act(() => {
      window.dispatchEvent(new Event('neko:tutorial-completed'));
    });
    // full → FullChatSurface（CompactChatApp 不 mount），实验 effect 不跑：不分配 variant、不上报曝光，
    // full-surface 用户没见过紧凑历史面板，不该进入 compact A/B 样本。
    expect(window.localStorage.getItem(COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY)).toBeNull();
  });

  it('still reports the exposure when sessionStorage access throws (privacy mode)', () => {
    window.localStorage.removeItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    window.localStorage.setItem(COMPACT_HISTORY_DEFAULT_EXPERIMENT_KEY, 'closed');
    const telemetry = vi.fn(() => true);
    (window as unknown as { appTelemetry?: { counter: (n: string, v?: number, d?: Record<string, unknown>) => boolean } }).appTelemetry = { counter: telemetry };
    // 只让 sessionStorage.getItem 抛（隐私浏览器/webview），localStorage 仍可读 cohort——
    // 二者共享 Storage.prototype，按 this 区分。
    const realGetItem = Storage.prototype.getItem;
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (this === window.sessionStorage) throw new DOMException('denied', 'SecurityError');
      return realGetItem.call(this, key);
    });
    try {
      const message = parseChatMessage({
        id: 'assistant-privacy-exposure', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
        blocks: [{ type: 'text', text: 'hi' }], status: 'sent',
      });
      render(<App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />);
      act(() => {
        window.dispatchEvent(new Event('neko:tutorial-completed'));
      });
      // sessionStorage 去重读失败不能吞掉曝光：有 variant 的用户仍要发出 experiment_exposure。
      expect(telemetry).toHaveBeenCalledWith('experiment_exposure', 1, expect.objectContaining({
        experiment: 'compact_history_default',
        variant: 'closed',
      }));
    } finally {
      getItemSpy.mockRestore();
      delete (window as unknown as { appTelemetry?: unknown }).appTelemetry;
    }
  });

  it('keeps the proactive meme overlay through the same-turn assistant caption that follows it', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    // 回归：主动分享是「发表情包 + 说台词」，台词是 assistant 消息、紧随 meme 落地。
    // 旧逻辑「有新消息就收起」会让图一瞬间被台词顶掉（线上实测：图闪一下就没）。
    const meme = parseChatMessage({
      id: 'meme-abc123',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=x', alt: 'lol' }],
      status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
    );
    const img = container.querySelector('.compact-meme-overlay img');
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', '/api/meme/proxy-image?url=x');

    const caption = parseChatMessage({
      id: 'assistant-newer',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'hi' }],
      status: 'sent',
    });
    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[meme, caption]} />);
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=x');
  });

  it('collapses the meme overlay once the user speaks again', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-abc123', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=x', alt: 'lol' }], status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
    );
    expect(container.querySelector('.compact-meme-overlay')).not.toBeNull();

    const userReply = parseChatMessage({
      id: 'user-1', role: 'user', author: 'Me', time: '10:02', createdAt: 3,
      blocks: [{ type: 'text', text: 'haha' }], status: 'sent',
    });
    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[meme, userReply]} />);
    expect(container.querySelector('.compact-meme-overlay')).toBeNull();
  });

  it('keeps the meme overlay alongside a music card from the same share (independent widgets)', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-xyz', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=y', alt: 'lol' }], status: 'sent',
    });
    const musicCard = parseChatMessage({
      id: 'music-abc', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 2,
      blocks: [{ type: 'link', url: 'https://example.com/song', title: 'Song' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme, musicCard]} />,
    );
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=y');
  });

  it('keeps the meme overlay even when a much later music-only turn arrives (no user message)', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    // 表情包是独立挂件，不被猫娘后续的音乐分享收起；只有用户开口才换场。
    const meme = parseChatMessage({
      id: 'meme-old', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1000,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=z', alt: 'lol' }], status: 'sent',
    });
    const laterMusic = parseChatMessage({
      id: 'music-later', role: 'assistant', author: 'Neko', time: '10:05', createdAt: 1000 + 60000,
      blocks: [{ type: 'link', url: 'https://example.com/song2', title: 'Song2' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme, laterMusic]} />,
    );
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=z');
  });

  it('keeps the meme overlay through a same-turn caption that shares its turnId', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    // host 给主动分享 meme 打上它所属轮的 turnId（与同轮台词相同）；同轮台词不该顶掉图。
    const meme = parseChatMessage({
      id: 'meme-turn1', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1, turnId: 'turn-1',
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=t1', alt: 'lol' }], status: 'sent',
    });
    const sameTurnCaption = parseChatMessage({
      id: 'assistant-caption', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 2, turnId: 'turn-1',
      blocks: [{ type: 'text', text: '给你看个图～' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme, sameTurnCaption]} />,
    );
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=t1');
  });

  it('collapses the meme overlay once a new assistant turn (different turnId) arrives', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    // 真正的新一轮回复/主动搭话（不同 turnId）应顶掉旧图，即便用户没开口。
    const meme = parseChatMessage({
      id: 'meme-turn1', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1, turnId: 'turn-1',
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=t1', alt: 'lol' }], status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
    );
    expect(container.querySelector('.compact-meme-overlay')).not.toBeNull();

    const newTurnReply = parseChatMessage({
      id: 'assistant-newturn', role: 'assistant', author: 'Neko', time: '10:05', createdAt: 2, turnId: 'turn-2',
      blocks: [{ type: 'text', text: '在干嘛呀～' }], status: 'sent',
    });
    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[meme, newTurnReply]} />);
    expect(container.querySelector('.compact-meme-overlay')).toBeNull();
  });

  it('keeps the meme overlay when a non-assistant (tool/system) message with a different turnId follows', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    // 只有「不同 turnId 的助手发言」算换场；tool/system 不是发言，不该顶掉图。
    const meme = parseChatMessage({
      id: 'meme-turn1', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1, turnId: 'turn-1',
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=t1', alt: 'lol' }], status: 'sent',
    });
    const toolMsg = parseChatMessage({
      id: 'tool-x', role: 'tool', author: 'Tool', time: '10:01', createdAt: 2, turnId: 'turn-2',
      blocks: [{ type: 'text', text: 'tool result' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme, toolMsg]} />,
    );
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=t1');
  });

  it('renders the meme overlay close button after the image loads and hides the overlay when clicked', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-closeme', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=close', alt: 'lol' }], status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
    );
    const img = container.querySelector('.compact-meme-overlay img');
    expect(img).toHaveAttribute('src', '/api/meme/proxy-image?url=close');
    expect(container.querySelector('.compact-meme-overlay-close')).toBeNull();

    fireEvent.load(img as Element);
    await waitFor(() => expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull());
    const closeButton = container.querySelector('.compact-meme-overlay-close');
    // ⚠️ host 只把带 data-compact-hit-region 的子元素登记成 native 可交互区；漏了它 Electron
    // pass-through 窗口里点击会穿到桌面（见 app-react-chat-window.js collectCompactCompositeGeometryItems）。
    expect(closeButton).toHaveAttribute('data-compact-hit-region', 'true');

    fireEvent.click(closeButton as Element);
    expect(container.querySelector('.compact-meme-overlay')).toBeNull();
  });

  it('refreshes compact interaction geometry when the meme close hit region changes', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-close-geometry', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=geometry', alt: 'lol' }], status: 'sent',
    });
    const geometryRefreshes: Event[] = [];
    const handleGeometryRefresh = (event: Event) => geometryRefreshes.push(event);
    window.addEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
      );
      await waitFor(() => expect(geometryRefreshes.length).toBeGreaterThan(0));
      geometryRefreshes.length = 0;

      const img = container.querySelector('.compact-meme-overlay img');
      expect(img).not.toBeNull();
      expect(container.querySelector('.compact-meme-overlay-close')).toBeNull();
      fireEvent.load(img as Element);
      expect(geometryRefreshes.length).toBe(0);
      await waitFor(() => expect(geometryRefreshes.length).toBeGreaterThan(0));
      expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull();
      geometryRefreshes.length = 0;

      fireEvent.click(container.querySelector('.compact-meme-overlay-close') as Element);
      await waitFor(() => expect(geometryRefreshes.length).toBeGreaterThan(0));
      expect(container.querySelector('.compact-meme-overlay')).toBeNull();
    } finally {
      window.removeEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    }
  });

  it('renders the meme overlay close button after the image fails to load', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-error-geometry', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=error', alt: 'lol' }], status: 'sent',
    });
    const geometryRefreshes: Event[] = [];
    const handleGeometryRefresh = (event: Event) => geometryRefreshes.push(event);
    window.addEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
      );
      await waitFor(() => expect(geometryRefreshes.length).toBeGreaterThan(0));
      geometryRefreshes.length = 0;

      const img = container.querySelector('.compact-meme-overlay img');
      expect(img).not.toBeNull();
      expect(container.querySelector('.compact-meme-overlay-close')).toBeNull();
      fireEvent.error(img as Element);
      expect(geometryRefreshes.length).toBe(0);
      await waitFor(() => expect(geometryRefreshes.length).toBeGreaterThan(0));
      expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull();
    } finally {
      window.removeEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    }
  });

  it('does not reuse a loaded meme overlay close button after history remounts the same image', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const meme = parseChatMessage({
      id: 'meme-history-remount', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=history-remount', alt: 'lol' }], status: 'sent',
    });
    vi.useFakeTimers();
    try {
      const { container } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
      );

      const firstImage = container.querySelector('.compact-meme-overlay img');
      fireEvent.load(firstImage as Element);
      expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-meme-overlay')).toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });

      const remountedImage = container.querySelector('.compact-meme-overlay img');
      expect(remountedImage).toHaveAttribute('src', '/api/meme/proxy-image?url=history-remount');
      expect(container.querySelector('.compact-meme-overlay-close')).toBeNull();

      fireEvent.load(remountedImage as Element);
      expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows a newer meme even after the previous one was manually closed', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const memeA = parseChatMessage({
      id: 'meme-A', role: 'assistant', author: 'Neko', time: '10:00', createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=A', alt: 'A' }], status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[memeA]} />,
    );
    const memeAImage = container.querySelector('.compact-meme-overlay img');
    fireEvent.load(memeAImage as Element);
    expect(container.querySelector('.compact-meme-overlay-close')).not.toBeNull();
    fireEvent.click(container.querySelector('.compact-meme-overlay-close') as Element);
    expect(container.querySelector('.compact-meme-overlay')).toBeNull();

    // 叉掉旧图后，来一张新表情包（不同 id）应照常显示——dismiss 只钉旧 id。
    const memeB = parseChatMessage({
      id: 'meme-B', role: 'assistant', author: 'Neko', time: '10:05', createdAt: 2,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=B', alt: 'B' }], status: 'sent',
    });
    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[memeA, memeB]} />);
    expect(container.querySelector('.compact-meme-overlay img')).toHaveAttribute('src', '/api/meme/proxy-image?url=B');
  });

  it('hides the proactive meme overlay while compact history is open', () => {
    const meme = parseChatMessage({
      id: 'meme-visible-in-history',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=history', alt: 'history meme' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[meme]} />,
    );

    expect(container.querySelector('.compact-meme-overlay')).toBeNull();
    const historyImage = container.querySelector(
      '[data-compact-export-history-message-id="meme-visible-in-history"] .message-block-image img',
    );
    expect(historyImage).toHaveAttribute('src', '/api/meme/proxy-image?url=history');
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
    expect(container.querySelector('.compact-export-history-scroll')).toHaveAttribute('data-compact-hit-region', 'true');
    expect(container.querySelector('.compact-export-history-scroll')).toHaveAttribute('data-compact-hit-region-id', 'history:scroll');
    expect(container.querySelector('.compact-export-history-scroll')).toHaveAttribute('data-compact-hit-region-kind', 'scroll');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region', 'true');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region-id', 'history:message:assistant-history-1');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('data-compact-hit-region-kind', 'message');
    expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
    expect(container.querySelector('.compact-music-player-mount#music-player-mount')).not.toBeNull();
    expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-music-player-mount', 'compact-surface');
    expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
    expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
    expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-geometry-item', 'musicPlayer');
    expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-geometry-hit-scope', 'children');
    expect(container.querySelector('.composer-panel #music-player-mount')).toBeNull();
    expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
    expect(container.querySelector('.compact-export-history-music-mount')).toBeNull();
    expect(container.querySelector('.compact-export-history-controls')).toBeNull();
    expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('data-compact-geometry-item', 'historyHandle');
    expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.compact-export-history-message')).toHaveAttribute('role', 'listitem');
    expect(container.querySelector('.compact-export-history-message')).not.toHaveAttribute('aria-pressed');
    expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('role');
    expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('aria-pressed');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-disabled', 'true');
    expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('tabindex', '-1');
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');

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
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
      expect(container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'false');
      expect(exportButton).toHaveAttribute('aria-pressed', 'false');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('role');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('aria-pressed');
      expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('aria-disabled', 'true');
      expect(container.querySelector('.compact-export-history-bubble')).toHaveAttribute('tabindex', '-1');
      expect(container.querySelector('.compact-export-history-bubble')).not.toHaveAttribute('data-compact-hit-region');
      expect(container.querySelector('.compact-export-history-scroll')).not.toHaveAttribute('data-compact-hit-region');
      expect(container.querySelector('.compact-export-history-music-mount')).toBeNull();
      expect(container.querySelector('.compact-export-history-controls')).toHaveAttribute('aria-disabled', 'true');
      expect(container.querySelector('.compact-export-history-controls')).not.toHaveAttribute('data-compact-hit-region');
      container.querySelectorAll<HTMLButtonElement>('.compact-export-history-control').forEach((button) => {
        expect(button).toBeDisabled();
      });
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('false');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });

      expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
      expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
      expect(container.querySelector('.compact-music-player-mount#music-player-mount')).not.toBeNull();
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
      expect(container.querySelector('.composer-panel #music-player-mount')).toBeNull();
      expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
      expect(container.querySelector('[data-compact-hit-region-id^="history:"]')).toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-open', 'true');
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
      expect(container.querySelector('.compact-export-history-music-mount')).toBeNull();
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

  it('keeps compact history hidden for new conversation messages after the user closes it', async () => {
    const initialMessage = parseChatMessage({
      id: 'assistant-history-before-close',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'I am visible before closing.' }],
      status: 'sent',
    });
    const userMessage = parseChatMessage({
      id: 'user-history-after-close',
      role: 'user',
      author: 'You',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: 'This should not flash while history is closed.' }],
      status: 'sent',
    });
    const assistantMessage = parseChatMessage({
      id: 'assistant-history-after-close',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 3,
      blocks: [{ type: 'text', text: 'But it should appear after reopening history.' }],
      status: 'sent',
    });

    vi.useFakeTimers();
    try {
      const { container, rerender } = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[initialMessage]} />,
      );
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-history-before-close"]')).not.toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');

      rerender(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={[initialMessage, userMessage, assistantMessage]}
        />,
      );
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(container.querySelector('[data-compact-export-history-message-id="user-history-after-close"]')).toBeNull();
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-history-after-close"]')).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });
      expect(container.querySelector('.compact-export-history-anchor')).toBeNull();

      rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[initialMessage, userMessage, assistantMessage]} />);
      expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
      expect(container.querySelector('[data-compact-export-history-message-id="user-history-after-close"]')).toBeNull();
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-history-after-close"]')).toBeNull();

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
      expect(container.querySelector('[data-compact-export-history-message-id="user-history-after-close"]')).not.toBeNull();
      expect(container.querySelector('[data-compact-export-history-message-id="assistant-history-after-close"]')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('refreshes compact interaction geometry when compact history closes, unmounts, and reopens', async () => {
    const message = parseChatMessage({
      id: 'assistant-history-geometry-refresh',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'History geometry should stay fresh.' }],
      status: 'sent',
    });
    const geometryRefreshes: Event[] = [];
    const handleGeometryRefresh = (event: Event) => geometryRefreshes.push(event);
    window.addEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    vi.useFakeTimers();
    let unmount: (() => void) | null = null;
    try {
      const rendered = render(
        <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
      );
      const { container } = rendered;
      unmount = rendered.unmount;
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      geometryRefreshes.length = 0;

      await act(async () => {
        fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      });
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(geometryRefreshes.length).toBeGreaterThan(0);
      const closeRefreshCount = geometryRefreshes.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
      });
      expect(container.querySelector('.compact-export-history-anchor')).toBeNull();
      expect(geometryRefreshes.length).toBeGreaterThan(closeRefreshCount);
      const unmountRefreshCount = geometryRefreshes.length;

      await act(async () => {
        fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle')!);
      });
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
      expect(geometryRefreshes.length).toBeGreaterThan(unmountRefreshCount);
    } finally {
      unmount?.();
      vi.useRealTimers();
      window.removeEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    }
  });

  it('opens and closes compact history from a guide request', async () => {
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" />,
    );
    const historyHandle = () => container.querySelector('.compact-history-visibility-handle');

    expect(historyHandle()).toHaveAttribute('aria-expanded', 'true');

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactHistoryOpenRequest={{
          id: 'compact-history-close-guide',
          open: false,
          reason: 'avatar-floating-guide-close-history',
        }}
      />,
    );
    await waitFor(() => {
      expect(historyHandle()).toHaveAttribute('aria-expanded', 'false');
    });

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactHistoryOpenRequest={{
          id: 'compact-history-open-guide',
          open: true,
          reason: 'avatar-floating-guide-open-history',
        }}
      />,
    );
    await waitFor(() => {
      expect(historyHandle()).toHaveAttribute('aria-expanded', 'true');
    });
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
      expect(container.querySelector('.compact-music-player-mount#music-player-mount')).not.toBeNull();
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
      expect(container.querySelector('.composer-panel #music-player-mount')).toBeNull();

      fireEvent.pointerDown(handle!, { pointerType: 'mouse', button: 0 });
      expect(handle).toHaveAttribute('aria-expanded', 'true');
      expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
      expect(container.querySelectorAll('#music-player-mount')).toHaveLength(1);
      expect(container.querySelector('.compact-export-history-panel #music-player-mount')).toBeNull();
      expect(container.querySelector('.compact-export-history-music-mount')).toBeNull();
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-music-player-mount', 'compact-surface');
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
      expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');
      expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');

      fireEvent.click(handle!);
      expect(handle).toHaveAttribute('aria-expanded', 'true');

      fireEvent.pointerDown(handle!, { pointerType: 'mouse', button: 0 });
      expect(handle).toHaveAttribute('aria-expanded', 'false');
      expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'closing');
      expect(container.querySelector('.compact-music-player-mount')).toHaveAttribute('data-compact-music-player-visibility', 'open');
      expect(container.querySelector('.compact-music-player-mount')).not.toHaveAttribute('aria-hidden');
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

  it('keeps compact history open when the first press causes the handle to leave before click', () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" />,
    );

    const handle = container.querySelector<HTMLButtonElement>('.compact-history-visibility-handle');
    expect(handle).not.toBeNull();
    expect(handle).toHaveAttribute('aria-expanded', 'false');

    fireEvent.pointerDown(handle!, { pointerType: 'mouse', button: 0, buttons: 1 });
    expect(handle).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();

    fireEvent.pointerLeave(handle!, { pointerType: 'mouse', buttons: 1 });
    fireEvent.click(handle!);

    expect(handle).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.compact-export-history-anchor')).toHaveAttribute('data-compact-export-history-visibility', 'open');
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');
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
    // 去随机后：气泡宽度统一（比对话条窄约 48px）、无水平偏移、无旋转——规整不歪扭。
    expect(second?.style.getPropertyValue('--compact-history-bubble-max-ratio')).toBe('calc(100% - 48px)');
    expect(second?.style.getPropertyValue('--compact-history-stagger-x')).toBe('0px');
    expect(user?.style.getPropertyValue('--compact-history-stagger-x')).toBe('0px');
    expect(user?.style.getPropertyValue('--compact-history-rotate')).toBe('0deg');
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
        format: 'image',
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

  it('keeps compact history bubbles selectable by click while leaving text selectable', async () => {
    const textMessage = parseChatMessage({
      id: 'assistant-history-selectable-text',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Select this text or click to pick the bubble.' }],
      status: 'sent',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[textMessage]} />,
    );

    await clickCompactExportTool();
    const message = container.querySelector<HTMLElement>('.compact-export-history-message')!;
    const bubble = container.querySelector<HTMLElement>('.compact-export-history-bubble')!;

    // The bubble no longer captures pointers for a drag subsystem, so its text
    // content stays in the DOM and selectable; only onClick / onKeyDown drive
    // the export-selection toggle.
    expect(bubble.textContent).toContain('Select this text or click to pick the bubble.');
    expect(bubble).toHaveAttribute('role', 'button');

    fireEvent.click(bubble);
    expect(message).toHaveClass('is-selected');

    fireEvent.click(bubble);
    expect(message).not.toHaveClass('is-selected');
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
          format: 'image',
          imageStyle: 'neko',
          imageFormat: 'png',
        });
      });

      fireEvent.click(container.querySelector<HTMLButtonElement>('.compact-export-preview-action')!);
      await waitFor(() => {
        expect(copyCompactInlineSelection).toHaveBeenCalledWith({
          messageIds: ['assistant-history-export-action'],
          format: 'image',
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

  it('shrinks compact history immediately after overdragging past the maximum height', () => {
    const message = parseChatMessage({
      id: 'assistant-history-resize-limit',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Resize me.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    const resizeBar = container.querySelector<HTMLDivElement>('.compact-export-history-resize-bar');
    expect(resizeBar).not.toBeNull();
    const maxHeight = Math.round(Math.max(120, window.innerHeight * 0.78 - 72));

    fireEvent.pointerDown(resizeBar!, {
      pointerId: 91,
      clientY: 500,
      screenY: 500,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-resizing', 'true');
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-content-locked', 'false');
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 91,
      clientY: -500,
      screenY: -500,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-content-locked', 'true');
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${maxHeight}px`);

    fireEvent.pointerMove(resizeBar!, {
      pointerId: 91,
      clientY: -700,
      screenY: -700,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${maxHeight}px`);

    fireEvent.pointerMove(resizeBar!, {
      pointerId: 91,
      clientY: -660,
      screenY: -660,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${maxHeight - 40}px`);
    fireEvent.pointerUp(resizeBar!, {
      pointerId: 91,
      clientY: -660,
      screenY: -660,
      buttons: 0,
      pointerType: 'mouse',
    });
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-resizing', 'false');
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-content-locked', 'false');
    expect(window.localStorage.getItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY)).toBe(`${maxHeight - 40}`);
  });

  it('keeps the max compact history height after releasing at an overdragged limit', () => {
    const message = parseChatMessage({
      id: 'assistant-history-resize-max-release',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Max me.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    const resizeBar = container.querySelector<HTMLDivElement>('.compact-export-history-resize-bar');
    expect(resizeBar).not.toBeNull();
    const maxHeight = Math.round(Math.max(120, window.innerHeight * 0.78 - 72));

    fireEvent.pointerDown(resizeBar!, {
      pointerId: 93,
      clientY: 500,
      screenY: 500,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 93,
      clientY: -500,
      screenY: -500,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 93,
      clientY: -700,
      screenY: -700,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${maxHeight}px`);
    fireEvent.pointerUp(resizeBar!, {
      pointerId: 93,
      clientY: -700,
      screenY: -700,
      buttons: 0,
      pointerType: 'mouse',
    });

    expect(window.localStorage.getItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY)).toBe(`${maxHeight}`);
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${maxHeight}px`);
  });

  it('keeps responsive compact history height when a resize returns to the starting height', () => {
    const message = parseChatMessage({
      id: 'assistant-history-resize-return',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Resize and return.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    const resizeBar = container.querySelector<HTMLDivElement>('.compact-export-history-resize-bar');
    expect(resizeBar).not.toBeNull();
    const defaultHeight = Math.round(Math.max(120, Math.min(430 * 1.18, window.innerHeight * 0.63)));

    fireEvent.pointerDown(resizeBar!, {
      pointerId: 92,
      clientY: 500,
      screenY: 500,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 92,
      clientY: 460,
      screenY: 460,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${defaultHeight + 40}px`);
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 92,
      clientY: 500,
      screenY: 500,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe(`${defaultHeight}px`);
    fireEvent.pointerUp(resizeBar!, {
      pointerId: 92,
      clientY: 500,
      screenY: 500,
      buttons: 0,
      pointerType: 'mouse',
    });

    expect(window.localStorage.getItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe('');
  });

  it('reverts compact history height when a resize is canceled', () => {
    const message = parseChatMessage({
      id: 'assistant-history-resize-cancel',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: 'Cancel resize.' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );
    const resizeBar = container.querySelector<HTMLDivElement>('.compact-export-history-resize-bar');
    expect(resizeBar).not.toBeNull();

    fireEvent.pointerDown(resizeBar!, {
      pointerId: 94,
      clientY: 500,
      screenY: 500,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });
    fireEvent.pointerMove(resizeBar!, {
      pointerId: 94,
      clientY: 440,
      screenY: 440,
      buttons: 1,
      pointerType: 'mouse',
    });
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).not.toBe('');
    fireEvent.pointerCancel(resizeBar!, {
      pointerId: 94,
      clientY: 440,
      screenY: 440,
      buttons: 0,
      pointerType: 'mouse',
    });

    expect(window.localStorage.getItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.style.getPropertyValue('--compact-history-slot-height')).toBe('');
    expect(container.querySelector('.compact-export-history-anchor'))
      .toHaveAttribute('data-compact-export-history-resizing', 'false');
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
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');

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

  it('positions compact galgame options after pending screenshot attachments', async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 900,
    });

    try {
      const { container } = render(
        <App
          chatSurfaceMode="compact"
          composerAttachments={[
            { id: 'screenshot-1', url: 'data:image/png;base64,aaa', alt: 'Screenshot 1' },
          ]}
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );

      const shell = container.querySelector('.compact-chat-surface-shell');
      const choiceLayer = document.body.querySelector<HTMLElement>('body > .compact-chat-choice-anchor');
      expect(shell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(shell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 100,
          top: 100,
          left: 24,
          right: 454,
          bottom: 248,
          width: 430,
          height: 148,
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
        expect(choiceLayer).toHaveStyle({
          '--compact-choice-surface-top': '100px',
          '--compact-choice-surface-left': '24px',
          '--compact-choice-surface-width': '430px',
          '--compact-choice-surface-height': '148px',
        });
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

  it('updates compact choice surface vars before applying forced desktop placement', async () => {
    const desktopWindow = window as typeof window & { __nekoDesktopCompactLayout?: unknown };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    desktopWindow.__nekoDesktopCompactLayout = {
      compactChoicePlacement: 'above',
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

      const shell = container.querySelector('.compact-chat-surface-shell');
      const choiceLayer = document.body.querySelector<HTMLElement>('body > .compact-chat-choice-anchor');
      expect(shell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(shell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 72,
          left: 18,
          right: 448,
          bottom: 154,
          width: 430,
          height: 82,
          toJSON: () => ({}),
        }),
      });

      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'above');
        expect(choiceLayer).toHaveStyle({
          '--compact-choice-surface-top': '72px',
          '--compact-choice-surface-left': '18px',
          '--compact-choice-surface-width': '430px',
          '--compact-choice-surface-height': '82px',
        });
      });
    } finally {
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
    }
  });

  it('syncs compact choice surface vars immediately from desktop layout events', async () => {
    const desktopWindow = window as typeof window & { __nekoDesktopCompactLayout?: unknown };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    let unmount: (() => void) | null = null;
    let restoreShellRect: (() => void) | null = null;

    try {
      const rendered = render(
        <App
          chatSurfaceMode="compact"
          galgameModeEnabled
          galgameOptions={[
            { label: 'A', text: 'Option A' },
            { label: 'B', text: 'Option B' },
          ]}
        />,
      );
      const { container } = rendered;
      unmount = rendered.unmount;

      const shell = container.querySelector('.compact-chat-surface-shell');
      const choiceLayer = document.body.querySelector<HTMLElement>('body > .compact-chat-choice-anchor');
      expect(shell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      const originalShellRect = shell!.getBoundingClientRect;
      restoreShellRect = () => {
        Object.defineProperty(shell!, 'getBoundingClientRect', {
          configurable: true,
          value: originalShellRect,
        });
      };
      Object.defineProperty(shell!, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 72,
          left: 18,
          right: 448,
          bottom: 154,
          width: 430,
          height: 82,
          toJSON: () => ({}),
        }),
      });

      const nextLayout = {
        compactChoicePlacement: 'below',
        surface: {
          left: 120,
          top: 260,
          width: 388,
          height: 64,
        },
        windowBounds: { x: 200, y: 100, width: 900, height: 700 },
        workArea: { x: 0, y: 0, width: 1440, height: 900 },
      };
      desktopWindow.__nekoDesktopCompactLayout = nextLayout;

      window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change', {
        detail: nextLayout,
      }));

      expect(choiceLayer).toHaveStyle({
        '--compact-choice-surface-left': '120px',
        '--compact-choice-surface-top': '260px',
        '--compact-choice-surface-width': '388px',
        '--compact-choice-surface-height': '64px',
      });
      await waitFor(() => {
        expect(choiceLayer).toHaveStyle({
          '--compact-choice-surface-left': '120px',
          '--compact-choice-surface-top': '260px',
          '--compact-choice-surface-width': '388px',
          '--compact-choice-surface-height': '64px',
        });
      });
    } finally {
      restoreShellRect?.();
      unmount?.();
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

    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);
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

  it('hides compact empty-state copy when the tutorial takes chat buttons', async () => {
    const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).not.toBeNull();
    expect(preview).toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);

    act(() => {
      document.body.classList.add('yui-guide-standalone-input-shield-active');
    });

    await waitFor(() => {
      expect(preview?.textContent ?? '').toBe('');
    });
    expect(preview).not.toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);
  });

  it('keeps tutorial guide streaming text fully readable in the compact capsule', () => {
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

  it('shows tutorial guide streaming text in the compact capsule immediately', () => {
    const guideText = '这里是新手教程台词，应该直接进入胶囊预览，不等待普通助手语音播放状态。'.repeat(2);
    const message = parseChatMessage({
      id: 'yui-guide-day1-line-1',
      role: 'assistant',
      author: '林悠怡',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: guideText }],
      status: 'streaming',
    });

    const { container } = render(<App chatSurfaceMode="compact" composerHidden messages={[message]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'false');
    expect(preview).toHaveAttribute('data-compact-preview-scrollable', 'true');
    expect(preview).toHaveTextContent(guideText);
  });

  it('does not merge the previous tutorial guide line into the next compact capsule line', () => {
    const firstText = '上一句教程台词已经播放完。';
    const secondText = '这一句教程台词刚开始流式播放，胶囊里只能看到这一句。';
    const firstMessage = parseChatMessage({
      id: 'yui-guide-day1-line-1',
      role: 'assistant',
      author: '林悠怡',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: firstText }],
      status: 'sent',
    });
    const secondMessage = parseChatMessage({
      id: 'yui-guide-day1-line-2',
      role: 'assistant',
      author: '林悠怡',
      time: '10:01',
      createdAt: 3,
      blocks: [{ type: 'text', text: secondText }],
      status: 'streaming',
    });

    const { container } = render(
      <App chatSurfaceMode="compact" composerHidden messages={[firstMessage, secondMessage]} />,
    );

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveTextContent(secondText);
    expect(preview).not.toHaveTextContent(firstText);
  });

  it('auto-scrolls long tutorial guide text to the latest capsule text', () => {
    const firstText = '这是新手教程胶囊里一段较长的台词，刚出现时应该跟随到末尾。';
    const secondText = `${firstText}后面继续补充更长的说明，胶囊文本需要向左滚动来露出最新内容。`;
    const firstMessage = parseChatMessage({
      id: 'yui-guide-scroll-line',
      role: 'assistant',
      author: '林悠怡',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: firstText }],
      status: 'streaming',
    });
    const secondMessage = parseChatMessage({
      ...firstMessage,
      blocks: [{ type: 'text', text: secondText }],
    });

    const { container, rerender } = render(
      <App chatSurfaceMode="compact" composerHidden messages={[firstMessage]} />,
    );
    const preview = container.querySelector('.compact-chat-capsule-text') as HTMLSpanElement;
    expect(preview).not.toBeNull();
    Object.defineProperty(preview, 'scrollWidth', {
      configurable: true,
      value: 420,
    });

    rerender(<App chatSurfaceMode="compact" composerHidden messages={[secondMessage]} />);

    expect(preview.scrollLeft).toBe(420);
    expect(preview).toHaveTextContent(secondText);
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
    expect(preview).toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);
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
    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).toHaveAttribute('data-compact-geometry-hit-scope', 'children');
    expect(container.querySelector('.composer-input')).toHaveAttribute('data-compact-hit-region-id', 'input:text');
    expect(container.querySelector('.composer-input')).toHaveAttribute('data-compact-hit-region-kind', 'input-text');
    expect(container.querySelector('.compact-chat-minimize-ball')).toHaveAttribute('data-compact-hit-region-id', 'input:minimize');
    expect(container.querySelector('.compact-chat-minimize-ball')).toHaveAttribute('data-compact-hit-region-kind', 'input-minimize');
    expect(container.querySelector('.compact-input-tool-toggle')).toHaveAttribute('data-compact-hit-region-id', 'input:tool-toggle');
    expect(container.querySelector('.compact-input-tool-toggle')).toHaveAttribute('data-compact-hit-region-kind', 'input-tool-toggle');
    expect(container.querySelector('.compact-chat-capsule-button')).toBeNull();
    expect(container.querySelector('.composer-bottom-bar')).toBeNull();
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
    const actionButton = screen.getByRole('button', { name: '更多工具' });
    expect(actionButton).toBeInTheDocument();
    expect(actionButton.querySelector('img')).toHaveAttribute('src', '/static/icons/dropdown_arrow.png');
    expect(actionButton.querySelector('img')).toHaveClass('compact-input-tool-toggle-icon');
  });

  it('exposes compact capsule inline tool regions before entering input state', () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="default" />);

    expect(container.querySelector('[data-compact-geometry-part="capsuleBody"]')).not.toBeNull();
    expect(container.querySelector('[data-compact-geometry-part="capsuleBody"]')).toHaveAttribute('data-compact-geometry-hit-scope', 'children');
    expect(container.querySelector('.compact-chat-capsule-button')).toHaveAttribute('data-compact-hit-region-id', 'capsule:text');
    expect(container.querySelector('.compact-chat-capsule-button')).toHaveAttribute('data-compact-hit-region-kind', 'capsule-text');
    expect(container.querySelector('.compact-input-tool-toggle')).toHaveAttribute('data-compact-hit-region-id', 'input:tool-toggle');
    expect(container.querySelector('.compact-input-tool-toggle')).toHaveAttribute('data-compact-hit-region-kind', 'input-tool-toggle');
  });

  it('keeps the compact chat surface visible while voice mode hides the composer input', () => {
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" composerHidden />,
    );

    expect(container.querySelector('.composer-panel')).not.toHaveStyle({ display: 'none' });
    expect(container.querySelector('.compact-chat-surface-shell')).not.toBeNull();
    expect(container.querySelector('.compact-chat-surface-frame')).toHaveAttribute('data-compact-geometry-item', 'capsule');
    expect(container.querySelector('.composer-input')).toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);
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
    expect(capsule).toHaveTextContent(DEFAULT_CHAT_COMPANION_EMPTY_STATE_FALLBACK);
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

  it('opens compact input tools from the default capsule hover ring without entering input state', () => {
    const onCompactChatStateChange = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="default"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    vi.spyOn(actionButton, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 40,
      right: 148,
      bottom: 88,
      width: 48,
      height: 48,
      x: 100,
      y: 40,
      toJSON: () => ({}),
    });

    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    fireEvent.pointerMove(window, { clientX: 124, clientY: 64, pointerType: 'mouse' });

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
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
    const appShell = container.querySelector('.app-shell');
    const inlineInput = container.querySelector('[data-compact-geometry-part="inputBody"]');
    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(fan).not.toBeNull();
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'default');
    expect(fan).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(fan).toHaveAttribute('data-compact-geometry-item', 'toolFan');
    expect(fan?.parentElement).toBe(shell);
    expect(inlineInput?.contains(fan)).toBe(false);
    expect(shell?.contains(fan)).toBe(true);
    expect(shell).toHaveAttribute('data-compact-tool-layer-open', 'true');
    expect(appShell).toHaveAttribute('data-compact-tool-layer-open', 'true');
    expect((fan as HTMLElement).style.getPropertyValue('--compact-tool-wheel-drag-angle')).toBe('0deg');
    expect((fan as HTMLElement).style.getPropertyValue('--compact-tool-wheel-drag-counter-angle')).toBe('0deg');
    expect(fan?.querySelector('.compact-input-tool-fan-hit-region')).not.toBeNull();
    expect(fan?.querySelector('.compact-input-tool-wheel-charge')).not.toBeNull();
    expect(fan?.querySelectorAll('[data-compact-tool-wheel-slot="-2"], [data-compact-tool-wheel-slot="-1"], [data-compact-tool-wheel-slot="0"], [data-compact-tool-wheel-slot="1"], [data-compact-tool-wheel-slot="2"]')).toHaveLength(5);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="-2"], .compact-input-tool-item[data-compact-tool-wheel-slot="-1"], .compact-input-tool-item[data-compact-tool-wheel-slot="0"], .compact-input-tool-item[data-compact-tool-wheel-slot="1"], .compact-input-tool-item[data-compact-tool-wheel-slot="2"]')).toHaveLength(5);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-forward"]')).toHaveLength(1);
    expect(fan?.querySelectorAll('.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-backward"]')).toHaveLength(1);
    expect(fan?.querySelectorAll('[tabindex="0"]')).toHaveLength(3);
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
  });

  it('renders stable compact input tool labels instead of native browser titles', () => {
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        importImageButtonLabel="Import test label"
        screenshotButtonLabel="Screenshot test label"
        jukeboxButtonLabel="Jukebox test label"
        translateButtonLabel="Translate test label"
        galgameToggleButtonLabel="Galgame test label"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));

    const fan = container.querySelector('.compact-input-tool-fan');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    const expectedLabels = [
      ['.compact-input-tool-item-import', 'Import test label'],
      ['.compact-input-tool-item-screenshot', 'Screenshot test label'],
      ['.compact-input-tool-item-jukebox', 'Jukebox test label'],
      ['.compact-input-tool-item-translate', 'Translate test label'],
      ['.compact-input-tool-item-galgame', 'Galgame test label'],
      ['.compact-input-tool-item-export', 'Show history actions'],
      ['.compact-input-tool-item-avatar', 'Avatar tools'],
    ];

    expectedLabels.forEach(([selector, label]) => {
      const item = fan?.querySelector(selector);
      expect(item).not.toBeNull();
      expect(item).not.toHaveAttribute('title');
      expect(item?.querySelector('.compact-input-tool-tooltip')).toHaveTextContent(label);
    });
  });

  it('keeps the default compact tool wheel layout on mobile when the original arc fits', () => {
    const originalMatchMedia = window.matchMedia;
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    mockMobileMatchMedia();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 10,
        top: 10,
        right: 242,
        bottom: 242,
        width: 232,
        height: 232,
        x: 10,
        y: 10,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'default');
    } finally {
      window.matchMedia = originalMatchMedia;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
    }
  });

  it('uses viewport-fit compact tool wheel layout on mobile when the original arc would clip', () => {
    const originalMatchMedia = window.matchMedia;
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    mockMobileMatchMedia();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 220,
        top: 580,
        right: 452,
        bottom: 812,
        width: 232,
        height: 232,
        x: 220,
        y: 580,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
    } finally {
      window.matchMedia = originalMatchMedia;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
    }
  });

  it('uses viewport-fit compact tool wheel layout on desktop when the surface is near the taskbar', () => {
    const desktopLayout = installDesktopCompactLayout({
      windowBounds: { x: 0, y: 470, width: 700, height: 330 },
      workArea: { x: 0, y: 0, width: 1000, height: 800 },
    }, { width: 700, height: 330 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 373,
        top: 165,
        right: 605,
        bottom: 397,
        width: 232,
        height: 232,
        x: 373,
        y: 165,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
    } finally {
      desktopLayout.restore();
    }
  });

  it('uses viewport-fit from desktop screen bottom distance before the compact carrier expands', async () => {
    const desktopLayout = installDesktopCompactLayout({
      windowBounds: { x: 100, y: 720, width: 430, height: 56 },
      workArea: { x: 0, y: 0, width: 1000, height: 800 },
    }, { width: 430, height: 56 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockImplementation(() => {
        const expanded = (desktopLayout.layout?.windowBounds?.height ?? 0) > 100;
        const left = expanded ? 373 : 303;
        const top = expanded ? 165 : 0;
        return {
          left,
          top,
          right: left + 232,
          bottom: top + 232,
          width: 232,
          height: 232,
          x: left,
          y: top,
          toJSON: () => ({}),
        } as DOMRect;
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');

      desktopLayout.setLayout({
        windowBounds: { x: 0, y: 470, width: 700, height: 330 },
        workArea: { x: 0, y: 0, width: 1000, height: 800 },
      });
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change', {
          detail: desktopLayout.layout,
        }));
      });

      await waitFor(() => {
        expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
      });
    } finally {
      desktopLayout.restore();
    }
  });

  it('keeps the default compact tool wheel layout when the desktop bottom reverse arc would clip at a side edge', () => {
    const desktopLayout = installDesktopCompactLayout({
      windowBounds: { x: 0, y: 720, width: 430, height: 56 },
      workArea: { x: 0, y: 0, width: 140, height: 800 },
    }, { width: 430, height: 56 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 10,
        top: 0,
        right: 242,
        bottom: 232,
        width: 232,
        height: 232,
        x: 10,
        y: 0,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'default');
    } finally {
      desktopLayout.restore();
    }
  });

  it('uses the visual viewport when checking compact tool wheel clipping on mobile', () => {
    const originalMatchMedia = window.matchMedia;
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    const originalVisualViewport = window.visualViewport;
    mockMobileMatchMedia();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: {
        width: 390,
        height: 180,
        offsetLeft: 0,
        offsetTop: 0,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      },
    });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 10,
        top: 10,
        right: 242,
        bottom: 242,
        width: 232,
        height: 232,
        x: 10,
        y: 10,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
    } finally {
      window.matchMedia = originalMatchMedia;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, 'visualViewport', { configurable: true, value: originalVisualViewport });
    }
  });

  it('keeps the default compact tool wheel layout when viewport-fit would still clip at the top edge', () => {
    const originalMatchMedia = window.matchMedia;
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    mockMobileMatchMedia();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);
      const fan = container.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      vi.spyOn(fan, 'getBoundingClientRect').mockReturnValue({
        left: 10,
        top: -90,
        right: 242,
        bottom: 142,
        width: 232,
        height: 232,
        x: 10,
        y: -90,
        toJSON: () => ({}),
      });

      const actionButton = container.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      fireEvent.click(actionButton);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'default');
    } finally {
      window.matchMedia = originalMatchMedia;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
    }
  });

  it('anchors compact avatar tool bubbles to the fan origin instead of the rotating tool item', async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

      const fan = container.querySelector('.compact-input-tool-fan');
      const avatarToolItem = container.querySelector('.compact-input-tool-item-avatar');
      const popover = container.querySelector('#composer-avatar-tool-quickbar');
      expect(fan).not.toBeNull();
      expect(avatarToolItem).not.toBeNull();
      expect(popover).not.toBeNull();
      expect(popover?.parentElement).toBe(fan);
      expect(avatarToolItem?.contains(popover)).toBe(false);
      expect(popover).toHaveClass('avatar-tool-quickbar');
    } finally {
      vi.useRealTimers();
    }
  });

  it('opens compact avatar tool manager near the edit button and supports header dragging', async () => {
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 768 });

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

      const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
      expect(editButton).not.toBeNull();
      expect(editButton.querySelectorAll('img')).toHaveLength(1);
      expect(editButton.querySelector('img')).toHaveAttribute('src', '/static/icons/edit_tool_unified.png');
      Object.defineProperty(editButton, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          left: 700,
          top: 600,
          right: 746,
          bottom: 646,
          width: 46,
          height: 46,
          x: 700,
          y: 600,
          toJSON: () => ({}),
        }),
      });

      fireEvent.click(editButton);

      const dialog = screen.getByRole('dialog', { name: 'Manage tools' });
      expect(dialog).toHaveClass('is-positioned');
      expect(dialog).toHaveStyle({
        '--avatar-tool-manager-left': '366px',
        '--avatar-tool-manager-top': '12px',
      });
      expect(dialog.querySelectorAll('.avatar-tool-manager-slot')).toHaveLength(3);
      expect(dialog.querySelector('.avatar-tool-icon-hammer')).not.toBeNull();

      const header = dialog.querySelector('.avatar-tool-manager-header') as HTMLElement;
      expect(header).not.toBeNull();
      fireEvent.pointerDown(header, {
        pointerId: 31,
        pointerType: 'mouse',
        button: 0,
        buttons: 1,
        clientX: 500,
        clientY: 200,
      });
      fireEvent.pointerMove(header, {
        pointerId: 31,
        pointerType: 'mouse',
        buttons: 1,
        clientX: 530,
        clientY: 230,
      });

      await waitFor(() => {
        expect(dialog).toHaveClass('is-dragging');
        expect(dialog).toHaveStyle({
          '--avatar-tool-manager-left': '396px',
          '--avatar-tool-manager-top': '42px',
        });
      });

      fireEvent.pointerUp(header, {
        pointerId: 31,
        pointerType: 'mouse',
        button: 0,
        clientX: 530,
        clientY: 230,
      });
      expect(dialog).not.toHaveClass('is-dragging');
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
    }
  });

  it('sizes compact avatar tool manager against the desktop work area when the carrier is small', async () => {
    const originalInnerWidth = window.innerWidth;
    const originalInnerHeight = window.innerHeight;
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: {
        windowBounds: { x: number; y: number; width: number; height: number };
        workArea: { x: number; y: number; width: number; height: number };
      } | null;
    };
    const originalDesktopLayout = desktopWindow.__nekoDesktopCompactLayout;
    const hadElectronChatWindowClass = document.body.classList.contains('electron-chat-window');

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 393 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 74 });
    document.body.classList.add('electron-chat-window');
    desktopWindow.__nekoDesktopCompactLayout = {
      windowBounds: { x: 976, y: 485, width: 393, height: 74 },
      workArea: { x: 0, y: 0, width: 1706, height: 1066 },
    };

    try {
      const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

      const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
      expect(editButton).not.toBeNull();
      Object.defineProperty(editButton, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          left: 310,
          top: 20,
          right: 356,
          bottom: 66,
          width: 46,
          height: 46,
          x: 310,
          y: 20,
          toJSON: () => ({}),
        }),
      });

      fireEvent.click(editButton);

      const dialog = screen.getByRole('dialog', { name: 'Manage tools' });
      expect(dialog).toHaveClass('is-positioned');
      expect(dialog).toHaveClass('is-desktop-compact-layout');
      expect(dialog).toHaveAttribute('data-compact-geometry-item', 'avatarToolManager');
      expect(dialog).toHaveStyle({
        '--avatar-tool-manager-left': '-24px',
        '--avatar-tool-manager-top': '-473px',
        '--avatar-tool-manager-width': '380px',
        '--avatar-tool-manager-height': '600px',
      });

      desktopWindow.__nekoDesktopCompactLayout = {
        windowBounds: { x: 0, y: 0, width: 1706, height: 1066 },
        workArea: { x: 0, y: 0, width: 1706, height: 1066 },
      };
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change', {
          detail: desktopWindow.__nekoDesktopCompactLayout,
        }));
      });

      await waitFor(() => {
        expect(dialog).toHaveStyle({
          '--avatar-tool-manager-left': '12px',
          '--avatar-tool-manager-top': '12px',
        });
      });
    } finally {
      if (hadElectronChatWindowClass) {
        document.body.classList.add('electron-chat-window');
      } else {
        document.body.classList.remove('electron-chat-window');
      }
      desktopWindow.__nekoDesktopCompactLayout = originalDesktopLayout;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalInnerHeight });
    }
  });

  it('adds the React chat asset version to compact avatar tool images when provided by the host template', async () => {
    window.__NEKO_REACT_CHAT_ASSET_VERSION__ = 'asset 1';
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
    const editImage = editButton?.querySelector('img');
    const quickbarImage = container.querySelector('.avatar-tool-quickbar-image') as HTMLImageElement;
    expect(editButton).not.toBeNull();
    expect(editImage).toHaveAttribute('src', '/static/icons/edit_tool_unified.png?v=asset%201');
    expect(quickbarImage).toHaveAttribute('src', '/static/icons/chat_sugar1.png?v=asset%201');

    fireEvent.click(editButton);
    const dialog = await screen.findByRole('dialog', { name: 'Manage tools' });
    const managerImage = dialog.querySelector('.avatar-tool-manager-tool-image') as HTMLImageElement;
    expect(managerImage).toHaveAttribute('src', '/static/icons/chat_sugar1.png?v=asset%201');
  });

  it('temporarily restores body pointer events while the avatar tool manager is open', async () => {
    document.body.style.pointerEvents = 'none';
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
    expect(editButton).not.toBeNull();
    fireEvent.click(editButton);

    expect(await screen.findByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();
    expect(document.body.style.pointerEvents).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Manage tools' })).toBeNull();
      expect(document.body.style.pointerEvents).toBe('none');
    });
  });

  it('keeps avatar tool manager focus trapped and restores focus to the edit button on close', async () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
    expect(editButton).not.toBeNull();
    editButton.focus();
    expect(editButton).toHaveFocus();
    fireEvent.click(editButton);

    const dialog = await screen.findByRole('dialog', { name: 'Manage tools' });
    const closeButton = screen.getByRole('button', { name: 'Close' });
    const saveButton = screen.getByRole('button', { name: 'Save changes' });

    await waitFor(() => {
      expect(closeButton).toHaveFocus();
    });

    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(saveButton).toHaveFocus();

    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(closeButton).toHaveFocus();

    fireEvent.click(closeButton);

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Manage tools' })).toBeNull();
      expect(editButton).toHaveFocus();
    });
  });

  it('clears avatar tool manager state when compact surface closes', async () => {
    const { container, rerender } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
    expect(editButton).not.toBeNull();
    fireEvent.click(editButton);
    expect(await screen.findByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();

    rerender(<App chatSurfaceMode="minimized" compactChatState="input" />);
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Manage tools' })).toBeNull();
    });

    rerender(<App chatSurfaceMode="compact" compactChatState="input" />);
    expect(screen.queryByRole('dialog', { name: 'Manage tools' })).toBeNull();
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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

      const fan = container.querySelector('.compact-input-tool-fan');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(container.querySelector('#composer-avatar-tool-quickbar')).not.toBeNull();

      fireEvent.pointerLeave(actionButton, { clientX: 96, clientY: 96, pointerType: 'mouse' });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(container.querySelector('#composer-avatar-tool-quickbar')).not.toBeNull();

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

  it('opens compact fan when an external avatar tool menu request arrives during tutorial lock', async () => {
    const { container, rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        composerDisabled
      />,
    );

    expect(container.querySelector('.compact-input-tool-fan')).toHaveAttribute(
      'data-compact-input-tool-fan-open',
      'false',
    );
    expect(container.querySelector('#composer-tool-popover-compact')).toBeNull();

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        composerDisabled
        avatarToolMenuOpenRequest={{
          id: 'avatar-tools-open-1',
          open: true,
          reason: 'avatar-floating-guide-open-avatar-tool-menu',
        }}
      />,
    );

    await waitFor(() => {
      expect(container.querySelector('.compact-input-tool-fan')).toHaveAttribute(
        'data-compact-input-tool-fan-open',
        'true',
      );
      expect(
        container.querySelectorAll('#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id]'),
      ).toHaveLength(3);
    });
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

  it('opens compact input tools on hover when Electron reports an empty pointer type', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);

    const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(actionButton).not.toBeNull();
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    fireEvent.pointerEnter(actionButton, { pointerType: '' });

    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });

  it('keeps compact input tools interactive when desktop hover open requests repeat', async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          compactToolFanOpenRequest={{
            id: 'desktop-hover-open-1',
            open: true,
            reason: 'desktop-compact-tool-toggle-cursor-poll',
          }}
        />,
      );

      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'false');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(230);
      });
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'true');

      rerender(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          compactToolFanOpenRequest={{
            id: 'desktop-hover-open-2',
            open: true,
            reason: 'desktop-compact-tool-toggle-hover-keepalive',
          }}
        />,
      );

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-interactive', 'true');
    } finally {
      vi.useRealTimers();
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
      rotateCompactToolToCenter(importButton);
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
      rotateCompactToolToCenter(importButton);

      fireEvent.pointerDown(importButton, { pointerId: 3, clientX: 55, button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(importButton, { pointerId: 3, clientX: 55, buttons: 0, pointerType: 'mouse' });
      fireEvent.click(importButton, { clientX: 140, clientY: 140 });

      expect(onComposerImportImage).toHaveBeenCalledTimes(1);
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps compact tool button clicks after sub-detent pointer jitter', async () => {
    vi.useFakeTimers();
    const onGalgameModeToggle = vi.fn();
    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          onGalgameModeToggle={onGalgameModeToggle}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      const fanRectSpy = mockCompactToolFanRect(fan);
      try {
        const galgameButton = fan.querySelector('.compact-input-tool-item-galgame') as HTMLButtonElement;
        expect(galgameButton).toHaveAttribute('data-compact-tool-wheel-slot', '-1');
        expect(galgameButton).not.toBeDisabled();

        fireEvent.pointerDown(galgameButton, { pointerId: 45, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
        fireEvent.pointerMove(galgameButton, { pointerId: 45, ...compactToolWheelPoint(5 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
        fireEvent.pointerUp(galgameButton, { pointerId: 45, ...compactToolWheelPoint(5 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
        fireEvent.click(galgameButton, { clientX: 140, clientY: 140 });

        expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
        expect(onGalgameModeToggle).toHaveBeenCalledTimes(1);
      } finally {
        fanRectSpy.mockRestore();
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps faded compact tool edge buttons visible but not confirmable', async () => {
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
      expect(exportButton).toHaveAttribute('tabindex', '-1');
      expect(exportButton).toHaveAttribute('aria-hidden', 'false');
      expect(exportButton).toBeDisabled();
      expect(galgameButton).toHaveAttribute('data-compact-tool-wheel-slot', '-1');
      expect(galgameButton).toHaveAttribute('tabindex', '0');
      expect(galgameButton).toHaveAttribute('aria-hidden', 'false');
      expect(galgameButton).not.toBeDisabled();

      fireEvent.click(galgameButton, { clientX: 140, clientY: 140 });
      expect(onGalgameModeToggle).toHaveBeenCalledTimes(1);

      expect(container.querySelector('.compact-export-history-controls')).toBeNull();
      fireEvent.click(exportButton, { clientX: 140, clientY: 140 });
      expect(onExportConversationClick).not.toHaveBeenCalled();
      expect(container.querySelector('.compact-export-history-controls')).toBeNull();
      expect(exportButton).toHaveAttribute('aria-pressed', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('rotates compact input tools by pointer dragging while keeping only center and adjacent buttons active', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);
    const firstCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]');
    expect(firstCenter).toHaveClass('compact-input-tool-item-screenshot');

    try {
      fireEvent.pointerDown(fan, { pointerId: 1, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 1, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(fan, { pointerId: 1, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });

      const nextCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]');
      expect(nextCenter).toHaveClass('compact-input-tool-item-avatar');
      expect(fan.querySelectorAll('[tabindex="0"]')).toHaveLength(3);
      expect(fan.querySelectorAll('[data-compact-tool-wheel-slot="-2"][tabindex="-1"]')).toHaveLength(1);
      expect(fan.querySelectorAll('[data-compact-tool-wheel-slot="2"][tabindex="-1"]')).toHaveLength(1);
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('shows continuous compact tool wheel drag offset below the detent threshold and snaps back on release', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
    expect(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle')).toBe('0deg');
    expect(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle')).toBe('45deg');

    try {
      fireEvent.pointerDown(fan, { pointerId: 41, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 41, ...compactToolWheelPoint(15 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });

      expect(fan).toHaveAttribute('data-compact-tool-wheel-drag-active', 'true');
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
      expect(Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'))).toBeCloseTo(15, 1);
      expect(Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle'))).toBeCloseTo(60, 1);

      fireEvent.pointerUp(fan, { pointerId: 41, ...compactToolWheelPoint(15 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });

      expect(fan).toHaveAttribute('data-compact-tool-wheel-drag-active', 'false');
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
      expect(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle')).toBe('0deg');
      expect(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle')).toBe('45deg');
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('keeps residual compact tool wheel drag offset after crossing one detent', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);

    try {
      fireEvent.pointerDown(fan, { pointerId: 42, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 42, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
      expect(Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'))).toBeGreaterThan(8);
      expect(Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'))).toBeLessThan(10);

      fireEvent.pointerUp(fan, { pointerId: 42, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('does not carry linear drag remainder into angular compact tool wheel dragging', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);

    try {
      fireEvent.pointerDown(fan, {
        pointerId: 44,
        clientX: 116,
        clientY: 116,
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 44,
        clientX: 116,
        clientY: 149,
        buttons: 1,
        pointerType: 'mouse',
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

      fireEvent.pointerMove(fan, {
        pointerId: 44,
        ...compactToolWheelPoint(100 * (Math.PI / 180)),
        buttons: 1,
        pointerType: 'mouse',
      });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
      const residualDragAngle = Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'));
      expect(residualDragAngle).toBeGreaterThan(1);
      expect(residualDragAngle).toBeLessThan(12);

      fireEvent.pointerUp(fan, {
        pointerId: 44,
        ...compactToolWheelPoint(100 * (Math.PI / 180)),
        buttons: 0,
        pointerType: 'mouse',
      });
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('adds detent resistance before compact tool wheel drag breaks through', () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);

    try {
      fireEvent.pointerDown(fan, { pointerId: 43, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 43, ...compactToolWheelPoint(34 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
      const resistedAngle = Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'));
      expect(resistedAngle).toBeGreaterThan(25);
      expect(resistedAngle).toBeLessThan(30);

      fireEvent.pointerMove(fan, { pointerId: 43, ...compactToolWheelPoint(37 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
      const residualAngle = Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle'));
      expect(residualAngle).toBeGreaterThan(5);
      expect(residualAngle).toBeLessThan(8);

      fireEvent.pointerUp(fan, { pointerId: 43, ...compactToolWheelPoint(37 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
    } finally {
      fanRectSpy.mockRestore();
    }
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
    const fanRectSpy = mockCompactToolFanRect(fan);
    const importButton = fan.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;
    expect(importButton).toHaveAttribute('data-compact-tool-wheel-slot', 'hidden-backward');

    try {
      fireEvent.pointerDown(importButton, { pointerId: 4, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(importButton, { pointerId: 4, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerUp(importButton, { pointerId: 4, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
      fireEvent.click(importButton, { clientX: 140, clientY: 140 });

      expect(onComposerImportImage).not.toHaveBeenCalled();
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('anchors compact avatar quickbar above the compact wheel toggle', async () => {
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

      expect(avatarTool.querySelector('#composer-avatar-tool-quickbar')).toBeNull();
      expect(fan.querySelector(':scope > #composer-avatar-tool-quickbar')).not.toBeNull();
      expect(avatarTool).toHaveAttribute('data-compact-tool-active', 'true');
      expect(emojiButton).toHaveClass('is-active');

      const lollipopButton = fan.querySelector<HTMLButtonElement>('#composer-avatar-tool-quickbar [data-avatar-tool-id="lollipop"]');
      expect(lollipopButton).not.toBeNull();
      fireEvent.click(lollipopButton!);

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
      expect(fan.querySelector('#composer-tool-popover-compact')).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });

      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
      expect(avatarTool).toHaveAttribute('data-compact-tool-active', 'true');
      expect(avatarTool.querySelector('.composer-emoji-btn')).toHaveClass('is-active');
      expect(fan.querySelector('#composer-avatar-tool-quickbar')).toBeNull();

      fireEvent.click(emojiButton);

      expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
      expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
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
      const fanRectSpy = mockCompactToolFanRect(fan);
      restoreFanRect = () => fanRectSpy.mockRestore();

      fireEvent.pointerDown(fan, {
        pointerId: 18,
        ...compactToolWheelPoint(45 * (Math.PI / 180)),
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
        clientX: 116,
        clientY: 980,
        buttons: 1,
        pointerType: 'mouse',
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
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
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.wheel(fan, { deltaY: 80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
    expect(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle')).toBe('0deg');

    fireEvent.wheel(fan, { deltaY: -80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.wheel(fan, { deltaY: 1 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

    fireEvent.wheel(fan, { deltaY: -1 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    const fanRectSpy = mockCompactToolFanRect(fan);
    try {
      fireEvent.pointerDown(fan, { pointerId: 7, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 7, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

      fireEvent.pointerMove(fan, { pointerId: 7, ...compactToolWheelPoint(-10 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

      fireEvent.pointerUp(fan, { pointerId: 7, ...compactToolWheelPoint(-10 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('keeps compact tool wheel rotation visually consistent in viewport-fit layout', () => {
    const desktopLayout = installDesktopCompactLayout({
      windowBounds: { x: 100, y: 720, width: 430, height: 56 },
      workArea: { x: 0, y: 0, width: 1000, height: 800 },
    }, { width: 430, height: 56 });

    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const fanRectSpy = mockCompactToolFanRect(fan);
      fireEvent.click(actionButton);

      try {
        expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
        expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

        fireEvent.wheel(fan, { deltaY: 80 });

        expect(fan.querySelector('.compact-input-tool-item-screenshot')).toHaveAttribute('data-compact-tool-wheel-slot', '1');
        expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-galgame');
      } finally {
        fanRectSpy.mockRestore();
      }
    } finally {
      desktopLayout.restore();
    }
  });

  it('uses visual charge direction in viewport-fit compact tool wheel layout', () => {
    const desktopLayout = installDesktopCompactLayout({
      windowBounds: { x: 100, y: 720, width: 430, height: 56 },
      workArea: { x: 0, y: 0, width: 1000, height: 800 },
    }, { width: 430, height: 56 });

    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      const actionButton = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
      expect(actionButton).not.toBeNull();
      const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
      const fanRectSpy = mockCompactToolFanRect(fan);
      fireEvent.click(actionButton);

      try {
        expect(fan).toHaveAttribute('data-compact-tool-wheel-layout', 'viewport-fit');
        fireEvent.pointerDown(fan, {
          pointerId: 86,
          ...compactToolWheelPoint(0),
          button: 0,
          buttons: 1,
          pointerType: 'mouse',
        });
        for (let index = 1; index <= 28; index += 1) {
          fireEvent.pointerMove(fan, {
            pointerId: 86,
            ...compactToolWheelPoint(index * 0.7),
            buttons: 1,
            pointerType: 'mouse',
          });
        }

        expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
        expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-direction', 'forward');
        fireEvent.pointerUp(fan, {
          pointerId: 86,
          ...compactToolWheelPoint(28 * 0.7),
          buttons: 0,
          pointerType: 'mouse',
        });
      } finally {
        fanRectSpy.mockRestore();
      }
    } finally {
      desktopLayout.restore();
    }
  });

  it('keeps viewport-fit hidden compact tool wheel slots on the reversed arc', () => {
    expect(compactChatStyles).toMatch(
      /\[data-compact-tool-wheel-layout="viewport-fit"\]\s+\.compact-input-tool-item\[data-compact-tool-wheel-slot="hidden-backward"\][\s\S]*?\{\s*transform:[^}]*-230deg/s,
    );
    expect(compactChatStyles).toMatch(
      /\[data-compact-tool-wheel-layout="viewport-fit"\]\s+\.compact-input-tool-item\[data-compact-tool-wheel-slot="hidden-forward"\][\s\S]*?\{\s*transform:[^}]*-50deg/s,
    );
    expect(compactChatStyles).toMatch(
      /\[data-compact-tool-wheel-layout="viewport-fit"\]\s+\.compact-input-tool-item\[data-compact-tool-wheel-slot="hidden"[\s\S]*?transition: none;/s,
    );
  });

  it('shows compact tool wheel tooltips from pointer hover or keyboard-visible focus only', () => {
    const tooltipVisibilityRule = compactChatStyles.match(
      /\.compact-input-tool-fan\[data-compact-input-tool-fan-open="true"\]\[data-compact-input-tool-fan-interactive="true"\][^{]+>\s*\.compact-input-tool-tooltip\s*\{/s,
    )?.[0] ?? '';

    expect(tooltipVisibilityRule).toContain('[data-compact-tool-pointer-hovered="true"]');
    expect(tooltipVisibilityRule).toContain(':focus-visible');
    expect(tooltipVisibilityRule).not.toContain(':focus-within');
    expect(compactChatStyles).not.toMatch(/:focus-within\s*>\s*\.compact-input-tool-tooltip/);
  });

  it('retargets compact tool hover to the visual button under the pointer after wheel rotation', async () => {
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    await openCompactInputTools();
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    const fanRectSpy = mockCompactToolFanRect(fan);
    // The default wheel's slot 0 sits at 45deg on the 80px orbit, initially the screenshot tool.
    const pointerAtSelectedSlot = compactToolWheelPoint(45 * (Math.PI / 180), 80);
    const pointerAtPreviousSlot = compactToolWheelPoint(75.82 * (Math.PI / 180), 80);

    try {
      fireEvent.pointerMove(fan, {
        pointerId: 81,
        ...pointerAtSelectedSlot,
        buttons: 0,
        pointerType: 'mouse',
      });

      const screenshotButton = fan.querySelector('.compact-input-tool-item-screenshot');
      const avatarButton = fan.querySelector('.compact-input-tool-item-avatar');
      const galgameButton = fan.querySelector('.compact-input-tool-item-galgame');
      expect(screenshotButton).toHaveAttribute('data-compact-tool-pointer-hovered', 'true');
      expect(avatarButton).toHaveAttribute('data-compact-tool-pointer-hovered', 'false');
      expect(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle')).toBe('45deg');

      fireEvent.pointerMove(fan, {
        pointerId: 81,
        ...pointerAtPreviousSlot,
        buttons: 0,
        pointerType: 'mouse',
      });

      expect(galgameButton).toHaveAttribute('data-compact-tool-pointer-hovered', 'true');
      expect(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle')).toBe('75.82deg');

      fireEvent.wheel(fan, {
        deltaY: 80,
        ...pointerAtSelectedSlot,
      });

      expect(screenshotButton).toHaveAttribute('data-compact-tool-pointer-hovered', 'false');
      expect(screenshotButton).toHaveAttribute('data-compact-tool-wheel-slot', '-1');
      expect(avatarButton).toHaveAttribute('data-compact-tool-pointer-hovered', 'true');
      expect(avatarButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');
      expect(fan.style.getPropertyValue('--compact-tool-wheel-selection-angle')).toBe('45deg');
    } finally {
      fanRectSpy.mockRestore();
    }
  });

  it('routes compact tool wheel background scrolling to the open inline history', () => {
    const previousHistoryOpen = window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    const scrollTopByElement = new WeakMap<HTMLElement, number>();
    const scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
    const scrollTopDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');
    const originalElementsFromPoint = document.elementsFromPoint;
    let scrollRectSpy: ReturnType<typeof vi.spyOn> | null = null;

    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 900 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get() {
        return this.classList.contains('compact-export-history-scroll') ? 300 : 0;
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
      window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'true');
      const messages = Array.from({ length: 8 }, (_, index) => parseChatMessage({
        id: `compact-history-wheel-${index}`,
        role: index % 2 === 0 ? 'assistant' : 'user',
        author: index % 2 === 0 ? 'Neko' : 'You',
        time: '10:00',
        createdAt: index,
        blocks: [{ type: 'text', text: `History row ${index}` }],
        status: 'sent',
      }));

      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
          messages={messages}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      const fan = document.body.querySelector<HTMLDivElement>('.compact-input-tool-fan')!;
      const fanHitRegion = fan.querySelector<HTMLElement>('.compact-input-tool-fan-hit-region')!;
      const scroll = document.body.querySelector<HTMLDivElement>('.compact-export-history-scroll')!;

      scrollRectSpy = vi.spyOn(scroll, 'getBoundingClientRect').mockReturnValue({
        left: 20,
        top: 20,
        right: 420,
        bottom: 320,
        width: 400,
        height: 300,
        x: 20,
        y: 20,
        toJSON: () => ({}),
      } as DOMRect);
      Object.defineProperty(document, 'elementsFromPoint', {
        configurable: true,
        value: () => [fanHitRegion, fan, scroll],
      });

      scroll.scrollTop = 120;
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

      act(() => {
        fireEvent.wheel(fanHitRegion, { deltaY: 80, clientX: 160, clientY: 160 });
      });

      expect(scroll.scrollTop).toBe(200);
      expect(scroll).toHaveAttribute('data-compact-scrollbar-visible', 'true');
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

      scroll.scrollTop = 0;
      act(() => {
        fireEvent.wheel(fanHitRegion, { deltaY: -80, clientX: 160, clientY: 160 });
      });

      expect(scroll.scrollTop).toBe(0);
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-galgame');
    } finally {
      if (previousHistoryOpen === null) {
        window.localStorage.removeItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
      } else {
        window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, previousHistoryOpen);
      }
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
      Object.defineProperty(document, 'elementsFromPoint', {
        configurable: true,
        value: originalElementsFromPoint || (() => []),
      });
      scrollRectSpy?.mockRestore();
    }
  });

  it('keeps compact tool wheel detent audio silent for an empty URL and plays every detent when configured', () => {
    const playSfx = vi.fn();
    const preloadSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx, preloadSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof preloadSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };

    playCompactToolWheelDetentSound('');
    expect(GameAudioSystem).not.toHaveBeenCalled();
    expect(playSfx).not.toHaveBeenCalled();

    playCompactToolWheelDetentSound('/static/sfx/tool-detent-a.ogg');
    playCompactToolWheelDetentSound('/static/sfx/tool-detent-a.ogg');

    expect(GameAudioSystem).toHaveBeenCalledTimes(1);
    expect(preloadSfx).toHaveBeenCalledTimes(1);
    expect(preloadSfx).toHaveBeenCalledWith(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS);
    expect(playSfx).toHaveBeenCalledTimes(2);
    expect(playSfx).toHaveBeenNthCalledWith(1, {
      src: '/static/sfx/tool-detent-a.ogg',
      preload: 'auto',
    });
    expect(playSfx).toHaveBeenNthCalledWith(2, {
      src: '/static/sfx/tool-detent-a.ogg',
      preload: 'auto',
    });
  });

  it('silently skips compact tool wheel audio when the host audio API is malformed', () => {
    const windowWithAudio = window as Window & { NekoGameSystem?: unknown };
    const malformedAudioSystems: unknown[] = [
      {},
      { GameAudioSystem: 'not-a-constructor' },
      { GameAudioSystem: vi.fn().mockImplementation(() => ({})) },
    ];

    malformedAudioSystems.forEach(audioSystemShape => {
      resetCompactToolWheelDetentAudioForTests();
      windowWithAudio.NekoGameSystem = audioSystemShape;
      expect(() => playCompactToolWheelDetentSound('/static/sfx/tool-detent-a.ogg')).not.toThrow();
      expect(() => {
        render(
          <App
            chatSurfaceMode="compact"
            compactChatState="input"
          />,
        );
      }).not.toThrow();
    });
  });

  it('preloads compact tool wheel sounds when the chat UI mounts before the wheel opens', async () => {
    const playSfx = vi.fn();
    const preloadSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx, preloadSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof preloadSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };

    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    await waitFor(() => {
      expect(GameAudioSystem).toHaveBeenCalledTimes(1);
    });
    expect(preloadSfx).toHaveBeenCalledTimes(1);
    expect(preloadSfx).toHaveBeenCalledWith(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS);
    expect(playSfx).not.toHaveBeenCalled();
    const fan = document.body.querySelector<HTMLElement>('.compact-input-tool-fan');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
  });

  it('retries compact tool wheel preload if the game audio system appears after chat UI mount', async () => {
    vi.useFakeTimers();
    const playSfx = vi.fn();
    const preloadSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx, preloadSfx }));

    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );
      expect(GameAudioSystem).not.toHaveBeenCalled();

      (window as Window & {
        NekoGameSystem?: {
          GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof preloadSfx };
        };
      }).NekoGameSystem = { GameAudioSystem };

      await act(async () => {
        await vi.advanceTimersByTimeAsync(130);
      });

      expect(GameAudioSystem).toHaveBeenCalledTimes(1);
      expect(preloadSfx).toHaveBeenCalledWith(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS);
      expect(playSfx).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('retries compact tool wheel preload when the first preload attempt fails', async () => {
    vi.useFakeTimers();
    const firstPreloadSfx = vi.fn(() => {
      throw new Error('preload failed');
    });
    const secondPreloadSfx = vi.fn();
    const playSfx = vi.fn();
    const GameAudioSystem = vi.fn()
      .mockImplementationOnce(() => ({ playSfx, preloadSfx: firstPreloadSfx }))
      .mockImplementationOnce(() => ({ playSfx, preloadSfx: secondPreloadSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof secondPreloadSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };

    try {
      render(
        <App
          chatSurfaceMode="compact"
          compactChatState="input"
        />,
      );

      expect(GameAudioSystem).toHaveBeenCalledTimes(1);
      expect(firstPreloadSfx).toHaveBeenCalledWith(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(130);
      });

      expect(GameAudioSystem).toHaveBeenCalledTimes(2);
      expect(secondPreloadSfx).toHaveBeenCalledWith(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS);
      expect(playSfx).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('uses the configured compact tool wheel prompt sound', () => {
    const playSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };

    playCompactToolWheelDetentSound();

    expect(COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS).toEqual([
      '/static/sounds/compact-tool-wheel/wheel-prompt.mp3',
    ]);
    expect(GameAudioSystem).toHaveBeenCalledWith({
      config: {
        audioMix: {
          sfx: {
            baseVolume: 0.24,
            maxVolume: 1,
          },
        },
        sfx: {},
      },
    });
    expect(playSfx).toHaveBeenCalledTimes(1);
    expect(playSfx).toHaveBeenNthCalledWith(1, {
      src: COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS[0],
      preload: 'auto',
    });
  });

  it('keeps compact tool wheel rebound audio disabled', () => {
    const playSfx = vi.fn();
    const preloadSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx, preloadSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof preloadSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };

    playCompactToolWheelReboundSound();

    expect(COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC).toBe('');
    expect(GameAudioSystem).not.toHaveBeenCalled();
    expect(preloadSfx).not.toHaveBeenCalled();
    expect(playSfx).not.toHaveBeenCalled();
  });

  it('scales compact tool wheel rebound visual intensity by residual drag distance', () => {
    expect(getCompactToolWheelReboundVisualIntensity(0)).toBeNull();
    expect(getCompactToolWheelReboundVisualIntensity(0.199)).toBeNull();
    expect(getCompactToolWheelReboundVisualIntensity(0.2)).toBe(0.38);
    expect(getCompactToolWheelReboundVisualIntensity(-0.36)).toBe(0.38);
    expect(getCompactToolWheelReboundVisualIntensity(0.399)).toBe(0.38);
    expect(getCompactToolWheelReboundVisualIntensity(0.4)).toBe(0.6);
    expect(getCompactToolWheelReboundVisualIntensity(-0.62)).toBe(0.6);
    expect(getCompactToolWheelReboundVisualIntensity(0.699)).toBe(0.6);
    expect(getCompactToolWheelReboundVisualIntensity(0.7)).toBe(0.85);
    expect(getCompactToolWheelReboundVisualIntensity(-0.9)).toBe(0.85);
  });

  it('rotates compact input tools from a guide request', async () => {
    const { rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactToolFanOpenRequest={{
          id: 'compact-tool-fan-open-guide',
          open: true,
          reason: 'avatar-floating-guide-open-tool-fan',
        }}
      />,
    );

    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('.compact-input-tool-item-galgame')).toHaveAttribute('data-compact-tool-wheel-slot', '-1');

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactToolFanOpenRequest={{
          id: 'compact-tool-fan-open-guide',
          open: true,
          reason: 'avatar-floating-guide-open-tool-fan',
        }}
        compactToolWheelRotateRequest={{
          id: 'compact-tool-wheel-rotate-guide',
          direction: 1,
          stepCount: 1,
          reason: 'avatar-floating-guide-galgame-drag',
          forceFast: true,
        }}
      />,
    );

    await waitFor(() => {
      expect(fan.querySelector('.compact-input-tool-item-galgame')).toHaveAttribute('data-compact-tool-wheel-slot', '-2');
    });
  });

  it('sets compact input tool wheel index from a guide request', async () => {
    const { rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactToolFanOpenRequest={{
          id: 'compact-tool-fan-open-guide',
          open: true,
          reason: 'avatar-floating-guide-open-tool-fan',
        }}
        compactToolWheelIndexRequest={{
          id: 'compact-tool-wheel-index-non-default',
          index: 6,
          reason: 'test-non-default',
        }}
      />,
    );

    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    await waitFor(() => {
      expect(fan.querySelector('.compact-input-tool-item-galgame')).toHaveAttribute('data-compact-tool-wheel-slot', '0');
    });

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        compactToolFanOpenRequest={{
          id: 'compact-tool-fan-open-guide',
          open: true,
          reason: 'avatar-floating-guide-open-tool-fan',
        }}
        compactToolWheelIndexRequest={{
          id: 'compact-tool-wheel-index-day3-entry',
          index: 0,
          reason: 'avatar-floating-guide-day3-entry-reset',
        }}
      />,
    );

    await waitFor(() => {
      expect(fan.querySelector('.compact-input-tool-item-galgame')).toHaveAttribute('data-compact-tool-wheel-slot', '-1');
    });
  });

  it('toggles compact history from a guide request', async () => {
    const { rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactHistoryOpenRequest={{
          id: 'compact-history-open-guide',
          open: true,
          reason: 'avatar-floating-guide-history',
        }}
      />,
    );

    await waitFor(() => {
      expect(document.body.querySelector('.compact-export-history-anchor')).not.toBeNull();
    });
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');

    rerender(
      <App
        chatSurfaceMode="compact"
        compactHistoryOpenRequest={{
          id: 'compact-history-close-guide',
          open: false,
          reason: 'avatar-floating-guide-history',
        }}
      />,
    );

    await waitFor(() => {
      expect(document.body.querySelector('.compact-export-history-anchor')).toHaveAttribute(
        'data-compact-export-history-visibility',
        'closing',
      );
    });
    expect(window.localStorage.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY)).toBe('true');
  });

  // 折中语义（取舍脉络见 App.tsx openCompactInputToolFan 注释）：
  // 轮盘转角在「会话内」（组件存活期间）延续上次滚到的位置，关闭再展开不弹回默认；
  // 但「不」持久化到 localStorage —— 页面刷新/组件重挂后随 useState 初值复位到环位 0。
  it('keeps the compact input tool wheel position within a session but resets after remount', () => {
    const { unmount } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );

    const actionButton = screen.getByRole('button', { name: '更多工具' });
    fireEvent.click(actionButton);
    let fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.wheel(fan, { deltaY: 80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
    // 转角不再写入 localStorage —— 该 key 始终为空。
    expect(window.localStorage.getItem(COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY)).toBeNull();

    // 会话内关闭再展开：保持上次转出来的位置（avatar 仍在正中槽位 0）。
    fireEvent.click(actionButton);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    fireEvent.click(actionButton);
    fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

    // 卸载再重挂（模拟页面刷新）：转角复位回默认环位 0（screenshot 居中）。
    unmount();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
    // 重挂后依然不产生持久化写入。
    expect(window.localStorage.getItem(COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY)).toBeNull();
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
      const fanRectSpy = mockCompactToolFanRect(fan);
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

      try {
        fireEvent.pointerDown(fan, {
          pointerId: 21,
          ...compactToolWheelPoint(0),
          button: 0,
          buttons: 1,
          pointerType: 'mouse',
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(16);
        });
        fireEvent.pointerMove(fan, {
          pointerId: 21,
          ...compactToolWheelPoint(40 * (Math.PI / 180)),
          buttons: 1,
          pointerType: 'mouse',
        });
        expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

        fireEvent.pointerUp(fan, {
          pointerId: 21,
          ...compactToolWheelPoint(40 * (Math.PI / 180)),
          buttons: 0,
          pointerType: 'mouse',
        });

        await act(async () => {
          await vi.advanceTimersByTimeAsync(900);
        });
        expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
      } finally {
        fanRectSpy.mockRestore();
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it('charges compact input tool wheel after sustained one-way drag and releases visually without changing the final center', async () => {
    vi.useFakeTimers();
    const playSfx = vi.fn();
    const preloadSfx = vi.fn();
    const GameAudioSystem = vi.fn().mockImplementation(() => ({ playSfx, preloadSfx }));
    (window as Window & {
      NekoGameSystem?: {
        GameAudioSystem: new () => { playSfx: typeof playSfx; preloadSfx: typeof preloadSfx };
      };
    }).NekoGameSystem = { GameAudioSystem };
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
          ...pointOnWheel(index * 0.7),
          buttons: 1,
          pointerType: 'mouse',
        });
      }
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');

      for (let index = 13; index <= 24; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 22,
          ...pointOnWheel(index * 0.7),
          buttons: 1,
          pointerType: 'mouse',
        });
      }

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-direction', 'forward');
      const beforeReleaseCenter = fan.querySelector('[data-compact-tool-wheel-slot="0"]')?.className;
      expect(beforeReleaseCenter).toBeTruthy();
      const playSfxCallsBeforeRelease = playSfx.mock.calls.length;
      fireEvent.pointerUp(fan, {
        pointerId: 22,
        ...pointOnWheel(24 * 0.7),
        buttons: 0,
        pointerType: 'mouse',
      });

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'true');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')?.className).not.toBe(beforeReleaseCenter);
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-offset', '6');

      for (let timerRun = 0; timerRun < 64 && fan.getAttribute('data-compact-tool-wheel-charge-release-active') === 'true'; timerRun += 1) {
        await act(async () => {
          await vi.advanceTimersToNextTimerAsync();
        });
      }
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'false');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-offset', '0');
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')?.className).toBe(beforeReleaseCenter);
      expect(Math.abs(Number.parseFloat(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle')))).toBeGreaterThan(4);
      const releasePlaySfxCalls = playSfx.mock.calls.slice(playSfxCallsBeforeRelease);
      expect(releasePlaySfxCalls.length).toBeGreaterThan(0);
      expect(releasePlaySfxCalls.every(([request]) => (
        typeof request === 'object'
        && request !== null
        && 'src' in request
        && request.src === COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS[0]
      ))).toBe(true);
      const playSfxCallsAfterRelease = playSfx.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(120);
      });
      expect(playSfx).toHaveBeenCalledTimes(playSfxCallsAfterRelease);
      expect(fan.style.getPropertyValue('--compact-tool-wheel-drag-angle')).toBe('0deg');
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
      for (let index = 1; index <= 28; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 23,
          ...pointOnWheel(index * 0.7),
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
        ...pointOnWheel(24 * 0.7),
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
        ...pointOnWheel(24 * 0.7),
        buttons: 0,
        pointerType: 'mouse',
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
    } finally {
      fanRectSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('rattles in two levels before auto releasing at max charge', async () => {
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
        pointerId: 24,
        ...pointOnWheel(0),
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      let index = 1;
      for (; index <= 80; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 24,
          ...pointOnWheel(index * 0.7),
          buttons: 1,
          pointerType: 'mouse',
        });
        if (fan.getAttribute('data-compact-tool-wheel-charge-rattle') === 'weak') {
          break;
        }
      }

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-rattle', 'weak');

      for (index += 1; index <= 100; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 24,
          ...pointOnWheel(index * 0.7),
          buttons: 1,
          pointerType: 'mouse',
        });
        if (fan.getAttribute('data-compact-tool-wheel-charge-rattle') === 'strong') {
          break;
        }
      }

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-rattle', 'strong');

      for (index += 1; index <= 120; index += 1) {
        fireEvent.pointerMove(fan, {
          pointerId: 24,
          ...pointOnWheel(index * 0.7),
          buttons: 1,
          pointerType: 'mouse',
        });
        if (fan.getAttribute('data-compact-tool-wheel-charge-release-active') === 'true') {
          break;
        }
      }

      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-active', 'false');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'true');
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-rattle', 'none');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(fan).toHaveAttribute('data-compact-tool-wheel-charge-release-active', 'false');
    } finally {
      fanRectSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('exposes a yarn-ball minimize entry in compact input and capsule states', () => {
    const onCompactMinimizeRequest = vi.fn();
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" onCompactMinimizeRequest={onCompactMinimizeRequest} />,
    );
    const ball = container.querySelector('.compact-chat-minimize-ball');
    expect(ball).not.toBeNull();
    // 毛绒球走 origin-drag 手势（单击折叠 / 长按拖 surface，与右侧轮盘原点对偶），
    // 标记 no-drag 避免宿主被动 hit-test 重复起拖。
    expect(ball).toHaveAttribute('data-compact-no-drag', 'true');
    // 纯单击（无拖动）折叠为 minimized。
    fireEvent.click(ball!);
    expect(onCompactMinimizeRequest).toHaveBeenCalledTimes(1);
    // 胶囊态同样有毛绒球折叠入口（两态都覆盖）。
    rerender(<App chatSurfaceMode="compact" compactChatState="default" onCompactMinimizeRequest={onCompactMinimizeRequest} />);
    expect(container.querySelector('.compact-chat-minimize-ball')).not.toBeNull();
  });

  it('clears the active avatar tool cursor before compact minimize', async () => {
    const onCompactMinimizeRequest = vi.fn();
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" onCompactMinimizeRequest={onCompactMinimizeRequest} />,
    );

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    await waitFor(() => {
      expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
    });

    fireEvent.click(container.querySelector('.compact-chat-minimize-ball') as HTMLButtonElement);

    expect(onCompactMinimizeRequest).toHaveBeenCalledTimes(1);
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
  });

  it('dispatches compact surface drag prime and renderer drag events from the input body', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);
    const input = document.body.querySelector('.composer-input') as HTMLTextAreaElement;
    const primes: Array<Record<string, number>> = [];
    const primeEnds: Array<Record<string, number>> = [];
    const grabs: Array<Record<string, number>> = [];
    const moves: Array<Record<string, number>> = [];
    const ends: Array<Record<string, number | string>> = [];
    const onPrime = (event: Event) => primes.push((event as CustomEvent).detail);
    const onPrimeEnd = (event: Event) => primeEnds.push((event as CustomEvent).detail);
    const onGrab = (event: Event) => grabs.push((event as CustomEvent).detail);
    const onMove = (event: Event) => moves.push((event as CustomEvent).detail);
    const onEnd = (event: Event) => ends.push((event as CustomEvent).detail);
    window.addEventListener('neko:compact-surface-drag-prime', onPrime);
    window.addEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
    window.addEventListener('neko:compact-surface-drag-grab', onGrab);
    window.addEventListener('neko:compact-surface-drag-move', onMove);
    window.addEventListener('neko:compact-surface-drag-end', onEnd);
    try {
      fireEvent.pointerDown(input, {
        pointerId: 51, clientX: 160, clientY: 46, screenX: 460, screenY: 340,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      expect(primes).toHaveLength(1);
      expect(primes[0]).toMatchObject({
        pointerId: 51,
        clientX: 160,
        clientY: 46,
        screenX: 460,
        screenY: 340,
      });
      fireEvent.pointerMove(document, {
        pointerId: 51, clientX: 182, clientY: 48, screenX: 482, screenY: 342,
        buttons: 1, pointerType: 'mouse',
      });
      expect(grabs).toHaveLength(1);
      expect(grabs[0]).toMatchObject({
        pointerId: 51,
        clientX: 160,
        clientY: 46,
        screenX: 460,
        screenY: 340,
        currentClientX: 182,
        currentClientY: 48,
      });
      expect(moves).toHaveLength(0);
      fireEvent.pointerMove(document, {
        pointerId: 51, clientX: 202, clientY: 60, screenX: 502, screenY: 354,
        buttons: 1, pointerType: 'mouse',
      });
      expect(moves).toHaveLength(1);
      expect(moves[0]).toMatchObject({
        pointerId: 51,
        clientX: 202,
        clientY: 60,
        screenX: 502,
        screenY: 354,
      });
      fireEvent.pointerUp(document, {
        pointerId: 51, clientX: 202, clientY: 60, screenX: 502, screenY: 354,
        buttons: 0, pointerType: 'mouse',
      });
      expect(primeEnds).toHaveLength(1);
      expect(primeEnds[0]).toMatchObject({ pointerId: 51 });
      expect(ends).toHaveLength(1);
      expect(ends[0]).toMatchObject({
        pointerId: 51,
        clientX: 202,
        clientY: 60,
        screenX: 502,
        screenY: 354,
        reason: 'pointerup',
      });
    } finally {
      window.removeEventListener('neko:compact-surface-drag-prime', onPrime);
      window.removeEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
      window.removeEventListener('neko:compact-surface-drag-grab', onGrab);
      window.removeEventListener('neko:compact-surface-drag-move', onMove);
      window.removeEventListener('neko:compact-surface-drag-end', onEnd);
    }
  });

  it('dispatches compact surface drag cleanup from document pointercancel', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);
    const input = document.body.querySelector('.composer-input') as HTMLTextAreaElement;
    const primeEnds: Array<Record<string, number>> = [];
    const ends: Array<Record<string, number | string>> = [];
    const onPrimeEnd = (event: Event) => primeEnds.push((event as CustomEvent).detail);
    const onEnd = (event: Event) => ends.push((event as CustomEvent).detail);
    window.addEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
    window.addEventListener('neko:compact-surface-drag-end', onEnd);
    try {
      fireEvent.pointerDown(input, {
        pointerId: 61, clientX: 120, clientY: 40, screenX: 320, screenY: 240,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerMove(document, {
        pointerId: 61, clientX: 150, clientY: 46, screenX: 350, screenY: 246,
        buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerCancel(document, {
        pointerId: 61, clientX: 150, clientY: 46, screenX: 350, screenY: 246,
        buttons: 0, pointerType: 'mouse',
      });
      expect(primeEnds).toEqual([{ pointerId: 61 }]);
      expect(ends).toHaveLength(1);
      expect(ends[0]).toMatchObject({
        pointerId: 61,
        clientX: 150,
        clientY: 46,
        screenX: 350,
        screenY: 246,
        reason: 'pointercancel',
      });
    } finally {
      window.removeEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
      window.removeEventListener('neko:compact-surface-drag-end', onEnd);
    }
  });

  it('finishes a replaced compact surface drag before priming the next pointer', () => {
    render(<App chatSurfaceMode="compact" compactChatState="input" />);
    const input = document.body.querySelector('.composer-input') as HTMLTextAreaElement;
    const minimize = document.body.querySelector('.compact-chat-minimize-ball') as HTMLButtonElement;
    const primes: Array<Record<string, number>> = [];
    const primeEnds: Array<Record<string, number>> = [];
    const ends: Array<Record<string, number | string>> = [];
    const sequence: string[] = [];
    const onPrime = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      primes.push(detail);
      sequence.push(`prime:${detail.pointerId}`);
    };
    const onPrimeEnd = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      primeEnds.push(detail);
      sequence.push(`prime-end:${detail.pointerId}`);
    };
    const onEnd = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      ends.push(detail);
      sequence.push(`end:${detail.pointerId}:${detail.reason}`);
    };
    window.addEventListener('neko:compact-surface-drag-prime', onPrime);
    window.addEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
    window.addEventListener('neko:compact-surface-drag-end', onEnd);
    try {
      fireEvent.pointerDown(input, {
        pointerId: 71, clientX: 150, clientY: 50, screenX: 450, screenY: 340,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerMove(document, {
        pointerId: 71, clientX: 175, clientY: 58, screenX: 475, screenY: 348,
        buttons: 1, pointerType: 'mouse',
      });
      fireEvent.pointerDown(minimize, {
        pointerId: 72, clientX: 95, clientY: 50, screenX: 395, screenY: 340,
        button: 0, buttons: 1, pointerType: 'mouse',
      });
      expect(primes).toHaveLength(2);
      expect(primes[0]).toMatchObject({ pointerId: 71 });
      expect(primes[1]).toMatchObject({ pointerId: 72 });
      expect(sequence).toEqual([
        'prime:71',
        'end:71:replaced',
        'prime-end:71',
        'prime:72',
      ]);
      expect(primeEnds).toContainEqual({ pointerId: 71 });
      expect(ends).toHaveLength(1);
      expect(ends[0]).toMatchObject({
        pointerId: 71,
        clientX: 175,
        clientY: 58,
        screenX: 475,
        screenY: 348,
        reason: 'replaced',
      });
      fireEvent.pointerUp(document, {
        pointerId: 72, clientX: 95, clientY: 50, screenX: 395, screenY: 340,
        buttons: 0, pointerType: 'mouse',
      });
    } finally {
      window.removeEventListener('neko:compact-surface-drag-prime', onPrime);
      window.removeEventListener('neko:compact-surface-drag-prime-end', onPrimeEnd);
      window.removeEventListener('neko:compact-surface-drag-end', onEnd);
    }
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
      fireEvent.pointerMove(document, {
        pointerId: 7, clientX: 122, clientY: 108, buttons: 1, pointerType: 'mouse',
      });
      // 拖动超阈值 → 派发一次抓取事件，锚点用按下点（不跳变）。
      expect(grabs).toHaveLength(1);
      expect(grabs[0]).toMatchObject({ clientX: 100, clientY: 100, screenX: 300, screenY: 320 });
      fireEvent.pointerUp(document, {
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

  it('suppresses the submit default action after dragging the compact submit toggle', () => {
    const onComposerSubmit = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onComposerSubmit={onComposerSubmit}
      />,
    );
    const input = document.body.querySelector('.composer-input') as HTMLTextAreaElement;
    const toggle = document.body.querySelector('.compact-input-tool-toggle') as HTMLButtonElement;
    fireEvent.change(input, { target: { value: 'hello' } });
    expect(toggle).toHaveAttribute('type', 'submit');

    fireEvent.pointerDown(toggle, {
      pointerId: 81, clientX: 120, clientY: 48, screenX: 420, screenY: 338,
      button: 0, buttons: 1, pointerType: 'mouse',
    });
    fireEvent.pointerMove(document, {
      pointerId: 81, clientX: 150, clientY: 56, screenX: 450, screenY: 346,
      buttons: 1, pointerType: 'mouse',
    });
    fireEvent.pointerUp(document, {
      pointerId: 81, clientX: 150, clientY: 56, screenX: 450, screenY: 346,
      buttons: 0, pointerType: 'mouse',
    });
    fireEvent.click(toggle);

    expect(onComposerSubmit).not.toHaveBeenCalled();
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
        ...compactToolWheelPoint(140 * (Math.PI / 180)),
        button: 0,
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 19,
        ...compactToolWheelPoint(180 * (Math.PI / 180)),
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerMove(fan, {
        pointerId: 19,
        ...compactToolWheelPoint(220 * (Math.PI / 180)),
        buttons: 1,
        pointerType: 'mouse',
      });
      fireEvent.pointerUp(fan, {
        pointerId: 19,
        ...compactToolWheelPoint(220 * (Math.PI / 180)),
        buttons: 0,
        pointerType: 'mouse',
      });

      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-translate');
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
    const fanRectSpy = mockCompactToolFanRect(fan);
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 40, buttons: 0, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    try {
      fireEvent.pointerDown(fan, { pointerId: 7, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 7, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');

      fireEvent.pointerUp(fan, { pointerId: 7, ...compactToolWheelPoint(40 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
      fireEvent.pointerMove(fan, { pointerId: 7, ...compactToolWheelPoint(90 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });
      expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-avatar');
    } finally {
      fanRectSpy.mockRestore();
    }
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
      const fanRectSpy = mockCompactToolFanRect(fan);
      try {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(240);
        });
        // 新工具顺序里 galgame 是环位 6（默认 slot -1）。反向拖一步（wheelIndex 0→6）
        // 把它转到正中 slot 0，再点击验证 toggle 后 fan 保持展开。
        fireEvent.pointerDown(fan, { pointerId: 1, ...compactToolWheelPoint(0), button: 0, buttons: 1, pointerType: 'mouse' });
        fireEvent.pointerMove(fan, { pointerId: 1, ...compactToolWheelPoint(-40 * (Math.PI / 180)), buttons: 1, pointerType: 'mouse' });
        fireEvent.pointerUp(fan, { pointerId: 1, ...compactToolWheelPoint(-40 * (Math.PI / 180)), buttons: 0, pointerType: 'mouse' });

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
        fanRectSpy.mockRestore();
      }
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

  it('keeps controlled compact input focused after submitting text for continuous typing', async () => {
    const onComposerSubmit = vi.fn();

    function CompactContinuousInputHarness() {
      const [compactChatState, setCompactChatState] = useState<CompactChatState>('input');
      return (
        <App
          chatSurfaceMode="compact"
          compactChatState={compactChatState}
          onCompactChatStateChange={setCompactChatState}
          onComposerSubmit={onComposerSubmit}
        />
      );
    }

    const { container } = render(<CompactContinuousInputHarness />);

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'First compact message' } });
    const sendButton = screen.getByRole('button', { name: 'Send' });
    sendButton.focus();
    expect(document.activeElement).toBe(sendButton);
    fireEvent.click(sendButton);

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'First compact message' });
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');
    expect(screen.getByPlaceholderText('Type a message...')).toHaveValue('');
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByPlaceholderText('Type a message...'));
    });
    const refocusedInput = screen.getByPlaceholderText('Type a message...') as HTMLTextAreaElement;
    expect(refocusedInput.selectionStart).toBe(refocusedInput.value.length);
    expect(refocusedInput.selectionEnd).toBe(refocusedInput.value.length);
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
    expect(input).toHaveValue('');
    expect(screen.getByRole('button', { name: '更多工具' })).toBeDisabled();
  });

  it('locks only compact text entry while tutorial input lock is active', async () => {
    const onComposerSubmit = vi.fn();
    const onComposerImportImage = vi.fn();
    renderInputApp({
      compactInputLocked: true,
      onComposerSubmit,
      onComposerImportImage,
    });

    const input = screen.getByPlaceholderText('Type a message...');
    expect(input).not.toBeDisabled();
    expect(input).toHaveAttribute('readonly');

    fireEvent.change(input, { target: { value: 'Blocked send' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(onComposerSubmit).not.toHaveBeenCalled();

    await openCompactInputTools();
    const importButton = document.body.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;
    rotateCompactToolToCenter(importButton);
    fireEvent.click(importButton);
    expect(onComposerImportImage).toHaveBeenCalledTimes(1);
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

    const importButton = document.body.querySelector('.compact-input-tool-item-import') as HTMLButtonElement;
    rotateCompactToolToCenter(importButton);
    fireEvent.click(importButton);
    expect(onComposerImportImage).toHaveBeenCalledTimes(1);

    await openCompactInputTools();
    const screenshotButton = document.body.querySelector('.compact-input-tool-item-screenshot') as HTMLButtonElement;
    rotateCompactToolToCenter(screenshotButton);
    fireEvent.click(screenshotButton);
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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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

  it('uses viewport positioning for cat-paw reward drops', async () => {
    const onAvatarInteraction = vi.fn();
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.1);
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
          right: 220,
          top: 100,
          bottom: 220,
          width: 120,
          height: 120,
        }),
      },
    });

    try {
      const { container } = renderInputApp({ onAvatarInteraction });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

      fireEvent.pointerDown(window, { button: 0, clientX: 190, clientY: 190 });

      expect(onAvatarInteraction).toHaveBeenCalledWith(expect.objectContaining({
        toolId: 'fist',
        rewardDrop: true,
      }));

      const firstDrop = container.querySelector<HTMLElement>('.fist-floating-drop');
      expect(firstDrop).not.toBeNull();
      expect(window.getComputedStyle(firstDrop!).position).toBe('fixed');
      expect(firstDrop).toHaveStyle({
        left: '171px',
        top: '159px',
      });
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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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

  it('keeps the lollipop desktop cursor image stable across avatar range changes', async () => {
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
      renderInputApp();

      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(240);
      });
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(90);
      });

      const avatarImage = () => document.body.querySelector('.avatar-cursor-overlay-image-lollipop');
      expect(avatarImage()).toHaveAttribute('src', '/static/icons/chat_sugar1_cursor.png');

      boundsAvailable = false;
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(90);
      });

      expect(avatarImage()).toHaveAttribute('src', '/static/icons/chat_sugar1_cursor.png');

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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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

  it('selects and clears an avatar tool from the quickbar', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    expect(screen.getByRole('group', { name: 'Avatar quick tools' })).toBeInTheDocument();

    const lollipopButton = document.body.querySelector<HTMLButtonElement>('[data-avatar-tool-id="lollipop"]');
    expect(lollipopButton).not.toBeNull();
    expect(lollipopButton).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(lollipopButton!);

    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
    expect(screen.queryByRole('group', { name: 'Avatar quick tools' })).toBeNull();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(screen.queryByRole('group', { name: 'Avatar quick tools' })).toBeNull();
  });

  it('closes the transparent compact tool fan after selecting an avatar cursor tool', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '棒棒糖' }));

    const fan = document.body.querySelector<HTMLElement>('.compact-input-tool-fan');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    expect(fan).toHaveAttribute('aria-hidden', 'true');
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
    expect(queryAvatarCursorOverlay()).not.toBeNull();
  });

  it('clears the selected compact avatar tool from the avatar wheel button', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    const lollipopButton = document.body.querySelector<HTMLButtonElement>('[data-avatar-tool-id="lollipop"]');
    expect(lollipopButton).not.toBeNull();
    fireEvent.click(lollipopButton!);

    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
    expect(screen.queryByRole('group', { name: 'Avatar quick tools' })).toBeNull();

    await openCompactInputTools();
    const fan = document.body.querySelector<HTMLElement>('.compact-input-tool-fan');
    const avatarTool = fan?.querySelector<HTMLElement>('.compact-input-tool-item-avatar');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(avatarTool).toHaveAttribute('data-compact-tool-active', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(queryAvatarCursorOverlay()).toBeNull();
    expect(screen.queryByRole('group', { name: 'Avatar quick tools' })).toBeNull();
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
  });

  it('disables avatar sub-actions when the avatar wheel slot is not actionable', async () => {
    const { container } = renderInputApp();

    await openCompactInputTools();
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    const lollipopButton = document.body.querySelector<HTMLButtonElement>('[data-avatar-tool-id="lollipop"]');
    const editButton = container.querySelector('.avatar-tool-quickbar-edit') as HTMLButtonElement;
    expect(lollipopButton).not.toBeNull();
    expect(lollipopButton).not.toBeDisabled();
    expect(editButton).not.toBeDisabled();

    fireEvent.wheel(fan, { deltaY: 80 });
    fireEvent.wheel(fan, { deltaY: 80 });
    fireEvent.wheel(fan, { deltaY: 80 });

    expect(fan.querySelector('.compact-input-tool-item-avatar')).toHaveAttribute('data-compact-tool-wheel-slot', '-2');
    expect(lollipopButton).toBeDisabled();
    expect(editButton).toBeDisabled();
  });

  it('clears the selected avatar tool from the quickbar bubble', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    const fistButton = document.body.querySelector<HTMLButtonElement>('[data-avatar-tool-id="fist"]');
    expect(fistButton).not.toBeNull();
    fireEvent.click(fistButton!);

    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
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
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '锤子' }), {
      clientX: 240,
      clientY: 320,
      screenX: 640,
      screenY: 420,
    });

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: true,
      toolId: 'hammer',
      variant: 'primary',
      imageKind: 'cursor',
      cursorClientX: 240,
      cursorClientY: 320,
      cursorScreenX: 640,
      cursorScreenY: 420,
      tool: expect.objectContaining({
        id: 'hammer',
        cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
        cursorHotspotX: 50,
        cursorHotspotY: 54,
        cursorNaturalWidth: 100,
        cursorNaturalHeight: 96,
        cursorDisplayWidth: 100,
        cursorDisplayHeight: 96,
      }),
    }));
  });

  it('anchors the desktop cursor overlay to the current pointer when a tool is activated', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }), {
      clientX: 240,
      clientY: 320,
    });

    const overlay = queryAvatarCursorOverlay();
    expect(overlay).not.toBeNull();
    expect((overlay as HTMLDivElement).style.transform).toBe('translate3d(218.16px, 294.24px, 0)');
  });

  it('moves the desktop cursor overlay synchronously with pointer movement', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }), {
      clientX: 240,
      clientY: 320,
    });

    const overlay = queryAvatarCursorOverlay();
    expect(overlay).not.toBeNull();

    fireEvent.pointerMove(window, { clientX: 420, clientY: 360 });

    expect((overlay as HTMLDivElement).style.transform).toBe('translate3d(398.16px, 334.24px, 0)');
  });

  it('expands the desktop avatar tool overlay when the pointer enters the avatar range', async () => {
    const onAvatarToolStateChange = vi.fn();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);
    const restoreLive2dManager = installLive2dBoundsMock();

    try {
      renderInputApp({ onAvatarToolStateChange });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }), {
        clientX: 20,
        clientY: 20,
      });

      const overlay = queryAvatarCursorOverlay();
      expect(overlay).not.toBeNull();
      expect(overlay).toHaveClass('is-compact');
      expect(overlay?.querySelector('img')).toHaveAttribute('src', '/static/icons/cat_claw1_cursor.png');

      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await waitFor(() => {
        expect(overlay).not.toHaveClass('is-compact');
        expect(overlay?.querySelector('img')).toHaveAttribute('src', '/static/icons/cat_claw1.png');
        expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
          active: true,
          toolId: 'fist',
          imageKind: 'icon',
          withinAvatarRange: true,
        }));
      });
    } finally {
      restoreLive2dManager();
      live2dContainer.remove();
    }
  });

  it('expands the desktop hammer overlay on avatar range hover before clicking', async () => {
    window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'false');
    const onAvatarToolStateChange = vi.fn();
    const live2dContainer = document.createElement('div');
    live2dContainer.id = 'live2d-container';
    Object.defineProperty(live2dContainer, 'getClientRects', {
      configurable: true,
      value: () => [{ width: 100, height: 100 }],
    });
    document.body.appendChild(live2dContainer);
    const restoreLive2dManager = installLive2dBoundsMock();

    try {
      renderInputApp({ onAvatarToolStateChange });

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '锤子' }), {
        clientX: 20,
        clientY: 20,
      });

      expect(queryHammerCursorCompactImage()).not.toBeNull();

      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });

      await waitFor(() => {
        expect(queryHammerCursorCompactImage()).toBeNull();
        expect(document.body.querySelector('.hammer-cursor-overlay')).not.toHaveClass('is-compact');
        expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
          active: true,
          toolId: 'hammer',
          imageKind: 'icon',
          withinAvatarRange: true,
        }));
      });

      onAvatarToolStateChange.mockClear();
      vi.useFakeTimers();
      fireEvent.pointerDown(window, { button: 0, clientX: 150, clientY: 150 });
      fireEvent.pointerMove(window, { clientX: 20, clientY: 20 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(16);
        await vi.advanceTimersByTimeAsync(220);
      });

      expect(queryHammerCursorCompactImage()).toBeNull();
      expect(document.body.querySelector('.hammer-cursor-overlay')).not.toHaveClass('is-compact');
      expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
        active: true,
        toolId: 'hammer',
        imageKind: 'icon',
        withinAvatarRange: false,
      }));
    } finally {
      vi.useRealTimers();
      window.localStorage.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, 'true');
      restoreLive2dManager();
      live2dContainer.remove();
    }
  });

  it('clears the tool cursor when the composer is hidden for voice mode', async () => {
    const { rerender } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(queryAvatarCursorOverlay()).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App compactChatState="input" composerHidden />);

    expect(queryAvatarCursorOverlay()).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('clears the tool cursor when the host issues a reset key', async () => {
    const { rerender } = renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(queryAvatarCursorOverlay()).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App compactChatState="input" _toolCursorResetKey="voice-mode-reset-1" />);

    expect(queryAvatarCursorOverlay()).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('preserves the outside-window cursor state when the host resets a tool cursor', async () => {
    const onAvatarToolStateChange = vi.fn();
    const { rerender } = renderInputApp({ onAvatarToolStateChange });

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));
    expect(queryAvatarCursorOverlay()).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    fireEvent.blur(window);

    await openCompactInputTools();
    onAvatarToolStateChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      insideHostWindow: true,
    }));
    expect(queryAvatarCursorOverlay()).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
    expect(screen.queryByRole('group', { name: 'Avatar quick tools' })).toBeNull();
  });

  it('restores the native cursor while desktop system UI owns focus', async () => {
    renderInputApp();

    await openCompactInputTools();
    fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(queryAvatarCursorOverlay()).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    fireEvent.blur(window);

    expect(queryAvatarCursorOverlay()).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');

    fireEvent.pointerMove(window, { clientX: 180, clientY: 260 });

    expect(queryAvatarCursorOverlay()).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');
  });

  it('uses the native cursor and clears it when leaving the Electron chat window', async () => {
    (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ = true;

    try {
      renderInputApp();

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

      expect(queryAvatarCursorOverlay()).toBeNull();
      expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

      fireEvent.pointerOut(window, { relatedTarget: null, clientX: 160, clientY: 220 });
      expect(queryAvatarCursorOverlay()).toBeNull();
      expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

      fireEvent.pointerOut(window, { relatedTarget: null, clientX: -1, clientY: 220 });

      expect(queryAvatarCursorOverlay()).toBeNull();
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
      renderInputApp();

      await openCompactInputTools();
      fireEvent.click(screen.getByRole('button', { name: 'Avatar tools' }));
      fireEvent.click(screen.getByRole('button', { name: '锤子' }));

      const compactImageBefore = queryHammerCursorCompactImage();
      expect(compactImageBefore).not.toBeNull();
      expect(compactImageBefore).toHaveAttribute('src', '/static/icons/chat_hammer1_cursor.png');

      fireEvent.pointerDown(window, { button: 0, clientX: 20, clientY: 20 });

      const compactImageAfter = queryHammerCursorCompactImage();
      expect(compactImageAfter).not.toBeNull();
      expect(compactImageAfter).toHaveAttribute('src', '/static/icons/chat_hammer2_cursor.png');
    } finally {
      delete (window as Window & { live2dManager?: unknown }).live2dManager;
      live2dContainer.remove();
    }
  });
});
