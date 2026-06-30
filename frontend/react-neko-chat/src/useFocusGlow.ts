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

/**
 * Drive the Focus edge glow from the streamed charge. Sets, on `ref`'s element:
 *   --focus-glow            intensity 0..1 (the brightness the CSS scales)
 *   data-focus-glow="true"  while charge >= ONSET (glow visible)
 *   data-focus-breathing    while charge >= ENTER (breathing + the jump)
 * Updates via requestAnimationFrame WITHOUT React re-renders. The rAF loop idles
 * itself once the charge has fully decayed and restarts on the next push.
 */
export function useFocusGlow(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    let setpoint = 0; // last charge from the backend
    let atMs = 0; // its wall-clock stamp (ms)
    let raf = 0;

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

    const clear = (el: HTMLElement) => {
      el.style.removeProperty('--focus-glow');
      el.removeAttribute('data-focus-glow');
      el.removeAttribute('data-focus-breathing');
    };

    // Apply the glow for a live charge. Returns false once fully decayed (the
    // rAF loop can then idle). rAF-independent — also called synchronously on
    // each push so the glow is correct even when rAF is throttled (backgrounded
    // window), with rAF only smoothing the decay between pushes.
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
      el.style.setProperty('--focus-glow', intensity.toFixed(3));
      el.setAttribute('data-focus-glow', 'true');
      if (breathing) el.setAttribute('data-focus-breathing', 'true');
      else el.removeAttribute('data-focus-breathing');
      return true;
    };

    const tick = () => {
      const el = ref.current;
      const charge = liveCharge();
      if (!el) {
        // No element (transient mount/unmount): keep waiting only while there is
        // still charge to show; once it has decayed to 0, idle instead of
        // spinning rAF forever on a permanently-null ref.
        if (charge <= 0) {
          raf = 0;
          return;
        }
        raf = requestAnimationFrame(tick);
        return;
      }
      if (!render(el, charge)) {
        raf = 0; // fully decayed — stop until the next push
        return;
      }
      // Activated glow floors at the ENTER baseline (its breathing is a pure CSS
      // keyframe, not driven by rAF); once charge has decayed to that floor the
      // intensity is constant, so idle the loop instead of re-writing the same
      // --focus-glow every frame. onCharge() restarts it on the next push.
      // Sub-ENTER charges keep decaying toward 0 and idle via render() above.
      if (setpoint >= ENTER && charge <= ENTER) {
        raf = 0;
        return;
      }
      raf = requestAnimationFrame(tick);
    };

    const onCharge = (e: Event) => {
      const d = (e as CustomEvent<{ charge?: number; atMs?: number }>).detail || {};
      setpoint = Math.max(0, Math.min(CAP, Number(d.charge) || 0));
      atMs = Number(d.atMs) || Date.now();
      const el = ref.current;
      if (el) render(el, liveCharge()); // immediate, rAF-independent
      if (!raf) raf = requestAnimationFrame(tick); // restart an idled loop for the decay
    };

    window.addEventListener('neko-focus-charge', onCharge);
    if (ref.current) render(ref.current, liveCharge());
    raf = requestAnimationFrame(tick);
    return () => {
      window.removeEventListener('neko-focus-charge', onCharge);
      if (raf) cancelAnimationFrame(raf);
      const el = ref.current;
      if (el) clear(el);
    };
  }, [ref]);
}
