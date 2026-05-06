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

  it('preserves quick action props through the window props schema', async () => {
    const action = {
      action_id: 'plugin.demo.toggle',
      type: 'instant',
      label: 'Demo toggle',
      description: 'Toggle demo mode',
      category: 'settings',
      plugin_id: 'demo',
      control: 'toggle',
      current_value: false,
      quick_action: true,
    } as const;
    const onQuickActionExecute = vi.fn(async () => ({
      ...action,
      current_value: true,
    }));
    const onQuickActionsRequest = vi.fn();
    const onQuickActionsPreferencesChange = vi.fn();
    const props = parseChatWindowProps({
      quickActions: [action],
      quickActionsPreferences: {
        pinned: [action.action_id],
        hidden: [],
        recent: [],
      },
      quickActionsLoading: true,
      onQuickActionExecute,
      onQuickActionsRequest,
      onQuickActionsPreferencesChange,
    });

    expect(props.quickActions?.[0]?.action_id).toBe(action.action_id);
    expect(props.quickActionsPreferences?.pinned).toEqual([action.action_id]);
    expect(props.quickActionsLoading).toBe(true);
    props.onQuickActionsRequest?.();
    props.onQuickActionsPreferencesChange?.({ pinned: [], hidden: [action.action_id], recent: [] });
    await expect(props.onQuickActionExecute?.(action.action_id, true)).resolves.toMatchObject({
      action_id: action.action_id,
      current_value: true,
    });
    expect(onQuickActionsRequest).toHaveBeenCalledTimes(1);
    expect(onQuickActionsPreferencesChange).toHaveBeenCalledWith({
      pinned: [],
      hidden: [action.action_id],
      recent: [],
    });
  });

  it('accepts async quick action request and preference callbacks', async () => {
    const onQuickActionsRequest = vi.fn(async () => {});
    const onQuickActionsPreferencesChange = vi.fn(async () => {});
    const props = parseChatWindowProps({
      onQuickActionsRequest,
      onQuickActionsPreferencesChange,
    });

    await expect(props.onQuickActionsRequest?.()).resolves.toBeUndefined();
    await expect(props.onQuickActionsPreferencesChange?.({ pinned: [], hidden: [], recent: [] }))
      .resolves.toBeUndefined();
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
