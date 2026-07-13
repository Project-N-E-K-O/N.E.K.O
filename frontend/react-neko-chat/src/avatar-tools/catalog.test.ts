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

  it('keeps every resolved variant asset explicit and the definitions serializable', () => {
    AVATAR_TOOL_DEFINITIONS.forEach((definition) => {
      expect(Object.keys(definition.visual.variants)).toEqual(['primary', 'secondary', 'tertiary']);
      Object.values(definition.visual.variants).forEach((variant) => {
        expect(variant.iconImagePath).toMatch(/^\/static\/icons\//);
        expect(variant.pointerImagePath).toMatch(/^\/static\/icons\//);
        expect(Number.isFinite(variant.menuOffsetX)).toBe(true);
        expect(Number.isFinite(variant.menuOffsetY)).toBe(true);
      });
      expect(JSON.parse(JSON.stringify(definition))).toEqual(definition);
    });
  });

  it('projects canonical definitions into the shared UI catalog', () => {
    expect(AVAILABLE_AVATAR_TOOLS).toEqual([
      {
        id: 'lollipop',
        labelKey: 'chat.toolLollipop',
        labelFallback: '棒棒糖',
        iconImagePath: '/static/icons/chat_sugar1.png',
        iconImagePathAlt: '/static/icons/chat_sugar2.png',
        iconImagePathAlt2: '/static/icons/chat_sugar3.png',
        pointerImagePath: '/static/icons/chat_sugar1_cursor.png',
        pointerImagePathAlt: '/static/icons/chat_sugar2_cursor.png',
        menuIconScale: 1.18,
        pointerHotspotX: 27,
        pointerHotspotY: 46,
        pointerNaturalWidth: 55,
        pointerNaturalHeight: 80,
        pointerDisplayWidth: 74,
        pointerDisplayHeight: 108,
      },
      {
        id: 'fist',
        labelKey: 'chat.toolFist',
        labelFallback: '猫爪',
        iconImagePath: '/static/icons/cat_claw1.png',
        iconImagePathAlt: '/static/icons/cat_claw2.png',
        pointerImagePath: '/static/icons/cat_claw1_cursor.png',
        pointerImagePathAlt: '/static/icons/cat_claw2_cursor.png',
        pointerHotspotX: 39,
        pointerHotspotY: 46,
        pointerNaturalWidth: 78,
        pointerNaturalHeight: 80,
        pointerDisplayWidth: 78,
        pointerDisplayHeight: 80,
      },
      {
        id: 'hammer',
        labelKey: 'chat.toolHammer',
        labelFallback: '锤子',
        iconImagePath: '/static/icons/chat_hammer1.png',
        iconImagePathAlt: '/static/icons/chat_hammer2.png',
        pointerImagePath: '/static/icons/chat_hammer1_cursor.png',
        pointerImagePathAlt: '/static/icons/chat_hammer2_cursor.png',
        menuIconScale: 1.52,
        menuIconOffsetX: -8,
        menuIconOffsetY: 4,
        menuIconOffsetXAlt: 1,
        menuIconOffsetYAlt: -1,
        pointerHotspotX: 50,
        pointerHotspotY: 54,
        pointerNaturalWidth: 100,
        pointerNaturalHeight: 96,
        pointerDisplayWidth: 100,
        pointerDisplayHeight: 96,
      },
    ]);
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
    const withFutureField = asDefinition({
      ...fist,
      interaction: {
        ...fist.interaction,
        chance: { ...fist.interaction.chance, field: 'bonusDrop' },
      },
    });
    const withReservedField = asDefinition({
      ...fist,
      interaction: {
        ...fist.interaction,
        chance: { ...fist.interaction.chance, field: 'toolId' },
      },
    });
    const withOversizedField = asDefinition({
      ...fist,
      interaction: {
        ...fist.interaction,
        chance: { ...fist.interaction.chance, field: `a${'b'.repeat(64)}` },
      },
    });

    expect(() => validateAvatarToolDefinition(withFutureField)).not.toThrow();
    expect(() => validateAvatarToolDefinition(withReservedField)).toThrow(/reserved payload field/);
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

  it('reuses identical sound ids but rejects conflicting resources', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    const shared = lollipop.sounds[0];
    if (!shared) throw new Error('invalid fixture');
    const exactReuse = asDefinition({ ...lollipop, sounds: [shared, { ...shared }] });
    const conflictingReuse = asDefinition({
      ...lollipop,
      sounds: [shared, { ...shared, src: '/different.mp3' }],
    });

    expect(() => validateAvatarToolDefinition(exactReuse)).not.toThrow();
    expect(createAvatarToolSoundResourceIndex([lollipop, exactReuse]).size).toBe(1);
    expect(() => validateAvatarToolDefinition(conflictingReuse)).toThrow(/conflicts with another resource/);
    expect(() => createAvatarToolSoundResourceIndex([lollipop, conflictingReuse]))
      .toThrow(/Conflicting avatar tool sound resource/);
  });
});
