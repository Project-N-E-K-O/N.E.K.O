import type { PluginSurfaceProps } from '@neko/plugin-ui';

type JsonObject = Record<string, unknown>;

type RunCreated = {
  id?: string;
  run_id?: string;
};

type RunStatus = {
  status?: string;
  message?: string;
  error?: {
    message?: string;
  };
};

type RunJsonResult = {
  success?: boolean;
  message?: string;
  data?: unknown;
  error?: {
    message?: string;
  };
};

type RunExportItem = {
  type?: string;
  json?: RunJsonResult;
};

type RunExport = {
  items?: RunExportItem[];
};

export async function readJsonResponse<T>(response: Response, label: string): Promise<T> {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json() as T;
}

export async function callPlugin<T = JsonObject>(
  entryId: string,
  args: JsonObject = {},
  signal?: AbortSignal,
): Promise<T> {
  const created = await readJsonResponse<RunCreated>(await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
    signal,
  }), 'Run create');
  const runId = created.run_id || created.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await waitForPoll(signal);
    const run = await readJsonResponse<RunStatus>(await fetch(`/runs/${runId}`, { signal }), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse<RunExport>(
        await fetch(`/runs/${runId}/export`, { signal }),
        'Run export',
      );
      const item = (exported.items || []).find(isJsonExportItem);
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(item.json.error?.message || item.json.message || 'Plugin call failed');
      }
      return (item.json.data || {}) as T;
    }
    if (['failed', 'canceled', 'timeout'].includes(String(run.status))) {
      throw new Error(run.error?.message || run.message || String(run.status));
    }
  }
  throw new Error('Plugin call timed out');
}

export function text(props: PluginSurfaceProps, key: string, fallback: string): string {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isJsonExportItem(item: RunExportItem): item is RunExportItem & { json: RunJsonResult } {
  return item.type === 'json' && Boolean(item.json);
}

function waitForPoll(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new Error('Plugin call aborted'));
  }
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(resolve, 350);
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timeoutId);
      reject(new Error('Plugin call aborted'));
    }, { once: true });
  });
}
