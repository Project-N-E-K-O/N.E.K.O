import { z } from 'zod';
import {
  AVATAR_TOOL_DEFINITION_IDS,
  AVATAR_TOOL_REGISTRY,
  AVATAR_TOOL_VARIANT_IDS,
  withAvatarToolAssetVersion,
  type AvatarToolDefinition,
  type AvatarToolDefinitionId,
  type AvatarToolInteractionProfile,
  type AvatarToolVariantId,
} from './catalog';
import {
  buildDesktopAvatarToolContract,
  desktopAvatarToolContractSchema,
} from './desktopContract';
import type { AvatarToolInteractionCommit } from './interaction';

// Host interaction protocol -------------------------------------------------

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


// Shared page/desktop state protocol ----------------------------------------

const avatarToolIdSchema = z.enum(AVATAR_TOOL_DEFINITION_IDS);
const avatarToolVariantIdSchema = z.enum(AVATAR_TOOL_VARIANT_IDS);
const avatarToolImageKindSchema = z.enum(['pointer', 'icon']);

const avatarToolDescriptorSchema = z.object({
  id: avatarToolIdSchema,
  label: z.string().optional(),
  iconImagePath: z.string().min(1),
  iconImagePathAlt: z.string().optional(),
  iconImagePathAlt2: z.string().optional(),
  pointerImagePath: z.string().min(1),
  pointerImagePathAlt: z.string().optional(),
  pointerImagePathAlt2: z.string().optional(),
  pointerHotspotX: z.number().finite().optional(),
  pointerHotspotY: z.number().finite().optional(),
  pointerNaturalWidth: z.number().finite().positive().optional(),
  pointerNaturalHeight: z.number().finite().positive().optional(),
  pointerDisplayWidth: z.number().finite().positive().optional(),
  pointerDisplayHeight: z.number().finite().positive().optional(),
  menuIconScale: z.number().finite().positive().optional(),
}).strict();

export const avatarToolStatePayloadSchema = z.object({
  active: z.boolean(),
  toolId: avatarToolIdSchema.nullable().optional(),
  desktopContract: desktopAvatarToolContractSchema.optional(),
  variant: avatarToolVariantIdSchema.optional(),
  avatarRangeVariant: avatarToolVariantIdSchema.optional(),
  outsideRangeVariant: avatarToolVariantIdSchema.optional(),
  imageKind: avatarToolImageKindSchema.optional(),
  withinAvatarRange: z.boolean().optional(),
  overCompactZone: z.boolean().optional(),
  insideHostWindow: z.boolean().optional(),
  cursorClientX: z.number().finite().optional(),
  cursorClientY: z.number().finite().optional(),
  cursorScreenX: z.number().finite().optional(),
  cursorScreenY: z.number().finite().optional(),
  tool: avatarToolDescriptorSchema.nullable().optional(),
  textContext: z.string().optional(),
  timestamp: z.number().finite(),
}).strict().superRefine((state, context) => {
  const contract = state.desktopContract;
  if (!contract) return;
  if (state.tool !== null && state.tool !== undefined) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['tool'],
      message: 'desktop contract state must not carry the page visual descriptor',
    });
  }
  if (!state.active) {
    if (contract.definition !== null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['desktopContract', 'definition'],
        message: 'inactive state must carry an inactive desktop contract',
      });
    }
    if (state.toolId !== null && state.toolId !== undefined) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['toolId'],
        message: 'inactive state must not carry a tool ID',
      });
    }
    return;
  }
  if (contract.definition === null) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['desktopContract', 'definition'],
      message: 'active state requires a desktop definition',
    });
    return;
  }
  if (state.toolId !== contract.definition.id) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['toolId'],
      message: 'state and desktop contract tool IDs must match',
    });
  }
});

export type AvatarToolStatePayload = z.infer<typeof avatarToolStatePayloadSchema>;

// Protocol builders ----------------------------------------------------------

export type AvatarToolPointer = {
  x: number;
  y: number;
  screenX?: number;
  screenY?: number;
};

export type AvatarToolDescriptorSource = {
  id: AvatarToolDefinitionId;
  iconImagePath: string;
  iconImagePathAlt?: string;
  iconImagePathAlt2?: string;
  pointerImagePath: string;
  pointerImagePathAlt?: string;
  pointerImagePathAlt2?: string;
  pointerHotspotX?: number;
  pointerHotspotY?: number;
  pointerNaturalWidth?: number;
  pointerNaturalHeight?: number;
  pointerDisplayWidth?: number;
  pointerDisplayHeight?: number;
  menuIconScale?: number;
};

export function createAvatarInteractionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `avatar-int-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function buildAvatarInteractionPayload(commit: AvatarToolInteractionCommit): AvatarInteractionPayload {
  const {
    clientX,
    clientY,
    timestamp,
    ...facts
  } = commit;
  return avatarInteractionPayloadSchema.parse({
    ...facts,
    interactionId: createAvatarInteractionId(),
    target: 'avatar' as const,
    pointer: { clientX, clientY },
    timestamp: timestamp ?? Date.now(),
  });
}

function buildAvatarToolDescriptor(activeTool: AvatarToolDescriptorSource | null, label?: string) {
  return activeTool ? {
    id: activeTool.id,
    label,
    iconImagePath: withAvatarToolAssetVersion(activeTool.iconImagePath),
    iconImagePathAlt: activeTool.iconImagePathAlt ? withAvatarToolAssetVersion(activeTool.iconImagePathAlt) : undefined,
    iconImagePathAlt2: activeTool.iconImagePathAlt2 ? withAvatarToolAssetVersion(activeTool.iconImagePathAlt2) : undefined,
    pointerImagePath: withAvatarToolAssetVersion(activeTool.pointerImagePath),
    pointerImagePathAlt: activeTool.pointerImagePathAlt ? withAvatarToolAssetVersion(activeTool.pointerImagePathAlt) : undefined,
    pointerImagePathAlt2: activeTool.pointerImagePathAlt2 ? withAvatarToolAssetVersion(activeTool.pointerImagePathAlt2) : undefined,
    pointerHotspotX: activeTool.pointerHotspotX,
    pointerHotspotY: activeTool.pointerHotspotY,
    pointerNaturalWidth: activeTool.pointerNaturalWidth,
    pointerNaturalHeight: activeTool.pointerNaturalHeight,
    pointerDisplayWidth: activeTool.pointerDisplayWidth,
    pointerDisplayHeight: activeTool.pointerDisplayHeight,
    menuIconScale: activeTool.menuIconScale,
  } : null;
}

export function buildAvatarToolDescriptorStatePayload({
  activeTool,
  avatarRangeVariant,
  outsideRangeVariant,
}: {
  activeTool: AvatarToolDescriptorSource | null;
  avatarRangeVariant?: AvatarToolVariantId;
  outsideRangeVariant?: AvatarToolVariantId;
}): AvatarToolStatePayload {
  return {
    active: !!activeTool,
    toolId: activeTool?.id ?? null,
    desktopContract: buildDesktopAvatarToolContract(activeTool?.id ?? null),
    ...(activeTool ? {
      avatarRangeVariant: avatarRangeVariant ?? 'primary',
      outsideRangeVariant: outsideRangeVariant ?? 'primary',
    } : {}),
    timestamp: Date.now(),
  };
}

export function buildAvatarToolStatePayload({
  activeTool,
  variant,
  avatarRangeVariant,
  outsideRangeVariant,
  imageKind,
  withinAvatarRange,
  overCompactZone,
  insideHostWindow,
  pointer,
  textContext,
  label,
}: {
  activeTool: AvatarToolDescriptorSource | null;
  variant: AvatarToolVariantId;
  avatarRangeVariant: AvatarToolVariantId;
  outsideRangeVariant: AvatarToolVariantId;
  imageKind: 'pointer' | 'icon';
  withinAvatarRange: boolean;
  overCompactZone: boolean;
  insideHostWindow: boolean;
  pointer: AvatarToolPointer;
  textContext?: string;
  label?: string;
}): AvatarToolStatePayload {
  const hasScreenPoint = Number.isFinite(pointer.screenX) && Number.isFinite(pointer.screenY);
  return {
    active: !!activeTool,
    toolId: activeTool?.id ?? null,
    variant,
    avatarRangeVariant,
    outsideRangeVariant,
    imageKind,
    withinAvatarRange,
    overCompactZone,
    insideHostWindow,
    cursorClientX: pointer.x,
    cursorClientY: pointer.y,
    ...(hasScreenPoint ? { cursorScreenX: pointer.screenX, cursorScreenY: pointer.screenY } : {}),
    tool: buildAvatarToolDescriptor(activeTool, label),
    ...(textContext ? { textContext } : {}),
    timestamp: Date.now(),
  };
}

export function getAvatarToolStatePayloadKey(payload: AvatarToolStatePayload): string {
  return JSON.stringify({ ...payload, timestamp: 0 });
}
