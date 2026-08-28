import {
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  type AvatarToolDefinition,
  type LocalAvatarToolId,
  type RandomScatterEffectRecipe,
} from './catalog';

export type LocalAvatarToolLimits = {
  maxTools: number;
  maxNameChars: number;
  maxMeaningChars: number;
  maxChangeImages: number;
  maxImageBytes: number;
  maxImagePixels: number;
  maxAudioBytes: number;
  maxAudioDurationMs: number;
  maxTotalBytes: number;
};

export type LocalAvatarToolDto = {
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

export type LocalAvatarToolChangeMode = 'press-swap' | 'click-advance';

export type LocalAvatarToolList = {
  items: LocalAvatarToolDto[];
  limits: LocalAvatarToolLimits;
};

export type CreateLocalAvatarToolInput = {
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

export type LocalAvatarToolDetail = {
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

export type UpdateLocalAvatarToolInput = {
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

function decodeSpecial(value: unknown): LocalAvatarToolDto['special'] | null {
  if (!value || typeof value !== 'object') return null;
  const special = value as Record<string, unknown>;
  if (
    !Object.keys(special).every(key => ['probability', 'imageUrl', 'soundUrl'].includes(key))
    || typeof special.probability !== 'number'
    || !Number.isFinite(special.probability)
    || special.probability <= 0
    || special.probability > 1
    || typeof special.imageUrl !== 'string'
    || !special.imageUrl
    || (special.soundUrl !== undefined && (typeof special.soundUrl !== 'string' || !special.soundUrl))
  ) return null;
  return {
    probability: special.probability,
    imageUrl: special.imageUrl,
    ...(typeof special.soundUrl === 'string' ? { soundUrl: special.soundUrl } : {}),
  };
}

function decodeLocalAvatarToolItem(value: unknown, maxChangeImages: number): LocalAvatarToolDto | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const changeUrls = item.changeUrls;
  const special = item.special === undefined ? undefined : decodeSpecial(item.special);
  if (
    typeof item.id !== 'string' || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(item.id)
    || typeof item.revision !== 'string' || !/^\d+-\d+$/.test(item.revision) || item.revision.length > 128
    || typeof item.name !== 'string' || !item.name.trim()
    || (item.changeMode !== 'press-swap' && item.changeMode !== 'click-advance')
    || typeof item.defaultUrl !== 'string' || !item.defaultUrl
    || !Array.isArray(changeUrls)
    || changeUrls.length < 1
    || changeUrls.length > maxChangeImages
    || changeUrls.some(url => typeof url !== 'string' || !url)
    || (item.changeMode === 'press-swap' && changeUrls.length !== 1)
    || (item.normalSoundUrl !== undefined && (typeof item.normalSoundUrl !== 'string' || !item.normalSoundUrl))
    || (item.special !== undefined && !special)
  ) return null;
  return {
    id: item.id as LocalAvatarToolId,
    revision: item.revision,
    name: item.name,
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
    const item = decodeLocalAvatarToolItem(candidate, limits.maxChangeImages);
    return item ? [item] : [];
  });
  return { items, limits };
}

function decodeResource(value: unknown, allowedExtraKeys: string[] = []): LocalAvatarToolResource | null {
  if (!value || typeof value !== 'object') return null;
  const resource = value as Record<string, unknown>;
  if (
    !Object.keys(resource).every(key => ['resource', 'url', ...allowedExtraKeys].includes(key))
    || typeof resource.resource !== 'string'
    || !resource.resource
    || resource.resource.includes('/')
    || resource.resource.includes('\\')
    || typeof resource.url !== 'string'
    || !resource.url
  ) return null;
  return { resource: resource.resource, url: resource.url };
}

function decodeLocalAvatarToolDetail(value: unknown, maxChangeImages: number): LocalAvatarToolDetail | null {
  if (!value || typeof value !== 'object') return null;
  const detail = value as Record<string, unknown>;
  const defaultImage = decodeResource(detail.defaultImage);
  if (
    typeof detail.id !== 'string'
    || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(detail.id)
    || typeof detail.revision !== 'string'
    || !/^\d+-\d+$/.test(detail.revision)
    || typeof detail.name !== 'string'
    || !detail.name.trim()
    || (detail.changeMode !== 'press-swap' && detail.changeMode !== 'click-advance')
    || !defaultImage
    || !Array.isArray(detail.changeItems)
    || detail.changeItems.length < 1
    || detail.changeItems.length > maxChangeImages
    || (detail.changeMode === 'press-swap' && detail.changeItems.length !== 1)
  ) return null;
  const changeItems = detail.changeItems.flatMap((candidate) => {
    if (!candidate || typeof candidate !== 'object') return [];
    const item = candidate as Record<string, unknown>;
    const resource = decodeResource(item, ['meaning']);
    return resource && typeof item.meaning === 'string' && item.meaning.trim()
      ? [{ ...resource, meaning: item.meaning }]
      : [];
  });
  if (changeItems.length !== detail.changeItems.length) return null;
  const normalSound = detail.normalSound === undefined ? undefined : decodeResource(detail.normalSound);
  if (detail.normalSound !== undefined && !normalSound) return null;
  let special: LocalAvatarToolDetail['special'];
  if (detail.special !== undefined) {
    if (!detail.special || typeof detail.special !== 'object') return null;
    const source = detail.special as Record<string, unknown>;
    const image = decodeResource(source.image);
    const sound = source.sound === undefined ? undefined : decodeResource(source.sound);
    if (
      typeof source.probability !== 'number'
      || !Number.isFinite(source.probability)
      || source.probability <= 0
      || source.probability > 1
      || !image
      || typeof source.meaning !== 'string'
      || !source.meaning.trim()
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

export async function fetchLocalAvatarTools(): Promise<LocalAvatarToolList> {
  const response = await fetch('/api/avatar-tools', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('avatar_tool_list_failed');
  return assertListResponse(await response.json());
}

export async function fetchLocalAvatarToolDetail(
  toolId: LocalAvatarToolId,
  maxChangeImages: number,
): Promise<LocalAvatarToolDetail> {
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
  const detail = decodeLocalAvatarToolDetail(payload.detail, maxChangeImages);
  if (!detail || detail.id !== toolId) throw new LocalAvatarToolDetailError('avatar_tool_detail_invalid');
  return detail;
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
): Promise<LocalAvatarToolDto | null> {
  const form = new FormData();
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
      return payload.ok === true ? decodeLocalAvatarToolItem(payload.item, input.changeItems.length) : null;
    } catch {
      return null;
    }
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

export async function createLocalAvatarTool(input: CreateLocalAvatarToolInput): Promise<LocalAvatarToolDto | null> {
  return postLocalAvatarTool(input, false);
}

async function putLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolInput,
  retry: boolean,
): Promise<LocalAvatarToolDto | null> {
  const form = new FormData();
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
      return payload.ok === true ? decodeLocalAvatarToolItem(payload.item, input.changeItems.length) : null;
    } catch {
      return null;
    }
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

export async function updateLocalAvatarTool(
  toolId: LocalAvatarToolId,
  input: UpdateLocalAvatarToolInput,
): Promise<LocalAvatarToolDto | null> {
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
