import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import NekoTooltipLayer from './NekoTooltipLayer';
import chatStyles from './styles.css?raw';

afterEach(() => {
  vi.useRealTimers();
});

describe('NekoTooltipLayer', () => {
  it('shows custom tooltip copy for keyboard focus without a native title', () => {
    render(
      <>
        <button type="button" data-neko-tooltip="打开历史记录">历史</button>
        <NekoTooltipLayer />
      </>,
    );

    const button = screen.getByRole('button', { name: '历史' });
    expect(button).not.toHaveAttribute('title');

    act(() => button.focus());
    expect(screen.getByRole('tooltip')).toHaveTextContent('打开历史记录');

    act(() => button.blur());
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('uses a short delay for pointer hover and closes when the pointer leaves', () => {
    vi.useFakeTimers();
    render(
      <>
        <button type="button" data-neko-tooltip="截图">截图按钮</button>
        <NekoTooltipLayer />
      </>,
    );

    const button = screen.getByRole('button', { name: '截图按钮' });
    fireEvent.pointerOver(button);
    expect(screen.queryByRole('tooltip')).toBeNull();

    act(() => vi.advanceTimersByTime(320));
    expect(screen.getByRole('tooltip')).toHaveTextContent('截图');

    fireEvent.pointerOut(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('keeps compact minimize copy above the button with in-wheel tooltip styling', () => {
    render(
      <>
        <button
          type="button"
          data-neko-tooltip="最小化聊天框"
          data-neko-tooltip-variant="compact-tool"
          data-neko-tooltip-placement="top"
        >
          最小化
        </button>
        <NekoTooltipLayer />
      </>,
    );

    act(() => screen.getByRole('button', { name: '最小化' }).focus());
    expect(screen.getByRole('tooltip')).toHaveAttribute('data-variant', 'compact-tool');
    expect(screen.getByRole('tooltip')).toHaveAttribute('data-placement', 'top');
    expect(chatStyles).toMatch(
      /\.neko-chat-tooltip\[data-variant="compact-tool"\]::after\s*\{\s*content: none;/,
    );
  });

  it('dismisses a visible tooltip instead of following viewport movement', () => {
    render(
      <>
        <button type="button" data-neko-tooltip="最小化聊天框">最小化</button>
        <NekoTooltipLayer />
      </>,
    );

    act(() => screen.getByRole('button', { name: '最小化' }).focus());
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    fireEvent.scroll(window);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('dismisses a visible tooltip when its anchor changes position', () => {
    let frameCallback: FrameRequestCallback | null = null;
    const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallback = callback;
      return 17;
    });
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});

    try {
      render(
        <>
          <button type="button" data-neko-tooltip="最小化聊天框">最小化</button>
          <NekoTooltipLayer />
        </>,
      );

      const button = screen.getByRole('button', { name: '最小化' });
      let left = 20;
      vi.spyOn(button, 'getBoundingClientRect').mockImplementation(() => ({
        x: left,
        y: 40,
        left,
        top: 40,
        right: left + 46,
        bottom: 86,
        width: 46,
        height: 46,
        toJSON: () => ({}),
      }));

      act(() => button.focus());
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
      expect(frameCallback).not.toBeNull();

      left = 26;
      act(() => frameCallback?.(16));
      expect(screen.queryByRole('tooltip')).toBeNull();
    } finally {
      requestFrame.mockRestore();
      cancelFrame.mockRestore();
    }
  });

  it('keeps generic and compact-wheel tooltips in the same restrained frosted style', () => {
    const genericRule = chatStyles.match(/\.neko-chat-tooltip\s*\{[\s\S]*?\n\}/)?.[0] ?? '';
    const wheelRule = chatStyles.match(
      /\.compact-input-tool-fan \.compact-input-tool-tooltip\s*\{[\s\S]*?\n\}/,
    )?.[0] ?? '';

    expect(genericRule).toContain('border-radius: 10px');
    expect(genericRule).toContain('backdrop-filter: blur(16px)');
    expect(wheelRule).toContain('border-radius: 9px');
    expect(wheelRule).toContain('backdrop-filter: blur(16px)');
    expect(wheelRule).not.toContain('border-radius: 0');
  });
});
