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
  const [loading, setLoading] = useState(request.mode === 'edit');
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
