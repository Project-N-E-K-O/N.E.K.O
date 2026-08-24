import { describe, expect, expectTypeOf, it } from 'vitest';
import { AVAILABLE_COMPACT_AVATAR_TOOLS } from '../avatarTools';
import { avatarInteractionPayloadSchema as messageSchemaExport } from '../message-schema';
import {
  AVATAR_TOOL_REGISTRY,
  type AvatarToolInteractionProfile,
} from './catalog';
import {
  avatarInteractionPayloadSchema,
  avatarToolStatePayloadSchema,
  buildAvatarInteractionPayload,
  buildAvatarToolPointerStatePayload,
  buildAvatarToolSelectionStatePayload,
  getAvatarToolStatePayloadKey,
  resolveAvatarToolRoundResult,
  type AvatarInteractionPayload,
} from './protocol';

const BASE_PAYLOAD = {
  interactionId: 'interaction-1',
  target: 'avatar',
  pointer: { clientX: 10, clientY: 20 },
  timestamp: 100,
} as const;

function declaredFacts(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'round-choice') return null;
  if (profile.kind === 'progressive-release') {
    return {
      actions: profile.stages.map(stage => ({
        actionId: stage.actionId,
        intensities: stage.variant === profile.burst.variant
          ? [stage.intensity, profile.burst.belowThresholdIntensity, profile.burst.thresholdIntensity]
          : [stage.intensity],
      })),
      touchZones: [] as ReadonlyArray<string>,
      chanceField: null,
      chanceIntensity: null,
    };
  }
  if (profile.kind === 'press-release') {
    return {
      actions: [{
        actionId: profile.actionId,
        intensities: [profile.burst.normalIntensity, profile.burst.rapidIntensity],
      }],
      touchZones: profile.touchZones,
      chanceField: profile.chance.field,
      chanceIntensity: null,
    };
  }
  return {
    actions: [{
      actionId: profile.actionId,
      intensities: [
        profile.burst.normalIntensity,
        profile.burst.rapidIntensity,
        profile.burst.burstIntensity,
        profile.chance.intensity,
      ],
    }],
    touchZones: profile.touchZones,
    chanceField: profile.chance.field,
    chanceIntensity: profile.chance.intensity,
  };
}

describe('avatar interaction payload contract', () => {
  it('derives every accepted action, intensity, touch zone and chance field from registrations', () => {
    AVATAR_TOOL_REGISTRY.forEach(({ definition }) => {
      const facts = declaredFacts(definition.interaction);
      if (!facts) {
        return;
      }
      facts.actions.forEach(({ actionId, intensities }) => {
        const touchZone = facts.touchZones[0];
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId,
          ...(touchZone ? { touchZone } : {}),
        }).success).toBe(false);
        intensities.forEach((intensity) => {
          expect(avatarInteractionPayloadSchema.safeParse({
            ...BASE_PAYLOAD,
            toolId: definition.id,
            actionId,
            intensity,
            ...(touchZone ? { touchZone } : {}),
            ...(facts.chanceField && intensity === facts.chanceIntensity
              ? { [facts.chanceField]: true }
              : {}),
          }).success).toBe(true);
        });
      });
      const firstActionId = facts.actions[0].actionId;
      const firstIntensity = facts.actions[0].intensities[0];
      facts.touchZones.forEach((touchZone) => {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId: firstActionId,
          intensity: firstIntensity,
          touchZone,
        }).success).toBe(true);
      });
      if (facts.touchZones.length > 0) {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId: firstActionId,
          intensity: firstIntensity,
        }).success).toBe(false);
      }
      if (facts.chanceField) {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId: firstActionId,
          intensity: facts.chanceIntensity ?? firstIntensity,
          touchZone: facts.touchZones[0],
          [facts.chanceField]: true,
        }).success).toBe(true);
      }
    });
  });

  it.each([
    ['rock', 'rock', 'draw'],
    ['rock', 'scissors', 'user_win'],
    ['rock', 'paper', 'avatar_win'],
    ['scissors', 'rock', 'avatar_win'],
    ['scissors', 'scissors', 'draw'],
    ['scissors', 'paper', 'user_win'],
    ['paper', 'rock', 'user_win'],
    ['paper', 'scissors', 'avatar_win'],
    ['paper', 'paper', 'draw'],
  ] as const)('accepts the canonical rps fact tuple %s/%s/%s', (
    userGesture,
    avatarGesture,
    roundResult,
  ) => {
    expect(resolveAvatarToolRoundResult(userGesture, avatarGesture)).toBe(roundResult);
    expect(avatarInteractionPayloadSchema.safeParse({
      ...BASE_PAYLOAD,
      toolId: 'rps',
      userGesture,
      avatarGesture,
      roundResult,
    }).success).toBe(true);
  });

  it('rejects incomplete, contradictory, or generic-action rps payloads', () => {
    const valid = {
      ...BASE_PAYLOAD,
      toolId: 'rps',
      userGesture: 'rock',
      avatarGesture: 'scissors',
      roundResult: 'user_win',
    } as const;
    [
      { ...valid, roundResult: 'avatar_win' },
      { ...valid, userGesture: 'unknown' },
      { ...valid, avatarGesture: 'unknown' },
      { ...valid, userGesture: 'unknown', avatarGesture: 'unknown', roundResult: 'draw' },
      { ...valid, roundResult: 'unknown' },
      { ...valid, userGesture: undefined },
      { ...valid, actionId: 'play', intensity: 'normal' },
    ].forEach((payload) => {
      expect(avatarInteractionPayloadSchema.safeParse(payload).success).toBe(false);
    });
  });

  it('rejects undeclared actions, intensities and cross-tool facts', () => {
    const invalidPayloads = [
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'bonk', intensity: 'normal' },
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'offer', intensity: 'burst' },
      { ...BASE_PAYLOAD, toolId: 'fist', actionId: 'poke', intensity: 'burst', touchZone: 'head' },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', intensity: 'unknown', touchZone: 'head' },
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'offer', intensity: 'normal', touchZone: 'face' },
      { ...BASE_PAYLOAD, toolId: 'fist', actionId: 'poke', intensity: 'normal', touchZone: 'head', easterEgg: true },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', intensity: 'normal', touchZone: 'head', rewardDrop: true },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', intensity: 'normal', touchZone: 'head', easterEgg: true },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', intensity: 'easter_egg', touchZone: 'head' },
    ];
    invalidPayloads.forEach((payload) => {
      expect(avatarInteractionPayloadSchema.safeParse(payload).success).toBe(false);
    });
  });

  it('keeps message-schema as a re-export rather than a second contract', () => {
    expect(messageSchemaExport).toBe(avatarInteractionPayloadSchema);
  });

  it('preserves the registration-derived discriminated TypeScript union', () => {
    type LollipopPayload = Extract<AvatarInteractionPayload, { toolId: 'lollipop' }>;
    type LollipopOfferPayload = Extract<LollipopPayload, { actionId: 'offer' }>;
    type LollipopTapPayload = Extract<LollipopPayload, { actionId: 'tap_soft' }>;
    type FistPayload = Extract<AvatarInteractionPayload, { toolId: 'fist' }>;
    type HammerPayload = Extract<AvatarInteractionPayload, { toolId: 'hammer' }>;
    type HammerEasterPayload = Extract<HammerPayload, { intensity: 'easter_egg' }>;
    type HammerRegularPayload = Extract<HammerPayload, { easterEgg?: false }>;
    type RpsPayload = Extract<AvatarInteractionPayload, { toolId: 'rps' }>;

    expectTypeOf<LollipopPayload['actionId']>().toEqualTypeOf<'offer' | 'tease' | 'tap_soft'>();
    expectTypeOf<LollipopPayload['intensity']>().toEqualTypeOf<'normal' | 'rapid' | 'burst'>();
    expectTypeOf<LollipopOfferPayload['intensity']>().toEqualTypeOf<'normal'>();
    expectTypeOf<LollipopTapPayload['intensity']>().toEqualTypeOf<'rapid' | 'burst'>();
    expectTypeOf<FistPayload['actionId']>().toEqualTypeOf<'poke'>();
    expectTypeOf<FistPayload['intensity']>().toEqualTypeOf<'normal' | 'rapid'>();
    expectTypeOf<FistPayload['touchZone']>().toEqualTypeOf<'ear' | 'head' | 'face' | 'body'>();
    expectTypeOf<HammerPayload['actionId']>().toEqualTypeOf<'bonk'>();
    expectTypeOf<HammerPayload['intensity']>()
      .toEqualTypeOf<'normal' | 'rapid' | 'burst' | 'easter_egg'>();
    expectTypeOf<HammerPayload['touchZone']>().toEqualTypeOf<'ear' | 'head' | 'face' | 'body'>();
    expectTypeOf<HammerEasterPayload['easterEgg']>().toEqualTypeOf<true>();
    expectTypeOf<HammerRegularPayload['easterEgg']>().toEqualTypeOf<false | undefined>();
    expectTypeOf<RpsPayload['userGesture']>().toEqualTypeOf<'rock' | 'scissors' | 'paper'>();
    expectTypeOf<RpsPayload['avatarGesture']>().toEqualTypeOf<'rock' | 'scissors' | 'paper'>();
    expectTypeOf<RpsPayload['roundResult']>().toEqualTypeOf<'user_win' | 'avatar_win' | 'draw'>();
  });
});
describe('avatar tool payload builders', () => {
  it('keeps tool-specific facts on their owning payload', () => {
    const fist = buildAvatarInteractionPayload({
      toolId: 'fist', actionId: 'poke', intensity: 'normal', clientX: 1, clientY: 2,
      touchZone: 'head', rewardDrop: true,
    });
    const hammer = buildAvatarInteractionPayload({
      toolId: 'hammer', actionId: 'bonk', intensity: 'easter_egg', clientX: 3, clientY: 4,
      touchZone: 'face', easterEgg: true,
    });
    expect(fist).toEqual(expect.objectContaining({ touchZone: 'head', rewardDrop: true }));
    expect(fist).not.toHaveProperty('easterEgg');
    expect(hammer).toEqual(expect.objectContaining({ easterEgg: true }));
    expect(hammer).not.toHaveProperty('rewardDrop');
  });

  it('fails closed when runtime facts do not match the canonical tool payload', () => {
    const invalidCommit = {
      toolId: 'hammer',
      actionId: 'poke',
      intensity: 'normal',
      touchZone: 'head',
      clientX: 3,
      clientY: 4,
    } as unknown as Parameters<typeof buildAvatarInteractionPayload>[0];

    expect(() => buildAvatarInteractionPayload(invalidCommit)).toThrow();
  });

  it('builds one strict rps fact payload without generic action fields', () => {
    const payload = buildAvatarInteractionPayload({
      toolId: 'rps',
      userGesture: 'rock',
      avatarGesture: 'scissors',
      roundResult: 'user_win',
      clientX: 3,
      clientY: 4,
    });
    expect(payload).toEqual(expect.objectContaining({
      toolId: 'rps',
      userGesture: 'rock',
      avatarGesture: 'scissors',
      roundResult: 'user_win',
    }));
    expect(payload).not.toHaveProperty('actionId');
    expect(payload).not.toHaveProperty('intensity');
  });

  it('deduplicates state payloads independently of timestamps', () => {
    const base = { active: false, toolId: null, tool: null, timestamp: 1 } as const;
    expect(getAvatarToolStatePayloadKey(base)).toBe(getAvatarToolStatePayloadKey({ ...base, timestamp: 99 }));
  });

  it('keeps the single-window pointer state lightweight', () => {
    const tool = AVAILABLE_COMPACT_AVATAR_TOOLS.find(item => item.id === 'fist')!;
    const payload = buildAvatarToolPointerStatePayload({
      activeTool: tool,
      variant: 'primary',
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      imageKind: 'pointer',
      withinAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: false,
      pointer: { x: 10, y: 20 },
    });

    expect(payload).not.toHaveProperty('desktopContract');
  });

  it('publishes complete localized round-choice labels and rejects them for other profiles', () => {
    const rps = AVAILABLE_COMPACT_AVATAR_TOOLS.find(item => item.id === 'rps')!;
    const labels = { user_win: 'You win', avatar_win: 'Yui wins', draw: 'Draw' };
    const payload = buildAvatarToolSelectionStatePayload({
      activeTool: rps,
      roundChoiceResultLabels: labels,
    });
    expect(payload.roundChoiceResultLabels).toEqual(labels);
    expect(() => avatarToolStatePayloadSchema.parse({
      ...payload,
      toolId: 'hammer',
      desktopContract: undefined,
    })).toThrow(/require a round-choice tool/);
    expect(avatarToolStatePayloadSchema.parse({
      ...payload,
      roundChoiceResultLabels: { user_win: 'You win', draw: 'Draw' },
    }).roundChoiceResultLabels).toEqual({ user_win: 'You win', draw: 'Draw' });
  });

  it('builds a desktop handoff with descriptor facts but no live pointer state', () => {
    const tool = AVAILABLE_COMPACT_AVATAR_TOOLS.find(item => item.id === 'hammer')!;
    const payload = buildAvatarToolSelectionStatePayload({ activeTool: tool });

    expect(Object.keys(payload).sort()).toEqual([
      'active',
      'avatarRangeVariant',
      'desktopContract',
      'outsideRangeVariant',
      'timestamp',
      'toolId',
    ]);
    expect(payload).toEqual(expect.objectContaining({
      active: true,
      toolId: 'hammer',
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      desktopContract: expect.objectContaining({
        wireVersion: 1,
        definition: expect.objectContaining({ id: 'hammer' }),
      }),
    }));
    expect(payload).not.toHaveProperty('tool');
  });

  it('publishes an inactive desktop handoff without active variants or a page visual descriptor', () => {
    const payload = buildAvatarToolSelectionStatePayload({ activeTool: null });

    expect(Object.keys(payload).sort()).toEqual([
      'active',
      'desktopContract',
      'timestamp',
      'toolId',
    ]);
    expect(payload).toMatchObject({
      active: false,
      toolId: null,
      desktopContract: { wireVersion: 1, definition: null, runtimePolicy: null },
    });
    expect(payload).not.toHaveProperty('tool');
    expect(payload).not.toHaveProperty('avatarRangeVariant');
    expect(payload).not.toHaveProperty('outsideRangeVariant');
  });
});
