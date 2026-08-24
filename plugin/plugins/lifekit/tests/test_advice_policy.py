from plugin.plugins.lifekit._advice_policy import DEFAULT_ADVICE_POLICY


def test_route_selection_and_advice_share_one_distance_policy() -> None:
    policy = DEFAULT_ADVICE_POLICY

    assert policy.route_modes(1.5) == ("walking", "bicycling")
    assert policy.primary_advice_mode(1.5, has_rain=False) == "walking"
    assert policy.route_modes(4.0) == ("bicycling", "transit")
    assert policy.primary_advice_mode(4.0, has_rain=False) == "bicycling"
    assert policy.primary_advice_mode(4.0, has_rain=True) == "transit"
    assert policy.primary_advice_mode(100.0, has_rain=True) == ""


def test_uv_protection_threshold_is_shared_across_weather_advice() -> None:
    policy = DEFAULT_ADVICE_POLICY

    assert policy.needs_sun_protection(5.9) is False
    assert policy.needs_sun_protection(6.0) is True
