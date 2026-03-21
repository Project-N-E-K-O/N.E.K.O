---
name: ssr-hydration-scraping
description: Best practices for extracting data from modern React/Vue SSR pages (like Next.js or Nuxt.js) by targeting hydration state blocks (__NEXT_DATA__, __NUXT__) using regex and `jmespath`, avoiding brittle DOM selector scraping.
---

# SSR Hydration Data Scraping

## 症状 (Symptoms of Brittle DOM Scraping)
- 爬虫经常因为前端 CSS Modules 或 Styled Components 的随机 Hash 类名（如 `class="sc-fHeRUl"`）变化而大面积失效。
- 难以准确遍历 DOM 树内嵌的复杂状态（如下拉加载更多、未渲染的图集等）。

## 根本原因 (Root Cause)
现代前端框架（React, Vue, Solid）在使用服务端渲染（SSR）时，为了在客户端“注水”（Hydration），通常会将首屏所需的完整甚至包含下一页数据的 JSON 序列化并挂载在 HTML 的 `<script>` 标签内。
直接提取这段纯净的 JSON 结构比解析混合了展示逻辑的 DOM 要稳定和高效得多。

## 代码解决方案 (Solution)

### 1. 定位 SSR 数据块
使用正则表达式全局提取目标脚本标签中的 JSON 字符串。
```python
import re
import json

def extract_ssr_data(html: str) -> dict:
    # Next.js
    next_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    # Nuxt.js / Vue
    nuxt_match = re.search(r'window\.__NUXT__\s*=\s*({.*?});', html, re.DOTALL)
    # 通用 Initial State
    init_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)

    if next_match:
        return json.loads(next_match.group(1))
    elif nuxt_match:
        return json.loads(nuxt_match.group(1))
    elif init_match:
        return json.loads(init_match.group(1))
    return {}
```

### 2. 使用 `jmespath` 结构化查询规避多层嵌套校验
SSR 数据常有极深的组件树嵌套，直接使用字典 `.get()` 或递归极易出错或遗漏。推荐使用 `jmespath` 进行路径嗅探：
```python
import jmespath

ssr_data = extract_ssr_data(html)
if ssr_data:
    # 使用 jmespath 嗅探可能的列表挂载点
    possible_paths = [
        "props.pageProps.data.rows",
        "props.pageProps.list",
        "payload.data[0].list"
    ]
    target_list = []
    for path in possible_paths:
        res = jmespath.search(path, ssr_data)
        if isinstance(res, list) and len(res) > 0:
            target_list = res
            break
            
    # 遍历干净的数据对象
    for item in target_list:
        print(item.get('url'), item.get('title'))
```

## 关键经验 (Key Takeaways)
1. **停止在 DOM 树里捡垃圾**：面对现代网站抓取任务，F12 后第一件事是全局搜索目标文本，查看是否直接躺在某个 `<script>` 或 `window.xxx` 的 JSON 赋值里。
2. **容错性**：使用 `jmespath` 可以跨越层级查找，极大地提升了针对未知嵌套结构的防御力。
3. **退路**：如果 SSR 没有数据，不要立刻写 DOM 抓取，先抓包看是否有页面渲染初期的直连 XHR API，走 XHR API（"结构化白嫖"）同样远优于 DOM 解析。
