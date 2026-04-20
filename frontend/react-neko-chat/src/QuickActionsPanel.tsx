import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { i18n } from './i18n';

/* ================================================================== */
/*  Types                                                              */
/* ================================================================== */

export interface ActionDescriptor {
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
  target?: string;
  open_in?: 'new_tab' | 'same_tab';
}

export interface QuickActionsPanelProps {
  actions: ActionDescriptor[];
  onExecuteAction: (actionId: string, value: unknown) => Promise<ActionDescriptor | null>;
  onInjectText: (text: string) => void;
  onNavigate: (target: string, openIn: string) => void;
  onClose: () => void;
}

/* ================================================================== */
/*  View modes                                                         */
/* ================================================================== */

type ViewMode = 'function' | 'plugin';

interface ViewModeDef {
  id: ViewMode;
  icon: string;
  labelKey: string;
  labelFallback: string;
}

const VIEW_MODES: ViewModeDef[] = [
  { id: 'plugin', icon: '🧩', labelKey: 'quickActions.viewPlugin', labelFallback: '按插件' },
  { id: 'function', icon: '🎛', labelKey: 'quickActions.viewFunction', labelFallback: '按功能' },
];

/* Function-view sub-tabs */
type FuncTab = 'all' | 'config' | 'lifecycle' | 'inject';

interface FuncTabDef {
  id: FuncTab;
  labelKey: string;
  labelFallback: string;
}

const FUNC_TABS: FuncTabDef[] = [
  { id: 'all', labelKey: 'quickActions.funcAll', labelFallback: '全部' },
  { id: 'config', labelKey: 'quickActions.funcConfig', labelFallback: '配置' },
  { id: 'lifecycle', labelKey: 'quickActions.funcLifecycle', labelFallback: '生命周期' },
  { id: 'inject', labelKey: 'quickActions.funcInject', labelFallback: '注入 / 导航' },
];

/* ================================================================== */
/*  Helpers                                                            */
/* ================================================================== */

function matchesSearch(a: ActionDescriptor, q: string): boolean {
  if (!q) return true;
  const low = q.toLowerCase();
  return (
    a.label.toLowerCase().includes(low) ||
    a.description.toLowerCase().includes(low) ||
    a.plugin_id.toLowerCase().includes(low) ||
    a.category.toLowerCase().includes(low)
  );
}

function filterByFuncTab(actions: ActionDescriptor[], tab: FuncTab): ActionDescriptor[] {
  switch (tab) {
    case 'all':
      return actions;
    case 'config':
      return actions.filter(
        a =>
          a.type === 'instant' &&
          a.control !== 'plugin_lifecycle' &&
          a.control !== 'entry_toggle' &&
          a.control !== 'button',
      );
    case 'lifecycle':
      return actions.filter(
        a =>
          a.control === 'plugin_lifecycle' ||
          a.control === 'entry_toggle' ||
          a.control === 'button',
      );
    case 'inject':
      return actions.filter(a => a.type === 'chat_inject' || a.type === 'navigation');
    default:
      return actions;
  }
}

function groupBy(actions: ActionDescriptor[], key: 'category' | 'plugin_id'): Map<string, ActionDescriptor[]> {
  const map = new Map<string, ActionDescriptor[]>();
  for (const a of actions) {
    const k = a[key];
    const list = map.get(k);
    if (list) list.push(a);
    else map.set(k, [a]);
  }
  return map;
}

/** Derive a display name for a plugin_id from its actions (use category as name). */
function pluginDisplayName(actions: ActionDescriptor[]): string {
  // The category of the first non-system action is usually the plugin name
  for (const a of actions) {
    if (a.category !== '系统') return a.category;
  }
  return actions[0]?.plugin_id ?? '?';
}

/** Check if any action for this plugin is a running plugin_lifecycle */
function isPluginRunning(actions: ActionDescriptor[]): boolean {
  for (const a of actions) {
    if (a.control === 'plugin_lifecycle') return Boolean(a.current_value);
  }
  return false;
}

/* ================================================================== */
/*  Control renderers                                                  */
/* ================================================================== */

interface ControlProps {
  action: ActionDescriptor;
  loading: boolean;
  error: string | null;
  onExecute: (actionId: string, value: unknown) => void;
  onInject: (text: string) => void;
  onNavigate: (target: string, openIn: string) => void;
}

function ToggleControl({ action, loading, error, onExecute }: ControlProps) {
  const checked = Boolean(action.current_value);
  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">
          {action.label}
        </span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <div className="qa-row-widget">
        {loading && <span className="qa-spinner" />}
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-label={action.label}
          className={`qa-toggle ${checked ? 'is-on' : ''} ${action.control === 'entry_toggle' ? 'is-sm' : ''}`}
          disabled={action.disabled || loading}
          onClick={() => onExecute(action.action_id, !checked)}
        >
          <span className="qa-toggle-thumb" />
        </button>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function DropdownControl({ action, loading, error, onExecute }: ControlProps) {
  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <div className="qa-row-widget">
        {loading && <span className="qa-spinner" />}
        <select
          className="qa-select"
          value={String(action.current_value ?? '')}
          disabled={action.disabled || loading}
          aria-label={action.label}
          onChange={e => onExecute(action.action_id, e.target.value)}
        >
          {(action.options ?? []).map(o => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function SliderControl({ action, loading, error, onExecute }: ControlProps) {
  const numVal = Number(action.current_value ?? action.min ?? 0);
  const [local, setLocal] = useState(numVal);
  const committed = useRef(numVal);
  useEffect(() => { setLocal(numVal); committed.current = numVal; }, [numVal]);

  const commit = () => {
    if (local !== committed.current) {
      committed.current = local;
      onExecute(action.action_id, local);
    }
  };

  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <div className="qa-row-widget qa-slider-wrap">
        {loading && <span className="qa-spinner" />}
        <input
          type="range"
          className="qa-slider"
          min={action.min ?? 0}
          max={action.max ?? 100}
          step={action.step ?? 1}
          value={local}
          disabled={action.disabled || loading}
          aria-label={action.label}
          onChange={e => setLocal(Number(e.target.value))}
          onMouseUp={commit}
          onTouchEnd={commit}
        />
        <span className="qa-slider-val">{local}</span>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function NumberControl({ action, loading, error, onExecute }: ControlProps) {
  const numVal = Number(action.current_value ?? 0);
  const [local, setLocal] = useState(numVal);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { setLocal(numVal); }, [numVal]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const commit = useCallback(
    (v: number) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => onExecute(action.action_id, v), 400);
    },
    [action.action_id, onExecute],
  );
  const step = action.step ?? 1;
  const inc = () => { const n = Math.min(local + step, action.max ?? Infinity); setLocal(n); commit(n); };
  const dec = () => { const n = Math.max(local - step, action.min ?? -Infinity); setLocal(n); commit(n); };

  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <div className="qa-row-widget">
        {loading && <span className="qa-spinner" />}
        <div className="qa-num-group">
          <button type="button" className="qa-num-btn" disabled={action.disabled || loading} onClick={dec} aria-label={`${action.label} −`}>−</button>
          <input
            type="number"
            className="qa-num-input"
            value={local}
            min={action.min}
            max={action.max}
            step={action.step}
            disabled={action.disabled || loading}
            aria-label={action.label}
            onChange={e => { const v = Number(e.target.value); setLocal(v); commit(v); }}
          />
          <button type="button" className="qa-num-btn" disabled={action.disabled || loading} onClick={inc} aria-label={`${action.label} +`}>+</button>
        </div>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function PluginLifecycleControl({ action, loading, error, onExecute }: ControlProps) {
  const running = Boolean(action.current_value);
  // Derive the reload action_id from the toggle action_id: system:{pid}:toggle → system:{pid}:reload
  const reloadId = action.action_id.replace(/:toggle$/, ':reload');
  return (
    <div className="qa-lifecycle-row">
      <div className="qa-lifecycle-info">
        <span className={`qa-dot ${running ? 'is-on' : ''}`} />
        <span className="qa-lifecycle-name">{action.label}</span>
      </div>
      <div className="qa-lifecycle-controls">
        {loading && <span className="qa-spinner" />}
        <button
          type="button"
          role="switch"
          aria-checked={running}
          aria-label={`${action.label} ${running ? i18n('quickActions.stop', '停止') : i18n('quickActions.start', '启动')}`}
          className={`qa-toggle ${running ? 'is-on' : ''}`}
          disabled={loading}
          onClick={() => onExecute(action.action_id, !running)}
        >
          <span className="qa-toggle-thumb" />
        </button>
        <button
          type="button"
          className="qa-reload-btn"
          disabled={!running || loading}
          aria-label={`${i18n('quickActions.reload', '重载')} ${action.label}`}
          title={i18n('quickActions.reload', '重载')}
          onClick={() => onExecute(reloadId, null)}
        >
          ↻
        </button>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function ButtonControl({ action, loading, error, onExecute }: ControlProps) {
  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <div className="qa-row-widget">
        {loading && <span className="qa-spinner" />}
        <button
          type="button"
          className="qa-action-btn"
          disabled={action.disabled || loading}
          aria-label={action.label}
          onClick={() => onExecute(action.action_id, null)}
        >
          {i18n('quickActions.run', '执行')}
        </button>
        {error && <span className="qa-err" title={error}>!</span>}
      </div>
    </div>
  );
}

function InjectButton({ action, onInject }: ControlProps) {
  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <button type="button" className="qa-inject-btn" onClick={() => onInject(action.inject_text ?? '')} aria-label={action.label}>
        <span aria-hidden="true">📎</span> {i18n('quickActions.inject', '注入')}
      </button>
    </div>
  );
}

function NavButton({ action, onNavigate }: ControlProps) {
  return (
    <div className="qa-row">
      <div className="qa-row-info">
        <span className="qa-row-label">{action.label}</span>
        {action.description && <span className="qa-row-desc">{action.description}</span>}
      </div>
      <button type="button" className="qa-nav-btn" onClick={() => onNavigate(action.target ?? '', action.open_in ?? 'new_tab')} aria-label={action.label}>
        <span aria-hidden="true">↗</span> {i18n('quickActions.open', '打开')}
      </button>
    </div>
  );
}

function ActionControl(props: ControlProps) {
  const { action } = props;
  if (action.type === 'chat_inject') return <InjectButton {...props} />;
  if (action.type === 'navigation') return <NavButton {...props} />;
  switch (action.control) {
    case 'toggle':
    case 'entry_toggle':
      return <ToggleControl {...props} />;
    case 'plugin_lifecycle':
      return <PluginLifecycleControl {...props} />;
    case 'button':
      return <ButtonControl {...props} />;
    case 'dropdown':
      return <DropdownControl {...props} />;
    case 'slider':
      return <SliderControl {...props} />;
    case 'number':
      return <NumberControl {...props} />;
    default:
      return null;
  }
}

/* ================================================================== */
/*  Staggered list — items animate in one by one                       */
/* ================================================================== */

function StaggeredList({ actions, loadingMap, errorMap, onExecute, onInject, onNavigate }: {
  actions: ActionDescriptor[];
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  return (
    <>
      {actions.map((a, i) => (
        <div key={a.action_id} className="qa-stagger-item" style={{ animationDelay: `${i * 25}ms` }}>
          <ActionControl
            action={a}
            loading={!!loadingMap[a.action_id]}
            error={errorMap[a.action_id] ?? null}
            onExecute={onExecute}
            onInject={onInject}
            onNavigate={onNavigate}
          />
        </div>
      ))}
    </>
  );
}

/* ================================================================== */
/*  Function view — sub-tabs filter by action type                     */
/* ================================================================== */

function FunctionView({ actions, loadingMap, errorMap, onExecute, onInject, onNavigate }: {
  actions: ActionDescriptor[];
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  const [tab, setTab] = useState<FuncTab>('all');
  const filtered = useMemo(() => filterByFuncTab(actions, tab), [actions, tab]);
  const groups = useMemo(() => groupBy(filtered, 'category'), [filtered]);

  // Count badges
  const counts = useMemo(() => {
    const c: Record<FuncTab, number> = { all: actions.length, config: 0, lifecycle: 0, inject: 0 };
    for (const a of actions) {
      if (a.type === 'chat_inject' || a.type === 'navigation') c.inject++;
      else if (a.control === 'plugin_lifecycle' || a.control === 'entry_toggle' || a.control === 'button') c.lifecycle++;
      else if (a.type === 'instant') c.config++;
    }
    return c;
  }, [actions]);

  return (
    <div className="qa-func-view">
      {/* Sub-tab pills */}
      <div className="qa-pills">
        {FUNC_TABS.map(t => (
          <button
            key={t.id}
            type="button"
            className={`qa-pill ${tab === t.id ? 'is-active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {i18n(t.labelKey, t.labelFallback)}
            {counts[t.id] > 0 && <span className="qa-pill-badge">{counts[t.id]}</span>}
          </button>
        ))}
      </div>

      {/* Grouped content with crossfade */}
      <div className="qa-fade-content" key={tab}>
        {filtered.length === 0 ? (
          <div className="qa-empty">{i18n('quickActions.empty', '无可用操作')}</div>
        ) : (
          Array.from(groups.entries()).map(([cat, items]) => (
            <div key={cat} className="qa-group">
              <div className="qa-group-title">{cat}</div>
              <StaggeredList
                actions={items}
                loadingMap={loadingMap}
                errorMap={errorMap}
                onExecute={onExecute}
                onInject={onInject}
                onNavigate={onNavigate}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Plugin view — collapsible cards per plugin                         */
/* ================================================================== */

function PluginCard({ actions, expanded, onToggle, loadingMap, errorMap, onExecute, onInject, onNavigate }: {
  actions: ActionDescriptor[];
  expanded: boolean;
  onToggle: () => void;
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  const name = pluginDisplayName(actions);
  const running = isPluginRunning(actions);
  const contentRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);

  // Measure content height for smooth accordion
  useEffect(() => {
    if (expanded && contentRef.current) {
      setHeight(contentRef.current.scrollHeight);
    } else {
      setHeight(0);
    }
  }, [expanded, actions.length]);

  return (
    <div className={`qa-plugin-card ${expanded ? 'is-open' : ''}`}>
      <button type="button" className="qa-plugin-header" onClick={onToggle} aria-expanded={expanded}>
        <span className={`qa-dot ${running ? 'is-on' : ''}`} />
        <span className="qa-plugin-name">{name}</span>
        <span className="qa-plugin-count">{actions.length}</span>
        <span className={`qa-chevron ${expanded ? 'is-open' : ''}`} />
      </button>
      <div
        className="qa-plugin-body"
        style={{ height: height !== undefined ? `${height}px` : undefined }}
      >
        <div ref={contentRef} className="qa-plugin-body-inner">
          {expanded && (
            <StaggeredList
              actions={actions}
              loadingMap={loadingMap}
              errorMap={errorMap}
              onExecute={onExecute}
              onInject={onInject}
              onNavigate={onNavigate}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function PluginView({ actions, loadingMap, errorMap, onExecute, onInject, onNavigate }: {
  actions: ActionDescriptor[];
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  const byPlugin = useMemo(() => groupBy(actions, 'plugin_id'), [actions]);
  const pluginIds = useMemo(() => Array.from(byPlugin.keys()), [byPlugin]);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Auto-expand first plugin if only one
  useEffect(() => {
    if (pluginIds.length === 1 && expandedId === null) setExpandedId(pluginIds[0]);
  }, [pluginIds, expandedId]);

  return (
    <div className="qa-plugin-view">
      {pluginIds.length === 0 ? (
        <div className="qa-empty">{i18n('quickActions.empty', '无可用操作')}</div>
      ) : (
        pluginIds.map((pid, i) => (
          <div key={pid} className="qa-stagger-item" style={{ animationDelay: `${i * 40}ms` }}>
            <PluginCard
              actions={byPlugin.get(pid)!}
              expanded={expandedId === pid}
              onToggle={() => setExpandedId(expandedId === pid ? null : pid)}
              loadingMap={loadingMap}
              errorMap={errorMap}
              onExecute={onExecute}
              onInject={onInject}
              onNavigate={onNavigate}
            />
          </div>
        ))
      )}
    </div>
  );
}

/* ================================================================== */
/*  Main panel                                                         */
/* ================================================================== */

export default function QuickActionsPanel({
  actions,
  onExecuteAction,
  onInjectText,
  onNavigate,
  onClose,
}: QuickActionsPanelProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('plugin');
  const [search, setSearch] = useState('');
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});
  const [localActions, setLocalActions] = useState<ActionDescriptor[]>(actions);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setLocalActions(actions); }, [actions]);

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => document.addEventListener('mousedown', onClick), 0);
    return () => { clearTimeout(id); document.removeEventListener('mousedown', onClick); };
  }, [onClose]);

  const handleExecute = useCallback(async (actionId: string, value: unknown) => {
    setLoadingMap(m => ({ ...m, [actionId]: true }));
    setErrorMap(m => ({ ...m, [actionId]: null }));
    try {
      const updated = await onExecuteAction(actionId, value);
      if (updated) setLocalActions(prev => prev.map(a => (a.action_id === updated.action_id ? updated : a)));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMap(m => ({ ...m, [actionId]: msg }));
      setTimeout(() => setErrorMap(m => ({ ...m, [actionId]: null })), 3000);
    } finally {
      setLoadingMap(m => ({ ...m, [actionId]: false }));
    }
  }, [onExecuteAction]);

  const handleInject = useCallback((text: string) => { onInjectText(text); onClose(); }, [onInjectText, onClose]);
  const handleNavigate = useCallback((target: string, openIn: string) => { onNavigate(target, openIn); }, [onNavigate]);

  const visible = useMemo(
    () => localActions.filter(a => matchesSearch(a, search)),
    [localActions, search],
  );

  const sharedProps = { loadingMap, errorMap, onExecute: handleExecute, onInject: handleInject, onNavigate: handleNavigate };

  return (
    <div className="qa-panel" ref={panelRef} role="dialog" aria-label={i18n('quickActions.title', '快捷操作')}>
      {/* ── Header ── */}
      <div className="qa-header">
        <span className="qa-title">{i18n('quickActions.title', '快捷操作')}</span>
        <div className="qa-search-wrap">
          <span className="qa-search-icon" aria-hidden="true">🔍</span>
          <input
            type="text"
            className="qa-search"
            placeholder={i18n('quickActions.searchPlaceholder', '搜索...')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            aria-label={i18n('quickActions.searchAriaLabel', '搜索操作')}
          />
        </div>
      </div>

      {/* ── View mode switcher ── */}
      <div className="qa-mode-bar">
        {VIEW_MODES.map(m => (
          <button
            key={m.id}
            type="button"
            className={`qa-mode-btn ${viewMode === m.id ? 'is-active' : ''}`}
            onClick={() => setViewMode(m.id)}
            aria-pressed={viewMode === m.id}
          >
            <span className="qa-mode-icon">{m.icon}</span>
            {i18n(m.labelKey, m.labelFallback)}
          </button>
        ))}
      </div>

      {/* ── Content ── */}
      <div className="qa-content">
        <div className="qa-fade-content" key={viewMode}>
          {viewMode === 'function' ? (
            <FunctionView actions={visible} {...sharedProps} />
          ) : (
            <PluginView actions={visible} {...sharedProps} />
          )}
        </div>
      </div>
    </div>
  );
}
