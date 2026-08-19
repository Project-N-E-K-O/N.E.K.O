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
  type AvatarToolRegistrySnapshot,
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
export const AVAILABLE_FULL_AVATAR_TOOLS: ReadonlyArray<AvatarToolItem> = REGISTERED_AVATAR_TOOLS;

const AVAILABLE_AVATAR_TOOL_IDS = new Set<AvatarToolId>(REGISTERED_AVATAR_TOOLS.map(item => item.id));

export function isAvatarToolId(value: unknown): value is AvatarToolId {
  return typeof value === 'string' && (
    AVAILABLE_AVATAR_TOOL_IDS.has(value as AvatarToolId)
    || LOCAL_AVATAR_TOOL_ID_PATTERN.test(value)
  );
}

export function sanitizeAvatarToolIds(
  value: unknown,
  validIds: ReadonlySet<AvatarToolId> = BUILT_IN_AVATAR_TOOL_REGISTRY.validIds,
): AvatarToolId[] {
  if (!Array.isArray(value)) {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }

  const next: AvatarToolId[] = [];
  value.forEach((candidate) => {
    if (!isAvatarToolId(candidate) || !validIds.has(candidate)) return;
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
    const parsed = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
    const next: AvatarToolId[] = [];
    parsed.forEach((candidate) => {
      if (!isAvatarToolId(candidate) || next.includes(candidate) || next.length >= MAX_ACTIVE_AVATAR_TOOLS) return;
      next.push(candidate);
    });
    return next;
  } catch {
    return [...DEFAULT_ACTIVE_AVATAR_TOOL_IDS];
  }
}

export function persistActiveAvatarToolIds(
  ids: AvatarToolId[],
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage?.setItem(
      ACTIVE_AVATAR_TOOLS_STORAGE_KEY,
      JSON.stringify(sanitizeAvatarToolIds(ids, registry.validIds)),
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
