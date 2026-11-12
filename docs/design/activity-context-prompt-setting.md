# Screen-sharing context prompt preference

The proactive chat settings expose **Screen-sharing suggestions** independently
of the screen-sharing source. `activityContextPromptEnabled` is a global boolean,
with an absent value treated as `true` for existing installations. Turning it off
suppresses both work and entertainment dialogs without changing screen sharing,
privacy mode, proactive sources, or chat scheduling.

The existing `/api/config/conversation-settings` GET/POST contract validates and
persists the boolean. It retains revision/ETag conflict handling and partial-write
semantics. The frontend includes the key in boot hydration, reset defaults, local
storage, server writes, and cross-window synchronization.

The tracker checks persisted settings immediately before delivering a context
signal. Disabled signals are consumed, and preference-read errors suppress this
optional prompt. The frontend also checks the current preference before enqueue,
on actual display, and before applying an acceptance. Disabling clears deferred
signals and dismisses a visible prompt; a queued dialog is cancelled when its
queue slot opens. Enabling permits future signals, without replaying stale ones
or resetting the existing once-per-session limit.

Tests cover API round trips and malformed values, revision conflicts, partial
writes, backend delivery gating, local reload/server hydration, cross-window
changes, deferred/visible dialogs, and disable/accept races.
