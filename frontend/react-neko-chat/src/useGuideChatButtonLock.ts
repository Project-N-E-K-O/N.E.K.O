import { useSyncExternalStore } from 'react';

function readGuideChatButtonLock(): boolean {
  if (typeof document === 'undefined') return false;
  const body = document.body;
  return body?.classList.contains('yui-guide-standalone-input-shield-active') === true
    || body?.classList.contains('yui-guide-chat-buttons-disabled') === true;
}

function subscribeGuideChatButtonLock(onStoreChange: () => void): () => void {
  if (
    typeof document === 'undefined'
    || typeof MutationObserver === 'undefined'
    || !document.body
  ) return () => {};

  const observer = new MutationObserver(onStoreChange);
  observer.observe(document.body, {
    attributes: true,
    attributeFilter: ['class'],
  });
  return () => observer.disconnect();
}

const readGuideChatButtonLockOnServer = () => false;

export function useGuideChatButtonLock(): boolean {
  return useSyncExternalStore(
    subscribeGuideChatButtonLock,
    readGuideChatButtonLock,
    readGuideChatButtonLockOnServer,
  );
}
