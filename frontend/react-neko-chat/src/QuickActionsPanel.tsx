import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { i18n } from './i18n';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface ActionDescriptor {
  action_id: string;
  type: 'instant' | 'chat_inject' | 'navigation';
  label: string;
  description: string;
  category: string;
  plugin_id: string;
  control?: 'toggle' | 'dropdown' | 'number' | 'slider' | 'plugin_toggle' | 'entry_toggle';
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

/* ------------------------------------------------------------------ */
/*  Tabs                                                               */
/* ------------------------------------------------------------------ */

type TabId = 'home' | 'quick' | 'plugins' | 'inject';

interface TabDef {
  id: TabId;
  icon: string;
  labelKey: string;
  labelFallback: string;
}

const TABS: TabDef[] = [
  { id: 'home', icon: '🏠', labelKey: 'quickActions.tabHome', labelFallback: '首页' },
  { id: 'quick', icon: '⚡', labelKey: 'quickActions.tabQuickConfig', labelFallback: '快捷配置' },
  { id: 'plugins', icon: '🔌', labelKey: 'quickActions.tabPlugins', labelFallback: '插件管理' },
  { id: 'inject', icon: '📎', labelKey: 'quickActions.tabInject', labelFallback: '注入' },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function matchesSearch(action: ActionDescriptor, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return action.label.toLowerCase().includes(q) || action.description.toLowerCase().includes(q);
}

function groupByCategory(actions: ActionDescriptor[]): Map<string, ActionDescriptor[]> {
  const map = new Map<string, ActionDescriptor[]>();
  for (const a of actions) {
    const list = map.get(a.category);
    if (list) list.push(a);
    else map.set(a.category, [a]);
  }
  return map;
}

function filterForTab(actions: ActionDescriptor[], tab: TabId): ActionDescriptor[] {
  switch (tab) {
    case 'home':
      return actions;
    case 'quick':
      return actions.filter(
        a => a.type === 'instant' && a.control !== 'plugin_toggle' && a.control !== 'entry_toggle',
      );
    case 'plugins':
      return actions.filter(
        a => a.control === 'plugin_toggle' || a.control === 'entry_toggle',
      );
    case 'inject':
      return actions.filter(a => a.type === 'chat_inject' || a.type === 'navigation');
    default:
      return actions;
  }
}

/* ------------------------------------------------------------------ */
/*  Control renderers                                                  */
/* ------------------------------------------------------------------ */

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
  const isPluginToggle = action.control === 'plugin_toggle';
  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">
          {isPluginToggle && (
            <span
              className={`quick-actions-status-dot ${checked ? 'is-active' : ''}`}
              aria-hidden="true"
            />
          )}
          {action.label}
        </span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <div className="quick-actions-control-widget">
        {loading && <span className="quick-actions-spinner" aria-hidden="true" />}
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-label={action.label}
          className={`quick-actions-toggle ${checked ? 'is-on' : ''} ${action.control === 'entry_toggle' ? 'is-small' : ''}`}
          disabled={action.disabled || loading}
          onClick={() => onExecute(action.action_id, !checked)}
        >
          <span className="quick-actions-toggle-thumb" />
        </button>
        {error && <span className="quick-actions-error-tip" title={error}>!</span>}
      </div>
    </div>
  );
}

function DropdownControl({ action, loading, error, onExecute }: ControlProps) {
  const value = String(action.current_value ?? '');
  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">{action.label}</span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <div className="quick-actions-control-widget">
        {loading && <span className="quick-actions-spinner" aria-hidden="true" />}
        <select
          className="quick-actions-select"
          value={value}
          disabled={action.disabled || loading}
          aria-label={action.label}
          onChange={e => onExecute(action.action_id, e.target.value)}
        >
          {(action.options ?? []).map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        {error && <span className="quick-actions-error-tip" title={error}>!</span>}
      </div>
    </div>
  );
}

function SliderControl({ action, loading, error, onExecute }: ControlProps) {
  const numVal = Number(action.current_value ?? action.min ?? 0);
  const [local, setLocal] = useState(numVal);
  const committed = useRef(numVal);

  useEffect(() => {
    setLocal(numVal);
    committed.current = numVal;
  }, [numVal]);

  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">{action.label}</span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <div className="quick-actions-control-widget quick-actions-slider-widget">
        {loading && <span className="quick-actions-spinner" aria-hidden="true" />}
        <input
          type="range"
          className="quick-actions-slider"
          min={action.min ?? 0}
          max={action.max ?? 100}
          step={action.step ?? 1}
          value={local}
          disabled={action.disabled || loading}
          aria-label={action.label}
          onChange={e => setLocal(Number(e.target.value))}
          onMouseUp={() => {
            if (local !== committed.current) {
              committed.current = local;
              onExecute(action.action_id, local);
            }
          }}
          onTouchEnd={() => {
            if (local !== committed.current) {
              committed.current = local;
              onExecute(action.action_id, local);
            }
          }}
        />
        <span className="quick-actions-slider-value">{local}</span>
        {error && <span className="quick-actions-error-tip" title={error}>!</span>}
      </div>
    </div>
  );
}

function NumberControl({ action, loading, error, onExecute }: ControlProps) {
  const numVal = Number(action.current_value ?? 0);
  const [local, setLocal] = useState(numVal);
  const commitTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setLocal(numVal); }, [numVal]);

  const commit = useCallback(
    (v: number) => {
      if (commitTimeout.current) clearTimeout(commitTimeout.current);
      commitTimeout.current = setTimeout(() => onExecute(action.action_id, v), 400);
    },
    [action.action_id, onExecute],
  );

  const step = action.step ?? 1;
  const inc = () => {
    const next = Math.min(local + step, action.max ?? Infinity);
    setLocal(next);
    commit(next);
  };
  const dec = () => {
    const next = Math.max(local - step, action.min ?? -Infinity);
    setLocal(next);
    commit(next);
  };

  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">{action.label}</span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <div className="quick-actions-control-widget">
        {loading && <span className="quick-actions-spinner" aria-hidden="true" />}
        <div className="quick-actions-number-group">
          <button
            type="button"
            className="quick-actions-number-btn"
            disabled={action.disabled || loading}
            aria-label={`${action.label} decrease`}
            onClick={dec}
          >
            −
          </button>
          <input
            type="number"
            className="quick-actions-number-input"
            value={local}
            min={action.min}
            max={action.max}
            step={action.step}
            disabled={action.disabled || loading}
            aria-label={action.label}
            onChange={e => {
              const v = Number(e.target.value);
              setLocal(v);
              commit(v);
            }}
          />
          <button
            type="button"
            className="quick-actions-number-btn"
            disabled={action.disabled || loading}
            aria-label={`${action.label} increase`}
            onClick={inc}
          >
            +
          </button>
        </div>
        {error && <span className="quick-actions-error-tip" title={error}>!</span>}
      </div>
    </div>
  );
}

function ChatInjectButton({ action, onInject }: ControlProps) {
  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">{action.label}</span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <button
        type="button"
        className="quick-actions-inject-btn"
        onClick={() => onInject(action.inject_text ?? '')}
        aria-label={action.label}
      >
        <span className="quick-actions-inject-icon" aria-hidden="true">📎</span>
        {i18n('quickActions.inject', '注入')}
      </button>
    </div>
  );
}

function NavigationButton({ action, onNavigate }: ControlProps) {
  return (
    <div className="quick-actions-control-row">
      <div className="quick-actions-control-info">
        <span className="quick-actions-control-label">{action.label}</span>
        {action.description && (
          <span className="quick-actions-control-desc">{action.description}</span>
        )}
      </div>
      <button
        type="button"
        className="quick-actions-nav-btn"
        onClick={() => onNavigate(action.target ?? '', action.open_in ?? 'new_tab')}
        aria-label={action.label}
      >
        <span className="quick-actions-nav-icon" aria-hidden="true">↗</span>
        {i18n('quickActions.open', '打开')}
      </button>
    </div>
  );
}

function ActionControl(props: ControlProps) {
  const { action } = props;
  if (action.type === 'chat_inject') return <ChatInjectButton {...props} />;
  if (action.type === 'navigation') return <NavigationButton {...props} />;
  switch (action.control) {
    case 'toggle':
    case 'plugin_toggle':
    case 'entry_toggle':
      return <ToggleControl {...props} />;
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

/* ------------------------------------------------------------------ */
/*  Home tab — plugin cards                                            */
/* ------------------------------------------------------------------ */

function HomeView({
  groups,
  loadingMap,
  errorMap,
  onExecute,
  onInject,
  onNavigate,
}: {
  groups: Map<string, ActionDescriptor[]>;
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const categories = Array.from(groups.keys());

  return (
    <div className="quick-actions-home">
      <div className="quick-actions-card-grid">
        {categories.map(cat => (
          <button
            key={cat}
            type="button"
            className={`quick-actions-card ${expanded === cat ? 'is-expanded' : ''}`}
            onClick={() => setExpanded(expanded === cat ? null : cat)}
            aria-expanded={expanded === cat}
          >
            <span className="quick-actions-card-icon">🔌</span>
            <span className="quick-actions-card-name">{cat}</span>
            <span className="quick-actions-card-count">{groups.get(cat)?.length ?? 0}</span>
          </button>
        ))}
      </div>
      {expanded && groups.has(expanded) && (
        <div className="quick-actions-card-detail">
          <div className="quick-actions-section-title">{expanded}</div>
          {groups.get(expanded)!.map(a => (
            <ActionControl
              key={a.action_id}
              action={a}
              loading={!!loadingMap[a.action_id]}
              error={errorMap[a.action_id] ?? null}
              onExecute={onExecute}
              onInject={onInject}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Grouped list view (for quick / plugins / inject tabs)              */
/* ------------------------------------------------------------------ */

function GroupedListView({
  groups,
  loadingMap,
  errorMap,
  onExecute,
  onInject,
  onNavigate,
}: {
  groups: Map<string, ActionDescriptor[]>;
  loadingMap: Record<string, boolean>;
  errorMap: Record<string, string | null>;
  onExecute: (id: string, v: unknown) => void;
  onInject: (t: string) => void;
  onNavigate: (t: string, o: string) => void;
}) {
  return (
    <>
      {Array.from(groups.entries()).map(([cat, items]) => (
        <div key={cat} className="quick-actions-group">
          <div className="quick-actions-section-title">{cat}</div>
          {items.map(a => (
            <ActionControl
              key={a.action_id}
              action={a}
              loading={!!loadingMap[a.action_id]}
              error={errorMap[a.action_id] ?? null}
              onExecute={onExecute}
              onInject={onInject}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Main panel                                                         */
/* ------------------------------------------------------------------ */

export default function QuickActionsPanel({
  actions,
  onExecuteAction,
  onInjectText,
  onNavigate,
  onClose,
}: QuickActionsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>('home');
  const [search, setSearch] = useState('');
  const [loadingMap, setLoadingMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});
  const [localActions, setLocalActions] = useState<ActionDescriptor[]>(actions);
  const panelRef = useRef<HTMLDivElement>(null);

  // Sync external actions
  useEffect(() => { setLocalActions(actions); }, [actions]);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    // Use setTimeout to avoid the opening click from immediately closing
    const id = setTimeout(() => document.addEventListener('mousedown', onClick), 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener('mousedown', onClick);
    };
  }, [onClose]);

  // Execute action handler
  const handleExecute = useCallback(
    async (actionId: string, value: unknown) => {
      setLoadingMap(m => ({ ...m, [actionId]: true }));
      setErrorMap(m => ({ ...m, [actionId]: null }));
      try {
        const updated = await onExecuteAction(actionId, value);
        if (updated) {
          setLocalActions(prev =>
            prev.map(a => (a.action_id === updated.action_id ? updated : a)),
          );
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        setErrorMap(m => ({ ...m, [actionId]: msg }));
        // Auto-clear error after 3s
        setTimeout(() => setErrorMap(m => ({ ...m, [actionId]: null })), 3000);
      } finally {
        setLoadingMap(m => ({ ...m, [actionId]: false }));
      }
    },
    [onExecuteAction],
  );

  // Inject handler — close panel after inject
  const handleInject = useCallback(
    (text: string) => {
      onInjectText(text);
      onClose();
    },
    [onInjectText, onClose],
  );

  // Navigate handler
  const handleNavigate = useCallback(
    (target: string, openIn: string) => {
      onNavigate(target, openIn);
    },
    [onNavigate],
  );

  // Filtered + tab-scoped actions
  const visibleActions = useMemo(() => {
    const tabFiltered = filterForTab(localActions, activeTab);
    return tabFiltered.filter(a => matchesSearch(a, search));
  }, [localActions, activeTab, search]);

  const groups = useMemo(() => groupByCategory(visibleActions), [visibleActions]);

  const isEmpty = visibleActions.length === 0;

  return (
    <div className="quick-actions-panel" ref={panelRef} role="dialog" aria-label={i18n('quickActions.title', '快捷操作')}>
      {/* Header */}
      <div className="quick-actions-header">
        <span className="quick-actions-title">{i18n('quickActions.title', '快捷操作')}</span>
        <div className="quick-actions-search-wrap">
          <input
            type="text"
            className="quick-actions-search"
            placeholder={i18n('quickActions.searchPlaceholder', '搜索操作...')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            aria-label={i18n('quickActions.searchAriaLabel', '搜索操作')}
          />
          <span className="quick-actions-search-icon" aria-hidden="true">🔍</span>
        </div>
      </div>

      {/* Content */}
      <div className="quick-actions-content">
        {isEmpty ? (
          <div className="quick-actions-empty">
            {i18n('quickActions.empty', '无可用操作')}
          </div>
        ) : activeTab === 'home' ? (
          <HomeView
            groups={groups}
            loadingMap={loadingMap}
            errorMap={errorMap}
            onExecute={handleExecute}
            onInject={handleInject}
            onNavigate={handleNavigate}
          />
        ) : (
          <GroupedListView
            groups={groups}
            loadingMap={loadingMap}
            errorMap={errorMap}
            onExecute={handleExecute}
            onInject={handleInject}
            onNavigate={handleNavigate}
          />
        )}
      </div>

      {/* Tab bar */}
      <div className="quick-actions-tabs" role="tablist">
        {TABS.map(tab => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`quick-actions-tab ${activeTab === tab.id ? 'is-active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="quick-actions-tab-icon">{tab.icon}</span>
            <span className="quick-actions-tab-label">{i18n(tab.labelKey, tab.labelFallback)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
