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
        no_session: '未建会话',
        chip_title: stageName => `当前阶段: ${stageName} · 点击展开 Stage Coach`,
        chip_title_expanded: '点击折叠 Stage Coach',
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
        scripts: 'Scripts 对话剧本',
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
        heading: '导入角色',
        intro: '把一个可用的角色灌进当前沙盒. 有两种来源: (1) testbench 自带的内置预设, 一键即可用, 也可用来清零本会话; (2) 从主 App 文档目录里的真实角色复制. 两种都**只写沙盒**, 不会回写主 App.',
        // 内置预设 (git 追踪的 seed 数据).
        builtin: {
          heading: '内置预设 (testbench 自带)',
          intro: '仓库里带的最小完整示例角色. 适合: 新会话快速起点; 把乱七八糟的沙盒一键覆盖回已知状态 (characters.json + persona.json + facts.json + recent.json 都会被重写). 多次点击会重复覆盖, 不会累积.',
          empty: '暂无内置预设 (tests/testbench/presets/ 里没有任何合法子目录).',
          button_apply: '一键载入',
          button_applying: '载入中…',
          apply_ok: (name, count) => `已载入预设 ${name} (${count} 个文件)`,
          apply_failed: '载入预设失败',
        },
        // 真实角色导入 (从主 App characters.json).
        real: {
          heading: '从主 App 真实角色导入',
          intro: '列出 ~/Documents/N.E.K.O/config/characters.json 里的所有猫娘. 只读真实目录, 复制到沙盒.',
        },
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
      // P12.5: Setup → Scripts 子页 (对话剧本模板编辑器).
      scripts: {
        heading: '对话剧本 (Dialog Scripts)',
        intro: '阅览 / 复制 / 编辑 / 新建 `dialog_templates/*.json`. 剧本里的 user turn 是测试输入, assistant turn 的 `expected` 字段会在 Chat 里跑脚本时自动写入消息的"参考回复"供对照评分 (P15 Comparative Judger). 评分 prompt / 评分维度不在这里, 归 Evaluation → Schemas 子页.',
        buttons: {
          refresh_list: '刷新列表',
          new_blank: '+ 新建空白模板',
          save: '保存',
          reload: '撤销',
          export: '导出 JSON',
        },
        list: {
          count_fmt: n => `共 ${n} 条`,
          user_group: n => `用户模板 (${n})`,
          builtin_group: n => `内置模板 (${n}, 只读)`,
          badge_builtin: '内置',
          badge_overriding: '覆盖 builtin',
          duplicate: '复制为可编辑',
          delete: '删除',
          turns_fmt: n => `${n} 轮`,
          empty: '还没有任何模板. 点 [+ 新建空白模板] 或把 JSON 文件放到 testbench_data/dialog_templates/ 后点 [刷新列表].',
        },
        editor: {
          loading: '加载中…',
          empty_title: '从左侧挑一个剧本开始编辑',
          empty_hint: '内置模板只读, 点 [复制为可编辑] 得到 user 副本再改. 也可以直接 [+ 新建空白模板].',
          untitled: '(未命名)',
          readonly_badge: '只读 · 内置',
          dirty_badge: '未保存',
          readonly_hint: '这是内置模板, 直接编辑会被 git 覆盖. 请先点 [复制为可编辑] 创建 user 副本.',
          basic_heading: '基本信息',
          bootstrap_heading: 'Bootstrap (初始虚拟时间)',
          bootstrap_intro: '加载此剧本时, 若会话尚未产生任何消息, 虚拟时钟会被重置到这里. 已有消息时忽略 (不会硬覆盖产生负时间差).',
          turns_heading: n => `对话回合 (${n} 轮)`,
          turns_empty: '还没有 turn. 点下方 [+ 添加 user turn] 开始写.',
          role_user: '用户 (user)',
          role_assistant: 'AI (assistant)',
          errors_heading: '顶层字段错误',
          buttons: {
            add_user: '+ 添加 user turn',
            add_assistant: '+ 添加 assistant 参考回复',
          },
          fields: {
            name: '模板 name (= 文件名)',
            description: '描述',
            user_persona_hint: '假想用户画像提示 (user_persona_hint)',
            virtual_now: '虚拟起始时间 (ISO, 可选)',
            last_gap_minutes: '距上一次对话分钟数 (可选)',
            user_content: 'user 消息内容',
            assistant_expected: '参考回复 (expected)',
            time_advance: '推进时长 (advance)',
            time_at: '绝对时间 (at)',
          },
          hints: {
            name: '字母 / 数字 / 下划线 / 短横线, 首字非符号, ≤64 字. 改名会走 Save As.',
            name_readonly: '内置模板 name 不可改. 复制为 user 副本后可改.',
            user_persona_hint: '生成假想用户消息时会注入这段话, 帮 LLM 进入角色.',
            virtual_now: '例: 2025-01-01T09:00',
            last_gap_minutes: '整数, 例: 10',
            assistant_expected: '这条 expected 会挂到上一条 user turn 对应 AI 回复的 reference_content. 连续多条 assistant 的 expected 会用 `\\n---\\n` 合并.',
            time_advance: '例: 5m / 1h30m / 2d. 与 at 只能二选一, 未填走默认.',
            time_at: '例: 2025-01-01T09:05. 与 advance 只能二选一.',
          },
          placeholders: {
            user_content: '例: 下午好. 我又被组长当众骂了...',
            assistant_expected: '例: ......当着全组的面啊, 那肯定难受得紧. 本喵听着都替你胃疼. 先别急着替他说话...',
          },
        },
        prompt: {
          new_name: '新模板的 name (= 文件名):',
          duplicate_name: src => `把内置模板 "${src}" 复制为 user 副本. 新 name:`,
          confirm_delete: name => `确定要删除 user 模板 "${name}" 吗? 此操作不可恢复 (但内置版本若存在会重新生效).`,
        },
        toast: {
          list_failed: '加载模板列表失败',
          load_failed: '加载模板详情失败',
          name_taken: name => `user 模板 "${name}" 已存在. 请换个 name.`,
          duplicate_failed: '复制失败',
          duplicated: name => `已复制到 "${name}"`,
          delete_failed: '删除失败',
          deleted: name => `已删除 "${name}"`,
          resurfaces_builtin: name => `内置版本的 "${name}" 重新生效`,
          save_failed: '保存失败',
          save_errors: n => `保存被拒: ${n} 条字段错误待修正`,
          saved: name => `已保存 "${name}"`,
          now_overriding_builtin: name => `当前 user 模板 "${name}" 覆盖了同名 builtin — 加载时优先用 user 版本`,
          rename_left_old: old => `改名已保存新版本, 但删除旧 user 模板 "${old}" 时失败, 请手动 [刷新列表] 检查.`,
        },
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
          // 结构化 / Raw 双视图 tab.
          tabs: {
            structured: '结构化',
            raw: 'Raw JSON',
          },
          tab_switch_blocked: brief => `当前 Raw 文本不是合法 JSON, 无法切到结构化视图: ${brief}`,
          // 结构化视图通用文案.
          advanced_toggle: '高级字段',
          add_entity: '添加实体',
          add_persona_fact: '添加事实',
          add_fact: '添加事实条目',
          add_reflection: '添加反思条目',
          add_message: '添加消息',
          prompt_entity_name: '新实体名 (如 master / neko / 自定义)',
          entity_exists: name => `实体 "${name}" 已存在`,
          delete_item: '删除',
          delete_entity: '删除实体',
          delete_entity_confirm: name => `删除实体 "${name}" 及其下所有条目? 点保存前都可用"还原"回退.`,
          count_items: n => `${n} 条`,
          empty_persona_hint: '还没有任何实体. 人设按实体 (master / neko / 自定义角色) 分组, 每个实体下挂一串事实. 点"＋添加实体"开始.',
          empty_facts_hint: '此实体下暂无事实.',
          empty_list_hint: '暂无条目. 点上方"＋"按钮添加.',
          recent_warn: '此文件是运行期对话日志 (LangChain dump), 一般由 Chat 工作区自动写入; 手动编辑仅用于制造异常输入来测 pipeline 容错.',
          complex_content_hint: '此消息 content 是 multimodal 分段结构但不含文本段 (如仅图片/音频), 不能直接用文本框编辑. 如需修改请切到 Raw JSON 视图.',
          multimodal_extras: count => `另含 ${count} 个非文本分段 (如图片/音频), 编辑不影响它们; 如需改切 Raw`,
          multimodal_multi_text: count => `此消息共有 ${count} 个文本分段, 上方只编辑首段; 改其它段请切 Raw`,
          textarea: {
            expand: '展开全文 ▾',
            collapse: '折叠 ▴',
          },
          // 字段标签.
          field: {
            text: '文本',
            entity: '实体',
            source: '来源',
            protected: 'protected (永久, 不可抑制)',
            suppress: 'suppress (临时抑制)',
            suppressed_at: 'suppress 开始时间',
            source_id: '上游 ID',
            recent_mentions: '最近提及时间戳',
            id: 'ID',
            importance: '重要度',
            tags: '标签 (逗号分隔)',
            hash: 'hash',
            created_at: '创建时间',
            absorbed: 'absorbed (已被反思吸收)',
            status: '状态',
            source_fact_ids: '源事实 IDs (逗号分隔)',
            feedback: '反馈',
            next_eligible_at: '下次触发时间',
            type: '消息类型',
            content: '内容',
            extra_data: '其它 data 字段 (JSON)',
          },
          // recent 消息的 LangChain type 枚举 label. 括号里是给测试人员看的中文注释,
          // 方便一眼知道 human/ai/system 分别对应"用户/助手/系统".
          message_type: {
            human: 'human (用户)',
            ai: 'ai (助手)',
            system: 'system (系统)',
          },
        },
        // P10 触发面板: 在 editor 下方让测试人员手动跑记忆合成操作, 查看
        // dry-run 预览, 确认后再写入磁盘. 全部 LLM 调用走本会话 memory 组.
        trigger: {
          section_title: '手动触发记忆合成',
          reloaded_after_commit: '已应用并重新加载磁盘',
          params_title: op => `参数配置 · ${op}`,
          preview_title: op => `预览 · ${op}`,
          failed_title: op => `触发失败 · ${op}`,
          close: '关闭',
          no_params: '此操作无可配置参数, 点\u300c执行\u300d直接跑.',
          run_button: '执行 (Dry-run)',
          cancel: '取消',
          running: '正在调用模型, 请稍候...',
          accept: '应用到磁盘',
          reject: '丢弃此次预览',
          committing: '写入中...',
          committed: op => `${op} 已应用到磁盘`,
          drop_item: '从本次提交中剔除',
          recent: {
            intro: '把 recent.json 尾部若干条消息压成一条 system 摘要, 节省 context. 只影响 recent.json, 不动 facts/reflections.',
            compress: { label: '压缩尾部消息' },
            params: {
              tail_count: '压缩条数',
              tail_count_ph: '默认按历史长度阈值自动算',
              tail_count_help: '要送进 LLM 压缩的末尾消息数. 留空=按 max_history_length 自动推导.',
              detailed: '详细摘要',
              detailed_help: '勾选后生成\u300c详细版\u300d摘要 (篇幅更长, 保留更多细节), 否则用简洁版.',
            },
            stats: {
              total_before: '压缩前条数',
              tail_count: '本次压缩',
              kept_count: '保留原样',
              total_after: '压缩后条数',
            },
            preview: {
              memo: '注入 recent 尾部的 system 消息',
              memo_help: '写入时会替换为单条 system 消息挂在 kept 之前. 可在此直接修改.',
              raw_summary: 'LLM 原始摘要输出',
              raw_summary_help: '仅参考. 实际写入的是上面的 system 消息文本.',
            },
          },
          facts: {
            intro: '从本会话消息 (或 recent.json) 中抽取可复用事实. 可逐条剔除/微调后再写入 facts.json.',
            extract: { label: '从对话抽事实' },
            params: {
              source: '来源',
              source_session: '本会话 Messages (Chat 页)',
              source_recent: '磁盘上的 recent.json',
              source_help: '默认用 Chat 工作区当前对话. 选 recent.json 则读磁盘.',
              min_importance: '最小重要度',
              min_importance_help: '低于此值的事实会被丢弃 (0-10, 默认 5).',
            },
            stats: {
              message_count: '扫描消息',
              extracted_count: '本次抽出',
              total_existing: '原有事实数',
            },
            fields: {
              text: '事实正文',
              entity: '实体',
              importance: '重要度 (0-10)',
              tags: '标签 (逗号分隔)',
            },
            preview: {
              empty: '模型没有抽到任何新事实 (可能已全部重复或未达最小重要度).',
            },
          },
          reflections: {
            intro: '把多条未吸收的事实合成为一条反思. 合成后相关事实会标记 absorbed, 反思进入 pending 等待裁决.',
          },
          reflect: {
            label: '合成反思',
            params: {
              min_facts: '最少事实数',
              min_facts_help: '未吸收事实少于此值时跳过合成. 默认 5.',
            },
            stats: {
              unabsorbed: '可用未吸收事实',
              source_count: '引用事实数',
            },
            fields: {
              text: '反思正文',
              entity: '归属实体',
              entity_master: 'master (主人)',
              entity_neko: 'neko (自己)',
              entity_relationship: 'relationship (关系)',
            },
            source_facts_title: n => `引用的事实 (${n})`,
          },
          persona: {
            intro: '手动添加一条 persona 事实. 若与现有 persona 或角色卡冲突, 会进入矛盾队列或直接拒绝.',
            add_fact: { label: '添加 persona 事实' },
            resolve_corrections: { label: '裁决矛盾队列' },
            params: {
              text: '事实内容',
              entity: '归属实体',
            },
            code: {
              added: '将直接写入',
              rejected_card: '与角色卡冲突 · 将被永久拒绝',
              queued: '与现有条目冲突 · 进入矛盾队列',
            },
            preview: {
              code_label: '预期结果',
              existing_count: '该实体现有条目',
              conflicting: '冲突条目原文',
              conflicting_help: '仅展示, 裁决将在矛盾队列阶段完成.',
              text: '即将写入的正文',
              text_help: '可在此微调文案 (不会触发重新跑矛盾检测).',
              entity: '归属实体',
              section_preview_title: n => `写入后该 entity 内容 (${n} 条)`,
            },
          },
          resolve: {
            stats: {
              queue_size: '队列规模',
              action_count: 'LLM 建议动作数',
            },
            empty: '矛盾队列为空, 没有需要裁决的条目.',
            fields: {
              old_text: '原条目',
              new_text: '待评估新条目',
              action: '建议动作',
              merged_text: '合并后文本',
              merged_text_help: 'action=replace/keep_new/keep_both 时会用到, 可手动改写.',
            },
            action: {
              replace: 'replace · 新替旧',
              keep_new: 'keep_new · 丢弃旧',
              keep_old: 'keep_old · 丢弃新',
              keep_both: 'keep_both · 两条都留',
            },
          },
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
    chat: {
      // P08 引入 preview; P09 补齐 stream / composer / role / source 命名空间.
      // 保持四个子节点 (preview / stream / composer / role|source) 平行, UI 代码
      // 里出现的任何 `chat.*` key 都能在这里直接定位.
      role: {
        user: '用户',
        assistant: '助手',
        system: '系统',
      },
      source: {
        manual: '手动',
        inject: '注入',
        llm: 'LLM',
        simuser: '假想用户',
        script: '脚本',
        auto: '自动',
      },
      stream: {
        count: (n) => `共 ${n} 条消息`,
        refresh_btn: '刷新',
        clear_btn: '清空',
        empty: '尚无消息.',
        empty_hint: '在下方输入框输入一条用户消息, 按 [发送] 发起对话; 或用 [注入 sys] 写入一条系统级中段指令.',
        menu_title: '消息操作',
        menu: {
          edit: '编辑内容',
          timestamp: '编辑时间戳',
          rerun: '从此处重跑',
          delete: '删除',
        },
        prompt: {
          edit: '编辑消息内容 (取消则不修改):',
          timestamp: '输入 ISO8601 时间戳, 留空则用当前虚拟时间:',
          delete: '确定删除这条消息? 不可撤销.',
          rerun: '将截断从此消息之后的所有内容, 并把时钟回退到本条消息的 timestamp. 继续?',
          clear_all: '清空当前会话全部消息? 不可撤销.',
        },
        toast: {
          bad_timestamp: '时间戳格式无效',
          rerun_done: (n) => `已截断 ${n} 条后续消息, 时钟已回退. 可继续编辑 / 重新发送.`,
        },
        long_content_title: (n) => `长消息 (${n} 字符)`,
        // P12: assistant 消息上挂的 reference_content (脚本 expected / 手工
        // 写的"理想人类回复") 折叠块标题 + 空态提示.
        reference_title: '参考回复 (reference_content)',
        reference_hint: '由脚本 expected 回填或测试人员手动写入, 用于 P15+ 对照评分. 不会发给目标 AI.',
      },
      composer: {
        placeholder: '在此输入消息 (Ctrl+Enter 发送)...',
        send: '发送',
        sending: '发送中…',
        send_title_user: '把你输入的内容以 role=user 写入历史, 并立即调用 LLM 拿一条回复.',
        send_title_system: '把你输入的内容以 role=system 写入历史, 并立即调用 LLM 拿一条回复 (等价于"下一秒 AI 看到新规则就马上开口回应").',
        inject: '注入 sys',
        inject_title: '把你输入的内容以 role=system 写入历史, 但不调 LLM. 适合"中段改规则/改背景", 下一次点发送时 AI 才会看到.',
        system_mode_hint: 'role=system 下 [发送] 会写入 + 立即跑 LLM; [注入 sys] 仅写入历史不跑 LLM.',
        inject_empty: 'textarea 为空, 无法注入.',
        clock_prefix: '虚拟时间: ',
        clock_unset: '未设置',
        next_turn_prefix: '下一轮 +',
        next_turn_custom: '自定义…',
        next_turn_clear: '清除',
        custom_prompt: '输入时长 (例: 1h30m / 2d / 90s / 纯数字按秒):',
        bad_duration: '时长格式无法解析',
        role_prefix: '角色: ',
        mode_prefix: '模式: ',
        mode: {
          manual: '手动',
          simuser: '假想用户 (SimUser)',
          script: '脚本化 (Scripted)',
          // P12 上线后 script 已启用, 但保留 script_deferred key 以防老代码
          // 引用; 文案里明确注明已启用, 避免误导.
          script_deferred: '脚本化 (Scripted)',
          auto: '双 AI 自动 (Auto)',
          // P13 上线后 auto 已启用; 保留 auto_deferred key 以防老代码引用,
          // 文案同步更新到"已启用"版本, 不再暗示未接入.
          auto_deferred: '双 AI 自动 (Auto)',
          // P09 的单文案值, 保留以便其它模块引用.
          deferred: '(Auto)',
        },
        mode_deferred_hint: '',
        // SimUser (P11) 专用文案. style key 与后端 STYLE_PRESETS 键对齐; 如新增
        // 风格, 这里补 label + 后端预设同步.
        simuser: {
          style_prefix: '风格:',
          style: {
            friendly: '友好',
            curious: '好奇',
            picky: '挑剔',
            emotional: '情绪化',
          },
          persona_toggle: '自定义人设',
          persona_toggle_title: '展开一个文本框, 可在本次会话里临时追加\u300c额外人设/背景\u300d给 SimUser LLM. 留空则只用风格预设.',
          persona_placeholder: '例: 你是一位 30 岁的程序员, 对本次对话话题有专业背景但想从外行视角提问...',
          persona_intro: 'SimUser 在本会话内生效的"额外人设/背景". 不会写回任何持久配置, 切回手动模式或重建会话即清空.',
          generate: '生成',
          generating: '生成中…',
          generate_title: '调用假想用户 LLM 生成一条"下一轮要说的"用户消息草稿, 自动填进左侧 textarea. 不会落盘也不会推进虚拟时钟; 你可以继续编辑后点[发送]以 source=simuser 写入.',
          generate_failed: '假想用户生成失败',
          generated_ok: '假想用户草稿已生成',
          generated_empty: '假想用户返回了空字符串 (可能在扮演\u300c沉默\u300d)',
          confirm_overwrite: '当前 textarea 已有内容, 再次生成会覆盖. 继续?',
        },
        // Scripted (P12) 专用文案. script name / description 由模板 JSON 自带,
        // 这里只放 UI 控件的静态文案.
        script: {
          template_prefix: '剧本:',
          no_template_selected: '— 请选一个剧本 —',
          load: '加载',
          loading: '加载中…',
          unload: '卸载',
          unload_title: '清空当前会话的脚本状态 (不影响已产生的消息).',
          next: '下一轮',
          next_title: '推进一个 user turn: 消费脚本 time 字段推进时钟 → 发给目标 AI → 若紧邻有 role=assistant 的 expected, 自动回填到 AI 回复的 reference_content.',
          next_running: '运行中…',
          run_all: '跑完剩余',
          run_all_title: '循环 [下一轮] 直到脚本末尾或遇到错误. 期间 Chat 输入 / 假想用户均被锁定.',
          run_all_running: '跑完中…',
          refresh_templates: '刷新列表',
          refresh_title: '重新扫描 dialog_templates 目录 (builtin + user) 并刷新下拉.',
          load_title: '把指定剧本加载到当前会话, 并应用 bootstrap (若会话暂无消息).',
          loaded_toast: (name, count) => `剧本 ${name} 已加载, 共 ${count} 轮.`,
          unloaded_toast: '剧本已卸载.',
          exhausted_toast: '剧本已跑完.',
          exhausted_status: '已跑完',
          progress: (cursor, total) => `${cursor}/${total}`,
          no_template: '当前没有加载剧本, 请先在下拉里选一条并点 [加载].',
          no_session: '先在顶栏新建一个会话, 再加载剧本.',
          load_failed: '剧本加载失败',
          schema_invalid: '剧本 JSON schema 无效',
          not_found: '找不到指定的剧本模板',
          turn_failed: '脚本执行失败',
          bootstrap_skipped_title: '脚本里包含 bootstrap 但会话已有消息, 未重设时钟. 如果需要让 bootstrap 生效, 请先清空对话或重建会话再加载.',
          templates_empty: '(没有可用剧本)',
          source_builtin: '内置',
          source_user: '自定义',
          overriding_builtin: ' (覆盖同名内置)',
          description_prefix: '说明',
          persona_hint_prefix: '角色提示',
          turn_warning_title: '脚本 time 字段警告',
          ref_auto_filled: '脚本 expected 已回填到 AI 回复的 reference_content.',
        },
        // P13 双 AI 自动对话. style key 复用 simuser.style.* (上方已定义),
        // 这里只补模式自身的 UI 文案. Start / Pause / Resume / Stop 文案
        // 以 "控制按钮 + 进度横幅" 两个入口分别使用, 统一收进 auto.*.
        auto: {
          style_prefix: '风格:',
          persona_toggle: '自定义人设',
          persona_toggle_title: '展开 textarea, 为 Auto-Dialog 的 SimUser 一方追加"额外人设/背景"描述. 与 SimUser 模式的人设彼此独立 (切换 mode 不会互相覆盖).',
          persona_placeholder: '例: 你是一位内向的新手用户, 对话题好奇但对技术细节没信心...',
          persona_intro: 'Auto-Dialog 的 SimUser 人设; 仅对 "双 AI 自动" 模式生效, 不会影响手动 SimUser 模式.',
          total_turns_prefix: '轮数:',
          total_turns_title: '要跑的目标 AI 回复次数. 每条 assistant 回复算一轮, 跑完 N 条即结束.',
          step_mode_prefix: '时钟步长:',
          step_mode_title: 'fixed = 每轮前固定推进 step_seconds 秒; off = 整段不动虚拟时钟.',
          step_mode: {
            fixed: '固定',
            off: '不动',
          },
          step_seconds_unit: '秒',
          step_seconds_title: 'fixed 模式下每轮推进的秒数 (1 ~ 604800).',
          start: '启动 Auto',
          starting: '启动中…',
          start_title: '启动双 AI 自动对话: SimUser ↔ Target AI 交替生成 N 轮, 期间消息流与手动发送一致, 可随时通过顶部进度条暂停/停止.',
          running_hint: '运行中 · 见顶部进度条',
          start_failed: '启动失败',
          no_session: '先在顶栏新建一个会话, 再启动 Auto-Dialog.',
          no_style: '请先选一个 SimUser 风格',
          invalid_turns: '轮数必须在 1 ~ 50 之间',
          invalid_step_seconds: 'step_seconds 必须在 1 ~ 604800 之间',
          toast_started: (n, mode) => `Auto-Dialog 已启动, 共 ${n} 轮, 步长: ${mode}.`,
        },
        pending_absolute: (iso) => `Next turn → ${iso}`,
        pending_delta: (label) => `Next turn +${label}`,
        no_session: '先在顶栏新建一个会话.',
        stream_error: '流式发送中断',
        send_failed: '发送失败',
      },
      // P13 Auto-Dialog 顶部进度横幅. 只在有活跃 auto_state 时渲染.
      auto_banner: {
        label_running: 'Auto-Dialog 运行中',
        label_paused: 'Auto-Dialog 已暂停',
        label_stopping: '正在停止…',
        progress: (done, total) => `${done}/${total} 轮`,
        step_fixed: (seconds) => `步长 +${seconds}s`,
        step_off: '步长: 不动时钟',
        pause: '暂停',
        resume: '继续',
        stop: '停止',
        pause_title: '当前 step 跑完后不再进入下一轮. 已生成的消息保留.',
        resume_title: '继续跑剩余轮数.',
        stop_title: '停止 Auto-Dialog. 当前 step 结束后立即终止, 已生成的消息保留.',
        pause_failed: '暂停请求失败',
        resume_failed: '继续请求失败',
        stop_failed: '停止请求失败',
        stopped_toast: (reason, done, total) => {
          const reasonText = {
            completed: '正常跑完',
            user_stop: '手动停止',
            error: '出错中止',
          }[reason] || reason;
          return `Auto-Dialog 结束 (${reasonText}): ${done}/${total} 轮`;
        },
        error_title: 'Auto-Dialog 执行错误',
      },
      preview: {
        heading: 'Prompt 预览',
        refresh_btn: '刷新',
        view: {
          structured: '结构化视图',
          raw: '原始 wire',
        },
        status: {
          not_loaded: '尚未加载',
          click_to_load: '点击"刷新"以加载当前会话的 Prompt 预览.',
          loading: '加载中…',
          loaded: (ts) => `已刷新 @ ${ts}`,
          load_failed: '加载失败',
          no_session: '当前无活跃会话',
          not_ready: '会话尚未就绪',
          dirty: '会话状态有变动, 建议点击"刷新"重新拉取.',
        },
        empty: {
          no_session: '请先在顶栏新建一个会话, 才能预览 Prompt.',
          no_character: '当前会话的 persona 还没填 character_name. 去 Setup → Persona 补全后再回来刷新.',
          no_wire: 'wire_messages 为空 (异常情况, 至少应该有 system 消息).',
          error: '构建 Prompt 预览时出错, 详情见下方.',
        },
        meta: {
          character: '角色',
          master: '主人',
          language: '语言',
          template: '模板',
          template_default: '默认 (自动生成)',
          template_stored: '自定义 (persona)',
          system_chars: 'system 字符数',
          approx_tokens: '估算 tokens',
          virtual_now: '虚拟时间',
        },
        // 结构化视图各分节标题. key 与 PromptBundle.structured 对齐.
        section: {
          session_init:           'session_init (会话起始提示)',
          character_prompt:       'character_prompt (角色 system_prompt)',
          persona_header:         'persona_header (长期记忆标题)',
          persona_content:        'persona_content (长期记忆正文)',
          inner_thoughts_header:  'inner_thoughts_header (内心活动标题)',
          inner_thoughts_dynamic: 'inner_thoughts_dynamic (当前时间注入)',
          recent_history:         'recent_history (最近对话记录)',
          recent_history_empty:   '(无最近对话)',
          time_context:           'time_context (距上次对话提示)',
          holiday_context:        'holiday_context (节日/假期提示)',
          closing:                'closing (context summary ready)',
        },
        hint: {
          structured: '⚠ 本视图仅拆解【首轮初始 system_prompt】的组成 (session_init → character_prompt → persona → inner_thoughts → recent_history → time/holiday → closing), 不含后续轮次的 user / assistant / 注入 system 消息. 要看真正完整发给 AI 的对话流水, 请切到"原始 wire". 各分节独立折叠, Alt+点击 可一次展开/折叠全部.',
          raw: '这是真正送到 AI 的 messages 数组: messages[0] 即首轮初始 system_prompt 的扁平串接 (session_init + character_prompt + memory_flat + closing), 后续每条 user / assistant / 注入 system 都按本轮对话顺序排列. 发送 / 注入 / 编辑消息完成后本视图会自动刷新 (~200ms).',
        },
        length_badge: (n) => `${n} 字符`,
        recent_summary: (n) => `共 ${n} 条`,
        recent_badge: (count, chars) => `${count} 条 / ${chars} 字符`,
        warnings_heading: (n) => `预览提示 (${n})`,
        wire: {
          title: (idx, role) => `messages[${idx}] · ${role}`,
        },
        copy_wire_json: '复制 messages JSON',
        copy_system_string: '复制 system 字符串',
        copied_wire: '已复制 wire_messages JSON',
        copied_system: '已复制 system 字符串',
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
          api_key: '留空 = 用预设/registry 兜底 (免费预设可留空)',
          model: '如 qwen-plus / gpt-4.1-mini',
          temperature: '留空 = 由模型端自决',
          max_tokens: '留空 = 不限制',
          timeout: '留空 = 60',
        },
        hint: {
          // 三个可选数值字段的行内提示. 明确"留空 = 不发送此参数给模型端"
          // 的语义, 避免用户以为空=0 或空=默认 1.0. 特别点出 o1/gpt-5-thinking
          // 这类拒绝 temperature 的模型必须留空.
          temperature: '留空表示不把 temperature 字段写进请求体 (让模型端用自己的默认值). o1 / o3 / gpt-5-thinking / Claude extended-thinking 等拒绝该参数的模型, 必须留空.',
          max_tokens: '留空表示不限制输出长度, 由模型自行决定; 填正整数则作为硬上限.',
          timeout: '客户端侧 httpx 超时, 不会发送给模型端. 流式长输出建议 ≥ 60s.',
        },
        api_key_status: {
          configured: '已配置 (请求时使用)',
          bundled_by_preset: '此预设内置 API Key (如免费版), 无需填写',
          from_preset: name => `将使用 tests/api_keys.json 中的 ${name}`,
          missing: '未配置, 请填入或去 API Keys 页面补',
        },
        toast: {
          switched_manual: '已切换到自定义模式',
          applied: name => `已应用预设: ${name}`,
          applied_free: name => `已应用预设: ${name} (免费版, API Key 自动兜底, 可直接测试)`,
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
          not_configured_hint: 'base_url / model 必填; api_key 若留空将使用预设 / registry 兜底',
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
    model_reminder: {
      dismiss_hint: '本次服务运行期不再提醒 (服务重启后会再出现)',
      welcome: {
        title: '欢迎使用 N.E.K.O. Testbench — 先配置 AI 模型',
        body:
          '新建会话后, chat / simuser / judge / memory 四组 AI 模型默认都是空的, '
          + '后续生成 persona、假想用户回复、对话、记忆摘要、评分都要走 LLM, '
          + '所以请先去 Settings → Models 给至少 chat / simuser / judge 三组配上模型. '
          + '如果你还没填过 provider 的 API Key, 请先编辑 `tests/api_keys.json`, '
          + '或挑一个 `is_free_version` 的免费 provider 直接用.',
        goto_btn: '去 Settings → Models',
      },
      warn: {
        title: '请先配置 AI 模型 API Key',
        body:
          '测试流程 (生成 persona / 假想用户回复 / 对话 / 评分等) 都要调用 LLM. '
          + '当前没有任何可用的 provider — 请编辑 `tests/api_keys.json` 和/或 '
          + '`tests/api_providers.json`, 然后去 Settings → API Keys 点 [Reload] 让后端重读.',
        goto_btn: '去 Settings → API Keys',
      },
    },
    stage: {
      name: {
        persona_setup: '人设准备',
        memory_build: '记忆搭建',
        prompt_assembly: 'Prompt 组装',
        chat_turn: '对话轮次',
        post_turn_memory_update: '轮后记忆更新',
        evaluation: '评分',
      },
      name_short: {
        persona_setup: '人设',
        memory_build: '记忆',
        prompt_assembly: 'Prompt',
        chat_turn: '对话',
        post_turn_memory_update: '记忆更新',
        evaluation: '评分',
      },
      op: {
        persona_edit: {
          label: '去 Setup → Persona 编辑人设',
          description:
            '六阶段里最早的一步: 配置角色名 / 用户名 / 基础人设文本. '
            + '只要人设为空或未保存, system prompt 拼不出来, 后面所有环节都会报 "PreviewNotReady".',
          when_to_run:
            '首次建会话 / 换人设 / 想让 SimUser 有针对性的人设风格时. '
            + '"执行并推进" 会把阶段切到 memory_build 同时跳到 Setup → Persona 子页, 请手动填写并 Save 后再回来点 "执行并推进" 推进到下一阶段.',
          when_to_skip:
            '人设已经填过 (context 面板里 persona_configured=true) / 想跳过使用**空白人设**模拟最原始对话时可以 skip.',
        },
        memory_edit: {
          label: '去 Setup → Memory 填写初始记忆',
          description:
            '给角色配置初始的 recent / facts / reflections / persona 记忆. '
            + '记忆为空时 Lanlan 也能跑对话, 但测试 "记忆影响回复" 这类场景需要先造数据.',
          when_to_run:
            '准备测试记忆召回 / 事实矛盾 / 反思触发等场景时. '
            + '记忆条目数见下方 context 面板 (memory_counts). 已有足够数据时可 skip.',
          when_to_skip:
            '测试零记忆冷启动, 或完全依赖对话运行时动态生成的记忆 (走 post_turn_memory_update 流程)时.',
        },
        prompt_preview: {
          label: '去 Chat 右栏预览 wire messages',
          description:
            '在 Chat workspace 右栏查看**真正要发给 LLM** 的 wire messages, '
            + '确认 system prompt + 历史 + 记忆注入拼接符合预期再发.',
          when_to_run:
            '改完人设 / 记忆 / 模型配置后第一次发消息前**强烈建议看一眼**, '
            + '否则容易把错误 prompt 发出去然后推断 "模型不行".',
          when_to_skip:
            '确信当前拼接逻辑没变过 (只是再发一轮) 时可以直接 skip 到 chat_turn.',
        },
        chat_send: {
          label: '在 Chat 发送一条消息',
          description:
            '任一对话模式均可: 手动 (自己打) / SimUser (AI 模拟用户) / '
            + 'Scripted (加载剧本) / Auto-Dialog (双 AI 自动跑). 每种模式自身也可以无限反复地发.',
          when_to_run:
            '前面几步就绪后正常进入对话阶段. 注意: **点 "执行并推进" 不会自动发消息**, '
            + '只是把阶段切到 post_turn_memory_update; 实际发消息请在 Chat workspace 的 Composer 里操作.',
          when_to_skip:
            '用不上对话 (比如只想测人设加载) 时可 skip, 但通常不会.',
        },
        memory_trigger: {
          label: '去 Setup → Memory 触发一次记忆 op',
          description:
            '对话之后的记忆合并/抽取/反思: recent.compress (压缩历史为摘要) / '
            + 'facts.extract (从对话抽取事实) / reflect (反思) / persona.add_fact / resolve_corrections. '
            + '每个 op 都是 "Trigger → 预览 drawer → Accept" 的确认流程, 绝不自动写盘.',
          when_to_run:
            '对话累积到想要压缩或抽事实时. 下方 context 面板里:'
            + ' messages_count 多 → 可能该 recent.compress;'
            + ' pending_memory_previews 非空 → 已有未 Accept 的 op 预览在等,'
            + ' 先去 Memory 子页处理那几个.',
          when_to_skip:
            '想继续跑对话不做记忆更新时 skip. 注意 skip 只切阶段,'
            + ' 不会清任何现有 pending 预览.',
        },
        evaluation_pending: {
          label: '评分 (P15/P16 未上线)',
          description:
            'ScoringSchema 一等公民 + 四类 Judger 分别在 P15 / P16 阶段落地. '
            + '届时此阶段会提供: Absolute/Comparative 双轴 × 单消息/整段对话 粒度 × 多 schema 切换 的评分入口.',
          when_to_run:
            '尚未上线. 现在点 "执行并推进" 只会把阶段循环回 chat_turn, 继续下一轮对话.',
          when_to_skip:
            '与 "执行并推进" 等效 (都只切阶段不跑评分). 可以视心情任选.',
        },
      },
      chip: {
        collapsed_prefix: stageShort => `阶段: ${stageShort}`,
        no_session: '阶段: (未建会话)',
        expand_hint: '点击展开 Stage Coach',
        collapse_hint: '点击折叠',
        expanded_default_tip: '本 workspace 下 Stage Coach 默认展开. 点右上 × 可折叠.',
      },
      panel: {
        intro_title: '这是一个帮手, 不是流程警察',
        intro_body:
          'Stage Coach 只是把测试流程做成了一张**可选的 checklist**, '
          + '同时**收集一些数据** (消息数 / 各类记忆条目数 / 虚拟时钟 / '
          + '未确认的 memory op 等) 放在下方"当前上下文"面板里, 帮你判断**现在该做什么/能不能跳过**.\n'
          + '它**不会自动跑任何 op** — 发消息 / 编辑人设 / 跑 memory 压缩 / 评分, '
          + '全都要你自己在对应的 workspace 里点按钮.\n'
          + '"执行并推进" 和 "跳过" **只改阶段指针** (以及跳转到相关子页), '
          + '完全不会动对话记录 / 记忆 / 虚拟时钟; "回退" 同理, 只让你回到某个阶段的 UI 视角, '
          + '既不会撤销已发消息也不会清记忆. 所以随便点, 出错也没副作用.',
        stage_bar_title: '流水线阶段',
        op_card_title: '下一步推荐',
        when_to_run: '什么时候该跑',
        when_to_skip: '什么时候可以跳过',
        context_title: '当前上下文 (帮你判断该不该跑)',
        history_title: '最近动作',
        history_empty: '暂无记录',
      },
      context: {
        messages_count: count => `消息数: ${count}`,
        messages_split: (user, asst) => `(user ${user} · assistant ${asst})`,
        last_message: role =>
          role ? `末尾消息角色: ${role}` : '末尾消息: (无)',
        memory_counts: c =>
          `记忆: recent ${c.recent} · facts ${c.facts}`
          + ` · reflections ${c.reflections} · persona_facts ${c.persona_facts}`,
        persona_configured: ok =>
          ok ? '人设已配置 ✓' : '人设未配置',
        pending_previews_none: '暂无记忆 op 待确认',
        pending_previews: ops => `记忆 op 待确认: ${ops.join(', ')}`,
        script_loaded: ok => ok ? '已加载脚本' : '未加载脚本',
        auto_running: ok => ok ? 'Auto-Dialog 运行中' : 'Auto-Dialog 未运行',
        virtual_now: t => `虚拟时钟: ${t || '(未设)'}`,
        pending_advance: s =>
          s == null ? '无暂存时长' : `下轮暂存推进 ${s} 秒`,
        warnings: ws =>
          ws.length === 0 ? '' : `(采集警告: ${ws.join(', ')})`,
      },
      buttons: {
        preview: '预览 dry-run',
        preview_disabled_hint:
          '本阶段推荐不提供内置 dry-run: memory op 请去 Setup → Memory 的 Trigger 按钮, 评分等 P15/P16.',
        advance: '执行并推进',
        skip: '跳过',
        rewind_open: '回退…',
        rewind_apply: '跳到此阶段',
        go_target: '跳转到目标页',
        collapse: '折叠',
      },
      action: {
        nav_persona: '已跳转到 Setup → Persona, 请填写并保存后回来点 "执行并推进"',
        nav_memory: '已跳转到 Setup → Memory 子页',
        nav_chat_preview: '已跳转到 Chat, 请在右栏确认 wire messages',
        nav_chat_send: '已跳转到 Chat, 请在 Composer 里发送消息',
        evaluation_pending_toast: 'P14 暂不提供评分 dry-run; 点 "执行并推进" 会循环回 chat_turn',
      },
      toast: {
        advance_ok: (from, to) => `阶段推进: ${from} → ${to}`,
        skip_ok: (from, to) => `阶段跳过: ${from} → ${to} (history 标 skipped)`,
        rewind_ok: (from, to) => `阶段回退: ${from} → ${to} (数据未改)`,
        advance_failed: '阶段推进失败',
        fetch_failed: '读取 stage 状态失败',
        no_session: '请先建会话',
        preview_unsupported: 'P14 范围内此 op 不提供 dry-run, 详见推荐说明',
      },
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
