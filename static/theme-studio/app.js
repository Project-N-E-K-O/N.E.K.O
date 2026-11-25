/**
 * 主题工坊面板脚本（静态面板，无构建步骤、无框架）
 *
 * 本体内置版：面板与主题服务同源（都挂在 N.E.K.O 主服务上），所以既没有插件
 * 宿主的 /runs 任务队列，也没有 CSRF 引导：
 *   POST /api/theme_studio/run/{entry_id}   一次请求直接拿到结果
 *   GET  /api/theme_studio/theme.json       读当前调色板（外部同步轮询同一地址）
 * 所有地址都从 API_BASE 拼，页面本身由 /theme_studio 直接服务。
 */

/* ============================================================================
 * 1. 变量目录 —— THEME_CONTRACT.md §3 的权威清单
 * ==========================================================================*/

var CATALOG = [
  {
    group: '聊天气泡',
    items: [
      { key: '--nts-user-bubble-bg', label: '用户气泡背景', type: 'color', native: '#44b7fe' },
      { key: '--nts-user-bubble-text', label: '用户气泡文字', type: 'color', native: '#ffffff' },
      { key: '--nts-user-bubble-border', label: '用户气泡描边', type: 'color', native: 'transparent' },
      { key: '--nts-ai-bubble-bg', label: 'AI 气泡背景', type: 'color', native: '#ffffff', darkNative: 'rgba(42,123,196,0.18)' },
      { key: '--nts-ai-bubble-text', label: 'AI 气泡文字', type: 'color', native: '#24445a', darkNative: '#e0e0e0' },
      { key: '--nts-ai-bubble-border', label: 'AI 气泡描边', type: 'color', native: 'transparent' },
      {
        key: '--nts-code-bg', label: '代码块背景', type: 'color',
        native: 'rgba(91, 111, 145, 0.12)', darkNative: 'rgba(100, 120, 160, 0.18)',
        hint: '行内代码与代码块的底色（.message-markdown-inline-code / .message-markdown-pre）；代码块本体原生没有底色，只有行内代码有；留空即保持原生配色'
      },
      {
        key: '--nts-code-text', label: '代码块文字', type: 'color',
        native: '#22324d', darkNative: '#c0cce0',
        hint: '行内代码与代码块的文字色；原生行内代码 #22324d、代码块 #1f2c42（暗色两者都是 #c0cce0）；留空即保持原生配色'
      },
      {
        key: '--nts-image-bg', label: '图片块背景', type: 'color',
        native: 'transparent', darkNative: 'transparent',
        hint: '消息里图片包裹层 .message-block-image 的底色，垫在图片下方；留空即保持原生（透明，只有图片本身透明或加载失败时才看得见）'
      },
      {
        key: '--nts-tool-bubble-bg', label: '工具/系统气泡背景', type: 'color',
        native: 'linear-gradient(180deg, #f8fafc, #edf2f8)', darkNative: 'linear-gradient(180deg, #232330, #1e1e2a)',
        hint: '工具气泡与系统气泡共用的底色（.message-bubble-tool / .message-bubble-system）；原生工具气泡是渐变、系统气泡是半透明灰（亮 rgba(131,148,175,0.14) / 暗 rgba(80,90,110,0.2)），填写后两者会被拉平成同一个纯色；留空即保持原生'
      },
      {
        key: '--nts-tool-bubble-text', label: '工具/系统气泡文字', type: 'color',
        native: '#2b3a50', darkNative: '#b8bcc4',
        hint: '工具气泡与系统气泡的文字色；原生工具 #2b3a50、系统 #607086（暗色 #b8bcc4 / #9aa0ac）；留空即保持原生配色'
      },
      {
        key: '--nts-status-bg', label: '状态块背景', type: 'color',
        native: 'rgba(70, 129, 227, 0.12)', darkNative: 'rgba(68, 130, 230, 0.15)',
        hint: '消息状态块的底色（.message-block-status 的 info / success / warning / error 四种 tone 共用这一个变量）；原生四种 tone 各不相同（亮 success rgba(48,161,100,0.12)、warning rgba(235,168,52,0.16)、error rgba(223,91,91,0.14)），填写后四种 tone 会被拉平成同一个颜色；留空即保持原生'
      },
      {
        key: '--nts-status-text', label: '状态块文字', type: 'color',
        native: '#28589d', darkNative: '#6fa8ff',
        hint: '消息状态块的文字色（四种 tone 共用这一个变量）；原生四种 tone 各不相同（亮 success #236845、warning #9a6008、error #a63a3a；暗色 #5ec790 / #e8b44a / #e07070），填写后四种 tone 会被拉平成同一个颜色；留空即保持原生'
      }
    ]
  },
  {
    group: '聊天窗口',
    items: [
      { key: '--nts-chat-bg', label: '聊天窗口背景', type: 'color', native: 'transparent',
        hint: '紧凑胶囊形态下那条胶囊的底色由它决定（背板不再抢它）。展开后还影响 #react-chat-window-root 与 .chat-window。留空即保持原生' },
      { key: '--nts-chat-panel-bg', label: '聊天背板背景', type: 'color', native: 'transparent',
        hint: '消息列表背后那块底板的颜色：紧凑态涂 .compact-export-history-scroll，展开/全屏态涂 .message-list 与 .chat-body。留空即保持原生（透明）' },
      {
        key: '--nts-chat-bg-image', label: '聊天背景图片', type: 'image', native: 'none',
        hint: '给消息列表那块底板铺一张图片：设置后图片优先于「聊天背板背景」的纯色（纯色仍在图片下方兜底，图片透明处会透出来）。支持 png/jpg/webp/gif，单张上限 4 MB。合法值只有 none 或 url("…/backgrounds/<文件>")。留空即回原生（无图片）'
      },
      {
        key: '--nts-chat-bg-image-brightness', label: '背景图亮度', type: 'number', native: '1',
        hint: '把「聊天背景图片」整体压暗，避免图片跟正文抢视线。取值 0-1：1 = 原图亮度，0.6 = 压暗四成，0 = 全黑。只往下压、不会变亮（实现方式是一层黑色蒙版盖在图片上，正文完全不受影响——不是给容器加 filter，那样会把气泡和文字一起调暗）。只在设了「聊天背景图片」时生效；留空即保持原图'
      },
      {
        key: '--nts-chat-bg-fit', label: '背景图适应方式', type: 'select', native: 'cover',
        options: [
          { value: 'cover', label: '铺满（可能裁切）' },
          { value: 'contain', label: '整图可见（留底色）' }
        ],
        hint: '背景图怎么填进聊天区：铺满 = cover，图片铺满整块、比例不对时上下或左右会被裁掉（原生行为，多数壁纸适合）；整图可见 = contain，整张图缩到能看见全部、两侧或上下留出底色。只影响背景图，不动其他元素。留空即铺满'
      },
      {
        key: '--nts-chat-bg-position', label: '背景图位置', type: 'select', native: 'center',
        options: [
          { value: 'center', label: '居中' },
          { value: 'top', label: '顶部对齐' },
          { value: 'bottom', label: '底部对齐' },
          { value: 'left', label: '左侧对齐' },
          { value: 'right', label: '右侧对齐' }
        ],
        hint: '背景图放在聊天区的哪个位置：只在图片比例和容器不一致（或选了「整图可见」）时看得出来。居中 = 原生行为；顶部/底部对齐适合「画面主体在上/下半部」的壁纸。留空即居中'
      },
      {
        key: '--nts-chat-bg-repeat', label: '背景图平铺', type: 'select', native: 'no-repeat',
        options: [
          { value: 'no-repeat', label: '不平铺（单张）' },
          { value: 'repeat', label: '双向平铺' },
          { value: 'repeat-x', label: '横向平铺' },
          { value: 'repeat-y', label: '纵向平铺' }
        ],
        hint: '壁纸是否重复铺满：照片类壁纸保持「不平铺」（原生行为）；细纹理、格纹、图案类图片选平铺可以铺满整块而不被拉伸。平铺时「适应方式」不生效（每格按原尺寸重复）。留空即不平铺'
      },
      {
        key: '--nts-corner-style', label: '圆角风格', type: 'select', native: 'round',
        options: [
          { value: 'round', label: '普通圆角' },
          { value: 'continuous', label: '连续曲率（iOS 风格）' }
        ],
        hint: '圆角的几何形状：普通圆角 = 圆弧（原生行为）；连续曲率 = 超椭圆，转角没有「突然变直」的那一下，Apple 的设备圆角就是这种。需要较新的渲染内核（Chrome 139+ 的 corner-shape）；不支持时这条不生效、其它设置照常，也不会报错。留空即普通圆角'
      },
      {
        key: '--nts-chat-top-fade', label: '顶部渐隐', type: 'number', native: '1',
        hint: '聊天记录最上边那条渐隐，也就是背景图顶部那层「雾」的来源。取值 0-1：1 = 应用原生（顶部 28px 由透明渐入，图片顶部跟着淡出），0 = 渐隐彻底取消、图片顶边清晰，中间值线性过渡。注意关掉后滚到顶部的消息会硬切、不再淡出'
      },
      {
        key: '--nts-chat-panel-radius', label: '聊天背板圆角', type: 'length', native: '0px',
        hint: '消息列表那块底板的圆角，与「聊天背板背景」配套。用的是独立变量——「通用圆角」会连带改圆按钮和输入框，这里只圆背板。留空即保持原生直角'
      },
      { key: '--nts-topbar-bg', label: '顶栏背景', type: 'color', native: 'transparent' },
      { key: '--nts-topbar-text', label: '顶栏文字', type: 'color', native: 'inherit' },
      {
        key: '--nts-chat-blur', label: '毛玻璃模糊', type: 'blur', native: 'blur(18px)',
        hint: 'CSS 消费方是 backdrop-filter，因此必须写成 blur(18px) 形式；只填 18px 后端会自动补成 blur(18px)；单位只接受 px/rem/em/pt（不支持 % / vh / vw）'
      },
      {
        key: '--nts-bubble-veil', label: '聊天背景虚化', type: 'switch', native: '开',
        hint: '气泡上那层「白纱 + 背景模糊」的总开关。关掉后气泡底完全透明、背景模糊归零，聊天背景图与底色会完整透出来（对比度更高，适合铺了背景图之后觉得画面发灰/看不清的情况）；打开则回到 N.E.K.O 原生观感。底层是 0-1 乘子，同时作用于白纱不透明度与模糊半径。'
      },
      {
        key: '--nts-chat-opacity', label: '聊天面不透明度', type: 'number', native: '1',
        hint: '取值 0-1。走桥接写成 --neko-chat-opacity-factor —— N.E.K.O 自带的透明度乘子：它乘的是各聊天表面底色的 alpha（原生写法形如 rgba(255,255,255,calc(.91 * 该系数))），所以是整体缩放聊天面板/窗口的透明度，不改任何单个颜色。留空即保持原生（系数 1 = 100%）。注意此项曾因应用侧加载器没有 number 值类型而静默丢弃、面板却报成功，现已修复'
      }
    ]
  },
  {
    group: '输入区',
    items: [
      { key: '--nts-input-bg', label: '输入区背景', type: 'color', native: 'rgba(40, 40, 40, 0.65)', darkNative: 'rgba(40,40,40,0.65)' },
      { key: '--nts-input-text', label: '输入文字', type: 'color', native: 'inherit', darkNative: '#e0e0e0' },
      { key: '--nts-input-border', label: '输入区描边', type: 'color', native: 'rgba(255, 255, 255, 0.06)', darkNative: 'rgba(255,255,255,0.06)' },
      { key: '--nts-input-placeholder', label: '占位文字', type: 'color', native: '#9aa7b2', darkNative: '#999999' },
      { key: '--nts-button-bg', label: '工具按钮背景', type: 'color', native: 'transparent', darkNative: '#2d2d2d' },
      { key: '--nts-button-text', label: '工具按钮文字', type: 'color', native: 'inherit' },
      { key: '--nts-button-hover-bg', label: '工具按钮悬停', type: 'color', native: '#44b7fe1a', darkNative: '#44b7fe26', hint: '只在鼠标悬停工具按钮时生效（.composer-tool-btn:hover），不影响按钮静止态（那由「工具按钮背景」管）；留空即保持原生悬停色（亮 #44b7fe1a / 暗 #44b7fe26）' },
      { key: '--nts-send-bg', label: '发送按钮背景', type: 'color', native: '#44b7fe', darkNative: '#2a7bc4' },
      { key: '--nts-send-text', label: '发送按钮文字', type: 'color', native: '#ffffff', darkNative: '#ffffff' }
    ]
  },
  {
    group: '面板 / 弹窗 / 菜单',
    items: [
      {
        key: '--nts-panel-bg', label: '面板背景', type: 'color', native: 'transparent',
        hint: '作用于输入区面板（.composer-panel）与悬浮任务面板（#agent-task-hud）；紧凑胶囊态下面板本身被应用压成透明，此时看「聊天背景」'
      },
      {
        key: '--nts-panel-text', label: '面板文字', type: 'color', native: '#333333', darkNative: '#e0e0e0',
        hint: '作用于悬浮任务面板、访问条等面板面的文字；输入框里的文字请用「输入文字」。紧凑胶囊的文字颜色不跟这里走（避免改面板文字时连胶囊文字一起变色）'
      },
      { key: '--nts-panel-border', label: '面板描边', type: 'color', native: 'rgba(0, 0, 0, 0.08)', darkNative: 'rgba(255,255,255,0.1)' },
      { key: '--nts-popover-bg', label: '弹出菜单背景', type: 'color', native: 'transparent' },
      { key: '--nts-popover-text', label: '弹出菜单文字', type: 'color', native: 'inherit' },
      { key: '--nts-popover-border', label: '弹出菜单描边', type: 'color', native: 'transparent' },
      { key: '--nts-modal-bg', label: '弹窗背景', type: 'color', native: '#ffffff', darkNative: '#2d2d2d' },
      { key: '--nts-modal-text', label: '弹窗文字', type: 'color', native: '#24445a', darkNative: '#e0e0e0' },
      {
        key: '--nts-accent-bg', label: '插件页强调底纹', type: 'color',
        native: 'rgba(42, 123, 196, 0.05)', darkNative: 'rgba(58, 159, 216, 0.1)',
        hint: '走桥接：写 #agent-plugin-content / #agent-plugin-views 上的 --neko-popup-accent-bg，即插件面板标签页的悬停底色与选中底色（#agent-content-tabs button:hover / [aria-selected="true"]）；留空即保持原生（面板自身兜底亮 rgba(42,123,196,.08) / 选中 .10，模式变量亮 rgba(42,123,196,0.05) / 暗 rgba(58,159,216,0.1)）'
      },
      {
        key: '--nts-panel-text-sub', label: '面板次级文字', type: 'color',
        native: '#666666', darkNative: '#b0b0b0',
        hint: '走桥接：写 #agent-plugin-content / #agent-plugin-views 上的 --neko-popup-text-sub，消费方是插件面板顶部的标签页文字与关闭按钮（#agent-content-tabs button / #agent-plugin-close）；留空即保持原生（面板自身还有一层兜底 #667582，亮 #666666 / 暗 #b0b0b0）'
      },
      {
        key: '--nts-overlay-bg', label: '弹窗遮罩背景', type: 'color',
        native: 'transparent', darkNative: 'rgba(0, 0, 0, 0.65)',
        hint: '弹窗背后的整屏遮罩 .modal-overlay 底色；原生亮色模式基本不铺遮罩（常见组件给的 rgba(0,0,0,0.5) 只在个别弹窗生效），暗色是 rgba(0,0,0,0.65)；小剧场/串门这两类透明窗弹窗自带 transparent !important，不受此项影响；留空即保持原生'
      }
    ]
  },
  {
    group: '推荐 / 系统 chip',
    items: [
      { key: '--nts-rec-bg', label: '推荐条背景', type: 'color', native: 'transparent' },
      { key: '--nts-rec-text', label: '推荐条文字', type: 'color', native: 'inherit' },
      { key: '--nts-rec-border', label: '推荐条描边', type: 'color', native: 'rgba(108, 151, 174, 0.18)', darkNative: 'rgb(65, 84, 95)', hint: '只在 .proactive-recommendation-card 上生效；留空即保持原生描边（亮 rgba(108,151,174,0.18) / 暗 rgb(65,84,95)）' },
      { key: '--nts-chip-bg', label: 'chip 背景', type: 'color', native: 'transparent' },
      { key: '--nts-chip-text', label: 'chip 文字', type: 'color', native: 'inherit' }
    ]
  },
  {
    group: '全局',
    items: [
      {
        key: '--nts-text-gradient', label: '文字渐变', type: 'gradient', native: 'none',
        hint: '把聊天正文的文字填充换成一条渐变（如 linear-gradient(135deg, #6ee7ff 0%, #a78bfa 100%)）。只作用于消息文本块，不影响气泡底色；代码块与行内代码会自动保持实色，避免「字看不见」。注意：开了以后「气泡文字色」对正文不再生效（文字由渐变填充），且浅色渐变在浅背景上可能看不清 —— 建议选深浅两端跨度大的颜色。留空即纯色文字'
      },
      { key: '--nts-accent', label: '主色调', type: 'color', native: '#2a7bc4' },
      { key: '--nts-link', label: '链接色', type: 'color', native: '#2a7bc4' },
      { key: '--nts-highlight', label: '选中高亮', type: 'color', native: 'rgba(68, 183, 254, 0.35)', darkNative: 'rgba(74,163,223,0.4)' },
      { key: '--nts-scrollbar-thumb', label: '滚动条滑块', type: 'color', native: 'rgba(0, 0, 0, 0.25)', darkNative: 'rgba(255,255,255,0.2)' },
      { key: '--nts-scrollbar-track', label: '滚动条轨道', type: 'color', native: 'transparent' }
    ]
  },
  {
    group: '非颜色项',
    items: [
      {
        key: '--nts-radius', label: '通用圆角（按钮/输入面板）', type: 'length', native: '8px',
        hint: '原生值各组件不同（顶栏 0 / 卡片 12px / 按钮 10px），留空即完全保持原生'
      },
      {
        key: '--nts-bubble-radius', label: '聊天气泡圆角', type: 'length', native: '16px',
        hint: '只影响气泡本体；留空即保持原生（用户气泡 16px/8px 不对称圆角）'
      },
      {
        key: '--nts-shell-radius', label: '聊天窗外壳圆角', type: 'length', native: '14px',
        hint: '只影响聊天窗口外壳 #react-chat-window-shell；留空即保持原生'
      },
      {
        key: '--nts-card-radius', label: '插件卡片圆角', type: 'length', native: '0px',
        hint: '只影响 .agent-plugin-view 卡片；留空即保持原生'
      },
      {
        key: '--nts-modal-radius', label: '弹窗圆角', type: 'length', native: '0px',
        hint: '只影响 .modal-dialog 对话框；留空即保持原生'
      },
      {
        key: '--nts-popover-radius', label: '浮层圆角', type: 'length', native: '12px',
        hint: '只影响 .composer-overflow-popover 等弹出层；留空即保持原生'
      },
      {
        key: '--nts-rec-radius', label: '推荐卡圆角', type: 'length', native: '18px',
        hint: '只影响推荐类气泡卡片；留空即保持原生'
      },
      { key: '--nts-font-family', label: '字体族', type: 'font', native: 'inherit' },
      {
        key: '--nts-font-size', label: '基础字号', type: 'length', native: '0.94rem',
        hint: '消息正文的字号，纯文本与 Markdown 两种消息都会跟着变（如 1rem / 17px）；留空即保持原生'
      },
      {
        key: '--nts-line-height', label: '行高', type: 'ratio', native: '',
        hint: '聊天气泡里文字的行距，写成无单位倍数（1.55 = 1.55 倍字号）。1.3 偏紧凑、1.55 接近原生、1.8 很松。它是倍数而不是 px，所以改「基础字号」时行距会跟着缩放，不会出现字变大行距没变、两行粘在一起。留空即保持原生'
      },
      {
        key: '--nts-shadow', label: '阴影', type: 'shadow', native: '',
        hint: '如 0 2px 4px rgba(0,0,0,0.1)；作用在 7 个卡片面上（用户/AI 气泡、输入面板、两个浮层、弹窗、推荐卡），会整体替换这些面原有的阴影；留空即保持原生阴影'
      }
    ]
  },
  {
    group: '面板光效 / 玻璃拟态',
    items: [
      {
        key: '--nts-panel-glow-color', label: '光晕颜色', type: 'color', native: '#ffffff',
        hint: '外发光、四角高光、边缘内侧渐变三者共用的颜色。任意 CSS 颜色都行（#7ff / dodgerblue / rgb(120,200,255)）。改这里，三处会一起变色'
      },
      {
        key: '--nts-panel-glow-strength', label: '发光强度', type: 'number', native: '1',
        hint: '0-1 乘子，同时缩放外发光的扩散范围、模糊半径与颜色浓度。0 = 完全无光，1 = 用满「光晕尺寸」。留空即保持原生（无光）'
      },
      {
        key: '--nts-panel-glow-size', label: '光晕尺寸', type: 'length', native: '24px',
        hint: '外发光的基础半径（扩散 1 倍、模糊 0.42 倍）。内侧边缘渐变的宽度也按它算。24px 是柔和的起步值，压到 40px 以上会明显晕开'
      },
      {
        key: '--nts-panel-glow-pulse', label: '呼吸幅度', type: 'number', native: '0',
        hint: '发光强度的呼吸幅度，0-1：0 = 恒定不呼吸，0.5 = 亮度在 100%~50% 之间起伏，1 = 完全暗到亮。留空或 0 都不会挂动画（省一层合成）'
      },
      {
        key: '--nts-panel-glow-speed', label: '呼吸节奏', type: 'time', native: '3.2s',
        hint: '一次呼吸的时长，写 3.2s / 800ms 都行；只填数字按秒算（3 会补成 3s）。上限 60s。数越小越急，5s 以上是慢速呼吸。只在「呼吸幅度」大于 0 时才有意义'
      },
      {
        key: '--nts-panel-border-color', label: '描边颜色', type: 'color', native: 'rgba(255,255,255,.55)',
        hint: '面板边缘那圈半透明描边。想做出玻璃高光感，用白色系带透明度（rgba(255,255,255,.55)）；想做出彩色霓虹边，填高饱和色'
      },
      {
        key: '--nts-panel-border-width', label: '描边粗细', type: 'length', native: '1.5px',
        hint: '边缘描边的宽度。1px 是细高光，2-3px 以上会有明显「玻璃边框」感；留空即不画描边'
      },
      {
        key: '--nts-panel-corner-glow', label: '四角高光', type: 'number', native: '1',
        hint: '0-1 乘子，控制四个角上那四团径向高光的浓度。玻璃拟态里最容易出「立体感」的一项；0 = 关闭，1 = 最亮'
      },
      {
        key: '--nts-panel-edge-gradient', label: '边缘内侧渐变', type: 'number', native: '1',
        hint: '0-1 乘子，控制从边缘往内柔和衰减的那层亮光（inset 阴影），配合外发光能做出「面板自己在发亮」的效果。0 = 关闭'
      },
      {
        key: '--nts-panel-sheen', label: '玻璃反射高光', type: 'number', native: '1',
        hint: '0-1 乘子，控制从左上斜下来那条高光带（模拟玻璃反光）。0.3-0.5 比较自然，1 会明显发白'
      },
      {
        key: '--nts-panel-inner-glow', label: '内阴影', type: 'number', native: '1',
        hint: '0-1 乘子，控制面板内侧一圈淡淡的下沉阴影，让面板看起来「厚」。0 = 关闭'
      },
      {
        key: '--nts-panel-glass-tint', label: '玻璃底色', type: 'color', native: 'transparent',
        hint: '半透明玻璃底色，铺在「聊天背板背景」之上、背景图之下。想做出磨砂玻璃，填 rgba(255,255,255,.14) 这类带透明度的亮色（暗色主题用 rgba(20,30,45,.35)）'
      },
      {
        key: '--nts-panel-glass-blur', label: '玻璃磨砂', type: 'blur', native: 'blur(18px)',
        hint: '把「聊天背景图片」本身糊掉（只糊图片，正文不受影响 —— 走的是夹在图片与文字之间的负层）。写 blur(18px) 或只写 18px（后端自动补成 blur(18px)）；单位只接受 px/rem/em/pt'
      }
    ]
  },
  {
    group: '液态玻璃 / 渐变',
    items: [
      {
        key: '--nts-glass-filter', label: '界面玻璃模糊', type: 'filter',
        native: 'blur(20px) saturate(150%)', darkNative: 'blur(20px) saturate(150%)',
        hint: '顶栏、输入框外壳、浮层这些「玻璃面」背后的模糊程度，直接写 backdrop-filter 的链，例如 blur(20px) saturate(150%)。最多 4 段、同名滤镜不能重复，blur 封顶 40px、其余百分比封顶 400%。留空 = 不生效，这些面保持原生观感'
      },
      {
        key: '--nts-glass-tint', label: '界面玻璃底色', type: 'color',
        native: 'rgba(255,255,255,0.55)', darkNative: 'rgba(20,26,38,0.55)',
        hint: '玻璃面上的半透明底色，和上面的模糊是配套的：想做出磨砂玻璃就填 rgba(255,255,255,0.55) 这类带透明度的颜色。留空 = 不生效，底色跟原生走'
      },
      {
        key: '--nts-glass-highlight', label: '玻璃高光描边', type: 'color',
        native: 'rgba(255,255,255,0.65)', darkNative: 'rgba(255,255,255,0.22)',
        hint: '玻璃面那圈高光描边，填白色带透明度就有玻璃边缘反光的味道。留空 = 不生效，不画这圈高光'
      },
      {
        key: '--nts-bubble-glass-filter', label: '气泡玻璃模糊', type: 'filter',
        native: 'blur(12px) saturate(140%)', darkNative: 'blur(14px) saturate(150%)',
        hint: '用户/AI 气泡自身背景的模糊程度，通常比界面玻璃更轻一点，例如 blur(12px) saturate(140%)。留空 = 不生效，气泡保持原生半透明'
      },
      {
        key: '--nts-bubble-glass-tint', label: '气泡玻璃底色', type: 'color',
        native: 'rgba(255,255,255,0.18)', darkNative: 'rgba(30,40,60,0.28)',
        hint: '气泡的半透明底色；想让气泡有玻璃质感就填 rgba(255,255,255,0.18) 这类很淡的颜色。留空 = 不生效，气泡底色跟原生走'
      },
      {
        key: '--nts-chat-gradient', label: '聊天区渐变', type: 'gradient',
        native: 'linear-gradient(135deg, #eef4ff 0%, #dfe9ff 100%)',
        darkNative: 'linear-gradient(135deg, #101828 0%, #1b2440 100%)',
        hint: '整个聊天区（消息列表、聊天主体）的背景渐变，必须写成 linear-gradient(...) 或 radial-gradient(...)，2-4 个停止点、每个停止点都要带百分比。留空 = 不生效，用原生背景'
      },
      {
        key: '--nts-panel-gradient', label: '面板渐变', type: 'gradient',
        native: 'linear-gradient(135deg, #ffffff 0%, #eef3ff 100%)',
        darkNative: 'linear-gradient(135deg, #1c2436 0%, #10151f 100%)',
        hint: '聊天背板/导出历史面板那层背景渐变，格式和上面一样，会盖在背板纯色之上。留空 = 不生效，面板保持原生底色'
      },
      {
        key: '--nts-user-bubble-gradient', label: '用户气泡渐变', type: 'gradient',
        native: '', darkNative: '',
        hint: '只给用户气泡单独铺渐变背景，用「角度 + 起止色」快组一键生成最省事。留空 = 不生效（默认就不配置），气泡仍用用户气泡背景那个纯色'
      },
      {
        key: '--nts-ai-bubble-gradient', label: 'AI 气泡渐变', type: 'gradient',
        native: '', darkNative: '',
        hint: '只给 AI 气泡铺渐变背景。留空 = 不生效（默认不配置），AI 气泡仍用 AI 气泡背景的纯色'
      }
    ]
  },
  {
    group: '液态玻璃质感',
    items: [
      {
        key: '--nts-glass-rim', label: '玻璃边缘反光', type: 'color',
        native: 'rgba(255,255,255,0.55)', darkNative: 'rgba(255,255,255,0.32)',
        hint: '玻璃边缘反射光：沿面板/气泡/输入框边缘画一圈像镜面切边的亮线。配合低的玻璃底色 alpha 与界面玻璃模糊，这圈越亮「玻璃厚度」越明显；留空 = 不画'
      },
      {
        key: '--nts-glass-sheen', label: '表面掠光强度', type: 'number',
        native: '0', darkNative: '0',
        hint: '表面掠光强度 0–1：0 = 完全不动，0.3–0.7 会有一条高光带缓缓扫过玻璃面（需要界面/气泡玻璃模糊已配置才看得见）；留空 = 不生效'
      },
      {
        key: '--nts-glass-noise', label: '雾面颗粒', type: 'number',
        native: '0', darkNative: '0',
        hint: '雾面颗粒 0–1：0 = 光滑，0.1–0.25 有轻微磨砂质地（配合玻璃底色 alpha 与模糊即可透视背后内容、又有磨砂颗粒感）；留空 = 不生效'
      },
      {
        key: '--nts-glass-lens', label: '边缘透镜亮环', type: 'number',
        native: '0', darkNative: '0',
        hint: '边缘透镜亮环 0–1：0 = 不画，0.5–0.8 在玻璃边缘叠一圈向内弯的亮环 + 内侧柔光，模拟 Apple 液态玻璃边缘把光「折」进来的折射感；留空 = 不生效'
      },
      {
        key: '--nts-glass-thickness', label: '玻璃厚度', type: 'number',
        native: '0', darkNative: '0',
        hint: '玻璃厚度 0–1：上缘 1px 高光 + 下缘 1px 暗边 + 四边半亮 + 外投影，越大越像一块有厚度的实体玻璃；留空 = 不生效'
      },
      {
        key: '--nts-glass-specular', label: '镜面高光', type: 'number',
        native: '0', darkNative: '0',
        hint: '镜面高光 0–1：0.6–0.8 时玻璃表面有一团柔和反光，会跟着鼠标指针移动（苹果液态玻璃最好认的特征）；触摸设备停在左上；留空 = 不生效'
      },
      {
        key: '--nts-ambient-glow', label: '指针光源', type: 'number',
        native: '0', darkNative: '0',
        hint: '指针光源 0–1：0.4–0.7 时玻璃表面会跟着鼠标出现一片柔光（光源在动，玻璃的反光也跟着动，比单点镜面更像「真实光照」）；留空 = 不生效'
      },
      {
        key: '--nts-glass-dispersion', label: '边缘分光色散', type: 'number',
        native: '0', darkNative: '0',
        hint: '边缘分光 0–1：0.3–0.55 时玻璃边缘出现暖色（左上）与冷色（右下）两条细亮线，模拟折射把光分成不同颜色；留空 = 不生效'
      },
      {
        key: '--nts-glass-caustic', label: '流动焦散', type: 'number',
        native: '0', darkNative: '0',
        hint: '流动焦散 0–1：0.3–0.6 时玻璃后面有一团柔光缓慢胀缩，像水面的光透过玻璃在晃；与「表面掠光」方向、节奏都不同；留空 = 不生效'
      }
    ]
  }
];

var CATALOG_BY_KEY = {};
CATALOG.forEach(function (group) {
  group.items.forEach(function (item) { CATALOG_BY_KEY[item.key] = item; });
});

/** 行首的类型标签：告诉用户这一行接受什么形态的值。 */
var TYPE_LABEL = {
  color: '色',
  length: '尺寸',
  blur: '模糊',
  number: '数值',
  font: '字体',
  shadow: '阴影',
  image: '图片',
  switch: '开关',
  select: '方式',
  time: '节奏',
  ratio: '行高',
  filter: '模糊链',
  gradient: '渐变'
};

/* ============================================================================
 * 2. 客户端校验镜像（与后端 __init__.py 的规则保持一致，仅用于即时反馈；
 *    真正的安全边界永远是后端）
 * ==========================================================================*/

var RE_HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
// CSS 命名颜色（148 个标准命名色 + transparent + currentcolor，与后端
// plugin/__init__.py 的 RE_KEYWORD_COLOR 完全同集合、同顺序）。刻意不收
// inherit/initial/unset/revert：它们把颜色继承成父级，会让「只改气泡文字色」串到别处去。
// transparent 必须显式列出：后端一直收它，这里漏了就会「面板不标红、落盘成功、加载器丢门控」。
// 每段末尾都带一个空格，最后 .split(' ') 拆开 —— 旧实现有几段漏了空格，名字粘成
// "purplebeccapurple"/"blueblueviolet" 这类假名字（150 条只剩 136 条，一半是错的）。
var COLOR_NAMES = (
  'transparent aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond ' +
  'blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk ' +
  'crimson currentcolor cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey ' +
  'darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen ' +
  'darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue ' +
  'dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold ' +
  'goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender ' +
  'lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow ' +
  'lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray ' +
  'lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine ' +
  'mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen ' +
  'mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy ' +
  'oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise ' +
  'palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red ' +
  'rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue ' +
  'slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise ' +
  'violet wheat white whitesmoke yellow yellowgreen'
).split(' ');

var NAMED_COLOR = Object.create(null);

/** 命名色 → hex（CSS 规范值）。旧实现 parseColor 不认命名色，色块只能退回 #000000，
 *  看起来就像「改成 dodgerblue 却变黑」。本轮把这张表补上：transparent → #00000000；
 *  currentcolor 没有固定 RGB（它继承父级文字色），只参与校验、不参与解析。 */
var NAMED_HEX = Object.create(null);
(function () {
  var table = {
    aliceblue: '#f0f8ff', antiquewhite: '#faebd7', aqua: '#00ffff', aquamarine: '#7fffd4',
    azure: '#f0ffff', beige: '#f5f5dc', bisque: '#ffe4c4', black: '#000000',
    blanchedalmond: '#ffebcd', blue: '#0000ff', blueviolet: '#8a2be2', brown: '#a52a2a',
    burlywood: '#deb887', cadetblue: '#5f9ea0', chartreuse: '#7fff00', chocolate: '#d2691e',
    coral: '#ff7f50', cornflowerblue: '#6495ed', cornsilk: '#fff8dc', crimson: '#dc143c',
    cyan: '#00ffff', darkblue: '#00008b', darkcyan: '#008b8b', darkgoldenrod: '#b8860b',
    darkgray: '#a9a9a9', darkgreen: '#006400', darkgrey: '#a9a9a9', darkkhaki: '#bdb76b',
    darkmagenta: '#8b008b', darkolivegreen: '#556b2f', darkorange: '#ff8c00', darkorchid: '#9932cc',
    darkred: '#8b0000', darksalmon: '#e9967a', darkseagreen: '#8fbc8f', darkslateblue: '#483d8b',
    darkslategray: '#2f4f4f', darkslategrey: '#2f4f4f', darkturquoise: '#00ced1', darkviolet: '#9400d3',
    deeppink: '#ff1493', deepskyblue: '#00bfff', dimgray: '#696969', dimgrey: '#696969',
    dodgerblue: '#1e90ff', firebrick: '#b22222', floralwhite: '#fffaf0', forestgreen: '#228b22',
    fuchsia: '#ff00ff', gainsboro: '#dcdcdc', ghostwhite: '#f8f8ff', gold: '#ffd700',
    goldenrod: '#daa520', gray: '#808080', green: '#008000', greenyellow: '#adff2f',
    grey: '#808080', honeydew: '#f0fff0', hotpink: '#ff69b4', indianred: '#cd5c5c',
    indigo: '#4b0082', ivory: '#fffff0', khaki: '#f0e68c', lavender: '#e6e6fa',
    lavenderblush: '#fff0f5', lawngreen: '#7cfc00', lemonchiffon: '#fffacd', lightblue: '#add8e6',
    lightcoral: '#f08080', lightcyan: '#e0ffff', lightgoldenrodyellow: '#fafad2', lightgray: '#d3d3d3',
    lightgreen: '#90ee90', lightgrey: '#d3d3d3', lightpink: '#ffb6c1', lightsalmon: '#ffa07a',
    lightseagreen: '#20b2aa', lightskyblue: '#87cefa', lightslategray: '#778899', lightslategrey: '#778899',
    lightsteelblue: '#b0c4de', lightyellow: '#ffffe0', lime: '#00ff00', limegreen: '#32cd32',
    linen: '#faf0e6', magenta: '#ff00ff', maroon: '#800000', mediumaquamarine: '#66cdaa',
    mediumblue: '#0000cd', mediumorchid: '#ba55d3', mediumpurple: '#9370db', mediumseagreen: '#3cb371',
    mediumslateblue: '#7b68ee', mediumspringgreen: '#00fa9a', mediumturquoise: '#48d1cc', mediumvioletred: '#c71585',
    midnightblue: '#191970', mintcream: '#f5fffa', mistyrose: '#ffe4e1', moccasin: '#ffe4b5',
    navajowhite: '#ffdead', navy: '#000080', oldlace: '#fdf5e6', olive: '#808000',
    olivedrab: '#6b8e23', orange: '#ffa500', orangered: '#ff4500', orchid: '#da70d6',
    palegoldenrod: '#eee8aa', palegreen: '#98fb98', paleturquoise: '#afeeee', palevioletred: '#db7093',
    papayawhip: '#ffefd5', peachpuff: '#ffdab9', peru: '#cd853f', pink: '#ffc0cb',
    plum: '#dda0dd', powderblue: '#b0e0e6', purple: '#800080', rebeccapurple: '#663399',
    red: '#ff0000', rosybrown: '#bc8f8f', royalblue: '#4169e1', saddlebrown: '#8b4513',
    salmon: '#fa8072', sandybrown: '#f4a460', seagreen: '#2e8b57', seashell: '#fff5ee',
    sienna: '#a0522d', silver: '#c0c0c0', skyblue: '#87ceeb', slateblue: '#6a5acd',
    slategray: '#708090', slategrey: '#708090', snow: '#fffafa', springgreen: '#00ff7f',
    steelblue: '#4682b4', tan: '#d2b48c', teal: '#008080', thistle: '#d8bfd8',
    tomato: '#ff6347', turquoise: '#40e0d0', violet: '#ee82ee', wheat: '#f5deb3',
    white: '#ffffff', whitesmoke: '#f5f5f5', yellow: '#ffff00', yellowgreen: '#9acd32',
  };
  var keys = Object.keys(table);
  for (var i = 0; i < keys.length; i += 1) NAMED_HEX[keys[i]] = table[keys[i]];
  NAMED_HEX.transparent = '#00000000';
}());

(function () {
  for (var i = 0; i < COLOR_NAMES.length; i += 1) { NAMED_COLOR[COLOR_NAMES[i]] = 1; }
}());
function isNamedColor(t) { return NAMED_COLOR[String(t).toLowerCase()] === 1; }
var RE_RGB = /^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
var RE_HSL = /^hsla?\(\s*-?[0-9]{1,3}(\.[0-9]+)?\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*,\s*[0-9]{1,3}(\.[0-9]+)?%\s*(,\s*(0|1|0?\.[0-9]+)\s*)?\)$/i;
var RE_LENGTH = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|%|pt|vh|vw)$/i;
var RE_PERCENT = /^[0-9]{1,3}(\.[0-9]+)?%$/;
var RE_BLUR = /^blur\(\s*-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)\s*\)$/i;
/* 模糊允许裸长度（后端会把 18px 补成 blur(18px)），但单位只认 px/rem/em/pt：
 * % / vh / vw 会被后端归一成 blur(18%) 这样的非法 CSS，loader 静默丢弃。 */
var RE_BLUR_LENGTH = /^-?[0-9]{1,4}(\.[0-9]+)?(px|rem|em|pt)$/i;
// 允许 1.0 / 1.00（用户最自然地把「100%」写成 1.0）；仍拒绝 1.5 / 2。
var RE_NUMBER = /^(?:0|1|0?\.[0-9]+|1\.0+)$/;
// 行高：无单位倍数 0.8–3.0（最多两位小数）。与后端 RE_RATIO、loader RE_RATIO 逐字相同。
var RE_RATIO = /^[0-9](?:\.[0-9]{1,2})?$/;
var RE_ZERO = /^0$/;
/* 时长（animation-duration）：带单位 s/ms，或裸数字按秒。与后端 RE_TIME /
 * loader RE_TIME 三处保持一致；上限 60s（超过 1 分钟对观感没有意义，还能挡住
 * 打字打错量级，例如 3000 当成秒 = 50 分钟）。 */
var RE_TIME = /^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$/i;
var RE_TIME_BARE = /^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$/;
var TIME_MAX_SECONDS = 60;
var RE_FONT = /^[A-Za-z0-9\u4e00-\u9fff ,'"\-_.]{1,200}$/;
var RE_FORBIDDEN = /[;{}<>\\]|@import|url\s*\(/i;
var RE_INSET = /^inset$/i;
/* 聊天背景图：整串锚定成「none」或本体背景图路由下的绝对 url(...)。
 * 只认 127.0.0.1 / localhost 或无主机名（同源）三种写法 —— 消费方是应用窗口，
 * 相对路径会被解析到页面自己的位置。与后端 RE_BG_URL、loader RE_BG_VALUE 三处一致。 */
var RE_BG_VALUE = /^(?:none|url\(\s*"?(?:https?:\/\/(?:127\.0\.0\.1|localhost):[0-9]{2,5})?\/api\/theme_studio\/backgrounds\/[A-Za-z0-9._-]{1,80}"?\s*\))$/i;

/** 与后端 _shadow_tokens 同构：按「括号外空白」切分；括号不配对 → []。 */
function shadowTokens(value) {
  var tokens = [], depth = 0, buf = '';
  for (var i = 0; i < value.length; i += 1) {
    var ch = value[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') { depth -= 1; if (depth < 0) return []; }
    if (/\s/.test(ch) && depth === 0) {
      if (buf) { tokens.push(buf); buf = ''; }
      continue;
    }
    buf += ch;
  }
  if (buf) tokens.push(buf);
  if (depth !== 0) return [];
  return tokens;
}

/**
 * box-shadow 的结构校验。只做「token 白名单」是不够的：浏览器对 box-shadow 的
 * 语法要求是 <inset>? && <length>{2,4} && <color>?，而百分比在 box-shadow 里
 * 不是合法长度。旧实现放行 '0 0 10% #000' / '1px' / '1px 2px 3px 4px 5px'，
 * 面板显示已应用，浏览器却把整条声明丢掉 —— 与「改了设置但外观不变」完全一样。
 * 三处校验（面板 / loader / 后端）必须给出同一判定，见 THEME_CONTRACT §9.10b。
 */
/** 按「顶层逗号」切多层 box-shadow（后端 _split_shadow_layers 同构）。 */
function shadowLayers(value) {
  var layers = [];
  var depth = 0;
  var buf = '';
  for (var i = 0; i < value.length; i += 1) {
    var ch = value.charAt(i);
    if (ch === '(') depth += 1;
    else if (ch === ')') { depth -= 1; if (depth < 0) return []; }
    if (ch === ',' && depth === 0) {
      if (buf.replace(/\s+/g, '')) layers.push(buf.trim());
      buf = '';
      continue;
    }
    buf += ch;
  }
  if (depth !== 0) return [];
  if (buf.replace(/\s+/g, '')) layers.push(buf.trim());
  return layers;
}

/** 单层 box-shadow 的 token 校验（与后端 _shadow_layer_ok 同判定）。 */
function shadowLayerOk(tokens) {
  if (!tokens.length || tokens.length > 6) return false; // inset + 4 长度 + 颜色
  var lengths = 0;
  var colors = 0;
  var insets = 0;
  for (var i = 0; i < tokens.length; i += 1) {
    var t = tokens[i];
    if (RE_INSET.test(t)) {
      if (++insets > 1) return false;
      continue;
    }
    if (isNamedColor(t) || RE_HEX.test(t) || RE_RGB.test(t) || RE_HSL.test(t)) {
      if (++colors > 1) return false;
      continue;
    }
    // 百分比不是 box-shadow 的合法长度（50% 会被浏览器整条丢弃）
    if (RE_PERCENT.test(t) || t.indexOf('%') !== -1) return false;
    if (RE_LENGTH.test(t) || RE_ZERO.test(t)) {
      lengths += 1;
      if (lengths > 4) return false;
      // 负值只有前两个长度（x/y 偏移）合法；模糊半径与扩散半径不能为负
      if (t.charAt(0) === '-' && lengths > 2) return false;
      continue;
    }
    return false;
  }
  return lengths >= 2;
}

/** 支持逗号分隔的多层阴影（霓虹发光主题的标准写法）。 */
function isSafeShadow(value) {
  var layers = shadowLayers(value);
  if (!layers.length || layers.length > 4) return false;
  for (var i = 0; i < layers.length; i += 1) {
    if (!shadowLayerOk(shadowTokens(layers[i]))) return false;
  }
  return true;
}

/* ---------------- 模糊链 / 渐变（FEATURE-CONTRACT v0.1.11 §2） ----------------
 * 与后端 _is_filter_value / _is_gradient_value、loader 的 isSafeValue 三端逐字同判定。
 * 面板只是即时反馈，安全边界永远在后端；三端任一放宽都会出现「面板能存、
 * 加载器丢值、用户看到改了没反应」。 */

var RE_FILTER_TOKEN = /^(blur|saturate|brightness|contrast|grayscale)\((\d{1,3}(?:\.\d+)?)(px|%)\)$/;

/** 空格分隔的滤镜链：1–4 个 token、同名不重复、blur ≤ 40px、其余 ≤ 400%。 */
function isSafeFilterChain(v) {
  if (typeof v !== 'string') return false;
  var s = v.trim();
  if (!s || s.length > 120) return false;
  var toks = s.split(' ');
  if (toks.length < 1 || toks.length > 4) return false;
  var seen = Object.create(null);
  for (var i = 0; i < toks.length; i += 1) {
    var m = RE_FILTER_TOKEN.exec(toks[i]);
    if (!m) return false;
    var fn = m[1], num = parseFloat(m[2]), unit = m[3];
    if (seen[fn]) return false;
    seen[fn] = 1;
    if (fn === 'blur') { if (unit !== 'px' || num > 40) return false; }
    else { if (unit !== '%' || num > 400) return false; }
    if (toks[i] !== m[0]) return false;
  }
  return true;
}

var RE_GRAD_HEAD = /^(linear|radial)-gradient\(([\s\S]*)\)$/;
var RE_GRAD_ANGLE = /^-?\d{1,3}(?:\.\d+)?deg$/;
// 停止点的百分比是**必填**的（契约 §2.2 散文 + 后端 _gradient_ok：group(2) is None
// 直接判非法）。所以 `linear-gradient(135deg, #fff, #000)` 非法，只有
// `linear-gradient(135deg, #fff 0%, #000 100%)` 这种「面板生成什么、校验就收什么」
// 的形态才合法 —— 两端判定必须逐字一致。
var RE_GRAD_STOP = /^((?:#[0-9a-f]{3,8})|(?:rgba?\([^()]*\))|(?:hsla?\([^()]*\))|(?:[a-z]{3,20}))\s+(\d{1,3}(?:\.\d+)?)%$/i;

/** 只接受 linear/radial-gradient：2–4 个停止点、停止点必须带百分比、整串锚定。 */
function isSafeGradient(v) {
  if (typeof v !== 'string') return false;
  var s = v.trim();
  if (!s || s.length > 300) return false;
  var m = RE_GRAD_HEAD.exec(s);
  if (!m) return false;
  // 按「顶层逗号」切分：rgba(0,0,0,.5) 里的逗号不算分隔符。
  var body = m[2], depth = 0, parts = [], buf = '';
  for (var i = 0; i < body.length; i += 1) {
    var ch = body[i];
    if (ch === '(') depth += 1; else if (ch === ')') { depth -= 1; if (depth < 0) return false; }
    if (ch === ',' && depth === 0) { parts.push(buf.trim()); buf = ''; continue; }
    buf += ch;
  }
  parts.push(buf.trim());
  if (depth !== 0) return false;
  if (m[1] === 'linear') {
    if (parts.length < 3 || !RE_GRAD_ANGLE.test(parts[0])) return false;
    parts = parts.slice(1);
  }
  if (parts.length < 2 || parts.length > 4) return false;
  for (var j = 0; j < parts.length; j += 1) {
    var sm = RE_GRAD_STOP.exec(parts[j]);
    if (!sm) return false;
    var color = sm[1];
    if (!(isNamedColor(color) || RE_HEX.test(color) || RE_RGB.test(color) || RE_HSL.test(color))) return false;
    // 百分比必填（正则已强制），且 ≤ 100。
    if (!sm[2] || parseFloat(sm[2]) > 100) return false;
  }
  return true;
}

/** 与后端 __init__.py 的 _is_safe_value 逐条对齐；仅作即时反馈，安全边界在后端。 */
function isSafeValue(key, value) {
  if (typeof value !== 'string') return false;
  var v = value.trim();
  if (!v || v.length > 300) return false;
  var item = CATALOG_BY_KEY[key];
  var type = item ? item.type : 'color';
  // 背景图必须排在 RE_FORBIDDEN 之前：黑名单里有 `url\s*\(`，而 url(...) 正是
  // 背景图的唯一合法写法。整串锚定，只认 none 或本体 /api/theme_studio/backgrounds/ 的绝对地址。
  if (type === 'image') return RE_BG_VALUE.test(v);
  if (type === 'select') {
    // 下拉类变量：值必须是 CATALOG 里声明的某个 option，整串匹配。
    var choices = (item && item.options) || [];
    for (var ci = 0; ci < choices.length; ci++) {
      if (choices[ci].value === v.toLowerCase()) return true;
    }
    return false;
  }
  if (RE_FORBIDDEN.test(v)) return false;
  // filter / gradient 走契约 §2 的专用校验（与后端/loader 同一套判定）。
  if (type === 'filter') return isSafeFilterChain(v);
  if (type === 'gradient') return isSafeGradient(v);
  if (type === 'color') {
    return RE_HEX.test(v) || RE_RGB.test(v) || RE_HSL.test(v) || isNamedColor(v);
  }
  // 负长度/负半径在 CSS 里一律非法（整条声明被丢弃 → 面板报成功、外观不变），
  // 后端 _is_safe_value 对 length/blur 单独拦 "-"；这里镜像同一条规则。
  // （shadow 的偏移量要负号，走的是 isSafeShadow 的 token 分支，不受影响。）
  if (type === 'length') {
    if (v.indexOf('-') !== -1) return false;
    return RE_LENGTH.test(v) || RE_PERCENT.test(v) || RE_ZERO.test(v);
  }
  if (type === 'blur') {
    if (v.indexOf('-') !== -1) return false;
    // 只接受 px/rem/em/pt（可写 blur(18px) 或裸 18px，后端会把裸长度补成 blur()）。
    // 旧实现直接用 RE_LENGTH（含 % / vh / vw）：18% 能通过面板校验，后端归一成
    // blur(18%) 非法 CSS 被 loader 丢弃 —— 「应用成功但外观不变」的根因之一。
    return RE_BLUR.test(v) || RE_BLUR_LENGTH.test(v) || RE_ZERO.test(v);
  }
  if (type === 'number') {
    // 百分比只接受 0–100（--nts-chat-opacity 是 0–1 乘子，999% 这类写法对应用无意义，后端会静默丢弃）
    if (RE_PERCENT.test(v)) return parseFloat(v) <= 100;
    return RE_NUMBER.test(v);
  }
  if (type === 'ratio') {
    // 行高倍数：0.8–3.0，最多两位小数。范围与后端 RATIO_MIN/RATIO_MAX 一致。
    if (!RE_RATIO.test(v)) return false;
    var ratio = parseFloat(v);
    return ratio >= 0.8 && ratio <= 3;
  }
  if (type === 'time') {
    // 裸数字按秒（后端会补 s）；带单位只认 s/ms，且换算后不得超过 60s。
    if (RE_TIME_BARE.test(v)) return parseFloat(v) <= TIME_MAX_SECONDS;
    if (!RE_TIME.test(v)) return false;
    var secs = /ms$/i.test(v) ? parseFloat(v) / 1000 : parseFloat(v);
    return isFinite(secs) && secs >= 0 && secs <= TIME_MAX_SECONDS;
  }
  if (type === 'font') return RE_FONT.test(v);
  if (type === 'shadow') return isSafeShadow(v);
  return false;
}

/* ============================================================================
 * 3. 颜色工具
 * ==========================================================================*/

function clamp(n, lo, hi) { return Math.min(hi, Math.max(lo, n)); }

function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360 / 360;
  s = clamp(s, 0, 100) / 100;
  l = clamp(l, 0, 100) / 100;
  if (s === 0) { var g = Math.round(l * 255); return [g, g, g]; }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var p = 2 * l - q;
  function hue(t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  }
  return [
    Math.round(hue(h + 1 / 3) * 255),
    Math.round(hue(h) * 255),
    Math.round(hue(h - 1 / 3) * 255)
  ];
}

/** 解析一个 CSS 颜色 → {r,g,b,a}；无法解析返回 null。 */
function parseColor(input) {
  if (typeof input !== 'string') return null;
  var v = input.trim();
  if (!v) return null;
  var lower = v.toLowerCase();
  if (lower === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
  // 命名色查 NAMED_HEX 后交给下面的 hex 分支解析。旧实现这里直接返回 null，
  // 于是 "dodgerblue" 这类值在色块上只能退回 #000000 —— 用户看到的就是
  // 「改了个名字色，结果变黑」。currentcolor 没有固定 RGB（它继承父级文字色），
  // 故意留在表外：返回 null，面板原样显示文字、不静默改成黑色。
  if (NAMED_HEX[lower]) return parseColor(NAMED_HEX[lower]);

  var m = v.match(/^#([0-9a-f]{3,8})$/i);
  if (m) {
    var h = m[1];
    if (h.length === 3) {
      return {
        r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16), a: 1
      };
    }
    if (h.length === 4) {
      // #RGBA：每个 nibble 复制成双位，alpha = 末位 / 15（等价 0x尾尾 / 255）
      return {
        r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16),
        a: parseInt(h[3] + h[3], 16) / 255
      };
    }
    if (h.length === 6) {
      return {
        r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1
      };
    }
    if (h.length === 8) {
      return {
        r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16),
        a: parseInt(h.slice(6, 8), 16) / 255
      };
    }
    return null;
  }

  m = v.match(/^rgba?\(([^)]*)\)$/i);
  if (m) {
    var parts = m[1].split(',').map(function (s) { return s.trim(); });
    if (parts.length < 3) return null;
    var r = parseInt(parts[0], 10), g = parseInt(parts[1], 10), b = parseInt(parts[2], 10);
    var a = parts.length > 3 ? parseFloat(parts[3]) : 1;
    if ([r, g, b].some(function (n) { return !isFinite(n); })) return null;
    return { r: clamp(r, 0, 255), g: clamp(g, 0, 255), b: clamp(b, 0, 255), a: isFinite(a) ? clamp(a, 0, 1) : 1 };
  }

  m = v.match(/^hsla?\(([^)]*)\)$/i);
  if (m) {
    var hp = m[1].split(',').map(function (s) { return s.trim(); });
    if (hp.length < 3) return null;
    var rgb = hslToRgb(parseFloat(hp[0]), parseFloat(hp[1]), parseFloat(hp[2]));
    var al = hp.length > 3 ? parseFloat(hp[3]) : 1;
    return { r: rgb[0], g: rgb[1], b: rgb[2], a: isFinite(al) ? clamp(al, 0, 1) : 1 };
  }

  return null;
}

function toHex(r, g, b) {
  function h(n) { var s = clamp(Math.round(n), 0, 255).toString(16); return s.length === 1 ? '0' + s : s; }
  return '#' + h(r) + h(g) + h(b);
}

function toRgba(r, g, b, a) {
  var alpha = Math.round(clamp(a, 0, 1) * 100) / 100;
  return 'rgba(' + clamp(Math.round(r), 0, 255) + ',' + clamp(Math.round(g), 0, 255) + ',' + clamp(Math.round(b), 0, 255) + ',' + alpha + ')';
}

/* ============================================================================
 * 4. 后端调用（本体 HTTP API）
 * ==========================================================================*/

/* 主题服务挂在本体主服务上的前缀。面板页面也是本体路由，所以同源、不必带端口。 */
var API_BASE = '/api/theme_studio';
/* 与 theme_studio/service.py 的 DEFAULT_THEME_NAME、index.html 主题名 placeholder 三处一致。
 * 后端把空串/纯空白 name 视为「恢复默认名」并返回默认名，面板仍显式提交它。 */
var DEFAULT_THEME_NAME = '默认';
/* 单次请求超时。本体服务是常驻 uvicorn 进程，正常情况毫秒级返回；给足 30s 只是
 * 为了不让慢盘上的「整库导入」被误判成失败。 */
var POST_TIMEOUT_MS = 30000;

function delay(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

/**
 * 带超时的 fetch。超时抛出的错误带 timeout=true，供上层区分「超时」与「HTTP 失败」。
 * 手写 AbortController 而不是 AbortSignal.timeout：后者在旧版 Electron/Chrome 里
 * 不一定存在，而这个静态面板没有构建步骤、无法做 polyfill。
 */
function fetchWithTimeout(url, options, timeoutMs) {
  var opts = options || {};
  var controller = new AbortController();
  var timer = setTimeout(function () { controller.abort(); }, timeoutMs);
  opts.signal = controller.signal;
  return fetch(url, opts).catch(function (error) {
    if (error && error.name === 'AbortError') {
      var timeoutError = new Error('请求超时（' + Math.round(timeoutMs / 1000) + 's）');
      timeoutError.timeout = true;
      throw timeoutError;
    }
    throw error;
  }).finally(function () { clearTimeout(timer); });
}

/** POST 一个 JSON 体，原样返回 fetch Response（业务判断交给调用方）。 */
function postJson(url, body) {
  return fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body
  }, POST_TIMEOUT_MS);
}

/* 宿主的错误包装：entry 里 return Err(SdkError("导入内容为空。")) 时，宿主会把它包成
 *   Plugin 'neko_theme_studio' entry 'entry import_theme' execution failed:
 *   Plugin 'neko_theme_studio' entry 'import_theme' execution failed: 导入内容为空。
 * 有时还会套两层。原样显示给用户就是一句看不懂的内部报错，所以把外壳剥掉，
 * 只留插件自己写的中文原因；剥完仍是空串时退回原文本，绝不吞掉错误。 */
var HOST_ERROR_WRAPPER = /execution failed:\s*/g;

function stripHostErrorWrapper(text) {
  var msg = String(text == null ? '' : text);
  var cut = -1;
  var m;
  HOST_ERROR_WRAPPER.lastIndex = 0;
  while ((m = HOST_ERROR_WRAPPER.exec(msg)) !== null) cut = m.index + m[0].length;
  if (cut > 0) msg = msg.slice(cut);
  return msg.trim() || String(text == null ? '' : text).trim();
}

function pluginErrorMessage(error) {
  if (!error) return '';
  if (typeof error === 'string') return stripHostErrorWrapper(error);
  if (typeof error.message === 'string') return stripHostErrorWrapper(error.message);
  if (typeof error.detail === 'string') return stripHostErrorWrapper(error.detail);
  if (typeof error.code === 'string') return error.code;
  return '';
}

/* 调用一个本体 entry，返回其 JSON 结果。
 *
 * 本体版没有「插件进程没起来」这个失败模式：主题服务跟着主服务一起启动，调用
 * 失败只可能是后端真的出错或超时，重不重试交给用户决定 —— 这里不再自愈重跑，
 * 免得把一次 30s 超时拖成两次。保留 callPlugin 这个名字是因为面板里有 20 多处
 * 调用点都叫它。 */
async function callPlugin(entryId, args) {
  return callPluginOnce(entryId, args);
}

/** 调用一个本体 entry。后端返回 {ok:true,result} / {ok:false,error}，HTTP 恒 200。 */
async function callPluginOnce(entryId, args) {
  var url = API_BASE + '/run/' + encodeURIComponent(entryId);
  var resp;
  try {
    resp = await postJson(url, JSON.stringify(args || {}));
  } catch (error) {
    if (error && !error.phase) error.phase = 'post';
    throw error;
  }
  if (!resp.ok) {
    var httpError = new Error('HTTP ' + resp.status);
    httpError.phase = 'post';
    httpError.status = resp.status;
    throw httpError;
  }
  var payload = await resp.json();
  if (!payload || payload.ok !== true) {
    var reason = (payload && (payload.error || payload.detail)) || '操作失败';
    throw new Error(pluginErrorMessage(reason) || '操作失败');
  }
  return payload.result;
}

/* ============================================================================
 * 5. 状态
 * ==========================================================================*/

/** 下拉项的短文案（去掉「（…）」备注），用于「原生 X」那一格。 */
function selectOptionLabel(item, value) {
  var opts = (item && item.options) || [];
  var hit = '';
  opts.forEach(function (opt) {
    if (!hit && String(opt.value) === String(value)) hit = String(opt.label || opt.value);
  });
  if (!hit) hit = String(value || '');
  return hit.split('（')[0];
}

var state = {
  mode: 'light',            // 'light' | 'dark' —— 编辑 vars 还是 dark_vars
  palette: null,            // 服务器上的调色板（唯一真相）
  edited: { light: {}, dark: {} },  // 用户真正改过的键 → 值
  cleared: { light: {}, dark: {} }, // 用户点了「清空」的键 → true（提交空串 = 删除）
  collapsed: {},
  filter: '',               // 搜索框里的过滤词（小写比较）
  onlyDirty: false,         // 只看改动：隐藏未改动的行
  busy: false,
  modalMode: 'import',
  modalReturnFocus: null,
  // 名称 / 启用的「已保存基线」：由 renderMeta 写入，applyTheme 用它判断有没有改动。
  // 初值等于 index.html 的 DOM 初值，所以首次 get_theme 失败（palette=null）时
  // 只改名称或启用开关也能被识别成有效改动。
  metaName: '',
  metaEnabled: true,
  // 「运行状态」卡片最近一次读到的后端状态（studio_status 的返回）。
  status: null
};

function editedMap() { return state.edited[state.mode]; }
function clearedMap() { return state.cleared[state.mode]; }

function isDirty(key) {
  return Object.prototype.hasOwnProperty.call(editedMap(), key) ||
    Object.prototype.hasOwnProperty.call(clearedMap(), key);
}


/** 名称/启用开关这类「元信息」的脏值（变量脏值仍由 dirtyCount 负责）。
 *
 *  旧实现只数变量，于是「只改名」或「只拐开关」时 dirty=0：
 *  ① 外部同步（按时段自动换主题）判定面板干净 → 直接刷新，把刚敲的名字冲掉；
 *  ② 「应用」按钮不显示改动项数，点下去才报「没有可应用的改动」，像插件坏了；
 *  ③ busy 期间这两个控件没被禁用，回读时敲的字会被 renderMeta 覆盖。
 */
function metaDirtyCount() {
  var n = 0;
  var nameInput = $('theme-name');
  if (nameInput && nameInput.value.trim() !== String(state.metaName || '').trim()) n += 1;
  var enabledInput = $('theme-enabled');
  if (enabledInput && (!!enabledInput.checked !== !!state.metaEnabled)) n += 1;
  return n;
}

/** 面板里所有未提交的改动：变量 + 名称/启用。 */
function pendingChangeCount() { return dirtyCount() + metaDirtyCount(); }

function dirtyCount() {
  var light = Object.keys(state.edited.light).length + Object.keys(state.cleared.light).length;
  var dark = Object.keys(state.edited.dark).length + Object.keys(state.cleared.dark).length;
  return light + dark;
}

/** 该行在当前模式下「显示」的值：改过就用改的值，否则用原生参考值。 */
function displayValue(key) {
  if (Object.prototype.hasOwnProperty.call(editedMap(), key)) return editedMap()[key];
  if (Object.prototype.hasOwnProperty.call(clearedMap(), key)) return '';
  var server = serverValue(key);
  if (server !== null) return server;
  if (state.mode === 'dark') {
    var item = CATALOG_BY_KEY[key];
    if (item && item.darkNative !== undefined) return item.darkNative;
  }
  return '';
}

/** 服务器调色板里该键的值（当前模式），没有则 null。 */
function serverValue(key) {
  if (!state.palette) return null;
  var bag = state.mode === 'dark' ? state.palette.dark_vars : state.palette.vars;
  if (bag && Object.prototype.hasOwnProperty.call(bag, key)) return bag[key];
  return null;
}

/** 提交时该键的取值：改过 → 值；清空 → ''（删除）；没碰过 → undefined（不发）。 */
function submitValue(key) {
  if (Object.prototype.hasOwnProperty.call(editedMap(), key)) return editedMap()[key];
  if (Object.prototype.hasOwnProperty.call(clearedMap(), key)) return '';
  return undefined;
}

/* ---------------------------------------------------------------- 可读性自检（v0.4.9）
 *
 * 面板里配完色，最常被忽略的是「文字和底色的对比度」。这组纯函数与
 * `_work/audit_themes.py` 的口径保持一致（WCAG 2.1：正文 4.5、次要 3.0），
 * 点一下就能扫出所有低于阈值的组合，并跳回对应变量行改。
 */

/** 把带 alpha 的前景合成到不透明背景上（都返回 {r,g,b,a:1}）。 */
function colorOver(fg, bg) {
  if (!fg) return null;
  if (!bg) return { r: fg.r, g: fg.g, b: fg.b, a: 1 };
  var a = (fg.a === undefined || fg.a === null) ? 1 : fg.a;
  if (a >= 1) return { r: fg.r, g: fg.g, b: fg.b, a: 1 };
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1
  };
}

function colorChannel(v) {
  v = v / 255;
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function colorLuma(c) {
  if (!c) return 0;
  return 0.2126 * colorChannel(c.r) + 0.7152 * colorChannel(c.g) + 0.0722 * colorChannel(c.b);
}

/** WCAG 对比度：(较亮 + .05) / (较暗 + .05)。 */
function contrastRatio(fg, bg) {
  if (!fg || !bg) return 0;
  var l1 = colorLuma(fg);
  var l2 = colorLuma(bg);
  if (l1 < l2) { var t = l1; l1 = l2; l2 = t; }
  return (l1 + 0.05) / (l2 + 0.05);
}

/** 渐变里最中间那个色标（气泡文字最常见的落点）。 */
function gradientMidColor(value) {
  if (typeof value !== 'string' || !value) return null;
  var re = /(#[0-9a-fA-F]{3,8}|rgba?\([^()]*\))/g;
  var stops = [];
  var m;
  while ((m = re.exec(value))) {
    var c = parseColor(m[1]);
    if (c) stops.push(c);
  }
  if (!stops.length) return null;
  return stops[Math.floor(stops.length / 2)];
}

/** 把颜色 c 朝 target 混合 t（0 保留原色、1 变成 target）。 */
function mixToward(c, target, t) {
  return {
    r: c.r + (target.r - c.r) * t,
    g: c.g + (target.g - c.g) * t,
    b: c.b + (target.b - c.b) * t,
    a: 1
  };
}

/**
 * 给「前景压在背景上」找一档刚好达标的颜色。
 * 同时朝黑、朝白两个方向按 1% 步进试，取「改动最小且先达标」的那个：
 * 浅底深字通常压暗，深底浅字通常提亮；已有极值色（纯白字压中蓝底）时
 * 会改走另一侧（压成深字）——两种都在 WCAG 上成立。已达标或无解返回 null。
 */
function contrastFixColor(fg, bg, need) {
  if (!fg || !bg) return null;
  var base = colorOver(fg, bg);
  if (!base) return null;
  if (contrastRatio(base, bg) >= need) return null;
  var targets = [
    { color: { r: 0, g: 0, b: 0 }, name: 'dark' },
    { color: { r: 255, g: 255, b: 255 }, name: 'light' }
  ];
  var best = null;
  targets.forEach(function (target) {
    for (var step = 1; step <= 100; step++) {
      // 先四舍五入到 8 位色再算：写进输入框的是 #rrggbb，若按浮点值判定
      // 达标、落盘后四舍五入又掉到阈值下，用户会觉得「修了还是红的」。
      var raw = mixToward(base, target.color, step / 100);
      var c = { r: Math.round(raw.r), g: Math.round(raw.g), b: Math.round(raw.b), a: 1 };
      var ratio = contrastRatio(c, bg);
      if (ratio >= need) {
        if (!best || step < best.t) {
          best = { color: c, ratio: ratio, t: step / 100, toward: target.name };
        }
        return;
      }
    }
  });
  return best;
}

/** {r,g,b} → '#rrggbb'（四舍五入并夹到 0–255）。 */
function colorToHex(c) {
  if (!c) return '';
  function part(v) {
    var n = Math.round(v);
    if (n < 0) n = 0;
    if (n > 255) n = 255;
    var s = n.toString(16);
    return s.length < 2 ? '0' + s : s;
  }
  return '#' + part(c.r) + part(c.g) + part(c.b);
}

/** 指定模式下的生效值（改过 → 改的值；清空 → ''；否则服务器值 / 原生值）。 */
function valueForMode(key, mode) {
  var ed = state.edited[mode] || {};
  var cl = state.cleared[mode] || {};
  if (Object.prototype.hasOwnProperty.call(ed, key)) return ed[key];
  if (Object.prototype.hasOwnProperty.call(cl, key)) return '';
  var bag = state.palette ? (mode === 'dark' ? state.palette.dark_vars : state.palette.vars) : null;
  if (bag && Object.prototype.hasOwnProperty.call(bag, key)) return bag[key];
  if (mode === 'dark') {
    var item = CATALOG_BY_KEY[key];
    if (item && item.darkNative !== undefined) return item.darkNative;
  }
  return '';
}

function auditParse(key, mode, fallback) {
  var v = valueForMode(key, mode);
  if (!v) v = fallback || '';
  return parseColor(v);
}

/** 11 组对比度检查（与 _work/audit_themes.py 的 checks() 同口径）。 */
var READ_PAIRS = [
  { name: '正文 / 聊天背景', fg: '--nts-panel-text', bg: 'chat', need: 4.5 },
  { name: '正文 / 顶栏', fg: '--nts-topbar-text', bg: 'surface', need: 4.5 },
  { name: '正文 / 输入框', fg: '--nts-input-text', bg: 'input', need: 4.5 },
  { name: '正文 / 面板玻璃', fg: '--nts-panel-text', bg: 'glass', need: 4.5 },
  { name: '正文 / AI 气泡', fg: '--nts-ai-bubble-text', bg: 'ai', need: 4.5 },
  { name: '用户气泡文字', fg: '--nts-user-bubble-text', bg: 'user', need: 4.5 },
  { name: '发送键文字', fg: '--nts-send-text', bg: 'send', need: 4.5 },
  { name: '次要文字 / 聊天背景', fg: '--nts-panel-text-sub', bg: 'chat', need: 3.0 },
  { name: 'placeholder', fg: '--nts-input-placeholder', bg: 'input', need: 3.0 },
  { name: 'chip 文字', fg: '--nts-chip-text', bg: 'chip', need: 3.0 },
  { name: '链接 / 聊天背景', fg: '--nts-link', bg: 'chat', need: 3.0 }
];

/** 某个模式下的一行审计结果数组（跳过解析不出的颜色）。 */
function auditRowsForMode(mode) {
  var dark = mode === 'dark';
  var chatBg = auditParse('--nts-chat-bg', mode, dark ? '#1f242a' : '#eef3f8');
  if (!chatBg) chatBg = { r: dark ? 31 : 238, g: dark ? 36 : 243, b: dark ? 42 : 248, a: 1 };
  var surface = auditParse('--nts-topbar-bg', mode, '') || chatBg;
  var glass = colorOver(auditParse('--nts-panel-glass-tint', mode, ''), chatBg) || surface;
  var inputBg = colorOver(auditParse('--nts-input-bg', mode, ''), chatBg) || chatBg;
  var userBg = colorOver(auditParse('--nts-user-bubble-bg', mode, ''), chatBg) ||
    gradientMidColor(valueForMode('--nts-user-bubble-gradient', mode)) || chatBg;
  var aiBg = colorOver(auditParse('--nts-ai-bubble-bg', mode, ''), chatBg) || chatBg;
  var chipBg = colorOver(auditParse('--nts-chip-bg', mode, ''), chatBg) || chatBg;
  var sendBg = auditParse('--nts-send-bg', mode, '') || chatBg;
  var bgs = { chat: chatBg, surface: surface, input: inputBg, glass: glass, ai: aiBg, user: userBg, chip: chipBg, send: sendBg };

  var out = [];
  READ_PAIRS.forEach(function (pair) {
    var fg = auditParse(pair.fg, mode, '');
    var bg = bgs[pair.bg];
    if (!fg || !bg) return;
    fg = colorOver(fg, bg);
    var ratio = contrastRatio(fg, bg);
    out.push({
      name: pair.name,
      key: pair.fg,
      mode: mode,
      ratio: Math.round(ratio * 100) / 100,
      need: pair.need,
      ok: ratio >= pair.need,
      // 自动修正要用的两个不透明色：前景已合成到背景上，背景是实际计算底。
      fg: fg,
      bg: bg
    });
  });
  return out;
}

/** 点失败项 → 切到对应模式、清搜索、滚到那一行并高亮。 */
function jumpToAuditRow(key, mode) {
  if (mode && state.mode !== mode) setMode(mode);
  if (state.filter) setFilter('');
  if (state.onlyDirty) setOnlyDirty(false);
  var ref = rowsByKey[key];
  if (!ref || !ref.container) return;
  if (ref.container.hidden) {
    // 该行被折叠组藏着：先展开那一组
    var group = null;
    CATALOG.forEach(function (g) {
      if (group) return;
      g.items.forEach(function (it) { if (it.key === key) group = g; });
    });
    if (group && state.collapsed[group.group]) {
      state.collapsed[group.group] = false;
      saveCatalogCollapsed();
      applyFilters();
    }
    ref.container.hidden = false;
  }
  if (ref.container.scrollIntoView) ref.container.scrollIntoView({ block: 'center', behavior: 'smooth' });
  ref.container.classList.add('is-flash');
  setTimeout(function () { ref.container.classList.remove('is-flash'); }, 1400);
  if (ref.hexInput && ref.hexInput.focus) ref.hexInput.focus();
}

/**
 * 把一条失败项的前景色修正成刚好达标的颜色：只写进输入框（成为 dirty），
 * 不直接提交——用户还能在预览里看、能改回、能点「应用」时一起生效。
 * @returns {boolean} true = 找到了可用的修正值
 */
function applyAuditFix(row, quiet) {
  if (!row) return false;
  // 多留 0.05 的余量：面板显示两位小数，卡在 4.499 会让人以为「修了还是红的」。
  var fix = contrastFixColor(row.fg, row.bg, row.need + 0.05);
  if (!fix) return false;
  if (row.mode && state.mode !== row.mode) setMode(row.mode);
  var hex = colorToHex(fix.color);
  if (!hex) return false;
  markEdited(row.key, hex);
  syncRow(row.key);
  if (!quiet) {
    showToast('「' + row.name + '」调到 ' + hex + '（' + fix.ratio.toFixed(2) + ':1），点「应用」生效', 'ok');
  }
  return true;
}

/** 把亮/暗两组里所有失败项一次性填好修正值（仍然需要用户点「应用」）。 */
function fixAllAudit() {
  var original = state.mode;
  var fixed = 0;
  var stuck = 0;
  ['light', 'dark'].forEach(function (mode) {
    var rows = auditRowsForMode(mode).filter(function (r) { return !r.ok; });
    if (!rows.length) return;
    if (state.mode !== mode) setMode(mode);
    rows.forEach(function (r) {
      if (applyAuditFix(r, true)) fixed += 1;
      else stuck += 1;
    });
  });
  if (state.mode !== original) setMode(original);
  renderAudit(true);
  if (!fixed) {
    showToast('没有能自动修正的项：这组底色和文字太接近，需要手动改背景色', 'error');
    return;
  }
  showToast('已填好 ' + fixed + ' 项修正值' + (stuck ? '，' + stuck + ' 项自动调不动' : '') + '；点「应用」生效', 'ok');
}

/** 渲染审计结果；第一次点击时才算（auto=false 表示仅用当前状态预扫一次）。 */
function renderAudit(auto) {
  var list = $('audit-list');
  var summary = $('audit-summary');
  if (!list || !summary) return;
  var light = auditRowsForMode('light');
  var dark = auditRowsForMode('dark');
  var bad = light.filter(function (r) { return !r.ok; }).concat(dark.filter(function (r) { return !r.ok; }));
  list.innerHTML = '';
  function renderGroup(label, rows) {
    var head = el('li', { class: 'audit-group' }, [el('span', { text: label })]);
    if (!rows.length) {
      head.appendChild(el('span', { class: 'audit-muted', text: '颜色没配全，暂时无法判断' }));
    } else {
      var groupBad = bad.filter(function (r) { return r.mode === rows[0].mode; }).length;
      head.appendChild(el('span', {
        class: 'audit-badge ' + (groupBad ? 'is-fail' : 'is-pass'),
        text: groupBad ? groupBad + ' / ' + rows.length + ' 项低于阈值' : rows.length + ' 项全部达标'
      }));
    }
    list.appendChild(head);
    rows.forEach(function (r) {
      var li = el('li', { class: 'audit-item ' + (r.ok ? 'is-pass' : 'is-fail') }, [
        el('span', { class: 'audit-name', text: r.name }),
        el('span', { class: 'audit-ratio', text: r.ratio.toFixed(2) + ':1' }),
        el('span', { class: 'audit-plus', text: r.ok ? '✓' : '需 ≥ ' + r.need.toFixed(1) })
      ]);
      if (!r.ok) {
        li.title = '点击跳到 ' + r.key + '（' + (r.mode === 'dark' ? '暗色' : '亮色') + '）';
        li.setAttribute('role', 'button');
        li.setAttribute('tabindex', '0');
        li.addEventListener('click', function () { jumpToAuditRow(r.key, r.mode); });
        li.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            jumpToAuditRow(r.key, r.mode);
          }
        });
        var fixBtn = el('button', {
          class: 'btn btn-mini audit-fix',
          type: 'button',
          text: '自动修正',
          title: '按背景亮度自动调出一个达标颜色（只填值，不直接应用）'
        });
        fixBtn.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          if (applyAuditFix(r, false)) renderAudit(true);
          else showToast('这一组自动调不到 ' + r.need.toFixed(1) + ':1，需要改背景色', 'error');
        });
        li.appendChild(fixBtn);
      }
      list.appendChild(li);
    });
  }
  renderGroup('亮色', light);
  renderGroup('暗色', dark);
  if (!light.length && !dark.length) {
    summary.textContent = '还没有可比较的颜色：先给「聊天背景」「正文」等基础变量配值。';
    return;
  }
  if (!bad.length) {
    summary.className = 'audit-summary is-pass';
    summary.textContent = '两组共 ' + (light.length + dark.length) + ' 项全部达到 WCAG 阈值（正文 4.5、次要 3.0）。' + (auto ? '' : '');
  } else {
    summary.className = 'audit-summary is-fail';
    summary.textContent = '有 ' + bad.length + ' 项低于阈值，点条目可跳到对应变量。';
  }
}

/* ===== UNDO_BEGIN（v0.10.0：撤销 / 重做）===== */

/**
 * 撤销栈：每一步存的是「改动前」的 edited/cleared 全量快照。
 *
 * 为什么存快照而不是存单条操作：编辑区的改动是「地图上的增删」，单条操作的
 * 逆操作要区分 markEdited / markCleared / 恢复到服务器值这些分支，容易漏；
 * 两张表一共最多一百多个键，快照代价可以忽略，正确性最高。
 *
 * 合并策略：同一个变量行 900ms 内的连续改动只压一次栈 —— 否则打十个字就是
 * 十步撤销，Ctrl+Z 会像卡住一样。
 */
var undoState = { past: [], future: [], limit: 50, lastKey: '', lastAt: 0, applying: false };

function cloneValueMap(map) {
  var out = {};
  if (map && typeof map === 'object') {
    Object.keys(map).forEach(function (key) { out[key] = map[key]; });
  }
  return out;
}

function undoSnapshot() {
  var edited = state.edited || {};
  var cleared = state.cleared || {};
  return {
    edited: { light: cloneValueMap(edited.light), dark: cloneValueMap(edited.dark) },
    cleared: { light: cloneValueMap(cleared.light), dark: cloneValueMap(cleared.dark) }
  };
}

function undoPush(key, force) {
  if (undoState.applying) return;
  var now = (typeof Date.now === 'function') ? Date.now() : new Date().getTime();
  if (!force && key && undoState.lastKey === key && now - undoState.lastAt < 900) {
    undoState.lastAt = now;
    return;
  }
  undoState.lastKey = key || '';
  undoState.lastAt = now;
  undoState.past.push(undoSnapshot());
  if (undoState.past.length > undoState.limit) undoState.past.shift();
  undoState.future = [];
  updateUndoButtons();
}

function undoApply(snapshot) {
  undoState.applying = true;
  state.edited = snapshot.edited;
  state.cleared = snapshot.cleared;
  markAllRowsValid();
  clearRgbMemory();
  syncAll();
  undoState.applying = false;
  renderChanges();
  updateUndoButtons();
}

function undoStep() {
  if (state.busy) return;
  if (!undoState.past.length) {
    showToast('没有可撤销的改动了');
    return;
  }
  undoState.future.push(undoSnapshot());
  undoApply(undoState.past.pop());
  showToast('已撤销上一步', 'ok');
}

function redoStep() {
  if (state.busy) return;
  if (!undoState.future.length) {
    showToast('没有可重做的改动了');
    return;
  }
  undoState.past.push(undoSnapshot());
  undoApply(undoState.future.pop());
  showToast('已重做', 'ok');
}

function updateUndoButtons() {
  var undoBtn = $('btn-undo');
  var redoBtn = $('btn-redo');
  if (undoBtn) {
    undoBtn.disabled = !!state.busy || !undoState.past.length;
    undoBtn.title = undoState.past.length
      ? '撤销上一次改动（还能撤销 ' + undoState.past.length + ' 步）'
      : '没有可撤销的改动';
  }
  if (redoBtn) {
    redoBtn.disabled = !!state.busy || !undoState.future.length;
    redoBtn.title = undoState.future.length
      ? '重做（还能重做 ' + undoState.future.length + ' 步）'
      : '没有可重做的改动';
  }
}

function initUndoShortcuts() {
  document.addEventListener('keydown', function (event) {
    var meta = event.ctrlKey || event.metaKey;
    if (!meta) return;
    var target = event.target;
    var tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
    // 输入框里有浏览器自己的撤销历史，抢过来只会让人更困惑。
    if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
        (target && target.isContentEditable)) return;
    var key = String(event.key || '').toLowerCase();
    if (key === 'z' && !event.shiftKey) {
      event.preventDefault();
      undoStep();
    } else if ((key === 'z' && event.shiftKey) || key === 'y') {
      event.preventDefault();
      redoStep();
    }
  });
}

/* ===== UNDO_END ===== */

function markEdited(key, value) {
  undoPush(key);
  // 记住「上一个通过校验的编辑值」：非颜色行输入非法时要回滚到它，
  // 否则应用仍会提交上一个合法值并报成功（用户以为新输入生效了）。
  rowValidCache[key] = value;
  delete clearedMap()[key];
  // 改回服务器上已有的值 = 没改：否则把值敲回去后按钮仍显示「已改」，
  // 应用时还会把同值再写一遍（版本号 +1，用户以为哪里坏了）。
  if (serverValue(key) === value) {
    delete editedMap()[key];
    return;
  }
  editedMap()[key] = value;
}

function markCleared(key) {
  undoPush(key);
  delete editedMap()[key];
  delete rowValidCache[key];
  markRowInvalid(key, false);
  clearedMap()[key] = true;
}

/**
 * 「清空」的语义是「删掉 theme.json 里的这一项」。若服务器本来就没有这一项，
 * 清空应当退化成「撤销本地编辑」而不是记一条空串提交（否则干净的行点一下清空
 * 就凭空多出一项改动，应用后版本号变化但外观不变）。
 * @returns {boolean} true = 真的提交了删除
 */
function clearRow(key) {
  var hasServer = serverValue(key) !== null;
  if (!hasServer) {
    delete editedMap()[key];
    delete clearedMap()[key];
    delete rowValidCache[key];
    markRowInvalid(key, false);
    return false;
  }
  markCleared(key);
  return true;
}

/* ---------------- 行级「非法输入」与「颜色记忆」 ----------------
 * 1) invalidRows：哪几行当前是红框非法态。applyTheme 见到非 0 就阻止应用，
 *    避免「输入非法 → 应用仍提交上一个合法值并报成功」。
 * 2) rowValidCache：每行上一次通过校验的编辑值；输入非法时把 edited 回滚到它
 *    （没有就删除该键 = 回退服务器值），使面板状态与将提交的内容一致。
 * 3) lastRgbByKey：全透明行记住最近一次的 RGB，让 alpha 滑块在 transparent
 *    状态下仍然可用（旧实现一置灰，用户就再也拖不回半透明）。 */
var rowValidCache = {};
var invalidRows = {};
var lastRgbByKey = {};

function markRowInvalid(key, bad) {
  if (bad) invalidRows[key] = true;
  else delete invalidRows[key];
}

function invalidRowCount() { return Object.keys(invalidRows).length; }

function invalidInputHint(item) {
  if (item && item.type === 'blur') return '模糊只接受 px/rem/em/pt（可写 18px 或 blur(18px)）';
  return '该值不合法，请改成合法值或点「清空」';
}

/** 非法输入时回滚到上一个合法编辑值；没有就删掉本地编辑（退回服务器值）。 */
function rollbackRowToLastValid(key) {
  if (Object.prototype.hasOwnProperty.call(rowValidCache, key)) {
    markEdited(key, rowValidCache[key]);
  } else {
    delete editedMap()[key];
  }
}

function markAllRowsValid() { invalidRows = {}; }

function clearRgbMemory() { lastRgbByKey = {}; }

/** 记住一行最近一次带颜色成分的 RGB（transparent 字面量没有颜色信息，跳过）。 */
function rememberRgbValue(key, parsed, raw) {
  if (!parsed) return;
  if (String(raw || '').trim().toLowerCase() === 'transparent') return;
  if (parsed.a > 0 || parsed.r || parsed.g || parsed.b) {
    lastRgbByKey[key] = { r: parsed.r, g: parsed.g, b: parsed.b };
  }
}

/** 宽容写法归一化：把「人最自然的写法」补全成校验能直接吃掉的形态。
 *
 * - ``length`` 行敲纯数字 ``16`` → ``16px``（过去会标红，必须自己补单位）；
 * - ``blur`` 行敲纯数字 ``7`` → ``blur(7px)``（后端本来就接受 ``blur(7px)`` 与裸 ``7px``，
 *   但不接受无单位的 ``7``）；
 * - 其余（带单位/带百分号/负数/多 token）原样返回，交给 ``isSafeValue()`` 判定
 *   （``-4px`` 仍然非法：负圆角会让整条声明被浏览器丢弃）。
 *
 * 注意：只在面板侧归一化，后端与 loader 的校验保持严格 —— API 契约不变，
 * 手改 ``theme.json`` 或走 JSON 导入仍须写完整形态。
 */
function normalizeTypedValue(item, raw) {
  if (!item) return raw;
  if (!/^-?[0-9]{1,4}(?:\.[0-9]+)?$/.test(raw)) return raw;
  if (item.type === 'length') return raw + 'px';
  if (item.type === 'blur') return 'blur(' + raw + 'px)';
  // 时长裸数字补 s：CSS 的 animation-duration 不认无单位数字，后端也会补，
  // 面板这里先补上，用户敲 3 立刻看到 3s。
  if (item.type === 'time') return raw + 's';
  return raw;
}

/** 统一设置输入框的红框 / 无障碍非法标记 / 提示标题。 */
function setInputInvalid(input, item, bad) {
  if (!input) return;
  input.classList.toggle('is-bad', !!bad);
  if (bad) {
    input.setAttribute('aria-invalid', 'true');
    input.title = invalidInputHint(item);
  } else {
    input.removeAttribute('aria-invalid');
    input.title = '';
  }
}

/* ============================================================================
 * 6. DOM 帮手
 * ==========================================================================*/

function el(tag, props, children) {
  var node = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.indexOf('on') === 0 && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
  }
  (children || []).forEach(function (c) { if (c) node.appendChild(c); });
  return node;
}

function $(id) { return document.getElementById(id); }

/** kind: 'ok' | 'error' | 空 —— 只影响配色，不影响文案。 */
function showToast(message, kind) {
  var node = $('toast');
  node.textContent = message;
  node.classList.toggle('is-ok', kind === 'ok');
  node.classList.toggle('is-error', kind === 'error');
  node.classList.add('show');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () { node.classList.remove('show'); }, 3000);
}

/** kind: 'idle' | 'busy' | 'ok' | 'error' | 'warn' —— 底部状态点配色。 */
function setStatus(text, kind) {
  var node = $('status');
  if (!node) return;
  node.textContent = text;
  node.dataset.kind = kind || 'idle';
}

/** kind: 'ok' | 'warn' | 'error' —— 左侧色条与配色。传空文案即收起。 */
function showNotice(kind, text) {
  var node = $('notice');
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.className = 'notice';
    var body = $('notice-text');
    if (body) body.textContent = '';
    return;
  }
  node.className = 'notice is-' + kind;
  var target = $('notice-text') || node;
  target.textContent = text;
  node.hidden = false;
}

function hideNotice() { showNotice('ok', ''); }

/** 顶部细进度条：读取/应用期间给一个非阻塞的「在忙」信号。 */
function setLoading(loading) {
  document.body.classList.toggle('is-loading', !!loading);
}

/* ============================================================================
 * 7. 渲染编辑器
 * ==========================================================================*/

var rowsByKey = {};   // key → { hexInput, colorInput, alpha, alphaVal, textInput, rowEl, syncFn }

var groupNodes = {};   // group 名 → <section class="group">

function buildEditor() {
  var host = $('groups');
  host.textContent = '';

  CATALOG.forEach(function (group) {
    var key = group.group;
    var body = el('div', { class: 'group-body' });
    var count = el('span', { class: 'group-count' });
    var head = el('div', {
      class: 'group-head',
      role: 'button',
      tabindex: '0',
      'aria-expanded': state.collapsed[key] ? 'false' : 'true',
      'aria-label': key + ' 分组（点击展开/收起）',
      onclick: function () { toggleGroup(key); },
      onkeydown: function (event) {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
          event.preventDefault();
          toggleGroup(key);
        }
      }
    }, [
      el('span', { class: 'group-caret', 'aria-hidden': 'true', text: '▶' }),
      el('span', { class: 'group-title', text: key }),
      count
    ]);
    var wrap = el('section', { class: 'group' + (state.collapsed[key] ? ' is-collapsed' : '') }, [head, body]);
    groupNodes[key] = wrap;

    group.items.forEach(function (item) { body.appendChild(buildRow(item, count)); });

    host.appendChild(wrap);
  });

  refreshCounts();
  updateGroupToggleLabel();
}

function toggleGroup(key) {
  state.collapsed[key] = !state.collapsed[key];
  var node = groupNodes[key];
  if (node) {
    node.classList.toggle('is-collapsed', !!state.collapsed[key]);
    var head = node.querySelector('.group-head');
    if (head) head.setAttribute('aria-expanded', state.collapsed[key] ? 'false' : 'true');
  }
  // 折叠状态写 localStorage：下次打开面板保持上次的分组浏览位置。
  saveCatalogCollapsed();
  updateGroupToggleLabel();
}

function allCollapsed() {
  return CATALOG.every(function (group) { return !!state.collapsed[group.group]; });
}

function updateGroupToggleLabel() {
  var btn = $('btn-toggle-groups');
  if (!btn) return;
  var collapsed = allCollapsed();
  btn.textContent = collapsed ? '全部展开' : '全部收起';
  // a11y（v0.4.21）：这个按钮控制的是所有分组，补一个 aria-expanded 让读屏
  // 知道「现在是全收起状态」；视觉上按钮文案已经表达了同一件事。
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}

function toggleAllGroups() {
  var collapse = !allCollapsed();
  CATALOG.forEach(function (group) {
    state.collapsed[group.group] = collapse;
    var node = groupNodes[group.group];
    if (!node) return;
    node.classList.toggle('is-collapsed', collapse);
    var head = node.querySelector('.group-head');
    if (head) head.setAttribute('aria-expanded', collapse ? 'false' : 'true');
  });
  saveCatalogCollapsed();
  updateGroupToggleLabel();
}

/** 当前模式下的「原生参考值」：暗色优先 darkNative，没有就退回亮色原生值。 */
function nativeOf(item) {
  if (state.mode === 'dark' && item.darkNative !== undefined) return item.darkNative;
  return item.native || '';
}

/** placeholder 展示原生参考值；太长的不塞进输入框，免得像噪声。 */
function placeholderFor(item) {
  var native = nativeOf(item);
  if (native && native.length <= 28) return '原生 ' + native;
  return '留空 = 原生';
}

/* ---------------- filter / gradient 行的预览条与渐变快组 ----------------
 * 本轮不允许改 style.css，所以预览条与快组控件全部用内联样式。 */

var PREVIEW_BASE_STYLE = 'position:relative;flex:0 0 84px;height:26px;border-radius:6px;' +
  'overflow:hidden;border:1px solid rgba(128,128,128,0.35);' +
  'background:repeating-linear-gradient(45deg, rgba(120,140,170,0.45) 0 5px, rgba(120,140,170,0.12) 5px 10px);';

/** 造一条预览条：底纹是斜条纹，filter 行让 backdrop-filter 去糊它；
 *  gradient 行则把条纹当「未配置」的占位。返回 { host, node }。 */
function buildPreviewHost(title) {
  var host = el('span', {
    class: 'live-preview',
    'aria-hidden': 'true',
    title: title,
    style: PREVIEW_BASE_STYLE
  });
  var node = el('span', { style: 'position:absolute;top:0;left:0;right:0;bottom:0;' });
  host.appendChild(node);
  return { host: host, node: node };
}

/** 把预览条同步成当前生效值；空/非法 → 清掉效果，只留底纹（＝没有预览）。 */
function syncValuePreview(ref, value) {
  if (!ref || !ref.previewNode) return;
  var v = value === null || value === undefined ? '' : String(value).trim();
  var ok = false;
  if (ref.item.type === 'filter') {
    ok = !!v && isSafeFilterChain(v);
    ref.previewNode.style.backdropFilter = ok ? v : '';
    ref.previewNode.style.webkitBackdropFilter = ok ? v : '';
    if (ref.previewHost) {
      ref.previewHost.title = ok
        ? '预览：' + v + '（只对预览条背后的斜纹生效）'
        : '当前没有可预览的滤镜链（留空 = 不生效）';
    }
  } else if (ref.item.type === 'gradient') {
    ok = !!v && isSafeGradient(v);
    ref.previewNode.style.backgroundImage = ok ? v : '';
    if (ref.previewHost) {
      ref.previewHost.title = ok ? '预览：' + v : '当前没有可预览的渐变（留空 = 不生效）';
    }
  }
}

/** 「角度 + 起止色」→ 写回文本框，再派发 input/change 事件走原有的输入流程
 *  （校验、脏标记、预览全部复用同一套代码，不另开路径）。 */
function composeGradient(textInput, angleInput, fromInput, toInput) {
  var angle = parseInt(angleInput.value, 10);
  if (!isFinite(angle)) angle = 135;
  angle = Math.round(clamp(angle, 0, 359));
  textInput.value = 'linear-gradient(' + angle + 'deg, ' + fromInput.value + ' 0%, '
    + toInput.value + ' 100%)';
  textInput.dispatchEvent(new Event('input', { bubbles: true }));
  textInput.dispatchEvent(new Event('change', { bubbles: true }));
  if (typeof textInput.focus === 'function') textInput.focus();
}

/** 这一行里是不是有焦点（正在输入 / 拖滑块 / 点快组控件）。 */
function rowHoldsFocus(ref) {
  var active = document.activeElement;
  if (!active || active === document.body) return false;
  return !!(ref && ref.container && ref.container.contains(active));
}

function buildRow(item, countNode) {
  var isColor = item.type === 'color';
  var isImage = item.type === 'image';
  var isSwitch = item.type === 'switch';
  var isSelect = item.type === 'select';
  var isFilter = item.type === 'filter';
  var isGradient = item.type === 'gradient';

  var stateTag = el('span', { class: 'row-state' });
  var label = el('div', { class: 'row-label', title: item.key }, [
    el('span', { class: 'label-line' }, [
      el('span', { class: 'label-main', text: item.label }),
      stateTag
    ]),
    el('span', { class: 'k', text: item.key })
  ]);

  var colorInput = null;
  var hexInput = null;
  var alpha = null;
  var alphaVal = null;
  var textInput = null;
  var nativeCell = null;
  var imageField = null;
  var imgSwatch = null;
  var switchInput = null;
  var switchField = null;
  var selectInput = null;
  var selectField = null;
  var previewHost = null;   // filter / gradient 行右侧的预览条外壳
  var previewNode = null;   // 真正承载 backdrop-filter / background-image 的那一层
  var quickRow = null;      // gradient 行的「角度 + 起止色 + 生成」快组
  var gradAngle = null;
  var gradFrom = null;
  var gradTo = null;
  var gradGen = null;

  if (isSwitch) {
    // 开关控件：勾选 = 1（原生观感），未勾选 = 0（完全清透）。
    // 用 '1' / '0' 两个字符串写进 edited，和 number 变量共用后端校验。
    switchInput = el('input', {
      type: 'checkbox', class: 'veil-switch',
      'aria-label': item.label
    });
    switchInput.addEventListener('change', function () {
      markEdited(item.key, switchInput.checked ? '1' : '0');
      syncRow(item.key);
    });
    switchField = el('div', { class: 'field switch-field' }, [
      switchInput,
      el('span', { class: 'switch-text', text: '开关' })
    ]);
    nativeCell = el('span', {
      class: 'row-native',
      title: '原生观感 = 打开',
      text: '原生 开'
    });
  }

  if (isSelect) {
    // 下拉类变量：选项写死在 CATALOG 的 options 里，选完立刻 markEdited；
    // 与 color/switch 一样走 edited 表，提交仍由「应用」统一完成。
    selectInput = el('select', {
      class: 'fit-select',
      'aria-label': item.label
    });
    (item.options || []).forEach(function (opt) {
      selectInput.appendChild(el('option', { value: opt.value, text: opt.label }));
    });
    selectInput.addEventListener('change', function () {
      markEdited(item.key, selectInput.value);
      syncRow(item.key);
    });
    selectField = el('div', { class: 'field' }, [selectInput]);
    nativeCell = el('span', {
      class: 'row-native',
      title: '原生写法：' + (nativeOf(item) || ''),
      text: '原生 ' + selectOptionLabel(item, nativeOf(item))
    });
  }

  if (isColor) {
    colorInput = el('input', {
      type: 'color', class: 'swatch', value: '#000000',
      'aria-label': item.label + ' 色板'
    });
    hexInput = el('input', {
      type: 'text',
      class: 'hex',
      spellcheck: 'false',
      autocomplete: 'off',
      placeholder: placeholderFor(item),
      'aria-label': item.label + ' 颜色值',
      oninput: function () {
        var raw = hexInput.value.trim();
        if (!raw) { clearRow(item.key); setInputInvalid(hexInput, item, false); syncRow(item.key, { keepHex: true, defer: true }); return; }
        if (raw.toLowerCase() === 'currentcolor') {
          // currentcolor 是合法 CSS 关键字（后端/loader 都收），但没有固定 RGB：
          // 原样存字符串，不能为了「渲染色块」把它盖成黑色，也不能标红。
          setInputInvalid(hexInput, item, false);
          markRowInvalid(item.key, false);
          markEdited(item.key, 'currentcolor');
          syncRow(item.key, { keepHex: true, defer: true });
          return;
        }
        var parsed = parseColor(raw);
        if (!parsed) { setInputInvalid(hexInput, item, true); markRowInvalid(item.key, true); return; }
        setInputInvalid(hexInput, item, false);
        markRowInvalid(item.key, false);
        // 只有 6/3 位 hex 没有自己的 alpha，才继承当前值的 alpha；transparent、
        // 4/8 位 hex、rgba(...) 一律用解析出的 alpha。旧实现一律走 currentAlpha()，
        // 而 currentAlpha 对全透明返回 1 → 输入 "transparent" 的过程中被记成
        // rgba(0,0,0,1) 纯黑，Ctrl+Enter（不要求失焦）直接写盘。
        var isPlainHex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(raw);
        var alphaOut = isPlainHex ? currentAlpha(item.key) : parsed.a;
        rememberRgbValue(item.key, parsed, raw);
        markEdited(item.key, toRgba(parsed.r, parsed.g, parsed.b, alphaOut));
        // keepHex：输入框保留用户正在敲的原文，否则 "#ff8" 会被当成 3 位色改写，
        // 后续按键拼出用户没输入过的值（实测 #ff8800 → #ffff8880）。
        syncRow(item.key, { keepHex: true, defer: true });
      },
      onchange: function () {
        // 失焦/回车才算定稿：只有此时才从 4/8 位 hex 里取 alpha 并归一化展示。
        // 逐键过程中保持原有 alpha，否则 6 位色 "#ff8800" 的 4 位前缀 "#ff88"
        // 会被当成 53% 透明的 4 位色，把用户输入的实色变成半透明。
        var raw = hexInput.value.trim();
        if (!raw) return;
        if (raw.toLowerCase() === 'currentcolor') {
          setInputInvalid(hexInput, item, false);
          markRowInvalid(item.key, false);
          markEdited(item.key, 'currentcolor');
          syncRow(item.key);
          return;
        }
        var parsed = parseColor(raw);
        if (!parsed) { setInputInvalid(hexInput, item, true); markRowInvalid(item.key, true); return; }
        setInputInvalid(hexInput, item, false);
        markRowInvalid(item.key, false);
        rememberRgbValue(item.key, parsed, raw);
        markEdited(item.key, toRgba(parsed.r, parsed.g, parsed.b, parsed.a));
        syncRow(item.key);
      }
    });

    alpha = el('input', {
      type: 'range', min: '0', max: '100', step: '1', value: '100',
      'aria-label': item.label + ' 不透明度',
      oninput: function () {
        var a = Number(alpha.value) / 100;
        var parsed = parseColor(displayValue(item.key));
        // 不是靠猜颜色：全透明行若记得上一次的 RGB（lastRgbByKey），就沿用它
        // 把不透明度拖回来 —— 旧实现此时滑块 disabled、currentAlpha 强制 1，
        // 用户再也调不回半透明。只有完全没有 RGB 记忆时才不猜（否则纯黑）。
        var rgb = parsed && parsed.a > 0 ? parsed : lastRgbByKey[item.key];
        // 拖动滑块会连发 input 事件：行内立刻更新，全局刷新按帧合并。
        if (!rgb) { syncRow(item.key, { defer: true }); return; }
        rememberRgbValue(item.key, rgb, '');
        markEdited(item.key, toRgba(rgb.r, rgb.g, rgb.b, a));
        syncRow(item.key, { defer: true });
      }
    });
    alphaVal = el('span', { class: 'alpha-val', text: '100%' });

    colorInput.addEventListener('input', function () {
      var parsed = parseColor(colorInput.value);
      if (!parsed) return;
      rememberRgbValue(item.key, parsed, '#');
      markEdited(item.key, toRgba(parsed.r, parsed.g, parsed.b, currentAlpha(item.key)));
      // 拖动取色器同样会连发 input：合并到下一帧再刷全局。
      syncRow(item.key, { defer: true });
    });
  } else if (isImage) {
    // 背景图行：合法值只有 none 或 url("…/backgrounds/<文件>")。选图先由后端落盘并
    // 回读成 url(...)，再走既有 markEdited / cleared 机制 —— 提交、脏标记、collect()
    // 都不另开路径，applyTheme 里照旧提交。
    imgSwatch = el('div', { class: 'img-swatch', 'aria-hidden': 'true' });
    var imageFileInput = el('input', {
      type: 'file',
      class: 'visually-hidden',
      accept: 'image/png,image/jpeg,image/webp,image/gif',
      tabindex: '-1',
      'aria-label': item.label + ' 选择图片文件',
      onchange: function () {
        var file = imageFileInput.files && imageFileInput.files[0];
        if (file) pickBackgroundImage(item, file, imageFileInput);
      }
    });
    var pickBtn = el('button', {
      type: 'button', class: 'btn btn-mini', 'data-act': 'pick', text: '选择图片',
      'aria-label': '选择' + item.label,
      onclick: function () { if (imageFileInput) imageFileInput.click(); }
    });
    var clearImageBtn = el('button', {
      type: 'button', class: 'btn btn-mini btn-ghost', 'data-act': 'clear', text: '清除',
      'aria-label': '清除' + item.label,
      onclick: function () {
        // 与 .row-clear 等价：走 cleared 机制，提交空串 = 删掉 theme.json 里的这一项。
        if (!isDirty(item.key) && serverValue(item.key) === null) {
          showToast('该行本来就是原生值');
          return;
        }
        var removed = clearRow(item.key);
        syncRow(item.key);
        if (!removed) showToast('已撤销本行改动');
      }
    });
    // 第二列位置放整块图片控件，并横跨「色块 + 编辑框」两列；行内没有文本输入框，
    // 因此这一行的子节点只有 4 个（标签 / 图片控件 / 原生值 / 清空）。
    imageField = el('div', {
      class: 'field image-field',
      style: 'grid-column: 2 / span 2;'
    }, [
      imgSwatch,
      el('div', { class: 'img-actions' }, [
        pickBtn,
        clearImageBtn,
        el('span', { class: 'img-hint', text: '图片会盖在背景色之上' })
      ]),
      imageFileInput
    ]);
    nativeCell = el('span', {
      class: 'row-native',
      title: nativeOf(item) ? '原生值：' + nativeOf(item) : '该变量没有统一的原生值',
      text: nativeOf(item) ? '原生 ' + nativeOf(item) : ''
    });
  } else {
    textInput = el('input', {
      type: 'text',
      class: 'raw',
      spellcheck: 'false',
      autocomplete: 'off',
      placeholder: placeholderFor(item),
      'aria-label': item.label,
      oninput: function () {
        // 注意：这里不能对 value 做 trim 后再回写。多 token 的值（阴影、字体族）
        // 是逐个字符敲出来的，行尾空格一被 trim 掉，下一个 token 就会粘在前一个
        // 上 —— 实测 "0 2px 4px red" 被录成 "02px4px red"，"Segoe UI" 变成
        // "SegoeUI"。所以：校验用 trim 后的值，显示保留用户原文。
        var raw = textInput.value;
        var trimmed = normalizeTypedValue(item, raw.trim());
        if (!trimmed) { clearRow(item.key); setInputInvalid(textInput, item, false); syncRow(item.key, { keepText: true, defer: true }); return; }
        if (!isSafeValue(item.key, trimmed)) {
          // 非法值只标红还不够：之前 edited 里仍留着上一个合法值，applyTheme
          // 会把它提交上去并报「已应用」，用户以为刚敲的值生效了。
          // 这里回滚到上一个合法编辑值（没有就退回服务器值）并记入非法行集合，
          // applyTheme 见到红框行会直接阻止应用。
          setInputInvalid(textInput, item, true);
          markRowInvalid(item.key, true);
          rollbackRowToLastValid(item.key);
          syncRow(item.key, { keepText: true, defer: true });
          return;
        }
        setInputInvalid(textInput, item, false);
        markRowInvalid(item.key, false);
        markEdited(item.key, trimmed);
        syncRow(item.key, { keepText: true, defer: true });
      },
      onchange: function () {
        // 失焦/回车时定稿：去掉首尾空白，再做一次完整校验。
        var trimmed = normalizeTypedValue(item, textInput.value.trim());
        textInput.value = trimmed;
        if (!trimmed) { clearRow(item.key); setInputInvalid(textInput, item, false); syncRow(item.key); return; }
        if (!isSafeValue(item.key, trimmed)) {
          setInputInvalid(textInput, item, true);
          markRowInvalid(item.key, true);
          rollbackRowToLastValid(item.key);
          syncRow(item.key);
          return;
        }
        setInputInvalid(textInput, item, false);
        markRowInvalid(item.key, false);
        markEdited(item.key, trimmed);
        syncRow(item.key);
      }
    });
    if (isFilter || isGradient) {
      // 预览条和输入框同一行：输入框允许伸缩，预览条固定宽度（内联样式，
      // 因为本轮不能改 style.css）。
      textInput.style.flex = '1 1 140px';
      textInput.style.width = 'auto';
      var previewParts = buildPreviewHost(
        isFilter ? '滤镜预览：背后的斜纹会被当前模糊链处理' : '渐变预览'
      );
      previewHost = previewParts.host;
      previewNode = previewParts.node;
    }
    if (isGradient) {
      // 快组：角度 + 起止色 + 生成。只在本地点「生成」时才写入输入框，
      // 不点就不改值 —— 否则一打开面板就凭空多出一项改动。
      gradAngle = el('input', {
        type: 'number', min: '0', max: '360', step: '1', value: '135',
        'aria-label': item.label + ' 渐变角度',
        style: 'flex:0 0 64px;width:64px;height:26px;padding:0 6px;'
      });
      gradFrom = el('input', {
        type: 'color', class: 'swatch', value: '#eef4ff',
        'aria-label': item.label + ' 起始色',
        style: 'flex:0 0 28px;width:28px;height:26px;'
      });
      gradTo = el('input', {
        type: 'color', class: 'swatch', value: '#dfe9ff',
        'aria-label': item.label + ' 结束色',
        style: 'flex:0 0 28px;width:28px;height:26px;'
      });
      gradGen = el('button', {
        type: 'button', class: 'btn btn-mini', text: '生成',
        title: '按角度与起止色生成 linear-gradient 并写入输入框',
        'aria-label': '生成' + item.label,
        onclick: function () { composeGradient(textInput, gradAngle, gradFrom, gradTo); }
      });
      quickRow = el('div', {
        style: 'display:flex;align-items:center;gap:6px;flex:1 1 100%;min-width:0;'
      }, [
        el('span', { style: 'flex:none;font-size:11px;color:var(--text-faint);', text: '角度' }),
        gradAngle,
        gradFrom,
        gradTo,
        gradGen
      ]);
    }
    nativeCell = el('span', {
      class: 'row-native',
      title: nativeOf(item) ? '原生值：' + nativeOf(item) : '该变量没有统一的原生值',
      text: nativeOf(item) ? '原生 ' + nativeOf(item) : ''
    });
  }

  var field;
  if (imageField) {
    field = imageField;
  } else if (switchField) {
    field = switchField;
  } else if (selectField) {
    field = selectField;
  } else if (previewHost) {
    // filter / gradient 行：输入框 + 预览条排第一行，渐变快组换第二行（row-gap 补间距）。
    field = el('div', { class: 'field', style: 'flex-wrap:wrap;row-gap:6px;' },
      [isColor ? hexInput : textInput, previewHost].concat(quickRow ? [quickRow] : []));
  } else {
    field = el('div', { class: 'field' }, [isColor ? hexInput : textInput]);
  }

  var alphaCell = el('div', { class: 'alpha-cell' });
  if (isColor) {
    alphaCell.appendChild(el('span', { class: 'alpha-label', text: '透明' }));
    alphaCell.appendChild(alpha);
    alphaCell.appendChild(alphaVal);
  }

  var clearBtn = el('button', {
    type: 'button', class: 'row-clear', text: '清空',
    title: '清空该行 = 回退到 N.E.K.O 原生值',
    'aria-label': '清空 ' + item.label,
    onclick: function () {
      // 干净且服务器上没有值的行：「清空」无事可做，不能凭空造出一项改动。
      if (!isDirty(item.key) && serverValue(item.key) === null) {
        showToast('该行本来就是原生值');
        return;
      }
      var removed = clearRow(item.key);
      syncRow(item.key);
      if (!removed) showToast('已撤销本行改动');
    }
  });

  var copyBtn = el('button', {
    type: 'button', class: 'row-icon row-copy', text: '⧉',
    title: '复制这一行的值（可以粘贴到别的行，甚至另一组亮/暗）',
    'aria-label': '复制 ' + item.label,
    onclick: function () { copyRowValue(item); }
  });
  var pasteBtn = el('button', {
    type: 'button', class: 'row-icon row-paste', text: '⇩',
    title: '把上次复制（或剪贴板里）的值粘贴到这一行',
    'aria-label': '粘贴到 ' + item.label,
    onclick: function () { pasteRowValue(item); }
  });
  var rowExtra = el('div', { class: 'row-extra' }, [copyBtn, pasteBtn, clearBtn]);

  // 网格五列：标签 | 色块或类型标签 | 编辑框 | 透明度或原生值 | 复制/粘贴/清空
  // 非颜色行没有色块，用同宽类型标签占位，保证输入框左边缘对齐。
  // 图片行特殊：整块图片控件占「色块 + 编辑框」两列（见上面的 image-field），
  // 所以这一行的子节点是 4 个而不是 5 个。
  var rowChildren;
  if (isImage) {
    rowChildren = [label, imageField, nativeCell || el('span', {}), rowExtra];
  } else {
    rowChildren = [
      label,
      isColor && colorInput ? colorInput : el('span', { class: 'type-tag', text: TYPE_LABEL[item.type] || item.type }),
      field,
      isColor ? alphaCell : (nativeCell || el('span', {})),
      rowExtra
    ];
  }
  var rowEl = el('div', { class: 'row' }, rowChildren);

  var hint = null;
  if (item.hint) {
    hint = el('div', { class: 'hint', title: item.hint, text: item.hint });
  }

  var container = el('div', { class: 'row-wrap' }, [rowEl, hint]);
  // 「只看改动」开着时，把值敲回原值会让这一行变干净；若此刻立刻 hidden，
  // 正在这一行里输入的人会整块消失、输入框失焦。先保留到焦点真正离开，
  // 再由 focusout 收走（见 applyFilters 的 rowHoldsFocus）。
  container.addEventListener('focusout', function () {
    if (!state.onlyDirty) return;
    window.setTimeout(function () {
      if (container.contains(document.activeElement)) return;
      applyFilters();
    }, 0);
  });
  rowsByKey[item.key] = {
    rowEl: rowEl, colorInput: colorInput, hexInput: hexInput,
    alpha: alpha, alphaVal: alphaVal, textInput: textInput, clearBtn: clearBtn,
    nativeCell: nativeCell, stateTag: stateTag, container: container,
    imgSwatch: imgSwatch, imageField: imageField,
    switchInput: switchInput,
    selectInput: selectInput,
    previewHost: previewHost, previewNode: previewNode,
    gradAngle: gradAngle, gradFrom: gradFrom, gradTo: gradTo, gradGen: gradGen,
    item: item, countNode: countNode
  };

  countNode.dataset.total = String(Number(countNode.dataset.total || 0) + 1);
  return container;
}

/* ---------------- 背景图片上传（聊天背景图行专用） ----------------
 * 图片不能像颜色那样直接写进变量：文件要先交给后端落盘（校验体积/类型），
 * 再拿回 /api/theme_studio/backgrounds/<文件> 的地址拼成 url("...")。所以这里只做
 * 「读文件 → 调 import_background → markEdited」，提交仍由 applyTheme 完成。 */

function pickBackgroundImage(item, file, input) {
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (!base64) {
      showToast('读取图片失败', 'error');
      if (input) input.value = '';
      return;
    }
    uploadBackgroundImage(item, file.name, base64, input);
  };
  reader.onerror = function () {
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', '读取图片失败：' + why);
    showToast('读取图片失败', 'error');
    if (input) input.value = '';
  };
  reader.readAsDataURL(file);
}

async function uploadBackgroundImage(item, filename, base64, input) {
  setBusy(true, '正在上传背景图片…');
  try {
    var result = await callPlugin('import_background', { filename: filename, data: base64 });
    var url = result && result.url ? String(result.url) : '';
    if (!url) throw new Error('后端没有返回图片地址');
    markEdited(item.key, 'url("' + url + '")');
    syncRow(item.key);
    setStatus('已选择背景图片', 'ok');
    showToast('已选择背景图片', 'ok');
  } catch (error) {
    showNotice('error', '背景图片上传失败：' + pluginErrorMessage(error));
    showToast('背景图片上传失败', 'error');
    setStatus('背景图片上传失败', 'error');
  } finally {
    // 清空 input：否则再选同一个文件不会触发 change。
    if (input) input.value = '';
    setBusy(false);
  }
}

function currentAlpha(key) {
  var raw = Object.prototype.hasOwnProperty.call(editedMap(), key) ? editedMap()[key] : displayValue(key);
  var parsed = parseColor(raw);
  // 当前值全透明（transparent / rgba(...,0)）时不能把 alpha=0 继承给新颜色：
  // 用户从色板选一个实色，提交的却是 rgba(...,0)，面板显示「已改」而画面
  // 依旧完全透明 —— 看起来就是「改了没反应」。色板选色默认按实色处理。
  // 注意：alpha 滑块不走这里，它用 lastRgbByKey 里记住的 RGB 调不透明度。
  if (!parsed || parsed.a === 0) return 1;
  return parsed.a;
}

/** 把某一行的控件同步成当前状态。opts.keepHex=true 时不动正在编辑的 hex 输入框。 */
/* ---- 编辑区全局刷新的合并（v0.4.17） -------------------------------------
 * syncRow 以前每被调用一次，末尾都要重算分组计数、预览、JSON dump、按钮状态。
 * 一次全量同步（syncAll 遍历 95 行）就把这套重活干了 96 遍；逐键输入时也是
 * 「每敲一个字符一遍」。这里把「行内 DOM 更新」与「全局刷新」拆开：
 * 行内必须立刻更新（输入框不能延迟），全局合并到下一帧。
 * 策略函数 editorSyncPlan 是纯函数，由 test/editor-selftest.js 直接锁住。
 */
var editorSyncState = { pending: false, flushes: 0, scheduled: 0 };

/* ===== EDITOR_SYNC_BEGIN ===== */
/**
 * 这次 syncRow 之后全局刷新该怎么做。
 * @param {object|undefined} opts syncRow 的可选参数
 * @returns {'none'|'deferred'|'immediate'}
 */
function editorSyncPlan(opts) {
  if (opts && opts.silent) return 'none';
  if (opts && opts.defer) return 'deferred';
  return 'immediate';
}

/** 下一帧执行；没有 requestAnimationFrame 时退回 16ms 定时器。 */
function nextFrame(fn) {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
  return setTimeout(fn, 16);
}

/** 立刻做一次全局刷新（分组计数 / 预览 / JSON dump / 按钮状态 / 只看改动过滤）。 */
function flushEditorGlobals() {
  editorSyncState.pending = false;
  editorSyncState.flushes += 1;
  refreshCounts();
  renderPreview();
  renderDump();
  updateButtons();
  // 「只看改动」开着时，行被改回干净状态就该从列表里收走；但正在输入的行必须
  // 留着（applyFilters 会保留焦点行），否则输入框瞬间失焦、行整块消失。
  if (state.onlyDirty) applyFilters();
}

/** 合并到下一帧再刷新；同一帧内多次调用只刷一次。 */
function scheduleEditorGlobals() {
  if (editorSyncState.pending) return;
  editorSyncState.pending = true;
  editorSyncState.scheduled += 1;
  nextFrame(flushEditorGlobals);
}
/* ===== EDITOR_SYNC_END ===== */

function syncRow(key, opts) {
  var ref = rowsByKey[key];
  if (!ref) return;
  var value = displayValue(key);
  var dirty = isDirty(key);

  ref.rowEl.classList.toggle('is-dirty', dirty);

  if (ref.stateTag) {
    var cleared = Object.prototype.hasOwnProperty.call(clearedMap(), key);
    ref.stateTag.textContent = dirty ? (cleared ? '已清空' : '已改') : '';
  }

  if (ref.hexInput) ref.hexInput.placeholder = placeholderFor(ref.item);
  if (ref.textInput) ref.textInput.placeholder = placeholderFor(ref.item);
  if (ref.nativeCell) {
    var native = nativeOf(ref.item);
    ref.nativeCell.textContent = native ? '原生 ' + native : '';
    ref.nativeCell.title = native ? '原生值：' + native : '该变量没有统一的原生值';
  }

  if (ref.item.type === 'color') {
    var parsed = parseColor(value);
    var fullyTransparent = !!parsed && parsed.a === 0;
    if (parsed) {
      // 记住最近一次带颜色的 RGB：transparent 之后拖动 alpha 滑块要靠它找回颜色。
      // rememberRgbValue 内部会跳过 transparent / rgba(...,0) 字面量。
      rememberRgbValue(key, parsed, value);
      // 全透明的 rgba(...,0) / #0000 若按 RGB 显示成 #000000，用户会以为颜色
      // 被改成了黑色；这里显式显示 transparent，和 alpha=0% 一起表达真实状态。
      if (!(opts && opts.keepHex)) {
        ref.hexInput.value = fullyTransparent ? 'transparent' : toHex(parsed.r, parsed.g, parsed.b);
      }
      ref.colorInput.value = toHex(parsed.r, parsed.g, parsed.b);
      ref.alpha.value = String(Math.round(parsed.a * 100));
      ref.alphaVal.textContent = Math.round(parsed.a * 100) + '%';
      setInputInvalid(ref.hexInput, ref.item, false);
      markRowInvalid(key, false);
    } else {
      // 值是 inherit / 空 等非颜色字面量：文本原样展示，控件退回中性
      // 色块也要跟着退，否则清空/重置后色块仍显示上一次的颜色，看起来像没生效。
      ref.hexInput.value = value || '';
      if (ref.colorInput) ref.colorInput.value = '#000000';
      ref.alpha.value = '100';
      ref.alphaVal.textContent = '100%';
    }
    // transparent / inherit / 空 都没有当前 RGB：滑块只在「有 RGB 记忆」时可用
    // （拖动会沿用上一次的颜色，见 lastRgbByKey）。完全没记忆时置灰，否则拖动
    // 会得到 rgba(0,0,0,x) 纯黑半透明，用户看到的是「一拖就变黑」。
    if (ref.alpha) {
      var hasRgbMemory = !!lastRgbByKey[key];
      ref.alpha.disabled = !!state.busy || !parsed || (fullyTransparent && !hasRgbMemory);
      if (!parsed) {
        ref.alpha.title = '当前值没有颜色成分（transparent / inherit / 空）；先填一个颜色再调不透明度';
      } else if (fullyTransparent && !hasRgbMemory) {
        ref.alpha.title = '当前值没有颜色成分（transparent）；先填一个颜色再调不透明度';
      } else if (fullyTransparent) {
        ref.alpha.title = '拖动不透明度会沿用上一次的颜色';
      } else {
        ref.alpha.title = '';
      }
    }
  } else if (ref.item.type === 'switch') {
    if (ref.switchInput) {
      // 显示值可能是 '1' / '0' / 空（空 = 服务器没有值 = 原生观感 = 打开）
      var raw = value === null || value === undefined ? '' : String(value).trim();
      ref.switchInput.checked = raw === '' ? true : !(raw === '0' || raw === '0.0');
    }
  } else if (ref.item.type === 'select') {
    // 空值/未知值一律显示成「原生项」（= 未设置的外观），避免下拉框停在第一项造成误导。
    if (ref.selectInput) {
      var selectValue = value === null || value === undefined ? '' : String(value).trim().toLowerCase();
      var known = '';
      (ref.item.options || []).forEach(function (opt) {
        if (!known && String(opt.value).toLowerCase() === selectValue) known = String(opt.value);
      });
      if (!known) known = String(ref.item.native || ((ref.item.options || [])[0] || {}).value || '');
      ref.selectInput.value = known;
      ref.selectInput.disabled = !!state.busy;
    }
    markRowInvalid(key, false);
  } else if (ref.item.type === 'image') {
    // 有图才画图；无图留空，CSS 用斜纹表示「没有背景图」。切换模式/重新读取后
    // 这里会显示对应模式的 url(...)。
    if (ref.imgSwatch) {
      var imgValue = value ? String(value) : '';
      var hasImage = !!imgValue && imgValue !== 'none' && imgValue.indexOf('url(') === 0;
      ref.imgSwatch.style.backgroundImage = hasImage ? imgValue : '';
      ref.imgSwatch.title = hasImage ? '当前背景图片' : '未设置背景图片（跟随原生）';
    }
    markRowInvalid(key, false);
  } else {
    if (!(opts && opts.keepText)) {
      ref.textInput.value = value || '';
      // 回显的是当前有效值，红框/非法行标记必须一起清掉；
      // 否则「清空」或撤销后红框还留着，applyTheme 会一直被非法行拦住。
      setInputInvalid(ref.textInput, ref.item, false);
      markRowInvalid(key, false);
    }
  }

  // filter / gradient：预览条跟着当前生效值走（空/非法只留底纹）。
  if (ref.previewNode) syncValuePreview(ref, value);

  // 行内 DOM 已经更新完，全局部分按策略走：全量同步时逐行静默、输入时合并到
  // 下一帧、其余（失焦定稿、清空、读回主题）仍然立刻刷新，行为与以前一致。
  var plan = editorSyncPlan(opts);
  if (plan === 'immediate') flushEditorGlobals();
  else if (plan === 'deferred') scheduleEditorGlobals();
}

function syncAll() {
  Object.keys(rowsByKey).forEach(function (key) {
    syncRow(key, { silent: true });
  });
  flushEditorGlobals();
}

function refreshCounts() {
  CATALOG.forEach(function (group) {
    var total = group.items.length;
    var dirty = group.items.filter(function (item) { return isDirtyAny(item.key); }).length;
    // 找到该组的计数节点：通过任意一行的 countNode
    var node = rowsByKey[group.items[0].key] && rowsByKey[group.items[0].key].countNode;
    if (!node) return;
    node.textContent = '';
    if (dirty) {
      node.appendChild(el('span', { class: 'is-dirty-count', text: dirty + ' 项已改' }));
      node.appendChild(el('span', { class: 'dot', text: '·' }));
    }
    node.appendChild(el('span', { text: total + ' 项' }));
  });
}

/* ---------------- 过滤：搜索 / 只看改动 ---------------- */

/** 该键在任一模式（亮/暗）里是否被改过或清空过。 */
function isDirtyAny(key) {
  return Object.prototype.hasOwnProperty.call(state.edited.light, key) ||
    Object.prototype.hasOwnProperty.call(state.edited.dark, key) ||
    Object.prototype.hasOwnProperty.call(state.cleared.light, key) ||
    Object.prototype.hasOwnProperty.call(state.cleared.dark, key);
}

/** 只按搜索词匹配（不含「只看改动」条件）；保留焦点行时也要靠它判断。 */
function rowMatchesQuery(item, query) {
  if (!query) return true;
  var hay = (item.label + ' ' + item.key + ' ' + (item.hint || '')).toLowerCase();
  return hay.indexOf(query) !== -1;
}

function rowMatches(item, query) {
  if (state.onlyDirty && !isDirtyAny(item.key)) return false;
  return rowMatchesQuery(item, query);
}

/**
 * 把「搜索词 + 只看改动」应用到行与分组上：
 * - 行级：row-wrap[hidden]
 * - 组级：没有命中行的组整体隐藏
 * - 过滤时强制展开，否则命中项藏在折叠组里等于没找到
 */
function applyFilters() {
  var query = (state.filter || '').trim().toLowerCase();
  var filtering = !!query || state.onlyDirty;
  var shown = 0;

  CATALOG.forEach(function (group) {
    var section = groupNodes[group.group];
    var visible = 0;
    group.items.forEach(function (item) {
      var ref = rowsByKey[item.key];
      if (!ref) return;
      var match = rowMatches(item, query);
      // 焦点行：值刚被敲回原值、从「只看改动」里掉了出去，但用户还在这一行里，
      // 隐藏等于把输入框抢走。先留着，focusout 之后再收。
      if (!match && rowHoldsFocus(ref) && rowMatchesQuery(item, query)) match = true;
      if (ref.container) ref.container.hidden = !match;
      if (match) { visible += 1; shown += 1; }
    });
    if (section) {
      section.hidden = visible === 0;
      section.classList.toggle('is-collapsed', !filtering && !!state.collapsed[group.group]);
      var head = section.querySelector('.group-head');
      if (head) head.setAttribute('aria-expanded', section.classList.contains('is-collapsed') ? 'false' : 'true');
    }
  });

  var noMatch = $('no-match');
  if (noMatch) noMatch.hidden = !(filtering && shown === 0);

  var clear = $('search-clear');
  if (clear) clear.hidden = !query;
}

function setFilter(value) {
  state.filter = value || '';
  applyFilters();
}

function clearFilter() {
  var input = $('row-search');
  if (input) input.value = '';
  state.filter = '';
  applyFilters();
  if (input) input.focus();
}

function setOnlyDirty(on) {
  state.onlyDirty = !!on;
  applyFilters();
}

/* ============================================================================
 * 8. 预览（内联样式，纯本地模拟）
 * ==========================================================================*/

/* ===== PANEL_FRESHNESS_BEGIN（v0.9.0：面板版本自检）=====
 * 宿主静态服务把 index.html 也当缓存资源发（max-age=3600）。升级后一小时内
 * 重开面板，浏览器可能直接给旧页面 —— 于是「新功能没有」其实只是页面没换。
 * 这里用 no-store 再要一次 index.html，只比版本号；不一致就带时间戳重载一次。
 */
var PANEL_FRESHNESS_DELAY_MS = 3000;
var PANEL_FRESHNESS_KEY = 'nts.panel.refreshed.';

function panelVersionOf(text) {
  var match = /v(\d+\.\d+\.\d+)/.exec(String(text || ''));
  return match ? match[1] : '';
}

async function checkPanelFreshness() {
  var node = $('panel-version');
  var local = panelVersionOf(node ? node.textContent : '');
  if (!local) return false;
  var response;
  try {
    // 本体内置版没有独立的 index.html：面板就是 /theme_studio 这个页面本身，
    // 所以重新拉当前页，用模板渲染出的 #panel-version 判断服务端是不是换了新面板。
    var pageUrl = String(window.location.pathname || '/theme_studio');
    response = await fetchWithTimeout(pageUrl + '?_=' + Date.now(), {
      cache: 'no-store', credentials: 'same-origin'
    }, 4000);
  } catch (error) {
    return false; // 拿不到就算了：这是锦上添花，不打扰用户
  }
  if (!response || !response.ok) return false;
  var html = '';
  try { html = await response.text(); } catch (error) { return false; }
  var holder = /id="panel-version"[^>]*>([^<]*)</.exec(String(html || ''));
  var remote = panelVersionOf(holder ? holder[1] : '');
  if (!remote || remote === local) return false;
  var key = PANEL_FRESHNESS_KEY + remote;
  try {
    if (window.sessionStorage && window.sessionStorage.getItem(key)) return false;
    if (window.sessionStorage) window.sessionStorage.setItem(key, '1');
  } catch (error) { /* 隐私模式禁用存储：仍然刷一次，靠版本变化自然收敛 */ }
  showNotice('warn', '面板界面已更新到 v' + remote + '（当前页还是 v' + local +
    '），正在重新加载…');
  setTimeout(function () {
    try { window.location.reload(); } catch (error) { /* 忽略 */ }
  }, 700);
  return true;
}
/* ===== PANEL_FRESHNESS_END ===== */

/* ===== HOVER_PREVIEW_BEGIN（v0.9.0：主题卡悬停预演）=====
 * 悬停仓库卡片时，把右侧预览临时换成那一套主题的颜色，移开就恢复。
 * 刻意只改预览：不调 applyTheme / 不写盘 / 不动 state.edited，
 * 否则「只是看一眼」会变成「改了面板里的草稿」。
 * 数据源就是主题文件本身（/api/theme_studio/presets|themes/<id>.json）——
 * 调 entry 要过一遍校验与写锁，悬停根本来不及；而这里只是读一个 JSON。
 */
var hoverPreview = {
  seq: 0,            // 只认最后一次请求，避免慢请求回来盖掉新的
  themeId: '',
  name: '',
  palette: null,     // { builtin, vars, dark_vars }
  cache: Object.create(null)
};

function hoverPreviewMap() {
  if (!hoverPreview.palette) return null;
  var map = state.mode === 'dark' ? hoverPreview.palette.dark_vars : hoverPreview.palette.vars;
  return map && typeof map === 'object' ? map : null;
}

/** 预演值：命中就返回，未命中返回 null（让 pvValue 落回当前调色板的逻辑）。 */
function hoverPreviewValue(key) {
  var map = hoverPreviewMap();
  if (!map || !Object.prototype.hasOwnProperty.call(map, key)) return null;
  var value = map[key];
  return value === null || value === undefined ? '' : String(value);
}

/** 内置主题里的图是可移植写法 url("images/<hash>")，预览要换成能直接访问的地址。 */
function rewritePreviewRefs(map, builtin) {
  var out = {};
  if (!map || typeof map !== 'object') return out;
  Object.keys(map).forEach(function (key) {
    var value = map[key];
    var text = String(value === null || value === undefined ? '' : value);
    var match = /url\(\s*["']?images\/([^"')]+)["']?\s*\)/.exec(text);
    if (match) {
      var imageUrl = builtin
        ? API_BASE + '/presets/images/' + match[1]
        : API_BASE + '/backgrounds/' + match[1];
      text = 'url("' + imageUrl + '")';
    }
    out[key] = text;
  });
  return out;
}

function hoverPreviewFetchUrl(item) {
  var id = String((item && item.id) || '');
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(id)) return '';
  return API_BASE + (item.builtin ? '/presets/' : '/themes/') + id + '.json';
}

async function showHoverPreview(item) {
  if (!item) return;
  if (state.busy) return;
  if (modal && !modal.hidden) return;
  var url = hoverPreviewFetchUrl(item);
  if (!url) return;
  hoverPreview.seq += 1;
  var seq = hoverPreview.seq;
  var cached = hoverPreview.cache[url];
  if (!cached) {
    try {
      var response = await fetchWithTimeout(url + '?v=' + Date.now(), {
        cache: 'no-store', credentials: 'same-origin'
      }, 4000);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var raw = await response.json();
      if (!raw || typeof raw !== 'object') throw new Error('主题文件不是对象');
      cached = {
        builtin: !!item.builtin,
        vars: rewritePreviewRefs(raw.vars, !!item.builtin),
        dark_vars: rewritePreviewRefs(raw.dark_vars, !!item.builtin)
      };
      // 缓存上限：仓库最多 200 条，但没必要为一个看着就走的悬停攒满内存。
      if (Object.keys(hoverPreview.cache).length < 60) hoverPreview.cache[url] = cached;
    } catch (error) {
      return; // 预演是锦上添花：网络失败就什么都不做，绝不弹错
    }
  }
  if (seq !== hoverPreview.seq) return;
  hoverPreview.themeId = String(item.id || '');
  hoverPreview.name = String(item.name || item.id || '');
  hoverPreview.palette = cached;
  renderPreview();
}

function clearHoverPreview() {
  if (!hoverPreview.palette) return;
  hoverPreview.seq += 1;
  hoverPreview.palette = null;
  hoverPreview.themeId = '';
  hoverPreview.name = '';
  renderPreview();
}
/* ===== HOVER_PREVIEW_END ===== */

function pvValue(key, fallback) {
  // 悬停预演优先：命中的键直接用那套主题的值，其余键仍走当前调色板。
  var preview = hoverPreviewValue(key);
  if (preview !== null && preview !== '') return preview;
  var v = displayValue(key);
  if (!v) return fallback || '';
  if (v.indexOf('--') === 0) return fallback || '';
  return v;
}

function renderPreview() {
  $('preview-mode').textContent = (state.mode === 'dark' ? '暗色' : '亮色') +
    (hoverPreview.palette ? ' · 预演「' + hoverPreview.name + '」' : '');

  var chatBg = pvValue('--nts-chat-bg', state.mode === 'dark' ? '#1f242a' : '#eef3f8');
  var panelBg = pvValue('--nts-chat-panel-bg', '');
  var radius = pvValue('--nts-radius', '12px');
  // 预览必须按真实消费方取圆角：外壳读 --nts-shell-radius、气泡读
  // --nts-bubble-radius，都没配时才回退通用 --nts-radius。之前全部读通用值，
  // 于是改「聊天气泡圆角」预览纹丝不动，看起来像没生效。
  var shellRadius = pvValue('--nts-shell-radius', radius);
  var bubbleRadius = pvValue('--nts-bubble-radius', radius);
  var blur = pvValue('--nts-chat-blur', '');
  // 契约允许裸长度（"18px"），真正落盘前 loader/后端会归一成 blur(18px)。
  // 预览直接拿裸长度写 backdropFilter 是非法值，浏览器整条丢弃 —— 于是
  // 明明改了模糊，预览却毫无反应（真机上更会被误判成主题没生效）。
  // 但拼接前必须确认单位合法：blur(18%) / blur(2vw) 同样是非法值，
  // 旧实现会无条件拼上去，等于把「面板校验放过的非法输入」带进预览。
  var blurCss = '';
  if (blur) {
    if (RE_BLUR.test(blur)) blurCss = blur;
    else if (RE_BLUR_LENGTH.test(blur)) blurCss = 'blur(' + blur + ')';
    else if (RE_ZERO.test(blur)) blurCss = 'blur(0)';
  }

  /* 「聊天背景虚化」开关（--nts-bubble-veil，0–1 乘子）。真机上它同时缩放气泡
   * 白纱底的不透明度与 backdrop 模糊半径 —— 预览必须真算一遍，否则拨开关时
   * 预览纹丝不动，会被当成「开关没生效」（真机其实是生效的，这种错觉最坑）。
   * 只在服务器确实有这个变量时才介入，未配置时保持预览原本的观感。 */
  var veilRaw = pvValue('--nts-bubble-veil', '');
  var veilActive = veilRaw !== '';
  var veil = 1;
  if (veilActive) {
    if (RE_NUMBER.test(veilRaw)) veil = parseFloat(veilRaw);
    else if (RE_PERCENT.test(veilRaw)) veil = parseFloat(veilRaw) / 100;
    if (!isFinite(veil) || veil < 0) veil = 0;
    if (veil > 1) veil = 1;
  }
  var veilBlur = 'blur(' + Math.round(8 * veil * 100) / 100 + 'px) saturate(1.08)';
  // 开关为 1 时的原生观感（白纱底色），与 neko-chat-window.css 的
  // .compact-export-history-bubble 亮/暗两套值一致。
  var veilUserSheet = state.mode === 'dark' ? [33, 72, 118, 0.902] : [208, 231, 255, 0.8784];
  var veilAiSheet = state.mode === 'dark' ? [33, 45, 62, 0.878] : [255, 255, 255, 0.8784];

  var preview = $('preview');
  preview.style.background = chatBg;
  preview.style.borderRadius = shellRadius;
  if (panelBg) preview.style.backgroundColor = panelBg;
  preview.style.backdropFilter = blurCss;
  preview.style.webkitBackdropFilter = blurCss;
  preview.style.boxShadow = pvValue('--nts-shadow', '');

  // 背板（消息列表底板）在预览里对应 .pv-body。圆角必须单独取
  // --nts-chat-panel-radius：它和「通用圆角」是两个不同消费方，读错了
  // 就会出现「改了背板圆角，预览里动的却是按钮」这种假象。
  var panelRadius = pvValue('--nts-chat-panel-radius', '');
  // 聊天背景图：与「背板底色」同层，图片画在 background-color 之上（所以预览里
  // 只要两者都设，看到的就是图片）。cover/center/no-repeat 必须成套设置，
  // 否则 4K 图会按原始像素平铺成一团马赛克。
  var panelImage = pvValue('--nts-chat-bg-image', '');
  var hasPanelImage = !!panelImage && panelImage !== 'none' && panelImage.indexOf('url(') === 0;
  // 背景图亮度：与 CSS 同一套算法 —— 黑色蒙版 alpha = 1 - 亮度，叠在图片前面。
  // 预览必须跟着变，否则用户拨了亮度、预览里图片纹丝不动，会以为没生效。
  var imgBrightRaw = pvValue('--nts-chat-bg-image-brightness', '');
  var imgBright = 1;
  if (imgBrightRaw !== '') {
    var bNum = RE_NUMBER.test(imgBrightRaw) ? parseFloat(imgBrightRaw) : NaN;
    if (isNaN(bNum) && RE_PERCENT.test(imgBrightRaw)) bNum = parseFloat(imgBrightRaw) / 100;
    if (!isNaN(bNum)) imgBright = Math.max(0, Math.min(1, bNum));
  }
  var imgScrim = hasPanelImage && imgBright < 1
    ? 'linear-gradient(rgba(0,0,0,' + (1 - imgBright).toFixed(4) + '), rgba(0,0,0,'
      + (1 - imgBright).toFixed(4) + ')), '
    : '';
  liveStyle($('pv-body'), {
    background: 'transparent',
    borderRadius: panelRadius || '0px'
  });
  var pvBody = $('pv-body');
  if (pvBody) {
    pvBody.style.backgroundImage = hasPanelImage ? (imgScrim + panelImage) : '';
    pvBody.style.backgroundSize = hasPanelImage ? 'cover' : '';
    pvBody.style.backgroundPosition = hasPanelImage ? 'center center' : '';
    pvBody.style.backgroundRepeat = hasPanelImage ? 'no-repeat' : '';
    // 顶部渐隐（用户说的「图片最上边那层雾」）：与应用同一条 mask 渐变的 alpha 乘法。
    var topFadeRaw = pvValue('--nts-chat-top-fade', '');
    if (topFadeRaw === '') {
      pvBody.style.maskImage = '';
      pvBody.style.webkitMaskImage = '';
    } else {
      var fadeNum = RE_NUMBER.test(topFadeRaw) ? parseFloat(topFadeRaw) : NaN;
      if (isNaN(fadeNum) && RE_PERCENT.test(topFadeRaw)) fadeNum = parseFloat(topFadeRaw) / 100;
      if (isNaN(fadeNum)) fadeNum = 1;
      var fade = Math.max(0, Math.min(1, fadeNum));
      var fadeCss = 'linear-gradient(to bottom, rgba(0,0,0,' + (1 - fade).toFixed(4) + ') 0px, rgba(0,0,0,'
        + (1 - 0.22 * fade).toFixed(4) + ') 12px, rgba(0,0,0,1) 28px)';
      pvBody.style.maskImage = fadeCss;
      pvBody.style.webkitMaskImage = fadeCss;
    }
  }


  /* ---------- 面板光效 / 玻璃拟态（预览侧还原）----------
   * 「未配置 = 不动预览」，与真机的门控语义一致；每个乘子都真算，否则拨动
   * 控件时预览纹丝不动会被当成没生效。 */
  function pvNumber(key) {
    var raw = pvValue(key, '');
    if (raw === '') return null;
    var n = NaN;
    if (RE_NUMBER.test(raw)) n = parseFloat(raw);
    else if (RE_PERCENT.test(raw)) n = parseFloat(raw) / 100;
    if (isNaN(n)) return null;
    return clamp(n, 0, 1);
  }

  function pvLength(key) {
    var raw = pvValue(key, '');
    if (raw === '') return '';
    if (RE_LENGTH.test(raw) || RE_ZERO.test(raw)) return raw;
    if (/^[0-9]{1,4}(?:\.[0-9]+)?$/.test(raw)) return raw + 'px';
    return '';
  }

  /* 把任意 CSS 颜色按比例混到透明上 —— 真机用 color-mix，预览用 rgba 近似。
   * 颜色解析复用 parseColor，范围与校验器一致。 */
  function pvTint(color, amount) {
    var c = parseColor(color || '');
    if (!c || amount <= 0) return 'rgba(0,0,0,0)';
    var a = Math.round(clamp(c.a * amount, 0, 1) * 1000) / 1000;
    return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + a + ')';
  }

  var glowColor = pvValue('--nts-panel-glow-color', '#ffffff');
  /* 与 CSS 同一条门控：外发光要求 strength 有值，其余按需。 */
  /* 与 CSS 的门控完全一致：外发光只要 color / strength / size 任一被配置就生效，
   * 其余取默认（strength 1、size 24px）。早期预览要求 strength 有值，导致用户
   * 只改「光晕颜色」时预览毫无光晕 —— 那正是最容易被误判成「改了没反应」的场景。 */
  var glowStrength = pvNumber('--nts-panel-glow-strength');
  var glowColorRaw = pvValue('--nts-panel-glow-color', '');
  var glowSizeRaw = pvValue('--nts-panel-glow-size', '');
  var glowOn = (glowColorRaw !== '' || glowStrength !== null || glowSizeRaw !== '');
  var glowAmount = glowStrength === null ? 1 : glowStrength;
  var glowSizePx = parseFloat(pvLength('--nts-panel-glow-size')) || 24;
  var borderColorRaw = pvValue('--nts-panel-border-color', '');
  var borderWidth = pvLength('--nts-panel-border-width');
  var cornerGlow = pvNumber('--nts-panel-corner-glow');
  var edgeGradient = pvNumber('--nts-panel-edge-gradient');
  var sheen = pvNumber('--nts-panel-sheen');
  var innerGlow = pvNumber('--nts-panel-inner-glow');
  var glassTintRaw = pvValue('--nts-panel-glass-tint', '');

  if (pvBody) {
    /* background-image 的**第一个**在最上层，所以光效层要 prepend。
     * 底下保持原有的「蒙版 + 背景图」。 */
    var below = hasPanelImage ? (imgScrim + panelImage) : '';
    var above = [];

    if (glassTintRaw) {
      var tint = parseColor(glassTintRaw);
      if (tint) above.push('linear-gradient(' + pvTint(glassTintRaw, 1) + ', '
        + pvTint(glassTintRaw, 1) + ')');
    }
    if (cornerGlow !== null && cornerGlow > 0) {
      ['0% 0%', '100% 0%', '0% 100%', '100% 100%'].forEach(function (pos) {
        above.push('radial-gradient(circle at ' + pos + ', '
          + pvTint(glowColor, cornerGlow * 0.95) + ', rgba(0,0,0,0) 34%)');
      });
    }
    if (sheen !== null && sheen > 0) {
      above.push('linear-gradient(135deg, ' + pvTint('#ffffff', sheen * 0.42)
        + ' 0%, rgba(0,0,0,0) 46%)');
    }
    if (glowOn && glowAmount > 0) {
      /* 外发光：一圈从透明过渡到发光色的径向渐变，厚度随 size×strength。 */
      var inner = Math.max(0, glowSizePx * 1.42 * glowAmount).toFixed(1);
      above.push('radial-gradient(ellipse at center, rgba(0,0,0,0) calc(100% - '
        + inner + 'px), ' + pvTint(glowColor, glowAmount * 0.55) + ' 100%)');
    }

    pvBody.style.backgroundImage = above.length
      ? (above.join(', ') + (below ? ', ' + below : ''))
      : below;
    if (hasPanelImage || above.length) {
      pvBody.style.backgroundRepeat = 'no-repeat';
      pvBody.style.backgroundSize = 'cover';
      pvBody.style.backgroundPosition = 'center center';
    }

    /* 描边：真机在 anchor::after 上，预览直接给 body 加 border。 */
    pvBody.style.border = (borderWidth || borderColorRaw)
      ? (borderWidth || '1.5px') + ' solid ' + (borderColorRaw || 'rgba(255,255,255,.55)')
      : '';

    /* 内侧边缘渐变 + 内阴影：预览里都用 inset 阴影表达。 */
    var glowShadows = [];
    if (edgeGradient !== null && edgeGradient > 0) {
      glowShadows.push('inset 0 0 ' + (glowSizePx * 0.9).toFixed(1) + 'px '
        + (glowSizePx * 0.12).toFixed(1) + 'px ' + pvTint(glowColor, edgeGradient));
    }
    if (innerGlow !== null && innerGlow > 0) {
      glowShadows.push('inset 0 0 ' + (18 * innerGlow).toFixed(1) + 'px '
        + (2 * innerGlow).toFixed(1) + 'px rgba(0,0,0,.35)');
    }
    pvBody.style.boxShadow = glowShadows.join(', ');
  }

  var fontFamily = pvValue('--nts-font-family', '');
  var fontSize = pvValue('--nts-font-size', '');
  preview.style.fontFamily = fontFamily || '';
  if (fontSize) preview.style.fontSize = fontSize;

  liveStyle($('pv-topbar'), {
    background: pvValue('--nts-topbar-bg', 'transparent'),
    color: pvValue('--nts-topbar-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a')
  });

  /* 气泡底色：设了「气泡背景」变量时它接管底色（虚化让位，与 CSS 的
   * yieldsTo 语义一致），但 backdrop 模糊仍随开关缩放。 */
  var userBgRaw = pvValue('--nts-user-bubble-bg', '');
  var aiBgRaw = pvValue('--nts-ai-bubble-bg', '');
  styleBubble($('pv-user'), {
    background: userBgRaw
      ? userBgRaw
      : (veilActive ? veilSheet(veilUserSheet, veil) : '#44b7fe'),
    color: pvValue('--nts-user-bubble-text',
      veilActive ? (state.mode === 'dark' ? '#dceaff' : '#142033') : '#ffffff'),
    borderColor: pvValue('--nts-user-bubble-border', 'transparent'),
    radius: bubbleRadius,
    align: 'flex-end',
    blur: veilActive ? veilBlur : ''
  });

  styleBubble($('pv-ai'), {
    background: aiBgRaw
      ? aiBgRaw
      : (veilActive
        ? veilSheet(veilAiSheet, veil)
        : (state.mode === 'dark' ? 'rgba(42,123,196,0.18)' : '#ffffff')),
    color: pvValue('--nts-ai-bubble-text',
      veilActive ? (state.mode === 'dark' ? '#e8f0fb' : '#273042') : (state.mode === 'dark' ? '#e0e0e0' : '#24445a')),
    borderColor: pvValue('--nts-ai-bubble-border', 'transparent'),
    radius: bubbleRadius,
    align: 'flex-start',
    blur: veilActive ? veilBlur : ''
  });

  $('pv-link').style.color = pvValue('--nts-link', '#2a7bc4');

  liveStyle($('pv-chip'), {
    background: pvValue('--nts-chip-bg', 'transparent'),
    color: pvValue('--nts-chip-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a'),
    borderColor: pvValue('--nts-rec-border', 'rgba(0,0,0,0.12)'),
    borderRadius: '999px'
  });

  liveStyle($('pv-input-area'), {
    background: pvValue('--nts-input-bg', state.mode === 'dark' ? 'rgba(40,40,40,0.65)' : 'rgba(255,255,255,0.5)'),
    borderTopColor: pvValue('--nts-input-border', 'rgba(0,0,0,0.06)'),
    blur: blurCss
  });

  liveStyle($('pv-input'), {
    background: pvValue('--nts-input-bg', 'transparent'),
    color: pvValue('--nts-input-text', state.mode === 'dark' ? '#e0e0e0' : '#24445a'),
    borderColor: pvValue('--nts-input-border', 'rgba(0,0,0,0.1)'),
    borderRadius: radius
  });

  liveStyle($('pv-send'), {
    background: pvValue('--nts-send-bg', '#44b7fe'),
    color: pvValue('--nts-send-text', '#ffffff'),
    borderRadius: radius
  });

  renderCompare();
}

/* ===== BATCH1_BEGIN（v0.10.0：单行复制粘贴 / 对比卡 / AI 提示词）===== */

var rowClipboard = '';

/** 复制这一行「当前显示的值」（含服务器值），供粘贴到别的行或别的模式。 */
function copyRowValue(item) {
  var value = displayValue(item.key);
  if (value === null || value === undefined || String(value) === '') {
    value = serverValue(item.key);
  }
  value = value === null || value === undefined ? '' : String(value);
  if (!value) {
    showToast('这一行现在是空的（原生值），没有可复制的值', 'warn');
    return;
  }
  rowClipboard = value;
  try { copyText(value); } catch (error) { /* 剪贴板不可用时仍有内存副本 */ }
  showToast('已复制「' + item.label + '」的值：' + value, 'ok');
}

/** 把上次复制（或系统剪贴板）的值粘贴到这一行，走正常校验与「已改」标记。 */
async function pasteRowValue(item) {
  var text = '';
  if (rowClipboard) {
    text = rowClipboard;
  } else if (navigator.clipboard && navigator.clipboard.readText) {
    try { text = await navigator.clipboard.readText(); } catch (error) { text = ''; }
  }
  text = String(text || '').trim();
  if (!text) {
    showToast('剪贴板里没有可粘贴的内容（也可以先点某一行的复制按钮）', 'warn');
    return;
  }
  if (!isSafeValue(item.key, text)) {
    showToast('「' + item.label + '」不接受这个值：' + text.slice(0, 40), 'warn');
    return;
  }
  undoPush(item.key, true);
  markEdited(item.key, normalizeTypedValue(item, text));
  syncRow(item.key);
  showToast('已粘贴到「' + item.label + '」', 'ok');
}

/**
 * 对比卡：悬停预演时列出「当前值 → 预演值」里真正不同且看得见的几项。
 *
 * 这里不复制整块预览 DOM（那要把 renderPreview 的近三百行改成可传参的形式，
 * 风险大且没必要）：改成逐变量对比关键面 —— 颜色给色块，长度/模糊给文本。
 */
var COMPARE_KEYS = [
  ['--nts-chat-bg', '聊天背景'],
  ['--nts-chat-panel-bg', '背板底色'],
  ['--nts-user-bubble-bg', '用户气泡'],
  ['--nts-ai-bubble-bg', 'AI 气泡'],
  ['--nts-user-bubble-text', '气泡文字'],
  ['--nts-accent', '主色调'],
  ['--nts-shell-radius', '外壳圆角'],
  ['--nts-chat-blur', '毛玻璃模糊']
];

function compareIsColor(value) {
  var text = String(value || '').trim().toLowerCase();
  if (!text) return false;
  if (text.charAt(0) === '#') return true;
  return text.indexOf('rgb') === 0 || text.indexOf('hsl') === 0;
}

function comparePairs() {
  var out = [];
  COMPARE_KEYS.forEach(function (pair) {
    var key = pair[0];
    var current = displayValue(key);
    var previewed = hoverPreviewValue(key);
    var before = current === null || current === undefined ? '' : String(current);
    var after = previewed === null || previewed === undefined ? '' : String(previewed);
    if (after === '' || after === before) return;
    out.push({ key: key, label: pair[1], before: before, after: after });
  });
  return out;
}

function renderCompare() {
  var card = $('pv-compare');
  if (!card) return;
  var body = $('pv-compare-rows');
  var hovering = !!(hoverPreview && hoverPreview.palette);
  card.hidden = !hovering;
  if (!hovering || !body) return;
  body.innerHTML = '';
  var pairs = comparePairs();
  if (!pairs.length) {
    body.appendChild(el('p', {
      class: 'pv-compare-empty',
      text: '这套主题在这几项上和当前配色一致，只有细节不同 —— 直接看上面的预览即可。'
    }));
    return;
  }
  pairs.forEach(function (pair) {
    var row = el('div', { class: 'pv-compare-row' });
    row.appendChild(el('span', { class: 'pv-compare-label', text: pair.label, title: pair.key }));
    var beforeCell = el('span', { class: 'pv-compare-val' });
    if (compareIsColor(pair.before)) {
      beforeCell.appendChild(el('span', { class: 'pv-chip-sw', style: 'background:' + pair.before }));
    }
    beforeCell.appendChild(el('code', { text: pair.before || '（空）' }));
    var afterCell = el('span', { class: 'pv-compare-val is-after' });
    if (compareIsColor(pair.after)) {
      afterCell.appendChild(el('span', { class: 'pv-chip-sw', style: 'background:' + pair.after }));
    }
    afterCell.appendChild(el('code', { text: pair.after }));
    row.appendChild(beforeCell);
    row.appendChild(el('span', { class: 'pv-compare-arrow', text: '→', 'aria-hidden': 'true' }));
    row.appendChild(afterCell);
    body.appendChild(row);
  });
}

/** 把当前配色整理成一段可以直接抛给 AI 的提示词（含变量契约与硬约束）。 */
function buildAiPrompt() {
  // 面板自测约定：源码里不许出现反引号（防止误用模板字符串），围栏用字符拼出来。
  var FENCE = String.fromCharCode(96, 96, 96);
  var name = ($('theme-name') && $('theme-name').value.trim()) || state.metaName || DEFAULT_THEME_NAME;
  var light = effectiveVars('light');
  var dark = effectiveVars('dark');
  var changes = pendingChangeList();
  var lines = [];
  lines.push('# N.E.K.O 主题：' + name);
  lines.push('');
  lines.push('你是一个配色设计师。下面是我当前用的主题（N.E.K.O 聊天窗口的 CSS 变量），');
  lines.push('请在此基础上做改进，并把**完整**结果按同样的两段结构输出，方便我直接粘贴回面板。');
  lines.push('');
  lines.push('硬约束：');
  lines.push('1. 只能使用下面出现过的变量名，不要新增不存在的变量，也不要删除它们；');
  lines.push('2. 颜色用 #rrggbb / rgba() / transparent；长度带 px；模糊写成 blur(18px) 这种形式；');
  lines.push('3. 亮色组写在 vars 里，暗色组写在 dark_vars 里，两组都要给全；');
  lines.push('4. 正文与背景对比度至少 4.5:1，次要文字至少 3:1（这套面板有对比度自检）；');
  lines.push('5. 只输出一个 ' + FENCE + 'json 代码块，内容为 {"name": "...", "vars": {...}, "dark_vars": {...}}。');
  lines.push('');
  if (changes.length) {
    lines.push('我这次改过的 ' + changes.length + ' 项（可以重点看这些）：');
    changes.slice(0, 12).forEach(function (entry) {
      lines.push('- ' + (CHANGE_MODE_LABELS[entry.mode] || entry.mode) + ' ' +
        changeKeyLabel(entry.key) + '：' + (entry.before || '（空）') + ' → ' + (entry.after || '（空）'));
    });
    lines.push('');
  }
  lines.push('当前配色：');
  lines.push(FENCE + 'json');
  lines.push(JSON.stringify({ name: name, vars: light, dark_vars: dark }, null, 2));
  lines.push(FENCE);
  return lines.join('\n');
}

function copyAiPrompt() {
  var text = buildAiPrompt();
  openModal(
    'export',
    '给 AI 的提示词 · ' + (($('theme-name') && $('theme-name').value.trim()) || DEFAULT_THEME_NAME),
    '复制后贴给任意 AI（ChatGPT / Claude / 本地模型都行）。它按变量契约改完，你把返回的 JSON 贴回「导入」即可。',
    text
  );
}

/* ===== BATCH1_END ===== */

function liveStyle(node, cfg) {
  if (!node) return;
  if (cfg.background !== undefined) node.style.backgroundColor = cfg.background;
  if (cfg.color !== undefined) node.style.color = cfg.color;
  if (cfg.borderColor !== undefined) node.style.borderColor = cfg.borderColor;
  if (cfg.borderTopColor !== undefined) node.style.borderTopColor = cfg.borderTopColor;
  if (cfg.borderRadius !== undefined) node.style.borderRadius = cfg.borderRadius;
  // 用 !== undefined 而不是真值判断：清空 --nts-chat-blur 时要能把上一帧的
  // backdrop-filter 撤掉，否则预览里毛玻璃永远留着，看着像清空没生效。
  if (cfg.blur !== undefined) {
    node.style.backdropFilter = cfg.blur || '';
    node.style.webkitBackdropFilter = cfg.blur || '';
  }
}

function styleBubble(node, cfg) {
  if (!node) return;
  node.style.backgroundColor = cfg.background;
  node.style.color = cfg.color;
  node.style.borderColor = cfg.borderColor;
  node.style.borderStyle = 'solid';
  node.style.borderWidth = '1px';
  node.style.borderRadius = cfg.radius || '12px';
  node.style.alignSelf = cfg.align;
  // 气泡的 backdrop 模糊：聊天背景虚化开关（--nts-bubble-veil）按 0–1 乘子
  // 缩放半径。必须无条件写（哪怕空串），否则关掉开关后上一帧的模糊会留在
  // 预览里，看着像开关没生效。
  if (cfg.blur !== undefined) {
    node.style.backdropFilter = cfg.blur || '';
    node.style.webkitBackdropFilter = cfg.blur || '';
  }
}

/* 把 RGBA 数组按 0–1 乘子缩放 alpha（预览里的"白纱"）。 */
function veilSheet(rgba, veil) {
  return toRgba(rgba[0], rgba[1], rgba[2], rgba[3] * veil);
}

/* 把一个 CSS 颜色按 0–1 乘子缩放 alpha；解析不了就原样返回。 */
function veilTint(color, veil) {
  var c = parseColor(color);
  if (!c) return color;
  var a = (c.a === undefined ? 1 : c.a) * veil;
  return toRgba(c.r, c.g, c.b, a);
}

/* ============================================================================
 * 9. 调色板 dump / 元信息
 * ==========================================================================*/

/* ===== DUMP_LAZY_BEGIN ===== */
// 当前调色板 JSON 卡（v0.4.21）：折叠时不再重建 JSON。
//
// 一份调色板是 2 × 95 行字符串，而 renderDump() 挂在每一次全局刷新上
// （编辑一行、切模式、重新读取、外部改动跟随都会触发）。折叠状态下用户根本
// 看不到这段文本，每次还去 JSON.stringify 一遍纯属浪费，所以折叠时只记「脏」，
// 展开时补一次渲染。
var DUMP_COLLAPSE_KEY = 'nts.dump.collapsed';
var dumpState = { collapsed: false, dirty: false, renders: 0, skipped: 0 };

/**
 * 折叠状态下是否跳过重建。抽成纯函数是为了能在 Node 里直接跑真值表
 * （见 test/dump-selftest.js），而不是靠读源码猜。
 * @param {boolean} collapsed
 * @param {boolean} dirty
 * @returns {'skip'|'render'}
 */
function dumpLazyPlan(collapsed, dirty) {
  if (!collapsed) return 'render';
  return 'skip';
}

function loadDumpCollapsed() {
  try {
    var store = libraryStorage();
    if (!store) return false;
    return store.getItem(DUMP_COLLAPSE_KEY) === '1';
  } catch (error) {
    return false;
  }
}

function saveDumpCollapsed() {
  try {
    var store = libraryStorage();
    if (store) store.setItem(DUMP_COLLAPSE_KEY, dumpState.collapsed ? '1' : '0');
  } catch (error) { /* 隐私模式：不记住就行 */ }
}

/** 把 dumpState.collapsed 反映到 DOM（按钮箭头、aria-expanded、body hidden）。 */
function applyDumpCollapsed() {
  var btn = $('btn-toggle-dump');
  var body = $('dump-body');
  if (btn) {
    btn.textContent = dumpState.collapsed ? '▸' : '▾';
    btn.setAttribute('aria-expanded', dumpState.collapsed ? 'false' : 'true');
    btn.setAttribute('title', dumpState.collapsed ? '展开 JSON' : '折叠 JSON');
  }
  if (body) body.hidden = dumpState.collapsed;
}

/** 折叠 / 展开；展开时若是脏的就补渲染一次。 */
function toggleDumpCard() {
  dumpState.collapsed = !dumpState.collapsed;
  saveDumpCollapsed();
  applyDumpCollapsed();
  if (!dumpState.collapsed && dumpState.dirty) renderDump(true);
}
/* ===== DUMP_LAZY_END ===== */

function renderDump(force) {
  // 折叠时只标脏（见上方 DUMP_LAZY 说明）；展开 / 复制 / 强制调用才真的重建。
  if (!force && dumpLazyPlan(dumpState.collapsed, dumpState.dirty) === 'skip') {
    dumpState.dirty = true;
    dumpState.skipped += 1;
    return;
  }
  dumpState.dirty = false;
  dumpState.renders += 1;
  var payload = {
    name: $('theme-name') ? $('theme-name').value : undefined,
    enabled: $('theme-enabled').checked,
    vars: effectiveVars('light'),
    dark_vars: effectiveVars('dark')
  };
  var pending = Object.keys(state.edited.light).length + Object.keys(state.edited.dark).length +
    Object.keys(state.cleared.light).length + Object.keys(state.cleared.dark).length;
  // 这里必须是纯 JSON：旧实现把 "// ↑ 含 N 项未应用的改动" 拼在 JSON 末尾，
  // 与右侧 dump 说明（effectiveVars，含未应用改动）不一致，复制出去也不是合法
  // JSON。未应用提示改放 #dump-foot —— 它在 #dump 之外，复制永远拿到合法 JSON。
  $('dump').textContent = JSON.stringify(payload, null, 2);

  var foot = $('dump-foot');
  if (foot) {
    var saved = paletteCounts(state.palette);
    foot.textContent = '已保存：亮色 ' + saved.light + ' 项 / 暗色 ' + saved.dark + ' 项' +
      (pending
        ? '；另有 ' + pending + ' 项改动尚未应用 —— 上面的 JSON 已经按改动后的样子生成（复制出去就是改完的效果），点「应用」才会写进服务器。'
        : '；没有未应用的改动。');
  }
}

/** 某模式「生效中」的变量：服务器已保存的值叠加本地未应用的改动。
 *
 * 注意不能直接用 collect(mode)：那只包含本次会话里改过/清空过的键，读回一个
 * 已保存的调色板时会渲染成空对象 —— 面板会声称「当前调色板 vars: {}」，
 * 与磁盘上真实的 theme.json 不符，极为误导。
 */
function effectiveVars(mode) {
  var out = {};
  var saved = state.palette
    ? (mode === 'dark' ? state.palette.dark_vars : state.palette.vars)
    : null;
  if (saved) Object.keys(saved).forEach(function (k) { out[k] = saved[k]; });
  Object.keys(state.edited[mode]).forEach(function (k) { out[k] = state.edited[mode][k]; });
  Object.keys(state.cleared[mode]).forEach(function (k) { delete out[k]; });
  return out;
}

function collect(mode) {
  var out = {};
  var edited = state.edited[mode];
  var cleared = state.cleared[mode];
  Object.keys(edited).forEach(function (k) { out[k] = edited[k]; });
  Object.keys(cleared).forEach(function (k) { out[k] = ''; });
  return out;
}

function renderMeta() {
  var p = state.palette;
  if (!p) return;
  // 用户已经改了名称/启用但还没应用时，不能拿服务端的值把输入框冲掉：旧实现
  // 每次回读都无条件写 #theme-enabled，于是「拐一下开关」会被静默复位成服务端的值，
  // 用户再点应用时还以为自己改过。
  var keepEdits = metaDirtyCount() > 0;
  // 基线必须在每次读回调色板时刷新：applyTheme 靠它判断名称/启用是否真的变了。
  state.metaName = p.name || '';
  state.metaEnabled = p.enabled !== false;
  // theme.json 不再有 version 字段（0.1.5 起）：面板也不显示版本计数，
  // 只留主题名与「最后更新时间」两枚 pill。
  $('meta-name').textContent = p.name || '默认';
  var when = p.updated_at ? String(p.updated_at).replace('T', ' ').slice(0, 19) : '—';
  $('meta-updated').textContent = when;
  if (!keepEdits && document.getElementById('theme-name') && document.activeElement !== $('theme-name')) {
    $('theme-name').value = p.name || '';
  }
  if (!keepEdits) $('theme-enabled').checked = p.enabled !== false;
}

/* ===== CHANGES_BEGIN（v0.5.1：待应用改动清单）===== */

var CHANGE_MODE_LABELS = { light: '亮色', dark: '暗色' };

/**
 * 把「服务器上的值 + 本地改动」摊平成一行一条改动（纯函数）。
 *
 * @param {{palette?: Object, edited?: Object, cleared?: Object}} snapshot
 * @returns {Array<{key: string, mode: string, before: string, after: string, kind: string}>}
 *   kind: changed（改值）/ added（原来没有）/ removed（清空）
 */
function changeEntryList(snapshot) {
  var snap = snapshot || {};
  var palette = snap.palette || {};
  var edited = snap.edited || {};
  var cleared = snap.cleared || {};
  var out = [];
  [['light', palette.vars], ['dark', palette.dark_vars]].forEach(function (pair) {
    var mode = pair[0];
    var saved = pair[1] && typeof pair[1] === 'object' ? pair[1] : {};
    var ed = edited[mode] && typeof edited[mode] === 'object' ? edited[mode] : {};
    var cl = cleared[mode] && typeof cleared[mode] === 'object' ? cleared[mode] : {};
    var keys = {};
    Object.keys(ed).forEach(function (k) { keys[k] = 1; });
    Object.keys(cl).forEach(function (k) { keys[k] = 1; });
    Object.keys(keys).sort().forEach(function (key) {
      var before = Object.prototype.hasOwnProperty.call(saved, key) ? String(saved[key]) : '';
      var after = '';
      var kind = 'changed';
      if (Object.prototype.hasOwnProperty.call(cl, key)) {
        after = '';
        kind = before ? 'removed' : 'changed';
      } else {
        after = Object.prototype.hasOwnProperty.call(ed, key) ? String(ed[key]) : '';
        kind = before ? 'changed' : 'added';
      }
      if (after === before) return;
      out.push({ key: key, mode: mode, before: before, after: after, kind: kind });
    });
  });
  return out;
}

function pendingChangeList() {
  return changeEntryList({
    palette: state.palette,
    edited: state.edited,
    cleared: state.cleared
  });
}

function changeValueText(value) {
  return String(value) === '' ? '（空）' : String(value);
}

function changeKeyLabel(key) {
  var item = CATALOG_BY_KEY[key];
  return (item && item.label) || key;
}

/** 撤掉一条改动：删掉本地编辑/清空标记，再把这一行同步回服务器值。 */
function revertChange(entry) {
  if (!entry) return;
  var edited = state.edited[entry.mode] || {};
  var cleared = state.cleared[entry.mode] || {};
  delete edited[entry.key];
  delete cleared[entry.key];
  state.edited[entry.mode] = edited;
  state.cleared[entry.mode] = cleared;
  syncRow(entry.key);
  renderChanges();
  showToast('已撤销「' + changeKeyLabel(entry.key) + '」的改动', 'ok');
}

function revertAllChanges() {
  var entries = pendingChangeList();
  if (!entries.length) return;
  if (!window.confirm('要撤销全部 ' + entries.length + ' 项未应用的改动吗？撤销后无法恢复。')) return;
  state.edited = { light: {}, dark: {} };
  state.cleared = { light: {}, dark: {} };
  markAllRowsValid();
  clearRgbMemory();
  syncAll();
  renderChanges();
  showNotice('warn', '已撤销 ' + entries.length + ' 项未应用的改动（服务器上的配色没动）。');
  showToast('已撤销全部改动', 'ok');
}

/** 重画清单。没有改动时整张卡隐藏，不占版面。 */
function renderChanges() {
  var card = $('changes-card');
  if (!card) return;
  var list = $('changes-list');
  var countNode = $('changes-count');
  var entries = pendingChangeList();
  if (countNode) countNode.textContent = entries.length ? String(entries.length) : '';
  card.hidden = !entries.length;
  if (!entries.length) {
    if (list) list.innerHTML = '';
    return;
  }
  if (!list) return;
  list.innerHTML = '';
  entries.forEach(function (entry) {
    var row = el('li', { class: 'change-row' });
    row.appendChild(el('span', {
      class: 'change-mode is-' + entry.mode,
      text: CHANGE_MODE_LABELS[entry.mode] || entry.mode
    }));
    row.appendChild(el('span', {
      class: 'change-label',
      text: changeKeyLabel(entry.key),
      title: entry.key
    }));
    row.appendChild(el('span', { class: 'change-delta' }, [
      el('code', { class: 'change-before', text: changeValueText(entry.before) }),
      el('span', { class: 'change-arrow', text: '→', 'aria-hidden': 'true' }),
      el('code', { class: 'change-after is-' + entry.kind, text: changeValueText(entry.after) })
    ]));
    var undo = el('button', {
      class: 'btn btn-mini change-undo',
      type: 'button',
      text: '撤销',
      'aria-label': '撤销「' + changeKeyLabel(entry.key) + '」的改动'
    });
    undo.addEventListener('click', function () { revertChange(entry); });
    row.appendChild(undo);
    list.appendChild(row);
  });
}

/* ===== CHANGES_END ===== */

function updateButtons() {
  var n = pendingChangeCount();
  var applyBtn = $('btn-apply');
  applyBtn.textContent = n ? '应用（' + n + ' 项改动）' : '应用';
  applyBtn.disabled = state.busy;
  applyBtn.dataset.busy = state.busy ? 'true' : 'false';
  ['btn-reset', 'btn-export', 'btn-import', 'btn-reload', 'btn-revert-mode'].forEach(function (id) {
    var node = $(id);
    if (node) node.disabled = state.busy;
  });
  // 待应用改动清单（v0.5.1）：跟着按钮文案一起刷新，卡片自带隐藏逻辑。
  renderChanges();
  updateUndoButtons();
  renderQuickPresets();
}

/* ============================================================================
 * 10. 动作
 * ==========================================================================*/

function buildArgs(mode) {
  return collect(mode);
}

/** 调色板里两组各有多少项（0 是常见且值得说出来的状态）。 */
function paletteCounts(p) {
  var l = p && p.vars && typeof p.vars === 'object' ? Object.keys(p.vars).length : 0;
  var d = p && p.dark_vars && typeof p.dark_vars === 'object' ? Object.keys(p.dark_vars).length : 0;
  return { light: l, dark: d };
}

/**
 * 只改了一组时的提醒。亮/暗两组是各管各的（THEME_CONTRACT §3.0），
 * 所以「只配了亮色、应用却在暗色模式」= 零效果 —— 这是最容易误判成
 * 「插件坏了」的用法，必须在提示里点出来。
 */
function modeHint(nLight, nDark) {
  if (nLight && !nDark) {
    return '\n注意：本次只改了「亮色」组 —— 应用若正处在暗色模式，外观不会变（两组各管各的）。';
  }
  if (nDark && !nLight) {
    return '\n注意：本次只改了「暗色」组 —— 应用若正处在亮色模式，外观不会变（两组各管各的）。';
  }
  return '';
}

/** 从磁盘读回调色板时的中性文案：不是「本次只改了」，而是「当前只有」。
 * 旧实现直接用 modeHint，刷新/回读时会误报「本次只改了亮色组」。 */
function savedModeHint(nLight, nDark) {
  if (nLight && !nDark) {
    return '\n当前只有「亮色」组有自定义项（暗色模式仍是原生外观）。';
  }
  if (nDark && !nLight) {
    return '\n当前只有「暗色」组有自定义项（亮色模式仍是原生外观）。';
  }
  return '';
}

/**
 * 编辑器底部那行常驻说明：它要回答「为什么我什么都没看到」这个最常见的问题，
 * 所以把「空 = 原生」和「亮暗两组各管各的」直接写在这里，而不是藏在 README。
 */
function renderPaletteHint() {
  var node = $('empty-hint');
  if (!node) return;
  if (!state.palette) {
    node.textContent = '尚未读取到调色板；面板仍可编辑，点「应用」会重试读取。';
    return;
  }
  var c = paletteCounts(state.palette);
  if (!c.light && !c.dark) {
    node.textContent = '当前调色板为空：所有组件都使用 N.E.K.O 原生外观。任意一行填值并应用，即刻生效。';
    return;
  }
  node.textContent = '已保存 ' + (c.light + c.dark) + ' 项（亮色 ' + c.light + ' / 暗色 ' + c.dark +
    '）。留空的行保持原生外观；两组各管各的，只配一组时另一模式不会变。';
}

/** 从后端 rejected 文案里抠出被拒的变量名（后端文案带 '--nts-xxx'）。 */
function rejectedKeysOf(rejected) {
  var keys = {};
  (rejected || []).forEach(function (msg) {
    var found = String(msg).match(/--nts-[a-z0-9-]+/g);
    if (!found) return;
    found.forEach(function (k) { keys[k] = true; });
  });
  return keys;
}

/**
 * 把被拒绝的提交值放回编辑态。后端只拒绝单条，合法部分已落盘，所以这里
 * 不能清空整组改动，否则用户只能凭记忆重新输入被拒的那个值。
 * @returns {number} 恢复的条目数
 */
function restoreRejected(rejected, sentLight, sentDark) {
  var keys = rejectedKeysOf(rejected);
  var restored = 0;
  function restoreBag(sent, edited, cleared) {
    Object.keys(sent).forEach(function (k) {
      if (!keys[k]) return;
      if (sent[k] === '') cleared[k] = true;
      else edited[k] = sent[k];
      restored += 1;
    });
  }
  restoreBag(sentLight, state.edited.light, state.cleared.light);
  restoreBag(sentDark, state.edited.dark, state.cleared.dark);
  if (restored) { syncAll(); applyFilters(); }
  return restored;
}

/** 材质中心当前编辑值的快照（不论是否有改动）；没挂载时返回 null。 */
function materialsSnapshot() {
  var api = window.NekoMaterials;
  if (!api || typeof api.snapshot !== 'function') return null;
  try { return api.snapshot() || null; } catch (error) { return null; }
}

/** 材质中心的当前编辑值；没改动/没挂载时返回 null（后端据此保持磁盘材质不变）。 */
function currentMaterialsPayload() {
  var api = window.NekoMaterials;
  if (!api || typeof api.snapshot !== 'function' || typeof api.isDirty !== 'function') return null;
  try {
    return api.isDirty() ? (api.snapshot() || null) : null;
  } catch (error) {
    return null;
  }
}

/** 把磁盘上的 palette 推给材质中心，让它的「已保存」基线跟上磁盘。 */
function syncMaterials(palette, options) {
  var api = window.NekoMaterials;
  if (!api || typeof api.sync !== 'function') return;
  try {
    api.sync(palette || state.palette, options);
  } catch (error) {
    // 材质中心渲染出错不能拖垮主面板：它只是面板的一部分。
  }
}

/** 重置/回读失败时把材质编辑态拉回基线。 */
function resetMaterialsLocal() {
  var api = window.NekoMaterials;
  if (!api || typeof api.resetLocal !== 'function') return;
  try {
    api.resetLocal();
  } catch (error) {
    // 同上：不影响主流程。
  }
}

async function applyTheme() {
  if (state.busy) return;

  // 红框行必须先解决：非法输入时 edited 里留的是「上一个合法值」，直接应用会
  // 把它提交上去并显示成功，用户会以为刚敲的非法值生效了。这里明确阻止。
  if (invalidRowCount()) {
    var nBad = invalidRowCount();
    showNotice('warn', '有 ' + nBad + ' 行输入不合法（红框），已阻止应用。\n' +
      '请把红框里的值改成合法值，或点该行「清空」。');
    showToast('有非法输入，未应用', 'error');
    setStatus('有非法输入，未应用', 'warn');
    return;
  }

  var lightVars = buildArgs('light');
  var darkVars = buildArgs('dark');
  var nLight = Object.keys(lightVars).length;
  var nDark = Object.keys(darkVars).length;
  var enabled = $('theme-enabled').checked;
  // 材质子树是整份替换语义：只有面板真改过才带上，没碰过就不传，
  // 否则「只调颜色」的提交会把磁盘上的材质覆盖成面板里的旧值。
  var materials = currentMaterialsPayload();
  var materialsDirty = !!materials;
  // 用 renderMeta 写入的「已保存基线」比较，而不是 !!state.palette：
  // 首次 get_theme 失败（palette=null）时旧实现一律判为没改，只改名称或启用
  // 开关永远提示「没有可应用的改动」，改动根本提交不出去。
  var enabledChanged = state.metaEnabled !== enabled;
  var nameInput = $('theme-name');
  var name = nameInput ? nameInput.value.trim() : '';
  // 只改主题名也是一次有效改动：漏掉它会让 name 永远存不进去（实测点了应用毫无反应）。
  var nameChanged = name !== (state.metaName || '');

  // 没有任何改动时不要假装成功：否则「点了应用但什么都没发生」会被当成插件故障。
  // 提示语写成诊断信息：编辑是逐键实时记录（oninput → markEdited），不需要失焦。
  if (!nLight && !nDark && !enabledChanged && !nameChanged && !materialsDirty) {
    showNotice('warn', '没有可应用的改动（当前 0 项变量改动，名称/启用未变，也没有红框非法输入）。\n' +
      '改值会在输入时立即记入面板，不必等控件失焦；如果确实改过，可能是刚才用「还原本组」撤销了。');
    showToast('没有改动可应用');
    setStatus('没有改动可应用', 'warn');
    return;
  }

  var args = {
    vars: lightVars,
    dark_vars: darkVars,
    enabled: enabled
  };
  if (materialsDirty) args.materials = materials;
  // 名称清空按默认名提交：与后端「空串/纯空白 = 恢复默认名」一致，
  // DEFAULT_THEME_NAME 与后端常量、placeholder 一致。
  if (nameChanged) args.name = name || DEFAULT_THEME_NAME;

  setBusy(true, '正在应用…');
  // 提交时刻的材质快照：保存 / 回读期间材质控件仍可编辑，之后的改动要留在面板里。
  var materialsAtSubmit = materialsSnapshot();
  try {
    var result = await callPlugin('apply_theme', args);
    var rejected = result && result.rejected;
    // 写入成功后必须回读，且要检查返回值：silent 失败被吞时面板还停在旧值，
    // 文案却显示「已应用」—— 用户看到的和磁盘不一致。
    var reloaded = await reloadPalette({ silent: true, materialsSubmitted: materialsAtSubmit });
    if (reloaded) {
      // 只有回读成功才认为「面板 = 磁盘」，此时才清脏标记与非法态。
      state.edited = { light: {}, dark: {} };
      state.cleared = { light: {}, dark: {} };
      markAllRowsValid();
      // reloadPalette 内部的 syncAll() 发生在清脏之前：行上的黄底「已改」、
      // 分组头的「已改 N 项」、dump 脚注都还停在旧状态。不在这里再刷一遍，
      // 用户会以为没保存成功，再点「应用」还被拦「没有可应用的改动」。
      syncAll();
      // 应用成功 = 服务端已经接受了这批改动，撤销栈再留着只会把「已写入的值」
      // 又撤成「没写过」的错觉；这里清空，撤销只在「未应用」阶段有意义。
      undoState.past = [];
      undoState.future = [];
      updateUndoButtons();
    } else {
      // 回读失败：脏标记必须保留（不能先清后读）。旧实现先清空 edited/cleared 再
      // 回读，回读一失败面板就显示成「已保存」，而屏幕上可能还是旧值 —— 用户会
      // 以为改动丢了。保留脏标记 = 面板诚实显示「未同步」，点「重新读取」才是
      // 恢复一致的路径。同时用提交值更新名称/启用基线，避免下次误判有改动。
      if (nameChanged) state.metaName = name || DEFAULT_THEME_NAME;
      state.metaEnabled = enabled;
    }
    // 被拒条目留在面板上，让用户就地修正（合法部分后端已落盘）
    var restored = rejected && rejected.length ? restoreRejected(rejected, lightVars, darkVars) : 0;
    // 只把真正发生的变化写进文案，避免「0 项」和「主题名已改」被漏报。
    var parts = [];
    if (nLight) parts.push('亮色 ' + nLight + ' 项');
    if (nDark) parts.push('暗色 ' + nDark + ' 项');
    if (enabledChanged) parts.push(enabled ? '已启用主题' : '已停用主题');
    if (nameChanged) parts.push('主题名「' + (name || DEFAULT_THEME_NAME) + '」');
    if (materialsDirty) parts.push('高级材质');
    var counts = parts.length ? parts.join(' / ') : '无变量改动';
    var readbackNote = reloaded ? '' :
      '\n已写入，但回读失败，面板仍标记为「未同步」（改动标记已保留），请点「重新读取」。';
    if (rejected && rejected.length) {
      showNotice('warn', '已应用（' + counts + '），但后端拒绝了 ' + rejected.length +
        ' 条，已把它们的原值放回面板便于就地修正：\n' + rejected.join('\n') + readbackNote);
      showToast('已应用（' + rejected.length + ' 条被拒绝' + (restored ? '，已回填' : '') + '）');
    } else {
      showNotice(reloaded ? 'ok' : 'warn', '已应用并写入 theme.json：' + counts +
        modeHint(nLight, nDark) + readbackNote);
      showToast('已应用 ' + counts, 'ok');
    }
    setStatus('已应用 ' + counts + (restored ? '（' + restored + ' 条待修正）' : '') +
      (reloaded ? '' : '（回读失败）'),
      reloaded ? 'ok' : 'warn');
    verifyAppliedShortly();
  } catch (error) {
    showNotice('error', '应用失败：' + pluginErrorMessage(error));
    showToast('应用失败', 'error');
    setStatus('应用失败', 'error');
  } finally {
    setBusy(false);
  }
}

async function resetTheme() {
  if (state.busy) return;
  if (!window.confirm('确定恢复内置默认主题？当前所有自定义颜色都会被清除。')) return;
  setBusy(true, '正在重置…');
  try {
    var result = await callPlugin('reset_theme', {});
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    var reloaded = await reloadPalette({ silent: true });
    if (reloaded) {
      showNotice('ok', '已恢复默认主题。');
    } else {
      // 重置已落盘，但面板没能回读：提示重新读取，而不是假装一切最新。
      // 材质同理 —— 后端已把材质恢复成默认（enabled=false），编辑态不能还停在旧值。
      resetMaterialsLocal();
      showNotice('warn', '已恢复默认主题。\n回读失败，面板显示可能不是最新值，请点「重新读取」。');
    }
    showToast('已重置', 'ok');
    setStatus('已重置' + (reloaded ? '' : '（回读失败）'), reloaded ? 'ok' : 'warn');
  } catch (error) {
    showNotice('error', '重置失败：' + pluginErrorMessage(error));
    showToast('重置失败', 'error');
    setStatus('重置失败', 'error');
  } finally {
    setBusy(false);
  }
}

/**
 * 让后端打包文件内容，再由面板触发浏览器下载。
 * 内置之后面板是普通顶层页面（不再是没有 allow-downloads 的 sandbox iframe），
 * 后端不再往桌面 / 下载目录写文件。
 */
async function saveThemeFile(args) {
  var result = await callPlugin('export_to_file', args);
  var data = result && result.data ? String(result.data) : '';
  if (!data || !result.filename) throw new Error('后端没有返回文件内容');
  downloadBlob(base64ToBytes(data), result.filename, result.mime || 'application/octet-stream');
  return result;
}

function savedHint(result) {
  var kb = Math.round((result.bytes || 0) / 1024);
  return '已下载：' + result.filename +
    '\n（' + kb + ' KB' + (result.images ? '，含 ' + result.images + ' 张背景图' : '') + '）';
}

async function exportTheme() {
  if (state.busy) return;
  // 后端 export_theme 返回的是磁盘上已保存的 theme.json，不含面板里未应用的改动。
  // 与右侧 dump（effectiveVars，含未应用改动）不一致，所以先明确告知再导出。
  var pending = dirtyCount();
  if (pending && !window.confirm('有 ' + pending + ' 项未应用的改动。\n' +
    '导出的 JSON 来自磁盘上已保存的 theme.json（不含这些未应用改动）。继续导出？')) {
    return;
  }
  setBusy(true, '正在导出…');
  try {
    var result = await callPlugin('export_theme', {});
    var hint = pending
      ? '下面是已保存到 theme.json 的调色板 JSON（不含 ' + pending + ' 项未应用改动），可复制保存或分享。'
      : '下面是当前调色板的 JSON 文本，可复制保存或分享。';
    // 同时下载一份 JSON 文件。
    try {
      var savedJson = await saveThemeFile({ kind: 'json' });
      hint = savedHint(savedJson) + '\n\n' + hint;
      setStatus(pending ? '已导出并下载（不含未应用改动）' : '已导出并下载', 'ok');
    } catch (saveError) {
      hint = '下载文件失败（' + pluginErrorMessage(saveError) + '），下面是 JSON 文本，可手动复制。\n\n' + hint;
      setStatus('已导出（未写出文件）', 'ok');
    }
    openModal('export', '导出主题', hint, result.payload || '');
  } catch (error) {
    showNotice('error', '导出失败：' + pluginErrorMessage(error));
    showToast('导出失败', 'error');
    setStatus('导出失败', 'error');
  } finally {
    setBusy(false);
  }
}

// 与 plugin/__init__.py 的 IMPORT_MAX_CHARS 保持一致。宿主的 input_schema 会在
// entry 被调用之前就按 maxLength 拒绝超长输入，并把错误包成 INTERNAL，用户看到的
// 是一条看不懂的内部报错 —— 所以这里先拦一次，给出人话提示。
var IMPORT_MAX_CHARS = 262144;

function openImport() {
  openModal('import', '导入主题', '粘贴调色板 JSON 文本。导入为整体替换；非法条目会被后端丢弃。', '');
}

async function doImport() {
  var text = $('modal-text').value.trim();
  if (!text) { showToast('请先粘贴内容'); return; }
  if (text.length > IMPORT_MAX_CHARS) {
    var tooLong = '导入内容过长（' + text.length + ' 字符，上限 ' + IMPORT_MAX_CHARS +
      ' 字符）。请删掉不必要的内容后重试。';
    showNotice('error', tooLong);
    showToast('导入失败', 'error');
    setStatus('导入失败：内容过长', 'error');
    openModal('import', '导入主题', tooLong + '\n粘贴内容已保留，可修改后重试。', text);
    return;
  }
  closeModal();
  setBusy(true, '正在导入…');
  try {
    var result = await callPlugin('import_theme', { payload: text });
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    var reloaded = await reloadPalette({ silent: true });
    var readbackNote = reloaded ? '' :
      '\n已写入，但回读失败，面板显示可能不是最新值，请点「重新读取」。';
    var rejected = result && result.rejected;
    if (rejected && rejected.length) {
      showNotice('warn', '已导入，但后端拒绝了 ' + rejected.length + ' 条：\n' + rejected.join('\n') + readbackNote);
      showToast('已导入（部分条目被拒绝）', 'ok');
      setStatus('已导入（' + rejected.length + ' 条被拒绝' + (reloaded ? '' : '，回读失败') + '）', 'warn');
    } else {
      showNotice(reloaded ? 'ok' : 'warn', '已导入并写入 theme.json。' + readbackNote);
      showToast('已导入', 'ok');
      setStatus('已导入' + (reloaded ? '' : '（回读失败）'), reloaded ? 'ok' : 'warn');
    }
  } catch (error) {
    showNotice('error', '导入失败：' + pluginErrorMessage(error));
    showToast('导入失败', 'error');
    setStatus('导入失败', 'error');
    // 失败时调色板没变，把用户粘贴的文本放回弹窗，避免「内容被吞、只能重新粘贴」。
    openModal('import', '导入主题',
      '导入失败：' + pluginErrorMessage(error) + '\n粘贴内容已保留，可修改后重试。', text);
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 10b. 主题仓库（保存 / 切换 / 分享 zip / 导入 zip）
 * ==========================================================================*/

/** 3 秒内二次点击才真正删除的行内确认状态（不用 window.confirm：
 *  无头/自动化环境下 confirm 会阻塞渲染进程，整个面板假死）。 */
var pendingDelete = { id: '', at: 0 };
var LIB_CONFIRM_MS = 3000;

/** base64 → Uint8Array。atob 对非法字符抛异常，调用方自己兜。 */
function base64ToBytes(base64) {
  var bin = atob(base64);
  var out = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i) & 0xff;
  return out;
}

/** 触发一次浏览器下载（Blob + 临时 <a download>）。 */
function downloadBlob(bytes, filename, mime) {
  var blob = new Blob([bytes], { type: mime || 'application/octet-stream' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  // 立刻 revoke 会让部分 Chromium 版本取消下载，延后一拍。
  setTimeout(function () {
    try { document.body.removeChild(a); } catch (e) { /* 已被移除 */ }
    URL.revokeObjectURL(url);
  }, 1000);
}

/* ===== LIB_CORE_BEGIN —— 主题库纯函数区 =====
 * 只做数据变换，不碰 DOM、不读全局 state：这样 _work/panel_selftest.js 可以把
 * 这一段原样抠出来直接断言（族/tag/搜索三重叠加、收藏置顶、折叠状态读写）。
 * 必须保持 ES5 语法（无箭头函数 / 模板串 / let-const）。 */
var LIB_FAMILIES = [
  { id: 'glassy', label: '液态玻璃', tone: '#7fb8ff' },
  { id: 'gradient', label: '渐变', tone: '#b48bff' },
  { id: 'dark', label: '暗色', tone: '#6b7585' },
  { id: 'light', label: '浅色', tone: '#d9b45a' },
  { id: 'texture', label: '纸感', tone: '#c79a6b' },
  { id: 'minimal', label: '极简', tone: '#9aa4b2' }
];
var LIB_FAV_FAMILY = '__fav__';
var LIB_CUSTOM_FAMILY = '__custom__';
var LIB_FAV_KEY = 'nts.library.favorites';
var LIB_COLLAPSE_KEY = 'nts.catalog.collapsed';

/** 六个合法族的 id 列表（顺序与 tab 一致）。 */
function libFamilyIds() {
  return LIB_FAMILIES.map(function (family) { return family.id; });
}

function libFamilyLabel(family) {
  if (family === LIB_CUSTOM_FAMILY) return '自定义';
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) {
    if (LIB_FAMILIES[i].id === family) return LIB_FAMILIES[i].label;
  }
  return '其他';
}

function libFamilyTone(family) {
  if (family === LIB_CUSTOM_FAMILY) return '#8f7bff';
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) {
    if (LIB_FAMILIES[i].id === family) return LIB_FAMILIES[i].tone;
  }
  return '#8a8f98';
}

/**
 * 条目的风格族：后端合法 family 直接用；旧数据没有 family 时按亮/暗变量数
 * 归入 dark / light（契约 §3.1 允许的一支），保证任何条目都有可浏览的族。
 */
function libFamilyOf(item) {
  var declared = item && typeof item.family === 'string' ? item.family.toLowerCase() : '';
  if (libFamilyIds().indexOf(declared) >= 0) return declared;
  var dark = item && isFinite(item.dark) ? Number(item.dark) : 0;
  var light = item && isFinite(item.light) ? Number(item.light) : 0;
  return dark > light ? 'dark' : 'light';
}

function libTagsOf(item) {
  var tags = item && item.tags;
  if (!(tags instanceof Array)) return [];
  var out = [];
  for (var i = 0; i < tags.length; i += 1) {
    var raw = tags[i] === null || tags[i] === undefined ? '' : String(tags[i]);
    var tag = raw.slice(0, 12);
    if (tag && out.indexOf(tag) < 0) out.push(tag);
  }
  return out;
}

function libSearchText(item) {
  if (!item) return '';
  return [String(item.name || ''), String(item.note || ''), libTagsOf(item).join(' ')]
    .join(' ').toLowerCase();
}

/** 搜索：name / note / tags 子串，大小写不敏感。 */
function libMatchesQuery(item, query) {
  var q = String(query || '').trim().toLowerCase();
  if (!q) return true;
  return libSearchText(item).indexOf(q) >= 0;
}

function libMatchesTag(item, tag) {
  if (!tag) return true;
  return libTagsOf(item).indexOf(String(tag)) >= 0;
}

function libIsFavorite(item, favorites) {
  if (!item || !item.id || !(favorites instanceof Array)) return false;
  return favorites.indexOf(String(item.id)) >= 0;
}

/** 风格族过滤；LIB_FAV_FAMILY / LIB_CUSTOM_FAMILY 是两个虚拟族。 */
function libMatchesFamily(item, family, favorites) {
  if (!family) return true;
  if (family === LIB_FAV_FAMILY) return libIsFavorite(item, favorites);
  // 「自定义」= 用户自己保存或导入的条目（后端 builtin=false）。
  if (family === LIB_CUSTOM_FAMILY) return !item || !item.builtin;
  return libFamilyOf(item) === family;
}

/**
 * 过滤主流程：family && tag && query 三重叠加。
 * 「全部」视图（family 为空）把收藏置顶，其余保持后端给的顺序（内置在前、新的在前）。
 */
function libSelectLibrary(items, opts) {
  var query = opts && opts.query ? opts.query : '';
  var tag = opts && opts.tag ? opts.tag : '';
  var family = opts && opts.family ? opts.family : '';
  var favorites = opts && opts.favorites instanceof Array ? opts.favorites : [];
  var picked = [];
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    var item = list[i];
    if (!item) continue;
    if (!libMatchesTag(item, tag)) continue;
    if (!libMatchesQuery(item, query)) continue;
    if (!libMatchesFamily(item, family, favorites)) continue;
    picked.push(item);
  }
  if (family) {
    return family === LIB_CUSTOM_FAMILY
      ? libSortCustomWithFavorites(picked, favorites)
      : picked;
  }
  var favs = [];
  var rest = [];
  for (var j = 0; j < picked.length; j += 1) {
    if (libIsFavorite(picked[j], favorites)) favs.push(picked[j]);
    else rest.push(picked[j]);
  }
  return favs.concat(rest);
}

/**
 * 「自定义」视图也要收藏置顶：自己的主题往往会攒到几十个，
 * 常用的那几套先冒出来才方便。
 */
function libSortCustomWithFavorites(items, favorites) {
  var favs = [];
  var rest = [];
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    if (libIsFavorite(list[i], favorites)) favs.push(list[i]);
    else rest.push(list[i]);
  }
  return favs.concat(rest);
}

/**
 * 各族 tab 的数量徽标：在当前 tag + 搜索条件下，每个族还能筛出多少条。
 * 这样徽标本身就是「点这个 tab 会看到几条」，0 条时也能一眼看出该换条件。
 */
function libFamilyCounts(items, query, tag, favorites) {
  var counts = { all: 0, fav: 0, custom: 0, families: {} };
  for (var i = 0; i < LIB_FAMILIES.length; i += 1) counts.families[LIB_FAMILIES[i].id] = 0;
  var list = items instanceof Array ? items : [];
  for (var j = 0; j < list.length; j += 1) {
    var item = list[j];
    if (!item) continue;
    if (!libMatchesTag(item, tag)) continue;
    if (!libMatchesQuery(item, query)) continue;
    counts.all += 1;
    var family = libFamilyOf(item);
    if (counts.families[family] !== undefined) counts.families[family] += 1;
    if (libIsFavorite(item, favorites)) counts.fav += 1;
    if (!item.builtin) counts.custom += 1;
  }
  return counts;
}

/** 只留下仍存在于仓库列表里的收藏 id（refreshLibrary 用，旧 id 自动清理）。 */
function libPruneFavorites(favorites, items) {
  var alive = {};
  var list = items instanceof Array ? items : [];
  for (var i = 0; i < list.length; i += 1) {
    if (list[i] && list[i].id) alive[String(list[i].id)] = true;
  }
  var out = [];
  var favs = favorites instanceof Array ? favorites : [];
  for (var j = 0; j < favs.length; j += 1) {
    var id = favs[j] === null || favs[j] === undefined ? '' : String(favs[j]);
    if (id && alive[id] && out.indexOf(id) < 0) out.push(id);
  }
  return out;
}

function libStorageRead(storage, key) {
  if (!storage) return null;
  try { return storage.getItem(key); } catch (error) { return null; }
}

function libStorageWrite(storage, key, value) {
  if (!storage) return false;
  try { storage.setItem(key, value); return true; } catch (error) { return false; }
}

/** 收藏 id 列表：非法 JSON / 非数组 / 非字符串项一律丢弃（localStorage 可能被改坏）。 */
function libReadIdList(storage, key) {
  var raw = libStorageRead(storage, key);
  if (!raw) return [];
  var parsed = null;
  try { parsed = JSON.parse(raw); } catch (error) { parsed = null; }
  if (!(parsed instanceof Array)) return [];
  var out = [];
  for (var i = 0; i < parsed.length; i += 1) {
    var id = parsed[i];
    if (typeof id === 'string' && id && out.indexOf(id) < 0) out.push(id);
  }
  return out;
}

function libWriteIdList(storage, key, ids) {
  var list = ids instanceof Array ? ids.slice(0, 400) : [];
  return libStorageWrite(storage, key, JSON.stringify(list));
}

/** 折叠状态：只收 names 里列出的分组名，且只认 true（默认全部展开）。 */
function libReadCollapsed(storage, key, names) {
  var raw = libStorageRead(storage, key);
  var out = {};
  if (!raw) return out;
  var parsed = null;
  try { parsed = JSON.parse(raw); } catch (error) { return out; }
  if (!parsed || typeof parsed !== 'object') return out;
  var known = names instanceof Array ? names : [];
  for (var i = 0; i < known.length; i += 1) {
    if (parsed[known[i]] === true) out[known[i]] = true;
  }
  return out;
}

function libWriteCollapsed(storage, key, collapsed, names) {
  var known = names instanceof Array ? names : [];
  var out = {};
  for (var i = 0; i < known.length; i += 1) {
    if (collapsed && collapsed[known[i]]) out[known[i]] = true;
  }
  return libStorageWrite(storage, key, JSON.stringify(out));
}
/* ===== LIB_CORE_END ===== */

/** 主题仓库的本地视图状态（v0.2.0：风格族 tab + 搜索 + 收藏 + 标签筛选）。 */
var libraryState = {
  items: [],
  filter: '',    // 标签筛选（'' = 全部标签）
  family: '',    // 风格族筛选（'' = 全部；LIB_FAV_FAMILY = 收藏虚拟族）
  query: '',     // 搜索词
  favorites: [], // 收藏 id 列表（与 localStorage 同步）
  lastShuffle: null
};
var LIB_EMPTY_TEXT = '仓库还是空的。调好配色后点「保存」存成自己的主题；别人分享的 .zip / .json 用「导入」收进来，之后随时切回来，也能「分享」成 zip 发给别人。';

// 主题库卡片节点缓存：搜索/标签/族筛选只改变「哪些卡片可见」，卡片本身不用重建。
// 键 = 主题 id，值 = { sig, node }。sig 不含过滤条件——标签 chip 的 aria-pressed 之后就地同步。
var libraryCardCache = Object.create(null);

/** localStorage 句柄：隐私模式 / 被禁用时 setItem 会抛，读写一律走 try/catch。 */
function libraryStorage() {
  try { return window.localStorage; } catch (error) { return null; }
}

function saveLibraryFavorites() {
  libWriteIdList(libraryStorage(), LIB_FAV_KEY, libraryState.favorites);
}

/** 切换收藏；返回 true = 这次是「收藏」。 */
function toggleLibraryFavorite(id) {
  var key = String(id || '');
  if (!key) return false;
  var at = libraryState.favorites.indexOf(key);
  if (at >= 0) libraryState.favorites.splice(at, 1);
  else libraryState.favorites.push(key);
  saveLibraryFavorites();
  return at < 0;
}

/** 重渲染会替换星标按钮，把焦点还给同一张卡的星标（键盘用户不丢位置）。 */
function refocusLibraryFav(id) {
  var list = $('lib-list');
  if (!list || typeof list.querySelectorAll !== 'function') return;
  var nodes = list.querySelectorAll('.lib-fav');
  for (var i = 0; i < nodes.length; i += 1) {
    if (nodes[i].getAttribute('data-fav-id') === String(id)) {
      if (typeof nodes[i].focus === 'function') nodes[i].focus();
      return;
    }
  }
}

/** 分类编辑器：分组名列表（折叠状态只持久化这些已知分组）。 */
function catalogGroupNames() {
  return CATALOG.map(function (group) { return group.group; });
}

/** 读取折叠状态（nts.catalog.collapsed）；没有记录 = 全部展开。 */
function loadCatalogCollapsed() {
  state.collapsed = libReadCollapsed(libraryStorage(), LIB_COLLAPSE_KEY, catalogGroupNames());
}

function saveCatalogCollapsed() {
  libWriteCollapsed(libraryStorage(), LIB_COLLAPSE_KEY, state.collapsed, catalogGroupNames());
}

/** 从条目里收集标签，按出现次数排序（内置主题自带 tags）。 */
function libraryTags(items) {
  var counts = {};
  items.forEach(function (item) {
    var tags = item && item.tags instanceof Array ? item.tags : [];
    tags.forEach(function (tag) {
      var key = String(tag || '').slice(0, 12);
      if (!key) return;
      counts[key] = (counts[key] || 0) + 1;
    });
  });
  return Object.keys(counts).sort(function (a, b) {
    if (counts[b] !== counts[a]) return counts[b] - counts[a];
    return a < b ? -1 : 1;
  });
}

/** 缩略色板：当前亮/暗模式下取后端给的 preview 快照。 */
function buildLibraryThumb(item) {
  var preview = null;
  if (item && item.preview && typeof item.preview === 'object') {
    preview = item.preview[state.mode] || item.preview.light || null;
  }
  var thumb = el('span', { class: 'lib-thumb', 'aria-hidden': 'true' });
  if (!preview) {
    thumb.classList.add('is-blank');
    return thumb;
  }
  // 值都来自后端校验过的变量，但这里仍走 CSSOM 逐条赋值：
  // 不拼 HTML、也不整串塞 style 属性，非法值最多被浏览器忽略。
  try {
    if (preview.bg) thumb.style.backgroundColor = String(preview.bg);
    if (preview.gradient) thumb.style.backgroundImage = String(preview.gradient);
    if (preview.panel) thumb.style.borderColor = String(preview.panel);
  } catch (error) { /* 个别浏览器拒绝某个值，忽略即可 */ }
  var dots = el('span', { class: 'lib-dots', 'aria-hidden': 'true' });
  var solidUser = preview.userSolid || preview.ai || '';
  if (solidUser) {
    var dotUser = el('span', { class: 'lib-dot' });
    try { dotUser.style.backgroundColor = String(solidUser); } catch (error) { /* 忽略 */ }
    dots.appendChild(dotUser);
  }
  if (preview.accent) {
    var dotAccent = el('span', { class: 'lib-dot lib-dot-accent' });
    try { dotAccent.style.backgroundColor = String(preview.accent); } catch (error) { /* 忽略 */ }
    dots.appendChild(dotAccent);
  }
  thumb.appendChild(dots);
  return thumb;
}


/** 卡片缓存指纹：只包含「会让这张卡 DOM 变化」的因素（模式、收藏态、条目数据）。 */
function libraryCardSig(item) {
  var tags = libTagsOf(item);
  return [
    state.mode,
    libIsFavorite(item, libraryState.favorites) ? '1' : '0',
    libFamilyOf(item),
    String(item && item.id !== undefined ? item.id : ''),
    String(item && item.name || ''),
    String(item && item.note || ''),
    String(item && item.saved_at || ''),
    String(item && item.light || 0),
    String(item && item.dark || 0),
    item && item.has_image ? '1' : '0',
    item && item.material ? String(item.material.preset || '') + '/' + String(item.material.quality || '') : '',
    tags.join('\u0001')
  ].join('\u0000');
}

/** 标签 chip 的选中态取决于当前筛选词，就地同步，避免为了一个属性重建整张卡。 */
function syncLibraryCardTags(node) {
  if (!node || !node.querySelectorAll) return;
  var chips = node.querySelectorAll('.lib-tag');
  for (var i = 0; i < chips.length; i += 1) {
    var pressed = chips[i].textContent === libraryState.filter ? 'true' : 'false';
    if (chips[i].getAttribute('aria-pressed') !== pressed) {
      chips[i].setAttribute('aria-pressed', pressed);
    }
  }
}

/** 仓库条目的一行（名字/描述用 textContent，绝不拼 HTML）。 */
function buildLibraryItem(item) {
  var name = String(item.name || item.id || '未命名');
  var family = libFamilyOf(item);
  var favorite = libIsFavorite(item, libraryState.favorites);
  var meta = [];
  // 内置主题的 saved_at 是「内置」，不要再当时间显示。
  if (item.builtin) meta.push('内置');
  else meta.push(String(item.saved_at || '').replace('T', ' ').slice(0, 16) || '未记录时间');
  meta.push('亮 ' + (item.light || 0) + ' / 暗 ' + (item.dark || 0) + ' 项');
  if (item.has_image) meta.push('含背景图');

  // 族徽标：中文族名 + 小色点（色点用 CSSOM 赋值，不拼 style 字符串）。
  var familyDot = el('span', { class: 'lib-family-dot', 'aria-hidden': 'true' });
  try { familyDot.style.backgroundColor = libFamilyTone(family); } catch (error) { /* 忽略 */ }
  var familyChip = el('span', { class: 'lib-family-chip', 'data-family': family }, [
    familyDot,
    el('span', { text: libFamilyLabel(family) })
  ]);

  // 材质徽标：这套主题自带高级材质（切过去会一起换），老主题没有就只显示族。
  var nameRow = el('span', { class: 'lib-name-row' }, [
    el('span', { class: 'lib-name-text', text: name, title: name }),
    familyChip
  ]);
  if (item.material && item.material.enabled === false) {
    // 配色版主题显式携带 materials.enabled=false：它要关掉高级材质，
    // 所以徽标必须说「无材质」，不能再显示一个具体的预设名。
    nameRow.appendChild(el('span', {
      class: 'lib-material-chip is-off',
      title: '这套主题不带高级材质；应用后会关闭高级材质（你手调的参数会保留）'
    }, [el('span', { text: '无材质' })]));
  } else if (item.material) {
    var materialLabel = String(item.material.label || item.material.preset || '材质');
    var qualityLabel = String(item.material.quality_label || item.material.quality || '');
    var materialChip = el('span', {
      class: 'lib-material-chip',
      'data-preset': String(item.material.preset || ''),
      title: '自带高级材质：' + materialLabel + (qualityLabel ? ' · ' + qualityLabel : '')
        + (item.material.enabled === false ? '（主题里默认未启用）' : '')
    }, [el('span', { text: '材质 · ' + materialLabel })]);
    nameRow.appendChild(materialChip);
  }
  var text = el('div', { class: 'lib-text' }, [nameRow]);
  // 描述一行截断；title 里放全文，鼠标悬停能看到完整说明。
  var noteText = item.note ? String(item.note) : '';
  if (noteText) {
    text.appendChild(el('span', {
      class: 'lib-note',
      text: noteText.slice(0, 160),
      title: noteText.slice(0, 500)
    }));
  } else if (!item.builtin) {
    // 自定义条目没写简介时给一个入口提示：用户不用先猜「编辑」能干什么。
    text.appendChild(el('span', {
      class: 'lib-note is-empty',
      text: '没有简介 · 点「编辑」写一句，分享时会一起带走',
      title: '简介会显示在卡片上，也会写进分享包的 theme.json'
    }));
  }
  text.appendChild(el('span', { class: 'lib-meta', text: meta.join(' · ') }));
  var tags = libTagsOf(item);
  if (tags.length) {
    var tagRow = el('span', { class: 'lib-tags' });
    tags.slice(0, 4).forEach(function (tag) {
      var chip = el('button', {
        type: 'button', class: 'lib-tag', text: tag,
        title: '只看「' + tag + '」主题',
        'aria-pressed': libraryState.filter === tag ? 'true' : 'false'
      });
      chip.addEventListener('click', function (event) {
        event.stopPropagation();
        libraryState.filter = libraryState.filter === tag ? '' : tag;
        renderLibrary();
      });
      tagRow.appendChild(chip);
    });
    text.appendChild(tagRow);
  }
  var main = el('div', { class: 'lib-main' }, [buildLibraryThumb(item), text]);

  // 收藏按钮：放在操作行里并带文字。之前是右上角一枚半透明的 ☆（22px、opacity .5），
  // 实际反馈是「根本找不到收藏在哪」，所以改成有文字的按钮（重渲染会换节点，
  // 仍然按 data-fav-id 找回焦点）。
  var favBtn = el('button', {
    type: 'button',
    class: 'btn btn-mini lib-fav' + (favorite ? ' is-on' : ''),
    text: favorite ? '★ 已收藏' : '☆ 收藏',
    'data-fav-id': String(item.id || ''),
    'aria-pressed': favorite ? 'true' : 'false',
    'aria-label': (favorite ? '取消收藏 ' : '收藏 ') + name,
    title: favorite ? '取消收藏' : '收藏（进「收藏」分类，并在「全部」里置顶）'
  });
  favBtn.addEventListener('click', function (event) {
    event.stopPropagation();
    var nowFavorite = toggleLibraryFavorite(item.id);
    renderLibrary();
    if (!refocusLibraryFav(item.id) && !nowFavorite) focusFamilyTab(LIB_FAV_FAMILY);
  });

  // 「编辑」：只改名称/备注/标签。内置主题不给改（后端也会拒绝），所以按 builtin 决定是否渲染。
  var metaBtn = item.builtin ? null : el('button', {
    type: 'button', class: 'btn btn-mini lib-meta', 'data-act': 'meta', text: '编辑',
    title: '改名称 / 备注 / 标签（id 不变，收藏和排程照旧认它）',
    'aria-label': '编辑主题信息 ' + name
  });
  if (metaBtn) {
    metaBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      openMetaModal(item);
    });
  }

  var applyBtn = el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'apply', text: '应用',
    'aria-label': '应用主题 ' + name
  });
  var exportBtn = el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'export', text: '分享',
    title: '导出成 zip（含背景图片）', 'aria-label': '分享主题 ' + name
  });
  // 「JSON」：把这一套主题导出成纯文本（贴进对话/issue 就能分享）。
  // 「分享」给的是 zip（含背景图），两者互补：zip 适合自己存档，JSON 适合贴文本。
  var jsonBtn = el('button', {
    type: 'button', class: 'btn btn-mini lib-json', 'data-act': 'json', text: 'JSON',
    title: '复制成纯文本 JSON（贴给对方，对方在「导入」里粘贴即可）',
    'aria-label': '复制主题 JSON ' + name
  });
  jsonBtn.addEventListener('click', function (event) {
    event.stopPropagation();
    copyLibraryItemJson(item, jsonBtn);
  });

  // 内置主题删不掉（后端也会拒绝），直接不渲染删除按钮。
  var deleteBtn = item.builtin ? null : el('button', {
    type: 'button', class: 'btn btn-mini', 'data-act': 'delete', text: '删除',
    'aria-label': '删除主题 ' + name
  });

  applyBtn.addEventListener('click', function () { applyLibraryItem(item, applyBtn); });
  exportBtn.addEventListener('click', function () { exportLibraryItem(item, exportBtn); });
  if (deleteBtn) {
    deleteBtn.addEventListener('click', function () { deleteLibraryItem(item, deleteBtn); });
  }

  var libBtns = [favBtn, applyBtn, exportBtn, jsonBtn];
  if (metaBtn) libBtns.push(metaBtn);
  if (deleteBtn) libBtns.push(deleteBtn);
  var row = el('li', { class: 'lib-item' + (favorite ? ' is-fav' : '') }, [
    main,
    el('div', { class: 'lib-btns' }, libBtns)
  ]);
  row.setAttribute('data-id', String(item.id || ''));
  // 悬停/焦点预演：鼠标经过或键盘聚焦时右侧预览换成这一套，离开恢复。
  // 只有真正指向卡片本体才算（卡片内的按钮悬停也会冒泡到卡片，这没关系：
  // 用户在看这张卡的应用/分享按钮时，预览它本来就是对的）。
  row.addEventListener('pointerenter', function () { showHoverPreview(item); });
  row.addEventListener('pointerleave', function () { clearHoverPreview(); });
  row.addEventListener('focusin', function () { showHoverPreview(item); });
  row.addEventListener('focusout', function () { clearHoverPreview(); });
  return row;
}

/** 风格族 tab 行（全部 / 收藏 / 六个实体族），带当前筛选下的数量徽标。 */
function renderLibraryFamilies() {
  var host = $('lib-family-tabs');
  if (!host) return;
  host.innerHTML = '';
  if (!libraryState.items.length) { host.hidden = true; return; }
  host.hidden = false;
  var counts = libFamilyCounts(libraryState.items, libraryState.query, libraryState.filter,
    libraryState.favorites);
  var makeTab = function (family, label, count) {
    var tab = el('button', {
      type: 'button',
      class: 'lib-family-tab' + (libraryState.family === family ? ' is-active' : ''),
      'data-family': family,
      'aria-pressed': libraryState.family === family ? 'true' : 'false'
    }, [
      el('span', { class: 'lib-family-tab-label', text: label }),
      el('span', { class: 'lib-family-tab-count', text: String(count) })
    ]);
    tab.addEventListener('click', function () {
      libraryState.family = family;
      renderLibrary();
    });
    return tab;
  };
  host.appendChild(makeTab('', '全部', counts.all));
  // 「收藏」「自定义」常驻（即使 0 条也保留）：它们是入口而不是筛选结果，
  // 藏起来时用户根本不知道有这个功能；点进去有对应的引导文案。
  host.appendChild(makeTab(LIB_FAV_FAMILY, '收藏', counts.fav));
  host.appendChild(makeTab(LIB_CUSTOM_FAMILY, '自定义', counts.custom));
  LIB_FAMILIES.forEach(function (family) {
    host.appendChild(makeTab(family.id, family.label, counts.families[family.id] || 0));
  });
}

/** 取消最后一条收藏后卡片消失时，把焦点还给「收藏」tab。 */
function focusFamilyTab(family) {
  var host = $('lib-family-tabs');
  if (!host || typeof host.querySelectorAll !== 'function') return;
  var tabs = host.querySelectorAll('.lib-family-tab');
  for (var i = 0; i < tabs.length; i += 1) {
    if (tabs[i].getAttribute('data-family') === String(family)) {
      if (typeof tabs[i].focus === 'function') tabs[i].focus();
      return;
    }
  }
}

/** 标签筛选条（内置主题多起来以后，一屏十几行很难找）。 */
function renderLibraryFilters() {
  var host = $('lib-filters');
  if (!host) return;
  var tags = libraryTags(libraryState.items);
  host.innerHTML = '';
  if (!tags.length) { host.hidden = true; return; }
  host.hidden = false;
  var makeChip = function (label, value) {
    var chip = el('button', {
      type: 'button',
      class: 'lib-chip' + (libraryState.filter === value ? ' is-active' : ''),
      text: label,
      'aria-pressed': libraryState.filter === value ? 'true' : 'false'
    });
    chip.addEventListener('click', function () {
      libraryState.filter = value;
      renderLibrary();
    });
    return chip;
  };
  host.appendChild(makeChip('全部', ''));
  tags.forEach(function (tag) { host.appendChild(makeChip(tag, tag)); });
}

/** 按当前「族 + 标签 + 搜索」筛选渲染仓库列表，「全部」视图里收藏置顶。 */
function renderLibrary() {
  var list = $('lib-list');
  var empty = $('lib-empty');
  if (!list) return null;
  var items = libraryState.items;
  var visible = libSelectLibrary(items, {
    query: libraryState.query,
    tag: libraryState.filter,
    family: libraryState.family,
    favorites: libraryState.favorites
  });
  // 复用上次渲染的卡片节点：输入搜索词时只增删可见项，不重建整表（24 套主题 × 10+ 节点）。
  var keep = Object.create(null);
  list.innerHTML = '';
  visible.forEach(function (item) {
    var key = String(item && item.id !== undefined ? item.id : '');
    keep[key] = true;
    var sig = libraryCardSig(item);
    var cached = libraryCardCache[key];
    if (!cached || cached.sig !== sig) {
      cached = { sig: sig, node: buildLibraryItem(item) };
      libraryCardCache[key] = cached;
    }
    syncLibraryCardTags(cached.node);
    list.appendChild(cached.node);
  });
  Object.keys(libraryCardCache).forEach(function (key) {
    if (!keep[key]) delete libraryCardCache[key];
  });
  if (empty) {
    empty.hidden = visible.length > 0;
    if (!items.length) empty.textContent = LIB_EMPTY_TEXT;
    else if (libraryState.query) {
      empty.textContent = '没有匹配「' + libraryState.query + '」的主题，换个词试试，或点「全部」清空筛选。';
    } else if (libraryState.family === LIB_FAV_FAMILY) {
      empty.textContent = '还没有收藏任何主题：点主题卡片下方的「☆ 收藏」按钮就能收藏，收藏会排在「全部」最前面。';
    } else if (libraryState.family === LIB_CUSTOM_FAMILY) {
      empty.textContent = '还没有自定义主题：「导入包」可以导入别人分享的 .zip / .json；调好配色点「保存」也会存到这里。';
    } else if (libraryState.filter) {
      empty.textContent = '没有「' + libraryState.filter + '」标签的主题，点「全部」看所有。';
    } else {
      empty.textContent = '这个风格族下还没有主题，试试别的族或「全部」。';
    }
  }
  renderLibraryFamilies();
  renderLibraryFilters();
  return visible;
}

async function refreshLibrary() {
  var list = $('lib-list');
  var empty = $('lib-empty');
  if (!list) return null;
  try {
    var result = await callPlugin('list_themes', {});
    // 后端返回 {count, themes:[...]}（早期版本写的是 items，字段名对不上时仓库永远空白）。
    var items = result && result.themes instanceof Array ? result.themes
      : (result && result.items instanceof Array ? result.items : []);
    libraryState.items = items;
    // 收藏里可能残留已被删除的主题 id：按新列表剪掉并写回 localStorage。
    var prunedFavorites = libPruneFavorites(libraryState.favorites, items);
    if (prunedFavorites.length !== libraryState.favorites.length || prunedFavorites.join('\n') !== libraryState.favorites.join('\n')) {
      libraryState.favorites = prunedFavorites;
      saveLibraryFavorites();
    }
    // 认不出的族值（旧版本 / 手改）退回「全部」；两个虚拟族是合法的。
    if (libraryState.family && libraryState.family !== LIB_FAV_FAMILY &&
        libraryState.family !== LIB_CUSTOM_FAMILY &&
        libFamilyIds().indexOf(libraryState.family) < 0) {
      libraryState.family = '';
    }
    // 当前筛选标签在新列表里不存在时自动回到「全部」。
    if (libraryState.filter && libraryTags(items).indexOf(libraryState.filter) < 0) {
      libraryState.filter = '';
    }
    renderLibrary();
    // 排程卡的下拉和「到点会切到哪套」都取自仓库列表：仓库一变就得重渲染，
    // 否则删掉 / 改名 / 导入主题之后它还显示旧名字（要重开面板才更新）。
    // 这里只重渲染，不发 RPC —— 排程配置本身没被 list_themes 改动。
    if (scheduleState.loaded) renderSchedule();
    return items;
  } catch (error) {
    // 仓库读不到不该挡住编辑器：只提示，不抛。
    if (empty) {
      empty.hidden = false;
      empty.textContent = '仓库读取失败：' + pluginErrorMessage(error);
    }
    return null;
  }
}

/** 「随机」：从内置主题里挑一套和上次不同的直接应用，不满意再点一次。 */
async function shuffleLibraryTheme() {
  if (state.busy) return;
  var builtins = libraryState.items.filter(function (item) { return item.builtin; });
  if (!builtins.length) {
    await refreshLibrary();
    builtins = libraryState.items.filter(function (item) { return item.builtin; });
  }
  if (!builtins.length) {
    showToast('还没有可随机的内置主题', 'error');
    return;
  }
  var pool = builtins.filter(function (item) { return item.id !== libraryState.lastShuffle; });
  if (!pool.length) pool = builtins;
  var pick = pool[Math.floor(Math.random() * pool.length)];
  libraryState.lastShuffle = pick.id;
  showToast('换个心情：' + String(pick.name || pick.id));
  await applyLibraryItem(pick, null);
}

async function saveLibraryTheme() {
  if (state.busy) return;
  var input = $('lib-name');
  var noteInput = $('lib-note');
  var name = input ? input.value.trim() : '';
  var note = noteInput ? noteInput.value.trim() : '';
  setBusy(true, '正在保存到仓库…');
  try {
    var args = {};
    if (name) args.name = name;
    if (note) args.note = note;
    var result = await callPlugin('save_theme', args);
    if (input) input.value = '';
    if (noteInput) noteInput.value = '';
    // 存完直接切到「自定义」，让用户看到刚存的那一条（内置主题不在这个分类里）。
    libraryState.family = LIB_CUSTOM_FAMILY;
    libraryState.filter = '';
    await refreshLibrary();
    var savedName = (result && result.theme && result.theme.name)
      || (result && result.name) || name || '当前主题';
    showToast('已保存「' + savedName + '」到自定义' + (note ? '（简介已写入，分享时一起带走）' : ''), 'ok');
    setStatus('已保存到自定义主题', 'ok');
  } catch (error) {
    showNotice('error', '保存到仓库失败：' + pluginErrorMessage(error));
    showToast('保存失败', 'error');
    setStatus('保存到仓库失败', 'error');
  } finally {
    setBusy(false);
  }
}

async function applyLibraryItem(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, '正在切换主题…');
  try {
    var result = await callPlugin('apply_saved_theme', { id: item.id });
    // 整体替换语义：面板里的未应用改动会失效，必须清干净再回读。
    state.edited = { light: {}, dark: {} };
    state.cleared = { light: {}, dark: {} };
    markAllRowsValid();
    clearRgbMemory();
    var reloaded = await reloadPalette({ silent: true });
    var label = (result && result.theme && result.theme.name)
      || (result && result.name) || item.name || '该主题';
    // 材质随主题：主题自带材质时明确告诉用户「材质也换了」，锁定时告诉用户「保留了」。
    var materialNote = '';
    if (result && result.material_applied && result.material_enabled === false) {
      materialNote = '，已关闭高级材质（这套主题不带材质）';
    } else if (result && result.material_applied) materialNote = '，材质已换成主题自带的那套';
    else if (result && result.material_present && result.material_locked) {
      materialNote = '，材质已锁定（保留你手调的设置）';
    }
    showNotice(reloaded ? 'ok' : 'warn', '已切换到「' + label + '」' + materialNote +
      (reloaded ? '' : '\n回读失败，面板显示可能不是最新值，请点「重新读取」。'));
    showToast('已切换到「' + label + '」' + materialNote, 'ok');
    setStatus('已切换主题' + (reloaded ? '' : '（回读失败）'), reloaded ? 'ok' : 'warn');
    verifyAppliedShortly();
  } catch (error) {
    showNotice('error', '切换主题失败：' + pluginErrorMessage(error));
    showToast('切换失败', 'error');
    setStatus('切换主题失败', 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

async function exportLibraryItem(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, '正在打包…');
  try {
    var result = await callPlugin('export_theme_archive', { id: item.id });
    var data = result && result.data ? String(result.data) : '';
    if (!data) throw new Error('后端没有返回包内容');
    var bytes = base64ToBytes(data);
    var filename = (result && result.filename) || ((item.name || 'theme') + '.zip');
    downloadBlob(bytes, filename, 'application/zip');
    var images = result && typeof result.images === 'number' ? result.images : 0;
    showToast('已导出 ' + filename + (images ? '（含 ' + images + ' 张图）' : ''), 'ok');
    setStatus('已导出分享包', 'ok');
  } catch (error) {
    showNotice('error', '导出分享包失败：' + pluginErrorMessage(error));
    showToast('导出失败', 'error');
    setStatus('导出分享包失败', 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

/** 把一套已存主题导出成纯文本 JSON，塞进只读弹窗里让人复制走。
 *
 * 与「分享」的区别：分享是 zip（能把背景图一起带走），这里是纯文本——
 * 图片没法变成文本，后端会把引用背景图的变量摘掉并告诉有几张，这里照实说明。
 * 也不直接写剪贴板：弹窗里能看见内容、能选中，剪贴板写失败时还能手动 Ctrl+C。
 */
async function copyLibraryItemJson(item, button) {
  if (state.busy) return;
  if (button) button.disabled = true;
  setBusy(true, '正在生成主题 JSON…');
  try {
    var result = await callPlugin('theme_json', { theme_id: item.id });
    var payload = result && typeof result.payload === 'string' ? result.payload : '';
    if (!payload) throw new Error('后端没有返回 JSON 内容');
    var name = (result && result.name) || item.name || item.id || '未命名';
    var images = result && typeof result.images === 'number' ? result.images : 0;
    var light = result && typeof result.light === 'number' ? result.light : 0;
    var dark = result && typeof result.dark === 'number' ? result.dark : 0;
    var hint = '这是「' + name + '」的纯文本主题（亮 ' + light + ' / 暗 ' + dark +
      ' 项）。贴给别人以后，对方在「导入」里粘贴就能用。';
    if (images > 0) {
      hint += '\n注意：这套主题引用了 ' + images + ' 张背景图，图片没法放进文本，' +
        '已从 JSON 里摘掉（对方会得到纯色背景）。要连图片一起分享请用「分享」导出 zip。';
    }
    openModal('export', '主题 JSON · ' + name, hint, payload);
    setStatus('已生成主题 JSON，点「复制并关闭」即可复制', 'ok');
  } catch (error) {
    showNotice('error', '生成主题 JSON 失败：' + pluginErrorMessage(error));
    showToast('生成失败', 'error');
    setStatus('生成主题 JSON 失败', 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

/* ============================================================================
 * 主题库：编辑名称 / 备注 / 标签（v0.4.18）
 * ==========================================================================*/

/** 打开「编辑主题信息」弹窗。内置主题不给改（后端也会拒绝）。 */
function openMetaModal(item) {
  if (!item || item.builtin) return;
  state.metaItem = item;
  openModal('meta', '编辑主题信息 · ' + String(item.name || item.id || ''),
    '只改名称、备注和标签。条目 id 不变 —— 收藏和「按时段自动换主题」里选的都是它，改名不会让它们失效。',
    '');
  $('meta-name-field').value = String(item.name || '');
  $('meta-note').value = String(item.note || '');
  $('meta-tags').value = (item.tags || []).join('，');
  var nameInput = $('meta-name-field');
  if (nameInput) {
    nameInput.focus();
    nameInput.select();
  }
}

/** 「暖色，渐变 ; 深色」→ ['暖色','渐变','深色']（最多 4 个，与后端 _theme_tags 同口径）。 */
function parseTagInput(text) {
  return String(text || '')
    .split(/[,，、;；\s]+/)
    .filter(function (part) { return part.trim(); })
    .map(function (part) { return part.trim().slice(0, 12); })
    .slice(0, 4);
}

async function saveThemeMeta() {
  var item = state.metaItem;
  if (!item) { closeModal(); return; }
  var name = String($('meta-name-field').value || '').trim();
  if (!name) {
    showToast('名称不能为空');
    $('meta-name-field').focus();
    return;
  }
  var okBtn = $('modal-ok');
  var original = okBtn.textContent;
  okBtn.disabled = true;
  okBtn.textContent = '保存中…';
  try {
    var result = await callPlugin('update_theme_meta', {
      theme_id: item.id,
      name: name,
      note: String($('meta-note').value || '').trim(),
      tags: parseTagInput($('meta-tags').value)
    });
    state.metaItem = null;
    closeModal();
    await refreshLibrary();
    if (result && result.updated === false) {
      showToast('没有变化，已保持原样');
    } else {
      showToast('已更新「' + String((result && result.name) || name) + '」', 'ok');
      setStatus('主题信息已更新', 'ok');
    }
  } catch (error) {
    // 弹窗不关：用户刚打的字还在，改一改再存即可。
    showNotice('error', '修改主题信息失败：' + pluginErrorMessage(error));
    showToast('修改失败，看看左侧提示');
  } finally {
    okBtn.disabled = false;
    okBtn.textContent = original;
  }
}

/* ============================================================================
 * 首次使用提示（v0.4.18）
 * ==========================================================================*/

var RESTART_HINT_KEY = 'nts.restartHint.dismissed';

/** 提示条：没关过就显示；关掉写 localStorage，隐私模式下写失败也不影响用（下次还会显示）。 */
function initRestartHint() {
  var bar = $('restart-hint');
  if (!bar) return;
  var dismissed = false;
  try { dismissed = localStorage.getItem(RESTART_HINT_KEY) === '1'; } catch (error) { dismissed = false; }
  bar.hidden = dismissed;
  if (dismissed) return;
  var closeBtn = $('restart-hint-close');
  if (!closeBtn) return;
  closeBtn.addEventListener('click', function () {
    bar.hidden = true;
    try { localStorage.setItem(RESTART_HINT_KEY, '1'); } catch (error) { /* 忽略 */ }
  });
}

async function deleteLibraryItem(item, button) {
  if (state.busy) return;
  var now = Date.now();
  var armed = pendingDelete.id === item.id && now - pendingDelete.at < LIB_CONFIRM_MS;
  if (!armed) {
    pendingDelete = { id: item.id, at: now };
    if (button) {
      button.classList.add('is-confirm');
      button.textContent = '确认删除';
    }
    // 这套主题可能正被「按时段自动换主题」用着：删掉之后那些时段到点只会保持
    // 当时正在用的主题，不会自动换 —— 得在删之前就说清楚，别等用户发现不灵。
    var refs = scheduleRefsFor(item.id);
    if (refs.length) {
      showNotice('warn', '「' + String(item.name || item.id) + '」正被自动换主题使用（' +
        refs.join(' / ') + '）。删掉之后这些时段会保持当时正在用的主题，不会自动换。');
      showToast('再点一次「确认删除」才会删掉（它正被自动换主题使用）');
    } else {
      showToast('再点一次「确认删除」才会删掉');
    }
    setTimeout(function () {
      // 超时自动解除，避免按钮永远停在确认态。
      if (button) {
        button.classList.remove('is-confirm');
        button.textContent = '删除';
      }
      if (pendingDelete.id === item.id) pendingDelete = { id: '', at: 0 };
    }, LIB_CONFIRM_MS);
    return;
  }
  pendingDelete = { id: '', at: 0 };
  if (button) button.disabled = true;
  setBusy(true, '正在删除…');
  try {
    await callPlugin('delete_theme', { id: item.id });
    await refreshLibrary();
    // 后端读到失效时段会静默丢掉，界面上的「现在是…会切到哪套」也要跟上。
    if (scheduleState.loaded) await refreshSchedule();
    showToast('已删除', 'ok');
    setStatus('已删除仓库条目', 'ok');
  } catch (error) {
    // 按钮在第 4406 行就被禁用了；失败后必须还原，否则这张卡永远是灰的，
    // 再点也没反应（列表签名不含禁用态，刷新列表还会复用同一个按钮）。
    if (button) {
      button.disabled = false;
      button.classList.remove('is-confirm');
      button.textContent = '删除';
    }
    showNotice('error', '删除失败：' + pluginErrorMessage(error));
    showToast('删除失败', 'error');
    setStatus('删除失败', 'error');
  } finally {
    setBusy(false);
  }
}

/** 读本地 zip / json 文件并导入仓库（只入仓库，不自动应用）。 */
function pickArchiveFile() {
  var fileInput = $('lib-file');
  if (fileInput) fileInput.click();
}

function handleArchiveFile(file, fileInput) {
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (fileInput) fileInput.value = '';
    if (!base64) { showToast('读取文件失败', 'error'); return; }
    importArchive(file.name, base64);
  };
  reader.onerror = function () {
    if (fileInput) fileInput.value = '';
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', '读取文件失败：' + why);
    showToast('读取文件失败', 'error');
  };
  reader.readAsDataURL(file);
}

async function importArchive(filename, base64) {
  if (state.busy) return;
  setBusy(true, '正在导入分享包…');
  try {
    var result = await callPlugin('import_theme_archive', {
      filename: filename || '',
      data: base64
    });
    // 导入的条目一律进「自定义」：直接切过去，用户马上能看到刚导入的那套，
    // 不会在「全部」里和二十多套内置主题混在一起找。
    libraryState.family = LIB_CUSTOM_FAMILY;
    libraryState.filter = '';
    await refreshLibrary();
    var label = (result && result.theme && result.theme.name)
      || (result && result.name) || '未命名主题';
    // import_theme_archive 返回 {imported, theme:{...has_image}, source_format, rejected}，
    // 没有顶层 images 计数，只能从 theme.has_image 推断（zip 里的图已落盘并改写引用）。
    var images = (result && result.theme && result.theme.has_image) ? 1 : 0;
    showNotice('ok', '已导入「' + label + '」到仓库' + (images ? '（含 ' + images + ' 张背景图）' : '') +
      '\n它还没有生效 —— 在列表里点「应用」才会切换过去。');
    showToast('已导入到仓库', 'ok');
    setStatus('已导入到仓库', 'ok');
  } catch (error) {
    showNotice('error', '导入分享包失败：' + pluginErrorMessage(error));
    showToast('导入失败', 'error');
    setStatus('导入分享包失败', 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 主题库备份 / 恢复（v0.4.16）
 *
 * 为什么需要：此前只有「单套主题」的分享 zip。换电脑、重装、或者误删
 * ``static/themes/`` 之后，用户自己那几十套自定义主题没有任何出口 ——
 * ``install.ps1`` 内部备份用户看不见也拿不到。这里补上「整套库」的进 / 出。
 *
 * 三个约束（对应 backend ``_build_library_archive`` / ``import_library``）：
 * 1. 备份包必须可移植：图片改写为 ``url("images/<name>")``，不含本机绝对地址；
 * 2. 恢复**只新增不覆盖**：重装后把老库拿回来时，仓库里可能已经有同名主题，
 *    各留一份；绝不能静默顶掉用户刚做的那些；
 * 3. 单条坏掉不毁整包：``skipped`` 里带着原因，其余照常导入并汇报。
 * ========================================================================== */

async function backupLibrary(button) {
  if (state.busy) return;
  if (!libraryState.items.length) {
    showToast('主题仓库还是空的，没有可备份的主题', 'warn');
    return;
  }
  setBusy(true, '正在打包主题库…');
  if (button) button.disabled = true;
  try {
    var result = await saveThemeFile({ kind: 'library' });
    var count = result.themes || 0;
    showNotice('ok', '主题库已备份（' + count + ' 套）\n' + savedHint(result) +
      '\n换电脑或重装后，用「恢复备份」选这个 zip 就能整套拿回来（只新增、不覆盖）。');
    showToast('已备份 ' + count + ' 套主题', 'ok');
    setStatus('已备份主题库', 'ok');
  } catch (error) {
    showNotice('error', '备份主题库失败：' + pluginErrorMessage(error));
    showToast('备份失败', 'error');
    setStatus('备份主题库失败', 'error');
  } finally {
    if (button) button.disabled = false;
    setBusy(false);
  }
}

function pickLibraryBackupFile() {
  var input = $('lib-backup-file');
  if (input) input.click();
}

function handleLibraryBackupFile(file, fileInput) {
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function () {
    var raw = String(reader.result || '');
    var comma = raw.indexOf(',');
    var base64 = comma >= 0 ? raw.slice(comma + 1) : '';
    if (fileInput) fileInput.value = '';
    if (!base64) { showToast('读取文件失败', 'error'); return; }
    restoreLibrary(file.name, base64);
  };
  reader.onerror = function () {
    if (fileInput) fileInput.value = '';
    var why = reader.error && reader.error.message ? reader.error.message : file.name;
    showNotice('error', '读取备份包失败：' + why);
    showToast('读取文件失败', 'error');
  };
  reader.readAsDataURL(file);
}

async function restoreLibrary(filename, base64) {
  if (state.busy) return;
  setBusy(true, '正在恢复主题库…');
  try {
    var result = await callPlugin('import_library', {
      filename: filename || '',
      data: base64
    });
    // 恢复进来的都是「自定义」：切过去让用户马上看到
    libraryState.family = LIB_CUSTOM_FAMILY;
    libraryState.filter = '';
    await refreshLibrary();
    var imported = (result && result.imported) || 0;
    var skipped = (result && result.skipped) || [];
    var text = '已从备份恢复 ' + imported + ' 套主题到仓库';
    if (skipped.length) {
      var shown = [];
      for (var i = 0; i < skipped.length && i < 3; i++) {
        shown.push('· ' + (skipped[i].name || '未命名') + '：' + (skipped[i].reason || '导入失败'));
      }
      text += '\n有 ' + skipped.length + ' 套没能恢复：\n' + shown.join('\n') +
        (skipped.length > shown.length ? '\n…' : '');
    }
    text += '\n恢复的主题只是进了仓库，点「应用」才会切换过去。';
    showNotice(skipped.length ? 'warn' : 'ok', text);
    showToast('已恢复 ' + imported + ' 套主题', skipped.length ? 'warn' : 'ok');
    setStatus('已恢复主题库', 'ok');
  } catch (error) {
    showNotice('error', '恢复主题库失败：' + pluginErrorMessage(error));
    showToast('恢复失败', 'error');
    setStatus('恢复主题库失败', 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 按时段自动换主题（v0.4.13）
 *
 * 分工：**后端负责换**，面板只负责配置。
 * 到点后后端把该时段的主题整体写进 theme.json，加载器 1.5s 内自己轮询到，
 * 于是「所有窗口（包括没开面板的）」一起变 —— 这里绝不在前端用定时器改样式，
 * 否则面板一关就失效，还会和加载器抢着写同一个 <style>。
 * 时段边界不在这里算：后端的 windows 原样下发，避免出现第三份「几点算早晨」。
 * ========================================================================== */

/** 时段 → 中文名。边界一律取 scheduleState.windows，不在前端写死小时数。 */
var SCHEDULE_ZONE_LABELS = { morning: '早晨', day: '白天', evening: '傍晚', night: '夜晚' };

var scheduleState = {
  schedule: { schema: 2, enabled: false, zones: {}, weekend_zones: {} },
  zones: ['morning', 'day', 'evening', 'night'],
  windows: [],
  now: null,
  loaded: false,
  /** 排程里记着、但仓库里已经不存在的时段（后端 get_schedule 的 stale）。 */
  stale: [],
  /** 周末表里只要有一个时段选了主题，就算「周末用另一套」。 */
  weekendEnabled: false,
  /** 卡里有还没保存的选择（改了下拉 / 勾了开关但没点保存）。 */
  dirty: false,
  /** 上次渲染选项用的主题列表指纹：列表没变就不重建 <option>，别丢焦点。 */
  optionsSig: ''
};

/** 取某一张表（工作日 / 周末）的 {时段: 主题 id}。 */
function scheduleMapOf(kind) {
  var schedule = scheduleState.schedule || {};
  var map = kind === 'weekend' ? schedule.weekend_zones : schedule.zones;
  return map && typeof map === 'object' ? map : {};
}

/** 周末表是否真的在用（有任一非空时段）。 */
function scheduleWeekendHasValues(schedule) {
  var map = schedule && schedule.weekend_zones;
  if (!map || typeof map !== 'object') return false;
  var zones = scheduleState.zones.length ? scheduleState.zones : ['morning', 'day', 'evening', 'night'];
  return zones.some(function (zone) {
    return String(map[zone] || '').trim() !== '';
  });
}

/** 从某个格子（#schedule-rows / #schedule-rows-weekend）收集 {时段: 主题 id}。 */
function collectScheduleZones(containerId) {
  var out = {};
  var rows = $(containerId);
  var selects = rows ? rows.querySelectorAll('.schedule-select') : [];
  for (var i = 0; i < selects.length; i += 1) {
    var zone = selects[i].getAttribute('data-zone');
    if (!zone) continue;
    out[zone] = selects[i].value || '';
  }
  return out;
}

function scheduleZoneLabel(zone) {
  return SCHEDULE_ZONE_LABELS[String(zone)] || String(zone);
}

function schedulePad2(value) {
  var n = Number(value);
  if (!isFinite(n)) n = 0;
  return (n < 10 ? '0' : '') + n;
}

/** 「06:00–10:00」；night 跨零点时显示成「22:00–次日 06:00」。 */
function scheduleWindowText(zone) {
  var win = null;
  scheduleState.windows.forEach(function (item) {
    if (item && item.zone === zone) win = item;
  });
  if (!win) return '';
  var start = schedulePad2(win.start) + ':00';
  var end = schedulePad2(win.end) + ':00';
  if (Number(win.start) > Number(win.end)) return start + '–次日 ' + end;
  return start + '–' + end;
}

/** 主题列表 → 选项。内置与自定义分两组，内置在前（大多数人的默认选择）。 */
function scheduleThemeGroups() {
  var builtin = [];
  var custom = [];
  (libraryState.items || []).forEach(function (item) {
    if (!item || !item.id) return;
    var option = { id: String(item.id), name: String(item.name || item.id) };
    if (item.builtin) builtin.push(option);
    else custom.push(option);
  });
  return { builtin: builtin, custom: custom };
}

function scheduleOptionsSig(groups) {
  var parts = [];
  groups.builtin.concat(groups.custom).forEach(function (option) {
    parts.push(option.id + '\u0001' + option.name);
  });
  return parts.join('\u0000');
}

/** 重建某个时段的下拉框（只在主题列表变化时调用）。 */
function fillScheduleSelect(select, groups, current) {
  if (!select) return;
  select.innerHTML = '';
  var none = el('option', { value: '', text: '（不换，保持原样）' });
  select.appendChild(none);
  [
    { label: '内置主题', items: groups.builtin },
    { label: '我的主题', items: groups.custom }
  ].forEach(function (group) {
    if (!group.items.length) return;
    var optgroup = el('optgroup', { label: group.label });
    group.items.forEach(function (option) {
      optgroup.appendChild(el('option', { value: option.id, text: option.name }));
    });
    select.appendChild(optgroup);
  });
  // 排程里引用的主题可能已被删除：补一个占位项，否则 select.value 赋值会静默失效、
  // 界面显示第一项「（不换）」而实际排程里还留着那个 id（下一次保存才暴露）。
  var known = groups.builtin.concat(groups.custom).some(function (option) {
    return option.id === current;
  });
  if (current && !known) {
    select.appendChild(el('option', { value: current, text: current + '（已不存在）' }));
  }
  select.value = current || '';
}

/** 记住「排程卡有未保存的选择」，并把保存按钮标成待保存。 */
function markScheduleDirty() {
  if (scheduleState.dirty) return;
  scheduleState.dirty = true;
  updateScheduleDirtyHint();
}

/** 保存成功 / 明确放弃后清掉 dirty 标记（下拉与两个开关都清）。 */
function clearScheduleDirty() {
  scheduleState.dirty = false;
  ['schedule-weekend', 'schedule-enabled'].forEach(function (id) {
    var node = $(id);
    if (node) delete node.dataset.dirty;
  });
  var selects = document.querySelectorAll('.schedule-select');
  for (var i = 0; i < selects.length; i += 1) delete selects[i].dataset.dirty;
  updateScheduleDirtyHint();
}

function updateScheduleDirtyHint() {
  var button = $('btn-schedule-save');
  if (!button) return;
  button.classList.toggle('is-dirty', !!scheduleState.dirty);
  button.textContent = scheduleState.dirty ? '保存 *' : '保存';
  button.title = scheduleState.dirty
    ? '有还没保存的选择（点这里保存）'
    : '保存工作日与周末的时段选择';
}

/** 重建一格（工作日 / 周末）的四个时段下拉；同一份签名就不动 DOM（别丢焦点）。 */
function renderScheduleGrid(containerId, kind) {
  var rows = $(containerId);
  if (!rows) return;
  var groups = scheduleThemeGroups();
  var zones = scheduleState.zones.length ? scheduleState.zones : ['morning', 'day', 'evening', 'night'];
  var map = scheduleMapOf(kind);
  var signature = scheduleOptionsSig(groups) + '|' + zones.join(',') + '|' + kind;
  if (rows.getAttribute('data-sig') === signature) {
    var selects = rows.querySelectorAll('.schedule-select');
    for (var i = 0; i < selects.length; i += 1) {
      // 用户改过但还没保存的选择必须留住：刷仓库 / 勾周末 / 外部同步都会
      // 走到这里，旧实现把 select.value 一律写回「已保存值」，等于静默撤销
      // 用户刚做的选择（而且没有任何提示）。
      if (selects[i].dataset.dirty === '1') continue;
      var zone = selects[i].getAttribute('data-zone');
      var want = String(map[zone] || '');
      if (selects[i].value !== want) selects[i].value = want;
    }
    return;
  }
  rows.innerHTML = '';
  zones.forEach(function (zone) {
    var select = el('select', {
      class: 'schedule-select',
      'data-zone': zone,
      'data-kind': kind,
      'aria-label': (kind === 'weekend' ? '周末' : '工作日') + scheduleZoneLabel(zone) + '用哪套主题'
    });
    fillScheduleSelect(select, groups, String(map[zone] || ''));
    select.addEventListener('change', function () {
      this.dataset.dirty = '1';
      markScheduleDirty();
    });
    rows.appendChild(el('div', { class: 'schedule-row' }, [
      el('span', { class: 'schedule-when' }, [
        el('b', { text: scheduleZoneLabel(zone) }),
        el('i', { text: scheduleWindowText(zone) })
      ]),
      select
    ]));
  });
  rows.setAttribute('data-sig', signature);
}

/** stale 列表压成一句人话（测试/调试用，渲染里只要长度就不重复调）。 */
function scheduleStaleText() {
  var stale = scheduleState.stale || [];
  if (!stale.length) return '';
  var labels = stale.map(function (item) {
    var kind = item && item.day_kind === 'weekend' ? '周末 ' : '工作日 ';
    return kind + scheduleZoneLabel(item && item.zone);
  });
  return labels.join(' / ');
}

/** 这个主题被自动换主题的哪些时段用着（删除前预警用）。 */
function scheduleRefsFor(themeId) {
  var id = String(themeId || '');
  if (!id) return [];
  var schedule = scheduleState.schedule || {};
  var refs = [];
  [['weekday', schedule.zones], ['weekend', schedule.weekend_zones]].forEach(function (pair) {
    var kind = pair[0];
    var map = pair[1] || {};
    Object.keys(map).forEach(function (zone) {
      if (String(map[zone] || '') !== id) return;
      var label = (kind === 'weekend' ? '周末 ' : '工作日 ') + scheduleZoneLabel(zone);
      if (refs.indexOf(label) < 0) refs.push(label);
    });
  });
  return refs;
}

/** 按 scheduleState 重渲染整张卡。 */
function renderSchedule() {
  renderScheduleGrid('schedule-rows', 'weekday');
  renderScheduleGrid('schedule-rows-weekend', 'weekend');

  var weekendBox = $('schedule-weekend');
  if (weekendBox && weekendBox.dataset.dirty !== '1') {
    weekendBox.checked = !!scheduleState.weekendEnabled;
  }
  var weekendRows = $('schedule-rows-weekend');
  if (weekendRows) weekendRows.hidden = !scheduleState.weekendEnabled;

  var enabled = $('schedule-enabled');
  if (enabled && enabled.dataset.dirty !== '1') {
    enabled.checked = !!scheduleState.schedule.enabled;
  }

  var now = $('schedule-now');
  if (now) {
    var detail = scheduleState.now;
    if (!detail) {
      now.textContent = scheduleState.loaded ? '还没读到当前时段。' : '正在读取…';
    } else {
      var current = scheduleZoneLabel(detail.zone);
      var dayLabel = detail.day_kind === 'weekend' ? '周末' : '工作日';
      var picked = String(detail.theme_id || '');
      var theme = '未选（保持原样）';
      if (picked) {
        var name = picked;
        var known = false;
        (libraryState.items || []).forEach(function (item) {
          if (item && item.id === picked) {
            name = String(item.name || picked);
            known = true;
          }
        });
        // 排程里还记着这个 id，但仓库里已经没有了（被删 / 备份没恢复回来）：
        // 老实说出来，别让用户以为到点会切到一套不存在的主题。
        theme = known ? name : name + '（已不存在）';
      }
      var head = '现在是' + dayLabel + '的' + current + '（' + schedulePad2(detail.hour) + ' 点）';
      if (!scheduleState.schedule.enabled) {
        now.textContent = head + '；自动换主题已关闭。';
      } else if (picked) {
        var fromWeekend = detail.day_kind === 'weekend' && scheduleState.weekendEnabled;
        now.textContent = head + '，到点会切到「' + theme + '」' + (fromWeekend ? '（周末表）' : '') + '。';
      } else if (scheduleState.stale.length) {
        // 后端把失效时段丢掉了（排程文件里还留着那个 id），不解释的话用户只会
        // 看到「这个时段还没选」而不知道自己配的东西为什么没了。
        now.textContent = head + '；有 ' + scheduleState.stale.length +
          ' 个时段引用的主题已不在仓库里（重新导入那套主题或重新选一个就会恢复）。';
      } else {
        now.textContent = head + '；这个时段还没选主题，到点不会换。';
      }
      now.classList.toggle('is-idle', !scheduleState.schedule.enabled);
    }
  }

  var saveBtn = $('btn-schedule-save');
  if (saveBtn) saveBtn.disabled = !!state.busy;
}

/** 读排程。主题列表为空时顺手拉一次仓库（下拉框要有内容才可选）。 */
async function refreshSchedule() {
  try {
    if (!libraryState.items.length) await refreshLibrary();
    var result = await callPlugin('get_schedule', {});
    if (result && result.schedule) scheduleState.schedule = result.schedule;
    if (result && result.zones instanceof Array && result.zones.length) scheduleState.zones = result.zones;
    if (result && result.windows instanceof Array) scheduleState.windows = result.windows;
    scheduleState.now = (result && result.now) || null;
    scheduleState.stale = (result && result.stale instanceof Array) ? result.stale : [];
    scheduleState.weekendEnabled = scheduleWeekendHasValues(result && result.schedule);
    scheduleState.loaded = true;
    renderSchedule();
    return scheduleState.schedule;
  } catch (error) {
    scheduleState.loaded = false;
    var rows = $('schedule-rows');
    if (rows) {
      rows.innerHTML = '';
      rows.setAttribute('data-sig', '');
    }
    var now = $('schedule-now');
    if (now) {
      now.classList.remove('is-idle');
      now.textContent = '读取排程失败：' + pluginErrorMessage(error);
    }
    return null;
  }
}

/** 保存排程。启用状态下后端会立刻按当前时段切一次，所以这里要顺带回读调色板。 */
async function saveSchedule() {
  if (state.busy) return;
  var zones = collectScheduleZones('schedule-rows');
  var weekendBox = $('schedule-weekend');
  var useWeekend = !!(weekendBox && weekendBox.checked);
  // 不勾「周末用另一套」就发空表：后端据此认为周末沿用工作日那张表。
  var weekend = useWeekend ? collectScheduleZones('schedule-rows-weekend') : {};
  var enabledBox = $('schedule-enabled');
  var wanted = !!(enabledBox && enabledBox.checked);
  setBusy(true, '正在保存自动换主题…');
  try {
    var result = await callPlugin('save_schedule', {
      enabled: wanted,
      zones: zones,
      weekend_zones: weekend
    });
    if (result && result.schedule) scheduleState.schedule = result.schedule;
    if (result && result.now) scheduleState.now = result.now;
    scheduleState.weekendEnabled = scheduleWeekendHasValues(result && result.schedule);
    // 存盘成功 = 面板里的选择就是磁盘上的选择，dirty 清零。
    clearScheduleDirty();
    renderSchedule();
    var applied = result && result.applied;
    if (applied && applied.action === 'applied') {
      // 后端已经换过主题了：面板里的编辑器还显示旧的，必须回读，否则用户
      // 一按「应用」就把自动切换的结果覆盖回去。
      await reloadPalette({ silent: true });
      showToast('已保存，并切换到「' + String((applied.theme && applied.theme.name) || applied.theme_id) + '」', 'ok');
      setStatus('自动换主题已生效', 'ok');
    } else if (!wanted) {
      showToast('已保存（自动换主题已关闭）', 'ok');
      setStatus('自动换主题已关闭', 'ok');
    } else {
      showToast('已保存；这个时段还没选主题', 'ok');
      setStatus('自动换主题已保存', 'ok');
    }
  } catch (error) {
    showNotice('error', '保存自动换主题失败：' + pluginErrorMessage(error));
    showToast('保存失败', 'error');
    setStatus('保存自动换主题失败', 'error');
  } finally {
    setBusy(false);
  }
}

/* ============================================================================
 * 18. 外部改动感知（v0.4.14）
 *
 * 面板不是唯一的写入方：按时段自动换主题的后台线程、开着面板的其它窗口、
 * 命令行/脚本都会改 theme.json。面板只在打开时 get_theme 一次，之后一直显示
 * 打开那一刻的旧值 —— 用户看到的主题名、颜色、可读性自检全是过期的，
 * 而且点「应用」时提交的是基于旧值编辑出来的差量。
 *
 * 对策：定时轻量轮询 theme.json（纯 GET，不走 entry 调度，没有校验/写锁开销），
 * 比对 updated_at + 两组变量数。只有「面板没有未提交改动」时才自动跟随；
 * 有未提交改动时只提示、绝不覆盖。
 * ==========================================================================*/

var EXTERNAL_SYNC_INTERVAL_MS = 5000;
var EXTERNAL_SYNC_START_DELAY_MS = 2500;
var EXTERNAL_SYNC_TIMEOUT_MS = 4000;

var externalSyncState = {
  timer: null,
  inFlight: false,
  lastSignature: '',   // 上次「面板已知同步」的签名
  pending: null,       // 已提示过、尚未跟随的外部改动签名
  failures: 0,
  warned: false,       // 连续失败提示只弹一次（恢复后重置）
  materialsDeferred: false, // 上一次回读因材质草稿而没有同步材质
  lastCheckAt: 0
};

/** FNV-1a 32 位 —— 同步、无需随机源，只用来判「内容变没变」。 */
function hashText(text) {
  var hash = 0x811c9dc5;
  for (var i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i) & 0xff;
    hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
  }
  return ('00000000' + hash.toString(16)).slice(-8);
}

/** 调色板的稳定签名：按**内容**算。
 *
 *  旧实现是 updated_at + 键数：只有「手改了一个已有变量的值、又没动
 *  updated_at」时完全感知不到 —— 而这恰恰是这个功能存在的理由（面板不是
 *  唯一写入方：按时段自动换主题、CLI、别的窗口都会改 theme.json）。
 *  96×2 条键值算一遍 FNV 是微秒级，5 秒一次完全没有开销问题。 */
function paletteSignature(p) {
  if (!p || typeof p !== 'object') return '';
  function pairBlock(map) {
    if (!map || typeof map !== 'object') return '';
    return Object.keys(map).sort().map(function (key) {
      return key + '\u0000' + String(map[key]);
    }).join('\u0001');
  }
  return hashText([
    String(p.name || ''),
    p.enabled === false ? '0' : '1',
    pairBlock(p.vars),
    pairBlock(p.dark_vars),
    // 材质只在别的窗口里改动时，也要能被感知到。
    materialsSignatureText(p.materials)
  ].join('\u0002'));
}

function materialsSignatureText(materials) {
  if (!materials || typeof materials !== 'object') return '';
  try { return JSON.stringify(materials); } catch (e) { return ''; }
}

/** 材质中心有未应用的改动时算 1 项待应用：外部同步不能把它覆盖掉。 */
function materialsDirtyCount() {
  var api = window.NekoMaterials;
  if (!api || typeof api.isDirty !== 'function') return 0;
  try { return api.isDirty() ? 1 : 0; } catch (e) { return 0; }
}

/** 是否应该自动跟随这次外部改动。纯函数，自测里直接断言。 */
function externalSyncDecision(signature, state) {
  var opts = state || {};
  if (!signature) return { apply: false, reason: 'empty' };
  if (signature === opts.lastSignature) return { apply: false, reason: 'same' };
  if (opts.busy) return { apply: false, reason: 'busy' };
  if (opts.dirty > 0) return { apply: false, reason: 'dirty' };
  if (opts.modalOpen) return { apply: false, reason: 'modal' };
  return { apply: true, reason: 'ok' };
}

/** 面板当前是否「干净到可以自动跟随」。 */
function externalSyncBlockers() {
  var modal = $('modal');
  return {
    busy: !!state.busy,
    dirty: pendingChangeCount() + materialsDirtyCount(),
    modalOpen: !!(modal && !modal.hidden)
  };
}

/** 提示「磁盘变了但面板有你的改动，没敢自动覆盖」。同一签名只提示一次。 */
function warnExternalChangeBlocked() {
  var blockers = externalSyncBlockers();
  var why = [];
  if (blockers.dirty) why.push('面板里有 ' + blockers.dirty + ' 项还没应用的改动');
  if (blockers.modalOpen) why.push('正在编辑弹窗内容');
  if (blockers.busy) why.push('正在处理上一次操作');
  showNotice('warn',
    '磁盘上的主题被外部改动了（可能是按时段自动换主题，或别的窗口）。\n' +
    '面板里' + (why.length ? why.join('、') : '有未完成的操作') +
    '，为了不覆盖你的改动，没有自动刷新。\n' +
    '想现在跟上：先应用或「还原本组」清掉面板里的改动；想放弃面板改动直接跟上，就点「重新读取」。');
  showToast('磁盘主题已变化，未自动覆盖');
}

/** 外部同步连续失败要说出来：静默坏掉的功能等于没有。 */
function noteExternalSyncFailure() {
  externalSyncState.failures += 1;
  if (externalSyncState.failures < 3 || externalSyncState.warned) return;
  externalSyncState.warned = true;
  showNotice('warn', '面板已经连续 ' + externalSyncState.failures
    + ' 次读不到 theme.json，「外部改动自动跟随」暂时不可用。\n'
    + '你仍然可以手动编辑；如果一直这样，点「运行状态」看看主题服务是否还正常。');
  setStatus('外部改动感知不可用', 'warn');
}

/** 轮询一次：跟着磁盘走，把面板刷新到一致。失败会累计，连续 3 次后提示一次。 */
async function checkExternalChange() {
  if (externalSyncState.inFlight) return false;
  externalSyncState.inFlight = true;
  externalSyncState.lastCheckAt = Date.now();
  try {
    var response = await fetchWithTimeout(API_BASE + '/theme.json?_=' + Date.now(),
      { cache: 'no-store', credentials: 'same-origin' }, EXTERNAL_SYNC_TIMEOUT_MS);
    if (!response.ok) { noteExternalSyncFailure(); return false; }
    var raw = await response.json();
    externalSyncState.failures = 0;
    externalSyncState.warned = false;
    var signature = paletteSignature(raw);
    var blockers = externalSyncBlockers();
    var decision = externalSyncDecision(signature, {
      lastSignature: externalSyncState.lastSignature,
      busy: blockers.busy,
      dirty: blockers.dirty,
      modalOpen: blockers.modalOpen
    });
    if (!decision.apply) {
      if (decision.reason === 'dirty' || decision.reason === 'modal') {
        if (externalSyncState.pending !== signature) {
          externalSyncState.pending = signature;
          warnExternalChangeBlocked();
        }
      }
      return false;
    }
    externalSyncState.pending = null;
    var palette = await reloadPalette({ silent: true, keepMaterialDrafts: true });
    if (!palette) return false;
    if (externalSyncState.materialsDeferred) {
      // 回读期间出现了材质草稿：保留草稿、不推进基线，按「有未应用改动」提示一次。
      if (externalSyncState.pending !== signature) {
        externalSyncState.pending = signature;
        warnExternalChangeBlocked();
      }
      return false;
    }
    // 用后端真值重新确立签名：GET theme.json 与 get_theme 之间可能又被改过，
    // 拿 GET 的签名当真会让下一轮立刻误报「又变了」。
    externalSyncState.lastSignature = paletteSignature(palette);
    showToast('已同步外部改动：' + (palette.name || '未命名'));
    setStatus('已同步外部改动', 'ok');
    // 「运行状态」卡片读的是 studio_status（后端的 theme.json + 窗口上报）。外部改动
    // 跟过来时只刷编辑区不刷这张卡，卡片就会一直写着上一次的主题名 —— 界面已经是
    // 默认外观、面板却还说「上次那套主题已生效」，正是用户报的那个现象。
    refreshHealth({});
    return true;
  } catch (error) {
    noteExternalSyncFailure();
    return false;
  } finally {
    externalSyncState.inFlight = false;
  }
}

function startExternalSync() {
  stopExternalSync();
  externalSyncState.timer = setInterval(function () {
    // 面板在后台时不轮询：省电，也避免切回来时连发几次。
    if (document.visibilityState === 'hidden') return;
    checkExternalChange();
  }, EXTERNAL_SYNC_INTERVAL_MS);
}

function stopExternalSync() {
  if (externalSyncState.timer) {
    clearInterval(externalSyncState.timer);
    externalSyncState.timer = null;
  }
}

function revertMode() {
  var mode = state.mode;
  var n = Object.keys(state.edited[mode]).length + Object.keys(state.cleared[mode]).length;
  if (!n) { showToast('当前' + (mode === 'dark' ? '暗色' : '亮色') + '组没有改动'); return; }
  state.edited[mode] = {};
  state.cleared[mode] = {};
  syncAll();
  showToast('已还原' + (mode === 'dark' ? '暗色' : '亮色') + '组改动', 'ok');
}

async function reloadPalette(options) {
  var silent = options && options.silent;
  // discardEdits：用户点「重新读取」时明确表示放弃面板里的改动。不清脏的话
  // dirtyCount 永远 > 0，外部同步被永久拦断且不再提示，面板看着像卡住了；
  // 更糟的是再点「应用」会把用户以为已放弃的值提交上去。
  var discardEdits = !!(options && options.discardEdits);
  var keepMaterialDrafts = !!(options && options.keepMaterialDrafts);
  setLoading(true);
  try {
    var palette = await callPlugin('get_theme', {});
    // 外部同步路径：等待 get_theme 期间用户可能刚改了材质（进度条不拦编辑）。
    // 这时不能用磁盘值覆盖材质草稿，也不推进签名基线，下一轮轮询再试。
    var materialsDeferred = keepMaterialDrafts && materialsDirtyCount() > 0;
    externalSyncState.materialsDeferred = materialsDeferred;
    state.palette = palette;
    if (discardEdits) {
      state.edited = { light: {}, dark: {} };
      state.cleared = { light: {}, dark: {} };
      markAllRowsValid();
      clearRgbMemory();
      // 「重新读取」既然丢弃了未应用的改动，撤销栈也要一起丢掉：否则再按 Ctrl+Z
      // 会把刚丢弃的改动又还原回来，用户看到的是「撤销竟然把不要的东西弄回来了」。
      undoState.past.length = 0;
      undoState.future.length = 0;
      updateUndoButtons();
    }
    // 基线签名跟着更新：否则刚读完就轮到外部同步，会把同一份数据误判成「又变了」。
    if (!materialsDeferred) {
      externalSyncState.lastSignature = paletteSignature(palette);
      externalSyncState.pending = null;
    } else {
      // 基线作废（而不是保留旧签名）：草稿被撤销或应用后，下一轮轮询无论磁盘
      // 此时是新值还是又回到了旧值，都会被判为「需要同步」并把面板拉回磁盘。
      externalSyncState.lastSignature = '';
    }
    renderMeta();
    renderPaletteHint();
    // 材质子树跟着磁盘刷新：导入/重置/应用之后，材质中心的「已保存」基线
    // 必须一起更新，否则它一直显示「N 项待应用」。
    // 保存路径会带上提交时的材质快照：等待期间又改的材质只更新基线、不被覆盖。
    var materialsSyncOptions = (options && Object.prototype.hasOwnProperty.call(options, 'materialsSubmitted'))
      ? { submitted: options.materialsSubmitted }
      : undefined;
    if (!materialsDeferred) syncMaterials(palette, materialsSyncOptions);
    syncAll();
    applyFilters();
    renderAudit(true);
    // 读一次磁盘就顺带把「运行状态」刷新一遍：这张卡片说「当前主题/窗口有没有应用」，
    // 它跟编辑区必须来自同一次读取，否则会出现「编辑区已经是默认、卡片还写着上次那套」。
    refreshHealth({});
    var c = paletteCounts(palette);
    if (!silent) {
      showNotice('ok', '已读取当前主题：亮色 ' + c.light +
        ' 项 / 暗色 ' + c.dark + ' 项' + savedModeHint(c.light, c.dark));
      // 状态栏常驻显示两条腿各有多少项，避免「只配了一组」被忽略。
      setStatus('已读取 · 亮色 ' + c.light + ' / 暗色 ' + c.dark, 'ok');
    }
    return palette;
  } catch (error) {
    if (!silent) {
      showNotice('error', '读取失败：' + pluginErrorMessage(error) + '\n（后端主题服务可能没响应；面板仍可编辑，点击「应用」会重试。）');
      setStatus('读取失败', 'error');
    }
    // 读取失败也不能让面板变成空白：用当前编辑状态继续渲染。
    renderPaletteHint();
    syncAll();
    return null;
  } finally {
    setLoading(false);
  }
}

/** busy 期间锁住行内控件：否则飞行中的编辑会在成功后随 state.edited 清空而静默丢失。 */
function setRowControlsDisabled(busy) {
  // 主题名/启用开关也是一次性提交的编辑目标：busy 期间不禁用，回读时敲的字会被覆盖。
  ['theme-name', 'theme-enabled'].forEach(function (id) {
    var node = $(id);
    if (node) node.disabled = !!busy;
  });
  Object.keys(rowsByKey).forEach(function (key) {
    var ref = rowsByKey[key];
    // 图片行的「选择图片 / 清除」、开关与下拉也要一起禁用：busy 期间它们
    // 还能点，就会和后端正在进行的写入并发（例如两张背景图同时 import）。
    ['hexInput', 'colorInput', 'alpha', 'textInput', 'clearBtn',
      'gradAngle', 'gradFrom', 'gradTo', 'gradGen',
      'pickBtn', 'clearImageBtn', 'switchInput', 'selectInput'].forEach(function (name) {
      if (ref[name]) ref[name].disabled = !!busy;
    });
    // 解锁时不能把 alpha 一律启用：transparent / inherit / 空 的行必须继续置灰，
    // 否则应用一次之后滑块又能拖出纯黑半透明（旧实现只在 syncRow 里设 disabled，
    // 被这里一次性覆盖掉）。但若有 RGB 记忆（拖过颜色/选过色），transparent
    // 也要保持可用 —— 拖动会沿用记忆里的颜色，见 lastRgbByKey。
    if (!busy && ref.alpha) {
      var parsed = parseColor(displayValue(key));
      var transparent = !!parsed && parsed.a === 0;
      ref.alpha.disabled = !parsed || (transparent && !lastRgbByKey[key]);
    }
  });
}

function setBusy(busy, message) {
  state.busy = busy;
  updateButtons();
  setRowControlsDisabled(busy);
  if (busy && message) setStatus(message, 'busy');
}

/* -------------------------------------------------------------------------- *
 * 10e. 运行状态（status card）
 *
 * 插件时代这张卡片叫「安装自检」：主题要生效，插件必须把 loader 两件套与模板
 * 标记写进 N.E.K.O 的安装目录，于是需要「一键修复 / 卸载 / 重新接入」三连。
 * 内置之后这层动作在设计上就不存在了 —— 模板自己 include 静态资源，没有可以
 * 「装错」的位置。卡片改成回答另一个问题：主题到底有没有真的贴到界面上。
 *
 * 数据来自后端 studio_status：数据目录、当前主题、仓库/背景图/内置主题数量，
 * 以及聊天窗口里的加载器通过 loader_report 回传的运行时诊断（后端落在
 * data_dir/diagnostics.json，保留最近 20 次）。
 * -------------------------------------------------------------------------- */

var HELP_TOPICS = [
  {
    title: '主题装上了但界面完全没变',
    body: '按顺序试：①确认在面板里选过颜色并点了「应用」；②彻底关掉 N.E.K.O 再重开'
      + '（已打开的窗口不会自己重载）；③看「应用侧上报」这一行 —— 一直没有上报，'
      + '说明聊天窗口还没被打开过。'
  },
  {
    title: '为什么每次改完都要点「应用」',
    body: '面板上的值只是草稿，点「应用」才会写进数据目录里的 theme.json；'
      + '聊天窗口每 1.5 秒读一次它，所以应用之后一般秒级生效。'
  },
  {
    title: '亮色 / 暗色是两套独立配色',
    body: '面板顶部的「亮色 / 暗色」互相独立。只改了暗色组时，本体处在亮色模式下当然看不到变化。'
  },
  {
    title: '主题数据存在哪',
    body: '点这张卡片上的「复制数据目录」：theme.json（当前主题）、themes/（主题仓库）、'
      + 'backgrounds/（背景图）、schedule.json（按时段自动换主题）、diagnostics.json（加载器上报）。'
  },
  {
    title: '换机器 / 重装怎么办',
    body: '面板底部可以「导出」分享包（zip，含背景图）。新机器上导入即可；'
      + '整个主题仓库也能一次导出，不用一棵树一棵树地存。'
  },
  {
    title: '背景图为什么没显示',
    body: '背景图由本体后端从数据目录的 backgrounds/ 提供。上传后如果面板预览正常、'
      + '聊天窗口却没变，先确认「应用侧上报」里没有报错、当前主题是启用状态。'
  }
];

function openHelpModal() {
  var lines = [];
  for (var i = 0; i < HELP_TOPICS.length; i += 1) {
    lines.push((i + 1) + '. ' + HELP_TOPICS[i].title);
    lines.push('   ' + HELP_TOPICS[i].body);
    lines.push('');
  }
  openModal('help', '主题没生效？',
    '这是主题工坊最常见的问题清单。改完关掉 N.E.K.O 再重开，通常就能看到变化。',
    lines.join('\n'));
}

function healthItem(key, value, level) {
  return { key: key, value: value, level: level || 'ok' };
}

function renderHealth(info) {
  var stateNode = $('health-state');
  var listNode = $('health-list');
  if (!stateNode || !listNode) return;
  while (listNode.firstChild) listNode.removeChild(listNode.firstChild);

  if (!info || typeof info !== 'object') {
    stateNode.textContent = '读取失败：拿不到主题服务状态';
    stateNode.className = 'health-line is-bad';
    return;
  }

  var items = [];
  var level = 'ok';

  items.push(healthItem('数据目录', String(info.data_dir || '未知'), 'ok'));

  // 「停用」不等于「没选主题」：窗口看到的是出厂默认，而 theme.json 里还留着
  // 上一次那套名字。两件事必须分开说，否则「界面是默认的、面板写着上次那套」
  // 读起来就像主题丢了。
  var themeName = String(info.theme_name || DEFAULT_THEME_NAME);
  var currentText = info.theme_enabled
    ? themeName + '（启用中）' + (info.theme_updated_at ? ' · ' + String(info.theme_updated_at) : '')
    : '出厂默认配色（主题「' + themeName + '」已停用，打开启用开关再点「应用」）';
  items.push(healthItem('当前主题', currentText, info.theme_enabled ? 'ok' : 'warn'));
  if (!info.theme_enabled && level === 'ok') level = 'warn';

  var light = Number(info.light_vars) || 0;
  var dark = Number(info.dark_vars) || 0;
  if (light + dark === 0) {
    if (level === 'ok') level = 'warn';
    items.push(healthItem('配色变量', '还是出厂空主题 —— 选好颜色点「应用」才会生效', 'warn'));
  } else {
    items.push(healthItem('配色变量', '亮色 ' + light + ' 项 · 暗色 ' + dark + ' 项', 'ok'));
  }

  items.push(healthItem('主题仓库', (Number(info.library_count) || 0) + ' 套自定义 · '
    + (Number(info.preset_count) || 0) + ' 套内置', 'ok'));
  items.push(healthItem('背景图', (Number(info.background_count) || 0) + ' 张', 'ok'));

  var loaderVersion = String(info.loader_version || '');
  if (loaderVersion) {
    items.push(healthItem('加载器资源', 'v' + loaderVersion
      + (info.css_sha8 ? ' · css ' + String(info.css_sha8) : ''), 'ok'));
  } else {
    if (level === 'ok') level = 'warn';
    items.push(healthItem('加载器资源', '读不到 static/theme-studio/loader/ 下的两件套', 'warn'));
  }

  var diag = info.diagnostics;
  if (diag && typeof diag === 'object') {
    var page = String(diag.page || '未知页面');
    var when = String(diag.received_at || '');
    items.push(healthItem('应用侧上报', page + (when ? ' · ' + when : '')
      + ' · nts 变量 ' + (Number(diag.nts_vars) || 0)
      + ' · 样式规则 ' + (Number(diag.rules) || 0), 'ok'));
    if (diag.enabled === false) {
      level = 'bad';
      items.push(healthItem('加载器状态', '上报说主题是停用的 —— 打开面板的启用开关再点「应用」', 'bad'));
    }
    var diagErrors = diag.errors || [];
    for (var e = 0; e < diagErrors.length && e < 5; e += 1) {
      level = 'bad';
      items.push(healthItem('加载器报错', String(diagErrors[e]), 'bad'));
    }
    // 上报内容必须和 theme.json 对得上：theme.json 是「后端认为该用的」，上报是
    // 「窗口里真用上的」。两者不一致时，面板只能如实报「窗口还没应用这次改动」，
    // 不能拿 theme.json 的名字替窗口宣布「已生效」。
    var themeVars = light + dark;
    var reportedVars = Number(diag.nts_vars) || 0;
    var reportedRules = Number(diag.rules) || 0;
    if (info.theme_enabled && themeVars > 0 && reportedVars === 0) {
      level = 'bad';
      items.push(healthItem('窗口对不上',
        '上报的窗口 ' + page + ' 一个变量都没应用 —— 那个窗口还是默认外观，刷新它试试', 'bad'));
    }
    if (reportedVars > 0 && reportedRules === 0) {
      level = 'bad';
      items.push(healthItem('样式未生效',
        '上报说注入了 ' + reportedVars + ' 个变量，但样式规则数为 0 —— 注入的 <style> 已被清理', 'bad'));
    }
    if (themeVars === 0 && reportedVars > 0) {
      // 反向不一致：theme.json 已经改回空主题，窗口还挂着上一次的样式。窗口 1.5s 内
      // 就会自己跟上，这里说出来只是让「刚重置完立刻看面板」的人知道是过渡态。
      if (level === 'ok') level = 'warn';
      items.push(healthItem('窗口滞后一拍',
        'theme.json 已经是空主题，窗口上报的还是上一次的 ' + reportedVars + ' 个变量 —— 1.5 秒内会自己跟上', 'warn'));
    }
  } else {
    if (level === 'ok') level = 'warn';
    items.push(healthItem('应用侧上报', '还没有收到上报 —— 打开一次聊天窗口后就会有', 'warn'));
  }

  for (var i = 0; i < items.length; i += 1) {
    var li = el('li', { class: items[i].level === 'ok' ? '' : 'is-' + items[i].level });
    li.appendChild(el('span', { class: 'hk', text: items[i].key + '：' }));
    li.appendChild(el('span', { class: 'hv', text: items[i].value }));
    listNode.appendChild(li);
  }

  if (level === 'ok') {
    stateNode.textContent = '一切正常：主题已经生效';
  } else if (level === 'warn') {
    stateNode.textContent = '主题服务正常，但有几处需要注意';
  } else {
    stateNode.textContent = '有问题：主题可能没有生效';
  }
  stateNode.className = 'health-line is-' + level;
}

function refreshHealth(options) {
  var opts = options || {};
  return callPlugin('studio_status', {})
    .then(function (result) {
      state.status = result || {};
      renderHealth(state.status);
      if (opts.toast) showToast('运行状态已刷新', 'ok');
      return state.status;
    })
    .catch(function (error) {
      state.status = null;
      var stateNode = $('health-state');
      if (stateNode) {
        stateNode.textContent = '读取失败：' + pluginErrorMessage(error);
        stateNode.className = 'health-line is-bad';
      }
      if (opts.toast) showToast('读取失败', 'error');
      return null;
    });
}

/**
 * 应用成功后隔一会儿复核一次「运行状态」。
 * 窗口 1.5s 内就轮到新主题，应用成功时还会立刻上报一次；因此 2.5s 后读到的
 * 运行状态足以判断「theme.json 改了、窗口也真用上了」。对不上就明说，不让
 * 「面板说已切换、界面还是默认」这种沉默的不一致留到用户自己发现。
 */
function verifyAppliedShortly() {
  window.setTimeout(function () {
    refreshHealth({}).then(function (info) {
      if (!info) return;
      var stateNode = $('health-state');
      var cls = stateNode ? String(stateNode.className || '') : '';
      if (cls.indexOf('is-bad') !== -1) {
        showNotice('warn', '主题已写进 theme.json，但窗口那边还没应用上 —— '
          + '看「运行状态」卡片里的「窗口对不上 / 样式未生效」，刷新那个窗口或重启应用即可。');
      }
    });
  }, 2500);
}

/** 复制数据目录：排查问题时可以直接贴给会看日志的人。 */
async function copyStatusPath() {
  var info = state.status;
  if (!info || !info.data_dir) {
    showToast('还没有读到数据目录', 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(String(info.data_dir));
    showToast('数据目录已复制', 'ok');
  } catch (error) {
    showNotice('info', '数据目录：' + String(info.data_dir));
  }
}/* ============================================================================
 * 11. 弹窗
 * ==========================================================================*/

function openModal(mode, title, hint, text) {
  state.modalMode = mode;
  // 记住打开弹窗前焦点在哪，关闭后还回去（否则键盘用户会掉回页面顶部）
  state.modalReturnFocus = document.activeElement;
  $('modal-title').textContent = title;
  $('modal-hint').textContent = hint;
  var isMeta = mode === 'meta';
  var area = $('modal-text');
  var fields = $('modal-fields');
  if (area) {
    area.style.display = isMeta ? 'none' : '';
    area.value = isMeta ? '' : (text || '');
    area.readOnly = mode === 'export' || mode === 'help';
    if (mode === 'export') area.select();
  }
  if (fields) fields.style.display = isMeta ? '' : 'none';
  $('modal-ok').textContent = mode === 'export' ? '复制并关闭' : (isMeta ? '保存' : '确定');
  $('modal').hidden = false;
  area.focus();
}

function closeModal() {
  var modal = $('modal');
  if (modal) modal.hidden = true;
  var back = state.modalReturnFocus;
  state.modalReturnFocus = null;
  if (back && typeof back.focus === 'function') {
    try { back.focus(); } catch (e) { /* 元素可能已被替换，忽略 */ }
  }
}

/** 弹窗打开时把 Tab 圈在面板内，避免焦点跑到背后的行编辑器上。 */
function trapModalFocus(event) {
  var panel = $('modal-panel') || $('modal');
  if (!panel) return;
  var nodes = panel.querySelectorAll('button, textarea, input, [tabindex]:not([tabindex="-1"])');
  var list = Array.prototype.filter.call(nodes, function (node) {
    return !node.disabled && node.offsetParent !== null;
  });
  if (list.length < 2) return;
  var first = list[0];
  var last = list[list.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return Promise.reject(new Error('clipboard unavailable'));
}

function onModalOk() {
  if (state.modalMode === 'meta') { saveThemeMeta(); return; }
  if (state.modalMode === 'export') {
    var area = $('modal-text');
    area.select();
    // 复制失败就别关弹窗：关掉之后用户既没复制成功、也看不到那段文本了。
    copyText(area.value).then(function () {
      showToast('已复制到剪贴板', 'ok');
      closeModal();
    }).catch(function () {
      if (area && area.focus) area.focus();
      if (area && area.select) area.select();
      showToast('复制失败，文本已选中 —— 按 Ctrl+C 手动复制');
    });
    return;
  }
  // 帮助弹窗只是说明文字：「确定」只关闭，绝不能当成调色板 JSON 去导入。
  if (state.modalMode !== 'import') { closeModal(); return; }
  doImport();
}

/** 复制调色板 dump（含未应用改动），方便贴 issue / 对比。 */
function copyDump() {
  // 折叠状态下 JSON 可能是旧的（折叠期间只标脏），复制前强制重建一次，
  // 保证「复制到的就是当前看到的调色板」。
  renderDump(true);
  copyText($('dump').textContent || '').then(function () {
    showToast('调色板 JSON 已复制', 'ok');
  }).catch(function () {
    showToast('复制失败，请手动选择文本');
  });
}

/* ============================================================================
 * 12. 引导
 * ==========================================================================*/

function isTypingTarget(node) {
  if (!node || !node.tagName) return false;
  var tag = node.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || node.isContentEditable === true;
}

/** 搜索结果里第一个可见行的可编辑控件 —— 让键盘用户按回车能直接开始改。 */
function firstVisibleRow() {
  var wrap = document.querySelector('.row-wrap:not([hidden])');
  if (!wrap) return null;
  // 只返回可键入的输入框：色块是 type=color，聚焦它既不能打字、也不会弹出色板
  // （浏览器只在点击时弹），键盘用户按回车会卡在那里。
  return wrap.querySelector('input.hex, input.raw');
}

/* ===== FEATURES_28_BEGIN（v0.5.0：拖拽导入 + 导出 CSS 片段）===== */

/**
 * 把文件拖到面板上直接导入（F7）。
 *
 * 为什么绑在 document.body 上而不是某张卡：用户拖文件时手是奔着「面板」来的，
 * 落在哪张卡上不应该决定能不能导入。用 dragenter/dragleave 的计数抵消子元素
 * 冒泡（进一个子元素会先 leave 再 enter），避免高亮闪烁。
 */
var dropDepth = 0;

function dropHasFiles(dt) {
  if (!dt || !dt.types) return false;
  for (var i = 0; i < dt.types.length; i += 1) {
    if (dt.types[i] === 'Files') return true;
  }
  return false;
}

function dropReset() {
  dropDepth = 0;
  if (document.body) document.body.classList.remove('is-drop-target');
}

function initDropImport() {
  if (!document.body || document.body.getAttribute('data-drop-import') === '1') return;
  document.body.setAttribute('data-drop-import', '1');

  document.body.addEventListener('dragenter', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    dropDepth += 1;
    document.body.classList.add('is-drop-target');
  });

  document.body.addEventListener('dragover', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = 'copy'; } catch (err) { /* 有些环境是只读的 */ }
  });

  document.body.addEventListener('dragleave', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    dropDepth -= 1;
    if (dropDepth <= 0) dropReset();
  });

  document.body.addEventListener('drop', function (e) {
    if (!dropHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    dropReset();
    var files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) return;
    if (files.length > 1) showToast('一次只能导入一个文件，已用第一个', 'warn');
    var file = files[0];
    var lower = String(file.name || '').toLowerCase();
    if (lower.slice(-4) !== '.zip' && lower.slice(-5) !== '.json') {
      showNotice('error', '只支持拖入 .zip 分享包或 .json 主题文件。');
      showToast('文件类型不支持', 'error');
      return;
    }
    if (state.busy) {
      showToast('正在忙，等这一步做完再拖', 'warn');
      return;
    }
    showNotice('info', '正在读取「' + file.name + '」…');
    handleArchiveFile(file, null);
  });

  /* 拖到面板外面时浏览器会直接打开文件（丢掉当前面板状态），一律拦掉。 */
  ['dragover', 'drop'].forEach(function (type) {
    window.addEventListener(type, function (e) {
      if (dropHasFiles(e.dataTransfer)) e.preventDefault();
    });
  });
}

/**
 * 把当前配色导出成 CSS 变量片段（F8）。
 *
 * 亮色写 `:root`、暗色写 `:root[data-theme="dark"]`，与宿主/插件自己的约定一致，
 * 用户把这段贴进任意页面就能得到同一套配色。空调色板返回空串（不产生空壳文件）。
 */
function cssSnippetText() {
  var light = effectiveVars('light');
  var dark = effectiveVars('dark');
  var palette = state.palette || {};
  var name = palette.name || '未命名主题';
  var lines = [];
  var today = new Date();
  var stamp = today.getFullYear() + '-'
    + ('0' + (today.getMonth() + 1)).slice(-2) + '-'
    + ('0' + today.getDate()).slice(-2);

  function pushBlock(selector, map, label) {
    var keys = Object.keys(map || {}).sort();
    if (!keys.length) return;
    lines.push('/* ' + label + ' */');
    lines.push(selector + ' {');
    for (var i = 0; i < keys.length; i += 1) {
      lines.push('  ' + keys[i] + ': ' + map[keys[i]] + ';');
    }
    lines.push('}');
    lines.push('');
  }

  pushBlock(':root', light, name + ' · 亮色');
  pushBlock(':root[data-theme="dark"]', dark, name + ' · 暗色');
  if (!lines.length) return '';
  lines.unshift('/* ' + name + ' —— 由 N.E.K.O 主题工作室导出（' + stamp + '） */', '');
  return lines.join('\n');
}

function copyCssSnippet() {
  var text = cssSnippetText();
  if (!text) {
    showToast('当前调色板是空的，没有可导出的变量', 'warn');
    return;
  }
  openModal(
    'export',
    'CSS 变量片段 · ' + ((state.palette && state.palette.name) || '当前配色'),
    '这段 CSS 可以直接贴到别处使用：亮色在 :root，暗色在 :root[data-theme="dark"]。'
      + '变量名就是面板里那一列，颜色值已经按当前改动算好（不需要先「应用」）。',
    text
  );
}

/* ===== FEATURES_28_END ===== */

/* ===== WALLPAPER_BEGIN（v0.11.0：壁纸取色 + 变体生成）===== */

var WALLPAPER_SAMPLE = 96;
var WALLPAPER_MAX_FILE = 12 * 1024 * 1024;
var VARIANT_STEPS = [
  { id: 'warm', label: '更暖', dh: 14, ds: 0.06, dl: -0.02 },
  { id: 'cool', label: '更冷', dh: -14, ds: 0.06, dl: -0.02 },
  { id: 'bright', label: '更亮', dh: 0, ds: -0.04, dl: 0.10 },
  { id: 'deep', label: '更深', dh: 0, ds: 0.04, dl: -0.10 }
];
var VARIANT_SURFACES = [
  '--nts-chat-bg', '--nts-chat-panel-bg', '--nts-user-bubble-bg', '--nts-ai-bubble-bg',
  '--nts-input-bg', '--nts-button-bg', '--nts-send-bg', '--nts-panel-bg',
  '--nts-popover-bg', '--nts-modal-bg', '--nts-rec-bg', '--nts-chip-bg',
  '--nts-topbar-bg', '--nts-accent-bg', '--nts-code-bg', '--nts-tool-bubble-bg',
  '--nts-status-bg', '--nts-overlay-bg'
];
var VARIANT_TEXT_PAIRS = [
  ['--nts-user-bubble-text', '--nts-user-bubble-bg'],
  ['--nts-ai-bubble-text', '--nts-ai-bubble-bg'],
  ['--nts-input-text', '--nts-input-bg'],
  ['--nts-button-text', '--nts-button-bg'],
  ['--nts-send-text', '--nts-send-bg'],
  ['--nts-panel-text', '--nts-panel-bg'],
  ['--nts-popover-text', '--nts-popover-bg'],
  ['--nts-modal-text', '--nts-modal-bg'],
  ['--nts-rec-text', '--nts-rec-bg'],
  ['--nts-chip-text', '--nts-chip-bg'],
  ['--nts-topbar-text', '--nts-topbar-bg'],
  ['--nts-code-text', '--nts-code-bg'],
  ['--nts-tool-bubble-text', '--nts-tool-bubble-bg'],
  ['--nts-status-text', '--nts-status-bg']
];
var variantState = { index: 0, lastLabel: '' };

function rgbToHsl(r, g, b) {
  var rr = Math.min(255, Math.max(0, r)) / 255;
  var gg = Math.min(255, Math.max(0, g)) / 255;
  var bb = Math.min(255, Math.max(0, b)) / 255;
  var max = Math.max(rr, gg, bb);
  var min = Math.min(rr, gg, bb);
  var l = (max + min) / 2;
  var h = 0;
  var s = 0;
  if (max !== min) {
    var d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rr) h = ((gg - bb) / d + (gg < bb ? 6 : 0)) / 6;
    else if (max === gg) h = ((bb - rr) / d + 2) / 6;
    else h = ((rr - gg) / d + 4) / 6;
  }
  return { h: h * 360, s: s, l: l };
}

/* 名字不能叫 hslToRgb：文件前面 parseColor 用的同名函数接收百分比、返回数组，
 * 函数声明会被这一份整体覆盖，结果输入 hsl(...) 颜色得到 rgba(NaN, ...)。
 * 这一份接收 0-1 的饱和度 / 亮度，返回 {r, g, b}，只给快捷调校与壁纸取色用。 */
function hslFractionToRgb(h, s, l) {
  var hh = ((h % 360) + 360) % 360 / 360;
  if (s === 0) {
    var gray = Math.round(l * 255);
    return { r: gray, g: gray, b: gray };
  }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var p = 2 * l - q;
  function channel(t) {
    var tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  }
  return {
    r: Math.round(channel(hh + 1 / 3) * 255),
    g: Math.round(channel(hh) * 255),
    b: Math.round(channel(hh - 1 / 3) * 255)
  };
}

function extractImageColors(pixels, width, height) {
  var buckets = Object.create(null);
  var total = Math.min(width * height, pixels.length / 4);
  for (var i = 0; i < total; i += 1) {
    var o = i * 4;
    if (pixels[o + 3] < 32) continue;
    var r = pixels[o];
    var g = pixels[o + 1];
    var b = pixels[o + 2];
    var key = (r >> 4) + ':' + (g >> 4) + ':' + (b >> 4);
    var bucket = buckets[key];
    if (!bucket) bucket = buckets[key] = { n: 0, r: 0, g: 0, b: 0 };
    bucket.n += 1;
    bucket.r += r;
    bucket.g += g;
    bucket.b += b;
  }
  var out = [];
  Object.keys(buckets).forEach(function (key) {
    var bucket = buckets[key];
    var color = {
      r: Math.round(bucket.r / bucket.n),
      g: Math.round(bucket.g / bucket.n),
      b: Math.round(bucket.b / bucket.n)
    };
    var hsl = rgbToHsl(color.r, color.g, color.b);
    var luma = (0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b) / 255;
    var usable = luma > 0.05 && luma < 0.95 ? 1 : 0.3;
    out.push({
      r: color.r,
      g: color.g,
      b: color.b,
      hex: colorToHex({ r: color.r, g: color.g, b: color.b, a: 1 }),
      count: bucket.n,
      hue: hsl.h,
      sat: hsl.s,
      luma: luma,
      score: bucket.n * (0.25 + hsl.s) * usable
    });
  });
  out.sort(function (a, b) { return b.score - a.score; });
  return out.slice(0, 8);
}

function wallpaperInk(bg, need) {
  var white = { r: 255, g: 255, b: 255, a: 1 };
  var black = { r: 12, g: 14, b: 18, a: 1 };
  var fixed = contrastFixColor(white, bg, need) || contrastFixColor(black, bg, need);
  // contrastFixColor 返回 { color: {r,g,b,a}, ratio, t, toward }，这里要转成 CSS 颜色字符串
  if (fixed && fixed.color) return colorToHex(fixed.color);
  return contrastRatio(white, bg) >= contrastRatio(black, bg) ? '#ffffff' : '#0c0e12';
}

function wallpaperPalette(colors, mode) {
  var dark = mode === 'dark';
  var primary = colors[0] || { r: 96, g: 138, b: 200 };
  var second = colors[1] || primary;
  var third = colors[2] || second;
  var white = { r: 255, g: 255, b: 255, a: 1 };
  var black = { r: 10, g: 12, b: 16, a: 1 };
  var bg = mixToward(primary, dark ? black : white, dark ? 0.86 : 0.90);
  var panel = mixToward(primary, dark ? black : white, dark ? 0.78 : 0.86);
  var popover = mixToward(second, dark ? black : white, dark ? 0.70 : 0.96);
  var bubbleUser = mixToward(primary, dark ? black : white, dark ? 0.28 : 0.16);
  var bubbleAi = mixToward(second, dark ? black : white, dark ? 0.74 : 0.92);
  var accent = mixToward(primary, white, dark ? 0.10 : 0);
  var border = mixToward(primary, dark ? white : black, dark ? 0.20 : 0.72);
  var ink = wallpaperInk(bg, 7.0);
  var inkSub = wallpaperInk(panel, 4.5);
  var inkUser = wallpaperInk(bubbleUser, 4.5);
  var inkAi = wallpaperInk(bubbleAi, 4.5);
  var codeBg = mixToward(third, dark ? black : white, dark ? 0.80 : 0.88);
  var out = {};
  out['--nts-chat-bg'] = colorToHex(bg);
  out['--nts-chat-panel-bg'] = colorToHex(panel);
  out['--nts-topbar-bg'] = colorToHex(mixToward(panel, bg, 0.5));
  out['--nts-topbar-text'] = colorToHex(mixToward(bubbleUser, dark ? white : black, dark ? 0.82 : 0.55));
  out['--nts-user-bubble-bg'] = colorToHex(bubbleUser);
  out['--nts-user-bubble-text'] = inkUser;
  out['--nts-user-bubble-border'] = colorToHex(border);
  out['--nts-ai-bubble-bg'] = colorToHex(bubbleAi);
  out['--nts-ai-bubble-text'] = inkAi;
  out['--nts-ai-bubble-border'] = colorToHex(mixToward(second, dark ? white : black, dark ? 0.18 : 0.76));
  out['--nts-code-bg'] = colorToHex(codeBg);
  out['--nts-code-text'] = wallpaperInk(codeBg, 4.5);
  out['--nts-input-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.06 : 0.5));
  out['--nts-input-text'] = inkSub;
  out['--nts-input-border'] = colorToHex(border);
  out['--nts-input-placeholder'] = colorToHex(mixToward(panel, dark ? white : black, dark ? 0.42 : 0.45));
  out['--nts-button-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.55 : 0.72));
  out['--nts-button-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.55 : 0.72), 4.5);
  out['--nts-button-hover-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.42 : 0.62));
  out['--nts-send-bg'] = colorToHex(accent);
  out['--nts-send-text'] = wallpaperInk(accent, 4.5);
  out['--nts-panel-bg'] = colorToHex(popover);
  out['--nts-panel-text'] = wallpaperInk(popover, 4.5);
  out['--nts-panel-border'] = colorToHex(border);
  out['--nts-panel-text-sub'] = colorToHex(mixToward(popover, dark ? white : black, dark ? 0.40 : 0.40));
  out['--nts-popover-bg'] = colorToHex(popover);
  out['--nts-popover-text'] = wallpaperInk(popover, 4.5);
  out['--nts-popover-border'] = colorToHex(border);
  out['--nts-modal-bg'] = colorToHex(mixToward(popover, dark ? black : white, dark ? 0.10 : 0.35));
  out['--nts-modal-text'] = wallpaperInk(mixToward(popover, dark ? black : white, dark ? 0.10 : 0.35), 4.5);
  out['--nts-overlay-bg'] = dark ? 'rgba(6, 8, 12, 0.62)' : 'rgba(18, 22, 30, 0.32)';
  out['--nts-accent'] = colorToHex(accent);
  out['--nts-accent-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.72 : 0.86));
  out['--nts-link'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.22 : 0.14));
  out['--nts-highlight'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.55 : 0.35));
  out['--nts-rec-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.04 : 0.42));
  out['--nts-rec-text'] = wallpaperInk(mixToward(panel, dark ? black : white, dark ? 0.04 : 0.42), 4.5);
  out['--nts-rec-border'] = colorToHex(border);
  out['--nts-chip-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.68 : 0.88));
  out['--nts-chip-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.68 : 0.88), 4.5);
  out['--nts-tool-bubble-bg'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.02 : 0.40));
  out['--nts-tool-bubble-text'] = wallpaperInk(mixToward(panel, dark ? black : white, dark ? 0.02 : 0.40), 4.5);
  out['--nts-status-bg'] = colorToHex(mixToward(accent, dark ? black : white, dark ? 0.74 : 0.90));
  out['--nts-status-text'] = wallpaperInk(mixToward(accent, dark ? black : white, dark ? 0.74 : 0.90), 4.5);
  out['--nts-scrollbar-track'] = colorToHex(mixToward(bg, dark ? white : black, dark ? 0.10 : 0.06));
  out['--nts-scrollbar-thumb'] = colorToHex(mixToward(accent, bg, 0.45));
  out['--nts-panel-glow-color'] = colorToHex(mixToward(accent, dark ? white : black, dark ? 0.35 : 0.20));
  out['--nts-panel-border-color'] = colorToHex(border);
  out['--nts-glass-tint'] = colorToHex(mixToward(panel, dark ? black : white, dark ? 0.12 : 0.35));
  out['--nts-glass-highlight'] = colorToHex(mixToward(accent, white, 0.62));
  out['--nts-bubble-glass-tint'] = colorToHex(mixToward(bubbleAi, dark ? black : white, dark ? 0.20 : 0.45));
  return out;
}

function shiftColorValue(value, step) {
  var color = parseColor(value);
  if (!color) return null;
  var hsl = rgbToHsl(color.r, color.g, color.b);
  var alpha = typeof color.a === 'number' ? color.a : 1;
  var hue = (hsl.h + step.dh + 360) % 360;
  var sat = Math.min(1, Math.max(0, hsl.s + step.ds));
  var light = Math.min(0.97, Math.max(0.03, hsl.l + step.dl));
  var rgb = hslFractionToRgb(hue, sat, light);
  if (alpha < 0.999) {
    return 'rgba(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ', ' + Math.round(alpha * 1000) / 1000 + ')';
  }
  return colorToHex({ r: rgb.r, g: rgb.g, b: rgb.b, a: 1 });
}

function buildVariantPalette(step) {
  var out = { light: {}, dark: {} };
  ['light', 'dark'].forEach(function (mode) {
    var current = effectiveVars(mode) || {};
    VARIANT_SURFACES.forEach(function (key) {
      if (!current[key]) return;
      var shifted = shiftColorValue(String(current[key]), step);
      if (shifted) out[mode][key] = shifted;
    });
    VARIANT_TEXT_PAIRS.forEach(function (pair) {
      var text = out[mode][pair[0]] || current[pair[0]];
      var bg = out[mode][pair[1]] || current[pair[1]];
      if (!text || !bg) return;
      var fixed = contrastFixColor(parseColor(text), parseColor(bg), 4.5);
      if (fixed && fixed.color) out[mode][pair[0]] = colorToHex(fixed.color);
      else if (!out[mode][pair[0]]) out[mode][pair[0]] = String(text);
    });
  });
  return out;
}

function applyWallpaperPalette(palette, label) {
  if (!palette || !palette.light || !palette.dark) return 0;
  var changed = 0;
  undoPush('', true);
  [['light', palette.light], ['dark', palette.dark]].forEach(function (pair) {
    var mode = pair[0];
    var values = pair[1];
    Object.keys(values).forEach(function (key) {
      var value = values[key];
      if (value === undefined || value === null || value === '') return;
      state.edited[mode][key] = String(value);
      delete state.cleared[mode][key];
      changed += 1;
    });
  });
  if (!changed) return 0;
  markAllRowsValid();
  clearRgbMemory();
  syncAll();
  renderChanges();
  updateUndoButtons();
  showToast('已填入 ' + changed + ' 个变量（Ctrl+Z 可撤销）', 'ok');
  showNotice('info', '已按「' + label + '」生成整套配色：先看看效果，满意再点「应用」，不满意 Ctrl+Z 撤销。');
  return changed;
}

function openWallpaperPicker() {
  var input = $('wallpaper-file');
  if (!input) return;
  input.value = '';
  input.click();
}

function handleWallpaperFile(file) {
  if (!file) return;
  var name = String(file.name || '壁纸');
  if (!/^image\//.test(String(file.type || ''))) {
    showToast('请选一张图片（png / jpg / webp / gif）', 'warn');
    return;
  }
  if (file.size > WALLPAPER_MAX_FILE) {
    showToast('图片太大了（上限 12 MB），先压缩一下再试', 'warn');
    return;
  }
  var reader = new FileReader();
  reader.onerror = function () { showToast('这张图片读不出来，换一张试试', 'error'); };
  reader.onload = function () {
    var img = new Image();
    img.onerror = function () { showToast('这张图片解码失败，换一张试试', 'error'); };
    img.onload = function () {
      try {
        var canvas = document.createElement('canvas');
        canvas.width = WALLPAPER_SAMPLE;
        canvas.height = WALLPAPER_SAMPLE;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE);
        var data = ctx.getImageData(0, 0, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE).data;
        var colors = extractImageColors(data, WALLPAPER_SAMPLE, WALLPAPER_SAMPLE);
        if (!colors.length) {
          showToast('这张图里没取到可用颜色（可能大部分是透明的）', 'warn');
          return;
        }
        applyWallpaperPalette({ light: wallpaperPalette(colors, 'light'), dark: wallpaperPalette(colors, 'dark') }, name);
      } catch (err) {
        showToast('生成配色失败：' + String(err && err.message ? err.message : err), 'error');
      }
    };
    img.src = String(reader.result || '');
  };
  reader.readAsDataURL(file);
}

function applyNextVariant() {
  var step = VARIANT_STEPS[variantState.index % VARIANT_STEPS.length];
  variantState.index += 1;
  var palette = buildVariantPalette(step);
  var changed = applyWallpaperPalette(palette, '变体 · ' + step.label);
  if (!changed) showToast('当前配色里没有可调整的颜色变量', 'warn');
  else variantState.lastLabel = step.label;
}

function initWallpaperFeature() {
  if ($('btn-wallpaper')) $('btn-wallpaper').addEventListener('click', openWallpaperPicker);
  if ($('wallpaper-file')) {
    $('wallpaper-file').addEventListener('change', function () {
      var files = this.files;
      handleWallpaperFile(files && files[0]);
      this.value = '';
    });
  }
  if ($('btn-variants')) $('btn-variants').addEventListener('click', applyNextVariant);
}

/* ===== WALLPAPER_END ===== */

/* ===== BATCH3_BEGIN（v0.12.0：快捷调校 —— 光效三档 / 字体三档）=====
 * 两组都是「一键铺开」：把一组变量同时填进编辑区，用户再按「应用」提交。
 * 只写 state.edited（不调 apply_theme），所以：
 *   - 立刻能在预览/JSON/改动清单里看到；
 *   - 不满意可 Ctrl+Z 一步退回（照旧走 undoPush）；
 *   - 混着手改也没问题 —— 它只是把几个值一起 markEdited 进去。
 */
var GLOW_LEVELS = {
  low: { label: '低', value: '0.15' },
  medium: { label: '中', value: '0.45' },
  high: { label: '高', value: '0.85' }
};
/* 这四个是「光效强度」的落点（用户 2026-11-19 拍板 0.15 / 0.45 / 0.85）：
 *   ambient-glow       指针跟随柔光
 *   glass-dispersion   边缘分光
 *   glass-caustic      流动焦散
 *   panel-glow-pulse   背板呼吸 */
var GLOW_LEVEL_KEYS = [
  '--nts-ambient-glow',
  '--nts-glass-dispersion',
  '--nts-glass-caustic',
  '--nts-panel-glow-pulse'
];
/* 字体三档（用户拍板「字体给三档预设」）。系统默认 = 清空两个变量、回到应用原生。 */
var FONT_PRESETS = {
  system: { label: '系统默认', family: '', lineHeight: '' },
  sans: {
    label: '无衬线',
    family: 'Inter, PingFang SC, Microsoft YaHei, Noto Sans SC, system-ui, sans-serif',
    lineHeight: '1.6'
  },
  mono: {
    label: '等宽',
    family: 'JetBrains Mono, Cascadia Code, Consolas, Microsoft YaHei, monospace',
    lineHeight: '1.55'
  }
};

function glowLevelOfValue(value) {
  var v = String(value == null ? '' : value).trim();
  if (!v) return '';
  var num = parseFloat(v);
  if (!isFinite(num)) return '';
  var best = '';
  var bestGap = 0.06;
  Object.keys(GLOW_LEVELS).forEach(function (id) {
    var gap = Math.abs(parseFloat(GLOW_LEVELS[id].value) - num);
    if (gap <= bestGap) { bestGap = gap; best = id; }
  });
  return best;
}

/** 当前配置命中哪一档（四个值都接近同一档才算命中，否则返回空）。 */
function currentGlowLevel() {
  var hit = '';
  for (var i = 0; i < GLOW_LEVEL_KEYS.length; i += 1) {
    var level = glowLevelOfValue(displayValue(GLOW_LEVEL_KEYS[i]));
    if (!level) return '';
    if (hit && level !== hit) return '';
    hit = level;
  }
  return hit;
}

/** 当前字体命中哪个预设（字体族精确匹配；系统默认 = 两个变量都空）。 */
function currentFontPreset() {
  var family = String(displayValue('--nts-font-family') || '').trim();
  var lineHeight = String(displayValue('--nts-line-height') || '').trim();
  if (!family && !lineHeight) return 'system';
  var ids = Object.keys(FONT_PRESETS);
  for (var i = 0; i < ids.length; i += 1) {
    var preset = FONT_PRESETS[ids[i]];
    if (!preset.family) continue;
    if (preset.family === family) return ids[i];
  }
  return '';
}

function applyGlowLevel(level) {
  var def = GLOW_LEVELS[level];
  if (!def) return;
  undoPush('', true);
  GLOW_LEVEL_KEYS.forEach(function (key) {
    markEdited(key, def.value);
    syncRow(key);
  });
  renderQuickPresets();
  showToast('光效强度已设为「' + def.label + '」（四项变量，Ctrl+Z 可撤销）', 'ok');
}

function applyFontPreset(id) {
  var preset = FONT_PRESETS[id];
  if (!preset) return;
  undoPush('', true);
  if (preset.family) {
    markEdited('--nts-font-family', preset.family);
  } else {
    markCleared('--nts-font-family');
  }
  syncRow('--nts-font-family');
  if (preset.lineHeight) {
    markEdited('--nts-line-height', preset.lineHeight);
  } else {
    markCleared('--nts-line-height');
  }
  syncRow('--nts-line-height');
  renderQuickPresets();
  showToast('字体已切到「' + preset.label + '」（点「应用」才生效，Ctrl+Z 可撤销）', 'ok');
}

var quickState = { glow: '', font: '' };

function renderQuickPresets() {
  quickState.glow = currentGlowLevel();
  quickState.font = currentFontPreset();
  ['low', 'medium', 'high'].forEach(function (id) {
    var btn = $('btn-glow-' + id);
    if (!btn) return;
    var on = quickState.glow === id;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  ['system', 'sans', 'mono'].forEach(function (id) {
    var btn = $('btn-font-' + id);
    if (!btn) return;
    var on = quickState.font === id;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function initQuickPresets() {
  ['low', 'medium', 'high'].forEach(function (id) {
    var btn = $('btn-glow-' + id);
    if (btn) btn.addEventListener('click', function () { applyGlowLevel(id); });
  });
  ['system', 'sans', 'mono'].forEach(function (id) {
    var btn = $('btn-font-' + id);
    if (btn) btn.addEventListener('click', function () { applyFontPreset(id); });
  });
  renderQuickPresets();
}

/* ===== BATCH3_END ===== */

function wireEvents() {
  $('mode-light').addEventListener('click', function () { setMode('light'); });
  $('mode-dark').addEventListener('click', function () { setMode('dark'); });
  // 标签式控件按惯例支持左右方向键切换
  ['mode-light', 'mode-dark'].forEach(function (id) {
    $(id).addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      var next = event.key === 'ArrowLeft' ? 'light' : 'dark';
      setMode(next);
      $(next === 'light' ? 'mode-light' : 'mode-dark').focus();
    });
  });

  $('btn-apply').addEventListener('click', applyTheme);
  $('btn-reset').addEventListener('click', resetTheme);
  $('btn-export').addEventListener('click', exportTheme);
  $('btn-import').addEventListener('click', openImport);
  $('btn-reload').addEventListener('click', function () {
    // 「重新读取」会丢弃未应用的改动：先数出来、让人确认，别让一次误点
    // 把半小时的调色白做（resetTheme 早就有确认，这里之前没有）。
    var pending = pendingChangeCount();
    if (pending && !window.confirm('面板里有 ' + pending + ' 项还没应用的改动。\n'
      + '「重新读取」会丢弃它们（不可恢复）。继续？')) return;
    reloadPalette({ discardEdits: true }).then(function (palette) {
      if (palette && pending) {
        showNotice('warn', '已重新读取，丢弃了 ' + pending + ' 项未应用的改动。');
      }
    });
  });
  $('btn-revert-mode').addEventListener('click', revertMode);

  var search = $('row-search');
  if (search) {
    search.addEventListener('input', function () { setFilter(search.value); });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        clearFilter();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        var first = firstVisibleRow();
        if (first) first.focus();
      }
    });
  }
  if ($('search-clear')) $('search-clear').addEventListener('click', clearFilter);
  if ($('only-dirty')) {
    $('only-dirty').addEventListener('change', function () { setOnlyDirty($('only-dirty').checked); });
  }
  if ($('btn-toggle-groups')) $('btn-toggle-groups').addEventListener('click', toggleAllGroups);
  if ($('btn-toggle-dump')) $('btn-toggle-dump').addEventListener('click', toggleDumpCard);
  if ($('btn-copy-dump')) $('btn-copy-dump').addEventListener('click', copyDump);
  if ($('btn-css-snippet')) $('btn-css-snippet').addEventListener('click', copyCssSnippet);
  if ($('btn-revert-all')) $('btn-revert-all').addEventListener('click', revertAllChanges);
  initWallpaperFeature();
  initQuickPresets();
  if ($('btn-undo')) $('btn-undo').addEventListener('click', undoStep);
  if ($('btn-redo')) $('btn-redo').addEventListener('click', redoStep);
  if ($('btn-ai-prompt')) $('btn-ai-prompt').addEventListener('click', copyAiPrompt);
  initUndoShortcuts();
  if ($('btn-audit')) $('btn-audit').addEventListener('click', function () { renderAudit(false); });
  if ($('btn-audit-fix')) $('btn-audit-fix').addEventListener('click', function () { fixAllAudit(); });
  if ($('notice-close')) $('notice-close').addEventListener('click', hideNotice);

  // 主题仓库
  if ($('btn-lib-save')) $('btn-lib-save').addEventListener('click', saveLibraryTheme);
  if ($('btn-lib-shuffle')) $('btn-lib-shuffle').addEventListener('click', function () {
    shuffleLibraryTheme();
  });
  if ($('btn-lib-refresh')) $('btn-lib-refresh').addEventListener('click', function () {
    refreshLibrary().then(function (items) {
      if (items) showToast('仓库共 ' + items.length + ' 个主题');
    });
  });
  if ($('btn-lib-import')) $('btn-lib-import').addEventListener('click', pickArchiveFile);
  if ($('btn-lib-backup')) $('btn-lib-backup').addEventListener('click', function () { backupLibrary(this); });
  if ($('btn-lib-restore')) $('btn-lib-restore').addEventListener('click', pickLibraryBackupFile);
  if ($('lib-backup-file')) {
    $('lib-backup-file').addEventListener('change', function (event) {
      var input = event.target;
      handleLibraryBackupFile(input.files && input.files[0], input);
    });
  }
  // 按时段自动换主题
  if ($('btn-schedule-save')) $('btn-schedule-save').addEventListener('click', saveSchedule);
  if ($('schedule-weekend')) {
    $('schedule-weekend').addEventListener('change', function () {
      this.dataset.dirty = '1';
      markScheduleDirty();
      scheduleState.weekendEnabled = !!this.checked;
      renderSchedule();
    });
  }
  if ($('schedule-enabled')) {
    $('schedule-enabled').addEventListener('change', function () {
      // 启用开关本身也是「未保存的选择」：后端 save_schedule 才写盘。
      this.dataset.dirty = '1';
      markScheduleDirty();
    });
  }
  if ($('lib-file')) {
    $('lib-file').addEventListener('change', function (event) {
      var input = event.target;
      handleArchiveFile(input.files && input.files[0], input);
    });
  }
  // 名称框回车 = 保存（textarea 默认换行，这里显式拦下来）
  if ($('lib-name')) {
    $('lib-name').addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        saveLibraryTheme();
      }
    });
  }

  // 主题仓库搜索框：输入即过滤，和族 tab / 标签筛选叠加；Esc 清空，回车跳到第一张卡。
  if ($('lib-search')) {
    var libSearch = $('lib-search');
    libSearch.addEventListener('input', function (event) {
      libraryState.query = String(event.target.value || '').trim();
      renderLibrary();
    });
    libSearch.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        event.target.value = '';
        libraryState.query = '';
        renderLibrary();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        var libList = $('lib-list');
        var firstFav = libList && libList.querySelector ? libList.querySelector('.lib-fav') : null;
        if (firstFav) firstFav.focus();
      }
    });
  }

  // 运行状态
  if ($('btn-health-refresh')) $('btn-health-refresh').addEventListener('click', function () {
    refreshHealth({ toast: true });
  });
  if ($('btn-status-copy')) $('btn-status-copy').addEventListener('click', copyStatusPath);
  if ($('btn-help-open')) $('btn-help-open').addEventListener('click', openHelpModal);

  $('theme-enabled').addEventListener('change', function () { renderDump(); updateButtons(); });
  // 不能直接把 renderDump 当监听器：它第一个参数是 force，InputEvent 是真值，
  // 折叠状态下每敲一个字都会重建 ~10KB JSON（v0.4.27 的惰性化就白做了）。
  if ($('theme-name')) {
    $('theme-name').addEventListener('input', function () {
      renderDump();
      updateButtons();
    });
  }

  $('modal-close').addEventListener('click', closeModal);
  $('modal-cancel').addEventListener('click', closeModal);
  $('modal-ok').addEventListener('click', onModalOk);
  $('modal').addEventListener('click', function (event) {
    if (event.target === $('modal')) closeModal();
  });

  document.addEventListener('keydown', function (event) {
    var modalOpen = !$('modal').hidden;
    if (event.key === 'Escape' && modalOpen) { closeModal(); return; }
    if (event.key === 'Tab' && modalOpen) { trapModalFocus(event); return; }
    // 弹窗打开时不响应全局快捷键，否则会在用户看不到的背景下触发应用/搜索
    if (modalOpen) return;
    if ((event.ctrlKey || event.metaKey) && (event.key === 'Enter' || event.key === 'NumpadEnter')) {
      event.preventDefault();
      applyTheme();
      return;
    }
    if (event.key === '/' && !isTypingTarget(event.target) && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      if (search) search.focus();
    }
  });
}

function setMode(mode) {
  state.mode = mode;
  $('mode-light').classList.toggle('is-active', mode === 'light');
  $('mode-dark').classList.toggle('is-active', mode === 'dark');
  $('mode-light').setAttribute('aria-selected', mode === 'light' ? 'true' : 'false');
  $('mode-dark').setAttribute('aria-selected', mode === 'dark' ? 'true' : 'false');
  // 缩略色板跟当前编辑模式走（亮/暗各一套 preview 快照）。
  if (libraryState.items.length) renderLibrary();
  syncAll();
}

/* 11g. 键盘焦点自愈（v0.15.72）
 * 现象：宠物/悬浮形态下，面板 iframe 能收到鼠标事件（悬停、拖选、点按钮都正常），
 * 但键盘事件完全进不来 —— 按 Tab 没有焦点框，点输入框也没有光标。
 * 这不是面板拦了焦点（面板里没有任何抢焦点的 mousedown/blur 代码），而是宿主把键盘
 * 焦点留在别的窗口/文档上。面板能做的只有两件事：①点输入框时主动向上层窗口要一次
 * 焦点并重试几次；②仍然拿不到时给一句可执行的提示，别让用户以为面板坏了。
 */
function initFocusRecovery() {
  var hinted = false;
  function claimFocus(el) {
    try { if (window.top && window.top !== window) window.top.focus(); } catch (e1) { /* 跨域顶层：忽略 */ }
    try { window.focus(); } catch (e2) { /* ignore */ }
    if (el && typeof el.focus === 'function') {
      try { el.focus({ preventScroll: true }); } catch (e3) { try { el.focus(); } catch (e4) { /* ignore */ } }
    }
    [0, 60, 220].forEach(function (delay) {
      setTimeout(function () {
        if (!document.hasFocus()) return;
        if (el && document.activeElement !== el) {
          try { el.focus({ preventScroll: true }); } catch (e5) { /* ignore */ }
        }
      }, delay);
    });
  }
  document.addEventListener('mousedown', function (event) {
    var node = event.target;
    var el = node && node.closest ? node.closest('input, textarea') : null;
    if (!el || el.disabled || el.readOnly) return;
    if (document.hasFocus()) return;
    claimFocus(el);
    setTimeout(function () {
      if (document.hasFocus() || hinted) return;
      hinted = true;
      setStatus('键盘焦点被 N.E.K.O 主窗口占着：先点一下主窗口（比如聊天输入框），再回来就能输入', 'warn');
    }, 400);
  }, true);
}
function bootstrap() {
  // 重复启动哨兵（v0.4.27）：面板被注入两次时，wireEvents 会绑两遍、外部同步与
  // 排程轮询也会各起一套 —— 表现为「点一次按钮执行两次」「提示弹两遍」。
  if (window.__NTS_PANEL_BOOTED__) return;
  window.__NTS_PANEL_BOOTED__ = true;
  // localStorage 里的收藏与分组折叠状态：读失败/隐私模式都退回默认值，不挡启动。
  libraryState.favorites = libReadIdList(libraryStorage(), LIB_FAV_KEY);
  loadCatalogCollapsed();
  // 当前调色板 JSON 卡的折叠状态（v0.4.21）：记住上次的样子，折叠时省掉重建。
  dumpState.collapsed = loadDumpCollapsed();
  buildEditor();
  wireEvents();
  initRestartHint();
  // 拖拽导入（v0.5.0）：绑在 body 上，拖到面板任意位置都能导入
  initDropImport();
  initFocusRecovery();
  setMode('light');
  // 先按本地状态渲染，保证面板不会白屏；随后用 get_theme 校准。
  syncAll();
  applyFilters();
  reloadPalette({});
  // 仓库要单独拉一次（它读的是 themes/ 目录，不是 theme.json）。失败只是列表空，
  // 不影响编辑器 —— refreshLibrary 自己吞异常并写进 #lib-empty。
  refreshLibrary();
  // 可读性自检：先用当前状态预扫一次，打开面板就能看到有没有配崩。
  renderAudit(true);
  // 按时段自动换主题：要等仓库列表回来才知道下拉框能选什么。
  refreshSchedule();
  // 运行状态：把数据目录、当前主题与加载器上报立刻摊在面板上。
  // 这是用户「装上了却没效果」时唯一能自助定位的地方。
  refreshHealth({});
  // 外部改动感知：等首屏那几发请求落定再起，避免互相抢。
  setTimeout(startExternalSync, EXTERNAL_SYNC_START_DELAY_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') checkExternalChange();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap);
} else {
  bootstrap();
  // 面板起来以后再查一次版本：升级后旧页面（含旧 app.js）需要自己退出舞台。
  setTimeout(checkPanelFreshness, PANEL_FRESHNESS_DELAY_MS);
}

/* 材质中心（materials.js）是独立脚本，只能通过 window 拿入口调用与 toast。
 * 同样只在缺失时挂，避免脚本被重复执行时把已有引用覆盖成死函数。 */
/* 材质中心是独立文件（materials.js）。它 404 或语法错时 #materials-root 会一直空着，
   面板其它功能照常，用户只会以为「材质页坏了」。兜底给一句可诊断的话。 */
setTimeout(function () {
  try {
    var box = document.getElementById('materials-root');
    if (!box || box.firstChild || window.NekoMaterials) return;
    box.textContent = '材质中心没能加载（materials.js 未就绪），刷新面板可以重试。';
  } catch (e) { /* 提示本身失败不该影响面板 */ }
}, 3000);

window.NekoPanel = window.NekoPanel || {
  call: function (entryId, args) { return callPlugin(entryId, args); },
  toast: function (message, kind) { showToast(message, kind); }
};

/* 自动化/调试钩子：面板里没有模块系统，测试脚本只能通过 window 拿内部函数。
 * 只在缺失时挂，不覆盖已有对象。 */
window.__ntsPanel = window.__ntsPanel || {
  checkPanelFreshness: checkPanelFreshness,
  panelVersionOf: panelVersionOf,
  hoverPreview: hoverPreview,
  showHoverPreview: showHoverPreview,
  clearHoverPreview: clearHoverPreview,
  rewritePreviewRefs: rewritePreviewRefs,
  dumpState: dumpState,
  dumpLazyPlan: dumpLazyPlan,
  cssSnippetText: cssSnippetText,
  copyCssSnippet: copyCssSnippet,
  extractImageColors: extractImageColors,
  wallpaperPalette: wallpaperPalette,
  shiftColorValue: shiftColorValue,
  buildVariantPalette: buildVariantPalette,
  applyWallpaperPalette: applyWallpaperPalette,
  handleWallpaperFile: handleWallpaperFile,
  applyNextVariant: applyNextVariant,
  variantState: variantState,
  undoState: undoState,
  undoStep: undoStep,
  redoStep: redoStep,
  updateUndoButtons: updateUndoButtons,
  copyRowValue: copyRowValue,
  pasteRowValue: pasteRowValue,
  comparePairs: comparePairs,
  renderCompare: renderCompare,
  buildAiPrompt: buildAiPrompt,
  copyAiPrompt: copyAiPrompt,
  GLOW_LEVELS: GLOW_LEVELS,
  GLOW_LEVEL_KEYS: GLOW_LEVEL_KEYS,
  FONT_PRESETS: FONT_PRESETS,
  quickState: quickState,
  currentGlowLevel: currentGlowLevel,
  currentFontPreset: currentFontPreset,
  applyGlowLevel: applyGlowLevel,
  applyFontPreset: applyFontPreset,
  renderQuickPresets: renderQuickPresets,
  pendingChangeList: pendingChangeList,
  changeEntryList: changeEntryList,
  renderChanges: renderChanges,
  revertChange: revertChange,
  revertAllChanges: revertAllChanges,
  initDropImport: initDropImport,
  toggleDumpCard: toggleDumpCard,
  applyDumpCollapsed: applyDumpCollapsed,
  // v0.4.17：编辑区刷新合并的观测点（scheduled 发起次数 / flushes 实际刷新次数）
  editorSyncState: editorSyncState,
  // v0.4.26：名称/启用开关也算脏，自测脚本要能直接量
  metaDirtyCount: metaDirtyCount,
  pendingChangeCount: pendingChangeCount,
  // v0.4.18：主题信息编辑 / 首次使用提示（自测脚本用）
  openMetaModal: openMetaModal,
  saveThemeMeta: saveThemeMeta,
  parseTagInput: parseTagInput,
  initRestartHint: initRestartHint,
  RESTART_HINT_KEY: RESTART_HINT_KEY,
  editorSyncPlan: editorSyncPlan,
  flushEditorGlobals: flushEditorGlobals,
  scheduleEditorGlobals: scheduleEditorGlobals,
  backupLibrary: backupLibrary,
  restoreLibrary: restoreLibrary,
  handleLibraryBackupFile: handleLibraryBackupFile,
  state: state,
  callPlugin: callPlugin,
  reloadPalette: reloadPalette,
  refreshLibrary: refreshLibrary,
  saveLibraryTheme: saveLibraryTheme,
  applyLibraryItem: applyLibraryItem,
  exportLibraryItem: exportLibraryItem,
  deleteLibraryItem: deleteLibraryItem,
  importArchive: importArchive,
  effectiveVars: effectiveVars,
  collect: collect,
  syncAll: syncAll,
  doImport: doImport,
  rowsByKey: rowsByKey,
  refreshHealth: refreshHealth,
  renderHealth: renderHealth,
  copyStatusPath: copyStatusPath,
  openHelpModal: openHelpModal,
  // v0.2.0 面板 UX：族 tab / 搜索 / 收藏 / 分组折叠（自测脚本用）
  libraryState: libraryState,
  LIB_FAMILIES: LIB_FAMILIES,
  LIB_FAV_FAMILY: LIB_FAV_FAMILY,
  LIB_FAV_KEY: LIB_FAV_KEY,
  LIB_COLLAPSE_KEY: LIB_COLLAPSE_KEY,
  libFamilyOf: libFamilyOf,
  libSelectLibrary: libSelectLibrary,
  libFamilyCounts: libFamilyCounts,
  libPruneFavorites: libPruneFavorites,
  libReadIdList: libReadIdList,
  libWriteIdList: libWriteIdList,
  libReadCollapsed: libReadCollapsed,
  libWriteCollapsed: libWriteCollapsed,
  toggleLibraryFavorite: toggleLibraryFavorite,
  renderLibrary: renderLibrary,
  // v0.4.13 按时段自动换主题（自测脚本用）
  scheduleState: scheduleState,
  SCHEDULE_ZONE_LABELS: SCHEDULE_ZONE_LABELS,
  refreshSchedule: refreshSchedule,
  saveSchedule: saveSchedule,
  renderSchedule: renderSchedule,
  scheduleWindowText: scheduleWindowText,
  scheduleRefsFor: scheduleRefsFor,
  scheduleStaleText: scheduleStaleText,
  scheduleThemeGroups: scheduleThemeGroups,
  // 外部改动感知（v0.4.14）
  externalSyncState: externalSyncState,
  paletteSignature: paletteSignature,
  externalSyncDecision: externalSyncDecision,
  checkExternalChange: checkExternalChange,
  startExternalSync: startExternalSync,
  stopExternalSync: stopExternalSync,
  loadCatalogCollapsed: loadCatalogCollapsed,
  saveCatalogCollapsed: saveCatalogCollapsed
};
