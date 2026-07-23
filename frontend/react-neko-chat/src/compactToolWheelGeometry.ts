export type CompactToolWheelVisibleSlot = {
  angleDeg: number;
  scale: number;
};

type ResolveCompactToolWheelPointerHitOptions = {
  fanElement: HTMLElement | null;
  clientX: number;
  clientY: number;
  itemCount: number;
  dragAngleDeg: number;
  visibleSlots: readonly CompactToolWheelVisibleSlot[];
  centerFallbackX: number;
  centerFallbackY: number;
  getSlot: (toolIndex: number) => number | null;
  isToolDisabled?: (toolIndex: number) => boolean;
};

export function resolveCompactToolWheelPointerHit({
  fanElement,
  clientX,
  clientY,
  itemCount,
  dragAngleDeg,
  visibleSlots,
  centerFallbackX,
  centerFallbackY,
  getSlot,
  isToolDisabled,
}: ResolveCompactToolWheelPointerHitOptions): number | null {
  if (!fanElement || !Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
  const fanRect = fanElement.getBoundingClientRect();
  const fanStyle = window.getComputedStyle ? window.getComputedStyle(fanElement) : null;
  const readFanPixelVar = (name: string, fallback: number) => {
    const rawValue = fanStyle?.getPropertyValue(name).trim() || '';
    const parsedValue = Number.parseFloat(rawValue);
    return Number.isFinite(parsedValue) ? parsedValue : fallback;
  };
  const centerX = fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', centerFallbackX);
  const centerY = fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', centerFallbackY);
  const orbitRadius = readFanPixelVar('--compact-tool-wheel-orbit-radius', 80);
  const buttonSize = readFanPixelVar('--compact-tool-button-size', 38);
  const dragAngleRad = dragAngleDeg * (Math.PI / 180);
  let matchedToolIndex: number | null = null;
  let matchedDistanceSquared = Number.POSITIVE_INFINITY;

  for (let toolIndex = 0; toolIndex < itemCount; toolIndex += 1) {
    const slot = getSlot(toolIndex);
    if (slot === null || Math.abs(slot) > 2 || isToolDisabled?.(toolIndex)) continue;
    const slotVisual = visibleSlots[slot + 2];
    if (!slotVisual) continue;
    const angleRad = (slotVisual.angleDeg * (Math.PI / 180)) + dragAngleRad;
    const itemCenterX = centerX + (Math.cos(angleRad) * orbitRadius);
    const itemCenterY = centerY + (Math.sin(angleRad) * orbitRadius);
    const hitRadius = (buttonSize * slotVisual.scale) / 2;
    const dx = clientX - itemCenterX;
    const dy = clientY - itemCenterY;
    const distanceSquared = (dx * dx) + (dy * dy);
    if (distanceSquared <= hitRadius * hitRadius && distanceSquared < matchedDistanceSquared) {
      matchedToolIndex = toolIndex;
      matchedDistanceSquared = distanceSquared;
    }
  }

  return matchedToolIndex;
}

type CompactToolWheelForwardedClickCoordinates = Pick<
  MouseEventInit,
  'clientX' | 'clientY' | 'screenX' | 'screenY'
>;

export function createCompactToolWheelForwardedClick(
  actionButton: HTMLButtonElement,
  coordinates: CompactToolWheelForwardedClickCoordinates,
): MouseEvent {
  const eventView = actionButton.ownerDocument.defaultView ?? window;
  const eventInit: MouseEventInit = {
    view: eventView,
    bubbles: true,
    cancelable: true,
    button: 0,
    ...coordinates,
  };
  try {
    return new eventView.MouseEvent('click', eventInit);
  } catch {
    // JSDOM 27 rejects its own Window proxy after converting MouseEventInit.
    // Browsers take the faithful view-bearing path above.
    const { view: _view, ...fallbackInit } = eventInit;
    return new eventView.MouseEvent('click', fallbackInit);
  }
}
