import type { LocalAvatarToolImageInteractions } from './localTools';
import type { AvatarToolImageId } from './avatarToolEditorModel';
import {
  findDuplicateAvatarToolNameIds,
  getAvatarToolNameValidationError,
  normalizeAvatarToolName,
} from './avatarToolNames';
import { isAvatarToolConnectionSide } from './avatarToolInteractionDrafts';
import type {
  AvatarToolDelayInteractionDraft,
  AvatarToolInteractionDraft,
  AvatarToolInteractionEditorState,
  AvatarToolInteractionId,
  AvatarToolInteractionLinkId,
  AvatarToolInteractionValidationIssue,
} from './avatarToolInteractionTypes';

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
