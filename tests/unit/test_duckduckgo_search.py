"""Tests for DuckDuckGo search parsing (取代主动搭话窗口上下文里的 Google 搜索)。

无法对 html.duckduckgo.com 做端到端实跑时（如被网络屏蔽），这些测试用真实的
DDG HTML 结构校验解析器：跳转链接解码、广告跳过、字段提取、上限裁剪。
"""
import pytest

from utils.web_scraper import parse_duckduckgo_results


# 取自 html.duckduckgo.com/html/ 的真实结构（精简）：
# - 第 1 条：正常结果，href 是 //duckduckgo.com/l/?uddg=<urlencoded>&rut=...
# - 第 2 条：广告（class 含 result--ad），必须被跳过
# - 第 3 条：真实 URL 自带查询参数（含 %3D），验证 parse_qs 只解一次码、不破坏
_DDG_HTML = """
<html><body>
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.com%2Fneko&amp;rut=abc123">Project NEKO Official</a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.com%2Fneko">
        Project NEKO is a virtual desktop companion.
      </a>
    </div>
  </div>

  <div class="result result--ad result--ad--small">
    <div class="links_main result__body">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/y.js?ad_provider=x">Buy NEKO Merch Now</a>
      </h2>
      <a class="result__snippet">Sponsored listing.</a>
    </div>
  </div>

  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%3Fid%3D42&amp;rut=def">NEKO Store Page</a>
      </h2>
      <a class="result__snippet">Buy the figure here.</a>
    </div>
  </div>
</body></html>
"""


@pytest.mark.unit
def test_parse_decodes_uddg_redirect_and_skips_ads():
    results = parse_duckduckgo_results(_DDG_HTML, limit=10)

    # 广告被跳过，只剩两条真实结果
    assert len(results) == 2

    first = results[0]
    assert first['title'] == 'Project NEKO Official'
    # uddg 跳转链接被解码成真实地址
    assert first['url'] == 'https://www.example.com/neko'
    assert 'virtual desktop companion' in first['abstract']

    # 任何结果都不应残留 DDG 跳转域名
    assert all('duckduckgo.com/l/' not in r['url'] for r in results)
    # 广告标题不应出现
    assert all('Merch' not in r['title'] for r in results)


@pytest.mark.unit
def test_parse_preserves_target_url_query_params():
    """真实 URL 自带的查询参数（id=42，原文 %3D）只被解一次码，不被二次破坏。"""
    results = parse_duckduckgo_results(_DDG_HTML, limit=10)
    store = next(r for r in results if r['title'] == 'NEKO Store Page')
    assert store['url'] == 'https://shop.example.com/p?id=42'


@pytest.mark.unit
def test_parse_respects_limit():
    results = parse_duckduckgo_results(_DDG_HTML, limit=1)
    assert len(results) == 1


@pytest.mark.unit
def test_parse_empty_html_returns_empty_list():
    assert parse_duckduckgo_results('<html><body>no results</body></html>', limit=5) == []


@pytest.mark.unit
def test_parse_scans_past_rejected_results():
    # 被拒绝的条目（无效 uddg 目标）排在前面时，不能因预截断漏掉后面的有效结果
    junk = ''.join(
        f'''
        <div class="result results_links web-result">
          <div class="links_main result__body">
            <h2 class="result__title">
              <a class="result__a" href="//duckduckgo.com/l/?uddg=javascript%3Avoid({i})">Broken Entry {i}</a>
            </h2>
          </div>
        </div>
        '''
        for i in range(10)
    )
    html = junk + '''
    <div class="result results_links web-result">
      <div class="links_main result__body">
        <h2 class="result__title">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fvalid">Valid Trailing Result</a>
        </h2>
      </div>
    </div>
    '''
    results = parse_duckduckgo_results(html, limit=3)
    assert [r['title'] for r in results] == ['Valid Trailing Result']


@pytest.mark.unit
def test_parse_skips_results_without_usable_url():
    # uddg 包着非 http 目标（javascript:）或残缺目标（裸 "https://"）
    # → 整条丢弃，不以空/无效 URL 占用结果位
    html = '''
    <div class="result results_links web-result">
      <div class="links_main result__body">
        <h2 class="result__title">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=javascript%3Aalert(1)&amp;rut=x">Malicious Entry</a>
        </h2>
        <a class="result__snippet">Should never surface.</a>
      </div>
    </div>
    <div class="result results_links web-result">
      <div class="links_main result__body">
        <h2 class="result__title">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F&amp;rut=y">Truncated Target</a>
        </h2>
      </div>
    </div>
    '''
    assert parse_duckduckgo_results(html, limit=5) == []
