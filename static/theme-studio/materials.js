/* 高级材质中心 —— 面板侧交互层。
   数据流：本文件只负责「编辑 materials 子树 + 调后端编译预览」，真正的落盘
   由 app.js 的 applyTheme 提交给 apply_theme，编译变量由后端 compile_materials
   产出，前端不重写一份公式，避免预览与应用窗口出现偏差。
   对外接口挂在 window.NekoMaterials：
     mount(root)     渲染外壳（index.html 里 #materials-root）
     sync(palette)   get_theme 返回后同步当前/基线并重绘
     snapshot()      applyTheme 取当前材质设置
     isDirty()       是否有未应用的材质改动
     resetLocal()    丢弃本地改动回到基线
*/
(function () {
  'use strict';

  var HAS = Object.prototype.hasOwnProperty;
  var NL = String.fromCharCode(10);

  var SPEC = null;          // material_spec 结果
  var PARAMS = {};          // key -> 参数定义
  var SECTION_OF = {};      // key -> section id
  var CURRENT = null;       // 可编辑副本
  var BASELINE = null;      // 已保存副本（脏判定基准）
  var MODE = 'light';       // 亮色 / 暗色编辑
  var BG_MODE = 'dark';     // 预览壁纸深浅：dark 深色壁纸 / light 浅色壁纸
  var BG_TOUCHED = false;   // 用户手动切过壁纸后，不再跟随编辑模式自动变
  var TAB = 'overview';
  var DIRTY = false;
  var MOUNTED = false;
  // 主题总开关（调色板的 enabled）。关掉时加载器会 removeStyle，材质随之不生效。
  var MASTER_ON = true;
  var PREVIEW_TIMER = null;
  var PREVIEW_SEQ = 0;
  var PREVIEW_VARS = null;

  /* ---------------- 通用工具 ---------------- */

  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'text') { node.textContent = String(value); return; }
        if (key === 'class') { node.className = String(value); return; }
        if (key.indexOf('on:') === 0 && typeof value === 'function') {
          node.addEventListener(key.slice(3), value);
          return;
        }
        node.setAttribute(key, value === true ? '' : String(value));
      });
    }
    for (var i = 2; i < arguments.length; i++) {
      var kid = arguments[i];
      if (kid === null || kid === undefined || kid === false) continue;
      if (Array.isArray(kid)) {
        kid.forEach(function (child) { if (child) node.appendChild(child); });
      } else if (typeof kid === 'string') {
        node.appendChild(document.createTextNode(kid));
      } else {
        node.appendChild(kid);
      }
    }
    return node;
  }

  function byId(id) { return document.getElementById(id); }

  function clone(value) {
    if (value === null || value === undefined) return value;
    return JSON.parse(JSON.stringify(value));
  }

  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function num(value, fallback) {
    var n = typeof value === 'number' ? value : parseFloat(String(value));
    return isFinite(n) ? n : fallback;
  }

  function clamp(n, lo, hi) {
    if (!isFinite(lo)) lo = n;
    if (!isFinite(hi)) hi = n;
    return Math.min(hi, Math.max(lo, n));
  }

  function toast(message, kind) {
    if (window.NekoPanel && window.NekoPanel.toast) window.NekoPanel.toast(message, kind);
  }

  function callEntry(entryId, args) {
    if (window.NekoPanel && window.NekoPanel.call) return window.NekoPanel.call(entryId, args);
    return Promise.reject(new Error('面板接口尚未就绪'));
  }

  function hexOf(value) {
    var text = String(value || '').trim();
    var short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(text);
    if (short) return ('#' + short[1] + short[1] + short[2] + short[2] + short[3] + short[3]).toLowerCase();
    if (/^#[0-9a-f]{6}$/i.test(text)) return text.toLowerCase();
    var rgb = /rgba?\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})/i.exec(text);
    if (rgb) {
      var parts = [rgb[1], rgb[2], rgb[3]].map(function (v) {
        var n = clamp(parseInt(v, 10), 0, 255);
        return (n < 16 ? '0' : '') + n.toString(16);
      });
      return ('#' + parts.join('')).toLowerCase();
    }
    return '#ffffff';
  }

  function deepDefaults() {
    var out = { version: 1, enabled: false, preset: 'custom', quality: 'balanced',
      dark: {}, components: {}, custom_presets: {} };
    ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
      out[section] = {};
    });
    Object.keys(PARAMS).forEach(function (key) {
      var param = PARAMS[key];
      if (!out[param.section]) out[param.section] = {};
      out[param.section][key] = param.default;
    });
    return out;
  }

  function ensureSections(materials) {
    var out = materials || {};
    ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
      if (!out[section] || typeof out[section] !== 'object') out[section] = {};
    });
    if (!out.dark || typeof out.dark !== 'object') out.dark = {};
    if (!out.components || typeof out.components !== 'object') out.components = {};
    if (!out.custom_presets || typeof out.custom_presets !== 'object') out.custom_presets = {};
    return out;
  }

  /* ---------------- 值读写 ---------------- */

  function paramOf(key) { return PARAMS[key] || null; }

  function lightValue(sectionId, key) {
    var section = CURRENT && CURRENT[sectionId];
    if (section && HAS.call(section, key)) return section[key];
    var param = paramOf(key);
    return param ? param.default : null;
  }

  function readValue(sectionId, key) {
    if (MODE === 'dark') {
      var dark = CURRENT && CURRENT.dark && CURRENT.dark[sectionId];
      if (dark && HAS.call(dark, key)) return dark[key];
    }
    return lightValue(sectionId, key);
  }

  function hasDarkOverride(sectionId, key) {
    var dark = CURRENT && CURRENT.dark && CURRENT.dark[sectionId];
    return !!(dark && HAS.call(dark, key));
  }

  function equalForKind(a, b, param) {
    if (param && (param.kind === 'number' || param.kind === 'percent' ||
      param.kind === 'px' || param.kind === 'deg' || param.kind === 'ms')) {
      return Math.abs(num(a, 0) - num(b, 0)) < 1e-6;
    }
    return String(a) === String(b);
  }

  function writeValue(sectionId, key, value) {
    var param = paramOf(key);
    if (MODE === 'dark') {
      // 暗色只存「显式覆盖」：与亮色相同的值直接删键，回到继承语义。
      var light = lightValue(sectionId, key);
      var darkRoot = CURRENT.dark || (CURRENT.dark = {});
      var darkSection = darkRoot[sectionId] || (darkRoot[sectionId] = {});
      if (equalForKind(value, light, param)) delete darkSection[key];
      else darkSection[key] = value;
      if (!Object.keys(darkSection).length) delete darkRoot[sectionId];
    } else {
      var section = CURRENT[sectionId] || (CURRENT[sectionId] = {});
      section[key] = value;
      // 亮色值变了以后，暗色组里与新亮色相同的覆盖就成了死值，一并清掉。
      var dk = CURRENT.dark && CURRENT.dark[sectionId];
      if (dk && HAS.call(dk, key) && equalForKind(dk[key], value, param)) {
        delete dk[key];
        if (!Object.keys(dk).length) delete CURRENT.dark[sectionId];
      }
    }
    markDirty();
  }

  // 暗色只存「与亮色不同」的显式覆盖。想让某一项回到继承亮色，只能把值手动调成与亮色完全相同 —— 34 个 number / 14 个 percent 参数没人能盯着数字对齐。
  // 所以给「暗色」标记一个可点击的清除入口。
  function clearDarkOverride(sectionId, key) {
    var darkRoot = CURRENT && CURRENT.dark;
    if (!darkRoot || !darkRoot[sectionId] || !HAS.call(darkRoot[sectionId], key)) return;
    delete darkRoot[sectionId][key];
    if (!Object.keys(darkRoot[sectionId]).length) delete darkRoot[sectionId];
    renderPane();
    markDirty();
  }

  function darkOverrideCount() {
    var total = 0;
    var dark = CURRENT && CURRENT.dark;
    if (!dark) return 0;
    Object.keys(dark).forEach(function (section) {
      if (dark[section] && typeof dark[section] === 'object') total += Object.keys(dark[section]).length;
    });
    return total;
  }

  function markDirty() {
    DIRTY = !same(CURRENT, BASELINE);
    renderBadge();
    // 头部状态（尤其暗色批量清除按钮的显示与计数）必须跟手：
    // 只更新徽标会让按钮停在上一帧，用户以为没生效。
    renderHeadState();
    schedulePreview();
  }

  /* ---------------- 预览编译 ---------------- */

  var PREVIEW_APPLIED = [];
  var SEARCH = '';
  var PREVIEW_INFLIGHT = false;
  var PREVIEW_PENDING = false;
  var REF_ON = false;
  var REF_CACHE = {};
  var REF_APPLIED = [];
  var REF_SEQ = 0;

  function schedulePreview() {
    if (!MOUNTED) return;
    if (PREVIEW_TIMER) clearTimeout(PREVIEW_TIMER);
    PREVIEW_TIMER = setTimeout(function () {
      PREVIEW_TIMER = null;
      runPreview();
    }, 140);
  }

  function runPreview() {
    if (!CURRENT) return;
    // 拖滑块会连续触发：上一个请求没回来就只记一个「待补」，回来后再发一次最新参数。
    // 否则快速拖动会堆出几十个 run，只是让过期结果互相覆盖。
    if (PREVIEW_INFLIGHT) { PREVIEW_PENDING = true; return; }
    PREVIEW_INFLIGHT = true;
    var seq = ++PREVIEW_SEQ;
    var payload = clone(CURRENT);
    var mode = MODE;
    callEntry('preview_materials', { materials: payload, mode: mode }).then(function (result) {
      PREVIEW_INFLIGHT = false;
      if (seq !== PREVIEW_SEQ) { drainPreview(); return; }
      PREVIEW_VARS = result && result.vars ? result.vars : {};
      paintPreview(result);
      drainPreview();
    }).catch(function (error) {
      PREVIEW_INFLIGHT = false;
      if (seq !== PREVIEW_SEQ) { drainPreview(); return; }
      var meta = byId('mt-pv-meta');
      if (meta) meta.textContent = '预览编译失败：' + (error && error.message ? error.message : '后端插件可能未启动');
      drainPreview();
    });
  }

  function drainPreview() {
    if (!PREVIEW_PENDING) return;
    PREVIEW_PENDING = false;
    schedulePreview();
  }

  function paintPreview(result) {
    var stage = byId('mt-pv');
    if (!stage) return;
    // 组件缩略块吃同一批变量：10 个小方块直接引用组件别名，未自定义时按回退链落到全局。
    var paintTargets = [stage];
    var strip = byId('mt-comp-strip');
    if (strip) paintTargets.push(strip);
    var vars = (result && result.vars) || {};
    PREVIEW_APPLIED.forEach(function (name) {
      paintTargets.forEach(function (target) { target.style.removeProperty(name); });
    });
    PREVIEW_APPLIED = [];
    Object.keys(vars).forEach(function (name) {
      paintTargets.forEach(function (target) { target.style.setProperty(name, vars[name]); });
      PREVIEW_APPLIED.push(name);
    });
    var attrs = (result && result.attrs) || {};
    ['data-nts-mt', 'data-nts-mt-quality', 'data-nts-mt-preset'].forEach(function (name) {
      paintTargets.forEach(function (target) {
        if (HAS.call(attrs, name)) target.setAttribute(name, attrs[name]);
        else target.removeAttribute(name);
      });
    });
    paintReadability(vars);
    // 对比开着、用户又切了亮暗时，右半屏要换成该模式的原始变量。
    if (REF_ON) ensureRef(MODE);
    var meta = byId('mt-pv-meta');
    if (meta) {
      meta.textContent = '';
      meta.appendChild(el('b', { text: Object.keys(vars).length + ' 个变量' }));
      meta.appendChild(document.createTextNode(' · 预设 ' + presetLabel(CURRENT.preset) +
        ' · 画质 ' + qualityLabel(CURRENT.quality) +
        ' · ' + (MODE === 'dark' ? '暗色' : '亮色')));
    }
  }

  // --nts-mt-bg 是「底色 rgba(tint, opacity 乘染色浓度)」，它的 alpha 就是玻璃的实心程度。
  function bgAlphaOf(vars) {
    var raw = (vars && (vars['--nts-mt-bg'] || vars['--nts-mt-bg-c'])) || '';
    var hit = String(raw).match(/rgba?\(([^)]*)\)/i);
    if (!hit) return null;
    var parts = hit[1].split(',');
    if (parts.length < 4) return 1;
    var alpha = Number(parts[parts.length - 1]);
    return isFinite(alpha) ? alpha : null;
  }

  function paintReadability(vars) {
    var box = byId('mt-pv-warn');
    if (!box) return;
    var alpha = bgAlphaOf(vars);
    if (alpha !== null && alpha >= 0.62) {
      box.textContent = '玻璃基底不透明度 ' + Math.round(alpha * 100) +
        '%，压在上面的文字容易看不清 —— 调低「透明度」或「染色浓度」可恢复对比度（当前设置仍然可用，只是观感偏实）。';
      box.style.display = '';
    } else {
      box.textContent = '';
      box.style.display = 'none';
    }
  }

  function presetLabel(id) {
    if (SPEC && SPEC.preset_labels && SPEC.preset_labels[id]) return SPEC.preset_labels[id];
    return id || 'custom';
  }

  function qualityLabel(id) {
    if (!SPEC || !SPEC.quality) return id || '';
    for (var i = 0; i < SPEC.quality.length; i++) {
      if (SPEC.quality[i].id === id) return SPEC.quality[i].label;
    }
    return id || '';
  }

  /* ---------------- 导航分组 ---------------- */

  var TABS = [
    { id: 'overview', label: '总览' },
    { id: 'base', label: '基础材质' },
    { id: 'dye', label: '玻璃染色' },
    { id: 'light', label: '光线反射' },
    { id: 'refract', label: '折射色散' },
    { id: 'liquid', label: '液态动态' },
    { id: 'surface', label: '表面细节' },
    { id: 'anim', label: '动画交互' },
    { id: 'components', label: '组件材质' },
    { id: 'presets', label: '预设与画质' }
  ];

  var GROUPS = {
    base: [
      { title: '基础面', keys: ['opacity', 'tint', 'tintStrength', 'blur', 'blurLevel', 'saturation', 'brightness'] }
    ],
    dye: [
      { title: '玻璃染色', keys: ['dyePrimary', 'dyeSecondary', 'dyeStrength', 'dyeWarmth', 'dyeAlpha', 'dyeGradient'] }
    ],
    light: [
      { title: '边缘光', keys: ['edgeIntensity', 'edgeWidth', 'edgeAlpha', 'edgeColor', 'edgeTop', 'edgeBottom', 'edgeLeft', 'edgeRight', 'edgeFade'] },
      { title: '内部高光', keys: ['innerIntensity', 'innerAlpha', 'innerRange', 'innerPos', 'innerSoftness', 'innerDouble'] },
      { title: '环境反射', keys: ['ambientIntensity', 'ambientColor', 'ambientDirection', 'ambientRange', 'ambientBlur', 'ambientAlpha'] },
      { title: '动态光照', keys: ['dynamic', 'dynamicSpeed', 'dynamicIntensity', 'dynamicRange', 'dynamicReflect', 'mouseFollow', 'mouseDelay'] }
    ],
    refract: [
      { title: '折射', keys: ['refractStrength', 'refractRange', 'refractEdge', 'refractBlur', 'refractAlpha', 'refractLocal'] },
      { title: '色散', keys: ['dispStrength', 'dispRange', 'dispAlpha', 'dispShift', 'dispWidth'] }
    ],
    liquid: [
      { title: '液态动态', keys: ['liquid', 'liquidAmp', 'liquidSpeed', 'liquidRange', 'liquidSmooth', 'liquidStrength'] }
    ],
    surface: [
      { title: '噪点', keys: ['noise', 'noiseStrength', 'noiseSize', 'noiseAlpha', 'noiseDensity'] },
      { title: '圆角', keys: ['radius', 'radiusCard', 'radiusButton', 'radiusDialog', 'radiusInput'] },
      { title: '边框', keys: ['borderWidth', 'borderAlpha'] },
      { title: '阴影', keys: ['shadowStrength', 'shadowSpread', 'shadowDistance', 'shadowBlur', 'innerShadow', 'innerShadowStrength'] }
    ],
    anim: [
      { title: '开关', keys: ['animation', 'reducedMotion', 'easing'] },
      { title: '时长', keys: ['enterDuration', 'exitDuration', 'hoverDuration', 'pressDuration', 'expandDuration'] },
      { title: '交互反馈', keys: ['hover', 'press', 'spotlight', 'spotRadius', 'spotIntensity', 'spotSpeed'] }
    ]
  };
  /* ---------------- 控件 ---------------- */

  function stepOf(param) {
    var min = num(param.min, 0);
    var max = num(param.max, 1);
    if (param.kind === 'number') return (max - min) <= 2 ? 0.01 : 0.1;
    if (param.kind === 'ms') return max > 2000 ? 50 : 10;
    if (param.kind === 'deg') return 1;
    if (param.kind === 'px') return 1;
    return 1;
  }

  function fmtNum(value) {
    var n = num(value, 0);
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return String(Math.round(n * 100) / 100);
  }

  function optionsOf(param) {
    if (param.choices && param.choices.length) {
      return param.choices.map(function (choice) { return { value: String(choice), label: String(choice) }; });
    }
    if (param.key === 'blurLevel' && SPEC && SPEC.blur_levels) {
      return Object.keys(SPEC.blur_levels).map(function (key) {
        return { value: key, label: SPEC.blur_levels[key] };
      });
    }
    if (param.key === 'easing' && SPEC && SPEC.easings) {
      return SPEC.easings.map(function (key) { return { value: key, label: key }; });
    }
    return [];
  }

  function buildRow(param) {
    var sectionId = param.section;
    var key = param.key;
    var row = el('div', { class: 'mt-row' });
    var label = el('label', { class: 'mt-row-label', title: param.label + ' · ' + key });
    label.appendChild(document.createTextNode(param.label));
    var mark = null;
    function syncMark() {
      var overridden = MODE === 'dark' && hasDarkOverride(sectionId, key);
      if (overridden && !mark) {
        mark = el('button', { class: 'mt-row-clear', type: 'button', title: '清除这一项的暗色覆盖，回到亮色', text: '暗色 ✕' });
        mark.addEventListener('click', function () { clearDarkOverride(sectionId, key); });
        label.appendChild(mark);
      } else if (!overridden && mark) {
        label.removeChild(mark);
        mark = null;
      }
    }
    syncMark();
    row.appendChild(label);
    var value = readValue(sectionId, key);

    function commit(next) {
      writeValue(sectionId, key, next);
      syncMark();
    }

    if (param.kind === 'bool') {
      var toggle = el('label', { class: 'mt-toggle' });
      var box = el('input', { type: 'checkbox' });
      box.checked = !!value;
      var caption = el('span', { text: value ? '开启' : '关闭' });
      box.addEventListener('change', function () {
        caption.textContent = box.checked ? '开启' : '关闭';
        commit(!!box.checked);
      });
      toggle.appendChild(box);
      toggle.appendChild(caption);
      row.appendChild(toggle);
      row.appendChild(el('span', {}));
      return row;
    }

    if (param.kind === 'enum') {
      var select = el('select', { class: 'mt-select mt-select-full' });
      optionsOf(param).forEach(function (option) {
        var node = el('option', { value: option.value, text: option.label });
        if (String(option.value) === String(value)) node.setAttribute('selected', '');
        select.appendChild(node);
      });
      select.addEventListener('change', function () { commit(select.value); });
      row.appendChild(select);
      row.appendChild(el('span', {}));
      return row;
    }

    if (param.kind === 'color') {
      var wrap = el('div', { class: 'mt-color' });
      var pick = el('input', { type: 'color', value: hexOf(value) });
      var text = el('input', { type: 'text', spellcheck: 'false', value: String(value || '') });
      pick.addEventListener('input', function () {
        text.value = pick.value;
        commit(pick.value);
      });
      text.addEventListener('change', function () {
        var next = String(text.value || '').trim();
        var okColor = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(next) || /^rgba?\(/i.test(next);
        if (!okColor) {
          text.value = String(readValue(sectionId, key) || '');
          toast('颜色格式不支持：' + next + '（用 #rrggbb 或 rgb()/rgba()）', 'error');
          return;
        }
        text.value = next;
        pick.value = hexOf(next);
        commit(next);
      });
      wrap.appendChild(pick);
      wrap.appendChild(text);
      row.appendChild(wrap);
      row.appendChild(el('span', {}));
      return row;
    }

    var min = num(param.min, 0);
    var max = num(param.max, 1);
    var step = stepOf(param);
    var range = el('input', {
      type: 'range', class: 'mt-range',
      min: min, max: max, step: step, value: fmtNum(value)
    });
    var number = el('input', {
      type: 'number', class: 'mt-num',
      min: min, max: max, step: step, value: fmtNum(value)
    });
    range.addEventListener('input', function () {
      var next = num(range.value, num(value, min));
      number.value = fmtNum(next);
      commit(next);
    });
    number.addEventListener('change', function () {
      var next = clamp(num(number.value, num(value, min)), min, max);
      number.value = fmtNum(next);
      range.value = fmtNum(next);
      commit(next);
    });
    row.appendChild(range);
    row.appendChild(number);
    return row;
  }

  /* ---------------- 面板内容 ---------------- */


  /** 90 个参数散在 8 个界面，找不到就只能靠翻页。搜索跨全部界面，命中即列。 */
  function renderSearch(pane) {
    var hits = [];
    Object.keys(PARAMS).forEach(function (key) {
      var param = PARAMS[key];
      var hay = (key + ' ' + (param.label || '') + ' ' + (param.section || '')).toLowerCase();
      if (hay.indexOf(SEARCH) >= 0) hits.push(param);
    });
    pane.appendChild(el('p', { class: 'mt-hint', text:
      hits.length ? ('找到 ' + hits.length + ' 项（跨全部界面），清空搜索回到当前界面。') : ('没有匹配「' + SEARCH + '」的参数。') }));
    hits.forEach(function (param) { pane.appendChild(buildRow(param)); });
  }
  function renderPane() {
    var pane = byId('mt-pane');
    if (!pane) return;
    pane.textContent = '';
    if (SEARCH) return renderSearch(pane);
    if (TAB === 'overview') return renderOverview(pane);
    if (TAB === 'components') return renderComponents(pane);
    if (TAB === 'presets') return renderPresets(pane);
    (GROUPS[TAB] || []).forEach(function (group) {
      pane.appendChild(el('div', { class: 'mt-sub', text: group.title }));
      group.keys.forEach(function (key) {
        var param = PARAMS[key];
        if (param) pane.appendChild(buildRow(param));
      });
    });
    if (TAB === 'base') {
      pane.appendChild(el('p', { class: 'mt-hint', text:
        '暗色主题下玻璃底会自动压暗（保留你的染色倾向）：宿主的暗色文字是浅色，白底会看不清，' +
        '所以暗色模式下看到的是深色玻璃。' }));
    }
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '亮色与暗色分别调：暗色只保存与亮色不同的值，未覆盖项自动继承亮色。' }));
  }

  function statLine(label, value) {
    var row = el('div', { class: 'mt-row' });
    row.appendChild(el('span', { class: 'mt-row-label', text: label }));
    row.appendChild(el('span', { class: 'mt-row-label', text: String(value) }));
    row.appendChild(el('span', {}));
    return row;
  }

  function renderOverview(pane) {
    pane.appendChild(el('div', { class: 'mt-sub', text: '当前状态' }));
    pane.appendChild(statLine('高级材质', CURRENT.enabled ? '已启用' : '未启用'));
    pane.appendChild(statLine('材质预设', presetLabel(CURRENT.preset)));
    pane.appendChild(statLine('性能模式', qualityLabel(CURRENT.quality)));
    pane.appendChild(statLine('暗色覆盖', darkOverrideCount() + ' 项'));
    pane.appendChild(statLine('组件自定义', Object.keys(CURRENT.components || {}).length + ' / 10'));
    pane.appendChild(statLine('参数总数', (SPEC.params || []).length + ' 项'));

    pane.appendChild(el('div', { class: 'mt-sub', text: '快捷预设' }));
    var grid = el('div', { class: 'mt-grid' });
    PRESET_ORDER().concat(CUSTOM_ORDER()).forEach(function (id) {
      grid.appendChild(presetCard(id));
    });
    pane.appendChild(grid);

    pane.appendChild(el('div', { class: 'mt-sub', text: '能力边界' }));
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '高级材质只作用于颜色、模糊、折射、光照、圆角、边框与阴影：不改 display/position/尺寸/溢出/z-index，' +
      '因此不会破坏布局；文本对比度由面板的「可读性自检」兜底。关闭插件或关掉这里的开关后，应用侧会整体撤掉材质层，回到原始主题。' }));
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '不支持 backdrop-filter 的环境（旧内核 / 硬件加速被禁用）会自动降级为纯色半透明，不会出现半渲染的毛玻璃。' }));
  }

  function PRESET_ORDER() {
    var order = [];
    ['clear', 'frosted', 'liquid', 'smoke', 'soft', 'prism'].forEach(function (id) {
      if (SPEC && SPEC.presets && SPEC.presets[id]) order.push(id);
    });
    Object.keys((SPEC && SPEC.presets) || {}).forEach(function (id) {
      if (order.indexOf(id) < 0) order.push(id);
    });
    return order;
  }

  /** 用户自存的预设名（按字典序，稳定排序以免每次渲染顺序抖动）。 */
  function CUSTOM_ORDER() {
    return Object.keys((CURRENT && CURRENT.custom_presets) || {}).sort();
  }

  function presetCard(id) {
    var builtin = (SPEC.presets || {})[id] || null;
    var custom = (!builtin && ((CURRENT && CURRENT.custom_presets) || {})[id]) || null;
    var active = CURRENT.preset === id;
    var card = el('button', {
      class: 'mt-preset' + (active ? ' is-active' : ''), type: 'button'
    });
    card.appendChild(el('b', {
      text: (builtin && builtin.label) || (custom ? id : presetLabel(id))
    }));
    card.appendChild(el('span', {
      text: (builtin && builtin.desc) || (custom ? '自定义预设 · 点击整套套用' : (PRESET_DESC[id] || ''))
    }));
    card.addEventListener('click', function () { applyPreset(id); });
    return card;
  }

  var PRESET_DESC = {
    clear: '清透玻璃，弱染色',
    frosted: '磨砂雾面，常规阅读',
    liquid: '苹果液态玻璃：大圆角、高饱和、顶部镜面亮线与内折射',
    smoke: '烟熏暗调，低饱和',
    soft: '柔和低对比，长时使用',
    prism: '棱镜色散，强边缘光'
  };

  function applyPreset(id) {
    var builtin = (SPEC && SPEC.presets && SPEC.presets[id]) || null;
    var custom = (!builtin && CURRENT && CURRENT.custom_presets && CURRENT.custom_presets[id]) || null;
    if (!builtin && !custom) return;
    var params = builtin ? (builtin.params || {}) : custom;
    ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
      CURRENT[section] = {};
      Object.keys(PARAMS).forEach(function (key) {
        if (PARAMS[key].section === section) CURRENT[section][key] = PARAMS[key].default;
      });
      if (params[section] && typeof params[section] === 'object') {
        Object.keys(params[section]).forEach(function (key) {
          CURRENT[section][key] = params[section][key];
        });
      }
    });
    CURRENT.preset = id;
    // 预设是「整套」语义：暗色覆盖一并清空，避免旧覆盖与新预设打架。
    var clearedDark = countOverrides(CURRENT.dark);
    CURRENT.dark = {};
    if (clearedDark > 0) {
      toast('切换预设会清空暗色覆盖：已清除 ' + clearedDark + ' 项（预设是整套替换）。', 'warn');
    }
    markDirty();
    renderPane();
    renderHeadState();
  }

  /** 把当前五节参数存成一条自定义预设（随「应用材质」提交，后端按名合并落盘）。 */
  function saveCurrentAsPreset() {
    var input = byId('mt-preset-name');
    var name = String((input && input.value) || '').trim().slice(0, 40);
    if (!name) { toast('先给预设起个名字。', 'warn'); return; }
    if (SPEC && SPEC.presets && SPEC.presets[name.toLowerCase()]) {
      toast('「' + name + '」和内置预设重名了，换一个。', 'warn');
      return;
    }
    if (!CURRENT.custom_presets || typeof CURRENT.custom_presets !== 'object') {
      CURRENT.custom_presets = {};
    }
    var copy = {};
    ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
      copy[section] = {};
      Object.keys(PARAMS).forEach(function (key) {
        if (PARAMS[key].section !== section) return;
        if (CURRENT[section] && HAS.call(CURRENT[section], key)) {
          copy[section][key] = CURRENT[section][key];
        }
      });
    });
    CURRENT.custom_presets[name] = copy;
    CURRENT.preset = name;
    if (input) input.value = '';
    markDirty();
    renderPane();
    renderHeadState();
    toast('已存为「' + name + '」，点「应用材质」写入磁盘。', 'ok');
  }

  /** 删除一条自定义预设：走独立 entry 立即落盘（应用的合并语义会把删掉的加回来）。 */
  function deleteCustomPreset(name) {
    if (!name) return;
    callEntry('delete_material_preset', { name: name }).then(function (data) {
      var materials = (data && data.materials) || null;
      if (materials) {
        CURRENT.custom_presets = materials.custom_presets || {};
        CURRENT.preset = materials.preset || 'custom';
        if (BASELINE) {
          BASELINE.custom_presets = clone(CURRENT.custom_presets);
          BASELINE.preset = CURRENT.preset;
        }
      } else {
        if (CURRENT.custom_presets) delete CURRENT.custom_presets[name];
        if (CURRENT.preset === name) CURRENT.preset = 'custom';
      }
      markDirty();
      renderPane();
      renderHeadState();
      toast('已删除自定义预设「' + name + '」。', 'ok');
    }).catch(function (error) {
      toast('删除预设失败：' + (error && error.message ? error.message : error), 'warn');
    });
  }

  function renderPresets(pane) {
    pane.appendChild(el('div', { class: 'mt-sub', text: '材质预设' }));
    var grid = el('div', { class: 'mt-grid' });
    PRESET_ORDER().forEach(function (id) { grid.appendChild(presetCard(id)); });
    pane.appendChild(grid);
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '预设是整套参数替换：切换后五节参数会整体重置为该预设的值，不会残留上一个预设的调校。' }));

    pane.appendChild(el('div', { class: 'mt-sub', text: '我的预设' }));
    var customIds = CUSTOM_ORDER();
    if (customIds.length) {
      var customGrid = el('div', { class: 'mt-grid' });
      customIds.forEach(function (id) {
        var wrap = el('div', { class: 'mt-preset-wrap' });
        wrap.appendChild(presetCard(id));
        var del = el('button', { class: 'mt-mini mt-preset-del', type: 'button', text: '删除' });
        del.addEventListener('click', function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          deleteCustomPreset(id);
        });
        wrap.appendChild(del);
        customGrid.appendChild(wrap);
      });
      pane.appendChild(customGrid);
    } else {
      pane.appendChild(el('p', { class: 'mt-hint', text:
        '还没有自定义预设。把参数调到满意后，在下面起个名存起来（会随「应用材质」写进 theme.json）。' }));
    }
    var saveRow = el('div', { class: 'mt-save-row' });
    var nameInput = el('input', { class: 'mt-input', id: 'mt-preset-name', type: 'text',
      placeholder: '给当前这套材质起个名（≤40 字）', maxlength: '40' });
    var saveBtn = el('button', { class: 'mt-mini', type: 'button', text: '存为预设' });
    saveBtn.addEventListener('click', saveCurrentAsPreset);
    nameInput.addEventListener('keydown', function (event) {
      if (event && event.key === 'Enter') saveCurrentAsPreset();
    });
    saveRow.appendChild(nameInput);
    saveRow.appendChild(saveBtn);
    pane.appendChild(saveRow);
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '删除自定义预设会立即写盘（不依赖「应用材质」）；保存则跟随「应用材质」一起提交，导出/分享包里也会带上。' }));

    pane.appendChild(el('div', { class: 'mt-sub', text: '性能模式' }));
    var modes = el('div', { class: 'mt-grid' });
    ((SPEC && SPEC.quality) || []).forEach(function (item) {
      var card = el('button', {
        class: 'mt-preset' + (CURRENT.quality === item.id ? ' is-active' : ''), type: 'button'
      });
      card.appendChild(el('b', { text: item.label }));
      card.appendChild(el('span', { text: QUALITY_DESC[item.id] || '' }));
      card.addEventListener('click', function () {
        CURRENT.quality = item.id;
        markDirty();
        renderPane();
        renderHeadState();
      });
      modes.appendChild(card);
    });
    pane.appendChild(modes);
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '省电：模糊减半、关闭折射/动态/噪点/追光；高品质：加强边缘光与环境反射；极致：全部效果满配。' }));

    pane.appendChild(el('div', { class: 'mt-sub', text: '重置' }));
    var reset = el('button', { class: 'mt-mini is-danger', type: 'button', text: '恢复默认材质参数' });
    reset.addEventListener('click', function () {
      var defaults = deepDefaults();
      ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
        CURRENT[section] = defaults[section];
      });
      CURRENT.dark = {};
      CURRENT.components = {};
      CURRENT.preset = 'custom';
      markDirty();
      renderPane();
      renderHeadState();
    });
    pane.appendChild(reset);
  }

  var QUALITY_DESC = {
    saver: '省电优先，效果减半',
    balanced: '均衡，默认档',
    high: '高品质，光效加强',
    ultra: '极致，全部效果满配'
  };
  /* ---------------- 组件材质 ---------------- */

  function compControl(compId, param) {
    var entry = (CURRENT.components && CURRENT.components[compId]) || {};
    var row = el('div', { class: 'mt-comp-row' });
    row.appendChild(el('span', { text: param.label }));
    var min = num(param.min, 0);
    var max = num(param.max, 1);
    var step = stepOf(param);
    var value = entry[param.key];
    if (value === undefined || value === null) value = lightValue(param.section, param.key);
    var range = el('input', { type: 'range', class: 'mt-range', min: min, max: max, step: step, value: fmtNum(value) });
    var out = el('span', { class: 'mt-comp-num', text: fmtNum(value) });
    range.addEventListener('input', function () {
      var next = num(range.value, num(value, min));
      out.textContent = fmtNum(next);
      CURRENT.components = CURRENT.components || {};
      var target = CURRENT.components[compId] || (CURRENT.components[compId] = { mode: 'custom' });
      target.mode = 'custom';
      target[param.key] = next;
      markDirty();
    });
    row.appendChild(range);
    row.appendChild(out);
    return row;
  }

  function applyComponentMode(compId, mode) {
    CURRENT.components = CURRENT.components || {};
    if (mode === 'inherit') {
      delete CURRENT.components[compId];
    } else if (mode === 'preset') {
      CURRENT.components[compId] = {
        mode: 'preset',
        preset: CURRENT.preset && CURRENT.preset !== 'custom' ? CURRENT.preset : 'frosted'
      };
    } else {
      CURRENT.components[compId] = {
        mode: 'custom',
        opacity: lightValue('global', 'opacity'),
        blur: lightValue('global', 'blur'),
        radius: lightValue('surface', 'radius'),
        borderWidth: lightValue('surface', 'borderWidth'),
        shadowStrength: lightValue('surface', 'shadowStrength')
      };
    }
    markDirty();
    renderPane();
  }

  function componentCard(comp) {
    var entry = (CURRENT.components && CURRENT.components[comp.id]) || { mode: 'inherit' };
    var card = el('div', { class: 'mt-comp' + (entry.mode === 'inherit' ? ' is-off' : '') });

    var head = el('div', { class: 'mt-comp-head' });
    head.appendChild(el('span', { class: 'mt-comp-name', text: comp.label }));

    var modeSel = el('select', { class: 'mt-select' });
    [['inherit', '继承全局'], ['preset', '套用预设'], ['custom', '自定义']].forEach(function (pair) {
      var opt = el('option', { value: pair[0], text: pair[1] });
      if (entry.mode === pair[0]) opt.setAttribute('selected', '');
      modeSel.appendChild(opt);
    });
    modeSel.addEventListener('change', function () { applyComponentMode(comp.id, modeSel.value); });
    head.appendChild(modeSel);

    if (entry.mode === 'preset') {
      var presetSel = el('select', { class: 'mt-select' });
      PRESET_ORDER().forEach(function (id) {
        var opt = el('option', { value: id, text: presetLabel(id) });
        if (entry.preset === id) opt.setAttribute('selected', '');
        presetSel.appendChild(opt);
      });
      presetSel.addEventListener('change', function () {
        CURRENT.components = CURRENT.components || {};
        CURRENT.components[comp.id] = { mode: 'preset', preset: presetSel.value };
        markDirty();
        renderPane();
      });
      head.appendChild(presetSel);
    }
    card.appendChild(head);

    if (entry.mode === 'custom') {
      var body = el('div', { class: 'mt-comp-body' });
      ((SPEC && SPEC.component_params) || []).forEach(function (key) {
        var param = PARAMS[key];
        if (param) body.appendChild(compControl(comp.id, param));
      });
      card.appendChild(body);
    } else if (entry.mode === 'preset') {
      card.appendChild(el('div', { class: 'mt-comp-note', text:
        '取「' + presetLabel(entry.preset) + '」的透明度 / 模糊 / 圆角 / 边框 / 阴影' }));
    } else {
      card.appendChild(el('div', { class: 'mt-comp-note', text: '跟随全局材质，不产生专属变量' }));
    }
    return card;
  }


  /** 组件材质缩略块：10 个小方块直接吃组件别名变量，拖参数就能看到变化。 */
  function buildComponentStrip() {
    var strip = el('div', { class: 'mt-comp-strip', id: 'mt-comp-strip' });
    ((SPEC && SPEC.components) || []).forEach(function (comp) {
      var id = String(comp.id);
      var face = el('div', { class: 'mt-comp-chip-face' });
      face.style.setProperty('--chip-bg', 'var(--nts-mt-c-bg-' + id + ', var(--nts-mt-bg))');
      face.style.setProperty('--chip-radius', 'var(--nts-mt-c-radius-' + id + ', var(--nts-mt-radius))');
      face.style.setProperty('--chip-blur', 'var(--nts-mt-c-blur-' + id + ', var(--nts-mt-blur))');
      face.style.setProperty('--chip-bw', 'var(--nts-mt-c-border-width-' + id + ', var(--nts-mt-border-width))');
      face.style.setProperty('--chip-shadow', 'var(--nts-mt-c-shadow-' + id + ', var(--nts-mt-shadow))');
      strip.appendChild(el('div', { class: 'mt-comp-chip' }, face,
        el('span', { class: 'mt-comp-chip-name', text: comp.label || id })));
    });
    return strip;
  }
  function renderComponents(pane) {
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '组件级材质只影响对应组件：选「继承全局」时不产生任何专属变量，绝不会覆盖全局设置。' }));
    (function () {
      var compCount = Object.keys(CURRENT.components || {}).length;
      var bar = el('div', { class: 'mt-comp-bar' });
      bar.appendChild(el('span', { class: 'mt-comp-bar-text', text: compCount ? ('已自定义 ' + compCount + ' 个组件') : '全部组件跟随全局' }));
      var resetAll = el('button', { class: 'mt-mini', type: 'button', id: 'mt-comp-reset-all', text: '全部继承全局' });
      resetAll.disabled = compCount === 0;
      resetAll.addEventListener('click', function () {
        CURRENT.components = {};
        renderPane();
        markDirty();
        schedulePreview();
        if (window.NekoPanel) window.NekoPanel.toast('所有组件已回到继承全局', 'ok');
      });
      bar.appendChild(resetAll);
      pane.appendChild(bar);
    })();
    if (SPEC && SPEC.components) pane.appendChild(buildComponentStrip());
    ((SPEC && SPEC.components) || []).forEach(function (comp) {
      pane.appendChild(componentCard(comp));
    });
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '组件级只覆盖透明度 / 模糊 / 圆角 / 边框 / 阴影五项，颜色仍走全局，避免局部配色跳脱。' }));
    pane.appendChild(el('p', { class: 'mt-hint', text:
      '缩略块直接吃组件变量：选「继承全局」时回退全局值，选「自定义」时立刻变化（只示意颜色 / 圆角 / 模糊 / 边框 / 阴影）。' }));
    schedulePreview();
  }

  /* ---------------- 头部状态 ---------------- */

  function paramPair(a, b) { return String(a) === String(b); }

  function diffCount() {
    if (!CURRENT || !BASELINE) return 0;
    var total = 0;
    ['global', 'lighting', 'refraction', 'surface', 'animation'].forEach(function (section) {
      var left = CURRENT[section] || {};
      var right = BASELINE[section] || {};
      var keys = {};
      Object.keys(left).forEach(function (key) { keys[key] = 1; });
      Object.keys(right).forEach(function (key) { keys[key] = 1; });
      Object.keys(keys).forEach(function (key) {
        if (!equalForKind(left[key], right[key], PARAMS[key])) total++;
      });
    });
    if (!!CURRENT.enabled !== !!BASELINE.enabled) total++;
    if (!paramPair(CURRENT.preset, BASELINE.preset)) total++;
    if (!paramPair(CURRENT.quality, BASELINE.quality)) total++;
    if (!!CURRENT.lock !== !!BASELINE.lock) total++;
    if (!same(CURRENT.components || {}, BASELINE.components || {})) total++;
    var darkLeft = darkOverrideCount();
    var darkRight = 0;
    var baseDark = BASELINE.dark || {};
    Object.keys(baseDark).forEach(function (section) {
      if (baseDark[section] && typeof baseDark[section] === 'object') darkRight += Object.keys(baseDark[section]).length;
    });
    total += Math.abs(darkLeft - darkRight);
    return total;
  }

  function renderBadge() {
    var badge = byId('mt-dirty');
    if (!badge) return;
    if (DIRTY) {
      badge.textContent = '材质改动 ' + diffCount() + ' 项待应用';
      badge.classList.add('is-on');
    } else {
      badge.textContent = '';
      badge.classList.remove('is-on');
    }
  }

  function renderHeadState() {
    var enable = byId('mt-enabled');
    if (enable) enable.checked = !!(CURRENT && CURRENT.enabled);
    var modeSel = byId('mt-mode');
    if (modeSel) modeSel.value = MODE;
    var quality = byId('mt-quality');
    if (quality) quality.value = (CURRENT && CURRENT.quality) || 'balanced';
    var follow = byId('mt-follow');
    if (follow) follow.checked = !(CURRENT && CURRENT.lock);
    var warn = byId('mt-master-warn');
    if (warn) warn.style.display = (!MASTER_ON && CURRENT && CURRENT.enabled) ? 'block' : 'none';
    syncOffTag();
    var darkBtn = byId('mt-dark-clear');
    if (darkBtn) {
      var nDark = countOverrides(CURRENT.dark);
      darkBtn.style.display = (MODE === 'dark' && nDark > 0) ? '' : 'none';
      darkBtn.textContent = '清除暗色覆盖（' + nDark + '）';
      darkBtn.disabled = nDark === 0;
    }
  }

  function renderTabs() {
    var nav = byId('mt-tabs');
    if (!nav) return;
    nav.textContent = '';
    TABS.forEach(function (tab) {
      var button = el('button', {
        class: 'mt-tab' + (TAB === tab.id ? ' is-active' : ''),
        type: 'button', text: tab.label
      });
      button.addEventListener('click', function () {
        TAB = tab.id;
        // 切界面就退出搜索：留着搜索词会让人以为点了 tab 没反应。
        SEARCH = '';
        var box = byId('mt-search');
        if (box) box.value = '';
        renderTabs();
        renderPane();
      });
      nav.appendChild(button);
    });
  }

  function renderAll() {
    renderTabs();
    renderPane();
    renderHeadState();
    renderBadge();
  }
  /* ---------------- 外壳与预览 ---------------- */

  function bgLabel() { return BG_MODE === 'light' ? '背景：浅色' : '背景：深色'; }

  function applyBgMode() {
    var stage = byId('mt-pv');
    if (stage) stage.setAttribute('data-bg', BG_MODE);
    var ref = byId('mt-pv-ref');
    if (ref) ref.setAttribute('data-bg', BG_MODE);
    var btn = byId('mt-pv-bg');
    if (btn) btn.textContent = bgLabel();
  }

  // 预览要覆盖规范要求的 6 类组件：玻璃卡片(chat) / 气泡(bubble) / 按钮 / 输入框 / 弹窗 / 侧边栏。
  // 每一类都吃自己的组件别名（--nts-mt-c-*-<comp>），没自定义时按回退链落到全局 ——
  // 否则「组件材质」页拖参数，预览纹丝不动，用户只能靠猜。
  function buildStack() {
    var stack = el('div', { class: 'mt-pv-stack' });
    var hero = el('div', { class: 'mt-pv-card mt-pv-hero mt-pv-chat' });
    hero.appendChild(el('div', { class: 'mt-pv-avatar' }));
    var lines = el('div', { class: 'mt-pv-lines' });
    lines.appendChild(el('i', {}));
    lines.appendChild(el('i', {}));
    hero.appendChild(lines);
    stack.appendChild(hero);

    var msg = el('div', { class: 'mt-pv-card mt-pv-msg' });
    msg.appendChild(el('span', { class: 'mt-pv-tag', text: '友' }));
    msg.appendChild(document.createTextNode('玻璃材质实时预览'));
    stack.appendChild(msg);

    var row = el('div', { class: 'mt-pv-row' });
    row.appendChild(el('div', { class: 'mt-pv-btn', text: '普通按钮' }));
    row.appendChild(el('div', { class: 'mt-pv-input' }));
    var side = el('div', { class: 'mt-pv-side' });
    side.appendChild(el('i', {}));
    side.appendChild(el('i', {}));
    side.appendChild(el('i', {}));
    row.appendChild(side);
    stack.appendChild(row);

    var pop = el('div', { class: 'mt-pv-pop' });
    var popHead = el('div', { class: 'mt-pv-pop-head' });
    popHead.appendChild(el('span', { text: '弹窗标题' }));
    popHead.appendChild(el('span', { class: 'mt-pv-x', text: '✕' }));
    pop.appendChild(popHead);
    pop.appendChild(el('div', { class: 'mt-pv-pop-body', text: '弹窗内容会吃「弹窗」这一组件的材质。' }));
    stack.appendChild(pop);
    return stack;
  }

  // 原始 vs 当前：右半屏垫一层参数默认值编译出来的变量，拖滑块时能直接看出前后差别。
  // 变量缓存按 mode 分开 —— 亮/暗两套编译结果不同，混用会把暗色原始值贴到亮色预览上。
  function toggleCompare() {
    REF_ON = !REF_ON;
    var stage = byId('mt-pv');
    if (stage) stage.setAttribute('data-compare', REF_ON ? '1' : '0');
    var btn = byId('mt-pv-compare');
    if (btn) {
      if (REF_ON) btn.classList.add('is-on');
      else btn.classList.remove('is-on');
    }
    if (REF_ON) ensureRef(MODE);
  }

  function ensureRef(mode) {
    var box = byId('mt-pv-ref');
    if (!box) return;
    if (REF_CACHE[mode]) { paintRef(REF_CACHE[mode]); return; }
    var seq = ++REF_SEQ;
    callEntry('preview_materials', { materials: deepDefaults(), mode: mode }).then(function (result) {
      if (seq !== REF_SEQ) return;
      REF_CACHE[mode] = (result && result.vars) || {};
      paintRef(REF_CACHE[mode]);
    }).catch(function () {
      // 原始材质编译失败不该拖垮预览：留空即可，卡片按 CSS 兜底值显示。
    });
  }

  function paintRef(vars) {
    var box = byId('mt-pv-ref');
    if (!box) return;
    REF_APPLIED.forEach(function (name) { box.style.removeProperty(name); });
    REF_APPLIED = [];
    Object.keys(vars || {}).forEach(function (name) {
      box.style.setProperty(name, vars[name]);
      REF_APPLIED.push(name);
    });
  }

  function buildPreview() {
    if (!BG_TOUCHED) BG_MODE = MODE === 'dark' ? 'dark' : 'light';
    var wrap = el('aside', { class: 'mt-pv-wrap' });
    var title = el('div', { class: 'mt-pv-title' });
    title.appendChild(el('span', { id: 'mt-pv-mode-label', text: '实时预览 · ' + (MODE === 'dark' ? '暗色' : '亮色') }));
    title.appendChild(el('span', { class: 'mt-pv-off', id: 'mt-pv-off', text: '材质未启用' }));
    syncOffTag();
    title.appendChild(el('button', {
      class: 'mt-pv-bg', type: 'button', id: 'mt-pv-compare',
      title: '右半屏显示参数默认值下的原始材质，拖滑块时能直接对比调整前后',
      text: '对比原始',
      'on:click': function () { toggleCompare(); }
    }));
    title.appendChild(el('button', {
      class: 'mt-pv-bg', type: 'button', id: 'mt-pv-bg',
      title: '切换预览壁纸深浅：浅色壁纸看浅色场景是否看得清，深色壁纸看白玻璃反光',
      text: bgLabel(),
      'on:click': function () {
        BG_MODE = BG_MODE === 'light' ? 'dark' : 'light';
        BG_TOUCHED = true;
        applyBgMode();
      }
    }));
    wrap.appendChild(title);

    var stage = el('div', { class: 'mt-pv', id: 'mt-pv', 'data-bg': BG_MODE, 'data-compare': '0' });
    stage.appendChild(el('div', { class: 'mt-pv-wall' }));

    stage.appendChild(buildStack());

    stage.appendChild(el('div', { class: 'mt-pv-ambient' }));
    stage.appendChild(el('div', { class: 'mt-pv-edge' }));
    stage.appendChild(el('div', { class: 'mt-pv-inner' }));
    stage.appendChild(el('div', { class: 'mt-pv-noise' }));

    // A/B 对比层：右半屏铺「原始材质」，左半屏仍是当前设置（clip-path 在 CSS 里切）。
    var ref = el('div', { class: 'mt-pv-ref', id: 'mt-pv-ref', 'data-bg': BG_MODE });
    ref.appendChild(el('div', { class: 'mt-pv-wall' }));
    ref.appendChild(buildStack());
    ref.appendChild(el('span', { class: 'mt-pv-side-label', text: '原始' }));
    stage.appendChild(ref);
    stage.appendChild(el('span', { class: 'mt-pv-side-label mt-pv-label-cur', id: 'mt-pv-label-cur', text: '当前' }));

    // 追光预览：与加载器同一套 --nts-mt-mx/-my 变量，看到的就是应用窗口的效果。
    stage.addEventListener('pointermove', function (event) {
      var rect = stage.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      var x = ((event.clientX - rect.left) / rect.width) * 100;
      var y = ((event.clientY - rect.top) / rect.height) * 100;
      stage.style.setProperty('--nts-mt-mx', x.toFixed(2) + '%');
      stage.style.setProperty('--nts-mt-my', y.toFixed(2) + '%');
      // 光照方向与应用窗口同一套算法：从中心指向指针，sheen 角度跟着转。
      var ang = Math.atan2(y - 50, x - 50) * 180 / Math.PI;
      if (ang < 0) ang += 360;
      stage.style.setProperty('--nts-mt-light-angle', ang.toFixed(1) + 'deg');
    }, { passive: true });

    wrap.appendChild(stage);
    wrap.appendChild(el('div', { class: 'mt-pv-meta', id: 'mt-pv-meta', text: '正在编译预览…' }));
    // 可读性提示：玻璃基底越实，压在上面的文字越难看清。只提示不阻止 —— 用户有权调到自己喜欢。
    wrap.appendChild(el('div', { class: 'mt-pv-warn', id: 'mt-pv-warn' }));
    return wrap;
  }

  function mount(root) {
    if (!root || MOUNTED) return;
    if (!CURRENT) {
      CURRENT = deepDefaults();
      BASELINE = clone(CURRENT);
    }
    root.textContent = '';
    var shell = el('div', { class: 'mt-shell' });

    var head = el('div', { class: 'mt-head' });
    var title = el('h3', { class: 'mt-head-title' });
    title.appendChild(document.createTextNode('高级材质'));
    title.appendChild(el('span', { class: 'mt-tag', text: 'v' + ((SPEC && SPEC.version) || 1) }));
    head.appendChild(title);
    head.appendChild(el('span', { class: 'mt-head-spacer' }));
    var searchBox = el('input', { class: 'mt-search', id: 'mt-search',
      type: 'search', placeholder: '搜索参数（如 色散 / 圆角 / blur）' });
    searchBox.value = SEARCH;
    searchBox.addEventListener('input', function () {
      SEARCH = String(searchBox.value || '').trim().toLowerCase();
      renderPane();
    });
    head.appendChild(searchBox);
    head.appendChild(el('span', { class: 'mt-head-spacer' }));
    head.appendChild(el('span', { class: 'mt-dirty', id: 'mt-dirty' }));
    var darkClear = el('button', { class: 'mt-mini mt-dark-clear', id: 'mt-dark-clear', type: 'button', text: '清除暗色覆盖' });
    darkClear.addEventListener('click', clearAllDark);
    head.appendChild(darkClear);

    var enableWrap = el('label', { class: 'mt-switch' });
    var enableBox = el('input', { type: 'checkbox', id: 'mt-enabled' });
    enableBox.checked = !!CURRENT.enabled;
    enableBox.addEventListener('change', function () {
      CURRENT.enabled = !!enableBox.checked;
      markDirty();
      schedulePreview();
    });
    enableWrap.appendChild(enableBox);
    enableWrap.appendChild(el('span', { text: '启用' }));
    head.appendChild(enableWrap);

    // 「跟随主题」是 lock 的反面：关掉它 = 材质锁定，切主题不覆盖这套手调参数。
    var followWrap = el('label', { class: 'mt-switch',
      title: '打开时，切换主题会用主题自带的材质替换当前设置；关掉则保留你手调的这套材质' });
    var followBox = el('input', { type: 'checkbox', id: 'mt-follow' });
    followBox.checked = !CURRENT.lock;
    followBox.addEventListener('change', function () {
      CURRENT.lock = !followBox.checked;
      markDirty();
      if (window.NekoPanel) {
        window.NekoPanel.toast(followBox.checked
          ? '材质将跟随主题切换'
          : '材质已锁定：切换主题时保留当前设置', 'ok');
      }
    });
    followWrap.appendChild(followBox);
    followWrap.appendChild(el('span', { text: '跟随主题' }));
    head.appendChild(followWrap);

    var modeSel = el('select', { class: 'mt-select', id: 'mt-mode', title: '分别编辑亮色与暗色材质' });
    [['light', '亮色'], ['dark', '暗色']].forEach(function (pair) {
      var opt = el('option', { value: pair[0], text: pair[1] });
      if (MODE === pair[0]) opt.setAttribute('selected', '');
      modeSel.appendChild(opt);
    });
    modeSel.addEventListener('change', function () {
      MODE = modeSel.value === 'dark' ? 'dark' : 'light';
      renderPane();
      buildPreviewTitle();
      schedulePreview();
    });
    head.appendChild(modeSel);

    var qualitySel = el('select', { class: 'mt-select', id: 'mt-quality', title: '性能模式' });
    ((SPEC && SPEC.quality) || []).forEach(function (item) {
      var opt = el('option', { value: item.id, text: item.label });
      if (CURRENT.quality === item.id) opt.setAttribute('selected', '');
      qualitySel.appendChild(opt);
    });
    qualitySel.addEventListener('change', function () {
      CURRENT.quality = qualitySel.value;
      markDirty();
      schedulePreview();
    });
    head.appendChild(qualitySel);

    var resetBtn = el('button', { class: 'mt-mini', type: 'button', text: '回到已保存' });
    resetBtn.addEventListener('click', function () {
      resetLocal();
      toast('已丢弃材质改动', 'ok');
    });
    head.appendChild(resetBtn);
    shell.appendChild(head);

    // 总开关关着的时候，勾着「启用」也没用（加载器整块撤掉）；不提示就是静默失效。
    var masterWarn = el('div', { class: 'mt-master-warn', id: 'mt-master-warn', text:
      '主题总开关（面板上方的「启用主题」）已关闭 —— 高级材质暂时不会生效。' });
    masterWarn.style.display = 'none';
    shell.appendChild(masterWarn);

    var body = el('div', { class: 'mt-body' });
    body.appendChild(el('nav', { class: 'mt-tabs', id: 'mt-tabs' }));
    body.appendChild(el('section', { class: 'mt-pane', id: 'mt-pane' }));
    body.appendChild(buildPreview());
    shell.appendChild(body);

    root.appendChild(shell);
    MOUNTED = true;
    renderAll();
    schedulePreview();
  }

  /** 暗色覆盖项数（用于提示「切预设会清掉多少项」）。 */
  // 暗色只存差异，想把「某一页的暗色调法」整体丢掉时，逐项点清除太慢。
  function clearAllDark() {
    var n = countOverrides(CURRENT.dark);
    if (!n) return;
    CURRENT.dark = {};
    renderPane();
    markDirty();
    schedulePreview();
    if (window.NekoPanel) window.NekoPanel.toast('已清除 ' + n + ' 项暗色覆盖，全部回到亮色', 'ok');
  }

  function countOverrides(dark) {
    var total = 0;
    Object.keys(dark || {}).forEach(function (section) {
      var box = dark[section];
      if (box && typeof box === 'object') total += Object.keys(box).length;
    });
    return total;
  }

  /** 质材总开关未开时，预览仍然会画出来（调参用），必须标明它不代表窗口现状。 */
  function syncOffTag() {
    var tag = byId('mt-pv-off');
    if (!tag) return;
    tag.style.display = (CURRENT && CURRENT.enabled) ? 'none' : '';
  }

  function buildPreviewTitle() {
    if (!BG_TOUCHED) BG_MODE = MODE === 'dark' ? 'dark' : 'light';
    applyBgMode();
    var label = byId('mt-pv-mode-label');
    if (label) label.textContent = '实时预览 · ' + (MODE === 'dark' ? '暗色' : '亮色');
  }

  /* ---------------- 对外接口 ---------------- */

  /**
   * 把磁盘上的材质设为新基线。
   * options.submitted：保存时提交的那份快照（保存路径才传）。等待保存 / 回读期间
   * 用户又改了材质（CURRENT 已不等于提交的快照）时，只更新基线、保留这些新改动，
   * 不能被回读值静默覆盖。
   */
  function sync(palette, options) {
    MASTER_ON = !(palette && palette.enabled === false);
    var materials = palette && palette.materials;
    if (!materials || typeof materials !== 'object') return;
    var keepEdits = !!(options && Object.prototype.hasOwnProperty.call(options, 'submitted') &&
      CURRENT && !same(CURRENT, options.submitted));
    BASELINE = ensureSections(clone(materials));
    if (keepEdits) {
      DIRTY = !same(CURRENT, BASELINE);
    } else {
      CURRENT = ensureSections(clone(materials));
      DIRTY = false;
    }
    if (MOUNTED) {
      renderHeadState();
      renderBadge();
      renderPane();
      schedulePreview();
    }
  }

  function snapshot() {
    if (!CURRENT) return null;
    return clone(CURRENT);
  }

  function isDirty() {
    return !!(MOUNTED && CURRENT && BASELINE && !same(CURRENT, BASELINE));
  }

  function resetLocal() {
    if (!BASELINE) return;
    CURRENT = ensureSections(clone(BASELINE));
    DIRTY = false;
    if (MOUNTED) {
      renderHeadState();
      renderBadge();
      renderPane();
      schedulePreview();
    }
  }

  function boot() {
    var root = byId('materials-root');
    if (!root) return;
    callEntry('material_spec', {}).then(function (spec) {
      SPEC = spec || {};
      (SPEC.params || []).forEach(function (param) { PARAMS[param.key] = param; });
      if (!CURRENT) {
        CURRENT = ensureSections(clone(SPEC.current || deepDefaults()));
        BASELINE = ensureSections(clone(CURRENT));
      }
      mount(root);
    }).catch(function (error) {
      root.textContent = '';
      var shell = el('div', { class: 'mt-shell' });
      var head = el('div', { class: 'mt-head' });
      head.appendChild(el('h3', { class: 'mt-head-title', text: '高级材质' }));
      head.appendChild(el('span', { class: 'mt-hint', text:
        '参数表读取失败：' + (error && error.message ? error.message : '后端插件可能未启动') }));
      shell.appendChild(head);
      root.appendChild(shell);
    });
  }

  window.NekoMaterials = {
    mount: mount,
    sync: sync,
    snapshot: snapshot,
    isDirty: isDirty,
    resetLocal: resetLocal,
    applyPreset: function (id) { applyPreset(id); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
