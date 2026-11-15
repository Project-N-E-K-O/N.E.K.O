"""Tests for DuckDuckGo parsing (replaces Google search in proactive window context).

When html.duckduckgo.com end-to-end runs are unavailable (e.g. network blocks), use real
DDG HTML structure to validate redirect decoding, ad skipping, field extraction, and limit trimming.
"""
import pytest

from utils.web_scraper import parse_duckduckgo_results


# Simplified real structure from html.duckduckgo.com/html/:
# - First: normal result, href //duckduckgo.com/l/?uddg=<urlencoded>&rut=...
# - Second: ad (class contains result--ad), must skip.
# - Third: actual URL has query parameters including %3D; verify parse_qs decodes once without corruption.
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

    # Skip ad, retaining two real results only.
    assert len(results) == 2

    first = results[0]
    assert first['title'] == 'Project NEKO Official'
    # Decode uddg redirect to actual URL.
    assert first['url'] == 'https://www.example.com/neko'
    assert 'virtual desktop companion' in first['abstract']

    # No result retains DDG redirect domain.
    assert all('duckduckgo.com/l/' not in r['url'] for r in results)
    # No ad title appears.
    assert all('Merch' not in r['title'] for r in results)


@pytest.mark.unit
def test_parse_preserves_target_url_query_params():
    """Decode actual URL query parameters (id=42, original %3D) once only, without double-decoding corruption."""
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
    # Rejected invalid uddg targets first must not pretruncate away valid later results.
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
    # uddg wraps non-http target (javascript:) or incomplete bare "https://";
    # discard entirely, never consume result slots with empty/invalid URLs.
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
