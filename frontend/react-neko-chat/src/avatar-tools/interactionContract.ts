import { z } from 'zod';
import {
  AVATAR_TOOL_REGISTRY,
  type AvatarToolDefinition,
  type AvatarToolInteractionProfile,
} from './catalog';

type RegistryRegistration = typeof AVATAR_TOOL_REGISTRY[number];
type RegistryDefinition = RegistryRegistration['definition'];

type ProgressiveStageFacts<
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'progressive-release-v1' }>,
  Stage = Profile['stages'][number],
> = Stage extends {
  variant: infer Variant extends string;
  actionId: infer ActionId extends string;
  intensity: infer StageIntensity extends string;
}
  ? {
    actionId: ActionId;
    intensity?: StageIntensity | (
      Variant extends Profile['burst']['variant']
        ? Profile['burst']['belowThresholdIntensity'] | Profile['burst']['thresholdIntensity']
        : never
    );
  }
  : never;

type SingleActionIntensityFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends {
      kind: 'press-release-v1';
      burst: {
        normalIntensity: infer NormalIntensity extends string;
        rapidIntensity: infer RapidIntensity extends string;
      };
    }
      ? NormalIntensity | RapidIntensity
      : Profile extends {
        kind: 'locked-impact-v1';
        burst: {
          normalIntensity: infer NormalIntensity extends string;
          rapidIntensity: infer RapidIntensity extends string;
          burstIntensity: infer BurstIntensity extends string;
        };
        chance: { intensity: infer ChanceIntensity extends string };
      }
        ? NormalIntensity | RapidIntensity | BurstIntensity | ChanceIntensity
        : never;

type TouchZoneFactsFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends { touchZones: ReadonlyArray<infer TouchZone extends string> }
    ? { touchZone?: TouchZone }
    : Record<never, never>;

type ChanceFactFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends { chance: { field: infer Field extends string } }
    ? { [Key in Field]?: boolean }
    : Record<never, never>;

type InteractionFactsFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'progressive-release-v1' }>
    ? ProgressiveStageFacts<Profile>
    : Profile extends { actionId: infer ActionId extends string }
      ? {
        actionId: ActionId;
        intensity?: SingleActionIntensityFor<Profile>;
      }
        & TouchZoneFactsFor<Profile>
        & ChanceFactFor<Profile>
      : never;

type AvatarInteractionPayloadBase = {
  interactionId: string;
  target: 'avatar';
  pointer: {
    clientX: number;
    clientY: number;
  };
  textContext?: string;
  timestamp: number;
};

type PayloadForDefinition<Definition extends RegistryDefinition> =
  Definition extends AvatarToolDefinition
    ? AvatarInteractionPayloadBase
      & { toolId: Definition['id'] }
      & InteractionFactsFor<Definition['interaction']>
    : never;

export type AvatarInteractionPayload =
  RegistryDefinition extends infer Definition
    ? Definition extends RegistryDefinition
      ? PayloadForDefinition<Definition>
      : never
    : never;

type RuntimeInteractionFacts = {
  actions: ReadonlyArray<{
    actionId: string;
    intensities: ReadonlyArray<string>;
  }>;
  touchZones: ReadonlyArray<string>;
  chanceField: string | null;
};

const avatarInteractionPayloadBaseShape = {
  interactionId: z.string().min(1),
  target: z.literal('avatar'),
  pointer: z.object({
    clientX: z.number().finite(),
    clientY: z.number().finite(),
  }).strict(),
  textContext: z.string().optional(),
  timestamp: z.number().finite(),
};

function deriveRuntimeInteractionFacts(profile: AvatarToolInteractionProfile): RuntimeInteractionFacts {
  if (profile.kind === 'progressive-release-v1') {
    const intensitiesByActionId = new Map<string, Set<string>>();
    profile.stages.forEach((stage) => {
      const intensities = intensitiesByActionId.get(stage.actionId) ?? new Set<string>();
      intensities.add(stage.intensity);
      if (stage.variant === profile.burst.variant) {
        intensities.add(profile.burst.belowThresholdIntensity);
        intensities.add(profile.burst.thresholdIntensity);
      }
      intensitiesByActionId.set(stage.actionId, intensities);
    });
    return {
      actions: [...intensitiesByActionId].map(([actionId, intensities]) => ({
        actionId,
        intensities: [...intensities],
      })),
      touchZones: [],
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

function oneOfDeclaredValues(values: ReadonlyArray<string>, field: string) {
  return z.string().refine(value => values.includes(value), {
    message: `${field} is not declared by the selected avatar tool`,
  });
}

function createRuntimePayloadSchema(definition: AvatarToolDefinition) {
  const facts = deriveRuntimeInteractionFacts(definition.interaction);
  const toolSpecificShape: z.ZodRawShape = {
    toolId: z.literal(definition.id),
    actionId: oneOfDeclaredValues(facts.actions.map(action => action.actionId), 'actionId'),
    intensity: z.string().optional(),
  };
  if (facts.touchZones.length > 0) {
    toolSpecificShape.touchZone = oneOfDeclaredValues(facts.touchZones, 'touchZone').optional();
  }
  if (facts.chanceField) {
    toolSpecificShape[facts.chanceField] = z.boolean().optional();
  }
  const shapeSchema = z.object({
    ...avatarInteractionPayloadBaseShape,
    ...toolSpecificShape,
  }).strict();
  return {
    shapeSchema,
    intensitiesByActionId: new Map(
      facts.actions.map(action => [action.actionId, new Set(action.intensities)]),
    ),
  };
}

const runtimeContractByToolId = new Map<string, ReturnType<typeof createRuntimePayloadSchema>>(
  AVATAR_TOOL_REGISTRY.map(({ definition }) => [
    definition.id,
    createRuntimePayloadSchema(definition),
  ]),
);

const toolIdProbeSchema = z.object({ toolId: z.string() }).passthrough();
const actionIntensityProbeSchema = z.object({
  actionId: z.string(),
  intensity: z.string().optional(),
}).passthrough();

function isAvatarInteractionPayload(value: unknown): value is AvatarInteractionPayload {
  const probe = toolIdProbeSchema.safeParse(value);
  if (!probe.success) return false;
  const contract = runtimeContractByToolId.get(probe.data.toolId);
  if (!contract || !contract.shapeSchema.safeParse(value).success) return false;
  const actionIntensity = actionIntensityProbeSchema.safeParse(value);
  if (!actionIntensity.success) return false;
  const allowedIntensities = contract.intensitiesByActionId.get(actionIntensity.data.actionId);
  if (!allowedIntensities) return false;
  return actionIntensity.data.intensity === undefined
    || allowedIntensities.has(actionIntensity.data.intensity);
}

export const avatarInteractionPayloadSchema = z.custom<AvatarInteractionPayload>(
  isAvatarInteractionPayload,
  'invalid avatar interaction payload',
);
