import { afterEach, describe, expect, it } from 'vitest';
import { avatarToolStatePayloadSchema } from '../message-schema';
import type { AvatarToolDefinition } from './catalog';
import {
  buildDesktopAvatarToolContract,
  projectDesktopAvatarToolContract,
} from './desktopContract';
import { desktopAvatarToolContractSchema } from './desktopContract';
import { AVATAR_TOOL_DEFINITIONS } from './catalog';

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
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
  it('projects inactive and all four active definitions with strict JSON round trips', () => {
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

    const rps = buildDesktopAvatarToolContract('rps');
    expect(rps.definition?.visual?.presentation).toMatchObject({
      inRangeVariantSource: 'range',
      outsideVariantSource: 'range',
    });
    expect(rps.definition?.interaction).toEqual(expect.objectContaining({
      profile: {
        kind: 'round-choice',
        choices: [
          { gesture: 'rock', variant: 'primary' },
          { gesture: 'scissors', variant: 'secondary' },
          { gesture: 'paper', variant: 'tertiary' },
        ],
        cycle: { outsideIntervalMs: 240, rangeIntervalMs: 720 },
        confirmation: { sound: 'rps-confirm' },
        reveal: {
          effect: 'rps-round-reveal',
          userWinSound: 'rps-user-win',
          otherResultSound: 'rps-other-result',
        },
      },
      sounds: [
        expect.objectContaining({ id: 'rps-confirm' }),
        expect.objectContaining({ id: 'rps-user-win' }),
        expect.objectContaining({ id: 'rps-other-result' }),
      ],
      effects: [expect.objectContaining({
        id: 'rps-round-reveal',
        kind: 'round-reveal',
        timeline: [
          { phase: 'approach', delayMs: 0 },
          { phase: 'impact', delayMs: 520 },
          { phase: 'result', delayMs: 760 },
          { phase: 'recover', delayMs: 3160 },
          { phase: 'idle', delayMs: 3340 },
        ],
      })],
    }));
  });

  it('preserves a tool-specific touch-zone subset in the desktop contract', () => {
    const fist = cloneJson(AVATAR_TOOL_DEFINITIONS.find(definition => definition.id === 'fist'));
    if (!fist || fist.interaction.kind !== 'press-release') throw new Error('invalid fixture');
    fist.interaction.touchZones = ['head'];

    const contract = projectDesktopAvatarToolContract(fist);

    expect(contract.definition?.interaction?.profile).toMatchObject({ touchZones: ['head'] });
    expect(() => desktopAvatarToolContractSchema.parse(contract)).not.toThrow();
  });

  it('rejects an empty effect list when the selected profile references an effect', () => {
    const contract = cloneJson(buildDesktopAvatarToolContract('rps'));
    if (!contract.definition?.interaction) throw new Error('invalid fixture');
    contract.definition.interaction.effects = [];

    const parsed = desktopAvatarToolContractSchema.safeParse(contract);

    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(parsed.error.issues).toEqual(expect.arrayContaining([
        expect.objectContaining({ message: 'missing referenced effect rps-round-reveal' }),
      ]));
    }
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
