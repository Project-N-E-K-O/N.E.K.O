import { i18n } from './i18n';

export function openWatchTogether() {
    const config = (window as Window & { lanlan_config?: { lanlan_name?: string } }).lanlan_config;
    const url = new URL('/watch_together', window.location.origin);
    if (config?.lanlan_name) url.searchParams.set('lanlan_name', config.lanlan_name);
    window.open(url.href, '_blank', 'noopener');
}

export function WatchTogetherButton() {
  return <button type="button" onClick={openWatchTogether}>{i18n('watchTogether.title', 'Watch together')}</button>;
}
