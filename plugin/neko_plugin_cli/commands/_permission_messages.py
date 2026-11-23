"""Messages for plugin.toml permission declarations and the permission scan.

Every message exists in all eight locales; ``texts(code, **params)`` returns
the per-locale strings for ``validate_cmd._format_multilingual``.
"""

from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    "table_type": {
        "en": "{label} must be a table",
        "zh-CN": "{label} 必须是一个表",
        "zh-TW": "{label} 必須是一個表",
        "ja": "{label} はテーブルにしてください",
        "ko": "{label}은(는) 테이블이어야 합니다",
        "ru": "{label} должно быть таблицей",
        "es": "{label} debe ser una tabla",
        "pt": "{label} deve ser uma tabela",
    },
    "unknown_section": {
        "en": "{label} is not a recognized permission section; use [permissions.require] or [permissions.ask]",
        "zh-CN": "{label} 不是可识别的权限分组，请使用 [permissions.require] 或 [permissions.ask]",
        "zh-TW": "{label} 不是可辨識的權限分組，請使用 [permissions.require] 或 [permissions.ask]",
        "ja": "{label} は認識できない権限セクションです。[permissions.require] か [permissions.ask] を使ってください",
        "ko": "{label}은(는) 알 수 없는 권한 섹션입니다. [permissions.require] 또는 [permissions.ask]를 사용하세요",
        "ru": "{label} — неизвестный раздел разрешений; используйте [permissions.require] или [permissions.ask]",
        "es": "{label} no es una sección de permisos reconocida; usa [permissions.require] o [permissions.ask]",
        "pt": "{label} não é uma seção de permissões reconhecida; use [permissions.require] ou [permissions.ask]",
    },
    "unknown_permission": {
        "en": "{label} is not a known permission id. Known ids: {known}",
        "zh-CN": "{label} 不是已知的权限 id。已知的 id：{known}",
        "zh-TW": "{label} 不是已知的權限 id。已知的 id：{known}",
        "ja": "{label} は既知の権限 ID ではありません。既知の ID：{known}",
        "ko": "{label}은(는) 알려진 권한 ID가 아닙니다. 알려진 ID: {known}",
        "ru": "{label} — неизвестный идентификатор разрешения. Известные идентификаторы: {known}",
        "es": "{label} no es un id de permiso conocido. Ids conocidos: {known}",
        "pt": "{label} não é um id de permissão conhecido. Ids conhecidos: {known}",
    },
    "duplicate_permission": {
        "en": '"{permission}" is declared in both [permissions.require] and [permissions.ask]; keep only one',
        "zh-CN": '"{permission}" 同时声明在 [permissions.require] 和 [permissions.ask] 里，只保留一处',
        "zh-TW": '"{permission}" 同時宣告在 [permissions.require] 和 [permissions.ask] 裡，只保留一處',
        "ja": '"{permission}" が [permissions.require] と [permissions.ask] の両方で宣言されています。どちらか一方だけにしてください',
        "ko": '"{permission}"이(가) [permissions.require]와 [permissions.ask]에 모두 선언되어 있습니다. 한 곳에만 두세요',
        "ru": '"{permission}" объявлено и в [permissions.require], и в [permissions.ask]; оставьте только одно',
        "es": '"{permission}" está declarado en [permissions.require] y en [permissions.ask]; deja solo uno',
        "pt": '"{permission}" está declarado em [permissions.require] e em [permissions.ask]; mantenha apenas um',
    },
    "entry_type": {
        "en": '{label} must be a table such as {{ reason = "..." }}',
        "zh-CN": '{label} 必须是一个表，例如 {{ reason = "..." }}',
        "zh-TW": '{label} 必須是一個表，例如 {{ reason = "..." }}',
        "ja": '{label} は {{ reason = "..." }} のようなテーブルにしてください',
        "ko": '{label}은(는) {{ reason = "..." }} 같은 테이블이어야 합니다',
        "ru": '{label} должно быть таблицей, например {{ reason = "..." }}',
        "es": '{label} debe ser una tabla como {{ reason = "..." }}',
        "pt": '{label} deve ser uma tabela como {{ reason = "..." }}',
    },
    "unknown_field": {
        "en": "{label} is not a recognized permission field and is ignored",
        "zh-CN": "{label} 不是可识别的权限字段，会被忽略",
        "zh-TW": "{label} 不是可辨識的權限欄位，會被忽略",
        "ja": "{label} は認識できない権限フィールドのため無視されます",
        "ko": "{label}은(는) 알 수 없는 권한 필드라서 무시됩니다",
        "ru": "{label} — неизвестное поле разрешения, оно игнорируется",
        "es": "{label} no es un campo de permiso reconocido y se ignora",
        "pt": "{label} não é um campo de permissão reconhecido e é ignorado",
    },
    "reason_missing": {
        "en": "{label} needs a reason: it is the sentence users see next to this permission",
        "zh-CN": "{label} 缺少 reason：这是用户在这项权限旁边看到的那句说明",
        "zh-TW": "{label} 缺少 reason：這是使用者在這項權限旁邊看到的那句說明",
        "ja": "{label} に reason がありません。ユーザーがこの権限の横で読む説明文です",
        "ko": "{label}에 reason이 없습니다. 사용자가 이 권한 옆에서 보게 되는 설명입니다",
        "ru": "{label} требует reason: это фраза, которую пользователь видит рядом с разрешением",
        "es": "{label} necesita un reason: es la frase que el usuario ve junto a este permiso",
        "pt": "{label} precisa de um reason: é a frase que o usuário vê ao lado desta permissão",
    },
    "text_empty": {
        "en": "{label} must not be empty: write a string, or a table of languages with at least one non-empty text",
        "zh-CN": "{label} 不能为空：写一个字符串，或者写一个语言表，至少有一种语言的内容不为空",
        "zh-TW": "{label} 不能為空：寫一個字串，或者寫一個語言表，至少有一種語言的內容不為空",
        "ja": "{label} は空にできません。文字列か、少なくとも 1 つの言語が空でない言語別テーブルを書いてください",
        "ko": "{label}은(는) 비워 둘 수 없습니다. 문자열이나, 적어도 한 언어는 내용이 있는 언어별 테이블을 쓰세요",
        "ru": "{label} не может быть пустым: укажите строку или таблицу языков, где хотя бы один текст не пуст",
        "es": "{label} no puede estar vacío: escribe una cadena o una tabla de idiomas con al menos un texto no vacío",
        "pt": "{label} não pode estar vazio: escreva uma string ou uma tabela de idiomas com pelo menos um texto não vazio",
    },
    "text_type": {
        "en": '{label} must be a string or a table of language code to text, such as {{ en = "...", ja = "..." }}',
        "zh-CN": '{label} 必须是字符串，或者“语言代码 = 文字”的表，例如 {{ en = "...", ja = "..." }}',
        "zh-TW": '{label} 必須是字串，或者「語言代碼 = 文字」的表，例如 {{ en = "...", ja = "..." }}',
        "ja": '{label} は文字列か、{{ en = "...", ja = "..." }} のような「言語コード = テキスト」のテーブルにしてください',
        "ko": '{label}은(는) 문자열이거나 {{ en = "...", ja = "..." }} 같은 "언어 코드 = 텍스트" 테이블이어야 합니다',
        "ru": '{label} должно быть строкой или таблицей «код языка = текст», например {{ en = "...", ja = "..." }}',
        "es": '{label} debe ser una cadena o una tabla de código de idioma a texto, como {{ en = "...", ja = "..." }}',
        "pt": '{label} deve ser uma string ou uma tabela de código de idioma para texto, como {{ en = "...", ja = "..." }}',
    },
    "text_translation_type": {
        "en": "{label} must be a string",
        "zh-CN": "{label} 必须是字符串",
        "zh-TW": "{label} 必須是字串",
        "ja": "{label} は文字列にしてください",
        "ko": "{label}은(는) 문자열이어야 합니다",
        "ru": "{label} должно быть строкой",
        "es": "{label} debe ser una cadena",
        "pt": "{label} deve ser uma string",
    },
    "text_translation_empty": {
        "en": "{label} is empty and is ignored; users of that language see another language that is written",
        "zh-CN": "{label} 为空，会被忽略；使用这种语言的用户会看到已写好的其他语言",
        "zh-TW": "{label} 為空，會被忽略；使用這種語言的使用者會看到已寫好的其他語言",
        "ja": "{label} は空なので無視されます。この言語のユーザーには、書かれている別の言語が表示されます",
        "ko": "{label}이(가) 비어 있어 무시됩니다. 이 언어 사용자에게는 작성된 다른 언어가 표시됩니다",
        "ru": "{label} пусто и игнорируется; пользователи этого языка увидят текст на другом заполненном языке",
        "es": "{label} está vacío y se ignora; los usuarios de ese idioma verán otro idioma que sí está escrito",
        "pt": "{label} está vazio e é ignorado; os usuários desse idioma verão outro idioma que esteja escrito",
    },
    "background_type": {
        "en": "{label} must be true or false",
        "zh-CN": "{label} 必须是 true 或 false",
        "zh-TW": "{label} 必須是 true 或 false",
        "ja": "{label} は true か false にしてください",
        "ko": "{label}은(는) true 또는 false여야 합니다",
        "ru": "{label} должно быть true или false",
        "es": "{label} debe ser true o false",
        "pt": "{label} deve ser true ou false",
    },
    "background_on_require": {
        "en": "{label} has no effect on a require permission: agreeing to a require permission already covers background use",
        "zh-CN": "{label} 对 require 权限没有效果：用户同意 require 权限时已经包括了后台使用",
        "zh-TW": "{label} 對 require 權限沒有效果：使用者同意 require 權限時已經包括了背景使用",
        "ja": "{label} は require 権限には効果がありません。require 権限への同意にはバックグラウンドでの使用も含まれます",
        "ko": "{label}은(는) require 권한에는 효과가 없습니다. require 권한에 동의하면 백그라운드 사용도 포함됩니다",
        "ru": "{label} не действует для разрешения require: согласие на require уже включает фоновое использование",
        "es": "{label} no tiene efecto en un permiso require: aceptar un permiso require ya cubre el uso en segundo plano",
        "pt": "{label} não tem efeito em uma permissão require: aceitar uma permissão require já cobre o uso em segundo plano",
    },
    "background_reason_missing": {
        "en": "{label} sets background = true, so it also needs a background_reason",
        "zh-CN": "{label} 设置了 background = true，还需要写 background_reason",
        "zh-TW": "{label} 設定了 background = true，還需要寫 background_reason",
        "ja": "{label} は background = true なので、background_reason も必要です",
        "ko": "{label}에 background = true를 설정했으므로 background_reason도 필요합니다",
        "ru": "{label} задаёт background = true, поэтому нужен и background_reason",
        "es": "{label} define background = true, así que también necesita un background_reason",
        "pt": "{label} define background = true, então também precisa de um background_reason",
    },
    "background_reason_unused": {
        "en": "{label} has no effect without background = true",
        "zh-CN": "{label} 在没有 background = true 时没有效果",
        "zh-TW": "{label} 在沒有 background = true 時沒有效果",
        "ja": "{label} は background = true がないと効果がありません",
        "ko": "{label}은(는) background = true가 없으면 효과가 없습니다",
        "ru": "{label} не действует без background = true",
        "es": "{label} no tiene efecto sin background = true",
        "pt": "{label} não tem efeito sem background = true",
    },
    "hosts_type": {
        "en": "{label} must be a list of non-empty host names",
        "zh-CN": "{label} 必须是由非空主机名组成的列表",
        "zh-TW": "{label} 必須是由非空主機名稱組成的清單",
        "ja": "{label} は空でないホスト名のリストにしてください",
        "ko": "{label}은(는) 비어 있지 않은 호스트 이름의 목록이어야 합니다",
        "ru": "{label} должно быть списком непустых имён хостов",
        "es": "{label} debe ser una lista de nombres de host no vacíos",
        "pt": "{label} deve ser uma lista de nomes de host não vazios",
    },
    "hosts_unused": {
        "en": "{label} only applies to system.network:connect and is ignored here",
        "zh-CN": "{label} 只对 system.network:connect 有意义，这里会被忽略",
        "zh-TW": "{label} 只對 system.network:connect 有意義，這裡會被忽略",
        "ja": "{label} は system.network:connect にのみ有効で、ここでは無視されます",
        "ko": "{label}은(는) system.network:connect에만 적용되며 여기서는 무시됩니다",
        "ru": "{label} относится только к system.network:connect и здесь игнорируется",
        "es": "{label} solo se aplica a system.network:connect y aquí se ignora",
        "pt": "{label} só se aplica a system.network:connect e aqui é ignorado",
    },
    "narrowing_type": {
        "en": "{label}: permissions= must be a list of permission id strings",
        "zh-CN": "{label}：permissions= 必须是由权限 id 字符串组成的列表",
        "zh-TW": "{label}：permissions= 必須是由權限 id 字串組成的清單",
        "ja": "{label}：permissions= は権限 ID 文字列のリストにしてください",
        "ko": "{label}: permissions=는 권한 ID 문자열의 목록이어야 합니다",
        "ru": "{label}: permissions= должно быть списком строк с идентификаторами разрешений",
        "es": "{label}: permissions= debe ser una lista de cadenas con ids de permiso",
        "pt": "{label}: permissions= deve ser uma lista de strings com ids de permissão",
    },
    "narrowing_not_declared": {
        "en": '{label}: permissions= lists "{permission}", which plugin.toml does not declare in [permissions.require] or [permissions.ask]',
        "zh-CN": '{label}：permissions= 里的 "{permission}" 没有在 plugin.toml 的 [permissions.require] 或 [permissions.ask] 里声明',
        "zh-TW": '{label}：permissions= 裡的 "{permission}" 沒有在 plugin.toml 的 [permissions.require] 或 [permissions.ask] 裡宣告',
        "ja": '{label}：permissions= の "{permission}" は plugin.toml の [permissions.require] にも [permissions.ask] にも宣言されていません',
        "ko": '{label}: permissions=의 "{permission}"이(가) plugin.toml의 [permissions.require]나 [permissions.ask]에 선언되어 있지 않습니다',
        "ru": '{label}: permissions= содержит "{permission}", но plugin.toml не объявляет его в [permissions.require] или [permissions.ask]',
        "es": '{label}: permissions= incluye "{permission}", que plugin.toml no declara en [permissions.require] ni en [permissions.ask]',
        "pt": '{label}: permissions= inclui "{permission}", que o plugin.toml não declara em [permissions.require] nem em [permissions.ask]',
    },
    "narrowing_unconfirmed": {
        "en": "{label}: permissions= is not a literal list, so it cannot be confirmed statically",
        "zh-CN": "{label}：permissions= 不是字面量列表，无法静态确认",
        "zh-TW": "{label}：permissions= 不是字面值清單，無法靜態確認",
        "ja": "{label}：permissions= がリテラルのリストではないため、静的に確認できません",
        "ko": "{label}: permissions=가 리터럴 목록이 아니어서 정적으로 확인할 수 없습니다",
        "ru": "{label}: permissions= не является литеральным списком, поэтому его нельзя проверить статически",
        "es": "{label}: permissions= no es una lista literal, así que no se puede confirmar de forma estática",
        "pt": "{label}: permissions= não é uma lista literal, então não pode ser confirmado estaticamente",
    },
    "narrowing_unsupported_decorator": {
        "en": "{label}: this decorator does not take permissions=, so the plugin fails to load; only component "
        "decorators (entries, LLM tools, entry hooks, event handlers) can narrow permissions",
        "zh-CN": "{label}：这个装饰器不接受 permissions= 参数，插件加载时会报错；只有组件装饰器（入口、LLM 工具、入口 hook、"
        "事件处理）可以收窄权限",
        "zh-TW": "{label}：這個裝飾器不接受 permissions= 參數，外掛載入時會報錯；只有元件裝飾器（入口、LLM 工具、入口 hook、"
        "事件處理）可以收窄權限",
        "ja": "{label}：このデコレーターは permissions= を受け付けないため、プラグインの読み込み時にエラーになります。"
        "権限を絞り込めるのはコンポーネントのデコレーター（エントリ、LLM ツール、エントリフック、イベントハンドラー）だけです",
        "ko": "{label}: 이 데코레이터는 permissions=를 받지 않아 플러그인을 불러올 때 오류가 납니다. "
        "권한을 좁힐 수 있는 것은 컴포넌트 데코레이터(엔트리, LLM 도구, 엔트리 훅, 이벤트 핸들러)뿐입니다",
        "ru": "{label}: этот декоратор не принимает permissions=, поэтому плагин не загрузится; сузить разрешения "
        "можно только в декораторах компонентов (точки входа, LLM-инструменты, хуки точек входа, обработчики событий)",
        "es": "{label}: este decorador no acepta permissions=, así que el plugin falla al cargarse; solo los decoradores "
        "de componentes (entradas, herramientas LLM, hooks de entrada, manejadores de eventos) pueden acotar permisos",
        "pt": "{label}: este decorador não aceita permissions=, então o plugin falha ao carregar; só os decoradores "
        "de componentes (entradas, ferramentas LLM, hooks de entrada, manipuladores de eventos) podem restringir permissões",
    },
    "used_undeclared": {
        "en": "the code uses {permission} ({locations}) but plugin.toml does not declare it; "
        "add it to [permissions.require] or [permissions.ask] with a reason",
        "zh-CN": "代码用到了 {permission}（{locations}），但 plugin.toml 没有声明；"
        "请把它加到 [permissions.require] 或 [permissions.ask] 并写上 reason",
        "zh-TW": "程式碼用到了 {permission}（{locations}），但 plugin.toml 沒有宣告；"
        "請把它加到 [permissions.require] 或 [permissions.ask] 並寫上 reason",
        "ja": "コードが {permission} を使っています（{locations}）が、plugin.toml で宣言されていません。"
        "reason を付けて [permissions.require] か [permissions.ask] に追加してください",
        "ko": "코드가 {permission}을(를) 사용하지만({locations}) plugin.toml에 선언되어 있지 않습니다. "
        "reason과 함께 [permissions.require] 또는 [permissions.ask]에 추가하세요",
        "ru": "код использует {permission} ({locations}), но plugin.toml его не объявляет; "
        "добавьте его в [permissions.require] или [permissions.ask] с reason",
        "es": "el código usa {permission} ({locations}) pero plugin.toml no lo declara; "
        "añádelo a [permissions.require] o [permissions.ask] con un reason",
        "pt": "o código usa {permission} ({locations}), mas o plugin.toml não o declara; "
        "adicione-o a [permissions.require] ou [permissions.ask] com um reason",
    },
    "used_undeclared_no_table": {
        "en": "the code uses {permission} ({locations}) but plugin.toml has no [permissions] table yet; "
        "this will become an error in a later release, so add it to [permissions.require] or [permissions.ask] with a reason",
        "zh-CN": "代码用到了 {permission}（{locations}），但 plugin.toml 还没有 [permissions] 表；"
        "以后的版本会把这里变成 error，请把它加到 [permissions.require] 或 [permissions.ask] 并写上 reason",
        "zh-TW": "程式碼用到了 {permission}（{locations}），但 plugin.toml 還沒有 [permissions] 表；"
        "之後的版本會把這裡變成 error，請把它加到 [permissions.require] 或 [permissions.ask] 並寫上 reason",
        "ja": "コードが {permission} を使っています（{locations}）が、plugin.toml にはまだ [permissions] テーブルがありません。"
        "今後のリリースでエラーになるため、reason を付けて [permissions.require] か [permissions.ask] に追加してください",
        "ko": "코드가 {permission}을(를) 사용하지만({locations}) plugin.toml에 아직 [permissions] 테이블이 없습니다. "
        "이후 릴리스에서 오류가 되므로 reason과 함께 [permissions.require] 또는 [permissions.ask]에 추가하세요",
        "ru": "код использует {permission} ({locations}), но в plugin.toml ещё нет таблицы [permissions]; "
        "в одном из следующих выпусков это станет ошибкой, поэтому добавьте его в [permissions.require] или [permissions.ask] с reason",
        "es": "el código usa {permission} ({locations}) pero plugin.toml todavía no tiene una tabla [permissions]; "
        "en una versión posterior esto será un error, así que añádelo a [permissions.require] o [permissions.ask] con un reason",
        "pt": "o código usa {permission} ({locations}), mas o plugin.toml ainda não tem uma tabela [permissions]; "
        "em uma versão futura isso será um erro, então adicione-o a [permissions.require] ou [permissions.ask] com um reason",
    },
    "used_undeclared_possible": {
        "en": "the code may use {permission} ({locations}) but plugin.toml does not declare it; "
        "the scan is not certain here, so declare it if the plugin really does this",
        "zh-CN": "代码可能用到了 {permission}（{locations}），但 plugin.toml 没有声明；"
        "扫描在这里不能确定，如果插件确实这样做，请声明它",
        "zh-TW": "程式碼可能用到了 {permission}（{locations}），但 plugin.toml 沒有宣告；"
        "掃描在這裡無法確定，如果外掛確實這樣做，請宣告它",
        "ja": "コードが {permission} を使っている可能性があります（{locations}）が、plugin.toml で宣言されていません。"
        "スキャンでは確定できないため、実際に使う場合は宣言してください",
        "ko": "코드가 {permission}을(를) 사용할 수 있지만({locations}) plugin.toml에 선언되어 있지 않습니다. "
        "스캔으로는 확실하지 않으니 실제로 사용한다면 선언하세요",
        "ru": "код, возможно, использует {permission} ({locations}), но plugin.toml его не объявляет; "
        "сканирование здесь не уверено, объявите его, если плагин действительно это делает",
        "es": "el código podría usar {permission} ({locations}) pero plugin.toml no lo declara; "
        "el análisis no está seguro aquí, así que decláralo si el plugin realmente lo hace",
        "pt": "o código pode usar {permission} ({locations}), mas o plugin.toml não o declara; "
        "a análise não tem certeza aqui, então declare-o se o plugin realmente fizer isso",
    },
    "declared_unused": {
        "en": '"{permission}" is declared but the scan found no use of it; this is fine if it is used in ways the scan cannot see',
        "zh-CN": '"{permission}" 已声明，但扫描没有发现用到它；如果是扫描看不到的用法，可以忽略',
        "zh-TW": '"{permission}" 已宣告，但掃描沒有發現用到它；如果是掃描看不到的用法，可以忽略',
        "ja": '"{permission}" は宣言されていますが、スキャンでは使用が見つかりませんでした。スキャンで見えない使い方なら問題ありません',
        "ko": '"{permission}"이(가) 선언되어 있지만 스캔에서 사용처를 찾지 못했습니다. 스캔으로 볼 수 없는 방식으로 쓴다면 괜찮습니다',
        "ru": '"{permission}" объявлено, но сканирование не нашло его использования; это нормально, если оно используется так, что сканирование этого не видит',
        "es": '"{permission}" está declarado pero el análisis no encontró ningún uso; no pasa nada si se usa de formas que el análisis no ve',
        "pt": '"{permission}" está declarado, mas a análise não encontrou nenhum uso; tudo bem se ele for usado de formas que a análise não vê',
    },
    "blind_spot": {
        "en": "{location}: the scan cannot confirm what this code does ({type})",
        "zh-CN": "{location}：扫描无法确认这段代码做了什么（{type}）",
        "zh-TW": "{location}：掃描無法確認這段程式碼做了什麼（{type}）",
        "ja": "{location}：このコードが何をするかスキャンでは確認できません（{type}）",
        "ko": "{location}: 이 코드가 무엇을 하는지 스캔으로 확인할 수 없습니다({type})",
        "ru": "{location}: сканирование не может подтвердить, что делает этот код ({type})",
        "es": "{location}: el análisis no puede confirmar qué hace este código ({type})",
        "pt": "{location}: a análise não consegue confirmar o que este código faz ({type})",
    },
    "blind_spot_context_loss": {
        "en": "{location}: {type}. The host cannot tell what triggered calls made from here and treats them as "
        "background use; asyncio.to_thread keeps the context",
        "zh-CN": "{location}：{type}。宿主无法判断从这里发出的调用是由什么触发的，会按后台使用处理；"
        "asyncio.to_thread 会保留上下文",
        "zh-TW": "{location}：{type}。宿主無法判斷從這裡發出的呼叫是由什麼觸發的，會按背景使用處理；"
        "asyncio.to_thread 會保留上下文",
        "ja": "{location}：{type}。ここからの呼び出しは何がきっかけかホストが判断できず、バックグラウンドでの使用として扱われます。"
        "asyncio.to_thread はコンテキストを保ちます",
        "ko": "{location}: {type}. 호스트는 여기서 나가는 호출이 무엇으로 시작됐는지 알 수 없어 백그라운드 사용으로 처리합니다. "
        "asyncio.to_thread는 컨텍스트를 유지합니다",
        "ru": "{location}: {type}. Хост не может определить, что вызвало вызовы отсюда, и считает их фоновым использованием; "
        "asyncio.to_thread сохраняет контекст",
        "es": "{location}: {type}. El host no puede saber qué provocó las llamadas hechas desde aquí y las trata como uso en "
        "segundo plano; asyncio.to_thread conserva el contexto",
        "pt": "{location}: {type}. O host não consegue saber o que disparou as chamadas feitas daqui e as trata como uso em "
        "segundo plano; asyncio.to_thread mantém o contexto",
    },
    "scan_no_extra_usage": {
        "en": "the code scan found no use beyond the declarations",
        "zh-CN": "代码扫描没有发现声明之外的用法",
        "zh-TW": "程式碼掃描沒有發現宣告之外的用法",
        "ja": "コードスキャンでは宣言以外の使用は見つかりませんでした",
        "ko": "코드 스캔에서 선언 이외의 사용은 발견되지 않았습니다",
        "ru": "сканирование кода не нашло использования сверх объявленного",
        "es": "el análisis del código no encontró usos más allá de las declaraciones",
        "pt": "a análise do código não encontrou usos além das declarações",
    },
    "scan_blind_spots": {
        "en": "the code scan has known blind spots: {types}; code in those places may use permissions the scan cannot see",
        "zh-CN": "代码扫描有已知盲区：{types}；这些地方的代码可能用到扫描看不到的权限",
        "zh-TW": "程式碼掃描有已知盲區：{types}；這些地方的程式碼可能用到掃描看不到的權限",
        "ja": "コードスキャンには既知の死角があります：{types}。その箇所のコードはスキャンで見えない権限を使っている可能性があります",
        "ko": "코드 스캔에는 알려진 사각지대가 있습니다: {types}. 그 위치의 코드는 스캔으로 볼 수 없는 권한을 쓸 수 있습니다",
        "ru": "у сканирования кода есть известные слепые зоны: {types}; код в этих местах может использовать разрешения, которые сканирование не видит",
        "es": "el análisis del código tiene puntos ciegos conocidos: {types}; el código en esos lugares puede usar permisos que el análisis no ve",
        "pt": "a análise do código tem pontos cegos conhecidos: {types}; o código nesses lugares pode usar permissões que a análise não vê",
    },
    "summary": {
        "en": "permission summary: {rows}",
        "zh-CN": "权限摘要：{rows}",
        "zh-TW": "權限摘要：{rows}",
        "ja": "権限の概要：{rows}",
        "ko": "권한 요약: {rows}",
        "ru": "сводка разрешений: {rows}",
        "es": "resumen de permisos: {rows}",
        "pt": "resumo de permissões: {rows}",
    },
}

# Where a summary row comes from; rows from different sources are never merged.
_SOURCE_LABELS: dict[str, dict[str, str]] = {
    "declared": {
        "en": "author declared",
        "zh-CN": "作者声明",
        "zh-TW": "作者宣告",
        "ja": "作者の宣言",
        "ko": "작성자 선언",
        "ru": "заявлено автором",
        "es": "declarado por el autor",
        "pt": "declarado pelo autor",
    },
    "detected": {
        "en": "found by scan",
        "zh-CN": "扫描发现",
        "zh-TW": "掃描發現",
        "ja": "スキャンで検出",
        "ko": "스캔에서 발견",
        "ru": "найдено сканированием",
        "es": "detectado por el análisis",
        "pt": "detectado pela análise",
    },
    "unconfirmed": {
        "en": "cannot confirm",
        "zh-CN": "无法确认",
        "zh-TW": "無法確認",
        "ja": "確認できない",
        "ko": "확인 불가",
        "ru": "нельзя подтвердить",
        "es": "no se puede confirmar",
        "pt": "não é possível confirmar",
    },
}

_BLIND_SPOT_LABELS: dict[str, dict[str, str]] = {
    "dynamic_import": {
        "en": "dynamic import",
        "zh-CN": "动态导入",
        "zh-TW": "動態匯入",
        "ja": "動的インポート",
        "ko": "동적 임포트",
        "ru": "динамический импорт",
        "es": "importación dinámica",
        "pt": "importação dinâmica",
    },
    "reflective_access": {
        "en": "reflective attribute access",
        "zh-CN": "反射式属性访问",
        "zh-TW": "反射式屬性存取",
        "ja": "リフレクションによる属性アクセス",
        "ko": "리플렉션 속성 접근",
        "ru": "рефлексивный доступ к атрибутам",
        "es": "acceso reflexivo a atributos",
        "pt": "acesso reflexivo a atributos",
    },
    "code_exec": {
        "en": "code run at runtime (eval/exec)",
        "zh-CN": "运行时执行代码（eval/exec）",
        "zh-TW": "執行時期執行程式碼（eval/exec）",
        "ja": "実行時のコード実行（eval/exec）",
        "ko": "런타임 코드 실행(eval/exec)",
        "ru": "выполнение кода во время работы (eval/exec)",
        "es": "código ejecutado en tiempo de ejecución (eval/exec)",
        "pt": "código executado em tempo de execução (eval/exec)",
    },
    "native_loader": {
        "en": "native library loading (ctypes)",
        "zh-CN": "加载原生库（ctypes）",
        "zh-TW": "載入原生程式庫（ctypes）",
        "ja": "ネイティブライブラリの読み込み（ctypes）",
        "ko": "네이티브 라이브러리 로드(ctypes)",
        "ru": "загрузка нативных библиотек (ctypes)",
        "es": "carga de bibliotecas nativas (ctypes)",
        "pt": "carregamento de bibliotecas nativas (ctypes)",
    },
    "context_loss": {
        "en": "lost call context (threads, own executors or event loops)",
        "zh-CN": "调用上下文丢失（线程、自建 executor 或事件循环）",
        "zh-TW": "呼叫上下文遺失（執行緒、自建 executor 或事件迴圈）",
        "ja": "呼び出しコンテキストの喪失（スレッド、独自の executor やイベントループ）",
        "ko": "호출 컨텍스트 손실(스레드, 자체 executor 또는 이벤트 루프)",
        "ru": "потеря контекста вызова (потоки, собственные executor или циклы событий)",
        "es": "pérdida del contexto de llamada (hilos, executors o bucles de eventos propios)",
        "pt": "perda do contexto da chamada (threads, executors ou loops de eventos próprios)",
    },
    "not_analyzed": {
        "en": "not scanned",
        "zh-CN": "未扫描",
        "zh-TW": "未掃描",
        "ja": "スキャン対象外",
        "ko": "스캔되지 않음",
        "ru": "не просканировано",
        "es": "no analizado",
        "pt": "não analisado",
    },
}

LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt")
assert all(set(table) == set(LOCALES) for table in _MESSAGES.values())
assert all(set(table) == set(LOCALES) for table in _SOURCE_LABELS.values())
assert all(set(table) == set(LOCALES) for table in _BLIND_SPOT_LABELS.values())


def message_codes() -> frozenset[str]:
    return frozenset(_MESSAGES)


def blind_spot_label(kind: str, locale: str) -> str:
    return _BLIND_SPOT_LABELS.get(kind, {}).get(locale, kind)


def texts(code: str, params: dict[str, str] | None = None, **localized) -> dict[str, str]:
    """Per-locale text of one message.

    ``params`` are filled in as-is; each ``localized`` value is a callable
    ``locale -> str`` for parameters that themselves need translating.
    """
    params = dict(params or {})
    result = {}
    for locale in LOCALES:
        values = {**params, **{key: render(locale) for key, render in localized.items()}}
        result[locale] = _MESSAGES[code][locale].format(**values)
    return result


def source_label(source: str, locale: str) -> str:
    return _SOURCE_LABELS[source][locale]
