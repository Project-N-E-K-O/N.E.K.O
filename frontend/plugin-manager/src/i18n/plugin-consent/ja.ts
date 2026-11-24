import labels from '../plugin-permissions/ja'
import { withPermissionLabels } from '../plugin-permissions/shared'
import type { PluginConsentMessages } from './en-US'

const messages: PluginConsentMessages = {
  title: '権限',
  dialogTitle: 'プラグイン機能の確認',
  introduction: 'このプラグインは実行中に以下の機能を使用します。確認すると起動できます。',
  analysisPending: '機能解析がまだ完了していません。もう一度起動をクリックすると直接起動します：{names}',
  incomplete: '機能解析は未完了ですが、確認して起動できます。',
  upgraded: 'アップグレード後に追加',
  pending: '要確認',
  decision: {
    approved: '同意済み',
    denied: '拒否済み',
  },
  banner: {
    confirm: '{n} 件の未確認の機能があります',
    upgradedConfirm: 'アップグレードで {n} 件の新しい機能が追加されました',
    capabilitiesDesc:
      'プラグインが会話や記憶を読み取り、本体へコンテンツを送信する可能性があります。確認後は、新しい機能が追加されるまで再確認されません。',
    incompleteTitle: '機能解析が完全に終了していません',
    incompleteDesc: '結果が不完全な可能性がありますが、同意して使用できます。',
    deniedDesc: '自動起動ではこのプラグインをスキップします。手動起動時は再度確認されます。',
    approvedAt: '{date} に確認',
    analyzingTitle: 'プラグイン機能を解析中…',
    analyzingDesc: '解析が完了すると結果が自動的に表示されます。',
  },
  approve: '同意',
  reject: '拒否',
  approveAndStart: '同意して起動',
  revoke: '確認を取り消す',
  revokeDecision: '決定を取り消す',
  evidenceToggle: 'コード位置（{n}）',
  meta: {
    analyzed: '解析日時 {date}',
    version: 'バージョン {version}',
    files: '{n} ファイル',
  },
  noticesTitle: '確認が必要なプラグイン機能があります',
  loading: '機能情報を読み込み中…',
  loadFailed: '機能情報を読み込めませんでした。',
  saveFailed: '確認を保存できませんでした。再試行してください。',
  unknown:
    '具体的なデータ利用はまだ不明です。SDK は会話や記憶の読み取り、本体へのコンテンツ送信に対応しています。',
  noneFound: '問題は見つかりませんでした：コードスキャンでは権限の使用も、静的に確認できないコードも見つかりませんでした。',
  retry: '再解析',
  findings: {
    usedTitle: 'このプラグインが使用する権限',
    usedBanner: 'このプラグインは {n} 件の権限を使用します',
    declared: '作者が宣言',
    detected: 'コード内で検出',
    blindSpotsTitle: '静的解析で確認できない箇所が {n} 件あります',
    blindSpotsDesc: 'これらの箇所はコードを動的に読み込む、リフレクションや生成したコードの実行を行う、または解析されていません。プラグインを実際に動かさない限り、何をするかは判断できません。',
    unlocated: 'プラグインの一部（特定のファイルなし）',
    partialTag: '一部確認不可',
    partialTagHint: '一部のコードは静的解析できません。詳細ページをご覧ください。',
  },
  errors: {
    sourceUnknown: 'このプラグインの現在のインストール元を確認できないため、付与済みの権限を守るために置き換えを中止しました。プラグインは変更されていません。プラグインをアンインストールして再インストールすると復旧できます。',
    readFailed: 'プラグインの権限記録を一時的に読み込めません。変更は行われていません。しばらくしてから再試行してください。',
    corrupt: 'プラグインの権限記録ファイル（plugin_permissions.json）が破損していて読み込めないため、操作を中止しました。N.E.K.O の設定フォルダーからこのファイルを移動して N.E.K.O を再起動してください。プラグインが権限を再度確認する場合があります。',
    unsupportedVersion: 'プラグインの権限記録は新しいバージョンの N.E.K.O で保存されたため、このバージョンでは読み込めず、操作を中止しました。N.E.K.O を最新版に更新してから再試行してください。',
    writeFailed: 'プラグインの権限記録を保存できませんでした。変更は行われていません。ディスクの空き容量と N.E.K.O 設定フォルダーへの書き込み権限を確認してから再試行してください。',
  },
  warnings: {
    budget_exceeded: '解析の作業上限に達しました。',
    source_too_large: 'ソースが解析サイズの上限を超えました。',
    source_unreadable: '一部のソースを解析できませんでした。',
    analysis_failed: 'バックグラウンド解析を完了できませんでした。',
  },
  capabilities: {
    conversations_read: {
      title: '会話の読み取り',
      description: '最近の発言や会話履歴を読み取り、会話や応答完了を監視する場合があります。',
    },
    memory_read: {
      title: '記憶の読み取り',
      description: '長期記憶やキャラクターの記憶、ペルソナを参照する場合があります。',
    },
    messages_push: {
      title: '本体へのコンテンツ送信',
      description: 'メッセージやカードの表示、応答への添付を行う場合があります。',
    },
  },
}

export default { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }
