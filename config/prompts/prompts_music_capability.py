"""On-demand music capability and bounded history labels."""

from config.prompts._locale import normalize_prompt_locale

MUSIC_CAPABILITY = {
    "zh": "[音乐能力] 后台可处理网易云账号、云端音乐库和播放请求；先简短回应，等真实结果再宣称成功。收藏默认存网易云：这首点红心，指定歌单则加入；只有明确说本地才存 N.E.K.O。",
    "zh-TW": "[音樂能力] 背景可處理網易雲帳號、雲端音樂庫和播放請求；先簡短回應，等真實結果再宣稱成功。收藏預設存網易雲：這首點紅心，指定歌單則加入；只有明確說本機儲存才存 N.E.K.O。",
    "en": "[Music capability] Background tasks can handle NetEase accounts, cloud libraries and playback. Wait for actual results before claiming success. Saves default to NetEase: like the current song or add it to the named cloud playlist. Save in N.E.K.O only on an explicit local-storage request.",
    "ja": "[音楽機能] バックグラウンドで NetEase のアカウント、クラウドライブラリ、再生を扱える。実際の結果を待ってから成功を伝える。保存は NetEase が既定で、現在曲にハートを付けるか指定プレイリストへ追加する。N.E.K.O への保存はローカル保存の明示的な依頼時のみ。",
    "ko": "[음악 기능] 백그라운드에서 NetEase 계정, 클라우드 라이브러리와 재생을 처리한다. 실제 결과를 받은 후 성공을 알린다. 저장 기본값은 NetEase 로 현재 곡에 하트를 누르거나 지정 목록에 추가한다. N.E.K.O 저장은 명시적인 로컬 저장 요청에만 허용한다.",
    "ru": "[Музыкальные возможности] Фоновые задачи работают с аккаунтом NetEase, облачной библиотекой и воспроизведением. Сообщайте об успехе только после фактического результата. По умолчанию сохраняйте в NetEase: отметьте текущую песню или добавьте в указанную облачную подборку. Сохранение в N.E.K.O требует явной просьбы о локальном хранении.",
    "es": "[Capacidad musical] Las tareas en segundo plano gestionan cuentas NetEase, bibliotecas en la nube y reproducción. Espera resultados reales antes de anunciar éxito. Guardar usa NetEase por defecto: marca la canción actual o añádela a la lista indicada. Guarda en N.E.K.O solo por petición explícita de almacenamiento local.",
    "pt": "[Recurso musical] Tarefas em segundo plano gerenciam contas NetEase, bibliotecas na nuvem e reprodução. Aguarde resultados reais antes de anunciar sucesso. Salvar usa NetEase por padrão: marque a música atual ou adicione à lista indicada. Salve no N.E.K.O somente por pedido explícito de armazenamento local.",
}

MUSIC_HISTORY_PREFIX = {
    "zh": "[网易云查询摘要，详细曲目需按页重新读取]",
    "zh-TW": "[網易雲查詢摘要，詳細曲目需分頁重新讀取]",
    "en": "[NetEase query summary; reload pages for track details]",
    "ja": "[NetEase 照会要約。曲の詳細はページごとに再取得]",
    "ko": "[NetEase 조회 요약. 곡 세부 정보는 페이지별로 다시 조회]",
    "ru": "[Сводка запроса NetEase; сведения о песнях загрузите постранично]",
    "es": "[Resumen de consulta NetEase; vuelve a cargar las páginas para detalles]",
    "pt": "[Resumo da consulta NetEase; recarregue as páginas para detalhes]",
}


def music_capability_text(lang):
    return "\n" + MUSIC_CAPABILITY[normalize_prompt_locale(lang)] + "\n"


def music_history_prefix(lang):
    return MUSIC_HISTORY_PREFIX[normalize_prompt_locale(lang)] + "\n"
