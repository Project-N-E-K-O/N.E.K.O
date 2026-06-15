import type { AvatarInteractionPayload } from './message-schema';

export type AvatarToolId = AvatarInteractionPayload['toolId'];

export type CursorVariant = 'primary' | 'secondary' | 'tertiary';

declare global {
  interface Window {
    __NEKO_REACT_CHAT_ASSET_VERSION__?: string;
  }
}

export type AvatarToolItem = {
  id: AvatarToolId;
  labelKey: string;
  labelFallback: string;
  iconImagePath: string;
  iconImagePathAlt?: string;
  iconImagePathAlt2?: string;
  menuIconScale?: number;
  menuIconOffsetX?: number;
  menuIconOffsetY?: number;
  menuIconOffsetXAlt?: number;
  menuIconOffsetYAlt?: number;
  menuIconOffsetXAlt2?: number;
  menuIconOffsetYAlt2?: number;
  cursorImagePath: string;
  cursorImagePathAlt?: string;
  cursorImagePathAlt2?: string;
  cursorHotspotX?: number;
  cursorHotspotY?: number;
};

export const ACTIVE_AVATAR_TOOLS_STORAGE_KEY = 'neko.reactChatWindow.activeAvatarTools';
export const MAX_ACTIVE_AVATAR_TOOLS = 3;
export const DEFAULT_ACTIVE_AVATAR_TOOL_IDS: AvatarToolId[] = ['lollipop', 'fist', 'hammer'];

export const AVAILABLE_AVATAR_TOOLS: AvatarToolItem[] = [
  {
    id: 'lollipop',
    labelKey: 'chat.toolLollipop',
    labelFallback: '棒棒糖',
    iconImagePath: '/static/icons/chat_sugar1.png',
    iconImagePathAlt: '/static/icons/chat_sugar2.png',
    iconImagePathAlt2: '/static/icons/chat_sugar3.png',
    cursorImagePath: '/static/icons/chat_sugar1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_sugar2_cursor.png',
    menuIconScale: 1.18,
    cursorHotspotX: 27,
    cursorHotspotY: 46,
  },
  {
    id: 'fist',
    labelKey: 'chat.toolFist',
    labelFallback: '猫爪',
    iconImagePath: '/static/icons/cat_claw1.png',
    iconImagePathAlt: '/static/icons/cat_claw2.png',
    cursorImagePath: '/static/icons/cat_claw1_cursor.png',
    cursorImagePathAlt: '/static/icons/cat_claw2_cursor.png',
    cursorHotspotX: 39,
    cursorHotspotY: 46,
  },
  {
    id: 'hammer',
    labelKey: 'chat.toolHammer',
    labelFallback: '锤子',
    iconImagePath: '/static/icons/chat_hammer1.png',
    iconImagePathAlt: '/static/icons/chat_hammer2.png',
    cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_hammer2_cursor.png',
    menuIconScale: 1.52,
    menuIconOffsetX: -8,
    menuIconOffsetY: 4,
    menuIconOffsetXAlt: 1,
    menuIconOffsetYAlt: -1,
    cursorHotspotX: 50,
    cursorHotspotY: 54,
  },
];

const AVAILABLE_AVATAR_TOOL_IDS = new Set<AvatarToolId>(AVAILABLE_AVATAR_TOOLS.map(item => item.id));

function getReactChatAssetVersion(): string {
  if (typeof window === 'undefined') return '';
  const version = window.__NEKO_REACT_CHAT_ASSET_VERSION__;
  return typeof version === 'string' ? version.trim() : '';
}

export function withAvatarToolAssetVersion(path: string): string {
  const version = getReactChatAssetVersion();
  if (!version || !path) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}v=${encodeURIComponent(version)}`;
}

export function isAvatarToolId(value: unknown): value is AvatarToolId {
  return typeof value === 'string' && AVAILABLE_AVATAR_TOOL_IDS.has(value as AvatarToolId);
}

export function sanitizeAvatarToolIds(value: unknown): AvatarToolId[] {
  if (!Array.isArray(value)) {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }

  const next: AvatarToolId[] = [];
  value.forEach((candidate) => {
    if (!isAvatarToolId(candidate)) return;
    if (next.includes(candidate)) return;
    if (next.length >= MAX_ACTIVE_AVATAR_TOOLS) return;
    next.push(candidate);
  });
  return next;
}

export function readPersistedActiveAvatarToolIds(): AvatarToolId[] {
  if (typeof window === 'undefined') {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }

  try {
    const rawValue = window.localStorage?.getItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY);
    if (rawValue === null || typeof rawValue === 'undefined') {
      return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
    }
    return sanitizeAvatarToolIds(JSON.parse(rawValue));
  } catch {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }
}

export function persistActiveAvatarToolIds(ids: AvatarToolId[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage?.setItem(
      ACTIVE_AVATAR_TOOLS_STORAGE_KEY,
      JSON.stringify(sanitizeAvatarToolIds(ids)),
    );
  } catch {
    // Keep in-memory state when localStorage is unavailable.
  }
}

export function resolveAvatarToolImagePaths(item: AvatarToolItem, variant: CursorVariant) {
  const iconImagePath = variant === 'tertiary' && item.iconImagePathAlt2
    ? item.iconImagePathAlt2
    : variant === 'secondary' && item.iconImagePathAlt
      ? item.iconImagePathAlt
      : item.iconImagePath;
  const cursorImagePath = variant === 'tertiary' && item.cursorImagePathAlt2
    ? item.cursorImagePathAlt2
    : variant === 'secondary' && item.cursorImagePathAlt
      ? item.cursorImagePathAlt
      : variant === 'tertiary' && item.cursorImagePathAlt
        ? item.cursorImagePathAlt
        : item.cursorImagePath;

  return {
    iconImagePath: withAvatarToolAssetVersion(iconImagePath),
    cursorImagePath: withAvatarToolAssetVersion(cursorImagePath),
  };
}

export function resolveAvatarToolMenuIconVisual(item: AvatarToolItem, variant: CursorVariant) {
  const imagePath = variant === 'tertiary' && item.iconImagePathAlt2
    ? item.iconImagePathAlt2
    : variant === 'secondary' && item.iconImagePathAlt
      ? item.iconImagePathAlt
      : item.iconImagePath;
  const offsetX = variant === 'tertiary'
    ? (item.menuIconOffsetXAlt2 ?? item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
      : (item.menuIconOffsetX ?? 0);
  const offsetY = variant === 'tertiary'
    ? (item.menuIconOffsetYAlt2 ?? item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
      : (item.menuIconOffsetY ?? 0);

  return {
    imagePath: withAvatarToolAssetVersion(imagePath),
    offsetX,
    offsetY,
  };
}
