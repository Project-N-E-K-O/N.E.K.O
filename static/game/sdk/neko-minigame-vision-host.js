/** Trusted one-shot, current-tab capture. No desktop fallback or persistent stream. */
(() => {
  'use strict';
  if (window.NekoMiniGameVisionHost) return;
  // One raw picker/capture per document, including an uninterruptible late picker.
  const occupied = new WeakSet();
  const inputOccupied = new WeakSet();
  const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 6 * 1024 * 1024;
  const MAX_DATA_URL_CHARS = Math.ceil(MAX_IMAGE_BYTES / 3) * 4 + 64;
  const imageTypes = ['image/jpeg', 'image/png', 'image/webp'];
  const error = (code, message) => Object.assign(new Error(message), { code });
  const invalid = () => { throw error('invalid_region', 'The region must be a visible, non-empty rectangle'); };
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  function keys(value, allowed) {
    if (!object(value) || Object.keys(value).some(key => !allowed.includes(key))) invalid();
  }
  function viewport(w) {
    const width = w.innerWidth;
    const height = w.innerHeight;
    if (![width, height].every(n => Number.isFinite(n) && n > 0 && n <= 32768)
      || (w.visualViewport && (w.visualViewport.scale !== 1
        || w.visualViewport.offsetLeft !== 0 || w.visualViewport.offsetTop !== 0))) {
      throw error('capture_unavailable', 'The current viewport cannot be mapped safely');
    }
    return { width, height, scrollX: w.scrollX || 0, scrollY: w.scrollY || 0 };
  }
  function resolveRegion(region, w) {
    const view = viewport(w);
    let x; let y; let width; let height;
    if (region?.kind === 'element') {
      keys(region, ['kind', 'selector']);
      if (typeof region.selector !== 'string' || !region.selector.trim() || region.selector.length > 512) invalid();
      let elements;
      try { elements = w.document.querySelectorAll(region.selector); } catch (_) { invalid(); }
      if (elements.length !== 1 || !elements[0].isConnected) invalid();
      ({ x, y, width, height } = elements[0].getBoundingClientRect());
    } else {
      if (!['px', 'percent'].includes(region?.unit)) invalid();
      const coordinate = (value, axis) => {
        if (!Number.isFinite(value) || value < 0 || (region.unit === 'percent' && value > 100)) invalid();
        return region.unit === 'percent' ? value * view[axis] / 100 : value;
      };
      if (region.kind === 'rect') {
        keys(region, ['kind', 'unit', 'x', 'y', 'width', 'height']);
        x = coordinate(region.x, 'width'); y = coordinate(region.y, 'height');
        width = coordinate(region.width, 'width'); height = coordinate(region.height, 'height');
      } else if (region.kind === 'edges') {
        keys(region, ['kind', 'unit', 'left', 'top', 'right', 'bottom']);
        x = coordinate(region.left, 'width'); y = coordinate(region.top, 'height');
        width = view.width - x - coordinate(region.right, 'width');
        height = view.height - y - coordinate(region.bottom, 'height');
      } else if (region.kind === 'corners') {
        keys(region, ['kind', 'unit', 'topLeft', 'topRight', 'bottomRight', 'bottomLeft']);
        const points = ['topLeft', 'topRight', 'bottomRight', 'bottomLeft'].map(key => {
          keys(region[key], ['x', 'y']);
          return { x: coordinate(region[key].x, 'width'), y: coordinate(region[key].y, 'height') };
        });
        const [tl, tr, br, bl] = points;
        // Never silently expand a polygon into its bounding rectangle.
        if (tl.y !== tr.y || tr.x !== br.x || br.y !== bl.y || bl.x !== tl.x) invalid();
        x = tl.x; y = tl.y; width = tr.x - x; height = bl.y - y;
      } else invalid();
    }
    if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0
      || x + width > view.width || y + height > view.height) invalid();
    return Object.freeze({ x, y, width, height, viewportWidth: view.width, viewportHeight: view.height });
  }
  function captureAvailable(w) {
    return !!(w.isSecureContext && w.top === w && w.crypto?.randomUUID
      && w.navigator?.mediaDevices?.getDisplayMedia && w.navigator.mediaDevices.setCaptureHandleConfig
      && w.MediaStreamTrack?.prototype?.getCaptureHandle
      && w.HTMLVideoElement?.prototype?.requestVideoFrameCallback);
  }
  function available(w) {
    return captureAvailable(w) || !!(w.isSecureContext && w.Blob && w.fetch && w.btoa);
  }

  async function normalizeAttachments(attachments, { windowImpl: w = window, signal } = {}) {
    const invalidImage = () => { throw error('invalid_image', 'Expected a bounded JPEG, PNG or WebP image'); };
    const check = () => { if (signal?.aborted) throw error('cancelled', 'Image input cancelled'); };
    check();
    if (inputOccupied.has(w)) throw error('busy', 'Image input is still pending');
    if (!Array.isArray(attachments) || attachments.length < 1 || attachments.length > 4) invalidImage();
    // Snapshot metadata/buffers before the first await; Blob is immutable.
    const inputs = attachments.map(item => {
      if (!object(item) || Object.keys(item).some(key => !['type', 'source', 'label', 'mimeType'].includes(key))) invalidImage();
      if (item.type !== 'image') throw error('unsupported_attachment', 'Only image attachments are supported');
      const label = item.label === undefined ? '' : item.label;
      const mimeType = item.mimeType;
      if (typeof label !== 'string' || label.length > 128 || (mimeType !== undefined && !imageTypes.includes(mimeType))) invalidImage();
      let source = item.source;
      if (typeof source === 'string') {
        if (!source || source.length > (source.startsWith('data:') ? MAX_DATA_URL_CHARS : 2048)) invalidImage();
      } else if (w.Blob && source instanceof w.Blob) {
        if (source.size < 1 || source.size > MAX_IMAGE_BYTES) invalidImage();
      } else if (source instanceof ArrayBuffer || source instanceof Uint8Array) {
        if (!source.byteLength || source.byteLength > MAX_IMAGE_BYTES || !mimeType) invalidImage();
        source = source instanceof ArrayBuffer ? source.slice(0) : new Uint8Array(source);
      } else invalidImage();
      return { source, label, mimeType };
    });
    inputOccupied.add(w);
    let total = 0;
    const result = [];
    const account = size => {
      if (!size || size > MAX_IMAGE_BYTES || total + size > MAX_TOTAL_BYTES) invalidImage();
      total += size;
    };
    try {
      for (const item of inputs) {
        check();
        let blob;
        let source = item.source;
        if (typeof source === 'string' && source.startsWith('data:')) {
          const match = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(source);
          if (!match || match[2].length % 4 || (item.mimeType && item.mimeType !== match[1])) invalidImage();
          const size = match[2].length / 4 * 3 - (match[2].endsWith('==') ? 2 : match[2].endsWith('=') ? 1 : 0);
          account(size);
          result.push({ type:'image', image_data_url:source, label:item.label });
          continue;
        }
        if (typeof source === 'string') {
          let url;
          try { url = new URL(source, w.location.href || w.location.origin); } catch (_) { invalidImage(); }
          if (!['http:', 'https:', 'blob:'].includes(url.protocol) || url.username || url.password
            || (url.protocol === 'blob:' && url.origin !== w.location.origin)) invalidImage();
          // Browser CORS remains authoritative; never proxy private URLs via the server.
          const response = await w.fetch(url.href, { signal, mode:'cors', credentials:'omit', redirect:'error', referrerPolicy:'no-referrer' });
          if (signal?.aborted || !response.ok || response.type === 'opaque') {
            const cancellation = response.body?.cancel?.(); cancellation?.catch?.(() => {});
            check(); throw error('image_unavailable', 'Image URL is unavailable or not CORS-readable');
          }
          const mime = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
          const declared = Number(response.headers.get('Content-Length') || 0);
          if (!imageTypes.includes(mime) || (item.mimeType && item.mimeType !== mime)
            || declared > MAX_IMAGE_BYTES || total + declared > MAX_TOTAL_BYTES || !response.body?.getReader) {
            const cancellation = response.body?.cancel?.(); cancellation?.catch?.(() => {});
            invalidImage();
          }
          const reader = response.body.getReader();
          const chunks = []; let bytes = 0; let complete = false;
          const abort = () => { const cancellation = reader.cancel(); cancellation?.catch?.(() => {}); };
          signal?.addEventListener('abort', abort, {once:true});
          try {
            check();
            while (true) {
              const {done, value} = await reader.read(); check();
              if (done) { complete = true; break; }
              bytes += value.byteLength;
              if (bytes > MAX_IMAGE_BYTES || total + bytes > MAX_TOTAL_BYTES) invalidImage();
              chunks.push(value);
            }
            blob = new w.Blob(chunks, {type:mime});
          } finally {
            signal?.removeEventListener('abort', abort);
            if (!complete) abort();
            reader.releaseLock(); chunks.length = 0;
          }
        } else if (w.Blob && source instanceof w.Blob) {
          const mime = source.type || item.mimeType;
          if (!imageTypes.includes(mime) || (source.type && item.mimeType && source.type !== item.mimeType)) invalidImage();
          blob = source.type ? source : new w.Blob([source], {type:mime});
        } else {
          blob = new w.Blob([source], {type:item.mimeType});
        }
        account(blob.size);
        const bytes = new Uint8Array(await blob.arrayBuffer()); check();
        let binary = '';
        for (let offset=0;offset<bytes.length;offset+=8192) binary += String.fromCharCode(...bytes.subarray(offset,offset+8192));
        source = `data:${blob.type};base64,${w.btoa(binary)}`;
        result.push({type:'image', image_data_url:source, label:item.label});
      }
      check();
      return result;
    } catch (cause) {
      check();
      if (cause?.code) throw cause;
      throw error('image_unavailable', 'Image input could not be read');
    } finally {
      inputOccupied.delete(w);
    }
  }
  async function capture(region, { windowImpl: w = window, signal, timeoutMs = 30000 } = {}) {
    if (!captureAvailable(w)) throw error('capture_unavailable', 'Verified current-tab capture is unavailable');
    if (signal?.aborted) throw error('cancelled', 'Capture cancelled');
    if (occupied.has(w)) throw error('busy', 'A capture or permission picker is still pending');
    const rect = resolveRegion(region, w);
    const initialView = viewport(w);
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1) throw error('invalid_timeout', 'Invalid capture timeout');
    occupied.add(w);
    const media = w.navigator.mediaDevices;
    const handle = w.crypto.randomUUID();
    let stream; let video; let canvas; let track; let frameId;
    let timer; let rejectCancellation; let cancelledError; let configured = false;
    const cancellation = new Promise((_, reject) => { rejectCancellation = reject; });
    const stopStream = value => { for (const item of value?.getTracks?.() || []) { try { item.stop(); } catch (_) {} } };
    const release = () => {
      track?.removeEventListener?.('capturehandlechange', sourceChanged);
      track?.removeEventListener?.('ended', ended);
      if (frameId !== undefined) video?.cancelVideoFrameCallback?.(frameId);
      stopStream(stream);
      if (video) { try { video.pause(); video.srcObject = null; video.remove(); } catch (_) {} }
      if (canvas) { canvas.width = 0; canvas.height = 0; }
      if (configured) { try { media.setCaptureHandleConfig({}); } catch (_) {} configured = false; }
    };
    const cancel = (code, message) => {
      if (cancelledError) return;
      cancelledError = error(code, message);
      release();
      rejectCancellation(cancelledError);
    };
    const externalAbort = () => cancel('cancelled', 'Capture cancelled');
    const ended = () => cancel('cancelled', 'Tab sharing ended');
    const sourceChanged = () => cancel('capture_source_mismatch', 'The shared page changed');
    const verify = () => {
      if (cancelledError) throw cancelledError;
      if (track?.getCaptureHandle?.()?.handle !== handle || track?.getSettings?.().displaySurface !== 'browser') {
        throw error('capture_source_mismatch', 'Select this game tab, not a window, desktop or another tab');
      }
      const current = viewport(w);
      if (Object.keys(initialView).some(key => current[key] !== initialView[key])) {
        throw error('capture_changed', 'The page moved or resized during capture; retry');
      }
      const latest = resolveRegion(region, w);
      if (Object.keys(rect).some(key => latest[key] !== rect[key])) {
        throw error('capture_changed', 'The selected element moved during capture; retry');
      }
    };
    signal?.addEventListener('abort', externalAbort, { once: true });
    w.addEventListener('pagehide', externalAbort, { once: true });
    timer = w.setTimeout(() => cancel('timeout', 'Capture timed out'), Math.min(timeoutMs, 30000));
    const raw = (async () => {
      try {
        media.setCaptureHandleConfig({ handle, exposeOrigin: false, permittedOrigins: [w.location.origin] });
        configured = true;
        // Must stay ahead of any asynchronous network call: requires user activation.
        stream = await media.getDisplayMedia({ video: true, audio: false, preferCurrentTab: true,
          selfBrowserSurface: 'include', surfaceSwitching: 'exclude', monitorTypeSurfaces: 'exclude' });
        if (cancelledError) { stopStream(stream); throw cancelledError; }
        [track] = stream.getVideoTracks();
        verify();
        track.addEventListener('capturehandlechange', sourceChanged);
        track.addEventListener('ended', ended);
        video = w.document.createElement('video');
        video.muted = true; video.playsInline = true; video.srcObject = stream;
        const frame = new Promise(resolve => { frameId = video.requestVideoFrameCallback(resolve); });
        await Promise.race([Promise.all([video.play(), frame]), cancellation]);
        verify();
        const scaleX = video.videoWidth / rect.viewportWidth;
        const scaleY = video.videoHeight / rect.viewportHeight;
        if (![scaleX, scaleY].every(n => Number.isFinite(n) && n > 0)
          || Math.abs(scaleX / scaleY - 1) > 0.01) {
          throw error('capture_unavailable', 'The shared image does not match the game viewport');
        }
        // Round inward, so subpixel selection never includes pixels outside it.
        const sx = Math.ceil(rect.x * scaleX); const sy = Math.ceil(rect.y * scaleY);
        const sw = Math.floor((rect.x + rect.width) * scaleX) - sx;
        const sh = Math.floor((rect.y + rect.height) * scaleY) - sy;
        if (sw < 1 || sh < 1) invalid();
        const ratio = Math.min(1, 1280 / sw, 720 / sh);
        canvas = w.document.createElement('canvas');
        canvas.width = Math.max(1, Math.floor(sw * ratio)); canvas.height = Math.max(1, Math.floor(sh * ratio));
        const context = canvas.getContext('2d', { alpha: false });
        if (!context) throw error('capture_unavailable', 'Canvas capture is unavailable');
        context.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        verify();
        const imageDataUrl = canvas.toDataURL('image/jpeg', 0.8);
        if (!imageDataUrl.startsWith('data:image/jpeg;base64,') || imageDataUrl.length > 2 * 1024 * 1024) {
          throw error('quota_exceeded', 'The captured image exceeds the image budget');
        }
        return Object.freeze({ imageDataUrl, width: canvas.width, height: canvas.height, region: rect });
      } catch (cause) {
        if (cause?.name === 'NotAllowedError') throw error('capture_denied', 'Tab sharing was not authorized');
        if (cause?.code) throw cause;
        throw error('capture_unavailable', 'Current-tab capture failed');
      } finally { release(); occupied.delete(w); }
    })();
    try { return await Promise.race([raw, cancellation]); }
    finally {
      w.clearTimeout(timer);
      signal?.removeEventListener('abort', externalAbort);
      w.removeEventListener('pagehide', externalAbort);
      release();
    }
  }
  Object.defineProperty(window, 'NekoMiniGameVisionHost', { value: Object.freeze({ available, captureAvailable, resolveRegion, capture, normalizeAttachments }) });
})();
