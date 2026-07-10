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

  it('normalizes empty turn ids while preserving non-empty turn ids', () => {
    const baseMessage = {
      id: 'msg-turn',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: 'hello' }],
    };

    expect(parseChatMessage({
      ...baseMessage,
      turnId: null,
    }).turnId).toBeUndefined();
    expect(parseChatMessage({
      ...baseMessage,
      turnId: '',
    }).turnId).toBeUndefined();
    expect(parseChatMessage({
      ...baseMessage,
      turnId: 'turn-1',
    }).turnId).toBe('turn-1');
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

  it('accepts new user icebreaker choice prompts', () => {
    const onChoiceSelect = vi.fn();
    const props = parseChatWindowProps({
      choicePrompt: {
        source: 'new_user_icebreaker',
        sessionId: 'icebreaker-day1-session',
        options: [
          { choice: 'A', label: '看得差不多了' },
          { choice: 'B', label: '还有点晕乎乎' },
        ],
      },
      onChoiceSelect,
    });

    expect(props.choicePrompt?.source).toBe('new_user_icebreaker');
    props.onChoiceSelect?.(props.choicePrompt!.options[0]!, 'new_user_icebreaker');
    expect(onChoiceSelect).toHaveBeenCalledTimes(1);
    expect(onChoiceSelect).toHaveBeenCalledWith(props.choicePrompt!.options[0]!, 'new_user_icebreaker');
  });

  it('accepts chat surface mode props', () => {
    const props = parseChatWindowProps({
      chatSurfaceMode: 'compact',
      compactChatState: 'input',
    });

    expect(props.chatSurfaceMode).toBe('compact');
    expect(props.compactChatState).toBe('input');
  });

  it('accepts compact history open requests', () => {
    const props = parseChatWindowProps({
      compactHistoryOpenRequest: {
        id: 'compact-history-open-guide',
        open: true,
        reason: 'avatar-floating-guide-history',
      },
    });

    expect(props.compactHistoryOpenRequest).toEqual({
      id: 'compact-history-open-guide',
      open: true,
      reason: 'avatar-floating-guide-history',
    });
  });

  it('accepts the revived "full" surface mode', () => {
    // `full` is the frozen legacy surface revived alongside compact/minimized.
    // The schema accepts all three; the host dispatcher routes `full` to the
    // isolated FullChatSurface.
    const props = parseChatWindowProps({
      chatSurfaceMode: 'full',
    });

    expect(props.chatSurfaceMode).toBe('full');
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

  it('keeps validated host callback identities stable across repeated prop parsing', () => {
    const onAvatarToolStateChange = vi.fn();
    const firstProps = parseChatWindowProps({ onAvatarToolStateChange });
    const secondProps = parseChatWindowProps({ onAvatarToolStateChange });

    expect(firstProps.onAvatarToolStateChange).toBe(secondProps.onAvatarToolStateChange);
    expect(firstProps.onAvatarToolStateChange).not.toBe(onAvatarToolStateChange);
    expect(() => secondProps.onAvatarToolStateChange?.({ active: 'yes' } as never)).toThrow(ZodError);
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
