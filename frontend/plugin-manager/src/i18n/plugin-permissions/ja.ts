import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'AI モデルを呼び出す（費用が発生する場合があります）',
    description: 'N.E.K.O のモデルゲートウェイ経由で AI モデルを呼び出すことがあり、利用料金が発生する場合があります。',
  },
  system_network_connect: {
    title: 'インターネットに接続',
    description: 'ネットワークサービスに接続することがあります。',
  },
  system_network_listen: {
    title: 'ネットワークポートを開く',
    description: 'このコンピューターでネットワークポートを開き、他のプログラムから接続できるようにすることがあります。',
  },
  system_fs_read: {
    title: 'ファイルを読み取る',
    description: '自身のデータフォルダー以外のファイルを読み取ることがあります。',
  },
  system_fs_write: {
    title: 'ファイルを書き込む',
    description: '自身のデータフォルダー以外のファイルを作成・変更することがあります。',
  },
  system_process_spawn: {
    title: 'プログラムを起動',
    description: 'このコンピューターで他のプログラムやコマンドを起動することがあります。',
  },
  system_env_read: {
    title: '環境変数を読み取る',
    description: 'このコンピューターの環境変数を読み取ることがあります。環境変数には API キーが含まれる場合があります。',
  },
}

export default labels
