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
      const choiceLayer = container.querySelector('.compact-chat-choice-anchor');
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
      const choiceLayer = container.querySelector('.compact-chat-choice-anchor');
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
      const choiceLayer = container.querySelector('.compact-chat-choice-anchor');
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

  it('renders compact input as the same surface with an inline send button only', () => {
    const { container } = render(<App chatSurfaceMode="compact" compactChatState="input" />);

    expect(container.querySelector('.compact-chat-inline-input')).not.toBeNull();
    expect(container.querySelector('.compact-chat-capsule-button')).toBeNull();
    expect(container.querySelector('.composer-bottom-bar')).toBeNull();
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
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
