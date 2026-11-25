import type { MarketFeedbackMessages } from './en-US'

const messages: MarketFeedbackMessages = {
  issuesTab: '問題の報告',
  noIssues: 'Issue はまだありません。',
  issueOpen: 'オープン',
  issueClosed: 'クローズ済み',
  backToIssues: 'Issue 一覧に戻る',
  closeIssue: 'Issue を閉じる',
  reopenIssue: 'Issue を再開する',
  updateIssueFailed: 'Issue を更新できませんでした。',
  reply: '返信',
  message: 'メッセージ',
  send: '送信',
  verifyEmail: '投稿する前にマーケットアカウントのメールアドレスを確認してください。',
  sendFailed: 'メッセージを送信できませんでした。',
  sentAccountChanged:
    '操作は完了しましたが、その間にマーケットのアカウントが切り替わりました。現在のアカウントを表示しています。',
  notSentSessionChanged:
    '送信されていません：マーケットのログイン状態が変わりました。下書きは残してあります。アカウントを確認してから送信してください。',
  draftMaybePostedByOtherAccount:
    'この内容は以前のアカウントですでに投稿されている可能性があります。もう一度送信する前に、上の会話を確認してください。',
  title: '自分のフィードバック',
  loginRequired: 'マーケットにログインすると、自分のプラグインへのフィードバックを確認できます。',
  kind: 'フィードバックの種類',
  allKinds: 'すべての種類',
  comment: 'コメント',
  issue: 'Issue',
  status: 'ステータス',
  allStatuses: 'すべてのステータス',
  unread: '未読',
  awaitingReply: '返信待ち',
  markAllRead: 'すべて既読にする',
  markRead: '既読にする',
  unlisted: '公開停止中 · 元のスレッドを開けません',
  open: 'マーケットで開く',
  empty: '条件に一致するフィードバックがありません。',
  loadFailed: 'フィードバックを読み込めませんでした。更新して再試行してください。',
  markFailed: '既読にできませんでした。',
  loadMore: 'さらに読み込む',
}

export default messages
