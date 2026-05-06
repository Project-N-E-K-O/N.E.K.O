import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import CommandPalette, { type CommandItem, type UserPreferences } from '../CommandPalette';

/* ── Fixtures ──────────────────────────────────────────────────────── */

const toggle: CommandItem = {
  action_id: 'demo:settings:enabled',
  type: 'instant',
  label: 'Enabled',
  description: 'Toggle feature',
  category: 'Demo',
  plugin_id: 'demo',
  control: 'toggle',
  current_value: false,
  icon: '🔘',
  keywords: ['demo', 'enabled'],
};

const slider: CommandItem = {
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
  icon: '🎚',
  keywords: ['demo', 'volume'],
};

const button: CommandItem = {
  action_id: 'system:demo:entry:do_thing',
  type: 'instant',
  label: 'Do Thing',
  description: 'Run a task',
  category: 'Demo',
  plugin_id: 'demo',
  control: 'button',
  icon: '⚡',
  keywords: ['demo', 'do_thing'],
};

const buttonWithParams: CommandItem = {
  ...button,
  action_id: 'system:demo:entry:with_params',
  label: 'With Params',
  input_schema: {
    type: 'object',
    properties: {
      name: { type: 'string', description: 'Name' },
    },
  },
};

const inject: CommandItem = {
  action_id: 'demo:greet',
  type: 'chat_inject',
  label: 'Greet',
  description: 'Say hello',
  category: 'Demo',
  plugin_id: 'demo',
  inject_text: '@Demo /greet',
  icon: '📎',
  keywords: ['demo', 'greet'],
};

const nav: CommandItem = {
  action_id: 'system:demo:open_ui',
  type: 'navigation',
  label: 'Open UI',
  description: '',
  category: 'Demo',
  plugin_id: 'demo',
  target: 'http://127.0.0.1:9090/plugin/demo/ui/',
  open_in: 'new_tab',
  icon: '↗',
  keywords: ['demo', 'ui'],
};

const startPlugin: CommandItem = {
  action_id: 'system:tts:start',
  type: 'instant',
  label: '启动 TTS',
  description: '',
  category: '插件管理',
  plugin_id: 'tts',
  control: 'button',
  icon: '▶',
  keywords: ['tts', 'start', '启动'],
  priority: -10,
};

const allItems = [toggle, slider, button, inject, nav, startPlugin];

const emptyPrefs: UserPreferences = { pinned: [], hidden: [], recent: [] };

function renderPalette(
  items: CommandItem[] = allItems,
  preferences: UserPreferences = emptyPrefs,
  overrides: Partial<React.ComponentProps<typeof CommandPalette>> = {},
) {
  const onExecute = vi.fn().mockResolvedValue(null);
  const onInjectText = vi.fn();
  const onNavigate = vi.fn();
  const onPreferencesChange = vi.fn();
  const onClose = vi.fn();

  const result = render(
    <CommandPalette
      items={items}
      preferences={preferences}
      onExecute={onExecute}
      onInjectText={onInjectText}
      onNavigate={onNavigate}
      onPreferencesChange={onPreferencesChange}
      onClose={onClose}
      {...overrides}
    />,
  );

  return { ...result, onExecute, onInjectText, onNavigate, onPreferencesChange, onClose };
}

/* ── Tests ─────────────────────────────────────────────────────────── */

describe('CommandPalette', () => {
  it('renders the panel with search bar', () => {
    renderPalette();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索操作...')).toBeInTheDocument();
  });

  it('auto-focuses the search input on open', () => {
    renderPalette();
    expect(screen.getByPlaceholderText('搜索操作...')).toHaveFocus();
  });

  it('shows all items in the "全部" section by default', () => {
    renderPalette();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Volume')).toBeInTheDocument();
    expect(screen.getByText('Do Thing')).toBeInTheDocument();
    expect(screen.getByText('Greet')).toBeInTheDocument();
    expect(screen.getByText('Open UI')).toBeInTheDocument();
    expect(screen.getByText('启动 TTS')).toBeInTheDocument();
  });

  it('closes on Escape key', () => {
    const { onClose } = renderPalette();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows empty state when no items', () => {
    renderPalette([]);
    expect(screen.getByText('暂无可用操作')).toBeInTheDocument();
  });
});

describe('Search', () => {
  it('filters items by label', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('搜索操作...');
    fireEvent.change(input, { target: { value: 'Volume' } });
    expect(screen.getByText('Volume')).toBeInTheDocument();
    expect(screen.queryByText('Greet')).not.toBeInTheDocument();
  });

  it('filters items by keywords', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('搜索操作...');
    fireEvent.change(input, { target: { value: 'greet' } });
    expect(screen.getByText('Greet')).toBeInTheDocument();
    expect(screen.queryByText('Volume')).not.toBeInTheDocument();
  });

  it('shows no-results state when search has no matches', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('搜索操作...');
    fireEvent.change(input, { target: { value: 'xyznonexistent' } });
    expect(screen.getByText('没有匹配的操作')).toBeInTheDocument();
  });

  it('shows clear button when search has text', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('搜索操作...');
    fireEvent.change(input, { target: { value: 'test' } });
    const clearBtn = screen.getByLabelText('清除搜索');
    expect(clearBtn).toBeInTheDocument();
    fireEvent.click(clearBtn);
    expect(input).toHaveValue('');
  });
});

describe('Sections', () => {
  it('shows pinned items first when preferences have pinned items', () => {
    const prefs: UserPreferences = { pinned: ['demo:greet'], hidden: [], recent: [] };
    renderPalette(allItems, prefs);
    // Pinned item should be visible
    expect(screen.getByText('Greet')).toBeInTheDocument();
  });

  it('shows items from recent list', () => {
    const prefs: UserPreferences = { pinned: [], hidden: [], recent: ['demo:settings:volume'] };
    renderPalette(allItems, prefs);
    // Recent item should be visible
    expect(screen.getByText('Volume')).toBeInTheDocument();
  });

  it('hides items in hidden list', () => {
    const prefs: UserPreferences = { pinned: [], hidden: ['demo:greet'], recent: [] };
    renderPalette(allItems, prefs);
    // Greet should not be visible in normal view
    expect(screen.queryByText('Greet')).not.toBeInTheDocument();
  });

  it('shows hidden items (greyed) when searching', () => {
    const prefs: UserPreferences = { pinned: [], hidden: ['demo:greet'], recent: [] };
    renderPalette(allItems, prefs);
    const input = screen.getByPlaceholderText('搜索操作...');
    fireEvent.change(input, { target: { value: 'Greet' } });
    // Should appear in search results even though hidden
    expect(screen.getByText('Greet')).toBeInTheDocument();
  });

  it('does not duplicate pinned items in the all section', () => {
    const prefs: UserPreferences = { pinned: ['demo:greet'], hidden: [], recent: [] };
    renderPalette(allItems, prefs);
    // Greet should appear once (in pinned), not twice
    const greetElements = screen.getAllByText('Greet');
    expect(greetElements).toHaveLength(1);
  });
});

describe('Toggle control', () => {
  it('renders a switch for toggle items', () => {
    renderPalette([toggle]);
    const sw = screen.getByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  it('calls onExecute with negated value on click', async () => {
    const { onExecute } = renderPalette([toggle]);
    const sw = screen.getByRole('switch');
    fireEvent.click(sw);
    await waitFor(() => {
      expect(onExecute).toHaveBeenCalledWith('demo:settings:enabled', true);
    });
  });
});

describe('Button control', () => {
  it('calls onExecute with null on click', async () => {
    const { onExecute } = renderPalette([button]);
    // The row is a div[role=button] — find by the label text inside it
    const label = screen.getByText('Do Thing');
    fireEvent.click(label.closest('.cp-row')!);
    await waitFor(() => {
      expect(onExecute).toHaveBeenCalledWith('system:demo:entry:do_thing', null);
    });
  });

  it('opens parameter form instead of executing null from keyboard Enter', () => {
    const { onExecute } = renderPalette([buttonWithParams]);
    const input = screen.getByPlaceholderText('搜索操作...');

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(onExecute).not.toHaveBeenCalled();
  });
});

describe('Chat inject', () => {
  it('calls onInjectText and onClose on click', () => {
    const { onInjectText, onClose } = renderPalette([inject]);
    const label = screen.getByText('Greet');
    fireEvent.click(label.closest('.cp-row')!);
    expect(onInjectText).toHaveBeenCalledWith('@Demo /greet');
    expect(onClose).toHaveBeenCalled();
  });
});

describe('Navigation', () => {
  it('calls onNavigate on click', () => {
    const { onNavigate } = renderPalette([nav]);
    const label = screen.getByText('Open UI');
    fireEvent.click(label.closest('.cp-row')!);
    expect(onNavigate).toHaveBeenCalledWith(
      'http://127.0.0.1:9090/plugin/demo/ui/',
      'new_tab',
    );
  });
});

describe('Context menu (pin/hide)', () => {
  it('toggles pin via context menu', async () => {
    const { onPreferencesChange } = renderPalette([button]);
    // Open context menu
    const ctxTrigger = screen.getByLabelText('更多');
    fireEvent.click(ctxTrigger);
    // Click pin
    const pinBtn = screen.getByText(/置顶/);
    fireEvent.click(pinBtn);
    expect(onPreferencesChange).toHaveBeenCalledWith(
      expect.objectContaining({ pinned: ['system:demo:entry:do_thing'] }),
    );
  });

  it('toggles hide via context menu', async () => {
    const { onPreferencesChange } = renderPalette([button]);
    const ctxTrigger = screen.getByLabelText('更多');
    fireEvent.click(ctxTrigger);
    const hideBtn = screen.getByText(/隐藏/);
    fireEvent.click(hideBtn);
    expect(onPreferencesChange).toHaveBeenCalledWith(
      expect.objectContaining({ hidden: ['system:demo:entry:do_thing'] }),
    );
  });
});

describe('Error handling', () => {
  it('shows error toast when execute fails', async () => {
    const onExecute = vi.fn().mockRejectedValue(new Error('boom'));
    renderPalette([button], emptyPrefs, { onExecute });
    const label = screen.getByText('Do Thing');
    fireEvent.click(label.closest('.cp-row')!);
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});

describe('Slider control', () => {
  it('renders a range input', () => {
    renderPalette([slider]);
    const input = screen.getByRole('slider');
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '100');
  });
});
