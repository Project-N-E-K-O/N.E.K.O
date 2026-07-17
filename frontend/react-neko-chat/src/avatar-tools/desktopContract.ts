import { z } from 'zod';
import {
  AVATAR_TOOL_ASSET_PATH_MAX_LENGTH,
  AVATAR_TOOL_DEFINITION_IDS,
  AVATAR_TOOL_INTERACTION_INTENSITIES,
  AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS,
  AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS,
  AVATAR_TOOL_TIMER_DELAY_MAX_MS,
  AVATAR_TOOL_TOUCH_ZONES,
  AVATAR_TOOL_VARIANT_IDS,
  getAvatarToolRegistration,
  hasValidAvatarToolAssetVersion,
  isAvatarToolSameOriginAssetPath,
  validateAvatarToolDefinition,
  withAvatarToolAssetVersion,
  type AvatarToolId,
  type AvatarToolContractDefinition,
  type AvatarToolContractEffectRecipe,
  type AvatarToolContractInteractionProfile,
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
const payloadFieldNameSchema = z.string().min(1).max(64).regex(/^[a-z][a-zA-Z0-9]*$/);
const payloadFieldSchema = payloadFieldNameSchema
  .refine(
    field => !(AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS as readonly string[]).includes(field),
    { message: 'payload field is reserved' },
  );
const roundChoiceFactFieldSchema = payloadFieldNameSchema.refine(
  (field): field is typeof AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS[number] => (
    (AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS as readonly string[]).includes(field)
  ),
  { message: 'payload field is not a canonical round field' },
);
const timerDelaySchema = nonNegativeNumberSchema.max(AVATAR_TOOL_TIMER_DELAY_MAX_MS);
const positiveTimerDelaySchema = positiveNumberSchema.max(AVATAR_TOOL_TIMER_DELAY_MAX_MS);
const avatarToolDefinitionIdSchema = z.enum(AVATAR_TOOL_DEFINITION_IDS);
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

const localizedLabelTextSchema = z.string().min(1).max(256).refine(value => value.trim() !== '', {
  message: 'localized label must not be blank',
});
const localizedLabelSchema = z.object({
  key: localizedLabelTextSchema,
  fallback: localizedLabelTextSchema,
}).strict();
const layoutOffsetSchema = z.object({
  x: finiteNumberSchema,
  y: finiteNumberSchema,
}).strict();
const roundRevealTimelineEntrySchema = z.object({
  phase: z.enum(['reveal', 'result', 'idle']),
  delayMs: timerDelaySchema,
}).strict();
const gestureLabelsSchema = z.record(identifierSchema, localizedLabelSchema).superRefine((labels, context) => {
  const count = Object.keys(labels).length;
  if (count === 0 || count > 64) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'gesture labels must contain between 1 and 64 entries',
    });
  }
});
const roundRevealEffectSchema = z.object({
  id: identifierSchema,
  kind: z.literal('round-reveal'),
  interactionLock: z.literal('effect-lifetime'),
  anchors: z.object({
    user: z.literal('release-pointer'),
    avatar: z.literal('avatar-head'),
  }).strict(),
  timeline: z.array(roundRevealTimelineEntrySchema).length(3),
  labels: z.object({
    gestures: gestureLabelsSchema,
    results: z.object({
      user_win: localizedLabelSchema,
      avatar_win: localizedLabelSchema,
      draw: localizedLabelSchema,
    }).strict(),
    announcement: localizedLabelSchema,
  }).strict(),
  layout: z.object({
    userOffset: layoutOffsetSchema,
    avatarOffset: layoutOffsetSchema,
    resultOffset: layoutOffsetSchema,
  }).strict(),
}).strict().superRefine((effect, context) => {
  const expected = ['reveal', 'result', 'idle'] as const;
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
        message: 'reveal must start at 0ms',
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
  actionId: identifierSchema,
  intensity: intensitySchema,
  choices: z.object({
    primary: identifierSchema,
    secondary: identifierSchema,
    tertiary: identifierSchema,
  }).strict(),
  beats: z.record(identifierSchema, identifierSchema),
  facts: z.object({
    userChoiceField: roundChoiceFactFieldSchema.and(z.literal('userGesture')),
    avatarChoiceField: roundChoiceFactFieldSchema.and(z.literal('avatarGesture')),
    resultField: roundChoiceFactFieldSchema.and(z.literal('roundResult')),
  }).strict(),
  cycle: z.object({
    outsideMs: positiveTimerDelaySchema,
    inRangeMs: positiveTimerDelaySchema,
    avatarPreviewMs: positiveTimerDelaySchema,
  }).strict(),
  random: z.object({
    strategy: z.literal('uniform'),
  }).strict(),
  presentation: z.object({
    confirmation: z.literal('render-owner-required'),
  }).strict(),
  feedback: z.object({
    effect: identifierSchema,
    confirmSound: identifierSchema,
    resultSounds: z.object({
      user_win: identifierSchema,
      avatar_win: identifierSchema,
      draw: identifierSchema,
    }).strict(),
  }).strict(),
}).strict().superRefine((profile, context) => {
  const choices = Object.values(profile.choices);
  if (new Set(choices).size !== choices.length) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['choices'],
      message: 'choices must be unique',
    });
  }
  const beatsKeys = Object.keys(profile.beats);
  const beatenChoices = choices.map(choice => profile.beats[choice]);
  if (
    beatsKeys.length !== choices.length
    || !choices.every(choice => beatsKeys.includes(choice))
    || beatenChoices.some((loser, index) => !choices.includes(loser) || loser === choices[index])
    || new Set(beatenChoices).size !== choices.length
  ) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['beats'],
      message: 'beats must form a unique three-element cycle',
    });
  }
});

const interactionProfileSchema = z.union([
  progressiveReleaseProfileSchema,
  pressReleaseProfileSchema,
  lockedImpactProfileSchema,
  roundChoiceProfileSchema,
]);

function collectInteractionReferences(profile: z.infer<typeof interactionProfileSchema>) {
  if (profile.kind === 'progressive-release') {
    return { sounds: [profile.feedback.sound], effects: [profile.feedback.effect] };
  }
  if (profile.kind === 'press-release') {
    return { sounds: [profile.chance.sound], effects: [profile.chance.effect] };
  }
  if (profile.kind === 'locked-impact') {
    return {
      sounds: [profile.chance.sound, profile.feedback.sound],
      effects: [profile.feedback.effect],
    };
  }
  return {
    sounds: [
      profile.feedback.confirmSound,
      profile.feedback.resultSounds.user_win,
      profile.feedback.resultSounds.avatar_win,
      profile.feedback.resultSounds.draw,
    ],
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
  if (interaction.profile.kind === 'round-choice') {
    const effect = interaction.effects.find(candidate => candidate.id === interaction.profile.feedback.effect);
    if (effect?.kind !== 'round-reveal') {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['profile', 'feedback', 'effect'],
        message: 'round choice must reference a round-reveal effect',
      });
      return;
    }
    const choices = Object.values(interaction.profile.choices);
    const gestureLabelKeys = Object.keys(effect.labels.gestures);
    if (
      gestureLabelKeys.length !== choices.length
      || !choices.every(choice => gestureLabelKeys.includes(choice))
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['effects', interaction.effects.indexOf(effect), 'labels', 'gestures'],
        message: 'gesture labels must match round choices',
      });
    }
  } else {
    references.effects.forEach((effectId) => {
      if (interaction.effects.find(effect => effect.id === effectId)?.kind === 'round-reveal') {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['profile'],
          message: 'round-reveal effects require a round-choice profile',
        });
      }
    });
  }
});

export const desktopAvatarToolDefinitionSchema = z.object({
  definitionVersion: z.literal(1),
  id: avatarToolDefinitionIdSchema,
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


// NEKO definition projection -------------------------------------------------

function projectAssetPath(path: string): string {
  return desktopAvatarToolAssetPathSchema.parse(withAvatarToolAssetVersion(path, '0'));
}

function projectVisual(definition: AvatarToolContractDefinition): DesktopAvatarToolVisual {
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

function unsupportedContractDiscriminator(value: never, domain: string): never {
  const kind = (value as { kind?: unknown })?.kind;
  throw new Error(`Unsupported desktop avatar tool ${domain} discriminator: ${String(kind)}`);
}

function projectEffect(effect: AvatarToolContractEffectRecipe) {
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
  if (effect.kind === 'hammer-swing') {
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
  if (effect.kind === 'round-reveal') {
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      anchors: {
        user: effect.anchors.user,
        avatar: effect.anchors.avatar,
      },
      timeline: effect.timeline.map(entry => ({ phase: entry.phase, delayMs: entry.delayMs })),
      labels: {
        gestures: Object.fromEntries(
          Object.entries(effect.labels.gestures).map(([choice, label]) => [choice, {
            key: label.key,
            fallback: label.fallback,
          }]),
        ),
        results: {
          user_win: { ...effect.labels.results.user_win },
          avatar_win: { ...effect.labels.results.avatar_win },
          draw: { ...effect.labels.results.draw },
        },
        announcement: { ...effect.labels.announcement },
      },
      layout: {
        userOffset: { ...effect.layout.userOffset },
        avatarOffset: { ...effect.layout.avatarOffset },
        resultOffset: { ...effect.layout.resultOffset },
      },
    };
  }
  return unsupportedContractDiscriminator(effect, 'effect');
}

function projectProfile(profile: AvatarToolContractInteractionProfile) {
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
    return {
      kind: profile.kind,
      actionId: profile.actionId,
      pointerDown: {
        rangeVariant: profile.pointerDown.rangeVariant,
        outsideVariant: profile.pointerDown.outsideVariant,
      },
      pointerRelease: {
        rangeVariant: profile.pointerRelease.rangeVariant,
        outsideVariant: profile.pointerRelease.outsideVariant,
      },
      burst: {
        windowMs: profile.burst.windowMs,
        rapidThreshold: profile.burst.rapidThreshold,
        normalIntensity: profile.burst.normalIntensity,
        rapidIntensity: profile.burst.rapidIntensity,
      },
      touchZone: profile.touchZone,
      touchZones: [...profile.touchZones],
      chance: {
        field: profile.chance.field,
        probability: profile.chance.probability,
        sound: profile.chance.sound,
        effect: profile.chance.effect,
      },
    };
  }
  if (profile.kind === 'locked-impact') {
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
  if (profile.kind === 'round-choice') {
    return {
      kind: profile.kind,
      actionId: profile.actionId,
      intensity: profile.intensity,
      choices: {
        primary: profile.choices.primary,
        secondary: profile.choices.secondary,
        tertiary: profile.choices.tertiary,
      },
      beats: { ...profile.beats },
      facts: {
        userChoiceField: profile.facts.userChoiceField,
        avatarChoiceField: profile.facts.avatarChoiceField,
        resultField: profile.facts.resultField,
      },
      cycle: {
        outsideMs: profile.cycle.outsideMs,
        inRangeMs: profile.cycle.inRangeMs,
        avatarPreviewMs: profile.cycle.avatarPreviewMs,
      },
      random: { strategy: profile.random.strategy },
      presentation: { confirmation: profile.presentation.confirmation },
      feedback: {
        effect: profile.feedback.effect,
        confirmSound: profile.feedback.confirmSound,
        resultSounds: {
          user_win: profile.feedback.resultSounds.user_win,
          avatar_win: profile.feedback.resultSounds.avatar_win,
          draw: profile.feedback.resultSounds.draw,
        },
      },
    };
  }
  return unsupportedContractDiscriminator(profile, 'profile');
}

function getReferencedResourceIds(profile: AvatarToolContractInteractionProfile) {
  if (profile.kind === 'progressive-release') {
    return { sounds: new Set([profile.feedback.sound]), effects: new Set([profile.feedback.effect]) };
  }
  if (profile.kind === 'press-release') {
    return { sounds: new Set([profile.chance.sound]), effects: new Set([profile.chance.effect]) };
  }
  if (profile.kind === 'locked-impact') {
    return {
      sounds: new Set<string>([profile.chance.sound, profile.feedback.sound]),
      effects: new Set<string>([profile.feedback.effect]),
    };
  }
  if (profile.kind === 'round-choice') {
    return {
      sounds: new Set<string>([
        profile.feedback.confirmSound,
        profile.feedback.resultSounds.user_win,
        profile.feedback.resultSounds.avatar_win,
        profile.feedback.resultSounds.draw,
      ]),
      effects: new Set<string>([profile.feedback.effect]),
    };
  }
  return unsupportedContractDiscriminator(profile, 'profile');
}

function projectInteraction(definition: AvatarToolContractDefinition): DesktopAvatarToolInteraction {
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
  };
}

export function projectDesktopAvatarToolContract(
  definition: AvatarToolContractDefinition | null,
  runtimePolicy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): DesktopAvatarToolContract {
  if (definition === null) {
    return desktopAvatarToolContractSchema.parse({
      wireVersion: 1,
      definition: null,
      runtimePolicy: null,
    });
  }
  validateAvatarToolDefinition(definition);
  const { desktopVisual, desktopInteraction } = definition.capability;
  return desktopAvatarToolContractSchema.parse({
    wireVersion: 1,
    definition: {
      definitionVersion: 1,
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
): DesktopAvatarToolContract {
  return projectDesktopAvatarToolContract(
    toolId === null ? null : getAvatarToolRegistration(toolId).definition,
  );
}
