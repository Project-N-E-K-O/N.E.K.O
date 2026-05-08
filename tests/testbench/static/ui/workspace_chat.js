/**
 * workspace_chat.js — Chat workspace (P09 完整, P25 Day 2 调整).
 *
 * 两栏 grid:
 *   - 左 .chat-main (grid 四行):
 *       auto banner (顶, 空闲 display:none) → message stream (1fr) →
 *       composer (auto) → external events 折叠面板 (auto, 默认收起).
 *     external events 面板被挂在左栏, 是 "输入性质" 的 tester-facing 控件
 *     (和 composer 一样是"把东西送进 session"), 默认折叠后 summary 高度
 *     ~32px, 不挤压 stream 视觉 — 展开由 tester 主动点 summary.
 *   - 右 .chat-sidebar: Prompt Preview 面板 (观察性质, 不是输入).
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
import { mountAutoBanner } from './chat/auto_banner.js';
import { mountExternalEventsPanel } from './chat/external_events_panel.js';

let previewHandle = null;
let streamHandle = null;
let composerHandle = null;
let autoBannerHandle = null;
let externalEventsHandle = null;
let activeWorkspaceSubscribed = false;
let chatMessagesChangedSubscribed = false;
let lastRefreshAt = 0;

export function mountChatWorkspace(host) {
  host.innerHTML = '';
  host.classList.add('chat-layout');

  // ── 左: auto banner + message stream + composer + external events ───
  // P13: banner 位于 leftPane 最上, sticky 贴在 .chat-main 顶部. 空闲时
  // display:none, 不占视觉空间; 有活跃 auto_state 时才展开.
  // P25 Day 2 (调整后): external events 面板放在 composer **下方** — 和
  // composer 一样是 "把东西送进 session" 的输入性 UI, 右侧 sidebar 是观察
  // 性 preview, 两者不该混. 面板默认折叠 (<details open=false>), summary
  // 行 ~32px 不会挤压 stream; 展开后 .chat-main 的 grid 第 4 行 auto 自
  // 然撑开面板, stream 的 1fr 被压缩但内部仍自滚.
  // 顺序是 banner → stream → composer → external events, 与 PLAN
  // §Chat workspace 的"对话流上方插入进度横幅"约定一致.
  const leftPane = el('div', { className: 'chat-main' });
  const autoBannerHost = el('div', { className: 'chat-auto-banner-host' });
  const streamHost = el('div', { className: 'chat-stream-host' });
  const composerHost = el('div', { className: 'chat-composer-host' });
  const externalEventsHost = el('div', {
    className: 'chat-main-external-events-host',
  });
  leftPane.append(autoBannerHost, streamHost, composerHost, externalEventsHost);
  host.append(leftPane);

  try { streamHandle?.destroy?.(); } catch (_) { /* ignore */ }
  try { composerHandle?.destroy?.(); } catch (_) { /* ignore */ }
  try { autoBannerHandle?.destroy?.(); } catch (_) { /* ignore */ }
  try { externalEventsHandle?.destroy?.(); } catch (_) { /* ignore */ }
  streamHandle = mountMessageStream(streamHost);
  autoBannerHandle = mountAutoBanner(autoBannerHost, { stream: streamHandle });
  composerHandle = mountComposer(composerHost, {
    stream: streamHandle,
    autoBanner: autoBannerHandle,
  });
  externalEventsHandle = mountExternalEventsPanel(externalEventsHost);

  // ── 右: Prompt Preview (观察性质, 唯一面板) ──────────────────
  const rightPane = el('aside', { className: 'chat-sidebar' });
  host.append(rightPane);

  const previewHost = el('div', { className: 'chat-sidebar-preview-host' });
  rightPane.append(previewHost);

  try { previewHandle?.destroy?.(); } catch (_) { /* ignore */ }
  previewHandle = mountPreviewPanel(previewHost);

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
