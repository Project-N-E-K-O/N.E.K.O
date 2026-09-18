import type { LocalAvatarToolDetail } from './localTools';
import type { AvatarToolImageId } from './avatarToolEditorModel';
import {
  createAvatarToolInteractionPresetState,
  emptyAvatarToolInteractionEditorState,
  isAvatarToolConnectionSide,
} from './avatarToolInteractionDrafts';
import type {
  AvatarToolImageAction,
  AvatarToolInteractionEditorAction,
  AvatarToolInteractionEditorState,
  AvatarToolInteractionId,
  AvatarToolInteractionLinkId,
} from './avatarToolInteractionTypes';

export function createAvatarToolInteractionEditorState(
  detail?: LocalAvatarToolDetail,
): AvatarToolInteractionEditorState {
  if (!detail) return emptyAvatarToolInteractionEditorState();

  if (detail.recordVersion === 3) {
    const initialImageLinkSides: AvatarToolInteractionEditorState['initialImageLinkSides'] = {};
    detail.imageInteractions.initialLinks.forEach((link) => {
      initialImageLinkSides[link.to] = {
        sourceSide: link.sourceSide,
        targetSide: link.targetSide,
      };
    });
    return {
      items: detail.imageInteractions.items.map(item => item.trigger.kind === 'mouse-click'
        ? {
          id: item.id,
          name: item.name,
          kind: 'mouse-click' as const,
          position: item.editorPosition,
          press: (item.actions as { press: AvatarToolImageAction; release: AvatarToolImageAction }).press,
          release: (item.actions as { press: AvatarToolImageAction; release: AvatarToolImageAction }).release,
        }
        : {
          id: item.id,
          name: item.name,
          kind: 'after' as const,
          position: item.editorPosition,
          delayMs: String(item.trigger.delayMs),
          complete: (item.actions as { complete: AvatarToolImageAction }).complete,
        }),
      links: detail.imageInteractions.links.map((link, index) => ({
        id: `link-v3-${String(index).padStart(3, '0')}` as AvatarToolInteractionLinkId,
        from: link.from,
        to: link.to,
        sourceSide: link.sourceSide,
        targetSide: link.targetSide,
      })),
      initialImageTargetIds: detail.imageInteractions.initialLinks.map(link => link.to),
      initialImageLinkSides,
      initialImagePosition: detail.imageInteractions.initialImagePosition,
      selectedInteractionId: null,
      selectedLinkId: null,
      selectedInitialLinkTargetId: null,
    };
  }

  const defaultImageId: AvatarToolImageId = 'img-v2-default';
  const changeImageIds = detail.changeItems.map((_, index) => (
    `img-v2-change-${String(index).padStart(3, '0')}` as AvatarToolImageId
  ));

  const pressSwap = detail.changeMode === 'press-swap';
  return createAvatarToolInteractionPresetState({
    kind: detail.changeMode,
    initialImageId: defaultImageId,
    targetImageIds: changeImageIds,
    initialImagePosition: { x: pressSwap ? -100 : -160, y: 180 },
    identity: pressSwap
      ? {
        interactionIds: ['ix-v2-press-swap'],
        linkIds: ['link-v2-press-swap-loop'],
      }
      : {
        interactionIds: changeImageIds.map((_, index) => (
          `ix-v2-click-advance-${String(index).padStart(3, '0')}` as AvatarToolInteractionId
        )),
        linkIds: changeImageIds.slice(0, -1).map((_, index) => (
          `link-v2-click-advance-${String(index).padStart(3, '0')}` as AvatarToolInteractionLinkId
        )),
      },
  });
}

function hasInteraction(state: AvatarToolInteractionEditorState, interactionId: AvatarToolInteractionId): boolean {
  return state.items.some(item => item.id === interactionId);
}

export function avatarToolInteractionEditorReducer(
  state: AvatarToolInteractionEditorState,
  action: AvatarToolInteractionEditorAction,
): AvatarToolInteractionEditorState {
  switch (action.type) {
    case 'reset':
      return action.state;
    case 'add':
      if (hasInteraction(state, action.interaction.id)) return state;
      return {
        ...state,
        items: [...state.items, action.interaction],
        selectedInteractionId: action.interaction.id,
        selectedLinkId: null,
        selectedInitialLinkTargetId: null,
      };
    case 'select-interaction':
      return action.interactionId === null || hasInteraction(state, action.interactionId)
        ? {
          ...state,
          selectedInteractionId: action.interactionId,
          selectedLinkId: null,
          selectedInitialLinkTargetId: null,
        }
        : state;
    case 'select-link':
      return action.linkId === null || state.links.some(link => link.id === action.linkId)
        ? {
          ...state,
          selectedInteractionId: null,
          selectedLinkId: action.linkId,
          selectedInitialLinkTargetId: null,
        }
        : state;
    case 'select-initial-link':
      return action.interactionId === null || state.initialImageTargetIds.includes(action.interactionId)
        ? {
          ...state,
          selectedInteractionId: null,
          selectedLinkId: null,
          selectedInitialLinkTargetId: action.interactionId,
        }
        : state;
    case 'move':
      return hasInteraction(state, action.interactionId)
        ? {
          ...state,
          items: state.items.map(item => item.id === action.interactionId
            ? { ...item, position: action.position }
            : item),
        }
        : state;
    case 'move-initial-image':
      return { ...state, initialImagePosition: action.position };
    case 'update-name':
      return {
        ...state,
        items: state.items.map(item => item.id === action.interactionId
          ? { ...item, name: action.name }
          : item),
      };
    case 'update-click-action':
      return {
        ...state,
        items: state.items.map(item => item.id === action.interactionId && item.kind === 'mouse-click'
          ? { ...item, [action.timing]: action.action }
          : item),
      };
    case 'update-delay':
      return {
        ...state,
        items: state.items.map(item => item.id === action.interactionId && item.kind === 'after'
          ? { ...item, delayMs: action.delayMs }
          : item),
      };
    case 'update-delay-action':
      return {
        ...state,
        items: state.items.map(item => item.id === action.interactionId && item.kind === 'after'
          ? { ...item, complete: action.action }
          : item),
      };
    case 'connect-initial-image':
      if (
        !hasInteraction(state, action.interactionId)
        || state.initialImageTargetIds.includes(action.interactionId)
        || !isAvatarToolConnectionSide(action.sourceSide)
        || !isAvatarToolConnectionSide(action.targetSide)
      ) {
        return state;
      }
      return {
        ...state,
        initialImageTargetIds: [...state.initialImageTargetIds, action.interactionId],
        initialImageLinkSides: {
          ...state.initialImageLinkSides,
          [action.interactionId]: {
            sourceSide: action.sourceSide,
            targetSide: action.targetSide,
          }
        },
        selectedInteractionId: null,
        selectedLinkId: null,
        selectedInitialLinkTargetId: action.interactionId,
      };
    case 'remove-initial-link':
      if (!state.initialImageTargetIds.includes(action.interactionId)) return state;
      {
        const initialImageLinkSides = { ...state.initialImageLinkSides };
        delete initialImageLinkSides[action.interactionId];
        return {
          ...state,
          initialImageTargetIds: state.initialImageTargetIds.filter(id => id !== action.interactionId),
          initialImageLinkSides,
          selectedInitialLinkTargetId: state.selectedInitialLinkTargetId === action.interactionId
            ? null
            : state.selectedInitialLinkTargetId,
        };
      }
    case 'connect':
      if (
        !hasInteraction(state, action.link.from)
        || !hasInteraction(state, action.link.to)
        || !isAvatarToolConnectionSide(action.link.sourceSide)
        || !isAvatarToolConnectionSide(action.link.targetSide)
        || state.links.some(link => link.id === action.link.id)
        || state.links.some(link => link.from === action.link.from && link.to === action.link.to)
      ) return state;
      return {
        ...state,
        links: [...state.links, action.link],
        selectedLinkId: action.link.id,
        selectedInteractionId: null,
        selectedInitialLinkTargetId: null,
      };
    case 'remove-link':
      return {
        ...state,
        links: state.links.filter(link => link.id !== action.linkId),
        selectedLinkId: state.selectedLinkId === action.linkId ? null : state.selectedLinkId,
      };
    case 'remove-interaction': {
      const removedIndex = state.items.findIndex(item => item.id === action.interactionId);
      if (removedIndex < 0) return state;
      const items = state.items.filter(item => item.id !== action.interactionId);
      const initialImageLinkSides = { ...state.initialImageLinkSides };
      delete initialImageLinkSides[action.interactionId];
      return {
        ...state,
        items,
        links: state.links.filter(link => link.from !== action.interactionId && link.to !== action.interactionId),
        initialImageTargetIds: state.initialImageTargetIds.filter(id => id !== action.interactionId),
        initialImageLinkSides,
        selectedInteractionId: state.selectedInteractionId === action.interactionId
          ? items[Math.min(removedIndex, items.length - 1)]?.id ?? null
          : state.selectedInteractionId,
        selectedLinkId: state.links.some(link => (
          link.id === state.selectedLinkId
          && (link.from === action.interactionId || link.to === action.interactionId)
        )) ? null : state.selectedLinkId,
        selectedInitialLinkTargetId: state.selectedInitialLinkTargetId === action.interactionId
          ? null
          : state.selectedInitialLinkTargetId,
      };
    }
    case 'duplicate-interaction':
      if (!hasInteraction(state, action.sourceId) || hasInteraction(state, action.duplicate.id)) return state;
      return {
        ...state,
        items: [...state.items, action.duplicate],
        selectedInteractionId: action.duplicate.id,
        selectedLinkId: null,
        selectedInitialLinkTargetId: null,
      };
    default:
      return state;
  }
}

export function getAvatarToolInteractionOrdinal(
  state: AvatarToolInteractionEditorState,
  interactionId: AvatarToolInteractionId,
): number {
  const target = state.items.find(item => item.id === interactionId);
  if (!target) return 0;
  return state.items.filter(item => item.kind === target.kind)
    .findIndex(item => item.id === interactionId) + 1;
}

export function getAvatarToolInteractionImageReferences(
  state: AvatarToolInteractionEditorState,
): Partial<Record<AvatarToolImageId, Array<{
  interactionId: AvatarToolInteractionId;
  field: 'press' | 'release' | 'complete';
}>>> {
  const references: Partial<Record<AvatarToolImageId, Array<{
    interactionId: AvatarToolInteractionId;
    field: 'press' | 'release' | 'complete';
  }>>> = {};
  const add = (
    imageId: AvatarToolImageId,
    interactionId: AvatarToolInteractionId,
    field: 'press' | 'release' | 'complete',
  ) => {
    (references[imageId] ??= []).push({ interactionId, field });
  };
  state.items.forEach((item) => {
    if (item.kind === 'mouse-click') {
      if (item.press.kind === 'show') add(item.press.imageId, item.id, 'press');
      if (item.release.kind === 'show') add(item.release.imageId, item.id, 'release');
    } else if (item.complete?.kind === 'show') {
      add(item.complete.imageId, item.id, 'complete');
    }
  });
  return references;
}
