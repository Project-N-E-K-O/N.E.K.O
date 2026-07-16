import { afterEach, describe, expect, it, vi } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import {
  AVATAR_TOOL_DEFINITIONS,
  FIST_REWARD_DROP_EFFECT_RECIPE,
  HAMMER_SWING_EFFECT_RECIPE,
  LOLLIPOP_HEART_EFFECT_RECIPE,
  getAvatarToolRegistration,
  type AvatarToolDefinition,
  type FixedParticleEffectRecipe,
} from './catalog';
import {
  createAvatarToolDisposer,
  createAvatarToolEffectExecution,
  createAvatarToolVariantState,
  deriveAvatarToolPresentation,
  getAvatarToolOverlayTransform,
  getAvatarToolOverlayTransformFromDefinition,
  playAvatarToolSound,
  prewarmAvatarToolSounds,
  resolveAvatarToolVisualPresentation,
} from './presentation';

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

  it('stops and unregisters audio when play rejects asynchronously', async () => {
    class RejectedPlayAudio extends AudioMock {
      override play = vi.fn(() => Promise.reject(new Error('play rejected')));
    }
    vi.stubGlobal('Audio', RejectedPlayAudio);
    const disposer = createAvatarToolDisposer(7, generation => generation === 7);

    playAvatarToolSound('coin-drop', disposer);
    await Promise.resolve();

    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
    expect(audioInstances[0]?.src).toBe('');
    expect(audioInstances[0]?.load).toHaveBeenCalledTimes(1);
    disposer.destroy();
    expect(audioInstances[0]?.pause).toHaveBeenCalledTimes(1);
  });
});


describe('avatar tool presentation', () => {
  it('initializes every catalogued tool without a hard-coded variant object', () => {
    expect(createAvatarToolVariantState()).toEqual({
      lollipop: 'primary',
      fist: 'primary',
      hammer: 'primary',
    });
  });

  it('derives the tool-specific effective variant and shared enlarged image kind', () => {
    const rangeVariants = {
      ...createAvatarToolVariantState(),
      lollipop: 'tertiary' as const,
      fist: 'secondary' as const,
      hammer: 'secondary' as const,
    };
    const outsideVariants = {
      ...createAvatarToolVariantState(),
      fist: 'tertiary' as const,
      hammer: 'secondary' as const,
    };

    expect(deriveAvatarToolPresentation({
      activeToolId: 'lollipop',
      rangeVariants,
      outsideVariants,
      overAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({ effectiveVariant: 'tertiary', imageKind: 'pointer' });

    expect(deriveAvatarToolPresentation({
      activeToolId: 'fist',
      rangeVariants,
      outsideVariants,
      overAvatarRange: true,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({
      effectiveVariant: 'secondary',
      withinAvatarRange: true,
      imageKind: 'icon',
    });

    expect(deriveAvatarToolPresentation({
      activeToolId: 'hammer',
      rangeVariants,
      outsideVariants,
      overAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: true,
    })).toMatchObject({ effectiveVariant: 'secondary', imageKind: 'icon' });
  });

  it('keeps compact UI overlap in pointer mode even while the pointer is over avatar bounds', () => {
    const variants = createAvatarToolVariantState();
    expect(deriveAvatarToolPresentation({
      activeToolId: 'fist',
      rangeVariants: variants,
      outsideVariants: variants,
      overAvatarRange: true,
      overCompactZone: true,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({ withinAvatarRange: false, imageKind: 'pointer' });
  });

  it('uses definition initial variants and presentation policy as runtime inputs', () => {
    const definitions = AVATAR_TOOL_DEFINITIONS.map(definition => definition.id === 'fist'
      ? {
        ...definition,
        visual: { ...definition.visual, initialVariant: 'tertiary' as const },
      }
      : definition);
    expect(createAvatarToolVariantState(definitions).fist).toBe('tertiary');

    const source = getAvatarToolRegistration('lollipop').definition;
    const withOutsidePolicy = {
      ...source,
      visual: {
        ...source.visual,
        presentation: {
          ...source.visual.presentation,
          outsideVariantSource: 'outside' as const,
        },
      },
    } satisfies AvatarToolDefinition;
    expect(resolveAvatarToolVisualPresentation({
      definition: withOutsidePolicy,
      rangeVariant: 'tertiary',
      outsideVariant: 'secondary',
      overAvatarRange: false,
      withinAvatarRange: false,
      effectActive: false,
    })).toEqual({ effectiveVariant: 'secondary', imageKind: 'pointer' });
  });
});

describe('avatar tool visual runtime geometry', () => {
  it('preserves the current pointer and in-range anchors for every tool', () => {
    const pointer = { x: 100, y: 100 };
    const transforms = Object.fromEntries(AVAILABLE_AVATAR_TOOLS.map(item => [item.id, {
      pointer: getAvatarToolOverlayTransform(item, true, pointer),
      inRange: getAvatarToolOverlayTransform(item, false, pointer),
    }]));

    expect(transforms).toEqual({
      lollipop: {
        pointer: 'translate3d(79.66px, 65.22px, 0)',
        inRange: 'translate3d(63.67px, 37.9px, 0)',
      },
      fist: {
        pointer: 'translate3d(78.16px, 74.24px, 0)',
        inRange: 'translate3d(61px, 54px, 0)',
      },
      hammer: {
        pointer: 'translate3d(74px, 71.92px, 0)',
        inRange: 'translate3d(50px, 46px, 0)',
      },
    });
  });

  it('uses the selected visual mode rendered anchor instead of tool-id branches', () => {
    const source = getAvatarToolRegistration('hammer').definition;
    const definition = {
      ...source,
      visual: {
        ...source.visual,
        pointer: {
          ...source.visual.pointer,
          renderedAnchor: { x: 1, y: 2, coordinateSpace: 'final-css-pixel' as const },
        },
        inRange: {
          ...source.visual.inRange,
          renderedAnchor: { x: 3, y: 4, coordinateSpace: 'final-css-pixel' as const },
        },
      },
    } satisfies AvatarToolDefinition;

    expect(getAvatarToolOverlayTransformFromDefinition(definition, true, { x: 100, y: 100 }))
      .toBe('translate3d(99px, 98px, 0)');
    expect(getAvatarToolOverlayTransformFromDefinition(definition, false, { x: 100, y: 100 }))
      .toBe('translate3d(97px, 96px, 0)');
  });

  it('subtracts a final CSS-pixel anchor exactly once without applying visual scale again', () => {
    const source = getAvatarToolRegistration('hammer').definition;
    expect(source.visual.pointer).toMatchObject({
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.52,
      renderedAnchor: { x: 26, y: 28.08, coordinateSpace: 'final-css-pixel' },
    });
    expect(getAvatarToolOverlayTransformFromDefinition(source, true, { x: 26, y: 28.08 }))
      .toBe('translate3d(0px, 0px, 0)');
  });
});
