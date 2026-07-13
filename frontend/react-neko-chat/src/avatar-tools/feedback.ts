import type {
  AvatarToolEffectRecipe,
  FixedParticleEffectRecipe,
  HammerSwingEffectRecipe,
  HammerSwingPhase,
  RandomScatterEffectRecipe,
  AvatarToolDefinitionId,
} from './catalog';
import { getAvatarToolRegistration, getAvatarToolSoundResource } from './catalog';
import type { AvatarToolSound } from './interactionEngine';

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
  readonly generation: number;
  isCurrent(): boolean;
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(timeoutId: number): void;
  requestAnimationFrame(callback: FrameRequestCallback): number;
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
    generation,
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
    requestAnimationFrame(callback) {
      const frameId = window.requestAnimationFrame((timestamp) => {
        cleanup.delete(cancel);
        if (isCurrent()) callback(timestamp);
      });
      const cancel = () => window.cancelAnimationFrame(frameId);
      cleanup.add(cancel);
      return frameId;
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
