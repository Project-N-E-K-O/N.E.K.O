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
