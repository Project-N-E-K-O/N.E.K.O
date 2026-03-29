export type ChatMessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type MessageAction = {
  id: string;
  label: string;
  action: string;
  variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  payload?: Record<string, unknown>;
};

export type TextBlock = {
  type: 'text';
  text: string;
};

export type ImageBlock = {
  type: 'image';
  url: string;
  alt?: string;
  width?: number;
  height?: number;
};

export type LinkBlock = {
  type: 'link';
  url: string;
  title?: string;
  description?: string;
  siteName?: string;
  thumbnailUrl?: string;
};

export type StatusBlock = {
  type: 'status';
  tone?: 'info' | 'success' | 'warning' | 'error';
  text: string;
};

export type ButtonGroupBlock = {
  type: 'buttons';
  buttons: MessageAction[];
};

export type MessageBlock =
  | TextBlock
  | ImageBlock
  | LinkBlock
  | StatusBlock
  | ButtonGroupBlock;

export type ChatMessage = {
  id: string;
  role: ChatMessageRole;
  author: string;
  time: string;
  createdAt?: number;
  avatarLabel?: string;
  avatarUrl?: string;
  blocks: MessageBlock[];
  actions?: MessageAction[];
  status?: 'sending' | 'sent' | 'failed' | 'streaming';
  sortKey?: number;
};
