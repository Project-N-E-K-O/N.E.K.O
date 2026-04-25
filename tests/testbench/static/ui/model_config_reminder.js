/**
 * model_config_reminder.js — 启动时提醒测试人员去配置 AI 模型.
 *
 * 两种显示模式:
 *   1. **欢迎横幅** (welcome): 本浏览器 × 本次服务启动组合**首次看到**时展
 *      示, 无条件显示, 引导去 Settings → Models 给新会话的 chat/simuser/
 *      judge/memory 四组模型配置. "首次"的判据是 LS 里存的 boot_id 和当前
 *      后端 `/healthz.boot_id` 不一致 (或 LS 空) — 所以**服务每次重启都会
 *      再出现一次** welcome (刻意如此: 测试人员常随服务重启开始新一轮测试,
 *      每次提醒一次"别忘了配模型"可接受的繁琐).
 *   2. **告警横幅** (warn): 任何时候只要**没有任何可用 provider** (既不是
 *      free_version 也未配 api_key) 就显示, 引导去 Settings → API Keys.
 *      不受 boot_id 约束 — 只要 provider 没配好就持续提醒, 配好了才消失.
 *
 * 两种模式都可以用 × 关闭. dismiss 也挂在 boot_id 上 (sessionStorage 存当前
 * boot_id), 所以同一次服务运行期内刷新仍保持关闭, 服务重启后自动失效.
 *
 * 触发重新检查的时机:
 *   - boot 之后一次;
 *   - 用户离开 settings workspace 时 (刚刚可能刚配好 → 关掉横幅);
 *   - session:change (换会话后不影响 provider 状态, 但保留钩子).
 *
 * 本模块**不影响后续流程** — 横幅只是提示, 用户坚持不配也能继续 (只是一调
 * LLM 就会报错, 错误走常规 toast). 这是刻意的选择: 不做硬阻塞, 保留测试人员
 * 离线探索 UI 的自由度.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { store, set, on, emit } from '../core/state.js';
import { el } from './_dom.js';

// sessionStorage: 记录"点 × 关闭时对应的 boot_id". 同 boot 内刷新仍视为已关;
// 服务重启后 boot_id 变化就自动失效 (符合"每次重启再提醒一次"的用户需求).
// 从布尔升级为 boot_id 字符串.
const DISMISS_SS = 'testbench:model_reminder:dismissed_at_boot_id';
// 旧字段: 布尔 "已见过". 保留读取做降级兼容 (healthz 挂时用), 不再写入.
const LEGACY_FIRST_LAUNCH_LS = 'testbench:model_reminder:first_launch_seen';
// localStorage: 记录"上次看过 welcome 时对应的后端 boot_id". 若跟当前 /healthz
// 的 boot_id 不一致 → 服务重启过, 重新显示 welcome.
const WELCOME_SEEN_BOOT_LS = 'testbench:model_reminder:welcome_seen_boot_id';

let hostEl = null;
let inflight = null;
let currentBootId = null;  // 本次进程的 boot_id, refresh 时从 /healthz 拉.

/**
 * 挂载横幅槽位. host 是 body 或 #app 内的容器, 我们在它里面插一个 div,
 * 位置由 CSS (.workspace-host 之前) 决定. 这里选择插到 #app 的第一个子元素
 * 之后 (即 header 之后), 这样总在 topbar 下方 tabbar 上方.
 */
export function mountModelConfigReminder() {
  const app = document.getElementById('app');
  const tabbar = document.getElementById('tabbar');
  if (!app || !tabbar) return;

  hostEl = el('div', { className: 'model-reminder-slot', 'aria-live': 'polite' });
  app.insertBefore(hostEl, tabbar);

  refresh();

  // 离开 settings workspace 时重新 check — 用户可能刚 reload 过 key.
  let prevWorkspace = store.active_workspace;
  on('active_workspace:change', (id) => {
    if (prevWorkspace === 'settings' && id !== 'settings') {
      refresh();
    }
    prevWorkspace = id;
  });

  // 换会话不影响 provider 状态, 但保留钩子.
  on('session:change', () => refresh());
}

async function refresh() {
  if (inflight) return inflight;
  inflight = (async () => {
    // errors.server 默认会 toast, 但这里属于 boot 时的健康检查, 失败就静默不
    // 显示横幅 (避免和其他错误 toast 叠加把用户淹没).
    // 并行拉 providers + healthz(boot_id). healthz 失败也不阻塞 — 只是 welcome
    // 会降级成"LS 非空就不再出", 和旧行为一致.
    const [providersRes, healthRes] = await Promise.all([
      api.get('/api/config/providers', { expectedStatuses: [500, 502, 503, 504] }),
      api.get('/healthz', { expectedStatuses: [404, 500, 502, 503, 504] }),
    ]);
    if (healthRes.ok && typeof healthRes.data?.boot_id === 'string') {
      currentBootId = healthRes.data.boot_id;
    } else {
      currentBootId = null;
    }
    if (!providersRes.ok) {
      render(null);
      return;
    }
    const providers = providersRes.data?.providers || [];
    render(providers);
  })().finally(() => { inflight = null; });
  return inflight;
}

function render(providers) {
  if (!hostEl) return;
  hostEl.innerHTML = '';

  // 拉不到 providers 列表 (后端错误 / 离线): 不渲染横幅, 让其他 toast 处理.
  if (!Array.isArray(providers)) return;

  // 本次 boot 是否已点过 ×. sessionStorage 存的是 dismiss 时的 boot_id;
  // 服务重启后 boot_id 变了就自动失效. 拿不到 currentBootId (healthz 挂)
  // 时降级到"只要有记录就算 dismiss"的布尔语义.
  const dismissedAtBoot = (() => {
    try { return sessionStorage.getItem(DISMISS_SS); } catch (_) { return null; }
  })();
  const dismissed = currentBootId
    ? dismissedAtBoot === currentBootId
    : !!dismissedAtBoot;
  if (dismissed) return;

  // "可用 provider" = free_version (无需 key) 或 api_key 已配置.
  const usable = providers.some((p) => p.is_free_version || p.api_key_configured);

  // welcome "首次判定" = 上次 seen 时记录的 boot_id 跟当前后端 boot_id 不一致.
  // 特殊情况:
  //   - LS 里没值 (全新浏览器, 或手动清过) → 显示.
  //   - currentBootId 为 null (healthz 拉失败) → 降级用 legacy flag 判定; 仍
  //     为空视为首次. 这样离线或后端半残场景不会把 welcome 锁死.
  const seenBootId = (() => {
    try { return localStorage.getItem(WELCOME_SEEN_BOOT_LS); } catch (_) { return null; }
  })();
  const legacySeen = (() => {
    try { return localStorage.getItem(LEGACY_FIRST_LAUNCH_LS) === '1'; } catch (_) { return false; }
  })();
  let firstLaunch;
  if (currentBootId) {
    firstLaunch = seenBootId !== currentBootId;
  } else {
    firstLaunch = !seenBootId && !legacySeen;
  }

  // 选模式: 无可用 provider → warn (高优先); 否则首次启动 → welcome; 均不满足 → 不渲染.
  let mode = null;
  if (!usable) mode = 'warn';
  else if (firstLaunch) mode = 'welcome';
  if (!mode) return;

  // NOTE: 故意**不**在 render 时立刻写 seen flag. 写入时机放到用户显式交互
  // (点 × 或点跳转按钮) 的回调里 — 这样任何"渲染成功但用户没看到"的情况
  // (比如被别的 UI 遮挡 / CSS 翻车 / 用户刚看一眼就关标签页) 下次刷新都还能
  // 再看见. 另外, **seen flag 现在记的是 boot_id 不是布尔值**, 所以服务重启
  // 后即使用户上次点过"我知道了", 这次还是会重新显示一次.

  const banner = el('div', {
    className: `model-reminder-banner mode-${mode}`,
    role: mode === 'warn' ? 'alert' : 'status',
  });

  const iconEl = el('span', { className: 'model-reminder-icon', 'aria-hidden': 'true' },
    mode === 'warn' ? '⚠️' : '👋');

  const body = el('div', { className: 'model-reminder-body' });
  const textNode = el('div', { className: 'model-reminder-text' });
  // 正文里用反引号包的段 → <code>, 其余走普通文本.
  const rawBody = i18n(`model_reminder.${mode}.body`);
  const parts = rawBody.split('`');
  parts.forEach((seg, i) => {
    if (i % 2 === 1) textNode.append(el('code', {}, seg));
    else if (seg) textNode.append(document.createTextNode(seg));
  });
  const reminderTitleText = i18n(`model_reminder.${mode}.title`);
  body.append(
    el('div', {
      className: 'model-reminder-title u-wrap-anywhere',
      title: reminderTitleText,
    }, reminderTitleText),
    textNode,
  );

  const actions = el('div', { className: 'model-reminder-actions' });

  // 用户交互时标记 "本次 boot 已见过"; welcome 写 boot_id, warn 不标 (provider
  // 没配好就继续提醒). 写的是 boot_id 而非布尔 → 下次服务重启自动失效, 实现
  // "每次重启再提醒一次"的语义.
  function markSeenIfWelcome() {
    if (mode !== 'welcome') return;
    if (!currentBootId) return;  // 拿不到 boot_id 就不写, 下次刷新再试.
    try { localStorage.setItem(WELCOME_SEEN_BOOT_LS, currentBootId); } catch (_) {}
    try { localStorage.removeItem(LEGACY_FIRST_LAUNCH_LS); } catch (_) {}
  }

  const gotoBtn = el('button', {
    type: 'button',
    className: 'btn primary',
    onClick: () => {
      markSeenIfWelcome();
      set('active_workspace', 'settings');
      // warn 模式跳 API Keys (先让全局 key 可用); welcome 模式跳 Models (配置当前会话).
      emit('settings:goto_page', mode === 'warn' ? 'api_keys' : 'models');
      // P24 §12.3.E #15: auto-dismiss on [去配置] click. Same rationale
      // as session_restore_banner: jumping to Settings is the user's
      // acknowledgement. Record the dismiss-at-boot marker so the
      // banner stays dismissed for the rest of this boot session; the
      // real-time refresh loop will bring it back if the user somehow
      // ends up without model config again.
      const bootMarker = currentBootId || 'pre-boot';
      try { sessionStorage.setItem(DISMISS_SS, bootMarker); }
      catch { /* private-mode / quota — banner will simply stay visible */ }
      if (hostEl) hostEl.innerHTML = '';
    },
  }, i18n(`model_reminder.${mode}.goto_btn`));

  const dismissBtn = el('button', {
    type: 'button',
    className: 'model-reminder-dismiss',
    title: i18n('model_reminder.dismiss_hint'),
    'aria-label': i18n('model_reminder.dismiss_hint'),
    onClick: () => {
      markSeenIfWelcome();
      // 记 dismiss 时的 boot_id (拿不到时退回一个哨兵字符串, 同 boot 内仍有效,
      // 重启后 currentBootId 换成新值自然失配).
      const marker = currentBootId || 'unknown';
      try { sessionStorage.setItem(DISMISS_SS, marker); } catch (_) {}
      hostEl.innerHTML = '';
    },
  }, '×');

  actions.append(gotoBtn, dismissBtn);

  banner.append(iconEl, body, actions);
  hostEl.append(banner);
}
