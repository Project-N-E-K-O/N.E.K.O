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

  it('accepts only real non-empty participant names for localized tool results', () => {
    const names = parseChatWindowProps({ userName: ' Ming ', assistantName: ' Yui ' });
    expect(names.userName).toBe('Ming');
    expect(names.assistantName).toBe('Yui');
    expect(() => parseChatWindowProps({ userName: '   ' })).toThrow();
    expect(() => parseChatWindowProps({ assistantName: '   ' })).toThrow();
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

  it('preserves the cat local text-only presentation flag', () => {
    expect(parseChatWindowProps({ catLocalTextOnly: true }).catLocalTextOnly).toBe(true);
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
      intensity: 'normal',
      touchZone: 'head',
      timestamp: Date.now(),
    });
    expect(onAvatarInteraction).toHaveBeenCalledTimes(1);

    expect(() => props.onAvatarInteraction?.({
      interactionId: 'avatar-int-invalid',
      toolId: 'fist',
      actionId: 'bonk',
      target: 'avatar',
      pointer: { clientX: 10, clientY: 20 },
      intensity: 'normal',
      touchZone: 'head',
      timestamp: Date.now(),
    } as never)).toThrow(ZodError);
    expect(onAvatarInteraction).toHaveBeenCalledTimes(1);
  });

  it('lets the screenshot host callback report handled back through the validated wrapper', () => {
    // 宿主 handleComposerScreenshot 会 return handled（布尔）。生产路径上这个回调一定
    // 经过 parseChatWindowProps 的 zod 校验壳（mount.tsx），壳会校验返回值——一旦把
    // 返回类型声明成 void，点一次截图就抛一个未捕获的 invalid_return_type。
    // 注意这里必须用真的返回布尔的函数：vi.fn() 默认返回 undefined，正好躲开这条校验。
    const onComposerScreenshot = vi.fn(function handleComposerScreenshot() {
      return true;
    });
    const props = parseChatWindowProps({ onComposerScreenshot });

    expect(() => props.onComposerScreenshot?.()).not.toThrow();
    expect(onComposerScreenshot).toHaveBeenCalledTimes(1);
  });

  it('keeps the void return contract for host callbacks that report nothing', () => {
    // 与上一条对偶：放宽只发生在截图这一项，其余回调仍然钉死"不许有返回值"。
    const onComposerImportImage = vi.fn(function handleComposerImportImage() {
      return true;
    });
    const props = parseChatWindowProps({ onComposerImportImage });

    expect(() => props.onComposerImportImage?.()).toThrow(ZodError);
  });

  it('keeps validated host callback identities stable across repeated prop parsing', () => {
    const onAvatarToolStateChange = vi.fn();
    const firstProps = parseChatWindowProps({ onAvatarToolStateChange });
    const secondProps = parseChatWindowProps({ onAvatarToolStateChange });

    expect(firstProps.onAvatarToolStateChange).toBe(secondProps.onAvatarToolStateChange);
    expect(firstProps.onAvatarToolStateChange).not.toBe(onAvatarToolStateChange);
    expect(() => secondProps.onAvatarToolStateChange?.({ active: 'yes' } as never)).toThrow(ZodError);
  });

});
