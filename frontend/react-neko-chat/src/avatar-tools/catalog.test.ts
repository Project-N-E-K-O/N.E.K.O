import { describe, expect, it } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import {
  AVATAR_TOOL_DEFINITIONS,
  AVATAR_TOOL_REGISTRY,
  createAvatarToolSoundResourceIndex,
  getAvatarToolEffectRecipe,
  getAvatarToolRegistration,
  validateAvatarToolDefinition,
  type AvatarToolDefinition,
} from './catalog';

describe('avatar tool definitions', () => {
  it('registers exactly the three supported tools once with explicit desktop capability', () => {
    const ids = AVATAR_TOOL_DEFINITIONS.map(definition => definition.id);

    expect(ids).toEqual(['lollipop', 'fist', 'hammer']);
    expect(new Set(ids).size).toBe(ids.length);
    expect(AVATAR_TOOL_REGISTRY).toHaveLength(ids.length);
    AVATAR_TOOL_DEFINITIONS.forEach((definition) => {
      expect(definition.definitionVersion).toBe(1);
      expect(definition.capability).toEqual({
        desktopVisual: true,
        desktopInteraction: true,
      });
    });
  });

  it('projects canonical definitions into the shared UI catalog', () => {
    expect(AVAILABLE_AVATAR_TOOLS.map(tool => tool.id)).toEqual(
      AVATAR_TOOL_DEFINITIONS.map(definition => definition.id),
    );
    AVATAR_TOOL_DEFINITIONS.forEach((definition) => {
      const tool = AVAILABLE_AVATAR_TOOLS.find(candidate => candidate.id === definition.id);
      expect(tool).toMatchObject({
        id: definition.id,
        labelKey: definition.label.key,
        labelFallback: definition.label.fallback,
        iconImagePath: definition.visual.variants.primary.iconImagePath,
        pointerImagePath: definition.visual.variants.primary.pointerImagePath,
        pointerHotspotX: definition.visual.hotspotX,
        pointerHotspotY: definition.visual.hotspotY,
        pointerNaturalWidth: definition.visual.naturalWidth,
        pointerNaturalHeight: definition.visual.naturalHeight,
      });
    });
  });

  it('keeps NEKO visual geometry as the product source of truth', () => {
    expect(getAvatarToolRegistration('lollipop').definition.visual).toMatchObject({
      pointer: {
        displayWidth: 74,
        displayHeight: 108,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 0.56,
        renderedAnchor: { coordinateSpace: 'final-css-pixel' },
      },
      inRange: {
        displayWidth: 74,
        displayHeight: 108,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 1,
        renderedAnchor: { coordinateSpace: 'final-css-pixel' },
      },
    });
    expect(getAvatarToolRegistration('fist').definition.visual).toMatchObject({
      pointer: { displayWidth: 78, displayHeight: 80, scale: 0.56 },
      inRange: { displayWidth: 78, displayHeight: 80, scale: 1 },
    });
    expect(getAvatarToolRegistration('hammer').definition.visual).toMatchObject({
      pointer: { displayWidth: 100, displayHeight: 96, scale: 0.52 },
      inRange: { displayWidth: 136, displayHeight: 130, scale: 1 },
    });
  });

  it('keeps tool rule facts isolated in their owning definitions', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition.interaction;
    const fist = getAvatarToolRegistration('fist').definition.interaction;
    const hammer = getAvatarToolRegistration('hammer').definition.interaction;

    expect(lollipop).toMatchObject({
      kind: 'progressive-release-v1',
      burst: { windowMs: 1800, threshold: 4 },
      feedback: { sound: 'lollipop-bite', effect: 'hearts' },
    });
    expect(fist).toMatchObject({
      kind: 'press-release-v1',
      actionId: 'poke',
      burst: {
        windowMs: 1400,
        rapidThreshold: 4,
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
      },
      chance: { field: 'rewardDrop', probability: 0.25 },
      touchZone: 'release',
      touchZones: ['ear', 'head', 'face', 'body'],
    });
    expect(hammer).toMatchObject({
      kind: 'locked-impact-v1',
      actionId: 'bonk',
      burst: {
        windowMs: 3200,
        rapidThreshold: 2,
        burstThreshold: 3,
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
        burstIntensity: 'burst',
      },
      chance: { field: 'easterEgg', probability: 0.05, intensity: 'easter_egg' },
      outsideFeedback: { variant: 'secondary', resetAfterMs: 220 },
      touchZone: 'release',
      touchZones: ['ear', 'head', 'face', 'body'],
    });
    expect(lollipop).not.toHaveProperty('touchZone');
    expect(lollipop).not.toHaveProperty('chance');
    expect(fist).not.toHaveProperty('outsideFeedback');
  });
});

function asDefinition(value: unknown): AvatarToolDefinition {
  return value as AvatarToolDefinition;
}

describe('avatar tool definition validation', () => {
  it('rejects unsupported ids, incomplete variants and non-positive visual geometry', () => {
    const source = getAvatarToolRegistration('lollipop').definition;
    const unsupportedId = asDefinition({ ...source, id: 'unknown-tool' });
    const incompleteVariants = asDefinition({
      ...source,
      visual: {
        ...source.visual,
        variants: {
          primary: source.visual.variants.primary,
          secondary: source.visual.variants.secondary,
        },
      },
    });
    const invalidDimensions = asDefinition({
      ...source,
      visual: {
        ...source.visual,
        pointer: { ...source.visual.pointer, displayWidth: 0 },
      },
    });
    const invalidAnchorSpace = asDefinition({
      ...source,
      visual: {
        ...source.visual,
        pointer: {
          ...source.visual.pointer,
          renderedAnchor: {
            ...source.visual.pointer.renderedAnchor,
            coordinateSpace: 'pre-scale-css-pixel',
          },
        },
      },
    });

    expect(() => validateAvatarToolDefinition(unsupportedId)).toThrow(/id is unsupported/);
    expect(() => validateAvatarToolDefinition(incompleteVariants)).toThrow(/primary, secondary and tertiary/);
    expect(() => validateAvatarToolDefinition(invalidDimensions)).toThrow(/displayWidth must be positive/);
    expect(() => validateAvatarToolDefinition(invalidAnchorSpace)).toThrow(/final-css-pixel/);
  });

  it('rejects incomplete progressive stages and out-of-range chance probabilities', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    if (lollipop.interaction.kind !== 'progressive-release-v1') throw new Error('invalid fixture');
    const incompleteStages = asDefinition({
      ...lollipop,
      interaction: {
        ...lollipop.interaction,
        stages: [
          lollipop.interaction.stages[0],
          lollipop.interaction.stages[0],
          lollipop.interaction.stages[2],
        ],
      },
    });

    const fist = getAvatarToolRegistration('fist').definition;
    if (fist.interaction.kind !== 'press-release-v1') throw new Error('invalid fixture');
    const invalidProbability = asDefinition({
      ...fist,
      interaction: {
        ...fist.interaction,
        chance: { ...fist.interaction.chance, probability: 1.01 },
      },
    });

    expect(() => validateAvatarToolDefinition(incompleteStages)).toThrow(/every variant exactly once/);
    expect(() => validateAvatarToolDefinition(invalidProbability)).toThrow(/between 0 and 1/);
  });

  it('rejects identifiers, capabilities and thresholds that the desktop consumer cannot accept', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    if (lollipop.interaction.kind !== 'progressive-release-v1') throw new Error('invalid fixture');
    const cases: Array<[AvatarToolDefinition, RegExp]> = [
      [asDefinition({ ...lollipop, interaction: {
        ...lollipop.interaction,
        stages: lollipop.interaction.stages.map((stage, index) => (
          index === 0 ? { ...stage, actionId: 'Offer' } : stage
        )),
      } }), /actionId.*lowercase identifier/],
      [asDefinition({ ...lollipop, interaction: {
        ...lollipop.interaction,
        burst: { ...lollipop.interaction.burst, threshold: Number.MAX_SAFE_INTEGER + 1 },
      } }), /safe positive integer/],
      [asDefinition({ ...lollipop, capability: {
        desktopVisual: false, desktopInteraction: true,
      } }), /requires desktop visual/],
    ];
    cases.forEach(([definition, message]) => {
      expect(() => validateAvatarToolDefinition(definition)).toThrow(message);
    });
  });

  it('rejects desktop-unsafe effect recipe sizes at the producer boundary', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    const heartEffect = lollipop.effects[0];
    if (heartEffect?.kind !== 'fixed-particles-v1') throw new Error('invalid fixture');
    const fist = getAvatarToolRegistration('fist').definition;
    const scatterEffect = fist.effects[0];
    if (scatterEffect?.kind !== 'random-scatter-v1') throw new Error('invalid fixture');
    const cases: Array<[AvatarToolDefinition, RegExp]> = [
      [asDefinition({ ...lollipop, effects: [{
        ...heartEffect, glyph: 'x'.repeat(17),
      }] }), /glyph.*at most 16/],
      [asDefinition({ ...lollipop, effects: [{
        ...heartEffect,
        particles: Array.from({ length: 65 }, () => ({ ...heartEffect.particles[0] })),
      }] }), /particles.*at most 64/],
      [asDefinition({ ...fist, effects: [{
        ...scatterEffect, count: 65,
      }] }), /count.*at most 64/],
    ];
    cases.forEach(([definition, message]) => {
      expect(() => validateAvatarToolDefinition(definition)).toThrow(message);
    });
  });

  it('rejects profile literals and asset sources that cannot reach the desktop consumer', () => {
    const fist = getAvatarToolRegistration('fist').definition;
    const hammer = getAvatarToolRegistration('hammer').definition;
    if (fist.interaction.kind !== 'press-release-v1' || hammer.interaction.kind !== 'locked-impact-v1') {
      throw new Error('invalid fixture');
    }
    const scatter = fist.effects[0];
    if (scatter?.kind !== 'random-scatter-v1') throw new Error('invalid fixture');
    const cases: Array<[AvatarToolDefinition, RegExp]> = [
      [asDefinition({ ...fist, interaction: { ...fist.interaction, touchZone: 'press' as never } }), /touchZone.*release/],
      [asDefinition({ ...hammer, interaction: {
        ...hammer.interaction,
        chance: { ...hammer.interaction.chance, intensity: 'normal' as never },
      } }), /chance\.intensity.*easter_egg/],
      [asDefinition({ ...hammer, interaction: {
        ...hammer.interaction,
        burst: { ...hammer.interaction.burst, burstIntensity: 'easter_egg' },
      } }), /chance intensity.*exclusive/],
      [asDefinition({ ...fist, visual: {
        ...fist.visual,
        variants: { ...fist.visual.variants, primary: {
          ...fist.visual.variants.primary, iconImagePath: 'https://example.invalid/tool.png',
        } },
      } }), /versioned same-origin asset path/],
      [asDefinition({ ...fist, sounds: [{ ...fist.sounds[0], src: '../sound.mp3' }] }), /versioned same-origin asset path/],
      [asDefinition({ ...fist, effects: [{ ...scatter, assetPath: '/drop.png#fragment' }] }), /versioned same-origin asset path/],
    ];
    cases.forEach(([definition, message]) => {
      expect(() => validateAvatarToolDefinition(definition)).toThrow(message);
    });
  });

  it('lets each touch-aware tool declare its own supported zone subset', () => {
    const fist = getAvatarToolRegistration('fist').definition;
    if (fist.interaction.kind !== 'press-release-v1') throw new Error('invalid fixture');
    const withHeadOnly = asDefinition({
      ...fist,
      interaction: { ...fist.interaction, touchZones: ['head'] },
    });
    const withoutZones = asDefinition({
      ...fist,
      interaction: { ...fist.interaction, touchZones: [] },
    });
    const withDuplicateZones = asDefinition({
      ...fist,
      interaction: { ...fist.interaction, touchZones: ['head', 'head'] },
    });
    const withUnknownZone = asDefinition({
      ...fist,
      interaction: { ...fist.interaction, touchZones: ['tail'] },
    });

    expect(() => validateAvatarToolDefinition(withHeadOnly)).not.toThrow();
    expect(() => validateAvatarToolDefinition(withoutZones)).toThrow(/non-empty unique subset/);
    expect(() => validateAvatarToolDefinition(withDuplicateZones)).toThrow(/non-empty unique subset/);
    expect(() => validateAvatarToolDefinition(withUnknownZone)).toThrow(/non-empty unique subset/);
  });

  it('allows a tool-owned chance field without allowing reserved or oversized payload keys', () => {
    const fist = getAvatarToolRegistration('fist').definition;
    if (fist.interaction.kind !== 'press-release-v1') throw new Error('invalid fixture');
    const interaction = fist.interaction;
    const withFutureField = asDefinition({
      ...fist,
      interaction: {
        ...interaction,
        chance: { ...interaction.chance, field: 'bonusDrop' },
      },
    });
    const withOversizedField = asDefinition({
      ...fist,
      interaction: {
        ...interaction,
        chance: { ...interaction.chance, field: `a${'b'.repeat(64)}` },
      },
    });

    expect(() => validateAvatarToolDefinition(withFutureField)).not.toThrow();
    ['toolId', 'clientX', 'clientY'].forEach((field) => {
      const withReservedField = asDefinition({
        ...fist,
        interaction: {
          ...interaction,
          chance: { ...interaction.chance, field },
        },
      });
      expect(() => validateAvatarToolDefinition(withReservedField)).toThrow(/reserved payload field/);
    });
    expect(() => validateAvatarToolDefinition(withOversizedField)).toThrow(/at most 64 characters/);
  });

  it('rejects missing feedback resources and incomplete hammer timelines', () => {
    const fist = getAvatarToolRegistration('fist').definition;
    const missingSound = asDefinition({ ...fist, sounds: [] });

    const hammer = getAvatarToolRegistration('hammer').definition;
    const hammerEffect = getAvatarToolEffectRecipe('hammer', 'hammer-swing');
    if (hammerEffect?.kind !== 'hammer-swing-v1') throw new Error('invalid fixture');
    const incompleteTimeline = asDefinition({
      ...hammer,
      effects: [{ ...hammerEffect, timeline: hammerEffect.timeline.slice(1) }],
    });

    expect(() => validateAvatarToolDefinition(missingSound)).toThrow(/sounds must contain/);
    expect(() => validateAvatarToolDefinition(incompleteTimeline)).toThrow(/windup, swing, impact, recover and idle/);
  });

  it('rejects duplicate sound ids inside one definition but reuses matching cross-definition resources', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    const shared = lollipop.sounds[0];
    if (!shared) throw new Error('invalid fixture');
    const duplicateInDefinition = asDefinition({ ...lollipop, sounds: [shared, { ...shared }] });
    const matchingDefinition = asDefinition({ ...lollipop, sounds: [{ ...shared }] });
    const conflictingDefinition = asDefinition({
      ...lollipop,
      sounds: [{ ...shared, src: '/different.mp3' }],
    });

    expect(() => validateAvatarToolDefinition(duplicateInDefinition)).toThrow(/sound .* duplicated/);
    expect(() => validateAvatarToolDefinition(matchingDefinition)).not.toThrow();
    expect(createAvatarToolSoundResourceIndex([lollipop, matchingDefinition]).size).toBe(1);
    expect(() => createAvatarToolSoundResourceIndex([lollipop, conflictingDefinition]))
      .toThrow(/Conflicting avatar tool sound resource/);
  });
});
