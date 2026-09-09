import { afterEach, describe, expect, it } from 'vitest';
import en from '../../../static/locales/en.json';
import es from '../../../static/locales/es.json';
import ja from '../../../static/locales/ja.json';
import ko from '../../../static/locales/ko.json';
import pt from '../../../static/locales/pt.json';
import ru from '../../../static/locales/ru.json';
import zhCN from '../../../static/locales/zh-CN.json';
import zhTW from '../../../static/locales/zh-TW.json';
import { getCardErrorMessage } from './htmlCardErrors';

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).safeT;
  delete (window as unknown as Record<string, unknown>).t;
});

describe('HTML card errors', () => {
  it.each(Object.entries({ en, es, ja, ko, pt, ru, 'zh-CN': zhCN, 'zh-TW': zhTW }))(
    'localizes host proxy errors using the %s catalog', (_locale, catalog) => {
      const translations: Record<string, string> = catalog.chat;
      (window as unknown as Record<string, unknown>).t = (key: string) => translations[key.replace(/^chat\./, '')];
      expect(translations.cardActionTimeout).toBeTruthy();
      expect(translations.cardServerUnavailable).toBeTruthy();
      expect(getCardErrorMessage({ detail: { code: 'plugin_card_action_timeout' } }))
        .toBe(translations.cardActionTimeout);
      expect(getCardErrorMessage({ detail: { code: 'plugin_card_server_unavailable' } }))
        .toBe(translations.cardServerUnavailable);
    },
  );

  it.each([
    'Plugin stopped',
    { message: 'Plugin stopped' },
    { detail: { message: 'Plugin stopped' } },
    { error: { detail: 'Plugin stopped' } },
    { detail: { code: 'plugin_error', message: 'Plugin stopped' } },
  ])('preserves plugin error text from %j', payload => {
    expect(getCardErrorMessage(payload)).toBe('Plugin stopped');
  });

  it('keeps a localized generic fallback for unrecognized payloads', () => {
    (window as unknown as Record<string, unknown>).safeT = (key: string) => key === 'chat.cardActionFailed'
      ? zhCN.chat.cardActionFailed : key;
    expect(getCardErrorMessage({ detail: { code: 'unknown' } })).toBe('插件操作失败');
    expect(getCardErrorMessage(null)).toBe('插件操作失败');
  });

  it('provides readable host errors when translations are unavailable', () => {
    expect(getCardErrorMessage({ detail: { code: 'plugin_card_action_timeout' } }))
      .toBe('Request timed out; the action may have completed.');
    expect(getCardErrorMessage({ detail: { code: 'plugin_card_server_unavailable' } }))
      .toBe('Plugin service is unavailable.');
    expect(getCardErrorMessage(undefined)).toBe('Plugin action failed');
  });
});
