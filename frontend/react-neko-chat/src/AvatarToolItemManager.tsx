import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { i18n } from './i18n';
import {
  MAX_ACTIVE_AVATAR_TOOLS,
  type AvatarToolId,
  type AvatarToolItem,
  sanitizeAvatarToolIds,
  withAvatarToolAssetVersion,
} from './avatarTools';

type AvatarToolSlotValue = AvatarToolId | null;

type AvatarToolDragSource = {
  kind: 'library' | 'slot';
  toolId: AvatarToolId;
  slotIndex?: number;
};

type AvatarToolDragSession = AvatarToolDragSource & {
  pointerId: number;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
  active: boolean;
  captureTarget: Element | null;
};

type AvatarToolItemManagerProps = {
  open: boolean;
  activeToolIds: AvatarToolId[];
  availableTools: AvatarToolItem[];
  anchorRect?: AvatarToolManagerAnchorRect | null;
  onSave: (toolIds: AvatarToolId[]) => void;
  onCancel: () => void;
};

const AVATAR_TOOL_DRAG_THRESHOLD = 7;
const AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER = 12;
const AVATAR_TOOL_MANAGER_ANCHOR_GAP = 12;
const AVATAR_TOOL_MANAGER_FALLBACK_WIDTH = 380;
const AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT = 600;
const AVATAR_TOOL_MANAGER_FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export type AvatarToolManagerAnchorRect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};

type AvatarToolManagerPosition = {
  left: number;
  top: number;
};

type AvatarToolManagerViewport = {
  left: number;
  top: number;
  width: number;
  height: number;
  right: number;
  bottom: number;
  compactDesktop: boolean;
};

type DesktopCompactLayoutRect = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
} | null;

type DesktopCompactLayoutForAvatarToolManager = {
  workArea?: DesktopCompactLayoutRect;
  windowBounds?: DesktopCompactLayoutRect;
} | null;

type AvatarToolManagerDialogDragSession = {
  pointerId: number;
  startX: number;
  startY: number;
  startLeft: number;
  startTop: number;
  active: boolean;
  captureTarget: Element | null;
};

function getToolLabel(tool: AvatarToolItem): string {
  return i18n(tool.labelKey, tool.labelFallback);
}

function createSlots(toolIds: AvatarToolId[]): AvatarToolSlotValue[] {
  const sanitized = sanitizeAvatarToolIds(toolIds);
  return Array.from({ length: MAX_ACTIVE_AVATAR_TOOLS }, (_, index) => sanitized[index] ?? null);
}

function compactSlots(slots: AvatarToolSlotValue[]): AvatarToolId[] {
  return sanitizeAvatarToolIds(slots.filter((toolId): toolId is AvatarToolId => !!toolId));
}

function getDropSlotIndexFromElement(element: Element | null): number | null {
  const target = element?.closest('[data-avatar-tool-drop-slot]');
  if (!target) return null;
  const rawIndex = Number(target.getAttribute('data-avatar-tool-drop-slot'));
  return Number.isInteger(rawIndex) && rawIndex >= 0 && rawIndex < MAX_ACTIVE_AVATAR_TOOLS
    ? rawIndex
    : null;
}

function findDropSlotIndex(clientX: number, clientY: number, eventTarget: EventTarget | null): number | null {
  if (typeof document !== 'undefined') {
    const elements = typeof document.elementsFromPoint === 'function'
      ? document.elementsFromPoint(clientX, clientY)
      : (
        typeof document.elementFromPoint === 'function'
          ? [document.elementFromPoint(clientX, clientY)].filter((element): element is Element => element instanceof Element)
          : []
      );
    for (const element of elements) {
      const slotIndex = getDropSlotIndexFromElement(element);
      if (slotIndex !== null) return slotIndex;
    }
  }
  if (eventTarget instanceof Element) {
    const eventTargetSlotIndex = getDropSlotIndexFromElement(eventTarget);
    if (eventTargetSlotIndex !== null) return eventTargetSlotIndex;
  }
  return null;
}

function placeLibraryToolInSlot(
  slots: AvatarToolSlotValue[],
  toolId: AvatarToolId,
  targetIndex: number,
): AvatarToolSlotValue[] {
  const next = slots.map(currentId => (currentId === toolId ? null : currentId));
  next[targetIndex] = toolId;
  return next;
}

function moveSlotTool(
  slots: AvatarToolSlotValue[],
  sourceIndex: number,
  targetIndex: number,
): AvatarToolSlotValue[] {
  if (sourceIndex === targetIndex) return slots;
  const movingToolId = slots[sourceIndex];
  if (!movingToolId) return slots;
  const ids = compactSlots(slots).filter(toolId => toolId !== movingToolId);
  ids.splice(Math.min(targetIndex, ids.length), 0, movingToolId);
  return createSlots(ids);
}

function clampValue(value: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

function getViewportSize() {
  if (typeof window === 'undefined') {
    return { width: 1024, height: 768 };
  }
  return {
    width: window.innerWidth || 1024,
    height: window.innerHeight || 768,
  };
}

function readPositiveLayoutNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function readLayoutNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function getDesktopCompactLayout(): DesktopCompactLayoutForAvatarToolManager {
  if (typeof window === 'undefined') return null;
  return (window as typeof window & {
    __nekoDesktopCompactLayout?: DesktopCompactLayoutForAvatarToolManager;
  }).__nekoDesktopCompactLayout || null;
}

function isElectronDesktopEnvironment(): boolean {
  return typeof window !== 'undefined' && !!(
    (window as any).__LANLAN_IS_ELECTRON_PET__
    || (typeof document !== 'undefined'
      && document.body?.classList.contains('electron-chat-window'))
  );
}

function getDialogViewport(): AvatarToolManagerViewport {
  const fallback = getViewportSize();
  const defaultViewport = {
    left: 0,
    top: 0,
    width: fallback.width,
    height: fallback.height,
    right: fallback.width,
    bottom: fallback.height,
    compactDesktop: false,
  };
  if (!isElectronDesktopEnvironment()) return defaultViewport;

  const layout = getDesktopCompactLayout();
  const workArea = layout?.workArea;
  const workAreaWidth = readPositiveLayoutNumber(workArea?.width);
  const workAreaHeight = readPositiveLayoutNumber(workArea?.height);
  if (!workAreaWidth || !workAreaHeight) return defaultViewport;

  const workAreaX = readLayoutNumber(workArea?.x) ?? 0;
  const workAreaY = readLayoutNumber(workArea?.y) ?? 0;
  const windowX = readLayoutNumber(layout?.windowBounds?.x) ?? workAreaX;
  const windowY = readLayoutNumber(layout?.windowBounds?.y) ?? workAreaY;
  const left = workAreaX - windowX;
  const top = workAreaY - windowY;
  return {
    left,
    top,
    width: workAreaWidth,
    height: workAreaHeight,
    right: left + workAreaWidth,
    bottom: top + workAreaHeight,
    compactDesktop: true,
  };
}

function getDesktopCompactDialogSize(viewport: AvatarToolManagerViewport) {
  return {
    width: Math.max(
      1,
      Math.min(
        AVATAR_TOOL_MANAGER_FALLBACK_WIDTH,
        viewport.width - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER * 2,
      ),
    ),
    height: Math.max(
      1,
      Math.min(
        AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT,
        viewport.height - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER * 2,
      ),
    ),
  };
}

function getDialogSize(dialogElement: HTMLElement | null, viewport: AvatarToolManagerViewport = getDialogViewport()) {
  if (viewport.compactDesktop) {
    return getDesktopCompactDialogSize(viewport);
  }
  return {
    width: dialogElement?.offsetWidth || AVATAR_TOOL_MANAGER_FALLBACK_WIDTH,
    height: dialogElement?.offsetHeight || AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT,
  };
}

function clampDialogPosition(
  position: AvatarToolManagerPosition,
  dialogSize: { width: number; height: number },
  viewport: AvatarToolManagerViewport = getDialogViewport(),
) {
  return {
    left: clampValue(
      position.left,
      viewport.left + AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
      viewport.right - dialogSize.width - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
    ),
    top: clampValue(
      position.top,
      viewport.top + AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
      viewport.bottom - dialogSize.height - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
    ),
  };
}

function resolveAnchoredDialogPosition(
  anchorRect: AvatarToolManagerAnchorRect | null | undefined,
  dialogSize: { width: number; height: number },
) {
  const viewport = getDialogViewport();
  if ((!isElectronDesktopEnvironment() && viewport.width <= 640) || !anchorRect) {
    return null;
  }

  const preferredBelowTop = anchorRect.bottom + AVATAR_TOOL_MANAGER_ANCHOR_GAP;
  const preferredAboveTop = anchorRect.top - dialogSize.height - AVATAR_TOOL_MANAGER_ANCHOR_GAP;
  const top = preferredBelowTop + dialogSize.height <= viewport.bottom - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER
    ? preferredBelowTop
    : preferredAboveTop;

  return clampDialogPosition({
    left: anchorRect.right - dialogSize.width,
    top,
  }, dialogSize, viewport);
}

function getFocusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(AVATAR_TOOL_MANAGER_FOCUSABLE_SELECTOR))
    .filter(element => (
      element.tabIndex >= 0
      && element.getAttribute('aria-hidden') !== 'true'
    ));
}

export default function AvatarToolItemManager({
  open,
  activeToolIds,
  availableTools,
  anchorRect = null,
  onSave,
  onCancel,
}: AvatarToolItemManagerProps) {
  const [draftSlots, setDraftSlots] = useState<AvatarToolSlotValue[]>(() => createSlots(activeToolIds));
  const [notice, setNotice] = useState('');
  const [dragSession, setDragSession] = useState<AvatarToolDragSession | null>(null);
  const [dialogPosition, setDialogPosition] = useState<AvatarToolManagerPosition | null>(null);
  const [dialogDragSession, setDialogDragSession] = useState<AvatarToolManagerDialogDragSession | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const prevActiveElementRef = useRef<HTMLElement | null>(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    setDraftSlots(createSlots(activeToolIds));
    setNotice('');
    setDragSession(null);
    setDialogDragSession(null);
    suppressClickRef.current = false;
  }, [activeToolIds, open]);

  useLayoutEffect(() => {
    if (!open) {
      setDialogPosition(null);
      setDialogDragSession(null);
      return;
    }
    const viewport = getDialogViewport();
    const nextPosition = resolveAnchoredDialogPosition(anchorRect, getDialogSize(dialogRef.current, viewport));
    setDialogPosition(nextPosition);
  }, [anchorRect, open]);

  useEffect(() => {
    if (!open || typeof window === 'undefined') return undefined;
    const clampCurrentPosition = () => {
      const viewport = getDialogViewport();
      setDialogPosition((position) => {
        if (!isElectronDesktopEnvironment() && viewport.width <= 640) return null;
        if (!position) return position;
        return clampDialogPosition(position, getDialogSize(dialogRef.current, viewport), viewport);
      });
    };
    window.addEventListener('resize', clampCurrentPosition);
    window.addEventListener('neko:desktop-compact-layout-change', clampCurrentPosition);
    return () => {
      window.removeEventListener('resize', clampCurrentPosition);
      window.removeEventListener('neko:desktop-compact-layout-change', clampCurrentPosition);
    };
  }, [open]);

  const isPositioned = dialogPosition !== null;

  useEffect(() => {
    if (!open || typeof window === 'undefined') return undefined;
    const prevPointerEvents = document.body.style.pointerEvents;
    if (prevPointerEvents === 'none') {
      document.body.style.pointerEvents = '';
    }
    if (isPositioned) {
      window.dispatchEvent(new CustomEvent('neko:compact-surface-resize-width-change'));
    }
    return () => {
      if (prevPointerEvents === 'none') {
        document.body.style.pointerEvents = prevPointerEvents;
      }
      if (isPositioned) {
        window.dispatchEvent(new CustomEvent('neko:compact-surface-resize-width-change'));
      }
    };
  }, [open, isPositioned]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;
    prevActiveElementRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus({ preventScroll: true });

    return () => {
      const previousElement = prevActiveElementRef.current;
      prevActiveElementRef.current = null;
      if (previousElement && document.contains(previousElement)) {
        previousElement.focus({ preventScroll: true });
      }
    };
  }, [open]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;
    const dialogElement = dialogRef.current;
    if (!dialogElement) return undefined;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const focusableElements = getFocusableElements(dialogElement);
      if (focusableElements.length === 0) {
        event.preventDefault();
        dialogElement.focus({ preventScroll: true });
        return;
      }

      const activeElement = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
      const currentIndex = activeElement ? focusableElements.indexOf(activeElement) : -1;
      const nextIndex = currentIndex === -1
        ? (event.shiftKey ? focusableElements.length - 1 : 0)
        : (
          currentIndex
          + (event.shiftKey ? -1 : 1)
          + focusableElements.length
        ) % focusableElements.length;
      event.preventDefault();
      focusableElements[nextIndex]?.focus({ preventScroll: true });
    };

    dialogElement.addEventListener('keydown', handleKeyDown);
    return () => {
      dialogElement.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const availableById = useMemo(() => (
    new Map(availableTools.map(tool => [tool.id, tool]))
  ), [availableTools]);
  const equippedIds = compactSlots(draftSlots);
  const equippedIdSet = new Set(equippedIds);
  const draftFull = equippedIds.length >= MAX_ACTIVE_AVATAR_TOOLS;
  const dialogTitleId = 'avatar-tool-manager-title';
  const noticeId = notice ? 'avatar-tool-manager-notice' : undefined;

  const startDrag = (source: AvatarToolDragSource, event: ReactPointerEvent<HTMLElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const captureTarget = event.currentTarget;
    setDragSession({
      ...source,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      currentX: event.clientX,
      currentY: event.clientY,
      active: false,
      captureTarget,
    });
    try {
      captureTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  };

  const updateDrag = (event: ReactPointerEvent<HTMLElement>) => {
    setDragSession((session) => {
      if (!session || session.pointerId !== event.pointerId) return session;
      const active = session.active
        || Math.hypot(event.clientX - session.startX, event.clientY - session.startY) >= AVATAR_TOOL_DRAG_THRESHOLD;
      if (active) {
        event.preventDefault();
      }
      return {
        ...session,
        currentX: event.clientX,
        currentY: event.clientY,
        active,
      };
    });
  };

  const finishDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const session = dragSession;
    if (!session || session.pointerId !== event.pointerId) return;

    try {
      session.captureTarget?.releasePointerCapture?.(event.pointerId);
    } catch (_) {}

    if (session.active) {
      event.preventDefault();
      suppressClickRef.current = true;
      const targetSlotIndex = findDropSlotIndex(event.clientX, event.clientY, event.target);
      if (targetSlotIndex !== null) {
        setDraftSlots((slots) => {
          if (session.kind === 'slot' && typeof session.slotIndex === 'number') {
            return moveSlotTool(slots, session.slotIndex, targetSlotIndex);
          }
          return placeLibraryToolInSlot(slots, session.toolId, targetSlotIndex);
        });
        setNotice('');
      }
      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    }

    setDragSession(null);
  };

  const cancelDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (!dragSession || dragSession.pointerId !== event.pointerId) return;
    try {
      dragSession.captureTarget?.releasePointerCapture?.(event.pointerId);
    } catch (_) {}
    setDragSession(null);
  };

  const handleLibraryClick = (toolId: AvatarToolId) => {
    if (suppressClickRef.current) return;
    if (equippedIdSet.has(toolId)) return;
    const firstEmptyIndex = draftSlots.findIndex(slotToolId => slotToolId === null);
    if (firstEmptyIndex < 0 || draftFull) {
      setNotice(i18n('chat.avatarToolSlotFull', 'Unequip a tool first.'));
      return;
    }
    setDraftSlots((slots) => {
      const next = [...slots];
      next[firstEmptyIndex] = toolId;
      return next;
    });
    setNotice('');
  };

  const handleRemoveSlot = (index: number) => {
    setDraftSlots((slots) => {
      const next = [...slots];
      next[index] = null;
      return next;
    });
    setNotice('');
  };

  const handleSave = () => {
    onSave(compactSlots(draftSlots));
  };

  const startDialogDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (!dialogPosition) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (event.target instanceof Element && event.target.closest('button')) return;

    const captureTarget = event.currentTarget;
    setDialogDragSession({
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startLeft: dialogPosition.left,
      startTop: dialogPosition.top,
      active: false,
      captureTarget,
    });
    try {
      captureTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  };

  const updateDialogDrag = (event: ReactPointerEvent<HTMLElement>) => {
    setDialogDragSession((session) => {
      if (!session || session.pointerId !== event.pointerId) return session;
      const active = session.active
        || Math.hypot(event.clientX - session.startX, event.clientY - session.startY) >= AVATAR_TOOL_DRAG_THRESHOLD;
      if (!active) return session;
      event.preventDefault();
      const viewport = getDialogViewport();
      setDialogPosition(clampDialogPosition({
        left: session.startLeft + event.clientX - session.startX,
        top: session.startTop + event.clientY - session.startY,
      }, getDialogSize(dialogRef.current, viewport), viewport));
      return {
        ...session,
        active,
      };
    });
  };

  const finishDialogDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const session = dialogDragSession;
    if (!session || session.pointerId !== event.pointerId) return;
    try {
      session.captureTarget?.releasePointerCapture?.(event.pointerId);
    } catch (_) {}
    if (session.active) {
      event.preventDefault();
    }
    setDialogDragSession(null);
  };

  const cancelDialogDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (!dialogDragSession || dialogDragSession.pointerId !== event.pointerId) return;
    try {
      dialogDragSession.captureTarget?.releasePointerCapture?.(event.pointerId);
    } catch (_) {}
    setDialogDragSession(null);
  };

  if (!open || typeof document === 'undefined') {
    return null;
  }

  const isDesktopMode = dialogPosition !== null;
  const dialogViewport = getDialogViewport();
  const dialogSize = getDialogSize(dialogRef.current, dialogViewport);
  const isDesktopCompactDialog = dialogViewport.compactDesktop;
  const dragTool = dragSession ? availableById.get(dragSession.toolId) : null;
  const managerDragging = !!dialogDragSession?.active || !!dragSession?.active;

  const dialogStyle = dialogPosition || isDesktopCompactDialog
    ? ({
      ...(dialogPosition ? {
        '--avatar-tool-manager-left': `${dialogPosition.left}px`,
        '--avatar-tool-manager-top': `${dialogPosition.top}px`,
      } : {}),
      ...(isDesktopCompactDialog ? {
        '--avatar-tool-manager-width': `${dialogSize.width}px`,
        '--avatar-tool-manager-height': `${dialogSize.height}px`,
        '--avatar-tool-manager-max-height': `${dialogSize.height}px`,
      } : {}),
    } as CSSProperties)
    : undefined;

  const stopModelDrag = (event: ReactPointerEvent<HTMLElement> | ReactMouseEvent<HTMLElement>) => {
    if (document.body.classList.contains('neko-model-dragging')) return;
    event.stopPropagation();
  };

  const dialogElement = (
    <section
      className={`avatar-tool-manager-dialog${dialogPosition ? ' is-positioned' : ''}${isDesktopCompactDialog ? ' is-desktop-compact-layout' : ''}${managerDragging ? ' is-dragging' : ''}`}
      ref={dialogRef}
      style={dialogStyle}
      role="dialog"
      aria-modal="true"
      aria-labelledby={dialogTitleId}
      aria-describedby={noticeId}
      tabIndex={-1}
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="avatarToolManager"
      onPointerDown={stopModelDrag}
      onMouseDown={stopModelDrag}
      onClick={(event) => event.stopPropagation()}
    >
      <header
        className="avatar-tool-manager-header"
        onPointerDown={startDialogDrag}
        onPointerMove={updateDialogDrag}
        onPointerUp={finishDialogDrag}
        onPointerCancel={cancelDialogDrag}
      >
        <div>
          <h2 id={dialogTitleId}>{i18n('chat.avatarToolManagerTitle', 'Manage tools')}</h2>
          <p>{i18n('chat.avatarToolManagerSubtitle', 'Choose up to 3 quick tools.')}</p>
        </div>
        <button
          className="avatar-tool-manager-icon-button"
          type="button"
          ref={closeButtonRef}
          aria-label={i18n('chat.avatarToolManagerClose', 'Close')}
          title={i18n('chat.avatarToolManagerClose', 'Close')}
          onClick={onCancel}
        >
          <img src="/static/icons/close_button.png" alt="" aria-hidden="true" />
        </button>
      </header>

      <div className="avatar-tool-manager-body">
        <section className="avatar-tool-manager-section" aria-label={i18n('chat.avatarToolCurrentTools', 'Current tools')}>
          <h3>{i18n('chat.avatarToolCurrentTools', 'Current tools')}</h3>
          <div className="avatar-tool-manager-slots">
            {draftSlots.map((toolId, index) => {
              const tool = toolId ? availableById.get(toolId) : null;
              const label = tool ? getToolLabel(tool) : i18n('chat.avatarToolEmptySlot', 'Empty slot');
              return (
                <div
                  key={index}
                  className={`avatar-tool-manager-slot${tool ? ' is-filled' : ' is-empty'}`}
                  data-avatar-tool-drop-slot={index}
                  data-avatar-tool-id={tool?.id ?? ''}
                >
                  {tool ? (
                    <button
                      className="avatar-tool-manager-slot-card"
                      type="button"
                      data-avatar-tool-slot-index={index}
                      onPointerDown={(event) => startDrag({ kind: 'slot', toolId: tool.id, slotIndex: index }, event)}
                      onPointerMove={updateDrag}
                      onPointerUp={finishDrag}
                      onPointerCancel={cancelDrag}
                    >
                      <img
                        className={`avatar-tool-manager-tool-image avatar-tool-icon avatar-tool-icon-${tool.id}`}
                        src={withAvatarToolAssetVersion(tool.iconImagePath)}
                        alt=""
                        aria-hidden="true"
                      />
                      <span>{label}</span>
                    </button>
                  ) : (
                    <span className="avatar-tool-manager-empty-slot">{label}</span>
                  )}
                  {tool ? (
                    <button
                      className="avatar-tool-manager-remove"
                      type="button"
                      aria-label={`${i18n('chat.avatarToolRemove', 'Remove')} ${label}`}
                      title={`${i18n('chat.avatarToolRemove', 'Remove')} ${label}`}
                      onClick={() => handleRemoveSlot(index)}
                    >
                      {i18n('chat.avatarToolRemove', 'Remove')}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>

        <section className="avatar-tool-manager-section" aria-label={i18n('chat.avatarToolLibrary', 'Tool library')}>
          <h3>{i18n('chat.avatarToolLibrary', 'Tool library')}</h3>
          {availableTools.length > 0 ? (
            <div className="avatar-tool-manager-library">
              {availableTools.map((tool) => {
                const label = getToolLabel(tool);
                const equipped = equippedIdSet.has(tool.id);
                return (
                  <button
                    key={tool.id}
                    className={`avatar-tool-manager-library-card${equipped ? ' is-equipped' : ''}`}
                    type="button"
                    aria-pressed={equipped}
                    data-avatar-tool-library-id={tool.id}
                    onClick={() => handleLibraryClick(tool.id)}
                    onPointerDown={equipped ? undefined : (event) => startDrag({ kind: 'library', toolId: tool.id }, event)}
                    onPointerMove={updateDrag}
                    onPointerUp={finishDrag}
                    onPointerCancel={cancelDrag}
                  >
                    <img
                      className={`avatar-tool-manager-tool-image avatar-tool-icon avatar-tool-icon-${tool.id}`}
                      src={withAvatarToolAssetVersion(tool.iconImagePath)}
                      alt=""
                      aria-hidden="true"
                    />
                    <span className="avatar-tool-manager-library-label">{label}</span>
                    <span className="avatar-tool-manager-library-status">
                      {equipped
                        ? i18n('chat.avatarToolEquipped', 'Equipped')
                        : i18n('chat.avatarToolEquip', 'Equip')}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="avatar-tool-manager-empty-library">
              {i18n('chat.avatarToolNoAvailableTools', 'No tools available')}
            </p>
          )}
        </section>
      </div>

      {notice ? (
        <p id="avatar-tool-manager-notice" className="avatar-tool-manager-notice" role="status">
          {notice}
        </p>
      ) : null}

      <footer className="avatar-tool-manager-actions">
        <button className="avatar-tool-manager-action secondary" type="button" onClick={onCancel}>
          {i18n('chat.avatarToolCancel', 'Cancel')}
        </button>
        <button className="avatar-tool-manager-action primary" type="button" onClick={handleSave}>
          {i18n('chat.avatarToolSave', 'Save changes')}
        </button>
      </footer>

      {dragSession?.active && dragTool ? (
        <div
          className="avatar-tool-manager-drag-ghost"
          aria-hidden="true"
          style={{
            transform: `translate3d(${dragSession.currentX}px, ${dragSession.currentY}px, 0)`,
          }}
        >
          <img
            className={`avatar-tool-icon avatar-tool-icon-${dragTool.id}`}
            src={withAvatarToolAssetVersion(dragTool.iconImagePath)}
            alt=""
          />
        </div>
      ) : null}
    </section>
  );

  return createPortal(
    <>
      <div
        className={`avatar-tool-manager-overlay${isDesktopMode ? ' is-desktop' : ''}`}
        data-testid="avatar-tool-manager-overlay"
        onPointerDown={stopModelDrag}
        onMouseDown={stopModelDrag}
        onClick={(event) => {
          event.stopPropagation();
          if (event.target === event.currentTarget) {
            onCancel();
          }
        }}
      />
      {dialogElement}
    </>,
    document.body,
  );
}

export type { AvatarToolItemManagerProps };
