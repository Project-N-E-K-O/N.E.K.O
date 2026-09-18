import { useCallback, useEffect, useState } from 'react';
import type { AvatarToolEditorResultMessage } from '../AvatarToolItemManager';
import {
  DEFAULT_ACTIVE_AVATAR_TOOL_IDS,
  forgetPersistedAvatarToolId,
  persistActiveAvatarToolIds,
  readPersistedActiveAvatarToolIds,
  sanitizeAvatarToolSlots,
  type AvatarToolId,
} from '../avatarTools';
import { useAvatarToolSlotReconciliation } from './useAvatarToolSlotReconciliation';
import type { LocalAvatarToolCatalog } from './useLocalAvatarToolCatalog';

type AvatarToolSurfaceSlotsOptions = {
  catalog: LocalAvatarToolCatalog;
  activeToolId: AvatarToolId | null;
  clearActiveTool(): void;
  managerOpen: boolean;
};

/** Each surface owns one instance; only the slot lifecycle rules are shared. */
export function useAvatarToolSurfaceSlots({
  catalog,
  activeToolId,
  clearActiveTool,
  managerOpen,
}: AvatarToolSurfaceSlotsOptions) {
  const [activeToolIds, setActiveToolIds] = useState<AvatarToolId[]>(readPersistedActiveAvatarToolIds);

  const saveSlots = useCallback((toolIds: AvatarToolId[]) => {
    const nextToolIds = sanitizeAvatarToolSlots(toolIds);
    setActiveToolIds(nextToolIds);
    persistActiveAvatarToolIds(nextToolIds);
    if (activeToolId && !nextToolIds.includes(activeToolId)) clearActiveTool();
  }, [activeToolId, clearActiveTool]);

  const restoreDefaultsInMemory = useCallback(() => {
    setActiveToolIds([...DEFAULT_ACTIVE_AVATAR_TOOL_IDS]);
  }, []);

  const forgetTool = useCallback((toolId: AvatarToolId) => {
    setActiveToolIds(current => current.filter(candidate => candidate !== toolId));
    forgetPersistedAvatarToolId(toolId);
    if (activeToolId === toolId) clearActiveTool();
  }, [activeToolId, clearActiveTool]);

  const applyEditorResult = useCallback((result: AvatarToolEditorResultMessage) => {
    if (result.action === 'deleted' && result.toolId) forgetTool(result.toolId as AvatarToolId);
  }, [forgetTool]);

  const deleteLocalTool = useCallback(async (toolId: `local-${string}`) => {
    await catalog.remove(toolId);
    forgetTool(toolId);
  }, [catalog.remove, forgetTool]);

  const handleConfirmedDeleted = useCallback((toolIds: ReadonlyArray<`local-${string}`>) => {
    const deletedIds = new Set<AvatarToolId>(toolIds);
    setActiveToolIds(current => current.filter(toolId => !deletedIds.has(toolId)));
    toolIds.forEach(forgetPersistedAvatarToolId);
  }, []);

  useAvatarToolSlotReconciliation({
    activeToolIds,
    authoritativeItems: catalog.items,
    authoritativeLoaded: catalog.authoritativeLoaded,
    onConfirmedDeleted: handleConfirmedDeleted,
  });

  useEffect(() => {
    if (!managerOpen) return;
    catalog.refresh().catch(() => undefined);
  }, [managerOpen, catalog.refresh]);

  useEffect(() => {
    if (activeToolId && !activeToolIds.includes(activeToolId)) clearActiveTool();
  }, [activeToolIds, activeToolId, clearActiveTool]);

  return { activeToolIds, saveSlots, restoreDefaultsInMemory, applyEditorResult, deleteLocalTool };
}
