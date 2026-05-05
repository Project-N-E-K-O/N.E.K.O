import { useState, useEffect, useMemo, useRef, useCallback, type CSSProperties } from 'react';
import MessageList from './MessageList';
import { i18n } from './i18n';
import {
  type ChatMessage,
  type MessageAction,
  type ChatWindowSchemaProps,
  type ComposerSubmitPayload,
  type ComposerAttachment,
  type AvatarInteractionPayload,
  type AvatarToolStatePayload,
  type GalgameOption,
  type ChoiceOption,
  type ChoicePrompt,
  type ChoicePromptSource,
} from './message-schema';

export type ChatWindowProps = ChatWindowSchemaProps & {
  onMessageAction?: (message: ChatMessage, action: MessageAction) => void;
  onComposerImportImage?: () => void;
  onComposerScreenshot?: () => void;
  onComposerRemoveAttachment?: (attachmentId: ComposerAttachment['id']) => void;
  onComposerSubmit?: (payload: ComposerSubmitPayload) => void;
  onAvatarInteraction?: (payload: AvatarInteractionPayload) => void;
  onAvatarToolStateChange?: (payload: AvatarToolStatePayload) => void;
  onJukeboxClick?: () => void;
  onTranslateToggle?: () => void;
  onGalgameModeToggle?: () => void;
  onGalgameOptionSelect?: (option: GalgameOption) => void;
  // Generic ChoicePrompt（mini-game invite 等通用三选项框架）。
  // galgame mode 现有路径继续走 galgameOptions / onGalgameOptionSelect（BC）；
  // 本框架先只承载 mini_game_invite，未来可把 galgame 也迁过来。
  choicePrompt?: ChoicePrompt | null;
  onChoiceSelect?: (option: ChoiceOption, source: ChoicePromptSource) => void;
};

const defaultMessages: ChatMessage[] = [];
type AvatarToolId = AvatarInteractionPayload['toolId'];

type ToolIconItem = {
  id: AvatarToolId;
  labelKey: string;
  labelFallback: string;
  iconImagePath: string;
  iconImagePathAlt?: string;
  iconImagePathAlt2?: string;
  menuIconScale?: number;
  menuIconOffsetX?: number;
  menuIconOffsetY?: number;
  menuIconOffsetXAlt?: number;
  menuIconOffsetYAlt?: number;
  menuIconOffsetXAlt2?: number;
  menuIconOffsetYAlt2?: number;
  cursorImagePath: string;
  cursorImagePathAlt?: string;
  cursorImagePathAlt2?: string;
  cursorHotspotX?: number;
  cursorHotspotY?: number;
};

const toolIconItems: ToolIconItem[] = [
  {
    id: 'lollipop',
    labelKey: 'chat.toolLollipop',
    labelFallback: '棒棒糖',
    iconImagePath: '/static/icons/chat_sugar1.png',
    iconImagePathAlt: '/static/icons/chat_sugar2.png',
    iconImagePathAlt2: '/static/icons/chat_sugar3.png',
    cursorImagePath: '/static/icons/chat_sugar1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_sugar2_cursor.png',
    menuIconScale: 1.18,
    cursorHotspotX: 27,
    cursorHotspotY: 46,
  },
  {
    id: 'fist',
    labelKey: 'chat.toolFist',
    labelFallback: '猫爪',
    iconImagePath: '/static/icons/cat_claw1.png',
    iconImagePathAlt: '/static/icons/cat_claw2.png',
    cursorImagePath: '/static/icons/cat_claw1_cursor.png',
    cursorImagePathAlt: '/static/icons/cat_claw2_cursor.png',
    cursorHotspotX: 39,
    cursorHotspotY: 46,
  },
  {
    id: 'hammer',
    labelKey: 'chat.toolHammer',
    labelFallback: '锤子',
    iconImagePath: '/static/icons/chat_hammer1.png',
    iconImagePathAlt: '/static/icons/chat_hammer2.png',
    cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_hammer2_cursor.png',
    menuIconScale: 1.42,
    menuIconOffsetX: -6,
    menuIconOffsetY: 1,
    menuIconOffsetXAlt: 1,
    menuIconOffsetYAlt: -1,
    cursorHotspotX: 50,
    cursorHotspotY: 54,
  },
];

const hammerToolItem = toolIconItems.find(item => item.id === 'hammer') ?? null;
const hammerOverlayTransformOrigin = {
  x: 60,
  y: 118,
};

function getToolItemLabel(item: ToolIconItem): string {
  return i18n(item.labelKey, item.labelFallback);
}

const avatarToolRangePadding = 100;
const avatarToolRangeHoldMs = 180;
const compactCursorZoneSelector = [
  '.composer-bottom-tools',
  '.composer-tool-menu',
  '.composer-icon-popover',
  '.composer-tool-btn',
  '.composer-icon-button',
  '.send-button-circle',
  '.window-topbar-actions',
  '.topbar-action-btn',
  '.message-action-button',
  '#live2d-floating-buttons',
  '#vrm-floating-buttons',
  '#mmd-floating-buttons',
  '#live2d-return-button-container',
  '#vrm-return-button-container',
  '#mmd-return-button-container',
  '#live2d-lock-icon',
  '#vrm-lock-icon',
  '#mmd-lock-icon',
  '.live2d-floating-btn',
  '.vrm-floating-btn',
  '.mmd-floating-btn',
  '.live2d-trigger-btn',
  '.vrm-trigger-btn',
  '.mmd-trigger-btn',
  '.live2d-return-btn',
  '.vrm-return-btn',
  '.mmd-return-btn',
  '.live2d-popup',
  '.vrm-popup',
  '.mmd-popup',
  '[id^="live2d-popup-"]',
  '[id^="vrm-popup-"]',
  '[id^="mmd-popup-"]',
  '[data-neko-sidepanel]',
].join(', ');

type CursorVariant = 'primary' | 'secondary' | 'tertiary';
type ToolCursorVariantState = Record<string, CursorVariant>;
type InteractionIntensity = NonNullable<AvatarInteractionPayload['intensity']>;
type AvatarInteractionToolId = AvatarToolId;
type AvatarTouchZone = 'ear' | 'head' | 'face' | 'body';
type AvatarInteractionPayloadByTool = {
  [K in AvatarInteractionToolId]: Extract<AvatarInteractionPayload, { toolId: K }>;
};

type HostAvatarBounds = {
  left: number;
  right: number;
  top: number;
  bottom: number;
  width: number;
  height: number;
  centerX?: number;
  centerY?: number;
};

type HostAvatarManager = {
  currentModel?: unknown;
  getModelScreenBounds?: () => HostAvatarBounds | null;
};

type AvatarBoundsCacheEntry = {
  bounds: HostAvatarBounds;
};

type AvatarToolCacheState = {
  loadedCursorImageCache: Map<string, Promise<HTMLImageElement>>;
  compactCursorValueCache: Map<string, Promise<string>>;
  avatarBoundsCacheTtlMs: number;
  avatarBoundsCache: {
    expiresAt: number;
    entries: AvatarBoundsCacheEntry[];
  };
};

type AvatarRangeHit = {
  bounds: HostAvatarBounds;
  touchZone: AvatarTouchZone;
};

type FloatingHeart = {
  id: number;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  scale: number;
  delayMs: number;
};

type FloatingFistDrop = {
  id: number;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  rotation: number;
  scale: number;
  delayMs: number;
};

function resolveToolImagePaths(item: ToolIconItem, variant: CursorVariant) {
  return {
    iconImagePath: variant === 'tertiary' && item.iconImagePathAlt2
      ? item.iconImagePathAlt2
      : variant === 'secondary' && item.iconImagePathAlt
        ? item.iconImagePathAlt
        : item.iconImagePath,
    cursorImagePath: variant === 'tertiary' && item.cursorImagePathAlt2
      ? item.cursorImagePathAlt2
      : variant === 'secondary' && item.cursorImagePathAlt
        ? item.cursorImagePathAlt
        : variant === 'tertiary' && item.cursorImagePathAlt
          ? item.cursorImagePathAlt
          : item.cursorImagePath,
  };
}

function resolveMenuIconVisual(item: ToolIconItem, variant: CursorVariant) {
  const imagePath = variant === 'tertiary' && item.iconImagePathAlt2
    ? item.iconImagePathAlt2
    : variant === 'secondary' && item.iconImagePathAlt
      ? item.iconImagePathAlt
      : item.iconImagePath;
  const offsetX = variant === 'tertiary'
    ? (item.menuIconOffsetXAlt2 ?? item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
      : (item.menuIconOffsetX ?? 0);
  const offsetY = variant === 'tertiary'
    ? (item.menuIconOffsetYAlt2 ?? item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
      : (item.menuIconOffsetY ?? 0);

  return {
    imagePath,
    offsetX,
    offsetY,
  };
}

function loadCursorImage(imagePath: string, cacheState: AvatarToolCacheState): Promise<HTMLImageElement> {
  const cached = cacheState.loadedCursorImageCache.get(imagePath);
  if (cached) return cached;

  const pending = new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load cursor image: ${imagePath}`));
    image.src = imagePath;
  });

  cacheState.loadedCursorImageCache.set(imagePath, pending);
  return pending;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

async function resolveCompactCursorValue(
  item: ToolIconItem,
  variant: CursorVariant,
  cacheState: AvatarToolCacheState,
): Promise<string> {
  const { iconImagePath, cursorImagePath } = resolveToolImagePaths(item, variant);
  const cursorScale = item.menuIconScale ?? 1;
  const cacheKey = [
    iconImagePath,
    cursorImagePath,
    cursorScale,
    item.cursorHotspotX ?? 18,
    item.cursorHotspotY ?? 18,
  ].join('|');

  const cached = cacheState.compactCursorValueCache.get(cacheKey);
  if (cached) return cached;

  const pending = Promise.all([
    loadCursorImage(iconImagePath, cacheState),
    loadCursorImage(cursorImagePath, cacheState),
  ]).then(([iconImage, cursorImage]) => {
    const boxSize = Math.max(32, Math.round(40 * cursorScale));
    const scale = Math.min(boxSize / iconImage.naturalWidth, boxSize / iconImage.naturalHeight);
    const drawWidth = Math.max(1, Math.round(iconImage.naturalWidth * scale));
    const drawHeight = Math.max(1, Math.round(iconImage.naturalHeight * scale));
    const offsetX = Math.round((boxSize - drawWidth) / 2);
    const offsetY = Math.round((boxSize - drawHeight) / 2);

    const canvas = document.createElement('canvas');
    canvas.width = boxSize;
    canvas.height = boxSize;
    const context = canvas.getContext('2d');
    if (!context) {
      return resolveCursorValue(item, variant);
    }

    context.clearRect(0, 0, boxSize, boxSize);
    context.drawImage(iconImage, offsetX, offsetY, drawWidth, drawHeight);

    const hotspotRatioX = (item.cursorHotspotX ?? 18) / Math.max(cursorImage.naturalWidth, 1);
    const hotspotRatioY = (item.cursorHotspotY ?? 18) / Math.max(cursorImage.naturalHeight, 1);
    const hotspotX = clamp(Math.round(offsetX + drawWidth * hotspotRatioX), 0, boxSize - 1);
    const hotspotY = clamp(Math.round(offsetY + drawHeight * hotspotRatioY), 0, boxSize - 1);

    return `url("${canvas.toDataURL('image/png')}") ${hotspotX} ${hotspotY}, auto`;
  }).catch(() => resolveCursorValue(item, variant));

  cacheState.compactCursorValueCache.set(cacheKey, pending);
  return pending;
}

function resolveCursorValue(item: ToolIconItem, variant: CursorVariant): string {
  const { cursorImagePath: imagePath } = resolveToolImagePaths(item, variant);
  const hotspotX = typeof item.cursorHotspotX === 'number' ? item.cursorHotspotX : 18;
  const hotspotY = typeof item.cursorHotspotY === 'number' ? item.cursorHotspotY : 18;
  return `url("${imagePath}") ${hotspotX} ${hotspotY}, auto`;
}

function supportsDesktopFinePointer(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true;
  }

  try {
    return window.matchMedia('(pointer: fine)').matches;
  } catch {
    return true;
  }
}

function isElectronMultiWindowHost(): boolean {
  return typeof window !== 'undefined'
    && (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ === true;
}

function clearForcedNativeCursorFallback() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.style.removeProperty('cursor');
  document.body?.style.removeProperty('cursor');
}

function clearGlobalToolCursorState() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.classList.remove('neko-tool-cursor-active');
  root.style.removeProperty('--neko-chat-tool-cursor');
  root.style.setProperty('cursor', 'auto', 'important');
  document.body?.style.setProperty('cursor', 'auto', 'important');
}

function isElementVisible(elementId: string): boolean {
  const element = document.getElementById(elementId);
  if (!element) return false;
  const computedStyle = window.getComputedStyle(element);
  return computedStyle.display !== 'none'
    && computedStyle.visibility !== 'hidden'
    && computedStyle.opacity !== '0'
    && element.getClientRects().length > 0;
}

function isPointInsideAvatarBounds(bounds: HostAvatarBounds, clientX: number, clientY: number): boolean {
  if (
    clientX < bounds.left - avatarToolRangePadding
    || clientX > bounds.right + avatarToolRangePadding
    || clientY < bounds.top - avatarToolRangePadding
    || clientY > bounds.bottom + avatarToolRangePadding
  ) {
    return false;
  }

  const centerX = typeof bounds.centerX === 'number'
    ? bounds.centerX
    : (bounds.left + bounds.right) / 2;
  const centerY = typeof bounds.centerY === 'number'
    ? bounds.centerY
    : (bounds.top + bounds.bottom) / 2;
  const radiusX = bounds.width * 0.3 + avatarToolRangePadding;
  const radiusY = bounds.height * 0.475 + avatarToolRangePadding;
  if (radiusX <= 0 || radiusY <= 0) return false;

  const normalizedX = (clientX - centerX) / radiusX;
  const normalizedY = (clientY - centerY) / radiusY;
  return normalizedX * normalizedX + normalizedY * normalizedY <= 1;
}

function getAvatarBoundsEntries(cacheState: AvatarToolCacheState): AvatarBoundsCacheEntry[] {
  const now = performance.now();
  if (cacheState.avatarBoundsCache.expiresAt <= now) {
    const hostWindow = window as Window & {
      mmdManager?: HostAvatarManager;
      vrmManager?: HostAvatarManager;
      live2dManager?: HostAvatarManager;
    };

    const candidates: Array<{ containerId: string; manager: HostAvatarManager | undefined }> = [
      { containerId: 'mmd-container', manager: hostWindow.mmdManager },
      { containerId: 'vrm-container', manager: hostWindow.vrmManager },
      { containerId: 'live2d-container', manager: hostWindow.live2dManager },
    ];

    cacheState.avatarBoundsCache = {
      expiresAt: now + cacheState.avatarBoundsCacheTtlMs,
      entries: candidates.flatMap(({ containerId, manager }) => {
        if (!manager?.currentModel || typeof manager.getModelScreenBounds !== 'function') {
          return [];
        }
        if (!isElementVisible(containerId)) return [];

        try {
          const bounds = manager.getModelScreenBounds();
          return bounds ? [{ bounds }] : [];
        } catch {
          return [];
        }
      }),
    };
  }

  return cacheState.avatarBoundsCache.entries;
}

function classifyAvatarTouchZone(bounds: HostAvatarBounds, clientX: number, clientY: number): AvatarTouchZone {
  if (bounds.width <= 0 || bounds.height <= 0) {
    return 'body';
  }

  const relativeX = clamp((clientX - bounds.left) / bounds.width, 0, 1);
  const relativeY = clamp((clientY - bounds.top) / bounds.height, 0, 1);

  if (relativeY <= 0.24 && (relativeX <= 0.24 || relativeX >= 0.76)) {
    return 'ear';
  }
  if (relativeY <= 0.34) {
    return 'head';
  }
  if (relativeY <= 0.62) {
    return 'face';
  }
  return 'body';
}

function getAvatarRangeHit(
  clientX: number,
  clientY: number,
  cacheState: AvatarToolCacheState,
): AvatarRangeHit | null {
  const matchedEntry = getAvatarBoundsEntries(cacheState).find(({ bounds }) => (
    isPointInsideAvatarBounds(bounds, clientX, clientY)
  ));
  if (!matchedEntry) {
    return null;
  }
  return {
    bounds: matchedEntry.bounds,
    touchZone: classifyAvatarTouchZone(matchedEntry.bounds, clientX, clientY),
  };
}

function isPointerWithinAvatarRange(
  clientX: number,
  clientY: number,
  cacheState: AvatarToolCacheState,
): boolean {
  return getAvatarRangeHit(clientX, clientY, cacheState) !== null;
}

function clearAvatarBoundsCache(cacheState: AvatarToolCacheState) {
  cacheState.avatarBoundsCache = {
    expiresAt: 0,
    entries: [],
  };
}

function isPointerOverCompactCursorZone(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(compactCursorZoneSelector);
}

function isPointWithinCompactCursorZone(clientX: number, clientY: number): boolean {
  if (typeof document === 'undefined') return false;

  const hitElements = typeof document.elementsFromPoint === 'function'
    ? document.elementsFromPoint(clientX, clientY)
    : (
      typeof document.elementFromPoint === 'function'
        ? [document.elementFromPoint(clientX, clientY)].filter((element): element is Element => element instanceof Element)
        : []
    );

  return hitElements.some(element => !!element.closest(compactCursorZoneSelector));
}

function resolveEffectiveCursorVariant(
  toolId: string | null,
  avatarRangeVariants: ToolCursorVariantState,
  outsideRangeVariants: ToolCursorVariantState,
  isWithinAvatarRange: boolean,
): CursorVariant {
  const avatarRangeVariant = toolId ? (avatarRangeVariants[toolId] ?? 'primary') : 'primary';
  const outsideRangeVariant = toolId ? (outsideRangeVariants[toolId] ?? 'primary') : 'primary';
  if (toolId === 'lollipop') {
    return avatarRangeVariant;
  }
  if (toolId === 'hammer') {
    return isWithinAvatarRange
      ? 'primary'
      : outsideRangeVariant;
  }
  return isWithinAvatarRange ? avatarRangeVariant : outsideRangeVariant;
}

function createDefaultToolCursorVariantState(): ToolCursorVariantState {
  return Object.fromEntries(toolIconItems.map(item => [item.id, 'primary'])) as ToolCursorVariantState;
}

function createAvatarInteractionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `avatar-int-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function sanitizeInteractionTextContext(text: string): string | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  return trimmed.length > 80 ? trimmed.slice(0, 80).trimEnd() : trimmed;
}

export default function App({
  title = i18n('chat.title', 'N.E.K.O Chat'),
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = i18n('chat.textInputPlaceholder', 'Type a message...'),
  sendButtonLabel = i18n('chat.send', 'Send'),
  chatWindowAriaLabel = i18n('chat.reactWindowAriaLabel', 'Neko chat window'),
  messageListAriaLabel = i18n('chat.messageListAriaLabel', 'Chat messages'),
  composerToolsAriaLabel = i18n('chat.composerToolsAriaLabel', 'Composer tools'),
  composerHidden = false,
  composerAttachments = [],
  composerAttachmentsAriaLabel = i18n('chat.pendingImagesAriaLabel', 'Pending attachments'),
  importImageButtonLabel = i18n('chat.importImage', 'Import Image'),
  screenshotButtonLabel = i18n('chat.screenshot', 'Screenshot'),
  importImageButtonAriaLabel,
  screenshotButtonAriaLabel,
  removeAttachmentButtonAriaLabel = i18n('chat.removePendingImage', 'Remove image'),
  failedStatusLabel = i18n('chat.messageFailed', 'Failed'),
  jukeboxButtonLabel = i18n('chat.jukeboxLabel', 'Jukebox'),
  jukeboxButtonAriaLabel = i18n('chat.jukebox', 'Jukebox'),
  translateEnabled = false,
  translateButtonLabel = i18n('subtitle.enable', 'Subtitle Translation'),
  translateButtonAriaLabel,
  galgameModeEnabled = false,
  galgameOptions = [],
  galgameOptionsLoading = false,
  galgameToggleButtonLabel = i18n('chat.galgameToggle', 'GalGame Mode'),
  galgameToggleButtonAriaLabel,
  galgameLoadingLabel = i18n('chat.galgameLoading', '生成回复选项中…'),
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
  onAvatarInteraction,
  onAvatarToolStateChange,
  onJukeboxClick,
  onTranslateToggle,
  onGalgameModeToggle,
  onGalgameOptionSelect,
  choicePrompt = null,
  onChoiceSelect,
  rollbackDraft,
  _rollbackKey,
  _toolCursorResetKey,
}: ChatWindowProps) {
  const [draft, setDraft] = useState('');
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  // 当 composer-bottom-bar 宽度 < 阈值时，把右侧 4 个工具按钮折叠成 ··· 菜单。
  // 用四态机让进出过渡都跑完动画再切稳态：
  //   expanded   → collapsing (右→左级联收起) → compact   (··· 入场)
  //   compact    → expanding  (··· 退场)       → expanded  (左→右级联展开)
  // 中途 resize 反向：collapsing↔expanded、expanding↔compact 直接跳回稳态。
  type ComposerLayout = 'expanded' | 'collapsing' | 'compact' | 'expanding';
  const [composerLayout, setComposerLayout] = useState<ComposerLayout>('expanded');
  const showRightTools = composerLayout === 'expanded' || composerLayout === 'collapsing';
  // 折叠瞬间记录右 4 按钮组的实际宽度，喂给 CSS keyframe 做 width 动画。
  // 没这个 layout 不会跟着动画收缩，发送按钮就被"顶住"直到 scaleX 跑完。
  const [collapseFromWidth, setCollapseFromWidth] = useState<number | null>(null);
  const [overflowMenuOpen, setOverflowMenuOpen] = useState(false);
  const [activeCursorToolId, setActiveCursorToolId] = useState<string | null>(null);
  const [avatarRangeCursorVariants, setAvatarRangeCursorVariants] = useState<ToolCursorVariantState>(() => createDefaultToolCursorVariantState());
  const [outsideRangeCursorVariants, setOutsideRangeCursorVariants] = useState<ToolCursorVariantState>(() => createDefaultToolCursorVariantState());
  const [isCursorOverAvatarRange, setIsCursorOverAvatarRange] = useState(false);
  const [isCursorOverCompactCursorZone, setIsCursorOverCompactCursorZone] = useState(false);
  const [isCursorInsideHostWindow, setIsCursorInsideHostWindow] = useState(true);
  const [hammerSwingPhase, setHammerSwingPhase] = useState<'idle' | 'windup' | 'swing' | 'impact' | 'recover'>('idle');
  const [isInnerHammerEasterEggActive, setIsInnerHammerEasterEggActive] = useState(false);
  const toolMenuRef = useRef<HTMLDivElement | null>(null);
  const composerBottomBarRef = useRef<HTMLDivElement | null>(null);
  const composerToolsRightRef = useRef<HTMLDivElement | null>(null);
  // 镜像 composerLayout 到 ref，让 ResizeObserver 闭包能读到最新稳态
  const composerLayoutRef = useRef<ComposerLayout>('expanded');
  const overflowMenuRef = useRef<HTMLDivElement | null>(null);
  const avatarCursorOverlayRef = useRef<HTMLDivElement | null>(null);
  const hammerCursorOverlayRef = useRef<HTMLDivElement | null>(null);
  const hammerSwingTimeoutIdsRef = useRef<number[]>([]);
  const outsideHammerResetTimeoutRef = useRef<number | null>(null);
  const floatingHeartIdRef = useRef(0);
  const floatingHeartTimeoutIdsRef = useRef<number[]>([]);
  const floatingFistDropIdRef = useRef(0);
  const floatingFistDropTimeoutIdsRef = useRef<number[]>([]);
  const interactionBurstHistoryRef = useRef<Record<string, number[]>>({});
  const latestPointerPositionRef = useRef({ x: 0, y: 0 });
  const latestPointerTargetRef = useRef<EventTarget | null>(null);
  const avatarRangeHoldUntilRef = useRef(0);
  const avatarRangeHoldTimerRef = useRef<number | null>(null);
  const draftRef = useRef(draft);
  const avatarInteractionCallbackRef = useRef(onAvatarInteraction);
  const avatarToolCacheState = useMemo<AvatarToolCacheState>(() => ({
    loadedCursorImageCache: new Map<string, Promise<HTMLImageElement>>(),
    compactCursorValueCache: new Map<string, Promise<string>>(),
    avatarBoundsCacheTtlMs: 80,
    avatarBoundsCache: {
      expiresAt: 0,
      entries: [],
    },
  }), []);
  const [floatingHearts, setFloatingHearts] = useState<FloatingHeart[]>([]);
  const [floatingFistDrops, setFloatingFistDrops] = useState<FloatingFistDrop[]>([]);
  const submittingRef = useRef(false);
  const lastRollbackKeyRef = useRef('');
  const lastToolCursorResetKeyRef = useRef('');
  const canSubmit = draft.trim().length > 0 || composerAttachments.length > 0;
  const clearActiveCursorToolSelection = useCallback(() => {
    clearGlobalToolCursorState();
    latestPointerTargetRef.current = null;
    avatarRangeHoldUntilRef.current = 0;
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }
    setActiveCursorToolId(null);
    setToolMenuOpen(false);
    setIsCursorOverAvatarRange(false);
    setIsCursorOverCompactCursorZone(false);
  }, []);
  const setCursorOverAvatarRange = useCallback((nextValue: boolean, options?: { allowHold?: boolean }) => {
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }

    if (nextValue) {
      const holdUntil = performance.now() + avatarToolRangeHoldMs;
      avatarRangeHoldUntilRef.current = holdUntil;
      setIsCursorOverAvatarRange(previousValue => (
        previousValue === true ? previousValue : true
      ));
      avatarRangeHoldTimerRef.current = window.setTimeout(() => {
        avatarRangeHoldTimerRef.current = null;
        if (performance.now() < avatarRangeHoldUntilRef.current) return;
        avatarRangeHoldUntilRef.current = 0;
        setIsCursorOverAvatarRange(previousValue => (previousValue ? false : previousValue));
      }, avatarToolRangeHoldMs);
      return;
    }

    setIsCursorOverAvatarRange(previousValue => {
      const shouldHold = options?.allowHold !== false
        && previousValue
        && performance.now() <= avatarRangeHoldUntilRef.current;
      if (shouldHold) {
        if (avatarRangeHoldTimerRef.current === null) {
          const delay = Math.max(0, avatarRangeHoldUntilRef.current - performance.now());
          avatarRangeHoldTimerRef.current = window.setTimeout(() => {
            avatarRangeHoldTimerRef.current = null;
            if (performance.now() < avatarRangeHoldUntilRef.current) return;
            avatarRangeHoldUntilRef.current = 0;
            setIsCursorOverAvatarRange(currentValue => (currentValue ? false : currentValue));
          }, delay);
        }
        return true;
      }
      if (avatarRangeHoldTimerRef.current !== null) {
        window.clearTimeout(avatarRangeHoldTimerRef.current);
        avatarRangeHoldTimerRef.current = null;
      }
      if (avatarRangeHoldUntilRef.current !== 0) {
        avatarRangeHoldUntilRef.current = 0;
      }
      return previousValue ? false : previousValue;
    });
  }, []);

  // Rollback draft when host signals a RESPONSE_TOO_LONG error
  // Use _rollbackKey for dedup — it changes on every rollbackLastDraft() call
  // and stays the same across intermediate renderWindow() calls, so the rollback
  // is applied exactly once regardless of how many times renderWindow fires.
  useEffect(() => {
    if (rollbackDraft && _rollbackKey && _rollbackKey !== lastRollbackKeyRef.current) {
      lastRollbackKeyRef.current = _rollbackKey;
      if (!draft || draft.trim() === '') {
        setDraft(rollbackDraft);
      }
    }
  }, [rollbackDraft, _rollbackKey, draft]);

  useEffect(() => {
    if (_toolCursorResetKey && _toolCursorResetKey !== lastToolCursorResetKeyRef.current) {
      lastToolCursorResetKeyRef.current = _toolCursorResetKey;
      clearActiveCursorToolSelection();
    }
  }, [_toolCursorResetKey, clearActiveCursorToolSelection]);
  const resolvedImportImageAriaLabel = importImageButtonAriaLabel || importImageButtonLabel;
  const resolvedScreenshotAriaLabel = screenshotButtonAriaLabel || screenshotButtonLabel;
  const resolvedTranslateAriaLabel = translateButtonAriaLabel || translateButtonLabel;
  const resolvedGalgameAriaLabel = galgameToggleButtonAriaLabel || galgameToggleButtonLabel;
  // ChoicePrompt（mini-game invite 等）和 galgame options 共用 composer 底部
  // 同一块 slot 视觉位。两者同时活跃会渲染出 6 个按钮挤一起；invite 是少见
  // 且需要用户即时响应的 transient event，galgame options 是常驻 mode →
  // invite 优先，galgame slot 临时让位。invite resolve 后下一轮 assistant
  // turn-end 会重新触发 galgame fetch，自然回归。
  const choicePromptHasOptions = !!(choicePrompt && choicePrompt.options.length > 0);
  // 模式开启 ≠ 选项实际占位。光开开关、还没收到 AI 新一轮时 slot 不撑开；
  // 选项到位（loading 占位也算）才让 slot 长出来，输入壳跟着自然变高。
  const galgameOptionsVisible =
    galgameModeEnabled && !choicePromptHasOptions
    && (galgameOptionsLoading || galgameOptions.length > 0);
  const emojiButtonAriaLabel = i18n('chat.emojiButtonAriaLabel', 'Emoji');
  const toolIconsAriaLabel = i18n('chat.toolIconsAriaLabel', 'Tool icons');
  const clearCursorToolAriaLabel = i18n('chat.clearCursorToolAriaLabel', '恢复鼠标');
  const overflowMenuAriaLabel = i18n('chat.composerOverflowMenu', '更多工具');
  const effectiveCursorVariant = resolveEffectiveCursorVariant(
    activeCursorToolId,
    avatarRangeCursorVariants,
    outsideRangeCursorVariants,
    isCursorOverAvatarRange,
  );
  const avatarRangeCursorVariant = activeCursorToolId
    ? (avatarRangeCursorVariants[activeCursorToolId] ?? 'primary')
    : 'primary';
  const activeToolItem = toolIconItems.find(item => item.id === activeCursorToolId) ?? null;
  const activeToolImagePaths = activeToolItem
    ? resolveToolImagePaths(activeToolItem, avatarRangeCursorVariant)
    : null;
  const isElectronMultiWindow = isElectronMultiWindowHost();
  const shouldUseLocalDesktopCursorOverlay = !!activeToolItem
    && supportsDesktopFinePointer()
    && !isElectronMultiWindow;
  const shouldRenderLocalDesktopCursorOverlay = shouldUseLocalDesktopCursorOverlay
    && isCursorInsideHostWindow;
  const shouldRenderAvatarRangeOverlay = isCursorOverAvatarRange && !isCursorOverCompactCursorZone;
  const avatarCursorOverlayActive = !!activeToolItem
    && activeCursorToolId !== 'hammer'
    && shouldRenderLocalDesktopCursorOverlay;
  const avatarCursorOverlayCompact = avatarCursorOverlayActive && !shouldRenderAvatarRangeOverlay;
  const hammerCursorOverlayActive = activeCursorToolId === 'hammer' && shouldRenderLocalDesktopCursorOverlay;
  const hammerCursorOverlayCompact = hammerCursorOverlayActive && !shouldRenderAvatarRangeOverlay;
  const hammerCursorOverlayMotionActive = hammerSwingPhase !== 'idle';
  const hammerCompactImagePaths = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, effectiveCursorVariant)
    : null;
  const hammerCursorOverlayUsesCompactImage = hammerCursorOverlayCompact && !hammerCursorOverlayMotionActive;
  const avatarCursorOverlayImagePath = activeToolItem && activeCursorToolId !== 'hammer'
    ? (
      avatarCursorOverlayCompact
        ? (activeToolImagePaths?.cursorImagePath ?? '')
        : (activeToolImagePaths?.iconImagePath ?? '')
    )
    : '';
  const hammerCursorOverlayCompactImagePath = hammerCursorOverlayUsesCompactImage
    ? (hammerCompactImagePaths?.cursorImagePath ?? '')
    : '';
  const hammerCursorOverlayPrimaryImagePath = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, 'primary').iconImagePath
    : '';
  const hammerCursorOverlaySecondaryImagePath = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, 'secondary').iconImagePath
    : '';
  const activeToolMenuVisual = activeToolItem
    ? resolveMenuIconVisual(activeToolItem, effectiveCursorVariant)
    : null;
  const activeToolLabel = activeToolItem ? getToolItemLabel(activeToolItem) : '';
  const selectedEmojiButtonAriaLabel = activeToolItem
    ? `${emojiButtonAriaLabel}: ${activeToolLabel}`
    : emojiButtonAriaLabel;
  const isCursorWithinAvatarToolRange = isCursorInsideHostWindow
    && isCursorOverAvatarRange
    && !isCursorOverCompactCursorZone;
  const avatarToolImageKind = activeToolItem
    ? (isCursorWithinAvatarToolRange ? 'icon' : 'cursor')
    : 'cursor';

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    avatarInteractionCallbackRef.current = onAvatarInteraction;
  }, [onAvatarInteraction]);

  useEffect(() => {
    if (!onAvatarToolStateChange) return;

    const outsideRangeVariant = activeCursorToolId
      ? (outsideRangeCursorVariants[activeCursorToolId] ?? 'primary')
      : 'primary';
    const textContext = sanitizeInteractionTextContext(draft);

    onAvatarToolStateChange({
      active: !!activeToolItem,
      toolId: activeToolItem?.id ?? null,
      variant: effectiveCursorVariant,
      avatarRangeVariant: avatarRangeCursorVariant,
      outsideRangeVariant,
      imageKind: avatarToolImageKind,
      withinAvatarRange: isCursorWithinAvatarToolRange,
      overCompactZone: isCursorOverCompactCursorZone,
      insideHostWindow: isCursorInsideHostWindow,
      tool: activeToolItem
        ? {
          id: activeToolItem.id,
          label: getToolItemLabel(activeToolItem),
          iconImagePath: activeToolItem.iconImagePath,
          iconImagePathAlt: activeToolItem.iconImagePathAlt,
          iconImagePathAlt2: activeToolItem.iconImagePathAlt2,
          cursorImagePath: activeToolItem.cursorImagePath,
          cursorImagePathAlt: activeToolItem.cursorImagePathAlt,
          cursorImagePathAlt2: activeToolItem.cursorImagePathAlt2,
          cursorHotspotX: activeToolItem.cursorHotspotX,
          cursorHotspotY: activeToolItem.cursorHotspotY,
          menuIconScale: activeToolItem.menuIconScale,
        }
        : null,
      textContext,
      timestamp: Date.now(),
    });
  }, [
    activeCursorToolId,
    activeToolItem,
    avatarRangeCursorVariant,
    draft,
    effectiveCursorVariant,
    avatarToolImageKind,
    isCursorInsideHostWindow,
    isCursorOverCompactCursorZone,
    isCursorWithinAvatarToolRange,
    onAvatarToolStateChange,
    outsideRangeCursorVariants,
  ]);

  function clearHammerSwingAnimation() {
    hammerSwingTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    hammerSwingTimeoutIdsRef.current = [];
    setHammerSwingPhase('idle');
    setIsInnerHammerEasterEggActive(false);
  }

  function clearOutsideHammerResetTimer(shouldResetToPrimary = true) {
    if (outsideHammerResetTimeoutRef.current !== null) {
      window.clearTimeout(outsideHammerResetTimeoutRef.current);
      outsideHammerResetTimeoutRef.current = null;
    }
    if (shouldResetToPrimary) {
      setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'primary' }));
    }
  }

  function spawnLollipopHearts(clientX: number, clientY: number) {
    const hearts: FloatingHeart[] = [
      { id: floatingHeartIdRef.current += 1, x: clientX - 12, y: clientY - 26, driftX: -26, driftY: -124, scale: 0.92, delayMs: 0 },
      { id: floatingHeartIdRef.current += 1, x: clientX + 10, y: clientY - 20, driftX: 24, driftY: -138, scale: 1.06, delayMs: 110 },
      { id: floatingHeartIdRef.current += 1, x: clientX - 4, y: clientY - 40, driftX: -18, driftY: -154, scale: 0.84, delayMs: 190 },
    ];
    setFloatingHearts(prev => [...prev, ...hearts]);
    hearts.forEach(heart => {
      const timeoutId = window.setTimeout(() => {
        setFloatingHearts(prev => prev.filter(item => item.id !== heart.id));
        floatingHeartTimeoutIdsRef.current = floatingHeartTimeoutIdsRef.current.filter(id => id !== timeoutId);
      }, 2100 + heart.delayMs);
      floatingHeartTimeoutIdsRef.current.push(timeoutId);
    });
  }

  function spawnFistDrops(clientX: number, clientY: number) {
    const drops: FloatingFistDrop[] = Array.from({ length: 3 }, () => {
      const launchAngleDeg = -140 + Math.random() * 100;
      const launchAngleRad = (launchAngleDeg * Math.PI) / 180;
      const distance = 76 + Math.random() * 42;
      return {
        id: floatingFistDropIdRef.current += 1,
        x: clientX - 8 + (Math.random() * 28 - 14),
        y: clientY - 24 + (Math.random() * 18 - 9),
        driftX: Math.round(Math.cos(launchAngleRad) * distance),
        driftY: Math.round(Math.sin(launchAngleRad) * distance),
        rotation: Math.round(-120 + Math.random() * 240),
        scale: Number((0.82 + Math.random() * 0.38).toFixed(2)),
        delayMs: Math.round(Math.random() * 140),
      };
    });
    setFloatingFistDrops(prev => [...prev, ...drops]);
    drops.forEach(drop => {
      const timeoutId = window.setTimeout(() => {
        setFloatingFistDrops(prev => prev.filter(item => item.id !== drop.id));
        floatingFistDropTimeoutIdsRef.current = floatingFistDropTimeoutIdsRef.current.filter(id => id !== timeoutId);
      }, 920 + drop.delayMs);
      floatingFistDropTimeoutIdsRef.current.push(timeoutId);
    });
  }

  function recordInteractionBurst(key: string, windowMs: number) {
    const now = Date.now();
    const recentTimestamps = (interactionBurstHistoryRef.current[key] ?? [])
      .filter(timestamp => now - timestamp <= windowMs);
    recentTimestamps.push(now);
    interactionBurstHistoryRef.current[key] = recentTimestamps;
    return recentTimestamps.length;
  }

  function updateHammerCursorOverlayPosition(clientX: number, clientY: number) {
    latestPointerPositionRef.current = { x: clientX, y: clientY };
    const overlayNode = hammerCursorOverlayRef.current;
    if (!overlayNode || !hammerToolItem) return;
    const hotspotX = hammerToolItem.cursorHotspotX ?? 18;
    const hotspotY = hammerToolItem.cursorHotspotY ?? 18;
    overlayNode.style.transform = `translate3d(${clientX - hotspotX}px, ${clientY - hotspotY}px, 0)`;
  }

  function updateAvatarCursorOverlayPosition(clientX: number, clientY: number) {
    latestPointerPositionRef.current = { x: clientX, y: clientY };
    const overlayNode = avatarCursorOverlayRef.current;
    if (!overlayNode || !activeToolItem) return;
    const hotspotX = activeToolItem.cursorHotspotX ?? 18;
    const hotspotY = activeToolItem.cursorHotspotY ?? 18;
    overlayNode.style.transform = `translate3d(${clientX - hotspotX}px, ${clientY - hotspotY}px, 0)`;
  }

  function emitAvatarInteraction<T extends AvatarInteractionToolId>(
    toolId: T,
    actionId: AvatarInteractionPayloadByTool[T]['actionId'],
    target: AvatarInteractionPayload['target'],
    clientX: number,
    clientY: number,
    options?: {
      intensity?: InteractionIntensity;
      rewardDrop?: boolean;
      easterEgg?: boolean;
      touchZone?: AvatarTouchZone;
    },
  ) {
    const callback = avatarInteractionCallbackRef.current;
    if (!callback) return;

    const payload = {
      interactionId: createAvatarInteractionId(),
      toolId,
      actionId,
      target,
      pointer: {
        clientX,
        clientY,
      },
      timestamp: Date.now(),
    } as AvatarInteractionPayloadByTool[T];

    const textContext = sanitizeInteractionTextContext(draftRef.current);
    if (textContext) {
      payload.textContext = textContext;
    }
    if (options?.intensity) {
      payload.intensity = options.intensity;
    }
    if (options?.touchZone && toolId !== 'lollipop') {
      (payload as { touchZone?: AvatarTouchZone }).touchZone = options.touchZone;
    }
    if (options?.rewardDrop && toolId === 'fist') {
      (payload as Extract<AvatarInteractionPayload, { toolId: 'fist' }>).rewardDrop = true;
    }
    if (options?.easterEgg && toolId === 'hammer') {
      (payload as Extract<AvatarInteractionPayload, { toolId: 'hammer' }>).easterEgg = true;
    }

    callback(payload);
  }

  useEffect(() => {
    if (!toolMenuOpen) return;

    const closeMenuOnOutsideClick = (event: MouseEvent) => {
      const menuNode = toolMenuRef.current;
      if (!menuNode) return;
      if (menuNode.contains(event.target as Node)) return;
      setToolMenuOpen(false);
    };

    const closeMenuOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setToolMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
    };
  }, [toolMenuOpen]);

  // 镜像 composerLayout 到 ref，给 ResizeObserver 闭包读
  useEffect(() => {
    composerLayoutRef.current = composerLayout;
  }, [composerLayout]);

  // 监听 composer-bottom-bar 宽度，决定是否进入 compact 折叠模式。
  // 阈值：低于此宽度时把右侧 4 个工具按钮折叠成 ··· 菜单。
  useEffect(() => {
    const target = composerBottomBarRef.current;
    if (!target || typeof ResizeObserver === 'undefined') return;
    const COMPACT_THRESHOLD = 300;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const wantCompact = entry.contentRect.width < COMPACT_THRESHOLD;
        // 在 expanded → collapsing 这一刻抓一下右 4 按钮组的当前像素宽度，
        // 同一批 setState 会和 layout 切换一起 commit，render 出来时
        // .is-leaving 类和 --collapse-from-width 变量同时生效，
        // CSS keyframe 就能从这个固定宽度插值到 0。
        // 用 offsetWidth 而非 getBoundingClientRect().width：前者基于布局盒，
        // 不受入场 scaleX 动画影响；如果 expand 动画还没跑完就又被压窄，
        // bounding rect 会拿到被 scaleX 缩小后的视觉宽度导致 layout 抖动。
        if (wantCompact && composerLayoutRef.current === 'expanded' && composerToolsRightRef.current) {
          const node = composerToolsRightRef.current;
          const w = Math.max(node.offsetWidth, node.scrollWidth);
          if (w > 0) setCollapseFromWidth(w);
        }
        setComposerLayout(prev => {
          if (wantCompact) {
            if (prev === 'expanded') return 'collapsing';
            // 展开过程中又被压窄：退场动画来不及跑完就反转，直接回到 compact 稳态。
            if (prev === 'expanding') return 'compact';
            return prev;
          } else {
            if (prev === 'compact') return 'expanding';
            // 收起过程中又被拉宽：跳回 expanded 稳态。
            if (prev === 'collapsing') return 'expanded';
            return prev;
          }
        });
      }
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  // 收起/展开动画跑完后切到稳态。时长需与 styles.css 中的 keyframes 对齐。
  // prefers-reduced-motion 下 styles.css 把动画设成 none，这时还等 270/220ms
  // 会让工具区滞留在过渡态（控件视觉上提前到位但 layout state 没切），
  // 直接 0ms 立刻切稳态。
  useEffect(() => {
    const prefersReducedMotion =
      typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (composerLayout === 'collapsing') {
      const timerId = window.setTimeout(() => {
        setComposerLayout(prev => (prev === 'collapsing' ? 'compact' : prev));
      }, prefersReducedMotion ? 0 : 270);
      return () => window.clearTimeout(timerId);
    }
    if (composerLayout === 'expanding') {
      const timerId = window.setTimeout(() => {
        setComposerLayout(prev => (prev === 'expanding' ? 'expanded' : prev));
      }, prefersReducedMotion ? 0 : 220);
      return () => window.clearTimeout(timerId);
    }
    return undefined;
  }, [composerLayout]);

  // 离开 compact 稳态时关闭 ··· 弹窗（包括反向中断和 expanding 阶段）
  useEffect(() => {
    if (composerLayout !== 'compact') setOverflowMenuOpen(false);
  }, [composerLayout]);

  // ··· 菜单的外部点击 / Esc 关闭
  useEffect(() => {
    if (!overflowMenuOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      const node = overflowMenuRef.current;
      if (!node) return;
      if (node.contains(event.target as Node)) return;
      setOverflowMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOverflowMenuOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [overflowMenuOpen]);

  useEffect(() => {
    if (!activeCursorToolId) return;

    const resetFistCursorVariant = () => {
      setAvatarRangeCursorVariants(prev => ({ ...prev, fist: 'primary' }));
      setOutsideRangeCursorVariants(prev => ({ ...prev, fist: 'primary' }));
    };

    const toggleCursorVariantOnPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const isOverCompactCursorZoneAtPointer = isPointWithinCompactCursorZone(event.clientX, event.clientY);
      setIsCursorOverCompactCursorZone(previousValue => (
        previousValue === isOverCompactCursorZoneAtPointer ? previousValue : isOverCompactCursorZoneAtPointer
      ));
      if (isOverCompactCursorZoneAtPointer) {
        return;
      }
      const avatarRangeHit = getAvatarRangeHit(event.clientX, event.clientY, avatarToolCacheState);
      const isOverAvatarAtPointer = avatarRangeHit !== null;
      setCursorOverAvatarRange(isOverAvatarAtPointer, { allowHold: true });

      if (activeCursorToolId === 'lollipop') {
        if (isOverAvatarAtPointer) {
          const currentVariant = avatarRangeCursorVariants.lollipop ?? 'primary';
          const actionId = currentVariant === 'primary'
            ? 'offer'
            : currentVariant === 'secondary'
              ? 'tease'
              : 'tap_soft';
          const lollipopTapCount = currentVariant === 'tertiary'
            ? recordInteractionBurst('lollipop:tap_soft', 1800)
            : 0;
          const intensity: InteractionIntensity = currentVariant === 'tertiary'
            ? (lollipopTapCount >= 4 ? 'burst' : 'rapid')
            : 'normal';
          emitAvatarInteraction('lollipop', actionId, 'avatar', event.clientX, event.clientY, {
            intensity,
          });

          if (currentVariant === 'tertiary') {
            spawnLollipopHearts(event.clientX, event.clientY);
            return;
          }
          const nextVariant: CursorVariant = currentVariant === 'primary' ? 'secondary' : 'tertiary';
          setAvatarRangeCursorVariants(prev => (
            prev.lollipop === nextVariant ? prev : { ...prev, lollipop: nextVariant }
          ));
          return;
        }
        return;
      }
      if (activeCursorToolId === 'fist') {
        const shouldSpawnRewardDrop = isOverAvatarAtPointer && Math.random() < 0.25;
        const fistTapCount = isOverAvatarAtPointer
          ? recordInteractionBurst('fist:poke', 1400)
          : 0;
        setAvatarRangeCursorVariants(prev => ({ ...prev, fist: 'secondary' }));
        setOutsideRangeCursorVariants(prev => ({ ...prev, fist: 'secondary' }));
        if (isOverAvatarAtPointer) {
          emitAvatarInteraction(
            'fist',
            'poke',
            'avatar',
            event.clientX,
            event.clientY,
            {
              intensity: fistTapCount >= 4 ? 'rapid' : 'normal',
              rewardDrop: shouldSpawnRewardDrop,
              touchZone: avatarRangeHit?.touchZone,
            },
          );
        }
        if (shouldSpawnRewardDrop) {
          spawnFistDrops(event.clientX, event.clientY);
        }
        return;
      }
      if (activeCursorToolId === 'hammer') {
        if (!isOverAvatarAtPointer) {
          clearOutsideHammerResetTimer(false);
          setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'secondary' }));
          outsideHammerResetTimeoutRef.current = window.setTimeout(() => {
            setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'primary' }));
            outsideHammerResetTimeoutRef.current = null;
          }, 220);
          return;
        }
        if (hammerSwingPhase !== 'idle') {
          return;
        }
        const shouldTriggerInnerHammerEasterEgg = Math.random() < 0.05;
        const hammerBonkCount = recordInteractionBurst('hammer:bonk', 3200);
        const hammerIntensity: InteractionIntensity = shouldTriggerInnerHammerEasterEgg
          ? 'easter_egg'
          : hammerBonkCount >= 3
            ? 'burst'
            : hammerBonkCount >= 2
              ? 'rapid'
              : 'normal';
        emitAvatarInteraction('hammer', 'bonk', 'avatar', event.clientX, event.clientY, {
          intensity: hammerIntensity,
          easterEgg: shouldTriggerInnerHammerEasterEgg,
          touchZone: avatarRangeHit?.touchZone,
        });
        setIsInnerHammerEasterEggActive(shouldTriggerInnerHammerEasterEgg);
        setHammerSwingPhase('windup');
        hammerSwingTimeoutIdsRef.current = [
          window.setTimeout(() => {
            setHammerSwingPhase('swing');
          }, 240),
          window.setTimeout(() => {
            setHammerSwingPhase('impact');
          }, 420),
          window.setTimeout(() => {
            setHammerSwingPhase('recover');
          }, 520),
          window.setTimeout(() => {
            setHammerSwingPhase('idle');
            if (shouldTriggerInnerHammerEasterEgg) {
              setIsInnerHammerEasterEggActive(false);
            }
            hammerSwingTimeoutIdsRef.current = [];
          }, 620),
        ];
        return;
      }
      if (isOverAvatarAtPointer) {
        setAvatarRangeCursorVariants(prev => ({
          ...prev,
          [activeCursorToolId]: prev[activeCursorToolId] === 'primary' ? 'secondary' : 'primary',
        }));
      } else {
        setOutsideRangeCursorVariants(prev => ({
          ...prev,
          [activeCursorToolId]: prev[activeCursorToolId] === 'primary' ? 'secondary' : 'primary',
        }));
      }
    };

    const handlePointerUp = () => {
      if (activeCursorToolId !== 'fist') return;
      resetFistCursorVariant();
    };

    window.addEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
    window.addEventListener('pointerup', handlePointerUp, true);
    window.addEventListener('pointercancel', handlePointerUp, true);
    window.addEventListener('blur', handlePointerUp);
    return () => {
      window.removeEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
      window.removeEventListener('pointerup', handlePointerUp, true);
      window.removeEventListener('pointercancel', handlePointerUp, true);
      window.removeEventListener('blur', handlePointerUp);
    };
  }, [activeCursorToolId, avatarRangeCursorVariants, hammerSwingPhase, setCursorOverAvatarRange]);

  useEffect(() => {
    if (activeCursorToolId === 'hammer') return;
    clearHammerSwingAnimation();
    clearOutsideHammerResetTimer();
  }, [activeCursorToolId, avatarToolCacheState]);

  useEffect(() => () => {
    clearHammerSwingAnimation();
    clearOutsideHammerResetTimer();
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }
    floatingHeartTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    floatingHeartTimeoutIdsRef.current = [];
    floatingFistDropTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    floatingFistDropTimeoutIdsRef.current = [];
  }, []);

  useEffect(() => {
    if (!activeCursorToolId) {
      setCursorOverAvatarRange(false, { allowHold: false });
      setIsCursorOverCompactCursorZone(false);
      return;
    }

    let frameId = 0;

    const updateCursorRangeState = (clientX: number, clientY: number) => {
      const nextValue = isPointerWithinAvatarRange(clientX, clientY, avatarToolCacheState);
      setCursorOverAvatarRange(nextValue, { allowHold: true });
    };

    const handlePointerMove = (event: PointerEvent) => {
      setIsCursorInsideHostWindow(true);
      latestPointerPositionRef.current = { x: event.clientX, y: event.clientY };
      latestPointerTargetRef.current = event.target;
      if (frameId) return;

      frameId = window.requestAnimationFrame(() => {
        frameId = 0;
        const { x, y } = latestPointerPositionRef.current;
        const isOverCompactCursorZone = isPointerOverCompactCursorZone(latestPointerTargetRef.current);
        if (activeCursorToolId === 'hammer') {
          updateHammerCursorOverlayPosition(x, y);
        } else if (activeCursorToolId) {
          updateAvatarCursorOverlayPosition(x, y);
        }
        updateCursorRangeState(x, y);
        setIsCursorOverCompactCursorZone(previousValue => (
          previousValue === isOverCompactCursorZone ? previousValue : isOverCompactCursorZone
        ));
      });
    };

    const hideLocalCursorOverlay = () => {
      clearAvatarBoundsCache(avatarToolCacheState);
      latestPointerTargetRef.current = null;
      setCursorOverAvatarRange(false, { allowHold: false });
      setIsCursorOverCompactCursorZone(false);
      setIsCursorInsideHostWindow(false);
    };

    const isPointerOutsideViewport = (event: MouseEvent | PointerEvent) => (
      event.clientX <= 0
      || event.clientY <= 0
      || event.clientX >= window.innerWidth
      || event.clientY >= window.innerHeight
    );

    const handleMouseOut = (event: MouseEvent) => {
      if (event.relatedTarget !== null) return;
      if (!isPointerOutsideViewport(event)) return;
      hideLocalCursorOverlay();
    };

    const handlePointerOut = (event: PointerEvent) => {
      if (event.relatedTarget !== null) return;
      if (!isPointerOutsideViewport(event)) return;
      hideLocalCursorOverlay();
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        hideLocalCursorOverlay();
      }
    };

    window.addEventListener('pointermove', handlePointerMove, { passive: true, capture: true });
    document.addEventListener('mouseleave', hideLocalCursorOverlay);
    window.addEventListener('pointerout', handlePointerOut, true);
    window.addEventListener('mouseout', handleMouseOut, true);
    window.addEventListener('blur', hideLocalCursorOverlay);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      clearAvatarBoundsCache(avatarToolCacheState);
      window.removeEventListener('pointermove', handlePointerMove, true);
      document.removeEventListener('mouseleave', hideLocalCursorOverlay);
      window.removeEventListener('pointerout', handlePointerOut, true);
      window.removeEventListener('mouseout', handleMouseOut, true);
      window.removeEventListener('blur', hideLocalCursorOverlay);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [activeCursorToolId, avatarToolCacheState, setCursorOverAvatarRange]);

  useEffect(() => {
    const root = document.documentElement;
    let cancelled = false;

    if (!activeCursorToolId || composerHidden) {
      clearGlobalToolCursorState();
      return;
    }

    if ((shouldUseLocalDesktopCursorOverlay || isElectronMultiWindow) && !isCursorInsideHostWindow) {
      clearGlobalToolCursorState();
      return;
    }

    const selected = toolIconItems.find(item => item.id === activeCursorToolId);
    if (!selected) {
      clearGlobalToolCursorState();
      return;
    }

    clearForcedNativeCursorFallback();
    root.classList.add('neko-tool-cursor-active');

    const applyResolvedCursor = async () => {
      let cursorValue: string;
      if (shouldUseLocalDesktopCursorOverlay || isElectronMultiWindow) {
        cursorValue = 'none';
      } else if (isCursorOverAvatarRange && !isCursorOverCompactCursorZone) {
        cursorValue = resolveCursorValue(selected, effectiveCursorVariant);
      } else {
        cursorValue = await resolveCompactCursorValue(selected, effectiveCursorVariant, avatarToolCacheState);
      }
      if (cancelled) return;
      root.style.setProperty('--neko-chat-tool-cursor', cursorValue);
    };

    void applyResolvedCursor();

    return () => {
      cancelled = true;
    };
  }, [activeCursorToolId, composerHidden, avatarToolCacheState, effectiveCursorVariant, isCursorInsideHostWindow, isCursorOverAvatarRange, isCursorOverCompactCursorZone, isElectronMultiWindow, shouldUseLocalDesktopCursorOverlay]);

  useEffect(() => {
    if (!activeToolItem) return;
    void resolveCompactCursorValue(activeToolItem, effectiveCursorVariant, avatarToolCacheState);
  }, [activeToolItem, avatarToolCacheState, effectiveCursorVariant]);

  useEffect(() => {
    if (!avatarCursorOverlayActive) return;
    updateAvatarCursorOverlayPosition(
      latestPointerPositionRef.current.x,
      latestPointerPositionRef.current.y,
    );
  }, [avatarCursorOverlayActive, avatarCursorOverlayImagePath, activeToolItem]);

  useEffect(() => {
    if (!hammerCursorOverlayActive) return;
    updateHammerCursorOverlayPosition(
      latestPointerPositionRef.current.x,
      latestPointerPositionRef.current.y,
    );
  }, [hammerCursorOverlayActive, hammerSwingPhase]);

  useEffect(() => {
    if (composerHidden && activeCursorToolId) {
      clearActiveCursorToolSelection();
    }
  }, [activeCursorToolId, composerHidden]);

  useEffect(() => {
    function handleDeactivate() {
      clearActiveCursorToolSelection();
    }
    window.addEventListener('neko:deactivate-tool-cursor', handleDeactivate);
    return () => window.removeEventListener('neko:deactivate-tool-cursor', handleDeactivate);
  }, []);

  useEffect(() => () => {
    clearGlobalToolCursorState();
  }, []);

  function submitDraft() {
    if (submittingRef.current) return;
    const text = draft.trim();
    if (!text && composerAttachments.length === 0) return;
    submittingRef.current = true;
    try {
      onComposerSubmit?.({ text });
      setDraft('');
    } finally {
      requestAnimationFrame(() => { submittingRef.current = false; });
    }
  }

  // 右侧 3 个工具按钮：在 compact 与 normal 两种布局中复用同一份 JSX，
  // 既避免重复，也保证 ref/事件绑定在两种模式下行为一致。
  const translateButtonNode = (
    <button
      className={`composer-tool-btn composer-translate-btn${translateEnabled ? ' is-active' : ''}`}
      type="button"
      aria-label={resolvedTranslateAriaLabel}
      aria-pressed={translateEnabled}
      title={translateButtonLabel}
      onClick={() => onTranslateToggle?.()}
    >
      <img src="/static/icons/translate_icon.png" alt="" aria-hidden="true" />
    </button>
  );

  const jukeboxButtonNode = (
    <button
      className="composer-tool-btn"
      type="button"
      aria-label={jukeboxButtonAriaLabel}
      title={jukeboxButtonLabel}
      onClick={() => onJukeboxClick?.()}
    >
      <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
    </button>
  );

  const galgameToggleButtonNode = (
    <button
      className={`composer-tool-btn composer-galgame-btn${galgameModeEnabled ? ' is-active' : ''}`}
      type="button"
      aria-label={resolvedGalgameAriaLabel}
      aria-pressed={galgameModeEnabled}
      title={galgameToggleButtonLabel}
      onClick={() => onGalgameModeToggle?.()}
    >
      <span className="composer-galgame-btn-glyph" aria-hidden="true">G</span>
    </button>
  );

  const emojiToolMenuNode = (
    <div className="composer-tool-menu" ref={toolMenuRef}>
      <button
        className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
        type="button"
        aria-label={selectedEmojiButtonAriaLabel}
        title={selectedEmojiButtonAriaLabel}
        aria-controls={toolMenuOpen ? 'composer-tool-popover' : undefined}
        aria-expanded={toolMenuOpen}
        onClick={() => {
          if (activeToolItem) {
            clearActiveCursorToolSelection();
            return;
          }
          setToolMenuOpen(open => !open);
        }}
      >
        <img
          src={activeToolMenuVisual?.imagePath || '/static/icons/emoji_icon.png'}
          style={activeToolItem ? {
            transform: `translate(${activeToolMenuVisual?.offsetX ?? 0}px, ${activeToolMenuVisual?.offsetY ?? 0}px) scale(${activeToolItem.menuIconScale ?? 1})`,
          } : undefined}
          alt=""
          aria-hidden="true"
        />
      </button>
      {activeToolItem ? (
        <button
          className="composer-tool-clear-btn"
          type="button"
          aria-label={clearCursorToolAriaLabel}
          title={clearCursorToolAriaLabel}
          onClick={(event) => {
            event.stopPropagation();
            setIsCursorInsideHostWindow(true);
            setActiveCursorToolId(null);
            setToolMenuOpen(false);
          }}
        >
          <span aria-hidden="true">×</span>
        </button>
      ) : null}
      {toolMenuOpen ? (
        <div
          id="composer-tool-popover"
          className="composer-icon-popover"
          role="group"
          aria-label={toolIconsAriaLabel}
        >
          {toolIconItems.map(item => {
            const itemLabel = getToolItemLabel(item);
            const menuVariant = activeCursorToolId === item.id
              ? effectiveCursorVariant
              : 'primary';
            const menuVisual = resolveMenuIconVisual(item, menuVariant);
            return (
            <button
              key={item.id}
              className={`composer-icon-button${activeCursorToolId === item.id ? ' is-active' : ''}`}
              type="button"
              aria-pressed={activeCursorToolId === item.id}
              aria-label={itemLabel}
              title={itemLabel}
              onClick={(event) => {
                latestPointerPositionRef.current = {
                  x: event.clientX,
                  y: event.clientY,
                };
                latestPointerTargetRef.current = event.currentTarget;
                setIsCursorInsideHostWindow(true);
                setIsCursorOverCompactCursorZone(true);
                setCursorOverAvatarRange(
                  isPointerWithinAvatarRange(event.clientX, event.clientY, avatarToolCacheState),
                  { allowHold: true },
                );
                if (activeCursorToolId === item.id) {
                  setActiveCursorToolId(null);
                  setToolMenuOpen(false);
                  return;
                }
                setAvatarRangeCursorVariants(prev => ({ ...prev, [item.id]: 'primary' }));
                setOutsideRangeCursorVariants(prev => ({ ...prev, [item.id]: 'primary' }));
                setActiveCursorToolId(item.id);
                setToolMenuOpen(false);
              }}
            >
              <img
                className="composer-icon-button-image"
                src={menuVisual.imagePath}
                style={{
                  transform: `translate(${menuVisual.offsetX}px, ${menuVisual.offsetY}px) scale(${item.menuIconScale ?? 1})`,
                }}
                alt=""
                aria-hidden="true"
              />
            </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );

  return (
    <main className="app-shell">
      {floatingFistDrops.map(drop => (
        <span
          key={drop.id}
          className="fist-floating-drop"
          aria-hidden="true"
          style={{
            left: `${drop.x}px`,
            top: `${drop.y}px`,
            '--drop-drift-x': `${drop.driftX}px`,
            '--drop-drift-y': `${drop.driftY}px`,
            '--drop-rotation': `${drop.rotation}deg`,
            '--drop-scale': drop.scale,
            '--drop-delay': `${drop.delayMs}ms`,
          } as CSSProperties}
        >
            <img
            className="fist-floating-drop-image"
            src="/static/icons/cat_moneny.png"
            alt=""
          />
        </span>
      ))}
      {floatingHearts.map(heart => (
        <span
          key={heart.id}
          className="lollipop-floating-heart"
          aria-hidden="true"
          style={{
            left: `${heart.x}px`,
            top: `${heart.y}px`,
            '--heart-drift-x': `${heart.driftX}px`,
            '--heart-drift-y': `${heart.driftY}px`,
            '--heart-sway-x': `${Math.max(8, Math.round(Math.abs(heart.driftX) * 0.32)) * (heart.driftX < 0 ? -1 : 1)}px`,
            '--heart-scale': heart.scale,
            '--heart-delay': `${heart.delayMs}ms`,
          } as CSSProperties}
        >
          <span className="lollipop-floating-heart-glyph">♥</span>
        </span>
      ))}
      {activeToolItem && activeCursorToolId !== 'hammer' && avatarCursorOverlayActive ? (
        <div
          ref={avatarCursorOverlayRef}
          className={`avatar-cursor-overlay avatar-cursor-overlay-${activeToolItem.id}${avatarCursorOverlayActive ? ' is-visible' : ''}${avatarCursorOverlayCompact ? ' is-compact' : ''}`}
          aria-hidden="true"
        >
          <div
            className="avatar-cursor-overlay-stage"
            style={{
              transformOrigin: `${activeToolItem.cursorHotspotX ?? 18}px ${activeToolItem.cursorHotspotY ?? 18}px`,
            }}
          >
            <img
              className={`avatar-cursor-overlay-image avatar-cursor-overlay-image-${activeToolItem.id}`}
              src={avatarCursorOverlayImagePath}
              alt=""
            />
          </div>
        </div>
      ) : null}
      {hammerToolItem && hammerCursorOverlayActive ? (
        <div
          ref={hammerCursorOverlayRef}
          className={`hammer-cursor-overlay${hammerCursorOverlayActive ? ' is-visible' : ''}${hammerCursorOverlayCompact ? ' is-compact' : ''}${isInnerHammerEasterEggActive ? ' is-easter-egg' : ''}`}
          aria-hidden="true"
        >
          <div
            className="hammer-cursor-overlay-stage"
            style={{
              transformOrigin: `${hammerToolItem.cursorHotspotX ?? 18}px ${hammerToolItem.cursorHotspotY ?? 18}px`,
            }}
          >
            {hammerCursorOverlayUsesCompactImage ? (
              <img
                className="hammer-cursor-overlay-compact-image"
                src={hammerCursorOverlayCompactImagePath}
                alt=""
              />
            ) : (
              <div
                className={`hammer-cursor-overlay-visual${hammerCursorOverlayMotionActive ? ' is-active' : ' is-idle'}${hammerSwingPhase === 'impact' ? ' is-impact' : ''}`}
                style={{
                  transformOrigin: `${hammerOverlayTransformOrigin.x}px ${hammerOverlayTransformOrigin.y}px`,
                }}
              >
                <img
                  className="hammer-cursor-overlay-image hammer-cursor-overlay-image-primary"
                  src={hammerCursorOverlayPrimaryImagePath}
                  alt=""
                />
                <img
                  className="hammer-cursor-overlay-image hammer-cursor-overlay-image-secondary"
                  src={hammerCursorOverlaySecondaryImagePath}
                  alt=""
                />
              </div>
            )}
          </div>
        </div>
      ) : null}
      <section className="chat-window" aria-label={chatWindowAriaLabel}>
        <header className="window-topbar">
          <div className="window-title-group">
            <div className="window-avatar window-avatar-image-shell">
              <img className="window-avatar-image" src={iconSrc} alt={title} />
            </div>
            <h1 className="window-title" id="react-chat-window-title">{title}</h1>
          </div>
          {/* Avatar button moved to #react-chat-window-header-actions in host template */}
        </header>

        <section className="chat-body">
          <MessageList
            messages={messages}
            ariaLabel={messageListAriaLabel}
            failedStatusLabel={failedStatusLabel}
            onAction={onMessageAction}
          />
        </section>

        <footer
          className={`composer-panel${galgameModeEnabled ? ' is-galgame-mode' : ''}`}
          style={composerHidden ? { display: 'none' } : undefined}
        >
          <div id="music-player-mount" />
          {composerAttachments.length > 0 ? (
            <div className="composer-attachments" aria-label={composerAttachmentsAriaLabel}>
              {composerAttachments.map((attachment) => (
                <figure key={attachment.id} className="composer-attachment-card">
                  <img
                    className="composer-attachment-image"
                    src={attachment.url}
                    alt={attachment.alt || ''}
                    loading="lazy"
                  />
                  <button
                    className="composer-attachment-remove"
                    type="button"
                    aria-label={`${removeAttachmentButtonAriaLabel}: ${attachment.alt || attachment.id}`}
                    onClick={() => onComposerRemoveAttachment?.(attachment.id)}
                  >
                    ×
                  </button>
                </figure>
              ))}
            </div>
          ) : null}
          <form className="composer" onSubmit={(event) => {
            event.preventDefault();
            submitDraft();
          }}>
            <div className="composer-input-shell">
              <textarea
                className="composer-input"
                placeholder={inputPlaceholder}
                aria-label={inputPlaceholder}
                rows={1}
                value={draft}
                onChange={(event) => { setDraft(event.target.value); }}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submitDraft();
                  }
                }}
              />
              {galgameModeEnabled && !choicePromptHasOptions ? (
                // Slot 始终挂在树上，开/关靠 is-open（max-height + opacity 过渡）。
                // 这样选项进/出时输入壳跟着自然长/缩，bottom-bar 锚在 panel 底
                // 不动，textarea 顶端跟着 shell-top 上抬，视觉上是从下往上展开。
                // mini-game invite 等 ChoicePrompt 占位时整块 slot 不挂树，避免
                // 与下面 composer-choice-slot 视觉撞车（参见 choicePromptHasOptions）。
                <div
                  className={`composer-galgame-slot${galgameOptionsVisible ? ' is-open' : ''}`}
                  aria-hidden={!galgameOptionsVisible}
                >
                  <div
                    className={`composer-galgame-options${galgameOptionsLoading ? ' is-loading' : ''}`}
                    role="group"
                    aria-label={galgameToggleButtonLabel}
                  >
                    {galgameOptions.length > 0
                      ? galgameOptions.slice(0, 3).map((option, index) => (
                          <button
                            key={`${index}-${option.label}`}
                            type="button"
                            className="composer-galgame-option"
                            title={option.text}
                            disabled={galgameOptionsLoading}
                            tabIndex={galgameOptionsVisible ? 0 : -1}
                            onClick={() => {
                              if (submittingRef.current) return;
                              submittingRef.current = true;
                              try {
                                onGalgameOptionSelect?.(option);
                              } finally {
                                requestAnimationFrame(() => { submittingRef.current = false; });
                              }
                            }}
                          >
                            <span className="composer-galgame-option-label" aria-hidden="true">{option.label}.</span>
                            <span className="composer-galgame-option-text">{option.text}</span>
                          </button>
                        ))
                      : galgameOptionsLoading
                        ? ['A', 'B', 'C'].map((label) => (
                            <button
                              key={label}
                              type="button"
                              className="composer-galgame-option is-placeholder"
                              disabled
                              tabIndex={-1}
                            >
                              <span className="composer-galgame-option-label" aria-hidden="true">{label}.</span>
                              <span className="composer-galgame-option-text">{galgameLoadingLabel}</span>
                            </button>
                          ))
                        : null}
                  </div>
                </div>
              ) : null}
              {/*
                Generic ChoicePrompt slot —— mini-game invite 等通用三选项
                抽象。复用 .composer-galgame-* CSS 让 visual / animation 与
                galgame mode 统一；本框架与 galgame slot 互不重叠（galgame
                走自己的 galgameOptions 路径，BC），未来可统一迁移。
              */}
              {choicePrompt && choicePrompt.options.length > 0 ? (
                <div
                  className={`composer-galgame-slot composer-choice-slot is-open is-${choicePrompt.source}`}
                  aria-hidden="false"
                  data-choice-source={choicePrompt.source}
                >
                  <div
                    className="composer-galgame-options composer-choice-options"
                    role="group"
                    aria-label={choicePrompt.source === 'mini_game_invite'
                      ? i18n('chat.miniGameInviteOptionsAriaLabel', 'Mini-game invite options')
                      : galgameToggleButtonLabel}
                  >
                    {choicePrompt.options.slice(0, 3).map((option, index) => (
                      <button
                        key={`${index}-${option.choice}`}
                        type="button"
                        className="composer-galgame-option composer-choice-option"
                        title={option.label}
                        onClick={() => {
                          if (submittingRef.current) return;
                          submittingRef.current = true;
                          try {
                            onChoiceSelect?.(option, choicePrompt.source);
                          } finally {
                            requestAnimationFrame(() => { submittingRef.current = false; });
                          }
                        }}
                      >
                        <span className="composer-galgame-option-text composer-choice-option-text">
                          {option.label}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="composer-bottom-bar" ref={composerBottomBarRef}>
                <div className="composer-bottom-tools" aria-label={composerToolsAriaLabel}>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedImportImageAriaLabel}
                    title={importImageButtonLabel}
                    onClick={() => onComposerImportImage?.()}
                  >
                    <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
                  </button>
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedScreenshotAriaLabel}
                    title={screenshotButtonLabel}
                    onClick={() => onComposerScreenshot?.()}
                  >
                    <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
                  </button>
                  {/* 这条分隔符在 expanded / compact 两态下都常驻同一位置，
                      避免切换时分隔符闪烁，让动画过渡更顺滑 */}
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  {showRightTools ? (
                    <div
                      ref={composerToolsRightRef}
                      className={`composer-tools-right${composerLayout === 'collapsing' ? ' is-leaving' : ''}`}
                      key="composer-tools-expanded"
                      style={
                        composerLayout === 'collapsing' && collapseFromWidth != null
                          ? ({ '--collapse-from-width': `${collapseFromWidth}px` } as CSSProperties)
                          : undefined
                      }
                    >
                      {galgameToggleButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {translateButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {jukeboxButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {emojiToolMenuNode}
                    </div>
                  ) : (
                    <div
                      className={`composer-overflow-menu${composerLayout === 'expanding' ? ' is-leaving' : ''}`}
                      key="composer-tools-collapsed"
                      ref={overflowMenuRef}
                    >
                      <button
                        className={`composer-tool-btn composer-overflow-btn${overflowMenuOpen ? ' is-active' : ''}`}
                        type="button"
                        aria-label={overflowMenuAriaLabel}
                        title={overflowMenuAriaLabel}
                        aria-haspopup="true"
                        aria-expanded={overflowMenuOpen}
                        onClick={() => setOverflowMenuOpen(open => !open)}
                      >
                        <svg
                          width="20"
                          height="20"
                          viewBox="0 0 24 24"
                          fill="currentColor"
                          aria-hidden="true"
                          focusable="false"
                        >
                          <circle cx="6" cy="12" r="2" />
                          <circle cx="12" cy="12" r="2" />
                          <circle cx="18" cy="12" r="2" />
                        </svg>
                      </button>
                      {overflowMenuOpen ? (
                        <div
                          className="composer-overflow-popover"
                          role="group"
                          aria-label={overflowMenuAriaLabel}
                        >
                          {galgameToggleButtonNode}
                          {translateButtonNode}
                          {jukeboxButtonNode}
                          {emojiToolMenuNode}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
                <button className="send-button-circle" type="submit" aria-label={sendButtonLabel} disabled={!canSubmit}>
                  <img src="/static/icons/send_new_icon.png" alt="" aria-hidden="true" />
                </button>
              </div>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
