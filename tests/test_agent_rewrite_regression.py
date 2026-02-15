from utils.config_manager import get_config_manager


def _route_paths(app_or_router):
    return {route.path for route in app_or_router.routes}


def test_core_config_uses_agent_model_only():
    cfg = get_config_manager().get_core_config()
    assert "AGENT_MODEL" in cfg
    assert "AGENT_MODEL_URL" in cfg
    assert "AGENT_MODEL_API_KEY" in cfg

    legacy_keys = [k for k in cfg.keys() if k.startswith("COMPUTER_USE_")]
    assert legacy_keys == []


def test_agent_server_legacy_endpoints_removed():
    from agent_server import app as agent_app

    paths = _route_paths(agent_app)
    assert "/process" not in paths
    assert "/plan" not in paths
    assert "/analyze_and_plan" not in paths


def test_main_agent_router_legacy_endpoints_removed():
    from main_routers.agent_router import router as agent_router

    paths = _route_paths(agent_router)
    assert "/api/agent/task_status" not in paths
    assert "/api/agent/notify_task_result" not in paths
