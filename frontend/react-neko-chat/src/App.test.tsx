import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import { parseChatMessage } from './message-schema';

describe('App', () => {
  it('renders the empty state when there are no messages', () => {
    render(<App />);

    expect(screen.getByText('聊天内容接入后会显示在这里。')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('输入消息...')).toBeInTheDocument();
  });

  it('renders grouped assistant messages with a single visible avatar', () => {
    const firstMessage = parseChatMessage({
      id: 'assistant-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      createdAt: 1,
      blocks: [{ type: 'text', text: '第一条消息' }],
    });
    const secondMessage = parseChatMessage({
      id: 'assistant-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'text', text: '第二条消息' }],
    });

    const { container } = render(<App messages={[firstMessage, secondMessage]} />);

    expect(screen.getByText('第一条消息')).toBeInTheDocument();
    expect(screen.getByText('第二条消息')).toBeInTheDocument();
    expect(container.querySelectorAll('.avatar-assistant').length).toBe(1);
    expect(container.querySelectorAll('.avatar-placeholder').length).toBe(1);
  });

  it('renders message status chips for streaming and failed messages', () => {
    const streamingMessage = parseChatMessage({
      id: 'streaming-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: '生成中消息' }],
      status: 'streaming',
    });
    const failedMessage = parseChatMessage({
      id: 'failed-1',
      role: 'user',
      author: 'You',
      time: '10:01',
      blocks: [{ type: 'text', text: '发送失败消息' }],
      status: 'failed',
    });

    render(<App messages={[streamingMessage, failedMessage]} />);

    expect(screen.getByText('生成中')).toBeInTheDocument();
    expect(screen.getByText('发送失败')).toBeInTheDocument();
  });

  it('submits composer text through the new submit callback', () => {
    const onComposerSubmit = vi.fn();
    render(<App onComposerSubmit={onComposerSubmit} />);

    const input = screen.getByPlaceholderText('输入消息...');
    fireEvent.change(input, { target: { value: '测试发送' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(onComposerSubmit).toHaveBeenCalledWith({ text: '测试发送' });
  });

  it('renders composer tool buttons and calls the React callbacks', () => {
    const onComposerImportImage = vi.fn();
    const onComposerScreenshot = vi.fn();

    render(
      <App
        onComposerImportImage={onComposerImportImage}
        onComposerScreenshot={onComposerScreenshot}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导入图片' }));
    fireEvent.click(screen.getByRole('button', { name: '截图' }));

    expect(onComposerImportImage).toHaveBeenCalledTimes(1);
    expect(onComposerScreenshot).toHaveBeenCalledTimes(1);
  });

  it('renders pending composer attachments and removes them through callback', () => {
    const onComposerRemoveAttachment = vi.fn();

    render(
      <App
        composerAttachments={[
          { id: 'img-1', url: 'data:image/png;base64,aaa', alt: '截图 1' },
        ]}
        onComposerRemoveAttachment={onComposerRemoveAttachment}
      />,
    );

    expect(screen.getByAltText('截图 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除图片: 截图 1' }));

    expect(onComposerRemoveAttachment).toHaveBeenCalledWith('img-1');
  });
});
