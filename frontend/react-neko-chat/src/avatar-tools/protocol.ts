import { z } from 'zod';
import {
  AVATAR_TOOL_DEFINITION_IDS,
  AVATAR_TOOL_REGISTRY,
  AVATAR_TOOL_ROUND_RESULTS,
  AVATAR_TOOL_VARIANT_IDS,
  resolveAvatarToolRoundResult,
  withAvatarToolAssetVersion,
  type AvatarToolDefinition,
  type AvatarToolId,
  type AvatarToolInteractionProfile,
  type AvatarToolRoundResult,
  type AvatarToolVariantId,
} from './catalog';
import {
  buildDesktopAvatarToolContract,
  desktopAvatarToolContractSchema,
} from './desktopContract';
import type { AvatarToolInteractionCommit } from './interaction';

export {
  AVATAR_TOOL_ROUND_RESULTS,
  resolveAvatarToolRoundResult,
  type AvatarToolRoundResult,
} from './catalog';

// Host interaction protocol -------------------------------------------------

type RegistryRegistration = typeof AVATAR_TOOL_REGISTRY[number];
type RegistryDefinition = RegistryRegistration['definition'];
const ROUND_CHOICE_TOOL_IDS = new Set<AvatarToolId>(
  AVATAR_TOOL_REGISTRY
    .filter(({ definition }) => definition.interaction.kind === 'round-choice')
    .map(({ definition }) => definition.id),
);

function isRoundChoiceToolId(toolId: AvatarToolId | null | undefined): boolean {
  return !!toolId && ROUND_CHOICE_TOOL_IDS.has(toolId);
}

type ProgressiveStageFacts<
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'progressive-release' }>,
  Stage = Profile['stages'][number],
> = Stage extends {
  variant: infer Variant extends string;
  actionId: infer ActionId extends string;
  intensity: infer StageIntensity extends string;
}
  ? {
    actionId: ActionId;
    intensity: StageIntensity | (
      Variant extends Profile['burst']['variant']
        ? Profile['burst']['belowThresholdIntensity'] | Profile['burst']['thresholdIntensity']
        : never
    );
  }
  : never;

type SingleActionIntensityFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends {
      kind: 'press-release';
      burst: {
        normalIntensity: infer NormalIntensity extends string;
        rapidIntensity: infer RapidIntensity extends string;
      };
    }
      ? NormalIntensity | RapidIntensity
      : Profile extends {
        kind: 'locked-impact';
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
    ? { touchZone: TouchZone }
    : Record<never, never>;

type ChanceFactFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends { chance: { field: infer Field extends string } }
    ? { [Key in Field]?: boolean }
    : Record<never, never>;


type RoundChoiceFactsFor<
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'round-choice' }>,
> = {
  userGesture: Profile['choices'][number]['gesture'];
  avatarGesture: Profile['choices'][number]['gesture'];
  roundResult: AvatarToolRoundResult;
};

type LockedImpactFactsFor<
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'locked-impact' }>,
> = {
  actionId: Profile['actionId'];
} & TouchZoneFactsFor<Profile> & (
  | {
    intensity: Profile['chance']['intensity'];
  } & { [Key in Profile['chance']['field']]: true }
  | {
    intensity: Exclude<SingleActionIntensityFor<Profile>, Profile['chance']['intensity']>;
  } & { [Key in Profile['chance']['field']]?: false }
);

type InteractionFactsFor<Profile extends AvatarToolInteractionProfile> =
  Profile extends Extract<AvatarToolInteractionProfile, { kind: 'round-choice' }>
    ? RoundChoiceFactsFor<Profile>
    : Profile extends Extract<AvatarToolInteractionProfile, { kind: 'progressive-release' }>
    ? ProgressiveStageFacts<Profile>
    : Profile extends Extract<AvatarToolInteractionProfile, { kind: 'locked-impact' }>
      ? LockedImpactFactsFor<Profile>
    : Profile extends { actionId: infer ActionId extends string }
      ? {
        actionId: ActionId;
        intensity: SingleActionIntensityFor<Profile>;
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

type AvatarInteractionContractFacts = {
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

function deriveAvatarInteractionContractFacts(
  profile: AvatarToolInteractionProfile,
): AvatarInteractionContractFacts {
  if (profile.kind === 'progressive-release') {
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
  if (profile.kind === 'press-release') {
    return {
      actions: [{
        actionId: profile.actionId,
        intensities: [profile.burst.normalIntensity, profile.burst.rapidIntensity],
      }],
      touchZones: profile.touchZones,
      chanceField: profile.chance.field,
    };
  }
  if (profile.kind === 'round-choice') {
    throw new Error('round-choice uses its dedicated host interaction facts');
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

function createAvatarInteractionPayloadSchema(definition: AvatarToolDefinition) {
  if (definition.interaction.kind === 'round-choice') {
    const gestures = definition.interaction.choices.map(choice => choice.gesture);
    return z.object({
      ...avatarInteractionPayloadBaseShape,
      toolId: z.literal(definition.id),
      userGesture: oneOfDeclaredValues(gestures, 'userGesture'),
      avatarGesture: oneOfDeclaredValues(gestures, 'avatarGesture'),
      roundResult: z.enum(AVATAR_TOOL_ROUND_RESULTS),
    }).strict().superRefine((payload, context) => {
      const expected = resolveAvatarToolRoundResult(payload.userGesture, payload.avatarGesture);
      if (payload.roundResult !== expected) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['roundResult'],
          message: 'roundResult must match the declared gestures',
        });
      }
    });
  }
  const facts = deriveAvatarInteractionContractFacts(definition.interaction);
  const intensitiesByActionId = new Map(
    facts.actions.map(action => [action.actionId, new Set(action.intensities)]),
  );
  const toolSpecificShape = {
    toolId: z.literal(definition.id),
    actionId: oneOfDeclaredValues(facts.actions.map(action => action.actionId), 'actionId'),
    intensity: z.string(),
  };
  const conditionalShape: z.ZodRawShape = {};
  if (facts.touchZones.length > 0) {
    conditionalShape.touchZone = oneOfDeclaredValues(facts.touchZones, 'touchZone');
  }
  if (facts.chanceField) {
    conditionalShape[facts.chanceField] = z.boolean().optional();
  }
  return z.object({
    ...avatarInteractionPayloadBaseShape,
    ...toolSpecificShape,
    ...conditionalShape,
  }).strict().superRefine((payload, context) => {
    if (!intensitiesByActionId.get(payload.actionId)?.has(payload.intensity)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['intensity'],
        message: 'intensity is not declared by the selected action',
      });
    }
    if (definition.interaction.kind === 'locked-impact' && facts.chanceField) {
      const chanceHit = (payload as Record<string, unknown>)[facts.chanceField] === true;
      if (chanceHit !== (payload.intensity === definition.interaction.chance.intensity)) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: [facts.chanceField],
          message: 'chance result must match its declared intensity',
        });
      }
    }
  });
}

const avatarInteractionPayloadSchemaByToolId = new Map<
  string,
  ReturnType<typeof createAvatarInteractionPayloadSchema>
>();
AVATAR_TOOL_REGISTRY.forEach(({ definition }) => {
  avatarInteractionPayloadSchemaByToolId.set(
    definition.id,
    createAvatarInteractionPayloadSchema(definition),
  );
});

const toolIdProbeSchema = z.object({ toolId: z.string() }).passthrough();
function isAvatarInteractionPayload(value: unknown): value is AvatarInteractionPayload {
  const probe = toolIdProbeSchema.safeParse(value);
  if (!probe.success) return false;
  const contract = avatarInteractionPayloadSchemaByToolId.get(probe.data.toolId);
  return contract?.safeParse(value).success === true;
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
  roundChoiceResultLabels: z.object({
    user_win: z.string().trim().min(1),
    avatar_win: z.string().trim().min(1).optional(),
    draw: z.string().trim().min(1),
  }).strict().optional(),
  timestamp: z.number().finite(),
}).strict().superRefine((state, context) => {
  if (state.roundChoiceResultLabels && !isRoundChoiceToolId(state.toolId)) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['roundChoiceResultLabels'],
      message: 'round choice result labels require a round-choice tool',
    });
  }
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
  id: AvatarToolId;
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

export function buildAvatarToolSelectionStatePayload({
  activeTool,
  avatarRangeVariant,
  outsideRangeVariant,
  roundChoiceResultLabels,
}: {
  activeTool: AvatarToolDescriptorSource | null;
  avatarRangeVariant?: AvatarToolVariantId;
  outsideRangeVariant?: AvatarToolVariantId;
  roundChoiceResultLabels?: AvatarToolStatePayload['roundChoiceResultLabels'];
}): AvatarToolStatePayload {
  return {
    active: !!activeTool,
    toolId: activeTool?.id ?? null,
    desktopContract: buildDesktopAvatarToolContract(activeTool?.id ?? null),
    ...(activeTool ? {
      avatarRangeVariant: avatarRangeVariant ?? 'primary',
      outsideRangeVariant: outsideRangeVariant ?? 'primary',
    } : {}),
    ...(isRoundChoiceToolId(activeTool?.id) && roundChoiceResultLabels ? { roundChoiceResultLabels } : {}),
    timestamp: Date.now(),
  };
}

export function buildAvatarToolPointerStatePayload({
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
  roundChoiceResultLabels,
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
  roundChoiceResultLabels?: AvatarToolStatePayload['roundChoiceResultLabels'];
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
    ...(isRoundChoiceToolId(activeTool?.id) && roundChoiceResultLabels ? { roundChoiceResultLabels } : {}),
    ...(textContext ? { textContext } : {}),
    timestamp: Date.now(),
  };
}

export function getAvatarToolStatePayloadKey(payload: AvatarToolStatePayload): string {
  return JSON.stringify({ ...payload, timestamp: 0 });
}
