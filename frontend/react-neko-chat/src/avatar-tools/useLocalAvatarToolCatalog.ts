import { useCallback, useEffect, useRef, useState } from 'react';
import {
  buildLocalAvatarToolDefinition,
  createLocalAvatarTool,
  fetchLocalAvatarTools,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolDto,
  type LocalAvatarToolLimits,
} from './localTools';
import { validateAvatarToolDefinition, type AvatarToolDefinition } from './catalog';
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

export function useLocalAvatarToolCatalog(): LocalAvatarToolCatalog {
  const [registry, setRegistry] = useState(BUILT_IN_AVATAR_TOOL_REGISTRY);
  const [limits, setLimits] = useState<LocalAvatarToolLimits | null>(null);
  const [authoritativeLoaded, setAuthoritativeLoaded] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;
    const request = (async () => {
      try {
        const response = await fetchLocalAvatarTools();
        const next = createAvatarToolRegistrySnapshot(buildValidLocalDefinitions(response.items));
        setRegistry(next);
        setLimits(response.limits);
        setAuthoritativeLoaded(true);
        setRefreshFailed(false);
      } catch (error) {
        setRefreshFailed(true);
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
    const refreshWhenActive = () => {
      if (document.visibilityState === 'hidden') return;
      refresh().catch(() => undefined);
    };
    window.addEventListener('focus', refreshWhenActive);
    document.addEventListener('visibilitychange', refreshWhenActive);
    return () => {
      window.removeEventListener('focus', refreshWhenActive);
      document.removeEventListener('visibilitychange', refreshWhenActive);
    };
  }, [refresh]);

  const create = useCallback(async (input: CreateLocalAvatarToolInput) => {
    const createdItem = await createLocalAvatarTool(input);
    if (createdItem) {
      const definitions = buildValidLocalDefinitions([createdItem]);
      if (definitions.length === 1) {
        setRegistry((current) => createAvatarToolRegistrySnapshot([
          ...current.definitions.filter(definition => definition.definitionVersion === 2 && definition.id !== createdItem.id),
          definitions[0],
        ]));
      }
    }
    await refresh().catch(() => undefined);
  }, [refresh]);

  return { registry, limits, authoritativeLoaded, refreshFailed, refresh, create };
}
