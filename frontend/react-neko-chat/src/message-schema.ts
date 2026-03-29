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

export const chatWindowPropsSchema = z.object({
  title: z.string().optional(),
  iconSrc: z.string().optional(),
  messages: z.array(chatMessageSchema).optional(),
  inputPlaceholder: z.string().optional(),
  sendButtonLabel: z.string().optional(),
  onMessageAction: z.function()
    .args(chatMessageSchema, messageActionSchema)
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
export type MessageBlock = z.infer<typeof messageBlockSchema>;
export type ChatMessage = z.infer<typeof chatMessageSchema>;
export type ChatWindowSchemaProps = z.infer<typeof chatWindowPropsSchema>;

export function parseChatMessage(input: unknown): ChatMessage {
  return chatMessageSchema.parse(input);
}

export function parseChatWindowProps<T extends Record<string, unknown> | undefined>(input: T) {
  return chatWindowPropsSchema.parse(input ?? {}) as ChatWindowSchemaProps;
}
