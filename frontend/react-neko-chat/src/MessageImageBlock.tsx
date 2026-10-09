import { useLayoutEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import { isMemeProxyImageUrl, swapImageToMemeLoadFailedSticker } from './memeImageFallback';
import type { MessageBlock } from './message-schema';

type ImageBlock = Extract<MessageBlock, { type: 'image' }>;

const IMAGE_DOWNLOAD_TIMEOUT_MS = 15_000;
const IMAGE_EXTENSIONS: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/apng': 'png',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/avif': 'avif',
  'image/bmp': 'bmp',
  'image/svg+xml': 'svg',
  'image/tiff': 'tiff',
  'image/x-icon': 'ico',
  'image/vnd.microsoft.icon': 'ico',
  'image/heic': 'heic',
  'image/heif': 'heif',
};

export default function MessageImageBlock({
  block,
  interactive,
}: {
  block: ImageBlock;
  interactive: boolean;
}) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [showingFallback, setShowingFallback] = useState(false);
  const download = useRef<AbortController | null>(null);

  useLayoutEffect(() => () => {
    download.current?.abort();
    download.current = null;
  }, []);

  async function saveImage() {
    if (download.current || showingFallback) return;
    const controller = new AbortController();
    download.current = controller;
    setSaving(true);
    setFailed(false);
    const timeout = window.setTimeout(() => controller.abort(), IMAGE_DOWNLOAD_TIMEOUT_MS);

    try {
      const url = new URL(block.url, window.location.href);
      if (!['http:', 'https:', 'blob:', 'data:'].includes(url.protocol)
        || (url.protocol === 'data:' && !/^data:image\//i.test(block.url))) {
        throw new Error('Unsupported image URL');
      }
      const response = await fetch(block.url, {
        signal: controller.signal,
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('Image unavailable');
      const blob = await response.blob();
      const mime = blob.type.split(';')[0].trim().toLowerCase();
      if (!mime.startsWith('image/') || !blob.size) throw new Error('Invalid image response');
      if (download.current !== controller || controller.signal.aborted) return;

      // Download the served bytes directly: no canvas conversion or recompression.
      const objectUrl = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = `neko-image-${Date.now()}.${IMAGE_EXTENSIONS[mime] || 'img'}`;
        link.hidden = true;
        document.body.appendChild(link);
        try {
          link.click();
        } finally {
          link.remove();
        }
      } finally {
        // Allow the browser to consume the URL before releasing its backing data.
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
      }
    } catch {
      if (download.current === controller) setFailed(true);
    } finally {
      window.clearTimeout(timeout);
      if (download.current === controller) {
        download.current = null;
        setSaving(false);
      }
    }
  }

  const imageLoadingProps = isMemeProxyImageUrl(block.url)
    ? { loading: 'eager' as const, fetchpriority: 'high' as const }
    : { loading: 'lazy' as const };

  const saveLabel = saving ? i18n('chat.savingImage', 'Saving…') : i18n('chat.saveImage', 'Save image');

  return (
    <>
      <figure className={`message-block message-block-image${interactive ? ' message-block-image-interactive' : ''}`}>
        <div style={block.width && block.height ? { aspectRatio: `${block.width} / ${block.height}` } : undefined}>
          <img
            src={block.url}
            alt={block.alt || ''}
            {...imageLoadingProps}
            onError={(event) => {
              if (swapImageToMemeLoadFailedSticker(event.currentTarget, block.url)) {
                setShowingFallback(true);
              }
            }}
          />
        </div>
        {interactive ? (
          <button
            className="message-image-save"
            type="button"
            aria-label={saveLabel}
            title={saveLabel}
            aria-disabled={saving || showingFallback}
            aria-busy={saving}
            onClick={(event) => {
              event.stopPropagation();
              void saveImage();
            }}
          >
            <svg className={saving ? 'message-image-save-spinner' : undefined} viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              {saving ? <circle cx="12" cy="12" r="8" strokeDasharray="36 14" /> : <path d="M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4" />}
            </svg>
          </button>
        ) : null}
      </figure>
      {interactive && failed ? (
        <p className="message-image-save-error" role="alert">
          {i18n('chat.saveImageFailed', 'Could not save the image. Please try again or use the image menu in your browser.')}
        </p>
      ) : null}
    </>
  );
}
