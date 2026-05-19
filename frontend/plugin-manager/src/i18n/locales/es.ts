/**
 * Paquete de idioma español
 */
export default {
  common: {
    loading: 'Cargando...',
    refresh: 'Actualizar',
    search: 'Buscar',
    filter: 'Filtrar',
    reset: 'Restablecer',
    confirm: 'Confirmar',
    cancel: 'Cancelar',
    save: 'Guardar',
    delete: 'Eliminar',
    edit: 'Editar',
    add: 'Añadir',
    back: 'Atrás',
    submit: 'Enviar',
    close: 'Cerrar',
    minimize: 'Minimizar',
    maximize: 'Maximizar',
    restore: 'Restaurar',
    success: 'Éxito',
    error: 'Error',
    warning: 'Advertencia',
    info: 'Información',
    noData: 'Sin datos',
    unknown: 'Desconocido',
    nA: 'N/D',
    darkMode: 'Modo oscuro',
    lightMode: 'Modo claro',
    logoutConfirmTitle: 'Aviso',
    disconnected: 'Servidor desconectado',
    languageAuto: 'Automático'
  },
  nav: {
    dashboard: 'Panel',
    plugins: 'Plugins',
    metrics: 'Métricas',
    logs: 'Registros',
    runs: 'Ejecuciones',
    serverLogs: 'Registros del servidor',
    adapters: 'Adaptadores',
    adapterUI: 'UI del adaptador',
    packageManager: 'Gestor de paquetes'
  },
  auth: {
    unauthorized: 'Acceso no autorizado',
    forbidden: 'Acceso denegado'
  },
  plugin: {
    addProfile: {
      prompt: 'Introduce un nombre de perfil nuevo',
      title: 'Añadir perfil',
      inputError: 'El nombre no puede estar vacío ni contener solo espacios'
    },
    removeProfile: {
      confirm: '¿Seguro que deseas eliminar el perfil "{name}"?',
      title: 'Eliminar perfil'
    }
  },
  dashboard: {
    title: 'Panel',
    pluginOverview: 'Resumen de plugins',
    totalPlugins: 'Plugins totales',
    running: 'En ejecución',
    stopped: 'Detenidos',
    crashed: 'Con fallos',
    globalMetrics: 'Monitorización global de rendimiento',
    totalCpuUsage: 'Uso total de CPU',
    totalMemoryUsage: 'Uso total de memoria',
    totalThreads: 'Hilos totales',
    activePlugins: 'Plugins activos',
    serverInfo: 'Información del servidor',
    sdkVersion: 'Versión del SDK',
    updateTime: 'Hora de actualización',
    noMetricsData: 'Sin datos de rendimiento',
    failedToLoadServerInfo: 'Error al cargar la información del servidor',
    startTutorial: 'Guía tutorial',
    tutorialHint: '¿Primera vez en el gestor de plugins? Pulsa aquí y te lo enseño rápido.'
  },
  plugins: {
    title: 'Plugins',
    name: 'Nombre del plugin',
    id: 'ID del plugin',
    version: 'Versión',
    description: 'Descripción',
    status: 'Estado',
    sdkVersion: 'Versión del SDK',
    actions: 'Acciones',
    start: 'Iniciar',
    stop: 'Detener',
    reload: 'Recargar',
    reloadAll: 'Recargar todo',
    reloadAllConfirm: '¿Seguro que quieres recargar los {count} plugins en ejecución?',
    reloadAllSuccess: 'Se recargaron correctamente {count} plugins',
    reloadAllPartial: 'Recarga completada: {success} con éxito, {fail} fallidos',
    viewDetails: 'Ver detalles',
    noPlugins: 'Sin plugins',
    adapterNotFound: 'Adaptador no encontrado',
    pluginNotFound: 'Plugin no encontrado',
    pluginDetail: 'Detalle del plugin',
    basicInfo: 'Información básica',
    entries: 'Puntos de entrada',
    performance: 'Rendimiento',
    config: 'Configuración',
    logs: 'Registros',
    entryPoint: 'Punto de entrada',
    entryName: 'Nombre',
    entryId: 'ID',
    entryDescription: 'Descripción',
    trigger: 'Activar',
    triggerSuccess: 'Activación correcta',
    triggerFailed: 'Error al activar',
    noEntries: 'Sin puntos de entrada',
    showMetrics: 'Mostrar métricas',
    hideMetrics: 'Ocultar métricas',
    filterPlaceholder: 'Filtrar plugins por texto, pinyin y reglas is:/type:/has:',
    filterRules: 'Reglas',
    filterRulesTitle: 'Reglas de filtro',
    filterRulesHint: 'Haz clic en una regla para insertarla en la consulta y combinarla con texto normal.',
    filterWhitelist: 'Lista blanca',
    filterBlacklist: 'Lista negra',
    invalidRegex: 'Expresión regular no válida',
    hoverToShowFilter: 'Pasa el cursor para mostrar el filtro',
    configPath: 'Archivo de configuración',
    lastModified: 'Última modificación',
    configEditorPlaceholder: 'Introduce la configuración del plugin en formato TOML',
    configInvalidToml: 'Formato TOML no válido. Corrígelo antes de guardar.',
    configLoadFailed: 'Error al cargar la configuración del plugin',
    configSaveFailed: 'Error al guardar la configuración del plugin',
    configReloadTitle: 'Recarga requerida',
    configReloadPrompt: 'Configuración actualizada. ¿Recargar el plugin ahora para aplicar los cambios?',
    configApplyTitle: 'Aplicar configuración',
    configHotUpdatePrompt: 'Configuración guardada. ¿Aplicarla al plugin en ejecución ahora? (la actualización en caliente no requiere reinicio)',
    hotUpdate: 'Actualización en caliente',
    reloadPlugin: 'Reiniciar plugin',
    hotUpdateSuccess: 'Configuración actualizada en caliente correctamente',
    hotUpdatePartial: 'Configuración guardada, pero el plugin no está en ejecución. Surtirá efecto al iniciarse.',
    hotUpdateFailed: 'Error en la actualización en caliente',
    formMode: 'Formulario',
    sourceMode: 'Fuente',
    formModeHint: 'Este modo renderiza un formulario a partir del objeto de configuración analizado por el servidor. Usa el modo fuente para funciones TOML avanzadas (comentarios/formato).',
    addField: 'Añadir campo',
    addItem: 'Añadir elemento',
    fieldName: 'Nombre del campo',
    fieldNameRequired: 'El nombre del campo es obligatorio',
    invalidFieldKey: 'Nombre de campo no válido',
    fieldType: 'Tipo de campo',
    duplicateFieldKey: 'El nombre del campo ya existe. Elige otro.',
    profiles: 'Perfiles',
    active: 'Activo',
    diffPreview: 'Vista previa de diferencias',
    unsavedChangesWarning: 'Tienes cambios sin guardar. Al cambiar de plugin se descartarán. ¿Continuar?',
    enabled: 'Habilitado',
    disabled: 'Deshabilitado',
    autoStart: 'Inicio automático',
    manualStart: 'Inicio manual',
    fetchFailed: 'Error al obtener los plugins',
    extension: 'Extensión',
    pluginType: 'Tipo',
    pluginTypeNormal: 'Plugin',
    hostPlugin: 'Plugin anfitrión',
    boundExtensions: 'Extensiones vinculadas',
    pluginsSection: 'Plugins',
    adaptersSection: 'Adaptadores',
    extensionsSection: 'Extensiones',
    typePlugin: 'Plugin',
    typeAdapter: 'Adaptador',
    typeExtension: 'Extensión',
    openPackageManager: 'Gestor de paquetes',
    closePackageManager: 'Ocultar gestor de paquetes',
    packageManagerOpened: 'Gestor de paquetes abierto',
    packageManagerSyncHint: 'Los filtros y plugins seleccionados se sincronizan directamente con el panel del gestor de paquetes.',
    multiSelect: 'Selección múltiple',
    exitMultiSelect: 'Salir de selección múltiple',
    selectedCount: '{count} seleccionados',
    selectAllVisible: 'Seleccionar visibles',
    invertVisibleSelection: 'Invertir visibles',
    clearSelection: 'Limpiar selección',
    batchStartConfirm: '¿Iniciar los {count} plugins seleccionados?',
    batchStopConfirm: '¿Detener los {count} plugins en ejecución?',
    batchReloadConfirm: '¿Recargar los {count} plugins en ejecución?',
    batchDeleteConfirm: '¿Eliminar los {count} plugins seleccionados? Esta acción no se puede deshacer.',
    batchStartSuccess: 'Se iniciaron correctamente {count} plugins',
    batchStopSuccess: 'Se detuvieron correctamente {count} plugins',
    batchReloadSuccess: 'Se recargaron correctamente {count} plugins',
    batchDeleteSuccess: 'Se eliminaron correctamente {count} plugins',
    batchPartial: 'Completado: {success} con éxito, {fail} fallidos',
    batchNoStartable: 'No hay plugins iniciables en la selección',
    batchNoStoppable: 'No hay plugins en ejecución en la selección',
    batchNoReloadable: 'No hay plugins en ejecución en la selección',
    import: 'Importar',
    importing: 'Importando…',
    importSuccess: 'Se importó {name}, se desempaquetaron {count} plugins',
    importFailed: 'Error al importar',
    export: 'Exportar',
    exportSuccess: 'Se exportaron {count} paquetes',
    exportFailed: 'Error al exportar',
    exportPackFailed: 'Falló el empaquetado, no se puede exportar',
    filterRuleGroups: {
      state: 'Estado',
      type: 'Tipo',
      meta: 'Metadatos'
    },
    filterRuleLabels: {
      running: 'En ejecución',
      stopped: 'Detenidos',
      disabled: 'Deshabilitado',
      selected: 'Seleccionados',
      manual: 'Inicio manual',
      auto: 'Inicio automático',
      plugin: 'Plugin',
      adapter: 'Adaptador',
      extension: 'Extensión',
      ui: 'Con UI',
      entries: 'Con puntos de entrada',
      host: 'Con anfitrión',
      name: 'Por nombre',
      id: 'Por ID',
      hostTarget: 'Por anfitrión',
      version: 'Por versión',
      entry: 'Por punto de entrada',
      author: 'Por autor'
    },
    contextSections: {
      navigation: 'Explorar',
      runtime: 'Tiempo de ejecución',
      plugin: 'Extras del plugin'
    },
    pack: 'Empaquetar plugin',
    delete: 'Eliminar plugin',
    disableExtension: 'Deshabilitar extensión',
    enableExtension: 'Habilitar extensión',
    dangerDialog: {
      title: 'Confirmar acción destructiva',
      warningTitle: 'Esta acción no se puede deshacer',
      deleteMessage: 'Al eliminar "{pluginName}" se borrará su directorio de plugin y la lista se actualizará inmediatamente.',
      hint: 'Para evitar pulsaciones accidentales, mantén pulsado el botón siguiente para continuar.',
      holdIdle: 'Mantén pulsado para eliminar',
      holdActive: 'Sigue pulsando para confirmar…',
      loading: 'Eliminando plugin...'
    },
    ui: {
      open: 'Abrir UI',
      title: 'UI',
      panel: 'Panel',
      guide: 'Tutorial',
      loading: 'Cargando UI del plugin...',
      loadError: 'Error al cargar la UI del plugin',
      noUI: 'Este plugin no tiene UI personalizada',
      hostedTsxPending: 'El renderizado Hosted TSX estará disponible pronto',
      markdownPending: 'El renderizado de tutoriales Markdown estará disponible pronto',
      autoPending: 'Los paneles autogenerados estarán disponibles pronto',
      surfaceUnavailable: 'Surface no disponible',
      surfaceEntryMissing: 'El archivo de entrada declarado por esta Surface no existe. Revisa la ruta entry en plugin.toml.',
      surfaceWarnings: 'La declaración de UI del plugin necesita atención',
      controlError: 'Error de control de la UI del plugin',
      hostedRuntimePending: 'El contenedor Vue reconoció esta Surface. Los renderizadores TSX, Markdown y Auto se conectarán en una fase posterior.'
    }
  },
  metrics: {
    title: 'Métricas',
    pluginMetrics: 'Métricas de rendimiento del plugin',
    cpuUsage: 'Uso de CPU',
    memoryUsage: 'Uso de memoria',
    threads: 'Hilos',
    pid: 'ID del proceso',
    noMetrics: 'Sin datos de rendimiento',
    refreshInterval: 'Intervalo de actualización',
    seconds: 'segundos',
    cpu: 'Uso de CPU',
    memory: 'Memoria',
    memoryPercent: '% de memoria',
    pendingRequests: 'Solicitudes pendientes',
    totalExecutions: 'Ejecuciones totales',
    noData: 'Sin datos'
  },
  logs: {
    title: 'Registros',
    pluginLogs: 'Registros del plugin',
    serverLogs: 'Registros del servidor',
    level: 'Nivel',
    time: 'Hora',
    source: 'Origen',
    file: 'Archivo',
    message: 'Mensaje',
    allLevels: 'Todos los niveles',
    noLogs: 'Sin registros',
    autoScroll: 'Desplazamiento automático',
    scrollToBottom: 'Desplazar al final',
    logFiles: 'Archivos de registro',
    selectFile: 'Seleccionar archivo',
    search: 'Buscar en registros...',
    lines: 'Líneas',
    totalLogs: 'Total {count} registros',
    loadError: 'Error al cargar los registros: {error}',
    emptyFile: 'El archivo de registro está vacío o no existe',
    noMatches: 'No hay registros coincidentes',
    logFile: 'Archivo de registro',
    totalLines: 'Líneas totales',
    returnedLines: 'Líneas devueltas',
    connected: 'Conectado',
    disconnected: 'Desconectado',
    connectionFailed: 'Error de conexión al flujo de registros'
  },
  runs: {
    title: 'Ejecuciones',
    detail: 'Detalle de ejecución',
    wsDisconnected: 'Conexión en tiempo real no establecida. Comprueba el estado del servidor.',
    noRuns: 'Sin ejecuciones',
    selectRun: 'Selecciona una ejecución para ver detalles',
    runId: 'ID de ejecución',
    status: 'Estado',
    pluginId: 'ID del plugin',
    entryId: 'Punto de entrada',
    updatedAt: 'Actualizado el',
    createdAt: 'Creado el',
    stage: 'Etapa',
    message: 'Mensaje',
    progress: 'Progreso',
    error: 'Error',
    export: 'Exportar',
    exportType: 'Tipo',
    exportContent: 'Contenido',
    noExport: 'Sin elementos para exportar',
    cancel: 'Cancelar ejecución',
    cancelConfirmTitle: '¿Cancelar esta ejecución?',
    cancelConfirmMessage: 'ID de ejecución: {runId}',
    cancelSuccess: 'Cancelación solicitada'
  },
  packageManager: {
    resultDialog: {
      title: 'Registro de resultados de paquetes',
      subtitle: 'Conserva los últimos {count} resultados',
      empty: 'Aquí aparecerán los resultados de las operaciones de paquetes',
      viewDetails: 'Ver detalles',
      detailTitle: 'Detalles del resultado',
      summaryTitle: 'Resumen',
      notesTitle: 'Notas',
      rawJsonTitle: 'JSON bruto del resultado',
      kinds: {
        pack: 'Empaquetar',
        inspect: 'Inspeccionar',
        verify: 'Verificar',
        unpack: 'Desempaquetar',
        analyze: 'Analizar',
      },
      inspect: {
        packageId: 'ID del paquete',
        packageType: 'Tipo',
        version: 'Versión',
        schemaVersion: 'Schema',
        hashCheck: 'Verificación hash',
        profiles: 'Perfiles',
        packageTypes: {
          bundle: 'Bundle',
          plugin: 'Paquete de plugin',
        },
        hashStatus: {
          notChecked: 'Sin comprobar',
          passed: 'Aprobado',
          failed: 'Falló',
        },
      },
      metrics: {
        pack: {
          type: 'Tipo',
          succeeded: 'Correctos',
          failed: 'Fallidos',
          containsPlugins: 'Contiene plugins',
          status: 'Estado',
          complete: 'Completado',
          partialFailed: 'Falló parcialmente',
        },
        inspect: {
          pluginCount: 'Cantidad de plugins',
          profileCount: 'Perfiles',
          hash: 'Hash',
        },
        unpack: {
          processedPlugins: 'Plugins procesados',
          conflictStrategy: 'Estrategia de conflicto',
          hash: 'Hash',
        },
        analyze: {
          pluginCount: 'Cantidad de plugins',
          commonDependencies: 'Dependencias comunes',
          sharedDependencies: 'Dependencias compartidas',
        },
      },
      highlights: {
        pack: {
          bundlePluginId: 'ID del bundle',
          bundleName: 'Nombre del bundle',
          bundleVersion: 'Versión del bundle',
          outputPath: 'Ruta de salida',
          firstPlugin: 'Primer plugin',
          latestPackagePath: 'Ruta del paquete más reciente',
        },
        inspect: {
          packageId: 'ID del paquete',
          packageType: 'Tipo de paquete',
          version: 'Versión',
        },
        unpack: {
          packageId: 'ID del paquete',
          pluginsRoot: 'Directorio de plugins',
          profilesRoot: 'Directorio de perfiles',
        },
        analyze: {
          currentSdk: 'Compatibilidad SDK actual',
          supported: 'compatible',
          unsupported: 'no totalmente compatible',
          matchingVersions: 'Combinaciones recomendadas',
        },
      },
      list: {
        pluginPrefix: 'plugin:',
        profilePrefix: 'perfil:',
        renamedSuffix: '(renombrado)',
        arrow: '->',
      },
      warnings: {
        bundleNeedsTwoPlugins: 'Un bundle suele incluir al menos dos plugins',
        verifyFailed: 'El paquete no pasó la verificación hash. No lo importes directamente.',
        inspectHashFailed: 'La verificación hash del paquete falló y el contenido pudo haber cambiado.',
        analyzeSdkMismatch: 'La versión actual del SDK no es compatible con todos los plugins a la vez.',
        analyzeSharedDependencies: 'Se detectaron {count} dependencias compartidas. Revisa bien las restricciones de versión.',
      },
    },
  },
  status: {
    running: 'En ejecución',
    stopped: 'Detenido',
    crashed: 'Con fallos',
    loadFailed: 'Error de carga',
    loading: 'Cargando',
    disabled: 'Deshabilitado',
    injected: 'Inyectado',
    pending: 'Anfitrión pendiente'
  },
  logLevel: {
    DEBUG: 'Depuración',
    INFO: 'Información',
    WARNING: 'Advertencia',
    ERROR: 'Error',
    CRITICAL: 'Crítico',
    UNKNOWN: 'Desconocido'
  },
  messages: {
    fetchFailed: 'Error al obtener los datos',
    operationSuccess: 'Operación correcta',
    operationFailed: 'Error en la operación',
    confirmDelete: '¿Confirmar eliminación?',
    confirmStop: '¿Confirmar detener plugin?',
    confirmStart: '¿Confirmar iniciar plugin?',
    confirmReload: '¿Confirmar recargar plugin?',
    pluginStarted: 'Plugin iniciado correctamente',
    pluginStopped: 'Plugin detenido',
    pluginReloaded: 'Plugin recargado correctamente',
    pluginPacked: 'Plugin empaquetado: {packageName}',
    pluginDeleted: 'Plugin eliminado',
    startFailed: 'Error al iniciar',
    stopFailed: 'Error al detener',
    reloadFailed: 'Error al recargar',
    packFailed: 'Error al empaquetar el plugin',
    deleteFailed: 'Error al eliminar el plugin',
    pluginLoadFailed: 'El plugin no se cargó y no puede iniciarse.',
    confirmDisableExt: '¿Deshabilitar esta extensión? Su funcionalidad se descargará del plugin anfitrión.',
    extensionDisabled: 'Extensión deshabilitada',
    extensionEnabled: 'Extensión habilitada',
    disableExtFailed: 'Error al deshabilitar la extensión',
    enableExtFailed: 'Error al habilitar la extensión',
    requestFailed: 'Solicitud fallida',
    requestFailedWithStatus: 'Solicitud fallida ({status})',
    badRequest: 'Parámetros de solicitud no válidos',
    resourceNotFound: 'Recurso solicitado no encontrado',
    internalServerError: 'Error interno del servidor',
    serviceUnavailable: 'Servicio no disponible',
    networkError: 'Error de red. Comprueba tu conexión.'
  },
  welcome: {
    about: {
      title: 'Acerca de N.E.K.O.',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism) es un metaverso de compañeros IA "vivos" que construimos juntos tú y yo. Es una plataforma UGC impulsada por código abierto y con orientación solidaria, dedicada a construir un metaverso AI-nativo estrechamente conectado con el mundo real.'
    },
    pluginManagement: {
      title: 'Gestión de plugins',
      description: 'Accede a la lista de plugins desde la barra de navegación izquierda. Puedes ver, iniciar, detener y recargar plugins. Cada plugin cuenta con monitorización de rendimiento y visualización de registros independientes para ayudarte a gestionar y depurar mejor el sistema de plugins.'
    },
    mcpServer: {
      title: 'Servidor MCP',
      description: 'N.E.K.O. admite servidores Model Context Protocol (MCP), lo que permite a los plugins interactuar con otros sistemas y servicios de IA mediante protocolos estandarizados. Puedes ver y gestionar las conexiones MCP en la página de detalles del plugin.'
    },
    documentation: {
      title: 'Documentación y recursos',
      description: 'Consulta la documentación del proyecto para más información:',
      links: [
        { text: 'Repositorio de GitHub', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Página de Steam', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Comunidad de Discord', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' y ',
      readme: 'Archivo README.md:',
      openFailed: 'Error al abrir README.md en el editor',
      openTimeout: 'Tiempo de espera agotado al abrir el archivo README.md',
      openError: 'Se produjo un error al abrir el archivo README.md'
    },
    community: {
      title: 'Comunidad y soporte',
      description: 'Únete a nuestra comunidad para conectar con otros desarrolladores y usuarios:',
      links: [
        { text: 'Servidor de Discord', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'Grupo QQ', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'Issues de GitHub', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' y '
    }
  },
  app: {
    titleSuffix: 'Gestor de plugins N.E.K.O'
  },
  tutorial: {
    yuiGuide: {
      buttons: {
        skipChat: 'Ahora no',
        sayHello: 'Hola',
      },
      lines: {
        introActivationHint: '¡Haz clic aquí para que pueda empezar a hablar, nyan~!',
        introGreetingReply: 'Brisa suave, sol, y tú apareciendo justo en el momento perfecto. Encantada de conocerte, soy Lin Youyi; ¡cuida de mí de ahora en adelante, miau! He escrito todo sobre este lugar en la guía para principiantes. Considéralo el primer regalito de nuestro encuentro; ¡échale un vistazo!',
        introBasic: '¡Oh, mira este botoncito brillante! Haz clic en él y podremos empezar a charlar enseguida. ¿Quieres contarme las novedades de hoy? ¿O solo decir mi nombre? Vamos, pruébalo, ¡no puedo esperar a escuchar tu voz! ¡Miau!',
        takeoverCaptureCursor: '¡Ta-dán! ¡Aparece el interruptor mágico definitivo! Con solo tocar aquí, puedo estirar mis patitas hasta tu teclado y tu ratón. Te ayudaré a escribir, a abrir páginas web... Pero, si ese puntero del ratón se mueve por ahí, quizá no pueda resistirme a saltar sobre él. ¿Estás listo para mis travesuras... ah, no, para mi ayuda? ¡Miau!',
        takeoverPluginPreviewHome: '¡Aún no ha terminado! Mira, mira, ¡aquí hay un montón de plugins divertidos!',
        takeoverPluginPreviewDashboard: 'Con ellos, no solo puedo leer el danmaku de Bilibili, también puedo ayudarte a apagar las luces y encender el aire acondicionado... ¡Soy la omnipotente súper diosa gata! ¡Jeje!',
        takeoverSettingsPeekIntro: 'Claro, si quieres que charle más contigo, tampoco es imposible. Solo prepárame más pescadito seco, jeje. Bueno, dejaré de tomarte el pelo; todos los ajustes están en este icono de engranaje.',
        takeoverSettingsPeekDetail: 'Mira, aquí puedes ponerme ropa nueva, darme una voz bonita... ¿Cambiarme por otra catgirl o modificar recuerdos? ¡Espera un momento! ¿Qué estás haciendo? ¿No estarás pensando en reemplazarme? ¡Aaaah, de ninguna manera! ¡Ciérralo, ciérralo rápido!',
        takeoverSettingsPeekDetailPart1: 'Mira, aquí puedes ponerme ropa nueva, darme una voz bonita... ¿Cambiarme por otra catgirl o modificar recuerdos?',
        takeoverSettingsPeekDetailPart2: '¡Espera un momento! ¿Qué estás haciendo? ¿No estarás pensando en reemplazarme? ¡Aaaah, de ninguna manera! ¡Ciérralo, ciérralo rápido!',
        takeoverReturnControl: 'Vale, vale, ¡ya no acapararé tu computadora! ¡Te devuelvo el control! Pero no puedes hacer clic en ajustes raros cuando no estoy mirando. ¡Sigue cuidando de mí de ahora en adelante!',
        interruptResistLight1: '¡Oye! No me arrastres, ¡aún no es tu turno!',
        interruptResistLight3: '¡Espera un momento! Todavía no ha terminado, ¡no me interrumpas así!',
        interruptAngryExit: '¡Humano! ¡Eres de verdad muy maleducado, miau! Ya que tantas ganas tienes de manejarlo tú mismo, ¡ve a jugar solo con la pantalla fría! ¡Hmph!',
        introPractice: '¡Ahora intenta hablarme y veamos si estamos perfectamente sincronizados, nyan~!',
      },
    }
  },
  yuiTutorial: {
    title: '¡Meow~ Bienvenido al Gestor de Plugins!',
    welcome: 'Aquí es donde gestionas todos tus plugins, nya~ Puedes navegar, lanzar y ajustarlos para hacerme aún más poderosa.',
    hint: 'Tómate tu tiempo para explorar un poco, y luego pulsa el botón de abajo cuando termines~',
    complete: '¡Todo listo, meow~!',
    dismiss: 'Quizás luego~',
    keyboardSkipHint: 'Pulsa Enter o Espacio para ir al siguiente paso. Se activa 0,5 segundos después de iniciar cada paso.',
    steps: {
      start: {
        title: 'Empieza aquí',
        body: 'Usa este botón para repetir la guía cuando quieras. Si cambias el idioma mientras corre, la guía se actualiza.'
      },
      stats: {
        title: 'Resumen de plugins',
        body: 'Estas tarjetas muestran el total, los que están en ejecución, detenidos y con fallos.'
      },
      metrics: {
        title: 'Monitor de rendimiento',
        body: 'Aquí ves CPU, memoria, hilos y plugins activos. Útil para revisar galgame OCR o Agent si van lentos.'
      },
      server: {
        title: 'Información del servidor',
        body: 'Aquí puedes revisar la versión del SDK, el número de plugins y la hora de actualización.'
      },
      plugins: {
        title: 'Lista de plugins',
        body: 'Entra en Plugins a la izquierda para iniciar, detener, configurar plugins o abrir la UI y la guía de galgame_plugin.'
      },
      pluginWorkbench: {
        title: 'Área de plugins',
        body: 'Aquí se agrupan plugins normales, adaptadores y extensiones.'
      },
      pluginFilters: {
        title: 'Búsqueda y filtros',
        body: 'Filtra por nombre, estado, tipo o reglas avanzadas.'
      },
      pluginLayout: {
        title: 'Diseño de vista',
        body: 'Cambia entre lista, una columna, dos columnas y vista compacta. Con muchos plugins, dos columnas o compacta reducen el desplazamiento.'
      },
      pluginContextMenu: {
        title: 'Acciones con clic derecho',
        body: 'Haz clic derecho para abrir detalles, configuración, logs, UI o guía, y para ejecutar acciones rápidas.'
      },
      packageManager: {
        title: 'Gestor de paquetes',
        body: 'El gestor reutiliza tus filtros y selección para crear paquetes de un plugin o bundles, y también para manejar paquetes locales.'
      },
      packageOperations: {
        title: 'Operaciones de paquete',
        body: 'Empaqueta plugins seleccionados, individuales o todos; crea bundles; inspecciona y verifica paquetes; descomprime o analiza dependencias.'
      },
      pluginDetail: {
        title: 'Detalles del plugin',
        body: 'La página de detalle muestra la UI, la guía, la información básica, entradas, métricas, configuración y logs.'
      },
      pluginDetailActions: {
        title: 'Acciones del detalle',
        body: 'Las acciones superiores se aplican al plugin actual. Para galgame_plugin, primero confirma que está en ejecución.'
      },
      runs: {
        title: 'Ejecuciones',
        body: 'Las ejecuciones muestran el historial y el estado en vivo de tareas del plugin.'
      },
      runsList: {
        title: 'Lista de ejecuciones',
        body: 'Selecciona una ejecución a la izquierda o actualiza para sincronizar los registros más recientes.'
      },
      runsDetail: {
        title: 'Detalle de ejecución',
        body: 'El panel muestra fase, progreso, errores y exportaciones; cancelar solo aparece en tareas cancelables.'
      },
      logs: {
        title: 'Logs del servidor',
        body: 'Los logs del servidor ayudan a revisar la salida y los errores del servicio de plugins.'
      },
      logToolbar: {
        title: 'Filtros de logs',
        body: 'Filtra por nivel, palabra clave y líneas, o activa y desactiva el auto-scroll.'
      },
      logList: {
        title: 'Lista de logs',
        body: 'Los logs muestran hora, origen, nivel y mensaje para depurar problemas de plugins, OCR, Memory Reader o paquetes.'
      }
    }
  }
}
