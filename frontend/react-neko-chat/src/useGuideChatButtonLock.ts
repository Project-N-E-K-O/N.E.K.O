import { useEffect, useState } from 'react';

function readGuideChatButtonLock(): boolean {
  if (typeof document === 'undefined') return false;
  const body = document.body;
  return body?.classList.contains('yui-guide-standalone-input-shield-active') === true
    || body?.classList.contains('yui-guide-chat-buttons-disabled') === true;
}

export function useGuideChatButtonLock(): boolean {
  const [locked, setLocked] = useState(readGuideChatButtonLock);

  useEffect(() => {
    if (
      typeof document === 'undefined'
      || typeof MutationObserver === 'undefined'
      || !document.body
    ) return undefined;

    const sync = () => {
      const next = readGuideChatButtonLock();
      setLocked(current => (current === next ? current : next));
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['class'],
    });
    return () => observer.disconnect();
  }, []);

  return locked;
}
