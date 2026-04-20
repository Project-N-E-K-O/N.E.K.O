import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import QuickActionsPanel, { type ActionDescriptor } from '../QuickActionsPanel';

/* ── Fixtures ──────────────────────────────────────────────────────── */

const lifecycle: ActionDescriptor = {
  action_id: 'system:demo:toggle',
  type: 'instant',
  label: 'Demo Plugin',
  description: '',
  category: '系统',
  plugin_id: 'demo',
  control: 'plugin_lifecycle',
  current_value: true,
};

const toggle: ActionDescriptor = {
  action_id: 'demo:settings:enabled',
  type: 'instant',
  label: 'Enabled',
  description: 'Toggle feature',
  category: 'Demo',
  plugin_id: 'demo',
  control: 'toggle',
  current_value: false,
};

const slider: ActionDescriptor = {
  action_id: 'demo:settings:volume',
  type: 'instant',
  label: 'Volume',
  description: '',
  category: 'Demo',
  plugin_id: 'demo',
  control: 'slider',
  current_value: 50,
  min: 0,
  max: 100,
  step: 1,
};

const button: ActionDescriptor = {
  action_id: 'system:demo:entry:do_thing',
  type: 'instant',
  label: 'Do Thing',
  description: '',
  category: '系统',
  plugin_id: 'demo',
  control: 'button',
};

const inject: ActionDescriptor = {
  action_id: 'demo:greet',
  type: 'chat_inject',
  label: 'Greet',
  description: 'Say hello',
  category: 'Demo',
  plugin_id: 'demo',
  inject_text: '@Demo /greet',
};

const nav: ActionDescriptor = {
  action_id: 'system:demo:open_ui',
  type: 'navigation',
  label: 'Open UI',
  description: '',
  category: '系统',
  plugin_id: 'demo',
  target: 'http://127.0.0.1:9090/plugin/demo/ui/',
  open_in: 'new_tab',
};

const allActions = [lifecycle, toggle, slider, button, inject, nav];

function renderPanel(
  actions: ActionDescriptor[] = allActions,
  overrides: Partial<React.ComponentProps<typeof QuickActionsPanel>> = {},
) {
  const onExecuteAction = vi.fn().mockResolvedValue(null);
  const onInjectText = vi.fn();
  const onNavigate = vi.fn();
  const onClose = vi.fn();

  const result = render(
    <QuickActionsPanel
      actions={actions}
      onExecuteAction={onExecuteAction}
      onInjectText={onInjectText}
      onNavigate={onNavigate}
      onClose={onClose}
      {...overrides}
    />,
  );

  return { ...result, onExecuteAction, onInjectText, onNavigate, onClose };
}

/* ── Tests ─────────────────────────────────────────────────────────── */

describe('QuickActionsPanel', () => {
  it('renders the panel with title', () => {
    renderPanel();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('快捷操作')).toBeInTheDocument();
  });

  it('defaults to plugin view', () => {
    renderPanel();
    // The "按插件" button should be active
    const pluginBtn = screen.getByRole('button', { pressed: true });
    expect(pluginBtn).toHaveTextContent('按插件');
  });

  it('switches between plugin and function views', () => {
    renderPanel();
    const funcBtn = screen.getByRole('button', { name: /按功能/ });
    fireEvent.click(funcBtn);
    // After switching, sub-tabs should appear
    expect(screen.getByText('全部')).toBeInTheDocument();
    expect(screen.getByText('配置')).toBeInTheDocument();
    expect(screen.getByText('生命周期')).toBeInTheDocument();
  });

  it('closes on Escape key', () => {
    const { onClose } = renderPanel();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('filters actions by search query', () => {
    renderPanel();
    // Switch to function view to see all actions flat
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));

    const searchInput = screen.getByPlaceholderText('搜索...');
    fireEvent.change(searchInput, { target: { value: 'Volume' } });

    expect(screen.getByText('Volume')).toBeInTheDocument();
    expect(screen.queryByText('Greet')).not.toBeInTheDocument();
  });
});

describe('PluginLifecycleControl', () => {
  it('renders toggle and reload button', () => {
    renderPanel([lifecycle]);
    // The toggle switch
    const toggle = screen.getByRole('switch');
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    // The reload button
    const reloadBtn = screen.getByTitle('重载');
    expect(reloadBtn).toBeInTheDocument();
  });

  it('reload button is disabled when plugin is stopped', () => {
    const stopped = { ...lifecycle, current_value: false };
    renderPanel([stopped]);
    const reloadBtn = screen.getByTitle('重载');
    expect(reloadBtn).toBeDisabled();
  });

  it('calls onExecuteAction with toggle action_id on switch click', async () => {
    const { onExecuteAction } = renderPanel([lifecycle]);
    const toggle = screen.getByRole('switch');
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(onExecuteAction).toHaveBeenCalledWith('system:demo:toggle', false);
    });
  });

  it('calls onExecuteAction with reload action_id on reload click', async () => {
    const { onExecuteAction } = renderPanel([lifecycle]);
    const reloadBtn = screen.getByTitle('重载');
    fireEvent.click(reloadBtn);
    await waitFor(() => {
      expect(onExecuteAction).toHaveBeenCalledWith('system:demo:reload', null);
    });
  });
});

describe('ToggleControl', () => {
  it('renders a switch for toggle actions', () => {
    renderPanel([toggle]);
    // Switch to function view
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    const sw = screen.getByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  it('calls onExecuteAction with negated value on click', async () => {
    const { onExecuteAction } = renderPanel([toggle]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    const sw = screen.getByRole('switch');
    fireEvent.click(sw);
    await waitFor(() => {
      expect(onExecuteAction).toHaveBeenCalledWith('demo:settings:enabled', true);
    });
  });
});

describe('ButtonControl', () => {
  it('renders an execute button', () => {
    renderPanel([button]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    expect(screen.getByText('执行')).toBeInTheDocument();
  });

  it('calls onExecuteAction with null value on click', async () => {
    const { onExecuteAction } = renderPanel([button]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    fireEvent.click(screen.getByText('执行'));
    await waitFor(() => {
      expect(onExecuteAction).toHaveBeenCalledWith('system:demo:entry:do_thing', null);
    });
  });
});

describe('InjectButton', () => {
  it('calls onInjectText and onClose on click', () => {
    const { onInjectText, onClose } = renderPanel([inject]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    fireEvent.click(screen.getByText('注入'));
    expect(onInjectText).toHaveBeenCalledWith('@Demo /greet');
    expect(onClose).toHaveBeenCalled();
  });
});

describe('NavButton', () => {
  it('calls onNavigate on click', () => {
    const { onNavigate } = renderPanel([nav]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    fireEvent.click(screen.getByText('打开'));
    expect(onNavigate).toHaveBeenCalledWith(
      'http://127.0.0.1:9090/plugin/demo/ui/',
      'new_tab',
    );
  });
});

describe('Error handling', () => {
  it('shows error indicator when execute fails', async () => {
    const onExecuteAction = vi.fn().mockRejectedValue(new Error('boom'));
    renderPanel([button], { onExecuteAction });
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    fireEvent.click(screen.getByText('执行'));
    await waitFor(() => {
      expect(screen.getByTitle('boom')).toBeInTheDocument();
    });
  });
});

describe('SliderControl', () => {
  it('renders a range input', () => {
    renderPanel([slider]);
    fireEvent.click(screen.getByRole('button', { name: /按功能/ }));
    const input = screen.getByRole('slider');
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '100');
  });
});
