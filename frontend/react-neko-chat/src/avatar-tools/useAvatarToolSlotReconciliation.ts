import { useEffect } from 'react';
import {
  isLocalAvatarToolId,
  type AvatarToolId,
  type AvatarToolItem,
} from '../avatarTools';
import {
  fetchLocalAvatarToolDetail,
  LocalAvatarToolDetailError,
} from './localTools';

type AvatarToolSlotReconciliationOptions = {
  activeToolIds: ReadonlyArray<AvatarToolId>;
  authoritativeItems: ReadonlyArray<AvatarToolItem>;
  authoritativeLoaded: boolean;
  onConfirmedDeleted(toolIds: ReadonlyArray<`local-${string}`>): void;
};

async function confirmLocalAvatarToolDeleted(toolId: `local-${string}`): Promise<boolean> {
  try {
    await fetchLocalAvatarToolDetail(toolId);
    return false;
  } catch (error) {
    return error instanceof LocalAvatarToolDetailError
      && error.message === 'tool_not_found';
  }
}

/**
 * Reconciles only deletions that the current surface can prove independently.
 * A list omission alone is not enough because invalid records are quarantined
 * out of the public list while their persisted slot intent must be retained.
 */
export function useAvatarToolSlotReconciliation({
  activeToolIds,
  authoritativeItems,
  authoritativeLoaded,
  onConfirmedDeleted,
}: AvatarToolSlotReconciliationOptions) {
  useEffect(() => {
    if (!authoritativeLoaded) return;
    const availableIds = new Set(authoritativeItems.map(item => item.id));
    const missingLocalIds = activeToolIds.filter((toolId): toolId is `local-${string}` => (
      isLocalAvatarToolId(toolId) && !availableIds.has(toolId)
    ));
    if (missingLocalIds.length === 0) return;

    let cancelled = false;
    void Promise.all(missingLocalIds.map(async toolId => (
      await confirmLocalAvatarToolDeleted(toolId) ? toolId : null
    ))).then((confirmedIds) => {
      if (cancelled) return;
      const deletedIds = confirmedIds.filter((toolId): toolId is `local-${string}` => toolId !== null);
      if (deletedIds.length > 0) onConfirmedDeleted(deletedIds);
    });

    return () => {
      cancelled = true;
    };
  }, [activeToolIds, authoritativeItems, authoritativeLoaded, onConfirmedDeleted]);
}
