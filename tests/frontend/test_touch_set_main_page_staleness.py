# -*- coding: utf-8 -*-
"""Regression tests: the running main page must pick up touch_set saves without reload.

User-reported bug: a custom Live2D touch area saved in the model manager persists
fine (PATCH /api/characters/catgirl/{name}/touch_set), but the running main page
keeps using the touch_set snapshot taken at page load, so the custom area does
nothing until the main page is reloaded.

Root cause: touch-config.js `InitializationTouchSet()` runs once at page startup
(startTouchConfigAfterStorageBarrier) and snapshots /api/characters's
`_reserved.touch_set` into window.live2dManager.touchSet. Model-manager saves
only trigger `reload_model` (model reload in
bootstrap-resources-and-model-reload.js, which never refetches touch_set), so
the running page keeps hit-testing against the stale snapshot
(_getCustomTouchAreaIdAtPoint -> no rect -> routes to default/random animation).

Fix (PR #3471):
  A. touch-config.js adds `nekoRefreshLive2DTouchSet()`: on model-load completion
     hooks it rewires native HitArea listeners when the data is fresh (<5s), and
     refetches when stale; `InitializationTouchSet` no longer passes `undefined`
     to setupHitAreaInteraction when the model is not ready, and no longer
     creates a junk `"null"` key when modelName is empty.
  B. A successful save broadcasts on the `neko_touch_set_channel`
     BroadcastChannel; the main page force-refetches on receipt (silently
     degrades to the old refresh-to-apply behavior without BroadcastChannel).
  C. live2d-init.js (initial load) and bootstrap-resources-and-model-reload.js
     (hot switch) each hook the refresh after a Live2D model finishes loading.
  D. Review fixes: a monotonically increasing generation discards stale
     /api/characters responses when refreshes overlap (older response must never
     clobber a newer one), and load-completion callers pass
     `allowLive2DReload` so stale `localStorage.modelType` /
     `vrm/mmdManager.currentModel` (the hot-switch path only pauses VRM/MMD
     rendering without clearing currentModel) cannot block the refresh.
  E. A save broadcast that arrives before the model exists records a pending
     force-refetch flag consumed by the next load-completion refresh, so the
     pre-save startup snapshot cannot shadow it.

The route probe below mirrors the onDragEnd click branch exactly
(_getCustomTouchAreaIdAtPoint + _getPreferredTouchSetHitArea); every phase
repeats n=3 times per the repo's attribution-experiment standard.
"""

import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page

CUSTOM_AREA_ID = "custom_repro_stale_area_1"
CUSTOM_RECT = {"x": 0.4, "y": 0.3, "width": 0.2, "height": 0.2}
PROBE_NX = 0.5  # probe point = rect center, normalized within model bounds
PROBE_NY = 0.4
REPEATS = 3


def _wait_main_page_ready(page: Page, timeout_ms: int = 60000):
    page.wait_for_function(
        """() => {
            const m = window.live2dManager;
            return !!(m && m.currentModel && !m.currentModel.destroyed
                && m.modelName && m.touchSet && m.touchSet[m.modelName]);
        }""",
        timeout=timeout_ms,
    )


def _wait_boot_settled(page: Page, timeout_ms: int = 60000):
    """Wait out the page's own startup model hot-switch so timing-sensitive
    scenarios are not interleaved with a background reload + touch-set commit.

    # The app's boot flow runs handleModelReload once the character config is
    # ready; its completion hook force-commits a touch-set refresh. That
    # hot-switch is triggered by server-side character loading and can fire long
    # after page load, so "no reload in flight right now" is not enough — wait
    # until the boot reload has actually COMPLETED (_lastModelReloadAt set) and
    # nothing is in flight, then leave a stability window.
    """
    try:
        # The hot-switch normally lands within a few seconds of the model being
        # ready; a boot that only did the direct initial load never sets the
        # marker, so cap this wait instead of burning the full timeout.
        page.wait_for_function(
            "() => Number(window._lastModelReloadAt || 0) > 0",
            timeout=10000,
        )
    except Exception:
        # Some boots may skip the hot-switch entirely; fall back to the
        # in-flight check + stability window instead of hard-failing.
        pass
    page.wait_for_function(
        "() => window._modelReloadInFlight !== true",
        timeout=timeout_ms,
    )
    page.wait_for_timeout(2500)
    _wait_main_page_ready(page)


def _wait_probe_hits(page: Page, area_id: str, timeout_ms: int = 15000):
    # Poll the route probe until the custom area is hit at the rect center
    # (without reloading the page).
    deadline = time.monotonic() + timeout_ms / 1000
    last_probe = None
    while time.monotonic() < deadline:
        last_probe = _route_probe(page)
        if (
            "error" not in last_probe
            and last_probe.get("customAreaId") == area_id
            and last_probe.get("useBlock") == area_id
        ):
            return last_probe
        page.wait_for_timeout(300)
    raise AssertionError(
        f"custom area {area_id} not hit within {timeout_ms}ms, last probe: {last_probe}"
    )


def _route_probe(page: Page):
    # Runs the same decision sequence as the onDragEnd click branch:
    #   customAreaId = _getCustomTouchAreaIdAtPoint(x, y)
    #   useBlock     = _getPreferredTouchSetHitArea(hitAreas, customAreaId)
    return page.evaluate(
        """({ nx, ny }) => {
            const manager = window.live2dManager;
            const b = manager._getModelBoundsRect(manager.currentModel);
            if (!b) return { error: 'no model bounds' };
            const px = b.left + nx * b.width;
            const py = b.top + ny * b.height;
            const customAreaId = manager._getCustomTouchAreaIdAtPoint(px, py);
            const hitAreas = Array.isArray(manager._lastTouchHitAreas)
                ? manager._lastTouchHitAreas
                : [];
            const useBlock = manager._getPreferredTouchSetHitArea(hitAreas, customAreaId);
            return {
                modelName: manager.modelName,
                inMemoryTouchSetKeys: Object.keys(manager.touchSet[manager.modelName] || {}),
                customAreaId,
                useBlock,
            };
        }""",
        {"nx": PROBE_NX, "ny": PROBE_NY},
    )


def _patch_touch_set_as_model_manager_does(page: Page, area_id: str = CUSTOM_AREA_ID) -> dict:
    # Mimics the model-manager save: replace touch_set[modelName] wholesale,
    # including one custom area.
    return page.evaluate(
        """async ({ areaId, rect }) => {
            const lanlan = new URLSearchParams(window.location.search).get('lanlan_name')
                || window.lanlan_config?.lanlan_name;
            const modelName = window.live2dManager.modelName;
            const touchSet = {
                default: { motions: [], expressions: [] },
                [areaId]: {
                    motions: [],
                    expressions: [],
                    customArea: {
                        id: areaId, type: 'rect', name: 'repro-stale',
                        rect, createdAt: Date.now(),
                    },
                },
            };
            const resp = await fetch(
                `/api/characters/catgirl/${encodeURIComponent(lanlan)}/touch_set`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model_name: modelName, touch_set: touchSet }),
                }
            );
            return await resp.json();
        }""",
        {"areaId": area_id, "rect": CUSTOM_RECT},
    )


def _server_has_custom_area(page: Page) -> bool:
    return page.evaluate(
        """async (areaId) => {
            const characters = await (await fetch('/api/characters')).json();
            const lanlan = new URLSearchParams(window.location.search).get('lanlan_name')
                || window.lanlan_config?.lanlan_name;
            const reserved = characters['猫娘']?.[lanlan]?._reserved || {};
            const saved = reserved.touch_set?.[window.live2dManager.modelName] || {};
            return !!(saved[areaId] && saved[areaId].customArea);
        }""",
        CUSTOM_AREA_ID,
    )


@pytest.mark.frontend
def test_custom_touch_area_saved_in_model_manager_reaches_main_page_only_after_reload(
    mock_page: Page, running_server: str
):
    """Cold-start baseline: a persisted custom area is hit right after a fresh page load.

    # (History: an earlier revision asserted the page MUST stay stale without a
    # notification; after the fix introduced two legitimate refresh paths
    # (load completion + save broadcast) the page self-heals, so that assertion
    # no longer holds and has been superseded by the fix-verification tests.)
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    # Simulate the model-manager save.
    patch_result = _patch_touch_set_as_model_manager_does(mock_page)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"
    assert _server_has_custom_area(mock_page), "custom area not persisted server-side"

    # Cold start: the same data must route to the custom area.
    mock_page.reload(wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)

    for attempt in range(REPEATS):
        probe = _route_probe(mock_page)
        assert "error" not in probe, f"probe {attempt + 1} failed: {probe}"
        assert CUSTOM_AREA_ID in probe["inMemoryTouchSetKeys"], (
            f"attempt {attempt + 1}: in-memory snapshot should contain the area: {probe}"
        )
        assert probe["customAreaId"] == CUSTOM_AREA_ID, (
            f"attempt {attempt + 1}: the same point should hit the custom area: {probe}"
        )
        assert probe["useBlock"] == CUSTOM_AREA_ID, (
            f"attempt {attempt + 1}: routing should target the custom area: {probe}"
        )
        mock_page.wait_for_timeout(1000)


@pytest.mark.frontend
def test_touch_set_broadcast_reaches_running_main_page_without_reload(
    mock_page: Page, running_server: str
):
    """Fix B: a save broadcast on neko_touch_set_channel applies without page reload.

    # The signal is posted through a SECOND BroadcastChannel object with the same
    # name, simulating the model-manager window. (BroadcastChannel never delivers
    # a message back to the sending channel object itself — review finding.)
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_fix_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    mock_page.evaluate(
        """() => {
            const sender = new BroadcastChannel('neko_touch_set_channel');
            sender.postMessage({
                model_name: window.live2dManager.modelName || '',
                timestamp: Date.now()
            });
            setTimeout(() => { try { sender.close(); } catch (_) {} }, 100);
        }"""
    )

    for attempt in range(REPEATS):
        probe = _wait_probe_hits(mock_page, area_id)
        assert area_id in probe["inMemoryTouchSetKeys"], (
            f"attempt {attempt + 1}: snapshot should contain the area: {probe['inMemoryTouchSetKeys']}"
        )
        mock_page.wait_for_timeout(1000)


@pytest.mark.frontend
def test_model_reload_hook_refetches_touch_set(
    mock_page: Page, running_server: str
):
    """Fix C: after a real handleModelReload, a newly saved area applies without reload."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_reload_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    reload_result = mock_page.evaluate(
        """async () => {
            if (typeof window.handleModelReload !== 'function') {
                return { success: false, error: 'handleModelReload unavailable' };
            }
            const lanlan = new URLSearchParams(window.location.search).get('lanlan_name')
                || window.lanlan_config?.lanlan_name;
            await window.handleModelReload(lanlan);
            return { success: true };
        }"""
    )
    assert reload_result.get("success"), f"model hot switch failed: {reload_result}"

    _wait_main_page_ready(mock_page)
    for attempt in range(REPEATS):
        probe = _wait_probe_hits(mock_page, area_id)
        assert probe["useBlock"] == area_id
        mock_page.wait_for_timeout(1000)


@pytest.mark.frontend
def test_refresh_hook_rewires_native_hit_area_listener(
    mock_page: Page, running_server: str
):
    """Fix A: the refresh hook rewires the native HitArea listener for the current model.

    # Simulates the slow-load state: wiring was never done and the data is marked
    # stale, so the refresh takes the full refetch path.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    for attempt in range(REPEATS):
        state = mock_page.evaluate(
            """() => {
                const manager = window.live2dManager;
                manager._touchSetHitModel = null;
                manager._touchSetHitHandler = null;
                manager._touchSetLoadedAt = 0;  // stale marker -> full refetch path
                return { modelName: manager.modelName };
            }"""
        )
        assert state["modelName"], "main page model name should not be empty"

        refreshed = mock_page.evaluate(
            """async () => {
                if (typeof window.nekoRefreshLive2DTouchSet !== 'function') {
                    return { success: false, error: 'refresh hook unavailable' };
                }
                return { success: await window.nekoRefreshLive2DTouchSet() === true };
            }"""
        )
        assert refreshed.get("success"), f"refresh hook failed: {refreshed}"

        wired = mock_page.evaluate(
            """() => {
                const manager = window.live2dManager;
                return {
                    hasHandler: typeof manager._touchSetHitHandler === 'function',
                    boundToCurrentModel: manager._touchSetHitModel === manager.currentModel,
                    loadedAtFresh: (Date.now() - Number(manager._touchSetLoadedAt || 0)) < 5000,
                };
            }"""
        )
        assert wired["hasHandler"], f"attempt {attempt + 1}: listener not rewired: {wired}"
        assert wired["boundToCurrentModel"], (
            f"attempt {attempt + 1}: listener should bind to the current model: {wired}"
        )
        assert wired["loadedAtFresh"], (
            f"attempt {attempt + 1}: touch data should be refetched: {wired}"
        )
        mock_page.wait_for_timeout(500)


@pytest.mark.frontend
def test_concurrent_refresh_discards_stale_response(
    mock_page: Page, running_server: str
):
    """Fix D (race): an older /api/characters response must never clobber a newer one.

    # Overlapping refreshes (rapid saves / save + hot switch) each call
    # InitializationTouchSet independently. Without the generation guard, the
    # older GET resolving last would overwrite touchSet with older data and mark
    # it fresh. Here the first response is artificially delayed and stripped of
    # the custom area; the guard must discard it.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_race_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    # Intercept GET /api/characters (exact path): response #1 is delayed 1.5s
    # and stripped of the custom area (simulating an older snapshot); later
    # responses return fast. The interceptor records whether stripping actually
    # happened so the test cannot pass vacuously. Unrelated page requests are
    # passed through untouched until the test arms the interceptor, so an
    # in-flight pre-PATCH GET cannot steal the callNo===1 slot.
    mock_page.evaluate(
        """(areaId) => {
            const realFetch = window.fetch;
            window.__realFetch = realFetch;
            window.__raceArmed = false;
            window.__charFetchCount = 0;
            window.__firstResponseStripped = false;
            window.fetch = async (url, opts) => {
                let parsed;
                try {
                    parsed = new URL(String(url), window.location.href);
                } catch (_) {
                    return realFetch(url, opts);
                }
                const method = String((opts && opts.method) || 'GET').toUpperCase();
                if (parsed.pathname === '/api/characters' && method === 'GET') {
                    if (!window.__raceArmed) return realFetch(url, opts);
                    // 只拦截来自 InitializationTouchSet 的请求：无关页面代码的
                    // 同路径 GET 原样放行，不能占用 callNo===1 的延迟/剥离名额。
                    const stack = new Error().stack || '';
                    if (!stack.includes('InitializationTouchSet')) {
                        return realFetch(url, opts);
                    }
                    window.__charFetchCount += 1;
                    const callNo = window.__charFetchCount;
                    const resp = await realFetch(url, opts);
                    const data = await resp.json();
                    const lanlan = new URLSearchParams(window.location.search).get('lanlan_name')
                        || window.lanlan_config?.lanlan_name;
                    const modelName = window.live2dManager.modelName;
                    if (callNo === 1) {
                        await new Promise(r => setTimeout(r, 1500));
                        const bucket = data['猫娘']?.[lanlan]?._reserved?.touch_set?.[modelName];
                        if (bucket && areaId in bucket) {
                            delete bucket[areaId];
                            window.__firstResponseStripped = true;
                        }
                    } else {
                        await new Promise(r => setTimeout(r, 50));
                    }
                    return new Response(JSON.stringify(data), {
                        status: resp.status,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return realFetch(url, opts);
            };
        }""",
        area_id,
    )

    try:
        # Arm only now: every /api/characters GET from this point on belongs to
        # the scenario (the PATCH above used a sub-path and is never matched).
        mock_page.evaluate("() => { window.__raceArmed = true; }")
        overlap = mock_page.evaluate(
            """async () => {
                const r1 = window.nekoRefreshLive2DTouchSet({ forceRefetch: true });
                await new Promise(r => setTimeout(r, 200));  // let request #1 go out
                const r2 = window.nekoRefreshLive2DTouchSet({ forceRefetch: true });
                await r2;
                await r1;
                return {
                    charFetchCount: window.__charFetchCount,
                    firstResponseStripped: window.__firstResponseStripped,
                };
            }"""
        )
        assert overlap.get("charFetchCount", 0) >= 2, (
            f"expected two /api/characters calls, got: {overlap}"
        )
        assert overlap.get("firstResponseStripped") is True, (
            "interceptor failed to strip the custom area from response #1; "
            "the test would be vacuous"
        )

        # 被取代的刷新不得安排幻影重试：r1 已被 r2 取代，其重试的世代号递增
        # 会丢弃 r2 的在途结果（coderabbit 第 9 轮）。等过 1.5s 重试窗口后，
        # 计数必须仍为 2。
        mock_page.wait_for_timeout(1800)
        count_after_retry_window = mock_page.evaluate("() => window.__charFetchCount")
        assert count_after_retry_window == 2, (
            f"a superseded refresh scheduled a phantom retry; "
            f"expected count to stay 2, got {count_after_retry_window}"
        )

        probe = _route_probe(mock_page)
        assert "error" not in probe, f"probe failed: {probe}"
        assert area_id in probe["inMemoryTouchSetKeys"], (
            f"stale response was applied; snapshot keys: {probe['inMemoryTouchSetKeys']}"
        )
    finally:
        mock_page.evaluate(
            """() => {
                if (window.__realFetch) window.fetch = window.__realFetch;
            }"""
        )


@pytest.mark.frontend
def test_live2d_reload_hook_bypasses_stale_type_guards(
    mock_page: Page, running_server: str
):
    """Fix D (guards): load-completion refresh must bypass stale type-guard state.

    # A VRM->Live2D hot switch only pauses VRM rendering; localStorage.modelType
    # and vrmManager.currentModel can stay stale and trip the type guard. With
    # allowLive2DReload the refresh must still apply; without the flag the guard
    # blocks the refetch (BC-notification path keeps the guard).
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_guard_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    result = mock_page.evaluate(
        """async (areaId) => {
            const manager = window.live2dManager;
            const prevModelType = localStorage.getItem('modelType');
            const prevVrmManager = window.vrmManager;
            // The main page starts with window.vrmManager = null and only
            // assigns the real manager once the VRM scripts finish loading, so
            // both null and undefined mean "absent" here.
            const hadVrmManager = prevVrmManager !== null && prevVrmManager !== undefined;
            // Save before mutating: prevVrmManager IS window.vrmManager, so
            // reading its currentModel later would read back the forged value.
            const prevVrmCurrentModel = hadVrmManager ? prevVrmManager.currentModel : undefined;
            // Forge the stale guard state: type says live3d, VRM "in use".
            localStorage.setItem('modelType', 'live3d');
            if (!hadVrmManager) window.vrmManager = {};
            window.vrmManager.currentModel = {};
            // Wipe the in-memory config so a refill is observable.
            delete manager.touchSet[manager.modelName];
            try {
                // 1) Without the bypass: the stale guard must block the refetch.
                await window.nekoRefreshLive2DTouchSet({ forceRefetch: true });
                const blockedKeys = Object.keys(manager.touchSet[manager.modelName] || {});
                // 2) With the bypass: the refresh must apply.
                await window.nekoRefreshLive2DTouchSet({
                    forceRefetch: true,
                    allowLive2DReload: true
                });
                const refilledKeys = Object.keys(manager.touchSet[manager.modelName] || {});
                return {
                    blockedKeys,
                    refilledKeys,
                    prevModelType,
                    hadVrmManager,
                };
            } finally {
                if (prevModelType === null) localStorage.removeItem('modelType');
                else localStorage.setItem('modelType', prevModelType);
                if (!hadVrmManager) {
                    // Restore the true original: undefined means it never
                    // existed, null means the VRM scripts had not run yet.
                    if (prevVrmManager === undefined) delete window.vrmManager;
                    else window.vrmManager = prevVrmManager;
                } else {
                    window.vrmManager.currentModel = prevVrmCurrentModel;
                }
            }
        }""",
        area_id,
    )

    assert area_id not in result["blockedKeys"], (
        f"without bypass the stale guard should block the refill: {result['blockedKeys']}"
    )
    assert area_id in result["refilledKeys"], (
        f"with allowLive2DReload the refresh should apply: {result['refilledKeys']}"
    )


@pytest.mark.frontend
def test_broadcast_while_model_loading_is_applied_once_model_ready(
    mock_page: Page, running_server: str
):
    """Fix E: a save broadcast dropped during model load must still apply once ready.

    # Codex round-4 finding: when the notification arrives before the initial
    # Live2D model exists, the old code discarded it; the startup
    # InitializationTouchSet had already fetched the pre-save snapshot and stamps
    # it fresh, so the load-completion hook (running within 5s) would only rewire
    # and the save stayed missing until another trigger. Now the dropped
    # notification records a pending force-refetch flag that the next
    # load-completion refresh consumes.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_pending_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    outcome = mock_page.evaluate(
        """async () => {
            const manager = window.live2dManager;
            const realGetCurrentModel = manager.getCurrentModel;
            try {
                // Simulate the model-still-loading window.
                manager.getCurrentModel = () => null;
                const dropped = await window.nekoRefreshLive2DTouchSet({ forceRefetch: true });
                const pendingFlag = manager._touchSetPendingForceRefetch === true;
                // Model becomes ready: the load-completion hook calls refresh
                // again (without forceRefetch — freshness alone must not skip it).
                manager.getCurrentModel = realGetCurrentModel;
                                // Model becomes ready: the load-completion hook calls refresh
                // again (without forceRefetch — freshness alone must not skip it).
                manager.getCurrentModel = realGetCurrentModel;
                // Pin freshness so the refetch can only be triggered by the
                // pending flag, not by the 5s staleness window.
                manager._touchSetLoadedAt = Date.now();
                const applied = await window.nekoRefreshLive2DTouchSet({});
                const flagConsumed = manager._touchSetPendingForceRefetch !== true;
                const keys = Object.keys(manager.touchSet[manager.modelName] || {});
                return {
                    droppedResult: dropped,
                    pendingFlag,
                    appliedResult: applied,
                    flagConsumed,
                    keys,
                };
            } finally {
                manager.getCurrentModel = realGetCurrentModel;
            }
        }"""
    )

    assert outcome["droppedResult"] is False, (
        f"refresh without a model should report False: {outcome}"
    )
    assert outcome["pendingFlag"] is True, (
        f"dropped notification should record the pending force-refetch flag: {outcome}"
    )
    assert outcome["appliedResult"] is True, (
        f"load-completion refresh should consume the flag and refetch: {outcome}"
    )
    assert outcome["flagConsumed"] is True, (
        f"load-completion refresh should clear the pending flag: {outcome}"
    )
    assert area_id in outcome["keys"], (
        f"the saved area should be applied once the model is ready: {outcome['keys']}"
    )


@pytest.mark.frontend
def test_failed_force_refetch_re_arms_pending_flag(
    mock_page: Page, running_server: str
):
    """Codex round-6: an explicit forceRefetch that fails to commit must re-arm the flag.

    # The save-broadcast receiver is one-shot: when it calls refresh with
    # forceRefetch and InitializationTouchSet cannot commit (transient
    # page_config failure), the notification must not be lost. The flag is
    # re-armed and the next plain refresh consumes it.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_force_fail_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    # Fail exactly one stack-filtered /api/characters GET with an empty
    # character table, so InitializationTouchSet resolves no character and
    # commits nothing. (Since the resolution prefers lanlan_config, page_config
    # is no longer fetched on a booted main page and cannot be the injection
    # point.)
    mock_page.evaluate(
        """() => {
            const realFetch = window.fetch;
            window.__realFetch = realFetch;
            window.__failNextCharFetch = false;
            window.__charFetchFailed = false;
            window.fetch = async (url, opts) => {
                let parsed;
                try {
                    parsed = new URL(String(url), window.location.href);
                } catch (_) {
                    return realFetch(url, opts);
                }
                const method = String((opts && opts.method) || 'GET').toUpperCase();
                if (parsed.pathname === '/api/characters' && method === 'GET'
                    && window.__failNextCharFetch === true) {
                    const stack = new Error().stack || '';
                    if (stack.includes('InitializationTouchSet')) {
                        window.__failNextCharFetch = false;
                        window.__charFetchFailed = true;
                        return new Response(JSON.stringify({ '猫娘': {} }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                }
                return realFetch(url, opts);
            };
        }"""
    )

    try:
        outcome = mock_page.evaluate(
            """async () => {
                const manager = window.live2dManager;
                // Model is present; the broadcast path uses an explicit
                // forceRefetch with no pending flag.
                window.__failNextCharFetch = true;
                const failedRefetch = await window.nekoRefreshLive2DTouchSet({
                    forceRefetch: true
                });
                const reArmed = manager._touchSetPendingForceRefetch === true;
                // Next plain refresh must consume the flag and apply the save
                // (freshness is pinned so only the flag can drive the refetch).
                manager._touchSetLoadedAt = Date.now();
                const applied = await window.nekoRefreshLive2DTouchSet({});
                const flagConsumed = manager._touchSetPendingForceRefetch !== true;
                const keys = Object.keys(manager.touchSet[manager.modelName] || {});
                return {
                    failedRefetch,
                    pageConfigFailed: window.__charFetchFailed,
                    reArmed,
                    applied,
                    flagConsumed,
                    keys,
                };
            }"""
        )
        assert outcome["pageConfigFailed"] is True, (
            f"the forced page_config failure did not trigger: {outcome}"
        )
        assert outcome["reArmed"] is True, (
            f"a failed explicit forceRefetch must re-arm the pending flag: {outcome}"
        )
        assert outcome["flagConsumed"] is True, (
            f"the next refresh should consume the re-armed flag: {outcome}"
        )
        assert area_id in outcome["keys"], (
            f"the saved area should be applied by the retry: {outcome['keys']}"
        )
    finally:
        mock_page.evaluate(
            """() => {
                if (window.__realFetch) window.fetch = window.__realFetch;
            }"""
        )


@pytest.mark.frontend
def test_failed_non_forced_refresh_is_retried(
    mock_page: Page, running_server: str
):
    """Codex round-8: a failed non-forced replacement refresh must be retried.

    # When the load-completion hook's non-forced refresh preempts the in-flight
    # startup call (the generation check discards it) and then transiently fails
    # to commit, the page would be left without touch data or native wiring
    # until another reload. refreshTouchSetForLoadedModel now schedules one
    # bounded retry (1.5s) whenever a refetch fails to commit.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_retry_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    # Fail exactly one stack-filtered /api/characters GET with an empty
    # character table so the hook-style (non-forced) refresh commits nothing;
    # the fetch stub is then restored so the scheduled retry succeeds.
    mock_page.evaluate(
        """() => {
            const realFetch = window.fetch;
            window.__realFetch = realFetch;
            window.__allowCharFetchFailures = 0;
            window.fetch = async (url, opts) => {
                let parsed;
                try {
                    parsed = new URL(String(url), window.location.href);
                } catch (_) {
                    return realFetch(url, opts);
                }
                const method = String((opts && opts.method) || 'GET').toUpperCase();
                if (parsed.pathname === '/api/characters' && method === 'GET'
                    && window.__allowCharFetchFailures > 0) {
                    const stack = new Error().stack || '';
                    if (stack.includes('InitializationTouchSet')) {
                        window.__allowCharFetchFailures -= 1;
                        return new Response(JSON.stringify({ '猫娘': {} }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                }
                return realFetch(url, opts);
            };
        }"""
    )

    try:
        outcome = mock_page.evaluate(
            """async () => {
                const manager = window.live2dManager;
                // Hook-style call: non-forced, driven by the staleness window.
                manager._touchSetLoadedAt = 0;
                window.__allowCharFetchFailures = 1;
                const first = await window.nekoRefreshLive2DTouchSet({
                    allowLive2DReload: true
                });
                const charFetchFailed = window.__allowCharFetchFailures === 0;
                // The retry is scheduled by now; restore the fetch so it succeeds.
                window.fetch = window.__realFetch;
                // > TOUCH_SET_RETRY_DELAY_MS (1.5s) plus margin.
                await new Promise(r => setTimeout(r, 2600));
                const keys = Object.keys(manager.touchSet[manager.modelName] || {});
                return { first, charFetchFailed, keys };
            }"""
        )
        assert outcome["charFetchFailed"] is True, (
            f"the forced character-fetch failure did not trigger: {outcome}"
        )
        assert outcome["first"] is True, f"refresh should report handled: {outcome}"
        assert area_id in outcome["keys"], (
            f"the scheduled retry should have committed the saved area: {outcome['keys']}"
        )
    finally:
        mock_page.evaluate(
            """() => {
                if (window.__realFetch) window.fetch = window.__realFetch;
            }"""
        )


@pytest.mark.frontend_contract
def test_character_switch_load_sites_wire_touch_refresh_hook():
    """Codex round-9: the character-switch Live2D loader must wire the refresh hook.

    # The catgirl-switch path loads models directly (primary + fallback loadModel
    # sites) without going through handleModelReload or live2d-init, so without
    # the hook the switched-to character keeps the previous character's in-memory
    # touch set and unwired native HitAreas.
    """
    app_character = Path(__file__).resolve().parents[2] / "static" / "app" / "app-character.js"
    source = app_character.read_text(encoding="utf-8")
    load_sites = [m.start() for m in re.finditer(
        r"await window\.live2dManager\.loadModel\(", source
    )]
    assert len(load_sites) == 2, (
        f"expected two loadModel sites in app-character.js, found {len(load_sites)}; "
        "update this contract if a new load site is added"
    )
    for start in load_sites:
        window_after = source[start:start + 1500]
        assert "nekoRefreshLive2DTouchSet" in window_after, (
            "a character-switch loadModel site is missing the touch-set "
            "refresh hook after completion"
        )
        assert "lanlanName: newCatgirl" in window_after, (
            "the hook must pass the new character's name (the URL keeps the "
            "old lanlan_name after a switch, so the refresh would fetch the "
            "previous character's touch set)"
        )


@pytest.mark.frontend
def test_initialization_touch_set_honors_explicit_lanlan_name(
    mock_page: Page, running_server: str
):
    """Greptile round-10: an explicit lanlanName override must take precedence.

    # After a catgirl switch the URL keeps the OLD lanlan_name; the switch hook
    # passes the new name explicitly. Calling InitializationTouchSet with a
    # nonexistent override must resolve against that name and return false
    # gracefully (character missing), not fall back to the URL/page_config
    # character and commit their data.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    outcome = mock_page.evaluate(
        """async () => {
            const manager = window.live2dManager;
            const keysBefore = JSON.stringify(
                Object.keys(manager.touchSet[manager.modelName] || {})
            );
            const result = await InitializationTouchSet(undefined, {
                lanlanName: '__no_such_character__',
                allowLive2DReload: true,
            });
            const keysAfter = JSON.stringify(
                Object.keys(manager.touchSet[manager.modelName] || {})
            );
            return { result, keysBefore, keysAfter };
        }"""
    )
    assert outcome["result"] is False, (
        f"nonexistent explicit character should resolve to no-commit: {outcome}"
    )
    assert outcome["keysBefore"] == outcome["keysAfter"], (
        f"an unresolvable override must not touch the in-memory snapshot: {outcome}"
    )


@pytest.mark.frontend
def test_touch_set_resolution_prefers_current_character_over_url(
    mock_page: Page, running_server: str
):
    """Coderabbit round-10: a stale URL lanlan_name must not win over the live one.

    # After an in-place catgirl switch, window.lanlan_config.lanlan_name holds
    # the NEW character while the URL keeps the old one. A later hot-switch
    # refresh (no explicit name) must resolve the CURRENT character; otherwise
    # it commits the previous character's touch set onto the current model.
    """
    mock_page.goto(f"{running_server}/?lanlan_name=YUI", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    outcome = mock_page.evaluate(
        """async () => {
            const manager = window.live2dManager;
            window.lanlan_config.lanlan_name = '__switched_away__';
            const keysBefore = JSON.stringify(
                Object.keys(manager.touchSet[manager.modelName] || {})
            );
            const result = await InitializationTouchSet(undefined, {
                allowLive2DReload: true,
            });
            const keysAfter = JSON.stringify(
                Object.keys(manager.touchSet[manager.modelName] || {})
            );
            return { result, keysBefore, keysAfter };
        }"""
    )
    try:
        assert outcome["result"] is False, (
            f"resolution should prefer lanlan_config over the stale URL and "
            f"fail on the unknown character: {outcome}"
        )
        assert outcome["keysBefore"] == outcome["keysAfter"], (
            f"an unresolvable current character must not commit: {outcome}"
        )
    finally:
        mock_page.evaluate(
            "() => { window.lanlan_config.lanlan_name = 'YUI'; }"
        )


@pytest.mark.frontend
def test_stale_character_retry_is_cancelled(
    mock_page: Page, running_server: str
):
    """Codex round-11: a retry must be cancelled when its character is no longer active.

    # Scenario: character B's switch refresh fails and schedules a 1.5s retry;
    # the user switches to C before it fires. Without the cancellation the retry
    # would run with a newer generation and commit B's touch set onto C's model.
    """
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _wait_main_page_ready(mock_page)
    _wait_boot_settled(mock_page)

    area_id = "custom_repro_cancel_area_1"
    patch_result = _patch_touch_set_as_model_manager_does(mock_page, area_id)
    assert patch_result.get("success"), f"PATCH save failed: {patch_result}"

    # Count /api/characters GETs issued from inside InitializationTouchSet only
    # (same stack filter as the race test), so unrelated page traffic is ignored.
    # Fail exactly one such GET with an empty character table so the refresh
    # commits nothing and schedules the retry (page_config is no longer fetched
    # once lanlan_config is set, so it cannot be the injection point).
    mock_page.evaluate(
        """() => {
            const realFetch = window.fetch;
            window.__realFetch = realFetch;
            window.__initCharFetches = 0;
            window.__failNextCharFetch = false;
            window.fetch = async (url, opts) => {
                let parsed;
                try {
                    parsed = new URL(String(url), window.location.href);
                } catch (_) {
                    return realFetch(url, opts);
                }
                if (parsed.pathname === '/api/characters'
                    && String((opts && opts.method) || 'GET').toUpperCase() === 'GET') {
                    const stack = new Error().stack || '';
                    if (stack.includes('InitializationTouchSet')) {
                        window.__initCharFetches += 1;
                        if (window.__failNextCharFetch === true) {
                            window.__failNextCharFetch = false;
                            return new Response(JSON.stringify({ '猫娘': {} }), {
                                status: 200,
                                headers: { 'Content-Type': 'application/json' }
                            });
                        }
                    }
                }
                return realFetch(url, opts);
            };
        }"""
    )

    try:
        outcome = mock_page.evaluate(
            """async () => {
                const manager = window.live2dManager;
                // Character "B" is current when the refresh fails and the
                // retry is scheduled.
                window.lanlan_config.lanlan_name = '__charB__';
                window.__failNextCharFetch = true;
                const failedRefetch = await window.nekoRefreshLive2DTouchSet({
                    forceRefetch: true,
                    lanlanName: '__charB__',
                });
                const fetchesAfterFailure = window.__initCharFetches;
                // User switches to C before the 1.5s retry fires.
                window.lanlan_config.lanlan_name = '__charC__';
                // > TOUCH_SET_RETRY_DELAY_MS (1.5s).
                await new Promise(r => setTimeout(r, 2200));
                return {
                    failedRefetch,
                    fetchesAfterFailure,
                    fetchesAfterRetryWindow: window.__initCharFetches,
                    pendingFlag: manager._touchSetPendingForceRefetch === true,
                };
            }"""
        )
        assert outcome["failedRefetch"] is True, (
            f"refresh should report handled: {outcome}"
        )
        assert outcome["fetchesAfterFailure"] == 1, (
            f"expected exactly one counted /api/characters GET from the failed "
            f"refresh (the empty-character injection must precede it): {outcome}"
        )
        assert outcome["fetchesAfterRetryWindow"] == 1, (
            f"the stale-character retry must be cancelled and issue no further "
            f"/api/characters GET: {outcome}"
        )
    finally:
        mock_page.evaluate(
            """() => {
                if (window.__realFetch) window.fetch = window.__realFetch;
                window.lanlan_config.lanlan_name = 'YUI';
            }"""
        )
