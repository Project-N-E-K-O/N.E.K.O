/**
 * workspace_setup.js — Setup workspace 入口.
 *
 * P03 只渲染占位, 后续阶段 (P05/P06/P07/P10) 会替换 `mount()` 内部:
 *   - 左侧 nav: Persona / Memory (Recent/Facts/Reflections/Persona) / Virtual Clock / Import
 *   - 右侧主区: 根据 nav 选中的子页切换
 */

import { renderPlaceholderWorkspace } from './workspace_placeholder.js';

export function mountSetupWorkspace(host) {
  renderPlaceholderWorkspace(host, 'setup');
}
