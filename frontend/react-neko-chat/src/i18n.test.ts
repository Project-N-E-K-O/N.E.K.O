import { afterEach, describe, expect, it } from 'vitest';
import en from '../../../static/locales/en.json';
import es from '../../../static/locales/es.json';
import ja from '../../../static/locales/ja.json';
import ko from '../../../static/locales/ko.json';
import pt from '../../../static/locales/pt.json';
import ru from '../../../static/locales/ru.json';
import zhCN from '../../../static/locales/zh-CN.json';
import zhTW from '../../../static/locales/zh-TW.json';
import { i18n } from './i18n';

const LOCALES = {
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  en,
  ja,
  ko,
  ru,
  es,
  pt,
} as const;

describe('react-neko-chat i18n locale keys (C12-001)', () => {
  const originalWindowT = (globalThis as { window?: { t?: (key: string) => string } }).window?.t;

  afterEach(() => {
    if (typeof window !== 'undefined') {
      if (originalWindowT === undefined) {
        delete (window as { t?: unknown }).t;
      } else {
        (window as { t?: unknown }).t = originalWindowT;
      }
    }
  });

  it('defines chat.composerOverflowMenu and chat.exportActionFailed across all 8 locales and resolves via i18n()', () => {
    for (const [lang, payload] of Object.entries(LOCALES)) {
      const chat = (payload as { chat: Record<string, string> }).chat;
      expect(typeof chat.composerOverflowMenu, `${lang} chat.composerOverflowMenu`).toBe('string');
      expect(chat.composerOverflowMenu.trim().length, `${lang} chat.composerOverflowMenu`).toBeGreaterThan(0);
      expect(typeof chat.exportActionFailed, `${lang} chat.exportActionFailed`).toBe('string');
      expect(chat.exportActionFailed.trim().length, `${lang} chat.exportActionFailed`).toBeGreaterThan(0);

      (window as unknown as { t: (key: string) => string }).t = (key: string) => {
        const resolved = key.split('.').reduce<unknown>(
          (acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined),
          payload,
        );
        return typeof resolved === 'string' ? resolved : key;
      };

      expect(i18n('chat.composerOverflowMenu', '更多工具')).toBe(chat.composerOverflowMenu);
      expect(i18n('chat.exportActionFailed', 'Export failed. Please try again.')).toBe(chat.exportActionFailed);
    }
  });
});
