import { useEffect, useRef, useState } from 'react';
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
  // Later you can swap to designed assets by filling these paths.
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
    // 视觉补偿：锤子源图留白较多，菜单里放大并微调居中
    menuIconScale: 1.42,
    // 主图向左下再移动一点（仅主图）
    menuIconOffsetX: -6,
    menuIconOffsetY: 1,
    // 副图保持原先观感
    menuIconOffsetXAlt: 1,
    menuIconOffsetYAlt: -1,
    // 光标热点放到图标视觉中心，避免看起来偏移
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
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
}: ChatWindowProps) {
  const [draft, setDraft] = useState('');
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [activeCursorToolId, setActiveCursorToolId] = useState<string | null>(null);
  const [cursorVariant, setCursorVariant] = useState<CursorVariant>('primary');
  const toolMenuRef = useRef<HTMLDivElement | null>(null);

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

  useEffect(() => {
    return () => {
      const root = document.documentElement;
      root.classList.remove('neko-tool-cursor-active');
      root.style.removeProperty('--neko-chat-tool-cursor');
    };
  }, []);

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
                src="/static/icons/chat_picture.png"
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
                src="/static/icons/chat_shot.png"
                alt=""
                aria-hidden="true"
              />
              <span>{screenshotButtonLabel}</span>
            </button>
            <div className="composer-tool-menu" ref={toolMenuRef}>
              <button
                className="composer-tool-chip composer-tool-chip-toggle"
                type="button"
                aria-label="更多工具"
                aria-expanded={toolMenuOpen}
                aria-haspopup="menu"
                onClick={() => setToolMenuOpen((open) => !open)}
              >
                <img
                  className="composer-tool-chip-icon"
                  src="/static/icons/chat_tools.png"
                  alt=""
                  aria-hidden="true"
                />
                <span>工具</span>
              </button>
              {toolMenuOpen ? (
                <div className="composer-icon-popover" role="menu" aria-label="工具图标">
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
