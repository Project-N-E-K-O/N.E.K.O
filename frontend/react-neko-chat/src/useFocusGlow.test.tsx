import { render, cleanup } from '@testing-library/react';
import { useRef } from 'react';
import { useFocusGlow } from './useFocusGlow';

// Manual rAF + clock so we can assert the loop *idles* (stops scheduling frames)
// once the glow has settled, instead of spinning rAF forever re-writing the same
// --focus-glow value every frame.
let now = 0;
let rafMap: Map<number, FrameRequestCallback>;
let nextRafId = 0;

function frame(dtMs = 1000): void {
  now += dtMs;
  const callbacks = [...rafMap.values()];
  rafMap.clear();
  callbacks.forEach((cb) => cb(now));
}

function pendingFrames(): number {
  return rafMap.size;
}

function settle(maxFrames = 120): void {
  let guard = 0;
  while (pendingFrames() > 0 && guard < maxFrames) {
    frame(1000);
    guard += 1;
  }
}

function pushCharge(charge: number): void {
  window.dispatchEvent(new CustomEvent('neko-focus-charge', { detail: { charge, atMs: now } }));
}

function GlowHost() {
  const ref = useRef<HTMLDivElement | null>(null);
  useFocusGlow(ref);
  return <div ref={ref} data-testid="glow-host" />;
}

describe('useFocusGlow', () => {
  beforeEach(() => {
    now = 1_000_000;
    rafMap = new Map();
    nextRafId = 0;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      const id = ++nextRafId;
      rafMap.set(id, cb);
      return id;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id: number) => {
      rafMap.delete(id);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('idles the rAF loop once an activated charge settles at the ENTER baseline', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    // No charge yet: the loop idles on its first frame.
    frame();
    expect(pendingFrames()).toBe(0);

    // Activated push (>= ENTER 0.6): glow appears, loop restarts.
    pushCharge(0.7);
    expect(pendingFrames()).toBe(1);

    // While decaying 0.7 -> 0.6 (DECAY_ACTIVATED 0.01/s, ~10s) the loop keeps
    // ticking and the intensity is still changing.
    frame(1000);
    expect(pendingFrames()).toBe(1);
    expect(Number(host.style.getPropertyValue('--focus-glow'))).toBeGreaterThan(0.6);

    // Past the settle window the loop must stop scheduling frames — this is the
    // fix: no more per-frame rewrites of a now-constant value.
    settle();
    expect(pendingFrames()).toBe(0);
    expect(host.style.getPropertyValue('--focus-glow')).toBe('0.600');
    // Breathing is a pure CSS keyframe and stays on while the loop is idle.
    expect(host.getAttribute('data-focus-breathing')).toBe('true');
    expect(host.getAttribute('data-focus-glow')).toBe('true');
  });

  it('restarts the idled loop on the next charge push', () => {
    render(<GlowHost />);
    frame();

    pushCharge(0.6); // exactly at ENTER -> settles immediately
    settle();
    expect(pendingFrames()).toBe(0);

    pushCharge(0.9); // a fresh push must wake the loop back up
    expect(pendingFrames()).toBe(1);
  });

  it('keeps fading a sub-ENTER charge all the way to 0 (no early idle at a floor)', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');
    frame();

    pushCharge(0.45); // between ONSET 0.3 and ENTER 0.6 -> must decay to 0, not floor
    expect(pendingFrames()).toBe(1);

    // Still ticking partway through the fade (does NOT idle at a floor).
    frame(1000);
    expect(pendingFrames()).toBe(1);

    settle();
    expect(pendingFrames()).toBe(0); // fully decayed -> idle
    expect(host.style.getPropertyValue('--focus-glow')).toBe(''); // cleared at 0
    expect(host.getAttribute('data-focus-glow')).toBeNull();
  });
});
