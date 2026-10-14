# Desktop chat state transfer

Independent compact/full WebViews may mirror a single character connection.
Copying visible messages alone does not preserve the in-flight response: its
continuation refers to adapter-owned buffers, queue owners and message IDs.
Pending composer rollback data also belongs to the shared chat controller.

`window.appChat.captureDesktopState()` returns a JSON-serializable, versioned
snapshot of that runtime data. Desktop providers must transport it opaquely.
They must not rebuild virtual references or manipulate queue ownership flags.

`window.appChat.restoreDesktopState(snapshot, options)` restores the snapshot
and returns a Promise. `options.draft` replaces the ordinary composer draft,
including an empty draft. The Promise resolves after the React composer has
committed that restoration. `options.restoreResponse = false` preserves a
receiver's newer response state while still restoring the composer handoff.
This is useful when the provider's frame watermark proves the receiver has
already processed newer response frames.

The provider remains responsible for transporting message history, pending
attachments through `appButtons`, the draft, and its frame watermark. Restore
the message history before calling this interface, defer subsequent frames
while its Promise is pending, and acknowledge the corresponding handoff
sequence only after the Promise resolves. Superseded acknowledgements must
not reveal a native window. Model/audio/real WebSocket ownership remains in
the desktop's Pet surface.

The React host exposes `captureComposerTransferState()` and
`restoreComposerTransferState(snapshot, draft)` for its pending rollback map.
The stream adapter delegates to these methods. A response rejected with
`RESPONSE_TOO_LONG` therefore restores its original draft after a window switch;
ordinary rollback continues to preserve text the user has typed since sending.
`react-chat-window:composer-rollback` publishes `{requestId, text}` so providers
can keep their draft mirror current when React restores text programmatically.

React's `composerRestore: {key, text}` is applied once per key, and
`onComposerRestore(key)` acknowledges the committed value. Compact and full
surfaces share this behavior through `useComposerRestore`. A new key must be
acknowledged even if the text already equals the local value.

Verification: `App.test.tsx` covers draft replacement, empty replacement,
unchanged empty text, key deduplication and ordinary rollback on both surfaces.
The Tauri provider's upstream integration probe additionally exercises image
handoff/removal/sending, continuation of a copied assistant bubble and rejected
submission rollback using the real shared pages.
