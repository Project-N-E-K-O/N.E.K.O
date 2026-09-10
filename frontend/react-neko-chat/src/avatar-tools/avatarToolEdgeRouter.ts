import { getBezierPath, Position } from '@xyflow/react';

export type AvatarToolRouteNodeBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type AvatarToolRoutePoint = {
  x: number;
  y: number;
};

export type AvatarToolRouteEdge = {
  id: string;
  source: string;
  target: string;
  sourcePosition?: Position;
  targetPosition?: Position;
};

export type AvatarToolPlannedEdgeRoute = {
  path: string;
  points: AvatarToolRoutePoint[];
  sourcePosition: Position;
  targetPosition: Position;
};

export type AvatarToolRoutePlanOptions = {
  previousRoutes?: ReadonlyMap<string, AvatarToolPlannedEdgeRoute>;
  previousBoxes?: ReadonlyMap<string, AvatarToolRouteNodeBox>;
  changedNodeIds?: ReadonlySet<string>;
};

export type AvatarToolEdgeLineStyle = 'orthogonal' | 'curved';

type RouteObstacle = {
  left: number;
  right: number;
  top: number;
  bottom: number;
};

type RouteSegment = {
  from: AvatarToolRoutePoint;
  to: AvatarToolRoutePoint;
  axis: 1 | 2;
};

type PreparedRouteSegment = RouteSegment & {
  left: number;
  right: number;
  top: number;
  bottom: number;
};

type OrthogonalRouteOptions = {
  existingRoutes?: readonly (readonly AvatarToolRoutePoint[])[];
  edgePenaltyExclusionZones?: readonly RouteObstacle[];
  sourcePosition?: Position;
  targetPosition?: Position;
};

type EndpointChoice = {
  sourcePosition: Position;
  targetPosition: Position;
};

const SIDES = [Position.Right, Position.Left, Position.Bottom, Position.Top] as const;
const EPSILON = 0.01;
const NODE_CLEARANCE = 18;
const PORT_STUB_LENGTH = 30;
const PORT_INSET = 16;
const PORT_GAP = 14;
const EDGE_GAP = 12;
const PORT_FAN_ZONE = PORT_STUB_LENGTH + EDGE_GAP;
const OUTER_CORRIDOR_MARGIN = 42;
const ROUTING_ENVELOPE_PADDING = 96;
const BEND_COST = 34;
const BACKTRACK_COST = 0.22;
const EDGE_CROSSING_COST = 150;
const EDGE_OVERLAP_COST = 220;
const CORNER_RADIUS = 10;
const CURVED_SELF_LOOP_RADIUS = 28;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function pointEquals(left: AvatarToolRoutePoint, right: AvatarToolRoutePoint): boolean {
  return Math.abs(left.x - right.x) < EPSILON && Math.abs(left.y - right.y) < EPSILON;
}

function center(box: AvatarToolRouteNodeBox): AvatarToolRoutePoint {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

function sideVector(position: Position): AvatarToolRoutePoint {
  if (position === Position.Left) return { x: -1, y: 0 };
  if (position === Position.Right) return { x: 1, y: 0 };
  if (position === Position.Top) return { x: 0, y: -1 };
  return { x: 0, y: 1 };
}

function pointOnSide(
  box: AvatarToolRouteNodeBox,
  position: Position,
  laneOffset = 0,
): AvatarToolRoutePoint {
  const boxCenter = center(box);
  if (position === Position.Left || position === Position.Right) {
    return {
      x: position === Position.Left ? box.x : box.x + box.width,
      y: clamp(boxCenter.y + laneOffset, box.y + PORT_INSET, box.y + box.height - PORT_INSET),
    };
  }
  return {
    x: clamp(boxCenter.x + laneOffset, box.x + PORT_INSET, box.x + box.width - PORT_INSET),
    y: position === Position.Top ? box.y : box.y + box.height,
  };
}

function sideLaneExtent(box: AvatarToolRouteNodeBox, position: Position): number {
  return Math.max(0, (position === Position.Left || position === Position.Right
    ? box.height
    : box.width) / 2 - PORT_INSET);
}

function pointOutsideNode(
  point: AvatarToolRoutePoint,
  position: Position,
  distance: number,
): AvatarToolRoutePoint {
  const vector = sideVector(position);
  return { x: point.x + vector.x * distance, y: point.y + vector.y * distance };
}

function routeObstacle(box: AvatarToolRouteNodeBox): RouteObstacle {
  return {
    left: box.x - NODE_CLEARANCE,
    right: box.x + box.width + NODE_CLEARANCE,
    top: box.y - NODE_CLEARANCE,
    bottom: box.y + box.height + NODE_CLEARANCE,
  };
}

function nodeBounds(box: AvatarToolRouteNodeBox): RouteObstacle {
  return {
    left: box.x,
    right: box.x + box.width,
    top: box.y,
    bottom: box.y + box.height,
  };
}

function obstacleIntersectsEnvelope(
  obstacle: RouteObstacle,
  start: AvatarToolRoutePoint,
  end: AvatarToolRoutePoint,
): boolean {
  const left = Math.min(start.x, end.x) - ROUTING_ENVELOPE_PADDING;
  const right = Math.max(start.x, end.x) + ROUTING_ENVELOPE_PADDING;
  const top = Math.min(start.y, end.y) - ROUTING_ENVELOPE_PADDING;
  const bottom = Math.max(start.y, end.y) + ROUTING_ENVELOPE_PADDING;
  return obstacle.right >= left
    && obstacle.left <= right
    && obstacle.bottom >= top
    && obstacle.top <= bottom;
}

function pointInsideObstacle(point: AvatarToolRoutePoint, obstacle: RouteObstacle): boolean {
  return point.x > obstacle.left + EPSILON
    && point.x < obstacle.right - EPSILON
    && point.y > obstacle.top + EPSILON
    && point.y < obstacle.bottom - EPSILON;
}

function pointInsideOrOnObstacle(point: AvatarToolRoutePoint, obstacle: RouteObstacle): boolean {
  return point.x >= obstacle.left - EPSILON
    && point.x <= obstacle.right + EPSILON
    && point.y >= obstacle.top - EPSILON
    && point.y <= obstacle.bottom + EPSILON;
}

function portFanZone(box: AvatarToolRouteNodeBox): RouteObstacle {
  return {
    left: box.x - PORT_FAN_ZONE,
    right: box.x + box.width + PORT_FAN_ZONE,
    top: box.y - PORT_FAN_ZONE,
    bottom: box.y + box.height + PORT_FAN_ZONE,
  };
}

function segmentCrossesObstacle(
  from: AvatarToolRoutePoint,
  to: AvatarToolRoutePoint,
  obstacle: RouteObstacle,
): boolean {
  if (Math.abs(from.y - to.y) < EPSILON) {
    const minimumX = Math.min(from.x, to.x);
    const maximumX = Math.max(from.x, to.x);
    return from.y > obstacle.top + EPSILON
      && from.y < obstacle.bottom - EPSILON
      && maximumX > obstacle.left + EPSILON
      && minimumX < obstacle.right - EPSILON;
  }
  if (Math.abs(from.x - to.x) < EPSILON) {
    const minimumY = Math.min(from.y, to.y);
    const maximumY = Math.max(from.y, to.y);
    return from.x > obstacle.left + EPSILON
      && from.x < obstacle.right - EPSILON
      && maximumY > obstacle.top + EPSILON
      && minimumY < obstacle.bottom - EPSILON;
  }
  return true;
}

function compactRoute(points: readonly AvatarToolRoutePoint[]): AvatarToolRoutePoint[] {
  const distinct = points.filter((point, index) => index === 0 || !pointEquals(point, points[index - 1]));
  return distinct.filter((point, index) => {
    if (index === 0 || index === distinct.length - 1) return true;
    const previous = distinct[index - 1];
    const next = distinct[index + 1];
    const vertical = Math.abs(previous.x - point.x) < EPSILON
      && Math.abs(point.x - next.x) < EPSILON
      && (point.y - previous.y) * (next.y - point.y) >= 0;
    const horizontal = Math.abs(previous.y - point.y) < EPSILON
      && Math.abs(point.y - next.y) < EPSILON
      && (point.x - previous.x) * (next.x - point.x) >= 0;
    return !vertical && !horizontal;
  });
}

function routeSegments(points: readonly AvatarToolRoutePoint[]): RouteSegment[] {
  const compact = compactRoute(points);
  return compact.slice(1).map((point, index) => {
    const from = compact[index];
    return {
      from,
      to: point,
      axis: Math.abs(from.y - point.y) < EPSILON ? 1 : 2,
    };
  });
}

function prepareRouteSegments(
  routes: readonly (readonly AvatarToolRoutePoint[])[],
): PreparedRouteSegment[] {
  return routes.flatMap(route => routeSegments(route).map(segment => ({
    ...segment,
    left: Math.min(segment.from.x, segment.to.x),
    right: Math.max(segment.from.x, segment.to.x),
    top: Math.min(segment.from.y, segment.to.y),
    bottom: Math.max(segment.from.y, segment.to.y),
  })));
}

function intervalOverlap(startA: number, endA: number, startB: number, endB: number): number {
  return Math.max(0, Math.min(Math.max(startA, endA), Math.max(startB, endB))
    - Math.max(Math.min(startA, endA), Math.min(startB, endB)));
}

function segmentEdgePenalty(candidate: RouteSegment, fixed: RouteSegment): number {
  if (candidate.axis === fixed.axis) {
    const distance = candidate.axis === 1
      ? Math.abs(candidate.from.y - fixed.from.y)
      : Math.abs(candidate.from.x - fixed.from.x);
    const overlap = candidate.axis === 1
      ? intervalOverlap(candidate.from.x, candidate.to.x, fixed.from.x, fixed.to.x)
      : intervalOverlap(candidate.from.y, candidate.to.y, fixed.from.y, fixed.to.y);
    if (overlap <= EPSILON || distance >= EDGE_GAP) return 0;
    if (distance < EPSILON) return EDGE_OVERLAP_COST + overlap * 1.6;
    return (EDGE_GAP - distance) * 2.5 + overlap * 0.18;
  }

  const horizontal = candidate.axis === 1 ? candidate : fixed;
  const vertical = candidate.axis === 2 ? candidate : fixed;
  const crossingX = vertical.from.x;
  const crossingY = horizontal.from.y;
  const crossesHorizontal = crossingX > Math.min(horizontal.from.x, horizontal.to.x) + EPSILON
    && crossingX < Math.max(horizontal.from.x, horizontal.to.x) - EPSILON;
  const crossesVertical = crossingY > Math.min(vertical.from.y, vertical.to.y) + EPSILON
    && crossingY < Math.max(vertical.from.y, vertical.to.y) - EPSILON;
  return crossesHorizontal && crossesVertical ? EDGE_CROSSING_COST : 0;
}

function segmentExistingRoutePenalty(
  segment: RouteSegment,
  existingSegments: readonly PreparedRouteSegment[],
  exclusionZones: readonly RouteObstacle[] = [],
): number {
  if (exclusionZones.some(zone => (
    pointInsideOrOnObstacle(segment.from, zone)
    && pointInsideOrOnObstacle(segment.to, zone)
  ))) return 0;
  const candidateLeft = Math.min(segment.from.x, segment.to.x);
  const candidateRight = Math.max(segment.from.x, segment.to.x);
  const candidateTop = Math.min(segment.from.y, segment.to.y);
  const candidateBottom = Math.max(segment.from.y, segment.to.y);
  return existingSegments.reduce((total, fixed) => {
    const separated = candidateRight < fixed.left - EDGE_GAP
      || candidateLeft > fixed.right + EDGE_GAP
      || candidateBottom < fixed.top - EDGE_GAP
      || candidateTop > fixed.bottom + EDGE_GAP;
    return separated ? total : total + segmentEdgePenalty(segment, fixed);
  }, 0);
}

function routeIsClear(points: readonly AvatarToolRoutePoint[], obstacles: readonly RouteObstacle[]): boolean {
  return routeSegments(points).every(segment => (
    !obstacles.some(obstacle => segmentCrossesObstacle(segment.from, segment.to, obstacle))
  ));
}

function routeScore(
  points: readonly AvatarToolRoutePoint[],
  existingRoutes: readonly (readonly AvatarToolRoutePoint[])[] = [],
): number {
  return routeScoreWithPreparedSegments(points, prepareRouteSegments(existingRoutes));
}

function routeScoreWithPreparedSegments(
  points: readonly AvatarToolRoutePoint[],
  existingSegments: readonly PreparedRouteSegment[],
): number {
  const segments = routeSegments(points);
  const length = segments.reduce((total, segment) => total
    + Math.abs(segment.to.x - segment.from.x)
    + Math.abs(segment.to.y - segment.from.y), 0);
  const crossings = segments.reduce(
    (total, segment) => total + segmentExistingRoutePenalty(segment, existingSegments),
    0,
  );
  return length + Math.max(0, segments.length - 1) * BEND_COST + crossings;
}

function routeRespectsEndpointDirections(
  points: readonly AvatarToolRoutePoint[],
  sourcePosition?: Position,
  targetPosition?: Position,
): boolean {
  const compact = compactRoute(points);
  if (compact.length < 2) return false;
  if (
    sourcePosition
    && movesAgainstDirection(compact[0], compact[1], sideVector(sourcePosition))
  ) return false;
  if (targetPosition) {
    const targetVector = sideVector(targetPosition);
    if (movesAgainstDirection(
      compact[compact.length - 2],
      compact[compact.length - 1],
      { x: -targetVector.x, y: -targetVector.y },
    )) return false;
  }
  return true;
}

function simpleOrthogonalRoute(
  start: AvatarToolRoutePoint,
  end: AvatarToolRoutePoint,
  obstacles: readonly RouteObstacle[],
  existingSegments: readonly PreparedRouteSegment[],
  options: OrthogonalRouteOptions,
): AvatarToolRoutePoint[] | null {
  const rawCandidates: AvatarToolRoutePoint[][] = [];
  if (Math.abs(start.x - end.x) < EPSILON || Math.abs(start.y - end.y) < EPSILON) {
    rawCandidates.push([start, end]);
  } else {
    rawCandidates.push(
      [start, { x: end.x, y: start.y }, end],
      [start, { x: start.x, y: end.y }, end],
    );
  }
  const candidates = rawCandidates
    .map(compactRoute)
    .filter(points => routeRespectsEndpointDirections(
      points,
      options.sourcePosition,
      options.targetPosition,
    ))
    .filter(points => routeIsClear(points, obstacles))
    .map(points => ({
      points,
      score: routeScoreWithPreparedSegments(points, existingSegments),
      edgePenalty: routeSegments(points).reduce(
        (total, segment) => total + segmentExistingRoutePenalty(
          segment,
          existingSegments,
          options.edgePenaltyExclusionZones,
        ),
        0,
      ),
    }))
    .filter(candidate => candidate.edgePenalty < EPSILON)
    .sort((left, right) => left.score - right.score);
  return candidates[0]?.points ?? null;
}

function sortedUnique(values: readonly number[]): number[] {
  return [...values]
    .sort((left, right) => left - right)
    .filter((value, index, all) => index === 0 || Math.abs(value - all[index - 1]) >= EPSILON);
}

class MinHeap {
  private items: Array<{ state: number; cost: number }> = [];

  get size(): number {
    return this.items.length;
  }

  push(item: { state: number; cost: number }): void {
    this.items.push(item);
    let index = this.items.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (this.items[parent].cost <= item.cost) break;
      this.items[index] = this.items[parent];
      index = parent;
    }
    this.items[index] = item;
  }

  pop(): { state: number; cost: number } | undefined {
    const root = this.items[0];
    const tail = this.items.pop();
    if (!root || !tail || this.items.length === 0) return root;
    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      const right = left + 1;
      if (left >= this.items.length) break;
      const child = right < this.items.length && this.items[right].cost < this.items[left].cost
        ? right
        : left;
      if (this.items[child].cost >= tail.cost) break;
      this.items[index] = this.items[child];
      index = child;
    }
    this.items[index] = tail;
    return root;
  }
}

function movesAgainstDirection(
  from: AvatarToolRoutePoint,
  to: AvatarToolRoutePoint,
  expected: AvatarToolRoutePoint,
): boolean {
  const dx = Math.sign(to.x - from.x);
  const dy = Math.sign(to.y - from.y);
  return dx * expected.x + dy * expected.y < 0;
}

function findAvatarToolOrthogonalRouteWithObstacles(
  start: AvatarToolRoutePoint,
  end: AvatarToolRoutePoint,
  obstacles: readonly RouteObstacle[],
  options: OrthogonalRouteOptions = {},
): AvatarToolRoutePoint[] | null {
  if (pointEquals(start, end)) return [start, end];
  const existingRoutes = options.existingRoutes ?? [];
  const envelope = {
    left: Math.min(start.x, end.x) - ROUTING_ENVELOPE_PADDING,
    right: Math.max(start.x, end.x) + ROUTING_ENVELOPE_PADDING,
    top: Math.min(start.y, end.y) - ROUTING_ENVELOPE_PADDING,
    bottom: Math.max(start.y, end.y) + ROUTING_ENVELOPE_PADDING,
  };
  const existingSegments = prepareRouteSegments(existingRoutes).filter(segment => (
    segment.right >= envelope.left
    && segment.left <= envelope.right
    && segment.bottom >= envelope.top
    && segment.top <= envelope.bottom
  ));
  const simpleRoute = simpleOrthogonalRoute(start, end, obstacles, existingSegments, options);
  if (simpleRoute) return simpleRoute;
  const relevantExistingPoints = existingRoutes.flatMap(route => route.filter(point => (
    point.x >= envelope.left
    && point.x <= envelope.right
    && point.y >= envelope.top
    && point.y <= envelope.bottom
  )));
  const obstacleXs = obstacles.flatMap(obstacle => [obstacle.left, obstacle.right]);
  const obstacleYs = obstacles.flatMap(obstacle => [obstacle.top, obstacle.bottom]);
  const boundsX = [start.x, end.x, ...obstacleXs];
  const boundsY = [start.y, end.y, ...obstacleYs];
  const minimumX = Math.min(...boundsX) - OUTER_CORRIDOR_MARGIN;
  const maximumX = Math.max(...boundsX) + OUTER_CORRIDOR_MARGIN;
  const minimumY = Math.min(...boundsY) - OUTER_CORRIDOR_MARGIN;
  const maximumY = Math.max(...boundsY) + OUTER_CORRIDOR_MARGIN;
  const xs = sortedUnique([
    start.x,
    end.x,
    minimumX,
    maximumX,
    ...obstacleXs,
    ...relevantExistingPoints.flatMap(point => [point.x - EDGE_GAP, point.x, point.x + EDGE_GAP]),
  ]);
  const ys = sortedUnique([
    start.y,
    end.y,
    minimumY,
    maximumY,
    ...obstacleYs,
    ...relevantExistingPoints.flatMap(point => [point.y - EDGE_GAP, point.y, point.y + EDGE_GAP]),
  ]);
  const points: AvatarToolRoutePoint[] = [];
  const gridPointIndices = Array.from({ length: ys.length }, () => Array(xs.length).fill(-1));
  ys.forEach((y, yIndex) => {
    xs.forEach((x, xIndex) => {
      const point = { x, y };
      if (obstacles.some(obstacle => pointInsideObstacle(point, obstacle))) return;
      gridPointIndices[yIndex][xIndex] = points.length;
      points.push(point);
    });
  });
  const startXIndex = xs.findIndex(x => Math.abs(x - start.x) < EPSILON);
  const startYIndex = ys.findIndex(y => Math.abs(y - start.y) < EPSILON);
  const endXIndex = xs.findIndex(x => Math.abs(x - end.x) < EPSILON);
  const endYIndex = ys.findIndex(y => Math.abs(y - end.y) < EPSILON);
  const startPointIndex = gridPointIndices[startYIndex]?.[startXIndex] ?? -1;
  const endPointIndex = gridPointIndices[endYIndex]?.[endXIndex] ?? -1;
  if (startPointIndex < 0 || endPointIndex < 0) return null;

  const pointGridPosition = new Map<number, { xIndex: number; yIndex: number }>();
  gridPointIndices.forEach((row, yIndex) => row.forEach((pointIndex, xIndex) => {
    if (pointIndex >= 0) pointGridPosition.set(pointIndex, { xIndex, yIndex });
  }));
  const stateCount = points.length * 3;
  const distances = Array(stateCount).fill(Number.POSITIVE_INFINITY);
  const previous = Array(stateCount).fill(-1);
  const startState = startPointIndex * 3;
  distances[startState] = 0;
  const queue = new MinHeap();
  queue.push({ state: startState, cost: 0 });
  let finalState = -1;

  while (queue.size > 0) {
    const current = queue.pop();
    if (!current || current.cost !== distances[current.state]) continue;
    const pointIndex = Math.floor(current.state / 3);
    const previousAxis = current.state % 3;
    if (pointIndex === endPointIndex) {
      finalState = current.state;
      break;
    }
    const gridPosition = pointGridPosition.get(pointIndex);
    if (!gridPosition) continue;
    const neighborGridPositions = [
      { xIndex: gridPosition.xIndex - 1, yIndex: gridPosition.yIndex, axis: 1 as const },
      { xIndex: gridPosition.xIndex + 1, yIndex: gridPosition.yIndex, axis: 1 as const },
      { xIndex: gridPosition.xIndex, yIndex: gridPosition.yIndex - 1, axis: 2 as const },
      { xIndex: gridPosition.xIndex, yIndex: gridPosition.yIndex + 1, axis: 2 as const },
    ];
    neighborGridPositions.forEach((neighborGrid) => {
      if (
        neighborGrid.xIndex < 0
        || neighborGrid.xIndex >= xs.length
        || neighborGrid.yIndex < 0
        || neighborGrid.yIndex >= ys.length
      ) return;
      const neighborPointIndex = gridPointIndices[neighborGrid.yIndex][neighborGrid.xIndex];
      if (neighborPointIndex < 0) return;
      const from = points[pointIndex];
      const to = points[neighborPointIndex];
      if (obstacles.some(obstacle => segmentCrossesObstacle(from, to, obstacle))) return;
      if (
        pointIndex === startPointIndex
        && options.sourcePosition
        && movesAgainstDirection(from, to, sideVector(options.sourcePosition))
      ) return;
      if (neighborPointIndex === endPointIndex && options.targetPosition) {
        const targetVector = sideVector(options.targetPosition);
        if (movesAgainstDirection(from, to, { x: -targetVector.x, y: -targetVector.y })) return;
      }
      const length = Math.abs(to.x - from.x) + Math.abs(to.y - from.y);
      const segment: RouteSegment = { from, to, axis: neighborGrid.axis };
      let nextCost = current.cost + length + segmentExistingRoutePenalty(
        segment,
        existingSegments,
        options.edgePenaltyExclusionZones,
      );
      if (previousAxis !== 0 && previousAxis !== neighborGrid.axis) nextCost += BEND_COST;
      const distanceBefore = Math.abs(end.x - from.x) + Math.abs(end.y - from.y);
      const distanceAfter = Math.abs(end.x - to.x) + Math.abs(end.y - to.y);
      if (distanceAfter > distanceBefore) nextCost += (distanceAfter - distanceBefore) * BACKTRACK_COST;
      const nextState = neighborPointIndex * 3 + neighborGrid.axis;
      if (nextCost >= distances[nextState]) return;
      distances[nextState] = nextCost;
      previous[nextState] = current.state;
      queue.push({ state: nextState, cost: nextCost });
    });
  }

  if (finalState < 0) return null;
  const route: AvatarToolRoutePoint[] = [];
  for (let state = finalState; state >= 0; state = previous[state]) {
    route.push(points[Math.floor(state / 3)]);
    if (state === startState) break;
  }
  return compactRoute(route.reverse());
}

export function findAvatarToolOrthogonalRoute(
  start: AvatarToolRoutePoint,
  end: AvatarToolRoutePoint,
  nodeObstacles: readonly AvatarToolRouteNodeBox[],
  options: OrthogonalRouteOptions = {},
): AvatarToolRoutePoint[] | null {
  const allObstacles = nodeObstacles.map(routeObstacle);
  const included = new Set(
    allObstacles.filter(obstacle => obstacleIntersectsEnvelope(obstacle, start, end)),
  );

  while (true) {
    const route = findAvatarToolOrthogonalRouteWithObstacles(
      start,
      end,
      [...included],
      options,
    );
    if (!route) return null;

    const missedObstacles = allObstacles.filter(obstacle => (
      !included.has(obstacle) && !routeIsClear(route, [obstacle])
    ));
    if (missedObstacles.length === 0) return route;
    missedObstacles.forEach(obstacle => included.add(obstacle));
  }
}

function roundedOrthogonalPath(
  points: readonly AvatarToolRoutePoint[],
  cornerRadius = CORNER_RADIUS,
): string {
  const compact = compactRoute(points);
  if (compact.length < 2) return '';
  let path = `M ${compact[0].x} ${compact[0].y}`;
  for (let index = 1; index < compact.length - 1; index += 1) {
    const previous = compact[index - 1];
    const current = compact[index];
    const next = compact[index + 1];
    const incoming = Math.abs(current.x - previous.x) + Math.abs(current.y - previous.y);
    const outgoing = Math.abs(next.x - current.x) + Math.abs(next.y - current.y);
    const radius = Math.min(cornerRadius, incoming / 2, outgoing / 2);
    const before = {
      x: current.x - Math.sign(current.x - previous.x) * radius,
      y: current.y - Math.sign(current.y - previous.y) * radius,
    };
    const after = {
      x: current.x + Math.sign(next.x - current.x) * radius,
      y: current.y + Math.sign(next.y - current.y) * radius,
    };
    path += ` L ${before.x} ${before.y} Q ${current.x} ${current.y} ${after.x} ${after.y}`;
  }
  const end = compact[compact.length - 1];
  return `${path} L ${end.x} ${end.y}`;
}

export function avatarToolEdgePath(
  route: AvatarToolPlannedEdgeRoute,
  lineStyle: AvatarToolEdgeLineStyle,
  selfConnection = false,
): string {
  if (lineStyle === 'orthogonal') return route.path;
  if (selfConnection) {
    return roundedOrthogonalPath(route.points, CURVED_SELF_LOOP_RADIUS);
  }
  const source = route.points[0];
  const target = route.points[route.points.length - 1];
  if (!source || !target) return '';
  return getBezierPath({
    sourceX: source.x,
    sourceY: source.y,
    sourcePosition: route.sourcePosition,
    targetX: target.x,
    targetY: target.y,
    targetPosition: route.targetPosition,
  })[0];
}

function sideAlignmentPenalty(
  from: AvatarToolRouteNodeBox,
  toward: AvatarToolRouteNodeBox,
  position: Position,
): number {
  const fromCenter = center(from);
  const towardCenter = center(toward);
  const dx = towardCenter.x - fromCenter.x;
  const dy = towardCenter.y - fromCenter.y;
  const distance = Math.max(Math.hypot(dx, dy), 1);
  const vector = sideVector(position);
  const alignment = (dx / distance) * vector.x + (dy / distance) * vector.y;
  return (1 - alignment) * 24;
}

function compactNearbyConnectionRoute(
  sourceBox: AvatarToolRouteNodeBox,
  targetBox: AvatarToolRouteNodeBox,
  sourcePoint: AvatarToolRoutePoint,
  targetPoint: AvatarToolRoutePoint,
  sourcePosition: Position,
  targetPosition: Position,
  obstacles: readonly AvatarToolRouteNodeBox[],
): AvatarToolRoutePoint[] | null {
  if (sourceBox === targetBox) return null;
  const sourceHorizontal = sourcePosition === Position.Left || sourcePosition === Position.Right;
  const targetHorizontal = targetPosition === Position.Left || targetPosition === Position.Right;
  let points: AvatarToolRoutePoint[] | null = null;

  if (sourceHorizontal === targetHorizontal) {
    const facesAcrossHorizontalGap = sourcePosition === Position.Right
      && targetPosition === Position.Left
      && sourcePoint.x <= targetPoint.x + EPSILON;
    const facesBackAcrossHorizontalGap = sourcePosition === Position.Left
      && targetPosition === Position.Right
      && sourcePoint.x >= targetPoint.x - EPSILON;
    const facesAcrossVerticalGap = sourcePosition === Position.Bottom
      && targetPosition === Position.Top
      && sourcePoint.y <= targetPoint.y + EPSILON;
    const facesBackAcrossVerticalGap = sourcePosition === Position.Top
      && targetPosition === Position.Bottom
      && sourcePoint.y >= targetPoint.y - EPSILON;

    if ((facesAcrossHorizontalGap || facesBackAcrossHorizontalGap)
      && Math.abs(targetPoint.x - sourcePoint.x) < PORT_STUB_LENGTH * 2) {
      const compactSourcePoint = pointEquals(sourcePoint, targetPoint)
        ? pointOnSide(sourceBox, sourcePosition, -PORT_GAP / 2)
        : sourcePoint;
      const compactTargetPoint = pointEquals(sourcePoint, targetPoint)
        ? pointOnSide(targetBox, targetPosition, PORT_GAP / 2)
        : targetPoint;
      const corridorX = (sourcePoint.x + targetPoint.x) / 2;
      points = compactRoute([
        compactSourcePoint,
        { x: corridorX, y: compactSourcePoint.y },
        { x: corridorX, y: compactTargetPoint.y },
        compactTargetPoint,
      ]);
    } else if ((facesAcrossVerticalGap || facesBackAcrossVerticalGap)
      && Math.abs(targetPoint.y - sourcePoint.y) < PORT_STUB_LENGTH * 2) {
      const compactSourcePoint = pointEquals(sourcePoint, targetPoint)
        ? pointOnSide(sourceBox, sourcePosition, -PORT_GAP / 2)
        : sourcePoint;
      const compactTargetPoint = pointEquals(sourcePoint, targetPoint)
        ? pointOnSide(targetBox, targetPosition, PORT_GAP / 2)
        : targetPoint;
      const corridorY = (sourcePoint.y + targetPoint.y) / 2;
      points = compactRoute([
        compactSourcePoint,
        { x: compactSourcePoint.x, y: corridorY },
        { x: compactTargetPoint.x, y: corridorY },
        compactTargetPoint,
      ]);
    }
  } else {
    const bend = sourceHorizontal
      ? { x: targetPoint.x, y: sourcePoint.y }
      : { x: sourcePoint.x, y: targetPoint.y };
    const sourceRun = Math.abs(bend.x - sourcePoint.x) + Math.abs(bend.y - sourcePoint.y);
    const targetRun = Math.abs(targetPoint.x - bend.x) + Math.abs(targetPoint.y - bend.y);
    const sourceDirection = sideVector(sourcePosition);
    const targetApproach = sideVector(targetPosition);
    const horizontalGap = Math.max(
      0,
      Math.max(sourceBox.x, targetBox.x)
        - Math.min(sourceBox.x + sourceBox.width, targetBox.x + targetBox.width),
    );
    const verticalGap = Math.max(
      0,
      Math.max(sourceBox.y, targetBox.y)
        - Math.min(sourceBox.y + sourceBox.height, targetBox.y + targetBox.height),
    );
    const leavesSourceSide = (bend.x - sourcePoint.x) * sourceDirection.x
      + (bend.y - sourcePoint.y) * sourceDirection.y >= -EPSILON;
    const entersTargetSide = (targetPoint.x - bend.x) * targetApproach.x
      + (targetPoint.y - bend.y) * targetApproach.y <= EPSILON;
    if (
      leavesSourceSide
      && entersTargetSide
      && (
        Math.min(sourceRun, targetRun) < PORT_STUB_LENGTH
        || (horizontalGap < PORT_STUB_LENGTH && verticalGap < PORT_STUB_LENGTH)
      )
    ) {
      points = compactRoute([sourcePoint, bend, targetPoint]);
    }
  }

  if (!points) return null;
  const otherObstacles = obstacles
    .filter(box => box !== sourceBox && box !== targetBox)
    .map(nodeBounds);
  return routeIsClear(points, otherObstacles) ? points : null;
}

function connectionRoute(
  sourceBox: AvatarToolRouteNodeBox,
  targetBox: AvatarToolRouteNodeBox,
  sourcePosition: Position,
  targetPosition: Position,
  sourceLaneOffset: number,
  targetLaneOffset: number,
  obstacles: readonly AvatarToolRouteNodeBox[],
  existingRoutes: readonly (readonly AvatarToolRoutePoint[])[],
): AvatarToolRoutePoint[] | null {
  const sourcePoint = pointOnSide(sourceBox, sourcePosition, sourceLaneOffset);
  const targetPoint = pointOnSide(targetBox, targetPosition, targetLaneOffset);
  const compactNearbyRoute = compactNearbyConnectionRoute(
    sourceBox,
    targetBox,
    sourcePoint,
    targetPoint,
    sourcePosition,
    targetPosition,
    obstacles,
  );
  if (compactNearbyRoute) return compactNearbyRoute;
  const sourceExit = pointOutsideNode(sourcePoint, sourcePosition, PORT_STUB_LENGTH);
  const targetEntry = pointOutsideNode(targetPoint, targetPosition, PORT_STUB_LENGTH);
  const sourceStubBlocked = obstacles.some(box => (
    box !== sourceBox
    && segmentCrossesObstacle(sourcePoint, sourceExit, routeObstacle(box))
  ));
  const targetStubBlocked = obstacles.some(box => (
    box !== targetBox
    && segmentCrossesObstacle(targetEntry, targetPoint, routeObstacle(box))
  ));
  if (sourceStubBlocked || targetStubBlocked) return null;
  const route = findAvatarToolOrthogonalRoute(sourceExit, targetEntry, obstacles, {
    existingRoutes,
    edgePenaltyExclusionZones: [portFanZone(sourceBox), portFanZone(targetBox)],
    sourcePosition,
    targetPosition,
  });
  return route ? compactRoute([sourcePoint, ...route, targetPoint]) : null;
}

function laneOffsetCandidates(
  box: AvatarToolRouteNodeBox,
  position: Position,
  preferredOffset: number,
): number[] {
  const extent = sideLaneExtent(box, position);
  return sortedUnique([
    preferredOffset,
    0,
    -extent,
    -extent / 2,
    extent / 2,
    extent,
  ]);
}

function connectionRouteWithFlexiblePorts(
  sourceBox: AvatarToolRouteNodeBox,
  targetBox: AvatarToolRouteNodeBox,
  sourcePosition: Position,
  targetPosition: Position,
  preferredSourceOffset: number,
  preferredTargetOffset: number,
  obstacles: readonly AvatarToolRouteNodeBox[],
  existingRoutes: readonly (readonly AvatarToolRoutePoint[])[],
): AvatarToolRoutePoint[] | null {
  const sourceOffsets = laneOffsetCandidates(sourceBox, sourcePosition, preferredSourceOffset);
  const targetOffsets = laneOffsetCandidates(targetBox, targetPosition, preferredTargetOffset);
  const existingSegments = prepareRouteSegments(existingRoutes);
  let bestNearby: { points: AvatarToolRoutePoint[]; score: number } | null = null;
  for (const sourceOffset of sourceOffsets) {
    for (const targetOffset of targetOffsets) {
      const points = compactNearbyConnectionRoute(
        sourceBox,
        targetBox,
        pointOnSide(sourceBox, sourcePosition, sourceOffset),
        pointOnSide(targetBox, targetPosition, targetOffset),
        sourcePosition,
        targetPosition,
        obstacles,
      );
      if (!points) continue;
      const portMovement = Math.abs(sourceOffset - preferredSourceOffset)
        + Math.abs(targetOffset - preferredTargetOffset);
      const score = routeScoreWithPreparedSegments(points, existingSegments) + portMovement * 0.35;
      if (!bestNearby || score < bestNearby.score - EPSILON) bestNearby = { points, score };
    }
  }
  if (bestNearby) return bestNearby.points;

  const preferredPoints = connectionRoute(
    sourceBox,
    targetBox,
    sourcePosition,
    targetPosition,
    preferredSourceOffset,
    preferredTargetOffset,
    obstacles,
    existingRoutes,
  );
  if (preferredPoints) return preferredPoints;

  const candidates = sourceOffsets.flatMap(sourceOffset => targetOffsets.map(targetOffset => ({
    sourceOffset,
    targetOffset,
    movement: Math.abs(sourceOffset - preferredSourceOffset)
      + Math.abs(targetOffset - preferredTargetOffset),
  }))).filter(candidate => (
    Math.abs(candidate.sourceOffset - preferredSourceOffset) >= EPSILON
    || Math.abs(candidate.targetOffset - preferredTargetOffset) >= EPSILON
  )).sort((left, right) => left.movement - right.movement);
  for (const candidate of candidates) {
    const points = connectionRoute(
      sourceBox,
      targetBox,
      sourcePosition,
      targetPosition,
      candidate.sourceOffset,
      candidate.targetOffset,
      obstacles,
      existingRoutes,
    );
    if (points) return points;
  }
  return null;
}

function fallbackConnectionRoute(
  sourceBox: AvatarToolRouteNodeBox,
  targetBox: AvatarToolRouteNodeBox,
  sourcePosition: Position,
  targetPosition: Position,
  sourceLaneOffset: number,
  targetLaneOffset: number,
  existingRoutes: readonly (readonly AvatarToolRoutePoint[])[],
): AvatarToolRoutePoint[] {
  let sourcePoint = pointOnSide(sourceBox, sourcePosition, sourceLaneOffset);
  let targetPoint = pointOnSide(targetBox, targetPosition, targetLaneOffset);
  if (pointEquals(sourcePoint, targetPoint)) {
    sourcePoint = pointOnSide(sourceBox, sourcePosition, sourceLaneOffset - PORT_GAP / 2);
    targetPoint = pointOnSide(targetBox, targetPosition, targetLaneOffset + PORT_GAP / 2);
  }
  const sourceExit = pointOutsideNode(sourcePoint, sourcePosition, PORT_STUB_LENGTH);
  const targetEntry = pointOutsideNode(targetPoint, targetPosition, PORT_STUB_LENGTH);
  const candidates = [
    compactRoute([
      sourcePoint,
      sourceExit,
      { x: targetEntry.x, y: sourceExit.y },
      targetEntry,
      targetPoint,
    ]),
    compactRoute([
      sourcePoint,
      sourceExit,
      { x: sourceExit.x, y: targetEntry.y },
      targetEntry,
      targetPoint,
    ]),
  ];
  const existingSegments = prepareRouteSegments(existingRoutes);
  let best = candidates[0];
  let bestScore = routeScoreWithPreparedSegments(best, existingSegments);
  for (const candidate of candidates.slice(1)) {
    const score = routeScoreWithPreparedSegments(candidate, existingSegments);
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  return best;
}

function chooseEndpointSides(
  sourceBox: AvatarToolRouteNodeBox,
  targetBox: AvatarToolRouteNodeBox,
  obstacles: readonly AvatarToolRouteNodeBox[],
  preferred: Partial<EndpointChoice> = {},
): EndpointChoice {
  if (preferred.sourcePosition && preferred.targetPosition) {
    return {
      sourcePosition: preferred.sourcePosition,
      targetPosition: preferred.targetPosition,
    };
  }
  let best: { choice: EndpointChoice; score: number } | null = null;
  const sourcePositions: readonly Position[] = preferred.sourcePosition
    ? [preferred.sourcePosition]
    : SIDES;
  const targetPositions: readonly Position[] = preferred.targetPosition
    ? [preferred.targetPosition]
    : SIDES;
  for (const sourcePosition of sourcePositions) {
    for (const targetPosition of targetPositions) {
      const points = connectionRoute(
        sourceBox,
        targetBox,
        sourcePosition,
        targetPosition,
        0,
        0,
        obstacles,
        [],
      );
      if (!points) continue;
      const score = routeScore(points)
        + sideAlignmentPenalty(sourceBox, targetBox, sourcePosition)
        + sideAlignmentPenalty(targetBox, sourceBox, targetPosition);
      if (!best || score < best.score - EPSILON) {
        best = { choice: { sourcePosition, targetPosition }, score };
      }
    }
  }
  return best === null
    ? {
      sourcePosition: preferred.sourcePosition ?? Position.Right,
      targetPosition: preferred.targetPosition ?? Position.Left,
    }
    : best.choice;
}

function sameSideSelfLoopPoints(
  box: AvatarToolRouteNodeBox,
  position: Position,
  reach: number,
): AvatarToolRoutePoint[] {
  const horizontalSide = position === Position.Left || position === Position.Right;
  const sourcePoint = pointOnSide(box, position, -14);
  const targetPoint = pointOnSide(box, position, 14);
  const vector = sideVector(position);
  const firstOuter = {
    x: sourcePoint.x + vector.x * reach,
    y: sourcePoint.y + vector.y * reach,
  };
  const secondOuter = horizontalSide
    ? { x: firstOuter.x, y: targetPoint.y }
    : { x: targetPoint.x, y: firstOuter.y };
  return compactRoute([sourcePoint, firstOuter, secondOuter, targetPoint]);
}

function selfLoopRoute(
  box: AvatarToolRouteNodeBox,
  obstacles: readonly AvatarToolRouteNodeBox[],
  existingRoutes: readonly (readonly AvatarToolRoutePoint[])[],
  preferredSourcePosition?: Position,
  preferredTargetPosition?: Position,
): AvatarToolPlannedEdgeRoute {
  const otherObstacles = obstacles
    .filter(candidate => candidate !== box)
    .map(routeObstacle);
  let best: AvatarToolPlannedEdgeRoute | null = null;
  let bestScore = Number.POSITIVE_INFINITY;
  const existingSegments = prepareRouteSegments(existingRoutes);
  const sourcePositions: readonly Position[] = preferredSourcePosition
    ? [preferredSourcePosition]
    : SIDES;
  const targetPositions: readonly Position[] = preferredTargetPosition
    ? [preferredTargetPosition]
    : SIDES;
  const choices: EndpointChoice[] = preferredSourcePosition || preferredTargetPosition
    ? sourcePositions.flatMap(sourcePosition => targetPositions.map(targetPosition => ({
      sourcePosition,
      targetPosition,
    })))
    : SIDES.map(position => ({ sourcePosition: position, targetPosition: position }));

  choices.forEach((choice, choiceIndex) => {
    if (choice.sourcePosition === choice.targetPosition) {
      [58, 82, 108].forEach((reach) => {
        const points = sameSideSelfLoopPoints(box, choice.sourcePosition, reach);
        if (!routeIsClear(points, otherObstacles)) return;
        const score = routeScoreWithPreparedSegments(points, existingSegments)
          + choiceIndex * 2 + reach * 0.05;
        if (score >= bestScore) return;
        bestScore = score;
        best = {
          path: roundedOrthogonalPath(points),
          points,
          sourcePosition: choice.sourcePosition,
          targetPosition: choice.targetPosition,
        };
      });
      return;
    }

    const points = connectionRoute(
      box,
      box,
      choice.sourcePosition,
      choice.targetPosition,
      -14,
      14,
      obstacles,
      existingRoutes,
    );
    if (!points) return;
    const score = routeScoreWithPreparedSegments(points, existingSegments) + choiceIndex * 2;
    if (score >= bestScore) return;
    bestScore = score;
    best = {
      path: roundedOrthogonalPath(points),
      points,
      sourcePosition: choice.sourcePosition,
      targetPosition: choice.targetPosition,
    };
  });
  if (best) return best;

  const sourcePosition = preferredSourcePosition ?? Position.Right;
  const targetPosition = preferredTargetPosition ?? sourcePosition;
  const sourcePoint = pointOnSide(box, sourcePosition, -14);
  const targetPoint = pointOnSide(box, targetPosition, 14);
  const sourceExit = pointOutsideNode(sourcePoint, sourcePosition, PORT_STUB_LENGTH);
  const targetEntry = pointOutsideNode(targetPoint, targetPosition, PORT_STUB_LENGTH);
  const points = sourcePosition === targetPosition
    ? sameSideSelfLoopPoints(box, sourcePosition, 82)
    : connectionRoute(
      box,
      box,
      sourcePosition,
      targetPosition,
      -14,
      14,
      [],
      existingRoutes,
    ) ?? compactRoute([
      sourcePoint,
      sourceExit,
      { x: targetEntry.x, y: sourceExit.y },
      targetEntry,
      targetPoint,
    ]);
  return {
    path: roundedOrthogonalPath(points),
    points,
    sourcePosition,
    targetPosition,
  };
}

function laneOffsets(
  edges: readonly AvatarToolRouteEdge[],
  choices: ReadonlyMap<string, EndpointChoice>,
  boxes: ReadonlyMap<string, AvatarToolRouteNodeBox>,
): Map<string, number> {
  const groups = new Map<string, Array<{
    edge: AvatarToolRouteEdge;
    endpoint: 'source' | 'target';
    side: Position;
    otherCenter: AvatarToolRoutePoint;
  }>>();
  edges.forEach((edge) => {
    if (edge.source === edge.target) return;
    const choice = choices.get(edge.id);
    const sourceBox = boxes.get(edge.source);
    const targetBox = boxes.get(edge.target);
    if (!choice || !sourceBox || !targetBox) return;
    const endpoints = [
      { endpoint: 'source' as const, nodeId: edge.source, side: choice.sourcePosition, other: targetBox },
      { endpoint: 'target' as const, nodeId: edge.target, side: choice.targetPosition, other: sourceBox },
    ];
    endpoints.forEach((entry) => {
      const key = `${entry.nodeId}:${entry.side}`;
      const group = groups.get(key) ?? [];
      group.push({ edge, endpoint: entry.endpoint, side: entry.side, otherCenter: center(entry.other) });
      groups.set(key, group);
    });
  });
  const offsets = new Map<string, number>();
  groups.forEach((group, key) => {
    const separator = key.lastIndexOf(':');
    const nodeId = key.slice(0, separator);
    const side = key.slice(separator + 1) as Position;
    const box = boxes.get(nodeId);
    if (!box) return;
    group.sort((left, right) => {
      const horizontalSide = side === Position.Left || side === Position.Right;
      const primary = horizontalSide
        ? left.otherCenter.y - right.otherCenter.y
        : left.otherCenter.x - right.otherCenter.x;
      const secondary = horizontalSide
        ? left.otherCenter.x - right.otherCenter.x
        : left.otherCenter.y - right.otherCenter.y;
      return primary || secondary || left.edge.id.localeCompare(right.edge.id);
    });
    const extent = sideLaneExtent(box, side);
    const gap = group.length > 1
      ? Math.min(PORT_GAP, (extent * 2) / (group.length - 1))
      : 0;
    group.forEach((entry, index) => {
      offsets.set(`${entry.edge.id}:${entry.endpoint}`, (index - (group.length - 1) / 2) * gap);
    });
  });
  return offsets;
}

function routeIntersectsNodeClearance(
  points: readonly AvatarToolRoutePoint[],
  box: AvatarToolRouteNodeBox,
): boolean {
  const obstacle = routeObstacle(box);
  return routeSegments(points).some(({ from, to }) => {
    if (Math.abs(from.y - to.y) < EPSILON) {
      const minimumX = Math.min(from.x, to.x);
      const maximumX = Math.max(from.x, to.x);
      return from.y >= obstacle.top - EPSILON
        && from.y <= obstacle.bottom + EPSILON
        && maximumX >= obstacle.left - EPSILON
        && minimumX <= obstacle.right + EPSILON;
    }
    const minimumY = Math.min(from.y, to.y);
    const maximumY = Math.max(from.y, to.y);
    return from.x >= obstacle.left - EPSILON
      && from.x <= obstacle.right + EPSILON
      && maximumY >= obstacle.top - EPSILON
      && minimumY <= obstacle.bottom + EPSILON;
  });
}

function affectedEdgeIdsForIncrementalPlan(
  edges: readonly AvatarToolRouteEdge[],
  boxes: ReadonlyMap<string, AvatarToolRouteNodeBox>,
  options: AvatarToolRoutePlanOptions,
): Set<string> | null {
  const { previousRoutes, previousBoxes, changedNodeIds } = options;
  if (!previousRoutes || !previousBoxes || !changedNodeIds) return null;

  const currentEdgeIds = new Set(edges.map(edge => edge.id));
  if (
    currentEdgeIds.size !== previousRoutes.size
    || [...currentEdgeIds].some(edgeId => !previousRoutes.has(edgeId))
  ) return null;

  const affected = new Set<string>();
  edges.forEach((edge) => {
    const previous = previousRoutes.get(edge.id);
    if (
      !previous
      || (edge.sourcePosition && edge.sourcePosition !== previous.sourcePosition)
      || (edge.targetPosition && edge.targetPosition !== previous.targetPosition)
    ) {
      affected.add(edge.id);
      return;
    }
    if (changedNodeIds.has(edge.source) || changedNodeIds.has(edge.target)) {
      affected.add(edge.id);
    }
  });

  edges.forEach((edge) => {
    if (affected.has(edge.id)) return;
    const previous = previousRoutes.get(edge.id);
    if (!previous) {
      affected.add(edge.id);
      return;
    }
    for (const nodeId of changedNodeIds) {
      if (nodeId === edge.source || nodeId === edge.target) continue;
      const currentBox = boxes.get(nodeId);
      const previousBox = previousBoxes.get(nodeId);
      if (
        (currentBox && routeIntersectsNodeClearance(previous.points, currentBox))
        || (previousBox && routeIntersectsNodeClearance(previous.points, previousBox))
      ) {
        affected.add(edge.id);
        break;
      }
    }
  });
  return affected;
}

export function planAvatarToolEdgeRoutes(
  edges: readonly AvatarToolRouteEdge[],
  boxes: ReadonlyMap<string, AvatarToolRouteNodeBox>,
  options: AvatarToolRoutePlanOptions = {},
): Map<string, AvatarToolPlannedEdgeRoute> {
  const obstacles = Array.from(boxes.values());
  const incrementalAffected = affectedEdgeIdsForIncrementalPlan(edges, boxes, options);
  const choices = new Map<string, EndpointChoice>();
  edges.forEach((edge) => {
    if (edge.source === edge.target) return;
    const sourceBox = boxes.get(edge.source);
    const targetBox = boxes.get(edge.target);
    if (!sourceBox || !targetBox) return;
    const previous = incrementalAffected && !incrementalAffected.has(edge.id)
      ? options.previousRoutes?.get(edge.id)
      : undefined;
    if (previous) {
      choices.set(edge.id, {
        sourcePosition: previous.sourcePosition,
        targetPosition: previous.targetPosition,
      });
      return;
    }
    choices.set(edge.id, chooseEndpointSides(sourceBox, targetBox, obstacles, {
      sourcePosition: edge.sourcePosition,
      targetPosition: edge.targetPosition,
    }));
  });
  const offsets = laneOffsets(edges, choices, boxes);
  if (incrementalAffected && options.previousBoxes && options.previousRoutes) {
    const previousChoices = new Map<string, EndpointChoice>();
    edges.forEach((edge) => {
      const previous = options.previousRoutes?.get(edge.id);
      if (!previous || edge.source === edge.target) return;
      previousChoices.set(edge.id, {
        sourcePosition: previous.sourcePosition,
        targetPosition: previous.targetPosition,
      });
    });
    const previousOffsets = laneOffsets(edges, previousChoices, options.previousBoxes);
    edges.forEach((edge) => {
      if (
        Math.abs((offsets.get(`${edge.id}:source`) ?? 0)
          - (previousOffsets.get(`${edge.id}:source`) ?? 0)) >= EPSILON
        || Math.abs((offsets.get(`${edge.id}:target`) ?? 0)
          - (previousOffsets.get(`${edge.id}:target`) ?? 0)) >= EPSILON
      ) incrementalAffected.add(edge.id);
    });
  }
  const existingRoutes: AvatarToolRoutePoint[][] = incrementalAffected
    ? edges.flatMap((edge) => {
      if (incrementalAffected.has(edge.id)) return [];
      const previous = options.previousRoutes?.get(edge.id);
      return previous ? [previous.points] : [];
    })
    : [];
  const plans = new Map<string, AvatarToolPlannedEdgeRoute>();
  if (incrementalAffected) {
    edges.forEach((edge) => {
      if (incrementalAffected.has(edge.id)) return;
      const previous = options.previousRoutes?.get(edge.id);
      if (previous) plans.set(edge.id, previous);
    });
  }
  edges.forEach((edge) => {
    if (incrementalAffected && !incrementalAffected.has(edge.id)) return;
    const sourceBox = boxes.get(edge.source);
    const targetBox = boxes.get(edge.target);
    if (!sourceBox || !targetBox) return;
    if (edge.source === edge.target) {
      const plan = selfLoopRoute(
        sourceBox,
        obstacles,
        existingRoutes,
        edge.sourcePosition,
        edge.targetPosition,
      );
      plans.set(edge.id, plan);
      existingRoutes.push(plan.points);
      return;
    }
    const choice = choices.get(edge.id);
    if (!choice) return;
    const preferredSourceOffset = offsets.get(`${edge.id}:source`) ?? 0;
    const preferredTargetOffset = offsets.get(`${edge.id}:target`) ?? 0;
    const routedPoints = connectionRouteWithFlexiblePorts(
      sourceBox,
      targetBox,
      choice.sourcePosition,
      choice.targetPosition,
      preferredSourceOffset,
      preferredTargetOffset,
      obstacles,
      existingRoutes,
    ) ?? connectionRoute(
      sourceBox,
      targetBox,
      choice.sourcePosition,
      choice.targetPosition,
      0,
      0,
      [],
      existingRoutes,
    );
    const points = routedPoints
      && !pointEquals(routedPoints[0], routedPoints[routedPoints.length - 1])
      ? routedPoints
      : fallbackConnectionRoute(
        sourceBox,
        targetBox,
        choice.sourcePosition,
        choice.targetPosition,
        preferredSourceOffset,
        preferredTargetOffset,
        existingRoutes,
      );
    const plan = {
      path: roundedOrthogonalPath(points),
      points,
      sourcePosition: choice.sourcePosition,
      targetPosition: choice.targetPosition,
    };
    plans.set(edge.id, plan);
    existingRoutes.push(points);
  });
  return new Map(edges.flatMap((edge) => {
    const plan = plans.get(edge.id);
    return plan ? [[edge.id, plan] as const] : [];
  }));
}
