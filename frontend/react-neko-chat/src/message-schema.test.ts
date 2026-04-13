import { ZodError } from 'zod';
import { parseChatMessage, parseChatWindowProps } from './message-schema';

describe('message-schema', () => {
  it('parses a valid chat message', () => {
    const message = parseChatMessage({
      id: 'msg-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'text', text: 'hello' }],
    });

    expect(message.role).toBe('assistant');
    expect(message.blocks[0]?.type).toBe('text');
  });

  it('rejects invalid message payloads', () => {
    expect(() => parseChatMessage({
      id: 'msg-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:00',
      blocks: [{ type: 'unknown', text: 'bad block' }],
    })).toThrow(ZodError);
  });

  it('normalizes empty props through the window props schema', () => {
    const props = parseChatWindowProps(undefined);

    expect(props).toEqual({});
  });
});
