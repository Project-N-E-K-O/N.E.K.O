import { LOCAL_AVATAR_TOOL_ID_PATTERN, type LocalAvatarToolId } from './catalog';
import {
  countAvatarToolNameCharacters,
  normalizeAvatarToolName,
} from './avatarToolNames';
import {
  assertListResponse,
  decodeLocalAvatarToolDetail,
  decodeLocalAvatarToolItem,
} from './localToolCodec';
import {
  LocalAvatarToolCreateError,
  LocalAvatarToolDeleteError,
  LocalAvatarToolDetailError,
  type CreateLocalAvatarToolInput,
  type CreateLocalAvatarToolV2Input,
  type CreateLocalAvatarToolV3Input,
  type LocalAvatarToolDetail,
  type LocalAvatarToolDto,
  type LocalAvatarToolLimits,
  type LocalAvatarToolList,
  type LocalAvatarToolMediaInput,
  type LocalAvatarToolV2Dto,
  type LocalAvatarToolV3Dto,
  type LocalAvatarToolV3SaveInput,
  type UpdateLocalAvatarToolInput,
  type UpdateLocalAvatarToolV2Input,
  type UpdateLocalAvatarToolV3Input,
} from './localToolTypes';

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
