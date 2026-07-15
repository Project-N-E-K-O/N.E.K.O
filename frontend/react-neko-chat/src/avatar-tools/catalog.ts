import type {
  AvatarToolCommand,
  AvatarToolRuleContext,
  AvatarToolRuleHandlers,
} from './interaction';

export const AVATAR_TOOL_DEFINITION_IDS = ['lollipop', 'fist', 'hammer'] as const;
export const AVATAR_TOOL_VARIANT_IDS = ['primary', 'secondary', 'tertiary'] as const;
export const AVATAR_TOOL_INTERACTION_INTENSITIES = ['normal', 'rapid', 'burst', 'easter_egg'] as const;
export const AVATAR_TOOL_TOUCH_ZONES = ['ear', 'head', 'face', 'body'] as const;

declare global {
  interface Window {
    __NEKO_REACT_CHAT_ASSET_VERSION__?: string;
  }
}
function getReactChatAssetVersion(): string {
  if (typeof window === 'undefined') return '';
  const version = window.__NEKO_REACT_CHAT_ASSET_VERSION__;
  return typeof version === 'string' ? version.trim() : '';
}

export function withAvatarToolAssetVersion(path: string, fallbackVersion = ''): string {
  const version = getReactChatAssetVersion() || fallbackVersion.trim();
  if (!version || !path) return path;
  const hashIndex = path.indexOf('#');
  const pathAndQuery = hashIndex >= 0 ? path.slice(0, hashIndex) : path;
  const hash = hashIndex >= 0 ? path.slice(hashIndex) : '';
  const queryIndex = pathAndQuery.indexOf('?');
  const pathname = queryIndex >= 0 ? pathAndQuery.slice(0, queryIndex) : pathAndQuery;
  const search = queryIndex >= 0 ? pathAndQuery.slice(queryIndex + 1) : '';
  const params = search.split('&').filter(Boolean).filter((entry) => {
    const encodedName = entry.split('=', 1)[0];
    try {
      return decodeURIComponent(encodedName.replace(/\+/g, ' ')) !== 'v';
    } catch {
      return true;
    }
  });
  params.push(`v=${encodeURIComponent(version)}`);
  return `${pathname}?${params.join('&')}${hash}`;
}

export type AvatarToolDefinitionId = typeof AVATAR_TOOL_DEFINITION_IDS[number];
export type AvatarToolVariantId = typeof AVATAR_TOOL_VARIANT_IDS[number];
export type AvatarToolDefinitionIntensity = typeof AVATAR_TOOL_INTERACTION_INTENSITIES[number];
export type AvatarToolTouchZone = typeof AVATAR_TOOL_TOUCH_ZONES[number];
export type AvatarToolDefinitionSound = string;
export type AvatarToolDefinitionEffect = string;

export type AvatarToolRenderedAnchor = {
  x: number;
  y: number;
  coordinateSpace: 'final-css-pixel';
};

export type AvatarToolSoundResource = {
  id: AvatarToolDefinitionSound;
  src: string;
  volume: number;
};

export type FixedParticleEffectRecipe = {
  id: string;
  kind: 'fixed-particles-v1';
  interactionLock: 'none';
  lifetimeMs: number;
  glyph: string;
  particles: ReadonlyArray<{
    offsetX: number;
    offsetY: number;
    driftX: number;
    driftY: number;
    scale: number;
    delayMs: number;
  }>;
};

export type RandomScatterEffectRecipe = {
  id: string;
  kind: 'random-scatter-v1';
  interactionLock: 'none';
  assetPath: string;
  count: number;
  lifetimeMs: number;
  angleDeg: { min: number; range: number };
  distance: { min: number; range: number };
  offsetX: { min: number; range: number };
  offsetY: { min: number; range: number };
  rotation: { min: number; range: number };
  scale: { min: number; range: number };
  delayMs: { min: number; range: number };
};

export type HammerSwingPhase = 'idle' | 'windup' | 'swing' | 'impact' | 'recover';

export type HammerSwingEffectRecipe = {
  id: string;
  kind: 'hammer-swing-v1';
  interactionLock: 'effect-lifetime';
  anchor: {
    source: 'live-pointer';
    visualMode: 'inRange';
  };
  transformOrigin: { x: number; y: number };
  impactRegistration: {
    transformOrigin: { x: number; y: number };
    translate: { x: number; y: number };
    rotationDeg: number;
    scale: number;
  };
  variants: {
    idle: AvatarToolVariantId;
    impact: AvatarToolVariantId;
  };
  timeline: ReadonlyArray<{
    phase: HammerSwingPhase;
    delayMs: number;
  }>;
  easterEgg: {
    mode: 'easter-egg';
    scale: number;
    anchorOffset: { x: number; y: number };
  };
};

export type AvatarToolEffectRecipe =
  | FixedParticleEffectRecipe
  | RandomScatterEffectRecipe
  | HammerSwingEffectRecipe;

export type AvatarToolVisualVariant = {
  iconImagePath: string;
  pointerImagePath: string;
  menuOffsetX: number;
  menuOffsetY: number;
};

export type AvatarToolVariantSource = 'range' | 'outside' | 'primary';

export type AvatarToolVisualDefinition = {
  initialVariant: AvatarToolVariantId;
  variants: Record<AvatarToolVariantId, AvatarToolVisualVariant>;
  presentation: {
    inRangeVariantSource: AvatarToolVariantSource;
    outsideVariantSource: AvatarToolVariantSource;
    effectActiveImageKind: 'pointer' | 'icon';
  };
  menuScale: number;
  hotspotX: number;
  hotspotY: number;
  naturalWidth: number;
  naturalHeight: number;
  pointer: {
    displayWidth: number;
    displayHeight: number;
    displayCoordinateSpace: 'pre-scale-css-pixel';
    scale: number;
    renderedAnchor: AvatarToolRenderedAnchor;
  };
  inRange: {
    displayWidth: number;
    displayHeight: number;
    displayCoordinateSpace: 'pre-scale-css-pixel';
    scale: number;
    renderedAnchor: AvatarToolRenderedAnchor;
  };
};

export type ProgressiveReleaseProfile = {
  kind: 'progressive-release-v1';
  stages: ReadonlyArray<{
    variant: AvatarToolVariantId;
    actionId: string;
    intensity: AvatarToolDefinitionIntensity;
    nextVariant: AvatarToolVariantId | null;
  }>;
  burst: {
    key: string;
    variant: AvatarToolVariantId;
    windowMs: number;
    threshold: number;
    belowThresholdIntensity: AvatarToolDefinitionIntensity;
    thresholdIntensity: AvatarToolDefinitionIntensity;
  };
  feedback: {
    sound: AvatarToolDefinitionSound;
    effect: AvatarToolDefinitionEffect;
    effectVariant: AvatarToolVariantId;
  };
};

export type PressReleaseProfile = {
  kind: 'press-release-v1';
  actionId: string;
  pointerDown: {
    rangeVariant: AvatarToolVariantId;
    outsideVariant: AvatarToolVariantId;
  };
  pointerRelease: {
    rangeVariant: AvatarToolVariantId;
    outsideVariant: AvatarToolVariantId;
  };
  burst: {
    key: string;
    windowMs: number;
    rapidThreshold: number;
    normalIntensity: AvatarToolDefinitionIntensity;
    rapidIntensity: AvatarToolDefinitionIntensity;
  };
  touchZone: 'release';
  touchZones: ReadonlyArray<AvatarToolTouchZone>;
  chance: {
    field: string;
    probability: number;
    sound: AvatarToolDefinitionSound;
    effect: AvatarToolDefinitionEffect;
  };
};

export type LockedImpactProfile = {
  kind: 'locked-impact-v1';
  actionId: string;
  touchZone: 'release';
  outsideFeedback: {
    variant: AvatarToolVariantId;
    resetAfterMs: number;
  };
  burst: {
    key: string;
    windowMs: number;
    rapidThreshold: number;
    burstThreshold: number;
    normalIntensity: AvatarToolDefinitionIntensity;
    rapidIntensity: AvatarToolDefinitionIntensity;
    burstIntensity: AvatarToolDefinitionIntensity;
  };
  touchZones: ReadonlyArray<AvatarToolTouchZone>;
  chance: {
    field: string;
    probability: number;
    intensity: 'easter_egg';
    sound: AvatarToolDefinitionSound;
  };
  feedback: {
    sound: AvatarToolDefinitionSound;
    effect: AvatarToolDefinitionEffect;
  };
};

export type AvatarToolInteractionProfile =
  | ProgressiveReleaseProfile
  | PressReleaseProfile
  | LockedImpactProfile;

export type AvatarToolDefinition = {
  definitionVersion: 1;
  id: AvatarToolDefinitionId;
  label: {
    key: string;
    fallback: string;
  };
  capability: {
    desktopVisual: boolean;
    desktopInteraction: boolean;
  };
  visual: AvatarToolVisualDefinition;
  sounds: ReadonlyArray<AvatarToolSoundResource>;
  effects: ReadonlyArray<AvatarToolEffectRecipe>;
  interaction: AvatarToolInteractionProfile;
};

export type AvatarToolRegistration = {
  definition: AvatarToolDefinition;
  handlers: AvatarToolRuleHandlers;
};

function fail(definition: AvatarToolDefinition, reason: string): never {
  throw new Error(`Invalid avatar tool definition "${String(definition?.id)}": ${reason}`);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function assertFinite(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value)) fail(definition, `${field} must be finite`);
}

function assertPositive(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value) || value <= 0) fail(definition, `${field} must be positive`);
}

function assertPositiveInteger(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!Number.isInteger(value) || Number(value) <= 0) fail(definition, `${field} must be a positive integer`);
}

function assertProbability(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value) || value < 0 || value > 1) {
    fail(definition, `${field} must be between 0 and 1`);
  }
}

function assertNonEmpty(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (typeof value !== 'string' || value.trim() === '') fail(definition, `${field} must be non-empty`);
}

function assertVariant(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!AVATAR_TOOL_VARIANT_IDS.includes(value as never)) {
    fail(definition, `${field} must be a supported variant`);
  }
}

function assertIntensity(definition: AvatarToolDefinition, value: unknown, field: string) {
  if (!AVATAR_TOOL_INTERACTION_INTENSITIES.includes(value as never)) {
    fail(definition, `${field} must be a supported intensity`);
  }
}

function assertTouchZones(
  definition: AvatarToolDefinition,
  value: ReadonlyArray<AvatarToolTouchZone> | undefined,
  field: string,
) {
  if (
    !Array.isArray(value)
    || value.length === 0
    || value.length > AVATAR_TOOL_TOUCH_ZONES.length
    || new Set(value).size !== value.length
    || !value.every(zone => AVATAR_TOOL_TOUCH_ZONES.includes(zone))
  ) {
    fail(definition, `${field} must be a non-empty unique subset of the supported touch zones`);
  }
}

function validateVisual(definition: AvatarToolDefinition) {
  const { visual } = definition;
  if (!visual || typeof visual !== 'object') fail(definition, 'visual is required');
  assertVariant(definition, visual.initialVariant, 'visual.initialVariant');
  const variantKeys = Object.keys(visual.variants ?? {});
  if (
    variantKeys.length !== AVATAR_TOOL_VARIANT_IDS.length
    || !AVATAR_TOOL_VARIANT_IDS.every(variant => variantKeys.includes(variant))
  ) {
    fail(definition, 'visual.variants must contain primary, secondary and tertiary exactly once');
  }
  AVATAR_TOOL_VARIANT_IDS.forEach((variant) => {
    const asset = visual.variants[variant];
    assertNonEmpty(definition, asset?.iconImagePath, `visual.variants.${variant}.iconImagePath`);
    assertNonEmpty(definition, asset?.pointerImagePath, `visual.variants.${variant}.pointerImagePath`);
    assertFinite(definition, asset?.menuOffsetX, `visual.variants.${variant}.menuOffsetX`);
    assertFinite(definition, asset?.menuOffsetY, `visual.variants.${variant}.menuOffsetY`);
  });
  const presentation = visual.presentation;
  const sources = ['range', 'outside', 'primary'];
  if (!sources.includes(presentation?.inRangeVariantSource)) {
    fail(definition, 'visual.presentation.inRangeVariantSource is invalid');
  }
  if (!sources.includes(presentation?.outsideVariantSource)) {
    fail(definition, 'visual.presentation.outsideVariantSource is invalid');
  }
  if (!['pointer', 'icon'].includes(presentation?.effectActiveImageKind)) {
    fail(definition, 'visual.presentation.effectActiveImageKind is invalid');
  }
  assertPositive(definition, visual.menuScale, 'visual.menuScale');
  assertFinite(definition, visual.hotspotX, 'visual.hotspotX');
  assertFinite(definition, visual.hotspotY, 'visual.hotspotY');
  assertPositive(definition, visual.naturalWidth, 'visual.naturalWidth');
  assertPositive(definition, visual.naturalHeight, 'visual.naturalHeight');
  (['pointer', 'inRange'] as const).forEach((mode) => {
    assertPositive(definition, visual[mode]?.displayWidth, `visual.${mode}.displayWidth`);
    assertPositive(definition, visual[mode]?.displayHeight, `visual.${mode}.displayHeight`);
    if (visual[mode]?.displayCoordinateSpace !== 'pre-scale-css-pixel') {
      fail(definition, `visual.${mode}.displayCoordinateSpace must be pre-scale-css-pixel`);
    }
    assertPositive(definition, visual[mode]?.scale, `visual.${mode}.scale`);
    assertFinite(definition, visual[mode]?.renderedAnchor?.x, `visual.${mode}.renderedAnchor.x`);
    assertFinite(definition, visual[mode]?.renderedAnchor?.y, `visual.${mode}.renderedAnchor.y`);
    if (visual[mode]?.renderedAnchor?.coordinateSpace !== 'final-css-pixel') {
      fail(definition, `visual.${mode}.renderedAnchor.coordinateSpace must be final-css-pixel`);
    }
  });
}

function validateSounds(definition: AvatarToolDefinition) {
  if (!Array.isArray(definition.sounds) || definition.sounds.length === 0) {
    fail(definition, 'sounds must contain at least one resource');
  }
  const resourcesById = new Map<string, AvatarToolSoundResource>();
  definition.sounds.forEach((sound, index) => {
    assertNonEmpty(definition, sound?.id, `sounds[${index}].id`);
    assertNonEmpty(definition, sound.src, `sounds[${index}].src`);
    assertProbability(definition, sound.volume, `sounds[${index}].volume`);
    const existing = resourcesById.get(sound.id);
    if (existing && (existing.src !== sound.src || existing.volume !== sound.volume)) {
      fail(definition, `sound ${sound.id} conflicts with another resource`);
    }
    resourcesById.set(sound.id, sound);
  });
}

function validateRange(
  definition: AvatarToolDefinition,
  range: { min: number; range: number } | undefined,
  field: string,
) {
  assertFinite(definition, range?.min, `${field}.min`);
  assertFinite(definition, range?.range, `${field}.range`);
  if (Number(range?.range) < 0) fail(definition, `${field}.range must not be negative`);
}

function validateEffects(definition: AvatarToolDefinition) {
  if (!Array.isArray(definition.effects)) fail(definition, 'effects must be an array');
  const ids = new Set<string>();
  definition.effects.forEach((effect: AvatarToolEffectRecipe, index: number) => {
    assertNonEmpty(definition, effect?.id, `effects[${index}].id`);
    if (ids.has(effect.id)) fail(definition, `effect ${effect.id} is duplicated`);
    ids.add(effect.id);
    if (effect.kind === 'fixed-particles-v1') {
      if (effect.interactionLock !== 'none') fail(definition, 'fixed-particles-v1 must not lock interaction');
      assertPositive(definition, effect.lifetimeMs, 'effects.hearts.lifetimeMs');
      assertNonEmpty(definition, effect.glyph, 'effects.hearts.glyph');
      if (!Array.isArray(effect.particles) || effect.particles.length === 0) {
        fail(definition, 'effects.hearts.particles must not be empty');
      }
      effect.particles.forEach((
        particle: FixedParticleEffectRecipe['particles'][number],
        particleIndex: number,
      ) => {
        ['offsetX', 'offsetY', 'driftX', 'driftY', 'delayMs'].forEach(field =>
          assertFinite(definition, particle?.[field as keyof typeof particle], `effects.hearts.particles[${particleIndex}].${field}`));
        assertPositive(definition, particle?.scale, `effects.hearts.particles[${particleIndex}].scale`);
        if (particle.delayMs < 0) fail(definition, 'heart particle delayMs must not be negative');
      });
      return;
    }
    if (effect.kind === 'random-scatter-v1') {
      if (effect.interactionLock !== 'none') fail(definition, 'random-scatter-v1 must not lock interaction');
      assertNonEmpty(definition, effect.assetPath, 'effects.reward-drops.assetPath');
      assertPositiveInteger(definition, effect.count, 'effects.reward-drops.count');
      assertPositive(definition, effect.lifetimeMs, 'effects.reward-drops.lifetimeMs');
      validateRange(definition, effect.angleDeg, 'effects.reward-drops.angleDeg');
      validateRange(definition, effect.distance, 'effects.reward-drops.distance');
      validateRange(definition, effect.offsetX, 'effects.reward-drops.offsetX');
      validateRange(definition, effect.offsetY, 'effects.reward-drops.offsetY');
      validateRange(definition, effect.rotation, 'effects.reward-drops.rotation');
      validateRange(definition, effect.scale, 'effects.reward-drops.scale');
      validateRange(definition, effect.delayMs, 'effects.reward-drops.delayMs');
      if (effect.scale.min <= 0) fail(definition, 'effects.reward-drops.scale.min must be positive');
      if (effect.distance.min <= 0) fail(definition, 'effects.reward-drops.distance.min must be positive');
      if (effect.delayMs.min < 0) fail(definition, 'effects.reward-drops.delayMs.min must not be negative');
      return;
    }
    if (effect.kind === 'hammer-swing-v1') {
      if (effect.interactionLock !== 'effect-lifetime') {
        fail(definition, 'hammer-swing-v1 must lock interaction for its effect lifetime');
      }
      if (effect.anchor?.source !== 'live-pointer') {
        fail(definition, 'effects.hammer-swing.anchor.source must be live-pointer');
      }
      if (effect.anchor?.visualMode !== 'inRange') {
        fail(definition, 'effects.hammer-swing.anchor.visualMode must be inRange');
      }
      assertFinite(definition, effect.transformOrigin?.x, 'effects.hammer-swing.transformOrigin.x');
      assertFinite(definition, effect.transformOrigin?.y, 'effects.hammer-swing.transformOrigin.y');
      assertFinite(
        definition,
        effect.impactRegistration?.transformOrigin?.x,
        'effects.hammer-swing.impactRegistration.transformOrigin.x',
      );
      assertFinite(
        definition,
        effect.impactRegistration?.transformOrigin?.y,
        'effects.hammer-swing.impactRegistration.transformOrigin.y',
      );
      assertFinite(
        definition,
        effect.impactRegistration?.translate?.x,
        'effects.hammer-swing.impactRegistration.translate.x',
      );
      assertFinite(
        definition,
        effect.impactRegistration?.translate?.y,
        'effects.hammer-swing.impactRegistration.translate.y',
      );
      assertFinite(
        definition,
        effect.impactRegistration?.rotationDeg,
        'effects.hammer-swing.impactRegistration.rotationDeg',
      );
      assertPositive(
        definition,
        effect.impactRegistration?.scale,
        'effects.hammer-swing.impactRegistration.scale',
      );
      assertVariant(definition, effect.variants?.idle, 'effects.hammer-swing.variants.idle');
      assertVariant(definition, effect.variants?.impact, 'effects.hammer-swing.variants.impact');
      const expectedPhases = ['windup', 'swing', 'impact', 'recover', 'idle'];
      const timeline: HammerSwingEffectRecipe['timeline'] = effect.timeline ?? [];
      if (
        timeline.length !== expectedPhases.length
        || timeline.some((entry, timelineIndex) => entry.phase !== expectedPhases[timelineIndex])
      ) {
        fail(definition, 'hammer timeline must contain windup, swing, impact, recover and idle in order');
      }
      timeline.forEach((entry, timelineIndex) => {
        assertFinite(definition, entry.delayMs, `effects.hammer-swing.timeline[${timelineIndex}].delayMs`);
        if (entry.delayMs < 0) fail(definition, 'hammer timeline delays must not be negative');
        if (timelineIndex === 0 && entry.delayMs !== 0) fail(definition, 'hammer windup must start at 0ms');
        if (timelineIndex > 0 && entry.delayMs <= timeline[timelineIndex - 1].delayMs) {
          fail(definition, 'hammer timeline delays after windup must be strictly increasing');
        }
      });
      if (effect.easterEgg?.mode !== 'easter-egg') {
        fail(definition, 'effects.hammer-swing.easterEgg.mode must be easter-egg');
      }
      assertPositive(definition, effect.easterEgg?.scale, 'effects.hammer-swing.easterEgg.scale');
      assertFinite(definition, effect.easterEgg?.anchorOffset?.x, 'effects.hammer-swing.easterEgg.anchorOffset.x');
      assertFinite(definition, effect.easterEgg?.anchorOffset?.y, 'effects.hammer-swing.easterEgg.anchorOffset.y');
      return;
    }
    fail(definition, `effects[${index}].kind is unsupported`);
  });
}

function validateInteractionReferences(definition: AvatarToolDefinition) {
  const soundIds = new Set(definition.sounds.map(sound => sound.id));
  const effectIds = new Set(definition.effects.map(effect => effect.id));
  const requireSound = (sound: AvatarToolDefinitionSound) => {
    if (!soundIds.has(sound)) fail(definition, `interaction references missing sound ${sound}`);
  };
  const requireEffect = (effect: AvatarToolDefinitionEffect) => {
    if (!effectIds.has(effect)) fail(definition, `interaction references missing effect ${effect}`);
  };
  const interaction = definition.interaction;
  assertNonEmpty(definition, interaction?.kind, 'interaction.kind');
  if (interaction.kind === 'progressive-release-v1') {
    const stages = interaction.stages ?? [];
    const variants = stages.map(stage => stage.variant);
    if (
      stages.length !== AVATAR_TOOL_VARIANT_IDS.length
      || new Set(variants).size !== AVATAR_TOOL_VARIANT_IDS.length
      || !AVATAR_TOOL_VARIANT_IDS.every(variant => variants.includes(variant))
    ) {
      fail(definition, 'progressive stages must cover every variant exactly once');
    }
    stages.forEach((stage, index) => {
      assertVariant(definition, stage.variant, `interaction.stages[${index}].variant`);
      assertNonEmpty(definition, stage.actionId, `interaction.stages[${index}].actionId`);
      assertIntensity(definition, stage.intensity, `interaction.stages[${index}].intensity`);
      if (stage.nextVariant !== null) {
        assertVariant(definition, stage.nextVariant, `interaction.stages[${index}].nextVariant`);
      }
    });
    assertVariant(definition, interaction.burst.variant, 'interaction.burst.variant');
    assertNonEmpty(definition, interaction.burst.key, 'interaction.burst.key');
    assertPositive(definition, interaction.burst.windowMs, 'interaction.burst.windowMs');
    assertPositiveInteger(definition, interaction.burst.threshold, 'interaction.burst.threshold');
    assertIntensity(
      definition,
      interaction.burst.belowThresholdIntensity,
      'interaction.burst.belowThresholdIntensity',
    );
    assertIntensity(
      definition,
      interaction.burst.thresholdIntensity,
      'interaction.burst.thresholdIntensity',
    );
    requireSound(interaction.feedback.sound);
    requireEffect(interaction.feedback.effect);
    assertVariant(definition, interaction.feedback.effectVariant, 'interaction.feedback.effectVariant');
    return;
  }
  assertNonEmpty(definition, interaction.actionId, 'interaction.actionId');
  assertNonEmpty(definition, interaction.burst.key, 'interaction.burst.key');
  assertPositive(definition, interaction.burst.windowMs, 'interaction.burst.windowMs');
  assertPositiveInteger(definition, interaction.burst.rapidThreshold, 'interaction.burst.rapidThreshold');
  assertIntensity(definition, interaction.burst.normalIntensity, 'interaction.burst.normalIntensity');
  assertIntensity(definition, interaction.burst.rapidIntensity, 'interaction.burst.rapidIntensity');
  assertTouchZones(definition, interaction.touchZones, 'interaction.touchZones');
  assertNonEmpty(definition, interaction.chance.field, 'interaction.chance.field');
  if (
    interaction.chance.field.length > 64
    || !/^[a-z][a-zA-Z0-9]*$/.test(interaction.chance.field)
  ) {
    fail(definition, 'interaction.chance.field must be a camel-case payload field of at most 64 characters');
  }
  if (['interactionId', 'target', 'pointer', 'textContext', 'timestamp', 'toolId', 'actionId', 'intensity', 'touchZone']
    .includes(interaction.chance.field)) {
    fail(definition, 'interaction.chance.field conflicts with a reserved payload field');
  }
  assertProbability(definition, interaction.chance.probability, 'interaction.chance.probability');
  if (interaction.kind === 'press-release-v1') {
    assertVariant(definition, interaction.pointerDown.rangeVariant, 'interaction.pointerDown.rangeVariant');
    assertVariant(definition, interaction.pointerDown.outsideVariant, 'interaction.pointerDown.outsideVariant');
    assertVariant(definition, interaction.pointerRelease.rangeVariant, 'interaction.pointerRelease.rangeVariant');
    assertVariant(definition, interaction.pointerRelease.outsideVariant, 'interaction.pointerRelease.outsideVariant');
    requireSound(interaction.chance.sound);
    requireEffect(interaction.chance.effect);
    return;
  }
  if (interaction.kind === 'locked-impact-v1') {
    assertPositiveInteger(definition, interaction.burst.burstThreshold, 'interaction.burst.burstThreshold');
    assertIntensity(definition, interaction.burst.burstIntensity, 'interaction.burst.burstIntensity');
    assertIntensity(definition, interaction.chance.intensity, 'interaction.chance.intensity');
    if (interaction.burst.rapidThreshold > interaction.burst.burstThreshold) {
      fail(definition, 'interaction burst thresholds are out of order');
    }
    assertVariant(definition, interaction.outsideFeedback.variant, 'interaction.outsideFeedback.variant');
    assertPositive(definition, interaction.outsideFeedback.resetAfterMs, 'interaction.outsideFeedback.resetAfterMs');
    requireSound(interaction.chance.sound);
    requireSound(interaction.feedback.sound);
    requireEffect(interaction.feedback.effect);
    return;
  }
  fail(definition, 'interaction.kind is unsupported');
}

export function validateAvatarToolDefinition(definition: AvatarToolDefinition): void {
  if (!definition || typeof definition !== 'object') throw new Error('Invalid avatar tool definition');
  if (definition.definitionVersion !== 1) fail(definition, 'definitionVersion must be 1');
  if (!AVATAR_TOOL_DEFINITION_IDS.includes(definition.id as never)) fail(definition, 'id is unsupported');
  assertNonEmpty(definition, definition.label?.key, 'label.key');
  assertNonEmpty(definition, definition.label?.fallback, 'label.fallback');
  if (
    typeof definition.capability?.desktopVisual !== 'boolean'
    || typeof definition.capability?.desktopInteraction !== 'boolean'
  ) {
    fail(definition, 'capability flags must be boolean');
  }
  validateVisual(definition);
  validateSounds(definition);
  validateEffects(definition);
  validateInteractionReferences(definition);
}

// Lollipop -------------------------------------------------------------------

export const LOLLIPOP_HEART_EFFECT_RECIPE = {
  id: 'hearts',
  kind: 'fixed-particles-v1',
  interactionLock: 'none',
  lifetimeMs: 2100,
  glyph: '*',
  particles: [
    { offsetX: -12, offsetY: -26, driftX: -26, driftY: -124, scale: 0.92, delayMs: 0 },
    { offsetX: 10, offsetY: -20, driftX: 24, driftY: -138, scale: 1.06, delayMs: 110 },
    { offsetX: -4, offsetY: -40, driftX: -18, driftY: -154, scale: 0.84, delayMs: 190 },
  ],
} as const satisfies FixedParticleEffectRecipe;

export const LOLLIPOP_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'lollipop',
  label: {
    key: 'chat.toolLollipop',
    fallback: '棒棒糖',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/chat_sugar1.png',
        pointerImagePath: '/static/icons/chat_sugar1_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/icons/chat_sugar2.png',
        pointerImagePath: '/static/icons/chat_sugar2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/icons/chat_sugar3.png',
        pointerImagePath: '/static/icons/chat_sugar2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
    },
    presentation: {
      inRangeVariantSource: 'range',
      outsideVariantSource: 'range',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1.18,
    hotspotX: 27,
    hotspotY: 46,
    naturalWidth: 55,
    naturalHeight: 80,
    pointer: {
      displayWidth: 74,
      displayHeight: 108,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.56,
      renderedAnchor: {
        x: 20.34327272727273,
        y: 34.776,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 74,
      displayHeight: 108,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 36.32727272727273,
        y: 62.1,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'lollipop-bite',
      src: '/static/sounds/avatar-tools/lollipop-bite.mp3',
      volume: 0.9,
    },
  ],
  effects: [LOLLIPOP_HEART_EFFECT_RECIPE],
  interaction: {
    kind: 'progressive-release-v1',
    stages: [
      { variant: 'primary', actionId: 'offer', intensity: 'normal', nextVariant: 'secondary' },
      { variant: 'secondary', actionId: 'tease', intensity: 'normal', nextVariant: 'tertiary' },
      { variant: 'tertiary', actionId: 'tap_soft', intensity: 'rapid', nextVariant: null },
    ],
    burst: {
      key: 'lollipop',
      variant: 'tertiary',
      windowMs: 1800,
      threshold: 4,
      belowThresholdIntensity: 'rapid',
      thresholdIntensity: 'burst',
    },
    feedback: {
      sound: 'lollipop-bite',
      effect: 'hearts',
      effectVariant: 'tertiary',
    },
  },
} as const satisfies AvatarToolDefinition;

const LOLLIPOP_INTERACTION = LOLLIPOP_AVATAR_TOOL_DEFINITION.interaction;

export const LOLLIPOP_BURST_WINDOW_MS = LOLLIPOP_INTERACTION.burst.windowMs;

function isLollipopFeedbackVariant(variant: AvatarToolVariantId): boolean {
  return variant === LOLLIPOP_INTERACTION.feedback.effectVariant;
}

export function resolveLollipopInteraction(currentVariant: AvatarToolVariantId, tapCount: number) {
  const stage = LOLLIPOP_INTERACTION.stages.find(candidate => candidate.variant === currentVariant);
  if (!stage) return null;
  switch (stage.variant) {
    case 'primary':
      return {
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
    case 'secondary':
      return {
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
    case 'tertiary':
      return {
        actionId: stage.actionId,
        intensity: tapCount >= LOLLIPOP_INTERACTION.burst.threshold
          ? LOLLIPOP_INTERACTION.burst.thresholdIntensity
          : LOLLIPOP_INTERACTION.burst.belowThresholdIntensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
  }
}

export function resolveLollipopCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const tapCount = context.rangeVariant === LOLLIPOP_INTERACTION.burst.variant
    ? context.recordBurst(LOLLIPOP_INTERACTION.burst.key, LOLLIPOP_BURST_WINDOW_MS)
    : 0;
  const decision = resolveLollipopInteraction(context.rangeVariant, tapCount);
  if (!decision) return {};
  const feedback: Omit<AvatarToolCommand, 'commit'> = {
    ...(decision.nextVariant ? { rangeVariant: decision.nextVariant } : {}),
    sound: LOLLIPOP_INTERACTION.feedback.sound,
    ...(decision.hearts ? { effect: LOLLIPOP_INTERACTION.feedback.effect } : {}),
  };
  switch (decision.actionId) {
    case LOLLIPOP_INTERACTION.stages[0].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
    case LOLLIPOP_INTERACTION.stages[1].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
    case LOLLIPOP_INTERACTION.stages[2].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
  }
}

export const LOLLIPOP_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: () => ({}),
  commit: resolveLollipopCommit,
  pointerRelease: () => ({}),
};

// Fist -----------------------------------------------------------------------

export const FIST_REWARD_DROP_EFFECT_RECIPE = {
  id: 'reward-drops',
  kind: 'random-scatter-v1',
  interactionLock: 'none',
  assetPath: '/static/icons/cat_moneny.png',
  count: 3,
  lifetimeMs: 920,
  angleDeg: { min: -140, range: 100 },
  distance: { min: 76, range: 42 },
  offsetX: { min: -22, range: 28 },
  offsetY: { min: -33, range: 18 },
  rotation: { min: -120, range: 240 },
  scale: { min: 0.82, range: 0.38 },
  delayMs: { min: 0, range: 140 },
} as const satisfies RandomScatterEffectRecipe;

export const FIST_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'fist',
  label: {
    key: 'chat.toolFist',
    fallback: '猫爪',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/cat_claw1.png',
        pointerImagePath: '/static/icons/cat_claw1_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/icons/cat_claw2.png',
        pointerImagePath: '/static/icons/cat_claw2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/icons/cat_claw1.png',
        pointerImagePath: '/static/icons/cat_claw2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
    },
    presentation: {
      inRangeVariantSource: 'range',
      outsideVariantSource: 'outside',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1,
    hotspotX: 39,
    hotspotY: 46,
    naturalWidth: 78,
    naturalHeight: 80,
    pointer: {
      displayWidth: 78,
      displayHeight: 80,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.56,
      renderedAnchor: {
        x: 21.84,
        y: 25.76,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 78,
      displayHeight: 80,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 39,
        y: 46,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'coin-drop',
      src: '/static/sounds/avatar-tools/coin-drop.mp3',
      volume: 0.9,
    },
  ],
  effects: [FIST_REWARD_DROP_EFFECT_RECIPE],
  interaction: {
    kind: 'press-release-v1',
    actionId: 'poke',
    pointerDown: {
      rangeVariant: 'secondary',
      outsideVariant: 'secondary',
    },
    pointerRelease: {
      rangeVariant: 'primary',
      outsideVariant: 'primary',
    },
    burst: {
      key: 'fist',
      windowMs: 1400,
      rapidThreshold: 4,
      normalIntensity: 'normal',
      rapidIntensity: 'rapid',
    },
    touchZone: 'release',
    touchZones: ['ear', 'head', 'face', 'body'],
    chance: {
      field: 'rewardDrop',
      probability: 0.25,
      sound: 'coin-drop',
      effect: 'reward-drops',
    },
  },
} as const satisfies AvatarToolDefinition;

const FIST_INTERACTION = FIST_AVATAR_TOOL_DEFINITION.interaction;

export const FIST_BURST_WINDOW_MS = FIST_INTERACTION.burst.windowMs;

export function resolveFistInteraction(tapCount: number, rewardRoll: number) {
  return {
    actionId: FIST_INTERACTION.actionId,
    intensity: tapCount >= FIST_INTERACTION.burst.rapidThreshold
      ? FIST_INTERACTION.burst.rapidIntensity
      : FIST_INTERACTION.burst.normalIntensity,
    rewardDrop: rewardRoll < FIST_INTERACTION.chance.probability,
  };
}

export function resolveFistPointerDown(): AvatarToolCommand {
  return { ...FIST_INTERACTION.pointerDown, pressFeedback: 'until-pointer-release' };
}

export function resolveFistCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const decision = resolveFistInteraction(
    context.recordBurst(FIST_INTERACTION.burst.key, FIST_BURST_WINDOW_MS),
    context.random(),
  );
  return {
    commit: {
      toolId: FIST_AVATAR_TOOL_DEFINITION.id,
      actionId: FIST_INTERACTION.actionId,
      intensity: decision.intensity,
      touchZone: context.hit.touchZone,
      rewardDrop: decision.rewardDrop,
      clientX: context.clientX,
      clientY: context.clientY,
    },
    ...(decision.rewardDrop ? {
      sound: FIST_INTERACTION.chance.sound,
      effect: FIST_INTERACTION.chance.effect,
    } : {}),
  };
}

export function resolveFistPointerRelease(): AvatarToolCommand {
  return { ...FIST_INTERACTION.pointerRelease };
}

export const FIST_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: resolveFistPointerDown,
  commit: resolveFistCommit,
  pointerRelease: resolveFistPointerRelease,
};

// Hammer ---------------------------------------------------------------------

export const HAMMER_SWING_EFFECT_RECIPE = {
  id: 'hammer-swing',
  kind: 'hammer-swing-v1',
  interactionLock: 'effect-lifetime',
  anchor: {
    source: 'live-pointer',
    visualMode: 'inRange',
  },
  transformOrigin: { x: 60, y: 118 },
  impactRegistration: {
    transformOrigin: { x: 80.19, y: 68 },
    translate: { x: 19.62, y: -9.01 },
    rotationDeg: 34.258,
    scale: 0.999333,
  },
  variants: {
    idle: 'primary',
    impact: 'secondary',
  },
  timeline: [
    { phase: 'windup', delayMs: 0 },
    { phase: 'swing', delayMs: 240 },
    { phase: 'impact', delayMs: 420 },
    { phase: 'recover', delayMs: 520 },
    { phase: 'idle', delayMs: 620 },
  ],
  easterEgg: {
    mode: 'easter-egg',
    scale: 5,
    anchorOffset: { x: 322.11, y: 259.27 },
  },
} as const satisfies HammerSwingEffectRecipe;

export const HAMMER_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'hammer',
  label: {
    key: 'chat.toolHammer',
    fallback: '锤子',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/chat_hammer1.png',
        pointerImagePath: '/static/icons/chat_hammer1_cursor.png',
        menuOffsetX: -8,
        menuOffsetY: 4,
      },
      secondary: {
        iconImagePath: '/static/icons/chat_hammer2.png',
        pointerImagePath: '/static/icons/chat_hammer2_cursor.png',
        menuOffsetX: 1,
        menuOffsetY: -1,
      },
      tertiary: {
        iconImagePath: '/static/icons/chat_hammer1.png',
        pointerImagePath: '/static/icons/chat_hammer2_cursor.png',
        menuOffsetX: 1,
        menuOffsetY: -1,
      },
    },
    presentation: {
      inRangeVariantSource: 'primary',
      outsideVariantSource: 'outside',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1.52,
    hotspotX: 50,
    hotspotY: 54,
    naturalWidth: 100,
    naturalHeight: 96,
    pointer: {
      displayWidth: 100,
      displayHeight: 96,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.52,
      renderedAnchor: {
        x: 26,
        y: 28.08,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 136,
      displayHeight: 130,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 50,
        y: 54,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'hammer-small',
      src: '/static/sounds/avatar-tools/hammer-small.mp3',
      volume: 0.9,
    },
    {
      id: 'hammer-big',
      src: '/static/sounds/avatar-tools/hammer-big.mp3',
      volume: 0.9,
    },
  ],
  effects: [HAMMER_SWING_EFFECT_RECIPE],
  interaction: {
    kind: 'locked-impact-v1',
    actionId: 'bonk',
    touchZone: 'release',
    outsideFeedback: {
      variant: 'secondary',
      resetAfterMs: 220,
    },
    burst: {
      key: 'hammer',
      windowMs: 3200,
      rapidThreshold: 2,
      burstThreshold: 3,
      normalIntensity: 'normal',
      rapidIntensity: 'rapid',
      burstIntensity: 'burst',
    },
    touchZones: ['ear', 'head', 'face', 'body'],
    chance: {
      field: 'easterEgg',
      probability: 0.05,
      intensity: 'easter_egg',
      sound: 'hammer-big',
    },
    feedback: {
      sound: 'hammer-small',
      effect: 'hammer-swing',
    },
  },
} as const satisfies AvatarToolDefinition;

const HAMMER_INTERACTION = HAMMER_AVATAR_TOOL_DEFINITION.interaction;

export const HAMMER_BURST_WINDOW_MS = HAMMER_INTERACTION.burst.windowMs;

export function resolveHammerInteraction(tapCount: number, easterEggRoll: number) {
  const easterEgg = easterEggRoll < HAMMER_INTERACTION.chance.probability;
  return {
    actionId: HAMMER_INTERACTION.actionId,
    intensity: easterEgg
      ? HAMMER_INTERACTION.chance.intensity
      : tapCount >= HAMMER_INTERACTION.burst.burstThreshold
        ? HAMMER_INTERACTION.burst.burstIntensity
        : tapCount >= HAMMER_INTERACTION.burst.rapidThreshold
          ? HAMMER_INTERACTION.burst.rapidIntensity
          : HAMMER_INTERACTION.burst.normalIntensity,
    easterEgg,
  };
}

export function resolveHammerPointerDown(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) {
    return {
      outsideVariant: HAMMER_INTERACTION.outsideFeedback.variant,
      resetOutsideVariantAfterMs: HAMMER_INTERACTION.outsideFeedback.resetAfterMs,
    };
  }
  return {};
}

export function resolveHammerCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const decision = resolveHammerInteraction(
    context.recordBurst(HAMMER_INTERACTION.burst.key, HAMMER_BURST_WINDOW_MS),
    context.random(),
  );
  return {
    commit: {
      toolId: HAMMER_AVATAR_TOOL_DEFINITION.id,
      actionId: HAMMER_INTERACTION.actionId,
      intensity: decision.intensity,
      touchZone: context.hit.touchZone,
      easterEgg: decision.easterEgg,
      clientX: context.clientX,
      clientY: context.clientY,
    },
    sound: decision.easterEgg ? HAMMER_INTERACTION.chance.sound : HAMMER_INTERACTION.feedback.sound,
    effect: HAMMER_INTERACTION.feedback.effect,
    ...(decision.easterEgg ? { effectMode: HAMMER_SWING_EFFECT_RECIPE.easterEgg.mode } : {}),
  };
}

export const HAMMER_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: resolveHammerPointerDown,
  commit: resolveHammerCommit,
  pointerRelease: () => ({}),
};

// Registry -------------------------------------------------------------------

export const AVATAR_TOOL_REGISTRY = [
  { definition: LOLLIPOP_AVATAR_TOOL_DEFINITION, handlers: LOLLIPOP_AVATAR_TOOL_HANDLERS },
  { definition: FIST_AVATAR_TOOL_DEFINITION, handlers: FIST_AVATAR_TOOL_HANDLERS },
  { definition: HAMMER_AVATAR_TOOL_DEFINITION, handlers: HAMMER_AVATAR_TOOL_HANDLERS },
] as const satisfies ReadonlyArray<AvatarToolRegistration>;

const registrationById = new Map<AvatarToolDefinitionId, AvatarToolRegistration>();
AVATAR_TOOL_REGISTRY.forEach((registration) => {
  validateAvatarToolDefinition(registration.definition);
  const { id } = registration.definition;
  if (registrationById.has(id)) throw new Error(`Duplicate avatar tool definition: ${id}`);
  registrationById.set(id, registration);
});

export const AVATAR_TOOL_DEFINITIONS: ReadonlyArray<AvatarToolDefinition> =
  AVATAR_TOOL_REGISTRY.map(registration => registration.definition);

export function createAvatarToolSoundResourceIndex(
  definitions: ReadonlyArray<AvatarToolDefinition>,
): ReadonlyMap<AvatarToolDefinitionSound, AvatarToolSoundResource> {
  const resources = new Map<AvatarToolDefinitionSound, AvatarToolSoundResource>();
  definitions.forEach((definition) => definition.sounds.forEach((sound) => {
    const existing = resources.get(sound.id);
    if (existing && (existing.src !== sound.src || existing.volume !== sound.volume)) {
      throw new Error(`Conflicting avatar tool sound resource: ${sound.id}`);
    }
    resources.set(sound.id, existing ?? sound);
  }));
  return resources;
}

const soundById = createAvatarToolSoundResourceIndex(AVATAR_TOOL_DEFINITIONS);

export function getAvatarToolRegistration(toolId: AvatarToolDefinitionId): AvatarToolRegistration {
  const registration = registrationById.get(toolId);
  if (!registration) throw new Error(`Unsupported avatar tool: ${toolId}`);
  return registration;
}

export function getAvatarToolSoundResource(soundId: AvatarToolDefinitionSound): AvatarToolSoundResource {
  const resource = soundById.get(soundId);
  if (!resource) throw new Error(`Unsupported avatar tool sound: ${soundId}`);
  return resource;
}

export function getAvatarToolEffectRecipe(
  toolId: AvatarToolDefinitionId,
  effectId: AvatarToolDefinitionEffect,
): AvatarToolEffectRecipe {
  const effect = getAvatarToolRegistration(toolId).definition.effects.find(recipe => recipe.id === effectId);
  if (!effect) throw new Error(`Unsupported avatar tool effect: ${toolId}/${effectId}`);
  return effect;
}
