import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const POINTER_OPEN_DELAY_MS = 320;
const VIEWPORT_EDGE_GAP_PX = 8;
const TARGET_GAP_PX = 9;
const ANCHOR_MOVEMENT_EPSILON_PX = 0.5;

type TooltipPlacement = 'top' | 'bottom';
type TooltipVariant = 'default' | 'compact-tool';

interface TooltipTarget {
  anchor: HTMLElement;
  text: string;
  variant: TooltipVariant;
}

interface TooltipLayout {
  left: number;
  top: number;
  placement: TooltipPlacement;
  ready: boolean;
}

interface AnchorRectSnapshot {
  left: number;
  top: number;
  width: number;
  height: number;
}

function snapshotAnchorRect(anchor: HTMLElement): AnchorRectSnapshot {
  const rect = anchor.getBoundingClientRect();
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

function hasAnchorMoved(
  initialRect: AnchorRectSnapshot,
  currentRect: AnchorRectSnapshot,
): boolean {
  return (
    Math.abs(initialRect.left - currentRect.left) > ANCHOR_MOVEMENT_EPSILON_PX
    || Math.abs(initialRect.top - currentRect.top) > ANCHOR_MOVEMENT_EPSILON_PX
    || Math.abs(initialRect.width - currentRect.width) > ANCHOR_MOVEMENT_EPSILON_PX
    || Math.abs(initialRect.height - currentRect.height) > ANCHOR_MOVEMENT_EPSILON_PX
  );
}

function findTooltipAnchor(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element
    ? target.closest<HTMLElement>('[data-neko-tooltip]')
    : null;
}

function isInsideAnchor(anchor: HTMLElement, target: EventTarget | null): boolean {
  return target instanceof Node && anchor.contains(target);
}

export default function NekoTooltipLayer() {
  const tooltipId = useId();
  const [target, setTarget] = useState<TooltipTarget | null>(null);
  const [layout, setLayout] = useState<TooltipLayout>({
    left: -9999,
    top: -9999,
    placement: 'top',
    ready: false,
  });
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const openTimerRef = useRef<number | null>(null);
  const pendingAnchorRef = useRef<HTMLElement | null>(null);
  const anchorRectRef = useRef<AnchorRectSnapshot | null>(null);
  const pointerInteractionRef = useRef(false);

  const clearOpenTimer = useCallback(() => {
    if (openTimerRef.current !== null) {
      window.clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const hideTooltip = useCallback((anchor?: HTMLElement | null) => {
    if (anchor && pendingAnchorRef.current && pendingAnchorRef.current !== anchor) return;
    pendingAnchorRef.current = null;
    anchorRectRef.current = null;
    clearOpenTimer();
    setTarget(null);
  }, [clearOpenTimer]);

  const showTooltip = useCallback((anchor: HTMLElement, delay: number) => {
    const text = anchor.dataset.nekoTooltip?.trim();
    if (!text) return;
    const variant: TooltipVariant = anchor.dataset.nekoTooltipVariant === 'compact-tool'
      ? 'compact-tool'
      : 'default';

    pendingAnchorRef.current = anchor;
    clearOpenTimer();

    const reveal = () => {
      openTimerRef.current = null;
      if (pendingAnchorRef.current !== anchor || !anchor.isConnected) return;
      setTarget({ anchor, text, variant });
    };

    if (delay <= 0) {
      reveal();
      return;
    }
    openTimerRef.current = window.setTimeout(reveal, delay);
  }, [clearOpenTimer]);

  const updatePosition = useCallback(() => {
    if (!target || !tooltipRef.current || !target.anchor.isConnected) return;

    const anchorRect = target.anchor.getBoundingClientRect();
    const tooltipRect = tooltipRef.current.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const preferredPlacement = target.anchor.dataset.nekoTooltipPlacement;
    const hasTopSpace = anchorRect.top >= tooltipRect.height + TARGET_GAP_PX + VIEWPORT_EDGE_GAP_PX;
    const hasBottomSpace = viewportHeight - anchorRect.bottom
      >= tooltipRect.height + TARGET_GAP_PX + VIEWPORT_EDGE_GAP_PX;
    const placement: TooltipPlacement = preferredPlacement === 'top'
      ? hasTopSpace || !hasBottomSpace ? 'top' : 'bottom'
      : preferredPlacement === 'bottom'
        ? hasBottomSpace || !hasTopSpace ? 'bottom' : 'top'
        : !hasTopSpace && hasBottomSpace ? 'bottom' : 'top';
    const unclampedLeft = anchorRect.left + (anchorRect.width - tooltipRect.width) / 2;
    const maxLeft = Math.max(VIEWPORT_EDGE_GAP_PX, viewportWidth - tooltipRect.width - VIEWPORT_EDGE_GAP_PX);
    const left = Math.min(Math.max(unclampedLeft, VIEWPORT_EDGE_GAP_PX), maxLeft);
    const top = placement === 'top'
      ? Math.max(VIEWPORT_EDGE_GAP_PX, anchorRect.top - tooltipRect.height - TARGET_GAP_PX)
      : Math.min(
        viewportHeight - tooltipRect.height - VIEWPORT_EDGE_GAP_PX,
        anchorRect.bottom + TARGET_GAP_PX,
      );

    setLayout({ left, top, placement, ready: true });
  }, [target]);

  useEffect(() => {
    const handlePointerOver = (event: PointerEvent) => {
      const anchor = findTooltipAnchor(event.target);
      if (!anchor || isInsideAnchor(anchor, event.relatedTarget)) return;
      showTooltip(anchor, POINTER_OPEN_DELAY_MS);
    };
    const handlePointerOut = (event: PointerEvent) => {
      const anchor = findTooltipAnchor(event.target);
      if (!anchor || isInsideAnchor(anchor, event.relatedTarget)) return;
      hideTooltip(anchor);
    };
    const handleFocusIn = (event: FocusEvent) => {
      if (pointerInteractionRef.current) return;
      const anchor = findTooltipAnchor(event.target);
      if (anchor) showTooltip(anchor, 0);
    };
    const handleFocusOut = (event: FocusEvent) => {
      const anchor = findTooltipAnchor(event.target);
      if (!anchor || isInsideAnchor(anchor, event.relatedTarget)) return;
      hideTooltip(anchor);
    };
    const handlePointerDown = () => {
      pointerInteractionRef.current = true;
      hideTooltip();
    };
    const handlePointerEnd = () => {
      pointerInteractionRef.current = false;
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hideTooltip();
    };

    document.addEventListener('pointerover', handlePointerOver);
    document.addEventListener('pointerout', handlePointerOut);
    document.addEventListener('focusin', handleFocusIn);
    document.addEventListener('focusout', handleFocusOut);
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('pointerup', handlePointerEnd);
    document.addEventListener('pointercancel', handlePointerEnd);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      clearOpenTimer();
      document.removeEventListener('pointerover', handlePointerOver);
      document.removeEventListener('pointerout', handlePointerOut);
      document.removeEventListener('focusin', handleFocusIn);
      document.removeEventListener('focusout', handleFocusOut);
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('pointerup', handlePointerEnd);
      document.removeEventListener('pointercancel', handlePointerEnd);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [clearOpenTimer, hideTooltip, showTooltip]);

  useLayoutEffect(() => {
    if (!target) return;
    anchorRectRef.current = snapshotAnchorRect(target.anchor);
    setLayout(current => ({ ...current, ready: false }));
    updatePosition();
  }, [target, updatePosition]);

  useEffect(() => {
    if (!target) return;
    const anchor = target.anchor;
    const describedBy = new Set(
      (anchor.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean),
    );
    describedBy.add(tooltipId);
    anchor.setAttribute('aria-describedby', Array.from(describedBy).join(' '));

    return () => {
      const remainingIds = (anchor.getAttribute('aria-describedby') ?? '')
        .split(/\s+/)
        .filter(id => id && id !== tooltipId);
      if (remainingIds.length > 0) {
        anchor.setAttribute('aria-describedby', remainingIds.join(' '));
      } else {
        anchor.removeAttribute('aria-describedby');
      }
    };
  }, [target, tooltipId]);

  useEffect(() => {
    if (!target) return;
    let movementFrame = 0;
    const hideForViewportChange = () => hideTooltip(target.anchor);
    const watchAnchorMovement = () => {
      const initialRect = anchorRectRef.current;
      if (
        !target.anchor.isConnected
        || (initialRect && hasAnchorMoved(initialRect, snapshotAnchorRect(target.anchor)))
      ) {
        hideTooltip(target.anchor);
        return;
      }
      movementFrame = window.requestAnimationFrame(watchAnchorMovement);
    };

    movementFrame = window.requestAnimationFrame(watchAnchorMovement);
    window.addEventListener('resize', hideForViewportChange);
    window.addEventListener('scroll', hideForViewportChange, true);
    return () => {
      window.cancelAnimationFrame(movementFrame);
      window.removeEventListener('resize', hideForViewportChange);
      window.removeEventListener('scroll', hideForViewportChange, true);
    };
  }, [hideTooltip, target]);

  if (!target || typeof document === 'undefined') return null;

  return createPortal(
    <div
      id={tooltipId}
      ref={tooltipRef}
      className="neko-chat-tooltip"
      role="tooltip"
      data-placement={layout.placement}
      data-variant={target.variant}
      data-ready={layout.ready ? 'true' : 'false'}
      style={{ left: layout.left, top: layout.top }}
    >
      {target.text}
    </div>,
    document.body,
  );
}
