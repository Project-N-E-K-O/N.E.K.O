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
  streamingStatusLabel = '生成中',
  failedStatusLabel = '发送失败',
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
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
        </header>

        <section className="chat-body">
          <MessageList
            messages={messages}
            emptyText={emptyText}
            ariaLabel={messageListAriaLabel}
            streamingStatusLabel={streamingStatusLabel}
            failedStatusLabel={failedStatusLabel}
            onAction={onMessageAction}
          />
        </section>

        <footer className="composer-panel">
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
                src="/static/icons/upload_icon.png"
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
