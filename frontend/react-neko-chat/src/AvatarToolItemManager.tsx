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
import AvatarToolCreatePage from './AvatarToolCreatePage';
import AvatarToolEditorWorkspace from './AvatarToolEditorWorkspace';
import {
  LocalAvatarToolRevisionConflictError,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolDetail,
  type LocalAvatarToolLimits,
  type UpdateLocalAvatarToolInput,
} from './avatar-tools/localTools';
import {
  MAX_ACTIVE_AVATAR_TOOLS,
  getAvatarToolItemLabel,
  isAvatarToolId,
  isLocalAvatarToolId,
  type AvatarToolId,
  type AvatarToolItem,
  sanitizeAvatarToolSlots,
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
  availableTools: ReadonlyArray<AvatarToolItem>;
  runnableToolIds?: ReadonlySet<AvatarToolId>;
  anchorRect?: AvatarToolManagerAnchorRect | null;
  onSave: (toolIds: AvatarToolId[]) => void;
  onCancel: () => void;
  createLimits?: LocalAvatarToolLimits | null;
  userName?: string;
  assistantName?: string;
  onCreate?: (input: CreateLocalAvatarToolInput) => Promise<void>;
  onLoadDetail?: (toolId: `local-${string}`) => Promise<LocalAvatarToolDetail>;
  onUpdate?: (toolId: `local-${string}`, input: UpdateLocalAvatarToolInput) => Promise<void>;
  onDelete?: (toolId: `local-${string}`) => Promise<void>;
  catalogAuthoritativeLoaded?: boolean;
  catalogRefreshFailed?: boolean;
  onExternalEditorResult?: (result: AvatarToolEditorResultMessage) => void;
};

const AVATAR_TOOL_DRAG_THRESHOLD = 7;
const AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER = 12;
const AVATAR_TOOL_MANAGER_ANCHOR_GAP = 12;
const AVATAR_TOOL_MANAGER_FALLBACK_WIDTH = 460;
const AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT = 680;
const AVATAR_TOOL_CREATE_FALLBACK_HEIGHT = 780;
const AVATAR_TOOL_CREATE_SPECIAL_FALLBACK_HEIGHT = 1040;
const AVATAR_TOOL_EDITOR_WINDOW_NAME = 'neko_avatar_tool_editor_singleton';
const AVATAR_TOOL_EDITOR_PREFERRED_WIDTH = 1280;
const AVATAR_TOOL_EDITOR_PREFERRED_HEIGHT = 900;
const AVATAR_TOOL_EDITOR_SCREEN_GUTTER = 48;
const AVATAR_TOOL_EDITOR_SUPPORTED_LANGUAGES = new Set([
  'zh-CN',
  'zh-TW',
  'en',
  'ja',
  'ko',
  'ru',
  'es',
  'pt',
]);
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

declare global {
  interface Window {
    openOrFocusWindow?: (
      url: string,
      windowName: string,
      features?: string,
      options?: {
        navigateOnReuse?: boolean;
        shouldNavigateOnReuse?: (existingWindow: Window, targetUrl: string) => boolean;
      },
    ) => Window | null;
    avatarToolEditorHasUnsavedChanges?: () => boolean;
  }
}

export type AvatarToolEditorResultMessage = {
  type: 'neko:avatar-tool-editor-result';
  action: 'created' | 'updated' | 'deleted';
  toolId?: string;
};

function normalizeAvatarToolEditorLanguage(value: unknown): string {
  const language = typeof value === 'string' ? value.trim() : '';
  if (!language) return '';
  if (AVATAR_TOOL_EDITOR_SUPPORTED_LANGUAGES.has(language)) return language;
  const lower = language.toLowerCase();
  const base = lower.split('-')[0];
  if (base === 'zh') return /(tw|hk|hant)/i.test(language) ? 'zh-TW' : 'zh-CN';
  return AVATAR_TOOL_EDITOR_SUPPORTED_LANGUAGES.has(base) ? base : '';
}

function currentAvatarToolEditorLanguage(): string {
  const runtime = window as unknown as {
    i18next?: { language?: unknown; resolvedLanguage?: unknown };
    i18n?: { language?: unknown; resolvedLanguage?: unknown };
  };
  const liveLanguage = normalizeAvatarToolEditorLanguage(
    runtime.i18next?.resolvedLanguage
      ?? runtime.i18next?.language
      ?? runtime.i18n?.resolvedLanguage
      ?? runtime.i18n?.language,
  );
  if (liveLanguage) return liveLanguage;
  try {
    return normalizeAvatarToolEditorLanguage(window.localStorage.getItem('i18nextLng'));
  } catch {
    return '';
  }
}

export function openAvatarToolEditorWindow(
  mode: 'create' | 'edit',
  toolId?: string,
): Window | null {
  if (typeof window === 'undefined') return null;
  const url = new URL('/avatar_tool_editor', window.location.origin);
  url.searchParams.set('mode', mode);
  if (mode === 'edit' && toolId) url.searchParams.set('toolId', toolId);
  const uiLanguage = currentAvatarToolEditorLanguage();
  if (uiLanguage) url.searchParams.set('ui_lang', uiLanguage);

  const availableWidth = Math.max(1, Number(window.screen?.availWidth) || 1440);
  const availableHeight = Math.max(1, Number(window.screen?.availHeight) || 1080);
  const width = Math.max(1, Math.min(
    AVATAR_TOOL_EDITOR_PREFERRED_WIDTH,
    availableWidth - Math.min(AVATAR_TOOL_EDITOR_SCREEN_GUTTER, availableWidth - 1),
  ));
  const height = Math.max(1, Math.min(
    AVATAR_TOOL_EDITOR_PREFERRED_HEIGHT,
    availableHeight - Math.min(AVATAR_TOOL_EDITOR_SCREEN_GUTTER, availableHeight - 1),
  ));
  const left = Math.round(Math.max(0, (availableWidth - width) / 2));
  const top = Math.round(Math.max(0, (availableHeight - height) / 2));
  const features = [
    'toolbar=no',
    'location=no',
    'status=no',
    'menubar=no',
    'scrollbars=no',
    'resizable=yes',
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
  ].join(',');
  const popup = typeof window.openOrFocusWindow === 'function'
    ? window.openOrFocusWindow(url.href, AVATAR_TOOL_EDITOR_WINDOW_NAME, features, {
      navigateOnReuse: true,
      shouldNavigateOnReuse: (existingWindow, targetUrl) => {
        try {
          const current = new URL(existingWindow.location.href);
          const target = new URL(targetUrl);
          if (
            current.origin === target.origin
            && current.pathname === target.pathname
            && current.searchParams.get('mode') === target.searchParams.get('mode')
            && current.searchParams.get('toolId') === target.searchParams.get('toolId')
          ) return false;
          if (!existingWindow.avatarToolEditorHasUnsavedChanges) return false;
          if (!existingWindow.avatarToolEditorHasUnsavedChanges()) return true;
          return window.confirm(i18n(
            'dialogs.unsavedChanges',
            'You have unsaved settings, are you sure you want to leave?',
          ));
        } catch {
          // An uninspectable window must not silently replace an unknown draft.
          return false;
        }
      },
    })
    // If the shared window manager is unavailable, a new window is safer than
    // navigating an existing named editor whose draft cannot be inspected.
    : window.open(url.href, '_blank', features);
  try { popup?.focus(); } catch (_) {}
  return popup;
}

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
  return getAvatarToolItemLabel(tool);
}

function getToolImageStyle(tool: AvatarToolItem): CSSProperties | undefined {
  const visual = tool.managerIconVisual;
  if (!visual) return undefined;
  return {
    transform: `scale(${visual.scale}) translate(${visual.translateXPercent}%, ${visual.translateYPercent}%)`,
    transformOrigin: 'center center',
  };
}

function createSlots(toolIds: AvatarToolId[]): AvatarToolSlotValue[] {
  const retained: AvatarToolId[] = [];
  toolIds.forEach((toolId) => {
    if (!isAvatarToolId(toolId) || retained.includes(toolId) || retained.length >= MAX_ACTIVE_AVATAR_TOOLS) return;
    retained.push(toolId);
  });
  return Array.from({ length: MAX_ACTIVE_AVATAR_TOOLS }, (_, index) => retained[index] ?? null);
}

// 草稿保留暂时不可用的 id，否则用户改一下
// 别的槽位再保存，就把一个只是本轮没出现在列表里的道具永久冲掉了。
function compactSlots(slots: AvatarToolSlotValue[]): AvatarToolId[] {
  return sanitizeAvatarToolSlots(
    slots.filter((toolId): toolId is AvatarToolId => !!toolId),
  );
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
      && document.body?.classList.contains('neko-electron-runtime'))
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

function getDesktopCompactDialogSize(
  viewport: AvatarToolManagerViewport,
  preferredHeight: number = AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT,
) {
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
        preferredHeight,
        viewport.height - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER * 2,
      ),
    ),
  };
}

function getDialogSize(
  dialogElement: HTMLElement | null,
  viewport: AvatarToolManagerViewport = getDialogViewport(),
  preferredHeight: number = AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT,
) {
  if (viewport.compactDesktop) {
    return getDesktopCompactDialogSize(viewport, preferredHeight);
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

function clampCreateDialogPosition(
  position: AvatarToolManagerPosition,
  dialogSize: { width: number; height: number },
  viewport: AvatarToolManagerViewport,
) {
  const top = clampValue(
    position.top,
    viewport.top + AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
    viewport.bottom - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER - 1,
  );
  const availableHeight = Math.max(
    1,
    viewport.bottom - top - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
  );
  return clampDialogPosition(
    { ...position, top },
    { ...dialogSize, height: Math.min(dialogSize.height, availableHeight) },
    viewport,
  );
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
  runnableToolIds,
  anchorRect = null,
  onSave,
  onCancel,
  createLimits = null,
  userName = '',
  assistantName = '',
  onCreate,
  onLoadDetail,
  onUpdate,
  onDelete,
  catalogAuthoritativeLoaded = true,
  catalogRefreshFailed = false,
  onExternalEditorResult,
}: AvatarToolItemManagerProps) {
  const validToolIds = useMemo(
    () => runnableToolIds ?? new Set<AvatarToolId>(availableTools.map(tool => tool.id)),
    [availableTools, runnableToolIds],
  );
  const [draftSlots, setDraftSlots] = useState<AvatarToolSlotValue[]>(() => createSlots(activeToolIds));
  const [view, setView] = useState<'library' | 'create' | 'edit'>('library');
  const [editDetail, setEditDetail] = useState<LocalAvatarToolDetail | null>(null);
  const [loadingEditToolId, setLoadingEditToolId] = useState<AvatarToolId | null>(null);
  const [createSpecialEnabled, setCreateSpecialEnabled] = useState(false);
  const [notice, setNotice] = useState('');
  const [noticeIsError, setNoticeIsError] = useState(false);
  const [dragSession, setDragSession] = useState<AvatarToolDragSession | null>(null);
  const [dialogPosition, setDialogPosition] = useState<AvatarToolManagerPosition | null>(null);
  const [dialogDragSession, setDialogDragSession] = useState<AvatarToolManagerDialogDragSession | null>(null);
  const preferredDialogHeight = view !== 'library'
    ? (createSpecialEnabled
      ? AVATAR_TOOL_CREATE_SPECIAL_FALLBACK_HEIGHT
      : AVATAR_TOOL_CREATE_FALLBACK_HEIGHT)
    : AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT;
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const workspaceBackButtonRef = useRef<HTMLButtonElement | null>(null);
  const workspaceReturnFocusSelectorRef = useRef<string | null>(null);
  const previousViewRef = useRef<'library' | 'create' | 'edit'>('library');
  const prevActiveElementRef = useRef<HTMLElement | null>(null);
  const suppressClickRef = useRef(false);
  const wasOpenRef = useRef(false);
  const previousActiveToolIdsRef = useRef(activeToolIds);
  const editRequestRef = useRef(0);
  // 保存请求在途时对话框仍可关闭。用户关掉再开、开始新一轮编辑后，旧请求完成
  // 时若无条件收尾，就会把新会话切回库页并丢掉他正在填的表单。
  const managerSessionRef = useRef(0);

  useEffect(() => {
    const removedIds = new Set(previousActiveToolIdsRef.current.filter(id => !activeToolIds.includes(id)));
    previousActiveToolIdsRef.current = activeToolIds;
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) {
      // 父层只在明确移除后改变保存槽位；目录缺项或重渲染不重置编辑草稿。
      if (removedIds.size > 0) {
        setDraftSlots(slots => slots.map(id => id !== null && removedIds.has(id) ? null : id));
      }
      return;
    }
    wasOpenRef.current = true;
    setDraftSlots(createSlots(activeToolIds));
    setView('library');
    setEditDetail(null);
    setLoadingEditToolId(null);
    setCreateSpecialEnabled(false);
    setNotice('');
    setNoticeIsError(false);
    editRequestRef.current += 1;
    managerSessionRef.current += 1;
    setDragSession(null);
    setDialogDragSession(null);
    suppressClickRef.current = false;
  }, [activeToolIds, open]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const handleEditorResult = (event: MessageEvent<AvatarToolEditorResultMessage>) => {
      if (event.origin !== window.location.origin) return;
      const payload = event.data;
      if (!payload || payload.type !== 'neko:avatar-tool-editor-result') return;
      if (!['created', 'updated', 'deleted'].includes(payload.action)) return;
      if (payload.toolId !== undefined && !isLocalAvatarToolId(payload.toolId)) return;
      if (payload.action === 'deleted' && typeof payload.toolId === 'string') {
        setDraftSlots(slots => slots.map(toolId => toolId === payload.toolId ? null : toolId));
      }
      window.dispatchEvent(new Event('neko:refresh-local-avatar-tools'));
      onExternalEditorResult?.(payload);
    };
    window.addEventListener('message', handleEditorResult);
    return () => window.removeEventListener('message', handleEditorResult);
  }, [onExternalEditorResult]);

  useLayoutEffect(() => {
    if (!open) {
      setDialogPosition(null);
      setDialogDragSession(null);
      return;
    }
    const viewport = getDialogViewport();
    const nextPosition = resolveAnchoredDialogPosition(
      anchorRect,
      getDialogSize(dialogRef.current, viewport, AVATAR_TOOL_MANAGER_FALLBACK_HEIGHT),
    );
    setDialogPosition(nextPosition);
  }, [
    anchorRect?.bottom,
    anchorRect?.left,
    anchorRect?.right,
    anchorRect?.top,
    anchorRect?.width,
    anchorRect?.height,
    open,
  ]);

  useEffect(() => {
    if (!open || view !== 'library' || typeof window === 'undefined') return undefined;
    const clampCurrentPosition = () => {
      const viewport = getDialogViewport();
      setDialogPosition((position) => {
        if (!isElectronDesktopEnvironment() && viewport.width <= 640) return null;
        if (!position) return position;
        const dialogSize = getDialogSize(dialogRef.current, viewport, preferredDialogHeight);
        return view !== 'library'
          ? clampCreateDialogPosition(position, dialogSize, viewport)
          : clampDialogPosition(position, dialogSize, viewport);
      });
    };
    window.addEventListener('resize', clampCurrentPosition);
    window.addEventListener('neko:desktop-compact-layout-change', clampCurrentPosition);
    return () => {
      window.removeEventListener('resize', clampCurrentPosition);
      window.removeEventListener('neko:desktop-compact-layout-change', clampCurrentPosition);
    };
  }, [open, preferredDialogHeight, view]);

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
      if (event.key === 'Escape' && !event.isComposing) {
        event.preventDefault();
        event.stopPropagation();
        if (view === 'library') {
          onCancel();
        } else {
          setCreateSpecialEnabled(false);
          setEditDetail(null);
          setNotice('');
          setNoticeIsError(false);
          setView('library');
        }
        return;
      }
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
  }, [onCancel, open, view]);

  useLayoutEffect(() => {
    const previousView = previousViewRef.current;
    previousViewRef.current = view;
    if (!open) return;
    if (view !== 'library') {
      workspaceBackButtonRef.current?.focus({ preventScroll: true });
      return;
    }
    if (previousView !== 'library') {
      const returnSelector = workspaceReturnFocusSelectorRef.current;
      workspaceReturnFocusSelectorRef.current = null;
      const returnTarget = returnSelector
        ? document.querySelector<HTMLElement>(returnSelector)
        : null;
      if (returnTarget) {
        returnTarget.focus({ preventScroll: true });
      }
    }
  }, [open, view]);

  const availableById = useMemo(() => (
    new Map(availableTools.map(tool => [tool.id, tool]))
  ), [availableTools]);
  const equippedIds = compactSlots(draftSlots);
  const equippedIdSet = new Set(equippedIds);
  // 列表缺项不代表删除，非 null ID 始终占据槽位。
  const draftFull = draftSlots.filter(toolId => toolId !== null).length
    >= MAX_ACTIVE_AVATAR_TOOLS;
  const catalogSaveBlocked = !catalogAuthoritativeLoaded && activeToolIds.some(isLocalAvatarToolId);
  const dialogTitleId = 'avatar-tool-manager-title';
  const noticeId = notice && view !== 'create' ? 'avatar-tool-manager-notice' : undefined;

  const startDrag = (source: AvatarToolDragSource, event: ReactPointerEvent<HTMLElement>) => {
    if (!validToolIds.has(source.toolId)) return;
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
          const targetId = slots[targetSlotIndex];
          if (targetId !== null && !validToolIds.has(targetId)) return slots;
          return placeLibraryToolInSlot(slots, session.toolId, targetSlotIndex);
        });
        setNotice('');
        setNoticeIsError(false);
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
    if (!validToolIds.has(toolId)) return;
    if (equippedIdSet.has(toolId)) return;
    const firstEmptyIndex = draftSlots.findIndex(
      slotToolId => slotToolId === null,
    );
    if (firstEmptyIndex < 0 || draftFull) {
      setNotice(i18n('chat.avatarToolSlotFull', 'Unequip a tool first.'));
      setNoticeIsError(false);
      return;
    }
    setDraftSlots((slots) => {
      const next = [...slots];
      next[firstEmptyIndex] = toolId;
      return next;
    });
    setNotice('');
    setNoticeIsError(false);
  };

  const handleRemoveSlot = (index: number) => {
    setDraftSlots((slots) => {
      const next = [...slots];
      next[index] = null;
      return next;
    });
    setNotice('');
    setNoticeIsError(false);
  };

  const openEdit = async (toolId: `local-${string}`) => {
    if (!onLoadDetail || loadingEditToolId) return;
    workspaceReturnFocusSelectorRef.current = `[data-avatar-tool-edit-id="${toolId}"]`;
    if (isElectronDesktopEnvironment()) {
      if (!openAvatarToolEditorWindow('edit', toolId)) {
        setNotice(i18n('chat.avatarToolEditorOpenError', 'Could not open the tool editor.'));
        setNoticeIsError(true);
      }
      return;
    }
    const request = ++editRequestRef.current;
    setLoadingEditToolId(toolId);
    setNotice('');
    setNoticeIsError(false);
    try {
      const detail = await onLoadDetail(toolId);
      if (request !== editRequestRef.current || detail.id !== toolId) return;
      setEditDetail(detail);
      setCreateSpecialEnabled(!!detail.special);
      setView('edit');
    } catch {
      if (request !== editRequestRef.current) return;
      setNotice(i18n('chat.avatarToolUpdateLoadError', 'Could not open this tool. Please try again.'));
      setNoticeIsError(true);
    } finally {
      if (request === editRequestRef.current) setLoadingEditToolId(null);
    }
  };

  const deleteEditedTool = async () => {
    if (!onDelete || !editDetail) return;
    const session = managerSessionRef.current;
    await onDelete(editDetail.id);
    if (session !== managerSessionRef.current) return;
    setDraftSlots(slots => slots.map(toolId => toolId === editDetail.id ? null : toolId));
    setEditDetail(null);
    setCreateSpecialEnabled(false);
    setView('library');
  };

  const handleSave = () => {
    onSave(compactSlots(draftSlots));
  };

  const returnToLibrary = () => {
    setCreateSpecialEnabled(false);
    setEditDetail(null);
    setNotice('');
    setNoticeIsError(false);
    setView('library');
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
      const nextPosition = {
        left: session.startLeft + event.clientX - session.startX,
        top: session.startTop + event.clientY - session.startY,
      };
      const dialogSize = getDialogSize(dialogRef.current, viewport, preferredDialogHeight);
      setDialogPosition(view !== 'library'
        ? clampCreateDialogPosition(nextPosition, dialogSize, viewport)
        : clampDialogPosition(nextPosition, dialogSize, viewport));
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
  const dialogSize = getDialogSize(dialogRef.current, dialogViewport, preferredDialogHeight);
  const positionedCreateHeight = dialogPosition && view !== 'library'
    ? Math.max(
      1,
      Math.min(
        preferredDialogHeight,
        dialogViewport.bottom - dialogPosition.top - AVATAR_TOOL_MANAGER_VIEWPORT_GUTTER,
      ),
    )
    : null;
  const isDesktopCompactDialog = dialogViewport.compactDesktop;
  const dragTool = dragSession ? availableById.get(dragSession.toolId) : null;
  const managerDragging = !!dialogDragSession?.active || !!dragSession?.active;

  const dialogStyle = dialogPosition || isDesktopCompactDialog
    ? ({
      ...(dialogPosition ? {
        '--avatar-tool-manager-left': `${dialogPosition.left}px`,
        '--avatar-tool-manager-top': `${dialogPosition.top}px`,
        ...(positionedCreateHeight !== null ? {
          '--avatar-tool-manager-positioned-create-height': `${positionedCreateHeight}px`,
        } : {}),
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

  const editorTitle = view === 'edit'
    ? i18n('chat.avatarToolUpdateTitle', 'Edit custom tool')
    : i18n('chat.avatarToolCreateTitle', 'Create custom tool');
  const editorElement = createLimits && view !== 'library' && (view === 'create' ? !!onCreate : !!onUpdate && !!editDetail) ? (
    <AvatarToolEditorWorkspace
      title={editorTitle}
      limits={createLimits}
      dialogRef={dialogRef}
      backButtonRef={workspaceBackButtonRef}
      onBack={returnToLibrary}
      onPointerDown={stopModelDrag}
      onMouseDown={stopModelDrag}
    >
      <AvatarToolCreatePage
        key={view === 'edit' ? `${editDetail?.id}:${editDetail?.revision}` : 'create'}
        limits={createLimits}
        userName={userName}
        assistantName={assistantName}
        initialDetail={view === 'edit' ? editDetail ?? undefined : undefined}
        existingToolNames={availableTools
          .filter(tool => tool.id !== editDetail?.id)
          .map(getToolLabel)}
        notice={view === 'edit' ? notice : ''}
        onSpecialEnabledChange={setCreateSpecialEnabled}
        onCancel={returnToLibrary}
        showCancelAction={false}
        onSave={async (input) => {
          const session = managerSessionRef.current;
          if (view === 'edit' && editDetail && onUpdate) {
            try {
              await onUpdate(editDetail.id, input as UpdateLocalAvatarToolInput);
            } catch (cause) {
              if (cause instanceof LocalAvatarToolRevisionConflictError) {
                if (session !== managerSessionRef.current) return;
                setEditDetail(cause.currentDetail);
                setCreateSpecialEnabled(!!cause.currentDetail.special);
                const fallback = 'This tool changed in another window. The latest version has been loaded.';
                setNotice(i18n(
                  'chat.avatarToolRevisionConflict',
                  fallback,
                ) || fallback);
                setNoticeIsError(false);
                return;
              }
              throw cause;
            }
          } else if (view === 'create' && onCreate) {
            await onCreate(input as CreateLocalAvatarToolInput);
          }
          // 对话框已经被关掉又重开过：这次收尾属于上一个会话，别去动新会话。
          if (session !== managerSessionRef.current) return;
          setCreateSpecialEnabled(false);
          setEditDetail(null);
          setView('library');
        }}
        onDelete={view === 'edit' && onDelete ? deleteEditedTool : undefined}
      />
    </AvatarToolEditorWorkspace>
  ) : null;

  const dialogElement = editorElement ?? (
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
          data-neko-tooltip={i18n('chat.avatarToolManagerClose', 'Close')}
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
              const runnable = !!tool && validToolIds.has(tool.id);
              const label = tool ? getToolLabel(tool) : toolId !== null
                ? i18n('chat.avatarToolTemporarilyUnavailable', 'Temporarily unavailable')
                : i18n('chat.avatarToolEmptySlot', 'Empty slot');
              return (
                <div
                  key={index}
                  className={`avatar-tool-manager-slot${toolId !== null ? ' is-filled' : ' is-empty'}${toolId !== null && !runnable ? ' is-unavailable' : ''}`}
                  data-avatar-tool-drop-slot={index}
                  data-avatar-tool-id={toolId ?? ''}
                >
                  {toolId !== null ? (
                    <button
                      className="avatar-tool-manager-slot-card"
                      type="button"
                      disabled={!runnable}
                      data-avatar-tool-slot-index={index}
                      onPointerDown={runnable && tool
                        ? (event) => startDrag({ kind: 'slot', toolId: tool.id, slotIndex: index }, event)
                        : undefined}
                      onPointerMove={updateDrag}
                      onPointerUp={finishDrag}
                      onPointerCancel={cancelDrag}
                    >
                      {tool ? <img
                        className="avatar-tool-manager-tool-image"
                        src={withAvatarToolAssetVersion(tool.iconImagePath)}
                        style={getToolImageStyle(tool)}
                        alt=""
                        aria-hidden="true"
                      /> : null}
                      <span>{label}</span>
                      {tool && !runnable ? (
                        <span className="avatar-tool-manager-library-status">
                          {i18n('chat.avatarToolNotYetEquippable', 'Not yet equippable')}
                        </span>
                      ) : null}
                    </button>
                  ) : (
                    <span className="avatar-tool-manager-empty-slot">{label}</span>
                  )}
                  {toolId !== null ? (
                    <button
                      className="avatar-tool-manager-remove"
                      type="button"
                      aria-label={`${i18n('chat.avatarToolRemove', 'Remove')} ${label}`}
                      data-neko-tooltip={`${i18n('chat.avatarToolRemove', 'Remove')} ${label}`}
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
                const runnable = validToolIds.has(tool.id);
                const localToolId = isLocalAvatarToolId(tool.id) ? tool.id : null;
                const loadingEdit = loadingEditToolId === tool.id;
                return (
                  <div className="avatar-tool-manager-library-item" key={tool.id}>
                    <button
                      className={`avatar-tool-manager-library-card${equipped ? ' is-equipped' : ''}`}
                      type="button"
                      aria-pressed={equipped}
                      disabled={loadingEdit || !runnable}
                      data-avatar-tool-library-id={tool.id}
                      onClick={() => handleLibraryClick(tool.id)}
                      onPointerDown={equipped || !runnable ? undefined : (event) => startDrag({ kind: 'library', toolId: tool.id }, event)}
                      onPointerMove={updateDrag}
                      onPointerUp={finishDrag}
                      onPointerCancel={cancelDrag}
                    >
                      <img
                        className="avatar-tool-manager-tool-image"
                        src={withAvatarToolAssetVersion(tool.iconImagePath)}
                        style={getToolImageStyle(tool)}
                        alt=""
                        aria-hidden="true"
                      />
                      <span className="avatar-tool-manager-library-label">{label}</span>
                      <span className="avatar-tool-manager-library-status">
                        {!runnable
                          ? i18n('chat.avatarToolNotYetEquippable', 'Not yet equippable')
                          : equipped
                          ? i18n('chat.avatarToolEquipped', 'Equipped')
                          : i18n('chat.avatarToolEquip', 'Equip')}
                      </span>
                    </button>
                    {localToolId && onLoadDetail && onUpdate ? (
                      <button
                        className="avatar-tool-manager-modify"
                        type="button"
                        disabled={!!loadingEditToolId}
                        data-avatar-tool-edit-id={localToolId}
                        aria-label={i18n('chat.avatarToolUpdateOpen', 'Edit {{name}}', { name: label })}
                        data-neko-tooltip={i18n('chat.avatarToolUpdateOpen', 'Edit {{name}}', { name: label })}
                        onClick={(event) => {
                          event.stopPropagation();
                          void openEdit(localToolId);
                        }}
                        onPointerDown={event => event.stopPropagation()}
                      >
                        {loadingEdit
                          ? i18n('chat.avatarToolUpdateLoading', 'Opening…')
                          : i18n('chat.avatarToolUpdateModify', 'Edit')}
                      </button>
                    ) : null}
                  </div>
                );
              })}
              {onCreate ? (
                <button
                  className="avatar-tool-manager-library-card avatar-tool-manager-create-card"
                  type="button"
                  data-avatar-tool-create
                  disabled={!createLimits || !catalogAuthoritativeLoaded}
                  onClick={() => {
                    workspaceReturnFocusSelectorRef.current = '[data-avatar-tool-create]';
                    setCreateSpecialEnabled(false);
                    setNotice('');
                    setNoticeIsError(false);
                    if (isElectronDesktopEnvironment()) {
                      if (!openAvatarToolEditorWindow('create')) {
                        setNotice(i18n('chat.avatarToolEditorOpenError', 'Could not open the tool editor.'));
                        setNoticeIsError(true);
                      }
                      return;
                    }
                    setView('create');
                  }}
                >
                  <span className="avatar-tool-manager-create-plus" aria-hidden="true">+</span>
                  <span className="avatar-tool-manager-library-label">
                    {i18n('chat.avatarToolCreateNew', 'Create tool')}
                  </span>
                </button>
              ) : null}
            </div>
          ) : (
            <p className="avatar-tool-manager-empty-library">
              {i18n('chat.avatarToolNoAvailableTools', 'No tools available')}
            </p>
          )}
        </section>
      </div>

      {notice ? (
        <p id="avatar-tool-manager-notice" className="avatar-tool-manager-notice" role={noticeIsError ? 'alert' : 'status'}>
          {notice}
        </p>
      ) : null}

      {catalogRefreshFailed ? (
        <p className="avatar-tool-manager-notice" role="alert">
          {catalogAuthoritativeLoaded
            ? i18n('chat.avatarToolRefreshError', 'Could not refresh local tools. The previous list is still available.')
            : i18n('chat.avatarToolLoadError', 'Could not load custom tools. Please try again.')}
        </p>
      ) : !catalogAuthoritativeLoaded ? (
        <p className="avatar-tool-manager-notice" role="status">
          {i18n('chat.avatarToolLoading', 'Loading custom tools…')}
        </p>
      ) : null}

      <footer className="avatar-tool-manager-actions">
        <button className="avatar-tool-manager-action secondary" type="button" onClick={onCancel}>
          {i18n('chat.avatarToolCancel', 'Cancel')}
        </button>
        <button
          className="avatar-tool-manager-action primary"
          type="button"
          disabled={catalogSaveBlocked}
          onClick={handleSave}
        >
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
            src={withAvatarToolAssetVersion(dragTool.iconImagePath)}
            style={getToolImageStyle(dragTool)}
            alt=""
          />
        </div>
      ) : null}
    </section>
  );

  return createPortal(
    <>
      <div
        className={`avatar-tool-manager-overlay${isDesktopMode ? ' is-desktop' : ''}${editorElement ? ' is-editor-workspace' : ''}`}
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
