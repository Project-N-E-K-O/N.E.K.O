import { useCallback, useEffect, useRef, useState } from 'react';
import {
  buildLocalAvatarToolDefinition,
  createLocalAvatarTool,
  deleteLocalAvatarTool,
  fetchLocalAvatarToolDetail,
  fetchLocalAvatarToolDetailWithLimits,
  fetchLocalAvatarTools,
  LocalAvatarToolCreateError,
  LocalAvatarToolDetailError,
  LocalAvatarToolRevisionConflictError,
  updateLocalAvatarTool,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolDetail,
  type LocalAvatarToolDto,
  type LocalAvatarToolLimits,
  type LocalAvatarToolV3RuntimeProjection,
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
  type AvatarToolItem,
  type AvatarToolRegistrySnapshot,
} from './registry';

export type LocalAvatarToolCatalog = {
  registry: AvatarToolRegistrySnapshot;
  items: ReadonlyArray<AvatarToolItem>;
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

function buildManagementItem(item: LocalAvatarToolDto): AvatarToolItem {
  const imageUrl = item.recordVersion === 3 ? item.initialImageUrl : item.defaultUrl;
  return {
    id: item.id,
    label: { kind: 'literal', value: item.name },
    iconImagePath: imageUrl,
    pointerImagePath: imageUrl,
    pointerHotspotX: 40,
    pointerHotspotY: 40,
    pointerNaturalWidth: 80,
    pointerNaturalHeight: 80,
    pointerDisplayWidth: 80,
    pointerDisplayHeight: 80,
  };
}

function buildManagementItems(items: ReadonlyArray<LocalAvatarToolDto>): AvatarToolItem[] {
  return [
    ...BUILT_IN_AVATAR_TOOL_REGISTRY.items,
    ...items.map(buildManagementItem),
  ];
}

function retainOtherLocalDefinitions(
  definitions: ReadonlyArray<AvatarToolDefinition>,
  excludedToolId: LocalAvatarToolId,
): AvatarToolDefinition[] {
  return definitions.filter(definition => (
    definition.definitionVersion !== 1 && definition.id !== excludedToolId
  ));
}

function retainedMediaMatches(
  detail: { resource: string; url: string } | undefined,
  input: { resource?: string; url?: string } | undefined,
): boolean {
  if (!detail || !input) return !detail && !input;
  if (!input.resource || !input.url) return false;
  try {
    const detailDigest = new URL(detail.url, 'https://neko.invalid').searchParams.get('v');
    const inputDigest = new URL(input.url, 'https://neko.invalid').searchParams.get('v');
    return !!detailDigest && detailDigest === inputDigest;
  } catch {
    return false;
  }
}

function mutationResultIsUncertain(error: unknown, invalidResponseCode: string): boolean {
  return !(error instanceof LocalAvatarToolCreateError)
    || error.message === invalidResponseCode;
}

// Compare every graph field, preserving array order. Object-key insertion order
// carries no graph meaning; the strict detail codec still validates its schema.
function graphFieldsEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => graphFieldsEqual(value, right[index]));
  }
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  const leftFields = left as Record<string, unknown>;
  const rightFields = right as Record<string, unknown>;
  const keys = Object.keys(leftFields);
  return keys.length === Object.keys(rightFields).length
    && keys.every(key => Object.prototype.hasOwnProperty.call(rightFields, key)
      && graphFieldsEqual(leftFields[key], rightFields[key]));
}

function detailMatchesUpdate(detail: LocalAvatarToolDetail, input: UpdateLocalAvatarToolInput): boolean {
  if ('images' in input) {
    if (detail.recordVersion !== 3) return false;
    if (
      input.images.some(image => image.image.file)
      || input.normalSound?.file
      || input.special?.image.file
      || input.special?.sound?.file
    ) return false;
    return detail.name === input.name
      && detail.initialImageId === input.initialImageId
      && graphFieldsEqual(detail.imageInteractions, input.imageInteractions)
      && detail.images.length === input.images.length
      && detail.images.every((image, index) => (
        image.id === input.images[index]?.id
        && image.name === input.images[index]?.name.trim()
        && image.meaning === input.images[index]?.meaning.trim()
        && retainedMediaMatches(image, input.images[index]?.image)
      ))
      && retainedMediaMatches(detail.normalSound, input.normalSound)
      && !!detail.special === !!input.special
      && (!detail.special || !input.special || (
        detail.special.probability === input.special.probability
        && retainedMediaMatches(detail.special.image, input.special.image)
        && detail.special.meaning === input.special.meaning.trim()
        && retainedMediaMatches(detail.special.sound, input.special.sound)
      ));
  }
  if (detail.recordVersion === 3) return false;
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
  if (detail.recordVersion === 3) {
    const initial = detail.images.find(image => image.id === detail.initialImageId)!;
    const runtime: LocalAvatarToolV3RuntimeProjection = {
      images: detail.images.map(image => ({
        id: image.id,
        url: image.url,
        hasMeaning: image.meaning.trim().length > 0,
      })),
      initialImageId: detail.initialImageId,
      initialInteractionIds: detail.imageInteractions.initialLinks.map(link => link.to),
      interactions: detail.imageInteractions.items.map(item => ({
        id: item.id,
        trigger: item.trigger,
        actions: item.actions,
      })),
      links: detail.imageInteractions.links.map(link => ({ from: link.from, to: link.to })),
      ...(detail.normalSound ? { normalSoundUrl: detail.normalSound.url } : {}),
      ...(detail.special ? {
        special: {
          probability: detail.special.probability,
          imageUrl: detail.special.image.url,
          hasMeaning: detail.special.meaning.trim().length > 0,
          ...(detail.special.sound ? { soundUrl: detail.special.sound.url } : {}),
        },
      } : {}),
    };
    return {
      recordVersion: 3,
      id: detail.id,
      revision: detail.revision,
      name: detail.name,
      initialImageUrl: initial.url,
      runtime,
    };
  }
  return {
    recordVersion: 2,
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
  const [items, setItems] = useState<ReadonlyArray<AvatarToolItem>>(BUILT_IN_AVATAR_TOOL_REGISTRY.items);
  const [limits, setLimits] = useState<LocalAvatarToolLimits | null>(null);
  const [authoritativeLoaded, setAuthoritativeLoaded] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);
  const refreshEpochRef = useRef(0);
  const authoritativeItemIdsRef = useRef<ReadonlySet<string> | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;
    const requestEpoch = refreshEpochRef.current;
    const request = (async () => {
      try {
        const response = await fetchLocalAvatarTools();
        const next = createAvatarToolRegistrySnapshot(buildValidLocalDefinitions(response.items));
        if (requestEpoch !== refreshEpochRef.current) return;
        authoritativeItemIdsRef.current = new Set(response.items.map(item => item.id));
        setRegistry(next);
        setItems(buildManagementItems(response.items));
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
    let createdItem: LocalAvatarToolDto;
    try {
      createdItem = await createLocalAvatarTool(input);
    } catch (error) {
      if (
        error instanceof LocalAvatarToolCreateError
        && error.message === 'tool_id_conflict'
      ) throw error;
      if (!mutationResultIsUncertain(error, 'avatar_tool_create_response_invalid')) throw error;
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      await staleRefresh?.catch(() => undefined);
      let refreshed = false;
      try {
        await refresh();
        refreshed = true;
      } catch {}
      if (refreshed && authoritativeItemIdsRef.current?.has(input.toolId) === true) {
        const confirmedItem = await createLocalAvatarTool(input);
        if (confirmedItem.id === input.toolId) return;
      }
      throw error;
    }
    const staleRefresh = refreshInFlightRef.current;
    refreshEpochRef.current += 1;
    setItems(current => [
      ...current.filter(item => item.id !== createdItem.id),
      buildManagementItem(createdItem),
    ]);
    const definitions = buildValidLocalDefinitions([createdItem]);
    setRegistry((current) => createAvatarToolRegistrySnapshot([
      ...retainOtherLocalDefinitions(current.definitions, createdItem.id),
      ...definitions,
    ]));
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [refresh]);

  const detail = useCallback(async (toolId: LocalAvatarToolId) => {
    const response = await fetchLocalAvatarToolDetailWithLimits(toolId);
    setLimits(response.limits);
    return response.detail;
  }, []);

  const update = useCallback(async (toolId: LocalAvatarToolId, input: UpdateLocalAvatarToolInput) => {
    let updatedItem: LocalAvatarToolDto;
    try {
      updatedItem = await updateLocalAvatarTool(toolId, input);
    } catch (error) {
      const revisionConflict = error instanceof LocalAvatarToolCreateError
        && error.message === 'tool_revision_conflict';
      if (
        !revisionConflict
        && !mutationResultIsUncertain(error, 'avatar_tool_update_response_invalid')
      ) throw error;
      const staleRefresh = refreshInFlightRef.current;
      refreshEpochRef.current += 1;
      await staleRefresh?.catch(() => undefined);
      let currentDetail: LocalAvatarToolDetail | null = null;
      try {
        const response = await fetchLocalAvatarToolDetailWithLimits(toolId);
        setLimits(response.limits);
        currentDetail = response.detail;
      } catch {}
      let refreshed = false;
      try {
        await refresh();
        refreshed = true;
      } catch {}
      if (revisionConflict && currentDetail) {
        let conflictDetail = currentDetail;
        if (refreshed) {
          try {
            const response = await fetchLocalAvatarToolDetailWithLimits(toolId);
            setLimits(response.limits);
            conflictDetail = response.detail;
          } catch {}
        }
        throw new LocalAvatarToolRevisionConflictError(conflictDetail);
      }
      if (revisionConflict) throw error;
      if (
        currentDetail
        && currentDetail.revision !== input.baseRevision
        && detailMatchesUpdate(currentDetail, input)
      ) {
        if (!refreshed) {
          setItems(current => [
            ...current.filter(item => item.id !== toolId),
            buildManagementItem(detailToPublicItem(currentDetail)),
          ]);
          const definitions = buildValidLocalDefinitions([detailToPublicItem(currentDetail)]);
          setRegistry((current) => createAvatarToolRegistrySnapshot([
            ...retainOtherLocalDefinitions(current.definitions, toolId),
            ...definitions,
          ]));
        }
        return;
      }
      throw error;
    }
    const staleRefresh = refreshInFlightRef.current;
    refreshEpochRef.current += 1;
    setItems(current => [
      ...current.filter(item => item.id !== toolId),
      buildManagementItem(updatedItem),
    ]);
    const definitions = buildValidLocalDefinitions([updatedItem]);
    setRegistry((current) => createAvatarToolRegistrySnapshot([
      ...retainOtherLocalDefinitions(current.definitions, toolId),
      ...definitions,
    ]));
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [refresh]);

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
      if (refreshed && authoritativeItemIdsRef.current?.has(toolId) === false) {
        try {
          await fetchLocalAvatarToolDetail(toolId);
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
    setItems(current => current.filter(item => item.id !== toolId));
    setRegistry((current) => createAvatarToolRegistrySnapshot(
      retainOtherLocalDefinitions(current.definitions, toolId),
    ));
    await staleRefresh?.catch(() => undefined);
    await refresh().catch(() => undefined);
  }, [refresh]);

  return { registry, items, limits, authoritativeLoaded, refreshFailed, refresh, create, detail, update, remove };
}
