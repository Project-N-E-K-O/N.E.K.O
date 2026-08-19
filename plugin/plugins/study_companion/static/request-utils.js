(function initializeStudyRequestUtils() {
  'use strict';

  function isAbortError(error) {
    return error instanceof DOMException && error.name === 'AbortError';
  }

  async function fetchWithTimeout({ url, init = {}, timeoutMs, signal, translate }) {
    if (timeoutMs <= 0) {
      throw new Error(translate('ui.error.plugin_call_timeout', 'Plugin call timed out'));
    }
    const controller = new AbortController();
    const relayAbort = () => controller.abort();
    if (signal?.aborted) relayAbort();
    signal?.addEventListener('abort', relayAbort);
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: controller.signal });
    } catch (error) {
      if (isAbortError(error)) {
        if (signal?.aborted) throw new Error('plugin_call_aborted');
        throw new Error(translate('ui.error.plugin_call_timeout', 'Plugin call timed out'));
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
      signal?.removeEventListener('abort', relayAbort);
    }
  }

  window.StudyRequestUtils = Object.freeze({ fetchWithTimeout });
}());
