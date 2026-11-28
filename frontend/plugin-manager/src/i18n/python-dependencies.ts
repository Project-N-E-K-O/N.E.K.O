// Loaded with the dependency dialog, not the plugin manager's initial bundle.
// Only pythonDependencies.startBlocked (shown by the request layer) stays in the locale files.
export const pythonDependencyMessages = {
  'zh-CN': {
    title: '安装运行依赖',
    introduction: '「{name}」需要先安装以下 Python 依赖才能启动。它们会从软件包下载源下载到插件目录。',
    columns: {
      name: '包名',
      version: '版本',
      size: '大小'
    },
    total: '共 {count} 个，约 {size}',
    totalPartial: '共 {count} 个，已知部分约 {size}，部分包大小未知',
    sizeUnknown: '未知',
    installAndStart: '安装并启动',
    failedTitle: '依赖没有安装成功，插件暂时无法启动。',
    details: '技术细节',
    phases: {
      planning: '正在读取依赖清单…',
      tools: '正在准备安装工具（首次使用需要下载）…',
      installing: '正在下载并安装依赖…',
      finishing: '正在完成安装…'
    },
    backgroundHint: '关闭此窗口不会中断安装，安装完成后再次点击「启动」即可。',
    errors: {
      tools_failed: '安装工具准备失败，请检查网络后重试。',
      lock_invalid: '插件的依赖锁文件无法读取，请联系插件作者。',
      network: '连接不上软件包下载源，请检查网络后重试。',
      not_on_index: '部分依赖在当前下载源中找不到，请联系插件作者。',
      no_wheel: '部分依赖没有适合本机的安装包，请联系插件作者。',
      hash_mismatch: '下载的文件没有通过校验，可能是网络或下载源异常，请稍后重试。',
      unpinned_source: '插件的依赖包含本地或 Git 来源，无法自动安装，请联系插件作者。',
      lock_changed: '插件在安装期间被更新了，请重试。',
      lock_mismatch: '插件的依赖锁文件和它声明的依赖对不上，请联系插件作者。',
      install_failed: '依赖安装失败，请重试。',
      not_applicable: '此插件不需要在这里安装依赖。',
      canceled: '安装已取消。',
      busy: '插件正在进行其他操作，请稍后重试。',
      internal_error: '安装时出现意外错误，请重试。'
    }
  },
  'zh-TW': {
    title: '安裝執行依賴',
    introduction: '「{name}」需要先安裝以下 Python 依賴才能啟動。它們會從套件下載來源下載到外掛目錄。',
    columns: {
      name: '套件名稱',
      version: '版本',
      size: '大小'
    },
    total: '共 {count} 個，約 {size}',
    totalPartial: '共 {count} 個，已知部分約 {size}，部分套件大小未知',
    sizeUnknown: '未知',
    installAndStart: '安裝並啟動',
    failedTitle: '依賴沒有安裝成功，外掛暫時無法啟動。',
    details: '技術細節',
    phases: {
      planning: '正在讀取依賴清單…',
      tools: '正在準備安裝工具（首次使用需要下載）…',
      installing: '正在下載並安裝依賴…',
      finishing: '正在完成安裝…'
    },
    backgroundHint: '關閉此視窗不會中斷安裝，安裝完成後再次點擊「啟動」即可。',
    errors: {
      tools_failed: '安裝工具準備失敗，請檢查網路後重試。',
      lock_invalid: '外掛的依賴鎖定檔無法讀取，請聯絡外掛作者。',
      network: '連不上套件下載來源，請檢查網路後重試。',
      not_on_index: '部分依賴在目前的下載來源中找不到，請聯絡外掛作者。',
      no_wheel: '部分依賴沒有適合本機的安裝套件，請聯絡外掛作者。',
      hash_mismatch: '下載的檔案沒有通過校驗，可能是網路或下載來源異常，請稍後重試。',
      unpinned_source: '外掛的依賴包含本機或 Git 來源，無法自動安裝，請聯絡外掛作者。',
      lock_changed: '外掛在安裝期間被更新了，請重試。',
      lock_mismatch: '外掛的依賴鎖定檔和它宣告的依賴對不上，請聯絡外掛作者。',
      install_failed: '依賴安裝失敗，請重試。',
      not_applicable: '此外掛不需要在這裡安裝依賴。',
      canceled: '安裝已取消。',
      busy: '外掛正在進行其他操作，請稍後重試。',
      internal_error: '安裝時發生意外錯誤，請重試。'
    }
  },
  'en-US': {
    title: 'Install dependencies',
    introduction: '"{name}" needs the following Python packages before it can start. They will be downloaded from the package index into the plugin\'s folder.',
    columns: {
      name: 'Package',
      version: 'Version',
      size: 'Size'
    },
    total: '{count} packages, about {size}',
    totalPartial: '{count} packages, about {size} known; some sizes are unknown',
    sizeUnknown: 'Unknown',
    installAndStart: 'Install and start',
    failedTitle: 'The dependencies were not installed, so the plugin cannot start yet.',
    details: 'Technical details',
    phases: {
      planning: 'Reading the dependency list…',
      tools: 'Preparing the installer (downloaded on first use)…',
      installing: 'Downloading and installing dependencies…',
      finishing: 'Finishing the installation…'
    },
    backgroundHint: 'Closing this window does not stop the installation. Click Start again once it has finished.',
    errors: {
      tools_failed: 'The installer could not be prepared. Check your network connection and try again.',
      lock_invalid: 'The plugin\'s dependency lock file cannot be read. Please contact the plugin author.',
      network: 'The package index cannot be reached. Check your network connection and try again.',
      not_on_index: 'Some dependencies are not available from the current package index. Please contact the plugin author.',
      no_wheel: 'Some dependencies have no package for this computer. Please contact the plugin author.',
      hash_mismatch: 'A downloaded file failed its integrity check, possibly due to the network or the package index. Please try again later.',
      unpinned_source: 'The plugin depends on local or Git sources, which cannot be installed automatically. Please contact the plugin author.',
      lock_changed: 'The plugin was updated during the installation. Please try again.',
      lock_mismatch: 'The plugin\'s lock file does not match the dependencies it declares. Please contact the plugin author.',
      install_failed: 'The dependencies could not be installed. Please try again.',
      not_applicable: 'This plugin does not need its dependencies installed here.',
      canceled: 'The installation was canceled.',
      busy: 'Another operation on plugins is in progress. Please try again in a moment.',
      internal_error: 'An unexpected error occurred during the installation. Please try again.'
    }
  },
  'ja': {
    title: '実行に必要な依存関係のインストール',
    introduction: '「{name}」を起動するには、次の Python パッケージを先にインストールする必要があります。パッケージ配布元からプラグインのフォルダーにダウンロードされます。',
    columns: {
      name: 'パッケージ',
      version: 'バージョン',
      size: 'サイズ'
    },
    total: '{count} 個、約 {size}',
    totalPartial: '{count} 個、判明分だけで約 {size}（一部のサイズは不明）',
    sizeUnknown: '不明',
    installAndStart: 'インストールして起動',
    failedTitle: '依存関係をインストールできなかったため、プラグインはまだ起動できません。',
    details: '技術的な詳細',
    phases: {
      planning: '依存関係の一覧を読み込んでいます…',
      tools: 'インストーラーを準備しています（初回はダウンロードが必要です）…',
      installing: '依存関係をダウンロードしてインストールしています…',
      finishing: 'インストールを完了しています…'
    },
    backgroundHint: 'このウィンドウを閉じてもインストールは止まりません。完了したら、もう一度「起動」をクリックしてください。',
    errors: {
      tools_failed: 'インストーラーを準備できませんでした。ネットワークを確認して再試行してください。',
      lock_invalid: 'プラグインの依存関係ロックファイルを読み込めません。プラグインの作者に連絡してください。',
      network: 'パッケージ配布元に接続できません。ネットワークを確認して再試行してください。',
      not_on_index: '一部の依存関係が現在のパッケージ配布元にありません。プラグインの作者に連絡してください。',
      no_wheel: '一部の依存関係に、このコンピューター向けのパッケージがありません。プラグインの作者に連絡してください。',
      hash_mismatch: 'ダウンロードしたファイルの検証に失敗しました。ネットワークまたは配布元の問題の可能性があります。しばらくしてから再試行してください。',
      unpinned_source: 'プラグインの依存関係にローカルまたは Git のソースが含まれているため、自動ではインストールできません。プラグインの作者に連絡してください。',
      lock_changed: 'インストール中にプラグインが更新されました。再試行してください。',
      lock_mismatch: 'プラグインのロックファイルが、宣言されている依存関係と一致しません。プラグインの作者に連絡してください。',
      install_failed: '依存関係をインストールできませんでした。再試行してください。',
      not_applicable: 'このプラグインは、ここで依存関係をインストールする必要はありません。',
      canceled: 'インストールはキャンセルされました。',
      busy: 'プラグインで別の操作が進行中です。しばらくしてから再試行してください。',
      internal_error: 'インストール中に予期しないエラーが発生しました。再試行してください。'
    }
  },
  'ko': {
    title: '실행 의존성 설치',
    introduction: '"{name}"을(를) 시작하려면 다음 Python 패키지를 먼저 설치해야 합니다. 패키지 저장소에서 플러그인 폴더로 다운로드됩니다.',
    columns: {
      name: '패키지',
      version: '버전',
      size: '크기'
    },
    total: '총 {count}개, 약 {size}',
    totalPartial: '총 {count}개, 확인된 크기만 약 {size} (일부 크기는 알 수 없음)',
    sizeUnknown: '알 수 없음',
    installAndStart: '설치 후 시작',
    failedTitle: '의존성을 설치하지 못해 플러그인을 아직 시작할 수 없습니다.',
    details: '기술 세부 정보',
    phases: {
      planning: '의존성 목록을 읽는 중…',
      tools: '설치 도구를 준비하는 중(처음 사용할 때 다운로드됩니다)…',
      installing: '의존성을 다운로드하고 설치하는 중…',
      finishing: '설치를 마무리하는 중…'
    },
    backgroundHint: '이 창을 닫아도 설치는 중단되지 않습니다. 설치가 끝나면 다시 "시작"을 클릭하세요.',
    errors: {
      tools_failed: '설치 도구를 준비하지 못했습니다. 네트워크를 확인한 뒤 다시 시도하세요.',
      lock_invalid: '플러그인의 의존성 잠금 파일을 읽을 수 없습니다. 플러그인 작성자에게 문의하세요.',
      network: '패키지 저장소에 연결할 수 없습니다. 네트워크를 확인한 뒤 다시 시도하세요.',
      not_on_index: '일부 의존성을 현재 패키지 저장소에서 찾을 수 없습니다. 플러그인 작성자에게 문의하세요.',
      no_wheel: '일부 의존성에 이 컴퓨터용 패키지가 없습니다. 플러그인 작성자에게 문의하세요.',
      hash_mismatch: '다운로드한 파일이 무결성 검사를 통과하지 못했습니다. 네트워크나 패키지 저장소 문제일 수 있습니다. 잠시 후 다시 시도하세요.',
      unpinned_source: '플러그인 의존성에 로컬 또는 Git 소스가 있어 자동으로 설치할 수 없습니다. 플러그인 작성자에게 문의하세요.',
      lock_changed: '설치하는 동안 플러그인이 업데이트되었습니다. 다시 시도하세요.',
      lock_mismatch: '플러그인의 잠금 파일이 선언한 의존성과 일치하지 않습니다. 플러그인 작성자에게 문의하세요.',
      install_failed: '의존성을 설치하지 못했습니다. 다시 시도하세요.',
      not_applicable: '이 플러그인은 여기서 의존성을 설치할 필요가 없습니다.',
      canceled: '설치가 취소되었습니다.',
      busy: '플러그인에서 다른 작업이 진행 중입니다. 잠시 후 다시 시도하세요.',
      internal_error: '설치 중 예기치 않은 오류가 발생했습니다. 다시 시도하세요.'
    }
  },
  'ru': {
    title: 'Установка зависимостей',
    introduction: 'Чтобы запустить «{name}», сначала нужно установить следующие пакеты Python. Они будут загружены из каталога пакетов в папку плагина.',
    columns: {
      name: 'Пакет',
      version: 'Версия',
      size: 'Размер'
    },
    total: 'Всего {count}, около {size}',
    totalPartial: 'Всего {count}; известная часть — около {size}, размер некоторых пакетов неизвестен',
    sizeUnknown: 'Неизвестно',
    installAndStart: 'Установить и запустить',
    failedTitle: 'Зависимости не установлены, поэтому плагин пока нельзя запустить.',
    details: 'Технические подробности',
    phases: {
      planning: 'Чтение списка зависимостей…',
      tools: 'Подготовка установщика (при первом использовании он загружается)…',
      installing: 'Загрузка и установка зависимостей…',
      finishing: 'Завершение установки…'
    },
    backgroundHint: 'Закрытие этого окна не прерывает установку. Когда она закончится, снова нажмите «Запустить».',
    errors: {
      tools_failed: 'Не удалось подготовить установщик. Проверьте подключение к сети и повторите попытку.',
      lock_invalid: 'Не удаётся прочитать файл блокировки зависимостей плагина. Обратитесь к автору плагина.',
      network: 'Нет связи с каталогом пакетов. Проверьте подключение к сети и повторите попытку.',
      not_on_index: 'Некоторых зависимостей нет в текущем каталоге пакетов. Обратитесь к автору плагина.',
      no_wheel: 'Для некоторых зависимостей нет пакета под этот компьютер. Обратитесь к автору плагина.',
      hash_mismatch: 'Загруженный файл не прошёл проверку целостности — возможно, из-за сети или каталога пакетов. Повторите попытку позже.',
      unpinned_source: 'Плагин зависит от локальных источников или Git, их нельзя установить автоматически. Обратитесь к автору плагина.',
      lock_changed: 'Плагин обновился во время установки. Повторите попытку.',
      lock_mismatch: 'Файл блокировки плагина не соответствует объявленным зависимостям. Обратитесь к автору плагина.',
      install_failed: 'Не удалось установить зависимости. Повторите попытку.',
      not_applicable: 'Этому плагину не нужно устанавливать зависимости здесь.',
      canceled: 'Установка отменена.',
      busy: 'С плагинами сейчас выполняется другая операция. Повторите попытку чуть позже.',
      internal_error: 'Во время установки произошла непредвиденная ошибка. Повторите попытку.'
    }
  },
  'es': {
    title: 'Instalar dependencias',
    introduction: 'Para iniciar «{name}» primero hay que instalar los siguientes paquetes de Python. Se descargarán del índice de paquetes a la carpeta del complemento.',
    columns: {
      name: 'Paquete',
      version: 'Versión',
      size: 'Tamaño'
    },
    total: '{count} paquetes, unos {size}',
    totalPartial: '{count} paquetes; la parte conocida ocupa unos {size} y algunos tamaños se desconocen',
    sizeUnknown: 'Desconocido',
    installAndStart: 'Instalar e iniciar',
    failedTitle: 'Las dependencias no se instalaron, así que el complemento todavía no puede iniciarse.',
    details: 'Detalles técnicos',
    phases: {
      planning: 'Leyendo la lista de dependencias…',
      tools: 'Preparando el instalador (se descarga la primera vez)…',
      installing: 'Descargando e instalando dependencias…',
      finishing: 'Terminando la instalación…'
    },
    backgroundHint: 'Cerrar esta ventana no detiene la instalación. Cuando termine, vuelve a pulsar Iniciar.',
    errors: {
      tools_failed: 'No se pudo preparar el instalador. Comprueba la conexión de red y vuelve a intentarlo.',
      lock_invalid: 'No se puede leer el archivo de bloqueo de dependencias del complemento. Contacta con su autor.',
      network: 'No se puede conectar con el índice de paquetes. Comprueba la conexión de red y vuelve a intentarlo.',
      not_on_index: 'Algunas dependencias no están en el índice de paquetes actual. Contacta con el autor del complemento.',
      no_wheel: 'Algunas dependencias no tienen paquete para este equipo. Contacta con el autor del complemento.',
      hash_mismatch: 'Un archivo descargado no superó la comprobación de integridad, quizá por la red o el índice de paquetes. Vuelve a intentarlo más tarde.',
      unpinned_source: 'El complemento depende de fuentes locales o de Git, que no se pueden instalar automáticamente. Contacta con su autor.',
      lock_changed: 'El complemento se actualizó durante la instalación. Vuelve a intentarlo.',
      lock_mismatch: 'El archivo de bloqueo del complemento no coincide con las dependencias que declara. Contacta con su autor.',
      install_failed: 'No se pudieron instalar las dependencias. Vuelve a intentarlo.',
      not_applicable: 'Este complemento no necesita instalar sus dependencias aquí.',
      canceled: 'La instalación se canceló.',
      busy: 'Hay otra operación de complementos en curso. Vuelve a intentarlo en un momento.',
      internal_error: 'Se produjo un error inesperado durante la instalación. Vuelve a intentarlo.'
    }
  },
  'pt': {
    title: 'Instalar dependências',
    introduction: 'Para iniciar "{name}", primeiro é preciso instalar os seguintes pacotes Python. Eles serão baixados do índice de pacotes para a pasta do plugin.',
    columns: {
      name: 'Pacote',
      version: 'Versão',
      size: 'Tamanho'
    },
    total: '{count} pacotes, cerca de {size}',
    totalPartial: '{count} pacotes; a parte conhecida tem cerca de {size} e alguns tamanhos são desconhecidos',
    sizeUnknown: 'Desconhecido',
    installAndStart: 'Instalar e iniciar',
    failedTitle: 'As dependências não foram instaladas, então o plugin ainda não pode ser iniciado.',
    details: 'Detalhes técnicos',
    phases: {
      planning: 'Lendo a lista de dependências…',
      tools: 'Preparando o instalador (baixado no primeiro uso)…',
      installing: 'Baixando e instalando dependências…',
      finishing: 'Concluindo a instalação…'
    },
    backgroundHint: 'Fechar esta janela não interrompe a instalação. Quando terminar, clique em Iniciar novamente.',
    errors: {
      tools_failed: 'Não foi possível preparar o instalador. Verifique a conexão de rede e tente novamente.',
      lock_invalid: 'O arquivo de bloqueio de dependências do plugin não pode ser lido. Entre em contato com o autor do plugin.',
      network: 'Não foi possível acessar o índice de pacotes. Verifique a conexão de rede e tente novamente.',
      not_on_index: 'Algumas dependências não estão disponíveis no índice de pacotes atual. Entre em contato com o autor do plugin.',
      no_wheel: 'Algumas dependências não têm pacote para este computador. Entre em contato com o autor do plugin.',
      hash_mismatch: 'Um arquivo baixado não passou na verificação de integridade, talvez por causa da rede ou do índice de pacotes. Tente novamente mais tarde.',
      unpinned_source: 'O plugin depende de fontes locais ou Git, que não podem ser instaladas automaticamente. Entre em contato com o autor do plugin.',
      lock_changed: 'O plugin foi atualizado durante a instalação. Tente novamente.',
      lock_mismatch: 'O arquivo de bloqueio do plugin não corresponde às dependências que ele declara. Entre em contato com o autor do plugin.',
      install_failed: 'Não foi possível instalar as dependências. Tente novamente.',
      not_applicable: 'Este plugin não precisa instalar dependências aqui.',
      canceled: 'A instalação foi cancelada.',
      busy: 'Outra operação com plugins está em andamento. Tente novamente em instantes.',
      internal_error: 'Ocorreu um erro inesperado durante a instalação. Tente novamente.'
    }
  },
} as const
