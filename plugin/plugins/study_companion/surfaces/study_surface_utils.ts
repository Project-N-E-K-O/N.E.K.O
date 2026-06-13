import type { PluginSurfaceProps } from '@neko/plugin-ui';

type RunExportItem = {
  type?: string;
  json?: {
    success?: boolean;
    error?: unknown;
    data?: unknown;
  };
};

type JsonRunExportItem = RunExportItem & {
  json: NonNullable<RunExportItem['json']>;
};

type CallPluginOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
};

const DEFAULT_PLUGIN_CALL_TIMEOUT_MS = 90000;
const PLUGIN_CALL_POLL_INTERVAL_MS = 250;

function isJsonRunExportItem(candidate: RunExportItem): candidate is JsonRunExportItem {
  return candidate.type === 'json' && !!candidate.json;
}

export const BRAND_CSS = `
  :host, :root {
    color-scheme: light;
    --bg: #f3f7f1;
    --paper: rgba(253, 255, 250, 0.94);
    --paper-strong: rgba(255, 255, 255, 0.98);
    --ink: #1f2924;
    --muted: #607168;
    --line: rgba(31, 41, 36, 0.13);
    --brand: #2f7d57;
    --brand-strong: #17563d;
    --accent: #d58b2b;
    --accent-strong: #8a5317;
    --warning: #b7791f;
    --warning-strong: #784910;
    --warning-bg: rgba(183, 121, 31, 0.10);
    --study-companion: #2f7d57;
    --study-interactive: #536aa3;
    --study-teaching: #c8762c;
    --mastery-new: #cbd5d0;
    --mastery-weak: #e89a90;
    --mastery-progress: #e2b85a;
    --mastery-good: #9bd9b8;
    --mastery-mastered: #82d99e;
    --pomodoro-focus: #ef4444;
    --pomodoro-break-short: #22c55e;
    --pomodoro-break-long: #3b82f6;
    --fsrs-again: #dc2626;
    --fsrs-hard: #b45309;
    --fsrs-good: #15803d;
    --fsrs-easy: #2563eb;
    --shadow: 0 10px 24px rgba(31, 52, 40, 0.07);
    --shadow-strong: 0 16px 34px rgba(31, 52, 40, 0.12);
    --radius: 8px;
    --radius-sm: 6px;
    --transition-fast: 150ms ease;
    --transition-normal: 300ms cubic-bezier(0.4, 0, 0.2, 1);
    --transition-slow: 500ms ease;
    --study-content-font-size: 16px;
    --study-math-font-size: 14px;
  }

  .study-panel {
    display: grid;
    gap: 14px;
    color: var(--ink);
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  }

  .surface-shell {
    min-width: 760px;
    padding: 18px;
    border: 1px solid rgba(47, 125, 87, 0.18);
    border-left: 5px solid rgba(47, 125, 87, 0.62);
    border-radius: var(--radius);
    background:
      linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(244, 249, 241, 0.86)),
      var(--paper);
    box-shadow: var(--shadow);
  }

  .surface-shell::before {
    content: "";
    justify-self: start;
    width: 96px;
    height: 2px;
    background: repeating-linear-gradient(
      90deg,
      rgba(47, 125, 87, 0.24) 0 14px,
      transparent 14px 22px
    );
  }

  .study-panel__header {
    display: grid;
    grid-template-columns: minmax(220px, 1fr) auto;
    gap: 14px;
    align-items: center;
  }

  .study-panel__header h1 {
    margin: 0;
    font-size: 24px;
    line-height: 1.15;
    letter-spacing: 0;
  }

  .study-panel__header span,
  .study-panel__reply-label {
    color: var(--muted);
    font-size: 13px;
  }

  .mode-switch {
    --indicator-left: 5px;
    --indicator-width: calc((100% - 18px) / 3);
    position: relative;
    display: flex;
    gap: 4px;
    min-width: 330px;
    padding: 5px;
    border: 1px solid rgba(47, 125, 87, 0.18);
    border-radius: var(--radius-sm);
    background:
      linear-gradient(180deg, rgba(247, 250, 244, 0.96), rgba(232, 243, 235, 0.78)),
      rgba(47, 125, 87, 0.055);
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.86),
      inset 0 -1px 0 rgba(47, 125, 87, 0.08);
    isolation: isolate;
  }

  .mode-switch[data-active="interactive"] {
    --indicator-left: calc(5px + ((100% - 18px) / 3) + 4px);
  }

  .mode-switch[data-active="teaching"] {
    --indicator-left: calc(5px + (((100% - 18px) / 3) + 4px) * 2);
  }

  .study-panel__modes.mode-switch::before,
  .study-panel__modes.mode-switch::after {
    display: none;
  }

  .mode-switch::before,
  .mode-switch::after {
    content: "";
    position: absolute;
    pointer-events: none;
    opacity: 1;
    transition:
      left var(--transition-normal),
      width var(--transition-normal),
      background var(--transition-fast);
  }

  .mode-switch::before {
    top: 5px;
    left: var(--indicator-left);
    width: var(--indicator-width);
    height: calc(100% - 10px);
    z-index: 0;
    border: 1px solid rgba(47, 125, 87, 0.16);
    border-radius: 5px;
    background: rgba(255, 255, 255, 0.92);
    box-shadow:
      0 5px 14px rgba(31, 52, 40, 0.06),
      inset 0 1px 0 rgba(255, 255, 255, 0.88);
  }

  .mode-switch::after {
    left: calc(var(--indicator-left) + 10px);
    bottom: 5px;
    width: max(24px, calc(var(--indicator-width) - 20px));
    height: 3px;
    z-index: 1;
    border-radius: 999px;
    background: rgba(47, 125, 87, 0.68);
  }

  .mode-switch[data-active="interactive"]::before {
    border-color: rgba(83, 106, 163, 0.18);
    background: rgba(83, 106, 163, 0.08);
  }

  .mode-switch[data-active="interactive"]::after {
    background: rgba(83, 106, 163, 0.62);
  }

  .mode-switch[data-active="teaching"]::before {
    border-color: rgba(200, 118, 44, 0.18);
    background: rgba(200, 118, 44, 0.09);
  }

  .mode-switch[data-active="teaching"]::after {
    background: rgba(200, 118, 44, 0.64);
  }

  .mode-btn {
    position: relative;
    z-index: 2;
    flex: 1 1 0;
    min-width: 0;
    min-height: 38px;
    padding: 8px 14px 10px;
    border: none;
    border-radius: 5px;
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 800;
    cursor: pointer;
    white-space: nowrap;
  }

  .mode-btn.active,
  .mode-btn.is-active {
    color: var(--brand-strong);
  }

  .mode-btn[data-mode="interactive"].active,
  .mode-btn[data-mode="interactive"].is-active {
    color: var(--study-interactive);
  }

  .mode-btn[data-mode="teaching"].active,
  .mode-btn[data-mode="teaching"].is-active {
    color: var(--study-teaching);
  }

  .study-panel__state {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
  }

  .study-panel__state > div {
    display: grid;
    gap: 4px;
    padding: 12px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.34);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
  }

  .study-panel__state span {
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .study-panel__state strong {
    overflow-wrap: anywhere;
  }

  .study-panel textarea,
  .study-panel pre,
  .study-panel__math-reply {
    width: 100%;
    min-height: 180px;
    margin: 0;
    border: 1px solid rgba(31, 35, 41, 0.12);
    border-radius: var(--radius-sm);
    background: var(--paper-strong);
    color: var(--ink);
    padding: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: break-word;
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.88),
      inset 4px 0 0 rgba(47, 125, 87, 0.08);
  }

  .study-panel__math-reply .katex {
    color: var(--ink);
  }

  .study-panel textarea {
    resize: vertical;
    background-image:
      linear-gradient(transparent calc(1.5em - 1px), rgba(47, 125, 87, 0.055) calc(1.5em - 1px));
    background-size: 100% 1.5em;
  }

  .study-panel__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }

  .study-panel__row {
    display: grid;
    grid-template-columns: minmax(180px, 0.8fr) minmax(220px, 1fr) auto;
    gap: 10px;
    align-items: center;
    width: 100%;
    padding: 12px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.28);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
  }

  .study-panel label {
    display: grid;
    gap: 6px;
  }

  .study-panel input,
  .study-panel select {
    min-height: 36px;
    border: 1px solid rgba(31, 35, 41, 0.12);
    border-radius: var(--radius-sm);
    background: var(--paper-strong);
    color: var(--ink);
    padding: 7px 10px;
    font: inherit;
  }

  .study-panel button {
    min-height: 36px;
    border: 1px solid rgba(23, 86, 61, 0.24);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.92);
    color: var(--brand-strong);
    font: inherit;
    font-weight: 800;
    cursor: pointer;
    transition:
      transform var(--transition-fast),
      box-shadow var(--transition-fast),
      border-color var(--transition-fast);
  }

  .study-panel button:hover:not(:disabled) {
    border-color: rgba(23, 86, 61, 0.42);
    box-shadow: 0 6px 16px rgba(31, 52, 40, 0.10);
  }

  .study-panel button:active:not(:disabled) {
    transform: scale(0.97);
  }

  .study-panel button:disabled {
    color: var(--muted);
    cursor: not-allowed;
    opacity: 0.58;
  }

  .study-panel button:focus-visible,
  .study-panel input:focus-visible,
  .study-panel select:focus-visible,
  .study-panel textarea:focus-visible {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  .study-panel button[data-rating="again"] {
    border-color: rgba(239, 68, 68, 0.36);
    color: var(--fsrs-again);
  }

  .study-panel button[data-rating="hard"] {
    border-color: rgba(245, 158, 11, 0.38);
    color: var(--fsrs-hard);
  }

  .study-panel button[data-rating="good"] {
    border-color: rgba(34, 197, 94, 0.36);
    color: var(--fsrs-good);
  }

  .study-panel button[data-rating="easy"] {
    border-color: rgba(59, 130, 246, 0.36);
    color: var(--fsrs-easy);
  }

  .knowledge-node {
    justify-content: flex-start;
    color: var(--ink);
  }

  .knowledge-node[data-mastery="new"] {
    background: var(--mastery-new);
    border-color: rgba(203, 213, 208, 0.72);
  }

  .knowledge-node[data-mastery="weak"] {
    background: var(--mastery-weak);
    border-color: rgba(214, 106, 95, 0.42);
  }

  .knowledge-node[data-mastery="progress"] {
    background: var(--mastery-progress);
    border-color: rgba(217, 164, 65, 0.46);
  }

  .knowledge-node[data-mastery="good"] {
    background: var(--mastery-good);
    border-color: rgba(47, 125, 87, 0.34);
  }

  .knowledge-node[data-mastery="mastered"] {
    background: var(--mastery-mastered);
    border-color: rgba(31, 157, 98, 0.34);
  }

  .pomodoro-ring {
    display: grid;
    place-items: center;
    min-height: 128px;
    border: 10px solid var(--pomodoro-focus);
    border-radius: 999px;
    color: var(--ink);
    font-size: 28px;
    font-weight: 900;
  }

  .pomodoro-ring[data-mode="break_short"] {
    border-color: var(--pomodoro-break-short);
  }

  .pomodoro-ring[data-mode="break_long"] {
    border-color: var(--pomodoro-break-long);
  }

  @media (prefers-reduced-motion: reduce) {
    .study-panel *,
    .study-panel *::before,
    .study-panel *::after {
      animation: none !important;
      transition-duration: 0.001ms !important;
    }
  }
`;

export const STUDY_SURFACE_MESSAGE_TYPES = {
  openSurface: 'neko-study-open-surface',
  reviewCompleted: 'neko-study-review-completed',
  refreshSummary: 'neko-study-refresh-summary',
  memoryDeckUpdated: 'neko-study-memory-deck-updated',
} as const;

type HostedRuntimeWindow = Window & {
  __NEKO_PAYLOAD?: {
    hostOrigin?: unknown;
  };
};

function studySurfaceTargetOrigin() {
  const payload = (window as HostedRuntimeWindow).__NEKO_PAYLOAD;
  const hostOrigin = payload && typeof payload.hostOrigin === 'string' ? payload.hostOrigin : '';
  if (hostOrigin) {
    return hostOrigin;
  }
  const origin = window.location.origin;
  return origin && origin !== 'null' ? origin : '*';
}

export function postStudySurfaceMessage(message: { type: string; payload?: unknown }) {
  window.parent?.postMessage?.(message, studySurfaceTargetOrigin());
}

let brandCSSInjected = false;

export function ensureBrandCSS() {
  if (brandCSSInjected) {
    return;
  }
  if (!document.head) {
    return;
  }
  if (document.getElementById('study-companion-brand-css')) {
    brandCSSInjected = true;
    return;
  }
  const style = document.createElement('style');
  style.id = 'study-companion-brand-css';
  style.textContent = BRAND_CSS;
  document.head.appendChild(style);
  // Brand CSS is static for an iframe lifetime. Hot updates need versioned cleanup.
  brandCSSInjected = true;
}

export async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json();
}

function pluginErrorMessage(error: unknown) {
  if (typeof error === 'string') {
    return error;
  }
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message) {
      return message;
    }
  }
  if (error !== undefined && error !== null) {
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  }
  return 'Plugin call failed';
}

function waitForPluginPoll(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new DOMException('Aborted', 'AbortError'));
  }
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, PLUGIN_CALL_POLL_INTERVAL_MS);
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timeout);
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });
}

export async function callPlugin(
  entryId: string,
  args: Record<string, unknown> = {},
  options: CallPluginOptions = {},
) {
  const { signal, timeoutMs = DEFAULT_PLUGIN_CALL_TIMEOUT_MS } = options;
  const created = await readJsonResponse(await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
    signal,
  }), 'Run create');
  const runId = created.run_id || created.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await waitForPluginPoll(signal);
    const run = await readJsonResponse(await fetch(`/runs/${runId}`, { signal }), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`, { signal }), 'Run export');
      const items = Array.isArray(exported.items) ? exported.items as RunExportItem[] : [];
      const item = items.find(isJsonRunExportItem);
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(pluginErrorMessage(item.json.error));
      }
      return item.json.data || {};
    }
    if (['failed', 'error', 'canceled', 'cancelled', 'timeout', 'timed_out'].includes(run.status)) {
      throw new Error(run.error?.message || run.error_message || run.message || run.status);
    }
  }
  throw new Error('Plugin call timed out');
}

export function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export function formatError(error: unknown) {
  return error instanceof Error ? error.message : pluginErrorMessage(error);
}
