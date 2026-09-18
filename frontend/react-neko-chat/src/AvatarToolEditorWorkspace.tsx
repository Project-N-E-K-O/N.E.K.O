import type {
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  RefObject,
} from 'react';
import { i18n } from './i18n';
import type { LocalAvatarToolLimits } from './avatar-tools/localTools';
import { AvatarToolInteractionEditorProvider } from './avatar-tools/AvatarToolInteractionEditorContext';
import { AvatarToolInteractionCanvas } from './AvatarToolInteractionCanvas';

export {
  AvatarToolInteractionCanvas,
  avatarToolConnectionPreviewPath,
  snapAvatarToolNodePosition,
} from './AvatarToolInteractionCanvas';

type AvatarToolEditorWorkspaceProps = {
  title: string;
  limits?: LocalAvatarToolLimits | null;
  dialogRef: RefObject<HTMLElement>;
  backButtonRef?: RefObject<HTMLButtonElement>;
  onBack?(): void;
  showHeader?: boolean;
  onPointerDown?(event: ReactPointerEvent<HTMLElement>): void;
  onMouseDown?(event: ReactMouseEvent<HTMLElement>): void;
  onInteractionEdit?(): void;
  children: ReactNode;
};

export default function AvatarToolEditorWorkspace({
  title,
  limits,
  dialogRef,
  backButtonRef,
  onBack,
  showHeader = true,
  onPointerDown,
  onMouseDown,
  onInteractionEdit,
  children,
}: AvatarToolEditorWorkspaceProps) {
  return (
    <AvatarToolInteractionEditorProvider onMutation={onInteractionEdit}>
      <section
        className="avatar-tool-editor-workspace"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={showHeader ? undefined : title}
        aria-labelledby={showHeader ? 'avatar-tool-editor-workspace-title' : undefined}
        tabIndex={-1}
        onPointerDown={onPointerDown}
        onMouseDown={onMouseDown}
        onClick={(event) => event.stopPropagation()}
      >
        {showHeader ? (
          <header className="avatar-tool-workspace-header">
            <button
              className="avatar-tool-workspace-back"
              type="button"
              ref={backButtonRef}
              onClick={onBack}
            >
              <span aria-hidden="true">←</span>
              {i18n('chat.avatarToolCreateBack', 'Back')}
            </button>
            <div className="avatar-tool-workspace-heading">
              <span>{i18n('chat.avatarToolManagerTitle', 'Manage tools')}</span>
              <h2 id="avatar-tool-editor-workspace-title">{title}</h2>
            </div>
            <span className="avatar-tool-workspace-local-badge">
              <span aria-hidden="true">●</span>
              {i18n('chat.avatarToolWorkspaceLocalOnly', 'Saved on this device')}
            </span>
          </header>
        ) : null}

        <div className="avatar-tool-workspace-main">
          <section
            className="avatar-tool-workspace-stage"
            aria-label={i18n('chat.avatarToolWorkspaceCanvasTitle', 'Interaction flow')}
          >
            <div className="avatar-tool-workspace-stage-heading">
              <h3>{i18n('chat.avatarToolWorkspaceCanvasTitle', 'Interaction flow')}</h3>
              <p>{i18n(
                'chat.avatarToolWorkspaceCanvasHint',
                'Drag nodes freely; they align on release · Connect from any edge point · Scroll to pan',
              )}</p>
            </div>
            <AvatarToolInteractionCanvas limits={limits} onApplyPreset={onInteractionEdit} />
          </section>

          <aside
            className="avatar-tool-workspace-settings"
            aria-label={i18n('chat.avatarToolWorkspaceEditorTitle', 'Tool editor')}
          >
            <div className="avatar-tool-workspace-settings-heading">
              <h3>{i18n('chat.avatarToolWorkspaceEditorTitle', 'Tool editor')}</h3>
              <p className="avatar-tool-workspace-content-note">{i18n(
                'chat.avatarToolCreatePrivacy',
                'Images and sounds stay on this device; during interactions, the current image or surprise prompt is sent to the model.',
              )}</p>
            </div>
            <div className="avatar-tool-workspace-settings-body">
              {children}
            </div>
          </aside>
        </div>
      </section>
    </AvatarToolInteractionEditorProvider>
  );
}
