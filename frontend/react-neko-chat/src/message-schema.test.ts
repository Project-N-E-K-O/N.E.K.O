import { ZodError } from 'zod';
import { parseChatMessage, parseChatWindowProps } from './message-schema';

describe('message-schema', () => {
  it('parses a valid chat message', () => {
    const message = parseChatMessage({
      id: 'msg-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: 'hello' }],
    });

    expect(message.role).toBe('assistant');
    expect(message.blocks[0]?.type).toBe('text');
  });

  it('rejects invalid message payloads', () => {
    expect(() => parseChatMessage({
      id: 'msg-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'unknown', text: 'bad block' }],
    })).toThrow(ZodError);
  });

  it('normalizes empty props through the window props schema', () => {
    const props = parseChatWindowProps(undefined);

    expect(props).toEqual({});
  });

  it('accepts chat surface mode props', () => {
    const props = parseChatWindowProps({
      chatSurfaceMode: 'compact',
      compactChatState: 'input',
    });

    expect(props.chatSurfaceMode).toBe('compact');
    expect(props.compactChatState).toBe('input');
  });

  it('migrates the legacy "full" surface mode to compact instead of throwing', () => {
    const props = parseChatWindowProps({
      // Cast: 'full' is no longer part of the public type but mixed-version
      // hosts can still send it; the schema must migrate rather than reject.
      chatSurfaceMode: 'full' as unknown as 'compact',
    });

    expect(props.chatSurfaceMode).toBe('compact');
  });

  it('accepts an avatar interaction callback in window props', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });

    expect(typeof props.onAvatarInteraction).toBe('function');
    props.onAvatarInteraction?.({
      interactionId: 'avatar-int-1',
      toolId: 'fist',
      actionId: 'poke',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      touchZone: 'head',
      timestamp: Date.now(),
    });
    expect(onAvatarInteraction).toHaveBeenCalledTimes(1);
  });

  it('accepts compact history drag state callbacks in window props', () => {
    const onCompactHistoryDragStateChange = vi.fn();
    const props = parseChatWindowProps({ onCompactHistoryDragStateChange });

    expect(typeof props.onCompactHistoryDragStateChange).toBe('function');
    props.onCompactHistoryDragStateChange?.({
      active: true,
      sessionId: 'compact-history-drag-1',
      seq: 1,
      phase: 'dragging',
      dragType: 'image',
      messageId: 'msg-1',
      pointerClient: { clientX: 10, clientY: 20 },
      sourceFrameRect: { left: 0, top: 0, right: 100, bottom: 50, width: 100, height: 50 },
      dragVisualRect: { left: 10, top: 20, right: 90, bottom: 60, width: 80, height: 40 },
      connectionVisualRect: { left: 0, top: 0, right: 90, bottom: 60, width: 90, height: 60 },
      dragHitRect: { left: 8, top: 18, right: 92, bottom: 62, width: 84, height: 44 },
      overTarget: false,
      needsDesktopBounds: true,
      timestamp: Date.now(),
    });

    expect(onCompactHistoryDragStateChange).toHaveBeenCalledTimes(1);
  });

  it('rejects avatar interaction payloads with a non-avatar target', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-2',
      toolId: 'hammer',
      actionId: 'bonk',
      target: 'outside',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });

  it('rejects avatar interaction payloads with an invalid tool/action pairing', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-3',
      toolId: 'lollipop',
      actionId: 'bonk',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });

  it('rejects avatar interaction payloads with an invalid touch zone', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-4',
      toolId: 'fist',
      actionId: 'poke',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      touchZone: 'tail',
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });

  it('rejects lollipop payloads with fist-only rewardDrop', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-5',
      toolId: 'lollipop',
      actionId: 'offer',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      rewardDrop: true,
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });

  it('rejects lollipop payloads with touchZone', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-5b',
      toolId: 'lollipop',
      actionId: 'offer',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      touchZone: 'face',
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });

  it('rejects fist payloads with hammer-only easterEgg', () => {
    const onAvatarInteraction = vi.fn();
    const props = parseChatWindowProps({ onAvatarInteraction });
    const invalidPayload = {
      interactionId: 'avatar-int-6',
      toolId: 'fist',
      actionId: 'poke',
      target: 'avatar',
      pointer: {
        clientX: 10,
        clientY: 20,
      },
      easterEgg: true,
      timestamp: Date.now(),
    } as unknown;

    expect(() => props.onAvatarInteraction?.(invalidPayload as never)).toThrow(ZodError);
  });
});
