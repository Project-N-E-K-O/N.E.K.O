/**
 * Русский языковой пакет
 */
import { yuiGuideLocales } from './yuiGuide'

export default {
  common: {
    loading: 'Загрузка...',
    refresh: 'Обновить',
    search: 'Поиск',
    filter: 'Фильтр',
    reset: 'Сброс',
    confirm: 'Подтвердить',
    cancel: 'Отмена',
    save: 'Сохранить',
    delete: 'Удалить',
    edit: 'Редактировать',
    add: 'Добавить',
    back: 'Назад',
    submit: 'Отправить',
    close: 'Закрыть',
    success: 'Успешно',
    error: 'Ошибка',
    warning: 'Предупреждение',
    info: 'Информация',
    noData: 'Нет данных',
    unknown: 'Неизвестно',
    nA: 'Н/Д',
    darkMode: 'Тёмная тема',
    lightMode: 'Светлая тема',
    logoutConfirmTitle: 'Уведомление',
    disconnected: 'Соединение с сервером потеряно',
    languageAuto: 'Авто'
  },
  nav: {
    dashboard: 'Панель',
    plugins: 'Плагины',
    metrics: 'Производительность',
    logs: 'Логи',
    runs: 'Запуски',
    serverLogs: 'Логи сервера',
    adapters: 'Адаптеры',
    adapterUI: 'Интерфейс адаптера',
    packageManager: 'Менеджер пакетов'
  },
  auth: {
    unauthorized: 'Неавторизованный доступ',
    forbidden: 'Доступ запрещён'
  },
  plugin: {
    addProfile: {
      prompt: 'Введите имя нового профиля',
      title: 'Добавить профиль',
      inputError: 'Имя не может быть пустым или состоять только из пробелов'
    },
    removeProfile: {
      confirm: 'Вы уверены, что хотите удалить профиль «{name}»?',
      title: 'Удалить профиль'
    }
  },
  dashboard: {
    title: 'Панель',
    pluginOverview: 'Обзор плагинов',
    totalPlugins: 'Всего плагинов',
    running: 'Запущено',
    stopped: 'Остановлено',
    crashed: 'Ошибка',
    globalMetrics: 'Глобальный мониторинг производительности',
    totalCpuUsage: 'Общее использование CPU',
    totalMemoryUsage: 'Общее использование памяти',
    totalThreads: 'Всего потоков',
    activePlugins: 'Активных плагинов',
    serverInfo: 'Информация о сервере',
    sdkVersion: 'Версия SDK',
    updateTime: 'Время обновления',
    noMetricsData: 'Нет данных о производительности',
    failedToLoadServerInfo: 'Не удалось загрузить информацию о сервере',
    startTutorial: 'Обучение',
    tutorialHint: 'Впервые в менеджере плагинов? Нажмите сюда, и я быстро всё покажу.'
  },
  plugins: {
    title: 'Список плагинов',
    name: 'Имя плагина',
    id: 'ID плагина',
    version: 'Версия',
    description: 'Описание',
    status: 'Статус',
    sdkVersion: 'Версия SDK',
    actions: 'Действия',
    start: 'Запустить',
    stop: 'Остановить',
    reload: 'Перезагрузить',
    reloadAll: 'Перезагрузить все',
    reloadAllConfirm: 'Вы уверены, что хотите перезагрузить все {count} запущенных плагинов?',
    reloadAllSuccess: 'Успешно перезагружено {count} плагинов',
    reloadAllPartial: 'Перезагрузка завершена: {success} успешно, {fail} с ошибками',
    viewDetails: 'Подробнее',
    noPlugins: 'Нет плагинов',
    adapterNotFound: 'Адаптер не найден',
    pluginNotFound: 'Плагин не найден',
    pluginDetail: 'Детали плагина',
    basicInfo: 'Основная информация',
    entries: 'Точки входа',
    performance: 'Производительность',
    config: 'Конфигурация',
    logs: 'Логи',
    entryPoint: 'Точка входа',
    entryName: 'Имя',
    entryId: 'ID',
    entryDescription: 'Описание',
    trigger: 'Триггер',
    triggerSuccess: 'Триггер выполнен',
    triggerFailed: 'Ошибка триггера',
    noEntries: 'Нет точек входа',
    showMetrics: 'Показать производительность',
    hideMetrics: 'Скрыть производительность',
    filterPlaceholder: 'Фильтр по тексту, пиньиню и правилам is:/type:/has:',
    filterRules: 'Правила',
    filterRulesTitle: 'Правила фильтрации',
    filterRulesHint: 'Нажмите правило ниже, чтобы вставить его в запрос и комбинировать с обычным текстом.',
    filterWhitelist: 'Белый список',
    filterBlacklist: 'Чёрный список',
    invalidRegex: 'Недопустимое регулярное выражение',
    hoverToShowFilter: 'Наведите для отображения фильтра',
    configPath: 'Файл конфигурации',
    lastModified: 'Последнее изменение',
    configEditorPlaceholder: 'Введите конфигурацию в формате TOML',
    configInvalidToml: 'Недопустимый формат TOML. Исправьте перед сохранением.',
    configLoadFailed: 'Не удалось загрузить конфигурацию плагина',
    configSaveFailed: 'Не удалось сохранить конфигурацию плагина',
    configReloadTitle: 'Требуется перезагрузка',
    configReloadPrompt: 'Конфигурация обновлена. Перезагрузить плагин для применения?',
    configApplyTitle: 'Применить конфигурацию',
    configHotUpdatePrompt: 'Конфигурация сохранена. Применить к запущенному плагину сейчас? (Горячее обновление не требует перезапуска)',
    hotUpdate: 'Горячее обновление',
    reloadPlugin: 'Перезапустить плагин',
    hotUpdateSuccess: 'Горячее обновление конфигурации выполнено',
    hotUpdatePartial: 'Конфигурация сохранена, но плагин не запущен. Изменения вступят в силу после запуска.',
    hotUpdateFailed: 'Ошибка горячего обновления',
    formMode: 'Форма',
    sourceMode: 'Исходник',
    formModeHint: 'Этот режим создаёт форму из объекта конфигурации, разобранного сервером. Для расширенных функций TOML (комментарии/форматирование) используйте режим исходника.',
    addField: 'Добавить поле',
    addItem: 'Добавить элемент',
    fieldName: 'Имя поля',
    fieldNameRequired: 'Имя поля обязательно',
    invalidFieldKey: 'Недопустимое имя поля',
    fieldType: 'Тип поля',
    duplicateFieldKey: 'Имя поля уже существует. Выберите другое.',
    profiles: 'Профили',
    active: 'Текущий',
    diffPreview: 'Предварительный просмотр изменений',
    unsavedChangesWarning: 'У вас есть несохранённые изменения. При переключении плагина они будут потеряны. Продолжить?',
    enabled: 'Включён',
    disabled: 'Отключён',
    autoStart: 'Автозапуск',
    manualStart: 'Ручной запуск',
    fetchFailed: 'Не удалось получить список плагинов',
    extension: 'Расширение',
    pluginType: 'Тип',
    pluginTypeNormal: 'Плагин',
    hostPlugin: 'Хост-плагин',
    boundExtensions: 'Привязанные расширения',
    pluginsSection: 'Плагины',
    adaptersSection: 'Адаптеры',
    extensionsSection: 'Расширения',
    typePlugin: 'Плагин',
    typeAdapter: 'Адаптер',
    typeExtension: 'Расширение',
    openPackageManager: 'Менеджер пакетов',
    closePackageManager: 'Скрыть менеджер пакетов',
    packageManagerOpened: 'Менеджер пакетов открыт',
    packageManagerSyncHint: 'Текущие фильтры и выбранные плагины напрямую синхронизируются с панелью менеджера пакетов справа.',
    multiSelect: 'Множественный выбор',
    exitMultiSelect: 'Выйти из выбора',
    selectedCount: 'Выбрано: {count}',
    selectAllVisible: 'Выбрать видимые',
    invertVisibleSelection: 'Инвертировать видимые',
    clearSelection: 'Очистить выбор',
    batchStartConfirm: 'Запустить {count} выбранных плагинов?',
    batchStopConfirm: 'Остановить {count} запущенных плагинов?',
    batchReloadConfirm: 'Перезагрузить {count} запущенных плагинов?',
    batchDeleteConfirm: 'Удалить {count} выбранных плагинов? Это действие необратимо.',
    batchStartSuccess: 'Успешно запущено {count} плагинов',
    batchStopSuccess: 'Успешно остановлено {count} плагинов',
    batchReloadSuccess: 'Успешно перезагружено {count} плагинов',
    batchDeleteSuccess: 'Успешно удалено {count} плагинов',
    batchPartial: 'Завершено: {success} успешно, {fail} с ошибками',
    batchNoStartable: 'Нет запускаемых плагинов в выборке',
    batchNoStoppable: 'Нет запущенных плагинов в выборке',
    batchNoReloadable: 'Нет запущенных плагинов в выборке',
    import: 'Импорт',
    importing: 'Импорт…',
    importSuccess: 'Импортирован {name}, распаковано {count} плагинов',
    importFailed: 'Ошибка импорта',
    export: 'Экспорт',
    exportSuccess: 'Экспортировано {count} пакетов',
    exportFailed: 'Ошибка экспорта',
    exportPackFailed: 'Ошибка упаковки, экспорт невозможен',
    filterRuleGroups: {
      state: 'Состояние',
      type: 'Тип',
      meta: 'Метаданные'
    },
    filterRuleLabels: {
      running: 'Запущен',
      stopped: 'Остановлен',
      disabled: 'Отключён',
      selected: 'Выбран',
      manual: 'Ручной старт',
      auto: 'Автозапуск',
      plugin: 'Плагин',
      adapter: 'Адаптер',
      extension: 'Расширение',
      ui: 'Есть UI',
      entries: 'Есть точки входа',
      host: 'Есть хост',
      name: 'По имени',
      id: 'По ID',
      hostTarget: 'По хосту',
      version: 'По версии',
      entry: 'По точке входа',
      author: 'По автору'
    },
    contextSections: {
      navigation: 'Навигация',
      runtime: 'Управление',
      plugin: 'Возможности плагина'
    },
    pack: 'Упаковать плагин',
    delete: 'Удалить плагин',
    disableExtension: 'Отключить расширение',
    enableExtension: 'Включить расширение',
    dangerDialog: {
      title: 'Подтверждение опасного действия',
      warningTitle: 'Это действие необратимо',
      deleteMessage: 'Удаление "{pluginName}" удалит каталог плагина и сразу обновит список.',
      hint: 'Чтобы избежать случайного нажатия, удерживайте кнопку ниже для подтверждения.',
      holdIdle: 'Удерживайте для удаления',
      holdActive: 'Продолжайте удерживать для подтверждения…',
      loading: 'Удаление плагина...'
    },
    ui: {
      open: 'Открыть UI',
      panel: 'Панель',
      guide: 'Обучение',
      loading: 'Загрузка интерфейса плагина...',
      loadError: 'Не удалось загрузить интерфейс плагина',
      noUI: 'У этого плагина нет пользовательского интерфейса',
      hostedTsxPending: 'Рендеринг Hosted TSX скоро будет доступен',
      markdownPending: 'Рендеринг Markdown-обучения скоро будет доступен',
      autoPending: 'Автоматические панели скоро будут доступны',
      surfaceUnavailable: 'Surface недоступен',
      surfaceEntryMissing: 'Файл entry, указанный этим Surface, не найден. Проверьте путь entry в plugin.toml.',
      surfaceWarnings: 'В объявлении UI плагина есть проблемы, требующие внимания',
      controlError: 'Ошибка элемента управления UI плагина',
      hostedRuntimePending: 'Vue-контейнер распознал этот Surface. TSX, Markdown и Auto рендереры будут подключены позже.'
    }
  },
  metrics: {
    title: 'Производительность',
    pluginMetrics: 'Производительность плагинов',
    cpuUsage: 'Использование CPU',
    memoryUsage: 'Использование памяти',
    threads: 'Потоки',
    pid: 'ID процесса',
    noMetrics: 'Нет данных о производительности',
    refreshInterval: 'Интервал обновления',
    seconds: 'сек.',
    cpu: 'Использование CPU',
    memory: 'Использование памяти',
    memoryPercent: '% памяти',
    pendingRequests: 'Ожидающие запросы',
    totalExecutions: 'Всего выполнений',
    noData: 'Нет данных'
  },
  logs: {
    title: 'Логи',
    pluginLogs: 'Логи плагинов',
    serverLogs: 'Логи сервера',
    level: 'Уровень',
    time: 'Время',
    source: 'Источник',
    file: 'Файл',
    message: 'Сообщение',
    allLevels: 'Все уровни',
    noLogs: 'Нет логов',
    autoScroll: 'Автопрокрутка',
    scrollToBottom: 'Прокрутить вниз',
    logFiles: 'Файлы логов',
    selectFile: 'Выбрать файл',
    search: 'Поиск по логам...',
    lines: 'Строки',
    totalLogs: 'Всего {count} записей',
    loadError: 'Не удалось загрузить логи: {error}',
    emptyFile: 'Файл логов пуст или не существует',
    noMatches: 'Совпадений не найдено',
    logFile: 'Файл логов',
    totalLines: 'Всего строк',
    returnedLines: 'Возвращено строк',
    connected: 'Подключено',
    disconnected: 'Отключено',
    connectionFailed: 'Ошибка подключения к потоку логов'
  },
  runs: {
    title: 'Запуски',
    detail: 'Детали запуска',
    wsDisconnected: 'Соединение в реальном времени не установлено. Проверьте состояние сервера.',
    noRuns: 'Нет запусков',
    selectRun: 'Выберите запуск для просмотра',
    runId: 'Run ID',
    status: 'Статус',
    pluginId: 'ID плагина',
    entryId: 'Точка входа',
    updatedAt: 'Обновлено',
    createdAt: 'Создано',
    stage: 'Этап',
    message: 'Сообщение',
    progress: 'Прогресс',
    error: 'Ошибка',
    export: 'Экспорт',
    exportType: 'Тип',
    exportContent: 'Содержимое',
    noExport: 'Нет данных для экспорта',
    cancel: 'Отменить запуск',
    cancelConfirmTitle: 'Отменить этот запуск?',
    cancelConfirmMessage: 'Run ID: {runId}',
    cancelSuccess: 'Запрос на отмену отправлен'
  },
  status: {
    running: 'Запущен',
    stopped: 'Остановлен',
    crashed: 'Ошибка',
    loadFailed: 'Ошибка загрузки',
    loading: 'Загрузка',
    disabled: 'Отключён',
    injected: 'Внедрён',
    pending: 'Ожидание хоста'
  },
  logLevel: {
    DEBUG: 'Отладка',
    INFO: 'Информация',
    WARNING: 'Предупреждение',
    ERROR: 'Ошибка',
    CRITICAL: 'Критическая',
    UNKNOWN: 'Неизвестно'
  },
  messages: {
    fetchFailed: 'Не удалось получить данные',
    operationSuccess: 'Операция выполнена успешно',
    operationFailed: 'Операция не выполнена',
    confirmDelete: 'Подтвердить удаление?',
    confirmStop: 'Остановить плагин?',
    confirmStart: 'Запустить плагин?',
    confirmReload: 'Перезагрузить плагин?',
    pluginStarted: 'Плагин запущен',
    pluginStopped: 'Плагин остановлен',
    pluginReloaded: 'Плагин перезагружен',
    pluginPacked: 'Плагин упакован: {packageName}',
    pluginDeleted: 'Плагин удален',
    startFailed: 'Ошибка запуска',
    stopFailed: 'Ошибка остановки',
    reloadFailed: 'Ошибка перезагрузки',
    packFailed: 'Не удалось упаковать плагин',
    deleteFailed: 'Не удалось удалить плагин',
    pluginDisabled: 'Плагин отключён. Сначала включите его.',
    pluginLoadFailed: 'Ошибка загрузки плагина. Запуск невозможен.',
    confirmDisableExt: 'Отключить это расширение? Функционал расширения будет выгружен из хост-плагина.',
    extensionDisabled: 'Расширение отключено',
    extensionEnabled: 'Расширение включено',
    disableExtFailed: 'Не удалось отключить расширение',
    enableExtFailed: 'Не удалось включить расширение',
    requestFailed: 'Ошибка запроса',
    requestFailedWithStatus: 'Ошибка запроса ({status})',
    badRequest: 'Неверные параметры запроса',
    resourceNotFound: 'Запрошенный ресурс не найден',
    internalServerError: 'Внутренняя ошибка сервера',
    serviceUnavailable: 'Сервис недоступен',
    networkError: 'Ошибка сети. Проверьте подключение.'
  },
  welcome: {
    about: {
      title: 'О N.E.K.O.',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism) — это «живая» метавселенная AI-компаньонов, которую мы создаём вместе. Это UGC-платформа с открытым исходным кодом и социальной направленностью, цель которой — построить AI-нативную метавселенную, тесно связанную с реальным миром.'
    },
    pluginManagement: {
      title: 'Управление плагинами',
      description: 'Откройте список плагинов через панель навигации слева. Вы можете просматривать, запускать, останавливать и перезагружать плагины. Каждый плагин имеет независимый мониторинг производительности и просмотр логов для удобного управления и отладки.'
    },
    mcpServer: {
      title: 'MCP-сервер',
      description: 'N.E.K.O. поддерживает серверы Model Context Protocol (MCP), позволяя плагинам взаимодействовать с другими AI-системами и сервисами через стандартизированные протоколы. Управление MCP-подключениями доступно на странице деталей плагина.'
    },
    documentation: {
      title: 'Документация и ресурсы',
      description: 'Подробнее см. в документации проекта:',
      links: [
        { text: 'Репозиторий GitHub', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Страница в Steam', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Сообщество Discord', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' и ',
      readme: 'Файл README.md:',
      openFailed: 'Не удалось открыть README.md в редакторе',
      openTimeout: 'Тайм-аут запроса. Не удалось открыть README.md.',
      openError: 'Ошибка при открытии файла README.md'
    },
    community: {
      title: 'Сообщество и поддержка',
      description: 'Присоединяйтесь к нашему сообществу для общения с другими разработчиками и пользователями:',
      links: [
        { text: 'Сервер Discord', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'Группа QQ', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'GitHub Issues', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' и '
    }
  },
  app: {
    titleSuffix: 'N.E.K.O Управление плагинами'
  },
  tutorial: {
    yuiGuide: yuiGuideLocales.ru
  },
  yuiTutorial: {
    title: 'Добро пожаловать в менеджер плагинов!',
    welcome: 'Здесь вы управляете всеми плагинами! Просматривайте, запускайте и настраивайте плагины, чтобы сделать меня ещё мощнее~',
    hint: 'Осмотритесь и нажмите кнопку ниже, когда закончите~',
    complete: 'Всё осмотрел~',
    dismiss: 'Пропустить',
    keyboardSkipHint: 'Нажмите Enter или Space, чтобы перейти дальше. Сработает через 0,5 секунды после начала каждого шага.',
    steps: {
      start: {
        title: 'Начните здесь',
        body: 'Эта кнопка запускает обучение по менеджеру плагинов в любой момент. Само оно появляться не будет, ня.'
      },
      stats: {
        title: 'Обзор плагинов',
        body: 'Эти карточки показывают общее число плагинов, запущенные, остановленные и упавшие плагины.'
      },
      metrics: {
        title: 'Мониторинг производительности',
        body: 'Здесь видны CPU, память, потоки и активные плагины сервиса плагинов.'
      },
      server: {
        title: 'Информация о сервере',
        body: 'Здесь можно проверить версию SDK, число плагинов и время обновления, чтобы убедиться, что сервис работает.'
      },
      plugins: {
        title: 'Список плагинов',
        body: 'Откройте «Плагины» слева, чтобы запускать, останавливать, настраивать плагины и смотреть логи.'
      },
      pluginWorkbench: {
        title: 'Рабочая область плагинов',
        body: 'Здесь собраны плагины, адаптеры и расширения для повседневного управления.'
      },
      pluginFilters: {
        title: 'Поиск и фильтры',
        body: 'Фильтруйте по имени, состоянию, типу или расширенным правилам.'
      },
      pluginLayout: {
        title: 'Вид списка',
        body: 'Переключайте список, одну колонку, две колонки или компактный режим под размер экрана.'
      },
      pluginContextMenu: {
        title: 'Действия правой кнопкой',
        body: 'Правый клик открывает детали, настройки, логи и быстрые действия вроде запуска или перезагрузки.'
      },
      packageManager: {
        title: 'Менеджер пакетов',
        body: 'Он использует текущие фильтры и выбор для упаковки, проверки, верификации и распаковки.'
      },
      packageOperations: {
        title: 'Операции с пакетами',
        body: 'Здесь выбираются режимы упаковки, проверки и анализа. Опасные действия tutorial не запускает.'
      },
      pluginDetail: {
        title: 'Детали плагина',
        body: 'Страница деталей показывает метаданные, точки входа, метрики, настройки и логи.'
      },
      pluginDetailActions: {
        title: 'Действия деталей',
        body: 'Кнопки справа сверху относятся к текущему плагину.'
      },
      runs: {
        title: 'Запуски',
        body: 'Запуски показывают историю и живой статус задач плагинов.'
      },
      runsList: {
        title: 'Список запусков',
        body: 'Выберите запуск слева или обновите список, чтобы получить свежие записи.'
      },
      runsDetail: {
        title: 'Детали запуска',
        body: 'Панель показывает этап, прогресс, ошибки и экспорт; отмена доступна только для отменяемых задач.'
      },
      logs: {
        title: 'Логи сервера',
        body: 'Логи сервера помогают смотреть вывод и ошибки самого сервиса плагинов.'
      },
      logToolbar: {
        title: 'Фильтры логов',
        body: 'Фильтруйте по уровню, ключевому слову и числу строк, либо переключайте автопрокрутку.'
      },
      logList: {
        title: 'Список логов',
        body: 'Логи показывают время, источник, уровень и сообщение для диагностики проблем.'
      }
    }
  }
}
