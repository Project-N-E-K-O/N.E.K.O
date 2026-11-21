/*
 * emote_maker.js
 *
 * Local editor for an imported template: library, groups, look, and export.
 */
(function () {
    'use strict';

    const CANVAS_SIZE = 1024;
    // Default GIF output policy; source skeleton coordinates are preserved.
    const GIF_EXPORT_FPS = 20;
    const GIF_EXPORT_MAX_FPS = 50;
    const GIF_EXPORT_WIDTH = 240;
    const GIF_EXPORT_COLORS = 128;
    const GIF_EXPORT_MAX_BYTES = 1000000;
    const GIF_EXPORT_MAX_FRAMES = 120;
    const ICON_ROOT = '/static/icons/';
    const PREVIEW_ZOOM_MIN = 0.75;
    const PREVIEW_ZOOM_MAX = 4;

    const state = {
        inspectorTab: 'parts',
        colors: { expression: '#ffffff' },
        secondaryColor: '#2a4664',
        colorMode: 'default',
        adjustments: { x: 0, y: 0, rotation: 0, scale: 100 },
        adjustOverwrite: true,
        // 导出设置刻意放在 state 里但不进撤销栈 ——
        // Ctrl+Z 不该改到你的输出尺寸。它们单独持久化到 neko-emote-maker-export。
        exportFormat: 'png',
        exportTransparent: true,
        exportResolution: GIF_EXPORT_WIDTH,
        exportFps: GIF_EXPORT_FPS,
        // 'mouth' = 说话时与待机交替一张定格张嘴图（经典 PNGTuber 口型）
        // 'loop'  = 不写 talking_image，待机动画连续播放、不做交替
        packScale: 1,
        packOffsetX: 0,
        packOffsetY: 0,
        packMirror: false,
        // 形象包的状态→场景映射放在 state 里而不是只读 DOM：renderAll 每次都会重建
        // 这些 select，拖拽落点需要一条可靠的写入路径。
        packSceneIds: {},
        // 基础模式的公共时长（null = 演完整段）与包格式
        packName: '',
        packPriority: 'talking',
        packFormat: 'flat',
        // 节点图本身。它是"谁从谁派生、改了什么"的唯一真相，导出读它。
        nodeGraph: null,
        // 形象包的编辑层级：基础只填一个待机场景，进阶交给节点图。
        packMode: 'basic',
        groupPanelOpen: false,
        // 底部「批量导出」那颗按钮的气泡开合：只是当前浏览状态，不存盘
        groupPickerOpen: false,
        zoom: 1,
        previewPan: { x: 0, y: 0 },
        assetQuery: '',
        animationId: 'idle',
        animationProgress: 0,
        animationSpeed: 1,
        animationPlaying: false,
        selectedSceneId: '',
        groupId: '',
        groups: [],
        shots: [],
        templateSkins: [],
        templateSlots: [],
        templateSlotDefaults: [],
        templateSlotGroup: ''
    };
    const TEMPLATE_SESSION_DEFAULTS = Object.fromEntries([
        'colors', 'secondaryColor', 'colorMode', 'adjustments', 'adjustOverwrite',
        'packSceneIds',
        'packName', 'packPriority', 'packFormat', 'nodeGraph',
        'animationProgress', 'animationSpeed', 'groups', 'shots', 'groupId',
    ].map((key) => [key, JSON.parse(JSON.stringify(state[key]))]));

    function resetTemplateSession() {
        for (const [key, value] of Object.entries(TEMPLATE_SESSION_DEFAULTS)) {
            state[key] = JSON.parse(JSON.stringify(value));
        }
    }

    // A package is kept in memory only.  The normalized manifest contains
    // paths and metadata; binary assets live in a short-lived client store and
    // are never serialized into localStorage.
    let templatePackage = null;
    let templateAssetStore = null;
    // 上次的导入方式，决定"重新打开"该弹哪个选择器。
    const groupLibrary = window.NekoEmoteGroupLibrary;
    // 整组导出会一路烘到底，中途不接受再来一次导出或改设置
    let groupExportBusy = false;
    let groupExportPicking = false;
    let groupExportAbort = false;
    // 预览列表里的多选集合（动画 id）。批量加组靠它，不然一个个右键点太累。
    const pickedAnimations = new Set();
    // ── 模板库 ────────────────────────────────────────────────────────────────
    // 数据规则都在 emote_template_library.js 里（可单测）；这里只负责喂和画。
    const templateLibraryModule = window.NekoEmoteTemplateLibrary;
    // 本次会话已经读进内存的模板：切回去不该再把几百个场景重新解析一遍。
    const loadedTemplates = new Map();
    let templateLibraryState = templateLibraryModule
        ? templateLibraryModule.emptyStore() : null;
    // 桌面端若能拿到绝对路径，跨重启就能静默重读；拿不到就靠用户点一次重新定位。

    const history = [];
    const future = [];
    let historyBusy = false;
    let toastTimer = null;
    let spineRenderer = null;
    let spineRendererGeneration = 0;
    let spineAnimationFrameHandle = null;
    let spineAnimationLastTimestamp = 0;

    const $ = (selector) => document.querySelector(selector);
    const canvas = $('#emote-canvas');
    let spineCanvas = $('#emote-spine-canvas');
    const wardrobePlacements = $('#wardrobe-placements');
    const canvasFrame = $('#canvas-frame');
    const canvasStage = $('.canvas-stage');
    const assetGrid = $('#asset-grid');
    const assetSectionTitle = $('#asset-section-title');
    const animationDock = document.querySelector('.animation-dock');
    const colorPanel = $('#inspector-panel-colors');
    const colorBackToTop = $('#color-back-to-top');
    const templateSlotControls = $('#template-slot-controls');
    const changedPartColors = $('#changed-part-colors');
    const changedPartColorList = $('#changed-part-color-list');
    const templateAttachmentSearch = $('#template-attachment-search');
    const templateAttachmentControls = $('#template-attachment-controls');
    const templateAttachmentEmpty = $('#template-attachment-empty');
    const zoomValue = $('#zoom-value');
    const toast = $('#emote-toast');
    const animationList = $('#animation-list');
    const animationProgress = $('#animation-progress');
    const animationTime = $('#animation-time');
    const animationSpeed = $('#animation-speed');
    const templateFileInput = $('#template-archive-file');
    const templateArchiveButton = $('#select-template-archive-btn');
    const templateImportStatus = $('#template-import-status');
    const templateRuntimeBadge = $('#template-runtime-badge');
    const templateCanvasStatus = $('#template-canvas-status');
    const templateCanvasStatusText = $('#template-canvas-status-text');
    const canvasImportButton = $('#canvas-import-btn');
    const inspectorEmptyState = $('#inspector-empty-state');
    const batchExportGroupSelect = $('#batch-export-group-select');
    const templateSkinControls = $('#template-skin-controls');

    const fallbackText = {
        'emoteMaker.title': '表情包制作',
        'emoteMaker.assetLibrary': '素材库',
        'emoteMaker.noTemplates': '模板暂未添加',
        'emoteMaker.selectTemplateHint': '请选择一个模板',
        'emoteMaker.selectTemplateArchive': "导入 Spine ZIP",
        'emoteMaker.spineZipRequired': "请选择包含 Spine 骨架、图集和全部引用 PNG 贴图的 ZIP。",
        'emoteMaker.spineSkin': "皮肤",
        'emoteMaker.partFollowAnimation': '跟随动作',
        'common.on': '开',
        'common.off': '关',
        'emoteMaker.spineChooseResource': "发现多组资源，请选择要导入的模型或图集。",
        'emoteMaker.importingTemplate': '正在读取模板…',
        'emoteMaker.importFailed': '模板添加失败',
        'emoteMaker.importFailedKeepCurrent': '模板读取失败，当前模板未更改。',
        'emoteMaker.templateMetadataOnly': '已读取模板，正在准备预览。',
        'emoteMaker.templateSceneList': "动作列表",
        'emoteMaker.templateCapabilityWarning': "预览暂时开不了，编辑不受影响。",
        'emoteMaker.templateCompatibilityWarning': "这个模板有些数据和当前版本对不上，能用的设置都保留了。",
        'emoteMaker.templatePendingPreview': '待预览',
        'emoteMaker.renameTemplate': '重命名模板',
        'emoteMaker.templateNoRuntime': '预览暂不可用',
        'emoteMaker.templateLibrary': "已有模板",
        'emoteMaker.removeTemplateConfirm': "删除「{{name}}」？",
        'emoteMaker.groupEmptyList': "暂无组",
        'emoteMaker.confirmCancel': "取消",
        'emoteMaker.confirmDelete': "删除",
        'emoteMaker.archiveSaveFailed': "模板没存进本地库，下次打开可能要重新选文件",
        'emoteMaker.archiveDeleteFailed': '本地模板副本删除失败，请重试',
        'emoteMaker.builtinTemplate': '内置',
        'emoteMaker.wardrobeGenerate': '猫猫辅助生成',
        'emoteMaker.wardrobeModel': '生图模型',
        'emoteMaker.wardrobeUnconfigured': '请在自定义 API 中配置生图模型和密钥。',
        'emoteMaker.wardrobeVisionUnavailable': '生成挂件需要配置支持识图的视觉模型。',
        'emoteMaker.wardrobeVisionFailed': '参考图识别失败，请检查视觉模型设置或稍后重试。',
        'emoteMaker.wardrobeWorking': '正在生成挂件…',
        'emoteMaker.wardrobeSaving': '正在更新模板…',
        'emoteMaker.wardrobeInvalidImage': '请选择不超过 8 MB、长边不超过 8192 像素的 PNG、JPEG 或 WebP 单张图片。',
        'emoteMaker.wardrobeFailed': '操作失败，请检查生图配置或稍后重试。',
        'emoteMaker.wardrobeBusy': '已有挂件正在生成，请稍后再试。',
        'emoteMaker.wardrobeSpriteGenerate': '生成挂件',
        'emoteMaker.wardrobeSpriteSend': '配置的视觉和生图 API 仅接收框内的原图局部。生成的透明底挂件会自动存入素材库。',
        'emoteMaker.wardrobeChooseReference': '选择参考图',
        'emoteMaker.wardrobePromptInvalid': '补充提示词仅适用于单个选框，最多 240 个字符。',
        'emoteMaker.wardrobeReferenceNavigator': '参考图导航',
        'emoteMaker.wardrobeCropCount': '已选区域',
        'emoteMaker.wardrobeCropPadded': '选框超出比例限制，生成时会自动补充空白区域。',
        'emoteMaker.wardrobeCropLowResolution': '选框内像素较少，生成时可能丢失细节。可以换一张更清晰的参考图。',
        'emoteMaker.wardrobeSpriteCrop': '在参考图上框选部件',
        'emoteMaker.wardrobeCropBox': '裁切框',
        'emoteMaker.wardrobeCropDelete': '删除选框',
        'emoteMaker.wardrobeClearMarks': '清空选框',
        'emoteMaker.wardrobeSpriteAdd': '添加所选部件',
        'emoteMaker.wardrobeSpriteOpen': '打开挂件',
        'emoteMaker.wardrobeLibrarySave': '存入素材库',
        'emoteMaker.wardrobeLibrarySaved': '已存入素材库',
        'emoteMaker.wardrobeLibrarySaveFailed': '无法存入素材库。可在当前预览中重试。',
        'emoteMaker.wardrobeLibraryImport': '导入图片',
        'emoteMaker.wardrobeLibraryEmpty': '导入图片或生成挂件后，可在这里取用。',
        'emoteMaker.wardrobeLibraryInvalidImage': '请选择长边不超过 2048 像素、大小不超过 32 MB 的静态 PNG、JPG 或 WebP 图片。',
        'emoteMaker.wardrobeLibraryConfirmDelete': '从素材库删除这个挂件素材？',
        'emoteMaker.wardrobeLibraryFailed': '素材库操作失败，请重试。',
        'emoteMaker.wardrobeSpriteOpenFailed': '无法打开挂件。请选择长边不超过 2048 像素、大小不超过 32 MB 的 PNG。',
        'emoteMaker.wardrobeSpriteAdded': '已添加。关闭窗口后可在画布上拖动和缩放部件。',
        'emoteMaker.wardrobePlacementTip': '可右键挂件设置跟随骨骼以及调整图层',
        'emoteMaker.wardrobeSpriteSize': '等比缩放部件',
        'emoteMaker.wardrobeSpriteRotate': '旋转部件',
        'emoteMaker.wardrobeSpriteRemove': '移除部件',
        'emoteMaker.wardrobeSpriteMove': '拖动部件，右键设置骨骼',
        'emoteMaker.wardrobeSpriteBinding': '跟随骨骼',
        'emoteMaker.wardrobeLayerUp': '上移一层',
        'emoteMaker.wardrobeLayerDown': '下移一层',
        'emoteMaker.wardrobeLayer': '图层',
        'emoteMaker.wardrobeLayerFront': '角色最前面',
        'emoteMaker.wardrobeLayerBack': '角色最后面',
        'emoteMaker.wardrobeLayerAbove': '在 {{name}} 前面',
        'emoteMaker.wardrobeSpriteAutoBinding': '自动匹配',
        'emoteMaker.wardrobeSpriteFreeBinding': '固定在画布',
        'emoteMaker.wardrobeSpriteNearbyBones': '落点候选',
        'emoteMaker.wardrobeSpriteAllBones': '全部骨骼',
        'emoteMaker.wardrobeBoneHead': '头部',
        'emoteMaker.wardrobeBoneChest': '胸部',
        'emoteMaker.wardrobeBoneHips': '腰胯',
        'emoteMaker.wardrobeBoneUpperArm': '上臂',
        'emoteMaker.wardrobeBoneForearm': '前臂',
        'emoteMaker.wardrobeBoneHand': '手部',
        'emoteMaker.wardrobeBoneThigh': '大腿',
        'emoteMaker.wardrobeBoneShin': '小腿',
        'emoteMaker.wardrobeSpriteUnsupported': '当前生图模型不支持使用参考图，请在 API 设置中更换模型。',
        'emoteMaker.wardrobeSpriteResultFailed': '生成结果缺少可用的透明背景。',
        'emoteMaker.wardrobeSpriteEmpty': '选框内没有可添加的部件。',
        'emoteMaker.wardrobeSpriteSaveFailed': '保存部件改动失败，请重试。',
        'emoteMaker.wardrobeMarkImageFailed': '未能加载图片，请检查参考图或重新选择部件。',
        'emoteMaker.wardrobeInvalidMarks': '裁切框无效，请检查范围和数量。',
        'emoteMaker.wardrobeProviderAccessDenied': '生图服务拒绝了请求，请检查密钥和模型权限。',
        'emoteMaker.wardrobeProviderLimited': '生图服务触发了限流，请稍后重试。',
        'emoteMaker.wardrobeProviderQuota': '生图账号额度已用完或账户欠费，请检查服务商账号。',
        'emoteMaker.wardrobeProviderTimeout': '生图请求超时，请稍后重试。',
        'emoteMaker.wardrobeProviderUnavailable': '生图服务暂时不可用，请稍后重试。',
        'emoteMaker.wardrobeProviderRejected': '生图服务未接受这次生成请求，请检查参考图和所选模型。',
        'emoteMaker.wardrobeProviderResponse': '生图服务返回的结果无法解析，请重试或更换模型。',
        'emoteMaker.wardrobeDownloadFailed': '已收到生成结果，但下载贴图失败，请稍后重试。',
        'emoteMaker.wardrobeLocalConnectionLost': '与本地服务的连接中断，请确认服务正在运行后重试。',
        'emoteMaker.templateLibraryEmpty': "目前只有这一个模板，再导入的会并排显示在这里。",
        'emoteMaker.templateInUse': "使用中",
        'emoteMaker.removeTemplate': "移除",
        'emoteMaker.templateSwitched': "已切换模板",
        'emoteMaker.templateImportHint': "请选择包含 Spine 骨架、图集和全部引用 PNG 贴图的 ZIP。",
        'emoteMaker.previewTitle': '表情包制作',
        'emoteMaker.searchParts': '搜索模板',
        'emoteMaker.addToGroup': "加入本组",
        'emoteMaker.groupExportCancel': "停止导出",
        'emoteMaker.groupNameDefault': "未命名组",
        'emoteMaker.groupCreated': "已新建组",
        'emoteMaker.groupRename': "改名",
        'emoteMaker.groupDelete': "删除",
        'emoteMaker.groupDeleteConfirm': "删除「{{name}}」？将移除这一组内的条目，保留原动作和图片。",
        'emoteMaker.groupDeleted': "已删除这个组",
        'emoteMaker.groupReadOnly': "导入的组改不了，想改就先新建一个",
        'emoteMaker.groupSceneAdded': "已加入本组",
        'emoteMaker.groupExportNoRuntime': "预览没跑起来的模板没法整组导出",
        'emoteMaker.groupExportEmpty': "这一组还是空的：右键中间的动作列表，就能加进来。",
        'emoteMaker.groupExporting': "正在导出 {{done}}/{{total}}…",
        'emoteMaker.groupExportDone': "整组已导出",
        'emoteMaker.groupExportCancelled': "已停止，已完成的部分照样打包",
        'emoteMaker.groupExportFailed': "有 {{count}} 项导出失败",
        'emoteMaker.groupExportTruncated': "一次最多导出 {{count}} 张，其余可另建新组",
        'emoteMaker.groupExportZipping': "正在打包…",
        'emoteMaker.groupExportFolderDone': "已写入所选文件夹，并打好压缩包",
        'emoteMaker.groupExportFolderFailed': "没法在这个目录里建文件夹",
        'emoteMaker.groupExportPickFolder': "选择导出位置",
        'emoteMaker.groupExportPickFailed': "选不了导出位置，改成直接下载",
        'emoteMaker.groupExportZipFailed': "打包失败，已经写好的文件还在文件夹里",
        'emoteMaker.groupPickFirst': "组内暂无动作，去添加？",
        'emoteMaker.groupMemberShot': "取景",
        'emoteMaker.groupMemberMissing': "来源已不在当前模板里",
        'emoteMaker.groupRemoveMember': "移出",
        'emoteMaker.groupNoSelection': "先在预览里挑一个动作，再加入本组。",
        'emoteMaker.batchExport': "批量导出",
        'emoteMaker.exportCurrent': "导出当前",
        'emoteMaker.batchExportPicker': "选择要导出的组",
        'emoteMaker.batchExportPickerEmpty': "还没有组：先在「批量导出」里按当前配置建一个。",
        'emoteMaker.groupImportedTag': "模板自带",
        'emoteMaker.groupMemberCount': "{{count}} 张",
        'emoteMaker.groupContextTitle': "批量导出组",
        'emoteMaker.groupContextAdd': "加入当前组",
        'emoteMaker.groupContextRemove': "移出当前组",
        'emoteMaker.groupContextHint': "右键中间的动作列表，就能加入当前组。",
        'emoteMaker.batchExportTip': '右键动作板块中的内容可快速多选和添加/移除当前组',
        'emoteMaker.newGroupFromCurrent': "按当前配置创建组",
        'emoteMaker.newGroupHelp': "新建一个组，记下当前的大小、旋转和换装。之后加进组里的动作，预览和导出都用这一套。",
        'emoteMaker.groupNonePicked': "还没选组",
        'emoteMaker.selectedCount': "已选 {{count}} 条",
        'emoteMaker.groupPickedMissing': "{{count}} 个动作没有对应的取景，没法加入",
        'emoteMaker.groupAddedCount': "已加入 {{count}} 条",
        'emoteMaker.groupRemovedCount': "已移出 {{count}} 条",
        'emoteMaker.selectAll': "全选",
        'emoteMaker.selectNone': "取消全选",
        'emoteMaker.resetView': '重置视图',
        'emoteMaker.viewportHint': '滚轮缩放 · Alt+滚轮旋转 · 中键平移',
        'emoteMaker.partsTab': '换装',
        'emoteMaker.adjustTab': '调整',
        'emoteMaker.colorsTab': '颜色',
        'emoteMaker.slotGroup': '色组',
        'emoteMaker.exportTab': '导出',
        'emoteMaker.editorPanels': '编辑器面板',
        'emoteMaker.animationProgressLabel': "动作进度",
        'emoteMaker.chooseParts': '选择部件',
        'emoteMaker.adjustments': '整体调整',
        'emoteMaker.overwrite': '覆盖默认值',
        'emoteMaker.positionX': '位置 X',
        'emoteMaker.positionY': '位置 Y',
        'emoteMaker.rotation': '旋转',
        'emoteMaker.scale': '缩放',
        'emoteMaker.colorSurface': '饱和度和明度',
        'emoteMaker.colorHue': '色相',
        'emoteMaker.colorHex': '十六进制颜色',
        'emoteMaker.colorRecent': '最近使用',
        'emoteMaker.colorChangedParts': '已改色部件',
        'emoteMaker.colorBackToTop': '返回颜色面板顶部',
        'emoteMaker.colorNoVisibleParts': '当前没有可调色的可见部件。',
        'emoteMaker.partColor': '颜色',
        'emoteMaker.tintBlack': '阴影颜色',
        'emoteMaker.speed': '速度',
        'emoteMaker.exportFormat': '格式',
        'emoteMaker.exportStatic': '静态 PNG',
        'emoteMaker.exportAnimated': '动画 GIF',
        'emoteMaker.exportGifProgress': '正在编码动画 GIF…',
        'emoteMaker.exportGifFailed': '动画 GIF 导出失败',
        'emoteMaker.exportGifUnavailable': '当前模板暂不支持动画导出',
        'emoteMaker.exportGifSuccess': 'GIF 已导出',
        'emoteMaker.exportPngFailed': 'PNG 导出失败，请重试。',
        'emoteMaker.exportResolution': '分辨率',
        'emoteMaker.exportFps': '帧率',
        'emoteMaker.packTitle': '形象包（PNGTuber）',
        'emoteMaker.packDropNoScene': "无法解析动作",
        'emoteMaker.packSkipOption': '不导出',
        'emoteMaker.packStateIdle': '待机',
        'emoteMaker.packStateTalking': '说话',
        'emoteMaker.packStateDrag': '拖拽',
        'emoteMaker.packStateClick': '点击',
        'emoteMaker.packStateHappy': '开心',
        'emoteMaker.packStateSad': '难过',
        'emoteMaker.packStateAngry': '生气',
        'emoteMaker.packStateSurprised': '惊讶',
        'emoteMaker.packExportZip': '导出形象包',
        'emoteMaker.packImportNow': '导入到模型管理',
        'emoteMaker.packBakingProgress': '正在生成形象包…',
        'emoteMaker.packIdleRequired': "先给「{{state}}」挑一个动作。",
        'emoteMaker.packZipSuccess': "形象包已导出，可以直接导入到模型管理。",
        'emoteMaker.packZipFailed': '形象包生成失败。',
        'emoteMaker.packImportSuccess': '形象包已导入模型管理。',
        'emoteMaker.packImportFailed': '形象包导入失败。',
        'emoteMaker.segmentScenes': "动作",
        'emoteMaker.segmentAnimations': "动作",
        'emoteMaker.resizeLibraryAria': '调整素材库宽度',
        'emoteMaker.resizeInspectorAria': '调整部件设置宽度',
        'emoteMaker.resizeListAria': '调整列表高度',
        'emoteMaker.packBasicFollowRoot': '跟随待机',
        'emoteMaker.packFormatLabel': '包格式',
        'emoteMaker.packFormatFlat': '平铺（GIF，通用兼容）',
        'emoteMaker.packFormatLayered': "分层（多状态动作）",
        'emoteMaker.packLayeredUnavailable': "分层打包功能没加载出来，刷新再试。",
        'emoteMaker.packModeAria': '编辑方式',
        'emoteMaker.packModeBasic': '基础',
        'emoteMaker.packDragTip': '长按动作板块中内容可拖动到对应位置快速应用',
        'emoteMaker.packModeGraph': "进阶",
        'emoteMaker.stageOpen': "打开进阶设置",
        'emoteMaker.stageTitle': "进阶设置",
        'emoteMaker.stageClose': "关闭",
        'emoteMaker.stageCanvasAria': "状态预览台",
        'emoteMaker.stageShelfAria': "状态格",
        'emoteMaker.stageAnimation': "这个状态用的动作",
        'emoteMaker.stageStripAria': "胶片带：拖动两端设置起止帧",
        'emoteMaker.stagePlayWhole': "整段播放",
        'emoteMaker.stageResetPose': "复位大小和角度",
        'emoteMaker.stagePoseNeedsLayered': "平铺包不保存每个状态的大小和角度；换成「分层」格式才会生效。",
        'emoteMaker.stageNoFrames': "这个状态还没有画面——先在左边挑一个动作。",
        'emoteMaker.stageScale': "大小",
        'emoteMaker.stageAngle': "角度",
        'emoteMaker.stageFollowTag': "跟随默认",
        'emoteMaker.stageStillTag': "定格",
        'emoteMaker.stageUseDefaults': "跟随默认外观",
        'emoteMaker.stageGestureHint': "拖动旋转，滚轮缩放",
        'emoteMaker.stageLibrary': "动作库",
        'emoteMaker.stageUserImages': "图片库",
        'emoteMaker.stageImportImages': "导入图片",
        'emoteMaker.stageNoUserImages': "还没有导入图片，点「导入图片」加几张",
        'emoteMaker.stageImagesSupportedFormats': "只支持 PNG 和 GIF 图片",
        'emoteMaker.stageImagesInvalidGif': "无法读取 GIF。请选择长边不超过 2048 像素、最多 240 帧且不超过 32 MB 的 GIF。",
        'emoteMaker.stageImagesImported': "已导入 {{count}} 张图片",
        'emoteMaker.stageImagesSaveFailed': "图片没存进模板副本：下次打开可能要重新导入",
        'emoteMaker.stageImageDeleteFailed': '删除图片失败，请重试。',
        'emoteMaker.stageImagesMissing': "{{count}} 个状态绑的图片不见了，已跳过",
        'emoteMaker.stageSearch': "搜索动作",
        'emoteMaker.packPlanSkipped': "已跳过未设置的状态",
        'emoteMaker.packPriorityLabel': "状态优先级",
        'emoteMaker.packPriorityHint': "说话优先时情绪在说话后播放，情绪优先时说话期间触发情绪会覆盖说话动画",
        'emoteMaker.packPriorityTalking': "说话优先",
        'emoteMaker.packPriorityEmotion': "情绪优先",
        'emoteMaker.packPlanUnassigned': "未选动作",
        'emoteMaker.packPlanDuplicate': "同一状态重复",
        'emoteMaker.graphCollapsedNotice': "同一状态设了两遍的，已保留你选的那个",
        'emoteMaker.stageUnavailable': "进阶设置没加载出来，刷新再试。",
        'emoteMaker.packNeedsTemplate': "先导入一个模板。",
        'emoteMaker.nodeCycleWarning': '节点之间存在环，无法求值。',
        'emoteMaker.nodeNoOutput': "还没给任何状态挑动作，先设置再导出。",
        'emoteMaker.undo': '撤销',
        'emoteMaker.redo': '重做',
        'emoteMaker.fitWindow': '适应窗口',
        'emoteMaker.partSettings': '模板设置',
        'emoteMaker.transparentBackground': '透明背景',
        'emoteMaker.exportSuccess': 'PNG 已导出',
        'emoteMaker.reset': '重置'
    };

    function t(key) {
        try {
            if (typeof window.t === 'function') {
                const translated = window.t(key);
                if (translated && translated !== key) return translated;
            }
        } catch (error) {
            // Keep the editor usable if the locale bundle is still loading.
        }
        return fallbackText[key] || key;
    }

    function getImportedScenes() {
        return Array.isArray(templatePackage?.scenes) ? templatePackage.scenes : [];
    }

    function getImportedAnimations() {
        const entries = Array.isArray(templatePackage?.catalogs?.animations)
            ? templatePackage.catalogs.animations
            : [];
        const result = entries.map((item) => ({ ...item }));
        const known = new Set(result.map((item) => String(item.id || '')));
        const nativeAnimations = spineRenderer?.animations?.() || [];
        nativeAnimations.forEach((item) => {
            const id = String(item?.name || '');
            if (!id || known.has(id)) return;
            known.add(id);
            result.push({ id, displayName: id, labels: {}, runtimeOnly: true });
        });
        return result;
    }

    /**
     * 有没有可播放的动画。带模板时要把整份动画清单过一遍（runtimeOnly 过滤），
     * 这个结果在清单不变时是常量 —— 缓存它，播放循环里就不必每帧重算。
     */
    let playableMemo = { key: '', value: false };

    function hasPlayableAnimations() {
        if (!templatePackage) return false;
        const key = listsSignature(getVisibleImportedAnimations(), 'anim');
        if (playableMemo.key !== key) {
            playableMemo = { key, value: getVisibleImportedAnimations().length > 0 };
        }
        return playableMemo.value;
    }

    function getVisibleImportedAnimations() { return getImportedAnimations(); }

    function getCatalogLabel(item) {
        if (!item) return '';
        const labels = item.labels || {};
        const language = document.documentElement.lang || 'zh-CN';
        return labels[language] || labels[language.replace('-', '_')] || labels.en || item.displayName || item.id || '';
    }

    function setTemplateImportStatus(message, tone = 'muted') {
        if (!templateImportStatus) return;
        templateImportStatus.textContent = message;
        templateImportStatus.dataset.tone = tone;
    }

    function hasSpineRenderer() {
        return Boolean(spineRenderer && !spineRenderer.disposed && spineRenderer.loaded);
    }

    function setCanvasMode(nativePreview) {
        if (canvas) canvas.hidden = Boolean(nativePreview);
        if (spineCanvas) spineCanvas.hidden = !nativePreview;
        wardrobePlacements.hidden = !nativePreview || templatePackage?.source?.builtIn !== true;
        if (wardrobePlacements.hidden) {
            wardrobeArtworkBehind.hidden = true;
            wardrobeSpriteMenu.hidden = true;
            canvasFrame.classList.remove('wardrobe-solid-background');
        }
        if (templateCanvasStatus) templateCanvasStatus.hidden = Boolean(nativePreview);
    }

    function stopSpineAnimationLoop() {
        if (spineAnimationFrameHandle) {
            window.cancelAnimationFrame(spineAnimationFrameHandle);
            spineAnimationFrameHandle = null;
        }
        spineAnimationLastTimestamp = 0;
    }

    function stopAnimationLoops() {
        stopSpineAnimationLoop();
    }

    function disposeSpineRenderer() {
        stopAnimationLoops();
        if (spineRenderer && typeof spineRenderer.dispose === 'function') {
            try {
                spineRenderer.dispose();
            } catch (_) {
                // Native WebGL teardown is best effort during replacement.
            }
        }
        spineRenderer = null;
        setCanvasMode(false);
    }

    function releaseAssetStore(store) {
        if (!store || typeof store.release !== 'function') return;
        try {
            store.release();
        } catch (_) {
            // Releasing local object URLs is best effort during navigation.
        }
    }

    function releaseTemplateAssetStore() {
        releaseAssetStore(templateAssetStore);
        templateAssetStore = null;
    }

    function importFailureMessage(error) {
        if (error?.userMessage) return error.userMessage;
        return t('emoteMaker.importFailedKeepCurrent');
    }

    function reportImportFailure(error) {
        const message = importFailureMessage(error);
        setTemplateImportStatus(
            message,
            'error',
        );
        showToast(message);
    }

    function getTemplateAssetPaths(packageManifest) {
        const paths = [
            packageManifest?.model?.skeleton?.path,
            packageManifest?.model?.atlas?.path,
            ...(Array.isArray(packageManifest?.model?.atlas?.pages)
                ? packageManifest.model.atlas.pages.map((page) => page?.path)
                : [])
        ];
        return [...new Set(paths.filter((path) => typeof path === 'string' && path))];
    }

    function getImportedAnimationDuration(animationId) {
        if (!animationId) return 0;
        const native = spineRenderer?.animations?.().find((item) => item.name === animationId);
        if (native && Number.isFinite(Number(native.duration))) return Number(native.duration);
        return 0;
    }

    function getSelectedScene() {
        return getImportedScenes().find((scene) => scene.id === state.selectedSceneId) || null;
    }

    function getTemplateSkinSelections() {
        if (Array.isArray(state.templateSkins) && state.templateSkins.length) {
            return state.templateSkins.map((item) => ({ ...item }));
        }
        return Array.isArray(templatePackage?.character?.skins)
            ? templatePackage.character.skins.map((item) => ({ ...item }))
            : [];
    }

    function getTemplateSlotStyles() {
        const styles = new Map((templatePackage?.character?.slots || []).map((item) => [item.name, item]));
        for (const item of state.templateSlots || []) {
            if (styles.has(item.name)) styles.set(item.name, item);
        }
        return Array.from(styles.values(), (item) => ({
            ...item,
            tintColor: item.tintColor ? { ...item.tintColor } : null,
            tintBlackColor: item.tintBlackColor ? { ...item.tintBlackColor } : null,
        }));
    }

    function colorToHex(value, fallback = '#ffffff') {
        if (!value || typeof value !== 'object') return fallback;
        const channel = (key, fallbackValue) => {
            const number = Number(value[key]);
            return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : fallbackValue;
        };
        const bytes = ['r', 'g', 'b'].map((key) => Math.round(channel(key, 1) * 255));
        return `#${bytes.map((byte) => byte.toString(16).padStart(2, '0')).join('')}`;
    }

    function hexToTemplateColor(value, fallback = { r: 1, g: 1, b: 1, a: 1 }) {
        const color = hexToColor(value, fallback);
        return { r: color.r, g: color.g, b: color.b, a: color.a };
    }

    function getTemplateSkinOptions() {
        const options = spineRenderer?.skinOptions?.() || {};
        return options && typeof options === 'object' ? options : {};
    }

    // 组里存的换装/位移只覆盖画布预览和整组导出。动作列表、形象包读编辑器自己的 state。
    let groupPreview = null;
    let captureLook = null;
    let capturingFrames = false;

    function renderLook() {
        if (captureLook) return captureLook;
        if (capturingFrames) return null;
        return groupPreview?.look || null;
    }

    function applyNativeCharacterState({ render = true } = {}) {
        if (!hasSpineRenderer()) return false;
        try {
            const look = renderLook();
            const slots = look ? (look.slots || []).map((item) => ({ ...item })) : getTemplateSlotStyles();
            if (colorDraft && !capturingFrames) {
                const slot = slots.find((item) => item.name === colorDraft.name);
                if (slot) slot[colorDraft.field] = colorDraft.value;
            }
            spineRenderer.setCharacterState(look ? {
                skins: (look.skins || []).map((item) => ({ ...item })),
                slots,
            } : {
                skins: getTemplateSkinSelections(),
                slots,
            }, { render });
            return true;
        } catch (_) {
            return false;
        }
    }

    function getNativeTransform() {
        const look = renderLook();
        if (look?.transform) {
            const transform = look.transform;
            return {
                x: Number(transform.x) || 0,
                y: Number(transform.y) || 0,
                rotation: Number(transform.rotation) || 0,
                scale: Math.max(0.01, Number(transform.scale) || 1),
            };
        }
        const adjustments = state.adjustOverwrite ? state.adjustments : null;
        return {
            x: Number(adjustments?.x) || 0,
            y: Number(adjustments?.y) || 0,
            rotation: Number(adjustments?.rotation) || 0,
            scale: Math.max(0.01, (Number(adjustments?.scale) || 100) / 100),
        };
    }

    function syncNativeScene(scene = getSelectedScene(), options = {}) {
        if (!hasSpineRenderer() || !scene) return false;
        const autoplay = options.autoplay ?? state.animationPlaying;
        const commit = options.commit !== false;
        const animationId = options.animationId || state.animationId || scene.animation?.id;
        if (!animationId || !spineRenderer.animationNames().includes(animationId)) return false;
        const duration = getImportedAnimationDuration(animationId);
        const sceneTime = Number(scene.animation?.time) || 0;
        const progressValue = options.progress == null
            ? (Number(state.animationProgress) || 0)
            : (Number(options.progress) || 0);
        const progressTime = duration > 0
            ? duration * Math.max(0, Math.min(100, progressValue)) / 100
            : sceneTime;
        const time = progressValue > 0 ? progressTime : sceneTime;
        try {
            spineRenderer.setScene(scene, { render: false });
            spineRenderer.setTransform(getNativeTransform(), { render: false });
            spineRenderer.selectAnimation(animationId, {
                loop: true,
                time,
                autoplay,
                render: false,
            });
            if (autoplay) spineRenderer.play();
            else spineRenderer.pause();
            applyNativeCharacterState({ render: false });
            if (commit) {
                state.animationId = animationId;
                if (duration > 0) state.animationProgress = (time / duration) * 100;
            }
            spineRenderer.render();
            renderNativeOverlays();
            return true;
        } catch (_) {
            return false;
        }
    }

    /** 画布回到「正在预览的组成员」，没有预览时回到动作列表选中的那条。 */
    function syncLiveCanvas({ autoplay = state.animationPlaying } = {}) {
        if (groupPreview?.scene) {
            syncNativeScene(groupPreview.scene, {
                autoplay,
                animationId: groupPreview.animationId,
                progress: groupPreview.progress,
                commit: false,
            });
            return;
        }
        const scene = getSelectedScene();
        if (scene) syncNativeScene(scene, { autoplay });
    }

    function syncNativeTimeFromProgress() {
        if (!hasSpineRenderer()) return false;
        const duration = getImportedAnimationDuration(state.animationId);
        if (!(duration > 0)) return false;
        try {
            spineRenderer.setTime(duration * Math.max(0, Math.min(100, state.animationProgress)) / 100);
            spineRenderer.render();
            renderNativeOverlays();
            return true;
        } catch (_) {
            return false;
        }
    }

    function startSpineAnimationLoop() {
        if (!hasSpineRenderer() || !state.animationPlaying || spineAnimationFrameHandle) return;
        spineAnimationLastTimestamp = 0;
        spineAnimationFrameHandle = window.requestAnimationFrame(spineAnimationTick);
    }

    function spineAnimationTick(timestamp) {
        spineAnimationFrameHandle = null;
        if (!hasSpineRenderer() || !state.animationPlaying) return;
        if (!spineAnimationLastTimestamp) spineAnimationLastTimestamp = timestamp;
        const elapsed = Math.max(0, Math.min(250, timestamp - spineAnimationLastTimestamp));
        spineAnimationLastTimestamp = timestamp;
        const sceneSpeed = Number((groupPreview?.scene || getSelectedScene())?.animation?.speed) || 1;
        try {
            const frame = spineRenderer.tick(
                (elapsed / 1000) * Math.max(0.05, state.animationSpeed) * sceneSpeed,
                { render: true },
            );
            renderNativeOverlays();
            // 组预览在播自己的动作，进度写回 state 会把动作列表的进度条拖走
            if (!groupPreview) {
                const duration = getImportedAnimationDuration(state.animationId);
                if (duration > 0) state.animationProgress = ((frame.time % duration) / duration) * 100;
            }
            updateAnimationControls();
        } catch (_) {
            state.animationPlaying = false;
            updateAnimationControls();
            return;
        }
        spineAnimationFrameHandle = window.requestAnimationFrame(spineAnimationTick);
    }

    function populateSpineMetadata(pkg, renderer) {
        const animations = renderer.animations();
        const scenes = animations.map((animation) => ({
            id: 'animation:' + animation.name, name: animation.name,
            animation: { id: animation.name, time: 0, speed: 1, paused: true },
        }));
        const skins = renderer.skinOptions().skin?.options || [];
        const defaultSkin = skins.includes('default') ? 'default' : skins[0];
        pkg.catalogs.animations = animations.map((item) => ({ id: item.name, displayName: item.name, labels: {}, duration: item.duration }));
        pkg.catalogs.skinGroups = skins.length ? [{ id: 'skin', displayName: t('emoteMaker.spineSkin'), labels: {} }] : [];
        pkg.character.skins = defaultSkin ? [{ group: 'skin', value: defaultSkin }] : [];
        pkg.scenes = scenes;
        Object.assign(pkg.counts, { animations: animations.length, scenes: scenes.length,
            skinGroups: Number(!!skins.length), activeSkins: Number(!!skins.length) });
        Object.assign(pkg.capabilities, { spineRender: true, staticPngExport: true, animatedExport: !!animations.length,
            characterSkinSelection: !!skins.length });
    }

    function chooseSpineResource(payload) {
        return new Promise((resolve) => {
            const dialog = document.createElement('div');
            dialog.className = 'emote-confirm';
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            const card = document.createElement('div');
            card.className = 'emote-confirm-card';
            const text = document.createElement('p');
            text.textContent = t('emoteMaker.spineChooseResource');
            const select = document.createElement('select');
            select.setAttribute('aria-label', text.textContent);
            select.append(new Option(t('emoteMaker.spineChooseResource'), ''),
                ...payload.candidates.map((item) => new Option(item.path, item.path)));
            const actions = document.createElement('div');
            actions.className = 'emote-confirm-actions';
            const cancel = document.createElement('button');
            cancel.className = 'emote-confirm-cancel';
            cancel.textContent = t('emoteMaker.confirmCancel');
            const ok = document.createElement('button');
            ok.className = 'emote-confirm-ok';
            ok.textContent = t('common.confirm');
            ok.disabled = true;
            const finish = (value) => { document.removeEventListener('keydown', onKey, true); dialog.remove(); resolve(value); };
            const onKey = (event) => { if (event.key === 'Escape') { event.stopPropagation(); finish(null); } };
            select.addEventListener('change', () => { ok.disabled = !select.value; });
            ok.addEventListener('click', () => { if (select.value) finish(select.value); });
            cancel.addEventListener('click', () => finish(null));
            actions.append(cancel, ok); card.append(text, select, actions); dialog.append(card);
            document.body.append(dialog); document.addEventListener('keydown', onKey, true); cancel.focus();
        });
    }

    async function activateNativeSpineRenderer() {
        const generation = ++spineRendererGeneration;
        disposeSpineRenderer();
        if (!templatePackage || !templateAssetStore || !spineCanvas) return;
        const api = window.NekoEmoteSpineRenderer;
        if (!api || typeof api.create !== 'function') return;

        setCanvasMode(true);
        setTemplateImportStatus(t('emoteMaker.importingTemplate'));
        try {
            const renderer = await api.create({
                canvas: spineCanvas,
                manifest: templatePackage,
                assetStore: templateAssetStore,
                animationName: state.animationId,
                loop: true,
                renderOnLoad: false,
                fitPadding: 1.05,
                imageLayers: wardrobeSpineLayers,
                matchTintPalette: true,
                highlightedPart: colorPartHighlight,
            });
            if (generation !== spineRendererGeneration || !templatePackage) {
                renderer.dispose();
                return;
            }
            spineRenderer = renderer;
            populateSpineMetadata(templatePackage, renderer);
            templatePackage.capabilities = {
                ...(templatePackage.capabilities || {}),
                spineRender: true,
                // Static PNG and animated export have separate capability flags.
                nativeExport: false,
                staticPngExport: true,
                animatedExport: getImportedAnimations().length > 0 && Boolean(window.NekoEmoteGifEncoder?.toBlob),
            };
            syncNativeScene(getSelectedScene(), { autoplay: state.animationPlaying });
            const warnings = [
                ...(Array.isArray(templatePackage.warnings) ? templatePackage.warnings : []),
                ...(Array.isArray(renderer.status().warnings) ? renderer.status().warnings : []),
            ].filter((warning) => warning && !String(warning).includes('已忽略'));
            setTemplateImportStatus(
                warnings.length ? presentTemplateWarning(warnings[0]) : '',
                warnings.length ? 'warning' : 'success',
            );
            setCanvasMode(true);
            renderAll();
            if (state.animationPlaying) startSpineAnimationLoop();
        } catch (error) {
            if (generation !== spineRendererGeneration) return;
            disposeSpineRenderer();
            setTemplateImportStatus(
                t('emoteMaker.templateCapabilityWarning'),
                'warning',
            );
            renderAll();
        }
    }

    function interpolate(value, values) {
        return String(value || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => String(values?.[key] ?? ''));
    }

    function selectImportedScene(scene) {
        if (!scene) return;
        state.selectedSceneId = scene.id;
        state.animationId = scene.animation?.id || '';
        state.animationProgress = 0;
        state.animationPlaying = false;
        stopAnimationLoops();
        // Scene cards are playback choices too: selecting one should show the
        // scene's first frame immediately and start its animation when the
        // native renderer is available.
        startSelectedAnimationPlayback();
        renderAll();
        // 选中的场景也要落盘：以前只在导入和关页时存，所以"重新打开"或从模板库
        // 切回来永远落回第一个场景，用户挑的那个丢了。
        persistTemplateSession();
    }

    function restoreState(snapshot) {
        const hadPreview = Boolean(groupPreview);
        groupPreview = null;
        Object.assign(state, structuredClone(snapshot));
        forgetStageFrames();
        renderAll();
        persistTemplateSession();
        void stateStageSession?.refresh();
        if (hadPreview) {
            const scene = getSelectedScene();
            if (scene) syncNativeScene(scene, { autoplay: false });
        }
    }

    function recordChange(field, value) {
        const hadPreview = Boolean(groupPreview);
        groupPreview = null;
        const before = structuredClone(state[field]);
        state[field] = value;
        rememberHistory(before, structuredClone(value), (next) => restoreState({ [field]: next }));
        renderAll();
        if (hadPreview) {
            const scene = getSelectedScene();
            if (scene) syncNativeScene(scene, { autoplay: state.animationPlaying });
        }
    }

    function rememberHistory(before, after, apply) {
        if (JSON.stringify(before) === JSON.stringify(after)) return;
        history.push({ before, after, apply });
        future.length = 0;
        if (history.length > 40) history.shift();
        updateHistoryButtons();
    }

    async function replayHistory(from, to, value) {
        if (!from.length || historyBusy || wardrobeFileBusy || wardrobeGestureActive || packBusy || groupExportBusy
                || (capturingFrames && !stageQueueRunning) || stateStageSession?.isEditing?.()) return;
        const entry = from.at(-1);
        const editableAreas = [$('.emote-maker-page'), stateStageSession?.element, $('#wardrobe-dialog')]
            .filter((element) => element && !element.inert);
        try {
            const apply = () => from.at(-1) === entry ? entry.apply(entry[value]) : undefined;
            const pending = stageQueueRunning
                ? queueStageCapture(Symbol(), STAGE_CAPTURE_INTERACTIVE + 1, 'history', apply)
                : apply();
            if (pending) {
                historyBusy = true;
                for (const area of editableAreas) area.inert = true;
                updateHistoryButtons();
                await pending;
            }
            if (from.at(-1) === entry) {
                from.pop();
                to.push(entry);
            }
        } catch (_) {
            showToast(t('emoteMaker.wardrobeSpriteSaveFailed'));
        } finally {
            if (historyBusy) {
                historyBusy = false;
                for (const area of editableAreas) area.inert = false;
            }
            updateHistoryButtons();
        }
    }

    function undo() {
        void replayHistory(history, future, 'before');
    }

    function redo() {
        void replayHistory(future, history, 'after');
    }





    function hexToColor(value, fallback = { r: 1, g: 1, b: 1, a: 1 }) {
        const text = String(value || '').trim().replace(/^#/, '');
        if (!/^[0-9a-f]{6}$/i.test(text)) return fallback;
        return {
            r: parseInt(text.slice(0, 2), 16) / 255,
            g: parseInt(text.slice(2, 4), 16) / 255,
            b: parseInt(text.slice(4, 6), 16) / 255,
            a: 1,
        };
    }

    function applyNativeVisualState({ render = false } = {}) {
        if (!hasSpineRenderer()) return false;
        try {
            spineRenderer.setColorMode({
                mode: state.colorMode,
                primary: hexToColor(state.colors.expression),
                secondary: hexToColor(state.secondaryColor, { r: 0, g: 0, b: 0, a: 1 }),
            }, { render: false });
            spineRenderer.setTransform(getNativeTransform(), { render: false });
            const transparent = state.exportTransparent !== false;
            const layered = templatePackage?.source?.builtIn === true;
            spineRenderer.setBackground(transparent || layered ? [0, 0, 0, 0] : [0.96, 0.98, 1, 1], { render: false });
            canvasFrame.classList.toggle('wardrobe-solid-background', layered && !transparent);
            if (render) spineRenderer.render();
            if (render) renderNativeOverlays();
            return true;
        } catch (_) {
            return false;
        }
    }



    function createTemplateLibraryCard(entry) {
        const card = document.createElement('div');
        card.className = 'asset-card template-lib-card';
        card.dataset.templateKey = entry.key;
        if (entry.builtIn) card.dataset.templateBuiltIn = 'true';
        const isCurrent = entry.key === currentTemplateKey();
        if (isCurrent) card.classList.add('is-selected');

        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'template-lib-open';
        const label = document.createElement('span');
        label.className = 'asset-card-label';
        // 显示名的裁决在库里（用户改的名 > 来源名 > 模板自报名 > 包名），别在这再写一条链
        label.textContent = templateLibraryModule.entryLabel(entry)
            || t('emoteMaker.templateSceneList');
        open.append(label);
        // 单击=装回模板，双击=改名：不延迟的话，双击的头一下已经把模板切过去了。
        // 240ms 的延迟对一次重导入级的操作无感。当前使用中的那条点了没去处，就地挡住。
        // 处理器放在**卡片层**而不是内层按钮上：卡片有 padding，点边缘时 target 是
        // 卡片自己，绑在按钮上那 7~8px 的边就点不动。移除钮和改名框各自挡掉。
        let openTimer = 0;
        card.addEventListener('click', (event) => {
            if (event.target.closest('.template-lib-remove')) return;
            if (event.target.closest('.template-lib-rename')) return;
            if (isCurrent || packBusy) return;
            clearTimeout(openTimer);
            openTimer = setTimeout(() => { void switchTemplate(entry.key); }, 240);
        });
        // 副本占多少就写多少：真实数字（后端 stat 来的），不是估的
        const archiveBytes = templateArchiveBytes.get(entry.key) || 0;
        if (archiveBytes > 0) {
            const size = document.createElement('small');
            size.className = 'template-lib-size';
            size.textContent = formatTemplateBytes(archiveBytes);
            open.append(size);
        }
        // 不能用 disabled 挡"当前使用中"：禁用按钮连同后代根本不派发指针事件，
        // 双击改名就永远落不到卡片上（当场踩过）。视觉用 aria-disabled 走同一套样式，
        // 行为在 click 处理器里挡。
        open.setAttribute('aria-label', `${label.textContent}${isCurrent ? ` · ${t('emoteMaker.templateInUse')}` : ''}`);
        const removing = pendingTemplateRemovals.has(entry.key);
        open.disabled = packBusy || removing;
        if (isCurrent) open.setAttribute('aria-disabled', 'true');

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'template-lib-remove';
        removeButton.dataset.templateKey = entry.key;
        removeButton.disabled = packBusy || removing;
        removeButton.setAttribute('aria-label', `${t('emoteMaker.removeTemplate')} · ${label.textContent}`);
        removeButton.textContent = '×';
        removeButton.addEventListener('click', (event) => {
            event.stopPropagation();
            removeTemplate(entry.key);
        });

        // 双击（整张卡都行，当前使用中的那条打开按钮是灰的也要能改名）= 就地改名；
        // 点在移除上不算。双击要先取消那个"单击切换"的定时器。
        card.addEventListener('dblclick', (event) => {
            if (entry.builtIn || event.target.closest('.template-lib-remove') || pendingTemplateRemovals.has(entry.key)) return;
            event.preventDefault();
            clearTimeout(openTimer);
            beginTemplateRename(entry.key, card);
        });

        // 完整名字给 title：一行装不下时鼠标悬停还能看全
        open.title = label.textContent;
        card.append(open);
        if (isCurrent || entry.builtIn) {
            const badge = document.createElement('span');
            badge.className = 'template-lib-badge';
            badge.textContent = t(isCurrent ? 'emoteMaker.templateInUse' : 'emoteMaker.builtinTemplate');
            card.append(badge);
        }
        if (!entry.builtIn) card.append(removeButton);
        return card;
    }

    /**
     * 双击卡片就地改名：整张卡换成一个输入框（input 不能住在 button 里——
     * 点它定位光标会触发"打开模板"），Enter/失焦提交，Esc 放弃。
     * 改成空 = 回到默认名。只动 alias，键不动 —— 改名是外观，不是身份，
     * 本地副本、会话、组都不跟着挪。
     */
    function beginTemplateRename(key, card) {
        const current = templateLibraryModule
            ? templateLibraryModule.entryLabel(
                templateLibraryModule.find(templateLibraryState, key)) : '';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'template-lib-rename';
        input.value = current;
        input.maxLength = templateLibraryModule?.MAX_LABEL_CHARS || 60;
        input.setAttribute('aria-label', t('emoteMaker.renameTemplate'));
        input.dataset.key = key;
        card.replaceChildren(input);
        input.focus();
        input.select();
        let done = false;
        const finish = (commit) => {
            if (done) return;
            done = true;
            const value = commit ? input.value : '';
            if (commit && value.trim() !== current.trim()) {
                templateLibraryState = templateLibraryModule.renameEntry(
                    templateLibraryState, key, value);
                try {
                    templateLibraryModule.save(localStorage, templateLibraryState);
                } catch (_) { /* 存不下只丢这一次改名 */ }
                renderAll();
                return;
            }
            renderTemplateLibraryGrid();
        };
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                finish(true);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                finish(false);
            }
            event.stopPropagation();
        });
        input.addEventListener('blur', () => finish(true));
        input.addEventListener('dblclick', (event) => event.stopPropagation());
    }

    function renderTemplateLibraryGrid() {
        if (!assetSectionTitle || !assetGrid) return;
        assetGrid.classList.add('is-template-library');
        assetSectionTitle.textContent = t('emoteMaker.templateLibrary');
        const entries = [
            ...[...builtinTemplates.keys()].map(findTemplateLibraryEntry),
            ...(templateLibraryState?.entries || []).filter((entry) => !entry.key.startsWith('builtin:')),
        ];
        const query = String(state.assetQuery || '').trim().toLocaleLowerCase();
        const shown = query
            ? entries.filter((entry) => `${entry.displayName || ''} ${entry.packageName || ''}`
                .toLocaleLowerCase().includes(query))
            : entries;
        if (!shown.length) {
            const empty = document.createElement('div');
            empty.className = 'empty-template-state';
            empty.setAttribute('role', 'status');
            empty.textContent = query ? t('emoteMaker.noTemplates') : t('emoteMaker.templateLibraryEmpty');
            assetGrid.replaceChildren(empty);
            return;
        }
        assetGrid.replaceChildren(...shown.map(createTemplateLibraryCard));
    }



    // 下拉只有一处构建者：以前这里和 renderGroupBar 各写一份，renderAnimations 之后
    // 跑到哪儿就把选项盖回哪份，界面上出现的是"场景列表 / ModelA (3)"这种老文案。
    function renderBatchExportGroupSelect() {
        if (!batchExportGroupSelect) return;
        if (!templatePackage) {
            batchExportGroupSelect.replaceChildren();
            return;
        }
        // 预览下方那份动画列表不再跟组联动：组只管"导出哪几张"，看动作归看动作
        const groups = allGroups();
        const options = groups.length
            ? []
            : [new Option(t('emoteMaker.groupEmptyList'), '')];
        groups.forEach((group) => {
            const option = new Option(
                group.readOnly && group.count ? `${group.name} · ${group.count}` : group.name,
                group.id,
            );
            if (!group.readOnly) {
                option.dataset.writable = '1';
                option.dataset.renameLabel = t('emoteMaker.groupRename');
                option.dataset.deleteLabel = t('emoteMaker.groupDelete');
            }
            options.push(option);
        });
        batchExportGroupSelect.replaceChildren(...options);
        batchExportGroupSelect.value = state.groupId;
    }

    function renderAssetGrid() {
        if (!assetGrid || !assetSectionTitle) return;
        // 左侧永远是模板库：加载了模板它是"最近用过"，没加载它就是入口本身
        //（点一条 = 从本地副本装回来）。以前的部件网格依赖 EXPRESSIONS 那套
        // 永远为空的脚手架，早就只剩一排点不动的分类。
        renderTemplateLibraryGrid();
    }




    function normalizeTemplateToken(value) {
        return String(value || '')
            .replace(/\[[^\]]*\]/g, '')
            .replace(/[\s_\-]+/g, '')
            .toLocaleLowerCase();
    }

    function renderTemplateSkinControls() {
        if (!templateSkinControls) return;
        if (!templatePackage) {
            templateSkinControls.hidden = true;
            templateSkinControls.replaceChildren();
            return;
        }
        const groups = Array.isArray(templatePackage.catalogs?.skinGroups)
            ? templatePackage.catalogs.skinGroups
            : [];
        const selections = getTemplateSkinSelections();
        const runtimeOptions = getTemplateSkinOptions();
        const children = [];
        groups.forEach((group) => {
            const groupId = String(group?.id || group?.displayName || '');
            if (!groupId) return;
            const selected = selections.find((item) => normalizeTemplateToken(item.group) === normalizeTemplateToken(groupId));
            const runtimeGroup = runtimeOptions[normalizeTemplateToken(groupId)];
            const values = Array.isArray(runtimeGroup?.options) && runtimeGroup.options.length
                ? runtimeGroup.options
                : (selected?.value ? [selected.value] : []);
            const row = document.createElement('label');
            row.className = 'template-compat-row';
            row.title = getCatalogLabel(group) || groupId;
            const name = document.createElement('span');
            name.textContent = getCatalogLabel(group) || groupId;
            const select = document.createElement('select');
            select.dataset.templateSkinGroup = groupId;
            select.disabled = !values.length || !hasSpineRenderer();
            if (!values.length) {
                select.appendChild(new Option(t('emoteMaker.templateMetadataOnly'), ''));
            } else {
                values.forEach((value) => {
                    const option = new Option(value, value);
                    option.selected = value === selected?.value;
                    select.appendChild(option);
                });
            }
            select.addEventListener('change', () => {
                const value = select.value;
                if (!value) return;
                const next = selections.filter((item) => normalizeTemplateToken(item.group) !== normalizeTemplateToken(groupId));
                next.push({ group: groupId, value });
                recordChange('templateSkins', next);
                applyNativeCharacterState();
                persistTemplateSession();
            });
            row.append(name, select);
            children.push(row);
        });
        if (!hasSpineRenderer()) {
            const note = document.createElement('p');
            note.className = 'template-compat-note';
            note.textContent = t('emoteMaker.templateMetadataOnly');
            children.push(note);
        }
        templateSkinControls.hidden = !children.length;
        templateSkinControls.replaceChildren(...children);
    }

    function filterTemplateAttachments() {
        const query = templateAttachmentSearch.value.trim().toLocaleLowerCase();
        let hasMatch = false;
        for (const row of templateAttachmentControls.children) {
            row.hidden = !row.dataset.searchText.includes(query);
            hasMatch ||= !row.hidden;
        }
        templateAttachmentEmpty.hidden = !query || templateAttachmentSearch.hidden || hasMatch;
    }

    function renderPartGroups() {
        const wardrobeActions = $('#wardrobe-entry-actions');
        if (wardrobeActions) wardrobeActions.hidden = templatePackage?.source?.builtIn !== true || !hasSpineRenderer();
        syncWardrobePlacements();
        renderTemplateSkinControls();
        if (!templateAttachmentControls) return;
        const parts = (templatePackage?.catalogs?.slotGroups || []).filter((part) => part.kind !== 'tint');
        const styles = getTemplateSlotStyles();
        const rows = parts.map((part) => {
            const style = styles.find((item) => item.name === part.id);
            const row = document.createElement('label');
            row.className = 'template-compat-row';
            const title = document.createElement('span');
            title.textContent = getCatalogLabel(part);
            const searchTerms = [title.textContent, part.id];
            const select = document.createElement('select');
            select.dataset.templateAttachmentSlot = part.id;
            select.disabled = !hasSpineRenderer();
            select.add(new Option(t('emoteMaker.partFollowAnimation'), 'auto'));
            if (part.kind === 'toggle') {
                select.add(new Option(t('common.on'), 'show'));
                select.add(new Option(t('common.off'), 'hide'));
            } else {
                for (const attachment of part.attachments) {
                    const label = getCatalogLabel({ id: attachment, labels: part.attachmentLabels?.[attachment] });
                    select.add(new Option(label, `attachment:${attachment}`));
                    searchTerms.push(label, attachment);
                }
            }
            const value = style?.attachmentMode === 'replace' ? `attachment:${style.attachment}` : style?.attachmentMode;
            select.value = [...select.options].some((option) => option.value === value) ? value : 'auto';
            select.addEventListener('change', () => {
                const next = getTemplateSlotStyles();
                const target = next.find((item) => item.name === part.id);
                if (!target) return;
                const replacement = select.value.startsWith('attachment:');
                target.attachmentMode = replacement ? 'replace' : select.value;
                target.attachment = replacement ? select.value.slice('attachment:'.length) : null;
                recordChange('templateSlots', next);
                persistTemplateSession();
            });
            row.dataset.searchText = searchTerms.join('\n').toLocaleLowerCase();
            row.append(title, select);
            return row;
        });
        templateAttachmentSearch.hidden = !rows.length;
        if (!rows.length) templateAttachmentSearch.value = '';
        templateAttachmentControls.hidden = !rows.length;
        templateAttachmentControls.replaceChildren(...rows);
        filterTemplateAttachments();
    }

    function wardrobePoint(event, surface, bounded = true) {
        const box = surface.getBoundingClientRect();
        const x = (event.clientX - box.left) / box.width;
        const y = (event.clientY - box.top) / box.height;
        const clamp = (value) => Math.max(0, Math.min(1, value));
        return bounded ? { x: clamp(x), y: clamp(y) } : { x, y };
    }

    function bindWardrobeCropBoxes(layer, { canEdit, limit, replace = false, onChange, onCommit }) {
        const boxes = [];
        const roundBounds = (bounds) => bounds.map((value) => Number(value.toFixed(4)));
        const edges = { nw: [0, 0], n: [50, 0], ne: [100, 0], e: [100, 50],
            se: [100, 100], s: [50, 100], sw: [0, 100], w: [0, 50] };
        let selected = null;
        let drag = null;
        const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

        function select(box) {
            selected?.element.classList.remove('is-selected');
            selected = box;
            selected?.element.classList.add('is-selected');
        }

        function position(box, bounds) {
            box.bounds = bounds;
            const [left, top, right, bottom] = bounds;
            Object.assign(box.element.style, {
                left: `${left * 100}%`, top: `${top * 100}%`,
                width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`,
            });
        }

        function create(point) {
            const element = document.createElement('div');
            element.className = 'wardrobe-crop-box is-drawing';
            element.tabIndex = 0;
            element.setAttribute('role', 'group');
            element.dataset.i18nAria = 'emoteMaker.wardrobeCropBox';
            element.setAttribute('aria-label', t(element.dataset.i18nAria));
            for (const [edge, [x, y]] of Object.entries(edges)) {
                const handle = document.createElement('span');
                handle.className = 'wardrobe-crop-handle';
                handle.dataset.edge = edge;
                handle.style.left = `${x}%`;
                handle.style.top = `${y}%`;
                element.append(handle);
            }
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'wardrobe-crop-remove';
            remove.textContent = '×';
            remove.dataset.i18nAria = remove.dataset.i18nTitle = 'emoteMaker.wardrobeCropDelete';
            remove.title = t(remove.dataset.i18nTitle);
            remove.setAttribute('aria-label', remove.title);
            element.append(remove);
            const box = { element, remove };
            remove.addEventListener('click', () => { if (canEdit()) removeBox(box); });
            position(box, [point.x, point.y, point.x, point.y]);
            layer.append(element);
            return box;
        }

        function move([left, top, right, bottom], dx, dy) {
            dx = clamp(dx, -left, 1 - right);
            dy = clamp(dy, -top, 1 - bottom);
            return [left + dx, top + dy, right + dx, bottom + dy];
        }

        function updateDrag(event) {
            const { box, start, original, edge } = drag;
            if (!original) {
                const point = wardrobePoint(event, layer);
                position(box, [Math.min(start.x, point.x), Math.min(start.y, point.y),
                    Math.max(start.x, point.x), Math.max(start.y, point.y)]);
                return;
            }
            // A handle extends past the box border. Clamp the resulting box,
            // rather than the pointer, so even an off-center grab can reach the image edge.
            const point = wardrobePoint(event, layer, false);
            const dx = point.x - start.x;
            const dy = point.y - start.y;
            if (!edge) {
                position(box, move(original, dx, dy));
                return;
            }
            let [left, top, right, bottom] = original;
            const minX = Math.min(right - left, Math.max(0.005, 5 / layer.clientWidth));
            const minY = Math.min(bottom - top, Math.max(0.005, 5 / layer.clientHeight));
            if (edge.includes('w')) left = clamp(left + dx, 0, right - minX);
            if (edge.includes('e')) right = clamp(right + dx, left + minX, 1);
            if (edge.includes('n')) top = clamp(top + dy, 0, bottom - minY);
            if (edge.includes('s')) bottom = clamp(bottom + dy, top + minY, 1);
            position(box, [left, top, right, bottom]);
        }

        function removeBox(box) {
            const index = boxes.indexOf(box);
            if (index < 0 || drag) return;
            const keepFocus = box.element.contains(document.activeElement);
            boxes.splice(index, 1)[0].element.remove();
            const next = selected === box ? boxes[Math.min(index, boxes.length - 1)] || null : selected;
            select(next);
            if (keepFocus) (next?.element || layer).focus({ preventScroll: true });
            onChange(true);
        }

        function stopDrag() {
            const finished = drag;
            drag = null;
            finished.events.abort();
            if (layer.hasPointerCapture(finished.pointerId)) layer.releasePointerCapture(finished.pointerId);
            return finished;
        }

        function cancel() {
            if (!drag) return;
            const { box, original, previous } = stopDrag();
            if (original) position(box, original);
            else box.element.remove();
            select(previous);
        }

        function clear() {
            cancel();
            select(null);
            for (const box of boxes.splice(0)) box.element.remove();
        }

        function finishDrag(event) {
            if (event) updateDrag(event);
            const { box, original, previous } = stopDrag();
            const [left, top, right, bottom] = box.bounds;
            let changed = Boolean(original?.some((value, index) => value !== box.bounds[index]));
            if (!original) {
                if (right - left >= 0.005 && bottom - top >= 0.005
                        && (right - left) * layer.clientWidth >= 5 && (bottom - top) * layer.clientHeight >= 5) {
                    if (replace) for (const old of boxes.splice(0)) old.element.remove();
                    boxes.push(box);
                    box.element.classList.remove('is-drawing');
                    changed = true;
                    box.element.focus({ preventScroll: true });
                } else {
                    box.element.remove();
                    select(previous);
                }
            }
            onChange(changed);
            if (changed && onCommit) onCommit(roundBounds(box.bounds));
        }

        function continueDrag(event) {
            if (drag?.pointerId !== event.pointerId) return;
            if (!(event.buttons & 1)) {
                // The button may have been released while the pointer was outside the window.
                finishDrag();
                return;
            }
            if (!layer.hasPointerCapture(event.pointerId)) layer.setPointerCapture(event.pointerId);
            updateDrag(event);
        }

        function cancelDrag() {
            cancel();
            onChange();
        }

        layer.addEventListener('pointerdown', (event) => {
            if (!canEdit() || drag || event.button !== 0 || event.target.closest('.wardrobe-crop-remove')) return;
            const existing = boxes.find(({ element }) => element === event.target.closest('.wardrobe-crop-box'));
            if (!existing && !replace && boxes.length >= limit()) return;
            const start = wardrobePoint(event, layer, !existing);
            const box = existing || create(start);
            const events = new AbortController();
            drag = { box, start, previous: selected, original: existing?.bounds, pointerId: event.pointerId,
                edge: event.target.closest('.wardrobe-crop-handle')?.dataset.edge, events };
            select(box);
            // Keep tracking the gesture if the host releases capture while the pointer is still down.
            const tracking = { capture: true, signal: events.signal };
            window.addEventListener('pointermove', continueDrag, tracking);
            window.addEventListener('pointerup', (event) => {
                if (drag?.pointerId === event.pointerId && event.button === 0) finishDrag(event);
            }, tracking);
            window.addEventListener('pointercancel', (event) => {
                if (drag?.pointerId === event.pointerId) cancelDrag();
            }, tracking);
            window.addEventListener('blur', cancelDrag, { signal: events.signal });
            layer.setPointerCapture(event.pointerId);
            event.preventDefault();
            (existing ? box.element : layer).focus({ preventScroll: true });
            onChange();
        });
        layer.addEventListener('focusin', (event) => {
            const box = boxes.find(({ element }) => element === event.target);
            if (box && !drag) {
                select(box);
                onChange();
            }
        });
        layer.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && drag) {
                event.preventDefault();
                cancelDrag();
                return;
            }
            if (!canEdit() || drag || !selected) return;
            const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
            if (direction) {
                event.preventDefault();
                const step = event.shiftKey ? 10 : 1;
                position(selected, move(selected.bounds, direction[0] * step / layer.clientWidth,
                    direction[1] * step / layer.clientHeight));
                onChange(true);
            } else if (event.key === 'Delete' || event.key === 'Backspace') {
                event.preventDefault();
                removeBox(selected);
            }
        });

        return {
            clear, cancel,
            setDisabled(disabled) {
                layer.setAttribute('aria-disabled', String(disabled));
                for (const box of boxes) box.remove.disabled = disabled;
            },
            get editing() { return Boolean(drag); },
            get count() { return boxes.length; },
            get selection() { return !drag && selected ? selected.bounds : null; },
            serialize() {
                return boxes.length ? JSON.stringify(boxes.map(({ bounds }) => roundBounds(bounds))) : '';
            },
        };
    }

    function bindWardrobeSourceCrops({ reference, onChange, onCommit }) {
        const dialog = $('#wardrobe-dialog');
        const panel = $('#wardrobe-source-pane');
        const empty = $('#wardrobe-source-empty');
        const viewport = $('#wardrobe-annotation-viewport');
        const surface = $('#wardrobe-annotation-surface');
        const layer = $('#wardrobe-annotation-crops');
        const hint = $('#wardrobe-annotation-hint');
        const count = $('#wardrobe-annotation-count');
        const clear = $('#wardrobe-annotation-clear');
        const zoomIn = $('#wardrobe-annotation-zoom-in');
        const zoomOut = $('#wardrobe-annotation-zoom-out');
        const fit = $('#wardrobe-annotation-fit');
        const scaleLabel = $('#wardrobe-annotation-scale');
        const pan = $('#wardrobe-annotation-pan');
        const minimap = $('#wardrobe-minimap');
        const minimapSurface = $('#wardrobe-minimap-image');
        const minimapImage = minimapSurface.querySelector('img');
        const minimapWindow = $('#wardrobe-minimap-window');
        const maxZoom = 8;
        let limit = 3;
        let image = null;
        let loading = null;
        let sourceKey = null;
        let loadFailed = false;
        let imageUrl = '';
        let disabled = false;
        let zoom = 1;
        let panMode = false;
        let panDrag = null;
        let minimapDrag = null;
        const crops = bindWardrobeCropBoxes(layer, {
            canEdit: () => Boolean(image && !disabled && !panMode && !panDrag),
            limit: () => limit,
            onChange,
            onCommit: ([x1, y1, x2, y2]) => {
                if (!image) return;
                const { naturalWidth: width, naturalHeight: height } = image;
                const left = Math.min(Math.round(x1 * width), width - 1);
                const top = Math.min(Math.round(y1 * height), height - 1);
                const w = Math.max(1, Math.round(x2 * width) - left);
                const h = Math.max(1, Math.round(y2 * height) - top);
                onCommit(w, h);
            },
        });

        function render() {
            layer.hidden = !image;
            crops.setDisabled(disabled || panMode);
            empty.hidden = Boolean(sourceKey);
            hint.hidden = !loadFailed;
            if (loadFailed) {
                const key = sourceKey.size > 8 * 1024 * 1024 ? 'wardrobeInvalidImage' : 'wardrobeMarkImageFailed';
                hint.textContent = t(`emoteMaker.${key}`);
            }
            const counter = `${crops.count}/${limit}`;
            if (count.value !== counter) count.value = counter;
            clear.disabled = disabled || crops.editing || !crops.count;
            zoomIn.disabled = !image || zoom >= maxZoom;
            zoomOut.disabled = fit.disabled = !image || zoom <= 1;
            pan.disabled = !image;
            scaleLabel.value = `${Math.round(zoom * 100)}%`;
            minimap.hidden = !image || panel.hidden;
            if (!minimap.hidden) renderMinimap();
        }

        function updateMinimapWindow() {
            if (!image || minimap.hidden) return;
            const photo = surface.getBoundingClientRect();
            if (!photo.width || !photo.height) return;
            const view = viewport.getBoundingClientRect();
            const clamp = (value) => Math.max(0, Math.min(1, value));
            const left = clamp((view.left + viewport.clientLeft - photo.left) / photo.width);
            const top = clamp((view.top + viewport.clientTop - photo.top) / photo.height);
            const right = clamp((view.left + viewport.clientLeft + viewport.clientWidth - photo.left) / photo.width);
            const bottom = clamp((view.top + viewport.clientTop + viewport.clientHeight - photo.top) / photo.height);
            Object.assign(minimapWindow.style, {
                left: `${left * 100}%`, top: `${top * 100}%`,
                width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`,
            });
        }

        function renderMinimap() {
            if (minimap.hidden || !image) return;
            const padding = Number.parseFloat(getComputedStyle(minimap).paddingLeft) * 2;
            const scale = Math.min((minimap.clientWidth - padding) / image.naturalWidth,
                (minimap.clientHeight - padding) / image.naturalHeight);
            minimapSurface.style.width = `${Math.max(1, Math.floor(image.naturalWidth * scale))}px`;
            minimapSurface.style.height = `${Math.max(1, Math.floor(image.naturalHeight * scale))}px`;
            updateMinimapWindow();
        }

        function centerOnMinimap(event) {
            const location = wardrobePoint(event, minimapSurface);
            const photo = surface.getBoundingClientRect();
            const view = viewport.getBoundingClientRect();
            viewport.scrollLeft += photo.left + location.x * photo.width - view.left - viewport.clientLeft - viewport.clientWidth / 2;
            viewport.scrollTop += photo.top + location.y * photo.height - view.top - viewport.clientTop - viewport.clientHeight / 2;
            updateMinimapWindow();
        }

        function renderView() {
            if (!image || panel.hidden || !dialog.open) return;
            const padding = Number.parseFloat(getComputedStyle(surface.parentElement).paddingLeft) * 2;
            const fitScale = Math.min((viewport.offsetWidth - padding) / image.naturalWidth,
                (viewport.offsetHeight - padding) / image.naturalHeight);
            const width = Math.max(1, Math.floor(image.naturalWidth * fitScale * zoom));
            const height = Math.max(1, Math.floor(image.naturalHeight * fitScale * zoom));
            surface.style.width = `${width}px`;
            surface.style.height = `${height}px`;
            render();
        }

        function changeZoom(value, anchor) {
            if (!image || crops.editing || panDrag) return;
            const next = Math.max(1, Math.min(maxZoom, value));
            if (next === zoom) return;
            const view = viewport.getBoundingClientRect();
            const cursor = anchor || { clientX: view.left + view.width / 2, clientY: view.top + view.height / 2 };
            const location = wardrobePoint(cursor, surface);
            zoom = next;
            renderView();
            const box = surface.getBoundingClientRect();
            viewport.scrollLeft += box.left + location.x * box.width - cursor.clientX;
            viewport.scrollTop += box.top + location.y * box.height - cursor.clientY;
            updateMinimapWindow();
        }

        async function loadImage() {
            if (!dialog.open || panel.hidden || !sourceKey || image || loading || loadFailed) return;
            const file = sourceKey;
            if (file.size > 8 * 1024 * 1024) {
                loadFailed = true;
                render();
                return;
            }
            const source = new Image();
            const url = URL.createObjectURL(file);
            loading = source;
            source.src = url;
            try {
                await source.decode();
                if (loading !== source) return;
                source.alt = '';
                source.draggable = false;
                image = source;
                imageUrl = url;
                minimapImage.src = url;
                loading = null;
                surface.prepend(source);
                surface.hidden = false;
                renderView();
            } catch (_) {
                if (loading === source) {
                    loadFailed = true;
                    render();
                }
            } finally {
                if (imageUrl !== url) URL.revokeObjectURL(url);
            }
        }

        clear.addEventListener('click', () => { crops.clear(); onChange(true); });
        dialog.addEventListener('close', () => {
            if (!image) {
                loading = null;
                loadFailed = false;
            }
            crops.cancel();
            panDrag = null;
            minimapDrag = null;
        });
        zoomIn.addEventListener('click', () => changeZoom(zoom * 1.25));
        zoomOut.addEventListener('click', () => changeZoom(zoom / 1.25));
        fit.addEventListener('click', () => changeZoom(1));
        viewport.addEventListener('wheel', (event) => {
            if (!image || !event.deltaY) return;
            event.preventDefault();
            changeZoom(zoom * (event.deltaY < 0 ? 1.25 : 0.8), event);
        }, { passive: false });
        pan.addEventListener('click', () => {
            panMode = !panMode;
            pan.setAttribute('aria-pressed', String(panMode));
            viewport.classList.toggle('is-panning', panMode);
            render();
        });
        viewport.addEventListener('pointerdown', (event) => {
            if (!image || panDrag || crops.editing || (event.button !== 1 && !(panMode && event.button === 0))) return;
            event.preventDefault();
            panDrag = {
                pointerId: event.pointerId, x: event.clientX, y: event.clientY,
                left: viewport.scrollLeft, top: viewport.scrollTop,
            };
            viewport.setPointerCapture(event.pointerId);
        });
        viewport.addEventListener('pointermove', (event) => {
            if (panDrag?.pointerId !== event.pointerId) return;
            viewport.scrollLeft = panDrag.left + panDrag.x - event.clientX;
            viewport.scrollTop = panDrag.top + panDrag.y - event.clientY;
        });
        viewport.addEventListener('lostpointercapture', (event) => {
            if (panDrag?.pointerId === event.pointerId) panDrag = null;
        });
        viewport.addEventListener('scroll', updateMinimapWindow, { passive: true });
        minimapSurface.addEventListener('pointerdown', (event) => {
            if (!image || event.button !== 0 || crops.editing || minimapDrag !== null) return;
            minimapDrag = event.pointerId;
            minimapSurface.setPointerCapture(event.pointerId);
            centerOnMinimap(event);
        });
        minimapSurface.addEventListener('pointermove', (event) => {
            if (minimapDrag === event.pointerId) centerOnMinimap(event);
        });
        minimapSurface.addEventListener('lostpointercapture', (event) => {
            if (minimapDrag === event.pointerId) minimapDrag = null;
        });
        minimapSurface.addEventListener('keydown', (event) => {
            const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
            if (!direction || crops.editing) return;
            event.preventDefault();
            viewport.scrollLeft += direction[0] * viewport.clientWidth / 10;
            viewport.scrollTop += direction[1] * viewport.clientHeight / 10;
        });
        new ResizeObserver(renderView).observe(viewport);
        new ResizeObserver(renderMinimap).observe(minimap);

        function reset() {
            image?.remove();
            minimapImage.removeAttribute('src');
            if (imageUrl) URL.revokeObjectURL(imageUrl);
            imageUrl = '';
            image = loading = sourceKey = null;
            loadFailed = false;
            zoom = 1;
            viewport.scrollLeft = viewport.scrollTop = 0;
            crops.clear();
            surface.hidden = true;
        }

        return {
            update(busy, regionLimit) {
                const nextSource = reference.files[0] || null;
                if (sourceKey !== nextSource) {
                    reset();
                    sourceKey = nextSource;
                }
                disabled = busy;
                limit = regionLimit;
                render();
                void loadImage();
            },
            get count() { return crops.count; },
            get editing() { return crops.editing; },
            serialize: crops.serialize,
        };
    }

    function bindWardrobeSpritePicker({ canEdit, onChange }) {
        const viewport = $('#wardrobe-spritesheet-viewport');
        const surface = $('#wardrobe-spritesheet-surface');
        const layer = $('#wardrobe-spritesheet-picker');
        let image = null;
        const crops = bindWardrobeCropBoxes(layer, {
            canEdit: () => Boolean(image && canEdit()),
            replace: true,
            onChange: update,
        });

        function render() {
            if (!image || !viewport.clientWidth || !viewport.clientHeight) return;
            const padding = Number.parseFloat(getComputedStyle(viewport).paddingLeft) * 2;
            const scale = Math.min((viewport.clientWidth - padding) / image.naturalWidth,
                (viewport.clientHeight - padding) / image.naturalHeight);
            surface.style.width = `${Math.max(1, Math.floor(image.naturalWidth * scale))}px`;
            surface.style.height = `${Math.max(1, Math.floor(image.naturalHeight * scale))}px`;
        }

        function update() {
            crops.setDisabled(!canEdit());
            onChange();
        }

        new ResizeObserver(render).observe(viewport);

        return {
            setImage(next) {
                image?.remove();
                image = next;
                if (image) {
                    image.alt = '';
                    image.draggable = false;
                    surface.prepend(image);
                }
                crops.clear();
                render();
                update();
            },
            clearSelection() { crops.clear(); update(); },
            update,
            cancel() { crops.cancel(); update(); },
            get selected() { return Boolean(crops.selection); },
            get editing() { return crops.editing; },
            async crop() {
                const selection = crops.selection;
                if (!image || !selection) return null;
                const [left, top, right, bottom] = selection.map((value, index) =>
                    Math.round(value * (index % 2 ? image.naturalHeight : image.naturalWidth)));
                const output = document.createElement('canvas');
                output.width = Math.max(1, right - left);
                output.height = Math.max(1, bottom - top);
                const pixels = output.getContext('2d', { willReadFrequently: true });
                pixels.drawImage(image, left, top, output.width, output.height, 0, 0, output.width, output.height);
                const data = pixels.getImageData(0, 0, output.width, output.height).data;
                let x1 = output.width, y1 = output.height, x2 = 0, y2 = 0;
                for (let y = 0; y < output.height; y += 1) {
                    for (let x = 0; x < output.width; x += 1) {
                        if (data[(y * output.width + x) * 4 + 3] === 0) continue;
                        x1 = Math.min(x1, x);
                        y1 = Math.min(y1, y);
                        x2 = Math.max(x2, x + 1);
                        y2 = Math.max(y2, y + 1);
                    }
                }
                if (x2 <= x1 || y2 <= y1) return null;
                output.width = x2 - x1;
                output.height = y2 - y1;
                pixels.drawImage(image, left + x1, top + y1, output.width, output.height, 0, 0, output.width, output.height);
                return new Promise((resolve) => output.toBlob(resolve, 'image/png'));
            },
        };
    }

    const WARDROBE_SPRITE_PREFIX = 'WardrobeSprites/';
    const wardrobeSprites = new Map();
    const wardrobeArtwork = $('#wardrobe-artwork');
    const wardrobeArtworkBehind = $('#wardrobe-artwork-behind');
    const wardrobeTransform = $('#wardrobe-transform');
    const wardrobeSpriteMenu = $('#wardrobe-sprite-menu');
    const wardrobeLayerUp = $('#wardrobe-layer-up');
    const wardrobeLayerDown = $('#wardrobe-layer-down');
    const wardrobeLayerSlot = $('#wardrobe-layer-slot');
    let wardrobeSpritesLoaded = false;
    let wardrobeTemplateKey = '';
    let selectedWardrobeSprite = null;
    let wardrobeFileBusy = false;
    let wardrobeGestureActive = false;

    function wardrobePlacementKey() {
        return `neko.emote.wardrobeSprites.${wardrobeTemplateKey}`;
    }

    function wardrobePlacement({ id, x, y, size, basis, anchor, bindingMode, behind, aboveSlot }) {
        return structuredClone({ id, x, y, size, basis, anchor, bindingMode, behind, aboveSlot });
    }

    function wardrobeSnapshot() {
        return [...wardrobeSprites.values()].map(wardrobePlacement);
    }

    function persistWardrobePlacements() {
        forgetStageFrames();
        void stateStageSession?.refresh();
        try {
            localStorage.setItem(wardrobePlacementKey(), JSON.stringify(wardrobeSnapshot()));
        } catch (_) {
            showToast(t('emoteMaker.wardrobeSpriteSaveFailed'));
        }
    }

    function restoreWardrobeSnapshot(placements) {
        const order = [];
        for (const placement of placements) {
            const sprite = wardrobeSprites.get(placement.id);
            if (!sprite) continue;
            Object.assign(sprite, structuredClone(placement));
            updateWardrobeSprite(sprite, false);
            order.push(sprite);
        }
        setWardrobeOrder(order);
        selectWardrobeSprite(selectedWardrobeSprite);
        renderWardrobeArtwork();
        if (!wardrobeSpriteMenu.hidden) {
            const selected = wardrobeSprites.get(selectedWardrobeSprite);
            if (selected) renderWardrobeBinding(selected);
            updateWardrobeLayerActions();
        }
        persistWardrobePlacements();
    }

    function commitWardrobeChange(before) {
        persistWardrobePlacements();
        rememberHistory(before, wardrobeSnapshot(), restoreWardrobeSnapshot);
    }

    function wardrobeSpriteGeometry(sprite) {
        const longest = Math.max(sprite.image.naturalWidth, sprite.image.naturalHeight);
        return {
            width: sprite.size * sprite.image.naturalWidth / longest,
            height: sprite.size * sprite.image.naturalHeight / longest,
        };
    }

    function wardrobeImageReady(image) {
        // Dimensions can arrive before the PNG data is complete. Uploading it then caches an empty WebGL texture.
        return Boolean(image?.complete && image.naturalWidth && image.naturalHeight);
    }

    function multiplyWardrobeMatrix([a, b, c, d, x, y], [e, f, g, h, u, v]) {
        return [a * e + c * f, b * e + d * f, a * g + c * h, b * g + d * h,
            a * u + c * v + x, b * u + d * v + y];
    }

    function inverseWardrobeMatrix([a, b, c, d, x, y]) {
        const determinant = a * d - b * c;
        if (Math.abs(determinant) < 1e-12) return null;
        return [d / determinant, -b / determinant, -c / determinant, a / determinant,
            (c * y - d * x) / determinant, (b * x - a * y) / determinant];
    }

    function rotateWardrobePose(pose, angle, aspect) {
        const cos = Math.cos(angle);
        const sin = Math.sin(angle);
        const basis = multiplyWardrobeMatrix([cos, sin * aspect, -sin / aspect, cos, 0, 0],
            [...pose.slice(0, 4), 0, 0]);
        return [...basis.slice(0, 4), pose[4], pose[5]];
    }

    function activeWardrobeBone(name) {
        if (!spineRenderer) return null;
        const alternates = templatePackage?.source?.wardrobeBindings?.boneAlternates;
        const pair = alternates?.find(({ bone, alternateBone }) => name === bone || name === alternateBone);
        if (!pair) return null;
        const { bone, slot, alternateBone, alternateSlot } = pair;
        const ownSlot = name === bone ? slot : alternateSlot;
        const otherSlot = name === bone ? alternateSlot : slot;
        const swap = !spineRenderer.partIsVisible(ownSlot) && spineRenderer.partIsVisible(otherSlot);
        return {
            name: swap ? (name === bone ? alternateBone : bone) : name,
            slot: swap ? otherSlot : ownSlot,
            slots: [slot, alternateSlot],
        };
    }

    function wardrobeBoneTransform(name) {
        if (!name || !spineRenderer) return null;
        const active = activeWardrobeBone(name);
        return (active && spineRenderer.boneCanvasTransform(active.name, name)) || spineRenderer.boneCanvasTransform(name);
    }

    function wardrobeSpriteLayerSlot(sprite) {
        if (!sprite.aboveSlot || !sprite.anchor) return sprite.aboveSlot;
        const active = activeWardrobeBone(sprite.anchor.name);
        return active?.slots.includes(sprite.aboveSlot)
            && !spineRenderer.partIsVisible(sprite.aboveSlot) && spineRenderer.partIsVisible(active.slot)
            ? active.slot : sprite.aboveSlot;
    }

    function wardrobeSpritePose(sprite) {
        if (sprite.dragPose) return sprite.dragPose;
        const bone = wardrobeBoneTransform(sprite.anchor?.name);
        if (bone) return multiplyWardrobeMatrix(bone, sprite.anchor.matrix);
        return [...(sprite.basis || [1, 0, 0, 1]), sprite.x, sprite.y];
    }

    function setWardrobeBinding(sprite, name, pose, bone = wardrobeBoneTransform(name)) {
        const inverse = bone && inverseWardrobeMatrix(bone);
        sprite.basis = pose.slice(0, 4);
        [sprite.x, sprite.y] = pose.slice(4);
        sprite.anchor = inverse ? { name, matrix: multiplyWardrobeMatrix(inverse, pose) } : null;
    }

    function wardrobeBindingCandidates(sprite, [, , c, d, x, y]) {
        if (!hasSpineRenderer()) return [];
        // A hat may sit above the figure while its brim touches the head.
        const halfHeight = wardrobeSpriteGeometry(sprite).height / 2;
        let hits = spineRenderer.attachmentsAtCanvasPoint(x, y);
        if (!hits.length) hits = spineRenderer.attachmentsAtCanvasPoint(x + c * halfHeight, y + d * halfHeight);
        const preferredBones = templatePackage?.source?.wardrobeBindings?.preferredBoneBySlot;
        return [...new Set(hits.flatMap(({ bone, slot }) => {
            const preferred = preferredBones?.[slot];
            return typeof preferred === 'string' ? [preferred, bone] : [bone];
        }))];
    }

    function updateWardrobeSprite(sprite, repaint = true) {
        if (!wardrobeImageReady(sprite?.image)) return;
        const [a, b, c, d, x, y] = wardrobeSpritePose(sprite);
        const { clientWidth, clientHeight } = wardrobePlacements;
        const aspect = clientWidth / (clientHeight || 1) || 1;
        sprite.button.style.left = `${x * 100}%`;
        sprite.button.style.top = `${y * 100}%`;
        const width = `${wardrobeSpriteGeometry(sprite).width * 100}%`;
        if (sprite.button.style.width !== width) sprite.button.style.width = width;
        sprite.button.style.transform = `translate(-50%, -50%) matrix(${a}, ${b / aspect}, ${c * aspect}, ${d}, 0, 0)`;
        sprite.button.hidden = Boolean(sprite.anchor && spineRenderer?.skeleton?.visible === false);
        if (sprite.id === selectedWardrobeSprite) {
            updateWardrobeTransform(sprite, [a, b, c, d, x, y], clientWidth, clientHeight);
        }
        if (repaint) renderWardrobeArtwork();
    }

    function updateWardrobeTransform(sprite, [a, b, c, d, x, y], canvasWidth, canvasHeight) {
        const visible = !wardrobePlacements.hidden && !sprite.button.hidden;
        wardrobeTransform.hidden = !visible;
        if (!visible) {
            wardrobeSpriteMenu.hidden = true;
            return;
        }
        if (!canvasWidth || !canvasHeight) return;
        const { width, height } = wardrobeSpriteGeometry(sprite);
        const place = (element, px, py) => {
            element.style.left = `${px * 100}%`;
            element.style.top = `${py * 100}%`;
        };
        const frame = wardrobeTransform.firstElementChild;
        const remove = $('#wardrobe-sprite-remove');
        remove.disabled = Boolean(sprite.removing);
        Object.assign(frame.style, {
            left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%`,
            transform: sprite.button.style.transform,
        });
        for (const corner of wardrobeTransform.querySelectorAll('.wardrobe-transform-scale')) {
            const dx = Number(corner.dataset.x) * width / 2;
            const dy = Number(corner.dataset.y) * height / 2;
            const vx = a * dx + c * dy;
            const vy = b * dx + d * dy;
            place(corner, x + vx, y + vy);
            corner.style.cursor = `${['ew', 'nwse', 'ns', 'nesw'][
                (Math.round(Math.atan2(vy * canvasHeight, vx * canvasWidth) / (Math.PI / 4)) + 4) % 4]}-resize`;
        }
        const topX = -c * height / 2;
        const topY = -d * height / 2;
        const topLength = Math.hypot(topX * canvasWidth, topY * canvasHeight) || 1;
        const placeAction = (element, px, py) => place(element,
            Math.max(12 / canvasWidth, Math.min(1 - 12 / canvasWidth, px)),
            Math.max(12 / canvasHeight, Math.min(1 - 12 / canvasHeight, py)));
        placeAction(wardrobeTransform.querySelector('.wardrobe-transform-rotate'),
            x + topX * (1 + 24 / topLength), y + topY * (1 + 24 / topLength));
        const sideX = a * width / 2;
        const sideY = b * width / 2;
        const sideLength = Math.hypot(sideX * canvasWidth, sideY * canvasHeight) || 1;
        placeAction(remove, x + topX + sideX * (1 + 17 / sideLength),
            y + topY + sideY * (1 + 17 / sideLength));
    }

    function renderWardrobeBinding(sprite) {
        const select = $('#wardrobe-sprite-bone');
        const mode = sprite.bindingMode || 'auto';
        const label = (name) => {
            const key = templatePackage?.source?.wardrobeBindings?.boneLabels?.[name];
            return typeof key === 'string' ? `${t(`emoteMaker.${key}`)} · ${name}` : name;
        };
        const names = spineRenderer?.boneNames() || [];
        const auto = t('emoteMaker.wardrobeSpriteAutoBinding');
        select.replaceChildren(
            new Option(mode === 'auto' && sprite.anchor ? `${auto} · ${label(sprite.anchor.name)}` : auto, 'auto'),
            new Option(t('emoteMaker.wardrobeSpriteFreeBinding'), 'free'),
        );
        const candidates = (sprite.bindingCandidates || []).filter((name) => names.includes(name));
        const remaining = names.filter((name) => !candidates.includes(name));
        for (const [groupLabel, groupNames] of [['wardrobeSpriteNearbyBones', candidates], ['wardrobeSpriteAllBones', remaining]]) {
            if (!groupNames.length) continue;
            const group = document.createElement('optgroup');
            group.label = t(`emoteMaker.${groupLabel}`);
            group.append(...groupNames.map((name) => new Option(label(name), `bone:${name}`)));
            select.append(group);
        }
        const value = mode === 'manual' && sprite.anchor ? `bone:${sprite.anchor.name}` : mode;
        select.value = Array.from(select.options).some((option) => option.value === value) ? value : 'auto';
    }

    function selectWardrobeSprite(id) {
        if (selectedWardrobeSprite !== id) wardrobeSpriteMenu.hidden = true;
        selectedWardrobeSprite = id;
        for (const sprite of wardrobeSprites.values()) {
            sprite.button.setAttribute('aria-pressed', String(sprite.id === id));
        }
        const selected = wardrobeSprites.get(id);
        if (wardrobeImageReady(selected?.image)) updateWardrobeSprite(selected);
        else wardrobeTransform.hidden = true;
    }

    function mountWardrobeSprite(placement, image) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'wardrobe-placement';
        button.setAttribute('aria-label', t('emoteMaker.wardrobeSpriteMove'));
        button.setAttribute('aria-pressed', 'false');
        image.alt = '';
        image.draggable = false;
        button.append(image);
        const sprite = { ...placement, image, button };
        wardrobeSprites.set(sprite.id, sprite);
        wardrobePlacements.insertBefore(button, wardrobeTransform);
        updateWardrobeSprite(sprite);
        let drag = null;
        const clamp = (value) => Math.max(0, Math.min(1, value));
        button.addEventListener('focus', () => selectWardrobeSprite(sprite.id));
        button.addEventListener('pointerdown', (event) => {
            if (wardrobeFileBusy || wardrobeGestureActive || sprite.removing || event.button !== 0) return;
            selectWardrobeSprite(sprite.id);
            wardrobeGestureActive = true;
            updateHistoryButtons();
            drag = { id: event.pointerId, x: event.clientX, y: event.clientY, pose: wardrobeSpritePose(sprite) };
            button.setPointerCapture(event.pointerId);
        });
        button.addEventListener('pointermove', (event) => {
            if (drag?.id !== event.pointerId) return;
            const box = wardrobePlacements.getBoundingClientRect();
            if (!sprite.dragPose && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 3) return;
            sprite.dragPose = [...drag.pose];
            sprite.dragPose[4] = clamp(drag.pose[4] + (event.clientX - drag.x) / box.width);
            sprite.dragPose[5] = clamp(drag.pose[5] + (event.clientY - drag.y) / box.height);
            updateWardrobeSprite(sprite);
        });
        button.addEventListener('pointerup', (event) => {
            if (drag?.id !== event.pointerId) return;
            drag = null;
            wardrobeGestureActive = false;
            updateHistoryButtons();
            if (!sprite.dragPose) return;
            const before = wardrobeSnapshot();
            const pose = sprite.dragPose;
            sprite.dragPose = null;
            sprite.bindingCandidates = wardrobeBindingCandidates(sprite, pose);
            const name = sprite.bindingMode === 'manual' ? sprite.anchor?.name
                : sprite.bindingMode === 'free' ? null : sprite.bindingCandidates[0];
            setWardrobeBinding(sprite, name, pose);
            updateWardrobeSprite(sprite);
            commitWardrobeChange(before);
        });
        button.addEventListener('lostpointercapture', (event) => {
            if (drag?.id !== event.pointerId) return;
            drag = null;
            wardrobeGestureActive = false;
            updateHistoryButtons();
            sprite.dragPose = null;
            updateWardrobeSprite(sprite);
        });
        button.addEventListener('keydown', (event) => {
            if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                event.preventDefault();
                const box = button.getBoundingClientRect();
                openWardrobeSpriteMenu(sprite, box.right, box.top);
                return;
            }
            if (event.key === 'Delete' || event.key === 'Backspace') {
                event.preventDefault();
                void removeWardrobeSprite(sprite);
                return;
            }
            const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
            if (!direction || wardrobeGestureActive || wardrobeFileBusy) return;
            event.preventDefault();
            const before = wardrobeSnapshot();
            const pose = wardrobeSpritePose(sprite);
            pose[4] = clamp(pose[4] + direction[0] * 0.01);
            pose[5] = clamp(pose[5] + direction[1] * 0.01);
            setWardrobeBinding(sprite, sprite.anchor?.name, pose);
            updateWardrobeSprite(sprite);
            commitWardrobeChange(before);
        });
        button.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            openWardrobeSpriteMenu(sprite, event.clientX, event.clientY);
        });
        return sprite;
    }

    function syncWardrobePlacements() {
        if (!wardrobePlacements) return;
        const visible = templatePackage?.source?.builtIn === true && hasSpineRenderer();
        wardrobePlacements.hidden = !visible;
        selectWardrobeSprite(selectedWardrobeSprite);
        renderWardrobeArtwork();
        if (!visible || wardrobeSpritesLoaded) return;
        const key = currentTemplateKey();
        if (!key) return;
        wardrobeTemplateKey = key;
        wardrobeSpritesLoaded = true;
        let saved;
        try {
            saved = JSON.parse(localStorage.getItem(wardrobePlacementKey()) || '[]');
        } catch (_) {
            return;
        }
        if (!Array.isArray(saved)) return;
        for (const sprite of saved) {
            if (!sprite || !/^sprite-[0-9a-f]{16}$/.test(sprite.id)
                    || ![sprite.x, sprite.y, sprite.size].every(Number.isFinite)
                    || sprite.size < .05 || sprite.size > 1.5 || wardrobeSprites.has(sprite.id)) continue;
            const validMatrix = (value, length) => Array.isArray(value) && value.length === length && value.every(Number.isFinite);
            if (!validMatrix(sprite.basis, 4)) delete sprite.basis;
            if (typeof sprite.anchor?.name !== 'string' || !validMatrix(sprite.anchor.matrix, 6)) delete sprite.anchor;
            if (!['auto', 'free', 'manual'].includes(sprite.bindingMode)) delete sprite.bindingMode;
            sprite.behind = sprite.behind === true;
            if (typeof sprite.aboveSlot !== 'string') delete sprite.aboveSlot;
            const path = `${WARDROBE_SPRITE_PREFIX}${sprite.id}.png`;
            const image = new Image();
            mountWardrobeSprite(sprite, image);
            image.addEventListener('load', () => {
                const current = wardrobeSprites.get(sprite.id);
                if (current?.image !== image) return;
                updateWardrobeSprite(current);
                forgetStageFrames();
            }, { once: true });
            image.addEventListener('error', () => {
                const current = wardrobeSprites.get(sprite.id);
                if (current?.image !== image) return;
                current.button.remove();
                wardrobeSprites.delete(sprite.id);
                if (selectedWardrobeSprite === sprite.id) selectWardrobeSprite(null);
                renderWardrobeArtwork();
            }, { once: true });
            image.src = templateTextureUrl(key, path);
        }
    }

    async function addWardrobePlacement(blob) {
        const key = currentTemplateKey();
        if (!key || templatePackage?.source?.builtIn !== true || wardrobeFileBusy || wardrobeGestureActive || historyBusy) {
            throw new Error('sprite_save_failed');
        }
        wardrobeTemplateKey = key;
        const id = 'sprite-' + Array.from(crypto.getRandomValues(new Uint8Array(8)),
            (byte) => byte.toString(16).padStart(2, '0')).join('');
        const placement = { id, x: 0.5, y: 0.5, size: 0.3 };
        const index = wardrobeSprites.size;
        const apply = (present) => setWardrobePresence(key, placement, index, blob, present);
        wardrobeFileBusy = true;
        updateHistoryButtons();
        try {
            await apply(true);
            if (currentTemplateKey() === key) {
                rememberHistory(false, true, apply);
                if (index === 0) {
                    pendingWardrobePlacementTip = key;
                    if (!$('#wardrobe-dialog')?.open) showWardrobePlacementTip();
                }
            }
        } catch (_) {
            throw new Error('sprite_save_failed');
        } finally {
            wardrobeFileBusy = false;
            updateHistoryButtons();
        }
    }

    function openWardrobeSpriteMenu(sprite, clientX, clientY) {
        selectWardrobeSprite(sprite.id);
        renderWardrobeBinding(sprite);
        const groups = templatePackage?.catalogs?.slotGroups || [];
        wardrobeLayerSlot.replaceChildren(
            new Option(t('emoteMaker.wardrobeLayerFront'), 'front'),
            ...spineRenderer.drawOrderNames().reverse().map((slot) => {
                const name = getCatalogLabel(groups.find((group) => group.id === slot)) || slot;
                return new Option(interpolate(t('emoteMaker.wardrobeLayerAbove'), { name }), `slot:${slot}`);
            }),
            new Option(t('emoteMaker.wardrobeLayerBack'), 'back'),
        );
        updateWardrobeLayerActions();
        const viewport = canvasStage.getBoundingClientRect();
        wardrobeSpriteMenu.hidden = false;
        const maxX = canvasStage.clientWidth - wardrobeSpriteMenu.offsetWidth - 4;
        const maxY = canvasStage.clientHeight - wardrobeSpriteMenu.offsetHeight - 4;
        wardrobeSpriteMenu.style.left = `${Math.max(4, Math.min(maxX, clientX - viewport.left - canvasStage.clientLeft))}px`;
        wardrobeSpriteMenu.style.top = `${Math.max(4, Math.min(maxY, clientY - viewport.top - canvasStage.clientTop))}px`;
        $('#wardrobe-sprite-bone').focus({ preventScroll: true });
    }

    function wardrobeLayers() {
        const sprites = [...wardrobeSprites.values()];
        const slots = spineRenderer.drawOrderNames();
        const layered = sprites.filter((sprite) => !sprite.behind)
            .map((sprite) => ({ sprite, slot: wardrobeSpriteLayerSlot(sprite) }));
        return [
            ...sprites.filter((sprite) => sprite.behind),
            ...slots.flatMap((slot) => [slot, ...layered.filter((item) => item.slot === slot).map((item) => item.sprite)]),
            ...layered.filter((item) => !slots.includes(item.slot)).map((item) => item.sprite),
        ];
    }

    function updateWardrobeLayerActions() {
        const order = wardrobeLayers();
        const sprite = wardrobeSprites.get(selectedWardrobeSprite);
        const index = order.indexOf(sprite);
        const unavailable = index < 0 || sprite.removing;
        wardrobeLayerUp.disabled = unavailable || index === order.length - 1;
        wardrobeLayerDown.disabled = unavailable || index === 0;
        wardrobeLayerSlot.disabled = unavailable;
        if (sprite) {
            const slot = wardrobeSpriteLayerSlot(sprite);
            wardrobeLayerSlot.value = order.includes(slot) ? `slot:${slot}` : sprite.behind ? 'back' : 'front';
        }
    }

    function applyWardrobeLayerOrder(order, sprite, position) {
        const before = wardrobeSnapshot();
        setWardrobeOrder(order.filter((item) => typeof item !== 'string'));
        sprite.behind = position === 'back';
        sprite.aboveSlot = position.startsWith('slot:') ? position.slice(5) : undefined;
        renderWardrobeArtwork();
        commitWardrobeChange(before);
        updateWardrobeLayerActions();
    }

    function setWardrobeOrder(sprites) {
        wardrobeSprites.clear();
        for (const sprite of sprites) {
            wardrobeSprites.set(sprite.id, sprite);
            wardrobePlacements.insertBefore(sprite.button, wardrobeTransform);
        }
    }

    function moveWardrobeLayer(step) {
        const order = wardrobeLayers();
        const sprite = wardrobeSprites.get(selectedWardrobeSprite);
        const index = order.indexOf(sprite);
        const adjacent = order[index + step];
        if (!sprite || sprite.removing || wardrobeFileBusy || wardrobeGestureActive || adjacent === undefined) return;
        const slot = wardrobeSpriteLayerSlot(sprite);
        let position = slot ? `slot:${slot}` : sprite.behind ? 'back' : 'front';
        [order[index], order[index + step]] = [adjacent, sprite];
        if (typeof adjacent === 'string') {
            const slotBehind = order.slice(0, index + step).findLast((item) => typeof item === 'string');
            position = slotBehind ? `slot:${slotBehind}` : 'back';
        } else {
            const adjacentSlot = wardrobeSpriteLayerSlot(adjacent);
            position = adjacentSlot ? `slot:${adjacentSlot}` : adjacent.behind ? 'back' : 'front';
        }
        applyWardrobeLayerOrder(order, sprite, position);
        const action = step > 0 ? wardrobeLayerUp : wardrobeLayerDown;
        if (action.disabled) (step > 0 ? wardrobeLayerDown : wardrobeLayerUp).focus({ preventScroll: true });
    }

    async function setWardrobePresence(key, placement, index, blob, present) {
        const { id } = placement;
        const path = `${WARDROBE_SPRITE_PREFIX}${id}.png`;
        let image;
        let url;
        try {
            if (present) {
                image = new Image();
                url = URL.createObjectURL(blob);
                image.src = url;
                await image.decode();
            }
            await writeTemplateTextures(key, [[path, blob]], { remove: !present });
        } catch (error) {
            if (url) URL.revokeObjectURL(url);
            throw error;
        }
        if (present) {
            const order = [...wardrobeSprites.values()];
            const sprite = mountWardrobeSprite(structuredClone(placement), image);
            sprite.blob = blob;
            order.splice(index, 0, sprite);
            setWardrobeOrder(order);
            selectWardrobeSprite(id);
        } else {
            const sprite = wardrobeSprites.get(id);
            if (sprite) {
                sprite.button.remove();
                if (sprite.image.src.startsWith('blob:')) URL.revokeObjectURL(sprite.image.src);
                wardrobeSprites.delete(id);
                if (selectedWardrobeSprite === id) selectWardrobeSprite(null);
            }
        }
        renderWardrobeArtwork();
        persistWardrobePlacements();
    }

    async function removeWardrobeSprite(sprite) {
        const key = currentTemplateKey();
        if (!key || wardrobeFileBusy || wardrobeGestureActive || historyBusy || sprite.removing) return;
        const remove = $('#wardrobe-sprite-remove');
        wardrobeFileBusy = true;
        sprite.removing = true;
        remove.disabled = true;
        updateHistoryButtons();
        try {
            const path = `${WARDROBE_SPRITE_PREFIX}${sprite.id}.png`;
            const blob = sprite.blob || await readTemplateTexture(key, path);
            if (!blob) throw new Error('sprite_save_failed');
            const placement = wardrobePlacement(sprite);
            const index = [...wardrobeSprites.keys()].indexOf(sprite.id);
            const apply = (present) => setWardrobePresence(key, placement, index, blob, present);
            await apply(false);
            if (currentTemplateKey() === key) rememberHistory(true, false, apply);
        } catch (_) {
            showToast(t('emoteMaker.wardrobeSpriteSaveFailed'));
        } finally {
            wardrobeFileBusy = false;
            sprite.removing = false;
            remove.disabled = Boolean(wardrobeSprites.get(selectedWardrobeSprite)?.removing);
            if (!wardrobeSpriteMenu.hidden) updateWardrobeLayerActions();
            updateHistoryButtons();
        }
    }

    function bindWardrobeTransform() {
        let gesture = null;
        wardrobeTransform.addEventListener('pointerdown', (event) => {
            const handle = event.target.closest('.wardrobe-transform-scale, .wardrobe-transform-rotate');
            const sprite = wardrobeSprites.get(selectedWardrobeSprite);
            if (!handle || !sprite || sprite.removing || wardrobeGestureActive || wardrobeFileBusy || event.button !== 0) return;
            const pose = wardrobeSpritePose(sprite);
            const box = wardrobePlacements.getBoundingClientRect();
            const bone = wardrobeBoneTransform(sprite.anchor?.name);
            if (!box.width || !box.height || (sprite.anchor && !bone)) return;
            event.preventDefault();
            wardrobeGestureActive = true;
            updateHistoryButtons();
            wardrobeSpriteMenu.hidden = true;
            const center = [box.left + box.width * pose[4], box.top + box.height * pose[5]];
            const dx = event.clientX - center[0];
            const dy = event.clientY - center[1];
            gesture = { sprite, pose, bone, handle, center, size: sprite.size, distance: Math.max(1, Math.hypot(dx, dy)),
                angle: Math.atan2(dy, dx), aspect: box.width / box.height, pointerId: event.pointerId,
                before: wardrobeSnapshot(), x: event.clientX, y: event.clientY, moved: false };
            sprite.dragPose = pose;
            handle.setPointerCapture(event.pointerId);
        });
        function transform(event) {
            if (gesture?.pointerId !== event.pointerId) return;
            if (!gesture.moved && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) < 2) return;
            gesture.moved = true;
            const { sprite, pose, handle, center, size, distance, angle, aspect } = gesture;
            const dx = event.clientX - center[0];
            const dy = event.clientY - center[1];
            if (handle.classList.contains('wardrobe-transform-scale')) {
                sprite.size = Math.max(0.05, Math.min(1.5, size * Math.hypot(dx, dy) / distance));
            } else {
                sprite.dragPose = rotateWardrobePose(pose, Math.atan2(dy, dx) - angle, aspect);
            }
            updateWardrobeSprite(sprite);
        }
        wardrobeTransform.addEventListener('pointermove', transform);
        wardrobeTransform.addEventListener('pointerup', (event) => {
            if (gesture?.pointerId !== event.pointerId) return;
            transform(event);
            const { sprite, handle, bone, before, moved } = gesture;
            gesture = null;
            wardrobeGestureActive = false;
            if (moved && handle.classList.contains('wardrobe-transform-rotate')) {
                setWardrobeBinding(sprite, sprite.anchor?.name, sprite.dragPose, bone);
            }
            sprite.dragPose = null;
            updateWardrobeSprite(sprite);
            if (moved) commitWardrobeChange(before);
            updateHistoryButtons();
        });
        wardrobeTransform.addEventListener('lostpointercapture', (event) => {
            if (gesture?.pointerId !== event.pointerId) return;
            const { sprite, size } = gesture;
            gesture = null;
            wardrobeGestureActive = false;
            sprite.dragPose = null;
            sprite.size = size;
            updateWardrobeSprite(sprite);
            updateHistoryButtons();
        });
        wardrobeTransform.addEventListener('click', (event) => {
            if (!event.target.closest('#wardrobe-sprite-remove')) return;
            const sprite = wardrobeSprites.get(selectedWardrobeSprite);
            if (sprite) void removeWardrobeSprite(sprite);
        });
        wardrobeTransform.addEventListener('keydown', (event) => {
            const sprite = wardrobeSprites.get(selectedWardrobeSprite);
            if (!sprite || sprite.removing || wardrobeGestureActive || wardrobeFileBusy) return;
            const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
            if (!step) return;
            const before = wardrobeSnapshot();
            const pose = wardrobeSpritePose(sprite);
            if (event.target.matches('.wardrobe-transform-scale')) {
                sprite.size = Math.max(0.05, Math.min(1.5, sprite.size + 0.02 * step));
            } else if (event.target.matches('.wardrobe-transform-rotate')) {
                const angle = Math.PI / 36 * step;
                const aspect = wardrobePlacements.clientWidth / (wardrobePlacements.clientHeight || 1) || 1;
                setWardrobeBinding(sprite, sprite.anchor?.name, rotateWardrobePose(pose, angle, aspect));
            } else return;
            event.preventDefault();
            updateWardrobeSprite(sprite);
            commitWardrobeChange(before);
        });
    }

    function bindWardrobePlacements() {
        const bone = $('#wardrobe-sprite-bone');
        bindWardrobeTransform();
        wardrobeLayerUp.addEventListener('click', () => moveWardrobeLayer(1));
        wardrobeLayerDown.addEventListener('click', () => moveWardrobeLayer(-1));
        wardrobeLayerSlot.addEventListener('change', () => {
            const sprite = wardrobeSprites.get(selectedWardrobeSprite);
            if (!sprite || sprite.removing || wardrobeGestureActive || wardrobeFileBusy) return;
            const position = wardrobeLayerSlot.value;
            const order = wardrobeLayers().filter((item) => item !== sprite);
            const index = position === 'front' ? order.length : position === 'back' ? 0 : order.indexOf(position.slice(5)) + 1;
            order.splice(index, 0, sprite);
            applyWardrobeLayerOrder(order, sprite, position);
        });
        if (wardrobePlacements) {
            new ResizeObserver(() => {
                if (wardrobePlacements.hidden || capturingFrames) return;
                for (const sprite of wardrobeSprites.values()) updateWardrobeSprite(sprite, false);
            }).observe(wardrobePlacements);
        }
        bone?.addEventListener('change', () => {
            const sprite = wardrobeSprites.get(selectedWardrobeSprite);
            if (!sprite || sprite.removing || wardrobeGestureActive || wardrobeFileBusy) return;
            const before = wardrobeSnapshot();
            const pose = wardrobeSpritePose(sprite);
            const value = bone.value;
            sprite.bindingMode = value.startsWith('bone:') ? 'manual' : value;
            sprite.bindingCandidates = wardrobeBindingCandidates(sprite, pose);
            const name = sprite.bindingMode === 'manual' ? value.slice(5)
                : value === 'auto' ? sprite.bindingCandidates[0] : null;
            setWardrobeBinding(sprite, name, pose);
            wardrobeSpriteMenu.hidden = true;
            sprite.button.focus({ preventScroll: true });
            updateWardrobeSprite(sprite);
            commitWardrobeChange(before);
        });
        document.addEventListener('pointerdown', (event) => {
            if (wardrobeSpriteMenu.contains(event.target)) return;
            wardrobeSpriteMenu.hidden = true;
            if (event.button === 0 && canvasFrame?.contains(event.target)
                    && !event.target.closest('.wardrobe-placement, .wardrobe-transform')) selectWardrobeSprite(null);
        });
        wardrobeSpriteMenu.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            wardrobeSpriteMenu.hidden = true;
            wardrobeSprites.get(selectedWardrobeSprite)?.button.focus({ preventScroll: true });
        });
    }

    function bindTimedTip(tip, { duration = 6000, returnFocus, onHide } = {}) {
        const message = tip.querySelector('p');
        const progress = tip.querySelector('.emote-timed-tip-progress');
        const close = tip.querySelector('.emote-timed-tip-close');
        const transition = 180;
        let animation = null;
        let progressAnimation = null;

        function hide() {
            animation?.cancel();
            progressAnimation?.cancel();
            animation = progressAnimation = null;
            tip.hidden = true;
            if (tip.hasAttribute('popover')) {
                tip.hidePopover?.();
                tip.removeAttribute('popover');
                for (const property of ['top', 'left', 'width']) tip.style.removeProperty(property);
            }
            close.disabled = false;
            onHide?.();
        }

        function animate(frames, length) {
            const current = tip.animate(frames, { duration: length, fill: 'both' });
            animation = current;
            current.addEventListener('finish', () => { if (animation === current) hide(); });
        }

        close.addEventListener('click', () => {
            if (tip.hidden || close.disabled) return;
            const { transform, opacity } = getComputedStyle(tip);
            const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
            const bounds = tip.getBoundingClientRect();
            animation?.cancel();
            progressAnimation?.pause();
            if (document.activeElement === close) returnFocus?.()?.focus({ preventScroll: true });
            close.disabled = true;

            let frames = [{ transform, opacity }, { transform, opacity: 0 }];
            if (!reducedMotion && typeof tip.showPopover === 'function') {
                // The top layer lets the tip fall past the edge of its container.
                tip.setAttribute('popover', 'manual');
                Object.assign(tip.style, {
                    top: `${bounds.top}px`, left: `${bounds.left}px`, width: `${bounds.width}px`,
                });
                tip.showPopover();
                const cornerLift = bounds.width * Math.sin(10 * Math.PI / 180) / 2;
                const distance = Math.ceil(window.innerHeight - bounds.top + cornerLift) + 12;
                frames = [
                    { transform: 'none', opacity, easing: 'cubic-bezier(.16, 1, .3, 1)' },
                    { transform: 'translate(5px, -15px) rotate(-4deg) scale(1.03)', opacity: 1, offset: 0.22,
                        easing: 'cubic-bezier(.55, .06, .83, .48)' },
                    { transform: `translate(19px, ${distance}px) rotate(10deg) scale(.96)`, opacity: 1 },
                ];
            }
            animate(frames, frames.length === 3 ? 540 : 120);
        });

        return {
            hide,
            show(key) {
                animation?.cancel();
                progressAnimation?.cancel();
                message.dataset.i18n = key;
                message.textContent = t(key);
                close.title = t('emoteMaker.wardrobeClose');
                close.setAttribute('aria-label', close.title);
                tip.hidden = false;
                const offset = matchMedia('(prefers-reduced-motion: reduce)').matches
                    ? 'none' : 'translateX(calc(-100% - 12px))';
                animate([
                    { transform: offset, opacity: 0 },
                    { transform: 'none', opacity: 1, offset: transition / duration },
                    { transform: 'none', opacity: 1, offset: 1 - transition / duration },
                    { transform: offset, opacity: 0 },
                ], duration);
                progressAnimation = progress.animate([
                    { transform: 'scaleX(0)' }, { transform: 'scaleX(1)' },
                ], { duration: duration - transition, fill: 'forwards' });
            },
        };
    }

    const shownPageTips = new Set();
    const activePageTips = new Map();
    let pendingWardrobePlacementTip = '';

    function showPageTip(key, { once = true, returnFocus } = {}) {
        if (once && shownPageTips.has(key)) return;
        const host = $('#emote-page-tips');
        if (!host) return;
        activePageTips.get(key)?.hide();
        const tip = $('#emote-page-tip-template').content.firstElementChild.cloneNode(true);
        const controller = bindTimedTip(tip, {
            duration: 10000,
            returnFocus,
            onHide: () => {
                tip.remove();
                activePageTips.delete(key);
            },
        });
        if (once) shownPageTips.add(key);
        activePageTips.set(key, controller);
        host.append(tip);
        controller.show(key);
    }

    function showWardrobePlacementTip() {
        const key = pendingWardrobePlacementTip;
        pendingWardrobePlacementTip = '';
        if (key && key === currentTemplateKey() && wardrobeSprites.size) {
            showPageTip('emoteMaker.wardrobePlacementTip', {
                once: false,
                returnFocus: () => wardrobeSprites.get(selectedWardrobeSprite)?.button,
            });
        }
    }

    function bindPackScrollTip() {
        const panel = $('#inspector-panel-export');
        const section = panel?.querySelector('.pack-section');
        if (!section) return;
        const key = 'emoteMaker.packDragTip';
        const positions = new WeakMap();
        const onScroll = (event) => {
            const target = event.target;
            const scroller = target === document ? document.scrollingElement : target;
            if (!scroller?.contains?.(panel)) return;
            const previous = positions.get(scroller) || 0;
            positions.set(scroller, scroller.scrollTop);
            if (!templatePackage || panel.hidden || scroller.scrollTop <= previous) return;
            const content = section.getBoundingClientRect();
            const visible = panel.getBoundingClientRect();
            const height = Math.min(content.bottom, visible.bottom, window.innerHeight)
                - Math.max(content.top, visible.top, 0);
            if (height < Math.min(24, content.height) || !content.height) return;
            showPageTip(key, { returnFocus: () => $('#inspector-tab-export') });
            window.removeEventListener('scroll', onScroll, true);
        };
        window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    }

    function bindWardrobeGeneration() {
        const api = '/api/emote-maker/builtin/wardrobe';
        const libraryApi = `${api}/spritesheets`;
        const headers = { 'x-neko-emote-wardrobe': '1' };
        const dialog = $('#wardrobe-dialog');
        if (!dialog) return;
        const reference = $('#wardrobe-reference');
        const referenceOpen = $('#wardrobe-reference-open');
        const promptField = $('#wardrobe-prompt-field');
        const supplementalPrompt = $('#wardrobe-prompt');
        const sourceView = $('#wardrobe-source-view');
        const libraryView = $('#wardrobe-library-view');
        const sourcePane = $('#wardrobe-source-pane');
        const libraryPane = $('#wardrobe-library-pane');
        const libraryImport = $('#wardrobe-library-import');
        const libraryImage = $('#wardrobe-library-image');
        const libraryStatus = $('#wardrobe-library-status');
        const libraryEmpty = $('#wardrobe-library-empty');
        const libraryGrid = $('#wardrobe-library-grid');
        const libraryConfirm = $('#wardrobe-library-confirm');
        const form = $('#wardrobe-form');
        const views = { source: [sourceView, sourcePane], library: [libraryView, libraryPane] };
        const sheetResult = $('#wardrobe-spritesheet-result');
        const sheetStatus = $('#wardrobe-sheet-status');
        const sheetLayoutWarning = $('#wardrobe-spritesheet-layout-warning');
        const addSprite = $('#wardrobe-sprite-add');
        const saveSheet = $('#wardrobe-spritesheet-save');
        const sheetFile = $('#wardrobe-spritesheet-file');
        const openSheet = $('#wardrobe-spritesheet-open');
        const status = $('#wardrobe-status');
        const model = $('#wardrobe-model');
        const configLink = $('#wardrobe-config');
        const submit = $('#wardrobe-submit');
        const close = $('#wardrobe-close');
        const paddingCooldown = 10000;
        const suggestedCropEdge = 64;
        const nextCropNotice = new Map();
        let cropAspectLimit = null;
        let configurationCode = null;
        let regionLimit = 3;
        let pending = '';
        let activeView = 'source';
        const paddingNotice = bindTimedTip($('#wardrobe-padding-notice'), {
            returnFocus: () => views[activeView][0],
        });
        let currentSheet = null;
        let libraryEntries = null;
        let libraryRefresh = null;
        let pendingRemoval = null;
        const picker = bindWardrobeSpritePicker({
            canEdit: () => !pending,
            onChange: () => {
                addSprite.disabled = Boolean(pending) || !picker.selected;
                renderSubmit();
            },
        });
        const annotations = bindWardrobeSourceCrops({
            reference,
            onCommit: (width, height) => {
                const longest = Math.max(width, height);
                if (cropAspectLimit && longest / Math.min(width, height) > cropAspectLimit) {
                    showCropNotice('emoteMaker.wardrobeCropPadded');
                } else if (longest < suggestedCropEdge) {
                    showCropNotice('emoteMaker.wardrobeCropLowResolution');
                }
            },
            onChange: (changed) => {
                if (changed && configurationCode === '') status.textContent = '';
                renderControls();
            },
        });

        function clearSheet() {
            if (currentSheet) URL.revokeObjectURL(currentSheet.url);
            currentSheet = null;
            sheetStatus.textContent = '';
            sheetLayoutWarning.hidden = true;
            picker.setImage(null);
        }

        function showCropNotice(key) {
            const now = performance.now();
            if (!dialog.open || now < (nextCropNotice.get(key) ?? 0)) return;
            nextCropNotice.set(key, now + paddingCooldown);
            paddingNotice.hide();
            paddingNotice.show(key);
        }

        function renderSubmit() {
            submit.disabled = Boolean(pending) || configurationCode !== '' || annotations.editing || picker.editing
                || !reference.files.length || !annotations.count;
        }

        function renderControls() {
            const busy = Boolean(pending);
            reference.disabled = referenceOpen.disabled = busy;
            configLink.hidden = !configurationCode;
            for (const [view, [button, pane]] of Object.entries(views)) {
                pane.hidden = view !== activeView;
                button.setAttribute('aria-pressed', String(!pane.hidden));
            }
            annotations.update(busy, regionLimit);
            promptField.hidden = annotations.count !== 1;
            supplementalPrompt.disabled = busy || promptField.hidden;
            if (!annotations.count) supplementalPrompt.value = '';
            sheetResult.hidden = !currentSheet;
            libraryPane.classList.toggle('has-preview', Boolean(currentSheet));
            form.hidden = activeView === 'library';
            libraryImport.disabled = busy;
            libraryGrid.disabled = busy || Boolean(libraryRefresh);
            saveSheet.disabled = busy;
            saveSheet.hidden = !currentSheet || Boolean(currentSheet.id) || pending === 'saving-sheet';
            openSheet.disabled = busy;
            picker.update();
            close.disabled = pending === 'saving' || pending === 'saving-sheet';
        }

        function reportError(code, target = status) {
            const key = {
                invalid_image: 'wardrobeInvalidImage',
                invalid_annotations: 'wardrobeInvalidMarks',
                invalid_spritesheet_regions: 'wardrobeInvalidMarks',
                invalid_wardrobe_prompt: 'wardrobePromptInvalid',
                invalid_spritesheet_result: 'wardrobeSpriteResultFailed',
                invalid_spritesheet_file: 'wardrobeSpriteOpenFailed',
                wardrobe_library_failed: 'wardrobeLibraryFailed',
                wardrobe_library_missing: 'wardrobeLibraryFailed',
                image_edit_unsupported: 'wardrobeSpriteUnsupported',
                empty_sprite: 'wardrobeSpriteEmpty',
                sprite_save_failed: 'wardrobeSpriteSaveFailed',
                generation_busy: 'wardrobeBusy',
                wardrobe_vision_unavailable: 'wardrobeVisionUnavailable',
                wardrobe_vision_failed: 'wardrobeVisionFailed',
                missing_api_key: 'wardrobeUnconfigured',
                not_configured: 'wardrobeUnconfigured',
                invalid_configuration: 'wardrobeUnconfigured',
                provider_access_denied: 'wardrobeProviderAccessDenied',
                provider_rate_limited: 'wardrobeProviderLimited',
                provider_quota_exceeded: 'wardrobeProviderQuota',
                provider_timeout: 'wardrobeProviderTimeout',
                provider_unavailable: 'wardrobeProviderUnavailable',
                provider_network_error: 'wardrobeProviderUnavailable',
                provider_request_rejected: 'wardrobeProviderRejected',
                provider_http_error: 'wardrobeProviderRejected',
                provider_task_failed: 'wardrobeProviderRejected',
                invalid_response: 'wardrobeProviderResponse',
                invalid_image_data: 'wardrobeProviderResponse',
                invalid_image_url: 'wardrobeProviderResponse',
                response_too_large: 'wardrobeProviderResponse',
                unsupported_content_encoding: 'wardrobeProviderResponse',
                image_download_failed: 'wardrobeDownloadFailed',
                local_connection_lost: 'wardrobeLocalConnectionLost',
            }[code] || 'wardrobeFailed';
            target.textContent = t(`emoteMaker.${key}`);
        }

        async function checkedFetch(url, options) {
            let response;
            try {
                response = await fetch(url, { ...options, credentials: 'same-origin' });
            } catch (_) {
                throw new Error('local_connection_lost');
            }
            if (!response.ok) {
                throw new Error((await response.json().catch(() => ({}))).code || 'request_failed');
            }
            return response;
        }

        function renderSheetLibrary() {
            libraryEmpty.hidden = Boolean(libraryRefresh) || libraryEntries?.length !== 0;
            const dateFormat = new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
                dateStyle: 'short', timeStyle: 'short',
            });
            const cards = (libraryEntries || []).map(({ id, savedAt }) => {
                const date = new Date(savedAt);
                const dateLabel = dateFormat.format(date);
                const card = document.createElement('div');
                card.className = 'wardrobe-library-card';
                const preview = document.createElement('button');
                preview.type = 'button';
                preview.className = 'wardrobe-button wardrobe-library-preview';
                preview.dataset.sheetOpen = id;
                preview.setAttribute('aria-label', `${t('emoteMaker.wardrobeSpriteOpen')} · ${dateLabel}`);
                preview.setAttribute('aria-pressed', String(currentSheet?.id === id));
                const img = new Image();
                img.alt = '';
                img.loading = 'lazy';
                img.src = `${libraryApi}/${id}?preview=true`;
                preview.append(img);
                const actions = document.createElement('div');
                actions.className = 'wardrobe-library-card-actions';
                const time = document.createElement('time');
                time.dateTime = date.toISOString();
                time.textContent = dateLabel;
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'wardrobe-button wardrobe-button-quiet';
                remove.dataset.sheetRemove = id;
                remove.textContent = t('emoteMaker.confirmDelete');
                remove.setAttribute('aria-label', `${remove.textContent} · ${dateLabel}`);
                actions.append(time, remove);
                card.append(preview, actions);
                return card;
            });
            libraryGrid.replaceChildren(...cards);
        }

        function refreshSheetLibrary() {
            if (libraryRefresh) return libraryRefresh;
            libraryEmpty.hidden = true;
            libraryStatus.textContent = t('common.loading');
            libraryGrid.disabled = true;
            libraryPane.setAttribute('aria-busy', 'true');
            libraryRefresh = checkedFetch(libraryApi)
                .then((response) => response.json())
                .then(({ entries }) => {
                    libraryEntries = entries;
                    libraryStatus.textContent = '';
                })
                .catch(() => { libraryStatus.textContent = t('emoteMaker.wardrobeLibraryFailed'); })
                .finally(() => {
                    libraryRefresh = null;
                    libraryPane.removeAttribute('aria-busy');
                    renderSheetLibrary();
                    libraryGrid.disabled = Boolean(pending);
                });
            return libraryRefresh;
        }

        async function loadLibrarySheet(id) {
            const response = await checkedFetch(`${libraryApi}/${id}`);
            await showSheet(await response.blob(), response.headers.get('X-Wardrobe-Spritesheet-Layout') === 'check',
                'invalid_spritesheet_file', id);
            renderSheetLibrary();
        }

        libraryGrid.addEventListener('click', async (event) => {
            if (pending) return;
            const remove = event.target.closest('[data-sheet-remove]');
            if (remove) {
                pendingRemoval = remove.dataset.sheetRemove;
                libraryConfirm.returnValue = '';
                libraryConfirm.showModal();
                return;
            }
            const id = event.target.closest('[data-sheet-open]')?.dataset.sheetOpen;
            if (!id) return;
            pending = 'opening-sheet';
            libraryStatus.textContent = '';
            renderControls();
            try {
                await loadLibrarySheet(id);
            } catch (_) {
                libraryStatus.textContent = t('emoteMaker.wardrobeLibraryFailed');
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') libraryView.focus({ preventScroll: true });
            }
        });

        libraryConfirm.addEventListener('close', async () => {
            const id = pendingRemoval;
            pendingRemoval = null;
            if (!id || libraryConfirm.returnValue !== 'delete') return;
            pending = 'removing-sheet';
            libraryStatus.textContent = '';
            renderControls();
            try {
                await checkedFetch(`${libraryApi}/${id}`, { method: 'DELETE', headers });
                if (currentSheet?.id === id) clearSheet();
                await libraryRefresh;
                libraryEntries = libraryEntries?.filter((entry) => entry.id !== id) || [];
                await refreshSheetLibrary();
            } catch (_) {
                libraryStatus.textContent = t('emoteMaker.wardrobeLibraryFailed');
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') libraryView.focus({ preventScroll: true });
            }
        });

        async function saveLibrarySheet(blob, layoutWarning = false) {
            const body = new FormData();
            body.append('sheet', blob, 'accessory');
            body.append('layout_warning', String(layoutWarning));
            const { id } = await (await checkedFetch(libraryApi, { method: 'POST', headers, body })).json();
            if (!id) throw new Error('wardrobe_library_failed');
            return id;
        }

        libraryImport.addEventListener('click', () => libraryImage.click());
        libraryImage.addEventListener('change', async () => {
            const file = libraryImage.files[0];
            libraryImage.value = '';
            if (!file || pending) return;
            pending = 'importing-image';
            libraryStatus.textContent = t('common.loading');
            renderControls();
            try {
                if (!file.size || file.size > 32 * 1024 * 1024) throw new Error('invalid_spritesheet_file');
                const id = await saveLibrarySheet(file);
                await libraryRefresh;
                await refreshSheetLibrary();
                await loadLibrarySheet(id);
                sheetStatus.textContent = t('emoteMaker.wardrobeLibrarySaved');
            } catch (error) {
                await libraryRefresh;
                const key = error.message === 'invalid_spritesheet_file'
                    ? 'wardrobeLibraryInvalidImage' : 'wardrobeLibraryFailed';
                libraryStatus.textContent = t(`emoteMaker.${key}`);
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') libraryImport.focus({ preventScroll: true });
            }
        });

        async function storeCurrentSheet() {
            const sheet = currentSheet;
            try {
                sheet.id = await saveLibrarySheet(sheet.blob, sheet.layoutWarning);
                sheetStatus.textContent = t('emoteMaker.wardrobeLibrarySaved');
            } catch (_) {
                sheetStatus.textContent = t('emoteMaker.wardrobeLibrarySaveFailed');
            }
            await libraryRefresh;
            await refreshSheetLibrary();
        }

        saveSheet.addEventListener('click', async () => {
            if (pending || !currentSheet || currentSheet.id) return;
            pending = 'saving-sheet';
            sheetStatus.textContent = '';
            renderControls();
            try {
                await storeCurrentSheet();
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') {
                    (currentSheet?.id ? libraryView : saveSheet).focus({ preventScroll: true });
                }
            }
        });

        async function refreshStatus() {
            if (pending) return;
            configurationCode = null;
            pending = 'loading';
            status.textContent = '';
            model.textContent = '';
            configLink.hidden = true;
            renderControls();
            try {
                const data = await (await checkedFetch(api)).json();
                configurationCode = data.spritesheetConfigurationCode || '';
                cropAspectLimit = data.spritesheetCropAspectLimit ?? null;
                regionLimit = data.spritesheetRegionLimit || regionLimit;
                if (data.model) model.textContent = `${t('emoteMaker.wardrobeModel')} · ${data.model}`;
                if (configurationCode) reportError(configurationCode);
            } catch (error) {
                reportError(error.message);
            } finally {
                pending = '';
                renderControls();
            }
        }

        $('#wardrobe-open').addEventListener('click', () => {
            activeView = 'source';
            renderControls();
            dialog.showModal();
            void refreshStatus();
        });
        $('#wardrobe-library-open').addEventListener('click', () => {
            selectView('library');
            dialog.showModal();
            libraryView.focus({ preventScroll: true });
        });
        window.addEventListener('focus', () => {
            if (dialog.open && activeView !== 'library' && configurationCode) void refreshStatus();
        });
        window.addEventListener('localechange', () => {
            if (!dialog.open) return;
            renderControls();
            if (activeView === 'library') renderSheetLibrary();
        });

        function selectView(view) {
            activeView = view;
            renderControls();
            if (view === 'library') void refreshSheetLibrary();
            else if (configurationCode === null) void refreshStatus();
        }

        for (const [view, [button]] of Object.entries(views)) {
            button.addEventListener('click', () => selectView(view));
        }
        reference.addEventListener('change', () => {
            if (configurationCode === '') status.textContent = '';
            selectView('source');
        });
        referenceOpen.addEventListener('click', () => reference.click());
        close.addEventListener('click', () => dialog.close());
        dialog.addEventListener('close', () => {
            picker.cancel();
            paddingNotice.hide();
            showWardrobePlacementTip();
        });
        dialog.addEventListener('cancel', (event) => {
            if (close.disabled) event.preventDefault();
        });

        async function generateSheet(file) {
            const body = new FormData();
            body.append('reference', file);
            body.append('annotation', annotations.serialize());
            const details = supplementalPrompt.value.trim();
            if (annotations.count === 1 && details) body.append('supplemental_prompt', details);
            const response = await checkedFetch(`${api}/generate-spritesheet`, { method: 'POST', headers, body });
            await showSheet(await response.blob(), response.headers.get('X-Wardrobe-Spritesheet-Layout') === 'check',
                'invalid_spritesheet_result');
            pending = 'saving-sheet';
            renderControls();
            await storeCurrentSheet();
        }

        async function showSheet(blob, layoutWarning, errorCode, id = null) {
            const url = URL.createObjectURL(blob);
            const image = new Image();
            image.src = url;
            try {
                await image.decode();
            } catch (_) {
                URL.revokeObjectURL(url);
                throw new Error(errorCode);
            }
            clearSheet();
            currentSheet = { url, blob, layoutWarning, id };
            sheetLayoutWarning.hidden = !layoutWarning;
            picker.setImage(image);
            activeView = 'library';
            status.textContent = '';
        }

        openSheet.addEventListener('click', () => sheetFile.click());
        sheetFile.addEventListener('change', async () => {
            const file = sheetFile.files[0];
            sheetFile.value = '';
            if (!file || pending) return;
            pending = 'opening-sheet';
            renderControls();
            try {
                if (file.size < 24 || file.size > 32 * 1024 * 1024) throw new Error('invalid_spritesheet_file');
                const header = new DataView(await file.slice(0, 24).arrayBuffer());
                const width = header.getUint32(16);
                const height = header.getUint32(20);
                if (header.getUint32(0) !== 0x89504e47 || header.getUint32(4) !== 0x0d0a1a0a
                        || header.getUint32(8) !== 13 || header.getUint32(12) !== 0x49484452
                        || !width || !height || Math.max(width, height) > 2048) {
                    throw new Error('invalid_spritesheet_file');
                }
                await showSheet(file, false, 'invalid_spritesheet_file');
                void refreshSheetLibrary();
            } catch (_) {
                reportError('invalid_spritesheet_file');
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') libraryView.focus({ preventScroll: true });
            }
        });

        $('#wardrobe-form').addEventListener('submit', async (event) => {
            event.preventDefault();
            if (submit.disabled) return;
            const image = reference.files[0];
            if (image?.size > 8 * 1024 * 1024) {
                reportError('invalid_image');
                renderControls();
                return;
            }
            pending = 'generating';
            status.textContent = t('emoteMaker.wardrobeWorking');
            renderControls();
            try {
                await generateSheet(image);
            } catch (error) {
                reportError(error.message);
            } finally {
                pending = '';
                renderControls();
                if (dialog.open && activeView === 'library') libraryView.focus({ preventScroll: true });
            }
        });

        addSprite.addEventListener('click', async () => {
            if (pending || !picker.selected) return;
            pending = 'saving';
            sheetStatus.textContent = t('emoteMaker.wardrobeSaving');
            renderControls();
            try {
                const blob = await picker.crop();
                if (!blob) throw new Error('empty_sprite');
                await addWardrobePlacement(blob);
                picker.clearSelection();
                sheetStatus.textContent = t('emoteMaker.wardrobeSpriteAdded');
            } catch (error) {
                reportError(error.message, sheetStatus);
            } finally {
                pending = '';
                renderControls();
            }
        });
    }

    function renderLibraryControls() {
        // 分类页签（模板/情绪/动作/装饰）连同它们唯一的部件源（两处 const []）已经
        // 删除：这个函数现在只负责把搜索/标题/清单亮出来——没加载模板时左侧就是
        // 模板库本身，点一条 = 从本地副本装回来。
        [
            document.querySelector('.asset-search'),
            document.querySelector('.asset-section-heading'),
            assetGrid,
        ].forEach((el) => { if (el) el.hidden = false; });
        if (assetGrid) assetGrid.setAttribute('aria-labelledby', 'asset-section-title');
    }

    function renderInspectorTabs() {
        document.querySelectorAll('.inspector-tab').forEach((button) => {
            const active = button.dataset.inspectorTab === state.inspectorTab;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
            button.setAttribute('tabindex', active ? '0' : '-1');
            button.disabled = false;
        });
        document.querySelectorAll('.inspector-tab-panel').forEach((panel) => {
            const active = panel.dataset.inspectorPanel === state.inspectorTab;
            panel.classList.toggle('is-active', active);
            panel.hidden = !active;
        });
        updateColorBackToTop();
    }

    function adjustmentValuesDiffer() {
        return Math.round(state.adjustments.x) !== 0
            || Math.round(state.adjustments.y) !== 0
            || Math.round(state.adjustments.rotation) !== 0
            || Math.round(state.adjustments.scale) !== 100;
    }

    function syncAdjustmentReadouts() {
        const values = {
            x: `${Math.round(state.adjustments.x)}`,
            y: `${Math.round(state.adjustments.y)}`,
            rotation: `${Math.round(state.adjustments.rotation)}°`,
            scale: `${Math.round(state.adjustments.scale)}%`
        };
        Object.entries(values).forEach(([key, value]) => {
            const output = $(`#adjust-${key}-value`);
            if (output) output.textContent = value;
        });
        const resetButton = $('#reset-adjustments-btn');
        // 改了值就得立刻让他能按重置：这条以前只在整页重绘时算，
        // 拆成轻路径之后如果忘了带上，按钮会一直停在禁用状态。
        if (resetButton) resetButton.disabled = !adjustmentValuesDiffer();
    }

    function renderAdjustments() {
        syncAdjustmentReadouts();
        Object.entries(state.adjustments).forEach(([key, value]) => {
            const input = $(`#adjust-${key}`);
            if (input) input.value = value;
        });
        const overwrite = $('#adjust-overwrite');
        if (overwrite) overwrite.checked = state.adjustOverwrite;
        // 「覆盖默认值」关掉时这四条滑杆根本不起作用（native 与 2D 两条绘制路径都读这个
        // 开关）。以前只在没有模板时禁用它们，于是导入了模板的人能拖、能改读数，画面却
        // 一动不动 —— 看上去就是"滑杆坏了 / 重置没作用"。
        const inert = !state.adjustOverwrite;
        document.querySelectorAll('.range-row input').forEach((input) => {
            input.disabled = inert;
        });
    }

    function renderColorMode() {
        renderTemplateSlotControls();
        renderChangedPartColors();
        applyNativeVisualState();
    }

    const PLAY_ICON_URL = `${ICON_ROOT}motion_play_icon.png`;

    /** 清单内容签名：由 pack 模块给（纯函数，可在 Node 里单测） */
    function listsSignature(items, kind) {
        const packModule = window.NekoEmotePngtuberPack;
        if (!packModule || typeof packModule.listSignature !== 'function') {
            return `${kind}|${(items || []).length}`;
        }
        return packModule.listSignature(kind, items);
    }

    function replaceAnimationList(...children) {
        if (!animationList) return;
        // Rebuilding the cards is sometimes necessary (locale/template
        // changes), but replaceChildren() resets the scroll container. Keep
        // the user's place so selecting an item near the end does not jump
        // back to the first animation.
        const scrollTop = animationList.scrollTop;
        const scrollLeft = animationList.scrollLeft;
        animationList.replaceChildren(...children);
        animationList.scrollTop = scrollTop;
        animationList.scrollLeft = scrollLeft;
    }

    function updateAnimationCardSelection() {
        if (!animationList) return;
        animationList.querySelectorAll('.animation-card').forEach((card) => {
            const selected = card.dataset.animationId === String(state.animationId);
            card.classList.toggle('is-selected', selected);
            card.setAttribute('aria-pressed', String(selected));
        });
    }



    // ── 组（分组导出）─────────────────────────────────────────────────────────
    // 数据规则都在 emote_group_library.js；这一层只管"页面上的组"：谁被选中、选中时
    // 列表显示什么、导出时把每一项交给烘焙器。
    function ownGroups() {
        return groupLibrary ? groupLibrary.groupsFrom(state.groups) : [];
    }

    function allGroups() {
        if (!groupLibrary) return [];
        const imported = groupLibrary.importedAnimationGroup(getImportedScenes(), t('emoteMaker.segmentAnimations'));
        const groups = ownGroups();
        return imported ? [imported, ...groups] : groups;
    }

    function activeGroup() {
        if (!state.groupId) return null;
        return allGroups().find((group) => group.id === state.groupId) || null;
    }

    function ownShots() {
        return groupLibrary ? groupLibrary.shotsFrom(state.shots) : [];
    }

    function findShotById(id) {
        return ownShots().find((shot) => shot.id === String(id)) || null;
    }

    function writeGroups(groups, shots) {
        state.groups = groups;
        state.shots = shots;
        // 组跟着模板走：换一个形象不该看到上一个形象的分组
        persistTemplateSession();
    }

    function selectGroup(id) {
        state.groupId = String(id || '');
        renderAnimations();
        renderGroupBar();
    }

    /**
     * 要往组里写东西时先有一个能写的组。没选组 / 选中的是导入的只读组 → 就地新建一个。
     * 空组，不带取景：右键「加入当前组」不该顺手塞进一条用户没点过的画面。
     */
    function ensureWritableGroup() {
        const current = activeGroup();
        if (current && !current.readOnly) return current;
        if (!groupLibrary || !templatePackage) return null;
        const fresh = groupLibrary.makeGroup({
            name: groupLibrary.uniqueGroupName(ownGroups(), t('emoteMaker.groupNameDefault')),
            look: currentLookDefaults(),
        });
        writeGroups([...ownGroups(), fresh], state.shots);
        selectGroup(fresh.id);
        return allGroups().find((group) => group.id === fresh.id) || fresh;
    }

    function renameGroupById(id, name) {
        if (!groupLibrary) return;
        const group = ownGroups().find((item) => item.id === id);
        const nextName = String(name || '').trim();
        if (!group || group.readOnly || !nextName || nextName === group.name) return;
        writeGroups(groupLibrary.renameGroup(ownGroups(), group.id, nextName), state.shots);
        renderGroupBar();
    }

    async function deleteGroupById(id) {
        if (!groupLibrary) return;
        const group = ownGroups().find((item) => item.id === id);
        if (!group || group.readOnly) return;
        const confirmed = await confirmModal(
            interpolate(t('emoteMaker.groupDeleteConfirm'), { name: group.name }),
        );
        if (!confirmed) return;
        const next = groupLibrary.deleteGroup(ownGroups(), ownShots(), group.id);
        writeGroups(next.groups, next.shots);
        groupPreview = null;
        const scene = getSelectedScene();
        if (scene) syncNativeScene(scene, { autoplay: false });
        if (state.groupId === group.id) {
            const fallback = allGroups()[0] || null;
            selectGroup(fallback ? fallback.id : '');
        } else {
            renderGroupBar();
        }
        showToast(t('emoteMaker.groupDeleted'));
    }

    /**
     * 「按当前配置创建组」：把当前换装/位移记在组上，并把它作为第一条成员存进去。
     * 之后右键加进来的动作不再各记一份，预览和导出都用组上这一套，避免被后来的滑杆带走。
     * 名字先用「未命名组」，要改就在下拉里点那支笔。
     */
    function createGroup() {
        if (!groupLibrary || !templatePackage) return null;
        const groups = ownGroups();
        const look = currentLookDefaults();
        const group = groupLibrary.makeGroup({
            name: groupLibrary.uniqueGroupName(groups, t('emoteMaker.groupNameDefault')),
            look,
        });
        const scene = getSelectedScene();
        let shots = ownShots();
        let withMember = group;
        if (scene) {
            const shot = groupLibrary.makeShot({
                name: `${scene.name || scene.id} · ${Math.round(Number(state.animationProgress) || 0)}%`,
                sourceSceneId: scene.id,
                progress: Number(state.animationProgress) || 0,
                look,
            });
            shots = [...shots, shot];
            withMember = groupLibrary.addItem(group, 'shot', shot.id);
        }
        writeGroups([...groups, withMember], shots);
        selectGroup(withMember.id);
        return withMember;
    }

    /**
     * 标题行上的多选工具。以前它自己独占一行、而且常驻：列表被压矮一截，
     * 用不到的时候也挂着。现在跟着标题走，「取消全选」只在真选了东西时才出现。
     */
    function updateListSelectRow() {
        const tools = $('#list-select-tools');
        if (tools) tools.hidden = !templatePackage;
        const all = $('#select-all-btn');
        if (all) all.hidden = !templatePackage || getVisibleImportedAnimations().length === 0;
        const none = $('#select-none-btn');
        if (none) none.hidden = pickedAnimations.size === 0;
        const count = $('#list-select-count');
        if (count) {
            count.textContent = pickedAnimations.size
                ? interpolate(t('emoteMaker.selectedCount'), { count: pickedAnimations.size })
                : '';
        }
    }

    function setPickedAnimations(ids) {
        pickedAnimations.clear();
        (ids || []).forEach((id) => pickedAnimations.add(String(id)));
        updateAnimationCardPicked();
        updateListSelectRow();
    }

    /** 多选里的动画 → 各自对应的场景（沿用拖拽那条解析：找不到就拒绝，不猜视角） */
    function pickedSceneEntries() {
        const out = [];
        const missing = [];
        pickedAnimations.forEach((id) => {
            const scene = window.NekoEmotePngtuberPack
                ? packResolveScene(id) : null;
            if (scene) out.push(scene);
            else missing.push(id);
        });
        return { scenes: out, missing };
    }

    function packResolveScene(animationId) {
        const packModule = window.NekoEmotePngtuberPack;
        if (!packModule || typeof packModule.resolveSceneForDrag !== 'function') return null;
        return packModule.resolveSceneForDrag(getImportedScenes(), {
            kind: 'animation', animationId,
        });
    }

    function addPickedToActiveGroup() {
        // 没选组 / 选的是导入的只读组：就地新建一个再放进来，而不是让人先去找「创建组」
        const group = ensureWritableGroup();
        if (!group) {
            showToast(t('emoteMaker.groupReadOnly'));
            return 0;
        }
        const { scenes, missing } = pickedSceneEntries();
        let next = group;
        scenes.forEach((scene) => {
            next = groupLibrary.addItem(next, 'scene', scene.id);
        });
        if (next.items.length !== group.items.length) {
            writeGroups(ownGroups().map((entry) => (entry.id === group.id ? next : entry)), state.shots);
        }
        renderGroupBar();
        if (missing.length) showToast(interpolate(t('emoteMaker.groupPickedMissing'), { count: missing.length }));
        return next.items.length - group.items.length;
    }

    function removePickedFromActiveGroup() {
        const group = activeGroup();
        if (!group || group.readOnly) return 0;
        const ids = new Set(pickedSceneEntries().scenes.map((scene) => scene.id));
        let next = group;
        ids.forEach((id) => { next = groupLibrary.removeItem(next, 'scene', id); });
        if (next.items.length !== group.items.length) {
            writeGroups(ownGroups().map((entry) => (entry.id === group.id ? next : entry)), state.shots);
        }
        renderGroupBar();
        return group.items.length - next.items.length;
    }

    function isPickedInGroup() {
        const group = activeGroup();
        if (!group) return false;
        const ids = pickedSceneEntries().scenes.map((scene) => scene.id);
        return ids.length > 0 && ids.every((id) => groupLibrary.hasItem(group, 'scene', id));
    }

    function showGroupContextMenu(event, animationId) {
        const id = String(animationId);
        // 再右键一次同一条 = 取消多选。菜单是「对当前选中做加入/移出」，
        // 取消本身不是这个动作，所以不再弹出。
        if (pickedAnimations.has(id)) {
            pickedAnimations.delete(id);
            updateAnimationCardPicked();
            updateListSelectRow();
            hideGroupContextMenu();
            return;
        }
        pickedAnimations.add(id);
        updateAnimationCardPicked();
        updateListSelectRow();
        const menu = $('#group-context-menu');
        if (!menu) return;
        const group = activeGroup();
        // 菜单上的「加入」不要求先选中一个组：没有就新建一个再放。
        const canWrite = Boolean(templatePackage && groupLibrary);
        const inGroup = isPickedInGroup();
        const add = $('#context-add-btn');
        const remove = $('#context-remove-btn');
        if (add) {
            add.hidden = !canWrite || inGroup;
            add.disabled = false;
        }
        if (remove) remove.hidden = !canWrite || !inGroup;
        const label = $('#context-group-name');
        if (label) {
            // 没有可写的组时，报出即将建出来的那个名字，别让人以为加进了空气
            label.textContent = group && !group.readOnly
                ? group.name
                : t('emoteMaker.groupNameDefault');
        }
        menu.hidden = false;
        menu.style.left = `${Math.max(8, event.clientX)}px`;
        menu.style.top = `${Math.max(8, event.clientY)}px`;
    }

    function hideGroupContextMenu() {
        const menu = $('#group-context-menu');
        if (menu) menu.hidden = true;
    }

    function updateAnimationCardPicked() {
        if (!animationList) return;
        animationList.querySelectorAll('[data-animation-id]').forEach((card) => {
            const on = pickedAnimations.has(String(card.dataset.animationId));
            card.classList.toggle('is-picked', on);
            card.setAttribute('aria-selected', String(on));
        });
    }

    function addCurrentSceneToGroup() {
        if (!state.selectedSceneId) {
            // 静默返回会被当成"按钮坏了"；这里连"加入哪一格"都没定，必须说
            showToast(t('emoteMaker.groupNoSelection'));
            return;
        }
        const group = ensureWritableGroup();
        if (!group) return;
        const next = groupLibrary.addItem(group, 'scene', state.selectedSceneId);
        if (next.items.length === group.items.length) return;
        writeGroups(ownGroups().map((item) => (item.id === group.id ? next : item)), state.shots);
        renderAnimations();
        renderGroupBar();
        showToast(t('emoteMaker.groupSceneAdded'));
    }

    /**
     * 组自己记下的换装/位移。旧组没这个字段时，用第一条取景上的那份。
     * 导入的只读组没有这份记录，预览就还是编辑器当前的数值。
     */
    function recordedGroupLook(group) {
        if (!group || group.readOnly) return null;
        if (group.look && group.look.transform) return group.look;
        const firstShot = (group.items || []).find((item) => item.kind === 'shot');
        return firstShot ? (findShotById(firstShot.ref)?.look || null) : null;
    }

    /**
     * 点组成员：用组记下的数值画这条动作。
     * 不写 state，动作列表和形象包继续用编辑器里那一套。
     */
    function previewGroupMember(item, look) {
        let scene = null;
        let progress = 0;
        if (item.kind === 'shot') {
            const shot = findShotById(item.ref);
            if (!shot) return false;
            scene = getImportedScenes().find((entry) => entry.id === shot.sourceSceneId) || null;
            progress = Number(shot.progress) || 0;
        } else {
            scene = getImportedScenes().find((entry) => entry.id === item.ref) || null;
        }
        if (!scene) return false;
        groupPreview = {
            look: look || {},
            scene,
            animationId: scene.animation?.id || state.animationId,
            progress,
        };
        stopAnimationLoops();
        const synced = syncNativeScene(scene, {
            autoplay: true,
            animationId: groupPreview.animationId,
            progress: groupPreview.progress,
            commit: false,
        });
        if (!synced) {
            groupPreview = null;
            return false;
        }
        state.animationPlaying = true;
        startSpineAnimationLoop();
        updateAnimationControls();
        return true;
    }

    function groupMemberInfo(item) {
        if (item.kind === 'shot') {
            const shot = findShotById(item.ref);
            return {
                name: shot ? shot.name : item.ref,
                kind: t('emoteMaker.groupMemberShot'),
                missing: !shot || !getImportedScenes().some((entry) => entry.id === shot.sourceSceneId),
            };
        }
        const scene = getImportedScenes().find((entry) => entry.id === item.ref);
        return {
            name: scene ? (scene.name || scene.id) : item.ref,
            kind: t('emoteMaker.segmentScenes'),
            missing: !scene,
        };
    }

    function removeGroupMember(group, item) {
        const next = groupLibrary.removeItem(group, item.kind, item.ref);
        writeGroups(ownGroups().map((entry) => (entry.id === group.id ? next : entry)), state.shots);
        renderAll();
    }

    function renderGroupMembers(group, writable) {
        const host = $('#group-members');
        if (!host) return;
        const items = group ? (group.items || []) : [];
        if (!items.length) {
            const empty = document.createElement('p');
            empty.className = 'group-members-empty';
            // 有组但没动作：一句去添加？加一个加号（顶替删掉的「加入本组」按钮）。
            // 连组都没有（下拉显示「暂无组」的那种）：组员区就别再指挥人做事了。
            empty.textContent = group ? t('emoteMaker.groupPickFirst') : t('emoteMaker.groupEmptyList');
            if (group && writable) {
                const add = document.createElement('button');
                add.type = 'button';
                add.id = 'add-scene-to-group-btn';
                add.className = 'group-add-btn';
                add.textContent = '+';
                add.setAttribute('aria-label', t('emoteMaker.addToGroup'));
                empty.appendChild(add);
            }
            host.replaceChildren(empty);
            return;
        }
        host.replaceChildren(...items.map((item) => {
            const info = groupMemberInfo(item);
            const row = document.createElement('div');
            row.className = 'group-member' + (info.missing ? ' is-missing' : '');
            row.dataset.memberKind = item.kind;
            row.dataset.memberRef = item.ref;

            const pick = document.createElement('button');
            pick.type = 'button';
            pick.className = 'group-member-pick';
            const chip = document.createElement('small');
            chip.className = 'group-member-kind';
            chip.textContent = info.kind;
            const name = document.createElement('span');
            name.className = 'group-member-name';
            name.textContent = info.name;
            pick.append(chip, name);
            pick.title = info.missing ? t('emoteMaker.groupMemberMissing') : info.name;
            pick.disabled = info.missing;
            pick.setAttribute('aria-disabled', String(info.missing));
            if (!info.missing) {
                pick.addEventListener('click', () => {
                    const look = recordedGroupLook(group);
                    if (look) {
                        previewGroupMember(item, look);
                        return;
                    }
                    groupPreview = null;
                    const sceneId = item.kind === 'shot'
                        ? findShotById(item.ref)?.sourceSceneId
                        : item.ref;
                    const scene = getImportedScenes().find((entry) => entry.id === sceneId);
                    if (scene) selectImportedScene(scene);
                });
            }
            row.append(pick);

            if (writable && !info.missing) {
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'group-member-remove';
                remove.textContent = '×';
                remove.setAttribute('aria-label', `${t('emoteMaker.groupRemoveMember')} · ${info.name}`);
                remove.addEventListener('click', () => removeGroupMember(group, item));
                row.append(remove);
            } else if (info.missing) {
                // 来源没了就别只留一个灰条：说清为什么点不动
                const note = document.createElement('span');
                note.className = 'group-member-note';
                note.textContent = t('emoteMaker.groupMemberMissing');
                row.append(note);
            }
            return row;
        }));
    }

    // ── 锚定在控件上的说明气泡 ────────────────────────────────────────────────
    // 原生 title 要悬停半秒才出、位置也不受控；跟着光标跑的框又显得毛躁。
    // [data-tip] 统一做成钉在触发控件上方（放不下就翻到下方）的带尖角气泡，
    // 位置只跟控件走，不跟鼠标。
    function bindCursorTips() {
        const tip = $('#emote-cursor-tip');
        if (!tip || tip.dataset.bound === '1') return;
        tip.dataset.bound = '1';
        let owner = null;

        const place = (target) => {
            const margin = 10;
            const gap = 10;
            const box = tip.getBoundingClientRect();
            const rect = target.getBoundingClientRect();
            const centered = rect.left + rect.width / 2 - box.width / 2;
            const left = Math.min(
                Math.max(margin, centered),
                window.innerWidth - box.width - margin,
            );
            // 默认在控件上方；上方放不下翻到下方，箭头跟着换边。
            const below = rect.top - box.height - gap < margin;
            const top = below ? rect.bottom + gap : rect.top - box.height - gap;
            // 箭头横向钉在触发控件中心（气泡被屏幕边夹住时也不跑偏）。
            const arrowX = Math.min(
                Math.max(rect.left + rect.width / 2 - left, 14),
                box.width - 14,
            );
            tip.style.setProperty('--tip-arrow-x', `${Math.round(arrowX)}px`);
            tip.classList.toggle('is-below', below);
            tip.style.left = `${Math.round(left)}px`;
            tip.style.top = `${Math.round(top)}px`;
        };
        const show = (target) => {
            owner = target;
            tip.textContent = target.dataset.tip || '';
            // 先显示再定位：hidden 的元素量出来是 0×0，会算出贴到左上角的位置
            tip.hidden = false;
            place(target);
            target.setAttribute('aria-describedby', tip.id);
        };
        const hide = () => {
            if (owner) owner.removeAttribute('aria-describedby');
            owner = null;
            tip.hidden = true;
        };
        const tipHost = (node) => ((node && node.closest) ? node.closest('[data-tip]') : null);

        document.addEventListener('pointerover', (event) => {
            if (event.pointerType && event.pointerType !== 'mouse') return;
            const target = tipHost(event.target);
            if (!target || target === owner || !target.dataset.tip) return;
            show(target);
        });
        document.addEventListener('pointerout', (event) => {
            if (owner && !owner.contains(event.relatedTarget)) hide();
        });
        // 键盘用户也能拿到：聚焦即出，同一套锚定位置
        document.addEventListener('focusin', (event) => {
            const target = tipHost(event.target);
            if (!target || target === owner || !target.dataset.tip) return;
            show(target);
        });
        document.addEventListener('focusout', hide);
        // 滚动/换行会让锚点跑掉，直接收掉比留着一个假位置好
        window.addEventListener('scroll', hide, true);
        window.addEventListener('resize', hide);
    }

    /** [data-tip-key] 的问号按钮：把键翻译成 [data-tip]，气泡那套就能接管。 */
    function bindTipKeys() {
        document.querySelectorAll('[data-tip-key]').forEach((el) => {
            el.dataset.tip = t(el.dataset.tipKey);
        });
    }

    function bindGroupPanelToggle() {
        const section = $('#group-section');
        if (!section || section.dataset.toggleBound === '1') return;
        section.dataset.toggleBound = '1';
        // 只挂一次：挂在每次重绘里再配 once，第一次收起就会把监听器消耗掉，
        // 之后再收起就再不存状态了
        section.addEventListener('toggle', () => {
            const expanded = section.open && state.groupPanelOpen === false;
            state.groupPanelOpen = section.open;
            persistLayout();
            if (expanded && !section.hidden && state.inspectorTab === 'export') {
                showPageTip('emoteMaker.batchExportTip', { returnFocus: () => section.querySelector('summary') });
            }
        });
    }

    /** 气泡里一行：组名 + 张数。点一下就是"导出这一组"。 */
    function createGroupPickerRow(group) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'group-picker-item';
        row.dataset.groupId = group.id;
        row.setAttribute('role', 'listitem');
        const name = document.createElement('span');
        name.className = 'group-picker-name';
        name.textContent = group.name;
        // 只读组显示它自带的张数，自建组显示攒了几条
        const total = group.readOnly ? (group.count || group.items.length) : group.items.length;
        const count = document.createElement('small');
        count.className = 'group-picker-count';
        count.textContent = interpolate(t('emoteMaker.groupMemberCount'), { count: total });
        row.append(name, count);
        if (group.readOnly) {
            const tag = document.createElement('small');
            tag.className = 'group-picker-tag';
            tag.textContent = t('emoteMaker.groupImportedTag');
            row.append(tag);
        }
        // 只读只是"不能改组员"，整组照导不误 —— 那正是"把这份场景列表全导出来"的入口
        row.disabled = !total || !hasSpineRenderer();
        row.addEventListener('click', () => {
            if (groupExportBusy || groupExportPicking) return;
            if (!hasSpineRenderer()) {
                closeGroupPicker();
                showToast(t('emoteMaker.groupExportNoRuntime'));
                return;
            }
            if (!planGroupExportFor(group, groupLibrary.MAX_ITEMS_PER_GROUP).plan.ready) {
                closeGroupPicker();
                showToast(t('emoteMaker.groupExportEmpty'));
                return;
            }
            // 选择框要在气泡藏起来之前就打开。先关掉再调的话，内嵌网页会直接拒绝。
            groupExportPicking = true;
            const picking = requestExportDirectory();
            closeGroupPicker();
            void exportGroup(group, $('#export-group-open-btn'), picking)
                .finally(() => { groupExportPicking = false; });
        });
        return row;
    }

    function renderGroupPicker() {
        const picker = $('#group-export-picker');
        const trigger = $('#export-group-open-btn');
        if (!picker || !trigger) return;
        const open = Boolean(state.groupPickerOpen) && Boolean(templatePackage);
        if (!open) {
            picker.hidden = true;
            trigger.setAttribute('aria-expanded', 'false');
            return;
        }
        const list = $('#group-picker-list');
        const groups = allGroups();
        if (list) {
            const rows = groups.length
                ? groups.map((group) => createGroupPickerRow(group))
                : [Object.assign(document.createElement('p'), {
                    className: 'group-picker-empty',
                    textContent: t('emoteMaker.batchExportPickerEmpty'),
                })];
            if (!hasSpineRenderer()) {
                // 行是灰的，但灰不等于说清了为什么灰：disabled 的按钮连悬停提示都给不出
                rows.push(Object.assign(document.createElement('p'), {
                    className: 'group-picker-note',
                    textContent: t('emoteMaker.groupExportNoRuntime'),
                }));
            }
            list.replaceChildren(...rows);
        }
        picker.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
    }

    function openGroupPicker() {
        if (!templatePackage || groupExportBusy) return;
        state.groupPickerOpen = true;
        renderGroupPicker();
    }

    function closeGroupPicker() {
        if (!state.groupPickerOpen) return;
        state.groupPickerOpen = false;
        renderGroupPicker();
    }

    function renderGroupBar() {
        bindGroupPanelToggle();
        const section = $('#group-section');
        if (section) {
            section.hidden = !templatePackage;
            if (templatePackage) section.open = state.groupPanelOpen;
        }
        const help = $('#new-group-help');
        // 说明交给跟着光标的气泡，不用原生 title（要等半秒）、也不弹底部 toast
        if (help) help.dataset.tip = t('emoteMaker.newGroupHelp');
        if (!templatePackage || !groupLibrary) return;
        renderBatchExportGroupSelect();
        const active = activeGroup();
        const writable = Boolean(active && !active.readOnly);
        const summary = $('#group-summary-state');
        if (summary) {
            // 收起时也要看得见"现在攒的是哪一组、几张"，否则收起就等于信息没了
            summary.textContent = active
                ? `${active.name} · ${active.items.length}`
                : t('emoteMaker.groupNonePicked');
        }
        renderGroupMembers(active, writable);
        renderGroupPicker();
    }

    /** 烘一项 → 一段字节。PNG 使用取景时间，GIF 播放完整动作。 */
    async function produceGroupFile(group, item, format) {
        return queueStageCapture(Symbol(), STAGE_CAPTURE_INTERACTIVE + 10, 'export',
            () => produceGroupFileNow(group, item, format));
    }

    async function produceGroupFileNow(group, item, format) {
        const encoder = window.NekoEmoteGifEncoder;
        const sheet = window.NekoEmoteSheet;
        const edge = state.exportResolution || GIF_EXPORT_WIDTH;
        const fps = state.exportFps || GIF_EXPORT_FPS;
        let scene = getImportedScenes().find((entry) => entry.id === item.ref) || null;
        let timeWindow;
        if (item.kind === 'shot') {
            const shot = findShotById(item.ref);
            if (!shot) throw new Error('missing-shot');
            scene = getImportedScenes().find((entry) => entry.id === shot.sourceSceneId) || null;
            if (!scene) throw new Error('scene_unavailable');
            // PNG shots preserve their saved pose; GIF members still play the full action.
            if (format === 'png') {
                const duration = Number(getImportedAnimationDuration(scene.animation?.id)) || 0;
                const from = duration * Math.max(0, Math.min(100, Number(shot.progress) || 0)) / 100;
                timeWindow = { from, to: from + 1 / fps };
            }
        }
        if (!scene) throw new Error('missing-scene');
        // 组上那一套数值盖到每一条上。没有记录的组（模板自带的列表）仍用编辑器当前值。
        const look = recordedGroupLook(group);
        if (look) captureLook = look;
        let frames;
        try {
            frames = await captureGifFramesNow({
                scene,
                timeWindow,
                maxFrames: format === 'png' ? 1 : GROUP_EXPORT_MAX_FRAMES,
                fps,
                maxDimension: edge,
            });
        } finally {
            captureLook = null;
        }
        if (!frames || !frames.length) throw new Error('empty');
        if (format === 'png') {
            return sheet.canvasToPngBytes(sheet.firstFrameCanvas(frames[0]));
        }
        if (!encoder || typeof encoder.encode !== 'function') throw new Error('encoder-unavailable');
        return encoder.encode(frames, {
            width: frames[0].width,
            height: frames[0].height,
            colors: GIF_EXPORT_COLORS,
            transparent: state.exportTransparent !== false,
            loopCount: 0,
        });
    }

    /**
     * 选导出位置。返回值：
     *   FileSystemDirectoryHandle —— 浏览器自己的目录句柄
     *   { kind: 'path', path } —— 桌面桥或本机对话框给出的绝对路径
     *   'cancelled' —— 用户关掉了对话框
     *   null —— 哪条路都没打开，调用方改走内存下载
     *
     * 必须在点击处理函数里同步调用：showDirectoryPicker 晚于第一个 await，
     * 或者点到的按钮已经被藏起来，浏览器会直接拒绝，对话框不会出现。
     */
    function requestExportDirectory() {
        const host = window.nekoHost;
        if (host && typeof host.pickDirectory === 'function') {
            return pickExportDirectoryFromHost();
        }
        if (typeof window.showDirectoryPicker !== 'function') {
            return pickExportDirectoryFromBackend();
        }
        let pending;
        try {
            // 不传 id：记住的上次目录失效时，有的环境会不弹窗直接拒绝。
            pending = window.showDirectoryPicker({ mode: 'readwrite' });
        } catch (error) {
            pending = Promise.reject(error);
        }
        return pending.catch((error) => {
            if (error && error.name === 'AbortError') return 'cancelled';
            console.warn('[emote-maker] 浏览器目录选择失败，改走系统对话框', error);
            return pickExportDirectoryFromBackend();
        });
    }

    async function pickExportDirectoryFromHost() {
        try {
            const result = await window.nekoHost.pickDirectory({
                title: t('emoteMaker.groupExportPickFolder'),
            });
            if (!result || typeof result !== 'object') return pickExportDirectoryFromBackend();
            if (result.cancelled) return 'cancelled';
            const path = String(result.selected_root || result.path || '').trim();
            if (!path) return pickExportDirectoryFromBackend();
            return { kind: 'path', path };
        } catch (error) {
            console.warn('[emote-maker] 桌面目录选择失败', error);
            return pickExportDirectoryFromBackend();
        }
    }

    async function pickExportDirectoryFromBackend() {
        try {
            const response = await fetch('/api/storage/location/pick-directory', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ start_path: '' }),
            });
            const payload = await response.json().catch(() => ({}));
            if (payload && payload.cancelled) return 'cancelled';
            const path = String((payload && payload.selected_root) || '').trim();
            if (!response.ok || !payload || payload.ok !== true || !path) return null;
            return { kind: 'path', path };
        } catch (error) {
            console.warn('[emote-maker] 系统目录选择失败', error);
            return null;
        }
    }

    async function prepareExportFolder(parentPath, folderName) {
        const response = await fetch('/api/emote-maker/export/folder', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Neko-Emote-Export': '1',
            },
            body: JSON.stringify({ directory: parentPath, folder: folderName }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload || !payload.success) {
            throw new Error((payload && payload.error) || 'folder');
        }
        return payload;
    }

    async function writeExportFile(disk, file) {
        const body = new FormData();
        body.append('directory', disk.path);
        body.append('folder', disk.folder);
        body.append('name', file.path);
        body.append('file', new Blob([file.data]), file.path);
        const response = await fetch('/api/emote-maker/export/file', {
            method: 'POST',
            headers: { 'X-Neko-Emote-Export': '1' },
            body,
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error((payload && payload.error) || 'write-failed');
        }
    }

    async function zipExportFolder(disk, names) {
        const response = await fetch('/api/emote-maker/export/zip', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Neko-Emote-Export': '1',
            },
            body: JSON.stringify({
                directory: disk.path,
                folder: disk.folder,
                names,
                zip_name: `${disk.folder}.zip`,
            }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error((payload && payload.error) || 'zip-failed');
        }
    }

    async function uniqueChildName(parent, name) {
        const taken = new Set();
        for await (const [key] of parent.entries()) taken.add(key);
        if (!taken.has(name)) return name;
        const dot = name.lastIndexOf('.');
        const stem = dot > 0 ? name.slice(0, dot) : name;
        const ext = dot > 0 ? name.slice(dot) : '';
        let index = 2;
        let candidate = `${stem} ${index}${ext}`;
        while (taken.has(candidate)) {
            index += 1;
            candidate = `${stem} ${index}${ext}`;
        }
        return candidate;
    }

    async function writeStoredZip(parent, folder, names, zipName) {
        const fflate = window.fflate;
        if (!fflate || typeof fflate.Zip !== 'function' || typeof fflate.ZipDeflate !== 'function') {
            throw new Error('zip-unavailable');
        }
        const fileName = await uniqueChildName(parent, zipName);
        const handle = await parent.getFileHandle(fileName, { create: true });
        const writable = await handle.createWritable();
        const chunks = [];
        let failed = null;
        const zip = new fflate.Zip((err, data) => {
            if (err) {
                failed = err;
                return;
            }
            if (data && data.length) chunks.push(data);
        });
        const flush = async () => {
            while (chunks.length) await writable.write(chunks.shift());
            if (failed) throw failed;
        };
        try {
            for (const name of names) {
                const fileHandle = await folder.getFileHandle(name);
                const file = await fileHandle.getFile();
                const bytes = new Uint8Array(await file.arrayBuffer());
                const entry = new fflate.ZipDeflate(name, { level: 6 });
                zip.add(entry);
                entry.push(bytes, true);
                await flush();
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
            zip.end();
            await flush();
            await writable.close();
        } catch (error) {
            try { await writable.abort(); } catch (_) { /* 半成品丢掉，别留一个打不开的 zip */ }
            throw error;
        }
    }

    function planGroupExportFor(group, limit, requestedFormat = state.exportFormat) {
        const format = requestedFormat === 'png' ? 'png' : 'gif';
        const plan = groupLibrary.planGroupExport({
            group,
            shots: ownShots(),
            ext: format,
            nameOf: (ref) => {
                const scene = getImportedScenes().find((entry) => entry.id === ref);
                return scene ? (scene.name || scene.id) : '';
            },
            limit,
        });
        return { format, plan };
    }

    /** 导出气泡/按钮真正要导的那一组 —— 由气泡里的行决定，不是"当前下拉选中的那组"。 */
    async function exportGroup(group, exportTrigger, directoryPick) {
        if (groupExportBusy) return;
        if (!group) return;
        if (!hasSpineRenderer()) {
            // 没有运行时 captureGifFrames 只能给到当前画面 —— 宁可不导，也不下一个是
            // "每张都长一样"的包，那种东西看起来像成功了。
            showToast(t('emoteMaker.groupExportNoRuntime'));
            return;
        }
        // 点击处已经把选择框打开了。这里再调一次只作为没经过那次点击的退路。
        const picked = await (directoryPick || requestExportDirectory());
        if (picked === 'cancelled') return;
        const savingToFolder = Boolean(picked);
        const planned = planGroupExportFor(
            group,
            savingToFolder ? groupLibrary.MAX_ITEMS_PER_GROUP : groupLibrary.MAX_BATCH,
        );
        const format = planned.format;
        let plan = planned.plan;
        if (!plan.ready) {
            showToast(t('emoteMaker.groupExportEmpty'));
            return;
        }
        if (!savingToFolder) showToast(t('emoteMaker.groupExportPickFailed'));
        let folder = null;
        let folderName = '';
        let disk = null;
        if (picked && picked.kind === 'path') {
            try {
                const prepared = await prepareExportFolder(
                    picked.path,
                    groupLibrary.sanitizeFileName(group.name, 'group'),
                );
                disk = { path: prepared.directory, folder: prepared.folder };
                folderName = prepared.folder;
                folder = disk;
            } catch (error) {
                console.warn('[emote-maker] 创建导出文件夹失败', error);
                showToast(t('emoteMaker.groupExportPickFailed'));
                plan = planGroupExportFor(group, groupLibrary.MAX_BATCH, format).plan;
            }
        } else if (picked) {
            try {
                folderName = await uniqueChildName(
                    picked,
                    groupLibrary.sanitizeFileName(group.name, 'group'),
                );
                folder = await picked.getDirectoryHandle(folderName, { create: true });
            } catch (error) {
                console.warn('[emote-maker] 创建导出文件夹失败', error);
                showToast(t('emoteMaker.groupExportPickFailed'));
                plan = planGroupExportFor(group, groupLibrary.MAX_BATCH, format).plan;
            }
        }
        groupExportBusy = true;
        groupExportAbort = false;
        setPackBusy(true);
        const trigger = exportTrigger && exportTrigger.nodeType === 1 ? exportTrigger : null;
        const triggerLabel = trigger ? trigger.textContent : '';
        if (trigger) trigger.textContent = t('emoteMaker.groupExportCancel');
        const previousSceneId = state.selectedSceneId;
        const previousAnimationId = state.animationId;
        const previousProgress = state.animationProgress;
        const previousOverwrite = state.adjustOverwrite;
        const previousPlaying = state.animationPlaying;
        let result = null;
        try {
            result = await groupLibrary.runGroupExport({
                plan,
                produce: (item) => produceGroupFile(group, item, format),
                store: disk ? (file) => writeExportFile(disk, file) : folder ? async (file) => {
                    const handle = await folder.getFileHandle(file.path, { create: true });
                    const stream = await handle.createWritable();
                    try {
                        await stream.write(file.data);
                        await stream.close();
                    } catch (error) {
                        try { await stream.abort(); } catch (_) { /* 这张没写完就丢掉 */ }
                        throw error;
                    }
                } : undefined,
                zipStored: disk ? async (names) => {
                    setGroupExportStatus(t('emoteMaker.groupExportZipping'));
                    await zipExportFolder(disk, names);
                } : folder ? async (names) => {
                    setGroupExportStatus(t('emoteMaker.groupExportZipping'));
                    await writeStoredZip(picked, folder, names, `${folderName}.zip`);
                } : undefined,
                zip: async (files) => {
                    const entries = {};
                    files.forEach((file) => { entries[file.path] = file.data; });
                    return new Blob([window.fflate.zipSync(entries, { level: 6 })], { type: 'application/zip' });
                },
                shouldAbort: () => groupExportAbort,
                onProgress: ({ done, total }) => setGroupExportStatus(
                    interpolate(t('emoteMaker.groupExporting'), { done, total })),
            });
        } catch (error) {
            console.warn('[emote-maker] 整组导出失败', error);
            showToast(t(folder ? 'emoteMaker.groupExportZipFailed' : 'emoteMaker.exportGifFailed'));
        } finally {
            groupExportBusy = false;
            setPackBusy(false);
            setGroupExportStatus('');
            if (trigger && trigger.isConnected) trigger.textContent = triggerLabel;
            state.adjustOverwrite = previousOverwrite;
            state.selectedSceneId = previousSceneId;
            state.animationId = previousAnimationId;
            state.animationProgress = previousProgress;
            state.animationPlaying = previousPlaying;
            syncLiveCanvas({ autoplay: previousPlaying });
            renderAll();
        }
        if (!result) return;
        if (result.blob) {
            fallbackDownload(
                result.blob,
                `${groupLibrary.sanitizeFileName(group.name, 'group')}.zip`,
                'emoteMaker.groupExportDone', 'emoteMaker.exportGifFailed');
        } else if (result.savedToFolder && !result.aborted) {
            showToast(t('emoteMaker.groupExportFolderDone'));
        }
        if (result.aborted) showToast(t('emoteMaker.groupExportCancelled'));
        const lost = result.failed.length + plan.skipped.length;
        if (lost) showToast(interpolate(t('emoteMaker.groupExportFailed'), { count: lost }));
        if (plan.truncated) showToast(interpolate(t('emoteMaker.groupExportTruncated'), { count: plan.limit }));
    }

    let animationCardsSignature = '';

    function renderAnimations() {
        if (!animationList) return;
        // 列表在画就顺手刷新标题行上那排多选工具：以前只有点过一张之后才现身，
        // 第一次进来的人根本看不见有这东西
        updateListSelectRow();
        const animations = templatePackage
            ? getVisibleImportedAnimations().map((item) => ({
                id: item.id,
                name: getCatalogLabel(item),
                duration: getImportedAnimationDuration(item.id),
                metadataOnly: !animationIsPlayable(item.id),
            }))
            : [];
        const signature = listsSignature(animations, 'anim');
        if (signature && signature === animationCardsSignature) {
            // 只有选中项变了：切 class 就够，不必重建 191 张卡
            updateAnimationCardSelection();
            updateAnimationControls();
            return;
        }
        animationCardsSignature = signature;
        if (!templatePackage) {
            const empty = document.createElement('div');
            empty.className = 'empty-template-state';
            empty.setAttribute('role', 'status');
            empty.textContent = t('emoteMaker.selectTemplateArchive');
            replaceAnimationList(empty);
            updateAnimationControls();
            return;
        }
        if (templatePackage && !animations.length) {
            replaceAnimationList(Object.assign(document.createElement('div'), {
                className: 'empty-template-state',
                textContent: t('emoteMaker.noTemplates')
            }));
            updateAnimationControls();
            return;
        }
        replaceAnimationList(...animations.map((animation) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'animation-card' + (animation.id === state.animationId ? ' is-selected' : '');
            button.dataset.animationId = animation.id;
            const icon = document.createElement('span');
            // 以前每卡一个 <img src=同一张图>：几百个元素、几百份解码调度，只为同一个播放符
            icon.className = 'animation-card-icon';
            icon.setAttribute('aria-hidden', 'true');
            const label = document.createElement('span');
            label.textContent = animation.nameKey ? t(animation.nameKey) : animation.name;
            const duration = document.createElement('small');
            duration.textContent = animation.metadataOnly
                ? t('emoteMaker.templatePendingPreview')
                : `${Number(animation.duration || 0).toFixed(1)}s`;
            button.append(icon, label, duration);
            button.disabled = Boolean(animation.metadataOnly);
            button.setAttribute('aria-pressed', String(animation.id === state.animationId));
            if (animation.metadataOnly) button.setAttribute('aria-disabled', 'true');
            attachDragSource(button, {
                kind: 'animation',
                animationId: animation.id,
                label: animation.nameKey ? t(animation.nameKey) : animation.name,
                thumbUrl: PLAY_ICON_URL,
            });
            button.addEventListener('click', (event) => {
                if (Date.now() < suppressClickUntil) return;
                if (event.ctrlKey || event.metaKey) {
                    // 按住 Ctrl/⌘ 点 = 加进/移出多选集合，不改当前播放的那条；
                    // 这条也不要求"能播"，同上
                    const id = String(animation.id);
                    if (pickedAnimations.has(id)) pickedAnimations.delete(id);
                    else pickedAnimations.add(id);
                    updateAnimationCardPicked();
                    updateListSelectRow();
                    return;
                }
                if (animation.metadataOnly) return;
                groupPreview = null;
                state.animationId = animation.id;
                const scene = getImportedScenes().find((entry) => entry.animation?.id === animation.id);
                if (scene) state.selectedSceneId = scene.id;
                state.animationProgress = 0;
                state.animationPlaying = false;
                // Selecting a card starts playback. Stop the previous loop
                // so an old RAF cannot overwrite the new selection.
                stopAnimationLoops();
                startSelectedAnimationPlayback();
                updateAnimationCardSelection();
                updateAnimationControls();
                void renderCanvas();
                persistTemplateSession();
            });
            button.title = animation.metadataOnly ? t('emoteMaker.templateMetadataOnly') : label.textContent;
            // 右键：未选中就选上并打开加组菜单，已选中再点一次就取消。
            // 不设"能不能播"这个门槛：攒一份要导出的清单不需要播放，
            // 真烘不出来由「批量导出」的气泡统一拦（那里才有渲染器可用与否的判断）
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                showGroupContextMenu(event, animation.id);
            });
            return button;
        }));
        updateAnimationControls();
    }

    function getCurrentAnimation() {
        if (templatePackage) {
            const imported = getImportedAnimations().find((animation) => animation.id === state.animationId);
            return {
                id: state.animationId,
                name: imported ? getCatalogLabel(imported) : state.animationId,
                duration: getImportedAnimationDuration(state.animationId) || 1
            };
        }
        return { id: state.animationId, name: state.animationId, duration: 1 };
    }

    function formatAnimationTime(totalSeconds) {
        const total = Math.max(0, Math.round(Number(totalSeconds) || 0));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        const pad = (value) => String(value).padStart(2, '0');
        return hours > 0
            ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
            : `${pad(minutes)}:${pad(seconds)}`;
    }

    // 这些是"上一次写进去的值"：播放循环每帧都会调这里，不比对就会每帧都
    // 写一次文本和属性（实测约 122 次文本写入/秒），逼浏览器重算布局和无障碍树。
    const controlsMemo = { progress: null, speed: null, time: '', play: null, disabled: null };

    function updateAnimationControls() {
        if (animationProgress) {
            if (controlsMemo.progress !== state.animationProgress) {
                controlsMemo.progress = state.animationProgress;
                animationProgress.value = state.animationProgress;
            }
        }
        if (animationSpeed && controlsMemo.speed !== state.animationSpeed) {
            controlsMemo.speed = state.animationSpeed;
            animationSpeed.value = state.animationSpeed;
        }
        if (animationTime) {
            const currentAnimation = getCurrentAnimation() || { duration: 0 };
            const totalSeconds = Number(currentAnimation.duration) || 0;
            const seconds = Math.round(totalSeconds * state.animationProgress / 100);
            const label = formatAnimationTime(seconds);
            if (controlsMemo.time !== label) {
                controlsMemo.time = label;
                animationTime.textContent = label;
            }
        }
        const playButton = $('#play-animation-btn');
        const nativeReady = hasSpineRenderer();
        // 只有清单变了才重算可见动画数（见 listsSignature）；以前每帧 new Set + filter
        const hasAnimations = hasPlayableAnimations();
        const disabled = !hasAnimations || (Boolean(templatePackage) && !nativeReady);
        if (playButton) {
            const playLabel = state.animationPlaying ? t('common.pause') : t('common.play');
            if (controlsMemo.play !== playLabel || controlsMemo.disabled !== disabled) {
                controlsMemo.play = playLabel;
                controlsMemo.disabled = disabled;
                playButton.textContent = playLabel;
                playButton.disabled = disabled;
                if (disabled) playButton.title = t('emoteMaker.templateMetadataOnly');
                else playButton.removeAttribute('title');
            }
        }
        if (animationProgress) {
            if (animationProgress.disabled !== disabled) {
                animationProgress.disabled = disabled;
                animationProgress.setAttribute('aria-disabled', String(disabled));
            }
        }
        if (animationSpeed) {
            // 同样只在变了时写：这条以前每帧都 setAttribute 一次
            if (animationSpeed.disabled !== disabled) {
                animationSpeed.disabled = disabled;
                animationSpeed.setAttribute('aria-disabled', String(disabled));
            }
        }
    }

    function toggleAnimationPlayback() {
        if (templatePackage && !hasSpineRenderer()) {
            showToast(t('emoteMaker.templateMetadataOnly'));
            return;
        }
        state.animationPlaying = !state.animationPlaying;
        if (templatePackage && hasSpineRenderer()) {
            if (state.animationPlaying) {
                spineRenderer.play();
                startSpineAnimationLoop();
            } else {
                spineRenderer.pause();
                stopSpineAnimationLoop();
            }
            updateAnimationControls();
            return;
        }
        state.animationPlaying = false;
        updateAnimationControls();
    }


    /**
     * Start the editor-owned playback loop after an animation has been
     * selected, preventing a stale RAF/Spine track from leaving the preview
     * frozen on the previous animation.
     */
    function startSelectedAnimationPlayback() {
        if (templatePackage) {
            if (!hasSpineRenderer()) return false;
            // Stop the previous native track before validating the new scene;
            // malformed/paused scene metadata must not leave an old Spine
            // animation playing behind a stopped editor loop.
            try { spineRenderer.pause(); } catch (_) { /* best effort */ }
            const synced = syncNativeScene(getSelectedScene(), { autoplay: true });
            if (!synced) {
                // A failed scene switch must not leave the old native track
                // running while the UI reports the newly selected animation.
                try { spineRenderer.pause(); } catch (_) { /* best effort */ }
                state.animationPlaying = false;
                return false;
            }
            state.animationPlaying = true;
            startSpineAnimationLoop();
            return true;
        }
        state.animationPlaying = false;
        return false;
    }



    const RECENT_COLORS_KEY = 'neko.emote.recentColors';
    const HEX_COLOR = /^#[0-9a-f]{6}$/i;
    const PART_TINT_FIELDS = ['tintColor', 'tintBlackColor'];
    let hoveredColorPart = '';
    let colorHighlightStart = 0;
    let colorHighlightFrame = 0;
    let colorHighlightVisible = false;
    let visibleColorPartsKey = '';
    let colorDraft = null;
    let colorPopover = null;

    function colorPartHighlight() {
        return (!capturingFrames && state.inspectorTab === 'colors' && colorHighlightVisible) ? hoveredColorPart : null;
    }

    function setColorPartHighlight(name = '') {
        if (name === hoveredColorPart) return;
        hoveredColorPart = name;
        colorHighlightStart = performance.now();
        cancelAnimationFrame(colorHighlightFrame);
        const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
        const tick = (now) => {
            const visible = Boolean(hoveredColorPart) && (reducedMotion || (now - colorHighlightStart) % 720 < 360);
            if (visible !== colorHighlightVisible || now === colorHighlightStart) {
                colorHighlightVisible = visible;
                if (!capturingFrames && !state.animationPlaying && hasSpineRenderer()) spineRenderer.render();
            }
            if (hoveredColorPart && !reducedMotion) colorHighlightFrame = requestAnimationFrame(tick);
        };
        tick(colorHighlightStart);
    }

    function recentColors() {
        try {
            const saved = JSON.parse(localStorage.getItem(RECENT_COLORS_KEY) || '[]');
            return Array.isArray(saved) ? saved.filter((color) => typeof color === 'string' && HEX_COLOR.test(color)).slice(0, 12) : [];
        } catch (_) {
            return [];
        }
    }

    function makePartColorPicker(current, draft, onPreview, onChoose) {
        const picker = document.createElement('div');
        picker.className = 'part-color-picker';

        const surface = document.createElement('div');
        surface.className = 'part-color-surface';
        surface.tabIndex = 0;
        surface.setAttribute('aria-label', t('emoteMaker.colorSurface'));
        const marker = document.createElement('span');
        surface.append(marker);
        const hue = document.createElement('input');
        hue.className = 'part-color-hue';
        hue.type = 'range';
        hue.max = '359';
        hue.setAttribute('aria-label', t('emoteMaker.colorHue'));
        const hex = document.createElement('input');
        hex.className = 'part-color-hex';
        hex.type = 'text';
        hex.maxLength = 7;
        hex.spellcheck = false;
        hex.setAttribute('aria-label', t('emoteMaker.colorHex'));
        const apply = document.createElement('button');
        apply.type = 'button';
        apply.textContent = t('common.confirm');
        const controls = document.createElement('div');
        controls.className = 'part-color-value';
        controls.append(hex, apply);
        const recents = document.createElement('div');
        recents.className = 'part-color-recents';
        const recentLabel = document.createElement('span');
        recentLabel.textContent = t('emoteMaker.colorRecent');
        const chips = document.createElement('div');
        recents.append(recentLabel, chips);

        let saturation = 0;
        let brightness = 1;
        function paint() {
            surface.style.setProperty('--part-hue', hue.value);
            marker.style.left = `${saturation * 100}%`;
            marker.style.top = `${(1 - brightness) * 100}%`;
            apply.disabled = !HEX_COLOR.test(hex.value);
        }
        function fromHex(color) {
            const { r, g, b } = hexToColor(color);
            const max = Math.max(r, g, b);
            const range = max - Math.min(r, g, b);
            const sector = max === r ? (g - b) / range : max === g ? 2 + (b - r) / range : 4 + (r - g) / range;
            hue.value = range ? String(Math.min(359, Math.round((sector + 6) % 6 * 60))) : '0';
            brightness = max;
            saturation = max ? range / max : 0;
            hex.value = color.toUpperCase();
            paint();
        }
        function fromSurface() {
            const angle = Number(hue.value) / 60;
            const chroma = brightness * saturation;
            const side = chroma * (1 - Math.abs(angle % 2 - 1));
            const [r, g, b] = [[chroma, side, 0], [side, chroma, 0], [0, chroma, side],
                [0, side, chroma], [side, 0, chroma], [chroma, 0, side]][Math.floor(angle)];
            const floor = brightness - chroma;
            hex.value = colorToHex({ r: r + floor, g: g + floor, b: b + floor }).toUpperCase();
            paint();
            onPreview(hex.value);
        }
        function updateRecents() {
            chips.replaceChildren(...recentColors().map((color) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.style.backgroundColor = color;
                chip.title = color.toUpperCase();
                chip.setAttribute('aria-label', chip.title);
                chip.addEventListener('click', () => { fromHex(color); commit(); });
                return chip;
            }));
            recents.hidden = !chips.childElementCount;
        }
        function commit() {
            if (!HEX_COLOR.test(hex.value)) return;
            const color = hex.value.toLowerCase();
            try {
                localStorage.setItem(RECENT_COLORS_KEY, JSON.stringify([color, ...recentColors().filter((item) => item !== color)].slice(0, 12)));
            } catch (_) { /* Color can still be applied when storage is unavailable. */ }
            updateRecents();
            onChoose(color);
        }
        const clamp = (value) => Math.max(0, Math.min(1, value));
        const selectPoint = (event) => {
            const box = surface.getBoundingClientRect();
            saturation = clamp((event.clientX - box.left) / box.width);
            brightness = clamp(1 - (event.clientY - box.top) / box.height);
            fromSurface();
        };
        surface.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            surface.setPointerCapture(event.pointerId);
            selectPoint(event);
        });
        surface.addEventListener('pointermove', (event) => {
            if (surface.hasPointerCapture(event.pointerId)) selectPoint(event);
        });
        surface.addEventListener('keydown', (event) => {
            const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowDown: [0, -1], ArrowUp: [0, 1] }[event.key];
            if (!step) return;
            event.preventDefault();
            saturation = clamp(saturation + step[0] * .02);
            brightness = clamp(brightness + step[1] * .02);
            fromSurface();
        });
        hue.addEventListener('input', fromSurface);
        hex.addEventListener('input', () => {
            if (HEX_COLOR.test(hex.value)) {
                fromHex(hex.value);
                onPreview(hex.value);
            } else paint();
        });
        hex.addEventListener('keydown', (event) => { if (event.key === 'Enter') commit(); });
        apply.addEventListener('click', commit);
        fromHex(draft || current);
        updateRecents();
        picker.append(surface, hue, controls, recents);
        return picker;
    }

    function commitPartColors(slots) {
        const before = structuredClone(state.templateSlots);
        state.templateSlots = slots;
        rememberHistory(before, structuredClone(slots), (next) => restoreState({ templateSlots: next }));
        forgetStageFrames();
        applyNativeCharacterState({ render: true });
        updateHistoryButtons();
        persistTemplateSession();
        renderChangedPartColors();
    }

    function slotColorLabel(field) {
        return t(field === 'tintBlackColor' ? 'emoteMaker.tintBlack' : 'emoteMaker.partColor');
    }

    function slotColorHex(slot, field) {
        return colorToHex(slot[field], field === 'tintBlackColor' ? '#000000' : '#ffffff').toUpperCase();
    }

    function makeSlotColorPicker(slot, field) {
        const dark = field === 'tintBlackColor';
        const fallback = dark ? { r: 0, g: 0, b: 0, a: 1 } : { r: 1, g: 1, b: 1, a: 1 };
        const isDraft = () => colorDraft?.name === slot.name && colorDraft.field === field;
        const picker = makePartColorPicker(slotColorHex(slot, field),
            isDraft() ? colorToHex(colorDraft.value) : null, (color) => {
                if (!picker.isConnected || (!color && !isDraft())) return;
                colorDraft = color ? { name: slot.name, field, value: hexToTemplateColor(color, fallback) } : null;
                if (colorPopover?.scope === 'controls') colorPopover.trigger.style.setProperty('--part-color-preview', color);
                applyNativeCharacterState({ render: true });
            }, (color) => {
                colorDraft = null;
                const next = getTemplateSlotStyles();
                const target = next.find((item) => item.name === slot.name);
                if (!target) return;
                if (target[field] && colorToHex(target[field]) === color) {
                    applyNativeCharacterState({ render: true });
                    positionColorPopover();
                    return;
                }
                target[field] = hexToTemplateColor(color, fallback);
                commitPartColors(next);
                renderTemplateSlotControls(true);
            });
        picker.dataset.colorField = field;
        return picker;
    }

    function positionColorPopover() {
        if (!colorPopover) return;
        const { editor, trigger, field, scope } = colorPopover;
        const anchor = trigger.getBoundingClientRect();
        const gap = 8;
        if (!field) editor.style.width = `${Math.min(anchor.width, window.innerWidth - 2 * gap)}px`;
        let left = anchor.left;
        let top = anchor.top;
        if (scope === 'changed') {
            const leftOfSwatch = anchor.left - editor.offsetWidth - gap;
            left = leftOfSwatch >= gap ? leftOfSwatch : anchor.right + gap;
        } else {
            editor.style.maxHeight = '';
            const above = anchor.top - 2 * gap;
            const below = window.innerHeight - anchor.bottom - 2 * gap;
            const naturalHeight = editor.offsetHeight;
            const openBelow = naturalHeight <= below || below >= above;
            const available = openBelow ? below : above;
            if (naturalHeight > available) editor.style.maxHeight = `${Math.max(0, available)}px`;
            top = openBelow ? anchor.bottom + gap : anchor.top - editor.offsetHeight - gap;
        }
        const width = editor.offsetWidth;
        const height = editor.offsetHeight;
        editor.style.left = `${Math.max(gap, Math.min(left, window.innerWidth - width - gap))}px`;
        editor.style.top = `${Math.max(gap, Math.min(top, window.innerHeight - height - gap))}px`;
    }

    function closeColorPopover(restoreFocus = false) {
        if (!colorPopover) return;
        const { editor, trigger, events, name, field } = colorPopover;
        colorPopover = null;
        events.abort();
        trigger.setAttribute('aria-expanded', 'false');
        trigger.style.removeProperty('--part-color-preview');
        editor.hidePopover();
        editor.remove();
        if (!field) setColorPartHighlight();
        if (field && colorDraft?.name === name && colorDraft.field === field) {
            colorDraft = null;
            applyNativeCharacterState({ render: true });
        }
        if (restoreFocus) trigger.focus({ preventScroll: true });
    }

    function toggleColorPopover(trigger, create) {
        if (colorPopover?.trigger === trigger) {
            closeColorPopover(true);
            return;
        }
        closeColorPopover();
        const { editor, ...settings } = create();
        editor.classList.add('color-popover');
        editor.popover = 'manual';
        editor.setAttribute('role', 'dialog');
        editor.setAttribute('aria-label', trigger.getAttribute('aria-label') || trigger.textContent);
        trigger.setAttribute('aria-expanded', 'true');
        trigger.closest('.inspector-tab-panel').append(editor);
        const events = new AbortController();
        const { signal } = events;
        colorPopover = { editor, trigger, events, ...settings, template: templatePackage };
        document.addEventListener('pointerdown', (event) => {
            if (!editor.contains(event.target) && !colorPopover?.trigger.contains(event.target)) closeColorPopover();
        }, { capture: true, signal });
        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            closeColorPopover(true);
        }, { signal });
        window.addEventListener('resize', positionColorPopover, { signal });
        window.addEventListener('scroll', positionColorPopover, { capture: true, signal });
        editor.showPopover();
        positionColorPopover();
        const focus = editor.querySelector('.part-color-hex, [aria-pressed="true"]');
        focus?.focus({ preventScroll: true });
        if (!settings.field && focus) editor.scrollTop = Math.max(0, focus.offsetTop - 8);
    }

    function anchorColorPopover(trigger) {
        colorPopover.trigger = trigger;
        trigger.setAttribute('aria-expanded', 'true');
        colorPopover.editor.setAttribute('aria-label', trigger.getAttribute('aria-label') || trigger.textContent);
        positionColorPopover();
    }

    function toggleSlotColorPopover(trigger, slot, field, scope) {
        toggleColorPopover(trigger, () => {
            const editor = makeSlotColorPicker(slot, field);
            if (scope === 'changed') editor.classList.add('changed-part-color-editor');
            return { editor, name: slot.name, field, scope };
        });
    }

    function updateColorBackToTop() {
        const offset = colorPanel.scrollTop;
        const range = colorPanel.scrollHeight - colorPanel.clientHeight;
        colorBackToTop.hidden = state.inspectorTab !== 'colors' || changedPartColors.hidden
            || offset <= 0 || offset < Math.min(80, range * 0.75);
    }

    function renderChangedPartColors() {
        if (!changedPartColors || !changedPartColorList) return;
        if (colorPopover && (colorPopover.template !== templatePackage || state.inspectorTab !== 'colors')) {
            closeColorPopover();
        }
        const defaults = new Map(state.templateSlotDefaults.map((slot) => [slot.name, slot]));
        const catalog = new Map((templatePackage?.catalogs?.slotGroups || []).map((item) => [item.id, item]));
        const rows = [];
        let activeTrigger = null;
        for (const slot of getTemplateSlotStyles()) {
            const original = defaults.get(slot.name);
            if (!original) continue;
            const fields = PART_TINT_FIELDS.filter((field) => {
                const current = slot[field];
                const baseline = original[field];
                return current && baseline
                    ? ['r', 'g', 'b', 'a'].some((channel) => current[channel] !== baseline[channel])
                    : Boolean(current) !== Boolean(baseline);
            });
            if (!fields.length) continue;
            const row = document.createElement('li');
            row.dataset.changedColorPart = slot.name;
            row.addEventListener('pointerenter', () => setColorPartHighlight(slot.name));
            row.addEventListener('pointerleave', () => setColorPartHighlight());
            const label = document.createElement('span');
            label.textContent = getCatalogLabel(catalog.get(slot.name)) || slot.name;
            row.append(label);
            for (const field of fields) {
                if (!slot[field]) continue;
                const swatch = document.createElement('button');
                swatch.type = 'button';
                swatch.className = 'part-color-swatch';
                swatch.dataset.colorField = field;
                const color = colorToHex(slot[field]).toUpperCase();
                swatch.style.backgroundColor = color;
                const fieldLabel = slotColorLabel(field);
                swatch.title = `${fieldLabel} ${color}`;
                swatch.setAttribute('aria-label', `${label.textContent} · ${swatch.title}`);
                const open = colorPopover?.scope === 'changed' && colorPopover.name === slot.name && colorPopover.field === field;
                swatch.setAttribute('aria-expanded', String(open));
                swatch.setAttribute('aria-haspopup', 'dialog');
                swatch.addEventListener('click', () => toggleSlotColorPopover(swatch, slot, field, 'changed'));
                if (open) activeTrigger = swatch;
                row.append(swatch);
            }
            const reset = document.createElement('button');
            reset.type = 'button';
            reset.className = 'text-button';
            reset.textContent = t('emoteMaker.reset');
            reset.setAttribute('aria-label', `${reset.textContent} · ${label.textContent}`);
            reset.addEventListener('click', () => {
                const index = Array.from(changedPartColorList.children).indexOf(row);
                const next = getTemplateSlotStyles();
                const target = next.find((item) => item.name === slot.name);
                if (!target) return;
                for (const field of PART_TINT_FIELDS) target[field] = original[field] ? { ...original[field] } : null;
                if (colorDraft?.name === slot.name) colorDraft = null;
                commitPartColors(next);
                renderTemplateSlotControls(true);
                const nextRow = changedPartColorList.children[Math.min(index, changedPartColorList.children.length - 1)];
                (nextRow?.querySelector('.text-button') || templateSlotControls?.querySelector('.part-color-menu'))?.focus({ preventScroll: true });
            });
            row.append(reset);
            rows.push(row);
        }
        changedPartColorList.replaceChildren(...rows);
        changedPartColors.hidden = !rows.length;
        updateColorBackToTop();
        if (activeTrigger) anchorColorPopover(activeTrigger);
        else if (colorPopover?.scope === 'changed') closeColorPopover();
    }

    function renderPartColorOptions(partList, slots, selectedName, labelFor) {
        const focused = partList.contains(document.activeElement) ? document.activeElement.dataset.colorPart : '';
        const scrollTop = partList.scrollTop;
        const options = slots.map((item) => {
            const option = document.createElement('button');
            option.type = 'button';
            option.dataset.colorPart = item.name;
            option.textContent = labelFor(item);
            option.setAttribute('aria-pressed', String(item.name === selectedName));
            option.addEventListener('pointerenter', () => setColorPartHighlight(item.name));
            option.addEventListener('pointerleave', () => setColorPartHighlight());
            option.addEventListener('focus', () => setColorPartHighlight(item.name));
            option.addEventListener('blur', () => setColorPartHighlight());
            option.addEventListener('click', () => {
                state.templateSlotGroup = item.name;
                renderTemplateSlotControls();
                templateSlotControls.querySelector('.part-color-menu')?.focus({ preventScroll: true });
            });
            return option;
        });
        partList.replaceChildren(...options);
        partList.scrollTop = scrollTop;
        if (focused) (options.find((option) => option.dataset.colorPart === focused)
            || options.find((option) => option.getAttribute('aria-pressed') === 'true'))?.focus({ preventScroll: true });
    }

    function makePartColorTrigger(label) {
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'part-color-trigger';
        trigger.textContent = label;
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-expanded', 'false');
        return trigger;
    }

    function renderTemplateSlotControls(preserveOpen = false) {
        if (!templateSlotControls) return;
        if (!preserveOpen && colorPopover?.scope === 'controls') closeColorPopover();
        const active = colorPopover?.scope === 'controls' ? colorPopover : null;
        if (!preserveOpen || !changedPartColorList?.querySelector('li:hover')) setColorPartHighlight();
        if (!templatePackage) {
            if (active) closeColorPopover();
            templateSlotControls.hidden = true;
            templateSlotControls.replaceChildren();
            return;
        }
        const visible = new Set(hasSpineRenderer() ? spineRenderer.visiblePartNames() : []);
        visibleColorPartsKey = [...visible].join('\n');
        if (colorDraft && colorPopover?.scope !== 'changed' && (!preserveOpen || !visible.has(colorDraft.name))) {
            colorDraft = null;
            applyNativeCharacterState({ render: true });
        }
        const slots = getTemplateSlotStyles().filter((item) => visible.has(item.name));
        templateSlotControls.hidden = false;
        if (!slots.length) {
            if (active) closeColorPopover();
            const note = document.createElement('p');
            note.className = 'template-compat-note';
            note.textContent = t(hasSpineRenderer() ? 'emoteMaker.colorNoVisibleParts' : 'emoteMaker.templateMetadataOnly');
            templateSlotControls.replaceChildren(note);
            return;
        }

        let selectedName = String(state.templateSlotGroup || '');
        if (!slots.some((item) => String(item.name) === selectedName)) {
            selectedName = String(slots[0].name);
        }
        const selected = slots.find((item) => String(item.name) === selectedName) || slots[0];
        const catalogEntries = Array.isArray(templatePackage.catalogs?.slotGroups)
            ? templatePackage.catalogs.slotGroups
            : [];
        const catalogMap = new Map(catalogEntries.map((item) => [String(item?.id || ''), item]));
        const labelFor = (item) => getCatalogLabel(catalogMap.get(String(item?.name || ''))) || String(item?.name || '');

        const partMenu = makePartColorTrigger(labelFor(selected));
        partMenu.classList.add('part-color-menu');
        partMenu.setAttribute('aria-label', `${t('emoteMaker.slotGroup')} · ${labelFor(selected)}`);
        partMenu.addEventListener('pointerenter', () => setColorPartHighlight(selectedName));
        partMenu.addEventListener('pointerleave', () => setColorPartHighlight());
        partMenu.addEventListener('click', () => toggleColorPopover(partMenu, () => {
            const editor = document.createElement('div');
            editor.className = 'part-color-options';
            renderPartColorOptions(editor, slots, selectedName, labelFor);
            return { editor, scope: 'controls' };
        }));
        const children = [partMenu];
        let activeTrigger = active && !active.field ? partMenu : null;
        const makeColorRow = (field) => {
            const color = slotColorHex(selected, field);
            const trigger = makePartColorTrigger(slotColorLabel(field));
            trigger.dataset.colorField = field;
            const swatch = document.createElement('span');
            swatch.className = 'part-color-swatch';
            swatch.style.backgroundColor = `var(--part-color-preview, ${color})`;
            const value = document.createElement('code');
            value.textContent = color;
            trigger.append(swatch, value);
            trigger.addEventListener('click', () => toggleSlotColorPopover(trigger, selected, field, 'controls'));
            if (active?.name === selectedName && active.field === field) {
                if (colorDraft) trigger.style.setProperty('--part-color-preview', colorToHex(colorDraft.value));
                activeTrigger = trigger;
            }
            return trigger;
        };
        children.push(makeColorRow('tintColor'));
        if (spineRenderer?.partHasDarkTint(selected.name)) {
            children.push(makeColorRow('tintBlackColor'));
        }
        templateSlotControls.replaceChildren(...children);
        if (activeTrigger) {
            if (!active.field) renderPartColorOptions(active.editor, slots, selectedName, labelFor);
            anchorColorPopover(activeTrigger);
        } else if (active) {
            closeColorPopover();
        }
    }





    function updateHistoryButtons() {
        const undoButton = $('#undo-btn');
        const redoButton = $('#redo-btn');
        const blocked = historyBusy || wardrobeFileBusy || wardrobeGestureActive;
        if (undoButton) undoButton.disabled = blocked || history.length === 0;
        if (redoButton) redoButton.disabled = blocked || future.length === 0;
    }












    function syncOverlayCanvasSize(target, source = spineCanvas) {
        if (!target || !source) return null;
        const width = Math.max(1, Number(source.width) || CANVAS_SIZE);
        const height = Math.max(1, Number(source.height) || CANVAS_SIZE);
        if (target.width !== width) target.width = width;
        if (target.height !== height) target.height = height;
        return { width, height };
    }

    function clearOverlayCanvas(target, source = spineCanvas) {
        const size = syncOverlayCanvasSize(target, source);
        if (!size) return null;
        const context = target.getContext('2d');
        context.clearRect(0, 0, size.width, size.height);
        return { context, ...size };
    }

    function drawWardrobeArtwork(context, canvasWidth, canvasHeight, sprites) {
        context.save();
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = 'high';
        for (const sprite of sprites) {
            if (!wardrobeImageReady(sprite.image) || (sprite.anchor && spineRenderer.skeleton.visible === false)) continue;
            const { width, height } = wardrobeSpriteGeometry(sprite);
            const [a, b, c, d, x, y] = wardrobeSpritePose(sprite);
            context.setTransform(a * canvasWidth, b * canvasHeight, c * canvasWidth, d * canvasHeight,
                x * canvasWidth, y * canvasHeight);
            context.drawImage(sprite.image, -width / 2, -height / 2, width, height);
        }
        context.restore();
    }

    function wardrobeSpineLayers() {
        if (wardrobePlacements.hidden || !hasSpineRenderer()) return [];
        const slots = new Set(spineRenderer.drawOrderNames());
        return [...wardrobeSprites.values()]
            .flatMap((sprite) => {
                const aboveSlot = wardrobeSpriteLayerSlot(sprite);
                if (!slots.has(aboveSlot) || !wardrobeImageReady(sprite.image)) return [];
                const { width, height } = wardrobeSpriteGeometry(sprite);
                const [a, b, c, d, x, y] = wardrobeSpritePose(sprite);
                const corners = [[-width, -height], [-width, height], [width, height], [width, -height]]
                    .map(([u, v]) => [x + (a * u + c * v) / 2, y + (b * u + d * v) / 2]);
                return [{ image: sprite.image, aboveSlot, corners }];
            });
    }

    function wardrobeOverlaySprites(behind) {
        const slots = new Set(spineRenderer.drawOrderNames());
        return [...wardrobeSprites.values()].filter((sprite) => !slots.has(wardrobeSpriteLayerSlot(sprite)) && Boolean(sprite.behind) === behind);
    }

    function renderWardrobeArtwork({ refreshSpine = true } = {}) {
        if (!wardrobeArtwork || capturingFrames) return;
        const visible = !wardrobePlacements.hidden && hasSpineRenderer();
        if (refreshSpine && visible) {
            spineRenderer.render();
        }
        for (const [target, behind] of [[wardrobeArtworkBehind, true], [wardrobeArtwork, false]]) {
            const layerSprites = visible ? wardrobeOverlaySprites(behind) : [];
            target.hidden = !layerSprites.length;
            if (target.hidden) continue;
            const layer = clearOverlayCanvas(target);
            if (layer) drawWardrobeArtwork(layer.context, layer.width, layer.height, layerSprites);
        }
    }

    function renderNativeOverlays() {
        if (!hasSpineRenderer()) {
            renderWardrobeArtwork({ refreshSpine: false });
            return;
        }
        if (!capturingFrames && wardrobePlacements && !wardrobePlacements.hidden) {
            for (const sprite of wardrobeSprites.values()) {
                if (sprite.anchor) updateWardrobeSprite(sprite, false);
            }
            renderWardrobeArtwork({ refreshSpine: false });
        }
        refreshVisibleColorParts();
    }

    function refreshVisibleColorParts() {
        if (!capturingFrames && state.inspectorTab === 'colors' && hasSpineRenderer()
                && spineRenderer.visiblePartNames().join('\n') !== visibleColorPartsKey) renderTemplateSlotControls(true);
    }

    function nativeCompositeCanvas() {
        if (!hasSpineRenderer() || !spineCanvas) return null;
        const output = document.createElement('canvas');
        output.width = spineCanvas.width || CANVAS_SIZE;
        output.height = spineCanvas.height || CANVAS_SIZE;
        const context = output.getContext('2d');
        if (state.exportTransparent === false) {
            context.fillStyle = '#f5f9fc';
            context.fillRect(0, 0, output.width, output.height);
        }
        const visible = !wardrobePlacements.hidden;
        if (visible) drawWardrobeArtwork(context, output.width, output.height, wardrobeOverlaySprites(true));
        if (!spineCanvas.hidden) context.drawImage(spineCanvas, 0, 0, output.width, output.height);
        if (visible) drawWardrobeArtwork(context, output.width, output.height, wardrobeOverlaySprites(false));
        return output;
    }

    async function renderCanvas() {
        if (!canvas) return;
        if (templatePackage && hasSpineRenderer()) {
            setCanvasMode(true);
            try {
                applyNativeCharacterState({ render: false });
                applyNativeVisualState();
                spineRenderer.render();
                renderNativeOverlays();
            } catch (_) {
                // The metadata fallback below remains available if the WebGL
                // context is lost while the editor is open.
                renderNativeOverlays();
            }
            return;
        }
        setCanvasMode(false);
        const context = canvas.getContext('2d');
        context.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = 'high';

        if (templatePackage) {
            const scene = getImportedScenes().find((item) => item.id === state.selectedSceneId);
            context.save();
            context.fillStyle = 'rgba(36, 170, 244, 0.06)';
            context.fillRect(86, 86, CANVAS_SIZE - 172, CANVAS_SIZE - 172);
            context.strokeStyle = 'rgba(36, 170, 244, 0.42)';
            context.setLineDash([14, 12]);
            context.lineWidth = 4;
            context.strokeRect(86, 86, CANVAS_SIZE - 172, CANVAS_SIZE - 172);
            context.setLineDash([]);
            context.fillStyle = '#1b6fa8';
            context.textAlign = 'center';
            context.textBaseline = 'middle';
            context.font = '700 34px "Microsoft YaHei", sans-serif';
            context.fillText(currentTemplateLabel() || t('emoteMaker.title'), CANVAS_SIZE / 2, 402);
            context.font = '600 24px "Microsoft YaHei", sans-serif';
            context.fillStyle = '#6d8eae';
            context.fillText(scene?.name || t('emoteMaker.templateSceneList'), CANVAS_SIZE / 2, 470);
            context.font = '500 20px "Microsoft YaHei", sans-serif';
            context.fillText(t('emoteMaker.templateNoRuntime'), CANVAS_SIZE / 2, 548);
            context.restore();
        }
    }

    function applyZoom() {
        const { zoom, previewPan } = state;
        const { clientWidth, clientHeight } = canvasStage;
        const { offsetLeft, offsetTop, offsetWidth, offsetHeight } = canvasFrame;
        const clampPan = (pan, viewport, offset, size) => {
            if (zoom <= 1 || size * zoom <= viewport) return 0;
            const center = offset + size / 2;
            const half = size * zoom / 2;
            return Math.max(Math.min(0, viewport - center - half), Math.min(Math.max(0, half - center), pan));
        };
        previewPan.x = clampPan(previewPan.x, clientWidth, offsetLeft, offsetWidth);
        previewPan.y = clampPan(previewPan.y, clientHeight, offsetTop, offsetHeight);
        canvasStage.classList.toggle('is-zoomed', zoom > 1);
        canvasFrame.style.transform = `translate(${previewPan.x}px, ${previewPan.y}px) scale(${zoom})`;
        zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    }

    function setPreviewZoom(value, point) {
        const zoom = Math.max(PREVIEW_ZOOM_MIN, Math.min(PREVIEW_ZOOM_MAX, value));
        if (point) {
            const bounds = canvasFrame.getBoundingClientRect();
            const shift = 1 - zoom / state.zoom;
            state.previewPan.x += (point.clientX - (bounds.left + bounds.width / 2)) * shift;
            state.previewPan.y += (point.clientY - (bounds.top + bounds.height / 2)) * shift;
        }
        state.zoom = zoom;
        applyZoom();
    }

    // ── 长按拖到形象包 ────────────────────────────────────────────────────────
    // 用指针事件而不是 HTML5 dnd：原生 draggable 在 mousedown+移动就启动，跟列表的
    // 点击抢同一个手势，没法表达"按住一会儿才算拖"。
    let suppressClickUntil = 0;

    function resolveSceneForPayload(payload) {
        const packModule = window.NekoEmotePngtuberPack;
        if (!packModule || typeof packModule.resolveSceneForDrag !== 'function') return null;
        return packModule.resolveSceneForDrag(getImportedScenes(), payload);
    }

    // 落点，从具体到笼统：表演台的某一格 → 基础模式某一行 → 形象包区块（只负责把
    // 表演台打开，然后提示"拖到格上"）。整个形象包区块都能接住，是为了避免
    // "拖了半天什么都没发生"这类最难自查的失败。
    function packDropTargetAt(x, y) {
        if (stateStageSession && stateStageSession.containsPoint(x, y)) {
            const stateKey = stateStageSession.stateAtPoint(x, y);
            return stateKey ? { kind: 'tile', element: stateStageSession.element, stateKey } : null;
        }
        const under = document.elementFromPoint(x, y);
        if (!under || !under.closest) return null;
        // 只承认"这一格会被设成什么"。整个区块都能接住看似周到，实际是让用户
        // 在八个地方松手、然后有三个什么都不做。
        const row = under.closest('.pack-basic-row');
        return row && row.dataset.stateKey ? { kind: 'row', element: row } : null;
    }

    let packDropRevealed = false;

    /**
     * 拖着拖着进了右侧面板：先把「导出设置 → 基础」翻出来、滚到形象包那块、
     * 让八行轻轻呼吸一下 —— 否则用户手里拿着东西，却不知道该往哪儿放。
     * 一次拖拽只reveal一次，不打断已经在对准的手。
     */
    function revealPackPanelForDrop() {
        if (packDropRevealed) return;
        const panel = $('#inspector-panel-export');
        if (!panel) return;
        const insideInspector = panel.closest('.inspector-panel') || panel;
        if (!insideInspector.getBoundingClientRect().width) return;
        packDropRevealed = true;
        if (state.inspectorTab !== 'export') setInspectorTab('export');
        if (state.packMode !== 'basic') {
            state.packMode = 'basic';
            persistLayout();
        }
        renderPackPanel();
        const rows = $('#pack-basic-rows');
        if (rows) {
            rows.classList.add('is-drop-hint');
            scrollGroupIntoView(rows);
        }
    }

    /**
     * 目标是"八行全都看得见"，不是把容器底边对齐到面板底 ——
     * 上一版用 scrollIntoView({block:'end'}) 就是滚到了最顶上，只露出后几行。
     */
    /**
     * 目标很具体：八行全都看得见（不是把容器底边对齐，也不是滚到顶）。
     * 哪个元素真能滚不靠 CSS 猜 —— 直接试着滚一下、读回来，能动才算找到。
     */
    function scrollGroupIntoView(el) {
        const pad = 10;
        const candidates = [];
        let node = el.parentElement;
        while (node && node !== document.body) {
            candidates.push(node);
            node = node.parentElement;
        }
        candidates.push(document.scrollingElement || document.documentElement);

        for (let i = 0; i < candidates.length; i += 1) {
            const scroller = candidates[i];
            const before = scroller.scrollTop;
            const frame = scroller.getBoundingClientRect();
            const box = el.getBoundingClientRect();
            let delta = 0;
            if (box.height + pad * 2 >= frame.height) {
                delta = box.top - frame.top - pad;      // 装不下：至少从顶部对齐
            } else {
                if (box.bottom > frame.bottom - pad) delta = box.bottom - (frame.bottom - pad);
                else if (box.top < frame.top + pad) delta = box.top - (frame.top + pad);
            }
            if (!delta) return;
            scroller.scrollTop = before + delta;
            // 上一行没生效（这个元素根本不滚）就换下一个候选
            if (Math.abs(scroller.scrollTop - before) > 0.5) return;
            scroller.scrollTop = before;
        }
    }

    function hidePackDropHint() {
        packDropRevealed = false;
        const rows = $('#pack-basic-rows');
        if (rows) rows.classList.remove('is-drop-hint');
    }



    function setDropHighlight(target, on) {
        if (!target) return;
        if (target.kind === 'tile') stateStageSession.setDropHighlight(on ? target.stateKey : null);
        else target.element.classList.toggle('is-drop-target', on);
    }

    function beginPackDrag(payload, event, sourceElement) {
        const holdModule = window.NekoEmoteDragHold;
        // 拖出来的东西长得像它来自的那张选项卡（缩略图 + 名字）。
        // 一行文字的 ghost 会让人以为自己在拖空气。
        const ghost = holdModule
            ? holdModule.buildGhost(payload, sourceElement)
            : Object.assign(document.createElement('div'), {
                className: 'pack-drag-ghost',
            });
        if (!holdModule) {
            const label = document.createElement('span');
            label.className = 'pack-drag-label';
            label.textContent = String(payload.label || '');
            ghost.appendChild(label);
        }
        document.body.appendChild(ghost);
        document.body.classList.add('is-pack-dragging');
        // 长按一起拖，这一轮就到此为止：原地松手不能被当成"点击选中那个场景"。
        // 以前只在松手时才记，指针一动不动地按住再松开仍会漏。
        suppressClickUntil = Date.now() + 600;
        let lastTarget = null;
        // 手里的东西跟着横向速度甩一下、停下回正：不甩就像钉在光标上的贴图
        const swing = holdModule ? holdModule.createGhostSwing(ghost) : null;
        const place = swing
            ? (x, y) => swing.moveTo(x, y)
            : (x, y) => { ghost.style.left = `${x}px`; ghost.style.top = `${y}px`; };
        if (swing) { place(event.clientX, event.clientY); swing.start(); }
        const highlight = (target) => {
            if (lastTarget === target) return;
            setDropHighlight(lastTarget, false);
            setDropHighlight(target, true);
            lastTarget = target;
        };
        const move = (moveEvent) => {
            place(moveEvent.clientX, moveEvent.clientY);
            const target = packDropTargetAt(moveEvent.clientX, moveEvent.clientY);
            if (!target) {
                // 手里有东西时指针样式不能被下面的按钮改来改去（CSS 里锁住），
                // 同时把该放的地方翻出来给他看。
                const under = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY);
                if (under && under.closest && under.closest('.inspector-panel')) {
                    revealPackPanelForDrop();
                }
            }
            highlight(target);
        };
        const stop = (upEvent) => {
            window.removeEventListener('pointermove', move, true);
            window.removeEventListener('pointerup', stop, true);
            window.removeEventListener('pointercancel', cancel, true);
            swing?.stop();
            ghost.remove();
            document.body.classList.remove('is-pack-dragging');
            hidePackDropHint();
            setDropHighlight(lastTarget, false);
            // 必须在这里就设，而不是等到确认"落到某一格"之后：
            // 悬停久了再松手时，起拖时设的那 600ms 已经过期，而下面任何一次早退
            // 都会让这一轮"长按过"的事实被漏掉 —— 于是被当成一次普通点击。
            suppressClickUntil = Date.now() + 600;
            if (upEvent.type === 'pointercancel') return;
            const target = packDropTargetAt(upEvent.clientX, upEvent.clientY);
            if (!target) return;
            if (payload.kind === 'image') {
                suppressClickUntil = Date.now() + 500;
                void stateStageSession.assignImage(target.stateKey, payload.imageId).catch(() => {
                    console.error('[emote-maker] Image assignment failed');
                });
                return;
            }
            const scene = resolveSceneForPayload(payload);
            if (!scene) {
                showToast(t('emoteMaker.packDropNoScene'));
                return;
            }
            // 长按拖出来的这一次不应该再被当成一次点击去选中它。
            suppressClickUntil = Date.now() + 500;
            if (target.kind === 'tile') {
                stateStageSession.assignScene(target.stateKey, scene.id);
                return;
            }
            if (target.kind === 'row') {
                state.packSceneIds[target.element.dataset.stateKey] = scene.id;
                persistExportSettings();
                renderPackPanel();
                showToast(t('emoteMaker.stageDropApplied').replace('{name}', scene.name || scene.id));
                return;
            }
            // 落在别处＝没对着任何一格：什么都不改
        };

        const cancel = () => stop({ type: 'pointercancel', clientX: 0, clientY: 0 });

        place(event.clientX, event.clientY);
        window.addEventListener('pointermove', move, true);
        window.addEventListener('pointerup', stop, true);
        window.addEventListener('pointercancel', cancel, true);
    }

    // 长按计时和光标圆环都在 NekoEmoteDragHold 里（表演台的行也要同一套手感，
    // 两边各写一份 320ms/8px 迟早会漂）。这里只负责"起拖之后做什么"。
    function armDragSource(card, payload, event) {
        const holdModule = window.NekoEmoteDragHold;
        if (!holdModule) {
            beginPackDrag(payload, { clientX: event.clientX, clientY: event.clientY }, card);
            return;
        }
        holdModule.armHold(event, {
            onComplete: (point) => beginPackDrag(payload, point, card),
        });
    }

    function attachDragSource(card, payload) {
        card.addEventListener('pointerdown', (event) => {
            if (card.disabled) return;
            armDragSource(card, payload, event);
        });
    }

    // PNGTuber 平铺模式真实存在的触发边，全部来自运行时源码；这些数字是写死的常量，
    // 所以这里只**标注**、不开放编辑 —— 给一个改了没用的旋钮比不给更糟。

    const NODE_GRAPH_KEY = 'neko-emote-maker-node-graph';

    function loadNodeGraph() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(NODE_GRAPH_KEY) || 'null');
        } catch (_) {
            saved = null;
        }
        const model = window.NekoEmoteNodeGraph;
        if (!model || !saved) return;
        state.nodeGraph = model.normalizeGraph(saved);
        // 旧图里可能有重复槽位：界面上报"跟随默认"、烘包却用了另一条动作。
        // 归一化时已经收成一条，这里要落盘并说一声，不能悄悄改人存好的设置。
        const collapsed = (state.nodeGraph.dropped || []).length;
        if (collapsed) {
            persistNodeGraph();
            showToast(`${t('emoteMaker.packPlanDuplicate')} (${collapsed}) · ${t('emoteMaker.graphCollapsedNotice')}`);
        }
    }

    function persistNodeGraph() {
        try {
            localStorage.setItem(NODE_GRAPH_KEY, JSON.stringify(state.nodeGraph));
        } catch (_) {
            // 存不下不影响导出走面板回落。
        }
    }


    function currentLookDefaults() {
        const adjustments = state.adjustOverwrite ? state.adjustments : null;
        return {
            sceneId: state.selectedSceneId || '',
            skins: (state.templateSkins || []).map((item) => ({ ...item })),
            slots: (state.templateSlots || []).map((item) => ({ ...item })),
            transform: {
                x: Number(adjustments?.x) || 0,
                y: Number(adjustments?.y) || 0,
                // 旋转角度也要带走：取景要能还原"我调成什么样"，少一项就少一项
                rotation: Number(adjustments?.rotation) || 0,
                scale: (Number(adjustments?.scale) || 100) / 100,
                mirror: false,
            },
        };
    }

    // ── 状态表演台 ────────────────────────────────────────────────────────────
    // 预览采集比导出便宜得多：低 fps、小边长、帧数封顶。秒数是唯一的真值，
    // 帧号由 delayMs 反算，所以预览的采样率和导出不一致也不会骗人。
    // 舞台上的清晰度由这一档决定：176 铺到 ~500px 的台面上必然糊。
    const STAGE_QUALITY = {
        // 悬停一眼：宁可先动起来也不等，帧数少、边长小
        poster: { fps: 8, edge: 232, maxFrames: 14 },
        // 真正选中那一格：要能看清细节，也要够铺满时间轴
        full: { fps: 12, edge: 420, maxFrames: 48 },
        // 选择器里的缩略图：只要一张小图。之前它走的是 full 档，
        // 展开一次等于把所有动画整条重采一遍，卡就卡在这里。
        thumb: { fps: 1, edge: 96, maxFrames: 1 },
    };
    const STAGE_ROOT_PREFIX = 'root:';
    const STAGE_CAPTURE_INTERACTIVE = 10;
    const STAGE_CAPTURE_THUMB = 1;
    // 表演台预览帧率：时间轴刻度和"一帧多长"都靠它，太高浪费、太低不流畅
    const STAGE_PREVIEW_FPS = 10;
    const stageFramesBySource = new Map();   // `${源}|${档位}` -> frame[]
    const stageThumbs = new Map();           // sceneId -> dataURL
    // 用户图片：id -> { name, dataUrl, extension }。字节随副本走（StageImages/ 合并进贴图管线），
    // 内存里只留 dataURL 供预览与帧绘制；导出/清库跟模板副本同生共死。
    const stageUserImages = new Map();
    let stageImageTemplateKey = '';
    let stageImagesReady = null;
    // 原始文件名随 localStorage 走：副本用内部 id 保存图片
    // （CJK 文件名进 id 会被洗成横杠），名字必须单独记。单键 { id: name }。
    const STAGE_IMAGE_NAMES_KEY = 'neko-emote-maker-stage-image-names';

    function loadStageImageNames() {
        try {
            const saved = JSON.parse(localStorage.getItem(STAGE_IMAGE_NAMES_KEY) || '{}');
            return saved && typeof saved === 'object' ? saved : {};
        } catch (_) {
            return {};
        }
    }

    function rememberStageImageName(id, name) {
        const names = loadStageImageNames();
        names[id] = String(name || '').trim() || id;
        try {
            localStorage.setItem(STAGE_IMAGE_NAMES_KEY, JSON.stringify(names));
        } catch (_) { /* 存不下就退回 id 当名字 */ }
    }

    function forgetStageImageName(id) {
        const names = loadStageImageNames();
        if (!(id in names)) return;
        delete names[id];
        try {
            localStorage.setItem(STAGE_IMAGE_NAMES_KEY, JSON.stringify(names));
        } catch (_) { /* 同上 */ }
    }
    const stagePending = new Map();          // 队列键 -> Promise（同一份不重复排）
    let stageFramesRevision = 0;
    /**
     * 所有采集排队串行跑。bakePackJobs 会暂停渲染器、改 state.selectedSceneId、
     * 结束时还原——并发调用不但抢主线程，还会互相踩场景（缩略图全变成同一条动画）。
     * 交互请求（选中/悬停）排在缩略图前面，缩略图可以在选择器收起时被丢掉。
     */
    const stageQueue = [];
    let stageQueueRunning = false;
    let stagePersistTimer = 0;
    let exportEditingLock = null;

    // A capture owns the live renderer while yielding between readbacks. Keep
    // editing gestures and shortcuts from changing its animation or appearance.
    // Window controls, batch stop and generated file downloads remain available.
    function lockExportEditing() {
        if (exportEditingLock) {
            exportEditingLock.count += 1;
            exportEditingLock.disableControls();
            return releaseExportEditing(exportEditingLock);
        }
        const main = document.querySelector('.emote-maker-page');
        const controls = new Map();
        const allowed = (target) => target instanceof Element && Boolean(target.closest(
            '[data-neko-window-control], #emote-maker-close, a[download][href^="blob:"]'
            + (groupExportBusy ? ', #export-group-open-btn' : ''),
        ));
        const disableControls = () => {
            document.querySelectorAll('button, input, select, textarea').forEach((control) => {
                if (allowed(control) || control.disabled) return;
                if (!controls.has(control)) controls.set(control, control.disabled);
                control.disabled = true;
            });
        };
        disableControls();
        const wasBusy = main?.getAttribute('aria-busy');
        main?.setAttribute('aria-busy', 'true');
        const block = (event) => {
            if (allowed(event.target)) return;
            event.preventDefault();
            event.stopImmediatePropagation();
        };
        const events = ['pointerdown', 'pointermove', 'pointerup', 'click', 'dblclick',
            'contextmenu', 'wheel', 'keydown', 'input', 'change', 'drop'];
        events.forEach((type) => window.addEventListener(type, block, { capture: true, passive: false }));
        exportEditingLock = {
            count: 1,
            disableControls,
            restore() {
                events.forEach((type) => window.removeEventListener(type, block, true));
                controls.forEach((disabled, control) => { control.disabled = disabled; });
                if (wasBusy == null) main?.removeAttribute('aria-busy');
                else main?.setAttribute('aria-busy', wasBusy);
            },
        };
        return releaseExportEditing(exportEditingLock);
    }

    function releaseExportEditing(lock) {
        let released = false;
        return () => {
            if (released) return;
            released = true;
            if (--lock.count) return;
            lock.restore();
            exportEditingLock = null;
        };
    }

    function queueStageCapture(key, priority, kind, task) {
        if (stagePending.has(key)) {
            // 已经在队里的任务可以提优先级：抽屉一开会排进几百条缩略图，
            // 右侧状态行那八条要是排在长队末尾，就一直是空白。
            const queued = stageQueue.find((entry) => entry.key === key);
            if (queued && priority > queued.priority) {
                queued.priority = priority;
                if (queued.kind === 'thumb') queued.kind = kind;
            }
            return stagePending.get(key);
        }
        let settle = null;
        const promise = new Promise((resolve, reject) => { settle = { resolve, reject }; });
        stageQueue.push({ key, priority, kind, task, resolve: settle.resolve, reject: settle.reject });
        stagePending.set(key, promise);
        // 队列自己消化异常（失败就不缓存，下次重试），这里只负责让 promise 被处理过
        promise.catch(() => []).then(() => {
            if (stagePending.get(key) === promise) stagePending.delete(key);
        });
        drainStageQueue();
        return promise;
    }

    async function drainStageQueue() {
        if (stageQueueRunning) return;
        stageQueueRunning = true;
        try {
            while (stageQueue.length) {
                stageQueue.sort((a, b) => b.priority - a.priority);
                const job = stageQueue.shift();
                const unlock = job.kind === 'export' ? lockExportEditing() : () => {};
                try {
                    job.resolve(await job.task());
                } catch (error) {
                    job.reject(error);
                } finally {
                    unlock();
                }
            }
        } finally {
            stageQueueRunning = false;
        }
    }

    /** 选择器收起：还没跑到的缩略图直接丢掉，别继续占着渲染器。*/
    function dropQueuedThumbs() {
        for (let i = stageQueue.length - 1; i >= 0; i -= 1) {
            if (stageQueue[i].kind === 'thumb') {
                const dropped = stageQueue.splice(i, 1)[0];
                stagePending.delete(dropped.key);
                dropped.resolve('');
            }
        }
    }

    function forgetStageFrames() {
        stageFramesRevision += 1;
        stageFramesBySource.clear();
        stageThumbs.clear();
        stagePending.clear();
    }

    // 动画列表一旦变了（换包、重导入、改名），按 sceneId 存的缓存就是别的包的画面。
    // 用签名自查比在每个改动点插一句可靠——漏一处就是"预览看着对、烘出来是空的"。
    let stageSceneSignature = '';
    function syncStageSceneCache() {
        const signature = getImportedScenes().map((scene) => `${scene.id}|${scene.name || ''}`).join(',');
        if (signature !== stageSceneSignature) {
            stageSceneSignature = signature;
            forgetStageFrames();
        }
    }

    function stageGraph() {
        return (stateStageSession && stateStageSession.getGraph())
            || state.nodeGraph
            || basicGraph();
    }

    /** 采一帧源的某一档。同一 sceneId 的不同档位分开缓存，海报不会污染清晰视图。*/
    // ── 用户图片：导入、持久化、恢复 ─────────────────────────────────────
    // 图片走本地副本的贴图管线（StageImages/ 合并进副本），随模板同生共死；
    // 不进场景清单，帧源用 image:<id> 前缀与场景区分。
    const STAGE_IMAGE_PREFIX = 'StageImages/';

    function stageImageId(name) {
        return `img-${String(name || 'image').replace(/[^\w.-]+/g, '-').toLowerCase()}-${Date.now().toString(36)}`;
    }

    function dataUrlToBlob(dataUrl) {
        const [head, body] = String(dataUrl).split(',');
        const mime = /:(.*?);/.exec(head)[1] || 'image/png';
        const binary = atob(body);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        return new Blob([bytes], { type: mime });
    }

    function syncStageImageTemplate() {
        const key = currentTemplateKey();
        if (key !== stageImageTemplateKey) {
            stageImageTemplateKey = key;
            stageImagesReady = null;
            stageUserImages.clear();
            forgetStageFrames();
            pushStageUserImages();
        }
        return key;
    }

    async function persistStageImages(entries, key) {
        if (!key || !entries.length) return;
        await writeTemplateTextures(key, entries.map(([id, image]) => [
            `${STAGE_IMAGE_PREFIX}${id}.${image.extension}`, dataUrlToBlob(image.dataUrl),
        ]));
    }

    async function removeStageImage(id) {
        const key = currentTemplateKey();
        const image = key === stageImageTemplateKey ? stageUserImages.get(id) : null;
        if (!image) return;
        const path = `${STAGE_IMAGE_PREFIX}${id}.${image.extension}`;
        try {
            await stageImagesReady;
            await writeTemplateTextures(key, [[path]], { remove: true });
        } catch (_) {
            if (currentTemplateKey() === key) showToast(t('emoteMaker.stageImageDeleteFailed'));
            return;
        }
        forgetStageImageName(id);
        if (currentTemplateKey() !== key || stageImageTemplateKey !== key) return;
        stageUserImages.delete(id);
        forgetStageFrames();
        pushStageUserImages();
    }

    function readStageImage(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(blob);
        });
    }

    async function fetchStageGifFrames(blob, edge) {
        const form = new FormData();
        form.append('file', blob, 'animation.gif');
        const response = await fetch(`/api/emote-maker/library/stage-image-frames?edge=${edge}`, {
            method: 'POST', body: form, credentials: 'same-origin', headers: { 'x-neko-stage-image': '1' },
        });
        if (!response.ok) throw new Error('invalid_stage_gif');
        return response;
    }

    async function importStageImages(files) {
        const key = syncStageImageTemplate();
        if (!key) return;
        const images = Array.from(files || []).filter(
            (file) => /\.(png|gif)$/i.test(file.name || ''),
        );
        if (!images.length) {
            showToast(t('emoteMaker.stageImagesSupportedFormats'));
            return;
        }
        const persisted = [];
        let invalidGif = false;
        let invalidPng = false;
        for (const file of images) {
            const extension = file.name.toLowerCase().endsWith('.gif') ? 'gif' : 'png';
            if (extension === 'gif') {
                try {
                    if (file.size > 32 * 1024 * 1024) throw new Error('invalid_stage_gif');
                    const response = await fetchStageGifFrames(file, 1);
                    await response.arrayBuffer();
                } catch (_) {
                    invalidGif = true;
                    continue;
                }
            }
            let dataUrl;
            try {
                if (!file.size || file.size > 64 * 1024 * 1024) throw new Error('invalid_stage_image');
                if (extension === 'png') {
                    const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer());
                    if (signature.length !== 8 || ![137,80,78,71,13,10,26,10].every((v, i) => signature[i] === v)) {
                        throw new Error('invalid_stage_image');
                    }
                }
                dataUrl = await readStageImage(file.slice(0, file.size, `image/${extension}`));
                if (extension === 'png') await new Promise((resolve, reject) => {
                    const image = new Image();
                    image.onload = () => image.naturalWidth && image.naturalHeight ? resolve() : reject();
                    image.onerror = reject;
                    image.src = dataUrl;
                });
            } catch (_) {
                if (extension === 'gif') invalidGif = true;
                else invalidPng = true;
                continue;
            }
            if (currentTemplateKey() !== key) return;
            const id = stageImageId(file.name);
            persisted.push([id, { name: file.name, dataUrl, extension }]);
        }
        if (currentTemplateKey() !== key) return;
        let saved = true;
        try {
            await persistStageImages(persisted, key);
        } catch (_) {
            saved = false;
        }
        if (currentTemplateKey() !== key) return;
        for (const [id, image] of persisted) {
            stageUserImages.set(id, image);
            rememberStageImageName(id, image.name);
        }
        pushStageUserImages();
        if (!saved) showToast(t('emoteMaker.stageImagesSaveFailed'));
        else if (invalidGif) showToast(t('emoteMaker.stageImagesInvalidGif'));
        else if (invalidPng) showToast(t('emoteMaker.stageImagesSupportedFormats'));
        else showToast(interpolate(t('emoteMaker.stageImagesImported'), { count: persisted.length }));
    }

    function restoreStageImages() {
        const key = syncStageImageTemplate();
        if (!key) return Promise.resolve();
        if (stageImagesReady) return stageImagesReady;
        const restore = (async () => {
            await pendingTemplateWrites.get(key)?.catch(() => {});
            const listResponse = await fetch(`${TEMPLATE_LIBRARY_API}/textures?key=${encodeURIComponent(key)}`,
                { credentials: 'same-origin' });
            if (listResponse.status === 404) return;
            if (!listResponse.ok) throw new Error('stage-image-restore-failed');
            const list = await listResponse.json();
            const paths = (list.paths || []).filter(
                (path) => String(path).startsWith(STAGE_IMAGE_PREFIX) && /\.(png|gif)$/i.test(path));
            const names = loadStageImageNames();
            for (const path of paths) {
                const id = path.slice(STAGE_IMAGE_PREFIX.length, -4);
                const extension = path.slice(-3).toLowerCase();
                const blob = await readTemplateTexture(key, path);
                if (!blob) continue;
                const dataUrl = await readStageImage(blob);
                if (currentTemplateKey() !== key || stageImagesReady !== restore) return;
                if (dataUrl) stageUserImages.set(
                    id, { name: names[id] || id, dataUrl, extension });
            }
            if (paths.length && currentTemplateKey() === key && stageImagesReady === restore) pushStageUserImages();
        })().catch(() => { if (stageImagesReady === restore) stageImagesReady = null; });
        stageImagesReady = restore;
        return restore;
    }

    /** 把内存清单推给舞台模块（冻结对象的方法照常可调）。 */
    function pushStageUserImages() {
        const stageModule = window.NekoEmoteStateStage;
        if (!stageModule || typeof stageModule.setUserImages !== 'function') return;
        stageModule.setUserImages([...stageUserImages.entries()].map(([id, image]) => ({
            id, label: image.name, thumb: image.dataUrl,
        })));
        void stateStageSession?.refreshUserImages()?.catch(() => {
            console.warn('[emote-maker] Failed to refresh user image previews');
        });
    }

    /** 用户图片 → 单帧（按导出边长画到画布中央，透明背景）。 */
    function frameFromStageImage(image, edge) {
        return new Promise((resolve) => {
            const imageEl = new Image();
            imageEl.onload = () => {
                const size = Math.max(1, edge || 512);
                const canvas = document.createElement('canvas');
                canvas.width = size;
                canvas.height = size;
                const ctx = canvas.getContext('2d');
                const scale = Math.min(size / imageEl.width, size / imageEl.height) || 1;
                const width = imageEl.width * scale;
                const height = imageEl.height * scale;
                ctx.drawImage(imageEl, (size - width) / 2, (size - height) / 2, width, height);
                const data = ctx.getImageData(0, 0, size, size);
                resolve([{ width: size, height: size, data: data.data }]);
            };
            imageEl.onerror = () => resolve([]);
            imageEl.src = image.dataUrl;
        });
    }

    async function framesFromStageImage(image, edge) {
        if (image.extension !== 'gif') return frameFromStageImage(image, edge);
        try {
            const response = await fetchStageGifFrames(dataUrlToBlob(image.dataUrl), edge);
            const [cell, columns, delays] = JSON.parse(response.headers.get('X-Stage-Image-Frames'));
            const atlas = await createImageBitmap(await response.blob());
            try {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = cell;
                const ctx = canvas.getContext('2d', { willReadFrequently: true });
                return delays.map((delayMs, index) => {
                    ctx.clearRect(0, 0, cell, cell);
                    ctx.drawImage(atlas, index % columns * cell, Math.floor(index / columns) * cell,
                        cell, cell, 0, 0, cell, cell);
                    return { width: cell, height: cell, data: ctx.getImageData(0, 0, cell, cell).data, delayMs };
                });
            } finally {
                atlas.close();
            }
        } catch (_) {
            return [];
        }
    }

    function stageImageFrameDelay(frame) {
        return Number(frame.delayMs) || 1000 / STAGE_PREVIEW_FPS;
    }

    function stageImageTimelineDuration(frames) {
        return frames.reduce((total, frame) => total + stageImageFrameDelay(frame), 0);
    }

    function trimStageImageTimeline(frames, start, duration) {
        const total = stageImageTimelineDuration(frames);
        const from = Math.min(Math.max(0, start * 1000), Math.max(0, total - 10));
        const to = duration > 0 ? Math.min(total, from + duration * 1000) : total;
        let elapsed = 0;
        const trimmed = [];
        for (const frame of frames) {
            const end = elapsed + stageImageFrameDelay(frame);
            const delayMs = Math.min(to, end) - Math.max(from, elapsed);
            if (delayMs > 0) trimmed.push({ ...frame, delayMs });
            if (end >= to) break;
            elapsed = end;
        }
        return trimmed;
    }

    function sampleStageImageTimeline(frames, fps, maxFrames, minFrames = 1) {
        if (!frames.length) return [];
        const duration = stageImageTimelineDuration(frames);
        let count = Math.max(1, Math.min(maxFrames,
            Math.max(minFrames, Math.floor(duration * fps / 1000 + 1e-9))));
        const preserveAll = minFrames >= frames.length && count >= frames.length;
        if (preserveAll) {
            // Layered sheets have one fixed frame interval. Use a common tick
            // for PNG slide boundaries, so a 10/100/10ms crop stays 1/10/1
            // ticks instead of three equal-length slides. Bound fine-grained
            // windows by the existing frame budget.
            let divisor = 0;
            let totalTicks = 0;
            for (const frame of frames) {
                let ticks = Math.max(1, Math.round(stageImageFrameDelay(frame) * 1000));
                totalTicks += ticks;
                while (ticks) [divisor, ticks] = [ticks, divisor % ticks];
            }
            const exactCount = totalTicks / divisor;
            count = exactCount <= maxFrames
                ? exactCount * Math.min(Math.floor(maxFrames / exactCount), Math.ceil(count / exactCount))
                : maxFrames;
        }
        const delayMs = duration / count;
        let source = 0;
        let end = stageImageFrameDelay(frames[source]);
        return Array.from({ length: count }, (_, index) => {
            const at = index * delayMs;
            // PNG slides each need a sample, including short trimmed edge
            // slides. Timed GIFs can still be thinned to the requested FPS.
            const last = preserveAll ? Math.min(source + 1, frames.length - 1) : frames.length - 1;
            const required = preserveAll ? frames.length - (count - index) : 0;
            while (source < last && (at + (preserveAll ? 1e-9 : 0) >= end || source < required)) {
                source += 1;
                end += stageImageFrameDelay(frames[source]);
            }
            return { ...frames[source], delayMs };
        });
    }

    function resizeStageImageFrames(frames, edge, resized) {
        const source = document.createElement('canvas');
        const target = document.createElement('canvas');
        target.width = target.height = edge;
        const output = target.getContext('2d', { willReadFrequently: true });
        return frames.map((frame) => {
            if (frame.width === edge && frame.height === edge) return frame;
            let data = resized.get(frame.data);
            if (!data) {
                source.width = frame.width;
                source.height = frame.height;
                source.getContext('2d').putImageData(new ImageData(frame.data, frame.width, frame.height), 0, 0);
                output.clearRect(0, 0, edge, edge);
                output.drawImage(source, 0, 0, edge, edge);
                data = output.getImageData(0, 0, edge, edge).data;
                resized.set(frame.data, data);
            }
            return { ...frame, width: edge, height: edge, data };
        });
    }

    function captureStageSource(sourceKey, quality, priorityOverride, windowOverride) {
        const revision = stageFramesRevision;
        const budget = STAGE_QUALITY[quality] || STAGE_QUALITY.full;
        const priority = Number(priorityOverride) > 0
            ? Number(priorityOverride)
            : (quality === 'thumb' ? STAGE_CAPTURE_THUMB : STAGE_CAPTURE_INTERACTIVE);
        // 同一源不同起点是不同的采集：队列键带上起点，缓存才不会互串
        const startAt = Math.max(0, Number(windowOverride && windowOverride.startAt) || 0);
        const captureKey = startAt > 0
            ? `${sourceKey}|${quality}@${startAt}` : `${sourceKey}|${quality}`;
        return queueStageCapture(`${revision}|${captureKey}`,
            priority,
            priority >= STAGE_CAPTURE_INTERACTIVE ? 'frames' : 'thumb',
            async () => {
                if (revision !== stageFramesRevision) return [];
                const captureKey = String(sourceKey || '');
                if (captureKey.startsWith('images:') || captureKey.startsWith('image:')) {
                    const sources = captureKey.slice(captureKey.indexOf(':') + 1).split('|')
                        .map((id) => stageUserImages.get(id)).filter(Boolean);
                    const frames = [];
                    for (const image of sources) {
                        const decoded = await framesFromStageImage(image, budget.edge);
                        frames.push(...decoded);
                    }
                    if (revision !== stageFramesRevision) return [];
                    if (frames.some((frame) => frame.delayMs)) {
                        return sampleStageImageTimeline(frames, quality === 'full' ? 30 : budget.fps,
                            quality === 'full' ? PACK_MAX_FRAMES : budget.maxFrames);
                    }
                    return frames;
                }
                const resolved = await resolveStageSource(sourceKey);
                if (!resolved) return [];
                const { scene, look } = resolved;
                const duration = Math.max(0.05,
                    Number(getImportedAnimationDuration(scene.animation?.id)) || 0.05);
                const frames = Math.max(1,
                    Math.min(budget.maxFrames, Math.ceil(duration * budget.fps)));
                const baked = await bakePackJobsNow(
                    [{ stateKey: 'preview', sceneId: scene.id, look, frames,
                        timeWindow: startAt > 0
                            ? { from: startAt, to: startAt + 1 / budget.fps }
                            : undefined }],
                    { fps: budget.fps, edge: budget.edge },
                );
                return revision === stageFramesRevision && baked[0] ? baked[0].frames : [];
            });
    }

    /**
     * 把"要看的源"翻译成（场景, 外观）。
     * `root:<stateKey>` = 这一格跟随默认外观，得从图里把它的 look 算出来；
     * 其余就是动画 id，外观沿用上游标签页定好的那套。
     */
    async function resolveStageSource(sourceKey) {
        const key = String(sourceKey || '');
        const model = window.NekoEmoteNodeGraph;
        const graph = stageGraph();
        if (!model || !graph || !hasSpineRenderer() || packBusy) return null;
        let sceneId = key;
        let look = { ...currentLookDefaults(), sceneId };
        if (key.indexOf(STAGE_ROOT_PREFIX) === 0) {
            const stateKey = key.slice(STAGE_ROOT_PREFIX.length);
            const evaluated = model.evaluate(graph, currentLookDefaults());
            const output = evaluated.outputs.find((item) => item.stateKey === stateKey);
            if (!output) return null;
            look = output.look;
            sceneId = look.sceneId;
        }
        const scene = getImportedScenes().find((item) => item.id === sceneId);
        return scene ? { scene, look } : null;
    }

    async function stageFramesFor(sourceKey, quality) {
        syncStageSceneCache();
        const key = `${String(sourceKey || '')}|${quality || 'full'}`;
        if (stageFramesBySource.has(key)) return stageFramesBySource.get(key);
        const result = await captureStageSource(String(sourceKey || ''), quality || 'full');
        // 采集失败/场景没了时不要缓存空表，否则切回来永远是空的
        if (result.length) stageFramesBySource.set(key, result);
        return result;
    }

    /**
     * 一条动画的 96px 单帧缩略图。走 thumb 档（1 帧、不碰整条动画）。
     * hot=true 是给右侧状态行用的：那八条要排在抽屉的缩略图长队前面，
     * 否则抽屉一开，状态行就长时间是空白。
     * opts.startAt：状态格的缩略图要采这一格起始手柄指向的那一帧——
     * 同一条动画不同起点采出来的样子不一样，缓存键按起点拆开。
     */
    async function stageThumbFor(sceneId, hot, opts) {
        const startAt = Math.max(0, Number(opts && opts.startAt) || 0);
        const key = startAt > 0 ? `${String(sceneId)}@${startAt}` : String(sceneId || '');
        if (stageThumbs.has(key)) return stageThumbs.get(key);
        const frames = await captureStageSource(
            String(sceneId || ''), 'thumb',
            hot ? STAGE_CAPTURE_INTERACTIVE : STAGE_CAPTURE_THUMB,
            startAt > 0 ? { startAt } : undefined);
        const first = frames[0];
        if (!first) return '';
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, first.width | 0);
        canvas.height = Math.max(1, first.height | 0);
        const data = first.data instanceof Uint8ClampedArray
            ? first.data : new Uint8ClampedArray(first.data);
        canvas.getContext('2d').putImageData(new ImageData(data, canvas.width, canvas.height), 0, 0);
        const url = canvas.toDataURL('image/png');
        stageThumbs.set(key, url);
        return url;
    }

    function openStateStage() {
        void restoreStageImages();   // 副本里的图片先拉回来（异步不阻塞打开）
        pushStageUserImages();       // 把当前清单推给舞台模块
        const stageModule = window.NekoEmoteStateStage;
        const model = window.NekoEmoteNodeGraph;
        if (!stageModule || !model) {
            showToast(t('emoteMaker.stageUnavailable'));
            return;
        }
        if (!hasSpineRenderer() || !getImportedScenes().length) {
            showToast(t('emoteMaker.packNeedsTemplate'));
            return;
        }
        if (stateStageSession) stateStageSession.dispose();
        if (!state.nodeGraph) state.nodeGraph = model.normalizeGraph(basicGraph());
        forgetStageFrames();
        stateStageSession = stageModule.create({
            model,
            graph: state.nodeGraph,
            t,
            fps: () => STAGE_PREVIEW_FPS,
            format: () => state.packFormat,
            framesFor: stageFramesFor,
            thumbFor: stageThumbFor,
            onPickerClose: dropQueuedThumbs,
            // 抽屉里的动作长按拖出来，走和动画列表完全同一套投递机制
            // 表演台的行自己已经用 NekoEmoteDragHold 计好长按了，这里只管起拖；
            // sourceElement 传回去，ghost 才能长成那张选项卡的样子。
            beginDrag: (payload, event, sourceElement) => beginPackDrag(payload, event, sourceElement),
            animationsFor: () => getImportedScenes().map((scene) => ({
                value: scene.id,
                label: scene.name || scene.animation?.id || scene.id,
            })),
            onImportImages: () => { void restoreStageImages().then(() => stageImageInput?.click()); },
            onRemoveImage: removeStageImage,
            onChange: (next) => {
                state.nodeGraph = next;
                state.packMode = 'graph';
                // 拖动时每帧都会回调，落盘必须合并成一次
                window.clearTimeout(stagePersistTimer);
                stagePersistTimer = window.setTimeout(() => {
                    persistNodeGraph();
                    persistLayout();
                }, 250);
            },
            onCommit: (before, after) => {
                if (JSON.stringify(before) === JSON.stringify(after)) return;
                state.nodeGraph = after;
                persistNodeGraph();
                persistTemplateSession();
                rememberHistory(before, after, restoreStageGraph);
            },
            onClose: () => {
                if (stateStageSession) stateStageSession.dispose();
                stateStageSession = null;
                window.clearTimeout(stagePersistTimer);
                persistNodeGraph();
                persistLayout();
                renderPackPanel();
            },
        });
        renderPackPanel();
    }

    function restoreStageGraph(graph) {
        forgetStageFrames();
        state.nodeGraph = structuredClone(graph);
        stateStageSession?.setGraph(state.nodeGraph);
        persistNodeGraph();
        persistTemplateSession();
        renderPackPanel();
    }

    // 把图算出来的 look 写进 state，让现有的场景套用与 overlay 渲染原样画出来。
    function applyLookToState(look) {
        state.selectedSceneId = look.sceneId || state.selectedSceneId;
        state.templateSkins = look.skins.map((item) => ({ ...item }));
        state.templateSlots = look.slots.map((item) => ({ ...item }));
        state.adjustments = {
            x: look.transform.x,
            y: look.transform.y,
            // 原来这里硬写 0：套一个取景会把旋转悄悄抹平，取景就白存了
            rotation: Number(look.transform.rotation) || 0,
            scale: Math.round(look.transform.scale * 100),
        };
    }




    // ── 上次打开的模板：只记元数据，不记素材字节 ─────────────────────────────
    // 浏览器不会把上一次选过的文件留给下一次，而"缓存这个包"与 assetPolicy
    // (local-import-and-render-only) 冲突，所以这里只存到足以一键重开+恢复编辑
    // 状态的程度：指纹、名字、导入方式、选中的场景、皮肤、色组、调整与映射。
    const LAST_TEMPLATE_KEY = 'neko-emote-maker-last-template';
    let lastTemplate = null;

    function templateFingerprint(pkg) {
        return pkg?.id || '';
    }

    function snapshotTemplateSession() {
        if (!templatePackage) return null;
        const pick = (list) => (Array.isArray(list) ? list.map((item) => ({ ...item })) : []);
        return {
            schemaVersion: 1,
            fingerprint: templateFingerprint(templatePackage),
            packageName: templatePackage.source?.packageName || '',
            displayName: templatePackage.model?.displayName || '',
            sceneId: state.selectedSceneId || '',
            groupId: state.groupId || '',
            groups: (Array.isArray(state.groups) ? state.groups : []).map((group) => ({ ...group })),
            shots: (Array.isArray(state.shots) ? state.shots : []).map((shot) => ({ ...shot })),
            packSceneIds: { ...(state.packSceneIds || {}) },
            nodeGraph: state.nodeGraph || null,
            packName: state.packName || '',
            packFormat: state.packFormat === 'layered' ? 'layered' : 'flat',
            packPriority: state.packPriority === 'emotion' ? 'emotion' : 'talking',
            character: {
                skins: pick(state.templateSkins),
                slots: pick(state.templateSlots),
            },
            adjustments: { ...state.adjustments },
            adjustOverwrite: state.adjustOverwrite,
            colorMode: state.colorMode,
            secondaryColor: state.secondaryColor,
        };
    }

    function persistTemplateSession() {
        persistTemplateToLibrary();
    }

    function loadTemplateSession() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(LAST_TEMPLATE_KEY) || 'null');
        } catch (_) {
            saved = null;
        }
        lastTemplate = saved && saved.schemaVersion === 1 && saved.fingerprint ? saved : null;
    }

    function currentTemplateKey() {
        if (!templateLibraryModule || !templatePackage) return '';
        return templateLibraryModule.entryKeyFor(templatePackage);
    }

    /**
     * 当前模板的显示名：库条目在就用库的裁决（用户改名 > 来源名 > 包名 > 自报名），
     * 库还没提交（导入的同一拍）就按同一条链就地算一遍 —— 两处顺序不能反，
     * 否则标题会比卡片晚一拍才对上。
     */
    function currentTemplateLabel() {
        if (!templateLibraryModule || !templatePackage) return '';
        const entry = templateLibraryModule.find(templateLibraryState, currentTemplateKey());
        if (entry) return templateLibraryModule.entryLabel(entry);
        const pkgName = String(templatePackage.source?.packageName || '')
            .replace(/\.zip$/i, '');
        return pkgName || templatePackage.model?.displayName || '';
    }

    /** 注册表里还活着的 store 不能释放：清字节是不可逆的，切回去就得重新读盘。 */
    function holdsTemplateStore(store) {
        if (!store) return false;
        for (const held of loadedTemplates.values()) {
            if (held.store === store) return true;
        }
        return false;
    }

    function rememberLoadedTemplate() {
        const key = currentTemplateKey();
        if (!key || !templateAssetStore) return '';
        const previous = loadedTemplates.get(key)?.store;
        loadedTemplates.set(key, {
            pkg: templatePackage,
            store: templateAssetStore,
        });
        if (previous && previous !== templateAssetStore && !holdsTemplateStore(previous)) releaseAssetStore(previous);
        return key;
    }

    // 96px 的 PNG，只用来"认脸"。取不到（画布还没画过、跨域像素）就留空，
    // 库里那条会继续用上一次存的缩略图。
    function templateThumbDataUrl() {
        if (!canvas || !templatePackage) return '';
        try {
            const box = document.createElement('canvas');
            box.width = 96;
            box.height = 96;
            const ctx = box.getContext('2d');
            ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height, 0, 0, 96, 96);
            const url = box.toDataURL('image/png');
            return typeof url === 'string' && url.startsWith('data:image/png') ? url : '';
        } catch (_) {
            return '';
        }
    }

    function persistTemplateToLibrary({ withThumb = false, sourceName = '' } = {}) {
        const snapshot = snapshotTemplateSession();
        if (!snapshot) return;
        // Every save entry point writes the same complete snapshot to both
        // stores before a later restore can choose either copy.
        lastTemplate = snapshot;
        try {
            localStorage.setItem(LAST_TEMPLATE_KEY, JSON.stringify(snapshot));
        } catch (_) { /* In-memory recovery remains available. */ }
        if (!templateLibraryModule) return;
        const key = currentTemplateKey();
        if (!key) return;
        const existing = templateLibraryModule.find(templateLibraryState, key);
        const thumb = withThumb ? templateThumbDataUrl() : '';
        try {
            let next = templateLibraryModule.upsert(templateLibraryState, templateLibraryModule.makeEntry({
                key,
                pkg: templatePackage,
                // 来源名：这次导入带来了就用这次的（ZIP 改名后再导一次，名字跟着走），
                // 没带来（切回/重绘触发的保存）沿用库里那份。alias 只有双击改名才写，
                // 这里永远沿用，别把用户改的名冲掉
                sourceName: templatePackage.source?.builtIn
                    ? templatePackage.model.displayName
                    : sourceName || (existing && existing.sourceName) || '',
                alias: (existing && existing.alias) || '',
                thumb: thumb || (existing && existing.thumb) || '',
                addedAt: existing && existing.addedAt,
                session: {
                    ...(existing && existing.session ? existing.session : {}),
                    ...snapshot,
                },
            }));
            next = templateLibraryModule.touch(next, key);
            templateLibraryState = next;
            templateLibraryModule.save(localStorage, templateLibraryState);
        } catch (_) {
            // 存不下只影响"下次库里还认得它"，不影响当前编辑。
        }
    }

    function loadTemplateLibrary() {
        if (!templateLibraryModule) return;
        let storage;
        try { storage = window.localStorage; } catch (_) { /* Storage can be unavailable. */ }
        templateLibraryState = templateLibraryModule.load(storage);
        if (!templateLibraryState.entries.length && lastTemplate) {
            const migrated = storage && templateLibraryModule.migrateFromLegacy(lastTemplate, storage);
            if (migrated) {
                templateLibraryState = migrated;
                try {
                    templateLibraryModule.save(storage, templateLibraryState);
                } catch (_) { /* 同上 */ }
            }
        }
    }

    // ── 导入即拷副本 ──────────────────────────────────────────────────────────
    // 导入成功后把 Spine ZIP 拷进应用数据目录，重开直接从副本恢复。
    // 副本即唯一事实：原件更新/删除/挪走都与这里无关，想更新就再导一次（同键原地覆盖）。
    // 后端没有这套接口时一切照旧：存副本失败提示一句，重开退回"请重新定位"。
    const TEMPLATE_LIBRARY_API = '/api/emote-maker/library';
    const builtinTemplates = new Map();
    const pendingTemplateWrites = new Map();
    const pendingTemplateRemovals = new Set();
    let templateArchiveBytes = new Map();
    let templateArchiveTotalBytes = 0;
    let archiveSizeRequest = 0;

    function findTemplateLibraryEntry(key) {
        const saved = templateLibraryModule?.find(templateLibraryState, key);
        const pkg = builtinTemplates.get(key);
        if (!pkg) return saved;
        const entry = templateLibraryModule.makeEntry({ pkg, sourceName: pkg.model.displayName });
        return {
            ...entry, ...saved, builtIn: true,
            packageName: entry.packageName, displayName: entry.displayName, sourceName: entry.sourceName,
        };
    }

    async function loadBuiltinTemplates() {
        if (!templateLibraryModule) return;
        try {
            const response = await fetch('/api/emote-maker/builtins', { credentials: 'same-origin' });
            const payload = await response.json();
            if (!response.ok || !payload.success || !Array.isArray(payload.packages)) return;
            for (const pkg of payload.packages) {
                if (pkg?.source?.builtIn === true) builtinTemplates.set(pkg.id, pkg);
            }
            renderTemplateLibraryGrid();
        } catch (_) {
            // A library request failure leaves ordinary local imports available.
        }
    }

    function formatTemplateBytes(bytes) {
        return templateLibraryModule && typeof templateLibraryModule.formatBytes === 'function'
            ? templateLibraryModule.formatBytes(bytes) : '';
    }

    async function refreshTemplateArchiveSizes() {
        const request = ++archiveSizeRequest;
        try {
            const response = await fetch(TEMPLATE_LIBRARY_API, { credentials: 'same-origin' });
            const payload = await response.json();
            if (request !== archiveSizeRequest || !response.ok || !payload.success || !Array.isArray(payload.entries)) return;
            const next = new Map(payload.entries.map((entry) => [
                String(entry.key), Number(entry.bytes) || 0,
            ]));
            const nextTotal = Number(payload.totalBytes) || 0;
            const same = nextTotal === templateArchiveTotalBytes
                && next.size === templateArchiveBytes.size
                && [...next].every(([key, bytes]) => templateArchiveBytes.get(key) === bytes);
            if (same) return;
            templateArchiveBytes = next;
            templateArchiveTotalBytes = nextTotal;
            renderTemplateLibraryGrid();
        } catch (_) {
            // 占用显示拿不到就先不显示，不影响任何功能
        }
    }

    function templateTextureUrl(key, path) {
        return `${TEMPLATE_LIBRARY_API}/texture?key=${encodeURIComponent(key)}&path=${encodeURIComponent(path)}`;
    }

    async function readTemplateTexture(key, path) {
        const response = await fetch(templateTextureUrl(key, path), { credentials: 'same-origin' });
        return response.ok ? response.blob() : null;
    }

    async function writeTemplateLibrary(key, resource, form) {
        // 重新导入同一模板会重写副本。附带图片的增删也要排队，避免被这次重写覆盖。
        const previous = pendingTemplateWrites.get(key);
        const write = (async () => {
            await previous?.catch(() => {});
            const response = await fetch(`${TEMPLATE_LIBRARY_API}/${resource}?key=${encodeURIComponent(key)}`,
                { method: 'POST', body: form, credentials: 'same-origin', headers: { 'x-neko-emote-library': '1' } },
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) throw new Error(payload.error || 'template-library-write-failed');
        })();
        pendingTemplateWrites.set(key, write);
        try {
            await write;
        } finally {
            if (pendingTemplateWrites.get(key) === write) pendingTemplateWrites.delete(key);
        }
    }

    function writeTemplateTextures(key, entries, { remove = false } = {}) {
        const form = new FormData();
        for (const [path, blob] of entries) {
            form.append('texture_paths', path);
            if (!remove) form.append('textures', blob, path.split('/').pop());
        }
        return writeTemplateLibrary(key, remove ? 'textures/remove' : 'textures', form);
    }

    /** 把刚导入的模板拷一份到应用数据目录。 */
    async function persistTemplateArchive(key, file) {
        const form = new FormData();
        form.append('file', file, file.name || 'template.zip');
        form.append('model_path', templatePackage?.model?.skeleton?.path || '');
        form.append('atlas_path', templatePackage?.model?.atlas?.path || '');
        await writeTemplateLibrary(key, 'archive', form);
        if (!pendingTemplateRemovals.has(key)) await refreshTemplateArchiveSizes();
    }

    /**
     * 从本地副本把一个模板装回来。没有副本 / 副本拉不下来 → false，
     * 调用方再走原来的"请重新定位"。老库条目第一次被点开时走的就是这条路：
     * 重新定位导入完成后副本自然就有了（迁移不需要专门代码）。
     */
    async function tryRestoreTemplateFromArchive(key) {
        if (!key || !templateLibraryModule) return false;
        const entry = findTemplateLibraryEntry(key);
        const builtinPackage = builtinTemplates.get(key);
        try {
            const archiveResponse = await fetch(`${TEMPLATE_LIBRARY_API}/archive?key=${encodeURIComponent(key)}`,
                { credentials: 'same-origin' });
            if (!archiveResponse.ok) return false;
            const blob = await archiveResponse.blob();
            // 副本使用应用生成的文件名；卡片显示名由库条目恢复。
            const rawName = archiveResponse.headers.get('X-Archive-Filename') || '';
            const fileName = rawName ? decodeURIComponent(rawName) : 'template.zip';
            const file = new File([blob], fileName, { type: 'application/zip' });
            return await importTemplateFile(file, {
                displayName: builtinPackage ? '' : templateLibraryModule.entryLabel(entry) || '',
                fromArchive: true,
                restoreKey: key,
                builtinPackage,
            });
        } catch (_) {
            return false;
        }
    }

    /** 等待导入写入落定，再确认本地副本已删除。 */
    async function deleteTemplateArchive(key) {
        await pendingTemplateWrites.get(key)?.catch(() => {});
        const response = await fetch(
            `${TEMPLATE_LIBRARY_API}?key=${encodeURIComponent(key)}`,
            { method: 'DELETE', credentials: 'same-origin' },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.success) throw new Error(payload.error || 'archive-delete-failed');
    }

    /**
     * 从内存注册表把某个模板装回来：不读盘、不请求后端、不重新解析场景。
     * 会话恢复复用 applyTemplateSession —— 它按"指纹对不对得上"决定要不要恢复外观，
     * 那套校验（只接受仍然存在的槽位名等）不该在库里再写第二份。
     */
    function applyTemplateFromRegistry(key) {
        const held = loadedTemplates.get(key);
        const entry = findTemplateLibraryEntry(key);
        if (!held || !entry) return false;
        persistTemplateToLibrary();
        groupPreview = null;
        spineRendererGeneration += 1;
        disposeSpineRenderer();
        templatePackage = held.pkg;
        templateAssetStore = held.store;
        lastTemplate = {
            ...entry.session,
            fingerprint: templateFingerprint(held.pkg),
        };
        state.templateSlotDefaults = (held.pkg.character?.slots || []).map((item) => ({ ...item }));
        state.templateSlots = state.templateSlotDefaults.map((item) => ({ ...item }));
        state.templateSlotGroup = state.templateSlots[0]?.name || '';
        resetTemplateSession();
        applyTemplateSession(held.pkg);
        if (!state.groupId) {
            const first = allGroups()[0];
            if (first) selectGroup(first.id);
        }
        if (entry.session.packName) state.packName = entry.session.packName;
        state.packFormat = entry.session.packFormat === 'layered' ? 'layered' : 'flat';
        state.packPriority = entry.session.packPriority === 'emotion' ? 'emotion' : 'talking';
        const firstScene = getImportedScenes()[0];
        if (firstScene && !getImportedScenes().some((scene) => scene.id === state.selectedSceneId)) {
            state.selectedSceneId = firstScene.id;
            state.animationId = firstScene.animation?.id || '';
        }
        history.length = 0;
        future.length = 0;
        templateLibraryState = templateLibraryModule.touch(templateLibraryState, key);
        templateLibraryModule.save(localStorage, templateLibraryState);
        renderAll();
        void activateNativeSpineRenderer();
        return true;
    }

    /**
     * 切到库里的另一个模板。内存里没有它（重启过、或那次导入的 store 已释放），
     * 就借现有导入流程请用户重新定位一次：落到同一个指纹上，库里那条会被就地补齐，
     * 不会多出第二条。
     */
    async function switchTemplate(key) {
        if (!templateLibraryModule || !key || packBusy || pendingTemplateRemovals.has(key)) return;
        if (key === currentTemplateKey()) return;
        if (!findTemplateLibraryEntry(key)) return;
        if (applyTemplateFromRegistry(key)) {
            showToast(t('emoteMaker.templateSwitched'));
            return;
        }
        persistTemplateToLibrary();
        // 内存里没有（重启过）就先试本地副本：有副本就不该再让用户去认一遍路径。
        // 旧条目第一次没有副本，走完重新定位导入后副本自然就有了。
        if (await tryRestoreTemplateFromArchive(key)) {
            showToast(t('emoteMaker.templateSwitched'));
            return;
        }
        if (builtinTemplates.has(key)) {
            reportImportFailure(new Error('builtin-template-load-failed'));
            return;
        }
        if (!pendingTemplateRemovals.has(key) && templateLibraryModule.find(templateLibraryState, key)) {
            chooseTemplateArchive();
        }
    }

    /** 页内确认框：window.confirm 的默认样式和这一页毫不相干，插值也归这里管。 */
    function confirmModal(text) {
        return new Promise((resolve) => {
            const box = $('#emote-confirm');
            if (!box) {
                resolve(window.confirm(text));
                return;
            }
            const previousFocus = document.activeElement;
            $('#emote-confirm-text').textContent = text;
            box.hidden = false;
            const ok = $('#emote-confirm-ok');
            const cancel = $('#emote-confirm-cancel');
            const done = (value) => {
                box.hidden = true;
                ok.removeEventListener('click', onOk);
                cancel.removeEventListener('click', onCancel);
                document.removeEventListener('keydown', onKey, true);
                if (previousFocus?.isConnected) previousFocus.focus();
                resolve(value);
            };
            const onOk = () => done(true);
            const onCancel = () => done(false);
            const onKey = (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    done(false);
                } else if (event.key === 'Enter') {
                    event.preventDefault();
                    event.stopPropagation();
                    done(true);
                } else if (event.key === 'Tab') {
                    event.preventDefault();
                    event.stopPropagation();
                    (document.activeElement === ok ? cancel : ok).focus();
                }
            };
            ok.addEventListener('click', onOk);
            cancel.addEventListener('click', onCancel);
            document.addEventListener('keydown', onKey, true);
            ok.focus();
        });
    }

    async function removeTemplate(key) {
        if (!templateLibraryModule || !key || key.startsWith('builtin:') || pendingTemplateRemovals.has(key)) return;
        // 删的是用户攒的模板 + 它的本地副本，得先问一句；名字用卡片上那个
        const entry = templateLibraryModule.find(templateLibraryState, key);
        const label = templateLibraryModule.entryLabel(entry) || key;
        if (!(await confirmModal(interpolate(t('emoteMaker.removeTemplateConfirm'), { name: label })))) {
            return;
        }
        if (pendingTemplateRemovals.has(key) || !templateLibraryModule.find(templateLibraryState, key)) return;
        pendingTemplateRemovals.add(key);
        renderTemplateLibraryGrid();
        try {
            await deleteTemplateArchive(key);
        } catch (_) {
            showToast(t('emoteMaker.archiveDeleteFailed'));
            return;
        } finally {
            pendingTemplateRemovals.delete(key);
            renderTemplateLibraryGrid();
        }
        const held = loadedTemplates.get(key);
        const isCurrent = key === currentTemplateKey();
        loadedTemplates.delete(key);
        // 移除的是当前正用着的那个时，store 归页面自己收尾，不能留着读不到的字节
        if (held && !isCurrent && !holdsTemplateStore(held.store)) releaseAssetStore(held.store);
        templateLibraryState = templateLibraryModule.remove(templateLibraryState, key);
        const clearSession = isCurrent || lastTemplate?.fingerprint === key;
        if (clearSession) lastTemplate = null;
        try {
            templateLibraryModule.save(localStorage, templateLibraryState);
            if (clearSession) localStorage.removeItem(LAST_TEMPLATE_KEY);
        } catch (_) { /* 存不下就只丢这一次的记忆 */ }
        if (isCurrent) {
            spineRendererGeneration += 1;
            disposeSpineRenderer();
            releaseTemplateAssetStore();
            templatePackage = null;
        }
        renderAll();
        void refreshTemplateArchiveSizes();
    }

    function applyTemplateSession(manifest, savedGraph = lastTemplate?.nodeGraph) {
        if (!lastTemplate || !manifest) return false;
        if (lastTemplate.fingerprint !== templateFingerprint(manifest)) {
            // 不是同一个包就什么都不恢复。把上一个包的外观套到新包上，
            // 会得到一个"看起来能开但全是错的东西"，比不恢复更糟。
            return false;
        }
        const scenes = getImportedScenes();
        const ids = new Set(scenes.map((scene) => scene.id));
        const aliases = manifest.source?.sceneAliases || {};
        const resolveSceneId = (id) => Object.hasOwn(aliases, id) ? aliases[id] : id;
        const character = lastTemplate.character || {};
        if (typeof lastTemplate.packName === 'string') state.packName = lastTemplate.packName;
        if (['flat', 'layered'].includes(lastTemplate.packFormat)) state.packFormat = lastTemplate.packFormat;
        if (['emotion', 'talking'].includes(lastTemplate.packPriority)) state.packPriority = lastTemplate.packPriority;
        if (Array.isArray(character.skins)) state.templateSkins = character.skins.map((item) => ({ ...item }));
        if (Array.isArray(character.slots) && character.slots.length) {
            // 只接受新包里仍然存在的槽位名，避免把过期色组写回去。
            const known = new Set((manifest.character?.slots || []).map((slot) => String(slot?.name || '')));
            const kept = character.slots.filter((slot) => known.has(String(slot?.name || '')));
            if (kept.length) {
                const savedNames = new Set(kept.map((slot) => slot.name));
                const added = state.templateSlotDefaults.filter((slot) => !savedNames.has(slot.name));
                state.templateSlots = [...kept, ...added].map((slot) => ({ ...slot }));
                state.templateSlotGroup = kept[0]?.name || '';
            }
        }
        if (lastTemplate.adjustments && typeof lastTemplate.adjustments === 'object') {
            state.adjustments = { ...state.adjustments, ...lastTemplate.adjustments };
        }
        if (typeof lastTemplate.adjustOverwrite === 'boolean') state.adjustOverwrite = lastTemplate.adjustOverwrite;
        if (lastTemplate.colorMode) state.colorMode = lastTemplate.colorMode;
        if (lastTemplate.secondaryColor) state.secondaryColor = lastTemplate.secondaryColor;
        const selectedSceneId = resolveSceneId(lastTemplate.sceneId);
        const scene = scenes.find((item) => item.id === selectedSceneId);
        if (scene) {
            state.selectedSceneId = scene.id;
            state.animationId = scene.animation?.id || state.animationId;
        }
        // 组/取景跟着模板回来。成员如果在这个包里已经不存在（模板换了版本、
        // 场景数变了），就把它从组里剔掉，而不是留着一条烘不出来的引用。
        if (groupLibrary && Array.isArray(lastTemplate.groups)) {
            const groups = groupLibrary.groupsFrom(lastTemplate.groups);
            const shots = groupLibrary.shotsFrom(lastTemplate.shots).map((shot) => ({
                ...shot, sourceSceneId: resolveSceneId(shot.sourceSceneId),
            }));
            const shotIds = new Set(shots.map((shot) => shot.id));
            state.shots = shots;
            state.groups = groups.map((group) => ({
                ...group,
                items: group.items.map((item) => item.kind === 'scene'
                    ? { ...item, ref: resolveSceneId(item.ref) } : item).filter((item) => (
                    item.kind === 'scene' ? ids.has(item.ref) : shotIds.has(item.ref)
                )),
            }));
        }
        if (allGroups().some((group) => group.id === lastTemplate.groupId)) {
            state.groupId = lastTemplate.groupId || '';
        }
        const pack = {};
        Object.entries(lastTemplate.packSceneIds || {}).forEach(([key, sceneId]) => {
            const id = resolveSceneId(sceneId);
            if (ids.has(id)) pack[key] = id;
        });
        state.packSceneIds = pack;
        const graphModel = window.NekoEmoteNodeGraph;
        if (savedGraph && graphModel) {
            // Repair stale sources before duplicate outputs are ranked and merged.
            const prepared = {
                ...savedGraph,
                nodes: (savedGraph.nodes || []).map((node) => {
                    if (node.type !== 'source.scene') return node;
                    const sceneId = resolveSceneId(node.params?.sceneId);
                    return { ...node, params: { ...node.params, sceneId: ids.has(sceneId) ? sceneId : '' } };
                }),
            };
            state.nodeGraph = graphModel.normalizeGraph(prepared);
            persistNodeGraph();
        }
        return true;
    }

    // ── 可拖拽的比例布局 ──────────────────────────────────────────────────────
    // 三栏宽度和底部列表高度都存在一个独立键里：它们是窗口偏好，既不属于角色
    // 设计（不该进撤销栈），也不属于导出参数（换模板时不应该跟着变）。
    const LAYOUT_STORAGE_KEY = 'neko-emote-maker-layout';
    // dock 的下限不是审美问题而是几何问题：它至少要容下标题 + 可滚动的列表 +
    // 40px 播放控件，否则 .animation-dock 的 overflow:hidden 会把控件裁掉。
    const LAYOUT_LIMITS = Object.freeze({
        left: Object.freeze([200, 520]),
        right: Object.freeze([220, 560]),
        dock: Object.freeze([180, 620]),
    });
    const layout = { left: 300, right: 360, dock: 0 };

    // 静态像素上限在窄窗口下会把面板拖出屏幕外（实测 1120px 窗口把两侧拖到
    // 520/560 会让网格溢出 52px、右栏出界 36px，而且手柄自己也跑出可视区，
    // 用户再也拖不回来）。所以上限必须按当前容器实算：可用宽度减去中列最小值、
    // 两条手柄轨道和全部 gap，再留给对面一个最小值。
    function parsePx(value, fallback) {
        const number = parseFloat(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function gridTracks(element, property) {
        const raw = getComputedStyle(element)[property] || '';
        return raw.split(' ').filter(Boolean).map((track) => parsePx(track, 0));
    }

    function sideMaximum(which) {
        const bounds = LAYOUT_LIMITS[which];
        const grid = document.querySelector('.maker-grid');
        if (!grid) return bounds[1];
        const style = getComputedStyle(grid);
        const tracks = gridTracks(grid, 'gridTemplateColumns');
        const gap = parsePx(style.columnGap, 0);
        const centerMin = parsePx(style.getPropertyValue('--center-min'), 340);
        const handleWidth = tracks.length > 3 ? tracks[1] + tracks[3] : 20;
        // 约束要加在两栏之"和"上：先前各侧只给对面留一个最小值，结果两边各自
        // 合法、加起来仍然超预算（1090px 下算出 438+458+340 > 1058，照样溢出）。
        // 所以这里用对面"当前"的宽度来限本侧，拖宽一边就会压缩另一边的上限。
        const other = which === 'left' ? 'right' : 'left';
        const usable = grid.clientWidth - centerMin - handleWidth - gap * 4;
        const room = usable - layout[other];
        return Math.max(bounds[0], Math.min(bounds[1], Math.floor(room)));
    }

    function dockMaximum() {
        const bounds = LAYOUT_LIMITS.dock;
        const workspace = document.querySelector('.workspace-panel');
        if (!workspace) return bounds[1];
        const style = getComputedStyle(workspace);
        const rows = gridTracks(workspace, 'gridTemplateRows');
        const gap = parsePx(style.rowGap, 0);
        const heading = rows.length > 0 ? rows[0] : 0;
        const handle = rows.length > 2 ? rows[2] : 12;
        const contentHeight = parsePx(style.height, 0) - parsePx(style.paddingTop, 0) - parsePx(style.paddingBottom, 0)
            - parsePx(style.borderTopWidth, 0) - parsePx(style.borderBottomWidth, 0);
        const canvasShare = parsePx(style.getPropertyValue('--canvas-min-share'), 35) / 100;
        const canvasMin = Math.min(parsePx(style.getPropertyValue('--canvas-min'), 300), contentHeight * canvasShare);
        const dockMin = parsePx(style.getPropertyValue('--dock-min'), 110);
        const shareable = contentHeight - canvasMin - heading - handle - gap * 3;
        return Math.max(dockMin, Math.min(bounds[1], Math.floor(shareable)));
    }

    function clampLayout(which, value) {
        const bounds = LAYOUT_LIMITS[which];
        const number = Number(value);
        if (!Number.isFinite(number)) return layout[which];
        const max = which === 'dock' ? dockMaximum() : sideMaximum(which);
        return Math.min(max, Math.max(bounds[0], Math.round(number)));
    }

    function applyLayout() {
        const grid = document.querySelector('.maker-grid');
        if (!grid) return;
        // 两侧互为对方的余量约束，走两遍才收敛（钳制只会往下调，不会震荡）。
        layout.left = clampLayout('left', layout.left);
        layout.right = clampLayout('right', layout.right);
        layout.left = clampLayout('left', layout.left);
        layout.right = clampLayout('right', layout.right);
        grid.style.setProperty('--col-left', `${layout.left}px`);
        grid.style.setProperty('--col-right', `${layout.right}px`);
        if (layout.dock) layout.dock = clampLayout('dock', layout.dock);
        // dock 允许 0 表示"未固定"，此时交给内容自适应。
        const workspace = document.querySelector('.workspace-panel');
        if (workspace) {
            if (layout.dock) workspace.style.setProperty('--dock-height', `${layout.dock}px`);
            else workspace.style.removeProperty('--dock-height');
        }
    }

    function loadLayout() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(LAYOUT_STORAGE_KEY) || 'null');
        } catch (_) {
            saved = null;
        }
        if (!saved || typeof saved !== 'object') return;
        ['left', 'right'].forEach((which) => {
            if (Number.isFinite(Number(saved[which])) && saved[which] > 0) {
                layout[which] = clampLayout(which, saved[which]);
            }
        });
        // dock 允许 0 表示"未固定"
        layout.dock = Number.isFinite(Number(saved.dock)) && Number(saved.dock) >= 0
            ? Math.min(LAYOUT_LIMITS.dock[1], Math.max(0, Math.round(Number(saved.dock))))
            : 0;
        // 旧存档里可能带着 listMode（当时列表分「场景 / 动画」两栏）。现在只剩一种，
        // 读到它就忽略 —— 不删键位，免得有人回滚代码时又丢设置。
        if (saved.packMode === 'basic' || saved.packMode === 'graph') {
            state.packMode = saved.packMode;
        }
        if (typeof saved.groupPanelOpen === 'boolean') state.groupPanelOpen = saved.groupPanelOpen;
    }

    function persistLayout() {
        try {
            localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({
                left: layout.left,
                right: layout.right,
                dock: layout.dock,
                packMode: state.packMode,
                groupPanelOpen: state.groupPanelOpen,
            }));
        } catch (_) {
            // 存不下只影响下次打开时的布局记忆。
        }
    }

    // sign 决定"往哪个方向拖是变大"：左栏向右变大、右栏向左变大、
    // 底部列表向上变大（所以 y 方向取负）。
    function bindResizeHandle(handle, read, write, signX, signY) {
        if (!handle) return;
        let startX = 0;
        let startY = 0;
        let startValue = 0;
        let dragging = false;

        const move = (event) => {
            if (!dragging) return;
            // 每个手柄只管一个轴；sign 为 ±1，表示"往哪个方向拖算变大"。
            const raw = signX
                ? (event.clientX - startX) * signX
                : (event.clientY - startY) * signY;
            write(clampValueFor(handle, startValue + Math.round(raw)));
        };
        const stop = () => {
            if (!dragging) return;
            dragging = false;
            handle.classList.remove('is-dragging');
            document.body.classList.remove('is-resizing');
            persistLayout();
        };
        handle.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            dragging = true;
            startX = event.clientX;
            startY = event.clientY;
            startValue = read();
            handle.classList.add('is-dragging');
            document.body.classList.add('is-resizing');
            if (typeof handle.setPointerCapture === 'function') {
                handle.setPointerCapture(event.pointerId);
            }
        });
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', stop);
        handle.addEventListener('pointercancel', stop);
        handle.addEventListener('lostpointercapture', stop);
        handle.addEventListener('keydown', (event) => {
            const step = event.shiftKey ? 24 : 8;
            let delta = 0;
            if (signX) {
                if (event.key === 'ArrowLeft') delta = -step * signX;
                if (event.key === 'ArrowRight') delta = step * signX;
            }
            if (signY) {
                if (event.key === 'ArrowUp') delta = -step * signY;
                if (event.key === 'ArrowDown') delta = step * signY;
            }
            if (!delta) return;
            event.preventDefault();
            write(clampValueFor(handle, read() + delta));
            persistLayout();
        });
    }

    function clampValueFor(handle, value) {
        if (handle.id === 'resize-left') return clampLayout('left', value);
        if (handle.id === 'resize-right') return clampLayout('right', value);
        return clampLayout('dock', value);
    }

    function setupResizing() {
        bindResizeHandle(
            $('#resize-left'),
            () => layout.left,
            (value) => { layout.left = value; applyLayout(); },
            1, 0,
        );
        bindResizeHandle(
            $('#resize-right'),
            () => layout.right,
            (value) => { layout.right = value; applyLayout(); },
            -1, 0,
        );
        bindResizeHandle(
            $('#resize-dock'),
            () => layout.dock || measureDock(),
            (value) => { layout.dock = value; applyLayout(); },
            0, -1,
        );
        // 窗口变窄时必须重新夹一遍：否则用户在大窗口下拉好的比例会在小窗口里
        // 把面板顶出屏幕，而手柄本身也跟着消失、无法再拖回来。
        let pending = 0;
        window.addEventListener('resize', () => {
            if (pending) return;
            pending = window.requestAnimationFrame(() => {
                pending = 0;
                applyLayout();
            });
        });
    }

    function measureDock() {
        const dock = document.querySelector('.animation-dock');
        return dock ? Math.round(dock.getBoundingClientRect().height) : LAYOUT_LIMITS.dock[0];
    }

    // 基础模式：两个场景下拉。填了说话场景就是经典口型交替（运行时在两张图之间
    // 换 src），留空则不产出 talking_image、说话时继续播待机动画。这比一个
    // "模式"开关直观，也少一个会撒谎的状态字段。
    // 把基础设置表达成同一张节点图 —— 于是导出只有一条路径，基础和进阶不会互相漂移。
    // 待机用 seconds:null 表示演完整段；填 0 会被算成 1 帧，待机就成静图了。
    function basicGraph() {
        const model = window.NekoEmoteNodeGraph;
        const packModule = window.NekoEmotePngtuberPack;
        if (!model || !packModule) return null;
        const nodes = [model.makeNode('source.default', 60, 60, {}, 'basic-root')];
        const links = [];
        // 八个槽全部可达：以前只有待机/说话两行，另外六个情绪槽永远是空的，
        // 运行时 setEmotion 查到空串就直接 return false，等于"导出后没有表情"。
        packModule.STATES.forEach((entry, index) => {
            const sceneId = state.packSceneIds[entry.key] || '';
            const top = 60 + index * 92;
            const outId = 'out-' + index;
            // 说话位固定单帧定格在装配处强制（运行时靠换 <img> src 做口型交替，
            // 给它多帧 GIF 会被反复从头重载，永远播不动）。
            // 秒数不再由面板给：整段动画就是默认，挑片段去表演台的胶片带上拖
            const seconds = 0;
            if (!sceneId) {
                // 未另选场景 = 复用根节点外观；除待机外的空槽由运行时回落 idle
                if (!entry.required) return;
                nodes.push(model.makeNode('output.state', 380, top, { stateKey: entry.key, seconds }, outId));
                links.push({ from: 'basic-root', fromSocket: 'look', to: outId, toSocket: 'look' });
                return;
            }
            const sceneNodeId = 'scene-' + index;
            nodes.push(model.makeNode('source.scene', 60, top, { sceneId }, sceneNodeId));
            nodes.push(model.makeNode('output.state', 380, top, { stateKey: entry.key, seconds }, outId));
            links.push({ from: sceneNodeId, fromSocket: 'look', to: outId, toSocket: 'look' });
        });
        return { schemaVersion: 1, nodes: nodes.filter(Boolean), links };
    }

    function currentBakeGraph() {
        return state.packMode === 'graph' && state.nodeGraph ? state.nodeGraph : basicGraph();
    }

    function renderPackPanel() {
        const packModule = window.NekoEmotePngtuberPack;
        if (!packModule) return;
        const basicPanel = $('#pack-panel-basic');
        const graphPanel = $('#pack-panel-graph');
        const isGraph = state.packMode === 'graph';
        const scenes = getImportedScenes();
        const available = hasSpineRenderer() && scenes.length > 0;
        const validIds = new Set(scenes.map((scene) => scene.id));
        // 换包后旧 scene id 大概率不存在：丢掉，别等导出时烘出空场景。
        Object.keys(state.packSceneIds).forEach((key) => {
            if (!validIds.has(state.packSceneIds[key])) delete state.packSceneIds[key];
        });
        if (!state.packSceneIds.idle_image && validIds.has(state.selectedSceneId)) {
            state.packSceneIds.idle_image = state.selectedSceneId;
        }

        document.querySelectorAll('[data-pack-mode]').forEach((button) => {
            const active = (button.dataset.packMode === 'graph') === isGraph;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
        });
        if (basicPanel) basicPanel.hidden = isGraph || !available;
        if (graphPanel) graphPanel.hidden = !isGraph || !available;

        const rowsHost = $('#pack-basic-rows');
        if (rowsHost) {
            rowsHost.replaceChildren();
            packModule.STATES.forEach((entry) => {
                const row = document.createElement('label');
                row.className = 'select-row pack-basic-row';
                // 落点解析要按这个认出"是哪一格"；以前只读不写，拖上去等于没反应
                row.dataset.stateKey = entry.key;
                const caption = document.createElement('span');
                caption.textContent = t(entry.labelKey);
                const select = document.createElement('select');
                select.id = 'pack-scene-' + entry.key.replace(/_image$/, '');
                const blank = document.createElement('option');
                blank.value = '';
                blank.textContent = entry.required
                    ? t('emoteMaker.packSkipOption')
                    : t('emoteMaker.packBasicFollowRoot');
                select.appendChild(blank);
                scenes.forEach((scene) => {
                    const option = document.createElement('option');
                    option.value = scene.id;
                    option.textContent = scene.name || scene.animation?.id || scene.id;
                    select.appendChild(option);
                });
                select.value = state.packSceneIds[entry.key] || '';
                select.addEventListener('change', () => {
                    if (select.value) state.packSceneIds[entry.key] = select.value;
                    else delete state.packSceneIds[entry.key];
                    persistExportSettings();
                    renderPackPanel();
                });
                row.append(caption, select);
                rowsHost.appendChild(row);
            });
        }
        const nameInput = $('#pack-name');
        if (nameInput) {
            nameInput.value = state.packName || '';
            nameInput.placeholder = currentTemplateLabel() || t('emoteMaker.title');
        }
        const formatSelect = $('#pack-format');
        if (formatSelect) formatSelect.value = state.packFormat === 'layered' ? 'layered' : 'flat';
        const prioritySelect = $('#pack-priority');
        if (prioritySelect) {
            prioritySelect.value = state.packPriority === 'emotion' ? 'emotion' : 'talking';
        }

        ['#pack-export-btn', '#pack-import-btn'].forEach((selector) => {
            const button = $(selector);
            if (button && !packBusy) button.disabled = !available;
        });
    }



    // ── 形象包产出（统一路径）─────────────────────────────────────────────────
    // 基础和进阶都先编译成节点图，因此这里只有一条采集流程；两种输出格式
    // （平铺 GIF / 分层 PNG 雪碧图）只在最后的装配阶段分叉。
    function packFrameBudget(seconds, fps) {
        const limit = PACK_MAX_FRAMES;
        const requested = Number(seconds);
        if (!Number.isFinite(requested) || requested <= 0) return limit;
        return Math.max(1, Math.min(limit, Math.round(requested * fps)));
    }

    async function bakePackJobs(jobs, preview = {}) {
        return queueStageCapture(Symbol(), STAGE_CAPTURE_INTERACTIVE + 10, 'export',
            () => bakePackJobsNow(jobs, preview));
    }

    async function bakePackJobsNow(jobs, preview = {}) {
        const previewFps = Number(preview.fps) || 0;
        const previewEdge = Number(preview.edge) || 0;
        const packFormat = preview.format || state.packFormat;
        // 预览用的 job 会自带 fps/edge（表演台只要看得清，不需要导出级精度）；
        // 导出路径不传，仍取全局值。
        const edge = Math.min(previewEdge || state.exportResolution || PACK_MAX_EDGE, PACK_MAX_EDGE);
        const requestedFps = previewFps || state.exportFps || GIF_EXPORT_FPS;
        // GIF intervals between 15 and 20ms encode as 20ms. Sample flat packs
        // at no more than 50 FPS so this cannot lengthen their animation.
        const fps = packFormat === 'layered' ? requestedFps : Math.min(GIF_EXPORT_MAX_FPS, requestedFps);
        const sourceScenes = getImportedScenes();
        const results = [];
        const imageFrames = new Map();
        const resizedImageFrames = new WeakMap();
        let missingImages = 0;
        const previousSceneId = state.selectedSceneId;
        const previousAnimationId = state.animationId;
        const previousLook = {
            templateSkins: state.templateSkins,
            templateSlots: state.templateSlots,
            adjustments: state.adjustments,
        };
        const previousPlaying = state.animationPlaying;
        try {
            for (let index = 0; index < jobs.length; index += 1) {
                const job = jobs[index];
                // 图片统一按预览时间轴裁切：PNG 每张停留一个预览帧，
                // GIF 保留源时长，再按输出格式采样。
                const jobImageIds = (job.imageIds && job.imageIds.length)
                    ? job.imageIds
                    : (job.imageId ? [job.imageId] : []);
                if (jobImageIds.length) {
                    const sources = jobImageIds
                        .map((id) => stageUserImages.get(id))
                        .filter(Boolean);
                    if (!sources.length) {
                        // 真缺图（图被删了还绑着）不再无声：导出时说一声缺了几个
                        if (!previewFps) missingImages += 1;
                        continue;
                    }
                    if (!previewFps) setPackStatus(`${t('emoteMaker.packBakingProgress')} ${index + 1}/${jobs.length}`);
                    const decoded = [];
                    for (const image of sources) {
                        if (!imageFrames.has(image)) imageFrames.set(image, await framesFromStageImage(image, edge));
                        decoded.push(...imageFrames.get(image));
                    }
                    if (!decoded.length) {
                        if (!previewFps) missingImages += 1;
                        continue;
                    }
                    // 平铺包的说话位只定格选中片段的第一张：与场景侧的 still 规则同源
                    // （运行时换 src 重载多帧 GIF 的空窗问题），分层不受限。
                    const still = job.stateKey === 'talking_image' && packFormat !== 'layered';
                    const timed = decoded.some((frame) => frame.delayMs);
                    const timeline = trimStageImageTimeline(decoded, job.imageStartAt || 0, job.imageSeconds || 0);
                    let frames = still ? timeline.slice(0, 1) : timeline;
                    let imageFps = fps;
                    if (!still && (packFormat === 'layered' || frames.length > PACK_MAX_FRAMES)) {
                        frames = sampleStageImageTimeline(timeline, fps, PACK_MAX_FRAMES,
                            timed ? 1 : timeline.length);
                        imageFps = 1000 / frames[0].delayMs;
                    }
                    frames = resizeStageImageFrames(frames, edge, resizedImageFrames);
                    results.push({
                        stateKey: job.stateKey, frames, edge, fps: imageFps,
                        scale: job.scale || 1, rotation: job.rotation || 0,
                    });
                    continue;
                }
                const scene = sourceScenes.find((item) => item.id === job.sceneId);
                if (!scene) continue;
                if (!previewFps) setPackStatus(`${t('emoteMaker.packBakingProgress')} ${index + 1}/${jobs.length}`);
                await yieldToBrowser();
                applyLookToState(job.look);
                const frames = await captureGifFramesNow({
                    scene,
                    maxFrames: job.frames,
                    fps,
                    format: packFormat,
                    maxDimension: edge,
                    timeWindow: job.timeWindow,
                });
                if (!frames.length) continue;
                results.push({
                    stateKey: job.stateKey,
                    frames,
                    edge,
                    // The frame budget can thin the samples without shortening the clip.
                    fps: 1000 / frames[0].delayMs,
                    // 每个状态自己的大小/角度：只有分层模式会把它写进 manifest
                    scale: job.scale,
                    rotation: job.rotation,
                });
            }
        } finally {
            if (!previewFps) setPackStatus('');
            state.selectedSceneId = previousSceneId;
            state.animationId = previousAnimationId;
            Object.assign(state, previousLook);
            syncLiveCanvas({ autoplay: previousPlaying });
        }
        if (missingImages > 0) {
            showToast(interpolate(t('emoteMaker.stageImagesMissing'), { count: missingImages }));
        }
        return results;
    }

    function flatStates(baked) {
        const encoder = window.NekoEmoteGifEncoder;
        return baked.map((item) => ({
            key: item.stateKey,
            bytes: encoder.encode(item.frames, {
                width: item.edge,
                height: item.edge,
                fps: item.fps,
                colors: GIF_EXPORT_COLORS,
                transparent: true,
                loop: item.stateKey !== 'click_image',
                loopCount: 0,
            }),
        }));
    }

    async function layeredStates(baked, displayName) {
        const sheet = window.NekoEmoteSheet;
        const layeredModule = window.NekoEmotePngtuberLayered;
        const packModule = window.NekoEmotePngtuberPack;
        const states = [];
        const extras = [];
        for (const item of baked) {
            // eslint-disable-next-line no-await-in-loop
            const composed = sheet.composeSheetCanvas(item.frames, (w, h) => {
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                return canvas;
            });
            // eslint-disable-next-line no-await-in-loop
            const sheetBytes = await sheet.canvasToPngBytes(composed.canvas);
            const path = `layers/${states.length}-${item.stateKey}.png`;
            extras.push({ path, data: sheetBytes });
            states.push({
                stateKey: item.stateKey,
                // 格子总数（含补齐的空格）和真实帧数要分开传：贴图尺寸决定网格，
                // frameCount 决定动画只播到有内容的那一格
                frames: composed.hframes * composed.vframes,
                frameCount: item.frames.length,
                // 写"合成器实际用的格子尺寸"，不是"我要求的导出边长"：metadata 的
                // frame_width 必须和表上的格子对得上，差一个像素就是整表错位闪烁
                width: composed.frameWidth,
                height: composed.frameHeight,
                sheetWidth: composed.canvas.width,
                sheetHeight: composed.canvas.height,
                fps: item.fps,
                pngBytes: sheetBytes,
                x: 0,
                y: 0,
                // 表演台上直接拖出来的大小/角度；平铺模式没有这两个字段的位置，会被忽略
                scale: item.scale,
                rotation: item.rotation,
            });
        }
        const built = layeredModule.buildLayeredPackage({
            // 画布与第一张表的格子同尺寸，否则合成画面与包里的表对不上
            canvasWidth: states[0].width,
            canvasHeight: states[0].height,
            states,
        });
        // 分层包也必须带一张平铺回落图：服务端要求 idle_image 非空且真实存在。
        const idleJob = baked.find((item) => item.stateKey === 'idle_image') || baked[0];
        const fallbackBytes = await sheet.canvasToPngBytes(
            sheet.firstFrameCanvas(idleJob.frames[0]),
        );
        const assembled = packModule.buildPackageFiles({
            displayName,
            description: `${t('emoteMaker.title')} · ${displayName}`,
            placement: packPlacement(),
            statePriority: state.packPriority,
            // 回落图只给 idle：其余槽位空着，运行时会回落到 idle 而不是 404
            states: [{ key: 'idle_image', file: 'idle.png', bytes: fallbackBytes }],
            layered: {
                adapter: built.adapter,
                metadataPath: built.metadataPath,
                files: built.files.filter((file) => file.path === 'metadata.json')
                    .concat(extras.map((file) => ({ path: file.path, data: file.data }))),
            },
        });
        return { assembled, metadata: built.metadata };
    }

    async function buildPngtuberPackage() {
        const packFormat = state.packFormat;
        const packModule = window.NekoEmotePngtuberPack;
        if (!packModule || !window.NekoEmoteGifEncoder || !hasSpineRenderer()) {
            showToast(t('emoteMaker.exportGifUnavailable'));
            return null;
        }
        if (packBusy) return null;
        if (packFormat === 'layered' && !window.NekoEmotePngtuberLayered) {
            showToast(t('emoteMaker.packLayeredUnavailable'));
            return null;
        }
        // 图片槽位的字节在副本里，内存清单只在开表演台时恢复。刷新后直接导出
        // 而没开过抽屉的话，清单是空的——所有图片槽会被烘包静默跳过。
        // 先起恢复不 await：setPackBusy 之前不能有让渡点，否则双击会导出两份；
        // 真正等它落在烘包前。
        const stageImagesReady = restoreStageImages();
        const graph = currentBakeGraph();
        const model = window.NekoEmoteNodeGraph;
        if (!graph || !model) {
            showToast(interpolate(t('emoteMaker.packIdleRequired'), { state: t('emoteMaker.packStateIdle') }));
            return null;
        }
        const evaluated = model.evaluate(graph, currentLookDefaults());
        if (evaluated.cycle) {
            showToast(t('emoteMaker.nodeCycleWarning'));
            return null;
        }
        // 只是点过去看过的格子会在图里留下"跟随默认外观"的输出。它们不该进包——
        // 否则烘出来的是当前场景的图，而用户从没给它选过动作。
        const plan = model.planOutputs(
            graph,
            evaluated.outputs,
            packModule.STATES.map((entry) => entry.key),
            ['idle_image'],
        );
        const sceneIds = new Set(getImportedScenes().map((scene) => scene.id));
        const usable = plan.kept.filter((output) =>
            sceneIds.has(output.look.sceneId)
            || (output.imageId && output.imageId.length)
            || (output.imageIds && output.imageIds.length));
        if (plan.dropped.length) {
            const reasons = new Set(plan.dropped.map((item) => item.reason));
            showToast(`${t('emoteMaker.packPlanSkipped')} (${plan.dropped.length})${
                reasons.has('unassigned') ? ` · ${t('emoteMaker.packPlanUnassigned')}` : ''}${
                reasons.has('duplicate') ? ` · ${t('emoteMaker.packPlanDuplicate')}` : ''}`);
        }
        if (!usable.length) {
            showToast(t('emoteMaker.nodeNoOutput'));
            return null;
        }
        if (!usable.some((output) => output.stateKey === 'idle_image')) {
            showToast(interpolate(t('emoteMaker.packIdleRequired'), { state: t('emoteMaker.packStateIdle') }));
            return null;
        }

        const fps = state.exportFps || GIF_EXPORT_FPS;
        // 优先用用户起的名字；没起才跟随模板名，否则每次导出都是同一个目录
        const displayName = String(state.packName || '').trim()
            || currentTemplateLabel()
            || templatePackage?.model?.displayName
            || t('emoteMaker.title');
        const jobs = usable.map((output) => {
            const imageIds = (output.imageIds && output.imageIds.length)
                ? output.imageIds
                : (output.imageId ? [output.imageId] : []);
            if (imageIds.length) {
                // 由图片源的时间轴决定时长，PNG 和 GIF 都先裁切再采样。
                return {
                    stateKey: output.stateKey,
                    imageIds,
                    imageStartAt: output.startAt,
                    imageSeconds: output.seconds,
                    scale: output.scale,
                    rotation: output.rotation,
                };
            }
            const scene = getImportedScenes().find((item) => item.id === output.look.sceneId);
            const duration = Math.max(0.05, Number(getImportedAnimationDuration(scene?.animation?.id)) || 0.05);
            const from = Math.min(Math.max(0, Number(output.startAt) || 0), duration);
            const wanted = Number(output.seconds) || 0;
            // 平铺包的说话位只烘一帧：运行时按口型节奏在 idle/talking 两张图之间来回切，
            // 图自己再动一次就成了双重动画。分层包不一样 —— 说话是去占状态槽，画面完全
            // 由这一格的雪碧图自己演，定格成一张就等于用户绑的动作没演。
            const still = output.stateKey === 'talking_image' && packFormat !== 'layered';
            const to = still
                ? Math.min(duration, from + 1 / fps)
                : (wanted > 0 ? Math.min(duration, from + wanted) : duration);
            const timeWindow = { from, to: Math.max(to, from + 1 / fps) };
            return {
                stateKey: output.stateKey,
                sceneId: output.look.sceneId,
                look: output.look,
                frames: still ? 1 : packFrameBudget(timeWindow.to - timeWindow.from, fps),
                timeWindow,
                scale: output.scale,
                rotation: output.rotation,
            };
        });

        setPackBusy(true);
        try {
            await stageImagesReady;
            const baked = await bakePackJobs(jobs, { format: packFormat });
            if (!baked.length) {
                showToast(t('emoteMaker.packZipFailed'));
                return null;
            }
            if (packFormat === 'layered') {
                baked[0].displayName = displayName;
                baked[0].description = `${t('emoteMaker.title')} · ${displayName}`;
                const result = await layeredStates(baked, displayName);
                return result.assembled;
            }
            return packModule.buildPackageFiles({
                displayName,
                description: `${t('emoteMaker.title')} · ${displayName}`,
                states: flatStates(baked),
                placement: packPlacement(),
                statePriority: state.packPriority,
            });
        } catch (error) {
            console.warn('[emote-maker] 形象包生成失败', error);
            showToast(t('emoteMaker.packZipFailed'));
            return null;
        } finally {
            setPackBusy(false);
            renderAll();
        }
    }

    function renderAll() {
        const hasEditorContent = Boolean(templatePackage);
        const nativePreview = hasSpineRenderer();
        document.body.classList.toggle('editor-empty', !hasEditorContent);
        document.body.classList.toggle('editor-has-template', Boolean(templatePackage));
        if (inspectorEmptyState) inspectorEmptyState.hidden = hasEditorContent;
        if (animationDock) animationDock.hidden = !hasEditorContent;
        if (templateRuntimeBadge) {
            // 徽章只报忧：跑不起预览才现身。一切正常时它就是第三个"模板已读取"。
            templateRuntimeBadge.hidden = !templatePackage || hasSpineRenderer();
            if (!templateRuntimeBadge.hidden) {
                templateRuntimeBadge.textContent = t('emoteMaker.templateNoRuntime');
                templateRuntimeBadge.dataset.tone = 'warning';
            }
        }
        bindTipKeys();
        renderLibraryControls();
        renderInspectorTabs();
        syncExportControls();
        renderPackPanel();
        renderBatchExportGroupSelect();
        renderGroupBar();
        renderAssetGrid();
        renderPartGroups();
        renderColorMode();
        renderAdjustments();
        renderAnimations();
        applyZoom();
        const exportButton = $('#export-png-btn');
        if (exportButton) exportButton.disabled = !hasSpineRenderer();
        const exportLabel = exportButton?.querySelector('span');
        if (exportLabel) {
            // 出什么格式由上面的「格式」下拉说了算，按钮只管说"导哪一张"：现在是两颗
            // 半宽按钮并排，再把 静态 PNG / 动画 GIF 印上去只会挤。渲染不了时留着原因，
            // 别让按钮哑掉而看不出为什么。
            exportLabel.textContent = (templatePackage && !hasSpineRenderer())
                ? t('emoteMaker.templateNoRuntime')
                : t('emoteMaker.exportCurrent');
        }
        if (templateCanvasStatus) {
            const statusText = templatePackage
                ? t('emoteMaker.templateCapabilityWarning')
                : t('emoteMaker.selectTemplateHint');
            if (templateCanvasStatusText) {
                templateCanvasStatusText.textContent = statusText;
            } else {
                templateCanvasStatus.textContent = statusText;
            }
            if (canvasImportButton) canvasImportButton.hidden = hasEditorContent;
            templateCanvasStatus.hidden = nativePreview;
        }
        updateHistoryButtons();
        void renderCanvas();
    }

    function showToast(message) {
        if (!toast) return;
        toast.textContent = message;
        toast.classList.add('is-visible');
        if (toastTimer) window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2200);
    }

    function presentTemplateWarning(warning) {
        const text = String(warning || '').trim();
        if (!text) return t('emoteMaker.templateCompatibilityWarning');
        // Keep implementation/provider terminology out of the normal editor
        // surface. Detailed diagnostics remain available in the package data
        // and developer tools, while users get one neutral notice.
        const technicalPattern = /(\bcharacter\b|\bspine\b|atlas(?:\.txt)?|skin|角色数据|模型.*不一致)/i;
        return technicalPattern.test(text)
            ? t('emoteMaker.templateCompatibilityWarning')
            : text;
    }



    function yieldToBrowser() {
        return new Promise((resolve) => {
            if (typeof window.requestAnimationFrame === 'function') {
                window.requestAnimationFrame(() => resolve());
            } else {
                window.setTimeout(resolve, 0);
            }
        });
    }

    // 缩放唯一出口：GIF 逐帧取图和 PNG 导出都走这里，避免两份缩放实现漂移。
    function downscaleCanvas(source, maxDimension) {
        if (!source || typeof source.getContext !== 'function') {
            throw new Error('当前画布不可读取');
        }
        const sourceWidth = Math.max(1, Math.round(Number(source.width) || 0));
        const sourceHeight = Math.max(1, Math.round(Number(source.height) || 0));
        const requested = Math.max(1, Math.round(Number(maxDimension) || sourceWidth));
        const scale = Math.min(1, requested / Math.max(sourceWidth, sourceHeight));
        const width = Math.max(1, Math.round(sourceWidth * scale));
        const height = Math.max(1, Math.round(sourceHeight * scale));
        if (width === sourceWidth && height === sourceHeight) {
            return { canvas: source, width, height };
        }
        const target = document.createElement('canvas');
        target.width = width;
        target.height = height;
        const context = target.getContext('2d');
        if (!context) throw new Error('当前画布不支持读取像素');
        context.clearRect(0, 0, width, height);
        // 1024→128 这种大跨度缩放，默认插值会出锯齿并丢细节；与 renderCanvas 的
        // 2D 路径保持一致用 high。
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = 'high';
        context.drawImage(source, 0, 0, width, height);
        return { canvas: target, width, height };
    }

    function captureCanvasFrame(source, maxDimension) {
        const fitted = downscaleCanvas(source, maxDimension);
        const context = fitted.canvas.getContext('2d', { willReadFrequently: true });
        if (!context || typeof context.getImageData !== 'function') {
            throw new Error('当前画布不支持读取像素');
        }
        const image = context.getImageData(0, 0, fitted.width, fitted.height);
        return { width: fitted.width, height: fitted.height, data: new Uint8ClampedArray(image.data) };
    }

    function captureCurrentEditorFrame(maxDimension) {
        const edge = Math.max(1, Number(maxDimension) || state.exportResolution || GIF_EXPORT_WIDTH);
        if (hasSpineRenderer()) {
            // Use the same composition as PNG export to include placed accessories.
            const composite = nativeCompositeCanvas();
            if (composite) return captureCanvasFrame(composite, edge);
            if (typeof spineRenderer.captureFrame === 'function') {
                return spineRenderer.captureFrame({ maxDimension: edge, render: false });
            }
            throw new Error('当前 Spine 运行时不支持帧读取');
        }
        return captureCanvasFrame(canvas, edge);
    }

    // options 供两条调用路径共用同一份逐帧循环与恢复逻辑：
    //   · GIF 导出用默认值（烘当前选中场景）。
    //   · PNGTuber 形象包传入 {scene, maxFrames, fps}，烘调用方指定的某个场景。
    // 传入 scene 时本函数只负责套用并烘焙它，不还原选中态 —— 那由调用方处理，
    // 因为它还要连带刷新一次界面。
    async function captureGifFrames(options = {}) {
        return queueStageCapture(Symbol(), STAGE_CAPTURE_INTERACTIVE + 10, 'export',
            () => captureGifFramesNow(options));
    }

    async function captureGifFramesNow(options = {}) {
        const scene = options.scene || null;
        const animationId = scene ? (scene.animation?.id || state.animationId) : state.animationId;
        const duration = Math.max(
            0.05,
            Number(scene
                ? getImportedAnimationDuration(animationId)
                : getCurrentAnimation()?.duration) || 0,
        );
        const sceneSpeed = templatePackage
            ? Math.max(0.05, Number((scene || getSelectedScene())?.animation?.speed) || 1)
            : 1;
        const playbackSpeed = Math.max(0.05, Number(state.animationSpeed) || 1) * sceneSpeed;
        const maxFrames = Math.max(1, Number(options.maxFrames) || GIF_EXPORT_MAX_FRAMES);
        const requestedFps = Number(options.fps) || state.exportFps || GIF_EXPORT_FPS;
        const fps = options.format === 'layered' ? requestedFps : Math.min(GIF_EXPORT_MAX_FPS, requestedFps);
        // 允许 1 帧：形象包的"口型交替"就是要一张定格图。动画导出仍保持最少 2 帧。
        const frameFloor = maxFrames === 1 ? 1 : 2;
        // timeWindow 把采集窗口从"整段动画"收窄到用户挑的那一段；不给就是整段，
        // 于是 GIF/PNG 导出这些老调用方行为完全不变。
        const windowFrom = Math.max(0, Math.min(duration, Number(options.timeWindow?.from) || 0));
        const windowTo = options.timeWindow?.to == null
            ? duration
            : Math.max(windowFrom + 0.01, Math.min(duration, Number(options.timeWindow.to) || duration));
        const span = windowTo - windowFrom;
        // Ignore floating-point noise at exact frame boundaries (e.g. 0.3-0.1).
        const frameCount = Math.max(frameFloor, Math.min(maxFrames, Math.floor(span * fps / playbackSpeed + 1e-9)));
        const delayMs = Math.max(10, (span * 1000) / frameCount / playbackSpeed);
        const frames = [];
        const wasPlaying = Boolean(state.animationPlaying);
        const previousProgress = Number(state.animationProgress) || 0;
        const previousTime = previousProgress > 0
            ? duration * Math.max(0, Math.min(100, previousProgress)) / 100
            : Number((scene || getSelectedScene())?.animation?.time) || 0;

        state.animationPlaying = false;
        if (templatePackage && hasSpineRenderer()) {
            stopSpineAnimationLoop();
            spineRenderer.pause();
        }

        try {
            capturingFrames = true;
            if (colorDraft) applyNativeCharacterState({ render: false });
            if (scene) {
                state.selectedSceneId = scene.id;
                state.animationId = animationId;
                state.animationProgress = 0;
                // 场景加载失败时停止导出，避免把当前画面写进另一个状态。
                if (!syncNativeScene(scene, { autoplay: false })) {
                    throw new Error('scene_unavailable');
                }
            }
            let loopOffset = 0;
            if (frameCount > 1 && windowFrom === 0 && windowTo === duration
                    && hasSpineRenderer() && spineRenderer.hasPhysicsConstraints()) {
                // Preview carries physics across the seam. Skip the reset pose and sample a settled loop.
                spineRenderer.selectAnimation(animationId, { loop: true, render: false });
                const cycles = Math.min(2, Math.max(1, Math.floor(8 / duration)));
                loopOffset = cycles * duration + 1e-7;
                for (let warmupTime = 2; warmupTime < loopOffset; warmupTime += 2) {
                    spineRenderer.setTime(warmupTime, { render: false });
                    await yieldToBrowser();
                }
            }
            for (let index = 0; index < frameCount; index += 1) {
                // 采集时间落在 timeWindow 里，进度条与 setTime 都按同一秒数走，
                // 否则旧渲染器（靠 animationProgress 反算秒）会和 Spine 路径烘出不同的帧。
                const at = windowFrom + (span * index) / frameCount;
                const progress = (at / duration) * 100;
                state.animationProgress = progress;
                if (templatePackage && hasSpineRenderer()) {
                    spineRenderer.setTime(loopOffset + at, { render: false });
                    spineRenderer.render();
                } else {
                    // The legacy canvas renderer is asynchronous because it
                    // may load a selected PNG layer.
                    await renderCanvas();
                }
                const frame = captureCurrentEditorFrame(options.maxDimension);
                frames.push({ ...frame, delayMs });
                // Let the browser paint between readbacks. This keeps the
                // editor responsive for 60–90 frame exports.
                if (index % 2 === 1) await yieldToBrowser();
            }
            return frames;
        } finally {
            captureLook = null;
            capturingFrames = false;
            if (colorDraft) applyNativeCharacterState({ render: false });
            state.animationProgress = previousProgress;
            if (templatePackage && hasSpineRenderer()) {
                spineRenderer.setTime(previousTime, { render: false });
                spineRenderer.render();
                renderNativeOverlays();
                if (wasPlaying) {
                    spineRenderer.play();
                    state.animationPlaying = true;
                    startSpineAnimationLoop();
                } else {
                    spineRenderer.pause();
                    state.animationPlaying = false;
                }
            } else {
                state.animationPlaying = false;
            }
            updateAnimationControls();
            void renderCanvas();
        }
    }

    function exportFilename(extension) {
        const value = state.animationId || 'empty';
        const safe = String(value)
            .replace(/[\\/:*?"<>|]+/g, '-')
            .replace(/\s+/g, '-')
            .replace(/^-+|-+$/g, '')
            .slice(0, 96) || 'empty';
        return `neko-emote-${safe}.${extension}`;
    }

    function gifByteLength(value) {
        if (value && Number.isFinite(Number(value.size))) return Number(value.size);
        if (value && Number.isFinite(Number(value.byteLength))) return Number(value.byteLength);
        if (value && Number.isFinite(Number(value.length))) return Number(value.length);
        return Number.POSITIVE_INFINITY;
    }

    function compactGifFrames(frames, stride) {
        const step = Math.max(1, Math.round(Number(stride) || 1));
        if (step === 1) return frames;
        const compacted = [];
        for (let index = 0; index < frames.length; index += step) {
            const frame = frames[index];
            const delayMs = frames.slice(index, index + step)
                .reduce((total, item) => total + (Number(item.delayMs) || 10), 0);
            compacted.push({
                ...frame,
                delayMs,
            });
        }
        return compacted;
    }

    function encodeGifWithinBudget(frames, encoder, transparent) {
        const attempts = [];
        // 阶梯从用户选的尺寸推导：主档不缩画布、只抽帧；还不够再逐级缩小。
        // 原来写死的 240 → 200/160/128 只在"用户想要 240"时才成立。
        const requested = Math.max(1, Math.round(Number(state.exportResolution) || GIF_EXPORT_WIDTH));
        const tiers = [requested];
        [0.83, 0.67, 0.5].forEach((factor) => {
            const size = Math.max(64, Math.round(requested * factor / 8) * 8);
            if (!tiers.includes(size)) tiers.push(size);
        });
        [1, 2, 3, 4, 5, 6, 8].forEach((stride) => {
            attempts.push({
                frames: compactGifFrames(frames, stride),
                width: tiers[0],
                height: tiers[0],
            });
        });
        tiers.slice(1).forEach((size) => {
            [1, 2, 4].forEach((stride) => {
                attempts.push({
                    frames: compactGifFrames(frames, stride),
                    width: size,
                    height: size,
                });
            });
        });

        // 从完整帧列表采样一次，16 次重试共用同一张表：否则"超预算就缩小尺寸"的
        // 重试会让同一个动画在不同 attempt 之间换色。降采样是最近邻、只取已有像素，
        // 所以对更小的 attempt 尺寸这张表依然有效。
        const sampled = typeof encoder.buildPaletteFromFrames === 'function'
            ? encoder.buildPaletteFromFrames(frames, { colors: GIF_EXPORT_COLORS })
            : null;

        let best = null;
        for (const attempt of attempts) {
            const encoded = encoder.toBlob(attempt.frames, {
                width: attempt.width,
                height: attempt.height,
                colors: GIF_EXPORT_COLORS,
                transparent,
                loopCount: 0,
                ...(sampled ? { palette: sampled } : {}),
            });
            const size = gifByteLength(encoded);
            const candidate = { encoded, size, ...attempt };
            if (!best || size < best.size) best = candidate;
            if (size <= GIF_EXPORT_MAX_BYTES) return candidate;
        }
        return best;
    }

    const EXPORT_STORAGE_KEY = 'neko-emote-maker-export';
    const EXPORT_RESOLUTIONS = Object.freeze([128, 240, 320, 480, 512, 1024]);
    const EXPORT_FPS_VALUES = Object.freeze([8, 10, 12, 15, 20, 24, 30, 48, 60]);
    // GIF 的编码耗时按像素数平方增长（实测 120 帧 240² ≈ 0.48s，折算 1024² ≈ 8.8s），
    // 而且 1MB 预算下大尺寸一定会被降质阶梯抽帧/缩小，所以 GIF 封顶 512。
    const GIF_MAX_RESOLUTION = 512;

    function loadExportSettings() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(EXPORT_STORAGE_KEY) || 'null');
        } catch (_) {
            saved = null;
        }
        if (!saved || typeof saved !== 'object') return;
        // localStorage 的内容算外部输入，逐项校验后再用，不直接相信形状。
        if (saved.exportFormat === 'gif' || saved.exportFormat === 'png') {
            state.exportFormat = saved.exportFormat;
        }
        if (typeof saved.exportTransparent === 'boolean') {
            state.exportTransparent = saved.exportTransparent;
        }
        if (EXPORT_RESOLUTIONS.includes(Number(saved.exportResolution))) {
            state.exportResolution = Number(saved.exportResolution);
        }
        if (EXPORT_FPS_VALUES.includes(Number(saved.exportFps))) {
            state.exportFps = Number(saved.exportFps);
        }
        // 落位的合法范围只在 NekoEmotePngtuberPack 里定义一份；这里复用它的钳制，
        // 免得两边各写一套 0.1/5/锚点枚举然后悄悄漂移。
        const packModule = window.NekoEmotePngtuberPack;
        if (packModule && typeof packModule.normalizePlacement === 'function') {
            const safe = packModule.normalizePlacement({
                scale: saved.packScale,
                offsetX: saved.packOffsetX,
                offsetY: saved.packOffsetY,
                mirror: saved.packMirror,
            });
            state.packScale = safe.scale;
            state.packOffsetX = safe.offset_x;
            state.packOffsetY = safe.offset_y;
            state.packMirror = safe.mirror;
        }
        if (saved.packFormat === 'layered' || saved.packFormat === 'flat') {
            state.packFormat = saved.packFormat;
        }
        if (typeof saved.packName === 'string') state.packName = saved.packName;
        if (saved.packPriority === 'emotion' || saved.packPriority === 'talking') {
            state.packPriority = saved.packPriority;
        }
        // 场景映射要按当前包重新过滤：跨包残留的 scene id 会让导出烘出空场景
        if (saved.packSceneIds && typeof saved.packSceneIds === 'object') {
            state.packSceneIds = { ...saved.packSceneIds };
        }
    }

    function persistExportSettings() {
        persistTemplateToLibrary();
        try {
            localStorage.setItem(EXPORT_STORAGE_KEY, JSON.stringify({
                exportFormat: state.exportFormat,
                exportTransparent: state.exportTransparent,
                exportResolution: state.exportResolution,
                exportFps: state.exportFps,
                packScale: state.packScale,
                packOffsetX: state.packOffsetX,
                packOffsetY: state.packOffsetY,
                packMirror: state.packMirror,
                packFormat: state.packFormat,
                packName: state.packName,
                packPriority: state.packPriority,
                packSceneIds: { ...(state.packSceneIds || {}) },
            }));
        } catch (_) {
            // 存不下只影响"记住选择"，不影响导出本身。
        }
    }

    function packPlacement() {
        return {
            scale: state.packScale,
            offsetX: state.packOffsetX,
            offsetY: state.packOffsetY,
            mirror: state.packMirror,
        };
    }

    function syncExportControls() {
        const isGif = state.exportFormat === 'gif';
        if (isGif && state.exportResolution > GIF_MAX_RESOLUTION) {
            state.exportResolution = GIF_MAX_RESOLUTION;
        }
        const format = $('#export-format');
        if (format) format.value = state.exportFormat;
        const resolution = $('#export-resolution');
        const resolutionMax = $('#export-resolution-1024');
        if (resolutionMax) resolutionMax.hidden = isGif;
        if (resolution) resolution.value = String(state.exportResolution);
        // 帧率刻意不再按格式禁用：它同时驱动动画 GIF 和形象包的每个状态，
        // 一个"看不见却在影响另一条路径"的置灰控件比常开的更容易误判。
        const fps = $('#export-fps');
        if (fps) fps.value = String(state.exportFps);
        const transparent = $('#transparent-background');
        if (transparent) transparent.checked = state.exportTransparent !== false;
        const scale = $('#pack-scale');
        if (scale) scale.value = String(state.packScale);
        const scaleValue = $('#pack-scale-value');
        if (scaleValue) scaleValue.textContent = `${Math.round(state.packScale * 100)}%`;
        const offsetX = $('#pack-offset-x');
        if (offsetX) offsetX.value = String(state.packOffsetX);
        const offsetXValue = $('#pack-offset-x-value');
        if (offsetXValue) offsetXValue.textContent = `${state.packOffsetX}px`;
        const offsetY = $('#pack-offset-y');
        if (offsetY) offsetY.value = String(state.packOffsetY);
        const offsetYValue = $('#pack-offset-y-value');
        if (offsetYValue) offsetYValue.textContent = `${state.packOffsetY}px`;
        const mirror = $('#pack-mirror');
        if (mirror) mirror.checked = state.packMirror === true;
    }

    // 这个页面跑在 Electron 托管子窗口里，那里的 File System Access 只能弹框、不能写：
    // showSaveFilePicker 会正常返回 handle（并且把目标文件创建/截断成 0 字节），紧接着
    // createWritable() 抛 NotAllowedError "not allowed by the user agent or the platform
    // in the current context"，用户手里剩下的就是一个打不开的空文件。
    // 所以这里不尝试 picker —— 一律用 <a download>，让 Electron 的原生保存框来选位置，
    // 一次交互、一个有效文件。
    // PNG 和 GIF 共用同一个分辨率选择。缩放失败时退回原尺寸，而不是让整个导出失败 ——
    // 拿到偏大的图总比拼不出来好。
    function downscaleForPng(source) {
        try {
            return downscaleCanvas(source, state.exportResolution).canvas;
        } catch (_) {
            return source;
        }
    }

    function canvasToPngBlob(source) {
        return new Promise((resolve) => {
            try {
                source.toBlob((blob) => resolve(blob || null), 'image/png');
            } catch (_) {
                // 被污染（tainted）的画布会在这里同步抛 SecurityError。
                resolve(null);
            }
        });
    }

    async function downloadPng() {
        return queueStageCapture(Symbol(), STAGE_CAPTURE_INTERACTIVE + 10, 'export', downloadPngNow);
    }

    async function downloadPngNow() {
        // 同步快照：nativeCompositeCanvas 把 WebGL 画布的当下像素拷进新的 2D canvas，
        // 因此编码期间动画继续跑也不会改变导出的那一帧。
        const highlighted = hasSpineRenderer() && colorPartHighlight();
        const draft = colorDraft;
        if (draft) {
            colorDraft = null;
            applyNativeCharacterState({ render: false });
        }
        if ((highlighted || draft) && hasSpineRenderer()) spineRenderer.render({ highlight: false });
        const outputCanvas = hasSpineRenderer() ? (nativeCompositeCanvas() || spineCanvas) : canvas;
        colorDraft = draft;
        if (draft) applyNativeCharacterState({ render: true });
        else if (highlighted) spineRenderer.render();
        if (!outputCanvas) return;
        if (!hasSpineRenderer()) {
            showToast(t('emoteMaker.noTemplates'));
            return;
        }
        const blob = await canvasToPngBlob(downscaleForPng(outputCanvas));
        if (!blob || !blob.size) {
            showToast(t('emoteMaker.exportPngFailed'));
            return;
        }
        fallbackDownload(blob, exportFilename('png'), 'emoteMaker.exportSuccess', 'emoteMaker.exportPngFailed');
    }

    async function downloadGif() {
        const encoder = window.NekoEmoteGifEncoder;
        if (!encoder || typeof encoder.toBlob !== 'function' || typeof Blob !== 'function') {
            showToast(t('emoteMaker.exportGifUnavailable'));
            return;
        }
        if (!hasSpineRenderer()) {
            showToast(t('emoteMaker.exportGifUnavailable'));
            return;
        }
        const exportButton = $('#export-png-btn');
        if (exportButton) exportButton.disabled = true;
        showToast(t('emoteMaker.exportGifProgress'));
        try {
            const frames = await captureGifFrames();
            const encodedResult = encodeGifWithinBudget(
                frames,
                encoder,
                state.exportTransparent !== false,
            );
            if (!encodedResult?.encoded) throw new Error('GIF 编码器没有返回数据');
            const encoded = encodedResult.encoded;
            const blob = typeof Blob === 'function' && encoded instanceof Blob
                ? encoded
                : new Blob([encoded], { type: 'image/gif' });
            if (!blob || !blob.size) throw new Error('GIF 编码结果为空');
            fallbackDownload(
                blob, exportFilename('gif'),
                'emoteMaker.exportGifSuccess', 'emoteMaker.exportGifFailed',
            );
        } catch (error) {
            showToast(t('emoteMaker.exportGifFailed'));
        } finally {
            if (exportButton) exportButton.disabled = false;
            renderAll();
        }
    }

    // ── 生成 PNGTuber 形象包 ────────────────────────────────────────────────
    // 每个状态烘一条 GIF：PNGTuber 的平铺模式直接接受 .gif
    // （main_routers/pngtuber_router.py:24 的 PNGTUBER_EXTENSIONS），运行时是 DOM
    // <img> 换 src、由浏览器自己播放 GIF。所以不需要分层雪碧图，也不用碰被 33 个
    // 静态契约测试钉住的 static/pngtuber-core.js。
    // 尺寸和帧数独立于表情导出的预算：桌面头像用不到 1024（PNGTuber 画布上限就是
    // 1024），而 8 个状态的帧数直接决定包体积。
    const PACK_MAX_EDGE = 512;
    const PACK_MAX_FRAMES = 120; // Four seconds at 30 FPS.
    const GROUP_EXPORT_MAX_FRAMES = 60;
    let packBusy = false;
    let unlockPackEditing = null;
    let stateStageSession = null;
    // 表演台的用户图片导入入口：input 常驻页面，表演台的"导入图片"按钮只负责叫它
    const stageImageInput = document.createElement('input');
    stageImageInput.type = 'file';
    stageImageInput.accept = '.png,.gif,image/png,image/gif';
    stageImageInput.multiple = true;
    stageImageInput.hidden = true;
    stageImageInput.addEventListener('change', () => {
        void importStageImages(stageImageInput.files);
        stageImageInput.value = '';
    });
    document.body.appendChild(stageImageInput);

    function animationIsPlayable(id) {
        if (!hasSpineRenderer()) return false;
        const names = spineRenderer.animationNames ? spineRenderer.animationNames() : [];
        return names.map(String).includes(String(id || ''));
    }

    /** 导出进度：状态行与摘要各写一份 —— 那块可能被收起，收起时摘要是唯一看得见的位置。 */
    function setGroupExportStatus(message) {
        const el = $('#group-export-status');
        if (el) el.textContent = message || '';
        const summary = $('#group-summary-state');
        if (summary && message) summary.textContent = message;
    }

    function setPackStatus(message) {
        const el = $('#pack-status');
        if (el) el.textContent = message || '';
    }

    function setPackBusy(busy) {
        packBusy = Boolean(busy);
        if (packBusy && !unlockPackEditing) unlockPackEditing = lockExportEditing();
        if (!packBusy && unlockPackEditing) {
            unlockPackEditing();
            unlockPackEditing = null;
        }
        // 烘一批的时候把单张导出按住：并发跑两次只会互相抢同一个渲染上下文。
        // 「批量导出」那颗不在此列 —— 跑起来时它就是停止按钮，按住就停不下来了。
        ['#pack-export-btn', '#pack-import-btn', '#export-png-btn'].forEach((selector) => {
            const button = $(selector);
            if (button) button.disabled = packBusy || Boolean(exportEditingLock);
        });
        const stop = $('#export-group-open-btn');
        if (stop && (groupExportBusy || exportEditingLock)) stop.disabled = !groupExportBusy;
    }




    // 图算出的每个输出节点 = 一张状态图。look 写回 state，让现有的场景套用与
    // overlay 渲染原样画出来，而不是再维护第二条渲染路径。



    async function downloadPngtuberPack() {
        const built = await buildPngtuberPackage();
        if (!built) return;
        try {
            const zip = window.NekoEmotePngtuberPack.packToZip(built.files);
            fallbackDownload(
                new Blob([zip], { type: 'application/zip' }),
                `${built.folder}.zip`,
                'emoteMaker.packZipSuccess',
                'emoteMaker.packZipFailed',
            );
        } catch (_) {
            showToast(t('emoteMaker.packZipFailed'));
        }
    }

    async function importPngtuberPack() {
        const built = await buildPngtuberPackage();
        if (!built) return;
        const packModule = window.NekoEmotePngtuberPack;
        try {
            // 一个文件一个 part、文件名带相对路径 —— 与模型管理选择文件夹时发出的
            // 请求完全同形，所以复用它的全部校验、限额、不覆盖和原子发布逻辑。
            const response = await fetch(packModule.uploadUrl(), {
                method: 'POST',
                body: packModule.appendMultipart(built.files),
                credentials: 'same-origin',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) {
                const reason = payload.error || payload.detail || '';
                showToast(reason
                    ? `${t('emoteMaker.packImportFailed')}：${reason}`
                    : t('emoteMaker.packImportFailed'));
                return;
            }
            showToast(t('emoteMaker.packImportSuccess'));
        } catch (_) {
            showToast(t('emoteMaker.packImportFailed'));
        }
    }

    function fallbackDownload(
        blob,
        filename,
        successKey = 'emoteMaker.exportSuccess',
        failureKey = 'emoteMaker.exportPngFailed',
    ) {
        let url = '';
        try {
            url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            showToast(t(successKey));
        } catch (_) {
            if (url) URL.revokeObjectURL(url);
            showToast(t(failureKey));
        }
    }

    function updateAdjustment(key, value, restoring = false) {
        // 「覆盖默认值」关着时这些值不参与绘制，Alt+滚轮也不该偷偷改它们
        if (!state.adjustOverwrite && !restoring) return;
        const numericValue = Number(value);
        if (!Number.isFinite(numericValue)) return;
        const hadPreview = Boolean(groupPreview);
        groupPreview = null;
        state.adjustments[key] = numericValue;
        syncAdjustmentReadouts();
        if (hadPreview) {
            const scene = getSelectedScene();
            if (scene) syncNativeScene(scene, { autoplay: state.animationPlaying });
        }
        applyNativeVisualState();
        void renderCanvas();
    }

    function saveAdjustment() {
        forgetStageFrames();
        void stateStageSession?.refresh();
        persistTemplateSession();
    }

    function bindAdjustment(key) {
        const input = $(`#adjust-${key}`);
        if (!input) return;
        let before;
        let template;
        input.addEventListener('input', () => {
            if (!state.adjustOverwrite) return;
            if (before === undefined || template !== templatePackage) {
                before = state.adjustments[key];
                template = templatePackage;
            }
            updateAdjustment(key, input.value);
        });
        const commit = () => {
            if (before === undefined) return;
            const previous = before;
            before = undefined;
            if (template !== templatePackage || previous === state.adjustments[key]) return;
            rememberHistory(previous, state.adjustments[key], (value) => {
                input.value = String(value);
                updateAdjustment(key, value, true);
                saveAdjustment();
            });
            saveAdjustment();
        };
        input.addEventListener('change', commit);
        input.addEventListener('pointercancel', commit);
        input.addEventListener('blur', commit);
    }

    function setInspectorTab(tab) {
        state.inspectorTab = tab;
        if (tab !== 'colors') closeColorPopover();
        setColorPartHighlight();
        if (colorDraft) {
            colorDraft = null;
            applyNativeCharacterState({ render: true });
        }
        renderInspectorTabs();
        if (tab === 'colors') renderTemplateSlotControls();
    }








    function chooseTemplateArchive() {
        templateFileInput?.click();
    }


    /**
     * @returns {Promise<boolean>} 导入是否成功。重开（从副本/重新定位）依赖这个返回值
     * 决定要不要退回下一条路，所以失败路径也必须返回 false 而不是静默。
     */
    async function importTemplateFile(file, {
        displayName = '', fromArchive = false, restoreKey = '', sourceName = '', selection = {},
        builtinPackage = null,
    } = {}) {
        if (!file) return false;
        sourceName = sourceName || (fromArchive ? displayName : String(file.name || '').replace(/\.zip$/i, ''));
        if (templateArchiveButton) templateArchiveButton.disabled = true;
        setTemplateImportStatus(t('emoteMaker.importingTemplate'));
        let nextAssetStore = null;
        let nextRenderer = null;
        let nextCanvas = null;
        let committed = false;
        try {
            let payload;
            if (builtinPackage) {
                payload = { package: JSON.parse(JSON.stringify(builtinPackage)) };
            } else {
                const formData = new FormData();
                formData.append('file', file, file.name || 'template.zip');
                if (selection.model) formData.append('model_path', selection.model);
                if (selection.atlas) formData.append('atlas_path', selection.atlas);
                const response = await fetch('/api/emote-maker/import', {
                    method: 'POST', body: formData, credentials: 'same-origin',
                    headers: { 'x-neko-emote-library': '1' },
                });
                payload = await response.json().catch(() => ({}));
                if (payload.code === 'selection_required') {
                    const path = await chooseSpineResource(payload);
                    if (!path) return false;
                    return await importTemplateFile(file, { displayName, fromArchive, restoreKey, sourceName,
                        selection: { ...selection, [payload.role]: path } });
                }
                if (!response.ok || !payload.success || !payload.package) {
                    const error = new Error(payload.error || t('emoteMaker.importFailed'));
                    error.userMessage = payload.error;
                    throw error;
                }
            }
            const provider = window.NekoEmoteTemplateRegistry?.resolve(payload.package);
            if (!provider) {
                throw new Error(t('emoteMaker.importFailed'));
            }
            if (displayName) payload.package.model.displayName = displayName;

            // Stage the selected ZIP locally before decoding the skeleton.
            const clientStoreApi = window.NekoEmoteClientZipStore;
            const ClientZipAssetStore = clientStoreApi?.ClientZipAssetStore;
            if (ClientZipAssetStore && typeof ClientZipAssetStore.fromFile === 'function') {
                try {
                    const candidate = await ClientZipAssetStore.fromFile(file);
                    const missing = getTemplateAssetPaths(payload.package)
                        .filter((path) => !candidate.has(path));
                    if (!missing.length) {
                        nextAssetStore = candidate;
                    } else {
                        candidate.release();
                    }
                } catch (_) {
                    // A missing candidate store is rejected before committing.
                }
            }

            // Everything above is staged. Do not tear down the current
            // renderer/package until the new manifest and asset
            // store have passed validation, so a bad import is recoverable.
            if (!nextAssetStore) throw new Error(t('emoteMaker.spineZipRequired'));
            nextCanvas = spineCanvas.cloneNode(false);
            nextRenderer = await window.NekoEmoteSpineRenderer.create({
                canvas: nextCanvas, manifest: payload.package, assetStore: nextAssetStore,
                loop: true, renderOnLoad: false, fitPadding: 1.1, imageLayers: wardrobeSpineLayers,
                matchTintPalette: true,
                highlightedPart: colorPartHighlight,
            });
            const nextKey = templateLibraryModule?.entryKeyFor(payload.package);
            if (pendingTemplateRemovals.has(nextKey) || (restoreKey && (pendingTemplateRemovals.has(restoreKey)
                || !findTemplateLibraryEntry(restoreKey)))) {
                throw new Error('template-removed-during-import');
            }
            populateSpineMetadata(payload.package, nextRenderer);
            const nextPackage = payload.package;
            const nextTemplateSkins = Array.isArray(nextPackage.character?.skins)
                ? nextPackage.character.skins.map((item) => ({ ...item }))
                : [];
            const nextTemplateSlots = Array.isArray(nextPackage.character?.slots)
                ? nextPackage.character.slots.map((item) => ({
                    ...item,
                    tintColor: item?.tintColor ? { ...item.tintColor } : null,
                    tintBlackColor: item?.tintBlackColor ? { ...item.tintBlackColor } : null,
                }))
                : [];
            const nextTemplateSlotDefaults = nextTemplateSlots.map((item) => ({
                ...item,
                tintColor: item?.tintColor ? { ...item.tintColor } : null,
                tintBlackColor: item?.tintBlackColor ? { ...item.tintBlackColor } : null,
            }));
            const firstScene = Array.isArray(nextPackage.scenes) ? nextPackage.scenes[0] : null;

            const previousAssetStore = templateAssetStore;
            groupPreview = null;
            spineRendererGeneration += 1;
            disposeSpineRenderer();
            if (previousAssetStore && previousAssetStore !== nextAssetStore
                && !holdsTemplateStore(previousAssetStore)) {
                // 库里还留着这个模板的贴图，清了就得重新读盘才能切回去
                releaseAssetStore(previousAssetStore);
            }
            spineCanvas.replaceWith(nextCanvas);
            spineCanvas = nextCanvas;
            spineRenderer = nextRenderer;
            nextRenderer = null;
            templatePackage = nextPackage;
            templateAssetStore = nextAssetStore;
            committed = true;
            templatePackage.capabilities = {
                ...(templatePackage.capabilities || {}),
                localAssets: true,
                assetTransport: 'client-memory',
            };
            state.templateSkins = nextTemplateSkins;
            state.templateSlots = nextTemplateSlots;
            state.templateSlotDefaults = nextTemplateSlotDefaults;
            state.templateSlotGroup = nextTemplateSlots[0]?.name || '';
            if (firstScene) {
                state.selectedSceneId = firstScene.id;
                state.animationId = firstScene.animation?.id || '';
            }
            // 组与取景属于某个模板：指纹对不上时 applyTemplateSession 会原样退回，
            // 不在这里清干净，新模板就会顶着旧模板的分组。
            state.groups = [];
            state.shots = [];
            state.groupId = '';
            // 多选集合里是上一个模板的动画 id，留着会把旧动作一起加进新模板的组
            setPickedAnimations([]);
            const saved = templateLibraryModule?.find(templateLibraryState, restoreKey || nextKey);
            const fingerprint = templateFingerprint(nextPackage);
            const sameSession = lastTemplate?.fingerprint === fingerprint;
            const restoredSession = saved?.session
                ? { ...saved.session, ...(sameSession ? lastTemplate : {}), fingerprint }
                : (sameSession ? lastTemplate : null);
            const savedGraph = restoredSession && Object.hasOwn(restoredSession, 'nodeGraph')
                ? restoredSession.nodeGraph : (sameSession ? state.nodeGraph : null);
            resetTemplateSession();
            if (restoredSession) {
                // The last-open snapshot may contain edits saved by an older
                // page that did not yet update the library on every change.
                lastTemplate = restoredSession;
            }
            if (builtinPackage) {
                state.nodeGraph = null;
                if (saved?.session) {
                    state.packName = saved.session.packName;
                    state.packFormat = saved.session.packFormat;
                    state.packPriority = saved.session.packPriority;
                }
            }
            applyTemplateSession(nextPackage, savedGraph);
            if (!state.groupId) {
                const first = allGroups()[0];
                if (first) selectGroup(first.id);
            }
            state.animationPlaying = false;
            history.length = 0;
            future.length = 0;
            const warnings = Array.isArray(templatePackage.warnings)
                ? templatePackage.warnings.filter((warning) => !String(warning).includes('已忽略'))
                : [];
            const warningCount = warnings.length;
            // 成功不需要庆祝——只有警告值得占这一行，而且不带"模板已读取"前缀
            setTemplateImportStatus(
                warningCount ? presentTemplateWarning(warnings[0]) : '',
                warningCount ? 'warning' : 'success',
            );
            // 库条目要先落进 templateLibraryState 再 renderAll：网格画的是那份状态，
            // 顺序反了就会出现"模板已载入、卡片却还没画出来"的窗口（自动化和手快都会撞上）
            rememberLoadedTemplate();
            persistTemplateToLibrary({ withThumb: true, sourceName });
            renderAll();
            // 导入即拷副本：在后台拷，别把界面卡住；失败要说话，不然用户会以为
            // 下次打开还能免定位。从副本恢复进来的那份不必再拷自己一遍。
            if (!fromArchive) {
                const archiveKey = currentTemplateKey();
                if (archiveKey) {
                    void persistTemplateArchive(archiveKey, file).catch(() => {
                        if (!pendingTemplateRemovals.has(archiveKey)) showToast(t('emoteMaker.archiveSaveFailed'));
                    });
                }
            }
            syncNativeScene(getSelectedScene(), { autoplay: false });
            renderAll();
            return true;
        } catch (error) {
            // A staged asset store belongs to the failed candidate only. The
            // active package/renderer remains untouched when validation fails.
            if (nextRenderer) nextRenderer.dispose();
            if (!committed && nextAssetStore) releaseAssetStore(nextAssetStore);
            if (committed) {
                // 成功不走嘴：激活渲染器的那条状态行随后会说话（或带着警告说）
            } else {
                reportImportFailure(error);
                renderAll();
            }
            return committed;
        } finally {
            if (templateArchiveButton) templateArchiveButton.disabled = false;
            if (templateFileInput) templateFileInput.value = '';
        }
    }

    function resetView() {
        state.adjustments = { x: 0, y: 0, rotation: 0, scale: 100 };
        renderAdjustments();
        setPreviewZoom(1);
        void renderCanvas();
    }

    function bindViewportNavigation() {
        if (!canvasStage) return;
        let drag = null;
        canvasStage.addEventListener('pointerdown', (event) => {
            const middleButton = event.button === 1;
            if (middleButton) event.preventDefault();
            if (drag || state.zoom <= 1 || event.target.closest('#wardrobe-sprite-menu')
                    || (!middleButton && (event.button !== 0 || event.target.closest('button')))) return;
            drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
            canvasStage.setPointerCapture(event.pointerId);
            canvasStage.classList.add('is-panning');
        });
        canvasStage.addEventListener('pointermove', (event) => {
            if (drag?.id !== event.pointerId) return;
            state.previewPan.x += event.clientX - drag.x;
            state.previewPan.y += event.clientY - drag.y;
            drag.x = event.clientX;
            drag.y = event.clientY;
            applyZoom();
        });
        canvasStage.addEventListener('lostpointercapture', (event) => {
            if (drag?.id !== event.pointerId) return;
            drag = null;
            canvasStage.classList.remove('is-panning');
        });
        const resize = new ResizeObserver(applyZoom);
        resize.observe(canvasStage);
        resize.observe(canvasFrame);
        canvasStage.addEventListener('wheel', (event) => {
            if (!event.deltaY || event.target.closest('#wardrobe-sprite-menu')) return;
            event.preventDefault();
            if (event.altKey) {
                updateAdjustment('rotation', state.adjustments.rotation + (event.deltaY > 0 ? 1 : -1));
                return;
            }
            setPreviewZoom(state.zoom + (event.deltaY > 0 ? -0.125 : 0.125), event);
        }, { passive: false });
    }

    function bindEvents() {
        bindWardrobeGeneration();
        bindWardrobePlacements();
        colorPanel.addEventListener('scroll', updateColorBackToTop, { passive: true });
        colorBackToTop.addEventListener('click', () => {
            colorPanel.scrollTop = 0;
            (templateSlotControls.querySelector('.part-color-menu') || $('#inspector-tab-colors')).focus({ preventScroll: true });
            updateColorBackToTop();
        });
        const colorResize = new ResizeObserver(updateColorBackToTop);
        colorResize.observe(colorPanel);
        colorResize.observe(colorPanel.querySelector('.color-section'));
        templateAttachmentSearch.addEventListener('input', () => {
            filterTemplateAttachments();
            templateAttachmentControls.scrollTop = 0;
        });
        document.querySelectorAll('.inspector-tab').forEach((button) => {
            button.addEventListener('click', () => setInspectorTab(button.dataset.inspectorTab || 'parts'));
        });
        $('#asset-search-input')?.addEventListener('input', (event) => {
            state.assetQuery = event.target.value || '';
            renderAssetGrid();
        });
        batchExportGroupSelect?.addEventListener('change', (event) => {
            selectGroup(event.target.value);
        });
        batchExportGroupSelect?.addEventListener('emote-select-action', (event) => {
            const detail = event.detail || {};
            if (detail.action === 'rename') renameGroupById(detail.value, detail.name);
            else if (detail.action === 'delete') void deleteGroupById(detail.value);
        });
        bindCursorTips();
        $('#new-group-btn')?.addEventListener('click', () => {
            if (createGroup()) showToast(t('emoteMaker.groupCreated'));
        });
        $('#select-all-btn')?.addEventListener('click', () => {
            setPickedAnimations(getVisibleImportedAnimations().map((item) => item.id));
        });
        $('#select-none-btn')?.addEventListener('click', () => setPickedAnimations([]));
        $('#context-add-btn')?.addEventListener('click', () => {
            const added = addPickedToActiveGroup();
            hideGroupContextMenu();
            showToast(interpolate(t('emoteMaker.groupAddedCount'), { count: Math.max(added, 0) }));
        });
        $('#context-remove-btn')?.addEventListener('click', () => {
            const removed = removePickedFromActiveGroup();
            hideGroupContextMenu();
            showToast(interpolate(t('emoteMaker.groupRemovedCount'), { count: Math.max(removed, 0) }));
        });
        // 气泡和右键菜单一样：点外面、按 Esc 都要能关掉，否则卡在屏幕上比不给更烦
        window.addEventListener('pointerdown', (event) => {
            const menu = $('#group-context-menu');
            if (menu && !menu.hidden && !menu.contains(event.target)) hideGroupContextMenu();
            const picker = $('#group-export-picker');
            if (picker && !picker.hidden
                && !picker.contains(event.target)
                && !event.target.closest('#export-group-open-btn')) closeGroupPicker();
        }, true);
        window.addEventListener('keydown', (event) => {
            if ($('#wardrobe-dialog')?.open) return;
            if (event.key !== 'Escape') return;
            hideGroupContextMenu();
            closeGroupPicker();
        });
        $('#group-members')?.addEventListener('click', (event) => {
            // 加号在每次重绘里重建，监听挂委托：点在它或它里面的东西都算
            if (event.target.closest('#add-scene-to-group-btn')) addCurrentSceneToGroup();
        });
        $('#export-group-open-btn')?.addEventListener('click', () => {
            if (groupExportBusy) {
                // 同一颗按钮在跑的时候就是停止：一批可能上百张，得能中途停
                groupExportAbort = true;
                return;
            }
            // aria-expanded 只由 renderGroupPicker 写：两处各写一份的话，
            // 一处漏了就成"按钮说它开着、气泡却没开"
            if (state.groupPickerOpen) closeGroupPicker();
            else openGroupPicker();
        });
        canvasImportButton?.addEventListener('click', () => chooseTemplateArchive());
        templateFileInput?.addEventListener('change', (event) => {
            const file = event.target.files?.[0];
            void importTemplateFile(file);
        });
        templateArchiveButton?.addEventListener('click', () => chooseTemplateArchive());
        $('#undo-btn')?.addEventListener('click', undo);
        $('#redo-btn')?.addEventListener('click', redo);
        // 曾经这里也绑列表分段：那时代码会把形象包的「基础/进阶」按钮一起选中，
        // 点它们顺手把 listMode 写回 'scenes'。列表只剩一种之后不再需要这个开关。
        const exportOptionChanges = [
            ['#export-format', (value) => { state.exportFormat = value === 'gif' ? 'gif' : 'png'; }],
            ['#export-resolution', (value) => {
                const resolution = Number(value);
                if (EXPORT_RESOLUTIONS.includes(resolution)) state.exportResolution = resolution;
            }],
            ['#export-fps', (value) => {
                const fps = Number(value);
                if (EXPORT_FPS_VALUES.includes(fps)) state.exportFps = fps;
            }],
        ];
        for (const [selector, apply] of exportOptionChanges) {
            $(selector)?.addEventListener('change', (event) => {
                apply(event.target.value);
                syncExportControls();
                persistExportSettings();
            });
        }
        $('#export-png-btn')?.addEventListener('click', () => {
            if (state.exportFormat === 'gif') {
                void downloadGif();
                return;
            }
            void downloadPng();
        });
        $('#open-state-stage-btn')?.addEventListener('click', () => { openStateStage(); });
        document.querySelectorAll('[data-pack-mode]').forEach((button) => {
            button.addEventListener('click', () => {
                state.packMode = button.dataset.packMode === 'graph' ? 'graph' : 'basic';
                persistLayout();
                renderAll();
            });
        });
        $('#pack-export-btn')?.addEventListener('click', () => { void downloadPngtuberPack(); });
        $('#pack-name')?.addEventListener('change', (event) => {
            state.packName = String(event.target.value || '').trim();
            persistExportSettings();
            renderPackPanel();
        });
        $('#pack-priority')?.addEventListener('change', (event) => {
            state.packPriority = event.target.value === 'emotion' ? 'emotion' : 'talking';
            persistExportSettings();
        });
        $('#pack-format')?.addEventListener('change', (event) => {
            state.packFormat = event.target.value === 'layered' ? 'layered' : 'flat';
            persistExportSettings();
            renderPackPanel();
        });
        $('#pack-import-btn')?.addEventListener('click', () => { void importPngtuberPack(); });
        const bindPackNumber = (selector, apply) => {
            const input = $(selector);
            if (!input) return;
            input.addEventListener('input', (event) => {
                apply(Number(event.target.value));
                // 只刷落位显示，不 renderAll —— 后者会重建整个形象包区块的 select，
                // 拖动过程中把焦点和拖动状态打断。
                syncExportControls();
            });
            input.addEventListener('change', () => persistExportSettings());
        };
        bindPackNumber('#pack-scale', (value) => { state.packScale = value; });
        bindPackNumber('#pack-offset-x', (value) => { state.packOffsetX = value; });
        bindPackNumber('#pack-offset-y', (value) => { state.packOffsetY = value; });
        $('#pack-mirror')?.addEventListener('change', (event) => {
            state.packMirror = event.target.checked === true;
            persistExportSettings();
            syncExportControls();
        });
        $('#pack-reset-btn')?.addEventListener('click', () => {
            state.packScale = 1;
            state.packOffsetX = 0;
            state.packOffsetY = 0;
            state.packMirror = false;
            persistExportSettings();
            syncExportControls();
        });
        $('#fit-btn')?.addEventListener('click', () => setPreviewZoom(1));
        $('#reset-view-btn')?.addEventListener('click', resetView);
        $('#zoom-in-btn')?.addEventListener('click', () => setPreviewZoom(state.zoom + 0.25));
        $('#zoom-out-btn')?.addEventListener('click', () => setPreviewZoom(state.zoom - 0.25));
        $('#adjust-overwrite')?.addEventListener('change', (event) => {
            state.adjustOverwrite = Boolean(event.target.checked);
            renderAdjustments();
            void renderCanvas();
        });
        ['x', 'y', 'rotation', 'scale'].forEach(bindAdjustment);
        $('#reset-adjustments-btn')?.addEventListener('click', () => {
            recordChange('adjustments', { x: 0, y: 0, rotation: 0, scale: 100 });
            applyNativeCharacterState({ render: true });
            applyNativeVisualState();
            saveAdjustment();
        });
        $('#play-animation-btn')?.addEventListener('click', toggleAnimationPlayback);
        animationProgress?.addEventListener('input', (event) => {
            const hadPreview = Boolean(groupPreview);
            groupPreview = null;
            state.animationProgress = Number(event.target.value) || 0;
            updateAnimationControls();
            if (hadPreview) {
                const scene = getSelectedScene();
                if (scene) syncNativeScene(scene, { autoplay: false });
            } else if (templatePackage && hasSpineRenderer()) {
                syncNativeTimeFromProgress();
            }
            void renderCanvas();
        });
        animationSpeed?.addEventListener('input', (event) => {
            state.animationSpeed = Number(event.target.value) || 1;
            updateAnimationControls();
        });
        $('#transparent-background')?.addEventListener('change', (event) => {
            state.exportTransparent = event.target.checked !== false;
            persistExportSettings();
            applyNativeVisualState();
            void renderCanvas();
        });
        bindViewportNavigation();
        window.addEventListener('keydown', (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
                event.preventDefault();
                if (event.shiftKey) redo(); else undo();
            }
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                event.preventDefault();
                if (state.exportFormat === 'gif') {
                    void downloadGif();
                } else {
                    void downloadPng();
                }
            }
        });
        window.addEventListener('localechange', renderAll);
        window.addEventListener('pagehide', () => {
            persistTemplateSession();
            spineRendererGeneration += 1;
            disposeSpineRenderer();
            releaseTemplateAssetStore();
        }, { once: true });
    }

    function initialize() {
        if (!canvas) return;
        canvas.width = CANVAS_SIZE;
        canvas.height = CANVAS_SIZE;
        history.length = 0;
        future.length = 0;
        loadExportSettings();
        loadLayout();
        loadTemplateSession();
        loadTemplateLibrary();
        void loadBuiltinTemplates();
        // 副本占用是后端 stat 的真实数字，页面起来就去拉一份
        void refreshTemplateArchiveSizes();
        loadNodeGraph();
        setTemplateImportStatus(t('emoteMaker.templateImportHint'), 'muted');
        bindEvents();
        bindPackScrollTip();
        setupResizing();
        applyLayout();
        renderAll();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
