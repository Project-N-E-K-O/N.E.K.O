import type { RefObject } from 'react';
import {
  AVAILABLE_AVATAR_TOOLS,
  resolveAvatarToolImagePaths,
  type AvatarToolId,
  type AvatarToolItem,
  type AvatarToolVariantId,
} from '../avatarTools';
import type { AvatarToolDefinition, AvatarToolVariantSource } from './catalog';
import { AVATAR_TOOL_DEFINITIONS, getAvatarToolRegistration } from './catalog';
import type {
  ActiveHammerSwingEffectExecution,
  AvatarToolTransientVisualEffect,
} from './feedback';
import { isPointWithinAvatarToolUi, isPointerOverAvatarToolUi } from './hitTesting';
import type { AvatarToolPointer } from './payload';

export type AvatarToolVariantState = Record<AvatarToolId, AvatarToolVariantId>;

export type AvatarToolPresentation = {
  activeTool: AvatarToolItem | null;
  avatarRangeVariant: AvatarToolVariantId;
  outsideRangeVariant: AvatarToolVariantId;
  effectiveVariant: AvatarToolVariantId;
  withinAvatarRange: boolean;
  imageKind: 'pointer' | 'icon';
};

export function getAvatarTool(toolId: AvatarToolId | null): AvatarToolItem | null {
  return AVAILABLE_AVATAR_TOOLS.find(item => item.id === toolId) ?? null;
}

export function createAvatarToolVariantState(
  definitions: ReadonlyArray<AvatarToolDefinition> = AVATAR_TOOL_DEFINITIONS,
): AvatarToolVariantState {
  const state = {} as AvatarToolVariantState;
  definitions.forEach((definition) => {
    state[definition.id] = definition.visual.initialVariant;
  });
  return state;
}

function resolveVariantSource(
  source: AvatarToolVariantSource,
  rangeVariant: AvatarToolVariantId,
  outsideVariant: AvatarToolVariantId,
): AvatarToolVariantId {
  if (source === 'range') return rangeVariant;
  if (source === 'outside') return outsideVariant;
  return 'primary';
}

export function resolveAvatarToolVisualPresentation({
  definition,
  rangeVariant,
  outsideVariant,
  overAvatarRange,
  withinAvatarRange,
  effectActive,
}: {
  definition: AvatarToolDefinition;
  rangeVariant: AvatarToolVariantId;
  outsideVariant: AvatarToolVariantId;
  overAvatarRange: boolean;
  withinAvatarRange: boolean;
  effectActive: boolean;
}): Pick<AvatarToolPresentation, 'effectiveVariant' | 'imageKind'> {
  const source = overAvatarRange
    ? definition.visual.presentation.inRangeVariantSource
    : definition.visual.presentation.outsideVariantSource;
  return {
    effectiveVariant: resolveVariantSource(source, rangeVariant, outsideVariant),
    imageKind: withinAvatarRange
      ? 'icon'
      : effectActive
        ? definition.visual.presentation.effectActiveImageKind
        : 'pointer',
  };
}

export function deriveAvatarToolPresentation({
  activeToolId,
  rangeVariants,
  outsideVariants,
  overAvatarRange,
  overCompactZone,
  insideHostWindow,
  effectActive,
}: {
  activeToolId: AvatarToolId | null;
  rangeVariants: AvatarToolVariantState;
  outsideVariants: AvatarToolVariantState;
  overAvatarRange: boolean;
  overCompactZone: boolean;
  insideHostWindow: boolean;
  effectActive: boolean;
}): AvatarToolPresentation {
  const activeTool = getAvatarTool(activeToolId);
  const avatarRangeVariant = activeToolId ? rangeVariants[activeToolId] : 'primary';
  const outsideRangeVariant = activeToolId ? outsideVariants[activeToolId] : 'primary';
  const withinAvatarRange = insideHostWindow && overAvatarRange && !overCompactZone;
  const visual = activeToolId
    ? resolveAvatarToolVisualPresentation({
      definition: getAvatarToolRegistration(activeToolId).definition,
      rangeVariant: avatarRangeVariant,
      outsideVariant: outsideRangeVariant,
      overAvatarRange,
      withinAvatarRange,
      effectActive,
    })
    : { effectiveVariant: avatarRangeVariant, imageKind: 'pointer' as const };

  return {
    activeTool,
    avatarRangeVariant,
    outsideRangeVariant,
    effectiveVariant: visual.effectiveVariant,
    withinAvatarRange,
    imageKind: visual.imageKind,
  };
}

export type AvatarToolImpactEffectVisualModel = ActiveHammerSwingEffectExecution & {
  pointerImagePath: string;
  idleImagePath: string;
  impactImagePath: string;
};

export type AvatarToolVisualModel = {
  activeTool: AvatarToolItem | null;
  activeToolId: AvatarToolId | null;
  effectiveVariant: AvatarToolVariantId;
  avatarRangeVariant: AvatarToolVariantId;
  withinAvatarRange: boolean;
  overlayRef: RefObject<HTMLDivElement>;
  overlayActive: boolean;
  overlayCompact: boolean;
  overlayImagePath: string;
  overlayEffect: AvatarToolImpactEffectVisualModel | null;
  transientEffects: AvatarToolTransientVisualEffect[];
};

export function buildAvatarToolVisualModel({
  activeTool,
  activeToolId,
  effectiveVariant,
  avatarRangeVariant,
  withinAvatarRange,
  overlayRef,
  overlayActive,
  overlayCompact,
  overlayEffectExecution,
  transientEffects,
}: Omit<AvatarToolVisualModel, 'overlayImagePath' | 'overlayEffect'> & {
  overlayEffectExecution: ActiveHammerSwingEffectExecution | null;
}) : AvatarToolVisualModel {
  const activeImagePaths = activeTool ? resolveAvatarToolImagePaths(activeTool, effectiveVariant) : null;
  const overlayEffect = activeTool && overlayEffectExecution ? {
    ...overlayEffectExecution,
    pointerImagePath: resolveAvatarToolImagePaths(activeTool, effectiveVariant).pointerImagePath,
    idleImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.recipe.variants.idle).iconImagePath,
    impactImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.recipe.variants.impact).iconImagePath,
  } : null;
  return {
    activeTool,
    activeToolId,
    effectiveVariant,
    avatarRangeVariant,
    withinAvatarRange,
    overlayRef,
    overlayActive,
    overlayCompact,
    overlayImagePath: activeTool && !overlayEffect
      ? (overlayCompact ? activeImagePaths?.pointerImagePath ?? '' : activeImagePaths?.iconImagePath ?? '')
      : '',
    overlayEffect,
    transientEffects,
  };
}

export function getAvatarToolPointer(event: {
  clientX: number;
  clientY: number;
  screenX: number;
  screenY: number;
}): AvatarToolPointer {
  return {
    x: event.clientX,
    y: event.clientY,
    ...(Number.isFinite(event.screenX) && Number.isFinite(event.screenY)
      ? { screenX: event.screenX, screenY: event.screenY }
      : {}),
  };
}

export function isAvatarToolUiExcluded(clientX: number, clientY: number, target: EventTarget | null): boolean {
  return isPointerOverAvatarToolUi(target) || isPointWithinAvatarToolUi(clientX, clientY);
}

export function getMonotonicNow(): number {
  return performance.now();
}

export function supportsFinePointer(): boolean {
  try {
    return typeof window.matchMedia !== 'function' || window.matchMedia('(pointer: fine)').matches;
  } catch {
    return true;
  }
}

export function isElectronMultiWindow(): boolean {
  return (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ === true;
}

function px(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return `${Object.is(rounded, -0) ? 0 : rounded}px`;
}

export function getAvatarToolOverlayTransformFromDefinition(
  definition: AvatarToolDefinition,
  compact: boolean,
  pointer: AvatarToolPointer,
): string {
  const mode = compact ? definition.visual.pointer : definition.visual.inRange;
  const anchor = mode.renderedAnchor;
  return `translate3d(${px(pointer.x - anchor.x)}, ${px(pointer.y - anchor.y)}, 0)`;
}

export function getAvatarToolOverlayTransform(
  item: AvatarToolItem,
  compact: boolean,
  pointer: AvatarToolPointer,
): string {
  return getAvatarToolOverlayTransformFromDefinition(
    getAvatarToolRegistration(item.id).definition,
    compact,
    pointer,
  );
}
