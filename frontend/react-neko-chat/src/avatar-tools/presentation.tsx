import type { CSSProperties, RefObject } from 'react';
import { createPortal } from 'react-dom';
import {
  resolveAvatarToolImagePaths,
  type AvatarToolId,
  type AvatarToolItem,
  type AvatarToolVariantId,
} from '../avatarTools';
import {
  AVATAR_TOOL_DEFINITIONS,
  withAvatarToolAssetVersion,
  type AvatarToolDefinition,
  type AvatarToolEffectRecipe,
  type AvatarToolSoundId,
  type AvatarToolVariantSource,
  type FixedParticleEffectRecipe,
  type HammerSwingEffectRecipe,
  type HammerSwingPhase,
  type RandomScatterEffectRecipe,
  type RoundRevealEffectRecipe,
  type RoundRevealPhase,
  type AvatarToolRoundChoiceGesture,
  type AvatarToolRoundResult,
} from './catalog';
import {
  BUILT_IN_AVATAR_TOOL_REGISTRY,
  type AvatarToolRegistrySnapshot,
} from './registry';
import { i18n } from '../i18n';
import {
  isPointWithinAvatarToolUi,
  isPointerOverAvatarToolUi,
} from './interaction';
import type { AvatarToolPointer } from './protocol';

// Local sound/effect lifecycle ----------------------------------------------

export type FixedParticleVisualEffect = {
  id: number;
  kind: 'fixed-particles';
  recipe: FixedParticleEffectRecipe;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  scale: number;
  delayMs: number;
};

export type RandomScatterVisualEffect = {
  id: number;
  kind: 'random-scatter';
  recipe: RandomScatterEffectRecipe;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  rotation: number;
  scale: number;
  delayMs: number;
};

export type AvatarToolTransientVisualEffect =
  | FixedParticleVisualEffect
  | RandomScatterVisualEffect;

export type HammerSwingEffectExecution = {
  kind: 'hammer-swing';
  recipe: HammerSwingEffectRecipe;
  interactionLock: 'effect-lifetime';
  mode: string | null;
};

export type ActiveHammerSwingEffectExecution = HammerSwingEffectExecution & {
  phase: HammerSwingPhase;
};

export type AvatarToolRoundRevealFacts = {
  userGesture: AvatarToolRoundChoiceGesture;
  userVariant: AvatarToolVariantId;
  avatarGesture: AvatarToolRoundChoiceGesture;
  avatarVariant: AvatarToolVariantId;
  roundResult: AvatarToolRoundResult;
};

export type ActiveRoundRevealEffectExecution = {
  kind: 'round-reveal';
  recipe: RoundRevealEffectRecipe;
  interactionLock: 'effect-lifetime';
  phase: RoundRevealPhase;
  round: AvatarToolRoundRevealFacts;
  userStart: { x: number; y: number };
  anchor: AvatarToolHeadAnchor;
  avatarName: string;
};

export type ActiveAvatarToolEffectExecution =
  | ActiveHammerSwingEffectExecution
  | ActiveRoundRevealEffectExecution;

export type AvatarToolEffectExecution =
  | {
    kind: 'fixed-particles';
    recipe: FixedParticleEffectRecipe;
    interactionLock: 'none';
    visuals: FixedParticleVisualEffect[];
  }
  | {
    kind: 'random-scatter';
    recipe: RandomScatterEffectRecipe;
    interactionLock: 'none';
    visuals: RandomScatterVisualEffect[];
  }
  | HammerSwingEffectExecution;

export type AvatarToolEffectExecutionContext = {
  clientX: number;
  clientY: number;
  nextId: () => number;
  random: () => number;
  mode?: string;
};

export function createAvatarToolEffectExecution(
  recipe: AvatarToolEffectRecipe,
  context: AvatarToolEffectExecutionContext,
): AvatarToolEffectExecution {
  if (recipe.kind === 'fixed-particles') {
    return {
      kind: recipe.kind,
      recipe,
      interactionLock: recipe.interactionLock,
      visuals: recipe.particles.map(particle => ({
        id: context.nextId(),
        kind: recipe.kind,
        recipe,
        x: context.clientX + particle.offsetX,
        y: context.clientY + particle.offsetY,
        driftX: particle.driftX,
        driftY: particle.driftY,
        scale: particle.scale,
        delayMs: particle.delayMs,
      })),
    };
  }
  if (recipe.kind === 'random-scatter') {
    return {
      kind: recipe.kind,
      recipe,
      interactionLock: recipe.interactionLock,
      visuals: Array.from({ length: recipe.count }, () => {
        const angle = ((recipe.angleDeg.min + context.random() * recipe.angleDeg.range) * Math.PI) / 180;
        const distance = recipe.distance.min + context.random() * recipe.distance.range;
        return {
          id: context.nextId(),
          kind: recipe.kind,
          recipe,
          x: Math.round(context.clientX + recipe.offsetX.min + context.random() * recipe.offsetX.range),
          y: Math.round(context.clientY + recipe.offsetY.min + context.random() * recipe.offsetY.range),
          driftX: Math.round(Math.cos(angle) * distance),
          driftY: Math.round(Math.sin(angle) * distance),
          rotation: Math.round(recipe.rotation.min + context.random() * recipe.rotation.range),
          scale: Number((recipe.scale.min + context.random() * recipe.scale.range).toFixed(2)),
          delayMs: Math.round(recipe.delayMs.min + context.random() * recipe.delayMs.range),
        };
      }),
    };
  }
  if (recipe.kind === 'round-reveal') {
    throw new Error('round-reveal requires confirmed round facts');
  }
  return {
    kind: recipe.kind,
    recipe,
    interactionLock: recipe.interactionLock,
    mode: context.mode ?? null,
  };
}
export function getAvatarToolTransientEffectLifetimeMs(effect: AvatarToolTransientVisualEffect): number {
  return effect.recipe.lifetimeMs + effect.delayMs;
}


export type AvatarToolDisposer = {
  isCurrent(): boolean;
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(timeoutId: number): void;
  add(dispose: () => void): () => void;
  destroy(): void;
};

export function createAvatarToolDisposer(
  generation: number,
  isGenerationCurrent: (generation: number) => boolean,
): AvatarToolDisposer {
  const cleanup = new Set<() => void>();
  const timeoutCleanup = new Map<number, () => void>();
  let destroyed = false;
  const isCurrent = () => !destroyed && isGenerationCurrent(generation);

  return {
    isCurrent,
    setTimeout(callback, delayMs) {
      const timeoutId = window.setTimeout(() => {
        cleanup.delete(cancel);
        timeoutCleanup.delete(timeoutId);
        if (isCurrent()) callback();
      }, delayMs);
      const cancel = () => {
        window.clearTimeout(timeoutId);
        cleanup.delete(cancel);
        timeoutCleanup.delete(timeoutId);
      };
      cleanup.add(cancel);
      timeoutCleanup.set(timeoutId, cancel);
      return timeoutId;
    },
    clearTimeout(timeoutId) {
      timeoutCleanup.get(timeoutId)?.();
    },
    add(dispose) {
      if (destroyed) {
        dispose();
        return () => {};
      }
      cleanup.add(dispose);
      return () => {
        cleanup.delete(dispose);
      };
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      cleanup.forEach(dispose => dispose());
      cleanup.clear();
    },
  };
}

function stopAudio(audio: HTMLAudioElement) {
  try { audio.pause(); } catch {}
  try { audio.removeAttribute('src'); } catch {}
  try { audio.load(); } catch {}
}

export function prewarmAvatarToolSounds(
  toolId: AvatarToolId,
  disposer: AvatarToolDisposer,
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
) {
  if (typeof Audio === 'undefined' || !disposer.isCurrent()) return;
  registry.getRegistration(toolId).definition.sounds.forEach((resource) => {
    if (!disposer.isCurrent()) return;
    let cleanup = () => {};
    try {
      const audio = new Audio(withAvatarToolAssetVersion(resource.src));
      audio.preload = 'auto';
      audio.volume = resource.volume;
      let unregister = () => {};
      const release = () => {
        audio.removeEventListener('error', release);
        unregister();
        stopAudio(audio);
      };
      cleanup = release;
      unregister = disposer.add(release);
      audio.addEventListener('error', release, { once: true });
      // load() starts fetching but is deliberately not awaited, so selecting a
      // tool and its first interaction remain synchronous.
      audio.load();
    } catch {
      // Audio is optional local feedback; a failed preload must not affect the session.
      cleanup();
    }
  });
}

const visualPreparation = new WeakMap<AvatarToolRegistrySnapshot, Map<AvatarToolId, Promise<void>>>();

export function prepareAvatarToolVisuals(
  toolId: AvatarToolId,
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
): Promise<void> {
  const registryPreparation = visualPreparation.get(registry) ?? new Map<AvatarToolId, Promise<void>>();
  visualPreparation.set(registry, registryPreparation);
  const existing = registryPreparation.get(toolId);
  if (existing) return existing;
  if (typeof Image === 'undefined') return Promise.resolve();

  const definition = registry.getRegistration(toolId).definition;
  const paths = new Set<string>();
  Object.values(definition.visual.variants).forEach((variant) => {
    paths.add(withAvatarToolAssetVersion(variant.iconImagePath));
    paths.add(withAvatarToolAssetVersion(variant.pointerImagePath));
  });
  definition.visual.frames?.forEach((frame) => {
    paths.add(withAvatarToolAssetVersion(frame.iconImagePath));
    paths.add(withAvatarToolAssetVersion(frame.pointerImagePath));
  });
  const preparation = Promise.all(Array.from(paths, imagePath => new Promise<void>((resolve) => {
    const image = new Image();
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      image.removeEventListener('load', decode);
      image.removeEventListener('error', finish);
      resolve();
    };
    const decode = () => {
      if (typeof image.decode !== 'function') {
        finish();
        return;
      }
      try {
        image.decode().catch(() => undefined).then(finish);
      } catch {
        finish();
      }
    };
    image.addEventListener('load', decode, { once: true });
    image.addEventListener('error', finish, { once: true });
    image.src = imagePath;
    if (image.complete) decode();
  }))).then(() => undefined);
  registryPreparation.set(toolId, preparation);
  return preparation;
}

export function playAvatarToolSound(sound: AvatarToolSoundId, disposer: AvatarToolDisposer): () => void;
export function playAvatarToolSound(
  toolId: AvatarToolId,
  sound: AvatarToolSoundId,
  disposer: AvatarToolDisposer,
  registry?: AvatarToolRegistrySnapshot,
): () => void;
export function playAvatarToolSound(
  toolOrSound: AvatarToolId | AvatarToolSoundId,
  soundOrDisposer: AvatarToolSoundId | AvatarToolDisposer,
  maybeDisposer?: AvatarToolDisposer,
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
) {
  const legacyCall = typeof soundOrDisposer !== 'string';
  const sound = legacyCall ? String(toolOrSound) : soundOrDisposer;
  const disposer = legacyCall ? soundOrDisposer : maybeDisposer;
  const toolId = legacyCall
    ? registry.definitions.find(definition => definition.sounds.some(resource => resource.id === sound))?.id
    : toolOrSound as AvatarToolId;
  if (!toolId || !disposer) return () => {};
  if (typeof Audio === 'undefined' || !disposer.isCurrent()) return () => {};
  let cleanup = () => {};
  try {
    const resource = registry.getSound(toolId, sound);
    const audio = new Audio(withAvatarToolAssetVersion(resource.src));
    audio.preload = 'auto';
    audio.volume = resource.volume;
    let unregister = () => {};
    let released = false;
    const release = () => {
      if (released) return false;
      released = true;
      audio.removeEventListener('ended', release);
      audio.removeEventListener('error', stop);
      unregister();
      return true;
    };
    const stop = () => {
      if (release()) stopAudio(audio);
    };
    cleanup = stop;
    unregister = disposer.add(stop);
    audio.addEventListener('ended', release, { once: true });
    audio.addEventListener('error', stop, { once: true });
    const pending = audio.play();
    pending?.catch?.(stop);
  } catch {
    // Local feedback must not block the interaction when audio is unavailable.
    cleanup();
  }
  return cleanup;
}


// Presentation state ---------------------------------------------------------

export type AvatarToolVariantState = Record<AvatarToolId, AvatarToolVariantId>;
export type AvatarToolImageFrameState = Record<AvatarToolId, number>;

export type AvatarToolPresentation = {
  activeTool: AvatarToolItem | null;
  avatarRangeVariant: AvatarToolVariantId;
  outsideRangeVariant: AvatarToolVariantId;
  effectiveVariant: AvatarToolVariantId;
  imageFrameIndex: number;
  withinAvatarRange: boolean;
  imageKind: 'pointer' | 'icon';
};

export function getAvatarTool(
  toolId: AvatarToolId | null,
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
): AvatarToolItem | null {
  return registry.items.find(item => item.id === toolId) ?? null;
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

export function createAvatarToolImageFrameState(
  definitions: ReadonlyArray<AvatarToolDefinition> = AVATAR_TOOL_DEFINITIONS,
): AvatarToolImageFrameState {
  const state = {} as AvatarToolImageFrameState;
  definitions.forEach((definition) => {
    state[definition.id] = 0;
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
  imageFrameIndices,
  overAvatarRange,
  overCompactZone,
  insideHostWindow,
  effectActive,
  registry = BUILT_IN_AVATAR_TOOL_REGISTRY,
}: {
  activeToolId: AvatarToolId | null;
  rangeVariants: AvatarToolVariantState;
  outsideVariants: AvatarToolVariantState;
  imageFrameIndices?: AvatarToolImageFrameState;
  overAvatarRange: boolean;
  overCompactZone: boolean;
  insideHostWindow: boolean;
  effectActive: boolean;
  registry?: AvatarToolRegistrySnapshot;
}): AvatarToolPresentation {
  const activeTool = getAvatarTool(activeToolId, registry);
  const avatarRangeVariant = activeToolId ? rangeVariants[activeToolId] : 'primary';
  const outsideRangeVariant = activeToolId ? outsideVariants[activeToolId] : 'primary';
  const withinAvatarRange = insideHostWindow && overAvatarRange && !overCompactZone;
  const imageFrameIndex = activeToolId ? imageFrameIndices?.[activeToolId] ?? 0 : 0;
  const visual = activeTool && activeToolId
    ? resolveAvatarToolVisualPresentation({
      definition: registry.getRegistration(activeToolId).definition,
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
    imageFrameIndex,
    withinAvatarRange,
    imageKind: visual.imageKind,
  };
}

export type AvatarToolImpactEffectVisualModel = ActiveHammerSwingEffectExecution & {
  pointerImagePath: string;
  idleImagePath: string;
  impactImagePath: string;
};

export type AvatarToolRoundRevealVisualModel = ActiveRoundRevealEffectExecution & {
  userImagePath: string;
  avatarImagePath: string;
  displayWidth: number;
  displayHeight: number;
  resultLabel: string;
};

export type AvatarToolOwnedEffectVisualModel =
  | AvatarToolImpactEffectVisualModel
  | AvatarToolRoundRevealVisualModel;

export type AvatarToolHeadAnchor = {
  x: number;
  y: number;
  coordinateSpace: 'viewport-css-pixel';
};

export type AvatarToolRoundChoiceAvatarGestureState = {
  variant: AvatarToolVariantId;
  anchor: AvatarToolHeadAnchor;
};

export type AvatarToolRoundChoiceAvatarGestureVisualModel =
  AvatarToolRoundChoiceAvatarGestureState & {
    imagePath: string;
    displayWidth: number;
    displayHeight: number;
  };

export type AvatarToolVisualModel = {
  activeTool: AvatarToolItem | null;
  activeToolId: AvatarToolId | null;
  effectiveVariant: AvatarToolVariantId;
  imageFrameIndex: number;
  avatarRangeVariant: AvatarToolVariantId;
  withinAvatarRange: boolean;
  overlayRef: RefObject<HTMLDivElement>;
  overlayActive: boolean;
  overlayCompact: boolean;
  overlayImagePath: string;
  overlayEffect: AvatarToolOwnedEffectVisualModel | null;
  roundChoiceAvatarGesture: AvatarToolRoundChoiceAvatarGestureVisualModel | null;
  transientEffects: AvatarToolTransientVisualEffect[];
  definition: AvatarToolDefinition | null;
};

export function buildAvatarToolVisualModel({
  activeTool,
  activeToolId,
  effectiveVariant,
  imageFrameIndex,
  avatarRangeVariant,
  withinAvatarRange,
  overlayRef,
  overlayActive,
  overlayCompact,
  overlayEffectExecution,
  roundChoiceAvatarGestureState,
  transientEffects,
  registry = BUILT_IN_AVATAR_TOOL_REGISTRY,
}: Omit<AvatarToolVisualModel, 'overlayImagePath' | 'overlayEffect' | 'roundChoiceAvatarGesture' | 'definition'> & {
  overlayEffectExecution: ActiveAvatarToolEffectExecution | null;
  roundChoiceAvatarGestureState: AvatarToolRoundChoiceAvatarGestureState | null;
  registry?: AvatarToolRegistrySnapshot;
}) : AvatarToolVisualModel {
  const activeDefinition = activeTool ? registry.getRegistration(activeTool.id).definition : null;
  const activeFrame = activeDefinition?.definitionVersion === 2
    ? activeDefinition.visual.frames?.[imageFrameIndex] ?? activeDefinition.visual.frames?.[0]
    : null;
  const activeImagePaths = activeTool
    ? activeFrame
      ? {
        iconImagePath: withAvatarToolAssetVersion(activeFrame.iconImagePath),
        pointerImagePath: withAvatarToolAssetVersion(activeFrame.pointerImagePath),
      }
      : resolveAvatarToolImagePaths(activeTool, effectiveVariant)
    : null;
  const overlayEffect: AvatarToolOwnedEffectVisualModel | null = activeTool && overlayEffectExecution
    ? overlayEffectExecution.kind === 'hammer-swing'
      ? {
        ...overlayEffectExecution,
        pointerImagePath: resolveAvatarToolImagePaths(activeTool, effectiveVariant).pointerImagePath,
        idleImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.recipe.variants.idle).iconImagePath,
        impactImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.recipe.variants.impact).iconImagePath,
      }
      : {
        ...overlayEffectExecution,
        userImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.round.userVariant).iconImagePath,
        avatarImagePath: resolveAvatarToolImagePaths(activeTool, overlayEffectExecution.round.avatarVariant).iconImagePath,
        displayWidth: registry.getRegistration(activeTool.id).definition.visual.inRange.displayWidth,
        displayHeight: registry.getRegistration(activeTool.id).definition.visual.inRange.displayHeight,
        resultLabel: getAvatarToolRoundResultLabel(
          overlayEffectExecution.round.roundResult,
          overlayEffectExecution.avatarName,
        ),
      }
    : null;
  const roundChoiceAvatarGesture = activeTool && roundChoiceAvatarGestureState ? {
    ...roundChoiceAvatarGestureState,
    imagePath: resolveAvatarToolImagePaths(activeTool, roundChoiceAvatarGestureState.variant).iconImagePath,
    displayWidth: registry.getRegistration(activeTool.id).definition.visual.inRange.displayWidth,
    displayHeight: registry.getRegistration(activeTool.id).definition.visual.inRange.displayHeight,
  } : null;
  return {
    activeTool,
    activeToolId,
    effectiveVariant,
    imageFrameIndex,
    avatarRangeVariant,
    withinAvatarRange,
    overlayRef,
    overlayActive,
    overlayCompact,
    overlayImagePath: activeTool && !overlayEffect
      ? (overlayCompact ? activeImagePaths?.pointerImagePath ?? '' : activeImagePaths?.iconImagePath ?? '')
      : '',
    overlayEffect,
    roundChoiceAvatarGesture,
    transientEffects,
    definition: activeTool ? registry.getRegistration(activeTool.id).definition : null,
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
  registry: AvatarToolRegistrySnapshot = BUILT_IN_AVATAR_TOOL_REGISTRY,
): string {
  return getAvatarToolOverlayTransformFromDefinition(
    registry.getRegistration(item.id).definition,
    compact,
    pointer,
  );
}

export function clampAvatarToolHeadGestureAnchor(
  anchor: AvatarToolHeadAnchor,
  displayWidth: number,
  displayHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): AvatarToolHeadAnchor {
  const gutter = 8;
  const headGap = 24;
  const minX = gutter + displayWidth / 2;
  const maxX = viewportWidth - gutter - displayWidth / 2;
  const minY = gutter + displayHeight + headGap;
  const maxY = viewportHeight - gutter;
  return {
    x: maxX >= minX ? Math.min(Math.max(anchor.x, minX), maxX) : viewportWidth / 2,
    y: maxY >= minY ? Math.min(Math.max(anchor.y, minY), maxY) : Math.max(gutter, viewportHeight - gutter),
    coordinateSpace: anchor.coordinateSpace,
  };
}

export function getAvatarToolRoundResultLabel(
  result: AvatarToolRoundResult,
  avatarName: string,
): string {
  if (result === 'user_win') {
    return i18n('chat.avatarToolRpsResultUserWin', 'You win');
  }
  if (result === 'draw') {
    return i18n('chat.avatarToolRpsResultDraw', 'Draw');
  }
  const name = avatarName.trim();
  return name
    ? i18n('chat.avatarToolRpsResultAvatarWin', '{{name}} wins', { name })
    : '';
}

export type AvatarToolRoundResultLabels = Pick<
  Record<AvatarToolRoundResult, string>,
  'user_win' | 'draw'
> & Partial<Pick<Record<AvatarToolRoundResult, string>, 'avatar_win'>>;

export function getAvatarToolRoundResultLabels(avatarName: string): AvatarToolRoundResultLabels {
  const name = avatarName.trim();
  return {
    user_win: getAvatarToolRoundResultLabel('user_win', name),
    ...(name ? { avatar_win: getAvatarToolRoundResultLabel('avatar_win', name) } : {}),
    draw: getAvatarToolRoundResultLabel('draw', name),
  };
}

export function clampAvatarToolRoundRevealAnchor(
  anchor: AvatarToolHeadAnchor,
  displayWidth: number,
  displayHeight: number,
  separationPx: number,
  resultOffsetY: number,
  viewportWidth: number,
  viewportHeight: number,
): AvatarToolHeadAnchor {
  const gutter = 12;
  const horizontalExtent = separationPx + displayWidth / 2;
  const minX = gutter + horizontalExtent;
  const maxX = viewportWidth - gutter - horizontalExtent;
  const handCenterOffsetY = 24 + displayHeight / 2;
  const minY = gutter + handCenterOffsetY;
  const maxY = viewportHeight - gutter - Math.max(24, resultOffsetY + 36) + handCenterOffsetY;
  return {
    x: maxX >= minX ? Math.min(Math.max(anchor.x, minX), maxX) : viewportWidth / 2,
    y: maxY >= minY ? Math.min(Math.max(anchor.y, minY), maxY) : Math.max(gutter, viewportHeight - gutter),
    coordinateSpace: anchor.coordinateSpace,
  };
}

// Stable React renderer ------------------------------------------------------

function AvatarToolTransientEffectVisual({ effect }: { effect: AvatarToolTransientVisualEffect }) {
  if (effect.kind === 'random-scatter') {
    return (
      <span
        className="avatar-tool-random-scatter-particle"
        aria-hidden="true"
        style={{
          position: 'fixed',
          left: `${effect.x}px`,
          top: `${effect.y}px`,
          '--drop-drift-x': `${effect.driftX}px`,
          '--drop-drift-y': `${effect.driftY}px`,
          '--drop-rotation': `${effect.rotation}deg`,
          '--drop-scale': effect.scale,
          '--drop-delay': `${effect.delayMs}ms`,
          animationDuration: `${effect.recipe.lifetimeMs}ms`,
        } as CSSProperties}
      >
        <img
          className="avatar-tool-random-scatter-particle-image"
          src={effect.recipe.assetPath}
          alt=""
          style={{ animationDuration: `${effect.recipe.lifetimeMs}ms` }}
        />
      </span>
    );
  }
  return (
    <span
      className="avatar-tool-fixed-particle"
      aria-hidden="true"
      style={{
        left: `${effect.x}px`,
        top: `${effect.y}px`,
        '--heart-drift-x': `${effect.driftX}px`,
        '--heart-drift-y': `${effect.driftY}px`,
        '--heart-sway-x': `${Math.max(8, Math.round(Math.abs(effect.driftX) * 0.32)) * (effect.driftX < 0 ? -1 : 1)}px`,
        '--heart-scale': effect.scale,
        '--heart-delay': `${effect.delayMs}ms`,
        animationDuration: `${effect.recipe.lifetimeMs}ms`,
      } as CSSProperties}
    >
      <span
        className="avatar-tool-fixed-particle-glyph"
        style={{ animationDuration: `${effect.recipe.lifetimeMs}ms` }}
      >
        {effect.recipe.glyph}
      </span>
    </span>
  );
}

export default function AvatarToolVisuals({ model }: { model: AvatarToolVisualModel }) {
  const activeVisual = model.definition?.visual ?? null;
  const activeVisualMode = activeVisual
    ? (model.overlayCompact ? activeVisual.pointer : activeVisual.inRange)
    : null;
  const toolVisual = model.activeTool && model.overlayActive && !model.overlayEffect ? (
    <div
      ref={model.overlayRef}
      className={`avatar-tool-visual-overlay avatar-tool-visual-overlay-${model.activeTool.id} is-visible${model.overlayCompact ? ' is-compact' : ''}`}
      aria-hidden="true"
      style={{
        '--avatar-tool-visual-overlay-scale': activeVisualMode?.scale ?? 1,
      } as CSSProperties}
    >
      <div className="avatar-tool-visual-overlay-stage" style={{ transformOrigin: '0 0' }}>
        <img
          className={`avatar-tool-visual-overlay-image avatar-tool-visual-overlay-image-${model.activeTool.id}`}
          src={model.overlayImagePath}
          alt=""
          style={{
            width: `${activeVisualMode?.displayWidth ?? 0}px`,
            height: `${activeVisualMode?.displayHeight ?? 0}px`,
          }}
        />
      </div>
    </div>
  ) : null;

  const overlayEffect = model.overlayEffect;
  const hammerEffect = overlayEffect?.kind === 'hammer-swing' ? overlayEffect : null;
  const overlayEffectDurationMs = hammerEffect?.recipe.timeline[
    hammerEffect.recipe.timeline.length - 1
  ]?.delayMs ?? 0;
  const overlayEffectEasterActive = !!hammerEffect
    && hammerEffect.mode === hammerEffect.recipe.easterEgg.mode;
  const impactEffectVisual = model.overlayActive && hammerEffect && activeVisualMode ? (
    <div
      ref={model.overlayRef}
      className={`avatar-tool-impact-effect is-visible${model.overlayCompact ? ' is-compact' : ''}${overlayEffectEasterActive ? ' is-easter-egg' : ''}`}
      aria-hidden="true"
      style={{
        '--avatar-tool-impact-effect-visual-scale': activeVisualMode.scale,
        '--avatar-tool-impact-effect-scale': overlayEffectEasterActive ? hammerEffect.recipe.easterEgg.scale : 1,
        '--avatar-tool-impact-effect-anchor-fix-x': `${overlayEffectEasterActive ? hammerEffect.recipe.easterEgg.anchorOffset.x : 0}px`,
        '--avatar-tool-impact-effect-anchor-fix-y': `${overlayEffectEasterActive ? hammerEffect.recipe.easterEgg.anchorOffset.y : 0}px`,
        '--avatar-tool-impact-origin-x': `${hammerEffect.recipe.impactRegistration.transformOrigin.x}px`,
        '--avatar-tool-impact-origin-y': `${hammerEffect.recipe.impactRegistration.transformOrigin.y}px`,
        '--avatar-tool-impact-translate-x': `${hammerEffect.recipe.impactRegistration.translate.x}px`,
        '--avatar-tool-impact-translate-y': `${hammerEffect.recipe.impactRegistration.translate.y}px`,
        '--avatar-tool-impact-rotation': `${hammerEffect.recipe.impactRegistration.rotationDeg}deg`,
        '--avatar-tool-impact-scale': hammerEffect.recipe.impactRegistration.scale,
      } as CSSProperties}
    >
      <div className="avatar-tool-impact-effect-stage" style={{ transformOrigin: '0 0' }}>
        {model.overlayCompact ? (
          <img
            className="avatar-tool-impact-effect-pointer-image"
            src={hammerEffect.pointerImagePath}
            alt=""
            style={{ width: `${activeVisualMode.displayWidth}px`, height: `${activeVisualMode.displayHeight}px` }}
          />
        ) : (
          <div
            className={`avatar-tool-impact-effect-visual${hammerEffect.phase !== 'idle' ? ' is-active' : ' is-idle'}${hammerEffect.phase === 'impact' ? ' is-impact' : ''}`}
            style={{
              width: `${activeVisualMode.displayWidth}px`,
              height: `${activeVisualMode.displayHeight}px`,
              transformOrigin: `${hammerEffect.recipe.transformOrigin.x}px ${hammerEffect.recipe.transformOrigin.y}px`,
              animationDuration: `${overlayEffectDurationMs}ms`,
            }}
          >
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-primary" src={hammerEffect.idleImagePath} alt="" />
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-secondary" src={hammerEffect.impactImagePath} alt="" />
          </div>
        )}
      </div>
    </div>
  ) : null;

  const roundReveal = overlayEffect?.kind === 'round-reveal' ? overlayEffect : null;
  const roundRevealAnchor = roundReveal ? clampAvatarToolRoundRevealAnchor(
    roundReveal.anchor,
    roundReveal.displayWidth,
    roundReveal.displayHeight,
    roundReveal.recipe.separationPx,
    roundReveal.recipe.resultOffsetY,
    typeof window === 'undefined' ? 0 : window.innerWidth,
    typeof window === 'undefined' ? 0 : window.innerHeight,
  ) : null;
  const roundRevealVisual = model.overlayActive && roundReveal && roundRevealAnchor ? (
    <div
      className={`avatar-tool-round-reveal is-${roundReveal.phase} is-${roundReveal.round.roundResult}`}
      aria-hidden="true"
      style={{
        left: `${roundRevealAnchor.x}px`,
        top: `${roundRevealAnchor.y - 24 - roundReveal.displayHeight / 2}px`,
        '--avatar-tool-rps-user-start-x': `${roundReveal.userStart.x - roundRevealAnchor.x}px`,
        '--avatar-tool-rps-user-start-y': `${roundReveal.userStart.y - (roundRevealAnchor.y - 24 - roundReveal.displayHeight / 2)}px`,
        '--avatar-tool-rps-separation': `${roundReveal.recipe.separationPx}px`,
        '--avatar-tool-rps-result-y': `${roundReveal.recipe.resultOffsetY}px`,
        '--avatar-tool-rps-approach-ms': `${roundReveal.recipe.timeline[1]?.delayMs ?? 520}ms`,
        '--avatar-tool-rps-impact-ms': `${(roundReveal.recipe.timeline[2]?.delayMs ?? 760) - (roundReveal.recipe.timeline[1]?.delayMs ?? 520)}ms`,
      } as CSSProperties}
    >
      <img
        className="avatar-tool-round-reveal-hand is-user"
        src={roundReveal.userImagePath}
        alt=""
        style={{ width: `${roundReveal.displayWidth}px`, height: `${roundReveal.displayHeight}px` }}
      />
      <img
        className="avatar-tool-round-reveal-hand is-avatar"
        src={roundReveal.avatarImagePath}
        alt=""
        style={{ width: `${roundReveal.displayWidth}px`, height: `${roundReveal.displayHeight}px` }}
      />
      <span className="avatar-tool-round-reveal-impact-ring" />
      {roundReveal.resultLabel ? (
        <span className="avatar-tool-round-reveal-result">{roundReveal.resultLabel}</span>
      ) : null}
    </div>
  ) : null;

  const roundChoiceAvatarGesture = model.roundChoiceAvatarGesture;
  const roundChoiceAvatarGestureAnchor = roundChoiceAvatarGesture
    ? clampAvatarToolHeadGestureAnchor(
      roundChoiceAvatarGesture.anchor,
      roundChoiceAvatarGesture.displayWidth,
      roundChoiceAvatarGesture.displayHeight,
      typeof window === 'undefined' ? 0 : window.innerWidth,
      typeof window === 'undefined' ? 0 : window.innerHeight,
    )
    : null;
  const roundChoiceAvatarGestureVisual = roundChoiceAvatarGesture ? (
    <div
      className="avatar-tool-round-choice-avatar-gesture"
      aria-hidden="true"
      style={{
        left: `${roundChoiceAvatarGestureAnchor?.x ?? roundChoiceAvatarGesture.anchor.x}px`,
        top: `${roundChoiceAvatarGestureAnchor?.y ?? roundChoiceAvatarGesture.anchor.y}px`,
      }}
    >
      <img
        className="avatar-tool-round-choice-avatar-gesture-image"
        src={roundChoiceAvatarGesture.imagePath}
        alt=""
        style={{
          width: `${roundChoiceAvatarGesture.displayWidth}px`,
          height: `${roundChoiceAvatarGesture.displayHeight}px`,
        }}
      />
    </div>
  ) : null;

  const visuals = (
    <>
      {toolVisual}
      {impactEffectVisual}
      {roundRevealVisual}
      {roundChoiceAvatarGestureVisual}
    </>
  );

  return (
    <>
      {model.transientEffects.map(effect => (
        <AvatarToolTransientEffectVisual key={effect.id} effect={effect} />
      ))}
      {typeof document !== 'undefined' ? createPortal(visuals, document.body) : visuals}
    </>
  );
}
