import {
  avatarToolInteractionEditorReducer,
  avatarToolConnectionSideFromHandleId,
  buildLocalAvatarToolImageInteractions,
  createAvatarToolInteractionEditorState,
  createAvatarToolInteractionPresetState,
  duplicateAvatarToolInteractionDraft,
  getAvatarToolInteractionImageReferences,
  getAvatarToolInteractionPresetRequirements,
  validateAvatarToolInteractionGraph,
  type AvatarToolInteractionEditorState,
} from './avatarToolInteractionEditorModel';
import type { LocalAvatarToolDetail } from './localTools';

const IMAGE_A = 'img-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' as const;
const IMAGE_B = 'img-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' as const;
const IMAGE_C = 'img-cccccccc-cccc-4ccc-8ccc-cccccccccccc' as const;

const DETAIL: LocalAvatarToolDetail = {
  id: 'local-12345678-1234-4123-8123-123456789abc',
  revision: '2-100',
  name: 'Loop',
  changeMode: 'press-swap',
  defaultImage: { resource: 'default.png', url: '/default.png' },
  changeItems: [{ resource: 'change-000.png', url: '/change.png', meaning: 'change' }],
};

function standardGraph(): AvatarToolInteractionEditorState {
  return {
    items: [
      {
        id: 'ix-click-1',
        kind: 'mouse-click',
        position: { x: 0, y: 0 },
        press: { kind: 'show', imageId: IMAGE_B },
        release: { kind: 'show', imageId: IMAGE_C },
      },
      {
        id: 'ix-delay-1',
        kind: 'after',
        position: { x: 260, y: 0 },
        delayMs: '800',
        complete: { kind: 'show', imageId: IMAGE_A },
      },
      {
        id: 'ix-click-2',
        kind: 'mouse-click',
        position: { x: 260, y: 180 },
        press: { kind: 'keep' },
        release: { kind: 'keep' },
      },
    ],
    links: [
      {
        id: 'link-1-2', from: 'ix-click-1', to: 'ix-delay-1', sourceSide: 'right', targetSide: 'left',
      },
      {
        id: 'link-1-3', from: 'ix-click-1', to: 'ix-click-2', sourceSide: 'bottom', targetSide: 'top',
      },
      {
        id: 'link-2-1', from: 'ix-delay-1', to: 'ix-click-1', sourceSide: 'left', targetSide: 'right',
      },
    ],
    initialImageTargetIds: ['ix-click-1'],
    initialImageLinkSides: {
      'ix-click-1': { sourceSide: 'right', targetSide: 'left' },
    },
    initialImagePosition: { x: -160, y: 160 },
    selectedInteractionId: null,
    selectedLinkId: null,
    selectedInitialLinkTargetId: null,
  };
}

describe('avatar tool interaction editor model', () => {
  it('creates independent ordinary v3 drafts from the reusable presets', () => {
    const first = createAvatarToolInteractionPresetState({
      kind: 'click-advance',
      initialImageId: IMAGE_B,
      targetImageIds: [IMAGE_A, IMAGE_C],
      initialImagePosition: { x: 10, y: 20 },
    });
    const second = createAvatarToolInteractionPresetState({
      kind: 'click-advance',
      initialImageId: IMAGE_B,
      targetImageIds: [IMAGE_A, IMAGE_C],
      initialImagePosition: { x: 10, y: 20 },
    });

    expect(first.items.map(item => item.kind === 'mouse-click' ? item.release : null)).toEqual([
      { kind: 'show', imageId: IMAGE_A },
      { kind: 'show', imageId: IMAGE_C },
    ]);
    expect(first.links.map(link => [link.from, link.to])).toEqual([
      [first.items[0].id, first.items[1].id],
    ]);
    expect(first.items.map(item => item.id)).not.toEqual(second.items.map(item => item.id));
    expect(first.links.map(link => link.id)).not.toEqual(second.links.map(link => link.id));

    const edited = avatarToolInteractionEditorReducer(first, {
      type: 'update-click-action',
      interactionId: first.items[0].id,
      timing: 'press',
      action: { kind: 'show', imageId: IMAGE_C },
    });
    expect(edited.items[0]).toMatchObject({ press: { kind: 'show', imageId: IMAGE_C } });
    expect(second.items[0]).toMatchObject({ press: { kind: 'keep' } });
  });

  it('uses only one target for the press-swap preset and restores the initial image', () => {
    const state = createAvatarToolInteractionPresetState({
      kind: 'press-swap',
      initialImageId: IMAGE_B,
      targetImageIds: [IMAGE_A, IMAGE_C],
    });

    expect(state.items).toHaveLength(1);
    expect(state.items[0]).toMatchObject({
      kind: 'mouse-click',
      press: { kind: 'show', imageId: IMAGE_A },
      release: { kind: 'show', imageId: IMAGE_B },
    });
    expect(state.links).toEqual([expect.objectContaining({
      from: state.items[0].id,
      to: state.items[0].id,
    })]);
  });

  it('creates connected preset graphs without requiring any image', () => {
    const pressSwap = createAvatarToolInteractionPresetState({
      kind: 'press-swap',
    });
    const clickAdvance = createAvatarToolInteractionPresetState({
      kind: 'click-advance',
    });
    const cycleStop = createAvatarToolInteractionPresetState({
      kind: 'cycle-stop',
    });

    expect(pressSwap.items).toHaveLength(1);
    expect(pressSwap.items[0]).toMatchObject({
      press: { kind: 'keep' },
      release: { kind: 'keep' },
    });
    expect(clickAdvance.items).toHaveLength(3);
    expect(clickAdvance.items).toEqual([
      expect.objectContaining({ press: { kind: 'keep' }, release: { kind: 'keep' } }),
      expect.objectContaining({ press: { kind: 'keep' }, release: { kind: 'keep' } }),
      expect.objectContaining({ press: { kind: 'keep' }, release: { kind: 'keep' } }),
    ]);
    expect(pressSwap.initialImageTargetIds).toEqual([pressSwap.items[0].id]);
    expect(clickAdvance.initialImageTargetIds).toEqual([clickAdvance.items[0].id]);
    expect(pressSwap.links[0]).toMatchObject({
      from: pressSwap.items[0].id,
      to: pressSwap.items[0].id,
    });
    expect(clickAdvance.links).toEqual([
      expect.objectContaining({
        from: clickAdvance.items[0].id,
        to: clickAdvance.items[1].id,
      }),
      expect.objectContaining({
        from: clickAdvance.items[1].id,
        to: clickAdvance.items[2].id,
      }),
    ]);
    expect(getAvatarToolInteractionPresetRequirements('click-advance')).toEqual({
      interactionCount: 3,
      totalLinkCount: 3,
    });

    const [firstDelay, secondDelay, thirdDelay, holdClick, resumeDelay] = cycleStop.items;
    expect(cycleStop.items).toHaveLength(5);
    expect(cycleStop.items.map(item => item.position)).toEqual([
      { x: 380, y: 180 },
      { x: 680, y: 180 },
      { x: 980, y: 180 },
      { x: 680, y: 400 },
      { x: 680, y: 620 },
    ]);
    expect(cycleStop.items.slice(0, 3)).toEqual([
      expect.objectContaining({ kind: 'after', delayMs: '800', complete: { kind: 'keep' } }),
      expect.objectContaining({ kind: 'after', delayMs: '800', complete: { kind: 'keep' } }),
      expect.objectContaining({ kind: 'after', delayMs: '800', complete: { kind: 'keep' } }),
    ]);
    expect(holdClick).toMatchObject({
      kind: 'mouse-click',
      press: { kind: 'keep' },
      release: { kind: 'keep' },
    });
    expect(resumeDelay).toMatchObject({
      kind: 'after',
      delayMs: '800',
      complete: { kind: 'keep' },
    });
    expect(cycleStop.initialImageTargetIds).toEqual([firstDelay.id]);
    const successors = (interactionId: string) => cycleStop.links
      .filter(link => link.from === interactionId)
      .map(link => link.to);
    expect(successors(firstDelay.id)).toEqual([secondDelay.id, holdClick.id]);
    expect(successors(secondDelay.id)).toEqual([thirdDelay.id, holdClick.id]);
    expect(successors(thirdDelay.id)).toEqual([firstDelay.id, holdClick.id]);
    expect(successors(holdClick.id)).toEqual([resumeDelay.id]);
    expect(successors(resumeDelay.id)).toEqual([firstDelay.id]);
    expect(cycleStop.links.map(link => [
      link.from,
      link.to,
      link.sourceSide,
      link.targetSide,
    ])).toEqual([
      [firstDelay.id, secondDelay.id, 'right', 'left'],
      [firstDelay.id, holdClick.id, 'bottom', 'left'],
      [secondDelay.id, thirdDelay.id, 'right', 'left'],
      [secondDelay.id, holdClick.id, 'bottom', 'top'],
      [thirdDelay.id, firstDelay.id, 'top', 'top'],
      [thirdDelay.id, holdClick.id, 'bottom', 'right'],
      [holdClick.id, resumeDelay.id, 'bottom', 'top'],
      [resumeDelay.id, firstDelay.id, 'left', 'bottom'],
    ]);
    expect(validateAvatarToolInteractionGraph(cycleStop, [])).toEqual([]);
    expect(buildLocalAvatarToolImageInteractions(cycleStop)).not.toHaveProperty('presetId');
    expect(getAvatarToolInteractionPresetRequirements('cycle-stop')).toEqual({
      interactionCount: 5,
      totalLinkCount: 9,
    });
  });

  it('validates optional interaction names with the shared Unicode-aware rule', () => {
    const state = standardGraph();
    state.items[0] = { ...state.items[0], name: '𠮷'.repeat(20) };
    expect(validateAvatarToolInteractionGraph(
      state,
      [IMAGE_A, IMAGE_B, IMAGE_C],
      item => item.name || item.id,
      600_000,
      20,
    ).some(issue => issue.field === 'name')).toBe(false);

    state.items[0] = { ...state.items[0], name: '𠮷'.repeat(21) };
    expect(validateAvatarToolInteractionGraph(
      state,
      [IMAGE_A, IMAGE_B, IMAGE_C],
      item => item.name || item.id,
      600_000,
      20,
    )).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'name-too-long', interactionId: state.items[0].id }),
    ]));

    state.items[0] = { ...state.items[0], name: 'click!' };
    expect(validateAvatarToolInteractionGraph(
      state,
      [IMAGE_A, IMAGE_B, IMAGE_C],
      item => item.name || item.id,
      600_000,
      20,
    )).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'name-invalid', interactionId: state.items[0].id }),
    ]));
  });

  it('reopens v3 graph semantics and preserves the user-selected connection sides', () => {
    const state = createAvatarToolInteractionEditorState({
      recordVersion: 3,
      id: DETAIL.id,
      revision: '3-100',
      name: 'Flow',
      images: [{ id: 'img-idle', name: '', resource: 'image-000.png', url: '/idle.png', meaning: '' }],
      initialImageId: 'img-idle',
      imageInteractions: {
        initialImagePosition: { x: 12, y: 34 },
        initialLinks: [{ to: 'ix-delay', sourceSide: 'bottom', targetSide: 'top' }],
        items: [{
          id: 'ix-delay',
          name: 'Pause',
          trigger: { kind: 'after', delayMs: 1200 },
          actions: { complete: { kind: 'keep' } },
          editorPosition: { x: 120, y: 260 },
        }],
        links: [{ from: 'ix-delay', to: 'ix-delay', sourceSide: 'left', targetSide: 'bottom' }],
      },
    });

    expect(state.initialImagePosition).toEqual({ x: 12, y: 34 });
    expect(state.initialImageLinkSides['ix-delay']).toEqual({ sourceSide: 'bottom', targetSide: 'top' });
    expect(state.items[0]).toMatchObject({ name: 'Pause', kind: 'after', delayMs: '1200', complete: { kind: 'keep' } });
    expect(state.links[0]).toMatchObject({
      id: 'link-v3-000', sourceSide: 'left', targetSide: 'bottom',
    });
  });

  it('projects the v2 press-swap behavior into one complete self-connected click', () => {
    expect(createAvatarToolInteractionEditorState(DETAIL)).toEqual({
      items: [{
        id: 'ix-v2-press-swap',
        name: '',
        kind: 'mouse-click',
        position: { x: 220, y: 180 },
        press: { kind: 'show', imageId: 'img-v2-change-000' },
        release: { kind: 'show', imageId: 'img-v2-default' },
      }],
      links: [{
        id: 'link-v2-press-swap-loop',
        from: 'ix-v2-press-swap',
        to: 'ix-v2-press-swap',
        sourceSide: 'right',
        targetSide: 'right',
      }],
      initialImageTargetIds: ['ix-v2-press-swap'],
      initialImageLinkSides: {
        'ix-v2-press-swap': { sourceSide: 'right', targetSide: 'left' },
      },
      initialImagePosition: { x: -100, y: 180 },
      selectedInteractionId: null,
      selectedLinkId: null,
      selectedInitialLinkTargetId: null,
    });
  });

  it('projects every v2 click-advance image into one finite ordered click chain', () => {
    const state = createAvatarToolInteractionEditorState({
      ...DETAIL,
      changeMode: 'click-advance',
      changeItems: [
        { resource: 'change-000.png', url: '/change-000.png', meaning: 'A' },
        { resource: 'change-001.png', url: '/change-001.png', meaning: 'B' },
        { resource: 'change-002.png', url: '/change-002.png', meaning: 'C' },
      ],
    });

    expect(state.items.map(item => ({
      id: item.id,
      kind: item.kind,
      press: item.kind === 'mouse-click' ? item.press : undefined,
      release: item.kind === 'mouse-click' ? item.release : undefined,
    }))).toEqual([
      {
        id: 'ix-v2-click-advance-000',
        kind: 'mouse-click',
        press: { kind: 'keep' },
        release: { kind: 'show', imageId: 'img-v2-change-000' },
      },
      {
        id: 'ix-v2-click-advance-001',
        kind: 'mouse-click',
        press: { kind: 'keep' },
        release: { kind: 'show', imageId: 'img-v2-change-001' },
      },
      {
        id: 'ix-v2-click-advance-002',
        kind: 'mouse-click',
        press: { kind: 'keep' },
        release: { kind: 'show', imageId: 'img-v2-change-002' },
      },
    ]);
    expect(state.links).toEqual([
      {
        id: 'link-v2-click-advance-000',
        from: 'ix-v2-click-advance-000',
        to: 'ix-v2-click-advance-001',
        sourceSide: 'right',
        targetSide: 'left',
      },
      {
        id: 'link-v2-click-advance-001',
        from: 'ix-v2-click-advance-001',
        to: 'ix-v2-click-advance-002',
        sourceSide: 'right',
        targetSide: 'left',
      },
    ]);
    expect(state.initialImageTargetIds).toEqual(['ix-v2-click-advance-000']);
  });

  it('edits the initial waiting set only through connections from the initial image', () => {
    const initial = { ...standardGraph(), initialImageTargetIds: [] };
    const connected = avatarToolInteractionEditorReducer(initial, {
      type: 'connect-initial-image',
      interactionId: 'ix-click-1',
      sourceSide: 'bottom',
      targetSide: 'left',
    });
    expect(connected.initialImageTargetIds).toEqual(['ix-click-1']);
    expect(connected.initialImageLinkSides['ix-click-1']).toEqual({
      sourceSide: 'bottom',
      targetSide: 'left',
    });
    expect(connected.selectedInitialLinkTargetId).toBe('ix-click-1');

    const moved = avatarToolInteractionEditorReducer(connected, {
      type: 'move-initial-image',
      position: { x: 90, y: 120 },
    });
    expect(moved.initialImagePosition).toEqual({ x: 90, y: 120 });
    expect(moved.initialImageTargetIds).toEqual(['ix-click-1']);

    const disconnected = avatarToolInteractionEditorReducer(moved, {
      type: 'remove-initial-link',
      interactionId: 'ix-click-1',
    });
    expect(disconnected.initialImageTargetIds).toEqual([]);
    expect(disconnected.initialImageLinkSides).toEqual({});
    expect(disconnected.selectedInitialLinkTargetId).toBeNull();
  });

  it('keeps user-selected sides on normal links and validates handle ids', () => {
    const initial = { ...standardGraph(), links: [] };
    const connected = avatarToolInteractionEditorReducer(initial, {
      type: 'connect',
      link: {
        id: 'link-user-sides',
        from: 'ix-click-1',
        to: 'ix-delay-1',
        sourceSide: 'top',
        targetSide: 'bottom',
      },
    });

    expect(connected.links[0]).toMatchObject({ sourceSide: 'top', targetSide: 'bottom' });
    expect(avatarToolConnectionSideFromHandleId('edge-left')).toBe('left');
    expect(avatarToolConnectionSideFromHandleId('edge-diagonal')).toBeUndefined();
    expect(avatarToolConnectionSideFromHandleId(null)).toBeUndefined();
  });

  it('does not accept or infer a connection whose chosen sides are missing', () => {
    const complete = buildLocalAvatarToolImageInteractions(standardGraph());
    expect(complete?.initialLinks[0]).toMatchObject({ sourceSide: 'right', targetSide: 'left' });
    expect(complete?.links[1]).toMatchObject({ sourceSide: 'bottom', targetSide: 'top' });

    const empty = {
      ...standardGraph(),
      links: [],
      initialImageTargetIds: [],
      initialImageLinkSides: {},
    };
    const initialWithoutSides = avatarToolInteractionEditorReducer(empty, {
      type: 'connect-initial-image',
      interactionId: 'ix-click-1',
    } as unknown as Parameters<typeof avatarToolInteractionEditorReducer>[1]);
    expect(initialWithoutSides.initialImageTargetIds).toEqual([]);

    const linkWithoutSides = avatarToolInteractionEditorReducer(empty, {
      type: 'connect',
      link: { id: 'link-missing-sides', from: 'ix-click-1', to: 'ix-delay-1' },
    } as unknown as Parameters<typeof avatarToolInteractionEditorReducer>[1]);
    expect(linkWithoutSides.links).toEqual([]);

    const malformedInitial = standardGraph();
    malformedInitial.initialImageLinkSides = {};
    expect(buildLocalAvatarToolImageInteractions(malformedInitial)).toBeNull();

    const malformedLink = standardGraph();
    malformedLink.links[0] = {
      ...malformedLink.links[0],
      sourceSide: undefined,
      targetSide: undefined,
    } as unknown as typeof malformedLink.links[number];
    expect(buildLocalAvatarToolImageInteractions(malformedLink)).toBeNull();
  });

  it('treats a complete node as one unit when copying and deleting', () => {
    const initial = standardGraph();
    const source = initial.items[0];
    const duplicate = { ...source, id: 'ix-click-copy' as const, position: { x: 44, y: 44 } };
    const copied = avatarToolInteractionEditorReducer(initial, {
      type: 'duplicate-interaction',
      sourceId: source.id,
      duplicate,
    });

    expect(copied.items[copied.items.length - 1]).toEqual(duplicate);
    expect(copied.links).toEqual(initial.links);
    expect(copied.initialImageTargetIds).toEqual(initial.initialImageTargetIds);

    const removed = avatarToolInteractionEditorReducer(copied, {
      type: 'remove-interaction',
      interactionId: 'ix-click-1',
    });
    expect(removed.items.some(item => item.id === 'ix-click-1')).toBe(false);
    expect(removed.links).toEqual([]);
    expect(removed.initialImageTargetIds).toEqual([]);
  });

  it('renames an interaction without changing its type or stable id', () => {
    const initial = standardGraph();
    const renamed = avatarToolInteractionEditorReducer(initial, {
      type: 'update-name',
      interactionId: 'ix-click-1',
      name: 'Wave hello',
    });

    expect(renamed.items[0]).toMatchObject({
      id: 'ix-click-1',
      name: 'Wave hello',
      kind: 'mouse-click',
    });
    expect(renamed.items[1]).toBe(initial.items[1]);
  });

  it('offsets a duplicate far enough to keep both complete nodes readable', () => {
    const source = { ...standardGraph().items[0], name: 'Wave hello' };
    const duplicate = duplicateAvatarToolInteractionDraft(source);
    expect(duplicate.position).toEqual({ x: 40, y: 140 });
    expect(duplicate).toMatchObject({
      name: '',
      kind: 'mouse-click',
      press: source.kind === 'mouse-click' ? source.press : undefined,
      release: source.kind === 'mouse-click' ? source.release : undefined,
    });
  });

  it('derives named image reference fields from the same graph state', () => {
    expect(getAvatarToolInteractionImageReferences(standardGraph())).toEqual({
      [IMAGE_A]: [{ interactionId: 'ix-delay-1', field: 'complete' }],
      [IMAGE_B]: [{ interactionId: 'ix-click-1', field: 'press' }],
      [IMAGE_C]: [{ interactionId: 'ix-click-1', field: 'release' }],
    });
  });

  it('accepts a delayed interaction that keeps the current image without creating an image reference', () => {
    const graph = standardGraph();
    const delay = graph.items.find(item => item.id === 'ix-delay-1');
    if (!delay || delay.kind !== 'after') throw new Error('missing delay fixture');
    delay.complete = { kind: 'keep' };

    expect(validateAvatarToolInteractionGraph(graph, [IMAGE_B, IMAGE_C]))
      .toEqual([]);
    expect(getAvatarToolInteractionImageReferences(graph)).toEqual({
      [IMAGE_B]: [{ interactionId: 'ix-click-1', field: 'press' }],
      [IMAGE_C]: [{ interactionId: 'ix-click-1', field: 'release' }],
    });
  });

  it('accepts the standard graph including its back edge and terminal keep-image click', () => {
    expect(validateAvatarToolInteractionGraph(
      standardGraph(),
      [IMAGE_A, IMAGE_B, IMAGE_C],
    )).toEqual([]);
  });

  it('rejects interaction names that only differ by case or surrounding spaces', () => {
    const graph = standardGraph();
    graph.items[0].name = 'Wave hello';
    graph.items[1].name = '  wave HELLO  ';

    const duplicateNameIssues = validateAvatarToolInteractionGraph(
      graph,
      [IMAGE_A, IMAGE_B, IMAGE_C],
      item => item.name?.trim() || item.id,
    ).filter(issue => issue.code === 'duplicate-name');

    expect(duplicateNameIssues.map(issue => issue.interactionId)).toEqual([
      'ix-click-1',
      'ix-delay-1',
    ]);
    expect(duplicateNameIssues.every(issue => issue.field === 'name')).toBe(true);
  });

  it('marks unreachable nodes and indistinguishable triggers without rejecting cycles', () => {
    const graph = standardGraph();
    graph.initialImageTargetIds = ['ix-click-1', 'ix-click-2'];
    graph.items.push(
      {
        id: 'ix-delay-2',
        kind: 'after',
        position: { x: 520, y: 0 },
        delayMs: '800',
        complete: { kind: 'show', imageId: IMAGE_B },
      },
      {
        id: 'ix-orphan',
        kind: 'mouse-click',
        position: { x: 700, y: 0 },
        press: { kind: 'keep' },
        release: { kind: 'keep' },
      },
    );
    graph.links.push({
      id: 'link-1-4',
      from: 'ix-click-1',
      to: 'ix-delay-2',
      sourceSide: 'right',
      targetSide: 'left',
    });

    const codes = validateAvatarToolInteractionGraph(graph, [IMAGE_A, IMAGE_B, IMAGE_C])
      .map(issue => issue.code);
    expect(codes).toContain('ambiguous-click');
    expect(codes).toContain('ambiguous-delay');
    expect(codes).toContain('unreachable');
    expect(codes).not.toContain('link-endpoint-missing');
  });
});
