import clsx from 'clsx';
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { i18n } from './i18n';
import MessageBlockView from './MessageBlockView';
import { type ChatMessage, type CompactHistoryDragStatePayload, type MessageAction } from './message-schema';

export const COMPACT_EXPORT_SELECTION_LIMIT = 100;

const COMPACT_EXPORT_BOTTOM_THRESHOLD = 30;
const COMPACT_EXPORT_CLICK_MOVE_THRESHOLD = 6;
const COMPACT_EXPORT_DRAG_MOVE_THRESHOLD = 8;
const COMPACT_EXPORT_TOUCH_SCROLL_ANGLE_RATIO = 1.35;
const COMPACT_HISTORY_SCROLL_SETTLE_FRAMES = 36;
export const COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS = 860;
const COMPACT_HISTORY_RETURN_ANIMATION_MS = 260;
const COMPACT_HISTORY_SEND_ANIMATION_MS = 340;
export const COMPACT_HISTORY_ENTER_DELAY_STEP_MS = 42;
export const COMPACT_HISTORY_ENTER_DELAY_MAX_MS = 420;
export const COMPACT_HISTORY_EXIT_DELAY_STEP_MS = 30;
export const COMPACT_HISTORY_EXIT_DELAY_MAX_MS = 320;

export function computeCompactHistoryEnterDelay(index: number, totalMessages: number): string {
  return `${Math.min(
    Math.max(totalMessages - 1 - index, 0) * COMPACT_HISTORY_ENTER_DELAY_STEP_MS,
    COMPACT_HISTORY_ENTER_DELAY_MAX_MS,
  )}ms`;
}

export function computeCompactHistoryExitDelay(index: number): string {
  return `${Math.min(index * COMPACT_HISTORY_EXIT_DELAY_STEP_MS, COMPACT_HISTORY_EXIT_DELAY_MAX_MS)}ms`;
}

export type CompactExportFormat = 'markdown' | 'image';
export type CompactExportImageStyle = 'neko' | 'original' | 'poster' | 'lyrics';
export type CompactExportImageFormat = 'png' | 'jpeg' | 'webp';

export type CompactExportActionRequest = {
  messageIds: string[];
  format: CompactExportFormat;
  imageStyle: CompactExportImageStyle;
  imageFormat: CompactExportImageFormat;
};

export type CompactExportPreviewResult =
  | { previewKind: 'empty' }
  | { previewKind: 'document'; previewDocument: string }
  | { previewKind: 'image'; previewUrl: string };

type CompactExportPreviewState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: CompactExportPreviewResult }
  | { status: 'failed'; message: string };

type CompactExportHistoryPanelProps = {
  messages: ChatMessage[];
  selectedIds: Set<string>;
  selectedCount: number;
  selectableCount: number;
  autoScrollToBottom: boolean;
  previewOpen: boolean;
  controlsOpen: boolean;
  choiceLayerAbove: boolean;
  visibilityState?: 'open' | 'closing';
  failedStatusLabel: string;
  onAutoScrollToBottomChange: (enabled: boolean) => void;
  onToggleMessage: (messageId: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onInvertSelection: () => void;
  onRequestPreview: () => void;
  onClosePreview: () => void;
  onBuildPreview: (request: CompactExportActionRequest) => Promise<CompactExportPreviewResult> | CompactExportPreviewResult;
  onCopyExport: (request: CompactExportActionRequest) => Promise<void> | void;
  onDownloadExport: (request: CompactExportActionRequest) => Promise<void> | void;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
  isDropTargetAt?: (point: CompactHistoryDropPoint) => boolean;
  onDropToTarget?: (request: CompactHistoryDropRequest) => Promise<boolean | void> | boolean | void;
  onDragStateChange?: (state: CompactHistoryDragStatePayload) => void;
};

export type CompactHistoryDragType = 'image' | 'bubble';
type PointerIntentPhase = 'pending' | 'click' | 'scroll' | 'imageDrag' | 'bubbleDrag' | 'cancelled';

type CompactHistoryRect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};

export type CompactHistoryImageDragPayload = {
  type: 'image';
  url: string;
  alt?: string;
  width?: number;
  height?: number;
};

export type CompactHistoryBubbleDragPayload = {
  type: 'bubble';
  role: ChatMessage['role'];
  blocks: ChatMessage['blocks'];
};

export type CompactHistoryDragPayload = CompactHistoryImageDragPayload | CompactHistoryBubbleDragPayload;

export type CompactHistoryDropPoint = {
  clientX: number;
  clientY: number;
  sessionId?: string;
};

export type CompactHistoryDropRequest = {
  type: CompactHistoryDragType;
  sessionId: string;
  messageId: string;
  blockIndex?: number;
  payload: CompactHistoryDragPayload;
  point: CompactHistoryDropPoint;
};

type CompactHistoryDragSource =
  | {
      type: 'image';
      blockIndex: number;
      payload: CompactHistoryImageDragPayload;
    }
  | {
      type: 'bubble';
      payload: CompactHistoryBubbleDragPayload;
    };

type ActiveCompactHistoryDrag = {
  sessionId: string;
  type: CompactHistoryDragType;
  phase: 'dragging' | 'returning' | 'sending';
  messageId: string;
  role: ChatMessage['role'];
  blockIndex?: number;
  payload: CompactHistoryDragPayload;
  originRect: CompactHistoryRect;
  sourceFrameRect: CompactHistoryRect;
  originElement: HTMLElement | null;
  pointerOffset: { x: number; y: number };
  pointerClient: { x: number; y: number };
  overDropTarget: boolean;
};

type CompactHistoryDragRebaseDetail = {
  sessionId?: string;
  deltaX?: number;
  deltaY?: number;
};

type CompactHistoryDesktopDropTargetDetail = {
  active?: boolean;
  sessionId?: string;
  seq?: number;
  desktopOverAvatar?: boolean | null;
  timestamp?: number;
};

type CompactHistoryElasticGeometry = {
  path: string;
  shellPath?: string;
  pull: number;
  opacity: number;
  nextCurvePoint: { x: number; y: number };
};

type CompactHistorySourceNub = {
  center: { x: number; y: number };
  width: number;
  height: number;
};

type CompactHistoryBubbleShellMetrics = {
  expandLeft: number;
  expandRight: number;
  expandTop: number;
  expandBottom: number;
  shellScaleX: number;
  shellScaleY: number;
  shellOriginX: string;
  shellOriginY: string;
  contentScaleX: number;
  contentScaleY: number;
  radiusTopLeft: number;
  radiusTopRight: number;
  radiusBottomRight: number;
  radiusBottomLeft: number;
};

type PointerIntentState = {
  sessionId: string;
  id: number;
  x: number;
  y: number;
  messageId: string;
  pointerType: string;
  phase: PointerIntentPhase;
  source: CompactHistoryDragSource;
  originRect: CompactHistoryRect;
  sourceFrameRect: CompactHistoryRect;
  originElement: HTMLElement | null;
  pointerOffset: { x: number; y: number };
  autoScrollToBottomOnStart: boolean;
};

type CompactHistoryBubbleTone = {
  group: 'first' | 'same' | 'switch';
  complexity: 'plain' | 'rich';
  style: CSSProperties & Record<string, string>;
};

export function isCompactExportMessageSelectable(message: ChatMessage) {
  return !!message.id && message.status !== 'sending';
}

function findSelectionIgnoredTarget(target: EventTarget | null, currentTarget: EventTarget, includeImage = true) {
  if (!(target instanceof Element) || !(currentTarget instanceof Element)) return null;
  const selector = includeImage
    ? 'a, button, input, textarea, select, [data-compact-history-ignore-selection="true"], .message-block-image'
    : 'a, button, input, textarea, select, [data-compact-history-ignore-selection="true"]';
  const interactive = target.closest(selector);
  return interactive && interactive !== currentTarget ? interactive : null;
}

function isSelectionIgnoredTarget(target: EventTarget | null, currentTarget: EventTarget) {
  return !!findSelectionIgnoredTarget(target, currentTarget, true);
}

function snapshotCompactHistoryRect(rect: DOMRect | ClientRect): CompactHistoryRect {
  return {
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
  };
}

function translateCompactHistoryRect(rect: CompactHistoryRect, dx: number, dy: number): CompactHistoryRect {
  return {
    left: rect.left + dx,
    top: rect.top + dy,
    right: rect.right + dx,
    bottom: rect.bottom + dy,
    width: rect.width,
    height: rect.height,
  };
}

function translateCompactHistoryActiveDrag(
  activeDrag: ActiveCompactHistoryDrag,
  dx: number,
  dy: number,
): ActiveCompactHistoryDrag {
  return {
    ...activeDrag,
    originRect: translateCompactHistoryRect(activeDrag.originRect, dx, dy),
    sourceFrameRect: translateCompactHistoryRect(activeDrag.sourceFrameRect, dx, dy),
    pointerClient: {
      x: activeDrag.pointerClient.x + dx,
      y: activeDrag.pointerClient.y + dy,
    },
  };
}

function translateCompactHistoryPointerIntent(
  intent: PointerIntentState,
  dx: number,
  dy: number,
): PointerIntentState {
  return {
    ...intent,
    x: intent.x + dx,
    y: intent.y + dy,
    originRect: translateCompactHistoryRect(intent.originRect, dx, dy),
    sourceFrameRect: translateCompactHistoryRect(intent.sourceFrameRect, dx, dy),
  };
}

function createCompactHistoryDragSessionId() {
  return `compact-history-drag-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function getCompactHistoryReducedMotionPreference() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

function shouldRequestDesktopBoundsForCompactHistoryDrag() {
  if (typeof window === 'undefined') return false;
  const desktopWindow = window as Window & {
    __NEKO_MULTI_WINDOW__?: boolean;
    __nekoDesktopCompactLayout?: unknown;
  };
  return desktopWindow.__NEKO_MULTI_WINDOW__ === true || !!desktopWindow.__nekoDesktopCompactLayout;
}

function rectFromCompactHistoryEdges(left: number, top: number, right: number, bottom: number): CompactHistoryRect {
  return {
    left,
    top,
    right,
    bottom,
    width: Math.max(right - left, 0),
    height: Math.max(bottom - top, 0),
  };
}

function inflateCompactHistoryRect(rect: CompactHistoryRect, padding: number): CompactHistoryRect {
  return rectFromCompactHistoryEdges(
    rect.left - padding,
    rect.top - padding,
    rect.right + padding,
    rect.bottom + padding,
  );
}

function unionCompactHistoryRects(rects: CompactHistoryRect[]): CompactHistoryRect | null {
  if (!rects.length) return null;
  return rectFromCompactHistoryEdges(
    Math.min(...rects.map(rect => rect.left)),
    Math.min(...rects.map(rect => rect.top)),
    Math.max(...rects.map(rect => rect.right)),
    Math.max(...rects.map(rect => rect.bottom)),
  );
}

function rectFromCompactHistorySourceNub(nub: CompactHistorySourceNub): CompactHistoryRect {
  return rectFromCompactHistoryEdges(
    nub.center.x - nub.width / 2,
    nub.center.y - nub.height / 2,
    nub.center.x + nub.width / 2,
    nub.center.y + nub.height / 2,
  );
}

function resolveImageDragSource(
  target: EventTarget | null,
  currentTarget: EventTarget,
  message: ChatMessage,
): CompactHistoryDragSource | null {
  if (!(target instanceof Element) || !(currentTarget instanceof Element)) return null;
  const imageElement = target.closest('.message-block-image');
  if (!imageElement || !currentTarget.contains(imageElement)) return null;
  const imageElements = Array.from(currentTarget.querySelectorAll('.message-block-image'));
  const imageOrdinal = imageElements.indexOf(imageElement);
  if (imageOrdinal < 0) return null;
  const imageBlocks = message.blocks
    .map((block, blockIndex) => ({ block, blockIndex }))
    .filter((entry): entry is { block: Extract<ChatMessage['blocks'][number], { type: 'image' }>; blockIndex: number } => entry.block.type === 'image');
  const match = imageBlocks[imageOrdinal];
  if (!match) return null;
  return {
    type: 'image',
    blockIndex: match.blockIndex,
    payload: {
      type: 'image',
      url: match.block.url,
      alt: match.block.alt,
      width: match.block.width,
      height: match.block.height,
    },
  };
}

function resolveImageDragOriginElement(target: EventTarget | null, currentTarget: HTMLElement) {
  if (!(target instanceof Element)) return currentTarget;
  const imageElement = target.closest('.message-block-image');
  return imageElement instanceof HTMLElement && currentTarget.contains(imageElement)
    ? imageElement
    : currentTarget;
}

function resolveBubbleDragOriginElement(target: EventTarget | null, currentTarget: HTMLElement) {
  if (!(target instanceof Element)) return currentTarget;
  const bubbleElement = target.closest('.compact-export-history-bubble');
  return bubbleElement instanceof HTMLElement && currentTarget.contains(bubbleElement)
    ? bubbleElement
    : currentTarget;
}

function resolveBubbleDragSource(message: ChatMessage): CompactHistoryDragSource {
  return {
    type: 'bubble',
    payload: {
      type: 'bubble',
      role: message.role,
      blocks: message.blocks,
    },
  };
}

function getDragPhaseForSource(source: CompactHistoryDragSource): PointerIntentPhase {
  return source.type === 'image' ? 'imageDrag' : 'bubbleDrag';
}

function getDragTypeForSource(source: CompactHistoryDragSource): CompactHistoryDragType {
  return source.type;
}

function shouldTreatPointerMoveAsScroll(pointerType: string, dx: number, dy: number) {
  if (pointerType === 'mouse') return false;
  return Math.abs(dy) > Math.abs(dx) * COMPACT_EXPORT_TOUCH_SCROLL_ANGLE_RATIO;
}

function isDraggingPhase(phase: PointerIntentPhase) {
  return phase === 'imageDrag' || phase === 'bubbleDrag';
}

function getCompactHistoryDragTransform(activeDrag: ActiveCompactHistoryDrag) {
  const left = activeDrag.pointerClient.x - activeDrag.pointerOffset.x;
  const top = activeDrag.pointerClient.y - activeDrag.pointerOffset.y;
  return {
    '--compact-history-drag-left': `${left}px`,
    '--compact-history-drag-top': `${top}px`,
    '--compact-history-drag-width': `${Math.max(activeDrag.originRect.width, 1)}px`,
    '--compact-history-drag-height': `${Math.max(activeDrag.originRect.height, 1)}px`,
  } as CSSProperties & Record<string, string>;
}

function getCompactHistoryDragPalette(activeDrag: ActiveCompactHistoryDrag) {
  if (activeDrag.role === 'user') {
    return {
      '--compact-history-drag-surface-rgb': '208 231 255',
      '--compact-history-drag-edge-rgb': '73 145 217',
      '--compact-history-drag-shadow-rgb': '44 104 168',
      '--compact-history-drag-text': '#142033',
    };
  }
  if (activeDrag.role === 'system') {
    return {
      '--compact-history-drag-surface-rgb': '227 233 243',
      '--compact-history-drag-edge-rgb': '126 145 172',
      '--compact-history-drag-shadow-rgb': '85 103 132',
      '--compact-history-drag-text': '#607086',
    };
  }
  return {
    '--compact-history-drag-surface-rgb': '255 255 255',
    '--compact-history-drag-edge-rgb': '77 160 220',
    '--compact-history-drag-shadow-rgb': '42 93 145',
    '--compact-history-drag-text': '#273042',
  };
}

function getCompactHistoryDragMotion(activeDrag: ActiveCompactHistoryDrag) {
  const originCenter = getCompactHistoryOriginCenter(activeDrag);
  const dragCenter = getCompactHistoryDragCenter(activeDrag);
  const dx = dragCenter.x - originCenter.x;
  const dy = dragCenter.y - originCenter.y;
  const rawDistance = Math.hypot(dx, dy);
  const distance = Math.max(rawDistance, 1);
  const fallback = getCompactHistoryFallbackDirection(activeDrag);
  const ux = rawDistance > 0.001 ? dx / distance : fallback.x;
  const uy = rawDistance > 0.001 ? dy / distance : fallback.y;
  const pull = clampCompactHistoryValue(distance / 330, 0, 1);
  return {
    ux,
    uy,
    pull,
    scaleX: clampCompactHistoryValue(1 + pull * (Math.abs(ux) * 0.11 - Math.abs(uy) * 0.035), 0.955, 1.125),
    scaleY: clampCompactHistoryValue(1 + pull * (Math.abs(uy) * 0.095 - Math.abs(ux) * 0.042), 0.948, 1.11),
  };
}

function getCompactHistoryBubbleBaseRadii(activeDrag: ActiveCompactHistoryDrag) {
  if (activeDrag.role === 'user') {
    return {
      topLeft: 16,
      topRight: 8,
      bottomRight: 16,
      bottomLeft: 16,
    };
  }
  return {
    topLeft: 8,
    topRight: 16,
    bottomRight: 16,
    bottomLeft: 16,
  };
}

function getCompactHistoryBubbleShellMetrics(activeDrag: ActiveCompactHistoryDrag): CompactHistoryBubbleShellMetrics {
  const { ux, uy, pull } = getCompactHistoryDragMotion(activeDrag);
  const horizontal = Math.abs(ux) >= Math.abs(uy) * 0.72;
  const vertical = Math.abs(uy) >= Math.abs(ux) * 0.72;
  const leftPull = horizontal ? Math.max(ux, 0) : Math.max(ux, 0) * 0.42;
  const rightPull = horizontal ? Math.max(-ux, 0) : Math.max(-ux, 0) * 0.42;
  const topPull = vertical ? Math.max(uy, 0) : Math.max(uy, 0) * 0.34;
  const bottomPull = vertical ? Math.max(-uy, 0) : Math.max(-uy, 0) * 0.34;
  const sizeRatio = clampCompactHistoryValue(Math.min(activeDrag.originRect.width, activeDrag.originRect.height) / 82, 0.72, 1.35);
  const sideReach = 6 + pull * 13 * sizeRatio;
  const verticalReach = 4 + pull * 9 * sizeRatio;
  const base = getCompactHistoryBubbleBaseRadii(activeDrag);
  const cornerLift = pull * 13;
  const softTopLeft = base.topLeft + cornerLift * Math.max(leftPull, topPull * 0.8);
  const softTopRight = base.topRight + cornerLift * Math.max(rightPull, topPull * 0.8);
  const softBottomRight = base.bottomRight + cornerLift * Math.max(rightPull, bottomPull * 0.8);
  const softBottomLeft = base.bottomLeft + cornerLift * Math.max(leftPull, bottomPull * 0.8);
  return {
    expandLeft: leftPull * sideReach,
    expandRight: rightPull * sideReach,
    expandTop: topPull * verticalReach,
    expandBottom: bottomPull * verticalReach,
    shellScaleX: clampCompactHistoryValue(1 + pull * (Math.abs(ux) * 0.115 + Math.abs(uy) * 0.018), 1, 1.14),
    shellScaleY: clampCompactHistoryValue(1 + pull * (Math.abs(uy) * 0.08 - Math.abs(ux) * 0.025), 0.975, 1.09),
    shellOriginX: ux > 0.18 ? '100%' : ux < -0.18 ? '0%' : '50%',
    shellOriginY: uy > 0.18 ? '100%' : uy < -0.18 ? '0%' : '50%',
    contentScaleX: clampCompactHistoryValue(1 + pull * Math.abs(ux) * 0.018, 1, 1.018),
    contentScaleY: clampCompactHistoryValue(1 - pull * Math.abs(ux) * 0.012 + pull * Math.abs(uy) * 0.01, 0.988, 1.01),
    radiusTopLeft: clampCompactHistoryValue(softTopLeft, 8, 30),
    radiusTopRight: clampCompactHistoryValue(softTopRight, 8, 30),
    radiusBottomRight: clampCompactHistoryValue(softBottomRight, 10, 30),
    radiusBottomLeft: clampCompactHistoryValue(softBottomLeft, 10, 30),
  };
}

function getCompactHistoryDragVisualStyle(activeDrag: ActiveCompactHistoryDrag) {
  const { ux, uy, scaleX, scaleY } = getCompactHistoryDragMotion(activeDrag);
  const shell = getCompactHistoryBubbleShellMetrics(activeDrag);
  return {
    ...getCompactHistoryDragTransform(activeDrag),
    ...getCompactHistoryDragPalette(activeDrag),
    '--compact-history-drag-skew': '0deg',
    '--compact-history-drag-scale-x': `${scaleX}`,
    '--compact-history-drag-scale-y': `${scaleY}`,
    '--compact-history-drag-origin-x': Math.abs(ux) > 0.28 ? (ux > 0 ? '0%' : '100%') : '50%',
    '--compact-history-drag-origin-y': Math.abs(uy) > 0.28 ? (uy > 0 ? '0%' : '100%') : '50%',
    '--compact-history-bubble-shell-left': `${-shell.expandLeft}px`,
    '--compact-history-bubble-shell-right': `${-shell.expandRight}px`,
    '--compact-history-bubble-shell-top': `${-shell.expandTop}px`,
    '--compact-history-bubble-shell-bottom': `${-shell.expandBottom}px`,
    '--compact-history-bubble-shell-scale-x': `${shell.shellScaleX}`,
    '--compact-history-bubble-shell-scale-y': `${shell.shellScaleY}`,
    '--compact-history-bubble-shell-origin-x': shell.shellOriginX,
    '--compact-history-bubble-shell-origin-y': shell.shellOriginY,
    '--compact-history-bubble-content-scale-x': `${shell.contentScaleX}`,
    '--compact-history-bubble-content-scale-y': `${shell.contentScaleY}`,
    '--compact-history-bubble-radius-tl': `${shell.radiusTopLeft}px`,
    '--compact-history-bubble-radius-tr': `${shell.radiusTopRight}px`,
    '--compact-history-bubble-radius-br': `${shell.radiusBottomRight}px`,
    '--compact-history-bubble-radius-bl': `${shell.radiusBottomLeft}px`,
  } as CSSProperties & Record<string, string>;
}

function clampCompactHistoryValue(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function getCompactHistoryDragCenter(activeDrag: ActiveCompactHistoryDrag) {
  return {
    x: activeDrag.pointerClient.x - activeDrag.pointerOffset.x + activeDrag.originRect.width / 2,
    y: activeDrag.pointerClient.y - activeDrag.pointerOffset.y + activeDrag.originRect.height / 2,
  };
}

function getCompactHistoryOriginCenter(activeDrag: ActiveCompactHistoryDrag) {
  return {
    x: activeDrag.originRect.left + activeDrag.originRect.width / 2,
    y: activeDrag.originRect.top + activeDrag.originRect.height / 2,
  };
}

function getCompactHistoryDragRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect {
  const left = activeDrag.pointerClient.x - activeDrag.pointerOffset.x;
  const top = activeDrag.pointerClient.y - activeDrag.pointerOffset.y;
  const width = activeDrag.originRect.width;
  const height = activeDrag.originRect.height;
  return {
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
  };
}

function getCompactHistoryBubbleShellRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect {
  const rect = getCompactHistoryDragRect(activeDrag);
  const shell = getCompactHistoryBubbleShellMetrics(activeDrag);
  const expanded = {
    left: rect.left - shell.expandLeft,
    top: rect.top - shell.expandTop,
    right: rect.right + shell.expandRight,
    bottom: rect.bottom + shell.expandBottom,
  };
  const originX = shell.shellOriginX === '0%' ? expanded.left : shell.shellOriginX === '100%' ? expanded.right : (expanded.left + expanded.right) / 2;
  const originY = shell.shellOriginY === '0%' ? expanded.top : shell.shellOriginY === '100%' ? expanded.bottom : (expanded.top + expanded.bottom) / 2;
  const left = originX + (expanded.left - originX) * shell.shellScaleX;
  const right = originX + (expanded.right - originX) * shell.shellScaleX;
  const top = originY + (expanded.top - originY) * shell.shellScaleY;
  const bottom = originY + (expanded.bottom - originY) * shell.shellScaleY;
  return {
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
  };
}

function getCompactHistoryScaledDragRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect {
  const rect = getCompactHistoryDragRect(activeDrag);
  if (activeDrag.type === 'bubble') return getCompactHistoryBubbleShellRect(activeDrag);
  const { ux, uy, scaleX, scaleY } = getCompactHistoryDragMotion(activeDrag);
  const originX = Math.abs(ux) > 0.28 ? (ux > 0 ? rect.left : rect.right) : rect.left + rect.width / 2;
  const originY = Math.abs(uy) > 0.28 ? (uy > 0 ? rect.top : rect.bottom) : rect.top + rect.height / 2;
  const left = originX + (rect.left - originX) * scaleX;
  const right = originX + (rect.right - originX) * scaleX;
  const top = originY + (rect.top - originY) * scaleY;
  const bottom = originY + (rect.bottom - originY) * scaleY;
  return {
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
  };
}

function getCompactHistoryConnectionVisualRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect | null {
  const sourceNubRect = rectFromCompactHistorySourceNub(getCompactHistorySourceNub(activeDrag));
  const dragVisualRect = getCompactHistoryScaledDragRect(activeDrag);
  const connectionRect = unionCompactHistoryRects([sourceNubRect, dragVisualRect]);
  return connectionRect ? inflateCompactHistoryRect(connectionRect, 24) : null;
}

function getCompactHistoryDragHitRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect {
  const dragRect = getCompactHistoryDragRect(activeDrag);
  return inflateCompactHistoryRect(dragRect, activeDrag.type === 'image' ? 10 : 14);
}

function buildCompactHistoryDragState(
  activeDrag: ActiveCompactHistoryDrag,
  seq: number,
): CompactHistoryDragStatePayload {
  return {
    active: true,
    sessionId: activeDrag.sessionId,
    seq,
    phase: activeDrag.phase,
    dragType: activeDrag.type,
    messageId: activeDrag.messageId,
    blockIndex: activeDrag.blockIndex,
    pointerClient: {
      clientX: activeDrag.pointerClient.x,
      clientY: activeDrag.pointerClient.y,
    },
    sourceFrameRect: activeDrag.sourceFrameRect,
    dragVisualRect: getCompactHistoryScaledDragRect(activeDrag),
    connectionVisualRect: getCompactHistoryConnectionVisualRect(activeDrag),
    dragHitRect: getCompactHistoryDragHitRect(activeDrag),
    overTarget: activeDrag.overDropTarget,
    needsDesktopBounds: shouldRequestDesktopBoundsForCompactHistoryDrag(),
    reducedMotion: getCompactHistoryReducedMotionPreference(),
    timestamp: Date.now(),
  };
}

function getCompactHistoryBubbleShellPath(activeDrag: ActiveCompactHistoryDrag, rect: CompactHistoryRect) {
  const shell = getCompactHistoryBubbleShellMetrics(activeDrag);
  const { ux, uy, pull } = getCompactHistoryDragMotion(activeDrag);
  const maxRadius = Math.max(Math.min(rect.width, rect.height) / 2 - 0.5, 1);
  const topLeft = clampCompactHistoryValue(shell.radiusTopLeft, 1, maxRadius);
  const topRight = clampCompactHistoryValue(shell.radiusTopRight, 1, maxRadius);
  const bottomRight = clampCompactHistoryValue(shell.radiusBottomRight, 1, maxRadius);
  const bottomLeft = clampCompactHistoryValue(shell.radiusBottomLeft, 1, maxRadius);
  const sideCapDepth = clampCompactHistoryValue(rect.height * (0.16 + pull * 0.08), 7, 22);
  const topBottomCapDepth = clampCompactHistoryValue(rect.width * (0.06 + pull * 0.035), 7, 22);
  const horizontalEdgeBend = clampCompactHistoryValue(rect.height * (0.09 + pull * 0.11), 9, 28);
  const verticalEdgeBend = clampCompactHistoryValue(rect.width * (0.035 + pull * 0.055), 9, 28);
  const nearSide = Math.abs(ux) >= Math.abs(uy)
    ? (ux > 0 ? 'left' : 'right')
    : (uy > 0 ? 'top' : 'bottom');
  if (nearSide === 'left') {
    return [
      `M ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      `C ${Number(rect.left - horizontalEdgeBend).toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.right - topRight - rect.width * 0.2).toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.right - topRight).toFixed(2)} ${rect.top.toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.top.toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.top + topRight).toFixed(2)}`,
      `L ${rect.right.toFixed(2)} ${Number(rect.bottom - bottomRight).toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.right - bottomRight).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `C ${Number(rect.right - bottomRight - rect.width * 0.2).toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.left - horizontalEdgeBend).toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.left + bottomLeft).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `C ${Number(rect.left - sideCapDepth).toFixed(2)} ${Number(rect.bottom - rect.height * 0.18).toFixed(2)} ${Number(rect.left - sideCapDepth).toFixed(2)} ${Number(rect.top + rect.height * 0.18).toFixed(2)} ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      'Z',
    ].join(' ');
  }
  if (nearSide === 'right') {
    return [
      `M ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      `C ${Number(rect.left + topLeft + rect.width * 0.2).toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.right + horizontalEdgeBend).toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.right - topRight).toFixed(2)} ${rect.top.toFixed(2)}`,
      `C ${Number(rect.right + sideCapDepth).toFixed(2)} ${Number(rect.top + rect.height * 0.18).toFixed(2)} ${Number(rect.right + sideCapDepth).toFixed(2)} ${Number(rect.bottom - rect.height * 0.18).toFixed(2)} ${Number(rect.right - bottomRight).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `C ${Number(rect.right + horizontalEdgeBend).toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.left + bottomLeft + rect.width * 0.2).toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.left + bottomLeft).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.bottom.toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.bottom - bottomLeft).toFixed(2)}`,
      `L ${rect.left.toFixed(2)} ${Number(rect.top + topLeft).toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      'Z',
    ].join(' ');
  }
  if (nearSide === 'top') {
    return [
      `M ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      `C ${Number(rect.left + rect.width * 0.18).toFixed(2)} ${Number(rect.top - topBottomCapDepth).toFixed(2)} ${Number(rect.right - rect.width * 0.18).toFixed(2)} ${Number(rect.top - topBottomCapDepth).toFixed(2)} ${Number(rect.right - topRight).toFixed(2)} ${rect.top.toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.top.toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.top + topRight).toFixed(2)}`,
      `C ${rect.right.toFixed(2)} ${Number(rect.top - verticalEdgeBend).toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.bottom - bottomRight - rect.height * 0.2).toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.bottom - bottomRight).toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.right - bottomRight).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `L ${Number(rect.left + bottomLeft).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.bottom.toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.bottom - bottomLeft).toFixed(2)}`,
      `C ${rect.left.toFixed(2)} ${Number(rect.bottom - bottomLeft - rect.height * 0.2).toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.top - verticalEdgeBend).toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.top + topLeft).toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      'Z',
    ].join(' ');
  }
  if (nearSide === 'bottom') {
    return [
      `M ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      `L ${Number(rect.right - topRight).toFixed(2)} ${rect.top.toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.top.toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.top + topRight).toFixed(2)}`,
      `C ${rect.right.toFixed(2)} ${Number(rect.top + topRight + rect.height * 0.2).toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.bottom + verticalEdgeBend).toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.bottom - bottomRight).toFixed(2)}`,
      `Q ${rect.right.toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.right - bottomRight).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `C ${Number(rect.right - rect.width * 0.18).toFixed(2)} ${Number(rect.bottom + topBottomCapDepth).toFixed(2)} ${Number(rect.left + rect.width * 0.18).toFixed(2)} ${Number(rect.bottom + topBottomCapDepth).toFixed(2)} ${Number(rect.left + bottomLeft).toFixed(2)} ${rect.bottom.toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.bottom.toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.bottom - bottomLeft).toFixed(2)}`,
      `C ${rect.left.toFixed(2)} ${Number(rect.bottom + verticalEdgeBend).toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.top + topLeft + rect.height * 0.2).toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.top + topLeft).toFixed(2)}`,
      `Q ${rect.left.toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
      'Z',
    ].join(' ');
  }
  return [
    `M ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
    `L ${Number(rect.right - topRight).toFixed(2)} ${rect.top.toFixed(2)}`,
    `Q ${rect.right.toFixed(2)} ${rect.top.toFixed(2)} ${rect.right.toFixed(2)} ${Number(rect.top + topRight).toFixed(2)}`,
    `L ${rect.right.toFixed(2)} ${Number(rect.bottom - bottomRight).toFixed(2)}`,
    `Q ${rect.right.toFixed(2)} ${rect.bottom.toFixed(2)} ${Number(rect.right - bottomRight).toFixed(2)} ${rect.bottom.toFixed(2)}`,
    `L ${Number(rect.left + bottomLeft).toFixed(2)} ${rect.bottom.toFixed(2)}`,
    `Q ${rect.left.toFixed(2)} ${rect.bottom.toFixed(2)} ${rect.left.toFixed(2)} ${Number(rect.bottom - bottomLeft).toFixed(2)}`,
    `L ${rect.left.toFixed(2)} ${Number(rect.top + topLeft).toFixed(2)}`,
    `Q ${rect.left.toFixed(2)} ${rect.top.toFixed(2)} ${Number(rect.left + topLeft).toFixed(2)} ${rect.top.toFixed(2)}`,
    'Z',
  ].join(' ');
}

function getCompactHistoryRectEdgePoint(
  rect: CompactHistoryRect,
  direction: { x: number; y: number },
) {
  const center = {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
  const halfWidth = Math.max(rect.width / 2, 1);
  const halfHeight = Math.max(rect.height / 2, 1);
  const tx = Math.abs(direction.x) > 0.001 ? halfWidth / Math.abs(direction.x) : Number.POSITIVE_INFINITY;
  const ty = Math.abs(direction.y) > 0.001 ? halfHeight / Math.abs(direction.y) : Number.POSITIVE_INFINITY;
  const distance = Math.min(tx, ty);
  return {
    x: center.x + direction.x * distance,
    y: center.y + direction.y * distance,
  };
}

function getCompactHistoryBubbleCornerRadius(activeDrag: ActiveCompactHistoryDrag, corner: 'topLeft' | 'topRight' | 'bottomRight' | 'bottomLeft') {
  const maxRadius = Math.max(Math.min(activeDrag.originRect.width, activeDrag.originRect.height) / 2 - 1, 4);
  if (activeDrag.role === 'user') {
    return Math.min(corner === 'topRight' ? 8 : 16, maxRadius);
  }
  return Math.min(corner === 'topLeft' ? 8 : 16, maxRadius);
}

function getCompactHistoryRoundedRectJoin(
  activeDrag: ActiveCompactHistoryDrag,
  direction: { x: number; y: number },
  inward: { x: number; y: number },
  width: number,
  overlap: number,
  normal: { x: number; y: number },
  targetRect?: CompactHistoryRect,
) {
  const rect = targetRect ?? getCompactHistoryDragRect(activeDrag);
  const center = {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
  const edge = getCompactHistoryRectEdgePoint(rect, direction);
  const hitLeft = Math.abs(edge.x - rect.left) <= 0.5;
  const hitRight = Math.abs(edge.x - rect.right) <= 0.5;
  const hitTop = Math.abs(edge.y - rect.top) <= 0.5;
  const hitBottom = Math.abs(edge.y - rect.bottom) <= 0.5;
  const corner =
    (direction.x < -0.001 && direction.y < -0.001 && ((hitLeft && edge.y < rect.top + getCompactHistoryBubbleCornerRadius(activeDrag, 'topLeft')) || (hitTop && edge.x < rect.left + getCompactHistoryBubbleCornerRadius(activeDrag, 'topLeft'))))
      ? 'topLeft'
      : (direction.x > 0.001 && direction.y < -0.001 && ((hitRight && edge.y < rect.top + getCompactHistoryBubbleCornerRadius(activeDrag, 'topRight')) || (hitTop && edge.x > rect.right - getCompactHistoryBubbleCornerRadius(activeDrag, 'topRight'))))
        ? 'topRight'
        : (direction.x > 0.001 && direction.y > 0.001 && ((hitRight && edge.y > rect.bottom - getCompactHistoryBubbleCornerRadius(activeDrag, 'bottomRight')) || (hitBottom && edge.x > rect.right - getCompactHistoryBubbleCornerRadius(activeDrag, 'bottomRight'))))
          ? 'bottomRight'
          : (direction.x < -0.001 && direction.y > 0.001 && ((hitLeft && edge.y > rect.bottom - getCompactHistoryBubbleCornerRadius(activeDrag, 'bottomLeft')) || (hitBottom && edge.x < rect.left + getCompactHistoryBubbleCornerRadius(activeDrag, 'bottomLeft'))))
            ? 'bottomLeft'
            : null;
  if (corner) {
    const radius = getCompactHistoryBubbleCornerRadius(activeDrag, corner);
    const cornerCenter = {
      x: corner === 'topLeft' || corner === 'bottomLeft' ? rect.left + radius : rect.right - radius,
      y: corner === 'topLeft' || corner === 'topRight' ? rect.top + radius : rect.bottom - radius,
    };
    const fromCenter = { x: center.x - cornerCenter.x, y: center.y - cornerCenter.y };
    const b = 2 * (fromCenter.x * direction.x + fromCenter.y * direction.y);
    const c = fromCenter.x * fromCenter.x + fromCenter.y * fromCenter.y - radius * radius;
    const discriminant = Math.max(b * b - 4 * c, 0);
    const t = (-b + Math.sqrt(discriminant)) / 2;
    const anchor = {
      x: center.x + direction.x * t,
      y: center.y + direction.y * t,
    };
    const radialLength = Math.max(Math.hypot(anchor.x - cornerCenter.x, anchor.y - cornerCenter.y), 1);
    const radialAngle = Math.atan2(anchor.y - cornerCenter.y, anchor.x - cornerCenter.x);
    const angleDelta = clampCompactHistoryValue(width / radialLength, 0.18, 0.82);
    const first = {
      x: cornerCenter.x + Math.cos(radialAngle - angleDelta) * radialLength + inward.x * overlap,
      y: cornerCenter.y + Math.sin(radialAngle - angleDelta) * radialLength + inward.y * overlap,
    };
    const second = {
      x: cornerCenter.x + Math.cos(radialAngle + angleDelta) * radialLength + inward.x * overlap,
      y: cornerCenter.y + Math.sin(radialAngle + angleDelta) * radialLength + inward.y * overlap,
    };
    const firstDot = (first.x - anchor.x) * normal.x + (first.y - anchor.y) * normal.y;
    const secondDot = (second.x - anchor.x) * normal.x + (second.y - anchor.y) * normal.y;
    return firstDot >= secondDot ? { first, second } : { first: second, second: first };
  }
  let tangent = hitLeft || hitRight ? { x: 0, y: 1 } : { x: 1, y: 0 };
  if (tangent.x * normal.x + tangent.y * normal.y < 0) {
    tangent = { x: -tangent.x, y: -tangent.y };
  }
  return {
    first: {
      x: edge.x + tangent.x * width + inward.x * overlap,
      y: edge.y + tangent.y * width + inward.y * overlap,
    },
    second: {
      x: edge.x - tangent.x * width + inward.x * overlap,
      y: edge.y - tangent.y * width + inward.y * overlap,
    },
  };
}

function getCompactHistorySourceStyle(activeDrag: ActiveCompactHistoryDrag) {
  return {
    ...getCompactHistoryDragPalette(activeDrag),
    '--compact-history-drag-pull-x': '0px',
    '--compact-history-drag-pull-y': '0px',
    '--compact-history-drag-stretch-x': '1',
    '--compact-history-drag-stretch-y': '1',
    '--compact-history-drag-skew': '0deg',
    '--compact-history-drag-source-content-opacity': '0',
  } as CSSProperties & Record<string, string>;
}

const COMPACT_HISTORY_SOURCE_DRAG_STYLE_KEYS = [
  '--compact-history-drag-surface-rgb',
  '--compact-history-drag-edge-rgb',
  '--compact-history-drag-shadow-rgb',
  '--compact-history-drag-text',
  '--compact-history-drag-pull-x',
  '--compact-history-drag-pull-y',
  '--compact-history-drag-stretch-x',
  '--compact-history-drag-stretch-y',
  '--compact-history-drag-skew',
  '--compact-history-drag-source-content-opacity',
];

function applyCompactHistoryStyleVars(element: HTMLElement | SVGElement | null, vars: CSSProperties & Record<string, string>) {
  if (!element) return;
  Object.entries(vars).forEach(([key, value]) => {
    element.style.setProperty(key, String(value));
  });
}

function clearCompactHistoryStyleVars(element: HTMLElement | null, keys: string[]) {
  if (!element) return;
  keys.forEach((key) => {
    element.style.removeProperty(key);
  });
}

function getCompactHistorySourceElement(activeDrag: ActiveCompactHistoryDrag) {
  if (!activeDrag.originElement?.isConnected) return null;
  if (activeDrag.originElement.classList.contains('compact-export-history-message')) {
    return activeDrag.originElement;
  }
  return activeDrag.originElement.closest('.compact-export-history-message') as HTMLElement | null;
}

function getCompactHistorySourceFrameRect(activeDrag: ActiveCompactHistoryDrag): CompactHistoryRect {
  return activeDrag.sourceFrameRect;
}

function getCompactHistoryFallbackDirection(activeDrag: ActiveCompactHistoryDrag) {
  return activeDrag.role === 'user' ? { x: -1, y: 0 } : { x: 1, y: 0 };
}

function getCompactHistorySourceNubAnchor(
  activeDrag: ActiveCompactHistoryDrag,
  dx: number,
  dy: number,
  fallbackDirection: { x: number; y: number },
) {
  if (activeDrag.type !== 'bubble') {
    return {
      edge: getCompactHistoryRectEdgePoint(activeDrag.originRect, fallbackDirection),
      retreat: fallbackDirection,
    };
  }
  const originCenter = getCompactHistoryOriginCenter(activeDrag);
  const frameRect = getCompactHistorySourceFrameRect(activeDrag);
  const sourceSide = activeDrag.role === 'user' ? 1 : -1;
  const horizontalRange = 34;
  const verticalRange = clampCompactHistoryValue(Math.min(activeDrag.originRect.height * 0.18, 22), 6, 22);
  const verticalPadding = Math.min(activeDrag.originRect.height * 0.34, 28);
  const sideMin = sourceSide < 0 ? frameRect.left : frameRect.right - horizontalRange;
  const sideMax = sourceSide < 0 ? frameRect.left + horizontalRange : frameRect.right;
  return {
    edge: {
      x: clampCompactHistoryValue(originCenter.x + dx * 0.12, sideMin, sideMax),
      y: clampCompactHistoryValue(
        originCenter.y + clampCompactHistoryValue(dy * 0.14, -verticalRange, verticalRange),
        activeDrag.originRect.top + verticalPadding,
        activeDrag.originRect.bottom - verticalPadding,
      ),
    },
    retreat: { x: sourceSide, y: 0 },
  };
}

function getCompactHistorySourceNub(activeDrag: ActiveCompactHistoryDrag): CompactHistorySourceNub {
  const originCenter = getCompactHistoryOriginCenter(activeDrag);
  const dragCenter = getCompactHistoryDragCenter(activeDrag);
  const dx = dragCenter.x - originCenter.x;
  const dy = dragCenter.y - originCenter.y;
  const rawDistance = Math.hypot(dx, dy);
  const distance = Math.max(rawDistance, 1);
  const pull = clampCompactHistoryValue(distance / 330, 0, 1);
  const fallback = getCompactHistoryFallbackDirection(activeDrag);
  const ux = rawDistance > 0.001 ? dx / distance : fallback.x;
  const uy = rawDistance > 0.001 ? dy / distance : fallback.y;
  const minSize = Math.max(Math.min(activeDrag.originRect.width, activeDrag.originRect.height), 28);
  const anchor = getCompactHistorySourceNubAnchor(activeDrag, dx, dy, { x: ux, y: uy });
  const size = clampCompactHistoryValue(minSize * (0.62 - pull * 0.24), 24, 48);
  const width = activeDrag.type === 'bubble'
    ? clampCompactHistoryValue(size * (1.04 + Math.abs(ux) * 0.28), 26, 58)
    : size;
  const height = activeDrag.type === 'bubble'
    ? clampCompactHistoryValue(size * (0.96 + Math.abs(uy) * 0.16), 24, 48)
    : size;
  return {
    center: {
      x: anchor.edge.x - anchor.retreat.x * size * 0.18,
      y: anchor.edge.y - anchor.retreat.y * size * 0.18,
    },
    width,
    height,
  };
}

function getCompactHistoryNubEdgePoint(
  nub: CompactHistorySourceNub,
  direction: { x: number; y: number },
) {
  const halfWidth = Math.max(nub.width / 2, 1);
  const halfHeight = Math.max(nub.height / 2, 1);
  const tx = Math.abs(direction.x) > 0.001 ? halfWidth / Math.abs(direction.x) : Number.POSITIVE_INFINITY;
  const ty = Math.abs(direction.y) > 0.001 ? halfHeight / Math.abs(direction.y) : Number.POSITIVE_INFINITY;
  const distance = Math.min(tx, ty);
  return {
    x: nub.center.x + direction.x * distance,
    y: nub.center.y + direction.y * distance,
  };
}

function getCompactHistoryElasticGeometry(
  activeDrag: ActiveCompactHistoryDrag,
  previousCurvePoint?: { x: number; y: number } | null,
): CompactHistoryElasticGeometry {
  const originCenter = getCompactHistoryOriginCenter(activeDrag);
  const dragCenter = getCompactHistoryDragCenter(activeDrag);
  const dx = dragCenter.x - originCenter.x;
  const dy = dragCenter.y - originCenter.y;
  const rawDistance = Math.hypot(dx, dy);
  const distance = Math.max(rawDistance, 1);
  const fallback = getCompactHistoryFallbackDirection(activeDrag);
  const ux = rawDistance > 0.001 ? dx / distance : fallback.x;
  const uy = rawDistance > 0.001 ? dy / distance : fallback.y;
  const nx = -uy;
  const ny = ux;
  const minSize = Math.max(Math.min(activeDrag.originRect.width, activeDrag.originRect.height), 28);
  const pull = clampCompactHistoryValue(distance / 330, 0, 1);
  const sourceNub = getCompactHistorySourceNub(activeDrag);
  const source = getCompactHistoryNubEdgePoint(sourceNub, { x: ux, y: uy });
  const dragRect = activeDrag.type === 'bubble' ? getCompactHistoryScaledDragRect(activeDrag) : getCompactHistoryDragRect(activeDrag);
  const dragOverlap = activeDrag.type === 'bubble'
    ? clampCompactHistoryValue(minSize * 0.3, 10, 22)
    : clampCompactHistoryValue(minSize * 0.48, 20, 44);
  const dragAnchor = getCompactHistoryRoundedRectJoin(activeDrag, { x: -ux, y: -uy }, { x: ux, y: uy }, 1, dragOverlap, { x: nx, y: ny }, dragRect);
  const drag = {
    x: (dragAnchor.first.x + dragAnchor.second.x) / 2,
    y: (dragAnchor.first.y + dragAnchor.second.y) / 2,
  };
  const linkDx = drag.x - source.x;
  const linkDy = drag.y - source.y;
  const linkLength = Math.max(Math.hypot(linkDx, linkDy), 1);
  const ribbonProgress = clampCompactHistoryValue((linkLength - 18) / 150, 0, 1);
  const ribbonWidthLimit = clampCompactHistoryValue(linkLength * (0.22 - ribbonProgress * 0.08), 8, 24);
  const sourceWidth = Math.min(
    clampCompactHistoryValue(Math.min(sourceNub.width, sourceNub.height) * (0.56 - pull * 0.18), 10, 26),
    ribbonWidthLimit,
  );
  const dragWidth = Math.min(
    clampCompactHistoryValue(minSize * (0.38 - pull * 0.12), 12, 32),
    ribbonWidthLimit * 1.08,
  );
  const sourceRx = Math.max(sourceNub.width / 2, 1);
  const sourceRy = Math.max(sourceNub.height / 2, 1);
  const sourceAngle = Math.atan2((source.y - sourceNub.center.y) / sourceRy, (source.x - sourceNub.center.x) / sourceRx);
  const sourceDelta = clampCompactHistoryValue(sourceWidth / Math.max(sourceRx, sourceRy), 0.22, 0.82);
  const sourceFirst = {
    x: sourceNub.center.x + Math.cos(sourceAngle - sourceDelta) * sourceRx,
    y: sourceNub.center.y + Math.sin(sourceAngle - sourceDelta) * sourceRy,
  };
  const sourceSecond = {
    x: sourceNub.center.x + Math.cos(sourceAngle + sourceDelta) * sourceRx,
    y: sourceNub.center.y + Math.sin(sourceAngle + sourceDelta) * sourceRy,
  };
  const sourceFirstDot = (sourceFirst.x - source.x) * nx + (sourceFirst.y - source.y) * ny;
  const sourceSecondDot = (sourceSecond.x - source.x) * nx + (sourceSecond.y - source.y) * ny;
  const s1 = sourceFirstDot >= sourceSecondDot ? sourceFirst : sourceSecond;
  const s2 = sourceFirstDot >= sourceSecondDot ? sourceSecond : sourceFirst;
  const dragJoin = getCompactHistoryRoundedRectJoin(activeDrag, { x: -ux, y: -uy }, { x: ux, y: uy }, dragWidth, dragOverlap, { x: nx, y: ny }, dragRect);
  const d1 = dragJoin.first;
  const d2 = dragJoin.second;
  const curveStrength = clampCompactHistoryValue(distance * 0.018, 0, 10) * (dy >= 0 ? 1 : -1);
  const targetWaistBase = {
    x: source.x + linkDx * 0.5,
    y: source.y + linkDy * 0.5,
  };
  const targetWaistPoint = {
    x: targetWaistBase.x + nx * curveStrength,
    y: targetWaistBase.y + ny * curveStrength,
  };
  const waist = previousCurvePoint
    ? {
        x: previousCurvePoint.x + (targetWaistPoint.x - previousCurvePoint.x) * 0.42,
        y: previousCurvePoint.y + (targetWaistPoint.y - previousCurvePoint.y) * 0.42,
      }
    : targetWaistPoint;
  const elasticPath = [
    `M ${s1.x.toFixed(2)} ${s1.y.toFixed(2)}`,
    `Q ${waist.x.toFixed(2)} ${waist.y.toFixed(2)} ${d1.x.toFixed(2)} ${d1.y.toFixed(2)}`,
    `L ${d2.x.toFixed(2)} ${d2.y.toFixed(2)}`,
    `Q ${waist.x.toFixed(2)} ${waist.y.toFixed(2)} ${s2.x.toFixed(2)} ${s2.y.toFixed(2)}`,
    `A ${sourceRx.toFixed(2)} ${sourceRy.toFixed(2)} 0 1 0 ${s1.x.toFixed(2)} ${s1.y.toFixed(2)}`,
    'Z',
  ].join(' ');
  const path = elasticPath;
  return {
    path,
    shellPath: activeDrag.type === 'bubble' ? getCompactHistoryBubbleShellPath(activeDrag, dragRect) : undefined,
    pull,
    opacity: clampCompactHistoryValue(0.9 - pull * 0.16, 0.58, 0.9),
    nextCurvePoint: waist,
  };
}

function getCompactHistoryMessageClassName(message: ChatMessage, selected: boolean, selectable: boolean, hasSelection: boolean) {
  return clsx('compact-export-history-message', {
    'is-user': message.role === 'user',
    'is-assistant': message.role === 'assistant' || message.role === 'tool',
    'is-system': message.role === 'system',
    'is-selected': selected,
    'is-unselected': hasSelection && selectable && !selected,
    'is-disabled': !selectable,
    'is-streaming': message.status === 'streaming',
    'is-failed': message.status === 'failed',
  });
}

function getCompactHistoryRoleGroup(message?: ChatMessage) {
  if (!message) return 'none';
  if (message.role === 'user') return 'user';
  if (message.role === 'assistant' || message.role === 'tool') return 'assistant';
  return 'system';
}

function getStableCompactHistoryHash(seed: string) {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = ((hash << 5) - hash + seed.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function hasRichCompactHistoryContent(message: ChatMessage) {
  return message.blocks.some(block => block.type === 'image' || block.type === 'buttons');
}

function getCompactHistoryBubbleTone(
  message: ChatMessage,
  index: number,
  previousMessage?: ChatMessage,
): CompactHistoryBubbleTone {
  const roleGroup = getCompactHistoryRoleGroup(message);
  const previousRoleGroup = getCompactHistoryRoleGroup(previousMessage);
  const group: CompactHistoryBubbleTone['group'] = index === 0
    ? 'first'
    : previousRoleGroup === roleGroup
      ? 'same'
      : 'switch';
  const richContent = hasRichCompactHistoryContent(message);
  const seed = message.id || `${message.role}:${message.createdAt ?? message.time}:${index}`;
  const hash = getStableCompactHistoryHash(seed);
  const widthSteps = roleGroup === 'system'
    ? ['84%', '90%', '94%']
    : richContent
      ? ['76%', '82%', '88%']
      : ['70%', '78%', '86%', '92%'];
  const offsetSteps = richContent ? [0, 6, 10] : [0, 8, 14, 20];
  const baseOffset = offsetSteps[Math.floor(hash / 7) % offsetSteps.length];
  const signedOffset = roleGroup === 'user'
    ? -baseOffset
    : roleGroup === 'assistant'
      ? baseOffset
      : 0;
  const sameGroupGaps = ['4px', '7px', '10px'];
  const switchGroupGaps = ['15px', '19px', '23px'];
  const gapSteps = group === 'same' ? sameGroupGaps : group === 'switch' ? switchGroupGaps : ['0px'];
  const rotateSteps = richContent || roleGroup === 'system'
    ? ['0deg']
    : ['-0.5deg', '-0.25deg', '0deg', '0.25deg', '0.5deg'];

  return {
    group,
    complexity: richContent ? 'rich' : 'plain',
    style: {
      '--compact-history-bubble-max-ratio': widthSteps[hash % widthSteps.length],
      '--compact-history-stagger-x': `${signedOffset}px`,
      '--compact-history-gap-before': gapSteps[Math.floor(hash / 31) % gapSteps.length],
      '--compact-history-rotate': rotateSteps[Math.floor(hash / 127) % rotateSteps.length],
    },
  };
}

export default function CompactExportHistoryPanel({
  messages,
  selectedIds,
  selectedCount,
  selectableCount,
  autoScrollToBottom,
  previewOpen,
  controlsOpen,
  choiceLayerAbove,
  visibilityState = 'open',
  failedStatusLabel,
  onAutoScrollToBottomChange,
  onToggleMessage,
  onSelectAll,
  onClearSelection,
  onInvertSelection,
  onRequestPreview,
  onClosePreview,
  onBuildPreview,
  onCopyExport,
  onDownloadExport,
  onAction,
  isDropTargetAt,
  onDropToTarget,
  onDragStateChange,
}: CompactExportHistoryPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pointerIntentRef = useRef<PointerIntentState | null>(null);
  const activeDragRef = useRef<ActiveCompactHistoryDrag | null>(null);
  const currentOverDropTargetRef = useRef(false);
  const desktopDropTargetRef = useRef<{ sessionId: string; overTarget: boolean; timestamp: number } | null>(null);
  const dragLayerRef = useRef<HTMLDivElement | null>(null);
  const dragElasticLayerRef = useRef<SVGSVGElement | null>(null);
  const dragElasticPathRef = useRef<SVGPathElement | null>(null);
  const dragBubbleShellPathRef = useRef<SVGPathElement | null>(null);
  const dragSourceElementRef = useRef<HTMLElement | null>(null);
  const dragElasticCurveRef = useRef<{ x: number; y: number } | null>(null);
  const dragAnimationTimerRef = useRef<number | null>(null);
  const dragMoveFrameRef = useRef<number | null>(null);
  const scrollbarVisibleTimerRef = useRef<number | null>(null);
  const dragRebasePendingRef = useRef<string | null>(null);
  const dragStateSeqRef = useRef(0);
  const lastDragStateSessionIdRef = useRef<string | null>(null);
  const pendingDragPointRef = useRef<{ intent: PointerIntentState; clientX: number; clientY: number } | null>(null);
  const suppressClickMessageIdRef = useRef<string | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);
  const enterDelayByMessageIdRef = useRef<Map<string, string>>(new Map());
  const previousVisibilityStateRef = useRef<'open' | 'closing' | null>(null);
  const [exportFormat, setExportFormat] = useState<CompactExportFormat>('markdown');
  const [imageStyle, setImageStyle] = useState<CompactExportImageStyle>('neko');
  const [imageFormat, setImageFormat] = useState<CompactExportImageFormat>('png');
  const [pendingAction, setPendingAction] = useState<'copy' | 'download' | null>(null);
  const [exportActionError, setExportActionError] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<CompactExportPreviewState>({ status: 'idle' });
  const [activeDrag, setActiveDrag] = useState<ActiveCompactHistoryDrag | null>(null);
  const [scrollbarVisible, setScrollbarVisible] = useState(false);
  const selectedMessages = messages.filter(message => selectedIds.has(message.id));
  const previewHasSelection = selectedMessages.length > 0;
  const selectedMessageIds = selectedMessages.map(message => message.id);
  const selectedMessageSignature = selectedMessages.map(message => [
    message.id,
    message.role,
    message.author,
    message.time,
    message.status || '',
    JSON.stringify(message.blocks),
  ].join('\u001e')).join('\u001f');
  const exportBusy = pendingAction !== null;
  const exportActionsDisabled = !previewHasSelection || exportBusy;
  const historyInteractive = visibilityState === 'open';
  const selectionControlsInteractive = historyInteractive && controlsOpen;
  const openingEnterDelayByMessageId = useMemo(() => (
    visibilityState === 'open' && previousVisibilityStateRef.current !== 'open'
      ? new Map(messages.map((message, index) => [
        message.id,
        computeCompactHistoryEnterDelay(index, messages.length),
      ]))
      : null
  ), [messages, visibilityState]);

  useLayoutEffect(() => {
    if (openingEnterDelayByMessageId) {
      enterDelayByMessageIdRef.current = openingEnterDelayByMessageId;
    }
    previousVisibilityStateRef.current = visibilityState;
  }, [openingEnterDelayByMessageId, visibilityState]);

  function resolveCompactHistoryEnterDelay(message: ChatMessage, index: number): string {
    const existingDelay = openingEnterDelayByMessageId?.get(message.id)
      ?? enterDelayByMessageIdRef.current.get(message.id);
    if (existingDelay !== undefined) return existingDelay;
    return visibilityState === 'open'
      ? '0ms'
      : computeCompactHistoryEnterDelay(index, messages.length);
  }

  function clearScrollbarVisibleTimer() {
    if (scrollbarVisibleTimerRef.current === null) return;
    window.clearTimeout(scrollbarVisibleTimerRef.current);
    scrollbarVisibleTimerRef.current = null;
  }

  function revealScrollbarForWheel() {
    if (!historyInteractive) return;
    clearScrollbarVisibleTimer();
    setScrollbarVisible(true);
    scrollbarVisibleTimerRef.current = window.setTimeout(() => {
      scrollbarVisibleTimerRef.current = null;
      setScrollbarVisible(false);
    }, COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS);
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (event.deltaX !== 0 || event.deltaY !== 0 || event.deltaZ !== 0) {
      revealScrollbarForWheel();
    }
  }

  function emitCompactHistoryDragState(activeDragState: ActiveCompactHistoryDrag) {
    const state = buildCompactHistoryDragState(activeDragState, dragStateSeqRef.current);
    dragStateSeqRef.current += 1;
    lastDragStateSessionIdRef.current = activeDragState.sessionId;
    onDragStateChange?.(state);
  }

  function emitCompactHistoryDragInactiveState(phase: 'idle' | 'cancelled' = 'idle') {
    const sessionId = lastDragStateSessionIdRef.current ?? undefined;
    lastDragStateSessionIdRef.current = null;
    dragStateSeqRef.current = 0;
    onDragStateChange?.({
      active: false,
      sessionId,
      phase,
      timestamp: Date.now(),
    });
  }

  function revokeCompactPreviewObjectUrl() {
    if (!previewObjectUrlRef.current) return;
    URL.revokeObjectURL(previewObjectUrlRef.current);
    previewObjectUrlRef.current = null;
  }

  useLayoutEffect(() => {
    if (!autoScrollToBottom) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    let frameId: number | null = null;
    let remainingFrames = COMPACT_HISTORY_SCROLL_SETTLE_FRAMES;
    const pinScrollToBottom = () => {
      scrollNode.scrollTop = scrollNode.scrollHeight;
      remainingFrames -= 1;
      if (remainingFrames <= 0) {
        frameId = null;
        return;
      }
      frameId = window.requestAnimationFrame(pinScrollToBottom);
    };
    pinScrollToBottom();
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [autoScrollToBottom, messages, previewOpen, visibilityState]);

  useEffect(() => {
    if (!previewOpen) {
      revokeCompactPreviewObjectUrl();
      setExportActionError(null);
      setPreviewState({ status: 'idle' });
      return;
    }
    if (!previewHasSelection) {
      revokeCompactPreviewObjectUrl();
      setPreviewState({ status: 'ready', result: { previewKind: 'empty' } });
      return;
    }

    let cancelled = false;
    const request: CompactExportActionRequest = {
      messageIds: selectedMessageIds,
      format: exportFormat,
      imageStyle,
      imageFormat,
    };
    revokeCompactPreviewObjectUrl();
    setPreviewState({ status: 'loading' });
    Promise.resolve()
      .then(() => onBuildPreview(request))
      .then((result) => {
        if (cancelled) {
          if (result.previewKind === 'image') {
            URL.revokeObjectURL(result.previewUrl);
          }
          return;
        }
        if (result.previewKind === 'image') {
          previewObjectUrlRef.current = result.previewUrl;
        }
        setPreviewState({ status: 'ready', result });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setPreviewState({ status: 'failed', message });
      });

    return () => {
      cancelled = true;
    };
  }, [previewOpen, previewHasSelection, selectedMessageSignature, exportFormat, imageStyle, imageFormat, onBuildPreview]);

  useEffect(() => () => {
    revokeCompactPreviewObjectUrl();
  }, []);

  useEffect(() => {
    if (historyInteractive) return undefined;
    clearScrollbarVisibleTimer();
    setScrollbarVisible(false);
    return undefined;
  }, [historyInteractive]);

  useEffect(() => () => {
    clearScrollbarVisibleTimer();
  }, []);

  useLayoutEffect(() => {
    activeDragRef.current = activeDrag;
    currentOverDropTargetRef.current = activeDrag?.overDropTarget ?? false;
    if (!activeDrag) {
      dragRebasePendingRef.current = null;
      desktopDropTargetRef.current = null;
      clearCompactHistoryStyleVars(dragSourceElementRef.current, COMPACT_HISTORY_SOURCE_DRAG_STYLE_KEYS);
      dragSourceElementRef.current = null;
      return;
    }
    const rebasePending = dragRebasePendingRef.current === activeDrag.sessionId;
    const useLiveRects = rebasePending ? false : undefined;
    dragRebasePendingRef.current = null;
    applyCompactHistoryDragFrame(activeDrag, activeDrag.pointerClient.x, activeDrag.pointerClient.y, {
      updateDropTarget: false,
      useLiveRects,
      emitState: !rebasePending,
    });
  }, [activeDrag]);

  useEffect(() => {
    const current = activeDragRef.current;
    if (!current) return;
    if (!messages.some(message => message.id === current.messageId)) {
      clearCompactHistoryDragAnimationTimer();
      clearCompactHistoryDragMoveFrame();
      clearCompactHistoryElasticCurve();
      activeDragRef.current = null;
      currentOverDropTargetRef.current = false;
      setActiveDrag(null);
      pointerIntentRef.current = null;
      emitCompactHistoryDragInactiveState('cancelled');
      return;
    }
    applyCompactHistoryDragFrame(current, current.pointerClient.x, current.pointerClient.y);
  }, [messages]);

  useEffect(() => () => {
    if (activeDragRef.current) {
      emitCompactHistoryDragInactiveState('cancelled');
    }
    pointerIntentRef.current = null;
    clearCompactHistoryDragAnimationTimer();
    clearCompactHistoryDragMoveFrame();
    clearCompactHistoryElasticCurve();
  }, []);

  useEffect(() => {
    function handleDesktopDragRebase(event: Event) {
      const detail = (event as CustomEvent<CompactHistoryDragRebaseDetail>).detail;
      const dx = Number(detail?.deltaX);
      const dy = Number(detail?.deltaY);
      if (!Number.isFinite(dx) || !Number.isFinite(dy) || (dx === 0 && dy === 0)) return;
      const current = activeDragRef.current;
      if (!current || !detail?.sessionId || detail.sessionId !== current.sessionId) return;
      const next = translateCompactHistoryActiveDrag(current, dx, dy);
      activeDragRef.current = next;
      if (dragElasticCurveRef.current) {
        dragElasticCurveRef.current = {
          x: dragElasticCurveRef.current.x + dx,
          y: dragElasticCurveRef.current.y + dy,
        };
      }
      const intent = pointerIntentRef.current;
      if (intent && intent.sessionId === next.sessionId) {
        pointerIntentRef.current = translateCompactHistoryPointerIntent(intent, dx, dy);
      }
      applyCompactHistoryDragFrame(next, next.pointerClient.x, next.pointerClient.y, {
        updateDropTarget: false,
        useLiveRects: false,
      });
      dragRebasePendingRef.current = next.sessionId;
      setActiveDrag((activeDragState) => (
        activeDragState && activeDragState.sessionId === next.sessionId ? next : activeDragState
      ));
    }

    window.addEventListener('neko:compact-history-drag-rebase', handleDesktopDragRebase);
    return () => {
      window.removeEventListener('neko:compact-history-drag-rebase', handleDesktopDragRebase);
    };
  }, []);

  useEffect(() => {
    function handleDesktopDropTargetChange(event: Event) {
      const detail = (event as CustomEvent<CompactHistoryDesktopDropTargetDetail>).detail;
      const current = activeDragRef.current;
      if (!current) {
        desktopDropTargetRef.current = null;
        return;
      }
      if (detail?.active === false) {
        desktopDropTargetRef.current = null;
        return;
      }
      if (!detail?.sessionId || detail.sessionId !== current.sessionId || typeof detail.desktopOverAvatar !== 'boolean') return;
      desktopDropTargetRef.current = {
        sessionId: current.sessionId,
        overTarget: detail.desktopOverAvatar,
        timestamp: Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : Date.now(),
      };
      if (current.overDropTarget === detail.desktopOverAvatar) return;
      const next = {
        ...current,
        overDropTarget: detail.desktopOverAvatar,
      };
      activeDragRef.current = next;
      currentOverDropTargetRef.current = next.overDropTarget;
      setActiveDrag((activeDragState) => (
        activeDragState && activeDragState.sessionId === next.sessionId ? next : activeDragState
      ));
    }

    window.addEventListener('neko:compact-history-drag-desktop-target-change', handleDesktopDropTargetChange);
    return () => {
      window.removeEventListener('neko:compact-history-drag-desktop-target-change', handleDesktopDropTargetChange);
    };
  }, []);

  useEffect(() => {
    function clearDraggingIntent(event: PointerEvent) {
      const intent = pointerIntentRef.current;
      if (!intent || intent.id !== event.pointerId || !isDraggingPhase(intent.phase)) return;
      event.preventDefault();
      suppressNextClickForMessage(intent.messageId);
      completePointerIntent(intent, {
        clientX: event.clientX,
        clientY: event.clientY,
        tryDrop: event.type === 'pointerup',
      });
    }

    function updateDraggingIntent(event: PointerEvent) {
      const intent = pointerIntentRef.current;
      if (!intent || intent.id !== event.pointerId || !isDraggingPhase(intent.phase)) return;
      if (intent.pointerType === 'mouse' && event.buttons === 0) {
        clearDraggingIntent(event);
        return;
      }
      event.preventDefault();
      updateCompactHistoryDrag(intent, event.clientX, event.clientY);
    }

    function clearCurrentIntent() {
      clearPointerIntentAfterDrag(pointerIntentRef.current);
    }

    function handleVisibilityChange() {
      if (document.visibilityState === 'hidden') {
        clearCurrentIntent();
      }
    }

    window.addEventListener('pointermove', updateDraggingIntent);
    window.addEventListener('pointerup', clearDraggingIntent);
    window.addEventListener('pointercancel', clearDraggingIntent);
    window.addEventListener('blur', clearCurrentIntent);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('pointermove', updateDraggingIntent);
      window.removeEventListener('pointerup', clearDraggingIntent);
      window.removeEventListener('pointercancel', clearDraggingIntent);
      window.removeEventListener('blur', clearCurrentIntent);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  });

  function restoreAutoScrollAfterDrag(intent: PointerIntentState) {
    onAutoScrollToBottomChange(intent.autoScrollToBottomOnStart);
  }

  function clearCompactHistoryDragAnimationTimer() {
    if (dragAnimationTimerRef.current === null) return;
    window.clearTimeout(dragAnimationTimerRef.current);
    dragAnimationTimerRef.current = null;
  }

  function clearCompactHistoryDragMoveFrame() {
    if (dragMoveFrameRef.current === null) return;
    window.cancelAnimationFrame(dragMoveFrameRef.current);
    dragMoveFrameRef.current = null;
    pendingDragPointRef.current = null;
  }

  function clearCompactHistoryElasticCurve() {
    dragElasticCurveRef.current = null;
  }

  function resolveLiveOriginRect(activeDrag: ActiveCompactHistoryDrag) {
    if (!activeDrag.originElement?.isConnected) return activeDrag.originRect;
    return snapshotCompactHistoryRect(activeDrag.originElement.getBoundingClientRect());
  }

  function resolveLiveSourceFrameRect(activeDrag: ActiveCompactHistoryDrag) {
    const sourceElement = getCompactHistorySourceElement(activeDrag);
    if (!sourceElement) return activeDrag.sourceFrameRect;
    const rect = snapshotCompactHistoryRect(sourceElement.getBoundingClientRect());
    return rect.width > 1 && rect.height > 1 ? rect : activeDrag.sourceFrameRect;
  }

  function applyCompactHistoryDragFrame(
    activeDrag: ActiveCompactHistoryDrag,
    clientX: number,
    clientY: number,
    options: { updateDropTarget?: boolean; useLiveRects?: boolean; emitState?: boolean } = {},
  ) {
    const originRect = options.useLiveRects === false ? activeDrag.originRect : resolveLiveOriginRect(activeDrag);
    const sourceFrameRect = options.useLiveRects === false ? activeDrag.sourceFrameRect : resolveLiveSourceFrameRect(activeDrag);
    const frame: ActiveCompactHistoryDrag = {
      ...activeDrag,
      originRect,
      sourceFrameRect,
      pointerClient: { x: clientX, y: clientY },
    };
    activeDragRef.current = frame;
    applyCompactHistoryStyleVars(dragLayerRef.current, getCompactHistoryDragVisualStyle(frame));
    applyCompactHistoryElasticFrame(frame);

    const sourceElement = getCompactHistorySourceElement(frame);
    if (dragSourceElementRef.current && dragSourceElementRef.current !== sourceElement) {
      clearCompactHistoryStyleVars(dragSourceElementRef.current, COMPACT_HISTORY_SOURCE_DRAG_STYLE_KEYS);
    }
    dragSourceElementRef.current = sourceElement;
    applyCompactHistoryStyleVars(sourceElement, getCompactHistorySourceStyle(frame));

    let bridgeFrame = frame;
    if (options.updateDropTarget !== false && frame.phase === 'dragging') {
      const overDropTarget = isCompactHistoryDropTargetAt(clientX, clientY);
      if (overDropTarget !== currentOverDropTargetRef.current) {
        currentOverDropTargetRef.current = overDropTarget;
        const next = {
          ...frame,
          overDropTarget,
        };
        bridgeFrame = next;
        activeDragRef.current = next;
        setActiveDrag((current) => (
          current && current.messageId === next.messageId && current.phase === 'dragging'
            ? {
                ...current,
                originRect: next.originRect,
                pointerClient: next.pointerClient,
                overDropTarget,
              }
            : current
        ));
      }
    }
    if (options.emitState !== false) {
      emitCompactHistoryDragState(bridgeFrame);
    }
  }

  function applyCompactHistoryElasticFrame(activeDrag: ActiveCompactHistoryDrag) {
    const geometry = getCompactHistoryElasticGeometry(activeDrag, dragElasticCurveRef.current);
    dragElasticCurveRef.current = geometry.nextCurvePoint;
    dragElasticPathRef.current?.setAttribute('d', geometry.path);
    if (geometry.shellPath) {
      dragBubbleShellPathRef.current?.setAttribute('d', geometry.shellPath);
    } else {
      dragBubbleShellPathRef.current?.removeAttribute('d');
    }
    applyCompactHistoryStyleVars(dragElasticLayerRef.current, {
      ...getCompactHistoryDragPalette(activeDrag),
      '--compact-history-elastic-pull': `${geometry.pull}`,
      '--compact-history-elastic-opacity': `${geometry.opacity}`,
    } as CSSProperties & Record<string, string>);
  }

  function suppressNextClickForMessage(messageId: string) {
    suppressClickMessageIdRef.current = messageId;
    window.setTimeout(() => {
      if (suppressClickMessageIdRef.current === messageId) {
        suppressClickMessageIdRef.current = null;
      }
    }, 120);
  }

  function isCompactHistoryDropTargetAt(clientX: number, clientY: number) {
    const desktopDropTarget = desktopDropTargetRef.current;
    const current = activeDragRef.current;
    if (
      desktopDropTarget
      && current
      && desktopDropTarget.sessionId === current.sessionId
      && Date.now() - desktopDropTarget.timestamp < 800
    ) {
      return desktopDropTarget.overTarget;
    }
    return isDropTargetAt?.({ clientX, clientY, sessionId: current?.sessionId }) ?? false;
  }

  function buildCompactHistoryDropRequest(
    intent: PointerIntentState,
    clientX: number,
    clientY: number,
  ): CompactHistoryDropRequest {
    return {
      type: getDragTypeForSource(intent.source),
      sessionId: intent.sessionId,
      messageId: intent.messageId,
      blockIndex: intent.source.type === 'image' ? intent.source.blockIndex : undefined,
      payload: intent.source.payload,
      point: { clientX, clientY },
    };
  }

  function completePointerIntent(
    intent: PointerIntentState | null,
    options?: { clientX: number; clientY: number; tryDrop: boolean },
  ) {
    if (!intent) return;
    const dragging = isDraggingPhase(intent.phase);
    const shouldDrop = !!(
      options
      && options.tryDrop
      && dragging
      && onDropToTarget
      && isCompactHistoryDropTargetAt(options.clientX, options.clientY)
    );
    const dropRequest = shouldDrop
      ? buildCompactHistoryDropRequest(intent, options.clientX, options.clientY)
      : null;
    pointerIntentRef.current = null;
    clearCompactHistoryDragMoveFrame();
    if (dragging) {
      restoreAutoScrollAfterDrag(intent);
    } else {
      clearCompactHistoryElasticCurve();
      activeDragRef.current = null;
      currentOverDropTargetRef.current = false;
      setActiveDrag(null);
    }
    if (dropRequest && onDropToTarget) {
      const dropPoint = { clientX: dropRequest.point.clientX, clientY: dropRequest.point.clientY };
      void Promise.resolve(onDropToTarget(dropRequest))
        .then((result) => {
          settleCompactHistoryDrag(intent, result === false ? 'returning' : 'sending', dropPoint);
        })
        .catch((error: unknown) => {
          console.error('[CompactExportHistory] drop delivery failed:', error);
          settleCompactHistoryDrag(intent, 'returning', dropPoint);
        });
      return;
    }
    if (dragging && options) {
      settleCompactHistoryDrag(intent, 'returning', {
        clientX: options.clientX,
        clientY: options.clientY,
      });
      return;
    }
    if (dragging) {
      clearCompactHistoryElasticCurve();
      activeDragRef.current = null;
      currentOverDropTargetRef.current = false;
      setActiveDrag(null);
      emitCompactHistoryDragInactiveState('cancelled');
    }
  }

  function clearPointerIntentAfterDrag(intent: PointerIntentState | null) {
    if (intent && isDraggingPhase(intent.phase)) {
      restoreAutoScrollAfterDrag(intent);
    }
    pointerIntentRef.current = null;
    clearCompactHistoryDragAnimationTimer();
    clearCompactHistoryDragMoveFrame();
    clearCompactHistoryElasticCurve();
    activeDragRef.current = null;
    currentOverDropTargetRef.current = false;
    setActiveDrag(null);
    if (intent && isDraggingPhase(intent.phase)) {
      emitCompactHistoryDragInactiveState('cancelled');
    }
  }

  function settleCompactHistoryDrag(
    intent: PointerIntentState,
    phase: 'returning' | 'sending',
    point: { clientX: number; clientY: number },
  ) {
    clearCompactHistoryDragAnimationTimer();
    clearCompactHistoryDragMoveFrame();
    setActiveDrag((current) => {
      const latest = activeDragRef.current?.messageId === intent.messageId
        ? activeDragRef.current
        : current;
      if (!latest || latest.messageId !== intent.messageId) return current;
      const originRect = resolveLiveOriginRect(latest);
      const sourceFrameRect = resolveLiveSourceFrameRect(latest);
      const pointerClient = phase === 'returning'
        ? {
            x: originRect.left + latest.pointerOffset.x,
            y: originRect.top + latest.pointerOffset.y,
          }
        : { x: point.clientX, y: point.clientY };
      const next = {
        ...latest,
        phase,
        originRect,
        sourceFrameRect,
        pointerClient,
        overDropTarget: phase === 'sending',
      };
      activeDragRef.current = next;
      currentOverDropTargetRef.current = next.overDropTarget;
      return next;
    });
    dragAnimationTimerRef.current = window.setTimeout(() => {
      dragAnimationTimerRef.current = null;
      setActiveDrag((current) => {
        if (!current || current.messageId !== intent.messageId) return current;
        activeDragRef.current = null;
        currentOverDropTargetRef.current = false;
        clearCompactHistoryElasticCurve();
        emitCompactHistoryDragInactiveState('idle');
        return null;
      });
    }, phase === 'sending' ? COMPACT_HISTORY_SEND_ANIMATION_MS : COMPACT_HISTORY_RETURN_ANIMATION_MS);
  }

  function startCompactHistoryDrag(intent: PointerIntentState, clientX: number, clientY: number) {
    const phase = getDragPhaseForSource(intent.source);
    intent.phase = phase;
    clearCompactHistoryDragAnimationTimer();
    clearCompactHistoryDragMoveFrame();
    clearCompactHistoryElasticCurve();
    const role = messages.find(message => message.id === intent.messageId)?.role ?? 'assistant';
    const active: ActiveCompactHistoryDrag = {
      sessionId: intent.sessionId,
      type: getDragTypeForSource(intent.source),
      phase: 'dragging',
      messageId: intent.messageId,
      role,
      blockIndex: intent.source.type === 'image' ? intent.source.blockIndex : undefined,
      payload: intent.source.payload,
      originRect: intent.originRect,
      sourceFrameRect: intent.sourceFrameRect,
      originElement: intent.originElement,
      pointerOffset: intent.pointerOffset,
      pointerClient: { x: clientX, y: clientY },
      overDropTarget: isCompactHistoryDropTargetAt(clientX, clientY),
    };
    activeDragRef.current = active;
    currentOverDropTargetRef.current = active.overDropTarget;
    setActiveDrag(active);
    if (intent.autoScrollToBottomOnStart) {
      onAutoScrollToBottomChange(false);
    }
  }

  function updateCompactHistoryDrag(intent: PointerIntentState, clientX: number, clientY: number) {
    pendingDragPointRef.current = { intent, clientX, clientY };
    if (dragMoveFrameRef.current !== null) return;
    dragMoveFrameRef.current = window.requestAnimationFrame(() => {
      dragMoveFrameRef.current = null;
      const pending = pendingDragPointRef.current;
      pendingDragPointRef.current = null;
      if (!pending) return;
      const current = activeDragRef.current;
      if (!current || current.messageId !== pending.intent.messageId || current.phase !== 'dragging') return;
      applyCompactHistoryDragFrame(current, pending.clientX, pending.clientY);
    });
  }

  function handleScroll() {
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    if (pointerIntentRef.current) {
      pointerIntentRef.current.phase = isDraggingPhase(pointerIntentRef.current.phase)
        ? pointerIntentRef.current.phase
        : 'scroll';
      completePointerIntent(pointerIntentRef.current);
    }
    const distanceToBottom = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight;
    onAutoScrollToBottomChange(distanceToBottom <= COMPACT_EXPORT_BOTTOM_THRESHOLD);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!historyInteractive) return;
    if (!selectable) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const imageSource = resolveImageDragSource(event.target, event.currentTarget, message);
    if (!imageSource && findSelectionIgnoredTarget(event.target, event.currentTarget, false)) return;
    const source = imageSource || resolveBubbleDragSource(message);
    const originElement = imageSource
      ? resolveImageDragOriginElement(event.target, event.currentTarget)
      : resolveBubbleDragOriginElement(event.target, event.currentTarget);
    const originRect = snapshotCompactHistoryRect(originElement.getBoundingClientRect());
    const sourceFrameElement = event.currentTarget.closest('.compact-export-history-message') as HTMLElement | null;
    if (!sourceFrameElement) return;
    const sourceFrameRect = snapshotCompactHistoryRect(sourceFrameElement.getBoundingClientRect());
    pointerIntentRef.current = {
      sessionId: createCompactHistoryDragSessionId(),
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      messageId: message.id,
      pointerType: event.pointerType || 'mouse',
      phase: 'pending',
      source,
      originRect,
      sourceFrameRect,
      originElement,
      pointerOffset: {
        x: event.clientX - originRect.left,
        y: event.clientY - originRect.top,
      },
      autoScrollToBottomOnStart: autoScrollToBottom,
    };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLElement>) {
    const intent = pointerIntentRef.current;
    if (!intent || intent.id !== event.pointerId) return;
    if (!historyInteractive) {
      completePointerIntent(intent);
      return;
    }
    const dx = event.clientX - intent.x;
    const dy = event.clientY - intent.y;
    if (isDraggingPhase(intent.phase)) {
      updateCompactHistoryDrag(intent, event.clientX, event.clientY);
      return;
    }
    if (intent.phase !== 'pending' && intent.phase !== 'click') return;
    const distance = Math.hypot(dx, dy);
    if (distance <= COMPACT_EXPORT_CLICK_MOVE_THRESHOLD) {
      intent.phase = 'click';
      return;
    }
    if (shouldTreatPointerMoveAsScroll(intent.pointerType, dx, dy)) {
      intent.phase = 'scroll';
      return;
    }
    if (distance >= COMPACT_EXPORT_DRAG_MOVE_THRESHOLD) {
      event.preventDefault();
      startCompactHistoryDrag(intent, event.clientX, event.clientY);
      return;
    }
    intent.phase = 'click';
  }

  function finishPointer(event: ReactPointerEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    const intent = pointerIntentRef.current;
    if (!intent || intent.id !== event.pointerId || intent.messageId !== message.id) return;
    if (
      (intent.phase === 'pending' || intent.phase === 'click')
      && selectable
      && selectionControlsInteractive
      && !isSelectionIgnoredTarget(event.target, event.currentTarget)
    ) {
      clearPointerIntentAfterDrag(intent);
      event.preventDefault();
      suppressNextClickForMessage(message.id);
      onToggleMessage(message.id);
    } else if (isDraggingPhase(intent.phase)) {
      event.preventDefault();
      suppressNextClickForMessage(message.id);
      completePointerIntent(intent, {
        clientX: event.clientX,
        clientY: event.clientY,
        tryDrop: true,
      });
    } else {
      clearPointerIntentAfterDrag(intent);
    }
  }

  function handleClick(event: ReactMouseEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (isSelectionIgnoredTarget(event.target, event.currentTarget)) return;
    if (suppressClickMessageIdRef.current === message.id) {
      suppressClickMessageIdRef.current = null;
      return;
    }
    if (!selectionControlsInteractive) return;
    onToggleMessage(message.id);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (!selectionControlsInteractive) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onToggleMessage(message.id);
  }

  function buildExportActionRequest(): CompactExportActionRequest {
    return {
      messageIds: selectedMessageIds,
      format: exportFormat,
      imageStyle,
      imageFormat,
    };
  }

  async function runExportAction(kind: 'copy' | 'download') {
    if (exportActionsDisabled) return;
    setPendingAction(kind);
    setExportActionError(null);
    try {
      const request = buildExportActionRequest();
      if (kind === 'copy') {
        await onCopyExport(request);
      } else {
        await onDownloadExport(request);
      }
    } catch (error) {
      console.error('[CompactExportHistoryPanel] export action failed', error);
      setExportActionError(i18n('chat.exportActionFailed', 'Export failed. Please try again.'));
    } finally {
      setPendingAction(null);
    }
  }

  const exportFormatOptions: { id: CompactExportFormat; label: string }[] = [
    { id: 'markdown', label: i18n('chat.exportFormatMarkdown', 'Markdown') },
    { id: 'image', label: i18n('chat.exportFormatImage', 'Image') },
  ];
  const imageStyleOptions: { id: CompactExportImageStyle; label: string }[] = [
    { id: 'neko', label: i18n('chat.exportImageStyleNeko', 'N.E.K.O') },
    { id: 'original', label: i18n('chat.exportImageStyleOriginal', 'Original') },
    { id: 'poster', label: i18n('chat.exportImageStylePoster', 'Fresh') },
    { id: 'lyrics', label: i18n('chat.exportImageStyleLyrics', 'Lyrics') },
  ];
  const imageFormatOptions: { id: CompactExportImageFormat; label: string }[] = [
    { id: 'png', label: i18n('chat.exportImageFormatPng', 'PNG') },
    { id: 'jpeg', label: i18n('chat.exportImageFormatJpeg', 'JPEG') },
    { id: 'webp', label: i18n('chat.exportImageFormatWebp', 'WebP') },
  ];

  function renderSelectedMessageFallback() {
    if (!previewHasSelection) return null;
    return (
      <div className="compact-export-preview-fallback" role="list" aria-label={i18n('chat.exportPreviewTitle', 'Export Preview')}>
        {selectedMessages.map((message) => {
          const streaming = message.status === 'streaming';
          return (
            <article
              key={message.id}
              className={clsx('compact-export-preview-message', {
                'is-user': message.role === 'user',
                'is-assistant': message.role === 'assistant' || message.role === 'tool',
                'is-system': message.role === 'system',
              })}
              role="listitem"
              data-compact-export-preview-message-id={message.id}
              data-message-role={message.role}
            >
              <div className="compact-export-preview-bubble">
                <div className="compact-export-preview-meta">
                  <span>{message.author}</span>
                  <span>{message.time}</span>
                </div>
                <div className="compact-export-preview-content">
                  {message.blocks.map((block, index) => (
                    <MessageBlockView
                      key={`${message.id}-preview-${block.type}-${index}`}
                      block={block}
                      message={message}
                      isStreaming={streaming}
                      onAction={onAction}
                    />
                  ))}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    );
  }

  function renderPreviewStage() {
    if (!previewHasSelection) {
      return (
        <div className="compact-export-preview-empty" role="status" aria-live="polite">
          {i18n('chat.exportSelectionEmpty', 'Select at least one message to export.')}
        </div>
      );
    }
    if (previewState.status === 'loading' || previewState.status === 'idle') {
      return (
        <div className="compact-export-preview-placeholder" role="status" aria-live="polite">
          {i18n('chat.exportPreviewLoading', 'Generating preview...')}
        </div>
      );
    }
    if (previewState.status === 'failed') {
      return (
        <div className="compact-export-preview-stage is-fallback">
          <div className="compact-export-preview-placeholder" role="status" aria-live="polite">
            {i18n('chat.exportPreviewFailed', 'Failed to build the preview.')}
          </div>
          {renderSelectedMessageFallback()}
        </div>
      );
    }
    if (previewState.result.previewKind === 'empty') {
      return (
        <div className="compact-export-preview-empty" role="status" aria-live="polite">
          {i18n('chat.exportSelectionEmpty', 'Select at least one message to export.')}
        </div>
      );
    }
    if (previewState.result.previewKind === 'image') {
      return (
        <div className="compact-export-preview-stage" data-compact-history-ignore-selection="true">
          <img
            className="compact-export-preview-image"
            src={previewState.result.previewUrl}
            alt={i18n('chat.exportPreviewTitle', 'Export Preview')}
          />
        </div>
      );
    }
    return (
      <div className="compact-export-preview-stage" data-compact-history-ignore-selection="true">
        <iframe
          className="compact-export-preview-frame"
          title={i18n('chat.exportPreviewTitle', 'Export Preview')}
          srcDoc={previewState.result.previewDocument}
          sandbox=""
        />
      </div>
    );
  }

  const previewNode = (
    <div
      className="compact-export-preview-region"
      data-compact-export-preview-open="true"
      data-compact-hit-region="true"
      data-compact-hit-region-id="history:preview"
      data-compact-hit-region-kind="preview"
    >
      <div className="compact-export-preview-header">
        <button
          type="button"
          className="compact-export-preview-back"
          onClick={onClosePreview}
          aria-label={i18n('chat.previewClose', 'Close')}
          title={i18n('chat.previewClose', 'Close')}
        >
          ‹
        </button>
        <div className="compact-export-preview-heading">
          <div className="compact-export-preview-title">{i18n('chat.exportPreviewTitle', 'Export Preview')}</div>
          <div className="compact-export-preview-subtitle" aria-live="polite">
            {selectedCount}/{selectableCount}
          </div>
        </div>
      </div>
      <div className="compact-export-preview-format-strip" aria-label={i18n('chat.exportFormatLabel', 'Export Format')}>
        {exportFormatOptions.map(option => (
          <button
            key={option.id}
            type="button"
            className={clsx('compact-export-preview-chip', {
              'is-active': exportFormat === option.id,
            })}
            aria-pressed={exportFormat === option.id}
            onClick={() => setExportFormat(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {exportFormat === 'image' ? (
        <div className="compact-export-preview-options" aria-label={i18n('chat.exportFormatImage', 'Image')}>
          <div className="compact-export-preview-option-row">
            {imageStyleOptions.map(option => (
              <button
                key={option.id}
                type="button"
                className={clsx('compact-export-preview-chip', {
                  'is-active': imageStyle === option.id,
                })}
                aria-pressed={imageStyle === option.id}
                onClick={() => setImageStyle(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="compact-export-preview-option-row">
            {imageFormatOptions.map(option => (
              <button
                key={option.id}
                type="button"
                className={clsx('compact-export-preview-chip', {
                  'is-active': imageFormat === option.id,
                })}
                aria-pressed={imageFormat === option.id}
                onClick={() => setImageFormat(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {renderPreviewStage()}
      <div className="compact-export-preview-actions" role="group" aria-label={i18n('chat.exportAction', 'Export')}>
        <button
          type="button"
          className="compact-export-preview-action"
          disabled={exportActionsDisabled}
          onClick={() => { void runExportAction('copy'); }}
        >
          {i18n('chat.copyToClipboard', 'Copy to Clipboard')}
        </button>
        <button
          type="button"
          className="compact-export-preview-action compact-export-preview-action-primary"
          disabled={exportActionsDisabled}
          onClick={() => { void runExportAction('download'); }}
        >
          {i18n('chat.exportAction', 'Export')}
        </button>
      </div>
      {exportActionError ? (
        <div className="compact-export-preview-error" role="status" aria-live="polite">
          {exportActionError}
        </div>
      ) : null}
    </div>
  );

  const activeDragMessage = activeDrag ? messages.find(message => message.id === activeDrag.messageId) : null;
  const activeDragElastic = activeDrag ? getCompactHistoryElasticGeometry(activeDrag) : null;
  const dragLayerNode = activeDrag ? (
    <>
      <svg className="compact-history-drag-filter-defs" aria-hidden="true" focusable="false">
        <defs>
          <filter id="compact-history-goo-filter" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
            <feColorMatrix
              in="blur"
              mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 20 -8"
              result="goo"
            />
          </filter>
        </defs>
      </svg>
      <svg
        className="compact-history-drag-elastic"
        data-compact-drag-elastic="true"
        data-compact-drag-phase={activeDrag.phase}
        data-compact-drag-over-target={activeDrag.overDropTarget ? 'true' : 'false'}
        data-compact-drag-role={activeDrag.role}
        style={{
          ...getCompactHistoryDragPalette(activeDrag),
          '--compact-history-elastic-pull': `${activeDragElastic?.pull ?? 0}`,
          '--compact-history-elastic-opacity': `${activeDragElastic?.opacity ?? 0.9}`,
        } as CSSProperties & Record<string, string>}
        ref={dragElasticLayerRef}
        width="100%"
        height="100%"
        focusable="false"
        aria-hidden="true"
      >
        <g className="compact-history-drag-goo-group">
          {activeDragElastic?.shellPath ? (
            <path
              ref={dragBubbleShellPathRef}
              className="compact-history-drag-bubble-shell-fill"
              d={activeDragElastic.shellPath}
            />
          ) : null}
          <path
            ref={dragElasticPathRef}
            className="compact-history-drag-elastic-fill"
            d={activeDragElastic?.path}
          />
        </g>
      </svg>
      <div
        className="compact-history-drag-layer"
        data-compact-drag-layer="true"
        data-compact-drag-type={activeDrag.type}
        data-compact-drag-phase={activeDrag.phase}
        data-compact-drag-over-target={activeDrag.overDropTarget ? 'true' : 'false'}
        data-compact-drag-role={activeDrag.role}
        data-compact-drag-message-id={activeDrag.messageId}
        data-compact-drag-block-index={activeDrag.blockIndex ?? undefined}
        style={getCompactHistoryDragVisualStyle(activeDrag)}
        aria-hidden="true"
        ref={(node) => {
          dragLayerRef.current = node;
          node?.setAttribute('inert', '');
        }}
      >
        <div className="compact-history-drag-shadow">
          {activeDrag.payload.type === 'image' ? (
            <img
              className="compact-history-drag-image"
              src={activeDrag.payload.url}
              alt=""
              draggable={false}
            />
          ) : activeDragMessage ? (
            <div className="compact-history-drag-bubble">
              <div className="compact-history-drag-bubble-content">
                {activeDrag.payload.blocks.map((block, index) => (
                  <MessageBlockView
                    key={`${activeDrag.messageId}-${block.type}-${index}`}
                    block={block}
                    message={activeDragMessage}
                    isStreaming={false}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </>
  ) : null;

  const dragLayerPortalNode = dragLayerNode && typeof document !== 'undefined'
    ? createPortal(dragLayerNode, document.body)
    : dragLayerNode;

  return (
    <>
      {dragLayerPortalNode}
      <section
        className={clsx('compact-export-history-anchor', {
          'under-choice-prompt': choiceLayerAbove,
          'has-preview': previewOpen,
          'controls-collapsed': !previewOpen && !controlsOpen,
        })}
        data-compact-geometry-owner="surface"
        data-compact-geometry-item="history"
        data-compact-geometry-hit-scope="children"
        data-compact-no-drag="true"
        data-compact-export-history-open={historyInteractive ? 'true' : 'false'}
        data-compact-export-history-visibility={visibilityState}
        data-compact-export-preview-open={previewOpen ? 'true' : 'false'}
        data-compact-export-under-choice={choiceLayerAbove ? 'true' : 'false'}
        aria-label={i18n('chat.exportConversation', 'Export Conversation')}
        onPointerDown={(event) => event.stopPropagation()}
        onPointerMove={(event) => event.stopPropagation()}
        onPointerUp={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="compact-export-history-panel">
        {previewOpen ? previewNode : (
          <>
            <div
              ref={scrollRef}
              className="compact-export-history-scroll"
              role="list"
              aria-label={i18n('chat.messageListAriaLabel', 'Chat messages')}
              data-compact-scrollbar-visible={scrollbarVisible ? 'true' : undefined}
              onScroll={handleScroll}
              onWheel={handleWheel}
              onTouchMove={(event) => event.stopPropagation()}
            >
              {messages.length > 0 ? (
                <div className="compact-export-history-scroll-content">
                  {messages.map((message, index) => {
                    const selectable = isCompactExportMessageSelectable(message);
                    const selected = selectedIds.has(message.id);
                    const selectionEnabled = selectionControlsInteractive && selectable;
                    const failed = message.status === 'failed';
                    const streaming = message.status === 'streaming';
                    const tone = getCompactHistoryBubbleTone(message, index, messages[index - 1]);
                    const motionStyle: CSSProperties & Record<string, string> = {
                      '--compact-history-enter-delay': resolveCompactHistoryEnterDelay(message, index),
                      '--compact-history-exit-delay': computeCompactHistoryExitDelay(index),
                    };
                    return (
                      <article
                        key={message.id}
                        className={getCompactHistoryMessageClassName(message, selected, selectable, selectedCount > 0)}
                        style={{
                          ...tone.style,
                          ...motionStyle,
                          ...(activeDrag?.messageId === message.id ? getCompactHistorySourceStyle(activeDrag) : null),
                        }}
                        role="listitem"
                        data-compact-export-history-message-id={message.id}
                        data-compact-history-group={tone.group}
                        data-compact-history-complexity={tone.complexity}
                        data-message-role={message.role}
                        data-message-status={message.status || ''}
                        data-compact-history-drag-source={activeDrag?.messageId === message.id ? activeDrag.type : undefined}
                        data-compact-history-drag-phase={activeDrag?.messageId === message.id ? activeDrag.phase : undefined}
                        data-compact-history-drag-over-target={activeDrag?.messageId === message.id && activeDrag.overDropTarget ? 'true' : undefined}
                      >
                        <div
                          className="compact-export-history-bubble"
                          role={selectionEnabled ? 'button' : undefined}
                          aria-pressed={selectionEnabled ? selected : undefined}
                          aria-disabled={!selectionEnabled}
                          tabIndex={selectionEnabled ? 0 : -1}
                          data-compact-hit-region={historyInteractive ? 'true' : undefined}
                          data-compact-hit-region-id={historyInteractive ? `history:message:${message.id}` : undefined}
                          data-compact-hit-region-kind={historyInteractive ? 'message' : undefined}
                          onPointerDown={(event) => handlePointerDown(event, message, selectable)}
                          onPointerMove={handlePointerMove}
                          onPointerUp={(event) => finishPointer(event, message, selectable)}
                          onPointerCancel={() => { completePointerIntent(pointerIntentRef.current); }}
                          onClick={(event) => handleClick(event, message, selectable)}
                          onKeyDown={(event) => handleKeyDown(event, message, selectable)}
                        >
                          <span className="compact-export-history-check" aria-hidden="true" />
                          <div className="compact-export-history-meta">
                            <span className="compact-export-history-author">{message.author}</span>
                            <span className="compact-export-history-time">{message.time}</span>
                            {failed ? <span className="compact-export-history-status">{failedStatusLabel}</span> : null}
                            {streaming ? <span className="compact-export-history-status">...</span> : null}
                          </div>
                          <div className="compact-export-history-content">
                            {message.blocks.map((block, index) => (
                              <MessageBlockView
                                key={`${message.id}-${block.type}-${index}`}
                                block={block}
                                message={message}
                                isStreaming={streaming}
                                onAction={onAction}
                              />
                            ))}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              ) : null}
            </div>
            <div
              className="compact-export-history-music-mount"
              data-music-player-mount="compact-history"
              data-compact-hit-region={historyInteractive ? 'true' : undefined}
              data-compact-hit-region-id={historyInteractive ? 'history:music-player' : undefined}
              data-compact-hit-region-kind={historyInteractive ? 'music' : undefined}
            />
            {controlsOpen ? (
              <div
                className="compact-export-history-controls"
                role="group"
                aria-label={i18n('chat.exportConversation', 'Export Conversation')}
                aria-disabled={!historyInteractive}
                data-compact-export-controls-open="true"
                data-compact-hit-region={historyInteractive ? 'true' : undefined}
                data-compact-hit-region-id={historyInteractive ? 'history:controls' : undefined}
                data-compact-hit-region-kind={historyInteractive ? 'controls' : undefined}
              >
                <div className="compact-export-history-controls-content">
                  <div className="compact-export-history-count" aria-live="polite">
                    {selectedCount}/{selectableCount}
                  </div>
                  <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectableCount <= 0} onClick={onSelectAll}>
                    {i18n('chat.exportSelectAll', 'Select All')}
                  </button>
                  <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectedCount <= 0} onClick={onClearSelection}>
                    {i18n('chat.exportSelectNone', 'Clear')}
                  </button>
                  <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectableCount <= 0} onClick={onInvertSelection}>
                    {i18n('chat.exportSelectInvert', 'Invert')}
                  </button>
                  <button
                    type="button"
                    className="compact-export-history-control compact-export-history-export"
                    disabled={!historyInteractive}
                    onClick={onRequestPreview}
                  >
                    {i18n('chat.exportAction', 'Export')}
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
        </div>
      </section>
    </>
  );
}
