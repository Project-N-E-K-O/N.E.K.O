function normalizeChatLocale(locale?: string): 'zh-CN' | 'zh-TW' | 'default' {
  const normalized = (locale || '').trim().toLowerCase();
  if (!normalized) return 'default';
  if (
    normalized === 'zh-tw'
    || normalized.startsWith('zh-tw')
    || normalized === 'zh-hk'
    || normalized.startsWith('zh-hk')
    || normalized === 'zh-mo'
    || normalized.startsWith('zh-mo')
    || normalized.includes('hant')
  ) {
    return 'zh-TW';
  }
  if (
    normalized === 'zh-cn'
    || normalized.startsWith('zh-cn')
    || normalized.includes('hans')
    || normalized === 'zh'
    || normalized.startsWith('zh-')
  ) {
    return 'zh-CN';
  }
  return 'default';
}

function getRuntimeLocale(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as {
    i18next?: { language?: unknown };
    localStorage?: Storage;
    navigator?: Navigator;
  };
  if (typeof w.i18next?.language === 'string' && w.i18next.language) {
    return w.i18next.language;
  }
  try {
    const stored = w.localStorage?.getItem('i18nextLng');
    if (stored) return stored;
  } catch {}
  return w.navigator?.language;
}

export function getChatEmptyStateFallback(locale = getRuntimeLocale()): string {
  switch (normalizeChatLocale(locale)) {
    case 'zh-CN':
      return '现在开始跟我聊天吧！';
    case 'zh-TW':
      return '現在開始跟我聊天吧！';
    default:
      return 'Start chatting with me now!';
  }
}
