import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_step1_modal_markup_present():
    html = (ROOT / "plugin/plugins/qq_auto_reply/static/index.html").read_text(encoding="utf-8")
    assert 'id="step1-guide-modal-overlay"' in html
    assert 'ui.guide.step1.modal.title' in html
    assert 'https://github.com/NapNeko/NapCatQQ/releases' in html


def test_step1_state_persisted_in_config_and_backend():
    config_store = (ROOT / "plugin/plugins/qq_auto_reply/config_store.py").read_text(encoding="utf-8")
    backend = (ROOT / "plugin/plugins/qq_auto_reply/__init__.py").read_text(encoding="utf-8")
    assert '"guide_step_napcat_done": False' in config_store
    assert 'guide_step_napcat_done' in backend
    assert 'runtime = self._build_runtime_status()' in backend
    assert 'runtime["napcat_managed"] and runtime["napcat_running"]' in backend


def test_step1_frontend_handlers_present():
    script = (ROOT / "plugin/plugins/qq_auto_reply/static/script.js").read_text(encoding="utf-8")
    assert 'function openStep1GuideModal()' in script
    assert 'async function confirmStep1GuideModal()' in script
    assert "guide_step_napcat_done: true" in script
    assert "document.getElementById('guide-step-napcat').addEventListener('click', () => {" in script
