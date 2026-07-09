import type { PluginSurfaceProps } from '@neko/plugin-ui';

type HostedApi = PluginSurfaceProps['api'];

type CallPluginOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
};

const DEFAULT_PLUGIN_CALL_TIMEOUT_MS = 90000;

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

  .study-panel__math-reply .study-reply-section {
    margin: 0 0 0.85rem;
    border: 1px solid rgba(31, 35, 41, 0.10);
    border-left: 4px solid var(--study-section-accent, rgba(47, 125, 87, 0.42));
    border-radius: var(--radius-sm);
    background: var(--study-section-bg, rgba(255, 255, 255, 0.72));
    padding: 10px 12px 11px;
    white-space: normal;
  }

  .study-panel__math-reply .study-reply-section--analysis {
    --study-section-accent: #2f7d57;
    --study-section-bg: rgba(47, 125, 87, 0.08);
  }

  .study-panel__math-reply .study-reply-section--process {
    --study-section-accent: #2f6fbb;
    --study-section-bg: rgba(47, 111, 187, 0.08);
  }

  .study-panel__math-reply .study-reply-section--answer {
    --study-section-accent: #9b6a16;
    --study-section-bg: rgba(155, 106, 22, 0.09);
  }

  .study-panel__math-reply .study-reply-section--transfer {
    --study-section-accent: #7a4aa0;
    --study-section-bg: rgba(122, 74, 160, 0.08);
  }

  .study-panel__math-reply .study-reply-section__title {
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    margin: 0 0 0.45rem;
    border-radius: 4px;
    background: var(--study-section-accent, #2f7d57);
    color: #fff;
    padding: 2px 8px;
    font-size: 13px;
    font-weight: 800;
    line-height: 1.35;
  }

  .study-panel__math-reply .study-reply-section__body {
    white-space: pre-wrap;
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

  .knowledge-edge-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 12px;
    align-items: start;
  }

  .knowledge-edge-card {
    display: grid;
    gap: 10px;
    min-width: 0;
    padding: 14px;
    border: 1px solid rgba(47, 125, 87, 0.16);
    border-radius: 8px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 252, 249, 0.86));
  }

  .knowledge-edge-card h3 {
    margin: 0;
    color: var(--ink);
    font-size: 14px;
    line-height: 1.35;
  }

  .knowledge-edge-card__items {
    display: grid;
    gap: 8px;
  }

  .knowledge-edge-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 8px;
    align-items: center;
    min-width: 0;
  }

  .knowledge-edge-row__relation {
    padding: 4px 8px;
    border-radius: 999px;
    background: rgba(47, 125, 87, 0.10);
    color: var(--brand-dark);
    font-size: 12px;
    font-weight: 900;
    white-space: nowrap;
  }

  .knowledge-edge-row[data-relation="prerequisite"] .knowledge-edge-row__relation {
    background: rgba(217, 164, 65, 0.16);
    color: #8a5a00;
  }

  .knowledge-edge-row__target {
    min-width: 0;
    color: var(--muted);
    font-weight: 800;
    overflow-wrap: anywhere;
  }

  .knowledge-edge-more {
    color: var(--muted);
    font-size: 12px;
    font-weight: 800;
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

  .study-panel__toolbar {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }

  .study-panel__note-list {
    display: grid;
    gap: 10px;
    align-content: start;
  }

  .study-panel__note-card {
    display: grid;
    gap: 6px;
    text-align: left;
    padding: 12px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.28);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
    color: var(--ink);
    font-weight: 400;
  }

  .study-panel__note-card.is-selected {
    border-left-color: var(--brand);
    background: var(--paper-strong);
    box-shadow: var(--shadow);
  }

  .study-panel__note-title {
    font-size: 15px;
    font-weight: 800;
    color: var(--ink);
  }

  .study-panel__note-meta {
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .study-panel__note-snippet {
    color: var(--ink);
    font-size: 13px;
    overflow-wrap: anywhere;
  }

  .study-panel__chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .study-panel__chip {
    padding: 2px 8px;
    border: 1px solid rgba(47, 125, 87, 0.18);
    border-radius: 999px;
    background: rgba(47, 125, 87, 0.07);
    color: var(--brand-strong);
    font-size: 11px;
    font-weight: 700;
  }

  .study-panel__chip.is-topic {
    border-color: rgba(83, 106, 163, 0.22);
    background: rgba(83, 106, 163, 0.09);
    color: var(--study-interactive);
  }

  .study-panel__empty {
    padding: 18px;
    border: 1px dashed rgba(47, 125, 87, 0.24);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.62);
    color: var(--muted);
    text-align: center;
    font-size: 13px;
  }

  .study-panel__layout {
    display: grid;
    grid-template-columns: minmax(180px, 0.7fr) minmax(220px, 1fr) minmax(220px, 1fr);
    gap: 14px;
    align-items: start;
  }

  .study-panel__sidebar {
    display: grid;
    gap: 8px;
    align-content: start;
  }

  .study-panel__folder {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: center;
    text-align: left;
    padding: 10px 12px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.28);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
    color: var(--ink);
  }

  .study-panel__folder.is-selected {
    border-left-color: var(--brand);
    background: var(--paper-strong);
    box-shadow: var(--shadow);
  }

  .study-panel__inline-form {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: center;
  }

  .study-panel__column {
    display: grid;
    gap: 10px;
    align-content: start;
  }

  .study-panel__search-row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: center;
  }

  .study-panel__detail {
    display: grid;
    gap: 10px;
    align-content: start;
    padding: 14px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.34);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
  }

  .study-panel__detail h2 {
    margin: 0;
    font-size: 18px;
  }

  .study-panel__tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .study-panel__tab.is-active {
    border-color: rgba(23, 86, 61, 0.42);
    background: var(--paper-strong);
    color: var(--brand-strong);
    box-shadow: 0 6px 16px rgba(31, 52, 40, 0.10);
  }

  .study-panel__result-row {
    display: grid;
    gap: 4px;
    padding: 12px;
    border: 1px solid rgba(47, 125, 87, 0.14);
    border-left: 3px solid rgba(47, 125, 87, 0.28);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.84);
  }

  .study-panel__result-row span {
    color: var(--muted);
    font-size: 13px;
    overflow-wrap: anywhere;
  }

  .study-panel__preview {
    width: 100%;
    min-height: 180px;
    margin: 0;
    border: 1px solid rgba(31, 35, 41, 0.12);
    border-radius: var(--radius-sm);
    background: var(--paper-strong);
    color: var(--ink);
    padding: 12px;
    line-height: 1.5;
    overflow-wrap: break-word;
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

function abortError() {
  return new DOMException('Aborted', 'AbortError');
}

function isAbortSignal(value: CallPluginOptions | AbortSignal): value is AbortSignal {
  return typeof AbortSignal !== 'undefined' && value instanceof AbortSignal;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object';
}

function unwrapPluginResult<T>(rawResult: unknown): T {
  let payload = rawResult;
  if (isObject(payload) && 'result' in payload && ('plugin_id' in payload || 'action_id' in payload)) {
    payload = payload.result;
  }
  if (isObject(payload) && (payload.success === false || 'error' in payload || 'data' in payload)) {
    // Only the legacy `/runs` envelope signals failure with `success === false`.
    // A hosted action result may legitimately carry an `error` field as domain
    // data (e.g. `{available: false, error: "..."}` for a disabled state), so the
    // mere presence of `error` must not be turned into a thrown exception.
    if (payload.success === false) {
      throw new Error(pluginErrorMessage(payload.error || payload.message));
    }
    if ('data' in payload) {
      return (payload.data ?? {}) as T;
    }
  }
  return (payload ?? {}) as T;
}

export async function callPlugin<T = Record<string, unknown>>(
  api: HostedApi,
  entryId: string,
  args: Record<string, unknown> = {},
  options: CallPluginOptions | AbortSignal = {},
): Promise<T> {
  const normalized = isAbortSignal(options) ? { signal: options } : options;
  const { signal, timeoutMs = DEFAULT_PLUGIN_CALL_TIMEOUT_MS } = normalized;
  if (!api || typeof api.call !== 'function') {
    throw new Error('Hosted API call bridge unavailable');
  }
  if (signal?.aborted) {
    throw abortError();
  }

  let timeoutId = 0;
  let abortHandler: (() => void) | undefined;
  const pending: Array<Promise<unknown>> = [api.call(entryId, args, { timeoutMs })];
  if (timeoutMs > 0) {
    pending.push(new Promise((_, reject) => {
      timeoutId = window.setTimeout(() => reject(new Error('Plugin call timed out')), timeoutMs);
    }));
  }
  if (signal) {
    pending.push(new Promise((_, reject) => {
      abortHandler = () => reject(abortError());
      signal.addEventListener('abort', abortHandler, { once: true });
    }));
  }

  try {
    return unwrapPluginResult<T>(await Promise.race(pending));
  } finally {
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    if (signal && abortHandler) {
      signal.removeEventListener('abort', abortHandler);
    }
  }
}

export function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export function deckTypeLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || 'custom').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    word: ['ui.memory.deck_type.word', 'Word'],
    passage: ['ui.memory.deck_type.passage', 'Passage'],
    formula: ['ui.memory.deck_type.formula', 'Formula'],
    custom: ['ui.memory.deck_type.custom', 'Custom'],
  };
  const pair = labels[normalized] || labels.custom;
  return text(props, pair[0], pair[1]);
}

export function goalUnitLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || 'cards').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    card: ['ui.daily_goal.deck_unit_cards', 'cards'],
    cards: ['ui.daily_goal.deck_unit_cards', 'cards'],
    minute: ['ui.daily_goal.deck_unit_minutes', 'minutes'],
    minutes: ['ui.daily_goal.deck_unit_minutes', 'minutes'],
    attempt: ['ui.daily_goal.deck_unit_attempts', 'attempts'],
    attempts: ['ui.daily_goal.deck_unit_attempts', 'attempts'],
  };
  const pair = labels[normalized];
  return pair ? text(props, pair[0], pair[1]) : normalized;
}

export function memoryItemTypeLabel(props: PluginSurfaceProps, value: unknown): string {
  return deckTypeLabel(props, value);
}

export function targetTypeLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'subject') return text(props, 'ui.label.subject', 'Subject');
  if (normalized === 'deck') return text(props, 'ui.memory.deck', 'Deck');
  return normalized || '-';
}

export function pomodoroModeLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || 'focus').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    focus: ['ui.pomodoro.mode.focus', 'Focus'],
    break_short: ['ui.pomodoro.mode.break_short', 'Short break'],
    break_long: ['ui.pomodoro.mode.break_long', 'Long break'],
  };
  const pair = labels[normalized];
  return pair ? text(props, pair[0], pair[1]) : normalized;
}

export function pomodoroStateLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || 'idle').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    idle: ['ui.status.pomodoro.idle', 'Idle'],
    focusing: ['ui.status.pomodoro.focusing', 'Focusing'],
    paused: ['ui.status.pomodoro.paused', 'Paused'],
    short_break: ['ui.status.pomodoro.short_break', 'Short break'],
    long_break: ['ui.status.pomodoro.long_break', 'Long break'],
    cancelled: ['ui.status.pomodoro.cancelled', 'Stopped'],
    completed: ['ui.status.pomodoro.completed', 'Completed'],
  };
  const pair = labels[normalized];
  return pair ? text(props, pair[0], pair[1]) : normalized;
}

export function exportFormatLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || '').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    csv: ['ui.format.csv', 'CSV'],
    json: ['ui.format.json', 'JSON'],
    markdown: ['ui.format.markdown', 'Markdown'],
    pdf: ['ui.format.pdf', 'PDF'],
    docx: ['ui.format.docx', 'DOCX'],
    xmind: ['ui.format.xmind', 'XMind'],
  };
  const pair = labels[normalized];
  return pair ? text(props, pair[0], pair[1]) : normalized;
}

export function exportStyleLabel(props: PluginSurfaceProps, value: unknown): string {
  const normalized = String(value || 'neko').trim().toLowerCase();
  const labels: Record<string, [string, string]> = {
    neko: ['ui.export.style.neko', 'Neko'],
    academic: ['ui.export.style.academic', 'Academic'],
    compact: ['ui.export.style.compact', 'Compact'],
  };
  const pair = labels[normalized] || labels.neko;
  return text(props, pair[0], pair[1]);
}

export function formatError(error: unknown) {
  return error instanceof Error ? error.message : pluginErrorMessage(error);
}
