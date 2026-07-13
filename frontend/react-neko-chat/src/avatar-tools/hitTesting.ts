import type { AvatarToolTouchZone } from './catalog';
import {
  AVATAR_TOOL_RUNTIME_POLICY,
  AVATAR_TOOL_RANGE_PADDING,
  type AvatarToolRuntimePolicy,
} from './interactionPolicy';

export type AvatarToolBounds = {
  left: number;
  right: number;
  top: number;
  bottom: number;
  width: number;
  height: number;
  centerX?: number;
  centerY?: number;
};

export type AvatarRangeHit = {
  bounds: AvatarToolBounds;
  touchZone: AvatarToolTouchZone;
};

type AvatarToolHostManager = {
  currentModel?: unknown;
  getModelScreenBounds?: () => unknown;
};

type AvatarToolHostWindow = Window & {
  mmdManager?: AvatarToolHostManager;
  vrmManager?: AvatarToolHostManager;
  live2dManager?: AvatarToolHostManager;
  __nekoDesktopAvatarBounds?: unknown;
};

export const AVATAR_TOOL_UI_EXCLUSION_SELECTOR = [
  '.composer-bottom-tools',
  '.composer-tool-menu',
  '.composer-icon-popover',
  '.composer-tool-btn',
  '.composer-icon-button',
  '[data-compact-hit-region="true"]',
  '.compact-input-tool-fan',
  '.compact-input-tool-toggle',
  '.compact-chat-capsule-button',
  '.avatar-tool-quickbar',
  '.avatar-tool-manager-overlay',
  '.avatar-tool-manager-dialog',
  '.compact-export-history-anchor',
  '.compact-history-visibility-handle',
  '.send-button-circle',
  '.window-topbar-actions',
  '.topbar-action-btn',
  '.message-action-button',
  '.chat-window.chat-surface-mode-full',
  '#react-chat-window-drag-handle',
  '.react-chat-resize-edge',
  '#yui-guide-standalone-interaction-shield',
  '.yui-guide-interaction-shield',
  '#live2d-floating-buttons',
  '#vrm-floating-buttons',
  '#mmd-floating-buttons',
  '#live2d-return-button-container',
  '#vrm-return-button-container',
  '#mmd-return-button-container',
  '#live2d-lock-icon',
  '#vrm-lock-icon',
  '#mmd-lock-icon',
  '.live2d-floating-btn',
  '.vrm-floating-btn',
  '.mmd-floating-btn',
  '.live2d-trigger-btn',
  '.vrm-trigger-btn',
  '.mmd-trigger-btn',
  '.live2d-return-btn',
  '.vrm-return-btn',
  '.mmd-return-btn',
  '.live2d-popup',
  '.vrm-popup',
  '.mmd-popup',
  '[id^="live2d-popup-"]',
  '[id^="vrm-popup-"]',
  '[id^="mmd-popup-"]',
  '[data-neko-sidepanel]',
].join(', ');

export function normalizeAvatarToolBounds(bounds: unknown): AvatarToolBounds | null {
  if (!bounds || typeof bounds !== 'object') return null;
  const raw = bounds as Partial<AvatarToolBounds>;
  const left = Number(raw.left);
  const top = Number(raw.top);
  const width = Number(raw.width);
  const height = Number(raw.height);
  if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
  return {
    left,
    top,
    width,
    height,
    right: Number.isFinite(Number(raw.right)) ? Number(raw.right) : left + width,
    bottom: Number.isFinite(Number(raw.bottom)) ? Number(raw.bottom) : top + height,
    centerX: Number.isFinite(Number(raw.centerX)) ? Number(raw.centerX) : left + width / 2,
    centerY: Number.isFinite(Number(raw.centerY)) ? Number(raw.centerY) : top + height / 2,
  };
}

function isElementVisible(elementId: string): boolean {
  const element = document.getElementById(elementId);
  if (!element) return false;
  const style = window.getComputedStyle(element);
  return style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && element.getClientRects().length > 0;
}

export function collectAvatarToolBounds(): AvatarToolBounds[] {
  const host = window as AvatarToolHostWindow;
  const desktop = normalizeAvatarToolBounds(host.__nekoDesktopAvatarBounds);
  const candidates: Array<[string, AvatarToolHostManager | undefined]> = [
    ['mmd-container', host.mmdManager],
    ['vrm-container', host.vrmManager],
    ['live2d-container', host.live2dManager],
  ];
  return [
    ...(desktop ? [desktop] : []),
    ...candidates.flatMap(([containerId, manager]) => {
      if (!manager?.currentModel || typeof manager.getModelScreenBounds !== 'function') return [];
      if (!isElementVisible(containerId)) return [];
      try {
        const bounds = normalizeAvatarToolBounds(manager.getModelScreenBounds());
        return bounds ? [bounds] : [];
      } catch {
        return [];
      }
    }),
  ];
}

export function isPointInsideAvatarBounds(
  bounds: AvatarToolBounds,
  clientX: number,
  clientY: number,
  padding: number = AVATAR_TOOL_RANGE_PADDING,
  policy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): boolean {
  if (
    clientX < bounds.left - padding
    || clientX > bounds.right + padding
    || clientY < bounds.top - padding
    || clientY > bounds.bottom + padding
  ) return false;
  const geometry = policy.range.geometry;
  if (geometry.shape !== 'ellipse' || geometry.boundary !== 'inclusive') return false;
  const centerX = bounds.centerX ?? (bounds.left + bounds.right) / 2;
  const centerY = bounds.centerY ?? (bounds.top + bounds.bottom) / 2;
  const radiusX = bounds.width * geometry.radiusXFromWidth + padding;
  const radiusY = bounds.height * geometry.radiusYFromHeight + padding;
  const normalizedX = (clientX - centerX) / radiusX;
  const normalizedY = (clientY - centerY) / radiusY;
  return normalizedX * normalizedX + normalizedY * normalizedY <= 1;
}

export function classifyAvatarTouchZone(
  bounds: AvatarToolBounds,
  clientX: number,
  clientY: number,
  policy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): AvatarToolTouchZone {
  const touchZones = policy.range.touchZones;
  if (touchZones.coordinateSpace !== 'normalized-avatar-bounds') return touchZones.fallback;
  const clamp = (value: number) => Math.min(Math.max(value, 0), 1);
  const normalize = (value: number) => touchZones.clampToBounds ? clamp(value) : value;
  const relativeX = normalize((clientX - bounds.left) / bounds.width);
  const relativeY = normalize((clientY - bounds.top) / bounds.height);
  const atOrBelow = (value: number, threshold: number) => (
    touchZones.boundary === 'inclusive' && value <= threshold
  );
  if (
    atOrBelow(relativeY, touchZones.ear.maxY)
    && (
      atOrBelow(relativeX, touchZones.ear.leftMaxX)
      || relativeX >= touchZones.ear.rightMinX
    )
  ) return 'ear';
  if (atOrBelow(relativeY, touchZones.headMaxY)) return 'head';
  if (atOrBelow(relativeY, touchZones.faceMaxY)) return 'face';
  return touchZones.fallback;
}

export function getAvatarRangeHit(
  clientX: number,
  clientY: number,
  bounds: AvatarToolBounds[],
  padding: number = AVATAR_TOOL_RANGE_PADDING,
  policy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): AvatarRangeHit | null {
  const matched = bounds.find(item => isPointInsideAvatarBounds(item, clientX, clientY, padding, policy));
  return matched ? { bounds: matched, touchZone: classifyAvatarTouchZone(matched, clientX, clientY, policy) } : null;
}

export function isPointerOverAvatarToolUi(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(AVATAR_TOOL_UI_EXCLUSION_SELECTOR);
}

export function isPointWithinAvatarToolUi(clientX: number, clientY: number): boolean {
  const elements = typeof document.elementsFromPoint === 'function'
    ? document.elementsFromPoint(clientX, clientY)
    : typeof document.elementFromPoint === 'function'
      ? [document.elementFromPoint(clientX, clientY)].filter((item): item is Element => item instanceof Element)
      : [];
  return elements.some(element => !!element.closest(AVATAR_TOOL_UI_EXCLUSION_SELECTOR));
}
