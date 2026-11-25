/*!
 * neko-theme-studio.js — N.E.K.O Theme Studio · browser-side runtime
 * ---------------------------------------------------------------------------
 * Job: turn the palette JSON produced by Theme Studio into CSS variables in
 * every app window.
 *
 * Background (why polling):
 *   The Theme Studio panel and the chat window can be open at the same time, and
 *   the window must follow colour changes immediately. Polling is the simplest
 *   and most robust option: the panel POSTs to disk, and each window fetches the
 *   same-origin /api/theme_studio/theme.json every 1.5s. Since this is built into
 *   the app there is no cross-origin issue and no plugin port or host wake-up
 *   mechanism is needed.
 *
 * Design points:
 *   1. Variables are injected into a <style>, not a CSS file -> no HTTP caching issues.
 *   2. localStorage cache -> applies instantly at startup with no flicker; the
 *      theme survives even when the service is unavailable.
 *   3. fetch uses cache:'no-store' + a timestamp query string -> bypasses any
 *      heuristic caching.
 *   4. Only whitelisted value formats and --nts- prefixed variable names are
 *      accepted (defence in depth; the server validates too).
 *   5. No dependencies, no side effects: every failure degrades silently and
 *      never throws into the host app.
 */
(function () {
  'use strict';

  // Double-injection sentinel (v1.5.23): the template patch and the self-upgrade
  // stylesheet swap can both cause this script to run a second time on the same
  // page. Without this gate there would be two poll timers, two sets of
  // pointer/scroll listeners, variables rewritten into the same <style> (flicker +
  // wasted CPU), and neither copy would know about the other's state.
  if (window.__NTS_LOADER_LOADED__) return;
  window.__NTS_LOADER_LOADED__ = true;

  // ---------------------------------------------------------------- Config constants

  var PLUGIN_ID = 'neko_theme_studio';
  var STYLE_ID = 'nts-dynamic';
  var CACHE_KEY = 'neko.themeStudio.palette';
  // Self-check snapshot key: see persistSnapshot() at the end of the file.
  var SNAPSHOT_KEY = 'neko.themeStudio.lastDiagnose';
  // Built into the app: the palette API is same-origin with the window; no plugin
  // port and no base-URL override.
  var API_BASE = '/api/theme_studio';
  var POLL_INTERVAL_MS = 1500;
  // Palette request timeout (v0.1.11 P0). Native fetch has no timeout: when the
  // endpoint hangs (TCP connects but never responds) the Promise stays pending
  // forever, fetchPalette's inflight stays true, and the 1.5s poll is stuck for
  // good. Symptom: "changed a colour in the panel, the window never updates, the
  // console is silent". A timeout counts as one network failure and goes through
  // the self-healing backoff (see reviveService).
  var FETCH_TIMEOUT_MS = 8000;
  // In the plugin era these were backoff parameters for "waking the plugin
  // service" (csrf-token -> /plugins/refresh -> /plugin/<id>/start). Built into
  // the app there is no host to wake: the loader fetches the app's own routes,
  // and if the backend is down the page itself cannot load. The three constants
  // below are kept only for compatibility with leftover references.
  var REVIVE_BASE_DELAY_MS = 1200;
  var REVIVE_MAX_DELAY_MS = 30000;
  var REVIVE_MAX_ATTEMPTS = 40;
  // Self-upgrade: a script in an already-open window cannot be replaced (the
  // browser has executed it). When the loader on disk is newer than this
  // instance, the only way to take over is to reload the page. This is the
  // root fix for "the panel added a variable and the user never sees it without
  // a restart": the old script silently drops the value because the variable is
  // not in its whitelist, apply_theme still returns succeeded, the panel shows no
  // error, and it is very hard to diagnose.
  var SELF_UPGRADE_MIN_INTERVAL_MS = 60000;
  // The guard records "version@timestamp". The same version is not reloaded again
  // within the cooldown (prevents reload loops); after the cooldown it tries once
  // more, otherwise a single cache miss would kill upgrades for the whole session.
  var SELF_UPGRADE_COOLDOWN_MS = 600000;
  var SELF_UPGRADE_GUARD_KEY = 'neko.themeStudio.selfUpgrade';
  var selfUpgradeAt = 0;
  var DEBUG = false;

  var VAR_PREFIX = '--nts-';
  // The script's own version (not the palette version). Deployment drift is a
  // common cause of "changed a colour in the panel, the app did not react":
  // static\neko-theme-studio.js is old while the CSS is new (or vice versa), so
  // the gating attributes and stylesheet rules do not line up, and colours never
  // change with no error. Exposed in diagnose() so version drift is obvious.
  // 1.4.0: on page open, check whether the stylesheet is stale and force a refresh;
  //        bubble text colours are fully scoped per role (AI colour no longer bleeds
  //        into user/tool/system body text), fixing "changing AI text colour also
  //        changes user text".
  // 1.4.2: value validation no longer defaults to colour (kind='auto' fallback), and
  //        the page reloads itself when a newer loader is detected on disk, fixing
  //        "newly added variables are silently dropped in already-open windows".
  // 1.5.0: theme.json no longer has a version counter (uses an updated_at stamp plus
  //        verbatim raw-JSON comparison); new image kind (--nts-chat-bg-image, chat
  //        background image).
  // 1.5.13: liquid-glass static layer (contract _work/FEATURE-CONTRACT-0.2.0.md §2):
  //        rim edge reflection, sheen glint, noise frosted grain; gated by three
  //        variables, zero effect for non-glass themes; registered kinds for
  //        --nts-glass-rim / --nts-glass-sheen / --nts-glass-noise.
  // 1.5.14: Apple Liquid Glass material layer (contract _work/FEATURE-CONTRACT-0.3.0.md):
  //        lens bright ring / thickness / specular pointer highlight, scroll
  //        settling and press deformation.
  // 1.5.15: lighting engine (contract _work/FEATURE-CONTRACT-0.4.0.md): ambient
  //        pointer light source, glass-dispersion edge splitting, glass-caustic
  //        flowing caustics; fixed two 0.3.0 defects, "black mask covers specular /
  //        sheen" and "sheen animation spins unconditionally"; animations pause
  //        while the window is hidden; the pointer also writes --nts-light-x/y for
  //        ambient.
  // 1.5.16: fixed a 0.4.0 regression: the lighting layer used to hang on
  //        `.compact-export-history-scroll`, which is position:static (deliberately
  //        not relative, so the ::before frosted layer keeps its anchor), so the
  //        absolute + inset:0 ::after spread over an outer positioned ancestor,
  //        showing as a whole square-cornered bright screen above the
  //        history/export panel. The lighting layer now only goes on the 6 surfaces
  //        with a definite containing block.
  // 1.5.17: 0.4.2 fixes "every theme has a square-cornered light screen". Root cause:
  //        absolute pseudo-elements of the lighting / panel decoration layers hung
  //        on position:static hosts (.composer-input-shell, .message-bubble-*,
  //        .compact-export-history-scroll) and escaped all the way to the
  //        position:fixed .compact-export-history-anchor big box and filled it.
  //        Now ensureGlassContainment() only adds inline relative to hosts whose
  //        computed position is static; the panel decoration layer itself moved to
  //        scroll::after (z-index:-1, border-radius inherit, inner glow).
  // 1.5.18: 0.4.3 structural fix for "something extra below the panel again". Root
  //        cause: the history message overlay `.compact-export-history-scroll` is
  //        not a panel at all: no radius, no background, height fixed by the slot
  //        reserved via `--compact-export-history-region-height`, taller than the
  //        bubbles when there are few messages. 0.4.2 moved panel-level decoration
  //        to its ::after, so the decoration spread into the empty slot below the
  //        bubbles. Panel-level lighting now all hangs on the real compact panel
  //        `.compact-chat-surface-frame::after` (999px radius + overflow:hidden,
  //        so decoration is naturally clipped to the capsule), frosting/base colour
  //        on its ::before; the history overlay keeps only bubble-level decoration.
  //        Also removed the rim box-shadow and containing-block patch on scroll.
  // 1.5.19: 0.4.8 fixes "moving the mouse over does not always update the
  //        lighting". Four causes:
  //        (1) the old version only redrew on pointermove, so when the pointer was
  //        still but the page scrolled (including nested scroll containers) / the
  //        window resized / new messages were inserted, the highlight stayed at old
  //        coordinates; now scroll uses the capture phase and resize and DOM
  //        rebuilds both redraw; (2) the old MutationObserver used a 350ms debounce,
  //        which streaming chat output reset forever -> the surface list never
  //        refreshed (new bubbles had no lighting); now a 220ms throttle; (3) newly
  //        inserted surfaces used to wait for the next pointer move to get
  //        coordinates, now they get one right after the rebuild; (4) the old 0.1%
  //        string comparison + instant jumps swallowed small moves and felt stiff;
  //        now each frame interpolates 22% toward the target (stops once within
  //        0.06%). pointermove/pointerdown/pointerover all use the capture phase so
  //        a component's stopPropagation cannot swallow pointer events.
  //   1.6.40 back off when palette polling fails repeatedly (cap 60s); diagnostic
  //          reporting stops on 401/403 and retries 15s after a failure instead of
  //          resending every tick.
  //   1.6.41 self-upgrade reloads only happen in chat / subtitle / prompt / HUD
  //          windows; the editor page is no longer auto-reloaded.
  //   1.6.42 clear the poll backoff and refetch immediately when the window regains
  //          focus / becomes visible.
  //   1.6.43 same-origin requests carry the session cookie (remote windows no longer
  //          get 401); applying a new palette directly invalidates an old palette
  //          still being warmed.
  //   1.6.44 the warm-up gate identifies palettes by content (two switches within
  //          the same second no longer stick on the old theme); warm-up timeout
  //          lets it through.
  //   1.6.45 material rules go into @layer nts-theme.materials so the palette
  //          layer's !important no longer overrides them; fixed a prematurely
  //          closed comment in the settings-page material section.
  //   1.6.46 compact chat (/chat) outer wrapper stays transparent while materials
  //          are on; only the capsule carries the material.
  //   1.6.47 the compact chat outer transparency rule no longer overrides
  //          animation; collapse / expand wipe animations work again.
  //   1.6.48 no longer writes an inline !important background colour on
  //          #react-chat-window-root (the material layer can already override the
  //          palette, and the inline value beat the transparent outer layer of
  //          compact /chat); in modes with a chat wallpaper the material does not
  //          take over the message area.
  //   1.6.49 Material layer mirrors three host !important overrides it would
  //          otherwise outrank: no animations while #chat-container is dragged,
  //          transparent mobile tool buttons during the expand guard, and the
  //          guide's borderless composer while it takes over.
  //   1.6.50 The upgrade-reload draft guard checks every composer (React and
  //          legacy), not only the first match.
  //   1.6.51 Material rules skip the chat shell while it is minimized,
  //          collapsing or expanding, so the host's yarn ball and transitions apply.
  var SCRIPT_VERSION = '1.6.51';

  // Allowed colour value shapes. Deliberately conservative: only plain colours, so
  // arbitrary CSS can never be injected into a variable.
  var RE_HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
  var RE_RGB = /^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
  var RE_HSL = /^hsla?\(\s*-?[0-9]{1,3}(\.[0-9]+)?\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
  // CSS named colours (including transparent and currentcolor). inherit/initial/
  // unset/revert are not accepted: they make the colour inherit from the parent,
  // and body text being pulled off is where the "colour bleed" complaints came from.
  // transparent must be listed explicitly: the backend RE_KEYWORD_COLOR always
  // accepted it but this table missed it, so "set background to transparent"
  // showed success in the panel while no gate was generated and the declaration
  // never matched.
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
  // --nts-chat-blur is consumed as `backdrop-filter: var(--nts-chat-blur)`, so the
  // value must be in blur(...) form; a bare length makes the browser drop the
  // whole declaration.
  var RE_BLUR = /^blur\(\s*-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)\s*\)$/i;
  // Bare lengths that are valid for blur: blur() only accepts absolute lengths;
  // % / vh / vw invalidate the whole backdrop-filter. RE_LEN must allow them for
  // shadow offsets, so this stricter regex is used to avoid generating values like
  // blur(18%) that "pass validation but are invalid CSS".
  var RE_BLUR_BARE = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)$/i;
  // --nts-chat-opacity bridges to the native --neko-chat-opacity-factor (a
  // multiplier), range 0-1. This kind used to be missing, so the panel/backend
  // accepted it while this script validated it as a colour and silently dropped it:
  // a dead control that "looks adjustable but does nothing" (found by an
  // independent verifier). Zero-padded forms like 1.0 / 1.00 are allowed (users
  // most naturally write "100%" as 1.0); values outside 0-1 such as 1.5 / 2 / -0.3
  // are still rejected.
  var RE_NUMBER = /^(?:0|1|0?\.[0-9]+|1\.0+)$/;
  // Line height: unitless multiplier 0.8–3.0 (at most two decimals). Identical
  // to the backend RE_RATIO.
  var RE_RATIO = /^[0-9](?:\.[0-9]{1,2})?$/;
  // Time values (v0.1.11, breathing rhythm): 3s / 1.5s / 800ms; bare numbers are
  // seconds (3 = 3s). Capped at 60s: users cannot tell a longer loop is "moving",
  // and it has no practical use.
  var RE_TIME = /^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$/i;
  var RE_TIME_BARE = /^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$/;
  var TIME_MAX_SECONDS = 60;
  // Percentage multiplier parsing: returns a 0-1 fraction, or null if invalid or
  // >100%. Note RE_PCT's capture group is the decimal part, not the integer part
  // (an early misuse of pm[1] rejected `50%` and computed `33.3%` as 0.003).
  var RE_PCT_FULL = /^([0-9]{1,3}(?:\.[0-9]+)?)%$/;
  function pctToFraction(v) {
    var m = RE_PCT_FULL.exec(v);
    if (!m) return null;
    var n = parseFloat(m[1]);
    if (!(n >= 0) || n > 100) return null;
    return Math.round(n / 100 * 10000) / 10000;
  }

  /**
   * Split a shadow string on whitespace outside parentheses (same shape as the
   * panel's shadowTokens / the backend's _shadow_tokens). This used to be
   * v.split(/\s+/), which broke rgba(0, 0, 0, .35) into 7 fragments, so "the panel
   * accepts, the backend saves, the loader silently drops" and the most common
   * box-shadow form never took effect. Returns [] on unbalanced parentheses.
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

  // Non-colour entries: these variables take lengths/percentages/font names/numbers
  // instead of colours. Must stay in sync with _value_kind()/LENGTH_VARS/BLUR_VARS
  // in the plugin-side __init__.py, otherwise the panel accepts and the app side
  // silently drops (two real cases: blur(24px) judged invalid; --nts-chat-opacity
  // dropped entirely for lack of a number branch).
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
    // Radius of the chat backplate (message list base). Separate from --nts-radius
    // because that one is the generic radius also applied to buttons/input panels;
    // the backplate must be roundable on its own without rounding buttons too.
    '--nts-chat-panel-radius': 'length',
    // Chat background image: value is "none" or an absolute url(...) into the
    // backgrounds/ directory. It needs its own image branch: the generic RE_BAD
    // blacklist blocks url( for colour values.
    '--nts-chat-bg-image': 'image',
    '--nts-font-family': 'font',
    '--nts-chat-blur': 'blur',
    '--nts-shadow': 'shadow',
    '--nts-chat-opacity': 'number',
    // Master switch for "the haze over the chat background" (0 = fully clear,
    // 1 = native white veil + background blur). A 0–1 multiplier like
    // --nts-chat-opacity, same number branch.
    '--nts-bubble-veil': 'number',
    // Background image brightness: 0-1 multiplier, 1 = original, 0 = black.
    // Kind number, same branch.
    '--nts-chat-bg-image-brightness': 'number',
    // Background image fit: cover (fills, may crop) / contain (whole image
    // visible, base colour on the sides). Kind keyword: only these two keywords,
    // anything else is dropped (never falls back to an odd size).
    '--nts-chat-bg-fit': 'keyword',
    // Corner geometry: round (plain radius) / continuous (continuous curvature =
    // superellipse). Also keyword: the allowed values are fixed in KEYWORD_VALUES,
    // anything else is dropped.
    '--nts-corner-style': 'keyword',
    // The "fog" at the top of the background image = the mask-image top fade the
    // app puts on the history list container. 1 = same as native, 0 = fade fully
    // removed. Same number branch as above.
    '--nts-chat-top-fade': 'number',
    // ===== Panel edge lighting / glassmorphism (v0.1.11) =====
    // All optional; if unset the whole CSS block is not loaded and the look is native.
    // Mount points: see NTS_GLOW in targets.js:
    //   outer glow -> anchor::before (scroll has its own mask-image, an outer glow
    //                 could only show 4px)
    //   border/corners/reflection/inner shadow -> anchor::after
    //   frosted layer -> scroll::before, z-index:-1 (between the container
    //                    background and the message content)
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
    // ===== Liquid glass / custom gradients (v0.1.11, contract _work/FEATURE-CONTRACT-0.1.11.md §1) =====
    // All three ends (Python _value_kind / panel isSafeValue / this table) must agree
    // on kind: if any end does not know a new variable, you get "the panel saves,
    // the loader silently drops, the user sees no change".
    // Although this map is named NON_COLOR_VARS, colours are registered explicitly
    // too: the 'auto' fallback additionally allows lengths (see isSafeValue), which
    // is looser than the backend's colour check; registering makes them match exactly.
    '--nts-glass-filter': 'filter',
    '--nts-glass-tint': 'color',
    '--nts-glass-highlight': 'color',
    // Liquid-glass static layer (v1.5.13, contract 0.2.0 §1): rim is a colour,
    // sheen/noise are 0~1 strengths. sheen/noise use the number kind (the panel/JSON
    // may write "55%", sanitizeVars normalises it to 0.55). All three ends (Python
    // _value_kind / panel isSafeValue / this table) must agree on kind, otherwise
    // "the panel saves, the loader silently drops, the user sees no change" again.
    '--nts-glass-rim': 'color',
    '--nts-glass-sheen': 'number',
    '--nts-glass-noise': 'number',
    // Liquid-glass material (v1.5.14, contract 0.3.0 §1): lens/thickness/specular
    // are all 0~1 continuous values. Also number kind (panel/JSON may write "70%"
    // or 0.7, sanitizeVars normalises to 0–1).
    '--nts-glass-lens': 'number',
    // v0.12.0: background position / repeat are keywords; line height is a
    // unitless multiplier (ratio).
    '--nts-chat-bg-position': 'keyword',
    '--nts-chat-bg-repeat': 'keyword',
    '--nts-line-height': 'ratio',
    '--nts-glass-thickness': 'number',
    '--nts-glass-specular': 'number',
    // Lighting engine (v1.5.15, contract 0.4.0 §1): pointer light / edge dispersion /
    // flowing caustics, all 0~1. Unset (absent) == zero effect; an invalid value ==
    // the entry is dropped and warned once (noteRejected).
    '--nts-ambient-glow': 'number',
    '--nts-glass-dispersion': 'number',
    '--nts-glass-caustic': 'number',
    '--nts-bubble-glass-filter': 'filter',
    '--nts-bubble-glass-tint': 'color',
    '--nts-chat-gradient': 'gradient',
    // v0.13.0: text gradient, same linear/radial validation as the background gradient.
    '--nts-text-gradient': 'gradient',
    '--nts-panel-gradient': 'gradient',
    '--nts-user-bubble-gradient': 'gradient',
    '--nts-ai-bubble-gradient': 'gradient'
  };

  // Explicitly rejected dangerous fragments.
  // Note: parentheses are deliberately **not** banned here, since valid
  // rgb()/hsl()/var() values need them. Safety comes from each kind's fully
  // anchored regex below: the whole value must match a known safe shape, so `)`
  // can never be used to close the declaration early.
  var RE_BAD = /[;{}<>\\]|\/\*|\*\/|url\s*:|expression\s*\(|@import|javascript:|&#/i;

  // Chat background image: fully anchored to "none" or a url(...) under Theme
  // Studio's backgrounds/. Built into the app, images are served from
  // /api/theme_studio/backgrounds/, same-origin with the window.
  var RE_BG_VALUE = /^(?:none|url\(\s*"?(?:https?:\/\/(?:127\.0\.0\.1|localhost):[0-9]{2,5})?\/api\/theme_studio\/backgrounds\/[A-Za-z0-9._-]{1,80}"?\s*\))$/i;

  // ---------------------------------------------------------------- Utilities

  /**
   * Palette "freshness" stamp. theme.json no longer has a version field, so
   * updated_at stands in. Only used for logs/diagnostics; equality is always
   * decided by the raw JSON text.
   * @param {Object} p palette
   * @returns {string}
   */
  function stampOf(p) {
    if (!p || typeof p !== 'object') return '';
    return String(p.updated_at || p.name || '');
  }

  /**
   * Palette signature for the warm-up gate, computed from content rather than
   * updated_at alone: server timestamps have one-second resolution, so clicking
   * two image themes within the same second would treat both palettes as the same
   * one and stick on the old theme.
   */
  function warmKeyOf(p) {
    var text = '';
    try {
      text = JSON.stringify([p.name, p.enabled, p.vars, p.dark_vars, p.material_vars]);
    } catch (e) {
      text = stampOf(p);
    }
    // FNV-1a 32-bit: only used to tell "is this the same palette", no collision
    // resistance needed.
    var h = 0x811c9dc5;
    for (var i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return stampOf(p) + '|' + text.length + '|' + h.toString(16);
  }
  /* ===== IMAGE_WARM_BEGIN ===== */
  // ------------------------------------------------- Background image warm-up (v1.5.21)
  //
  // When switching themes the background image URL changes and the browser must
  // download / decode the new image first; as soon as the CSS variable changes the
  // old image is gone while the new one has not arrived, so the chat area flashes
  // a blank base colour. The bigger the image the more visible, especially the
  // first time a theme is loaded.
  //
  // So when "the theme really changed" is detected, warm the new images first and
  // only change the CSS once they are ready (or after a 1.5s timeout). The first
  // apply goes the same way: a cached image theme should not flash on cold start
  // either. Only our own backgrounds/ directory is warmed (constrained by
  // RE_BG_VALUE); no external links are touched.
  var IMAGE_WARM_TIMEOUT_MS = 1500;
  var warmedImages = Object.create(null);   // url -> 'ok' | 'fail'
  var warmWaiters = Object.create(null);    // url -> [fn]
  var lastWarmKey = null;   // signature of the last palette we decided to apply directly
  var warmingKey = null;    // signature currently being warmed
  var warmPending = null;   // palette to apply once warm-up finishes
  var warmSeq = 0;          // warm-up ticket: only the latest warm-up may apply in its callback

  /** All background image URLs in a palette (light / dark deduplicated, only our backgrounds/). */
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
   * Warm a set of images: call back once, and only once, when all are settled
   * (success or failure) or on timeout. Already-warmed URLs are skipped, no
   * duplicate requests.
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
      // A timeout must "let it through": mark images that have not returned as
      // handled, otherwise when the callback re-enters applyPalette it still
      // decides to warm and starts another 1.5s round; if an image never returns,
      // the new palette never takes effect.
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
   * Palette endpoint. Built into the app it is same-origin with the page, so the
   * relative path is used directly instead of building a plugin service URL.
   * @returns {string}
   */
  function themeUrl() {
    return API_BASE + '/theme.json';
  }

  // ---------------------------------------------------------------- Value validation

  /**
   * Whether a value can be safely written into the given variable.
   * @param {string} name  CSS variable name
   * @param {string} value candidate value
   * @returns {boolean}
   */
  /** Allowed values for keyword variables (one-to-one with backend KEYWORD_VARS;
   *  changing one place means changing all three). */
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
      // A background image has only two valid shapes, fully anchored; it skips
      // RE_BAD (url( is its normal form).
      return RE_BG_VALUE.test(v);
    }
    if (RE_BAD.test(v)) return false;

    // v0.1.11: two new value kinds (contract §2). Both are fully anchored against
    // token / colour-stop whitelists, so `url(...)`, `var(...)`, `expression(...)`
    // and any extra content are rejected.
    if (kind === 'keyword') return (KEYWORD_VALUES[name] || []).indexOf(v.toLowerCase()) >= 0;
    // Line height is a "unitless multiplier", unlike the 0–1 number multiplier,
    // so it cannot share RE_NUMBER.
    if (kind === 'ratio') {
      if (!RE_RATIO.test(v)) return false;
      var ratio = parseFloat(v);
      return ratio >= 0.8 && ratio <= 3;
    }
    if (kind === 'filter') return isSafeFilterChain(v);
    if (kind === 'gradient') return isSafeGradient(v);

    if (kind === 'color' || kind === 'auto') {
      // 'auto' is the fallback for variables "added after this version and not yet
      // in the whitelist". It comes from a real incident: after
      // --nts-chat-panel-radius was added to the panel and backend, the user's open
      // windows still ran the old loader (previous script in the browser cache),
      // whose default kind was 'color', so '24px' was silently dropped as an
      // invalid colour: the background colour applied, the radius did not, and
      // apply_theme still returned succeeded with no panel error. Only a restart
      // fixed it, a systemic source of "changed in the panel, no effect in the app".
      // The fallback only allows the two anchored safe shapes, color and length,
      // so no other safety is reduced.
      var asColor =
        isNamedColor(v) || RE_HEX.test(v) ||
        RE_RGB.test(v) || RE_HSL.test(v);
      if (asColor) return true;
      if (kind === 'color') {
        // Deliberately does **not** support `var(--nts-*)` derived colours: the panel
        // and backend only accept concrete colours; allowing it in the loader alone
        // would create a path only reachable by hand-editing theme.json, and combos
        // like "a colour variable referencing a length variable" invalidate the whole
        // declaration (silently no colour). All three validators must match exactly,
        // see THEME_CONTRACT §9.10b.
        return false;
      }
      // kind === 'auto' and not a colour -> try again as a length (same check as
      // the length branch below)
      if (v.indexOf('-') !== -1) return false;
      return RE_LEN.test(v) || RE_PCT.test(v) || /^0$/.test(v);
    }
    if (kind === 'length') {
      // Negative lengths are always invalid here (the whole declaration is
      // dropped -> the panel reports success, nothing changes). The backend
      // _is_safe_value and panel isSafeValue both reject '-' separately, so this
      // must match; RE_LEN only allows a minus sign for shadow offsets (shadow
      // has its own token branch).
      if (v.indexOf('-') !== -1) return false;
      if (RE_LEN.test(v) || RE_PCT.test(v)) return true;
      if (/^0$/.test(v)) return true;
      return false;
    }
    if (kind === 'blur') {
      // The panel always outputs blur(...), but contract §3 allows bare lengths;
      // both are accepted and stored as-is. Negative blur is invalid too (the
      // whole backdrop-filter fails), rejected like the backend/panel do.
      if (v.indexOf('-') !== -1) return false;
      // % / vh / vw are not valid blur radii (CSS drops the whole declaration);
      // rejected, consistent with the backend RE_BLUR_BARE.
      return RE_BLUR.test(v) || RE_BLUR_BARE.test(v) || /^0$/.test(v);
    }
    if (kind === 'time') {
      // Breathing period (v0.1.11). Bare numbers are seconds (3 = 3s);
      // sanitizeVars adds the unit later. Only s / ms: breathing rhythm needs no
      // other units, and loosening this would only let invalid CSS through.
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
      // The panel's number inputs allow percentages (`50%` = half transparent) and
      // the backend accepts them; this script used to accept only 0-1 decimals, so
      // "the panel shows no error, saving succeeds, it is silently dropped here".
      // Now <=100% is accepted and normalised to a fraction in sanitizeVars
      // (multiplier semantics).
      return pctToFraction(v) !== null;
    }
    if (kind === 'font') {
      return RE_FONT.test(v) && !RE_BAD.test(v);
    }
    if (kind === 'shadow') {
      // box-shadow syntax is <inset>? && <length>{2,4} && <color>?; a plain token
      // whitelist would allow '0 0 10% #000' (percentages invalid), '1px' (too few
      // lengths) etc., which the browser drops entirely -> the panel reports
      // success, nothing changes. All three validators must agree.
      // Multi-layer values (comma separated) are split into layers and checked one
      // by one; accepting only a single layer would reject
      // `0 0 20px rgba(...), 0 0 15px rgba(...)` entirely -> no gate -> the shadow
      // silently disappears.
      var layers = shadowLayers(v);
      if (!layers.length || layers.length > 4) return false;
      for (var li = 0; li < layers.length; li++) {
        if (!shadowLayerOk(shadowTokens(layers[li]))) return false;
      }
      return true;
    }
    return false;
  }

  // --------------------------------------------- New value kind validation: filter / gradient
  //
  // v0.1.11 adds two value kinds (contract §2; Python _filter_chain_ok/_gradient_ok,
  // the panel's isSafeFilterChain/isSafeGradient and this code must agree exactly):
  //   filter   = space-separated filter chain, 1–4 tokens, no repeats, blur ≤ 40px,
  //              others ≤ 400%
  //   gradient = linear/radial-gradient, 2–4 colour stops, whole string ≤ 300 chars
  // These values are spliced verbatim into `--nts-*: <value>` declarations, so they
  // must be fully anchored / token-whitelisted, never a loose "looks like a filter"
  // regex (that would open an injection channel through the theme JSON).
  var RE_FILTER_TOKEN = /^(blur|saturate|brightness|contrast|grayscale)\((\d{1,3}(?:\.\d+)?)(px|%)\)$/;
  var FILTER_MAX_TOKENS = 4;
  var FILTER_MAX_CHARS = 120;
  var FILTER_MAX_BLUR_PX = 40;
  var FILTER_MAX_PERCENT = 400;

  /**
   * Filter chain validation (contract §2.1).
   * @param {string} v e.g. `blur(20px) saturate(150%)`
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
   * Gradient validation (contract §2.2): only linear / radial, 2–4 colour stops,
   * stop percentages ≤ 100%. Top-level comma splitting must track parenthesis
   * depth, since rgba(0, 0, 0, .5) is full of commas.
   * @param {string} v e.g. `linear-gradient(135deg, #eef4ff 0%, #dfe9ff 100%)`
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

  /** Split a multi-layer box-shadow on top-level commas (same shape as the backend _split_shadow_layers). */
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

  /** Token validation for a single box-shadow layer (same check as the backend _shadow_layer_ok). */
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
   * A rejected variable is reported loudly only once (DEBUG logs everything).
   * Since v0.1.11 the message must include the variable name: when the user cannot
   * see the panel's validation mirror, "which variable, what value, judged as which
   * kind" in the console is the only self-diagnosis clue; a bare "reject value"
   * says nothing.
   */
  var warnedReject = Object.create(null);
  function noteRejected(name, value) {
    log('reject value', name, value);
    if (warnedReject[name]) return;
    warnedReject[name] = 1;
    try {
      var kind = NON_COLOR_VARS[name] || 'color';
      console.warn('[theme-studio] invalid value for variable ' + name + ' (kind=' + kind + '), skipped: ' +
        String(value).slice(0, 120) +
        '\n  Valid forms: see FEATURE-CONTRACT §2; once fixed it applies on the next poll.');
    } catch (e) { /* ignore */ }
  }

  /**
   * Filter down to a safe vars object.
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
      // Matches the plugin-side normalize_value(): wrap bare lengths in blur(...),
      // otherwise `backdrop-filter: 18px` is an invalid declaration the browser drops.
      if (NON_COLOR_VARS[k] === 'blur' && RE_BLUR_BARE.test(v)) v = 'blur(' + v + ')';
      // Normalise percentage multipliers to fractions: `50%` -> `0.5`. Keeping it
      // as-is would hand CSS `50%` and invalidate the whole declaration.
      if (NON_COLOR_VARS[k] === 'number') {
        var frac = pctToFraction(v);
        if (frac !== null) v = String(frac);
      }
      // Add a unit to time values: `animation-duration: 3` is invalid CSS (durations
      // need a unit); otherwise the whole animation declaration is dropped ->
      // "rhythm set but nothing moves".
      if (NON_COLOR_VARS[k] === 'time' && RE_TIME_BARE.test(v)) v = v + 's';
      out[k] = v;
    }
    return out;
  }

  // ---------------------------------------------------------------- Injection

  // v0.1.11 double-injection sentinel: reuse the <style> by its fixed id before
  // injecting. If the host (or the user by hand) injects this script again, the
  // second instance hits the same id and does not insert another; if several nodes
  // already share the id (template re-render / repeated append), only the first is
  // kept, avoiding "whoever writes last wins".
  var lastAppliedCss = '';
  function styleEl() {
    var el = document.getElementById(STYLE_ID);
    if (el) {
      if (el.tagName && String(el.tagName).toUpperCase() === 'STYLE') {
        removeDuplicateStyles(el);
        return el;
      }
      // The id is taken by a non-<style> node: remove it and create our own.
      if (el.parentNode) el.parentNode.removeChild(el);
    }
    el = document.createElement('style');
    el.id = STYLE_ID;
    el.setAttribute('data-nts', '1');
    (document.head || document.documentElement).appendChild(el);
    return el;
  }

  /** Remove extra <style> nodes that share keep's id. */
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

  /** Whether the injected <style> is still in the DOM and holds the most recently written content. */
  function styleIntact() {
    if (!lastAppliedCss) return true;
    var el = document.getElementById(STYLE_ID);
    return !!(el && el.parentNode && el.textContent === lastAppliedCss);
  }

  // v0.1.11 CSP self-check: when style-src is blocked by CSP, the <style> node is
  // still inserted into the DOM but none of its rules apply: variable injection
  // "succeeds", nothing changes, and there is no error (easily misread as "the
  // plugin does nothing"). Write a probe custom property into the injected content
  // and verify via getComputedStyle that it was actually parsed; if not, warn once,
  // no throw, no retry (CSP will not fix itself).
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
      console.warn('[NTS] Theme styles are blocked by CSP; the theme will not take effect');
    } catch (e2) { /* ignore */ }
  }

  function removeStyle() {
    var el = document.getElementById(STYLE_ID);
    if (el && el.parentNode) el.parentNode.removeChild(el);
    lastAppliedCss = '';
    clearGates();
    // v1.6.0: the material layer is gated by data-nts-mt, so removing styles must
    // also remove the attributes; otherwise MATERIAL_CSS still matches with the
    // variables missing and degrades into a leftover "semi-transparent base" look.
    clearMaterialAttrs();
  }

  // ------------------------------------------------------- Gate attributes (gates)
  //
  // Some stylesheet rules cannot be expressed with a "native fallback value" (the
  // native value is a gradient, or varies with context, or a whole group of
  // properties must change together). Those rules are all gated on
  //   :root[data-nts-<var>]      -- the user actually configured this variable
  //   :root[data-nts-active]     -- at least one variable is configured
  // So the loader must mirror these attributes onto documentElement, otherwise
  // those rules never apply (a real defect: only data-nts="1" was set and the rules
  // never matched).

  // Since v3 **every** generated rule is gated on per-mode attributes:
  //   :root:not([data-theme="dark"])[data-nts-l-<var>]  -- variable configured in the light group
  //   :root[data-theme="dark"][data-nts-d-<var>]        -- variable configured in the dark group
  // and there are no fallback values at all. Reason: fallbacks were measured on a
  // synthetic fixture page, not the real page's computed values (the real
  // .chat-body sits under .chat-surface-mode-compact, body has
  // .electron-chat-window, plus runtime-injected !important style layers), so any
  // measured fallback would silently rewrite unconfigured appearance.
  // With gating, "unconfigured == not loaded" holds by construction: no attribute
  // -> selector does not match -> zero effect.
  var GATE_ATTR = 'data-nts-';
  var ACTIVE_ATTR = 'data-nts-active';
  // Scope for light values: light means "not dark". Same semantics as the
  // generator's light gate prefix.
  var LIGHT_SCOPE = ':root:not([data-theme="dark"])';
  var gateApplied = null; // Array<string> attribute names set last time, for exact removal

  /** Variable name + mode -> gate attribute name: data-nts-l-foo / data-nts-d-foo
   *  Must match gateAttrFor() in gen-css.js exactly (strips the whole `--nts-` prefix). */
  function gateNameFor(varName, mode) {
    return GATE_ATTR + (mode === 'dark' ? 'd-' : 'l-') + String(varName).replace(/^--nts-/, '');
  }

  function clearGates() {
    var de = document.documentElement;
    if (!de) return;
    if (gateApplied) {
      for (var i = 0; i < gateApplied.length; i++) de.removeAttribute(gateApplied[i]);
    } else {
      // Without bookkeeping, fall back to a scan so no stale gates are left
      // (attribute changes trigger a style recalc)
      var names = [];
      /* Only clear "variable gates": `data-nts-l-<var>` / `data-nts-d-<var>`.
       * The old implementation fell back to the bare `data-nts-` prefix and also
       * deleted the loader's own state attributes (data-nts-active: some variable
       * configured / data-nts-paused: paused in background / data-nts-fade: theme
       * switch fade-in), which showed up as the fade being cut off when switching
       * themes and glass animations not moving after returning to the foreground. */
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
   * Mirror the current palette's variables as gate attributes on documentElement.
   * Idempotent: if the set is unchanged the DOM is not touched (saves a full-page
   * style recalc).
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
        // Dedupe by (mode, variable): the same variable may appear in both groups,
        // and those are two independent gates.
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

  /** Switch attribute for continuous-curvature corners: set only when the palette
   *  contains continuous. The CSS side (gen-css's @supports section) is gated on
   *  `[data-nts-squircle]`, and the whole section is inert when the engine lacks
   *  `corner-shape`, so the attribute can be set purely by value here. */
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

  // -------------------- Liquid-glass material layer (v1.5.14, contract 0.3.0)
  //
  // Goal: recreate the **perceptual elements** of Apple Liquid Glass in CSS on the
  // host's functional layers (top bar / input / bubbles / popovers / export area),
  // rather than piling decoration onto the page. Apple describes it as "a
  // translucent material that refracts and reflects its surroundings and flows
  // with context", which breaks down into four layers + one dynamic dimension.
  // Chromium does not support backdrop-filter referencing an SVG filter, so true
  // displacement refraction is impossible (argued in contract §2); this is a
  // perceptual equivalent:
  //
  //   1 see-through  backdrop-filter: from --nts-glass-filter (written by the
  //                  generated CSS, not touched here)
  //   2 tint         background-color: from --nts-glass-tint (same)
  //   3 edge         lens bright ring + thickness (new here, box-shadow combo):
  //                  1px highlight on top, 1px dark edge at the bottom, 1px
  //                  half-bright on all sides; lens adds a bright ring and inner
  //                  soft glow to mimic the glass edge "bending" light inward
  //   4 specular     radial specular highlight (new here, pseudo-element), position
  //                  --nts-spec-x/--nts-spec-y written by initGlassPointer to follow
  //                  the pointer; this is Apple glass's most recognisable trait:
  //                  the highlight moves with the light source/pointer instead of a
  //                  fixed sweep
  //   1 dynamics     keeps 0.2.0's sheen glint + noise grain; while scrolling
  //                  --nts-scroll-k=1 makes the specular highlight settle ("liquid"
  //                  flowing with the scroll); slight squash on press (see rule
  //                  group 11)
  //
  // Three hard constraints (carried over from 0.2.0, contract §1/§2/§6):
  //   1) No new variable is configured in old themes -> every rule is gated on
  //      data-nts-l/d-glass-*; unconfigured == zero effect (old themes are
  //      pixel-identical).
  //   2) Never write the element's own background-image (the generated CSS
  //      already claims it with !important); only add via pseudo-elements and
  //      box-shadow.
  //   3) The host .composer-input-shell / message bubbles are position:static, and
  //      absolutely positioned pseudo-elements need a positioned ancestor -> add
  //      position:relative (no offsets, no layout change); no isolation.
  //
  // Pseudo-element slots (audit of 46 host CSS files, script _work/glass_audit.py):
  //   both ::before + ::after free -- .composer-input-shell, .composer-overflow-popover,
  //       .message-bubble-user, .message-bubble-assistant, .compact-export-history-bubble
  //       -> ::before does noise, ::after does specular + sheen (two background
  //       layers, screen blend).
  //   only ::after free -- .window-topbar (::before taken by the dark gradient),
  //       .compact-export-history-scroll (::before taken by the panel frosted layer)
  //       -> ::after stacks specular + sheen + noise (screen / screen / overlay).
  //   both ::before and ::after taken -- .compact-chat-surface-frame, .composer-icon-button
  //       -> only box-shadow lens + thickness (no specular pseudo-element).
  //
  // The gate prefix adds :root:root:root:root so specificity beats the generated
  // !important rules. In a data URI # must be written as %23: a bare # truncates
  // the CSS in the middle of url().
  var GLASS_NOISE_URI = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='120' height='120' filter='url(%23n)' opacity='0.6'/></svg>\")";

  var GLASS_CSS = (function () {
    var LIGHT = ':root:not([data-theme=dark])';
    var DARK = ':root[data-theme=dark]';
    // Same as the generator: repeated :root raises specificity to beat the host's
    // runtime-injected !important.
    var BOOST = ':root:root:root:root';
    var MODES = [
      { pre: LIGHT, key: GATE_ATTR + 'l-' },
      { pre: DARK, key: GATE_ATTR + 'd-' }
    ];
    var lines = [];

    /** Build the selector list for "mode x gate variable x target".
     *  The rule applies if any variable in vars is configured in any mode; each
     *  variable gets its own selector, independent of the others. */
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

    // Uniform read for 0–1 variables: unset counts as 0 (= zero effect).
    var V_T = 'var(--nts-glass-thickness, 0)';
    var V_L = 'var(--nts-glass-lens, 0)';
    var V_S = 'var(--nts-glass-specular, 0)';
    var V_D = 'var(--nts-glass-dispersion, 0)';
    var V_A = 'var(--nts-ambient-glow, 0)';
    var V_C = 'var(--nts-glass-caustic, 0)';
    // Pointer light position (written by initGlassPointer, percentages): the
    // ambient light spot follows the pointer.
    var LIGHT_AT = 'var(--nts-light-x, 50%) var(--nts-light-y, 0%)';
    var V_K = 'var(--nts-scroll-k, 0)';
    var RIM = 'var(--nts-glass-rim, rgba(255, 255, 255, 0.55))';

    // Specular highlight position: follows the pointer (--nts-spec-x/y,
    // percentages); rests top-left on devices without a pointer.
    var SPEC_AT = 'var(--nts-spec-x, 28%) var(--nts-spec-y, 14%)';
    // Specular settles by 45% while scrolling (--nts-scroll-k is written by
    // initGlassPointer's scroll listener).
    var SPEC_ALPHA = 'calc(' + V_S + ' * (1 - 0.45 * ' + V_K + '))';

    var EDGE = ['rim', 'thickness', 'lens', 'dispersion'];

    // 1) Edge + thickness: the 4 surfaces whose outline is taken by
    //    --nts-glass-highlight. Use a box-shadow combo instead of outline so as not
    //    to fight the generated !important outline.
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

    // 2) Bubble group: append var(--nts-shadow) to keep the theme's drop shadow
    //    (the override would otherwise displace the generated rule).
    rule(sel(['.message-bubble-user', '.message-bubble-assistant',
      '.compact-export-history-bubble'], EDGE), edgeShadow(true));

    // 3) Surfaces without an outline conflict: rim stays an outline, lens/thickness
    //    go to box-shadow. 0.4.3: `.compact-export-history-scroll` was removed: it is
    //    a message list container sized by its reserved slot (no radius), and
    //    outline + inset shadows would draw a square-cornered band along the empty slot.
    rule(sel(['.composer-overflow-popover'], EDGE), [
      'outline: 1px solid ' + RIM + ' !important;',
      'outline-offset: -1px !important;',
      'box-shadow: inset 0 -1px 0 rgba(0, 0, 0, calc(' + V_T + ' * 0.30)),' +
        ' inset 0 0 0 1px rgba(255, 255, 255, calc(' + V_L + ' * 0.45)),' +
        ' inset 0 1px 0 rgba(255, 255, 255, calc(' + V_T + ' * 0.55)) !important;'
    ]);

    // 4) Focus fallback: the box-shadow rim covers the focus ring these two surfaces
    //    natively draw with box-shadow, and keyboard focus must stay visible -> add
    //    an outline (colour taken from the host's own focus ring, #40c5f1).
    rule(sel(['.composer-input-shell:focus-within'], ['rim']), [
      'outline: 2px solid rgba(64, 197, 241, 0.62) !important;',
      'outline-offset: 1px !important;'
    ]);
    rule(sel(['.compact-export-history-bubble:focus-visible'], ['rim']), [
      'outline: 2px solid rgba(64, 197, 241, 0.62) !important;',
      'outline-offset: 2px !important;'
    ]);

    // 5) Positioning: position:static surfaces need a positioned ancestor to host
    //    the pseudo-elements. .window-topbar is already relative; the popover is
    //    absolute and export bubbles relative, left alone;
    //    .compact-export-history-scroll is deliberately skipped: its ::before
    //    frosted layer fills its current positioned ancestor, and making it
    //    relative would change that layer's radius/extent.
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

    // 6) Composite lighting layer (0.4.0 rework): ambient pointer light + caustic +
    //    specular + sheen. All four go through custom-property slots (--nts-fx-*),
    //    so any single configured variable composites correctly without overriding
    //    the others. 0.3.0's "black mask + noise overlay" had a real defect: veil
    //    alpha = 1 - noise, so with noise unset it was a fully opaque black layer
    //    covering the specular/sheen below (and after screen blending it vanished),
    //    meaning the top bar and export area specular/sheen were often dead in
    //    0.3.0. 0.4.0 removed that approach; noise is kept only on the 5 surfaces
    //    with a free ::before (top bar/export area no longer support noise).
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
    // Fixed slot order: ambient -> caustic -> specular -> sheen. The list length is
    // constant, so the background-size/position/blend mapping never drifts with
    // configuration.
    function fxBody() {
      return FX_STRUCT.concat([
        'background-image: var(--nts-fx-amb, none), var(--nts-fx-caustic, none),' +
          ' var(--nts-fx-spec, none), var(--nts-fx-sheen, none) !important;',
        'background-size: 100% 100%, 240% 240%, 100% 100%, 220% 100% !important;',
        'background-repeat: no-repeat, no-repeat, no-repeat, no-repeat !important;',
        'background-position: 0 0, 50% 50%, 0 0, 120% 0 !important;',
        'background-blend-mode: screen, screen, screen, screen !important;',
        'mix-blend-mode: screen !important;',
        // Two separate animation slots: sheen alone runs only the glint, caustic
        // alone runs only the caustics.
        'animation: var(--nts-anim-sheen, none), var(--nts-anim-caustic, none) !important;'
      ]);
    }

    // 0.4.1 fix: **do not add `.compact-export-history-scroll` here**.
    // It is position:static (§5 deliberately skips relative, which would move the
    // ::before frosted layer's anchor), so an absolute + inset:0 ::after here would
    // fill an outer positioned ancestor, showing as a whole square-cornered bright
    // screen above the export/history panel (very obvious once 0.4.0's
    // ambient/specular is on). The lighting layer only goes on the 6 surfaces with
    // a definite containing block.
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
    // Animations attach only when their variable is configured (0.3.0 always
    // attached the sheen animation, pure waste when sheen=0).
    rule(sel(FX_SURFACES, ['sheen'], '::after'), [
      '--nts-anim-sheen: nts-glass-sheen 7.5s ease-in-out infinite !important;'
    ]);
    rule(sel(FX_SURFACES, ['caustic'], '::after'), [
      '--nts-anim-caustic: nts-glass-caustic 13s ease-in-out infinite !important;'
    ]);

    // 7) Noise (::before): only the 5 surfaces with a free pseudo-element; the top
    //    bar and export area are excluded.
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

    // 8) Sheen sweep (only moves the 4th background-position layer = sheen).
    //    After 60% it holds position: the band sweeps out, rests, then rewinds,
    //    avoiding a hard snap back.
    lines.push('@keyframes nts-glass-sheen {\n' +
      '  0% { background-position: 0 0, 50% 50%, 0 0, 120% 0; }\n' +
      '  60%, 100% { background-position: 0 0, 50% 50%, 0 0, -40% 0; }\n' +
      '}');
    // 9) Caustic breathing (only animates the 2nd background-size layer = caustic):
    //    the light spot slowly swells and shrinks, like light refracted off water
    //    wobbling behind the glass.
    lines.push('@keyframes nts-glass-caustic {\n' +
      '  0%, 100% { background-size: 100% 100%, 240% 240%, 100% 100%, 220% 100%; }\n' +
      '  50% { background-size: 100% 100%, 300% 260%, 100% 100%, 220% 100%; }\n' +
      '}');

    // 9) "Liquid": the glass squashes slightly on press (only the two truly clickable
    //    surfaces, so message bubble scrolling is not affected).
    rule(sel(['.composer-icon-button'], ['thickness'], ':active'), [
      'transform: scale(0.965) !important;'
    ]);
    rule(sel(['.composer-overflow-popover'], ['thickness'], ':active'), [
      'transform: scale(0.985) !important;'
    ]);

    // 10) Pause all NTS animations while the window is hidden (0.4.0 performance
    //     item): background tabs no longer keep compositing. The selector uses
    //     :root[data-nts-paused] + 4 x :root to beat high-specificity rules in the
    //     generated CSS.
    //     (The animation rules are (0,8,2), so the pause head needs 8 x :root to
    //     reach (0,10,2); with 5 it was (0,7,2) and lost.)
    var PAUSE_HEAD = ':root[data-nts-paused]:root:root:root:root:root:root:root body ';
    var pauseSel = [];
    for (var q = 0; q < FX_SURFACES.length; q++) {
      pauseSel.push(PAUSE_HEAD + FX_SURFACES[q] + '::after');
    }
    for (var n2 = 0; n2 < dual.length; n2++) {
      pauseSel.push(PAUSE_HEAD + dual[n2] + '::before');
    }
    // 0.4.3: the panel decoration layer moved from the history overlay (scroll) to
    // the ::after of the real compact panel (.compact-chat-surface-frame),
    // consistent with NTS_GLOW in the generated CSS.
    pauseSel.push(PAUSE_HEAD + '.compact-chat-surface-frame::after');
    lines.push(pauseSel.join(',\n') + ' { animation-play-state: paused !important; }');

    // 11) Reduced-motion preference: stop sweep/caustics (the band rests off-view,
    //     leaving only rim / thickness / specular, no flashing).
    //     The animation is attached by sel(...) rules at specificity (0,8,2)
    //     (mode prefix + gate attribute + BOOST + target), and all of them are
    //     !important in the same sheet, so this selector needs a higher one or it
    //     silently loses: 8 x :root + attribute + target = (0,10,2).
    var RM_HEAD = ':root[' + ACTIVE_ATTR + ']:root:root:root:root:root:root:root body ';
    var rmTargets = FX_SURFACES.slice();
    var rmSel = [];
    for (var r = 0; r < rmTargets.length; r++) {
      rmSel.push(RM_HEAD + rmTargets[r] + '::after');
    }

    // 12) Theme switch transition (v1.5.20): when applyPalette detects a stamp
    //     change it sets data-nts-fade on :root for 420ms, so functional-layer
    //     colours/borders/shadows transition over 280ms instead of cutting hard.
    //     No flash on initial load (no previousStamp) or when content is unchanged;
    //     disabled entirely under reduced motion.
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

  // Wrapped in nts-theme's sublayer nts-theme.materials: the whole static palette
  // sheet uses !important inside @layer nts-theme, and layered !important always
  // beats unlayered, so if the material rules were not layered, background / blur /
  // shadow / radius would all be overridden by the palette when materials are on.
  // For !important, an explicit sublayer beats its parent layer's own rules, so
  // materials win over the palette while still beating the host's unlayered
  // !important. Only the --nts-mt-* custom properties here are normal declarations;
  // no other layer defines them, so layer order does not affect them.
  var MATERIAL_CSS = [
    '@layer nts-theme.materials {',
    '/* ===== Advanced material layer (contract 0.4.0) =====',
    '   Every rule is gated on :root[data-nts-mt]: without that attribute nothing matches and the app keeps its original look.',
    '   Only writes background / border / box-shadow / backdrop-filter / border-radius / animation,',
    '   never color / display / position / width / height / overflow / z-index / transform,',
    '   so text readability, layout and hit areas are unaffected by materials. */',
    ':root[data-nts-mt] { --nts-mt-mx: 50%; --nts-mt-my: 50%; --nts-mt-light-angle: 162deg; }',
    '',
    '/* ---- Per-component variable binding (component-specific value first, falls back to global) ---- */',
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
    '/* ---- Base surfaces: declared once, everything via the -c variables ---- */',
    ':root[data-nts-mt]:root:root:root:root body .compact-chat-surface-frame,',
    ':root[data-nts-mt]:root:root:root:root body .chat-window,',
    // Not while minimized / collapsing / expanding: the host styles those states
    // itself (yarn ball, scale-and-move transitions) and the layered material
    // !important would otherwise paint a panel behind the ball and replace the
    // shell's transitions.
    ':root[data-nts-mt]:root:root:root:root body #react-chat-window-shell:not(.is-minimized):not(.is-collapsing):not(.is-expanding),',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-user,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-assistant,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-tool,',
    ':root[data-nts-mt]:root:root:root:root body .message-bubble-system,',
    ':root[data-nts-mt]:root:root:root:root body .compact-export-history-bubble,',
    ':root[data-nts-mt]:root:root:root:root body .composer-input-shell,',
    ':root[data-nts-mt]:root:root:root:root body .composer-panel,',
    // The message area is where the chat wallpaper lands (the palette sheet paints
    // --nts-chat-bg-image on .message-list). Being layered, the material layer
    // would beat those rules, so when the current mode has a wallpaper it does not
    // take over the message area.
    ':root[data-nts-mt]:not([data-theme="dark"]):not([data-nts-l-chat-bg-image]):root:root:root body .message-list,',
    ':root[data-nts-mt][data-theme="dark"]:not([data-nts-d-chat-bg-image]):root:root:root body .message-list,',
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

    '/* Backdrop blur only for containers without their own transform / filter; the shell / inner root / popovers / export bubbles are in the no-blur group below. */',
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
    '/* The chat window base colour actually lives on the inner root (the palette layer gives it a solid colour with !important);',
    '   with materials on it steps aside so the glass can show through from the shell. */',
    ':root[data-nts-mt]:root:root:root:root:root body #react-chat-window-root {',
    '  background-color: var(--nts-mt-bg-c, var(--nts-mt-bg)) !important;',
    '  background-image: none !important;',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '/* The shell / inner root / popovers / export bubbles carry their own transform, filter or will-change (see audit_mt_backdrop_hazards.py);',
    '   Chromium often fails to paint them entirely with backdrop-filter, so blur is removed and the glass is expressed via base colour / layered backgrounds / border / shadow. */',
    ':root[data-nts-mt]:root:root:root:root:root body #react-chat-window-shell:not(.is-minimized):not(.is-collapsing):not(.is-expanding),',
    ':root[data-nts-mt]:root:root:root:root:root body .composer-icon-popover,',
    ':root[data-nts-mt]:root:root:root:root:root body .compact-export-history-bubble {',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '',
    '/* ---- Settings window (api_key_settings.html): where the panel "Settings" component lands ----',
    '   It is merged into the base-surface rule above (same 9-layer background + full box-shadow),',
    '   so the settings window and the chat window are the same glass. Only the radius is removed separately:',
    '   a radius on a fullscreen window would clip content; .container still takes radius-c from the base surface. */',
/* The message area is one large scrolling surface: behind it is the same window glass, so blur makes no
   visible difference, but every scroll recomputes backdrop-filter for the whole area, the top cause of
   scroll jank. Background/radius/shadow are kept.
   Specificity: the blur rule above is (0,7,1); this must be higher to win (both !important, same
   layer), so 6 x :root + attribute + class = (0,8,1). With 4 x :root it was (0,6,1) and lost. */
    ':root[data-nts-mt]:root:root:root:root:root body .message-list {',
    '  -webkit-backdrop-filter: none !important;',
    '  backdrop-filter: none !important;',
    '}',
    '',
    ':root[data-nts-mt]:root:root body.api-key-settings-page #main-content {',
    '  border-radius: 0 !important;',
    '}',

    '/* Inside the settings page: the host hard-codes the content area and config cards to #fff, so changing only the shell still leaves white,',
    '   hence the inner parts follow too. In dark theme the host dark-mode.css already has dedicated rules; not overridden here. */',
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
    '/* ---- Power saver: frequent small elements no longer take part in backdrop blur compositing ----',
    '   Behind bubbles/input/top bar is the semi-transparent window base, so blur makes almost no visible difference,',
    '   but twenty bubbles on screen means twenty backdrop-filter layers. The saver tier simply does not declare it.',
    '   Specificity (0,8,1) = 5 x :root + 2 attributes + class, above the (0,7,1) blur rule it has to override. */',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .message-bubble-user,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .message-bubble-assistant,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .message-bubble-tool,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .message-bubble-system,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .compact-export-history-bubble,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .composer-input-shell,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .composer-panel,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .message-list,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .window-topbar,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .composer-overflow-popover,',
    ':root[data-nts-mt][data-nts-mt-quality="saver"]:root:root:root:root body .composer-icon-popover {',
    '      -webkit-backdrop-filter: none !important;',
    '      backdrop-filter: none !important;',
    '}',

    '/* ---- Buttons: small, so no noise/light tracking; only base colour/border/radius/shadow ---- */',
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
    '/* ---- Motion: slow dye drift + ambient reflection sweep (none when --nts-mt-anim is off) ---- */',
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
    '/* ---- Interaction motion: enter / expand / hover / press ---- */',
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
    '/* ---- Fallback: when the engine lacks backdrop blur, trade for readability with a more solid base (no fake glass) ---- */',
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
    // The fallback has 8 layers and must carry its own size/position: otherwise it
    // inherits the main rule's 9 values, the noise gets ambient-size (stretched into
    // blotches) and the dye loses drift-size (stops drifting).
    '    background-size: auto, auto, auto, auto, auto, auto, auto, var(--nts-mt-drift-size) !important;',
    '    background-position: 0 0, 0 0, 0 0, 0 0, 0 0, 0 0, 0 0, 0% 50% !important;',
    '  }',
    '}',
    '',
    '/* ---- System reduced motion: materials run no drift/enter animations ---- */',
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
    '/* ---- Compact chat (/chat floating capsule): outer wrappers stay transparent ----',
    '   In compact mode the host index.css makes outer layers like root / .chat-window / .composer-panel transparent,',
    '   so only the .compact-chat-surface-frame capsule has an appearance. Being layered, the material !important would beat',
    '   that host rule, so the outer layers are made transparent again over the same scope; the capsule itself still gets the material. */',
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
    // No animation here: .compact-chat-surface-shell relies on the host's
    // neko-compact-collapse-wipe / expand-wipe animations to collapse / expand;
    // touching it here kills the transition.
    '}',
    '',
    // Host overrides that the layered material rules would otherwise outrank
    // (layered !important beats the host's unlayered !important regardless of
    // specificity). Each mirrors a host rule with the same scope and only the
    // properties the material rules set.
    '/* index.css: no animations while the legacy chat container is dragged. */',
    ':root[data-nts-mt]:root:root:root:root:root body #chat-container.dragging * {',
    '  animation: none !important;',
    '}',
    '/* index.css (mobile): tool buttons stay transparent during the expand guard. */',
    '@media only screen and (max-width: 768px) {',
    '  :root[data-nts-mt]:root:root:root:root:root body:not(.electron-chat-window):not(.lanlan-pet-mode) #react-chat-window-shell.is-mobile-expand-guarding .composer-tool-btn,',
    '  :root[data-nts-mt]:root:root:root:root:root body:not(.electron-chat-window):not(.lanlan-pet-mode) #react-chat-window-shell.is-mobile-expand-guarding .composer-tool-btn:hover,',
    '  :root[data-nts-mt]:root:root:root:root:root body:not(.electron-chat-window):not(.lanlan-pet-mode) #react-chat-window-shell.is-mobile-expand-guarding .composer-tool-btn:active,',
    '  :root[data-nts-mt]:root:root:root:root:root body:not(.electron-chat-window):not(.lanlan-pet-mode) #react-chat-window-shell.is-mobile-expand-guarding .composer-tool-btn:focus {',
    '    background-color: transparent !important;',
    '    background-image: none !important;',
    '  }',
    '}',
    '/* yui-guide.css: while the guide takes over, the composer drops its border and shadow. */',
    ':root[data-nts-mt]:root:root:root:root:root body.yui-taking-over #react-chat-window-root .composer-input-shell,',
    ':root[data-nts-mt]:root:root:root:root:root body.yui-taking-over #react-chat-window-root .composer-input-shell:focus-within {',
    '  border-color: transparent !important;',
    '  box-shadow: none !important;',
    '}',
    '}'
  ].join('\n');

  // -------------------- Liquid glass: pointer specular highlight + scroll flow (v1.5.14, rewritten as v1.5.19 in 2026-11)
  //
  // Only writes CSS variables, never touches host/theme declarations; rAF
  // throttled, pointermove is passive. Each glass surface tracks its own target
  // position (percentages) and current value, moving 22% toward the target each
  // frame, so the highlight "glides" instead of jumping; once the pointer stops
  // the animation settles and stops producing frames.
  //
  // The four "mouse moved but nothing happened" bugs fixed in v1.5.19:
  //   1. The old version only redrew on pointermove: with the pointer still while
  //      the page scrolled / new messages were inserted, the highlight stayed at
  //      old coordinates; now scroll (capture phase, including nested scroll
  //      containers), resize and DOM rebuilds all redraw.
  //   2. The old MutationObserver used a 350ms debounce; during streaming chat
  //      output the DOM keeps changing, the timer was reset forever -> the
  //      surface list never refreshed (new bubbles had no lighting); now a 220ms
  //      throttle.
  //   3. Newly inserted surfaces used to wait for the next pointer move to get
  //      coordinates; now they get one right after the rebuild.
  //   4. The old version skipped writes via a 0.1% string comparison, swallowing
  //      small moves; now it interpolates numerically and stops only once within
  //      0.06%.
  //
  // v1.5.20 (performance + visual polish):
  //   1. Full lighting hibernation: when the palette has no lighting variable at
  //      all (non-glass theme), refreshGlassPanels / MutationObserver /
  //      onGlassPointer return immediately, no DOM scan every 220ms and no rAF:
  //      zero pointer-tracking cost for normal themes.
  //   2. Theme switch transition: when applyPalette detects a stamp change it
  //      sets data-nts-fade on :root for 420ms, so functional-layer
  //      colours/borders/shadows transition over 280ms instead of cutting hard.
  // -------------------- Containing block fix (v1.5.17)
  //
  // The lighting layer and panel decoration layer are `position:absolute; inset:0`
  // pseudo-elements that rely on the host element itself being a positioned
  // ancestor. Measured (compact mode, neko-chat-window.css), these hosts are
  // `position: static`: .composer-input-shell, .message-bubble-user,
  // .message-bubble-assistant, .compact-export-history-scroll. Absolute
  // pseudo-elements on them escape to the nearest positioned ancestor, i.e. the
  // position:fixed .compact-export-history-anchor, a big square-cornered box
  // larger than the visible panel, which showed up as "every theme has an extra
  // square-cornered light screen".
  //
  // This cannot be hard-coded in CSS: some hosts are themselves absolute
  // (.composer-overflow-popover), and forcing relative would change their
  // positioning semantics. So only elements whose **computed value is static** get
  // an inline relative (layout-equivalent, no offset), marked to avoid reprocessing.
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
      try { pos = window.getComputedStyle(el).position || 'static'; } catch (e2) { /* fall back to static */ }
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
  var GLASS_FOLLOW = 0.22;      // fraction moved toward the pointer target per frame (higher = snappier)
  var GLASS_SNAP = 0.0006;      // snap when the difference is under 0.06%, avoiding endless micro-animation
  var GLASS_REBUILD_MS = 220;   // rebuild throttle after DOM changes
  var GLASS_STALE_MS = 900;     // if the list has not been rebuilt for this long, rebuild on the next pointer move
  // Lighting gate: when the palette has no "lighting variable" at all, pointer
  // tracking hibernates entirely; non-glass themes need no DOM scan every 220ms
  // and no rAF (v1.5.20 performance optimisation).
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

  // Short theme switch transition (v1.5.20): applying a new theme sets
  // data-nts-fade on the root for 420ms so functional-layer colours/borders/shadows
  // transition over 280ms instead of cutting hard; CSS disables it under reduced motion.
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
    // No DOM sweep without lighting variables: for non-glass themes this would be
    // wasted work every 220ms.
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
      // New surface: cx/cy left empty so the first frame lands on the pointer
      // position directly (no glide in from 0,0)
      next.push(keep || { el: el, cx: null, cy: null, tx: 0, ty: 0, sx: '', sy: '', lx: '', ly: '' });
    }
    glassPanels = next;
    // Redraw at the current pointer right after rebuilding: newly mounted
    // bubbles/top bar do not have to wait for the user to move the mouse again
    scheduleGlassSpecular();
  }

  function scheduleGlassRebuild() {
    if (glassRebuildTimer) return; // throttle: under continuous changes it still rebuilds every 220ms
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
      // Specular: smoothed position (a bit of "glide" inertia)
      var sx = glassPct(g.cx), sy = glassPct(g.cy);
      if (sx !== g.sx || sy !== g.sy) {
        g.sx = sx; g.sy = sy;
        g.el.style.setProperty('--nts-spec-x', sx);
        g.el.style.setProperty('--nts-spec-y', sy);
      }
      // Pointer light: uses the raw pointer position (snappier than specular),
      // raised 18% vertically as if "the light source is above"
      var lx = glassPct(tx), ly = glassPct(Math.max(0, ty - 0.18));
      if (lx !== g.lx || ly !== g.ly) {
        g.lx = lx; g.ly = ly;
        g.el.style.setProperty('--nts-light-x', lx);
        g.el.style.setProperty('--nts-light-y', ly);
      }
    }
    if (moving) scheduleGlassSpecular(); // keep running frames while there is a gap; stops once settled
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
    // The list may be stale due to streaming messages/page switches: rebuild on
    // pointer movement
    var now = Date.now ? Date.now() : new Date().getTime();
    if (!glassPanels.length || now - glassLastRebuild > GLASS_STALE_MS) scheduleGlassRebuild();
    scheduleGlassSpecular();
  }

  // Scrolling moves "surfaces" under a still pointer; scroll does not bubble, so
  // the capture phase is used to catch nested scroll containers.
  function onGlassScroll() {
    var root = document.documentElement;
    /* Non-glass themes do not write --nts-scroll-k: the variable only drives glass
     * specular settling, writing it triggers a full-page style recalc, and
     * scrolling is high-frequency for normal themes. Reset it to 0 once when
     * switching away, otherwise switching back to a glass theme would stay stuck
     * in the "settled" still state. */
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
    } catch (e) { /* environments without document.hidden: keep animating */ }
    if (!document.hidden) { scheduleGlassRebuild(); scheduleGlassSpecular(); }
  }

  function initGlassPointer() {
    refreshGlassPanels();
    if (glassPointerBound) return;
    glassPointerBound = true;
    try {
      // Capture phase: pointer moves are not lost even if a component calls stopPropagation
      document.addEventListener('pointermove', onGlassPointer, { passive: true, capture: true });
      document.addEventListener('pointerdown', onGlassPointer, { passive: true, capture: true });
      document.addEventListener('pointerover', onGlassPointer, { passive: true, capture: true });
      // Pause all NTS animations in background tabs: saves power and avoids
      // animation phase jumps on return.
      document.addEventListener('visibilitychange', syncAnimationPause);
      syncAnimationPause();
      document.addEventListener('scroll', onGlassScroll, { passive: true, capture: true });
      window.addEventListener('scroll', onGlassScroll, { passive: true });
      window.addEventListener('resize', function () { refreshGlassPanels(); }, { passive: true });
      // The theme library/message list keep adding and removing nodes: throttle
      // surface list rebuilds (the old debounce starved during streaming output).
      if (window.MutationObserver && document.body) {
        glassObserver = new MutationObserver(function () {
          if (!glassFxActive()) return; // non-glass theme: do not even schedule a rebuild timer
          scheduleGlassRebuild();
        });
        glassObserver.observe(document.body, { childList: true, subtree: true });
      }
    } catch (e) { /* very old WebView: degrade to a static highlight position */ }
    scheduleGlassSpecular();
  }

  /**
   * Whether the stylesheet is actually loaded (the one whose href contains
   * neko-theme-studio.css). If variables are injected but the stylesheet is not
   * loaded, gate attributes are still set and variables still resolve, yet no rule
   * uses them: nothing changes and the console is silent. This is the most hidden
   * form of "changed a colour in the panel, the app did not react" and must be
   * reported loudly.
   */
  /** Count "real style rules", descending into grouping rules.
   *
   *  Since v3.1 the whole sheet is wrapped in `@layer nts-theme { … }` (to use
   *  layered !important to beat the native two-id !important rules in index.css),
   *  so the top-level cssRules is always 1. Reporting .length directly would make
   *  diagnose() look like "only 1 rule loaded" and misjudge a healthy state as
   *  broken. This recursively expands @layer / @media / @supports before counting. */
  function countStyleRules(sheet) {
    var n = 0;
    try {
      var rules = sheet && sheet.cssRules;
      if (!rules) return 0;
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        // Grouping rules have no selectorText but do have cssRules
        if (r.selectorText === undefined && r.cssRules) {
          n += countStyleRules({ cssRules: r.cssRules });
        } else if (r.selectorText !== undefined) {
          n += 1;
        }
      }
    } catch (e) { /* cross-origin or no access */ }
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

  /** A missing stylesheet is reported loudly only once, so polling does not spam the console. */
  var warnedNoCss = false;
  function warnIfCssMissing() {
    if (warnedNoCss) return;
    var info = cssSheetInfo();
    if (info.loaded) return;
    warnedNoCss = true;
    try {
      console.warn('[theme-studio] Stylesheet neko-theme-studio.css is not loaded: CSS variables were injected, ' +
        'but no rule uses them, so nothing will change visually.' +
        '\n  Most likely the template patch was not applied (install.ps1 §C), or /static/css/neko-theme-studio.css was not copied into the static directory.' +
        '\n  To check: run NekoThemeStudio.diagnose() in the app window and look at cssLoaded.');
    } catch (e) { /* ignore */ }
  }

  /**
   * Stylesheet freshness self-healing (1.4.0).
   *
   * The template's <link href="/static/css/neko-theme-studio.css?v=<asset>-<css sha8>">
   * changes with every deployment, but **already-open windows** do not reload the
   * page: the HTTP header is max-age=3600 and the renderer caches another copy. So
   * after a restart with updated CSS the user still sees old rules; this is the
   * systemic cause of "changed a colour in the panel, the app did not react" and
   * even "old bugs keep happening" (the old version hung the AI text colour on the
   * unscoped .message-block-*, and even after deploying the new CSS the window
   * still ran the old sheet, so colours kept bleeding).
   *
   * Approach: fetch the current file from the server with cache:'reload', parse it
   * into a constructed stylesheet, and compare a "rule count + rule text length"
   * signature against the sheet active on the page; a mismatch means the page's
   * sheet is old -> swap the link href for a URL with a new query string to force a
   * reload. Nothing happens when signatures match (zero extra cost on a normal
   * load, no flicker). Any failing step degrades silently.
   */
  // Self-healing cannot run only once. This function used to be guarded as
  // one-shot by `sheetChecked`: swap if mismatched at startup, never look again.
  // But already-open windows are the most common victims: the plugin gets updated
  // while the window is open, the page's sheet stays old forever, yet the 1500ms
  // palette poll keeps running and variables keep updating, so the panel shows
  // success and the window does not change. Now it is re-checked from the poll,
  // throttled by SHEET_CHECK_MS.
  var SHEET_CHECK_MS = 15000;
  var lastSheetCheckAt = 0;
  var sheetSwapCount = 0;
  function sheetSignature(sheet) {
    try {
      var rules = sheet.cssRules || [];
      var total = 0;
      for (var i = 0; i < rules.length; i++) total += (rules[i].cssText || '').length;
      // Signature = "real style rule count + total rule text length": counting
      // only the top level, wrapping the whole sheet in @layer makes the count
      // always 1, leaving only the text-length half of the signature and dulling
      // swap detection. countStyleRules descends into grouping rules, so the
      // signature is comparable whether or not a layer wraps the sheet.
      return countStyleRules(sheet) + ':' + total;
    } catch (e) { return null; }
  }

  function refreshStaleStylesheet(force) {    // Throttle: re-check at most every 15s from the poll, not a fetch on every heartbeat.
    // force=true (at startup) skips the throttle.
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

    // v0.7.0: check the fingerprint from theme.json first. The page href already
    // carries -<css8> (written by the template patch), so comparing it with the
    // on-disk fingerprint tells whether to act: equal -> return with zero download;
    // different -> only swap the hash segment so the browser fetches the new file at
    // the new URL. Only when the page href has no hash segment (old template) does it
    // fall back to the download + signature comparison below.
    if (assetMeta.css8) {
      var pageHash = readSheetHash(href);
      if (pageHash === assetMeta.css8) return;
      if (pageHash) {
        var swapped = href.replace(/-[0-9a-f]{8}(?=[?&#]|$)/, '-' + assetMeta.css8);
        if (swapped !== href) {
          sheetSwapCount += 1;
          link.setAttribute('href', swapped);
          try {
            console.warn('[theme-studio] Stylesheet is not the current version (page ' + pageHash + ' -> disk ' +
              assetMeta.css8 + '), swapped (swap #' + sheetSwapCount + ').');
          } catch (e5) { /* ignore */ }
          return;
        }
      }
    }

    // Find the sheet already active on the page (any href containing the file name).
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

    // Constructed stylesheets: supported in Chromium 73+. If parsing fails, give up
    // self-healing; never affect existing styles.
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
      // The server's sheet differs from the one active on the page (the page is
      // old) -> swap. Strip the nts-refresh param left by the previous swap before
      // appending, otherwise the URL grows with every swap.
      var base = abs.replace(/([?&])nts-refresh=\d+/g, '').replace(/[?&]$/, '');
      var sep = base.indexOf('?') === -1 ? '?' : '&';
      var busted = base + sep + 'nts-refresh=' + Date.now();
      sheetSwapCount += 1;
      link.setAttribute('href', busted);
      try {
        console.warn('[theme-studio] neko-theme-studio.css on the page differs from the server copy (updated after the window opened), ' +
          'forced a swap (swap #' + sheetSwapCount + ').');
      } catch (e4) { /* ignore */ }
    })['catch'](function () { /* degrade silently */ });
  }

  /**
   * Is the loader on disk newer than this instance? If so, reload the page so the
   * new script takes over.
   *
   * Why reload instead of hot-swapping: this script is an already-executed IIFE,
   * and the NON_COLOR_VARS whitelist, regexes and functions in its closure are
   * frozen. After a new length-type variable is added, the old instance rejects it
   * as a colour (`log('reject value', ...)` is only visible under DEBUG), the gate
   * attribute is never set, so "the panel shows applied, the window does not
   * change". This is the root cause of the recurring "changes do not take effect",
   * and the CSS side cannot fix it: gate attributes are written by this script, and
   * an old script that does not know a new variable will never write them.
   *
   * Safety valve: sessionStorage records "the version running when the reload was
   * triggered". After a successful reload this instance's SCRIPT_VERSION is the new
   * version and no longer equals the record, so the next heartbeat compares as
   * usual; this makes several consecutive upgrades possible (1.4.2 -> 1.5.0 ->
   * 1.5.2 -> ...). They are only equal when "reloaded but got the same old script
   * back" (HTTP cache not bypassed), and then it stops to avoid endless reloads.
   * Also, two attempts are at least SELF_UPGRADE_MIN_INTERVAL_MS apart.
   */
  // ------------------------------------------------- Asset fingerprints (v0.7.0)
  // theme.json now carries css_sha8 / loader_version (on-disk fingerprints from the
  // backend _asset_meta()). With them, the two self-healing actions no longer need
  // "download the whole file, then decide":
  //   downloading 158KB of CSS every 15s just to compare a signature, and 126KB of
  //   JS every 60s just to read one version line, is about 45MB per window per
  //   hour, wasting laptop battery. When the fields are missing (older plugin) the
  //   code below falls back to the old download path unchanged.
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

  // All the "yield" checks and the guard for upgrade reloads (extracted from
  // selfUpgradeIfNeeded with logic unchanged; both paths must obey them: do not
  // wipe drafts, yield to streaming output, persist the guard last).
  // Built into the app, the loader is on 23 pages, many of them editors (emotion
  // mapping, character cards, memory browser...) whose unsaved state the loader
  // cannot know. Only the long-lived chat / overlay windows auto-reload: they may
  // stay open across a backend upgrade, and their "draft / streaming" yield checks
  // are below. Other pages do not auto-reload; the template's ?v= naturally picks
  // up the new loader next time they open.
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
      // Must not wipe the draft the user is typing: if the input has content, skip
      // the reload and retry on the next heartbeat (throttled to 60s). Likewise
      // yield while a reply is streaming, so it is not interrupted.
      try {
        // Check every composer: the templates put the (often empty) legacy
        // #textInputBox before the React root, so querySelector alone would
        // return it and miss a draft in the React composer.
        var inputs = document.querySelectorAll('textarea.composer-input, .composer-input[contenteditable="true"], #textInputBox');
        for (var ii = 0; ii < inputs.length; ii += 1) {
          var box = inputs[ii];
          var text = box.value != null ? box.value : box.textContent;
          if (text && String(text).trim()) {
            log('self-upgrade deferred: composer has draft text');
            return;
          }
        }
        if (document.querySelector('.message-block-status, [data-streaming="true"]')) {
          log('self-upgrade deferred: a reply is in flight');
          return;
        }
      } catch (e8) { /* ignore */ }
      // Store "the version we are leaving + now": after the reload the new
      // instance's SCRIPT_VERSION becomes remote, which differs from the recorded
      // gVer, so the next round compares as usual (this is the fix for the 1.5.0
      // lockup); if the version did not change after the reload (cache not
      // bypassed), the cooldown is the fallback, so it never locks permanently.
      // Note: this line must come after all the "yield" checks. It used to be first,
      // so yielding for a draft or streaming output had already persisted the guard,
      // and no upgrade was attempted again within the 10-minute cooldown.
      try {
        window.sessionStorage.setItem(SELF_UPGRADE_GUARD_KEY, SCRIPT_VERSION + '@' + now);
      } catch (e5) { /* ignore */ }
      try {
        console.warn('[theme-studio] Loader upgrade detected: this window ' + SCRIPT_VERSION +
          ' -> ' + remote + ', reloading the page to take over (so new variables are not dropped by the old script).');
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
      // Same version + within cooldown = just reloaded but still got this script
      // (cache not bypassed), so stop for now. After the cooldown one more try is
      // allowed, so a single cache miss cannot lock upgrades permanently.
      if (gVer === SCRIPT_VERSION && (now - gAt) < SELF_UPGRADE_COOLDOWN_MS) return;
    }

    // v0.7.0: theme.json already carries loader_version: equal -> return with zero
    // download; different -> treat it as "new version ready" directly, no need to
    // download 126KB of JS to regex out one version line. When the field is missing
    // (older plugin) the download path below runs unchanged.
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
      // Add a timestamp to bypass the HTTP cache, otherwise the same old script may come back.
      url = url.replace(/([?&])nts-upgrade=\d+/g, '').replace(/[?&]$/, '');
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'nts-upgrade=' + now;
    } catch (e3) { return; }

    selfUpgradeAt = now;

    fetch(url, { cache: 'reload', credentials: 'same-origin' }).then(function (res) {
      if (!res || !res.ok) return null;
      return res.text();
    }).then(function (code) {
      if (!code) return;
      // Compare versions: only reload if the server script really changed,
      // otherwise the page would reload for nothing.
      var m = /SCRIPT_VERSION\s*=\s*'([^']+)'/.exec(code);
      var remote = m ? m[1] : '';
      if (!remote || remote === SCRIPT_VERSION) return;
      reloadForUpgrade(remote, now);
    })['catch'](function () { /* degrade silently: if unavailable, do not upgrade; same behaviour as before */ });
  }

  /**
   * Current appearance mode. The app's main window switches to dark via
   * html[data-theme="dark"] (resources/bin/static/theme-manager.js), and some pages
   * also add a .dark class; both are recognised. If neither is present, fall back
   * to the system colour scheme.
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
   * "The current mode's group is empty while the other group has values": the most
   * common "panel shows applied, the app did not change at all". Light and dark
   * groups are independent (a settled requirement); the user configured only light
   * while the app runs in dark, so variables are injected and gates set, but no rule
   * matches in the visible area. Each combination is warned once; switching
   * light/dark re-checks.
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
    var zh = (mode === 'dark') ? 'dark' : 'light';
    var otherZh = (mode === 'dark') ? 'light' : 'dark';
    try {
      console.warn('[theme-studio] The app is in ' + zh + ' mode, but the palette\'s ' + zh + ' group is empty' +
        ' (the ' + otherZh + ' group has ' + other + ' entries). The groups are independent; configuring one does not change the other\'s appearance. ' +
        'Switch to the "' + zh + '" tab in the Theme Studio panel and configure it too, or switch the app to ' + otherZh + ' mode.');
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
  // ------------------------------------------- Advanced material layer (v1.6.0)
  //
  // Material variables are generated by the backend materials.compile_materials():
  // whitelisted parameters, clamped ranges, normalised enums, all with the
  // --nts-mt- prefix. Only a shape check is done here (prefix / length / no
  // semicolons, braces or comments); sanitizeVars is not re-run, since it validates
  // with "palette colour" semantics and would reject gradients, multi-layer
  // shorthands and the keyword none one by one, losing the material entirely.
  var MT_VAR_PREFIX = '--nts-mt-';
  var MT_ATTR = 'data-nts-mt';
  var MT_QUALITY_ATTR = 'data-nts-mt-quality';
  var MT_PRESET_ATTR = 'data-nts-mt-preset';
  var MT_MAX_VALUE = 400;
  var mtPointerBound = false;
  var mtFollow = 0;
  var mtDelay = 60;

  /** Value shape check: blocks CSS injection (semicolons / braces / angle brackets / comment openers). */
  function mtValueOk(v) {
    if (typeof v !== 'string') return false;
    if (!v || v.length > MT_MAX_VALUE) return false;
    if (/[;{}<>]/.test(v)) return false;
    if (v.indexOf(String.fromCharCode(47, 42)) !== -1) return false;
    return true;
  }

  /** Get one material variable map (mode = light | dark); invalid entries are silently dropped. */
  function materialVarsOf(p, mode) {
    var out = {};
    var src = p && p.material_vars;
    if (!src || typeof src !== 'object') return out;
    var map = src[mode];
    if (!map || typeof map !== 'object') return out;
    for (var k in map) {
      if (!Object.prototype.hasOwnProperty.call(map, k)) continue;
      // Variable names are spliced directly into CSS text: require the full
      // --nts-mt-* shape, not just the prefix.
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

  /** Remove material gate attributes and pointer variables (= materials fully inactive). */
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
   * Formerly "make the chat window inner root step aside": the palette layer gives
   * #react-chat-window-root a solid colour with !important, which unlayered
   * material rules could not beat, so an inline !important was written. Since
   * 1.6.45 the material rules live in @layer nts-theme.materials and reliably beat
   * the palette layer (MATERIAL_CSS has a dedicated #react-chat-window-root rule,
   * and compact mode has a matching transparency rule); the inline override would
   * instead beat the transparent outer layer of compact /chat and leave an extra
   * patch of base colour behind the capsule.
   * So now this only cleans up: removes inline values older versions may have left,
   * never writes. The parameter is kept for call-site compatibility.
   */
  function applyRootBypass(_on) {
    var el = document.querySelector ? document.querySelector('#react-chat-window-root') : null;
    if (!el || !el.style || !el.style.removeProperty) return;
    if (el.style.getPropertyValue && el.style.getPropertyValue('background-color').indexOf('--nts-mt-bg') !== -1) {
      el.style.removeProperty('background-color');
    }
  }

  /**
   * Mirror material_attrs onto documentElement.
   * hasVars false means materials are off / have no variables; the attributes must
   * then be removed so MATERIAL_CSS matches nothing.
   * @param {Object<string,string>} attrs keys are attribute names (data-nts-mt / -quality / -preset)
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
    // The two pointer light-tracking parameters live in variables; read and cache
    // them once so each pointermove does not trigger a recalc.
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
   * Pointer light tracking: write the mouse position into --nts-mt-mx / -my
   * (percentages). Only written when mouse-follow strength > 0, throttled by
   * --nts-mt-mouse-delay, so floods of pointermove do not drag the main thread
   * (rAF coalesces to the last frame).
   */
  function initMaterialPointer() {
    if (mtPointerBound) return;
    mtPointerBound = true;
    var timer = 0;
    var lastEv = null;
    function onMove(moveEv) {
      if (!document.documentElement.hasAttribute(MT_ATTR)) return;
      if (mtFollow <= 0) return;
      // Throttle, not debounce: the old clearTimeout-per-move restarted the delay on every
      // event, so a continuously moving pointer never updated the light until it stopped.
      // Keep the latest event and let one pending timer write it.
      lastEv = moveEv;
      if (timer) return;
      timer = window.setTimeout(function () {
        timer = 0;
        var ev = lastEv;
        var de = document.documentElement;
        if (!ev || !de.hasAttribute(MT_ATTR)) return;
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
        // Light direction: from the panel centre toward the pointer; the sheen and
        // edge band angles rotate with it, so the glass feels directionally "lit"
        // (instead of a dead highlight fixed at 162deg).
        var ang = Math.atan2(cy - 50, cx - 50) * 180 / Math.PI;
        if (ang < 0) ang += 360;
        set('--nts-mt-light-angle', ang.toFixed(1) + 'deg');
      }, mtDelay);
    }
    window.addEventListener('pointermove', onMove, { passive: true });
  }

  /**
   * Apply a palette. Idempotent: the DOM is not written if content is unchanged.
   * @param {{vars?:Object,dark_vars?:Object,enabled?:boolean,name?:string,updated_at?:string}} p
   */
  /* ---------------------------------------------------------------- Diagnostic reporting
   * "Was the material really injected into the page?" can only be inferred from
   * outside the host, and inference is not evidence. This sends the state the
   * loader actually observed back to the plugin entry loader_report, persisted as
   * static/diagnostics.json: rule count / gate attributes / variable count /
   * backplate capability / pointer binding. At most once a minute, failures fully
   * silent: diagnostics must never affect the theme itself. */
  var reportAt = 0;
  // The write endpoint is local-only: once a remote / reverse-proxied window gets
  // 401/403 this session stops reporting, otherwise every retry would log an
  // unauthorised-access warning on the server.
  var reportBlocked = false;
  function reportRuntime(force) {
    try {
      if (reportBlocked) return;
      var now = Date.now();
      if (!force && reportAt && now - reportAt < 60000) return;
      // The throttle window is only consumed **after a successful write to disk**
      // (see then/catch below): during initialisation the service is often ready
      // later than the page, so the first attempt always fails; the old code spent
      // the window on that failure and the session never got a second report.

      // v1.6.23: reporting before any theme was actually applied only writes a
      // "css_len=0 / focus_rules=0" snapshot, which the self-check reads as a false
      // red (most likely in the few seconds the service restarts after install).
      // The criterion is lastApply.at: normal apply, empty palette and explicit
      // disable all write it, so "nothing applied" is not reported while "applied"
      // can still be proven.
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
      if (apply.mt > 0 && !attrs['data-nts-mt']) issues.push('material variables injected but the gate attribute is missing');
      if (attrs['data-nts-mt'] && rules <= 0) issues.push('gate attribute present but the style rule count is 0');
      if (!backdrop) issues.push('backdrop-filter is not supported in this environment; materials use the fallback background');
      // Value-level proof: counting "how many variables were injected" cannot prove
      // the values in the window are right (a stale mirror, missing gates or
      // overridden variables all keep the count positive). Sampling computed style
      // shows at a glance whether rim / refraction lines / dispersion / drift landed.
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
      // Whether per-component material really reaches the element: component
      // aliases are only inherited when the component selector matches, so a value
      // on the global variable does not mean the component override is in effect.
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
      } catch (e4) { /* sampling failure does not affect the report */ }
      // v1.6.24: element-level proof: proving "the variables are on root" is not
      // enough. When the user says "nothing changed", we must be able to answer
      // "what background-color / backdrop-filter did this element end up computing".
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
      // v1.6.28: who is covering the glass: sample the matching rules and own
      // styles of the chat window's inner element.
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
      // v1.6.25: per-layer sampling. The chat window's visible glass may be on
      // .chat-window or .compact-chat-surface-frame rather than
      // #react-chat-window-shell; also count CSSOM rules matching the element to
      // tell "rule never made it into the sheet" from "it did but lost".
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
      // Built into the app: the report is POSTed straight to the app's own endpoint
      // (same-origin, no CSRF token needed, no more /runs run_id polling).
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
        // Retry 15s after a failure (not on the next 1.5s tick): do not hit the
        // service every tick while it is not ready.
        reportAt = now - 45000;
      });
    } catch (e) { /* ignore */ }
  }

  function applyPalette(p) {
    if (!p || typeof p !== 'object') return false;

    // Background image warm-up gate (v1.5.21): when the theme really changed / on
    // first apply, and new images are not ready yet, warm them before changing the
    // CSS, avoiding the blank flash of "old image gone, new image not arrived".
    // After warm-up this function is re-entered with the same palette (by then
    // warmedImages is populated).
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
            // v1.5.23: every warm-up has its own ticket. In the old implementation,
            // if p2 arrived before p1's warm-up finished, p1's callback applied
            // warmPending (now p2) directly, but none of p2's images were warmed,
            // so it still flashed white, just with a different image. A mismatched
            // ticket discards the whole callback.
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

    // Reaching here means p is applied directly. If an earlier palette is still
    // waiting on warm-up, p supersedes it: invalidate its ticket, otherwise when its
    // warm-up finishes the old palette would be applied again (even re-enabling a
    // theme that was just turned off).
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
    // Advanced materials (v1.6.0): materials are an independent subtree that
    // coexists with palette variables.
    var mtLight = materialVarsOf(p, 'light');
    var mtDark = materialVarsOf(p, 'dark');
    var nMt = countVars(mtLight) + countVars(mtDark);

    if (!nLight && !nDark && !nMt) {
      // Empty palette + no materials = restore the original look
      removeStyle();
      currentVersion = stampOf(p);
      lastApply = { light: 0, dark: 0, mt: 0, enabled: true, at: Date.now() };
      reportRuntime();
      return true;
    }

    var previousStamp = currentVersion;
    var css = '';
    var l = declBlock(light);
    // Light values must be scoped to "not dark", otherwise a light-only palette
    // leaks into dark mode (independent verifier counterexample CE-B: light values
    // in :root{} still applied in dark mode).
    if (l) css += LIGHT_SCOPE + '{' + l + '}';
    var d = declBlock(dark);
    if (d) css += ':root[data-theme="dark"]{' + d + '}';
    // Material variables are likewise split into light/dark blocks and share the
    // palette's <style>. The dark map is complete (the backend fills missing dark
    // values from light), so materials are never "half empty".
    var ml = declBlock(mtLight);
    if (ml) css += LIGHT_SCOPE + '{' + ml + '}';
    var md = declBlock(mtDark);
    if (md) css += ':root[data-theme="dark"]{' + md + '}';

    var el = styleEl();
    // The probe always goes first: it is only for the CSP self-check, takes part in
    // no gating and does not affect existing variables. The liquid-glass static
    // layer goes last: it is a constant string with each rule self-gated, written
    // in one textContent together with the palette variables (lastAppliedCss /
    // styleIntact / dedupe logic unchanged).
    // v1.6.0: the material layer (MATERIAL_CSS) is also a constant string, written
    // in the same single textContent as the glass layer.
    css = PROBE_CSS + css + '\n' + GLASS_CSS + '\n' + MATERIAL_CSS;
    if (el.textContent !== css) el.textContent = css;
    lastAppliedCss = css;
    // Gate attributes must be synced before styles take effect (setAttribute and
    // <style> content changes in the same frame merge into one style recalc, so no
    // ungated state flashes).
    setGates(light, dark);
    // Material gate attributes: removed when nMt is 0 -> MATERIAL_CSS matches
    // nothing -> the app keeps its original look.
    syncMaterialAttrs(p.material_attrs, nMt > 0);
    // Only transition when the theme really changed (different stamp); no flash on
    // first apply or unchanged content.
    if (previousStamp && previousStamp !== stampOf(p)) flashThemeSwap();
    // Glass material layer: bind pointer specular highlight / scroll flow
    // (idempotent; only writes CSS variables, never changes declarations)
    initGlassPointer();
    initMaterialPointer();
    probeStyleApplied();
    warnIfCssMissing();
    warnIfModeUncovered(nLight, nDark);
    currentVersion = stampOf(p);
    lastApply = { light: nLight, dark: nDark, mt: nMt, enabled: true, at: Date.now() };
    // Force a self-check snapshot refresh after a successful apply: what debugging
    // needs most is the gate and injected-content state "after applying"; captured
    // only at startup, the snapshot would be empty and show nothing.
    lastSnapAt = 0;
    persistSnapshot();
    // Report immediately when the theme really changed (bypassing the 60s
    // throttle): the panel's "runtime status" relies on this report to judge
    // "has the window applied it", and reporting a minute late makes the panel
    // briefly claim the opposite of reality; the user sees "the UI is default but
    // the panel shows the previous theme".
    reportRuntime(!previousStamp || previousStamp !== stampOf(p));
    return true;
  }

  // ---------------------------------------------------------------- State

  var currentVersion = null;
  var lastFetchAt = 0;
  var timer = null;
  var inflight = false;
  /** Bumped whenever a palette arrives from outside the poll (another window via the shared
   *  cache). A poll response whose request started before that is stale: it must neither
   *  overwrite the newer palette nor write itself back to the shared cache. */
  var paletteGen = 0;
  /** Summary of the last successful apply (for diagnostics). */
  var lastApply = null;
  /** Raw text + time of the last fetch failure (for diagnostics). */
  var lastError = null;
  var lastErrorAt = 0;
  /** Error already reported loudly to the console, so the 1.5s poll does not spam. */
  var lastWarnedError = null;
  /** Raw text of the last successfully applied palette, to detect "same version, changed content". */
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

  // ------------------------------------------------- Failure visibility (formerly the "service self-healing" section)
  //
  // In the plugin era this was a whole chain for waking the plugin service
  // (csrf-token -> /plugins/refresh -> /plugin/<id>/start), because the loader ran
  // in the app window while the palette lived on a plugin service on another port,
  // and when the plugin was not running only an external kick could revive it.
  // Built into the app, the palette is the app's own route: if the backend is up
  // the endpoint is up; if not, the page itself cannot load. So only the failure
  // counters and diagnostic fields are kept, for the panel's "self-check" card.
  // The plugin era's two "has the user disabled this plugin" probes
  // (/ui/disabled.json and the /plugins registry) are meaningless once built in:
  // Theme Studio is not a disableable plugin and the app has no plugin registry.
  var reviveAt = 0;
  var reviveCount = 0;
  var reviving = false;
  var lastReviveError = null;
  // Current self-healing backoff interval (v0.1.11): starts at 1.2s, x2 per
  // failure, capped at 30s; reset on success.
  var reviveDelay = REVIVE_BASE_DELAY_MS;

  /**
   * Failure classification (kept for diagnostics and logs): a network failure means
   * the backend did not respond; a non-network failure means data arrived but was
   * invalid (broken JSON, contract mismatch). Built into the app, both only affect
   * log wording.
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

  /** Self-healing precondition (old name kept): only network failures count. */
  function shouldRevive(msg) {
    return isNetworkFailure(msg);
  }

  /**
   * Name kept for all old call sites (fetchPalette's failure branch, API.revive).
   * Built into the app there is no plugin service to wake; polling itself is the
   * retry mechanism.
   * @param {string} msg
   */
  function reviveService(msg) { /* no-op: built into the app, no plugin host to wake */ }

  /**
   * fetch with a timeout (v0.1.11 P0).
   * Native fetch has no timeout: when the endpoint hangs (connection established
   * but no response ever) the Promise stays pending, the caller's inflight lock is
   * never released, and the 1.5s poll is stuck forever. Symptom: "changed a colour
   * in the panel, the window never updates, the console is silent". AbortController
   * aborts after 8s and the abort is normalised into a 'timeout <ms>' error so it is
   * handled as a "network failure" with self-healing backoff (distinct from JSON
   * parse / contract errors, which should not restart the plugin service).
   * @param {string} url
   * @param {Object} opts fetch options
   * @param {number} timeoutMs
   * @returns {Promise<Response>}
   */
  function fetchWithTimeout(url, opts, timeoutMs) {
    var ms = timeoutMs || FETCH_TIMEOUT_MS;
    // Very old hosts lack AbortController: fall back to plain fetch (better than
    // not sending a request at all).
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
    var onError = function (err) {
      clearTimer();
      if (timedOut) {
        var te = new Error('timeout ' + ms + 'ms');
        te.name = 'TimeoutError';
        throw te;
      }
      throw err;
    };
    return p.then(function (res) {
      // The deadline also covers reading the body: headers can arrive and the body then
      // stall, which would keep the poll's inflight lock held. Aborting rejects a pending
      // body read, so keep the timer armed until json()/text() settles. A body that is
      // never read is simply aborted when the timer fires, which is harmless.
      if (res && typeof res.json === 'function') {
        ['json', 'text'].forEach(function (method) {
          var original = res[method];
          if (typeof original !== 'function') return;
          res[method] = function () {
            return original.apply(res, arguments).then(function (value) {
              clearTimer();
              return value;
            }, onError);
          };
        });
      } else {
        clearTimer();
      }
      return res;
    }, onError);
  }

  /** Contract errors (data arrived but invalid) are reported loudly once per reason; never trigger a self-healing restart. */
  var warnedContract = Object.create(null);
  function warnContractOnce(reason) {
    lastError = 'contract: ' + reason;
    lastErrorAt = Date.now();
    if (warnedContract[reason]) return;
    warnedContract[reason] = 1;
    try {
      console.warn('[theme-studio] The returned palette is invalid (' + reason + '); ignored, the service will not be restarted.' +
        '\n  Endpoint: ' + themeUrl() +
        '\n  Most likely theme.json was broken by a manual edit, or the backend/loader versions do not match.');
    } catch (e) { /* ignore */ }
  }

  // Backoff on consecutive failures: while the main service restarts / runs in a
  // restricted startup mode / the endpoint is unavailable, windows should not each
  // send a request every 1.5s. The first 3 retries are normal (covering brief
  // blips), then backoff doubles up to 60s; any success restores the 1.5s poll.
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

  // When the user returns to the window, do not keep waiting out a backoff of up
  // to 60s: the service has most likely finished restarting.
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
    // Built in, this is a same-origin request: remote / Docker / reverse-proxied
    // windows must send the instance-access session cookie, otherwise
    // InstanceAccessMiddleware returns 401 before routing.
    var opts = { cache: 'no-store', credentials: 'same-origin' };
    // v0.1.11 P0: release inflight at the end of the chain whether it succeeded,
    // got invalid data, failed on the network or timed out. The old code only reset
    // it in then/catch, so a hung endpoint (fetch pending forever) stalled polling
    // permanently.
    var release = function () { inflight = false; };
    var gen = paletteGen;
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
      // Data arrived = service alive: reset the self-healing budget as a success
      // first (so it can recover again if it really goes down next time).
      lastError = null;
      lastWarnedError = null;
      reviveCount = 0;
      reviveAt = 0;
      reviveDelay = REVIVE_BASE_DELAY_MS;
      // ---- Contract validation (v0.1.11): data arrived but invalid -> no self-healing ----
      // Restarting the plugin service only fixes "service missing", not "the
      // returned JSON is broken".
      if (!p || typeof p !== 'object' ||
          Object.prototype.toString.call(p) !== '[object Object]') {
        warnContractOnce('theme.json is not a JSON object');
        return;
      }
      if (p.schema !== undefined && p.schema !== 1) {
        warnContractOnce('theme.json schema=' + String(p.schema) + ' (this loader only accepts 1)');
        return;
      }
      // A newer palette arrived from another window while this request was in flight:
      // this response may predate it. Drop it (no apply, no cache write-back, which would
      // also bounce the old palette to every other window); the next poll re-reads.
      if (gen !== paletteGen) {
        log('drop stale poll response (a newer palette arrived meanwhile)');
        return;
      }
      rememberAssetMeta(p);
      var raw = '';
      try { raw = JSON.stringify(p); } catch (e) { raw = ''; }
      // theme.json has no version field (since v0.1.5): raw is the exact text of the
      // JSON the server returned, and identical text means "unchanged". This is
      // stricter than a version number: hand-editing theme.json or resetting to the
      // same content are both reflected accurately.
      if (!force && raw === lastRaw) {
        // v0.1.11 P1: unchanged raw does not mean the theme is still there. Host
        // template re-renders / DOM cleanup / re-applied CSP can all remove the
        // injected <style>; the old code only looked at raw, so a missing node was
        // never rebuilt, showing as "the theme vanished from the window entirely
        // until the page was refreshed".
        if (!styleIntact()) {
          log('injected style node missing, rebuilding');
          applyPalette(p);
          lastRaw = raw;
        }
        return; // unchanged
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
      // Polling runs every 1.5s; the same error is reported loudly only once, then
      // goes to the debug log.
      if (msg !== lastWarnedError) {
        lastWarnedError = msg;
        try {
          console.warn('[theme-studio] Failed to fetch the palette (keeping the cache; no new theme will be applied in this window): ' + msg +
            '\n  Endpoint: ' + url +
            '\n  The endpoint is served by the app itself; failing to connect usually means the main service just restarted or the page was closed.');
        } catch (e) { /* ignore */ }
      }
      log('fetch failed (keeping cache)', msg);
      // Network failure = the app's main service did not respond; non-network =
      // data arrived but was invalid. Both are only recorded; the next 1.5s poll
      // retries naturally.
      if (isNetworkFailure(msg)) log('network failure, will retry on next poll:', msg);
      else log('non-network failure:', msg);
    }).then(release, release);
  }

  function startPolling() {
    stopPolling();
    timer = window.setInterval(function () {
      if (document.visibilityState === 'hidden') return;
      // Every heartbeat also re-checks stylesheet freshness (throttled internally
      // to 15s). So when the user changes colours in the panel, even a window
      // opened before an update switches to the new rule sheet within seconds,
      // without restarting the app.
      refreshStaleStylesheet(false);
      // Also check whether the loader itself has been updated (throttled
      // internally to 60s + once per session).
      selfUpgradeIfNeeded();
      fetchPalette(false);
      // Periodically persist this window's self-check state for external
      // debugging (throttled internally to 60s).
      try { if (window.NekoThemeStudio && window.NekoThemeStudio.report) window.NekoThemeStudio.report(); } catch (e9) { /* ignore */ }
    }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (timer !== null) { window.clearInterval(timer); timer = null; }
  }

  // ---------------------------------------------------------------- Bootstrap

  // 1) Render from the cache right away to avoid startup flicker.
  restoreFromCache();

  // 2) Then ask the service for the latest version.
  function boot() {
    // One "I'm alive" line, visible at a glance in the console when checking
    // "did the app load the script at all".
    try {
      console.info('[theme-studio] loader v' + SCRIPT_VERSION + ' loaded, endpoint ' + themeUrl());
    } catch (e) { /* ignore */ }

    // First make sure the stylesheet is current (otherwise the rules are old and
    // even correct injection has no effect). Forced once at startup, ignoring the
    // throttle.
    refreshStaleStylesheet(true);

    fetchPalette(true);
    startPolling();
    // Also check the loader version at startup: the user may not have restarted
    // the app for days, so the window may run a script several versions old that
    // silently drops every new variable.
    selfUpgradeIfNeeded();

    // Re-check "is the current mode's group empty" when the app switches
    // light/dark (html[data-theme]); otherwise a user who configures light and
    // then switches to dark never sees that hint.
    try {
      if (window.MutationObserver) {
        var mo = new window.MutationObserver(function () {
          if (lastApply) warnIfModeUncovered(lastApply.light, lastApply.dark);
        });
        mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
      }
    } catch (e) { /* ignore */ }

    // Window regains focus / becomes visible -> sync immediately (the user just
    // changed colours in the panel)
    window.addEventListener('focus', function () { fetchPalette(false); }, false);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') fetchPalette(false);
    }, false);

    // Another same-origin window changed the cache -> follow it (multi-window app)
    window.addEventListener('storage', function (ev) {
      if (!ev || ev.key !== CACHE_KEY || !ev.newValue) return;
      try {
        var p = JSON.parse(ev.newValue);
        if (!p || p.schema !== 1) return;
        // Same equality rule as fetchPalette: compare raw JSON text only.
        var raw = '';
        try { raw = JSON.stringify(p); } catch (e0) { raw = ''; }
        if (raw === lastRaw) return;
        paletteGen += 1; // invalidate a poll still in flight (see fetchPalette)
        applyPalette(p);
        lastRaw = raw;
      } catch (e) { /* ignore */ }
    }, false);

    // Variables need no recompute after a dark-mode switch (CSS selectors handle
    // it), but expose a hook for external code
    window.addEventListener('neko-theme-changed', function () { /* no-op, reserved for future extensions */ }, false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, false);
  } else {
    boot();
  }

  // ---------------------------------------------------------------- Public API

  window.NekoThemeStudio = {
    /** Refetch the palette immediately */
    refresh: function () { fetchPalette(true); },
    /** Apply a palette object directly (for debugging / manual use) */
    apply: applyPalette,
    /** Clear the variables injected in this window (back to the app's native look; the next poll re-applies automatically) */
    clear: function () {
      removeStyle();
      // Without clearing the content equality key, polling would conclude
      // "unchanged = no need to re-inject" and the palette would never come back
      // after clear() (only the debug API hits this, but it is well hidden).
      currentVersion = '';
      lastRaw = null;
      lastApply = null;
    },
    /** updated_at stamp of the current palette (theme.json has no version field since v0.1.5) */
    get updatedAt() { return currentVersion; },
    /** The loader script's own version (for spotting deployment drift) */
    scriptVersion: SCRIPT_VERSION,
    /** API base (built into the app, fixed to /api/theme_studio on the current origin) */
    get baseUrl() { return API_BASE; },
    /** Kept for compatibility: built into the app the API base is fixed; external overrides are ignored. */
    setBaseUrl: function (u) { /* no-op */ },
    /** Enable debug logging */
    setDebug: function (on) { DEBUG = !!on; },
    /** Kept for compatibility: in the plugin era this woke the plugin service; built in, there is no host to wake. */
    revive: function () {
      reviveCount = 0;
      reviveAt = 0;
      reviveDelay = REVIVE_BASE_DELAY_MS;
    },
    /**
     * Self-check: packs the runtime state of the "loader -> app API -> CSS" chain
     * into one object. Run `NekoThemeStudio.diagnose()` in the app window's DevTools.
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
      // Whether the stylesheet is really loaded: without the CSS, gate attributes
      // are still set but no rule matches -> variables resolve, elements look the
      // same (easily misread as "the plugin does nothing").
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
        // v0.1.11 self-check items: whether the injected node is still alive
        // (styleAlive=false when the host removed it), whether injected styles
        // actually apply (cspBlocked=true when blocked by CSP), and variable names
        // rejected by value validation; for "the panel saves, the loader drops",
        // check rejectedVars first.
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
        // Stylesheet self-healing state: sheetSwapCount > 0 means this window has
        // swapped sheets (typically an update while the window was open). Check
        // these two numbers first for "changed in the panel, the window did not move".
        sheetSwapCount: sheetSwapCount,
        lastSheetCheckAgoMs: lastSheetCheckAt ? Date.now() - lastSheetCheckAt : null,
        selfUpgradeAt: selfUpgradeAt || null,
        selfUpgradeGuard: (function () {
          try { return window.sessionStorage.getItem(SELF_UPGRADE_GUARD_KEY) || ''; }
          catch (e) { return ''; }
        })(),
        // v1.5.19 pointer lighting self-check: panels is the number of surfaces
        // currently carrying lighting, seen=false means no pointermove received yet;
        // sampleSpecX/Y is the first surface's specular position and should change
        // as the mouse moves (check these three first for "mouse moved, nothing happened").
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
      // v0.1.11 new value kinds: exposed for self-tests/debugging (NekoThemeStudio._internals.isSafeFilterChain etc.).
      isSafeFilterChain: isSafeFilterChain,
      isSafeGradient: isSafeGradient,
      // v1.5.19 pointer lighting: expose rebuild/repaint entry points for
      // self-tests and on-device regression scripts.
      refreshGlassPanels: refreshGlassPanels,
      paintGlassSpecular: paintGlassSpecular,
      glassSurfaces: function () { return glassPanels.length; }
    }
  };

  log('runtime ready; endpoint =', themeUrl());

  // ------------------------------------------------- Persisted self-check snapshot (readable remotely)

  // The app's chat window is an Electron window with no CDP port open, so its state
  // cannot be inspected from outside, and users may not want to (or know how to)
  // open the console. But Electron persists that same localStorage to
  // `Local Storage\leveldb`, so diagnose() snapshots are written there
  // periodically; when debugging "the panel shows applied, the window does not
  // change", the window's real gate attributes, stylesheet href and injected
  // content length can be read straight from disk instead of guessed.
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
        // Elements at the button's centre point (top to bottom), showing at a
        // glance whether another layer covers it
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
      // Issues like "the button right of the input is invisible" can only be
      // answered by the real window: write the composer geometry, the round
      // button's existence and its stacking into the snapshot, read later from leveldb.
      try {
        d.composer = composerSnapshot();
      } catch (e3) { d.composer = null; }
      d.at = new Date().toISOString();
      window.localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(d));
    } catch (e) { /* ignore */ }
  }
  window.NekoThemeStudio.report = persistSnapshot;
  persistSnapshot();

  // Independent scheduling for diagnostic reports (v1.6.2): applyPalette call
  // timing alone is not enough, since visibility changes and an unchanged theme
  // never re-enter applyPalette, leaving diagnostics empty. Report once 5 seconds
  // after startup (covering "the service is ready later than the page"), then
  // every minute while visible.
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
