import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from './i18n';

const LOCALES_DIR = path.resolve(__dirname, '../../../static/locales');
const LANGUAGES = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'] as const;

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
    for (const lang of LANGUAGES) {
      const payload = JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, `${lang}.json`), 'utf8')) as {
        chat: Record<string, string>;
      };
      expect(typeof payload.chat.composerOverflowMenu, `${lang} chat.composerOverflowMenu`).toBe('string');
      expect(payload.chat.composerOverflowMenu.trim().length, `${lang} chat.composerOverflowMenu`).toBeGreaterThan(0);
      expect(typeof payload.chat.exportActionFailed, `${lang} chat.exportActionFailed`).toBe('string');
      expect(payload.chat.exportActionFailed.trim().length, `${lang} chat.exportActionFailed`).toBeGreaterThan(0);

      (window as unknown as { t: (key: string) => string }).t = (key: string) => {
        const resolved = key.split('.').reduce<unknown>(
          (acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined),
          payload,
        );
        return typeof resolved === 'string' ? resolved : key;
      };

      expect(i18n('chat.composerOverflowMenu', '更多工具')).toBe(payload.chat.composerOverflowMenu);
      expect(i18n('chat.exportActionFailed', 'Export failed. Please try again.')).toBe(payload.chat.exportActionFailed);
    }
  });
});
