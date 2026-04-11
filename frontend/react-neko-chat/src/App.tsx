import { useState } from 'react';
import MessageList from './MessageList';
import { i18n } from './i18n';
import {
  type ChatMessage,
  type MessageAction,
  type ChatWindowSchemaProps,
  type ComposerSubmitPayload,
  type ComposerAttachment,
} from './message-schema';

export type ChatWindowProps = ChatWindowSchemaProps & {
  onMessageAction?: (message: ChatMessage, action: MessageAction) => void;
  onComposerImportImage?: () => void;
  onComposerScreenshot?: () => void;
  onComposerRemoveAttachment?: (attachmentId: ComposerAttachment['id']) => void;
  onComposerSubmit?: (payload: ComposerSubmitPayload) => void;
  onJukeboxClick?: () => void;
  onTranslateToggle?: () => void;
};

const defaultMessages: ChatMessage[] = [];

export default function App({
  title = i18n('chat.title', 'N.E.K.O Chat'),
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = i18n('chat.textInputPlaceholder', 'Type a message...'),
  sendButtonLabel = i18n('chat.send', 'Send'),
  chatWindowAriaLabel = i18n('chat.reactWindowAriaLabel', 'Neko chat window'),
  messageListAriaLabel = i18n('chat.messageListAriaLabel', 'Chat messages'),
  composerToolsAriaLabel = i18n('chat.composerToolsAriaLabel', 'Composer tools'),
  composerAttachments = [],
  composerAttachmentsAriaLabel = i18n('chat.pendingImagesAriaLabel', 'Pending attachments'),
  importImageButtonLabel = i18n('chat.importImage', 'Import Image'),
  screenshotButtonLabel = i18n('chat.screenshot', 'Screenshot'),
  importImageButtonAriaLabel,
  screenshotButtonAriaLabel,
  removeAttachmentButtonAriaLabel = i18n('chat.removePendingImage', 'Remove image'),
  failedStatusLabel = i18n('chat.messageFailed', 'Failed'),
  jukeboxButtonLabel = i18n('chat.jukeboxLabel', 'Jukebox'),
  jukeboxButtonAriaLabel = i18n('chat.jukebox', 'Jukebox'),
  translateEnabled = false,
  translateButtonLabel = i18n('subtitle.enable', 'Subtitle Translation'),
  translateButtonAriaLabel,
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
  onJukeboxClick,
  onTranslateToggle,
}: ChatWindowProps) {
  const [draft, setDraft] = useState('');
  const canSubmit = draft.trim().length > 0 || composerAttachments.length > 0;
  const resolvedImportImageAriaLabel = importImageButtonAriaLabel || importImageButtonLabel;
  const resolvedScreenshotAriaLabel = screenshotButtonAriaLabel || screenshotButtonLabel;
  const resolvedTranslateAriaLabel = translateButtonAriaLabel || translateButtonLabel;

  function submitDraft() {
    const text = draft.trim();
    if (!text && composerAttachments.length === 0) return;
    onComposerSubmit?.({ text });
    setDraft('');
  }

  return (
    <main className="app-shell">
      <section className="chat-window" aria-label={chatWindowAriaLabel}>
        <header className="window-topbar">
          <div className="window-title-group">
            <div className="window-avatar window-avatar-image-shell">
              <img className="window-avatar-image" src={iconSrc} alt={title} />
            </div>
            <h1 className="window-title" id="react-chat-window-title">{title}</h1>
          </div>
          {/* Avatar button moved to #react-chat-window-header-actions in host template */}
        </header>

        <section className="chat-body">
          <MessageList
            messages={messages}
            ariaLabel={messageListAriaLabel}
            failedStatusLabel={failedStatusLabel}
            onAction={onMessageAction}
          />
        </section>

        <footer className="composer-panel">
          <div id="music-player-mount" />
          {composerAttachments.length > 0 ? (
            <div className="composer-attachments" aria-label={composerAttachmentsAriaLabel}>
              {composerAttachments.map((attachment) => (
                <figure key={attachment.id} className="composer-attachment-card">
                  <img
                    className="composer-attachment-image"
                    src={attachment.url}
                    alt={attachment.alt || ''}
                    loading="lazy"
                  />
                  <button
                    className="composer-attachment-remove"
                    type="button"
                    aria-label={`${removeAttachmentButtonAriaLabel}: ${attachment.alt || attachment.id}`}
                    onClick={() => onComposerRemoveAttachment?.(attachment.id)}
                  >
                    ×
                  </button>
                </figure>
              ))}
            </div>
          ) : null}
          <form className="composer" onSubmit={(event) => {
            event.preventDefault();
            submitDraft();
          }}>
            <div className="composer-input-shell">
              <textarea
                className="composer-input"
                placeholder={inputPlaceholder}
                aria-label={inputPlaceholder}
                rows={1}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submitDraft();
                  }
                }}
              />
              <div className="composer-bottom-bar">
                <div className="composer-bottom-tools" aria-label={composerToolsAriaLabel}>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedImportImageAriaLabel}
                    title={importImageButtonLabel}
                    onClick={() => onComposerImportImage?.()}
                  >
                    <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
                  </button>
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedScreenshotAriaLabel}
                    title={screenshotButtonLabel}
                    onClick={() => onComposerScreenshot?.()}
                  >
                    <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
                  </button>
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  <button
                    className={`composer-tool-btn composer-translate-btn${translateEnabled ? ' is-active' : ''}`}
                    type="button"
                    aria-label={resolvedTranslateAriaLabel}
                    aria-pressed={translateEnabled}
                    title={translateButtonLabel}
                    onClick={() => onTranslateToggle?.()}
                  >
                    <img src="/static/icons/translate_icon.png" alt="" aria-hidden="true" />
                  </button>
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={jukeboxButtonAriaLabel}
                    title={jukeboxButtonLabel}
                    onClick={() => onJukeboxClick?.()}
                  >
                    <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
                  </button>
                </div>
                <button className="send-button-circle" type="submit" aria-label={sendButtonLabel} disabled={!canSubmit}>
                  <img src="/static/icons/send_new_icon.png" alt="" aria-hidden="true" />
                </button>
              </div>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
