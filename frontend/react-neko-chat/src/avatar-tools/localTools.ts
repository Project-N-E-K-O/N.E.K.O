import {
  hasValidAvatarToolAssetVersion,
  isAvatarToolSameOriginAssetPath,
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  type AvatarToolDefinition,
  type LocalAvatarToolId,
  type RandomScatterEffectRecipe,
} from './catalog';
import {
  countAvatarToolNameCharacters,
  getAvatarToolNameValidationError,
  normalizeAvatarToolComparableName,
  normalizeAvatarToolName,
} from './avatarToolNames';

export type LocalAvatarToolLimits = {
  maxTools: number;
  maxNameChars: number;
  maxMeaningChars: number;
  maxChangeImages: number;
  maxImages: number;
  maxInteractions: number;
  maxLinks: number;
  maxDelayMs: number;
  maxImageBytes: number;
  maxImagePixels: number;
  maxAudioBytes: number;
  maxAudioDurationMs: number;
  maxTotalBytes: number;
};

export type LocalAvatarToolV2Dto = {
  recordVersion?: 2;
  id: LocalAvatarToolId;
  revision: string;
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultUrl: string;
  changeUrls: string[];
  normalSoundUrl?: string;
  special?: {
    probability: number;
    imageUrl: string;
    soundUrl?: string;
  };
};

export type LocalAvatarToolV3Dto = {
  recordVersion: 3;
  id: LocalAvatarToolId;
  revision: string;
  name: string;
  initialImageUrl: string;
  runtime: LocalAvatarToolV3RuntimeProjection;
};

export type LocalAvatarToolDto = LocalAvatarToolV2Dto | LocalAvatarToolV3Dto;

export type LocalAvatarToolChangeMode = 'press-swap' | 'click-advance';

export type LocalAvatarToolList = {
  items: LocalAvatarToolDto[];
  limits: LocalAvatarToolLimits;
};

export type CreateLocalAvatarToolV2Input = {
  toolId: LocalAvatarToolId;
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultImage: File;
  changeItems: Array<{ image: File; meaning: string }>;
  normalSound?: File;
  special?: {
    probability: number;
    image: File;
    meaning: string;
    sound?: File;
  };
};

export type LocalAvatarToolResource = {
  resource: string;
  url: string;
};

export type LocalAvatarToolV2Detail = {
  recordVersion?: 2;
  id: LocalAvatarToolId;
  revision: string;
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultImage: LocalAvatarToolResource;
  changeItems: Array<LocalAvatarToolResource & { meaning: string }>;
  normalSound?: LocalAvatarToolResource;
  special?: {
    probability: number;
    image: LocalAvatarToolResource;
    meaning: string;
    sound?: LocalAvatarToolResource;
  };
};

export type LocalAvatarToolConnectionSide = 'top' | 'right' | 'bottom' | 'left';

export type LocalAvatarToolImageAction =
  | { kind: 'keep' }
  | { kind: 'show'; imageId: `img-${string}` };

export type LocalAvatarToolV3RuntimeInteraction = {
  id: `ix-${string}`;
  trigger:
    | { kind: 'mouse-click' }
    | { kind: 'after'; delayMs: number };
  actions:
    | { press: LocalAvatarToolImageAction; release: LocalAvatarToolImageAction }
    | { complete: LocalAvatarToolImageAction };
};

export type LocalAvatarToolV3RuntimeProjection = {
  images: Array<{
    id: `img-${string}`;
    url: string;
    hasMeaning: boolean;
  }>;
  initialImageId: `img-${string}`;
  initialInteractionIds: Array<`ix-${string}`>;
  interactions: LocalAvatarToolV3RuntimeInteraction[];
  links: Array<{ from: `ix-${string}`; to: `ix-${string}` }>;
  normalSoundUrl?: string;
  special?: {
    probability: number;
    imageUrl: string;
    hasMeaning: boolean;
    soundUrl?: string;
  };
};

export type LocalAvatarToolImageInteractions = {
  initialImagePosition: { x: number; y: number };
  initialLinks: Array<{
    to: `ix-${string}`;
    sourceSide: LocalAvatarToolConnectionSide;
    targetSide: LocalAvatarToolConnectionSide;
  }>;
  items: Array<{
    id: `ix-${string}`;
    name: string;
    trigger:
      | { kind: 'mouse-click' }
      | { kind: 'after'; delayMs: number };
    actions:
      | { press: LocalAvatarToolImageAction; release: LocalAvatarToolImageAction }
      | { complete: LocalAvatarToolImageAction };
    editorPosition: { x: number; y: number };
  }>;
  links: Array<{
    from: `ix-${string}`;
    to: `ix-${string}`;
    sourceSide: LocalAvatarToolConnectionSide;
    targetSide: LocalAvatarToolConnectionSide;
  }>;
};

export type LocalAvatarToolV3Detail = {
  recordVersion: 3;
  id: LocalAvatarToolId;
  revision: string;
  name: string;
  images: Array<LocalAvatarToolResource & {
    id: `img-${string}`;
    name: string;
    meaning: string;
  }>;
  initialImageId: `img-${string}`;
  imageInteractions: LocalAvatarToolImageInteractions;
  normalSound?: LocalAvatarToolResource;
  special?: {
    probability: number;
    image: LocalAvatarToolResource;
    meaning: string;
    sound?: LocalAvatarToolResource;
  };
};

export type LocalAvatarToolDetail = LocalAvatarToolV2Detail | LocalAvatarToolV3Detail;

export type UpdateLocalAvatarToolV2Input = {
  baseRevision: string;
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultImage: { resource?: string; url?: string; file?: File };
  changeItems: Array<{ resource?: string; url?: string; file?: File; meaning: string }>;
  normalSound?: { resource?: string; url?: string; file?: File };
  special?: {
    probability: number;
    image: { resource?: string; url?: string; file?: File };
    meaning: string;
    sound?: { resource?: string; url?: string; file?: File };
  };
};

export type LocalAvatarToolMediaInput = {
  resource?: string;
  url?: string;
  file?: File;
};

export type LocalAvatarToolV3SaveInput = {
  recordVersion: 3;
  name: string;
  images: Array<{
    id: `img-${string}`;
    name: string;
    image: LocalAvatarToolMediaInput;
    meaning: string;
  }>;
  initialImageId: `img-${string}`;
  imageInteractions: LocalAvatarToolImageInteractions;
  normalSound?: LocalAvatarToolMediaInput;
  special?: {
    probability: number;
    image: LocalAvatarToolMediaInput;
    meaning: string;
    sound?: LocalAvatarToolMediaInput;
  };
};

export type CreateLocalAvatarToolV3Input = LocalAvatarToolV3SaveInput & {
  toolId: LocalAvatarToolId;
};

export type UpdateLocalAvatarToolV3Input = LocalAvatarToolV3SaveInput & {
  baseRevision: string;
};

export type CreateLocalAvatarToolInput = CreateLocalAvatarToolV2Input | CreateLocalAvatarToolV3Input;
export type UpdateLocalAvatarToolInput = UpdateLocalAvatarToolV2Input | UpdateLocalAvatarToolV3Input;

export class LocalAvatarToolCreateError extends Error {
  readonly field?: string;
  readonly index?: number;

  constructor(code: string, options?: { field?: string; index?: number }) {
    super(code);
    this.name = 'LocalAvatarToolCreateError';
    this.field = options?.field;
    this.index = options?.index;
  }
}

export class LocalAvatarToolRevisionConflictError extends LocalAvatarToolCreateError {
  readonly currentDetail: LocalAvatarToolDetail;

  constructor(currentDetail: LocalAvatarToolDetail) {
    super('tool_revision_conflict');
    this.name = 'LocalAvatarToolRevisionConflictError';
    this.currentDetail = currentDetail;
  }
}

export class LocalAvatarToolDeleteError extends Error {
  constructor(code: string) {
    super(code);
    this.name = 'LocalAvatarToolDeleteError';
  }
}

export class LocalAvatarToolDetailError extends Error {
  constructor(code: string) {
    super(code);
    this.name = 'LocalAvatarToolDetailError';
  }
}

export function createLocalAvatarToolId(): LocalAvatarToolId {
  const toolId = `local-${globalThis.crypto.randomUUID().toLowerCase()}`;
  if (!LOCAL_AVATAR_TOOL_ID_PATTERN.test(toolId)) {
    throw new Error('Could not create a local avatar tool ID');
  }
  return toolId as LocalAvatarToolId;
}

export const LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE = {
  id: 'special-scatter',
  kind: 'random-scatter',
  interactionLock: 'none',
  assetPath: '',
  count: 5,
  lifetimeMs: 920,
  angleDeg: { min: -150, range: 120 },
  distance: { min: 72, range: 52 },
  offsetX: { min: -24, range: 48 },
  offsetY: { min: -36, range: 24 },
  rotation: { min: -135, range: 270 },
  scale: { min: 0.72, range: 0.46 },
  delayMs: { min: 0, range: 160 },
} as const satisfies RandomScatterEffectRecipe;

function decodeSpecial(value: unknown): LocalAvatarToolV2Dto['special'] | null {
  if (!value || typeof value !== 'object') return null;
  const special = value as Record<string, unknown>;
  if (
    !Object.keys(special).every(key => ['probability', 'imageUrl', 'soundUrl'].includes(key))
    || typeof special.probability !== 'number'
    || !Number.isFinite(special.probability)
    || special.probability <= 0
    || special.probability > 1
    || !isStrictAvatarToolResourceUrl(special.imageUrl)
    || (special.soundUrl !== undefined && !isStrictAvatarToolResourceUrl(special.soundUrl))
  ) return null;
  return {
    probability: special.probability,
    imageUrl: special.imageUrl,
    ...(typeof special.soundUrl === 'string' ? { soundUrl: special.soundUrl } : {}),
  };
}

const LOCAL_AVATAR_TOOL_IMAGE_ID_PATTERN = /^img-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const LOCAL_AVATAR_TOOL_INTERACTION_ID_PATTERN = /^ix-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const LOCAL_AVATAR_TOOL_STABLE_ID_MAX_LENGTH = 80;
const LOCAL_AVATAR_TOOL_RESOURCE_PATTERN = /^(?:default|change-[0-9]{3}|image-[0-9]{3}|normal|special|special-sound)\.(?:png|jpg|jpeg|webp|gif|mp3|wav|ogg|m4a)$/;
const LOCAL_AVATAR_TOOL_CONNECTION_SIDES = new Set<LocalAvatarToolConnectionSide>(['top', 'right', 'bottom', 'left']);
const LOCAL_AVATAR_TOOL_MEANING_CONTROL_PATTERN = /[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;

function isStrictAvatarToolResourceUrl(value: unknown): value is string {
  if (
    typeof value !== 'string'
    || !isAvatarToolSameOriginAssetPath(value)
    || !hasValidAvatarToolAssetVersion(value)
  ) return false;
  try {
    const parsed = new URL(value, 'https://neko.invalid');
    return [...parsed.searchParams.keys()].every(key => key === 'v')
      && parsed.searchParams.getAll('v').length === 1;
  } catch {
    return false;
  }
}

function isValidAvatarToolMeaning(value: unknown, maximum: number, required = false): value is string {
  if (typeof value !== 'string') return false;
  const normalized = value.replace(/\r\n?/g, '\n').trim();
  return (!required || normalized.length > 0)
    && Array.from(normalized).length <= maximum
    && !LOCAL_AVATAR_TOOL_MEANING_CONTROL_PATTERN.test(normalized);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).every(key => keys.includes(key));
}

function isRevision(value: unknown): value is string {
  return typeof value === 'string' && /^\d+-\d+$/.test(value) && value.length <= 128;
}

function decodeV3RuntimeProjection(
  value: unknown,
  limits: Pick<LocalAvatarToolLimits, 'maxImages' | 'maxInteractions' | 'maxLinks' | 'maxDelayMs'>,
): LocalAvatarToolV3RuntimeProjection | null {
  if (!value || typeof value !== 'object') return null;
  const runtime = value as Record<string, unknown>;
  if (!hasOnlyKeys(runtime, [
    'images', 'initialImageId', 'initialInteractionIds', 'interactions', 'links',
    'normalSoundUrl', 'special',
  ])) return null;
  if (
    !Array.isArray(runtime.images)
    || runtime.images.length < 1
    || runtime.images.length > limits.maxImages
  ) return null;
  const imageIds = new Set<string>();
  const images: LocalAvatarToolV3RuntimeProjection['images'] = [];
  for (const candidate of runtime.images) {
    if (!candidate || typeof candidate !== 'object') return null;
    const image = candidate as Record<string, unknown>;
    if (
      !hasOnlyKeys(image, ['id', 'url', 'hasMeaning'])
      || typeof image.id !== 'string'
      || image.id.length > LOCAL_AVATAR_TOOL_STABLE_ID_MAX_LENGTH
      || !LOCAL_AVATAR_TOOL_IMAGE_ID_PATTERN.test(image.id)
      || imageIds.has(image.id)
      || !isStrictAvatarToolResourceUrl(image.url)
      || typeof image.hasMeaning !== 'boolean'
    ) return null;
    imageIds.add(image.id);
    images.push({
      id: image.id as `img-${string}`,
      url: image.url,
      hasMeaning: image.hasMeaning,
    });
  }
  if (typeof runtime.initialImageId !== 'string' || !imageIds.has(runtime.initialImageId)) return null;
  if (
    !Array.isArray(runtime.interactions)
    || runtime.interactions.length < 1
    || runtime.interactions.length > limits.maxInteractions
  ) return null;
  const interactionIds = new Set<string>();
  const interactions: LocalAvatarToolV3RuntimeInteraction[] = [];
  for (const candidate of runtime.interactions) {
    if (!candidate || typeof candidate !== 'object') return null;
    const item = candidate as Record<string, unknown>;
    if (
      !hasOnlyKeys(item, ['id', 'trigger', 'actions'])
      || typeof item.id !== 'string'
      || item.id.length > LOCAL_AVATAR_TOOL_STABLE_ID_MAX_LENGTH
      || !LOCAL_AVATAR_TOOL_INTERACTION_ID_PATTERN.test(item.id)
      || interactionIds.has(item.id)
      || !item.trigger || typeof item.trigger !== 'object'
      || !item.actions || typeof item.actions !== 'object'
    ) return null;
    const trigger = item.trigger as Record<string, unknown>;
    const actions = item.actions as Record<string, unknown>;
    let decoded: LocalAvatarToolV3RuntimeInteraction;
    if (hasOnlyKeys(trigger, ['kind']) && trigger.kind === 'mouse-click') {
      if (!hasOnlyKeys(actions, ['press', 'release'])) return null;
      const press = decodeImageAction(actions.press, imageIds);
      const release = decodeImageAction(actions.release, imageIds);
      if (!press || !release) return null;
      decoded = {
        id: item.id as `ix-${string}`,
        trigger: { kind: 'mouse-click' },
        actions: { press, release },
      };
    } else if (
      hasOnlyKeys(trigger, ['kind', 'delayMs'])
      && trigger.kind === 'after'
      && Number.isSafeInteger(trigger.delayMs)
      && Number(trigger.delayMs) >= 1
      && Number(trigger.delayMs) <= limits.maxDelayMs
    ) {
      if (!hasOnlyKeys(actions, ['complete'])) return null;
      const complete = decodeImageAction(actions.complete, imageIds);
      if (!complete) return null;
      decoded = {
        id: item.id as `ix-${string}`,
        trigger: { kind: 'after', delayMs: Number(trigger.delayMs) },
        actions: { complete },
      };
    } else {
      return null;
    }
    interactionIds.add(item.id);
    interactions.push(decoded);
  }
  if (
    !Array.isArray(runtime.initialInteractionIds)
    || runtime.initialInteractionIds.length < 1
    || runtime.initialInteractionIds.some(id => typeof id !== 'string' || !interactionIds.has(id))
    || new Set(runtime.initialInteractionIds).size !== runtime.initialInteractionIds.length
    || !Array.isArray(runtime.links)
    || runtime.initialInteractionIds.length + runtime.links.length > limits.maxLinks
  ) return null;
  const links: LocalAvatarToolV3RuntimeProjection['links'] = [];
  const linkKeys = new Set<string>();
  for (const candidate of runtime.links) {
    if (!candidate || typeof candidate !== 'object') return null;
    const link = candidate as Record<string, unknown>;
    if (
      !hasOnlyKeys(link, ['from', 'to'])
      || typeof link.from !== 'string' || !interactionIds.has(link.from)
      || typeof link.to !== 'string' || !interactionIds.has(link.to)
    ) return null;
    const key = `${link.from}\u0000${link.to}`;
    if (linkKeys.has(key)) return null;
    linkKeys.add(key);
    links.push({ from: link.from as `ix-${string}`, to: link.to as `ix-${string}` });
  }
  const initialInteractionIds = runtime.initialInteractionIds as Array<`ix-${string}`>;
  const reachable = new Set<string>();
  const queue = [...initialInteractionIds];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (reachable.has(id)) continue;
    reachable.add(id);
    links.forEach((link) => { if (link.from === id) queue.push(link.to); });
  }
  if (reachable.size !== interactionIds.size) return null;
  const byId = new Map(interactions.map(item => [item.id, item]));
  const waitingPositions = [
    initialInteractionIds,
    ...interactions.map(item => links.filter(link => link.from === item.id).map(link => link.to)),
  ];
  for (const ids of waitingPositions) {
    const candidates = ids.map(id => byId.get(id)!);
    if (candidates.filter(item => item.trigger.kind === 'mouse-click').length > 1) return null;
    const delays = candidates.flatMap(item => item.trigger.kind === 'after' ? [item.trigger.delayMs] : []);
    if (new Set(delays).size !== delays.length) return null;
  }
  if (runtime.normalSoundUrl !== undefined && !isStrictAvatarToolResourceUrl(runtime.normalSoundUrl)) return null;
  let special: LocalAvatarToolV3RuntimeProjection['special'];
  if (runtime.special !== undefined) {
    if (!runtime.special || typeof runtime.special !== 'object') return null;
    const value = runtime.special as Record<string, unknown>;
    if (
      !hasOnlyKeys(value, ['probability', 'imageUrl', 'hasMeaning', 'soundUrl'])
      || typeof value.probability !== 'number'
      || !Number.isFinite(value.probability)
      || value.probability <= 0 || value.probability > 1
      || !isStrictAvatarToolResourceUrl(value.imageUrl)
      || typeof value.hasMeaning !== 'boolean'
      || (value.soundUrl !== undefined && !isStrictAvatarToolResourceUrl(value.soundUrl))
    ) return null;
    special = {
      probability: value.probability,
      imageUrl: value.imageUrl,
      hasMeaning: value.hasMeaning,
      ...(typeof value.soundUrl === 'string' ? { soundUrl: value.soundUrl } : {}),
    };
  }
  return {
    images,
    initialImageId: runtime.initialImageId as `img-${string}`,
    initialInteractionIds: [...initialInteractionIds],
    interactions,
    links,
    ...(typeof runtime.normalSoundUrl === 'string' ? { normalSoundUrl: runtime.normalSoundUrl } : {}),
    ...(special ? { special } : {}),
  };
}

function decodeLocalAvatarToolItem(
  value: unknown,
  limits: Pick<LocalAvatarToolLimits, 'maxNameChars' | 'maxImages' | 'maxInteractions' | 'maxLinks' | 'maxDelayMs'>
    & Partial<Pick<LocalAvatarToolLimits, 'maxChangeImages'>>,
): LocalAvatarToolDto | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const special = item.special === undefined ? undefined : decodeSpecial(item.special);
  const commonInvalid = (
    typeof item.id !== 'string' || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(item.id)
    || !isRevision(item.revision)
    || typeof item.name !== 'string'
    || getAvatarToolNameValidationError(item.name, limits.maxNameChars, true) !== null
    || (item.normalSoundUrl !== undefined && !isStrictAvatarToolResourceUrl(item.normalSoundUrl))
    || (item.special !== undefined && !special)
  );
  if (commonInvalid) return null;
  if (item.recordVersion === 3) {
    const runtime = decodeV3RuntimeProjection(item.runtime, limits);
    if (
      !hasOnlyKeys(item, ['recordVersion', 'id', 'revision', 'name', 'initialImageUrl', 'runtime'])
      || !isStrictAvatarToolResourceUrl(item.initialImageUrl)
      || !/^3-\d+$/.test(item.revision as string)
      || !runtime
      || runtime.images.find(image => image.id === runtime.initialImageId)?.url !== item.initialImageUrl
    ) return null;
    return {
      recordVersion: 3,
      id: item.id as LocalAvatarToolId,
      revision: item.revision as string,
      name: item.name as string,
      initialImageUrl: item.initialImageUrl,
      runtime,
    };
  }
  const changeUrls = item.changeUrls;
  if (
    (item.recordVersion !== undefined && item.recordVersion !== 2)
    || !hasOnlyKeys(item, ['recordVersion', 'id', 'revision', 'name', 'changeMode', 'defaultUrl', 'changeUrls', 'normalSoundUrl', 'special'])
    || (item.changeMode !== 'press-swap' && item.changeMode !== 'click-advance')
    || !isStrictAvatarToolResourceUrl(item.defaultUrl)
    || !Array.isArray(changeUrls)
    || changeUrls.length < 1
    || (limits.maxChangeImages !== undefined && changeUrls.length > limits.maxChangeImages)
    || changeUrls.some(url => !isStrictAvatarToolResourceUrl(url))
    || (item.changeMode === 'press-swap' && changeUrls.length !== 1)
  ) return null;
  return {
    ...(item.recordVersion === 2 ? { recordVersion: 2 as const } : {}),
    id: item.id as LocalAvatarToolId,
    revision: item.revision as string,
    name: item.name as string,
    changeMode: item.changeMode,
    defaultUrl: item.defaultUrl,
    changeUrls: [...changeUrls] as string[],
    ...(typeof item.normalSoundUrl === 'string' ? { normalSoundUrl: item.normalSoundUrl } : {}),
    ...(special ? { special } : {}),
  };
}

function assertListResponse(value: unknown): LocalAvatarToolList {
  if (!value || typeof value !== 'object') throw new Error('avatar_tool_list_invalid');
  const payload = value as Record<string, unknown>;
  if (payload.ok !== true || !Array.isArray(payload.items) || !payload.limits || typeof payload.limits !== 'object') {
    throw new Error('avatar_tool_list_invalid');
  }
  const source = payload.limits as Record<string, unknown>;
  const required = [
    'maxTools',
    'maxNameChars',
    'maxMeaningChars',
    'maxChangeImages',
    'maxImages',
    'maxInteractions',
    'maxLinks',
    'maxDelayMs',
    'maxImageBytes',
    'maxImagePixels',
    'maxAudioBytes',
    'maxAudioDurationMs',
    'maxTotalBytes',
  ] as const;
  const limits = {} as LocalAvatarToolLimits;
  required.forEach((key) => {
    if (!Number.isSafeInteger(source[key]) || Number(source[key]) <= 0) throw new Error('avatar_tool_limits_invalid');
    limits[key] = Number(source[key]);
  });
  const items = payload.items.flatMap((candidate): LocalAvatarToolDto[] => {
    const item = decodeLocalAvatarToolItem(candidate, limits);
    return item ? [item] : [];
  });
  return { items, limits };
}

function decodeResource(
  value: unknown,
  allowedExtraKeys: string[] = [],
  expectedResource?: string,
): LocalAvatarToolResource | null {
  if (!value || typeof value !== 'object') return null;
  const resource = value as Record<string, unknown>;
  if (
    !Object.keys(resource).every(key => ['resource', 'url', ...allowedExtraKeys].includes(key))
    || typeof resource.resource !== 'string'
    || !resource.resource
    || resource.resource.includes('/')
    || resource.resource.includes('\\')
    || !LOCAL_AVATAR_TOOL_RESOURCE_PATTERN.test(resource.resource)
    || (expectedResource !== undefined && resource.resource !== expectedResource)
    || !isStrictAvatarToolResourceUrl(resource.url)
  ) return null;
  return { resource: resource.resource, url: resource.url };
}

function decodeImageAction(value: unknown, imageIds: ReadonlySet<string>): LocalAvatarToolImageAction | null {
  if (!value || typeof value !== 'object') return null;
  const action = value as Record<string, unknown>;
  if (action.kind === 'keep' && hasOnlyKeys(action, ['kind'])) return { kind: 'keep' };
  if (
    action.kind === 'show'
    && hasOnlyKeys(action, ['kind', 'imageId'])
    && typeof action.imageId === 'string'
    && LOCAL_AVATAR_TOOL_IMAGE_ID_PATTERN.test(action.imageId)
    && imageIds.has(action.imageId)
  ) return { kind: 'show', imageId: action.imageId as `img-${string}` };
  return null;
}

function decodePosition(value: unknown): { x: number; y: number } | null {
  if (!value || typeof value !== 'object') return null;
  const position = value as Record<string, unknown>;
  return hasOnlyKeys(position, ['x', 'y'])
    && typeof position.x === 'number' && Number.isFinite(position.x)
    && typeof position.y === 'number' && Number.isFinite(position.y)
    ? { x: position.x, y: position.y }
    : null;
}

function decodeSide(value: unknown): LocalAvatarToolConnectionSide | null {
  return typeof value === 'string' && LOCAL_AVATAR_TOOL_CONNECTION_SIDES.has(value as LocalAvatarToolConnectionSide)
    ? value as LocalAvatarToolConnectionSide
    : null;
}

function decodeV3Interactions(
  value: unknown,
  imageIds: ReadonlySet<string>,
  limits: LocalAvatarToolLimits,
): LocalAvatarToolImageInteractions | null {
  if (!value || typeof value !== 'object') return null;
  const source = value as Record<string, unknown>;
  if (
    !hasOnlyKeys(source, ['initialImagePosition', 'initialLinks', 'items', 'links'])
    || !Array.isArray(source.initialLinks)
    || !Array.isArray(source.items)
    || !Array.isArray(source.links)
    || source.items.length < 1
    || source.items.length > limits.maxInteractions
    || source.initialLinks.length + source.links.length > limits.maxLinks
  ) return null;
  const initialImagePosition = decodePosition(source.initialImagePosition);
  if (!initialImagePosition) return null;
  const ids = new Set<string>();
  const names = new Set<string>();
  const items: LocalAvatarToolImageInteractions['items'] = [];
  for (const candidate of source.items) {
    if (!candidate || typeof candidate !== 'object') return null;
    const item = candidate as Record<string, unknown>;
    if (
      !hasOnlyKeys(item, ['id', 'name', 'trigger', 'actions', 'editorPosition'])
      || typeof item.id !== 'string' || item.id.length > LOCAL_AVATAR_TOOL_STABLE_ID_MAX_LENGTH
      || !LOCAL_AVATAR_TOOL_INTERACTION_ID_PATTERN.test(item.id) || ids.has(item.id)
      || typeof item.name !== 'string'
      || getAvatarToolNameValidationError(item.name, limits.maxNameChars) !== null
    ) return null;
    const normalizedName = normalizeAvatarToolComparableName(item.name);
    if (normalizedName && names.has(normalizedName)) return null;
    if (normalizedName) names.add(normalizedName);
    if (!item.trigger || typeof item.trigger !== 'object' || !item.actions || typeof item.actions !== 'object') return null;
    const trigger = item.trigger as Record<string, unknown>;
    const actions = item.actions as Record<string, unknown>;
    const editorPosition = decodePosition(item.editorPosition);
    if (!editorPosition) return null;
    ids.add(item.id);
    if (trigger.kind === 'mouse-click' && hasOnlyKeys(trigger, ['kind']) && hasOnlyKeys(actions, ['press', 'release'])) {
      const press = decodeImageAction(actions.press, imageIds);
      const release = decodeImageAction(actions.release, imageIds);
      if (!press || !release) return null;
      items.push({
        id: item.id as `ix-${string}`,
        name: item.name,
        trigger: { kind: 'mouse-click' },
        actions: { press, release },
        editorPosition,
      });
      continue;
    }
    if (
      trigger.kind === 'after'
      && hasOnlyKeys(trigger, ['kind', 'delayMs'])
      && Number.isSafeInteger(trigger.delayMs)
      && Number(trigger.delayMs) >= 1
      && Number(trigger.delayMs) <= limits.maxDelayMs
      && hasOnlyKeys(actions, ['complete'])
    ) {
      const complete = decodeImageAction(actions.complete, imageIds);
      if (!complete) return null;
      items.push({
        id: item.id as `ix-${string}`,
        name: item.name,
        trigger: { kind: 'after', delayMs: Number(trigger.delayMs) },
        actions: { complete },
        editorPosition,
      });
      continue;
    }
    return null;
  }
  const decodeConnection = (candidate: unknown, initial: boolean) => {
    if (!candidate || typeof candidate !== 'object') return null;
    const link = candidate as Record<string, unknown>;
    const allowed = initial ? ['to', 'sourceSide', 'targetSide'] : ['from', 'to', 'sourceSide', 'targetSide'];
    const from = initial ? undefined : link.from;
    const sourceSide = decodeSide(link.sourceSide);
    const targetSide = decodeSide(link.targetSide);
    if (
      !hasOnlyKeys(link, allowed)
      || (!initial && (typeof from !== 'string' || !ids.has(from)))
      || typeof link.to !== 'string' || !ids.has(link.to)
      || !sourceSide || !targetSide
    ) return null;
    return initial
      ? { to: link.to as `ix-${string}`, sourceSide, targetSide }
      : { from: from as `ix-${string}`, to: link.to as `ix-${string}`, sourceSide, targetSide };
  };
  const initialLinks = source.initialLinks.map(link => decodeConnection(link, true));
  const links = source.links.map(link => decodeConnection(link, false));
  if (initialLinks.some(link => !link) || links.some(link => !link)) return null;
  const cleanInitialLinks = initialLinks as LocalAvatarToolImageInteractions['initialLinks'];
  const cleanLinks = links as LocalAvatarToolImageInteractions['links'];
  const connectionKeys = new Set<string>();
  for (const link of [...cleanInitialLinks, ...cleanLinks]) {
    const key = 'from' in link ? `${link.from}>${link.to}` : `initial>${link.to}`;
    if (connectionKeys.has(key)) return null;
    connectionKeys.add(key);
  }
  const reachable = new Set(cleanInitialLinks.map(link => link.to));
  let changed = true;
  while (changed) {
    changed = false;
    cleanLinks.forEach((link) => {
      if (reachable.has(link.from) && !reachable.has(link.to)) {
        reachable.add(link.to);
        changed = true;
      }
    });
  }
  if (reachable.size !== ids.size) return null;
  const itemById = new Map(items.map(item => [item.id, item]));
  const waitingGroups = [
    cleanInitialLinks.map(link => link.to),
    ...items.map(item => cleanLinks.filter(link => link.from === item.id).map(link => link.to)),
  ];
  for (const targetIds of waitingGroups) {
    const candidates = targetIds.map(targetId => itemById.get(targetId)!);
    if (candidates.filter(item => item.trigger.kind === 'mouse-click').length > 1) return null;
    const delays = candidates.flatMap(item => item.trigger.kind === 'after' ? [item.trigger.delayMs] : []);
    if (new Set(delays).size !== delays.length) return null;
  }
  return {
    initialImagePosition,
    initialLinks: cleanInitialLinks,
    items,
    links: cleanLinks,
  };
}

function decodeV2Detail(detail: Record<string, unknown>, limits: LocalAvatarToolLimits): LocalAvatarToolV2Detail | null {
  const defaultImage = decodeResource(detail.defaultImage, [], 'default.png');
  if (
    (detail.recordVersion !== undefined && detail.recordVersion !== 2)
    || !hasOnlyKeys(detail, ['recordVersion', 'id', 'revision', 'name', 'changeMode', 'defaultImage', 'changeItems', 'normalSound', 'special'])
    || typeof detail.id !== 'string'
    || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(detail.id)
    || !isRevision(detail.revision)
    || typeof detail.name !== 'string'
    || getAvatarToolNameValidationError(detail.name, limits.maxNameChars, true) !== null
    || (detail.changeMode !== 'press-swap' && detail.changeMode !== 'click-advance')
    || !defaultImage
    || !Array.isArray(detail.changeItems)
    || detail.changeItems.length < 1
    || detail.changeItems.length > limits.maxChangeImages
    || (detail.changeMode === 'press-swap' && detail.changeItems.length !== 1)
  ) return null;
  const changeItems = detail.changeItems.flatMap((candidate, index) => {
    if (!candidate || typeof candidate !== 'object') return [];
    const item = candidate as Record<string, unknown>;
    const resource = decodeResource(item, ['meaning'], `change-${String(index).padStart(3, '0')}.png`);
    return resource && isValidAvatarToolMeaning(item.meaning, limits.maxMeaningChars, true)
      ? [{ ...resource, meaning: item.meaning }]
      : [];
  });
  if (changeItems.length !== detail.changeItems.length) return null;
  const normalSound = detail.normalSound === undefined ? undefined : decodeResource(detail.normalSound, [], 'normal.mp3');
  if (detail.normalSound !== undefined && !normalSound) return null;
  let special: LocalAvatarToolV2Detail['special'];
  if (detail.special !== undefined) {
    if (!detail.special || typeof detail.special !== 'object') return null;
    const source = detail.special as Record<string, unknown>;
    if (!hasOnlyKeys(source, ['probability', 'image', 'meaning', 'sound'])) return null;
    const image = decodeResource(source.image, [], 'special.png');
    const sound = source.sound === undefined ? undefined : decodeResource(source.sound, [], 'special.mp3');
    if (
      typeof source.probability !== 'number'
      || !Number.isFinite(source.probability)
      || source.probability <= 0
      || source.probability > 1
      || !image
      || !isValidAvatarToolMeaning(source.meaning, limits.maxMeaningChars, true)
      || (source.sound !== undefined && !sound)
    ) return null;
    special = {
      probability: source.probability,
      image,
      meaning: source.meaning,
      ...(sound ? { sound } : {}),
    };
  }
  return {
    ...(detail.recordVersion === 2 ? { recordVersion: 2 as const } : {}),
    id: detail.id as LocalAvatarToolId,
    revision: detail.revision,
    name: detail.name,
    changeMode: detail.changeMode,
    defaultImage,
    changeItems,
    ...(normalSound ? { normalSound } : {}),
    ...(special ? { special } : {}),
  };
}

function decodeV3Detail(detail: Record<string, unknown>, limits: LocalAvatarToolLimits): LocalAvatarToolV3Detail | null {
  if (
    !hasOnlyKeys(detail, ['recordVersion', 'id', 'revision', 'name', 'images', 'initialImageId', 'imageInteractions', 'normalSound', 'special'])
    || detail.recordVersion !== 3
    || typeof detail.id !== 'string' || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(detail.id)
    || !isRevision(detail.revision) || !/^3-\d+$/.test(detail.revision)
    || typeof detail.name !== 'string'
    || getAvatarToolNameValidationError(detail.name, limits.maxNameChars, true) !== null
    || !Array.isArray(detail.images) || detail.images.length < 1 || detail.images.length > limits.maxImages
    || typeof detail.initialImageId !== 'string'
  ) return null;
  const imageIds = new Set<string>();
  const imageNames = new Set<string>();
  const images: LocalAvatarToolV3Detail['images'] = [];
  for (const [index, candidate] of detail.images.entries()) {
    if (!candidate || typeof candidate !== 'object') return null;
    const item = candidate as Record<string, unknown>;
    const resource = decodeResource(item, ['id', 'name', 'meaning']);
    if (
      !resource
      || resource.resource !== `image-${String(index).padStart(3, '0')}.png`
      || typeof item.id !== 'string' || item.id.length > LOCAL_AVATAR_TOOL_STABLE_ID_MAX_LENGTH
      || !LOCAL_AVATAR_TOOL_IMAGE_ID_PATTERN.test(item.id) || imageIds.has(item.id)
      || typeof item.name !== 'string'
      || getAvatarToolNameValidationError(item.name, limits.maxNameChars) !== null
      || !isValidAvatarToolMeaning(item.meaning, limits.maxMeaningChars)
    ) return null;
    const normalizedName = normalizeAvatarToolComparableName(item.name);
    if (normalizedName && imageNames.has(normalizedName)) return null;
    if (normalizedName) imageNames.add(normalizedName);
    imageIds.add(item.id);
    images.push({ id: item.id as `img-${string}`, name: item.name, meaning: item.meaning, ...resource });
  }
  if (!imageIds.has(detail.initialImageId)) return null;
  const imageInteractions = decodeV3Interactions(detail.imageInteractions, imageIds, limits);
  if (!imageInteractions) return null;
  const normalSound = detail.normalSound === undefined ? undefined : decodeResource(detail.normalSound, [], 'normal.mp3');
  if (detail.normalSound !== undefined && !normalSound) return null;
  let special: LocalAvatarToolV3Detail['special'];
  if (detail.special !== undefined) {
    if (!detail.special || typeof detail.special !== 'object') return null;
    const source = detail.special as Record<string, unknown>;
    if (!hasOnlyKeys(source, ['probability', 'image', 'meaning', 'sound'])) return null;
    const image = decodeResource(source.image, [], 'special.png');
    const sound = source.sound === undefined ? undefined : decodeResource(source.sound, [], 'special.mp3');
    if (
      typeof source.probability !== 'number' || !Number.isFinite(source.probability)
      || source.probability <= 0 || source.probability > 1 || !image
      || !isValidAvatarToolMeaning(source.meaning, limits.maxMeaningChars, true)
      || (source.sound !== undefined && !sound)
    ) return null;
    special = { probability: source.probability, image, meaning: source.meaning, ...(sound ? { sound } : {}) };
  }
  return {
    recordVersion: 3,
    id: detail.id as LocalAvatarToolId,
    revision: detail.revision as string,
    name: detail.name,
    images,
    initialImageId: detail.initialImageId as `img-${string}`,
    imageInteractions,
    ...(normalSound ? { normalSound } : {}),
    ...(special ? { special } : {}),
  };
}

function decodeLocalAvatarToolDetail(value: unknown, limits: LocalAvatarToolLimits): LocalAvatarToolDetail | null {
  if (!value || typeof value !== 'object') return null;
  const detail = value as Record<string, unknown>;
  return detail.recordVersion === 3 ? decodeV3Detail(detail, limits) : decodeV2Detail(detail, limits);
}

export async function fetchLocalAvatarTools(): Promise<LocalAvatarToolList> {
  const response = await fetch('/api/avatar-tools', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('avatar_tool_list_failed');
  return assertListResponse(await response.json());
}

export type LocalAvatarToolDetailResponse = {
  detail: LocalAvatarToolDetail;
  limits: LocalAvatarToolLimits;
};

export async function fetchLocalAvatarToolDetailWithLimits(
  toolId: LocalAvatarToolId,
): Promise<LocalAvatarToolDetailResponse> {
  if (!LOCAL_AVATAR_TOOL_ID_PATTERN.test(toolId)) throw new LocalAvatarToolDetailError('invalid_tool_id');
  const response = await fetch(`/api/avatar-tools/${encodeURIComponent(toolId)}`, {
    credentials: 'same-origin',
    cache: 'no-store',
  });
  let payload: Record<string, unknown> = {};
  try {
    payload = await response.json() as Record<string, unknown>;
  } catch {}
  if (!response.ok || payload.ok !== true) {
    throw new LocalAvatarToolDetailError(String(payload.error_code ?? 'avatar_tool_detail_failed'));
  }
  let limitsPayload: LocalAvatarToolLimits;
  try {
    limitsPayload = assertListResponse({ ok: true, items: [], limits: payload.limits }).limits;
  } catch {
    throw new LocalAvatarToolDetailError('avatar_tool_limits_invalid');
  }
  const detail = decodeLocalAvatarToolDetail(payload.detail, limitsPayload);
  if (!detail || detail.id !== toolId) throw new LocalAvatarToolDetailError('avatar_tool_detail_invalid');
  return { detail, limits: limitsPayload };
}

export async function fetchLocalAvatarToolDetail(
  toolId: LocalAvatarToolId,
): Promise<LocalAvatarToolDetail> {
  return (await fetchLocalAvatarToolDetailWithLimits(toolId)).detail;
}

declare global {
  interface Window {
    nekoLocalMutationSecurity?: {
      getMutationHeaders?: () => Promise<Record<string, string>> | Record<string, string>;
      refreshToken?: () => Promise<unknown> | unknown;
    };
  }
}

async function postLocalAvatarTool(
  input: CreateLocalAvatarToolInput,
  retry: boolean,
): Promise<LocalAvatarToolDto> {
  const isV3 = 'images' in input;
  const form = isV3 ? buildV3Form(input.toolId, input) : new FormData();
  if (!isV3) {
    form.set('tool_id', input.toolId);
    form.set('name', input.name);
    form.set('change_mode', input.changeMode);
    form.set('default_image', input.defaultImage);
    input.changeItems.forEach((item) => {
      form.append('change_images', item.image);
      form.append('change_meanings', item.meaning);
    });
    if (input.normalSound) form.set('normal_sound', input.normalSound);
    if (input.special) {
      form.set('special_probability', String(input.special.probability));
      form.set('special_image', input.special.image);
      form.set('special_meaning', input.special.meaning);
      if (input.special.sound) form.set('special_sound', input.special.sound);
    }
  }
  const security = window.nekoLocalMutationSecurity;
  const headers = security?.getMutationHeaders ? await security.getMutationHeaders() : {};
  const response = await fetch('/api/avatar-tools', {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: form,
  });
  if (response.ok) {
    try {
      const payload = await response.json() as Record<string, unknown>;
      const item = payload.ok === true
        ? decodeLocalAvatarToolItem(payload.item, {
          maxNameChars: countAvatarToolNameCharacters(normalizeAvatarToolName(input.name)),
          maxImages: isV3 ? input.images.length : 1,
          maxInteractions: isV3 ? input.imageInteractions.items.length : 1,
          maxLinks: isV3
            ? input.imageInteractions.initialLinks.length + input.imageInteractions.links.length
            : 1,
          maxDelayMs: isV3 ? Math.max(1, ...input.imageInteractions.items.flatMap(item => (
            item.trigger.kind === 'after' ? [item.trigger.delayMs] : []
          ))) : 1,
          ...(!isV3 ? { maxChangeImages: input.changeItems.length } : {}),
        })
        : null;
      if (item && item.id === input.toolId) return item;
    } catch (cause) {
      if (cause instanceof LocalAvatarToolCreateError) throw cause;
    }
    throw new LocalAvatarToolCreateError('avatar_tool_create_response_invalid');
  }
  let errorCode = '';
  let errorField: string | undefined;
  let errorIndex: number | undefined;
  try {
    const payload = await response.json() as Record<string, unknown>;
    errorCode = String(payload.error_code ?? '');
    if (typeof payload.field === 'string' && payload.field) errorField = payload.field;
    if (Number.isSafeInteger(payload.index) && Number(payload.index) >= 0) errorIndex = Number(payload.index);
  } catch {}
  if (!retry && response.status === 403 && errorCode === 'csrf_validation_failed' && security?.refreshToken) {
    await security.refreshToken();
    return postLocalAvatarTool(input, true);
  }
  throw new LocalAvatarToolCreateError(
    errorCode || 'avatar_tool_create_failed',
    { field: errorField, index: errorIndex },
  );
}

export function createLocalAvatarTool(input: CreateLocalAvatarToolV2Input): Promise<LocalAvatarToolV2Dto>;
export function createLocalAvatarTool(input: CreateLocalAvatarToolV3Input): Promise<LocalAvatarToolV3Dto>;
export function createLocalAvatarTool(input: CreateLocalAvatarToolInput): Promise<LocalAvatarToolDto>;
export async function createLocalAvatarTool(input: CreateLocalAvatarToolInput): Promise<LocalAvatarToolDto> {
  return postLocalAvatarTool(input, false);
}

type V3TransportSource = { kind: 'upload'; index: number } | { kind: 'resource'; name: string };

function buildV3Form(
  toolId: LocalAvatarToolId,
  input: LocalAvatarToolV3SaveInput,
  baseRevision?: string,
): FormData {
  const form = new FormData();
  const uploads: File[] = [];
  const mediaSource = (media: LocalAvatarToolMediaInput, field: string): V3TransportSource => {
    if (media.file) {
      const index = uploads.length;
      uploads.push(media.file);
      return { kind: 'upload', index };
    }
    if (media.resource) return { kind: 'resource', name: media.resource };
    throw new LocalAvatarToolCreateError('resource_source_invalid', { field });
  };
  const manifest = {
    recordVersion: 3,
    id: toolId,
    name: input.name,
    images: input.images.map(image => ({
      id: image.id,
      name: image.name,
      source: mediaSource(image.image, 'image'),
      meaning: image.meaning,
    })),
    initialImageId: input.initialImageId,
    imageInteractions: input.imageInteractions,
    interaction: {
      ...(input.normalSound ? { normalSound: mediaSource(input.normalSound, 'normal_sound') } : {}),
      ...(input.special ? {
        special: {
          probability: input.special.probability,
          image: mediaSource(input.special.image, 'special_image'),
          meaning: input.special.meaning,
          ...(input.special.sound ? { sound: mediaSource(input.special.sound, 'special_sound') } : {}),
        },
      } : {}),
    },
  };
  if (baseRevision !== undefined) form.set('base_revision', baseRevision);
  form.set('record_version', '3');
  form.set('manifest', JSON.stringify(manifest));
  uploads.forEach(file => form.append('uploads', file));
  return form;
}

async function putLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolInput,
  retry: boolean,
): Promise<LocalAvatarToolDto> {
  const isV3 = 'images' in input;
  const form = isV3 ? buildV3Form(toolId, input, input.baseRevision) : new FormData();
  if (!isV3) {
    form.set('base_revision', input.baseRevision);
    form.set('name', input.name);
    form.set('change_mode', input.changeMode);
    if (input.defaultImage.file) form.set('default_image', input.defaultImage.file);
    else if (input.defaultImage.resource) form.set('default_resource', input.defaultImage.resource);
    input.changeItems.forEach((item) => {
      form.append('change_resources', item.file ? '' : (item.resource ?? ''));
      form.append('change_meanings', item.meaning);
      if (item.file) form.append('change_images', item.file);
    });
    if (input.normalSound?.file) form.set('normal_sound', input.normalSound.file);
    else if (input.normalSound?.resource) form.set('normal_sound_resource', input.normalSound.resource);
    if (input.special) {
      form.set('special_probability', String(input.special.probability));
      form.set('special_meaning', input.special.meaning);
      if (input.special.image.file) form.set('special_image', input.special.image.file);
      else if (input.special.image.resource) form.set('special_image_resource', input.special.image.resource);
      if (input.special.sound?.file) form.set('special_sound', input.special.sound.file);
      else if (input.special.sound?.resource) form.set('special_sound_resource', input.special.sound.resource);
    }
  }
  const security = window.nekoLocalMutationSecurity;
  const headers = security?.getMutationHeaders ? await security.getMutationHeaders() : {};
  const response = await fetch(`/api/avatar-tools/${encodeURIComponent(toolId)}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers,
    body: form,
  });
  if (response.ok) {
    try {
      const payload = await response.json() as Record<string, unknown>;
      const item = payload.ok === true
        ? decodeLocalAvatarToolItem(payload.item, {
          maxNameChars: countAvatarToolNameCharacters(normalizeAvatarToolName(input.name)),
          maxImages: isV3 ? input.images.length : 1,
          maxInteractions: isV3 ? input.imageInteractions.items.length : 1,
          maxLinks: isV3
            ? input.imageInteractions.initialLinks.length + input.imageInteractions.links.length
            : 1,
          maxDelayMs: isV3 ? Math.max(1, ...input.imageInteractions.items.flatMap(item => (
            item.trigger.kind === 'after' ? [item.trigger.delayMs] : []
          ))) : 1,
          ...(!isV3 ? { maxChangeImages: input.changeItems.length } : {}),
        })
        : null;
      if (item && item.id === toolId) return item;
    } catch (cause) {
      if (cause instanceof LocalAvatarToolCreateError) throw cause;
    }
    throw new LocalAvatarToolCreateError('avatar_tool_update_response_invalid');
  }
  let errorCode = '';
  let errorField: string | undefined;
  let errorIndex: number | undefined;
  try {
    const payload = await response.json() as Record<string, unknown>;
    errorCode = String(payload.error_code ?? '');
    if (typeof payload.field === 'string' && payload.field) errorField = payload.field;
    if (Number.isSafeInteger(payload.index) && Number(payload.index) >= 0) errorIndex = Number(payload.index);
  } catch {}
  if (!retry && response.status === 403 && errorCode === 'csrf_validation_failed' && security?.refreshToken) {
    await security.refreshToken();
    return putLocalAvatarTool(toolId, input, true);
  }
  throw new LocalAvatarToolCreateError(
    errorCode || 'avatar_tool_update_failed',
    { field: errorField, index: errorIndex },
  );
}

export function updateLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolV2Input,
): Promise<LocalAvatarToolV2Dto>;
export function updateLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolV3Input,
): Promise<LocalAvatarToolV3Dto>;
export function updateLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolInput,
): Promise<LocalAvatarToolDto>;
export async function updateLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolInput,
): Promise<LocalAvatarToolDto> {
  if (!LOCAL_AVATAR_TOOL_ID_PATTERN.test(toolId)) {
    throw new LocalAvatarToolCreateError('invalid_tool_id');
  }
  return putLocalAvatarTool(toolId, input, false);
}

async function deleteLocalAvatarToolRequest(toolId: LocalAvatarToolId, retry: boolean): Promise<void> {
  const security = window.nekoLocalMutationSecurity;
  const headers = security?.getMutationHeaders ? await security.getMutationHeaders() : {};
  const response = await fetch(`/api/avatar-tools/${encodeURIComponent(toolId)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers,
  });
  let payload: Record<string, unknown> = {};
  try {
    payload = await response.json() as Record<string, unknown>;
  } catch {}
  if (response.ok && payload.ok === true && payload.deletedId === toolId) return;
  const errorCode = String(payload.error_code ?? '');
  if (!retry && response.status === 403 && errorCode === 'csrf_validation_failed' && security?.refreshToken) {
    await security.refreshToken();
    return deleteLocalAvatarToolRequest(toolId, true);
  }
  throw new LocalAvatarToolDeleteError(errorCode || 'avatar_tool_delete_failed');
}

export async function deleteLocalAvatarTool(toolId: LocalAvatarToolId): Promise<void> {
  if (!LOCAL_AVATAR_TOOL_ID_PATTERN.test(toolId)) {
    throw new LocalAvatarToolDeleteError('invalid_tool_id');
  }
  await deleteLocalAvatarToolRequest(toolId, false);
}

export function buildLocalAvatarToolDefinition(item: LocalAvatarToolDto): AvatarToolDefinition {
  if (item.recordVersion === 3) {
    const initial = item.runtime.images.find(image => image.id === item.runtime.initialImageId)!;
    const initialVariant = {
      iconImagePath: initial.url,
      pointerImagePath: initial.url,
      menuOffsetX: 0,
      menuOffsetY: 0,
    };
    const frames = item.runtime.images.map(image => ({
      iconImagePath: image.url,
      pointerImagePath: image.url,
      menuOffsetX: 0,
      menuOffsetY: 0,
    }));
    const normalSound = item.runtime.normalSoundUrl ? {
      id: 'normal-feedback',
      src: item.runtime.normalSoundUrl,
      volume: 0.9,
    } : null;
    const specialSound = item.runtime.special?.soundUrl ? {
      id: 'special-feedback',
      src: item.runtime.special.soundUrl,
      volume: 0.9,
    } : null;
    const specialEffect = item.runtime.special ? {
      ...LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE,
      assetPath: item.runtime.special.imageUrl,
    } : null;
    return {
      definitionVersion: 3,
      id: item.id,
      label: { kind: 'literal', value: item.name },
      capability: { desktopVisual: true, desktopInteraction: true },
      visual: {
        initialVariant: 'primary',
        variants: { primary: initialVariant, secondary: initialVariant, tertiary: initialVariant },
        frames,
        presentation: {
          inRangeVariantSource: 'range',
          outsideVariantSource: 'outside',
          effectActiveImageKind: 'pointer',
        },
        menuScale: 1,
        hotspotX: 40,
        hotspotY: 40,
        naturalWidth: 80,
        naturalHeight: 80,
        pointer: {
          displayWidth: 80,
          displayHeight: 80,
          displayCoordinateSpace: 'pre-scale-css-pixel',
          scale: 0.62,
          renderedAnchor: { x: 24.8, y: 24.8, coordinateSpace: 'final-css-pixel' },
        },
        inRange: {
          displayWidth: 80,
          displayHeight: 80,
          displayCoordinateSpace: 'pre-scale-css-pixel',
          scale: 1,
          renderedAnchor: { x: 40, y: 40, coordinateSpace: 'final-css-pixel' },
        },
      },
      sounds: [normalSound, specialSound].filter((sound): sound is NonNullable<typeof sound> => !!sound),
      effects: specialEffect ? [specialEffect] : [],
      interaction: {
        kind: 'custom-graph',
        revision: item.revision,
        images: item.runtime.images.map((image, frameIndex) => ({
          id: image.id,
          frameIndex,
          hasMeaning: image.hasMeaning,
        })),
        initialImageId: item.runtime.initialImageId,
        initialInteractionIds: [...item.runtime.initialInteractionIds],
        interactions: item.runtime.interactions.map(interaction => ({
          id: interaction.id,
          trigger: interaction.trigger,
          actions: interaction.actions,
        })),
        links: item.runtime.links.map(link => ({ ...link })),
        burst: {
          key: item.id,
          windowMs: 1800,
          rapidThreshold: 3,
          normalIntensity: 'normal',
          rapidIntensity: 'rapid',
        },
        touchZone: 'release',
        touchZones: ['ear', 'head', 'face', 'body'],
        ...(normalSound ? { feedback: { sound: normalSound.id } } : {}),
        ...(item.runtime.special ? {
          chance: {
            field: 'specialTriggered',
            probability: item.runtime.special.probability,
            effect: LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE.id,
            ...(specialSound ? { sound: specialSound.id } : {}),
          },
        } : {}),
      },
    };
  }
  const defaultVariant = {
    iconImagePath: item.defaultUrl,
    pointerImagePath: item.defaultUrl,
    menuOffsetX: 0,
    menuOffsetY: 0,
  };
  const frames = [item.defaultUrl, ...item.changeUrls].map(path => ({
    iconImagePath: path,
    pointerImagePath: path,
    menuOffsetX: 0,
    menuOffsetY: 0,
  }));
  const normalSound = item.normalSoundUrl ? {
    id: 'normal-feedback',
    src: item.normalSoundUrl,
    volume: 0.9,
  } : null;
  const specialSound = item.special?.soundUrl ? {
    id: 'special-feedback',
    src: item.special.soundUrl,
    volume: 0.9,
  } : null;
  const specialEffect = item.special ? {
    ...LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE,
    assetPath: item.special.imageUrl,
  } : null;
  return {
    definitionVersion: 2,
    id: item.id,
    label: { kind: 'literal', value: item.name },
    capability: { desktopVisual: true, desktopInteraction: true },
    visual: {
      initialVariant: 'primary',
      variants: { primary: defaultVariant, secondary: defaultVariant, tertiary: defaultVariant },
      frames,
      presentation: {
        inRangeVariantSource: 'range',
        outsideVariantSource: 'outside',
        effectActiveImageKind: 'pointer',
      },
      menuScale: 1,
      hotspotX: 40,
      hotspotY: 40,
      naturalWidth: 80,
      naturalHeight: 80,
      pointer: {
        displayWidth: 80,
        displayHeight: 80,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 0.62,
        renderedAnchor: { x: 24.8, y: 24.8, coordinateSpace: 'final-css-pixel' },
      },
      inRange: {
        displayWidth: 80,
        displayHeight: 80,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 1,
        renderedAnchor: { x: 40, y: 40, coordinateSpace: 'final-css-pixel' },
      },
    },
    sounds: [normalSound, specialSound].filter((sound): sound is NonNullable<typeof sound> => !!sound),
    effects: specialEffect ? [specialEffect] : [],
    interaction: {
      kind: 'press-release',
      revision: item.revision,
      actionId: 'interact',
      imageChange: { kind: item.changeMode },
      burst: {
        key: item.id,
        windowMs: 1800,
        rapidThreshold: 3,
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
      },
      touchZone: 'release',
      touchZones: ['ear', 'head', 'face', 'body'],
      ...(normalSound ? { feedback: { sound: normalSound.id } } : {}),
      ...(item.special ? {
        chance: {
          field: 'specialTriggered',
          probability: item.special.probability,
          effect: LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE.id,
          ...(specialSound ? { sound: specialSound.id } : {}),
        },
      } : {}),
    },
  };
}
