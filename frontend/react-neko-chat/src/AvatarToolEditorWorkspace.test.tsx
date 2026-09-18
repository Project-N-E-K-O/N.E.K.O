import { useEffect } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Position } from '@xyflow/react';
import {
  AvatarToolInteractionCanvas,
  avatarToolConnectionPreviewPath,
  snapAvatarToolNodePosition,
} from './AvatarToolEditorWorkspace';
import {
  AvatarToolInteractionEditorProvider,
  useAvatarToolInteractionEditor,
} from './avatar-tools/AvatarToolInteractionEditorContext';
import {
  avatarToolEdgePath,
  findAvatarToolOrthogonalRoute,
  planAvatarToolEdgeRoutes,
} from './avatar-tools/avatarToolEdgeRouter';
import { findAvailableAvatarToolInteractionPosition } from './avatar-tools/avatarToolInteractionEditorModel';
import type { LocalAvatarToolLimits } from './avatar-tools/localTools';

const LIMITS: LocalAvatarToolLimits = {
  maxTools: 64,
  maxNameChars: 20,
  maxMeaningChars: 100,
  maxChangeImages: 16,
  maxImages: 17,
  maxInteractions: 16,
  maxLinks: 32,
  maxDelayMs: 600_000,
  maxImageBytes: 8_388_608,
  maxImagePixels: 16_000_000,
  maxAudioBytes: 5_242_880,
  maxAudioDurationMs: 10_000,
  maxTotalBytes: 268_435_456,
};

function PreparedCanvas() {
  const { dispatch, setImageState } = useAvatarToolInteractionEditor();
  useEffect(() => {
    setImageState([{
      id: 'img-a',
      image: null,
      imageUrl: '/initial.png',
      meaning: '',
    }], 'img-a');
    const ids = ['ix-a', 'ix-b', 'ix-c', 'ix-d'] as const;
    dispatch({
      type: 'reset',
      state: {
        items: ids.map((id, index) => ({
          id,
          kind: 'mouse-click' as const,
          position: { x: (index % 2) * 260, y: Math.floor(index / 2) * 160 },
          press: { kind: 'keep' as const },
          release: { kind: 'keep' as const },
        })),
        links: [{
          id: 'link-a-b',
          from: 'ix-a',
          to: 'ix-b',
          sourceSide: 'right',
          targetSide: 'left',
        }],
        initialImageTargetIds: ['ix-a'],
        initialImageLinkSides: {
          'ix-a': { sourceSide: 'right', targetSide: 'left' },
        },
        initialImagePosition: { x: -180, y: 80 },
        selectedInteractionId: null,
        selectedLinkId: 'link-a-b',
        selectedInitialLinkTargetId: null,
      },
    });
  }, [dispatch, setImageState]);
  return <AvatarToolInteractionCanvas />;
}

function PresetCanvas() {
  const { state, setImageState } = useAvatarToolInteractionEditor();
  useEffect(() => {
    setImageState([
      { id: 'img-a', image: null, imageUrl: '/a.png', meaning: '' },
      { id: 'img-b', image: null, imageUrl: '/b.png', meaning: '' },
      { id: 'img-c', image: null, imageUrl: '/c.png', meaning: '' },
    ], 'img-b');
  }, [setImageState]);
  return (
    <>
      <AvatarToolInteractionCanvas limits={LIMITS} />
      <output data-testid="preset-state">{JSON.stringify(state)}</output>
    </>
  );
}

function EmptyPresetCanvas() {
  const { state } = useAvatarToolInteractionEditor();
  return (
    <>
      <AvatarToolInteractionCanvas limits={LIMITS} />
      <output data-testid="empty-preset-state">{JSON.stringify(state)}</output>
    </>
  );
}

describe('AvatarToolEditorWorkspace', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('places consecutively added interactions in nearby empty slots', () => {
    const preferred = { x: 180, y: 140 };
    expect(findAvailableAvatarToolInteractionPosition(preferred, [])).toEqual(preferred);
    expect(findAvailableAvatarToolInteractionPosition(preferred, [
      { position: preferred },
    ])).toEqual({ x: 480, y: 140 });
    expect(findAvailableAvatarToolInteractionPosition(preferred, [
      { position: preferred },
      { position: { x: 480, y: 140 } },
    ])).toEqual({ x: 180, y: 310 });
  });

  it('applies reusable presets as replaceable ordinary graph drafts', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <AvatarToolInteractionEditorProvider>
        <PresetCanvas />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sequential switch' }));
    let state = JSON.parse(screen.getByTestId('preset-state').textContent || '{}');
    expect(state.items).toHaveLength(3);
    expect(state.items.map((item: { press: unknown; release: unknown }) => ({
      press: item.press,
      release: item.release,
    }))).toEqual([
      { press: { kind: 'keep' }, release: { kind: 'keep' } },
      { press: { kind: 'keep' }, release: { kind: 'keep' } },
      { press: { kind: 'keep' }, release: { kind: 'keep' } },
    ]);
    const firstIds = state.items.map((item: { id: string }) => item.id);
    expect(confirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Press swap' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(JSON.parse(screen.getByTestId('preset-state').textContent || '{}').items)
      .toHaveLength(3);

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Press swap' }));
    state = JSON.parse(screen.getByTestId('preset-state').textContent || '{}');
    expect(state.items).toHaveLength(1);
    expect(state.items[0]).toMatchObject({
      press: { kind: 'keep' },
      release: { kind: 'keep' },
    });
    expect(state.items.map((item: { id: string }) => item.id)).not.toEqual(firstIds);
    confirm.mockRestore();
  });

  it('shows actual built-in tools as read-only preset references', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <EmptyPresetCanvas />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    const pressSwap = screen.getByRole('button', { name: 'Press swap' });
    const clickAdvance = screen.getByRole('button', { name: 'Sequential switch' });
    const cycleStop = screen.getByRole('button', { name: 'Image cycle' });

    expect(within(pressSwap).getByText('Reference: 猫爪')).toBeInTheDocument();
    expect(within(pressSwap).getByText('Mouse click 1: Press switches the image → Release restores it'))
      .toBeInTheDocument();
    expect(within(pressSwap).getByText('Follow-up setup')).toBeInTheDocument();
    expect(within(pressSwap).getByText(/In “Mouse click 1”.*“Press”.*“Release”/))
      .toBeInTheDocument();
    expect(within(clickAdvance).getByText('Reference: 棒棒糖')).toBeInTheDocument();
    expect(within(clickAdvance).getByText('Mouse click 1 → Mouse click 2 → Mouse click 3 → end'))
      .toBeInTheDocument();
    expect(within(clickAdvance).getByText(/“Mouse click 1”.*“Mouse click 2”.*“Mouse click 3”.*“Release”/))
      .toBeInTheDocument();
    expect(within(cycleStop).getByText('Reference: 猜拳')).toBeInTheDocument();
    expect(within(cycleStop).getByText(/“Delayed switch 1”.*“Delayed switch 2”.*“Delayed switch 3”.*“Mouse click 1”.*“Delayed switch 4”/))
      .toBeInTheDocument();
    expect(within(cycleStop).getByText(/does not include win\/loss logic or result animation/))
      .toBeInTheDocument();
    expect(screen.getByText(/without copying the reference images or choosing node images/))
      .toBeInTheDocument();

    const imageSources = Array.from(
      document.querySelectorAll<HTMLImageElement>('.avatar-tool-preset-guide-images img'),
      image => image.getAttribute('src'),
    );
    expect(imageSources).toEqual(expect.arrayContaining([
      '/static/assets/avatar-tools/fist/primary-icon.png',
      '/static/assets/avatar-tools/fist/secondary-icon.png',
      '/static/assets/avatar-tools/lollipop/primary-icon.png',
      '/static/assets/avatar-tools/lollipop/secondary-icon.png',
      '/static/assets/avatar-tools/lollipop/tertiary-icon.png',
      '/static/assets/avatar-tools/rps/rock-icon.png',
      '/static/assets/avatar-tools/rps/scissors-icon.png',
      '/static/assets/avatar-tools/rps/paper-icon.png',
    ]));
    expect(JSON.parse(screen.getByTestId('empty-preset-state').textContent || '{}').items)
      .toEqual([]);
  });

  it('applies a connected preset graph without any image', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(
      <AvatarToolInteractionEditorProvider>
        <EmptyPresetCanvas />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    expect(screen.getByRole('button', { name: /Press swap/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Sequential switch/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Image cycle/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /Press swap/ }));
    expect(confirm).not.toHaveBeenCalled();
    const state = JSON.parse(screen.getByTestId('empty-preset-state').textContent || '{}');
    expect(state.items).toEqual([expect.objectContaining({
      kind: 'mouse-click',
      press: { kind: 'keep' },
      release: { kind: 'keep' },
    })]);
    expect(state.initialImageTargetIds).toEqual([state.items[0].id]);
    expect(state.links).toEqual([expect.objectContaining({
      from: state.items[0].id,
      to: state.items[0].id,
    })]);

    const pressSwapId = state.items[0].id;
    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: /Sequential switch/ }));
    expect(confirm).toHaveBeenCalledTimes(1);
    const sequential = JSON.parse(screen.getByTestId('empty-preset-state').textContent || '{}');
    expect(sequential.items).toHaveLength(3);
    expect(sequential.items.map((item: { id: string }) => item.id)).not.toContain(pressSwapId);
    expect(sequential.initialImageTargetIds).toEqual([sequential.items[0].id]);
    expect(sequential.links).toEqual([
      expect.objectContaining({ from: sequential.items[0].id, to: sequential.items[1].id }),
      expect.objectContaining({ from: sequential.items[1].id, to: sequential.items[2].id }),
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: /Image cycle/ }));
    expect(confirm).toHaveBeenCalledTimes(2);
    const cycleStop = JSON.parse(screen.getByTestId('empty-preset-state').textContent || '{}');
    const [firstDelay, secondDelay, thirdDelay, holdClick, resumeDelay] = cycleStop.items;
    expect(cycleStop.items).toHaveLength(5);
    expect(cycleStop.initialImageTargetIds).toEqual([firstDelay.id]);
    expect(cycleStop.links).toHaveLength(8);
    expect(cycleStop.links.filter((link: { from: string }) => link.from === firstDelay.id))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ to: secondDelay.id }),
        expect.objectContaining({ to: holdClick.id }),
      ]));
    expect(cycleStop.links.filter((link: { from: string }) => link.from === secondDelay.id))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ to: thirdDelay.id }),
        expect.objectContaining({ to: holdClick.id }),
      ]));
    expect(cycleStop.links.filter((link: { from: string }) => link.from === thirdDelay.id))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ to: firstDelay.id }),
        expect.objectContaining({ to: holdClick.id }),
      ]));
    expect(cycleStop.links.filter((link: { from: string }) => link.from === holdClick.id))
      .toEqual([expect.objectContaining({ to: resumeDelay.id })]);
    expect(cycleStop.links.filter((link: { from: string }) => link.from === resumeDelay.id))
      .toEqual([expect.objectContaining({ to: firstDelay.id })]);
    confirm.mockRestore();
  });

  it('routes a connection around another node instead of through it', () => {
    const source = { x: 0, y: 0, width: 100, height: 100 };
    const blocker = { x: 150, y: 0, width: 100, height: 100 };
    const target = { x: 300, y: 0, width: 100, height: 100 };
    const route = findAvatarToolOrthogonalRoute(
      { x: 118, y: 50 },
      { x: 282, y: 50 },
      [source, blocker, target],
    );

    expect(route).not.toBeNull();
    expect(route!.some(point => point.y <= -18 || point.y >= 118)).toBe(true);
    expect(route).not.toEqual([{ x: 118, y: 50 }, { x: 282, y: 50 }]);
  });

  it('checks the final route against nodes outside the initial search envelope', () => {
    const blocker = { x: 718, y: 175, width: 228, height: 104 };
    const route = planAvatarToolEdgeRoutes([{
      id: 'source-target',
      source: 'source',
      target: 'target',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }], new Map([
      ['source', { x: 502, y: 419, width: 202, height: 82 }],
      ['target', { x: 327, y: -7, width: 228, height: 104 }],
      ['blocker', blocker],
    ])).get('source-target')!;
    const crossesBlocker = route.points.slice(1).some((point, index) => {
      const previous = route.points[index];
      if (previous.y === point.y) {
        return previous.y > blocker.y
          && previous.y < blocker.y + blocker.height
          && Math.max(previous.x, point.x) > blocker.x
          && Math.min(previous.x, point.x) < blocker.x + blocker.width;
      }
      return previous.x > blocker.x
        && previous.x < blocker.x + blocker.width
        && Math.max(previous.y, point.y) > blocker.y
        && Math.min(previous.y, point.y) < blocker.y + blocker.height;
    });

    expect(crossesBlocker).toBe(false);
  });

  it('keeps reciprocal routes on different corridors', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'forward', source: 'a', target: 'b' },
      { id: 'backward', source: 'b', target: 'a' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
    ]));
    const forward = routes.get('forward')!;
    const backward = routes.get('backward')!;

    expect(forward.points[0]).not.toEqual(backward.points[backward.points.length - 1]);
    expect(forward.points).not.toEqual([...backward.points].reverse());
  });

  it('does not create a tiny endpoint dogleg when reciprocal and fan-out routes meet', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'initial-a', source: 'initial', target: 'a' },
      { id: 'edge-2-a-b', source: 'a', target: 'b' },
      { id: 'a-c', source: 'a', target: 'c' },
      { id: 'a-d', source: 'a', target: 'd' },
      { id: 'edge-1-b-a', source: 'b', target: 'a' },
    ], new Map([
      ['initial', { x: 80, y: 180, width: 202, height: 82 }],
      ['a', { x: 480.937, y: 206.914, width: 228, height: 104 }],
      ['b', { x: 180.937, y: 376.914, width: 228, height: 104 }],
      ['c', { x: 480.937, y: 376.914, width: 228, height: 104 }],
      ['d', { x: -119.063, y: 376.914, width: 228, height: 104 }],
    ]));
    const reciprocal = routes.get('edge-1-b-a')!;
    const segmentLengths = reciprocal.points.slice(1).map((point, index) => (
      Math.abs(point.x - reciprocal.points[index].x)
      + Math.abs(point.y - reciprocal.points[index].y)
    ));

    expect(Math.min(...segmentLengths)).toBeGreaterThanOrEqual(12);
  });

  it('does not let distant nodes change a clear route', () => {
    const start = { x: 0, y: 0 };
    const end = { x: 180, y: 80 };
    const route = findAvatarToolOrthogonalRoute(start, end, []);
    const routeWithDistantNode = findAvatarToolOrthogonalRoute(start, end, [
      { x: 4_000, y: 4_000, width: 200, height: 120 },
    ]);

    expect(routeWithDistantNode).toEqual(route);
  });

  it('does not let a distant connection change an unrelated route', () => {
    const boxes = new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
      ['c', { x: 4_000, y: 4_000, width: 100, height: 80 }],
      ['d', { x: 4_340, y: 4_000, width: 100, height: 80 }],
    ]);
    const route = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
    ], boxes).get('a-b');
    const routeWithDistantEdge = planAvatarToolEdgeRoutes([
      { id: 'c-d', source: 'c', target: 'd' },
      { id: 'a-b', source: 'a', target: 'b' },
    ], boxes).get('a-b');

    expect(routeWithDistantEdge).toEqual(route);
  });

  it('reuses unaffected routes when a node moves elsewhere on the canvas', () => {
    const edges = [
      { id: 'a-b', source: 'a', target: 'b', sourcePosition: Position.Right, targetPosition: Position.Left },
      { id: 'c-d', source: 'c', target: 'd', sourcePosition: Position.Right, targetPosition: Position.Left },
    ];
    const previousBoxes = new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
      ['c', { x: 0, y: 500, width: 100, height: 80 }],
      ['d', { x: 340, y: 500, width: 100, height: 80 }],
    ]);
    const previousRoutes = planAvatarToolEdgeRoutes(edges, previousBoxes);
    const nextBoxes = new Map(previousBoxes);
    nextBoxes.set('a', { x: 20, y: 30, width: 100, height: 80 });
    const nextRoutes = planAvatarToolEdgeRoutes(edges, nextBoxes, {
      previousRoutes,
      previousBoxes,
      changedNodeIds: new Set(['a']),
    });

    expect(nextRoutes.get('a-b')).not.toBe(previousRoutes.get('a-b'));
    expect(nextRoutes.get('c-d')).toBe(previousRoutes.get('c-d'));
  });

  it('reroutes a previously unaffected edge when a moved node enters its path', () => {
    const edges = [{
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }];
    const previousBoxes = new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
      ['blocker', { x: 160, y: 240, width: 80, height: 80 }],
    ]);
    const previousRoutes = planAvatarToolEdgeRoutes(edges, previousBoxes);
    const nextBoxes = new Map(previousBoxes);
    nextBoxes.set('blocker', { x: 160, y: 0, width: 80, height: 80 });
    const nextRoutes = planAvatarToolEdgeRoutes(edges, nextBoxes, {
      previousRoutes,
      previousBoxes,
      changedNodeIds: new Set(['blocker']),
    });

    expect(nextRoutes.get('a-b')).not.toBe(previousRoutes.get('a-b'));
    expect(nextRoutes.get('a-b')!.points.some(point => point.y <= -18 || point.y >= 98)).toBe(true);
  });

  it('reroutes an edge when a blocker leaves the clearance boundary it created', () => {
    const edges = [{
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }];
    const previousBoxes = new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
      ['blocker', { x: 160, y: 0, width: 80, height: 80 }],
    ]);
    const previousRoutes = planAvatarToolEdgeRoutes(edges, previousBoxes);
    const nextBoxes = new Map(previousBoxes);
    nextBoxes.set('blocker', { x: 160, y: 300, width: 80, height: 80 });
    const nextRoutes = planAvatarToolEdgeRoutes(edges, nextBoxes, {
      previousRoutes,
      previousBoxes,
      changedNodeIds: new Set(['blocker']),
    });

    expect(nextRoutes.get('a-b')).not.toBe(previousRoutes.get('a-b'));
    expect(nextRoutes.get('a-b')!.points).toEqual([
      { x: 100, y: 40 },
      { x: 340, y: 40 },
    ]);
  });

  it('updates only shared-side peers whose lane order changes after a move', () => {
    const edges = [
      { id: 'a-b', source: 'a', target: 'b', sourcePosition: Position.Right, targetPosition: Position.Left },
      { id: 'a-c', source: 'a', target: 'c', sourcePosition: Position.Right, targetPosition: Position.Left },
      { id: 'd-e', source: 'd', target: 'e', sourcePosition: Position.Right, targetPosition: Position.Left },
    ];
    const previousBoxes = new Map([
      ['a', { x: 0, y: 100, width: 100, height: 100 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
      ['c', { x: 340, y: 220, width: 100, height: 80 }],
      ['d', { x: 0, y: 600, width: 100, height: 80 }],
      ['e', { x: 340, y: 600, width: 100, height: 80 }],
    ]);
    const previousRoutes = planAvatarToolEdgeRoutes(edges, previousBoxes);
    const nextBoxes = new Map(previousBoxes);
    nextBoxes.set('b', { x: 340, y: 340, width: 100, height: 80 });
    const nextRoutes = planAvatarToolEdgeRoutes(edges, nextBoxes, {
      previousRoutes,
      previousBoxes,
      changedNodeIds: new Set(['b']),
    });

    expect(nextRoutes.get('a-b')).not.toBe(previousRoutes.get('a-b'));
    expect(nextRoutes.get('a-c')).not.toBe(previousRoutes.get('a-c'));
    expect(nextRoutes.get('d-e')).toBe(previousRoutes.get('d-e'));
    expect(nextRoutes.get('a-b')!.points[0].y).toBeGreaterThan(nextRoutes.get('a-c')!.points[0].y);
  });

  it('keeps a clear facing connection straight without endpoint doglegs', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
    ]));

    expect(routes.get('a-b')?.points).toEqual([
      { x: 100, y: 40 },
      { x: 340, y: 40 },
    ]);
  });

  it('keeps slightly misaligned facing nodes connected by one straight horizontal segment', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 10, width: 100, height: 80 }],
    ])).get('a-b')!;

    expect(route.points).toEqual([
      { x: 100, y: 45 },
      { x: 340, y: 45 },
    ]);
  });

  it('keeps slightly misaligned facing nodes connected by one straight vertical segment', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 10, y: 280, width: 100, height: 80 }],
    ])).get('a-b')!;

    expect(route.points).toEqual([
      { x: 55, y: 80 },
      { x: 55, y: 280 },
    ]);
  });

  it('keeps close slightly misaligned facing nodes straight instead of adding a short dogleg', () => {
    const horizontal = planAvatarToolEdgeRoutes([{
      id: 'horizontal',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 228, height: 104 }],
      ['b', { x: 248, y: 10, width: 228, height: 104 }],
    ])).get('horizontal')!;
    const vertical = planAvatarToolEdgeRoutes([{
      id: 'vertical',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }], new Map([
      ['a', { x: 0, y: 0, width: 228, height: 104 }],
      ['b', { x: 10, y: 124, width: 228, height: 104 }],
    ])).get('vertical')!;

    expect(horizontal.points).toEqual([
      { x: 228, y: 57 },
      { x: 248, y: 57 },
    ]);
    expect(vertical.points).toEqual([
      { x: 119, y: 104 },
      { x: 119, y: 124 },
    ]);
  });

  it('connects very close facing sides directly instead of circling the nodes', () => {
    const horizontal = planAvatarToolEdgeRoutes([{
      id: 'horizontal',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 108, y: 0, width: 100, height: 80 }],
    ])).get('horizontal')!;
    const vertical = planAvatarToolEdgeRoutes([{
      id: 'vertical',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 0, y: 88, width: 100, height: 80 }],
    ])).get('vertical')!;

    expect(horizontal.points).toEqual([{ x: 100, y: 40 }, { x: 108, y: 40 }]);
    expect(vertical.points).toEqual([{ x: 50, y: 80 }, { x: 50, y: 88 }]);
  });

  it('keeps a close offset connection inside the gap between facing nodes', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'offset',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 108, y: 70, width: 100, height: 80 }],
    ])).get('offset')!;

    expect(route.points[0].x).toBe(100);
    expect(route.points[route.points.length - 1].x).toBe(108);
    expect(route.points.every(point => point.x >= 100 && point.x <= 108)).toBe(true);
    expect(Math.max(...route.points.map(point => point.y))
      - Math.min(...route.points.map(point => point.y))).toBeLessThan(70);
  });

  it('uses a compact corner when close perpendicular sides face the same open area', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'corner',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Top,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 108, y: 88, width: 100, height: 80 }],
    ])).get('corner')!;

    expect(route.points[0].x).toBe(100);
    expect(route.points[route.points.length - 1].y).toBe(88);
    expect(route.points).toHaveLength(3);
    expect(Math.max(...route.points.map(point => point.x))).toBeLessThan(158);
  });

  it('automatically chooses the short facing route for close legacy connections', () => {
    const route = planAvatarToolEdgeRoutes([
      { id: 'legacy', source: 'a', target: 'b' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 108, y: 0, width: 100, height: 80 }],
    ])).get('legacy')!;

    expect(route.sourcePosition).toBe(Position.Right);
    expect(route.targetPosition).toBe(Position.Left);
    expect(route.points).toEqual([{ x: 100, y: 40 }, { x: 108, y: 40 }]);
  });

  it.each([1, 8, 30, 59, 60, 61])(
    'keeps a facing route inside the available horizontal gap at %ipx spacing',
    (gap) => {
      const route = planAvatarToolEdgeRoutes([{
        id: `gap-${gap}`,
        source: 'a',
        target: 'b',
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      }], new Map([
        ['a', { x: 0, y: 0, width: 100, height: 80 }],
        ['b', { x: 100 + gap, y: 0, width: 100, height: 80 }],
      ])).get(`gap-${gap}`)!;

      expect(route.points[0]).toEqual({ x: 100, y: 40 });
      expect(route.points[route.points.length - 1]).toEqual({ x: 100 + gap, y: 40 });
      expect(route.points.every(point => point.x >= 100 && point.x <= 100 + gap)).toBe(true);
    },
  );

  it('keeps a visible short connection when facing nodes exactly touch', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'touching',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 100, y: 0, width: 100, height: 80 }],
    ])).get('touching')!;

    expect(route.path).not.toBe('');
    expect(route.points).toEqual([{ x: 100, y: 33 }, { x: 100, y: 47 }]);
  });

  it('separates close reciprocal connections without sending either around the nodes', () => {
    const routes = planAvatarToolEdgeRoutes([
      {
        id: 'forward',
        source: 'a',
        target: 'b',
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      },
      {
        id: 'backward',
        source: 'b',
        target: 'a',
        sourcePosition: Position.Left,
        targetPosition: Position.Right,
      },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 108, y: 0, width: 100, height: 80 }],
    ]));
    const forward = routes.get('forward')!;
    const backward = routes.get('backward')!;

    expect(forward.points.every(point => point.x >= 100 && point.x <= 108)).toBe(true);
    expect(backward.points.every(point => point.x >= 100 && point.x <= 108)).toBe(true);
    expect(forward.points).not.toEqual([...backward.points].reverse());
  });

  it('fans several close facing connections through their shared narrow side', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b', sourcePosition: Position.Right, targetPosition: Position.Left },
      { id: 'a-c', source: 'a', target: 'c', sourcePosition: Position.Right, targetPosition: Position.Left },
      { id: 'a-d', source: 'a', target: 'd', sourcePosition: Position.Right, targetPosition: Position.Left },
    ], new Map([
      ['a', { x: 0, y: 100, width: 100, height: 100 }],
      ['b', { x: 108, y: 0, width: 100, height: 80 }],
      ['c', { x: 108, y: 110, width: 100, height: 80 }],
      ['d', { x: 108, y: 220, width: 100, height: 80 }],
    ]));
    const sourcePorts = ['a-b', 'a-c', 'a-d'].map(id => routes.get(id)!.points[0]);

    expect(new Set(sourcePorts.map(point => point.y)).size).toBe(3);
    expect(['a-b', 'a-c', 'a-d'].every(id => (
      routes.get(id)!.points.every(point => point.x >= 100 && point.x <= 108)
    ))).toBe(true);
  });

  it('moves the landing point along the chosen side when a nearby node blocks its center', () => {
    const blocker = { x: 102, y: 30, width: 4, height: 20 };
    const route = planAvatarToolEdgeRoutes([{
      id: 'blocked-center',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['blocker', blocker],
      ['b', { x: 108, y: 0, width: 100, height: 80 }],
    ])).get('blocked-center')!;
    const crossesBlocker = route.points.slice(1).some((point, index) => {
      const previous = route.points[index];
      if (previous.y === point.y) {
        return previous.y > blocker.y
          && previous.y < blocker.y + blocker.height
          && Math.max(previous.x, point.x) > blocker.x
          && Math.min(previous.x, point.x) < blocker.x + blocker.width;
      }
      return previous.x > blocker.x
        && previous.x < blocker.x + blocker.width
        && Math.max(previous.y, point.y) > blocker.y
        && Math.min(previous.y, point.y) < blocker.y + blocker.height;
    });

    expect(route.sourcePosition).toBe(Position.Right);
    expect(route.targetPosition).toBe(Position.Left);
    expect(route.points[0].y).not.toBe(40);
    expect(crossesBlocker).toBe(false);
  });

  it('keeps every close start/end side pair attached, finite, and orthogonal', () => {
    const sides = [Position.Top, Position.Right, Position.Bottom, Position.Left];
    const sourceBox = { x: 0, y: 0, width: 100, height: 80 };
    const targetBox = { x: 108, y: 0, width: 100, height: 80 };
    const onSide = (
      point: { x: number; y: number },
      box: typeof sourceBox,
      side: Position,
    ) => {
      if (side === Position.Left) return point.x === box.x;
      if (side === Position.Right) return point.x === box.x + box.width;
      if (side === Position.Top) return point.y === box.y;
      return point.y === box.y + box.height;
    };

    sides.forEach((sourcePosition) => sides.forEach((targetPosition) => {
      const route = planAvatarToolEdgeRoutes([{
        id: `${sourcePosition}-${targetPosition}`,
        source: 'a',
        target: 'b',
        sourcePosition,
        targetPosition,
      }], new Map([
        ['a', sourceBox],
        ['b', targetBox],
      ])).get(`${sourcePosition}-${targetPosition}`)!;

      expect(route.sourcePosition).toBe(sourcePosition);
      expect(route.targetPosition).toBe(targetPosition);
      expect(route.path).not.toBe('');
      expect(onSide(route.points[0], sourceBox, sourcePosition)).toBe(true);
      expect(onSide(route.points[route.points.length - 1], targetBox, targetPosition)).toBe(true);
      expect(route.points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y))).toBe(true);
      expect(route.points.slice(1).every((point, index) => (
        point.x === route.points[index].x || point.y === route.points[index].y
      ))).toBe(true);
    }));
  });

  it('never drops a valid connection across overlapping, touching, near, and distant layouts', () => {
    const sides = [Position.Top, Position.Right, Position.Bottom, Position.Left];
    const sourceBox = { x: 0, y: 0, width: 100, height: 80 };
    const targetBoxes = [
      { x: 0, y: 0, width: 100, height: 80 },
      { x: 40, y: 20, width: 100, height: 80 },
      { x: 100, y: 0, width: 100, height: 80 },
      { x: 108, y: 70, width: 100, height: 80 },
      { x: -50, y: -50, width: 220, height: 180 },
      { x: 500, y: -300, width: 100, height: 80 },
    ];

    targetBoxes.forEach((targetBox, layoutIndex) => {
      sides.forEach((sourcePosition) => sides.forEach((targetPosition) => {
        const id = `${layoutIndex}-${sourcePosition}-${targetPosition}`;
        const route = planAvatarToolEdgeRoutes([{
          id,
          source: 'a',
          target: 'b',
          sourcePosition,
          targetPosition,
        }], new Map([
          ['a', sourceBox],
          ['b', targetBox],
        ])).get(id);

        expect(route, id).toBeDefined();
        expect(route?.path, id).not.toBe('');
        expect(route?.sourcePosition, id).toBe(sourcePosition);
        expect(route?.targetPosition, id).toBe(targetPosition);
        expect(route?.points.length, id).toBeGreaterThanOrEqual(2);
        expect(route?.points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y)), id)
          .toBe(true);
        expect(route?.points.slice(1).every((point, index) => (
          point.x === route.points[index].x || point.y === route.points[index].y
        )), id).toBe(true);
      }));
    });
  });

  it('keeps every self-connection side pair attached and orthogonal', () => {
    const sides = [Position.Top, Position.Right, Position.Bottom, Position.Left];
    const box = { x: 100, y: 100, width: 120, height: 80 };

    sides.forEach((sourcePosition) => sides.forEach((targetPosition) => {
      const route = planAvatarToolEdgeRoutes([{
        id: `${sourcePosition}-${targetPosition}`,
        source: 'a',
        target: 'a',
        sourcePosition,
        targetPosition,
      }], new Map([['a', box]])).get(`${sourcePosition}-${targetPosition}`)!;

      expect(route.sourcePosition).toBe(sourcePosition);
      expect(route.targetPosition).toBe(targetPosition);
      expect(route.path).not.toBe('');
      expect(route.points.slice(1).every((point, index) => (
        point.x === route.points[index].x || point.y === route.points[index].y
      ))).toBe(true);
    }));
  });

  it('uses distinct ports for several edges sharing the same node side', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
      { id: 'a-c', source: 'a', target: 'c' },
      { id: 'a-d', source: 'a', target: 'd' },
    ], new Map([
      ['a', { x: 0, y: 120, width: 100, height: 100 }],
      ['b', { x: 380, y: 0, width: 100, height: 80 }],
      ['c', { x: 380, y: 130, width: 100, height: 80 }],
      ['d', { x: 380, y: 260, width: 100, height: 80 }],
    ]));
    const sourcePorts = ['a-b', 'a-c', 'a-d'].map(id => routes.get(id)?.points[0]);

    expect(new Set(sourcePorts.map(point => `${point?.x}:${point?.y}`)).size).toBe(3);
  });

  it('keeps a connection visible when a crowded lane cannot leave the node safely', () => {
    const routes = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
      { id: 'a-c', source: 'a', target: 'c' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 100 }],
      ['nearby', { x: 120, y: 10, width: 20, height: 20 }],
      ['b', { x: 400, y: 0, width: 100, height: 80 }],
      ['c', { x: 400, y: 100, width: 100, height: 80 }],
    ]));

    expect(routes.size).toBe(2);
    expect(routes.get('a-b')?.path).not.toBe('');
    expect(routes.get('a-c')?.path).not.toBe('');
  });

  it('keeps a usable straight run before the first and after the last bend', () => {
    const route = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['blocker', { x: 150, y: 0, width: 100, height: 80 }],
      ['b', { x: 300, y: 0, width: 100, height: 80 }],
    ])).get('a-b')!;
    const first = route.points[0];
    const afterFirst = route.points[1];
    const beforeLast = route.points[route.points.length - 2];
    const last = route.points[route.points.length - 1];
    const firstLength = Math.abs(afterFirst.x - first.x) + Math.abs(afterFirst.y - first.y);
    const lastLength = Math.abs(last.x - beforeLast.x) + Math.abs(last.y - beforeLast.y);

    expect(firstLength).toBeGreaterThanOrEqual(30);
    expect(lastLength).toBeGreaterThanOrEqual(30);
  });

  it('does not choose a side whose endpoint run passes through a nearby node', () => {
    const route = planAvatarToolEdgeRoutes([
      { id: 'a-b', source: 'a', target: 'b' },
    ], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 100 }],
      ['nearby', { x: 102, y: 35, width: 1, height: 30 }],
      ['b', { x: 300, y: 0, width: 100, height: 100 }],
    ])).get('a-b')!;

    expect(route.sourcePosition).not.toBe(Position.Right);
  });

  it('keeps the exact start and end sides selected by the user after nodes move', () => {
    const edge = {
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Top,
      targetPosition: Position.Bottom,
    };
    const initialRoute = planAvatarToolEdgeRoutes([edge], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 340, y: 0, width: 100, height: 80 }],
    ])).get('a-b')!;
    const movedRoute = planAvatarToolEdgeRoutes([edge], new Map([
      ['a', { x: 300, y: 240, width: 100, height: 80 }],
      ['b', { x: 0, y: 0, width: 100, height: 80 }],
    ])).get('a-b')!;

    expect(initialRoute).toMatchObject({
      sourcePosition: Position.Top,
      targetPosition: Position.Bottom,
    });
    expect(initialRoute.points[0].y).toBe(0);
    expect(initialRoute.points[initialRoute.points.length - 1]?.y).toBe(80);
    expect(movedRoute).toMatchObject({
      sourcePosition: Position.Top,
      targetPosition: Position.Bottom,
    });
    expect(movedRoute.points[0].y).toBe(240);
    expect(movedRoute.points[movedRoute.points.length - 1]?.y).toBe(80);
  });

  it('keeps both user-selected sides on a self connection', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'a-a',
      source: 'a',
      target: 'a',
      sourcePosition: Position.Left,
      targetPosition: Position.Bottom,
    }], new Map([
      ['a', { x: 100, y: 100, width: 120, height: 80 }],
    ])).get('a-a')!;

    expect(route.sourcePosition).toBe(Position.Left);
    expect(route.targetPosition).toBe(Position.Bottom);
    expect(route.points[0].x).toBe(100);
    expect(route.points[route.points.length - 1]?.y).toBe(180);
  });

  it('renders the previous bezier shape from the routed boundary points', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'a-b',
      source: 'a',
      target: 'b',
      sourcePosition: Position.Right,
      targetPosition: Position.Top,
    }], new Map([
      ['a', { x: 0, y: 0, width: 100, height: 80 }],
      ['b', { x: 260, y: 150, width: 100, height: 80 }],
    ])).get('a-b')!;

    const path = avatarToolEdgePath(route, 'curved');
    expect(path).toMatch(/^M/);
    expect(path).toContain('C');
    expect(path).toContain(`${route.points[0].x},${route.points[0].y}`);
    expect(path).toContain(`${route.points[route.points.length - 1].x},${route.points[route.points.length - 1].y}`);
    expect(path).not.toContain('NaN');
  });

  it('uses the selected line style for the connection preview', () => {
    const positions = {
      fromX: 100,
      fromY: 80,
      fromPosition: Position.Right,
      toX: 320,
      toY: 220,
      toPosition: Position.Left,
    };
    const orthogonal = avatarToolConnectionPreviewPath('orthogonal', positions);
    const curved = avatarToolConnectionPreviewPath('curved', positions);

    expect(orthogonal).toMatch(/^M/);
    expect(orthogonal).not.toContain('C');
    expect(curved).toMatch(/^M/);
    expect(curved).toContain('C');
    expect(curved).not.toBe(orthogonal);
  });

  it('keeps a curved self connection outside the node instead of collapsing it', () => {
    const route = planAvatarToolEdgeRoutes([{
      id: 'a-a',
      source: 'a',
      target: 'a',
      sourcePosition: Position.Right,
      targetPosition: Position.Right,
    }], new Map([
      ['a', { x: 100, y: 100, width: 120, height: 80 }],
    ])).get('a-a')!;

    const path = avatarToolEdgePath(route, 'curved', true);
    expect(path).toMatch(/^M/);
    expect(path).toContain('Q');
    expect(path).not.toBe(route.path);
    expect(path).not.toContain('NaN');
  });

  it('keeps connections rendered while switching styles and moving a node, and remembers the style', () => {
    const first = render(
      <AvatarToolInteractionEditorProvider>
        <PreparedCanvas />
      </AvatarToolInteractionEditorProvider>,
    );
    const elbowButton = screen.getByRole('button', { name: 'Elbow' });
    const curveButton = screen.getByRole('button', { name: 'Curve' });
    const initialNodeCount = document.querySelectorAll('.react-flow__node').length;
    const initialEdgeCount = document.querySelectorAll('.react-flow__edge-path').length;

    expect(initialEdgeCount).toBeGreaterThan(0);
    expect(elbowButton).toHaveAttribute('aria-pressed', 'true');
    expect(curveButton).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(curveButton);

    expect(curveButton).toHaveAttribute('aria-pressed', 'true');
    expect(elbowButton).toHaveAttribute('aria-pressed', 'false');
    expect(document.querySelectorAll('.react-flow__node')).toHaveLength(initialNodeCount);
    expect(document.querySelectorAll('.react-flow__edge-path')).toHaveLength(initialEdgeCount);
    expect(window.localStorage.getItem('neko.avatarToolEditor.edgeStyle.v1')).toBe('curved');

    const connectedNode = document.querySelector<HTMLElement>('.react-flow__node[data-id="ix-a"]')!;
    fireEvent.click(connectedNode);
    fireEvent.keyDown(connectedNode, { key: 'ArrowRight' });
    expect(connectedNode.style.transform).toContain('translate(5px,0px)');
    expect(document.querySelectorAll('.react-flow__edge-path')).toHaveLength(initialEdgeCount);

    first.unmount();
    render(
      <AvatarToolInteractionEditorProvider>
        <PreparedCanvas />
      </AvatarToolInteractionEditorProvider>,
    );
    expect(screen.getByRole('button', { name: 'Curve' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps movement free and aligns a node only when its dropped position is committed', () => {
    expect(snapAvatarToolNodePosition({ x: 104, y: 206 })).toEqual({ x: 100, y: 210 });
    expect(snapAvatarToolNodePosition({ x: 110, y: 220 })).toEqual({ x: 110, y: 220 });
  });

  it('uses a narrower edge hit target and renders hover or selection feedback', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <PreparedCanvas />
      </AvatarToolInteractionEditorProvider>,
    );

    const edgeGroups = [...document.querySelectorAll('.react-flow__edge')];
    expect(edgeGroups.length).toBeGreaterThan(0);
    edgeGroups.forEach((edge) => {
      expect(edge.querySelector('.react-flow__edge-interaction'))
        .toHaveAttribute('stroke-width', '12');
      expect(edge.querySelector('.avatar-tool-edge-feedback')).toBeInTheDocument();
    });
    expect(document.querySelector('.avatar-tool-edge-feedback.is-selected')).toBeInTheDocument();
  });

  it('shows the real initial image entry, continuous edge boundaries, and one movable overview dock', async () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <PreparedCanvas />
      </AvatarToolInteractionEditorProvider>,
    );

    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zoom out' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fit view' })).toBeInTheDocument();
    expect(screen.getByLabelText('Interaction overview')).toBeInTheDocument();
    expect(screen.getByLabelText('Interaction overview').querySelectorAll('.react-flow__minimap-node'))
      .toHaveLength(5);
    expect(screen.queryByText('Overview')).not.toBeInTheDocument();
    expect(document.querySelector('.avatar-tool-overview-collapse')).toBeInTheDocument();
    expect(document.querySelector('.react-flow__node[data-id="avatar-tool-initial-image"]'))
      .toHaveTextContent('Initial image');
    expect(document.querySelector('.react-flow__node[data-id="avatar-tool-initial-image"]'))
      .toHaveTextContent('The interaction flow starts from this image');
    const interactionNode = document.querySelector('.react-flow__node[data-id="ix-a"]');
    expect(interactionNode?.querySelectorAll('.avatar-tool-connection-boundary')).toHaveLength(4);
    expect(interactionNode?.querySelector('[aria-label="Top edge for connections"]')).toBeInTheDocument();
    expect(interactionNode?.querySelector('[aria-label="Right edge for connections"]')).toBeInTheDocument();
    expect(interactionNode?.querySelector('[aria-label="Bottom edge for connections"]')).toBeInTheDocument();
    expect(interactionNode?.querySelector('[aria-label="Left edge for connections"]')).toBeInTheDocument();
    expect(document.querySelector('.avatar-tool-interaction-handle')).not.toBeInTheDocument();
    expect(document.querySelector('.react-flow__node[data-id="ix-a"]')).toHaveAttribute('tabindex', '0');
    expect(document.querySelector('[id^="react-flow__edge-desc-"]')).toHaveTextContent(
      'Press Enter to select this connection. Press Delete to remove it.',
    );
    expect(screen.queryByRole('group', { name: 'Overview position' })).not.toBeInTheDocument();
    const positionTrigger = screen.getByRole('button', { name: 'Overview position' });
    fireEvent.click(positionTrigger);
    const positionOptions = screen.getByRole('group', { name: 'Overview position' });
    expect(positionOptions).toBeInTheDocument();
    const topLeftOption = screen.getByRole('button', { name: 'Move overview to top left' });
    fireEvent.keyDown(topLeftOption, { key: 'Escape' });
    expect(screen.queryByRole('group', { name: 'Overview position' })).not.toBeInTheDocument();
    expect(positionTrigger).toHaveFocus();
    fireEvent.click(positionTrigger);
    fireEvent.click(screen.getByRole('button', { name: 'Move overview to top left' }));
    expect(screen.getByLabelText('Interaction overview').closest('.avatar-tool-overview-dock'))
      .toHaveClass('top', 'left');
    fireEvent.click(screen.getByRole('button', { name: 'Hide overview' }));
    expect(screen.queryByLabelText('Interaction overview')).not.toBeInTheDocument();
    const showOverview = screen.getByRole('button', { name: 'Show overview' });
    expect(showOverview.querySelector('span')).toBeNull();
    expect(showOverview.closest('.avatar-tool-overview-dock'))
      .toHaveClass('top', 'left', 'is-collapsed');
    await waitFor(() => expect(showOverview).toHaveFocus());
    fireEvent.click(screen.getByRole('button', { name: 'Show overview' }));
    expect(screen.getByLabelText('Interaction overview').closest('.avatar-tool-overview-dock'))
      .toHaveClass('top', 'left', 'is-open');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Overview position' })).toHaveFocus());
  });
});
