import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { chatWindowPropsSchema } from './message-schema';

// 串门工具条（docs/design/visit-infrastructure.md §5 PR-12）：宿主给状态与按钮，点按钮回传 id；
// 与 compact 选项共用同一层（定位与 Electron 命中区），但不把聊天面切到 options 态。
describe('compact visit bar', () => {
  it('renders the badge, detail and actions in the choice layer and reports clicks', () => {
    const onVisitBarAction = vi.fn();
    render(
      <App
        chatSurfaceMode="compact"
        visitBar={{
          badge: '小白 在做客',
          tone: 'active',
          detail: '邀请码 ABCDEFGH23',
          actions: [
            { id: 'to_guest', label: '发给 小白', pressed: true },
            { id: 'to_own', label: '发给 咪咪', pressed: false },
            { id: 'recall', label: '送客', variant: 'danger' },
          ],
        }}
        onVisitBarAction={onVisitBarAction}
      />,
    );

    const layer = document.body.querySelector('body > .compact-chat-choice-anchor');
    expect(layer).not.toBeNull();
    expect(layer).toHaveAttribute('data-choice-layer-open', 'true');
    const bar = layer!.querySelector('.composer-visit-bar');
    expect(bar).not.toBeNull();
    expect(bar).toHaveClass('is-active');
    expect(bar!.querySelector('.composer-visit-bar-badge')?.textContent).toBe('小白 在做客');
    expect(bar!.querySelector('.composer-visit-bar-detail')?.textContent).toBe('邀请码 ABCDEFGH23');
    const toGuest = bar!.querySelector('[data-visit-bar-action="to_guest"]');
    expect(toGuest).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(bar!.querySelector('[data-visit-bar-action="recall"]')!);
    expect(onVisitBarAction).toHaveBeenCalledWith('recall');
    // 不切到 options 态：输入框与胶囊照常
    expect(document.body.querySelector('.app-shell')).toHaveAttribute('data-compact-chat-state', 'default');
  });

  it('renders nothing and keeps the layer closed without a bar', () => {
    render(<App chatSurfaceMode="compact" visitBar={null} />);
    const layer = document.body.querySelector('body > .compact-chat-choice-anchor');
    expect(layer).toHaveAttribute('data-choice-layer-open', 'false');
    expect(document.body.querySelector('.composer-visit-bar')).toBeNull();
  });

  it('keeps visitBar and onVisitBarAction through the props schema', () => {
    const handler = () => undefined;
    const parsed = chatWindowPropsSchema.parse({
      visitBar: { actions: [{ id: 'entry', label: '串门' }] },
      onVisitBarAction: handler,
    });
    expect(parsed.visitBar).toEqual({ actions: [{ id: 'entry', label: '串门' }] });
    expect(typeof parsed.onVisitBarAction).toBe('function');
  });
});

// full（冻结的 legacy 界面）有自己的一份工具条，代码与 compact 不共用
describe('full visit bar', () => {
  it('renders above the composer and reports clicks', () => {
    const onVisitBarAction = vi.fn();
    const { container } = render(
      <App
        chatSurfaceMode="full"
        visitBar={{
          badge: '她在 邻居 家',
          tone: 'active',
          actions: [{ id: 'recall', label: '叫她回来', variant: 'primary' }],
        }}
        onVisitBarAction={onVisitBarAction}
      />,
    );
    const bar = container.querySelector('.composer-panel .full-visit-bar');
    expect(bar).not.toBeNull();
    expect(bar!.querySelector('.full-visit-bar-badge')?.textContent).toBe('她在 邻居 家');
    // 工具条在 composer 表单之前
    const form = container.querySelector('.composer-panel form.composer');
    expect(bar!.compareDocumentPosition(form!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(bar!.querySelector('[data-visit-bar-action="recall"]')!);
    expect(onVisitBarAction).toHaveBeenCalledWith('recall');
    expect(document.body.querySelector('.composer-visit-bar')).toBeNull();
  });

  it('hides the bar while the theater presentation is active', () => {
    const { container } = render(
      <App
        chatSurfaceMode="full"
        theaterPresentation={{ active: true, phase: 'performing' }}
        visitBar={{ tone: 'idle', actions: [{ id: 'entry', label: '串门' }] }}
      />,
    );
    expect(container.querySelector('.full-visit-bar')).toBeNull();
  });

  it('renders nothing without a bar', () => {
    const { container } = render(<App chatSurfaceMode="full" visitBar={null} />);
    expect(container.querySelector('.full-visit-bar')).toBeNull();
  });
});
