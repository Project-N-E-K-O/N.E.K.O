import type {
  AvatarToolVariantId as CatalogAvatarToolVariantId,
  AvatarToolDefinitionId,
  AvatarToolDefinition,
} from './avatar-tools/catalog';
import { AVATAR_TOOL_DEFINITIONS } from './avatar-tools/catalog';

export type AvatarToolId = AvatarToolDefinitionId;

export type AvatarToolVariantId = CatalogAvatarToolVariantId;

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
  pointerImagePath: string;
  pointerImagePathAlt?: string;
  pointerImagePathAlt2?: string;
  pointerHotspotX?: number;
  pointerHotspotY?: number;
  pointerNaturalWidth?: number;
  pointerNaturalHeight?: number;
  pointerDisplayWidth?: number;
  pointerDisplayHeight?: number;
};

export const ACTIVE_AVATAR_TOOLS_STORAGE_KEY = 'neko.reactChatWindow.activeAvatarTools';
export const MAX_ACTIVE_AVATAR_TOOLS = 3;
export const DEFAULT_ACTIVE_AVATAR_TOOL_IDS: AvatarToolId[] = ['lollipop', 'fist', 'hammer'];

function projectAvatarToolDefinitionToItem(definition: AvatarToolDefinition): AvatarToolItem {
  const { primary, secondary, tertiary } = definition.visual.variants;
  const secondaryIcon = secondary.iconImagePath !== primary.iconImagePath
    ? secondary.iconImagePath
    : undefined;
  const tertiaryIcon = tertiary.iconImagePath !== primary.iconImagePath
    ? tertiary.iconImagePath
    : undefined;
  const secondaryPointer = secondary.pointerImagePath !== primary.pointerImagePath
    ? secondary.pointerImagePath
    : undefined;
  const tertiaryPointer = tertiary.pointerImagePath !== secondary.pointerImagePath
    ? tertiary.pointerImagePath
    : undefined;
  const secondaryOffsetX = secondary.menuOffsetX !== primary.menuOffsetX
    ? secondary.menuOffsetX
    : undefined;
  const secondaryOffsetY = secondary.menuOffsetY !== primary.menuOffsetY
    ? secondary.menuOffsetY
    : undefined;
  const tertiaryOffsetX = tertiary.menuOffsetX !== secondary.menuOffsetX
    ? tertiary.menuOffsetX
    : undefined;
  const tertiaryOffsetY = tertiary.menuOffsetY !== secondary.menuOffsetY
    ? tertiary.menuOffsetY
    : undefined;

  return {
    id: definition.id,
    labelKey: definition.label.key,
    labelFallback: definition.label.fallback,
    iconImagePath: primary.iconImagePath,
    ...(secondaryIcon ? { iconImagePathAlt: secondaryIcon } : {}),
    ...(tertiaryIcon ? { iconImagePathAlt2: tertiaryIcon } : {}),
    pointerImagePath: primary.pointerImagePath,
    ...(secondaryPointer ? { pointerImagePathAlt: secondaryPointer } : {}),
    ...(tertiaryPointer ? { pointerImagePathAlt2: tertiaryPointer } : {}),
    ...(definition.visual.menuScale !== 1 ? { menuIconScale: definition.visual.menuScale } : {}),
    ...(primary.menuOffsetX !== 0 ? { menuIconOffsetX: primary.menuOffsetX } : {}),
    ...(primary.menuOffsetY !== 0 ? { menuIconOffsetY: primary.menuOffsetY } : {}),
    ...(secondaryOffsetX !== undefined ? { menuIconOffsetXAlt: secondaryOffsetX } : {}),
    ...(secondaryOffsetY !== undefined ? { menuIconOffsetYAlt: secondaryOffsetY } : {}),
    ...(tertiaryOffsetX !== undefined ? { menuIconOffsetXAlt2: tertiaryOffsetX } : {}),
    ...(tertiaryOffsetY !== undefined ? { menuIconOffsetYAlt2: tertiaryOffsetY } : {}),
    pointerHotspotX: definition.visual.hotspotX,
    pointerHotspotY: definition.visual.hotspotY,
    pointerNaturalWidth: definition.visual.naturalWidth,
    pointerNaturalHeight: definition.visual.naturalHeight,
    pointerDisplayWidth: definition.visual.pointer.displayWidth,
    pointerDisplayHeight: definition.visual.pointer.displayHeight,
  };
}

export const AVAILABLE_AVATAR_TOOLS: AvatarToolItem[] =
  AVATAR_TOOL_DEFINITIONS.map(projectAvatarToolDefinitionToItem);

const AVAILABLE_AVATAR_TOOL_IDS = new Set<AvatarToolId>(AVAILABLE_AVATAR_TOOLS.map(item => item.id));

function getReactChatAssetVersion(): string {
  if (typeof window === 'undefined') return '';
  const version = window.__NEKO_REACT_CHAT_ASSET_VERSION__;
  return typeof version === 'string' ? version.trim() : '';
}

export function withAvatarToolAssetVersion(path: string, fallbackVersion = ''): string {
  const version = getReactChatAssetVersion() || fallbackVersion.trim();
  if (!version || !path) return path;
  const hashIndex = path.indexOf('#');
  const pathAndQuery = hashIndex >= 0 ? path.slice(0, hashIndex) : path;
  const hash = hashIndex >= 0 ? path.slice(hashIndex) : '';
  const queryIndex = pathAndQuery.indexOf('?');
  const pathname = queryIndex >= 0 ? pathAndQuery.slice(0, queryIndex) : pathAndQuery;
  const search = queryIndex >= 0 ? pathAndQuery.slice(queryIndex + 1) : '';
  const params = search.split('&').filter(Boolean).filter((entry) => {
    const encodedName = entry.split('=', 1)[0];
    try {
      return decodeURIComponent(encodedName.replace(/\+/g, ' ')) !== 'v';
    } catch {
      return true;
    }
  });
  params.push(`v=${encodeURIComponent(version)}`);
  return `${pathname}?${params.join('&')}${hash}`;
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
