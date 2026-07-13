"""Tests for Baidu search parsing in utils/web_scraper.py.

Mirrors the web_search plugin fixes (plugin/plugins/web_search/_parsing.py):
- titles come only from links under h3, no longer the first <a> in the
  container (card sub-links / related-search suggestions were misreported);
- javascript: and other pseudo-scheme links are rejected;
- titles/abstracts are sanitized of iconfont private-use glyphs, zero-width
  characters, U+FFFD and other illegal characters.

Invisible/private-use characters are written as \\u escapes only, so no
naked-eye-invisible literals appear in the source.
"""
import pytest

from utils.web_scraper import parse_baidu_results, _sanitize_search_text

_PUA = "\ue687"   # Baidu iconfont 私有区字符（实测页面中出现）
_ZWSP = "\u200b"  # 零宽空格
_REPL = "\ufffd"  # replacement character

_BAIDU_HTML = f"""
<html><body><div id="content_left">

  <!-- 正常结果：标题带 em 高亮和 iconfont 字符，摘要带零宽字符 -->
  <div class="result c-container">
    <h3 class="t"><a href="http://www.baidu.com/link?url=AAA">上海<em>气象</em>预报站{_PUA}</a></h3>
    <span class="content-right_XYZ99">今日{_ZWSP}多云&nbsp;33/28℃{_REPL}</span>
  </div>

  <!-- 卡片：第一个 a 是子链接且无 h3 —— 修复前会被当成标题 -->
  <div class="c-container">
    <a href="http://www.baidu.com/link?url=SUB">查看40天预报详情页</a>
    <span>61%</span>
  </div>

  <!-- javascript 伪协议 —— 应被拒绝 -->
  <div class="c-container">
    <h3><a href="javascript:void(0)">点击展开更多结果内容</a></h3>
  </div>

  <!-- 相关搜索：站内相对链接 —— 不能被 urljoin 洗白成结果 -->
  <div class="c-container">
    <h3><a href="/s?wd=%E4%BB%8A%E5%A4%A9&rn=8">今天上海的气温是多少度</a></h3>
  </div>

  <div class="result c-container">
    <h3><a href="http://www.baidu.com/link?url=BBB">中国天气网权威预警发布</a></h3>
  </div>

</div></body></html>
"""


@pytest.mark.unit
def test_sanitize_search_text_strips_illegal_chars():
    assert _sanitize_search_text(f"上海{_ZWSP}天气{_PUA}预报{_REPL}") == "上海天气预报"
    assert _sanitize_search_text("多云\xa0转晴\n 33℃ ") == "多云 转晴 33℃"
    assert _sanitize_search_text("") == ""


@pytest.mark.unit
def test_parse_baidu_takes_h3_titles_and_sanitizes():
    results = parse_baidu_results(_BAIDU_HTML, limit=10)

    titles = [r['title'] for r in results]
    assert titles == ['上海气象预报站', '中国天气网权威预警发布']
    # 摘要清洗：零宽/U+FFFD 被清掉，&nbsp; 归一为普通空格
    assert results[0]['abstract'] == '今日多云 33/28℃'


@pytest.mark.unit
def test_parse_baidu_rejects_unsafe_links():
    results = parse_baidu_results(_BAIDU_HTML, limit=10)

    joined = ' '.join(r['title'] + r['url'] for r in results)
    assert 'javascript' not in joined
    assert '查看40天预报详情页' not in joined  # 卡片子链接不再被当成结果标题
    assert '今天上海的气温是多少度' not in joined  # 相关搜索的站内相对链接被拒绝
    assert all(r['url'].startswith(('http://', 'https://')) for r in results)


@pytest.mark.unit
def test_parse_baidu_scans_past_rejected_containers():
    # 大量相关搜索容器排在前面时，不能因预截断漏掉后面的有效结果
    junk = ''.join(
        f'<div class="c-container"><h3><a href="/s?wd=related{i}">相关搜索推荐词条目{i}</a></h3></div>'
        for i in range(10)
    )
    html = (
        f'<html><body><div id="content_left">{junk}'
        '<div class="result c-container">'
        '<h3><a href="http://www.baidu.com/link?url=REAL">真实结果标题条目在后面</a></h3>'
        '</div></div></body></html>'
    )
    results = parse_baidu_results(html, limit=3)
    assert [r['title'] for r in results] == ['真实结果标题条目在后面']


@pytest.mark.unit
def test_parse_baidu_empty_html_returns_empty_list():
    assert parse_baidu_results('<html><body>no results</body></html>', limit=5) == []
