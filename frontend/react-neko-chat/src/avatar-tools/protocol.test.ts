import { describe, expect, expectTypeOf, it } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import { avatarInteractionPayloadSchema as messageSchemaExport } from '../message-schema';
import {
  AVATAR_TOOL_REGISTRY,
  type AvatarToolInteractionProfile,
} from './catalog';
import {
  avatarInteractionPayloadSchema,
  buildAvatarInteractionPayload,
  buildAvatarToolDescriptorStatePayload,
  buildAvatarToolStatePayload,
  getAvatarToolStatePayloadKey,
  type AvatarInteractionPayload,
} from './protocol';

const BASE_PAYLOAD = {
  interactionId: 'interaction-1',
  target: 'avatar',
  pointer: { clientX: 10, clientY: 20 },
  timestamp: 100,
} as const;

function declaredFacts(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'progressive-release-v1') {
    return {
      actions: profile.stages.map(stage => ({
        actionId: stage.actionId,
        intensities: stage.variant === profile.burst.variant
          ? [stage.intensity, profile.burst.belowThresholdIntensity, profile.burst.thresholdIntensity]
          : [stage.intensity],
      })),
      touchZones: [] as ReadonlyArray<string>,
      chanceField: null,
    };
  }
  if (profile.kind === 'press-release-v1') {
    return {
      actions: [{
        actionId: profile.actionId,
        intensities: [profile.burst.normalIntensity, profile.burst.rapidIntensity],
      }],
      touchZones: profile.touchZones,
      chanceField: profile.chance.field,
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
  };
}

describe('avatar interaction payload contract', () => {
  it('accepts the canonical payload shape for all three tools', () => {
    expect(avatarInteractionPayloadSchema.parse({
      ...BASE_PAYLOAD,
      toolId: 'lollipop',
      actionId: 'tap_soft',
      intensity: 'burst',
    })).toMatchObject({ toolId: 'lollipop', actionId: 'tap_soft', intensity: 'burst' });
    expect(avatarInteractionPayloadSchema.parse({
      ...BASE_PAYLOAD,
      toolId: 'fist',
      actionId: 'poke',
      intensity: 'rapid',
      touchZone: 'face',
      rewardDrop: true,
    })).toMatchObject({ toolId: 'fist', rewardDrop: true });
    expect(avatarInteractionPayloadSchema.parse({
      ...BASE_PAYLOAD,
      toolId: 'hammer',
      actionId: 'bonk',
      intensity: 'easter_egg',
      touchZone: 'head',
      easterEgg: true,
    })).toMatchObject({ toolId: 'hammer', easterEgg: true });
  });

  it('derives every accepted action, intensity, touch zone and chance field from registrations', () => {
    AVATAR_TOOL_REGISTRY.forEach(({ definition }) => {
      const facts = declaredFacts(definition.interaction);
      facts.actions.forEach(({ actionId, intensities }) => {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId,
        }).success).toBe(true);
        intensities.forEach((intensity) => {
          expect(avatarInteractionPayloadSchema.safeParse({
            ...BASE_PAYLOAD,
            toolId: definition.id,
            actionId,
            intensity,
          }).success).toBe(true);
        });
      });
      const firstActionId = facts.actions[0].actionId;
      facts.touchZones.forEach((touchZone) => {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId: firstActionId,
          touchZone,
        }).success).toBe(true);
      });
      if (facts.chanceField) {
        expect(avatarInteractionPayloadSchema.safeParse({
          ...BASE_PAYLOAD,
          toolId: definition.id,
          actionId: firstActionId,
          [facts.chanceField]: true,
        }).success).toBe(true);
      }
    });
  });

  it('rejects undeclared actions, intensities and cross-tool facts', () => {
    const invalidPayloads = [
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'bonk' },
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'offer', intensity: 'burst' },
      { ...BASE_PAYLOAD, toolId: 'fist', actionId: 'poke', intensity: 'burst' },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', intensity: 'unknown' },
      { ...BASE_PAYLOAD, toolId: 'lollipop', actionId: 'offer', touchZone: 'face' },
      { ...BASE_PAYLOAD, toolId: 'fist', actionId: 'poke', easterEgg: true },
      { ...BASE_PAYLOAD, toolId: 'hammer', actionId: 'bonk', rewardDrop: true },
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

    expectTypeOf<LollipopPayload['actionId']>().toEqualTypeOf<'offer' | 'tease' | 'tap_soft'>();
    expectTypeOf<LollipopPayload['intensity']>().toEqualTypeOf<'normal' | 'rapid' | 'burst' | undefined>();
    expectTypeOf<LollipopOfferPayload['intensity']>().toEqualTypeOf<'normal' | undefined>();
    expectTypeOf<LollipopTapPayload['intensity']>().toEqualTypeOf<'rapid' | 'burst' | undefined>();
    expectTypeOf<FistPayload['actionId']>().toEqualTypeOf<'poke'>();
    expectTypeOf<FistPayload['intensity']>().toEqualTypeOf<'normal' | 'rapid' | undefined>();
    expectTypeOf<HammerPayload['actionId']>().toEqualTypeOf<'bonk'>();
    expectTypeOf<HammerPayload['intensity']>()
      .toEqualTypeOf<'normal' | 'rapid' | 'burst' | 'easter_egg' | undefined>();
  });
});
describe('avatar tool payload builders', () => {
  it('keeps tool-specific facts on their owning payload', () => {
    const fist = buildAvatarInteractionPayload({
      toolId: 'fist', actionId: 'poke', clientX: 1, clientY: 2, touchZone: 'head', rewardDrop: true,
    });
    const hammer = buildAvatarInteractionPayload({
      toolId: 'hammer', actionId: 'bonk', clientX: 3, clientY: 4, easterEgg: true,
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
      clientX: 3,
      clientY: 4,
      rewardDrop: true,
    } as unknown as Parameters<typeof buildAvatarInteractionPayload>[0];

    expect(() => buildAvatarInteractionPayload(invalidCommit)).toThrow();
  });

  it('deduplicates state payloads independently of timestamps', () => {
    const base = { active: false, toolId: null, tool: null, timestamp: 1 } as const;
    expect(getAvatarToolStatePayloadKey(base)).toBe(getAvatarToolStatePayloadKey({ ...base, timestamp: 99 }));
  });

  it('keeps the single-window pointer state lightweight', () => {
    const tool = AVAILABLE_AVATAR_TOOLS.find(item => item.id === 'fist')!;
    const payload = buildAvatarToolStatePayload({
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

  it('builds a desktop handoff with descriptor facts but no live pointer state', () => {
    const tool = AVAILABLE_AVATAR_TOOLS.find(item => item.id === 'hammer')!;
    const payload = buildAvatarToolDescriptorStatePayload({ activeTool: tool });

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
    const payload = buildAvatarToolDescriptorStatePayload({ activeTool: null });

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
