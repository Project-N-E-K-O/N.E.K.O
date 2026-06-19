from plugin.plugins.neko_roast.core.contracts import RoastConfig


def test_roast_config_defaults_to_dry_run_for_real_room_safety():
    assert RoastConfig().dry_run is True
    assert RoastConfig.from_mapping({}).dry_run is True
    assert RoastConfig.from_mapping(None).dry_run is True


def test_roast_config_preserves_explicit_dry_run_false_for_real_output_window():
    assert RoastConfig.from_mapping({"dry_run": False}).dry_run is False
