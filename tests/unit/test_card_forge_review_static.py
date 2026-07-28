import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_card_forge_polling_and_interactions_are_lifecycle_safe_and_keyboard_accessible():
    source = _read("frontend/card-forge/src/App.jsx")
    modal_source = _read("frontend/card-forge/src/components/CardInspectModal.jsx")

    assert source.count("let requestVersion = 0") == 2
    assert "if (!cancelled && version === requestVersion) setCloudInventory(cards)" in source
    assert source.count("cancelled || version !== requestVersion") >= 2
    assert "forgedCard.storyGenerationStatus === 'temporary-fallback'" in source
    assert "throw new Error('forge_story_temporary_fallback')" in source
    assert "故事生成暂时不可用，请再次点击所选卡片重试。" in source
    assert "'铸造未完成，请再次点击所选卡片重试。'" in source
    assert "hasForgedRef.current = false" in source
    assert source.count('role="button"') >= 2
    assert source.count("event.key === 'Enter' || event.key === ' '") >= 2
    assert "aria-pressed={isPicked}" in source
    assert 'role="dialog"' in modal_source
    assert 'aria-modal="true"' in modal_source
    assert "dialog?.focus()" in modal_source
    assert "previousFocus.focus?.()" in modal_source


def test_mobile_avatar_layout_keeps_screen_share_control_for_every_renderer():
    methods_source = _read("static/avatar/avatar-ui-buttons/methods-buttons.js")
    assert "id: 'screen'" in methods_source
    assert "titleKey: 'buttons.screenShare'" in methods_source
    assert "mobileOnly: true" in methods_source
    assert "btnWrapper.dataset.mobileOnly = 'true'" in methods_source
    assert "ManagerPrototype.syncResponsiveButtonVisibility" in methods_source
    assert "querySelectorAll('[data-mobile-only=\"true\"]')" in methods_source

    renderer_sources = [
        _read("static/live2d/live2d-ui-buttons.js"),
        _read("static/vrm/vrm-ui-buttons.js"),
        _read("static/mmd/mmd-ui-buttons.js"),
        _read("static/pngtuber-core.js"),
    ]
    for source in renderer_sources:
        assert "syncResponsiveButtonVisibility(buttonsContainer)" in source
        assert "if (config.mobileOnly" not in source
        assert "config.id === 'screen'" in source
    for source in renderer_sources[1:3]:
        assert "{ id: 'screen', mobileOnly: true }" in source
        assert ".filter(c => !(c.mobileOnly && !mobile))" in source


def test_mobile_screen_share_state_reconciles_after_capture_attempts():
    state_source = _read(
        "static/avatar/avatar-ui-buttons/methods-state-and-cleanup.js"
    )
    controls_source = _read("static/app/app-ui/surface-floating-controls.js")

    assert "const screenButton = document.getElementById('screenButton');" in state_source
    assert (
        "this.setButtonActive('screen', screenButton.classList.contains('active'));"
        in state_source
    )
    assert re.search(
        r"window\.addEventListener\('live2d-screen-toggle', async \(e\) => \{.*?"
        r"\} finally \{\s*"
        r"if \(typeof window\.syncFloatingScreenButtonState === 'function'\) \{\s*"
        r"window\.syncFloatingScreenButtonState\(isScreenSharingActive\(\)\);",
        controls_source,
        re.S,
    )


def test_stop_script_resolves_runtime_ports_before_terminating_processes():
    source = _read("scripts/card_forge/stop-card-forge.ps1")
    start_source = _read("scripts/card_forge/start-card-forge.bat")

    assert "$env:NEKO_MAIN_SERVER_PORT" in source
    assert "$env:MAIN_SERVER_PORT" in source
    assert '"N.E.K.O\\port_config.json"' in source
    assert "$env:NEKO_CARD_FORGE_PORT" in source
    assert "$env:CARD_FORGE_PORT" in source
    assert "$config.CARD_FORGE_PORT" in source
    assert "$cardForgePort = Get-DesktopCardForgePort" in source
    assert "$ports = @($mainServerPort, $cardForgePort, 5173)" in source
    assert '("N.E.K.O Main Server - {0}" -f $mainServerPort)' in source
    assert '("Neko Card Forge Server - {0}" -f $cardForgePort)' in source
    assert "N.E.K.O Main Server - %MAIN_SERVER_PORT_VALUE%" in start_source
    assert "Neko Card Forge Server - %CARD_FORGE_PORT_VALUE%" in start_source
    assert "function Get-CardForgeProcessIds" in source
    assert "$owned.Contains([int]$proc.ParentProcessId)" in source
    assert "$ownedProcessIds.Contains([int]$processId)" in source
    assert "$cardForgePatterns" not in source
    assert "Get-SafeCommandPreview" not in source
    assert "CommandLine" not in source
    assert source.count("Write-Verbose") >= 3


def test_windows_launchers_bootstrap_frontend_dependencies_before_services():
    python_launcher = _read("scripts/card_forge/start_card_forge.py")
    batch_launcher = _read("scripts/card_forge/start-card-forge.bat")

    assert "ensure_frontend_dependencies()" in python_launcher
    assert python_launcher.index("ensure_frontend_dependencies()") < python_launcher.index(
        'print(f"[1/3] Opening N.E.K.O main server window'
    )
    assert 'subprocess.run(["npm.cmd", "ci"]' in python_launcher
    assert 'if not exist "%FRONTEND_ROOT%\\node_modules\\.bin\\vite.cmd"' in batch_launcher
    assert "call npm ci" in batch_launcher
    assert batch_launcher.index("call npm ci") < batch_launcher.index(
        "echo [1/3] Opening N.E.K.O main server window"
    )


def test_live2d_click_actions_are_not_dispatched_by_the_generic_listener_twice():
    source = _read("static/live2d/live2d-ui-buttons.js")

    assert "window.dispatchEvent(new CustomEvent('live2d-social-click'))" in source
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'))" in source
    assert "} else if (config.id !== 'social' && config.id !== 'goodbye') {" in source
