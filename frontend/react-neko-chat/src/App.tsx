export type ChatWindowMessage = {
  id: string;
  role: 'assistant' | 'user' | 'system';
  author: string;
  time: string;
  text: string;
};

export type ChatWindowProps = {
  title?: string;
  subtitle?: string;
  status?: string;
  iconSrc?: string;
  messages?: ChatWindowMessage[];
  draftPlaceholder?: string;
  sendLabel?: string;
  composerHint?: string;
};

const defaultMessages: ChatWindowMessage[] = [
  {
    id: 'assistant-1',
    role: 'assistant',
    author: 'Neko',
    time: '22:14',
    text: '新版聊天框骨架已经准备好了，接下来可以逐步迁移旧版消息和输入逻辑。',
  },
  {
    id: 'user-1',
    role: 'user',
    author: 'You',
    time: '22:15',
    text: '先保留旧版实现，新版先把窗口结构、消息样式和挂载导出能力建起来。',
  },
  {
    id: 'system-1',
    role: 'system',
    author: 'System',
    time: '22:16',
    text: '当前为静态演示窗口。后续将通过 host bridge 接入现有 websocket、IPC 和宿主页面能力。',
  },
];

function MessageBubble({ message }: { message: ChatWindowMessage }) {
  if (message.role === 'system') {
    return (
      <div className="message-row message-row-system">
        <div className="system-chip">
          <span className="system-chip-time">{message.time}</span>
          <span>{message.text}</span>
        </div>
      </div>
    );
  }

  const isUser = message.role === 'user';

  return (
    <div className={`message-row ${isUser ? 'message-row-user' : 'message-row-assistant'}`}>
      <div className={`avatar ${isUser ? 'avatar-user' : 'avatar-assistant'}`}>
        {isUser ? 'Y' : 'N'}
      </div>
      <div className="message-stack">
        <div className="message-meta">
          <span className="message-author">{message.author}</span>
          <span className="message-time">{message.time}</span>
        </div>
        <div className={`message-bubble ${isUser ? 'message-bubble-user' : 'message-bubble-assistant'}`}>
          {message.text}
        </div>
      </div>
    </div>
  );
}

export default function App({
  title = 'N.E.K.O Chat',
  subtitle = 'QQ-style chat window skeleton',
  status = 'Prototype Window',
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  draftPlaceholder = '输入消息，后续这里会接入真实发送逻辑...',
  sendLabel = '发送',
  composerHint = 'Enter 发送，Shift + Enter 换行',
}: ChatWindowProps) {
  return (
    <main className="app-shell">
      <section className="chat-window" aria-label="Neko chat window">
        <header className="window-topbar">
          <div className="window-title-group">
            <div className="window-avatar window-avatar-image-shell">
              <img className="window-avatar-image" src={iconSrc} alt={title} />
            </div>
            <div>
              <h1 className="window-title">{title}</h1>
              {subtitle ? <p className="window-subtitle">{subtitle}</p> : null}
            </div>
          </div>
          {status ? (
            <div className="window-actions" aria-label="Window status">
              <span className="window-status">{status}</span>
            </div>
          ) : null}
        </header>

        <section className="chat-body">
          <div className="message-list" aria-label="Chat messages">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
          </div>
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
              placeholder={draftPlaceholder}
              rows={4}
            />
            <div className="composer-footer">
              <span className="composer-hint">{composerHint}</span>
              <button className="send-button" type="submit">{sendLabel}</button>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
