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

export function text(props: PluginSurfaceProps, key: string, fallback: string): string {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
