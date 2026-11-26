/* Advanced material center -- panel-side interaction layer.
   Data flow: this file only "edits the materials subtree + asks the backend to
   compile a preview". Persisting is done by app.js applyTheme via apply_theme,
   and the compiled variables come from the backend compile_materials, so the
   frontend never duplicates the formulas (preview and app window can't drift).
   Public API on window.NekoMaterials:
     mount(root)     render the shell (#materials-root in index.html)
     sync(palette)   sync current/baseline after get_theme returns, then redraw
     snapshot()      current material settings for applyTheme
     isDirty()       whether there are unapplied material edits
     resetLocal()    drop local edits and return to the baseline
*/
(function () {
  'use strict';

  var HAS = Object.prototype.hasOwnProperty;
  var NL = String.fromCharCode(10);

  /** Translate a panel string under themeStudio.<key>; falls back to the Chinese source text. */
  function T(key, fallback, params) {
    if (typeof window.tsT === 'function') return window.tsT(key, fallback, params);
    var text = String(fallback == null ? key : fallback);
    if (!params) return text;
    return text.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, function (match, name) {
      return HAS.call(params, name) ? String(params[name]) : match;
    });
  }

  function modeLabel(mode) {
    return mode === 'dark' ? T('materials.mode.dark', '暗色') : T('materials.mode.light', '亮色');
  }

  var SPEC = null;          // material_spec result
  var PARAMS = {};          // key -> param definition
  var SECTION_OF = {};      // key -> section id
  var CURRENT = null;       // editable copy
  var BASELINE = null;      // saved copy (dirty-check baseline)
  var MODE = 'light';       // editing light / dark
  var BG_MODE = 'dark';     // preview wallpaper: dark wallpaper / light wallpaper
  var BG_TOUCHED = false;   // once the user flips the wallpaper, stop following the edit mode
  var TAB = 'overview';
  var DIRTY = false;
  var MOUNTED = false;
  // Theme master switch (palette.enabled). When off the loader removes the style, so materials don't apply.
  var MASTER_ON = true;
  var PREVIEW_TIMER = null;
  var PREVIEW_SEQ = 0;
  var PREVIEW_VARS = null;

  /* ---------------- Generic helpers ---------------- */

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
    return Promise.reject(new Error(T('materials.error.panelNotReady', '面板接口尚未就绪')));
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

  /* ---------------- Value read / write ---------------- */

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
      // Dark only stores explicit overrides: a value equal to light drops the key and inherits again.
      var light = lightValue(sectionId, key);
      var darkRoot = CURRENT.dark || (CURRENT.dark = {});
      var darkSection = darkRoot[sectionId] || (darkRoot[sectionId] = {});
      if (equalForKind(value, light, param)) delete darkSection[key];
      else darkSection[key] = value;
      if (!Object.keys(darkSection).length) delete darkRoot[sectionId];
    } else {
      var section = CURRENT[sectionId] || (CURRENT[sectionId] = {});
      section[key] = value;
      // Once the light value changes, dark overrides equal to the new light value are dead; drop them too.
      var dk = CURRENT.dark && CURRENT.dark[sectionId];
      if (dk && HAS.call(dk, key) && equalForKind(dk[key], value, param)) {
        delete dk[key];
        if (!Object.keys(dk).length) delete CURRENT.dark[sectionId];
      }
    }
    markDirty();
  }

  // Dark only stores overrides that differ from light. Returning an item to inherit-from-light would
  // otherwise mean matching the light value by hand -- nobody can line up 34 number / 14 percent params.
  // So the "dark" marker doubles as a clickable clear button.
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
    // Header state (especially the dark bulk-clear button visibility and count) must update immediately:
    // refreshing only the badge leaves the button one frame behind and looks broken.
    renderHeadState();
    schedulePreview();
  }

  /* ---------------- Preview compilation ---------------- */

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
    // Dragging a slider fires continuously: while a request is in flight just mark one "pending" and
    // send the latest params when it returns. Otherwise fast drags pile up dozens of stale runs.
    if (PREVIEW_INFLIGHT) { PREVIEW_PENDING = true; return; }
    PREVIEW_INFLIGHT = true;
    var seq = ++PREVIEW_SEQ;
    var payload = clone(CURRENT);
    var mode = MODE;
    // A response compiled for the other mode (light/dark switched while it was in flight) is
    // stale even if no newer request was sent yet: never paint it, and make sure the current
    // mode gets compiled.
    function stale() {
      if (seq !== PREVIEW_SEQ) return true;
      if (mode !== MODE) { PREVIEW_PENDING = true; return true; }
      return false;
    }
    callEntry('preview_materials', { materials: payload, mode: mode }).then(function (result) {
      PREVIEW_INFLIGHT = false;
      if (stale()) { drainPreview(); return; }
      PREVIEW_VARS = result && result.vars ? result.vars : {};
      paintPreview(result);
      drainPreview();
    }).catch(function (error) {
      PREVIEW_INFLIGHT = false;
      if (stale()) { drainPreview(); return; }
      var meta = byId('mt-pv-meta');
      if (meta) {
        meta.textContent = T('materials.preview.compileFailed', '预览编译失败：{{error}}', {
          error: error && error.message ? error.message : T('materials.error.backendDown', '后端插件可能未启动')
        });
      }
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
    // Component chips consume the same variables: the 10 chips reference component aliases and fall back to global when not customized.
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
    // With compare on and the light/dark mode switched, the right half must show that mode's original variables.
    if (REF_ON) ensureRef(MODE);
    var meta = byId('mt-pv-meta');
    if (meta) {
      meta.textContent = '';
      meta.appendChild(el('b', { text: T('materials.preview.varCount', '{{count}} 个变量', { count: Object.keys(vars).length }) }));
      meta.appendChild(document.createTextNode(T('materials.preview.metaLine', ' · 预设 {{preset}} · 画质 {{quality}} · {{mode}}', {
        preset: presetLabel(CURRENT.preset),
        quality: qualityLabel(CURRENT.quality),
        mode: modeLabel(MODE)
      })));
    }
  }

  // --nts-mt-bg is the base "rgba(tint, opacity x tint strength)"; its alpha is how solid the glass is.
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
      box.textContent = T('materials.preview.readabilityWarn',
        '玻璃基底不透明度 {{percent}}%，压在上面的文字容易看不清 —— 调低「透明度」或「染色浓度」可恢复对比度（当前设置仍然可用，只是观感偏实）。',
        { percent: Math.round(alpha * 100) });
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

  /* ---------------- Navigation groups ---------------- */

  // label / title below are the Chinese source text; they are localized at render time
  // via materials.nav.<id> and materials.group.<id>.
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
      { id: 'baseSurface', title: '基础面', keys: ['opacity', 'tint', 'tintStrength', 'blur', 'blurLevel', 'saturation', 'brightness'] }
    ],
    dye: [
      { id: 'dye', title: '玻璃染色', keys: ['dyePrimary', 'dyeSecondary', 'dyeStrength', 'dyeWarmth', 'dyeAlpha', 'dyeGradient'] }
    ],
    light: [
      { id: 'edge', title: '边缘光', keys: ['edgeIntensity', 'edgeWidth', 'edgeAlpha', 'edgeColor', 'edgeTop', 'edgeBottom', 'edgeLeft', 'edgeRight', 'edgeFade'] },
      { id: 'inner', title: '内部高光', keys: ['innerIntensity', 'innerAlpha', 'innerRange', 'innerPos', 'innerSoftness', 'innerDouble'] },
      { id: 'ambient', title: '环境反射', keys: ['ambientIntensity', 'ambientColor', 'ambientDirection', 'ambientRange', 'ambientBlur', 'ambientAlpha'] },
      { id: 'dynamic', title: '动态光照', keys: ['dynamic', 'dynamicSpeed', 'dynamicIntensity', 'dynamicRange', 'dynamicReflect', 'mouseFollow', 'mouseDelay'] }
    ],
    refract: [
      { id: 'refract', title: '折射', keys: ['refractStrength', 'refractRange', 'refractEdge', 'refractBlur', 'refractAlpha', 'refractLocal'] },
      { id: 'dispersion', title: '色散', keys: ['dispStrength', 'dispRange', 'dispAlpha', 'dispShift', 'dispWidth'] }
    ],
    liquid: [
      { id: 'liquid', title: '液态动态', keys: ['liquid', 'liquidAmp', 'liquidSpeed', 'liquidRange', 'liquidSmooth', 'liquidStrength'] }
    ],
    surface: [
      { id: 'noise', title: '噪点', keys: ['noise', 'noiseStrength', 'noiseSize', 'noiseAlpha', 'noiseDensity'] },
      { id: 'radius', title: '圆角', keys: ['radius', 'radiusCard', 'radiusButton', 'radiusDialog', 'radiusInput'] },
      { id: 'border', title: '边框', keys: ['borderWidth', 'borderAlpha'] },
      { id: 'shadow', title: '阴影', keys: ['shadowStrength', 'shadowSpread', 'shadowDistance', 'shadowBlur', 'innerShadow', 'innerShadowStrength'] }
    ],
    anim: [
      { id: 'switches', title: '开关', keys: ['animation', 'reducedMotion', 'easing'] },
      { id: 'durations', title: '时长', keys: ['enterDuration', 'exitDuration', 'hoverDuration', 'pressDuration', 'expandDuration'] },
      { id: 'feedback', title: '交互反馈', keys: ['hover', 'press', 'spotlight', 'spotRadius', 'spotIntensity', 'spotSpeed'] }
    ]
  };
  /* ---------------- Controls ---------------- */

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
        mark = el('button', { class: 'mt-row-clear', type: 'button',
          title: T('materials.row.clearDarkTitle', '清除这一项的暗色覆盖，回到亮色'),
          text: T('materials.row.clearDark', '暗色 ✕') });
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
      var caption = el('span', { text: value ? T('materials.row.on', '开启') : T('materials.row.off', '关闭') });
      box.addEventListener('change', function () {
        caption.textContent = box.checked ? T('materials.row.on', '开启') : T('materials.row.off', '关闭');
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
          toast(T('materials.toast.badColor', '颜色格式不支持：{{value}}（用 #rrggbb 或 rgb()/rgba()）', { value: next }), 'error');
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
    // The row <label> is not bound to these inputs (two controls share it), so give each an
    // accessible name of its own from the param label.
    var range = el('input', {
      type: 'range', class: 'mt-range', 'aria-label': param.label,
      min: min, max: max, step: step, value: fmtNum(value)
    });
    var number = el('input', {
      type: 'number', class: 'mt-num', 'aria-label': param.label,
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

  /* ---------------- Pane content ---------------- */


  /** 90 params spread over 8 views are hard to find by paging; search spans all views and lists every hit. */
  function renderSearch(pane) {
    var hits = [];
    Object.keys(PARAMS).forEach(function (key) {
      var param = PARAMS[key];
      var hay = (key + ' ' + (param.label || '') + ' ' + (param.section || '')).toLowerCase();
      if (hay.indexOf(SEARCH) >= 0) hits.push(param);
    });
    pane.appendChild(el('p', { class: 'mt-hint', text:
      hits.length
        ? T('materials.search.found', '找到 {{count}} 项（跨全部界面），清空搜索回到当前界面。', { count: hits.length })
        : T('materials.search.none', '没有匹配「{{query}}」的参数。', { query: SEARCH }) }));
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
      pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.group.' + group.id, group.title) }));
      group.keys.forEach(function (key) {
        var param = PARAMS[key];
        if (param) pane.appendChild(buildRow(param));
      });
    });
    if (TAB === 'base') {
      pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.hint.darkGlass',
        '暗色主题下玻璃底会自动压暗（保留你的染色倾向）：宿主的暗色文字是浅色，白底会看不清，所以暗色模式下看到的是深色玻璃。') }));
    }
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.hint.lightDark',
      '亮色与暗色分别调：暗色只保存与亮色不同的值，未覆盖项自动继承亮色。') }));
  }

  function statLine(label, value) {
    var row = el('div', { class: 'mt-row' });
    row.appendChild(el('span', { class: 'mt-row-label', text: label }));
    row.appendChild(el('span', { class: 'mt-row-label', text: String(value) }));
    row.appendChild(el('span', {}));
    return row;
  }

  function renderOverview(pane) {
    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.overview.status', '当前状态') }));
    pane.appendChild(statLine(T('materials.overview.advanced', '高级材质'),
      CURRENT.enabled ? T('materials.overview.enabled', '已启用') : T('materials.overview.disabled', '未启用')));
    pane.appendChild(statLine(T('materials.overview.preset', '材质预设'), presetLabel(CURRENT.preset)));
    pane.appendChild(statLine(T('materials.overview.quality', '性能模式'), qualityLabel(CURRENT.quality)));
    pane.appendChild(statLine(T('materials.overview.darkOverrides', '暗色覆盖'),
      T('materials.overview.itemCount', '{{count}} 项', { count: darkOverrideCount() })));
    pane.appendChild(statLine(T('materials.overview.components', '组件自定义'), Object.keys(CURRENT.components || {}).length + ' / 10'));
    pane.appendChild(statLine(T('materials.overview.paramTotal', '参数总数'),
      T('materials.overview.itemCount', '{{count}} 项', { count: (SPEC.params || []).length })));

    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.overview.quickPresets', '快捷预设') }));
    var grid = el('div', { class: 'mt-grid' });
    PRESET_ORDER().concat(CUSTOM_ORDER()).forEach(function (id) {
      grid.appendChild(presetCard(id));
    });
    pane.appendChild(grid);

    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.overview.limits', '能力边界') }));
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.overview.limitsHint',
      '高级材质只作用于颜色、模糊、折射、光照、圆角、边框与阴影：不改 display/position/尺寸/溢出/z-index，因此不会破坏布局；文本对比度由面板的「可读性自检」兜底。关闭插件或关掉这里的开关后，应用侧会整体撤掉材质层，回到原始主题。') }));
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.overview.fallbackHint',
      '不支持 backdrop-filter 的环境（旧内核 / 硬件加速被禁用）会自动降级为纯色半透明，不会出现半渲染的毛玻璃。') }));
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

  /** User-saved preset names (sorted, so the render order stays stable). */
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
      text: (builtin && builtin.desc) || (custom
        ? T('materials.preset.customDesc', '自定义预设 · 点击整套套用')
        : (PRESET_DESC[id] ? T('materials.presetDesc.' + id, PRESET_DESC[id]) : ''))
    }));
    card.addEventListener('click', function () { applyPreset(id); });
    return card;
  }

  // Chinese source text; localized at render time via materials.presetDesc.<id>.
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
    // A preset replaces the whole set: dark overrides are cleared too, so old overrides don't fight the new preset.
    var clearedDark = countOverrides(CURRENT.dark);
    CURRENT.dark = {};
    if (clearedDark > 0) {
      toast(T('materials.toast.presetClearedDark', '切换预设会清空暗色覆盖：已清除 {{count}} 项（预设是整套替换）。', { count: clearedDark }), 'warn');
    }
    markDirty();
    renderPane();
    renderHeadState();
  }

  /** Save the current five sections as a custom preset (submitted with "apply materials"; the backend merges by name). */
  function saveCurrentAsPreset() {
    var input = byId('mt-preset-name');
    var name = String((input && input.value) || '').trim().slice(0, 40);
    if (!name) { toast(T('materials.toast.presetNameEmpty', '先给预设起个名字。'), 'warn'); return; }
    if (SPEC && SPEC.presets && SPEC.presets[name.toLowerCase()]) {
      toast(T('materials.toast.presetNameTaken', '「{{name}}」和内置预设重名了，换一个。', { name: name }), 'warn');
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
    toast(T('materials.toast.presetSaved', '已存为「{{name}}」，点「应用材质」写入磁盘。', { name: name }), 'ok');
  }

  /** Delete a custom preset: a dedicated entry persists it immediately (apply's merge semantics would re-add it). */
  function deleteCustomPreset(name) {
    if (!name) return;
    // A preset that exists only in the draft (created but never applied) is not
    // on the server: remove it locally; the server call is for saved presets only.
    var saved = !!(BASELINE && BASELINE.custom_presets &&
      Object.prototype.hasOwnProperty.call(BASELINE.custom_presets, name));
    if (!saved) {
      if (CURRENT.custom_presets) delete CURRENT.custom_presets[name];
      if (CURRENT.preset === name) CURRENT.preset = 'custom';
      markDirty();
      renderPane();
      renderHeadState();
      toast(T('materials.toast.presetDeleted', '已删除自定义预设「{{name}}」。', { name: name }), 'ok');
      return;
    }
    // Drop the entry from the draft before the request: an Apply clicked while the
    // delete is in flight must not send the preset again (the backend merges
    // submitted presets into the stored ones, which would bring it back).
    // Only this entry is removed; other unapplied new / edited presets stay.
    var removedParams = CURRENT.custom_presets ? CURRENT.custom_presets[name] : undefined;
    var wasSelected = CURRENT.preset === name;
    if (CURRENT.custom_presets) delete CURRENT.custom_presets[name];
    if (wasSelected) CURRENT.preset = 'custom';
    callEntry('delete_material_preset', { name: name }).then(function (data) {
      var materials = (data && data.materials) || null;
      // The saved baseline follows the server (the delete is persisted), so remaining unapplied edits still show as pending.
      if (BASELINE) {
        if (materials) {
          BASELINE.custom_presets = clone(materials.custom_presets || {});
          BASELINE.preset = materials.preset || 'custom';
        } else {
          if (BASELINE.custom_presets) delete BASELINE.custom_presets[name];
          if (BASELINE.preset === name) BASELINE.preset = 'custom';
        }
      }
      markDirty();
      renderPane();
      renderHeadState();
      toast(T('materials.toast.presetDeleted', '已删除自定义预设「{{name}}」。', { name: name }), 'ok');
    }).catch(function (error) {
      // The delete did not happen: put the entry back unless it was re-created meanwhile.
      if (removedParams !== undefined) {
        if (!CURRENT.custom_presets) CURRENT.custom_presets = {};
        if (!Object.prototype.hasOwnProperty.call(CURRENT.custom_presets, name)) {
          CURRENT.custom_presets[name] = removedParams;
          if (wasSelected && CURRENT.preset === 'custom') CURRENT.preset = name;
        }
        renderPane();
        renderHeadState();
      }
      toast(T('materials.toast.presetDeleteFailed', '删除预设失败：{{error}}', {
        error: error && error.message ? error.message : error
      }), 'warn');
    });
  }

  /** Move (rename) or copy a custom preset inside the draft; other draft presets are untouched. */
  function moveDraftPreset(oldName, newName, keepSource) {
    if (!CURRENT.custom_presets || typeof CURRENT.custom_presets !== 'object') CURRENT.custom_presets = {};
    var params = CURRENT.custom_presets[oldName];
    if (params === undefined) return;
    CURRENT.custom_presets[newName] = clone(params);
    if (!keepSource) {
      delete CURRENT.custom_presets[oldName];
      if (CURRENT.preset === oldName) CURRENT.preset = newName;
    }
  }

  /**
   * Rename (keepSource=false) or duplicate (keepSource=true) a custom preset.
   * Presets that exist only in the draft are handled locally; saved ones go
   * through rename_material_preset / duplicate_material_preset, which persist
   * immediately (apply's merge-by-name would otherwise keep the old name).
   */
  /** Ask for a name with the shared in-page dialog (window.prompt does not work in
   *  Electron). Resolves to the entered text, or null when cancelled. */
  function askPresetName(message, defaultValue) {
    if (typeof window.showPrompt === 'function') {
      return Promise.resolve(window.showPrompt(message, defaultValue, null, {
        inputAttributes: { maxlength: '40' }
      }));
    }
    try {
      return Promise.resolve(window.prompt(message, defaultValue));
    } catch (error) {
      return Promise.resolve(null);
    }
  }

  function renameOrCopyPreset(name, keepSource) {
    if (!name) return;
    var suggestion = keepSource
      ? name + ' ' + T('materials.presets.copySuffix', '副本')
      : name;
    askPresetName(keepSource
      ? T('materials.presets.duplicatePrompt', '复制「{{name}}」为：', { name: name })
      : T('materials.presets.renamePrompt', '把「{{name}}」改名为：', { name: name }), suggestion.slice(0, 40))
      .then(function (answer) {
        if (answer === null || answer === undefined) return;
        commitPresetName(name, String(answer), keepSource);
      });
  }

  function commitPresetName(name, answer, keepSource) {
    var target = String(answer).trim().slice(0, 40);
    if (!target) { toast(T('materials.toast.presetNameEmpty', '先给预设起个名字。'), 'warn'); return; }
    if (!keepSource && target === name) return;
    if (SPEC && SPEC.presets && SPEC.presets[target.toLowerCase()]) {
      toast(T('materials.toast.presetNameTaken', '「{{name}}」和内置预设重名了，换一个。', { name: target }), 'warn');
      return;
    }
    if (CURRENT.custom_presets && HAS.call(CURRENT.custom_presets, target)) {
      toast(T('materials.toast.presetNameExists', '已经有名为「{{name}}」的预设了。', { name: target }), 'warn');
      return;
    }
    var doneText = keepSource
      ? T('materials.toast.presetDuplicated', '已复制为「{{name}}」。', { name: target })
      : T('materials.toast.presetRenamed', '已改名为「{{name}}」。', { name: target });
    var saved = !!(BASELINE && BASELINE.custom_presets && HAS.call(BASELINE.custom_presets, name));
    if (!saved) {
      moveDraftPreset(name, target, keepSource);
      markDirty();
      renderPane();
      renderHeadState();
      toast(doneText, 'ok');
      return;
    }
    // Rename the draft before the request (like deleteCustomPreset): an Apply clicked
    // while the rename is in flight must not submit the old name, which the backend's
    // merge-by-name would write back next to the renamed preset. Rolled back on failure.
    if (!keepSource) {
      moveDraftPreset(name, target, false);
      // Redraw now so the old card (and its buttons) is not clickable with a name
      // that no longer exists in the draft while the request is in flight.
      renderPane();
      renderHeadState();
    }
    callEntry(keepSource ? 'duplicate_material_preset' : 'rename_material_preset',
      { name: name, new_name: target }).then(function (data) {
      var materials = (data && data.materials) || null;
      var finalName = (data && data.name) || target;
      // The draft keeps its unapplied edits: a duplicate copies this preset's draft params.
      if (keepSource) moveDraftPreset(name, finalName, true);
      if (BASELINE && materials) {
        BASELINE.custom_presets = clone(materials.custom_presets || {});
        BASELINE.preset = materials.preset || 'custom';
      }
      markDirty();
      renderPane();
      renderHeadState();
      toast(doneText, 'ok');
    }).catch(function (error) {
      if (!keepSource && CURRENT.custom_presets && HAS.call(CURRENT.custom_presets, target) &&
          !HAS.call(CURRENT.custom_presets, name)) {
        // moveDraftPreset already points the selection back when it still follows the
        // renamed preset; a different preset chosen meanwhile is left alone.
        moveDraftPreset(target, name, false);
        renderPane();
        renderHeadState();
      }
      toast(T('materials.toast.presetRenameFailed', '操作预设失败：{{error}}', {
        error: error && error.message ? error.message : error
      }), 'warn');
    });
  }

  function renderPresets(pane) {
    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.presets.builtin', '材质预设') }));
    var grid = el('div', { class: 'mt-grid' });
    PRESET_ORDER().forEach(function (id) { grid.appendChild(presetCard(id)); });
    pane.appendChild(grid);
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.presets.builtinHint',
      '预设是整套参数替换：切换后五节参数会整体重置为该预设的值，不会残留上一个预设的调校。') }));

    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.presets.mine', '我的预设') }));
    var customIds = CUSTOM_ORDER();
    if (customIds.length) {
      var customGrid = el('div', { class: 'mt-grid' });
      customIds.forEach(function (id) {
        var wrap = el('div', { class: 'mt-preset-wrap' });
        wrap.appendChild(presetCard(id));
        var del = el('button', { class: 'mt-mini mt-preset-del', type: 'button', text: T('materials.presets.delete', '删除') });
        del.addEventListener('click', function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          deleteCustomPreset(id);
        });
        var rename = el('button', { class: 'mt-mini mt-preset-rename', type: 'button', text: T('materials.presets.rename', '改名') });
        rename.addEventListener('click', function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          renameOrCopyPreset(id, false);
        });
        var dup = el('button', { class: 'mt-mini mt-preset-dup', type: 'button', text: T('materials.presets.duplicate', '复制') });
        dup.addEventListener('click', function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          renameOrCopyPreset(id, true);
        });
        var actions = el('div', { class: 'mt-preset-actions' });
        actions.appendChild(rename);
        actions.appendChild(dup);
        actions.appendChild(del);
        wrap.appendChild(actions);
        customGrid.appendChild(wrap);
      });
      pane.appendChild(customGrid);
    } else {
      pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.presets.mineEmpty',
        '还没有自定义预设。把参数调到满意后，在下面起个名存起来（会随「应用材质」写进 theme.json）。') }));
    }
    var saveRow = el('div', { class: 'mt-save-row' });
    var nameInput = el('input', { class: 'mt-input', id: 'mt-preset-name', type: 'text',
      placeholder: T('materials.presets.namePlaceholder', '给当前这套材质起个名（≤40 字）'), maxlength: '40' });
    var saveBtn = el('button', { class: 'mt-mini', type: 'button', text: T('materials.presets.save', '存为预设') });
    saveBtn.addEventListener('click', saveCurrentAsPreset);
    nameInput.addEventListener('keydown', function (event) {
      if (event && event.key === 'Enter') saveCurrentAsPreset();
    });
    saveRow.appendChild(nameInput);
    saveRow.appendChild(saveBtn);
    pane.appendChild(saveRow);
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.presets.mineHint',
      '删除自定义预设会立即写盘（不依赖「应用材质」）；保存则跟随「应用材质」一起提交，导出/分享包里也会带上。') }));

    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.presets.quality', '性能模式') }));
    var modes = el('div', { class: 'mt-grid' });
    ((SPEC && SPEC.quality) || []).forEach(function (item) {
      var card = el('button', {
        class: 'mt-preset' + (CURRENT.quality === item.id ? ' is-active' : ''), type: 'button'
      });
      card.appendChild(el('b', { text: item.label }));
      card.appendChild(el('span', { text: QUALITY_DESC[item.id] ? T('materials.qualityDesc.' + item.id, QUALITY_DESC[item.id]) : '' }));
      card.addEventListener('click', function () {
        CURRENT.quality = item.id;
        markDirty();
        renderPane();
        renderHeadState();
      });
      modes.appendChild(card);
    });
    pane.appendChild(modes);
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.presets.qualityHint',
      '省电：模糊减半、关闭折射/动态/噪点/追光；高品质：加强边缘光与环境反射；极致：全部效果满配。') }));

    pane.appendChild(el('div', { class: 'mt-sub', text: T('materials.presets.reset', '重置') }));
    var reset = el('button', { class: 'mt-mini is-danger', type: 'button', text: T('materials.presets.resetDefaults', '恢复默认材质参数') });
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

  // Chinese source text; localized at render time via materials.qualityDesc.<id>.
  var QUALITY_DESC = {
    saver: '省电优先，效果减半',
    balanced: '均衡，默认档',
    high: '高品质，光效加强',
    ultra: '极致，全部效果满配'
  };
  /* ---------------- Component materials ---------------- */

  function compControl(compId, param) {
    var entry = (CURRENT.components && CURRENT.components[compId]) || {};
    var row = el('div', { class: 'mt-comp-row' });
    row.appendChild(el('span', { text: param.label }));
    var min = num(param.min, 0);
    var max = num(param.max, 1);
    var step = stepOf(param);
    var value = entry[param.key];
    if (value === undefined || value === null) value = lightValue(param.section, param.key);
    var range = el('input', { type: 'range', class: 'mt-range', 'aria-label': param.label,
      min: min, max: max, step: step, value: fmtNum(value) });
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
    [
      ['inherit', T('materials.comp.inherit', '继承全局')],
      ['preset', T('materials.comp.preset', '套用预设')],
      ['custom', T('materials.comp.custom', '自定义')]
    ].forEach(function (pair) {
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
      card.appendChild(el('div', { class: 'mt-comp-note', text: T('materials.comp.presetNote',
        '取「{{preset}}」的透明度 / 模糊 / 圆角 / 边框 / 阴影', { preset: presetLabel(entry.preset) }) }));
    } else {
      card.appendChild(el('div', { class: 'mt-comp-note', text: T('materials.comp.inheritNote', '跟随全局材质，不产生专属变量') }));
    }
    return card;
  }


  /** Component material chips: 10 small tiles consume the component alias variables, so param drags show up live. */
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
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.comp.hint',
      '组件级材质只影响对应组件：选「继承全局」时不产生任何专属变量，绝不会覆盖全局设置。') }));
    (function () {
      var compCount = Object.keys(CURRENT.components || {}).length;
      var bar = el('div', { class: 'mt-comp-bar' });
      bar.appendChild(el('span', { class: 'mt-comp-bar-text', text: compCount
        ? T('materials.comp.customCount', '已自定义 {{count}} 个组件', { count: compCount })
        : T('materials.comp.allInherit', '全部组件跟随全局') }));
      var resetAll = el('button', { class: 'mt-mini', type: 'button', id: 'mt-comp-reset-all', text: T('materials.comp.resetAll', '全部继承全局') });
      resetAll.disabled = compCount === 0;
      resetAll.addEventListener('click', function () {
        CURRENT.components = {};
        renderPane();
        markDirty();
        schedulePreview();
        if (window.NekoPanel) window.NekoPanel.toast(T('materials.toast.compResetAll', '所有组件已回到继承全局'), 'ok');
      });
      bar.appendChild(resetAll);
      pane.appendChild(bar);
    })();
    if (SPEC && SPEC.components) pane.appendChild(buildComponentStrip());
    ((SPEC && SPEC.components) || []).forEach(function (comp) {
      pane.appendChild(componentCard(comp));
    });
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.comp.scopeHint',
      '组件级只覆盖透明度 / 模糊 / 圆角 / 边框 / 阴影五项，颜色仍走全局，避免局部配色跳脱。') }));
    pane.appendChild(el('p', { class: 'mt-hint', text: T('materials.comp.stripHint',
      '缩略块直接吃组件变量：选「继承全局」时回退全局值，选「自定义」时立刻变化（只示意颜色 / 圆角 / 模糊 / 边框 / 阴影）。') }));
    schedulePreview();
  }

  /* ---------------- Header state ---------------- */

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
    // Dark overrides: count the keys that actually differ (added, removed or changed), not
    // the difference in override counts -- editing an existing override changes no count.
    var curDark = CURRENT.dark || {};
    var baseDark = BASELINE.dark || {};
    var darkSections = {};
    Object.keys(curDark).forEach(function (section) { darkSections[section] = 1; });
    Object.keys(baseDark).forEach(function (section) { darkSections[section] = 1; });
    Object.keys(darkSections).forEach(function (section) {
      var left = (curDark[section] && typeof curDark[section] === 'object') ? curDark[section] : {};
      var right = (baseDark[section] && typeof baseDark[section] === 'object') ? baseDark[section] : {};
      var keys = {};
      Object.keys(left).forEach(function (key) { keys[key] = 1; });
      Object.keys(right).forEach(function (key) { keys[key] = 1; });
      Object.keys(keys).forEach(function (key) {
        if (HAS.call(left, key) !== HAS.call(right, key) ||
          !equalForKind(left[key], right[key], PARAMS[key])) total++;
      });
    });
    return total;
  }

  function renderBadge() {
    var badge = byId('mt-dirty');
    if (!badge) return;
    if (DIRTY) {
      badge.textContent = T('materials.head.dirty', '材质改动 {{count}} 项待应用', { count: diffCount() });
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
      darkBtn.textContent = T('materials.head.clearDarkCount', '清除暗色覆盖（{{count}}）', { count: nDark });
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
        type: 'button', text: T('materials.nav.' + tab.id, tab.label)
      });
      button.addEventListener('click', function () {
        TAB = tab.id;
        // Switching views exits search: a lingering query makes the tab click look like it did nothing.
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
  /* ---------------- Shell and preview ---------------- */

  function bgLabel() {
    return BG_MODE === 'light'
      ? T('materials.preview.bgLight', '背景：浅色')
      : T('materials.preview.bgDark', '背景：深色');
  }

  function applyBgMode() {
    var stage = byId('mt-pv');
    if (stage) stage.setAttribute('data-bg', BG_MODE);
    var ref = byId('mt-pv-ref');
    if (ref) ref.setAttribute('data-bg', BG_MODE);
    var btn = byId('mt-pv-bg');
    if (btn) btn.textContent = bgLabel();
  }

  // The preview covers the 6 component kinds the spec requires: glass card (chat) / bubble / button /
  // input / dialog / sidebar. Each consumes its own component alias (--nts-mt-c-*-<comp>) and falls back
  // to global when not customized -- otherwise dragging on the "component materials" page wouldn't move the preview.
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
    msg.appendChild(el('span', { class: 'mt-pv-tag', text: T('materials.sample.tag', '友') }));
    msg.appendChild(document.createTextNode(T('materials.sample.message', '玻璃材质实时预览')));
    stack.appendChild(msg);

    var row = el('div', { class: 'mt-pv-row' });
    row.appendChild(el('div', { class: 'mt-pv-btn', text: T('materials.sample.button', '普通按钮') }));
    row.appendChild(el('div', { class: 'mt-pv-input' }));
    var side = el('div', { class: 'mt-pv-side' });
    side.appendChild(el('i', {}));
    side.appendChild(el('i', {}));
    side.appendChild(el('i', {}));
    row.appendChild(side);
    stack.appendChild(row);

    var pop = el('div', { class: 'mt-pv-pop' });
    var popHead = el('div', { class: 'mt-pv-pop-head' });
    popHead.appendChild(el('span', { text: T('materials.sample.dialogTitle', '弹窗标题') }));
    popHead.appendChild(el('span', { class: 'mt-pv-x', text: '✕' }));
    pop.appendChild(popHead);
    pop.appendChild(el('div', { class: 'mt-pv-pop-body', text: T('materials.sample.dialogBody', '弹窗内容会吃「弹窗」这一组件的材质。') }));
    stack.appendChild(pop);
    return stack;
  }

  // Original vs current: the right half is backed by variables compiled from param defaults, so slider
  // drags show the before/after directly. The cache is per mode -- light/dark compile differently, and
  // mixing them would paint dark originals onto the light preview.
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
    // Bump the sequence even on a cache hit: a request still in flight for the other mode
    // must not land afterwards and repaint the reference with that mode's values.
    var seq = ++REF_SEQ;
    if (REF_CACHE[mode]) { paintRef(REF_CACHE[mode]); return; }
    callEntry('preview_materials', { materials: deepDefaults(), mode: mode }).then(function (result) {
      if (seq !== REF_SEQ) return;
      REF_CACHE[mode] = (result && result.vars) || {};
      paintRef(REF_CACHE[mode]);
    }).catch(function () {
      // A failed compile of the original material must not break the preview: leave it empty, cards use CSS fallbacks.
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
    title.appendChild(el('span', { id: 'mt-pv-mode-label',
      text: T('materials.preview.title', '实时预览 · {{mode}}', { mode: modeLabel(MODE) }) }));
    title.appendChild(el('span', { class: 'mt-pv-off', id: 'mt-pv-off', text: T('materials.preview.off', '材质未启用') }));
    syncOffTag();
    title.appendChild(el('button', {
      class: 'mt-pv-bg', type: 'button', id: 'mt-pv-compare',
      title: T('materials.preview.compareTitle', '右半屏显示参数默认值下的原始材质，拖滑块时能直接对比调整前后'),
      text: T('materials.preview.compare', '对比原始'),
      'on:click': function () { toggleCompare(); }
    }));
    title.appendChild(el('button', {
      class: 'mt-pv-bg', type: 'button', id: 'mt-pv-bg',
      title: T('materials.preview.bgTitle', '切换预览壁纸深浅：浅色壁纸看浅色场景是否看得清，深色壁纸看白玻璃反光'),
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

    // A/B compare layer: the right half shows the original material, the left half the current settings (clip-path cut in CSS).
    var ref = el('div', { class: 'mt-pv-ref', id: 'mt-pv-ref', 'data-bg': BG_MODE });
    ref.appendChild(el('div', { class: 'mt-pv-wall' }));
    ref.appendChild(buildStack());
    ref.appendChild(el('span', { class: 'mt-pv-side-label', text: T('materials.preview.original', '原始') }));
    stage.appendChild(ref);
    stage.appendChild(el('span', { class: 'mt-pv-side-label mt-pv-label-cur', id: 'mt-pv-label-cur', text: T('materials.preview.current', '当前') }));

    // Spotlight preview: same --nts-mt-mx/-my variables as the loader, so it matches the app window.
    stage.addEventListener('pointermove', function (event) {
      var rect = stage.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      var x = ((event.clientX - rect.left) / rect.width) * 100;
      var y = ((event.clientY - rect.top) / rect.height) * 100;
      stage.style.setProperty('--nts-mt-mx', x.toFixed(2) + '%');
      stage.style.setProperty('--nts-mt-my', y.toFixed(2) + '%');
      // Light direction uses the same algorithm as the app window: from center toward the pointer, sheen angle follows.
      var ang = Math.atan2(y - 50, x - 50) * 180 / Math.PI;
      if (ang < 0) ang += 360;
      stage.style.setProperty('--nts-mt-light-angle', ang.toFixed(1) + 'deg');
    }, { passive: true });

    wrap.appendChild(stage);
    wrap.appendChild(el('div', { class: 'mt-pv-meta', id: 'mt-pv-meta', text: T('materials.preview.compiling', '正在编译预览…') }));
    // Readability hint: the more solid the glass, the harder text on it is to read. Warn only, never block -- users may tune it as they like.
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
    title.appendChild(document.createTextNode(T('materials.head.title', '高级材质')));
    title.appendChild(el('span', { class: 'mt-tag', text: 'v' + ((SPEC && SPEC.version) || 1) }));
    head.appendChild(title);
    head.appendChild(el('span', { class: 'mt-head-spacer' }));
    var searchBox = el('input', { class: 'mt-search', id: 'mt-search',
      type: 'search', placeholder: T('materials.head.searchPlaceholder', '搜索参数（如 色散 / 圆角 / blur）') });
    searchBox.value = SEARCH;
    searchBox.addEventListener('input', function () {
      SEARCH = String(searchBox.value || '').trim().toLowerCase();
      renderPane();
    });
    head.appendChild(searchBox);
    head.appendChild(el('span', { class: 'mt-head-spacer' }));
    head.appendChild(el('span', { class: 'mt-dirty', id: 'mt-dirty' }));
    var darkClear = el('button', { class: 'mt-mini mt-dark-clear', id: 'mt-dark-clear', type: 'button', text: T('materials.head.clearDark', '清除暗色覆盖') });
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
    enableWrap.appendChild(el('span', { text: T('materials.head.enable', '启用') }));
    head.appendChild(enableWrap);

    // "Follow theme" is the inverse of lock: turning it off locks the materials, so switching themes keeps these hand-tuned params.
    var followWrap = el('label', { class: 'mt-switch',
      title: T('materials.head.followTitle', '打开时，切换主题会用主题自带的材质替换当前设置；关掉则保留你手调的这套材质') });
    var followBox = el('input', { type: 'checkbox', id: 'mt-follow' });
    followBox.checked = !CURRENT.lock;
    followBox.addEventListener('change', function () {
      CURRENT.lock = !followBox.checked;
      markDirty();
      if (window.NekoPanel) {
        window.NekoPanel.toast(followBox.checked
          ? T('materials.toast.followOn', '材质将跟随主题切换')
          : T('materials.toast.followOff', '材质已锁定：切换主题时保留当前设置'), 'ok');
      }
    });
    followWrap.appendChild(followBox);
    followWrap.appendChild(el('span', { text: T('materials.head.follow', '跟随主题') }));
    head.appendChild(followWrap);

    var modeSel = el('select', { class: 'mt-select', id: 'mt-mode', title: T('materials.head.modeTitle', '分别编辑亮色与暗色材质') });
    [['light', modeLabel('light')], ['dark', modeLabel('dark')]].forEach(function (pair) {
      var opt = el('option', { value: pair[0], text: pair[1] });
      if (MODE === pair[0]) opt.setAttribute('selected', '');
      modeSel.appendChild(opt);
    });
    modeSel.addEventListener('change', function () {
      MODE = modeSel.value === 'dark' ? 'dark' : 'light';
      renderPane();
      // The "clear dark overrides" button is only shown in dark mode: refresh it right away.
      renderHeadState();
      buildPreviewTitle();
      schedulePreview();
    });
    head.appendChild(modeSel);

    var qualitySel = el('select', { class: 'mt-select', id: 'mt-quality', title: T('materials.head.qualityTitle', '性能模式') });
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

    var resetBtn = el('button', { class: 'mt-mini', type: 'button', text: T('materials.head.revert', '回到已保存') });
    resetBtn.addEventListener('click', function () {
      resetLocal();
      toast(T('materials.toast.reverted', '已丢弃材质改动'), 'ok');
    });
    head.appendChild(resetBtn);
    shell.appendChild(head);

    // With the master switch off, ticking "enable" does nothing (the loader drops the whole layer); without a hint it fails silently.
    var masterWarn = el('div', { class: 'mt-master-warn', id: 'mt-master-warn', text: T('materials.head.masterOff',
      '主题总开关（面板上方的「启用主题」）已关闭 —— 高级材质暂时不会生效。') });
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

  /** Clear every dark override at once. */
  // Dark only stores differences; clearing item by item is too slow when dropping a whole dark tuning.
  function clearAllDark() {
    var n = countOverrides(CURRENT.dark);
    if (!n) return;
    CURRENT.dark = {};
    renderPane();
    markDirty();
    schedulePreview();
    if (window.NekoPanel) window.NekoPanel.toast(T('materials.toast.darkCleared', '已清除 {{count}} 项暗色覆盖，全部回到亮色', { count: n }), 'ok');
  }

  function countOverrides(dark) {
    var total = 0;
    Object.keys(dark || {}).forEach(function (section) {
      var box = dark[section];
      if (box && typeof box === 'object') total += Object.keys(box).length;
    });
    return total;
  }

  /** When the material switch is off the preview still renders (for tuning), so mark that it doesn't reflect the windows. */
  function syncOffTag() {
    var tag = byId('mt-pv-off');
    if (!tag) return;
    tag.style.display = (CURRENT && CURRENT.enabled) ? 'none' : '';
  }

  function buildPreviewTitle() {
    if (!BG_TOUCHED) BG_MODE = MODE === 'dark' ? 'dark' : 'light';
    applyBgMode();
    var label = byId('mt-pv-mode-label');
    if (label) label.textContent = T('materials.preview.title', '实时预览 · {{mode}}', { mode: modeLabel(MODE) });
  }

  /* ---------------- Public API ---------------- */

  /**
   * Make the on-disk materials the new baseline.
   * options.submitted: the snapshot submitted on save (only passed on the save path). If the user
   * edited materials again while saving / reading back (CURRENT no longer equals the submitted
   * snapshot), only the baseline is updated and those new edits are kept, never silently overwritten.
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
      head.appendChild(el('h3', { class: 'mt-head-title', text: T('materials.head.title', '高级材质') }));
      head.appendChild(el('span', { class: 'mt-hint', text: T('materials.error.specFailed', '参数表读取失败：{{error}}', {
        error: error && error.message ? error.message : T('materials.error.backendDown', '后端插件可能未启动')
      }) }));
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

  // Boot once the DOM is ready and the i18n bundle has loaded (whenI18nReady also times out, so it never blocks).
  function start() {
    if (typeof window.whenI18nReady === 'function') window.whenI18nReady(boot);
    else boot();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
