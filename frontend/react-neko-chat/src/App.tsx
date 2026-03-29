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
  onMessageAction?: (message: ChatMessage, action: MessageAction) => void;
};

const defaultMessages: ChatMessage[] = [];

export default function App({
  title = 'N.E.K.O Chat',
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = '输入消息...',
  sendButtonLabel = '发送',
  onMessageAction,
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
          <MessageList messages={messages} onAction={onMessageAction} />
        </section>

        <footer className="composer-panel">
          <div className="composer-toolbar" aria-label="Composer tools">
            <button className="tool-button" type="button" aria-label="表情">☺</button>
            <button className="tool-button" type="button" aria-label="附件">＋</button>
          </div>
          <form className="composer" onSubmit={(event) => event.preventDefault()}>
            <div className="composer-row">
              <label className="composer-input-shell">
                <textarea
                  className="composer-input"
                  placeholder={inputPlaceholder}
                  rows={1}
                />
              </label>
              <button className="send-button" type="submit">{sendButtonLabel}</button>
            </div>
          </form>
        </footer>
      </section>
    </main>
  );
}
