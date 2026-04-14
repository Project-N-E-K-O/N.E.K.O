import pytest
from playwright.sync_api import Page, expect


@pytest.mark.frontend
def test_model_manager_mmd_loading_overlay(mock_page: Page, running_server: str):
    url = f"{running_server}/model_manager"
    mock_page.goto(url)
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_function("() => !!window.MMDLoadingOverlay", timeout=15000)

    mock_page.evaluate(
        """
        () => {
            const overlay = window.MMDLoadingOverlay;
            window.__mmdOverlayEvents = [];

            const origBegin = overlay.begin.bind(overlay);
            const origUpdate = overlay.update.bind(overlay);
            const origEnd = overlay.end.bind(overlay);
            const origFail = overlay.fail.bind(overlay);

            overlay.begin = (sessionId, payload = {}) => {
                window.__mmdOverlayEvents.push({ type: 'begin', stage: payload.stage || '' });
                return origBegin(sessionId, payload);
            };
            overlay.update = (sessionId, payload = {}) => {
                window.__mmdOverlayEvents.push({ type: 'update', stage: payload.stage || '' });
                return origUpdate(sessionId, payload);
            };
            overlay.end = (sessionId) => {
                window.__mmdOverlayEvents.push({ type: 'end', stage: '' });
                return origEnd(sessionId);
            };
            overlay.fail = (sessionId, payload = {}) => {
                window.__mmdOverlayEvents.push({ type: 'fail', stage: payload.stage || 'failed' });
                return origFail(sessionId, payload);
            };

            window.mmdModuleLoaded = true;
            window._mmdModulesLoading = false;
            window._mmdModulesFailed = null;
            window.mmdManager = null;

            window.initMMDModel = async function () {
                const fakeManager = {
                    scene: {},
                    enablePhysics: true,
                    currentModel: { mesh: {} },
                    stopAnimation() {},
                    applySettings() {},
                    playAnimation() {},
                    waitForRenderFrame() {
                        return new Promise((resolve) => setTimeout(resolve, 40));
                    },
                    loadModel(_modelPath, options = {}) {
                        window.__capturedMmdLoadOptions = options;
                        return new Promise((resolve) => {
                            setTimeout(() => {
                                if (options.loadingSessionId) {
                                    window.MMDLoadingOverlay.update(options.loadingSessionId, {
                                        stage: 'physics'
                                    });
                                }
                                resolve({ name: 'Fake MMD Model' });
                            }, 120);
                        });
                    },
                    loadAnimation() {
                        return new Promise((resolve) => setTimeout(resolve, 120));
                    }
                };
                window.mmdManager = fakeManager;
                return fakeManager;
            };

            const select = document.getElementById('mmd-model-select');
            select.innerHTML = `
                <option value="">请选择</option>
                <option value="/static/mmd/Miku/Miku.pmx">Fake MMD</option>
            `;
            select.value = '/static/mmd/Miku/Miku.pmx';
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """
    )

    overlay = mock_page.locator("#neko-mmd-loading-overlay")
    expect(overlay).to_have_class("is-visible", timeout=5000)
    mock_page.wait_for_function(
        """
        () => {
            const stages = (window.__mmdOverlayEvents || [])
                .map((entry) => entry.stage)
                .filter(Boolean);
            return ['engine', 'settings', 'model', 'physics', 'idle', 'done']
                .every((stage) => stages.includes(stage));
        }
        """,
        timeout=8000,
    )
    mock_page.wait_for_function(
        "() => !!window.__capturedMmdLoadOptions && !!window.__capturedMmdLoadOptions.loadingSessionId",
        timeout=5000,
    )
    mock_page.wait_for_function(
        """
        () => {
            const overlay = document.getElementById('neko-mmd-loading-overlay');
            return !!overlay && overlay.hidden === true;
        }
        """,
        timeout=8000,
    )

    events = mock_page.evaluate("window.__mmdOverlayEvents")
    stages = [entry["stage"] for entry in events if entry["stage"]]
    assert stages[0] == "engine"
    assert "physics" in stages
    assert "idle" in stages
    assert "done" in stages
    assert not any(entry["type"] == "fail" for entry in events)
