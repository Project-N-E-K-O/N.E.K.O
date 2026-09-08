import { useEffect, type RefObject } from 'react';

const IDLE_DELAY_MS = 1800;
const PLAY_STATE = '--compact-decoration-play-state';

/** Bound decorative animation to recent local input, including in Electron windows
 * whose backgroundThrottling:false keeps document.hidden false after hide(). */
export function useCompactDecorationActivity(
  ref: RefObject<HTMLElement | null>,
  enabled: boolean,
  portalRef?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const element = ref.current;
    if (!element || !enabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = false;
    let deadline = 0;

    const stop = () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      active = false;
      element.style.removeProperty(PLAY_STATE);
    };
    const settle = () => {
      timer = undefined;
      const remaining = deadline - Date.now();
      if (remaining > 0) timer = setTimeout(settle, remaining);
      else stop();
    };
    // Choice controls mount in a body portal. Read its current ref per event so
    // late mounts work without observing or waking on unrelated page input.
    const owns = (target: EventTarget | null) => target instanceof Node
      && (element.contains(target) || !!portalRef?.current?.contains(target));
    const activate = (event: Event) => {
      if (!owns(event.target)) return;
      if (document.hidden) return;
      deadline = Date.now() + IDLE_DELAY_MS;
      if (!active) {
        active = true;
        element.style.setProperty(PLAY_STATE, 'running');
      }
      // Renew a deadline, not a timeout for every high-frequency pointer event.
      if (timer === undefined) timer = setTimeout(settle, IDLE_DELAY_MS);
    };
    const onVisibilityChange = () => {
      if (document.hidden) stop();
    };
    const onPointerOut = (event: PointerEvent) => {
      if (owns(event.target) && !owns(event.relatedTarget)) stop();
    };
    const events = ['pointermove', 'pointerdown', 'keydown', 'input', 'wheel'] as const;
    for (const event of events) document.addEventListener(event, activate, { capture: true, passive: true });
    element.addEventListener('pointerleave', onPointerOut);
    document.addEventListener('pointerout', onPointerOut, true);
    window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      stop();
      for (const event of events) document.removeEventListener(event, activate, true);
      element.removeEventListener('pointerleave', onPointerOut);
      document.removeEventListener('pointerout', onPointerOut, true);
      window.removeEventListener('blur', stop);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [ref, enabled, portalRef]);
}
