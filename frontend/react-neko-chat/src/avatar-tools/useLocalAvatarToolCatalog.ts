import { useCallback, useEffect, useRef, useState } from 'react';
import {
  buildLocalAvatarToolDefinition,
  createLocalAvatarTool,
  deleteLocalAvatarTool,
  fetchLocalAvatarToolDetail,
  fetchLocalAvatarTools,
  LocalAvatarToolCreateError,
  LocalAvatarToolDetailError,
  LocalAvatarToolRevisionConflictError,
  updateLocalAvatarTool,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolDetail,
  type LocalAvatarToolDto,
  type LocalAvatarToolLimits,
  type UpdateLocalAvatarToolInput,
} from './localTools';
import {
  validateAvatarToolDefinition,
  type AvatarToolDefinition,
  type LocalAvatarToolId,
} from './catalog';
import {
  BUILT_IN_AVATAR_TOOL_REGISTRY,
  createAvatarToolRegistrySnapshot,
  type AvatarToolRegistrySnapshot,
} from './registry';

export type LocalAvatarToolCatalog = {
  registry: AvatarToolRegistrySnapshot;
  limits: LocalAvatarToolLimits | null;
  authoritativeLoaded: boolean;
  refreshFailed: boolean;
  refresh(): Promise<void>;
  create(input: CreateLocalAvatarToolInput): Promise<void>;
  detail(toolId: LocalAvatarToolId): Promise<LocalAvatarToolDetail>;
  update(toolId: LocalAvatarToolId, input: UpdateLocalAvatarToolInput): Promise<void>;
  remove(toolId: LocalAvatarToolId): Promise<void>;
};

function buildValidLocalDefinitions(items: ReadonlyArray<LocalAvatarToolDto>): AvatarToolDefinition[] {
  return items.flatMap((item) => {
    try {
      const definition = buildLocalAvatarToolDefinition(item);
      validateAvatarToolDefinition(definition);
      return [definition];
    } catch {
      return [];
    }
  });
}

function detailMatchesUpdate(detail: LocalAvatarToolDetail, input: UpdateLocalAvatarToolInput): boolean {
  if (
    input.defaultImage.file
    || input.changeItems.some(item => item.file)
    || input.normalSound?.file
    || input.special?.image.file
    || input.special?.sound?.file
  ) return false;
  if (
    detail.name !== input.name
    || detail.changeMode !== input.changeMode
    || detail.defaultImage.resource !== input.defaultImage.resource
    || !input.defaultImage.url
    || detail.defaultImage.url !== input.defaultImage.url
    || detail.changeItems.length !== input.changeItems.length
    || detail.changeItems.some((item, index) => (
      item.resource !== input.changeItems[index]?.resource
      || !input.changeItems[index]?.url
      || item.url !== input.changeItems[index]?.url
      || item.meaning !== input.changeItems[index]?.meaning.trim()
    ))
    || !!detail.normalSound !== !!input.normalSound
    || (detail.normalSound?.resource !== input.normalSound?.resource)
    || (!!input.normalSound && (!input.normalSound.url || detail.normalSound?.url !== input.normalSound.url))
    || !!detail.special !== !!input.special
  ) return false;
  if (!detail.special || !input.special) return true;
  return detail.special.probability === input.special.probability
    && detail.special.image.resource === input.special.image.resource
    && !!input.special.image.url
    && detail.special.image.url === input.special.image.url
    && detail.special.meaning === input.special.meaning.trim()
    && !!detail.special.sound === !!input.special.sound
    && detail.special.sound?.resource === input.special.sound?.resource
    && (!input.special.sound || (
      !!input.special.sound.url
      && detail.special.sound?.url === input.special.sound.url
    ));
}

function detailToPublicItem(detail: LocalAvatarToolDetail): LocalAvatarToolDto {
  return {
    id: detail.id,
    revision: detail.revision,
    name: detail.name,
    changeMode: detail.changeMode,
    defaultUrl: detail.defaultImage.url,
    changeUrls: detail.changeItems.map(item => item.url),
    ...(detail.normalSound ? { normalSoundUrl: detail.normalSound.url } : {}),
    ...(detail.special ? {
      special: {
        probability: detail.special.probability,
        imageUrl: detail.special.image.url,
        ...(detail.special.sound ? { soundUrl: detail.special.sound.url } : {}),
      },
    } : {}),
  };
}

export function useLocalAvatarToolCatalog(): LocalAvatarToolCatalog {
  const [registry, setRegistry] = useState(BUILT_IN_AVATAR_TOOL_REGISTRY);
  const [limits, setLimits] = useState<LocalAvatarToolLimits | null>(null);
  const [authoritativeLoaded, setAuthoritativeLoaded] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);
  const refreshEpochRef = useRef(0);
  const authoritativeRegistryRef = useRef<AvatarToolRegistrySnapshot | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;
    const requestEpoch = refreshEpochRef.current;
    const request = (async () => {
      try {
        const response = await fetchLocalAvatarTools();
        const next = createAvatarToolRegistrySnapshot(buildValidLocalDefinitions(response.items));
        if (requestEpoch !== refreshEpochRef.current) return;
        authoritativeRegistryRef.current = next;
        setRegistry(next);
        setLimits(response.limits);
        setAuthoritativeLoaded(true);
        setRefreshFailed(false);
      } catch (error) {
        if (requestEpoch === refreshEpochRef.current) setRefreshFailed(true);
        throw error;
      }
    })();
    refreshInFlightRef.current = request.then(
      () => {
        refreshInFlightRef.current = null;
      },
      (error) => {
        refreshInFlightRef.current = null;
        throw error;
      },
    );
    return refreshInFlightRef.current;
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    const requestFreshRefresh = () => {
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      void (async () => {
        await staleRefresh?.catch(() => undefined);
        await refresh().catch(() => undefined);
      })();
    };
    const refreshWhenActive = () => {
      if (document.visibilityState === 'hidden') return;
      requestFreshRefresh();
    };
    window.addEventListener('focus', refreshWhenActive);
    window.addEventListener('neko:refresh-local-avatar-tools', requestFreshRefresh);
    document.addEventListener('visibilitychange', refreshWhenActive);
    return () => {
      window.removeEventListener('focus', refreshWhenActive);
      window.removeEventListener('neko:refresh-local-avatar-tools', requestFreshRefresh);
      document.removeEventListener('visibilitychange', refreshWhenActive);
    };
  }, [refresh]);

  useEffect(() => {
    if (!authoritativeLoaded) return;
    window.dispatchEvent(new Event('neko:republish-avatar-tool-state'));
  }, [authoritativeLoaded, registry]);

  const create = useCallback(async (input: CreateLocalAvatarToolInput) => {
    let createdItem: LocalAvatarToolDto | null;
    try {
      createdItem = await createLocalAvatarTool(input);
    } catch (error) {
      if (
        error instanceof LocalAvatarToolCreateError
        && error.message === 'tool_id_conflict'
      ) throw error;
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      await staleRefresh?.catch(() => undefined);
      let refreshed = false;
      try {
        await refresh();
        refreshed = true;
      } catch {}
      if (refreshed && authoritativeRegistryRef.current?.has(input.toolId) === true) {
        const confirmedItem = await createLocalAvatarTool(input);
        if (confirmedItem?.id === input.toolId) return;
      }
      throw error;
    }
    const staleRefresh = refreshInFlightRef.current;
    refreshEpochRef.current += 1;
    if (createdItem) {
      const definitions = buildValidLocalDefinitions([createdItem]);
      if (definitions.length === 1) {
        setRegistry((current) => createAvatarToolRegistrySnapshot([
          ...current.definitions.filter(definition => definition.definitionVersion === 2 && definition.id !== createdItem.id),
          definitions[0],
        ]));
      }
    }
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [refresh]);

  const detail = useCallback(async (toolId: LocalAvatarToolId) => (
    fetchLocalAvatarToolDetail(toolId, limits?.maxChangeImages ?? 16)
  ), [limits?.maxChangeImages]);

  const update = useCallback(async (toolId: LocalAvatarToolId, input: UpdateLocalAvatarToolInput) => {
    let updatedItem: LocalAvatarToolDto | null;
    try {
      updatedItem = await updateLocalAvatarTool(toolId, input);
    } catch (error) {
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      await staleRefresh?.catch(() => undefined);
      let currentDetail: LocalAvatarToolDetail | null = null;
      try {
        currentDetail = await fetchLocalAvatarToolDetail(toolId, limits?.maxChangeImages ?? 16);
      } catch {}
      let refreshed = false;
      try {
        await refresh();
        refreshed = true;
      } catch {}
      if (
        currentDetail
        && currentDetail.revision !== input.baseRevision
        && detailMatchesUpdate(currentDetail, input)
      ) {
        if (!refreshed) {
          const definitions = buildValidLocalDefinitions([detailToPublicItem(currentDetail)]);
          if (definitions.length === 1) {
            setRegistry((current) => createAvatarToolRegistrySnapshot([
              ...current.definitions.filter(definition => definition.definitionVersion === 2 && definition.id !== toolId),
              definitions[0],
            ]));
          }
        }
        return;
      }
      if (
        error instanceof LocalAvatarToolCreateError
        && error.message === 'tool_revision_conflict'
        && currentDetail
      ) {
        let conflictDetail = currentDetail;
        if (refreshed) {
          try {
            conflictDetail = await fetchLocalAvatarToolDetail(toolId, limits?.maxChangeImages ?? 16);
          } catch {}
        }
        throw new LocalAvatarToolRevisionConflictError(conflictDetail);
      }
      throw error;
    }
    const staleRefresh = refreshInFlightRef.current;
    refreshEpochRef.current += 1;
    if (updatedItem) {
      const definitions = buildValidLocalDefinitions([updatedItem]);
      if (definitions.length === 1) {
        setRegistry((current) => createAvatarToolRegistrySnapshot([
          ...current.definitions.filter(definition => definition.definitionVersion === 2 && definition.id !== toolId),
          definitions[0],
        ]));
      }
    }
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [limits?.maxChangeImages, refresh]);

  const remove = useCallback(async (toolId: LocalAvatarToolId) => {
    try {
      await deleteLocalAvatarTool(toolId);
    } catch (error) {
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      await staleRefresh?.catch(() => undefined);
      let refreshed = false;
      try {
        await refresh();
        refreshed = true;
      } catch {}
      // 列表缺席不等于删掉了：list_items 会跳过校验失败的道具，被隔离的道具
      // 同样不在列表里，但它还在磁盘上。要确认删除得拿一个明确的 tool_not_found，
      // 否则用户会看到「删除成功」而道具下次刷新又冒出来。
      if (refreshed && authoritativeRegistryRef.current?.has(toolId) === false) {
        try {
          await fetchLocalAvatarToolDetail(toolId, limits?.maxChangeImages ?? 16);
        } catch (confirmation) {
          if (
            confirmation instanceof LocalAvatarToolDetailError
            && confirmation.message === 'tool_not_found'
          ) return;
        }
      }
      throw error;
    }
    const staleRefresh = refreshInFlightRef.current;
    refreshEpochRef.current += 1;
    setRegistry((current) => createAvatarToolRegistrySnapshot(
      current.definitions.filter(definition => definition.definitionVersion === 2 && definition.id !== toolId),
    ));
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [limits?.maxChangeImages, refresh]);

  return { registry, limits, authoritativeLoaded, refreshFailed, refresh, create, detail, update, remove };
}
