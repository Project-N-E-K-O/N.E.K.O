import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AVAILABLE_COMPACT_AVATAR_TOOLS, type AvatarToolId } from '../avatarTools';
import type { AvatarInteractionPayload, AvatarToolStatePayload } from '../message-schema';
import AvatarToolVisuals from './presentation';
import {
  resolveAvatarToolHeadAnchor,
  useAvatarToolRuntime,
  type AvatarToolRuntimeProviders,
} from './runtime';
import '../styles.css';

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
  avatarName?: string;
  renderVisuals?: boolean;
};

function Harness({
  onInteraction,
  providers,
  toolId = 'fist',
  tutorialLocked = false,
  deactivationKey,
  onStateChange,
  avatarName = 'Yui',
  renderVisuals = false,
}: HarnessProps) {
  const runtime = useAvatarToolRuntime({
    composerHidden: false,
    composerDisabled: false,
    interactionDisabled: tutorialLocked,
    deactivationKey,
    onInteraction,
    onStateChange,
    getToolLabel: item => item.id,
    avatarName,
    providers,
  });
  const tool = AVAILABLE_COMPACT_AVATAR_TOOLS.find(item => item.id === toolId)!;
  const confirmedRound = runtime.visualModel.overlayEffect?.kind === 'round-reveal'
    ? runtime.visualModel.overlayEffect.round
    : null;
  return (
    <>
      <button type="button" onClick={event => runtime.selectTool(tool, event)}>
        select tool
      </button>
      <output aria-label="active tool">{runtime.activeToolId ?? 'inactive'}</output>
      <output aria-label="within avatar range">{String(runtime.visualModel.withinAvatarRange)}</output>
      <output aria-label="effective tool variant">{runtime.effectiveVariant}</output>
      <output aria-label="avatar gesture phase">
        {runtime.visualModel.roundChoiceAvatarGesture ? 'cycling' : 'hidden'}
      </output>
      <output aria-label="avatar gesture variant">
        {runtime.visualModel.roundChoiceAvatarGesture?.variant ?? 'none'}
      </output>
      <output aria-label="avatar gesture anchor">
        {runtime.visualModel.roundChoiceAvatarGesture
          ? `${runtime.visualModel.roundChoiceAvatarGesture.anchor.x},${runtime.visualModel.roundChoiceAvatarGesture.anchor.y}`
          : 'none'}
      </output>
      <output aria-label="confirmed round">
        {confirmedRound
          ? [
            confirmedRound.userGesture,
            confirmedRound.userVariant,
            confirmedRound.avatarGesture,
            confirmedRound.avatarVariant,
            confirmedRound.roundResult,
          ].join(':')
          : 'none'}
      </output>
      <output aria-label="round reveal phase">
        {runtime.visualModel.overlayEffect?.kind === 'round-reveal'
          ? runtime.visualModel.overlayEffect.phase
          : 'hidden'}
      </output>
      <output aria-label="round reveal result">
        {runtime.visualModel.overlayEffect?.kind === 'round-reveal'
          ? runtime.visualModel.overlayEffect.round.roundResult
          : 'none'}
      </output>
      <output aria-label="round reveal label">
        {runtime.visualModel.overlayEffect?.kind === 'round-reveal'
          ? runtime.visualModel.overlayEffect.resultLabel
          : 'none'}
      </output>
      {renderVisuals ? <AvatarToolVisuals model={runtime.visualModel} /> : null}
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
      {AVAILABLE_COMPACT_AVATAR_TOOLS.slice(0, 2).map(tool => (
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
    prepareVisuals: () => undefined,
    getHeadAnchor: () => ({ x: 150, y: 90, coordinateSpace: 'viewport-css-pixel' }),
    ...overrides,
  };
}

function selectTool() {
  fireEvent.click(screen.getByRole('button', { name: 'select tool' }), { clientX: 10, clientY: 10 });
}

describe('avatar tool head anchor adapter', () => {
  it('moves a reliable head point to the head top and falls back to the model top center', () => {
    expect(resolveAvatarToolHeadAnchor({
      head: { x: 123, y: 145 },
      bubbleHeadRect: { left: 100, top: 112, width: 46, height: 66 },
      bounds: INITIAL_BOUNDS,
    })).toEqual({ x: 123, y: 112, coordinateSpace: 'viewport-css-pixel' });
    expect(resolveAvatarToolHeadAnchor({
      head: { x: 123, y: 145 },
      bounds: INITIAL_BOUNDS,
    })).toEqual({ x: 123, y: 100, coordinateSpace: 'viewport-css-pixel' });
    expect(resolveAvatarToolHeadAnchor({
      head: { x: Number.NaN, y: 45 },
      bounds: INITIAL_BOUNDS,
    })).toEqual({ x: 150, y: 100, coordinateSpace: 'viewport-css-pixel' });
    expect(resolveAvatarToolHeadAnchor({ head: null, bounds: null })).toBeNull();
  });
});

describe('useAvatarToolRuntime press lifecycle', () => {
  beforeEach(() => {
    audioInstances.length = 0;
    vi.stubGlobal('Audio', QuietAudio);
  });

  afterEach(() => {
    delete (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__;
    vi.restoreAllMocks();
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

  it('runs one rps reveal and emits the same round facts once', () => {
    vi.useFakeTimers();
    const onInteraction = vi.fn();
    const view = render(
      <Harness onInteraction={onInteraction} providers={createProviders()} toolId="rps" avatarName="" />,
    );

    try {
      selectTool();
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');

      act(() => vi.advanceTimersByTime(240));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');

      fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      act(() => vi.advanceTimersByTime(1_000));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');

      fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      expect(onInteraction).toHaveBeenCalledTimes(1);
      expect(onInteraction).toHaveBeenCalledWith(expect.objectContaining({
        toolId: 'rps',
        userGesture: 'scissors',
        avatarGesture: 'paper',
        roundResult: 'user_win',
      }));
      expect(audioInstances).toHaveLength(4);
      expect(audioInstances.some(audio => audio.play.mock.calls.length === 1)).toBe(true);
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');

      fireEvent.pointerDown(window, { button: 0, pointerId: 8, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 8, clientX: 150, clientY: 150 });
      expect(audioInstances).toHaveLength(4);
      expect(onInteraction).toHaveBeenCalledTimes(1);

      act(() => vi.advanceTimersByTime(519));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('impact');
      act(() => vi.advanceTimersByTime(240));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('result');
      expect(screen.getByRole('status', { name: 'round reveal result' })).toHaveTextContent('user_win');
      expect(screen.getByRole('status', { name: 'round reveal label' })).toHaveTextContent('You win');
      expect(audioInstances).toHaveLength(5);
      act(() => vi.advanceTimersByTime(2_399));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('recover');
      act(() => vi.advanceTimersByTime(180));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('hidden');
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('does not cycle or accept an rps press before its declared visuals are ready', async () => {
    vi.useFakeTimers();
    let markReady: (() => void) | undefined;
    const readiness = new Promise<void>((resolve) => { markReady = resolve; });
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ prepareVisuals: () => readiness })}
        toolId="rps"
      />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 7, clientX: 150, clientY: 150 });
      act(() => vi.advanceTimersByTime(240));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      expect(audioInstances.every(audio => audio.play.mock.calls.length === 0)).toBe(true);

      await act(async () => { markReady?.(); });
      act(() => vi.advanceTimersByTime(719));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('resumes the same rps sequence without confirmation after a moved release', () => {
    vi.useFakeTimers();
    const onInteraction = vi.fn();
    const view = render(
      <Harness onInteraction={onInteraction} providers={createProviders()} toolId="rps" />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 9, clientX: 150, clientY: 150 });
      fireEvent.pointerMove(window, { pointerId: 9, clientX: 160, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 9, clientX: 160, clientY: 150 });

      expect(onInteraction).not.toHaveBeenCalled();
      expect(audioInstances.every(audio => audio.play.mock.calls.length === 0)).toBe(true);
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(720));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('reschedules the next rps tick at raw range boundaries without changing the visible choice', () => {
    vi.useFakeTimers();
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness onInteraction={vi.fn()} providers={createProviders()} toolId="rps" />,
    );
    const flushPointerMove = () => {
      const callback = frameCallbacks.shift();
      expect(callback).toBeTypeOf('function');
      act(() => callback!(0));
    };

    try {
      selectTool();
      act(() => vi.advanceTimersByTime(120));

      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(719));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');

      fireEvent.pointerMove(window, { clientX: 400, clientY: 400 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
      act(() => vi.advanceTimersByTime(239));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('secondary');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('tertiary');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('shows the current catgirl cycling hand only inside the raw range and cycles it in the same session', () => {
    vi.useFakeTimers();
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness onInteraction={vi.fn()} providers={createProviders()} toolId="rps" />,
    );
    const flushPointerMove = () => {
      const callback = frameCallbacks.shift();
      expect(callback).toBeTypeOf('function');
      act(() => callback!(0));
    };

    try {
      selectTool();
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('hidden');

      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('cycling');
      expect(screen.getByRole('status', { name: 'avatar gesture variant' })).toHaveTextContent('primary');
      expect(screen.getByRole('status', { name: 'avatar gesture anchor' })).toHaveTextContent('150,90');

      act(() => vi.advanceTimersByTime(240));
      expect(screen.getByRole('status', { name: 'avatar gesture variant' })).toHaveTextContent('secondary');

      fireEvent.pointerMove(window, { clientX: 400, clientY: 400 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('hidden');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('freezes the user hand on press while the catgirl hand keeps cycling', () => {
    vi.useFakeTimers();
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness onInteraction={vi.fn()} providers={createProviders()} toolId="rps" />,
    );

    try {
      selectTool();
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      const frameCallback = frameCallbacks.shift();
      expect(frameCallback).toBeTypeOf('function');
      act(() => frameCallback!(0));
      act(() => vi.advanceTimersByTime(100));
      fireEvent.pointerDown(window, {
        button: 0,
        pointerId: 70,
        clientX: 150,
        clientY: 150,
      });

      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      expect(screen.getByRole('status', { name: 'avatar gesture variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(139));
      expect(screen.getByRole('status', { name: 'avatar gesture variant' })).toHaveTextContent('primary');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'effective tool variant' })).toHaveTextContent('primary');
      expect(screen.getByRole('status', { name: 'avatar gesture variant' })).toHaveTextContent('secondary');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('retains the round reveal through range exit with only the last reliable hit bounds', () => {
    vi.useFakeTimers();
    const random = vi.fn(() => 0.9);
    const getHeadAnchor = vi.fn((fallbackBounds: typeof INITIAL_BOUNDS | null) => fallbackBounds ? ({
      x: fallbackBounds.left + fallbackBounds.width / 2,
      y: fallbackBounds.top,
      coordinateSpace: 'viewport-css-pixel' as const,
    }) : null);
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ random, getHeadAnchor })}
        toolId="rps"
      />,
    );
    const flushPointerMove = () => {
      const callback = frameCallbacks.shift();
      expect(callback).toBeTypeOf('function');
      act(() => callback!(0));
    };

    try {
      selectTool();
      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      flushPointerMove();
      fireEvent.pointerDown(window, { button: 0, pointerId: 71, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 71, clientX: 150, clientY: 150 });

      expect(random).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('rock:primary:paper:tertiary:avatar_win');
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('hidden');

      fireEvent.pointerMove(window, { clientX: 400, clientY: 400 });
      flushPointerMove();
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      act(() => vi.advanceTimersByTime(500));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      act(() => vi.advanceTimersByTime(20));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('impact');
      act(() => vi.advanceTimersByTime(2_819));
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('rock:primary:paper:tertiary:avatar_win');
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('none');
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('hidden');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('keeps one reveal across viewport exit and resumes preparation from the latest range state', () => {
    vi.useFakeTimers();
    const onInteraction = vi.fn();
    const frameCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    const view = render(
      <Harness
        onInteraction={onInteraction}
        providers={createProviders({ random: () => 0.5 })}
        toolId="rps"
        avatarName=""
        renderVisuals
      />,
    );
    const flushPointerMove = () => {
      const callback = frameCallbacks.shift();
      expect(callback).toBeTypeOf('function');
      act(() => callback!(0));
    };

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 81, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 81, clientX: 150, clientY: 150 });
      expect(onInteraction).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      const hiddenResult = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-result');
      const userHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-user');
      const avatarHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-avatar');
      expect(hiddenResult).toHaveTextContent('You win');
      expect(window.getComputedStyle(hiddenResult!).opacity).toBe('0');
      expect(window.getComputedStyle(userHand!).getPropertyValue('--avatar-tool-rps-impact-scale').trim()).toBe('1.44');
      expect(window.getComputedStyle(avatarHand!).getPropertyValue('--avatar-tool-rps-impact-scale').trim()).toBe('1.3');
      expect(window.getComputedStyle(userHand!).zIndex).toBe('2');

      fireEvent(window, new MouseEvent('mouseout', {
        bubbles: true,
        clientX: 0,
        clientY: 150,
        relatedTarget: null,
      }));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      expect(document.querySelector('.avatar-tool-round-reveal')).toBeNull();

      fireEvent.pointerMove(window, { clientX: 150, clientY: 150 });
      flushPointerMove();
      expect(document.querySelector('.avatar-tool-round-reveal')).not.toBeNull();
      const activeUserHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-user');
      const activeAvatarHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-avatar');
      fireEvent.pointerDown(window, { button: 0, pointerId: 82, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 82, clientX: 150, clientY: 150 });
      expect(onInteraction).toHaveBeenCalledTimes(1);

      act(() => vi.advanceTimersByTime(520));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('impact');
      expect(window.getComputedStyle(activeUserHand!).zIndex).toBe('2');
      expect(window.getComputedStyle(activeAvatarHand!).filter).toContain('grayscale(0.62)');
      act(() => vi.advanceTimersByTime(240));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('result');
      const visibleResult = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-result');
      expect(visibleResult).toHaveTextContent('You win');
      expect(window.getComputedStyle(visibleResult!).opacity).toBe('1');
      expect(window.getComputedStyle(activeUserHand!).zIndex).not.toBe('2');
      expect(window.getComputedStyle(activeUserHand!).transform).not.toContain('scale');
      expect(window.getComputedStyle(activeAvatarHand!).transform).not.toContain('scale');
      expect(window.getComputedStyle(activeAvatarHand!).filter).toContain('grayscale(0.62)');
      act(() => vi.advanceTimersByTime(2_400));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('recover');
      act(() => vi.advanceTimersByTime(180));
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('hidden');
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('none');
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('cycling');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('keeps draw hands equal at the shared collision center without loser styling', () => {
    vi.useFakeTimers();
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ random: () => 0 })}
        toolId="rps"
        renderVisuals
      />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 83, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 83, clientX: 150, clientY: 150 });
      const userHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-user');
      const avatarHand = document.querySelector<HTMLElement>('.avatar-tool-round-reveal-hand.is-avatar');
      expect(screen.getByRole('status', { name: 'round reveal result' })).toHaveTextContent('draw');
      expect(window.getComputedStyle(userHand!).getPropertyValue('--avatar-tool-rps-impact-scale').trim()).toBe('1.3');
      expect(window.getComputedStyle(avatarHand!).getPropertyValue('--avatar-tool-rps-impact-scale').trim()).toBe('1.3');
      act(() => vi.advanceTimersByTime(520));
      expect(window.getComputedStyle(userHand!).zIndex).not.toBe('2');
      expect(window.getComputedStyle(avatarHand!).zIndex).not.toBe('2');
      expect(window.getComputedStyle(userHand!).filter).not.toContain('grayscale');
      expect(window.getComputedStyle(avatarHand!).filter).not.toContain('grayscale');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('ignores an unrelated pointer cancellation after an rps round is confirmed', () => {
    vi.useFakeTimers();
    const onInteraction = vi.fn();
    const view = render(
      <Harness
        onInteraction={onInteraction}
        providers={createProviders({ random: () => 0.5 })}
        toolId="rps"
        renderVisuals
      />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 70, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 70, clientX: 150, clientY: 150 });
      expect(onInteraction).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('status', { name: 'confirmed round' })).not.toHaveTextContent('none');

      fireEvent.pointerCancel(window, { pointerId: 71, clientX: 150, clientY: 150 });

      expect(screen.getByRole('status', { name: 'confirmed round' })).not.toHaveTextContent('none');
      expect(screen.getByRole('status', { name: 'round reveal phase' })).toHaveTextContent('approach');
      expect(document.querySelector('.avatar-tool-round-reveal')).not.toBeNull();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('does not choose a current catgirl hand for an invalid rps release and clears a confirmed reveal on blur', () => {
    vi.useFakeTimers();
    const random = vi.fn(() => 0.5);
    const view = render(
      <Harness
        onInteraction={vi.fn()}
        providers={createProviders({ random })}
        toolId="rps"
      />,
    );

    try {
      selectTool();
      fireEvent.pointerDown(window, { button: 0, pointerId: 72, clientX: 150, clientY: 150 });
      fireEvent.pointerMove(window, { pointerId: 72, clientX: 160, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 72, clientX: 160, clientY: 150 });
      expect(random).not.toHaveBeenCalled();
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('none');

      fireEvent.pointerDown(window, { button: 0, pointerId: 73, clientX: 150, clientY: 150 });
      fireEvent.pointerUp(window, { button: 0, pointerId: 73, clientX: 150, clientY: 150 });
      expect(random).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('status', { name: 'confirmed round' })).not.toHaveTextContent('none');
      fireEvent.blur(window);
      expect(screen.getByRole('status', { name: 'confirmed round' })).toHaveTextContent('none');
      expect(screen.getByRole('status', { name: 'avatar gesture phase' })).toHaveTextContent('hidden');
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
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
