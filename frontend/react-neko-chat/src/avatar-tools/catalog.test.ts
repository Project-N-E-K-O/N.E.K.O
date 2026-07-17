import { describe, expect, it } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import {
  AVATAR_TOOL_DEFINITIONS,
  AVATAR_TOOL_REGISTRY,
  createAvatarToolSoundResourceIndex,
  getAvatarToolEffectRecipe,
  getAvatarToolRegistration,
  validateAvatarToolDefinition,
  type AvatarToolContractDefinition,
  type AvatarToolDefinition,
  type AvatarToolEffectRecipe,
  type AvatarToolInteractionProfile,
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

  it('keeps contract-only discriminators out of the current runtime unions', () => {
    const runtimeProfileKinds: AvatarToolInteractionProfile['kind'][] = [
      'progressive-release',
      'press-release',
      'locked-impact',
    ];
    const runtimeEffectKinds: AvatarToolEffectRecipe['kind'][] = [
      'fixed-particles',
      'random-scatter',
      'hammer-swing',
    ];
    // Stage 1 validates/projects these contract kinds without activating runtime consumers.
    // @ts-expect-error round-choice remains contract-only until the profile runtime stage.
    const contractOnlyProfileKind: AvatarToolInteractionProfile['kind'] = 'round-choice';
    // @ts-expect-error round-reveal remains contract-only until the presentation stage.
    const contractOnlyEffectKind: AvatarToolEffectRecipe['kind'] = 'round-reveal';

    expect(runtimeProfileKinds).toEqual([
      'progressive-release',
      'press-release',
      'locked-impact',
    ]);
    expect(runtimeEffectKinds).toEqual([
      'fixed-particles',
      'random-scatter',
      'hammer-swing',
    ]);
    expect([contractOnlyProfileKind, contractOnlyEffectKind]).toEqual([
      'round-choice',
      'round-reveal',
    ]);
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
      kind: 'progressive-release',
      burst: { windowMs: 1800, threshold: 4 },
      feedback: { sound: 'lollipop-bite', effect: 'lollipop-hearts' },
    });
    expect(fist).toMatchObject({
      kind: 'press-release',
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
      kind: 'locked-impact',
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

function asContractDefinition(value: unknown): AvatarToolContractDefinition {
  return value as AvatarToolContractDefinition;
}

function createSyntheticRoundChoiceDefinition(): AvatarToolContractDefinition {
  const source = getAvatarToolRegistration('lollipop').definition;
  return {
    ...source,
    sounds: [
      { id: 'round-confirm', src: '/static/sounds/avatar-tools/test/confirm.mp3', volume: 0.9 },
      { id: 'round-user-win', src: '/static/sounds/avatar-tools/test/user-win.mp3', volume: 0.9 },
      { id: 'round-other-result', src: '/static/sounds/avatar-tools/test/other-result.mp3', volume: 0.9 },
    ],
    effects: [{
      id: 'round-reveal',
      kind: 'round-reveal',
      interactionLock: 'effect-lifetime',
      anchors: {
        user: 'release-pointer',
        avatar: 'avatar-head',
      },
      timeline: [
        { phase: 'reveal', delayMs: 0 },
        { phase: 'result', delayMs: 1000 },
        { phase: 'idle', delayMs: 2500 },
      ],
      labels: {
        gestures: {
          alpha: { key: 'chat.round.alpha', fallback: 'Alpha' },
          beta: { key: 'chat.round.beta', fallback: 'Beta' },
          gamma: { key: 'chat.round.gamma', fallback: 'Gamma' },
        },
        results: {
          user_win: { key: 'chat.round.userWin', fallback: 'You win' },
          avatar_win: { key: 'chat.round.avatarWin', fallback: '{{name}} wins' },
          draw: { key: 'chat.round.draw', fallback: 'Draw' },
        },
        announcement: { key: 'chat.round.announcement', fallback: 'Ready, set, reveal!' },
      },
      layout: {
        userOffset: { x: 0, y: 0 },
        avatarOffset: { x: 12, y: -8 },
        resultOffset: { x: 0, y: 24 },
      },
    }],
    interaction: {
      kind: 'round-choice',
      actionId: 'play',
      intensity: 'normal',
      choices: {
        primary: 'alpha',
        secondary: 'beta',
        tertiary: 'gamma',
      },
      beats: {
        alpha: 'gamma',
        beta: 'alpha',
        gamma: 'beta',
      },
      facts: {
        userChoiceField: 'userGesture',
        avatarChoiceField: 'avatarGesture',
        resultField: 'roundResult',
      },
      cycle: {
        outsideMs: 240,
        inRangeMs: 1200,
        avatarPreviewMs: 200,
      },
      random: { strategy: 'uniform' },
      presentation: { confirmation: 'render-owner-required' },
      feedback: {
        effect: 'round-reveal',
        confirmSound: 'round-confirm',
        resultSounds: {
          user_win: 'round-user-win',
          avatar_win: 'round-other-result',
          draw: 'round-other-result',
        },
      },
    },
  } as const satisfies AvatarToolContractDefinition;
}

describe('avatar tool definition validation', () => {
  it('accepts a synthetic round-choice definition without registering a product', () => {
    const definition = createSyntheticRoundChoiceDefinition();

    expect(() => validateAvatarToolDefinition(definition)).not.toThrow();
    expect(AVATAR_TOOL_DEFINITIONS.map(candidate => candidate.id)).toEqual([
      'lollipop',
      'fist',
      'hammer',
    ]);
  });

  it('strictly validates round choices, facts, timing and reveal labels', () => {
    const valid = createSyntheticRoundChoiceDefinition();
    const interaction = valid.interaction as unknown as Record<string, any>;
    const effect = valid.effects[0] as unknown as Record<string, any>;
    const cases: Array<[AvatarToolContractDefinition, RegExp]> = [
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          choices: { ...interaction.choices, tertiary: 'beta' },
        },
      }), /choices.*unique/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          beats: { alpha: 'gamma', beta: 'gamma', gamma: 'beta' },
        },
      }), /beats.*three-element cycle/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          facts: { ...interaction.facts, resultField: 'bonusRound' },
        },
      }), /facts.*canonical round field/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          facts: {
            ...interaction.facts,
            userChoiceField: 'avatarGesture',
            avatarChoiceField: 'userGesture',
          },
        },
      }), /facts\.userChoiceField.*userGesture/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          cycle: { ...interaction.cycle, inRangeMs: 0 },
        },
      }), /cycle\.inRangeMs.*positive/],
      [asContractDefinition({
        ...valid,
        interaction: { ...interaction, unexpected: true },
      }), /interaction contains unexpected field/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          cycle: {
            outsideMs: interaction.cycle.outsideMs,
            inRangeMs: interaction.cycle.inRangeMs,
          },
        },
      }), /cycle\.avatarPreviewMs is required/],
      [asContractDefinition({
        ...valid,
        interaction: {
          ...interaction,
          cycle: { ...interaction.cycle, outsideMs: 2_147_483_648 },
        },
      }), /cycle\.outsideMs.*2147483647ms/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          timeline: [
            { phase: 'reveal', delayMs: 0 },
            { phase: 'idle', delayMs: 1000 },
            { phase: 'result', delayMs: 2500 },
          ],
        }],
      }), /round reveal timeline.*reveal, result and idle/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          timeline: [
            { phase: 'reveal', delayMs: 0 },
            { phase: 'result', delayMs: 1000 },
            { phase: 'idle', delayMs: 2_147_483_648 },
          ],
        }],
      }), /timeline\[2\]\.delayMs.*2147483647ms/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            gestures: {
              alpha: effect.labels.gestures.alpha,
              beta: effect.labels.gestures.beta,
              delta: effect.labels.gestures.gamma,
            },
          },
        }],
      }), /gesture labels.*choices/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            results: {
              ...effect.labels.results,
              draw: { key: 'chat.round.draw', fallback: 'x'.repeat(257) },
            },
          },
        }],
      }), /fallback.*at most 256/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            results: {
              ...effect.labels.results,
              draw: { key: '   ', fallback: 'Draw' },
            },
          },
        }],
      }), /key.*non-blank/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            gestures: effect.labels.gestures,
            results: effect.labels.results,
          },
        }],
      }), /labels\.announcement is required/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            announcement: { key: 'chat.round.announcement' },
          },
        }],
      }), /labels\.announcement\.fallback is required/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            announcement: { ...effect.labels.announcement, unexpected: true },
          },
        }],
      }), /labels\.announcement contains unexpected field/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            announcement: { key: 'chat.round.announcement', fallback: '   ' },
          },
        }],
      }), /labels\.announcement\.fallback.*non-blank/],
      [asContractDefinition({
        ...valid,
        effects: [{
          ...effect,
          labels: {
            ...effect.labels,
            announcement: { key: 'x'.repeat(257), fallback: 'Play!' },
          },
        }],
      }), /labels\.announcement\.key.*at most 256/],
      [asContractDefinition({
        ...valid,
        effects: [{ ...effect, unexpected: true }],
      }), /unexpected field/],
    ];

    cases.forEach(([definition, message]) => {
      expect(() => validateAvatarToolDefinition(definition)).toThrow(message);
    });
  });

  it('requires an exact round feedback resource closure and rejects unknown discriminators', () => {
    const valid = createSyntheticRoundChoiceDefinition();
    const interaction = valid.interaction as unknown as Record<string, any>;
    const cases: Array<[AvatarToolContractDefinition, RegExp]> = [
      [asContractDefinition({ ...valid, sounds: valid.sounds.slice(1) }), /missing sound round-confirm/],
      [asContractDefinition({
        ...valid,
        sounds: [
          ...valid.sounds,
          { id: 'unused-sound', src: '/static/sounds/avatar-tools/test/unused.mp3', volume: 1 },
        ],
      }), /unreferenced sound unused-sound/],
      [asContractDefinition({
        ...valid,
        effects: [
          ...valid.effects,
          { ...valid.effects[0], id: 'unused-effect' },
        ],
      }), /unreferenced effect unused-effect/],
      [asContractDefinition({ ...valid, interaction: { ...interaction, kind: 'future-profile' } }), /interaction\.kind is unsupported/],
      [asContractDefinition({
        ...valid,
        effects: [{ ...valid.effects[0], kind: 'future-effect' }],
      }), /effects\[0\]\.kind is unsupported/],
    ];

    cases.forEach(([definition, message]) => {
      expect(() => validateAvatarToolDefinition(definition)).toThrow(message);
    });
  });

  it('keeps legacy extra-resource filtering while rejecting a referenced round reveal', () => {
    const legacy = getAvatarToolRegistration('lollipop').definition;
    if (legacy.interaction.kind !== 'progressive-release') throw new Error('invalid fixture');
    const legacyEffect = legacy.effects[0];
    const withUnusedLegacyResources = asDefinition({
      ...legacy,
      sounds: [
        ...legacy.sounds,
        { id: 'unused-sound', src: '/static/sounds/avatar-tools/test/unused.mp3', volume: 1 },
      ],
      effects: [
        ...legacy.effects,
        { ...legacyEffect, id: 'unused-effect' },
      ],
    });
    expect(() => validateAvatarToolDefinition(withUnusedLegacyResources)).not.toThrow();

    const round = createSyntheticRoundChoiceDefinition();
    const roundEffect = round.effects[0];
    const legacyReferencingRoundReveal = asContractDefinition({
      ...legacy,
      effects: [{ ...roundEffect, id: legacy.interaction.feedback.effect }],
    });
    expect(() => validateAvatarToolDefinition(legacyReferencingRoundReveal))
      .toThrow(/round-reveal effects require a round-choice profile/);
  });

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
    if (lollipop.interaction.kind !== 'progressive-release') throw new Error('invalid fixture');
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
    if (fist.interaction.kind !== 'press-release') throw new Error('invalid fixture');
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
    if (lollipop.interaction.kind !== 'progressive-release') throw new Error('invalid fixture');
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
    if (heartEffect?.kind !== 'fixed-particles') throw new Error('invalid fixture');
    const fist = getAvatarToolRegistration('fist').definition;
    const scatterEffect = fist.effects[0];
    if (scatterEffect?.kind !== 'random-scatter') throw new Error('invalid fixture');
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

  it('reports effect validation failures at the actual recipe index', () => {
    const lollipop = getAvatarToolRegistration('lollipop').definition;
    const heartEffect = lollipop.effects[0];
    if (heartEffect?.kind !== 'fixed-particles') throw new Error('invalid fixture');
    const invalidSecondEffect = asDefinition({
      ...lollipop,
      effects: [
        { ...heartEffect, id: 'decoy-hearts' },
        { ...heartEffect, glyph: 'x'.repeat(17) },
      ],
    });

    expect(() => validateAvatarToolDefinition(invalidSecondEffect))
      .toThrow(/effects\[1\]\.glyph must contain at most 16 characters/);
  });

  it('rejects profile literals and asset sources that cannot reach the desktop consumer', () => {
    const fist = getAvatarToolRegistration('fist').definition;
    const hammer = getAvatarToolRegistration('hammer').definition;
    if (fist.interaction.kind !== 'press-release' || hammer.interaction.kind !== 'locked-impact') {
      throw new Error('invalid fixture');
    }
    const scatter = fist.effects[0];
    if (scatter?.kind !== 'random-scatter') throw new Error('invalid fixture');
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
    if (fist.interaction.kind !== 'press-release') throw new Error('invalid fixture');
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
    if (fist.interaction.kind !== 'press-release') throw new Error('invalid fixture');
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
    ['toolId', 'clientX', 'clientY', 'userGesture', 'avatarGesture', 'roundResult'].forEach((field) => {
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
    if (hammerEffect?.kind !== 'hammer-swing') throw new Error('invalid fixture');
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
