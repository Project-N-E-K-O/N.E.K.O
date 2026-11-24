import bindingErrors from '../model-binding-errors/ja'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: 'モデル API の割り当て',
  manage: 'スロットと使用量の管理',
  description: 'プラグインが宣言した用途ごとに、保存済みモデルスロットを選択します。変更はすぐに保存されます。',
  ready: '必須のモデル用途はすべて割り当て済みです',
  notReady: '割り当てが必要な必須のモデル用途があります',
  readinessHint: '必須の割り当てが揃っているかを示します。接続先の利用可否は確認せず、プラグインの起動も制御しません。',
  boundSummary: '{bound}/{total} 割り当て済み',
  collapse: '折りたたむ',
  expand: '展開',
  required: '必須',
  optional: '任意',
  unbound: '未割り当て',
  incompatible: '必要な機能が不足',
  noSlots: 'モデルスロットがありません。Plugin API で追加してから更新してください。',
  loadFailed: 'モデルの割り当てを読み込めませんでした。再試行してください。',
  saveFailed: '割り当てを保存できませんでした。以前の割り当てを表示しています。',
  capabilities: { text: 'テキスト', image_input: '画像入力', tool_calling: 'ツール呼び出し', streaming: 'ストリーミング' },
  status: { bound: '割り当て済み', unbound: 'スロット未選択', incompatible: '割り当て済みのスロットがこの用途の要件を満たしていません' },
}

export default messages
