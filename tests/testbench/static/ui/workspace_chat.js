/**
 * workspace_chat.js — Chat workspace (P09 完整).
 *
 * 两栏 grid:
 *   - 左 .chat-main:  message stream (上) + composer (下), flex 竖向.
 *   - 右 .chat-sidebar: Prompt Preview 面板.
 *
 * 跨组件通信:
 *   - composer → message_stream: 直接拿 stream handle 调 beginAssistantStream /
 *     appendIncomingMessage, 流式 delta 直接写同一个 DOM 节点.
 *   - composer / message_stream → preview_panel: 触发 `chat:messages_changed`,
 *     preview_panel 打 dirty; 切回 Chat workspace 时 active_workspace:change
 *     的 listener 再 refresh (200ms 防抖).
 *   - workspace 懒挂载不卸载, 所以 stream / composer 的 session:change 订阅
 *     会伴随整个前端生命周期 — 没有泄漏风险, 因为 state.js 的 listener Set
 *     是模块级的, 卸载 workspace 也只是停渲染.
 */

import { store, on } from '../core/state.js';
import { el } from './_dom.js';
import { mountPreviewPanel } from './chat/preview_panel.js';
import { mountMessageStream } from './chat/message_stream.js';
import { mountComposer } from './chat/composer.js';

let previewHandle = null;
let streamHandle = null;
let composerHandle = null;
let activeWorkspaceSubscribed = false;
let chatMessagesChangedSubscribed = false;
let lastRefreshAt = 0;

export function mountChatWorkspace(host) {
  host.innerHTML = '';
  host.classList.add('chat-layout');

  // ── 左: message stream + composer ──────────────────────────────
  const leftPane = el('div', { className: 'chat-main' });
  const streamHost = el('div', { className: 'chat-stream-host' });
  const composerHost = el('div', { className: 'chat-composer-host' });
  leftPane.append(streamHost, composerHost);
  host.append(leftPane);

  try { streamHandle?.destroy?.(); } catch (_) { /* ignore */ }
  try { composerHandle?.destroy?.(); } catch (_) { /* ignore */ }
  streamHandle = mountMessageStream(streamHost);
  composerHandle = mountComposer(composerHost, { stream: streamHandle });

  // ── 右: Prompt Preview ─────────────────────────────────────────
  const rightPane = el('aside', { className: 'chat-sidebar' });
  host.append(rightPane);
  try { previewHandle?.destroy?.(); } catch (_) { /* ignore */ }
  previewHandle = mountPreviewPanel(rightPane);

  // 切回 Chat 时自动拉一次 preview (app.js 只会首次挂载; 之后完全靠事件驱动).
  if (!activeWorkspaceSubscribed) {
    on('active_workspace:change', (id) => {
      if (id !== 'chat') return;
      if (!previewHandle) return;
      if (!store.session?.id) return;
      const now = Date.now();
      if (now - lastRefreshAt < 200) return;
      lastRefreshAt = now;
      previewHandle.refresh();
    });
    activeWorkspaceSubscribed = true;
  }

  // 消息列表变更 → 自动刷新 preview. `chat:messages_changed` 只在写入动作完全
  // 落盘后才 emit (composer 在 SSE `done` 才 emit; message_stream 在 edit/delete/
  // truncate/patch_timestamp 成功后才 emit; inject 在 POST 成功后才 emit), 因此
  // 不会跟流式 delta 竞争. 200ms 防抖保护连续编辑 (比如拖着改时间戳) 的场景.
  if (!chatMessagesChangedSubscribed) {
    let refreshTimer = null;
    on('chat:messages_changed', () => {
      if (!previewHandle) return;
      previewHandle.markDirty?.();
      if (!store.session?.id) return;
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        previewHandle?.refresh?.();
      }, 200);
    });
    chatMessagesChangedSubscribed = true;
  }
}
