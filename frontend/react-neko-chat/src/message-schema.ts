import { z } from 'zod';
import {
  avatarInteractionPayloadSchema,
  avatarToolStatePayloadSchema,
  type AvatarInteractionPayload,
  type AvatarToolStatePayload,
} from './avatar-tools/protocol';

export {
  avatarInteractionPayloadSchema,
  avatarToolStatePayloadSchema,
};
export type {
  AvatarInteractionPayload,
  AvatarToolStatePayload,
};

const messageActionSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  action: z.string().min(1),
  variant: z.enum(['primary', 'secondary', 'danger']).optional(),
  disabled: z.boolean().optional(),
  payload: z.record(z.unknown()).optional(),
});

const textBlockSchema = z.object({
  type: z.literal('text'),
  text: z.string(),
});

const imageBlockSchema = z.object({
  type: z.literal('image'),
  url: z.string().min(1),
  alt: z.string().optional(),
  width: z.number().finite().positive().optional(),
  height: z.number().finite().positive().optional(),
});

const linkBlockSchema = z.object({
  type: z.literal('link'),
  url: z.string().min(1),
  title: z.string().optional(),
  description: z.string().optional(),
  siteName: z.string().optional(),
  thumbnailUrl: z.string().optional(),
});

const statusBlockSchema = z.object({
  type: z.literal('status'),
  tone: z.enum(['info', 'success', 'warning', 'error']).optional(),
  text: z.string(),
});

// Frontend-only "she has a topic she'd like to bring up" teaser, shown just
// before a proactive deep-topic opener. Backend sends only the character name
// (no LLM-context text); the dedicated TopicHintBubble renders localized copy.
const topicHintBlockSchema = z.object({
  type: z.literal('topic-hint'),
  // Trim before length check so a whitespace-only author is rejected, matching
  // the trim the bubble does at render time.
  author: z.string().trim().min(1),
});

const buttonGroupBlockSchema = z.object({
  type: z.literal('buttons'),
  buttons: z.array(messageActionSchema),
});

const composerAttachmentSchema = z.object({
  id: z.string().min(1),
  url: z.string().min(1),
  alt: z.string().optional(),
});

// `full` is the frozen legacy surface (full chat window) revived alongside the
// active `compact` floating bar and `minimized` ball. The host dispatcher routes
// `full` to the isolated FullChatSurface; `compact`/`minimized` stay on the
// active App. Keep all three valid at the parse boundary.
const chatSurfaceModeSchema = z.enum(['full', 'compact', 'minimized']);
const compactChatStateSchema = z.enum(['default', 'options', 'input']);

const galgameOptionSchema = z.object({
  label: z.string().min(1),
  text: z.string().min(1),
});

// Generic ChoicePrompt — composer-anchored "AI 给你出几个选项" UI 组件抽象。
//
// 当前 source：
//   - 'galgame'           ：旧路径（galgameOptions / onGalgameOptionSelect 依然
//                           保留 BC，本框架不替换它，作为渐进迁移目标）
//   - 'mini_game_invite'  ：mini-game 邀请三选项（accept / decline / later）
//   - 'new_user_icebreaker'：七日教程结束后的预置破冰二选项
//
// 未来扩展：
//   - 'tutorial_step' / 'plugin_action' / ...
//   - 当需要"对话框 + avatar 旁边同步显示"时，加 placement: 'composer' | 'avatar'
//     | 'both'，不破坏 wire-format。
//
// option.choice 是后端 wire-format 标识符（accept/decline/later 之类），点击
// 时回传给 onChoiceSelect；UI 显示用 option.label。
const choiceOptionSchema = z.object({
  choice: z.string().min(1),  // wire id (accept/decline/later/...)
  label: z.string().min(1),   // 显示文本
});

const choicePromptSourceSchema = z.enum(['galgame', 'mini_game_invite', 'new_user_icebreaker']);

const choicePromptSchema = z.object({
  source: choicePromptSourceSchema,
  options: z.array(choiceOptionSchema).min(1),
  sessionId: z.string().optional(),
  gameType: z.string().optional(),
}).nullable();

const avatarToolMenuOpenRequestSchema = z.object({
  id: z.string().min(1),
  open: z.boolean(),
  reason: z.string().optional(),
}).nullable();

const compactToolFanOpenRequestSchema = z.object({
  id: z.string().min(1),
  open: z.boolean(),
  reason: z.string().optional(),
}).nullable();

const compactHistoryOpenRequestSchema = z.object({
  id: z.string().min(1),
  open: z.boolean(),
  reason: z.string().optional(),
}).nullable();

export const COMPACT_TOOL_WHEEL_POSITIONS = 7;

const compactToolWheelRotateRequestSchema = z.object({
  id: z.string().min(1),
  // 1 rotates clockwise, -1 rotates counter-clockwise.
  direction: z.union([z.literal(1), z.literal(-1)]),
  stepCount: z.number().int().positive().max(COMPACT_TOOL_WHEEL_POSITIONS),
  reason: z.string().optional(),
  forceFast: z.boolean().optional(),
}).nullable();

const compactToolWheelIndexRequestSchema = z.object({
  id: z.string().min(1),
  index: z.number().int().min(0).max(COMPACT_TOOL_WHEEL_POSITIONS - 1),
  reason: z.string().optional(),
}).nullable();

export const messageBlockSchema = z.discriminatedUnion('type', [
  textBlockSchema,
  imageBlockSchema,
  linkBlockSchema,
  statusBlockSchema,
  buttonGroupBlockSchema,
  topicHintBlockSchema,
]);

const turnIdSchema = z.preprocess((value) => {
  if (value === null || value === undefined || value === '') {
    return undefined;
  }
  return value;
}, z.string().min(1).optional());

export const chatMessageSchema = z.object({
  id: z.string().min(1),
  role: z.enum(['user', 'assistant', 'system', 'tool']),
  author: z.string().min(1),
  time: z.string(),
  createdAt: z.number().finite().optional(),
  turnId: turnIdSchema,
  avatarLabel: z.string().optional(),
  avatarUrl: z.string().optional(),
  blocks: z.array(messageBlockSchema),
  actions: z.array(messageActionSchema).optional(),
  status: z.enum(['sending', 'sent', 'failed', 'streaming']).optional(),
  sortKey: z.number().finite().optional(),
});

export const composerSubmitSchema = z.object({
  text: z.string(),
  requestId: z.string().optional(),
});

export const chatWindowPropsSchema = z.object({
  title: z.string().optional(),
  iconSrc: z.string().optional(),
  messages: z.array(chatMessageSchema).optional(),
  assistantName: z.string().trim().min(1).optional(),
  inputPlaceholder: z.string().optional(),
  sendButtonLabel: z.string().optional(),

  chatWindowAriaLabel: z.string().optional(),
  messageListAriaLabel: z.string().optional(),
  composerToolsAriaLabel: z.string().optional(),
  composerAttachments: z.array(composerAttachmentSchema).optional(),
  composerAttachmentsAriaLabel: z.string().optional(),
  importImageButtonLabel: z.string().optional(),
  screenshotButtonLabel: z.string().optional(),
  importImageButtonAriaLabel: z.string().optional(),
  screenshotButtonAriaLabel: z.string().optional(),
  removeAttachmentButtonAriaLabel: z.string().optional(),
  failedStatusLabel: z.string().optional(),
  inputHint: z.string().optional(),
  rollbackDraft: z.string().optional(),
  _rollbackKey: z.string().optional(),
  _avatarToolDeactivationKey: z.string().optional(),
  jukeboxButtonLabel: z.string().optional(),
  jukeboxButtonAriaLabel: z.string().optional(),
  avatarGeneratorButtonLabel: z.string().optional(),
  avatarGeneratorButtonAriaLabel: z.string().optional(),
  exportConversationButtonLabel: z.string().optional(),
  exportConversationButtonAriaLabel: z.string().optional(),
  composerHidden: z.boolean().optional(),
  composerDisabled: z.boolean().optional(),
  compactInputLocked: z.boolean().optional(),
  catLocalTextOnly: z.boolean().optional(),
  chatSurfaceMode: chatSurfaceModeSchema.optional(),
  // host 折叠取消序号：必须在 schema 里声明，否则 z.object().parse() 默认 strip 未知键、
  // App 永远只看到默认 0，重开立即复位的 useLayoutEffect 不会触发（Codex P2）。
  // 逻辑上是单调递增的非负整数计数（host 从 0 起 += 1），加 int/nonnegative 作边界防御
  // （CodeRabbit）；host 恒传合法值，约束不会触发拒绝。
  compactMinimizeCancelSeq: z.number().int().nonnegative().optional(),
  compactChatState: compactChatStateSchema.optional(),
  onCompactChatStateChange: z.function()
    .args(compactChatStateSchema)
    .returns(z.void())
    .optional(),
  onCompactMinimizeRequest: z.function()
    .args()
    .returns(z.void())
    .optional(),
  translateEnabled: z.boolean().optional(),
  translateButtonLabel: z.string().optional(),
  translateButtonAriaLabel: z.string().optional(),
  galgameModeEnabled: z.boolean().optional(),
  galgameOptions: z.array(galgameOptionSchema).optional(),
  galgameOptionsLoading: z.boolean().optional(),
  galgameToggleButtonLabel: z.string().optional(),
  galgameToggleButtonAriaLabel: z.string().optional(),
  galgameLoadingLabel: z.string().optional(),
  avatarToolMenuOpenRequest: avatarToolMenuOpenRequestSchema.optional(),
  compactToolFanOpenRequest: compactToolFanOpenRequestSchema.optional(),
  compactHistoryOpenRequest: compactHistoryOpenRequestSchema.optional(),
  compactToolWheelRotateRequest: compactToolWheelRotateRequestSchema.optional(),
  compactToolWheelIndexRequest: compactToolWheelIndexRequestSchema.optional(),
  onMessageAction: z.function()
    .args(chatMessageSchema, messageActionSchema)
    .returns(z.void())
    .optional(),
  onComposerImportImage: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onComposerScreenshot: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onComposerRemoveAttachment: z.function()
    .args(z.string())
    .returns(z.void())
    .optional(),
  onComposerSubmit: z.function()
    .args(composerSubmitSchema)
    .returns(z.void())
    .optional(),
  onAvatarInteraction: z.function()
    .args(avatarInteractionPayloadSchema)
    .returns(z.void())
    .optional(),
  onAvatarToolStateChange: z.function()
    .args(avatarToolStatePayloadSchema)
    .returns(z.void())
    .optional(),
  onJukeboxClick: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onAvatarGeneratorClick: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onExportConversationClick: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onTranslateToggle: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onGalgameModeToggle: z.function()
    .args()
    .returns(z.void())
    .optional(),
  onGalgameOptionSelect: z.function()
    .args(galgameOptionSchema)
    .returns(z.void())
    .optional(),
  // Generic ChoicePrompt（mini-game invite 等通用三选项框架）
  choicePrompt: choicePromptSchema.optional(),
  onChoiceSelect: z.function()
    // source 必须是固定枚举，与 ChoicePrompt['source'] 对齐——CodeRabbit 指出
    // 任意 z.string() 会让 zod 验证变松。
    .args(choiceOptionSchema, choicePromptSourceSchema)
    .returns(z.void())
    .optional(),
});

export type ChatMessageRole = z.infer<typeof chatMessageSchema>['role'];
export type MessageAction = z.infer<typeof messageActionSchema>;
export type TextBlock = z.infer<typeof textBlockSchema>;
export type ImageBlock = z.infer<typeof imageBlockSchema>;
export type LinkBlock = z.infer<typeof linkBlockSchema>;
export type StatusBlock = z.infer<typeof statusBlockSchema>;
export type ButtonGroupBlock = z.infer<typeof buttonGroupBlockSchema>;
export type TopicHintBlock = z.infer<typeof topicHintBlockSchema>;
export type ComposerAttachment = z.infer<typeof composerAttachmentSchema>;
export type ChatSurfaceMode = z.infer<typeof chatSurfaceModeSchema>;
export type CompactChatState = z.infer<typeof compactChatStateSchema>;
export type GalgameOption = z.infer<typeof galgameOptionSchema>;
export type ChoiceOption = z.infer<typeof choiceOptionSchema>;
export type ChoicePrompt = NonNullable<z.infer<typeof choicePromptSchema>>;
export type ChoicePromptSource = ChoicePrompt['source'];
export type MessageBlock = z.infer<typeof messageBlockSchema>;
export type ChatMessage = z.infer<typeof chatMessageSchema>;
export type ComposerSubmitPayload = z.infer<typeof composerSubmitSchema>;
export type ChatWindowSchemaProps = z.infer<typeof chatWindowPropsSchema>;

export function parseChatMessage(input: unknown): ChatMessage {
  return chatMessageSchema.parse(input);
}
const parsedCallbackCaches = new Map<string, WeakMap<object, unknown>>();

function stabilizeParsedCallbackIdentities(
  input: Record<string, unknown> | undefined,
  parsed: ChatWindowSchemaProps,
): ChatWindowSchemaProps {
  if (!input) return parsed;

  const parsedRecord = parsed as Record<string, unknown>;
  Object.entries(input).forEach(([key, rawValue]) => {
    const parsedValue = parsedRecord[key];
    if (typeof rawValue !== 'function' || typeof parsedValue !== 'function') return;

    let callbackCache = parsedCallbackCaches.get(key);
    if (!callbackCache) {
      callbackCache = new WeakMap<object, unknown>();
      parsedCallbackCaches.set(key, callbackCache);
    }

    const cachedCallback = callbackCache.get(rawValue);
    if (typeof cachedCallback === 'function') {
      parsedRecord[key] = cachedCallback;
      return;
    }

    // z.function() returns a new validating wrapper on every parse. Reuse that
    // wrapper for the same host callback so ordinary message renders do not
    // look like callback changes to React effects.
    callbackCache.set(rawValue, parsedValue);
  });

  return parsed;
}

export function parseChatWindowProps<T extends Record<string, unknown> | undefined>(input: T) {
  const parsed = chatWindowPropsSchema.parse(input ?? {}) as ChatWindowSchemaProps;
  return stabilizeParsedCallbackIdentities(input, parsed);
}
