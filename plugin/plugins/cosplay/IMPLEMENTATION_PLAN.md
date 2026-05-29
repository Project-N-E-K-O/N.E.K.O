# Cosplay 插件实施计划

> 基于 14 项决策，分 7 个阶段推进
> 项目路径：`plugin/plugins/cosplay/` + `plugin/plugins/cosplay_plugin/`

---

## 总览

```
Phase 1  地基重构（类名/文件名/引用清理）
Phase 2  数据架构（存储方案 + 作品集框架）
Phase 3  三要素系统（角色 + 服装 + 场景）
Phase 4  剧场模式重构（新剧本引擎）
Phase 5  互动模式（实时对话 + 每轮生图）
Phase 6  Web UI（仪表盘 + 剧场 + 互动）
Phase 7  完善（导出/标签/收藏/打磨）
```

依赖关系：Phase 1 → 2 → 3 → 4/5（可并行）→ 6 → 7

---

## Phase 1：地基重构

**目标**：所有 Galgame 前缀改为 Cosplay，代码干净一致。

### 1.1 类名重构（cosplay_plugin）
- `GalgamePlugin` → `CosplayPlugin`
- `GalgameBridgePlugin` → `CosplayBridgePlugin`
- `GalgamePluginConfigService` → `CosplayPluginConfigService`
- `GalgameLLMConfig` → `CosplayLLMConfig`
- `GalgameSharedState` → `CosplaySharedState`
- 其他 `Galgame*` 类名统一替换
- 涉及文件：~109 个 .py 文件

### 1.2 文件名重构（plugin_entries/）
- `galgame_agent_command.py` → `cosplay_agent_command.py`
- `galgame_bind_game.py` → `cosplay_bind_game.py`
- `galgame_explain_line.py` → `cosplay_explain_line.py`
- ... 共 40+ 个文件
- 同步更新所有 `import` 引用

### 1.3 配置键名重构
- `plugin.toml` 中 `[galgame]` section → `[cosplay]`（已做）
- `store_keys.py` 中的键名前缀更新
- `models/config.py` 中的配置字段更新

### 1.4 日志/错误信息清理
- 所有 `logger` 名称从 `galgame.*` → `cosplay.*`
- 错误提示中的 galgame 文案改为 cosplay

### 1.5 验证
- `grep -rn "galgame\|Galgame" cosplay/ cosplay_plugin/` 应返回 0 结果
- plugin.toml entry point 能正确解析
- 插件可加载启动（不崩溃）

**预估工作量**：中等（2-3 小时，大部分是机械替换 + 逐一验证）

---

## Phase 2：数据架构

**目标**：建立 PluginStore + JSON 作品文件夹的混合存储。

### 2.1 作品目录结构设计
```
data/
  works/                          # 所有作品
    {work_id}/                    # 每个作品一个文件夹
      meta.json                   # 元信息（标题、时间、标签、收藏）
      config_snapshot.json        # 角色+服装+场景配置快照
      script.json                 # 剧本/对话记录
      assets/                     # 生成的图片
        scene-001.png
        scene-002.png
        ...
      exports/                    # 导出文件（后续）
  character_library/              # 角色库
    {character_id}/
      character.json              # 角色定义（三要素）
      reference/                  # 参考图片
        ref-001.jpg
        ...
  templates/                      # 预设模板
    templates.json
```

### 2.2 PluginStore 键设计
```python
# 存储在 PluginStore 中（轻量配置）
cosplay_current_work_id        # 当前活跃作品 ID
cosplay_character_library_index # 角色库索引（快速检索用）
cosplay_model_config           # AI 模型配置（已有，重命名）
cosplay_role_config            # 角色配置（已有，重命名）
cosplay_ui_preferences         # UI 偏好设置
cosplay_works_index            # 作品索引（标题+时间+标签摘要）
```

### 2.3 作品管理模块
- 新建 `cosplay_plugin/work_manager.py`
  - `create_work()` → 创建作品文件夹 + meta.json
  - `list_works()` → 读取索引，返回作品列表
  - `get_work(work_id)` → 读取作品详情
  - `update_work_meta()` → 更新元信息
  - `delete_work()` → 删除作品
  - `add_asset_to_work()` → 往作品中添加生成的图片
  - `add_dialogue_to_work()` → 往作品中追加对话记录

### 2.4 角色库模块
- 新建 `cosplay_plugin/character_library.py`
  - `create_character()` → 创建角色（三要素）
  - `list_characters()` → 列出所有角色
  - `get_character()` → 获取角色详情
  - `update_character()` → 更新角色
  - `delete_character()` → 删除角色
  - `add_reference_image()` → 添加参考图

### 2.5 模板系统
- 新建 `cosplay_plugin/template_manager.py`
  - 内置模板：日系女仆、汉服少女、机甲战士、魔法少女、和风巫女...
  - `list_templates()` → 列出模板
  - `apply_template()` → 应用模板到角色定义

**预估工作量**：中等（3-4 小时）

---

## Phase 3：三要素系统

**目标**：角色 + 服装 + 场景的组合输入系统。

### 3.1 三要素数据模型
```python
# cosplay_plugin/models/character.py
@dataclass
class CosplayCharacter:
    id: str
    name: str                           # 角色名
    description: str                    # 自由文字描述
    structured: StructuredAttributes    # 结构化属性
    costume: Costume                    # 服装定义
    scene: SceneDefinition              # 场景定义
    reference_images: list[str]         # 参考图路径
    template_id: str | None             # 来源模板 ID

@dataclass
class StructuredAttributes:
    gender: str                         # 性别
    hair_style: str                     # 发型
    hair_color: str                     # 发色
    body_type: str                      # 体型
    age_range: str                      # 年龄段
    personality: str                    # 性格关键词

@dataclass
class Costume:
    name: str                           # 服装名称
    style: str                          # 风格（和风/洋装/现代/奇幻...）
    description: str                    # 自由描述
    colors: list[str]                   # 主色调
    accessories: list[str]              # 配饰
    reference_images: list[str]         # 服装参考图

@dataclass
class SceneDefinition:
    name: str                           # 场景名
    environment: str                    # 环境（室内/室外/幻想）
    location: str                       # 具体地点描述
    time_of_day: str                    # 时间（白天/黄昏/夜晚）
    weather: str                        # 天气
    mood: str                           # 氛围
    description: str                    # 自由描述
    reference_images: list[str]         # 场景参考图
```

### 3.2 Prompt 组装器
- 新建 `cosplay_plugin/prompt_assembler.py`
- 核心函数：`assemble_image_prompt(character, costume, scene, action, camera) → str`
- 将三要素结构化属性 + 自由文字 + 参考图分析 → 组装为生图 prompt
- 支持不同风格的 prompt 模板（写实/动漫/水墨等）

### 3.3 参考图理解
- 利用 DashScope 的多模态能力分析参考图
- 提取关键视觉特征补充到 prompt 中
- 新建 `cosplay_plugin/reference_analyzer.py`

**预估工作量**：中等（3-4 小时）

---

## Phase 4：剧场模式重构

**目标**：重新设计剧本引擎，原生支持三要素、多人同框、动作描述、镜头指令。

### 4.1 新剧本格式设计
```
【角色】
  雪乃：黑长直少女，温柔内敛
  服装：白色和服，蓝色腰带，木屐
  场景：黄昏，樱花神社，花瓣飘落

  悠太：棕发少年，开朗
  服装：黑色校服，领带松散
  场景：同上

【第1幕：相遇】
  [镜头：全景]
  [场景：神社鸟居前，夕阳西下]
  [动作：雪乃站在鸟居下，悠太从远处跑来]

  雪乃：你又迟到了。
  悠太：抱歉抱歉！路上遇到只猫...
  [动作：悠太双手合十道歉]
  [镜头：特写，雪乃微微皱眉]

  雪乃：（内心）每次都这样，但看到他的笑脸就气不起来了。

【第2幕：...】
```

### 4.2 剧本解析器重写
- 重写 `cosplay_plugin/script_parser.py`
- 支持的语法：
  - `【角色】` 块：角色定义 + 服装 + 场景
  - `【第N幕：标题】` 块：场景划分
  - `[镜头：XXX]` 指令：全景/特写/远景/仰拍/俯拍
  - `[场景：XXX]` 指令：场景切换
  - `[动作：XXX]` 指令：角色动作描述
  - `角色名：台词` 对话
  - `（内心）` 旁白/内心独白
  - `#旁白#` 纯旁白文本

### 4.3 分镜引擎重写
- 重写 `cosplay_plugin/storyboard_engine.py`
- 每个分镜节点包含：
  ```python
  @dataclass
  class StoryboardNode:
      scene_id: int
      camera: str              # 镜头指令
      scene_desc: str          # 场景描述
      actions: list[Action]    # 动作列表
      dialogues: list[Dialogue] # 对话列表
      narration: str           # 旁白
      characters: list[str]    # 出场角色
      image_prompt: str        # 组装后的生图 prompt
      image_url: str           # 生成的图片
      mood: str                # 氛围
  ```

### 4.4 生图 Prompt 组装
- 三要素 + 镜头指令 + 动作描述 → 完整 prompt
- 支持多角色同框 prompt 构造
- 调用 DashScope 生图

### 4.5 舞台演出逻辑
- 适配新的 StoryboardNode 结构
- 支持旁白/内心独白的特殊显示样式
- 支持动作描述的展示

**预估工作量**：大（6-8 小时，核心重写）

---

## Phase 5：互动模式

**目标**：1v1 实时对话 + 每轮 AI 生图。

### 5.1 互动引擎
- 新建 `cosplay_plugin/interactive_engine.py`
- 核心类 `CosplayInteractiveEngine`
  - `start_session(character, costume, scene, goal=None)` → 开始互动
  - `send_message(user_text)` → 用户发消息，返回 AI 回复 + 图片
  - `set_goal(goal_text)` → 设置情境目标
  - `clear_goal()` → 清除目标，回到自由对话
  - `get_history()` → 获取对话历史
  - `end_session()` → 结束，归档为作品

### 5.2 LLM 对话管理
- 角色人设注入 system prompt
- 三要素描述作为角色背景
- 情境目标作为额外约束
- 对话历史管理（上下文窗口）
- 每轮提取场景描述用于生图

### 5.3 每轮生图流程
```
用户发消息 → LLM 回复文字 + 输出场景描述
  → prompt_assembler 组装生图 prompt
  → DashScope 生图
  → 返回 {text: "回复", image_url: "...", scene_desc: "..."}
```

### 5.4 互动归档
- 互动结束时自动创建作品
- 对话记录保存为 script.json
- 每轮生成的图片保存到 assets/
- 作品元信息标记 mode="interactive"

**预估工作量**：大（5-7 小时）

---

## Phase 6：Web UI

**目标**：仪表盘 + 剧场界面 + 互动界面。

### 6.1 仪表盘首页
- 最近作品列表（卡片式，显示封面图 + 标题 + 时间）
- 快速入口：「开始剧场」「开始互动」
- 角色卡片：已创建的角色缩略展示
- 作品统计：总数、本月创作数

### 6.2 剧场界面
- 剧本编辑区（支持新格式语法高亮）
- 预览区（实时显示分镜画面）
- 演出控制栏（播放/暂停/上一幕/下一幕）
- 三要素配置面板（侧边栏）

### 6.3 互动界面
- 对话区（聊天气泡 + AI 生成的场景图）
- 角色信息栏（当前角色三要素摘要）
- 输入框 + 发送
- 目标设置入口（可选）

### 6.4 作品集浏览
- 作品列表（按时间/标签/收藏筛选）
- 作品详情页（元信息 + 对话记录 + 图片画廊）
- 导出按钮（Phase 7 实现）

### 6.5 角色库管理
- 角色列表 + 创建/编辑/删除
- 三要素编辑表单（结构化字段 + 自由文字 + 参考图上传）
- 模板选择器

**预估工作量**：大（8-10 小时，前端工作量大）

---

## Phase 7：完善

**目标**：导出、标签、收藏、打磨。

### 7.1 导出功能
- 导出为 HTML（自包含页面，内嵌图片）
- 导出为图片集（ZIP 打包）
- 导出为 PDF（可选，需要额外依赖）

### 7.2 标签系统
- 作品支持自定义标签
- 按标签筛选作品
- 自动标签建议（基于角色/场景/风格）

### 7.3 收藏/置顶
- 作品可标记收藏
- 收藏作品置顶显示
- 收藏筛选

### 7.4 画风配置
- 支持多种画风选择（写实/动漫/水墨/像素/油画）
- 画风作为生图 prompt 的一部分
- 用户可自定义画风 prompt 片段

### 7.5 打磨
- 错误处理完善
- 加载状态优化
- 边界情况处理
- 性能优化（图片懒加载等）

**预估工作量**：中等（4-5 小时）

---

## 工作量总览

| 阶段 | 工作量 | 核心产出 |
|------|--------|---------|
| Phase 1 | 2-3h | 干净的 Cosplay 命名空间 |
| Phase 2 | 3-4h | 数据层 + 作品管理 + 角色库 |
| Phase 3 | 3-4h | 三要素模型 + Prompt 组装器 |
| Phase 4 | 6-8h | 新剧本引擎（最大改动） |
| Phase 5 | 5-7h | 互动模式完整可用 |
| Phase 6 | 8-10h | 全套 Web UI |
| Phase 7 | 4-5h | 导出 + 标签 + 收藏 + 打磨 |
| **合计** | **31-41h** | **完整 cosplay 插件** |

## 建议执行顺序

```
Phase 1（地基）→ Phase 2（数据）→ Phase 3（三要素）
  → Phase 4（剧场）和 Phase 5（互动）可并行
  → Phase 6（UI）→ Phase 7（完善）
```

Phase 1 是纯机械工作，可以快速完成。
Phase 2-3 是架构层，做好了后面才顺。
Phase 4-5 是核心功能，工作量最大。
Phase 6 是前端，依赖后端 API 稳定。
Phase 7 是锦上添花，可以逐步迭代。
