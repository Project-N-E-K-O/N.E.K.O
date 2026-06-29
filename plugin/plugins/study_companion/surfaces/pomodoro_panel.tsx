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

  async function refresh() {
    setStatus(await callPlugin(props.api, 'study_pomodoro_status'));
  }
  async function act(entryId: string) {
    try {
      setStatus(await callPlugin(props.api, entryId));
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

  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.pomodoro_panel', 'Pomodoro')}</h1>
          <span>{pomodoroStateLabel(props, status.state)}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div><span>{text(props, 'ui.label.remaining', 'Remaining')}</span><strong>{formatSeconds(status.remaining_seconds)}</strong></div>
        <div><span>{text(props, 'ui.label.sessions', 'Sessions')}</span><strong>{status.session_count || 0}</strong></div>
        <div><span>{text(props, 'ui.label.mode', 'Mode')}</span><strong>{pomodoroModeLabel(props, status.mode)}</strong></div>
      </section>
      <div className="pomodoro-ring" data-mode={status.mode || 'focus'}>{formatSeconds(status.remaining_seconds)}</div>
      <div className="study-panel__actions">
        <button type="button" onClick={() => act('study_pomodoro_start')}>{text(props, 'ui.button.start', 'Start')}</button>
        <button type="button" onClick={() => act('study_pomodoro_pause')}>{text(props, 'ui.button.pause', 'Pause')}</button>
        <button type="button" onClick={() => act('study_pomodoro_resume')}>{text(props, 'ui.button.resume', 'Resume')}</button>
        <button type="button" onClick={() => act('study_pomodoro_stop')}>{text(props, 'ui.button.stop', 'Stop')}</button>
        <button type="button" onClick={() => act('study_pomodoro_skip_break')}>{text(props, 'ui.button.skip_break', 'Skip break')}</button>
      </div>
    </div>
  );
}
