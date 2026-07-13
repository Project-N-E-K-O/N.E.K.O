import { describe, expect, expectTypeOf, it } from 'vitest';
import { avatarInteractionPayloadSchema as messageSchemaExport } from '../message-schema';
import {
  AVATAR_TOOL_REGISTRY,
  type AvatarToolInteractionProfile,
} from './catalog';
import {
  avatarInteractionPayloadSchema,
  type AvatarInteractionPayload,
} from './interactionContract';

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
