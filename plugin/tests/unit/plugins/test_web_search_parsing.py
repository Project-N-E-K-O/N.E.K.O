"""web_search 插件解析层测试。

覆盖用户报告的三类问题：
1. 非法字符——Baidu 页面里的 iconfont 私有区字符、编码错位产生的 U+FFFD、
   零宽/bidi 控制符进入 LLM/TTS 被读出乱码；
2. 解析瑕疵——容器里第一个 <a> 是卡片子链接/相关搜索词，被当成结果标题；
3. 不安全——javascript:/相对链接原样返回、反爬验证页被静默解析成 0 条。

不可见/私有区字符一律用 \\u 转义书写，避免源码里出现肉眼不可见的字面量。
"""
from __future__ import annotations

import pytest

from plugin.plugins.web_search import _parsing as p

pytestmark = pytest.mark.plugin_unit

_PUA = "\ue687"      # Baidu iconfont 私有区字符（实测页面中出现）
_ZWSP = "\u200b"     # 零宽空格
_RLO = "\u202e"      # bidi 右到左覆盖符
_REPL = "\ufffd"     # U+FFFD replacement character


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_strips_pua_and_invisible_chars() -> None:
    dirty = f"上海天气{_ZWSP}预报{_RLO}今日\x07实况{_REPL}{_PUA}"
    assert p.sanitize_text(dirty) == "上海天气预报今日实况"


def test_sanitize_collapses_whitespace_variants() -> None:
    assert p.sanitize_text("多云\xa0转晴\n\t 33/28℃ ") == "多云 转晴 33/28℃"


def test_sanitize_preserves_normal_text_and_emoji() -> None:
    assert p.sanitize_text("NEKO 🐱 v2.0 发布!") == "NEKO 🐱 v2.0 发布!"


def test_sanitize_empty() -> None:
    assert p.sanitize_text("") == ""


# ---------------------------------------------------------------------------
# decode_html
# ---------------------------------------------------------------------------

def test_decode_gbk_bytes_mislabeled_as_utf8_header() -> None:
    # 实测过 Baidu 会用 charset=utf-8 的响应头下发 GBK 字节，
    # 严格解码失败后应回退 gb18030 而不是产出一串 U+FFFD
    raw = "<html><body>查看40天预报</body></html>".encode("gbk")
    text = p.decode_html(raw, "text/html;charset=utf-8")
    assert "查看40天预报" in text
    assert _REPL not in text


def test_decode_bare_content_type_defaults_utf8() -> None:
    raw = "<html><body>上海天气</body></html>".encode("utf-8")
    assert "上海天气" in p.decode_html(raw, "text/html")


def test_decode_honors_meta_gb2312_upgraded_to_gb18030() -> None:
    raw = (
        b'<html><head><meta http-equiv="content-type" '
        b'content="text/html;charset=gb2312"></head><body>'
        + "天气预报".encode("gbk")
        + b"</body></html>"
    )
    assert "天气预报" in p.decode_html(raw, "text/html")


# ---------------------------------------------------------------------------
# is_http_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://www.baidu.com/link?url=abc",
    "https://example.com/path?q=1",
])
def test_is_http_url_accepts_http_absolute(url: str) -> None:
    assert p.is_http_url(url)


@pytest.mark.parametrize("url", [
    "/s?wd=%E4%BB%8A%E5%A4%A9&rn=8",   # 相关搜索的站内相对链接
    "javascript:void(0)",
    "ftp://example.com/x",
    "",
    "http://",                          # 无 netloc
])
def test_is_http_url_rejects_unsafe(url: str) -> None:
    assert not p.is_http_url(url)


# ---------------------------------------------------------------------------
# parse_baidu_html
# ---------------------------------------------------------------------------

_BAIDU_HTML = f"""
<html><body><div id="content_left">

  <!-- 正常结果：标题带 em 高亮和 iconfont 字符，摘要带零宽字符 -->
  <div class="result c-container">
    <h3 class="t"><a href="http://www.baidu.com/link?url=AAA">上海<em>天气</em>预报{_PUA}</a></h3>
    <div class="c-abstract">今日{_ZWSP}多云&nbsp;33/28℃</div>
  </div>

  <!-- 卡片：第一个 a 是子链接且无 h3 —— 整个容器应被跳过 -->
  <div class="c-container">
    <a href="http://www.baidu.com/link?url=SUB">查看40天预报</a>
    <span>61%</span>
  </div>

  <!-- 相关搜索：有 h3 但 href 是站内相对链接 —— 应被拒绝 -->
  <div class="c-container">
    <h3><a href="/s?wd=%E4%BB%8A%E5%A4%A9&rn=8">今天上海的气温是多少</a></h3>
  </div>

  <!-- javascript 伪协议 —— 应被拒绝 -->
  <div class="c-container">
    <h3><a href="javascript:void(0)">点击展开更多结果</a></h3>
  </div>

  <!-- 商业推广 —— 应被跳过 -->
  <div class="c-container" data-tuiguang="1">
    <h3><a href="http://www.baidu.com/link?url=ADS">买天气仪器上XX商城</a></h3>
  </div>

  <!-- 新版模板：content-right 带 CSS-module 哈希后缀 -->
  <div class="result c-container">
    <h3><a href="http://www.baidu.com/link?url=BBB">中国天气网上海站</a></h3>
    <span class="content-right_XYZ99">权威发布{_PUA}上海天气预警</span>
  </div>

  <!-- 与第一条重复的 URL —— 应被去重 -->
  <div class="result c-container">
    <h3><a href="http://www.baidu.com/link?url=AAA">上海天气预报（重复）</a></h3>
  </div>

</div></body></html>
"""


def test_parse_baidu_picks_h3_titles_only() -> None:
    results = p.parse_baidu_html(_BAIDU_HTML, max_results=10)
    titles = [r["title"] for r in results]
    assert titles == ["上海天气预报", "中国天气网上海站"]


def test_parse_baidu_sanitizes_title_and_snippet() -> None:
    results = p.parse_baidu_html(_BAIDU_HTML, max_results=10)
    # PUA 字符被清掉、零宽空格被清掉、&nbsp; 归一为普通空格
    assert results[0]["title"] == "上海天气预报"
    assert results[0]["snippet"] == "今日多云 33/28℃"
    # content-right 哈希类名命中且清洗生效
    assert results[1]["snippet"] == "权威发布上海天气预警"


def test_parse_baidu_urls_are_absolute_http() -> None:
    results = p.parse_baidu_html(_BAIDU_HTML, max_results=10)
    assert all(r["url"].startswith("http") for r in results)
    joined = " ".join(r["title"] + r["url"] for r in results)
    assert "javascript" not in joined
    assert "查看40天预报" not in joined       # 卡片子链接没被当成结果
    assert "今天上海的气温是多少" not in joined  # 相关搜索词没被当成结果
    assert "XX商城" not in joined             # 推广容器被跳过


def test_parse_baidu_respects_max_results() -> None:
    assert len(p.parse_baidu_html(_BAIDU_HTML, max_results=1)) == 1


def test_baidu_blocked_page_detected_not_silently_empty() -> None:
    verify_page = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>百度安全验证</title></head><body>...</body></html>"
    )
    assert p.is_baidu_blocked(verify_page)
    assert not p.is_baidu_blocked(_BAIDU_HTML)


# ---------------------------------------------------------------------------
# parse_ddg_html / parse_ddg_lite_html
# ---------------------------------------------------------------------------

_DDG_HTML = f"""
<html><body>
  <div class="result results_links web-result">
    <div class="links_main result__body">
      <h2 class="result__title">
        <a class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.com%2Fneko&amp;rut=abc">Project{_ZWSP} NEKO Official</a>
      </h2>
      <a class="result__snippet">A virtual desktop companion.</a>
    </div>
  </div>
  <div class="result result--ad">
    <div class="links_main result__body">
      <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">Buy Now</a>
    </div>
  </div>
  <!-- 正常结果：目标 URL 自己的查询串带 ad_provider= 参数，不能被当广告误杀 -->
  <div class="result results_links web-result">
    <div class="links_main result__body">
      <a class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%3Fad_provider%3Dbing&amp;rut=def">Shop Landing</a>
    </div>
  </div>
</body></html>
"""


def test_parse_ddg_html_decodes_uddg_and_sanitizes() -> None:
    results = p.parse_ddg_html(_DDG_HTML, max_results=10)
    assert [r["title"] for r in results] == ["Project NEKO Official", "Shop Landing"]
    assert results[0]["url"] == "https://www.example.com/neko"
    assert results[0]["snippet"] == "A virtual desktop companion."
    assert results[1]["url"] == "https://shop.example.com/p?ad_provider=bing"


def test_ddg_ad_filter_only_matches_yjs_wrapper() -> None:
    assert p.is_ddg_ad_url("https://duckduckgo.com/y.js?ad_provider=bing&u3=x")
    assert p.is_ddg_ad_url("//duckduckgo.com/y.js?ad_provider=x")
    # 目标 URL 带同名参数、或路径里碰巧含 y.js 字样的，都不是广告
    assert not p.is_ddg_ad_url("https://shop.example.com/p?ad_provider=bing")
    assert not p.is_ddg_ad_url("https://example.com/duckduckgo.com/y.js")


_DDG_LITE_HTML = f"""
<html><body><table>
  <tr><td><a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result{_REPL} One</a></td></tr>
  <tr><td class="result-snippet">Snippet{_RLO} text</td></tr>
  <tr><td><a href="/local/nav">站内导航</a></td></tr>
</table></body></html>
"""


def test_parse_ddg_lite_sanitizes_and_skips_relative() -> None:
    results = p.parse_ddg_lite_html(_DDG_LITE_HTML, max_results=10)
    assert len(results) == 1
    assert results[0]["title"] == "Result One"
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["snippet"] == "Snippet text"
