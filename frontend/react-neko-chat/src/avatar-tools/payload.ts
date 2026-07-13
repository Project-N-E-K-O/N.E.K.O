import {
  withAvatarToolAssetVersion,
  type AvatarToolItem,
  type AvatarToolVariantId,
} from '../avatarTools';
import {
  type AvatarToolStatePayload,
} from '../message-schema';
import { buildDesktopAvatarToolContract } from './desktopContract';
import {
  avatarInteractionPayloadSchema,
  type AvatarInteractionPayload,
} from './interactionContract';
import type { AvatarToolInteractionCommit } from './interactionEngine';

export type AvatarToolPointer = {
  x: number;
  y: number;
  screenX?: number;
  screenY?: number;
};

export function createAvatarInteractionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `avatar-int-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function buildAvatarInteractionPayload(commit: AvatarToolInteractionCommit): AvatarInteractionPayload {
  const {
    clientX,
    clientY,
    timestamp,
    ...facts
  } = commit;
  return avatarInteractionPayloadSchema.parse({
    ...facts,
    interactionId: createAvatarInteractionId(),
    target: 'avatar' as const,
    pointer: { clientX, clientY },
    timestamp: timestamp ?? Date.now(),
  });
}

function buildAvatarToolDescriptor(activeTool: AvatarToolItem | null, label?: string) {
  return activeTool ? {
    id: activeTool.id,
    label,
    iconImagePath: withAvatarToolAssetVersion(activeTool.iconImagePath),
    iconImagePathAlt: activeTool.iconImagePathAlt ? withAvatarToolAssetVersion(activeTool.iconImagePathAlt) : undefined,
    iconImagePathAlt2: activeTool.iconImagePathAlt2 ? withAvatarToolAssetVersion(activeTool.iconImagePathAlt2) : undefined,
    pointerImagePath: withAvatarToolAssetVersion(activeTool.pointerImagePath),
    pointerImagePathAlt: activeTool.pointerImagePathAlt ? withAvatarToolAssetVersion(activeTool.pointerImagePathAlt) : undefined,
    pointerImagePathAlt2: activeTool.pointerImagePathAlt2 ? withAvatarToolAssetVersion(activeTool.pointerImagePathAlt2) : undefined,
    pointerHotspotX: activeTool.pointerHotspotX,
    pointerHotspotY: activeTool.pointerHotspotY,
    pointerNaturalWidth: activeTool.pointerNaturalWidth,
    pointerNaturalHeight: activeTool.pointerNaturalHeight,
    pointerDisplayWidth: activeTool.pointerDisplayWidth,
    pointerDisplayHeight: activeTool.pointerDisplayHeight,
    menuIconScale: activeTool.menuIconScale,
  } : null;
}

export function buildAvatarToolDescriptorStatePayload({
  activeTool,
  avatarRangeVariant,
  outsideRangeVariant,
}: {
  activeTool: AvatarToolItem | null;
  avatarRangeVariant?: AvatarToolVariantId;
  outsideRangeVariant?: AvatarToolVariantId;
}): AvatarToolStatePayload {
  return {
    active: !!activeTool,
    toolId: activeTool?.id ?? null,
    desktopContract: buildDesktopAvatarToolContract(activeTool?.id ?? null),
    ...(activeTool ? {
      avatarRangeVariant: avatarRangeVariant ?? 'primary',
      outsideRangeVariant: outsideRangeVariant ?? 'primary',
    } : {}),
    timestamp: Date.now(),
  };
}

export function buildAvatarToolStatePayload({
  activeTool,
  variant,
  avatarRangeVariant,
  outsideRangeVariant,
  imageKind,
  withinAvatarRange,
  overCompactZone,
  insideHostWindow,
  pointer,
  textContext,
  label,
}: {
  activeTool: AvatarToolItem | null;
  variant: AvatarToolVariantId;
  avatarRangeVariant: AvatarToolVariantId;
  outsideRangeVariant: AvatarToolVariantId;
  imageKind: 'pointer' | 'icon';
  withinAvatarRange: boolean;
  overCompactZone: boolean;
  insideHostWindow: boolean;
  pointer: AvatarToolPointer;
  textContext?: string;
  label?: string;
}): AvatarToolStatePayload {
  const hasScreenPoint = Number.isFinite(pointer.screenX) && Number.isFinite(pointer.screenY);
  return {
    active: !!activeTool,
    toolId: activeTool?.id ?? null,
    variant,
    avatarRangeVariant,
    outsideRangeVariant,
    imageKind,
    withinAvatarRange,
    overCompactZone,
    insideHostWindow,
    cursorClientX: pointer.x,
    cursorClientY: pointer.y,
    ...(hasScreenPoint ? { cursorScreenX: pointer.screenX, cursorScreenY: pointer.screenY } : {}),
    tool: buildAvatarToolDescriptor(activeTool, label),
    ...(textContext ? { textContext } : {}),
    timestamp: Date.now(),
  };
}

export function getAvatarToolStatePayloadKey(payload: AvatarToolStatePayload): string {
  return JSON.stringify({ ...payload, timestamp: 0 });
}
