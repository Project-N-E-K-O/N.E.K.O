export const MEME_IMAGE_LOAD_FAILED_STICKER_URL = '/static/icons/meme-image-load-failed-sticker.png';

export function isMemeProxyImageUrl(url: string): boolean {
  try {
    const parsed = new URL(url, window.location.href);
    return parsed.pathname === '/api/meme/proxy-image';
  } catch {
    return url.startsWith('/api/meme/proxy-image');
  }
}

export function shouldUseMemeImageLoadFailedSticker(url: string): boolean {
  return isMemeProxyImageUrl(url);
}

export function swapImageToMemeLoadFailedSticker(image: HTMLImageElement, originalUrl: string): boolean {
  if (!shouldUseMemeImageLoadFailedSticker(originalUrl)) {
    return false;
  }
  if (image.dataset.nekoImageLoadFailedSticker === 'true') {
    return false;
  }
  image.dataset.nekoImageLoadFailedSticker = 'true';
  image.src = MEME_IMAGE_LOAD_FAILED_STICKER_URL;
  image.alt = image.alt || 'Image failed to load';
  return true;
}
