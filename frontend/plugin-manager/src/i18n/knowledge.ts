/**
 * Knowledge Manager copy, kept out of the locale bundles.
 *
 * The locale modules are part of the entry chunk, which has a cold-start budget
 * (src/e2e/payload-budget.e2e.ts). Only the lazily loaded Knowledge Manager
 * view needs these strings, so it merges them into i18n when it loads.
 */
import { i18n, type AppLocale } from '.'

const zhCN = {
  marketConnected: '知识包市场已经接通；订阅后会先校验，再写入本地知识库。',
  openMarket: '浏览知识库市场', marketUnavailable: '插件市场当前不可用',
  loginRequired: '请先在插件管理页面登录市场账号', marketPairFailed: '无法与本机 N.E.K.O 安全配对',
  title: '知识库管理', subtitle: '管理本地公共知识、数据包和对话命中情况',
  marketNotice: '知识包市场协议接口已预留；当前可管理内置知识库和本地数据包。',
  overview: '总览', ready: '正常', degraded: '异常', entries: '词条', disabled: '已禁用', packs: '数据包',
  autoContext: '参与自动搭话', catalog: '词条管理', searchPlaceholder: '搜索标题、别名、摘要或正文',
  term: '词条', summary: '摘要', source: '来源', actions: '操作', details: '详情', restore: '恢复', disable: '禁用',
  previous: '上一页', next: '下一页', packId: '数据包 ID', materialType: '内容类型', subscription: '订阅来源', localImport: '本地导入',
  importPack: '导入知识包', diagnostics: '最近命中', time: '时间', matchMode: '匹配方式',
  delivered: '已递卡', yes: '是', no: '否', terms: '识别词', titleMatch: '词条标题', aliasTerms: '别名', recognitionPhrases: '识别短语', tags: '标签', content: '正文',
  indexStatus: '向量索引', indexOrigin: '索引来源', indexTrust: '信任状态', indexValidation: '校验状态', indexFallback: '降级方式',
  localEmbeddingState: '本机维护状态', allowLocalEmbedding: '允许本机维护向量', enabled: '已启用', disabledState: '已禁用',
  packageStatus: '知识包运行态', sourceDistribution: '来源分布', otherSources: '其他', inactivePacks: '关闭', needsAttention: '需关注', materialMix: '知识包类型', noPacks: '暂无知识包',
  vectorReadyPercent: '已就绪 {percent}%', noVectorChunks: '暂无向量分块', vectorBuilding: '构建中', vectorComplete: '全部就绪', vectorWaiting: '等待构建',
  indexPolicyHint: '可能占用本机 CPU 和内存；关闭后，可信索引不可用时将降级为 BM25。',
  loadFailed: '知识库数据加载失败', operationFailed: '知识库操作失败', importSuccess: '知识包导入成功', importQueued: '知识包正在后台静默处理，准备完成后会自动载入', importStillProcessing: '知识包仍在后台处理，请稍后刷新查看状态',
  degradedJobs: '已隔离的导入任务', degradedJobHint: '损坏的任务无法继续。核对 ID 后丢弃对应任务，即可恢复新的导入。', discardJobConfirm: '确定丢弃隔离任务 {name} 吗？只会删除它的暂存文件。', jobDiscarded: '隔离任务已丢弃',
  importingPacks: '正在导入知识包', importingPackHint: '正在校验并准备本地知识库，可离开此页面，稍后回来查看。',
  importStateQueued: '等待处理', importStateValidating: '正在校验', importStateBuildingFts: '正在建立词法索引', importStateVerifyingIndex: '正在检查向量索引', importStateEmbedding: '正在准备向量',
  importProgressMeta: '已完成 {percent}% · {entries} 词条 · {chunks} 分块', importPreparingMeta: '{entries} 词条 · {chunks} 分块',
  invalidPack: '知识包格式无效', importTooLarge: '知识包超过 10 MiB 大小限制', removeConfirm: '确定移除知识包 {name} 吗？'
}

const zhTW = {
  marketConnected: '知識包市集已接通；訂閱後會先驗證，再寫入本機知識庫。',
  openMarket: '瀏覽知識庫市集', marketUnavailable: '外掛市集目前無法使用',
  loginRequired: '請先在外掛管理頁面登入市集帳號', marketPairFailed: '無法與本機 N.E.K.O 安全配對',
  title: '知識庫管理', subtitle: '管理本機公共知識、資料包與對話命中情況',
  marketNotice: '知識包市集協議介面已預留；目前可管理內建知識庫與本機資料包。',
  overview: '總覽', ready: '正常', degraded: '異常', entries: '詞條', disabled: '已停用', packs: '資料包',
  autoContext: '參與自動搭話', catalog: '詞條管理', searchPlaceholder: '搜尋標題、別名、摘要或正文',
  term: '詞條', summary: '摘要', source: '來源', actions: '操作', details: '詳情', restore: '恢復', disable: '停用',
  previous: '上一頁', next: '下一頁', packId: '資料包 ID', materialType: '內容類型', subscription: '訂閱來源', localImport: '本機匯入',
  importPack: '匯入知識包', diagnostics: '最近命中', time: '時間', matchMode: '匹配方式',
  delivered: '已遞卡', yes: '是', no: '否', terms: '識別詞', titleMatch: '詞條標題', aliasTerms: '別名', recognitionPhrases: '識別短語', tags: '標籤', content: '正文',
  indexStatus: '向量索引', indexOrigin: '索引來源', indexTrust: '信任狀態', indexValidation: '驗證狀態', indexFallback: '降級方式',
  localEmbeddingState: '本機維護狀態', allowLocalEmbedding: '允許本機維護向量', enabled: '已啟用', disabledState: '已停用',
  packageStatus: '知識包運行態', sourceDistribution: '來源分布', otherSources: '其他', inactivePacks: '關閉', needsAttention: '需關注', materialMix: '知識包類型', noPacks: '暫無知識包',
  vectorReadyPercent: '已就緒 {percent}%', noVectorChunks: '暫無向量分塊', vectorBuilding: '建構中', vectorComplete: '全部就緒', vectorWaiting: '等待建構',
  indexPolicyHint: '可能使用本機 CPU 與記憶體；停用後，可信索引不可用時將降級為 BM25。',
  loadFailed: '知識庫資料載入失敗', operationFailed: '知識庫操作失敗', importSuccess: '知識包匯入成功', importQueued: '知識包正在背景靜默處理，準備完成後會自動載入', importStillProcessing: '知識包仍在背景處理，請稍後重新整理查看狀態',
  degradedJobs: '已隔離的匯入工作', degradedJobHint: '損壞的工作無法繼續。核對 ID 後丟棄對應工作，即可恢復新的匯入。', discardJobConfirm: '確定丟棄隔離工作 {name} 嗎？只會刪除它的暫存檔案。', jobDiscarded: '隔離工作已丟棄',
  importingPacks: '正在匯入知識包', importingPackHint: '正在校驗並準備本機知識庫，可離開此頁面，稍後回來查看。',
  importStateQueued: '等待處理', importStateValidating: '正在校驗', importStateBuildingFts: '正在建立詞法索引', importStateVerifyingIndex: '正在檢查向量索引', importStateEmbedding: '正在準備向量',
  importProgressMeta: '已完成 {percent}% · {entries} 詞條 · {chunks} 分塊', importPreparingMeta: '{entries} 詞條 · {chunks} 分塊',
  invalidPack: '知識包格式無效', importTooLarge: '知識包超過 10 MiB 大小限制', removeConfirm: '確定移除知識包 {name} 嗎？'
}

const enUS = {
  marketConnected: 'The knowledge market is connected. Packages are verified before local installation.',
  openMarket: 'Browse knowledge market', marketUnavailable: 'The plugin market is unavailable',
  loginRequired: 'Sign in to the market from the Plugins page first', marketPairFailed: 'Could not securely pair with the local N.E.K.O client',
  title: 'Knowledge Manager', subtitle: 'Manage local public knowledge, data packs, and conversation matches',
  marketNotice: 'The knowledge-market protocol socket is reserved. The unified public knowledge database and local data packs are available now.',
  overview: 'Overview', ready: 'Ready', degraded: 'Degraded', entries: 'Entries', disabled: 'Disabled', packs: 'Data packs',
  autoContext: 'Use in automatic conversation', catalog: 'Catalog', searchPlaceholder: 'Search titles, aliases, summaries, or content',
  term: 'Entry', summary: 'Summary', source: 'Source', actions: 'Actions', details: 'Details', restore: 'Restore', disable: 'Disable',
  previous: 'Previous', next: 'Next', packId: 'Pack ID', materialType: 'Content type', subscription: 'Subscription', localImport: 'Local import',
  importPack: 'Import knowledge pack', diagnostics: 'Recent matches', time: 'Time', matchMode: 'Match mode',
  delivered: 'Card delivered', yes: 'Yes', no: 'No', terms: 'Recognition terms', titleMatch: 'Entry title', aliasTerms: 'Aliases', recognitionPhrases: 'Recognition phrases', tags: 'Tags', content: 'Content',
  indexStatus: 'Vector index', indexOrigin: 'Origin', indexTrust: 'Trust', indexValidation: 'Validation', indexFallback: 'Fallback',
  localEmbeddingState: 'Local maintenance', allowLocalEmbedding: 'Allow local vector maintenance', enabled: 'Enabled', disabledState: 'Disabled',
  packageStatus: 'Package runtime', sourceDistribution: 'Source distribution', otherSources: 'Other', inactivePacks: 'Off', needsAttention: 'Needs attention', materialMix: 'Pack type', noPacks: 'No knowledge packs',
  vectorReadyPercent: '{percent}% ready', noVectorChunks: 'No vector chunks', vectorBuilding: 'Building', vectorComplete: 'Fully ready', vectorWaiting: 'Waiting to build',
  indexPolicyHint: 'May use local CPU and memory. If disabled, an unavailable trusted index falls back to BM25.',
  loadFailed: 'Failed to load knowledge data', operationFailed: 'Knowledge operation failed', importSuccess: 'Knowledge pack imported', importQueued: 'Knowledge pack is being prepared quietly and will appear when ready', importStillProcessing: 'The knowledge pack is still processing. Refresh later to check its status.',
  degradedJobs: 'Quarantined imports', degradedJobHint: 'Damaged jobs cannot continue. Review the ID, then discard one to unblock new imports.', discardJobConfirm: 'Discard quarantined job {name}? Only its staged files will be removed.', jobDiscarded: 'Quarantined job discarded',
  importingPacks: 'Importing knowledge packs', importingPackHint: 'The local knowledge base is being verified and prepared. You can leave this page and return later.',
  importStateQueued: 'Waiting', importStateValidating: 'Validating', importStateBuildingFts: 'Building lexical index', importStateVerifyingIndex: 'Checking vector index', importStateEmbedding: 'Preparing vectors',
  importProgressMeta: '{percent}% complete · {entries} entries · {chunks} chunks', importPreparingMeta: '{entries} entries · {chunks} chunks',
  invalidPack: 'Invalid knowledge pack', importTooLarge: 'Knowledge pack exceeds the 10 MiB limit', removeConfirm: 'Remove knowledge pack {name}?'
}

const ja = {
  marketConnected: 'ナレッジマーケットに接続しました。パッケージは検証後にローカルへ保存されます。',
  openMarket: 'ナレッジマーケットを見る', marketUnavailable: 'プラグインマーケットを利用できません',
  loginRequired: '先にプラグイン管理画面でマーケットにログインしてください', marketPairFailed: 'ローカルの N.E.K.O と安全にペアリングできませんでした',
  title: 'ナレッジ管理', subtitle: 'ローカル公開知識、データパック、会話の一致履歴を管理します',
  marketNotice: 'ナレッジ市場プロトコルの接続口は予約済みです。現在は統合公開ナレッジベースとローカルパックを管理できます。',
  overview: '概要', ready: '正常', degraded: '異常', entries: '項目', disabled: '無効', packs: 'データパック',
  autoContext: '自動会話で使用', catalog: '項目管理', searchPlaceholder: 'タイトル、別名、要約、本文を検索',
  term: '項目', summary: '要約', source: '出典', actions: '操作', details: '詳細', restore: '復元', disable: '無効化',
  previous: '前へ', next: '次へ', packId: 'パック ID', materialType: 'コンテンツ種別', subscription: '購読元', localImport: 'ローカル導入',
  importPack: 'ナレッジパックを導入', diagnostics: '最近の一致', time: '時刻', matchMode: '一致方式',
  delivered: 'カード送信', yes: 'はい', no: 'いいえ', terms: '認識語', titleMatch: '項目タイトル', aliasTerms: '別名', recognitionPhrases: '認識フレーズ', tags: 'タグ', content: '本文',
  indexStatus: 'ベクトル索引', indexOrigin: '索引元', indexTrust: '信頼状態', indexValidation: '検証状態', indexFallback: 'フォールバック',
  localEmbeddingState: 'ローカル保守状態', allowLocalEmbedding: 'ローカルでのベクトル保守を許可', enabled: '有効', disabledState: '無効',
  packageStatus: 'パック稼働状態', sourceDistribution: '出典分布', otherSources: 'その他', inactivePacks: 'オフ', needsAttention: '要確認', materialMix: 'パック種別', noPacks: 'ナレッジパックはありません',
  vectorReadyPercent: '{percent}% 準備完了', noVectorChunks: 'ベクトルチャンクなし', vectorBuilding: '構築中', vectorComplete: 'すべて準備完了', vectorWaiting: '構築待ち',
  indexPolicyHint: 'ローカルの CPU とメモリを使用する場合があります。無効時は信頼済み索引が利用できなければ BM25 にフォールバックします。',
  loadFailed: 'ナレッジデータを読み込めません', operationFailed: 'ナレッジ操作に失敗しました', importSuccess: 'ナレッジパックを導入しました', importQueued: 'ナレッジパックをバックグラウンドで準備中です。完了後に自動で利用可能になります', importStillProcessing: 'ナレッジパックは処理中です。後でもう一度更新して状態を確認してください。',
  degradedJobs: '隔離されたインポート', degradedJobHint: '破損したジョブは続行できません。ID を確認して破棄すると、新しいインポートを再開できます。', discardJobConfirm: '隔離ジョブ {name} を破棄しますか？ステージングファイルのみ削除されます。', jobDiscarded: '隔離ジョブを破棄しました',
  importingPacks: 'ナレッジパックをインポート中', importingPackHint: 'ローカルナレッジベースを検証して準備しています。このページを離れて後で戻ることができます。',
  importStateQueued: '待機中', importStateValidating: '検証中', importStateBuildingFts: '字句インデックスを作成中', importStateVerifyingIndex: 'ベクトルインデックスを確認中', importStateEmbedding: 'ベクトルを準備中',
  importProgressMeta: '{percent}% 完了 · {entries} 件 · {chunks} チャンク', importPreparingMeta: '{entries} 件 · {chunks} チャンク',
  invalidPack: 'ナレッジパックが無効です', importTooLarge: 'ナレッジパックが 10 MiB の上限を超えています', removeConfirm: 'ナレッジパック {name} を削除しますか？'
}

const ko = {
  marketConnected: '지식 마켓이 연결되었습니다. 패키지를 검증한 뒤 로컬에 설치합니다.',
  openMarket: '지식 마켓 둘러보기', marketUnavailable: '플러그인 마켓을 사용할 수 없습니다',
  loginRequired: '먼저 플러그인 관리 페이지에서 마켓에 로그인하세요', marketPairFailed: '로컬 N.E.K.O 클라이언트와 안전하게 페어링할 수 없습니다',
  title: '지식 저장소 관리', subtitle: '로컬 공개 지식, 데이터 팩, 대화 일치 기록을 관리합니다',
  marketNotice: '지식 팩 마켓 프로토콜 연결 지점이 준비되었습니다. 현재는 통합 공용 지식베이스와 로컬 팩을 관리할 수 있습니다.',
  overview: '개요', ready: '정상', degraded: '오류', entries: '항목', disabled: '비활성', packs: '데이터 팩',
  autoContext: '자동 대화에 사용', catalog: '항목 관리', searchPlaceholder: '제목, 별칭, 요약 또는 본문 검색',
  term: '항목', summary: '요약', source: '출처', actions: '작업', details: '상세', restore: '복원', disable: '비활성화',
  previous: '이전', next: '다음', packId: '팩 ID', materialType: '콘텐츠 유형', subscription: '구독 출처', localImport: '로컬 가져오기',
  importPack: '지식 팩 가져오기', diagnostics: '최근 일치', time: '시간', matchMode: '일치 방식',
  delivered: '카드 전달', yes: '예', no: '아니요', terms: '인식어', titleMatch: '항목 제목', aliasTerms: '별칭', recognitionPhrases: '인식 문구', tags: '태그', content: '본문',
  indexStatus: '벡터 인덱스', indexOrigin: '인덱스 출처', indexTrust: '신뢰 상태', indexValidation: '검증 상태', indexFallback: '대체 방식',
  localEmbeddingState: '로컬 유지 상태', allowLocalEmbedding: '로컬 벡터 유지 허용', enabled: '활성화', disabledState: '비활성화',
  packageStatus: '지식 팩 실행 상태', sourceDistribution: '출처 분포', otherSources: '기타', inactivePacks: '꺼짐', needsAttention: '확인 필요', materialMix: '팩 유형', noPacks: '지식 팩 없음',
  vectorReadyPercent: '{percent}% 준비됨', noVectorChunks: '벡터 청크 없음', vectorBuilding: '구축 중', vectorComplete: '모두 준비됨', vectorWaiting: '구축 대기 중',
  indexPolicyHint: '로컬 CPU와 메모리를 사용할 수 있습니다. 끄면 신뢰할 수 있는 인덱스를 사용할 수 없을 때 BM25로 대체됩니다.',
  loadFailed: '지식 데이터를 불러오지 못했습니다', operationFailed: '지식 작업에 실패했습니다', importSuccess: '지식 팩을 가져왔습니다', importQueued: '지식 팩을 백그라운드에서 준비 중이며 완료되면 자동으로 사용할 수 있습니다', importStillProcessing: '지식 팩이 아직 처리 중입니다. 나중에 새로고침하여 상태를 확인하세요.',
  degradedJobs: '격리된 가져오기 작업', degradedJobHint: '손상된 작업은 계속할 수 없습니다. ID를 확인한 뒤 폐기하면 새 가져오기를 다시 시작할 수 있습니다.', discardJobConfirm: '격리 작업 {name}을(를) 폐기할까요? 스테이징 파일만 삭제됩니다.', jobDiscarded: '격리 작업을 폐기했습니다',
  importingPacks: '지식 팩 가져오는 중', importingPackHint: '로컬 지식 베이스를 확인하고 준비하는 중입니다. 이 페이지를 나갔다가 나중에 돌아올 수 있습니다.',
  importStateQueued: '대기 중', importStateValidating: '확인 중', importStateBuildingFts: '어휘 색인 생성 중', importStateVerifyingIndex: '벡터 색인 확인 중', importStateEmbedding: '벡터 준비 중',
  importProgressMeta: '{percent}% 완료 · 항목 {entries}개 · 청크 {chunks}개', importPreparingMeta: '항목 {entries}개 · 청크 {chunks}개',
  invalidPack: '잘못된 지식 팩입니다', importTooLarge: '지식 팩이 10 MiB 크기 제한을 초과했습니다', removeConfirm: '지식 팩 {name}을(를) 제거할까요?'
}

const ru = {
  marketConnected: 'Маркет знаний подключён. Пакеты проверяются перед локальной установкой.',
  openMarket: 'Открыть маркет знаний', marketUnavailable: 'Маркет плагинов недоступен',
  loginRequired: 'Сначала войдите в маркет на странице управления плагинами', marketPairFailed: 'Не удалось безопасно связаться с локальным клиентом N.E.K.O',
  title: 'Управление знаниями', subtitle: 'Управление локальными знаниями, пакетами данных и совпадениями в диалогах',
  marketNotice: 'Интерфейс маркета знаний зарезервирован. Сейчас доступны единая публичная база знаний и локальные пакеты.',
  overview: 'Обзор', ready: 'Готово', degraded: 'Ошибка', entries: 'Записи', disabled: 'Отключено', packs: 'Пакеты',
  autoContext: 'Использовать в диалоге', catalog: 'Каталог', searchPlaceholder: 'Поиск по заголовкам, псевдонимам, описаниям и тексту',
  term: 'Запись', summary: 'Описание', source: 'Источник', actions: 'Действия', details: 'Подробнее', restore: 'Восстановить', disable: 'Отключить',
  previous: 'Назад', next: 'Далее', packId: 'ID пакета', materialType: 'Тип содержимого', subscription: 'Подписка', localImport: 'Локальный импорт',
  importPack: 'Импортировать пакет', diagnostics: 'Последние совпадения', time: 'Время', matchMode: 'Режим совпадения',
  delivered: 'Карточка передана', yes: 'Да', no: 'Нет', terms: 'Термины', titleMatch: 'Заголовок', aliasTerms: 'Псевдонимы', recognitionPhrases: 'Фразы распознавания', tags: 'Теги', content: 'Содержимое',
  indexStatus: 'Векторный индекс', indexOrigin: 'Источник', indexTrust: 'Доверие', indexValidation: 'Проверка', indexFallback: 'Резервный режим',
  localEmbeddingState: 'Локальное обслуживание', allowLocalEmbedding: 'Разрешить локальное обслуживание векторов', enabled: 'Включено', disabledState: 'Отключено',
  packageStatus: 'Состояние пакетов', sourceDistribution: 'Распределение источников', otherSources: 'Другие', inactivePacks: 'Отключено', needsAttention: 'Требует внимания', materialMix: 'Тип пакета', noPacks: 'Нет пакетов знаний',
  vectorReadyPercent: 'Готово {percent}%', noVectorChunks: 'Нет векторных фрагментов', vectorBuilding: 'Создаётся', vectorComplete: 'Полностью готово', vectorWaiting: 'Ожидает создания',
  indexPolicyHint: 'Может использовать локальные CPU и память. Если отключено, при недоступном доверенном индексе используется BM25.',
  loadFailed: 'Не удалось загрузить данные', operationFailed: 'Операция не выполнена', importSuccess: 'Пакет импортирован', importQueued: 'Пакет готовится в фоновом режиме и появится после завершения', importStillProcessing: 'Пакет всё ещё обрабатывается. Обновите страницу позже, чтобы проверить состояние.',
  degradedJobs: 'Импорты в карантине', degradedJobHint: 'Повреждённые задания не могут продолжиться. Проверьте ID и удалите задание, чтобы разблокировать новый импорт.', discardJobConfirm: 'Удалить задание в карантине {name}? Будут удалены только его временные файлы.', jobDiscarded: 'Задание в карантине удалено',
  importingPacks: 'Импорт пакетов знаний', importingPackHint: 'Локальная база знаний проверяется и подготавливается. Можно покинуть страницу и вернуться позже.',
  importStateQueued: 'В очереди', importStateValidating: 'Проверка', importStateBuildingFts: 'Создание лексического индекса', importStateVerifyingIndex: 'Проверка векторного индекса', importStateEmbedding: 'Подготовка векторов',
  importProgressMeta: 'Готово {percent}% · записей: {entries} · фрагментов: {chunks}', importPreparingMeta: 'Записей: {entries} · фрагментов: {chunks}',
  invalidPack: 'Недопустимый пакет', importTooLarge: 'Пакет знаний превышает ограничение 10 МиБ', removeConfirm: 'Удалить пакет {name}?'
}

const es = {
  marketConnected: 'El mercado de conocimiento está conectado. Los paquetes se validan antes de instalarlos localmente.',
  openMarket: 'Explorar mercado de conocimiento', marketUnavailable: 'El mercado de plugins no está disponible',
  loginRequired: 'Inicia sesión en el mercado desde la página de plugins', marketPairFailed: 'No se pudo emparejar de forma segura con el cliente N.E.K.O local',
  title: 'Gestor de conocimiento', subtitle: 'Gestiona conocimiento público local, paquetes de datos y coincidencias de conversación',
  marketNotice: 'La conexión del mercado de conocimiento está reservada. Ya se pueden gestionar la base pública unificada y los paquetes locales.',
  overview: 'Resumen', ready: 'Listo', degraded: 'Degradado', entries: 'Entradas', disabled: 'Desactivadas', packs: 'Paquetes',
  autoContext: 'Usar en conversación automática', catalog: 'Catálogo', searchPlaceholder: 'Buscar títulos, alias, resúmenes o contenido',
  term: 'Entrada', summary: 'Resumen', source: 'Fuente', actions: 'Acciones', details: 'Detalles', restore: 'Restaurar', disable: 'Desactivar',
  previous: 'Anterior', next: 'Siguiente', packId: 'ID del paquete', materialType: 'Tipo de contenido', subscription: 'Suscripción', localImport: 'Importación local',
  importPack: 'Importar paquete', diagnostics: 'Coincidencias recientes', time: 'Hora', matchMode: 'Modo de coincidencia',
  delivered: 'Tarjeta enviada', yes: 'Sí', no: 'No', terms: 'Términos', titleMatch: 'Título', aliasTerms: 'Alias', recognitionPhrases: 'Frases de reconocimiento', tags: 'Etiquetas', content: 'Contenido',
  indexStatus: 'Índice vectorial', indexOrigin: 'Origen', indexTrust: 'Confianza', indexValidation: 'Validación', indexFallback: 'Modo alternativo',
  localEmbeddingState: 'Mantenimiento local', allowLocalEmbedding: 'Permitir mantenimiento local de vectores', enabled: 'Activado', disabledState: 'Desactivado',
  packageStatus: 'Estado de paquetes', sourceDistribution: 'Distribución de fuentes', otherSources: 'Otros', inactivePacks: 'Desactivados', needsAttention: 'Requiere atención', materialMix: 'Tipo de paquete', noPacks: 'No hay paquetes',
  vectorReadyPercent: '{percent}% listo', noVectorChunks: 'Sin fragmentos vectoriales', vectorBuilding: 'Creando', vectorComplete: 'Todo listo', vectorWaiting: 'En espera',
  indexPolicyHint: 'Puede usar CPU y memoria locales. Si se desactiva, se usa BM25 cuando el índice de confianza no está disponible.',
  loadFailed: 'No se pudieron cargar los datos', operationFailed: 'La operación falló', importSuccess: 'Paquete importado', importQueued: 'El paquete se está preparando en segundo plano y aparecerá cuando esté listo', importStillProcessing: 'El paquete sigue procesándose. Actualiza más tarde para ver su estado.',
  degradedJobs: 'Importaciones en cuarentena', degradedJobHint: 'Las tareas dañadas no pueden continuar. Revisa el ID y descarta la tarea para desbloquear nuevas importaciones.', discardJobConfirm: '¿Descartar la tarea en cuarentena {name}? Solo se eliminarán sus archivos temporales.', jobDiscarded: 'Tarea en cuarentena descartada',
  importingPacks: 'Importando paquetes de conocimiento', importingPackHint: 'La base de conocimiento local se está verificando y preparando. Puedes salir de esta página y volver más tarde.',
  importStateQueued: 'En espera', importStateValidating: 'Validando', importStateBuildingFts: 'Creando índice léxico', importStateVerifyingIndex: 'Comprobando índice vectorial', importStateEmbedding: 'Preparando vectores',
  importProgressMeta: '{percent}% completado · {entries} entradas · {chunks} fragmentos', importPreparingMeta: '{entries} entradas · {chunks} fragmentos',
  invalidPack: 'Paquete no válido', importTooLarge: 'El paquete de conocimiento supera el límite de 10 MiB', removeConfirm: '¿Eliminar el paquete {name}?'
}

const pt = {
  marketConnected: 'O mercado de conhecimento está conectado. Os pacotes são verificados antes da instalação local.',
  openMarket: 'Explorar mercado de conhecimento', marketUnavailable: 'O mercado de plugins está indisponível',
  loginRequired: 'Entre no mercado pela página de plugins primeiro', marketPairFailed: 'Não foi possível emparelhar com segurança com o cliente N.E.K.O local',
  title: 'Gerenciador de conhecimento', subtitle: 'Gerencie conhecimento público local, pacotes de dados e correspondências de conversa',
  marketNotice: 'A conexão do mercado de conhecimento está reservada. A base pública unificada e os pacotes locais já podem ser gerenciados.',
  overview: 'Visão geral', ready: 'Pronto', degraded: 'Degradado', entries: 'Entradas', disabled: 'Desativadas', packs: 'Pacotes',
  autoContext: 'Usar na conversa automática', catalog: 'Catálogo', searchPlaceholder: 'Pesquisar títulos, aliases, resumos ou conteúdo',
  term: 'Entrada', summary: 'Resumo', source: 'Fonte', actions: 'Ações', details: 'Detalhes', restore: 'Restaurar', disable: 'Desativar',
  previous: 'Anterior', next: 'Próxima', packId: 'ID do pacote', materialType: 'Tipo de conteúdo', subscription: 'Assinatura', localImport: 'Importação local',
  importPack: 'Importar pacote', diagnostics: 'Correspondências recentes', time: 'Hora', matchMode: 'Modo de correspondência',
  delivered: 'Cartão enviado', yes: 'Sim', no: 'Não', terms: 'Termos', titleMatch: 'Título', aliasTerms: 'Aliases', recognitionPhrases: 'Frases de reconhecimento', tags: 'Tags', content: 'Conteúdo',
  indexStatus: 'Índice vetorial', indexOrigin: 'Origem', indexTrust: 'Confiança', indexValidation: 'Validação', indexFallback: 'Modo alternativo',
  localEmbeddingState: 'Manutenção local', allowLocalEmbedding: 'Permitir manutenção local de vetores', enabled: 'Ativado', disabledState: 'Desativado',
  packageStatus: 'Estado dos pacotes', sourceDistribution: 'Distribuição de fontes', otherSources: 'Outros', inactivePacks: 'Desativados', needsAttention: 'Precisa de atenção', materialMix: 'Tipo de pacote', noPacks: 'Nenhum pacote',
  vectorReadyPercent: '{percent}% pronto', noVectorChunks: 'Sem fragmentos vetoriais', vectorBuilding: 'Criando', vectorComplete: 'Tudo pronto', vectorWaiting: 'Aguardando criação',
  indexPolicyHint: 'Pode usar CPU e memória locais. Se desativado, usa BM25 quando o índice confiável não está disponível.',
  loadFailed: 'Falha ao carregar os dados', operationFailed: 'A operação falhou', importSuccess: 'Pacote importado', importQueued: 'O pacote está sendo preparado em segundo plano e aparecerá quando estiver pronto', importStillProcessing: 'O pacote ainda está sendo processado. Atualize mais tarde para verificar o estado.',
  degradedJobs: 'Importações em quarentena', degradedJobHint: 'Tarefas danificadas não podem continuar. Confira o ID e descarte a tarefa para liberar novas importações.', discardJobConfirm: 'Descartar a tarefa em quarentena {name}? Apenas os arquivos temporários serão removidos.', jobDiscarded: 'Tarefa em quarentena descartada',
  importingPacks: 'Importando pacotes de conhecimento', importingPackHint: 'A base de conhecimento local está sendo verificada e preparada. Você pode sair desta página e voltar depois.',
  importStateQueued: 'Aguardando', importStateValidating: 'Validando', importStateBuildingFts: 'Criando índice lexical', importStateVerifyingIndex: 'Verificando índice vetorial', importStateEmbedding: 'Preparando vetores',
  importProgressMeta: '{percent}% concluído · {entries} entradas · {chunks} fragmentos', importPreparingMeta: '{entries} entradas · {chunks} fragmentos',
  invalidPack: 'Pacote inválido', importTooLarge: 'O pacote de conhecimento excede o limite de 10 MiB', removeConfirm: 'Remover o pacote {name}?'
}

export const knowledgeMessages: Record<AppLocale, Record<string, string>> = { 'zh-CN': zhCN, 'zh-TW': zhTW, 'en-US': enUS, ja, ko, ru, es, pt }

let registered = false

/** Merge the Knowledge Manager copy into every locale; later calls are no-ops. */
export function registerKnowledgeMessages(): void {
  if (registered) return
  for (const [locale, messages] of Object.entries(knowledgeMessages)) {
    i18n.global.mergeLocaleMessage(locale, { knowledge: messages })
  }
  registered = true
}
