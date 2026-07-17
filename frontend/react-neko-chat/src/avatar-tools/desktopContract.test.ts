import { afterEach, describe, expect, it } from 'vitest';
import { avatarToolStatePayloadSchema } from '../message-schema';
import type { AvatarToolContractDefinition, AvatarToolDefinition } from './catalog';
import {
  buildDesktopAvatarToolContract,
  projectDesktopAvatarToolContract,
} from './desktopContract';
import { desktopAvatarToolContractSchema } from './desktopContract';
import { AVATAR_TOOL_DEFINITIONS } from './catalog';

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function createSyntheticRoundChoiceDefinition(): AvatarToolContractDefinition {
  const source = AVATAR_TOOL_DEFINITIONS[0];
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
      anchors: { user: 'release-pointer', avatar: 'avatar-head' },
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
      choices: { primary: 'alpha', secondary: 'beta', tertiary: 'gamma' },
      beats: { alpha: 'gamma', beta: 'alpha', gamma: 'beta' },
      facts: {
        userChoiceField: 'userGesture',
        avatarChoiceField: 'avatarGesture',
        resultField: 'roundResult',
      },
      cycle: { outsideMs: 240, inRangeMs: 1200, avatarPreviewMs: 200 },
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

function withCapability(
  definition: AvatarToolDefinition,
  desktopVisual: boolean,
  desktopInteraction: boolean,
): AvatarToolDefinition {
  return {
    ...definition,
    capability: { desktopVisual, desktopInteraction },
  };
}

function collectContractAssetPaths(contract: ReturnType<typeof buildDesktopAvatarToolContract>): string[] {
  const definition = contract.definition;
  if (!definition) return [];
  const paths: string[] = [];
  if (definition.visual) {
    Object.values(definition.visual.variants).forEach((variant) => {
      paths.push(variant.iconImagePath, variant.pointerImagePath);
    });
  }
  definition.interaction?.sounds.forEach(sound => paths.push(sound.src));
  definition.interaction?.effects.forEach((effect) => {
    if (effect.kind === 'random-scatter') paths.push(effect.assetPath);
  });
  return paths;
}

afterEach(() => {
  delete window.__NEKO_REACT_CHAT_ASSET_VERSION__;
});

describe('desktop avatar tool contract', () => {
  it('projects a synthetic round contract on version 1 without registering a product', () => {
    window.__NEKO_REACT_CHAT_ASSET_VERSION__ = 'round contract';
    const contract = projectDesktopAvatarToolContract(createSyntheticRoundChoiceDefinition());

    expect(contract.wireVersion).toBe(1);
    expect(contract.definition?.definitionVersion).toBe(1);
    expect(contract.definition?.interaction?.profile).toEqual({
      kind: 'round-choice',
      actionId: 'play',
      intensity: 'normal',
      choices: { primary: 'alpha', secondary: 'beta', tertiary: 'gamma' },
      beats: { alpha: 'gamma', beta: 'alpha', gamma: 'beta' },
      facts: {
        userChoiceField: 'userGesture',
        avatarChoiceField: 'avatarGesture',
        resultField: 'roundResult',
      },
      cycle: { outsideMs: 240, inRangeMs: 1200, avatarPreviewMs: 200 },
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
    });
    expect(contract.definition?.interaction?.effects).toEqual([{
      id: 'round-reveal',
      kind: 'round-reveal',
      interactionLock: 'effect-lifetime',
      anchors: { user: 'release-pointer', avatar: 'avatar-head' },
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
    }]);
    expect(contract.definition?.interaction?.sounds.map(sound => sound.src)).toEqual([
      '/static/sounds/avatar-tools/test/confirm.mp3?v=round%20contract',
      '/static/sounds/avatar-tools/test/user-win.mp3?v=round%20contract',
      '/static/sounds/avatar-tools/test/other-result.mp3?v=round%20contract',
    ]);
    expect(AVATAR_TOOL_DEFINITIONS.map(definition => definition.id)).toEqual([
      'lollipop',
      'fist',
      'hammer',
    ]);
  });

  it('strictly consumes round discriminators, facts, relations, timing and labels', () => {
    const valid = projectDesktopAvatarToolContract(createSyntheticRoundChoiceDefinition());
    const cases = [
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.kind = 'future-profile';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].kind = 'future-effect';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.unexpected = true;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        delete value.definition.interaction.profile.cycle.avatarPreviewMs;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.facts.resultField = 'bonusRound';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.facts.userChoiceField = 'avatarGesture';
        value.definition.interaction.profile.facts.avatarChoiceField = 'userGesture';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.choices.tertiary = 'beta';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.beats.beta = 'gamma';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.cycle.outsideMs = 0;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.profile.cycle.outsideMs = 2_147_483_648;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].timeline[1].phase = 'idle';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].timeline[2].delayMs = 2_147_483_648;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.gestures.gamma.fallback = '';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.gestures.delta =
          value.definition.interaction.effects[0].labels.gestures.gamma;
        delete value.definition.interaction.effects[0].labels.gestures.gamma;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.results.draw.key = 'x'.repeat(257);
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        delete value.definition.interaction.effects[0].labels.announcement;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        delete value.definition.interaction.effects[0].labels.announcement.fallback;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.announcement.unexpected = true;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.announcement.fallback = '   ';
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.effects[0].labels.announcement.key = 'x'.repeat(257);
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        delete value.definition.interaction.effects[0].layout.resultOffset;
        return value;
      })(),
      (() => {
        const value = cloneJson(valid) as any;
        value.definition.interaction.sounds.push({
          id: 'unused-sound',
          src: '/static/sounds/avatar-tools/test/unused.mp3?v=0',
          volume: 1,
        });
        return value;
      })(),
    ];

    cases.forEach(value => {
      expect(() => desktopAvatarToolContractSchema.parse(value)).toThrow();
    });
  });

  it('keeps round resources exact while chance fields still reject round-reserved names', () => {
    const definition = createSyntheticRoundChoiceDefinition();
    expect(() => projectDesktopAvatarToolContract({
      ...definition,
      sounds: [
        ...definition.sounds,
        { id: 'unused-sound', src: '/static/sounds/avatar-tools/test/unused.mp3', volume: 1 },
      ],
    })).toThrow(/unreferenced sound/);

    const valid = buildDesktopAvatarToolContract('hammer');
    for (const field of ['userGesture', 'avatarGesture', 'roundResult']) {
      const reservedChanceField = cloneJson(valid);
      const profile = reservedChanceField.definition?.interaction?.profile;
      if (profile?.kind === 'locked-impact') profile.chance.field = field;
      expect(() => desktopAvatarToolContractSchema.parse(reservedChanceField)).toThrow();
    }
  });

  it('filters legacy source extras and rejects legacy profiles that reference round reveal', () => {
    const legacy = AVATAR_TOOL_DEFINITIONS[0];
    if (legacy.interaction.kind !== 'progressive-release') throw new Error('invalid fixture');
    const legacyEffect = legacy.effects[0];
    const withUnusedLegacyResources = {
      ...legacy,
      sounds: [
        ...legacy.sounds,
        { id: 'unused-sound', src: '/static/sounds/avatar-tools/test/unused.mp3', volume: 1 },
      ],
      effects: [
        ...legacy.effects,
        { ...legacyEffect, id: 'unused-effect' },
      ],
    } as const satisfies AvatarToolContractDefinition;
    const filtered = projectDesktopAvatarToolContract(withUnusedLegacyResources);
    expect(filtered.definition?.interaction?.sounds.map(sound => sound.id)).toEqual(['lollipop-bite']);
    expect(filtered.definition?.interaction?.effects.map(effect => effect.id)).toEqual(['lollipop-hearts']);

    const round = createSyntheticRoundChoiceDefinition();
    const legacyReferencingRoundReveal = {
      ...legacy,
      effects: [{ ...round.effects[0], id: legacy.interaction.feedback.effect }],
    } as const satisfies AvatarToolContractDefinition;
    expect(() => projectDesktopAvatarToolContract(legacyReferencingRoundReveal))
      .toThrow(/round-reveal effects require a round-choice profile/);

    const legacyContract = buildDesktopAvatarToolContract('lollipop');
    const roundContract = projectDesktopAvatarToolContract(round);
    const consumerMismatch = cloneJson(legacyContract) as any;
    consumerMismatch.definition.interaction.effects = [{
      ...cloneJson(roundContract.definition?.interaction?.effects[0]),
      id: legacy.interaction.feedback.effect,
    }];
    expect(() => desktopAvatarToolContractSchema.parse(consumerMismatch))
      .toThrow(/round-reveal effects require a round-choice profile/);
  });

  it('projects inactive and all three active definitions with strict JSON round trips', () => {
    const inactive = buildDesktopAvatarToolContract(null);
    expect(Object.keys(inactive).sort()).toEqual(['definition', 'runtimePolicy', 'wireVersion']);
    expect(inactive).toEqual({ wireVersion: 1, definition: null, runtimePolicy: null });

    AVATAR_TOOL_DEFINITIONS.forEach((source) => {
      const contract = buildDesktopAvatarToolContract(source.id);
      expect(desktopAvatarToolContractSchema.parse(JSON.parse(JSON.stringify(contract)))).toEqual(contract);
      expect(Object.keys(contract.definition ?? {}).sort()).toEqual([
        'capability',
        'definitionVersion',
        'id',
        'interaction',
        'visual',
      ]);
      expect(Object.keys(contract.definition?.visual ?? {}).sort()).toEqual([
        'hotspotX',
        'hotspotY',
        'inRange',
        'initialVariant',
        'naturalHeight',
        'naturalWidth',
        'pointer',
        'presentation',
        'variants',
      ]);
      expect(contract.definition?.interaction?.profile).not.toHaveProperty('burst.key');
      expect(JSON.stringify(contract)).not.toMatch(/menuOffset|menuScale|label|cursorClient|cursorScreen|withinAvatarRange/);
    });
  });

  it('carries model-side presentation facts without PC guesses', () => {
    const fist = buildDesktopAvatarToolContract('fist');
    expect(fist.definition?.visual?.inRange).toMatchObject({
      displayWidth: 78,
      displayHeight: 80,
      scale: 1,
    });
    expect(fist.definition?.interaction?.profile).toMatchObject({
      kind: 'press-release',
      burst: {
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
      },
      touchZones: ['ear', 'head', 'face', 'body'],
    });

    const hammer = buildDesktopAvatarToolContract('hammer');
    expect(hammer.definition?.interaction?.profile).toMatchObject({
      kind: 'locked-impact',
      burst: {
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
        burstIntensity: 'burst',
      },
      touchZones: ['ear', 'head', 'face', 'body'],
    });
    const effect = hammer.definition?.interaction?.effects[0];
    expect(effect).toMatchObject({
      kind: 'hammer-swing',
      anchor: { source: 'live-pointer', visualMode: 'inRange' },
      impactRegistration: {
        transformOrigin: { x: 80.19, y: 68 },
        translate: { x: 19.62, y: -9.01 },
        rotationDeg: 34.258,
        scale: 0.999333,
      },
      timeline: [
        { phase: 'windup', delayMs: 0 },
        { phase: 'swing', delayMs: 240 },
        { phase: 'impact', delayMs: 420 },
        { phase: 'recover', delayMs: 520 },
        { phase: 'idle', delayMs: 620 },
      ],
      easterEgg: { scale: 5, anchorOffset: { x: 322.11, y: 259.27 } },
    });
  });

  it('preserves a tool-specific touch-zone subset in the desktop contract', () => {
    const fist = cloneJson(AVATAR_TOOL_DEFINITIONS.find(definition => definition.id === 'fist'));
    if (!fist || fist.interaction.kind !== 'press-release') throw new Error('invalid fixture');
    fist.interaction.touchZones = ['head'];

    const contract = projectDesktopAvatarToolContract(fist);

    expect(contract.definition?.interaction?.profile).toMatchObject({ touchZones: ['head'] });
    expect(() => desktopAvatarToolContractSchema.parse(contract)).not.toThrow();
  });

  it('versions only declared asset paths exactly once and preserves referenced resources only', () => {
    window.__NEKO_REACT_CHAT_ASSET_VERSION__ = 'wire 1';
    AVATAR_TOOL_DEFINITIONS.forEach((source) => {
      const contract = buildDesktopAvatarToolContract(source.id);
      const interaction = contract.definition?.interaction;
      const assetPaths = collectContractAssetPaths(contract);
      expect(assetPaths.length).toBeGreaterThan(0);
      assetPaths.forEach((path) => {
        expect(path).toContain('v=wire%201');
        expect(path.match(/(?:\?|&)v=/g)).toHaveLength(1);
      });
      expect(interaction?.sounds.map(sound => sound.id).sort())
        .toEqual(source.sounds.map(sound => sound.id).sort());
      expect(interaction?.effects.map(effect => effect.id)).toEqual(source.effects.map(effect => effect.id));
      expect(interaction?.profile).not.toHaveProperty('key');
      expect(interaction?.profile).not.toHaveProperty('burst.key');
    });

    const stale = cloneJson(AVATAR_TOOL_DEFINITIONS[0]) as AvatarToolDefinition;
    stale.visual.variants.primary.iconImagePath = '/static/assets/avatar-tools/lollipop/primary-icon.png?v=stale';
    const replaced = projectDesktopAvatarToolContract(stale);
    expect(replaced.definition?.visual?.variants.primary.iconImagePath).toContain('v=wire%201');
    expect(replaced.definition?.visual?.variants.primary.iconImagePath).not.toContain('stale');
  });

  it('enforces the declared desktop capability matrix', () => {
    const source = AVATAR_TOOL_DEFINITIONS[0];
    const none = projectDesktopAvatarToolContract(withCapability(source, false, false));
    expect(none.definition).toMatchObject({ visual: null, interaction: null });
    expect(none.runtimePolicy).toBeNull();

    const visualOnly = projectDesktopAvatarToolContract(withCapability(source, true, false));
    expect(visualOnly.definition?.visual).not.toBeNull();
    expect(visualOnly.definition?.interaction).toBeNull();
    expect(visualOnly.runtimePolicy).not.toBeNull();

    const full = projectDesktopAvatarToolContract(withCapability(source, true, true));
    expect(full.definition?.visual).not.toBeNull();
    expect(full.definition?.interaction).not.toBeNull();
    expect(full.runtimePolicy).not.toBeNull();

    expect(() => projectDesktopAvatarToolContract(withCapability(source, false, true))).toThrow();
  });

  it('rejects unknown fields, versions, unsafe assets, duplicate IDs, missing references and oversized arrays', () => {
    const valid = buildDesktopAvatarToolContract('hammer');

    expect(() => desktopAvatarToolContractSchema.parse({ ...valid, wireVersion: 2 })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({
      ...valid,
      definition: { ...valid.definition, definitionVersion: 2 },
    })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({
      ...valid,
      runtimePolicy: { ...valid.runtimePolicy, policyVersion: 2 },
    })).toThrow();

    const round = projectDesktopAvatarToolContract(createSyntheticRoundChoiceDefinition());
    expect(() => desktopAvatarToolContractSchema.parse({ ...round, wireVersion: 2 })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({
      ...round,
      definition: { ...round.definition, definitionVersion: 2 },
    })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({
      ...round,
      runtimePolicy: { ...round.runtimePolicy, policyVersion: 2 },
    })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({ ...valid, unexpected: true })).toThrow();
    expect(() => desktopAvatarToolContractSchema.parse({
      ...valid,
      definition: { ...valid.definition, id: 'unknown-tool' },
    })).toThrow();

    for (const field of ['clientX', 'clientY']) {
      const reservedChanceField = cloneJson(valid);
      const profile = reservedChanceField.definition?.interaction?.profile;
      if (profile?.kind === 'locked-impact') profile.chance.field = field;
      expect(() => desktopAvatarToolContractSchema.parse(reservedChanceField)).toThrow();
    }

    const unknownPolicyField = cloneJson(valid) as typeof valid & {
      runtimePolicy: NonNullable<typeof valid.runtimePolicy> & { unexpected?: boolean };
    };
    if (unknownPolicyField.runtimePolicy) unknownPolicyField.runtimePolicy.unexpected = true;
    expect(() => desktopAvatarToolContractSchema.parse(unknownPolicyField)).toThrow();

    const unsafeAsset = cloneJson(valid);
    if (unsafeAsset.definition?.visual) {
      unsafeAsset.definition.visual.variants.primary.iconImagePath = 'https://example.invalid/tool.png';
    }
    expect(() => desktopAvatarToolContractSchema.parse(unsafeAsset)).toThrow();

    const missingVersion = cloneJson(valid);
    if (missingVersion.definition?.visual) {
      missingVersion.definition.visual.variants.primary.iconImagePath = '/static/tool.png';
    }
    expect(() => desktopAvatarToolContractSchema.parse(missingVersion)).toThrow();

    const emptyVersion = cloneJson(valid);
    if (emptyVersion.definition?.visual) {
      emptyVersion.definition.visual.variants.primary.iconImagePath = '/static/tool.png?v=';
    }
    expect(() => desktopAvatarToolContractSchema.parse(emptyVersion)).toThrow();

    const fragmentVersion = cloneJson(valid);
    if (fragmentVersion.definition?.visual) {
      fragmentVersion.definition.visual.variants.primary.iconImagePath = '/static/tool.png?v=1#stale';
    }
    expect(() => desktopAvatarToolContractSchema.parse(fragmentVersion)).toThrow();

    const duplicateSound = cloneJson(valid);
    const duplicateInteraction = duplicateSound.definition?.interaction;
    if (duplicateInteraction) duplicateInteraction.sounds.push(cloneJson(duplicateInteraction.sounds[0]));
    expect(() => desktopAvatarToolContractSchema.parse(duplicateSound)).toThrow();

    const missingSound = cloneJson(valid);
    if (missingSound.definition?.interaction) missingSound.definition.interaction.sounds = [];
    expect(() => desktopAvatarToolContractSchema.parse(missingSound)).toThrow();

    const oversizedEffects = cloneJson(valid);
    const oversizedInteraction = oversizedEffects.definition?.interaction;
    if (oversizedInteraction) {
      oversizedInteraction.effects = Array.from({ length: 17 }, (_, index) => ({
        ...cloneJson(oversizedInteraction.effects[0]),
        id: `effect-${index}`,
      }));
    }
    expect(() => desktopAvatarToolContractSchema.parse(oversizedEffects)).toThrow();

    const unsafeThreshold = cloneJson(valid);
    const unsafeProfile = unsafeThreshold.definition?.interaction?.profile;
    if (unsafeProfile?.kind === 'locked-impact') {
      unsafeProfile.burst.rapidThreshold = Number.MAX_SAFE_INTEGER + 1;
    }
    expect(() => desktopAvatarToolContractSchema.parse(unsafeThreshold)).toThrow();
  });

  it('keeps desktop contract states strict without breaking page visual state payloads', () => {
    const pageVisualState = {
      active: true,
      toolId: 'hammer',
      tool: { id: 'hammer', iconImagePath: '/hammer.png', pointerImagePath: '/hammer-cursor.png' },
      timestamp: 1,
    };
    expect(() => avatarToolStatePayloadSchema.parse(pageVisualState)).not.toThrow();

    const activeDesktopState = {
      active: true,
      toolId: 'hammer',
      desktopContract: buildDesktopAvatarToolContract('hammer'),
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      timestamp: 2,
    };
    expect(() => avatarToolStatePayloadSchema.parse(activeDesktopState)).not.toThrow();
    expect(() => avatarToolStatePayloadSchema.parse({
      ...activeDesktopState,
      tool: pageVisualState.tool,
    })).toThrow();

    const inactiveDesktopState = {
      active: false,
      toolId: null,
      desktopContract: buildDesktopAvatarToolContract(null),
      timestamp: 3,
    };
    expect(() => avatarToolStatePayloadSchema.parse(inactiveDesktopState)).not.toThrow();

    expect(() => avatarToolStatePayloadSchema.parse({ ...pageVisualState, desktopContract: null })).toThrow();
    expect(() => avatarToolStatePayloadSchema.parse({
      ...activeDesktopState,
      desktopContract: { wireVersion: 2, definition: null, runtimePolicy: null },
    })).toThrow();

    const mismatch = {
      ...activeDesktopState,
      desktopContract: buildDesktopAvatarToolContract('fist'),
    };
    expect(() => avatarToolStatePayloadSchema.parse(mismatch)).toThrow();
  });
});
