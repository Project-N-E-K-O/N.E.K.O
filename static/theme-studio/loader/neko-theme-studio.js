/*!
 * neko-theme-studio.js — N.E.K.O 主题工坊 · 浏览器侧运行时
 * ---------------------------------------------------------------------------
 * 职责：把主题工坊产出的调色板 JSON 变成应用各窗口里的 CSS 变量。
 *
 * 背景（为什么是轮询）：
 *   主题工坊面板与聊天窗口可以同时打开，面板改色后窗口要立刻跟着变。轮询是最
 *   简单也最稳的方案：面板 POST 落盘，窗口每 1.5s 拉一次同源的
 *   /api/theme_studio/theme.json。本体内置后不存在跨源问题，也不需要任何插件
 *   端口或宿主唤醒机制。
 *
 * 设计要点：
 *   1. 变量注入 <style>，不写 CSS 文件 → 没有 HTTP 缓存问题。
 *   2. localStorage 缓存 → 启动瞬间即生效，无闪烁；插件服务不可用时主题仍在。
 *   3. fetch 用 cache:'no-store' + 时间戳查询串 → 穿透可能的启发式缓存。
 *   4. 只接受白名单内的值格式与 --nts- 前缀变量名（纵深防御，服务端也校验）。
 *   5. 无依赖、无副作用：任何失败都静默降级，绝不抛错影响宿主应用。
 */
(function () {
  'use strict';

  // 重复注入哨兵（v1.5.23）：模板补丁与 self-upgrade 换表都可能让本脚本在同一页面
  // 执行第二次。没有这道闸门时会出现两份轮询定时器、两套 pointer/scroll 监听，
  // 变量被重复写入同一 <style>（闪烁 + 白烧 CPU），而且两边状态互不知情。
  if (window.__NTS_LOADER_LOADED__) return;
  window.__NTS_LOADER_LOADED__ = true;

  // ---------------------------------------------------------------- 配置常量

  var PLUGIN_ID = 'neko_theme_studio';
  var STYLE_ID = 'nts-dynamic';
  var CACHE_KEY = 'neko.themeStudio.palette';
  // 自检快照键：见文件末尾 persistSnapshot()。
  var SNAPSHOT_KEY = 'neko.themeStudio.lastDiagnose';
  // 本体内置：调色板接口与窗口同源，没有插件端口，也没有基础地址覆盖。
  var API_BASE = '/api/theme_studio';
  var POLL_INTERVAL_MS = 1500;
  // 调色板请求超时（v0.1.11 P0）。原生 fetch 没有超时：端点假死（TCP 连上但不回
  // 响应）时 Promise 永远 pending，fetchPalette 的 inflight 就永远是 true，
  // 1.5s 的轮询被永久卡死 —— 症状是「面板改了色，窗口再也不更新，控制台全静默」。
  // 超时算一次网络失败，走自愈退避（见 reviveService）。
  var FETCH_TIMEOUT_MS = 8000;
  // 插件时代这里放的是「唤醒插件服务」的退避参数（csrf-token → /plugins/refresh
  // → /plugin/<id>/start）。本体内置后没有需要唤醒的宿主：加载器拉的就是本体自己
  // 的路由，后端没起来时页面本身也打不开。下面三个常量仅为兼容残留引用而保留。
  var REVIVE_BASE_DELAY_MS = 1200;
  var REVIVE_MAX_DELAY_MS = 30000;
  var REVIVE_MAX_ATTEMPTS = 40;
  // 自升级：已开窗口里的脚本无法被替换（浏览器已执行完），当磁盘上的加载器比
  // 本实例新时，唯一的接管办法是重新加载本页面。这是「面板新增了一个变量，用户
  // 不重启就永远看不到效果」的根治手段 —— 旧脚本会因为白名单里没有该变量而静默
  // 丢弃值，且 apply_theme 仍返回 succeeded，面板不报错，极难自查。
  var SELF_UPGRADE_MIN_INTERVAL_MS = 60000;
  // 守卫记录「版本@时间戳」。同一个版本在冷却期内不重复重载（防死循环），
  // 冷却期一过就再试一次 —— 否则一次缓存失误会把本会话的升级能力永久掐死。
  var SELF_UPGRADE_COOLDOWN_MS = 600000;
  var SELF_UPGRADE_GUARD_KEY = 'neko.themeStudio.selfUpgrade';
  var selfUpgradeAt = 0;
  var DEBUG = false;

  var VAR_PREFIX = '--nts-';
  // 脚本自身版本（不是调色板版本）。部署漂移是「面板改了色、本体没反应」的
  // 常见原因之一：static\neko-theme-studio.js 还是旧的、CSS 已是新的
  // （或反之），此时门控属性与样式表规则对不上，颜色永远不变且没有报错。
  // 把它暴露在 diagnose() 里，便于一眼看出是不是版本漂移。
  // 1.4.0: 打开页面时自检样式表是否陈旧并强制换新；气泡文字色彻底按角色作用域
  //        （AI 色不再串染用户/工具/系统正文），修复「改 AI 文字色用户文字也变」。
  // 1.4.2: 值校验不再默认按颜色处理（kind='auto' 兜底），并在检测到磁盘上有更新的
  //        加载器时自行刷新页面 —— 修复「新加变量在已开窗口里被静默丢弃」。
  // 1.5.0: theme.json 不再有 version 计数（改用 updated_at 标记 + 原始 JSON 逐字比较）；
  //        新增 image 类型（--nts-chat-bg-image，聊天背景图）。
  // 1.5.13: 液态玻璃静态层（契约 _work/FEATURE-CONTRACT-0.2.0.md §2）：rim 边缘反射光、
  //        sheen 掠光、noise 雾面颗粒，三个变量门控、非玻璃主题零效果；新增
  //        --nts-glass-rim / --nts-glass-sheen / --nts-glass-noise 的 kind 登记。
  // 1.5.14: Apple Liquid Glass 材质层（契约 _work/FEATURE-CONTRACT-0.3.0.md）：
  //        lens 透镜亮环 / thickness 厚度 / specular 指针镜面，滚动收敛与按压形变。
  // 1.5.15: 光效引擎（契约 _work/FEATURE-CONTRACT-0.4.0.md）：ambient 指针光源、
  //        glass-dispersion 边缘分光、glass-caustic 流动焦散；修掉 0.3.0 里
  //        「黑色遮罩盖死镜面/掠光」与「sheen 动画无条件空转」两个缺陷；窗口
  //        不可见时暂停动画；指针同时写 --nts-light-x/y 供 ambient 使用。
  // 1.5.16: 修复 0.4.0 的回归 —— 光效层曾挂在 `.compact-export-history-scroll` 上，
  //        而该表面是 position:static（刻意不加 relative，避免改掉 ::before 磨砂层
  //        的锚点），absolute + inset:0 的 ::after 于是铺到更外层的定位祖先上，
  //        表现为历史/导出面板上方多出一整块直角的亮屏。现在只对有确定包含块的
  //        6 个表面做光效层。
  // 1.5.17: 0.4.2 修复「每个主题都有一层直角光屏」——根因是光效层/面板装饰层的
  //        absolute 伪元素挂在 position:static 的宿主上（.composer-input-shell、
  //        .message-bubble-*、.compact-export-history-scroll），一路逃到
  //        position:fixed 的 .compact-export-history-anchor 大盒子上铺满。现在
  //        ensureGlassContainment() 只对计算值为 static 的宿主补 inline relative；
  //        面板装饰层本身改挂 scroll::after（z-index:-1、圆角 inherit、内发光）。
  // 1.5.18: 0.4.3 结构性修正「面板下面又多了」——根因是历史消息浮层
  //        `.compact-export-history-scroll` 根本不是面板：无圆角、无背景、高度按
  //        `--compact-export-history-region-height` 预留槽位定死，消息少时比气泡
  //        实际占位更高。0.4.2 把面板级装饰改挂在它的 ::after 上，于是装饰跟着
  //        铺到气泡下方那块空槽里。现在面板级光效全部改挂紧凑态真实面板
  //        `.compact-chat-surface-frame::after`（999px 圆角 + overflow:hidden，
  //        装饰天然被裁进胶囊），磨砂/底色挂它的 ::before；历史浮层只保留气泡级
  //        装饰。同步移除 scroll 上的 rim box-shadow 与包含块补丁。
  // 1.5.19: 0.4.8 修「鼠标移动过去不一定有光效动态改变」。四个原因：
  //        ① 旧版只在 pointermove 时重绘，指针不动而页面在滚（含嵌套滚动容器）/
  //        窗口 resize / 新消息插入时，高光停在旧坐标 —— 现在滚动走捕获阶段、
  //        resize 与 DOM 重建后都会重绘；② MutationObserver 旧版是 350ms 防抖，
  //        聊天流式输出时计时器被无限重置 → 表面列表永不刷新（新气泡没有光效），
  //        改为 220ms 节流；③ 新插入的表面旧版要等下一次指针移动才拿到坐标，
  //        现在重建后立刻补一次；④ 旧版 0.1% 字符串比较 + 瞬时跳变，小范围移动
  //        被吃掉、观感生硬 —— 改为逐帧向目标插值 22%（收敛 0.06% 后停帧）。
  //        pointermove/pointerdown/pointerover 全部用捕获阶段，组件 stopPropagation
  //        也不会把指针事件吞掉。
  //   1.6.40 调色板轮询连续失败时退避（上限 60s）；诊断上报遇 401/403 停止，
  //          失败后 15s 再试，不再每拍重发。
  //   1.6.41 自升级重载只在聊天 / 字幕 / 提示 / HUD 窗口进行，编辑器页面不再被自动刷新。
  //   1.6.42 窗口重新获得焦点 / 变为可见时清掉轮询退避，立即重新拉取。
  //   1.6.43 同源请求带会话 cookie（远程窗口不再 401）；新调色板直接应用时作废仍在预热的旧调色板。
  //   1.6.44 预热闸门按调色板内容识别（同一秒两次切换不再停在旧主题）；预热超时放行。
  //   1.6.45 材质规则放进 @layer nts-theme.materials，不再被调色板层的 !important 覆盖；
  //          修掉设置页材质段里提前闭合的注释。
  //   1.6.46 紧凑聊天（/chat）外层包裹在材质开启时保持透明，只有胶囊带材质。
  var SCRIPT_VERSION = '1.6.46';

  // 允许的颜色值形态。刻意保守：只放行纯颜色，杜绝把任意 CSS 注进变量。
  var RE_HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
  var RE_RGB = /^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
  var RE_HSL = /^hsla?\(\s*-?[0-9]{1,3}(\.[0-9]+)?\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
  // CSS 命名颜色（含 transparent 与 currentcolor）。不收 inherit/initial/unset/revert：
  // 那四个会把颜色继承成父级，正文色被带偏就是「串色」投诉的来源。
  // transparent 必须显式列出：后端 RE_KEYWORD_COLOR 一直收它，但本表漏了，
  // 于是「把背景设成透明」在面板上显示成功、门控却不生成 —— 声明永远不匹配。
  var COLOR_NAMES = ('transparent aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue' +
    ' blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson' +
    ' currentcolor cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta' +
    ' darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise' +
    ' darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia' +
    ' gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink' +
    ' indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral' +
    ' lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray' +
    ' lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue' +
    ' mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose' +
    ' moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod' +
    ' palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple' +
    ' rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna' +
    ' silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal' +
    ' thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen').split(' ');
  var NAMED_COLOR = Object.create(null);
  for (var ci = 0; ci < COLOR_NAMES.length; ci += 1) { NAMED_COLOR[COLOR_NAMES[ci]] = 1; }
  function isNamedColor(t) { return NAMED_COLOR[String(t).toLowerCase()] === 1; }
  var RE_PCT = /^[0-9]{1,3}(\.[0-9]+)?%$/;
  var RE_LEN = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|%|pt|vh|vw)$/i;
  var RE_FONT = /^[A-Za-z0-9\u4e00-\u9fff ,'"\-_.]{1,200}$/;
  // --nts-chat-blur 的消费方是 `backdrop-filter: var(--nts-chat-blur)`，
  // 因此值必须是 blur(...) 形态；裸长度会被浏览器整条丢弃。
  var RE_BLUR = /^blur\(\s*-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)\s*\)$/i;
  // 裸长度里对 blur 合法的写法：blur() 只接受绝对长度，% / vh / vw 会让
  // backdrop-filter 整条失效。RE_LEN 为 shadow 的偏移量必须放行它们，所以单独用这条
  // 更严的正则，避免生成 blur(18%) 这种「校验通过、CSS 无效」的值。
  var RE_BLUR_BARE = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)$/i;
  // --nts-chat-opacity 桥接到原生的 --neko-chat-opacity-factor（乘子），取值 0-1。
  // 曾经缺失这个 kind，导致面板/后端都接受、而本脚本按颜色校验后静默丢弃 ——
  // 一个「看起来能调、实际没反应」的死控件（由独立验证者实测发现）。
  // 允许 1.0 / 1.00 这类零填充写法（用户最自然地把「100%」写成 1.0）；
  // 仍拒绝 1.5 / 2 / -0.3 这类超出 0-1 的值。
  var RE_NUMBER = /^(?:0|1|0?\.[0-9]+|1\.0+)$/;
  // 行高：无单位倍数 0.8–3.0（最多两位小数）。与后端 RE_RATIO 逐字相同。
  var RE_RATIO = /^[0-9](?:\.[0-9]{1,2})?$/;
  // 时间值（v0.1.11，呼吸节奏）：3s / 1.5s / 800ms，裸数字按秒（3 = 3s）。
  // 上限 60s —— 再长的循环用户看不出"在动"，也没有实际意义。
  var RE_TIME = /^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$/i;
  var RE_TIME_BARE = /^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$/;
  var TIME_MAX_SECONDS = 60;
  // 百分比乘子解析：返回 0-1 的小数，非法或 >100% 返回 null。
  // 注意 RE_PCT 的捕获组是小数部分、不是整数部分（早期误用 pm[1] 导致
  // `50%` 被判非法、`33.3%` 被算成 0.003）。
  var RE_PCT_FULL = /^([0-9]{1,3}(?:\.[0-9]+)?)%$/;
  function pctToFraction(v) {
    var m = RE_PCT_FULL.exec(v);
    if (!m) return null;
    var n = parseFloat(m[1]);
    if (!(n >= 0) || n > 100) return null;
    return Math.round(n / 100 * 10000) / 10000;
  }

  /**
   * 按「括号外空白」切分阴影串（面板 shadowTokens / 后端 _shadow_tokens 同构）。
   * 早期这里用 v.split(/\s+/)，会把 rgba(0, 0, 0, .35) 拆成 7 个碎 token，
   * 于是「面板接受、后端落盘、loader 静默丢弃」—— 最常见的 box-shadow 写法
   * 反而永远不生效。括号不配对返回 []。
   */
  function shadowTokens(value) {
    var tokens = [];
    var depth = 0;
    var buf = '';
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

  // 非颜色项：这些变量允许长度/百分比/字体名/数值，而不是颜色。
  // 必须与插件端 __init__.py 的 _value_kind()/LENGTH_VARS/BLUR_VARS 保持一致，
  // 否则面板接受、应用侧静默丢弃（曾实测出两例：blur(24px) 被判非法；
  // --nts-chat-opacity 缺 number 分支而整项被丢）。
  var NON_COLOR_VARS = {
    '--nts-radius': 'length',
    '--nts-font-size': 'length',
    // Region-specific radii are independent knobs, not aliases of --nts-radius.
    '--nts-bubble-radius': 'length',
    '--nts-shell-radius': 'length',
    '--nts-card-radius': 'length',
    '--nts-modal-radius': 'length',
    '--nts-popover-radius': 'length',
    '--nts-rec-radius': 'length',
    // 聊天背板（消息列表底板）的圆角。和 --nts-radius 分开是因为后者是通用圆角，
    // 同时打在按钮/输入面板上；背板要能单独变圆而不能连带改圆按钮。
    '--nts-chat-panel-radius': 'length',
    // 聊天背景图：值是「none」或指向插件 backgrounds/ 的绝对 url(...)。
    // 它必须走单独的 image 分支：通用黑名单 RE_BAD 挡 url( 是给颜色值用的。
    '--nts-chat-bg-image': 'image',
    '--nts-font-family': 'font',
    '--nts-chat-blur': 'blur',
    '--nts-shadow': 'shadow',
    '--nts-chat-opacity': 'number',
    // 「聊天背景上那层虚化」的总开关（0 = 完全清透，1 = 原生白纱+背景模糊）。
    // 与 --nts-chat-opacity 同为 0–1 乘子，走同一个 number 分支。
    '--nts-bubble-veil': 'number',
    // 背景图亮度：0-1 乘子，1 = 原图，0 = 全黑。值种类 number，走同一个分支。
    '--nts-chat-bg-image-brightness': 'number',
    // 背景图适应方式：cover（铺满，可能裁切）/ contain（整图可见，两侧留底色）。
    // 值种类 keyword —— 只收这两个关键字，别的一律丢弃（不会回退成奇怪尺寸）。
    '--nts-chat-bg-fit': 'keyword',
    // 圆角几何形状：round（普通圆角）/ continuous（连续曲率 = 超椭圆）。
    // 同样是 keyword 型 —— 值域写死在 KEYWORD_VALUES 里，别的一律丢弃。
    '--nts-corner-style': 'keyword',
    // 背景图顶上那层「雾」= 应用给历史列表容器加的 mask-image 顶部渐隐。
    // 1 = 与应用原生一致，0 = 渐隐完全取消。与上一个同走 number 分支。
    '--nts-chat-top-fade': 'number',
    // ===== 面板边缘光效 / 玻璃拟态（v0.1.11）=====
    // 全部可选；不配置则整块 CSS 不加载，外观与原生一致。
    // 挂载点见 targets.js 的 NTS_GLOW：
    //   外发光 -> anchor::before（scroll 自带 mask-image，外发光只能透出 4px）
    //   描边/四角/反射/内阴影 -> anchor::after
    //   磨砂层 -> scroll::before，z-index:-1（夹在容器背景与消息内容之间）
    '--nts-panel-glow-color': 'color',
    '--nts-panel-glow-strength': 'number',
    '--nts-panel-glow-size': 'length',
    '--nts-panel-glow-pulse': 'number',
    '--nts-panel-glow-speed': 'time',
    '--nts-panel-border-color': 'color',
    '--nts-panel-border-width': 'length',
    '--nts-panel-corner-glow': 'number',
    '--nts-panel-edge-gradient': 'number',
    '--nts-panel-glass-tint': 'color',
    '--nts-panel-glass-blur': 'blur',
    '--nts-panel-sheen': 'number',
    '--nts-panel-inner-glow': 'number',
    // ===== 液态玻璃 / 自定义渐变（v0.1.11，契约 _work/FEATURE-CONTRACT-0.1.11.md §1）=====
    // 三端（Python _value_kind / 面板 isSafeValue / 本表）必须同 kind：任一端不认识
    // 新变量，就会出现「面板能存、加载器静默丢值、用户看到改了没反应」。
    // 这里的 map 名字虽然叫 NON_COLOR_VARS，但 color 也显式登记：'auto' 兜底会
    // 额外放行长度值（见 isSafeValue），比后端的 color 判定宽松，登记后才逐字一致。
    '--nts-glass-filter': 'filter',
    '--nts-glass-tint': 'color',
    '--nts-glass-highlight': 'color',
    // 液态玻璃静态层（v1.5.13，契约 0.2.0 §1）：rim 是颜色，sheen/noise 是 0~1 强度。
    // sheen/noise 走 number kind（允许面板/JSON 用 "55%" 写法，sanitizeVars 归一成 0.55）。
    // 三端（Python _value_kind / 面板 isSafeValue / 本表）必须同 kind，否则又会出现
    // 「面板能存、加载器静默丢值、用户看到改了没反应」。
    '--nts-glass-rim': 'color',
    '--nts-glass-sheen': 'number',
    '--nts-glass-noise': 'number',
    // 液态玻璃材质（v1.5.14，契约 0.3.0 §1）：lens/thickness/specular 都是 0~1 连续量。
    // 同样是 number kind（面板/JSON 可写 "70%" 或 0.7，sanitizeVars 归一到 0–1）。
    '--nts-glass-lens': 'number',
    // v0.12.0：背景图位置 / 平铺是关键字型；行高是无单位倍数（ratio）。
    '--nts-chat-bg-position': 'keyword',
    '--nts-chat-bg-repeat': 'keyword',
    '--nts-line-height': 'ratio',
    '--nts-glass-thickness': 'number',
    '--nts-glass-specular': 'number',
    // 光效引擎（v1.5.15，契约 0.4.0 §1）：指针光源 / 边缘分光 / 流动焦散，都是 0~1。
    // 未配置（absent）== 零效果；配置了非法值 == 整条丢弃并 warn 一次（noteRejected）。
    '--nts-ambient-glow': 'number',
    '--nts-glass-dispersion': 'number',
    '--nts-glass-caustic': 'number',
    '--nts-bubble-glass-filter': 'filter',
    '--nts-bubble-glass-tint': 'color',
    '--nts-chat-gradient': 'gradient',
    // v0.13.0：文字渐变，与背景渐变同一套 linear/radial 校验。
    '--nts-text-gradient': 'gradient',
    '--nts-panel-gradient': 'gradient',
    '--nts-user-bubble-gradient': 'gradient',
    '--nts-ai-bubble-gradient': 'gradient'
  };

  // 明确拒绝的危险片段。
  // 注意：这里刻意 **不** 禁用圆括号 —— rgb()/hsl()/var() 合法值本身需要括号。
  // 安全性由下面每个 kind 的「整串锚定」正则保证：值必须整体匹配某个已知安全形态，
  // 因此 `)` 不可能被用来提前闭合声明。
  var RE_BAD = /[;{}<>\\]|\/\*|\*\/|url\s*:|expression\s*\(|@import|javascript:|&#/i;

  // 聊天背景图：整串锚定成「none」或主题工坊 backgrounds/ 下的 url(...)。
  // 本体内置后背景图由 /api/theme_studio/backgrounds/ 提供，与窗口同源。
  var RE_BG_VALUE = /^(?:none|url\(\s*"?(?:https?:\/\/(?:127\.0\.0\.1|localhost):[0-9]{2,5})?\/api\/theme_studio\/backgrounds\/[A-Za-z0-9._-]{1,80}"?\s*\))$/i;

  // ---------------------------------------------------------------- 工具

  /**
   * 调色板的"新旧"标记。theme.json 不再有 version 字段，用 updated_at 充当：
   * 只用于日志/诊断展示，判等一律靠原始 JSON 文本。
   * @param {Object} p 调色板
   * @returns {string}
   */
  function stampOf(p) {
    if (!p || typeof p !== 'object') return '';
    return String(p.updated_at || p.name || '');
  }

  /**
   * 预热闸门用的调色板签名：按内容算，不能只看 updated_at —— 服务端时间戳只精确
   * 到秒，同一秒里连点两套带图主题时，两份调色板会被当成同一份，停在旧主题上。
   */
  function warmKeyOf(p) {
    var text = '';
    try {
      text = JSON.stringify([p.name, p.enabled, p.vars, p.dark_vars, p.material_vars]);
    } catch (e) {
      text = stampOf(p);
    }
    // FNV-1a 32 位：只用来判「是不是同一份」，不需要抗碰撞。
    var h = 0x811c9dc5;
    for (var i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return stampOf(p) + '|' + text.length + '|' + h.toString(16);
  }
  /* ===== IMAGE_WARM_BEGIN ===== */
  // ------------------------------------------------- 背景图预热（v1.5.21）
  //
  // 切换主题时背景图 URL 变了，浏览器要先下载 / 解码新图；CSS 变量一改，旧图
  // 立刻失效、新图还没到，聊天区就会闪一下空白底色 —— 图越大越明显，第一次
  // 加载某个主题时最容易看见。
  //
  // 所以「检测到主题真的换了」时先把新图预热，等它们就绪（或超时 1.5s）再改
  // CSS。首次应用也走这条路：冷启动时缓存里若是带图的主题，同样不该闪。
  // 只预热本插件自己的 backgrounds/ 目录（受 RE_BG_VALUE 约束），不碰任何外链。
  var IMAGE_WARM_TIMEOUT_MS = 1500;
  var warmedImages = Object.create(null);   // url -> 'ok' | 'fail'
  var warmWaiters = Object.create(null);    // url -> [fn]
  var lastWarmKey = null;   // 上一次「已经决定直接应用」的调色板签名
  var warmingKey = null;    // 正在预热中的签名
  var warmPending = null;   // 预热完成后要应用的调色板
  var warmSeq = 0;          // 预热票号：只有最新一次预热允许回调应用

  /** 取调色板里所有背景图 URL（亮 / 暗两组去重，只认本插件 backgrounds/）。 */
  function imageUrlsOf(p) {
    var out = [];
    var seen = Object.create(null);
    var maps = [p && p.vars, p && p.dark_vars];
    for (var i = 0; i < maps.length; i += 1) {
      var src = maps[i];
      if (!src || typeof src !== 'object') continue;
      for (var k in src) {
        if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
        var v = src[k];
        if (typeof v !== 'string' || !v || v === 'none') continue;
        if (!RE_BG_VALUE.test(v)) continue;
        var hit = /url\(\s*"?([^")\s]+)"?\s*\)/i.exec(v);
        if (!hit) continue;
        if (seen[hit[1]]) continue;
        seen[hit[1]] = 1;
        out.push(hit[1]);
      }
    }
    return out;
  }

  function markImageWarmed(url, status) {
    warmedImages[url] = status;
    var list = warmWaiters[url];
    if (!list) return;
    delete warmWaiters[url];
    for (var i = 0; i < list.length; i += 1) {
      try { list[i](); } catch (e) { /* ignore */ }
    }
  }

  /**
   * 预热若干图片：全部就绪（成功或失败）或超时后回调一次，且只回调一次。
   * 已经预热过的 URL 直接跳过，不会重复发请求。
   */
  function warmImages(urls, done) {
    var pending = 0;
    var fired = false;
    var timer = 0;
    var unsettled = Object.create(null);
    function finish() {
      if (fired) return;
      fired = true;
      if (timer) { clearTimeout(timer); timer = 0; }
      try { done(); } catch (e) { /* ignore */ }
    }
    function timeout() {
      // 超时要「放行」：把还没回来的图记成已处理，否则回调重新进 applyPalette 时
      // 仍判定需要预热，又开一轮 1.5s —— 图一直不回来，新配色就一直不生效。
      for (var u in unsettled) {
        if (unsettled[u] && !warmedImages[u]) markImageWarmed(u, 'timeout');
      }
      finish();
    }
    for (var i = 0; i < urls.length; i += 1) {
      if (warmedImages[urls[i]]) continue;
      pending += 1;
      unsettled[urls[i]] = 1;
      (function (url) {
        var img = new Image();
        function settle(status) {
          unsettled[url] = 0;
          markImageWarmed(url, status);
          pending -= 1;
          if (!pending) finish();
        }
        img.onload = function () { settle('ok'); };
        img.onerror = function () { settle('fail'); };
        try { img.src = url; } catch (e) { settle('fail'); }
      }(urls[i]));
    }
    if (!pending) { finish(); return; }
    timer = setTimeout(timeout, IMAGE_WARM_TIMEOUT_MS);
  }
  /* ===== IMAGE_WARM_END ===== */

  function log() {
    if (!DEBUG) return;
    try {
      var a = Array.prototype.slice.call(arguments);
      a.unshift('[theme-studio]');
      console.log.apply(console, a);
    } catch (e) { /* ignore */ }
  }

  function safeGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }

  function safeSet(key, val) {
    try { window.localStorage.setItem(key, val); } catch (e) { /* ignore */ }
  }

  /**
   * 调色板端点。本体内置后与页面同源，直接用它自己的相对路径，
   * 不再拼插件服务地址。
   * @returns {string}
   */
  function themeUrl() {
    return API_BASE + '/theme.json';
  }

  // ---------------------------------------------------------------- 值校验

  /**
   * 判断一个值是否可以安全地写进给定变量。
   * @param {string} name  CSS 变量名
   * @param {string} value 候选值
   * @returns {boolean}
   */
  /** keyword 型变量的值域（与后端 KEYWORD_VARS 一一对应，改一处必须改三处）。 */
  var KEYWORD_VALUES = {
    '--nts-chat-bg-fit': ['cover', 'contain'],
    '--nts-corner-style': ['round', 'continuous'],
    '--nts-chat-bg-position': ['center', 'top', 'bottom', 'left', 'right'],
    '--nts-chat-bg-repeat': ['no-repeat', 'repeat', 'repeat-x', 'repeat-y']
  };

  function isSafeValue(name, value) {
    if (typeof value !== 'string') return false;
    var v = value.trim();
    if (!v || v.length > 300) return false;

    var kind = NON_COLOR_VARS[name] || 'auto';
    if (kind === 'image') {
      // 背景图只有两种合法形态，整串锚定；不走 RE_BAD（url( 是它的正常写法）。
      return RE_BG_VALUE.test(v);
    }
    if (RE_BAD.test(v)) return false;

    // v0.1.11：两类新值（契约 §2）。都按 token / 停止点白名单整串锚定，
    // 因此 `url(...)`、`var(...)`、`expression(...)` 与任何多余内容都会被拒。
    if (kind === 'keyword') return (KEYWORD_VALUES[name] || []).indexOf(v.toLowerCase()) >= 0;
    // 行高是「无单位倍数」，与 0–1 的 number 乘子不同，不能共用 RE_NUMBER。
    if (kind === 'ratio') {
      if (!RE_RATIO.test(v)) return false;
      var ratio = parseFloat(v);
      return ratio >= 0.8 && ratio <= 3;
    }
    if (kind === 'filter') return isSafeFilterChain(v);
    if (kind === 'gradient') return isSafeGradient(v);

    if (kind === 'color' || kind === 'auto') {
      // 'auto' 是给「本版本之后新增、白名单里还没记录」的变量兜底。
      // 起因是一个真实事故：--nts-chat-panel-radius 加进面板与后端后，用户已开的
      // 窗口仍跑着旧 loader（浏览器缓存里是上一版脚本），旧 loader 的默认 kind
      // 是 'color'，于是把 '24px' 当成非法颜色静默丢弃 —— 背景色进去了、圆角没有，
      // 而 apply_theme 照样返回 succeeded，面板也不报错。要靠用户重启才恢复，
      // 这正是「面板改了应用里不生效」的一个系统性来源。
      // 兜底只放行 color 与 length 两种已锚定的安全形态，不降低其它安全性。
      var asColor =
        isNamedColor(v) || RE_HEX.test(v) ||
        RE_RGB.test(v) || RE_HSL.test(v);
      if (asColor) return true;
      if (kind === 'color') {
        // 刻意**不**支持 `var(--nts-*)` 派生色：面板与后端都只收具体色值，loader 单独放行
        // 会造出一条只有手改 theme.json 才走得通的路，且「颜色变量引用长度变量」这类
        // 组合会让声明整条失效（静默无色）。三处校验必须逐字一致，见 THEME_CONTRACT §9.10b。
        return false;
      }
      // kind === 'auto' 且不是颜色 → 再按长度试一次（与下面的 length 分支同判定）
      if (v.indexOf('-') !== -1) return false;
      return RE_LEN.test(v) || RE_PCT.test(v) || /^0$/.test(v);
    }
    if (kind === 'length') {
      // 负长度在 CSS 里一律非法（整条声明被丢掉 → 面板报成功、外观不变）。
      // 后端 _is_safe_value 与面板 isSafeValue 都单独拦 '-'，这里必须对齐；
      // RE_LEN 为了 shadow 偏移量才允许负号（shadow 走自己的 token 分支）。
      if (v.indexOf('-') !== -1) return false;
      if (RE_LEN.test(v) || RE_PCT.test(v)) return true;
      if (/^0$/.test(v)) return true;
      return false;
    }
    if (kind === 'blur') {
      // 面板统一输出 blur(...)，但契约 §3 允许裸长度；两者都收，存原样。
      // 负模糊值同样非法（backdrop-filter 整条失效），与后端/面板一致拒掉。
      if (v.indexOf('-') !== -1) return false;
      // % / vh / vw 不是合法的 blur 半径（CSS 会丢整条声明），与后端 RE_BLUR_BARE 一致拒掉。
      return RE_BLUR.test(v) || RE_BLUR_BARE.test(v) || /^0$/.test(v);
    }
    if (kind === 'time') {
      // 呼吸周期（v0.1.11）。裸数字按秒（3 = 3s），稍后 sanitizeVars 会补单位。
      // 只收 s / ms：呼吸节奏用不到其它单位，放宽只会放出无效 CSS。
      if (RE_TIME.test(v)) {
        var unit = v.slice(-2).toLowerCase();
        var sec = unit === 'ms' ? parseFloat(v.slice(0, -2)) / 1000
          : parseFloat(v.slice(0, -1));
        return isFinite(sec) && sec >= 0 && sec <= TIME_MAX_SECONDS;
      }
      if (RE_TIME_BARE.test(v)) return parseFloat(v) <= TIME_MAX_SECONDS;
      return false;
    }
    if (kind === 'number') {
      if (RE_NUMBER.test(v)) return true;
      // 面板的数值输入框允许写百分比（`50%` 表示半透明），后端也接受；
      // 本脚本早先只认 0-1 小数，于是「面板不标红、落盘成功、这里静默丢弃」。
      // 现在收 <=100%，并在 sanitizeVars 里归一成小数（乘子语义）。
      return pctToFraction(v) !== null;
    }
    if (kind === 'font') {
      return RE_FONT.test(v) && !RE_BAD.test(v);
    }
    if (kind === 'shadow') {
      // box-shadow 的语法是 <inset>? && <length>{2,4} && <color>?；只做 token
      // 白名单会放行 '0 0 10% #000'（百分比非法）、'1px'（长度不足）等，
      // 浏览器整条丢弃 → 面板报成功、外观不变。三处校验必须同判定。
      // 多层写法（逗号分隔）要先切层再逐层校验；只认单层会把
      // `0 0 20px rgba(...), 0 0 15px rgba(...)` 整串拒掉 → 门控不设 → 阴影静默消失。
      var layers = shadowLayers(v);
      if (!layers.length || layers.length > 4) return false;
      for (var li = 0; li < layers.length; li++) {
        if (!shadowLayerOk(shadowTokens(layers[li]))) return false;
      }
      return true;
    }
    return false;
  }

  // --------------------------------------------- 新值类型校验：filter / gradient
  //
  // v0.1.11 新增两类值（契约 §2，Python _filter_chain_ok/_gradient_ok、面板
  // isSafeFilterChain/isSafeGradient 与这里必须逐字同判定）：
  //   filter   = 空格分隔的滤镜链，1–4 个 token 且不可重复，blur ≤ 40px、其它 ≤ 400%
  //   gradient = linear/radial-gradient，2–4 个停止点，整串 ≤ 300 字符
  // 这两类值会原样拼进 `--nts-*: <值>` 声明，所以必须整串锚定 / 按 token 白名单，
  // 不能用「看起来像滤镜」的宽松正则（那等于给主题 JSON 开了一条注入通道）。
  var RE_FILTER_TOKEN = /^(blur|saturate|brightness|contrast|grayscale)\((\d{1,3}(?:\.\d+)?)(px|%)\)$/;
  var FILTER_MAX_TOKENS = 4;
  var FILTER_MAX_CHARS = 120;
  var FILTER_MAX_BLUR_PX = 40;
  var FILTER_MAX_PERCENT = 400;

  /**
   * 滤镜链校验（契约 §2.1）。
   * @param {string} v 例如 `blur(20px) saturate(150%)`
   * @returns {boolean}
   */
  function isSafeFilterChain(v) {
    if (typeof v !== 'string') return false;
    var s = v.trim();
    if (!s || s.length > FILTER_MAX_CHARS) return false;
    var toks = s.split(' ');
    if (toks.length < 1 || toks.length > FILTER_MAX_TOKENS) return false;
    var seen = Object.create(null);
    for (var i = 0; i < toks.length; i += 1) {
      var m = RE_FILTER_TOKEN.exec(toks[i]);
      if (!m) return false;
      var fn = m[1], num = parseFloat(m[2]), unit = m[3];
      if (seen[fn]) return false;
      seen[fn] = 1;
      if (fn === 'blur') { if (unit !== 'px' || num > FILTER_MAX_BLUR_PX) return false; }
      else { if (unit !== '%' || num > FILTER_MAX_PERCENT) return false; }
      if (toks[i] !== m[0]) return false;
    }
    return true;
  }

  var RE_GRAD_HEAD = /^(linear|radial)-gradient\(([\s\S]*)\)$/;
  var RE_GRAD_ANGLE = /^-?\d{1,3}(?:\.\d+)?deg$/;
  var RE_GRAD_STOP = /^((?:#[0-9a-f]{3,8})|(?:rgba?\([^()]*\))|(?:hsla?\([^()]*\))|(?:[a-z]{3,20}))(?:\s+(\d{1,3}(?:\.\d+)?)%)?$/i;
  var GRADIENT_MAX_STOPS = 4;
  var GRADIENT_MAX_CHARS = 300;

  /**
   * 渐变校验（契约 §2.2）：只认 linear / radial，2–4 个停止点，停止点百分比 ≤ 100%。
   * 顶层逗号切分要数括号深度 —— rgba(0, 0, 0, .5) 里全是逗号。
   * @param {string} v 例如 `linear-gradient(135deg, #eef4ff 0%, #dfe9ff 100%)`
   * @returns {boolean}
   */
  function isSafeGradient(v) {
    if (typeof v !== 'string') return false;
    var s = v.trim();
    if (!s || s.length > GRADIENT_MAX_CHARS) return false;
    var m = RE_GRAD_HEAD.exec(s);
    if (!m) return false;
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
    if (parts.length < 2 || parts.length > GRADIENT_MAX_STOPS) return false;
    for (var j = 0; j < parts.length; j += 1) {
      var sm = RE_GRAD_STOP.exec(parts[j]);
      if (!sm) return false;
      var color = sm[1];
      if (!(isNamedColor(color) || RE_HEX.test(color) || RE_RGB.test(color) || RE_HSL.test(color))) return false;
      if (sm[2] !== undefined && parseFloat(sm[2]) > 100) return false;
    }
    return true;
  }

  /** 按「顶层逗号」切多层 box-shadow（后端 _split_shadow_layers 同构）。 */
  function shadowLayers(value) {
    var layers = [];
    var depth = 0;
    var buf = '';
    for (var i = 0; i < value.length; i += 1) {
      var ch = value[i];
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

  /** 单层 box-shadow 的 token 校验（与后端 _shadow_layer_ok 同判定）。 */
  function shadowLayerOk(toks) {
    if (!toks.length || toks.length > 6) return false;
    var lengths = 0;
    var colors = 0;
    var insets = 0;
    for (var i = 0; i < toks.length; i++) {
      var tk = toks[i];
      if (/^inset$/i.test(tk)) {
        if (++insets > 1) return false;
        continue;
      }
      if (isNamedColor(tk) || RE_HEX.test(tk) || RE_RGB.test(tk) || RE_HSL.test(tk)) {
        if (++colors > 1) return false;
        continue;
      }
      if (RE_PCT.test(tk) || tk.indexOf('%') !== -1) return false;
      if (RE_LEN.test(tk) || /^0$/.test(tk)) {
        lengths += 1;
        if (lengths > 4) return false;
        if (tk.charAt(0) === '-' && lengths > 2) return false;
        continue;
      }
      return false;
    }
    return lengths >= 2;
  }

  function isSafeVarName(name) {
    return typeof name === 'string' &&
      name.indexOf(VAR_PREFIX) === 0 &&
      /^--nts-[a-z0-9-]{1,60}$/.test(name);
  }

  /**
   * 被拒变量只大声报一次（DEBUG 日志全量）。
   * v0.1.11 起错误信息必须带变量名 —— 用户看不到面板的校验镜像时，控制台里
   * 「哪个变量、什么值、按什么 kind 判的」是唯一能自查的线索；不带名字的
   * 「reject value」等于没说。
   */
  var warnedReject = Object.create(null);
  function noteRejected(name, value) {
    log('reject value', name, value);
    if (warnedReject[name]) return;
    warnedReject[name] = 1;
    try {
      var kind = NON_COLOR_VARS[name] || 'color';
      console.warn('[theme-studio] 变量 ' + name + ' 的值不合法（kind=' + kind + '），已跳过：' +
        String(value).slice(0, 120) +
        '\n  合法形态见 FEATURE-CONTRACT §2；改对后下一轮轮询会自动生效。');
    } catch (e) { /* ignore */ }
  }

  /**
   * 过滤出一个安全的 vars 对象。
   * @returns {Object<string,string>}
   */
  function sanitizeVars(src) {
    var out = {};
    if (!src || typeof src !== 'object') return out;
    for (var k in src) {
      if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
      if (!isSafeVarName(k)) { log('reject var name', k); continue; }
      if (!isSafeValue(k, src[k])) { noteRejected(k, src[k]); continue; }
      var v = String(src[k]).trim();
      // 与插件端 normalize_value() 对齐：裸长度补成 blur(...)，否则
      // `backdrop-filter: 18px` 整条声明非法而被浏览器丢弃。
      if (NON_COLOR_VARS[k] === 'blur' && RE_BLUR_BARE.test(v)) v = 'blur(' + v + ')';
      // 百分比乘子归一成小数：`50%` → `0.5`。存原样会让 CSS 拿到 `50%` 而整条失效。
      if (NON_COLOR_VARS[k] === 'number') {
        var frac = pctToFraction(v);
        if (frac !== null) v = String(frac);
      }
      // 时间值补单位：`animation-duration: 3` 是非法 CSS（时长必须带单位），
      // 不补的话整条 animation 声明被丢弃 → 「节奏填了但不动」。
      if (NON_COLOR_VARS[k] === 'time' && RE_TIME_BARE.test(v)) v = v + 's';
      out[k] = v;
    }
    return out;
  }

  // ---------------------------------------------------------------- 注入

  // v0.1.11 重复注入哨兵：注入 <style> 前先按固定 id 复用。宿主（或用户手工）
  // 重复注入本脚本时，第二份实例会命中同一个 id，不会再插一份；同一个 id 下若
  // 已有多份节点（模板重渲染 / 多次 append），只保留第一份，避免「谁最后写谁生效」。
  var lastAppliedCss = '';
  function styleEl() {
    var el = document.getElementById(STYLE_ID);
    if (el) {
      if (el.tagName && String(el.tagName).toUpperCase() === 'STYLE') {
        removeDuplicateStyles(el);
        return el;
      }
      // 同一个 id 被非 <style> 节点占用：让它让位，自己重建一份。
      if (el.parentNode) el.parentNode.removeChild(el);
    }
    el = document.createElement('style');
    el.id = STYLE_ID;
    el.setAttribute('data-nts', '1');
    (document.head || document.documentElement).appendChild(el);
    return el;
  }

  /** 清掉与 keep 同 id 的多余 <style> 节点。 */
  function removeDuplicateStyles(keep) {
    try {
      var all = document.querySelectorAll('style#' + STYLE_ID);
      for (var i = 0; i < all.length; i += 1) {
        var n = all[i];
        if (n === keep) continue;
        if (n.parentNode) n.parentNode.removeChild(n);
      }
    } catch (e) { /* ignore */ }
  }

  /** 注入的 <style> 是否还在 DOM 里、内容是否就是最近一次写入的内容。 */
  function styleIntact() {
    if (!lastAppliedCss) return true;
    var el = document.getElementById(STYLE_ID);
    return !!(el && el.parentNode && el.textContent === lastAppliedCss);
  }

  // v0.1.11 CSP 自检：style-src 被 CSP 拦掉时，<style> 节点照样能插进 DOM，但规则
  // 一条都不生效 —— 变量注入"成功"、外观纹丝不动，且没有任何报错（最容易被误判成
  // 「插件没生效」）。往注入内容里写一条探针自定义属性，用 getComputedStyle 验证它
  // 是否真的被解析；不生效时只 warn 一次，不 throw、不重试（CSP 不会自己变好）。
  var PROBE_CSS = ':root{--nts-probe:1}';
  var cspWarned = false;
  function probeStyleApplied() {
    if (cspWarned) return;
    var got = '';
    try {
      if (!window.getComputedStyle) return;
      got = window.getComputedStyle(document.documentElement).getPropertyValue('--nts-probe');
    } catch (e) { return; }
    if (String(got).trim() === '1') return;
    cspWarned = true;
    try {
      console.warn('[NTS] 主题样式被 CSP 拦截，主题不会生效');
    } catch (e2) { /* ignore */ }
  }

  function removeStyle() {
    var el = document.getElementById(STYLE_ID);
    if (el && el.parentNode) el.parentNode.removeChild(el);
    lastAppliedCss = '';
    clearGates();
    // v1.6.0：材质层靠 data-nts-mt 门控，撤样式时必须一起撤属性，
    // 否则 MATERIAL_CSS 仍匹配、只是变量缺失 —— 会退化成"半透明底"视觉残留。
    clearMaterialAttrs();
  }

  // ------------------------------------------------------- 门控属性（gates）
  //
  // stylesheet 里有一批规则无法用"原生回退值"表达（原生是 gradient、或原生值
  // 随上下文变化、或需要整组属性一起换）。这些规则统一门控在
  //   :root[data-nts-<var>]      -- 该变量确实被用户配置过
  //   :root[data-nts-active]     -- 至少有一个变量被配置过
  // 上。因此 loader 必须把这些属性同步打到 documentElement，否则那部分规则
  // 永远不生效（曾实测出该缺陷：只有 data-nts="1"，规则全程空转）。

  // v3 起**每一条**生成的规则都门控在按模式分键的属性上：
  //   :root:not([data-theme="dark"])[data-nts-l-<var>]  -- 该变量在「亮色」组被配置过
  //   :root[data-theme="dark"][data-nts-d-<var>]        -- 该变量在「暗色」组被配置过
  // 且不再有任何回退值。原因：回退值是合成量测页里量到的，那不是真实页的计算值
  // （真实 .chat-body 处于 .chat-surface-mode-compact 下、body 带 .electron-chat-window、
  // 还有运行时注入的 !important style 层），任何量测回退都会静默改写未配置外观。
  // 门控后「未配置 == 未加载」是构造性成立的：没有属性 → 选择器不匹配 → 零效果。
  var GATE_ATTR = 'data-nts-';
  var ACTIVE_ATTR = 'data-nts-active';
  // 亮色值的限定作用域：非暗色时才是亮色。与生成器的亮色门控前缀保持一致语义。
  var LIGHT_SCOPE = ':root:not([data-theme="dark"])';
  var gateApplied = null; // Array<string> 上一次打上的属性名，用于精确撤销

  /** 变量名 + 模式 -> 门控属性名：data-nts-l-foo / data-nts-d-foo
   *  必须与 gen-css.js 的 gateAttrFor() 完全一致（去掉整个 `--nts-` 前缀）。 */
  function gateNameFor(varName, mode) {
    return GATE_ATTR + (mode === 'dark' ? 'd-' : 'l-') + String(varName).replace(/^--nts-/, '');
  }

  function clearGates() {
    var de = document.documentElement;
    if (!de) return;
    if (gateApplied) {
      for (var i = 0; i < gateApplied.length; i++) de.removeAttribute(gateApplied[i]);
    } else {
      // 没有记账时兜底扫描，避免残留陈旧门控（属性变化会触发样式重算）
      var names = [];
      /* 只清「变量门控」：`data-nts-l-<变量>` / `data-nts-d-<变量>`。
       * 旧实现用裸前缀 `data-nts-` 兜底，会把 loader 自己的状态属性一起删掉
       * （data-nts-active 有变量被配置 / data-nts-paused 后台暂停 /
       * data-nts-fade 主题切换渐入）—— 表现为换主题时渐变被掐断、切回前台
       * 玻璃动画不动。 */
      var gatePrefixes = [GATE_ATTR + 'l-', GATE_ATTR + 'd-'];
      for (var j = 0; j < de.attributes.length; j++) {
        var n = de.attributes[j].name;
        if (n.indexOf(gatePrefixes[0]) === 0 || n.indexOf(gatePrefixes[1]) === 0) names.push(n);
      }
      for (var k = 0; k < names.length; k++) de.removeAttribute(names[k]);
    }
    gateApplied = null;
  }

  /**
   * 把当前调色板的变量同步成 documentElement 上的门控属性。
   * 幂等：集合不变则不碰 DOM（省掉一次整页样式重算）。
   * @param {Object<string,string>} light
   * @param {Object<string,string>} dark
   */
  function setGates(light, dark) {
    var de = document.documentElement;
    if (!de) return;
    syncCornerStyle(light, dark);
    var want = [];
    var seen = {};
    function add(o, mode) {
      var ks = [];
      for (var k in o) {
        if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
        // 按 (模式, 变量) 去重：同一个变量可以在两组里都出现，那是两个独立门控。
        var key = mode + '|' + k;
        if (seen[key]) continue;
        seen[key] = 1;
        ks.push(k);
      }
      ks.sort();
      for (var i = 0; i < ks.length; i++) want.push(gateNameFor(ks[i], mode));
    }
    add(light, 'light');
    add(dark, 'dark');
    if (!want.length) { clearGates(); return; }
    want.unshift(ACTIVE_ATTR);

    var same = gateApplied && gateApplied.length === want.length;
    if (same) {
      for (var i = 0; i < want.length; i++) {
        if (gateApplied[i] !== want[i]) { same = false; break; }
      }
    }
    if (same) return;

    clearGates();
    for (var m = 0; m < want.length; m++) de.setAttribute(want[m], '1');
    gateApplied = want;
  }

  /** 连续曲率圆角的开关属性：只在调色板里出现 continuous 时打上。
   *  CSS 侧（gen-css 的 @supports 段）靠 `[data-nts-squircle]` 门控，内核不支持
   *  `corner-shape` 时整段不生效 —— 所以这里可以无条件按值打属性。 */
  var SQUIRCLE_ATTR = 'data-nts-squircle';

  function syncCornerStyle(light, dark) {
    var de = document.documentElement;
    if (!de) return;
    var on = false;
    var groups = [light, dark];
    for (var i = 0; i < groups.length && !on; i += 1) {
      var g = groups[i];
      if (!g || typeof g !== 'object') continue;
      var raw = g['--nts-corner-style'];
      if (typeof raw === 'string' && raw.trim().toLowerCase() === 'continuous') on = true;
    }
    if (on) {
      if (!de.hasAttribute(SQUIRCLE_ATTR)) de.setAttribute(SQUIRCLE_ATTR, '1');
    } else if (de.hasAttribute(SQUIRCLE_ATTR)) {
      de.removeAttribute(SQUIRCLE_ATTR);
    }
  }

  // -------------------- 液态玻璃材质层（v1.5.14，契约 0.3.0）
  //
  // 目标：把 Apple Liquid Glass 的**感知要素**用 CSS 复刻到宿主的功能层上
  // （顶栏 / 输入框 / 气泡 / 弹层 / 导出区），而不是往页面上堆装饰。
  // Apple 官方把它描述成「半透明材质，会折射、反射周围内容并随上下文流动」，
  // 拆开是四层 + 一个动态维度；Chromium 不支持 backdrop-filter 引用 SVG filter，
  // 所以真位移折射无法做（契约 §2 已论证），这里做感知等价：
  //
  //   1 透视  backdrop-filter：来自 --nts-glass-filter（生成 CSS 已写，本层不碰）
  //   2 染色  background-color：来自 --nts-glass-tint（同上）
  //   3 边缘  lens 亮环 + thickness 厚度（本层新增，box-shadow 组合）
  //           上缘 1px 高光、下缘 1px 暗边、四边 1px 半亮；lens 再叠一圈亮环
  //           与内侧柔光，模拟玻璃边把光「弯」进来的感觉
  //   4 镜面  specular 径向高光（本层新增，伪元素），位置 --nts-spec-x/--nts-spec-y
  //           由 initGlassPointer 跟随指针写入 —— 这是 Apple 玻璃最容易被认出来的
  //           特征：高光随光源/指针移动，而不是固定扫光
  //   1 动态  沿用 0.2.0 的 sheen 掠光 + noise 颗粒；滚动时 --nts-scroll-k=1
  //           让镜面高光收敛（「液」随滚动流动）；按压轻微压扁（见第 11 组规则）
  //
  // 三条硬约束（沿用 0.2.0，契约 §1/§2/§6）：
  //   1) 所有新变量在旧主题里都没配置 → 每条规则都门控在 data-nts-l/d-glass-* 上，
  //      没配置 == 零效果（旧主题外观逐像素不变）。
  //   2) 不写元素自身的 background-image（生成 CSS 已用 !important 占了），
  //      只用伪元素与 box-shadow 做加法。
  //   3) 宿主 .composer-input-shell / 消息气泡是 position:static，绝对定位伪元素
  //      需要定位祖先 → 补 position:relative（无偏移量，不改布局）；不加 isolation。
  //
  // 伪元素槽位（审计 46 个宿主 CSS 文件，脚本 _work/glass_audit.py）：
  //   ::before + ::after 都空闲 —— .composer-input-shell、.composer-overflow-popover、
  //       .message-bubble-user、.message-bubble-assistant、.compact-export-history-bubble
  //       → ::before 做 noise，::after 做 specular + sheen（两层背景，screen 混合）。
  //   只剩 ::after 空闲 —— .window-topbar（::before 被暗色渐变占用）、
  //       .compact-export-history-scroll（::before 被面板磨砂层占用）
  //       → ::after 里叠 specular + sheen + noise 三层（screen / screen / overlay）。
  //   ::before、::after 都被占用 —— .compact-chat-surface-frame、.composer-icon-button
  //       → 只做 box-shadow 的 lens + thickness（无镜面伪元素）。
  //
  // 门控前缀补 :root:root:root:root，specificity 压过生成的 !important 规则。
  // data URI 里 # 必须写成 %23 —— 裸 # 会让 CSS 在 url() 中间截断。
  var GLASS_NOISE_URI = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='120' height='120' filter='url(%23n)' opacity='0.6'/></svg>\")";

  var GLASS_CSS = (function () {
    var LIGHT = ':root:not([data-theme=dark])';
    var DARK = ':root[data-theme=dark]';
    // 与生成器一致：重复 :root 抬高 specificity，压住宿主运行时注入的 !important。
    var BOOST = ':root:root:root:root';
    var MODES = [
      { pre: LIGHT, key: GATE_ATTR + 'l-' },
      { pre: DARK, key: GATE_ATTR + 'd-' }
    ];
    var lines = [];

    /** 生成「模式 × 门控变量 × 目标」的选择器列表。
     *  vars 里任一变量在任一模式被配置过，规则就生效；变量各写一条，互不牵连。 */
    function sel(targets, vars, pseudo) {
      var out = [];
      for (var t = 0; t < targets.length; t++) {
        for (var m = 0; m < MODES.length; m++) {
          for (var v = 0; v < vars.length; v++) {
            out.push(MODES[m].pre + '[' + MODES[m].key + 'glass-' + vars[v] + ']' +
              BOOST + ' body ' + targets[t] + (pseudo || ''));
          }
        }
      }
      return out;
    }

    function rule(sels, body) {
      lines.push(sels.join(',\n') + ' {\n' + body.join('\n') + '\n}');
    }

    // 0–1 变量统一读法：未配置时按 0 算（= 零效果）。
    var V_T = 'var(--nts-glass-thickness, 0)';
    var V_L = 'var(--nts-glass-lens, 0)';
    var V_S = 'var(--nts-glass-specular, 0)';
    var V_D = 'var(--nts-glass-dispersion, 0)';
    var V_A = 'var(--nts-ambient-glow, 0)';
    var V_C = 'var(--nts-glass-caustic, 0)';
    // 指针光源位置（initGlassPointer 写入，百分比）：ambient 光斑跟着指针走。
    var LIGHT_AT = 'var(--nts-light-x, 50%) var(--nts-light-y, 0%)';
    var V_K = 'var(--nts-scroll-k, 0)';
    var RIM = 'var(--nts-glass-rim, rgba(255, 255, 255, 0.55))';

    // 镜面高光位置：指针跟随（--nts-spec-x/y，百分比），无指针设备时停在左上。
    var SPEC_AT = 'var(--nts-spec-x, 28%) var(--nts-spec-y, 14%)';
    // 滚动时镜面收敛 45%（--nts-scroll-k 由 initGlassPointer 的滚动监听写入）。
    var SPEC_ALPHA = 'calc(' + V_S + ' * (1 - 0.45 * ' + V_K + '))';

    var EDGE = ['rim', 'thickness', 'lens', 'dispersion'];

    // 1) 边缘 + 厚度：由 --nts-glass-highlight outline 占用的 4 个表面。
    //    用 box-shadow 组合而不是 outline：不抢生成的 !important outline。
    function edgeShadow(withShadowVar) {
      var parts = [
        'inset 0 1px 0 ' + RIM,
        'inset 0 -1px 0 rgba(0, 0, 0, calc(' + V_T + ' * 0.34))',
        'inset 1px 0 0 rgba(255, 255, 255, calc(' + V_T + ' * 0.22))',
        'inset -1px 0 0 rgba(255, 255, 255, calc(' + V_T + ' * 0.16))',
        'inset 0 0 0 1px rgba(255, 255, 255, calc(' + V_L + ' * 0.50))',
        'inset 1px 1px 0 rgba(255, 196, 128, calc(' + V_D + ' * 0.55))',
        'inset -1px -1px 0 rgba(140, 205, 255, calc(' + V_D + ' * 0.50))',
        'inset 0 1px 12px rgba(255, 255, 255, calc(' + V_T + ' * 0.10))',
        '0 14px 34px -16px rgba(0, 0, 0, calc(0.30 + ' + V_T + ' * 0.24))'
      ];
      if (withShadowVar) parts.push('var(--nts-shadow, 0 0 0 0 rgba(0, 0, 0, 0))');
      return ['box-shadow: ' + parts.join(',\n    ') + ' !important;'];
    }

    rule(sel(['.window-topbar', '.composer-input-shell', '.compact-chat-surface-frame',
      '.composer-icon-button'], EDGE), edgeShadow(false));

    // 2) 气泡组：追加 var(--nts-shadow)，保住主题投影（override 会顶掉生成规则）。
    rule(sel(['.message-bubble-user', '.message-bubble-assistant',
      '.compact-export-history-bubble'], EDGE), edgeShadow(true));

    // 3) 没有 outline 冲突的表面：rim 保持 outline，lens/thickness 交给 box-shadow。
    //    0.4.3：`.compact-export-history-scroll` 已移出 —— 它是按预留槽位定高的消息
    //    列表容器（无圆角），outline + inset 阴影会沿那块空槽画出直角色带。
    rule(sel(['.composer-overflow-popover'], EDGE), [
      'outline: 1px solid ' + RIM + ' !important;',
      'outline-offset: -1px !important;',
      'box-shadow: inset 0 -1px 0 rgba(0, 0, 0, calc(' + V_T + ' * 0.30)),' +
        ' inset 0 0 0 1px rgba(255, 255, 255, calc(' + V_L + ' * 0.45)),' +
        ' inset 0 1px 0 rgba(255, 255, 255, calc(' + V_T + ' * 0.55)) !important;'
    ]);

    // 4) 焦点兜底：box-shadow rim 会盖掉这两个表面原生用 box-shadow 画的焦点环，
    //    键盘焦点必须看得见 → 用 outline 补一条（颜色取宿主自己焦点环的 #40c5f1）。
    rule(sel(['.composer-input-shell:focus-within'], ['rim']), [
      'outline: 2px solid rgba(64, 197, 241, 0.62) !important;',
      'outline-offset: 1px !important;'
    ]);
    rule(sel(['.compact-export-history-bubble:focus-visible'], ['rim']), [
      'outline: 2px solid rgba(64, 197, 241, 0.62) !important;',
      'outline-offset: 2px !important;'
    ]);

    // 5) 定位：position:static 的表面需要定位祖先承载伪元素。
    //    .window-topbar 已 relative；popover absolute、导出气泡 relative，不碰；
    //    .compact-export-history-scroll 刻意不加 —— 它的 ::before 磨砂层按现有
    //    定位祖先铺满，改 relative 会让那层的圆角/范围跟着变。
    var FX = ['specular', 'sheen', 'noise'];
    rule(sel(['.composer-input-shell', '.message-bubble-user', '.message-bubble-assistant'],
      ['specular', 'sheen'], null), [
      'position: relative !important;'
    ]);

    var specLayer = 'radial-gradient(120% 90% at ' + SPEC_AT + ',' +
      ' rgba(255, 255, 255, ' + SPEC_ALPHA + ') 0%,' +
      ' rgba(255, 255, 255, calc(' + SPEC_ALPHA + ' * 0.34)) 34%,' +
      ' rgba(255, 255, 255, 0) 68%)';
    var sheenLayer = 'linear-gradient(115deg, transparent 35%,' +
      ' rgba(255, 255, 255, calc(var(--nts-glass-sheen, 0) * 0.55)) 50%,' +
      ' transparent 65%)';

    var noiseBody = [
      'content: "" !important;',
      'position: absolute !important;',
      'inset: 0 !important;',
      'z-index: 0 !important;',
      'pointer-events: none !important;',
      'border-radius: inherit !important;',
      'background-image: ' + GLASS_NOISE_URI + ' !important;',
      'background-size: 120px 120px !important;',
      'background-repeat: repeat !important;',
      'opacity: var(--nts-glass-noise, 0) !important;',
      'mix-blend-mode: overlay !important;'
    ];

    // 6) 合成光效层（0.4.0 重构）：ambient 指针光源 + caustic 焦散 + specular 镜面 + sheen 掠光
    //    四层全部走自定义属性槽位（--nts-fx-*），任何单个变量被配置都能正确合成、互不覆盖。
    //    0.3.0 那套「黑色遮罩 + noise 叠层」有真实缺陷：veil alpha = 1 - noise，noise 未配置时
    //    等于全黑不透明层，把下面的 specular/sheen 整个盖掉（再经 screen 混合等于消失），
    //    所以顶栏与导出区的镜面/掠光在 0.3.0 里其实经常是死的。0.4.0 移除该写法，
    //    noise 只保留在 5 个 ::before 空闲的表面上（顶栏/导出区不再支持 noise）。
    var ambientLayer = 'radial-gradient(64% 120% at ' + LIGHT_AT + ',' +
      ' rgba(255, 255, 255, calc(' + V_A + ' * 0.22)) 0%,' +
      ' rgba(255, 255, 255, calc(' + V_A + ' * 0.07)) 38%,' +
      ' rgba(255, 255, 255, 0) 70%)';
    var causticLayer = 'radial-gradient(58% 46% at 50% 50%,' +
      ' rgba(255, 255, 255, calc(' + V_C + ' * 0.30)) 0%,' +
      ' rgba(255, 255, 255, calc(' + V_C + ' * 0.10)) 46%,' +
      ' rgba(255, 255, 255, 0) 72%)';

    var FX_STRUCT = [
      'content: "" !important;',
      'position: absolute !important;',
      'inset: 0 !important;',
      'z-index: 0 !important;',
      'pointer-events: none !important;',
      'border-radius: inherit !important;'
    ];
    // 四个槽位的固定顺序：ambient → caustic → specular → sheen。
    // 列表长度恒定，所以 background-size/position/blend 的对应关系不会随配置漂移。
    function fxBody() {
      return FX_STRUCT.concat([
        'background-image: var(--nts-fx-amb, none), var(--nts-fx-caustic, none),' +
          ' var(--nts-fx-spec, none), var(--nts-fx-sheen, none) !important;',
        'background-size: 100% 100%, 240% 240%, 100% 100%, 220% 100% !important;',
        'background-repeat: no-repeat, no-repeat, no-repeat, no-repeat !important;',
        'background-position: 0 0, 50% 50%, 0 0, 120% 0 !important;',
        'background-blend-mode: screen, screen, screen, screen !important;',
        'mix-blend-mode: screen !important;',
        // 两个动画槽位分开挂：只配 sheen 就只跑掠光，只配 caustic 就只跑焦散。
        'animation: var(--nts-anim-sheen, none), var(--nts-anim-caustic, none) !important;'
      ]);
    }

    // 0.4.1 修复：**不要把 `.compact-export-history-scroll` 放进来**。
    // 它是 position:static（§5 刻意不加 relative，否则会改掉 ::before 磨砂层的锚点），
    // 于是这里的 absolute + inset:0 的 ::after 会去找更外层的定位祖先铺满，
    // 表现为导出/历史面板上方多出一整块直角的亮屏（0.4.0 的 ambient/specular
    // 一打开就非常明显）。只在有确定包含块的 6 个表面上做光效层。
    var FX_SURFACES = ['.window-topbar', '.composer-input-shell', '.composer-overflow-popover',
      '.message-bubble-user', '.message-bubble-assistant',
      '.compact-export-history-bubble'];
    var FX_PROPS = [
      ['ambient', '--nts-fx-amb', ambientLayer],
      ['caustic', '--nts-fx-caustic', causticLayer],
      ['specular', '--nts-fx-spec', specLayer],
      ['sheen', '--nts-fx-sheen', sheenLayer]
    ];
    for (var f = 0; f < FX_PROPS.length; f++) {
      rule(sel(FX_SURFACES, [FX_PROPS[f][0]], '::after'),
        fxBody().concat([FX_PROPS[f][1] + ': ' + FX_PROPS[f][2] + ' !important;']));
    }
    // 动画只在对应变量配置时挂（0.3.0 是无条件挂 sheen 动画，sheen=0 时纯浪费）。
    rule(sel(FX_SURFACES, ['sheen'], '::after'), [
      '--nts-anim-sheen: nts-glass-sheen 7.5s ease-in-out infinite !important;'
    ]);
    rule(sel(FX_SURFACES, ['caustic'], '::after'), [
      '--nts-anim-caustic: nts-glass-caustic 13s ease-in-out infinite !important;'
    ]);

    // 7) 噪声（::before）：只剩 5 个伪元素空闲的表面；顶栏与导出区不参与。
    var noiseBody = [
      'content: "" !important;',
      'position: absolute !important;',
      'inset: 0 !important;',
      'z-index: 0 !important;',
      'pointer-events: none !important;',
      'border-radius: inherit !important;',
      'background-image: ' + GLASS_NOISE_URI + ' !important;',
      'background-size: 120px 120px !important;',
      'background-repeat: repeat !important;',
      'opacity: var(--nts-glass-noise, 0) !important;',
      'mix-blend-mode: overlay !important;'
    ];
    var dual = ['.composer-input-shell', '.composer-overflow-popover', '.message-bubble-user',
      '.message-bubble-assistant', '.compact-export-history-bubble'];
    for (var i = 0; i < dual.length; i++) {
      rule(sel([dual[i]], ['noise'], '::before'), noiseBody);
    }

    // 8) 掠光扫过（只动 background-position 的第 4 层 = sheen）。
    //    60% 之后停在同一位置：带子扫出去后静止一段再回卷，避免硬回弹。
    lines.push('@keyframes nts-glass-sheen {\n' +
      '  0% { background-position: 0 0, 50% 50%, 0 0, 120% 0; }\n' +
      '  60%, 100% { background-position: 0 0, 50% 50%, 0 0, -40% 0; }\n' +
      '}');
    // 9) 焦散呼吸（只动 background-size 的第 2 层 = caustic）：
    //    光斑缓慢胀缩，像水面折出来的光在玻璃后面晃。
    lines.push('@keyframes nts-glass-caustic {\n' +
      '  0%, 100% { background-size: 100% 100%, 240% 240%, 100% 100%, 220% 100%; }\n' +
      '  50% { background-size: 100% 100%, 300% 260%, 100% 100%, 220% 100%; }\n' +
      '}');

    // 9) 「液」：按压时玻璃轻微压扁（只给真正可点的两个表面，避免动到消息气泡的滚动）。
    rule(sel(['.composer-icon-button'], ['thickness'], ':active'), [
      'transform: scale(0.965) !important;'
    ]);
    rule(sel(['.composer-overflow-popover'], ['thickness'], ':active'), [
      'transform: scale(0.985) !important;'
    ]);

    // 10) 窗口不可见时暂停所有 NTS 动画（0.4.0 性能项）：后台标签页不再持续合成。
    //     选择器用 :root[data-nts-paused] + 4 个 :root，压过生成 CSS 里的高特异度规则。
    var PAUSE_HEAD = ':root[data-nts-paused]:root:root:root:root body ';
    var pauseSel = [];
    for (var q = 0; q < FX_SURFACES.length; q++) {
      pauseSel.push(PAUSE_HEAD + FX_SURFACES[q] + '::after');
    }
    for (var n2 = 0; n2 < dual.length; n2++) {
      pauseSel.push(PAUSE_HEAD + dual[n2] + '::before');
    }
    // 0.4.3：面板装饰层已从历史浮层 (scroll) 迁到紧凑态真实面板
    // (.compact-chat-surface-frame) 的 ::after，与生成 CSS 的 NTS_GLOW 一致。
    pauseSel.push(PAUSE_HEAD + '.compact-chat-surface-frame::after');
    lines.push(pauseSel.join(',\n') + ' { animation-play-state: paused !important; }');

    // 11) 减少动态偏好：停掉扫动/焦散（光带停在视野外，只剩 rim / 厚度 / 镜面，不闪）。
    var rmTargets = FX_SURFACES.slice();
    var rmSel = [];
    for (var r = 0; r < rmTargets.length; r++) {
      rmSel.push(':root[' + ACTIVE_ATTR + '] body ' + rmTargets[r] + '::after');
    }

    // 12) 主题切换过渡（v1.5.20）：applyPalette 检测到版本戳变化时给 :root 打 420ms
    //     的 data-nts-fade，功能层颜色/边框/阴影在 280ms 内渐变而不是硬切。
    //     初始加载（无 previousStamp）与内容未变都不闪；减少动态偏好里直接关掉。
    var FADE_SURFACES = ['.window-topbar', '.composer-input-shell', '.compact-chat-surface-frame',
      '.composer-icon-button', '.composer-overflow-popover',
      '.message-bubble-user', '.message-bubble-assistant',
      '.compact-export-history-bubble', '.message-list', '.chat-body'];
    var fadeSel = [];
    for (var fd = 0; fd < FADE_SURFACES.length; fd++) {
      fadeSel.push(LIGHT + '[data-nts-fade] body ' + FADE_SURFACES[fd]);
      fadeSel.push(DARK + '[data-nts-fade] body ' + FADE_SURFACES[fd]);
    }
    lines.push(fadeSel.join(',\n') + ' {\n  transition: background-color .28s ease, color .28s ease,' +
      ' border-color .28s ease, box-shadow .28s ease, outline-color .28s ease !important;\n}');
    lines.push('@media (prefers-reduced-motion: reduce) {');
    lines.push('  ' + rmSel.join(',\n  ') + ' { animation: none !important; }');
    lines.push('  ' + fadeSel.join(',\n  ') + ' { transition: none !important; }');
    lines.push('}');

    return lines.join('\n');
  })();

  // 包在 nts-theme 的子层 nts-theme.materials 里：静态调色板表整张在 @layer nts-theme
  // 内用 !important，而带层的 !important 一律压过不带层的 —— 材质规则若不进层，
  // 开启材质时背景 / 模糊 / 阴影 / 圆角都会被调色板覆盖。对 !important 来说，
  // 显式子层又压过父层自己的规则，所以材质赢调色板，同时仍压过宿主无层的 !important。
  // 这里只有 --nts-mt-* 自定义属性是普通声明，没有别的层定义它们，不受层序影响。
  var MATERIAL_CSS = [
    '@layer nts-theme.materials {',
    '/* ===== 高级材质层（契约 0.4.0） =====',
    '   全部规则门控在 :root[data-nts-mt] 上：没有该属性 = 一条都不匹配 = 应用完全保持原始外观。',
    '   只写 background / border / box-shadow / backdrop-filter / border-radius / animation，',
    '   不碰 color / display / position / width / height / overflow / z-index / transform ——',
    '   文本可读性、布局与点击区域都不受材质影响。 */',
    ':root[data-nts-mt] { --nts-mt-mx: 50%; --nts-mt-my: 50%; --nts-mt-light-angle: 162deg; }',
    '',
    '/* ---- 组件级变量绑定（先查组件专属值，缺失则回退全局） ---- */',
    ':root[data-nts-mt] .compact-chat-surface-frame,',
    ':root[data-nts-mt] .chat-window,',
    ':root[data-nts-mt] #react-chat-window-shell {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-chat, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-chat, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-chat, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-chat, var(--nts-mt-radius-card));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-chat, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .message-bubble-user,',
    ':root[data-nts-mt] .message-bubble-assistant,',
    ':root[data-nts-mt] .message-bubble-tool,',
    ':root[data-nts-mt] .message-bubble-system,',
    ':root[data-nts-mt] .compact-export-history-bubble {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-bubble, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-bubble, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-bubble, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-bubble, var(--nts-mt-radius));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-bubble, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .composer-input-shell,',
    ':root[data-nts-mt] .composer-input,',
    ':root[data-nts-mt] .composer-panel {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-input, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-input, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-input, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-input, var(--nts-mt-radius-input));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-input, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .message-list {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-sidebar, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-sidebar, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-sidebar, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-sidebar, var(--nts-mt-radius-card));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-sidebar, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .window-topbar {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-topbar, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-topbar, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-topbar, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-topbar, var(--nts-mt-radius-card));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-topbar, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] body.api-key-settings-page #main-content {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-settings, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-settings, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-settings, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-settings, var(--nts-mt-radius-dialog));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-settings, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .modal-overlay,',
    ':root[data-nts-mt] .modal-dialog,',
    ':root[data-nts-mt] .modal-content {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-dialog, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-dialog, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-dialog, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-dialog, var(--nts-mt-radius-dialog));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-dialog, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .agent-plugin-view {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-plugin, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-plugin, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-plugin, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-plugin, var(--nts-mt-radius-card));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-plugin, var(--nts-mt-shadow));',
    '}',
    ':root[data-nts-mt] .composer-overflow-popover,',
    ':root[data-nts-mt] .composer-overflow-menu,',
    ':root[data-nts-mt] .composer-icon-popover {',
    '  --nts-mt-bg-c: var(--nts-mt-c-bg-menu, var(--nts-mt-bg));',
    '  --nts-mt-blur-c: var(--nts-mt-c-blur-menu, var(--nts-mt-blur));',
    '  --nts-mt-bw-c: var(--nts-mt-c-border-width-menu, var(--nts-mt-border-width));',
    '  --nts-mt-radius-c: var(--nts-mt-c-radius-menu, var(--nts-mt-radius-card));',
    '  --nts-mt-shadow-c: var(--nts-mt-c-shadow-menu, var(--nts-mt-shadow));',
    '}',
    '',
    '/* ---- 基础面：一次声明，全部走 -c 变量 ---- */',
    ':root[data-nts-mt]:root:root:root:root body .compact-chat-surface-frame,',
    ':root[data-nts-mt]:root:root:root:root body .chat-window,',
    ':root[data-nts-mt]:root:root:root:root body #react-chat-window-shell,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-user,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-assistant,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-tool,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-system,',
    ':root[data-nts-mt]:root:root:root:root body .compact-export-history-bubble,',
    ':root[data-nts-mt]:root:root:root:root body .composer-input-shell,',
    ':root[data-nts-mt]:root:root:root:root body .composer-panel,',
    ':root[data-nts-mt]:root:root:root:root body .message-list,',
    ':root[data-nts-mt]:root:root:root:root body .window-topbar,',
    ':root[data-nts-mt]:root:root:root:root body .modal-dialog,',
    ':root[data-nts-mt]:root:root:root:root body .modal-overlay,',
    ':root[data-nts-mt]:root:root:root:root body .agent-plugin-view,',
    ':root[data-nts-mt]:root:root:root:root body .composer-overflow-popover,',
    ':root[data-nts-mt]:root:root:root:root body.api-key-settings-page #main-content,',
    ':root[data-nts-mt]:root:root:root:root body.api-key-settings-page .container,',
    ':root[data-nts-mt]:root:root:root:root body .composer-icon-popover {',
    '  background-color: var(--nts-mt-bg-c) !important;',
    '  background-image:',
    '    var(--nts-mt-inner-img),',
    '    var(--nts-mt-inner2),',
    '    var(--nts-mt-rim-img),',
    '    var(--nts-mt-sheen-img),',
    '    var(--nts-mt-spot),',
    '    var(--nts-mt-noise-img),',
    '    var(--nts-mt-ambient-img),',
    '    linear-gradient(var(--nts-mt-refract-tint), var(--nts-mt-refract-tint)),',
    '    var(--nts-mt-dye) !important;',
    '  background-size: auto, auto, auto, auto, auto, auto, var(--nts-mt-ambient-size) var(--nts-mt-ambient-size),',
    '    var(--nts-mt-refract-size) var(--nts-mt-refract-size), var(--nts-mt-drift-size) !important;',
    '  background-repeat: no-repeat !important;',
    '  background-position: 0 0, 0 0, 0 0, 0 0, center, 0 0, 0 0, 0 0, 0% 50% !important;',
    '  border-style: solid !important;',
    '  border-color: var(--nts-mt-border) !important;',
    '  border-width: var(--nts-mt-bw-c) !important;',
    '  border-radius: var(--nts-mt-radius-c) !important;',
    '  box-shadow:',
    '    var(--nts-mt-shadow-c),',
    '    var(--nts-mt-inset),',
    '    inset 0 var(--nts-mt-edge-width) 0 var(--nts-mt-edge-t),',
    '    inset 0 calc(-1 * var(--nts-mt-edge-width)) 0 var(--nts-mt-edge-b),',
    '    inset var(--nts-mt-edge-width) 0 0 var(--nts-mt-edge-l),',
    '    inset calc(-1 * var(--nts-mt-edge-width)) 0 0 var(--nts-mt-edge-r),',
    '    inset 0 0 var(--nts-mt-lens-blur) var(--nts-mt-lens-glow),',
    '    inset 0 0 calc(var(--nts-mt-edge-width) * 6) var(--nts-mt-rim-shade),',
    '    inset 0 calc(var(--nts-mt-edge-width) * -1.2) calc(var(--nts-mt-edge-width) * 1.8) var(--nts-mt-under-glow),',
    '    inset 0 calc(var(--nts-mt-lens) * -1.4) calc(var(--nts-mt-lens) * 2.8) calc(var(--nts-mt-lens) * -1) var(--nts-mt-heel),',
    '    inset var(--nts-mt-disp-shift) 0 var(--nts-mt-disp-blur) var(--nts-mt-disp-warm),',
    '    inset calc(var(--nts-mt-disp-shift) * -1) 0 var(--nts-mt-disp-blur) var(--nts-mt-disp-cool),',
    '    inset 0 calc(-1 * var(--nts-mt-liquid-amp)) calc(var(--nts-mt-liquid-amp) * 2) var(--nts-mt-refract-tint) !important;',
    '  transition: background-color var(--nts-mt-exit-duration) var(--nts-mt-easing),',
    '              box-shadow var(--nts-mt-hover-duration) var(--nts-mt-easing) !important;',
    '}',

    '/* 背景模糊只给自身不带 transform / filter 的容器；壳体 / 内层 / 弹出层 / 导出气泡在下方禁模糊组。 */',
    ':root[data-nts-mt]:root:root:root:root body .compact-chat-surface-frame,',
    ':root[data-nts-mt]:root:root:root:root body .chat-window,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-user,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-assistant,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-tool,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-system,',
    ':root[data-nts-mt]:root:root:root:root body .composer-input-shell,',
    ':root[data-nts-mt]:root:root:root:root body .composer-panel,',
    ':root[data-nts-mt]:root:root:root:root body .message-list,',
    ':root[data-nts-mt]:root:root:root:root body .window-topbar,',
    ':root[data-nts-mt]:root:root:root:root body .modal-dialog,',
    ':root[data-nts-mt]:root:root:root:root body .modal-overlay,',
    ':root[data-nts-mt]:root:root:root:root body .agent-plugin-view,',
    ':root[data-nts-mt]:root:root:root:root body .composer-overflow-popover,',
    ':root[data-nts-mt]:root:root:root:root body.api-key-settings-page #main-content,',
    ':root[data-nts-mt]:root:root:root:root body.api-key-settings-page .container {',
    '  -webkit-backdrop-filter: blur(var(--nts-mt-blur-c)) saturate(var(--nts-mt-saturation)) brightness(var(--nts-mt-brightness)) contrast(1.03) !important;',
    '  backdrop-filter: blur(var(--nts-mt-blur-c)) saturate(var(--nts-mt-saturation)) brightness(var(--nts-mt-brightness)) contrast(1.03) !important;',
    '}',
    '/* 聊天窗真正承载底色的是内层 root（调色板层用 !important 给它上实色），',
    '   材质启用时让它让位，玻璃才能从外壳透出来。 */',
    ':root[data-nts-mt]:root:root:root:root:root body #react-chat-window-root {',
    '  background-color: var(--nts-mt-bg-c, var(--nts-mt-bg)) !important;',
    '  background-image: none !important;',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '/* 外壳 / 内层 / 弹出层 / 导出气泡自身带 transform、filter 或 will-change（见 audit_mt_backdrop_hazards.py），',
    '   Chromium 在它们上面做 backdrop-filter 容易整块不绘制：模糊一律撤掉，玻璃靠底色 / 多层背景 / 边框 / 阴影表达。 */',
    ':root[data-nts-mt]:root:root:root:root:root body #react-chat-window-shell,',
    ':root[data-nts-mt]:root:root:root:root:root body .composer-icon-popover,',
    ':root[data-nts-mt]:root:root:root:root:root body .compact-export-history-bubble {',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '',
    '/* ---- 设置窗口（api_key_settings.html）：面板里「设置」组件的落点 ----',
    '   它已并入上面的基础面规则（同一套 9 层背景 + 完整 box-shadow），',
    '   这样设置窗口与聊天窗才是同一块玻璃。只有圆角要单独收掉 ——',
    '   全屏窗口加圆角会把内容裁掉；.container 仍从基础面拿 radius-c。 */',
/* 消息区是整块滚动大面：背后就是同一块窗玻璃，模糊前后看不出差别，
   但每次滚动都要整块重算 backdrop-filter —— 滚动卡顿的头号来源。背景/圆角/阴影照留。 */
    ':root[data-nts-mt]:root:root:root body .message-list {',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '',
    ':root[data-nts-mt]:root:root body.api-key-settings-page #main-content {',
    '  border-radius: 0 !important;',
    '}',

    '/* 设置页内部：宿主把内容区与配置卡片写死成 #fff，只换外壳还是白，',
    '   所以内层也要跟随。暗色主题下宿主 dark-mode.css 已有专门规则，这里不抢。 */',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .container-content {',
    '  background-color: transparent !important;',
    '}',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .model-config-container,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .nested-model-config-container,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .custom-api-container {',
    '  background-color: var(--nts-mt-bg-c) !important;',
    '  border: var(--nts-mt-bw-c) solid var(--nts-mt-border) !important;',
    '  border-radius: var(--nts-mt-radius-c) !important;',
    '  box-shadow: var(--nts-mt-shadow-c) !important;',
    '}',
    '  :root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .field-row input:focus,',
    '  :root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .field-row select:focus,',
    '  :root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .api-provider-dropdown-trigger:focus-visible {',
    '  border-color: var(--nts-accent, #40C5F1) !important;',
    '  box-shadow: 0 0 0 3px rgba(64, 197, 241, 0.18) !important;',
    '}',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .field-row input,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .field-row select,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page #current-api-key,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .api-key-info,',
    ':root[data-nts-mt]:root:root:not([data-theme="dark"]) body.api-key-settings-page .api-provider-dropdown-trigger {',
    '  background-color: var(--nts-mt-bg-c) !important;',
    '  border: var(--nts-mt-bw-c) solid var(--nts-mt-border) !important;',
    '  border-radius: var(--nts-mt-radius-input) !important;',
    '}',
    '',
    '/* ---- 省电模式：高频小元素不再参与背景模糊合成 ----',
    '   气泡/输入/顶栏背后就是半透明的窗口底，模糊前后几乎看不出差别，',
    '   但一屏二十个气泡就是二十层 backdrop-filter。saver 档干脆不声明。 */',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .message-bubble-user,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .message-bubble-assistant,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .message-bubble-tool,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .message-bubble-system,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .compact-export-history-bubble,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .composer-input-shell,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .composer-panel,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .message-list,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .window-topbar,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .composer-overflow-popover,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root body .composer-icon-popover {',
    '      -webkit-backdrop-filter: none !important;',
    '      backdrop-filter: none !important;',
    '}',

    '/* ---- 按钮：体量小，不做噪点/追光，只保留底色/描边/圆角/阴影 ---- */',
    ':root[data-nts-mt]:root:root body .composer-icon-button,',
    ':root[data-nts-mt]:root:root body .composer-tool-btn,',
    ':root[data-nts-mt]:root:root body .send-button-circle,',
    ':root[data-nts-mt]:root:root body #sendButton {',
    '  background-color: var(--nts-mt-c-bg-button, var(--nts-mt-bg)) !important;',
    '  -webkit-backdrop-filter: blur(var(--nts-mt-c-blur-button, var(--nts-mt-blur))) saturate(var(--nts-mt-saturation)) !important;',
    '  backdrop-filter: blur(var(--nts-mt-c-blur-button, var(--nts-mt-blur))) saturate(var(--nts-mt-saturation)) !important;',
    '  border: var(--nts-mt-c-border-width-button, var(--nts-mt-border-width)) solid var(--nts-mt-border) !important;',
    '  border-radius: var(--nts-mt-c-radius-button, var(--nts-mt-radius-button, var(--nts-mt-radius))) !important;',
    '  box-shadow: var(--nts-mt-c-shadow-button, var(--nts-mt-shadow)),',
    '    inset 0 1px 0 var(--nts-mt-sheen),',
    '    inset 0 0 var(--nts-mt-lens-blur) var(--nts-mt-lens-glow) !important;',
    '  transition: box-shadow var(--nts-mt-hover-duration) var(--nts-mt-easing) !important;',
    '}',
    '',
    '/* ---- 动态：染料缓慢流动 + 环境反射扫过（--nts-mt-anim 关闭时是 none） ---- */',
    '@keyframes nts-mt-drift {',
    '  0%   { background-position: 0 0, 0 0, 0 0, 0 0, center, 0 0, 0 0, 0 0, 0% 50%; }',
    '  50%  { background-position: 0 0, 0 0, 3% 3%, 0 0, center, 0 0, 0 0, 0 0, 100% 50%; }',
    '  100% { background-position: 0 0, 0 0, 0 0, 0 0, center, 0 0, 0 0, 0 0, 0% 50%; }',
    '}',
    ':root[data-nts-mt] .compact-chat-surface-frame,',
    ':root[data-nts-mt] .chat-window {',
    '  animation: var(--nts-mt-anim) !important;',
    '}',
    '',
    '/* ---- 交互动效：进入 / 展开 / 悬停 / 按压 ---- */',
    '@keyframes nts-mt-enter { from { opacity: .01; } to { opacity: 1; } }',
    '@keyframes nts-mt-expand { from { opacity: .01; } to { opacity: 1; } }',
    ':root[data-nts-mt] .message-bubble-user,',
    ':root[data-nts-mt] .message-bubble-assistant {',
    '  animation: nts-mt-enter var(--nts-mt-enter-duration) var(--nts-mt-easing) both !important;',
    '}',
    ':root[data-nts-mt] .composer-overflow-popover,',
    ':root[data-nts-mt] .composer-overflow-menu,',
    ':root[data-nts-mt] .composer-icon-popover,',
    ':root[data-nts-mt] .modal-dialog {',
    '  animation: nts-mt-expand var(--nts-mt-expand-duration) var(--nts-mt-easing) both !important;',
    '}',
    ':root[data-nts-mt]:root:root body .composer-icon-button:hover,',
    ':root[data-nts-mt]:root:root body .composer-tool-btn:hover,',
    ':root[data-nts-mt]:root:root body .send-button-circle:hover,',
    ':root[data-nts-mt]:root:root body .message-bubble-assistant:hover,',
    ':root[data-nts-mt]:root:root body .composer-overflow-popover:hover,',
    ':root[data-nts-mt]:root:root body .modal-dialog:hover {',
    '  box-shadow:',
    '    var(--nts-mt-shadow-c),',
    '    var(--nts-mt-inset),',
    '    inset 0 0 0 1px rgba(255, 255, 255, var(--nts-mt-hover)),',
    '    inset 0 var(--nts-mt-edge-width) 0 var(--nts-mt-edge-t),',
    '    inset 0 0 var(--nts-mt-lens-blur) var(--nts-mt-lens-glow),',
    '    inset 0 0 calc(var(--nts-mt-edge-width) * 6) var(--nts-mt-rim-shade),',
    '    inset 0 calc(var(--nts-mt-edge-width) * -1.2) calc(var(--nts-mt-edge-width) * 1.8) var(--nts-mt-under-glow),',
    '    inset 0 calc(var(--nts-mt-lens) * -1.4) calc(var(--nts-mt-lens) * 2.8) calc(var(--nts-mt-lens) * -1) var(--nts-mt-heel) !important;',
    '}',
    ':root[data-nts-mt]:root:root body .composer-icon-button:active,',
    ':root[data-nts-mt]:root:root body .composer-tool-btn:active,',
    ':root[data-nts-mt]:root:root body .send-button-circle:active,',
    ':root[data-nts-mt]:root:root body #sendButton:active {',
    '  box-shadow: var(--nts-mt-shadow-c), inset 0 2px 8px rgba(0, 0, 0, var(--nts-mt-press)) !important;',
    '  transition-duration: var(--nts-mt-press-duration) !important;',
    '}',
    '',
    '/* ---- 降级：内核不支持背景模糊时，用更实的底色换可读性（不假装有玻璃） ---- */',
    '@supports not ((-webkit-backdrop-filter: blur(2px)) or (backdrop-filter: blur(2px))) {',
    '  :root[data-nts-mt]:root:root body .compact-chat-surface-frame,',
    '  :root[data-nts-mt]:root:root body .message-bubble-user,',
    '  :root[data-nts-mt]:root:root body .message-bubble-assistant,',
    '  :root[data-nts-mt]:root:root body .composer-input-shell,',
    '  :root[data-nts-mt]:root:root body .modal-dialog,',
    '  :root[data-nts-mt]:root:root body .agent-plugin-view {',
    '    background-image: linear-gradient(var(--nts-mt-bg-c), var(--nts-mt-bg-c)),',
    '      linear-gradient(var(--nts-mt-bg-c), var(--nts-mt-bg-c)),',
    '      var(--nts-mt-inner2), var(--nts-mt-sheen-img), var(--nts-mt-rim-img), var(--nts-mt-spot), var(--nts-mt-noise-img),',
    '      var(--nts-mt-dye) !important;',
    // 降级段 8 层，必须自带 size/position：否则会继承主规则那份 9 值，
    // 噪点被套上 ambient-size（拉成色块）、染料失去 drift-size（不再流动）。
    '    background-size: auto, auto, auto, auto, auto, auto, auto, var(--nts-mt-drift-size) !important;',
    '    background-position: 0 0, 0 0, 0 0, 0 0, 0 0, 0 0, 0 0, 0% 50% !important;',
    '  }',
    '}',
    '',
    '/* ---- 系统级减弱动效：材质不做任何流动/进入动画 ---- */',
    '@media (prefers-reduced-motion: reduce) {',
    '  :root[data-nts-mt] .compact-chat-surface-frame,',
    '  :root[data-nts-mt] .chat-window,',
    '  :root[data-nts-mt] .composer-overflow-menu,',
    '  :root[data-nts-mt] .message-bubble-user,',
    '  :root[data-nts-mt] .message-bubble-assistant,',
    '  :root[data-nts-mt] .composer-overflow-popover,',
    ':root[data-nts-mt] .composer-icon-popover,',
    '  :root[data-nts-mt] .modal-dialog { animation: none !important; }',
    '}',
    '',
    '/* ---- 紧凑聊天（/chat 悬浮胶囊）：外层包裹保持透明 ----',
    '   宿主 index.css 在紧凑模式下把 root / .chat-window / .composer-panel 等外层设为透明，',
    '   只让 .compact-chat-surface-frame 这只胶囊有外观。材质层带层后其 !important 会压过',
    '   宿主那条规则，所以这里按同一范围把外层再收回透明；胶囊本身照常上材质。 */',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root,',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root .app-shell,',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root .chat-window,',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root .composer-panel,',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root .composer,',
    ':root[data-nts-mt]:root:root:root:root:root body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized) #react-chat-window-root .compact-chat-surface-shell {',
    '  background: transparent !important;',
    '  box-shadow: none !important;',
    '  border: 0 !important;',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '  animation: none !important;',
    '}',
    '}'
  ].join('\n');

  // -------------------- 液态玻璃：指针镜面高光 + 滚动流动（v1.5.14，2026-11 重写为 v1.5.19）
  //
  // 只写 CSS 变量，不碰任何宿主/主题声明；rAF 节流，pointermove 用 passive。
  // 每个玻璃表面记自己的目标位置（百分比）与当前值，逐帧向目标靠 22%，
  // 所以高光是「滑过去」而不是瞬间跳过去 —— 指针停下后动画自然收敛并停帧。
  //
  // v1.5.19 修的四个「鼠标动了却没反应」：
  //   1. 旧版只在 pointermove 时重绘 —— 指针不动、页面在滚/新消息插入时，
  //      高光位置还停在旧坐标；现在滚动（捕获阶段，含嵌套滚动容器）、
  //      resize、DOM 重建后都会重绘。
  //   2. 旧版 MutationObserver 是 350ms 防抖，聊天流式输出时 DOM 一直在变，
  //      计时器被无限重置 → 表面列表永不刷新（新气泡没光效）；改成 220ms 节流。
  //   3. 新插入的表面旧版要等下一次指针移动才拿到坐标；现在重建后立刻补一次。
  //   4. 旧版按 0.1% 字符串比较跳过写值，小范围移动会被吃掉；现在按数值插值，
  //      收敛到 0.06% 以内才停帧。
  //
  // v1.5.20（性能 + 视觉打磨）：
  //   1. 光效整套休眠：调色板里一个光效变量都没有（非玻璃主题）时，
  //      refreshGlassPanels / MutationObserver / onGlassPointer 直接返回，
  //      不再每 220ms 扫 DOM、也不起 rAF —— 普通主题下指针追踪零开销。
  //   2. 主题切换过渡：applyPalette 检测到版本戳变化时给 :root 打 420ms 的
  //      data-nts-fade，功能层颜色/边框/阴影在 280ms 内渐变，不再硬切。
  // -------------------- 包含块修复（v1.5.17）
  //
  // 光效层与面板装饰层都是 `position:absolute; inset:0` 的伪元素，依赖宿主元素
  // 自己是定位祖先。实测（compact 态 neko-chat-window.css）这几个宿主是
  // `position: static`：.composer-input-shell、.message-bubble-user、
  // .message-bubble-assistant、.compact-export-history-scroll。它们上面的
  // absolute 伪元素会一路逃到最近的定位祖先，也就是 position:fixed 的
  // .compact-export-history-anchor —— 一块比可见面板大、四角笔直的大盒子，
  // 于是表现为「每个主题都多出一层直角光屏」。
  //
  // 不能写死在 CSS 里：有的宿主本身就是 absolute（.composer-overflow-popover），
  // 统一设 relative 会改掉它的定位语义。所以这里只对**计算值为 static** 的元素
  // 补 inline relative（布局等价，不产生偏移），并打标记避免重复处理。
  var GLASS_CONTAIN_SEL = [
    '.composer-input-shell', '.message-bubble-user',
    '.message-bubble-assistant'
  ].join(',');

  function ensureGlassContainment() {
    var nodes;
    try { nodes = document.querySelectorAll(GLASS_CONTAIN_SEL); } catch (e) { return; }
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute('data-nts-box') === '1') continue;
      var pos = 'static';
      try { pos = window.getComputedStyle(el).position || 'static'; } catch (e2) { /* 保底当 static */ }
      if (pos === 'static') el.style.position = 'relative';
      el.setAttribute('data-nts-box', '1');
    }
  }

  var GLASS_SURFACE_SEL = [
    '.window-topbar', '.composer-input-shell', '.compact-chat-surface-frame',
    '.composer-icon-button', '.composer-overflow-popover',
    '.message-bubble-user', '.message-bubble-assistant',
    '.compact-export-history-bubble'
  ].join(',');
  var GLASS_FOLLOW = 0.22;      // 每帧向指针目标靠的比例（越大越跟手）
  var GLASS_SNAP = 0.0006;      // 差值小于 0.06% 就吸附，避免无限微动画
  var GLASS_REBUILD_MS = 220;   // DOM 变动后的重建节流
  var GLASS_STALE_MS = 900;     // 列表超过这么久没重建，指针一动就补一次
  // 光效门控：调色板里一个「光效用变量」都没有时，指针追踪整套直接休眠 ——
  // 非玻璃主题不必每 220ms 扫一遍 DOM，也不跑 rAF（v1.5.20 性能优化）。
  var GLASS_FX_VARS = [
    'glass-filter', 'glass-tint', 'glass-highlight', 'glass-rim', 'glass-specular',
    'glass-lens', 'glass-thickness', 'glass-sheen', 'glass-noise', 'ambient-glow',
    'glass-dispersion', 'glass-caustic', 'bubble-glass-filter', 'bubble-glass-tint'
  ];
  var glassFxGates = (function () {
    var out = [];
    for (var gv = 0; gv < GLASS_FX_VARS.length; gv++) {
      out.push(GATE_ATTR + 'l-' + GLASS_FX_VARS[gv], GATE_ATTR + 'd-' + GLASS_FX_VARS[gv]);
    }
    return out;
  })();

  function glassFxActive() {
    var de = document.documentElement;
    if (!de || !de.hasAttribute) return false;
    for (var gi = 0; gi < glassFxGates.length; gi++) {
      if (de.hasAttribute(glassFxGates[gi])) return true;
    }
    return false;
  }

  // 主题切换的短暂过渡（v1.5.20）：应用新主题时给根节点打 420ms 的 data-nts-fade，
  // 让功能层颜色/边框/阴影在 280ms 内渐变而不是硬切；减少动态时由 CSS 关掉。
  var FADE_ATTR = 'data-nts-fade';
  var fadeTimer = 0;

  function flashThemeSwap() {
    var de = document.documentElement;
    if (!de || !de.setAttribute) return;
    if (fadeTimer) clearTimeout(fadeTimer);
    de.setAttribute(FADE_ATTR, '');
    fadeTimer = setTimeout(function () {
      fadeTimer = 0;
      if (de.removeAttribute) de.removeAttribute(FADE_ATTR);
    }, 420);
  }

  var glassPointerBound = false;
  var glassPanels = [];
  var glassPointer = { x: -1, y: -1, raf: 0, seen: false };
  var glassScrollTimer = 0;
  var glassObserver = null;
  var glassRebuildTimer = 0;
  var glassLastRebuild = 0;

  function glassNodes() {
    try { return document.querySelectorAll(GLASS_SURFACE_SEL); } catch (e) { return []; }
  }

  function refreshGlassPanels() {
    glassRebuildTimer = 0;
    glassLastRebuild = Date.now ? Date.now() : new Date().getTime();
    // 没有光效变量时不清扫 DOM：非玻璃主题下这里每 220ms 都会白跑一次。
    if (!glassFxActive()) { glassPanels = []; return; }
    ensureGlassContainment();
    var nodes = glassNodes();
    var next = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var keep = null;
      for (var j = 0; j < glassPanels.length; j++) {
        if (glassPanels[j].el === el) { keep = glassPanels[j]; break; }
      }
      // 新表面：cx/cy 留空，第一帧直接落在指针位置（不从 0,0 滑过来）
      next.push(keep || { el: el, cx: null, cy: null, tx: 0, ty: 0, sx: '', sy: '', lx: '', ly: '' });
    }
    glassPanels = next;
    // 重建后立刻按当前指针重绘：新挂载的气泡/顶栏不用等用户再动鼠标
    scheduleGlassSpecular();
  }

  function scheduleGlassRebuild() {
    if (glassRebuildTimer) return; // 节流：持续变动时每 220ms 也一定重建一次
    glassRebuildTimer = setTimeout(refreshGlassPanels, GLASS_REBUILD_MS);
  }

  function glassPct(value) {
    return (value * 100).toFixed(1) + '%';
  }

  function paintGlassSpecular() {
    glassPointer.raf = 0;
    if (!glassPointer.seen || !glassPanels.length) return;
    var moving = false;
    for (var i = 0; i < glassPanels.length; i++) {
      var g = glassPanels[i], r;
      try { r = g.el.getBoundingClientRect(); } catch (e) { continue; }
      if (!r || r.width < 8 || r.height < 8) continue;
      var tx = (glassPointer.x - r.left) / r.width;
      var ty = (glassPointer.y - r.top) / r.height;
      tx = Math.max(-0.25, Math.min(1.25, tx));
      ty = Math.max(-0.25, Math.min(1.25, ty));
      g.tx = tx; g.ty = ty;
      if (g.cx === null || g.cy === null) { g.cx = tx; g.cy = ty; }
      var dx = tx - g.cx, dy = ty - g.cy;
      if (Math.abs(dx) > GLASS_SNAP || Math.abs(dy) > GLASS_SNAP) {
        g.cx += dx * GLASS_FOLLOW;
        g.cy += dy * GLASS_FOLLOW;
        moving = true;
      } else {
        g.cx = tx; g.cy = ty;
      }
      // 镜面：平滑后的位置（有一点「滑」的惯性）
      var sx = glassPct(g.cx), sy = glassPct(g.cy);
      if (sx !== g.sx || sy !== g.sy) {
        g.sx = sx; g.sy = sy;
        g.el.style.setProperty('--nts-spec-x', sx);
        g.el.style.setProperty('--nts-spec-y', sy);
      }
      // 指针光源：直接用真实指针位置（比镜面更跟手），纵向抬 18% 当作「光源在上方」
      var lx = glassPct(tx), ly = glassPct(Math.max(0, ty - 0.18));
      if (lx !== g.lx || ly !== g.ly) {
        g.lx = lx; g.ly = ly;
        g.el.style.setProperty('--nts-light-x', lx);
        g.el.style.setProperty('--nts-light-y', ly);
      }
    }
    if (moving) scheduleGlassSpecular(); // 还有差距就继续跑帧，收敛后自动停
  }

  function scheduleGlassSpecular() {
    if (glassPointer.raf) return;
    glassPointer.raf = window.requestAnimationFrame
      ? window.requestAnimationFrame(paintGlassSpecular)
      : window.setTimeout(paintGlassSpecular, 16);
  }

  function onGlassPointer(e) {
    if (!e || typeof e.clientX !== 'number' || typeof e.clientY !== 'number') return;
    glassPointer.x = e.clientX;
    glassPointer.y = e.clientY;
    glassPointer.seen = true;
    if (!glassFxActive()) {
      if (glassPanels.length) glassPanels = [];
      return;
    }
    // 列表可能因为流式消息/切页而陈旧：指针一动就顺带补建一次
    var now = Date.now ? Date.now() : new Date().getTime();
    if (!glassPanels.length || now - glassLastRebuild > GLASS_STALE_MS) scheduleGlassRebuild();
    scheduleGlassSpecular();
  }

  // 滚动会让「表面」在静止的指针下面移动；scroll 不冒泡，所以用捕获阶段接住嵌套滚动容器。
  function onGlassScroll() {
    var root = document.documentElement;
    /* 非玻璃主题不写 --nts-scroll-k：这个变量只驱动玻璃镜面收敛，写它会触发
     * 整页样式重算，而普通主题的滚动是高频事件。切走时把它归零一次，否则
     * 下次切回玻璃主题仍停在「已收敛」的静止状态。 */
    if (!glassFxActive()) {
      if (root && root.style && root.style.getPropertyValue('--nts-scroll-k') === '1') {
        root.style.setProperty('--nts-scroll-k', '0');
      }
      return;
    }
    if (root && root.style) root.style.setProperty('--nts-scroll-k', '1');
    if (glassScrollTimer) clearTimeout(glassScrollTimer);
    glassScrollTimer = setTimeout(function () {
      if (root && root.style) root.style.setProperty('--nts-scroll-k', '0');
    }, 160);
    scheduleGlassSpecular();
  }

  function syncAnimationPause() {
    try {
      var root = document.documentElement;
      if (!root) return;
      if (document.hidden) root.setAttribute('data-nts-paused', '');
      else root.removeAttribute('data-nts-paused');
    } catch (e) { /* 无 document.hidden 的环境：保持动画 */ }
    if (!document.hidden) { scheduleGlassRebuild(); scheduleGlassSpecular(); }
  }

  function initGlassPointer() {
    refreshGlassPanels();
    if (glassPointerBound) return;
    glassPointerBound = true;
    try {
      // 捕获阶段：即使某个组件 stopPropagation，指针移动也不会丢
      document.addEventListener('pointermove', onGlassPointer, { passive: true, capture: true });
      document.addEventListener('pointerdown', onGlassPointer, { passive: true, capture: true });
      document.addEventListener('pointerover', onGlassPointer, { passive: true, capture: true });
      // 后台标签页暂停所有 NTS 动画：省电，也避免回来后动画相位跳变。
      document.addEventListener('visibilitychange', syncAnimationPause);
      syncAnimationPause();
      document.addEventListener('scroll', onGlassScroll, { passive: true, capture: true });
      window.addEventListener('scroll', onGlassScroll, { passive: true });
      window.addEventListener('resize', function () { refreshGlassPanels(); }, { passive: true });
      // 主题库/消息列表会持续增删节点：节流重建表面列表（旧版防抖在流式输出时会饿死）。
      if (window.MutationObserver && document.body) {
        glassObserver = new MutationObserver(function () {
          if (!glassFxActive()) return; // 非玻璃主题：连重建定时器都不排
          scheduleGlassRebuild();
        });
        glassObserver.observe(document.body, { childList: true, subtree: true });
      }
    } catch (e) { /* 极老 WebView：退化成静态高光位置 */ }
    scheduleGlassSpecular();
  }

  /**
   * 样式表是否真的加载了（href 里带 neko-theme-studio.css 的那张）。
   * 变量注入成功但样式表没加载时，门控属性照样打上、变量照样解析，
   * 却没有任何规则使用它们 —— 外观完全不变且控制台全静默。
   * 这是「面板改了色、本体没反应」最隐蔽的一种形态，必须大声报出来。
   */
  /** 数「真实样式规则」条数，穿透分组规则。
   *
   *  v3.1 起整张表被包在 `@layer nts-theme { … }` 里（为了用 layered !important
   *  压过 index.css 里那几条双 id !important 的原生规则），于是顶层 cssRules
   *  恒为 1。直接报 .length 会让 diagnose() 看起来"只加载了 1 条规则"，把正常
   *  状态误判成半残。这里递归展开 @layer / @media / @supports 再计数。 */
  function countStyleRules(sheet) {
    var n = 0;
    try {
      var rules = sheet && sheet.cssRules;
      if (!rules) return 0;
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        // 分组规则没有 selectorText，但有 cssRules
        if (r.selectorText === undefined && r.cssRules) {
          n += countStyleRules({ cssRules: r.cssRules });
        } else if (r.selectorText !== undefined) {
          n += 1;
        }
      }
    } catch (e) { /* cross-origin 或无权限 */ }
    return n;
  }

  function cssSheetInfo() {
    var info = { loaded: false, rules: 0 };
    try {
      var sheets = document.styleSheets || [];
      for (var i = 0; i < sheets.length; i++) {
        var href = sheets[i].href || '';
        if (href.indexOf('neko-theme-studio.css') !== -1) {
          info.loaded = true;
          info.rules = countStyleRules(sheets[i]);
          break;
        }
      }
    } catch (e3) { /* ignore */ }
    return info;
  }

  /** 样式表缺失只大声报一次，避免轮询刷屏。 */
  var warnedNoCss = false;
  function warnIfCssMissing() {
    if (warnedNoCss) return;
    var info = cssSheetInfo();
    if (info.loaded) return;
    warnedNoCss = true;
    try {
      console.warn('[theme-studio] 样式表 neko-theme-studio.css 未加载：CSS 变量已经注入，' +
        '但没有任何规则去使用它们，所以外观不会变化。' +
        '\n  多半是模板补丁没打上（install.ps1 的 §C），或 /static/css/neko-theme-studio.css 没复制到 static 目录。' +
        '\n  自查：在应用窗口执行 NekoThemeStudio.diagnose() 看 cssLoaded。');
    } catch (e) { /* ignore */ }
  }

  /**
   * 样式表新鲜度自愈（1.4.0）。
   *
   * 模板里的 <link href="/static/css/neko-theme-studio.css?v=<asset>-<css sha8>">
   * 会随每次部署变化，但**已经打开的窗口**不会重新加载页面：HTTP 头是
   * max-age=3600，渲染器还会再缓存一份。于是插件重启、CSS 已更新，用户看到的
   * 仍是旧规则 —— 这正是「面板里改了色，本体没反应」乃至「旧版的 bug 继续
   * 生效」的系统性原因（旧版把 AI 文字色挂在无作用域的 .message-block-* 上，
   * 用户即使已经部署了新 CSS，窗口里跑的还是旧表，串色照旧）。
   *
   * 做法：用 cache:'reload' 直接向服务端要当前文件，把它解析成一张构造样式表
   * 并与页面里已生效的那张比「规则数 + 规则文本长度」签名；不一致说明页面里的
   * 是旧的 → 把 link 的 href 换成一个带新查询串的 URL 强制换表。签名一致时不动
   * （正常加载零额外开销，也不会闪一下）。任何一步失败都静默降级。
   */
  // 自愈不能只做一次。这个函数原来用 `sheetChecked` 守成一次性：启动那一刻对不上
  // 就换表，之后再也不看。但「已经打开的窗口」才是最常见的受害者 —— 用户开着窗口
  // 时插件被更新，页面里的表永远是旧的，而 1500ms 的调色轮询照样在跑、变量照样在
  // 更新，于是面板显示成功、窗口纹丝不动。现在改成轮询里按 SHEET_CHECK_MS 节流复检。
  var SHEET_CHECK_MS = 15000;
  var lastSheetCheckAt = 0;
  var sheetSwapCount = 0;
  function sheetSignature(sheet) {
    try {
      var rules = sheet.cssRules || [];
      var total = 0;
      for (var i = 0; i < rules.length; i++) total += (rules[i].cssText || '').length;
      // 用「真实样式规则条数 + 全部规则文本长度」做签名：只数顶层时，整表被
      // @layer 包起来会让条数恒为 1，届时签名只剩文本长度一半信息，换表检测
      // 会变钝。countStyleRules 会穿透分组规则，签名对包 layer 与不包都可比。
      return countStyleRules(sheet) + ':' + total;
    } catch (e) { return null; }
  }

  function refreshStaleStylesheet(force) {    // 节流：轮询里每 15s 最多复检一次，避免每次心跳都去 fetch 一遍。
    // force=true（启动时）跳过节流。
    var now = Date.now();
    if (!force && now - lastSheetCheckAt < SHEET_CHECK_MS) return;
    lastSheetCheckAt = now;
    var link = null;
    try {
      var links = document.querySelectorAll('link[rel~="stylesheet"][href*="neko-theme-studio.css"]');
      if (!links || !links.length) return;
      link = links[0];
    } catch (e) { return; }

    var href = '';
    var abs = '';
    try {
      href = link.getAttribute('href') || '';
      abs = new URL(href, window.location.href).href;
      if (!/^https?:/i.test(abs)) return;
    } catch (e) { return; }

    // v0.7.0：先看 theme.json 给的指纹。页面 href 里本来就带 -<css8>（模板补丁写的），
    // 和磁盘指纹一比就知道要不要动 —— 一致就零下载返回，不一致只换 hash 段让浏览器
    // 按新 URL 取新文件。只有「页面 href 里没有 hash 段」（老模板）才退回下面的
    // 下载 + 签名比对。
    if (assetMeta.css8) {
      var pageHash = readSheetHash(href);
      if (pageHash === assetMeta.css8) return;
      if (pageHash) {
        var swapped = href.replace(/-[0-9a-f]{8}(?=[?&#]|$)/, '-' + assetMeta.css8);
        if (swapped !== href) {
          sheetSwapCount += 1;
          link.setAttribute('href', swapped);
          try {
            console.warn('[theme-studio] 样式表不是当前版本（页面 ' + pageHash + ' → 磁盘 ' +
              assetMeta.css8 + '），已换表（第 ' + sheetSwapCount + ' 次）。');
          } catch (e5) { /* ignore */ }
          return;
        }
      }
    }

    // 找到页面里已经生效的那张表（href 含同名文件即可）。
    var loaded = null;
    try {
      var sheets = document.styleSheets || [];
      for (var i = 0; i < sheets.length; i++) {
        if ((sheets[i].href || '').indexOf('neko-theme-studio.css') !== -1) { loaded = sheets[i]; break; }
      }
    } catch (e) { /* ignore */ }
    if (!loaded) return;
    var loadedSig = sheetSignature(loaded);
    if (!loadedSig) return;

    // 构造样式表：Chromium 73+ 支持。解析失败就放弃自愈，绝不影响既有样式。
    var probe = null;
    try { probe = new window.CSSStyleSheet(); } catch (e) { return; }

    fetch(abs, { cache: 'reload', credentials: 'same-origin' }).then(function (res) {
      if (!res || !res.ok) return null;
      return res.text();
    }).then(function (css) {
      if (!css) return;
      try { probe.replaceSync(css); } catch (e) { return; }
      var serverSig = sheetSignature(probe);
      if (!serverSig || serverSig === loadedSig) return;
      // 服务端的表与页面上生效的不是同一份（页面是旧的）→ 换表。
      // 先把上一次换表留下的 nts-refresh 参数剥掉再拼，否则每换一次 URL 就长一截。
      var base = abs.replace(/([?&])nts-refresh=\d+/g, '').replace(/[?&]$/, '');
      var sep = base.indexOf('?') === -1 ? '?' : '&';
      var busted = base + sep + 'nts-refresh=' + Date.now();
      sheetSwapCount += 1;
      link.setAttribute('href', busted);
      try {
        console.warn('[theme-studio] 页面里的 neko-theme-studio.css 与服务器上的不一致（窗口打开后插件更新过），' +
          '已强制换表（第 ' + sheetSwapCount + ' 次）。');
      } catch (e4) { /* ignore */ }
    })['catch'](function () { /* 静默降级 */ });
  }

  /**
   * 磁盘上的加载器是否比本实例新？新则重载本页面，让新脚本接管。
   *
   * 为什么必须重载而不是热替换：本脚本是一个已执行的 IIFE，闭包里的
   * NON_COLOR_VARS 白名单、正则与函数全被冻结。插件新增一个长度类变量后，
   * 旧实例会把它当颜色拒掉（`log('reject value', ...)` 只在 DEBUG 下可见），
   * 门控属性永不设置，于是「面板显示已应用、窗口毫无变化」。
   * 这正是用户反复遇到的「改了不生效」的根因，而且无法靠 CSS 侧补救 ——
   * 门控属性由本脚本写入，旧脚本不认识新变量就永远不会写。
   *
   * 安全阀：sessionStorage 记录「触发重载时正在运行的版本」。重载成功后本实例的
   * SCRIPT_VERSION 变成新版本，与记录不再相等，于是下一轮心跳照常比对 —— 这样
   * 才可能连续升级多次（1.4.2 → 1.5.0 → 1.5.2 → …）。只有在「重载了却仍然拿回
   * 同一个旧脚本」（HTTP 缓存没绕开）时两者才会相等，此时停下，避免无限刷新。
   * 另外两次尝试至少间隔 SELF_UPGRADE_MIN_INTERVAL_MS。
   */
  // ------------------------------------------------- 资源指纹（v0.7.0）
  // theme.json 现在带 css_sha8 / loader_version（后端 _asset_meta() 算的磁盘指纹）。
  // 有了它们，两个自愈动作不必再「整份下载再判断」：
  //   15s 一次下载 158KB CSS 只为比签名、60s 一次下载 126KB JS 只为读一行版本号 ——
  //   每小时约 45MB/窗口，笔记本上白烧电。字段缺失（旧版插件）时下面的代码原样
  //   退回旧的下载路径，行为不变。
  var assetMeta = { css8: '', loaderVersion: '' };

  function rememberAssetMeta(p) {
    if (!p || typeof p !== 'object') return;
    var css8 = typeof p.css_sha8 === 'string' ? p.css_sha8 : '';
    if (/^[0-9a-f]{8}$/.test(css8)) assetMeta.css8 = css8;
    var ver = typeof p.loader_version === 'string' ? p.loader_version : '';
    if (/^[0-9]+\.[0-9]+\.[0-9]+$/.test(ver)) assetMeta.loaderVersion = ver;
  }

  function readSheetHash(href) {
    var m = /-([0-9a-f]{8})(?=[?&#]|$)/.exec(String(href || ''));
    return m ? m[1] : '';
  }

  // 升级重载的全部「让路」判断与守卫（从 selfUpgradeIfNeeded 里抽出来的，
  // 逻辑逐字未改 —— 两条路径都必须遵守：草稿不让路、流式输出让路、守卫最后落盘）。
  // 内置之后加载器挂在 23 个页面上，其中大量是编辑器（情感映射、角色卡、记忆浏览…），
  // 它们的未保存状态加载器无从得知。只有常驻的聊天 / 浮层窗口才自动重载：
  // 这些窗口可能跨越一次后端升级一直开着，而它们的「草稿 / 流式输出」让路判断就在下面。
  // 其余页面不自动重载，下次打开时模板里的 ?v= 自然换成新加载器。
  var AUTO_RELOAD_PATHS = { '/subtitle': 1, '/toast': 1, '/agenthud': 1 };
  function autoReloadAllowed() {
    try {
      var path = String(window.location.pathname || '').replace(/\/+$/, '');
      if (AUTO_RELOAD_PATHS[path]) return true;
      return !!document.querySelector('#react-chat-window-root, textarea.composer-input, #textInputBox');
    } catch (e) {
      return false;
    }
  }

  function reloadForUpgrade(remote, now) {
      if (!autoReloadAllowed()) {
        log('self-upgrade skipped: page may hold unsaved edits; new loader applies on next open');
        return;
      }
      // 不能把用户正在输入的草稿刷掉：输入框有内容就先不重载，等下一轮心跳
      // （节流 60s）再试。同理会话正在流式输出时也让路，避免打断回复。
      try {
        var ta = document.querySelector('textarea.composer-input, #textInputBox');
        if (ta && ta.value && String(ta.value).trim()) {
          log('self-upgrade deferred: composer has draft text');
          return;
        }
        if (document.querySelector('.message-block-status, [data-streaming="true"]')) {
          log('self-upgrade deferred: a reply is in flight');
          return;
        }
      } catch (e8) { /* ignore */ }
      // 存「正要离开的版本 + 现在」：重载后新实例的 SCRIPT_VERSION 变成 remote，
      // 与记录的 gVer 不等，于是下一轮照常比对（这是 1.5.0 卡死的修复点）；
      // 若重载后版本没变（缓存没绕开），则靠冷却期兜底，不会永久锁死。
      // 注意：这一行必须在所有「让路」判断之后 —— 以前写在最前面，一旦因为草稿或
      // 流式输出让路，守卫就已经落盘，冷却期 10 分钟内不会再尝试升级。
      try {
        window.sessionStorage.setItem(SELF_UPGRADE_GUARD_KEY, SCRIPT_VERSION + '@' + now);
      } catch (e5) { /* ignore */ }
      try {
        console.warn('[theme-studio] 检测到加载器已升级：本窗口 ' + SCRIPT_VERSION +
          ' → ' + remote + '，正在重新加载本页面以接管（避免新变量被旧脚本丢弃）。');
      } catch (e6) { /* ignore */ }
      try { window.location.reload(); } catch (e7) { /* ignore */ }
  }

  function selfUpgradeIfNeeded() {
    var now = Date.now();
    if (now - selfUpgradeAt < SELF_UPGRADE_MIN_INTERVAL_MS) return;

    var guard = '';
    try { guard = window.sessionStorage.getItem(SELF_UPGRADE_GUARD_KEY) || ''; } catch (e) { guard = ''; }
    if (guard) {
      var gPart = String(guard).split('@');
      var gVer = gPart[0] || '';
      var gAt = parseInt(gPart[1] || '0', 10) || 0;
      // 同一个版本 + 冷却期内 = 刚重载过却还是这个脚本（缓存没绕开），先停。
      // 冷却期过后允许再试一次，避免一次缓存失误永久锁死升级能力。
      if (gVer === SCRIPT_VERSION && (now - gAt) < SELF_UPGRADE_COOLDOWN_MS) return;
    }

    // v0.7.0：theme.json 里已经带 loader_version —— 一致就是零下载返回；不一致就
    // 直接按「新版本已就绪」处理，不必再下载 126KB 的 JS 去正则读一行版本号。
    // 字段缺失（旧版插件）时下面原样走下载路径。
    if (assetMeta.loaderVersion && assetMeta.loaderVersion === SCRIPT_VERSION) return;
    if (assetMeta.loaderVersion) {
      selfUpgradeAt = now;
      reloadForUpgrade(assetMeta.loaderVersion, now);
      return;
    }

    var src = '';
    try {
      var scripts = document.querySelectorAll('script[src*="neko-theme-studio.js"]');
      if (!scripts || !scripts.length) return;
      src = scripts[0].getAttribute('src') || '';
      if (!src) return;
    } catch (e2) { return; }

    var url = '';
    try {
      url = new URL(src, window.location.href).href;
      if (!/^https?:/i.test(url)) return;
      // 加时间戳绕开 HTTP 缓存，否则可能拿回同一份旧脚本。
      url = url.replace(/([?&])nts-upgrade=\d+/g, '').replace(/[?&]$/, '');
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'nts-upgrade=' + now;
    } catch (e3) { return; }

    selfUpgradeAt = now;

    fetch(url, { cache: 'reload', credentials: 'same-origin' }).then(function (res) {
      if (!res || !res.ok) return null;
      return res.text();
    }).then(function (code) {
      if (!code) return;
      // 比版本号：只有服务端脚本确实更新了才重载，否则会无谓刷新页面。
      var m = /SCRIPT_VERSION\s*=\s*'([^']+)'/.exec(code);
      var remote = m ? m[1] : '';
      if (!remote || remote === SCRIPT_VERSION) return;
      reloadForUpgrade(remote, now);
    })['catch'](function () { /* 静默降级：拿不到就不升级，行为与旧版一致 */ });
  }

  /**
   * 当前外观模式。应用的主窗口用 html[data-theme="dark"] 切换暗色
   * （resources/bin/static/theme-manager.js），部分页面同时挂 .dark 类，
   * 两者都认；都没有时退回系统配色。
   */
  function currentMode() {
    try {
      var de = document.documentElement;
      if (de) {
        var t = de.getAttribute('data-theme');
        if (t === 'dark') return 'dark';
        if (t === 'light') return 'light';
        if (de.classList && de.classList.contains('dark')) return 'dark';
      }
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
    } catch (e) { /* ignore */ }
    return 'light';
  }

  /**
   * 「当前模式这一组是空的、另一组却有值」—— 这是最常见的「面板显示已应用、
   * 本体一点没变」：亮色与暗色两组各管各的（已拍板的需求），用户只配了亮色
   * 而应用正跑在暗色，于是变量注入了、门控也打上了，可视区域却没有任何规则命中。
   * 每种组合只提醒一次，切换亮/暗时会重新检查。
   */
  var warnedMode = {};
  function warnIfModeUncovered(nLight, nDark) {
    var mode = currentMode();
    var miss = (mode === 'dark') ? nDark : nLight;
    var other = (mode === 'dark') ? nLight : nDark;
    if (miss !== 0 || other === 0) return;
    var key = mode + ':' + other;
    if (warnedMode[key]) return;
    warnedMode[key] = 1;
    var zh = (mode === 'dark') ? '暗色' : '亮色';
    var otherZh = (mode === 'dark') ? '亮色' : '暗色';
    try {
      console.warn('[theme-studio] 当前是' + zh + '模式，但调色板里' + zh + '这一组是空的' +
        '（' + otherZh + '组有 ' + other + ' 项）。两组互不影响，只配一组不会改变另一组的外观 —— ' +
        '请在主题工坊面板切到「' + zh + '」页签再配一遍，或把应用切到' + otherZh + '模式看看。');
    } catch (e) { /* ignore */ }
  }

  function declBlock(vars) {
    var buf = [];
    for (var k in vars) {
      if (!Object.prototype.hasOwnProperty.call(vars, k)) continue;
      buf.push(k + ':' + vars[k]);
    }
    return buf.join(';');
  }
  // ------------------------------------------- 高级材质层（v1.6.0）
  //
  // 材质变量由后端 materials.compile_materials() 生成：白名单参数、范围钳制、
  // 枚举收敛，且每条都带 --nts-mt- 前缀。这里只做形态体检（前缀 / 长度 /
  // 不能含分号花括号注释），不重跑 sanitizeVars —— 后者按"调色板颜色"语义校验，
  // gradient、多层 shorthand、关键字 none 会被逐条判非法，材质会整体丢失。
  var MT_VAR_PREFIX = '--nts-mt-';
  var MT_ATTR = 'data-nts-mt';
  var MT_QUALITY_ATTR = 'data-nts-mt-quality';
  var MT_PRESET_ATTR = 'data-nts-mt-preset';
  var MT_MAX_VALUE = 400;
  var mtPointerBound = false;
  var mtFollow = 0;
  var mtDelay = 60;

  /** 值形态体检：阻断 CSS 注入（分号 / 花括号 / 尖括号 / 注释起始）。 */
  function mtValueOk(v) {
    if (typeof v !== 'string') return false;
    if (!v || v.length > MT_MAX_VALUE) return false;
    if (/[;{}<>]/.test(v)) return false;
    if (v.indexOf(String.fromCharCode(47, 42)) !== -1) return false;
    return true;
  }

  /** 取一份材质变量表（mode = light | dark），非法项静默丢弃。 */
  function materialVarsOf(p, mode) {
    var out = {};
    var src = p && p.material_vars;
    if (!src || typeof src !== 'object') return out;
    var map = src[mode];
    if (!map || typeof map !== 'object') return out;
    for (var k in map) {
      if (!Object.prototype.hasOwnProperty.call(map, k)) continue;
      // 变量名会被直接拼进 CSS 文本：只认完整的 --nts-mt-* 形状，不能只看前缀。
      if (String(k).indexOf(MT_VAR_PREFIX) !== 0 || !/^--nts-mt-[a-z0-9-]{1,80}$/.test(k)) continue;
      var v = map[k];
      if (typeof v !== 'string') continue;
      v = v.trim();
      if (!mtValueOk(v)) continue;
      out[k] = v;
    }
    return out;
  }

  function countVars(o) {
    var n = 0;
    for (var k in o) {
      if (Object.prototype.hasOwnProperty.call(o, k)) n += 1;
    }
    return n;
  }

  /** 撤掉材质门控属性与指针变量（= 材质完全不起作用）。 */
  function clearMaterialAttrs() {
    var de = document.documentElement;
    if (!de) return;
    de.removeAttribute(MT_ATTR);
    de.removeAttribute(MT_QUALITY_ATTR);
    de.removeAttribute(MT_PRESET_ATTR);
    if (de.style && de.style.removeProperty) {
      de.style.removeProperty('--nts-mt-mx');
      de.style.removeProperty('--nts-mt-my');
      de.style.removeProperty('--nts-mt-light-angle');
    }
    mtFollow = 0;
    applyRootBypass(false);
  }

  /**
   * 聊天窗内层让位：调色板层用 !important 给 #react-chat-window-root 上实色，
   * 玻璃在外壳上会被整块遮住。内联 !important 是唯一能稳赢 author !important 的位置。
   */
  function applyRootBypass(on) {
    var el = document.querySelector ? document.querySelector('#react-chat-window-root') : null;
    if (!el || !el.style || !el.style.setProperty) return;
    if (on) {
      // 只让「实色底」换成本材质自己的玻璃底（--nts-mt-bg），不是清成 transparent：
      // 清成 transparent 时整个窗口只剩壳上那层 15% 白玻璃，浅色壁纸下看起来像「聊天框没了」。
      // background-image 必须保留：那可能是用户的聊天壁纸（--nts-chat-bg-image）。
      // 2026-11-21 用户就是这么看到「聊天框没了」的。
      el.style.setProperty('background-color', 'var(--nts-mt-bg, transparent)', 'important');
    } else {
      el.style.removeProperty('background-color');
      el.style.removeProperty('background-image');
    }
  }

  /**
   * 把 material_attrs 同步到 documentElement。
   * hasVars 为假表示材质未启用 / 无变量 —— 此时必须撤属性，让 MATERIAL_CSS 零匹配。
   * @param {Object<string,string>} attrs 键名即属性名（data-nts-mt / -quality / -preset）
   * @param {boolean} hasVars
   */
  function syncMaterialAttrs(attrs, hasVars) {
    var de = document.documentElement;
    if (!de) return;
    if (!hasVars || !attrs || typeof attrs !== 'object') { clearMaterialAttrs(); return; }
    var keys = [MT_ATTR, MT_QUALITY_ATTR, MT_PRESET_ATTR];
    for (var i = 0; i < keys.length; i += 1) {
      var v = attrs[keys[i]];
      if (typeof v === 'string' && v) de.setAttribute(keys[i], v);
      else de.removeAttribute(keys[i]);
    }
    // 指针追光的两个参数在变量里，读一次缓存，避免每次 pointermove 触发重算。
    if (window.getComputedStyle) {
      var cs = window.getComputedStyle(de);
      var follow = parseFloat(cs.getPropertyValue('--nts-mt-mouse-follow'));
      var delay = parseFloat(cs.getPropertyValue('--nts-mt-mouse-delay'));
      mtFollow = isNaN(follow) ? 0 : Math.max(0, Math.min(1, follow));
      mtDelay = (isNaN(delay) || delay < 0) ? 60 : Math.min(300, delay);
    }
    applyRootBypass(true);
  }

  /**
   * 指针追光：把鼠标位置写进 --nts-mt-mx / -my（百分比）。
   * 只在鼠标跟随强度 > 0 时写，且按 --nts-mt-mouse-delay 节流，
   * 大量 pointermove 不会拖动主线程（rAF 合并最后一帧）。
   */
  function initMaterialPointer() {
    if (mtPointerBound) return;
    mtPointerBound = true;
    var timer = 0;
    function onMove(ev) {
      if (!document.documentElement.hasAttribute(MT_ATTR)) return;
      if (mtFollow <= 0) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        var de = document.documentElement;
        if (!de.hasAttribute(MT_ATTR)) return;
        var w = window.innerWidth || 1;
        var h = window.innerHeight || 1;
        var x = (ev.clientX / w) * 100;
        var y = (ev.clientY / h) * 100;
        var set = function (name, val) {
          if (de.style && de.style.setProperty) de.style.setProperty(name, val);
        };
        var cx = Math.max(0, Math.min(100, x));
        var cy = Math.max(0, Math.min(100, y));
        set('--nts-mt-mx', cx.toFixed(2) + '%');
        set('--nts-mt-my', cy.toFixed(2) + '%');
        // 光照方向：从面板中心指向指针，sheen 与边缘环带的角度跟着转，
        // 玻璃才有被光「照到」的方向感（不是固定 162deg 的死高光）。
        var ang = Math.atan2(cy - 50, cx - 50) * 180 / Math.PI;
        if (ang < 0) ang += 360;
        set('--nts-mt-light-angle', ang.toFixed(1) + 'deg');
      }, mtDelay);
    }
    window.addEventListener('pointermove', onMove, { passive: true });
  }

  /**
   * 应用一个调色板。幂等：内容不变则不写 DOM。
   * @param {{vars?:Object,dark_vars?:Object,enabled?:boolean,name?:string,updated_at?:string}} p
   */
  /* ---------------------------------------------------------------- 诊断上报
   * 「材质真的注入页面了吗」从宿主外部只能靠推断 —— 而推断不算证据。
   * 这里把加载器实际观察到的状态回传给插件 entry loader_report，落盘成
   * static/diagnostics.json：规则数 / 门控属性 / 变量数 / 背板能力 / 指针绑定。
   * 一分钟最多一次、失败完全静默：诊断永远不能影响主题本身。 */
  var reportAt = 0;
  // 写入口只对本机开放：远程 / 反代窗口拿到 401/403 后本会话不再上报，
  // 否则每次重试都会在服务端刷一条越权告警。
  var reportBlocked = false;
  function reportRuntime(force) {
    try {
      if (reportBlocked) return;
      var now = Date.now();
      if (!force && reportAt && now - reportAt < 60000) return;
      // 节流窗口只在**成功写进磁盘后**才占用（见下方 then/catch）：初始化阶段
      // 插件服务常常比页面晚就绪，第一次必然失败，旧写法把窗口消耗在失败上，
      // 整个会话就再也等不到第二次上报。

      // v1.6.23：还没真正应用过任何主题就上报，只会写一份「css_len=0 / focus_rules=0」
      // 的快照，自检读到它会假红（install 之后插件服务重启的那几秒最容易撞上）。
      // 判据是 lastApply.at —— 正常应用、空调色板、显式禁用三条路径都会写它，
      // 所以「什么都没应用」不会上报，「已应用」照样能被证明。
      if (!lastApply || !lastApply.at) {
        reportAt = 0;
        return;
      }

      var attrs = {};
      var de = document.documentElement;
      if (de && de.getAttribute) {
        var attrNames = ['data-nts-mt', 'data-nts-mt-quality', 'data-nts-mt-preset'];
        for (var i = 0; i < attrNames.length; i++) {
          var attrValue = de.getAttribute(attrNames[i]);
          if (attrValue !== null && attrValue !== '') attrs[attrNames[i]] = String(attrValue);
        }
      }
      var rules = 0;
      try {
        var styleNode = document.getElementById(STYLE_ID);
        var sheet = styleNode && styleNode.sheet;
        if (sheet && sheet.cssRules) rules = sheet.cssRules.length;
      } catch (e1) { rules = -1; }
      var cssLen = 0;
      var focusRules = 0;
      try {
        var styleNode2 = document.getElementById(STYLE_ID);
        if (styleNode2) {
          cssLen = String(styleNode2.textContent || '').length;
          var sheet2 = styleNode2.sheet;
          var list2 = sheet2 && sheet2.cssRules;
          if (list2) {
            for (var ri = 0; ri < list2.length; ri += 1) {
              var sel2 = list2[ri] && list2[ri].selectorText;
              if (sel2 && String(sel2).indexOf('input:focus') >= 0) focusRules += 1;
            }
          }
        }
      } catch (e5) { cssLen = -1; }
      var backdrop = false;
      try {
        var cssApi = window.CSS;
        backdrop = !!(cssApi && cssApi.supports && (
          cssApi.supports('backdrop-filter', 'blur(2px)') ||
          cssApi.supports('-webkit-backdrop-filter', 'blur(2px)')
        ));
      } catch (e2) { backdrop = false; }
      var apply = lastApply || {};
      var issues = [];
      if (lastError) issues.push(String(lastError).slice(0, 300));
      if (apply.mt > 0 && !attrs['data-nts-mt']) issues.push('材质变量已注入但门控属性缺失');
      if (attrs['data-nts-mt'] && rules <= 0) issues.push('门控属性在但 style 规则数为 0');
      if (!backdrop) issues.push('运行环境不支持 backdrop-filter，材质会走降级背景');
      // 值级自证：只数「注入几个变量」无法证明窗口里的值是对的（镜像过期、
      // 门控没打上、变量被覆盖都会让计数照常为正）。读 computed style 采样，
      // 一眼就能看出环带 / 折光线 / 色散 / 漂移到底有没有落地。
      var samples = {};
      try {
        var cs = window.getComputedStyle ? window.getComputedStyle(de) : null;
        if (cs && cs.getPropertyValue) {
          var sampleNames = ['--nts-mt-under-glow', '--nts-mt-rim-img', '--nts-mt-rim-shade',
            '--nts-mt-disp-warm', '--nts-mt-lens-glow', '--nts-mt-sheen-img',
            '--nts-mt-bg', '--nts-mt-anim'];
          for (var si = 0; si < sampleNames.length; si++) {
            var sv = cs.getPropertyValue(sampleNames[si]);
            if (sv) samples[sampleNames[si]] = String(sv).slice(0, 160);
          }
        }
      } catch (e3) { samples = {}; }
      // 组件级材质是否真的落到元素上：组件别名只在组件选择器命中时才会继承下来，
      // 全局变量有值不代表组件覆盖在生效。
      try {
        var compEl = null;
        var compHit = '';
        var compProbe = ['#react-chat-window-shell', '.chat-window', '.compact-chat-surface-frame', '.composer-input', '.modal-dialog'];
        var compRects = {};
        for (var ci = 0; ci < compProbe.length; ci++) {
          var cprobe = document.querySelector ? document.querySelector(compProbe[ci]) : null;
          if (!cprobe) continue;
          if (!compEl) { compEl = cprobe; compHit = compProbe[ci]; }
          try {
            var crect = cprobe.getBoundingClientRect ? cprobe.getBoundingClientRect() : null;
            if (crect) compRects[compProbe[ci]] = Math.round(crect.width) + 'x' + Math.round(crect.height) + '@' + Math.round(crect.left) + ',' + Math.round(crect.top);
          } catch (e6) {}
        }
        if (compEl && window.getComputedStyle) {
          var ccs = window.getComputedStyle(compEl);
          if (ccs && ccs.getPropertyValue) {
            var compNames = ['--nts-mt-bg-c', '--nts-mt-radius-c', '--nts-mt-blur-c'];
            for (var cj = 0; cj < compNames.length; cj++) {
              var cv = ccs.getPropertyValue(compNames[cj]);
              if (cv) samples[compNames[cj]] = String(cv).slice(0, 160);
            }
          }
        }
      } catch (e4) { /* 采样失败不影响上报 */ }
      // v1.6.24：元素级自证 —— 只证明「root 上有变量」不够。用户说「没变化」时，
      // 必须能回答「这个元素最终算出来的 background-color / backdrop-filter 是什么」。
      var pageTheme = '';
      var elemSamples = {};
      try {
        if (de && de.getAttribute) pageTheme = String(de.getAttribute('data-theme') || '');
        if (compEl && window.getComputedStyle) {
          var ecs = window.getComputedStyle(compEl);
          var elemProps = ['background-color', 'background-image', 'backdrop-filter', 'border-radius', 'box-shadow', 'opacity',
            '--nts-mt-bg-c', '--nts-mt-blur-c', '--nts-mt-shadow-c', '--nts-mt-radius-c', '--nts-mt-bg'];
          for (var pi = 0; pi < elemProps.length; pi++) {
            var pv = ecs.getPropertyValue(elemProps[pi]);
            if (pv) elemSamples[elemProps[pi]] = String(pv).slice(0, 140);
          }
          var inlineRaw = compEl.getAttribute ? String(compEl.getAttribute('style') || '') : '';
          if (inlineRaw) elemSamples['__inline'] = inlineRaw.slice(0, 300);
          var inlineImp = [];
          var st = compEl.style;
          if (st && st.length) {
            for (var ii = 0; ii < st.length && ii < 80; ii++) {
              var nm = st[ii];
              if (st.getPropertyPriority && st.getPropertyPriority(nm) === 'important') inlineImp.push(nm);
            }
          }
          elemSamples['__inline_important'] = inlineImp.join(',') || '(none)';
        }
      } catch (e6) { elemSamples = {}; }
      // v1.6.28：谁在遮住玻璃 —— 把聊天窗内层元素的匹配规则与自身样式采出来。
      var rootInfo = {};
      try {
        var rootEl = document.querySelector ? document.querySelector('#react-chat-window-root') : null;
        if (rootEl) {
          rootInfo.className = String(rootEl.className || '').slice(0, 160);
          rootInfo.inline = String(rootEl.getAttribute ? (rootEl.getAttribute('style') || '') : '').slice(0, 240);
          var rcs = window.getComputedStyle ? window.getComputedStyle(rootEl) : null;
          if (rcs && rcs.getPropertyValue) {
            rootInfo.bg = String(rcs.getPropertyValue('background-color') || '').slice(0, 60);
            rootInfo.bgi = String(rcs.getPropertyValue('background-image') || '').slice(0, 120);
            rootInfo.bf = String(rcs.getPropertyValue('backdrop-filter') || '').slice(0, 80);
            rootInfo.opacity = String(rcs.getPropertyValue('opacity') || '').slice(0, 20);
            rootInfo.visibility = String(rcs.getPropertyValue('visibility') || '').slice(0, 20);
            rootInfo.display = String(rcs.getPropertyValue('display') || '').slice(0, 20);
            rootInfo.zIndex = String(rcs.getPropertyValue('z-index') || '').slice(0, 20);
          }
          try {
            var rr = rootEl.getBoundingClientRect();
            rootInfo.rect = Math.round(rr.width) + 'x' + Math.round(rr.height) + '@' + Math.round(rr.left) + ',' + Math.round(rr.top);
          } catch (eRect) { rootInfo.rect = ''; }
          var hits = [];
          var sheets = document.styleSheets || [];
          for (var si2 = 0; si2 < sheets.length && hits.length < 8; si2++) {
            var rs = null;
            try { rs = sheets[si2].cssRules; } catch (eS) { rs = null; }
            if (!rs) continue;
            for (var ri = 0; ri < rs.length && hits.length < 8; ri++) {
              var rl = rs[ri];
              var sel2 = rl && rl.selectorText;
              if (!sel2 || sel2.length > 200) continue;
              if (sel2.indexOf('chat-window-root') < 0 && sel2.indexOf('chat-body') < 0) continue;
              var txt2 = String((rl.style && rl.style.cssText) || '');
              if (txt2.indexOf('background') < 0 && txt2.indexOf('backdrop-filter') < 0) continue;
              var m2 = false;
              try { m2 = rootEl.matches ? rootEl.matches(sel2) : false; } catch (eM) { m2 = false; }
              if (!m2) continue;
              hits.push(sel2.slice(0, 100) + ' => ' + txt2.slice(0, 160));
            }
          }
          rootInfo.hits = hits.join(' || ');
        }
      } catch (e7) { rootInfo = {}; }
      // v1.6.25：分层采样。聊天窗的可见玻璃可能在 .chat-window 或
      // .compact-chat-surface-frame 上，而不是 #react-chat-window-shell；
      // 同时数 CSSOM 里命中该元素的规则条数，区分「规则没进样式表」和「进了但没赢」。
      var elem2 = {};
      try {
        var probes2 = ['#react-chat-window-root', '.chat-body', '.chat-surface', '.chat-window',
          '.compact-chat-surface-frame', '.message-list', '.composer-input-shell', '.window-topbar'];
        for (var qi = 0; qi < probes2.length; qi++) {
          var qel = document.querySelector ? document.querySelector(probes2[qi]) : null;
          if (!qel || !window.getComputedStyle) continue;
          var qcs = window.getComputedStyle(qel);
          elem2[probes2[qi]] = {
            bg: String(qcs.getPropertyValue('background-color') || '').slice(0, 80),
            bf: String(qcs.getPropertyValue('backdrop-filter') || '').slice(0, 60)
          };
        }
      } catch (e7) { elem2 = {}; }
      var shellInfo = {};
      try {
        var shellEl = document.querySelector ? document.querySelector('#react-chat-window-shell') : null;
        if (shellEl && window.getComputedStyle) {
          var scs = window.getComputedStyle(shellEl);
          var sb = shellEl.getBoundingClientRect();
          shellInfo.rect = Math.round(sb.width) + 'x' + Math.round(sb.height) + '@' + Math.round(sb.left) + ',' + Math.round(sb.top);
          shellInfo.className = String(shellEl.className || '').slice(0, 120);
          shellInfo.bg = String(scs.getPropertyValue('background-color') || '').slice(0, 60);
          shellInfo.bgi = String(scs.getPropertyValue('background-image') || '').slice(0, 100);
          shellInfo.bf = String(scs.getPropertyValue('backdrop-filter') || '').slice(0, 80);
          shellInfo.opacity = String(scs.getPropertyValue('opacity') || '').slice(0, 20);
          shellInfo.visibility = String(scs.getPropertyValue('visibility') || '').slice(0, 20);
          shellInfo.display = String(scs.getPropertyValue('display') || '').slice(0, 20);
          shellInfo.zIndex = String(scs.getPropertyValue('z-index') || '').slice(0, 20);
        }
      } catch (eShell) { shellInfo = {}; }
      var shellChain = [];
      try {
        var shel = document.querySelector ? document.querySelector('#react-chat-window-shell') : null;
        for (var si2 = 0; si2 < 8 && shel; si2++) {
          var ssc = window.getComputedStyle ? window.getComputedStyle(shel) : null;
          var srt = shel.getBoundingClientRect ? shel.getBoundingClientRect() : null;
          var sdesc = (shel.tagName || '?').toLowerCase();
          if (shel.id) sdesc += '#' + shel.id;
          if (shel.className && typeof shel.className === 'string') {
            var sp = String(shel.className).replace(/^ +| +$/g, '').split(' ');
            for (var sz = 0; sz < sp.length && sz < 4; sz++) { if (sp[sz]) sdesc += '.' + sp[sz]; }
          }
          sdesc += ' ' + (srt ? Math.round(srt.width) + 'x' + Math.round(srt.height) : '?');
          if (ssc) sdesc += ' disp=' + String(ssc.getPropertyValue('display')) + ' vis=' + String(ssc.getPropertyValue('visibility')) + ' pos=' + String(ssc.getPropertyValue('position')) + ' op=' + String(ssc.getPropertyValue('opacity')) + ' hid=' + String(!!shel.hidden);
          shellChain.push(sdesc.slice(0, 240));
          shel = shel.parentElement;
        }
      } catch (eShellChain) { shellChain = []; }
      var centerChain = [];
      var viewportStr = '';
      try {
        viewportStr = String(window.innerWidth || 0) + 'x' + String(window.innerHeight || 0);
        var cxp = Math.round((window.innerWidth || 0) / 2);
        var cyp = Math.round((window.innerHeight || 0) / 2);
        var cnode = document.elementFromPoint ? document.elementFromPoint(cxp, cyp) : null;
        for (var ci2 = 0; ci2 < 10 && cnode; ci2++) {
          var csc2 = window.getComputedStyle ? window.getComputedStyle(cnode) : null;
          var crect2 = cnode.getBoundingClientRect ? cnode.getBoundingClientRect() : null;
          var cdesc = (cnode.tagName || '?').toLowerCase();
          if (cnode.id) cdesc += '#' + cnode.id;
          if (cnode.className && typeof cnode.className === 'string') {
            var cparts = String(cnode.className).replace(/^ +| +$/g, '').split(' ');
            for (var cz = 0; cz < cparts.length && cz < 4; cz++) { if (cparts[cz]) cdesc += '.' + cparts[cz]; }
          }
          cdesc += ' ' + (crect2 ? Math.round(crect2.width) + 'x' + Math.round(crect2.height) : '?');
          if (csc2) cdesc += ' bg=' + String(csc2.getPropertyValue('background-color')) + ' bf=' + String(csc2.getPropertyValue('backdrop-filter'));
          centerChain.push(cdesc.slice(0, 260));
          cnode = cnode.parentElement;
        }
      } catch (eCenter) { centerChain = []; }
      var mtRuleCount = 0;
      var mtDeclCount = 0;
      try {
        var sn = document.getElementById(STYLE_ID);
        var sh = sn && sn.sheet;
        if (sh && sh.cssRules) {
          for (var ri2 = 0; ri2 < sh.cssRules.length; ri2 += 1) {
            var st2 = sh.cssRules[ri2];
            var sel3 = st2 && st2.selectorText;
            if (sel3 && String(sel3).indexOf('react-chat-window-shell') >= 0) {
              mtRuleCount += 1;
              mtDeclCount += (st2.style && st2.style.length) || 0;
            }
          }
        }
      } catch (e8) { mtRuleCount = -1; }
      var report = {
        page: (function () {
          try {
            return String((window.location && window.location.pathname) || '').slice(0, 120);
          } catch (err) { return ''; }
        })(),
        version: SCRIPT_VERSION,
        ntsVars: (apply.light || 0) + (apply.dark || 0),
        mtVars: apply.mt || 0,
        attrs: attrs,
        pageTheme: pageTheme,
        elemSamples: elemSamples,
        elem2: elem2,
        rootInfo: rootInfo,
        shellInfo: shellInfo,
        shellChain: shellChain,
        centerChain: centerChain,
        viewport: viewportStr,
        mtRuleCount: mtRuleCount,
        mtDeclCount: mtDeclCount,
        compMatched: compHit,
        compRects: compRects,
        samples: samples,
        rules: rules,
        cssLen: cssLen,
        focusRules: focusRules,
        backdrop: backdrop,
        pointer: !!mtPointerBound,
        enabled: !!apply.enabled,
        appliedAt: apply.at ? new Date(apply.at).toISOString() : '',
        errors: issues
      };
      // 本体内置：报告直接 POST 给本体自己的入口（同源，不需要 CSRF 令牌，
      // 也不再走 /runs 的 run_id 轮询）。
      fetchWithTimeout(API_BASE + '/run/loader_report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ report: report }),
        cache: 'no-store', credentials: 'same-origin'
      }, FETCH_TIMEOUT_MS).then(function (res) {
        if (res && (res.status === 401 || res.status === 403)) {
          reportBlocked = true;
          return;
        }
        if (!res || !res.ok) throw new Error('report HTTP ' + (res ? res.status : '?'));
        reportAt = now;
      }).catch(function () {
        // 失败后 15s 再试（不是下一拍 1.5s）：服务没就绪时不要每拍打一次。
        reportAt = now - 45000;
      });
    } catch (e) { /* ignore */ }
  }

  function applyPalette(p) {
    if (!p || typeof p !== 'object') return false;

    // 背景图预热闸门（v1.5.21）：主题真的换了 / 首次应用，而新图还没就绪时，
    // 先把图预热再改 CSS，避免「旧图撤掉、新图未到」的空白闪烁。
    // 预热完成后会带着同一份调色板再进本函数一次（那时 warmedImages 已就位）。
    if (p.enabled !== false) {
      var warmKey = warmKeyOf(p);
      if (warmKey !== lastWarmKey) {
        var warmUrls = imageUrlsOf(p);
        var needWarm = false;
        for (var wi = 0; wi < warmUrls.length; wi += 1) {
          if (!warmedImages[warmUrls[wi]]) { needWarm = true; break; }
        }
        if (needWarm) {
          if (warmingKey !== warmKey) {
            warmingKey = warmKey;
            warmPending = p;
            // v1.5.23：每一次预热都有自己的票号。旧实现里 p1 的预热还没结束就来了
            // p2，p1 的回调会把 warmPending（此时是 p2）直接 apply —— 而 p2 的图
            // 一张都没预热，闪白照旧，只是换了张图闪。票号对不上就整包丢弃。
            var warmToken = (warmSeq += 1);
            log('warming ' + warmUrls.length + ' background image(s) before applying');
            warmImages(warmUrls, function () {
              if (warmToken !== warmSeq) {
                log('drop stale warm callback (' + warmToken + ' != ' + warmSeq + ')');
                return;
              }
              var queued = warmPending;
              warmPending = null;
              warmingKey = null;
              if (queued) applyPalette(queued);
            });
          }
          return true;
        }
        lastWarmKey = warmKey;
      }
    }

    // 走到这里就是要直接应用 p。若还有一份更早的调色板在等预热，它已经被 p 取代：
    // 作废它的票号，否则预热完成时会把旧调色板再应用一遍（甚至把刚关掉的主题重新打开）。
    if (warmPending && warmPending !== p) {
      warmSeq += 1;
      warmPending = null;
      warmingKey = null;
      log('superseded a pending warm-up');
    }

    if (p.enabled === false) {
      removeStyle();
      if (fadeTimer) { clearTimeout(fadeTimer); fadeTimer = 0; }
      if (document.documentElement && document.documentElement.removeAttribute) {
        document.documentElement.removeAttribute(FADE_ATTR);
      }
      currentVersion = stampOf(p);
      lastApply = { light: 0, dark: 0, mt: 0, enabled: false, at: Date.now() };
      reportRuntime();
      return true;
    }

    var light = sanitizeVars(p.vars);
    var dark = sanitizeVars(p.dark_vars);
    var nLight = Object.keys(light).length;
    var nDark = Object.keys(dark).length;
    // 高级材质（v1.6.0）：材质是独立子树，与调色板变量并存。
    var mtLight = materialVarsOf(p, 'light');
    var mtDark = materialVarsOf(p, 'dark');
    var nMt = countVars(mtLight) + countVars(mtDark);

    if (!nLight && !nDark && !nMt) {
      // 空调色板 + 无材质 = 恢复原样
      removeStyle();
      currentVersion = stampOf(p);
      lastApply = { light: 0, dark: 0, mt: 0, enabled: true, at: Date.now() };
      reportRuntime();
      return true;
    }

    var previousStamp = currentVersion;
    var css = '';
    var l = declBlock(light);
    // 亮色值必须限定在「非暗色」，否则只配了亮色时会渗进暗色模式
    // （独立验证者反例 CE-B：:root{} 里的亮色值在暗色下依然生效）。
    if (l) css += LIGHT_SCOPE + '{' + l + '}';
    var d = declBlock(dark);
    if (d) css += ':root[data-theme="dark"]{' + d + '}';
    // 材质变量同样按亮/暗分块，与调色板共用一份 <style>。暗色表是全量的
    // （暗色缺失时后端已用亮色铺满），所以不会出现"材质半边空"。
    var ml = declBlock(mtLight);
    if (ml) css += LIGHT_SCOPE + '{' + ml + '}';
    var md = declBlock(mtDark);
    if (md) css += ':root[data-theme="dark"]{' + md + '}';

    var el = styleEl();
    // 探针始终放在最前面：它只用于 CSP 自检，不参与任何门控，也不影响原有变量。
    // 液态玻璃静态层拼在最后：它是常量字符串、每条规则自带门控，和调色板变量
    // 一起只写一次 textContent（lastAppliedCss / styleIntact / 去重逻辑都不变）。
    // v1.6.0：材质层（MATERIAL_CSS）也是常量字符串，与玻璃层一起只写一次 textContent。
    css = PROBE_CSS + css + '\n' + GLASS_CSS + '\n' + MATERIAL_CSS;
    if (el.textContent !== css) el.textContent = css;
    lastAppliedCss = css;
    // 门控属性必须在样式生效前同步（同一帧内 setAttribute 与 <style> 内容变化
    // 都会被合并进同一次样式重算，不会闪一下未门控的状态）。
    setGates(light, dark);
    // 材质门控属性：nMt 为 0 时撤掉 → MATERIAL_CSS 零匹配 → 应用保持原始外观。
    syncMaterialAttrs(p.material_attrs, nMt > 0);
    // 主题真的换了（版本戳不同）才做切换过渡；首次应用/内容未变时不闪。
    if (previousStamp && previousStamp !== stampOf(p)) flashThemeSwap();
    // 玻璃材质层：绑定指针镜面高光 / 滚动流动（幂等；纯 CSS 变量写入，不改声明）
    initGlassPointer();
    initMaterialPointer();
    probeStyleApplied();
    warnIfCssMissing();
    warnIfModeUncovered(nLight, nDark);
    currentVersion = stampOf(p);
    lastApply = { light: nLight, dark: nDark, mt: nMt, enabled: true, at: Date.now() };
    // 应用成功后强制刷新一次自检快照：排查时最需要的正是「应用之后」的门控与
    // 注入内容状态，若只在启动时抓一次，快照会是空的、看不出问题。
    lastSnapAt = 0;
    persistSnapshot();
    // 主题真的换了就立刻上报（绕过 60s 节流）：面板「运行状态」靠这份上报判断
    // 「窗口到底应用了没有」，晚一分钟上报会让面板短时间说出与实际相反的结论
    // —— 用户看到的现象就是「界面是默认的，面板写着上次那套主题」。
    reportRuntime(!previousStamp || previousStamp !== stampOf(p));
    return true;
  }

  // ---------------------------------------------------------------- 状态

  var currentVersion = null;
  var lastFetchAt = 0;
  var timer = null;
  var inflight = false;
  /** 最近一次成功应用的摘要（诊断用）。 */
  var lastApply = null;
  /** 最近一次拉取失败的原文 + 时间（诊断用）。 */
  var lastError = null;
  var lastErrorAt = 0;
  /** 已经向控制台大声报过的错误，避免 1.5s 一次的轮询刷屏。 */
  var lastWarnedError = null;
  /** 上次应用成功的调色板原文，用来识别「版本号没变但内容变了」。 */
  var lastRaw = null;

  function restoreFromCache() {
    var raw = safeGet(CACHE_KEY);
    if (!raw) return false;
    try {
      var p = JSON.parse(raw);
      if (!p || p.schema !== 1) return false;
      return applyPalette(p);
    } catch (e) {
      log('cache parse failed', e);
      return false;
    }
  }

  function saveToCache(p) {
    try { safeSet(CACHE_KEY, JSON.stringify(p)); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------- 失败可见性（原「服务自愈」段）
  //
  // 插件时代这里是一整条唤醒插件服务的链路（csrf-token → /plugins/refresh →
  // /plugin/<id>/start），因为加载器跑在本体窗口里、调色板却在另一个端口的插件
  // 服务上，插件没启动时只能靠外部把服务拉活。本体内置后调色板就是本体自己的
  // 路由：后端在、接口就在；后端不在，页面本身也打不开。因此这里只保留失败计数
  // 与诊断字段，供面板「自检」卡片展示。
  // 插件时代的「用户是否停用本插件」两条探测（/ui/disabled.json 与 /plugins 注册表）
  // 在本体内置后失去意义：主题工坊不是一个可停用的插件，本体也没有插件注册表。
  var reviveAt = 0;
  var reviveCount = 0;
  var reviving = false;
  var lastReviveError = null;
  // 自愈退避的当前间隔（v0.1.11）：1.2s 起，每次失败 ×2，上限 30s；成功后复位。
  var reviveDelay = REVIVE_BASE_DELAY_MS;

  /**
   * 失败分类（保留给诊断与日志）：网络类失败说明后端没响应，非网络类失败说明
   * 数据到了但不合法（JSON 坏了、契约不符）。本体内置后两者都只影响日志措辞。
   */
  function isNetworkFailure(msg) {
    if (!msg) return false;
    var m = String(msg);
    if (m.indexOf('timeout ') === 0 || m.indexOf('TimeoutError') === 0) return true;
    if (m.indexOf('HTTP 404') === 0 || m.indexOf('HTTP 503') === 0) return true;
    if (m.indexOf('Failed to fetch') !== -1 || m.indexOf('NetworkError') !== -1) return true;
    if (m.indexOf('Load failed') !== -1 || m.indexOf('AbortError') !== -1) return true;
    return false;
  }

  /** 自愈前置判断（保留旧名字）：只认网络类失败。 */
  function shouldRevive(msg) {
    return isNetworkFailure(msg);
  }

  /**
   * 保留这个名字给所有旧调用点（fetchPalette 的失败分支、API.revive）。
   * 本体内置后没有插件服务可唤醒，轮询本身就是重试机制。
   * @param {string} msg
   */
  function reviveService(msg) { /* no-op: 本体内置，无插件宿主可唤醒 */ }

  /**
   * 带超时的 fetch（v0.1.11 P0）。
   * 原生 fetch 没有超时：插件端点假死（连接建立了但永不回包）时 Promise 永远
   * pending，调用方的 inflight 锁就永远不释放 —— 1.5s 轮询被永久卡死，症状是
   * 「面板改了色，窗口再也不更新，控制台全静默」。用 AbortController 在 8s 后中断，
   * 并把中断统一成 'timeout <ms>' 错误，便于按「网络类失败」走自愈退避
   * （与 JSON 解析 / 契约错误区分开，后者不该重启插件服务）。
   * @param {string} url
   * @param {Object} opts fetch 选项
   * @param {number} timeoutMs
   * @returns {Promise<Response>}
   */
  function fetchWithTimeout(url, opts, timeoutMs) {
    var ms = timeoutMs || FETCH_TIMEOUT_MS;
    // 极老的宿主没有 AbortController：退回普通 fetch（总比完全不发请求好）。
    if (typeof window.AbortController !== 'function') return window.fetch(url, opts);
    var ctrl;
    try { ctrl = new window.AbortController(); } catch (e0) { return window.fetch(url, opts); }
    var o = {};
    for (var k in opts) {
      if (Object.prototype.hasOwnProperty.call(opts, k)) o[k] = opts[k];
    }
    o.signal = ctrl.signal;
    var timedOut = false;
    var timer = window.setTimeout(function () {
      timedOut = true;
      try { ctrl.abort(); } catch (e1) { /* ignore */ }
    }, ms);
    var clearTimer = function () {
      if (timer) { window.clearTimeout(timer); timer = null; }
    };
    var p;
    try {
      p = window.fetch(url, o);
    } catch (e2) {
      clearTimer();
      throw e2;
    }
    return p.then(function (res) { clearTimer(); return res; }, function (err) {
      clearTimer();
      if (timedOut) {
        var te = new Error('timeout ' + ms + 'ms');
        te.name = 'TimeoutError';
        throw te;
      }
      throw err;
    });
  }

  /** 契约错误（数据到达但不合法）每个原因只大声报一次；绝不自愈重启。 */
  var warnedContract = Object.create(null);
  function warnContractOnce(reason) {
    lastError = 'contract: ' + reason;
    lastErrorAt = Date.now();
    if (warnedContract[reason]) return;
    warnedContract[reason] = 1;
    try {
      console.warn('[theme-studio] 插件返回的调色板不合法（' + reason + '），已忽略且不会重启服务。' +
        '\n  端点：' + themeUrl() +
        '\n  多半是 theme.json 被手工改坏，或插件/加载器版本不一致。');
    } catch (e) { /* ignore */ }
  }

  // 连续失败时的退避：主服务重启 / 启动受限模式 / 接口不可用时，不要每个窗口
  // 每 1.5s 打一次请求。前 3 次照常重试（覆盖短暂抖动），之后按 2 倍退避，
  // 上限 60s；任何一次成功立即恢复 1.5s 轮询。
  var POLL_BACKOFF_AFTER = 3;
  var POLL_BACKOFF_MAX_MS = 60000;
  var pollFailStreak = 0;
  var pollBackoffUntil = 0;

  function notePollFailure() {
    pollFailStreak += 1;
    if (pollFailStreak < POLL_BACKOFF_AFTER) return;
    var steps = Math.min(pollFailStreak - POLL_BACKOFF_AFTER + 1, 6);
    var delay = Math.min(POLL_BACKOFF_MAX_MS, POLL_INTERVAL_MS * Math.pow(2, steps));
    pollBackoffUntil = Date.now() + delay;
  }

  // 用户切回窗口时不要还等着最长 60s 的退避：服务多半已经重启好了。
  function clearPollBackoff() {
    if (document.visibilityState === 'hidden') return;
    pollBackoffUntil = 0;
  }
  try {
    window.addEventListener('focus', clearPollBackoff);
    document.addEventListener('visibilitychange', clearPollBackoff);
  } catch (eBackoff) { /* ignore */ }

  function fetchPalette(force) {
    if (inflight) return;
    if (!force && pollBackoffUntil && Date.now() < pollBackoffUntil) return;
    inflight = true;
    var url = themeUrl() + '?_=' + Date.now();
    // 内置后是同源请求：远程 / Docker / 反代窗口要带上实例访问的会话 cookie，
    // 否则 InstanceAccessMiddleware 在路由之前就返回 401。
    var opts = { cache: 'no-store', credentials: 'same-origin' };
    // v0.1.11 P0：无论成功、数据不合法、网络失败还是超时，都在链尾释放 inflight。
    // 旧实现只在 then/catch 里复位：端点假死（fetch 永远 pending）时轮询永久停摆。
    var release = function () { inflight = false; };
    var req;
    try {
      req = fetchWithTimeout(url, opts, FETCH_TIMEOUT_MS);
    } catch (e) {
      release();
      return;
    }
    req.then(function (res) {
      if (!res || !res.ok) throw new Error('HTTP ' + (res ? res.status : '?'));
      return res.json();
    }).then(function (p) {
      lastFetchAt = Date.now();
      pollFailStreak = 0;
      pollBackoffUntil = 0;
      // 数据到达 = 服务活着：先按成功重置自愈预算（下次真挂了还能再救）。
      lastError = null;
      lastWarnedError = null;
      reviveCount = 0;
      reviveAt = 0;
      reviveDelay = REVIVE_BASE_DELAY_MS;
      // ---- 契约校验（v0.1.11）：数据到达但不合法 → 不触发自愈 ----
      // 重启插件服务只能救「服务不在」，救不了「返回的 JSON 是坏的」。
      if (!p || typeof p !== 'object' ||
          Object.prototype.toString.call(p) !== '[object Object]') {
        warnContractOnce('theme.json 不是一个 JSON 对象');
        return;
      }
      if (p.schema !== undefined && p.schema !== 1) {
        warnContractOnce('theme.json 的 schema=' + String(p.schema) + '（本加载器只认 1）');
        return;
      }
      rememberAssetMeta(p);
      var raw = '';
      try { raw = JSON.stringify(p); } catch (e) { raw = ''; }
      // theme.json 不再有 version 字段（v0.1.5 起）：raw 是服务端返回 JSON 的精确
      // 文本，逐字相同就是「没变化」。这比版本号更严格 —— 手工改 theme.json、
      // 重置回同一份内容都能如实反映。
      if (!force && raw === lastRaw) {
        // v0.1.11 P1：raw 没变 ≠ 主题还在。宿主模板重渲染 / 清理 DOM / 重新挂 CSP
        // 都可能删掉注入的 <style>，旧实现只看 raw，于是节点没了也永远不重建
        // —— 表现为「主题在窗口里彻底消失，只有刷新页面才好」。
        if (!styleIntact()) {
          log('injected style node missing, rebuilding');
          applyPalette(p);
          lastRaw = raw;
        }
        return; // 无变化
      }
      applyPalette(p);
      lastRaw = raw;
      saveToCache(p);
      log('palette updated', stampOf(p));
      try {
        window.dispatchEvent(new CustomEvent('neko-theme-studio-changed', {
          detail: { name: p.name, updatedAt: p.updated_at || '' }
        }));
      } catch (e) { /* ignore */ }
    }).catch(function (err) {
      var msg = (err && err.message) || 'unknown';
      lastError = msg;
      lastErrorAt = Date.now();
      notePollFailure();
      // 轮询每 1.5s 一次，同一个错误只大声报一次，之后走 debug 日志。
      if (msg !== lastWarnedError) {
        lastWarnedError = msg;
        try {
          console.warn('[theme-studio] 拉取调色板失败（保留缓存，窗口内不会应用新主题）：' + msg +
            '\n  端点：' + url +
            '\n  接口由本体提供，连不上通常意味着主服务刚重启或页面已被关闭。');
        } catch (e) { /* ignore */ }
      }
      log('fetch failed (keeping cache)', msg);
      // 网络类失败 = 本体主服务没响应；非网络类 = 数据到了但不合法。
      // 两种都只是记一笔，下一轮 1.5s 轮询自然重试。
      if (isNetworkFailure(msg)) log('network failure, will retry on next poll:', msg);
      else log('non-network failure:', msg);
    }).then(release, release);
  }

  function startPolling() {
    stopPolling();
    timer = window.setInterval(function () {
      if (document.visibilityState === 'hidden') return;
      // 每次心跳都顺带复检一次样式表新鲜度（内部按 15s 节流）。这样用户在面板里
      // 改了色，即使窗口是在插件更新之前打开的，也能在十几秒内自己换到新规则表，
      // 不需要重启应用。
      refreshStaleStylesheet(false);
      // 顺带检查加载器本身是否已被插件更新（内部 60s 节流 + 会话内一次）。
      selfUpgradeIfNeeded();
      fetchPalette(false);
      // 把本窗口的自检状态定期落盘，便于外部排查（内部 60s 节流）。
      try { if (window.NekoThemeStudio && window.NekoThemeStudio.report) window.NekoThemeStudio.report(); } catch (e9) { /* ignore */ }
    }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (timer !== null) { window.clearInterval(timer); timer = null; }
  }

  // ---------------------------------------------------------------- 引导

  // 1) 先用手上的缓存立刻渲染，避免启动闪烁。
  restoreFromCache();

  // 2) 再向插件服务要最新版。
  function boot() {
    // 一行「我还活着」——排查「本体到底加载到脚本没有」时控制台一眼可见。
    try {
      console.info('[theme-studio] loader v' + SCRIPT_VERSION + ' 已加载，端点 ' + themeUrl());
    } catch (e) { /* ignore */ }

    // 先确认样式表是当前版本（否则规则是旧的，注入再正确也不生效）。
    // 启动时强制检查一次，不看节流。
    refreshStaleStylesheet(true);

    fetchPalette(true);
    startPolling();
    // 启动时也查一次加载器新旧：用户可能已经很多天没重启应用，窗口里跑的是
    // 好几版之前的脚本，新变量全都被静默丢弃。
    selfUpgradeIfNeeded();

    // 应用切换亮/暗（html[data-theme]）时重新检查「当前模式那一组是否为空」，
    // 否则用户先配亮色、再切暗色，就永远看不到那条提示。
    try {
      if (window.MutationObserver) {
        var mo = new window.MutationObserver(function () {
          if (lastApply) warnIfModeUncovered(lastApply.light, lastApply.dark);
        });
        mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
      }
    } catch (e) { /* ignore */ }

    // 窗口重新获得焦点 / 重新可见 → 立即同步一次（用户刚在面板里改完色）
    window.addEventListener('focus', function () { fetchPalette(false); }, false);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') fetchPalette(false);
    }, false);

    // 同一源的其它窗口改了缓存 → 跟进（应用内多窗口场景）
    window.addEventListener('storage', function (ev) {
      if (!ev || ev.key !== CACHE_KEY || !ev.newValue) return;
      try {
        var p = JSON.parse(ev.newValue);
        if (!p || p.schema !== 1) return;
        // 与 fetchPalette 的判等口径保持一致：只比原始 JSON 文本。
        var raw = '';
        try { raw = JSON.stringify(p); } catch (e0) { raw = ''; }
        if (raw === lastRaw) return;
        applyPalette(p);
        lastRaw = raw;
      } catch (e) { /* ignore */ }
    }, false);

    // 暗色切换后变量本身不用重算（CSS 选择器负责），但给外部一个钩子
    window.addEventListener('neko-theme-changed', function () { /* no-op, 保留给后续扩展 */ }, false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, false);
  } else {
    boot();
  }

  // ---------------------------------------------------------------- 对外 API

  window.NekoThemeStudio = {
    /** 立即重新拉取调色板 */
    refresh: function () { fetchPalette(true); },
    /** 直接应用一个调色板对象（调试/手工用） */
    apply: applyPalette,
    /** 清除本窗口注入的变量（回到应用原生外观；下次轮询会自动重新应用） */
    clear: function () {
      removeStyle();
      // 不清内容判等键的话，轮询会认为「没变化 = 不用重注入」，
      // 调用 clear() 之后调色板再也不会回来（只有调试 API 会踩到，但很隐蔽）。
      currentVersion = '';
      lastRaw = null;
      lastApply = null;
    },
    /** 当前调色板的 updated_at 标记（v0.1.5 起 theme.json 不再有 version 字段） */
    get updatedAt() { return currentVersion; },
    /** 加载器脚本自身版本（部署漂移自查用） */
    scriptVersion: SCRIPT_VERSION,
    /** 接口基址（本体内置后固定为当前源的 /api/theme_studio） */
    get baseUrl() { return API_BASE; },
    /** 保留接口：本体内置后接口基址固定，忽略外部覆盖。 */
    setBaseUrl: function (u) { /* no-op */ },
    /** 打开调试日志 */
    setDebug: function (on) { DEBUG = !!on; },
    /** 保留接口：插件时代用于叫醒插件服务，本体内置后无宿主可叫醒。 */
    revive: function () {
      reviveCount = 0;
      reviveAt = 0;
      reviveDelay = REVIVE_BASE_DELAY_MS;
    },
    /**
     * 自检：把「加载器 → 本体接口 → CSS」这条链路的运行时状态打成一个对象。
     * 在应用窗口的 DevTools 里执行 `NekoThemeStudio.diagnose()` 即可。
     * @returns {{version:*,mode:string,baseUrl:string,endpoint:string,gates:string[],
     *            styleLen:number,lastApply:*,lastFetchAgoMs:*,lastError:*,
     *            hasCache:boolean,bubbleBg:*,bubbleColor:*}}
     */
    diagnose: function () {
      var de = document.documentElement;
      var gates = [];
      if (de && de.attributes) {
        for (var i = 0; i < de.attributes.length; i++) {
          var n = de.attributes[i].name;
          if (n.indexOf(GATE_ATTR) === 0) gates.push(n);
        }
      }
      var el = document.getElementById(STYLE_ID);
      var bubble = null;
      try {
        bubble = document.querySelector('.message-bubble-user, .message.user');
      } catch (e) { /* ignore */ }
      var cs = bubble ? (window.getComputedStyle ? window.getComputedStyle(bubble) : null) : null;
      var hasCache = false;
      try { hasCache = !!safeGet(CACHE_KEY); } catch (e) { /* ignore */ }
      // 样式表是否真的加载了：CSS 没加载时门控属性照样打上，但没有任何规则
      // 匹配 → 变量生效、元素外观不变（最容易被误判成「插件没生效」）。
      var cssLoaded = false;
      var cssRules = 0;
      try {
        var sheets = document.styleSheets || [];
        for (var s = 0; s < sheets.length; s++) {
          var href = sheets[s].href || '';
          if (href.indexOf('neko-theme-studio.css') !== -1) {
            cssLoaded = true;
            cssRules = countStyleRules(sheets[s]);
            break;
          }
        }
      } catch (e3) { /* ignore */ }
      return {
        scriptVersion: SCRIPT_VERSION,
        cssLoaded: cssLoaded,
        cssRules: cssRules,
        updatedAt: currentVersion,
        mode: currentMode(),
        baseUrl: API_BASE,
        endpoint: themeUrl(),
        gates: gates,
        styleLen: el ? (el.textContent || '').length : 0,
        // v0.1.11 自检项：注入节点是否还活着（被宿主删掉时 styleAlive=false）、
        // 注入样式是否真的生效（CSP 拦截时 cspBlocked=true），以及被值校验拒掉的
        // 变量名 —— 「面板能存、加载器丢值」时先看 rejectedVars。
        styleAlive: !!(el && el.parentNode),
        styleApplied: lastAppliedCss ? styleIntact() : null,
        cspBlocked: cspWarned,
        rejectedVars: (function () {
          var out = [];
          for (var rk in warnedReject) {
            if (Object.prototype.hasOwnProperty.call(warnedReject, rk)) out.push(rk);
          }
          return out;
        })(),
        lastApply: lastApply,
        lastFetchAgoMs: lastFetchAt ? Date.now() - lastFetchAt : null,
        lastError: lastError,
        lastErrorAgoMs: lastErrorAt ? Date.now() - lastErrorAt : null,
        hasCache: hasCache,
        reviveCount: reviveCount,
        lastReviveError: lastReviveError,
        // 样式表自愈状态：sheetSwapCount > 0 说明这个窗口曾经换过表（典型场景是
        // 窗口开着时插件被更新）。排查「面板改了但窗口不动」时先看这两个数。
        sheetSwapCount: sheetSwapCount,
        lastSheetCheckAgoMs: lastSheetCheckAt ? Date.now() - lastSheetCheckAt : null,
        selfUpgradeAt: selfUpgradeAt || null,
        selfUpgradeGuard: (function () {
          try { return window.sessionStorage.getItem(SELF_UPGRADE_GUARD_KEY) || ''; }
          catch (e) { return ''; }
        })(),
        // v1.5.19 指针光效自检：panels 是当前挂了光效的表面数，seen=false 表示
        // 还没收到过 pointermove；sampleSpecX/Y 是第一个表面的镜面位置，
        // 鼠标移动时它应当跟着变（排查「鼠标动了但没反应」先看这三项）。
        glassPointer: {
          seen: glassPointer.seen,
          x: glassPointer.x,
          y: glassPointer.y,
          panels: glassPanels.length,
          sampleSpecX: glassPanels.length ? glassPanels[0].sx : null,
          sampleSpecY: glassPanels.length ? glassPanels[0].sy : null,
          follow: GLASS_FOLLOW
        },
        bubbleFound: !!bubble,
        bubbleBg: cs ? cs.backgroundColor : null,
        bubbleColor: cs ? cs.color : null
      };
    },
    _internals: {
      isSafeValue: isSafeValue,
      sanitizeVars: sanitizeVars,
      themeUrl: themeUrl,
      // v0.1.11 新值类型：暴露给自测/排查用（NekoThemeStudio._internals.isSafeFilterChain 等）。
      isSafeFilterChain: isSafeFilterChain,
      isSafeGradient: isSafeGradient,
      // v1.5.19 指针光效：暴露重建/重绘入口，供自测与真机回归脚本调用。
      refreshGlassPanels: refreshGlassPanels,
      paintGlassSpecular: paintGlassSpecular,
      glassSurfaces: function () { return glassPanels.length; }
    }
  };

  log('runtime ready; endpoint =', themeUrl());

  // ------------------------------------------------- 自检快照落盘（可远程读取）

  // 应用聊天窗是 Electron 窗口，没有开 CDP 端口，外部无法连进去看状态；用户也
  // 未必愿意/会开控制台。但同一个 localStorage 会被 Electron 落盘到
  // `Local Storage\leveldb`，所以把 diagnose() 快照定期写进去，排查
  // 「面板显示已应用、窗口毫无变化」时就能直接从磁盘读到那个窗口当时真实的
  // 门控属性、样式表 href 与注入内容长度，不用再猜。
  function rectOf(el) {
    if (!el) return null;
    var b = el.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)];
  }
  function composerSnapshot() {
    var out = {};
    var frame = document.querySelector('.compact-chat-surface-frame');
    var shell = document.querySelector('.compact-chat-surface-shell');
    var capsule = document.querySelector('.compact-chat-capsule-button');
    var toggle = document.querySelector('.send-button-circle');
    out.state = frame ? (frame.getAttribute('data-compact-chat-state') || '') : 'no-frame';
    out.dpr = window.devicePixelRatio || 1;
    out.viewport = [window.innerWidth, window.innerHeight];
    out.shell = rectOf(shell);
    if (frame) {
      var fs = window.getComputedStyle(frame);
      out.frame = {
        rect: rectOf(frame),
        padding: [fs.paddingTop, fs.paddingRight, fs.paddingBottom, fs.paddingLeft],
        overflow: fs.overflow,
        zIndex: fs.zIndex,
        position: fs.position
      };
    }
    out.capsule = rectOf(capsule);
    if (toggle) {
      var ts = window.getComputedStyle(toggle);
      var img = toggle.querySelector('img');
      out.toggle = {
        exists: true,
        rect: rectOf(toggle),
        opacity: ts.opacity,
        visibility: ts.visibility,
        display: ts.display,
        zIndex: ts.zIndex,
        position: ts.position,
        bg: ts.backgroundColor,
        transform: ts.transform,
        ariaLabel: toggle.getAttribute('aria-label') || '',
        imgSrc: img ? (img.getAttribute('src') || '') : '',
        // 该按钮中心点上有哪些元素（自上而下），能一眼看出是否被别的层盖住
        stack: (function () {
          var b = toggle.getBoundingClientRect();
          var cx = Math.round(b.left + b.width / 2);
          var cy = Math.round(b.top + b.height / 2);
          var list = [];
          try {
            var els = document.elementsFromPoint(cx, cy) || [];
            for (var i = 0; i < els.length && i < 6; i++) {
              var e = els[i];
              var es = window.getComputedStyle(e);
              list.push({
                tag: e.tagName,
                cls: String(e.className || '').slice(0, 48),
                z: es.zIndex,
                pe: es.pointerEvents,
                bg: es.backgroundColor
              });
            }
          } catch (e4) { /* ignore */ }
          return list;
        })()
      };
    } else {
      out.toggle = { exists: false };
    }
    var tools = document.querySelectorAll('.composer-tool-btn');
    out.toolBtnCount = tools.length;
    out.toolBtnFirst = tools.length ? rectOf(tools[0]) : null;
    var fan = document.querySelector('.compact-input-tool-fan');
    out.fanOpen = fan ? fan.getAttribute('data-compact-input-tool-fan-open') : null;
    out.scroll = [window.scrollX, window.scrollY];
    return out;
  }

  var lastSnapAt = 0;
  function persistSnapshot() {
    var now = Date.now();
    if (now - lastSnapAt < 60000) return;
    lastSnapAt = now;
    try {
      var d = window.NekoThemeStudio.diagnose();
      var link = null;
      try { link = document.querySelector('link[href*="neko-theme-studio.css"]'); } catch (e1) { link = null; }
      d.sheetHref = link ? link.getAttribute('href') : '';
      d.scriptSrc = (function () {
        try {
          var s = document.querySelector('script[src*="neko-theme-studio.js"]');
          return s ? s.getAttribute('src') : '';
        } catch (e2) { return ''; }
      })();
      d.styleText = (document.getElementById(STYLE_ID) || {}).textContent || '';
      // 「输入框右侧按钮看不到」这类问题，只有真实窗口能回答：把 composer 的
      // 几何、那个圆形按钮的存在性与覆盖栈一起写进快照，事后从 leveldb 读。
      try {
        d.composer = composerSnapshot();
      } catch (e3) { d.composer = null; }
      d.at = new Date().toISOString();
      window.localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(d));
    } catch (e) { /* ignore */ }
  }
  window.NekoThemeStudio.report = persistSnapshot;
  persistSnapshot();

  // 诊断上报的自主调度（v1.6.2）：不能只靠 applyPalette 的调用时机 ——
  // 页面可见性变化、主题未变时都不会再进 applyPalette，诊断就一直是空的。
  // 启动 5 秒后一次（兜住"插件服务比页面晚就绪"），之后可见时每分钟一次。
  (function () {
    function kickReport() {
      reportRuntime(true);
    }
    setTimeout(kickReport, 5000);
    setInterval(function () {
      try {
        if (document.visibilityState === 'visible') kickReport();
      } catch (e) { /* ignore */ }
    }, 60000);
    try {
      document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') kickReport();
      });
    } catch (e) { /* ignore */ }
  })();
})();
