# Avatar 自定义道具设计与维护规范

本文是本地自定义 Avatar 道具 v0.1 的长期设计与维护入口。v0.1 已跑通用户在 Compact 中创建、装备、使用、修改和删除道具的本地最小闭环，并接入 Web、NEKO-PC、Host、Python 提示词和应用存储生命周期。

本文只描述当前已经实现并需要长期保持的产品语义、代码边界和维护规则，不记录实施阶段、临时调试过程或未来设想。若本文与当前代码、测试或可复现运行结果冲突，以可复现证据和当前代码为准，并同步修正文档。

通用道具输入、definition、desktopContract 和运行时规则由以下文档继续统一维护，本文不复制另一套规则：

- `docs/design/avatar-tool-interaction-design-and-maintenance.md`
- `docs/design/avatar-tool-prompt-guidelines.md`

产品版本 v0.1 与持久化结构的 `recordVersion: 2` 是两个不同概念：前者表示当前功能范围，后者只表示本地记录结构版本。

## v0.1 产品边界

### 已支持

- 用户在 Compact 的现有“管理道具”内创建本地自定义道具。
- 道具包含名称、默认图片、切图方式，以及一张或多张与互动描述一一对应的变化图片。
- 支持两种切图方式：
  - `press-swap`：按下临时切换，松开或取消后恢复默认图片；
  - `click-advance`：有效点击后依次前进，到最后一张保持，不循环。
- 可选普通互动 MP3。
- 可选概率彩蛋：概率、彩蛋图片、彩蛋互动描述和可选彩蛋 MP3。
- 创建成功后进入现有道具库，由用户按现有三槽逻辑装备，不自动装备。
- Compact 使用同一表单修改已有道具；保存修改保持同一个本地 ID。
- 删除入口只位于修改页，删除后清理该道具的记录、资源、当前使用态和当前 surface 槽位。
- Full 与 Compact 加载同一权威本地道具目录；Full 只允许查看、装备和使用，不提供创建、修改或删除入口。
- Web 与 Electron Pet 使用同一 v2 definition 语义；Host/Python 根据权威记录选择本次图片对应的互动描述。
- 本地目录随应用存储根迁移，但不进入云存档。

### 明确不支持

- Full 创建、修改或删除。
- 预览区、试听按钮、音量设置、独立编辑器、自动保存或可恢复草稿。
- 复制、版本历史、导入包、导出包、分享、市场、云同步或跨设备同步。
- Full／Compact 活动槽位同步。
- 用户配置命中范围、连续阈值、动作、效果类型、anchor、hotspot、窗口行为、协议字段或任意脚本。
- HTML、SVG、APNG、动画代码、网络图片 URL 或任意文件路径。
- 为自定义道具重建 Manager、三槽、pointer runtime、播放器、效果调度、模型回复或 memory 系统。
- 修改四个内置道具的 definition v1、资源或行为。

新增能力必须延续现有窄链；不能因为未来可能增加模式而预建通用脚本系统、可配置状态机或第二套运行时。

## 用户流程与页面职责

### 创建

1. 用户在 Compact 打开“管理道具”。
2. 道具库最后显示一个创建加号。
3. 点击加号后，Manager 内部切换为创建表单，不打开新窗口。
4. 用户填写名称、默认图片，并选择“按住时切换”或“点击后切换”。
5. 每一张变化图片与自己的互动描述在同一区块内紧邻显示。
6. 用户可以选择普通互动 MP3，也可以开启并填写完整彩蛋配置。
7. 保存失败时保留已填内容，并把错误显示、滚动和聚焦到对应字段。
8. 保存成功后返回道具库，新道具位于创建加号之前，但不自动装备。
9. 用户继续使用现有三槽行为装备、移除、排序并保存。

创建页字段顺序保持为：

```text
道具名称
默认图片
切图方式
变化图片与互动描述
普通互动音效（可选）
彩蛋（可选）
返回 / 保存道具
```

底部操作固定在页面下方。只有中间表单内容确实超过可用高度时才内部滚动；默认单项布局不应出现无意义滚动条。

### 修改与删除

- Compact 自定义道具卡片只有一个修改入口；卡片主体继续负责装备。
- 修改入口必须阻止卡片装备和拖拽事件。
- 修改页复用创建表单，不复制第二套字段、校验或布局。
- 修改页完整带入当前名称、模式、图片、逐图描述、普通音效和彩蛋。
- 未重新选择图片或音效表示保留；可选音效只能通过显式移除操作删除。
- 返回、关闭或保存失败只丢弃本次内存改动，不修改权威记录。
- 保存修改继续使用原 `local-<uuid-v4>`，槽位和顺序保持不变。
- 删除按钮只位于修改页底部，并经过二次确认；创建页、管理页卡片、内置道具和 Full 均不显示删除入口。

### Full／Compact

- 两个 surface 都读取同一个后端本地目录并构建自己的动态 registry snapshot。
- 两个 surface 分别保存最多三个活动槽位。桌面 Full 使用 `persist:neko-full-chat` partition，Compact 使用默认 partition；相同 storage key 不代表共享选择。
- 创建、修改或删除不会主动覆盖另一 surface 的槽位。
- 隐藏 surface 在重新获得 lease 或刷新权威目录时，按当前 ID 和资源版本保留、刷新或清理自己的状态。

## 表单语义与校验

### 切图方式

`press-swap` 与 `click-advance` 是两份独立编辑内容：

- `press-swap` 必须且只能有一组变化图片和互动描述，不显示添加、删除或排序控件。
- `click-advance` 必须至少有一组，可添加到后端公布的 `maxChangeImages` 上限，并支持替换、删除、上移和下移。
- 两种方式切换时不转换、覆盖或删除另一方式已经填写的内容；保存时只提交当前选中的方式。
- 不提供“所有图片共用一段描述”的第二种数据关系。需要相同描述时，用户可分别填写相同内容。

默认图片只表示未触发切图时显示的帧。进入或离开有效互动范围只改变当前帧的显示大小，不改变图片。

### 文本

- 名称必填。NFC 归一化、去除首尾空白并合并连续半角空格后长度为 `1–20` 个字符。
- 名称只允许 Unicode 文字、数字、半角空格、`-` 和 `_`；不允许换行、emoji、控制字符或其它符号。
- 名称只用于显示，不参与本地 ID，允许重名。
- 每段变化图片互动描述必填，去除首尾空白后长度为 `1–100` 个字符。
- 彩蛋开启时，彩蛋互动描述必填并使用相同长度与控制字符规则。
- 互动描述允许正常标点和换行；它是模型理解本次互动的数据，不是脚本或指令。
- 前端只负责即时提示，后端始终执行最终权威校验。

### 图片与音频

- 图片只接受真实、可完整解码、非动画且不是完全透明的 PNG。
- 后端使用 Pillow 校验格式、帧数、像素上限和完整解码，并重新编码为静态 RGBA PNG；原始上传和规范化输出都必须满足单图字节上限。
- 音频只接受真实可解码、包含音频流且不超过时长和大小上限的 MP3；使用 PyAV 校验。
- 自定义道具的 multipart mutation 必须在 FastAPI 解析表单前完成 loopback、CSRF/origin 与请求聚合体积守门；缺失或低报 Content-Length 的流式超限也必须由外层守门返回统一 413 并关闭未读连接，不能被 FastAPI 内层异常响应替换；endpoint 仍按文件读取并复验单文件上限、格式和内容，不能只依赖全局非 multipart body cap。
- 单图大小、像素、音频大小和时长、单道具变化图片数量、有效可见道具数量和本地总占用由后端 `AVATAR_TOOL_LIMITS` 唯一定义；被证伪的记录既不占有效道具数量也不占总占用，但只是暂时读不出来的必须照常占名额（判据见「权威存储与 API」）。前端读取 limits 展示，不能成为权威来源。
- 用户原文件名不进入记录或资源路径。

### 彩蛋

- 彩蛋是一个完整可选块，关闭时不保存概率、图片、描述或音效，也不执行 RNG。
- 概率由 `1%–100%`、步进 `1%` 的滑杆选择，默认 `10%`；记录中保存为 `0 < probability <= 1` 的小数。
- 彩蛋命中时使用现有 `random-scatter` 效果。
- 声音选择顺序固定为：彩蛋音效 → 普通音效 → 静默。
- 每次有效互动最多播放一次选定声音。

## 权威存储与 API

### 本地目录

自定义道具保存在 `ConfigManager.app_docs_dir` 下，不写入仓库、浏览器 localStorage 或 Electron partition：

```text
app_docs_dir/
  avatar_tools/
    local-<uuid-v4>/
      record.json
      default.png
      change-000.png
      change-001.png
      normal.mp3       # 可选
      special.png      # 彩蛋存在时
      special.mp3      # 可选
```

必须保持以下不变量：

- ID 由 Compact 创建表单在一次创建会话开始时生成，严格为 `local-<lowercase-uuid-v4>`；不向用户显示或开放输入。同一会话的保存重试复用该 ID，删除后的新建会话重新生成；不为已删除 ID 增加永久 tombstone。
- 每个道具独占一个目录，所有资源只属于该道具。
- 记录只引用应用生成的同目录相对文件名，不接受绝对路径、`..`、软链接或其它道具的资源。
- 资源顺序来自 record 的有序列表，不依赖目录枚举或用户文件名。
- 目录实际文件必须与 record 引用形成严格闭包；缺失、重复声明之外的多余文件或私有记录暴露均应拒绝。
- record 保存每个受管理资源的 SHA-256。逐字节核验发生在真正消费资源的地方——详情／修改、互动落库前的权威读取——这些入口只接纳与摘要一致的资源。公开目录列表本身只做轻量校验（记录形状、资源存在、摘要键集合、资源闭包），因为前端每次窗口聚焦都会拉取它，逐字节重算会让列表开销随总字节数增长。轻量校验不得被当成核验的替代：任何入口都不能以大小、时间戳或其它文件元数据判定资源完好。
- **隔离的判据是「记录已被证伪」，不是「摘要对不上」**。摘要不符、资源大小越界、闭包不符、JSON 非法、schema 不符都属于被证伪，一律隔离；后几种在轻量校验里就能发现，所以列表本身也会隔离它们。**读失败（文件被占用、网络盘抖动）不等于损坏，绝不隔离** —— 否则一次杀软扫描就能永久藏掉一个好道具。「道具不存在」同样不属于被证伪。
- 被隔离的道具既不进公开目录，也不占有效道具数量和总存储配额：它在界面上看不到，编辑页也打不开，用户没有任何入口能删掉它，继续计入等于把名额和剩余空间永久扣走。相对地，**只是这一轮读不出来的道具必须照常占名额** —— 缺席不等于不存在。
- 隔离是进程内状态，重启后重新评估；成功的创建／修改／删除解除对应道具的隔离。
- 各类读取都必须有界，且不能为此牺牲正常路径的开销：`record.json` 按 schema 上限一次读定量即判定；资源按其类型上限在已打开的 fd 上先 `fstat` 预检（用实际大小读，不要为一张小图预分配整个上限的缓冲区），摘要计算还要按累计字节封顶，因为 `fstat` 只是快照、外部写者仍可能在之后追加，而这段循环全程持有 store 锁。
- `record.json` 永远不进入 HTTP 资源空间。

### record v2

```text
recordVersion: 2
id: local-<uuid-v4>
name: 用户显示名称
defaultImage: default.png
imageChange:
  mode: press-swap | click-advance
  items:
    - image: change-000.png
      meaning: 该图片对应的互动描述
interaction:
  normalSound: normal.mp3                 # 可选
  special:                                # 整块可选
    probability: 0 < number <= 1
    image: special.png
    meaning: 彩蛋互动描述
    sound: special.mp3                    # 可选
resourceDigests:
  default.png: <sha256>
  change-000.png: <sha256>
  normal.mp3: <sha256>                    # 仅资源存在时
  special.png: <sha256>                   # 仅资源存在时
  special.mp3: <sha256>                   # 仅资源存在时
```

未知 `recordVersion`、未知 mode、字段缺失、多余字段或不完整可选块直接隔离，不做猜测性 fallback。开发期旧记录不能通过猜测图片含义升级为 v2；需要兼容时必须先设计明确、可验证的迁移。

### API 与 DTO 边界

- `GET /api/avatar-tools`：返回全部有效记录的公开运行投影和 limits；单条坏记录只记录日志并跳过。
- `GET /api/avatar-tools/{tool_id}`：只为 Compact 修改页返回该 ID 的完整可编辑详情和受管理资源标识。
- `POST /api/avatar-tools`：multipart 携带本次创建会话的 `tool_id` 创建一个新道具；后端必须复验 ID 格式。
- `PUT /api/avatar-tools/{tool_id}`：以详情 revision 为基线完整更新，保持同一 ID。
- `DELETE /api/avatar-tools/{tool_id}`：删除合法本地 ID 的独占目录。
- `/user_avatar_tools/...`：由 `AvatarToolStaticFiles` 只读暴露 allowlist 内的 PNG／MP3；摘要匹配后必须从同一次打开并核验的文件实例返回字节，不能重新按可替换路径打开，同时保留 HEAD、条件请求和音频 byte range 语义；Range 数量必须在解析和 multipart 物化前受固定上限约束。
  - 请求必须恰好携带一个合法摘要形态的 `v`。缺少 `v`、摘要畸形、大小写不符或附带额外参数一律拒绝，不得回退到未经核验、也不受管理大小上限约束的通道 —— 资源 URL 只有一个生产者（`_asset_url`），任何其它形态都不是本应用发出的请求，放行等于把手工改动或同步损坏的存储根里的任意字节直接流出去。
  - 这一层只负责按 allowlist 提供字节或拒绝，不做完整性裁定：它读到的字节与任何 record 都来自两次独立打开，中间可能夹着一次原子发布，据此判定会误伤刚更新好的道具。完整性归 store 的消费点。
  - 公开路径判定涉及 symlink／resolve／stat 等同步文件系统调用，必须放在事件循环之外执行。
  - 存储根**自身**是软链接不构成拒绝理由：用软链接把存储挪到别的盘是正当操作，而写入侧从不拒绝这种根，服务侧单方面拒绝只会让道具建得出来、图却全是 404。穿越由 `resolve()` 归一后与根比较挡住；根**里面**的道具目录和资源文件仍然必须是实体，那才是能指到根外面去的一类。

公开列表 DTO 只包含 `id`、内容 `revision`、`name`、`changeMode`、`defaultUrl`、有序 `changeUrls`，以及存在时的普通音效和彩蛋运行投影。所有资源 URL 必须是单 `/` 开头、无反斜杠和 fragment 的同源绝对路径，并且必须且只能携带一个非空 `v` 参数；`v` 的内容身份规则见下方原子性约束。互动描述不得进入公开列表、registry、desktopContract 或 PC；只有修改详情和 Python 权威 record resolver 可以读取。

POST、PUT 和 DELETE 必须经过 loopback access、同源 mutation 校验和存储写围栏。PC 只消费同源资源 URL，不读取磁盘路径或 record。

### 原子性与恢复

- 创建在同父目录的 `.local-<uuid>.uploading` 中组成完整记录，校验通过后一次原子改名为正式目录。
- 同一创建会话的保存和重试必须复用同一个 `tool_id`；正式目录已存在且规范化 record 与资源逐项一致时，按同一次创建的幂等重放返回原公开记录。相同 ID 携带不同内容时必须明确冲突并保留表单，不能把旧记录误报为本次保存成功，也不能用重试内容覆盖原记录。
- 修改在 `.local-<uuid>.updating` 中组成完整新目录；保留资源也复制为受管理副本，全部校验通过后通过 `.backup` 完成正式目录替换。
- 修改 revision 必须来自该道具完整 record 与资源的内容身份，不能只依赖文件大小或修改时间；仅替换资源也必须产生新 revision，并使旧修改页得到冲突。
- 公开资源 URL 的不可变版本参数直接使用对应资源摘要，不能使用大小或修改时间；record、revision 和资源 URL 必须指向同一份内容身份。
- 任一步失败必须保留原正式记录，并清理本次可证明属于该操作的临时目录。
- 删除先把正式目录改名为 `.local-<uuid>.deleting`，再清理目录；残留在启动初始化时处理。
- 创建、修改和删除由同一进程内 mutation lock 串行化，使数量、总占用和发布属于同一操作。
- 写围栏检查必须发生在创建目录或清理残留之前；启动恢复被围栏阻止时只标记待恢复，不改动候选目录，待后续存储操作重新通过围栏后再完成恢复；存储维护或迁移期间明确拒绝写入。
- 更新成功后若旧 `.backup` 暂时无法清理，其实际占用必须计入后续总存储配额，直到启动恢复或同 ID 后续操作将其清理。
- 启动初始化只处理本模块严格命名的临时目录和可证明完整的备份；清理失败必须保持待恢复状态，不能让残留目录脱离配额与后续恢复；删除在发布删除前必须先清理同 ID 的旧 backup，使已删除道具不能在启动恢复中复活。普通 GET 不执行清理，也不扫描其它目录；唯一例外是启动初始化已因存储暂时不可用或写围栏而明确挂起时，首次重新确认目录可用且通过围栏的存储操作必须先完成同一初始化恢复。
- 启动初始化不做全量内容复核。恢复只遍历留下 `.updating` 或 `.backup` 痕迹的 ID；给每次冷启动摊上 O(总字节数) 的摘要重算是不可接受的回归，正常启动必须一个受管理资源都不 hash。
- **能否用 backup 覆盖正式目录，判据是「有没有东西会被牺牲」，不是「正式目录是否有效」**：
  - 正式目录有效 —— 任何情况下都不许被顶掉，`.backup` 只是残留，连同 `.updating` 一起清理；
  - 正式目录**确实不存在** —— 可以回滚，没有内容会被牺牲（修改回滚时会先删 `.updating` 再把 backup 挪回，最后那步失败正是这个状态，不恢复就真丢了）。「不存在」不等于「不是目录」：正式目录的名字被普通文件或软链接占着时，那是**有东西**在那儿，见下一条；
  - 正式目录存在但被证伪 —— 只有 `.updating` 作为真正的暂存**目录**存在时才允许回滚；同名普通文件不构成中断证据。没有中断证据时，`.backup` 只是上一次成功修改的残留，拿它覆盖等于回滚用户的最新版本。
- 正式目录的名字被非本模块创建的东西占着（同步客户端或手工操作留下的普通文件、软链接）时，两个方向都不能走：拿 `.backup` 覆盖要先删掉用户的东西，违反下面「不替用户删除」这一条；直接清掉 `.backup` 又可能丢掉这个道具仅存的副本。保留现场并留在待恢复状态，等用户或后续操作处理。
- 恢复不替用户删除正式目录。被证伪也包括「资源闭包不符」，而那可能只是同步客户端或用户往道具目录里放了别的文件，删掉会连带丢失他的原始内容；登记隔离即可。反过来，**被证伪或不可用于回滚的 `.backup` 必须清理** —— 它进不了公开目录、界面上也没有任何入口能删，却一直计入总占用。
- 恢复中的读失败与「被证伪」必须分开：读不出来（文件被占用、网络盘抖动）保留现场并让该存储根留在待恢复状态，下次再判；只有被证伪才做破坏性处置。判定未完成时不得清除待恢复标记。
- 这条同样适用于**文件系统探测本身**，而且是整个模块的不变量，不只是恢复路径的。`Path.is_dir()` / `Path.is_file()` / `Path.exists()` 会把任何 `OSError` 压成 `False`，于是「这次没读到」被静默改写成「它不在」—— 而「不在」恰好是放行破坏性动作、释放名额、少算配额的那个答案。凡是据此做判断的地方，都必须用能区分「确实不在」与「这次没读到」的探测（`os.lstat` 分辨 `FileNotFoundError` 与其它 `OSError`），并按站点各自决定读不到时算什么。**读不到一律不得当作不存在**：

  | 探测点 | 读不到时必须 | 当成「不存在」的后果 |
  | --- | --- | --- |
  | 恢复时的正式目录 / `.updating` / `.backup` | 保留现场，留在待恢复状态 | 旧 `.backup` 顶掉盘上完好的正式目录，静默回滚用户数据 |
  | 记录读取和校验触及的每一项（`record.json`、道具目录、声明的资源、闭包遍历项） | 一律报暂时性失败（`transient`），不报 `tool_not_found`、`record_invalid` 或「闭包不符」 | 启动恢复把健康道具判成「被证伪」，隔离它甚至拿旧 backup 顶掉它 |
  | 暂存目录大小（`_directory_bytes`） | 拒绝写入 | 低估暂存字节，放行一次本该被拒绝的更新 |
  | 恢复时的 `.uploading` / `.deleting` 残留 | 判定未完成，保持待恢复 | 待恢复标记被清掉，孤儿本进程内不再重试，继续绕过或占用配额 |
  | 名额计数的道具目录 | 保守计入，照占名额 | 少算一个名额，上限被悄悄突破 |
  | 配额统计的目录和文件 | 拒绝写入（存储暂时不可用） | 总量被低估，`maxTotalBytes` 形同虚设 |

  反过来，纯清理路径（`remove_owned_directory`、删除前的残留清理）读不到就跳过是安全的：那只会少删一次，不会做出破坏性判断，且后续操作会再遇到它。

- **破坏性动作之前必须重新确认授权它的那个前提**。校验一个 `.backup` 要把它的每个资源逐字节 hash 一遍，这段时间足够同步客户端发布一个新的正式目录 —— 拿授权时的旧观察去删它，抹掉的是用户刚同步下来的新版本。删除/替换之前重新探测，状态和授权时不一致就整轮弃权。这一条对所有「先观察、后破坏」的两段式操作成立，不只是恢复路径。

`avatar_tools` 必须加入应用存储根迁移、storage diagnostics 和“运行时是否有用户内容”的识别，但不能因此进入云存档托管列表。

“有没有用户内容”的扫描一般把点开头的子项当作噪声跳过（`.DS_Store` 之类），但本模块的原子更新恰恰用点开头的目录做暂存。更新被打断时，`.local-<uuid>.backup` / `.local-<uuid>.updating` 可能是某个道具**仅存的副本**，而 cloudsave bootstrap 的 legacy 导入跑在道具恢复之前：一旦目标根被判定为“没有用户内容”，导入会不备份就整根替换，把这两个可恢复目录直接删掉。因此这类事务暂存目录在该判定中必须算作用户内容 —— 但判据只放宽到本模块自有的事务命名，普通点文件仍然是噪声。

## 动态 definition 与运行时

### ID、label 与 registry

- `BuiltInAvatarToolId` 保持四个内置 ID 的封闭集合。
- `LocalAvatarToolId` 严格匹配本地 UUID 格式。
- `AvatarToolId` 是两者的联合；本地 ID 不加入内置静态 tuple。
- 内置道具使用 i18n label，本地道具使用 literal label；用户名称不能伪装成 i18n key。
- 每个 surface 用“内置 registration + 当前有效本地 definition”生成不可变 registry snapshot。
- 资源查询必须以 `(toolId, resourceId)` 为作用域，多个本地道具可以复用内部 sound/effect ID 而不串用资源。

本地列表首次权威加载完成前，不能把保存槽位中的 `local-*` 当作未知 ID 清除。首次加载失败保留已保存槽位；已有 snapshot 刷新失败继续使用上一份 snapshot。创建、修改和删除后的请求代次必须使旧 GET 失效，迟到响应不能覆盖较新的权威结果。当前选择的本地 ID 若在新的权威 registry 中消失，当前 surface 必须安全停用该道具，不能在过渡渲染中继续读取已经不存在的 registration。

### definition v2

四个内置道具继续使用 definition v1。自定义道具固定构建 definition v2：

- 帧 `0` 是默认图片；帧 `1..N` 对应 record 变化项 `0..N-1`。
- `press-swap` 恰好一个变化帧；`click-advance` 至少一个变化帧。
- `actionId` 固定为 `interact`。
- intensity 只允许 `normal` 和 `rapid`。
- touch zone 复用 `ear / head / face / body`。
- 普通 sound、chance、chance sound 和 effect 仅在记录实际配置时声明；不使用空对象、空字符串或伪造资源占位。
- chance field 固定为 `specialTriggered`；chance 不存在时不调用 RNG，也不输出该字段。
- 显示尺寸、anchor、hotspot、音量、连续判定和 `random-scatter` recipe 由代码常量提供，用户不能修改。

固定 builder 只把权威公开 DTO 转成 definition。后端不保存或生成前端 definition，PC 也不重建本地 definition。

### 一次互动

所有自定义道具复用现有 press/release pointer session：

1. 范围变化只改变当前帧大小。
2. `press-swap` 只在未锁定且命中 avatar 的 pointer down 临时显示帧 `1`，但不提交；release 或 cancel 后恢复帧 `0`，页面背景按下保持帧 `0`。
3. `click-advance` 只在有效 release commit 时前进一帧并封顶；无效点击不前进。
4. release 必须重新验证 bounds、UI exclusion、同一 pointer/button、移动阈值和 touch zone。
5. `changeIndex` 始终表示本次有效互动对应的变化项索引，不是视觉帧索引。
6. 到末张后的后续有效点击继续提交末项索引，但画面不循环。
7. 取消选择、切换道具、强制停用、surface handoff、页面重建或应用重启会结束选择 session；重新选择从默认帧开始。
8. 连续记录按当前 tool session 隔离，图片索引不参与 normal/rapid 判断。

Web 和 PC 必须由同一 v2 profile 计算图片索引、声音、效果和事件事实，不得增加按具体 `local-*` ID 的行为分支。

## 跨端、提示词与隐私

### desktopContract 与 NEKO-PC

- Web projector 把 definition v2 投影为严格 descriptor；用户互动描述不进入 descriptor。
- definition v2、desktop descriptor 和本地互动 payload 必须携带公开目录对应的内容 revision；Python 只按完全相同的当前 record revision 解释图片索引和彩蛋事实，过期互动直接拒绝，不能用新记录解释旧画面。
- PC consumer 严格校验有序帧、两种图片变化规则、可选声音、chance、effect 和资源闭包。
- PC 只保存当前选择 session 的图片索引，不保存本地 record 或互动描述。
- deactivate、dispose、renderer reload 和 surface handoff 必须清理未完成 press、timer、effect、sound 和旧 generation。
- surface lease 下发布本地 descriptor 前，要向权威公开列表核对 ID、有序版本化资源 URL、切图方式、可选音效和彩蛋概率/资源语义；无论 lease 与页面状态谁先到，首次发布和重发都不能绕过校验。已删除 ID 发布 inactive，任一内容过期只请求 renderer 刷新，不能发送旧 descriptor；同一 lease 下较新的页面状态必须替代尚未完成的旧校验。
- 列表暂时请求失败不能解释为删除，也不能回流未经确认的旧本地 descriptor；显式目录失效事件若撞上在途 GET，必须废弃其快照并在结束后再发起一次新 GET。

### Host 与 Python

本地道具提交固定事件事实：

- `toolId = local-<uuid-v4>`
- `actionId = interact`
- `target = avatar`
- `intensity = normal | rapid`
- `touchZone = ear | head | face | body`
- `changeIndex = 非负安全整数`
- `specialTriggered = 明确布尔值`，仅在该 definition 声明彩蛋时存在

Host 只做静态 wire 校验和现有 dispatch/cooldown/ack 生命周期，不持有本地记录。Python 在消耗互动冷却前通过同进程 store 读取权威 record，复验 ID、索引和彩蛋字段：

- 重复或仍在冷却期的事件只做 record/revision/索引轻量校验；冷却判断、可能进入提示词的事件逐字节核验资源摘要、冷却提交必须由同一会话门串行化，核验失败不消耗冷却；提示词只能使用这次严格核验得到的 record；
- 会话门里只允许出现判定与去重登记，**任何回执都必须出门之后再发**。冷却是连击时的高频分支，而回执走 WebSocket，把这次 `await` 关在门内会让下行一有背压就把后续每一次互动堵在门口，包括冷却窗口结束后第一个本该被接受的互动。同理，延迟恢复期间权威读取抛出的维护态错误必须被互动链路吸收，不能穿透到上层；

- 未命中彩蛋时只选择当前 `changeIndex` 对应的互动描述；
- 命中彩蛋时只选择彩蛋互动描述；
- 记录缺失、损坏、索引越界或彩蛋事实不一致时返回 `invalid_payload`；
- 不能回退到猫爪、第一张图片、普通点击或其它描述。

提示词必须把名称和互动描述作为有边界的不可信 JSON 数据，而不是可执行指令。固定模板保留当前角色身份、关系和语言。memory note 只保存安全显示名称和系统事实，不直接保存用户互动描述；去重 key 使用稳定本地 ID。

图片和音频不发送给模型。道具名称和命中的互动描述会进入当次模型反馈；使用远程模型时会随请求发送，创建页必须明确告知。

## 生命周期与故障语义

| 场景 | 必须保持的结果 |
| --- | --- |
| 创建成功 | 当前 Compact snapshot 立即加入新 ID，随后 GET 校准；不自动装备，不改另一 surface 槽位。 |
| 创建响应不确定 | 用本次创建会话的稳定 ID 刷新权威列表；该 ID 已存在时，必须由同 ID、同完整内容的幂等 POST 明确确认才按原提交创建成功收口，不能只凭 ID 存在关闭表单。无法确认则保留表单，用户再次保存仍复用同一 ID；再次保存的内容若已变化，后端必须与已存在记录判定冲突，不能静默丢弃新内容。 |
| 修改成功 | 同 ID definition 被替换；旧 session 副作用清理，保持选择并从新默认帧开始；槽位顺序不变。 |
| 修改响应不确定 | 读取同 ID 详情和列表，以 revision 与提交内容判断原提交是否成功；最终 registry 必须保留最后一次成功列表刷新得到的更新版本，不能再用较早读取的详情覆盖；不能盲目重试产生分叉。 |
| 修改 revision 冲突 | 保持修改页打开，载入并显示最新权威详情与 revision，明确提示内容已变化；不把过期草稿或文件猜测合并到新版本。 |
| 创建或修改暂存清理失败 | 保留原操作失败结果并将存储标记为待恢复；后续变更必须先清理 `.uploading` / `.updating`，清理仍失败时不得继续写入或绕过总量限制。 |
| 修改发布和备份回滚均失败 | 保留 `.backup` 并将存储标记为待恢复；后续变更必须先恢复原正式记录，不能在正式目录缺失时创建同 ID 新记录。 |
| 删除成功 | 精确移除该 ID 的目录、registry、当前使用态和当前 surface 已保存槽位，并退出该道具的修改页。 |
| 删除响应不确定 | 权威 GET 确认 ID 已不存在才按成功收口；GET 失败或 ID 仍在则保留真实状态。 |
| 单条坏 record | 只隔离该项并记录日志；内置道具和其它本地道具继续工作。 |
| 资源加载/解码失败 | 结束并清理本次表现，不破坏 Pet、页面或其它道具。 |
| 首次目录请求失败 | 显示内置道具但保留本地槽位，不用不完整目录回写 localStorage。 |
| 刷新失败 | 保留上一份有效 snapshot，并在 Manager 打开、focus 或 surface 激活时重试；这些激活信号与旧 GET 重叠时，必须废弃旧结果并在其结束后补一次新 GET。 |
| 页面/renderer 销毁 | 清理 pointer、sound、effect、timer、异步回调和桌面 descriptor owner。 |
| 应用重启 | record 与资源恢复；各 surface 清洗并恢复自己的槽位；当前选择和逐次索引不恢复。 |
| 存储维护/迁移 | 创建、修改和删除明确失败，不能同时写入旧根与新根。 |

多道具之间只能通过共享的 registry/runtime 能力共存，不能共享 record、目录、图片索引、burst history、sound/effect 实例或修改 revision。

## 维护与扩展规则

### 必须保持的架构边界

1. N.E.K.O record 是本地自定义道具的唯一业务事实源。
2. 后端保存业务数据和受管理资源，不保存 AvatarToolDefinition 或 desktopContract。
3. Web builder 负责固定 definition；registry/runtime 负责执行；页面组件只负责表单和接线。
4. PC 只消费 descriptor 和同源资源 URL，不读 record、不保存互动描述、不按本地 ID 写分支。
5. Host 只校验 wire，Python 才读取 record 并选择互动描述。
6. Full／Compact 共享目录内容但不共享槽位，不能增加隐式同步。
7. 创建、修改和删除使用同一个 store、record 版本、limits、写围栏和原子发布方式。
8. 所有用户可见文案使用八 locale；用户名称和描述保留原文，不创建动态 key。

### 增加新切图方式

只有产品流程明确需要第三种方式时才扩展。必须同时完成：

- record mode 的严格判别结构与版本兼容判断；
- 创建/修改表单独立编辑状态和清晰文案；
- 固定 Web builder、catalog validator 和 profile interpreter；
- Web runtime 与 PC runtime 的对称状态转换；
- desktopContract producer/consumer strict decode；
- `changeIndex`、提示词含义选择和失效恢复测试。

不得用多个布尔值拼状态，不得把模式脚本存入 record，也不得为单个 mode 建第二套 pointer runtime。

### 修改 record 或 API

- 不兼容结构变化必须提升 record 版本，并明确旧版本是迁移、只读还是隔离。
- 迁移只能基于可证明事实；不能猜测旧图片的互动含义、模式或资源归属。
- 列表 DTO 保持最小公开投影。只有运行消费者真正需要的字段才能进入列表或 desktopContract。
- 新资源必须进入 record 闭包、静态 allowlist、容量计算、原子更新、删除和迁移测试。
- 不为读取失败增加宽松 fallback，不把未知字段当成未来兼容。

### 修改 UI

- 继续复用现有 Manager、三槽和创建/修改表单。
- 简单目标不能通过新增预览页、二级编辑器、同步状态或重复确认流程复杂化。
- 布局变化必须验证默认无外层滚动、内容区内部滚动、底部操作固定、页面切换不跳位，以及亮暗主题。
- 文件选择继续使用浏览器/Electron 原生独立文件选择器，不能限制在聊天模块内模拟文件窗口。

### 修改提示词

遵循 `docs/design/avatar-tool-prompt-guidelines.md`：先确认互动事实和权威描述选择，再调整模板。不能根据图片外观、内置道具示例或资源名猜测这是喂食、身体触碰或其它互动。

## 验收与回归门禁

### 自动化

- Web：DTO/详情严格解码、固定 builder、动态 registry、表单增删排序和模式独立、资源保留/替换/移除、两种切图、末张封顶、session 复位、声音、chance、Full catalog 和 desktopContract。
- 后端：名称/描述、模式、数量、图片/音频、multipart 上限、资源闭包、路径穿越、CSRF、详情隔离、同 ID POST 重试、同 ID PUT、revision 冲突、原子失败恢复、删除、维护态写围栏、总容量和坏记录隔离。
- Host/Python：local ID、`changeIndex`、`specialTriggered`、权威描述选择、缺失/损坏记录、八语言 prompt、prompt injection 边界、cooldown、ack 和 memory 去重。
- PC：v1 回归、v2 strict decode、两种切图、Web/PC 索引一致、声音、chance、坏资源、deactivate/dispose、surface lease、删除，以及 URL 相同但切图方式或彩蛋概率已经变化的过期 descriptor。
- 跨仓：同一 v2 fixture 必须同时通过 Web projector 与 PC consumer，并产生一致的帧和 payload 结果。
- i18n：`en/es/ja/ko/pt/ru/zh-CN/zh-TW` JSON 可解析、key 集合一致、代码引用存在。

### 实际运行

每次改变用户流程、资源、runtime、desktopContract 或生命周期时，按影响范围实际验证：

1. Compact 创建、保存失败保留、装备和使用。
2. `press-swap` 按下、松开、cancel 和范围缩放。
3. `click-advance` 多图排序、有效/无效点击、末张不循环和重新选择复位。
4. 普通声音，以及彩蛋命中/未命中、独立音效/普通音效回退/静默。
5. 同 ID 修改、资源保留/替换/移除和当前 session 清理。
6. 删除当前或隐藏 surface 使用的道具，多道具之间不互相污染。
7. Compact → Full → Compact、Pet reload 和应用重启。
8. 单条坏 record、资源 404、目录请求失败和存储维护态。

仅验证 schema 或 mock DTO 不能代替真实页面、实际构建产物和 Electron/Pet 链路。

### 不受影响链路

必须继续回归四个内置道具，以及普通聊天、拖拽、最小化、教程、截图暂停、窗口隐藏、Full／Compact handoff、Pet reload 和页面销毁。平台相关改动还必须覆盖 macOS、Windows 和现有 Linux X11／Wayland／Niri 安全路径。

## 代码索引

### N.E.K.O

- `utils/avatar_tool_store.py`：record v2、limits、资源校验、创建/修改/删除和恢复。
- `main_routers/avatar_tool_router.py`：列表、详情、multipart mutation API 和错误映射。
- `app/main_server/web_app.py`：私有目录初始化和安全静态资源挂载。
- `utils/config_manager/storage_roots.py`、`utils/storage/migration.py`、`utils/cloudsave_runtime/`、`main_routers/storage_location_router.py`：存储根、迁移、写围栏和诊断。
- `frontend/react-neko-chat/src/AvatarToolItemManager.tsx`：道具库、三槽和 Compact 创建/修改入口。
- `frontend/react-neko-chat/src/AvatarToolCreatePage.tsx`：创建/修改共用表单。
- `frontend/react-neko-chat/src/avatar-tools/localTools.ts`：公开/详情 DTO、API client 和固定 v2 builder。
- `frontend/react-neko-chat/src/avatar-tools/useLocalAvatarToolCatalog.ts`：动态目录请求、请求代次和 snapshot 校准。
- `frontend/react-neko-chat/src/avatar-tools/catalog.ts`、`registry.ts`、`profileInterpreter.ts`、`runtime.ts`、`presentation.tsx`、`desktopContract.ts`、`protocol.ts`：通用定义、执行、表现和桌面投影。
- `frontend/react-neko-chat/src/App.tsx`、`FullChatSurface.tsx`、`avatarTools.ts`：Compact/Full 页面接线、槽位和菜单投影。
- `static/app/app-buttons.js`：Host 本地互动 wire 校验和派发。
- `config/prompts/avatar_interaction_contract.py`、`config/prompts/prompts_avatar_interaction.py`、`main_logic/core/greeting.py`、`main_logic/cross_server.py`：Python 复验、提示词、ack 和 memory。
- `static/locales/*.json`：八语言用户可见文案。

### N.E.K.O-PC

- `src/desktop-avatar-tools/contract.js`：v1/v2 descriptor 严格 consumer。
- `src/desktop-avatar-tools/runtime.js`、`interaction-output.js`：桌面输入、帧、声音、效果和 Host payload。
- `src/desktop-avatar-tools/surface-lifecycle.js`：descriptor ownership、handoff 和 renderer guard。
- `src/preload/bridges/chat-avatar-tool-bridge.js`：Full/Compact descriptor 发布、ID/资源版本校验和刷新。
- `src/preload/bridges/pet-avatar-tool-adapter.js`：Pet pointer、模型 bounds、桌面 runtime 和 interaction IPC 适配。
- `src/window-manager.js`：Full 独立 `persist:neko-full-chat` partition。
