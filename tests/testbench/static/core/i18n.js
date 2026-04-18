/**
 * i18n.js — 文案字典 + 轻量读取 API.
 *
 * 当前只定义 `zh-CN` 字典. Settings → UI 预留语言切换位 (P04 实装),
 * 目前 `setLocale` 只支持 `zh-CN`.
 *
 * 约定:
 *   - key 采用点号命名空间:  `topbar.session.new` / `workspace.chat.title`
 *   - 未命中的 key 返回 key 本身并在 console 发 warn, 便于开发期发现遗漏
 *   - hydrate(root) 扫描 `[data-i18n]` / `[data-i18n-title]` / `[data-i18n-placeholder]`
 *     属性并回填文案, 无需在每个模块手写 textContent 赋值
 */

export const I18N = {
  'zh-CN': {
    app: {
      name: 'N.E.K.O. Testbench',
      tagline: '独立测试生态',
    },
    topbar: {
      session: {
        label: '会话',
        none: '(未建会话)',
        new: '新建会话',
        delete: '销毁当前会话',
        load: '加载存档…',
        save: '保存',
        save_as: '另存为…',
        import: '导入 JSON…',
        restore_autosave: '恢复自动保存…',
        not_implemented: '该功能将在 P21 后实装',
      },
      stage: {
        label: '阶段',
        chip_placeholder: '未启用',
        not_implemented: 'Stage Coach 将在 P14 实装',
      },
      timeline: {
        label: '时间轴',
        chip_placeholder: '无快照',
        not_implemented: '快照时间线将在 P18 实装',
      },
      error_badge: {
        title_none: '最近错误 (0)',
        title_some: count => `最近错误 (${count})`,
        empty: '暂无错误',
        view_all: '查看全部',
      },
      menu: {
        label: '菜单',
        export: '导出…',
        reset: '重置…',
        about: '关于',
        diagnostics: '打开诊断',
        settings: '打开设置',
      },
    },
    tabs: {
      setup: 'Setup 准备',
      chat: 'Chat 对话',
      evaluation: 'Evaluation 评分',
      diagnostics: 'Diagnostics 诊断',
      settings: 'Settings 设置',
    },
    workspace: {
      setup: {
        title: 'Setup 准备',
        placeholder_heading: '测试环境准备',
        placeholder_body:
          '此 workspace 用于配置角色 / 记忆 / 虚拟时钟 / 从真实角色导入. '
          + '具体子页在后续阶段注入.',
        todo_list: [
          { tag: 'P05', text: 'Persona + Import 子页 (人设表单 + 一键从真实角色拷贝)' },
          { tag: 'P06', text: 'Virtual Clock 子页 (bootstrap / live cursor / per-turn default)' },
          { tag: 'P07', text: 'Memory 四子页 (Recent / Facts / Reflections / Persona) 读写' },
          { tag: 'P10', text: 'Memory 操作触发 + 预览 drawer' },
        ],
      },
      chat: {
        title: 'Chat 对话',
        placeholder_heading: '测试主战场',
        placeholder_body:
          '对话流 + 四模式 Composer (Manual / SimUser / Scripted / Auto-Dialog) + '
          + '右侧 Prompt Preview 双视图. 在后续阶段填入.',
        todo_list: [
          { tag: 'P08', text: 'Prompt Preview 面板 (Structured / Raw wire)' },
          { tag: 'P09', text: '消息流 + 手动 Send + SSE' },
          { tag: 'P11', text: 'SimUser 模式' },
          { tag: 'P12', text: 'Scripted 模式 + 模板下拉' },
          { tag: 'P13', text: 'Auto-Dialog 模式 + 进度横幅' },
        ],
      },
      evaluation: {
        title: 'Evaluation 评分',
        placeholder_heading: '评分中心',
        placeholder_body:
          '四个子页: Run / Results / Aggregate / Schemas. ScoringSchema 作为一等公民.',
        todo_list: [
          { tag: 'P15', text: 'Schemas 子页 + 内置三套 schema' },
          { tag: 'P16', text: 'Run 子页 + 四类 Judger' },
          { tag: 'P17', text: 'Results + Aggregate 子页 + 导出报告' },
        ],
      },
      diagnostics: {
        title: 'Diagnostics 诊断',
        placeholder_heading: '运维诊断',
        placeholder_body:
          '出问题时才来. 包含 Logs / Errors / Snapshots / Paths / Reset 五个子页.',
        todo_list: [
          { tag: 'P19', text: '全局异常中间件 + Errors/Logs 子页' },
          { tag: 'P20', text: 'Paths / Snapshots / Reset 子页' },
        ],
      },
      settings: {
        title: 'Settings 设置',
        placeholder_heading: '集中配置',
        placeholder_body:
          '四组模型配置 (chat/simuser/judge/memory) + API Keys 状态 + '
          + 'Providers 只读 + UI 偏好 + About.',
        todo_list: [
          { tag: 'P04', text: 'Models / API Keys / Providers / UI / About 五个子页' },
        ],
      },
    },
    collapsible: {
      expand_all: '展开全部',
      collapse_all: '折叠全部',
      copy: '复制',
      copy_ok: '已复制',
      copy_fail: '复制失败',
      collapse: '折叠',
      length_chars: n => `${n} 字符`,
    },
    toast: {
      close: '关闭',
      dismiss_all: '清除全部',
    },
    session: {
      created: name => `会话已创建: ${name}`,
      destroyed: '会话已销毁',
      no_active: '当前无活跃会话',
      create_failed: '创建会话失败',
      destroy_failed: '销毁会话失败',
      confirm_destroy: '确认销毁当前会话? 沙盒目录将被清空.',
    },
    errors: {
      network: '网络请求失败',
      server: code => `服务端错误 (HTTP ${code})`,
      unknown: '未知错误',
    },
    common: {
      ok: '确定',
      cancel: '取消',
      close: '关闭',
      loading: '加载中…',
      not_implemented: '尚未实装',
    },
  },
};

let _locale = 'zh-CN';

/** 切换当前语言 (P04 接入 UI; 目前仅接受 zh-CN). */
export function setLocale(locale) {
  if (!I18N[locale]) {
    console.warn(`[i18n] unsupported locale: ${locale}, keeping ${_locale}`);
    return;
  }
  _locale = locale;
}

export function getLocale() {
  return _locale;
}

/**
 * 按点号 key 读取文案. 支持值为函数时直接调用.
 *
 * @param {string} key   `topbar.session.new`
 * @param {...any} args  若字典中的值是函数, 透传这些参数
 * @returns {string}
 */
export function i18n(key, ...args) {
  const dict = I18N[_locale];
  const parts = key.split('.');
  let node = dict;
  for (const p of parts) {
    if (node && typeof node === 'object' && p in node) {
      node = node[p];
    } else {
      console.warn(`[i18n] missing key: ${key}`);
      return key;
    }
  }
  if (typeof node === 'function') {
    try {
      return node(...args);
    } catch (err) {
      console.warn(`[i18n] formatter ${key} threw:`, err);
      return key;
    }
  }
  return node;
}

/** 读对象/数组原值 (供渲染 todo list 等结构化文案). */
export function i18nRaw(key) {
  const dict = I18N[_locale];
  const parts = key.split('.');
  let node = dict;
  for (const p of parts) {
    if (node && typeof node === 'object' && p in node) {
      node = node[p];
    } else {
      console.warn(`[i18n] missing key: ${key}`);
      return null;
    }
  }
  return node;
}

/**
 * 扫描 DOM 节点, 回填 `data-i18n` / `data-i18n-title` / `data-i18n-placeholder`
 * 属性. 同一节点可以用多个属性.
 */
export function hydrateI18n(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) {
    el.textContent = i18n(el.dataset.i18n);
  }
  for (const el of root.querySelectorAll('[data-i18n-title]')) {
    el.title = i18n(el.dataset.i18nTitle);
  }
  for (const el of root.querySelectorAll('[data-i18n-placeholder]')) {
    el.placeholder = i18n(el.dataset.i18nPlaceholder);
  }
  for (const el of root.querySelectorAll('[data-i18n-aria-label]')) {
    el.setAttribute('aria-label', i18n(el.dataset.i18nAriaLabel));
  }
}
