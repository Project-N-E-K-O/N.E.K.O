import {
  LOCAL_AVATAR_TOOL_ID_PATTERN,
  type AvatarToolDefinition,
  type LocalAvatarToolId,
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
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultUrl: string;
  changeUrls: string[];
  normalSoundUrl?: string;
};

export type LocalAvatarToolChangeMode = 'press-swap' | 'click-advance';

export type LocalAvatarToolList = {
  items: LocalAvatarToolDto[];
  limits: LocalAvatarToolLimits;
};

export type CreateLocalAvatarToolInput = {
  name: string;
  changeMode: LocalAvatarToolChangeMode;
  defaultImage: File;
  changeItems: Array<{ image: File; meaning: string }>;
  normalSound?: File;
};

function decodeLocalAvatarToolItem(value: unknown): LocalAvatarToolDto | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const changeUrls = item.changeUrls;
  if (
    typeof item.id !== 'string' || !LOCAL_AVATAR_TOOL_ID_PATTERN.test(item.id)
    || typeof item.name !== 'string' || !item.name.trim()
    || (item.changeMode !== 'press-swap' && item.changeMode !== 'click-advance')
    || typeof item.defaultUrl !== 'string'
    || !Array.isArray(changeUrls)
    || changeUrls.length < 1
    || changeUrls.length > 16
    || changeUrls.some(url => typeof url !== 'string' || !url)
    || (item.changeMode === 'press-swap' && changeUrls.length !== 1)
    || (item.normalSoundUrl !== undefined && (typeof item.normalSoundUrl !== 'string' || !item.normalSoundUrl))
  ) return null;
  return {
    id: item.id as LocalAvatarToolId,
    name: item.name,
    changeMode: item.changeMode,
    defaultUrl: item.defaultUrl,
    changeUrls: [...changeUrls] as string[],
    ...(typeof item.normalSoundUrl === 'string' ? { normalSoundUrl: item.normalSoundUrl } : {}),
  };
}

function assertListResponse(value: unknown): LocalAvatarToolList {
  if (!value || typeof value !== 'object') throw new Error('avatar_tool_list_invalid');
  const payload = value as Record<string, unknown>;
  if (payload.ok !== true || !Array.isArray(payload.items) || !payload.limits || typeof payload.limits !== 'object') {
    throw new Error('avatar_tool_list_invalid');
  }
  const items = payload.items.flatMap((candidate): LocalAvatarToolDto[] => {
    const item = decodeLocalAvatarToolItem(candidate);
    return item ? [item] : [];
  });
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
  return { items, limits };
}

export async function fetchLocalAvatarTools(): Promise<LocalAvatarToolList> {
  const response = await fetch('/api/avatar-tools', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('avatar_tool_list_failed');
  return assertListResponse(await response.json());
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
  form.set('name', input.name);
  form.set('change_mode', input.changeMode);
  form.set('default_image', input.defaultImage);
  input.changeItems.forEach((item) => {
    form.append('change_images', item.image);
    form.append('change_meanings', item.meaning);
  });
  if (input.normalSound) form.set('normal_sound', input.normalSound);
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
      return payload.ok === true ? decodeLocalAvatarToolItem(payload.item) : null;
    } catch {
      return null;
    }
  }
  let errorCode = '';
  try { errorCode = String((await response.json())?.error_code ?? ''); } catch {}
  if (!retry && response.status === 403 && errorCode === 'csrf_validation_failed' && security?.refreshToken) {
    await security.refreshToken();
    return postLocalAvatarTool(input, true);
  }
  throw new Error(errorCode || 'avatar_tool_create_failed');
}

export async function createLocalAvatarTool(input: CreateLocalAvatarToolInput): Promise<LocalAvatarToolDto | null> {
  return postLocalAvatarTool(input, false);
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
    sounds: normalSound ? [normalSound] : [],
    effects: [],
    interaction: {
      kind: 'press-release',
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
    },
  };
}
