import { z } from 'zod';

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

const buttonGroupBlockSchema = z.object({
  type: z.literal('buttons'),
  buttons: z.array(messageActionSchema),
});

const composerAttachmentSchema = z.object({
  id: z.string().min(1),
  url: z.string().min(1),
  alt: z.string().optional(),
});

const avatarInteractionPayloadBaseSchema = z.object({
  interactionId: z.string().min(1),
  target: z.literal('avatar'),
  pointer: z.object({
    clientX: z.number().finite(),
    clientY: z.number().finite(),
  }),
  textContext: z.string().optional(),
  timestamp: z.number().finite(),
  intensity: z.enum(['normal', 'rapid', 'burst', 'easter_egg']).optional(),
});

export const avatarInteractionPayloadSchema = z.discriminatedUnion('toolId', [
  avatarInteractionPayloadBaseSchema.extend({
    toolId: z.literal('lollipop'),
    actionId: z.enum(['offer', 'tease', 'tap_soft']),
  }).strict(),
  avatarInteractionPayloadBaseSchema.extend({
    toolId: z.literal('fist'),
    actionId: z.enum(['poke']),
    touchZone: z.enum(['ear', 'head', 'face', 'body']).optional(),
    rewardDrop: z.boolean().optional(),
  }).strict(),
  avatarInteractionPayloadBaseSchema.extend({
    toolId: z.literal('hammer'),
    actionId: z.enum(['bonk']),
    touchZone: z.enum(['ear', 'head', 'face', 'body']).optional(),
    easterEgg: z.boolean().optional(),
  }).strict(),
]);

const avatarToolIdSchema = z.enum(['lollipop', 'fist', 'hammer']);
const avatarToolCursorVariantSchema = z.enum(['primary', 'secondary', 'tertiary']);
const avatarToolImageKindSchema = z.enum(['cursor', 'icon']);

const avatarToolDescriptorSchema = z.object({
  id: avatarToolIdSchema,
  label: z.string().optional(),
  iconImagePath: z.string().min(1),
  iconImagePathAlt: z.string().optional(),
  iconImagePathAlt2: z.string().optional(),
  cursorImagePath: z.string().min(1),
  cursorImagePathAlt: z.string().optional(),
  cursorImagePathAlt2: z.string().optional(),
  cursorHotspotX: z.number().finite().optional(),
  cursorHotspotY: z.number().finite().optional(),
  menuIconScale: z.number().finite().positive().optional(),
}).strict();

export const avatarToolStatePayloadSchema = z.object({
  active: z.boolean(),
  toolId: avatarToolIdSchema.nullable().optional(),
  variant: avatarToolCursorVariantSchema.optional(),
  avatarRangeVariant: avatarToolCursorVariantSchema.optional(),
  outsideRangeVariant: avatarToolCursorVariantSchema.optional(),
  imageKind: avatarToolImageKindSchema.optional(),
  withinAvatarRange: z.boolean().optional(),
  overCompactZone: z.boolean().optional(),
  insideHostWindow: z.boolean().optional(),
  tool: avatarToolDescriptorSchema.nullable().optional(),
  textContext: z.string().optional(),
  timestamp: z.number().finite(),
}).strict();

export const messageBlockSchema = z.discriminatedUnion('type', [
  textBlockSchema,
  imageBlockSchema,
  linkBlockSchema,
  statusBlockSchema,
  buttonGroupBlockSchema,
]);

export const chatMessageSchema = z.object({
  id: z.string().min(1),
  role: z.enum(['user', 'assistant', 'system', 'tool']),
  author: z.string().min(1),
  time: z.string(),
  createdAt: z.number().finite().optional(),
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
  _toolCursorResetKey: z.string().optional(),
  jukeboxButtonLabel: z.string().optional(),
  jukeboxButtonAriaLabel: z.string().optional(),
  avatarGeneratorButtonLabel: z.string().optional(),
  avatarGeneratorButtonAriaLabel: z.string().optional(),
  composerHidden: z.boolean().optional(),
  translateEnabled: z.boolean().optional(),
  translateButtonLabel: z.string().optional(),
  translateButtonAriaLabel: z.string().optional(),
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
  onTranslateToggle: z.function()
    .args()
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
export type ComposerAttachment = z.infer<typeof composerAttachmentSchema>;
export type AvatarInteractionPayload = z.infer<typeof avatarInteractionPayloadSchema>;
export type AvatarToolStatePayload = z.infer<typeof avatarToolStatePayloadSchema>;
export type MessageBlock = z.infer<typeof messageBlockSchema>;
export type ChatMessage = z.infer<typeof chatMessageSchema>;
export type ComposerSubmitPayload = z.infer<typeof composerSubmitSchema>;
export type ChatWindowSchemaProps = z.infer<typeof chatWindowPropsSchema>;

export function parseChatMessage(input: unknown): ChatMessage {
  return chatMessageSchema.parse(input);
}

export function parseChatWindowProps<T extends Record<string, unknown> | undefined>(input: T) {
  return chatWindowPropsSchema.parse(input ?? {}) as ChatWindowSchemaProps;
}
