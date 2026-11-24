/**
 * Words of the Market feedback pages.
 * One module per language, all of one shape, so that a language pack carries
 * its own words only (see ../power).
 */

const messages = {
  issuesTab: 'Issue feedback',
  noIssues: 'No issues yet.',
  issueOpen: 'Open',
  issueClosed: 'Closed',
  backToIssues: 'Back to issues',
  closeIssue: 'Close issue',
  reopenIssue: 'Reopen issue',
  updateIssueFailed: 'Could not update the issue.',
  reply: 'Reply',
  message: 'Message',
  send: 'Send',
  verifyEmail: 'Verify your Market account email before posting.',
  sendFailed: 'Could not send the message.',
  sentAccountChanged:
    'Done, but the Market account changed in the meantime. Showing the current account now.',
  notSentSessionChanged:
    'Not sent: the Market sign-in changed. Your draft is kept; check the account and send again.',
  draftMaybePostedByOtherAccount:
    'This text may already have been posted from your previous account. Check the thread above before sending it again.',
  title: 'My feedback',
  loginRequired: 'Sign in to Market to see feedback for your plugins.',
  kind: 'Feedback type',
  allKinds: 'All types',
  comment: 'Comment',
  issue: 'Issue',
  status: 'Status',
  allStatuses: 'All statuses',
  unread: 'Unread',
  awaitingReply: 'Awaiting reply',
  markAllRead: 'Mark all as read',
  markRead: 'Mark as read',
  unlisted: 'Not listed · Original thread unavailable',
  open: 'Open in Market',
  empty: 'No feedback matches these filters.',
  loadFailed: 'Could not load feedback. Try refreshing.',
  markFailed: 'Could not mark feedback as read.',
  loadMore: 'Load more',
}

export type MarketFeedbackMessages = typeof messages
export default messages
