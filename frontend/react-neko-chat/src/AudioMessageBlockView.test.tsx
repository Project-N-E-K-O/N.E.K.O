import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AudioMessageBlockView from './AudioMessageBlockView';

describe('AudioMessageBlockView', () => {
  it('shows the duration and transcript without a stored recording', () => {
    render(<AudioMessageBlockView interactive block={{ type: 'audio_message', durationMs: 4200, transcript: 'hello there' }} />);
    const play = screen.getByRole('button', { name: 'Voice message 0:05' });
    expect(play).toBeDisabled();
    expect(screen.queryByText('hello there')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Recording actions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Transcript' }));
    expect(screen.getByText('hello there')).toBeVisible();
  });

  it('plays an in-session recording URL', () => {
    render(<AudioMessageBlockView interactive block={{ type: 'audio_message', durationMs: 1000, audioUrl: 'blob:voice' }} />);
    expect(screen.getByRole('button', { name: 'Play recording' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Recording actions' })).toBeNull();
  });

  it('renders a plain label outside interactive bubbles', () => {
    render(<AudioMessageBlockView interactive={false} block={{ type: 'audio_message', durationMs: 12_000 }} />);
    expect(screen.getByText('Voice message 0:12')).toBeVisible();
  });
});
