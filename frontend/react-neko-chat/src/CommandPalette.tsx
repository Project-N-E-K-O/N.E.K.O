import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { i18n } from './i18n';

/* ================================================================== */
/*  Types                                                              */
/* ================================================================== */

export interface CommandItem {
  action_id: string;
  type: 'instant' | 'chat_inject' | 'navigation';
  label: string;
  description: string;
  category: string;
  plugin_id: string;
  control?: 'toggle' | 'button' | 'dropdown' | 'number' | 'slider' | 'plugin_lifecycle' | 'entry_toggle';
  current_value?: unknown;
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  inject_text?: string;
  input_schema?: Record<string, unknown>;
  target?: string;
  open_in?: 'new_tab' | 'same_tab';
  keywords?: string[];
  icon?: string | null;
  priority?: number;
  section?: 'pinned' | 'recent' | 'commands' | null;
  quick_action?: boolean;
}

export interface UserPreferences {
  pinned: string[];
  hidden: string[];
  recent: string[];
}

export interface CommandPaletteProps {
  items: CommandItem[];
  preferences: UserPreferences;
  loading?: boolean;
  slashMode?: boolean;
  onExecute: (actionId: string, value: unknown) => Promise<CommandItem | null>;
  onInjectText: (text: string) => void;
  onNavigate: (target: string, openIn: string) => void;
  onPreferencesChange: (prefs: UserPreferences) => void;
  onClose: () => void;
}

/* ================================================================== */
/*  Helpers                                                            */
/* ================================================================== */

function matchesSearch(item: CommandItem, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    item.label.toLowerCase().includes(q) ||
    item.description.toLowerCase().includes(q) ||
    item.plugin_id.toLowerCase().includes(q) ||
    item.category.toLowerCase().includes(q) ||
    (item.keywords ?? []).some(k => k.toLowerCase().includes(q))
  );
}

function defaultIcon(item: CommandItem): string {
  if (item.icon) return item.icon;
  if (item.type === 'chat_inject') return '📎';
  if (item.type === 'navigation') return '↗';
  switch (item.control) {
    case 'toggle': return '🔘';
    case 'slider': return '🎚';
    case 'number': return '🔢';
    case 'dropdown': return '📋';
    case 'button': return '⚡';
    default: return '•';
  }
}

/* ================================================================== */
/*  Inline control renderers                                           */
/* ================================================================== */

interface ControlProps {
  item: CommandItem;
  loading: boolean;
  error: string | null;
  onExec: (id: string, value: unknown) => void;
  onInject: (text: string) => void;
  onNavigate: (target: string, openIn: string) => void;
}

function ToggleWidget({ item, loading, onExec }: ControlProps) {
  const checked = Boolean(item.current_value);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={item.label}
      className={`cp-toggle ${checked ? 'is-on' : ''}`}
      disabled={item.disabled || loading}
      onClick={e => { e.stopPropagation(); onExec(item.action_id, !checked); }}
    >
      <span className="cp-toggle-thumb" />
    </button>
  );
}

function DropdownWidget({ item, loading, onExec }: ControlProps) {
  return (
    <select
      className="cp-select"
      value={String(item.current_value ?? '')}
      disabled={item.disabled || loading}
      aria-label={item.label}
      onClick={e => e.stopPropagation()}
      onChange={e => onExec(item.action_id, e.target.value)}
    >
      {(item.options ?? []).map(o => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

function SliderWidget({ item, loading, onExec }: ControlProps) {
  const numVal = Number(item.current_value ?? item.min ?? 0);
  const [local, setLocal] = useState(numVal);
  const committed = useRef(numVal);
  useEffect(() => { setLocal(numVal); committed.current = numVal; }, [numVal]);

  const commit = () => {
    if (local !== committed.current) {
      committed.current = local;
      onExec(item.action_id, local);
    }
  };

  return (
    <div className="cp-slider-wrap" onClick={e => e.stopPropagation()}>
      <input
        type="range"
        className="cp-slider"
        min={item.min ?? 0}
        max={item.max ?? 100}
        step={item.step ?? 1}
        value={local}
        disabled={item.disabled || loading}
        aria-label={item.label}
        onChange={e => setLocal(Number(e.target.value))}
        onMouseUp={commit}
        onTouchEnd={commit}
        onKeyUp={commit}
      />
      <span className="cp-slider-val">{local}</span>
    </div>
  );
}

function NumberWidget({ item, loading, onExec }: ControlProps) {
  const numVal = Number(item.current_value ?? 0);
  const [local, setLocal] = useState(numVal);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { setLocal(numVal); }, [numVal]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const commit = useCallback(
    (v: number) => {
      if (timer.current) clearTimeout(timer.current);
      // Clamp to min/max and reject NaN
      const lo = item.min ?? -Infinity;
      const hi = item.max ?? Infinity;
      const clamped = Number.isFinite(v) ? Math.min(Math.max(v, lo), hi) : numVal;
      timer.current = setTimeout(() => onExec(item.action_id, clamped), 400);
    },
    [item.action_id, item.min, item.max, numVal, onExec],
  );
  const step = item.step ?? 1;
  const inc = () => { const n = Math.min(local + step, item.max ?? Infinity); setLocal(n); commit(n); };
  const dec = () => { const n = Math.max(local - step, item.min ?? -Infinity); setLocal(n); commit(n); };

  return (
    <div className="cp-num-group" onClick={e => e.stopPropagation()}>
      <button type="button" className="cp-num-btn" disabled={item.disabled || loading} onClick={dec} aria-label={`${item.label} −`}>−</button>
      <input
        type="number"
        className="cp-num-input"
        value={local}
        min={item.min}
        max={item.max}
        step={item.step}
        disabled={item.disabled || loading}
        aria-label={item.label}
        onChange={e => { const v = Number(e.target.value); setLocal(v); commit(v); }}
      />
      <button type="button" className="cp-num-btn" disabled={item.disabled || loading} onClick={inc} aria-label={`${item.label} +`}>+</button>
    </div>
  );
}

function InlineWidget(props: ControlProps) {
  const { item } = props;
  if (item.type === 'chat_inject' || item.type === 'navigation') return null;
  switch (item.control) {
    case 'toggle':
    case 'entry_toggle':
      return <ToggleWidget {...props} />;
    case 'dropdown':
      return <DropdownWidget {...props} />;
    case 'slider':
      return <SliderWidget {...props} />;
    case 'number':
      return <NumberWidget {...props} />;
    default:
      return null;
  }
}

/* ================================================================== */
/*  Parameter form (for button entries with input_schema)              */
/* ================================================================== */

function ParamForm({ item, onExec, onCancel }: {
  item: CommandItem;
  onExec: (id: string, value: unknown) => void;
  onCancel: () => void;
}) {
  const schema = item.input_schema as Record<string, unknown> | undefined;
  const properties = (schema?.properties ?? {}) as Record<string, { type?: string; description?: string; default?: unknown }>;
  const propKeys = Object.keys(properties);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const defaults: Record<string, string> = {};
    for (const key of propKeys) {
      const prop = properties[key];
      defaults[key] = prop?.default != null ? String(prop.default) : '';
    }
    return defaults;
  });

  const submit = () => {
    const args: Record<string, unknown> = {};
    for (const key of propKeys) {
      const prop = properties[key];
      const raw = values[key] ?? '';
      if (prop?.type === 'number' || prop?.type === 'integer') {
        args[key] = Number(raw) || 0;
      } else if (prop?.type === 'boolean') {
        args[key] = raw === 'true' || raw === '1';
      } else {
        args[key] = raw;
      }
    }
    onExec(item.action_id, args);
  };

  return (
    <div className="cp-param-form" onClick={e => e.stopPropagation()}>
      {propKeys.map(key => {
        const prop = properties[key];
        return (
          <label key={key} className="cp-param-field">
            <span className="cp-param-label">{prop?.description || key}</span>
            <input
              type={prop?.type === 'number' || prop?.type === 'integer' ? 'number' : 'text'}
              className="cp-param-input"
              value={values[key] ?? ''}
              placeholder={key}
              onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))}
              onKeyDown={e => { if (e.key === 'Enter') submit(); }}
            />
          </label>
        );
      })}
      <div className="cp-param-actions">
        <button type="button" className="cp-param-cancel" onClick={onCancel}>
          {i18n('commandPalette.cancel', '取消')}
        </button>
        <button type="button" className="cp-param-submit" onClick={submit}>
          {i18n('commandPalette.confirm', '确认')}
        </button>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Context menu (pin / hide)                                          */
/* ================================================================== */

function ContextMenu({ item, prefs, onPrefsChange, onClose }: {
  item: CommandItem;
  prefs: UserPreferences;
  onPrefsChange: (p: UserPreferences) => void;
  onClose: () => void;
}) {
  const isPinned = prefs.pinned.includes(item.action_id);
  const isHidden = prefs.hidden.includes(item.action_id);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => { clearTimeout(id); document.removeEventListener('mousedown', handler); };
  }, [onClose]);

  const togglePin = () => {
    const next = { ...prefs };
    if (isPinned) {
      next.pinned = next.pinned.filter(id => id !== item.action_id);
    } else {
      next.pinned = [...next.pinned, item.action_id];
      // Unpin also unhides
      next.hidden = next.hidden.filter(id => id !== item.action_id);
    }
    onPrefsChange(next);
    onClose();
  };

  const toggleHide = () => {
    const next = { ...prefs };
    if (isHidden) {
      next.hidden = next.hidden.filter(id => id !== item.action_id);
    } else {
      next.hidden = [...next.hidden, item.action_id];
      // Hide also unpins
      next.pinned = next.pinned.filter(id => id !== item.action_id);
    }
    onPrefsChange(next);
    onClose();
  };

  return (
    <div className="cp-ctx-menu" ref={menuRef}>
      <button type="button" className="cp-ctx-item" onClick={togglePin}>
        {isPinned
          ? `📌 ${i18n('commandPalette.unpin', '取消置顶')}`
          : `📌 ${i18n('commandPalette.pin', '置顶')}`}
      </button>
      <button type="button" className="cp-ctx-item" onClick={toggleHide}>
        {isHidden
          ? `👁 ${i18n('commandPalette.unhide', '取消隐藏')}`
          : `🙈 ${i18n('commandPalette.hide', '隐藏')}`}
      </button>
    </div>
  );
}

/* ================================================================== */
/*  Single command row                                                 */
/* ================================================================== */

function CommandRow({ item, loading, error, highlighted, prefs, onExec, onInject, onNavigate, onPrefsChange }: {
  item: CommandItem;
  loading: boolean;
  error: string | null;
  highlighted?: boolean;
  prefs: UserPreferences;
  onExec: (id: string, value: unknown) => void;
  onInject: (text: string) => void;
  onNavigate: (target: string, openIn: string) => void;
  onPrefsChange: (p: UserPreferences) => void;
}) {
  const [ctxOpen, setCtxOpen] = useState(false);
  const [paramFormOpen, setParamFormOpen] = useState(false);
  const isHidden = prefs.hidden.includes(item.action_id);
  const isPinned = prefs.pinned.includes(item.action_id);

  const hasInlineWidget = item.type === 'instant' && (
    item.control === 'toggle' || item.control === 'entry_toggle' ||
    item.control === 'dropdown' || item.control === 'slider' || item.control === 'number'
  );

  const hasParams = (() => {
    if (item.control !== 'button') return false;
    const schema = item.input_schema as Record<string, unknown> | undefined;
    const props = schema?.properties as Record<string, unknown> | undefined;
    return props && Object.keys(props).length > 0;
  })();

  const handleRowClick = () => {
    if (hasInlineWidget) return;
    if (item.disabled || loading) return;
    if (item.type === 'chat_inject') {
      onInject(item.inject_text ?? '');
      return;
    }
    if (item.type === 'navigation') {
      onNavigate(item.target ?? '', item.open_in ?? 'new_tab');
      return;
    }
    if (item.control === 'button') {
      if (hasParams) {
        setParamFormOpen(open => !open);
      } else {
        onExec(item.action_id, null);
      }
    }
  };

  const controlProps: ControlProps = { item, loading, error, onExec, onInject, onNavigate };

  return (
    <div className={`cp-row-wrap ${isHidden ? 'is-hidden' : ''}`}>
      <div
        className={`cp-row ${hasInlineWidget ? '' : 'cp-row-clickable'}${highlighted ? ' cp-row-highlighted' : ''}`}
        onClick={handleRowClick}
        role={hasInlineWidget ? undefined : 'button'}
        tabIndex={hasInlineWidget ? undefined : 0}
        onKeyDown={hasInlineWidget ? undefined : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleRowClick(); } }}
      >
        <span className="cp-row-icon-wrap" aria-hidden="true">
          <span className="cp-row-icon">{defaultIcon(item)}</span>
        </span>
        <div className="cp-row-info">
          <div className="cp-row-label-line">
            <span className="cp-row-label">{item.label}</span>
            {isPinned && <span className="cp-row-pin-badge" aria-label="pinned">📌</span>}
          </div>
          {item.description ? (
            <span className="cp-row-desc">{item.description}</span>
          ) : (
            <span className="cp-row-desc cp-row-category">{item.category}</span>
          )}
        </div>
        <div className="cp-row-right">
          {loading && <span className="cp-spinner" />}
          <InlineWidget {...controlProps} />
          {!hasInlineWidget && item.type === 'chat_inject' && (
            <span className="cp-row-badge cp-row-badge-inject">{i18n('commandPalette.inject', '注入')}</span>
          )}
          {!hasInlineWidget && item.type === 'navigation' && (
            <span className="cp-row-badge cp-row-badge-nav">{i18n('commandPalette.open', '打开')}</span>
          )}
          {!hasInlineWidget && item.control === 'button' && !hasParams && (
            <span className="cp-row-badge cp-row-badge-run">{i18n('commandPalette.run', '执行')}</span>
          )}
          {!hasInlineWidget && item.control === 'button' && hasParams && (
            <span className="cp-row-badge cp-row-badge-run">{paramFormOpen ? '▾' : i18n('commandPalette.run', '执行')}</span>
          )}
          {error && <span className="cp-err" title={error}>!</span>}
          <button
            type="button"
            className="cp-ctx-trigger"
            aria-label={i18n('commandPalette.more', '更多')}
            onClick={e => { e.stopPropagation(); setCtxOpen(o => !o); }}
          >
            ⋮
          </button>
          {ctxOpen && (
            <ContextMenu
              item={item}
              prefs={prefs}
              onPrefsChange={onPrefsChange}
              onClose={() => setCtxOpen(false)}
            />
          )}
        </div>
      </div>
      {paramFormOpen && hasParams && (
        <ParamForm
          item={item}
          onExec={(id, val) => { setParamFormOpen(false); onExec(id, val); }}
          onCancel={() => setParamFormOpen(false)}
        />
      )}
    </div>
  );
}

/* ================================================================== */
/*  Toast stack                                                        */
/* ================================================================== */

type ToastItem = { id: number; tone: 'success' | 'error'; text: string };
let _toastId = 0;

function ToastStack({ toasts }: { toasts: ToastItem[] }) {
  if (toasts.length === 0) return null;
  return (
    <div className="cp-toast-stack">
      {toasts.map(t => (
        <div key={t.id} className={`message-block-status tone-${t.tone} cp-toast`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

/* ================================================================== */
/*  Helpers: classify items                                            */
/* ================================================================== */

type ContentTab = 'quick' | 'settings' | 'all';
type GroupMode = 'byPlugin' | 'byFunction';

const _SETTINGS_CONTROLS = new Set(['toggle', 'entry_toggle', 'dropdown', 'slider', 'number']);

function isSettingsItem(item: CommandItem): boolean {
  return item.type === 'instant' && _SETTINGS_CONTROLS.has(item.control ?? '');
}

function functionGroupLabel(item: CommandItem): string {
  if (item.type === 'chat_inject') return '💬 斜杠命令';
  if (item.type === 'navigation') return '↗ 导航';
  switch (item.control) {
    case 'toggle': case 'entry_toggle': return '🔘 开关';
    case 'slider': return '🎚 滑块';
    case 'number': return '🔢 数值';
    case 'dropdown': return '📋 下拉';
    case 'button': return '⚡ 操作';
    case 'plugin_lifecycle': return '🔧 生命周期';
    default: return '• 其他';
  }
}

function groupItems(items: CommandItem[], mode: GroupMode): Map<string, CommandItem[]> {
  const groups = new Map<string, CommandItem[]>();
  for (const item of items) {
    // "按插件" groups by plugin_id so lifecycle actions join their plugin
    const key = mode === 'byPlugin' ? item.plugin_id : functionGroupLabel(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(item);
  }
  return groups;
}

/* ================================================================== */
/*  Collapsible plugin card (for "按插件" view)                        */
/* ================================================================== */

function PluginCard({ pluginName, items, loadingMap, errorMap, sharedRowProps }: {
  pluginName: string;
  items: CommandItem[];
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  sharedRowProps: {
    prefs: UserPreferences;
    onExec: (id: string, value: unknown) => void;
    onInject: (text: string) => void;
    onNavigate: (target: string, openIn: string) => void;
    onPrefsChange: (p: UserPreferences) => void;
  };
}) {
  const [expanded, setExpanded] = useState(false);
  const entryCount = items.filter(a => a.category !== '插件管理').length;
  const mgmtCount = items.filter(a => a.category === '插件管理').length;

  return (
    <div className="cp-plugin-card">
      <button
        type="button"
        className="cp-plugin-card-header"
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <span className="cp-plugin-card-chevron">{expanded ? '▾' : '▸'}</span>
        <span className="cp-plugin-card-name">{pluginName}</span>
        <span className="cp-plugin-card-counts">
          {entryCount > 0 && <span className="cp-plugin-card-badge">{entryCount}</span>}
          {mgmtCount > 0 && <span className="cp-plugin-card-badge cp-plugin-card-badge-mgmt">⚙{mgmtCount}</span>}
        </span>
      </button>
      {expanded && (
        <div className="cp-plugin-card-body">
          {items.map((item) => (
            <CommandRow
              key={item.action_id}
              item={item}
              loading={!!loadingMap[item.action_id]}
              error={errorMap[item.action_id] ?? null}
              highlighted={false}
              {...sharedRowProps}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */
/*  Main component                                                     */
/* ================================================================== */

export default function CommandPalette({
  items,
  preferences,
  loading: externalLoading = false,
  slashMode = false,
  onExecute,
  onInjectText,
  onNavigate,
  onPreferencesChange,
  onClose,
}: CommandPaletteProps) {
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState<GroupMode>('byFunction');
  const [filterTab, setFilterTab] = useState<ContentTab>('all');
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});
  const [localItems, setLocalItems] = useState<CommandItem[]>(items);
  const [localPrefs, setLocalPrefs] = useState<UserPreferences>(preferences);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const panelRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setLocalItems(items); }, [items]);
  useEffect(() => { setLocalPrefs(preferences); }, [preferences]);

  useEffect(() => { searchRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => document.addEventListener('mousedown', onClick), 0);
    return () => { clearTimeout(id); document.removeEventListener('mousedown', onClick); };
  }, [onClose]);

  const pushToast = useCallback((tone: ToastItem['tone'], text: string) => {
    const id = ++_toastId;
    setToasts(prev => [...prev.slice(-2), { id, tone, text }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
  }, []);

  const handleExecute = useCallback(async (actionId: string, value: unknown) => {
    setLoadingMap(m => ({ ...m, [actionId]: true }));
    setErrorMap(m => ({ ...m, [actionId]: null }));
    const label = localItems.find(a => a.action_id === actionId)?.label ?? actionId;
    try {
      const updated = await onExecute(actionId, value);
      // Don't do local patching here — the host will re-fetch all actions
      // and pass new `items` prop, which triggers the useEffect sync above.
      // Local patching would conflict with the full refresh.
      if (updated) {
        setLocalItems(prev => prev.map(a => (a.action_id === updated.action_id ? updated : a)));
      }
      pushToast('success', `${label}: ${i18n('commandPalette.success', '成功')}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      const cleanMsg = msg.replace(/^executeChatAction:\s*HTTP\s*\d+\s*[-–—]?\s*/i, '');
      setErrorMap(m => ({ ...m, [actionId]: cleanMsg }));
      setTimeout(() => setErrorMap(m => ({ ...m, [actionId]: null })), 3000);
      pushToast('error', `${label}: ${cleanMsg}`);
    } finally {
      setLoadingMap(m => ({ ...m, [actionId]: false }));
    }
  }, [onExecute, localItems, pushToast]);

  const handleInject = useCallback((text: string) => {
    onInjectText(text);
    onClose();
  }, [onInjectText, onClose]);

  const handlePrefsChange = useCallback((prefs: UserPreferences) => {
    setLocalPrefs(prefs);
    onPreferencesChange(prefs);
  }, [onPreferencesChange]);

  // ── Build display items ──
  const { displayItems, hasResults } = useMemo(() => {
    const isSearching = search.trim().length > 0;
    const baseItems = slashMode
      ? localItems.filter(a => a.type === 'chat_inject')
      : localItems;
    const matched = baseItems.filter(a => matchesSearch(a, search));
    const visible = isSearching
      ? matched
      : matched.filter(a => !localPrefs.hidden.includes(a.action_id));

    const sortByPriority = (a: CommandItem, b: CommandItem) => {
      const pa = a.priority ?? 0;
      const pb = b.priority ?? 0;
      if (pa !== pb) return pb - pa;
      return a.label.localeCompare(b.label);
    };

    // "按插件" mode: show all, no filter tabs
    if (viewMode === 'byPlugin') {
      const sorted = [...visible].sort(sortByPriority);
      return { displayItems: sorted, hasResults: sorted.length > 0 };
    }

    // "按功能" mode: apply filter tab
    let filtered: CommandItem[];
    if (filterTab === 'quick') {
      const pinnedIds = new Set(localPrefs.pinned);
      const pinned = localPrefs.pinned
        .map(id => visible.find(a => a.action_id === id))
        .filter((a): a is CommandItem => a !== undefined);
      const quick = visible
        .filter(a => a.quick_action && !pinnedIds.has(a.action_id))
        .sort(sortByPriority);
      filtered = [...pinned, ...quick];
    } else if (filterTab === 'settings') {
      filtered = visible.filter(isSettingsItem).sort(sortByPriority);
    } else {
      filtered = [...visible].sort(sortByPriority);
    }

    return { displayItems: filtered, hasResults: filtered.length > 0 };
  }, [localItems, localPrefs, search, slashMode, viewMode, filterTab]);

  const isSearching = search.trim().length > 0;

  // ── Keyboard navigation ──
  const [highlightIdx, setHighlightIdx] = useState(-1);
  useEffect(() => { setHighlightIdx(-1); }, [search, displayItems.length, viewMode, filterTab]);

  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIdx(prev => (prev + 1) % Math.max(displayItems.length, 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIdx(prev => prev <= 0 ? displayItems.length - 1 : prev - 1);
    } else if (e.key === 'Enter' && highlightIdx >= 0 && highlightIdx < displayItems.length) {
      e.preventDefault();
      const item = displayItems[highlightIdx];
      if (item.type === 'chat_inject') {
        handleInject(item.inject_text ?? '');
      } else if (item.type === 'navigation') {
        onNavigate(item.target ?? '', item.open_in ?? 'new_tab');
      } else if (item.control === 'button') {
        handleExecute(item.action_id, null);
      }
    }
  }, [displayItems, highlightIdx, handleInject, onNavigate, handleExecute]);

  const sharedRowProps = {
    prefs: localPrefs,
    onExec: handleExecute, onInject: handleInject, onNavigate,
    onPrefsChange: handlePrefsChange,
  };

  const hasQuickActions = localItems.some(a => a.quick_action);
  const hasSettingsItems = localItems.some(isSettingsItem);
  const showFilterTabs = viewMode === 'byFunction' && !slashMode;
  const showGrouped = viewMode === 'byPlugin' && !isSearching;

  const renderGrouped = (items: CommandItem[]) => {
    const groups = groupItems(items, 'byPlugin');
    // Resolve display name: use category from first non-management item, fallback to plugin_id
    return Array.from(groups.entries()).map(([pluginId, groupItems]) => {
      const nameItem = groupItems.find(a => a.category !== '插件管理');
      const displayName = nameItem?.category || pluginId;
      return (
        <PluginCard
          key={pluginId}
          pluginName={displayName}
          items={groupItems}
          loadingMap={loadingMap}
          errorMap={errorMap}
          sharedRowProps={sharedRowProps}
        />
      );
    });
  };

  const renderFlat = (items: CommandItem[]) => (
    <div className="cp-section">
      {items.map((item, i) => (
        <div key={item.action_id} className="cp-stagger" style={{ animationDelay: `${i * 20}ms` }}>
          <CommandRow item={item} loading={!!loadingMap[item.action_id]} error={errorMap[item.action_id] ?? null} highlighted={highlightIdx === i} {...sharedRowProps} />
        </div>
      ))}
    </div>
  );

  const emptyText = (() => {
    if (isSearching) return i18n('commandPalette.noResults', '没有匹配的操作');
    if (viewMode === 'byFunction') {
      if (filterTab === 'quick') return i18n('commandPalette.noQuickActions', '暂无快捷操作');
      if (filterTab === 'settings') return i18n('commandPalette.noSettings', '暂无配置项');
    }
    return i18n('commandPalette.empty', '暂无可用操作');
  })();

  return (
    <div className="cp-panel" ref={panelRef} role="dialog" aria-label={i18n('commandPalette.title', '命令面板')}>
      {/* ── Search ── */}
      <div className="cp-search-bar">
        <span className="cp-search-icon" aria-hidden="true">🔍</span>
        <input
          ref={searchRef}
          type="text"
          className="cp-search"
          placeholder={slashMode
            ? i18n('commandPalette.slashPlaceholder', '搜索斜杠命令...')
            : i18n('commandPalette.searchPlaceholder', '搜索操作...')}
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={handleSearchKeyDown}
          aria-label={i18n('commandPalette.searchAriaLabel', '搜索操作')}
        />
        {search && (
          <button type="button" className="cp-search-clear" aria-label={i18n('commandPalette.clearSearch', '清除搜索')}
            onClick={() => { setSearch(''); searchRef.current?.focus(); }}>✕</button>
        )}
      </div>

      {/* ── Filter tabs (only in "按功能" mode) ── */}
      {showFilterTabs && (
        <div className="cp-tab-bar cp-tab-bar-top">
          <button type="button" className={`cp-tab ${filterTab === 'all' ? 'cp-tab-active' : ''}`} onClick={() => setFilterTab('all')}>
            📋 {i18n('commandPalette.allCommands', '全部')}
          </button>
          {hasQuickActions && (
            <button type="button" className={`cp-tab ${filterTab === 'quick' ? 'cp-tab-active' : ''}`} onClick={() => setFilterTab('quick')}>
              ⚡ {i18n('commandPalette.quickActions', '快捷操作')}
            </button>
          )}
          {hasSettingsItems && (
            <button type="button" className={`cp-tab ${filterTab === 'settings' ? 'cp-tab-active' : ''}`} onClick={() => setFilterTab('settings')}>
              ⚙️ {i18n('commandPalette.settings', '配置项')}
            </button>
          )}
        </div>
      )}

      {/* ── Content ── */}
      <div className="cp-content">
        {externalLoading && localItems.length === 0 ? (
          <div className="cp-empty"><span className="cp-spinner" /></div>
        ) : !hasResults ? (
          <div className="cp-empty">
            <div className="cp-empty-icon" aria-hidden="true">{isSearching ? '🔍' : '📋'}</div>
            <div className="cp-empty-text">{emptyText}</div>
            {isSearching && (
              <button type="button" className="cp-empty-clear" onClick={() => { setSearch(''); searchRef.current?.focus(); }}>
                {i18n('commandPalette.clearSearch', '清除搜索')}
              </button>
            )}
          </div>
        ) : showGrouped ? (
          renderGrouped(displayItems)
        ) : (
          renderFlat(displayItems)
        )}
      </div>

      {/* ── Bottom: view mode toggle (always visible) ── */}
      {!slashMode && (
        <div className="cp-tab-bar cp-tab-bar-bottom">
          <button type="button" className={`cp-tab ${viewMode === 'byPlugin' ? 'cp-tab-active' : ''}`} onClick={() => setViewMode('byPlugin')}>
            🧩 {i18n('commandPalette.byPlugin', '按插件')}
          </button>
          <button type="button" className={`cp-tab ${viewMode === 'byFunction' ? 'cp-tab-active' : ''}`} onClick={() => setViewMode('byFunction')}>
            🏷️ {i18n('commandPalette.byFunction', '按功能')}
          </button>
        </div>
      )}

      <ToastStack toasts={toasts} />
    </div>
  );
}
