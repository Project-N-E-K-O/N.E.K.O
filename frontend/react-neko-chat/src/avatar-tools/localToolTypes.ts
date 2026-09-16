import { LOCAL_AVATAR_TOOL_ID_PATTERN, type LocalAvatarToolId } from './catalog';

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
  recordVersion: 2;
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
  recordVersion: 2;
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
