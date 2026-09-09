import { useEffect, useRef, useState } from 'react';
import type { HtmlCard } from './message-schema';
import { openExternalUrl } from './openExternal';
import { i18n } from './i18n';
import { getCardErrorMessage } from './htmlCardErrors';

// Installed plugins supply HTML/CSS, not executable page scripts. The parent
// owns DOM listeners; do not add allow-scripts to this iframe.
const DOCUMENT = '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="script-src \'none\'; object-src \'none\'; frame-src \'none\'; form-action \'none\'"></head><body></body></html>';

type PendingAction = {
  binding: string;
  controller: AbortController;
  timeout: number;
};

// One observer for all mounted cards. Only theme changes schedule work; no
// subtree observation, polling or per-card observer. The transition ending also
// needs a sync so we do not retain an intermediate computed text color.
const themeListeners = new Set<() => void>();
let themeObserver: MutationObserver | null = null;
let themeFrame: number | null = null;
function subscribeCardTheme(listener: () => void) {
  themeListeners.add(listener);
  if (!themeObserver) {
    const root = document.documentElement;
    const signature = () => `${root.getAttribute('data-theme')}|${root.classList.contains('theme-transitioning')}`;
    let previous = signature();
    themeObserver = new MutationObserver(() => {
      const next = signature();
      if (next === previous) return;
      previous = next;
      if (themeFrame !== null) return;
      themeFrame = window.requestAnimationFrame(() => {
        themeFrame = null;
        themeListeners.forEach(sync => sync());
      });
    });
    themeObserver.observe(root, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  }
  return () => {
    themeListeners.delete(listener);
    if (!themeListeners.size) {
      themeObserver?.disconnect();
      themeObserver = null;
      if (themeFrame !== null) window.cancelAnimationFrame(themeFrame);
      themeFrame = null;
    }
  };
}

export default function HtmlCardBlock({ block }: { block: HtmlCard }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const updateRef = useRef<((card: HtmlCard) => void) | null>(null);
  const [feedback, setFeedback] = useState('');
  const identity = JSON.stringify([block.pluginId, block.cardId, block.targetLanlan, block.presentation || 'chat']);
  // Schema parsing recreates objects on every chat update. Compare content,
  // and keep request lifetime separate from rendering a partial card update.
  const definition = JSON.stringify(block);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const [pluginId, cardId, targetLanlan, presentation] = JSON.parse(identity) as string[];
    let card: HtmlCard | null = null;
    let doc: Document | null = null;
    let renderedHtml: string | undefined;
    let renderedCss: string | undefined;
    let disposed = false;
    let detach = () => {};
    const requests = new Map<string, PendingAction>();
    const disabledStates = new WeakMap<HTMLButtonElement, boolean>();
    let feedbackOwner: PendingAction | null = null;
    setFeedback('');

    const syncButtons = () => {
      doc?.querySelectorAll<HTMLButtonElement>('button[data-neko-action]').forEach(button => {
        if (requests.has(button.dataset.nekoAction || '')) {
          if (!disabledStates.has(button)) disabledStates.set(button, button.disabled);
          button.disabled = true;
        } else if (disabledStates.has(button)) {
          button.disabled = disabledStates.get(button)!;
          disabledStates.delete(button);
        }
      });
    };
    const resize = () => {
      if (!disposed && doc?.body) frame.style.height = `${Math.min(640, Math.max(48, doc.body.scrollHeight))}px`;
    };
    const syncAppearance = () => {
      if (!doc?.body || disposed) return;
      const parentStyle = getComputedStyle(frame.parentElement || frame);
      doc.body.style.color = parentStyle.color;
      doc.body.style.fontFamily = parentStyle.fontFamily;
    };
    const renderDocument = () => {
      if (!card || !doc?.body || disposed) return;
      if (renderedCss !== card.css) {
        doc.head.querySelector('[data-card-style]')?.remove();
        const style = doc.createElement('style');
        style.dataset.cardStyle = '';
        style.textContent = 'body{margin:0;padding:12px;overflow-wrap:anywhere}*{box-sizing:border-box}img{max-width:100%}button:disabled{opacity:.6}' + card.css;
        doc.head.appendChild(style);
        renderedCss = card.css;
      }
      if (renderedHtml !== card.html) {
        doc.body.innerHTML = card.html;
        renderedHtml = card.html;
      }
      doc.documentElement.lang = document.documentElement.lang;
      syncAppearance();
      syncButtons();
      resize();
    };
    const update = (next: HtmlCard) => {
      card = next;
      for (const [actionId, pending] of requests) {
        if (JSON.stringify(next.actions[actionId]) === pending.binding) continue;
        // Changing/removing the action invalidates its response, independently
        // of cosmetic HTML/CSS/summary changes to the same running action.
        requests.delete(actionId);
        window.clearTimeout(pending.timeout);
        pending.controller.abort('card-action-changed');
        if (feedbackOwner === pending) feedbackOwner = null;
      }
      if (!feedbackOwner) setFeedback('');
      renderDocument();
    };

    const click = async (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest || !card) return;
      const button = target.closest<HTMLButtonElement>('button[data-neko-action]');
      if (!button) {
        const link = target.closest<HTMLAnchorElement>('a[href]');
        if (link) { event.preventDefault(); openExternalUrl(link.getAttribute('href') || ''); }
        return;
      }
      event.preventDefault();
      const actionId = button.dataset.nekoAction || '';
      const action = card.actions[actionId];
      if (!action || button.disabled || requests.has(actionId)) return;
      const controller = new AbortController();
      const pending: PendingAction = {
        binding: JSON.stringify(action), controller,
        timeout: window.setTimeout(() => controller.abort('timeout'), 30000),
      };
      requests.set(actionId, pending);
      feedbackOwner = pending;
      syncButtons();
      setFeedback(i18n('chat.cardActionRunning', 'Working…'));
      const isCurrent = () => !disposed && requests.get(actionId) === pending;
      try {
        const response = await fetch(`/api/plugin-cards/${encodeURIComponent(pluginId)}/action/${encodeURIComponent(action.entry)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ card_id: cardId, target_lanlan: targetLanlan, presentation,
            args: action.args || {}, locale: document.documentElement.lang || 'en' }),
          signal: controller.signal,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(getCardErrorMessage(payload));
        if (isCurrent() && feedbackOwner === pending) setFeedback(typeof payload.result?.message === 'string'
          ? payload.result.message : i18n('chat.cardActionDone', 'Done'));
      } catch (error) {
        if (isCurrent() && feedbackOwner === pending) setFeedback(controller.signal.reason === 'timeout'
          ? i18n('chat.cardActionTimeout', 'Request timed out; the action may have completed.')
          : error instanceof Error ? error.message : getCardErrorMessage(error));
      } finally {
        window.clearTimeout(pending.timeout);
        if (isCurrent()) {
          requests.delete(actionId);
          if (feedbackOwner === pending) feedbackOwner = null;
          syncButtons();
        }
      }
    };
    const mount = () => {
      detach();
      doc = frame.contentDocument;
      renderedHtml = renderedCss = undefined;
      if (!doc?.body || disposed) return;
      const mountedDoc = doc;
      const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
      observer?.observe(mountedDoc.body);
      mountedDoc.addEventListener('load', resize, true);
      mountedDoc.addEventListener('click', click);
      detach = () => {
        observer?.disconnect();
        mountedDoc.removeEventListener('load', resize, true);
        mountedDoc.removeEventListener('click', click);
      };
      renderDocument();
    };
    updateRef.current = update;
    const unsubscribeTheme = subscribeCardTheme(syncAppearance);
    frame.addEventListener('load', mount);
    if (frame.contentDocument?.readyState === 'complete') mount();
    return () => {
      disposed = true;
      unsubscribeTheme();
      updateRef.current = null;
      frame.removeEventListener('load', mount);
      detach();
      requests.forEach(pending => {
        window.clearTimeout(pending.timeout);
        pending.controller.abort('card-disposed');
      });
      requests.clear();
    };
  }, [identity]);

  useEffect(() => {
    updateRef.current?.(JSON.parse(definition));
  }, [definition]);

  return <div className="message-block message-block-html-card">
    <iframe ref={frameRef} srcDoc={DOCUMENT} sandbox="allow-same-origin"
      title={block.summary || block.pluginId} style={{ width: '100%', height: 48, border: 0, display: 'block' }} />
    {feedback ? <div role="status" className="message-card-feedback">{feedback}</div> : null}
  </div>;
}
