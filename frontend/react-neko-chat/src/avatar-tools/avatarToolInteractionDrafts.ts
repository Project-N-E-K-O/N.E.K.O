import type { AvatarToolImageId } from './avatarToolEditorModel';
import type {
  AvatarToolClickInteractionDraft,
  AvatarToolConnectionSide,
  AvatarToolConnectionSides,
  AvatarToolDelayInteractionDraft,
  AvatarToolInteractionDraft,
  AvatarToolInteractionEditorState,
  AvatarToolInteractionId,
  AvatarToolInteractionLinkDraft,
  AvatarToolInteractionLinkId,
  AvatarToolInteractionPresetKind,
  AvatarToolInteractionPresetRequirements,
} from './avatarToolInteractionTypes';

const AVATAR_TOOL_NODE_HORIZONTAL_GAP = 300;
const AVATAR_TOOL_NODE_VERTICAL_GAP = 170;
const AVATAR_TOOL_DEFAULT_DELAY_MS = '800';
const AVATAR_TOOL_INTERACTION_PRESET_REQUIREMENTS: Record<
  AvatarToolInteractionPresetKind,
  AvatarToolInteractionPresetRequirements
> = {
  'press-swap': { interactionCount: 1, totalLinkCount: 2 },
  'click-advance': { interactionCount: 3, totalLinkCount: 3 },
  'cycle-stop': { interactionCount: 5, totalLinkCount: 9 },
};
const AVATAR_TOOL_CONNECTION_SIDES: readonly AvatarToolConnectionSide[] = [
  'top',
  'right',
  'bottom',
  'left',
];

type AvatarToolInteractionPresetIdentity = {
  interactionIds?: readonly AvatarToolInteractionId[];
  linkIds?: readonly AvatarToolInteractionLinkId[];
};

export function isAvatarToolConnectionSide(value: unknown): value is AvatarToolConnectionSide {
  return AVATAR_TOOL_CONNECTION_SIDES.includes(value as AvatarToolConnectionSide);
}

export function avatarToolConnectionSideFromHandleId(
  handleId: string | null | undefined,
): AvatarToolConnectionSide | undefined {
  if (!handleId?.startsWith('edge-')) return undefined;
  const side = handleId.slice('edge-'.length) as AvatarToolConnectionSide;
  return isAvatarToolConnectionSide(side) ? side : undefined;
}

export function findAvailableAvatarToolInteractionPosition(
  preferred: { x: number; y: number },
  occupied: ReadonlyArray<{ position: { x: number; y: number } }>,
): { x: number; y: number } {
  const candidates: Array<{ x: number; y: number }> = [];
  const rows = Math.max(2, Math.ceil((occupied.length + 1) / 3) + 1);

  for (let row = 0; row < rows; row += 1) {
    const columns = row === 0 ? [0, 1] : [0, 1, -1];
    columns.forEach((column) => candidates.push({
      x: preferred.x + column * AVATAR_TOOL_NODE_HORIZONTAL_GAP,
      y: preferred.y + row * AVATAR_TOOL_NODE_VERTICAL_GAP,
    }));
  }

  return candidates.find(candidate => occupied.every(item => (
    Math.abs(candidate.x - item.position.x) >= 260
    || Math.abs(candidate.y - item.position.y) >= 130
  ))) ?? {
    x: preferred.x,
    y: preferred.y + rows * AVATAR_TOOL_NODE_VERTICAL_GAP,
  };
}

export function createAvatarToolInteractionId(): AvatarToolInteractionId {
  return `ix-${globalThis.crypto.randomUUID().toLowerCase()}` as AvatarToolInteractionId;
}

export function createAvatarToolInteractionLinkId(): AvatarToolInteractionLinkId {
  return `link-${globalThis.crypto.randomUUID().toLowerCase()}` as AvatarToolInteractionLinkId;
}

export function getAvatarToolInteractionPresetRequirements(
  kind: AvatarToolInteractionPresetKind,
): AvatarToolInteractionPresetRequirements {
  return AVATAR_TOOL_INTERACTION_PRESET_REQUIREMENTS[kind];
}

export function createAvatarToolInteractionDraft(
  kind: AvatarToolInteractionDraft['kind'],
  position: { x: number; y: number },
): AvatarToolInteractionDraft {
  const id = createAvatarToolInteractionId();
  return kind === 'mouse-click'
    ? {
      id,
      name: '',
      kind,
      position,
      press: { kind: 'keep' },
      release: { kind: 'keep' },
    }
    : {
      id,
      name: '',
      kind,
      position,
      delayMs: AVATAR_TOOL_DEFAULT_DELAY_MS,
      complete: null,
    };
}

export function duplicateAvatarToolInteractionDraft(
  source: AvatarToolInteractionDraft,
  occupied: ReadonlyArray<{ position: { x: number; y: number } }> = [source],
): AvatarToolInteractionDraft {
  return {
    ...source,
    id: createAvatarToolInteractionId(),
    name: '',
    position: findAvailableAvatarToolInteractionPosition(
      { x: source.position.x + 40, y: source.position.y + 140 },
      occupied,
    ),
  };
}

export function emptyAvatarToolInteractionEditorState(
  initialImagePosition: { x: number; y: number } = { x: 80, y: 180 },
): AvatarToolInteractionEditorState {
  return {
    items: [],
    links: [],
    initialImageTargetIds: [],
    initialImageLinkSides: {},
    initialImagePosition,
    selectedInteractionId: null,
    selectedLinkId: null,
    selectedInitialLinkTargetId: null,
  };
}

type AvatarToolInteractionPresetIdFactory = {
  interactionIdAt(index: number): AvatarToolInteractionId;
  linkIdAt(index: number): AvatarToolInteractionLinkId;
};

type AvatarToolInteractionPresetInitialTarget = {
  id: AvatarToolInteractionId;
  sides: AvatarToolConnectionSides;
};

function completeAvatarToolInteractionPresetState({
  initialImagePosition,
  items,
  links,
  initialTargets,
}: {
  initialImagePosition: { x: number; y: number };
  items: AvatarToolInteractionDraft[];
  links: AvatarToolInteractionLinkDraft[];
  initialTargets: AvatarToolInteractionPresetInitialTarget[];
}): AvatarToolInteractionEditorState {
  const initialImageLinkSides: AvatarToolInteractionEditorState['initialImageLinkSides'] = {};
  initialTargets.forEach(({ id, sides }) => {
    initialImageLinkSides[id] = sides;
  });
  return {
    items,
    links,
    initialImageTargetIds: initialTargets.map(target => target.id),
    initialImageLinkSides,
    initialImagePosition,
    selectedInteractionId: null,
    selectedLinkId: null,
    selectedInitialLinkTargetId: null,
  };
}

function createPressSwapPresetState({
  initialImageId,
  targetImageId,
  initialImagePosition,
  ids,
}: {
  initialImageId: AvatarToolImageId | null;
  targetImageId: AvatarToolImageId | null;
  initialImagePosition: { x: number; y: number };
  ids: AvatarToolInteractionPresetIdFactory;
}): AvatarToolInteractionEditorState {
  const id = ids.interactionIdAt(0);
  return completeAvatarToolInteractionPresetState({
    initialImagePosition,
    items: [{
      id,
      name: '',
      kind: 'mouse-click',
      position: { x: initialImagePosition.x + 320, y: initialImagePosition.y },
      press: targetImageId ? { kind: 'show', imageId: targetImageId } : { kind: 'keep' },
      release: initialImageId ? { kind: 'show', imageId: initialImageId } : { kind: 'keep' },
    }],
    links: [{
      id: ids.linkIdAt(0),
      from: id,
      to: id,
      sourceSide: 'right',
      targetSide: 'right',
    }],
    initialTargets: [{ id, sides: { sourceSide: 'right', targetSide: 'left' } }],
  });
}

function createClickAdvancePresetState({
  targetImageIds,
  initialImagePosition,
  ids,
}: {
  targetImageIds: readonly AvatarToolImageId[];
  initialImagePosition: { x: number; y: number };
  ids: AvatarToolInteractionPresetIdFactory;
}): AvatarToolInteractionEditorState {
  const clickTargets: Array<AvatarToolImageId | null> = targetImageIds.length > 0
    ? [...targetImageIds]
    : [null, null, null];
  const items: AvatarToolClickInteractionDraft[] = clickTargets.map((imageId, index) => ({
    id: ids.interactionIdAt(index),
    name: '',
    kind: 'mouse-click',
    position: {
      x: initialImagePosition.x + 280 + index * 270,
      y: initialImagePosition.y,
    },
    press: { kind: 'keep' },
    release: imageId ? { kind: 'show', imageId } : { kind: 'keep' },
  }));
  const links: AvatarToolInteractionLinkDraft[] = items.slice(0, -1).map((item, index) => ({
    id: ids.linkIdAt(index),
    from: item.id,
    to: items[index + 1].id,
    sourceSide: 'right',
    targetSide: 'left',
  }));
  return completeAvatarToolInteractionPresetState({
    initialImagePosition,
    items,
    links,
    initialTargets: [{ id: items[0].id, sides: { sourceSide: 'right', targetSide: 'left' } }],
  });
}

function createCycleStopPresetState({
  initialImagePosition,
  ids,
}: {
  initialImagePosition: { x: number; y: number };
  ids: AvatarToolInteractionPresetIdFactory;
}): AvatarToolInteractionEditorState {
  const cycleItems: AvatarToolDelayInteractionDraft[] = [
    { x: initialImagePosition.x + 300, y: initialImagePosition.y },
    { x: initialImagePosition.x + 600, y: initialImagePosition.y },
    { x: initialImagePosition.x + 900, y: initialImagePosition.y },
  ].map((position, index) => ({
    id: ids.interactionIdAt(index),
    name: '',
    kind: 'after',
    position,
    delayMs: AVATAR_TOOL_DEFAULT_DELAY_MS,
    complete: { kind: 'keep' },
  }));
  const holdClick: AvatarToolClickInteractionDraft = {
    id: ids.interactionIdAt(cycleItems.length),
    name: '',
    kind: 'mouse-click',
    position: { x: initialImagePosition.x + 600, y: initialImagePosition.y + 220 },
    press: { kind: 'keep' },
    release: { kind: 'keep' },
  };
  const resumeDelay: AvatarToolDelayInteractionDraft = {
    id: ids.interactionIdAt(cycleItems.length + 1),
    name: '',
    kind: 'after',
    position: { x: initialImagePosition.x + 600, y: initialImagePosition.y + 440 },
    delayMs: AVATAR_TOOL_DEFAULT_DELAY_MS,
    complete: { kind: 'keep' },
  };
  const cycleLinkSides: readonly AvatarToolConnectionSides[] = [
    { sourceSide: 'right', targetSide: 'left' },
    { sourceSide: 'right', targetSide: 'left' },
    { sourceSide: 'top', targetSide: 'top' },
  ];
  const holdLinkSides: readonly AvatarToolConnectionSides[] = [
    { sourceSide: 'bottom', targetSide: 'left' },
    { sourceSide: 'bottom', targetSide: 'top' },
    { sourceSide: 'bottom', targetSide: 'right' },
  ];
  const links = cycleItems.flatMap((item, index): AvatarToolInteractionLinkDraft[] => {
    const nextCycleItem = cycleItems[(index + 1) % cycleItems.length];
    return [
      {
        id: ids.linkIdAt(index * 2),
        from: item.id,
        to: nextCycleItem.id,
        ...cycleLinkSides[index],
      },
      {
        id: ids.linkIdAt(index * 2 + 1),
        from: item.id,
        to: holdClick.id,
        ...holdLinkSides[index],
      },
    ];
  });
  links.push({
    id: ids.linkIdAt(cycleItems.length * 2),
    from: holdClick.id,
    to: resumeDelay.id,
    sourceSide: 'bottom',
    targetSide: 'top',
  });
  links.push({
    id: ids.linkIdAt(cycleItems.length * 2 + 1),
    from: resumeDelay.id,
    to: cycleItems[0].id,
    sourceSide: 'left',
    targetSide: 'bottom',
  });
  return completeAvatarToolInteractionPresetState({
    initialImagePosition,
    items: [...cycleItems, holdClick, resumeDelay],
    links,
    initialTargets: [
      { id: cycleItems[0].id, sides: { sourceSide: 'right', targetSide: 'left' } },
    ],
  });
}

export function createAvatarToolInteractionPresetState({
  kind,
  initialImageId = null,
  targetImageIds = [],
  initialImagePosition = { x: 80, y: 180 },
  identity,
}: {
  kind: AvatarToolInteractionPresetKind;
  initialImageId?: AvatarToolImageId | null;
  targetImageIds?: readonly AvatarToolImageId[];
  initialImagePosition?: { x: number; y: number };
  identity?: AvatarToolInteractionPresetIdentity;
}): AvatarToolInteractionEditorState {
  const ids: AvatarToolInteractionPresetIdFactory = {
    interactionIdAt: index => (
      identity?.interactionIds?.[index] ?? createAvatarToolInteractionId()
    ),
    linkIdAt: index => (
      identity?.linkIds?.[index] ?? createAvatarToolInteractionLinkId()
    ),
  };
  if (kind === 'press-swap') {
    return createPressSwapPresetState({
      initialImageId,
      targetImageId: targetImageIds[0] ?? null,
      initialImagePosition,
      ids,
    });
  }
  if (kind === 'cycle-stop') return createCycleStopPresetState({ initialImagePosition, ids });
  return createClickAdvancePresetState({ targetImageIds, initialImagePosition, ids });
}
