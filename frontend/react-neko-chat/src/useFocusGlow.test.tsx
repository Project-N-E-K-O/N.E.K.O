import { render, cleanup } from '@testing-library/react';
import { useRef } from 'react';
import styles from './styles.css?raw';
import { FOCUS_BREATH_PERIOD_MS, FOCUS_GLOW_FPS, useFocusGlow } from './useFocusGlow';

const FRAME_MS = 1000 / FOCUS_GLOW_FPS;

function pushCharge(charge: number): void {
  window.dispatchEvent(new CustomEvent('neko-focus-charge', { detail: { charge, atMs: Date.now() } }));
}

function GlowHost() {
  const ref = useRef<HTMLDivElement | null>(null);
  useFocusGlow(ref);
  return <div ref={ref} data-testid="glow-host" />;
}

function mockReducedMotion(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: matches && query.includes('reduce'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
}

// Advance in small steps and record the --focus-breath value after each one, so
// the number of distinct updates reflects how often the hook actually rendered.
function sampleBreath(host: HTMLElement, durationMs: number, stepMs = 5): string[] {
  const samples: string[] = [];
  for (let elapsed = 0; elapsed < durationMs; elapsed += stepMs) {
    vi.advanceTimersByTime(stepMs);
    samples.push(host.style.getPropertyValue('--focus-breath'));
  }
  return samples;
}

describe('useFocusGlow', () => {
  const originalMatchMedia = window.matchMedia;
  const timeoutSpy = () => vi.mocked(globalThis.setTimeout);
  const rafSpy = () => vi.mocked(window.requestAnimationFrame);

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.setSystemTime(1_000_000);
    mockReducedMotion(false);
    vi.spyOn(globalThis, 'setTimeout');
    vi.spyOn(window, 'requestAnimationFrame');
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: originalMatchMedia });
  });

  it('schedules nothing and writes no glow while there is no charge', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    vi.advanceTimersByTime(5000);
    expect(vi.getTimerCount()).toBe(0);
    expect(rafSpy()).not.toHaveBeenCalled();
    expect(host.getAttribute('data-focus-glow')).toBeNull();
    expect(host.style.getPropertyValue('--focus-glow')).toBe('');
  });

  it('caps glow updates at FOCUS_GLOW_FPS instead of the display refresh rate', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    pushCharge(0.8);
    timeoutSpy().mockClear();
    vi.advanceTimersByTime(1000);

    const delays = timeoutSpy().mock.calls.map((call) => Number(call[1]));
    expect(delays.length).toBeGreaterThanOrEqual(FOCUS_GLOW_FPS - 1);
    expect(delays.length).toBeLessThanOrEqual(FOCUS_GLOW_FPS + 1);
    delays.forEach((delay) => expect(delay).toBeGreaterThanOrEqual(FRAME_MS - 0.01));
    expect(rafSpy()).not.toHaveBeenCalled();

    const samples = sampleBreath(host, FOCUS_BREATH_PERIOD_MS);
    const changes = samples.filter((value, i) => i > 0 && value !== samples[i - 1]).length;
    expect(changes).toBeLessThanOrEqual(Math.ceil((FOCUS_BREATH_PERIOD_MS / 1000) * FOCUS_GLOW_FPS));
  });

  it('keeps breathing at the ENTER floor, including after the window loses focus', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    pushCharge(0.7);
    vi.advanceTimersByTime(30_000); // well past the 0.7 -> 0.6 decay
    expect(host.style.getPropertyValue('--focus-glow')).toBe('0.600');
    expect(host.getAttribute('data-focus-breathing')).toBe('true');

    window.dispatchEvent(new Event('blur'));
    const samples = sampleBreath(host, FOCUS_BREATH_PERIOD_MS).map(Number);
    expect(vi.getTimerCount()).toBe(1);
    expect(Math.min(...samples)).toBeLessThan(0.05);
    expect(Math.max(...samples)).toBeGreaterThan(0.95);

    timeoutSpy().mockClear();
    vi.advanceTimersByTime(1000);
    expect(timeoutSpy().mock.calls.length).toBeGreaterThanOrEqual(15);
  });

  it('starts the breathing cycle at its trough when activation begins', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    vi.advanceTimersByTime(1234);
    pushCharge(0.9);
    expect(host.style.getPropertyValue('--focus-breath')).toBe('0.000');
    vi.advanceTimersByTime(FOCUS_BREATH_PERIOD_MS / 2);
    expect(Number(host.style.getPropertyValue('--focus-breath'))).toBeGreaterThan(0.95);
  });

  it('fades a sub-ENTER charge to 0, then stops the timer and clears the glow', () => {
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    pushCharge(0.45);
    expect(host.getAttribute('data-focus-glow')).toBe('true');
    expect(host.getAttribute('data-focus-breathing')).toBeNull();
    expect(host.style.getPropertyValue('--focus-breath')).toBe('');

    vi.advanceTimersByTime(1000);
    expect(vi.getTimerCount()).toBe(1); // still fading, no early idle at a floor

    vi.advanceTimersByTime(30_000);
    expect(vi.getTimerCount()).toBe(0);
    expect(host.style.getPropertyValue('--focus-glow')).toBe('');
    expect(host.getAttribute('data-focus-glow')).toBeNull();
  });

  it('restarts the stopped timer on the next charge push', () => {
    render(<GlowHost />);
    pushCharge(0.4);
    vi.advanceTimersByTime(30_000);
    expect(vi.getTimerCount()).toBe(0);

    pushCharge(0.9);
    expect(vi.getTimerCount()).toBe(1);
  });

  it('holds a steady glow without a timer under reduced motion', () => {
    mockReducedMotion(true);
    const { getByTestId } = render(<GlowHost />);
    const host = getByTestId('glow-host');

    pushCharge(0.6);
    expect(host.getAttribute('data-focus-breathing')).toBe('true');
    expect(host.style.getPropertyValue('--focus-breath')).toBe('');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops the timer and clears the glow on unmount', () => {
    const { getByTestId, unmount } = render(<GlowHost />);
    const host = getByTestId('glow-host');
    pushCharge(0.8);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    expect(host.getAttribute('data-focus-glow')).toBeNull();
  });

  it('keeps Focus breathing out of CSS keyframes, which follow the display refresh rate', () => {
    expect(styles).not.toMatch(/@keyframes\s+focus-glow/);
    const focusRules = styles.match(/[^{}]*\[data-focus-(?:glow|breathing)="true"\][^{}]*\{[^}]*\}/g) ?? [];
    expect(focusRules.length).toBeGreaterThanOrEqual(6);
    focusRules.forEach((rule) => expect(rule).not.toMatch(/\banimation\s*:/));
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: no-preference\)\s*\{\s*\.app-shell\.chat-surface-mode-compact\[data-focus-breathing="true"\] \.compact-chat-surface-frame\s*\{[^}]*var\(--focus-breath/,
    );
  });
});
