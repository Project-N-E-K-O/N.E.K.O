import { z } from 'zod';
import {
  AVATAR_TOOL_INTERACTION_INTENSITIES,
  AVATAR_TOOL_TOUCH_ZONES,
  AVATAR_TOOL_VARIANT_IDS,
} from './catalog';
import { avatarToolRuntimePolicySchema } from './interactionPolicy';

const finiteNumberSchema = z.number().finite();
const nonNegativeNumberSchema = finiteNumberSchema.nonnegative();
const positiveNumberSchema = finiteNumberSchema.positive();
const positiveIntegerSchema = z.number().int().positive();
const probabilitySchema = finiteNumberSchema.min(0).max(1);
const identifierSchema = z.string().min(1).max(64).regex(/^[a-z][a-z0-9_-]*$/);
const actionIdentifierSchema = z.string().min(1).max(64).regex(/^[a-z][a-z0-9_-]*$/);
const payloadFieldSchema = z.string().min(1).max(64).regex(/^[a-z][a-zA-Z0-9]*$/)
  .refine(field => ![
    'interactionId', 'target', 'pointer', 'textContext', 'timestamp',
    'toolId', 'actionId', 'intensity', 'touchZone',
  ].includes(field), { message: 'payload field is reserved' });
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

export const desktopAvatarToolAssetPathSchema = z.string().min(1).max(2048)
  .refine(path => path.startsWith('/') && !path.startsWith('//') && !path.includes('\\'), {
    message: 'asset path must be a same-origin absolute path',
  })
  .refine((path) => {
    try {
      const parsed = new URL(path, 'https://neko.invalid');
      const versions = parsed.searchParams.getAll('v');
      return parsed.origin === 'https://neko.invalid'
        && parsed.hash === ''
        && versions.length === 1
        && versions[0].trim() !== '';
    } catch {
      return false;
    }
  }, { message: 'asset path must contain exactly one non-empty version parameter and no fragment' });

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
  kind: z.literal('fixed-particles-v1'),
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
  kind: z.literal('random-scatter-v1'),
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
  kind: z.literal('hammer-swing-v1'),
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

const effectRecipeSchema = z.union([
  fixedParticlesEffectSchema,
  randomScatterEffectSchema,
  hammerSwingEffectSchema,
]);

const progressiveReleaseProfileSchema = z.object({
  kind: z.literal('progressive-release-v1'),
  stages: z.array(z.object({
    variant: avatarToolVariantIdSchema,
    actionId: actionIdentifierSchema,
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
  kind: z.literal('press-release-v1'),
  actionId: actionIdentifierSchema,
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

const lockedImpactProfileSchema = z.object({
  kind: z.literal('locked-impact-v1'),
  actionId: actionIdentifierSchema,
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
});

const interactionProfileSchema = z.union([
  progressiveReleaseProfileSchema,
  pressReleaseProfileSchema,
  lockedImpactProfileSchema,
]);

function collectInteractionReferences(profile: z.infer<typeof interactionProfileSchema>) {
  if (profile.kind === 'progressive-release-v1') {
    return { sounds: [profile.feedback.sound], effects: [profile.feedback.effect] };
  }
  if (profile.kind === 'press-release-v1') {
    return { sounds: [profile.chance.sound], effects: [profile.chance.effect] };
  }
  return {
    sounds: [profile.chance.sound, profile.feedback.sound],
    effects: [profile.feedback.effect],
  };
}

export const desktopAvatarToolInteractionSchema = z.object({
  profile: interactionProfileSchema,
  sounds: z.array(soundResourceSchema).min(1).max(16),
  effects: z.array(effectRecipeSchema).min(1).max(16),
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

export const desktopAvatarToolDefinitionSchema = z.object({
  definitionVersion: z.literal(1),
  id: identifierSchema,
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
});

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
export type DesktopAvatarToolInteraction = z.infer<typeof desktopAvatarToolInteractionSchema>;
export type DesktopAvatarToolContract = z.infer<typeof desktopAvatarToolContractSchema>;
