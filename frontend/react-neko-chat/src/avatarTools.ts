import type {
  AvatarToolVariantId as CatalogAvatarToolVariantId,
  AvatarToolId,
} from './avatar-tools/catalog';
import {
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  withAvatarToolAssetVersion,
} from './avatar-tools/catalog';
import {
  BUILT_IN_AVATAR_TOOL_REGISTRY,
  type AvatarToolItem,
} from './avatar-tools/registry';
import { i18n } from './i18n';

export { withAvatarToolAssetVersion };

export type { AvatarToolId } from './avatar-tools/catalog';

export type AvatarToolVariantId = CatalogAvatarToolVariantId;

export type { AvatarToolItem } from './avatar-tools/registry';

export const ACTIVE_AVATAR_TOOLS_STORAGE_KEY = 'neko.reactChatWindow.activeAvatarTools';
export const MAX_ACTIVE_AVATAR_TOOLS = 3;
export const DEFAULT_ACTIVE_AVATAR_TOOL_IDS: AvatarToolId[] = ['lollipop', 'fist', 'hammer'];

export function getAvatarToolItemLabel(item: AvatarToolItem): string {
  return item.label.kind === 'literal'
    ? item.label.value
    : i18n(item.label.key, item.label.fallback);
}

const REGISTERED_AVATAR_TOOLS: ReadonlyArray<AvatarToolItem> = BUILT_IN_AVATAR_TOOL_REGISTRY.items;

export const AVAILABLE_COMPACT_AVATAR_TOOLS: ReadonlyArray<AvatarToolItem> = REGISTERED_AVATAR_TOOLS;

const AVAILABLE_AVATAR_TOOL_IDS = new Set<AvatarToolId>(REGISTERED_AVATAR_TOOLS.map(item => item.id));

export function isAvatarToolId(value: unknown): value is AvatarToolId {
  return typeof value === 'string' && (
    AVAILABLE_AVATAR_TOOL_IDS.has(value as AvatarToolId)
    || LOCAL_AVATAR_TOOL_ID_PATTERN.test(value)
  );
}

export function isLocalAvatarToolId(value: unknown): value is `local-${string}` {
  return typeof value === 'string' && LOCAL_AVATAR_TOOL_ID_PATTERN.test(value);
}

// 槽位记录的是「用户想装备什么」，不是「现在能不能用」。本地道具的列表是
// 尽力而为的（校验失败会被跳过），所以持久化和草稿一律走这个只校验形状的
// 入口，把暂时不可用的 id 原样留住；能不能画出来由渲染层按 registry 决定。
export function sanitizeAvatarToolSlots(value: unknown): AvatarToolId[] {
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

// 额外按当前 registry 收窄，只给真正需要「此刻可用」的地方用。
export function sanitizeAvatarToolIds(
  value: unknown,
  validIds: ReadonlySet<AvatarToolId> = BUILT_IN_AVATAR_TOOL_REGISTRY.validIds,
): AvatarToolId[] {
  if (!Array.isArray(value)) {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }
  return sanitizeAvatarToolSlots(value).filter(toolId => validIds.has(toolId));
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
    return sanitizeAvatarToolSlots(JSON.parse(rawValue));
  } catch {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }
}

// 删除是确定性的「这个道具不存在了」，和 list_items 那种尽力而为的缺席不同，
// 所以要落盘。但只摘掉这一个 id：其余槽位可能只是本轮列表没带上，不能顺手
// 一起 sanitize 掉。
export function forgetPersistedAvatarToolId(toolId: AvatarToolId) {
  if (typeof window === 'undefined') return;
  try {
    const rawValue = window.localStorage?.getItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY);
    if (!rawValue) return;
    const parsed = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) return;
    const next = parsed.filter(candidate => candidate !== toolId);
    if (next.length === parsed.length) return;
    window.localStorage?.setItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Keep in-memory state when localStorage is unavailable.
  }
}

export function persistActiveAvatarToolIds(ids: AvatarToolId[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage?.setItem(
      ACTIVE_AVATAR_TOOLS_STORAGE_KEY,
      JSON.stringify(sanitizeAvatarToolSlots(ids)),
    );
  } catch {
    // Keep in-memory state when localStorage is unavailable.
  }
}

export function resolveAvatarToolImagePaths(item: AvatarToolItem, variant: AvatarToolVariantId) {
  const iconImagePath = variant === 'tertiary' && item.iconImagePathAlt2
    ? item.iconImagePathAlt2
    : variant === 'secondary' && item.iconImagePathAlt
      ? item.iconImagePathAlt
      : item.iconImagePath;
  const pointerImagePath = variant === 'tertiary' && item.pointerImagePathAlt2
    ? item.pointerImagePathAlt2
    : variant === 'secondary' && item.pointerImagePathAlt
      ? item.pointerImagePathAlt
      : variant === 'tertiary' && item.pointerImagePathAlt
        ? item.pointerImagePathAlt
        : item.pointerImagePath;

  return {
    iconImagePath: withAvatarToolAssetVersion(iconImagePath),
    pointerImagePath: withAvatarToolAssetVersion(pointerImagePath),
  };
}

export function resolveAvatarToolMenuIconVisual(item: AvatarToolItem, variant: AvatarToolVariantId) {
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
