import { z } from 'zod';
import {
  getAvatarToolRegistration,
  type AvatarToolEffectId,
  type AvatarToolId,
  type AvatarToolRoundChoiceGesture,
  type AvatarToolRoundResult,
  type AvatarToolSoundId,
  type AvatarToolTouchZone,
  type AvatarToolVariantId,
} from './catalog';
import type { AvatarInteractionPayload } from './protocol';

// Shared policy and geometry --------------------------------------------------

const finiteNumberSchema = z.number().finite();
const nonNegativeNumberSchema = finiteNumberSchema.nonnegative();
const positiveNumberSchema = finiteNumberSchema.positive();
const ratioSchema = positiveNumberSchema.max(1);

const touchZonesSchema = z.object({
  coordinateSpace: z.literal('normalized-avatar-bounds'),
  clampToBounds: z.literal(true),
  boundary: z.literal('inclusive'),
  ear: z.object({
    maxY: ratioSchema,
    leftMaxX: ratioSchema,
    rightMinX: ratioSchema,
  }).strict(),
  headMaxY: ratioSchema,
  faceMaxY: ratioSchema,
  fallback: z.literal('body'),
}).strict().superRefine((touchZones, context) => {
  if (touchZones.ear.maxY > touchZones.headMaxY || touchZones.headMaxY > touchZones.faceMaxY) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: 'touch-zone Y thresholds must be ordered' });
  }
  if (touchZones.ear.leftMaxX >= touchZones.ear.rightMinX) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['ear'],
      message: 'ear X thresholds must leave a center region',
    });
  }
});

const rangeSchema = z.object({
  geometry: z.object({
    shape: z.literal('ellipse'),
    radiusXFromWidth: ratioSchema,
    radiusYFromHeight: ratioSchema,
    boundary: z.literal('inclusive'),
  }).strict(),
  enterPadding: nonNegativeNumberSchema,
  exitPadding: nonNegativeNumberSchema,
  visualHold: z.object({
    durationMs: nonNegativeNumberSchema,
    semantics: z.literal('presentation-only'),
    grantsInteractionHit: z.literal(false),
  }).strict(),
  bounds: z.object({
    cacheTtlMs: nonNegativeNumberSchema,
    missingGraceMs: nonNegativeNumberSchema,
  }).strict(),
  touchZones: touchZonesSchema,
  forcedExit: z.object({
    uiExclusion: z.literal('immediate'),
    deactivation: z.literal('immediate'),
    hostExit: z.literal('immediate'),
  }).strict(),
}).strict().superRefine((range, context) => {
  if (range.exitPadding < range.enterPadding) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['exitPadding'],
      message: 'range.exitPadding must be at least range.enterPadding',
    });
  }
});

export const avatarToolRuntimePolicySchema = z.object({
  policyVersion: z.literal(1),
  range: rangeSchema,
  press: z.object({
    button: z.literal(0),
    requiresRawHit: z.literal(true),
    matchingRelease: z.object({
      pointerId: z.literal('same-as-press'),
      button: z.literal('same-as-press'),
    }).strict(),
    move: z.object({
      thresholdPx: positiveNumberSchema,
      comparison: z.literal('strictly-greater'),
    }).strict(),
  }).strict(),
  release: z.object({
    bounds: z.literal('fresh'),
    heldVisualRangeIsHit: z.literal(false),
    touchZone: z.literal('fresh-release-hit'),
    uiExclusion: z.literal('reject'),
  }).strict(),
}).strict();

export type AvatarToolRuntimePolicy = z.infer<typeof avatarToolRuntimePolicySchema>;

export function validateAvatarToolRuntimePolicy(policy: unknown): asserts policy is AvatarToolRuntimePolicy {
  avatarToolRuntimePolicySchema.parse(policy);
}

export const AVATAR_TOOL_RUNTIME_POLICY = {
  policyVersion: 1,
  range: {
    geometry: {
      shape: 'ellipse',
      radiusXFromWidth: 0.3,
      radiusYFromHeight: 0.475,
      boundary: 'inclusive',
    },
    enterPadding: 100,
    exitPadding: 116,
    visualHold: {
      durationMs: 180,
      semantics: 'presentation-only',
      grantsInteractionHit: false,
    },
    bounds: {
      cacheTtlMs: 80,
      missingGraceMs: 640,
    },
    touchZones: {
      coordinateSpace: 'normalized-avatar-bounds',
      clampToBounds: true,
      boundary: 'inclusive',
      ear: {
        maxY: 0.24,
        leftMaxX: 0.24,
        rightMinX: 0.76,
      },
      headMaxY: 0.34,
      faceMaxY: 0.62,
      fallback: 'body',
    },
    forcedExit: {
      uiExclusion: 'immediate',
      deactivation: 'immediate',
      hostExit: 'immediate',
    },
  },
  press: {
    button: 0,
    requiresRawHit: true,
    matchingRelease: {
      pointerId: 'same-as-press',
      button: 'same-as-press',
    },
    move: {
      thresholdPx: 6,
      comparison: 'strictly-greater',
    },
  },
  release: {
    bounds: 'fresh',
    heldVisualRangeIsHit: false,
    touchZone: 'fresh-release-hit',
    uiExclusion: 'reject',
  },
} as const satisfies AvatarToolRuntimePolicy;

validateAvatarToolRuntimePolicy(AVATAR_TOOL_RUNTIME_POLICY);

export const AVATAR_TOOL_RANGE_PADDING = AVATAR_TOOL_RUNTIME_POLICY.range.enterPadding;
export const AVATAR_TOOL_RANGE_EXIT_PADDING = AVATAR_TOOL_RUNTIME_POLICY.range.exitPadding;


// Browser hit-testing adapter ------------------------------------------------

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
  const { left, top, width, height } = raw;
  if (
    typeof left !== 'number'
    || typeof top !== 'number'
    || typeof width !== 'number'
    || typeof height !== 'number'
    || ![left, top, width, height].every(Number.isFinite)
    || width <= 0
    || height <= 0
  ) return null;
  return {
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    centerX: left + width / 2,
    centerY: top + height / 2,
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


// Tool rule port and dispatch ------------------------------------------------

type AvatarToolInteractionCommitFromPayload<Payload extends AvatarInteractionPayload> =
  Payload extends AvatarInteractionPayload
    ? Omit<Payload, 'interactionId' | 'target' | 'pointer' | 'timestamp'> & {
      clientX: number;
      clientY: number;
      timestamp?: number;
    }
    : never;

export type AvatarToolInteractionCommit =
  AvatarToolInteractionCommitFromPayload<AvatarInteractionPayload>;

export type AvatarToolRoundChoiceConfirmation = {
  userGesture: AvatarToolRoundChoiceGesture;
  userVariant: AvatarToolVariantId;
  avatarGesture: AvatarToolRoundChoiceGesture;
  avatarVariant: AvatarToolVariantId;
  roundResult: AvatarToolRoundResult;
  revealEffect: AvatarToolEffectId;
  resultSound: AvatarToolSoundId;
};

export type AvatarToolCommand = {
  commit?: AvatarToolInteractionCommit;
  rangeVariant?: AvatarToolVariantId;
  outsideVariant?: AvatarToolVariantId;
  sound?: AvatarToolSoundId;
  effect?: AvatarToolEffectId;
  effectMode?: string;
  pressFeedback?: 'until-pointer-release';
  roundChoiceCycle?: 'pause';
  roundChoiceConfirmation?: AvatarToolRoundChoiceConfirmation;
  resetOutsideVariantAfterMs?: number;
};

export type AvatarToolRuleContext = {
  toolId: AvatarToolId;
  clientX: number;
  clientY: number;
  hit: AvatarRangeHit | null;
  rangeVariant: AvatarToolVariantId;
  outsideVariant: AvatarToolVariantId;
  visibleVariant: AvatarToolVariantId;
  interactionLocked: boolean;
  recordBurst(key: string, windowMs: number): number;
  random(): number;
};

export type AvatarToolRule = (context: AvatarToolRuleContext) => AvatarToolCommand;

export type AvatarToolRuleHandlers = {
  pointerDown: AvatarToolRule;
  commit: AvatarToolRule;
  pointerRelease: () => AvatarToolCommand;
};

export function resolveAvatarToolPointerDown(context: AvatarToolRuleContext): AvatarToolCommand {
  return getAvatarToolRegistration(context.toolId).handlers.pointerDown(context);
}

export function resolveAvatarToolCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (context.interactionLocked) return {};
  return getAvatarToolRegistration(context.toolId).handlers.commit(context);
}

export function resolveAvatarToolPointerRelease(toolId: AvatarToolId): AvatarToolCommand {
  return getAvatarToolRegistration(toolId).handlers.pointerRelease();
}
