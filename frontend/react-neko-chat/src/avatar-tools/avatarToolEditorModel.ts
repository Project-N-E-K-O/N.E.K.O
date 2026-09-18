import type { LocalAvatarToolDetail } from './localTools';

export type AvatarToolImageId = `img-${string}`;

export type AvatarToolImageDraft = {
  id: AvatarToolImageId;
  name?: string;
  image: File | null;
  imageResource?: string;
  imageUrl?: string;
  meaning: string;
};

export type AvatarToolImageEditorState = {
  images: AvatarToolImageDraft[];
  initialImageId: AvatarToolImageId | null;
  selectedImageId: AvatarToolImageId | null;
};

export type AvatarToolImageRemovalBlock =
  | { kind: 'initial' }
  | { kind: 'referenced'; locations: readonly string[] };

export type AvatarToolImageEditorAction =
  | { type: 'add'; image: AvatarToolImageDraft; maximumImages: number }
  | { type: 'replace'; imageId: AvatarToolImageId; file: File }
  | { type: 'update-name'; imageId: AvatarToolImageId; name: string }
  | { type: 'update-meaning'; imageId: AvatarToolImageId; meaning: string }
  | { type: 'select'; imageId: AvatarToolImageId }
  | { type: 'choose-initial'; imageId: AvatarToolImageId }
  | { type: 'remove'; imageId: AvatarToolImageId };

export function createAvatarToolImageId(): AvatarToolImageId {
  return `img-${globalThis.crypto.randomUUID().toLowerCase()}` as AvatarToolImageId;
}

export function createAvatarToolImageDraft(file: File): AvatarToolImageDraft {
  return {
    id: createAvatarToolImageId(),
    name: '',
    image: file,
    meaning: '',
  };
}

export function createAvatarToolImageEditorState(
  detail?: LocalAvatarToolDetail,
): AvatarToolImageEditorState {
  if (detail?.recordVersion === 3) {
    const images = detail.images.map(image => ({
      id: image.id,
      name: image.name,
      image: null,
      imageResource: image.resource,
      imageUrl: image.url,
      meaning: image.meaning,
    }));
    return {
      images,
      initialImageId: detail.initialImageId,
      selectedImageId: detail.initialImageId,
    };
  }
  const images: AvatarToolImageDraft[] = detail ? [
    {
      id: 'img-v2-default',
      name: '',
      image: null,
      imageResource: detail.defaultImage.resource,
      imageUrl: detail.defaultImage.url,
      meaning: '',
    },
    ...detail.changeItems.map((item, index) => ({
      id: `img-v2-change-${String(index).padStart(3, '0')}` as AvatarToolImageId,
      name: '',
      image: null,
      imageResource: item.resource,
      imageUrl: item.url,
      meaning: item.meaning,
    })),
  ] : [];
  const firstImageId = images[0]?.id ?? null;
  return {
    images,
    initialImageId: firstImageId,
    selectedImageId: firstImageId,
  };
}

function hasImage(state: AvatarToolImageEditorState, imageId: AvatarToolImageId): boolean {
  return state.images.some(image => image.id === imageId);
}

export function avatarToolImageEditorReducer(
  state: AvatarToolImageEditorState,
  action: AvatarToolImageEditorAction,
): AvatarToolImageEditorState {
  switch (action.type) {
    case 'add': {
      if (
        state.images.length >= action.maximumImages
        || state.images.some(image => image.id === action.image.id)
      ) return state;
      return {
        images: [...state.images, action.image],
        initialImageId: state.initialImageId ?? action.image.id,
        selectedImageId: action.image.id,
      };
    }
    case 'replace': {
      if (!hasImage(state, action.imageId)) return state;
      return {
        ...state,
        images: state.images.map(image => image.id === action.imageId
          ? {
            ...image,
            image: action.file,
            imageResource: undefined,
            imageUrl: undefined,
          }
          : image),
        selectedImageId: action.imageId,
      };
    }
    case 'update-meaning': {
      if (!hasImage(state, action.imageId)) return state;
      return {
        ...state,
        images: state.images.map(image => image.id === action.imageId
          ? { ...image, meaning: action.meaning }
          : image),
      };
    }
    case 'update-name': {
      if (!hasImage(state, action.imageId)) return state;
      return {
        ...state,
        images: state.images.map(image => image.id === action.imageId
          ? { ...image, name: action.name }
          : image),
      };
    }
    case 'select':
      return hasImage(state, action.imageId)
        ? { ...state, selectedImageId: action.imageId }
        : state;
    case 'choose-initial':
      return hasImage(state, action.imageId)
        ? { ...state, initialImageId: action.imageId, selectedImageId: action.imageId }
        : state;
    case 'remove': {
      if (action.imageId === state.initialImageId) return state;
      const removedIndex = state.images.findIndex(image => image.id === action.imageId);
      if (removedIndex < 0) return state;
      const images = state.images.filter(image => image.id !== action.imageId);
      const selectedImageId = state.selectedImageId === action.imageId
        ? images[Math.min(removedIndex, images.length - 1)]?.id ?? null
        : state.selectedImageId;
      return { ...state, images, selectedImageId };
    }
    default:
      return state;
  }
}

export function getAvatarToolImageRemovalBlock(
  state: AvatarToolImageEditorState,
  imageId: AvatarToolImageId,
  references: Readonly<Partial<Record<AvatarToolImageId, readonly string[]>>>,
): AvatarToolImageRemovalBlock | null {
  if (imageId === state.initialImageId) return { kind: 'initial' };
  const locations = references[imageId] ?? [];
  return locations.length > 0 ? { kind: 'referenced', locations } : null;
}
