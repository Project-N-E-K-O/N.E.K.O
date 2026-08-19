import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS, formatError, pomodoroModeLabel, pomodoroStateLabel, text } from './study_surface_utils';

function formatSeconds(value: number) {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function PomodoroPanel(props: PluginSurfaceProps) {
  const [status, setStatus] = useState<any>({});
  const [error, setError] = useState('');
  const [focusMinutes, setFocusMinutes] = useState('25');
  const stateKey = String(status.state || 'idle');
  const modeKey = String(status.mode || 'focus');
  const isFocusing = stateKey === 'focusing';
  const isPaused = stateKey === 'paused';
  const isBreak = stateKey === 'short_break' || stateKey === 'long_break';
  const isRunning = isFocusing || isPaused || isBreak;
  const allowCustomDuration = status.config?.allow_custom_duration !== false;
  const stateLabel = pomodoroStateLabel(props, stateKey);
  const modeLabel = pomodoroModeLabel(props, modeKey);
  const remaining = formatSeconds(status.remaining_seconds);
  const selectedMinutes = Math.min(120, Math.max(1, Math.round(Number(focusMinutes) || 25)));
  const modeMinutes = modeKey === 'short_break'
    ? Number(status.config?.short_break_minutes || 5)
    : modeKey === 'long_break'
      ? Number(status.config?.long_break_minutes || 15)
      : Number(status.current_focus_session?.planned_minutes || selectedMinutes);
  const totalSeconds = Math.max(60, modeMinutes * 60);
  const displaySeconds = isRunning ? Number(status.remaining_seconds || 0) : selectedMinutes * 60;
  const progress = isRunning
    ? Math.min(1, Math.max(0, Number(status.remaining_seconds || 0) / totalSeconds))
    : 1;
  const progressOffset = 100 - progress * 100;

  async function refresh() {
    setStatus(await callPlugin(props.api, 'study_pomodoro_status'));
  }
  async function act(entryId: string, args: Record<string, unknown> = {}) {
    try {
      setStatus(await callPlugin(props.api, entryId, args));
      setError('');
    } catch (err) {
      setError(formatError(err));
    }
  }

  useEffect(() => {
    ensureBrandCSS();
    let disposed = false;
    let timeoutId = 0;
    const tick = async () => {
      try {
        await refresh();
        if (!disposed) setError('');
      } catch (err) {
        if (!disposed) setError(formatError(err));
      } finally {
        if (!disposed) timeoutId = window.setTimeout(() => void tick(), 1000);
      }
    };
    void tick();
    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
    };
  }, []);

  useEffect(() => {
    const configuredMinutes = Number(status.config?.focus_minutes);
    if (Number.isFinite(configuredMinutes) && configuredMinutes >= 1 && configuredMinutes <= 120) {
      setFocusMinutes(String(Math.round(configuredMinutes)));
    }
  }, [status.config?.focus_minutes]);

  function normalizedFocusMinutes() {
    return selectedMinutes;
  }

  return (
    <div
      className="study-panel surface-shell"
      data-surface="pomodoro-panel"
      data-state={stateKey}
      data-mode={modeKey}
    >
      <header className="study-panel__header">
        <div className="study-panel__title pomodoro-title">
          <span className="pomodoro-title__mark" aria-hidden="true">◷</span>
          <h1>{text(props, 'ui.surface.pomodoro_panel', 'Pomodoro')}</h1>
        </div>
        <span className="study-panel__status-chip" role="status" aria-live="polite">{stateLabel}</span>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="pomodoro-stage">
        <div className="pomodoro-ring" data-mode={modeKey}>
          <svg className="pomodoro-ring__progress" viewBox="0 0 260 260" aria-hidden="true">
            <circle className="pomodoro-ring__ticks" cx="130" cy="130" r="123" pathLength="100" />
            <circle className="pomodoro-ring__track" cx="130" cy="130" r="112" pathLength="100" />
            <circle className="pomodoro-ring__value" cx="130" cy="130" r="112" pathLength="100" strokeDasharray="100" strokeDashoffset={progressOffset} />
          </svg>
          <div className="pomodoro-ring__core">
            <span className="pomodoro-ring__mode">{modeLabel}</span>
            <strong className="pomodoro-ring__time">{formatSeconds(displaySeconds)}</strong>
            <span className="pomodoro-ring__state">{stateLabel}</span>
          </div>
        </div>
      </section>
      <label className="pomodoro-duration">
        <span>{text(props, 'ui.label.focus_minutes', 'Focus minutes')}</span>
        <input
          type="number"
          min="1"
          max="120"
          step="1"
          inputMode="numeric"
          value={focusMinutes}
          disabled={isRunning || !allowCustomDuration}
          onChange={(event) => setFocusMinutes(event.currentTarget.value)}
          onBlur={() => setFocusMinutes(String(normalizedFocusMinutes()))}
        />
        <small>1–120</small>
      </label>
      <section className="study-panel__state pomodoro-metrics">
        <div><span>{text(props, 'ui.label.remaining', 'Remaining')}</span><strong>{remaining}</strong></div>
        <div><span>{text(props, 'ui.label.sessions', 'Sessions')}</span><strong>{status.session_count || 0}</strong></div>
        <div><span>{text(props, 'ui.label.mode', 'Mode')}</span><strong>{modeLabel}</strong></div>
      </section>
      <div className="study-panel__actions study-panel__actions--primary pomodoro-actions">
        <button className={!isRunning ? 'pomodoro-action is-primary' : 'pomodoro-action'} data-action="start" type="button" disabled={isRunning} onClick={() => act('study_pomodoro_start', allowCustomDuration ? { focus_minutes: normalizedFocusMinutes() } : {})}>{text(props, 'ui.button.start', 'Start')}</button>
        <button className={isFocusing ? 'pomodoro-action is-primary' : 'pomodoro-action'} data-action="pause" type="button" disabled={!isFocusing} onClick={() => act('study_pomodoro_pause')}>{text(props, 'ui.button.pause', 'Pause')}</button>
        <button className={isPaused ? 'pomodoro-action is-primary' : 'pomodoro-action'} data-action="resume" type="button" disabled={!isPaused} onClick={() => act('study_pomodoro_resume')}>{text(props, 'ui.button.resume', 'Resume')}</button>
        <button className="pomodoro-action is-danger" data-action="stop" type="button" disabled={!isRunning} onClick={() => act('study_pomodoro_stop')}>{text(props, 'ui.button.stop', 'Stop')}</button>
        <button className={isBreak ? 'pomodoro-action is-primary' : 'pomodoro-action'} data-action="skip-break" type="button" disabled={!isBreak} onClick={() => act('study_pomodoro_skip_break')}>{text(props, 'ui.button.skip_break', 'Skip break')}</button>
      </div>
    </div>
  );
}
