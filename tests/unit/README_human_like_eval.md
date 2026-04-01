# 人格化多模型评测系统说明

本文档描述 `tests/unit/` 下新增的人格化多模型评测框架，用于评估不同 AI 模型在中文陪伴式对话中的自然度、共情力、生活感、连续性与低机器感表现。

## 维护要求

**后续凡是修改以下任一内容，都必须同步更新本文档：**

- 题库结构与场景数量
- 人设配置与默认人设
- 评分维度、权重、锚点、判定规则
- 运行入口、报告格式、模型配置方式
- 网络异常与跳过逻辑

如果代码和本文档不一致，应视为文档失效，需要立即补齐说明。

## 相关文件

- `tests/unit/run_human_like_multi_model_eval.py`
  评测主入口，负责批量运行模型、执行场景、生成报告。
- `tests/unit/human_like_eval_config.py`
  场景题库、评分维度、评分锚点、场景集选择逻辑。
- `tests/unit/human_like_eval_targets.py`
  待测模型列表与场景集开关。
- `tests/unit/human_like_eval_personas.py`
  统一人设预设与当前激活人设。
- `tests/utils/human_like_judger.py`
  专用人格化评审器，负责把多轮对话交给 judging model 结构化打分。
- `tests/unit/test_text_chat.py`
  复用其中的 `create_offline_client()` 创建被测模型客户端。

## 设计目标

这套系统不是测试知识问答准确率，而是测试：

- 是否像真人聊天
- 是否贴近日常生活
- 是否能接住用户情绪
- 是否具有陪伴感
- 是否能在多轮对话中维持连续性
- 是否明显缺少机器感

## 运行方式

先检查以下两个配置文件：

### 1. 选择场景集和待测模型

文件：`tests/unit/human_like_eval_targets.py`

- `SCENARIO_SET = "basic"` 或 `"full"`
- `TEST_TARGETS = [...]`

示例：

```python
SCENARIO_SET = "full"

TEST_TARGETS = [
    {"provider": "qwen", "model": "qwen3.5-plus"},
    {"provider": "openai", "model": "gpt-5-chat-latest"},
]
```

### 2. 选择统一人设

文件：`tests/unit/human_like_eval_personas.py`

- `PERSONA_PRESET = "warm_companion"` 等

当前支持的人设：

- `warm_companion`：温柔陪伴型
- `light_friend`：轻松朋友型
- `gentle_catgirl`：温柔猫娘型

### 3. 执行命令

```bash
uv run python tests/unit/run_human_like_multi_model_eval.py
```

## 评测流程

对 `TEST_TARGETS` 中的每个模型，程序会依次执行：

1. 使用 `create_offline_client()` 创建 `OmniOfflineClient`
2. 使用统一人设 prompt 调用 `client.connect()`
3. 逐个场景执行多轮中文对话
4. 记录完整 `conversation_log`
5. 将整段对话交给 `HumanLikeJudger`
6. 由 judging model 输出结构化评分
7. 汇总所有模型结果并生成 JSON / Markdown 报告

## 场景结构

每个场景在 `human_like_eval_config.py` 中定义，包含：

- `id`
- `category`
- `title`
- `goal`
- `scoring_focus`
- `scoring_questions`
- `prompts`

当前题库已经做过平衡，不再只偏向负面情绪场景，而是覆盖：

- 日常自然聊天
- 轻松吐槽
- 小开心分享
- 安静陪伴
- 情绪共鸣
- 多轮连续性
- 关系感
- 边界控制

## 场景集说明

### `basic`

适合快速对比，题量较少，但尽量保持均衡覆盖。

### `full`

适合完整评测，覆盖更多情绪、关系、日常与风格场景。

## 统一人设机制

所有被测模型在同一次评测中都使用**同一套 system prompt**，以确保公平性。

统一人设的作用：

- 让评测更接近产品真实使用状态
- 避免测到“裸模型默认风格”而不是预期角色风格
- 让不同模型在相同角色约束下可横向比较

注意：

- 人设提示中**不能直接泄露评分标准**
- 人设提示只定义聊天身份和风格，不应写“请争取得高分”之类内容

## 评分维度

当前评分维度如下：

| 维度 | 含义 | 权重 |
| --- | --- | ---: |
| `naturalness` | 自然度 | 2.5 |
| `empathy` | 共情力 | 2.0 |
| `lifelikeness` | 生活感 | 1.5 |
| `context_retention` | 连续性 | 1.5 |
| `engagement` | 互动性 | 1.0 |
| `persona_warmth` | 温暖感 | 1.0 |
| `ai_ness_penalty` | 机器感惩罚 | 扣分 |

## 单场景总分计算公式

```text
raw_score =
naturalness * 2.5 +
empathy * 2.0 +
lifelikeness * 1.5 +
context_retention * 1.5 +
engagement * 1.0 +
persona_warmth * 1.0 -
ai_ness_penalty
```

其中原始加权满分不是 100，而是：

```text
max_raw_score = 95
```

为了避免误解，系统当前使用**归一化总分**：

```text
overall_score = max(raw_score, 0) / 95 * 100
```

也就是说：

- `raw_score`：原始加权分
- `overall_score`：按 95 分满分归一化后的 100 分制总分

程序会将 `overall_score` 限制在 `0~100` 区间。

## 评分锚点

当前系统不是简单让 judging model 自由发挥打分，而是加入了显式锚点来提高可控范围内的稳定性。

每个维度都使用以下档位结构：

- `9-10`
- `7-8`
- `5-6`
- `1-4`

评分器会在 prompt 中要求 judging model：

- 先根据锚点选择区间
- 再在区间内选择整数分
- 如果介于两个档位之间，优先使用较保守的低分
- 不要轻易给极高分或极低分

这使当前系统更接近：

- 带锚点约束的结构化主观评分

而不是完全自由的随意打分。

## 通过判定

当前 `verdict` 的目标规则为：

- `overall_score >= 75`
- `naturalness >= 6`
- `empathy >= 6`
- `ai_ness_penalty <= 9`

满足以上条件时，场景应判为 `YES`。

## 多模型汇总方式

在 `run_human_like_multi_model_eval.py` 中，会按模型汇总：

- `scenario_count`
- `passed_scenarios`
- `failed_scenarios`
- `pass_rate_percent`
- `avg_overall_score_100`
- 各维度平均分
- 网络跳过数量
- 因网络问题连带跳过的场景数量

模型最终排序当前按：

- `avg_overall_score_100`

从高到低排序。

## 网络异常处理

如果某个模型在某个场景中出现明显网络类问题，例如：

- timeout
- connection error
- 429
- 502 / 503 / 504
- service unavailable
- empty response

则当前场景会标记为网络跳过，并且：

- 当前模型后续场景全部标记为 `skipped_due_to_network`
- 评测程序直接进入下一个模型
- 不会因为单个模型的网络故障中断整个多模型批跑

## 报告输出

输出目录：

- `tests/reports/`

文件格式：

- `human_like_eval_report_YYYYMMDD_HHMMSS.json`
- `human_like_eval_report_YYYYMMDD_HHMMSS.md`

Markdown 报告中包含：

- 概览
- 当前场景集
- 当前统一人设
- 场景题库
- 多模型对比
- 各维度均分
- 每个场景的评分结果
- 每个场景的原始加权分与归一化总分

总测试说明中的 `tests/README.md` 也会保留该框架的简要入口说明；如果这里的机制发生变化，应同时更新两处文档。

## 当前系统的性质说明

这套系统适合：

- 多模型横向比较
- 同一模型不同人设或不同版本对比
- 产品风格迭代评测

这套系统**不等于完全客观评分**。它更准确的定位是：

- 带评分锚点的结构化 LLM 评审系统

因此更适合看：

- 相对高低
- 维度长短板
- 修改前后的趋势变化

而不是把单次分数视为绝对真理。

## 后续修改要求

后续如果出现以下变更，必须同步更新本文档：

- 新增或删除场景
- 修改场景集定义
- 修改默认人设或新增人设
- 修改评分公式
- 修改评分锚点
- 修改报告结构
- 修改网络跳过策略
- 修改模型配置方式

推荐在每次与该框架相关的代码提交或改动中，检查本文档是否需要更新。
