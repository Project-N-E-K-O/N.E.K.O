import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { parseChatMessage, type CompactChatState } from './message-schema';

describe('App', () => {
  it('renders the empty state when there are no messages', () => {
    render(<App />);

    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();
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

  it('renders a compact drag handle in compact display and input states only', () => {
    const { container, rerender } = render(<App chatSurfaceMode="compact" compactChatState="default" />);

    expect(container.querySelector('.compact-chat-capsule-shell .compact-chat-drag-handle')).not.toBeNull();
    expect(container.querySelector('[data-compact-geometry-item="capsule"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(container.querySelector('[data-compact-geometry-item="dragHandle"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');

    rerender(<App chatSurfaceMode="compact" compactChatState="input" />);
    expect(container.querySelector('.compact-chat-input-shell .compact-chat-drag-handle')).not.toBeNull();
    expect(container.querySelector('[data-compact-geometry-item="input"]')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(container.querySelector('[data-compact-geometry-part="inputBody"]')).not.toBeNull();

    rerender(<App chatSurfaceMode="full" />);
    expect(container.querySelector('.compact-chat-drag-handle')).toBeNull();
    expect(container.querySelector('[data-compact-geometry-owner="surface"]')).toBeNull();
  });

  it('elevates compact state to options when choices are visible', () => {
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

      const appShell = container.querySelector('.app-shell');
      const choiceLayer = document.body.querySelector('body > .compact-chat-choice-anchor');
      expect(appShell).not.toBeNull();
      expect(choiceLayer).not.toBeNull();

      Object.defineProperty(appShell!, 'getBoundingClientRect', {
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
          top: shellBottom - 260,
          left: 0,
          right: 420,
          bottom: shellBottom,
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

      await waitFor(() => {
        expect(choiceLayer).toHaveAttribute('data-compact-choice-placement', 'below');
      });

      shellBottom = 820;

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

  it('keeps compact-only state derivation out of the full surface', () => {
    const { container } = render(
      <App
        chatSurfaceMode="full"
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

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
    expect(container.querySelector('.composer-choice-layer')).not.toHaveClass('compact-chat-choice-anchor');
  });

  it('renders compact default as a single search-like entry without history or extra controls', () => {
    const message = parseChatMessage({
      id: 'assistant-compact-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '今天想让我陪你做什么呢？' }],
    });
    const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

    expect(container.querySelector('.compact-chat-stage-body-slot')).toHaveAttribute('data-compact-stage-fallback', 'message-list');
    expect(container.querySelector('.message-list')).toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).not.toBeNull();
    expect(container.querySelector('.compact-chat-inline-input')).toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent('今天想让我陪你做什么呢？');
    expect(container.querySelector('.compact-chat-entry-button')).toBeNull();
    expect(container.querySelector('.compact-chat-tool-btn')).toBeNull();
  });

  it('requests compact input when the single compact entry is clicked', () => {
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
        messages={[message]}
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '可以先说一句你今天想做什么' }));

    expect(onCompactChatStateChange).toHaveBeenCalledWith('input');
  });

  it('prefers the latest assistant text for compact preview instead of echoing the latest user message', () => {
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
      <App chatSurfaceMode="compact" messages={[assistantMessage, userMessage]} />,
    );

    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent('先看我这边的引导内容');
    expect(container.querySelector('.compact-chat-capsule-button')).not.toHaveTextContent('这是我刚刚发出的内容');
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
        <App chatSurfaceMode="compact" messages={[streamingAssistantMessage]} />,
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
        <App chatSurfaceMode="compact" messages={[settledAssistantMessage]} />,
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
      const [compactChatState, setCompactChatState] = useState<CompactChatState>('default');
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

    fireEvent.click(screen.getByRole('button', { name: '点开就直接输入吧' }));

    const input = await screen.findByPlaceholderText('Type a message...');
    await waitFor(() => {
      expect(input).toHaveFocus();
    });
  });

  it('prefers the latest assistant text for compact preview instead of echoing the latest user message', () => {
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
      <App chatSurfaceMode="compact" messages={[assistantMessage, userMessage]} />,
    );

    expect(container.querySelector('.compact-chat-capsule-button')).toHaveTextContent('先看我这边的引导内容');
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

    const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'true');
    expect(preview).toHaveTextContent('');
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
      const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

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

  it('does not route speech playback state into the full surface preview', async () => {
    vi.useFakeTimers();
    const streamingText = '完整聊天框不应该被紧凑态语音显字状态接管。';
    const message = parseChatMessage({
      id: 'assistant-full-speech-event-isolated',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: streamingText }],
      status: 'streaming',
    });

    try {
      const { container } = render(<App chatSurfaceMode="full" messages={[message]} />);

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

      expect(container.querySelector('.compact-chat-capsule-text')).toBeNull();
      expect(container.querySelector('.message-list')).toHaveTextContent(streamingText);
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
      const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

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

    const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

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
      const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

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
        <App chatSurfaceMode="compact" messages={[firstStreamingMessage, secondStreamingMessage]} />,
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
        <App chatSurfaceMode="compact" messages={[firstSettledMessage, secondStreamingMessage]} />,
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

  it('keeps settled compact preview text bounded after streaming ends', () => {
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

    const { container } = render(<App chatSurfaceMode="compact" messages={[message]} />);

    const preview = container.querySelector('.compact-chat-capsule-text');
    expect(preview).toHaveAttribute('data-compact-preview-streaming', 'false');
    expect(preview?.textContent?.length).toBe(84);
    expect(preview?.textContent?.endsWith('...')).toBe(true);
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
      const { container, rerender } = render(<App chatSurfaceMode="compact" messages={[streamingMessage]} />);

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

      rerender(<App chatSurfaceMode="compact" messages={[sentMessage]} />);

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
        <App chatSurfaceMode="compact" messages={[firstStreamingMessage]} />,
      );
      const preview = container.querySelector('.compact-chat-capsule-text') as HTMLSpanElement;
      expect(preview).not.toBeNull();
      Object.defineProperty(preview, 'scrollWidth', {
        configurable: true,
        value: 320,
      });

      rerender(
        <App chatSurfaceMode="compact" messages={[finalStreamingMessage]} />,
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

  it('renders compact input as the same surface with one inline action button', () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    expect(container.querySelector('.compact-chat-inline-input')).not.toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toBeNull();
    expect(container.querySelector('.composer-bottom-bar')).toBeNull();
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
    const actionButton = screen.getByRole('button', { name: '更多工具' });
    expect(actionButton).toBeInTheDocument();
    expect(actionButton.querySelector('img')).toHaveAttribute('src', '/static/icons/dropdown_arrow.png');
    expect(actionButton.querySelector('img')).toHaveClass('compact-input-tool-toggle-icon');
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

    const fan = document.body.querySelector('.compact-input-tool-fan');
    const shell = container.querySelector('.compact-chat-input-shell');
    const inlineInput = container.querySelector('.compact-chat-inline-input');
    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(fan).not.toBeNull();
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(fan?.parentElement).toBe(document.body);
    expect(inlineInput?.contains(fan)).toBe(false);
    expect(shell?.contains(fan)).toBe(false);
    expect(fan?.querySelectorAll('[data-compact-tool-wheel-slot="-2"], [data-compact-tool-wheel-slot="-1"], [data-compact-tool-wheel-slot="0"], [data-compact-tool-wheel-slot="1"], [data-compact-tool-wheel-slot="2"]')).toHaveLength(5);
    expect(fan?.querySelectorAll('[tabindex="0"]')).toHaveLength(3);
    expect(container.querySelectorAll('.send-button-circle')).toHaveLength(1);
  });

  it('keeps compact tool buttons clickable after pointer down starts on a button', () => {
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

    fireEvent.pointerDown(importButton, { pointerId: 3, clientX: 55, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(importButton, { pointerId: 3, clientX: 55, buttons: 0, pointerType: 'mouse' });
    fireEvent.click(importButton);

    expect(onComposerImportImage).toHaveBeenCalledTimes(1);
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
  });

  it('rotates compact input tools by pointer dragging while keeping three active buttons', () => {
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
    expect(fan.querySelectorAll('[tabindex="0"]')).toHaveLength(3);
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
    fireEvent.click(importButton);

    expect(onComposerImportImage).not.toHaveBeenCalled();
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });

  it('opens compact export history with an empty state when there are no messages', async () => {
    const onExportConversationClick = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onExportConversationClick={onExportConversationClick}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    expect(fan.querySelectorAll('.compact-input-tool-item')).toHaveLength(7);
    expect(fan.querySelector('.compact-input-tool-item-avatar-preview')).toBeNull();
    expect(fan.querySelector('.compact-input-tool-item-export')).not.toBeNull();

    fireEvent.pointerDown(fan, { pointerId: 8, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 8, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 8, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    const exportButton = fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement;
    expect(exportButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');
    expect(exportButton).toHaveAttribute('title', 'History');
    expect(exportButton).toHaveAttribute('aria-label', 'History');
    expect(exportButton).not.toHaveClass('is-active');
    expect(exportButton).toHaveAttribute('aria-pressed', 'false');
    expect(exportButton).toHaveAttribute('data-compact-tool-active', 'false');
    fireEvent.click(exportButton);

    expect(onExportConversationClick).not.toHaveBeenCalled();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'true');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '0');
    expect(container.querySelector('.compact-export-history-anchor')).not.toBeNull();
    expect(container.querySelector('.compact-export-history-empty')).toHaveTextContent('There is no conversation to export yet.');
    expect(container.querySelectorAll('[data-compact-export-history-message-id]')).toHaveLength(0);
    expect(container.querySelector('.compact-export-history-count')).toHaveTextContent('0/0');
    expect(container.querySelectorAll('.compact-export-history-control')).toHaveLength(4);
    container.querySelectorAll('.compact-export-history-control').forEach((button) => {
      expect(button).toBeDisabled();
    });
    expect(exportButton).toHaveClass('is-active');
    expect(exportButton).toHaveAttribute('aria-pressed', 'true');
    expect(exportButton).toHaveAttribute('data-compact-tool-active', 'true');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
  });

  it('toggles compact export history state instead of opening the full export flow when messages exist', async () => {
    const onExportConversationClick = vi.fn();
    const onCompactChatStateChange = vi.fn();
    const message = parseChatMessage({
      id: 'compact-export-history-message',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '可以导出的对话' }],
    });
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        onExportConversationClick={onExportConversationClick}
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 9, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 9, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 9, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    const exportButton = fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement;
    expect(exportButton).not.toHaveClass('is-active');
    expect(exportButton).toHaveAttribute('aria-pressed', 'false');
    expect(exportButton).toHaveAttribute('data-compact-tool-active', 'false');
    fireEvent.click(exportButton);

    expect(onExportConversationClick).not.toHaveBeenCalled();
    expect(onCompactChatStateChange).not.toHaveBeenCalled();
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'true');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.compact-chat-inline-input')).not.toBeNull();
    expect(exportButton).toHaveClass('is-active');
    expect(exportButton).toHaveAttribute('aria-pressed', 'true');
    expect(exportButton).toHaveAttribute('data-compact-tool-active', 'true');
    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    fireEvent.click(exportButton);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'input');
    expect(exportButton).not.toHaveClass('is-active');
    expect(exportButton).toHaveAttribute('aria-pressed', 'false');
    expect(exportButton).toHaveAttribute('data-compact-tool-active', 'false');
  });

  it('closes compact export history state after leaving compact mode', async () => {
    const message = parseChatMessage({
      id: 'compact-export-history-close-message',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '切换模式时关闭历史层' }],
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 10, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 10, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 10, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'true');

    rerender(<App chatSurfaceMode="full" messages={[message]} />);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '0');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'false');
  });

  it('keeps compact export history open with an empty state when messages are cleared', async () => {
    const message = parseChatMessage({
      id: 'compact-export-history-clear-message',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '清空消息时关闭历史层' }],
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 11, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 11, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 11, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'true');

    rerender(<App chatSurfaceMode="compact" compactChatState="input" messages={[]} />);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-history-open', 'true');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '0');
    expect(container.querySelector('.compact-export-history-empty')).toHaveTextContent('There is no conversation to export yet.');
    expect(container.querySelector('.compact-export-history-count')).toHaveTextContent('0/0');
    expect(fan.querySelector('.compact-input-tool-item-export')).toHaveClass('is-active');
    expect(fan.querySelector('.compact-input-tool-item-export')).toHaveAttribute('aria-pressed', 'true');
    expect(fan.querySelector('.compact-input-tool-item-export')).toHaveAttribute('data-compact-tool-active', 'true');
  });

  it('renders compact export history without the message list cap and keeps assistant left/user right', async () => {
    const messages = Array.from({ length: 60 }, (_, index) => parseChatMessage({
      id: `compact-export-history-long-${index}`,
      role: index % 2 === 0 ? 'assistant' : 'user',
      author: index % 2 === 0 ? 'Neko' : 'You',
      time: `10:${String(index).padStart(2, '0')}`,
      createdAt: index,
      blocks: [{ type: 'text', text: `history item ${index}` }],
      status: 'sent',
    }));
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={messages} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 12, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 12, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 12, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    expect(history).not.toBeNull();
    expect(history).toHaveAttribute('data-compact-geometry-item', 'history');
    expect(history).toHaveAttribute('data-compact-geometry-hit-scope', 'children');
    expect(history.querySelector('.compact-export-history-scroll')).toHaveAttribute('data-compact-hit-region', 'true');
    expect(history.querySelector('.compact-export-history-scroll')).toHaveAttribute('data-compact-hit-region-kind', 'scroll');
    const rows = history.querySelectorAll('[data-compact-export-history-message-id]');
    expect(rows).toHaveLength(60);
    expect(rows[0]).toHaveClass('is-assistant');
    expect(rows[1]).toHaveClass('is-user');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('0/60');

    fireEvent.click(history.querySelector('.compact-export-history-control') as HTMLButtonElement);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '60');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('60/60');
  });

  it('supports compact export history bubble selection and selection controls', async () => {
    const messages = [
      parseChatMessage({
        id: 'compact-export-select-assistant',
        role: 'assistant',
        author: 'Neko',
        time: '10:00',
        blocks: [{ type: 'text', text: '第一条' }],
        status: 'sent',
      }),
      parseChatMessage({
        id: 'compact-export-select-user',
        role: 'user',
        author: 'You',
        time: '10:01',
        blocks: [{ type: 'text', text: '第二条' }],
        status: 'sent',
      }),
    ];
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={messages} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 13, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 13, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 13, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    const rows = history.querySelectorAll('[data-compact-export-history-message-id]');
    const selectAll = history.querySelectorAll('.compact-export-history-control')[0] as HTMLButtonElement;
    const clear = history.querySelectorAll('.compact-export-history-control')[1] as HTMLButtonElement;
    const invert = history.querySelectorAll('.compact-export-history-control')[2] as HTMLButtonElement;
    const controlsBar = history.querySelector('.compact-export-history-controls') as HTMLElement;
    const controlsContent = history.querySelector('.compact-export-history-controls-content') as HTMLElement;
    const controlsToggle = history.querySelector('.compact-export-history-controls-toggle') as HTMLButtonElement;

    expect(controlsBar).toHaveAttribute('data-compact-hit-region', 'true');
    expect(controlsBar).toHaveAttribute('data-compact-hit-region-kind', 'controls');
    expect(controlsBar).toHaveAttribute('data-compact-export-controls-collapsed', 'false');
    expect(controlsToggle).toHaveAttribute('aria-expanded', 'true');
    expect(controlsContent).not.toHaveAttribute('hidden');

    fireEvent.click(controlsToggle);
    expect(controlsBar).toHaveAttribute('data-compact-export-controls-collapsed', 'true');
    expect(controlsToggle).toHaveAttribute('aria-expanded', 'false');
    expect(controlsContent).toHaveAttribute('hidden');

    fireEvent.click(controlsToggle);
    expect(controlsBar).toHaveAttribute('data-compact-export-controls-collapsed', 'false');
    expect(controlsToggle).toHaveAttribute('aria-expanded', 'true');
    expect(controlsContent).not.toHaveAttribute('hidden');

    fireEvent.click(rows[0]);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'true');
    expect(rows[1]).toHaveClass('is-unselected');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('1/2');

    fireEvent.click(clear);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'false');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('0/2');

    fireEvent.click(selectAll);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'true');
    expect(rows[1]).toHaveAttribute('aria-pressed', 'true');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('2/2');

    fireEvent.click(invert);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'false');
    expect(rows[1]).toHaveAttribute('aria-pressed', 'false');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('0/2');
  });

  it('opens an inline compact export preview shell that follows the shared selection', async () => {
    const messages = [
      parseChatMessage({
        id: 'compact-export-preview-assistant',
        role: 'assistant',
        author: 'Neko',
        time: '10:00',
        blocks: [{ type: 'text', text: '预览第一条' }],
        status: 'sent',
      }),
      parseChatMessage({
        id: 'compact-export-preview-user',
        role: 'user',
        author: 'You',
        time: '10:01',
        blocks: [{ type: 'text', text: '预览第二条' }],
        status: 'sent',
      }),
    ];
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={messages} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 19, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 19, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 19, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    const rows = history.querySelectorAll('[data-compact-export-history-message-id]');
    const controls = history.querySelectorAll('.compact-export-history-control');
    const exportPreview = controls[3] as HTMLButtonElement;

    fireEvent.click(rows[0]);
    fireEvent.click(exportPreview);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'true');
    expect(history).toHaveClass('has-preview');
    expect(history.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-export-preview-open', 'true');
    expect(history.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-hit-region', 'true');
    expect(history.querySelector('.compact-export-preview-region')).toHaveAttribute('data-compact-hit-region-kind', 'preview');
    expect(history.querySelector('.compact-export-history-scroll')).toBeNull();
    expect(history.querySelector('.compact-export-history-controls')).toBeNull();
    expect(history.querySelectorAll('[data-compact-export-preview-message-id]')).toHaveLength(1);
    expect(history.querySelector('[data-compact-export-preview-message-id="compact-export-preview-assistant"]')).not.toBeNull();
    expect(history.querySelector('.compact-export-preview-subtitle')).toHaveTextContent('1/2');
    expect(history.querySelector('.compact-export-preview-empty')).toBeNull();
    history.querySelectorAll('.compact-export-preview-action').forEach((button) => {
      expect(button).toBeDisabled();
    });

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={messages}
      />,
    );

    expect(history.querySelectorAll('[data-compact-export-preview-message-id]')).toHaveLength(1);

    fireEvent.click(history.querySelector('.compact-export-preview-back') as HTMLButtonElement);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'false');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '1');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('1/2');
    const restoredRows = history.querySelectorAll('[data-compact-export-history-message-id]');
    fireEvent.click(restoredRows[1]);
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('2/2');

    const restoredExportPreview = history.querySelectorAll('.compact-export-history-control')[3] as HTMLButtonElement;
    fireEvent.click(restoredExportPreview);
    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[]}
      />,
    );

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-preview-open', 'true');
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '0');
    expect(history.querySelectorAll('[data-compact-export-preview-message-id]')).toHaveLength(0);
    expect(history.querySelector('.compact-export-preview-empty')).toHaveTextContent('Select at least one message to export.');
    expect(history.querySelector('.compact-export-preview-subtitle')).toHaveTextContent('0/0');
  });

  it('does not select sending messages or toggle a bubble from inner message actions', async () => {
    const onMessageAction = vi.fn();
    const messages = [
      parseChatMessage({
        id: 'compact-export-sent-with-action',
        role: 'assistant',
        author: 'Neko',
        time: '10:00',
        blocks: [
          { type: 'text', text: '带按钮的消息' },
          {
            type: 'buttons',
            buttons: [{ id: 'act-1', label: '内部按钮', action: 'test' }],
          },
        ],
        status: 'sent',
      }),
      parseChatMessage({
        id: 'compact-export-sending-disabled',
        role: 'user',
        author: 'You',
        time: '10:01',
        blocks: [{ type: 'text', text: '发送中的消息' }],
        status: 'sending',
      }),
    ];
    const { container } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={messages}
        onMessageAction={onMessageAction}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 14, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 14, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 14, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    const rows = history.querySelectorAll('[data-compact-export-history-message-id]');
    fireEvent.click(screen.getByRole('button', { name: '内部按钮' }));
    expect(onMessageAction).toHaveBeenCalledTimes(1);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'false');
    expect(rows[1]).toHaveAttribute('aria-disabled', 'true');

    fireEvent.click(history.querySelector('.compact-export-history-control') as HTMLButtonElement);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '1');
    expect(rows[0]).toHaveAttribute('aria-pressed', 'true');
    expect(rows[1]).toHaveAttribute('aria-pressed', 'false');
  });

  it('turns off compact export history auto scroll after the user scrolls upward', async () => {
    const messages = Array.from({ length: 6 }, (_, index) => parseChatMessage({
      id: `compact-export-scroll-${index}`,
      role: 'assistant',
      author: 'Neko',
      time: `10:0${index}`,
      blocks: [{ type: 'text', text: `scroll item ${index}` }],
      status: 'sent',
    }));
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={messages} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 15, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 15, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 15, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const scroll = container.querySelector('.compact-export-history-scroll') as HTMLDivElement;
    Object.defineProperty(scroll, 'scrollHeight', { configurable: true, value: 500 });
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 120 });
    scroll.scrollTop = 40;
    fireEvent.scroll(scroll);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'false');

    scroll.scrollTop = 380;
    fireEvent.scroll(scroll);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'true');
  });

  it('restores compact export history auto scroll for non-text outgoing actions', async () => {
    const onComposerSubmit = vi.fn();
    const onGalgameOptionSelect = vi.fn();
    const message = parseChatMessage({
      id: 'compact-export-outgoing-action',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '主动动作恢复贴底' }],
      status: 'sent',
    });
    const { container, rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        onComposerSubmit={onComposerSubmit}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 16, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 16, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 16, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const scroll = container.querySelector('.compact-export-history-scroll') as HTMLDivElement;
    Object.defineProperty(scroll, 'scrollHeight', { configurable: true, value: 500 });
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 120 });
    scroll.scrollTop = 40;
    fireEvent.scroll(scroll);
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'false');

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        messages={[message]}
        composerAttachments={[{ id: 'img-1', url: 'data:image/png;base64,aaa', alt: 'Screenshot 1' }]}
        onComposerSubmit={onComposerSubmit}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: '' });
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'true');
    await act(async () => {
      await new Promise((resolve) => window.requestAnimationFrame(() => resolve(undefined)));
    });

    scroll.scrollTop = 40;
    fireEvent.scroll(scroll);
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'false');

    rerender(
      <App
        chatSurfaceMode="compact"
        compactChatState="default"
        messages={[message]}
        galgameModeEnabled
        galgameOptions={[{ label: 'A', text: 'Option A' }]}
        onGalgameOptionSelect={onGalgameOptionSelect}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Option A' }));

    expect(onGalgameOptionSelect).toHaveBeenCalledWith({ label: 'A', text: 'Option A' });
    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-auto-scroll', 'true');
  });

  it('limits compact export select all to the export selection cap', async () => {
    const messages = Array.from({ length: 120 }, (_, index) => parseChatMessage({
      id: `compact-export-limit-${index}`,
      role: index % 2 === 0 ? 'assistant' : 'user',
      author: index % 2 === 0 ? 'Neko' : 'You',
      time: `10:${String(index % 60).padStart(2, '0')}`,
      blocks: [{ type: 'text', text: `limit item ${index}` }],
      status: 'sent',
    }));
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={messages} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 16, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 16, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 16, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    fireEvent.click(history.querySelector('.compact-export-history-control') as HTMLButtonElement);

    expect(container.querySelector('.app-shell')).toHaveAttribute('data-compact-export-selected-count', '100');
    expect(history.querySelector('.compact-export-history-count')).toHaveTextContent('100/120');
  });

  it('supports keyboard selection and ignores pointer drags in compact export history', async () => {
    const message = parseChatMessage({
      id: 'compact-export-keyboard',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '键盘选择' }],
      status: 'sent',
    });
    const { container } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 17, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 17, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 17, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    const row = container.querySelector('[data-compact-export-history-message-id="compact-export-keyboard"]') as HTMLElement;
    fireEvent.pointerDown(row, { pointerId: 18, clientX: 10, clientY: 10, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(row, { pointerId: 18, clientX: 34, clientY: 10, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(row, { pointerId: 18, clientX: 34, clientY: 10, buttons: 0, pointerType: 'mouse' });
    fireEvent.click(row);

    expect(row).toHaveAttribute('aria-pressed', 'false');

    fireEvent.keyDown(row, { key: 'Enter' });
    expect(row).toHaveAttribute('aria-pressed', 'true');

    fireEvent.keyDown(row, { key: ' ' });
    expect(row).toHaveAttribute('aria-pressed', 'false');
  });

  it('yields compact export history interaction only when compact choices are above history', async () => {
    const message = parseChatMessage({
      id: 'compact-export-choice-yield',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '选项打开时历史层让位' }],
      status: 'sent',
    });
    const { container, rerender } = render(
      <App chatSurfaceMode="compact" compactChatState="input" messages={[message]} />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 19, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 19, clientX: 156, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 19, clientX: 156, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(fan.querySelector('.compact-input-tool-item-export') as HTMLButtonElement);

    let history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
    expect(history).toHaveAttribute('data-compact-export-under-choice', 'false');

    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: { compactChoicePlacement?: 'above' | 'below' } | null;
    };
    try {
      desktopWindow.__nekoDesktopCompactLayout = { compactChoicePlacement: 'below' };
      rerender(
        <App
          chatSurfaceMode="compact"
          compactChatState="default"
          messages={[message]}
          galgameModeEnabled
          galgameOptions={[{ label: 'A', text: 'Option A' }]}
        />,
      );
      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(document.body.querySelector('body > .compact-chat-choice-anchor')).toHaveAttribute('data-compact-choice-placement', 'below');
      });
      history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
      expect(history).not.toHaveClass('under-choice-prompt');
      expect(history).toHaveAttribute('data-compact-export-under-choice', 'false');

      desktopWindow.__nekoDesktopCompactLayout = { compactChoicePlacement: 'above' };
      fireEvent(window, new Event('resize'));

      await waitFor(() => {
        expect(document.body.querySelector('body > .compact-chat-choice-anchor')).toHaveAttribute('data-compact-choice-placement', 'above');
      });
      history = container.querySelector('.compact-export-history-anchor') as HTMLElement;
      expect(history).toHaveClass('under-choice-prompt');
      expect(history).toHaveAttribute('data-compact-export-under-choice', 'true');
    } finally {
      desktopWindow.__nekoDesktopCompactLayout = null;
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

    fireEvent.wheel(fan, { deltaY: 80 });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 40, buttons: 0, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-import');

    fireEvent.pointerDown(fan, { pointerId: 7, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 60, buttons: 1, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');

    fireEvent.pointerUp(fan, { pointerId: 7, clientX: 60, buttons: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 7, clientX: 10, buttons: 0, pointerType: 'mouse' });
    expect(fan.querySelector('[data-compact-tool-wheel-slot="0"]')).toHaveClass('compact-input-tool-item-screenshot');
  });

  it('keeps compact toggle tools open and shows their active state after toggling', async () => {
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

    render(<Harness />);

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, { pointerId: 1, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 1, clientX: 60, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 1, clientX: 60, buttons: 0, pointerType: 'mouse' });
    fireEvent.pointerDown(fan, { pointerId: 2, clientX: 100, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerMove(fan, { pointerId: 2, clientX: 60, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(fan, { pointerId: 2, clientX: 60, buttons: 0, pointerType: 'mouse' });

    const galgameButton = fan.querySelector('.compact-input-tool-item-galgame') as HTMLButtonElement;
    expect(galgameButton).toHaveAttribute('data-compact-tool-wheel-slot', '0');
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    fireEvent.click(galgameButton);

    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    expect(galgameButton).toHaveClass('is-active');
    expect(galgameButton).toHaveAttribute('data-compact-tool-active', 'true');
    expect(galgameButton).toHaveAttribute('aria-pressed', 'true');
  });

  it('restores the full chat body and composer after leaving compact input mode', () => {
    const { container, rerender } = render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        galgameModeEnabled
        translateEnabled
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    expect(document.body.querySelector('.compact-input-tool-fan')).not.toBeNull();

    rerender(
      <App
        chatSurfaceMode="full"
        galgameModeEnabled
        translateEnabled
      />,
    );

    expect(container.querySelector('.chat-window')).toHaveClass('chat-surface-mode-full');
    expect(container.querySelector('.window-topbar')).not.toBeNull();
    expect(container.querySelector('.message-list')).not.toBeNull();
    expect(container.querySelector('.composer-bottom-bar')).not.toBeNull();
    expect(document.body.querySelector('.compact-input-tool-fan')).toBeNull();
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
    expect(document.body.querySelector('.compact-chat-inline-input')).not.toBeNull();
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
  });

  it('closes compact input tools when the desktop fan layer covers the toggle origin', () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    const fan = document.body.querySelector('.compact-input-tool-fan') as HTMLDivElement;
    fireEvent.pointerDown(fan, {
      pointerId: 12,
      clientX: 16,
      clientY: 16,
      button: 0,
      buttons: 1,
      pointerType: 'mouse',
    });

    expect(fan).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    expect(document.body.querySelector('.compact-chat-inline-input')).not.toBeNull();
    expect(onCompactChatStateChange).not.toHaveBeenCalledWith('default');
  });

  it('collapses empty compact input when desktop compact pointer leaves native hit regions', () => {
    const onCompactChatStateChange = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        compactChatState="input"
        onCompactChatStateChange={onCompactChatStateChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    fireEvent(window, new CustomEvent('neko:desktop-compact-pointer-outside'));

    expect(document.body.querySelector('.compact-input-tool-fan')).toHaveAttribute('data-compact-input-tool-fan-open', 'false');
    expect(onCompactChatStateChange).toHaveBeenCalledWith('default');
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

  it('collapses compact input back to display state when it loses focus with no content', async () => {
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

  it('collapses compact input on window blur even when focus remains in the compact shell', async () => {
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

  it('collapses compact input when a document-level outside pointer starts with no content', async () => {
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

  it('re-attaches the composer width observer after returning from compact mode', async () => {
    vi.useFakeTimers();
    const originalResizeObserver = globalThis.ResizeObserver;
    const observerInstances: ResizeObserverMock[] = [];

    const emitResize = (observer: ResizeObserverMock, width: number) => {
      if (!observer.target) {
        throw new Error('ResizeObserver target missing in test');
      }
      observer.callback([
        {
          target: observer.target,
          contentRect: {
            width,
            height: 40,
            x: 0,
            y: 0,
            top: 0,
            left: 0,
            bottom: 40,
            right: width,
            toJSON: () => ({}),
          },
        } as ResizeObserverEntry,
      ], observer as unknown as ResizeObserver);
    };

    class ResizeObserverMock {
      readonly callback: ResizeObserverCallback;
      target: Element | null = null;

      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        observerInstances.push(this);
      }

      observe(target: Element) {
        this.target = target;
      }

      disconnect() {}
      unobserve() {}
      takeRecords() { return []; }
    }

    globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;

    try {
      const { container, rerender } = render(<App />);
      expect(observerInstances.length).toBeGreaterThanOrEqual(1);
      const initialObserverCount = observerInstances.length;

      rerender(<App chatSurfaceMode="compact" compactChatState="input" />);
      rerender(<App chatSurfaceMode="full" />);
      expect(observerInstances.length).toBeGreaterThan(initialObserverCount);

      await act(async () => {
        emitResize(observerInstances[observerInstances.length - 1], 420);
        vi.advanceTimersByTime(300);
      });

      expect(container.querySelector('.composer-overflow-btn')).toBeNull();
      expect(container.querySelector('.composer-galgame-btn')).not.toBeNull();
    } finally {
      globalThis.ResizeObserver = originalResizeObserver;
      vi.useRealTimers();
    }
  });

  it('re-attaches the composer width observer after returning from compact mode', async () => {
    vi.useFakeTimers();
    const originalResizeObserver = globalThis.ResizeObserver;
    const observerInstances: ResizeObserverMock[] = [];

    const emitResize = (observer: ResizeObserverMock, width: number) => {
      if (!observer.target) {
        throw new Error('ResizeObserver target missing in test');
      }
      observer.callback([
        {
          target: observer.target,
          contentRect: {
            width,
            height: 40,
            x: 0,
            y: 0,
            top: 0,
            left: 0,
            bottom: 40,
            right: width,
            toJSON: () => ({}),
          },
        } as ResizeObserverEntry,
      ], observer as unknown as ResizeObserver);
    };

    class ResizeObserverMock {
      readonly callback: ResizeObserverCallback;
      target: Element | null = null;

      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        observerInstances.push(this);
      }

      observe(target: Element) {
        this.target = target;
      }

      disconnect() {}
      unobserve() {}
      takeRecords() { return []; }
    }

    globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;

    try {
      const { container, rerender } = render(<App />);
      expect(observerInstances.length).toBeGreaterThanOrEqual(1);
      const initialObserverCount = observerInstances.length;

      rerender(<App chatSurfaceMode="compact" compactChatState="input" />);
      rerender(<App chatSurfaceMode="full" />);
      expect(observerInstances.length).toBeGreaterThan(initialObserverCount);

      await act(async () => {
        emitResize(observerInstances[observerInstances.length - 1], 420);
        vi.advanceTimersByTime(300);
      });

      expect(container.querySelector('.composer-overflow-btn')).toBeNull();
      expect(container.querySelector('.composer-galgame-btn')).not.toBeNull();
    } finally {
      globalThis.ResizeObserver = originalResizeObserver;
      vi.useRealTimers();
    }
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

    const { container } = render(<App messages={[firstMessage, secondMessage]} />);

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

    render(<App messages={[streamingMessage, failedMessage]} />);

    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('submits composer text through the new submit callback', () => {
    const onComposerSubmit = vi.fn();
    render(<App onComposerSubmit={onComposerSubmit} />);

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'Test send' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'Test send' });
  });

  it('disables composer submission while the home tutorial owns interaction', () => {
    const onComposerSubmit = vi.fn();
    render(<App composerDisabled onComposerSubmit={onComposerSubmit} />);

    const input = screen.getByPlaceholderText('Type a message...');
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { value: 'Blocked send' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('does not render a local optimistic user bubble before the host echoes messages', () => {
    const onComposerSubmit = vi.fn();
    render(<App onComposerSubmit={onComposerSubmit} />);

    const input = screen.getByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'No local optimistic bubble' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: 'No local optimistic bubble' });
    expect(screen.queryByText('No local optimistic bubble')).not.toBeInTheDocument();
    expect(screen.queryByText('You')).not.toBeInTheDocument();
  });

  it('renders composer tool buttons and calls the React callbacks', () => {
    const onComposerImportImage = vi.fn();
    const onComposerScreenshot = vi.fn();

    render(
      <App
        onComposerImportImage={onComposerImportImage}
        onComposerScreenshot={onComposerScreenshot}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Import Image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Screenshot' }));

    expect(onComposerImportImage).toHaveBeenCalledTimes(1);
    expect(onComposerScreenshot).toHaveBeenCalledTimes(1);
  });

  it('renders pending composer attachments and removes them through callback', () => {
    const onComposerRemoveAttachment = vi.fn();

    render(
      <App
        composerAttachments={[
          { id: 'img-1', url: 'data:image/png;base64,aaa', alt: 'Screenshot 1' },
        ]}
        onComposerRemoveAttachment={onComposerRemoveAttachment}
      />,
    );

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

  it('only emits avatar interactions when the pointer hits the avatar range', () => {
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
      render(<App onAvatarInteraction={onAvatarInteraction} />);

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

  it('derives different touch zones for different avatar hit areas', () => {
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
      render(<App onAvatarInteraction={onAvatarInteraction} />);

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

  it('escalates lollipop interactions from normal to burst on repeated in-range taps', () => {
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
      render(<App onAvatarInteraction={onAvatarInteraction} />);

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
      const { container } = render(<App />);

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

  it('escalates fist interactions to rapid on repeated in-range taps', () => {
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
      render(<App onAvatarInteraction={onAvatarInteraction} />);

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

  it('does not emit avatar interactions when compact UI overlaps the avatar hit range', () => {
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
      render(<App onAvatarInteraction={onAvatarInteraction} />);

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

  it('selects an avatar tool from the group and clears it from the active badge', () => {
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));

    expect(screen.getByRole('group', { name: 'Tool icons' })).toBeInTheDocument();

    const lollipopButton = screen.getByRole('button', { name: '棒棒糖' });
    expect(lollipopButton).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(lollipopButton);

    const activeBadgeButton = screen.getByRole('button', { name: 'Emoji: 棒棒糖' });
    expect(activeBadgeButton).toHaveClass('is-active');
    expect(screen.queryByRole('group', { name: 'Tool icons' })).not.toBeInTheDocument();

    fireEvent.click(activeBadgeButton);

    expect(screen.getByRole('button', { name: 'Emoji' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Tool icons' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Emoji: 棒棒糖' })).not.toBeInTheDocument();
  });

  it('clears the selected avatar tool from the icon badge', () => {
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(screen.getByRole('button', { name: 'Emoji: 猫爪' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '恢复鼠标' }));

    expect(screen.getByRole('button', { name: 'Emoji' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Emoji: 猫爪' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复鼠标' })).not.toBeInTheDocument();
  });

  it('emits avatar tool state changes for desktop hosts', () => {
    const onAvatarToolStateChange = vi.fn();
    render(<App onAvatarToolStateChange={onAvatarToolStateChange} />);

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      tool: null,
    }));

    onAvatarToolStateChange.mockClear();
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

  it('anchors the desktop cursor overlay to the current pointer when a tool is activated', () => {
    const { container } = render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }), {
      clientX: 240,
      clientY: 320,
    });

    const overlay = container.querySelector('.avatar-cursor-overlay');
    expect(overlay).not.toBeNull();
    expect((overlay as HTMLDivElement).style.transform).toBe('translate3d(201px, 274px, 0)');
  });

  it('clears the tool cursor when the composer is hidden for voice mode', () => {
    const { container, rerender } = render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App composerHidden />);

    expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('clears the tool cursor when the host issues a reset key', () => {
    const { container, rerender } = render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));

    expect(container.querySelector('.avatar-cursor-overlay')).not.toBeNull();
    expect(document.documentElement).toHaveClass('neko-tool-cursor-active');

    rerender(<App _toolCursorResetKey="voice-mode-reset-1" />);

    expect(container.querySelector('.avatar-cursor-overlay')).toBeNull();
    expect(document.documentElement).not.toHaveClass('neko-tool-cursor-active');
    expect(document.documentElement.style.getPropertyValue('--neko-chat-tool-cursor')).toBe('');
    expect(document.documentElement.style.getPropertyValue('cursor')).toBe('auto');
  });

  it('preserves the outside-window cursor state when the host resets a tool cursor', () => {
    const onAvatarToolStateChange = vi.fn();
    const { rerender } = render(<App onAvatarToolStateChange={onAvatarToolStateChange} />);

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
    rerender(<App onAvatarToolStateChange={onAvatarToolStateChange} _toolCursorResetKey="voice-mode-reset-2" />);

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      insideHostWindow: false,
    }));
  });

  it('marks the cursor back inside the host when clearing a tool from the composer', () => {
    const onAvatarToolStateChange = vi.fn();
    render(<App onAvatarToolStateChange={onAvatarToolStateChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));
    fireEvent.click(screen.getByRole('button', { name: '猫爪' }));
    fireEvent.blur(window);

    onAvatarToolStateChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '恢复鼠标' }));

    expect(onAvatarToolStateChange).toHaveBeenCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
      insideHostWindow: true,
    }));
  });

  it('restores the native cursor while desktop system UI owns focus', () => {
    const { container } = render(<App />);

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

  it('uses the native cursor and clears it when leaving the Electron chat window', () => {
    (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ = true;

    try {
      const { container } = render(<App />);

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

  it('shows the hammer secondary cursor asset on outside-range desktop clicks', () => {
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
      const { container } = render(<App />);

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
