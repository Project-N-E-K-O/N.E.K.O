import { useEffect, useMemo, useRef, useState } from 'react';
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

type ToolIconItem = {
  id: string;
  icon: string;
  iconAlt?: string;
  label: string;
  iconImagePath?: string;
  iconImagePathAlt?: string;
  menuIconScale?: number;
  menuIconOffsetX?: number;
  menuIconOffsetY?: number;
  menuIconOffsetXAlt?: number;
  menuIconOffsetYAlt?: number;
  cursorImagePath?: string;
  cursorImagePathAlt?: string;
  cursorHotspotX?: number;
  cursorHotspotY?: number;
};

const toolIconItems: ToolIconItem[] = [
  {
    id: 'lollipop',
    icon: '🍭',
    iconAlt: '🍬',
    label: '棒棒糖',
    iconImagePath: '/static/icons/chat_sugar1.png',
    iconImagePathAlt: '/static/icons/chat_sugar2.png',
    cursorImagePath: '/static/icons/chat_sugar1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_sugar2_cursor.png',
    menuIconScale: 1.18,
    cursorHotspotX: 27,
    cursorHotspotY: 40,
  },
  {
    id: 'fist',
    icon: '👊',
    iconAlt: '✊',
    label: '拳头',
    iconImagePath: '/static/icons/cat_claw1.png',
    iconImagePathAlt: '/static/icons/cat_claw2.png',
    cursorImagePath: '/static/icons/cat_claw1_cursor.png',
    cursorImagePathAlt: '/static/icons/cat_claw2_cursor.png',
    cursorHotspotX: 39,
    cursorHotspotY: 40,
  },
  {
    id: 'hammer',
    icon: '🔨',
    iconAlt: '⚒️',
    label: '锤子',
    iconImagePath: '/static/icons/chat_hammer1.png',
    iconImagePathAlt: '/static/icons/chat_hammer2.png',
    cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_hammer2_cursor.png',
    menuIconScale: 1.42,
    menuIconOffsetX: -6,
    menuIconOffsetY: 1,
    menuIconOffsetXAlt: 1,
    menuIconOffsetYAlt: -1,
    cursorHotspotX: 50,
    cursorHotspotY: 48,
  },
];

function buildEmojiCursor(emoji: string): string {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='36' height='36' viewBox='0 0 36 36'><text x='18' y='24' text-anchor='middle' font-size='24'>${emoji}</text></svg>`;
  return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}") 18 18, auto`;
}

type CursorVariant = 'primary' | 'secondary';

function resolveCursorValue(item: ToolIconItem, variant: CursorVariant): string {
  const hotspotX = typeof item.cursorHotspotX === 'number' ? item.cursorHotspotX : 18;
  const hotspotY = typeof item.cursorHotspotY === 'number' ? item.cursorHotspotY : 18;

  if (variant === 'secondary') {
    if (item.cursorImagePathAlt) {
      return `url("${item.cursorImagePathAlt}") ${hotspotX} ${hotspotY}, auto`;
    }
    return buildEmojiCursor(item.iconAlt || item.icon);
  }

  if (item.cursorImagePath) {
    return `url("${item.cursorImagePath}") ${hotspotX} ${hotspotY}, auto`;
  }
  return buildEmojiCursor(item.icon);
}

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
  const [pendingDrafts, setPendingDrafts] = useState<Array<{ id: string; text: string; time: string; lastMsgId: string | null }>>([]);
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [activeCursorToolId, setActiveCursorToolId] = useState<string | null>(null);
  const [cursorVariant, setCursorVariant] = useState<CursorVariant>('primary');
  const toolMenuRef = useRef<HTMLDivElement | null>(null);

  const canSubmit = draft.trim().length > 0 || composerAttachments.length > 0;
  const resolvedImportImageAriaLabel = importImageButtonAriaLabel || importImageButtonLabel;
  const resolvedScreenshotAriaLabel = screenshotButtonAriaLabel || screenshotButtonLabel;
  const resolvedTranslateAriaLabel = translateButtonAriaLabel || translateButtonLabel;
  const moreToolsButtonLabel = i18n('chat.moreTools', 'More tools');
  const toolIconsAriaLabel = i18n('chat.toolIconsAriaLabel', 'Tool icons');

  // Clear pending drafts once the host confirms them (appears in messages)
  useEffect(() => {
    if (pendingDrafts.length === 0) return;
    const remaining = pendingDrafts.filter((draftItem) => {
      const anchor = draftItem.lastMsgId ? messages.findIndex((m) => m.id === draftItem.lastMsgId) : -1;
      const newMsgs = messages.slice(anchor + 1);
      const newUserTexts = new Set(
        newMsgs
          .filter((m) => m.role === 'user')
          .flatMap((m) => m.blocks.flatMap((b) => (b.type === 'text' ? [b.text] : []))),
      );
      return !newUserTexts.has(draftItem.text);
    });
    if (remaining.length < pendingDrafts.length) {
      setPendingDrafts(remaining);
    }
  }, [messages, pendingDrafts]);

  // Merge host messages + optimistic pending drafts
  const lastUserAuthor = [...messages].reverse().find((m) => m.role === 'user')?.author;
  const allMessages = useMemo(() => {
    if (pendingDrafts.length === 0) return messages;
    const optimistic: ChatMessage[] = pendingDrafts.map((draftItem) => ({
      id: draftItem.id,
      role: 'user' as const,
      author: lastUserAuthor || 'You',
      time: draftItem.time,
      blocks: [{ type: 'text' as const, text: draftItem.text }],
      status: 'sending' as const,
    }));
    return [...messages, ...optimistic];
  }, [messages, pendingDrafts, lastUserAuthor]);

  useEffect(() => {
    if (!toolMenuOpen) return;

    const closeMenuOnOutsideClick = (event: MouseEvent) => {
      const menuNode = toolMenuRef.current;
      if (!menuNode) return;
      if (menuNode.contains(event.target as Node)) return;
      setToolMenuOpen(false);
    };

    const closeMenuOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setToolMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
    };
  }, [toolMenuOpen]);

  useEffect(() => {
    if (!activeCursorToolId) return;

    const toggleCursorVariantOnPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      setCursorVariant((prev) => (prev === 'primary' ? 'secondary' : 'primary'));
    };

    window.addEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
    return () => {
      window.removeEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
    };
  }, [activeCursorToolId]);

  useEffect(() => {
    const root = document.documentElement;
    if (!activeCursorToolId) {
      root.classList.remove('neko-tool-cursor-active');
      root.style.removeProperty('--neko-chat-tool-cursor');
      return;
    }

    const selected = toolIconItems.find((item) => item.id === activeCursorToolId);
    if (!selected) {
      root.classList.remove('neko-tool-cursor-active');
      root.style.removeProperty('--neko-chat-tool-cursor');
      return;
    }

    const cursorValue = resolveCursorValue(selected, cursorVariant);
    root.style.setProperty('--neko-chat-tool-cursor', cursorValue);
    root.classList.add('neko-tool-cursor-active');
  }, [activeCursorToolId, cursorVariant]);

  useEffect(() => () => {
    const root = document.documentElement;
    root.classList.remove('neko-tool-cursor-active');
    root.style.removeProperty('--neko-chat-tool-cursor');
  }, []);

  function submitDraft() {
    const text = draft.trim();
    if (!text && composerAttachments.length === 0) return;
    const now = new Date();
    const time = [now.getHours(), now.getMinutes(), now.getSeconds()]
      .map((n) => String(n).padStart(2, '0')).join(':');
    if (text) {
      setPendingDrafts((prev) => [...prev, {
        id: `pending-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        text,
        time,
        lastMsgId: messages.length > 0 ? messages[messages.length - 1].id : null,
      }]);
    }
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
            messages={allMessages}
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
                  <div className="composer-tool-menu" ref={toolMenuRef}>
                    <button
                      className="composer-tool-btn composer-tool-btn-toggle"
                      type="button"
                      aria-label={moreToolsButtonLabel}
                      title={moreToolsButtonLabel}
                      aria-expanded={toolMenuOpen}
                      aria-haspopup="menu"
                      onClick={() => setToolMenuOpen((open) => !open)}
                    >
                      <img src="/static/icons/chat_tools.png" alt="" aria-hidden="true" />
                    </button>
                    {toolMenuOpen ? (
                      <div className="composer-icon-popover" role="menu" aria-label={toolIconsAriaLabel}>
                        {toolIconItems.map((item) => (
                          <button
                            key={item.id}
                            className={`composer-icon-button${activeCursorToolId === item.id ? ' is-active' : ''}`}
                            type="button"
                            role="menuitem"
                            aria-label={item.label}
                            title={item.label}
                            onClick={() => {
                              setCursorVariant('primary');
                              setActiveCursorToolId(item.id);
                              setToolMenuOpen(false);
                            }}
                          >
                            {item.iconImagePath ? (
                              <img
                                className="composer-icon-button-image"
                                src={activeCursorToolId === item.id
                                  && cursorVariant === 'secondary'
                                  && item.iconImagePathAlt
                                  ? item.iconImagePathAlt
                                  : item.iconImagePath}
                                style={{
                                  transform: `translate(${
                                    activeCursorToolId === item.id
                                    && cursorVariant === 'secondary'
                                    && item.iconImagePathAlt
                                      ? (item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
                                      : (item.menuIconOffsetX ?? 0)
                                  }px, ${
                                    activeCursorToolId === item.id
                                    && cursorVariant === 'secondary'
                                    && item.iconImagePathAlt
                                      ? (item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
                                      : (item.menuIconOffsetY ?? 0)
                                  }px) scale(${item.menuIconScale ?? 1})`,
                                }}
                                alt=""
                                aria-hidden="true"
                              />
                            ) : item.icon}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
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
