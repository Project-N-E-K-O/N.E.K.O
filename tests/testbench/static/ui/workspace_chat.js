/**
 * workspace_chat.js — Chat workspace 入口.
 *
 * P03 占位. 后续 (P08/P09/P11/P12/P13) 会在此挂:
 *   - 消息流 + 每条 [⋯] 菜单
 *   - 四模式 Composer (Manual / SimUser / Scripted / Auto-Dialog)
 *   - 右侧 Prompt Preview 双视图面板
 */

import { renderPlaceholderWorkspace } from './workspace_placeholder.js';

export function mountChatWorkspace(host) {
  renderPlaceholderWorkspace(host, 'chat');
}
