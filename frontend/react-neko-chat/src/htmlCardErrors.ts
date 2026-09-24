import { i18n } from './i18n';

export function getCardErrorMessage(payload: unknown): string {
  if (typeof payload === 'string') return payload;
  if (payload && typeof payload === 'object') {
    const value = payload as Record<string, unknown>;
    if (value.code === 'plugin_card_action_timeout') {
      return i18n('chat.cardActionTimeout', 'Request timed out; the action may have completed.');
    }
    if (value.code === 'plugin_card_server_unavailable') {
      return i18n('chat.cardServerUnavailable', 'Plugin service is unavailable.');
    }
    return getCardErrorMessage(value.message ?? value.detail ?? value.error);
  }
  return i18n('chat.cardActionFailed', 'Plugin action failed');
}
