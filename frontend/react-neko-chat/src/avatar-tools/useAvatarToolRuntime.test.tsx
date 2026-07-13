import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS, type AvatarToolId } from '../avatarTools';
import type { AvatarInteractionPayload, AvatarToolStatePayload } from '../message-schema';
import {
  useAvatarToolRuntime,
  type AvatarToolRuntimeProviders,
} from './useAvatarToolRuntime';

const INITIAL_BOUNDS = {
  left: 100,
  right: 200,
  top: 100,
  bottom: 200,
  width: 100,
  height: 100,
};

const audioInstances: QuietAudio[] = [];

class QuietAudio extends EventTarget {
  preload = '';
  volume = 1;
  src: string;
  play = vi.fn(() => Promise.resolve());
  pause = vi.fn();
  load = vi.fn();

  constructor(src = '') {
    super();
    this.src = src;
    audioInstances.push(this);
  }

  removeAttribute(name: string) {
    if (name === 'src') this.src = '';
  }
}

type HarnessProps = {
  onInteraction: (payload: AvatarInteractionPayload) => void;
  providers: AvatarToolRuntimeProviders;
  toolId?: AvatarToolId;
  tutorialLocked?: boolean;
  deactivationKey?: string;
  onStateChange?: (payload: AvatarToolStatePayload) => void;
};

function Harness({
  onInteraction,
  providers,
  toolId = 'fist',
  tutorialLocked = false,
  deactivationKey,
  onStateChange,
}: HarnessProps) {
  const runtime = useAvatarToolRuntime({
    composerHidden: false,
    composerDisabled: false,
    interactionDisabled: tutorialLocked,
    deactivationKey,
    onInteraction,
    onStateChange,
    getToolLabel: item => item.id,
    providers,
  });
  const tool = AVAILABLE_AVATAR_TOOLS.find(item => item.id === toolId)!;
  return (
    <>
      <button type="button" onClick={event => runtime.selectTool(tool, event)}>
        select tool
      </button>
      <output aria-label="active tool">{runtime.activeToolId ?? 'inactive'}</output>
      <output aria-label="within avatar range">{String(runtime.visualModel.withinAvatarRange)}</output>
      <output aria-label="effective tool variant">{runtime.effectiveVariant}</output>
    </>
  );
}

function SwitchingHarness({
  onStateChange,
}: {
  onStateChange: (payload: AvatarToolStatePayload) => void;
}) {
  const runtime = useAvatarToolRuntime({
    composerHidden: false,
    composerDisabled: false,
    onStateChange,
    getToolLabel: item => item.id,
    providers: createProviders(),
  });
  return (
    <>
      {AVAILABLE_AVATAR_TOOLS.slice(0, 2).map(tool => (
        <button key={tool.id} type="button" onClick={event => runtime.selectTool(tool, event)}>
          select {tool.id}
        </button>
      ))}
      <output aria-label="active tool">{runtime.activeToolId ?? 'inactive'}</output>
    </>
  );
}

function createProviders(overrides: AvatarToolRuntimeProviders = {}): AvatarToolRuntimeProviders {
  return {
    collectBounds: () => [INITIAL_BOUNDS],
    isUiExcluded: () => false,
    now: () => 1_000,
    monotonicNow: () => 0,
    random: () => 0.9,
    ...overrides,
  };
}

function selectTool() {
  fireEvent.click(screen.getByRole('button', { name: 'select tool' }), { clientX: 10, clientY: 10 });
}

describe('useAvatarToolRuntime press lifecycle', () => {
  beforeEach(() => {
    audioInstances.length = 0;
    vi.stubGlobal('Audio', QuietAudio);
  });

  afterEach(() => {
    delete (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__;
    vi.unstubAllGlobals();
  });

  it('keeps the web single-window press on the matching fresh release and commits once', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    expect(onInteraction).not.toHaveBeenCalled();

    fireEvent.pointerUp(window, { pointerId: 8, clientX: 150, clientY: 150 });
    expect(onInteraction).not.toHaveBeenCalled();

    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).toHaveBeenCalledTimes(1);
    expect(onInteraction).toHaveBeenCalledWith(expect.objectContaining({
      toolId: 'fist',
      actionId: 'poke',
      touchZone: 'face',
    }));
    expect(onInteraction.mock.calls[0][0]).not.toHaveProperty('textContext');
  });

  it('applies and releases declared press feedback without a tool-id branch', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');

    fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
    expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('blocks a second interaction while the active recipe owns the generic effect lock', () => {
    const onInteraction = vi.fn();
    render(
      <Harness
        onInteraction={onInteraction}
        providers={createProviders()}
        toolId="hammer"
      />,
    );
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { button: 0, pointerId: 8, clientX: 150, clientY: 150 });

    expect(onInteraction).toHaveBeenCalledTimes(1);
  });

  it('publishes only the selected descriptor without local runtime work in desktop multi-window mode', async () => {
    (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ = true;
    const onInteraction = vi.fn();
    const onStateChange = vi.fn<(payload: AvatarToolStatePayload) => void>();
    const collectBounds = vi.fn(() => [INITIAL_BOUNDS]);
    const windowAddEventListener = vi.spyOn(window, 'addEventListener');
    const documentAddEventListener = vi.spyOn(document, 'addEventListener');

    render(
      <Harness
        onInteraction={onInteraction}
        onStateChange={onStateChange}
        providers={createProviders({ collectBounds })}
      />,
    );
    selectTool();

    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      toolId: 'fist',
    })));
    const activePayload = onStateChange.mock.calls[onStateChange.mock.calls.length - 1]?.[0];
    expect(activePayload).not.toHaveProperty('tool');
    expect(audioInstances).toHaveLength(0);
    expect(collectBounds).not.toHaveBeenCalled();
    expect(windowAddEventListener.mock.calls.some(([type]) => (
      type === 'pointerdown'
      || type === 'pointerup'
      || type === 'pointercancel'
      || type === 'pointermove'
      || type === 'pointerout'
      || type === 'mouseout'
      || type === 'blur'
    ))).toBe(false);
    expect(documentAddEventListener.mock.calls.some(([type]) => (
      type === 'mouseleave' || type === 'visibilitychange'
    ))).toBe(false);
    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();

    selectTool();
    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: false,
      toolId: null,
    })));
    const inactivePayload = onStateChange.mock.calls[onStateChange.mock.calls.length - 1]?.[0];
    expect(inactivePayload).not.toHaveProperty('tool');
  });

  it('ignores a matching pointer release from the wrong button', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { button: 2, pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();

    fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    expect(onInteraction).toHaveBeenCalledTimes(1);
  });

  it('uses fresh release bounds and touch zone instead of the press hit snapshot', () => {
    const onInteraction = vi.fn();
    let bounds = INITIAL_BOUNDS;
    const providers = createProviders({
      collectBounds: () => [bounds],
    });
    render(<Harness onInteraction={onInteraction} providers={providers} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    bounds = { ...INITIAL_BOUNDS, top: 130, bottom: 230 };
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).toHaveBeenCalledWith(expect.objectContaining({ touchZone: 'head' }));
  });

  it('does not commit when fresh avatar bounds disappear before release', () => {
    const onInteraction = vi.fn();
    let bounds = [INITIAL_BOUNDS];
    const providers = createProviders({ collectBounds: () => bounds });
    render(<Harness onInteraction={onInteraction} providers={providers} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    bounds = [];
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('keeps range hold visual-only and never turns a held presentation into a press or commit', () => {
    const onInteraction = vi.fn();
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');
    const view = render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
    expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');
    setTimeoutSpy.mockClear();

    fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 400, clientY: 400 });
    fireEvent.pointerUp(window, { button: 0, pointerId: 8, clientX: 400, clientY: 400 });

    expect(onInteraction).not.toHaveBeenCalled();
    expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');
    expect(setTimeoutSpy.mock.calls.map(([, delay]) => delay)).toContain(180);
    view.unmount();
  });

  it('requeues range hold until the monotonic deadline is actually reached', () => {
    vi.useFakeTimers();
    let monotonic = 0;
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ monotonicNow: () => monotonic })}
      />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
      fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 400, clientY: 400 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 8, clientX: 400, clientY: 400 });

      monotonic = 100;
      act(() => vi.advanceTimersByTime(180));
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');

      monotonic = 180;
      act(() => vi.advanceTimersByTime(80));
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('false');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('starts the full visual hold on first exit without extending it on later outside moves', () => {
    vi.useFakeTimers();
    let monotonic = 0;
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ monotonicNow: () => monotonic })}
      />,
    );
    const flushPointerMove = () => {
      const callback = frameCallbacks.shift();
      expect(callback).toBeTypeOf('function');
      act(() => callback!(monotonic));
    };

    try {
      selectTool();
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');

      monotonic = 60_000;
      fireEvent.pointerMove(window, { clientX: 400, clientY: 400 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');

      monotonic = 60_179;
      act(() => vi.advanceTimersByTime(179));
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');

      fireEvent.pointerMove(window, { clientX: 410, clientY: 410 });
      flushPointerMove();

      monotonic = 60_180;
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('false');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('forces UI exclusion out of visual range without waiting for the hold timer', () => {
    const onInteraction = vi.fn();
    let uiExcluded = false;
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');
    const view = render(
      <Harness
        onInteraction={onInteraction}
        providers={createProviders({ isUiExcluded: () => uiExcluded })}
      />,
    );
    selectTool();
    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
    setTimeoutSpy.mockClear();

    uiExcluded = true;
    fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 150, clientY: 150 });

    expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('false');
    expect(setTimeoutSpy.mock.calls.map(([, delay]) => delay)).not.toContain(180);
    view.unmount();
  });

  it('forces interaction deactivation out of range without waiting for visual hold', () => {
    const onInteraction = vi.fn();
    const providers = createProviders();
    const view = render(
      <Harness onInteraction={onInteraction} providers={providers} tutorialLocked={false} />,
    );
    selectTool();
    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 400, clientY: 400 });
    expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('true');

    view.rerender(<Harness onInteraction={onInteraction} providers={providers} tutorialLocked />);

    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('inactive');
    expect(screen.getByRole('status', { name: 'within avatar range' })).toHaveTextContent('false');
  });

  it('cancels a press after drag movement even when release still hits the avatar', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 160, clientY: 150 });
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 160, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('does not commit when a press is dragged out of the avatar range', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 20, clientY: 20 });
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 20, clientY: 20 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('does not commit after pointer cancellation', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerCancel(window, { pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('enters hammer windup immediately without scheduling a duplicate 0ms phase', () => {
    const onInteraction = vi.fn();
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');
    const view = render(
      <Harness
        onInteraction={onInteraction}
        providers={createProviders()}
        toolId="hammer"
      />,
    );
    selectTool();
    setTimeoutSpy.mockClear();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).toHaveBeenCalledWith(expect.objectContaining({ toolId: 'hammer' }));
    const delays = setTimeoutSpy.mock.calls.map(([, delay]) => delay);
    expect(delays).toEqual(expect.arrayContaining([240, 420, 520, 620]));
    expect(delays).not.toContain(0);
    view.unmount();
  });

  it('does not commit after window blur', () => {
    const onInteraction = vi.fn();
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    fireEvent.blur(window);
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('does not commit after the document becomes hidden', () => {
    const onInteraction = vi.fn();
    const ownHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
    render(<Harness onInteraction={onInteraction} providers={createProviders()} />);
    selectTool();

    try {
      fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      fireEvent(document, new Event('visibilitychange'));
      fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

      expect(onInteraction).not.toHaveBeenCalled();
    } finally {
      if (ownHiddenDescriptor) {
        Object.defineProperty(document, 'hidden', ownHiddenDescriptor);
      } else {
        Reflect.deleteProperty(document, 'hidden');
      }
    }
  });

  it('does not commit when release is owned by UI', () => {
    const onInteraction = vi.fn();
    let uiExcluded = false;
    const providers = createProviders({ isUiExcluded: () => uiExcluded });
    render(<Harness onInteraction={onInteraction} providers={providers} />);
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    uiExcluded = true;
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('cancels an in-progress press when the tutorial shield locks interaction', () => {
    const onInteraction = vi.fn();
    const providers = createProviders();
    const view = render(
      <Harness onInteraction={onInteraction} providers={providers} tutorialLocked={false} />,
    );
    selectTool();

    fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
    view.rerender(
      <Harness onInteraction={onInteraction} providers={providers} tutorialLocked />,
    );
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 150, clientY: 150 });

    expect(onInteraction).not.toHaveBeenCalled();
  });

  it('refuses to activate a tool while interaction is already disabled', () => {
    const onInteraction = vi.fn();
    const onStateChange = vi.fn<(payload: AvatarToolStatePayload) => void>();
    render(
      <Harness
        onInteraction={onInteraction}
        onStateChange={onStateChange}
        providers={createProviders()}
        tutorialLocked
      />,
    );

    selectTool();

    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('inactive');
    expect(onStateChange.mock.calls.some(([payload]) => payload.active)).toBe(false);
  });

  it('deduplicates the host deactivation key inside the shared runtime', () => {
    const onInteraction = vi.fn();
    const providers = createProviders();
    const view = render(
      <Harness
        onInteraction={onInteraction}
        providers={providers}
        deactivationKey="reset-1"
      />,
    );
    selectTool();
    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('fist');

    view.rerender(
      <Harness
        onInteraction={onInteraction}
        providers={providers}
        deactivationKey="reset-1"
      />,
    );
    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('fist');

    view.rerender(
      <Harness
        onInteraction={onInteraction}
        providers={providers}
        deactivationKey="reset-2"
      />,
    );
    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('inactive');
  });

  it('replaces the published descriptor directly when switching tools', async () => {
    const onStateChange = vi.fn<(payload: AvatarToolStatePayload) => void>();
    render(<SwitchingHarness onStateChange={onStateChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'select lollipop' }), { clientX: 10, clientY: 10 });
    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      toolId: 'lollipop',
    })));
    onStateChange.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'select fist' }), { clientX: 10, clientY: 10 });
    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      toolId: 'fist',
    })));

    expect(onStateChange.mock.calls.every(([payload]) => payload.active && payload.toolId === 'fist')).toBe(true);
    expect(screen.getByRole('status', { name: 'active tool' })).toHaveTextContent('fist');
  });

  it('publishes inactive synchronously on refresh and restores the live descriptor after bfcache return', async () => {
    const onInteraction = vi.fn();
    const onStateChange = vi.fn<(payload: AvatarToolStatePayload) => void>();
    render(
      <Harness
        onInteraction={onInteraction}
        onStateChange={onStateChange}
        providers={createProviders()}
      />,
    );
    selectTool();
    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ active: true })));

    onStateChange.mockClear();
    fireEvent(window, new Event('beforeunload'));
    expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ active: false, toolId: null }));

    fireEvent(window, new Event('pageshow'));
    expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ active: true, toolId: 'fist' }));
  });

  it('publishes inactive synchronously and releases prewarmed audio when unmounted', async () => {
    const onInteraction = vi.fn();
    const onStateChange = vi.fn<(payload: AvatarToolStatePayload) => void>();
    const view = render(
      <Harness
        onInteraction={onInteraction}
        onStateChange={onStateChange}
        providers={createProviders()}
        toolId="hammer"
      />,
    );
    selectTool();
    await waitFor(() => expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      active: true,
      toolId: 'hammer',
    })));
    expect(audioInstances).toHaveLength(2);
    expect(audioInstances.every(audio => audio.preload === 'auto')).toBe(true);
    expect(audioInstances.every(audio => audio.play.mock.calls.length === 0)).toBe(true);

    onStateChange.mockClear();
    view.unmount();

    expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ active: false, toolId: null }));
    expect(audioInstances.every(audio => audio.pause.mock.calls.length === 1)).toBe(true);
    expect(audioInstances.every(audio => audio.src === '')).toBe(true);
  });
});
