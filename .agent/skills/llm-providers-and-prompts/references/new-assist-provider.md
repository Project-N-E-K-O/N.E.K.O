# 新增一个辅助 API provider

字段含义见 `docs/api_providers_fields.md`。最稳的做法：拿最近加进来的一家（例如 `orcarouter`）在仓库里 grep 一遍，照它出现的每一处补齐。

## 登记点

后端（照抄相邻的一家即可）：

1. `config/api_providers.json`
   - `assist_api_providers.<key>`：地址（字段名是 `openrouter_url`，见下）和各角色的默认模型。
   - `assist_api_key_fields.<key>`：映射到 `ASSIST_API_KEY_<NAME>`。
   - `api_key_registry.<key>`：`config_field`（前端字段名 `assistApiKey<Name>`）和 `restricted`。
2. `config/api_profiles.py`：默认配置里的 `assistApiKey<Name>` 空值、代码侧的 profile 兜底、key 字段映射。
3. `utils/config_manager/core_config.py`：`get_core_config` 里 `config['ASSIST_API_KEY_<NAME>'] = ...` 那一行。
4. `main_routers/config_router/core_config.py`：`CORE_CONFIG_ASSIST_API_KEY_FIELDS`（决定这个字段按密钥处理、回显时打码）和返回给设置页的字段。
5. `docker/CONFIG_REFERENCE.md` 的 provider 表。
6. 如果它是聚合网关：`config/providers.py::ROUTER_REASONING` 加一行并实测（见 `provider-dialects.md`）；默认模型要么已在 `MODELS_EXTRA_BODY_MAP`，要么能经网关行映射过去。
7. 如果它的输出上限字段或缓存行为和默认不同：`CACHE_PROVIDERS` 加一项。

前端：设置页是数据驱动的，不需要为新 provider 写 JS 分支，但**要补 i18n**（见易漏点 1）。

测试：`tests/unit/test_api_config_manager.py`（见易漏点 2），网关另加 `tests/unit/test_router_reasoning.py` 用例。

## ⚠️ 易漏点 1：八种语言 × 两个命名空间，CI 抓不到

`static/locales/` 下八个文件各要两条：

- `api.assistProviderNames.<key>`：辅助 API 下拉框里的名字。漏了会回落到 `provider.name`，不崩，但丢掉其它境外 provider 都有的地区限定说明。
- `api.keyBook.<key>`：API Key 管理簿里的标签。**漏了会把翻译键本身显示给用户。** 原因：`static/i18n-i18next.js` 处理 `data-i18n` 元素时，翻译缺失只在调试模式下打一条警告，然后照样把返回值（就是键名）写进 `textContent`；旁边处理 placeholder / title 的分支有「等于键名就不写」的保护，`data-i18n` 分支没有。回退语言同样缺这个键时，八种语言全中。

为什么 CI 抓不到：`scripts/check_i18n_sync.py` 只在 locale 文件**被改动**时才校验八个文件是否同步，一个都没改就是空操作；`scripts/check_i18n.py` 只查 JS / HTML 里写成字面量的键，而这两个命名空间下的键是按 provider key 在运行时拼出来的，扫不到。只能靠人照这份清单核对。

仓库里有同一形态的回归测试可以照抄：`tests/frontend/test_api_settings.py` 里断言标签文本不等于翻译键的用例。

## ⚠️ 易漏点 2：`restricted` 的判据是「端点域名是否在境外」

`api_key_registry.<key>.restricted` 的唯一消费者是前端 `static/js/api_key_settings.js::isProviderRestricted`，而且只对中国大陆用户生效：把这家从下拉框和 Key 管理簿里隐藏。

- 判据是端点域名：境外域名 `true`，境内或本机 `false`。决定性的对照是同一厂商的两档（国内端点 `false`，国际端点 `true`）。和「是否收费」「是否聚合平台」「是否外国公司」无关。
- 中转网关无法从仓库内判断（它存在的理由可能正是给大陆用户中转），要请提交者说明。
- 改这个值不止改一行 JSON：`tests/unit/test_api_config_manager.py::test_restricted_providers` 里有一份写死的名单，名单内必须是 `true`，名单外必须不是 `true`。新 provider 设成 `false` 会被这个测试静默放行，所以值本身要人工核对。

## ⚠️ 易漏点 3：回归报告门禁也管 `main_routers/`

`scripts/check_pr_report.py` 的 `WATCHED_PREFIXES` 是 `app/`、`main_logic/`、`main_routers/`、`memory/`，而 PR 模板和部分文档只写了三个目录。新增 provider 要改 `main_routers/config_router/core_config.py`，所以 PR 描述里要有「回归报告」一节。

## 不需要改的地方（免得来回质疑）

- `OPENROUTER_URL` / `openrouter_url` 是全仓共用的历史字段名，不是 OpenRouter 私有的。新 provider 复用它，不要另起字段。
- `config/api_providers.json` 里的 `description` 是裸中文字面量，没有 locale 命名空间，前端也不消费。
- `MODELS_EXTRA_BODY_MAP` 不登记是允许的，已有多家先例。**不能**靠剥掉 `vendor/` 前缀归一化成上游模型名再查表：另一种方言的 extra_body 发给第三方网关可能 400。
- `docs/` 下各语言的 provider 介绍页刻意只讲机制、不冻结清单。
