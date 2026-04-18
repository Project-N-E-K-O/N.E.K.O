/**
 * workspace_diagnostics.js — Diagnostics workspace 入口.
 *
 * P03 占位. 后续 (P19/P20) 会挂 Logs / Errors / Snapshots / Paths / Reset 五子页.
 */

import { renderPlaceholderWorkspace } from './workspace_placeholder.js';

export function mountDiagnosticsWorkspace(host) {
  renderPlaceholderWorkspace(host, 'diagnostics');
}
