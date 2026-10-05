import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import MessageBlockView from './MessageBlockView';
import { parseChatMessage, type MessageBlock } from './message-schema';
import { MEME_IMAGE_LOAD_FAILED_STICKER_URL } from './memeImageFallback';

const NativeURL = URL;
const fetchImage = vi.fn();
const createObjectURL = vi.fn();
const revokeObjectURL = vi.fn();
const downloads: { href: string; filename: string; connected: boolean }[] = [];
const message = parseChatMessage({
  id: 'plugin-selfie', role: 'system', author: 'selfie', time: '10:00',
  blocks: [], status: 'sent',
});

function imageResponse(type = 'image/jpeg', data = 'image bytes') {
  const blob = new Blob([data], { type });
  return { ok: true, blob: vi.fn().mockResolvedValue(blob) };
}

function showImage(url = '/media/selfie-id', interactive = true) {
  const block: MessageBlock = { type: 'image', url, alt: 'Selfie' };
  return render(<MessageBlockView block={block} message={message} interactive={interactive} />);
}

beforeEach(() => {
  downloads.length = 0;
  fetchImage.mockReset().mockResolvedValue(imageResponse());
  createObjectURL.mockReset().mockReturnValue('blob:download-image');
  revokeObjectURL.mockReset();
  vi.stubGlobal('fetch', fetchImage);
  vi.stubGlobal('URL', Object.assign(class extends NativeURL {}, { createObjectURL, revokeObjectURL }));
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ href: this.href, filename: this.download, connected: this.isConnected });
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('saving chat images', () => {
  it.each([
    ['/media/selfie-id', 'image/jpeg', 'jpg'],
    ['data:image/png;base64,aW1hZ2U=', 'image/png', 'png'],
    ['blob:local-image', 'image/webp', 'webp'],
    ['https://images.example/animation', 'image/gif', 'gif'],
  ])('downloads %s without changing its bytes', async (url, mime, extension) => {
    const response = imageResponse(mime);
    const originalBlob = await response.blob();
    fetchImage.mockResolvedValue(response);
    showImage(url);
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(fetchImage).toHaveBeenCalledWith(url, {
      signal: expect.any(AbortSignal), credentials: 'same-origin',
    });
    expect(createObjectURL).toHaveBeenCalledWith(originalBlob);
    expect(downloads[0]).toEqual({
      href: 'blob:download-image',
      filename: expect.stringMatching(new RegExp(`^neko-image-\\d+\\.${extension}$`)),
      connected: true,
    });
    expect(document.querySelector('a[download]')).toBeNull();
    expect(screen.getByRole('button').querySelector('path')).toHaveAttribute('d', 'M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4');
    expect(screen.getByRole('button', { name: 'Save image' })).toBeEnabled();
  });

  it('does not show save controls in export previews or selection mode', () => {
    showImage('/media/selfie-id', false);
    expect(screen.getByRole('img', { name: 'Selfie' })).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(fetchImage).not.toHaveBeenCalled();
  });

  it('prevents duplicate downloads and does not click the containing message', async () => {
    let finish!: (response: ReturnType<typeof imageResponse>) => void;
    fetchImage.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const parentClick = vi.fn();
    render(
      <div onClick={parentClick}>
        <MessageBlockView block={{ type: 'image', url: '/media/selfie-id' }} message={message} />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    const saving = screen.getByRole('button', { name: 'Saving…' });
    expect(saving).toBeDisabled();
    expect(saving).toHaveAttribute('aria-busy', 'true');
    expect(parentClick).not.toHaveBeenCalled();
    fireEvent.click(saving);
    expect(fetchImage).toHaveBeenCalledTimes(1);
    await act(async () => { finish(imageResponse()); });
    expect(downloads).toHaveLength(1);
  });

  it.each(['missing', 'html', 'empty', 'cors'])('shows a retryable error for %s responses', async (kind) => {
    if (kind === 'missing') fetchImage.mockResolvedValue({ ok: false, status: 404 });
    if (kind === 'html') fetchImage.mockResolvedValue(imageResponse('text/html'));
    if (kind === 'empty') fetchImage.mockResolvedValue(imageResponse('image/jpeg', ''));
    if (kind === 'cors') fetchImage.mockRejectedValue(new TypeError('Failed to fetch'));
    showImage();
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the image');
    expect(downloads).toHaveLength(0);
    expect(createObjectURL).not.toHaveBeenCalled();
    fetchImage.mockResolvedValue(imageResponse());
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it.each(['javascript:alert(1)', 'file:///private/image.jpg', 'data:text/html,hello'])('does not fetch unsafe URL %s', async url => {
    showImage(url);
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(fetchImage).not.toHaveBeenCalled();
    expect(downloads).toHaveLength(0);
  });

  it('aborts a stalled download after 15 seconds and restores the button', async () => {
    vi.useFakeTimers();
    fetchImage.mockImplementation((_url, { signal }: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    showImage();
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    await act(async () => { vi.advanceTimersByTime(15_000); });
    expect(fetchImage.mock.calls[0][1].signal.aborted).toBe(true);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save image' })).toBeEnabled();
    expect(downloads).toHaveLength(0);
  });

  it('cancels on unmount and never downloads a late response', async () => {
    let finish!: (response: ReturnType<typeof imageResponse>) => void;
    fetchImage.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const { unmount } = showImage();
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    const signal = fetchImage.mock.calls[0][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { finish(imageResponse()); });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(downloads).toHaveLength(0);
  });

  it('releases the temporary download URL after the browser can consume it', async () => {
    vi.useFakeTimers();
    showImage();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save image' })); });
    expect(downloads).toHaveLength(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:download-image');
  });

  it('does not save the failed meme placeholder and resets when the URL changes', () => {
    const { rerender } = showImage('/api/meme/proxy-image?url=missing');
    fireEvent.error(screen.getByRole('img', { name: 'Selfie' }));
    expect(screen.getByRole('img')).toHaveAttribute('src', MEME_IMAGE_LOAD_FAILED_STICKER_URL);
    expect(screen.getByRole('button', { name: 'Save image' })).toBeDisabled();
    rerender(<MessageBlockView block={{ type: 'image', url: '/media/new-selfie', alt: 'New selfie' }} message={message} />);
    expect(screen.getByRole('button', { name: 'Save image' })).toBeEnabled();
    expect(screen.getByRole('img')).toHaveAttribute('src', '/media/new-selfie');
  });

  it('allows another save and restores the download icon after failure', async () => {
    showImage();
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    let fail!: (reason: Error) => void;
    fetchImage.mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
    fireEvent.click(screen.getByRole('button', { name: 'Save image' }));
    expect(screen.getByRole('button').querySelector('circle')).toBeInTheDocument();
    await act(async () => { fail(new TypeError('Failed to fetch')); });
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button')).toBeEnabled();
    expect(screen.getByRole('button').querySelector('path')).toHaveAttribute('d', 'M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4');
    expect(downloads).toHaveLength(1);
  });

  it('uses localized labels', () => {
    const labels: Record<string, string> = { 'chat.saveImage': '保存图片' };
    vi.stubGlobal('t', (key: string) => labels[key] || key);
    showImage();
    expect(screen.getByRole('button', { name: '保存图片' })).toBeInTheDocument();
  });
});
