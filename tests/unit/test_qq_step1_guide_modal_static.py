import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_step1_modal_markup_present():
    # The NapCat first-step onboarding moved from a standalone modal in index.html
    # (index.html is now just the napcat/open-platform mode picker) to the embedded
    # "配置引导" guide page in napcat.html.
    html = (ROOT / "plugin/plugins/qq_auto_reply/static/napcat.html").read_text(encoding="utf-8")
    assert 'id="guide-detail-step-napcat"' in html
    assert 'data-i18n="ui.shared.card.guide"' in html
    assert 'https://github.com/NapNeko/NapCatQQ/releases' in html


def test_step1_state_persisted_in_config_and_backend():
    config_store = (ROOT / "plugin/plugins/qq_auto_reply/config_store.py").read_text(encoding="utf-8")
    backend = (ROOT / "plugin/plugins/qq_auto_reply/__init__.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "plugin/plugins/qq_auto_reply/dashboard_service.py").read_text(encoding="utf-8")
    assert '"guide_step_napcat_done": False' in config_store
    assert 'guide_step_napcat_done' in backend
    # Runtime status is built by the runtime service, and the napcat step's done-state
    # is derived from a live managed+running NapCat process in the dashboard service.
    assert 'runtime = self.plugin.runtime_service.build_runtime_status()' in dashboard
    assert 'runtime["napcat_managed"] and runtime["napcat_running"]' in dashboard


def test_step1_frontend_handlers_present():
    script = (ROOT / "plugin/plugins/qq_auto_reply/static/script.js").read_text(encoding="utf-8")
    assert 'function openStep1GuideModal()' in script
    assert 'async function confirmStep1GuideModal()' in script
    assert "guide_step_napcat_done: true" in script
    assert "document.getElementById('guide-step-napcat').addEventListener('click', () => {" in script
