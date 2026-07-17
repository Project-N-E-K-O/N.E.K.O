import type { AvatarToolRuleHandlers } from './interaction';
import { createAvatarToolProfileHandlers } from './profileInterpreter';

export const AVATAR_TOOL_DEFINITION_IDS = ['lollipop', 'fist', 'hammer'] as const;
export const AVATAR_TOOL_VARIANT_IDS = ['primary', 'secondary', 'tertiary'] as const;
export const AVATAR_TOOL_INTERACTION_INTENSITIES = ['normal', 'rapid', 'burst', 'easter_egg'] as const;
export const AVATAR_TOOL_TOUCH_ZONES = ['ear', 'head', 'face', 'body'] as const;
export const AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS = [
  'userGesture', 'avatarGesture', 'roundResult',
] as const;
export const AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS = [
  'interactionId', 'target', 'pointer', 'textContext', 'timestamp',
  'toolId', 'actionId', 'intensity', 'touchZone', 'clientX', 'clientY',
  ...AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS,
] as const;
const AVATAR_TOOL_WIRE_IDENTIFIER_PATTERN = /^[a-z][a-z0-9_-]*$/;
const AVATAR_TOOL_PAYLOAD_FIELD_PATTERN = /^[a-z][a-zA-Z0-9]*$/;
const AVATAR_TOOL_WIRE_IDENTIFIER_MAX_LENGTH = 64;
const AVATAR_TOOL_RESOURCE_MAX_COUNT = 16;
const AVATAR_TOOL_EFFECT_ITEM_MAX_COUNT = 64;
export const AVATAR_TOOL_TIMER_DELAY_MAX_MS = 2_147_483_647;
export const AVATAR_TOOL_ASSET_PATH_MAX_LENGTH = 2048;

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

export function isAvatarToolSameOriginAssetPath(path: string): boolean {
  return path.startsWith('/') && !path.startsWith('//') && !path.includes('\\');
}

export function hasValidAvatarToolAssetVersion(path: string): boolean {
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
}

export type AvatarToolId = typeof AVATAR_TOOL_DEFINITION_IDS[number];
export type AvatarToolVariantId = typeof AVATAR_TOOL_VARIANT_IDS[number];
export type AvatarToolInteractionIntensity = typeof AVATAR_TOOL_INTERACTION_INTENSITIES[number];
export type AvatarToolTouchZone = typeof AVATAR_TOOL_TOUCH_ZONES[number];
export type AvatarToolSoundId = string;
export type AvatarToolEffectId = string;

export type AvatarToolRenderedAnchor = {
  x: number;
  y: number;
  coordinateSpace: 'final-css-pixel';
};

export type AvatarToolSoundResource = {
  id: AvatarToolSoundId;
  src: string;
  volume: number;
};

export type FixedParticleEffectRecipe = {
  id: string;
  kind: 'fixed-particles';
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
  kind: 'random-scatter';
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
  kind: 'hammer-swing';
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

export type AvatarToolLocalizedLabel = {
  key: string;
  fallback: string;
};

export type AvatarToolLayoutOffset = {
  x: number;
  y: number;
};

export type RoundRevealEffectRecipe = {
  id: string;
  kind: 'round-reveal';
  interactionLock: 'effect-lifetime';
  anchors: {
    user: 'release-pointer';
    avatar: 'avatar-head';
  };
  timeline: ReadonlyArray<{
    phase: 'reveal' | 'result' | 'idle';
    delayMs: number;
  }>;
  labels: {
    gestures: Readonly<Record<string, AvatarToolLocalizedLabel>>;
    results: Readonly<Record<'user_win' | 'avatar_win' | 'draw', AvatarToolLocalizedLabel>>;
    announcement: AvatarToolLocalizedLabel;
  };
  layout: {
    userOffset: AvatarToolLayoutOffset;
    avatarOffset: AvatarToolLayoutOffset;
    resultOffset: AvatarToolLayoutOffset;
  };
};

export type AvatarToolEffectRecipe =
  | FixedParticleEffectRecipe
  | RandomScatterEffectRecipe
  | HammerSwingEffectRecipe;

export type AvatarToolContractEffectRecipe =
  | AvatarToolEffectRecipe
  | RoundRevealEffectRecipe;

export type AvatarToolVisualVariant = {
  iconImagePath: string;
  pointerImagePath: string;
  menuOffsetX: number;
  menuOffsetY: number;
};

export type AvatarToolManagerIconVisual = {
  scale: number;
  translateXPercent: number;
  translateYPercent: number;
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
  managerIcon?: AvatarToolManagerIconVisual;
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
  kind: 'progressive-release';
  stages: ReadonlyArray<{
    variant: AvatarToolVariantId;
    actionId: string;
    intensity: AvatarToolInteractionIntensity;
    nextVariant: AvatarToolVariantId | null;
  }>;
  burst: {
    key: string;
    variant: AvatarToolVariantId;
    windowMs: number;
    threshold: number;
    belowThresholdIntensity: AvatarToolInteractionIntensity;
    thresholdIntensity: AvatarToolInteractionIntensity;
  };
  feedback: {
    sound: AvatarToolSoundId;
    effect: AvatarToolEffectId;
    effectVariant: AvatarToolVariantId;
  };
};

export type PressReleaseProfile = {
  kind: 'press-release';
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
    normalIntensity: AvatarToolInteractionIntensity;
    rapidIntensity: AvatarToolInteractionIntensity;
  };
  touchZone: 'release';
  touchZones: ReadonlyArray<AvatarToolTouchZone>;
  chance: {
    field: string;
    probability: number;
    sound: AvatarToolSoundId;
    effect: AvatarToolEffectId;
  };
};

export type LockedImpactProfile = {
  kind: 'locked-impact';
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
    normalIntensity: AvatarToolInteractionIntensity;
    rapidIntensity: AvatarToolInteractionIntensity;
    burstIntensity: AvatarToolInteractionIntensity;
  };
  touchZones: ReadonlyArray<AvatarToolTouchZone>;
  chance: {
    field: string;
    probability: number;
    intensity: 'easter_egg';
    sound: AvatarToolSoundId;
  };
  feedback: {
    sound: AvatarToolSoundId;
    effect: AvatarToolEffectId;
  };
};

export type RoundChoiceProfile = {
  kind: 'round-choice';
  actionId: string;
  intensity: AvatarToolInteractionIntensity;
  choices: Readonly<Record<AvatarToolVariantId, string>>;
  beats: Readonly<Record<string, string>>;
  facts: {
    userChoiceField: 'userGesture';
    avatarChoiceField: 'avatarGesture';
    resultField: 'roundResult';
  };
  cycle: {
    outsideMs: number;
    inRangeMs: number;
    avatarPreviewMs: number;
  };
  random: {
    strategy: 'uniform';
  };
  presentation: {
    confirmation: 'render-owner-required';
  };
  feedback: {
    effect: AvatarToolEffectId;
    confirmSound: AvatarToolSoundId;
    resultSounds: Readonly<Record<'user_win' | 'avatar_win' | 'draw', AvatarToolSoundId>>;
  };
};

export type AvatarToolInteractionProfile =
  | ProgressiveReleaseProfile
  | PressReleaseProfile
  | LockedImpactProfile;

export type AvatarToolContractInteractionProfile =
  | AvatarToolInteractionProfile
  | RoundChoiceProfile;

type AvatarToolDefinitionShape<
  EffectRecipe extends AvatarToolContractEffectRecipe,
  InteractionProfile extends AvatarToolContractInteractionProfile,
> = {
  definitionVersion: 1;
  id: AvatarToolId;
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
  effects: ReadonlyArray<EffectRecipe>;
  interaction: InteractionProfile;
};

export type AvatarToolDefinition = AvatarToolDefinitionShape<
  AvatarToolEffectRecipe,
  AvatarToolInteractionProfile
>;

export type AvatarToolContractDefinition = AvatarToolDefinitionShape<
  AvatarToolContractEffectRecipe,
  AvatarToolContractInteractionProfile
>;

export type AvatarToolRegistration = {
  definition: AvatarToolDefinition;
  handlers: AvatarToolRuleHandlers;
};

function registerAvatarTool<const Definition extends AvatarToolDefinition>(
  definition: Definition,
) {
  return {
    definition,
    handlers: createAvatarToolProfileHandlers(definition),
  };
}

function fail(definition: AvatarToolContractDefinition, reason: string): never {
  throw new Error(`Invalid avatar tool definition "${String(definition?.id)}": ${reason}`);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function assertFinite(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value)) fail(definition, `${field} must be finite`);
}

function assertPositive(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value) || value <= 0) fail(definition, `${field} must be positive`);
}

function assertPositiveInteger(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!Number.isSafeInteger(value) || Number(value) <= 0) {
    fail(definition, `${field} must be a safe positive integer`);
  }
}

function assertProbability(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!isFiniteNumber(value) || value < 0 || value > 1) {
    fail(definition, `${field} must be between 0 and 1`);
  }
}

function assertNonEmpty(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (typeof value !== 'string' || value.trim() === '') fail(definition, `${field} must be non-empty`);
}

function assertDesktopAssetSource(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  const projected = typeof value === 'string' ? withAvatarToolAssetVersion(value, '0') : '';
  if (
    projected.length > AVATAR_TOOL_ASSET_PATH_MAX_LENGTH
    || !isAvatarToolSameOriginAssetPath(projected)
    || !hasValidAvatarToolAssetVersion(projected)
  ) {
    fail(definition, `${field} must project to a versioned same-origin asset path`);
  }
}

function assertWireIdentifier(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (
    typeof value !== 'string'
    || value.length > AVATAR_TOOL_WIRE_IDENTIFIER_MAX_LENGTH
    || !AVATAR_TOOL_WIRE_IDENTIFIER_PATTERN.test(value)
  ) {
    fail(definition, `${field} must be a lowercase identifier of at most 64 characters`);
  }
}

function assertPayloadFieldSyntax(
  definition: AvatarToolContractDefinition,
  value: unknown,
  field: string,
) {
  if (
    typeof value !== 'string'
    || value.length > AVATAR_TOOL_WIRE_IDENTIFIER_MAX_LENGTH
    || !AVATAR_TOOL_PAYLOAD_FIELD_PATTERN.test(value)
  ) {
    fail(definition, `${field} must be a camel-case payload field of at most 64 characters`);
  }
}

function assertUnreservedPayloadField(
  definition: AvatarToolContractDefinition,
  value: unknown,
  field: string,
) {
  assertPayloadFieldSyntax(definition, value, field);
  if ((AVATAR_TOOL_RESERVED_PAYLOAD_FIELDS as readonly unknown[]).includes(value)) {
    fail(definition, `${field} conflicts with a reserved payload field`);
  }
}

function assertRoundChoiceFactField(
  definition: AvatarToolContractDefinition,
  value: unknown,
  field: string,
) {
  assertPayloadFieldSyntax(definition, value, field);
  if (!(AVATAR_TOOL_ROUND_CHOICE_FACT_FIELDS as readonly unknown[]).includes(value)) {
    fail(definition, `${field} must be a canonical round field`);
  }
}

function assertTimerDelay(
  definition: AvatarToolContractDefinition,
  value: unknown,
  field: string,
  allowZero: boolean,
) {
  if (
    !isFiniteNumber(value)
    || (allowZero ? value < 0 : value <= 0)
    || value > AVATAR_TOOL_TIMER_DELAY_MAX_MS
  ) {
    fail(
      definition,
      `${field} must be ${allowZero ? 'non-negative' : 'positive'} and at most ${AVATAR_TOOL_TIMER_DELAY_MAX_MS}ms`,
    );
  }
}

function assertExactKeys(
  definition: AvatarToolContractDefinition,
  value: unknown,
  expectedKeys: ReadonlyArray<string>,
  field: string,
) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(definition, `${field} must be an object`);
  }
  const actualKeys = Object.keys(value);
  const missing = expectedKeys.find(key => !actualKeys.includes(key));
  if (missing) fail(definition, `${field}.${missing} is required`);
  const unexpected = actualKeys.find(key => !expectedKeys.includes(key));
  if (unexpected) fail(definition, `${field} contains unexpected field ${unexpected}`);
}

function assertLocalizedLabel(
  definition: AvatarToolContractDefinition,
  value: unknown,
  field: string,
) {
  assertExactKeys(definition, value, ['key', 'fallback'], field);
  const label = value as AvatarToolLocalizedLabel;
  (['key', 'fallback'] as const).forEach((labelField) => {
    const text = label[labelField];
    if (typeof text !== 'string' || text.trim() === '' || text.length > 256) {
      fail(definition, `${field}.${labelField} must be non-blank and at most 256 characters`);
    }
  });
}

function assertVariant(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!AVATAR_TOOL_VARIANT_IDS.includes(value as never)) {
    fail(definition, `${field} must be a supported variant`);
  }
}

function assertIntensity(definition: AvatarToolContractDefinition, value: unknown, field: string) {
  if (!AVATAR_TOOL_INTERACTION_INTENSITIES.includes(value as never)) {
    fail(definition, `${field} must be a supported intensity`);
  }
}

function assertTouchZones(
  definition: AvatarToolContractDefinition,
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

function validateVisual(definition: AvatarToolContractDefinition) {
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
    if (definition.capability.desktopVisual) {
      assertDesktopAssetSource(definition, asset.iconImagePath, `visual.variants.${variant}.iconImagePath`);
      assertDesktopAssetSource(definition, asset.pointerImagePath, `visual.variants.${variant}.pointerImagePath`);
    }
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
  if (visual.managerIcon) {
    assertPositive(definition, visual.managerIcon.scale, 'visual.managerIcon.scale');
    assertFinite(definition, visual.managerIcon.translateXPercent, 'visual.managerIcon.translateXPercent');
    assertFinite(definition, visual.managerIcon.translateYPercent, 'visual.managerIcon.translateYPercent');
  }
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

function validateSounds(definition: AvatarToolContractDefinition) {
  if (
    !Array.isArray(definition.sounds)
    || definition.sounds.length === 0
    || definition.sounds.length > AVATAR_TOOL_RESOURCE_MAX_COUNT
  ) {
    fail(definition, 'sounds must contain between 1 and 16 resources');
  }
  const ids = new Set<string>();
  definition.sounds.forEach((sound, index) => {
    assertWireIdentifier(definition, sound?.id, `sounds[${index}].id`);
    assertNonEmpty(definition, sound.src, `sounds[${index}].src`);
    if (definition.capability.desktopInteraction) {
      assertDesktopAssetSource(definition, sound.src, `sounds[${index}].src`);
    }
    assertProbability(definition, sound.volume, `sounds[${index}].volume`);
    if (ids.has(sound.id)) fail(definition, `sound ${sound.id} is duplicated`);
    ids.add(sound.id);
  });
}

function validateRange(
  definition: AvatarToolContractDefinition,
  range: { min: number; range: number } | undefined,
  field: string,
) {
  assertFinite(definition, range?.min, `${field}.min`);
  assertFinite(definition, range?.range, `${field}.range`);
  if (Number(range?.range) < 0) fail(definition, `${field}.range must not be negative`);
}

function validateRoundRevealEffect(
  definition: AvatarToolContractDefinition,
  effect: RoundRevealEffectRecipe,
  effectPath: string,
) {
  assertExactKeys(
    definition,
    effect,
    ['id', 'kind', 'interactionLock', 'anchors', 'timeline', 'labels', 'layout'],
    effectPath,
  );
  if (effect.interactionLock !== 'effect-lifetime') {
    fail(definition, `${effectPath} must lock interaction for its effect lifetime`);
  }
  assertExactKeys(definition, effect.anchors, ['user', 'avatar'], `${effectPath}.anchors`);
  if (effect.anchors?.user !== 'release-pointer') {
    fail(definition, `${effectPath}.anchors.user must be release-pointer`);
  }
  if (effect.anchors?.avatar !== 'avatar-head') {
    fail(definition, `${effectPath}.anchors.avatar must be avatar-head`);
  }
  const expectedPhases = ['reveal', 'result', 'idle'] as const;
  const timeline = effect.timeline ?? [];
  if (
    !Array.isArray(timeline)
    || timeline.length !== expectedPhases.length
    || timeline.some((entry, index) => entry?.phase !== expectedPhases[index])
  ) {
    fail(definition, 'round reveal timeline must contain reveal, result and idle in order');
  }
  timeline.forEach((entry, index) => {
    assertExactKeys(definition, entry, ['phase', 'delayMs'], `${effectPath}.timeline[${index}]`);
    assertTimerDelay(definition, entry.delayMs, `${effectPath}.timeline[${index}].delayMs`, true);
    if (index === 0 && entry.delayMs !== 0) {
      fail(definition, 'round reveal timeline must start at 0ms');
    }
    if (index > 0 && entry.delayMs <= timeline[index - 1].delayMs) {
      fail(definition, 'round reveal timeline delays after reveal must be strictly increasing');
    }
  });
  assertExactKeys(
    definition,
    effect.labels,
    ['gestures', 'results', 'announcement'],
    `${effectPath}.labels`,
  );
  const gestureLabels = effect.labels?.gestures;
  if (
    !gestureLabels
    || typeof gestureLabels !== 'object'
    || Array.isArray(gestureLabels)
    || Object.keys(gestureLabels).length === 0
    || Object.keys(gestureLabels).length > AVATAR_TOOL_EFFECT_ITEM_MAX_COUNT
  ) {
    fail(definition, `${effectPath}.labels.gestures must contain between 1 and 64 labels`);
  }
  Object.entries(gestureLabels).forEach(([choice, label]) => {
    assertWireIdentifier(definition, choice, `${effectPath}.labels.gestures key`);
    assertLocalizedLabel(definition, label, `${effectPath}.labels.gestures.${choice}`);
  });
  assertExactKeys(
    definition,
    effect.labels?.results,
    ['user_win', 'avatar_win', 'draw'],
    `${effectPath}.labels.results`,
  );
  (['user_win', 'avatar_win', 'draw'] as const).forEach((result) => {
    assertLocalizedLabel(
      definition,
      effect.labels.results[result],
      `${effectPath}.labels.results.${result}`,
    );
  });
  assertLocalizedLabel(
    definition,
    effect.labels?.announcement,
    `${effectPath}.labels.announcement`,
  );
  assertExactKeys(
    definition,
    effect.layout,
    ['userOffset', 'avatarOffset', 'resultOffset'],
    `${effectPath}.layout`,
  );
  (['userOffset', 'avatarOffset', 'resultOffset'] as const).forEach((offset) => {
    const path = `${effectPath}.layout.${offset}`;
    assertExactKeys(definition, effect.layout?.[offset], ['x', 'y'], path);
    assertFinite(definition, effect.layout[offset].x, `${path}.x`);
    assertFinite(definition, effect.layout[offset].y, `${path}.y`);
  });
}

function validateEffects(definition: AvatarToolContractDefinition) {
  if (!Array.isArray(definition.effects) || definition.effects.length > AVATAR_TOOL_RESOURCE_MAX_COUNT) {
    fail(definition, 'effects must contain at most 16 resources');
  }
  const ids = new Set<string>();
  definition.effects.forEach((effect: AvatarToolContractEffectRecipe, index: number) => {
    const effectPath = `effects[${index}]`;
    assertWireIdentifier(definition, effect?.id, `${effectPath}.id`);
    if (ids.has(effect.id)) fail(definition, `effect ${effect.id} is duplicated`);
    ids.add(effect.id);
    if (effect.kind === 'fixed-particles') {
      if (effect.interactionLock !== 'none') fail(definition, `${effectPath} must not lock interaction`);
      assertPositive(definition, effect.lifetimeMs, `${effectPath}.lifetimeMs`);
      assertNonEmpty(definition, effect.glyph, `${effectPath}.glyph`);
      if (effect.glyph.length > 16) {
        fail(definition, `${effectPath}.glyph must contain at most 16 characters`);
      }
      if (
        !Array.isArray(effect.particles)
        || effect.particles.length === 0
        || effect.particles.length > AVATAR_TOOL_EFFECT_ITEM_MAX_COUNT
      ) {
        fail(definition, `${effectPath}.particles must contain between 1 and at most 64 items`);
      }
      effect.particles.forEach((
        particle: FixedParticleEffectRecipe['particles'][number],
        particleIndex: number,
      ) => {
        ['offsetX', 'offsetY', 'driftX', 'driftY', 'delayMs'].forEach(field =>
          assertFinite(definition, particle?.[field as keyof typeof particle], `${effectPath}.particles[${particleIndex}].${field}`));
        assertPositive(definition, particle?.scale, `${effectPath}.particles[${particleIndex}].scale`);
        if (particle.delayMs < 0) {
          fail(definition, `${effectPath}.particles[${particleIndex}].delayMs must not be negative`);
        }
      });
      return;
    }
    if (effect.kind === 'random-scatter') {
      if (effect.interactionLock !== 'none') fail(definition, `${effectPath} must not lock interaction`);
      assertNonEmpty(definition, effect.assetPath, `${effectPath}.assetPath`);
      if (definition.capability.desktopInteraction) {
        assertDesktopAssetSource(definition, effect.assetPath, `${effectPath}.assetPath`);
      }
      assertPositiveInteger(definition, effect.count, `${effectPath}.count`);
      if (effect.count > AVATAR_TOOL_EFFECT_ITEM_MAX_COUNT) {
        fail(definition, `${effectPath}.count must be at most 64`);
      }
      assertPositive(definition, effect.lifetimeMs, `${effectPath}.lifetimeMs`);
      validateRange(definition, effect.angleDeg, `${effectPath}.angleDeg`);
      validateRange(definition, effect.distance, `${effectPath}.distance`);
      validateRange(definition, effect.offsetX, `${effectPath}.offsetX`);
      validateRange(definition, effect.offsetY, `${effectPath}.offsetY`);
      validateRange(definition, effect.rotation, `${effectPath}.rotation`);
      validateRange(definition, effect.scale, `${effectPath}.scale`);
      validateRange(definition, effect.delayMs, `${effectPath}.delayMs`);
      if (effect.scale.min <= 0) fail(definition, `${effectPath}.scale.min must be positive`);
      if (effect.distance.min <= 0) fail(definition, `${effectPath}.distance.min must be positive`);
      if (effect.delayMs.min < 0) fail(definition, `${effectPath}.delayMs.min must not be negative`);
      return;
    }
    if (effect.kind === 'hammer-swing') {
      if (effect.interactionLock !== 'effect-lifetime') {
        fail(definition, `${effectPath} must lock interaction for its effect lifetime`);
      }
      if (effect.anchor?.source !== 'live-pointer') {
        fail(definition, `${effectPath}.anchor.source must be live-pointer`);
      }
      if (effect.anchor?.visualMode !== 'inRange') {
        fail(definition, `${effectPath}.anchor.visualMode must be inRange`);
      }
      assertFinite(definition, effect.transformOrigin?.x, `${effectPath}.transformOrigin.x`);
      assertFinite(definition, effect.transformOrigin?.y, `${effectPath}.transformOrigin.y`);
      assertFinite(
        definition,
        effect.impactRegistration?.transformOrigin?.x,
        `${effectPath}.impactRegistration.transformOrigin.x`,
      );
      assertFinite(
        definition,
        effect.impactRegistration?.transformOrigin?.y,
        `${effectPath}.impactRegistration.transformOrigin.y`,
      );
      assertFinite(
        definition,
        effect.impactRegistration?.translate?.x,
        `${effectPath}.impactRegistration.translate.x`,
      );
      assertFinite(
        definition,
        effect.impactRegistration?.translate?.y,
        `${effectPath}.impactRegistration.translate.y`,
      );
      assertFinite(
        definition,
        effect.impactRegistration?.rotationDeg,
        `${effectPath}.impactRegistration.rotationDeg`,
      );
      assertPositive(
        definition,
        effect.impactRegistration?.scale,
        `${effectPath}.impactRegistration.scale`,
      );
      assertVariant(definition, effect.variants?.idle, `${effectPath}.variants.idle`);
      assertVariant(definition, effect.variants?.impact, `${effectPath}.variants.impact`);
      const expectedPhases = ['windup', 'swing', 'impact', 'recover', 'idle'];
      const timeline: HammerSwingEffectRecipe['timeline'] = effect.timeline ?? [];
      if (
        timeline.length !== expectedPhases.length
        || timeline.some((entry, timelineIndex) => entry.phase !== expectedPhases[timelineIndex])
      ) {
        fail(definition, 'hammer timeline must contain windup, swing, impact, recover and idle in order');
      }
      timeline.forEach((entry, timelineIndex) => {
        assertFinite(definition, entry.delayMs, `${effectPath}.timeline[${timelineIndex}].delayMs`);
        if (entry.delayMs < 0) fail(definition, 'hammer timeline delays must not be negative');
        if (timelineIndex === 0 && entry.delayMs !== 0) fail(definition, 'hammer windup must start at 0ms');
        if (timelineIndex > 0 && entry.delayMs <= timeline[timelineIndex - 1].delayMs) {
          fail(definition, 'hammer timeline delays after windup must be strictly increasing');
        }
      });
      if (effect.easterEgg?.mode !== 'easter-egg') {
        fail(definition, `${effectPath}.easterEgg.mode must be easter-egg`);
      }
      assertPositive(definition, effect.easterEgg?.scale, `${effectPath}.easterEgg.scale`);
      assertFinite(definition, effect.easterEgg?.anchorOffset?.x, `${effectPath}.easterEgg.anchorOffset.x`);
      assertFinite(definition, effect.easterEgg?.anchorOffset?.y, `${effectPath}.easterEgg.anchorOffset.y`);
      return;
    }
    if (effect.kind === 'round-reveal') {
      validateRoundRevealEffect(definition, effect, effectPath);
      return;
    }
    fail(definition, `effects[${index}].kind is unsupported`);
  });
}

function validateInteractionReferences(definition: AvatarToolContractDefinition) {
  const soundIds = new Set(definition.sounds.map(sound => sound.id));
  const effectIds = new Set(definition.effects.map(effect => effect.id));
  const requireSound = (sound: AvatarToolSoundId) => {
    if (!soundIds.has(sound)) fail(definition, `interaction references missing sound ${sound}`);
  };
  const requireEffect = (effect: AvatarToolEffectId) => {
    if (!effectIds.has(effect)) fail(definition, `interaction references missing effect ${effect}`);
  };
  const requireLegacyEffect = (effectId: AvatarToolEffectId) => {
    requireEffect(effectId);
    if (definition.effects.find(effect => effect.id === effectId)?.kind === 'round-reveal') {
      fail(definition, 'round-reveal effects require a round-choice profile');
    }
  };
  const requireExactResourceClosure = (
    referencedSounds: ReadonlySet<AvatarToolSoundId>,
    referencedEffects: ReadonlySet<AvatarToolEffectId>,
  ) => {
    referencedSounds.forEach(requireSound);
    referencedEffects.forEach(requireEffect);
    soundIds.forEach((sound) => {
      if (!referencedSounds.has(sound)) fail(definition, `interaction has unreferenced sound ${sound}`);
    });
    effectIds.forEach((effect) => {
      if (!referencedEffects.has(effect)) fail(definition, `interaction has unreferenced effect ${effect}`);
    });
  };
  const interaction = definition.interaction;
  assertNonEmpty(definition, interaction?.kind, 'interaction.kind');
  if (interaction.kind === 'progressive-release') {
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
      assertWireIdentifier(definition, stage.actionId, `interaction.stages[${index}].actionId`);
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
    assertVariant(definition, interaction.feedback.effectVariant, 'interaction.feedback.effectVariant');
    requireSound(interaction.feedback.sound);
    requireLegacyEffect(interaction.feedback.effect);
    return;
  }
  if (interaction.kind === 'round-choice') {
    assertExactKeys(
      definition,
      interaction,
      [
        'kind', 'actionId', 'intensity', 'choices', 'beats', 'facts', 'cycle',
        'random', 'presentation', 'feedback',
      ],
      'interaction',
    );
    assertWireIdentifier(definition, interaction.actionId, 'interaction.actionId');
    assertIntensity(definition, interaction.intensity, 'interaction.intensity');
    assertExactKeys(definition, interaction.choices, AVATAR_TOOL_VARIANT_IDS, 'interaction.choices');
    const choices = AVATAR_TOOL_VARIANT_IDS.map(variant => interaction.choices[variant]);
    choices.forEach((choice, index) => {
      assertWireIdentifier(definition, choice, `interaction.choices.${AVATAR_TOOL_VARIANT_IDS[index]}`);
    });
    if (new Set(choices).size !== AVATAR_TOOL_VARIANT_IDS.length) {
      fail(definition, 'interaction.choices must contain three unique choice identifiers');
    }
    assertExactKeys(definition, interaction.beats, choices, 'interaction.beats');
    const beatenChoices = choices.map((choice) => interaction.beats[choice]);
    if (
      beatenChoices.some((loser, index) => !choices.includes(loser) || loser === choices[index])
      || new Set(beatenChoices).size !== choices.length
    ) {
      fail(definition, 'interaction.beats must form a unique three-element cycle');
    }
    assertExactKeys(
      definition,
      interaction.facts,
      ['userChoiceField', 'avatarChoiceField', 'resultField'],
      'interaction.facts',
    );
    const expectedFactFields = {
      userChoiceField: 'userGesture',
      avatarChoiceField: 'avatarGesture',
      resultField: 'roundResult',
    } as const;
    (Object.keys(expectedFactFields) as Array<keyof typeof expectedFactFields>).forEach((field) => {
      const value = interaction.facts[field];
      assertRoundChoiceFactField(definition, value, `interaction.facts.${field}`);
      if (value !== expectedFactFields[field]) {
        fail(definition, `interaction.facts.${field} must be ${expectedFactFields[field]}`);
      }
    });
    assertExactKeys(
      definition,
      interaction.cycle,
      ['outsideMs', 'inRangeMs', 'avatarPreviewMs'],
      'interaction.cycle',
    );
    (['outsideMs', 'inRangeMs', 'avatarPreviewMs'] as const).forEach((field) => {
      assertTimerDelay(definition, interaction.cycle[field], `interaction.cycle.${field}`, false);
    });
    assertExactKeys(definition, interaction.random, ['strategy'], 'interaction.random');
    if (interaction.random.strategy !== 'uniform') {
      fail(definition, 'interaction.random.strategy must be uniform');
    }
    assertExactKeys(
      definition,
      interaction.presentation,
      ['confirmation'],
      'interaction.presentation',
    );
    if (interaction.presentation.confirmation !== 'render-owner-required') {
      fail(definition, 'interaction.presentation.confirmation must be render-owner-required');
    }
    assertExactKeys(
      definition,
      interaction.feedback,
      ['effect', 'confirmSound', 'resultSounds'],
      'interaction.feedback',
    );
    assertExactKeys(
      definition,
      interaction.feedback.resultSounds,
      ['user_win', 'avatar_win', 'draw'],
      'interaction.feedback.resultSounds',
    );
    const referencedSounds = new Set<AvatarToolSoundId>([
      interaction.feedback.confirmSound,
      interaction.feedback.resultSounds.user_win,
      interaction.feedback.resultSounds.avatar_win,
      interaction.feedback.resultSounds.draw,
    ]);
    const referencedEffects = new Set<AvatarToolEffectId>([interaction.feedback.effect]);
    requireExactResourceClosure(referencedSounds, referencedEffects);
    const revealEffect = definition.effects.find(effect => effect.id === interaction.feedback.effect);
    if (revealEffect?.kind !== 'round-reveal') {
      fail(definition, 'interaction.feedback.effect must reference a round-reveal effect');
    }
    const gestureLabelKeys = Object.keys(revealEffect.labels.gestures);
    if (
      gestureLabelKeys.length !== choices.length
      || !choices.every(choice => gestureLabelKeys.includes(choice))
    ) {
      fail(definition, 'round reveal gesture labels must match interaction choices');
    }
    return;
  }
  if (interaction.kind !== 'press-release' && interaction.kind !== 'locked-impact') {
    fail(definition, 'interaction.kind is unsupported');
  }
  assertWireIdentifier(definition, interaction.actionId, 'interaction.actionId');
  if (interaction.touchZone !== 'release') {
    fail(definition, 'interaction.touchZone must be release');
  }
  assertNonEmpty(definition, interaction.burst.key, 'interaction.burst.key');
  assertPositive(definition, interaction.burst.windowMs, 'interaction.burst.windowMs');
  assertPositiveInteger(definition, interaction.burst.rapidThreshold, 'interaction.burst.rapidThreshold');
  assertIntensity(definition, interaction.burst.normalIntensity, 'interaction.burst.normalIntensity');
  assertIntensity(definition, interaction.burst.rapidIntensity, 'interaction.burst.rapidIntensity');
  assertTouchZones(definition, interaction.touchZones, 'interaction.touchZones');
  assertUnreservedPayloadField(definition, interaction.chance.field, 'interaction.chance.field');
  assertProbability(definition, interaction.chance.probability, 'interaction.chance.probability');
  if (interaction.kind === 'press-release') {
    assertVariant(definition, interaction.pointerDown.rangeVariant, 'interaction.pointerDown.rangeVariant');
    assertVariant(definition, interaction.pointerDown.outsideVariant, 'interaction.pointerDown.outsideVariant');
    assertVariant(definition, interaction.pointerRelease.rangeVariant, 'interaction.pointerRelease.rangeVariant');
    assertVariant(definition, interaction.pointerRelease.outsideVariant, 'interaction.pointerRelease.outsideVariant');
    requireSound(interaction.chance.sound);
    requireLegacyEffect(interaction.chance.effect);
    return;
  }
  if (interaction.kind === 'locked-impact') {
    assertPositiveInteger(definition, interaction.burst.burstThreshold, 'interaction.burst.burstThreshold');
    assertIntensity(definition, interaction.burst.burstIntensity, 'interaction.burst.burstIntensity');
    if (interaction.chance.intensity !== 'easter_egg') {
      fail(definition, 'interaction.chance.intensity must be easter_egg');
    }
    if ([
      interaction.burst.normalIntensity,
      interaction.burst.rapidIntensity,
      interaction.burst.burstIntensity,
    ].includes(interaction.chance.intensity)) {
      fail(definition, 'interaction chance intensity must be exclusive to the chance result');
    }
    if (interaction.burst.rapidThreshold > interaction.burst.burstThreshold) {
      fail(definition, 'interaction burst thresholds are out of order');
    }
    assertVariant(definition, interaction.outsideFeedback.variant, 'interaction.outsideFeedback.variant');
    assertPositive(definition, interaction.outsideFeedback.resetAfterMs, 'interaction.outsideFeedback.resetAfterMs');
    requireSound(interaction.chance.sound);
    requireSound(interaction.feedback.sound);
    requireLegacyEffect(interaction.feedback.effect);
    return;
  }
  fail(definition, 'interaction.kind is unsupported');
}

export function validateAvatarToolDefinition(definition: AvatarToolContractDefinition): void {
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
  if (!definition.capability.desktopVisual && definition.capability.desktopInteraction) {
    fail(definition, 'desktop interaction requires desktop visual capability');
  }
  validateVisual(definition);
  validateSounds(definition);
  validateEffects(definition);
  validateInteractionReferences(definition);
}

// Lollipop -------------------------------------------------------------------

export const LOLLIPOP_HEART_EFFECT_RECIPE = {
  id: 'lollipop-hearts',
  kind: 'fixed-particles',
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
        iconImagePath: '/static/assets/avatar-tools/lollipop/primary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/lollipop/primary-pointer.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/assets/avatar-tools/lollipop/secondary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/lollipop/secondary-pointer.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/assets/avatar-tools/lollipop/tertiary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/lollipop/secondary-pointer.png',
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
      src: '/static/sounds/avatar-tools/lollipop/bite.mp3',
      volume: 0.9,
    },
  ],
  effects: [LOLLIPOP_HEART_EFFECT_RECIPE],
  interaction: {
    kind: 'progressive-release',
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
      effect: 'lollipop-hearts',
      effectVariant: 'tertiary',
    },
  },
} as const satisfies AvatarToolDefinition;

// Fist -----------------------------------------------------------------------

export const FIST_REWARD_DROP_EFFECT_RECIPE = {
  id: 'fist-reward-drops',
  kind: 'random-scatter',
  interactionLock: 'none',
  assetPath: '/static/assets/avatar-tools/fist/reward-drop.png',
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
        iconImagePath: '/static/assets/avatar-tools/fist/primary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/fist/primary-pointer.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/assets/avatar-tools/fist/secondary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/fist/secondary-pointer.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/assets/avatar-tools/fist/primary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/fist/secondary-pointer.png',
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
      id: 'fist-reward-drop',
      src: '/static/sounds/avatar-tools/fist/reward-drop.mp3',
      volume: 0.9,
    },
  ],
  effects: [FIST_REWARD_DROP_EFFECT_RECIPE],
  interaction: {
    kind: 'press-release',
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
      sound: 'fist-reward-drop',
      effect: 'fist-reward-drops',
    },
  },
} as const satisfies AvatarToolDefinition;

// Hammer ---------------------------------------------------------------------

export const HAMMER_SWING_EFFECT_RECIPE = {
  id: 'hammer-swing',
  kind: 'hammer-swing',
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
        iconImagePath: '/static/assets/avatar-tools/hammer/primary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/hammer/primary-pointer.png',
        menuOffsetX: -8,
        menuOffsetY: 4,
      },
      secondary: {
        iconImagePath: '/static/assets/avatar-tools/hammer/secondary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/hammer/secondary-pointer.png',
        menuOffsetX: 1,
        menuOffsetY: -1,
      },
      tertiary: {
        iconImagePath: '/static/assets/avatar-tools/hammer/primary-icon.png',
        pointerImagePath: '/static/assets/avatar-tools/hammer/secondary-pointer.png',
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
    managerIcon: {
      scale: 1.38,
      translateXPercent: -14,
      translateYPercent: 10,
    },
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
      id: 'hammer-impact',
      src: '/static/sounds/avatar-tools/hammer/impact.mp3',
      volume: 0.9,
    },
    {
      id: 'hammer-easter-egg',
      src: '/static/sounds/avatar-tools/hammer/easter-egg.mp3',
      volume: 0.9,
    },
  ],
  effects: [HAMMER_SWING_EFFECT_RECIPE],
  interaction: {
    kind: 'locked-impact',
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
      sound: 'hammer-easter-egg',
    },
    feedback: {
      sound: 'hammer-impact',
      effect: 'hammer-swing',
    },
  },
} as const satisfies AvatarToolDefinition;

// Registry -------------------------------------------------------------------

export const AVATAR_TOOL_REGISTRY = [
  registerAvatarTool(LOLLIPOP_AVATAR_TOOL_DEFINITION),
  registerAvatarTool(FIST_AVATAR_TOOL_DEFINITION),
  registerAvatarTool(HAMMER_AVATAR_TOOL_DEFINITION),
] as const satisfies ReadonlyArray<AvatarToolRegistration>;

const registrationById = new Map<AvatarToolId, AvatarToolRegistration>();
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
): ReadonlyMap<AvatarToolSoundId, AvatarToolSoundResource> {
  const resources = new Map<AvatarToolSoundId, AvatarToolSoundResource>();
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

export function getAvatarToolRegistration(toolId: AvatarToolId): AvatarToolRegistration {
  const registration = registrationById.get(toolId);
  if (!registration) throw new Error(`Unsupported avatar tool: ${toolId}`);
  return registration;
}

export function getAvatarToolSoundResource(soundId: AvatarToolSoundId): AvatarToolSoundResource {
  const resource = soundById.get(soundId);
  if (!resource) throw new Error(`Unsupported avatar tool sound: ${soundId}`);
  return resource;
}

export function getAvatarToolEffectRecipe(
  toolId: AvatarToolId,
  effectId: AvatarToolEffectId,
): AvatarToolEffectRecipe {
  const effect = getAvatarToolRegistration(toolId).definition.effects.find(recipe => recipe.id === effectId);
  if (!effect) throw new Error(`Unsupported avatar tool effect: ${toolId}/${effectId}`);
  return effect;
}
