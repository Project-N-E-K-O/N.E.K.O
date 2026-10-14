import { useLayoutEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import type { ChatWindowSchemaProps } from './message-schema';

export function useComposerRestore(
  draft: string,
  setDraft: Dispatch<SetStateAction<string>>,
  restore: ChatWindowSchemaProps['composerRestore'],
  acknowledged: ChatWindowSchemaProps['onComposerRestore'],
) {
  const appliedKey = useRef<string>();
  const acknowledgedKey = useRef<string>();
  useLayoutEffect(() => {
    if (!restore || restore.key === appliedKey.current) return;
    appliedKey.current = restore.key;
    setDraft(restore.text);
  }, [restore, setDraft]);
  useLayoutEffect(() => {
    if (!restore || appliedKey.current !== restore.key || draft !== restore.text ||
        acknowledgedKey.current === restore.key) return;
    acknowledgedKey.current = restore.key;
    acknowledged?.(restore.key);
  }, [restore, draft, acknowledged]);
}
