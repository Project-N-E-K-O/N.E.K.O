import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import {
  BaseEdge,
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  getBezierPath,
  getSmoothStepPath,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  type Connection,
  type ConnectionLineComponentProps,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeHandle,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import { i18n } from './i18n';
import type { AvatarToolImageDraft } from './avatar-tools/avatarToolEditorModel';
import {
  avatarToolConnectionSideFromHandleId,
  createAvatarToolInteractionDraft,
  createAvatarToolInteractionLinkId,
  findAvailableAvatarToolInteractionPosition,
  getAvatarToolInteractionOrdinal,
  type AvatarToolInteractionDraft,
} from './avatar-tools/avatarToolInteractionEditorModel';
import {
  AvatarToolInteractionEditorProvider,
  useAvatarToolInteractionEditor,
} from './avatar-tools/AvatarToolInteractionEditorContext';
import {
  avatarToolEdgePath,
  planAvatarToolEdgeRoutes,
  type AvatarToolEdgeLineStyle,
  type AvatarToolPlannedEdgeRoute,
  type AvatarToolRouteEdge,
  type AvatarToolRouteNodeBox,
} from './avatar-tools/avatarToolEdgeRouter';

type AvatarToolInteractionNodeData = {
  title: string;
  kind: AvatarToolInteractionDraft['kind'];
  hasError: boolean;
  localeRevision: number;
  rows: Array<{ label: string; value: string }>;
};

type AvatarToolFlowNode = Node<AvatarToolInteractionNodeData, 'avatar-tool-interaction'>;
type AvatarToolInitialImageNodeData = {
  image: AvatarToolImageDraft | null;
  imageNumber: number;
  hasError: boolean;
  localeRevision: number;
};
type AvatarToolInitialImageFlowNode = Node<
  AvatarToolInitialImageNodeData,
  'avatar-tool-initial-image'
>;
type AvatarToolCanvasNode = AvatarToolFlowNode | AvatarToolInitialImageFlowNode;

type AvatarToolFloatingEdgeData = {
  initial: boolean;
  hasError: boolean;
  path: string;
  lineStyle: AvatarToolEdgeLineStyle;
};
type AvatarToolCanvasEdge = Edge<AvatarToolFloatingEdgeData, 'avatar-tool-floating'>;
type AvatarToolRouteSeed = AvatarToolRouteEdge & { initial: boolean };
type AvatarToolRoutePlanCache = {
  topologyKey: string;
  boxes: ReadonlyMap<string, AvatarToolRouteNodeBox>;
  routes: ReadonlyMap<string, AvatarToolPlannedEdgeRoute>;
};

const AVATAR_TOOL_INITIAL_IMAGE_NODE_ID = 'avatar-tool-initial-image';
const AVATAR_TOOL_INITIAL_CONNECTION_EDGE_PREFIX = 'avatar-tool-initial-connection:';
const AVATAR_TOOL_OVERVIEW_PREFERENCE_KEY = 'neko.avatarToolEditor.overview.v1';
const AVATAR_TOOL_EDGE_STYLE_PREFERENCE_KEY = 'neko.avatarToolEditor.edgeStyle.v1';
const AVATAR_TOOL_INITIAL_NODE_SIZE = { width: 202, height: 82 } as const;
const AVATAR_TOOL_INTERACTION_NODE_SIZE = { width: 228, height: 104 } as const;
const AVATAR_TOOL_OVERVIEW_SIZE = { width: 202, height: 126 } as const;
const AVATAR_TOOL_EDGE_VISUALS = {
  error: {
    markerEnd: { type: MarkerType.ArrowClosed, width: 17, height: 17, color: '#b42318' },
    style: { stroke: '#b42318' },
  },
  selected: {
    markerEnd: { type: MarkerType.ArrowClosed, width: 17, height: 17, color: '#168dcc' },
    style: { stroke: '#168dcc' },
  },
  initial: {
    markerEnd: { type: MarkerType.ArrowClosed, width: 17, height: 17, color: '#3b9a7f' },
    style: { stroke: '#3b9a7f' },
  },
  normal: {
    markerEnd: { type: MarkerType.ArrowClosed, width: 17, height: 17, color: '#5d9fc2' },
    style: { stroke: '#5d9fc2' },
  },
} as const;
const AVATAR_TOOL_CONNECTION_POSITIONS = [
  Position.Top,
  Position.Right,
  Position.Bottom,
  Position.Left,
] as const;
type AvatarToolOverviewPosition = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';

function avatarToolConnectionHandles(size: { width: number; height: number }): NodeHandle[] {
  return [
    { id: 'edge-top', type: 'source', position: Position.Top, x: 0, y: 0, width: size.width, height: 0 },
    { id: 'edge-right', type: 'source', position: Position.Right, x: size.width, y: 0, width: 0, height: size.height },
    { id: 'edge-bottom', type: 'source', position: Position.Bottom, x: 0, y: size.height, width: size.width, height: 0 },
    { id: 'edge-left', type: 'source', position: Position.Left, x: 0, y: 0, width: 0, height: size.height },
  ];
}

function avatarToolConnectionBoundaryLabel(position: Position): string {
  if (position === Position.Top) {
    return i18n('chat.avatarToolWorkspaceHandleTop', 'Top edge for connections');
  }
  if (position === Position.Right) {
    return i18n('chat.avatarToolWorkspaceHandleRight', 'Right edge for connections');
  }
  if (position === Position.Bottom) {
    return i18n('chat.avatarToolWorkspaceHandleBottom', 'Bottom edge for connections');
  }
  return i18n('chat.avatarToolWorkspaceHandleLeft', 'Left edge for connections');
}

const AVATAR_TOOL_INITIAL_NODE_HANDLES = avatarToolConnectionHandles(AVATAR_TOOL_INITIAL_NODE_SIZE);
const AVATAR_TOOL_INTERACTION_NODE_HANDLES = avatarToolConnectionHandles(AVATAR_TOOL_INTERACTION_NODE_SIZE);

function AvatarToolConnectionBoundaries({ sourceOnly = false }: { sourceOnly?: boolean }) {
  return AVATAR_TOOL_CONNECTION_POSITIONS.map(position => (
    <Handle
      key={position}
      id={`edge-${position}`}
      className={`avatar-tool-connection-boundary is-${position}`}
      type="source"
      position={position}
      isConnectableStart
      isConnectableEnd={!sourceOnly}
      aria-label={avatarToolConnectionBoundaryLabel(position)}
    />
  ));
}

type AvatarToolConnectionPreviewPathOptions = Pick<
  ConnectionLineComponentProps,
  'fromX' | 'fromY' | 'fromPosition' | 'toX' | 'toY' | 'toPosition'
>;

export function avatarToolConnectionPreviewPath(
  lineStyle: AvatarToolEdgeLineStyle,
  {
    fromX,
    fromY,
    fromPosition,
    toX,
    toY,
    toPosition,
  }: AvatarToolConnectionPreviewPathOptions,
): string {
  return lineStyle === 'curved'
    ? getBezierPath({
      sourceX: fromX,
      sourceY: fromY,
      sourcePosition: fromPosition,
      targetX: toX,
      targetY: toY,
      targetPosition: toPosition,
    })[0]
    : getSmoothStepPath({
      sourceX: fromX,
      sourceY: fromY,
      sourcePosition: fromPosition,
      targetX: toX,
      targetY: toY,
      targetPosition: toPosition,
      borderRadius: 10,
      offset: 30,
    })[0];
}

function AvatarToolConnectionPreview({
  lineStyle,
  connectionStatus,
  ...positions
}: ConnectionLineComponentProps & { lineStyle: AvatarToolEdgeLineStyle }) {
  const path = avatarToolConnectionPreviewPath(lineStyle, positions);
  const status = connectionStatus ?? 'pending';

  return (
    <g
      className={`avatar-tool-connection-preview is-${status}`}
      data-line-style={lineStyle}
    >
      <path className="avatar-tool-connection-preview-halo" d={path} />
      <path className="avatar-tool-connection-preview-path" d={path} />
    </g>
  );
}

const AvatarToolOrthogonalConnectionPreview = memo(function AvatarToolOrthogonalConnectionPreview(
  props: ConnectionLineComponentProps,
) {
  return <AvatarToolConnectionPreview {...props} lineStyle="orthogonal" />;
});

const AvatarToolCurvedConnectionPreview = memo(function AvatarToolCurvedConnectionPreview(
  props: ConnectionLineComponentProps,
) {
  return <AvatarToolConnectionPreview {...props} lineStyle="curved" />;
});

function InteractionIcon({ kind }: { kind: AvatarToolInteractionDraft['kind'] }) {
  return kind === 'mouse-click' ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5.2 3.8 18.6 12l-6.1 1.2-3.4 5.2L5.2 3.8Z" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="7.4" />
      <path d="M12 7.8v4.7l3.1 2" />
    </svg>
  );
}

function EdgeLineStyleIcon({ style }: { style: AvatarToolEdgeLineStyle }) {
  return (
    <svg viewBox="0 0 24 16" aria-hidden="true">
      <path d={style === 'orthogonal' ? 'M2 13h7V3h13' : 'M2 13C8 13 8 3 14 3h8'} />
    </svg>
  );
}

const AvatarToolInteractionNode = memo(function AvatarToolInteractionNode({
  id,
  data,
}: NodeProps<AvatarToolFlowNode>) {
  return (
    <div
      className={`avatar-tool-interaction-node is-${data.kind}${data.hasError ? ' has-error' : ''}`}
      data-avatar-tool-interaction-id={id}
    >
      <AvatarToolConnectionBoundaries />
      <div className="avatar-tool-interaction-node-heading">
        <span className="avatar-tool-interaction-node-icon" aria-hidden="true">
          <InteractionIcon kind={data.kind} />
        </span>
        <strong>{data.title}</strong>
      </div>
      <div className="avatar-tool-interaction-node-summary">
        {data.rows.map(row => (
          <div key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
      {data.hasError ? (
        <span className="avatar-tool-interaction-node-error" aria-label={i18n(
          'chat.avatarToolInteractionNodeHasError',
          'This interaction needs attention',
        )}>!</span>
      ) : null}
    </div>
  );
}, (previous, next) => (
  previous.id === next.id
  && previous.data.title === next.data.title
  && previous.data.kind === next.data.kind
  && previous.data.hasError === next.data.hasError
  && previous.data.localeRevision === next.data.localeRevision
  && previous.data.rows.length === next.data.rows.length
  && previous.data.rows.every((row, index) => (
    row.label === next.data.rows[index]?.label
    && row.value === next.data.rows[index]?.value
  ))
));

function AvatarToolInitialImagePreview({ image }: { image: AvatarToolImageDraft | null }) {
  const [objectUrl, setObjectUrl] = useState('');

  useEffect(() => {
    if (!image?.image || typeof URL.createObjectURL !== 'function') {
      setObjectUrl('');
      return undefined;
    }
    const nextUrl = URL.createObjectURL(image.image);
    setObjectUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [image?.image]);

  const source = objectUrl || image?.imageUrl;
  return source
    ? <img src={source} alt="" />
    : <span aria-hidden="true">＋</span>;
}

const AvatarToolInitialImageNode = memo(function AvatarToolInitialImageNode({
  data,
}: NodeProps<AvatarToolInitialImageFlowNode>) {
  const imageLabel = data.image
    ? data.image.name?.trim() || i18n('chat.avatarToolInteractionImageNumber', 'Tool image {{number}}', {
      number: String(data.imageNumber),
    })
    : i18n('chat.avatarToolInitialImageMissing', 'No initial image selected');
  return (
    <div className={`avatar-tool-initial-image-node${data.hasError ? ' has-error' : ''}`}>
      <AvatarToolConnectionBoundaries sourceOnly />
      <span className="avatar-tool-initial-image-preview">
        <AvatarToolInitialImagePreview image={data.image} />
      </span>
      <span className="avatar-tool-initial-image-copy">
        <small>{i18n('chat.avatarToolInitialImageNode', 'Initial image')}</small>
        <strong>{imageLabel}</strong>
        <span>{i18n(
          'chat.avatarToolInitialImageNodeHint',
          'The interaction flow starts from this image',
        )}</span>
      </span>
    </div>
  );
}, (previous, next) => (
  previous.data.image === next.data.image
  && previous.data.imageNumber === next.data.imageNumber
  && previous.data.hasError === next.data.hasError
  && previous.data.localeRevision === next.data.localeRevision
));

const AvatarToolFloatingEdge = memo(function AvatarToolFloatingEdge({
  id,
  data,
  markerEnd,
  style,
  interactionWidth,
  selected,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
}: EdgeProps<AvatarToolCanvasEdge>) {
  const path = data?.path || (data?.lineStyle === 'curved'
    ? getBezierPath({
      sourceX,
      sourceY,
      sourcePosition,
      targetX,
      targetY,
      targetPosition,
    })[0]
    : getSmoothStepPath({
      sourceX,
      sourceY,
      sourcePosition,
      targetX,
      targetY,
      targetPosition,
      borderRadius: 10,
      offset: 30,
    })[0]);

  return (
    <>
      <path
        className={`avatar-tool-edge-feedback${selected ? ' is-selected' : ''}`}
        d={path}
        style={{ stroke: style?.stroke }}
      />
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={style}
        interactionWidth={interactionWidth}
      />
    </>
  );
}, (previous, next) => (
  previous.id === next.id
  && previous.data?.path === next.data?.path
  && previous.data?.lineStyle === next.data?.lineStyle
  && previous.data?.hasError === next.data?.hasError
  && previous.selected === next.selected
  && previous.sourceX === next.sourceX
  && previous.sourceY === next.sourceY
  && previous.sourcePosition === next.sourcePosition
  && previous.targetX === next.targetX
  && previous.targetY === next.targetY
  && previous.targetPosition === next.targetPosition
  && previous.interactionWidth === next.interactionWidth
  && previous.style?.stroke === next.style?.stroke
  && previous.markerEnd === next.markerEnd
));

const avatarToolNodeTypes = {
  'avatar-tool-interaction': AvatarToolInteractionNode,
  'avatar-tool-initial-image': AvatarToolInitialImageNode,
};

const avatarToolEdgeTypes = {
  'avatar-tool-floating': AvatarToolFloatingEdge,
};

function interactionTitle(
  state: ReturnType<typeof useAvatarToolInteractionEditor>['state'],
  item: AvatarToolInteractionDraft,
): string {
  const number = getAvatarToolInteractionOrdinal(state, item.id);
  const defaultTitle = item.kind === 'mouse-click'
    ? i18n('chat.avatarToolInteractionClickNumber', 'Mouse click {{number}}', { number: String(number) })
    : i18n('chat.avatarToolInteractionDelayNumber', 'Delayed switch {{number}}', { number: String(number) });
  return item.name?.trim() || defaultTitle;
}

function imageActionSummary(
  action: { kind: 'keep' } | { kind: 'show'; imageId: `img-${string}` },
  images: readonly AvatarToolImageDraft[],
): string {
  if (action.kind === 'keep') return i18n('chat.avatarToolInteractionKeepImage', 'Keep image');
  const number = images.findIndex(image => image.id === action.imageId) + 1;
  return number > 0
    ? images[number - 1]?.name?.trim() || i18n(
      'chat.avatarToolInteractionImageNumber',
      'Tool image {{number}}',
      { number: String(number) },
    )
    : i18n('chat.avatarToolInteractionMissingImage', 'Missing image');
}

function initialConnectionEdgeId(interactionId: string): string {
  return `${AVATAR_TOOL_INITIAL_CONNECTION_EDGE_PREFIX}${interactionId}`;
}

function initialConnectionTargetFromEdgeId(edgeId: string): `ix-${string}` | null {
  return edgeId.startsWith(AVATAR_TOOL_INITIAL_CONNECTION_EDGE_PREFIX)
    ? edgeId.slice(AVATAR_TOOL_INITIAL_CONNECTION_EDGE_PREFIX.length) as `ix-${string}`
    : null;
}

function readOverviewPreference(): {
  visible?: boolean;
  position: AvatarToolOverviewPosition;
} {
  const fallback = { position: 'bottom-right' as const };
  try {
    const value = globalThis.localStorage?.getItem(AVATAR_TOOL_OVERVIEW_PREFERENCE_KEY);
    if (!value) return fallback;
    const parsed = JSON.parse(value) as { visible?: unknown; position?: unknown };
    const positions: AvatarToolOverviewPosition[] = [
      'top-left',
      'top-right',
      'bottom-left',
      'bottom-right',
    ];
    return {
      visible: typeof parsed.visible === 'boolean' ? parsed.visible : undefined,
      position: positions.includes(parsed.position as AvatarToolOverviewPosition)
        ? parsed.position as AvatarToolOverviewPosition
        : fallback.position,
    };
  } catch {
    return fallback;
  }
}

function saveOverviewPreference(visible: boolean, position: AvatarToolOverviewPosition): void {
  try {
    globalThis.localStorage?.setItem(
      AVATAR_TOOL_OVERVIEW_PREFERENCE_KEY,
      JSON.stringify({ visible, position }),
    );
  } catch {
    // The canvas still works when browser storage is unavailable.
  }
}

function readEdgeLineStylePreference(): AvatarToolEdgeLineStyle {
  try {
    return globalThis.localStorage?.getItem(AVATAR_TOOL_EDGE_STYLE_PREFERENCE_KEY) === 'curved'
      ? 'curved'
      : 'orthogonal';
  } catch {
    return 'orthogonal';
  }
}

function saveEdgeLineStylePreference(style: AvatarToolEdgeLineStyle): void {
  try {
    globalThis.localStorage?.setItem(AVATAR_TOOL_EDGE_STYLE_PREFERENCE_KEY, style);
  } catch {
    // The canvas still works when browser storage is unavailable.
  }
}

function OverviewMapIcon() {
  return (
    <svg className="avatar-tool-overview-map-icon" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.5" y="3" width="15" height="14" rx="2.5" />
      <path d="m5.5 12 3-3 2.2 2.1 3.8-4" />
      <circle cx="5.5" cy="7" r="1" />
    </svg>
  );
}

function OverviewPositionIcon({ position }: { position: AvatarToolOverviewPosition }) {
  return (
    <span className={`avatar-tool-overview-position-icon is-${position}`} aria-hidden="true">
      <span />
    </span>
  );
}

export function AvatarToolInteractionCanvas() {
  const {
    state,
    dispatch,
    issues,
    images,
    initialImageId,
  } = useAvatarToolInteractionEditor();
  const [localeRevision, setLocaleRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const [flow, setFlow] = useState<ReactFlowInstance<AvatarToolCanvasNode, AvatarToolCanvasEdge> | null>(null);
  const initialOverviewPreference = useMemo(readOverviewPreference, []);
  const [overviewVisible, setOverviewVisible] = useState(initialOverviewPreference.visible ?? false);
  const [overviewExplicit, setOverviewExplicit] = useState(initialOverviewPreference.visible !== undefined);
  const [overviewPosition, setOverviewPosition] = useState<AvatarToolOverviewPosition>(
    initialOverviewPreference.position,
  );
  const [overviewPositionMenuOpen, setOverviewPositionMenuOpen] = useState(false);
  const [edgeLineStyle, setEdgeLineStyle] = useState<AvatarToolEdgeLineStyle>(
    readEdgeLineStylePreference,
  );
  const [draggingNodeIds, setDraggingNodeIds] = useState<ReadonlySet<string>>(() => new Set());
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const overviewPositionTriggerRef = useRef<HTMLButtonElement | null>(null);
  const overviewOpenButtonRef = useRef<HTMLButtonElement | null>(null);
  const routePlanCacheRef = useRef<AvatarToolRoutePlanCache | null>(null);
  useLayoutEffect(() => {
    const refreshLocalizedContent = () => setLocaleRevision(revision => revision + 1);
    window.addEventListener('localechange', refreshLocalizedContent);
    return () => window.removeEventListener('localechange', refreshLocalizedContent);
  }, []);
  const issueInteractionIds = useMemo(
    () => new Set(issues.flatMap(issue => issue.interactionId ? [issue.interactionId] : [])),
    [issues],
  );
  const issueLinkIds = useMemo(
    () => new Set(issues.flatMap(issue => issue.linkId ? [issue.linkId] : [])),
    [issues],
  );
  const initialImage = images.find(image => image.id === initialImageId) ?? null;
  const initialImageNumber = initialImage
    ? images.findIndex(image => image.id === initialImage.id) + 1
    : 0;
  const nodes = useMemo<AvatarToolCanvasNode[]>(() => [
    {
      id: AVATAR_TOOL_INITIAL_IMAGE_NODE_ID,
      type: 'avatar-tool-initial-image',
      position: state.initialImagePosition,
      ...AVATAR_TOOL_INITIAL_NODE_SIZE,
      measured: AVATAR_TOOL_INITIAL_NODE_SIZE,
      handles: AVATAR_TOOL_INITIAL_NODE_HANDLES,
      dragging: draggingNodeIds.has(AVATAR_TOOL_INITIAL_IMAGE_NODE_ID),
      deletable: false,
      selectable: false,
      ariaLabel: i18n('chat.avatarToolInitialImageNode', 'Initial image'),
      data: {
        image: initialImage,
        imageNumber: initialImageNumber,
        hasError: issues.some(issue => issue.code === 'initial-connection-required'),
        localeRevision,
      },
    },
    ...state.items.map((item): AvatarToolFlowNode => ({
      id: item.id,
      type: 'avatar-tool-interaction',
      position: item.position,
      ...AVATAR_TOOL_INTERACTION_NODE_SIZE,
      measured: AVATAR_TOOL_INTERACTION_NODE_SIZE,
      handles: AVATAR_TOOL_INTERACTION_NODE_HANDLES,
      dragging: draggingNodeIds.has(item.id),
      selected: state.selectedInteractionId === item.id,
      ariaLabel: interactionTitle(state, item),
      data: {
        title: interactionTitle(state, item),
        kind: item.kind,
        hasError: issueInteractionIds.has(item.id),
        localeRevision,
        rows: item.kind === 'mouse-click'
          ? [
            {
              label: i18n('chat.avatarToolInteractionPressTiming', 'Press'),
              value: imageActionSummary(item.press, images),
            },
            {
              label: i18n('chat.avatarToolInteractionReleaseTiming', 'Release'),
              value: imageActionSummary(item.release, images),
            },
          ]
          : [
            {
              label: i18n('chat.avatarToolInteractionWaitTime', 'Wait'),
              value: item.delayMs.trim()
                ? i18n('chat.avatarToolInteractionMillisecondsValue', '{{count}} ms', { count: item.delayMs })
                : i18n('chat.avatarToolInteractionNotSet', 'Not set'),
            },
            {
              label: i18n('chat.avatarToolInteractionThenShow', 'Switch to'),
              value: item.complete
                ? imageActionSummary(item.complete, images)
                : i18n('chat.avatarToolInteractionNotSet', 'Not set'),
            },
          ],
      },
    })),
  ], [
    images,
    draggingNodeIds,
    initialImage,
    initialImageNumber,
    issueInteractionIds,
    issues,
    localeRevision,
    state,
  ]);

  const routeSeeds = useMemo<AvatarToolRouteSeed[]>(() => [
    ...state.initialImageTargetIds.map(interactionId => ({
      id: initialConnectionEdgeId(interactionId),
      source: AVATAR_TOOL_INITIAL_IMAGE_NODE_ID,
      target: interactionId,
      sourcePosition: state.initialImageLinkSides[interactionId]?.sourceSide as Position | undefined,
      targetPosition: state.initialImageLinkSides[interactionId]?.targetSide as Position | undefined,
      initial: true,
    })),
    ...state.links.map(link => ({
      id: link.id,
      source: link.from,
      target: link.to,
      sourcePosition: link.sourceSide as Position | undefined,
      targetPosition: link.targetSide as Position | undefined,
      initial: false,
    })),
  ], [state.initialImageLinkSides, state.initialImageTargetIds, state.links]);
  const routingTopologyKey = useMemo(() => routeSeeds.map(seed => [
    seed.id,
    seed.source,
    seed.target,
    seed.sourcePosition ?? '',
    seed.targetPosition ?? '',
  ].join(':')).join('|'), [routeSeeds]);
  const routingGeometryKey = [
    `${AVATAR_TOOL_INITIAL_IMAGE_NODE_ID}:${state.initialImagePosition.x}:${state.initialImagePosition.y}`,
    ...state.items.map(item => `${item.id}:${item.position.x}:${item.position.y}`),
  ].join('|');
  const positionById = useMemo(() => new Map<string, AvatarToolRouteNodeBox>([
    [AVATAR_TOOL_INITIAL_IMAGE_NODE_ID, {
      ...state.initialImagePosition,
      ...AVATAR_TOOL_INITIAL_NODE_SIZE,
    }],
    ...state.items.map(item => [item.id, {
      ...item.position,
      ...AVATAR_TOOL_INTERACTION_NODE_SIZE,
    }] as const),
  ]), [routingGeometryKey]);
  const plannedRoutes = useMemo(() => {
    const previous = routePlanCacheRef.current;
    const changedNodeIds = new Set<string>();
    if (previous?.topologyKey === routingTopologyKey) {
      positionById.forEach((box, nodeId) => {
        const oldBox = previous.boxes.get(nodeId);
        if (
          !oldBox
          || box.x !== oldBox.x
          || box.y !== oldBox.y
          || box.width !== oldBox.width
          || box.height !== oldBox.height
        ) changedNodeIds.add(nodeId);
      });
    }
    const routes = planAvatarToolEdgeRoutes(
      routeSeeds,
      positionById,
      previous?.topologyKey === routingTopologyKey
        ? {
          previousRoutes: previous.routes,
          previousBoxes: previous.boxes,
          changedNodeIds,
        }
        : undefined,
    );
    routePlanCacheRef.current = {
      topologyKey: routingTopologyKey,
      boxes: positionById,
      routes,
    };
    return routes;
  }, [positionById, routeSeeds, routingGeometryKey, routingTopologyKey]);

  const edges = useMemo<AvatarToolCanvasEdge[]>(() => {
    return routeSeeds.map((seed) => {
      const selected = seed.initial
        ? state.selectedInitialLinkTargetId === seed.target
        : state.selectedLinkId === seed.id;
      const hasError = !seed.initial && issueLinkIds.has(seed.id as `link-${string}`);
      const route = plannedRoutes.get(seed.id);
      const visual = hasError
        ? AVATAR_TOOL_EDGE_VISUALS.error
        : selected
          ? AVATAR_TOOL_EDGE_VISUALS.selected
          : seed.initial
            ? AVATAR_TOOL_EDGE_VISUALS.initial
            : AVATAR_TOOL_EDGE_VISUALS.normal;

      return {
        id: seed.id,
        source: seed.source,
        target: seed.target,
        sourceHandle: route ? `edge-${route.sourcePosition}` : undefined,
        targetHandle: route ? `edge-${route.targetPosition}` : undefined,
        type: 'avatar-tool-floating',
        selected,
        className: `${seed.initial ? 'is-initial-link' : ''}${hasError ? ' has-error' : ''}`.trim() || undefined,
        markerEnd: visual.markerEnd,
        style: visual.style,
        data: {
          initial: seed.initial,
          hasError,
          lineStyle: edgeLineStyle,
          path: route
            ? avatarToolEdgePath(route, edgeLineStyle, seed.source === seed.target)
            : '',
        },
        interactionWidth: 12,
      };
    });
  }, [
    edgeLineStyle,
    issueLinkIds,
    plannedRoutes,
    routeSeeds,
    state.selectedInitialLinkTargetId,
    state.selectedLinkId,
  ]);

  useEffect(() => {
    if (!overviewExplicit && state.items.length >= 4) setOverviewVisible(true);
  }, [overviewExplicit, state.items.length]);

  const setOverview = useCallback((visible: boolean) => {
    setOverviewVisible(visible);
    setOverviewPositionMenuOpen(false);
    setOverviewExplicit(true);
    saveOverviewPreference(visible, overviewPosition);
    window.requestAnimationFrame(() => {
      if (visible) overviewPositionTriggerRef.current?.focus();
      else overviewOpenButtonRef.current?.focus();
    });
  }, [overviewPosition]);

  const moveOverview = useCallback((position: AvatarToolOverviewPosition) => {
    setOverviewPosition(position);
    setOverviewPositionMenuOpen(false);
    setOverviewExplicit(true);
    saveOverviewPreference(overviewVisible, position);
    overviewPositionTriggerRef.current?.focus();
  }, [overviewVisible]);

  const closeOverviewPositionMenu = useCallback(() => {
    setOverviewPositionMenuOpen(false);
    overviewPositionTriggerRef.current?.focus();
  }, []);

  const handleOverviewKeyDown = useCallback((event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape' || !overviewPositionMenuOpen) return;
    event.preventDefault();
    event.stopPropagation();
    closeOverviewPositionMenu();
  }, [closeOverviewPositionMenu, overviewPositionMenuOpen]);

  const addInteraction = useCallback((kind: AvatarToolInteractionDraft['kind']) => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    const screenPosition = bounds
      ? { x: bounds.left + bounds.width * 0.5, y: bounds.top + bounds.height * 0.46 }
      : { x: 360, y: 280 };
    const preferredPosition = flow
      ? flow.screenToFlowPosition(screenPosition)
      : { x: 140 + state.items.length * 36, y: 140 + state.items.length * 28 };
    const position = findAvailableAvatarToolInteractionPosition(preferredPosition, [
      ...state.items,
      { position: state.initialImagePosition },
    ]);
    dispatch({ type: 'add', interaction: createAvatarToolInteractionDraft(kind, position) });
  }, [dispatch, flow, state.initialImagePosition, state.items]);

  const onNodesChange = useCallback((changes: NodeChange<AvatarToolCanvasNode>[]) => {
    const draggingChanges = changes.filter((change): change is Extract<
      NodeChange<AvatarToolCanvasNode>,
      { type: 'position' }
    > => change.type === 'position' && typeof change.dragging === 'boolean');
    const removedNodeIds = changes
      .filter(change => change.type === 'remove')
      .map(change => change.id);
    if (draggingChanges.length || removedNodeIds.length) {
      setDraggingNodeIds((current) => {
        const next = new Set(current);
        draggingChanges.forEach((change) => {
          if (change.dragging) next.add(change.id);
          else next.delete(change.id);
        });
        removedNodeIds.forEach(id => next.delete(id));
        if (next.size === current.size && [...next].every(id => current.has(id))) return current;
        return next;
      });
    }
    changes.forEach((change) => {
      if (change.type === 'position' && change.position) {
        if (change.id === AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
          dispatch({ type: 'move-initial-image', position: change.position });
        } else {
          dispatch({ type: 'move', interactionId: change.id as `ix-${string}`, position: change.position });
        }
      } else if (change.type === 'select') {
        if (change.selected && change.id !== AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
          dispatch({ type: 'select-interaction', interactionId: change.id as `ix-${string}` });
        }
      } else if (change.type === 'remove' && change.id !== AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
        dispatch({ type: 'remove-interaction', interactionId: change.id as `ix-${string}` });
      }
    });
  }, [dispatch]);

  const isValidConnection = useCallback((connection: Edge | Connection) => {
    if (!connection.source || !connection.target) return false;
    if (connection.target === AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) return false;
    if (connection.source === AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
      return connection.target !== AVATAR_TOOL_INITIAL_IMAGE_NODE_ID
        && state.items.some(item => item.id === connection.target)
        && !state.initialImageTargetIds.includes(connection.target as `ix-${string}`);
    }
    if (
      !state.items.some(item => item.id === connection.source)
      || !state.items.some(item => item.id === connection.target)
    ) return false;
    return !state.links.some(link => link.from === connection.source && link.to === connection.target);
  }, [state.initialImageTargetIds, state.items, state.links]);

  const ariaLabelConfig = {
    'node.a11yDescription.default': i18n(
      'chat.avatarToolWorkspaceNodeA11y',
      'Press Enter to select this interaction. Use the arrow keys to move it.',
    ),
    'edge.a11yDescription.default': i18n(
      'chat.avatarToolWorkspaceEdgeA11y',
      'Press Enter to select this connection. Press Delete to remove it.',
    ),
    'controls.ariaLabel': i18n('chat.avatarToolWorkspaceControls', 'Canvas controls'),
    'controls.zoomIn.ariaLabel': i18n('chat.avatarToolWorkspaceZoomIn', 'Zoom in'),
    'controls.zoomOut.ariaLabel': i18n('chat.avatarToolWorkspaceZoomOut', 'Zoom out'),
    'controls.fitView.ariaLabel': i18n('chat.avatarToolWorkspaceFitView', 'Fit view'),
    'minimap.ariaLabel': i18n('chat.avatarToolWorkspaceMiniMap', 'Interaction overview'),
    'handle.ariaLabel': i18n('chat.avatarToolWorkspaceHandle', 'Node edge for connections'),
  };

  return (
    <div ref={canvasRef} className="avatar-tool-workspace-canvas" data-testid="avatar-tool-workspace-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={(changes) => changes.forEach((change) => {
          if (change.type === 'select') {
            if (!change.selected) return;
            const initialTarget = initialConnectionTargetFromEdgeId(change.id);
            if (initialTarget) {
              dispatch({ type: 'select-initial-link', interactionId: initialTarget });
            } else {
              dispatch({ type: 'select-link', linkId: change.id as `link-${string}` });
            }
          } else if (change.type === 'remove') {
            const initialTarget = initialConnectionTargetFromEdgeId(change.id);
            if (initialTarget) {
              dispatch({ type: 'remove-initial-link', interactionId: initialTarget });
            } else {
              dispatch({ type: 'remove-link', linkId: change.id as `link-${string}` });
            }
          }
        })}
        onConnect={(connection) => {
          if (!connection.source || !connection.target || !isValidConnection(connection)) return;
          const sourceSide = avatarToolConnectionSideFromHandleId(connection.sourceHandle);
          const targetSide = avatarToolConnectionSideFromHandleId(connection.targetHandle);
          if (connection.source === AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
            dispatch({
              type: 'connect-initial-image',
              interactionId: connection.target as `ix-${string}`,
              sourceSide,
              targetSide,
            });
            return;
          }
          dispatch({
            type: 'connect',
            link: {
              id: createAvatarToolInteractionLinkId(),
              from: connection.source as `ix-${string}`,
              to: connection.target as `ix-${string}`,
              sourceSide,
              targetSide,
            },
          });
        }}
        isValidConnection={isValidConnection}
        connectionLineComponent={edgeLineStyle === 'curved'
          ? AvatarToolCurvedConnectionPreview
          : AvatarToolOrthogonalConnectionPreview}
        onInit={(instance) => {
          setFlow(instance);
          setReady(true);
        }}
        onPaneClick={() => {
          dispatch({ type: 'select-interaction', interactionId: null });
          dispatch({ type: 'select-link', linkId: null });
          dispatch({ type: 'select-initial-link', interactionId: null });
        }}
        onNodeClick={(_, node) => {
          if (node.id !== AVATAR_TOOL_INITIAL_IMAGE_NODE_ID) {
            dispatch({ type: 'select-interaction', interactionId: node.id as `ix-${string}` });
          }
        }}
        onEdgeClick={(_, edge) => {
          const initialTarget = initialConnectionTargetFromEdgeId(edge.id);
          if (initialTarget) {
            dispatch({ type: 'select-initial-link', interactionId: initialTarget });
          } else {
            dispatch({ type: 'select-link', linkId: edge.id as `link-${string}` });
          }
        }}
        fitView
        fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
        minZoom={0.25}
        maxZoom={1.75}
        panOnScroll
        zoomOnDoubleClick={false}
        connectionMode={ConnectionMode.Loose}
        nodeTypes={avatarToolNodeTypes}
        edgeTypes={avatarToolEdgeTypes}
        deleteKeyCode={['Backspace', 'Delete']}
        selectionOnDrag
        selectNodesOnDrag={false}
        nodesFocusable
        edgesFocusable
        autoPanOnNodeFocus
        disableKeyboardA11y={false}
        ariaLabelConfig={ariaLabelConfig}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} />
        <Controls showInteractive={false} />
        <Panel
          className={`avatar-tool-overview-dock is-${overviewVisible ? 'open' : 'collapsed'}`}
          position={overviewPosition}
          onKeyDown={handleOverviewKeyDown}
        >
          {overviewVisible ? (
            <>
              <MiniMap
                className="avatar-tool-overview-map"
                position={overviewPosition}
                style={AVATAR_TOOL_OVERVIEW_SIZE}
                pannable
                zoomable
                nodeColor={node => node.id === AVATAR_TOOL_INITIAL_IMAGE_NODE_ID ? '#55a98e' : '#68a9cd'}
              />
              <div className="avatar-tool-overview-toolbar">
                <button
                  ref={overviewPositionTriggerRef}
                  className="avatar-tool-overview-position-trigger"
                  type="button"
                  aria-expanded={overviewPositionMenuOpen}
                  aria-controls="avatar-tool-overview-position-options"
                  aria-label={i18n('chat.avatarToolOverviewPosition', 'Overview position')}
                  title={i18n('chat.avatarToolOverviewPosition', 'Overview position')}
                  onClick={() => setOverviewPositionMenuOpen(open => !open)}
                >
                  <OverviewPositionIcon position={overviewPosition} />
                </button>
                <button
                  className="avatar-tool-overview-collapse"
                  type="button"
                  aria-expanded="true"
                  aria-label={i18n('chat.avatarToolOverviewHide', 'Hide overview')}
                  title={i18n('chat.avatarToolOverviewHide', 'Hide overview')}
                  onClick={() => setOverview(false)}
                >
                  <span aria-hidden="true" className="avatar-tool-overview-collapse-icon" />
                </button>
              </div>
              {overviewPositionMenuOpen ? (
                <div
                  id="avatar-tool-overview-position-options"
                  className="avatar-tool-overview-position-menu"
                  role="group"
                  aria-label={i18n(
                    'chat.avatarToolOverviewPosition',
                    'Overview position',
                  )}
                >
                  {([
                    ['top-left', 'chat.avatarToolOverviewTopLeft', 'Move overview to top left'],
                    ['top-right', 'chat.avatarToolOverviewTopRight', 'Move overview to top right'],
                    ['bottom-left', 'chat.avatarToolOverviewBottomLeft', 'Move overview to bottom left'],
                    ['bottom-right', 'chat.avatarToolOverviewBottomRight', 'Move overview to bottom right'],
                  ] as const).map(([position, key, fallback]) => (
                    <button
                      key={position}
                      type="button"
                      className={overviewPosition === position ? 'is-active' : ''}
                      aria-pressed={overviewPosition === position}
                      aria-label={i18n(key, fallback)}
                      title={i18n(key, fallback)}
                      onClick={() => moveOverview(position)}
                    >
                      <OverviewPositionIcon position={position} />
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <button
              ref={overviewOpenButtonRef}
              className="avatar-tool-overview-open"
              type="button"
              aria-expanded="false"
              aria-label={i18n('chat.avatarToolOverviewShow', 'Show overview')}
              onClick={() => setOverview(true)}
            >
              <OverviewMapIcon />
            </button>
          )}
        </Panel>
      </ReactFlow>
      <div className="avatar-tool-canvas-toolbar">
        <div className="avatar-tool-interaction-add" aria-label={i18n(
          'chat.avatarToolInteractionAddGroup',
          'Add interaction',
        )}>
          <span>{i18n('chat.avatarToolInteractionAdd', 'Add')}</span>
          <button type="button" onClick={() => addInteraction('mouse-click')}>
            <span aria-hidden="true"><InteractionIcon kind="mouse-click" /></span>
            {i18n('chat.avatarToolInteractionMouseClick', 'Mouse click')}
          </button>
          <button type="button" onClick={() => addInteraction('after')}>
            <span aria-hidden="true"><InteractionIcon kind="after" /></span>
            {i18n('chat.avatarToolInteractionAfterTime', 'Delayed switch')}
          </button>
        </div>
        <div className="avatar-tool-edge-style" role="group" aria-label={i18n(
          'chat.avatarToolEdgeStyle',
          'Connection style',
        )}>
          {(['orthogonal', 'curved'] as const).map(style => {
            const label = style === 'orthogonal'
              ? i18n('chat.avatarToolEdgeStyleOrthogonal', 'Elbow')
              : i18n('chat.avatarToolEdgeStyleCurved', 'Curve');
            return (
              <button
                key={style}
                type="button"
                className={edgeLineStyle === style ? 'is-active' : ''}
                aria-pressed={edgeLineStyle === style}
                title={label}
                onClick={() => {
                  setEdgeLineStyle(style);
                  saveEdgeLineStylePreference(style);
                }}
              >
                <EdgeLineStyleIcon style={style} />
                {label}
              </button>
            );
          })}
        </div>
      </div>
      <span className="avatar-tool-workspace-canvas-status" role="status">
        {ready
          ? i18n('chat.avatarToolWorkspaceCanvasReady', 'Canvas ready')
          : i18n('chat.avatarToolWorkspaceCanvasLoading', 'Preparing canvas…')}
      </span>
    </div>
  );
}

type AvatarToolEditorWorkspaceProps = {
  title: string;
  dialogRef: RefObject<HTMLElement>;
  backButtonRef?: RefObject<HTMLButtonElement>;
  onBack?(): void;
  showHeader?: boolean;
  onPointerDown?(event: ReactPointerEvent<HTMLElement>): void;
  onMouseDown?(event: ReactMouseEvent<HTMLElement>): void;
  children: ReactNode;
};

export default function AvatarToolEditorWorkspace({
  title,
  dialogRef,
  backButtonRef,
  onBack,
  showHeader = true,
  onPointerDown,
  onMouseDown,
  children,
}: AvatarToolEditorWorkspaceProps) {
  return (
    <AvatarToolInteractionEditorProvider>
      <section
        className="avatar-tool-editor-workspace"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={showHeader ? undefined : title}
        aria-labelledby={showHeader ? 'avatar-tool-editor-workspace-title' : undefined}
        tabIndex={-1}
        onPointerDown={onPointerDown}
        onMouseDown={onMouseDown}
        onClick={(event) => event.stopPropagation()}
      >
        {showHeader ? (
          <header className="avatar-tool-workspace-header">
            <button
              className="avatar-tool-workspace-back"
              type="button"
              ref={backButtonRef}
              onClick={onBack}
            >
              <span aria-hidden="true">←</span>
              {i18n('chat.avatarToolCreateBack', 'Back')}
            </button>
            <div className="avatar-tool-workspace-heading">
              <span>{i18n('chat.avatarToolManagerTitle', 'Manage tools')}</span>
              <h2 id="avatar-tool-editor-workspace-title">{title}</h2>
            </div>
            <span className="avatar-tool-workspace-local-badge">
              <span aria-hidden="true">●</span>
              {i18n('chat.avatarToolWorkspaceLocalOnly', 'Saved on this device')}
            </span>
          </header>
        ) : null}

        <div className="avatar-tool-workspace-main">
          <section
            className="avatar-tool-workspace-stage"
            aria-label={i18n('chat.avatarToolWorkspaceCanvasTitle', 'Interaction flow')}
          >
            <div className="avatar-tool-workspace-stage-heading">
              <h3>{i18n('chat.avatarToolWorkspaceCanvasTitle', 'Interaction flow')}</h3>
              <p>{i18n(
                'chat.avatarToolWorkspaceCanvasHint',
                'Drag nodes to move · Connect from any edge point · Scroll to pan',
              )}</p>
            </div>
            <AvatarToolInteractionCanvas />
          </section>

          <aside
            className="avatar-tool-workspace-settings"
            aria-label={i18n('chat.avatarToolWorkspaceEditorTitle', 'Tool editor')}
          >
            <div className="avatar-tool-workspace-settings-heading">
              <h3>{i18n('chat.avatarToolWorkspaceEditorTitle', 'Tool editor')}</h3>
              <p className="avatar-tool-workspace-content-note">{i18n(
                'chat.avatarToolCreatePrivacy',
                'Images and sounds stay on this device; during interactions, the name and matching description are sent to the model.',
              )}</p>
            </div>
            <div className="avatar-tool-workspace-settings-body">
              {children}
            </div>
          </aside>
        </div>
      </section>
    </AvatarToolInteractionEditorProvider>
  );
}
