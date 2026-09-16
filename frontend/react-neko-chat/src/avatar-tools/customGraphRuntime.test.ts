import type { CustomGraphProfile } from './catalog';
import { createCustomGraphRuntime } from './customGraphRuntime';
import {
  buildLocalAvatarToolImageInteractions,
  createAvatarToolInteractionPresetState,
} from './avatarToolInteractionEditorModel';

function profile(): CustomGraphProfile {
  return {
    kind: 'custom-graph',
    revision: '3-123',
    images: [
      { id: 'img-a', frameIndex: 0, hasMeaning: true },
      { id: 'img-b', frameIndex: 1, hasMeaning: false },
      { id: 'img-c', frameIndex: 2, hasMeaning: true },
    ],
    initialImageId: 'img-a',
    initialInteractionIds: ['ix-click'],
    interactions: [
      {
        id: 'ix-click',
        trigger: { kind: 'mouse-click' },
        actions: {
          press: { kind: 'show', imageId: 'img-b' },
          release: { kind: 'show', imageId: 'img-c' },
        },
      },
      {
        id: 'ix-delay',
        trigger: { kind: 'after', delayMs: 800 },
        actions: { complete: { kind: 'show', imageId: 'img-a' } },
      },
      {
        id: 'ix-exit',
        trigger: { kind: 'mouse-click' },
        actions: { press: { kind: 'keep' }, release: { kind: 'keep' } },
      },
    ],
    links: [
      { from: 'ix-click', to: 'ix-delay' },
      { from: 'ix-delay', to: 'ix-click' },
      { from: 'ix-delay', to: 'ix-exit' },
    ],
    burst: {
      key: 'fixture', windowMs: 1800, rapidThreshold: 3,
      normalIntensity: 'normal', rapidIntensity: 'rapid',
    },
    touchZone: 'release',
    touchZones: ['ear', 'head', 'face', 'body'],
  };
}

function scheduler() {
  let now = 0;
  let id = 0;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const runDue = () => {
    while (true) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.at <= now)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!due) return;
      timers.delete(due[0]);
      due[1].callback();
    }
  };
  return {
    api: {
      now: () => now,
      setTimeout(callback: () => void, delayMs: number) {
        const timerId = ++id;
        timers.set(timerId, { at: now + delayMs, callback });
        return timerId;
      },
      clearTimeout(timerId: number) { timers.delete(timerId); },
    },
    advance(delayMs: number) {
      now += delayMs;
      runDue();
    },
    elapse(delayMs: number) { now += delayMs; },
    get size() { return timers.size; },
  };
}

describe('custom graph runtime', () => {
  it('runs the visible A to B to C to delayed A path and rebuilds the waiting position', () => {
    const clock = scheduler();
    const changes: Array<[string, number]> = [];
    const runtime = createCustomGraphRuntime(profile(), {
      scheduler: clock.api,
      onImageChange: (id, frame) => changes.push([id, frame]),
    });

    expect(runtime.getSnapshot().currentImageId).toBe('img-a');
    expect(runtime.beginClick()).toBe(true);
    expect(runtime.getSnapshot().currentImageId).toBe('img-b');
    expect(runtime.completeClick()).toMatchObject({ capturedImageId: 'img-a', currentImageId: 'img-c' });
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual(['ix-delay']);
    clock.advance(800);
    expect(runtime.getSnapshot()).toMatchObject({
      currentImageId: 'img-a',
      waitingInteractionIds: ['ix-click', 'ix-exit'],
    });
    expect(changes).toEqual([['img-b', 1], ['img-c', 2], ['img-a', 0]]);
  });

  it('holds an expired sibling delay behind a click and lets normal release win', () => {
    const clock = scheduler();
    const source = profile();
    source.initialInteractionIds = ['ix-click', 'ix-delay'];
    source.links = [];
    const runtime = createCustomGraphRuntime(source, { scheduler: clock.api, onImageChange: () => {} });

    expect(runtime.beginClick()).toBe(true);
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-b');
    expect(runtime.completeClick()).toMatchObject({ currentImageId: 'img-c' });
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual([]);
  });

  it('restores the pressed image on cancellation and then resolves an already-due delay', () => {
    const clock = scheduler();
    const source = profile();
    source.initialInteractionIds = ['ix-click', 'ix-delay'];
    source.links = [];
    const changes: string[] = [];
    const runtime = createCustomGraphRuntime(source, {
      scheduler: clock.api,
      onImageChange: id => changes.push(id),
    });

    runtime.beginClick();
    clock.advance(800);
    expect(runtime.cancelClick()).toBe(true);
    expect(runtime.getSnapshot()).toMatchObject({ currentImageId: 'img-a', waitingInteractionIds: [] });
    expect(changes).toEqual(['img-b', 'img-a']);
  });

  it('settles elapsed delays even when their timer callback has not run yet', () => {
    const clock = scheduler();
    const source = profile();
    source.initialInteractionIds = ['ix-click', 'ix-delay'];
    source.links = [];
    source.interactions[1].actions = { complete: { kind: 'show', imageId: 'img-c' } };
    const runtime = createCustomGraphRuntime(source, { scheduler: clock.api, onImageChange: () => {} });

    clock.elapse(800);
    expect(runtime.beginClick()).toBe(false);
    expect(runtime.getSnapshot()).toMatchObject({ currentImageId: 'img-c', waitingInteractionIds: [] });
    expect(clock.size).toBe(0);
  });

  it('stops at a terminal interaction and destroy invalidates every pending timeout', () => {
    const clock = scheduler();
    const source = profile();
    source.initialInteractionIds = ['ix-exit'];
    source.links = [];
    const runtime = createCustomGraphRuntime(source, { scheduler: clock.api, onImageChange: () => {} });
    runtime.beginClick();
    runtime.completeClick();
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual([]);

    const delayed = createCustomGraphRuntime(profile(), { scheduler: clock.api, onImageChange: () => {} });
    delayed.beginClick();
    delayed.completeClick();
    expect(clock.size).toBe(1);
    delayed.destroy();
    expect(clock.size).toBe(0);
  });

  it('holds the clicked frame for one delay before the cycle-stop preset resumes', () => {
    const editorState = createAvatarToolInteractionPresetState({ kind: 'cycle-stop' });
    const delayItems = editorState.items.filter(item => item.kind === 'after');
    const [firstDelay, secondDelay, thirdDelay, resumeDelay] = delayItems;
    firstDelay.complete = { kind: 'show', imageId: 'img-b' };
    secondDelay.complete = { kind: 'show', imageId: 'img-c' };
    thirdDelay.complete = { kind: 'show', imageId: 'img-a' };
    const graph = buildLocalAvatarToolImageInteractions(editorState);
    if (!graph) throw new Error('cycle-stop preset must build a graph');
    const source = profile();
    source.initialInteractionIds = graph.initialLinks.map(link => link.to);
    source.interactions = graph.items.map(item => ({
      id: item.id,
      trigger: item.trigger,
      actions: item.actions,
    }));
    source.links = graph.links.map(link => ({ from: link.from, to: link.to }));

    const clock = scheduler();
    const runtime = createCustomGraphRuntime(source, {
      scheduler: clock.api,
      onImageChange: () => undefined,
    });

    expect(runtime.beginClick()).toBe(false);
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-b');
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-c');
    expect(runtime.beginClick()).toBe(true);
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-c');
    expect(runtime.completeClick()).toMatchObject({
      capturedImageId: 'img-c',
      currentImageId: 'img-c',
    });
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual([resumeDelay.id]);
    expect(clock.size).toBe(1);
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-c');
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual([firstDelay.id]);
    expect(clock.size).toBe(1);
    clock.advance(800);
    expect(runtime.getSnapshot().currentImageId).toBe('img-b');
    expect(runtime.getSnapshot().waitingInteractionIds).toEqual([secondDelay.id, editorState.items[3].id]);
    expect(clock.size).toBe(1);
  });
});
