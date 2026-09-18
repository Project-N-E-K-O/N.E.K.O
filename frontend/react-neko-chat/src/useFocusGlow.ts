import { useEffect } from 'react';
import type { RefObject } from 'react';

// Curve anchors — mirror config.FOCUS_CHARGE_* / FOCUS_TIME_DECAY_*. The backend
// streams the charge setpoint + wall-clock stamp on each turn (and on connect);
// we extrapolate the same piecewise time decay locally so the edge glow fades
// smoothly between sparse pushes instead of stepping.
const ONSET = 0.3; // FOCUS_CHARGE_EXIT — glow first appears
const ENTER = 0.6; // FOCUS_CHARGE_ENTER — "full activation": non-linear jump + breathing
const CAP = 1.0; // FOCUS_CHARGE_CAP
const DECAY = 0.02; // per second while charge < ENTER
const DECAY_ACTIVATED = 0.01; // per second while charge >= ENTER (slower → more persistent)

// The glow is a blurred box-shadow, so every visual update is a repaint. Drive it
// from a fixed-rate timer instead of rAF or a CSS keyframe: both of those follow
// the display refresh rate (120/144/260Hz) and rAF additionally stops whenever the
// compositor stops producing frames for the page. Chromium does not throttle
// timers of a visible page that merely lost focus (and the Electron chat windows
// run with backgroundThrottling disabled), so the breathing keeps its full rate
// while another window has focus.
export const FOCUS_GLOW_FPS = 30;
const FRAME_MS = 1000 / FOCUS_GLOW_FPS;
export const FOCUS_BREATH_PERIOD_MS = 3400;

// CSS `ease-in-out` = cubic-bezier(0.42, 0, 0.58, 1), solved for y at time x.
function easeInOut(x: number): number {
  let lo = 0;
  let hi = 1;
  let t = x;
  for (let i = 0; i < 16; i += 1) {
    const u = 1 - t;
    const bx = 3 * u * u * t * 0.42 + 3 * u * t * t * 0.58 + t * t * t;
    if (bx < x) lo = t;
    else hi = t;
    t = (lo + hi) / 2;
  }
  const u = 1 - t;
  return 3 * u * t * t + t * t * t;
}

// 0 at the trough, 1 at the peak. Matches the former 0% / 50% / 100% keyframes
// with ease-in-out on each half.
function breathAt(elapsedMs: number): number {
  const phase = (elapsedMs % FOCUS_BREATH_PERIOD_MS) / FOCUS_BREATH_PERIOD_MS;
  return easeInOut(phase < 0.5 ? phase * 2 : 2 - phase * 2);
}

/**
 * Drive the Focus edge glow from the streamed charge. Sets, on `ref`'s element:
 *   --focus-glow            intensity 0..1 (the brightness the CSS scales)
 *   --focus-breath          breathing phase 0..1 while breathing
 *   data-focus-glow="true"  while charge >= ONSET (glow visible)
 *   data-focus-breathing    while charge >= ENTER (breathing + the jump)
 * Updates at most FOCUS_GLOW_FPS times per second WITHOUT React re-renders. With
 * no charge there is no timer and no animated style at all; the loop restarts on
 * the next push.
 */
export function useFocusGlow(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    let setpoint = 0; // last charge from the backend
    let atMs = 0; // its wall-clock stamp (ms)
    let timer: ReturnType<typeof setTimeout> | null = null;
    let breathStartMs: number | null = null;
    const reducedMotion = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null;

    const liveCharge = (): number => {
      if (setpoint <= 0) return 0;
      if (!atMs) return setpoint;
      const rem = Math.max(0, (Date.now() - atMs) / 1000);
      // Floored at ENTER once activated: time decay can only bring charge >= ENTER
      // down to ENTER (a turn drops it below); below ENTER it bleeds to 0. Mirrors
      // backend _decay_charge_over_time.
      if (setpoint >= ENTER) return Math.max(ENTER, setpoint - DECAY_ACTIVATED * rem);
      return Math.max(0, setpoint - DECAY * rem);
    };

    // Skip identical writes so a settled value does not invalidate style.
    const setVar = (el: HTMLElement, name: string, value: string) => {
      if (el.style.getPropertyValue(name) !== value) el.style.setProperty(name, value);
    };

    const clear = (el: HTMLElement) => {
      breathStartMs = null;
      el.style.removeProperty('--focus-glow');
      el.style.removeProperty('--focus-breath');
      el.removeAttribute('data-focus-glow');
      el.removeAttribute('data-focus-breathing');
    };

    // Apply the glow for a live charge. Returns whether another frame is needed:
    // the charge is still decaying, or the activated glow is breathing.
    const render = (el: HTMLElement, charge: number): boolean => {
      if (charge < ONSET) {
        clear(el);
        return charge > 0; // keep ticking through the sub-onset fade to 0
      }
      const breathing = charge >= ENTER;
      // Below ENTER: rising 0.3→0.6 maps to 0→0.5 (sub-baseline, no breathing).
      // At/above ENTER: a non-linear step up to the 0.6 baseline, then 0.6→1.0
      // scales the breathing peak up to the cap.
      const intensity = breathing
        ? 0.6 + ((charge - ENTER) / (CAP - ENTER)) * 0.4
        : ((charge - ONSET) / (ENTER - ONSET)) * 0.5;
      setVar(el, '--focus-glow', intensity.toFixed(3));
      if (el.getAttribute('data-focus-glow') !== 'true') el.setAttribute('data-focus-glow', 'true');
      if (!breathing) {
        breathStartMs = null;
        el.style.removeProperty('--focus-breath');
        el.removeAttribute('data-focus-breathing');
        return true; // sub-ENTER charge always keeps decaying toward 0
      }
      if (el.getAttribute('data-focus-breathing') !== 'true') el.setAttribute('data-focus-breathing', 'true');
      const decaying = setpoint >= ENTER && charge > ENTER;
      if (reducedMotion?.matches) {
        breathStartMs = null;
        el.style.removeProperty('--focus-breath');
        return decaying;
      }
      const now = Date.now();
      if (breathStartMs === null) breathStartMs = now;
      setVar(el, '--focus-breath', breathAt(now - breathStartMs).toFixed(3));
      return true;
    };

    // React detaches the ref before this effect's cleanup runs, so remember the
    // element that was last styled in order to clear it on unmount.
    let styledEl: HTMLElement | null = null;

    const tick = () => {
      timer = null;
      const el = ref.current;
      if (el) styledEl = el;
      const charge = liveCharge();
      // No element (transient mount/unmount): keep waiting only while there is
      // still charge to show.
      const more = el ? render(el, charge) : charge > 0;
      if (more) timer = setTimeout(tick, FRAME_MS);
    };

    const wake = () => {
      if (timer !== null) return;
      tick();
    };

    const onCharge = (e: Event) => {
      const d = (e as CustomEvent<{ charge?: number; atMs?: number }>).detail || {};
      setpoint = Math.max(0, Math.min(CAP, Number(d.charge) || 0));
      atMs = Number(d.atMs) || Date.now();
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      tick(); // apply immediately, then keep the capped loop alive if needed
    };

    window.addEventListener('neko-focus-charge', onCharge);
    reducedMotion?.addEventListener?.('change', wake);
    tick();
    return () => {
      window.removeEventListener('neko-focus-charge', onCharge);
      reducedMotion?.removeEventListener?.('change', wake);
      if (timer !== null) clearTimeout(timer);
      timer = null;
      const el = ref.current ?? styledEl;
      if (el) clear(el);
    };
  }, [ref]);
}
