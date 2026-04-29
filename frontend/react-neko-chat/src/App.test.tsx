import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import { parseChatMessage } from './message-schema';

describe('App', () => {
  it('renders the empty state when there are no messages', () => {
    render(<App />);

    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();
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

  it('exposes avatar tools as a toggle group with pressed state', () => {
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Emoji' }));

    expect(screen.getByRole('group', { name: 'Tool icons' })).toBeInTheDocument();

    const lollipopButton = screen.getByRole('button', { name: '棒棒糖' });
    expect(lollipopButton).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(lollipopButton);
    fireEvent.click(screen.getByRole('button', { name: 'Emoji: 棒棒糖' }));

    expect(screen.getByRole('button', { name: '棒棒糖' })).toHaveAttribute('aria-pressed', 'true');
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
