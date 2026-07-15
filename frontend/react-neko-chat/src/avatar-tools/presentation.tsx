import type { CSSProperties, RefObject } from 'react';
import { createPortal } from 'react-dom';
import {
  AVAILABLE_AVATAR_TOOLS,
  resolveAvatarToolImagePaths,
  type AvatarToolId,
  type AvatarToolItem,
  type AvatarToolVariantId,
} from '../avatarTools';
import {
  AVATAR_TOOL_DEFINITIONS,
  getAvatarToolRegistration,
  getAvatarToolSoundResource,
  type AvatarToolDefinition,
  type AvatarToolDefinitionId,
  type AvatarToolEffectRecipe,
  type AvatarToolVariantSource,
  type FixedParticleEffectRecipe,
  type HammerSwingEffectRecipe,
  type HammerSwingPhase,
  type RandomScatterEffectRecipe,
} from './catalog';
import {
  isPointWithinAvatarToolUi,
  isPointerOverAvatarToolUi,
  type AvatarToolSound,
} from './interaction';
import type { AvatarToolPointer } from './protocol';

// Local sound/effect lifecycle ----------------------------------------------

export type FixedParticleVisualEffect = {
  id: number;
  kind: 'fixed-particles-v1';
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
  kind: 'random-scatter-v1';
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
  kind: 'hammer-swing-v1';
  recipe: HammerSwingEffectRecipe;
  interactionLock: 'effect-lifetime';
  mode: string | null;
};

export type ActiveHammerSwingEffectExecution = HammerSwingEffectExecution & {
  phase: HammerSwingPhase;
};

export type AvatarToolEffectExecution =
  | {
    kind: 'fixed-particles-v1';
    recipe: FixedParticleEffectRecipe;
    interactionLock: 'none';
    visuals: FixedParticleVisualEffect[];
  }
  | {
    kind: 'random-scatter-v1';
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
  if (recipe.kind === 'fixed-particles-v1') {
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
  if (recipe.kind === 'random-scatter-v1') {
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

export function prewarmAvatarToolSounds(toolId: AvatarToolDefinitionId, disposer: AvatarToolDisposer) {
  if (typeof Audio === 'undefined' || !disposer.isCurrent()) return;
  getAvatarToolRegistration(toolId).definition.sounds.forEach((resource) => {
    if (!disposer.isCurrent()) return;
    let cleanup = () => {};
    try {
      const audio = new Audio(resource.src);
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

export function playAvatarToolSound(sound: AvatarToolSound, disposer: AvatarToolDisposer) {
  if (typeof Audio === 'undefined' || !disposer.isCurrent()) return;
  let cleanup = () => {};
  try {
    const resource = getAvatarToolSoundResource(sound);
    const audio = new Audio(resource.src);
    audio.preload = 'auto';
    audio.volume = resource.volume;
    let unregister = () => {};
    const release = () => {
      audio.removeEventListener('ended', release);
      audio.removeEventListener('error', release);
      unregister();
    };
    const stop = () => {
      audio.removeEventListener('ended', release);
      audio.removeEventListener('error', release);
      unregister();
      stopAudio(audio);
    };
    cleanup = stop;
    unregister = disposer.add(stop);
    audio.addEventListener('ended', release, { once: true });
    audio.addEventListener('error', release, { once: true });
    const pending = audio.play();
    pending?.catch?.(release);
  } catch {
    // Local feedback must not block the interaction when audio is unavailable.
    cleanup();
  }
}


// Presentation state ---------------------------------------------------------

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

// Stable React renderer ------------------------------------------------------

function AvatarToolTransientEffectVisual({ effect }: { effect: AvatarToolTransientVisualEffect }) {
  if (effect.kind === 'random-scatter-v1') {
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
  const activeVisual = model.activeToolId
    ? getAvatarToolRegistration(model.activeToolId).definition.visual
    : null;
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
  const overlayEffectDurationMs = overlayEffect?.recipe.timeline[
    overlayEffect.recipe.timeline.length - 1
  ]?.delayMs ?? 0;
  const overlayEffectEasterActive = !!overlayEffect
    && overlayEffect.mode === overlayEffect.recipe.easterEgg.mode;
  const impactEffectVisual = model.overlayActive && overlayEffect && activeVisualMode ? (
    <div
      ref={model.overlayRef}
      className={`avatar-tool-impact-effect is-visible${model.overlayCompact ? ' is-compact' : ''}${overlayEffectEasterActive ? ' is-easter-egg' : ''}`}
      aria-hidden="true"
      style={{
        '--avatar-tool-impact-effect-visual-scale': activeVisualMode.scale,
        '--avatar-tool-impact-effect-scale': overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.scale : 1,
        '--avatar-tool-impact-effect-anchor-fix-x': `${overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.anchorOffset.x : 0}px`,
        '--avatar-tool-impact-effect-anchor-fix-y': `${overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.anchorOffset.y : 0}px`,
        '--avatar-tool-impact-origin-x': `${overlayEffect.recipe.impactRegistration.transformOrigin.x}px`,
        '--avatar-tool-impact-origin-y': `${overlayEffect.recipe.impactRegistration.transformOrigin.y}px`,
        '--avatar-tool-impact-translate-x': `${overlayEffect.recipe.impactRegistration.translate.x}px`,
        '--avatar-tool-impact-translate-y': `${overlayEffect.recipe.impactRegistration.translate.y}px`,
        '--avatar-tool-impact-rotation': `${overlayEffect.recipe.impactRegistration.rotationDeg}deg`,
        '--avatar-tool-impact-scale': overlayEffect.recipe.impactRegistration.scale,
      } as CSSProperties}
    >
      <div className="avatar-tool-impact-effect-stage" style={{ transformOrigin: '0 0' }}>
        {model.overlayCompact ? (
          <img
            className="avatar-tool-impact-effect-pointer-image"
            src={overlayEffect.pointerImagePath}
            alt=""
            style={{ width: `${activeVisualMode.displayWidth}px`, height: `${activeVisualMode.displayHeight}px` }}
          />
        ) : (
          <div
            className={`avatar-tool-impact-effect-visual${overlayEffect.phase !== 'idle' ? ' is-active' : ' is-idle'}${overlayEffect.phase === 'impact' ? ' is-impact' : ''}`}
            style={{
              width: `${activeVisualMode.displayWidth}px`,
              height: `${activeVisualMode.displayHeight}px`,
              transformOrigin: `${overlayEffect.recipe.transformOrigin.x}px ${overlayEffect.recipe.transformOrigin.y}px`,
              animationDuration: `${overlayEffectDurationMs}ms`,
            }}
          >
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-primary" src={overlayEffect.idleImagePath} alt="" />
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-secondary" src={overlayEffect.impactImagePath} alt="" />
          </div>
        )}
      </div>
    </div>
  ) : null;

  const visuals = (
    <>
      {toolVisual}
      {impactEffectVisual}
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
