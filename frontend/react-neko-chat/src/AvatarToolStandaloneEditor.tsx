import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import AvatarToolCreatePage from './AvatarToolCreatePage';
import AvatarToolEditorWorkspace from './AvatarToolEditorWorkspace';
import { i18n } from './i18n';
import {
  LocalAvatarToolRevisionConflictError,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolDetail,
  type UpdateLocalAvatarToolInput,
} from './avatar-tools/localTools';
import { useLocalAvatarToolCatalog } from './avatar-tools/useLocalAvatarToolCatalog';
import { getAvatarToolItemLabel, isLocalAvatarToolId } from './avatarTools';
import {
  AVATAR_TOOL_EDITOR_WINDOW_NAME,
  confirmDiscardAvatarToolEditorChanges,
  isSameAvatarToolEditorTarget,
} from './AvatarToolItemManager';

declare global {
  interface Window {
    nekoBeforeWindowClose?: () => unknown;
  }
}

const SHARED_WINDOW_REGISTRY_KEY = `neko:named-window:${AVATAR_TOOL_EDITOR_WINDOW_NAME}`;
const SHARED_WINDOW_FOCUS_KEY = `neko:named-window-focus:${AVATAR_TOOL_EDITOR_WINDOW_NAME}`;
const SHARED_WINDOW_CHANNEL = 'neko:named-window';
const SHARED_WINDOW_HEARTBEAT_MS = 1000;

type SharedWindowMessage = {
  type?: unknown;
  windowName?: unknown;
  timestamp?: unknown;
  payload?: { type?: unknown; url?: unknown } | null;
};

function restoreAndFocusEditorWindow() {
  try {
    const control = (window as unknown as { nekoWindowControl?: { restore?: () => unknown } }).nekoWindowControl;
    if (typeof control?.restore === 'function') void Promise.resolve(control.restore()).catch(() => undefined);
  } catch (_) {}
  try { window.focus(); } catch (_) {}
}

type EditorMode = 'create' | 'edit';
type EditorResultAction = 'created' | 'updated' | 'deleted';

function readEditorRequest(): { mode: EditorMode; toolId: `local-${string}` | null } {
  const params = new URLSearchParams(window.location.search);
  const mode = params.get('mode') === 'edit' ? 'edit' : 'create';
  const candidate = params.get('toolId');
  return {
    mode,
    toolId: mode === 'edit' && candidate && isLocalAvatarToolId(candidate) ? candidate : null,
  };
}

function notifyOpener(action: EditorResultAction, toolId?: string) {
  try {
    const opener = window.opener;
    opener?.postMessage({
      type: 'neko:avatar-tool-editor-result',
      action,
      ...(toolId ? { toolId } : {}),
    }, window.location.origin);
    if (opener && !opener.closed) opener.focus();
  } catch (_) {}
}

function closeEditorWindow() {
  window.close();
}

export default function AvatarToolStandaloneEditor() {
  const request = readEditorRequest();
  const catalog = useLocalAvatarToolCatalog();
  const [, setLocaleRevision] = useState(0);
  const [detail, setDetail] = useState<LocalAvatarToolDetail | null>(null);
  const [loading, setLoading] = useState(request.mode === 'edit' && !!request.toolId);
  const [loadError, setLoadError] = useState(request.mode === 'edit' && !request.toolId);
  const [notice, setNotice] = useState('');
  const [specialEnabled, setSpecialEnabled] = useState(false);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const loadRequestRef = useRef(0);
  const dirtyRef = useRef(false);

  useEffect(() => {
    const hasUnsavedChanges = () => dirtyRef.current;
    window.avatarToolEditorHasUnsavedChanges = hasUnsavedChanges;
    return () => {
      if (window.avatarToolEditorHasUnsavedChanges === hasUnsavedChanges) {
        delete window.avatarToolEditorHasUnsavedChanges;
      }
    };
  }, []);

  useEffect(() => {
    // Last line of defense for an edited draft: reload, a window.open(name)
    // navigation from a reloaded opener, or closing the page.
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    // The desktop title-bar close button (window_controls.js) asks this hook
    // first; confirming discards the draft so beforeunload stays quiet.
    const beforeWindowClose = async () => {
      if (!dirtyRef.current) return undefined;
      if (!confirmDiscardAvatarToolEditorChanges()) return { handled: true };
      dirtyRef.current = false;
      return undefined;
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    window.nekoBeforeWindowClose = beforeWindowClose;
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      if (window.nekoBeforeWindowClose === beforeWindowClose) delete window.nekoBeforeWindowClose;
    };
  }, []);

  useEffect(() => {
    // Register in the shared named-window registry (static/common_dialogs.js)
    // so an opener without a live handle focuses this editor and delegates the
    // target switch here instead of navigating over an unsaved draft.
    let lastMessageTimestamp: unknown = null;
    const markActive = () => {
      try {
        window.localStorage.setItem(SHARED_WINDOW_REGISTRY_KEY, JSON.stringify({
          url: window.location.href,
          timestamp: Date.now(),
        }));
      } catch (_) {}
    };
    const clearActive = () => {
      try { window.localStorage.removeItem(SHARED_WINDOW_REGISTRY_KEY); } catch (_) {}
    };
    const handleMessage = (data: SharedWindowMessage | null | undefined) => {
      if (!data || data.windowName !== AVATAR_TOOL_EDITOR_WINDOW_NAME) return;
      if (data.type !== 'neko:named-window-focus' && data.type !== 'neko:named-window-message') return;
      // common_dialogs.js sends each request over BroadcastChannel and storage.
      if (data.timestamp !== undefined && data.timestamp === lastMessageTimestamp) return;
      lastMessageTimestamp = data.timestamp;
      restoreAndFocusEditorWindow();
      const payload = data.payload;
      if (payload?.type !== 'neko:navigate-on-reuse' || typeof payload.url !== 'string') return;
      try {
        const target = new URL(payload.url, window.location.href);
        if (target.origin !== window.location.origin || target.pathname !== window.location.pathname) return;
        if (isSameAvatarToolEditorTarget(window.location.href, target.href)) return;
        if (dirtyRef.current && !confirmDiscardAvatarToolEditorChanges()) return;
        dirtyRef.current = false;
        window.location.replace(target.href);
      } catch (_) {}
    };
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== SHARED_WINDOW_FOCUS_KEY || !event.newValue) return;
      try { handleMessage(JSON.parse(event.newValue) as SharedWindowMessage); } catch (_) {}
    };
    let channel: BroadcastChannel | null = null;
    try {
      if (typeof BroadcastChannel === 'function') {
        channel = new BroadcastChannel(SHARED_WINDOW_CHANNEL);
        channel.onmessage = (event: MessageEvent) => handleMessage(event.data as SharedWindowMessage);
      }
    } catch (_) {
      channel = null;
    }
    markActive();
    const heartbeat = window.setInterval(markActive, SHARED_WINDOW_HEARTBEAT_MS);
    window.addEventListener('storage', handleStorage);
    window.addEventListener('pagehide', clearActive);
    return () => {
      window.clearInterval(heartbeat);
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener('pagehide', clearActive);
      try { channel?.close(); } catch (_) {}
      clearActive();
    };
  }, []);

  const markEdited = useCallback(() => { dirtyRef.current = true; }, []);

  const title = request.mode === 'edit'
    ? i18n('chat.avatarToolUpdateTitle', 'Edit custom tool')
    : i18n('chat.avatarToolCreateTitle', 'Create custom tool');

  useLayoutEffect(() => {
    const refreshLocalizedContent = () => setLocaleRevision((revision) => revision + 1);
    window.addEventListener('localechange', refreshLocalizedContent);

    // i18n may finish between the first React render and effect registration.
    // Re-render once when a translator already exists so that initial fallbacks
    // cannot remain stuck for the lifetime of this standalone window.
    const runtime = window as unknown as Record<string, unknown>;
    if (typeof runtime.safeT === 'function' || typeof runtime.t === 'function') {
      refreshLocalizedContent();
    }

    return () => window.removeEventListener('localechange', refreshLocalizedContent);
  }, []);

  useEffect(() => {
    document.body.classList.add('avatar-tool-editor-page');
    document.title = `${title} - N.E.K.O.`;
    return () => document.body.classList.remove('avatar-tool-editor-page');
  }, [title]);

  useEffect(() => {
    const requestId = ++loadRequestRef.current;
    // Initial loads and retries share ownership; changing the request or
    // unmounting invalidates every outstanding callback, including retries.
    const invalidateLoad = () => { loadRequestRef.current += 1; };
    if (request.mode !== 'edit' || !request.toolId) return invalidateLoad;
    setLoading(true);
    setLoadError(false);
    void catalog.detail(request.toolId).then((nextDetail) => {
      if (requestId !== loadRequestRef.current) return;
      setDetail(nextDetail);
      setSpecialEnabled(!!nextDetail.special);
      setLoading(false);
    }).catch(() => {
      if (requestId !== loadRequestRef.current) return;
      setLoadError(true);
      setLoading(false);
    });
    return invalidateLoad;
  }, [catalog.detail, request.mode, request.toolId]);

  const retryLoad = async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    setLoadError(false);
    try {
      const [, nextDetail] = await Promise.all([
        !catalog.authoritativeLoaded || catalog.refreshFailed ? catalog.refresh() : Promise.resolve(),
        request.toolId && !detail ? catalog.detail(request.toolId) : Promise.resolve(null),
      ]);
      if (requestId !== loadRequestRef.current) return;
      if (nextDetail) {
        setDetail(nextDetail);
        setSpecialEnabled(!!nextDetail.special);
      }
    } catch {
      if (requestId === loadRequestRef.current) setLoadError(true);
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  };

  let content;
  if (loading || ((!catalog.authoritativeLoaded || !catalog.limits) && !catalog.refreshFailed)) {
    content = (
      <div className="avatar-tool-standalone-status" role="status">
        {i18n('chat.avatarToolUpdateLoading', 'Opening…')}
      </div>
    );
  } else if (loadError || !catalog.authoritativeLoaded || !catalog.limits || (request.mode === 'edit' && !detail)) {
    content = (
      <div className="avatar-tool-standalone-status is-error" role="alert">
        <p>{request.mode === 'edit'
          ? i18n('chat.avatarToolUpdateLoadError', 'Could not open this tool. Please try again.')
          : i18n('chat.avatarToolEditorOpenError', 'Could not open the tool editor.')}</p>
        <button type="button" onClick={retryLoad}>
          {i18n('common.retry', 'Retry')}
        </button>
      </div>
    );
  } else {
    content = (
      <AvatarToolCreatePage
        key={detail ? `${detail.id}:${detail.revision}` : 'create'}
        limits={catalog.limits}
        initialDetail={detail ?? undefined}
        existingToolNames={(catalog.items ?? [])
          .filter(item => item.id !== request.toolId)
          .map(getAvatarToolItemLabel)}
        notice={notice}
        onEdit={markEdited}
        onSpecialEnabledChange={setSpecialEnabled}
        onCancel={closeEditorWindow}
        showCancelAction={false}
        onSave={async (input) => {
          if (request.mode === 'edit' && request.toolId && detail) {
            try {
              await catalog.update(request.toolId, input as UpdateLocalAvatarToolInput);
            } catch (cause) {
              if (cause instanceof LocalAvatarToolRevisionConflictError) {
                dirtyRef.current = false;
                setDetail(cause.currentDetail);
                setSpecialEnabled(!!cause.currentDetail.special);
                setNotice(i18n(
                  'chat.avatarToolRevisionConflict',
                  'This tool changed in another window. The latest version has been loaded.',
                ));
                return;
              }
              throw cause;
            }
            notifyOpener('updated', request.toolId);
          } else {
            const createInput = input as CreateLocalAvatarToolInput;
            await catalog.create(createInput);
            notifyOpener('created', createInput.toolId);
          }
          dirtyRef.current = false;
          closeEditorWindow();
        }}
        onDelete={request.mode === 'edit' && request.toolId ? async () => {
          await catalog.remove(request.toolId!);
          notifyOpener('deleted', request.toolId!);
          dirtyRef.current = false;
          closeEditorWindow();
        } : undefined}
      />
    );
  }

  return (
    <AvatarToolEditorWorkspace
      title={title}
      limits={catalog.limits}
      dialogRef={workspaceRef}
      onInteractionEdit={markEdited}
      showHeader={false}
    >
      <div data-avatar-tool-editor-special={specialEnabled ? 'true' : 'false'}>
        {content}
      </div>
    </AvatarToolEditorWorkspace>
  );
}
