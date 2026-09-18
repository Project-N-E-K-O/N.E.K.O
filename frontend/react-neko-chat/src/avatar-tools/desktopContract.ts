import { z } from 'zod';
import {
  AVATAR_TOOL_ASSET_PATH_MAX_LENGTH,
  AVATAR_TOOL_DEFINITION_IDS,
  AVATAR_TOOL_INTERACTION_INTENSITIES,
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS,
  AVATAR_TOOL_ROUND_CHOICE_GESTURES,
  AVATAR_TOOL_TOUCH_ZONES,
  AVATAR_TOOL_VARIANT_IDS,
  getAvatarToolRegistration,
  hasValidAvatarToolAssetVersion,
  isAvatarToolSameOriginAssetPath,
  withAvatarToolAssetVersion,
  type AvatarToolId,
  type AvatarToolDefinition,
  type AvatarToolEffectRecipe,
  type AvatarToolInteractionProfile,
} from './catalog';
import {
  AVATAR_TOOL_RUNTIME_POLICY,
  avatarToolRuntimePolicySchema,
  type AvatarToolRuntimePolicy,
} from './interaction';

// Strict desktop wire schema -------------------------------------------------

const finiteNumberSchema = z.number().finite();
const nonNegativeNumberSchema = finiteNumberSchema.nonnegative();
const positiveNumberSchema = finiteNumberSchema.positive();
const positiveIntegerSchema = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const probabilitySchema = finiteNumberSchema.min(0).max(1);
const identifierSchema = z.string().min(1).max(64).regex(/^[a-z][a-z0-9_-]*$/);
const payloadFieldSchema = z.string().min(1).max(64).regex(/^[a-z][a-zA-Z0-9]*$/)
  .refine(
    field => !(AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS as readonly string[]).includes(field),
    { message: 'payload field is reserved' },
  );
const builtInAvatarToolDefinitionIdSchema = z.enum(AVATAR_TOOL_DEFINITION_IDS);
const localAvatarToolDefinitionIdSchema = z.string().regex(LOCAL_AVATAR_TOOL_ID_PATTERN);
const localAvatarToolImageIdSchema = z.string().max(80).regex(/^img-[a-z0-9]+(?:-[a-z0-9]+)*$/);
const localAvatarToolInteractionIdSchema = z.string().max(80).regex(/^ix-[a-z0-9]+(?:-[a-z0-9]+)*$/);
const avatarToolVariantIdSchema = z.enum(AVATAR_TOOL_VARIANT_IDS);
const intensitySchema = z.enum(AVATAR_TOOL_INTERACTION_INTENSITIES);
const touchZoneSchema = z.enum(AVATAR_TOOL_TOUCH_ZONES);
const touchZonesSchema = z.array(touchZoneSchema).min(1).max(AVATAR_TOOL_TOUCH_ZONES.length).superRefine((touchZones, context) => {
  if (new Set(touchZones).size !== touchZones.length) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'touchZones must not contain duplicates',
    });
  }
});

export const desktopAvatarToolAssetPathSchema = z.string().min(1).max(AVATAR_TOOL_ASSET_PATH_MAX_LENGTH)
  .refine(isAvatarToolSameOriginAssetPath, {
    message: 'asset path must be a same-origin absolute path',
  })
  .refine(hasValidAvatarToolAssetVersion, {
    message: 'asset path must contain exactly one non-empty version parameter and no fragment',
  });

const renderedAnchorSchema = z.object({
  x: finiteNumberSchema,
  y: finiteNumberSchema,
  coordinateSpace: z.literal('final-css-pixel'),
}).strict();

const visualModeSchema = z.object({
  displayWidth: positiveNumberSchema,
  displayHeight: positiveNumberSchema,
  displayCoordinateSpace: z.literal('pre-scale-css-pixel'),
  scale: positiveNumberSchema,
  renderedAnchor: renderedAnchorSchema,
}).strict();

const visualVariantSchema = z.object({
  iconImagePath: desktopAvatarToolAssetPathSchema,
  pointerImagePath: desktopAvatarToolAssetPathSchema,
}).strict();

export const desktopAvatarToolVisualSchema = z.object({
  initialVariant: avatarToolVariantIdSchema,
  variants: z.object({
    primary: visualVariantSchema,
    secondary: visualVariantSchema,
    tertiary: visualVariantSchema,
  }).strict(),
  frames: z.array(visualVariantSchema).min(1).max(17).optional(),
  presentation: z.object({
    inRangeVariantSource: z.enum(['range', 'outside', 'primary']),
    outsideVariantSource: z.enum(['range', 'outside', 'primary']),
    effectActiveImageKind: z.enum(['pointer', 'icon']),
  }).strict(),
  hotspotX: finiteNumberSchema,
  hotspotY: finiteNumberSchema,
  naturalWidth: positiveNumberSchema,
  naturalHeight: positiveNumberSchema,
  pointer: visualModeSchema,
  inRange: visualModeSchema,
}).strict();

const soundResourceSchema = z.object({
  id: identifierSchema,
  src: desktopAvatarToolAssetPathSchema,
  volume: probabilitySchema,
}).strict();

const finiteRangeSchema = z.object({
  min: finiteNumberSchema,
  range: nonNegativeNumberSchema,
}).strict();

const fixedParticlesEffectSchema = z.object({
  id: identifierSchema,
  kind: z.literal('fixed-particles'),
  interactionLock: z.literal('none'),
  lifetimeMs: positiveNumberSchema,
  glyph: z.string().min(1).max(16),
  particles: z.array(z.object({
    offsetX: finiteNumberSchema,
    offsetY: finiteNumberSchema,
    driftX: finiteNumberSchema,
    driftY: finiteNumberSchema,
    scale: positiveNumberSchema,
    delayMs: nonNegativeNumberSchema,
  }).strict()).min(1).max(64),
}).strict();

const randomScatterEffectSchema = z.object({
  id: identifierSchema,
  kind: z.literal('random-scatter'),
  interactionLock: z.literal('none'),
  assetPath: desktopAvatarToolAssetPathSchema,
  count: positiveIntegerSchema.max(64),
  lifetimeMs: positiveNumberSchema,
  angleDeg: finiteRangeSchema,
  distance: finiteRangeSchema,
  offsetX: finiteRangeSchema,
  offsetY: finiteRangeSchema,
  rotation: finiteRangeSchema,
  scale: finiteRangeSchema,
  delayMs: finiteRangeSchema,
}).strict().superRefine((effect, context) => {
  if (effect.distance.min <= 0) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['distance', 'min'], message: 'must be positive' });
  }
  if (effect.scale.min <= 0) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['scale', 'min'], message: 'must be positive' });
  }
  if (effect.delayMs.min < 0) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['delayMs', 'min'], message: 'must not be negative' });
  }
});

const hammerTimelineEntrySchema = z.object({
  phase: z.enum(['idle', 'windup', 'swing', 'impact', 'recover']),
  delayMs: nonNegativeNumberSchema,
}).strict();

const hammerSwingEffectSchema = z.object({
  id: identifierSchema,
  kind: z.literal('hammer-swing'),
  interactionLock: z.literal('effect-lifetime'),
  anchor: z.object({
    source: z.literal('live-pointer'),
    visualMode: z.literal('inRange'),
  }).strict(),
  transformOrigin: z.object({ x: finiteNumberSchema, y: finiteNumberSchema }).strict(),
  impactRegistration: z.object({
    transformOrigin: z.object({ x: finiteNumberSchema, y: finiteNumberSchema }).strict(),
    translate: z.object({ x: finiteNumberSchema, y: finiteNumberSchema }).strict(),
    rotationDeg: finiteNumberSchema,
    scale: positiveNumberSchema,
  }).strict(),
  variants: z.object({
    idle: avatarToolVariantIdSchema,
    impact: avatarToolVariantIdSchema,
  }).strict(),
  timeline: z.array(hammerTimelineEntrySchema).length(5),
  easterEgg: z.object({
    mode: z.literal('easter-egg'),
    scale: positiveNumberSchema,
    anchorOffset: z.object({ x: finiteNumberSchema, y: finiteNumberSchema }).strict(),
  }).strict(),
}).strict().superRefine((effect, context) => {
  const expected = ['windup', 'swing', 'impact', 'recover', 'idle'] as const;
  effect.timeline.forEach((entry, index) => {
    if (entry.phase !== expected[index]) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'phase'],
        message: `must be ${expected[index]}`,
      });
    }
    if (index === 0 && entry.delayMs !== 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'delayMs'],
        message: 'windup must start at 0ms',
      });
    }
    if (index > 0 && entry.delayMs <= effect.timeline[index - 1].delayMs) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'delayMs'],
        message: 'must be strictly increasing',
      });
    }
  });
});

const roundRevealTimelineEntrySchema = z.object({
  phase: z.enum(['approach', 'impact', 'result', 'recover', 'idle']),
  delayMs: nonNegativeNumberSchema,
}).strict();

const roundRevealEffectSchema = z.object({
  id: identifierSchema,
  kind: z.literal('round-reveal'),
  interactionLock: z.literal('effect-lifetime'),
  separationPx: positiveNumberSchema,
  resultOffsetY: finiteNumberSchema,
  timeline: z.array(roundRevealTimelineEntrySchema).length(5),
}).strict().superRefine((effect, context) => {
  const expected = ['approach', 'impact', 'result', 'recover', 'idle'] as const;
  effect.timeline.forEach((entry, index) => {
    if (entry.phase !== expected[index]) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'phase'],
        message: `must be ${expected[index]}`,
      });
    }
    if (index === 0 && entry.delayMs !== 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'delayMs'],
        message: 'approach must start at 0ms',
      });
    }
    if (index > 0 && entry.delayMs <= effect.timeline[index - 1].delayMs) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['timeline', index, 'delayMs'],
        message: 'must be strictly increasing',
      });
    }
  });
  if (effect.timeline[3].delayMs - effect.timeline[2].delayMs < 2000) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['timeline', 3, 'delayMs'],
      message: 'result must remain visible for at least 2000ms',
    });
  }
});

const effectRecipeSchema = z.union([
  fixedParticlesEffectSchema,
  randomScatterEffectSchema,
  hammerSwingEffectSchema,
  roundRevealEffectSchema,
]);

const progressiveReleaseProfileSchema = z.object({
  kind: z.literal('progressive-release'),
  stages: z.array(z.object({
    variant: avatarToolVariantIdSchema,
    actionId: identifierSchema,
    intensity: intensitySchema,
    nextVariant: avatarToolVariantIdSchema.nullable(),
  }).strict()).min(1).max(8),
  burst: z.object({
    variant: avatarToolVariantIdSchema,
    windowMs: positiveNumberSchema,
    threshold: positiveIntegerSchema,
    belowThresholdIntensity: intensitySchema,
    thresholdIntensity: intensitySchema,
  }).strict(),
  feedback: z.object({
    sound: identifierSchema,
    effect: identifierSchema,
    effectVariant: avatarToolVariantIdSchema,
  }).strict(),
}).strict().superRefine((profile, context) => {
  const variants = profile.stages.map(stage => stage.variant);
  if (profile.stages.length !== 3 || new Set(variants).size !== 3) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['stages'],
      message: 'must cover primary, secondary and tertiary exactly once',
    });
  }
});

const pressReleaseProfileSchema = z.object({
  kind: z.literal('press-release'),
  actionId: identifierSchema,
  pointerDown: z.object({
    rangeVariant: avatarToolVariantIdSchema,
    outsideVariant: avatarToolVariantIdSchema,
  }).strict(),
  pointerRelease: z.object({
    rangeVariant: avatarToolVariantIdSchema,
    outsideVariant: avatarToolVariantIdSchema,
  }).strict(),
  burst: z.object({
    windowMs: positiveNumberSchema,
    rapidThreshold: positiveIntegerSchema,
    normalIntensity: intensitySchema,
    rapidIntensity: intensitySchema,
  }).strict(),
  touchZone: z.literal('release'),
  touchZones: touchZonesSchema,
  chance: z.object({
    field: payloadFieldSchema,
    probability: probabilitySchema,
    sound: identifierSchema,
    effect: identifierSchema,
  }).strict(),
}).strict();

const localPressReleaseProfileSchema = z.object({
  kind: z.literal('press-release'),
  revision: z.string().regex(/^\d+-\d+$/).max(128),
  actionId: z.literal('interact'),
  imageChange: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('press-swap') }).strict(),
    z.object({ kind: z.literal('click-advance') }).strict(),
  ]),
  burst: z.object({
    windowMs: positiveNumberSchema,
    rapidThreshold: positiveIntegerSchema,
    normalIntensity: z.literal('normal'),
    rapidIntensity: z.literal('rapid'),
  }).strict(),
  touchZone: z.literal('release'),
  touchZones: touchZonesSchema,
  feedback: z.object({ sound: identifierSchema }).strict().optional(),
  chance: z.object({
    field: z.literal('specialTriggered'),
    probability: probabilitySchema.positive(),
    effect: identifierSchema,
    sound: identifierSchema.optional(),
  }).strict().optional(),
}).strict();

const customGraphImageActionSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('keep') }).strict(),
  z.object({ kind: z.literal('show'), imageId: localAvatarToolImageIdSchema }).strict(),
]);

const customGraphProfileSchema = z.object({
  kind: z.literal('custom-graph'),
  revision: z.string().regex(/^3-\d+$/).max(128),
  images: z.array(z.object({
    id: localAvatarToolImageIdSchema,
    frameIndex: z.number().int().nonnegative().max(16),
    hasMeaning: z.boolean(),
  }).strict()).min(1).max(17),
  initialImageId: localAvatarToolImageIdSchema,
  initialInteractionIds: z.array(localAvatarToolInteractionIdSchema).min(1).max(16),
  interactions: z.array(z.union([
    z.object({
      id: localAvatarToolInteractionIdSchema,
      trigger: z.object({ kind: z.literal('mouse-click') }).strict(),
      actions: z.object({
        press: customGraphImageActionSchema,
        release: customGraphImageActionSchema,
      }).strict(),
    }).strict(),
    z.object({
      id: localAvatarToolInteractionIdSchema,
      trigger: z.object({ kind: z.literal('after'), delayMs: positiveIntegerSchema.max(600000) }).strict(),
      actions: z.object({ complete: customGraphImageActionSchema }).strict(),
    }).strict(),
  ])).min(1).max(16),
  links: z.array(z.object({
    from: localAvatarToolInteractionIdSchema,
    to: localAvatarToolInteractionIdSchema,
  }).strict()).max(32),
  burst: z.object({
    windowMs: positiveNumberSchema,
    rapidThreshold: positiveIntegerSchema,
    normalIntensity: z.literal('normal'),
    rapidIntensity: z.literal('rapid'),
  }).strict(),
  touchZone: z.literal('release'),
  touchZones: touchZonesSchema,
  feedback: z.object({ sound: identifierSchema }).strict().optional(),
  chance: z.object({
    field: z.literal('specialTriggered'),
    probability: probabilitySchema.positive(),
    effect: identifierSchema,
    sound: identifierSchema.optional(),
  }).strict().optional(),
}).strict().superRefine((profile, context) => {
  const imageIds = profile.images.map(image => image.id);
  const frameIndices = profile.images.map(image => image.frameIndex);
  if (new Set(imageIds).size !== imageIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['images'], message: 'image IDs must be unique' });
  }
  if (new Set(frameIndices).size !== frameIndices.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['images'], message: 'frame indices must be unique' });
  }
  if (!imageIds.includes(profile.initialImageId)) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['initialImageId'], message: 'must reference an image' });
  }
  const interactionIds = profile.interactions.map(item => item.id);
  const interactionIdSet = new Set(interactionIds);
  if (interactionIdSet.size !== interactionIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['interactions'], message: 'interaction IDs must be unique' });
  }
  if (
    new Set(profile.initialInteractionIds).size !== profile.initialInteractionIds.length
    || profile.initialInteractionIds.some(id => !interactionIdSet.has(id))
  ) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['initialInteractionIds'], message: 'must be unique interaction references' });
  }
  const imageIdSet = new Set(imageIds);
  profile.interactions.forEach((item, index) => {
    const actions = 'press' in item.actions
      ? [item.actions.press, item.actions.release]
      : [item.actions.complete];
    actions.forEach((action) => {
      if (action.kind === 'show' && !imageIdSet.has(action.imageId)) {
        context.addIssue({ code: z.ZodIssueCode.custom, path: ['interactions', index, 'actions'], message: 'references an unknown image' });
      }
    });
  });
  const linkKeys = new Set<string>();
  profile.links.forEach((link, index) => {
    const key = `${link.from}\u0000${link.to}`;
    if (!interactionIdSet.has(link.from) || !interactionIdSet.has(link.to) || linkKeys.has(key)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['links', index], message: 'must be a unique interaction link' });
    }
    linkKeys.add(key);
  });
  if (profile.initialInteractionIds.length + profile.links.length > 32) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['links'], message: 'total links must not exceed 32' });
  }
  const successorsById = new Map<string, string[]>();
  profile.links.forEach((link) => {
    const successors = successorsById.get(link.from) ?? [];
    successors.push(link.to);
    successorsById.set(link.from, successors);
  });
  const reachable = new Set<string>();
  const queue = [...profile.initialInteractionIds];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (reachable.has(id)) continue;
    reachable.add(id);
    successorsById.get(id)?.forEach(successor => queue.push(successor));
  }
  if (reachable.size !== interactionIdSet.size) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['interactions'], message: 'all interactions must be reachable' });
  }
  const interactionsById = new Map(profile.interactions.map(interaction => [interaction.id, interaction]));
  const waitingPositions = [
    profile.initialInteractionIds,
    ...profile.interactions.map(interaction => successorsById.get(interaction.id) ?? []),
  ];
  waitingPositions.forEach((ids, index) => {
    const candidates = ids.flatMap((id) => {
      const candidate = interactionsById.get(id);
      return candidate ? [candidate] : [];
    });
    if (candidates.filter(candidate => candidate.trigger.kind === 'mouse-click').length > 1) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['links'], message: `waiting position ${index} has ambiguous mouse clicks` });
    }
    const delays = candidates.flatMap(candidate => candidate.trigger.kind === 'after' ? [candidate.trigger.delayMs] : []);
    if (new Set(delays).size !== delays.length) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['links'], message: `waiting position ${index} has ambiguous delays` });
    }
  });
});

const lockedImpactProfileSchema = z.object({
  kind: z.literal('locked-impact'),
  actionId: identifierSchema,
  touchZone: z.literal('release'),
  outsideFeedback: z.object({
    variant: avatarToolVariantIdSchema,
    resetAfterMs: positiveNumberSchema,
  }).strict(),
  burst: z.object({
    windowMs: positiveNumberSchema,
    rapidThreshold: positiveIntegerSchema,
    burstThreshold: positiveIntegerSchema,
    normalIntensity: intensitySchema,
    rapidIntensity: intensitySchema,
    burstIntensity: intensitySchema,
  }).strict(),
  touchZones: touchZonesSchema,
  chance: z.object({
    field: payloadFieldSchema,
    probability: probabilitySchema,
    intensity: z.literal('easter_egg'),
    sound: identifierSchema,
  }).strict(),
  feedback: z.object({
    sound: identifierSchema,
    effect: identifierSchema,
  }).strict(),
}).strict().superRefine((profile, context) => {
  if (profile.burst.rapidThreshold > profile.burst.burstThreshold) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['burst'],
      message: 'rapidThreshold must not exceed burstThreshold',
    });
  }
  if ([
    profile.burst.normalIntensity,
    profile.burst.rapidIntensity,
    profile.burst.burstIntensity,
  ].includes(profile.chance.intensity)) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['chance', 'intensity'],
      message: 'chance intensity must be exclusive to the chance result',
    });
  }
});

const roundChoiceProfileSchema = z.object({
  kind: z.literal('round-choice'),
  choices: z.array(z.object({
    gesture: z.enum(AVATAR_TOOL_ROUND_CHOICE_GESTURES),
    variant: avatarToolVariantIdSchema,
  }).strict()).length(3),
  cycle: z.object({
    outsideIntervalMs: positiveNumberSchema,
    rangeIntervalMs: positiveNumberSchema,
  }).strict(),
  confirmation: z.object({
    sound: identifierSchema,
  }).strict(),
  reveal: z.object({
    effect: identifierSchema,
    userWinSound: identifierSchema,
    otherResultSound: identifierSchema,
  }).strict(),
}).strict().superRefine((profile, context) => {
  const gestures = profile.choices.map(choice => choice.gesture);
  const variants = profile.choices.map(choice => choice.variant);
  if (
    new Set(gestures).size !== AVATAR_TOOL_ROUND_CHOICE_GESTURES.length
    || new Set(variants).size !== AVATAR_TOOL_VARIANT_IDS.length
  ) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['choices'],
      message: 'must map every gesture and variant exactly once',
    });
  }
  if (profile.cycle.rangeIntervalMs <= profile.cycle.outsideIntervalMs) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['cycle', 'rangeIntervalMs'],
      message: 'must be greater than outsideIntervalMs',
    });
  }
});

const interactionProfileSchema = z.union([
  progressiveReleaseProfileSchema,
  pressReleaseProfileSchema,
  lockedImpactProfileSchema,
  roundChoiceProfileSchema,
]);

const localInteractionProfileSchema = localPressReleaseProfileSchema;

function collectInteractionReferences(
  profile: z.infer<typeof interactionProfileSchema> | z.infer<typeof localInteractionProfileSchema>,
) {
  if (profile.kind === 'progressive-release') {
    return { sounds: [profile.feedback.sound], effects: [profile.feedback.effect] };
  }
  if (profile.kind === 'press-release') {
    const feedbackSound = 'feedback' in profile ? profile.feedback?.sound : undefined;
    return {
      sounds: [feedbackSound, profile.chance?.sound].filter((value): value is string => !!value),
      effects: profile.chance ? [profile.chance.effect] : [],
    };
  }
  if (profile.kind === 'round-choice') {
    return {
      sounds: [profile.confirmation.sound, profile.reveal.userWinSound, profile.reveal.otherResultSound],
      effects: [profile.reveal.effect],
    };
  }
  return {
    sounds: [profile.chance.sound, profile.feedback.sound],
    effects: [profile.feedback.effect],
  };
}
export const desktopAvatarToolInteractionSchema = z.object({
  profile: interactionProfileSchema,
  sounds: z.array(soundResourceSchema).min(1).max(16),
  effects: z.array(effectRecipeSchema).max(16),
}).strict().superRefine((interaction, context) => {
  const soundIds = interaction.sounds.map(sound => sound.id);
  const effectIds = interaction.effects.map(effect => effect.id);
  if (new Set(soundIds).size !== soundIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds'], message: 'sound IDs must be unique' });
  }
  if (new Set(effectIds).size !== effectIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects'], message: 'effect IDs must be unique' });
  }
  const references = collectInteractionReferences(interaction.profile);
  const expectedSounds = new Set(references.sounds);
  const expectedEffects = new Set(references.effects);
  soundIds.forEach((id, index) => {
    if (!expectedSounds.has(id)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds', index, 'id'], message: 'unreferenced sound' });
    }
  });
  effectIds.forEach((id, index) => {
    if (!expectedEffects.has(id)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects', index, 'id'], message: 'unreferenced effect' });
    }
  });
  references.sounds.forEach((id) => {
    if (!soundIds.includes(id)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds'], message: `missing referenced sound ${id}` });
    }
  });
  references.effects.forEach((id) => {
    if (!effectIds.includes(id)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects'], message: `missing referenced effect ${id}` });
    }
  });
});

export const desktopLocalAvatarToolInteractionSchema = z.object({
  profile: localInteractionProfileSchema,
  sounds: z.array(soundResourceSchema).max(16),
  effects: z.array(effectRecipeSchema).max(16),
}).strict().superRefine((interaction, context) => {
  const soundIds = interaction.sounds.map(sound => sound.id);
  const effectIds = interaction.effects.map(effect => effect.id);
  if (new Set(soundIds).size !== soundIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds'], message: 'sound IDs must be unique' });
  }
  if (new Set(effectIds).size !== effectIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects'], message: 'effect IDs must be unique' });
  }
  const references = collectInteractionReferences(interaction.profile);
  if (soundIds.length !== references.sounds.length || soundIds.some(id => !references.sounds.includes(id))) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds'], message: 'sounds must match references exactly' });
  }
  if (effectIds.length !== references.effects.length || effectIds.some(id => !references.effects.includes(id))) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects'], message: 'effects must match references exactly' });
  }
});

export const desktopLocalAvatarToolCustomGraphInteractionSchema = z.object({
  profile: customGraphProfileSchema,
  sounds: z.array(soundResourceSchema).max(16),
  effects: z.array(effectRecipeSchema).max(16),
}).strict().superRefine((interaction, context) => {
  const soundIds = interaction.sounds.map(sound => sound.id);
  const effectIds = interaction.effects.map(effect => effect.id);
  const expectedSounds = [
    interaction.profile.feedback?.sound,
    interaction.profile.chance?.sound,
  ].filter((value): value is string => !!value);
  const expectedEffects = interaction.profile.chance ? [interaction.profile.chance.effect] : [];
  if (
    new Set(soundIds).size !== soundIds.length
    || soundIds.length !== new Set(expectedSounds).size
    || soundIds.some(id => !expectedSounds.includes(id))
  ) context.addIssue({ code: z.ZodIssueCode.custom, path: ['sounds'], message: 'sounds must match references exactly' });
  if (
    new Set(effectIds).size !== effectIds.length
    || effectIds.length !== new Set(expectedEffects).size
    || effectIds.some(id => !expectedEffects.includes(id))
  ) context.addIssue({ code: z.ZodIssueCode.custom, path: ['effects'], message: 'effects must match references exactly' });
  if (interaction.profile.chance) {
    const effect = interaction.effects.find(candidate => candidate.id === interaction.profile.chance?.effect);
    if (effect?.kind !== 'random-scatter') {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['profile', 'chance', 'effect'], message: 'must reference random-scatter' });
    }
  }
});

const desktopBuiltInAvatarToolDefinitionSchema = z.object({
  definitionVersion: z.literal(1),
  id: builtInAvatarToolDefinitionIdSchema,
  capability: z.object({
    desktopVisual: z.boolean(),
    desktopInteraction: z.boolean(),
  }).strict(),
  visual: desktopAvatarToolVisualSchema.nullable(),
  interaction: desktopAvatarToolInteractionSchema.nullable(),
}).strict().superRefine((definition, context) => {
  const { desktopVisual, desktopInteraction } = definition.capability;
  if (!desktopVisual && desktopInteraction) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['capability'],
      message: 'desktop interaction requires desktop visual capability',
    });
  }
  if ((definition.visual !== null) !== desktopVisual) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['visual'],
      message: 'visual projection must match desktopVisual capability',
    });
  }
  if ((definition.interaction !== null) !== desktopInteraction) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['interaction'],
      message: 'interaction projection must match desktopInteraction capability',
    });
  }
  if (definition.visual?.frames !== undefined) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['visual', 'frames'],
      message: 'v1 visual must not include ordered frames',
    });
  }
});

const desktopLocalAvatarToolDefinitionSchema = z.object({
  definitionVersion: z.literal(2),
  id: localAvatarToolDefinitionIdSchema,
  capability: z.object({
    desktopVisual: z.literal(true),
    desktopInteraction: z.literal(true),
  }).strict(),
  visual: desktopAvatarToolVisualSchema,
  interaction: desktopLocalAvatarToolInteractionSchema,
}).strict().superRefine((definition, context) => {
  const frames = definition.visual.frames;
  if (!frames || frames.length < 2) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['visual', 'frames'],
      message: 'v2 visual requires ordered frames',
    });
    return;
  }
  if (definition.interaction.profile.imageChange.kind === 'press-swap' && frames.length !== 2) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['visual', 'frames'],
      message: 'press-swap requires one default and one change frame',
    });
  }
});

const desktopLocalAvatarToolV3DefinitionSchema = z.object({
  definitionVersion: z.literal(3),
  id: localAvatarToolDefinitionIdSchema,
  capability: z.object({
    desktopVisual: z.literal(true),
    desktopInteraction: z.literal(true),
  }).strict(),
  visual: desktopAvatarToolVisualSchema,
  interaction: desktopLocalAvatarToolCustomGraphInteractionSchema,
}).strict().superRefine((definition, context) => {
  const frames = definition.visual.frames;
  if (!frames || frames.length !== definition.interaction.profile.images.length) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['visual', 'frames'],
      message: 'v3 frames must match the custom graph image mapping',
    });
  }
  definition.interaction.profile.images.forEach((image, index) => {
    if (image.frameIndex !== index) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['interaction', 'profile', 'images', index, 'frameIndex'],
        message: 'frame mapping must follow ordered images',
      });
    }
  });
});

export const desktopAvatarToolDefinitionSchema = z.union([
  desktopBuiltInAvatarToolDefinitionSchema,
  desktopLocalAvatarToolDefinitionSchema,
  desktopLocalAvatarToolV3DefinitionSchema,
]);

export const desktopAvatarToolContractSchema = z.object({
  wireVersion: z.literal(1),
  definition: desktopAvatarToolDefinitionSchema.nullable(),
  runtimePolicy: avatarToolRuntimePolicySchema.nullable(),
}).strict().superRefine((contract, context) => {
  if (contract.definition === null) {
    if (contract.runtimePolicy !== null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['runtimePolicy'],
        message: 'inactive contract must not include a runtime policy',
      });
    }
    return;
  }
  const requiresPolicy = contract.definition.capability.desktopVisual;
  if ((contract.runtimePolicy !== null) !== requiresPolicy) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['runtimePolicy'],
      message: 'runtime policy must match desktop visual capability',
    });
  }
});

export type DesktopAvatarToolVisual = z.infer<typeof desktopAvatarToolVisualSchema>;
export type DesktopAvatarToolInteraction =
  | z.infer<typeof desktopAvatarToolInteractionSchema>
  | z.infer<typeof desktopLocalAvatarToolInteractionSchema>
  | z.infer<typeof desktopLocalAvatarToolCustomGraphInteractionSchema>;
export type DesktopAvatarToolContract = z.infer<typeof desktopAvatarToolContractSchema>;


// NEKO definition projection -------------------------------------------------

function projectAssetPath(path: string): string {
  return desktopAvatarToolAssetPathSchema.parse(withAvatarToolAssetVersion(path, '0'));
}

function projectVisual(definition: AvatarToolDefinition): DesktopAvatarToolVisual {
  const { visual } = definition;
  const projectVariant = (variant: keyof typeof visual.variants) => ({
    iconImagePath: projectAssetPath(visual.variants[variant].iconImagePath),
    pointerImagePath: projectAssetPath(visual.variants[variant].pointerImagePath),
  });
  const projectMode = (mode: 'pointer' | 'inRange') => ({
    displayWidth: visual[mode].displayWidth,
    displayHeight: visual[mode].displayHeight,
    displayCoordinateSpace: visual[mode].displayCoordinateSpace,
    scale: visual[mode].scale,
    renderedAnchor: {
      x: visual[mode].renderedAnchor.x,
      y: visual[mode].renderedAnchor.y,
      coordinateSpace: visual[mode].renderedAnchor.coordinateSpace,
    },
  });
  return {
    initialVariant: visual.initialVariant,
    variants: {
      primary: projectVariant('primary'),
      secondary: projectVariant('secondary'),
      tertiary: projectVariant('tertiary'),
    },
    ...(definition.definitionVersion !== 1 && visual.frames
      ? { frames: visual.frames.map(frame => ({
        iconImagePath: projectAssetPath(frame.iconImagePath),
        pointerImagePath: projectAssetPath(frame.pointerImagePath),
      })) }
      : {}),
    presentation: {
      inRangeVariantSource: visual.presentation.inRangeVariantSource,
      outsideVariantSource: visual.presentation.outsideVariantSource,
      effectActiveImageKind: visual.presentation.effectActiveImageKind,
    },
    hotspotX: visual.hotspotX,
    hotspotY: visual.hotspotY,
    naturalWidth: visual.naturalWidth,
    naturalHeight: visual.naturalHeight,
    pointer: projectMode('pointer'),
    inRange: projectMode('inRange'),
  };
}

function projectEffect(effect: AvatarToolEffectRecipe) {
  if (effect.kind === 'fixed-particles') {
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      lifetimeMs: effect.lifetimeMs,
      glyph: effect.glyph,
      particles: effect.particles.map(particle => ({
        offsetX: particle.offsetX,
        offsetY: particle.offsetY,
        driftX: particle.driftX,
        driftY: particle.driftY,
        scale: particle.scale,
        delayMs: particle.delayMs,
      })),
    };
  }
  if (effect.kind === 'random-scatter') {
    const projectRange = (range: { min: number; range: number }) => ({
      min: range.min,
      range: range.range,
    });
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      assetPath: projectAssetPath(effect.assetPath),
      count: effect.count,
      lifetimeMs: effect.lifetimeMs,
      angleDeg: projectRange(effect.angleDeg),
      distance: projectRange(effect.distance),
      offsetX: projectRange(effect.offsetX),
      offsetY: projectRange(effect.offsetY),
      rotation: projectRange(effect.rotation),
      scale: projectRange(effect.scale),
      delayMs: projectRange(effect.delayMs),
    };
  }
  if (effect.kind === 'round-reveal') {
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      separationPx: effect.separationPx,
      resultOffsetY: effect.resultOffsetY,
      timeline: effect.timeline.map(entry => ({ phase: entry.phase, delayMs: entry.delayMs })),
    };
  }
  return {
    id: effect.id,
    kind: effect.kind,
    interactionLock: effect.interactionLock,
    anchor: {
      source: effect.anchor.source,
      visualMode: effect.anchor.visualMode,
    },
    transformOrigin: {
      x: effect.transformOrigin.x,
      y: effect.transformOrigin.y,
    },
    impactRegistration: {
      transformOrigin: {
        x: effect.impactRegistration.transformOrigin.x,
        y: effect.impactRegistration.transformOrigin.y,
      },
      translate: {
        x: effect.impactRegistration.translate.x,
        y: effect.impactRegistration.translate.y,
      },
      rotationDeg: effect.impactRegistration.rotationDeg,
      scale: effect.impactRegistration.scale,
    },
    variants: {
      idle: effect.variants.idle,
      impact: effect.variants.impact,
    },
    timeline: effect.timeline.map(entry => ({ phase: entry.phase, delayMs: entry.delayMs })),
    easterEgg: {
      mode: effect.easterEgg.mode,
      scale: effect.easterEgg.scale,
      anchorOffset: {
        x: effect.easterEgg.anchorOffset.x,
        y: effect.easterEgg.anchorOffset.y,
      },
    },
  };
}

function projectProfile(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'custom-graph') {
    return {
      kind: profile.kind,
      revision: profile.revision,
      images: profile.images.map(image => ({ ...image })),
      initialImageId: profile.initialImageId,
      initialInteractionIds: [...profile.initialInteractionIds],
      interactions: profile.interactions.map(interaction => ({
        id: interaction.id,
        trigger: { ...interaction.trigger },
        actions: 'press' in interaction.actions
          ? {
            press: { ...interaction.actions.press },
            release: { ...interaction.actions.release },
          }
          : { complete: { ...interaction.actions.complete } },
      })),
      links: profile.links.map(link => ({ ...link })),
      burst: {
        windowMs: profile.burst.windowMs,
        rapidThreshold: profile.burst.rapidThreshold,
        normalIntensity: profile.burst.normalIntensity,
        rapidIntensity: profile.burst.rapidIntensity,
      },
      touchZone: profile.touchZone,
      touchZones: [...profile.touchZones],
      ...(profile.feedback ? { feedback: { sound: profile.feedback.sound } } : {}),
      ...(profile.chance ? {
        chance: {
          field: profile.chance.field,
          probability: profile.chance.probability,
          effect: profile.chance.effect,
          ...(profile.chance.sound ? { sound: profile.chance.sound } : {}),
        },
      } : {}),
    };
  }
  if (profile.kind === 'progressive-release') {
    return {
      kind: profile.kind,
      stages: profile.stages.map(stage => ({
        variant: stage.variant,
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
      })),
      burst: {
        variant: profile.burst.variant,
        windowMs: profile.burst.windowMs,
        threshold: profile.burst.threshold,
        belowThresholdIntensity: profile.burst.belowThresholdIntensity,
        thresholdIntensity: profile.burst.thresholdIntensity,
      },
      feedback: {
        sound: profile.feedback.sound,
        effect: profile.feedback.effect,
        effectVariant: profile.feedback.effectVariant,
      },
    };
  }
  if (profile.kind === 'press-release') {
    if (profile.imageChange) {
      return {
        kind: profile.kind,
        ...(profile.revision ? { revision: profile.revision } : {}),
        actionId: profile.actionId,
        imageChange: { kind: profile.imageChange.kind },
        burst: {
          windowMs: profile.burst.windowMs,
          rapidThreshold: profile.burst.rapidThreshold,
          normalIntensity: profile.burst.normalIntensity,
          rapidIntensity: profile.burst.rapidIntensity,
        },
        touchZone: profile.touchZone,
        touchZones: [...profile.touchZones],
        ...(profile.feedback ? { feedback: { sound: profile.feedback.sound } } : {}),
        ...(profile.chance ? { chance: {
          field: profile.chance.field,
          probability: profile.chance.probability,
          effect: profile.chance.effect,
          ...(profile.chance.sound ? { sound: profile.chance.sound } : {}),
        } } : {}),
      };
    }
    return {
      kind: profile.kind,
      actionId: profile.actionId,
      pointerDown: {
        rangeVariant: profile.pointerDown!.rangeVariant,
        outsideVariant: profile.pointerDown!.outsideVariant,
      },
      pointerRelease: {
        rangeVariant: profile.pointerRelease!.rangeVariant,
        outsideVariant: profile.pointerRelease!.outsideVariant,
      },
      burst: {
        windowMs: profile.burst.windowMs,
        rapidThreshold: profile.burst.rapidThreshold,
        normalIntensity: profile.burst.normalIntensity,
        rapidIntensity: profile.burst.rapidIntensity,
      },
      touchZone: profile.touchZone,
      touchZones: [...profile.touchZones],
      ...(profile.feedback ? { feedback: { sound: profile.feedback.sound } } : {}),
      ...(profile.chance ? { chance: {
        field: profile.chance.field,
        probability: profile.chance.probability,
        effect: profile.chance.effect,
        ...(profile.chance.sound ? { sound: profile.chance.sound } : {}),
      } } : {}),
    };
  }
  if (profile.kind === 'round-choice') {
    return {
      kind: profile.kind,
      choices: profile.choices.map(choice => ({
        gesture: choice.gesture,
        variant: choice.variant,
      })),
      cycle: {
        outsideIntervalMs: profile.cycle.outsideIntervalMs,
        rangeIntervalMs: profile.cycle.rangeIntervalMs,
      },
      confirmation: {
        sound: profile.confirmation.sound,
      },
      reveal: {
        effect: profile.reveal.effect,
        userWinSound: profile.reveal.userWinSound,
        otherResultSound: profile.reveal.otherResultSound,
      },
    };
  }
  return {
    kind: profile.kind,
    actionId: profile.actionId,
    touchZone: profile.touchZone,
    outsideFeedback: {
      variant: profile.outsideFeedback.variant,
      resetAfterMs: profile.outsideFeedback.resetAfterMs,
    },
    burst: {
      windowMs: profile.burst.windowMs,
      rapidThreshold: profile.burst.rapidThreshold,
      burstThreshold: profile.burst.burstThreshold,
      normalIntensity: profile.burst.normalIntensity,
      rapidIntensity: profile.burst.rapidIntensity,
      burstIntensity: profile.burst.burstIntensity,
    },
    touchZones: [...profile.touchZones],
    chance: {
      field: profile.chance.field,
      probability: profile.chance.probability,
      intensity: profile.chance.intensity,
      sound: profile.chance.sound,
    },
    feedback: {
      sound: profile.feedback.sound,
      effect: profile.feedback.effect,
    },
  };
}

function getReferencedResourceIds(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'progressive-release') {
    return { sounds: new Set([profile.feedback.sound]), effects: new Set([profile.feedback.effect]) };
  }
  if (profile.kind === 'press-release' || profile.kind === 'custom-graph') {
    return {
      sounds: new Set([
        profile.feedback?.sound,
        profile.chance?.sound,
      ].filter((value): value is string => !!value)),
      effects: new Set(profile.chance ? [profile.chance.effect] : []),
    };
  }
  if (profile.kind === 'round-choice') {
    return {
      sounds: new Set([
        profile.confirmation.sound,
        profile.reveal.userWinSound,
        profile.reveal.otherResultSound,
      ]),
      effects: new Set([profile.reveal.effect]),
    };
  }
  return {
    sounds: new Set([profile.chance.sound, profile.feedback.sound]),
    effects: new Set([profile.feedback.effect]),
  };
}

function projectInteraction(definition: AvatarToolDefinition): DesktopAvatarToolInteraction {
  const references = getReferencedResourceIds(definition.interaction);
  return {
    profile: projectProfile(definition.interaction),
    sounds: definition.sounds
      .filter(sound => references.sounds.has(sound.id))
      .map(sound => ({
        id: sound.id,
        src: projectAssetPath(sound.src),
        volume: sound.volume,
      })),
    effects: definition.effects
      .filter(effect => references.effects.has(effect.id))
      .map(projectEffect),
  } as DesktopAvatarToolInteraction;
}

export function projectDesktopAvatarToolContract(
  definition: AvatarToolDefinition | null,
  runtimePolicy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): DesktopAvatarToolContract {
  if (definition === null) {
    return desktopAvatarToolContractSchema.parse({
      wireVersion: 1,
      definition: null,
      runtimePolicy: null,
    });
  }
  const { desktopVisual, desktopInteraction } = definition.capability;
  return desktopAvatarToolContractSchema.parse({
    wireVersion: 1,
    definition: {
      definitionVersion: definition.definitionVersion,
      id: definition.id,
      capability: {
        desktopVisual,
        desktopInteraction,
      },
      visual: desktopVisual ? projectVisual(definition) : null,
      interaction: desktopInteraction ? projectInteraction(definition) : null,
    },
    runtimePolicy: desktopVisual ? avatarToolRuntimePolicySchema.parse(runtimePolicy) : null,
  });
}

export function buildDesktopAvatarToolContract(
  toolId: AvatarToolId | null,
  definition?: AvatarToolDefinition | null,
): DesktopAvatarToolContract {
  return projectDesktopAvatarToolContract(
    toolId === null ? null : definition ?? getAvatarToolRegistration(toolId).definition,
  );
}
