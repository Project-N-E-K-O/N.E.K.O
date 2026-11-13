import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import App from './App';
import AudioMessageComposerView, { useAudioMessageComposer } from './AudioMessageComposer';
import { AUDIO_MAX_SAMPLES, recordAudio, type AudioMessagePayload } from './audioRecording';

vi.mock('./audioRecording', async importOriginal => ({
  ...await importOriginal<typeof import('./audioRecording')>(),
  recordAudio: vi.fn(),
}));

function AudioMessageComposer({ onBusyChange, ...props }: Parameters<typeof useAudioMessageComposer>[0] & {
  onBusyChange: (busy: boolean) => void;
}) {
  const composer = useAudioMessageComposer(props);
  useEffect(() => onBusyChange(composer.active), [composer.active, onBusyChange]);
  return <AudioMessageComposerView composer={composer} />;
}

describe('recording draft submission', () => {
  const showStatusToast = vi.fn();

  beforeEach(() => {
    showStatusToast.mockClear();
    vi.stubGlobal('showStatusToast', showStatusToast);
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
    expect(showStatusToast).toHaveBeenCalledWith(expect.stringContaining('Use HTTPS'), expect.any(Number));
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

  it('keeps recording across folding and unfolding, then stops at the limit while folded', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 404 })));
    const stop = vi.fn(async () => new Int16Array(AUDIO_MAX_SAMPLES));
    const cancel = vi.fn();
    const send = vi.fn();
    vi.mocked(recordAudio).mockResolvedValueOnce({ stop, cancel });
    const onCompactMinimizeRequest = vi.fn();
    const props = { audioMessageTarget: 'YUI', onComposerAudioMessage: send, onCompactMinimizeRequest };
    vi.useFakeTimers();
    try {
      const view = render(<App {...props} chatSurfaceMode="compact" />);
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' })); });
      expect(screen.getByRole('button', { name: 'Stop and send' })).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: 'Minimize' }));
      expect(onCompactMinimizeRequest).toHaveBeenCalledOnce();
      view.rerender(<App {...props} chatSurfaceMode="minimized" />);
      expect(screen.queryByRole('button', { name: 'Stop and send' })).toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
      expect(stop).not.toHaveBeenCalled();
      expect(cancel).not.toHaveBeenCalled();
      view.rerender(<App {...props} chatSurfaceMode="compact" />);
      expect(screen.getByRole('button', { name: 'Stop and send' })).toBeVisible();
      expect(screen.getByText('0:01')).toBeVisible();

      fireEvent.click(screen.getByRole('button', { name: 'Minimize' }));
      view.rerender(<App {...props} chatSurfaceMode="minimized" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(28_800); });
      expect(stop).toHaveBeenCalledOnce();
      view.rerender(<App {...props} chatSurfaceMode="compact" />);
      expect(screen.getByRole('button', { name: 'Preview recording' })).toHaveTextContent('0:30');
      expect(screen.getByRole('button', { name: 'Send recording' })).toBeEnabled();
      expect(send).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('preserves preview after uncertain delivery and provides no resend action', async () => {
    const send = vi.fn(async () => { throw new Error('socket closed after write'); });
    const busy = vi.fn();
    render(<AudioMessageComposer target="YUI" disabled={false} send={send} onBusyChange={busy} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Delivery could not be confirmed'));
    expect(showStatusToast).toHaveBeenCalledWith(screen.getByRole('alert').textContent, expect.any(Number));
    expect(busy).toHaveBeenLastCalledWith(false);
    expect(screen.getByLabelText('Preview recording')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Send recording' })).toBeNull();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it.each(['busy', 'storage', 'manual_unavailable'])('keeps the same recording ID and disables retries while blocked after %s', async reason => {
    const rejected = new Error(reason);
    rejected.name = 'AudioMessageRejected';
    let rejectSend!: (error: Error) => void;
    const send = vi.fn<(_payload: AudioMessagePayload) => Promise<unknown>>()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSend = reject; }))
      .mockResolvedValueOnce({});
    const props = { target: 'YUI', send, onBusyChange: () => {} };
    const view = render(<AudioMessageComposer {...props} disabled={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record a voice message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    view.rerender(<AudioMessageComposer {...props} disabled />);
    await act(async () => { rejectSend(rejected); });
    if (reason === 'manual_unavailable') {
      expect(screen.getByRole('alert')).toHaveTextContent('could not confirm recording support');
    }
    expect(showStatusToast).toHaveBeenCalledWith(screen.getByRole('alert').textContent, expect.any(Number));
    const retry = screen.getByRole('button', { name: 'Send recording' });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(send).toHaveBeenCalledTimes(1);
    view.rerender(<AudioMessageComposer {...props} disabled={false} />);
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    await screen.findByRole('button', { name: 'Record a voice message' });
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[0][0].requestId).toBe(send.mock.calls[1][0].requestId);
  });
});
