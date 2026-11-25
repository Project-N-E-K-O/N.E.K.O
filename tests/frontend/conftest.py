"""Accept the editor license during tests of unrelated editor interactions."""
import pytest


@pytest.fixture(autouse=True)
def acknowledge_template_license(request):
    if request.node.get_closest_marker("template_license") or "page" not in request.fixturenames:
        return
    page = request.getfixturevalue("page")
    # Use the actual confirmation button, including after reload. Tests covering
    # the gate itself opt out; no application state or storage flags are patched.
    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
        const dialog = document.getElementById('builtin-template-license');
        if (!dialog) return;
        const accept = () => {
            if (!dialog.open) return;
            document.getElementById('builtin-license-acknowledge').click();
            observer.disconnect();
        };
        const observer = new MutationObserver(accept);
        observer.observe(dialog, {attributes: true, attributeFilter: ['open']});
        accept();
    }, {once: true});""")
