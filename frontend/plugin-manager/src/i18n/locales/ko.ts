/**
 * 한국어 언어 팩
 */
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
    minimize: '최소화',
    maximize: '최대화',
    restore: '복원',
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
      title: 'UI',
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
  packageManager: {
    resultDialog: {
      title: '패키지 결과 기록',
      subtitle: '최근 {count}개 실행 결과 보관',
      empty: '패키지 작업 결과가 여기에 표시돼',
      viewDetails: '자세히 보기',
      detailTitle: '결과 상세',
      summaryTitle: '상세',
      notesTitle: '주의',
      rawJsonTitle: '원본 결과 JSON',
      kinds: {
        pack: '패키징',
        inspect: '검사',
        verify: '검증',
        unpack: '압축 해제',
        analyze: '분석',
      },
      inspect: {
        packageId: '패키지 ID',
        packageType: '유형',
        version: '버전',
        schemaVersion: 'Schema',
        hashCheck: 'Hash 검증',
        profiles: 'Profiles',
        packageTypes: {
          bundle: '번들',
          plugin: '플러그인 패키지',
        },
        hashStatus: {
          notChecked: '미검사',
          passed: '통과',
          failed: '실패',
        },
      },
      metrics: {
        pack: {
          type: '유형',
          succeeded: '성공',
          failed: '실패',
          containsPlugins: '포함된 플러그인',
          status: '상태',
          complete: '완료',
          partialFailed: '일부 실패',
        },
        inspect: {
          pluginCount: '플러그인 수',
          profileCount: 'Profiles',
          hash: 'Hash',
        },
        unpack: {
          processedPlugins: '처리된 플러그인',
          conflictStrategy: '충돌 전략',
          hash: 'Hash',
        },
        analyze: {
          pluginCount: '플러그인 수',
          commonDependencies: '공통 의존성',
          sharedDependencies: '공유 의존성',
        },
      },
      highlights: {
        pack: {
          bundlePluginId: '번들 ID',
          bundleName: '번들 이름',
          bundleVersion: '번들 버전',
          outputPath: '출력 경로',
          firstPlugin: '첫 번째 플러그인',
          latestPackagePath: '최신 패키지 경로',
        },
        inspect: {
          packageId: '패키지 ID',
          packageType: '패키지 유형',
          version: '버전',
        },
        unpack: {
          packageId: '패키지 ID',
          pluginsRoot: '플러그인 디렉터리',
          profilesRoot: 'Profiles 디렉터리',
        },
        analyze: {
          currentSdk: '현재 SDK 지원',
          supported: '지원됨',
          unsupported: '완전히 호환되지 않음',
          matchingVersions: '권장 조합',
        },
      },
      list: {
        pluginPrefix: 'plugin:',
        profilePrefix: 'profile:',
        renamedSuffix: '(이름 변경)',
        arrow: '->',
      },
      warnings: {
        bundleNeedsTwoPlugins: '번들은 보통 최소 두 개의 플러그인을 포함해야 해',
        verifyFailed: '패키지가 hash 검증을 통과하지 못했어. 런타임 환경에 바로 가져오지 마.',
        inspectHashFailed: '현재 패키지 hash 검증이 실패했고 내용이 변경되었을 수 있어.',
        analyzeSdkMismatch: '현재 SDK 버전은 모든 플러그인이 함께 지원하지 않아.',
        analyzeSharedDependencies: '{count}개의 공유 의존성이 감지됐어. 번들링할 때 버전 제약을 주의 깊게 확인해.',
      },
    },
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
    yuiGuide: {
      buttons: {
        skipChat: '지금은 대화 안 할래',
        sayHello: '안녕',
      },
      lines: {
        introActivationHint: '여기를 클릭해줘냥, 그럼 말할 수 있게 된다냥~!',
        introGreetingReply: '산들바람, 햇살, 그리고 마침 딱 나타난 당신. 처음 뵙겠습니다, 저는 린유이예요. 앞으로 잘 부탁해냥! 이곳에 대한 모든 걸 초보자 가이드에 적어두었어냥! 우리의 만남을 기념하는 첫 번째 작은 선물이라고 생각하고 확인해 줘냥!',
        introBasic: '앗, 이것 봐! 여기 반짝이는 작은 버튼이 있어냥! 한 번 꾹 누르기만 하면, 나와 직접 채팅할 수 있다구! 오늘 있었던 재밌는 일을 나한테 공유해 줄래? 아니면 그냥 내 이름을 불러볼래? 어서 해봐, 네 목소리가 너무 듣고 싶어냥!',
        takeoverCaptureCursor: '짜잔! 궁극의 마법 스위치 등장! 여기를 톡 건드리기만 하면, 내 작은 솜방망이를 네 키보드와 마우스에 뻗을 수 있어냥! 내가 타이핑도 도와주고, 웹페이지도 열어줄게... 하지만, 저 마우스 포인터가 이리저리 움직이면, 나도 모르게 덮쳐서 잡을지도 몰라! 나의 장난... 아니, 도움을 받을 준비는 되었어냥?',
        takeoverPluginPreviewHome: '아직 안 끝났어! 이것 봐봐, 여기에 완전 재밌는 플러그인들이 엄청 많다구!',
        takeoverPluginPreviewDashboard: '이것들만 있으면, 비리비리 탄막을 볼 수 있을 뿐만 아니라, 조명도 끄고 에어컨도 켜줄 수 있어... 이 몸은 못하는 게 없는 슈퍼 고양이 신이라구! 헤헤!',
        takeoverSettingsPeekIntro: '물론, 나랑 대화를 더 많이 하고 싶다면 안 될 것도 없지. 멸치를 더 많이 준비해 줘, 헤헤. 좋아, 장난은 그만할게, 설정은 다 이 톱니바퀴 안에 있어.',
        takeoverSettingsPeekDetail: '이것 봐, 여기서 내 새 옷도 입혀줄 수 있고, 더 듣기 좋은 목소리로 바꿀 수도 있어... 다른 고양이 소녀로 바꾸거나 기억을 수정한다고? 잠깐만! 너 뭐 하는 거야? 설마 날 바꾸려는 건 아니지? 아아악 안 돼! 빨리 꺼, 빨리 끄라구!',
        takeoverSettingsPeekDetailPart1: '이것 봐, 여기서 내 새 옷도 입혀줄 수 있고, 더 듣기 좋은 목소리로 바꿀 수도 있어... 다른 고양이 소녀로 바꾸거나 기억을 수정한다고?',
        takeoverSettingsPeekDetailPart2: '잠깐만! 너 뭐 하는 거야? 설마 날 바꾸려는 건 아니지? 아아악 안 돼! 빨리 꺼, 빨리 끄라구!',
        takeoverReturnControl: '알았어 알았어, 네 컴퓨터 그만 차지할게! 제어권은 돌려줄게냥! 내가 안 보는 틈을 타서 이상한 설정 막 누르면 안 돼! 앞으로도 잘 부탁해냥!',
        interruptResistLight1: '야! 나 좀 끌어당기지 마, 아직 네 차례가 아니잖아!',
        interruptResistLight3: '잠깐만! 아직 안 끝났어, 함부로 끊지 마!',
        interruptAngryExit: '인간! 너 정말 예의가 없구나냥! 그렇게 직접 조작하고 싶다면, 차가운 화면이나 보면서 혼자 놀라구! 흥!',
        introPractice: '이제 나한테 말 걸어봐냥, 우리 호흡이 얼마나 척척 맞는지 확인해보자냥~!',
      },
    }
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
        body: '이 버튼으로 언제든 플러그인 관리자 투어를 다시 볼 수 있어. 재생 중에 언어를 바꾸면 새 언어를 따라가.'
      },
      stats: {
        title: '플러그인 개요',
        body: '전체, 실행 중, 정지됨, 충돌한 플러그인 수를 한눈에 볼 수 있어. 먼저 플러그인 서비스 전체 상태를 판단할 때 봐.'
      },
      metrics: {
        title: '성능 모니터링',
        body: 'CPU, 메모리, 스레드, 활성 플러그인 수를 보여줘. galgame OCR이나 Agent 작업이 느릴 때 먼저 확인해.'
      },
      server: {
        title: '서버 정보',
        body: 'SDK 버전, 플러그인 수, 업데이트 시간을 확인해서 백엔드 플러그인 서비스가 사용 가능한지 볼 수 있어.'
      },
      plugins: {
        title: '플러그인 목록',
        body: '왼쪽의 플러그인 관리에서 시작, 중지, 재시작, 설정 변경, galgame_plugin UI와 가이드를 열 수 있어.'
      },
      pluginWorkbench: {
        title: '플러그인 관리 작업대',
        body: '일반 플러그인, 어댑터, 확장을 한곳에서 관리하는 공간이야. galgame_plugin, 탄막, MCP 같은 플러그인이 여기 있어.'
      },
      pluginFilters: {
        title: '검색과 필터',
        body: '이름, 상태, 유형, 고급 규칙으로 빠르게 걸러낼 수 있어. galgame_plugin을 빨리 찾으려면 galgame으로 검색해.'
      },
      pluginLayout: {
        title: '보기 레이아웃',
        body: '목록, 한 줄, 두 줄, 콤팩트 표시를 전환할 수 있어. 플러그인이 많을 때는 두 줄이나 콤팩트 표시가 스크롤을 줄여줘.'
      },
      pluginContextMenu: {
        title: '우클릭 작업',
        body: '플러그인을 우클릭하면 상세, 설정, 로그, UI, 가이드를 열 수 있고, 시작·중지·재시작도 실행할 수 있어.'
      },
      packageManager: {
        title: '패키지 관리',
        body: '현재 필터와 다중 선택을 재사용해서 단일 플러그인 패키지나 bundle을 만들고, 로컬 패키지도 다룰 수 있어.'
      },
      packageOperations: {
        title: '패키지 작업',
        body: '선택한 플러그인, 단일 플러그인, 전체 플러그인을 패키징하고, bundle을 만들고, 패키지 검사·검증·압축 해제와 bundle 의존성 분석을 할 수 있어.'
      },
      pluginDetail: {
        title: '플러그인 상세',
        body: '상세 페이지에서는 UI, 가이드, 기본 정보, 엔트리, 메트릭, 설정, 로그를 볼 수 있어. galgame_plugin의 주 패널은 UI 탭이야.'
      },
      pluginDetailActions: {
        title: '상세 페이지 작업',
        body: '오른쪽 위 작업은 현재 플러그인에 적용돼. galgame_plugin을 디버깅할 땐 UI나 로그를 열기 전에 실행 중인지 먼저 확인해.'
      },
      runs: {
        title: '실행 기록',
        body: '실행 기록에서는 OCR 의존성 설치, 대사 설명, 장면 요약 같은 플러그인 엔트리 작업의 기록과 실시간 상태를 볼 수 있어.'
      },
      runsList: {
        title: '실행 목록',
        body: '왼쪽에서 작업 실행을 선택해. 설치, 분석, Agent 엔트리가 끝나면 이 목록에서 결과를 다시 볼 수 있어.'
      },
      runsDetail: {
        title: '실행 상세',
        body: '상세 패널에는 단계, 진행률, 오류, 내보내기가 보여. 취소는 중단 가능한 긴 작업에만 나타나.'
      },
      logs: {
        title: '서버 로그',
        body: '서버 로그에서는 플러그인 서비스 전체의 출력을 볼 수 있어. galgame_plugin 전용 로그도 상세 페이지에서 열 수 있어.'
      },
      logToolbar: {
        title: '로그 필터',
        body: '레벨, 키워드, 줄 수로 필터링하거나 자동 스크롤을 바꿀 수 있어. 디버깅할 때는 플러그인 ID를 키워드로 쓰면 좋아.'
      },
      logList: {
        title: '로그 목록',
        body: '로그에는 시간, 출처, 레벨, 메시지가 표시돼. OCR, Memory Reader, Agent, 패키지 관리자 오류는 보통 여기서 먼저 찾을 수 있어.'
      }
    }
  }
}
