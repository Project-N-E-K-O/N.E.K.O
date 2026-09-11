import type { LocalAvatarToolDetail, LocalAvatarToolImageInteractions } from './localTools';
import type { AvatarToolImageId } from './avatarToolEditorModel';
import {
  findDuplicateAvatarToolNameIds,
  getAvatarToolNameValidationError,
  normalizeAvatarToolName,
} from './avatarToolNames';

export type AvatarToolInteractionId = `ix-${string}`;
export type AvatarToolInteractionLinkId = `link-${string}`;
export type AvatarToolConnectionSide = 'top' | 'right' | 'bottom' | 'left';

export type AvatarToolConnectionSides = {
  sourceSide: AvatarToolConnectionSide;
  targetSide: AvatarToolConnectionSide;
};

export type AvatarToolImageAction =
  | { kind: 'keep' }
  | { kind: 'show'; imageId: AvatarToolImageId };

type AvatarToolInteractionBase = {
  id: AvatarToolInteractionId;
  name?: string;
  position: { x: number; y: number };
};

export type AvatarToolClickInteractionDraft = AvatarToolInteractionBase & {
  kind: 'mouse-click';
  press: AvatarToolImageAction;
  release: AvatarToolImageAction;
};

export type AvatarToolDelayInteractionDraft = AvatarToolInteractionBase & {
  kind: 'after';
  delayMs: string;
  complete: AvatarToolImageAction | null;
};

export type AvatarToolInteractionDraft =
  | AvatarToolClickInteractionDraft
  | AvatarToolDelayInteractionDraft;

export type AvatarToolInteractionLinkDraft = {
  id: AvatarToolInteractionLinkId;
  from: AvatarToolInteractionId;
  to: AvatarToolInteractionId;
  sourceSide: AvatarToolConnectionSide;
  targetSide: AvatarToolConnectionSide;
};

export type AvatarToolInteractionEditorState = {
  items: AvatarToolInteractionDraft[];
  links: AvatarToolInteractionLinkDraft[];
  initialImageTargetIds: AvatarToolInteractionId[];
  initialImageLinkSides: Partial<Record<AvatarToolInteractionId, AvatarToolConnectionSides>>;
  initialImagePosition: { x: number; y: number };
  selectedInteractionId: AvatarToolInteractionId | null;
  selectedLinkId: AvatarToolInteractionLinkId | null;
  selectedInitialLinkTargetId: AvatarToolInteractionId | null;
};

export type AvatarToolInteractionValidationCode =
  | 'initial-connection-required'
  | 'duplicate-name'
  | 'name-too-long'
  | 'name-invalid'
  | 'action-image-missing'
  | 'delay-invalid'
  | 'delay-image-missing'
  | 'link-endpoint-missing'
  | 'duplicate-link'
  | 'unreachable'
  | 'ambiguous-click'
  | 'ambiguous-delay';

export type AvatarToolInteractionValidationIssue = {
  key: string;
  code: AvatarToolInteractionValidationCode;
  interactionId?: AvatarToolInteractionId;
  linkId?: AvatarToolInteractionLinkId;
  field?: 'name' | 'press' | 'release' | 'delayMs' | 'complete' | 'initialConnection' | 'connection';
  waitingAfterId?: AvatarToolInteractionId;
  delayMs?: number;
  maxNameChars?: number;
};

export type AvatarToolInteractionEditorAction =
  | { type: 'reset'; state: AvatarToolInteractionEditorState }
  | { type: 'add'; interaction: AvatarToolInteractionDraft }
  | { type: 'select-interaction'; interactionId: AvatarToolInteractionId | null }
  | { type: 'select-link'; linkId: AvatarToolInteractionLinkId | null }
  | { type: 'select-initial-link'; interactionId: AvatarToolInteractionId | null }
  | { type: 'move'; interactionId: AvatarToolInteractionId; position: { x: number; y: number } }
  | { type: 'move-initial-image'; position: { x: number; y: number } }
  | { type: 'update-name'; interactionId: AvatarToolInteractionId; name: string }
  | { type: 'update-click-action'; interactionId: AvatarToolInteractionId; timing: 'press' | 'release'; action: AvatarToolImageAction }
  | { type: 'update-delay'; interactionId: AvatarToolInteractionId; delayMs: string }
  | { type: 'update-delay-action'; interactionId: AvatarToolInteractionId; action: AvatarToolImageAction | null }
  | {
    type: 'connect-initial-image';
    interactionId: AvatarToolInteractionId;
    sourceSide: AvatarToolConnectionSide;
    targetSide: AvatarToolConnectionSide;
  }
  | { type: 'remove-initial-link'; interactionId: AvatarToolInteractionId }
  | { type: 'connect'; link: AvatarToolInteractionLinkDraft }
  | { type: 'remove-link'; linkId: AvatarToolInteractionLinkId }
  | { type: 'remove-interaction'; interactionId: AvatarToolInteractionId }
  | { type: 'duplicate-interaction'; sourceId: AvatarToolInteractionId; duplicate: AvatarToolInteractionDraft };

const AVATAR_TOOL_NODE_HORIZONTAL_GAP = 300;
const AVATAR_TOOL_NODE_VERTICAL_GAP = 170;
const AVATAR_TOOL_CONNECTION_SIDES: readonly AvatarToolConnectionSide[] = [
  'top',
  'right',
  'bottom',
  'left',
];

function isAvatarToolConnectionSide(value: unknown): value is AvatarToolConnectionSide {
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
      delayMs: '800',
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

export function createAvatarToolInteractionEditorState(
  detail?: LocalAvatarToolDetail,
): AvatarToolInteractionEditorState {
  if (!detail) {
    return {
      items: [],
      links: [],
      initialImageTargetIds: [],
      initialImageLinkSides: {},
      initialImagePosition: { x: 80, y: 180 },
      selectedInteractionId: null,
      selectedLinkId: null,
      selectedInitialLinkTargetId: null,
    };
  }

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

  if (detail.changeMode === 'press-swap') {
    const id: AvatarToolInteractionId = 'ix-v2-press-swap';
    const item: AvatarToolClickInteractionDraft = {
      id,
      name: '',
      kind: 'mouse-click',
      position: { x: 220, y: 180 },
      press: changeImageIds[0] ? { kind: 'show', imageId: changeImageIds[0] } : { kind: 'keep' },
      release: { kind: 'show', imageId: defaultImageId },
    };
    return {
      items: [item],
      links: [{
        id: 'link-v2-press-swap-loop',
        from: id,
        to: id,
        sourceSide: 'right',
        targetSide: 'right',
      }],
      initialImageTargetIds: [id],
      initialImageLinkSides: { [id]: { sourceSide: 'right', targetSide: 'left' } },
      initialImagePosition: { x: -100, y: 180 },
      selectedInteractionId: null,
      selectedLinkId: null,
      selectedInitialLinkTargetId: null,
    };
  }

  const items: AvatarToolClickInteractionDraft[] = changeImageIds.map((imageId, index) => ({
    id: `ix-v2-click-advance-${String(index).padStart(3, '0')}` as AvatarToolInteractionId,
    name: '',
    kind: 'mouse-click',
    position: { x: 120 + index * 270, y: 180 },
    press: { kind: 'keep' },
    release: { kind: 'show', imageId },
  }));
  const links: AvatarToolInteractionLinkDraft[] = items.map((item, index) => ({
    id: `link-v2-click-advance-${String(index).padStart(3, '0')}` as AvatarToolInteractionLinkId,
    from: item.id,
    to: items[index + 1]?.id ?? item.id,
    sourceSide: 'right',
    targetSide: items[index + 1] ? 'left' : 'right',
  }));
  return {
    items,
    links,
    initialImageTargetIds: items[0] ? [items[0].id] : [],
    initialImageLinkSides: items[0]
      ? { [items[0].id]: { sourceSide: 'right', targetSide: 'left' } }
      : {},
    initialImagePosition: { x: -160, y: 180 },
    selectedInteractionId: null,
    selectedLinkId: null,
    selectedInitialLinkTargetId: null,
  };
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

function parseDelayMs(value: string): number | null {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) return null;
  const delayMs = Number(normalized);
  return Number.isSafeInteger(delayMs) && delayMs > 0 ? delayMs : null;
}

export function buildLocalAvatarToolImageInteractions(
  state: AvatarToolInteractionEditorState,
): LocalAvatarToolImageInteractions | null {
  const positions = new Map(state.items.map(item => [item.id, item.position]));
  const initialLinks: LocalAvatarToolImageInteractions['initialLinks'] = [];
  for (const to of state.initialImageTargetIds) {
    const targetPosition = positions.get(to);
    const sides = state.initialImageLinkSides[to];
    if (
      !targetPosition
      || !sides
      || !isAvatarToolConnectionSide(sides.sourceSide)
      || !isAvatarToolConnectionSide(sides.targetSide)
    ) return null;
    initialLinks.push({ to, ...sides });
  }
  const links: LocalAvatarToolImageInteractions['links'] = [];
  for (const link of state.links) {
    const sourcePosition = positions.get(link.from);
    const targetPosition = positions.get(link.to);
    if (
      !sourcePosition
      || !targetPosition
      || !isAvatarToolConnectionSide(link.sourceSide)
      || !isAvatarToolConnectionSide(link.targetSide)
    ) return null;
    links.push({
      from: link.from,
      to: link.to,
      sourceSide: link.sourceSide,
      targetSide: link.targetSide,
    });
  }
  const items: LocalAvatarToolImageInteractions['items'] = [];
  for (const item of state.items) {
    if (item.kind === 'mouse-click') {
      items.push({
        id: item.id,
        name: normalizeAvatarToolName(item.name ?? ''),
        trigger: { kind: 'mouse-click' },
        actions: { press: item.press, release: item.release },
        editorPosition: item.position,
      });
      continue;
    }
    const delayMs = parseDelayMs(item.delayMs);
    if (delayMs === null || !item.complete) return null;
    items.push({
      id: item.id,
      name: normalizeAvatarToolName(item.name ?? ''),
      trigger: { kind: 'after', delayMs },
      actions: { complete: item.complete },
      editorPosition: item.position,
    });
  }
  return {
    initialImagePosition: state.initialImagePosition,
    initialLinks,
    items,
    links,
  };
}

export function validateAvatarToolInteractionGraph(
  state: AvatarToolInteractionEditorState,
  imageIds: readonly AvatarToolImageId[],
  getInteractionDisplayName: (item: AvatarToolInteractionDraft) => string = item => (
    item.name?.trim() || item.id
  ),
  maxDelayMs = Number.MAX_SAFE_INTEGER,
  maxNameChars = Number.MAX_SAFE_INTEGER,
): AvatarToolInteractionValidationIssue[] {
  const issues: AvatarToolInteractionValidationIssue[] = [];
  const interactionIds = new Set(state.items.map(item => item.id));
  const imageIdSet = new Set(imageIds);
  const validInitialImageTargetIds = state.initialImageTargetIds.filter(id => interactionIds.has(id));

  if (validInitialImageTargetIds.length === 0) {
    issues.push({
      key: 'interaction:initial-connection',
      code: 'initial-connection-required',
      field: 'initialConnection',
    });
  }

  const duplicateNameIds = findDuplicateAvatarToolNameIds(
    state.items,
    getInteractionDisplayName,
  );
  state.items.forEach((item) => {
    const nameError = getAvatarToolNameValidationError(item.name ?? '', maxNameChars);
    if (nameError === 'too-long' || nameError === 'invalid') {
      issues.push({
        key: `interaction:${item.id}:name`,
        code: nameError === 'too-long' ? 'name-too-long' : 'name-invalid',
        interactionId: item.id,
        field: 'name',
        maxNameChars,
      });
    } else if (duplicateNameIds.has(item.id)) {
      issues.push({
        key: `interaction:${item.id}:name`,
        code: 'duplicate-name',
        interactionId: item.id,
        field: 'name',
      });
    }
  });

  state.items.forEach((item) => {
    if (item.kind === 'mouse-click') {
      (['press', 'release'] as const).forEach((field) => {
        const action = item[field];
        if (action.kind === 'show' && !imageIdSet.has(action.imageId)) {
          issues.push({
            key: `interaction:${item.id}:${field}`,
            code: 'action-image-missing',
            interactionId: item.id,
            field,
          });
        }
      });
    } else {
      const delayMs = parseDelayMs(item.delayMs);
      if (delayMs === null || delayMs > maxDelayMs) {
        issues.push({
          key: `interaction:${item.id}:delayMs`,
          code: 'delay-invalid',
          interactionId: item.id,
          field: 'delayMs',
        });
      }
      if (!item.complete || (item.complete.kind === 'show' && !imageIdSet.has(item.complete.imageId))) {
        issues.push({
          key: `interaction:${item.id}:complete`,
          code: 'delay-image-missing',
          interactionId: item.id,
          field: 'complete',
        });
      }
    }
  });

  const seenConnections = new Map<string, AvatarToolInteractionLinkId>();
  state.links.forEach((link) => {
    if (!interactionIds.has(link.from) || !interactionIds.has(link.to)) {
      issues.push({
        key: `link:${link.id}:endpoint`,
        code: 'link-endpoint-missing',
        linkId: link.id,
        field: 'connection',
      });
      return;
    }
    const signature = `${link.from}\u0000${link.to}`;
    if (seenConnections.has(signature)) {
      issues.push({
        key: `link:${link.id}:duplicate`,
        code: 'duplicate-link',
        linkId: link.id,
        field: 'connection',
      });
    } else {
      seenConnections.set(signature, link.id);
    }
  });

  const reachable = new Set<AvatarToolInteractionId>();
  const queue = [...validInitialImageTargetIds];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (reachable.has(id)) continue;
    reachable.add(id);
    state.links.forEach((link) => {
      if (link.from === id && interactionIds.has(link.to) && !reachable.has(link.to)) queue.push(link.to);
    });
  }
  state.items.forEach((item) => {
    if (!reachable.has(item.id)) {
      issues.push({
        key: `interaction:${item.id}:unreachable`,
        code: 'unreachable',
        interactionId: item.id,
      });
    }
  });

  const waitingPositions: Array<{
    waitingAfterId?: AvatarToolInteractionId;
    candidates: AvatarToolInteractionDraft[];
  }> = [
    {
      candidates: validInitialImageTargetIds
        .map(id => state.items.find(item => item.id === id))
        .filter((item): item is AvatarToolInteractionDraft => !!item),
    },
    ...state.items.map(source => ({
      waitingAfterId: source.id,
      candidates: state.links
        .filter(link => link.from === source.id)
        .map(link => state.items.find(item => item.id === link.to))
        .filter((item): item is AvatarToolInteractionDraft => !!item),
    })),
  ];

  waitingPositions.forEach(({ waitingAfterId, candidates }) => {
    const uniqueCandidates = [...new Map(candidates.map(item => [item.id, item])).values()];
    const clicks = uniqueCandidates.filter(item => item.kind === 'mouse-click');
    if (clicks.length > 1) {
      clicks.forEach((item) => issues.push({
        key: `interaction:${item.id}:ambiguous-click:${waitingAfterId ?? 'initial-image'}`,
        code: 'ambiguous-click',
        interactionId: item.id,
        waitingAfterId,
      }));
    }

    const delaysByTime = new Map<number, AvatarToolDelayInteractionDraft[]>();
    uniqueCandidates.forEach((item) => {
      if (item.kind !== 'after') return;
      const delayMs = parseDelayMs(item.delayMs);
      if (delayMs === null) return;
      const group = delaysByTime.get(delayMs) ?? [];
      group.push(item);
      delaysByTime.set(delayMs, group);
    });
    delaysByTime.forEach((items, delayMs) => {
      if (items.length < 2) return;
      items.forEach((item) => issues.push({
          key: `interaction:${item.id}:ambiguous-delay:${waitingAfterId ?? 'initial-image'}:${delayMs}`,
        code: 'ambiguous-delay',
        interactionId: item.id,
        waitingAfterId,
        delayMs,
      }));
    });
  });

  return issues;
}
