import MessageList from './MessageList';
import {
  type ChatMessage,
  type MessageAction,
} from './message-schema';

export type ChatWindowProps = {
  title?: string;
  iconSrc?: string;
  messages?: ChatMessage[];
  inputPlaceholder?: string;
  sendButtonLabel?: string;
  inputHint?: string;
};

const sampleMessages: ChatMessage[] = [
  {
    id: 'assistant-intro',
    role: 'assistant',
    author: 'Neko',
    time: '22:14',
    avatarLabel: 'N',
    sortKey: 1,
    blocks: [
      {
        type: 'text',
        text: '新版消息底座已经换成 block 结构了。后面不管是文字、图片、链接、按钮，还是工具消息，都能沿着同一套协议往里接。',
      },
      {
        type: 'status',
        tone: 'info',
        text: '这条消息现在已经适合做导出、重排和后续虚拟列表接入。',
      },
    ],
  },
  {
    id: 'user-reference',
    role: 'user',
    author: 'You',
    time: '22:15',
    avatarLabel: 'Y',
    sortKey: 2,
    blocks: [
      {
        type: 'text',
        text: '先把消息能力做强，后面再接真实消息流和旧版逻辑。',
      },
    ],
  },
  {
    id: 'assistant-link-card',
    role: 'assistant',
    author: 'Neko',
    time: '22:16',
    avatarLabel: 'N',
    sortKey: 3,
    status: 'streaming',
    blocks: [
      {
        type: 'link',
        url: 'https://example.com/chat-spec',
        title: '消息协议草案',
        description: '统一消息结构、按钮事件和导出格式，方便宿主页面与 React 模块之间做桥接。',
        siteName: 'example.com',
      },
      {
        type: 'buttons',
        buttons: [
          { id: 'open-spec', label: '查看链接', action: 'open_link', variant: 'primary' },
          { id: 'copy-schema', label: '复制 schema', action: 'copy_schema' },
        ],
      },
    ],
  },
  {
    id: 'assistant-image',
    role: 'assistant',
    author: 'Neko',
    time: '22:16',
    avatarLabel: 'N',
    sortKey: 4,
    blocks: [
      {
        type: 'image',
        url: '/static/icons/chat_icon.png',
        alt: '聊天图标预览',
        width: 28,
        height: 28,
      },
      {
        type: 'text',
        text: '图片块现在也和普通文本共用同一条消息外壳，后面接截图、附件缩略图会比较自然。',
      },
    ],
    actions: [
      { id: 'refresh-preview', label: '刷新预览', action: 'refresh_preview' },
      { id: 'remove-preview', label: '移除', action: 'remove_preview', variant: 'danger' },
    ],
  },
  {
    id: 'system-divider',
    role: 'system',
    author: 'System',
    time: '22:17',
    sortKey: 5,
    blocks: [
      {
        type: 'text',
        text: '后续接入真实消息后，这里也可以承载时间分割线、系统提示和工具通知。',
      },
    ],
  },
];

function handleMessageAction(message: ChatMessage, action: MessageAction) {
  console.log('[ReactChatWindow] message action:', {
    messageId: message.id,
    actionId: action.id,
    action: action.action,
    payload: action.payload || null,
  });
}

export default function App({
  title = 'N.E.K.O Chat',
  iconSrc = '/static/icons/chat_icon.png',
  messages = sampleMessages,
  inputPlaceholder = '文字聊天模式...回车发送，Shift+回车换行',
  sendButtonLabel = '发送',
  inputHint = 'Enter 发送，Shift + Enter 换行',
}: ChatWindowProps) {
  return (
    <main className="app-shell">
      <section className="chat-window" aria-label="Neko chat window">
        <header className="window-topbar">
          <div className="window-title-group">
            <div className="window-avatar window-avatar-image-shell">
              <img className="window-avatar-image" src={iconSrc} alt={title} />
            </div>
            <h1 className="window-title">{title}</h1>
          </div>
        </header>

        <section className="chat-body">
          <MessageList messages={messages} onAction={handleMessageAction} />
        </section>

        <footer className="composer-panel">
          <div className="composer-toolbar" aria-label="Composer tools">
            <button className="tool-button" type="button">😀</button>
            <button className="tool-button" type="button">🖼</button>
            <button className="tool-button" type="button">📎</button>
            <button className="tool-button" type="button">⋯</button>
          </div>
          <form className="composer" onSubmit={(event) => event.preventDefault()}>
            <textarea
              className="composer-input"
              placeholder={inputPlaceholder}
              rows={4}
            />
            <div className="composer-footer">
              <span className="composer-hint">{inputHint}</span>
              <button className="send-button" type="submit">{sendButtonLabel}</button>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
