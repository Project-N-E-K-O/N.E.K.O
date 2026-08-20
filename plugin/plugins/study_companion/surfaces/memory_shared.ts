import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin as callHostedPlugin } from './study_surface_utils';

type JsonObject = Record<string, unknown>;
type HostedApi = PluginSurfaceProps['api'];

export async function callPlugin<T = JsonObject>(
  api: HostedApi,
  entryId: string,
  args: JsonObject = {},
  signal?: AbortSignal,
): Promise<T> {
  return await callHostedPlugin<T>(api, entryId, args, signal);
}

type MemoryDeckPage<T> = {
  decks?: T[];
  has_more?: boolean;
  next_offset?: number | null;
};

export async function listAllMemoryDecks<T>(
  api: HostedApi,
  signal?: AbortSignal,
): Promise<T[]> {
  const decks: T[] = [];
  let offset = 0;
  let hasMore = true;

  while (hasMore) {
    const payload = await callPlugin<MemoryDeckPage<T>>(
      api,
      'study_memory_list_decks',
      { limit: 100, offset },
      signal,
    );
    const pageDecks = Array.isArray(payload.decks) ? payload.decks : [];
    decks.push(...pageDecks);
    hasMore = payload.has_more === true;
    if (!hasMore) break;

    const nextOffset = Number(payload.next_offset);
    if (!Number.isSafeInteger(nextOffset) || nextOffset <= offset) {
      throw new Error('Invalid memory deck continuation offset');
    }
    offset = nextOffset;
  }

  return decks;
}

export function text(props: PluginSurfaceProps, key: string, fallback: string): string {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
