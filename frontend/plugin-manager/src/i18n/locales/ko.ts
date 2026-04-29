/**
 * 한국어 언어 팩
 */
import { yuiGuideLocales } from './yuiGuide'

export default {
  common: {
    loading: '로딩 중...',
    refresh: '새로고침',
    search: '검색',
    filter: '필터',
    reset: '초기화',
    confirm: '확인',
    cancel: '취소',
    save: '저장',
    delete: '삭제',
    edit: '편집',
    add: '추가',
    back: '뒤로',
    submit: '제출',
    close: '닫기',
    success: '성공',
    error: '오류',
    warning: '경고',
    info: '정보',
    noData: '데이터 없음',
    unknown: '알 수 없음',
    nA: 'N/A',
    darkMode: '다크 모드',
    lightMode: '라이트 모드',
    logoutConfirmTitle: '알림',
    disconnected: '서버 연결이 끊어졌습니다',
    languageAuto: '자동'
  },
  nav: {
    dashboard: '대시보드',
    plugins: '플러그인 관리',
    metrics: '성능 지표',
    logs: '로그',
    runs: '실행 기록',
    serverLogs: '서버 로그',
    adapters: '어댑터',
    adapterUI: '어댑터 UI',
    packageManager: '패키지 관리'
  },
  auth: {
    unauthorized: '인증되지 않은 접근',
    forbidden: '접근이 거부되었습니다'
  },
  plugin: {
    addProfile: {
      prompt: '새 프로필 이름을 입력하세요',
      title: '프로필 추가',
      inputError: '이름은 비어 있거나 공백만으로 구성될 수 없습니다'
    },
    removeProfile: {
      confirm: '프로필 "{name}"을(를) 삭제하시겠습니까?',
      title: '프로필 삭제'
    }
  },
  dashboard: {
    title: '대시보드',
    pluginOverview: '플러그인 개요',
    totalPlugins: '총 플러그인 수',
    running: '실행 중',
    stopped: '정지됨',
    crashed: '충돌',
    globalMetrics: '글로벌 성능 모니터링',
    totalCpuUsage: '총 CPU 사용률',
    totalMemoryUsage: '총 메모리 사용량',
    totalThreads: '총 스레드 수',
    activePlugins: '활성 플러그인 수',
    serverInfo: '서버 정보',
    sdkVersion: 'SDK 버전',
    updateTime: '업데이트 시간',
    noMetricsData: '성능 데이터 없음',
    failedToLoadServerInfo: '서버 정보를 불러오지 못했습니다',
    startTutorial: '튜토리얼 가이드',
    tutorialHint: '플러그인 관리자가 처음이라면 여기를 눌러 빠르게 둘러보자냥.'
  },
  plugins: {
    title: '플러그인 목록',
    name: '플러그인 이름',
    id: '플러그인 ID',
    version: '버전',
    description: '설명',
    status: '상태',
    sdkVersion: 'SDK 버전',
    actions: '작업',
    start: '시작',
    stop: '정지',
    reload: '리로드',
    reloadAll: '모두 리로드',
    reloadAllConfirm: '실행 중인 {count}개의 플러그인을 모두 리로드하시겠습니까?',
    reloadAllSuccess: '{count}개의 플러그인을 리로드했습니다',
    reloadAllPartial: '리로드 완료: {success}개 성공, {fail}개 실패',
    viewDetails: '상세 보기',
    noPlugins: '플러그인 없음',
    adapterNotFound: '어댑터를 찾을 수 없습니다',
    pluginNotFound: '플러그인을 찾을 수 없습니다',
    pluginDetail: '플러그인 상세',
    basicInfo: '기본 정보',
    entries: '엔트리 포인트',
    performance: '성능 지표',
    config: '설정',
    logs: '로그',
    entryPoint: '엔트리 포인트',
    entryName: '이름',
    entryId: 'ID',
    entryDescription: '설명',
    trigger: '트리거',
    triggerSuccess: '트리거 성공',
    triggerFailed: '트리거 실패',
    noEntries: '엔트리 포인트 없음',
    showMetrics: '성능 지표 표시',
    hideMetrics: '성능 지표 숨기기',
    filterPlaceholder: '텍스트, 병음, is:/type:/has: 규칙으로 필터링',
    filterRules: '규칙',
    filterRulesTitle: '필터 규칙',
    filterRulesHint: '아래 규칙을 클릭하면 쿼리에 바로 삽입되며 일반 텍스트와 함께 사용할 수 있습니다.',
    filterWhitelist: '화이트리스트',
    filterBlacklist: '블랙리스트',
    invalidRegex: '잘못된 정규식입니다',
    hoverToShowFilter: '호버하여 필터 표시',
    configPath: '설정 파일',
    lastModified: '마지막 수정',
    configEditorPlaceholder: 'TOML 형식의 설정 내용을 입력하세요',
    configInvalidToml: 'TOML 형식이 잘못되었습니다. 수정 후 저장하세요.',
    configLoadFailed: '플러그인 설정을 불러오지 못했습니다',
    configSaveFailed: '플러그인 설정을 저장하지 못했습니다',
    configReloadTitle: '리로드 필요',
    configReloadPrompt: '설정이 업데이트되었습니다. 플러그인을 리로드하여 적용하시겠습니까?',
    configApplyTitle: '설정 적용',
    configHotUpdatePrompt: '설정이 저장되었습니다. 실행 중인 플러그인에 바로 적용하시겠습니까? (핫 업데이트는 재시작이 필요하지 않습니다)',
    hotUpdate: '핫 업데이트',
    reloadPlugin: '플러그인 재시작',
    hotUpdateSuccess: '설정 핫 업데이트가 완료되었습니다',
    hotUpdatePartial: '설정이 저장되었지만 플러그인이 실행 중이 아닙니다. 시작 후 적용됩니다.',
    hotUpdateFailed: '핫 업데이트에 실패했습니다',
    formMode: '폼',
    sourceMode: '소스',
    formModeHint: '이 모드는 서버에서 파싱된 설정 객체로 폼을 생성합니다. 고급 TOML 기능(주석/포맷팅)은 소스 모드를 사용하세요.',
    addField: '필드 추가',
    addItem: '항목 추가',
    fieldName: '필드 이름',
    fieldNameRequired: '필드 이름은 필수입니다',
    invalidFieldKey: '잘못된 필드 이름입니다',
    fieldType: '필드 유형',
    duplicateFieldKey: '필드 이름이 이미 존재합니다. 다른 이름을 사용하세요.',
    profiles: '프로필',
    active: '현재',
    diffPreview: '차이점 미리보기',
    unsavedChangesWarning: '저장하지 않은 변경사항이 있습니다. 플러그인을 전환하면 변경사항이 손실됩니다. 계속하시겠습니까?',
    enabled: '활성화됨',
    disabled: '비활성화됨',
    autoStart: '자동 시작',
    manualStart: '수동 시작',
    fetchFailed: '플러그인 목록을 불러오지 못했습니다',
    extension: '확장 기능',
    pluginType: '유형',
    pluginTypeNormal: '플러그인',
    hostPlugin: '호스트 플러그인',
    boundExtensions: '바인딩된 확장 기능',
    pluginsSection: '플러그인',
    adaptersSection: '어댑터',
    extensionsSection: '확장 기능',
    typePlugin: '플러그인',
    typeAdapter: '어댑터',
    typeExtension: '확장 기능',
    openPackageManager: '패키지 관리',
    closePackageManager: '패키지 관리 닫기',
    packageManagerOpened: '패키지 관리가 열려 있음',
    packageManagerSyncHint: '현재 필터와 선택 상태가 오른쪽 패키지 관리 패널에 그대로 동기화됩니다.',
    multiSelect: '다중 선택',
    exitMultiSelect: '다중 선택 종료',
    selectedCount: '{count}개 선택됨',
    selectAllVisible: '현재 보이는 항목 전체 선택',
    invertVisibleSelection: '현재 보이는 항목 반전 선택',
    clearSelection: '선택 지우기',
    batchStartConfirm: '선택한 {count}개의 플러그인을 시작하시겠습니까?',
    batchStopConfirm: '실행 중인 {count}개의 플러그인을 정지하시겠습니까?',
    batchReloadConfirm: '실행 중인 {count}개의 플러그인을 리로드하시겠습니까?',
    batchDeleteConfirm: '선택한 {count}개의 플러그인을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.',
    batchStartSuccess: '{count}개의 플러그인을 시작했습니다',
    batchStopSuccess: '{count}개의 플러그인을 정지했습니다',
    batchReloadSuccess: '{count}개의 플러그인을 리로드했습니다',
    batchDeleteSuccess: '{count}개의 플러그인을 삭제했습니다',
    batchPartial: '완료: {success}개 성공, {fail}개 실패',
    batchNoStartable: '선택 항목 중 시작 가능한 플러그인이 없습니다',
    batchNoStoppable: '선택 항목 중 실행 중인 플러그인이 없습니다',
    batchNoReloadable: '선택 항목 중 실행 중인 플러그인이 없습니다',
    import: '가져오기',
    importing: '가져오는 중…',
    importSuccess: '{name}을(를) 가져와 {count}개의 플러그인을 풀었습니다',
    importFailed: '가져오기에 실패했습니다',
    export: '내보내기',
    exportSuccess: '{count}개의 패키지를 내보냈습니다',
    exportFailed: '내보내기에 실패했습니다',
    exportPackFailed: '패키징에 실패하여 내보낼 수 없습니다',
    filterRuleGroups: {
      state: '상태',
      type: '유형',
      meta: '메타데이터'
    },
    filterRuleLabels: {
      running: '실행 중',
      stopped: '중지됨',
      disabled: '비활성화',
      selected: '선택됨',
      manual: '수동 시작',
      auto: '자동 시작',
      plugin: '플러그인',
      adapter: '어댑터',
      extension: '확장 기능',
      ui: 'UI 있음',
      entries: '엔트리 있음',
      host: '호스트 있음',
      name: '이름 기준',
      id: 'ID 기준',
      hostTarget: '호스트 기준',
      version: '버전 기준',
      entry: '엔트리 기준',
      author: '작성자 기준'
    },
    contextSections: {
      navigation: '탐색',
      runtime: '실행',
      plugin: '확장 기능'
    },
    pack: '플러그인 패키징',
    delete: '플러그인 삭제',
    disableExtension: '확장 기능 비활성화',
    enableExtension: '확장 기능 활성화',
    dangerDialog: {
      title: '위험 작업 확인',
      warningTitle: '되돌릴 수 없는 작업',
      deleteMessage: '"{pluginName}"을 삭제하면 플러그인 디렉터리가 제거되고 목록도 즉시 새로고침됩니다.',
      hint: '오작동을 막기 위해 아래 버튼을 길게 눌러 확인해 주세요.',
      holdIdle: '길게 눌러 삭제',
      holdActive: '계속 누르면 확인됩니다…',
      loading: '플러그인을 삭제하는 중...'
    },
    ui: {
      open: 'UI 열기',
      panel: '패널',
      guide: '튜토리얼',
      loading: '플러그인 UI 로딩 중...',
      loadError: '플러그인 UI를 불러오지 못했습니다',
      noUI: '이 플러그인에는 사용자 정의 UI가 없습니다',
      hostedTsxPending: 'Hosted TSX 렌더링은 곧 지원됩니다',
      markdownPending: 'Markdown 튜토리얼 렌더링은 곧 지원됩니다',
      autoPending: '자동 생성 패널은 곧 지원됩니다',
      surfaceUnavailable: 'Surface를 사용할 수 없습니다',
      surfaceEntryMissing: '이 Surface가 선언한 엔트리 파일이 없습니다. plugin.toml의 entry 경로를 확인해 주세요.',
      surfaceWarnings: '플러그인 UI 선언에 확인이 필요한 문제가 있습니다',
      controlError: '플러그인 UI 컨트롤 오류',
      hostedRuntimePending: 'Vue 컨테이너가 이 Surface를 인식했습니다. TSX/Markdown/Auto 렌더러는 이후 단계에서 연결됩니다.'
    }
  },
  metrics: {
    title: '성능 지표',
    pluginMetrics: '플러그인 성능 지표',
    cpuUsage: 'CPU 사용률',
    memoryUsage: '메모리 사용량',
    threads: '스레드 수',
    pid: '프로세스 ID',
    noMetrics: '성능 데이터 없음',
    refreshInterval: '새로고침 간격',
    seconds: '초',
    cpu: 'CPU 사용률',
    memory: '메모리 사용량',
    memoryPercent: '메모리 비율',
    pendingRequests: '대기 중인 요청',
    totalExecutions: '총 실행 횟수',
    noData: '데이터 없음'
  },
  logs: {
    title: '로그',
    pluginLogs: '플러그인 로그',
    serverLogs: '서버 로그',
    level: '레벨',
    time: '시간',
    source: '소스',
    file: '파일',
    message: '메시지',
    allLevels: '모든 레벨',
    noLogs: '로그 없음',
    autoScroll: '자동 스크롤',
    scrollToBottom: '하단으로 스크롤',
    logFiles: '로그 파일',
    selectFile: '파일 선택',
    search: '로그 검색...',
    lines: '줄 수',
    totalLogs: '총 {count}건',
    loadError: '로그를 불러오지 못했습니다: {error}',
    emptyFile: '로그 파일이 비어 있거나 존재하지 않습니다',
    noMatches: '일치하는 로그가 없습니다',
    logFile: '로그 파일',
    totalLines: '총 줄 수',
    returnedLines: '반환된 줄 수',
    connected: '연결됨',
    disconnected: '연결 안 됨',
    connectionFailed: '로그 스트림 연결에 실패했습니다'
  },
  runs: {
    title: '실행 기록',
    detail: '실행 상세',
    wsDisconnected: '실시간 연결이 설정되지 않았습니다. 서버 상태를 확인하세요.',
    noRuns: '실행 기록 없음',
    selectRun: '실행 기록을 선택하세요',
    runId: 'Run ID',
    status: '상태',
    pluginId: '플러그인 ID',
    entryId: '엔트리',
    updatedAt: '업데이트 시간',
    createdAt: '생성 시간',
    stage: '단계',
    message: '메시지',
    progress: '진행률',
    error: '오류',
    export: '내보내기',
    exportType: '유형',
    exportContent: '내용',
    noExport: '내보내기 내용 없음',
    cancel: '실행 취소',
    cancelConfirmTitle: '실행을 취소하시겠습니까?',
    cancelConfirmMessage: 'Run ID: {runId}',
    cancelSuccess: '취소 요청을 전송했습니다'
  },
  status: {
    running: '실행 중',
    stopped: '정지됨',
    crashed: '충돌',
    loadFailed: '로드 실패',
    loading: '로딩 중',
    disabled: '비활성화됨',
    injected: '주입됨',
    pending: '호스트 대기 중'
  },
  logLevel: {
    DEBUG: '디버그',
    INFO: '정보',
    WARNING: '경고',
    ERROR: '오류',
    CRITICAL: '심각',
    UNKNOWN: '알 수 없음'
  },
  messages: {
    fetchFailed: '데이터를 불러오지 못했습니다',
    operationSuccess: '작업이 성공했습니다',
    operationFailed: '작업이 실패했습니다',
    confirmDelete: '삭제하시겠습니까?',
    confirmStop: '플러그인을 정지하시겠습니까?',
    confirmStart: '플러그인을 시작하시겠습니까?',
    confirmReload: '플러그인을 리로드하시겠습니까?',
    pluginStarted: '플러그인이 시작되었습니다',
    pluginStopped: '플러그인이 정지되었습니다',
    pluginReloaded: '플러그인을 리로드했습니다',
    pluginPacked: '플러그인이 패키징되었습니다: {packageName}',
    pluginDeleted: '플러그인이 삭제되었습니다',
    startFailed: '시작에 실패했습니다',
    stopFailed: '정지에 실패했습니다',
    reloadFailed: '리로드에 실패했습니다',
    packFailed: '플러그인 패키징에 실패했습니다',
    deleteFailed: '플러그인 삭제에 실패했습니다',
    pluginDisabled: '플러그인이 비활성화되어 있습니다. 먼저 활성화하세요.',
    pluginLoadFailed: '플러그인 로드에 실패하여 시작할 수 없습니다.',
    confirmDisableExt: '이 확장 기능을 비활성화하시겠습니까? 호스트 플러그인의 확장 기능이 언로드됩니다.',
    extensionDisabled: '확장 기능이 비활성화되었습니다',
    extensionEnabled: '확장 기능이 활성화되었습니다',
    disableExtFailed: '확장 기능 비활성화에 실패했습니다',
    enableExtFailed: '확장 기능 활성화에 실패했습니다',
    requestFailed: '요청에 실패했습니다',
    requestFailedWithStatus: '요청에 실패했습니다 ({status})',
    badRequest: '잘못된 요청 매개변수입니다',
    resourceNotFound: '요청한 리소스를 찾을 수 없습니다',
    internalServerError: '서버 내부 오류',
    serviceUnavailable: '서비스를 사용할 수 없습니다',
    networkError: '네트워크 오류. 연결을 확인하세요.'
  },
  welcome: {
    about: {
      title: 'N.E.K.O. 소개',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism)는 당신과 함께 만들어가는 "살아있는" AI 컴패니언 메타버스입니다. 오픈소스 기반의 공익 지향 UGC 플랫폼으로, 현실 세계와 밀접하게 연결된 AI 네이티브 메타버스를 구축하는 것을 목표로 합니다.'
    },
    pluginManagement: {
      title: '플러그인 관리',
      description: '왼쪽 내비게이션 바에서 플러그인 목록에 접근할 수 있습니다. 플러그인을 조회, 시작, 정지, 리로드할 수 있습니다. 각 플러그인에는 독립적인 성능 모니터링 및 로그 보기 기능이 있어 플러그인 시스템을 더 잘 관리하고 디버깅할 수 있습니다.'
    },
    mcpServer: {
      title: 'MCP 서버',
      description: 'N.E.K.O.는 Model Context Protocol (MCP) 서버를 지원하여 플러그인이 표준화된 프로토콜을 통해 다른 AI 시스템 및 서비스와 상호작용할 수 있습니다. 플러그인 상세 페이지에서 MCP 연결을 확인하고 관리할 수 있습니다.'
    },
    documentation: {
      title: '문서 및 리소스',
      description: '자세한 내용은 프로젝트 문서를 참조하세요:',
      links: [
        { text: 'GitHub 리포지토리', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Steam 스토어 페이지', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Discord 커뮤니티', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ', ',
      readme: 'README.md 파일:',
      openFailed: '에디터에서 README.md 파일을 열지 못했습니다',
      openTimeout: '요청 시간 초과. README.md 파일을 열지 못했습니다.',
      openError: 'README.md 파일을 여는 중 오류가 발생했습니다'
    },
    community: {
      title: '커뮤니티 및 지원',
      description: '커뮤니티에 참여하여 다른 개발자 및 사용자와 교류하세요:',
      links: [
        { text: 'Discord 서버', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'QQ 그룹', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'GitHub Issues', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ', '
    }
  },
  app: {
    titleSuffix: 'N.E.K.O 플러그인 관리'
  },
  tutorial: {
    yuiGuide: yuiGuideLocales.ko
  },
  yuiTutorial: {
    title: '냐~ 플러그인 관리 패널에 오신 걸 환영해!',
    welcome: '여기가 모든 플러그인을 관리하는 곳이야! 플러그인을 보고, 실행하고, 설정해서 나를 더 강력하게 만들어줘~',
    hint: '천천히 둘러보고 다 봤으면 아래 버튼을 눌러줘~',
    complete: '다 봤어 냐~',
    dismiss: '나중에 볼게',
    keyboardSkipHint: 'Enter 또는 Space를 누르면 다음 단계로 넘어가. 각 단계 시작 0.5초 후부터 동작해.',
    steps: {
      start: {
        title: '여기서 시작',
        body: '이 버튼으로 언제든 플러그인 관리자 튜토리얼을 다시 볼 수 있어. 내가 먼저 튀어나오진 않는다냥.'
      },
      stats: {
        title: '플러그인 개요',
        body: '전체, 실행 중, 정지됨, 충돌한 플러그인 수를 한눈에 확인할 수 있어.'
      },
      metrics: {
        title: '성능 모니터링',
        body: '플러그인 서비스의 CPU, 메모리, 스레드, 활성 플러그인 상태를 보여줘.'
      },
      server: {
        title: '서버 정보',
        body: 'SDK 버전, 플러그인 수, 업데이트 시간을 확인해서 서비스 상태를 볼 수 있어.'
      },
      plugins: {
        title: '플러그인 목록',
        body: '플러그인 시작, 정지, 설정, 로그 확인은 왼쪽의 플러그인 관리에서 하면 된다냥.'
      },
      pluginWorkbench: {
        title: '플러그인 관리 작업대',
        body: '여기에서 플러그인, 어댑터, 확장을 한곳에서 관리할 수 있어.'
      },
      pluginFilters: {
        title: '검색과 필터',
        body: '이름, 상태, 유형, 고급 규칙으로 플러그인을 빠르게 찾을 수 있어.'
      },
      pluginLayout: {
        title: '보기 레이아웃',
        body: '목록, 한 줄, 두 줄, 콤팩트 표시를 화면에 맞게 바꿀 수 있어.'
      },
      pluginContextMenu: {
        title: '우클릭 작업',
        body: '플러그인을 우클릭하면 상세, 설정, 로그를 열거나 시작, 정지, 리로드를 실행할 수 있어.'
      },
      packageManager: {
        title: '패키지 관리',
        body: '현재 필터와 선택을 재사용해서 패키징, 검사, 검증, 압축 해제를 할 수 있어.'
      },
      packageOperations: {
        title: '패키지 작업',
        body: '패키징 모드, 검사, 압축 해제, 번들 분석을 선택할 수 있어. 위험한 작업은 자동 실행하지 않아.'
      },
      pluginDetail: {
        title: '플러그인 상세',
        body: '상세 페이지에서는 메타 정보, 엔트리, 성능, 설정, 로그를 확인할 수 있어.'
      },
      pluginDetailActions: {
        title: '상세 페이지 작업',
        body: '오른쪽 위 작업은 현재 플러그인에 대한 빠른 작업이야.'
      },
      runs: {
        title: '실행 기록',
        body: '실행 기록은 플러그인 엔트리 작업의 히스토리와 상태를 보여줘.'
      },
      runsList: {
        title: '실행 목록',
        body: '왼쪽 목록에서 실행을 선택하고 새로고침으로 최신 기록을 동기화할 수 있어.'
      },
      runsDetail: {
        title: '실행 상세',
        body: '오른쪽에는 단계, 진행률, 오류, 내보낸 결과가 표시돼.'
      },
      logs: {
        title: '서버 로그',
        body: '서버 로그에서는 플러그인 서비스 자체의 출력과 오류를 확인할 수 있어.'
      },
      logToolbar: {
        title: '로그 필터',
        body: '레벨, 키워드, 줄 수로 로그를 필터링하고 자동 스크롤을 바꿀 수 있어.'
      },
      logList: {
        title: '로그 목록',
        body: '시간, 출처, 레벨, 메시지를 보고 플러그인 문제를 추적할 수 있어.'
      }
    }
  }
}
