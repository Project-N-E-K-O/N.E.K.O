/**
 * Theme Studio panel script (static panel: no build step, no framework).
 *
 * Built-in edition: the panel and the theme service share an origin (both run on
 * the N.E.K.O main server), so there is neither the plugin host's /runs queue nor
 * a CSRF bootstrap:
 *   POST /api/theme_studio/run/{entry_id}   one request returns the result
 *   GET  /api/theme_studio/theme.json       current palette (also polled by external sync)
 * Every URL is built from API_BASE; the page itself is served at /theme_studio.
 */

/* ============================================================================
 * 0. i18n
 *
 * The panel uses the host i18n (static/i18n-i18next.js): static markup carries
 * data-i18n* attributes, runtime strings go through tsT(). Every key lives
 * under the top-level "themeStudio" namespace of static/locales/*.json.
 * The Chinese text passed as `fallback` is what the panel shows when the key
 * or the i18n runtime is missing, so behaviour without translations is
 * unchanged. Placeholders use i18next syntax: {{name}}.
 * ==========================================================================*/

var TS_I18N_NS = 'themeStudio.';

function tsInterpolate(text, params) {
  if (!params) return String(text);
  return String(text).replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, function (match, name) {
    return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match;
  });
}

/**
 * Translate a panel string.
 * @param {string} key      key below "themeStudio." (e.g. "toast.applied")
 * @param {string} fallback Chinese source text, used when no translation exists
 * @param {Object=} params  interpolation values for {{name}} placeholders
 */
function tsT(key, fallback, params) {
  var full = TS_I18N_NS + key;
  try {
    if (typeof window.t === 'function' && window.i18n && window.i18n.isInitialized !== false) {
      var text = window.t(full, params || {});
      if (typeof text === 'string' && text && text !== full) return text;
    }
  } catch (error) {
    // fall through to the source text
  }
  return tsInterpolate(fallback == null ? key : fallback, params);
}
window.tsT = tsT;

/**
 * Run `callback` once the host i18n has loaded the language bundle (it fires
 * "localechange" after initialising), or after a short timeout so a failed or
 * slow i18n load never blocks the panel.
 * If the timeout won and the bundle arrives later, `onLate` (optional) runs once
 * so the caller can refresh text it generated with the fallback strings.
 */
function whenI18nReady(callback, onLate) {
  var done = false;
  var timedOut = false;
  function onLocale() {
    if (!done) { done = true; callback(); return; }
    if (timedOut && typeof onLate === 'function') {
      timedOut = false;
      window.removeEventListener('localechange', onLocale);
      onLate();
    }
  }
  if (window.i18n && window.i18n.isInitialized) { done = true; callback(); return; }
  window.addEventListener('localechange', onLocale);
  setTimeout(function () {
    if (done) {
      window.removeEventListener('localechange', onLocale);
      return;
    }
    done = true;
    timedOut = true;
    if (typeof onLate !== 'function') window.removeEventListener('localechange', onLocale);
    callback();
  }, 2500);
}
window.whenI18nReady = whenI18nReady;

/** Stable id of a catalog group for persisted UI state (not its display name). */
function groupStateKey(group) {
  return (group && (group.stateKey || group.group)) || '';
}

/**
 * Translate the variable catalog in place (called once before the editor is
 * built). Keys are derived from data, so catalog entries need no extra fields:
 *   group  -> themeStudio.catalog.groups.<groupKey>
 *   label  -> themeStudio.catalog.vars.<var name without "--nts-">.label
 *   hint   -> themeStudio.catalog.vars.<var name without "--nts-">.hint
 * Options of select-type items: themeStudio.catalog.vars.<name>.options.<value>
 */
function localizeCatalog() {
  if (typeof CATALOG === 'undefined' || !CATALOG || !CATALOG.forEach) return;
  CATALOG.forEach(function (group) {
    // Collapsed-group state is persisted by group; keep it keyed by the original
    // (untranslated) name so saved state survives language changes and matches
    // what earlier versions stored.
    if (!group.stateKey) group.stateKey = group.group;
    if (group.groupKey) {
      group.group = tsT('catalog.groups.' + group.groupKey, group.group);
    }
    (group.items || []).forEach(function (item) {
      var base = 'catalog.vars.' + String(item.key || '').replace(/^--nts-/, '');
      if (item.label) item.label = tsT(base + '.label', item.label);
      if (item.hint) item.hint = tsT(base + '.hint', item.hint);
      if (Array.isArray(item.options)) {
        item.options.forEach(function (option) {
          if (option && typeof option === 'object' && option.label != null) {
            option.label = tsT(base + '.options.' + String(option.value), option.label);
          }
        });
      }
    });
  });
}

/* ============================================================================
 * 1. Variable catalog — the authoritative list from THEME_CONTRACT.md §3
 * ==========================================================================*/

var CATALOG = [
  {
    group: '聊天气泡',
    groupKey: 'chatBubbles',
    items: [
      { key: '--nts-user-bubble-bg', label: '用户气泡背景', type: 'color', native: '#44b7fe' },
      { key: '--nts-user-bubble-text', label: '用户气泡文字', type: 'color', native: '#ffffff' },
      { key: '--nts-user-bubble-border', label: '用户气泡描边', type: 'color', native: 'transparent' },
      { key: '--nts-ai-bubble-bg', label: 'AI 气泡背景', type: 'color', native: '#ffffff', darkNative: 'rgba(42,123,196,0.18)' },
      { key: '--nts-ai-bubble-text', label: 'AI 气泡文字', type: 'color', native: '#24445a', darkNative: '#e0e0e0' },
      { key: '--nts-ai-bubble-border', label: 'AI 气泡描边', type: 'color', native: 'transparent' },
      {
        key: '--nts-code-bg', label: '代码块背景', type: 'color',
        native: 'rgba(91, 111, 145, 0.12)', darkNative: 'rgba(100, 120, 160, 0.18)',
        hint: '行内代码与代码块的底色（.message-markdown-inline-code / .message-markdown-pre）；代码块本体原生没有底色，只有行内代码有；留空即保持原生配色'
      },
      {
        key: '--nts-code-text', label: '代码块文字', type: 'color',
        native: '#22324d', darkNative: '#c0cce0',
        hint: '行内代码与代码块的文字色；原生行内代码 #22324d、代码块 #1f2c42（暗色两者都是 #c0cce0）；留空即保持原生配色'
      },
      {
        key: '--nts-image-bg', label: '图片块背景', type: 'color',
        native: 'transparent', darkNative: 'transparent',
        hint: '消息里图片包裹层 .message-block-image 的底色，垫在图片下方；留空即保持原生（透明，只有图片本身透明或加载失败时才看得见）'
      },
      {
        key: '--nts-tool-bubble-bg', label: '工具/系统气泡背景', type: 'color',
        native: 'linear-gradient(180deg, #f8fafc, #edf2f8)', darkNative: 'linear-gradient(180deg, #232330, #1e1e2a)',
        hint: '工具气泡与系统气泡共用的底色（.message-bubble-tool / .message-bubble-system）；原生工具气泡是渐变、系统气泡是半透明灰（亮 rgba(131,148,175,0.14) / 暗 rgba(80,90,110,0.2)），填写后两者会被拉平成同一个纯色；留空即保持原生'
      },
      {
        key: '--nts-tool-bubble-text', label: '工具/系统气泡文字', type: 'color',
        native: '#2b3a50', darkNative: '#b8bcc4',
        hint: '工具气泡与系统气泡的文字色；原生工具 #2b3a50、系统 #607086（暗色 #b8bcc4 / #9aa0ac）；留空即保持原生配色'
      },
      {
        key: '--nts-status-bg', label: '状态块背景', type: 'color',
        native: 'rgba(70, 129, 227, 0.12)', darkNative: 'rgba(68, 130, 230, 0.15)',
        hint: '消息状态块的底色（.message-block-status 的 info / success / warning / error 四种 tone 共用这一个变量）；原生四种 tone 各不相同（亮 success rgba(48,161,100,0.12)、warning rgba(235,168,52,0.16)、error rgba(223,91,91,0.14)），填写后四种 tone 会被拉平成同一个颜色；留空即保持原生'
      },
      {
        key: '--nts-status-text', label: '状态块文字', type: 'color',
        native: '#28589d', darkNative: '#6fa8ff',
        hint: '消息状态块的文字色（四种 tone 共用这一个变量）；原生四种 tone 各不相同（亮 success #236845、warning #9a6008、error #a63a3a；暗色 #5ec790 / #e8b44a / #e07070），填写后四种 tone 会被拉平成同一个颜色；留空即保持原生'
      }
    ]
  },
  {
    group: '聊天窗口',
    groupKey: 'chatWindow',
    items: [
      { key: '--nts-chat-bg', label: '聊天窗口背景', type: 'color', native: 'transparent',
        hint: '紧凑胶囊形态下那条胶囊的底色由它决定（背板不再抢它）。展开后还影响 #react-chat-window-root 与 .chat-window。留空即保持原生' },
      { key: '--nts-chat-panel-bg', label: '聊天背板背景', type: 'color', native: 'transparent',
        hint: '消息列表背后那块底板的颜色：紧凑态涂 .compact-export-history-scroll，展开/全屏态涂 .message-list 与 .chat-body。留空即保持原生（透明）' },
      {
        key: '--nts-chat-bg-image', label: '聊天背景图片', type: 'image', native: 'none',
        hint: '给消息列表那块底板铺一张图片：设置后图片优先于「聊天背板背景」的纯色（纯色仍在图片下方兜底，图片透明处会透出来）。支持 png/jpg/webp/gif，单张上限 4 MB。合法值只有 none 或 url("…/backgrounds/<文件>")。留空即回原生（无图片）'
      },
      {
        key: '--nts-chat-bg-image-brightness', label: '背景图亮度', type: 'number', native: '1',
        hint: '把「聊天背景图片」整体压暗，避免图片跟正文抢视线。取值 0-1：1 = 原图亮度，0.6 = 压暗四成，0 = 全黑。只往下压、不会变亮（实现方式是一层黑色蒙版盖在图片上，正文完全不受影响——不是给容器加 filter，那样会把气泡和文字一起调暗）。只在设了「聊天背景图片」时生效；留空即保持原图'
      },
      {
        key: '--nts-chat-bg-fit', label: '背景图适应方式', type: 'select', native: 'cover',
        options: [
          { value: 'cover', label: '铺满（可能裁切）' },
          { value: 'contain', label: '整图可见（留底色）' }
        ],
        hint: '背景图怎么填进聊天区：铺满 = cover，图片铺满整块、比例不对时上下或左右会被裁掉（原生行为，多数壁纸适合）；整图可见 = contain，整张图缩到能看见全部、两侧或上下留出底色。只影响背景图，不动其他元素。留空即铺满'
      },
      {
        key: '--nts-chat-bg-position', label: '背景图位置', type: 'select', native: 'center',
        options: [
          { value: 'center', label: '居中' },
          { value: 'top', label: '顶部对齐' },
          { value: 'bottom', label: '底部对齐' },
          { value: 'left', label: '左侧对齐' },
          { value: 'right', label: '右侧对齐' }
        ],
        hint: '背景图放在聊天区的哪个位置：只在图片比例和容器不一致（或选了「整图可见」）时看得出来。居中 = 原生行为；顶部/底部对齐适合「画面主体在上/下半部」的壁纸。留空即居中'
      },
      {
        key: '--nts-chat-bg-repeat', label: '背景图平铺', type: 'select', native: 'no-repeat',
        options: [
          { value: 'no-repeat', label: '不平铺（单张）' },
          { value: 'repeat', label: '双向平铺' },
          { value: 'repeat-x', label: '横向平铺' },
          { value: 'repeat-y', label: '纵向平铺' }
        ],
        hint: '壁纸是否重复铺满：照片类壁纸保持「不平铺」（原生行为）；细纹理、格纹、图案类图片选平铺可以铺满整块而不被拉伸。平铺时「适应方式」不生效（每格按原尺寸重复）。留空即不平铺'
      },
      {
        key: '--nts-corner-style', label: '圆角风格', type: 'select', native: 'round',
        options: [
          { value: 'round', label: '普通圆角' },
          { value: 'continuous', label: '连续曲率（iOS 风格）' }
        ],
        hint: '圆角的几何形状：普通圆角 = 圆弧（原生行为）；连续曲率 = 超椭圆，转角没有「突然变直」的那一下，Apple 的设备圆角就是这种。需要较新的渲染内核（Chrome 139+ 的 corner-shape）；不支持时这条不生效、其它设置照常，也不会报错。留空即普通圆角'
      },
      {
        key: '--nts-chat-top-fade', label: '顶部渐隐', type: 'number', native: '1',
        hint: '聊天记录最上边那条渐隐，也就是背景图顶部那层「雾」的来源。取值 0-1：1 = 应用原生（顶部 28px 由透明渐入，图片顶部跟着淡出），0 = 渐隐彻底取消、图片顶边清晰，中间值线性过渡。注意关掉后滚到顶部的消息会硬切、不再淡出'
      },
      {
        key: '--nts-chat-panel-radius', label: '聊天背板圆角', type: 'length', native: '0px',
        hint: '消息列表那块底板的圆角，与「聊天背板背景」配套。用的是独立变量——「通用圆角」会连带改圆按钮和输入框，这里只圆背板。留空即保持原生直角'
      },
      { key: '--nts-topbar-bg', label: '顶栏背景', type: 'color', native: 'transparent' },
      { key: '--nts-topbar-text', label: '顶栏文字', type: 'color', native: 'inherit' },
      {
        key: '--nts-chat-blur', label: '毛玻璃模糊', type: 'blur', native: 'blur(18px)',
        hint: 'CSS 消费方是 backdrop-filter，因此必须写成 blur(18px) 形式；只填 18px 后端会自动补成 blur(18px)；单位只接受 px/rem/em/pt（不支持 % / vh / vw）'
      },
      {
        key: '--nts-bubble-veil', label: '聊天背景虚化', type: 'switch',
        // Display-only reference value; resolved lazily so it follows the current locale.
        get native() { return tsT('catalog.vars.bubble-veil.native', '开'); },
        hint: '气泡上那层「白纱 + 背景模糊」的总开关。关掉后气泡底完全透明、背景模糊归零，聊天背景图与底色会完整透出来（对比度更高，适合铺了背景图之后觉得画面发灰/看不清的情况）；打开则回到 N.E.K.O 原生观感。底层是 0-1 乘子，同时作用于白纱不透明度与模糊半径。'
      },
      {
        key: '--nts-chat-opacity', label: '聊天面不透明度', type: 'number', native: '1',
        hint: '取值 0-1。走桥接写成 --neko-chat-opacity-factor —— N.E.K.O 自带的透明度乘子：它乘的是各聊天表面底色的 alpha（原生写法形如 rgba(255,255,255,calc(.91 * 该系数))），所以是整体缩放聊天面板/窗口的透明度，不改任何单个颜色。留空即保持原生（系数 1 = 100%）。注意此项曾因应用侧加载器没有 number 值类型而静默丢弃、面板却报成功，现已修复'
      }
    ]
  },
  {
    group: '输入区',
    groupKey: 'composer',
    items: [
      { key: '--nts-input-bg', label: '输入区背景', type: 'color', native: 'rgba(40, 40, 40, 0.65)', darkNative: 'rgba(40,40,40,0.65)' },
      { key: '--nts-input-text', label: '输入文字', type: 'color', native: 'inherit', darkNative: '#e0e0e0' },
      { key: '--nts-input-border', label: '输入区描边', type: 'color', native: 'rgba(255, 255, 255, 0.06)', darkNative: 'rgba(255,255,255,0.06)' },
      { key: '--nts-input-placeholder', label: '占位文字', type: 'color', native: '#9aa7b2', darkNative: '#999999' },
      { key: '--nts-button-bg', label: '工具按钮背景', type: 'color', native: 'transparent', darkNative: '#2d2d2d' },
      { key: '--nts-button-text', label: '工具按钮文字', type: 'color', native: 'inherit' },
      { key: '--nts-button-hover-bg', label: '工具按钮悬停', type: 'color', native: '#44b7fe1a', darkNative: '#44b7fe26', hint: '只在鼠标悬停工具按钮时生效（.composer-tool-btn:hover），不影响按钮静止态（那由「工具按钮背景」管）；留空即保持原生悬停色（亮 #44b7fe1a / 暗 #44b7fe26）' },
      { key: '--nts-send-bg', label: '发送按钮背景', type: 'color', native: '#44b7fe', darkNative: '#2a7bc4' },
      { key: '--nts-send-text', label: '发送按钮文字', type: 'color', native: '#ffffff', darkNative: '#ffffff' }
    ]
  },
  {
    group: '面板 / 弹窗 / 菜单',
    groupKey: 'panels',
    items: [
      {
        key: '--nts-panel-bg', label: '面板背景', type: 'color', native: 'transparent',
        hint: '作用于输入区面板（.composer-panel）与悬浮任务面板（#agent-task-hud）；紧凑胶囊态下面板本身被应用压成透明，此时看「聊天背景」'
      },
      {
        key: '--nts-panel-text', label: '面板文字', type: 'color', native: '#333333', darkNative: '#e0e0e0',
        hint: '作用于悬浮任务面板、访问条等面板面的文字；输入框里的文字请用「输入文字」。紧凑胶囊的文字颜色不跟这里走（避免改面板文字时连胶囊文字一起变色）'
      },
      { key: '--nts-panel-border', label: '面板描边', type: 'color', native: 'rgba(0, 0, 0, 0.08)', darkNative: 'rgba(255,255,255,0.1)' },
      { key: '--nts-popover-bg', label: '弹出菜单背景', type: 'color', native: 'transparent' },
      { key: '--nts-popover-text', label: '弹出菜单文字', type: 'color', native: 'inherit' },
      { key: '--nts-popover-border', label: '弹出菜单描边', type: 'color', native: 'transparent' },
      { key: '--nts-modal-bg', label: '弹窗背景', type: 'color', native: '#ffffff', darkNative: '#2d2d2d' },
      { key: '--nts-modal-text', label: '弹窗文字', type: 'color', native: '#24445a', darkNative: '#e0e0e0' },
      {
        key: '--nts-accent-bg', label: '插件页强调底纹', type: 'color',
        native: 'rgba(42, 123, 196, 0.05)', darkNative: 'rgba(58, 159, 216, 0.1)',
        hint: '走桥接：写 #agent-plugin-content / #agent-plugin-views 上的 --neko-popup-accent-bg，即插件面板标签页的悬停底色与选中底色（#agent-content-tabs button:hover / [aria-selected="true"]）；留空即保持原生（面板自身兜底亮 rgba(42,123,196,.08) / 选中 .10，模式变量亮 rgba(42,123,196,0.05) / 暗 rgba(58,159,216,0.1)）'
      },
      {
        key: '--nts-panel-text-sub', label: '面板次级文字', type: 'color',
        native: '#666666', darkNative: '#b0b0b0',
        hint: '走桥接：写 #agent-plugin-content / #agent-plugin-views 上的 --neko-popup-text-sub，消费方是插件面板顶部的标签页文字与关闭按钮（#agent-content-tabs button / #agent-plugin-close）；留空即保持原生（面板自身还有一层兜底 #667582，亮 #666666 / 暗 #b0b0b0）'
      },
      {
        key: '--nts-overlay-bg', label: '弹窗遮罩背景', type: 'color',
        native: 'transparent', darkNative: 'rgba(0, 0, 0, 0.65)',
        hint: '弹窗背后的整屏遮罩 .modal-overlay 底色；原生亮色模式基本不铺遮罩（常见组件给的 rgba(0,0,0,0.5) 只在个别弹窗生效），暗色是 rgba(0,0,0,0.65)；小剧场/串门这两类透明窗弹窗自带 transparent !important，不受此项影响；留空即保持原生'
      }
    ]
  },
  {
    group: '推荐 / 系统 chip',
    groupKey: 'recommendations',
    items: [
      { key: '--nts-rec-bg', label: '推荐条背景', type: 'color', native: 'transparent' },
      { key: '--nts-rec-text', label: '推荐条文字', type: 'color', native: 'inherit' },
      { key: '--nts-rec-border', label: '推荐条描边', type: 'color', native: 'rgba(108, 151, 174, 0.18)', darkNative: 'rgb(65, 84, 95)', hint: '只在 .proactive-recommendation-card 上生效；留空即保持原生描边（亮 rgba(108,151,174,0.18) / 暗 rgb(65,84,95)）' },
      { key: '--nts-chip-bg', label: 'chip 背景', type: 'color', native: 'transparent' },
      { key: '--nts-chip-text', label: 'chip 文字', type: 'color', native: 'inherit' }
    ]
  },
  {
    group: '全局',
    groupKey: 'global',
    items: [
      {
        key: '--nts-text-gradient', label: '文字渐变', type: 'gradient', native: 'none',
        hint: '把聊天正文的文字填充换成一条渐变（如 linear-gradient(135deg, #6ee7ff 0%, #a78bfa 100%)）。只作用于消息文本块，不影响气泡底色；代码块与行内代码会自动保持实色，避免「字看不见」。注意：开了以后「气泡文字色」对正文不再生效（文字由渐变填充），且浅色渐变在浅背景上可能看不清 —— 建议选深浅两端跨度大的颜色。留空即纯色文字'
      },
      { key: '--nts-accent', label: '主色调', type: 'color', native: '#2a7bc4' },
      { key: '--nts-link', label: '链接色', type: 'color', native: '#2a7bc4' },
      { key: '--nts-highlight', label: '选中高亮', type: 'color', native: 'rgba(68, 183, 254, 0.35)', darkNative: 'rgba(74,163,223,0.4)' },
      { key: '--nts-scrollbar-thumb', label: '滚动条滑块', type: 'color', native: 'rgba(0, 0, 0, 0.25)', darkNative: 'rgba(255,255,255,0.2)' },
      { key: '--nts-scrollbar-track', label: '滚动条轨道', type: 'color', native: 'transparent' }
    ]
  },
  {
    group: '非颜色项',
    groupKey: 'nonColor',
    items: [
      {
        key: '--nts-radius', label: '通用圆角（按钮/输入面板）', type: 'length', native: '8px',
        hint: '原生值各组件不同（顶栏 0 / 卡片 12px / 按钮 10px），留空即完全保持原生'
      },
      {
        key: '--nts-bubble-radius', label: '聊天气泡圆角', type: 'length', native: '16px',
        hint: '只影响气泡本体；留空即保持原生（用户气泡 16px/8px 不对称圆角）'
      },
      {
        key: '--nts-shell-radius', label: '聊天窗外壳圆角', type: 'length', native: '14px',
        hint: '只影响聊天窗口外壳 #react-chat-window-shell；留空即保持原生'
      },
      {
        key: '--nts-card-radius', label: '插件卡片圆角', type: 'length', native: '0px',
        hint: '只影响 .agent-plugin-view 卡片；留空即保持原生'
      },
      {
        key: '--nts-modal-radius', label: '弹窗圆角', type: 'length', native: '0px',
        hint: '只影响 .modal-dialog 对话框；留空即保持原生'
      },
      {
        key: '--nts-popover-radius', label: '浮层圆角', type: 'length', native: '12px',
        hint: '只影响 .composer-overflow-popover 等弹出层；留空即保持原生'
      },
      {
        key: '--nts-rec-radius', label: '推荐卡圆角', type: 'length', native: '18px',
        hint: '只影响推荐类气泡卡片；留空即保持原生'
      },
      { key: '--nts-font-family', label: '字体族', type: 'font', native: 'inherit' },
      {
        key: '--nts-font-size', label: '基础字号', type: 'length', native: '0.94rem',
        hint: '消息正文的字号，纯文本与 Markdown 两种消息都会跟着变（如 1rem / 17px）；留空即保持原生'
      },
      {
        key: '--nts-line-height', label: '行高', type: 'ratio', native: '',
        hint: '聊天气泡里文字的行距，写成无单位倍数（1.55 = 1.55 倍字号）。1.3 偏紧凑、1.55 接近原生、1.8 很松。它是倍数而不是 px，所以改「基础字号」时行距会跟着缩放，不会出现字变大行距没变、两行粘在一起。留空即保持原生'
      },
      {
        key: '--nts-shadow', label: '阴影', type: 'shadow', native: '',
        hint: '如 0 2px 4px rgba(0,0,0,0.1)；作用在 7 个卡片面上（用户/AI 气泡、输入面板、两个浮层、弹窗、推荐卡），会整体替换这些面原有的阴影；留空即保持原生阴影'
      }
    ]
  },
  {
    group: '面板光效 / 玻璃拟态',
    groupKey: 'panelGlow',
    items: [
      {
        key: '--nts-panel-glow-color', label: '光晕颜色', type: 'color', native: '#ffffff',
        hint: '外发光、四角高光、边缘内侧渐变三者共用的颜色。任意 CSS 颜色都行（#7ff / dodgerblue / rgb(120,200,255)）。改这里，三处会一起变色'
      },
      {
        key: '--nts-panel-glow-strength', label: '发光强度', type: 'number', native: '1',
        hint: '0-1 乘子，同时缩放外发光的扩散范围、模糊半径与颜色浓度。0 = 完全无光，1 = 用满「光晕尺寸」。留空即保持原生（无光）'
      },
      {
        key: '--nts-panel-glow-size', label: '光晕尺寸', type: 'length', native: '24px',
        hint: '外发光的基础半径（扩散 1 倍、模糊 0.42 倍）。内侧边缘渐变的宽度也按它算。24px 是柔和的起步值，压到 40px 以上会明显晕开'
      },
      {
        key: '--nts-panel-glow-pulse', label: '呼吸幅度', type: 'number', native: '0',
        hint: '发光强度的呼吸幅度，0-1：0 = 恒定不呼吸，0.5 = 亮度在 100%~50% 之间起伏，1 = 完全暗到亮。留空或 0 都不会挂动画（省一层合成）'
      },
      {
        key: '--nts-panel-glow-speed', label: '呼吸节奏', type: 'time', native: '3.2s',
        hint: '一次呼吸的时长，写 3.2s / 800ms 都行；只填数字按秒算（3 会补成 3s）。上限 60s。数越小越急，5s 以上是慢速呼吸。只在「呼吸幅度」大于 0 时才有意义'
      },
      {
        key: '--nts-panel-border-color', label: '描边颜色', type: 'color', native: 'rgba(255,255,255,.55)',
        hint: '面板边缘那圈半透明描边。想做出玻璃高光感，用白色系带透明度（rgba(255,255,255,.55)）；想做出彩色霓虹边，填高饱和色'
      },
      {
        key: '--nts-panel-border-width', label: '描边粗细', type: 'length', native: '1.5px',
        hint: '边缘描边的宽度。1px 是细高光，2-3px 以上会有明显「玻璃边框」感；留空即不画描边'
      },
      {
        key: '--nts-panel-corner-glow', label: '四角高光', type: 'number', native: '1',
        hint: '0-1 乘子，控制四个角上那四团径向高光的浓度。玻璃拟态里最容易出「立体感」的一项；0 = 关闭，1 = 最亮'
      },
      {
        key: '--nts-panel-edge-gradient', label: '边缘内侧渐变', type: 'number', native: '1',
        hint: '0-1 乘子，控制从边缘往内柔和衰减的那层亮光（inset 阴影），配合外发光能做出「面板自己在发亮」的效果。0 = 关闭'
      },
      {
        key: '--nts-panel-sheen', label: '玻璃反射高光', type: 'number', native: '1',
        hint: '0-1 乘子，控制从左上斜下来那条高光带（模拟玻璃反光）。0.3-0.5 比较自然，1 会明显发白'
      },
      {
        key: '--nts-panel-inner-glow', label: '内阴影', type: 'number', native: '1',
        hint: '0-1 乘子，控制面板内侧一圈淡淡的下沉阴影，让面板看起来「厚」。0 = 关闭'
      },
      {
        key: '--nts-panel-glass-tint', label: '玻璃底色', type: 'color', native: 'transparent',
        hint: '半透明玻璃底色，铺在「聊天背板背景」之上、背景图之下。想做出磨砂玻璃，填 rgba(255,255,255,.14) 这类带透明度的亮色（暗色主题用 rgba(20,30,45,.35)）'
      },
      {
        key: '--nts-panel-glass-blur', label: '玻璃磨砂', type: 'blur', native: 'blur(18px)',
        hint: '把「聊天背景图片」本身糊掉（只糊图片，正文不受影响 —— 走的是夹在图片与文字之间的负层）。写 blur(18px) 或只写 18px（后端自动补成 blur(18px)）；单位只接受 px/rem/em/pt'
      }
    ]
  },
  {
    group: '液态玻璃 / 渐变',
    groupKey: 'liquidGlass',
    items: [
      {
        key: '--nts-glass-filter', label: '界面玻璃模糊', type: 'filter',
        native: 'blur(20px) saturate(150%)', darkNative: 'blur(20px) saturate(150%)',
        hint: '顶栏、输入框外壳、浮层这些「玻璃面」背后的模糊程度，直接写 backdrop-filter 的链，例如 blur(20px) saturate(150%)。最多 4 段、同名滤镜不能重复，blur 封顶 40px、其余百分比封顶 400%。留空 = 不生效，这些面保持原生观感'
      },
      {
        key: '--nts-glass-tint', label: '界面玻璃底色', type: 'color',
        native: 'rgba(255,255,255,0.55)', darkNative: 'rgba(20,26,38,0.55)',
        hint: '玻璃面上的半透明底色，和上面的模糊是配套的：想做出磨砂玻璃就填 rgba(255,255,255,0.55) 这类带透明度的颜色。留空 = 不生效，底色跟原生走'
      },
      {
        key: '--nts-glass-highlight', label: '玻璃高光描边', type: 'color',
        native: 'rgba(255,255,255,0.65)', darkNative: 'rgba(255,255,255,0.22)',
        hint: '玻璃面那圈高光描边，填白色带透明度就有玻璃边缘反光的味道。留空 = 不生效，不画这圈高光'
      },
      {
        key: '--nts-bubble-glass-filter', label: '气泡玻璃模糊', type: 'filter',
        native: 'blur(12px) saturate(140%)', darkNative: 'blur(14px) saturate(150%)',
        hint: '用户/AI 气泡自身背景的模糊程度，通常比界面玻璃更轻一点，例如 blur(12px) saturate(140%)。留空 = 不生效，气泡保持原生半透明'
      },
      {
        key: '--nts-bubble-glass-tint', label: '气泡玻璃底色', type: 'color',
        native: 'rgba(255,255,255,0.18)', darkNative: 'rgba(30,40,60,0.28)',
        hint: '气泡的半透明底色；想让气泡有玻璃质感就填 rgba(255,255,255,0.18) 这类很淡的颜色。留空 = 不生效，气泡底色跟原生走'
      },
      {
        key: '--nts-chat-gradient', label: '聊天区渐变', type: 'gradient',
        native: 'linear-gradient(135deg, #eef4ff 0%, #dfe9ff 100%)',
        darkNative: 'linear-gradient(135deg, #101828 0%, #1b2440 100%)',
        hint: '整个聊天区（消息列表、聊天主体）的背景渐变，必须写成 linear-gradient(...) 或 radial-gradient(...)，2-4 个停止点、每个停止点都要带百分比。留空 = 不生效，用原生背景'
      },
      {
        key: '--nts-panel-gradient', label: '面板渐变', type: 'gradient',
        native: 'linear-gradient(135deg, #ffffff 0%, #eef3ff 100%)',
        darkNative: 'linear-gradient(135deg, #1c2436 0%, #10151f 100%)',
        hint: '聊天背板/导出历史面板那层背景渐变，格式和上面一样，会盖在背板纯色之上。留空 = 不生效，面板保持原生底色'
      },
      {
        key: '--nts-user-bubble-gradient', label: '用户气泡渐变', type: 'gradient',
        native: '', darkNative: '',
        hint: '只给用户气泡单独铺渐变背景，用「角度 + 起止色」快组一键生成最省事。留空 = 不生效（默认就不配置），气泡仍用用户气泡背景那个纯色'
      },
      {
        key: '--nts-ai-bubble-gradient', label: 'AI 气泡渐变', type: 'gradient',
        native: '', darkNative: '',
        hint: '只给 AI 气泡铺渐变背景。留空 = 不生效（默认不配置），AI 气泡仍用 AI 气泡背景的纯色'
      }
    ]
  },
  {
    group: '液态玻璃质感',
    groupKey: 'glassTexture',
    items: [
      {
        key: '--nts-glass-rim', label: '玻璃边缘反光', type: 'color',
        native: 'rgba(255,255,255,0.55)', darkNative: 'rgba(255,255,255,0.32)',
        hint: '玻璃边缘反射光：沿面板/气泡/输入框边缘画一圈像镜面切边的亮线。配合低的玻璃底色 alpha 与界面玻璃模糊，这圈越亮「玻璃厚度」越明显；留空 = 不画'
      },
      {
        key: '--nts-glass-sheen', label: '表面掠光强度', type: 'number',
        native: '0', darkNative: '0',
        hint: '表面掠光强度 0–1：0 = 完全不动，0.3–0.7 会有一条高光带缓缓扫过玻璃面（需要界面/气泡玻璃模糊已配置才看得见）；留空 = 不生效'
      },
      {
        key: '--nts-glass-noise', label: '雾面颗粒', type: 'number',
        native: '0', darkNative: '0',
        hint: '雾面颗粒 0–1：0 = 光滑，0.1–0.25 有轻微磨砂质地（配合玻璃底色 alpha 与模糊即可透视背后内容、又有磨砂颗粒感）；留空 = 不生效'
      },
      {
        key: '--nts-glass-lens', label: '边缘透镜亮环', type: 'number',
        native: '0', darkNative: '0',
        hint: '边缘透镜亮环 0–1：0 = 不画，0.5–0.8 在玻璃边缘叠一圈向内弯的亮环 + 内侧柔光，模拟 Apple 液态玻璃边缘把光「折」进来的折射感；留空 = 不生效'
      },
      {
        key: '--nts-glass-thickness', label: '玻璃厚度', type: 'number',
        native: '0', darkNative: '0',
        hint: '玻璃厚度 0–1：上缘 1px 高光 + 下缘 1px 暗边 + 四边半亮 + 外投影，越大越像一块有厚度的实体玻璃；留空 = 不生效'
      },
      {
        key: '--nts-glass-specular', label: '镜面高光', type: 'number',
        native: '0', darkNative: '0',
        hint: '镜面高光 0–1：0.6–0.8 时玻璃表面有一团柔和反光，会跟着鼠标指针移动（苹果液态玻璃最好认的特征）；触摸设备停在左上；留空 = 不生效'
      },
      {
        key: '--nts-ambient-glow', label: '指针光源', type: 'number',
        native: '0', darkNative: '0',
        hint: '指针光源 0–1：0.4–0.7 时玻璃表面会跟着鼠标出现一片柔光（光源在动，玻璃的反光也跟着动，比单点镜面更像「真实光照」）；留空 = 不生效'
      },
      {
        key: '--nts-glass-dispersion', label: '边缘分光色散', type: 'number',
        native: '0', darkNative: '0',
        hint: '边缘分光 0–1：0.3–0.55 时玻璃边缘出现暖色（左上）与冷色（右下）两条细亮线，模拟折射把光分成不同颜色；留空 = 不生效'
      },
      {
        key: '--nts-glass-caustic', label: '流动焦散', type: 'number',
        native: '0', darkNative: '0',
        hint: '流动焦散 0–1：0.3–0.6 时玻璃后面有一团柔光缓慢胀缩，像水面的光透过玻璃在晃；与「表面掠光」方向、节奏都不同；留空 = 不生效'
      }
    ]
  }
];

var CATALOG_BY_KEY = {};
CATALOG.forEach(function (group) {
  group.items.forEach(function (item) { CATALOG_BY_KEY[item.key] = item; });
});

/** Type tag at the start of a row: tells the user what kind of value the row accepts.
 *  Getters resolve the translation lazily (this object is built before i18n is ready). */
var TYPE_LABEL = {
  get color() { return tsT('core.typeLabel.color', '色'); },
  get length() { return tsT('core.typeLabel.length', '尺寸'); },
  get blur() { return tsT('core.typeLabel.blur', '模糊'); },
  get number() { return tsT('core.typeLabel.number', '数值'); },
  get font() { return tsT('core.typeLabel.font', '字体'); },
  get shadow() { return tsT('core.typeLabel.shadow', '阴影'); },
  get image() { return tsT('core.typeLabel.image', '图片'); },
  get switch() { return tsT('core.typeLabel.switch', '开关'); },
  get select() { return tsT('core.typeLabel.select', '方式'); },
  get time() { return tsT('core.typeLabel.time', '节奏'); },
  get ratio() { return tsT('core.typeLabel.ratio', '行高'); },
  get filter() { return tsT('core.typeLabel.filter', '模糊链'); },
  get gradient() { return tsT('core.typeLabel.gradient', '渐变'); }
};

/* ============================================================================
 * 2. Client-side validation mirror (kept in sync with the backend __init__.py
 *    rules; for instant feedback only — the real security boundary is always
 *    the backend)
 * ==========================================================================*/

var RE_HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
// CSS named colors (148 standard names + transparent + currentcolor; exactly the
// same set and order as RE_KEYWORD_COLOR in the backend plugin/__init__.py).
// inherit/initial/unset/revert are deliberately excluded: they inherit the parent's
// color, so "change only the bubble text color" would leak elsewhere.
// transparent must be listed explicitly: the backend has always accepted it; missing it
// here means "panel shows no error, save succeeds, loader drops the gate".
// Every chunk ends with a space and is split with .split(' ') at the end — the old
// version missed the space in a few chunks, gluing names into fakes like
// "purplebeccapurple"/"blueblueviolet" (150 entries became 136, half of them wrong).
var COLOR_NAMES = (
  'transparent aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond ' +
  'blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk ' +
  'crimson currentcolor cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey ' +
  'darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen ' +
  'darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue ' +
  'dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold ' +
  'goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender ' +
  'lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow ' +
  'lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray ' +
  'lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine ' +
  'mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen ' +
  'mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy ' +
  'oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise ' +
  'palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red ' +
  'rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue ' +
  'slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise ' +
  'violet wheat white whitesmoke yellow yellowgreen'
).split(' ');

var NAMED_COLOR = Object.create(null);

/** Named color -> hex (CSS spec values). The old parseColor did not understand named
 *  colors, so the swatch fell back to #000000 — it looked like "set dodgerblue, got black".
 *  This table fills the gap: transparent -> #00000000; currentcolor has no fixed RGB
 *  (it inherits the parent text color), so it is validated but never parsed. */
var NAMED_HEX = Object.create(null);
(function () {
  var table = {
    aliceblue: '#f0f8ff', antiquewhite: '#faebd7', aqua: '#00ffff', aquamarine: '#7fffd4',
    azure: '#f0ffff', beige: '#f5f5dc', bisque: '#ffe4c4', black: '#000000',
    blanchedalmond: '#ffebcd', blue: '#0000ff', blueviolet: '#8a2be2', brown: '#a52a2a',
    burlywood: '#deb887', cadetblue: '#5f9ea0', chartreuse: '#7fff00', chocolate: '#d2691e',
    coral: '#ff7f50', cornflowerblue: '#6495ed', cornsilk: '#fff8dc', crimson: '#dc143c',
    cyan: '#00ffff', darkblue: '#00008b', darkcyan: '#008b8b', darkgoldenrod: '#b8860b',
    darkgray: '#a9a9a9', darkgreen: '#006400', darkgrey: '#a9a9a9', darkkhaki: '#bdb76b',
    darkmagenta: '#8b008b', darkolivegreen: '#556b2f', darkorange: '#ff8c00', darkorchid: '#9932cc',
    darkred: '#8b0000', darksalmon: '#e9967a', darkseagreen: '#8fbc8f', darkslateblue: '#483d8b',
    darkslategray: '#2f4f4f', darkslategrey: '#2f4f4f', darkturquoise: '#00ced1', darkviolet: '#9400d3',
    deeppink: '#ff1493', deepskyblue: '#00bfff', dimgray: '#696969', dimgrey: '#696969',
    dodgerblue: '#1e90ff', firebrick: '#b22222', floralwhite: '#fffaf0', forestgreen: '#228b22',
    fuchsia: '#ff00ff', gainsboro: '#dcdcdc', ghostwhite: '#f8f8ff', gold: '#ffd700',
    goldenrod: '#daa520', gray: '#808080', green: '#008000', greenyellow: '#adff2f',
    grey: '#808080', honeydew: '#f0fff0', hotpink: '#ff69b4', indianred: '#cd5c5c',
    indigo: '#4b0082', ivory: '#fffff0', khaki: '#f0e68c', lavender: '#e6e6fa',
    lavenderblush: '#fff0f5', lawngreen: '#7cfc00', lemonchiffon: '#fffacd', lightblue: '#add8e6',
    lightcoral: '#f08080', lightcyan: '#e0ffff', lightgoldenrodyellow: '#fafad2', lightgray: '#d3d3d3',
    lightgreen: '#90ee90', lightgrey: '#d3d3d3', lightpink: '#ffb6c1', lightsalmon: '#ffa07a',
    lightseagreen: '#20b2aa', lightskyblue: '#87cefa', lightslategray: '#778899', lightslategrey: '#778899',
    lightsteelblue: '#b0c4de', lightyellow: '#ffffe0', lime: '#00ff00', limegreen: '#32cd32',
    linen: '#faf0e6', magenta: '#ff00ff', maroon: '#800000', mediumaquamarine: '#66cdaa',
    mediumblue: '#0000cd', mediumorchid: '#ba55d3', mediumpurple: '#9370db', mediumseagreen: '#3cb371',
    mediumslateblue: '#7b68ee', mediumspringgreen: '#00fa9a', mediumturquoise: '#48d1cc', mediumvioletred: '#c71585',
    midnightblue: '#191970', mintcream: '#f5fffa', mistyrose: '#ffe4e1', moccasin: '#ffe4b5',
    navajowhite: '#ffdead', navy: '#000080', oldlace: '#fdf5e6', olive: '#808000',
    olivedrab: '#6b8e23', orange: '#ffa500', orangered: '#ff4500', orchid: '#da70d6',
    palegoldenrod: '#eee8aa', palegreen: '#98fb98', paleturquoise: '#afeeee', palevioletred: '#db7093',
    papayawhip: '#ffefd5', peachpuff: '#ffdab9', peru: '#cd853f', pink: '#ffc0cb',
    plum: '#dda0dd', powderblue: '#b0e0e6', purple: '#800080', rebeccapurple: '#663399',
    red: '#ff0000', rosybrown: '#bc8f8f', royalblue: '#4169e1', saddlebrown: '#8b4513',
    salmon: '#fa8072', sandybrown: '#f4a460', seagreen: '#2e8b57', seashell: '#fff5ee',
    sienna: '#a0522d', silver: '#c0c0c0', skyblue: '#87ceeb', slateblue: '#6a5acd',
    slategray: '#708090', slategrey: '#708090', snow: '#fffafa', springgreen: '#00ff7f',
    steelblue: '#4682b4', tan: '#d2b48c', teal: '#008080', thistle: '#d8bfd8',
    tomato: '#ff6347', turquoise: '#40e0d0', violet: '#ee82ee', wheat: '#f5deb3',
    white: '#ffffff', whitesmoke: '#f5f5f5', yellow: '#ffff00', yellowgreen: '#9acd32',
  };
  var keys = Object.keys(table);
  for (var i = 0; i < keys.length; i += 1) NAMED_HEX[keys[i]] = table[keys[i]];
  NAMED_HEX.transparent = '#00000000';
}());

(function () {
  for (var i = 0; i < COLOR_NAMES.length; i += 1) { NAMED_COLOR[COLOR_NAMES[i]] = 1; }
}());
function isNamedColor(t) { return NAMED_COLOR[String(t).toLowerCase()] === 1; }
var RE_RGB = /^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
var RE_HSL = /^hsla?\(\s*-?[0-9]{1,3}(\.[0-9]+)?\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
var RE_LENGTH = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|%|pt|vh|vw)$/i;
var RE_PERCENT = /^[0-9]{1,3}(\.[0-9]+)?%$/;
var RE_BLUR = /^blur\(\s*-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)\s*\)$/i;
/* Blur accepts a bare length (the backend expands 18px to blur(18px)), but only in
 * px/rem/em/pt: % / vh / vw would be normalized by the backend into invalid CSS like
 * blur(18%), which the loader silently drops. */
var RE_BLUR_LENGTH = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)$/i;
// Allow 1.0 / 1.00 (users naturally write "100%" as 1.0); still reject 1.5 / 2.
var RE_NUMBER = /^(?:0|1|0?\.[0-9]+|1\.0+)$/;
// Line height: unitless multiplier 0.8–3.0 (at most two decimals). Identical to backend RE_RATIO and loader RE_RATIO.
var RE_RATIO = /^[0-9](?:\.[0-9]{1,2})?$/;
var RE_ZERO = /^0$/;
/* Duration (animation-duration): with unit s/ms, or a bare number meaning seconds.
 * Kept in sync with backend RE_TIME / loader RE_TIME; capped at 60s (over a minute
 * is meaningless visually, and it catches magnitude typos such as 3000 read as
 * seconds = 50 minutes). */
var RE_TIME = /^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$/i;
var RE_TIME_BARE = /^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$/;
var TIME_MAX_SECONDS = 60;
var RE_FONT = /^[A-Za-z0-9\u4e00-\u9fff ,'"\-_.]{1,200}$/;
var RE_FORBIDDEN = /[;{}<>\\]|@import|url\s*\(/i;
var RE_INSET = /^inset$/i;
/* Chat background image: the whole string must be "none" or an absolute url(...) under
 * the built-in backgrounds route. Only 127.0.0.1 / localhost / no host (same origin)
 * are accepted — the consumer is the app window, where a relative path would resolve
 * against the page itself. Kept in sync with backend RE_BG_URL and loader RE_BG_VALUE. */
var RE_BG_VALUE = /^(?:none|url\(\s*"?(?:https?:\/\/(?:127\.0\.0\.1|localhost):[0-9]{2,5})?\/api\/theme_studio\/backgrounds\/[A-Za-z0-9._-]{1,80}"?\s*\))$/i;

/** Mirrors backend _shadow_tokens: split on whitespace outside parentheses; unbalanced parentheses -> []. */
function shadowTokens(value) {
  var tokens = [], depth = 0, buf = '';
  for (var i = 0; i < value.length; i += 1) {
    var ch = value[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') { depth -= 1; if (depth < 0) return []; }
    if (/\s/.test(ch) && depth === 0) {
      if (buf) { tokens.push(buf); buf = ''; }
      continue;
    }
    buf += ch;
  }
  if (buf) tokens.push(buf);
  if (depth !== 0) return [];
  return tokens;
}

/**
 * Structural validation of box-shadow. A token whitelist alone is not enough: the
 * browser grammar is <inset>? && <length>{2,4} && <color>?, and percentages are not
 * valid lengths in box-shadow. The old version let '0 0 10% #000' / '1px' /
 * '1px 2px 3px 4px 5px' through; the panel said "applied" while the browser dropped
 * the whole declaration — exactly "changed the setting but nothing looks different".
 * All three validators (panel / loader / backend) must agree; see THEME_CONTRACT §9.10b.
 */
/** Split a multi-layer box-shadow on top-level commas (mirrors backend _split_shadow_layers). */
function shadowLayers(value) {
  var layers = [];
  var depth = 0;
  var buf = '';
  for (var i = 0; i < value.length; i += 1) {
    var ch = value.charAt(i);
    if (ch === '(') depth += 1;
    else if (ch === ')') { depth -= 1; if (depth < 0) return []; }
    if (ch === ',' && depth === 0) {
      if (buf.replace(/\s+/g, '')) layers.push(buf.trim());
      buf = '';
      continue;
    }
    buf += ch;
  }
  if (depth !== 0) return [];
  if (buf.replace(/\s+/g, '')) layers.push(buf.trim());
  return layers;
}

/** Token validation of a single box-shadow layer (same verdict as backend _shadow_layer_ok). */
function shadowLayerOk(tokens) {
  if (!tokens.length || tokens.length > 6) return false; // inset + 4 lengths + color
  var lengths = 0;
  var colors = 0;
  var insets = 0;
  for (var i = 0; i < tokens.length; i += 1) {
    var t = tokens[i];
    if (RE_INSET.test(t)) {
      if (++insets > 1) return false;
      continue;
    }
    if (isNamedColor(t) || RE_HEX.test(t) || RE_RGB.test(t) || RE_HSL.test(t)) {
      if (++colors > 1) return false;
      continue;
    }
    // Percentages are not valid box-shadow lengths (50% makes the browser drop the whole declaration)
    if (RE_PERCENT.test(t) || t.indexOf('%') !== -1) return false;
    if (RE_LENGTH.test(t) || RE_ZERO.test(t)) {
      lengths += 1;
      if (lengths > 4) return false;
      // Negative values are only valid for the first two lengths (x/y offset); blur and spread radii cannot be negative
      if (t.charAt(0) === '-' && lengths > 2) return false;
      continue;
    }
    return false;
  }
  return lengths >= 2;
}

/** Supports comma-separated multi-layer shadows (the standard way to write neon-glow themes). */
function isSafeShadow(value) {
  var layers = shadowLayers(value);
  if (!layers.length || layers.length > 4) return false;
  for (var i = 0; i < layers.length; i += 1) {
    if (!shadowLayerOk(shadowTokens(layers[i]))) return false;
  }
  return true;
}

/* ---------------- Filter chain / gradient (FEATURE-CONTRACT v0.1.11 §2) ----------------
 * Same verdict, rule for rule, as backend _is_filter_value / _is_gradient_value and the
 * loader's isSafeValue. The panel only gives instant feedback; the security boundary is
 * always the backend. Loosening any one of the three leads to "panel saves it, loader
 * drops it, user sees no change". */

var RE_FILTER_TOKEN = /^(blur|saturate|brightness|contrast|grayscale)\((\d{1,3}(?:\.\d+)?)(px|%)\)$/;

/** Space-separated filter chain: 1–4 tokens, no duplicate functions, blur ≤ 40px, others ≤ 400%. */
function isSafeFilterChain(v) {
  if (typeof v !== 'string') return false;
  var s = v.trim();
  if (!s || s.length > 120) return false;
  var toks = s.split(' ');
  if (toks.length < 1 || toks.length > 4) return false;
  var seen = Object.create(null);
  for (var i = 0; i < toks.length; i += 1) {
    var m = RE_FILTER_TOKEN.exec(toks[i]);
    if (!m) return false;
    var fn = m[1], num = parseFloat(m[2]), unit = m[3];
    if (seen[fn]) return false;
    seen[fn] = 1;
    if (fn === 'blur') { if (unit !== 'px' || num > 40) return false; }
    else { if (unit !== '%' || num > 400) return false; }
    if (toks[i] !== m[0]) return false;
  }
  return true;
}

var RE_GRAD_HEAD = /^(linear|radial)-gradient\(([\s\S]*)\)$/;
var RE_GRAD_ANGLE = /^-?\d{1,3}(?:\.\d+)?deg$/;
// Color-stop percentages are **required** (contract §2.2 prose + backend _gradient_ok:
// group(2) is None is rejected outright). So `linear-gradient(135deg, #fff, #000)` is
// invalid; only the "what the panel generates is what validation accepts" form
// `linear-gradient(135deg, #fff 0%, #000 100%)` is valid — both sides must agree exactly.
var RE_GRAD_STOP = /^((?:#[0-9a-f]{3,8})|(?:rgba?\([^()]*\))|(?:hsla?\([^()]*\))|(?:[a-z]{3,20}))\s+(\d{1,3}(?:\.\d+)?)%$/i;

/** Only linear/radial-gradient: 2–4 color stops, each with a percentage, whole string anchored. */
function isSafeGradient(v) {
  if (typeof v !== 'string') return false;
  var s = v.trim();
  if (!s || s.length > 300) return false;
  var m = RE_GRAD_HEAD.exec(s);
  if (!m) return false;
  // Split on top-level commas: the commas inside rgba(0,0,0,.5) are not separators.
  var body = m[2], depth = 0, parts = [], buf = '';
  for (var i = 0; i < body.length; i += 1) {
    var ch = body[i];
    if (ch === '(') depth += 1; else if (ch === ')') { depth -= 1; if (depth < 0) return false; }
    if (ch === ',' && depth === 0) { parts.push(buf.trim()); buf = ''; continue; }
    buf += ch;
  }
  parts.push(buf.trim());
  if (depth !== 0) return false;
  if (m[1] === 'linear') {
    if (parts.length < 3 || !RE_GRAD_ANGLE.test(parts[0])) return false;
    parts = parts.slice(1);
  }
  if (parts.length < 2 || parts.length > 4) return false;
  for (var j = 0; j < parts.length; j += 1) {
    var sm = RE_GRAD_STOP.exec(parts[j]);
    if (!sm) return false;
    var color = sm[1];
    if (!(isNamedColor(color) || RE_HEX.test(color) || RE_RGB.test(color) || RE_HSL.test(color))) return false;
    // Percentage is required (enforced by the regex) and must be ≤ 100.
    if (!sm[2] || parseFloat(sm[2]) > 100) return false;
  }
  return true;
}

/** Aligned rule by rule with backend __init__.py _is_safe_value; instant feedback only, the security boundary is the backend. */
function isSafeValue(key, value) {
  if (typeof value !== 'string') return false;
  var v = value.trim();
  if (!v || v.length > 300) return false;
  var item = CATALOG_BY_KEY[key];
  var type = item ? item.type : 'color';
  // Background images must be checked before RE_FORBIDDEN: the blacklist contains `url\s*\(`,
  // yet url(...) is the only valid form for a background image. Whole string anchored: only
  // none or an absolute address under the built-in /api/theme_studio/backgrounds/.
  if (type === 'image') return RE_BG_VALUE.test(v);
  if (type === 'select') {
    // Select-type variables: the value must exactly match one of the options declared in CATALOG.
    var choices = (item && item.options) || [];
    for (var ci = 0; ci < choices.length; ci++) {
      if (choices[ci].value === v.toLowerCase()) return true;
    }
    return false;
  }
  if (RE_FORBIDDEN.test(v)) return false;
  // filter / gradient use the dedicated validators from contract §2 (same verdict as backend/loader).
  if (type === 'filter') return isSafeFilterChain(v);
  if (type === 'gradient') return isSafeGradient(v);
  if (type === 'color') {
    return RE_HEX.test(v) || RE_RGB.test(v) || RE_HSL.test(v) || isNamedColor(v);
  }
  // Negative lengths/radii are always invalid CSS (the declaration is dropped -> panel
  // reports success, nothing changes). Backend _is_safe_value rejects "-" for length/blur;
  // this mirrors the same rule. (Shadow offsets need the minus sign but go through
  // isSafeShadow's token branch, so they are unaffected.)
  if (type === 'length') {
    if (v.indexOf('-') !== -1) return false;
    return RE_LENGTH.test(v) || RE_PERCENT.test(v) || RE_ZERO.test(v);
  }
  if (type === 'blur') {
    if (v.indexOf('-') !== -1) return false;
    // Only px/rem/em/pt (blur(18px) or bare 18px; the backend wraps bare lengths in blur()).
    // The old version used RE_LENGTH (incl. % / vh / vw): 18% passed the panel check, the
    // backend normalized it to invalid blur(18%) and the loader dropped it — one root cause
    // of "applied successfully but nothing changed".
    return RE_BLUR.test(v) || RE_BLUR_LENGTH.test(v) || RE_ZERO.test(v);
  }
  if (type === 'number') {
    // Percentages only 0–100 (--nts-chat-opacity is a 0–1 multiplier; values like 999% are meaningless and silently dropped by the backend)
    if (RE_PERCENT.test(v)) return parseFloat(v) <= 100;
    return RE_NUMBER.test(v);
  }
  if (type === 'ratio') {
    // Line-height multiplier: 0.8–3.0, at most two decimals. Range matches backend RATIO_MIN/RATIO_MAX.
    if (!RE_RATIO.test(v)) return false;
    var ratio = parseFloat(v);
    return ratio >= 0.8 && ratio <= 3;
  }
  if (type === 'time') {
    // Bare numbers mean seconds (the backend appends s); units only s/ms, and at most 60s after conversion.
    if (RE_TIME_BARE.test(v)) return parseFloat(v) <= TIME_MAX_SECONDS;
    if (!RE_TIME.test(v)) return false;
    var secs = /ms$/i.test(v) ? parseFloat(v) / 1000 : parseFloat(v);
    return isFinite(secs) && secs >= 0 && secs <= TIME_MAX_SECONDS;
  }
  if (type === 'font') return RE_FONT.test(v);
  if (type === 'shadow') return isSafeShadow(v);
  return false;
}

/* ============================================================================
 * 3. Color utilities
 * ==========================================================================*/

function clamp(n, lo, hi) { return Math.min(hi, Math.max(lo, n)); }

function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360 / 360;
  s = clamp(s, 0, 100) / 100;
  l = clamp(l, 0, 100) / 100;
  if (s === 0) { var g = Math.round(l * 255); return [g, g, g]; }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var p = 2 * l - q;
  function hue(t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  }
  return [
    Math.round(hue(h + 1 / 3) * 255),
    Math.round(hue(h) * 255),
    Math.round(hue(h - 1 / 3) * 255)
  ];
}

/** Parse a CSS color -> {r,g,b,a}; returns null when it cannot be parsed. */
function parseColor(input) {
  if (typeof input !== 'string') return null;
  var v = input.trim();
  if (!v) return null;
  var lower = v.toLowerCase();
  if (lower === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
  // Named colors are looked up in NAMED_HEX and handed to the hex branch below. The old
  // version returned null here, so values like "dodgerblue" fell back to #000000 on the
  // swatch — users saw "set a named color, it turned black". currentcolor has no fixed RGB
  // (it inherits the parent text color) and is deliberately left out: it returns null and
  // the panel shows the text as-is instead of silently turning it black.
  if (NAMED_HEX[lower]) return parseColor(NAMED_HEX[lower]);

  var m = v.match(/^#([0-9a-f]{3,8})$/i);
  if (m) {
    var h = m[1];
    if (h.length === 3) {
      return {
        r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16), a: 1
      };
    }
    if (h.length === 4) {
      // #RGBA: each nibble is doubled; alpha = last nibble / 15 (same as 0xNN / 255)
      return {
        r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16),
        a: parseInt(h[3] + h[3], 16) / 255
      };
    }
    if (h.length === 6) {
      return {
        r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1
      };
    }
    if (h.length === 8) {
      return {
        r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16),
        a: parseInt(h.slice(6, 8), 16) / 255
      };
    }
    return null;
  }

  m = v.match(/^rgba?\(([^)]*)\)$/i);
  if (m) {
    var parts = m[1].split(',').map(function (s) { return s.trim(); });
    if (parts.length < 3) return null;
    var r = parseInt(parts[0], 10), g = parseInt(parts[1], 10), b = parseInt(parts[2], 10);
    var a = parts.length > 3 ? parseFloat(parts[3]) : 1;
    if ([r, g, b].some(function (n) { return !isFinite(n); })) return null;
    return { r: clamp(r, 0, 255), g: clamp(g, 0, 255), b: clamp(b, 0, 255), a: isFinite(a) ? clamp(a, 0, 1) : 1 };
  }

  m = v.match(/^hsla?\(([^)]*)\)$/i);
  if (m) {
    var hp = m[1].split(',').map(function (s) { return s.trim(); });
    if (hp.length < 3) return null;
    var rgb = hslToRgb(parseFloat(hp[0]), parseFloat(hp[1]), parseFloat(hp[2]));
    var al = hp.length > 3 ? parseFloat(hp[3]) : 1;
    return { r: rgb[0], g: rgb[1], b: rgb[2], a: isFinite(al) ? clamp(al, 0, 1) : 1 };
  }

  return null;
}

function toHex(r, g, b) {
  function h(n) { var s = clamp(Math.round(n), 0, 255).toString(16); return s.length === 1 ? '0' + s : s; }
  return '#' + h(r) + h(g) + h(b);
}

function toRgba(r, g, b, a) {
  var alpha = Math.round(clamp(a, 0, 1) * 100) / 100;
  return 'rgba(' + clamp(Math.round(r), 0, 255) + ',' + clamp(Math.round(g), 0, 255) + ',' + clamp(Math.round(b), 0, 255) + ',' + alpha + ')';
}

/* ============================================================================
 * 4. Backend calls (built-in HTTP API)
 * ==========================================================================*/

/* Prefix of the theme service mounted on the main server. The panel page is a built-in
 * route too, so it is same-origin and needs no port. */
var API_BASE = '/api/theme_studio';
/* Must match DEFAULT_THEME_NAME in theme_studio/service.py and the theme-name placeholder
 * in index.html. The backend treats an empty/blank name as "restore the default name" and
 * returns it; the panel still submits it explicitly. (Kept verbatim: it is the stored value.) */
var DEFAULT_THEME_NAME = '默认';
/* Per-request timeout. The built-in service is a long-running uvicorn process that normally
 * answers in milliseconds; the generous 30s only keeps a full-library import on a slow
 * disk from being misreported as a failure. */
var POST_TIMEOUT_MS = 30000;

function delay(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

/**
 * fetch with a timeout. The error thrown on timeout carries timeout=true so callers can
 * tell "timed out" from "HTTP failure". Uses a hand-rolled AbortController rather than
 * AbortSignal.timeout: the latter may be missing in older Electron/Chrome, and this
 * static panel has no build step to polyfill it.
 */
function fetchWithTimeout(url, options, timeoutMs) {
  var opts = options || {};
  var controller = new AbortController();
  var timer = setTimeout(function () { controller.abort(); }, timeoutMs);
  opts.signal = controller.signal;
  function translate(error) {
    if (error && error.name === 'AbortError') {
      var timeoutError = new Error(tsT('core.request.timeout', '请求超时（{{seconds}}s）', { seconds: Math.round(timeoutMs / 1000) }));
      timeoutError.timeout = true;
      return timeoutError;
    }
    return error;
  }
  function clear() { clearTimeout(timer); }
  return fetch(url, opts).then(function (response) {
    // The deadline also covers reading the body: a stalled body read would otherwise hang
    // the caller (and e.g. keep the external-sync poll "in flight" forever). Aborting the
    // controller rejects a pending body read, so the timer stays armed until it settles.
    // A body nobody reads just gets aborted when the timer fires, which is harmless.
    ['json', 'text', 'blob', 'arrayBuffer'].forEach(function (method) {
      var original = response[method];
      if (typeof original !== 'function') return;
      response[method] = function () {
        return original.apply(response, arguments)
          .catch(function (error) { throw translate(error); })
          .finally(clear);
      };
    });
    return response;
  }, function (error) {
    clear();
    throw translate(error);
  });
}

/** POST a JSON body and return the raw fetch Response (business checks are left to the caller). */
/** UI language of the panel, sent so backend messages come back in the same language. */
function panelLocale() {
  try {
    var lang = (window.i18n && window.i18n.language) || document.documentElement.lang || '';
    return String(lang || '');
  } catch (error) {
    return '';
  }
}

function postJson(url, body) {
  var headers = { 'Content-Type': 'application/json' };
  var locale = panelLocale();
  if (locale) headers['X-Neko-Locale'] = locale;
  return fetchWithTimeout(url, {
    method: 'POST',
    headers: headers,
    body: body
  }, POST_TIMEOUT_MS);
}

/* Host error wrapping: when an entry does return Err(SdkError("<reason>")), the host wraps it as
 *   Plugin 'neko_theme_studio' entry 'entry import_theme' execution failed:
 *   Plugin 'neko_theme_studio' entry 'import_theme' execution failed: <reason>
 * sometimes nested twice. Shown as-is it is an unreadable internal error, so strip the
 * wrappers and keep only the reason written by the plugin; if nothing is left, fall back
 * to the original text — never swallow the error. */
var HOST_ERROR_WRAPPER = /execution failed:\s*/g;

function stripHostErrorWrapper(text) {
  var msg = String(text == null ? '' : text);
  var cut = -1;
  var m;
  HOST_ERROR_WRAPPER.lastIndex = 0;
  while ((m = HOST_ERROR_WRAPPER.exec(msg)) !== null) cut = m.index + m[0].length;
  if (cut > 0) msg = msg.slice(cut);
  return msg.trim() || String(text == null ? '' : text).trim();
}

function pluginErrorMessage(error) {
  if (!error) return '';
  if (typeof error === 'string') return stripHostErrorWrapper(error);
  if (typeof error.message === 'string') return stripHostErrorWrapper(error.message);
  if (typeof error.detail === 'string') return stripHostErrorWrapper(error.detail);
  if (typeof error.code === 'string') return error.code;
  return '';
}

/* Call a built-in entry and return its JSON result.
 *
 * The built-in version has no "plugin process not running" failure mode: the theme
 * service starts with the main server, so a failure can only be a real backend error or
 * a timeout, and retrying is up to the user — no self-healing retry here, so one 30s
 * timeout never becomes two. The name callPlugin is kept because 20+ call sites use it. */
async function callPlugin(entryId, args) {
  return callPluginOnce(entryId, args);
}

/** Call a built-in entry. The backend returns {ok:true,result} / {ok:false,error}, always with HTTP 200. */
async function callPluginOnce(entryId, args) {
  var url = API_BASE + '/run/' + encodeURIComponent(entryId);
  var resp;
  try {
    resp = await postJson(url, JSON.stringify(args || {}));
  } catch (error) {
    if (error && !error.phase) error.phase = 'post';
    throw error;
  }
  if (!resp.ok) {
    var httpError = new Error('HTTP ' + resp.status);
    httpError.phase = 'post';
    httpError.status = resp.status;
    throw httpError;
  }
  var payload = await resp.json();
  if (!payload || payload.ok !== true) {
    var reason = (payload && (payload.error || payload.detail)) || tsT('core.request.failed', '操作失败');
    throw new Error(pluginErrorMessage(reason) || tsT('core.request.failed', '操作失败'));
  }
  return payload.result;
}

/* ============================================================================
 * 5. State
 * ==========================================================================*/

/** Short label of a select option (drops the "(…)" note), used in the "native X" cell. */
function selectOptionLabel(item, value) {
  var opts = (item && item.options) || [];
  var hit = '';
  opts.forEach(function (opt) {
    if (!hit && String(opt.value) === String(value)) hit = String(opt.label || opt.value);
  });
  if (!hit) hit = String(value || '');
  // Translated labels may use an ASCII "(" instead of the full-width "（".
  return hit.split(/\s*[（(]/)[0];
}

var state = {
  mode: 'light',            // 'light' | 'dark' — editing vars or dark_vars
  palette: null,            // palette on the server (single source of truth)
  edited: { light: {}, dark: {} },  // keys the user actually changed -> value
  cleared: { light: {}, dark: {} }, // keys the user hit "Clear" on -> true (empty string submitted = delete)
  collapsed: {},
  filter: '',               // filter text from the search box (compared in lower case)
  onlyDirty: false,         // "changes only": hide unchanged rows
  busy: false,
  modalMode: 'import',
  modalReturnFocus: null,
  // Saved baseline for name / enabled: written by renderMeta, used by applyTheme to detect
  // changes. The initial values equal the DOM defaults in index.html, so even if the first
  // get_theme fails (palette=null), changing only the name or the toggle counts as a change.
  metaName: '',
  metaEnabled: true,
  // Backend status last read by the "Runtime status" card (studio_status result).
  status: null
};

function editedMap() { return state.edited[state.mode]; }
function clearedMap() { return state.cleared[state.mode]; }

function isDirty(key) {
  return Object.prototype.hasOwnProperty.call(editedMap(), key) ||
    Object.prototype.hasOwnProperty.call(clearedMap(), key);
}


/** Dirty count of the "meta" fields: name and enabled toggle (variables are still counted by dirtyCount).
 *
 *  The old version only counted variables, so renaming or flipping the toggle alone gave dirty=0:
 *  (1) external sync (time-based theme switching) saw a clean panel and refreshed, wiping the typed name;
 *  (2) the "Apply" button showed no change count and only said "nothing to apply" when clicked, looking broken;
 *  (3) these two controls were not disabled while busy, so text typed during reload was overwritten by renderMeta.
 */
function metaDirtyCount() {
  var n = 0;
  var nameInput = $('theme-name');
  if (nameInput && nameInput.value.trim() !== String(state.metaName || '').trim()) n += 1;
  var enabledInput = $('theme-enabled');
  if (enabledInput && (!!enabledInput.checked !== !!state.metaEnabled)) n += 1;
  return n;
}

/** All uncommitted changes in the panel: variables + name/enabled. */
function pendingChangeCount() { return dirtyCount() + metaDirtyCount() + materialsDirtyCount(); }

function dirtyCount() {
  var light = Object.keys(state.edited.light).length + Object.keys(state.cleared.light).length;
  var dark = Object.keys(state.edited.dark).length + Object.keys(state.cleared.dark).length;
  return light + dark;
}

/** Value displayed for the row in the current mode: the edited value if changed, otherwise the native reference value. */
function displayValue(key) {
  if (Object.prototype.hasOwnProperty.call(editedMap(), key)) return editedMap()[key];
  if (Object.prototype.hasOwnProperty.call(clearedMap(), key)) return '';
  var server = serverValue(key);
  if (server !== null) return server;
  if (state.mode === 'dark') {
    var item = CATALOG_BY_KEY[key];
    if (item && item.darkNative !== undefined) return item.darkNative;
  }
  return '';
}

/** Value of this key in the server palette (current mode), or null if absent. */
function serverValue(key) {
  if (!state.palette) return null;
  var bag = state.mode === 'dark' ? state.palette.dark_vars : state.palette.vars;
  if (bag && Object.prototype.hasOwnProperty.call(bag, key)) return bag[key];
  return null;
}

/** Value submitted for this key: edited -> value; cleared -> '' (delete); untouched -> undefined (not sent). */
function submitValue(key) {
  if (Object.prototype.hasOwnProperty.call(editedMap(), key)) return editedMap()[key];
  if (Object.prototype.hasOwnProperty.call(clearedMap(), key)) return '';
  return undefined;
}

/* ---------------------------------------------------------------- Readability check (v0.4.9)
 *
 * After picking colors, the most overlooked thing is text/background contrast. These pure
 * functions use the same criteria as `_work/audit_themes.py` (WCAG 2.1: body 4.5,
 * secondary 3.0); one click lists every pair below the threshold and jumps to its row.
 */

/** Composite a foreground with alpha onto an opaque background (both return {r,g,b,a:1}). */
function colorOver(fg, bg) {
  if (!fg) return null;
  if (!bg) return { r: fg.r, g: fg.g, b: fg.b, a: 1 };
  var a = (fg.a === undefined || fg.a === null) ? 1 : fg.a;
  if (a >= 1) return { r: fg.r, g: fg.g, b: fg.b, a: 1 };
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1
  };
}

function colorChannel(v) {
  v = v / 255;
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function colorLuma(c) {
  if (!c) return 0;
  return 0.2126 * colorChannel(c.r) + 0.7152 * colorChannel(c.g) + 0.0722 * colorChannel(c.b);
}

/** WCAG contrast ratio: (lighter + .05) / (darker + .05). */
function contrastRatio(fg, bg) {
  if (!fg || !bg) return 0;
  var l1 = colorLuma(fg);
  var l2 = colorLuma(bg);
  if (l1 < l2) { var t = l1; l1 = l2; l2 = t; }
  return (l1 + 0.05) / (l2 + 0.05);
}

/** The middle color stop of a gradient (where bubble text most often sits). */
function gradientMidColor(value) {
  if (typeof value !== 'string' || !value) return null;
  var re = /(#[0-9a-fA-F]{3,8}|rgba?\([^()]*\))/g;
  var stops = [];
  var m;
  while ((m = re.exec(value))) {
    var c = parseColor(m[1]);
    if (c) stops.push(c);
  }
  if (!stops.length) return null;
  return stops[Math.floor(stops.length / 2)];
}

/** Mix color c toward target by t (0 keeps the original, 1 becomes target). */
function mixToward(c, target, t) {
  return {
    r: c.r + (target.r - c.r) * t,
    g: c.g + (target.g - c.g) * t,
    b: c.b + (target.b - c.b) * t,
    a: 1
  };
}

/**
 * Find a foreground color that just meets the threshold on the given background.
 * Tries both toward black and toward white in 1% steps and picks the smallest change
 * that passes: dark text on light backgrounds usually gets darker, light text on dark
 * backgrounds lighter; when the color is already extreme (pure white on mid blue) it goes
 * the other way (to dark text) — both are valid under WCAG. Returns null if it already
 * passes or there is no solution.
 */
function contrastFixColor(fg, bg, need) {
  if (!fg || !bg) return null;
  var base = colorOver(fg, bg);
  if (!base) return null;
  if (contrastRatio(base, bg) >= need) return null;
  var targets = [
    { color: { r: 0, g: 0, b: 0 }, name: 'dark' },
    { color: { r: 255, g: 255, b: 255 }, name: 'light' }
  ];
  var best = null;
  targets.forEach(function (target) {
    for (var step = 1; step <= 100; step++) {
      // Round to 8-bit channels before measuring: the input receives #rrggbb, and if a
      // float value passed but the rounded one falls below the threshold, users would
      // see "fixed it, still red".
      var raw = mixToward(base, target.color, step / 100);
      var c = { r: Math.round(raw.r), g: Math.round(raw.g), b: Math.round(raw.b), a: 1 };
      var ratio = contrastRatio(c, bg);
      if (ratio >= need) {
        if (!best || step < best.t) {
          best = { color: c, ratio: ratio, t: step / 100, toward: target.name };
        }
        return;
      }
    }
  });
  return best;
}

/** {r,g,b} -> '#rrggbb' (rounded and clamped to 0–255). */
function colorToHex(c) {
  if (!c) return '';
  function part(v) {
    var n = Math.round(v);
    if (n < 0) n = 0;
    if (n > 255) n = 255;
    var s = n.toString(16);
    return s.length < 2 ? '0' + s : s;
  }
  return '#' + part(c.r) + part(c.g) + part(c.b);
}

/** Effective value in the given mode (edited -> edited value; cleared -> ''; otherwise server / native value). */
function valueForMode(key, mode) {
  var ed = state.edited[mode] || {};
  var cl = state.cleared[mode] || {};
  if (Object.prototype.hasOwnProperty.call(ed, key)) return ed[key];
  if (Object.prototype.hasOwnProperty.call(cl, key)) return '';
  var bag = state.palette ? (mode === 'dark' ? state.palette.dark_vars : state.palette.vars) : null;
  if (bag && Object.prototype.hasOwnProperty.call(bag, key)) return bag[key];
  if (mode === 'dark') {
    var item = CATALOG_BY_KEY[key];
    if (item && item.darkNative !== undefined) return item.darkNative;
  }
  return '';
}

function auditParse(key, mode, fallback) {
  var v = valueForMode(key, mode);
  if (!v) v = fallback || '';
  return parseColor(v);
}

/** 11 contrast checks (same criteria as checks() in _work/audit_themes.py).
 *  `name` is a getter so it is translated when the audit runs, not at load time. */
var READ_PAIRS = [
  { get name() { return tsT('core.audit.pair.bodyChat', '正文 / 聊天背景'); }, fg: '--nts-panel-text', bg: 'chat', need: 4.5 },
  { get name() { return tsT('core.audit.pair.bodyTopbar', '正文 / 顶栏'); }, fg: '--nts-topbar-text', bg: 'surface', need: 4.5 },
  { get name() { return tsT('core.audit.pair.bodyInput', '正文 / 输入框'); }, fg: '--nts-input-text', bg: 'input', need: 4.5 },
  { get name() { return tsT('core.audit.pair.bodyGlass', '正文 / 面板玻璃'); }, fg: '--nts-panel-text', bg: 'glass', need: 4.5 },
  { get name() { return tsT('core.audit.pair.bodyAiBubble', '正文 / AI 气泡'); }, fg: '--nts-ai-bubble-text', bg: 'ai', need: 4.5 },
  { get name() { return tsT('core.audit.pair.userBubbleText', '用户气泡文字'); }, fg: '--nts-user-bubble-text', bg: 'user', need: 4.5 },
  { get name() { return tsT('core.audit.pair.sendText', '发送键文字'); }, fg: '--nts-send-text', bg: 'send', need: 4.5 },
  { get name() { return tsT('core.audit.pair.subTextChat', '次要文字 / 聊天背景'); }, fg: '--nts-panel-text-sub', bg: 'chat', need: 3.0 },
  { get name() { return tsT('core.audit.pair.placeholder', 'placeholder'); }, fg: '--nts-input-placeholder', bg: 'input', need: 3.0 },
  { get name() { return tsT('core.audit.pair.chipText', 'chip 文字'); }, fg: '--nts-chip-text', bg: 'chip', need: 3.0 },
  { get name() { return tsT('core.audit.pair.linkChat', '链接 / 聊天背景'); }, fg: '--nts-link', bg: 'chat', need: 3.0 }
];

/** Audit result rows for one mode (colors that cannot be parsed are skipped). */
function auditRowsForMode(mode) {
  var dark = mode === 'dark';
  var chatBg = auditParse('--nts-chat-bg', mode, dark ? '#1f242a' : '#eef3f8');
  if (!chatBg) chatBg = { r: dark ? 31 : 238, g: dark ? 36 : 243, b: dark ? 42 : 248, a: 1 };
  var surface = auditParse('--nts-topbar-bg', mode, '') || chatBg;
  var glass = colorOver(auditParse('--nts-panel-glass-tint', mode, ''), chatBg) || surface;
  var inputBg = colorOver(auditParse('--nts-input-bg', mode, ''), chatBg) || chatBg;
  var userBg = colorOver(auditParse('--nts-user-bubble-bg', mode, ''), chatBg) ||
    gradientMidColor(valueForMode('--nts-user-bubble-gradient', mode)) || chatBg;
  var aiBg = colorOver(auditParse('--nts-ai-bubble-bg', mode, ''), chatBg) || chatBg;
  var chipBg = colorOver(auditParse('--nts-chip-bg', mode, ''), chatBg) || chatBg;
  var sendBg = auditParse('--nts-send-bg', mode, '') || chatBg;
  var bgs = { chat: chatBg, surface: surface, input: inputBg, glass: glass, ai: aiBg, user: userBg, chip: chipBg, send: sendBg };

  var out = [];
  READ_PAIRS.forEach(function (pair) {
    var fg = auditParse(pair.fg, mode, '');
    var bg = bgs[pair.bg];
    if (!fg || !bg) return;
    fg = colorOver(fg, bg);
    var ratio = contrastRatio(fg, bg);
    out.push({
      name: pair.name,
      key: pair.fg,
      mode: mode,
      ratio: Math.round(ratio * 100) / 100,
      need: pair.need,
      ok: ratio >= pair.need,
      // The two opaque colors used by auto-fix: fg is already composited onto bg, bg is the computed backdrop.
      fg: fg,
      bg: bg
    });
  });
  return out;
}

/** Clicking a failed item -> switch to its mode, clear the search, scroll to the row and highlight it. */
function jumpToAuditRow(key, mode) {
  if (mode && state.mode !== mode) setMode(mode);
  if (state.filter) setFilter('');
  if (state.onlyDirty) setOnlyDirty(false);
  var ref = rowsByKey[key];
  if (!ref || !ref.container) return;
  if (ref.container.hidden) {
    // The row is hidden inside a collapsed group: expand that group first
    var group = null;
    CATALOG.forEach(function (g) {
      if (group) return;
      g.items.forEach(function (it) { if (it.key === key) group = g; });
    });
    if (group && state.collapsed[groupStateKey(group)]) {
      state.collapsed[groupStateKey(group)] = false;
      saveCatalogCollapsed();
      applyFilters();
    }
    ref.container.hidden = false;
  }
  if (ref.container.scrollIntoView) ref.container.scrollIntoView({ block: 'center', behavior: 'smooth' });
  ref.container.classList.add('is-flash');
  setTimeout(function () { ref.container.classList.remove('is-flash'); }, 1400);
  if (ref.hexInput && ref.hexInput.focus) ref.hexInput.focus();
}

/**
 * Fix a failed item's foreground to a color that just passes: only fills the input
 * (making it dirty) without submitting — the user can still preview, revert, and apply
 * it together with other changes via "Apply".
 * @returns {boolean} true = a usable fix value was found
 */
function applyAuditFix(row, quiet) {
  if (!row) return false;
  // Keep a 0.05 margin: the panel shows two decimals, and 4.499 would look like "fixed but still red".
  var fix = contrastFixColor(row.fg, row.bg, row.need + 0.05);
  if (!fix) return false;
  if (row.mode && state.mode !== row.mode) setMode(row.mode);
  var hex = colorToHex(fix.color);
  if (!hex) return false;
  markEdited(row.key, hex);
  syncRow(row.key);
  if (!quiet) {
    showToast(tsT('core.audit.fixedOne', '「{{name}}」调到 {{hex}}（{{ratio}}:1），点「应用」生效', {
      name: row.name, hex: hex, ratio: fix.ratio.toFixed(2)
    }), 'ok');
  }
  return true;
}

/** Fill in fix values for every failed item in both light and dark sets at once (the user still has to click "Apply"). */
function fixAllAudit() {
  var original = state.mode;
  var fixed = 0;
  var stuck = 0;
  ['light', 'dark'].forEach(function (mode) {
    var rows = auditRowsForMode(mode).filter(function (r) { return !r.ok; });
    if (!rows.length) return;
    if (state.mode !== mode) setMode(mode);
    rows.forEach(function (r) {
      if (applyAuditFix(r, true)) fixed += 1;
      else stuck += 1;
    });
  });
  if (state.mode !== original) setMode(original);
  renderAudit(true);
  if (!fixed) {
    showToast(tsT('core.audit.fixNone', '没有能自动修正的项：这组底色和文字太接近，需要手动改背景色'), 'error');
    return;
  }
  showToast(stuck
    ? tsT('core.audit.fixedSomeStuck', '已填好 {{fixed}} 项修正值，{{stuck}} 项自动调不动；点「应用」生效', { fixed: fixed, stuck: stuck })
    : tsT('core.audit.fixedAll', '已填好 {{fixed}} 项修正值；点「应用」生效', { fixed: fixed }), 'ok');
}

/** Render the audit results; computed on first click (auto=false means a one-off pre-scan of the current state). */
function renderAudit(auto) {
  var list = $('audit-list');
  var summary = $('audit-summary');
  if (!list || !summary) return;
  var light = auditRowsForMode('light');
  var dark = auditRowsForMode('dark');
  var bad = light.filter(function (r) { return !r.ok; }).concat(dark.filter(function (r) { return !r.ok; }));
  list.innerHTML = '';
  function renderGroup(label, rows) {
    var head = el('li', { class: 'audit-group' }, [el('span', { text: label })]);
    if (!rows.length) {
      head.appendChild(el('span', { class: 'audit-muted', text: tsT('core.audit.incomplete', '颜色没配全，暂时无法判断') }));
    } else {
      var groupBad = bad.filter(function (r) { return r.mode === rows[0].mode; }).length;
      head.appendChild(el('span', {
        class: 'audit-badge ' + (groupBad ? 'is-fail' : 'is-pass'),
        text: groupBad
          ? tsT('core.audit.groupFail', '{{bad}} / {{total}} 项低于阈值', { bad: groupBad, total: rows.length })
          : tsT('core.audit.groupPass', '{{total}} 项全部达标', { total: rows.length })
      }));
    }
    list.appendChild(head);
    rows.forEach(function (r) {
      var li = el('li', { class: 'audit-item ' + (r.ok ? 'is-pass' : 'is-fail') }, [
        el('span', { class: 'audit-name', text: r.name }),
        el('span', { class: 'audit-ratio', text: r.ratio.toFixed(2) + ':1' }),
        el('span', { class: 'audit-plus', text: r.ok ? '✓' : tsT('core.audit.need', '需 ≥ {{need}}', { need: r.need.toFixed(1) }) })
      ]);
      if (!r.ok) {
        li.title = r.mode === 'dark'
          ? tsT('core.audit.jumpDark', '点击跳到 {{key}}（暗色）', { key: r.key })
          : tsT('core.audit.jumpLight', '点击跳到 {{key}}（亮色）', { key: r.key });
        li.setAttribute('role', 'button');
        li.setAttribute('tabindex', '0');
        li.addEventListener('click', function () { jumpToAuditRow(r.key, r.mode); });
        li.addEventListener('keydown', function (event) {
          // Keys pressed on the nested "auto fix" button bubble up here: leave them to the
          // button, or preventDefault would cancel its click and jump to the row instead.
          if (event.target !== li) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            jumpToAuditRow(r.key, r.mode);
          }
        });
        var fixBtn = el('button', {
          class: 'btn btn-mini audit-fix',
          type: 'button',
          text: tsT('core.audit.fixButton', '自动修正'),
          title: tsT('core.audit.fixButtonTitle', '按背景亮度自动调出一个达标颜色（只填值，不直接应用）')
        });
        fixBtn.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          if (applyAuditFix(r, false)) renderAudit(true);
          else showToast(tsT('core.audit.fixFailed', '这一组自动调不到 {{need}}:1，需要改背景色', { need: r.need.toFixed(1) }), 'error');
        });
        li.appendChild(fixBtn);
      }
      list.appendChild(li);
    });
  }
  renderGroup(tsT('core.audit.light', '亮色'), light);
  renderGroup(tsT('core.audit.dark', '暗色'), dark);
  if (!light.length && !dark.length) {
    summary.textContent = tsT('core.audit.nothingToCompare', '还没有可比较的颜色：先给「聊天背景」「正文」等基础变量配值。');
    return;
  }
  if (!bad.length) {
    summary.className = 'audit-summary is-pass';
    summary.textContent = tsT('core.audit.summaryPass', '两组共 {{total}} 项全部达到 WCAG 阈值（正文 4.5、次要 3.0）。', { total: light.length + dark.length }) + (auto ? '' : '');
  } else {
    summary.className = 'audit-summary is-fail';
    summary.textContent = tsT('core.audit.summaryFail', '有 {{bad}} 项低于阈值，点条目可跳到对应变量。', { bad: bad.length });
  }
}

/* ===== UNDO_BEGIN (v0.10.0: undo / redo) ===== */

/**
 * Undo stack: each step stores a full snapshot of edited/cleared from before the change.
 *
 * Why snapshots instead of individual operations: edits are additions/removals in maps,
 * and inverting a single operation has to distinguish markEdited / markCleared /
 * revert-to-server branches, which is easy to get wrong; the two maps hold at most a
 * hundred-odd keys, so snapshots are cheap and the most reliable.
 *
 * Coalescing: consecutive edits to the same row within 900ms push only one entry —
 * otherwise typing ten characters means ten undo steps and Ctrl+Z feels stuck.
 */
var undoState = { past: [], future: [], limit: 50, lastKey: '', lastAt: 0, applying: false, grouping: false };

/**
 * Drop the whole undo history, including the coalescing marker: otherwise an edit
 * to the same row within 900 ms after the reset would be folded into a step that
 * no longer exists and get no undo entry of its own.
 */
function resetUndoHistory() {
  undoState.past.length = 0;
  undoState.future.length = 0;
  undoState.lastKey = '';
  undoState.lastAt = 0;
  if (typeof updateUndoButtons === 'function') updateUndoButtons();
}

function cloneValueMap(map) {
  var out = {};
  if (map && typeof map === 'object') {
    Object.keys(map).forEach(function (key) { out[key] = map[key]; });
  }
  return out;
}

function undoSnapshot() {
  var edited = state.edited || {};
  var cleared = state.cleared || {};
  return {
    edited: { light: cloneValueMap(edited.light), dark: cloneValueMap(edited.dark) },
    cleared: { light: cloneValueMap(cleared.light), dark: cloneValueMap(cleared.dark) }
  };
}

function undoPush(key, force) {
  if (undoState.applying || undoState.grouping) return;
  var now = (typeof Date.now === 'function') ? Date.now() : new Date().getTime();
  if (!force && key && undoState.lastKey === key && now - undoState.lastAt < 900) {
    undoState.lastAt = now;
    return;
  }
  undoState.lastKey = key || '';
  undoState.lastAt = now;
  undoState.past.push(undoSnapshot());
  if (undoState.past.length > undoState.limit) undoState.past.shift();
  undoState.future = [];
  updateUndoButtons();
}

/**
 * Run a multi-row action as ONE undo step: push a single snapshot up front, then mute the
 * per-row undoPush calls made by markEdited / markCleared inside fn (each has a different
 * key, so coalescing would not fold them and one click would need several Ctrl+Z).
 */
function undoGroup(fn) {
  undoPush('', true);
  undoState.grouping = true;
  try {
    fn();
  } finally {
    undoState.grouping = false;
  }
}

function undoApply(snapshot) {
  undoState.applying = true;
  state.edited = snapshot.edited;
  state.cleared = snapshot.cleared;
  markAllRowsValid();
  clearRgbMemory();
  syncAll();
  undoState.applying = false;
  renderChanges();
  updateUndoButtons();
}

function undoStep() {
  if (state.busy) return;
  if (!undoState.past.length) {
    showToast(tsT('core.undo.nothingToUndo', '没有可撤销的改动了'));
    return;
  }
  undoState.future.push(undoSnapshot());
  undoApply(undoState.past.pop());
  showToast(tsT('core.undo.undone', '已撤销上一步'), 'ok');
}

function redoStep() {
  if (state.busy) return;
  if (!undoState.future.length) {
    showToast(tsT('core.undo.nothingToRedo', '没有可重做的改动了'));
    return;
  }
  undoState.past.push(undoSnapshot());
  undoApply(undoState.future.pop());
  showToast(tsT('core.undo.redone', '已重做'), 'ok');
}

function updateUndoButtons() {
  var undoBtn = $('btn-undo');
  var redoBtn = $('btn-redo');
  if (undoBtn) {
    undoBtn.disabled = !!state.busy || !undoState.past.length;
    undoBtn.title = undoState.past.length
      ? tsT('core.undo.undoTitle', '撤销上一次改动（还能撤销 {{count}} 步）', { count: undoState.past.length })
      : tsT('core.undo.undoTitleEmpty', '没有可撤销的改动');
  }
  if (redoBtn) {
    redoBtn.disabled = !!state.busy || !undoState.future.length;
    redoBtn.title = undoState.future.length
      ? tsT('core.undo.redoTitle', '重做（还能重做 {{count}} 步）', { count: undoState.future.length })
      : tsT('core.undo.redoTitleEmpty', '没有可重做的改动');
  }
}

function initUndoShortcuts() {
  document.addEventListener('keydown', function (event) {
    if (event.defaultPrevented || event.isComposing || activeEditorDialog()) return;
    var meta = event.ctrlKey || event.metaKey;
    if (!meta) return;
    var target = event.target;
    var tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
    // Inputs have the browser's own undo history; hijacking it would only confuse people.
    if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
        (target && target.isContentEditable)) return;
    var key = String(event.key || '').toLowerCase();
    if (key === 'z' && !event.shiftKey) {
      event.preventDefault();
      undoStep();
    } else if ((key === 'z' && event.shiftKey) || key === 'y') {
      event.preventDefault();
      redoStep();
    }
  });
}

/* ===== UNDO_END ===== */


// Store only unapplied palette and metadata edits. Material drafts are owned by
// the material editor and may contain a different saved baseline.
var DRAFT_KEY = 'neko.themeStudio.draft';
var draftReady = false;
var draftTimer = 0;
var lastDraftRaw = null;
function readDraft() {
  try {
    lastDraftRaw = localStorage.getItem(DRAFT_KEY);
    var value = JSON.parse(lastDraftRaw);
    if (value && value.v === 1 && Date.now() - value.ts < 7 * 86400000) return value;
  } catch (error) { /* Storage is optional. */ }
  return null;
}

function saveDraftNow() {
  if (!draftReady || !state.palette) return;
  clearTimeout(draftTimer);
  try {
    if (!dirtyCount() && !metaDirtyCount()) {
      if (lastDraftRaw !== null && localStorage.getItem(DRAFT_KEY) === lastDraftRaw) localStorage.removeItem(DRAFT_KEY);
      lastDraftRaw = null;
      return;
    }
    var draft = {
      v: 1, ts: Date.now(), revision: state.palette.revision, edited: state.edited, cleared: state.cleared
    };
    var name = $('theme-name');
    var enabled = $('theme-enabled');
    if (name && name.value !== state.metaName) draft.name = name.value;
    if (enabled && enabled.checked !== state.metaEnabled) draft.enabled = enabled.checked;
    lastDraftRaw = JSON.stringify(draft);
    localStorage.setItem(DRAFT_KEY, lastDraftRaw);
  } catch (error) { /* The editor also works with storage disabled. */ }
}

function scheduleDraftSave() {
  if (!draftReady) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraftNow, 300);
}

function restoreDraftIfAny() {
  if (draftReady || !state.palette) return;
  var draft = readDraft();
  draftReady = true;
  if (!draft || dirtyCount() || metaDirtyCount()) return;
  if (draft.revision && draft.revision !== state.palette.revision &&
      !window.confirm(tsT('workflow.draftConflict', 'The saved theme changed. Restore the previous draft on top of it?'))) {
    saveDraftNow();
    return;
  }
  ['light', 'dark'].forEach(function (mode) {
    ['edited', 'cleared'].forEach(function (field) {
      var values = draft[field] && draft[field][mode];
      if (!values || typeof values !== 'object') return;
      Object.keys(values).forEach(function (key) {
        if (!Object.prototype.hasOwnProperty.call(CATALOG_BY_KEY, key)) return;
        if (field === 'cleared' && values[key] === true) state.cleared[mode][key] = true;
        if (field === 'edited' && typeof values[key] === 'string') state.edited[mode][key] = values[key];
      });
    });
  });
  if (typeof draft.name === 'string') $('theme-name').value = draft.name;
  if (typeof draft.enabled === 'boolean') $('theme-enabled').checked = draft.enabled;
  syncAll();
  applyFilters();
  var count = pendingChangeCount();
  if (count) showNotice('ok', tsT('workflow.draftRestored', 'Restored {{count}} unsaved changes', {count: count}));
}

function samePaletteValue(a, b) {
  if (a === b) return true;
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  var x = parseColor(a);
  var y = parseColor(b);
  return !!(x && y && x.r === y.r && x.g === y.g && x.b === y.b && Math.abs(x.a - y.a) < 0.004);
}

function markEditedForMode(mode, key, value) {
  if (mode === state.mode) { markEdited(key, value); return; }
  delete state.cleared[mode][key];
  var saved = state.palette && (mode === 'dark' ? state.palette.dark_vars : state.palette.vars);
  if (saved && samePaletteValue(saved[key], value)) delete state.edited[mode][key];
  else state.edited[mode][key] = value;
}

function draftPaletteBoth(extracted, seeds) {
  var light = p55BuildVars(extracted, 'light', seeds);
  var dark = p55BuildVars(extracted, 'dark', seeds);
  undoGroup(function () {
    [['light', light], ['dark', dark]].forEach(function (pair) {
      Object.keys(pair[1].vars).forEach(function (key) {
        markEditedForMode(pair[0], key, pair[1].vars[key]);
      });
    });
  });
  syncAll();
  return {light: light, dark: dark, notes: light.notes.concat(dark.notes)};
}

var PREVIEW_PALETTE_KEYS = [
  '--nts-chat-bg', '--nts-chat-bg-image', '--nts-panel-bg', '--nts-panel-text',
  '--nts-ai-bubble-bg', '--nts-ai-bubble-text', '--nts-user-bubble-bg', '--nts-user-bubble-text',
  '--nts-accent', '--nts-topbar-bg', '--nts-topbar-text'
];
var previewPaletteSig = '';
function syncPreviewPalette(force) {
  if (!$('mt-pv')) return;
  var values = effectiveVars(state.mode);
  var vars = {};
  PREVIEW_PALETTE_KEYS.forEach(function (key) {
    if (values[key]) vars[key] = values[key];
  });
  var sig = state.mode + JSON.stringify(vars);
  if (!force && sig === previewPaletteSig) return;
  previewPaletteSig = sig;
  document.dispatchEvent(new CustomEvent('nts:palette-change', {detail: {mode: state.mode, vars: vars}}));
}

var lastApplyUndo = null;
function snapshotForUndo(palette) {
  return palette ? JSON.parse(JSON.stringify(palette)) : null;
}

function makeApplyUndo(snapshot, counts, note, response) {
  var revision = response && (response.revision || (response.palette && response.palette.revision));
  if (response && response.previous_revision && (!snapshot || snapshot.revision !== response.previous_revision)) return null;
  return snapshot && revision ? {palette: snapshot, revision: revision, counts: counts, note: note} : null;
}

function syncUndoApplyButton() {
  var btn = $('btn-undo-apply');
  if (btn) {
    btn.disabled = state.busy || !lastApplyUndo;
    btn.title = (lastApplyUndo && [lastApplyUndo.counts, lastApplyUndo.note].filter(Boolean).join('\n')) ||
      tsT('workflow.undoApply', 'Undo last apply');
  }
}

function undoVars(previous, current) {
  var values = Object.assign({}, previous);
  Object.keys(current || {}).forEach(function (key) {
    if (!Object.prototype.hasOwnProperty.call(values, key)) values[key] = '';
  });
  return values;
}

async function undoLastApply() {
  if (state.busy || !lastApplyUndo) return;
  var undo = lastApplyUndo;
  var old = undo.palette;
  setBusy(true, tsT('workflow.undoApply', 'Undo last apply'));
  try {
    var current = await callPlugin('get_theme', {});
    var result;
    if (current.revision === undo.revision) {
      result = await callPlugin('apply_theme', {
        base_revision: undo.revision, name: old.name, enabled: old.enabled, materials: old.materials,
        vars: undoVars(old.vars, current.vars), dark_vars: undoVars(old.dark_vars, current.dark_vars)
      });
    }
    if (!result || result.applied === false) {
      showNotice('warn', tsT('workflow.undoConflict', 'The saved theme changed. The last apply can no longer be undone.'));
    } else {
      clearPreviewState();
      pokeThemeWindows();
      await reloadPalette({silent: true, keepMaterialDrafts: true});
      showNotice('ok', tsT('workflow.undoDone', 'The previous theme was restored.'));
    }
    lastApplyUndo = null;
  } catch (error) { showWorkflowError(error); }
  finally { setBusy(false); }
}

function pokeThemeWindows() {
  try { localStorage.setItem('neko.themeStudio.poke', String(Date.now())); } catch (error) { /* Polling remains available. */ }
}
function showWorkflowError(error) {
  showNotice('error', tsT('workflow.operationFailed', 'Could not complete this action: {{error}}', {error: pluginErrorMessage(error)}));
}

var previewTimer = 0;
function previewActive() { return !!state.previewing; }
function previewRemaining() {
  return Math.max(0, Math.ceil((state.previewDeadline - Date.now()) / 1000)) || 0;
}
function syncPreviewButton() {
  var btn = $('btn-preview');
  var accept = $('btn-preview-accept');
  var left = previewRemaining();
  var time = Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
  if (btn) {
    btn.textContent = previewActive()
      ? tsT('workflow.previewStop', 'Stop preview ({{time}})', {time: time})
      : tsT('workflow.previewStart', 'Preview for 2 minutes');
    btn.setAttribute('aria-pressed', String(previewActive()));
    btn.disabled = state.busy;
  }
  if (accept) { accept.hidden = !previewActive(); accept.disabled = state.busy; }
}
function clearPreviewState() {
  state.previewing = null;
  state.previewDeadline = 0;
  clearInterval(previewTimer);
  syncPreviewButton();
}
async function togglePreview(options) {
  if (state.busy) return;
  setBusy(true);
  try {
    if (previewActive()) {
      await callPlugin('stop_preview', {id: state.previewing});
      clearPreviewState();
      showNotice('ok', tsT('workflow.previewStopped', 'Preview stopped.'));
    } else {
      if (invalidRowCount()) {
        showNotice('warn', tsT('actions.apply.invalidShort', 'Correct invalid entries first.'));
        return;
      }
      var seconds = Math.max(5, Math.min(600, Number(options && options.seconds) || 120));
      var result = await callPlugin('start_preview', {
        palette: buildArgs('light'), dark: buildArgs('dark'), seconds: seconds, materials: currentMaterialsPayload()
      });
      var preview = result.preview;
      seconds = preview.seconds || seconds;
      state.previewing = preview.id;
      state.previewDeadline = preview.expires_at * 1000;
      syncPreviewButton();
      previewTimer = setInterval(function () {
        if (Date.now() >= state.previewDeadline) clearPreviewState();
        else syncPreviewButton();
      }, 1000);
      showNotice('ok', tsT('workflow.previewActive', 'Other windows are previewing this draft for {{time}}.', {time: Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0')}));
    }
    pokeThemeWindows();
  } catch (error) { showWorkflowError(error); }
  finally { setBusy(false); }
}
async function acceptPreview() {
  if (previewActive() && Date.now() < state.previewDeadline) await applyTheme();
}

var livePrecheckTimer = null;
var livePrecheckSignature = '';
var livePrecheck = null;
function scheduleLivePrecheck() {
  var payload = dirtyCount() ? {palette: effectiveVars('light'), dark: effectiveVars('dark'), standalone: true} : null;
  var key = payload ? JSON.stringify(payload) : '';
  if (key === livePrecheckSignature) return;
  livePrecheckSignature = key;
  livePrecheck = null;
  clearTimeout(livePrecheckTimer);
  if (!key) return;
  livePrecheckTimer = setTimeout(async function () {
    try {
      var result = await callPlugin('check_contrast', payload);
      if (livePrecheckSignature !== key) return;
      livePrecheck = result;
      renderChanges();
    } catch (error) { /* Readability hints are optional. */ }
  }, 400);
}
function renderLivePrecheckRow(list) {
  var fails = livePrecheck && livePrecheck.fails;
  if (!fails || !fails.length) return;
  var first = fails[0];
  var row = el('li', {class: 'change-row change-precheck is-warn'});
  row.appendChild(el('span', {class: 'change-mode', text: tsT('workflow.contrast', 'Readability')}));
  row.appendChild(el('span', {class: 'change-label', text: tsT('workflow.below', 'Below target: {{count}}', {count: fails.length})}));
  row.appendChild(el('span', {class: 'change-delta', text: first.fg + ' · ' + first.ratio + ':1 / ' + first.floor + ':1'}));
  var jump = el('button', {type: 'button', class: 'btn btn-mini', text: tsT('workflow.goto', 'Go to setting')});
  jump.addEventListener('click', function () { setMode(first.mode); focusRowByKey(first.fg); });
  row.appendChild(jump);
  list.appendChild(row);
}
async function confirmContrast(light, dark) {
  try {
    var result = await callPlugin('check_contrast', {palette: light, dark: dark, standalone: true});
    if (!result.fails || !result.fails.length) return true;
    var details = result.fails.slice(0, 3).map(function (item) {
      return changeModeLabel(item.mode) + ' · ' + item.fg + ' / ' + item.bg + ' · ' + item.ratio + ':1 / ' + item.floor + ':1';
    });
    return window.confirm(tsT('workflow.applyAnyway', 'Some text falls below its contrast target. Apply anyway?') + '\n\n' + details.join('\n'));
  } catch (error) { return true; }
}

async function exportShareCard() {
  if (state.busy) return;
  if (pendingChangeCount() && !window.confirm(tsT('workflow.sharePending', 'The preview image uses the saved theme. Download it without your unsaved changes?'))) return;
  setBusy(true, tsT('actions.export.busy', 'Exporting…'));
  try {
    var result = downloadExportResult(await callPlugin('export_share_card', {}));
    showNotice('ok', savedHint(result));
  } catch (error) { showWorkflowError(error); }
  finally { setBusy(false); }
}

function markEdited(key, value) {
  undoPush(key);
  // Remember the last edited value that passed validation: invalid input on a non-color
  // row rolls back to it; otherwise Apply would still submit the previous valid value and
  // report success (the user would think the new input took effect).
  rowValidCache()[key] = value;
  delete clearedMap()[key];
  // Changing back to the server's value = unchanged: otherwise the button would still say
  // "changed" after retyping it, and Apply would write the same value again (version +1,
  // making users think something broke).
  if (samePaletteValue(serverValue(key), value)) {
    delete editedMap()[key];
    return;
  }
  editedMap()[key] = value;
}

function markCleared(key) {
  undoPush(key);
  delete editedMap()[key];
  delete rowValidCache()[key];
  markRowInvalid(key, false);
  clearedMap()[key] = true;
}

/**
 * "Clear" means "delete this entry from theme.json". If the server has no such entry,
 * clearing degrades to "discard the local edit" instead of recording an empty-string
 * submission (otherwise clicking Clear on a clean row would invent a change, and applying
 * it would bump the version without any visible difference).
 * @returns {boolean} true = a deletion will actually be submitted
 */
function clearRow(key) {
  var hasServer = serverValue(key) !== null;
  if (!hasServer) {
    delete editedMap()[key];
    delete clearedMap()[key];
    delete rowValidCache()[key];
    markRowInvalid(key, false);
    return false;
  }
  markCleared(key);
  return true;
}

/* ---------------- Per-row "invalid input" and "color memory" ----------------
 * 1) invalidRows: rows currently in the red invalid state. applyTheme refuses to apply
 *    when this is non-zero, avoiding "invalid input -> Apply still submits the previous
 *    valid value and reports success".
 * 2) rowValidCache: each row's last edited value that passed validation; on invalid input
 *    edited is rolled back to it (or the key is deleted = back to the server value), so
 *    the panel state matches what will be submitted.
 * 3) rgbMemory(): fully transparent rows remember their last RGB so the alpha slider
 *    still works while transparent (the old version greyed it out and users could never
 *    drag back to semi-transparent).
 * 2) and 3) are kept per mode (light / dark are independent palettes, like state.edited):
 * a single map would roll a dark row back to its light value, or let the dark alpha slider
 * reuse the light RGB. Always go through rowValidCache() / rgbMemory(). */
var rowValidCacheByMode = { light: {}, dark: {} };
var invalidRows = {};
var lastRgbByMode = { light: {}, dark: {} };

function rowValidCache() { return rowValidCacheByMode[state.mode === 'dark' ? 'dark' : 'light']; }
function rgbMemory() { return lastRgbByMode[state.mode === 'dark' ? 'dark' : 'light']; }

function markRowInvalid(key, bad) {
  if (bad) invalidRows[key] = true;
  else delete invalidRows[key];
}

function invalidRowCount() { return Object.keys(invalidRows).length; }

function invalidInputHint(item) {
  if (item && item.type === 'blur') return tsT('core.validate.badBlur', '模糊只接受 px/rem/em/pt（可写 18px 或 blur(18px)）');
  return tsT('core.validate.badValue', '该值不合法，请改成合法值或点「清空」');
}

/** On invalid input, roll back to the last valid edited value; if none, drop the local edit (back to the server value). */
function rollbackRowToLastValid(key) {
  if (Object.prototype.hasOwnProperty.call(rowValidCache(), key)) {
    markEdited(key, rowValidCache()[key]);
  } else {
    delete editedMap()[key];
  }
}

function markAllRowsValid() { invalidRows = {}; }

function clearRgbMemory() { lastRgbByMode = { light: {}, dark: {} }; }

/** Remember a row's most recent RGB that carried color (the literal transparent has none, so it is skipped). */
function rememberRgbValue(key, parsed, raw) {
  if (!parsed) return;
  if (String(raw || '').trim().toLowerCase() === 'transparent') return;
  if (parsed.a > 0 || parsed.r || parsed.g || parsed.b) {
    rgbMemory()[key] = { r: parsed.r, g: parsed.g, b: parsed.b };
  }
}

/** Lenient input normalization: complete the most natural way people type a value into a
 *  form the validator accepts directly.
 *
 * - a bare number ``16`` in a ``length`` row -> ``16px`` (used to turn red; the unit had to be typed);
 * - a bare number ``7`` in a ``blur`` row -> ``blur(7px)`` (the backend already accepts
 *   ``blur(7px)`` and bare ``7px``, but not the unitless ``7``);
 * - everything else (with unit / percent / negative / multiple tokens) is returned as-is
 *   for ``isSafeValue()`` to judge (``-4px`` stays invalid: a negative radius makes the
 *   browser drop the whole declaration).
 *
 * Note: normalization happens only in the panel; backend and loader validation stay strict —
 * the API contract is unchanged, and hand-edited ``theme.json`` or JSON imports must still
 * use the full form.
 */
function normalizeTypedValue(item, raw) {
  if (!item) return raw;
  if (!/^-?[0-9]{1,4}(?:\.[0-9]+)?$/.test(raw)) return raw;
  if (item.type === 'length') return raw + 'px';
  if (item.type === 'blur') return 'blur(' + raw + 'px)';
  // Append s to a bare duration: CSS animation-duration rejects unitless numbers. The
  // backend appends it too, but doing it here means typing 3 shows 3s immediately.
  if (item.type === 'time') return raw + 's';
  return raw;
}

/** Set an input's red border / accessible invalid flag / hint title in one place. */
function setInputInvalid(input, item, bad) {
  if (!input) return;
  input.classList.toggle('is-bad', !!bad);
  if (bad) {
    input.setAttribute('aria-invalid', 'true');
    input.title = invalidInputHint(item);
  } else {
    input.removeAttribute('aria-invalid');
    input.title = '';
  }
}

/* ============================================================================
 * 6. DOM helpers
 * ==========================================================================*/

function el(tag, props, children) {
  var node = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.indexOf('on') === 0 && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
  }
  (children || []).forEach(function (c) { if (c) node.appendChild(c); });
  return node;
}

function $(id) { return document.getElementById(id); }

/** kind: 'ok' | 'error' | empty — affects colours only, not the text. */
function showToast(message, kind) {
  var node = $('toast');
  node.textContent = message;
  node.classList.toggle('is-ok', kind === 'ok');
  node.classList.toggle('is-error', kind === 'error');
  node.classList.add('show');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () { node.classList.remove('show'); }, 3000);
}

/** kind: 'idle' | 'busy' | 'ok' | 'error' | 'warn' — colour of the footer status dot. */
function setStatus(text, kind) {
  var node = $('status');
  if (!node) return;
  node.textContent = text;
  node.dataset.kind = kind || 'idle';
}

/** kind: 'ok' | 'warn' | 'error' — left accent bar and colours. Empty text hides the notice. */
function showNotice(kind, text, action) {
  var button = $('notice-action');
  if (button) {
    button.hidden = !text || !action;
    button.textContent = action ? action.label : '';
    button.onclick = action && (action.handler || action.onClick);
  }
  var node = $('notice');
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.className = 'notice';
    var body = $('notice-text');
    if (body) body.textContent = '';
    return;
  }
  node.className = 'notice is-' + kind;
  var target = $('notice-text') || node;
  target.textContent = text;
  node.hidden = false;
}

function hideNotice() { showNotice('ok', ''); }

/** Thin top progress bar: a non-blocking "busy" signal while loading/applying. */
function setLoading(loading) {
  document.body.classList.toggle('is-loading', !!loading);
}

/* ============================================================================
 * 7. Editor rendering
 * ==========================================================================*/

var rowsByKey = {};   // key → { hexInput, colorInput, alpha, alphaVal, textInput, rowEl, syncFn }

var groupNodes = {};   // group name → <section class="group">

function buildEditor() {
  var host = $('groups');
  host.textContent = '';

  CATALOG.forEach(function (group) {
    var key = groupStateKey(group);
    var body = el('div', { class: 'group-body' });
    var count = el('span', { class: 'group-count' });
    var head = el('div', {
      class: 'group-head',
      role: 'button',
      tabindex: '0',
      'aria-expanded': state.collapsed[key] ? 'false' : 'true',
      'aria-label': tsT('editor.group.ariaLabel', '{{group}} 分组（点击展开/收起）', { group: group.group }),
      onclick: function () { toggleGroup(key); },
      onkeydown: function (event) {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
          event.preventDefault();
          toggleGroup(key);
        }
      }
    }, [
      el('span', { class: 'group-caret', 'aria-hidden': 'true', text: '▶' }),
      el('span', { class: 'group-title', text: group.group }),
      count
    ]);
    var wrap = el('section', { class: 'group' + (state.collapsed[key] ? ' is-collapsed' : '') }, [head, body]);
    groupNodes[key] = wrap;

    group.items.forEach(function (item) { body.appendChild(buildRow(item, count)); });

    host.appendChild(wrap);
  });

  refreshCounts();
  updateGroupToggleLabel();
}

function toggleGroup(key) {
  state.collapsed[key] = !state.collapsed[key];
  var node = groupNodes[key];
  if (node) {
    node.classList.toggle('is-collapsed', !!state.collapsed[key]);
    var head = node.querySelector('.group-head');
    if (head) head.setAttribute('aria-expanded', state.collapsed[key] ? 'false' : 'true');
  }
  // Persist collapse state to localStorage so the panel reopens where the user left off.
  saveCatalogCollapsed();
  updateGroupToggleLabel();
}

function allCollapsed() {
  return CATALOG.every(function (group) { return !!state.collapsed[groupStateKey(group)]; });
}

function updateGroupToggleLabel() {
  var btn = $('btn-toggle-groups');
  if (!btn) return;
  var collapsed = allCollapsed();
  btn.textContent = collapsed
    ? tsT('editor.groups.expandAll', '全部展开')
    : tsT('editor.groups.collapseAll', '全部收起');
  // a11y (v0.4.21): this button controls every group, so add aria-expanded to tell
  // screen readers "all collapsed"; visually the button text already says the same.
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}

function toggleAllGroups() {
  var collapse = !allCollapsed();
  CATALOG.forEach(function (group) {
    state.collapsed[groupStateKey(group)] = collapse;
    var node = groupNodes[groupStateKey(group)];
    if (!node) return;
    node.classList.toggle('is-collapsed', collapse);
    var head = node.querySelector('.group-head');
    if (head) head.setAttribute('aria-expanded', collapse ? 'false' : 'true');
  });
  saveCatalogCollapsed();
  updateGroupToggleLabel();
}

/** Native reference value for the current mode: darkNative in dark mode, else the light native value. */
function nativeOf(item) {
  if (state.mode === 'dark' && item.darkNative !== undefined) return item.darkNative;
  return item.native || '';
}

/** Placeholder shows the native reference value; long ones are left out so they don't look like noise. */
function placeholderFor(item) {
  var native = nativeOf(item);
  if (native && native.length <= 28) return tsT('editor.row.nativeValue', '原生 {{value}}', { value: native });
  return tsT('editor.row.emptyIsNative', '留空 = 原生');
}

/* ---------------- Preview strip and gradient quick-builder for filter / gradient rows ----------------
 * style.css was off-limits this round, so the preview strip and quick-builder use inline styles only. */

var PREVIEW_BASE_STYLE = 'position:relative;flex:0 0 84px;height:26px;border-radius:6px;' +
  'overflow:hidden;border:1px solid rgba(128,128,128,0.35);' +
  'background:repeating-linear-gradient(45deg, rgba(120,140,170,0.45) 0 5px, rgba(120,140,170,0.12) 5px 10px);';

/** Build a preview strip over a diagonal-stripe backdrop: filter rows blur it with backdrop-filter;
 *  gradient rows use the stripes as the "not configured" placeholder. Returns { host, node }. */
function buildPreviewHost(title) {
  var host = el('span', {
    class: 'live-preview',
    'aria-hidden': 'true',
    title: title,
    style: PREVIEW_BASE_STYLE
  });
  var node = el('span', { style: 'position:absolute;top:0;left:0;right:0;bottom:0;' });
  host.appendChild(node);
  return { host: host, node: node };
}

/** Sync the preview strip to the effective value; empty/invalid clears the effect and leaves only the stripes (= no preview). */
function syncValuePreview(ref, value) {
  if (!ref || !ref.previewNode) return;
  var v = value === null || value === undefined ? '' : String(value).trim();
  var ok = false;
  if (ref.item.type === 'filter') {
    ok = !!v && isSafeFilterChain(v);
    ref.previewNode.style.backdropFilter = ok ? v : '';
    ref.previewNode.style.webkitBackdropFilter = ok ? v : '';
    if (ref.previewHost) {
      ref.previewHost.title = ok
        ? tsT('editor.preview.filterTitle', '预览：{{value}}（只对预览条背后的斜纹生效）', { value: v })
        : tsT('editor.preview.filterNone', '当前没有可预览的滤镜链（留空 = 不生效）');
    }
  } else if (ref.item.type === 'gradient') {
    ok = !!v && isSafeGradient(v);
    ref.previewNode.style.backgroundImage = ok ? v : '';
    if (ref.previewHost) {
      ref.previewHost.title = ok
        ? tsT('editor.preview.gradientTitle', '预览：{{value}}', { value: v })
        : tsT('editor.preview.gradientNone', '当前没有可预览的渐变（留空 = 不生效）');
    }
  }
}

/** "Angle + from/to colours" → write into the text box, then dispatch input/change to reuse the normal
 *  input flow (validation, dirty marking and preview all share one code path). */
function composeGradient(textInput, angleInput, fromInput, toInput) {
  var angle = parseInt(angleInput.value, 10);
  if (!isFinite(angle)) angle = 135;
  angle = Math.round(clamp(angle, 0, 359));
  textInput.value = 'linear-gradient(' + angle + 'deg, ' + fromInput.value + ' 0%, '
    + toInput.value + ' 100%)';
  textInput.dispatchEvent(new Event('input', { bubbles: true }));
  textInput.dispatchEvent(new Event('change', { bubbles: true }));
  if (typeof textInput.focus === 'function') textInput.focus();
}

/** Whether focus is inside this row (typing / dragging a slider / using the quick-builder). */
function rowHoldsFocus(ref) {
  var active = document.activeElement;
  if (!active || active === document.body) return false;
  return !!(ref && ref.container && ref.container.contains(active));
}

function buildRow(item, countNode) {
  var isColor = item.type === 'color';
  var isImage = item.type === 'image';
  var isSwitch = item.type === 'switch';
  var isSelect = item.type === 'select';
  var isFilter = item.type === 'filter';
  var isGradient = item.type === 'gradient';

  var stateTag = el('span', { class: 'row-state' });
  var labelMain = el('span', { class: 'label-main', text: item.label });

  var darkFlag = el('span', { class: 'row-darkflag', hidden: true, text: tsT('rows.diffFlag','Dark ≠ light'),
    title: tsT('rows.diffHint', 'Dark override differs from the light value.') });
  var keyNode = el('span', { class: 'k', text: item.key });
  var label = el('div', { class: 'row-label', title: item.key }, [
    el('span', { class: 'label-line' }, [
      labelMain, stateTag, darkFlag
    ]),
    keyNode
  ]);

  var colorInput = null;
  var hexInput = null;
  var alpha = null;
  var alphaVal = null;
  var textInput = null;
  var nativeCell = null;
  var imageField = null;
  var imgSwatch = null;
  var switchInput = null;
  var switchField = null;
  var selectInput = null;
  var selectField = null;
  var previewHost = null;   // preview strip shell on the right of filter / gradient rows
  var previewNode = null;   // the layer that actually carries backdrop-filter / background-image
  var quickRow = null;      // "angle + from/to + generate" quick-builder of gradient rows
  var gradAngle = null;
  var gradFrom = null;
  var gradTo = null;
  var gradGen = null;

  if (isSwitch) {
    // Switch control: checked = 1 (native look), unchecked = 0 (fully clear).
    // Stored in edited as the strings '1' / '0' so it shares backend validation with number vars.
    switchInput = el('input', {
      type: 'checkbox', class: 'veil-switch',
      'aria-label': item.label
    });
    switchInput.addEventListener('change', function () {
      markEdited(item.key, switchInput.checked ? '1' : '0');
      syncRow(item.key);
    });
    switchField = el('div', { class: 'field switch-field' }, [
      switchInput,
      el('span', { class: 'switch-text', text: tsT('editor.switch.label', '开关') })
    ]);
    nativeCell = el('span', {
      class: 'row-native',
      title: tsT('editor.switch.nativeTitle', '原生观感 = 打开'),
      text: tsT('editor.switch.nativeOn', '原生 开')
    });
  }

  if (isSelect) {
    // Select variables: options are fixed in CATALOG's options; markEdited right after choosing.
    // Like color/switch they go through the edited table; "Apply" still does the submit.
    selectInput = el('select', {
      class: 'fit-select',
      'aria-label': item.label
    });
    (item.options || []).forEach(function (opt) {
      selectInput.appendChild(el('option', { value: opt.value, text: opt.label }));
    });
    selectInput.addEventListener('change', function () {
      markEdited(item.key, selectInput.value);
      syncRow(item.key);
    });
    selectField = el('div', { class: 'field' }, [selectInput]);
    nativeCell = el('span', {
      class: 'row-native',
      title: tsT('editor.row.nativeSyntax', '原生写法：{{value}}', { value: nativeOf(item) || '' }),
      text: tsT('editor.row.nativeValue', '原生 {{value}}', { value: selectOptionLabel(item, nativeOf(item)) })
    });
  }

  if (isColor) {
    colorInput = el('input', {
      type: 'color', class: 'swatch', value: '#000000',
      'aria-label': tsT('editor.color.swatchAria', '{{label}} 色板', { label: item.label })
    });
    hexInput = el('input', {
      type: 'text',
      class: 'hex',
      spellcheck: 'false',
      autocomplete: 'off',
      placeholder: placeholderFor(item),
      'aria-label': tsT('editor.color.valueAria', '{{label}} 颜色值', { label: item.label }),
      onfocus: function () {
        // Alpha of the value before this typing session: plain 6/3-digit hex inherits it.
        // Captured once, because mid-typing drafts change it (the 4-digit prefix "#ff88" of
        // "#ff8800" is a 53% colour, which currentAlpha() would then hand to "#ff8800").
        hexInput.dataset.baseAlpha = String(currentAlpha(item.key));
      },
      oninput: function () {
        var raw = hexInput.value.trim();
        if (!raw) { clearRow(item.key); setInputInvalid(hexInput, item, false); syncRow(item.key, { keepHex: true, defer: true }); return; }
        if (raw.toLowerCase() === 'currentcolor') {
          // currentcolor is a valid CSS keyword (backend/loader accept it) but has no fixed RGB:
          // store the string as-is; never coerce it to black for the swatch, and never flag it red.
          setInputInvalid(hexInput, item, false);
          markRowInvalid(item.key, false);
          markEdited(item.key, 'currentcolor');
          syncRow(item.key, { keepHex: true, defer: true });
          return;
        }
        var parsed = parseColor(raw);
        if (!parsed) { setInputInvalid(hexInput, item, true); markRowInvalid(item.key, true); return; }
        setInputInvalid(hexInput, item, false);
        markRowInvalid(item.key, false);
        // Only 6/3-digit hex has no alpha of its own, so only it inherits the current alpha;
        // transparent, 4/8-digit hex and rgba(...) use the parsed alpha. The old code always used
        // currentAlpha(), which returns 1 for fully transparent → typing "transparent" got recorded
        // as rgba(0,0,0,1) solid black, and Ctrl+Enter (no blur needed) wrote it to disk.
        rememberRgbValue(item.key, parsed, raw);
        markEdited(item.key, hexDraftValue(parsed, raw));
        // keepHex: keep the user's in-progress text, otherwise "#ff8" gets rewritten as a 3-digit
        // colour and later keystrokes build a value never typed (observed: #ff8800 → #ffff8880).
        syncRow(item.key, { keepHex: true, defer: true });
      },
      onchange: function () {
        // Blur/Enter finalises and normalises the display. The draft value itself follows the
        // same rule as oninput (hexDraftValue), so finishing with blur or applying straight
        // from the keyboard records the same colour.
        var raw = hexInput.value.trim();
        if (!raw) return;
        if (raw.toLowerCase() === 'currentcolor') {
          setInputInvalid(hexInput, item, false);
          markRowInvalid(item.key, false);
          markEdited(item.key, 'currentcolor');
          syncRow(item.key);
          return;
        }
        var parsed = parseColor(raw);
        if (!parsed) { setInputInvalid(hexInput, item, true); markRowInvalid(item.key, true); return; }
        setInputInvalid(hexInput, item, false);
        markRowInvalid(item.key, false);
        rememberRgbValue(item.key, parsed, raw);
        // Same rule as oninput, so blur and a quick apply (Ctrl+Enter, no blur) agree.
        markEdited(item.key, hexDraftValue(parsed, raw));
        syncRow(item.key);
      }
    });
    // Draft value of a typed colour, shared by oninput and onchange. Only 6/3-digit hex has
    // no alpha of its own, so it inherits the pre-typing alpha; everything else (transparent,
    // 4/8-digit hex, rgba(...)) uses its parsed alpha.
    var hexDraftValue = function (parsed, raw) {
      var isPlainHex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(raw);
      var base = parseFloat(hexInput.dataset.baseAlpha);
      var alphaOut = isPlainHex ? (isNaN(base) ? currentAlpha(item.key) : base) : parsed.a;
      return toRgba(parsed.r, parsed.g, parsed.b, alphaOut);
    };

    alpha = el('input', {
      type: 'range', min: '0', max: '100', step: '1', value: '100',
      'aria-label': tsT('editor.color.opacityAria', '{{label}} 不透明度', { label: item.label }),
      oninput: function () {
        var a = Number(alpha.value) / 100;
        var parsed = parseColor(displayValue(item.key));
        // No colour guessing: a fully transparent row that remembers its last RGB (rgbMemory())
        // reuses it so opacity can be dragged back up — the old code disabled the slider and forced
        // currentAlpha to 1, so the user could never get back to semi-transparent. Only when there
        // is no remembered RGB at all do we skip (otherwise it would be solid black).
        var rgb = parsed && parsed.a > 0 ? parsed : rgbMemory()[item.key];
        // Dragging the slider fires input repeatedly: update the row now, batch the global refresh per frame.
        if (!rgb) { syncRow(item.key, { defer: true }); return; }
        rememberRgbValue(item.key, rgb, '');
        markEdited(item.key, toRgba(rgb.r, rgb.g, rgb.b, a));
        syncRow(item.key, { defer: true });
      }
    });
    alphaVal = el('span', { class: 'alpha-val', text: '100%' });

    colorInput.addEventListener('input', function () {
      var parsed = parseColor(colorInput.value);
      if (!parsed) return;
      rememberRgbValue(item.key, parsed, '#');
      markEdited(item.key, toRgba(parsed.r, parsed.g, parsed.b, currentAlpha(item.key)));
      // Dragging the colour picker also fires input repeatedly: batch the global refresh to the next frame.
      syncRow(item.key, { defer: true });
    });
  } else if (isImage) {
    // Background image row: the only valid values are none or url("…/backgrounds/<file>"). A picked
    // image is saved by the backend and read back as url(...), then goes through the existing
    // markEdited / cleared flow — submit, dirty marking and collect() share one path; applyTheme submits as usual.
    imgSwatch = el('div', { class: 'img-swatch', 'aria-hidden': 'true' });
    var imageFileInput = el('input', {
      type: 'file',
      class: 'visually-hidden',
      accept: 'image/png,image/jpeg,image/webp,image/gif',
      tabindex: '-1',
      'aria-label': tsT('editor.image.fileAria', '{{label}} 选择图片文件', { label: item.label }),
      onchange: function () {
        var file = imageFileInput.files && imageFileInput.files[0];
        if (file) pickBackgroundImage(item, file, imageFileInput);
      }
    });
    var pickBtn = el('button', {
      type: 'button', class: 'btn btn-mini', 'data-act': 'pick', text: tsT('editor.image.pick', '选择图片'),
      'aria-label': tsT('editor.image.pickAria', '选择{{label}}', { label: item.label }),
      onclick: function () { if (imageFileInput) imageFileInput.click(); }
    });
    var clearImageBtn = el('button', {
      type: 'button', class: 'btn btn-mini btn-ghost', 'data-act': 'clear', text: tsT('editor.image.clear', '清除'),
      'aria-label': tsT('editor.image.clearAria', '清除{{label}}', { label: item.label }),
      onclick: function () {
        // Same as .row-clear: goes through the cleared flow; submitting "" removes the entry from theme.json.
        if (!isDirty(item.key) && serverValue(item.key) === null) {
          showToast(tsT('editor.row.alreadyNative', '该行本来就是原生值'));
          return;
        }
        var removed = clearRow(item.key);
        syncRow(item.key);
        if (!removed) showToast(tsT('editor.row.editReverted', '已撤销本行改动'));
      }
    });
    // The image control sits in column 2 and spans "swatch + editor" columns; there is no text input,
    // so this row has only 4 children (label / image control / native value / clear).
    imageField = el('div', {
      class: 'field image-field',
      style: 'grid-column: 2 / span 2;'
    }, [
      imgSwatch,
      el('div', { class: 'img-actions' }, [
        pickBtn,
        clearImageBtn,
        el('span', { class: 'img-hint', text: tsT('editor.image.hint', '图片会盖在背景色之上') })
      ]),
      imageFileInput
    ]);
    nativeCell = el('span', {
      class: 'row-native',
      title: nativeOf(item)
        ? tsT('editor.row.nativeValueTitle', '原生值：{{value}}', { value: nativeOf(item) })
        : tsT('editor.row.noUniformNative', '该变量没有统一的原生值'),
      text: nativeOf(item) ? tsT('editor.row.nativeValue', '原生 {{value}}', { value: nativeOf(item) }) : ''
    });
  } else {
    textInput = el('input', {
      type: 'text',
      class: 'raw',
      spellcheck: 'false',
      autocomplete: 'off',
      placeholder: placeholderFor(item),
      'aria-label': item.label,
      oninput: function () {
        // Note: never write a trimmed value back here. Multi-token values (shadows, font families)
        // are typed char by char; trimming the trailing space glues the next token onto the previous
        // one — observed "0 2px 4px red" recorded as "02px4px red", "Segoe UI" as "SegoeUI".
        // So validate the trimmed value but keep the user's raw text on display.
        var raw = textInput.value;
        var trimmed = normalizeTypedValue(item, raw.trim());
        if (!trimmed) { clearRow(item.key); setInputInvalid(textInput, item, false); syncRow(item.key, { keepText: true, defer: true }); return; }
        if (!isSafeValue(item.key, trimmed)) {
          // Flagging red is not enough: edited still holds the previous valid value, so applyTheme
          // would submit it and report "applied", making the user think the new value took effect.
          // Roll back to the last valid edit (or the server value) and record the row as invalid;
          // applyTheme refuses to apply while any row is flagged.
          setInputInvalid(textInput, item, true);
          markRowInvalid(item.key, true);
          rollbackRowToLastValid(item.key);
          syncRow(item.key, { keepText: true, defer: true });
          return;
        }
        setInputInvalid(textInput, item, false);
        markRowInvalid(item.key, false);
        markEdited(item.key, trimmed);
        syncRow(item.key, { keepText: true, defer: true });
      },
      onchange: function () {
        // Finalise on blur/Enter: strip surrounding whitespace and validate fully once more.
        var trimmed = normalizeTypedValue(item, textInput.value.trim());
        textInput.value = trimmed;
        if (!trimmed) { clearRow(item.key); setInputInvalid(textInput, item, false); syncRow(item.key); return; }
        if (!isSafeValue(item.key, trimmed)) {
          setInputInvalid(textInput, item, true);
          markRowInvalid(item.key, true);
          rollbackRowToLastValid(item.key);
          syncRow(item.key);
          return;
        }
        setInputInvalid(textInput, item, false);
        markRowInvalid(item.key, false);
        markEdited(item.key, trimmed);
        syncRow(item.key);
      }
    });
    if (isFilter || isGradient) {
      // Preview strip shares the line with the input: the input flexes, the strip has a fixed
      // width (inline styles, since style.css was off-limits this round).
      textInput.style.flex = '1 1 140px';
      textInput.style.width = 'auto';
      var previewParts = buildPreviewHost(
        isFilter
          ? tsT('editor.preview.filterHost', '滤镜预览：背后的斜纹会被当前模糊链处理')
          : tsT('editor.preview.gradientHost', '渐变预览')
      );
      previewHost = previewParts.host;
      previewNode = previewParts.node;
    }
    if (isGradient) {
      // Quick-builder: angle + from/to + generate. Writes into the input only when "Generate" is
      // clicked — otherwise opening the panel would conjure up an edit out of nowhere.
      gradAngle = el('input', {
        type: 'number', min: '0', max: '360', step: '1', value: '135',
        'aria-label': tsT('editor.gradient.angleAria', '{{label}} 渐变角度', { label: item.label }),
        style: 'flex:0 0 64px;width:64px;height:26px;padding:0 6px;'
      });
      gradFrom = el('input', {
        type: 'color', class: 'swatch', value: '#eef4ff',
        'aria-label': tsT('editor.gradient.fromAria', '{{label}} 起始色', { label: item.label }),
        style: 'flex:0 0 28px;width:28px;height:26px;'
      });
      gradTo = el('input', {
        type: 'color', class: 'swatch', value: '#dfe9ff',
        'aria-label': tsT('editor.gradient.toAria', '{{label}} 结束色', { label: item.label }),
        style: 'flex:0 0 28px;width:28px;height:26px;'
      });
      gradGen = el('button', {
        type: 'button', class: 'btn btn-mini', text: tsT('editor.gradient.generate', '生成'),
        title: tsT('editor.gradient.generateTitle', '按角度与起止色生成 linear-gradient 并写入输入框'),
        'aria-label': tsT('editor.gradient.generateAria', '生成{{label}}', { label: item.label }),
        onclick: function () { composeGradient(textInput, gradAngle, gradFrom, gradTo); }
      });
      quickRow = el('div', {
        style: 'display:flex;align-items:center;gap:6px;flex:1 1 100%;min-width:0;'
      }, [
        el('span', { style: 'flex:none;font-size:11px;color:var(--text-faint);', text: tsT('editor.gradient.angle', '角度') }),
        gradAngle,
        gradFrom,
        gradTo,
        gradGen
      ]);
    }
    nativeCell = el('span', {
      class: 'row-native',
      title: nativeOf(item)
        ? tsT('editor.row.nativeValueTitle', '原生值：{{value}}', { value: nativeOf(item) })
        : tsT('editor.row.noUniformNative', '该变量没有统一的原生值'),
      text: nativeOf(item) ? tsT('editor.row.nativeValue', '原生 {{value}}', { value: nativeOf(item) }) : ''
    });
  }

  var field;
  if (imageField) {
    field = imageField;
  } else if (switchField) {
    field = switchField;
  } else if (selectField) {
    field = selectField;
  } else if (previewHost) {
    // filter / gradient rows: input + preview strip on line one, gradient quick-builder wraps to line two (row-gap spaces it).
    field = el('div', { class: 'field', style: 'flex-wrap:wrap;row-gap:6px;' },
      [isColor ? hexInput : textInput, previewHost].concat(quickRow ? [quickRow] : []));
  } else {
    field = el('div', { class: 'field' }, [isColor ? hexInput : textInput]);
  }

  var alphaCell = el('div', { class: 'alpha-cell' });
  if (isColor) {
    alphaCell.appendChild(el('span', { class: 'alpha-label', text: tsT('editor.color.alphaLabel', '透明') }));
    alphaCell.appendChild(alpha);
    alphaCell.appendChild(alphaVal);
  }

  var clearBtn = el('button', {
    type: 'button', class: 'row-clear', text: tsT('editor.row.clear', '清空'),
    title: tsT('editor.row.clearTitle', '清空该行 = 回退到 N.E.K.O 原生值'),
    'aria-label': tsT('editor.row.clearAria', '清空 {{label}}', { label: item.label }),
    onclick: function () {
      // Clean row with no server value: "Clear" has nothing to do and must not invent an edit.
      if (!isDirty(item.key) && serverValue(item.key) === null) {
        showToast(tsT('editor.row.alreadyNative', '该行本来就是原生值'));
        return;
      }
      var removed = clearRow(item.key);
      syncRow(item.key);
      if (!removed) showToast(tsT('editor.row.editReverted', '已撤销本行改动'));
    }
  });

  var copyBtn = el('button', {
    type: 'button', class: 'row-icon row-copy', text: '⧉',
    title: tsT('editor.row.copyTitle', '复制这一行的值（可以粘贴到别的行，甚至另一组亮/暗）'),
    'aria-label': tsT('editor.row.copyAria', '复制 {{label}}', { label: item.label }),
    onclick: function () { copyRowValue(item); }
  });
  var pasteBtn = el('button', {
    type: 'button', class: 'row-icon row-paste', text: '⇩',
    title: tsT('editor.row.pasteTitle', '把上次复制（或剪贴板里）的值粘贴到这一行'),
    'aria-label': tsT('editor.row.pasteAria', '粘贴到 {{label}}', { label: item.label }),
    onclick: function () { pasteRowValue(item); }
  });
  var copyLightTitle = tsT('rows.copyLight', 'Copy light value to dark');
  var toLightBtn = el('button', {
    type: 'button', class: 'row-icon row-to-light', hidden: true, text: '=',
    title: copyLightTitle, 'aria-label': copyLightTitle,
    onclick: function () { copyLightIntoDark(item.key); }
  });
  var rowExtra = el('div', { class: 'row-extra' }, [toLightBtn, copyBtn, pasteBtn, clearBtn]);

  // Five grid columns: label | swatch or type tag | editor | opacity or native value | copy/paste/clear
  // Non-colour rows have no swatch; a same-width type tag fills in so input left edges line up.
  // Image rows are special: the image control spans "swatch + editor" (see image-field above),
  // so the row has 4 children instead of 5.
  var rowChildren;
  if (isImage) {
    rowChildren = [label, imageField, nativeCell || el('span', {}), rowExtra];
  } else {
    rowChildren = [
      label,
      isColor && colorInput ? colorInput : el('span', { class: 'type-tag', text: TYPE_LABEL[item.type] || item.type }),
      field,
      isColor ? alphaCell : (nativeCell || el('span', {})),
      rowExtra
    ];
  }
  var rowEl = el('div', { class: 'row' }, rowChildren);
  [copyBtn, pasteBtn, clearBtn].forEach(function (node) { node.tabIndex = -1; });
  if (colorInput && hexInput) colorInput.tabIndex = -1;
  rowEl.addEventListener('keydown', function (event) { handleRowKeydown(event, item); });

  var hint = null;
  if (item.hint) {
    hint = el('div', { class: 'hint', title: item.hint, text: item.hint });
  }

  var container = el('div', { class: 'row-wrap', 'data-key': item.key }, [rowEl, hint]);
  // With "Only changed" on, typing a value back to the original makes the row clean; hiding it
  // right away would make the row vanish under the user and drop input focus. Keep it until focus
  // really leaves, then let focusout remove it (see rowHoldsFocus in applyFilters).
  container.addEventListener('focusout', function () {
    if (!state.onlyDirty && !state.onlyDarkDiff) return;
    window.setTimeout(function () {
      if (container.contains(document.activeElement)) return;
      applyFilters();
    }, 0);
  });
  rowsByKey[item.key] = {
    rowEl: rowEl, colorInput: colorInput, hexInput: hexInput,
    alpha: alpha, alphaVal: alphaVal, textInput: textInput, clearBtn: clearBtn,
    nativeCell: nativeCell, stateTag: stateTag, container: container,
    imgSwatch: imgSwatch, imageField: imageField,
    switchInput: switchInput,
    selectInput: selectInput,
    previewHost: previewHost, previewNode: previewNode,
    gradAngle: gradAngle, gradFrom: gradFrom, gradTo: gradTo, gradGen: gradGen,
    item: item, countNode: countNode, labelMain: labelMain, keyNode: keyNode, hintNode: hint,
    darkFlag: darkFlag, toLightBtn: toLightBtn
  };

  countNode.dataset.total = String(Number(countNode.dataset.total || 0) + 1);
  return container;
}

/* ---------------- Background image upload (chat background row only) ----------------
 * An image can't be written into a variable like a colour: the file is first saved by the backend
 * (size/type checked), then its /api/theme_studio/backgrounds/<file> URL is wrapped as url("...").
 * So this only does "read file → call import_background → markEdited"; applyTheme still submits. */

function pickBackgroundImage(item, file, input) {
  // Take the busy lock before reading the file, not after: otherwise a second upload or an
  // apply could start while FileReader is still running and interleave with this one.
  if (state.busy) {
    if (input) input.value = '';
    return;
  }
  setBusy(true, tsT('editor.image.uploading', '正在上传背景图片…'));
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (!base64) {
      showToast(tsT('editor.image.readFailed', '读取图片失败'), 'error');
      if (input) input.value = '';
      setBusy(false);
      return;
    }
    uploadBackgroundImage(item, file.name, base64, input);
  };
  reader.onerror = function () {
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', tsT('editor.image.readFailedDetail', '读取图片失败：{{reason}}', { reason: why }));
    showToast(tsT('editor.image.readFailed', '读取图片失败'), 'error');
    if (input) input.value = '';
    setBusy(false);
  };
  reader.readAsDataURL(file);
}

/** Upload step; the caller (pickBackgroundImage) already holds the busy lock, released here. */
async function uploadBackgroundImage(item, filename, base64, input) {
  setBusy(true, tsT('editor.image.uploading', '正在上传背景图片…'));
  try {
    var result = await callPlugin('import_background', { filename: filename, data: base64 });
    var url = result && result.url ? String(result.url) : '';
    if (!url) throw new Error(tsT('editor.image.noUrl', '后端没有返回图片地址'));
    markEdited(item.key, 'url("' + url + '")');
    syncRow(item.key);
    setStatus(tsT('editor.image.selected', '已选择背景图片'), 'ok');
    showToast(tsT('editor.image.selected', '已选择背景图片'), 'ok');
  } catch (error) {
    showNotice('error', tsT('editor.image.uploadFailedDetail', '背景图片上传失败：{{reason}}', { reason: pluginErrorMessage(error) }));
    showToast(tsT('editor.image.uploadFailed', '背景图片上传失败'), 'error');
    setStatus(tsT('editor.image.uploadFailed', '背景图片上传失败'), 'error');
  } finally {
    // Reset the input, otherwise picking the same file again won't fire change.
    if (input) input.value = '';
    setBusy(false);
  }
}

function currentAlpha(key) {
  var raw = Object.prototype.hasOwnProperty.call(editedMap(), key) ? editedMap()[key] : displayValue(key);
  var parsed = parseColor(raw);
  // When the current value is fully transparent (transparent / rgba(...,0)), don't pass alpha=0 on
  // to the new colour: the user picks a solid colour but rgba(...,0) gets submitted, the panel shows
  // "changed" while the page stays fully transparent — it looks like nothing happened. Swatch picks
  // default to solid. Note: the alpha slider doesn't use this; it uses the RGB in rgbMemory().
  if (!parsed || parsed.a === 0) return 1;
  return parsed.a;
}

/** Sync a row's controls to the current state. opts.keepHex=true leaves the hex input being edited alone. */
/* ---- Batching of editor-wide refreshes (v0.4.17) -------------------------------------
 * syncRow used to recompute group counts, preview, JSON dump and button state on every call.
 * A full sync (syncAll over 95 rows) did that heavy work 96 times, and typing did it once per
 * keystroke. Here "row DOM update" and "global refresh" are split: the row updates immediately
 * (inputs must not lag), the global part is batched to the next frame.
 * The policy function editorSyncPlan is pure and pinned by test/editor-selftest.js.
 */
var editorSyncState = { pending: false, flushes: 0, scheduled: 0 };

/* ===== EDITOR_SYNC_BEGIN ===== */
/**
 * How to do the global refresh after this syncRow call.
 * @param {object|undefined} opts optional syncRow options
 * @returns {'none'|'deferred'|'immediate'}
 */
function editorSyncPlan(opts) {
  if (opts && opts.silent) return 'none';
  if (opts && opts.defer) return 'deferred';
  return 'immediate';
}

/** Run on the next frame; falls back to a 16ms timer without requestAnimationFrame. */
function nextFrame(fn) {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
  return setTimeout(fn, 16);
}

/** Do a global refresh now (group counts / preview / JSON dump / button state / "only changed" filter). */
function flushEditorGlobals() {
  editorSyncState.pending = false;
  editorSyncState.flushes += 1;
  refreshCounts();
  renderPreview();
  renderDump();
  updateButtons();
  // With "Only changed" on, rows that become clean should leave the list; but the row being typed
  // in must stay (applyFilters keeps the focused row), or the input loses focus and the row vanishes.
  if (state.onlyDirty || state.onlyDarkDiff) applyFilters();
  else refreshDarkFlags();
}

/** Batch the refresh to the next frame; multiple calls within one frame refresh once. */
function scheduleEditorGlobals() {
  if (editorSyncState.pending) return;
  editorSyncState.pending = true;
  editorSyncState.scheduled += 1;
  nextFrame(flushEditorGlobals);
}
/* ===== EDITOR_SYNC_END ===== */

function syncRow(key, opts) {
  var ref = rowsByKey[key];
  if (!ref) return;
  var value = displayValue(key);
  var dirty = isDirty(key);

  ref.rowEl.classList.toggle('is-dirty', dirty);

  if (ref.stateTag) {
    var cleared = Object.prototype.hasOwnProperty.call(clearedMap(), key);
    ref.stateTag.textContent = dirty
      ? (cleared ? tsT('editor.row.stateCleared', '已清空') : tsT('editor.row.stateChanged', '已改'))
      : '';
  }

  if (ref.hexInput) ref.hexInput.placeholder = placeholderFor(ref.item);
  if (ref.textInput) ref.textInput.placeholder = placeholderFor(ref.item);
  if (ref.nativeCell) {
    var native = nativeOf(ref.item);
    ref.nativeCell.textContent = native ? tsT('editor.row.nativeValue', '原生 {{value}}', { value: native }) : '';
    ref.nativeCell.title = native
      ? tsT('editor.row.nativeValueTitle', '原生值：{{value}}', { value: native })
      : tsT('editor.row.noUniformNative', '该变量没有统一的原生值');
  }

  if (ref.item.type === 'color') {
    var parsed = parseColor(value);
    var fullyTransparent = !!parsed && parsed.a === 0;
    if (parsed) {
      // Remember the latest coloured RGB: after transparent, dragging the alpha slider uses it to recover the colour.
      // rememberRgbValue itself skips transparent / rgba(...,0) literals.
      rememberRgbValue(key, parsed, value);
      // Showing fully transparent rgba(...,0) / #0000 as #000000 would make users think it turned black;
      // show transparent explicitly so that, together with alpha=0%, it reflects the real state.
      if (!(opts && opts.keepHex)) {
        ref.hexInput.value = fullyTransparent ? 'transparent' : toHex(parsed.r, parsed.g, parsed.b);
      }
      ref.colorInput.value = toHex(parsed.r, parsed.g, parsed.b);
      ref.alpha.value = String(Math.round(parsed.a * 100));
      ref.alphaVal.textContent = Math.round(parsed.a * 100) + '%';
      setInputInvalid(ref.hexInput, ref.item, false);
      markRowInvalid(key, false);
    } else {
      // Non-colour literals like inherit / empty: show the text as-is and reset the controls to neutral,
      // swatch included — otherwise after clear/reset it still shows the old colour and looks ineffective.
      ref.hexInput.value = value || '';
      if (ref.colorInput) ref.colorInput.value = '#000000';
      ref.alpha.value = '100';
      ref.alphaVal.textContent = '100%';
    }
    // transparent / inherit / empty have no current RGB: the slider is only usable with a remembered
    // RGB (dragging reuses the last colour, see rgbMemory()). With no memory it is disabled, otherwise
    // dragging yields semi-transparent black rgba(0,0,0,x) and it looks like "drag turns it black".
    if (ref.alpha) {
      var hasRgbMemory = !!rgbMemory()[key];
      ref.alpha.disabled = !!state.busy || !parsed || (fullyTransparent && !hasRgbMemory);
      if (!parsed) {
        ref.alpha.title = tsT('editor.color.alphaNoColor', '当前值没有颜色成分（transparent / inherit / 空）；先填一个颜色再调不透明度');
      } else if (fullyTransparent && !hasRgbMemory) {
        ref.alpha.title = tsT('editor.color.alphaTransparent', '当前值没有颜色成分（transparent）；先填一个颜色再调不透明度');
      } else if (fullyTransparent) {
        ref.alpha.title = tsT('editor.color.alphaReuse', '拖动不透明度会沿用上一次的颜色');
      } else {
        ref.alpha.title = '';
      }
    }
  } else if (ref.item.type === 'switch') {
    if (ref.switchInput) {
      // Display value may be '1' / '0' / empty (empty = no server value = native look = on)
      var raw = value === null || value === undefined ? '' : String(value).trim();
      ref.switchInput.checked = raw === '' ? true : !(raw === '0' || raw === '0.0');
    }
  } else if (ref.item.type === 'select') {
    // Empty/unknown values show the "native" option (= unset look), so the dropdown doesn't mislead by sitting on the first item.
    if (ref.selectInput) {
      var selectValue = value === null || value === undefined ? '' : String(value).trim().toLowerCase();
      var known = '';
      (ref.item.options || []).forEach(function (opt) {
        if (!known && String(opt.value).toLowerCase() === selectValue) known = String(opt.value);
      });
      if (!known) known = String(ref.item.native || ((ref.item.options || [])[0] || {}).value || '');
      ref.selectInput.value = known;
      ref.selectInput.disabled = !!state.busy;
    }
    markRowInvalid(key, false);
  } else if (ref.item.type === 'image') {
    // Draw the image only if there is one; otherwise leave it empty and CSS stripes mean "no background
    // image". After switching mode / reloading this shows that mode's url(...).
    if (ref.imgSwatch) {
      var imgValue = value ? String(value) : '';
      var hasImage = !!imgValue && imgValue !== 'none' && imgValue.indexOf('url(') === 0;
      ref.imgSwatch.style.backgroundImage = hasImage ? imgValue : '';
      ref.imgSwatch.title = hasImage
        ? tsT('editor.image.current', '当前背景图片')
        : tsT('editor.image.none', '未设置背景图片（跟随原生）');
    }
    markRowInvalid(key, false);
  } else {
    if (!(opts && opts.keepText)) {
      ref.textInput.value = value || '';
      // We're echoing the current valid value, so clear the red border / invalid-row mark too;
      // otherwise after "Clear" or undo it lingers and applyTheme stays blocked by an invalid row.
      setInputInvalid(ref.textInput, ref.item, false);
      markRowInvalid(key, false);
    }
  }

  // filter / gradient: the preview strip follows the effective value (empty/invalid shows only stripes).
  if (ref.previewNode) syncValuePreview(ref, value);

  // Row DOM is done; the global part follows the plan: silent per row during full sync, batched to
  // the next frame while typing, and immediate otherwise (blur commit, clear, reload) as before.
  var plan = editorSyncPlan(opts);
  if (plan === 'immediate') flushEditorGlobals();
  else if (plan === 'deferred') scheduleEditorGlobals();
}

function syncAll() {
  Object.keys(rowsByKey).forEach(function (key) {
    syncRow(key, { silent: true });
  });
  flushEditorGlobals();
}

function refreshCounts() {
  CATALOG.forEach(function (group) {
    var total = group.items.length;
    var dirty = group.items.filter(function (item) { return isDirtyAny(item.key); }).length;
    // Find the group's count node via any row's countNode
    var node = rowsByKey[group.items[0].key] && rowsByKey[group.items[0].key].countNode;
    if (!node) return;
    node.textContent = '';
    if (dirty) {
      node.appendChild(el('span', { class: 'is-dirty-count', text: tsT('editor.group.changedCount', '{{count}} 项已改', { count: dirty }) }));
      node.appendChild(el('span', { class: 'dot', text: '·' }));
    }
    node.appendChild(el('span', { text: tsT('editor.group.totalCount', '{{count}} 项', { count: total }) }));
  });
}

/* ---------------- Filtering: search / only changed ---------------- */

/** Whether this key was edited or cleared in either mode (light/dark). */
function isDirtyAny(key) {
  return Object.prototype.hasOwnProperty.call(state.edited.light, key) ||
    Object.prototype.hasOwnProperty.call(state.edited.dark, key) ||
    Object.prototype.hasOwnProperty.call(state.cleared.light, key) ||
    Object.prototype.hasOwnProperty.call(state.cleared.dark, key);
}

/** Match by search query only (ignores "only changed"); also used when keeping the focused row. */
function rowMatchesQuery(item, query) {
  if (!query) return true;
  var hay = (item.label + ' ' + item.key + ' ' + (item.hint || '')).toLowerCase();
  return hay.indexOf(query) !== -1;
}

function rowMatches(item, query) {
  if (state.onlyDirty && !isDirtyAny(item.key)) return false;
  return rowMatchesQuery(item, query);
}

function highlightRowText(node, value, query) {
  if (!node || node.dataset.highlightQuery === query) return;
  node.dataset.highlightQuery = query;
  if (!query || !value) { node.textContent = value; return; }
  var pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  var fragment = document.createDocumentFragment();
  var last = 0, match;
  while ((match = pattern.exec(value)) !== null) {
    fragment.appendChild(document.createTextNode(value.slice(last, match.index)));
    fragment.appendChild(el('mark', { class: 'row-hl', text: match[0] }));
    last = pattern.lastIndex;
  }
  fragment.appendChild(document.createTextNode(value.slice(last)));
  node.replaceChildren(fragment);
}

function darkDiffersFromLight(key, light, dark) {
  return !!dark[key] && !samePaletteValue(dark[key], light[key]);
}

function refreshDarkFlags() {
  var darkMode = state.mode === 'dark';
  var light = effectiveVars('light'), dark = effectiveVars('dark');
  var count = 0, overrides = 0;
  Object.keys(rowsByKey).forEach(function (key) {
    var ref = rowsByKey[key];
    var differs = darkMode && darkDiffersFromLight(key, light, dark);
    if (differs) count += 1;
    if (dark[key]) overrides += 1;
    ref.darkFlag.hidden = !differs;
    ref.toLightBtn.hidden = !darkMode;
    ref.container.classList.toggle('is-darkdiff', differs);
  });
  var summary = $('dark-diff-summary');
  if (summary) {
    summary.hidden = !darkMode;
    if (darkMode) summary.textContent = tsT('rows.diffSummary', '{{count}} dark overrides differ from the light values.', { count: count });
  }
  var bulk = $('dark-clear-all');
  if (bulk) {
    bulk.hidden = !darkMode;
    bulk.textContent = tsT('rows.clearDark', 'Clear dark overrides ({{count}})', { count: overrides });
  }
  var toggle = $('only-dark-diff');
  if (toggle) {
    var chip = toggle.closest('.filter-chip') || toggle.parentNode;
    if (chip) chip.hidden = !darkMode;
    if (!darkMode) toggle.checked = state.onlyDarkDiff = false;
  }
}

function setOnlyDarkDiff(on) {
  state.onlyDarkDiff = !!on;
  applyFilters();
}

function copyLightIntoDark(key) {
  if (state.mode !== 'dark') return false;
  var value = effectiveVars('light')[key];
  if (value) markEdited(key, value);
  else clearRow(key);
  syncRow(key);
  return true;
}

function clearAllDarkOverrides() {
  if (state.mode !== 'dark') return 0;
  var keys = Object.keys(effectiveVars('dark')).filter(function (key) { return !!rowsByKey[key]; });
  if (!keys.length) {
    showToast(tsT('rows.noOverrides', 'No dark overrides to clear.'));
    return 0;
  }
  undoGroup(function () { keys.forEach(clearRow); });
  syncAll();
  showToast(tsT('rows.clearedOverrides', 'Cleared {{count}} dark overrides. Undo to restore them.', { count: keys.length }));
  return keys.length;
}

function wireDarkBulk() {
  var bulk = $('dark-clear-all');
  if (bulk) bulk.addEventListener('click', clearAllDarkOverrides);
}

/**
 * Apply "search query + only changed" to rows and groups:
 * - row level: row-wrap[hidden]
 * - group level: groups with no matching rows are hidden entirely
 * - force-expand while filtering, otherwise matches hidden in collapsed groups are as good as missing
 */
function applyFilters() {
  var query = (state.filter || '').trim().toLowerCase();
  var onlyDarkDiff = state.mode === 'dark' && state.onlyDarkDiff;
  var light = onlyDarkDiff && effectiveVars('light'), dark = onlyDarkDiff && effectiveVars('dark');
  var filtering = !!query || state.onlyDirty || onlyDarkDiff;
  var shown = 0;

  CATALOG.forEach(function (group) {
    var section = groupNodes[groupStateKey(group)];
    var visible = 0;
    group.items.forEach(function (item) {
      var ref = rowsByKey[item.key];
      if (!ref) return;
      var match = rowMatches(item, query) && (!onlyDarkDiff || darkDiffersFromLight(item.key, light, dark));
      // Focused row: its value was just typed back to the original and dropped out of "only changed",
      // but the user is still in it; hiding it would steal the input. Keep it until focusout.
      if (!match && rowHoldsFocus(ref) && rowMatchesQuery(item, query)) match = true;
      if (ref.container) ref.container.hidden = !match;
      if (match) {
        visible += 1; shown += 1;
        highlightRowText(ref.labelMain, item.label, query);
        highlightRowText(ref.keyNode, item.key, query);
        highlightRowText(ref.hintNode, item.hint, query);
      }
    });
    if (section) {
      section.hidden = visible === 0;
      section.classList.toggle('is-collapsed', !filtering && !!state.collapsed[groupStateKey(group)]);
      var head = section.querySelector('.group-head');
      if (head) head.setAttribute('aria-expanded', section.classList.contains('is-collapsed') ? 'false' : 'true');
    }
  });

  var noMatch = $('no-match');
  if (noMatch) noMatch.hidden = !(filtering && shown === 0);

  var clear = $('search-clear');
  if (clear) clear.hidden = !query;
  refreshDarkFlags();
}

function setFilter(value) {
  state.filter = value || '';
  applyFilters();
}

function clearFilter() {
  var input = $('row-search');
  if (input) input.value = '';
  state.filter = '';
  applyFilters();
  if (input) input.focus();
}

function rowOrderKeys() {
  return Array.prototype.slice.call(document.querySelectorAll('.row-wrap:not([hidden])'))
    .filter(function (wrap) { return !wrap.closest('.is-collapsed, [hidden]'); })
    .map(function (wrap) { return wrap.dataset.key; });
}

function rowPrimaryInput(key) {
  var ref = rowsByKey[key];
  if (!ref) return null;
  return ref.hexInput || ref.textInput || ref.selectInput || ref.switchInput ||
    (ref.imageField && ref.imageField.querySelector('[data-act="pick"]'));
}

function focusRowByKey(key) {
  var input = rowPrimaryInput(key);
  if (!input || input.disabled) return false;
  input.focus();
  if (typeof input.select === 'function' && input.type === 'text') input.select();
  return true;
}

function moveRowFocus(delta) {
  var active = document.activeElement;
  var wrap = active && active.closest('.row-wrap');
  if (!wrap) return false;
  var keys = rowOrderKeys();
  var index = keys.indexOf(wrap.dataset.key);
  return index >= 0 && focusRowByKey(keys[index + delta]);
}

function handleRowKeydown(event, item) {
  if (!event.altKey || event.metaKey || event.ctrlKey || event.shiftKey || event.isComposing) return;
  switch (event.key.toLowerCase()) {
    case 'arrowup': if (moveRowFocus(-1)) event.preventDefault(); return;
    case 'arrowdown': if (moveRowFocus(1)) event.preventDefault(); return;
    case 'c': copyRowValue(item); break;
    case 'v': pasteRowValue(item); break;
    case 'backspace':
    case 'delete': rowsByKey[item.key].clearBtn.click(); break;
    default: return;
  }
  event.preventDefault();
}

function setOnlyDirty(on) {
  state.onlyDirty = !!on;
  applyFilters();
}

/* ============================================================================
 * 8. Preview (inline styles, purely local simulation)
 * ==========================================================================*/

/* ===== PANEL_FRESHNESS_BEGIN (v0.9.0: panel version self-check) =====
 * The host static server serves index.html as a cacheable asset (max-age=3600). Reopening the
 * panel within an hour of an upgrade may get the old page — "missing features" is just a stale page.
 * Fetch index.html again with no-store, compare only the version; on mismatch reload once with a timestamp.
 */
var PANEL_FRESHNESS_DELAY_MS = 3000;
var PANEL_FRESHNESS_KEY = 'nts.panel.refreshed.';

function panelVersionOf(text) {
  var match = /v(\d+\.\d+\.\d+)/.exec(String(text || ''));
  return match ? match[1] : '';
}

async function checkPanelFreshness() {
  var node = $('panel-version');
  var local = panelVersionOf(node ? node.textContent : '');
  if (!local) return false;
  var response;
  try {
    // The built-in version has no separate index.html: the panel is the /theme_studio page itself,
    // so re-fetch the current page and use the template-rendered #panel-version to detect a new panel.
    var pageUrl = String(window.location.pathname || '/theme_studio');
    response = await fetchWithTimeout(pageUrl + '?_=' + Date.now(), {
      cache: 'no-store', credentials: 'same-origin'
    }, 4000);
  } catch (error) {
    return false; // Give up quietly: this is a nicety, don't bother the user
  }
  if (!response || !response.ok) return false;
  var html = '';
  try { html = await response.text(); } catch (error) { return false; }
  var holder = /id="panel-version"[^>]*>([^<]*)</.exec(String(html || ''));
  var remote = panelVersionOf(holder ? holder[1] : '');
  if (!remote || remote === local) return false;
  var key = PANEL_FRESHNESS_KEY + remote;
  try {
    if (window.sessionStorage && window.sessionStorage.getItem(key)) return false;
    if (window.sessionStorage) window.sessionStorage.setItem(key, '1');
  } catch (error) { /* Storage disabled in private mode: still reload once; version changes converge naturally */ }
  showNotice('warn', tsT('editor.freshness.reloading', '面板界面已更新到 v{{remote}}（当前页还是 v{{local}}），正在重新加载…', {
    remote: remote, local: local
  }));
  setTimeout(function () {
    try { window.location.reload(); } catch (error) { /* ignore */ }
  }, 700);
  return true;
}
/* ===== PANEL_FRESHNESS_END ===== */

/* ===== HOVER_PREVIEW_BEGIN (v0.9.0: theme card hover preview) =====
 * Hovering a library card temporarily switches the right-hand preview to that theme's colours,
 * restoring on leave. Deliberately preview-only: no applyTheme / no disk writes / no state.edited,
 * otherwise "just a peek" would become "edited the panel draft".
 * The data source is the theme file itself (/api/theme_studio/presets|themes/<id>.json) — calling
 * an entry goes through validation and the write lock, far too slow for hover; this just reads JSON.
 */
var hoverPreview = {
  seq: 0,            // only honour the latest request so a slow one can't overwrite a newer one
  themeId: '',
  name: '',
  palette: null,     // { builtin, vars, dark_vars }
  cache: Object.create(null)
};

function hoverPreviewMap() {
  if (!hoverPreview.palette) return null;
  var map = state.mode === 'dark' ? hoverPreview.palette.dark_vars : hoverPreview.palette.vars;
  return map && typeof map === 'object' ? map : null;
}

/** Hover-preview value: returned on hit, null on miss (so pvValue falls back to the current palette). */
function hoverPreviewValue(key) {
  var map = hoverPreviewMap();
  if (!map || !Object.prototype.hasOwnProperty.call(map, key)) return null;
  var value = map[key];
  return value === null || value === undefined ? '' : String(value);
}

/** Built-in themes reference images portably as url("images/<hash>"); the preview needs a directly reachable URL. */
function rewritePreviewRefs(map, builtin) {
  var out = {};
  if (!map || typeof map !== 'object') return out;
  Object.keys(map).forEach(function (key) {
    var value = map[key];
    var text = String(value === null || value === undefined ? '' : value);
    var match = /url\(\s*["']?images\/([^"')]+)["']?\s*\)/.exec(text);
    if (match) {
      var imageUrl = builtin
        ? API_BASE + '/presets/images/' + match[1]
        : API_BASE + '/backgrounds/' + match[1];
      text = 'url("' + imageUrl + '")';
    }
    out[key] = text;
  });
  return out;
}

function hoverPreviewFetchUrl(item) {
  var id = String((item && item.id) || '');
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(id)) return '';
  return API_BASE + (item.builtin ? '/presets/' : '/themes/') + id + '.json';
}

async function showHoverPreview(item) {
  if (!item) return;
  if (state.busy) return;
  if (modal && !modal.hidden) return;
  var url = hoverPreviewFetchUrl(item);
  if (!url) return;
  hoverPreview.seq += 1;
  var seq = hoverPreview.seq;
  var cached = hoverPreview.cache[url];
  if (!cached) {
    try {
      var response = await fetchWithTimeout(url + '?v=' + Date.now(), {
        cache: 'no-store', credentials: 'same-origin'
      }, 4000);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var raw = await response.json();
      if (!raw || typeof raw !== 'object') throw new Error('theme file is not an object');
      cached = {
        builtin: !!item.builtin,
        vars: rewritePreviewRefs(raw.vars, !!item.builtin),
        dark_vars: rewritePreviewRefs(raw.dark_vars, !!item.builtin)
      };
      // Cache cap: the library holds up to 200 entries, but a passing hover needn't fill memory.
      if (Object.keys(hoverPreview.cache).length < 60) hoverPreview.cache[url] = cached;
    } catch (error) {
      return; // Hover preview is a nicety: on network failure do nothing, never show an error
    }
  }
  if (seq !== hoverPreview.seq) return;
  hoverPreview.themeId = String(item.id || '');
  hoverPreview.name = String(item.name || item.id || '');
  hoverPreview.palette = cached;
  renderPreview();
}

function clearHoverPreview() {
  // Always bump seq, even with nothing painted yet: leaving a card must invalidate a
  // request still in flight, or it would paint after the pointer is gone.
  hoverPreview.seq += 1;
  if (!hoverPreview.palette) return;
  hoverPreview.palette = null;
  hoverPreview.themeId = '';
  hoverPreview.name = '';
  renderPreview();
}
/* ===== HOVER_PREVIEW_END ===== */

function pvValue(key, fallback) {
  // Hover preview wins: keys it has use that theme's values; others still use the current palette.
  var preview = hoverPreviewValue(key);
  if (preview !== null && preview !== '') return preview;
  var v = displayValue(key);
  if (!v) return fallback || '';
  if (v.indexOf('--') === 0) return fallback || '';
  return v;
}

function renderPreview() {
  $('preview-mode').textContent = (state.mode === 'dark'
    ? tsT('editor.preview.modeDark', '暗色')
    : tsT('editor.preview.modeLight', '亮色')) +
    (hoverPreview.palette
      ? tsT('editor.preview.hoverSuffix', ' · 预演「{{name}}」', { name: hoverPreview.name })
      : '');

  var chatBg = pvValue('--nts-chat-bg', state.mode === 'dark' ? '#1f242a' : '#eef3f8');
  var panelBg = pvValue('--nts-chat-panel-bg', '');
  var radius = pvValue('--nts-radius', '12px');
  // The preview must read radii like the real consumers: the shell reads --nts-shell-radius, bubbles
  // read --nts-bubble-radius, falling back to the generic --nts-radius only when unset. Previously all
  // read the generic value, so changing the bubble radius didn't move the preview and looked broken.
  var shellRadius = pvValue('--nts-shell-radius', radius);
  var bubbleRadius = pvValue('--nts-bubble-radius', radius);
  var blur = pvValue('--nts-chat-blur', '');
  // The contract allows a bare length ("18px"); loader/backend normalise it to blur(18px) before saving.
  // Writing a bare length into backdropFilter is invalid and the browser drops it — so the blur was
  // changed but the preview didn't react (and on device it looks like the theme didn't apply).
  // But the unit must be valid before wrapping: blur(18%) / blur(2vw) are invalid too, and the old
  // code wrapped unconditionally, leaking input the panel validation let through into the preview.
  var blurCss = '';
  if (blur) {
    if (RE_BLUR.test(blur)) blurCss = blur;
    else if (RE_BLUR_LENGTH.test(blur)) blurCss = 'blur(' + blur + ')';
    else if (RE_ZERO.test(blur)) blurCss = 'blur(0)';
  }

  /* "Chat background veil" switch (--nts-bubble-veil, 0–1 multiplier). On device it scales both the
   * opacity of the bubbles' frosted backing and the backdrop blur radius — the preview must compute it
   * too, or flipping the switch won't move the preview and it looks broken (though it works on device).
   * Only kicks in when the server actually has the variable; otherwise the preview keeps its usual look. */
  var veilRaw = pvValue('--nts-bubble-veil', '');
  var veilActive = veilRaw !== '';
  var veil = 1;
  if (veilActive) {
    if (RE_NUMBER.test(veilRaw)) veil = parseFloat(veilRaw);
    else if (RE_PERCENT.test(veilRaw)) veil = parseFloat(veilRaw) / 100;
    if (!isFinite(veil) || veil < 0) veil = 0;
    if (veil > 1) veil = 1;
  }
  var veilBlur = 'blur(' + Math.round(8 * veil * 100) / 100 + 'px) saturate(1.08)';
  // Native look when the switch is 1 (frosted backing colour), matching the light/dark values of
  // .compact-export-history-bubble in neko-chat-window.css.
  var veilUserSheet = state.mode === 'dark' ? [33, 72, 118, 0.902] : [208, 231, 255, 0.8784];
  var veilAiSheet = state.mode === 'dark' ? [33, 45, 62, 0.878] : [255, 255, 255, 0.8784];

  var preview = $('preview');
  preview.style.background = chatBg;
  preview.style.borderRadius = shellRadius;
  if (panelBg) preview.style.backgroundColor = panelBg;
  preview.style.backdropFilter = blurCss;
  preview.style.webkitBackdropFilter = blurCss;
  preview.style.boxShadow = pvValue('--nts-shadow', '');

  // The backing panel (message list base) maps to .pv-body in the preview. Its radius must come from
  // --nts-chat-panel-radius: that and the generic radius have different consumers; reading the wrong
  // one makes "changed the panel radius but the buttons moved in the preview".
  var panelRadius = pvValue('--nts-chat-panel-radius', '');
  // Chat background image: same layer as the panel background colour, painted above background-color
  // (so with both set the preview shows the image). cover/center/no-repeat must be set together,
  // otherwise a 4K image tiles at native pixels into a mosaic.
  var panelImage = pvValue('--nts-chat-bg-image', '');
  var hasPanelImage = !!panelImage && panelImage !== 'none' && panelImage.indexOf('url(') === 0;
  // Background image brightness: same algorithm as the CSS — a black scrim with alpha = 1 - brightness
  // over the image. The preview must follow, or adjusting brightness looks like it did nothing.
  var imgBrightRaw = pvValue('--nts-chat-bg-image-brightness', '');
  var imgBright = 1;
  if (imgBrightRaw !== '') {
    var bNum = RE_NUMBER.test(imgBrightRaw) ? parseFloat(imgBrightRaw) : NaN;
    if (isNaN(bNum) && RE_PERCENT.test(imgBrightRaw)) bNum = parseFloat(imgBrightRaw) / 100;
    if (!isNaN(bNum)) imgBright = Math.max(0, Math.min(1, bNum));
  }
  var imgScrim = hasPanelImage && imgBright < 1
    ? 'linear-gradient(rgba(0,0,0,' + (1 - imgBright).toFixed(4) + '), rgba(0,0,0,'
      + (1 - imgBright).toFixed(4) + ')), '
    : '';
  liveStyle($('pv-body'), {
    background: 'transparent',
    borderRadius: panelRadius || '0px'
  });
  // Fit / position / repeat of the image (and its brightness scrim, which shares them on
  // device). Only catalog option values are honoured: one bad entry would invalidate the
  // whole comma-separated list below and drop every layer's sizing.
  function pvSelectValue(key, fallback) {
    var raw = pvValue(key, '');
    var item = CATALOG_BY_KEY[key];
    var ok = !!raw && !!item && (item.options || []).some(function (opt) { return String(opt.value) === raw; });
    return ok ? raw : fallback;
  }
  var imgFit = pvSelectValue('--nts-chat-bg-fit', 'cover');
  var imgPosition = pvSelectValue('--nts-chat-bg-position', 'center');
  var imgRepeat = pvSelectValue('--nts-chat-bg-repeat', 'no-repeat');
  var imgLayerCount = hasPanelImage ? (imgScrim ? 2 : 1) : 0;
  var pvBody = $('pv-body');
  if (pvBody) {
    pvBody.style.backgroundImage = hasPanelImage ? (imgScrim + panelImage) : '';
    pvBody.style.backgroundSize = hasPanelImage ? imgFit : '';
    pvBody.style.backgroundPosition = hasPanelImage ? imgPosition : '';
    pvBody.style.backgroundRepeat = hasPanelImage ? imgRepeat : '';
    // Top fade (the "haze at the top of the image" users mention): same mask-gradient alpha multiply as the app.
    var topFadeRaw = pvValue('--nts-chat-top-fade', '');
    if (topFadeRaw === '') {
      pvBody.style.maskImage = '';
      pvBody.style.webkitMaskImage = '';
    } else {
      var fadeNum = RE_NUMBER.test(topFadeRaw) ? parseFloat(topFadeRaw) : NaN;
      if (isNaN(fadeNum) && RE_PERCENT.test(topFadeRaw)) fadeNum = parseFloat(topFadeRaw) / 100;
      if (isNaN(fadeNum)) fadeNum = 1;
      var fade = Math.max(0, Math.min(1, fadeNum));
      var fadeCss = 'linear-gradient(to bottom, rgba(0,0,0,' + (1 - fade).toFixed(4) + ') 0px, rgba(0,0,0,'
        + (1 - 0.22 * fade).toFixed(4) + ') 12px, rgba(0,0,0,1) 28px)';
      pvBody.style.maskImage = fadeCss;
      pvBody.style.webkitMaskImage = fadeCss;
    }
  }


  /* ---------- Panel glow / glassmorphism (preview-side reproduction) ----------
   * "Unset = leave the preview alone", matching the on-device gating; every multiplier is really
   * computed, otherwise moving a control without the preview reacting looks broken. */
  function pvNumber(key) {
    var raw = pvValue(key, '');
    if (raw === '') return null;
    var n = NaN;
    if (RE_NUMBER.test(raw)) n = parseFloat(raw);
    else if (RE_PERCENT.test(raw)) n = parseFloat(raw) / 100;
    if (isNaN(n)) return null;
    return clamp(n, 0, 1);
  }

  function pvLength(key) {
    var raw = pvValue(key, '');
    if (raw === '') return '';
    if (RE_LENGTH.test(raw) || RE_ZERO.test(raw)) return raw;
    if (/^[0-9]{1,4}(?:\.[0-9]+)?$/.test(raw)) return raw + 'px';
    return '';
  }

  /* Mix any CSS colour toward transparent by a ratio — the device uses color-mix, the preview an rgba
   * approximation. Colour parsing reuses parseColor, same range as the validator. */
  function pvTint(color, amount) {
    var c = parseColor(color || '');
    if (!c || amount <= 0) return 'rgba(0,0,0,0)';
    var a = Math.round(clamp(c.a * amount, 0, 1) * 1000) / 1000;
    return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + a + ')';
  }

  var glowColor = pvValue('--nts-panel-glow-color', '#ffffff');
  /* Same gating as the CSS: outer glow requires strength to be set, the rest as needed. */
  /* Exactly matches the CSS gating: outer glow turns on when any of color / strength / size is set,
   * others use defaults (strength 1, size 24px). Early previews required strength, so changing only
   * the glow colour showed no glow — exactly the case most easily mistaken for "changed, nothing happened". */
  var glowStrength = pvNumber('--nts-panel-glow-strength');
  var glowColorRaw = pvValue('--nts-panel-glow-color', '');
  var glowSizeRaw = pvValue('--nts-panel-glow-size', '');
  var glowOn = (glowColorRaw !== '' || glowStrength !== null || glowSizeRaw !== '');
  var glowAmount = glowStrength === null ? 1 : glowStrength;
  var glowSizePx = parseFloat(pvLength('--nts-panel-glow-size')) || 24;
  var borderColorRaw = pvValue('--nts-panel-border-color', '');
  var borderWidth = pvLength('--nts-panel-border-width');
  var cornerGlow = pvNumber('--nts-panel-corner-glow');
  var edgeGradient = pvNumber('--nts-panel-edge-gradient');
  var sheen = pvNumber('--nts-panel-sheen');
  var innerGlow = pvNumber('--nts-panel-inner-glow');
  var glassTintRaw = pvValue('--nts-panel-glass-tint', '');

  if (pvBody) {
    /* The **first** background-image is on top, so glow layers are prepended.
     * The original "scrim + background image" stays underneath. */
    var below = hasPanelImage ? (imgScrim + panelImage) : '';
    var above = [];

    if (glassTintRaw) {
      var tint = parseColor(glassTintRaw);
      if (tint) above.push('linear-gradient(' + pvTint(glassTintRaw, 1) + ', '
        + pvTint(glassTintRaw, 1) + ')');
    }
    if (cornerGlow !== null && cornerGlow > 0) {
      ['0% 0%', '100% 0%', '0% 100%', '100% 100%'].forEach(function (pos) {
        above.push('radial-gradient(circle at ' + pos + ', '
          + pvTint(glowColor, cornerGlow * 0.95) + ', rgba(0,0,0,0) 34%)');
      });
    }
    if (sheen !== null && sheen > 0) {
      above.push('linear-gradient(135deg, ' + pvTint('#ffffff', sheen * 0.42)
        + ' 0%, rgba(0,0,0,0) 46%)');
    }
    if (glowOn && glowAmount > 0) {
      /* Outer glow: a radial gradient ring from transparent to the glow colour, thickness scales with size×strength. */
      var inner = Math.max(0, glowSizePx * 1.42 * glowAmount).toFixed(1);
      above.push('radial-gradient(ellipse at center, rgba(0,0,0,0) calc(100% - '
        + inner + 'px), ' + pvTint(glowColor, glowAmount * 0.55) + ' 100%)');
    }

    pvBody.style.backgroundImage = above.length
      ? (above.join(', ') + (below ? ', ' + below : ''))
      : below;
    if (hasPanelImage || above.length) {
      // Per-layer lists: glow / tint layers keep their own cover / center / no-repeat, the
      // image layers (scrim + image) use the selected fit / position / repeat.
      var layerSizes = [];
      var layerPositions = [];
      var layerRepeats = [];
      for (var li = 0; li < above.length; li += 1) {
        layerSizes.push('cover');
        layerPositions.push('center center');
        layerRepeats.push('no-repeat');
      }
      for (var lj = 0; lj < imgLayerCount; lj += 1) {
        layerSizes.push(imgFit);
        layerPositions.push(imgPosition);
        layerRepeats.push(imgRepeat);
      }
      pvBody.style.backgroundRepeat = layerRepeats.join(', ');
      pvBody.style.backgroundSize = layerSizes.join(', ');
      pvBody.style.backgroundPosition = layerPositions.join(', ');
    }

    /* Border: on device it's on anchor::after; the preview puts a border on the body directly. */
    pvBody.style.border = (borderWidth || borderColorRaw)
      ? (borderWidth || '1.5px') + ' solid ' + (borderColorRaw || 'rgba(255,255,255,.55)')
      : '';

    /* Inner edge gradient + inner shadow: both expressed as inset shadows in the preview. */
    var glowShadows = [];
    if (edgeGradient !== null && edgeGradient > 0) {
      glowShadows.push('inset 0 0 ' + (glowSizePx * 0.9).toFixed(1) + 'px '
        + (glowSizePx * 0.12).toFixed(1) + 'px ' + pvTint(glowColor, edgeGradient));
    }
    if (innerGlow !== null && innerGlow > 0) {
      glowShadows.push('inset 0 0 ' + (18 * innerGlow).toFixed(1) + 'px '
        + (2 * innerGlow).toFixed(1) + 'px rgba(0,0,0,.35)');
    }
    pvBody.style.boxShadow = glowShadows.join(', ');
  }

  var fontFamily = pvValue('--nts-font-family', '');
  var fontSize = pvValue('--nts-font-size', '');
  preview.style.fontFamily = fontFamily || '';
  // Always assign (empty clears it): otherwise emptying the field keeps the old inline size.
  preview.style.fontSize = fontSize || '';

  liveStyle($('pv-topbar'), {
    background: pvValue('--nts-topbar-bg', 'transparent'),
    color: pvValue('--nts-topbar-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a')
  });

  /* Bubble background: when the bubble background variable is set it takes over (the veil yields,
   * matching the CSS yieldsTo semantics), but the backdrop blur still scales with the switch. */
  var userBgRaw = pvValue('--nts-user-bubble-bg', '');
  var aiBgRaw = pvValue('--nts-ai-bubble-bg', '');
  styleBubble($('pv-user'), {
    background: userBgRaw
      ? userBgRaw
      : (veilActive ? veilSheet(veilUserSheet, veil) : '#44b7fe'),
    color: pvValue('--nts-user-bubble-text',
      veilActive ? (state.mode === 'dark' ? '#dceaff' : '#142033') : '#ffffff'),
    borderColor: pvValue('--nts-user-bubble-border', 'transparent'),
    radius: bubbleRadius,
    align: 'flex-end',
    blur: veilActive ? veilBlur : ''
  });

  styleBubble($('pv-ai'), {
    background: aiBgRaw
      ? aiBgRaw
      : (veilActive
        ? veilSheet(veilAiSheet, veil)
        : (state.mode === 'dark' ? 'rgba(42,123,196,0.18)' : '#ffffff')),
    color: pvValue('--nts-ai-bubble-text',
      veilActive ? (state.mode === 'dark' ? '#e8f0fb' : '#273042') : (state.mode === 'dark' ? '#e0e0e0' : '#24445a')),
    borderColor: pvValue('--nts-ai-bubble-border', 'transparent'),
    radius: bubbleRadius,
    align: 'flex-start',
    blur: veilActive ? veilBlur : ''
  });

  $('pv-link').style.color = pvValue('--nts-link', '#2a7bc4');

  liveStyle($('pv-chip'), {
    background: pvValue('--nts-chip-bg', 'transparent'),
    color: pvValue('--nts-chip-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a'),
    borderColor: pvValue('--nts-rec-border', 'rgba(0,0,0,0.12)'),
    borderRadius: '999px'
  });

  liveStyle($('pv-input-area'), {
    background: pvValue('--nts-input-bg', state.mode === 'dark' ? 'rgba(40,40,40,0.65)' : 'rgba(255,255,255,0.5)'),
    borderTopColor: pvValue('--nts-input-border', 'rgba(0,0,0,0.06)'),
    blur: blurCss
  });

  liveStyle($('pv-input'), {
    background: pvValue('--nts-input-bg', 'transparent'),
    color: pvValue('--nts-input-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a'),
    borderColor: pvValue('--nts-input-border', 'rgba(0,0,0,0.1)'),
    borderRadius: radius
  });

  liveStyle($('pv-send'), {
    background: pvValue('--nts-send-bg', '#44b7fe'),
    color: pvValue('--nts-send-text', '#ffffff'),
    borderRadius: radius
  });

  renderCompare();
}

/* ===== BATCH1_BEGIN (v0.10.0: per-row copy/paste / compare card / AI prompt) ===== */

var rowClipboard = '';

/** Copy this row's currently displayed value (including the server value) to paste into another row or mode. */
function copyRowValue(item) {
  var value = displayValue(item.key);
  if (value === null || value === undefined || String(value) === '') {
    value = serverValue(item.key);
  }
  value = value === null || value === undefined ? '' : String(value);
  if (!value) {
    showToast(tsT('editor.row.copyEmpty', '这一行现在是空的（原生值），没有可复制的值'), 'warn');
    return;
  }
  rowClipboard = value;
  try { copyText(value); } catch (error) { /* in-memory copy remains if the clipboard is unavailable */ }
  showToast(tsT('editor.row.copied', '已复制「{{label}}」的值：{{value}}', { label: item.label, value: value }), 'ok');
}

/** Paste the last copied (or system clipboard) value into this row, with normal validation and "changed" marking. */
async function pasteRowValue(item) {
  var text = '';
  if (rowClipboard) {
    text = rowClipboard;
  } else if (navigator.clipboard && navigator.clipboard.readText) {
    try { text = await navigator.clipboard.readText(); } catch (error) { text = ''; }
  }
  text = String(text || '').trim();
  if (!text) {
    showToast(tsT('editor.row.pasteEmpty', '剪贴板里没有可粘贴的内容（也可以先点某一行的复制按钮）'), 'warn');
    return;
  }
  if (!isSafeValue(item.key, text)) {
    showToast(tsT('editor.row.pasteRejected', '「{{label}}」不接受这个值：{{value}}', { label: item.label, value: text.slice(0, 40) }), 'warn');
    return;
  }
  undoPush(item.key, true);
  markEdited(item.key, normalizeTypedValue(item, text));
  syncRow(item.key);
  showToast(tsT('editor.row.pasted', '已粘贴到「{{label}}」', { label: item.label }), 'ok');
}

/**
 * Compare card: while hover-previewing, list the few "current → previewed" values that actually
 * differ and are visible.
 *
 * The whole preview DOM is not duplicated (that would mean parameterising ~300 lines of
 * renderPreview, risky and unnecessary): instead compare key surfaces per variable — swatches for
 * colours, text for lengths/blur.
 * Entries: [CSS variable, i18n key suffix under editor.compare., Chinese fallback label];
 * labels are translated at render time.
 */
var COMPARE_KEYS = [
  ['--nts-chat-bg', 'chatBg', '聊天背景'],
  ['--nts-chat-panel-bg', 'panelBg', '背板底色'],
  ['--nts-user-bubble-bg', 'userBubble', '用户气泡'],
  ['--nts-ai-bubble-bg', 'aiBubble', 'AI 气泡'],
  ['--nts-user-bubble-text', 'bubbleText', '气泡文字'],
  ['--nts-accent', 'accent', '主色调'],
  ['--nts-shell-radius', 'shellRadius', '外壳圆角'],
  ['--nts-chat-blur', 'chatBlur', '毛玻璃模糊']
];

function compareIsColor(value) {
  var text = String(value || '').trim().toLowerCase();
  if (!text) return false;
  if (text.charAt(0) === '#') return true;
  return text.indexOf('rgb') === 0 || text.indexOf('hsl') === 0;
}

function comparePairs() {
  var out = [];
  COMPARE_KEYS.forEach(function (pair) {
    var key = pair[0];
    var current = displayValue(key);
    var previewed = hoverPreviewValue(key);
    var before = current === null || current === undefined ? '' : String(current);
    var after = previewed === null || previewed === undefined ? '' : String(previewed);
    if (after === '' || after === before) return;
    out.push({ key: key, label: tsT('editor.compare.' + pair[1], pair[2]), before: before, after: after });
  });
  return out;
}

function renderCompare() {
  var card = $('pv-compare');
  if (!card) return;
  var body = $('pv-compare-rows');
  var hovering = !!(hoverPreview && hoverPreview.palette);
  card.hidden = !hovering;
  if (!hovering || !body) return;
  body.innerHTML = '';
  var pairs = comparePairs();
  if (!pairs.length) {
    body.appendChild(el('p', {
      class: 'pv-compare-empty',
      text: tsT('editor.compare.same', '这套主题在这几项上和当前配色一致，只有细节不同 —— 直接看上面的预览即可。')
    }));
    return;
  }
  pairs.forEach(function (pair) {
    var row = el('div', { class: 'pv-compare-row' });
    row.appendChild(el('span', { class: 'pv-compare-label', text: pair.label, title: pair.key }));
    var beforeCell = el('span', { class: 'pv-compare-val' });
    if (compareIsColor(pair.before)) {
      beforeCell.appendChild(el('span', { class: 'pv-chip-sw', style: 'background:' + pair.before }));
    }
    beforeCell.appendChild(el('code', { text: pair.before || tsT('editor.value.empty', '（空）') }));
    var afterCell = el('span', { class: 'pv-compare-val is-after' });
    if (compareIsColor(pair.after)) {
      afterCell.appendChild(el('span', { class: 'pv-chip-sw', style: 'background:' + pair.after }));
    }
    afterCell.appendChild(el('code', { text: pair.after }));
    row.appendChild(beforeCell);
    row.appendChild(el('span', { class: 'pv-compare-arrow', text: '→', 'aria-hidden': 'true' }));
    row.appendChild(afterCell);
    body.appendChild(row);
  });
}

/** Turn the current palette into a prompt that can be handed straight to an AI (with the variable contract and hard constraints). */
function buildAiPrompt() {
  // Panel self-test rule: no backticks in the source (guards against template literals), so build the fence from char codes.
  var FENCE = String.fromCharCode(96, 96, 96);
  var name = ($('theme-name') && $('theme-name').value.trim()) || state.metaName || DEFAULT_THEME_NAME;
  var light = effectiveVars('light');
  var dark = effectiveVars('dark');
  var changes = pendingChangeList();
  var lines = [];
  var emptyText = tsT('editor.value.empty', '（空）');
  lines.push(tsT('editor.aiPrompt.title', '# N.E.K.O 主题：{{name}}', { name: name }));
  lines.push('');
  lines.push(tsT('editor.aiPrompt.intro1', '你是一个配色设计师。下面是我当前用的主题（N.E.K.O 聊天窗口的 CSS 变量），'));
  lines.push(tsT('editor.aiPrompt.intro2', '请在此基础上做改进，并把**完整**结果按同样的两段结构输出，方便我直接粘贴回面板。'));
  lines.push('');
  lines.push(tsT('editor.aiPrompt.rulesHeading', '硬约束：'));
  lines.push(tsT('editor.aiPrompt.rule1', '1. 只能使用下面出现过的变量名，不要新增不存在的变量，也不要删除它们；'));
  lines.push(tsT('editor.aiPrompt.rule2', '2. 颜色用 #rrggbb / rgba() / transparent；长度带 px；模糊写成 blur(18px) 这种形式；'));
  lines.push(tsT('editor.aiPrompt.rule3', '3. 亮色组写在 vars 里，暗色组写在 dark_vars 里，两组都要给全；'));
  lines.push(tsT('editor.aiPrompt.rule4', '4. 正文与背景对比度至少 4.5:1，次要文字至少 3:1（这套面板有对比度自检）；'));
  lines.push(tsT('editor.aiPrompt.rule5', '5. 只输出一个 {{fence}}json 代码块，内容为 {"name": "...", "vars": {...}, "dark_vars": {...}}。', { fence: FENCE }));
  lines.push('');
  if (changes.length) {
    lines.push(tsT('editor.aiPrompt.changesHeading', '我这次改过的 {{count}} 项（可以重点看这些）：', { count: changes.length }));
    changes.slice(0, 12).forEach(function (entry) {
      lines.push(tsT('editor.aiPrompt.changeLine', '- {{mode}} {{label}}：{{before}} → {{after}}', {
        mode: changeModeLabel(entry.mode),
        label: changeKeyLabel(entry.key),
        before: entry.before || emptyText,
        after: entry.after || emptyText
      }));
    });
    lines.push('');
  }
  lines.push(tsT('editor.aiPrompt.currentPalette', '当前配色：'));
  lines.push(FENCE + 'json');
  lines.push(JSON.stringify({ name: name, vars: light, dark_vars: dark }, null, 2));
  lines.push(FENCE);
  return lines.join('\n');
}

function copyAiPrompt() {
  var text = buildAiPrompt();
  openModal(
    'export',
    tsT('editor.aiPrompt.modalTitle', '给 AI 的提示词 · {{name}}', {
      name: ($('theme-name') && $('theme-name').value.trim()) || DEFAULT_THEME_NAME
    }),
    tsT('editor.aiPrompt.modalHint', '复制后贴给任意 AI（ChatGPT / Claude / 本地模型都行）。它按变量契约改完，你把返回的 JSON 贴回「导入」即可。'),
    text
  );
}

/* ===== BATCH1_END ===== */

function liveStyle(node, cfg) {
  if (!node) return;
  if (cfg.background !== undefined) node.style.backgroundColor = cfg.background;
  if (cfg.color !== undefined) node.style.color = cfg.color;
  if (cfg.borderColor !== undefined) node.style.borderColor = cfg.borderColor;
  if (cfg.borderTopColor !== undefined) node.style.borderTopColor = cfg.borderTopColor;
  if (cfg.borderRadius !== undefined) node.style.borderRadius = cfg.borderRadius;
  // Use !== undefined rather than truthiness: clearing --nts-chat-blur must remove the previous
  // backdrop-filter, otherwise the frosted glass lingers in the preview and clearing looks broken.
  if (cfg.blur !== undefined) {
    node.style.backdropFilter = cfg.blur || '';
    node.style.webkitBackdropFilter = cfg.blur || '';
  }
}

function styleBubble(node, cfg) {
  if (!node) return;
  node.style.backgroundColor = cfg.background;
  node.style.color = cfg.color;
  node.style.borderColor = cfg.borderColor;
  node.style.borderStyle = 'solid';
  node.style.borderWidth = '1px';
  node.style.borderRadius = cfg.radius || '12px';
  node.style.alignSelf = cfg.align;
  // Bubble backdrop blur: the chat background veil switch (--nts-bubble-veil) scales the radius by a
  // 0–1 multiplier. Always write it (even an empty string), otherwise after turning the switch off the
  // previous blur stays in the preview and the switch looks broken.
  if (cfg.blur !== undefined) {
    node.style.backdropFilter = cfg.blur || '';
    node.style.webkitBackdropFilter = cfg.blur || '';
  }
}

/* Scale the alpha of an RGBA array by a 0–1 multiplier (the frosted "veil" in the preview). */
function veilSheet(rgba, veil) {
  return toRgba(rgba[0], rgba[1], rgba[2], rgba[3] * veil);
}

/* Scale a CSS colour's alpha by a 0–1 multiplier; return it unchanged if it can't be parsed. */
function veilTint(color, veil) {
  var c = parseColor(color);
  if (!c) return color;
  var a = (c.a === undefined ? 1 : c.a) * veil;
  return toRgba(c.r, c.g, c.b, a);
}

/* ============================================================================
 * 9. Palette dump / meta info
 * ==========================================================================*/

/* ===== DUMP_LAZY_BEGIN ===== */
// Current palette JSON card (v0.4.21): no JSON rebuild while collapsed.
//
// A palette is 2 × 95 lines of strings, and renderDump() runs on every global refresh
// (editing a row, switching mode, reloading, following external changes). While collapsed the
// user can't see the text, so JSON.stringify each time is pure waste: collapsed only marks it
// dirty, and expanding renders once.
var DUMP_COLLAPSE_KEY = 'nts.dump.collapsed';
var dumpState = { collapsed: false, dirty: false, renders: 0, skipped: 0 };

/**
 * Whether to skip the rebuild while collapsed. A pure function so the truth table can run directly
 * in Node (see test/dump-selftest.js) instead of guessing from the source.
 * @param {boolean} collapsed
 * @param {boolean} dirty
 * @returns {'skip'|'render'}
 */
function dumpLazyPlan(collapsed, dirty) {
  if (!collapsed) return 'render';
  return 'skip';
}

function loadDumpCollapsed() {
  try {
    var store = libraryStorage();
    if (!store) return false;
    return store.getItem(DUMP_COLLAPSE_KEY) === '1';
  } catch (error) {
    return false;
  }
}

function saveDumpCollapsed() {
  try {
    var store = libraryStorage();
    if (store) store.setItem(DUMP_COLLAPSE_KEY, dumpState.collapsed ? '1' : '0');
  } catch (error) { /* private mode: just don't remember it */ }
}

/** Reflect dumpState.collapsed in the DOM (button arrow, aria-expanded, body hidden). */
function applyDumpCollapsed() {
  var btn = $('btn-toggle-dump');
  var body = $('dump-body');
  if (btn) {
    btn.textContent = dumpState.collapsed ? '▸' : '▾';
    btn.setAttribute('aria-expanded', dumpState.collapsed ? 'false' : 'true');
    btn.setAttribute('title', dumpState.collapsed
      ? tsT('editor.dump.expand', '展开 JSON')
      : tsT('editor.dump.collapse', '折叠 JSON'));
  }
  if (body) body.hidden = dumpState.collapsed;
}

/** Collapse / expand; when expanding a dirty dump, render it once. */
function toggleDumpCard() {
  dumpState.collapsed = !dumpState.collapsed;
  saveDumpCollapsed();
  applyDumpCollapsed();
  if (!dumpState.collapsed && dumpState.dirty) renderDump(true);
}
/* ===== DUMP_LAZY_END ===== */

function renderDump(force) {
  // While collapsed only mark dirty (see DUMP_LAZY above); expand / copy / forced calls actually rebuild.
  if (!force && dumpLazyPlan(dumpState.collapsed, dumpState.dirty) === 'skip') {
    dumpState.dirty = true;
    dumpState.skipped += 1;
    return;
  }
  dumpState.dirty = false;
  dumpState.renders += 1;
  var payload = {
    name: $('theme-name') ? $('theme-name').value : undefined,
    enabled: $('theme-enabled').checked,
    vars: effectiveVars('light'),
    dark_vars: effectiveVars('dark')
  };
  var pending = Object.keys(state.edited.light).length + Object.keys(state.edited.dark).length +
    Object.keys(state.cleared.light).length + Object.keys(state.cleared.dark).length;
  // This must be pure JSON: the old code appended "// ↑ includes N unapplied changes" to the JSON,
  // which contradicted the dump description (effectiveVars, unapplied changes included) and made the
  // copy invalid JSON. The unapplied note lives in #dump-foot, outside #dump, so copies are always valid.
  $('dump').textContent = JSON.stringify(payload, null, 2);

  var foot = $('dump-foot');
  if (foot) {
    var saved = paletteCounts(state.palette);
    foot.textContent = tsT('editor.dump.saved', '已保存：亮色 {{light}} 项 / 暗色 {{dark}} 项', {
      light: saved.light, dark: saved.dark
    }) +
      (pending
        ? tsT('editor.dump.pending', '；另有 {{count}} 项改动尚未应用 —— 上面的 JSON 已经按改动后的样子生成（复制出去就是改完的效果），点「应用」才会写进服务器。', { count: pending })
        : tsT('editor.dump.noPending', '；没有未应用的改动。'));
  }
}

/** Effective variables for a mode: server-saved values overlaid with local unapplied changes.
 *
 * Don't use collect(mode) directly: it only has keys edited/cleared in this session, so reading back
 * a saved palette would render an empty object — the panel would claim "current palette vars: {}",
 * contradicting the real theme.json on disk, which is badly misleading.
 */
function effectiveVars(mode) {
  var out = {};
  var saved = state.palette
    ? (mode === 'dark' ? state.palette.dark_vars : state.palette.vars)
    : null;
  if (saved) Object.keys(saved).forEach(function (k) { out[k] = saved[k]; });
  Object.keys(state.edited[mode]).forEach(function (k) { out[k] = state.edited[mode][k]; });
  Object.keys(state.cleared[mode]).forEach(function (k) { delete out[k]; });
  return out;
}

function collect(mode) {
  var out = {};
  var edited = state.edited[mode];
  var cleared = state.cleared[mode];
  Object.keys(edited).forEach(function (k) { out[k] = edited[k]; });
  Object.keys(cleared).forEach(function (k) { out[k] = ''; });
  return out;
}

function renderMeta(options) {
  var p = state.palette;
  if (!p) return;
  // If the user changed name/enabled but hasn't applied, don't overwrite the inputs with server values:
  // the old code always wrote #theme-enabled on every read-back, so flipping the switch was silently
  // reset to the server value, and the user thought it was changed when applying.
  // Exception: when the user clicks "Reload" and confirms discarding changes, name / enabled also go
  // back to the disk values, otherwise the panel says "discarded" while these two linger, keep blocking
  // external sync, and get written on the next apply.
  var discard = !!(options && options.discard);
  var keepEdits = !discard && metaDirtyCount() > 0;
  // The baseline must refresh on every palette read-back: applyTheme uses it to tell whether name/enabled really changed.
  state.metaName = p.name || '';
  state.metaEnabled = p.enabled !== false;
  // theme.json has no version field anymore (since 0.1.5): the panel shows no version counter,
  // only the theme name and "last updated" pills.
  $('meta-name').textContent = p.name || tsT('editor.meta.defaultName', '默认');
  var when = p.updated_at ? String(p.updated_at).replace('T', ' ').slice(0, 19) : '—';
  $('meta-updated').textContent = when;
  if (!keepEdits && document.getElementById('theme-name') &&
      (discard || document.activeElement !== $('theme-name'))) {
    $('theme-name').value = p.name || '';
  }
  if (!keepEdits) $('theme-enabled').checked = p.enabled !== false;
}

/* ===== CHANGES_BEGIN (v0.5.1: pending changes list) ===== */

/** Display label for a palette mode ('light' / 'dark'), translated at call time. */
function changeModeLabel(mode) {
  if (mode === 'light') return tsT('editor.preview.modeLight', '亮色');
  if (mode === 'dark') return tsT('editor.preview.modeDark', '暗色');
  return mode;
}

/**
 * Flatten "server values + local edits" into one entry per change (pure function).
 *
 * @param {{palette?: Object, edited?: Object, cleared?: Object}} snapshot
 * @returns {Array<{key: string, mode: string, before: string, after: string, kind: string}>}
 *   kind: changed (value changed) / added (previously absent) / removed (cleared)
 */
function changeEntryList(snapshot) {
  var snap = snapshot || {};
  var palette = snap.palette || {};
  var edited = snap.edited || {};
  var cleared = snap.cleared || {};
  var out = [];
  [['light', palette.vars], ['dark', palette.dark_vars]].forEach(function (pair) {
    var mode = pair[0];
    var saved = pair[1] && typeof pair[1] === 'object' ? pair[1] : {};
    var ed = edited[mode] && typeof edited[mode] === 'object' ? edited[mode] : {};
    var cl = cleared[mode] && typeof cleared[mode] === 'object' ? cleared[mode] : {};
    var keys = {};
    Object.keys(ed).forEach(function (k) { keys[k] = 1; });
    Object.keys(cl).forEach(function (k) { keys[k] = 1; });
    Object.keys(keys).sort().forEach(function (key) {
      var before = Object.prototype.hasOwnProperty.call(saved, key) ? String(saved[key]) : '';
      var after = '';
      var kind = 'changed';
      if (Object.prototype.hasOwnProperty.call(cl, key)) {
        after = '';
        kind = before ? 'removed' : 'changed';
      } else {
        after = Object.prototype.hasOwnProperty.call(ed, key) ? String(ed[key]) : '';
        kind = before ? 'changed' : 'added';
      }
      if (after === before) return;
      out.push({ key: key, mode: mode, before: before, after: after, kind: kind });
    });
  });
  return out;
}

function pendingChangeList() {
  return changeEntryList({
    palette: state.palette,
    edited: state.edited,
    cleared: state.cleared
  });
}

function changeValueText(value) {
  return String(value) === '' ? tsT('editor.value.empty', '（空）') : String(value);
}

function changeKeyLabel(key) {
  var item = CATALOG_BY_KEY[key];
  return (item && item.label) || key;
}

/** Revert one change: drop the local edit/clear mark, then sync the row back to the server value. */
function revertChange(entry) {
  if (!entry) return;
  var edited = state.edited[entry.mode] || {};
  var cleared = state.cleared[entry.mode] || {};
  delete edited[entry.key];
  delete cleared[entry.key];
  state.edited[entry.mode] = edited;
  state.cleared[entry.mode] = cleared;
  syncRow(entry.key);
  renderChanges();
  showToast(tsT('editor.changes.reverted', '已撤销「{{label}}」的改动', { label: changeKeyLabel(entry.key) }), 'ok');
}

function revertAllChanges() {
  var count = pendingChangeCount();
  if (!count) return;
  if (!window.confirm(tsT('editor.changes.revertAllConfirm', '要撤销全部 {{count}} 项未应用的改动吗？撤销后无法恢复。', { count: count }))) return;
  state.edited = { light: {}, dark: {} };
  state.cleared = { light: {}, dark: {} };
  markAllRowsValid();
  clearRgbMemory();
  renderMeta({ discard: true });
  resetMaterialsLocal();
  syncAll();
  renderChanges();
  showNotice('warn', tsT('editor.changes.revertedAllNotice', '已撤销 {{count}} 项未应用的改动（服务器上的配色没动）。', { count: count }));
  showToast(tsT('editor.changes.revertedAll', '已撤销全部改动'), 'ok');
}

/** Redraw the list. With no changes the whole card is hidden and takes no space. */
function renderChanges() {
  var card = $('changes-card');
  if (!card) return;
  var list = $('changes-list');
  var countNode = $('changes-count');
  var entries = pendingChangeList();
  var total = pendingChangeCount();
  if (countNode) countNode.textContent = total ? String(total) : '';
  card.hidden = !total;
  if (!total) {
    if (list) list.innerHTML = '';
    return;
  }
  if (!list) return;
  list.innerHTML = '';
  entries.forEach(function (entry) {
    var row = el('li', { class: 'change-row' });
    row.appendChild(el('span', {
      class: 'change-mode is-' + entry.mode,
      text: changeModeLabel(entry.mode)
    }));
    row.appendChild(el('span', {
      class: 'change-label',
      text: changeKeyLabel(entry.key),
      title: entry.key
    }));
    row.appendChild(el('span', { class: 'change-delta' }, [
      el('code', { class: 'change-before', text: changeValueText(entry.before) }),
      el('span', { class: 'change-arrow', text: '→', 'aria-hidden': 'true' }),
      el('code', { class: 'change-after is-' + entry.kind, text: changeValueText(entry.after) })
    ]));
    var undo = el('button', {
      class: 'btn btn-mini change-undo',
      type: 'button',
      text: tsT('editor.changes.undo', '撤销'),
      'aria-label': tsT('editor.changes.undoAria', '撤销「{{label}}」的改动', { label: changeKeyLabel(entry.key) })
    });
    undo.addEventListener('click', function () { revertChange(entry); });
    row.appendChild(undo);
    if (entry.mode === 'light' || entry.mode === 'dark') {
      var goto = el('button', {type: 'button', class: 'btn btn-mini', text: tsT('workflow.goto', 'Go to setting')});
      goto.addEventListener('click', function () { setMode(entry.mode); focusRowByKey(entry.key); });
      row.appendChild(goto);
    }
    list.appendChild(row);
  });
  if (materialsDirtyCount()) {
    var materialRow = el('li', {class: 'change-row'});
    materialRow.appendChild(el('span', {class: 'change-label', text: tsT('actions.apply.partMaterials', 'Materials') + ' (' + materialsDirtyCount() + ')'}));
    var reset = el('button', {class: 'btn btn-mini', type: 'button', text: tsT('editor.changes.undo', 'Undo')});
    reset.addEventListener('click', function () { resetMaterialsLocal(); updateButtons(); });
    materialRow.appendChild(reset);
    list.appendChild(materialRow);
  }
  renderLivePrecheckRow(list);
}

/* ===== CHANGES_END ===== */

function updateButtons() {
  var n = pendingChangeCount();
  scheduleDraftSave();
  syncPreviewPalette();
  scheduleLivePrecheck();
  syncUndoApplyButton();
  syncPreviewButton();
  var applyBtn = $('btn-apply');
  applyBtn.textContent = n
    ? tsT('editor.apply.withCount', '应用（{{count}} 项改动）', { count: n })
    : tsT('editor.apply.label', '应用');
  applyBtn.disabled = state.busy;
  applyBtn.dataset.busy = state.busy ? 'true' : 'false';
  ['btn-reset', 'btn-export', 'btn-import', 'btn-reload', 'btn-revert-mode', 'btn-share-card'].forEach(function (id) {
    var node = $(id);
    if (node) node.disabled = state.busy;
  });
  // Schedule save also needs a successfully read schedule: saving after a failed read would
  // overwrite the stored schedule with the empty / default card (see refreshSchedule).
  var scheduleSave = $('btn-schedule-save');
  if (scheduleSave) scheduleSave.disabled = !!state.busy || !(scheduleState && scheduleState.loaded);
  // Pending changes list (v0.5.1): refreshed with the button text; the card hides itself.
  renderChanges();
  updateUndoButtons();
  renderQuickPresets();
}

/* ============================================================================
 * 10. Actions
 * ==========================================================================*/

function buildArgs(mode) {
  return collect(mode);
}

/** How many entries each of the two palette groups has (0 is common and worth stating). */
function paletteCounts(p) {
  var l = p && p.vars && typeof p.vars === 'object' ? Object.keys(p.vars).length : 0;
  var d = p && p.dark_vars && typeof p.dark_vars === 'object' ? Object.keys(p.dark_vars).length : 0;
  return { light: l, dark: d };
}

/**
 * Warning shown when only one group changed. Light and dark are independent
 * (THEME_CONTRACT §3.0), so "only light configured while the app is in dark
 * mode" has zero effect -- the usage most easily mistaken for "the plugin is
 * broken", so the hint must call it out.
 */
function modeHint(nLight, nDark) {
  if (nLight && !nDark) {
    return '\n' + tsT('actions.modeHint.lightOnly', '注意：本次只改了「亮色」组 —— 应用若正处在暗色模式，外观不会变（两组各管各的）。');
  }
  if (nDark && !nLight) {
    return '\n' + tsT('actions.modeHint.darkOnly', '注意：本次只改了「暗色」组 —— 应用若正处在亮色模式，外观不会变（两组各管各的）。');
  }
  return '';
}

/** Neutral wording when reading the palette back from disk: not "this time you
 * only changed" but "currently only". The old code reused modeHint and falsely
 * reported "only the light group changed" on refresh / readback. */
function savedModeHint(nLight, nDark) {
  if (nLight && !nDark) {
    return '\n' + tsT('actions.savedModeHint.lightOnly', '当前只有「亮色」组有自定义项（暗色模式仍是原生外观）。');
  }
  if (nDark && !nLight) {
    return '\n' + tsT('actions.savedModeHint.darkOnly', '当前只有「暗色」组有自定义项（亮色模式仍是原生外观）。');
  }
  return '';
}

/**
 * The persistent note under the editor: it answers the most common question,
 * "why don't I see anything", so "empty = native" and "light and dark are
 * independent" are stated right here instead of hidden in the README.
 */
function renderPaletteHint() {
  var node = $('empty-hint');
  if (!node) return;
  if (!state.palette) {
    node.textContent = tsT('actions.paletteHint.notLoaded', '尚未读取到调色板；面板仍可编辑，点「应用」会重试读取。');
    return;
  }
  var c = paletteCounts(state.palette);
  if (!c.light && !c.dark) {
    node.textContent = tsT('actions.paletteHint.empty', '当前调色板为空：所有组件都使用 N.E.K.O 原生外观。任意一行填值并应用，即刻生效。');
    return;
  }
  node.textContent = tsT('actions.paletteHint.saved',
    '已保存 {{total}} 项（亮色 {{light}} / 暗色 {{dark}}）。留空的行保持原生外观；两组各管各的，只配一组时另一模式不会变。',
    { total: c.light + c.dark, light: c.light, dark: c.dark });
}

/** Extract rejected variable names from the backend's rejected messages (they contain '--nts-xxx'). */
function rejectedKeysOf(rejected) {
  var keys = {};
  (rejected || []).forEach(function (msg) {
    var found = String(msg).match(/--nts-[a-z0-9-]+/g);
    if (!found) return;
    found.forEach(function (k) { keys[k] = true; });
  });
  return keys;
}

/**
 * Put rejected submitted values back into the edit state. The backend rejects
 * entries individually and the valid part is already on disk, so we must not
 * clear the whole change set, or the user would have to retype the rejected
 * value from memory.
 * @returns {number} number of restored entries
 */
function restoreRejected(rejected, sentLight, sentDark) {
  var keys = rejectedKeysOf(rejected);
  var restored = 0;
  function restoreBag(sent, edited, cleared) {
    Object.keys(sent).forEach(function (k) {
      if (!keys[k]) return;
      if (sent[k] === '') cleared[k] = true;
      else edited[k] = sent[k];
      restored += 1;
    });
  }
  restoreBag(sentLight, state.edited.light, state.cleared.light);
  restoreBag(sentDark, state.edited.dark, state.cleared.dark);
  if (restored) { syncAll(); applyFilters(); }
  return restored;
}

/** Snapshot of the material center's current edit values (dirty or not); null when not mounted. */
function materialsSnapshot() {
  var api = window.NekoMaterials;
  if (!api || typeof api.snapshot !== 'function') return null;
  try { return api.snapshot() || null; } catch (error) { return null; }
}

/** The material center's current edit values; null when unchanged / not mounted (the backend then keeps the on-disk materials). */
function currentMaterialsPayload() {
  var api = window.NekoMaterials;
  if (!api || typeof api.snapshot !== 'function' || typeof api.isDirty !== 'function') return null;
  try {
    return api.isDirty() ? (api.snapshot() || null) : null;
  } catch (error) {
    return null;
  }
}

/** Push the on-disk palette to the material center so its "saved" baseline matches disk. */
function syncMaterials(palette, options) {
  var api = window.NekoMaterials;
  if (!api || typeof api.sync !== 'function') return;
  try {
    api.sync(palette || state.palette, options);
  } catch (error) {
    // A material center render error must not take down the main panel: it is only one part of it.
  }
}

/** Pull the material edit state back to its baseline after reset / failed readback. */
function resetMaterialsLocal() {
  var api = window.NekoMaterials;
  if (!api || typeof api.resetLocal !== 'function') return;
  try {
    api.resetLocal();
  } catch (error) {
    // Same as above: never affects the main flow.
  }
}

/** Copy of the palette drafts (edited values and cleared keys) per mode. */
function snapshotDrafts() {
  return {
    edited: { light: Object.assign({}, state.edited.light), dark: Object.assign({}, state.edited.dark) },
    cleared: { light: Object.assign({}, state.cleared.light), dark: Object.assign({}, state.cleared.dark) }
  };
}

/**
 * Remove the drafts that were submitted by a save, keeping edits made while the
 * save was in flight (a different value for the same key, or a new key).
 * Returns the number of drafts left.
 */
function dropSubmittedDrafts(snapshot) {
  var left = 0;
  ['light', 'dark'].forEach(function (mode) {
    var edited = state.edited[mode];
    var cleared = state.cleared[mode];
    Object.keys(snapshot.edited[mode]).forEach(function (key) {
      if (Object.prototype.hasOwnProperty.call(edited, key) && edited[key] === snapshot.edited[mode][key]) {
        delete edited[key];
      }
    });
    Object.keys(snapshot.cleared[mode]).forEach(function (key) {
      if (Object.prototype.hasOwnProperty.call(cleared, key)) delete cleared[key];
    });
    left += Object.keys(edited).length + Object.keys(cleared).length;
  });
  return left;
}

async function applyTheme() {
  if (state.busy) return;

  // Red-outlined rows must be resolved first: with invalid input, `edited` still
  // holds the "last valid value", so applying would submit it and report success,
  // making the user think the invalid value just typed took effect. Block it here.
  if (invalidRowCount()) {
    var nBad = invalidRowCount();
    showNotice('warn', tsT('actions.apply.invalidNotice',
      '有 {{count}} 行输入不合法（红框），已阻止应用。\n请把红框里的值改成合法值，或点该行「清空」。',
      { count: nBad }));
    showToast(tsT('actions.apply.invalidShort', '有非法输入，未应用'), 'error');
    setStatus(tsT('actions.apply.invalidShort', '有非法输入，未应用'), 'warn');
    return;
  }

  var lightVars = buildArgs('light');
  var darkVars = buildArgs('dark');
  var nLight = Object.keys(lightVars).length;
  var nDark = Object.keys(darkVars).length;
  var enabled = $('theme-enabled').checked;
  // The materials subtree is replaced wholesale: only send it when the panel actually
  // changed it, otherwise a "colors only" submit would overwrite on-disk materials
  // with stale panel values.
  var materials = currentMaterialsPayload();
  var materialsDirty = !!materials;
  // Compare against the "saved baseline" written by renderMeta, not !!state.palette:
  // when the first get_theme failed (palette=null) the old code always judged "unchanged",
  // so changing only the name or the enabled toggle always said "nothing to apply"
  // and the change could never be submitted.
  var enabledChanged = state.metaEnabled !== enabled;
  var nameInput = $('theme-name');
  var name = nameInput ? nameInput.value.trim() : '';
  // Changing only the theme name is a valid change too: missing it means the name
  // could never be saved (observed: clicking apply did nothing).
  var nameChanged = name !== (state.metaName || '');

  // Do not fake success when nothing changed: otherwise "clicked apply but nothing
  // happened" gets taken for a plugin fault. The message is diagnostic: edits are
  // recorded per keystroke (oninput -> markEdited), no blur needed.
  if (!nLight && !nDark && !enabledChanged && !nameChanged && !materialsDirty) {
    showNotice('warn', tsT('actions.apply.noChangesNotice',
      '没有可应用的改动（当前 0 项变量改动，名称/启用未变，也没有红框非法输入）。\n改值会在输入时立即记入面板，不必等控件失焦；如果确实改过，可能是刚才用「还原本组」撤销了。'));
    showToast(tsT('actions.apply.noChanges', '没有改动可应用'));
    setStatus(tsT('actions.apply.noChanges', '没有改动可应用'), 'warn');
    return;
  }

  var args = {vars: lightVars, dark_vars: darkVars};
  if (enabledChanged) args.enabled = enabled;
  if (materialsDirty) args.materials = materials;
  // Let the backend normalize the submitted name and return any warning.
  if (nameChanged) args.name = name;
  var before = snapshotForUndo(state.palette);
  if (before && before.revision) args.base_revision = before.revision;

  setBusy(true, tsT('actions.apply.busy', '正在应用…'));
  // Material snapshot at submit time: material controls stay editable during save /
  // readback, and later edits must remain in the panel.
  var materialsAtSubmit = materialsSnapshot();
  // Same for palette drafts: presets, variants and row paste stay usable while the
  // save is in flight, so only the drafts that were actually submitted are cleared.
  var draftsAtSubmit = snapshotDrafts();
  try {
    if ((nLight || nDark) && !(await confirmContrast(effectiveVars('light'), effectiveVars('dark')))) return;
    var result = await callPlugin('apply_theme', args);
    if (result && result.applied === false && result.conflict) {
      if (!window.confirm(tsT('workflow.conflict', 'The saved theme changed while you were editing. Apply your changes to the latest version?'))) return;
      before = await callPlugin('get_theme', {});
      if (!before.revision || before.revision !== result.conflict.disk_revision) {
        showNotice('warn', tsT('sync.blockedToast', 'The saved theme has changed.'));
        return;
      }
      args.base_revision = before.revision;
      result = await callPlugin('apply_theme', args);
    }
    if (!result || result.applied === false) {
      showNotice('warn', tsT('sync.blockedToast', 'The saved theme has changed.'));
      return;
    }
    clearPreviewState();
    pokeThemeWindows();
    lastApplyUndo = makeApplyUndo(before, '', '', result);
    var rejected = result && result.rejected;
    // After a successful write we must read back and check the result: if a silent
    // failure is swallowed, the panel keeps old values while saying "applied" --
    // what the user sees would not match disk.
    var reloaded = await reloadPalette({ silent: true, materialsSubmitted: materialsAtSubmit });
    if (reloaded) {
      // Only a successful readback means "panel = disk"; only then clear dirty and
      // invalid state -- for the submitted drafts. Edits made during the save remain.
      var leftover = dropSubmittedDrafts(draftsAtSubmit);
      if (!leftover) markAllRowsValid();
      // The syncAll() inside reloadPalette ran before the dirty state was cleared: the
      // yellow "changed" rows, the group header's "N changed" and the dump footnote are
      // all stale. Without refreshing here the user thinks the save failed, and clicking
      // "apply" again gets blocked with "nothing to apply".
      syncAll();
      // Applied = the server accepted these changes; keeping the undo stack would only
      // create the illusion of undoing written values into "never written". Clear it:
      // undo only makes sense before applying.
      // A successful save is the undo boundary even when drafts made during the
      // save remain: older snapshots predate the saved values, and restoring
      // them would bring saved drafts back as pending changes.
      resetUndoHistory();
    } else {
      // Readback failed: dirty flags must be kept (never clear before reading). The old
      // code cleared edited/cleared first, so a failed readback showed "saved" while
      // the screen might still hold old values -- the user would think the changes were
      // lost. Keeping them = the panel honestly shows "not synced", and "reload" is the
      // path back to consistency. Also update the name/enabled baseline with the
      // submitted values so the next run does not detect phantom changes.
      if (nameChanged) state.metaName = name || DEFAULT_THEME_NAME;
      state.metaEnabled = enabled;
    }
    // Rejected entries stay in the panel for in-place fixing (the valid part is already on disk).
    var restored = rejected && rejected.length ? restoreRejected(rejected, lightVars, darkVars) : 0;
    // Only describe changes that actually happened, so "0 entries" or a renamed theme is never misreported.
    var parts = [];
    if (nLight) parts.push(tsT('actions.apply.partLight', '亮色 {{count}} 项', { count: nLight }));
    if (nDark) parts.push(tsT('actions.apply.partDark', '暗色 {{count}} 项', { count: nDark }));
    if (enabledChanged) {
      parts.push(enabled ? tsT('actions.apply.partEnabled', '已启用主题') : tsT('actions.apply.partDisabled', '已停用主题'));
    }
    if (nameChanged) parts.push(tsT('actions.apply.partName', '主题名「{{name}}」', { name: name || DEFAULT_THEME_NAME }));
    if (materialsDirty) parts.push(tsT('actions.apply.partMaterials', '高级材质'));
    var counts = parts.length ? parts.join(' / ') : tsT('actions.apply.noVarChanges', '无变量改动');
    if (lastApplyUndo) lastApplyUndo.counts = counts;
    var readbackNote = reloaded ? '' :
      '\n' + tsT('actions.apply.readbackFailedNote', '已写入，但回读失败，面板仍标记为「未同步」（改动标记已保留），请点「重新读取」。');
    if (result.name_warning) readbackNote += '\n' + result.name_warning;
    if (Array.isArray(result.material_warnings) && result.material_warnings.length) {
      readbackNote += '\n' + result.material_warnings.join('\n');
    }
    if (reloaded && nameChanged && nameInput && nameInput.value.trim() === name) nameInput.value = reloaded.name;
    if (rejected && rejected.length) {
      showNotice('warn', tsT('actions.apply.partialNotice',
        '已应用（{{counts}}），但后端拒绝了 {{rejected}} 条，已把它们的原值放回面板便于就地修正：\n',
        { counts: counts, rejected: rejected.length }) + rejected.join('\n') + readbackNote);
      showToast(restored
        ? tsT('actions.apply.partialToastRestored', '已应用（{{count}} 条被拒绝，已回填）', { count: rejected.length })
        : tsT('actions.apply.partialToast', '已应用（{{count}} 条被拒绝）', { count: rejected.length }));
    } else {
      showNotice(reloaded ? 'ok' : 'warn', tsT('actions.apply.doneNotice', '已应用并写入 theme.json：{{counts}}', { counts: counts }) +
        modeHint(nLight, nDark) + readbackNote);
      showToast(tsT('actions.apply.doneShort', '已应用 {{counts}}', { counts: counts }), 'ok');
    }
    setStatus(tsT('actions.apply.doneShort', '已应用 {{counts}}', { counts: counts }) +
      (restored ? tsT('actions.apply.statusPendingFix', '（{{count}} 条待修正）', { count: restored }) : '') +
      (reloaded ? '' : tsT('actions.readbackFailedSuffix', '（回读失败）')),
      reloaded ? 'ok' : 'warn');
    verifyAppliedShortly();
  } catch (error) {
    showNotice('error', tsT('actions.apply.failedNotice', '应用失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.apply.failed', '应用失败'), 'error');
    setStatus(tsT('actions.apply.failed', '应用失败'), 'error');
  } finally {
    setBusy(false);
  }
}

async function resetTheme() {
  if (state.busy) return;
  if (!window.confirm(tsT('actions.reset.confirm', '确定恢复内置默认主题？当前所有自定义颜色都会被清除。'))) return;
  setBusy(true, tsT('actions.reset.busy', '正在重置…'));
  try {
    var before = snapshotForUndo(state.palette);
    var result = await callPlugin('reset_theme', {});
    lastApplyUndo = makeApplyUndo(before, tsT('actions.reset.doneShort', 'Reset'), '', result);
    clearPreviewState();
    pokeThemeWindows();
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    // Wholesale replacement: name / enabled inputs follow the saved values too.
    var reloaded = await reloadPalette({ silent: true, discardEdits: true });
    if (reloaded) {
      showNotice('ok', tsT('actions.reset.done', '已恢复默认主题。'));
    } else {
      // The reset is on disk but the panel could not read it back: ask to reload
      // instead of pretending everything is current. Same for materials -- the backend
      // restored them to default (enabled=false), so the edit state must not keep old values.
      resetMaterialsLocal();
      showNotice('warn', tsT('actions.reset.doneReadbackFailed', '已恢复默认主题。\n回读失败，面板显示可能不是最新值，请点「重新读取」。'));
    }
    showToast(tsT('actions.reset.doneShort', '已重置'), 'ok');
    setStatus(tsT('actions.reset.doneShort', '已重置') + (reloaded ? '' : tsT('actions.readbackFailedSuffix', '（回读失败）')), reloaded ? 'ok' : 'warn');
  } catch (error) {
    showNotice('error', tsT('actions.reset.failedNotice', '重置失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.reset.failed', '重置失败'), 'error');
    setStatus(tsT('actions.reset.failed', '重置失败'), 'error');
  } finally {
    setBusy(false);
  }
}

/**
 * Let the backend package the file content, then trigger a browser download from the panel.
 * Since being built in, the panel is a normal top-level page (no longer a sandboxed
 * iframe without allow-downloads), so the backend no longer writes to Desktop / Downloads.
 */
async function saveThemeFile(args) {
  return downloadExportResult(await callPlugin('export_to_file', args));
}

function downloadExportResult(result) {
  var data = result && result.data ? String(result.data) : '';
  if (!data || !result.filename) throw new Error(tsT('actions.export.noFileContent', '后端没有返回文件内容'));
  downloadBlob(base64ToBytes(data), result.filename, result.mime || 'application/octet-stream');
  return result;
}

function savedHint(result) {
  var kb = Math.round((result.bytes || 0) / 1024);
  return result.images
    ? tsT('actions.export.downloadedWithImages', '已下载：{{filename}}\n（{{kb}} KB，含 {{images}} 张背景图）',
      { filename: result.filename, kb: kb, images: result.images })
    : tsT('actions.export.downloaded', '已下载：{{filename}}\n（{{kb}} KB）', { filename: result.filename, kb: kb });
}

async function exportTheme() {
  if (state.busy) return;
  // The backend's export_theme returns the theme.json saved on disk, without the
  // panel's unapplied changes. That differs from the dump on the right (effectiveVars,
  // which includes them), so say so explicitly before exporting.
  var pending = dirtyCount();
  if (pending && !window.confirm(tsT('actions.export.pendingConfirm',
    '有 {{count}} 项未应用的改动。\n导出的 JSON 来自磁盘上已保存的 theme.json（不含这些未应用改动）。继续导出？',
    { count: pending }))) {
    return;
  }
  setBusy(true, tsT('actions.export.busy', '正在导出…'));
  try {
    var result = await callPlugin('export_theme', {});
    var hint = pending
      ? tsT('actions.export.hintPending', '下面是已保存到 theme.json 的调色板 JSON（不含 {{count}} 项未应用改动），可复制保存或分享。', { count: pending })
      : tsT('actions.export.hint', '下面是当前调色板的 JSON 文本，可复制保存或分享。');
    // Also download a JSON file.
    try {
      var savedJson = await saveThemeFile({ kind: 'json' });
      hint = savedHint(savedJson) + '\n\n' + hint;
      setStatus(pending
        ? tsT('actions.export.doneDownloadedPending', '已导出并下载（不含未应用改动）')
        : tsT('actions.export.doneDownloaded', '已导出并下载'), 'ok');
    } catch (saveError) {
      hint = tsT('actions.export.downloadFailed', '下载文件失败（{{error}}），下面是 JSON 文本，可手动复制。',
        { error: pluginErrorMessage(saveError) }) + '\n\n' + hint;
      setStatus(tsT('actions.export.doneNoFile', '已导出（未写出文件）'), 'ok');
    }
    openModal('export', tsT('actions.export.title', '导出主题'), hint, result.payload || '');
  } catch (error) {
    showNotice('error', tsT('actions.export.failedNotice', '导出失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.export.failed', '导出失败'), 'error');
    setStatus(tsT('actions.export.failed', '导出失败'), 'error');
  } finally {
    setBusy(false);
  }
}

// Keep in sync with IMPORT_MAX_CHARS in plugin/__init__.py. The host's input_schema
// rejects over-long input by maxLength before the entry is even called and wraps the
// error as INTERNAL, which users cannot make sense of -- so check here first and give
// a readable message.
var IMPORT_MAX_CHARS = 262144;

function openImport() {
  openModal('import', tsT('actions.import.title', '导入主题'),
    tsT('actions.import.hint', '粘贴调色板 JSON 文本。导入为整体替换；非法条目会被后端丢弃。'), '');
}

async function doImport() {
  var text = $('modal-text').value.trim();
  if (!text) { showToast(tsT('actions.import.empty', '请先粘贴内容')); return; }
  if (text.length > IMPORT_MAX_CHARS) {
    var tooLong = tsT('actions.import.tooLong',
      '导入内容过长（{{length}} 字符，上限 {{max}} 字符）。请删掉不必要的内容后重试。',
      { length: text.length, max: IMPORT_MAX_CHARS });
    showNotice('error', tooLong);
    showToast(tsT('actions.import.failed', '导入失败'), 'error');
    setStatus(tsT('actions.import.failedTooLong', '导入失败：内容过长'), 'error');
    openModal('import', tsT('actions.import.title', '导入主题'),
      tooLong + '\n' + tsT('actions.import.textKept', '粘贴内容已保留，可修改后重试。'), text);
    return;
  }
  closeModal();
  setBusy(true, tsT('actions.import.busy', '正在导入…'));
  try {
    var result = await callPlugin('import_theme', { payload: text });
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    // Wholesale replacement: name / enabled inputs follow the saved values too.
    var reloaded = await reloadPalette({ silent: true, discardEdits: true });
    var readbackNote = reloaded ? '' :
      '\n' + tsT('actions.readbackFailedNote', '已写入，但回读失败，面板显示可能不是最新值，请点「重新读取」。');
    var rejected = result && result.rejected;
    if (rejected && rejected.length) {
      showNotice('warn', tsT('actions.import.partialNotice', '已导入，但后端拒绝了 {{count}} 条：\n', { count: rejected.length }) +
        rejected.join('\n') + readbackNote);
      showToast(tsT('actions.import.partialToast', '已导入（部分条目被拒绝）'), 'ok');
      setStatus(reloaded
        ? tsT('actions.import.partialStatus', '已导入（{{count}} 条被拒绝）', { count: rejected.length })
        : tsT('actions.import.partialStatusReadbackFailed', '已导入（{{count}} 条被拒绝，回读失败）', { count: rejected.length }),
        'warn');
    } else {
      showNotice(reloaded ? 'ok' : 'warn', tsT('actions.import.doneNotice', '已导入并写入 theme.json。') + readbackNote);
      showToast(tsT('actions.import.done', '已导入'), 'ok');
      setStatus(tsT('actions.import.done', '已导入') + (reloaded ? '' : tsT('actions.readbackFailedSuffix', '（回读失败）')), reloaded ? 'ok' : 'warn');
    }
  } catch (error) {
    showNotice('error', tsT('actions.import.failedNotice', '导入失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.import.failed', '导入失败'), 'error');
    setStatus(tsT('actions.import.failed', '导入失败'), 'error');
    // On failure the palette is unchanged; put the pasted text back into the dialog so it is not "swallowed".
    openModal('import', tsT('actions.import.title', '导入主题'),
      tsT('actions.import.failedNotice', '导入失败：{{error}}', { error: pluginErrorMessage(error) }) + '\n' +
      tsT('actions.import.textKept', '粘贴内容已保留，可修改后重试。'), text);
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 10b. Theme library (save / switch / share zip / import zip)
 * ==========================================================================*/

/** Inline confirm state: a second click within 3 s actually deletes (no window.confirm:
 *  in headless / automated environments confirm blocks the renderer and freezes the panel). */
var pendingDelete = { id: '', at: 0 };
var LIB_CONFIRM_MS = 3000;

/** base64 -> Uint8Array. atob throws on invalid characters; callers handle it. */
function base64ToBytes(base64) {
  var bin = atob(base64);
  var out = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i) & 0xff;
  return out;
}

/** Trigger a browser download (Blob + temporary <a download>). */
function downloadBlob(bytes, filename, mime) {
  var blob = new Blob([bytes], { type: mime || 'application/octet-stream' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  // Revoking immediately makes some Chromium versions cancel the download; delay a bit.
  setTimeout(function () {
    try { document.body.removeChild(a); } catch (e) { /* already removed */ }
    URL.revokeObjectURL(url);
  }, 1000);
}

/* ===== LIB_CORE_BEGIN -- theme library pure functions =====
 * Data transforms only: no DOM, no global state, so _work/panel_selftest.js can
 * extract this block verbatim and assert on it (family/tag/search filtering,
 * favorites pinned first, collapsed-state read/write).
 * Must stay ES5 (no arrow functions / template strings / let-const).
 * Labels below are the Chinese source text; they are translated at render time
 * via libT(), which falls back to the source text when tsT is not available. */
function libT(key, fallback) {
  return typeof tsT === 'function' ? tsT(key, fallback) : fallback;
}

var LIB_FAMILIES = [
  { id: 'glassy', label: '液态玻璃', tone: '#7fb8ff' },
  { id: 'gradient', label: '渐变', tone: '#b48bff' },
  { id: 'dark', label: '暗色', tone: '#6b7585' },
  { id: 'light', label: '浅色', tone: '#d9b45a' },
  { id: 'texture', label: '纸感', tone: '#c79a6b' },
  { id: 'minimal', label: '极简', tone: '#9aa4b2' }
];
var LIB_FAV_FAMILY = '__fav__';
var LIB_CUSTOM_FAMILY = '__custom__';

var LIB_RECENT_FAMILY = '__recent__';
var RECENT_KEY = 'neko.themeStudio.recent';
var RECENT_LIMIT = 12;
var LIB_FAV_KEY = 'nts.library.favorites';
var LIB_SORT_KEY = 'neko.themeStudio.libSort';
var LIB_SORTS = ['default', 'name', 'detail', 'saved'];

var LIB_TAG_VISIBLE = 14;
var LIB_COLLAPSE_KEY = 'nts.catalog.collapsed';

/** Ids of the six valid families (same order as the tabs). */
function libFamilyIds() {
  return LIB_FAMILIES.map(function (family) { return family.id; });
}

function libFamilyLabel(family) {
  if (family === LIB_CUSTOM_FAMILY) return libT('library.family.custom', '自定义');
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) {
    if (LIB_FAMILIES[i].id === family) return libT('library.family.' + family, LIB_FAMILIES[i].label);
  }
  return libT('library.family.other', '其他');
}

function libFamilyTone(family) {
  if (family === LIB_CUSTOM_FAMILY) return '#8f7bff';
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) {
    if (LIB_FAMILIES[i].id === family) return LIB_FAMILIES[i].tone;
  }
  return '#8a8f98';
}

/**
 * An entry's style family: a valid backend family is used as is; legacy data
 * without one falls into dark / light by its light/dark variable counts (allowed
 * by contract §3.1), so every entry has a browsable family.
 */
function libFamilyOf(item) {
  var declared = item && typeof item.family === 'string' ? item.family.toLowerCase() : '';
  if (libFamilyIds().indexOf(declared) >= 0) return declared;
  var dark = item && isFinite(item.dark) ? Number(item.dark) : 0;
  var light = item && isFinite(item.light) ? Number(item.light) : 0;
  return dark > light ? 'dark' : 'light';
}

function libTagsOf(item) {
  var tags = item && item.tags;
  if (!(tags instanceof Array)) return [];
  var out = [];
  for (var i = 0; i < tags.length; i += 1) {
    var raw = tags[i] === null || tags[i] === undefined ? '' : String(tags[i]);
    var tag = raw.slice(0, 12);
    if (tag && out.indexOf(tag) < 0) out.push(tag);
  }
  return out;
}

function libSearchText(item) {
  if (!item) return '';
  return [String(item.name || ''), String(item.note || ''), libTagsOf(item).join(' ')]
    .join(' ').toLowerCase();
}

/** Search: case-insensitive substring match on name / note / tags. */
function libMatchesQuery(item, query) {
  var q = String(query || '').replace(/\u3000/g, ' ').trim().toLowerCase();
  if (!q) return true;
  var text = libSearchText(item);
  var terms = q.split(/\s+/);
  for (var i = 0; i < terms.length; i += 1) {
    if (terms[i] && text.indexOf(terms[i]) < 0) return false;
  }
  return true;
}

function libMatchesTag(item, tag) {
  if (!tag) return true;
  return libTagsOf(item).indexOf(String(tag)) >= 0;
}

function libIsFavorite(item, favorites) {
  if (!item || !item.id || !(favorites instanceof Array)) return false;
  return favorites.indexOf(String(item.id)) >= 0;
}

/** Family filter; LIB_FAV_FAMILY / LIB_CUSTOM_FAMILY are two virtual families. */
function libMatchesFamily(item, family, favorites, recent) {
  if (!family) return true;
  if (family === LIB_FAV_FAMILY) return libIsFavorite(item, favorites);
  if (family === LIB_RECENT_FAMILY) return !!item && (recent || []).indexOf(String(item.id)) >= 0;
  // "Custom" = entries the user saved or imported (backend builtin=false).
  if (family === LIB_CUSTOM_FAMILY) return !item || !item.builtin;
  return libFamilyOf(item) === family;
}

/**
 * Main filter: family && tag && query combined.
 * The "All" view (empty family) pins favorites first; the rest keep the backend
 * order (built-ins first, newest first).
 */
function libSelectLibrary(items, opts) {
  var query = opts && opts.query ? opts.query : '';
  var tag = opts && opts.tag ? opts.tag : '';
  var family = opts && opts.family ? opts.family : '';
  var favorites = opts && opts.favorites instanceof Array ? opts.favorites : [];
  var recent = (opts && opts.recent) || [];
  var picked = [];
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    var item = list[i];
    if (!item) continue;
    if (!libMatchesTag(item, tag)) continue;
    if (!libMatchesQuery(item, query)) continue;
    if (!libMatchesFamily(item, family, favorites, recent)) continue;
    picked.push(item);
  }
  if (family === LIB_RECENT_FAMILY) {
    return picked.sort(function (a, b) { return recent.indexOf(String(a.id)) - recent.indexOf(String(b.id)); });
  }
  if (opts && opts.sort && opts.sort !== 'default') return libApplySort(picked, opts.sort);
  if (family) {
    return family === LIB_CUSTOM_FAMILY
      ? libSortCustomWithFavorites(picked, favorites)
      : picked;
  }
  var favs = [];
  var rest = [];
  for (var j = 0; j < picked.length; j += 1) {
    if (libIsFavorite(picked[j], favorites)) favs.push(picked[j]);
    else rest.push(picked[j]);
  }
  return favs.concat(rest);
}


var sessionLibrarySort = '';
function libSortMode() {
  if (sessionLibrarySort) return sessionLibrarySort;
  var raw = '';
  try {
    raw = libraryStorage().getItem(LIB_SORT_KEY) || '';
  } catch (error) {
    raw = '';
  }
  for (var i = 0; i < LIB_SORTS.length; i += 1) {
    if (LIB_SORTS[i] === raw) return raw;
  }
  return 'default';
}

function libSetSortMode(mode) {
  var id = 'default';
  for (var i = 0; i < LIB_SORTS.length; i += 1) {
    if (LIB_SORTS[i] === mode) id = mode;
  }
  sessionLibrarySort = id;
  try {
    libraryStorage().setItem(LIB_SORT_KEY, id);
  } catch (error) { /* Storage can be disabled. */ }
  return id;
}

function libItemDetail(item) {
  var light = Number(item && item.light) || 0;
  var dark = Number(item && item.dark) || 0;
  return light + dark;
}

function libSavedRank(item) {

  var raw = String((item && item.saved_at) || '');
  if (!/^\d{4}-\d{2}-\d{2}/.test(raw)) return '';
  return raw;
}

function libApplySort(items, mode) {
  var list = (items instanceof Array ? items : []).slice();
  if (mode === 'name') {
    list.sort(function (a, b) {
      return String(a && a.name || '').localeCompare(String(b && b.name || ''), document.documentElement.lang || undefined);
    });
    return list;
  }
  if (mode === 'detail') {
    list.sort(function (a, b) {
      var diff = libItemDetail(b) - libItemDetail(a);
      if (diff) return diff;
      return String(a && a.name || '').localeCompare(String(b && b.name || ''), document.documentElement.lang || undefined);
    });
    return list;
  }

  list.sort(function (a, b) {
    var ra = libSavedRank(a);
    var rb = libSavedRank(b);
    if (ra !== rb) {
      if (!ra) return 1;
      if (!rb) return -1;
      return ra < rb ? 1 : -1;
    }
    return String(a && a.name || '').localeCompare(String(b && b.name || ''), document.documentElement.lang || undefined);
  });
  return list;
}

function libRenderSortSelect() {
  var select = $('lib-sort');
  if (!select) return;
  select.value = libSortMode();
}

/**
 * The "Custom" view pins favorites first too: users often collect dozens of
 * their own themes, and the frequently used ones should surface first.
 */
function libSortCustomWithFavorites(items, favorites) {
  var favs = [];
  var rest = [];
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    if (libIsFavorite(list[i], favorites)) favs.push(list[i]);
    else rest.push(list[i]);
  }
  return favs.concat(rest);
}

/**
 * Count badges for the family tabs: how many entries each family yields under the
 * current tag + search. The badge thus reads "this tab will show N", and a 0 makes
 * it obvious the filters need changing.
 */
function libFamilyCounts(items, query, tag, favorites, recent) {
  var counts = { all: 0, fav: 0, custom: 0, recent: 0, families: {} };
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) counts.families[LIB_FAMILIES[i].id] = 0;
  var list = items instanceof Array ? items : [];
  for (var j = 0; j < list.length; j += 1) {
    var item = list[j];
    if (!item) continue;
    if (!libMatchesTag(item, tag)) continue;
    if (!libMatchesQuery(item, query)) continue;
    counts.all += 1;
    var family = libFamilyOf(item);
    if (counts.families[family] !== undefined) counts.families[family] += 1;
    if (libIsFavorite(item, favorites)) counts.fav += 1;
    if (recent && recent.indexOf(String(item.id)) >= 0) counts.recent += 1;
    if (!item.builtin) counts.custom += 1;
  }
  return counts;
}

/** Keep only favorite ids that still exist in the library (used by refreshLibrary; stale ids are pruned). */
function libPruneFavorites(favorites, items) {
  var alive = {};
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    if (list[i] && list[i].id) alive[String(list[i].id)] = true;
  }
  var out = [];
  var favs = favorites instanceof Array ? favorites : [];
  for (var j = 0; j < favs.length; j += 1) {
    var id = favs[j] === null || favs[j] === undefined ? '' : String(favs[j]);
    if (id && alive[id] && out.indexOf(id) < 0) out.push(id);
  }
  return out;
}

function libStorageRead(storage, key) {
  if (!storage) return null;
  try { return storage.getItem(key); } catch (error) { return null; }
}

function libStorageWrite(storage, key, value) {
  if (!storage) return false;
  try { storage.setItem(key, value); return true; } catch (error) { return false; }
}

/** Favorite id list: invalid JSON / non-arrays / non-string items are dropped (localStorage may be corrupted). */
function libReadIdList(storage, key) {
  var raw = libStorageRead(storage, key);
  if (!raw) return [];
  var parsed = null;
  try { parsed = JSON.parse(raw); } catch (error) { parsed = null; }
  if (!(parsed instanceof Array)) return [];
  var out = [];
  for (var i = 0; i < parsed.length; i += 1) {
    var id = parsed[i];
    if (typeof id === 'string' && id && out.indexOf(id) < 0) out.push(id);
  }
  return out;
}

function libWriteIdList(storage, key, ids) {
  var list = ids instanceof Array ? ids.slice(0, 400) : [];
  return libStorageWrite(storage, key, JSON.stringify(list));
}

/** Collapsed state: only group names listed in `names`, and only `true` counts (everything expanded by default). */
function libReadCollapsed(storage, key, names) {
  var raw = libStorageRead(storage, key);
  var out = {};
  if (!raw) return out;
  var parsed = null;
  try { parsed = JSON.parse(raw); } catch (error) { return out; }
  if (!parsed || typeof parsed !== 'object') return out;
  var known = names instanceof Array ? names : [];
  for (var i = 0; i < known.length; i += 1) {
    if (parsed[known[i]] === true) out[known[i]] = true;
  }
  return out;
}

function libWriteCollapsed(storage, key, collapsed, names) {
  var known = names instanceof Array ? names : [];
  var out = {};
  for (var i = 0; i < known.length; i += 1) {
    if (collapsed && collapsed[known[i]]) out[known[i]] = true;
  }
  return libStorageWrite(storage, key, JSON.stringify(out));
}
/* ===== LIB_CORE_END ===== */

/** Local view state of the theme library (v0.2.0: family tabs + search + favorites + tag filter). */
var libraryState = {
  items: [],
  filter: '',    // tag filter ('' = all tags)
  family: '',    // family filter ('' = all; LIB_FAV_FAMILY = favorites virtual family)
  query: '',     // search text
  favorites: [], // favorite id list (synced with localStorage)
  recent: libReadIdList(libraryStorage(), RECENT_KEY),
  tagsExpanded: false,
  lastShuffle: null
};
/** Switch to the "Custom" tab to show an action's result (save / import / restore). Tag filter
 *  and search are cleared too, so a leftover query can't hide the entry just added. */
function showCustomFamilyResult() {
  libraryState.family = LIB_CUSTOM_FAMILY;
  libraryState.filter = '';
  libraryState.query = '';
  var search = $('lib-search');
  if (search) search.value = '';
}
/** Empty-library text; a function so the translation is looked up at render time. */
function libEmptyText() {
  return tsT('library.empty', '仓库还是空的。调好配色后点「保存」存成自己的主题；别人分享的 .zip / .json 用「导入」收进来，之后随时切回来，也能「分享」成 zip 发给别人。');
}

// Library card node cache: search / tag / family filters only change which cards are
// visible, so cards need not be rebuilt.
// Key = theme id, value = { sig, node }. sig excludes the filters -- the tag chips'
// aria-pressed is synced in place afterwards.
var libraryCardCache = Object.create(null);

/** localStorage handle: setItem throws in private mode / when disabled, so all reads and writes go through try/catch. */
function libraryStorage() {
  try { return window.localStorage; } catch (error) { return null; }
}

function saveLibraryFavorites() {
  libWriteIdList(libraryStorage(), LIB_FAV_KEY, libraryState.favorites);
}


function libRecentIndexOf(id) {
  var key = String(id || '');
  if (!key) return -1;
  return libraryState.recent.indexOf(key);
}

function libRecentPush(id) {
  var key = String(id || '');
  if (!key) return false;
  var list = libraryState.recent.filter(function (each) { return each !== key; });
  list.unshift(key);
  libraryState.recent = list.slice(0, RECENT_LIMIT);
  libWriteIdList(libraryStorage(), RECENT_KEY, libraryState.recent);
  return true;
}

function libPruneRecent(items) {
  var alive = Object.create(null);
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    if (list[i] && list[i].id !== undefined) alive[String(list[i].id)] = true;
  }
  var kept = [];
  for (var j = 0; j < libraryState.recent.length; j += 1) {
    if (alive[libraryState.recent[j]]) kept.push(libraryState.recent[j]);
  }
  if (kept.length !== libraryState.recent.length) {
    libraryState.recent = kept;
    libWriteIdList(libraryStorage(), RECENT_KEY, kept);
  }
}

function currentThemeLabel() {
  var meta = $('meta-name');
  if (!meta) return '';
  return String(meta.textContent || '').trim();
}


function libRecentMaybeRecord(name) {
  var label = String(name || '').trim();
  if (!label) return false;
  var items = libraryState.items || [];
  if (!items.length) return false;
  var hit = null;
  for (var i = 0; i < items.length; i += 1) {
    if (items[i] && String(items[i].name || '').trim() === label) { hit = items[i]; break; }
  }
  if (!hit) return false;
  if (libRecentIndexOf(hit.id) === 0) return false;
  libRecentPush(hit.id);

  renderLibrary();
  return true;
}

/** Toggle a favorite; returns true when it was just favorited. */
function toggleLibraryFavorite(id) {
  var key = String(id || '');
  if (!key) return false;
  var at = libraryState.favorites.indexOf(key);
  if (at >= 0) libraryState.favorites.splice(at, 1);
  else libraryState.favorites.push(key);
  saveLibraryFavorites();
  return at < 0;
}

/** Re-rendering replaces the star button; give focus back to the same card's star (keyboard users keep their place). */
function refocusLibraryFav(id) {
  var list = $('lib-list');
  if (!list || typeof list.querySelectorAll !== 'function') return;
  var nodes = list.querySelectorAll('.lib-fav');
  for (var i = 0; i < nodes.length; i += 1) {
    if (nodes[i].getAttribute('data-fav-id') === String(id)) {
      if (typeof nodes[i].focus === 'function') nodes[i].focus();
      return;
    }
  }
}

/** Catalog editor: list of group names (collapsed state is persisted only for these known groups). */
function catalogGroupNames() {
  return CATALOG.map(function (group) { return groupStateKey(group); });
}

/** Load collapsed state (nts.catalog.collapsed); no record = everything expanded. */
function loadCatalogCollapsed() {
  state.collapsed = libReadCollapsed(libraryStorage(), LIB_COLLAPSE_KEY, catalogGroupNames());
}

function saveCatalogCollapsed() {
  libWriteCollapsed(libraryStorage(), LIB_COLLAPSE_KEY, state.collapsed, catalogGroupNames());
}

/** Collect tags from the entries, sorted by frequency (built-in themes ship with tags). */
function libraryTags(items) {
  var counts = {};
  items.forEach(function (item) {
    var tags = item && item.tags instanceof Array ? item.tags : [];
    tags.forEach(function (tag) {
      var key = String(tag || '').slice(0, 12);
      if (!key) return;
      counts[key] = (counts[key] || 0) + 1;
    });
  });
  return Object.keys(counts).sort(function (a, b) {
    if (counts[b] !== counts[a]) return counts[b] - counts[a];
    return a < b ? -1 : 1;
  });
}

/** Thumbnail swatch: the backend's preview snapshot for the current light/dark mode. */
function buildLibraryThumb(item) {
  var preview = null;
  if (item && item.preview && typeof item.preview === 'object') {
    preview = item.preview[state.mode] || item.preview.light || null;
  }
  var thumb = el('span', { class: 'lib-thumb', 'aria-hidden': 'true' });
  if (!preview) {
    thumb.classList.add('is-blank');
    return thumb;
  }
  // All values come from backend-validated variables, but still assign them one by one
  // via CSSOM: no HTML concatenation, no whole style string; invalid values are at worst ignored.
  try {
    if (preview.bg) thumb.style.backgroundColor = String(preview.bg);
    if (preview.gradient) thumb.style.backgroundImage = String(preview.gradient);
    if (preview.panel) thumb.style.borderColor = String(preview.panel);
  } catch (error) { /* some browsers reject a value; just ignore it */ }
  var dots = el('span', { class: 'lib-dots', 'aria-hidden': 'true' });
  var solidUser = preview.userSolid || preview.ai || '';
  if (solidUser) {
    var dotUser = el('span', { class: 'lib-dot' });
    try { dotUser.style.backgroundColor = String(solidUser); } catch (error) { /* ignore */ }
    dots.appendChild(dotUser);
  }
  if (preview.accent) {
    var dotAccent = el('span', { class: 'lib-dot lib-dot-accent' });
    try { dotAccent.style.backgroundColor = String(preview.accent); } catch (error) { /* ignore */ }
    dots.appendChild(dotAccent);
  }
  thumb.appendChild(dots);
  return thumb;
}


/** Card cache signature: only the factors that change this card's DOM (mode, favorite state, entry data). */
function libraryCardSig(item) {
  var tags = libTagsOf(item);
  return [
    state.mode,
    libIsFavorite(item, libraryState.favorites) ? '1' : '0',
    libFamilyOf(item),
    String(item && item.id !== undefined ? item.id : ''),
    String(item && item.name || ''),
    String(item && item.name || '') === currentThemeLabel() ? 'cur' : '',
    String(item && item.note || ''),
    String(item && item.saved_at || ''),
    String(item && item.light || 0),
    String(item && item.dark || 0),
    item && item.has_image ? '1' : '0',
    item && item.material ? String(item.material.preset || '') + '/' + String(item.material.quality || '') : '',
    item && item.contrast ? String(item.contrast.min) + '/' + String(item.contrast.fails) : '',
    tags.join('\u0001')
  ].join('\u0000');
}

/** A tag chip's pressed state depends on the current filter; sync it in place rather than rebuild the card for one attribute. */
function syncLibraryCardTags(node) {
  if (!node || !node.querySelectorAll) return;
  var chips = node.querySelectorAll('.lib-tag');
  for (var i = 0; i < chips.length; i += 1) {
    var pressed = chips[i].textContent === libraryState.filter ? 'true' : 'false';
    if (chips[i].getAttribute('aria-pressed') !== pressed) {
      chips[i].setAttribute('aria-pressed', pressed);
    }
  }
}

/** One library entry row (name/description via textContent, never concatenated HTML). */
function buildLibraryItem(item) {
  var name = String(item.name || item.id || tsT('library.item.untitled', '未命名'));
  var family = libFamilyOf(item);
  var favorite = libIsFavorite(item, libraryState.favorites);
  var meta = [];
  // A built-in theme's saved_at is "built-in"; do not display it as a time.
  if (item.builtin) meta.push(tsT('library.item.builtin', '内置'));
  else meta.push(String(item.saved_at || '').replace('T', ' ').slice(0, 16) || tsT('library.item.noTime', '未记录时间'));
  meta.push(tsT('library.item.counts', '亮 {{light}} / 暗 {{dark}} 项', { light: item.light || 0, dark: item.dark || 0 }));
  if (item.has_image) meta.push(tsT('library.item.hasImage', '含背景图'));

  // Family badge: family name + small dot (dot color via CSSOM, no style string).
  var familyDot = el('span', { class: 'lib-family-dot', 'aria-hidden': 'true' });
  try { familyDot.style.backgroundColor = libFamilyTone(family); } catch (error) { /* ignore */ }
  var familyChip = el('span', { class: 'lib-family-chip', 'data-family': family }, [
    familyDot,
    el('span', { text: libFamilyLabel(family) })
  ]);

  // Material badge: this theme ships advanced materials (switched along with it); older themes show only the family.
  var nameRow = el('span', { class: 'lib-name-row' }, [
    el('span', { class: 'lib-name-text', text: name, title: name }),
    familyChip
  ]);
  if (item.material && item.material.enabled === false) {
    // Color-only themes carry materials.enabled=false explicitly: they turn advanced
    // materials off, so the badge must say "no materials", not a specific preset name.
    nameRow.appendChild(el('span', {
      class: 'lib-material-chip is-off',
      title: tsT('library.item.noMaterialTitle', '这套主题不带高级材质；应用后会关闭高级材质（你手调的参数会保留）')
    }, [el('span', { text: tsT('library.item.noMaterial', '无材质') })]));
  } else if (item.material) {
    var materialLabel = String(item.material.label || item.material.preset || tsT('library.item.materialFallback', '材质'));
    var qualityLabel = String(item.material.quality_label || item.material.quality || '');
    var materialChip = el('span', {
      class: 'lib-material-chip',
      'data-preset': String(item.material.preset || ''),
      title: tsT('library.item.materialTitle', '自带高级材质：{{material}}', {
        material: materialLabel + (qualityLabel ? ' · ' + qualityLabel : '')
      }) + (item.material.enabled === false ? tsT('library.item.materialOffByDefault', '（主题里默认未启用）') : '')
    }, [el('span', { text: tsT('library.item.materialChip', '材质 · {{material}}', { material: materialLabel }) })]);
    nameRow.appendChild(materialChip);
  }

  var contrast = item && item.contrast;
  if (contrast && contrast.min) {
    var contrastFails = Number(contrast.fails) || 0;
    var contrastText = Number(contrast.min).toFixed(2) + ':1';
    var contrastTitle = tsT('library.tools.contrast', 'Worst contrast: {{ratio}} · {{count}} failures. Open comparison.',
      { ratio: contrastText, count: contrastFails });
    nameRow.appendChild(el('button', {
      class: 'lib-ratio' + (contrastFails ? ' is-fail' : ''),
      type: 'button',
      tabIndex: -1,
      title: contrastTitle,
      'data-min': String(contrast.min),
      'data-fails': String(contrastFails),
      'data-compare-open': '1',
      'aria-label': contrastTitle,
      onclick: function () { openCompareFor(item); }
    }, [el('span', { text: contrastText })]));
  }
  if (name && name === currentThemeLabel()) {
    nameRow.appendChild(el('span', {
      class: 'lib-current-chip',
      title: tsT('library.tools.current', 'Current')
    }, [el('span', { text: tsT('library.tools.current', 'Current') })]));
  }
  var text = el('div', { class: 'lib-text' }, [nameRow]);
  // The description is truncated to one line; the title holds the full text on hover.
  var noteText = item.note ? String(item.note) : '';
  if (noteText) {
    text.appendChild(el('span', {
      class: 'lib-note',
      text: noteText.slice(0, 160),
      title: noteText.slice(0, 500)
    }));
  } else if (!item.builtin) {
    // A custom entry without a description gets a hint, so users need not guess what "Edit" does.
    text.appendChild(el('span', {
      class: 'lib-note is-empty',
      text: tsT('library.item.noNote', '没有简介 · 点「编辑」写一句，分享时会一起带走'),
      title: tsT('library.item.noNoteTitle', '简介会显示在卡片上，也会写进分享包的 theme.json')
    }));
  }
  text.appendChild(el('span', { class: 'lib-meta', text: meta.join(' · ') }));
  var tags = libTagsOf(item);
  if (tags.length) {
    var tagRow = el('span', { class: 'lib-tags' });
    tags.slice(0, 4).forEach(function (tag) {
      var chip = el('button', {
        type: 'button', class: 'lib-tag', text: tag,
        title: tsT('library.item.tagTitle', '只看「{{tag}}」主题', { tag: tag }),
        'aria-pressed': libraryState.filter === tag ? 'true' : 'false'
      });
      chip.addEventListener('click', function (event) {
        event.stopPropagation();
        libraryState.filter = libraryState.filter === tag ? '' : tag;
        renderLibrary();
      });
      tagRow.appendChild(chip);
    });
    text.appendChild(tagRow);
  }
  var main = el('div', { class: 'lib-main' }, [buildLibraryThumb(item), text]);

  // Favorite button: lives in the action row with text. It used to be a translucent ☆
  // in the top-right corner (22px, opacity .5), and the feedback was "can't find the
  // favorite button at all", so it is now a labelled button (re-rendering replaces the
  // node; focus is still restored via data-fav-id).
  var favBtn = el('button', {
    type: 'button',
    class: 'btn btn-mini lib-fav' + (favorite ? ' is-on' : ''),
    text: favorite ? tsT('library.item.favOn', '★ 已收藏') : tsT('library.item.favOff', '☆ 收藏'),
    'data-fav-id': String(item.id || ''),
    'aria-pressed': favorite ? 'true' : 'false',
    'aria-label': favorite
      ? tsT('library.item.unfavAria', '取消收藏 {{name}}', { name: name })
      : tsT('library.item.favAria', '收藏 {{name}}', { name: name }),
    title: favorite
      ? tsT('library.item.unfavTitle', '取消收藏')
      : tsT('library.item.favTitle', '收藏（进「收藏」分类，并在「全部」里置顶）')
  });
  favBtn.addEventListener('click', function (event) {
    event.stopPropagation();
    var nowFavorite = toggleLibraryFavorite(item.id);
    renderLibrary();
    if (!refocusLibraryFav(item.id) && !nowFavorite) focusFamilyTab(LIB_FAV_FAMILY);
  });

  // "Edit": name / note / tags only. Built-in themes cannot be edited (the backend
  // rejects it too), so render the button based on builtin.
  var metaBtn = item.builtin ? null : el('button', {
    type: 'button', class: 'btn btn-mini lib-meta', 'data-act': 'meta', text: tsT('library.item.edit', '编辑'),
    title: tsT('library.item.editTitle', '改名称 / 备注 / 标签（id 不变，收藏和排程照旧认它）'),
    'aria-label': tsT('library.item.editAria', '编辑主题信息 {{name}}', { name: name })
  });
  if (metaBtn) {
    metaBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      openMetaModal(item);
    });
  }

  var applyBtn = el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'apply', text: tsT('library.item.apply', '应用'),
    'aria-label': tsT('library.item.applyAria', '应用主题 {{name}}', { name: name })
  });
  var exportBtn = el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'export', text: tsT('library.item.share', '分享'),
    title: tsT('library.item.shareTitle', '导出成 zip（含背景图片）'),
    'aria-label': tsT('library.item.shareAria', '分享主题 {{name}}', { name: name })
  });
  // "JSON": export this theme as plain text (paste into a chat / issue to share).
  // "Share" gives a zip (with background images); they complement each other: zip for
  // archiving, JSON for pasting.
  var jsonBtn = el('button', {
    type: 'button', class: 'btn btn-mini lib-json', 'data-act': 'json', text: 'JSON',
    title: tsT('library.item.jsonTitle', '复制成纯文本 JSON（贴给对方，对方在「导入」里粘贴即可）'),
    'aria-label': tsT('library.item.jsonAria', '复制主题 JSON {{name}}', { name: name })
  });
  jsonBtn.addEventListener('click', function (event) {
    event.stopPropagation();
    copyLibraryItemJson(item, jsonBtn);
  });

  // Built-in themes cannot be deleted (the backend rejects it too), so no delete button.
  var deleteBtn = item.builtin ? null : el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'delete', text: tsT('library.item.delete', '删除'),
    'aria-label': tsT('library.item.deleteAria', '删除主题 {{name}}', { name: name })
  });

  applyBtn.addEventListener('click', function () { applyLibraryItem(item, applyBtn); });
  exportBtn.addEventListener('click', function () { exportLibraryItem(item, exportBtn); });
  if (deleteBtn) {
    deleteBtn.addEventListener('click', function () { deleteLibraryItem(item, deleteBtn); });
  }

  [favBtn, applyBtn, exportBtn, jsonBtn].forEach(function (b) { b.tabIndex = -1; });
  if (metaBtn) metaBtn.tabIndex = -1;
  if (deleteBtn) deleteBtn.tabIndex = -1;
  if (tags.length) tagRow.querySelectorAll('.lib-tag').forEach(function (b) { b.tabIndex = -1; });
  var libBtns = [favBtn, applyBtn, exportBtn, jsonBtn];
  if (metaBtn) libBtns.push(metaBtn);
  if (deleteBtn) libBtns.push(deleteBtn);
  var row = el('li', { class: 'lib-item' + (favorite ? ' is-fav' : '') }, [
    main,
    el('div', { class: 'lib-btns' }, libBtns)
  ]);
  row.setAttribute('data-id', String(item.id || ''));
  row.tabIndex = -1;
  row.addEventListener('keydown', function (event) { libCardKeydown(event, row, item); });
  // Hover / focus preview: the right-hand preview switches to this theme while the
  // pointer or keyboard focus is on the card, and restores on leave. (Hovering the
  // card's buttons bubbles up to the card too, which is fine: previewing it while the
  // user looks at its apply/share buttons is exactly right.)
  row.addEventListener('pointerenter', function () { showHoverPreview(item); });
  row.addEventListener('pointerleave', function () { clearHoverPreview(); });
  row.addEventListener('focusin', function () { showHoverPreview(item); });
  row.addEventListener('focusout', function () { clearHoverPreview(); });
  return row;
}


function libFamilyScrollIntoView(tab) {
  var host = $('lib-family-tabs');
  if (!host || !tab || typeof host.scrollLeft !== 'number') return;
  var span = host.clientWidth;
  if (!span) return;
  var max = Math.max(0, host.scrollWidth - span);

  var hostBox = host.getBoundingClientRect();
  var tabBox = tab.getBoundingClientRect();
  var left = tabBox.left - hostBox.left + host.scrollLeft;
  var width = tabBox.width;
  var pad = 6;

  var target = 0;
  if (left < host.scrollLeft + pad) target = left - pad;
  else if (left + width > host.scrollLeft + span - pad) target = left + width - span + pad;
  else return;
  target = Math.max(0, Math.min(target, max));
  if (Math.abs(host.scrollLeft - target) > 1) host.scrollLeft = target;
}

function libFamilyScrollIntoViewSoon(tab) {
  if (!tab) return;
  var run = function () { libFamilyScrollIntoView(tab); };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(function () { requestAnimationFrame(run); });
  } else {
    setTimeout(run, 32);
  }
}

var libFamilyResizeTimer = null;
window.addEventListener('resize', function () {
  if (libFamilyResizeTimer) clearTimeout(libFamilyResizeTimer);
  libFamilyResizeTimer = setTimeout(function () {
    libFamilyResizeTimer = null;
    var host = $('lib-family-tabs');
    if (!host) return;
    var active = host.querySelector('.lib-family-tab.is-active');
    if (active) libFamilyScrollIntoView(active);
  }, 160);
});

/** Family tab row (All / Favorites / the six real families), with count badges under the current filters. */
function renderLibraryFamilies() {
  var host = $('lib-family-tabs');
  if (!host) return;
  host.innerHTML = '';
  if (!libraryState.items.length) { host.hidden = true; return; }
  host.hidden = false;
  var counts = libFamilyCounts(libraryState.items, libraryState.query, libraryState.filter,
    libraryState.favorites, libraryState.recent);
  var makeTab = function (family, label, count) {
    var tab = el('button', {
      type: 'button',
      class: 'lib-family-tab' + (libraryState.family === family ? ' is-active' : ''),
      'data-family': family,
      'aria-pressed': libraryState.family === family ? 'true' : 'false'
    }, [
      el('span', { class: 'lib-family-tab-label', text: label }),
      el('span', { class: 'lib-family-tab-count', text: String(count) })
    ]);
    tab.addEventListener('click', function () {
      libraryState.family = family;
      renderLibrary();
    });
    tab.addEventListener('focus', function () { libFamilyScrollIntoView(tab); });
    return tab;
  };
  host.appendChild(makeTab('', tsT('library.filter.all', '全部'), counts.all));
  // "Favorites" and "Custom" are always shown (even at 0): they are entry points, not
  // filter results; hidden, users would never learn the feature exists. Each has a
  // guiding empty-state text.
  host.appendChild(makeTab(LIB_FAV_FAMILY, tsT('library.family.favorites', '收藏'), counts.fav));
  host.appendChild(makeTab(LIB_RECENT_FAMILY, tsT('library.tools.recent', 'Recent'), counts.recent));
  host.appendChild(makeTab(LIB_CUSTOM_FAMILY, libFamilyLabel(LIB_CUSTOM_FAMILY), counts.custom));
  LIB_FAMILIES.forEach(function (family) {
    host.appendChild(makeTab(family.id, libFamilyLabel(family.id), counts.families[family.id] || 0));
  });
  var active = host.querySelector('.lib-family-tab.is-active');
  if (active) libFamilyScrollIntoViewSoon(active);
}

/** When un-favoriting the last card makes it disappear, return focus to the "Favorites" tab. */
function focusFamilyTab(family) {
  var host = $('lib-family-tabs');
  if (!host || typeof host.querySelectorAll !== 'function') return;
  var tabs = host.querySelectorAll('.lib-family-tab');
  for (var i = 0; i < tabs.length; i += 1) {
    if (tabs[i].getAttribute('data-family') === String(family)) {
      if (typeof tabs[i].focus === 'function') tabs[i].focus();
      return;
    }
  }
}

/** Tag filter bar (with many built-in themes, a dozen-plus rows per screen are hard to scan). */
function renderLibraryFilters() {
  var host = $('lib-filters');
  if (!host) return;
  var tags = libraryTags(libraryState.items);
  host.innerHTML = '';
  if (!tags.length) { host.hidden = true; return; }
  host.hidden = false;
  var makeChip = function (label, value) {
    var chip = el('button', {
      type: 'button',
      class: 'lib-chip' + (libraryState.filter === value ? ' is-active' : ''),
      text: label,
      'aria-pressed': libraryState.filter === value ? 'true' : 'false'
    });
    chip.addEventListener('click', function () {
      libraryState.filter = value;
      renderLibrary();
    });
    return chip;
  };
  host.appendChild(makeChip(tsT('library.filter.all', '全部'), ''));

  var active = libraryState.filter;
  var ordered = tags;
  if (active && ordered.indexOf(active) >= 0) {
    ordered = [active].concat(tags.filter(function (tag) { return tag !== active; }));
  }
  var overflow = ordered.length - LIB_TAG_VISIBLE;
  var shown = libraryState.tagsExpanded ? ordered : ordered.slice(0, LIB_TAG_VISIBLE);
  shown.forEach(function (tag) { host.appendChild(makeChip(tag, tag)); });
  if (overflow > 0) {
    var more = el('button', {
      type: 'button',
      class: 'lib-chip lib-chip-more',
      text: libraryState.tagsExpanded ? tsT('library.tools.hideTags', 'Show fewer tags')
        : tsT('library.tools.moreTags', '+{{count}} tags', { count: overflow }),
      'aria-expanded': libraryState.tagsExpanded ? 'true' : 'false'
    });
    more.addEventListener('click', function () {
      libraryState.tagsExpanded = !libraryState.tagsExpanded;
      renderLibraryFilters();
    });
    host.appendChild(more);
  }
}


function libCardNodes() {
  var list = $('lib-list');
  if (!list) return [];
  return Array.prototype.slice.call(list.querySelectorAll('.lib-item'));
}

function libRovingSync() {
  var cards = libCardNodes();
  if (!cards.length) return null;
  var id = libraryState.rovingId;
  var target = null;
  for (var i = 0; i < cards.length; i += 1) {
    if (id && cards[i].getAttribute('data-id') === String(id)) { target = cards[i]; break; }
  }
  if (!target) target = cards[0];
  cards.forEach(function (card) { card.tabIndex = card === target ? 0 : -1; });
  libraryState.rovingId = target.getAttribute('data-id') || '';
  return target;
}

function libRovingFocus(node) {
  if (!node) return;
  libraryState.rovingId = node.getAttribute('data-id') || '';
  libRovingSync();
  try { node.focus({ preventScroll: false }); } catch (error) { node.focus(); }
}

function libRovingMove(dx, dy) {
  var cards = libCardNodes();
  if (!cards.length) return null;
  var current = document.activeElement && document.activeElement.closest
    ? document.activeElement.closest('.lib-item') : null;
  var index = cards.indexOf(current);
  if (index < 0) return cards[0];
  var rects = cards.map(function (card) { return card.getBoundingClientRect(); });
  var base = rects[index];
  var best = -1;
  var bestScore = Infinity;
  for (var i = 0; i < cards.length; i += 1) {
    if (i === index) continue;
    var r = rects[i];
    var vGap = r.top - base.top;
    var hGap = r.left - base.left;
    if (dy > 0 && vGap <= 2) continue;
    if (dy < 0 && vGap >= -2) continue;
    if (dx > 0 && hGap <= 2) continue;
    if (dx < 0 && hGap >= -2) continue;
    var score = (dy !== 0 ? Math.abs(hGap) * 4 + Math.abs(vGap) : Math.abs(vGap) * 4 + Math.abs(hGap));
    if (score < bestScore) { bestScore = score; best = i; }
  }
  if (best < 0) {

    best = index + (dy > 0 || dx > 0 ? 1 : -1);
    if (best < 0 || best >= cards.length) return cards[index];
  }
  return cards[best];
}

function libRefocusCard(id) {
  var list = $('lib-list');
  if (!list) return;
  var node = libCardNodes().find(function (card) { return card.dataset.id === String(id); });
  if (node) libRovingFocus(node);
}

function libCardKeydown(event, row, item) {
  if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.target !== row) return;
  var key = event.key;
  var navigational = { ArrowDown: [0, 1], ArrowUp: [0, -1], ArrowRight: [1, 0], ArrowLeft: [-1, 0] };
  if (navigational[key]) {
    event.preventDefault();
    libRovingFocus(libRovingMove(navigational[key][0], navigational[key][1]));
    return;
  }
  if (key === 'Enter' || key === ' ') {
    event.preventDefault();
    var applyBtn = row.querySelector('[data-act="apply"]');
    if (applyBtn) applyBtn.click();
    return;
  }
  var lower = String(key).toLowerCase();
  if (lower === 'f') {
    event.preventDefault();
    var favBtn = row.querySelector('.lib-fav');
    if (favBtn) favBtn.click();
    libRefocusCard(item.id);
    return;
  }
  if (lower === 's') {
    event.preventDefault();
    var shareBtn = row.querySelector('[data-act="export"]');
    if (shareBtn) shareBtn.click();
    libRefocusCard(item.id);
    return;
  }
  if (lower === 'c') {
    event.preventDefault();
    openCompareFor(item);
    return;
  }
  if (lower === 'j') {
    event.preventDefault();
    var jsonBtn = row.querySelector('[data-act="json"]');
    if (jsonBtn) jsonBtn.click();
    libRefocusCard(item.id);
    return;
  }
  if (lower === 'e') {
    event.preventDefault();
    var metaBtn = row.querySelector('[data-act="meta"]');
    if (metaBtn) metaBtn.click();
    libRefocusCard(item.id);
    return;
  }
  if (key === 'Delete') {
    var delBtn = row.querySelector('[data-act="delete"]');
    if (delBtn) { event.preventDefault(); delBtn.click(); }
  }
}

/** Render the library list filtered by family + tag + search; favorites are pinned first in "All". */
function renderLibrary() {
  var list = $('lib-list');
  var empty = $('lib-empty');
  if (!list) return null;
  var items = libraryState.items;
  var visible = libSelectLibrary(items, {
    query: libraryState.query,
    tag: libraryState.filter,
    family: libraryState.family,
    favorites: libraryState.favorites, recent: libraryState.recent, sort: libSortMode()
  });


  var focusInside = false;
  var focusId = '';
  if (list.contains(document.activeElement) && document.activeElement.closest) {
    var focusedCard = document.activeElement.closest('.lib-item');
    if (focusedCard) { focusInside = true; focusId = focusedCard.getAttribute('data-id') || ''; }
  }
  // Reuse card nodes from the last render: typing a search only adds/removes visible items
  // instead of rebuilding the whole list (24 themes x 10+ nodes).
  var keep = Object.create(null);
  list.innerHTML = '';
  visible.forEach(function (item) {
    var key = String(item && item.id !== undefined ? item.id : '');
    keep[key] = true;
    var sig = libraryCardSig(item);
    var cached = libraryCardCache[key];
    if (!cached || cached.sig !== sig) {
      cached = { sig: sig, node: buildLibraryItem(item) };
      libraryCardCache[key] = cached;
    }
    syncLibraryCardTags(cached.node);
    list.appendChild(cached.node);
  });
  Object.keys(libraryCardCache).forEach(function (key) {
    if (!keep[key]) delete libraryCardCache[key];
  });
  if (empty) {
    empty.hidden = visible.length > 0;
    if (!items.length) empty.textContent = libEmptyText();
    else if (libraryState.query) {
      empty.textContent = tsT('library.emptyState.query', '没有匹配「{{query}}」的主题，换个词试试，或点「全部」清空筛选。',
        { query: libraryState.query });
    } else if (libraryState.family === LIB_RECENT_FAMILY) {
      empty.textContent = tsT('library.tools.emptyRecent', 'Apply a theme to see it here.');
    } else if (libraryState.family === LIB_FAV_FAMILY) {
      empty.textContent = tsT('library.emptyState.favorites', '还没有收藏任何主题：点主题卡片下方的「☆ 收藏」按钮就能收藏，收藏会排在「全部」最前面。');
    } else if (libraryState.family === LIB_CUSTOM_FAMILY) {
      empty.textContent = tsT('library.emptyState.custom', '还没有自定义主题：「导入包」可以导入别人分享的 .zip / .json；调好配色点「保存」也会存到这里。');
    } else if (libraryState.filter) {
      empty.textContent = tsT('library.emptyState.tag', '没有「{{tag}}」标签的主题，点「全部」看所有。', { tag: libraryState.filter });
    } else {
      empty.textContent = tsT('library.emptyState.family', '这个风格族下还没有主题，试试别的族或「全部」。');
    }
  }
  renderLibraryFamilies();
  renderLibraryFilters();
  var roving = libRovingSync();
  if (focusInside) {
    var want = libCardNodes().find(function (node) { return node.dataset.id === focusId; });
    libRovingFocus(want || roving);
  }
  return visible;
}


var libInflight = null;
var libraryReadId = 0;
function refreshLibrary(options) {
  if (libInflight && !(options && options.fresh) && !state.busy) return libInflight;
  var p = refreshLibraryNow(++libraryReadId);
  libInflight = p;
  var clear = function () { if (libInflight === p) libInflight = null; };
  p.then(clear, clear);
  return p;
}

async function refreshLibraryNow(readId) {
  var list = $('lib-list');
  var empty = $('lib-empty');
  if (!list) return null;
  try {
    var result = await callPlugin('list_themes', {});
    if (readId !== libraryReadId) return libraryState.items;
    // The backend returns {count, themes:[...]} (early versions used `items`; a field mismatch left the library forever blank).
    var items = result && result.themes instanceof Array ? result.themes
      : (result && result.items instanceof Array ? result.items : []);
    libraryState.items = items;
    libPruneRecent(items);
    // Favorites may hold ids of deleted themes: prune them against the new list and write back to localStorage.
    var prunedFavorites = libPruneFavorites(libraryState.favorites, items);
    if (prunedFavorites.length !== libraryState.favorites.length || prunedFavorites.join('\n') !== libraryState.favorites.join('\n')) {
      libraryState.favorites = prunedFavorites;
      saveLibraryFavorites();
    }
    // Unknown family values (old versions / hand edits) fall back to "All"; the two virtual families are valid.
    if (libraryState.family && libraryState.family !== LIB_FAV_FAMILY &&
        libraryState.family !== LIB_CUSTOM_FAMILY && libraryState.family !== LIB_RECENT_FAMILY &&
        libFamilyIds().indexOf(libraryState.family) < 0) {
      libraryState.family = '';
    }
    // If the current tag filter no longer exists in the new list, fall back to "All".
    if (libraryState.filter && libraryTags(items).indexOf(libraryState.filter) < 0) {
      libraryState.filter = '';
    }
    renderLibrary();
    // The schedule card's dropdown and "which theme it switches to" come from the library
    // list: re-render whenever the library changes, or it keeps showing old names after a
    // delete / rename / import (until the panel is reopened).
    // Re-render only, no RPC -- list_themes does not change the schedule config itself.
    if (scheduleState.loaded) renderSchedule();
    return items;
  } catch (error) {
    if (readId !== libraryReadId) return libraryState.items;
    // A library read failure must not block the editor: show a hint, do not throw.
    if (empty) {
      empty.hidden = false;
      empty.textContent = tsT('library.loadFailed', '仓库读取失败：{{error}}', { error: pluginErrorMessage(error) });
    }
    return null;
  }
}

/** "Shuffle": pick a built-in theme different from the last one and apply it; click again if you don't like it. */
async function shuffleLibraryTheme() {
  if (state.busy) return;
  var builtins = libraryState.items.filter(function (item) { return item.builtin; });
  if (!builtins.length) {
    await refreshLibrary();
    builtins = libraryState.items.filter(function (item) { return item.builtin; });
  }
  if (!builtins.length) {
    showToast(tsT('library.shuffle.none', '还没有可随机的内置主题'), 'error');
    return;
  }
  var pool = builtins.filter(function (item) { return item.id !== libraryState.lastShuffle; });
  if (!pool.length) pool = builtins;
  var pick = pool[Math.floor(Math.random() * pool.length)];
  libraryState.lastShuffle = pick.id;
  showToast(tsT('library.shuffle.picked', '换个心情：{{name}}', { name: String(pick.name || pick.id) }));
  await applyLibraryItem(pick, null);
}

async function saveLibraryTheme() {
  if (state.busy) return;
  var input = $('lib-name');
  var noteInput = $('lib-note');
  var name = input ? input.value.trim() : '';
  var note = noteInput ? noteInput.value.trim() : '';
  setBusy(true, tsT('library.save.busy', '正在保存到仓库…'));
  try {
    var args = {};
    if (name) args.name = name;
    if (note) args.note = note;
    var result = await callPlugin('save_theme', args);
    if (input) input.value = '';
    if (noteInput) noteInput.value = '';
    // After saving, switch to "Custom" so the user sees the entry just saved (built-ins are not in that view).
    showCustomFamilyResult();
    await refreshLibrary();
    var savedName = (result && result.theme && result.theme.name)
      || (result && result.name) || name || tsT('library.save.currentTheme', '当前主题');
    showToast(note
      ? tsT('library.save.doneWithNote', '已保存「{{name}}」到自定义（简介已写入，分享时一起带走）', { name: savedName })
      : tsT('library.save.done', '已保存「{{name}}」到自定义', { name: savedName }), 'ok');
    var renamedFrom = (result && result.renamed_from) || '';
    if (renamedFrom) {

      showNotice('ok', tsT('library.tools.renamed', 'A theme named “{{old}}” already exists. Saved as “{{name}}”.',
        { old: renamedFrom, name: savedName }));
    }
    setStatus(tsT('library.save.doneStatus', '已保存到自定义主题'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.save.failedNotice', '保存到仓库失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.save.failed', '保存失败'), 'error');
    setStatus(tsT('library.save.failedStatus', '保存到仓库失败'), 'error');
  } finally {
    setBusy(false);
  }
}

async function applyLibraryItem(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, tsT('library.apply.busy', '正在切换主题…'));

  var undoSnapshot = snapshotForUndo(state.palette);
  var beforeName = (undoSnapshot && undoSnapshot.name) || tsT('library.save.currentTheme', 'Current theme');
  try {
    var result = await callPlugin('apply_saved_theme', { id: item.id });
    clearPreviewState();
    pokeThemeWindows();
    // Wholesale replacement: unapplied panel changes become invalid, so clear them before reading back.
    // discardEdits also resets the name / enabled inputs to what the backend saved.
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    var reloaded = await reloadPalette({ silent: true, discardEdits: true });
    var label = (result && result.theme && result.theme.name)
      || (result && result.name) || item.name || tsT('library.apply.thisTheme', '该主题');
    // Materials follow the theme: say "materials switched too" when the theme ships them,
    // and "kept" when they are locked.
    var materialNote = '';
    if (result && result.material_applied && result.material_enabled === false) {
      materialNote = tsT('library.apply.materialOff', '，已关闭高级材质（这套主题不带材质）');
    } else if (result && result.material_applied) materialNote = tsT('library.apply.materialSwitched', '，材质已换成主题自带的那套');
    else if (result && result.material_present && result.material_locked) {
      materialNote = tsT('library.apply.materialLocked', '，材质已锁定（保留你手调的设置）');
    }
    var switchedText = tsT('library.apply.switched', '已切换到「{{name}}」', { name: label }) + materialNote;
    showNotice(reloaded ? 'ok' : 'warn', switchedText +
      (reloaded ? '' : '\n' + tsT('actions.readbackFailedHint', '回读失败，面板显示可能不是最新值，请点「重新读取」。')));
    showToast(switchedText, 'ok');
    setStatus(tsT('library.apply.doneStatus', '已切换主题') + (reloaded ? '' : tsT('actions.readbackFailedSuffix', '（回读失败）')), reloaded ? 'ok' : 'warn');
    libRecentPush(item.id);
    renderLibrary();
    lastApplyUndo = makeApplyUndo(undoSnapshot, switchedText, beforeName, result);
    syncUndoApplyButton();
    verifyAppliedShortly();
  } catch (error) {
    showNotice('error', tsT('library.apply.failedNotice', '切换主题失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.apply.failed', '切换失败'), 'error');
    setStatus(tsT('library.apply.failedStatus', '切换主题失败'), 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

async function exportLibraryItem(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, tsT('library.share.busy', '正在打包…'));
  try {
    var result = await callPlugin('export_theme_archive', { id: item.id });
    var data = result && result.data ? String(result.data) : '';
    if (!data) throw new Error(tsT('library.share.noContent', '后端没有返回包内容'));
    var bytes = base64ToBytes(data);
    var filename = (result && result.filename) || ((item.name || 'theme') + '.zip');
    downloadBlob(bytes, filename, 'application/zip');
    var images = result && typeof result.images === 'number' ? result.images : 0;
    showToast(images
      ? tsT('library.share.doneWithImages', '已导出 {{filename}}（含 {{images}} 张图）', { filename: filename, images: images })
      : tsT('library.share.done', '已导出 {{filename}}', { filename: filename }), 'ok');
    setStatus(tsT('library.share.doneStatus', '已导出分享包'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.share.failedNotice', '导出分享包失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.export.failed', '导出失败'), 'error');
    setStatus(tsT('library.share.failedStatus', '导出分享包失败'), 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

/** Export a saved theme as plain-text JSON into a read-only dialog for copying.
 *
 * Unlike "Share" (a zip that carries background images), this is plain text --
 * images cannot become text, so the backend strips variables referencing background
 * images and reports how many; we state that plainly here.
 * We also do not write to the clipboard directly: the dialog shows selectable
 * content, so users can still Ctrl+C manually if a clipboard write fails.
 */
async function copyLibraryItemJson(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, tsT('library.json.busy', '正在生成主题 JSON…'));
  try {
    var result = await callPlugin('theme_json', { theme_id: item.id });
    var payload = result && typeof result.payload === 'string' ? result.payload : '';
    if (!payload) throw new Error(tsT('library.json.noContent', '后端没有返回 JSON 内容'));
    var name = (result && result.name) || item.name || item.id || tsT('library.item.untitled', '未命名');
    var images = result && typeof result.images === 'number' ? result.images : 0;
    var light = result && typeof result.light === 'number' ? result.light : 0;
    var dark = result && typeof result.dark === 'number' ? result.dark : 0;
    var hint = tsT('library.json.hint',
      '这是「{{name}}」的纯文本主题（亮 {{light}} / 暗 {{dark}} 项）。贴给别人以后，对方在「导入」里粘贴就能用。',
      { name: name, light: light, dark: dark });
    if (images > 0) {
      hint += '\n' + tsT('library.json.imagesStripped',
        '注意：这套主题引用了 {{count}} 张背景图，图片没法放进文本，已从 JSON 里摘掉（对方会得到纯色背景）。要连图片一起分享请用「分享」导出 zip。',
        { count: images });
    }
    openModal('export', tsT('library.json.title', '主题 JSON · {{name}}', { name: name }), hint, payload);
    setStatus(tsT('library.json.doneStatus', '已生成主题 JSON，点「复制并关闭」即可复制'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.json.failedNotice', '生成主题 JSON 失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.json.failed', '生成失败'), 'error');
    setStatus(tsT('library.json.failedStatus', '生成主题 JSON 失败'), 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

/* ============================================================================
 * Theme library: edit name / note / tags (v0.4.18)
 * ==========================================================================*/

/** Open the "Edit theme info" dialog. Built-in themes cannot be edited (the backend rejects it too). */
function openMetaModal(item) {
  if (!item || item.builtin) return;
  state.metaItem = item;
  openModal('meta', tsT('library.meta.title', '编辑主题信息 · {{name}}', { name: String(item.name || item.id || '') }),
    tsT('library.meta.hint', '只改名称、备注和标签。条目 id 不变 —— 收藏和「按时段自动换主题」里选的都是它，改名不会让它们失效。'),
    '');
  $('meta-name-field').value = String(item.name || '');
  $('meta-note').value = String(item.note || '');
  $('meta-tags').value = (item.tags || []).join('，');
  var nameInput = $('meta-name-field');
  if (nameInput) {
    nameInput.focus();
    nameInput.select();
  }
}

/** "暖色，渐变 ; 深色" -> ['暖色','渐变','深色'] (at most 4, same rules as the backend's _theme_tags). */
function parseTagInput(text) {
  return String(text || '')
    .split(/[,，、;；\s]+/)
    .filter(function (part) { return part.trim(); })
    .map(function (part) { return part.trim().slice(0, 12); })
    .slice(0, 4);
}

async function saveThemeMeta() {
  var item = state.metaItem;
  if (!item) { closeModal(); return; }
  var name = String($('meta-name-field').value || '').trim();
  if (!name) {
    showToast(tsT('library.meta.nameRequired', '名称不能为空'));
    $('meta-name-field').focus();
    return;
  }
  var okBtn = $('modal-ok');
  var original = okBtn.textContent;
  okBtn.disabled = true;
  okBtn.textContent = tsT('library.meta.saving', '保存中…');
  try {
    var result = await callPlugin('update_theme_meta', {
      theme_id: item.id,
      name: name,
      note: String($('meta-note').value || '').trim(),
      tags: parseTagInput($('meta-tags').value)
    });
    state.metaItem = null;
    closeModal();
    await refreshLibrary();
    if (result && result.updated === false) {
      showToast(tsT('library.meta.unchanged', '没有变化，已保持原样'));
    } else {
      showToast(tsT('library.meta.updated', '已更新「{{name}}」', { name: String((result && result.name) || name) }), 'ok');
      setStatus(tsT('library.meta.updatedStatus', '主题信息已更新'), 'ok');
    }
  } catch (error) {
    // Keep the dialog open: what the user just typed is still there to fix and save again.
    showNotice('error', tsT('library.meta.failedNotice', '修改主题信息失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.meta.failed', '修改失败，看看左侧提示'));
  } finally {
    okBtn.disabled = false;
    okBtn.textContent = original;
  }
}

/* ============================================================================
 * First-use hint (v0.4.18)
 * ==========================================================================*/

var RESTART_HINT_KEY = 'nts.restartHint.dismissed';

/** Hint bar: shown until dismissed; dismissal is written to localStorage (if that fails in private mode it still works, it just shows again next time). */
function initRestartHint() {
  var bar = $('restart-hint');
  if (!bar) return;
  var dismissed = false;
  try { dismissed = localStorage.getItem(RESTART_HINT_KEY) === '1'; } catch (error) { dismissed = false; }
  bar.hidden = dismissed;
  if (dismissed) return;
  var closeBtn = $('restart-hint-close');
  if (!closeBtn) return;
  closeBtn.addEventListener('click', function () {
    bar.hidden = true;
    try { localStorage.setItem(RESTART_HINT_KEY, '1'); } catch (error) { /* ignore */ }
  });
}

function undoDeleteTheme(themeId) {
  return restoreTrashTheme(themeId);
}

async function deleteLibraryItem(item, button) {
  if (state.busy) return;
  var now = Date.now();
  var armed = pendingDelete.id === item.id && now - pendingDelete.at < LIB_CONFIRM_MS;
  if (!armed) {
    pendingDelete = { id: item.id, at: now };
    if (button) {
      button.classList.add('is-confirm');
      button.textContent = tsT('library.delete.confirmButton', '确认删除');
    }
    // This theme may be used by "auto-switch theme by time": after deletion those slots
    // just keep whatever theme is active instead of switching -- say so before deleting,
    // not after the user notices it stopped working.
    var refs = scheduleRefsFor(item.id);
    if (refs.length) {
      showNotice('warn', tsT('library.delete.scheduledNotice',
        '「{{name}}」正被自动换主题使用（{{slots}}）。删掉之后这些时段会保持当时正在用的主题，不会自动换。',
        { name: String(item.name || item.id), slots: refs.join(' / ') }));
      showToast(tsT('library.delete.clickAgainScheduled', '再点一次「确认删除」才会删掉（它正被自动换主题使用）'));
    } else {
      showToast(tsT('library.delete.clickAgain', '再点一次「确认删除」才会删掉'));
    }
    setTimeout(function () {
      // Disarm automatically on timeout so the button never stays in the confirm state.
      if (button) {
        button.classList.remove('is-confirm');
        button.textContent = tsT('library.item.delete', '删除');
      }
      if (pendingDelete.id === item.id) pendingDelete = { id: '', at: 0 };
    }, LIB_CONFIRM_MS);
    return;
  }
  pendingDelete = { id: '', at: 0 };
  if (button) button.disabled = true;
  setBusy(true, tsT('library.delete.busy', '正在删除…'));
  try {
    await callPlugin('delete_theme', { id: item.id });
    await refreshLibrary();
    // The backend silently drops slots that point to missing themes; the UI's
    // "now ... will switch to" must follow.
    if (scheduleState.loaded) await refreshSchedule();
    var goneName = String(item.name || item.id);
    var deleted = tsT('library.tools.deleted', 'Moved “{{name}}” to the trash.', { name: goneName });
    showNotice('ok', deleted, {
      label: tsT('library.tools.undoDelete', 'Undo deletion'),
      onClick: function () { undoDeleteTheme(item.id); }
    });
    showToast(deleted, 'ok');
    await refreshHealth({ fresh: true });
    setStatus(tsT('library.delete.doneStatus', '已删除仓库条目'), 'ok');
  } catch (error) {
    // The button was disabled above; it must be restored on failure, otherwise the card
    // stays greyed out forever and clicks do nothing (the list signature excludes the
    // disabled state, so a refresh reuses the same button).
    if (button) {
      button.disabled = false;
      button.classList.remove('is-confirm');
      button.textContent = tsT('library.item.delete', '删除');
    }
    showNotice('error', tsT('library.delete.failedNotice', '删除失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.delete.failed', '删除失败'), 'error');
    setStatus(tsT('library.delete.failed', '删除失败'), 'error');
  } finally {
    setBusy(false);
  }
}

/** Read a local zip / json file and import it into the library (library only, not applied). */
function pickArchiveFile() {
  var fileInput = $('lib-file');
  if (fileInput) fileInput.click();
}

function handleArchiveFile(file, fileInput) {
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (fileInput) fileInput.value = '';
    if (!base64) { showToast(tsT('library.file.readFailed', '读取文件失败'), 'error'); return; }
    importArchive(file.name, base64);
  };
  reader.onerror = function () {
    if (fileInput) fileInput.value = '';
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', tsT('library.file.readFailedNotice', '读取文件失败：{{error}}', { error: why }));
    showToast(tsT('library.file.readFailed', '读取文件失败'), 'error');
  };
  reader.readAsDataURL(file);
}

async function importArchive(filename, base64) {
  if (state.busy) return;
  setBusy(true, tsT('library.importArchive.busy', '正在导入分享包…'));
  try {
    var result = await callPlugin('import_theme_archive', {
      filename: filename || '',
      data: base64
    });
    // Imported entries always land in "Custom": switch there so the user sees the new
    // theme right away instead of hunting among twenty-plus built-ins in "All".
    showCustomFamilyResult();
    await refreshLibrary();
    var label = (result && result.theme && result.theme.name)
      || (result && result.name) || tsT('library.importArchive.untitled', '未命名主题');
    // import_theme_archive returns {imported, theme:{...has_image}, source_format, rejected}
    // with no top-level images count, so infer it from theme.has_image (zip images are
    // already on disk with references rewritten).
    var images = (result && result.theme && result.theme.has_image) ? 1 : 0;
    showNotice('ok', (images
      ? tsT('library.importArchive.doneWithImages', '已导入「{{name}}」到仓库（含 {{images}} 张背景图）', { name: label, images: images })
      : tsT('library.importArchive.done', '已导入「{{name}}」到仓库', { name: label })) +
      '\n' + tsT('library.importArchive.notApplied', '它还没有生效 —— 在列表里点「应用」才会切换过去。'));
    showToast(tsT('library.importArchive.doneShort', '已导入到仓库'), 'ok');
    setStatus(tsT('library.importArchive.doneShort', '已导入到仓库'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.importArchive.failedNotice', '导入分享包失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('actions.import.failed', '导入失败'), 'error');
    setStatus(tsT('library.importArchive.failedStatus', '导入分享包失败'), 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * Theme library backup / restore (v0.4.16)
 *
 * Why: previously there was only the single-theme share zip. After switching
 * computers, reinstalling, or accidentally deleting ``static/themes/``, users had
 * no way out for their dozens of custom themes -- the internal ``install.ps1``
 * backup is invisible and inaccessible to them. This adds whole-library in / out.
 *
 * Three constraints (matching backend ``_build_library_archive`` / ``import_library``):
 * 1. The backup must be portable: images are rewritten to ``url("images/<name>")``,
 *    with no absolute local paths;
 * 2. Restore **only adds, never overwrites**: when bringing an old library back after
 *    a reinstall, same-named themes may already exist -- keep both; never silently
 *    replace what the user just made;
 * 3. One broken entry does not ruin the whole package: ``skipped`` carries reasons,
 *    the rest are imported and reported.
 * ========================================================================== */

async function backupLibrary(button) {
  if (state.busy) return;
  if (!libraryState.items.length) {
    showToast(tsT('library.backup.empty', '主题仓库还是空的，没有可备份的主题'), 'warn');
    return;
  }
  setBusy(true, tsT('library.backup.busy', '正在打包主题库…'));
  if (button) button.disabled = true;
  try {
    var result = await saveThemeFile({ kind: 'library' });
    var count = result.themes || 0;
    // Images the backend could not pack (unreadable files): the backup is
    // incomplete, so say so instead of reporting a full backup.
    var skippedImages = Number(result.skipped_images) || 0;
    var skippedNote = skippedImages
      ? '\n' + tsT('backupSkippedImages',
        '有 {{count}} 张背景图读取失败、没有打进备份，恢复后这些主题会缺背景图。',
        { count: skippedImages })
      : '';
    showNotice(skippedImages ? 'warn' : 'ok',
      tsT('library.backup.doneTitle', '主题库已备份（{{count}} 套）', { count: count }) + '\n' + savedHint(result) +
      '\n' + tsT('library.backup.restoreHint', '换电脑或重装后，用「恢复备份」选这个 zip 就能整套拿回来（只新增、不覆盖）。') +
      skippedNote);
    showToast(tsT('library.backup.done', '已备份 {{count}} 套主题', { count: count }), 'ok');
    setStatus(tsT('library.backup.doneStatus', '已备份主题库'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.backup.failedNotice', '备份主题库失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.backup.failed', '备份失败'), 'error');
    setStatus(tsT('library.backup.failedStatus', '备份主题库失败'), 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

function pickLibraryBackupFile() {
  var input = $('lib-backup-file');
  if (input) input.click();
}

function handleLibraryBackupFile(file, fileInput) {
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (fileInput) fileInput.value = '';
    if (!base64) { showToast(tsT('library.file.readFailed', '读取文件失败'), 'error'); return; }
    restoreLibrary(file.name, base64);
  };
  reader.onerror = function () {
    if (fileInput) fileInput.value = '';
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', tsT('library.restore.readFailedNotice', '读取备份包失败：{{error}}', { error: why }));
    showToast(tsT('library.file.readFailed', '读取文件失败'), 'error');
  };
  reader.readAsDataURL(file);
}

async function restoreLibrary(filename, base64) {
  if (state.busy) return;
  setBusy(true, tsT('library.restore.busy', '正在恢复主题库…'));
  try {
    var result = await callPlugin('import_library', {
      filename: filename || '',
      data: base64
    });
    // Everything restored is "Custom": switch there so the user sees it right away.
    showCustomFamilyResult();
    await refreshLibrary();
    var imported = (result && result.imported) || 0;
    var skipped = (result && result.skipped) || [];
    var text = tsT('library.restore.done', '已从备份恢复 {{count}} 套主题到仓库', { count: imported });
    if (skipped.length) {
      var shown = [];
      for (var i = 0; i < skipped.length && i < 3; i++) {
        shown.push(tsT('library.restore.skippedItem', '· {{name}}：{{reason}}', {
          name: skipped[i].name || tsT('library.item.untitled', '未命名'),
          reason: skipped[i].reason || tsT('actions.import.failed', '导入失败')
        }));
      }
      text += '\n' + tsT('library.restore.skipped', '有 {{count}} 套没能恢复：', { count: skipped.length }) + '\n' +
        shown.join('\n') + (skipped.length > shown.length ? '\n…' : '');
    }
    text += '\n' + tsT('library.restore.notApplied', '恢复的主题只是进了仓库，点「应用」才会切换过去。');
    showNotice(skipped.length ? 'warn' : 'ok', text);
    showToast(tsT('library.restore.doneShort', '已恢复 {{count}} 套主题', { count: imported }), skipped.length ? 'warn' : 'ok');
    setStatus(tsT('library.restore.doneStatus', '已恢复主题库'), 'ok');
  } catch (error) {
    showNotice('error', tsT('library.restore.failedNotice', '恢复主题库失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.restore.failed', '恢复失败'), 'error');
    setStatus(tsT('library.restore.failedStatus', '恢复主题库失败'), 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * Auto-switch theme by time of day (v0.4.13)
 *
 * Division of labor: **the backend does the switching**, the panel only configures.
 * When a slot starts, the backend writes that slot's theme into theme.json and the
 * loader picks it up by polling within 1.5 s, so all windows (including ones without
 * the panel) change together -- never restyle from a front-end timer here, or it stops
 * working once the panel closes and fights the loader over the same <style>.
 * Slot boundaries are not computed here: the backend's windows are used as is, to
 * avoid a third definition of "when does morning start".
 * ========================================================================== */

/** Slot -> display name (Chinese source text, translated at render time in
 * scheduleZoneLabel). Boundaries always come from scheduleState.windows; no hours
 * are hard-coded in the front end. */
var SCHEDULE_ZONE_LABELS = { morning: '早晨', day: '白天', evening: '傍晚', night: '夜晚' };

var scheduleState = {
  schedule: { schema: 2, enabled: false, zones: {}, weekend_zones: {} },
  zones: ['morning', 'day', 'evening', 'night'],
  windows: [],
  now: null,
  loaded: false,
  /** Slots recorded in the schedule whose theme no longer exists in the library (backend get_schedule's stale). */
  stale: [],
  /** If any weekend slot has a theme, "use a different set on weekends" is on. */
  weekendEnabled: false,
  /** The card has unsaved choices (changed a dropdown / toggled a switch without saving). */
  dirty: false,
  /** Signature of the theme list used for the last options render: unchanged list = do not rebuild <option>s (keeps focus). */
  optionsSig: ''
};

/** Get one table's (weekday / weekend) {slot: theme id}. */
function scheduleMapOf(kind) {
  var schedule = scheduleState.schedule || {};
  var map = kind === 'weekend' ? schedule.weekend_zones : schedule.zones;
  return map && typeof map === 'object' ? map : {};
}

/** Whether the weekend table is actually in use (any non-empty slot). */
function scheduleWeekendHasValues(schedule) {
  var map = schedule && schedule.weekend_zones;
  if (!map || typeof map !== 'object') return false;
  var zones = scheduleState.zones.length ? scheduleState.zones : ['morning', 'day', 'evening', 'night'];
  return zones.some(function (zone) {
    return String(map[zone] || '').trim() !== '';
  });
}

/** Collect {slot: theme id} from a grid (#schedule-rows / #schedule-rows-weekend). */
function collectScheduleZones(containerId) {
  var out = {};
  var rows = $(containerId);
  var selects = rows ? rows.querySelectorAll('.schedule-select') : [];
  for (var i = 0; i < selects.length; i += 1) {
    var zone = selects[i].getAttribute('data-zone');
    if (!zone) continue;
    out[zone] = selects[i].value || '';
  }
  return out;
}

function scheduleZoneLabel(zone) {
  var key = String(zone);
  if (!Object.prototype.hasOwnProperty.call(SCHEDULE_ZONE_LABELS, key)) return key;
  return tsT('library.schedule.zone.' + key, SCHEDULE_ZONE_LABELS[key]);
}

/** "Weekend" / "Weekday" label for a day kind. */
function scheduleDayLabel(kind) {
  return kind === 'weekend'
    ? tsT('library.schedule.weekend', '周末')
    : tsT('library.schedule.weekday', '工作日');
}

function schedulePad2(value) {
  var n = Number(value);
  if (!isFinite(n)) n = 0;
  return (n < 10 ? '0' : '') + n;
}

/** "06:00–10:00"; a night slot crossing midnight shows as "22:00–next day 06:00". */
function scheduleWindowText(zone) {
  var win = null;
  scheduleState.windows.forEach(function (item) {
    if (item && item.zone === zone) win = item;
  });
  if (!win) return '';
  var start = schedulePad2(win.start) + ':00';
  var end = schedulePad2(win.end) + ':00';
  if (Number(win.start) > Number(win.end)) {
    return tsT('library.schedule.windowOvernight', '{{start}}–次日 {{end}}', { start: start, end: end });
  }
  return start + '–' + end;
}

/** Theme list -> options. Built-in and custom in two groups, built-in first (most people's default choice). */
function scheduleThemeGroups() {
  var builtin = [];
  var custom = [];
  (libraryState.items || []).forEach(function (item) {
    if (!item || !item.id) return;
    var option = { id: String(item.id), name: String(item.name || item.id) };
    if (item.builtin) builtin.push(option);
    else custom.push(option);
  });
  return { builtin: builtin, custom: custom };
}

function scheduleOptionsSig(groups) {
  var parts = [];
  groups.builtin.concat(groups.custom).forEach(function (option) {
    parts.push(option.id + '\u0001' + option.name);
  });
  return parts.join('\u0000');
}

/** Rebuild a slot's dropdown (only called when the theme list changes). */
function fillScheduleSelect(select, groups, current) {
  if (!select) return;
  select.innerHTML = '';
  var none = el('option', { value: '', text: tsT('library.schedule.optionNone', '（不换，保持原样）') });
  select.appendChild(none);
  [
    { label: tsT('library.schedule.groupBuiltin', '内置主题'), items: groups.builtin },
    { label: tsT('library.schedule.groupCustom', '我的主题'), items: groups.custom }
  ].forEach(function (group) {
    if (!group.items.length) return;
    var optgroup = el('optgroup', { label: group.label });
    group.items.forEach(function (option) {
      optgroup.appendChild(el('option', { value: option.id, text: option.name }));
    });
    select.appendChild(optgroup);
  });
  // A theme referenced by the schedule may have been deleted: add a placeholder option,
  // otherwise assigning select.value silently fails, the UI shows the first item
  // ("don't switch") while the schedule still holds that id (exposed only on next save).
  var known = groups.builtin.concat(groups.custom).some(function (option) {
    return option.id === current;
  });
  if (current && !known) {
    select.appendChild(el('option', { value: current, text: tsT('library.schedule.missing', '{{name}}（已不存在）', { name: current }) }));
  }
  select.value = current || '';
}

/** Remember that the schedule card has unsaved choices and mark the save button as pending. */
function markScheduleDirty() {
  if (scheduleState.dirty) return;
  scheduleState.dirty = true;
  updateScheduleDirtyHint();
}

/** All controls of the schedule card that can carry an unsaved choice (both switches + dropdowns). */
function scheduleControls() {
  var out = [];
  ['schedule-weekend', 'schedule-enabled'].forEach(function (id) {
    var node = $(id);
    if (node) out.push(node);
  });
  var selects = document.querySelectorAll('.schedule-select');
  for (var i = 0; i < selects.length; i += 1) out.push(selects[i]);
  return out;
}

function scheduleControlValue(node) {
  return node.type === 'checkbox' ? String(!!node.checked) : String(node.value || '');
}

/** What a save is about to send: [{node, value}] per control, see clearScheduleDirty. */
function scheduleControlSnapshot() {
  return scheduleControls().map(function (node) {
    return { node: node, value: scheduleControlValue(node) };
  });
}

/**
 * Clear the dirty flags after a successful save (dropdowns and both switches).
 * With a snapshot taken when the request was sent, only controls still holding the sent
 * value are cleared: a choice made while the save was in flight stays dirty, otherwise
 * renderSchedule would overwrite it with the saved value.
 */
function clearScheduleDirty(sentSnapshot) {
  var anyDirty = false;
  scheduleControls().forEach(function (node) {
    var keep = false;
    if (sentSnapshot && node.dataset.dirty === '1') {
      var sent = null;
      for (var i = 0; i < sentSnapshot.length; i += 1) {
        if (sentSnapshot[i].node === node) { sent = sentSnapshot[i]; break; }
      }
      // A control created after the request (grid rebuilt) has no snapshot: keep it dirty.
      keep = !sent || sent.value !== scheduleControlValue(node);
    }
    if (keep) anyDirty = true;
    else delete node.dataset.dirty;
  });
  scheduleState.dirty = anyDirty;
  updateScheduleDirtyHint();
}

function updateScheduleDirtyHint() {
  var button = $('btn-schedule-save');
  if (!button) return;
  button.classList.toggle('is-dirty', !!scheduleState.dirty);
  button.textContent = scheduleState.dirty
    ? tsT('library.schedule.saveDirty', '保存 *')
    : tsT('library.schedule.save', '保存');
  button.title = scheduleState.dirty
    ? tsT('library.schedule.saveDirtyTitle', '有还没保存的选择（点这里保存）')
    : tsT('library.schedule.saveTitle', '保存工作日与周末的时段选择');
}

/** Rebuild one grid's (weekday / weekend) four slot dropdowns; same signature = leave the DOM alone (keeps focus). */
function renderScheduleGrid(containerId, kind) {
  var rows = $(containerId);
  if (!rows) return;
  var groups = scheduleThemeGroups();
  var zones = scheduleState.zones.length ? scheduleState.zones : ['morning', 'day', 'evening', 'night'];
  var map = scheduleMapOf(kind);
  var signature = scheduleOptionsSig(groups) + '|' + zones.join(',') + '|' + kind;
  if (rows.getAttribute('data-sig') === signature) {
    var selects = rows.querySelectorAll('.schedule-select');
    for (var i = 0; i < selects.length; i += 1) {
      // Unsaved user choices must be kept: library refresh / weekend toggle / external
      // sync all land here, and the old code wrote select.value back to the saved value,
      // silently undoing what the user just picked (with no notice at all).
      if (selects[i].dataset.dirty === '1') continue;
      var zone = selects[i].getAttribute('data-zone');
      var want = String(map[zone] || '');
      if (selects[i].value !== want) selects[i].value = want;
    }
    return;
  }
  // The theme list changed (added / renamed / deleted): the dropdowns are rebuilt,
  // but unsaved choices must survive the rebuild, same as in the branch above.
  var dirtyChoices = {};
  var oldSelects = rows.querySelectorAll('.schedule-select');
  for (var j = 0; j < oldSelects.length; j += 1) {
    if (oldSelects[j].dataset.dirty === '1') {
      dirtyChoices[oldSelects[j].getAttribute('data-zone')] = oldSelects[j].value;
    }
  }
  rows.innerHTML = '';
  zones.forEach(function (zone) {
    var select = el('select', {
      class: 'schedule-select',
      'data-zone': zone,
      'data-kind': kind,
      'aria-label': tsT('library.schedule.selectAria', '{{day}}{{zone}}用哪套主题',
        { day: scheduleDayLabel(kind), zone: scheduleZoneLabel(zone) })
    });
    fillScheduleSelect(select, groups, String(map[zone] || ''));
    if (Object.prototype.hasOwnProperty.call(dirtyChoices, zone)) {
      var wanted = dirtyChoices[zone];
      var available = Array.prototype.some.call(select.options, function (option) {
        return option.value === wanted;
      });
      // A choice whose theme was just deleted cannot be kept; fall back to the saved value.
      if (available) {
        select.value = wanted;
        select.dataset.dirty = '1';
      }
    }
    select.addEventListener('change', function () {
      this.dataset.dirty = '1';
      markScheduleDirty();
    });
    rows.appendChild(el('div', { class: 'schedule-row' }, [
      el('span', { class: 'schedule-when' }, [
        el('b', { text: scheduleZoneLabel(zone) }),
        el('i', { text: scheduleWindowText(zone) })
      ]),
      select
    ]));
  });
  rows.setAttribute('data-sig', signature);
}

/** Collapse the stale list into one readable line (for tests/debugging; rendering only needs its length). */
function scheduleStaleText() {
  var stale = scheduleState.stale || [];
  if (!stale.length) return '';
  var labels = stale.map(function (item) {
    return tsT('library.schedule.slotLabel', '{{day}} {{zone}}', {
      day: scheduleDayLabel(item && item.day_kind),
      zone: scheduleZoneLabel(item && item.zone)
    });
  });
  return labels.join(' / ');
}

/** Which auto-switch slots use this theme (for the pre-delete warning). */
function scheduleRefsFor(themeId) {
  var id = String(themeId || '');
  if (!id) return [];
  var schedule = scheduleState.schedule || {};
  var refs = [];
  [['weekday', schedule.zones], ['weekend', schedule.weekend_zones]].forEach(function (pair) {
    var kind = pair[0];
    var map = pair[1] || {};
    Object.keys(map).forEach(function (zone) {
      if (String(map[zone] || '') !== id) return;
      var label = tsT('library.schedule.slotLabel', '{{day}} {{zone}}', {
        day: scheduleDayLabel(kind),
        zone: scheduleZoneLabel(zone)
      });
      if (refs.indexOf(label) < 0) refs.push(label);
    });
  });
  return refs;
}

/** Re-render the whole card from scheduleState. */
function renderSchedule() {
  renderScheduleGrid('schedule-rows', 'weekday');
  renderScheduleGrid('schedule-rows-weekend', 'weekend');

  var weekendBox = $('schedule-weekend');
  if (weekendBox && weekendBox.dataset.dirty !== '1') {
    weekendBox.checked = !!scheduleState.weekendEnabled;
  }
  var weekendRows = $('schedule-rows-weekend');
  if (weekendRows) weekendRows.hidden = !scheduleState.weekendEnabled;

  var enabled = $('schedule-enabled');
  if (enabled && enabled.dataset.dirty !== '1') {
    enabled.checked = !!scheduleState.schedule.enabled;
  }

  var now = $('schedule-now');
  if (now) {
    var detail = scheduleState.now;
    if (!detail) {
      now.textContent = scheduleState.loaded
        ? tsT('library.schedule.nowUnknown', '还没读到当前时段。')
        : tsT('library.schedule.loading', '正在读取…');
    } else {
      var current = scheduleZoneLabel(detail.zone);
      var dayLabel = scheduleDayLabel(detail.day_kind);
      var picked = String(detail.theme_id || '');
      var theme = tsT('library.schedule.notPicked', '未选（保持原样）');
      if (picked) {
        var name = picked;
        var known = false;
        (libraryState.items || []).forEach(function (item) {
          if (item && item.id === picked) {
            name = String(item.name || picked);
            known = true;
          }
        });
        // The schedule still records this id but the library no longer has it (deleted /
        // not restored from backup): say so honestly, rather than implying it will switch
        // to a theme that does not exist.
        theme = known ? name : tsT('library.schedule.missing', '{{name}}（已不存在）', { name: name });
      }
      var head = tsT('library.schedule.nowHead', '现在是{{day}}的{{zone}}（{{hour}} 点）',
        { day: dayLabel, zone: current, hour: schedulePad2(detail.hour) });
      if (!scheduleState.schedule.enabled) {
        now.textContent = head + tsT('library.schedule.nowDisabled', '；自动换主题已关闭。');
      } else if (picked) {
        var fromWeekend = detail.day_kind === 'weekend' && scheduleState.weekendEnabled;
        now.textContent = head + (fromWeekend
          ? tsT('library.schedule.nowWillSwitchWeekend', '，到点会切到「{{theme}}」（周末表）。', { theme: theme })
          : tsT('library.schedule.nowWillSwitch', '，到点会切到「{{theme}}」。', { theme: theme }));
      } else if (scheduleState.stale.length) {
        // The backend dropped stale slots (the schedule file still holds the id); without
        // an explanation the user would only see "no theme picked for this slot" and not
        // know why their setting vanished.
        now.textContent = head + tsT('library.schedule.nowStale',
          '；有 {{count}} 个时段引用的主题已不在仓库里（重新导入那套主题或重新选一个就会恢复）。',
          { count: scheduleState.stale.length });
      } else {
        now.textContent = head + tsT('library.schedule.nowNotPicked', '；这个时段还没选主题，到点不会换。');
      }
      now.classList.toggle('is-idle', !scheduleState.schedule.enabled);
    }
  }

  var saveBtn = $('btn-schedule-save');
  if (saveBtn) saveBtn.disabled = !!state.busy || !scheduleState.loaded;
}

/** Load the schedule. If the theme list is empty, fetch the library too (the dropdowns need options). */
async function refreshSchedule() {
  try {
    if (!libraryState.items.length) await refreshLibrary();
    var result = await callPlugin('get_schedule', {});
    if (result && result.schedule) scheduleState.schedule = result.schedule;
    if (result && result.zones instanceof Array && result.zones.length) scheduleState.zones = result.zones;
    if (result && result.windows instanceof Array) scheduleState.windows = result.windows;
    scheduleState.now = (result && result.now) || null;
    scheduleState.stale = (result && result.stale instanceof Array) ? result.stale : [];
    scheduleState.weekendEnabled = scheduleWeekendHasValues(result && result.schedule);
    scheduleState.loaded = true;
    renderSchedule();
    return scheduleState.schedule;
  } catch (error) {
    scheduleState.loaded = false;
    // Not knowing what is stored, a save would overwrite it with the empty card: disable it.
    var failedSave = $('btn-schedule-save');
    if (failedSave) failedSave.disabled = true;
    var rows = $('schedule-rows');
    if (rows) {
      rows.innerHTML = '';
      rows.setAttribute('data-sig', '');
    }
    var now = $('schedule-now');
    if (now) {
      now.classList.remove('is-idle');
      now.textContent = tsT('library.schedule.loadFailed', '读取排程失败：{{error}}', { error: pluginErrorMessage(error) });
    }
    return null;
  }
}

/** Save the schedule. When enabled the backend switches immediately for the current slot, so read the palette back too. */
async function saveSchedule() {
  if (state.busy || !scheduleState.loaded) return;
  var zones = collectScheduleZones('schedule-rows');
  var weekendBox = $('schedule-weekend');
  var useWeekend = !!(weekendBox && weekendBox.checked);
  // Without "use a different set on weekends", send an empty table: the backend then reuses the weekday table.
  var weekend = useWeekend ? collectScheduleZones('schedule-rows-weekend') : {};
  var enabledBox = $('schedule-enabled');
  var wanted = !!(enabledBox && enabledBox.checked);
  var sentSnapshot = scheduleControlSnapshot();
  setBusy(true, tsT('library.schedule.saving', '正在保存自动换主题…'));
  try {
    var result = await callPlugin('save_schedule', {
      enabled: wanted,
      zones: zones,
      weekend_zones: weekend
    });
    if (result && result.schedule) scheduleState.schedule = result.schedule;
    if (result && result.now) scheduleState.now = result.now;
    scheduleState.weekendEnabled = scheduleWeekendHasValues(result && result.schedule);
    // Saved = the sent choices are what is on disk; clear dirty for those only, so choices
    // made while the request was in flight survive (see clearScheduleDirty).
    clearScheduleDirty(sentSnapshot);
    if (weekendBox && weekendBox.dataset.dirty === '1') scheduleState.weekendEnabled = !!weekendBox.checked;
    renderSchedule();
    var applied = result && result.applied;
    if (applied && applied.action === 'applied') {
      // The backend already switched themes while the editor still shows the old one:
      // read back, or the user's next "Apply" would overwrite the auto-switch result.
      await reloadPalette({ silent: true });
      showToast(tsT('library.schedule.savedSwitched', '已保存，并切换到「{{name}}」',
        { name: String((applied.theme && applied.theme.name) || applied.theme_id) }), 'ok');
      setStatus(tsT('library.schedule.savedSwitchedStatus', '自动换主题已生效'), 'ok');
    } else if (!wanted) {
      showToast(tsT('library.schedule.savedDisabled', '已保存（自动换主题已关闭）'), 'ok');
      setStatus(tsT('library.schedule.savedDisabledStatus', '自动换主题已关闭'), 'ok');
    } else {
      showToast(tsT('library.schedule.savedNoTheme', '已保存；这个时段还没选主题'), 'ok');
      setStatus(tsT('library.schedule.savedStatus', '自动换主题已保存'), 'ok');
    }
  } catch (error) {
    showNotice('error', tsT('library.schedule.saveFailedNotice', '保存自动换主题失败：{{error}}', { error: pluginErrorMessage(error) }));
    showToast(tsT('library.save.failed', '保存失败'), 'error');
    setStatus(tsT('library.schedule.saveFailedStatus', '保存自动换主题失败'), 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 18. External change detection (v0.4.14)
 *
 * The panel is not the only writer: the scheduled theme-switch thread, other
 * windows with the panel open, and CLI/scripts all modify theme.json. The panel
 * only calls get_theme once on open and then keeps showing those stale values,
 * so the theme name, colors and readability audit the user sees are outdated,
 * and clicking "Apply" submits a diff edited on top of old values.
 *
 * Fix: poll theme.json lightly on a timer (plain GET, no entry dispatch, no
 * validation/write-lock cost) and compare signatures. Follow automatically only
 * when the panel has no uncommitted edits; otherwise just notify, never overwrite.
 * ==========================================================================*/

var EXTERNAL_SYNC_INTERVAL_MS = 5000;
var EXTERNAL_SYNC_START_DELAY_MS = 2500;
var EXTERNAL_SYNC_TIMEOUT_MS = 4000;

var externalSyncState = {
  timer: null,
  inFlight: false,
  lastSignature: '',   // signature of the last state the panel knows it is in sync with
  pending: null,       // signature of an external change already notified but not yet followed
  failures: 0,
  warned: false,       // the consecutive-failure notice shows only once (reset on recovery)
  materialsDeferred: false, // last read-back skipped materials because of material drafts
  lastCheckAt: 0
};

/** FNV-1a 32-bit: synchronous, no randomness, only used to tell whether content changed. */
function hashText(text) {
  var hash = 0x811c9dc5;
  for (var i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i) & 0xff;
    hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
  }
  return ('00000000' + hash.toString(16)).slice(-8);
}

/** Stable palette signature, computed from the **content**.
 *
 *  The old implementation used updated_at + key counts, which missed a
 *  hand-edited value of an existing variable when updated_at stayed the same,
 *  which is exactly why this feature exists (the panel is not the only writer:
 *  scheduled switching, CLI and other windows all modify theme.json).
 *  FNV over 96x2 key/values takes microseconds; once every 5 s costs nothing. */
function paletteSignature(p) {
  if (!p || typeof p !== 'object') return '';
  if (p.revision) return String(p.revision);
  function pairBlock(map) {
    if (!map || typeof map !== 'object') return '';
    return Object.keys(map).sort().map(function (key) {
      return key + '\u0000' + String(map[key]);
    }).join('\u0001');
  }
  return hashText([
    String(p.name || ''),
    p.enabled === false ? '0' : '1',
    pairBlock(p.vars),
    pairBlock(p.dark_vars),
    // Changes made only to materials in another window must be detected too.
    materialsSignatureText(p.materials)
  ].join('\u0002'));
}

function materialsSignatureText(materials) {
  if (!materials || typeof materials !== 'object') return '';
  try { return JSON.stringify(materials); } catch (e) { return ''; }
}

/** Unapplied edits in the material center count as 1 pending item: external sync must not overwrite them. */
function materialsDirtyCount() {
  var api = window.NekoMaterials;
  if (api && typeof api.diffCount === 'function') return api.diffCount();
  if (!api || typeof api.isDirty !== 'function') return 0;
  try { return api.isDirty() ? 1 : 0; } catch (e) { return 0; }
}

/** Whether to follow this external change automatically. Pure function, asserted directly in self-tests. */
function externalSyncDecision(signature, state) {
  var opts = state || {};
  if (!signature) return { apply: false, reason: 'empty' };
  if (signature === opts.lastSignature) return { apply: false, reason: 'same' };
  if (opts.busy) return { apply: false, reason: 'busy' };
  if (opts.dirty > 0) return { apply: false, reason: 'dirty' };
  if (opts.modalOpen) return { apply: false, reason: 'modal' };
  return { apply: true, reason: 'ok' };
}

/** Whether the panel is currently clean enough to follow automatically. */
function externalSyncBlockers() {
  var modal = $('modal');
  return {
    busy: !!state.busy,
    dirty: pendingChangeCount(),
    modalOpen: !!(modal && !modal.hidden) || !!activeEditorDialog()
  };
}

/** Notify that the disk changed but the panel holds edits, so it was not overwritten. Once per signature. */
function warnExternalChangeBlocked() {
  var blockers = externalSyncBlockers();
  var why = [];
  if (blockers.dirty) why.push(tsT('sync.blockerDirty', '面板里有 {{count}} 项还没应用的改动', { count: blockers.dirty }));
  if (blockers.modalOpen) why.push(tsT('sync.blockerModal', '正在编辑弹窗内容'));
  if (blockers.busy) why.push(tsT('sync.blockerBusy', '正在处理上一次操作'));
  showNotice('warn', tsT('sync.blockedNotice',
    '磁盘上的主题被外部改动了（可能是按时段自动换主题，或别的窗口）。\n' +
    '面板里{{reasons}}，为了不覆盖你的改动，没有自动刷新。\n' +
    '想现在跟上：先应用或「还原本组」清掉面板里的改动；想放弃面板改动直接跟上，就点「重新读取」。',
    { reasons: why.length ? why.join(tsT('sync.reasonSeparator', '、')) : tsT('sync.blockerUnknown', '有未完成的操作') }));
  showToast(tsT('sync.blockedToast', '磁盘主题已变化，未自动覆盖'));
}

/** Report repeated external-sync failures: a feature that breaks silently is no feature. */
function noteExternalSyncFailure() {
  externalSyncState.failures += 1;
  if (externalSyncState.failures < 3 || externalSyncState.warned) return;
  externalSyncState.warned = true;
  showNotice('warn', tsT('sync.failureNotice',
    '面板已经连续 {{count}} 次读不到 theme.json，「外部改动自动跟随」暂时不可用。\n'
    + '你仍然可以手动编辑；如果一直这样，点「运行状态」看看主题服务是否还正常。',
    { count: externalSyncState.failures }));
  setStatus(tsT('sync.failureStatus', '外部改动感知不可用'), 'warn');
}

/** Poll once: follow the disk and bring the panel in line. Failures accumulate; notify once after 3 in a row. */
async function checkExternalChange() {
  if (externalSyncState.inFlight) return false;
  externalSyncState.inFlight = true;
  externalSyncState.lastCheckAt = Date.now();
  try {
    var response = await fetchWithTimeout(API_BASE + '/theme.json?_=' + Date.now(),
      { cache: 'no-store', credentials: 'same-origin' }, EXTERNAL_SYNC_TIMEOUT_MS);
    if (!response.ok) { noteExternalSyncFailure(); return false; }
    var raw = await response.json();
    externalSyncState.failures = 0;
    externalSyncState.warned = false;
    if (!state.busy && previewActive() && (!raw.preview || !raw.preview.active || raw.preview.id !== state.previewing)) clearPreviewState();
    var signature = paletteSignature(raw);
    var blockers = externalSyncBlockers();
    var decision = externalSyncDecision(signature, {
      lastSignature: externalSyncState.lastSignature,
      busy: blockers.busy,
      dirty: blockers.dirty,
      modalOpen: blockers.modalOpen
    });
    if (!decision.apply) {
      if (decision.reason === 'dirty' || decision.reason === 'modal') {
        if (externalSyncState.pending !== signature) {
          externalSyncState.pending = signature;
          warnExternalChangeBlocked();
        }
      }
      return false;
    }
    externalSyncState.pending = null;
    var palette = await reloadPalette({ silent: true, keepMaterialDrafts: true });
    if (!palette) return false;
    if (externalSyncState.materialsDeferred) {
      // Material drafts appeared during read-back: keep them, do not advance the baseline, notify once as "unapplied edits".
      if (externalSyncState.pending !== signature) {
        externalSyncState.pending = signature;
        warnExternalChangeBlocked();
      }
      return false;
    }
    // Re-establish the signature from the backend's actual value: the file may have
    // changed again between GET theme.json and get_theme, and trusting the GET
    // signature would make the next poll report "changed again" right away.
    externalSyncState.lastSignature = paletteSignature(palette);
    showToast(tsT('sync.syncedToast', '已同步外部改动：{{name}}', { name: palette.name || tsT('misc.untitled', '未命名') }));
    setStatus(tsT('sync.syncedStatus', '已同步外部改动'), 'ok');
    // The status card reads studio_status (backend theme.json + window reports). If an
    // external change refreshes only the editor and not this card, the card keeps the
    // previous theme name: the UI already looks default while the panel says the old
    // theme is active, which is exactly the reported symptom.
    refreshHealth({});
    libRecentMaybeRecord(palette.name);
    if (lastApplyUndo && palette.revision !== lastApplyUndo.revision) lastApplyUndo = null;
    syncUndoApplyButton();
    return true;
  } catch (error) {
    noteExternalSyncFailure();
    return false;
  } finally {
    externalSyncState.inFlight = false;
  }
}

function startExternalSync() {
  stopExternalSync();
  externalSyncState.timer = setInterval(function () {
    // Do not poll while the panel is in the background: saves power and avoids a burst on return.
    if (document.visibilityState === 'hidden') return;
    checkExternalChange();
  }, EXTERNAL_SYNC_INTERVAL_MS);
}

function stopExternalSync() {
  if (externalSyncState.timer) {
    clearInterval(externalSyncState.timer);
    externalSyncState.timer = null;
  }
}

function revertMode() {
  var mode = state.mode;
  var n = Object.keys(state.edited[mode]).length + Object.keys(state.cleared[mode]).length;
  if (!n) {
    showToast(mode === 'dark'
      ? tsT('misc.revertNoneDark', '当前暗色组没有改动')
      : tsT('misc.revertNoneLight', '当前亮色组没有改动'));
    return;
  }
  state.edited[mode] = {};
  state.cleared[mode] = {};
  syncAll();
  showToast(mode === 'dark'
    ? tsT('misc.revertedDark', '已还原暗色组改动')
    : tsT('misc.revertedLight', '已还原亮色组改动'), 'ok');
}

async function reloadPalette(options) {
  var silent = options && options.silent;
  // discardEdits: clicking "Reload" explicitly discards the panel's edits. Without
  // clearing the dirty state, dirtyCount stays > 0, external sync is blocked forever
  // with no further notice and the panel looks stuck; worse, the next "Apply" would
  // submit values the user thinks were discarded.
  var discardEdits = !!(options && options.discardEdits);
  var keepMaterialDrafts = !!(options && options.keepMaterialDrafts);
  setLoading(true);
  try {
    var palette = await callPlugin('get_theme', {});
    // External-sync path: the user may have edited materials while get_theme was pending
    // (the progress bar does not block editing). Do not overwrite the material drafts with
    // disk values or advance the signature baseline; retry on the next poll.
    var materialsDeferred = keepMaterialDrafts && materialsDirtyCount() > 0;
    externalSyncState.materialsDeferred = materialsDeferred;
    state.palette = palette;
    if (discardEdits) {
      state.edited = { light: {}, dark: {} };
      state.cleared = { light: {}, dark: {} };
      markAllRowsValid();
      clearRgbMemory();
      // Since "Reload" discards unapplied edits, drop the undo stack too: otherwise Ctrl+Z
      // would bring back the edits just discarded.
      resetUndoHistory();
    }
    // Update the baseline signature too: otherwise an external-sync poll right after this read would misjudge the same data as "changed".
    if (!materialsDeferred) {
      externalSyncState.lastSignature = paletteSignature(palette);
      externalSyncState.pending = null;
    } else {
      // Invalidate the baseline (instead of keeping the old signature): once the drafts are
      // reverted or applied, the next poll treats the disk as "needs sync" whether it holds
      // new values or old ones again, and pulls the panel back to disk.
      externalSyncState.lastSignature = '';
    }
    renderMeta({ discard: discardEdits });
    renderPaletteHint();
    // Refresh the materials subtree from disk: after import/reset/apply the material
    // center's "saved" baseline must update too, or it keeps showing "N pending".
    // The save path passes the submitted material snapshot: materials edited while
    // waiting only update the baseline and are not overwritten.
    var materialsSyncOptions = (options && Object.prototype.hasOwnProperty.call(options, 'materialsSubmitted'))
      ? { submitted: options.materialsSubmitted }
      : undefined;
    if (!materialsDeferred) syncMaterials(palette, materialsSyncOptions);
    syncAll();
    applyFilters();
    renderAudit(true);
    // Every disk read also refreshes the status card: it reports whether the theme is
    // applied in the windows, and must come from the same read as the editor, or the
    // editor shows defaults while the card still names the previous theme.
    refreshHealth({});
    var c = paletteCounts(palette);
    if (!silent) {
      showNotice('ok', tsT('sync.loadedNotice', '已读取当前主题：亮色 {{light}} 项 / 暗色 {{dark}} 项',
        { light: c.light, dark: c.dark }) + savedModeHint(c.light, c.dark));
      // The status bar always shows both counts so "only one group configured" is not missed.
      setStatus(tsT('sync.loadedStatus', '已读取 · 亮色 {{light}} / 暗色 {{dark}}', { light: c.light, dark: c.dark }), 'ok');
    }
    restoreDraftIfAny();
    return palette;
  } catch (error) {
    if (!silent) {
      showNotice('error', tsT('sync.loadFailedNotice',
        '读取失败：{{error}}\n（后端主题服务可能没响应；面板仍可编辑，点击「应用」会重试。）',
        { error: pluginErrorMessage(error) }));
      setStatus(tsT('sync.loadFailedStatus', '读取失败'), 'error');
    }
    // A failed read must not leave the panel blank: keep rendering from the current edit state.
    renderPaletteHint();
    syncAll();
    return null;
  } finally {
    setLoading(false);
  }
}

/** Lock row controls while busy: otherwise in-flight edits are silently lost when state.edited is cleared on success. */
function setRowControlsDisabled(busy) {
  // Theme name / enable switch are submitted in the same batch: if not disabled while busy, text typed during read-back gets overwritten.
  ['theme-name', 'theme-enabled'].forEach(function (id) {
    var node = $(id);
    if (node) node.disabled = !!busy;
  });
  Object.keys(rowsByKey).forEach(function (key) {
    var ref = rowsByKey[key];
    // Image rows' "Choose image / Clear", switches and selects must be disabled too: if they
    // stay clickable while busy they race the backend write in progress (e.g. two background imports).
    ['hexInput', 'colorInput', 'alpha', 'textInput', 'clearBtn',
      'gradAngle', 'gradFrom', 'gradTo', 'gradGen',
      'pickBtn', 'clearImageBtn', 'switchInput', 'selectInput'].forEach(function (name) {
      if (ref[name]) ref[name].disabled = !!busy;
    });
    // On unlock, do not enable alpha unconditionally: transparent / inherit / empty rows must
    // stay disabled, or after one apply the slider could drag out semi-transparent black (the old
    // code set disabled only in syncRow and this overwrote it). With an RGB memory (color dragged
    // or picked), transparent stays enabled: dragging reuses the remembered color, see rgbMemory().
    if (!busy && ref.alpha) {
      var parsed = parseColor(displayValue(key));
      var transparent = !!parsed && parsed.a === 0;
      ref.alpha.disabled = !parsed || (transparent && !rgbMemory()[key]);
    }
  });
}

function setBusy(busy, message) {
  state.busy = busy;
  updateButtons();
  setRowControlsDisabled(busy);
  if (busy && message) setStatus(message, 'busy');
}

/* -------------------------------------------------------------------------- *
 * 10e. Status card
 *
 * In the plugin era this card was the "install self-check": for a theme to take
 * effect the plugin had to write the two loader files and template markers into
 * the N.E.K.O install directory, hence "repair / uninstall / reconnect". Built in,
 * that layer no longer exists by design: templates include the static assets
 * themselves, so nothing can be "installed wrong". The card now answers another
 * question: is the theme actually applied to the UI?
 *
 * Data comes from the backend studio_status: data directory, current theme,
 * library/background/built-in counts, plus runtime diagnostics the chat-window
 * loader sends back via loader_report (stored in data_dir/diagnostics.json,
 * last 20 kept).
 * -------------------------------------------------------------------------- */

// Built on demand (not at load time) so the text follows the current UI language.
function helpTopics() {
  return [
    {
      title: tsT('health.help.noChange.title', '主题装上了但界面完全没变'),
      body: tsT('health.help.noChange.body', '按顺序试：①确认在面板里选过颜色并点了「应用」；②彻底关掉 N.E.K.O 再重开'
        + '（已打开的窗口不会自己重载）；③看「应用侧上报」这一行 —— 一直没有上报，'
        + '说明聊天窗口还没被打开过。')
    },
    {
      title: tsT('health.help.whyApply.title', '为什么每次改完都要点「应用」'),
      body: tsT('health.help.whyApply.body', '面板上的值只是草稿，点「应用」才会写进数据目录里的 theme.json；'
        + '聊天窗口每 1.5 秒读一次它，所以应用之后一般秒级生效。')
    },
    {
      title: tsT('health.help.lightDark.title', '亮色 / 暗色是两套独立配色'),
      body: tsT('health.help.lightDark.body', '面板顶部的「亮色 / 暗色」互相独立。只改了暗色组时，本体处在亮色模式下当然看不到变化。')
    },
    {
      title: tsT('health.help.storage.title', '主题数据存在哪'),
      body: tsT('health.help.storage.body', '点这张卡片上的「复制数据目录」：theme.json（当前主题）、themes/（主题仓库）、'
        + 'backgrounds/（背景图）、schedule.json（按时段自动换主题）、diagnostics.json（加载器上报）。')
    },
    {
      title: tsT('health.help.migrate.title', '换机器 / 重装怎么办'),
      body: tsT('health.help.migrate.body', '面板底部可以「导出」分享包（zip，含背景图）。新机器上导入即可；'
        + '整个主题仓库也能一次导出，不用一棵树一棵树地存。')
    },
    {
      title: tsT('health.help.background.title', '背景图为什么没显示'),
      body: tsT('health.help.background.body', '背景图由本体后端从数据目录的 backgrounds/ 提供。上传后如果面板预览正常、'
        + '聊天窗口却没变，先确认「应用侧上报」里没有报错、当前主题是启用状态。')
    }
  ];
}

function openHelpModal() {
  var topics = helpTopics();
  var lines = [];
  for (var i = 0; i < topics.length; i += 1) {
    lines.push((i + 1) + '. ' + topics[i].title);
    lines.push('   ' + topics[i].body);
    lines.push('');
  }
  openModal('help', tsT('health.help.modalTitle', '主题没生效？'),
    tsT('health.help.modalHint', '这是主题工坊最常见的问题清单。改完关掉 N.E.K.O 再重开，通常就能看到变化。'),
    lines.join('\n'));
}

function healthItem(key, value, level) {
  return { key: key, value: value, level: level || 'ok' };
}

function renderHealth(info) {
  var stateNode = $('health-state');
  var listNode = $('health-list');
  if (!stateNode || !listNode) return;
  while (listNode.firstChild) listNode.removeChild(listNode.firstChild);

  if (!info || typeof info !== 'object') {
    stateNode.textContent = tsT('health.stateNoService', '读取失败：拿不到主题服务状态');
    stateNode.className = 'health-line is-bad';
    return;
  }

  var items = [];
  var level = 'ok';

  items.push(healthItem(tsT('health.key.dataDir', '数据目录'), String(info.data_dir || tsT('health.unknown', '未知')), 'ok'));

  // "Disabled" is not "no theme selected": windows show the factory default while
  // theme.json still holds the previous name. Say the two apart, or "UI is default,
  // panel names the old theme" reads like the theme was lost.
  var themeName = String(info.theme_name || DEFAULT_THEME_NAME);
  var currentText = info.theme_enabled
    ? tsT('health.themeEnabled', '{{name}}（启用中）', { name: themeName })
      + (info.theme_updated_at ? ' · ' + String(info.theme_updated_at) : '')
    : tsT('health.themeDisabled', '出厂默认配色（主题「{{name}}」已停用，打开启用开关再点「应用」）', { name: themeName });
  items.push(healthItem(tsT('health.key.currentTheme', '当前主题'), currentText, info.theme_enabled ? 'ok' : 'warn'));
  if (!info.theme_enabled && level === 'ok') level = 'warn';



  var recovery = info.theme_recovery;
  if (recovery && typeof recovery === 'object') {
    level = 'bad';
    items.push(healthItem(
      tsT('health.key.currentTheme', 'Current theme'),
      tsT('library.tools.recovery', 'The theme file could not be read. The original was saved as {{file}}. Apply a theme to replace the factory colors.',
        { file: recovery.file || '—' }),
      'bad'
    ));
  }

  var light = Number(info.light_vars) || 0;
  var dark = Number(info.dark_vars) || 0;
  if (light + dark === 0) {
    if (level === 'ok') level = 'warn';
    items.push(healthItem(tsT('health.key.vars', '配色变量'), tsT('health.varsEmpty', '还是出厂空主题 —— 选好颜色点「应用」才会生效'), 'warn'));
  } else {
    items.push(healthItem(tsT('health.key.vars', '配色变量'),
      tsT('health.varsCount', '亮色 {{light}} 项 · 暗色 {{dark}} 项', { light: light, dark: dark }), 'ok'));
  }

  items.push(healthItem(tsT('health.key.library', '主题仓库'), tsT('health.libraryCount', '{{custom}} 套自定义 · {{builtin}} 套内置',
    { custom: Number(info.library_count) || 0, builtin: Number(info.preset_count) || 0 }), 'ok'));
  items.push(healthItem(tsT('health.key.backgrounds', '背景图'),
    tsT('health.backgroundCount', '{{count}} 张', { count: Number(info.background_count) || 0 }), 'ok'));

  var trash = info.trash && typeof info.trash === 'object' ? info.trash : null;
  var trashCount = trash ? Number(trash.count) || 0 : 0;
  if (trashCount > 0) {
    var trashBytes = Number(trash.bytes) || 0;
    var trashLimit = Number(trash.limit) || 30;
    var trashItem = healthItem(
      tsT('library.tools.trash', 'Trash'),
      tsT('library.tools.trashCount', '{{count}} of {{limit}} entries · {{size}}', {
        count: trashCount, limit: trashLimit, size: trashBytes >= 1024 ? Math.round(trashBytes / 1024) + ' KB' : trashBytes + ' B'
      }),
      'ok'
    );
    trashItem.actions = [
      { label: tsT('library.tools.viewTrash', 'View trash'), onClick: openTrashModal },
      { label: tsT('library.tools.emptyTrash', 'Empty trash'), onClick: clearTrashFromHealth }
    ];
    items.push(trashItem);
  }

  var loaderVersion = String(info.loader_version || '');
  if (loaderVersion) {
    items.push(healthItem(tsT('health.key.loader', '加载器资源'), 'v' + loaderVersion
      + (info.css_sha8 ? ' · css ' + String(info.css_sha8) : ''), 'ok'));
  } else {
    if (level === 'ok') level = 'warn';
    items.push(healthItem(tsT('health.key.loader', '加载器资源'), tsT('health.loaderMissing', '读不到 static/theme-studio/loader/ 下的两件套'), 'warn'));
  }

  var diag = info.diagnostics;
  if (diag && typeof diag === 'object') {


    var surfaces = Array.isArray(diag.mt_surfaces) ? diag.mt_surfaces : [];
    if (surfaces.length) {
      var surfaceLabels = {
        chatWindow: tsT('catalog.groups.chatWindow', 'Chat window'), chatShell: tsT('editor.compare.chatBg', 'Chat background'),
        composer: tsT('catalog.groups.composer', 'Input area'), capsule: changeKeyLabel('--nts-chip-text'),
        topbar: changeKeyLabel('--nts-topbar-bg'), assistantBubble: tsT('editor.compare.aiBubble', 'AI bubble'),
        userBubble: tsT('editor.compare.userBubble', 'User bubble')
      };
      var surfaceHit = [];
      var surfaceMiss = [];
      for (var si = 0; si < surfaces.length; si += 1) {
        var sf = surfaces[si] || {};
        if (sf.r || sf.b || sf.f) surfaceHit.push(sf.k);
        else surfaceMiss.push(surfaceLabels[sf.k] || String(sf.k || ''));
      }
      items.push(healthItem(
        tsT('library.tools.surfaces', 'Material surfaces'),
        tsT('library.tools.surfaceStats', 'Active: {{count}}/{{total}} · Missing: {{missing}}',
          { count: surfaceHit.length, total: surfaces.length, missing: surfaceMiss.join(', ') || '—' }),
        surfaceMiss.length ? 'warn' : 'ok'
      ));
    }
    var page = String(diag.page || tsT('health.unknownPage', '未知页面'));
    var when = String(diag.received_at || '');
    items.push(healthItem(tsT('health.key.report', '应用侧上报'), page + (when ? ' · ' + when : '')
      + tsT('health.reportStats', ' · nts 变量 {{vars}} · 样式规则 {{rules}}',
        { vars: Number(diag.nts_vars) || 0, rules: Number(diag.rules) || 0 }), 'ok'));


    var mtPreset = String(diag.mt_preset || '');
    var mtQuality = String(diag.mt_quality || '');
    var mtVars = Number(diag.mt_vars) || 0;
    var mtRules = Number(diag.mt_rules) || 0;
    var wantPreset = String(info.material_preset || '');
    if (mtVars > 0) {
      items.push(healthItem(tsT('library.tools.materialLayer', 'Material layer'), (mtPreset || wantPreset || '—')
        + (mtQuality ? ' · ' + mtQuality : '') + tsT('health.reportStats', ' · nts variables {{vars}} · rules {{rules}}',
          { vars: mtVars, rules: mtRules }), 'ok'));
    } else if (wantPreset && wantPreset !== 'clear') {
      if (level === 'ok') level = 'warn';
      items.push(healthItem(tsT('library.tools.materialLayer', 'Material layer'),
        tsT('library.tools.materialMissing', 'Preset {{preset}} is selected, but no material variables were reported.',
          { preset: wantPreset }), 'warn'));
    }
    if (diag.enabled === false) {
      level = 'bad';
      items.push(healthItem(tsT('health.key.loaderState', '加载器状态'), tsT('health.loaderDisabled', '上报说主题是停用的 —— 打开面板的启用开关再点「应用」'), 'bad'));
    }
    var diagErrors = diag.errors || [];
    for (var e = 0; e < diagErrors.length && e < 5; e += 1) {
      level = 'bad';
      items.push(healthItem(tsT('health.key.loaderError', '加载器报错'), String(diagErrors[e]), 'bad'));
    }
    // The report must match theme.json: theme.json is what the backend thinks should be
    // used, the report is what the window actually uses. When they differ, the panel can
    // only say "the window has not applied this change yet"; it must not use the
    // theme.json name to declare the theme active on the window's behalf.
    var themeVars = light + dark;
    var reportedVars = Number(diag.nts_vars) || 0;
    var reportedRules = Number(diag.rules) || 0;
    if (info.theme_enabled && themeVars > 0 && reportedVars === 0) {
      level = 'bad';
      items.push(healthItem(tsT('health.key.windowMismatch', '窗口对不上'),
        tsT('health.windowMismatch', '上报的窗口 {{page}} 一个变量都没应用 —— 那个窗口还是默认外观，刷新它试试', { page: page }), 'bad'));
    }
    if (reportedVars > 0 && reportedRules === 0) {
      level = 'bad';
      items.push(healthItem(tsT('health.key.styleMissing', '样式未生效'),
        tsT('health.styleMissing', '上报说注入了 {{count}} 个变量，但样式规则数为 0 —— 注入的 <style> 已被清理', { count: reportedVars }), 'bad'));
    }
    if (themeVars === 0 && reportedVars > 0) {
      // Reverse mismatch: theme.json is back to an empty theme but the window still holds
      // the previous styles. The window catches up within 1.5 s; this only tells someone
      // checking right after a reset that it is a transitional state.
      if (level === 'ok') level = 'warn';
      items.push(healthItem(tsT('health.key.windowLagging', '窗口滞后一拍'),
        tsT('health.windowLagging', 'theme.json 已经是空主题，窗口上报的还是上一次的 {{count}} 个变量 —— 1.5 秒内会自己跟上', { count: reportedVars }), 'warn'));
    }
  } else {
    if (level === 'ok') level = 'warn';
    items.push(healthItem(tsT('health.key.report', '应用侧上报'), tsT('health.noReport', '还没有收到上报 —— 打开一次聊天窗口后就会有'), 'warn'));
  }

  for (var i = 0; i < items.length; i += 1) {
    var li = el('li', { class: items[i].level === 'ok' ? '' : 'is-' + items[i].level });
    li.appendChild(el('span', { class: 'hk', text: tsT('health.keyLabel', '{{key}}：', { key: items[i].key }) }));
    li.appendChild(el('span', { class: 'hv', text: items[i].value }));

    var acts = Array.isArray(items[i].actions)
      ? items[i].actions
      : (items[i].action ? [items[i].action] : []);
    for (var a = 0; a < acts.length; a += 1) {
      var act = acts[a];
      if (!act || typeof act.onClick !== 'function') continue;
      var actBtn = el('button', { class: 'hk-action', type: 'button', text: String(act.label || '') });
      actBtn.addEventListener('click', act.onClick);
      li.appendChild(actBtn);
    }
    listNode.appendChild(li);
  }

  if (level === 'ok') {
    stateNode.textContent = tsT('health.stateOk', '一切正常：主题已经生效');
  } else if (level === 'warn') {
    stateNode.textContent = tsT('health.stateWarn', '主题服务正常，但有几处需要注意');
  } else {
    stateNode.textContent = tsT('health.stateBad', '有问题：主题可能没有生效');
  }
  stateNode.className = 'health-line is-' + level;
}


var trashModalEl = null;

function trashItemsFromStatus() {
  var status = state.status || {};
  var trash = status.trash && typeof status.trash === 'object' ? status.trash : {};
  var items = Array.isArray(trash.items) ? trash.items : [];
  return items.filter(function (item) { return item && item.theme_id; });
}

function closeTrashModal() {
  if (!trashModalEl) return;
  trashModalEl.remove();
  trashModalEl = null;
  var button = document.querySelector('#health-list .hk-action');
  if (button) button.focus();
}

function formatTrashTime(value) {
  var text = String(value || '');
  if (!text) return '—';
  return text.replace('T', ' ').replace(/\.[0-9]+/, '').replace(/([+-][0-9]{2}:[0-9]{2}|Z)$/, '');
}

function renderTrashList(listNode, emptyNode) {
  while (listNode.firstChild) listNode.removeChild(listNode.firstChild);
  var items = trashItemsFromStatus();
  emptyNode.hidden = items.length > 0;
  for (var i = 0; i < items.length; i += 1) {
    var item = items[i];
    var row = el('div', { class: 'trash-row', 'data-theme-id': item.theme_id });
    var main = el('div', { class: 'trash-main' }, [
      el('div', { class: 'trash-name', text: String(item.name || item.theme_id) }),
      el('div', {
        class: 'trash-meta',
        text: formatTrashTime(item.deleted_at)
          + (Number(item.schedule_zones) > 0 ? ' · ' + tsT('library.tools.scheduleUses', '{{count}} schedule periods',
            { count: Number(item.schedule_zones) }) : '')
      })
    ]);
    row.appendChild(main);
    var restore = el('button', { class: 'hk-action trash-restore', type: 'button', text: tsT('library.tools.restore', 'Restore') });
    restore.disabled = item.restorable === false;
    restore.addEventListener('click', function (themeId) {
      return function () { restoreTrashTheme(themeId, this); };
    }(item.theme_id));
    row.appendChild(restore);
    listNode.appendChild(row);
  }
}

async function restoreTrashTheme(themeId, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, tsT('library.tools.restoring', 'Restoring…'));
  try {
    var res = await callPlugin('restore_theme', { theme_id: themeId });
    var name = (res && res.name) || themeId;
    var zones = Array.isArray(res.schedule_restored) ? res.schedule_restored.length : Number(res.restored_schedule_zones) || 0;
    var message = tsT('library.tools.restored', 'Restored “{{name}}” ({{count}} schedule periods).', { name: name, count: zones });
    hideNotice();
    showToast(message, 'ok');
    setStatus(message, 'ok');
    await refreshLibrary({ fresh: true });
    if (scheduleState.loaded) await refreshSchedule();
    var status = await refreshHealth({ fresh: true });
    if (status && trashModalEl) {
      renderTrashList(trashModalEl.querySelector('.trash-list'), trashModalEl.querySelector('.trash-empty'));
      if (!trashItemsFromStatus().length) closeTrashModal();
    } else if (!trashModalEl) {
      libRefocusCard(themeId);
    }
  } catch (error) {
    showToast(tsT('library.tools.restoreFailed', 'Could not restore the theme: {{error}}', { error: pluginErrorMessage(error) }), 'error');
    if (button) button.disabled = false;
  } finally {
    setBusy(false);
  }
}

function openTrashModal() {
  if (trashModalEl) return;
  var listNode = el('div', { class: 'trash-list' });
  var emptyNode = el('div', { class: 'trash-empty', text: '—' });
  var closeBtn = el('button', { class: 'hk-action', type: 'button', text: tsT('shortcuts.close', 'Close the dialog') });
  var title = tsT('library.tools.trash', 'Trash');
  var panel = el('div', { class: 'trash-panel', role: 'dialog', 'aria-modal': 'true', 'aria-label': title }, [
    el('div', { class: 'trash-head' }, [
      el('h3', { text: title }),
      closeBtn
    ]),
    el('p', { class: 'trash-hint', text: tsT('library.tools.trashHint', 'Deleted themes are kept here until restored or removed.') }),
    listNode,
    emptyNode
  ]);
  trashModalEl = el('div', { class: 'trash-modal', id: 'trash-modal' }, [panel]);
  document.body.appendChild(trashModalEl);
  trashModalEl.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { event.preventDefault(); closeTrashModal(); }
    if (event.key === 'Tab') trapModalFocus(event, trashModalEl);
  });
  closeBtn.addEventListener('click', closeTrashModal);
  trashModalEl.addEventListener('mousedown', function (event) {
    if (event.target === trashModalEl) closeTrashModal();
  });
  renderTrashList(listNode, emptyNode);
  closeBtn.focus();
}

function clearTrashFromHealth() {
  var status = state.status || {};
  var trash = status.trash && typeof status.trash === 'object' ? status.trash : {};
  var count = Number(trash.count) || 0;
  if (state.busy) return;
  if (!window.confirm(tsT('library.tools.confirmEmpty', 'Permanently delete {{count}} trash entries? They cannot be restored.', { count: count }))) {
    return;
  }
  setBusy(true, tsT('library.tools.emptyTrash', 'Empty trash'));
  callPlugin('clear_trash', {})
    .then(function (result) {
      var removed = Number((result || {}).removed) || 0;
      showToast(tsT('library.tools.cleared', 'Removed {{count}} files from the trash.', { count: removed }), 'ok');
      return refreshHealth({ fresh: true });
    })
    .catch(function (error) {
      showToast(tsT('library.tools.clearFailed', 'Could not empty the trash: {{error}}', { error: pluginErrorMessage(error) }), 'error');
    })
    .then(function () { setBusy(false); });
}

var healthInflight = null;
var healthReadId = 0;
function refreshHealth(options) {
  var opts = options || {};
  if (healthInflight && !opts.toast && !opts.fresh && !state.busy) return healthInflight;
  var p = refreshHealthNow(opts, ++healthReadId);
  healthInflight = p;
  var clear = function () { if (healthInflight === p) healthInflight = null; };
  p.then(clear, clear);
  return p;
}

function refreshHealthNow(options, readId) {
  var opts = options || {};
  return callPlugin('studio_status', {})
    .then(function (result) {
      if (readId !== healthReadId) return state.status;
      state.status = result || {};
      renderHealth(state.status);
      if (opts.toast) showToast(tsT('health.refreshedToast', '运行状态已刷新'), 'ok');
      return state.status;
    })
    .catch(function (error) {
      if (readId !== healthReadId) return state.status;
      state.status = null;
      var stateNode = $('health-state');
      if (stateNode) {
        stateNode.textContent = tsT('health.stateLoadFailed', '读取失败：{{error}}', { error: pluginErrorMessage(error) });
        stateNode.className = 'health-line is-bad';
      }
      if (opts.toast) showToast(tsT('sync.loadFailedStatus', '读取失败'), 'error');
      return null;
    });
}

/**
 * Re-check the status card shortly after a successful apply.
 * Windows pick up the new theme within 1.5 s and also report immediately on a
 * successful apply, so the status read after 2.5 s tells whether theme.json changed
 * and the window really uses it. If not, say so instead of leaving a silent
 * "panel says switched, UI still default" mismatch for the user to discover.
 */
function verifyAppliedShortly() {
  window.setTimeout(function () {
    refreshHealth({}).then(function (info) {
      if (!info) return;
      var stateNode = $('health-state');
      var cls = stateNode ? String(stateNode.className || '') : '';
      if (cls.indexOf('is-bad') !== -1) {
        showNotice('warn', tsT('health.verifyMismatch', '主题已写进 theme.json，但窗口那边还没应用上 —— '
          + '看「运行状态」卡片里的「窗口对不上 / 样式未生效」，刷新那个窗口或重启应用即可。'));
      }
    });
  }, 2500);
}

/** Copy the data directory so it can be pasted to whoever reads the logs while troubleshooting. */
async function copyStatusPath() {
  var info = state.status;
  if (!info || !info.data_dir) {
    showToast(tsT('health.dataDirMissing', '还没有读到数据目录'), 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(String(info.data_dir));
    showToast(tsT('health.dataDirCopied', '数据目录已复制'), 'ok');
  } catch (error) {
    showNotice('info', tsT('health.dataDirNotice', '数据目录：{{path}}', { path: String(info.data_dir) }));
  }
}/* ============================================================================
 * 11. Modal
 * ==========================================================================*/

function openModal(mode, title, hint, text) {
  state.modalMode = mode;
  // Remember where focus was before opening and restore it on close (otherwise keyboard users land at the page top)
  state.modalReturnFocus = document.activeElement;
  $('modal-title').textContent = title;
  $('modal-hint').textContent = hint;
  var isMeta = mode === 'meta';
  var area = $('modal-text');
  var fields = $('modal-fields');
  if (area) {
    area.style.display = isMeta ? 'none' : '';
    area.value = isMeta ? '' : (text || '');
    area.readOnly = mode === 'export' || mode === 'help';
    if (mode === 'export') area.select();
  }
  if (fields) fields.style.display = isMeta ? '' : 'none';
  $('modal-ok').textContent = mode === 'export'
    ? tsT('modal.copyAndClose', '复制并关闭')
    : (isMeta ? tsT('modal.save', '保存') : tsT('modal.ok', '确定'));
  $('modal').hidden = false;
  area.focus();
}

function closeModal() {
  var modal = $('modal');
  if (modal) modal.hidden = true;
  var back = state.modalReturnFocus;
  state.modalReturnFocus = null;
  if (back && typeof back.focus === 'function') {
    try { back.focus(); } catch (e) { /* the element may have been replaced; ignore */ }
  }
}

/** While the modal is open, keep Tab inside it so focus does not reach the row editors behind. */
function trapModalFocus(event, dialog) {
  var panel = dialog || $('modal-panel') || $('modal');
  if (!panel) return;
  var nodes = panel.querySelectorAll('button, select, textarea, input, [tabindex]:not([tabindex="-1"])');
  var list = Array.prototype.filter.call(nodes, function (node) {
    return !node.disabled && node.offsetParent !== null;
  });
  if (!list.length) return;
  var first = list[0];
  var last = list[list.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}


var COMPARE_CURRENT_ID = '__current__';
var compareCache = new Map();
var compareGeneration = 0;

function cmpColor(value) {
  var color = parseColor(value);
  return color && [color.r, color.g, color.b, color.a];
}

function compareThemeOptions() {
  var options = [{ id: COMPARE_CURRENT_ID, label: tsT('library.tools.editable', 'Current draft') }];
  (libraryState.items || []).forEach(function (item) {
    options.push({ id: item.id, label: item.name || item.id });
  });
  return options;
}

function fillCompareSelect(select, selectedId) {
  if (!select) return;
  select.textContent = '';
  compareThemeOptions().forEach(function (opt) {
    var node = el('option', { value: opt.id, text: opt.label });
    select.appendChild(node);
  });
  var available = Array.prototype.some.call(select.options, function (o) { return o.value === selectedId; });
  select.value = available ? selectedId : (select.options[0] ? select.options[0].value : '');
}

async function compareLoad(id) {
  if (compareCache.has(id)) return compareCache.get(id);
  var work = (async function () {
    var light, dark;
    if (id === COMPARE_CURRENT_ID) {
      light = effectiveVars('light');
      dark = effectiveVars('dark');
    } else {
      var result = await callPlugin('theme_json', { theme_id: id });
      var theme = JSON.parse(result.payload);
      light = theme.vars || {};
      dark = theme.dark_vars || {};
    }
    // The server composites the same theme surfaces for the preset audit and both columns.
    var report = await callPlugin('check_contrast', { palette: light, dark: dark, standalone: true });
    return { rows: report.rows || [], light: light, dark: dark };
  })();
  compareCache.set(id, work);
  try {
    return await work;
  } catch (error) {
    if (compareCache.get(id) === work) compareCache.delete(id);
    throw error;
  }
}

function compareCell(row, vars) {
  var cell = el('span', { class: 'cmp-cell', role: 'cell' });
  if (!row) {
    cell.appendChild(el('span', { class: 'cmp-ratio is-missing', text: '—' }));
    return cell;
  }
  var swatch = el('span', { class: 'cmp-swatch', title: vars[row.bg] || '' });
  swatch.style.backgroundColor = vars[row.bg] || 'transparent';
  var text = el('span', { class: 'cmp-fg', text: vars[row.fg] || '—' });
  text.style.color = vars[row.fg] || '';
  text.style.backgroundColor = vars[row.bg] || '';
  cell.appendChild(swatch);
  cell.appendChild(text);
  cell.appendChild(el('span', { class: 'cmp-ratio' + (row.ok ? '' : ' is-fail'), text: Number(row.ratio).toFixed(2),
    title: tsT('library.tools.threshold', 'Minimum contrast: {{floor}}:1', { floor: row.floor }) }));
  return cell;
}

function renderThemeComparison(a, b) {
  var body = $('compare-body');
  var summary = $('compare-summary');
  var mode = $('compare-mode').value;
  var onlyDiff = $('compare-only-diff').checked;
  var onlyFail = $('compare-only-fail').checked;
  var pairs = new Set();
  function byPair(column) {
    var entries = new Map();
    column.rows.forEach(function (row) {
      if (row.mode === mode) { pairs.add(row.pair); entries.set(row.pair, row); }
    });
    return entries;
  }
  var left = byPair(a), right = byPair(b);
  var different = 0, failedA = 0, failedB = 0, shown = 0;
  body.textContent = '';
  pairs.forEach(function (pair) {
    var ra = left.get(pair), rb = right.get(pair), fields = ra || rb;
    var changed = !ra || !rb || ra.ratio !== rb.ratio || ra.ok !== rb.ok
      || a[mode][fields.fg] !== b[mode][fields.fg] || a[mode][fields.bg] !== b[mode][fields.bg];
    var fail = (ra && !ra.ok) || (rb && !rb.ok);
    if (changed) different += 1;
    if (ra && !ra.ok) failedA += 1;
    if (rb && !rb.ok) failedB += 1;
    if ((onlyDiff && !changed) || (onlyFail && !fail)) return;
    var row = el('div', { role: 'row', class: 'cmp-row' + (changed ? ' is-diff' : ''), 'data-pair': pair });
    row.appendChild(el('span', { class: 'cmp-label', role: 'rowheader', text: changeKeyLabel(fields.fg) }));
    row.appendChild(compareCell(ra, a[mode]));
    row.appendChild(compareCell(rb, b[mode]));
    body.appendChild(row);
    shown += 1;
  });
  summary.textContent = '';
  [
    tsT('library.tools.different', 'Different: {{count}}/{{total}}', { count: different, total: pairs.size }),
    tsT('library.tools.failures', '{{side}} failures: {{count}}', { side: 'A', count: failedA }),
    tsT('library.tools.failures', '{{side}} failures: {{count}}', { side: 'B', count: failedB }),
    tsT('library.tools.visible', 'Showing {{count}} pairs', { count: shown })
  ].forEach(function (text) { summary.appendChild(el('span', { class: 'cmp-tag', text: text })); });
}

async function refreshCompare() {
  var generation = ++compareGeneration;
  try {
    var results = await Promise.all([compareLoad($('compare-a').value), compareLoad($('compare-b').value)]);
    if (generation === compareGeneration) renderThemeComparison(results[0], results[1]);
  } catch (error) {
    if (generation !== compareGeneration) return;
    $('compare-body').textContent = '';
    $('compare-summary').textContent = tsT('library.tools.compareFailed', 'Could not compare themes: {{error}}',
      { error: pluginErrorMessage(error) });
  }
}


function openCompare(opts) {
  var modal = $('compare-modal');
  if (!modal) return;
  var options = opts || {};
  compareCache.clear();
  state.compareReturnFocus = options.returnFocus || document.activeElement;
  var items = libraryState.items || [];
  var idA = options.a || (items.length ? items[0].id : COMPARE_CURRENT_ID);
  var idB = options.b || (items.length > 1 ? items[1].id : COMPARE_CURRENT_ID);
  fillCompareSelect($('compare-a'), idA);
  fillCompareSelect($('compare-b'), idB);
  $('compare-only-diff').checked = !!options.onlyDiff;
  if ($('compare-only-fail')) $('compare-only-fail').checked = !!options.onlyFail;
  $('compare-mode').value = options.mode === 'dark' ? 'dark' : 'light';
  modal.hidden = false;
  refreshCompare();
  var firstPick = $('compare-a');
  if (firstPick) { try { firstPick.focus(); } catch (e) {  } }
}

function openCompareFor(item) {
  if (!item || !item.id) return;
  var mode = (item.contrast && item.contrast.mode === 'dark') ? 'dark' : 'light';
  openCompare({
    a: item.id,
    b: COMPARE_CURRENT_ID,
    mode: mode,
    onlyFail: true,
    returnFocus: document.activeElement
  });
}

function closeCompare() {
  var modal = $('compare-modal');
  compareGeneration += 1;
  compareCache.clear();
  if (modal) modal.hidden = true;
  var back = state.compareReturnFocus;
  state.compareReturnFocus = null;
  if (back && typeof back.focus === 'function') {
    try { back.focus(); } catch (e) {  }
  }
}

function wireCompare() {
  var openBtn = $('btn-lib-compare');
  if (openBtn) openBtn.addEventListener('click', openCompare);
  ['compare-a', 'compare-b', 'compare-mode', 'compare-only-diff', 'compare-only-fail'].forEach(function (id) {
    var node = $(id);
    if (node) node.addEventListener('change', function () { refreshCompare(); });
  });
  var closeBtn = $('compare-close');
  if (closeBtn) closeBtn.addEventListener('click', closeCompare);
  var doneBtn = $('compare-done');
  if (doneBtn) doneBtn.addEventListener('click', closeCompare);
  var modal = $('compare-modal');
  if (modal) {
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeCompare();
    });
  }
  document.addEventListener('keydown', function (event) {
    if (!modal || modal.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeCompare(); }
    if (event.key === 'Tab') trapModalFocus(event, modal);
  });
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return Promise.reject(new Error('clipboard unavailable'));
}

function onModalOk() {
  if (state.modalMode === 'meta') { saveThemeMeta(); return; }
  if (state.modalMode === 'export') {
    var area = $('modal-text');
    area.select();
    // Keep the modal open if copying fails: once closed, the text is neither copied nor visible.
    copyText(area.value).then(function () {
      showToast(tsT('modal.copied', '已复制到剪贴板'), 'ok');
      closeModal();
    }).catch(function () {
      if (area && area.focus) area.focus();
      if (area && area.select) area.select();
      showToast(tsT('modal.copyFailedSelected', '复制失败，文本已选中 —— 按 Ctrl+C 手动复制'));
    });
    return;
  }
  // The help modal is just text: "OK" only closes it and must never import it as palette JSON.
  if (state.modalMode !== 'import') { closeModal(); return; }
  doImport();
}

/** Copy the palette dump (including unapplied edits) for issues / comparisons. */
function copyDump() {
  // While collapsed the JSON may be stale (only marked dirty), so force a rebuild
  // before copying: what gets copied is the palette currently shown.
  renderDump(true);
  copyText($('dump').textContent || '').then(function () {
    showToast(tsT('modal.dumpCopied', '调色板 JSON 已复制'), 'ok');
  }).catch(function () {
    showToast(tsT('modal.copyFailedManual', '复制失败，请手动选择文本'));
  });
}

/* ============================================================================
 * 12. Guidance
 * ==========================================================================*/

function isTypingTarget(node) {
  if (!node || !node.tagName) return false;
  var tag = node.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || node.isContentEditable === true;
}

/** Editable control of the first visible row in search results, so keyboard users can press Enter and start editing. */
function firstVisibleRow() {
  var wrap = document.querySelector('.row-wrap:not([hidden])');
  if (!wrap) return null;
  // Only return text inputs: the swatch is type=color, which can neither be typed into
  // nor opens the picker on focus (browsers open it only on click), so Enter would get stuck.
  return wrap.querySelector('input.hex, input.raw');
}

/* ===== FEATURES_28_BEGIN (v0.5.0: drag-and-drop import + CSS snippet export) ===== */

/**
 * Drop a file onto the panel to import it (F7).
 *
 * Bound to document.body rather than a single card: the user aims at "the panel",
 * and which card the file lands on should not decide whether import works. A
 * dragenter/dragleave counter cancels out child-element bubbling (entering a child
 * fires leave then enter) to avoid highlight flicker.
 */
var dropDepth = 0;

function dropHasFiles(dt) {
  if (!dt || !dt.types) return false;
  for (var i = 0; i < dt.types.length; i += 1) {
    if (dt.types[i] === 'Files') return true;
  }
  return false;
}

function dropReset() {
  dropDepth = 0;
  if (document.body) document.body.classList.remove('is-drop-target');
}

function initDropImport() {
  if (!document.body || document.body.getAttribute('data-drop-import') === '1') return;
  document.body.setAttribute('data-drop-import', '1');
  // Overlay text for body.is-drop-target::after (CSS reads it via attr()).
  document.body.setAttribute('data-drop-label',
    tsT('misc.dropOverlay', '松开即可导入主题（.zip 分享包 / .json）'));

  document.body.addEventListener('dragenter', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    dropDepth += 1;
    document.body.classList.add('is-drop-target');
  });

  document.body.addEventListener('dragover', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = 'copy'; } catch (err) { /* read-only in some environments */ }
  });

  document.body.addEventListener('dragleave', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    dropDepth -= 1;
    if (dropDepth <= 0) dropReset();
  });

  document.body.addEventListener('drop', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    dropReset();
    var files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) return;
    if (files.length > 1) showToast(tsT('misc.dropMultiple', '一次只能导入一个文件，已用第一个'), 'warn');
    var file = files[0];
    var lower = String(file.name || '').toLowerCase();
    if (lower.slice(-4) !== '.zip' && lower.slice(-5) !== '.json') {
      showNotice('error', tsT('misc.dropUnsupportedNotice', '只支持拖入 .zip 分享包或 .json 主题文件。'));
      showToast(tsT('misc.dropUnsupportedToast', '文件类型不支持'), 'error');
      return;
    }
    if (state.busy) {
      showToast(tsT('misc.dropBusy', '正在忙，等这一步做完再拖'), 'warn');
      return;
    }
    showNotice('info', tsT('misc.dropReading', '正在读取「{{name}}」…', { name: file.name }));
    handleArchiveFile(file, null);
  });

  /* Dropping outside the panel makes the browser open the file (losing panel state); always block it. */
  ['dragover', 'drop'].forEach(function (type) {
    window.addEventListener(type, function (e) {
      if (dropHasFiles(e.dataTransfer)) e.preventDefault();
    });
  });
}

/**
 * Export the current palette as a CSS variable snippet (F8).
 *
 * Light goes to `:root`, dark to `:root[data-theme="dark"]`, matching the host/plugin
 * convention, so pasting it into any page reproduces the palette. An empty palette
 * returns an empty string (no empty shell file).
 */
function cssSnippetText() {
  var light = effectiveVars('light');
  var dark = effectiveVars('dark');
  var palette = state.palette || {};
  var name = palette.name || tsT('misc.untitledTheme', '未命名主题');
  var lines = [];
  var today = new Date();
  var stamp = today.getFullYear() + '-'
    + ('0' + (today.getMonth() + 1)).slice(-2) + '-'
    + ('0' + today.getDate()).slice(-2);

  function pushBlock(selector, map, label) {
    var keys = Object.keys(map || {}).sort();
    if (!keys.length) return;
    lines.push('/* ' + label + ' */');
    lines.push(selector + ' {');
    for (var i = 0; i < keys.length; i += 1) {
      lines.push('  ' + keys[i] + ': ' + map[keys[i]] + ';');
    }
    lines.push('}');
    lines.push('');
  }

  pushBlock(':root', light, tsT('misc.cssBlockLight', '{{name}} · 亮色', { name: name }));
  pushBlock(':root[data-theme="dark"]', dark, tsT('misc.cssBlockDark', '{{name}} · 暗色', { name: name }));
  if (!lines.length) return '';
  lines.unshift('/* ' + tsT('misc.cssHeader', '{{name}} —— 由 N.E.K.O 主题工作室导出（{{date}}）',
    { name: name, date: stamp }) + ' */', '');
  return lines.join('\n');
}

function copyCssSnippet() {
  var text = cssSnippetText();
  if (!text) {
    showToast(tsT('misc.cssEmpty', '当前调色板是空的，没有可导出的变量'), 'warn');
    return;
  }
  openModal(
    'export',
    tsT('modal.cssTitle', 'CSS 变量片段 · {{name}}',
      { name: (state.palette && state.palette.name) || tsT('modal.cssCurrentPalette', '当前配色') }),
    tsT('modal.cssHint', '这段 CSS 可以直接贴到别处使用：亮色在 :root，暗色在 :root[data-theme="dark"]。'
      + '变量名就是面板里那一列，颜色值已经按当前改动算好（不需要先「应用」）。'),
    text
  );
}

/* ===== FEATURES_28_END ===== */

/* ===== WALLPAPER_BEGIN (v0.11.0: wallpaper color extraction + variant generation) ===== */

var WALLPAPER_SAMPLE = 96;
var WALLPAPER_MAX_FILE = 12 * 1024 * 1024;
// `label` is the source-text fallback; the shown label is resolved via tsT('misc.variant.<id>') at use time.
var VARIANT_STEPS = [
  { id: 'warm', label: '更暖', dh: 14, ds: 0.06, dl: -0.02 },
  { id: 'cool', label: '更冷', dh: -14, ds: 0.06, dl: -0.02 },
  { id: 'bright', label: '更亮', dh: 0, ds: -0.04, dl: 0.10 },
  { id: 'deep', label: '更深', dh: 0, ds: 0.04, dl: -0.10 }
];
var VARIANT_SURFACES = [
  '--nts-chat-bg', '--nts-chat-panel-bg', '--nts-user-bubble-bg', '--nts-ai-bubble-bg',
  '--nts-input-bg', '--nts-button-bg', '--nts-send-bg', '--nts-panel-bg',
  '--nts-popover-bg', '--nts-modal-bg', '--nts-rec-bg', '--nts-chip-bg',
  '--nts-topbar-bg', '--nts-accent-bg', '--nts-code-bg', '--nts-tool-bubble-bg',
  '--nts-status-bg', '--nts-overlay-bg'
];
var VARIANT_TEXT_PAIRS = [
  ['--nts-user-bubble-text', '--nts-user-bubble-bg'],
  ['--nts-ai-bubble-text', '--nts-ai-bubble-bg'],
  ['--nts-input-text', '--nts-input-bg'],
  ['--nts-button-text', '--nts-button-bg'],
  ['--nts-send-text', '--nts-send-bg'],
  ['--nts-panel-text', '--nts-panel-bg'],
  ['--nts-popover-text', '--nts-popover-bg'],
  ['--nts-modal-text', '--nts-modal-bg'],
  ['--nts-rec-text', '--nts-rec-bg'],
  ['--nts-chip-text', '--nts-chip-bg'],
  ['--nts-topbar-text', '--nts-topbar-bg'],
  ['--nts-code-text', '--nts-code-bg'],
  ['--nts-tool-bubble-text', '--nts-tool-bubble-bg'],
  ['--nts-status-text', '--nts-status-bg']
];
var variantState = { index: 0, lastLabel: '' };

function rgbToHsl(r, g, b) {
  var rr = Math.min(255, Math.max(0, r)) / 255;
  var gg = Math.min(255, Math.max(0, g)) / 255;
  var bb = Math.min(255, Math.max(0, b)) / 255;
  var max = Math.max(rr, gg, bb);
  var min = Math.min(rr, gg, bb);
  var l = (max + min) / 2;
  var h = 0;
  var s = 0;
  if (max !== min) {
    var d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rr) h = ((gg - bb) / d + (gg < bb ? 6 : 0)) / 6;
    else if (max === gg) h = ((bb - rr) / d + 2) / 6;
    else h = ((rr - gg) / d + 4) / 6;
  }
  return { h: h * 360, s: s, l: l };
}

/* Must not be named hslToRgb: the function of that name used by parseColor earlier in
 * the file takes percentages and returns an array; a same-named declaration would
 * replace it and hsl(...) input would yield rgba(NaN, ...). This one takes 0-1
 * saturation / lightness and returns {r, g, b}; used only by quick-tune and wallpaper extraction. */
function hslFractionToRgb(h, s, l) {
  var hh = ((h % 360) + 360) % 360 / 360;
  if (s === 0) {
    var gray = Math.round(l * 255);
    return { r: gray, g: gray, b: gray };
  }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var p = 2 * l - q;
  function channel(t) {
    var tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  }
  return {
    r: Math.round(channel(hh + 1 / 3) * 255),
    g: Math.round(channel(hh) * 255),
    b: Math.round(channel(hh - 1 / 3) * 255)
  };
}

function extractImageColors(pixels, width, height) {
  var buckets = Object.create(null);
  var total = Math.min(width * height, pixels.length / 4);
  for (var i = 0; i < total; i += 1) {
    var o = i * 4;
    if (pixels[o + 3] < 32) continue;
    var r = pixels[o];
    var g = pixels[o + 1];
    var b = pixels[o + 2];
    var key = (r >> 4) + ':' + (g >> 4) + ':' + (b >> 4);
    var bucket = buckets[key];
    if (!bucket) bucket = buckets[key] = { n: 0, r: 0, g: 0, b: 0 };
    bucket.n += 1;
    bucket.r += r;
    bucket.g += g;
    bucket.b += b;
  }
  var out = [];
  Object.keys(buckets).forEach(function (key) {
    var bucket = buckets[key];
    var color = {
      r: Math.round(bucket.r / bucket.n),
      g: Math.round(bucket.g / bucket.n),
      b: Math.round(bucket.b / bucket.n)
    };
    var hsl = rgbToHsl(color.r, color.g, color.b);
    var luma = (0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b) / 255;
    var usable = luma > 0.05 && luma < 0.95 ? 1 : 0.3;
    out.push({
      r: color.r,
      g: color.g,
      b: color.b,
      hex: colorToHex({ r: color.r, g: color.g, b: color.b, a: 1 }),
      count: bucket.n,
      hue: hsl.h,
      sat: hsl.s,
      luma: luma,
      score: bucket.n * (0.25 + hsl.s) * usable
    });
  });
  out.sort(function (a, b) { return b.score - a.score; });
  return out.slice(0, 8);
}

function wallpaperInk(bg, need) {
  var white = { r: 255, g: 255, b: 255, a: 1 };
  var black = { r: 12, g: 14, b: 18, a: 1 };
  var fixed = contrastFixColor(white, bg, need) || contrastFixColor(black, bg, need);
  // contrastFixColor returns { color: {r,g,b,a}, ratio, t, toward }; convert to a CSS color string here
  if (fixed && fixed.color) return colorToHex(fixed.color);
  return contrastRatio(white, bg) >= contrastRatio(black, bg) ? '#ffffff' : '#0c0e12';
}

function wallpaperPalette(colors, mode) {
  var dark = mode === 'dark';
  var primary = colors[0] || { r: 96, g: 138, b: 200 };
  var second = colors[1] || primary;
  var third = colors[2] || second;
  var white = { r: 255, g: 255, b: 255, a: 1 };
  var black = { r: 10, g: 12, b: 16, a: 1 };
  var bg = mixToward(primary, dark ? black : white, dark ? 0.86 : 0.90);
  var panel = mixToward(primary, dark ? black : white, dark ? 0.78 : 0.86);
  var popover = mixToward(second, dark ? black : white, dark ? 0.70 : 0.96);
  var bubbleUser = mixToward(primary, dark ? black : white, dark ? 0.28 : 0.16);
  var bubbleAi = mixToward(second, dark ? black : white, dark ? 0.74 : 0.92);
  var accent = mixToward(primary, white, dark ? 0.10 : 0);
  var border = mixToward(primary, dark ? white : black, dark ? 0.20 : 0.72);
  var ink = wallpaperInk(bg, 7.0);
  var inkSub = wallpaperInk(panel, 4.5);
  var inkUser = wallpaperInk(bubbleUser, 4.5);
  var inkAi = wallpaperInk(bubbleAi, 4.5);
  var codeBg = mixToward(third, dark ? black : white, dark ? 0.80 : 0.88);
  var out = {};
  out['--nts-chat-bg'] = colorToHex(bg);
  out['--nts-chat-panel-bg'] = colorToHex(panel);
  out['--nts-topbar-bg'] = colorToHex(mixToward(panel, bg, 0.5));
  out['--nts-topbar-text'] = colorToHex(mixToward(bubbleUser, dark ? white : black, dark ? 0.82 : 0.55));
  out['--nts-user-bubble-bg'] = colorToHex(bubbleUser);
  out['--nts-user-bubble-text'] = inkUser;
  out['--nts-user-bubble-border'] = colorToHex(border);
  out['--nts-ai-bubble-bg'] = colorToHex(bubbleAi);
  out['--nts-ai-bubble-text'] = inkAi;
  out['--nts-ai-bubble-border'] = colorToHex(mixToward(second, dark ? white : black, dark ? 0.18 : 0.76));
  out['--nts-code-bg'] = colorToHex(codeBg);
  out['--nts-code-text'] = wallpaperInk(codeBg, 4.5);
  out['--nts-input-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.06 : 0.5));
  out['--nts-input-text'] = inkSub;
  out['--nts-input-border'] = colorToHex(border);
  out['--nts-input-placeholder'] = colorToHex(mixToward(panel, dark ? white : black, dark ? 0.42 : 0.45));
  out['--nts-button-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.55 : 0.72));
  out['--nts-button-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.55 : 0.72), 4.5);
  out['--nts-button-hover-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.42 : 0.62));
  out['--nts-send-bg'] = colorToHex(accent);
  out['--nts-send-text'] = wallpaperInk(accent, 4.5);
  out['--nts-panel-bg'] = colorToHex(popover);
  out['--nts-panel-text'] = wallpaperInk(popover, 4.5);
  out['--nts-panel-border'] = colorToHex(border);
  out['--nts-panel-text-sub'] = colorToHex(mixToward(popover, dark ? white : black, dark ? 0.40 : 0.40));
  out['--nts-popover-bg'] = colorToHex(popover);
  out['--nts-popover-text'] = wallpaperInk(popover, 4.5);
  out['--nts-popover-border'] = colorToHex(border);
  out['--nts-modal-bg'] = colorToHex(mixToward(popover, dark ? black : white, dark ? 0.10 : 0.35));
  out['--nts-modal-text'] = wallpaperInk(mixToward(popover, dark ? black : white, dark ? 0.10 : 0.35), 4.5);
  out['--nts-overlay-bg'] = dark ? 'rgba(6, 8, 12, 0.62)' : 'rgba(18, 22, 30, 0.32)';
  out['--nts-accent'] = colorToHex(accent);
  out['--nts-accent-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.72 : 0.86));
  out['--nts-link'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.22 : 0.14));
  out['--nts-highlight'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.55 : 0.35));
  out['--nts-rec-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.04 : 0.42));
  out['--nts-rec-text'] = wallpaperInk(mixToward(panel, dark ? black : white, dark ? 0.04 : 0.42), 4.5);
  out['--nts-rec-border'] = colorToHex(border);
  out['--nts-chip-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.68 : 0.88));
  out['--nts-chip-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.68 : 0.88), 4.5);
  out['--nts-tool-bubble-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.02 : 0.40));
  out['--nts-tool-bubble-text'] = wallpaperInk(mixToward(panel, dark ? black : white, dark ? 0.02 : 0.40), 4.5);
  out['--nts-status-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.74 : 0.90));
  out['--nts-status-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.74 : 0.90), 4.5);
  out['--nts-scrollbar-track'] = colorToHex(mixToward(bg, dark ? white : black, dark ? 0.10 : 0.06));
  out['--nts-scrollbar-thumb'] = colorToHex(mixToward(accent, bg, 0.45));
  out['--nts-panel-glow-color'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.35 : 0.20));
  out['--nts-panel-border-color'] = colorToHex(border);
  out['--nts-glass-tint'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.12 : 0.35));
  out['--nts-glass-highlight'] = colorToHex(mixToward(accent, white, 0.62));
  out['--nts-bubble-glass-tint'] = colorToHex(mixToward(bubbleAi, dark ? black : white, dark ? 0.20 : 0.45));
  return out;
}

function shiftColorValue(value, step) {
  var color = parseColor(value);
  if (!color) return null;
  var hsl = rgbToHsl(color.r, color.g, color.b);
  var alpha = typeof color.a === 'number' ? color.a : 1;
  var hue = (hsl.h + step.dh + 360) % 360;
  var sat = Math.min(1, Math.max(0, hsl.s + step.ds));
  var light = Math.min(0.97, Math.max(0.03, hsl.l + step.dl));
  var rgb = hslFractionToRgb(hue, sat, light);
  if (alpha < 0.999) {
    return 'rgba(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ', ' + Math.round(alpha * 1000) / 1000 + ')';
  }
  return colorToHex({ r: rgb.r, g: rgb.g, b: rgb.b, a: 1 });
}

function buildVariantPalette(step) {
  var out = { light: {}, dark: {} };
  ['light', 'dark'].forEach(function (mode) {
    var current = effectiveVars(mode) || {};
    VARIANT_SURFACES.forEach(function (key) {
      if (!current[key]) return;
      var shifted = shiftColorValue(String(current[key]), step);
      if (shifted) out[mode][key] = shifted;
    });
    VARIANT_TEXT_PAIRS.forEach(function (pair) {
      var text = out[mode][pair[0]] || current[pair[0]];
      var bg = out[mode][pair[1]] || current[pair[1]];
      if (!text || !bg) return;
      var fixed = contrastFixColor(parseColor(text), parseColor(bg), 4.5);
      if (fixed && fixed.color) out[mode][pair[0]] = colorToHex(fixed.color);
      else if (!out[mode][pair[0]]) out[mode][pair[0]] = String(text);
    });
  });
  return out;
}

function applyWallpaperPalette(palette, label) {
  if (!palette || !palette.light || !palette.dark) return 0;
  var changed = 0;
  undoPush('', true);
  [['light', palette.light], ['dark', palette.dark]].forEach(function (pair) {
    var mode = pair[0];
    var values = pair[1];
    Object.keys(values).forEach(function (key) {
      var value = values[key];
      if (value === undefined || value === null || value === '') return;
      state.edited[mode][key] = String(value);
      delete state.cleared[mode][key];
      changed += 1;
    });
  });
  if (!changed) return 0;
  markAllRowsValid();
  clearRgbMemory();
  syncAll();
  renderChanges();
  updateUndoButtons();
  showToast(tsT('misc.wallpaperFilled', '已填入 {{count}} 个变量（Ctrl+Z 可撤销）', { count: changed }), 'ok');
  showNotice('info', tsT('misc.wallpaperGenerated', '已按「{{label}}」生成整套配色：先看看效果，满意再点「应用」，不满意 Ctrl+Z 撤销。', { label: label }));
  return changed;
}

function openWallpaperPicker() {
  var input = $('wallpaper-file');
  if (!input) return;
  input.value = '';
  input.click();
}

function handleWallpaperFile(file) {
  if (!file) return;
  var name = String(file.name || tsT('misc.wallpaperDefaultName', '壁纸'));
  if (!/^image\//.test(String(file.type || ''))) {
    showToast(tsT('misc.wallpaperNotImage', '请选一张图片（png / jpg / webp / gif）'), 'warn');
    return;
  }
  if (file.size > WALLPAPER_MAX_FILE) {
    showToast(tsT('misc.wallpaperTooLarge', '图片太大了（上限 12 MB），先压缩一下再试'), 'warn');
    return;
  }
  var reader = new FileReader();
  reader.onerror = function () { showToast(tsT('misc.wallpaperReadFailed', '这张图片读不出来，换一张试试'), 'error'); };
  reader.onload = function () {
    var img = new Image();
    img.onerror = function () { showToast(tsT('misc.wallpaperDecodeFailed', '这张图片解码失败，换一张试试'), 'error'); };
    img.onload = function () {
      try {
        var canvas = document.createElement('canvas');
        canvas.width = WALLPAPER_SAMPLE;
        canvas.height = WALLPAPER_SAMPLE;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE);
        var data = ctx.getImageData(0, 0, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE).data;
        var colors = extractImageColors(data, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE);
        if (!colors.length) {
          showToast(tsT('misc.wallpaperNoColors', '这张图里没取到可用颜色（可能大部分是透明的）'), 'warn');
          return;
        }
        applyWallpaperPalette({ light: wallpaperPalette(colors, 'light'), dark: wallpaperPalette(colors, 'dark') }, name);
      } catch (err) {
        showToast(tsT('misc.wallpaperFailed', '生成配色失败：{{error}}', { error: String(err && err.message ? err.message : err) }), 'error');
      }
    };
    img.src = String(reader.result || '');
  };
  reader.readAsDataURL(file);
}

function applyNextVariant() {
  var step = VARIANT_STEPS[variantState.index % VARIANT_STEPS.length];
  variantState.index += 1;
  var palette = buildVariantPalette(step);
  var stepLabel = tsT('misc.variant.' + step.id, step.label);
  var changed = applyWallpaperPalette(palette, tsT('misc.variantLabel', '变体 · {{label}}', { label: stepLabel }));
  if (!changed) showToast(tsT('misc.variantNoColors', '当前配色里没有可调整的颜色变量'), 'warn');
  else variantState.lastLabel = step.label;
}

function initWallpaperFeature() {
  if ($('btn-wallpaper')) $('btn-wallpaper').addEventListener('click', openWallpaperPicker);
  if ($('wallpaper-file')) {
    $('wallpaper-file').addEventListener('change', function () {
      var files = this.files;
      handleWallpaperFile(files && files[0]);
      this.value = '';
    });
  }
  if ($('btn-variants')) $('btn-variants').addEventListener('click', applyNextVariant);
}

/* ===== WALLPAPER_END ===== */

/* ===== BATCH3_BEGIN (v0.12.0: quick tune - three glow levels / three font presets) =====
 * Both are one-click fills: a set of variables goes into the editor at once and the
 * user then presses "Apply". They only write state.edited (no apply_theme), so:
 *   - the result shows immediately in the preview / JSON / change list;
 *   - Ctrl+Z reverts it in one step (still goes through undoPush);
 *   - mixing with manual edits is fine: it just markEdited()s several values together.
 * `label` fields are source-text fallbacks; shown labels are resolved via tsT at use time.
 */
var GLOW_LEVELS = {
  low: { label: '低', value: '0.15' },
  medium: { label: '中', value: '0.45' },
  high: { label: '高', value: '0.85' }
};
/* The four targets of "glow intensity" (levels 0.15 / 0.45 / 0.85 decided 2026-11-19):
 *   ambient-glow       pointer-following soft glow
 *   glass-dispersion   edge dispersion
 *   glass-caustic      flowing caustics
 *   panel-glow-pulse   backplate breathing */
var GLOW_LEVEL_KEYS = [
  '--nts-ambient-glow',
  '--nts-glass-dispersion',
  '--nts-glass-caustic',
  '--nts-panel-glow-pulse'
];
/* Three font presets (as decided: "three font presets"). System default = clear both variables and fall back to the app's native font. */
var FONT_PRESETS = {
  system: { label: '系统默认', family: '', lineHeight: '' },
  sans: {
    label: '无衬线',
    family: 'Inter, PingFang SC, Microsoft YaHei, Noto Sans SC, system-ui, sans-serif',
    lineHeight: '1.6'
  },
  mono: {
    label: '等宽',
    family: 'JetBrains Mono, Cascadia Code, Consolas, Microsoft YaHei, monospace',
    lineHeight: '1.55'
  }
};

function glowLevelOfValue(value) {
  var v = String(value == null ? '' : value).trim();
  if (!v) return '';
  var num = parseFloat(v);
  if (!isFinite(num)) return '';
  var best = '';
  var bestGap = 0.06;
  Object.keys(GLOW_LEVELS).forEach(function (id) {
    var gap = Math.abs(parseFloat(GLOW_LEVELS[id].value) - num);
    if (gap <= bestGap) { bestGap = gap; best = id; }
  });
  return best;
}

/** Which level the current config matches (all four values must be near the same level, else empty). */
function currentGlowLevel() {
  var hit = '';
  for (var i = 0; i < GLOW_LEVEL_KEYS.length; i += 1) {
    var level = glowLevelOfValue(displayValue(GLOW_LEVEL_KEYS[i]));
    if (!level) return '';
    if (hit && level !== hit) return '';
    hit = level;
  }
  return hit;
}

/** Which preset the current font matches (exact font-family match; system default = both variables empty). */
function currentFontPreset() {
  var family = String(displayValue('--nts-font-family') || '').trim();
  var lineHeight = String(displayValue('--nts-line-height') || '').trim();
  if (!family && !lineHeight) return 'system';
  var ids = Object.keys(FONT_PRESETS);
  for (var i = 0; i < ids.length; i += 1) {
    var preset = FONT_PRESETS[ids[i]];
    if (!preset.family) continue;
    if (preset.family === family) return ids[i];
  }
  return '';
}

function applyGlowLevel(level) {
  var def = GLOW_LEVELS[level];
  if (!def) return;
  undoGroup(function () {
    GLOW_LEVEL_KEYS.forEach(function (key) {
      markEdited(key, def.value);
      syncRow(key);
    });
  });
  renderQuickPresets();
  showToast(tsT('misc.glowApplied', '光效强度已设为「{{label}}」（四项变量，Ctrl+Z 可撤销）',
    { label: tsT('misc.glow.' + level, def.label) }), 'ok');
}

function applyFontPreset(id) {
  var preset = FONT_PRESETS[id];
  if (!preset) return;
  undoGroup(function () {
    if (preset.family) {
      markEdited('--nts-font-family', preset.family);
    } else {
      markCleared('--nts-font-family');
    }
    syncRow('--nts-font-family');
    if (preset.lineHeight) {
      markEdited('--nts-line-height', preset.lineHeight);
    } else {
      markCleared('--nts-line-height');
    }
    syncRow('--nts-line-height');
  });
  renderQuickPresets();
  showToast(tsT('misc.fontApplied', '字体已切到「{{label}}」（点「应用」才生效，Ctrl+Z 可撤销）',
    { label: tsT('misc.font.' + id, preset.label) }), 'ok');
}

var quickState = { glow: '', font: '' };

function renderQuickPresets() {
  quickState.glow = currentGlowLevel();
  quickState.font = currentFontPreset();
  ['low', 'medium', 'high'].forEach(function (id) {
    var btn = $('btn-glow-' + id);
    if (!btn) return;
    var on = quickState.glow === id;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  ['system', 'sans', 'mono'].forEach(function (id) {
    var btn = $('btn-font-' + id);
    if (!btn) return;
    var on = quickState.font === id;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function initQuickPresets() {
  ['low', 'medium', 'high'].forEach(function (id) {
    var btn = $('btn-glow-' + id);
    if (btn) btn.addEventListener('click', function () { applyGlowLevel(id); });
  });
  ['system', 'sans', 'mono'].forEach(function (id) {
    var btn = $('btn-font-' + id);
    if (btn) btn.addEventListener('click', function () { applyFontPreset(id); });
  });
  renderQuickPresets();
}

/* ===== BATCH3_END ===== */



var RIM_PREF_KEY = 'neko.themeStudio.rim';
var LOOK_RIM = { clear: '2', std: '1', solid: '0' };

var LOOK_CHROMA = { clear: '2', std: '1', solid: '0' };

var CHROMA_PREF_KEY = 'neko.themeStudio.chroma';

function readChromaPref() {
  try {
    var raw = window.localStorage.getItem(CHROMA_PREF_KEY);
    return raw === '0' || raw === '2' ? raw : '1';
  } catch (error) { return '1'; }
}

function setChromaPref(value) {
  var v = value === '0' || value === '2' ? value : '1';
  try { window.localStorage.setItem(CHROMA_PREF_KEY, v); } catch (error) { /* Optional in restricted browser contexts. */ }
  if (window.NekoThemeStudio && typeof window.NekoThemeStudio.setChroma === 'function') {
    window.NekoThemeStudio.setChroma(v);
  }
  try {
    document.dispatchEvent(new CustomEvent('nts:chroma-change', { detail: { value: v } }));
  } catch (error) { /* Optional in restricted browser contexts. */ }
  return v;
}

var HEAVY_PREF_KEY = 'neko.themeStudio.heavy';
var HEAVY_LEVELS = [
  { id: 'btn-heavy-auto', value: 'auto' },
  { id: 'btn-heavy-on', value: 'on' },
  { id: 'btn-heavy-off', value: 'off' }
];

function readHeavyPref() {
  try {
    var raw = window.localStorage.getItem(HEAVY_PREF_KEY);
    return raw === 'on' || raw === 'off' ? raw : 'auto';
  } catch (error) { return 'auto'; }
}

function setHeavyPref(value) {
  var v = value === 'on' || value === 'off' ? value : 'auto';
  try { window.localStorage.setItem(HEAVY_PREF_KEY, v); } catch (error) { /* Optional in restricted browser contexts. */ }
  if (window.NekoThemeStudio && typeof window.NekoThemeStudio.setHeavy === 'function') {
    window.NekoThemeStudio.setHeavy(v);
  }
  try {
    document.dispatchEvent(new CustomEvent('nts:heavy-change', { detail: { value: v } }));
  } catch (error) { /* Optional in restricted browser contexts. */ }
  return v;
}

function syncHeavyButtons() {
  var current = readHeavyPref();
  HEAVY_LEVELS.forEach(function (level) {
    var node = document.getElementById(level.id);
    if (!node) return;
    var active = level.value === current;
    node.setAttribute('aria-pressed', active ? 'true' : 'false');
    node.classList.toggle('is-active', active);
  });
}

function wireHeavyButtons() {
  HEAVY_LEVELS.forEach(function (level) {
    var node = document.getElementById(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      setHeavyPref(level.value);
      syncHeavyButtons();
      showToast(tsT('glass.setting', '{{value}} selected.', { value: node.textContent }), 'ok');
    });
  });
  document.addEventListener('nts:heavy-change', syncHeavyButtons);
  syncHeavyButtons();
}

function readRimPref() {
  try {
    var raw = window.localStorage.getItem(RIM_PREF_KEY);
    return raw === '0' || raw === '2' ? raw : '1';
  } catch (error) { return '1'; }
}

function setRimPref(value) {
  var v = value === '0' || value === '2' ? value : '1';
  try { window.localStorage.setItem(RIM_PREF_KEY, v); } catch (error) { /* Optional in restricted browser contexts. */ }
  if (window.NekoThemeStudio && typeof window.NekoThemeStudio.setRim === 'function') {
    window.NekoThemeStudio.setRim(v);
  }
  try {
    document.dispatchEvent(new CustomEvent('nts:rim-change', { detail: { value: v } }));
  } catch (error) { /* Optional in restricted browser contexts. */ }
  return v;
}

var LOOK_LEVELS = [

  { id: 'clear', opacity: 0.46, brightness: 0.7 },
  { id: 'std', opacity: 0.56, brightness: 0.88 },
  { id: 'solid', opacity: 0.68, brightness: 1 }
];
var LOOK_BRIGHTNESS_KEY = '--nts-chat-bg-image-brightness';

function currentLookOpacity() {
  var api = window.NekoMaterials;
  try {
    var snap = api && typeof api.snapshot === 'function' ? api.snapshot() : null;
    if (snap && snap.global && snap.global.opacity !== undefined) return Number(snap.global.opacity);
  } catch (error) { /* Optional in restricted browser contexts. */ }
  return null;
}

function currentLookBrightness() {
  var ed = state.edited[state.mode] || {};
  if (Object.prototype.hasOwnProperty.call(ed, LOOK_BRIGHTNESS_KEY)) {
    return Number(ed[LOOK_BRIGHTNESS_KEY]);
  }
  var palette = state.palette || {};
  var bag = (state.mode === 'dark' ? palette.dark_vars : palette.vars) || {};
  if (Object.prototype.hasOwnProperty.call(bag, LOOK_BRIGHTNESS_KEY)) {
    return Number(bag[LOOK_BRIGHTNESS_KEY]);
  }
  var light = palette.vars || {};
  if (Object.prototype.hasOwnProperty.call(light, LOOK_BRIGHTNESS_KEY)) {
    return Number(light[LOOK_BRIGHTNESS_KEY]);
  }
  return 1;
}

function syncLookButtons() {
  var opacity = currentLookOpacity();
  var brightness = currentLookBrightness();
  LOOK_LEVELS.forEach(function (level) {
    var btn = $('btn-look-' + level.id);
    if (!btn) return;
    var active = opacity !== null && Math.abs(opacity - level.opacity) < 0.005 &&
      Math.abs(brightness - level.brightness) < 0.02;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function applyLookLevel(level) {
  var api = window.NekoMaterials;
  if (!api || typeof api.patch !== 'function') {
    showToast(tsT('glass.unavailable', 'The material editor is still loading.'), 'warn');
    return;
  }
  api.patch([{ key: 'opacity', value: level.opacity }], 'both');

  markEdited(LOOK_BRIGHTNESS_KEY, String(level.brightness));

  var rimValue = LOOK_RIM[level.id];
  setRimPref(rimValue);
  setChromaPref(LOOK_CHROMA[level.id]);
  syncAll();
  syncLookButtons();

  updateButtons();
  showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: $('btn-look-' + level.id).textContent }), 'info');
}

function wireLookLevels() {
  LOOK_LEVELS.forEach(function (level) {
    var btn = $('btn-look-' + level.id);
    if (!btn) return;
    btn.addEventListener('click', function () { applyLookLevel(level); });
  });
  syncLookButtons();
}

var MATERIAL_PRESETS = ['clear', 'frosted', 'liquid', 'smoke', 'soft', 'prism'];

function currentMaterialPreset() {
  var api = window.NekoMaterials;
  try {
    var snap = api && typeof api.snapshot === 'function' ? api.snapshot() : null;
    var id = snap && snap.preset ? String(snap.preset) : '';
    return MATERIAL_PRESETS.indexOf(id) >= 0 ? id : 'custom';
  } catch (error) {
    return 'custom';
  }
}

function syncMaterialPresetSelect() {
  var sel = $('sel-material-preset');
  if (!sel) return;
  var id = currentMaterialPreset();
  if (sel.value !== id) sel.value = id;
}

function applyMaterialPreset(id) {
  var api = window.NekoMaterials;
  if (!api || typeof api.applyPreset !== 'function') {
    showToast(tsT('glass.unavailable', 'The material editor is still loading.'), 'warn');
    return false;
  }
  api.applyPreset(id);

  syncLookButtons();
  try { document.dispatchEvent(new CustomEvent('nts:materials-change')); } catch (error) { /* Optional in restricted browser contexts. */ }
  syncMaterialPresetSelect();
  updateButtons();
  var label = materialPresetLabel(id);

  var note = (typeof api.lastPresetNote === 'function') ? api.lastPresetNote() : '';
  showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: note ? label + ' (' + note + ')' : label }), 'ok');
  return true;
}

var AUTO_WALLPAPER_KEY = '--nts-chat-bg-image';

function wallpaperUrl() {
  var raw = String(displayValue(AUTO_WALLPAPER_KEY) || '').trim();
  if (!RE_BG_VALUE.test(raw)) return '';
  var match = /url\(\s*["']?([^"')]+)["']?\s*\)/.exec(raw);
  if (!match) return '';
  var href = match[1];
  try { return new URL(href, window.location.href).href; } catch (error) { return ''; }
}

function wallpaperStats(url) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    img.decoding = 'async';
    img.onload = function () {
      try {
        var side = 48;
        var canvas = document.createElement('canvas');
        canvas.width = side;
        canvas.height = side;
        var ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, side, side);
        var data = ctx.getImageData(0, 0, side, side).data;
        var n = 0;
        var sumL = 0;
        var sumS = 0;
        var sumL2 = 0;
        for (var i = 0; i + 3 < data.length; i += 4) {
          if (data[i + 3] < 8) continue;
          var r = data[i] / 255;
          var g = data[i + 1] / 255;
          var b = data[i + 2] / 255;
          var max = Math.max(r, g, b);
          var min = Math.min(r, g, b);
          var l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
          var s = max <= 0 ? 0 : (max - min) / max;
          n += 1;
          sumL += l;
          sumS += s;
          sumL2 += l * l;
        }
        if (n < 8) { reject(new Error(tsT('glass.transparent', 'The image is almost transparent.'))); return; }
        var meanL = sumL / n;
        var variance = Math.max(0, sumL2 / n - meanL * meanL);
        resolve({ luminance: meanL, saturation: sumS / n, contrast: Math.sqrt(variance) });
      } catch (error) {
        reject(error);
      }
    };
    img.onerror = function () { reject(new Error(tsT('glass.loadFailed', 'The image could not be loaded.'))); };
    img.src = url;
  });
}

function pickPresetForStats(stats) {
  var l = Number(stats && stats.luminance) || 0;
  var s = Number(stats && stats.saturation) || 0;
  var c = Number(stats && stats.contrast) || 0;
  if (l < 0.22) return 'smoke';
  if (s > 0.42) return l > 0.58 ? 'prism' : 'liquid';
  if (c > 0.20) return 'frosted';
  if (s < 0.10 && l > 0.70) return 'clear';
  return 'soft';
}

function materialPresetLabel(id) {
  var sel = $('sel-material-preset');
  if (sel) {
    var opt = sel.querySelector('option[value="' + id + '"]');
    if (opt) return String(opt.textContent);
  }
  return id;
}

function describeWallpaperStats(stats) {
  return tsT('glass.stats', 'Brightness {{brightness}} · Saturation {{saturation}} · Contrast {{contrast}}', { brightness: stats.luminance.toFixed(2), saturation: stats.saturation.toFixed(2), contrast: stats.contrast.toFixed(2) });
}

function autoMaterialFromWallpaper() {
  var url = wallpaperUrl();
  if (!url) {
    showToast(tsT('glass.noWallpaper', 'Choose a chat background image in the palette first.'), 'warn');
    return Promise.resolve({ preset: '', reason: 'no-wallpaper', stats: null });
  }
  return wallpaperStats(url).then(function (stats) {
    var id = pickPresetForStats(stats);
    if (url !== wallpaperUrl()) return { preset: '', reason: 'wallpaper-changed', stats: stats };
    if (!applyMaterialPreset(id)) return { preset: '', reason: 'unavailable', stats: stats };
    showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: materialPresetLabel(id) + ' (' + describeWallpaperStats(stats) + ')' }), 'info');
    return { preset: id, reason: 'ok', stats: stats };
  }).catch(function (error) {
    var message = (error && error.message) || String(error);
    showToast(tsT('glass.imageReadError', 'Could not read the wallpaper: {{error}}', { error: message }), 'warn');
    return { preset: '', reason: 'error', stats: null, error: message };
  });
}

function paletteHexContrast(a, b) {
  return contrastRatio(parseColor(a), parseColor(b));
}

function mixPaletteHex(a, b, weight) {
  return colorToHex(mixToward(parseColor(a), parseColor(b), weight));
}

function ensurePaletteContrast(fg, bg, target) {
  var want = Number(target) || 4.5;
  var best = fg;
  var bestRatio = paletteHexContrast(fg, bg);
  if (bestRatio >= want) return { value: best, ratio: bestRatio };

  var dirs = ['#000000', '#ffffff'];
  for (var d = 0; d < dirs.length; d += 1) {
    for (var i = 1; i <= 20; i += 1) {
      var candidate = mixPaletteHex(fg, dirs[d], i / 20);
      var ratio = paletteHexContrast(candidate, bg);
      if (ratio > bestRatio) { best = candidate; bestRatio = ratio; }
      if (ratio >= want) return { value: candidate, ratio: ratio };
    }
  }
  return { value: best, ratio: bestRatio };
}

function adjustPaletteLuminance(hex, low, high) {
  var color = hex;
  var lum = colorLuma(parseColor(color));
  var guard = 0;
  while (lum > high && guard < 24) { color = mixPaletteHex(color, '#000000', 0.08); lum = colorLuma(parseColor(color)); guard += 1; }
  while (lum < low && guard < 48) { color = mixPaletteHex(color, '#ffffff', 0.10); lum = colorLuma(parseColor(color)); guard += 1; }
  return color;
}

function wallpaperBuckets(url) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    img.decoding = 'async';
    img.onload = function () {
      try {
        var side = 64;
        var canvas = document.createElement('canvas');
        canvas.width = side;
        canvas.height = side;
        var ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, side, side);
        var data = ctx.getImageData(0, 0, side, side).data;
        var buckets = {};
        var total = 0;
        for (var i = 0; i + 3 < data.length; i += 4) {
          if (data[i + 3] < 16) continue;
          var rgb = [data[i], data[i + 1], data[i + 2]];
          var hsl = rgbToHsl(rgb[0], rgb[1], rgb[2]);
          var satBand = hsl.s < 0.14 ? 0 : (hsl.s < 0.42 ? 1 : 2);
          var lightBand = hsl.l < 0.32 ? 0 : (hsl.l < 0.68 ? 1 : 2);
          var key = (satBand === 0 ? 'n' : Math.floor(hsl.h / 30)) + '_' + satBand + '_' + lightBand;
          if (!buckets[key]) buckets[key] = { n: 0, r: 0, g: 0, b: 0, s: 0, l: 0, hue: hsl.h };
          var bucket = buckets[key];
          bucket.n += 1;
          bucket.r += rgb[0];
          bucket.g += rgb[1];
          bucket.b += rgb[2];
          bucket.s += hsl.s;
          bucket.l += hsl.l;
          total += 1;
        }
        if (total < 32) { reject(new Error(tsT('glass.transparent', 'The image is almost transparent.'))); return; }
        var list = Object.keys(buckets).map(function (key) {
          var bucket = buckets[key];
          return {
            key: key,
            weight: bucket.n / total,
            hex: toHex(bucket.r / bucket.n, bucket.g / bucket.n, bucket.b / bucket.n),
            saturation: bucket.s / bucket.n,
            lightness: bucket.l / bucket.n,
            hue: bucket.hue
          };
        }).sort(function (a, b) { return b.weight - a.weight; });
        resolve({ buckets: list, total: total });
      } catch (error) {
        reject(error);
      }
    };
    img.onerror = function () { reject(new Error(tsT('glass.loadFailed', 'The image could not be loaded.'))); };
    img.src = url;
  });
}

function p55BuildVars(extracted, mode, seeds) {
  var dark = mode === 'dark';
  var buckets = (extracted && extracted.buckets) || [];
  if (!buckets.length && !(seeds && seeds.background && seeds.accent)) throw new Error(tsT('glass.noColors', 'No colors could be extracted.'));
  var dominant = buckets[0] || { hex: seeds.background, weight: 1, saturation: 0, lightness: 0.5, hue: 0 };

  var background = adjustPaletteLuminance(
    (seeds && seeds.background) ? seeds.background : dominant.hex,
    dark ? 0.015 : 0.82, dark ? 0.055 : 0.96
  );
  var vivid = null;
  buckets.forEach(function (bucket) {
    if (bucket.saturation < 0.12) return;

    var stand = paletteHexContrast(bucket.hex, background);
    if (stand < 1.8) return;
    var score = bucket.saturation * Math.pow(bucket.weight, 0.35) * Math.min(1, stand / 3);
    if (!vivid || score > vivid.score) { vivid = { bucket: bucket, score: score }; }
  });
  if (!vivid) {
    var best = null;
    buckets.forEach(function (bucket) {
      var c = paletteHexContrast(bucket.hex, background);
      if (!best || c > best.c) best = { bucket: bucket, c: c };
    });
    vivid = { bucket: best ? best.bucket : dominant, score: 0 };
  }

  var accentSource = (seeds && seeds.accent) ? seeds.accent : vivid.bucket.hex;
  var accent = accentSource;

  accent = ensurePaletteContrast(accent, background, 3).value;

  var panel = dark ? mixPaletteHex(background, '#ffffff', 0.05) : mixPaletteHex(background, '#ffffff', 0.55);
  var surface = dark ? mixPaletteHex(background, '#ffffff', 0.10) : mixPaletteHex(background, '#ffffff', 0.74);

  var panelText = ensurePaletteContrast(dark ? '#f2f6fa' : '#1d232b', panel, 7).value;
  var panelTextSub = ensurePaletteContrast(dark ? '#c3ccd6' : '#4a5563', panel, 4.5).value;
  var text = panelText;
  var textSub = panelTextSub;
  var topbar = mixPaletteHex(accent, background, dark ? 0.78 : 0.82);
  var topbarText = ensurePaletteContrast(text, topbar, 4.5).value;
  var userBubble = mixPaletteHex(accent, background, dark ? 0.72 : 0.82);
  var userText = ensurePaletteContrast(text, userBubble, 7).value;
  var aiBubble = surface;
  var aiText = ensurePaletteContrast(text, aiBubble, 7).value;
  var inputBg = dark ? mixPaletteHex(background, '#ffffff', 0.12) : mixPaletteHex(background, '#ffffff', 0.9);
  var inputText = ensurePaletteContrast(text, inputBg, 7).value;
  var inputBorder = mixPaletteHex(accent, inputBg, 0.55);
  var codeBg = mixPaletteHex(background, '#000000', dark ? 0.35 : 0.07);
  var codeText = ensurePaletteContrast(dark ? '#e6edf3' : '#22303c', codeBg, 7).value;
  var highlight = mixPaletteHex(accent, '#ffffff', dark ? 0.25 : 0.68);
  var link = ensurePaletteContrast(accent, background, 4.5).value;

  var vars = {
    '--nts-chat-bg': background,
    '--nts-chat-panel-bg': panel,
    '--nts-panel-bg': panel,
    '--nts-panel-text': panelText,
    '--nts-panel-text-sub': panelTextSub,
    '--nts-panel-border': mixPaletteHex(accent, panel, 0.62),
    '--nts-topbar-bg': topbar,
    '--nts-topbar-text': topbarText,
    '--nts-input-bg': inputBg,
    '--nts-input-text': inputText,
    '--nts-input-border': inputBorder,
    '--nts-input-placeholder': ensurePaletteContrast(textSub, inputBg, 3).value,
    '--nts-user-bubble-bg': userBubble,
    '--nts-user-bubble-text': userText,
    '--nts-user-bubble-border': mixPaletteHex(accent, userBubble, 0.6),
    '--nts-ai-bubble-bg': aiBubble,
    '--nts-ai-bubble-text': aiText,
    '--nts-ai-bubble-border': mixPaletteHex(accent, aiBubble, 0.78),
    '--nts-code-bg': codeBg,
    '--nts-code-text': codeText,
    '--nts-accent': accent,
    '--nts-link': link,
    '--nts-highlight': highlight,
    '--nts-send-bg': accent,
    '--nts-send-text': ensurePaletteContrast(dark ? '#101418' : '#ffffff', accent, 4.5).value,
    '--nts-button-bg': mixPaletteHex(accent, panel, 0.35),
    '--nts-button-text': ensurePaletteContrast(dark ? '#101418' : '#ffffff', mixPaletteHex(accent, panel, 0.35), 4.5).value,
    '--nts-chip-bg': mixPaletteHex(accent, background, 0.86),
    '--nts-chip-text': ensurePaletteContrast(text, mixPaletteHex(accent, background, 0.86), 7).value
  };

  var notes = [tsT('glass.colors', 'Background {{background}} · Accent {{accent}} · Text contrast {{ratio}}:1', { background: background, accent: accent, ratio: paletteHexContrast(text, background).toFixed(1) })];
  return { vars: vars, notes: notes, accent: accent, background: background };
}

var PALETTE_SCHEMES = [
  { id: 'sakura', background: '#fff6f8', accent: '#e5789a' },
  { id: 'ocean', background: '#f2f7fb', accent: '#1f6f9e' },
  { id: 'forest', background: '#f3f7f1', accent: '#2f7d4f' },
  { id: 'dusk', background: '#fff5e8', accent: '#c2661a' },
  { id: 'graphite', background: '#f4f5f7', accent: '#4a5568' },
  { id: 'aurora', background: '#eef7f4', accent: '#0f7d6b' }
];

function paletteSchemeName(id) {
  var select = $('palette-preset');
  var option = select && select.querySelector('option[value="' + id + '"]');
  return option ? option.textContent : id;
}

function schemeBuckets(scheme) {
  function bucket(hex, weight) {
    var rgb = parseColor(hex);
    var hsl = rgbToHsl(rgb.r, rgb.g, rgb.b);
    return {
      hex: hex,
      weight: weight,
      saturation: hsl.s,
      lightness: hsl.l,
      hue: hsl.h
    };
  }
  return { buckets: [bucket(scheme.background, 0.62), bucket(scheme.accent, 0.38)], total: 2 };
}

function applyPaletteScheme(id) {
  var scheme = null;
  PALETTE_SCHEMES.forEach(function (item) { if (item.id === id) scheme = item; });
  if (!scheme) return { reason: 'unknown-scheme', id: id, vars: {} };
  var mode = (window.state && state.mode) || 'light';
  var drafted = null;
  try {
    drafted = draftPaletteBoth(schemeBuckets(scheme), { background: scheme.background, accent: scheme.accent });
  } catch (error) {
    showToast(tsT('glass.paletteError', 'Could not generate the palette: {{error}}', { error: (error && error.message) || error }), 'warn');
    return { reason: 'error', id: id, vars: {}, error: String(error) };
  }
  showToast(tsT('glass.paletteReady', 'Drafted {{count}} colors for each mode. {{detail}}. Click Apply to save.', { count: Object.keys(drafted.light.vars).length, detail: drafted.notes.join(" / ") }), 'info');
  return {
    reason: 'ok',
    id: id,
    name: paletteSchemeName(scheme.id),
    vars: drafted.light.vars,
    darkVars: drafted.dark.vars,
    notes: drafted.notes,
    mode: mode
  };
}

function wirePaletteScheme() {
  var select = $('palette-preset');
  if (!select) return;
  select.addEventListener('change', function () {
    var id = select.value;
    if (!id) return;
    applyPaletteScheme(id);

    select.value = '';
  });
}

window.NekoPaletteSchemes = {
  get list() { return PALETTE_SCHEMES.map(function (item) { return { id: item.id, name: paletteSchemeName(item.id) }; }); },
  apply: applyPaletteScheme,
  build: function (id, mode) {
    var scheme = null;
    PALETTE_SCHEMES.forEach(function (item) { if (item.id === id) scheme = item; });
    if (!scheme) throw new Error('unknown scheme: ' + id);
    return p55BuildVars(schemeBuckets(scheme), mode || ((window.state && state.mode) || 'light'),
      { background: scheme.background, accent: scheme.accent });
  }
};

function autoPaletteFromWallpaper() {
  var url = wallpaperUrl();
  if (!url) {
    showToast(tsT('glass.noWallpaper', 'Choose a chat background image in the palette first.'), 'warn');
    return Promise.resolve({ reason: 'no-wallpaper', vars: {} });
  }
  var mode = (window.state && state.mode) || 'light';
  return wallpaperBuckets(url).then(function (extracted) {
    if (url !== wallpaperUrl()) return { reason: 'wallpaper-changed', vars: {} };
    var drafted = draftPaletteBoth(extracted);
    showToast(tsT('glass.paletteReady', 'Drafted {{count}} colors for each mode. {{detail}}. Click Apply to save.', { count: Object.keys(drafted.light.vars).length, detail: drafted.notes.join(" / ") }), 'info');
    return {
      reason: 'ok',
      vars: drafted.light.vars,
      darkVars: drafted.dark.vars,
      notes: drafted.notes,
      mode: mode
    };
  }).catch(function (error) {
    var message = (error && error.message) || String(error);
    showToast(tsT('glass.imageReadError', 'Could not read the wallpaper: {{error}}', { error: message }), 'warn');
    return { reason: 'error', vars: {}, error: message };
  });
}

function wireAutoPalette() {
  var btn = $('btn-auto-palette');
  if (!btn) return;
  btn.addEventListener('click', function () { autoPaletteFromWallpaper(); });
}

window.NekoPaletteAuto = {
  fromImage: function (url, mode) {
    return wallpaperBuckets(url).then(function (extracted) {
      var built = p55BuildVars(extracted, mode || 'light');
      return { vars: built.vars, notes: built.notes, accent: built.accent, background: built.background,
        buckets: extracted.buckets.length };
    });
  },
  fromWallpaper: function () { return autoPaletteFromWallpaper(); },
  contrast: paletteHexContrast,
  ensure: function (fg, bg, target) { return ensurePaletteContrast(fg, bg, target); }
};

function wireAutoMaterial() {
  var btn = $('btn-auto-material');
  if (!btn) return;
  btn.addEventListener('click', function () { autoMaterialFromWallpaper(); });
}

window.NekoMaterialAuto = {
  fromImage: function (url) {
    return wallpaperStats(url).then(function (stats) {
      return { preset: pickPresetForStats(stats), stats: stats };
    });
  },
  fromStats: function (stats) { return pickPresetForStats(stats); },
  fromWallpaper: function () { return autoMaterialFromWallpaper(); },
  statsOfWallpaper: function () {
    var url = wallpaperUrl();
    return url ? wallpaperStats(url) : Promise.resolve(null);
  }
};

function wireMaterialPreset() {
  var sel = $('sel-material-preset');
  if (!sel) return;
  sel.addEventListener('change', function () {
    var id = String(sel.value || '');
    if (!id || id === 'custom') { syncMaterialPresetSelect(); return; }
    applyMaterialPreset(id);
  });
  syncMaterialPresetSelect();
}

var shortcutReturnFocus = null;

function activeEditorDialog() {
  return ['shortcut-sheet', 'compare-modal', 'trash-modal', 'modal'].map($).find(function (dialog) {
    return dialog && !dialog.hidden;
  });
}

function renderShortcutList() {
  var list = $('shortcut-list');
  if (!list) return;
  list.textContent = '';
  var shortcuts = [
  [["Ctrl","↵"], tsT('shortcuts.apply', 'Apply changes')],
  [["Ctrl","S"], tsT('shortcuts.save', 'Save changes')],
  [["Alt","Z"], tsT('shortcuts.undoApply', 'Undo the last application')],
  [["Alt","P"], tsT('shortcuts.preview', 'Start or stop preview')],
  [["Ctrl","Z"], tsT('shortcuts.undo', 'Undo a draft edit')],
  [["Ctrl","Shift","Z"], tsT('shortcuts.redo', 'Redo a draft edit')],
  [["/"], tsT('shortcuts.search', 'Search variables')],
  [["Ctrl","K"], tsT('shortcuts.library', 'Search the theme library')],
  [["Alt","↑ / ↓"], tsT('shortcuts.row', 'Move between variable rows')],
  [["Alt","C"], tsT('shortcuts.copy', 'Copy this row’s value')],
  [["Alt","V"], tsT('shortcuts.paste', 'Paste into this row')],
  [["Alt","Backspace"], tsT('shortcuts.clear', 'Reset this row')],
  [["↑","↓","←","→"], tsT('shortcuts.cards', 'Move between theme cards')],
  [["Enter"], tsT('shortcuts.use', 'Apply the selected theme')],
  [["F"], tsT('shortcuts.favorite', 'Toggle favorite')],
  [["S"], tsT('shortcuts.share', 'Export the selected theme')],
  [["J"], tsT('shortcuts.json', 'Copy theme JSON')],
  [["E"], tsT('shortcuts.edit', 'Edit theme details')],
  [["C"], tsT('shortcuts.compare', 'Compare themes')],
  [["Delete"], tsT('shortcuts.remove', 'Delete the selected theme')],
  [["?"], tsT('shortcuts.help', 'Toggle keyboard shortcuts')],
  [["Esc"], tsT('shortcuts.close', 'Close the dialog')]
  ];
  var mac = /Mac|iPad|iPhone/.test(navigator.platform || '');
  shortcuts.forEach(function (entry) {
    var row = document.createElement('li');
    var keys = document.createElement('span');
    keys.className = 'keys';
    entry[0].forEach(function (key) {
      var badge = document.createElement('kbd');
      badge.textContent = key === 'Ctrl' && mac ? '⌘' : key;
      keys.appendChild(badge);
    });
    var label = document.createElement('span');
    label.textContent = entry[1];
    row.appendChild(keys);
    row.appendChild(label);
    list.appendChild(row);
  });
}

function openShortcutSheet() {
  var sheet = $('shortcut-sheet');
  if (!sheet || activeEditorDialog()) return;
  renderShortcutList();
  shortcutReturnFocus = document.activeElement;
  sheet.hidden = false;
  $('shortcut-close').focus();
}

function closeShortcutSheet() {
  $('shortcut-sheet').hidden = true;
  var back = shortcutReturnFocus;
  shortcutReturnFocus = null;
  if (back && document.contains(back)) back.focus();
}

function wireShortcuts() {
  var sheet = $('shortcut-sheet');
  if (!sheet) return;
  $('btn-shortcuts').addEventListener('click', openShortcutSheet);
  $('shortcut-close').addEventListener('click', closeShortcutSheet);
  sheet.addEventListener('click', function (event) {
    if (event.target === sheet) closeShortcutSheet();
  });
  window.addEventListener('localechange', function () {
    if (!sheet.hidden) renderShortcutList();
  });
}

var VIEW_KEY = 'neko.themeStudio.view';

function readViewState() {
  try {
    return JSON.parse(window.localStorage.getItem(VIEW_KEY)) || {};
  } catch (error) { return {}; }
}

function writeViewState(patch) {
  try {
    var old = readViewState();
    window.localStorage.setItem(VIEW_KEY, JSON.stringify({
      mode: patch.mode === undefined ? old.mode : patch.mode,
      scroll: patch.scroll === undefined ? old.scroll : patch.scroll
    }));
  } catch (error) { /* Storage can be unavailable in private windows. */ }
}

// The scroller grows when the library loads. Wait for its first render before
// restoring an offset, and give user navigation precedence in the meantime.
function initEditorView() {
  var saved = readViewState();
  setMode(saved.mode === 'dark' ? 'dark' : 'light');
  var scroller = document.querySelector('.container-content');
  if (!scroller) return function () {};
  var target = Number(saved.scroll);
  var pending = Number.isFinite(target) && target > 0;
  var timer;
  var gestures = ['pointerdown', 'keydown', 'wheel', 'touchmove'];
  function finish() {
    pending = false;
    gestures.forEach(function (type) { document.removeEventListener(type, finish, true); });
  }
  function save() {
    if (!pending) writeViewState({ scroll: Math.round(scroller.scrollTop) });
  }
  if (pending) gestures.forEach(function (type) {
    document.addEventListener(type, finish, { capture: true, passive: true });
  });
  scroller.addEventListener('scroll', function () {
    if (pending || timer) return;
    timer = window.setTimeout(function () { timer = 0; save(); }, 250);
  }, { passive: true });
  window.addEventListener('pagehide', save);
  return function () {
    if (!pending) return;
    window.requestAnimationFrame(function () {
      if (!pending) return;
      scroller.scrollTop = target;
      finish();
    });
  };
}

function wireEvents() {
  $('mode-light').addEventListener('click', function () { setMode('light'); });
  $('mode-dark').addEventListener('click', function () { setMode('dark'); });
  // Tab-style controls switch with Left/Right arrow keys by convention
  ['mode-light', 'mode-dark'].forEach(function (id) {
    $(id).addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      var next = event.key === 'ArrowLeft' ? 'light' : 'dark';
      setMode(next);
      $(next === 'light' ? 'mode-light' : 'mode-dark').focus();
    });
  });

  $('btn-apply').addEventListener('click', applyTheme);
  $('btn-preview').addEventListener('click', togglePreview);
  $('btn-preview-accept').addEventListener('click', acceptPreview);
  $('btn-undo-apply').addEventListener('click', undoLastApply);
  $('btn-share-card').addEventListener('click', exportShareCard);
  document.addEventListener('nts:palette-request', function () { syncPreviewPalette(true); });
  window.addEventListener('pagehide', saveDraftNow);
  window.addEventListener('localechange', function () {
    updateButtons();
    refreshDarkFlags();
    libraryCardCache = Object.create(null);
    if (libraryState.items.length) renderLibrary();
    if (state.status) renderHealth(state.status);
    var compare = $('compare-modal');
    if (compare && !compare.hidden) {
      ['compare-a', 'compare-b'].forEach(function (id) { fillCompareSelect($(id), $(id).value); });
      refreshCompare();
    }
  });
  wireCompare();
  wireDarkBulk();
  if ($('only-dark-diff')) $('only-dark-diff').addEventListener('change', function () {setOnlyDarkDiff(this.checked);});
  if ($('lib-sort')) {
    libRenderSortSelect();
    $('lib-sort').addEventListener('change', function () {libSetSortMode(this.value); renderLibrary();});
  }

  if ($('btn-look-clear')) wireLookLevels();
  wireMaterialPreset();

  if ($('btn-auto-material')) wireAutoMaterial();
  if ($('btn-auto-palette')) wireAutoPalette();
  if ($('palette-preset')) wirePaletteScheme();

  document.addEventListener('nts:materials-change', function () {
    updateButtons();

    syncGlassButtons();
    syncGrainButtons();
    syncBorderButtons();
    syncPerfButtons();
    syncMaterialPresetSelect();
    if ($('btn-look-clear')) syncLookButtons();
  });

  var GLASS_LEVELS = [
    { id: 'btn-glass-thin', value: 0.42 },
    { id: 'btn-glass-std', value: 0.58 },
    { id: 'btn-glass-thick', value: 0.72 }
  ];
  function syncGlassButtons() {
    var api = window.NekoMaterials;
    var snap = api && typeof api.snapshot === 'function' ? api.snapshot() : null;
    var current = snap && snap.global ? Number(snap.global.opacity) : null;
    GLASS_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var on = current !== null && Math.abs(current - level.value) < 0.005;
      node.setAttribute('aria-pressed', on ? 'true' : 'false');
      node.classList.toggle('is-active', on);
    });
  }
  GLASS_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      var api = window.NekoMaterials;
      if (!api || typeof api.patch !== 'function') { showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.')); return; }
      api.patch([{ key: 'opacity', value: level.value }], 'both');
      syncGlassButtons();
      updateButtons();
      showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: node.textContent }), 'ok');
    });
  });

  function syncPerfHint() {
    var node = $('perf-hint');
    if (!node) return;
    var mode = typeof readHeavyPref === 'function' ? readHeavyPref() : 'auto';
    if (mode !== 'auto' && mode !== 'on' && mode !== 'off') mode = 'auto';
    node.textContent = mode === 'on' ? tsT('glass.onHint', 'Chat effects are always simplified.') :
      mode === 'off' ? tsT('glass.offHint', 'All effects stay active in long chats and may slow scrolling.') :
      tsT('glass.autoHint', 'Simplifies chat effects after 40 messages.');
    node.classList.toggle('is-warn', mode === 'off');
  }

  var PERF_LEVELS = [
    { id: 'btn-perf-saver', key: 'saver' },
    { id: 'btn-perf-balanced', key: 'balanced' },
    { id: 'btn-perf-high', key: 'high' },
    { id: 'btn-perf-ultra', key: 'ultra' }
  ];
  function currentQuality() {
    var api = window.NekoMaterials;
    if (api && typeof api.getQuality === 'function') {
      var q = api.getQuality();
      if (q) return String(q);
    }
    var sel = $('mt-quality');
    return sel && sel.value ? String(sel.value) : 'balanced';
  }
  function syncPerfButtons() {
    var now = currentQuality();
    PERF_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var active = level.key === now;
      node.setAttribute('aria-pressed', active ? 'true' : 'false');
      node.classList.toggle('is-active', active);
    });
  }
  PERF_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      var api = window.NekoMaterials;
      if (!api || typeof api.setQuality !== 'function') { showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.')); return; }
      if (!api.setQuality(level.key)) { showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.')); return; }
      syncPerfButtons();
      updateButtons();
      showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: node.textContent }), 'ok');
    });
  });

  var GRAIN_LEVELS = [
    { id: 'btn-grain-off', values: { noise: false } },
    { id: 'btn-grain-soft', values: { noise: true, noiseStrength: 0.45, noiseAlpha: 0.35, noiseSize: 2, noiseDensity: 72 } },
    { id: 'btn-grain-strong', values: { noise: true, noiseStrength: 0.85, noiseAlpha: 0.5, noiseSize: 3, noiseDensity: 58 } }
  ];
  function syncGrainButtons() {
    var api = window.NekoMaterials;
    var snap = api && typeof api.snapshot === 'function' ? api.snapshot() : null;
    var surf = snap && snap.surface ? snap.surface : null;
    var on = !!(surf && surf.noise);
    var strength = surf ? Number(surf.noiseStrength) : 0;
    GRAIN_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var active;
      if (level.values.noise === false) active = !on;
      else if (!on) active = false;
      else if (level.values.noiseStrength >= 0.7) active = strength >= 0.65;
      else active = strength < 0.65;
      node.setAttribute('aria-pressed', active ? 'true' : 'false');
      node.classList.toggle('is-active', active);
    });
  }
  GRAIN_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      var api = window.NekoMaterials;
      if (!api || typeof api.patch !== 'function') { showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.')); return; }
      var entries = Object.keys(level.values).map(function (key) {
        return { key: key, value: level.values[key] };
      });
      api.patch(entries, 'both');
      syncGrainButtons();
      updateButtons();
      showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: node.textContent }), 'ok');
    });
  });

  var APPLE_RECIPE = {
    params: [
      { key: 'opacity', value: 0.58 },
      { key: 'borderAlpha', value: 0.42 },
      { key: 'noise', value: true },
      { key: 'noiseStrength', value: 0.45 },
      { key: 'noiseAlpha', value: 0.35 },
      { key: 'noiseSize', value: 2 },
      { key: 'noiseDensity', value: 72 }
    ],
    preset: 'liquid'
  };
  (function wireAppleRecipe() {
    var node = $('btn-recipe-apple');
    if (!node) return;
    node.addEventListener('click', function () {
      var api = window.NekoMaterials;
      if (!api || typeof api.applyPreset !== 'function') {
        showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.'));
        return;
      }
      api.applyPreset(APPLE_RECIPE.preset);
      if (typeof api.patch === 'function') api.patch(APPLE_RECIPE.params, 'both');

      try { window.localStorage.setItem('neko.themeStudio.plate', '0'); } catch (error) { /* Optional in restricted browser contexts. */ }
      if (window.NekoThemeStudio && typeof window.NekoThemeStudio.setPlate === 'function') {
        window.NekoThemeStudio.setPlate('0');
      }
      try { syncGlassButtons(); syncGrainButtons(); syncBorderButtons(); syncPlateButtons(); syncRimButtons(); } catch (error) { /* Optional in restricted browser contexts. */ }
      updateButtons();
      showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: node.textContent }), 'ok');
    });
  })();

  var BORDER_LEVELS = [
    { id: 'btn-border-weak', value: 0.22 },
    { id: 'btn-border-std', value: 0.42 },
    { id: 'btn-border-strong', value: 0.62 }
  ];
  function syncBorderButtons() {
    var api = window.NekoMaterials;
    var snap = api && typeof api.snapshot === 'function' ? api.snapshot() : null;
    var surf = snap && snap.surface ? snap.surface : null;
    var current = surf ? Number(surf.borderAlpha) : null;
    BORDER_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var on = current !== null && isFinite(current) && Math.abs(current - level.value) < 0.005;
      node.setAttribute('aria-pressed', on ? 'true' : 'false');
      node.classList.toggle('is-active', on);
    });
  }
  BORDER_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      var api = window.NekoMaterials;
      if (!api || typeof api.patch !== 'function') { showNotice('warn', tsT('glass.unavailable', 'The material editor is still loading.')); return; }
      api.patch([{ key: 'borderAlpha', value: level.value }], 'both');
      syncBorderButtons();
      updateButtons();
      showToast(tsT('glass.draftSetting', '{{value}} selected. Click Apply to save.', { value: node.textContent }), 'ok');
    });
  });

  var PLATE_KEY = 'neko.themeStudio.plate';
  var PLATE_LEVELS = [
    { id: 'btn-plate-hide', value: '0' },
    { id: 'btn-plate-light', value: '1' },
    { id: 'btn-plate-std', value: '2' }
  ];
  function readPlate() {
    try {
      var raw = window.localStorage.getItem(PLATE_KEY);
      return raw === '1' || raw === '2' ? raw : '0';
    } catch (error) { return '0'; }
  }
  function syncPlateButtons() {
    var current = readPlate();
    PLATE_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var on = current === level.value;
      node.setAttribute('aria-pressed', on ? 'true' : 'false');
      node.classList.toggle('is-active', on);
    });
  }
  PLATE_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      try { window.localStorage.setItem(PLATE_KEY, level.value); } catch (error) { /* Optional in restricted browser contexts. */ }
      if (window.NekoThemeStudio && typeof window.NekoThemeStudio.setPlate === 'function') {
        window.NekoThemeStudio.setPlate(level.value);
      }
      syncPlateButtons();
      showToast(tsT('glass.setting', '{{value}} selected.', { value: node.textContent }), 'ok');
    });
  });

  var RIM_LEVELS = [
    { id: 'btn-rim-off', value: '0' },
    { id: 'btn-rim-std', value: '1' },
    { id: 'btn-rim-strong', value: '2' }
  ];
  function syncRimButtons() {
    var current = readRimPref();
    RIM_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var on = current === level.value;
      node.setAttribute('aria-pressed', on ? 'true' : 'false');
      node.classList.toggle('is-active', on);
    });
  }
  RIM_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      setRimPref(level.value);
      showToast(tsT('glass.setting', '{{value}} selected.', { value: node.textContent }), 'ok');
    });
  });

  document.addEventListener('nts:rim-change', syncRimButtons);
  syncRimButtons();

  var CHROMA_LEVELS = [
    { id: 'btn-chroma-off', value: '0' },
    { id: 'btn-chroma-std', value: '1' },
    { id: 'btn-chroma-strong', value: '2' }
  ];
  function syncChromaButtons() {
    var current = readChromaPref();
    CHROMA_LEVELS.forEach(function (level) {
      var node = $(level.id);
      if (!node) return;
      var on = current === level.value;
      node.setAttribute('aria-pressed', on ? 'true' : 'false');
      node.classList.toggle('is-active', on);
    });
  }
  CHROMA_LEVELS.forEach(function (level) {
    var node = $(level.id);
    if (!node) return;
    node.addEventListener('click', function () {
      setChromaPref(level.value);
      showToast(tsT('glass.setting', '{{value}} selected.', { value: node.textContent }), 'ok');
    });
  });
  document.addEventListener('nts:chroma-change', syncChromaButtons);
  syncChromaButtons();
  syncPlateButtons();
  syncGrainButtons();
  syncPerfButtons();

  window.addEventListener('localechange', syncPerfHint);
  syncPerfHint();
  document.addEventListener('nts:heavy-change', syncPerfHint);
  wireHeavyButtons();
  syncGlassButtons();

  $('btn-reset').addEventListener('click', resetTheme);
  $('btn-export').addEventListener('click', exportTheme);
  $('btn-import').addEventListener('click', openImport);
  $('btn-reload').addEventListener('click', function () {
    // "Reload" discards unapplied edits: count them and ask first, so one misclick does
    // not throw away half an hour of tuning (resetTheme already confirmed; this did not).
    var pending = pendingChangeCount();
    if (pending && !window.confirm(tsT('sync.reloadConfirm', '面板里有 {{count}} 项还没应用的改动。\n'
      + '「重新读取」会丢弃它们（不可恢复）。继续？', { count: pending }))) return;
    reloadPalette({ discardEdits: true }).then(function (palette) {
      if (palette && pending) {
        showNotice('warn', tsT('sync.reloadDiscarded', '已重新读取，丢弃了 {{count}} 项未应用的改动。', { count: pending }));
      }
    });
  });
  $('btn-revert-mode').addEventListener('click', revertMode);

  var search = $('row-search');
  if (search) {
    search.addEventListener('input', function () { setFilter(search.value); });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        clearFilter();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        var first = firstVisibleRow();
        if (first) first.focus();
      }
    });
  }
  if ($('search-clear')) $('search-clear').addEventListener('click', clearFilter);
  if ($('only-dirty')) {
    $('only-dirty').addEventListener('change', function () { setOnlyDirty($('only-dirty').checked); });
  }
  if ($('btn-toggle-groups')) $('btn-toggle-groups').addEventListener('click', toggleAllGroups);
  if ($('btn-toggle-dump')) $('btn-toggle-dump').addEventListener('click', toggleDumpCard);
  if ($('btn-copy-dump')) $('btn-copy-dump').addEventListener('click', copyDump);
  if ($('btn-css-snippet')) $('btn-css-snippet').addEventListener('click', copyCssSnippet);
  if ($('btn-revert-all')) $('btn-revert-all').addEventListener('click', revertAllChanges);
  initWallpaperFeature();
  initQuickPresets();
  if ($('btn-undo')) $('btn-undo').addEventListener('click', undoStep);
  if ($('btn-redo')) $('btn-redo').addEventListener('click', redoStep);
  if ($('btn-ai-prompt')) $('btn-ai-prompt').addEventListener('click', copyAiPrompt);
  initUndoShortcuts();
  if ($('btn-audit')) $('btn-audit').addEventListener('click', function () { renderAudit(false); });
  if ($('btn-audit-fix')) $('btn-audit-fix').addEventListener('click', function () { fixAllAudit(); });
  if ($('notice-close')) $('notice-close').addEventListener('click', hideNotice);

  // Theme library
  if ($('btn-lib-save')) $('btn-lib-save').addEventListener('click', saveLibraryTheme);
  if ($('btn-lib-shuffle')) $('btn-lib-shuffle').addEventListener('click', function () {
    shuffleLibraryTheme();
  });
  if ($('btn-lib-refresh')) $('btn-lib-refresh').addEventListener('click', function () {
    refreshLibrary().then(function (items) {
      if (items) showToast(tsT('misc.libraryCount', '仓库共 {{count}} 个主题', { count: items.length }));
    });
  });
  if ($('btn-lib-import')) $('btn-lib-import').addEventListener('click', pickArchiveFile);
  if ($('btn-lib-backup')) $('btn-lib-backup').addEventListener('click', function () { backupLibrary(this); });
  if ($('btn-lib-restore')) $('btn-lib-restore').addEventListener('click', pickLibraryBackupFile);
  if ($('lib-backup-file')) {
    $('lib-backup-file').addEventListener('change', function (event) {
      var input = event.target;
      handleLibraryBackupFile(input.files && input.files[0], input);
    });
  }
  // Scheduled theme switching
  if ($('btn-schedule-save')) $('btn-schedule-save').addEventListener('click', saveSchedule);
  if ($('schedule-weekend')) {
    $('schedule-weekend').addEventListener('change', function () {
      this.dataset.dirty = '1';
      markScheduleDirty();
      scheduleState.weekendEnabled = !!this.checked;
      renderSchedule();
    });
  }
  if ($('schedule-enabled')) {
    $('schedule-enabled').addEventListener('change', function () {
      // The enable switch itself is an unsaved choice too: only the backend save_schedule writes it to disk.
      this.dataset.dirty = '1';
      markScheduleDirty();
    });
  }
  if ($('lib-file')) {
    $('lib-file').addEventListener('change', function (event) {
      var input = event.target;
      handleArchiveFile(input.files && input.files[0], input);
    });
  }
  // Enter in the name box = save (a textarea inserts a newline by default, so intercept it explicitly)
  if ($('lib-name')) {
    $('lib-name').addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        saveLibraryTheme();
      }
    });
  }

  // Library search box: filters as you type, combined with family tabs / tag filters; Esc clears, Enter jumps to the first card.
  if ($('lib-search')) {
    var libSearch = $('lib-search');
    libSearch.addEventListener('input', function (event) {
      libraryState.query = String(event.target.value || '').trim();
      renderLibrary();
    });
    libSearch.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        event.target.value = '';
        libraryState.query = '';
        renderLibrary();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        var libList = $('lib-list');
        var firstFav = libList && libList.querySelector ? libList.querySelector('.lib-fav') : null;
        if (firstFav) firstFav.focus();
      }
    });
  }

  // Status card
  if ($('btn-health-refresh')) $('btn-health-refresh').addEventListener('click', function () {
    refreshHealth({ toast: true });
  });
  if ($('btn-status-copy')) $('btn-status-copy').addEventListener('click', copyStatusPath);
  if ($('btn-help-open')) $('btn-help-open').addEventListener('click', openHelpModal);

  $('theme-enabled').addEventListener('change', function () { renderDump(); updateButtons(); });
  // Do not use renderDump directly as the listener: its first parameter is `force` and an
  // InputEvent is truthy, so every keystroke would rebuild ~10KB of JSON even while
  // collapsed (undoing the v0.4.27 laziness).
  if ($('theme-name')) {
    $('theme-name').addEventListener('input', function () {
      renderDump();
      updateButtons();
    });
  }

  $('modal-close').addEventListener('click', closeModal);
  $('modal-cancel').addEventListener('click', closeModal);
  $('modal-ok').addEventListener('click', onModalOk);
  $('modal').addEventListener('click', function (event) {
    if (event.target === $('modal')) closeModal();
  });

  wireShortcuts();
  document.addEventListener('keydown', function (event) {
    if (event.defaultPrevented || event.isComposing) return;
    var dialog = activeEditorDialog();
    var key = String(event.key).toLowerCase();
    var meta = event.ctrlKey || event.metaKey;
    if (dialog) {
      if (dialog.id === 'shortcut-sheet') {
        if (key === 'escape' || key === '?') {
          event.preventDefault();
          closeShortcutSheet();
        } else if (key === 'tab') {
          event.preventDefault();
          $('shortcut-close').focus();
        }
      } else if (dialog.id === 'modal') {
        if (key === 'escape') closeModal();
        if (key === 'tab') trapModalFocus(event);
      }
      return;
    }
    if (meta && !event.altKey && !event.shiftKey) {
      if (key === 's' || key === 'enter' || key === 'numpadenter') {
        event.preventDefault();
        if (!event.repeat && !state.busy && (key !== 's' || pendingChangeCount() > 0)) applyTheme();
      } else if (key === 'k') {
        event.preventDefault();
        var field = $('lib-search');
        if (field) { field.focus(); field.select(); }
      }
      return;
    }
    if (meta || isTypingTarget(event.target)) return;
    if (event.altKey) {
      if (event.shiftKey || (key !== 'p' && key !== 'z')) return;
      event.preventDefault();
      if (event.repeat || state.busy) return;
      if (key === 'p') togglePreview();
      else if (lastApplyUndo) undoLastApply();
      return;
    }
    if (key === '/' && search) {
      event.preventDefault();
      search.focus();
    } else if (key === '?' && !event.repeat) {
      event.preventDefault();
      openShortcutSheet();
    }
  });
}

function setMode(mode) {
  if (state.mode !== mode) writeViewState({mode: mode});
  state.mode = mode;
  $('mode-light').classList.toggle('is-active', mode === 'light');
  $('mode-dark').classList.toggle('is-active', mode === 'dark');
  $('mode-light').setAttribute('aria-selected', mode === 'light' ? 'true' : 'false');
  $('mode-dark').setAttribute('aria-selected', mode === 'dark' ? 'true' : 'false');
  // Thumbnail swatches follow the current edit mode (one preview snapshot each for light/dark).
  if (libraryState.items.length) renderLibrary();
  syncAll();
}

/* 11g. Keyboard focus recovery (v0.15.72)
 * Symptom: in pet/floating mode the panel iframe receives mouse events (hover, drag-select
 * and button clicks work) but no keyboard events at all: Tab shows no focus ring and
 * clicking an input shows no caret. The panel does not steal focus (it has no focus-grabbing
 * mousedown/blur code); the host keeps keyboard focus on another window/document. The panel
 * can only (1) ask the parent window for focus when an input is clicked, retrying a few
 * times, and (2) if that still fails, show an actionable hint so it does not look broken.
 */
function initFocusRecovery() {
  var hinted = false;
  function claimFocus(el) {
    try { if (window.top && window.top !== window) window.top.focus(); } catch (e1) { /* cross-origin top: ignore */ }
    try { window.focus(); } catch (e2) { /* ignore */ }
    if (el && typeof el.focus === 'function') {
      try { el.focus({ preventScroll: true }); } catch (e3) { try { el.focus(); } catch (e4) { /* ignore */ } }
    }
    [0, 60, 220].forEach(function (delay) {
      setTimeout(function () {
        if (!document.hasFocus()) return;
        if (el && document.activeElement !== el) {
          try { el.focus({ preventScroll: true }); } catch (e5) { /* ignore */ }
        }
      }, delay);
    });
  }
  document.addEventListener('mousedown', function (event) {
    var node = event.target;
    var el = node && node.closest ? node.closest('input, textarea') : null;
    if (!el || el.disabled || el.readOnly) return;
    if (document.hasFocus()) return;
    claimFocus(el);
    setTimeout(function () {
      if (document.hasFocus() || hinted) return;
      hinted = true;
      setStatus(tsT('guide.focusHint', '键盘焦点被 N.E.K.O 主窗口占着：先点一下主窗口（比如聊天输入框），再回来就能输入'), 'warn');
    }, 400);
  }, true);
}
function bootstrap() {
  // Double-boot guard (v0.4.27): if the panel is injected twice, wireEvents binds twice
  // and external sync / schedule polling start twice, showing up as "one click runs
  // twice" and "notices pop up twice".
  if (window.__NTS_PANEL_BOOTED__) return;
  window.__NTS_PANEL_BOOTED__ = true;
  // Favorites and group collapse state from localStorage: read failures / private mode fall back to defaults without blocking startup.
  libraryState.favorites = libReadIdList(libraryStorage(), LIB_FAV_KEY);
  loadCatalogCollapsed();
  // Collapse state of the current-palette JSON card (v0.4.21): remember it and skip rebuilds while collapsed.
  dumpState.collapsed = loadDumpCollapsed();
  // These two controls only get translated text from script, so set it once at
  // startup instead of showing the template's Chinese until the first change.
  applyDumpCollapsed();
  updateScheduleDirtyHint();
  buildEditor();
  wireEvents();
  initRestartHint();
  // Drag-and-drop import (v0.5.0): bound on body, so dropping anywhere on the panel works
  initDropImport();
  initFocusRecovery();
  var restoreView = initEditorView();
  // Render from local state first so the panel never shows blank; get_theme corrects it afterwards.
  syncAll();
  applyFilters();
  reloadPalette({});
  // The library is fetched separately (it reads themes/, not theme.json). A failure only
  // leaves the list empty without affecting the editor: refreshLibrary swallows errors into #lib-empty.
  Promise.resolve(refreshLibrary()).then(restoreView, restoreView);
  // Readability audit: pre-scan the current state so a broken palette shows right away.
  renderAudit(true);
  // Scheduled switching: the dropdown options depend on the library list.
  refreshSchedule();
  // Status card: show data directory, current theme and loader reports right away.
  // It is the only place users can self-diagnose "installed but no effect".
  refreshHealth({});
  // External change detection: start after the initial requests settle so they do not compete.
  setTimeout(function () { if (document.visibilityState !== 'hidden') startExternalSync(); }, EXTERNAL_SYNC_START_DELAY_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      startExternalSync();
      checkExternalChange();
    } else {
      stopExternalSync();
      saveDraftNow();
    }
  });
}

// Runtime strings are resolved while the editor is built, so wait for the
// language bundle first (bounded; see whenI18nReady).
function bootstrapWhenReady() {
  whenI18nReady(function () {
    localizeCatalog();
    bootstrap();
  }, refreshGeneratedTexts);
}

/**
 * The language bundle arrived after the panel was already built with the
 * fallback strings: translate the catalog and rebuild the generated UI from the
 * current state (drafts, filters and collapsed groups live in `state`, so no
 * edit is lost). Static markup is handled by the host's updatePageTexts().
 */
function refreshGeneratedTexts() {
  // Rebuilding the editor replaces its inputs. Text that exists only in an input
  // (unfinished typing in the focused field, or invalid text whose last valid
  // value is what state.edited holds) would be lost, so wait until neither is
  // the case. Everything else lives in `state` and survives the rebuild.
  var groups = $('groups');
  var typing = groups && document.activeElement && groups.contains(document.activeElement);
  if (typing || invalidRowCount()) {
    setTimeout(refreshGeneratedTexts, 1000);
    return;
  }
  try {
    localizeCatalog();
    buildEditor();
    syncAll();
    applyFilters();
    renderMeta();
    renderAudit(true);
    refreshLibrary();
    refreshSchedule();
    refreshHealth({});
    applyDumpCollapsed();
    updateScheduleDirtyHint();
  } catch (error) {
    // A partial refresh must never break the panel; the next reopen is fully translated.
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrapWhenReady);
} else {
  bootstrapWhenReady();
  // Check the version again once the panel is up: after an upgrade, a stale page (with an old app.js) must retire itself.
  setTimeout(checkPanelFreshness, PANEL_FRESHNESS_DELAY_MS);
}

/* The material center (materials.js) is a separate script and can only reach entry calls
 * and toast through window. Also attached only when missing, so a re-executed script does
 * not overwrite existing references with dead functions. */
/* The material center is a separate file (materials.js). If it 404s or has a syntax error,
   #materials-root stays empty while the rest works, and users just think the materials
   page is broken. Fall back to a diagnosable message. */
setTimeout(function () {
  try {
    var box = document.getElementById('materials-root');
    if (!box || box.firstChild || window.NekoMaterials) return;
    box.textContent = tsT('misc.materialsLoadFailed', '材质中心没能加载（materials.js 未就绪），刷新面板可以重试。');
  } catch (e) { /* a failing hint must not affect the panel */ }
}, 3000);

window.NekoPanel = window.NekoPanel || {
  call: function (entryId, args) { return callPlugin(entryId, args); },
  toast: function (message, kind) { showToast(message, kind); },
  notice: showNotice,
  clearNotice: function () {
    var button = $('notice-action');
    if (button) { button.hidden = true; button.onclick = null; button.textContent = ''; }
  }
};

/* Automation/debug hooks: the panel has no module system, so test scripts reach internals via window.
 * Attached only when missing; never overwrites an existing object. */
window.__ntsPanel = window.__ntsPanel || {
  copyLightIntoDark: copyLightIntoDark,
  clearAllDarkOverrides: clearAllDarkOverrides,
  togglePreview: togglePreview,
  acceptPreview: acceptPreview,
  previewRemaining: previewRemaining,
  previewActive: previewActive,
  libRecentMaybeRecord: libRecentMaybeRecord,
  checkPanelFreshness: checkPanelFreshness,
  panelVersionOf: panelVersionOf,
  hoverPreview: hoverPreview,
  showHoverPreview: showHoverPreview,
  clearHoverPreview: clearHoverPreview,
  rewritePreviewRefs: rewritePreviewRefs,
  dumpState: dumpState,
  dumpLazyPlan: dumpLazyPlan,
  cssSnippetText: cssSnippetText,
  copyCssSnippet: copyCssSnippet,
  extractImageColors: extractImageColors,
  wallpaperPalette: wallpaperPalette,
  shiftColorValue: shiftColorValue,
  buildVariantPalette: buildVariantPalette,
  applyWallpaperPalette: applyWallpaperPalette,
  handleWallpaperFile: handleWallpaperFile,
  applyNextVariant: applyNextVariant,
  variantState: variantState,
  undoState: undoState,
  undoStep: undoStep,
  redoStep: redoStep,
  updateUndoButtons: updateUndoButtons,
  copyRowValue: copyRowValue,
  pasteRowValue: pasteRowValue,
  comparePairs: comparePairs,
  renderCompare: renderCompare,
  buildAiPrompt: buildAiPrompt,
  copyAiPrompt: copyAiPrompt,
  GLOW_LEVELS: GLOW_LEVELS,
  GLOW_LEVEL_KEYS: GLOW_LEVEL_KEYS,
  FONT_PRESETS: FONT_PRESETS,
  quickState: quickState,
  currentGlowLevel: currentGlowLevel,
  currentFontPreset: currentFontPreset,
  applyGlowLevel: applyGlowLevel,
  applyFontPreset: applyFontPreset,
  renderQuickPresets: renderQuickPresets,
  pendingChangeList: pendingChangeList,
  changeEntryList: changeEntryList,
  renderChanges: renderChanges,
  revertChange: revertChange,
  revertAllChanges: revertAllChanges,
  initDropImport: initDropImport,
  toggleDumpCard: toggleDumpCard,
  applyDumpCollapsed: applyDumpCollapsed,
  // v0.4.17: observation point for coalesced editor refreshes (scheduled = requests, flushes = actual refreshes)
  editorSyncState: editorSyncState,
  // v0.4.26: name / enable switch count as dirty too; self-test scripts measure this directly
  metaDirtyCount: metaDirtyCount,
  pendingChangeCount: pendingChangeCount,
  // v0.4.18: theme info editing / first-use hint (for self-test scripts)
  openMetaModal: openMetaModal,
  saveThemeMeta: saveThemeMeta,
  parseTagInput: parseTagInput,
  initRestartHint: initRestartHint,
  RESTART_HINT_KEY: RESTART_HINT_KEY,
  editorSyncPlan: editorSyncPlan,
  flushEditorGlobals: flushEditorGlobals,
  scheduleEditorGlobals: scheduleEditorGlobals,
  backupLibrary: backupLibrary,
  restoreLibrary: restoreLibrary,
  handleLibraryBackupFile: handleLibraryBackupFile,
  state: state,
  callPlugin: callPlugin,
  reloadPalette: reloadPalette,
  refreshLibrary: refreshLibrary,
  saveLibraryTheme: saveLibraryTheme,
  applyLibraryItem: applyLibraryItem,
  exportLibraryItem: exportLibraryItem,
  deleteLibraryItem: deleteLibraryItem,
  importArchive: importArchive,
  effectiveVars: effectiveVars,
  collect: collect,
  syncAll: syncAll,
  doImport: doImport,
  rowsByKey: rowsByKey,
  refreshHealth: refreshHealth,
  renderHealth: renderHealth,
  copyStatusPath: copyStatusPath,
  openHelpModal: openHelpModal,
  // v0.2.0 panel UX: family tabs / search / favorites / group collapse (for self-test scripts)
  libraryState: libraryState,
  LIB_FAMILIES: LIB_FAMILIES,
  LIB_FAV_FAMILY: LIB_FAV_FAMILY,
  LIB_FAV_KEY: LIB_FAV_KEY,
  LIB_COLLAPSE_KEY: LIB_COLLAPSE_KEY,
  libFamilyOf: libFamilyOf,
  libSelectLibrary: libSelectLibrary,
  libFamilyCounts: libFamilyCounts,
  libPruneFavorites: libPruneFavorites,
  libReadIdList: libReadIdList,
  libWriteIdList: libWriteIdList,
  libReadCollapsed: libReadCollapsed,
  libWriteCollapsed: libWriteCollapsed,
  toggleLibraryFavorite: toggleLibraryFavorite,
  renderLibrary: renderLibrary,
  // v0.4.13 scheduled theme switching (for self-test scripts)
  scheduleState: scheduleState,
  SCHEDULE_ZONE_LABELS: SCHEDULE_ZONE_LABELS,
  refreshSchedule: refreshSchedule,
  saveSchedule: saveSchedule,
  renderSchedule: renderSchedule,
  scheduleWindowText: scheduleWindowText,
  scheduleRefsFor: scheduleRefsFor,
  scheduleStaleText: scheduleStaleText,
  scheduleThemeGroups: scheduleThemeGroups,
  // External change detection (v0.4.14)
  externalSyncState: externalSyncState,
  paletteSignature: paletteSignature,
  externalSyncDecision: externalSyncDecision,
  checkExternalChange: checkExternalChange,
  startExternalSync: startExternalSync,
  stopExternalSync: stopExternalSync,
  loadCatalogCollapsed: loadCatalogCollapsed,
  saveCatalogCollapsed: saveCatalogCollapsed
};
