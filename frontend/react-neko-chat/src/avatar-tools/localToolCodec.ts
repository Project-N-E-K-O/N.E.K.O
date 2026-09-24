import {
  hasValidAvatarToolAssetVersion,
  isAvatarToolSameOriginAssetPath,
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  type LocalAvatarToolId,
} from './catalog';
import {
  getAvatarToolNameValidationError,
  normalizeAvatarToolComparableName,
} from './avatarToolNames';
import type {
  LocalAvatarToolConnectionSide,
  LocalAvatarToolDetail,
  LocalAvatarToolDto,
  LocalAvatarToolImageAction,
  LocalAvatarToolImageInteractions,
  LocalAvatarToolLimits,
  LocalAvatarToolList,
  LocalAvatarToolResource,
  LocalAvatarToolV2Detail,
  LocalAvatarToolV2Dto,
  LocalAvatarToolV3Detail,
  LocalAvatarToolV3RuntimeInteraction,
  LocalAvatarToolV3RuntimeProjection,
} from './localToolTypes';

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

export function decodeLocalAvatarToolItem(
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
    item.recordVersion !== 2
    || !/^2-\d+$/.test(item.revision as string)
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
    recordVersion: 2,
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

export function assertListResponse(value: unknown): LocalAvatarToolList {
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
    detail.recordVersion !== 2
    || !hasOnlyKeys(detail, ['recordVersion', 'id', 'revision', 'name', 'changeMode', 'defaultImage', 'changeItems', 'normalSound', 'special'])
    || typeof detail.id !== 'string'
    || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(detail.id)
    || !isRevision(detail.revision) || !/^2-\d+$/.test(detail.revision)
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
    recordVersion: 2,
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

export function decodeLocalAvatarToolDetail(value: unknown, limits: LocalAvatarToolLimits): LocalAvatarToolDetail | null {
  if (!value || typeof value !== 'object') return null;
  const detail = value as Record<string, unknown>;
  return detail.recordVersion === 3 ? decodeV3Detail(detail, limits) : decodeV2Detail(detail, limits);
}
