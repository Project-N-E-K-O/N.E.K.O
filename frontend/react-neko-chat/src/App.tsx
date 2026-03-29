export type ChatWindowMessage = {
  id: string;
  role: 'assistant' | 'user' | 'system';
  author: string;
  time: string;
  text: string;
};

export type ChatWindowProps = {
  title?: string;
  iconSrc?: string;
  messages?: ChatWindowMessage[];
  inputPlaceholder?: string;
  sendButtonLabel?: string;
  inputHint?: string;
};

const defaultMessages: ChatWindowMessage[] = [];

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
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
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
          <div className="message-list" aria-label="Chat messages">
            {messages.length > 0 ? (
              messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))
            ) : (
              <div className="message-empty-state">聊天内容接入后会显示在这里。</div>
            )}
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
