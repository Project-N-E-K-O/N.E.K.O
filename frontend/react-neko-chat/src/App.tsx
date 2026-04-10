import { useState } from 'react';
import MessageList from './MessageList';
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
  onAvatarGeneratorClick?: () => void;
};

const defaultMessages: ChatMessage[] = [];

export default function App({
  title = 'N.E.K.O Chat',
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = '输入消息...',
  sendButtonLabel = '发送',
  emptyText = '聊天内容接入后会显示在这里。',
  chatWindowAriaLabel = 'Neko chat window',
  messageListAriaLabel = 'Chat messages',
  composerToolsAriaLabel = 'Composer tools',
  composerAttachments = [],
  composerAttachmentsAriaLabel = 'Pending attachments',
  importImageButtonLabel = '导入图片',
  screenshotButtonLabel = '截图',
  importImageButtonAriaLabel = '导入图片',
  screenshotButtonAriaLabel = '截图',
  removeAttachmentButtonAriaLabel = '移除图片',
  failedStatusLabel = '发送失败',
  jukeboxButtonLabel = '点歌台',
  jukeboxButtonAriaLabel = '点歌台',
  avatarGeneratorButtonLabel = '头像',
  avatarGeneratorButtonAriaLabel = '生成头像',
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
  onJukeboxClick,
  onAvatarGeneratorClick,
}: ChatWindowProps) {
  const [draft, setDraft] = useState('');

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
          <div className="window-topbar-actions">
            <button
              id="reactAvatarPreviewButton"
              className="topbar-action-btn"
              type="button"
              aria-label={avatarGeneratorButtonAriaLabel}
              title={avatarGeneratorButtonAriaLabel}
              onClick={() => onAvatarGeneratorClick?.()}
            >
              <svg className="topbar-action-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 12a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5Z" />
                <path d="M5.5 19.25a6.5 6.5 0 0 1 13 0" />
              </svg>
              <span className="topbar-action-label">{avatarGeneratorButtonLabel}</span>
            </button>
            <button
              id="reactJukeboxButton"
              className="topbar-action-btn"
              type="button"
              aria-label={jukeboxButtonAriaLabel}
              title={jukeboxButtonAriaLabel}
              onClick={() => onJukeboxClick?.()}
            >
              <svg className="topbar-action-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
              </svg>
              <span className="topbar-action-label">{jukeboxButtonLabel}</span>
            </button>
          </div>
        </header>

        <section className="chat-body">
          <MessageList
            messages={messages}
            emptyText={emptyText}
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
          <div className="composer-toolbar" aria-label={composerToolsAriaLabel}>
            <button
              className="composer-tool-chip"
              type="button"
              aria-label={importImageButtonAriaLabel}
              onClick={() => onComposerImportImage?.()}
            >
              <img
                className="composer-tool-chip-icon"
                src="/static/icons/image_icon.svg"
                alt=""
                aria-hidden="true"
              />
              <span>{importImageButtonLabel}</span>
            </button>
            <button
              className="composer-tool-chip"
              type="button"
              aria-label={screenshotButtonAriaLabel}
              onClick={() => onComposerScreenshot?.()}
            >
              <img
                className="composer-tool-chip-icon"
                src="/static/icons/screenshot_icon.png"
                alt=""
                aria-hidden="true"
              />
              <span>{screenshotButtonLabel}</span>
            </button>
          </div>
          <form className="composer" onSubmit={(event) => {
            event.preventDefault();
            submitDraft();
          }}>
            <div className="composer-row">
              <label className="composer-input-shell">
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
              </label>
              <button className="send-button" type="submit">
                <img
                  className="send-button-paw"
                  src="/static/icons/paw_ui.png"
                  alt=""
                  aria-hidden="true"
                />
                <img
                  className="send-button-icon"
                  src="/static/icons/send_icon.png"
                  alt=""
                  aria-hidden="true"
                />
                <span>{sendButtonLabel}</span>
              </button>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
