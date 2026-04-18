/**
 * i18n.js — 文案字典 + 轻量读取 API.
 *
 * 当前只定义 `zh-CN` 字典. Settings → UI 预留语言切换位 (P04 实装),
 * 目前 `setLocale` 只支持 `zh-CN`.
 *
 * 约定:
 *   - key 采用点号命名空间:  `topbar.session.new` / `workspace.chat.title`
 *   - 未命中的 key 返回 key 本身并在 console 发 warn, 便于开发期发现遗漏
 *   - hydrate(root) 扫描 `[data-i18n]` / `[data-i18n-title]` / `[data-i18n-placeholder]`
 *     属性并回填文案, 无需在每个模块手写 textContent 赋值
 */

export const I18N = {
  'zh-CN': {
    app: {
      name: 'N.E.K.O. Testbench',
      tagline: '独立测试生态',
    },
    topbar: {
      session: {
        label: '会话',
        none: '(未建会话)',
        new: '新建会话',
        delete: '销毁当前会话',
        load: '加载存档…',
        save: '保存',
        save_as: '另存为…',
        import: '导入 JSON…',
        restore_autosave: '恢复自动保存…',
        not_implemented: '该功能将在 P21 后实装',
      },
      stage: {
        label: '阶段',
        chip_placeholder: '未启用',
        not_implemented: 'Stage Coach 将在 P14 实装',
      },
      timeline: {
        label: '时间轴',
        chip_placeholder: '无快照',
        not_implemented: '快照时间线将在 P18 实装',
      },
      error_badge: {
        title_none: '最近错误 (0)',
        title_some: count => `最近错误 (${count})`,
        empty: '暂无错误',
        view_all: '查看全部',
      },
      menu: {
        label: '菜单',
        export: '导出…',
        reset: '重置…',
        about: '关于',
        diagnostics: '打开诊断',
        settings: '打开设置',
      },
    },
    tabs: {
      setup: 'Setup 准备',
      chat: 'Chat 对话',
      evaluation: 'Evaluation 评分',
      diagnostics: 'Diagnostics 诊断',
      settings: 'Settings 设置',
    },
    workspace: {
      setup: {
        title: 'Setup 准备',
        placeholder_heading: '测试环境准备',
        placeholder_body:
          '此 workspace 用于配置角色 / 记忆 / 虚拟时钟 / 从真实角色导入. '
          + '具体子页在后续阶段注入.',
        todo_list: [
          { tag: 'P05', text: 'Persona + Import 子页 (人设表单 + 一键从真实角色拷贝)' },
          { tag: 'P06', text: 'Virtual Clock 子页 (bootstrap / live cursor / per-turn default)' },
          { tag: 'P07', text: 'Memory 四子页 (Recent / Facts / Reflections / Persona) 读写' },
          { tag: 'P10', text: 'Memory 操作触发 + 预览 drawer' },
        ],
      },
      chat: {
        title: 'Chat 对话',
        placeholder_heading: '测试主战场',
        placeholder_body:
          '对话流 + 四模式 Composer (Manual / SimUser / Scripted / Auto-Dialog) + '
          + '右侧 Prompt Preview 双视图. 在后续阶段填入.',
        todo_list: [
          { tag: 'P08', text: 'Prompt Preview 面板 (Structured / Raw wire)' },
          { tag: 'P09', text: '消息流 + 手动 Send + SSE' },
          { tag: 'P11', text: 'SimUser 模式' },
          { tag: 'P12', text: 'Scripted 模式 + 模板下拉' },
          { tag: 'P13', text: 'Auto-Dialog 模式 + 进度横幅' },
        ],
      },
      evaluation: {
        title: 'Evaluation 评分',
        placeholder_heading: '评分中心',
        placeholder_body:
          '四个子页: Run / Results / Aggregate / Schemas. ScoringSchema 作为一等公民.',
        todo_list: [
          { tag: 'P15', text: 'Schemas 子页 + 内置三套 schema' },
          { tag: 'P16', text: 'Run 子页 + 四类 Judger' },
          { tag: 'P17', text: 'Results + Aggregate 子页 + 导出报告' },
        ],
      },
      diagnostics: {
        title: 'Diagnostics 诊断',
        placeholder_heading: '运维诊断',
        placeholder_body:
          '出问题时才来. 包含 Logs / Errors / Snapshots / Paths / Reset 五个子页.',
        todo_list: [
          { tag: 'P19', text: '全局异常中间件 + Errors/Logs 子页' },
          { tag: 'P20', text: 'Paths / Snapshots / Reset 子页' },
        ],
      },
      settings: {
        title: 'Settings 设置',
        placeholder_heading: '集中配置',
        placeholder_body:
          '四组模型配置 (chat/simuser/judge/memory) + API Keys 状态 + '
          + 'Providers 只读 + UI 偏好 + About.',
        todo_list: [
          { tag: 'P04', text: 'Models / API Keys / Providers / UI / About 五个子页' },
        ],
      },
    },
    setup: {
      nav: {
        persona: 'Persona 人设',
        import: 'Import 导入',
        virtual_clock: 'Virtual Clock 虚拟时钟',
        // 记忆分组 (P07); `memory_group` 是导航侧分组标题, 不对应子页.
        memory_group: '记忆 (Memory)',
        memory_recent: '最近对话',
        memory_facts: '事实',
        memory_reflections: '反思',
        memory_persona: '人设记忆',
      },
      no_session: {
        heading: '先建一个会话',
        body: '左上角"新建会话"后, 本页将允许编辑人设或从真实角色导入.',
      },
      persona: {
        heading: '人设元数据',
        intro: '定义本次会话的身份: 主人名 / 角色名 / 语言 / system_prompt. 修改只落在当前沙盒, 不影响主 App 的真实 characters.json.',
        fields: {
          master_name: '主人名',
          character_name: '角色名',
          language: '语言',
          system_prompt: 'System Prompt',
        },
        placeholder: {
          master_name: '例: Master / 主人',
          character_name: '例: N.E.K.O. / 兰兰',
          system_prompt: '留空可通过 Import 从真实角色拷贝默认 prompt.',
        },
        hint: {
          master_name: '出现在 human 消息前的说话人标签.',
          character_name: '角色专属 memory 子目录名; 变更后 Import 会写到新目录.',
          language: 'zh-CN / en / ja 等 ISO 代码. 作用于后续 Prompt 本地化.',
          system_prompt: '支持 {LANLAN_NAME} / {MASTER_NAME} 占位符, 由 Prompt 合成阶段 (P08) 替换. 留空或保留默认文本时, 运行期会自动替换为当前 language 的默认模板 — 下方可预览实际效果.',
        },
        buttons: {
          save: '保存',
          revert: '撤销',
        },
        status: {
          saved: '已保存',
          save_failed: '保存失败',
          loaded: '已载入当前会话 persona',
          no_change: '没有未保存的改动',
        },
        // 实际 system_prompt 预览 (P05 补强, 对照后端 /api/persona/effective_system_prompt).
        preview: {
          heading: '预览实际 system_prompt',
          intro: '按当前表单内容 (未保存也生效) 还原运行时装配结果: 空/默认文本会走对应 language 默认模板, 然后替换 {LANLAN_NAME}/{MASTER_NAME}. 实际 LLM 请求里的 system_prompt 就是此处的 "最终" 文本.',
          refresh_btn: '刷新预览',
          loading: '加载中...',
          load_failed: '加载失败',
          source_label: '来源',
          source_default: '默认模板 (language = {0})',
          source_stored: '自定义 (来自已保存 system_prompt)',
          default_warning: '注意: 当前自定义文本被识别为某语言默认模板 — 运行期会被当作"空"并自动换成 language={0} 的版本. 若想固化保存, 需要对默认文本做任意修改.',
          placeholder_warning: '注意: 主人名 / 角色名为空, 预览里保留了 {LANLAN_NAME} / {MASTER_NAME} 占位符, 运行期会按实际填写值替换.',
          resolved_label: '最终 (名字替换后)',
          template_label: '模板原文 (未替换占位符)',
          copy_btn: '复制',
          copy_done: '已复制',
          copy_fail: '复制失败',
          char_count: n => `${n} 字符`,
        },
      },
      import: {
        heading: '从真实角色导入',
        intro: '列出主 App 文档目录下的所有猫娘, 点击"导入"即把其 memory 子目录 + system_prompt 拷贝进当前沙盒. 不会写回主 App.',
        no_session: '需要先建会话才能读取真实角色目录.',
        no_real: '主 App 文档目录下没找到 characters.json, 或暂无角色.',
        columns: {
          name: '角色名',
          status: '状态',
          files: 'memory 文件',
          action: '操作',
        },
        badge_current: '当前',
        badge_has_prompt: 'prompt ✓',
        badge_no_prompt: 'prompt ✗',
        badge_no_memdir: '无 memory 目录',
        button_import: '导入到当前会话',
        button_importing: '导入中…',
        confirm_overwrite: name => `"${name}" 已经在当前沙盒存在同名 memory 目录. 继续将覆盖文件; 确认?`,
        import_ok: (name, count) => `已导入 ${name} (${count} 个文件)`,
        import_failed: '导入失败',
        source_paths_label: '数据源',
      },
      memory: {
        // 无会话 / 无角色 时的空状态文案.
        no_session: {
          heading: '先建一个会话',
          body: '左上角"新建会话"后, 这里会打开本会话沙盒里的记忆文件 (recent / facts / reflections / persona).',
        },
        no_character: {
          heading: '先选一个角色',
          body: '请去 Setup → Persona 填写\u300c角色档案名\u300d, 或去 Setup → Import 从真实角色导入, 再回来编辑记忆.',
        },
        // 公共工具条文案, 4 个子页都会用到.
        editor: {
          recent: {
            heading: 'Recent · 最近对话 (recent.json)',
            intro: 'LangChain 风格的消息数组 (type=human/ai/system, data.content=字符串). 这是未压缩的原始对话, Prompt 装配会取末尾若干条.',
          },
          facts: {
            heading: 'Facts · 事实池 (facts.json)',
            intro: '压缩提炼出的事实列表. 每条含 id / text / importance / entity (master|neko|relationship) / tags / hash / created_at / absorbed.',
          },
          reflections: {
            heading: 'Reflections · 反思 (reflections.json)',
            intro: 'pending / confirmed / denied / promoted 四态. status=promoted 表示已晋升为 persona fact. 手动编辑时注意 id 不要重复.',
          },
          persona: {
            heading: 'Persona · 人设记忆 (persona.json)',
            intro: '顶层按 entity 分节 (master / neko / relationship / 自定义), 每节是 { "facts": [...] }. 注意: 真实 PersonaManager 首次加载还会自动合并角色卡片, 这里看到的是磁盘上的原始状态.',
          },
          path_label: '文件路径',
          not_exists_badge: '文件尚未生成 (保存后创建)',
          exists_badge: '已存在',
          count_list: count => `共 ${count} 条`,
          count_dict: count => `共 ${count} 个 entity`,
          valid: 'JSON 合法',
          invalid: prefix => `JSON 解析失败: ${prefix}`,
          dirty_badge: '未保存',
          saving: '保存中...',
          saved: '已保存',
          reloading: '重新加载中...',
          reloaded: '已重新加载',
          format_done: '已重新格式化',
          format_failed: 'JSON 不合法, 无法格式化',
          buttons: {
            save: '保存',
            reload: '从磁盘重新加载',
            format: '重排格式',
            revert: '还原到上次加载',
          },
          confirm_overwrite: '当前有未保存修改, 重新加载会丢弃. 确认吗?',
        },
      },
      virtual_clock: {
        heading: '虚拟时钟 (滚动游标)',
        intro: '测试用的时间源. Prompt 装配和记忆计算使用的"当前时间"全部取自这个游标, 不读系统时钟. 游标不会自己前进, 需要通过下方"实时游标"或"每轮暂存"手动推进.',
        no_session: {
          heading: '先建一个会话',
          body: '左上角"新建会话"后, 才能配置会话级时钟.',
        },
        live: {
          heading: '实时游标 (当前时间)',
          intro: '本次会话的"虚拟当前时间". 未设定时回退到系统真实时间, 每秒自动刷新; 一旦设定为某个具体时间就会冻结在那里, 只能手动推进.',
          now_label: '当前时间',
          real_time_badge: '跟随系统时间',
          virtual_badge: '虚拟时间',
          absolute_label: '设为指定时间',
          advance_label: '按时长推进',
          set_btn: '设定',
          release_btn: '回到系统时间',
          advance_btn: '推进',
          preset_plus_5m: '+5 分钟',
          preset_plus_1h: '+1 小时',
          preset_plus_1d: '+1 天',
          delta_hint: '支持 "1h30m" / "45s" / "2d 4h" 这类写法, 也接受纯数字 (如 "120" 视作 120 秒); 以负号开头 (如 "-1h") 即回退.',
        },
        bootstrap: {
          heading: '会话起点 (Bootstrap)',
          intro: '会话创建时的"虚拟当前时间", 以及"距离上次对话过去了多久". 这两个值只在**首条消息发送前**被 Prompt 用到; 一旦有了首条消息, 后续 gap 改以最后一条消息的时间戳为准, 本段数据就不再影响新轮次.',
          bootstrap_at_label: '起点时间',
          initial_gap_label: '距上次对话',
          sync_cursor_label: '同时把"实时游标"也设到起点时间 (常见用法)',
          set_btn: '保存起点',
          clear_bootstrap_btn: '清除起点时间',
          clear_gap_btn: '清除距上次对话',
          hint: '"距上次对话"支持 "1h30m" / "3600s" / 纯秒数等写法, 表示"上次聊天发生在起点时间之前多久".',
        },
        per_turn_default: {
          heading: '每轮默认推进',
          intro: '自动对话 / 脚本对话 / 手动 Composer 每发送一轮后, 游标默认往前走的时长. 单轮在下面"每轮暂存"里显式设定的值会覆盖本项.',
          value_label: '默认 +',
          set_btn: '保存',
          clear_btn: '清空',
          hint: '支持 "1h30m" / "45s" / 纯数字 (秒) 等写法; 留空保存 = 清除, 等同"不自动推进".',
          current_label: '当前默认',
          unset_value: '不自动推进 (每轮游标保持不动)',
        },
        pending: {
          heading: '下一轮暂存 (Pending)',
          intro: '临时声明"下一次发送前, 把游标推到某个时间". 在下一次发送消息时会被使用一次, 使用完立即清空, 不影响后续轮次. 如果同时设了"按时长"和"设为指定时间", 以"指定时间"为准.',
          none_label: '当前没有暂存 (下一轮按"每轮默认推进"执行)',
          pending_delta_label: '下一轮按时长推进',
          pending_abs_label: '下一轮设为指定时间',
          delta_input_label: '按时长',
          abs_input_label: '指定时间',
          stage_delta_btn: '暂存时长',
          stage_abs_btn: '暂存时间',
          clear_btn: '清空暂存',
        },
        reset: {
          heading: '重置时钟',
          intro: '一键清空"实时游标 / 会话起点 / 每轮默认推进 / 下一轮暂存", 回到"跟随系统时间 + 无起点"的裸态. 不会删除任何消息或记忆.',
          reset_btn: '重置时钟',
          confirm: '确定要重置虚拟时钟吗? (不影响消息和记忆)',
        },
        status: {
          saved: '已更新',
          save_failed: '更新失败',
          cleared: '已清除',
          invalid_duration: '无法解析时长, 请检查格式',
          invalid_datetime: '无法解析时间, 请检查格式',
        },
      },
    },
    diagnostics: {
      errors: {
        heading: 'Errors · 错误面板 (临时)',
        notice: '本面板是 P04 夹带的**临时调试视图**, P19 会替换为完整 Logs/Errors/Snapshots/Paths/Reset 五子页. 出错时先看这里确认一下.',
        sources: {
          http: 'HTTP',
          sse: 'SSE',
          js: 'JS',
          promise: 'Promise',
          resource: 'Asset',
          unknown: '其他',
        },
        columns: {
          at: '时间',
          source: '来源',
          type: '类型',
          message: '摘要',
        },
        clear: '清空',
        expand_all: '展开全部',
        collapse_all: '折叠全部',
        cleared: count => `已清空 ${count} 条`,
        empty: '暂无错误. 继续测试; 有问题会自动出现在这里.',
        count: count => `共 ${count} 条`,
        trigger_test: '制造一条测试错误',
        trigger_test_done: '已追加一条合成错误, 用于验证面板',
      },
    },
    settings: {
      nav: {
        models: 'Models 模型',
        api_keys: 'API Keys 密钥',
        providers: 'Providers 服务商',
        ui: 'UI 偏好',
        about: '关于',
      },
      models: {
        heading: '模型配置',
        intro: '四组模型分别用于 目标 AI / 假想用户 / 评分 / 记忆合成. 修改后立刻生效于当前会话, 不会写入磁盘; 保存会话时 api_key 默认脱敏.',
        groups: {
          chat: { title: '目标 AI (chat)', hint: '被测对象, 接收 wire_messages 并输出回复.' },
          simuser: { title: '假想用户 (simuser)', hint: 'SimUser 模式下替测试人员生成 user 消息.' },
          judge: { title: '评分 AI (judge)', hint: '按 ScoringSchema 出评分 JSON. 推荐能力较强的模型.' },
          memory: { title: '记忆合成 (memory)', hint: '压缩 / 反思 / persona 更新的后台 LLM.' },
        },
        fields: {
          provider: '预设服务商',
          provider_manual: '自定义 (手动)',
          base_url: '服务端 base_url',
          api_key: 'API Key',
          model: '模型名',
          temperature: 'Temperature',
          max_tokens: 'Max tokens',
          timeout: '超时 (秒)',
        },
        placeholder: {
          base_url: '如 https://dashscope.aliyuncs.com/compatible-mode/v1',
          api_key: '留空则自动用 tests/api_keys.json 里对应字段',
          model: '如 qwen-plus / gpt-4.1-mini',
        },
        api_key_status: {
          configured: '已配置 (请求时使用)',
          from_preset: name => `将使用 tests/api_keys.json 中的 ${name}`,
          missing: '未配置, 请填入或去 API Keys 页面补',
        },
        buttons: {
          apply_preset: '应用预设',
          test: '测试连接',
          save: '保存',
          revert: '撤销',
          reload_key: '重新读取 API Keys',
        },
        status: {
          saved: '已保存',
          save_failed: '保存失败',
          testing: '测试中…',
          test_ok: latency => `连通 (${latency}ms)`,
          test_failed: '失败',
          not_configured_hint: 'base_url / model 必填; api_key 若留空将使用预设 key',
        },
      },
      api_keys: {
        heading: 'API Keys 状态',
        intro: '本表反映 tests/api_keys.json 各字段是否已填. 不回显明文. 修改本地文件后点"重新读取"即可刷新.',
        path_label: '文件路径',
        path_missing: '(文件不存在, 可从 tests/api_keys.json.template 拷贝生成)',
        last_read: '最后读取',
        reload: '重新读取',
        columns: {
          field: '字段',
          provider: '关联预设',
          status: '状态',
        },
        status_present: '已填',
        status_missing: '未填',
        extra_heading: '额外字段 (本表未列出, 仍会被主 app 使用)',
        load_error_label: '加载错误',
      },
      providers: {
        heading: 'Providers (只读)',
        intro: '读取自 config/api_providers.json → assist_api_providers. 修改请直接编辑 JSON 文件; testbench 不提供写入.',
        columns: {
          key: 'key',
          name: '名称',
          base_url: 'base_url',
          conversation_model: '对话模型',
          summary_model: '摘要模型',
          api_key: 'Key 状态',
        },
        free_tag: '免费',
        has_key: '✓',
        no_key: '✗',
      },
      ui: {
        heading: 'UI 偏好 (占位)',
        intro: '以下选项将在后续 phase 接入. 本期 P04 仅展示入口.',
        language_label: '界面语言',
        language_only_zh: '目前仅支持简体中文, 其它语种将在后续版本加入.',
        theme_label: '主题',
        theme_dark: '暗色 (默认)',
        theme_light_todo: '浅色 (TODO)',
        snapshot_limit_label: '快照内存上限 (条)',
        snapshot_limit_hint: 'P18 实装快照时间线时生效.',
        fold_defaults_label: '默认折叠策略',
        fold_defaults_hint: '按内容类型分别设置默认展开/折叠, 将在 P08 Prompt Preview 落地时接入.',
        reset_fold: '清除当前会话的 localStorage fold 记录',
        reset_fold_ok: '已清除 fold 键',
      },
      about: {
        heading: '关于 N.E.K.O. Testbench',
        version_label: '版本',
        phase_label: '当前阶段',
        host_label: '监听地址',
        loading: '加载中…',
        limits_heading: '本期声明 (刻意不做的能力)',
        limits: [
          '本期只支持单活跃会话; 多标签会相互踩状态',
          '仅文本对话, 暂不接入 Realtime / 语音',
          '默认绑定 127.0.0.1, 不监听公网',
          'api_key 在内存中保留明文, 保存到磁盘时自动脱敏',
        ],
        docs_hint: '计划与进度: tests/testbench/docs/{PLAN, PROGRESS, AGENT_NOTES}.md',
      },
    },
    collapsible: {
      expand_all: '展开全部',
      collapse_all: '折叠全部',
      copy: '复制',
      copy_ok: '已复制',
      copy_fail: '复制失败',
      collapse: '折叠',
      length_chars: n => `${n} 字符`,
    },
    toast: {
      close: '关闭',
      dismiss_all: '清除全部',
    },
    session: {
      created: name => `会话已创建: ${name}`,
      destroyed: '会话已销毁',
      no_active: '当前无活跃会话',
      create_failed: '创建会话失败',
      destroy_failed: '销毁会话失败',
      confirm_destroy: '确认销毁当前会话? 沙盒目录将被清空.',
    },
    errors: {
      network: '网络请求失败',
      server: code => `服务端错误 (HTTP ${code})`,
      unknown: '未知错误',
    },
    common: {
      ok: '确定',
      cancel: '取消',
      close: '关闭',
      loading: '加载中…',
      not_implemented: '尚未实装',
    },
  },
};

let _locale = 'zh-CN';

/** 切换当前语言 (P04 接入 UI; 目前仅接受 zh-CN). */
export function setLocale(locale) {
  if (!I18N[locale]) {
    console.warn(`[i18n] unsupported locale: ${locale}, keeping ${_locale}`);
    return;
  }
  _locale = locale;
}

export function getLocale() {
  return _locale;
}

/**
 * 按点号 key 读取文案. 支持值为函数时直接调用.
 *
 * @param {string} key   `topbar.session.new`
 * @param {...any} args  若字典中的值是函数, 透传这些参数
 * @returns {string}
 */
export function i18n(key, ...args) {
  const dict = I18N[_locale];
  const parts = key.split('.');
  let node = dict;
  for (const p of parts) {
    if (node && typeof node === 'object' && p in node) {
      node = node[p];
    } else {
      console.warn(`[i18n] missing key: ${key}`);
      return key;
    }
  }
  if (typeof node === 'function') {
    try {
      return node(...args);
    } catch (err) {
      console.warn(`[i18n] formatter ${key} threw:`, err);
      return key;
    }
  }
  return node;
}

/** 读对象/数组原值 (供渲染 todo list 等结构化文案). */
export function i18nRaw(key) {
  const dict = I18N[_locale];
  const parts = key.split('.');
  let node = dict;
  for (const p of parts) {
    if (node && typeof node === 'object' && p in node) {
      node = node[p];
    } else {
      console.warn(`[i18n] missing key: ${key}`);
      return null;
    }
  }
  return node;
}

/**
 * 扫描 DOM 节点, 回填 `data-i18n` / `data-i18n-title` / `data-i18n-placeholder`
 * 属性. 同一节点可以用多个属性.
 */
export function hydrateI18n(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) {
    el.textContent = i18n(el.dataset.i18n);
  }
  for (const el of root.querySelectorAll('[data-i18n-title]')) {
    el.title = i18n(el.dataset.i18nTitle);
  }
  for (const el of root.querySelectorAll('[data-i18n-placeholder]')) {
    el.placeholder = i18n(el.dataset.i18nPlaceholder);
  }
  for (const el of root.querySelectorAll('[data-i18n-aria-label]')) {
    el.setAttribute('aria-label', i18n(el.dataset.i18nAriaLabel));
  }
}
