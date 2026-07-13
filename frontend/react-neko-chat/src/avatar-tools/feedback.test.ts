import { afterEach, describe, expect, it, vi } from 'vitest';
import type { FixedParticleEffectRecipe } from './catalog';
import {
  createAvatarToolDisposer,
  createAvatarToolEffectExecution,
  playAvatarToolSound,
  prewarmAvatarToolSounds,
} from './feedback';
import { FIST_REWARD_DROP_EFFECT_RECIPE } from './tools/fist';
import { HAMMER_SWING_EFFECT_RECIPE } from './tools/hammer';
import { LOLLIPOP_HEART_EFFECT_RECIPE } from './tools/lollipop';

describe('avatar tool effect recipes', () => {
  it('executes a custom recipe by kind without depending on its id', () => {
    const recipe = {
      id: 'fixture-sparkles',
      kind: 'fixed-particles-v1',
      interactionLock: 'none',
      lifetimeMs: 300,
      glyph: '+',
      particles: [
        { offsetX: 2, offsetY: -3, driftX: 4, driftY: -5, scale: 1.2, delayMs: 6 },
      ],
    } as const satisfies FixedParticleEffectRecipe;
    const execution = createAvatarToolEffectExecution(recipe, {
      clientX: 10,
      clientY: 20,
      nextId: () => 7,
      random: () => 0.5,
    });

    expect(execution).toMatchObject({
      kind: 'fixed-particles-v1',
      interactionLock: 'none',
      recipe: { id: 'fixture-sparkles' },
      visuals: [{ id: 7, x: 12, y: 17, driftX: 4, driftY: -5, scale: 1.2, delayMs: 6 }],
    });
  });

  it('drives lollipop particle creation from the declared fixed recipe', () => {
    let id = 0;
    const execution = createAvatarToolEffectExecution(LOLLIPOP_HEART_EFFECT_RECIPE, {
      clientX: 100,
      clientY: 120,
      nextId: () => ++id,
      random: () => 0.5,
    });
    expect(execution.kind).toBe('fixed-particles-v1');
    if (execution.kind !== 'fixed-particles-v1') throw new Error('invalid fixture');

    expect(execution.visuals).toHaveLength(LOLLIPOP_HEART_EFFECT_RECIPE.particles.length);
    expect(execution.visuals[0]).toMatchObject({
      id: 1,
      x: 88,
      y: 94,
      driftX: -26,
      driftY: -124,
      scale: 0.92,
      delayMs: 0,
    });
  });

  it('drives fist particle count and ranges from the declared scatter recipe', () => {
    let id = 0;
    const execution = createAvatarToolEffectExecution(FIST_REWARD_DROP_EFFECT_RECIPE, {
      clientX: 100,
      clientY: 100,
      nextId: () => ++id,
      random: () => 0.5,
    });
    expect(execution.kind).toBe('random-scatter-v1');
    if (execution.kind !== 'random-scatter-v1') throw new Error('invalid fixture');

    expect(execution.visuals).toHaveLength(FIST_REWARD_DROP_EFFECT_RECIPE.count);
    expect(execution.visuals[0]).toMatchObject({
      id: 1,
      x: 92,
      y: 76,
      driftX: 0,
      driftY: -97,
      rotation: 0,
      scale: 1.01,
      delayMs: 70,
    });
  });

  it('declares immediate windup while scheduling later hammer phases once', () => {
    expect(HAMMER_SWING_EFFECT_RECIPE).toMatchObject({
      id: 'hammer-swing',
      kind: 'hammer-swing-v1',
      interactionLock: 'effect-lifetime',
      anchor: {
        source: 'live-pointer',
        visualMode: 'inRange',
      },
      transformOrigin: { x: 60, y: 118 },
      impactRegistration: {
        transformOrigin: { x: 80.19, y: 68 },
        translate: { x: 19.62, y: -9.01 },
        rotationDeg: 34.258,
        scale: 0.999333,
      },
      variants: { idle: 'primary', impact: 'secondary' },
      easterEgg: {
        mode: 'easter-egg',
        scale: 5,
        anchorOffset: { x: 322.11, y: 259.27 },
      },
    });
    expect(HAMMER_SWING_EFFECT_RECIPE.timeline).toEqual([
      { phase: 'windup', delayMs: 0 },
      { phase: 'swing', delayMs: 240 },
      { phase: 'impact', delayMs: 420 },
      { phase: 'recover', delayMs: 520 },
      { phase: 'idle', delayMs: 620 },
    ]);
  });
});

describe('avatar tool lifecycle', () => {
  afterEach(() => vi.useRealTimers());

  it('makes destroy idempotent and blocks stale generation callbacks', () => {
    vi.useFakeTimers();
    let generation = 1;
    const callback = vi.fn();
    const disposer = createAvatarToolDisposer(1, value => value === generation);
    disposer.setTimeout(callback, 20);
    generation = 2;
    vi.advanceTimersByTime(20);
    expect(callback).not.toHaveBeenCalled();
    expect(() => {
      disposer.destroy();
      disposer.destroy();
    }).not.toThrow();
  });

  it('cancels a tracked timeout without leaving it eligible to run', () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    const disposer = createAvatarToolDisposer(1, value => value === 1);
    const timeoutId = disposer.setTimeout(callback, 20);

    disposer.clearTimeout(timeoutId);
    vi.advanceTimersByTime(20);

    expect(callback).not.toHaveBeenCalled();
    expect(() => disposer.destroy()).not.toThrow();
  });

  it('allows completed resources to unregister their destroy cleanup', () => {
    const cleanup = vi.fn();
    const disposer = createAvatarToolDisposer(1, value => value === 1);

    const unregister = disposer.add(cleanup);
    unregister();
    disposer.destroy();

    expect(cleanup).not.toHaveBeenCalled();
  });
});

const audioInstances: AudioMock[] = [];

class AudioMock extends EventTarget {
  preload = '';
  volume = 1;
  src: string;
  play = vi.fn(() => Promise.resolve());
  pause = vi.fn();
  load = vi.fn();

  constructor(src = '') {
    super();
    this.src = src;
    audioInstances.push(this);
  }

  removeAttribute(name: string) {
    if (name === 'src') this.src = '';
  }
}

describe('avatar tool sound lifecycle', () => {
  afterEach(() => {
    audioInstances.length = 0;
    vi.unstubAllGlobals();
  });

  it('stops and releases active audio when its tool session is destroyed', () => {
    vi.stubGlobal('Audio', AudioMock);
    const disposer = createAvatarToolDisposer(3, generation => generation === 3);

    playAvatarToolSound('hammer-small', disposer);
    expect(audioInstances).toHaveLength(1);
    expect(audioInstances[0]?.src).toBe('/static/sounds/avatar-tools/hammer-small.mp3');
    expect(audioInstances[0]?.volume).toBe(0.9);

    disposer.destroy();

    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
    expect(audioInstances[0]?.src).toBe('');
    expect(audioInstances[0]?.load).toHaveBeenCalledTimes(1);
  });

  it('prewarms all sounds for the selected tool and releases them with the session', () => {
    vi.stubGlobal('Audio', AudioMock);
    const disposer = createAvatarToolDisposer(4, generation => generation === 4);

    prewarmAvatarToolSounds('hammer', disposer);

    expect(audioInstances.map(audio => audio.src)).toEqual([
      '/static/sounds/avatar-tools/hammer-small.mp3',
      '/static/sounds/avatar-tools/hammer-big.mp3',
    ]);
    expect(audioInstances.every(audio => audio.preload === 'auto')).toBe(true);
    expect(audioInstances.every(audio => audio.volume === 0.9)).toBe(true);
    expect(audioInstances.every(audio => audio.load.mock.calls.length === 1)).toBe(true);
    expect(audioInstances.every(audio => audio.play.mock.calls.length === 0)).toBe(true);

    disposer.destroy();

    expect(audioInstances.every(audio => audio.pause.mock.calls.length === 1)).toBe(true);
    expect(audioInstances.every(audio => audio.src === '')).toBe(true);
  });

  it('does not let a preload failure escape into tool selection', () => {
    class BrokenAudio extends AudioMock {
      override load = vi.fn(() => { throw new Error('media unavailable'); });
    }
    vi.stubGlobal('Audio', BrokenAudio);
    const disposer = createAvatarToolDisposer(5, generation => generation === 5);

    expect(() => prewarmAvatarToolSounds('lollipop', disposer)).not.toThrow();
    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
    expect(audioInstances[0]?.src).toBe('');
    disposer.destroy();
    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
  });

  it('immediately releases audio when play throws synchronously', () => {
    class BrokenPlayAudio extends AudioMock {
      override play = vi.fn(() => { throw new Error('play unavailable'); });
    }
    vi.stubGlobal('Audio', BrokenPlayAudio);
    const disposer = createAvatarToolDisposer(6, generation => generation === 6);

    expect(() => playAvatarToolSound('coin-drop', disposer)).not.toThrow();
    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
    expect(audioInstances[0]?.src).toBe('');
    disposer.destroy();
    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
  });
});
