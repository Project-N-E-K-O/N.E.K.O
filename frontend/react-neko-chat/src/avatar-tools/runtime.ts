import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  type AvatarToolId,
  type AvatarToolItem,
} from '../avatarTools';
import type { AvatarInteractionPayload, AvatarToolStatePayload } from '../message-schema';
import {
  createAvatarToolEffectExecution,
  createAvatarToolDisposer,
  getAvatarToolTransientEffectLifetimeMs,
  playAvatarToolSound,
  prepareAvatarToolVisuals,
  prewarmAvatarToolSounds,
  type ActiveHammerSwingEffectExecution,
  type AvatarToolDisposer,
  type AvatarToolHeadAnchor,
  type AvatarToolRoundChoiceAvatarGestureState,
  type AvatarToolTransientVisualEffect,
} from './presentation';
import {
  collectAvatarToolBounds,
  getAvatarRangeHit,
  normalizeAvatarToolBounds,
  type AvatarRangeHit,
  type AvatarToolBounds,
} from './interaction';
import {
  buildAvatarInteractionPayload,
  buildAvatarToolPointerStatePayload,
  buildAvatarToolSelectionStatePayload,
  getAvatarToolStatePayloadKey,
  type AvatarToolPointer,
} from './protocol';
import {
  buildAvatarToolVisualModel,
  createAvatarToolVariantState,
  deriveAvatarToolPresentation,
  getAvatarToolOverlayTransform,
  getAvatarToolPointer,
  getAvatarTool,
  getMonotonicNow,
  isAvatarToolUiExcluded,
  isElectronMultiWindow,
  supportsFinePointer,
} from './presentation';
import {
  resolveAvatarToolCommit,
  resolveAvatarToolPointerDown,
  resolveAvatarToolPointerRelease,
  type AvatarToolCommand,
} from './interaction';
import {
  getAvatarToolEffectRecipe,
  getAvatarToolRegistration,
  type AvatarToolRoundChoiceGesture,
  type AvatarToolVariantId,
} from './catalog';
import {
  AVATAR_TOOL_RANGE_EXIT_PADDING,
  AVATAR_TOOL_RANGE_PADDING,
  AVATAR_TOOL_RUNTIME_POLICY,
} from './interaction';

const VISUAL_HOLD_IS_PRESENTATION_ONLY: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.range.visualHold.semantics === 'presentation-only';
const RANGE_HOLD_MS = VISUAL_HOLD_IS_PRESENTATION_ONLY
  ? AVATAR_TOOL_RUNTIME_POLICY.range.visualHold.durationMs
  : 0;
const BOUNDS_CACHE_TTL_MS = AVATAR_TOOL_RUNTIME_POLICY.range.bounds.cacheTtlMs;
const BOUNDS_MISSING_GRACE_MS = AVATAR_TOOL_RUNTIME_POLICY.range.bounds.missingGraceMs;
const POINTER_BUTTON = AVATAR_TOOL_RUNTIME_POLICY.press.button;
const POINTER_MOVE_COMMIT_THRESHOLD_PX = AVATAR_TOOL_RUNTIME_POLICY.press.move.thresholdPx;
const POINTER_MOVE_USES_STRICT_GREATER: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.press.move.comparison === 'strictly-greater';
const RELEASE_REQUIRES_SAME_POINTER: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.press.matchingRelease.pointerId === 'same-as-press';
const RELEASE_REQUIRES_SAME_BUTTON: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.press.matchingRelease.button === 'same-as-press';
const RELEASE_USES_FRESH_BOUNDS = AVATAR_TOOL_RUNTIME_POLICY.release.bounds === 'fresh';
const RELEASE_REJECTS_UI: boolean = AVATAR_TOOL_RUNTIME_POLICY.release.uiExclusion === 'reject';
const RELEASE_TOUCH_ZONE_USES_FRESH_HIT: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.release.touchZone === 'fresh-release-hit';
const RAW_INTERACTION_PADDING: number = (
  AVATAR_TOOL_RUNTIME_POLICY.press.requiresRawHit
  && !AVATAR_TOOL_RUNTIME_POLICY.release.heldVisualRangeIsHit
)
  ? AVATAR_TOOL_RUNTIME_POLICY.range.enterPadding
  : AVATAR_TOOL_RUNTIME_POLICY.range.exitPadding;
const UI_EXCLUSION_ALLOWS_VISUAL_HOLD: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.range.forcedExit.uiExclusion !== 'immediate';
const DEACTIVATION_ALLOWS_VISUAL_HOLD: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.range.forcedExit.deactivation !== 'immediate';
const HOST_EXIT_ALLOWS_VISUAL_HOLD: boolean =
  AVATAR_TOOL_RUNTIME_POLICY.range.forcedExit.hostExit !== 'immediate';

type RuntimeSession = {
  toolId: AvatarToolId;
  generation: number;
  disposer: AvatarToolDisposer;
  burstHistory: Record<string, number[]>;
  outsideVariantResetTimeoutId: number | null;
  press: RuntimePress | null;
  pressFeedbackActive: boolean;
  roundChoice: RuntimeRoundChoiceCycle | null;
};

type RuntimePress = {
  toolId: AvatarToolId;
  generation: number;
  pointerId: number;
  button: number;
  startX: number;
  startY: number;
  moved: boolean;
  frozenVariant: AvatarToolVariantId;
};

type RuntimeRoundChoiceCycle = {
  variants: ReadonlyArray<AvatarToolVariantId>;
  outsideIntervalMs: number;
  rangeIntervalMs: number;
  rawHitActive: boolean;
  rawHitBounds: AvatarToolBounds | null;
  status: 'preparing' | 'running' | 'pressed' | 'confirmed';
  timeoutId: number | null;
  avatarVariant: AvatarToolVariantId;
  avatarTimeoutId: number | null;
  round: AvatarToolRoundChoiceRound | null;
};

export type AvatarToolRoundChoiceRound = {
  userGesture: AvatarToolRoundChoiceGesture;
  userVariant: AvatarToolVariantId;
  avatarGesture: AvatarToolRoundChoiceGesture;
  avatarVariant: AvatarToolVariantId;
};

type AvatarReactionBubbleAnchorSnapshot = {
  head?: unknown;
  headRect?: unknown;
  bubbleHeadRect?: unknown;
  bounds?: unknown;
};

function normalizeAvatarToolHeadPoint(value: unknown): AvatarToolHeadAnchor | null {
  if (!value || typeof value !== 'object') return null;
  const point = value as { x?: unknown; y?: unknown };
  if (
    typeof point.x !== 'number'
    || typeof point.y !== 'number'
    || !Number.isFinite(point.x)
    || !Number.isFinite(point.y)
  ) return null;
  return { x: point.x, y: point.y, coordinateSpace: 'viewport-css-pixel' };
}

export function resolveAvatarToolHeadAnchor(snapshot: unknown): AvatarToolHeadAnchor | null {
  if (!snapshot || typeof snapshot !== 'object') return null;
  const anchor = snapshot as AvatarReactionBubbleAnchorSnapshot;
  const head = normalizeAvatarToolHeadPoint(anchor.head);
  const bounds = normalizeAvatarToolBounds(anchor.bounds);
  if (head) {
    const headBounds = normalizeAvatarToolBounds(anchor.bubbleHeadRect)
      ?? normalizeAvatarToolBounds(anchor.headRect);
    const headTop = headBounds?.top ?? bounds?.top;
    return {
      ...head,
      y: typeof headTop === 'number' ? Math.min(head.y, headTop) : head.y,
    };
  }
  if (!bounds) return null;
  return {
    x: bounds.left + bounds.width / 2,
    y: bounds.top,
    coordinateSpace: 'viewport-css-pixel',
  };
}

function getActiveAvatarToolHeadAnchor(fallbackBounds: AvatarToolBounds | null): AvatarToolHeadAnchor | null {
  const fallback = fallbackBounds ? resolveAvatarToolHeadAnchor({ bounds: fallbackBounds }) : null;
  if (typeof window === 'undefined') return fallback;
  const helper = (window as Window & {
    avatarReactionBubble?: { getActiveAvatarBubbleAnchor?: () => unknown };
  }).avatarReactionBubble;
  if (typeof helper?.getActiveAvatarBubbleAnchor !== 'function') return fallback;
  try {
    return resolveAvatarToolHeadAnchor(helper.getActiveAvatarBubbleAnchor()) ?? fallback;
  } catch {
    return fallback;
  }
}

export type AvatarToolRuntimeProviders = {
  collectBounds?: () => AvatarToolBounds[];
  isUiExcluded?: (clientX: number, clientY: number, target: EventTarget | null) => boolean;
  now?: () => number;
  monotonicNow?: () => number;
  random?: () => number;
  prepareVisuals?: (toolId: AvatarToolId) => void | Promise<void>;
  getHeadAnchor?: (fallbackBounds: AvatarToolBounds | null) => AvatarToolHeadAnchor | null;
};

type UseAvatarToolRuntimeOptions = {
  composerHidden: boolean;
  composerDisabled: boolean;
  interactionDisabled?: boolean;
  deactivationKey?: string;
  onInteraction?: (payload: AvatarInteractionPayload) => void;
  onStateChange?: (payload: AvatarToolStatePayload) => void;
  getToolLabel: (item: AvatarToolItem) => string;
  onDeactivate?: () => void;
  providers?: AvatarToolRuntimeProviders;
};

export function useAvatarToolRuntime({
  composerHidden,
  composerDisabled,
  interactionDisabled = false,
  deactivationKey,
  onInteraction,
  onStateChange,
  getToolLabel,
  onDeactivate,
  providers,
}: UseAvatarToolRuntimeOptions) {
  const collectBounds = providers?.collectBounds ?? collectAvatarToolBounds;
  const isUiExcluded = providers?.isUiExcluded ?? isAvatarToolUiExcluded;
  const now = providers?.now ?? Date.now;
  const monotonicNow = providers?.monotonicNow ?? getMonotonicNow;
  const random = providers?.random ?? Math.random;
  const prepareVisuals = providers?.prepareVisuals ?? prepareAvatarToolVisuals;
  const getHeadAnchor = providers?.getHeadAnchor ?? getActiveAvatarToolHeadAnchor;
  const ownsLocalPointerRuntime = !isElectronMultiWindow();
  const [activeToolId, setActiveToolId] = useState<AvatarToolId | null>(null);
  const [rangeVariants, setRangeVariants] = useState(createAvatarToolVariantState);
  const [outsideVariants, setOutsideVariants] = useState(createAvatarToolVariantState);
  const [overAvatarRange, setOverAvatarRange] = useState(false);
  const [overCompactZone, setOverCompactZone] = useState(false);
  const [insideHostWindow, setInsideHostWindow] = useState(true);
  const [overlayEffectExecution, setOverlayEffectExecution] =
    useState<ActiveHammerSwingEffectExecution | null>(null);
  const [transientEffects, setTransientEffects] = useState<AvatarToolTransientVisualEffect[]>([]);
  const [roundChoiceAvatarGestureState, setRoundChoiceAvatarGestureState] =
    useState<AvatarToolRoundChoiceAvatarGestureState | null>(null);
  const [roundChoiceRound, setRoundChoiceRound] = useState<AvatarToolRoundChoiceRound | null>(null);

  const generationRef = useRef(0);
  const sessionRef = useRef<RuntimeSession | null>(null);
  const latestPointerRef = useRef<AvatarToolPointer>({ x: 0, y: 0 });
  const latestTargetRef = useRef<EventTarget | null>(null);
  const rangeRef = useRef(false);
  const compactZoneRef = useRef(false);
  const insideHostRef = useRef(true);
  const activeToolIdRef = useRef<AvatarToolId | null>(null);
  const rangeVariantsRef = useRef(rangeVariants);
  const outsideVariantsRef = useRef(outsideVariants);
  const presentedVariantRef = useRef<AvatarToolVariantId>('primary');
  const overlayEffectExecutionRef = useRef<ActiveHammerSwingEffectExecution | null>(null);
  const interactionLockRef = useRef(false);
  const interactionCallbackRef = useRef(onInteraction);
  const stateCallbackRef = useRef(onStateChange);
  const deactivateCallbackRef = useRef(onDeactivate);
  const toolLabelCallbackRef = useRef(getToolLabel);
  const lastStateKeyRef = useRef('');
  const lastDeactivationKeyRef = useRef('');
  const boundsCacheRef = useRef<{ expiresAt: number; lastAvailableAt: number; bounds: AvatarToolBounds[] }>({
    expiresAt: 0,
    lastAvailableAt: 0,
    bounds: [],
  });
  const rangeHoldUntilRef = useRef(0);
  const rangeHoldTimerRef = useRef<number | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const effectVisualIdRef = useRef(0);

  const overlayEffectActive = overlayEffectExecution !== null;
  const presentation = useMemo(() => deriveAvatarToolPresentation({
    activeToolId,
    rangeVariants,
    outsideVariants,
    overAvatarRange,
    overCompactZone,
    insideHostWindow,
    effectActive: overlayEffectActive,
  }), [
    activeToolId,
    overlayEffectActive,
    insideHostWindow,
    outsideVariants,
    overAvatarRange,
    overCompactZone,
    rangeVariants,
  ]);
  const {
    activeTool,
    avatarRangeVariant,
    effectiveVariant,
    withinAvatarRange,
    imageKind,
  } = presentation;
  const shouldUseLocalOverlay = !!activeTool && supportsFinePointer() && !isElectronMultiWindow();
  const localOverlayAllowed = shouldUseLocalOverlay && insideHostWindow;
  const overlayActive = localOverlayAllowed;
  const overlayCompact = overlayActive && imageKind === 'pointer';

  const getBounds = useCallback((fresh = false) => {
    const timestamp = monotonicNow();
    if (fresh) {
      const collected = collectBounds();
      if (collected.length > 0) {
        boundsCacheRef.current = {
          expiresAt: timestamp + BOUNDS_CACHE_TTL_MS,
          lastAvailableAt: timestamp,
          bounds: collected,
        };
      }
      return collected;
    }
    if (boundsCacheRef.current.expiresAt <= timestamp) {
      const collected = collectBounds();
      if (collected.length > 0) {
        boundsCacheRef.current = {
          expiresAt: timestamp + BOUNDS_CACHE_TTL_MS,
          lastAvailableAt: timestamp,
          bounds: collected,
        };
      } else if (timestamp - boundsCacheRef.current.lastAvailableAt > BOUNDS_MISSING_GRACE_MS) {
        boundsCacheRef.current = { expiresAt: timestamp + BOUNDS_CACHE_TTL_MS, lastAvailableAt: 0, bounds: [] };
      } else {
        boundsCacheRef.current.expiresAt = timestamp + BOUNDS_CACHE_TTL_MS;
      }
    }
    return boundsCacheRef.current.bounds;
  }, [collectBounds, monotonicNow]);

  const clearRangeHold = useCallback(() => {
    if (rangeHoldTimerRef.current !== null) window.clearTimeout(rangeHoldTimerRef.current);
    rangeHoldTimerRef.current = null;
    rangeHoldUntilRef.current = 0;
  }, []);

  const setRange = useCallback((next: boolean, allowHold = true) => {
    if (rangeHoldTimerRef.current !== null) {
      window.clearTimeout(rangeHoldTimerRef.current);
      rangeHoldTimerRef.current = null;
    }
    if (next) {
      rangeHoldUntilRef.current = 0;
      rangeRef.current = true;
      setOverAvatarRange(true);
      return;
    }
    if (allowHold && rangeRef.current) {
      const timestamp = monotonicNow();
      if (rangeHoldUntilRef.current <= 0) {
        rangeHoldUntilRef.current = timestamp + RANGE_HOLD_MS;
      }
      const finishRangeHold = () => {
        rangeHoldTimerRef.current = null;
        const remaining = rangeHoldUntilRef.current - monotonicNow();
        if (remaining > 0) {
          rangeHoldTimerRef.current = window.setTimeout(finishRangeHold, remaining);
          return;
        }
        rangeHoldUntilRef.current = 0;
        rangeRef.current = false;
        setOverAvatarRange(false);
      };
      const remaining = rangeHoldUntilRef.current - timestamp;
      if (remaining > 0) {
        rangeHoldTimerRef.current = window.setTimeout(finishRangeHold, remaining);
        return;
      }
    }
    clearRangeHold();
    rangeRef.current = false;
    setOverAvatarRange(false);
  }, [clearRangeHold, monotonicNow]);

  const getRawHit = useCallback((clientX: number, clientY: number, fresh = false): AvatarRangeHit | null => (
    getAvatarRangeHit(
      clientX,
      clientY,
      getBounds(fresh),
      RAW_INTERACTION_PADDING,
    )
  ), [getBounds]);

  const getVisualHit = useCallback((clientX: number, clientY: number): AvatarRangeHit | null => (
    getAvatarRangeHit(
      clientX,
      clientY,
      getBounds(),
      rangeRef.current ? AVATAR_TOOL_RANGE_EXIT_PADDING : AVATAR_TOOL_RANGE_PADDING,
    )
  ), [getBounds]);

  const updateOverlayPosition = useCallback((pointer = latestPointerRef.current) => {
    const tool = getAvatarTool(activeToolIdRef.current);
    if (!tool) return;
    const current = deriveAvatarToolPresentation({
      activeToolId: tool.id,
      rangeVariants: rangeVariantsRef.current,
      outsideVariants: outsideVariantsRef.current,
      overAvatarRange: rangeRef.current,
      overCompactZone: compactZoneRef.current,
      insideHostWindow: insideHostRef.current,
      effectActive: overlayEffectExecutionRef.current !== null,
    });
    const compact = current.imageKind === 'pointer';
    const node = overlayRef.current;
    if (node) node.style.transform = getAvatarToolOverlayTransform(tool, compact, pointer);
  }, []);

  const publishState = useCallback(() => {
    const callback = stateCallbackRef.current;
    if (!callback) return;
    const toolId = activeToolIdRef.current;
    if (!ownsLocalPointerRuntime) {
      const currentTool = getAvatarTool(toolId);
      const payload = buildAvatarToolSelectionStatePayload({
        activeTool: currentTool,
        avatarRangeVariant: currentTool ? rangeVariantsRef.current[currentTool.id] : undefined,
        outsideRangeVariant: currentTool ? outsideVariantsRef.current[currentTool.id] : undefined,
      });
      const key = getAvatarToolStatePayloadKey(payload);
      if (key === lastStateKeyRef.current) return;
      lastStateKeyRef.current = key;
      callback(payload);
      return;
    }
    const current = deriveAvatarToolPresentation({
      activeToolId: toolId,
      rangeVariants: rangeVariantsRef.current,
      outsideVariants: outsideVariantsRef.current,
      overAvatarRange: rangeRef.current,
      overCompactZone: compactZoneRef.current,
      insideHostWindow: insideHostRef.current,
      effectActive: overlayEffectExecutionRef.current !== null,
    });
    const payload = buildAvatarToolPointerStatePayload({
      activeTool: current.activeTool,
      variant: current.effectiveVariant,
      avatarRangeVariant: current.avatarRangeVariant,
      outsideRangeVariant: current.outsideRangeVariant,
      imageKind: current.imageKind,
      withinAvatarRange: current.withinAvatarRange,
      overCompactZone: compactZoneRef.current,
      insideHostWindow: insideHostRef.current,
      pointer: latestPointerRef.current,
      label: current.activeTool ? toolLabelCallbackRef.current(current.activeTool) : undefined,
    });
    const key = getAvatarToolStatePayloadKey(payload);
    if (key === lastStateKeyRef.current) return;
    lastStateKeyRef.current = key;
    callback(payload);
  }, [ownsLocalPointerRuntime]);

  const publishInactiveState = useCallback(() => {
    const callback = stateCallbackRef.current;
    if (!callback) return;
    const payload = ownsLocalPointerRuntime ? buildAvatarToolPointerStatePayload({
      activeTool: null,
      variant: 'primary',
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      imageKind: 'pointer',
      withinAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: insideHostRef.current,
      pointer: latestPointerRef.current,
    }) : buildAvatarToolSelectionStatePayload({
      activeTool: null,
    });
    const key = getAvatarToolStatePayloadKey(payload);
    if (key === lastStateKeyRef.current) return;
    lastStateKeyRef.current = key;
    callback(payload);
  }, [ownsLocalPointerRuntime]);

  const disposeSession = useCallback(() => {
    generationRef.current += 1;
    sessionRef.current?.disposer.destroy();
    sessionRef.current = null;
    clearRangeHold();
  }, [clearRangeHold]);

  const destroySession = useCallback(() => {
    disposeSession();
    setOverlayEffectExecution(null);
    overlayEffectExecutionRef.current = null;
    interactionLockRef.current = false;
    setTransientEffects([]);
    setRoundChoiceAvatarGestureState(null);
    setRoundChoiceRound(null);
    const nextRangeVariants = createAvatarToolVariantState();
    const nextOutsideVariants = createAvatarToolVariantState();
    setRangeVariants(nextRangeVariants);
    setOutsideVariants(nextOutsideVariants);
    rangeVariantsRef.current = nextRangeVariants;
    outsideVariantsRef.current = nextOutsideVariants;
  }, [disposeSession]);

  const setRoundChoiceVariants = useCallback((session: RuntimeSession, variant: AvatarToolVariantId) => {
    const nextRange = { ...rangeVariantsRef.current, [session.toolId]: variant };
    rangeVariantsRef.current = nextRange;
    setRangeVariants(nextRange);
    publishState();
  }, [publishState]);

  const stopRoundChoiceAvatarGesture = useCallback((session: RuntimeSession) => {
    const cycle = session.roundChoice;
    if (!cycle) return;
    if (cycle.avatarTimeoutId !== null) {
      session.disposer.clearTimeout(cycle.avatarTimeoutId);
      cycle.avatarTimeoutId = null;
    }
    setRoundChoiceAvatarGestureState(null);
  }, []);

  const startRoundChoiceAvatarGesture = useCallback((session: RuntimeSession) => {
    const cycle = session.roundChoice;
    if (!cycle || !session.disposer.isCurrent()) return;
    if (cycle.avatarTimeoutId !== null) session.disposer.clearTimeout(cycle.avatarTimeoutId);
    cycle.avatarTimeoutId = null;
    const finalVisible = cycle.status === 'confirmed' && cycle.round !== null;
    if ((!cycle.rawHitActive && !finalVisible) || cycle.status === 'preparing') {
      setRoundChoiceAvatarGestureState(null);
      return;
    }
    const update = (advance: boolean) => {
      const keepFinalVisible = cycle.status === 'confirmed' && cycle.round !== null;
      if (!session.disposer.isCurrent() || (!cycle.rawHitActive && !keepFinalVisible)) {
        stopRoundChoiceAvatarGesture(session);
        return;
      }
      if (advance && !cycle.round) {
        const currentIndex = cycle.variants.indexOf(cycle.avatarVariant);
        cycle.avatarVariant = cycle.variants[
          (currentIndex + 1 + cycle.variants.length) % cycle.variants.length
        ];
      }
      const anchor = getHeadAnchor(cycle.rawHitBounds);
      const nextState: AvatarToolRoundChoiceAvatarGestureState | null = anchor ? {
        variant: cycle.round?.avatarVariant ?? cycle.avatarVariant,
        phase: cycle.round ? 'final' : 'cycling',
        anchor,
      } : null;
      setRoundChoiceAvatarGestureState(previous => (
        previous
        && nextState
        && previous.variant === nextState.variant
        && previous.phase === nextState.phase
        && previous.anchor.x === nextState.anchor.x
        && previous.anchor.y === nextState.anchor.y
          ? previous
          : nextState
      ));
      cycle.avatarTimeoutId = session.disposer.setTimeout(
        () => update(true),
        cycle.outsideIntervalMs,
      );
    };
    update(false);
  }, [getHeadAnchor, stopRoundChoiceAvatarGesture]);

  const clearRoundChoiceRound = useCallback((session: RuntimeSession) => {
    const cycle = session.roundChoice;
    if (!cycle) return;
    cycle.round = null;
    setRoundChoiceRound(null);
    if (cycle.rawHitActive && cycle.status !== 'preparing') {
      startRoundChoiceAvatarGesture(session);
    } else {
      stopRoundChoiceAvatarGesture(session);
    }
  }, [startRoundChoiceAvatarGesture, stopRoundChoiceAvatarGesture]);

  const confirmRoundChoice = useCallback((
    session: RuntimeSession,
    userGesture: AvatarToolRoundChoiceGesture,
    userVariant: AvatarToolVariantId,
    avatarGesture: AvatarToolRoundChoiceGesture,
    avatarVariant: AvatarToolVariantId,
  ) => {
    const cycle = session.roundChoice;
    if (!cycle) return;
    const round = { userGesture, userVariant, avatarGesture, avatarVariant };
    cycle.round = round;
    cycle.avatarVariant = avatarVariant;
    setRoundChoiceRound(round);
    startRoundChoiceAvatarGesture(session);
  }, [startRoundChoiceAvatarGesture]);

  const resumeRoundChoiceCycle = useCallback((session: RuntimeSession, confirmationHoldMs?: number) => {
    const cycle = session.roundChoice;
    if (!cycle || !session.disposer.isCurrent()) return;
    if (cycle.timeoutId !== null) session.disposer.clearTimeout(cycle.timeoutId);
    cycle.status = confirmationHoldMs === undefined ? 'running' : 'confirmed';
    const advance = () => {
      if (
        (cycle.status !== 'running' && cycle.status !== 'confirmed')
        || !session.disposer.isCurrent()
      ) return;
      const confirmationEnded = cycle.status === 'confirmed';
      cycle.status = 'running';
      if (confirmationEnded) clearRoundChoiceRound(session);
      const currentVariant = rangeVariantsRef.current[session.toolId];
      const currentIndex = cycle.variants.indexOf(currentVariant);
      const nextVariant = cycle.variants[(currentIndex + 1 + cycle.variants.length) % cycle.variants.length];
      setRoundChoiceVariants(session, nextVariant);
      const intervalMs = cycle.rawHitActive ? cycle.rangeIntervalMs : cycle.outsideIntervalMs;
      cycle.timeoutId = session.disposer.setTimeout(advance, intervalMs);
    };
    const intervalMs = confirmationHoldMs
      ?? (cycle.rawHitActive ? cycle.rangeIntervalMs : cycle.outsideIntervalMs);
    cycle.timeoutId = session.disposer.setTimeout(advance, intervalMs);
  }, [clearRoundChoiceRound, setRoundChoiceVariants]);

  const cancelRoundChoiceState = useCallback((session: RuntimeSession) => {
    const cycle = session.roundChoice;
    if (!cycle) return;
    if (cycle.status === 'confirmed') {
      cycle.round = null;
      setRoundChoiceRound(null);
      stopRoundChoiceAvatarGesture(session);
      resumeRoundChoiceCycle(session);
      return;
    }
    if (cycle.round) clearRoundChoiceRound(session);
  }, [clearRoundChoiceRound, resumeRoundChoiceCycle, stopRoundChoiceAvatarGesture]);

  const updateRoundChoiceRawHit = useCallback((session: RuntimeSession, hit: AvatarRangeHit | null) => {
    const cycle = session.roundChoice;
    if (!cycle) return;
    const rawHitActive = hit !== null;
    if (hit) cycle.rawHitBounds = hit.bounds;
    if (cycle.rawHitActive === rawHitActive) return;
    cycle.rawHitActive = rawHitActive;
    if (cycle.status === 'running') resumeRoundChoiceCycle(session);
    if (rawHitActive) startRoundChoiceAvatarGesture(session);
    else if (cycle.status === 'confirmed' && cycle.round) startRoundChoiceAvatarGesture(session);
    else stopRoundChoiceAvatarGesture(session);
  }, [resumeRoundChoiceCycle, startRoundChoiceAvatarGesture, stopRoundChoiceAvatarGesture]);

  const createSession = useCallback((toolId: AvatarToolId) => {
    destroySession();
    const generation = generationRef.current;
    const disposer = createAvatarToolDisposer(generation, value => value === generationRef.current);
    const profile = getAvatarToolRegistration(toolId).definition.interaction;
    const session: RuntimeSession = {
      toolId,
      generation,
      disposer,
      burstHistory: {},
      outsideVariantResetTimeoutId: null,
      press: null,
      pressFeedbackActive: false,
      roundChoice: profile.kind === 'round-choice' ? {
        variants: profile.choices.map(choice => choice.variant),
        outsideIntervalMs: profile.cycle.outsideIntervalMs,
        rangeIntervalMs: profile.cycle.rangeIntervalMs,
        rawHitActive: false,
        rawHitBounds: null,
        status: 'preparing',
        timeoutId: null,
        avatarVariant: profile.choices[0].variant,
        avatarTimeoutId: null,
        round: null,
      } : null,
    };
    sessionRef.current = session;
    prewarmAvatarToolSounds(toolId, disposer);
    if (!session.roundChoice) return;
    try {
      const readiness = prepareVisuals(toolId);
      if (!readiness) {
        resumeRoundChoiceCycle(session);
        return;
      }
      Promise.resolve(readiness)
        .catch(() => undefined)
        .then(() => {
          if (sessionRef.current !== session || !session.disposer.isCurrent()) return;
          resumeRoundChoiceCycle(session);
          if (session.roundChoice?.rawHitActive) startRoundChoiceAvatarGesture(session);
        });
    } catch {
      resumeRoundChoiceCycle(session);
      if (session.roundChoice.rawHitActive) startRoundChoiceAvatarGesture(session);
    }
  }, [destroySession, prepareVisuals, resumeRoundChoiceCycle, startRoundChoiceAvatarGesture]);

  const clearTool = useCallback((options?: { insideHostWindow?: boolean }) => {
    destroySession();
    activeToolIdRef.current = null;
    setActiveToolId(null);
    latestTargetRef.current = null;
    compactZoneRef.current = false;
    setOverCompactZone(false);
    setRange(false, DEACTIVATION_ALLOWS_VISUAL_HOLD);
    if (options?.insideHostWindow !== undefined) {
      insideHostRef.current = options.insideHostWindow;
      setInsideHostWindow(options.insideHostWindow);
    }
    deactivateCallbackRef.current?.();
    publishInactiveState();
  }, [destroySession, publishInactiveState, setRange]);

  const selectTool = useCallback((item: AvatarToolItem, event: ReactMouseEvent<HTMLButtonElement>) => {
    if (composerHidden || composerDisabled || interactionDisabled) {
      clearTool();
      return;
    }
    if (!ownsLocalPointerRuntime) {
      if (activeToolIdRef.current === item.id) {
        clearTool();
        return;
      }
      destroySession();
      activeToolIdRef.current = item.id;
      setActiveToolId(item.id);
      const initialVariant = getAvatarToolRegistration(item.id).definition.visual.initialVariant;
      const nextRange = { ...rangeVariantsRef.current, [item.id]: initialVariant };
      const nextOutside = { ...outsideVariantsRef.current, [item.id]: initialVariant };
      rangeVariantsRef.current = nextRange;
      outsideVariantsRef.current = nextOutside;
      setRangeVariants(nextRange);
      setOutsideVariants(nextOutside);
      window.queueMicrotask(publishState);
      return;
    }
    latestPointerRef.current = getAvatarToolPointer(event);
    latestTargetRef.current = event.currentTarget;
    insideHostRef.current = true;
    setInsideHostWindow(true);
    compactZoneRef.current = true;
    setOverCompactZone(true);
    setRange(false, UI_EXCLUSION_ALLOWS_VISUAL_HOLD);
    if (activeToolIdRef.current === item.id) {
      clearTool();
      return;
    }
    createSession(item.id);
    activeToolIdRef.current = item.id;
    setActiveToolId(item.id);
    const initialVariant = getAvatarToolRegistration(item.id).definition.visual.initialVariant;
    const nextRange = { ...rangeVariantsRef.current, [item.id]: initialVariant };
    const nextOutside = { ...outsideVariantsRef.current, [item.id]: initialVariant };
    rangeVariantsRef.current = nextRange;
    outsideVariantsRef.current = nextOutside;
    setRangeVariants(nextRange);
    setOutsideVariants(nextOutside);
    window.queueMicrotask(() => {
      updateOverlayPosition();
      publishState();
    });
  }, [
    clearTool,
    composerDisabled,
    composerHidden,
    createSession,
    destroySession,
    interactionDisabled,
    ownsLocalPointerRuntime,
    publishState,
    setRange,
    updateOverlayPosition,
  ]);

  const recordBurst = useCallback((key: string, windowMs: number): number => {
    const session = sessionRef.current;
    if (!session) return 0;
    const timestamp = now();
    const recent = (session.burstHistory[key] ?? []).filter(value => timestamp - value <= windowMs);
    recent.push(timestamp);
    session.burstHistory[key] = recent;
    return recent.length;
  }, [now]);

  const executeEffect = useCallback((
    effectId: string,
    effectMode: string | undefined,
    clientX: number,
    clientY: number,
  ) => {
    const session = sessionRef.current;
    if (!session) return;
    const recipe = getAvatarToolEffectRecipe(session.toolId, effectId);
    const execution = createAvatarToolEffectExecution(recipe, {
      clientX,
      clientY,
      nextId: () => ++effectVisualIdRef.current,
      random,
      mode: effectMode,
    });
    if (execution.kind === 'fixed-particles' || execution.kind === 'random-scatter') {
      const visuals: AvatarToolTransientVisualEffect[] = [...execution.visuals];
      setTransientEffects(current => [...current, ...visuals]);
      visuals.forEach(visual => session.disposer.setTimeout(() => {
        setTransientEffects(current => current.filter(item => item.id !== visual.id));
      }, getAvatarToolTransientEffectLifetimeMs(visual)));
      return;
    }
    const initial = execution.recipe.timeline[0];
    if (!initial) return;
    interactionLockRef.current = execution.interactionLock === 'effect-lifetime';
    const initialExecution = { ...execution, phase: initial.phase };
    overlayEffectExecutionRef.current = initialExecution;
    setOverlayEffectExecution(initialExecution);
    execution.recipe.timeline.forEach(({ phase, delayMs }, timelineIndex) => {
      if (timelineIndex === 0) return;
      session.disposer.setTimeout(() => {
        if (phase === 'idle') {
          overlayEffectExecutionRef.current = null;
          setOverlayEffectExecution(null);
          interactionLockRef.current = false;
        } else {
          const nextExecution = { ...execution, phase };
          overlayEffectExecutionRef.current = nextExecution;
          setOverlayEffectExecution(nextExecution);
        }
        updateOverlayPosition();
        publishState();
      }, delayMs);
    });
  }, [publishState, random, updateOverlayPosition]);

  const emit = useCallback((commit: Parameters<typeof buildAvatarInteractionPayload>[0]) => {
    const callback = interactionCallbackRef.current;
    if (!callback) return;
    callback(buildAvatarInteractionPayload({
      ...commit,
      timestamp: commit.timestamp ?? now(),
    }));
  }, [now]);

  const applyCommand = useCallback((command: AvatarToolCommand, clientX: number, clientY: number) => {
    const session = sessionRef.current;
    if (!session) return;
    if (command.rangeVariant) {
      const next = { ...rangeVariantsRef.current, [session.toolId]: command.rangeVariant };
      rangeVariantsRef.current = next;
      setRangeVariants(next);
    }
    if (command.outsideVariant) {
      const next = { ...outsideVariantsRef.current, [session.toolId]: command.outsideVariant };
      outsideVariantsRef.current = next;
      setOutsideVariants(next);
    }
    if (command.commit) emit(command.commit);
    if (command.sound) playAvatarToolSound(command.sound, session.disposer);
    if (command.pressFeedback === 'until-pointer-release') session.pressFeedbackActive = true;
    if (command.roundChoiceCycle && session.roundChoice) {
      const cycle = session.roundChoice;
      if (cycle.timeoutId !== null) {
        session.disposer.clearTimeout(cycle.timeoutId);
        cycle.timeoutId = null;
      }
      if (command.roundChoiceCycle === 'pause') {
        cycle.status = 'pressed';
        if (cycle.round) clearRoundChoiceRound(session);
      } else if (
        command.roundChoiceUserGesture
        && command.roundChoiceUserVariant
        && command.roundChoiceAvatarGesture
        && command.roundChoiceAvatarVariant
      ) {
        resumeRoundChoiceCycle(session, command.roundChoiceHoldMs);
        confirmRoundChoice(
          session,
          command.roundChoiceUserGesture,
          command.roundChoiceUserVariant,
          command.roundChoiceAvatarGesture,
          command.roundChoiceAvatarVariant,
        );
      }
    }
    if (command.effect) executeEffect(command.effect, command.effectMode, clientX, clientY);
    if (command.resetOutsideVariantAfterMs) {
      if (session.outsideVariantResetTimeoutId !== null) {
        session.disposer.clearTimeout(session.outsideVariantResetTimeoutId);
      }
      session.outsideVariantResetTimeoutId = session.disposer.setTimeout(() => {
        session.outsideVariantResetTimeoutId = null;
        const initialVariant = getAvatarToolRegistration(session.toolId).definition.visual.initialVariant;
        const next = { ...outsideVariantsRef.current, [session.toolId]: initialVariant };
        outsideVariantsRef.current = next;
        setOutsideVariants(next);
        publishState();
      }, command.resetOutsideVariantAfterMs);
    }
    publishState();
  }, [clearRoundChoiceRound, confirmRoundChoice, emit, executeEffect, publishState, resumeRoundChoiceCycle]);

  const releasePressFeedback = useCallback((cancelOutsideFeedback: boolean) => {
    const session = sessionRef.current;
    if (!session) return;
    const shouldRelease = session.press !== null || session.pressFeedbackActive;
    session.press = null;
    session.pressFeedbackActive = false;
    if (session.roundChoice?.status === 'pressed') resumeRoundChoiceCycle(session);
    if (cancelOutsideFeedback && session.outsideVariantResetTimeoutId !== null) {
      session.disposer.clearTimeout(session.outsideVariantResetTimeoutId);
      session.outsideVariantResetTimeoutId = null;
      const initialVariant = getAvatarToolRegistration(session.toolId).definition.visual.initialVariant;
      applyCommand({ outsideVariant: initialVariant }, latestPointerRef.current.x, latestPointerRef.current.y);
    }
    if (shouldRelease) {
      applyCommand(
        resolveAvatarToolPointerRelease(session.toolId),
        latestPointerRef.current.x,
        latestPointerRef.current.y,
      );
    }
  }, [applyCommand, resumeRoundChoiceCycle]);

  useEffect(() => {
    if (!activeToolId || !ownsLocalPointerRuntime) return;
    const handlePointerDown = (event: PointerEvent) => {
      const session = sessionRef.current;
      if (!session) return;
      latestPointerRef.current = getAvatarToolPointer(event);
      latestTargetRef.current = event.target;
      const blocked = isUiExcluded(event.clientX, event.clientY, event.target);
      compactZoneRef.current = blocked;
      setOverCompactZone(blocked);
      if (blocked) {
        updateRoundChoiceRawHit(session, null);
        setRange(false, UI_EXCLUSION_ALLOWS_VISUAL_HOLD);
        publishState();
        return;
      }
      if (
        event.button !== POINTER_BUTTON
        || session.press
        || session.roundChoice?.status === 'preparing'
        || session.roundChoice?.status === 'confirmed'
      ) return;
      const hit = getRawHit(event.clientX, event.clientY);
      updateRoundChoiceRawHit(session, hit);
      setRange(!!getVisualHit(event.clientX, event.clientY));
      const toolId = session.toolId;
      const interactionLocked = interactionLockRef.current;
      const visibleVariant = presentedVariantRef.current;
      applyCommand(resolveAvatarToolPointerDown({
        toolId,
        clientX: event.clientX,
        clientY: event.clientY,
        hit,
        rangeVariant: rangeVariantsRef.current[toolId],
        outsideVariant: outsideVariantsRef.current[toolId],
        visibleVariant,
        interactionLocked,
        recordBurst,
        random,
      }), event.clientX, event.clientY);
      if (hit && !interactionLocked) {
        session.press = {
          toolId,
          generation: session.generation,
          pointerId: event.pointerId,
          button: event.button,
          startX: event.clientX,
          startY: event.clientY,
          moved: false,
          frozenVariant: visibleVariant,
        };
      }
    };
    const handlePointerUp = (event: PointerEvent) => {
      const session = sessionRef.current;
      if (!session) return;
      const press = session.press;
      const releaseDoesNotMatch = !!press && (
        (RELEASE_REQUIRES_SAME_POINTER && press.pointerId !== event.pointerId)
        || (RELEASE_REQUIRES_SAME_BUTTON && press.button !== event.button)
      );
      latestPointerRef.current = getAvatarToolPointer(event);
      latestTargetRef.current = event.target;
      const blocked = isUiExcluded(event.clientX, event.clientY, event.target);
      compactZoneRef.current = blocked;
      setOverCompactZone(blocked);
      if (blocked) {
        setRange(false, UI_EXCLUSION_ALLOWS_VISUAL_HOLD);
      }
      if (releaseDoesNotMatch) return;
      session.press = null;
      const releaseHit = blocked && RELEASE_REJECTS_UI
        ? null
        : getRawHit(event.clientX, event.clientY, RELEASE_USES_FRESH_BOUNDS);
      updateRoundChoiceRawHit(session, releaseHit);
      if (!blocked) {
        setRange(!!getVisualHit(event.clientX, event.clientY));
      }
      const commitHit = RELEASE_TOUCH_ZONE_USES_FRESH_HIT ? releaseHit : null;
      if (
        press
        && (!RELEASE_REQUIRES_SAME_POINTER || press.pointerId === event.pointerId)
        && (!RELEASE_REQUIRES_SAME_BUTTON || press.button === event.button)
        && press.generation === session.generation
        && press.toolId === session.toolId
        && !press.moved
        && commitHit
      ) {
        applyCommand(resolveAvatarToolCommit({
          toolId: session.toolId,
          clientX: event.clientX,
          clientY: event.clientY,
          hit: commitHit,
          rangeVariant: rangeVariantsRef.current[session.toolId],
          outsideVariant: outsideVariantsRef.current[session.toolId],
          visibleVariant: press.frozenVariant,
          interactionLocked: interactionLockRef.current,
          recordBurst,
          random,
        }), event.clientX, event.clientY);
      }
      releasePressFeedback(false);
    };
    const handlePointerCancel = (event: PointerEvent) => {
      const session = sessionRef.current;
      const press = session?.press;
      if (press && press.pointerId !== event.pointerId) return;
      if (session) cancelRoundChoiceState(session);
      releasePressFeedback(true);
    };
    const handleBlur = () => {
      const session = sessionRef.current;
      if (session) cancelRoundChoiceState(session);
      releasePressFeedback(true);
    };
    const handleVisibilityCancel = () => {
      if (!document.hidden) return;
      const session = sessionRef.current;
      if (session) cancelRoundChoiceState(session);
      releasePressFeedback(true);
    };
    window.addEventListener('pointerdown', handlePointerDown, true);
    window.addEventListener('pointerup', handlePointerUp, true);
    window.addEventListener('pointercancel', handlePointerCancel, true);
    window.addEventListener('blur', handleBlur);
    document.addEventListener('visibilitychange', handleVisibilityCancel);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown, true);
      window.removeEventListener('pointerup', handlePointerUp, true);
      window.removeEventListener('pointercancel', handlePointerCancel, true);
      window.removeEventListener('blur', handleBlur);
      document.removeEventListener('visibilitychange', handleVisibilityCancel);
    };
  }, [
    activeToolId,
    applyCommand,
    cancelRoundChoiceState,
    getRawHit,
    getVisualHit,
    isUiExcluded,
    ownsLocalPointerRuntime,
    publishState,
    random,
    recordBurst,
    releasePressFeedback,
    setRange,
    updateRoundChoiceRawHit,
  ]);

  useEffect(() => {
    if (!activeToolId || !ownsLocalPointerRuntime) return;
    let frameId = 0;
    const handlePointerMove = (event: PointerEvent) => {
      const press = sessionRef.current?.press;
      if (press && press.pointerId === event.pointerId && !press.moved) {
        const deltaX = event.clientX - press.startX;
        const deltaY = event.clientY - press.startY;
        const movedPastThreshold = deltaX * deltaX + deltaY * deltaY > POINTER_MOVE_COMMIT_THRESHOLD_PX ** 2;
        if (POINTER_MOVE_USES_STRICT_GREATER && movedPastThreshold) {
          press.moved = true;
        }
      }
      insideHostRef.current = true;
      setInsideHostWindow(true);
      latestPointerRef.current = getAvatarToolPointer(event);
      latestTargetRef.current = event.target;
      updateOverlayPosition(latestPointerRef.current);
      if (frameId) return;
      frameId = window.requestAnimationFrame(() => {
        frameId = 0;
        const pointer = latestPointerRef.current;
        const blocked = isUiExcluded(pointer.x, pointer.y, latestTargetRef.current);
        compactZoneRef.current = blocked;
        setOverCompactZone(blocked);
        if (blocked) {
          const session = sessionRef.current;
          if (session) updateRoundChoiceRawHit(session, null);
          setRange(false, UI_EXCLUSION_ALLOWS_VISUAL_HOLD);
        } else {
          const session = sessionRef.current;
          if (session) updateRoundChoiceRawHit(session, getRawHit(pointer.x, pointer.y));
          setRange(!!getVisualHit(pointer.x, pointer.y));
        }
        publishState();
      });
    };
    const hide = () => {
      const activeSession = sessionRef.current;
      if (activeSession) cancelRoundChoiceState(activeSession);
      releasePressFeedback(true);
      boundsCacheRef.current.expiresAt = 0;
      latestTargetRef.current = null;
      compactZoneRef.current = false;
      setOverCompactZone(false);
      insideHostRef.current = false;
      setInsideHostWindow(false);
      const session = sessionRef.current;
      if (session) updateRoundChoiceRawHit(session, null);
      setRange(false, HOST_EXIT_ALLOWS_VISUAL_HOLD);
      publishState();
    };
    const outsideViewport = (event: MouseEvent | PointerEvent) => (
      event.clientX <= 0 || event.clientY <= 0 || event.clientX >= window.innerWidth || event.clientY >= window.innerHeight
    );
    const handleOut = (event: MouseEvent | PointerEvent) => {
      if (event.relatedTarget === null && outsideViewport(event)) hide();
    };
    const handleVisibility = () => { if (document.hidden) hide(); };
    window.addEventListener('pointermove', handlePointerMove, { passive: true, capture: true });
    document.addEventListener('mouseleave', hide);
    window.addEventListener('pointerout', handleOut, true);
    window.addEventListener('mouseout', handleOut, true);
    window.addEventListener('blur', hide);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      if (frameId) window.cancelAnimationFrame(frameId);
      window.removeEventListener('pointermove', handlePointerMove, true);
      document.removeEventListener('mouseleave', hide);
      window.removeEventListener('pointerout', handleOut, true);
      window.removeEventListener('mouseout', handleOut, true);
      window.removeEventListener('blur', hide);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [
    activeToolId,
    cancelRoundChoiceState,
    getRawHit,
    getVisualHit,
    isUiExcluded,
    ownsLocalPointerRuntime,
    publishState,
    releasePressFeedback,
    setRange,
    updateOverlayPosition,
    updateRoundChoiceRawHit,
  ]);

  useLayoutEffect(() => {
    presentedVariantRef.current = effectiveVariant;
  }, [effectiveVariant]);

  useEffect(() => {
    activeToolIdRef.current = activeToolId;
    rangeVariantsRef.current = rangeVariants;
    outsideVariantsRef.current = outsideVariants;
    rangeRef.current = overAvatarRange;
    compactZoneRef.current = overCompactZone;
    insideHostRef.current = insideHostWindow;
    updateOverlayPosition();
    publishState();
  }, [
    activeToolId,
    overlayEffectExecution,
    insideHostWindow,
    outsideVariants,
    overAvatarRange,
    overCompactZone,
    publishState,
    rangeVariants,
    updateOverlayPosition,
  ]);

  useEffect(() => { interactionCallbackRef.current = onInteraction; }, [onInteraction]);
  useEffect(() => {
    toolLabelCallbackRef.current = getToolLabel;
    publishState();
  }, [getToolLabel, publishState]);
  useEffect(() => {
    if (stateCallbackRef.current === onStateChange) return;
    stateCallbackRef.current = onStateChange;
    lastStateKeyRef.current = '';
    publishState();
  }, [onStateChange, publishState]);
  useEffect(() => { deactivateCallbackRef.current = onDeactivate; }, [onDeactivate]);

  useEffect(() => {
    if (
      (composerHidden || composerDisabled || interactionDisabled)
      && (activeToolIdRef.current !== null || sessionRef.current !== null)
    ) clearTool();
  }, [activeToolId, clearTool, composerDisabled, composerHidden, interactionDisabled]);

  useEffect(() => {
    if (!deactivationKey || deactivationKey === lastDeactivationKeyRef.current) return;
    lastDeactivationKeyRef.current = deactivationKey;
    clearTool();
  }, [clearTool, deactivationKey]);

  useEffect(() => {
    const deactivate = () => clearTool();
    window.addEventListener('neko:deactivate-avatar-tool', deactivate);
    return () => window.removeEventListener('neko:deactivate-avatar-tool', deactivate);
  }, [clearTool]);

  useEffect(() => {
    const publishInactive = () => publishInactiveState();
    const republishCurrent = () => {
      lastStateKeyRef.current = '';
      publishState();
    };
    window.addEventListener('beforeunload', publishInactive);
    window.addEventListener('pagehide', publishInactive);
    window.addEventListener('pageshow', republishCurrent);
    return () => {
      window.removeEventListener('beforeunload', publishInactive);
      window.removeEventListener('pagehide', publishInactive);
      window.removeEventListener('pageshow', republishCurrent);
      publishInactiveState();
      disposeSession();
    };
  }, [disposeSession, publishInactiveState, publishState]);

  const visualModel = buildAvatarToolVisualModel({
    activeTool,
    activeToolId,
    effectiveVariant,
    avatarRangeVariant,
    withinAvatarRange,
    overlayRef,
    overlayActive,
    overlayCompact,
    overlayEffectExecution,
    roundChoiceAvatarGestureState,
    transientEffects,
  });

  return {
    activeToolId,
    activeTool,
    effectiveVariant,
    selectTool,
    clearTool,
    roundChoiceRound,
    setInsideHostWindow: (value: boolean) => {
      insideHostRef.current = value;
      setInsideHostWindow(value);
    },
    visualModel,
  };
}
