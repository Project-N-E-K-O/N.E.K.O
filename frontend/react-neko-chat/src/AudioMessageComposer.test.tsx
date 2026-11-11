import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import AudioMessageComposer from './AudioMessageComposer';
import { recordAudio, type AudioMessagePayload } from './audioRecording';

vi.mock('./audioRecording', async importOriginal => ({
  ...await importOriginal<typeof import('./audioRecording')>(),
  recordAudio: vi.fn(),
}));

describe('recording draft submission', () => {
  beforeEach(() => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:recording'), revokeObjectURL: vi.fn() });
    vi.mocked(recordAudio).mockResolvedValue({ stop: vi.fn(async () => new Int16Array(16000)), cancel: vi.fn() });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('reports an insecure connection without locking the composer or starting the microphone', () => {
    vi.stubGlobal('isSecureContext', false);
    vi.stubGlobal('crypto', {});
    const busy = vi.fn();
    render(<AudioMessageComposer target="YUI" disabled={false} send={vi.fn()} onBusyChange={busy} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Use HTTPS');
    expect(screen.queryByText('Preparing…')).toBeNull();
    expect(busy).not.toHaveBeenCalledWith(true);
    expect(recordAudio).not.toHaveBeenCalled();
  });

  it('sends once immediately when the user stops recording', async () => {
    let finish!: () => void;
    const send = vi.fn((_payload: AudioMessagePayload) => new Promise<void>(resolve => { finish = resolve; }));
    render(<AudioMessageComposer target="YUI" disabled={false} send={send} onBusyChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    const stop = await screen.findByRole('button', { name: 'Stop and send' });
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(stop);
    fireEvent.click(stop);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Send recording' })).toBeNull();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ target: 'YUI', sampleRateHz: 16000 });
    await act(async () => { finish(); });
    expect(screen.getByRole('button', { name: 'Record a voice message' })).toBeVisible();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:recording');
  });

  it('discards a draft when its character changes', async () => {
    const send = vi.fn();
    const props = { disabled: false, send, onBusyChange: () => {} };
    const view = render(<AudioMessageComposer {...props} target="YUI" />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    await screen.findByRole('button', { name: 'Stop and send' });
    await act(async () => { vi.mocked(recordAudio).mock.lastCall?.[0](); });
    await screen.findByRole('button', { name: 'Send recording' });
    expect(screen.getByRole('button', { name: 'Preview recording' })).toHaveTextContent('0:01');
    expect(send).not.toHaveBeenCalled();
    view.rerender(<AudioMessageComposer {...props} target="Other" />);
    expect(screen.queryByRole('button', { name: 'Send recording' })).toBeNull();
    expect(send).not.toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:recording');
  });

  it('releases the microphone when the host hides the chat and can record on reopen', async () => {
    const cancel = vi.fn();
    vi.mocked(recordAudio).mockResolvedValueOnce({ stop: vi.fn(), cancel });
    const send = vi.fn();
    render(<AudioMessageComposer target="YUI" disabled={false} send={send} onBusyChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    await screen.findByRole('button', { name: 'Stop and send' });

    act(() => { window.dispatchEvent(new Event('react-chat-window:closed')); });
    expect(cancel).toHaveBeenCalledOnce();
    expect(send).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    expect(await screen.findByRole('button', { name: 'Stop and send' })).toBeVisible();
  });

  it('preserves preview after uncertain delivery and provides no resend action', async () => {
    const send = vi.fn(async () => { throw new Error('socket closed after write'); });
    render(<AudioMessageComposer target="YUI" disabled={false} send={send} onBusyChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
    expect(screen.getByLabelText('Preview recording')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Send recording' })).toBeNull();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('reuses the same recording ID when the server refused it before submission', async () => {
    const rejected = new Error('busy');
    rejected.name = 'AudioMessageRejected';
    const send = vi.fn<(_payload: AudioMessagePayload) => Promise<unknown>>()
      .mockRejectedValueOnce(rejected).mockResolvedValueOnce({});
    render(<AudioMessageComposer target="YUI" disabled={false} send={send} onBusyChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Send recording' }));
    await screen.findByRole('button', { name: 'Record a voice message' });
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[0][0].requestId).toBe(send.mock.calls[1][0].requestId);
  });
});
