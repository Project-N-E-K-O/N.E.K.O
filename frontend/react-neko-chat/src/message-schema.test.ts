import { ZodError, z } from 'zod';
import { chatWindowPropsSchema, parseChatMessage, parseChatWindowProps } from './message-schema';

// zod 4 的函数包装器（z.function().implement）在调用时用核心层 parse，抛的是
// z.core.$ZodError（classic ZodError 的父类），不是 ZodError 本身。
const ZodCallbackError = z.core.$ZodError;

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

  it('preserves the dedicated theater submit callback', () => {
    const onTheaterSubmit = vi.fn();
    const props = parseChatWindowProps({ onTheaterSubmit });

    props.onTheaterSubmit?.('继续演绎');
    expect(onTheaterSubmit).toHaveBeenCalledWith('继续演绎');
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
    } as never)).toThrow(ZodCallbackError);
    expect(onAvatarInteraction).toHaveBeenCalledTimes(1);
  });

  it('lets the screenshot host callback report handled back through the validated wrapper', () => {
    // 宿主 handleComposerScreenshot 会 return handled（布尔）。生产路径上这个回调一定
    // 经过 parseChatWindowProps 的 zod 校验壳（mount.tsx），壳会校验返回值——一旦把
    // 返回类型声明成 void，点一次截图就抛一个未捕获的返回值类型错误（zod 4 里 issue code 是 invalid_type）。
    // 注意这里必须用真的返回布尔的函数：vi.fn() 默认返回 undefined，正好躲开这条校验。
    const onComposerScreenshot = vi.fn(function handleComposerScreenshot() {
      return true;
    });
    const props = parseChatWindowProps({ onComposerScreenshot });

    expect(props.onComposerScreenshot?.()).toBe(true);
    expect(onComposerScreenshot).toHaveBeenCalledTimes(1);
  });

  it('keeps the void return contract for host callbacks that report nothing', () => {
    // 与上一条对偶：放宽只发生在截图这一项，其余回调仍然钉死"不许有返回值"。
    const onComposerImportImage = vi.fn(function handleComposerImportImage() {
      return true;
    });
    const props = parseChatWindowProps({ onComposerImportImage });

    expect(() => props.onComposerImportImage?.()).toThrow(ZodCallbackError);
  });

  it('declares every host callback with an unknown rest parameter', () => {
    // 结构性守卫：新增回调若绕过 hostCallback 直接写 z.function().input([...])，
    // 就会丢掉 rest 参数，宿主多传一个参数就会抛错。
    const callbacks = Object.entries(chatWindowPropsSchema.shape).flatMap(([key, schema]) => {
      let inner: unknown = schema;
      // 拆掉 optional / nullable / default 等包装，避免套了多层的回调被漏检。
      while (inner instanceof z.ZodOptional || inner instanceof z.ZodNullable || inner instanceof z.ZodDefault) {
        inner = inner.unwrap();
      }
      return inner instanceof z.ZodFunction ? [[key, inner] as const] : [];
    });
    // 精确数量：新增 / 删除回调时同步更新，防止覆盖面静默缩水。
    expect(callbacks.map(([key]) => key)).toHaveLength(20);
    for (const [key, fn] of callbacks) {
      const input = fn._zod.def.input as z.core.$ZodTuple;
      expect(input._zod.def.rest, key).toBeInstanceOf(z.ZodUnknown);
    }
  });

  it('tolerates extra trailing arguments on validated host callbacks like zod 3 did', () => {
    // zod 3 的 .args(...) 自带 rest(z.unknown())，多传的参数不会被拒；zod 4 的 .input([...])
    // 默认不带 rest，所以 schema 里显式补了 z.unknown() rest。这里钉住这个兼容行为。
    const onJukeboxClick = vi.fn();
    const onTheaterSubmit = vi.fn();
    const props = parseChatWindowProps({ onJukeboxClick, onTheaterSubmit });

    expect(() => (props.onJukeboxClick as (...args: unknown[]) => void)({ type: 'click' })).not.toThrow();
    expect(() => (props.onTheaterSubmit as (...args: unknown[]) => void)('hi', { extra: true })).not.toThrow();
    expect(onTheaterSubmit).toHaveBeenCalledWith('hi', { extra: true });
    expect(() => (props.onTheaterSubmit as (...args: unknown[]) => void)(42)).toThrow(ZodCallbackError);
  });

  it('keeps validated host callback identities stable across repeated prop parsing', () => {
    const onAvatarToolStateChange = vi.fn();
    const firstProps = parseChatWindowProps({ onAvatarToolStateChange });
    const secondProps = parseChatWindowProps({ onAvatarToolStateChange });

    expect(firstProps.onAvatarToolStateChange).toBe(secondProps.onAvatarToolStateChange);
    expect(firstProps.onAvatarToolStateChange).not.toBe(onAvatarToolStateChange);
    expect(() => secondProps.onAvatarToolStateChange?.({ active: 'yes' } as never)).toThrow(ZodCallbackError);
  });

  it('preserves an optional character reaction without changing legacy messages', () => {
    const base = {
      id: 'reaction-user', role: 'user', author: 'You', time: '10:00',
      blocks: [{ type: 'text', text: 'Hello' }],
    };
    expect(parseChatMessage(base).reaction).toBeUndefined();
    for (const emoji of ['😊', '😄', '😃', '🙂', '😌', '🤔', '🧐', '💭', '❓', '👍', '✅', '🙌', '💪', '🎉', '🙏', '🤝', '😮', '👀', '⚠️', '💡', '😔', '😢', '😅', '🙇', '🥳', '✨', '🌟', '💻', '🤖', '📚', '🔧', '❤️', '⭐', '🔥', '🚀', '📌', '😂', '🤗']) {
      expect(parseChatMessage({ ...base, reaction: { emoji, author: ' Neko ' } }).reaction)
        .toEqual({ emoji, author: 'Neko' });
    }
    expect(parseChatWindowProps({ messages: [{ ...base, reaction: { emoji: '❤️', author: 'Neko' } }] })
      .messages?.[0]?.reaction).toEqual({ emoji: '❤️', author: 'Neko' });
  });

  it.each([
    { emoji: 'invalid', author: 'Neko' },
    { emoji: '❤️', author: '' },
    { emoji: '❤️', author: '   ' },
    { emoji: '❤️' },
    null,
  ])('rejects malformed reactions at the schema boundary: %j', (reaction) => {
    expect(() => parseChatMessage({
      id: 'reaction-invalid', role: 'user', author: 'You', time: '10:00',
      blocks: [{ type: 'text', text: 'Hello' }], reaction,
    })).toThrow(ZodError);
  });

});
