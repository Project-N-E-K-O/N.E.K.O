"""Runtime resolution and prompt-safe rendering for the ship catalog."""

from __future__ import annotations

from dataclasses import replace

import pytest

from plugin.plugins.neko_wows.ship_data.models import (
    CatalogMeta,
    CatalogShip,
    ShipCounts,
    ShipProfile,
)
from plugin.plugins.neko_wows.ship_data.context import BattleShipContextManager
from plugin.plugins.neko_wows.ship_data.renderer import ShipReferenceRenderer
from plugin.plugins.neko_wows.ship_data.resolver import ShipResolver
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.domain.snapshot import (
    RELATION_ALLY,
    RELATION_ENEMY,
    RELATION_SELF,
    STATUS_LIVE,
    SelfShip,
    Ship,
    WowsSnapshot,
)


@pytest.fixture
def catalog_meta() -> CatalogMeta:
    return CatalogMeta(
        schema_version=1,
        catalog_version="15.6.0.0.12830008:c4f6ae75:v1",
        game_version="15.6.0.0.12830008",
        channel="live",
        source_repo="https://github.com/wowsinfo/data",
        source_commit="c4f6ae751548c8e9a4887f69555a847d1cc5a300",
        content_sha256="c" * 64,
        default_language="zh-CN",
        ship_count=3,
        profile_count=3,
    )


@pytest.fixture
def yamato() -> CatalogShip:
    return CatalogShip(
        ship_id=4276041424,
        ship_index="PJSB018",
        name_key="IDS_PJSB018",
        display_name="大和",
        nation="Japan",
        ship_class="Battleship",
        tier=10,
        is_special=True,
        availability_group="special",
    )


@pytest.fixture
def yamato_profile(yamato: CatalogShip) -> ShipProfile:
    return ShipProfile(
        profile_id=f"{yamato.ship_id}:reference_top:primary",
        ship_id=yamato.ship_id,
        configuration="reference_top",
        variant_key="primary",
        is_primary=True,
        profile_schema_version=1,
        data={
            "survivability": {
                "hit_points": 97_200,
                "torpedo_protection_ratio": 0.55,
                "ignored_none": None,
            },
            "mobility": {
                "max_speed_knots": 27.0,
                "turning_radius_m": 900,
                "rudder_shift_s": 22.1,
            },
            "concealment": {
                "surface_detect_m": 17_500,
                "air_detect_m": 10_000,
            },
            "main_battery": {
                "range_m": 26_630,
                "reload_s": 30.0,
                "rotation_180_s": 60.0,
                "sigma": 2.1,
                "mounts": ({"mount_count": 3, "barrels_per_mount": 3},),
                "projectiles": (
                    {
                        "ammo_type": "HE",
                        "max_damage": 7_300,
                        "fire_chance_ratio": 0.35,
                        "caliber_mm": 460,
                    },
                    {
                        "ammo_type": "AP",
                        "max_damage": 14_800,
                        "initial_velocity_mps": 780,
                    },
                ),
                "raw_unknown_key": "must not escape",
            },
            "anti_air": {
                "auras": ({
                    "band": "far",
                    "max_range_m": 5_800,
                    "continuous_dps": 147,
                },),
            },
            "aircraft": ({
                "role": "fighter",
                "display_name": "大和战斗机",
                "hit_points": 2_060,
                "cruise_speed_knots": 128,
                "max_speed_knots": 160,
                "squadron_size": 9,
                "attack_group_size": 3,
                "restoration_s": 84.0,
                "weapon": {"ammo_type": "HE", "max_damage": 7_300},
            },),
            "raw_unknown_section": {"application_id": "secret"},
        },
        profile_sha256="d" * 64,
    )


class FakeCatalogSnapshot:
    def __init__(
        self,
        meta: CatalogMeta,
        aliases: dict[str, tuple[CatalogShip, ...]],
        profiles: dict[int, ShipProfile],
    ) -> None:
        self.meta = meta
        self._aliases = aliases
        self._profiles = profiles
        self.closed = False

    def alias_candidates(self, alias_norm: str) -> tuple[CatalogShip, ...]:
        return self._aliases.get(alias_norm, ())

    def ship(self, ship_id: int) -> CatalogShip | None:
        for ships in self._aliases.values():
            for ship in ships:
                if ship.ship_id == ship_id:
                    return ship
        return None

    def primary_profile(self, ship_id: int) -> ShipProfile | None:
        return self._profiles.get(ship_id)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def snapshot(
    catalog_meta: CatalogMeta,
    yamato: CatalogShip,
    yamato_profile: ShipProfile,
) -> FakeCatalogSnapshot:
    destroyer = replace(
        yamato,
        ship_id=111,
        ship_index="PSSD111",
        name_key="IDS_PSSD111",
        display_name="同名驱逐舰",
        ship_class="Destroyer",
        tier=8,
    )
    cruiser = replace(
        yamato,
        ship_id=222,
        ship_index="PSSC222",
        name_key="IDS_PSSC222",
        display_name="同名巡洋舰",
        ship_class="Cruiser",
        tier=8,
    )
    profiles = {
        yamato.ship_id: yamato_profile,
        destroyer.ship_id: replace(
            yamato_profile,
            profile_id="111:reference_top:primary",
            ship_id=111,
        ),
        cruiser.ship_id: replace(
            yamato_profile,
            profile_id="222:reference_top:primary",
            ship_id=222,
        ),
    }
    return FakeCatalogSnapshot(
        catalog_meta,
        aliases={
            "yamato": (yamato,),
            "大 和": (yamato,),
            "duplicate": (destroyer, cruiser),
        },
        profiles=profiles,
    )


def test_resolver_uses_nfkc_casefold_and_collapsed_space(snapshot):
    result = ShipResolver(snapshot).resolve(
        "  ＹＡＭＡＴＯ  ", tier=10, ship_type="Battleship")

    assert result.reason == "resolved"
    assert result.ship is not None
    assert result.ship.ship_id == 4276041424
    assert result.profile is not None


def test_resolver_collapses_internal_whitespace(snapshot):
    result = ShipResolver(snapshot).resolve("  大\t\n和  ")

    assert result.reason == "resolved"
    assert result.ship is not None
    assert result.ship.ship_id == 4276041424


def test_resolver_never_fuzzy_guesses(snapshot):
    result = ShipResolver(snapshot).resolve(
        "Yamto", tier=10, ship_type="Battleship")

    assert result.reason == "alias_not_found"
    assert result.ship is None
    assert result.profile is None


def test_resolver_returns_ambiguous_when_tier_and_class_do_not_decide(snapshot):
    result = ShipResolver(snapshot).resolve("duplicate", tier=8)

    assert result.reason == "ambiguous_alias"
    assert result.ship is None


def test_resolver_disambiguates_only_with_explicit_class_mapping(snapshot):
    result = ShipResolver(snapshot).resolve(
        "duplicate", tier=8, ship_type="驱逐舰")

    assert result.reason == "resolved"
    assert result.ship is not None
    assert result.ship.ship_class == "Destroyer"


def test_unknown_class_does_not_guess_between_candidates(snapshot):
    result = ShipResolver(snapshot).resolve(
        "duplicate", tier=8, ship_type="fast stealth boat")

    assert result.reason == "ambiguous_alias"


def test_resolver_reports_missing_primary_profile(
    catalog_meta: CatalogMeta,
    yamato: CatalogShip,
):
    snapshot = FakeCatalogSnapshot(
        catalog_meta, aliases={"yamato": (yamato,)}, profiles={})

    result = ShipResolver(snapshot).resolve("Yamato")

    assert result.reason == "profile_not_found"
    assert result.ship == yamato
    assert result.profile is None


def test_renderer_has_stable_boundaries_required_metadata_and_units(snapshot):
    resolution = ShipResolver(snapshot).resolve("Yamato")

    rendered = ShipReferenceRenderer().render(
        resolution,
        ShipCounts(self_count=1, ally_count=1, enemy_count=2),
        version_status="match",
    )

    assert rendered.startswith("<<<WOWS_SHIP_REFERENCE>>>\n")
    assert rendered.endswith("\n<<<END_WOWS_SHIP_REFERENCE>>>")
    assert "catalog_version=15.6.0.0.12830008:c4f6ae75:v1" in rendered
    assert "version_status=match" in rendered
    assert "configuration=reference_top" in rendered
    assert "舰船：大和 | X级 | 战列舰 | 自身1 友军1 敌军2" in rendered
    assert "HP 97200" in rendered
    assert "射程 26630 m" in rendered
    assert "装填 30.0 s" in rendered
    assert "sigma 2.1" in rendered
    assert "航空兵1：fighter；大和战斗机" in rendered
    assert "单机HP 2060" in rendered
    assert "编队 9" in rendered


def test_renderer_uses_a_whitelist_and_omits_none_and_internal_fields(snapshot):
    resolution = ShipResolver(snapshot).resolve("Yamato")

    rendered = ShipReferenceRenderer().render(
        resolution, ShipCounts(), version_status="unknown")

    assert "raw_unknown_key" not in rendered
    assert "raw_unknown_section" not in rendered
    assert "ignored_none" not in rendered
    assert "application_id" not in rendered
    assert "secret" not in rendered
    assert "profile_sha256" not in rendered
    assert "source_commit" not in rendered


def test_renderer_rejects_unresolved_ship(snapshot):
    resolution = ShipResolver(snapshot).resolve("Yamto")

    with pytest.raises(ValueError, match="resolved"):
        ShipReferenceRenderer().render(
            resolution, ShipCounts(), version_status="match")


def test_renderer_rejects_unknown_version_status(snapshot):
    resolution = ShipResolver(snapshot).resolve("Yamato")

    with pytest.raises(ValueError, match="version_status"):
        ShipReferenceRenderer().render(
            resolution, ShipCounts(), version_status="maybe")


def test_count_update_is_small_and_bounded(snapshot):
    resolution = ShipResolver(snapshot).resolve("Yamato")

    rendered = ShipReferenceRenderer().render_count_update(
        resolution,
        ShipCounts(self_count=1, ally_count=0, enemy_count=2),
        version_status="match",
    )

    assert rendered.startswith("<<<WOWS_SHIP_COUNT_UPDATE>>>\n")
    assert "舰船：大和 | 自身1 友军0 敌军2" in rendered
    assert "HP 97200" not in rendered
    assert rendered.endswith("\n<<<END_WOWS_SHIP_COUNT_UPDATE>>>")


class FakePlugin:
    def __init__(self, receipt=None) -> None:
        self.receipt = {"submitted": True} if receipt is None else receipt
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def push_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.receipt


class MutableStore:
    def __init__(self, current: FakeCatalogSnapshot) -> None:
        self.current = current
        self.calls = 0
        self.languages: list[str | None] = []

    def snapshot(self, *, language: str | None = None):
        self.calls += 1
        self.languages.append(language)
        return self.current

    def active_manifest_info(self):
        meta = self.current.meta
        return {
            "catalog_version": meta.catalog_version if meta else "",
            "game_version": meta.game_version if meta else "",
            "schema_version": meta.schema_version if meta else None,
        }


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def battle_snapshot(
    *ships: Ship,
    identity: tuple[str, str] = ("instance-1", "battle-1"),
    game_version: str = "15.6.0.0.12830008",
    own_player_id: int = 1,
) -> WowsSnapshot:
    return WowsSnapshot(
        instance_id=identity[0],
        battle_id=identity[1],
        game_version=game_version,
        status=STATUS_LIVE,
        active=True,
        self_ship=SelfShip(player_id=own_player_id, team_id=1),
        ships=tuple(ships),
    )


def yamato_ship(
    ui_id: int,
    player_id: int,
    relation: int,
    *,
    name: str = "Yamato",
) -> Ship:
    return Ship(
        ui_id=ui_id,
        player_id=player_id,
        team_id=1 if relation != RELATION_ENEMY else 2,
        relation=relation,
        ship_type="Battleship",
        name=name,
        tier=10,
        alive=True,
        visible=True,
    )


@pytest.fixture
def context_parts(snapshot):
    plugin = FakePlugin()
    store = MutableStore(snapshot)
    clock = FakeClock()
    cfg = WowsConfig(ship_catalog_enabled=True, dry_run=False)
    context = BattleShipContextManager(
        plugin, store, cfg, clock=clock)
    return context, plugin, store, clock


def test_context_submits_each_resolved_type_once_with_team_counts(context_parts):
    context, plugin, _, _ = context_parts
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ALLY),
        yamato_ship(3, 3, RELATION_ENEMY),
        yamato_ship(4, 4, RELATION_ENEMY),
    )

    result = context.observe(frame, dry_run=False)

    assert result.submitted_ship_ids == (4276041424,)
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["ai_behavior"] == "read"
    assert plugin.calls[0]["visibility"] == []
    assert plugin.calls[0]["priority"] == 0
    assert "自身1 友军1 敌军2" in plugin.calls[0]["parts"][0]["text"]
    assert plugin.calls[0]["metadata"]["ship_ids"] == [4276041424]

    again = context.observe(frame, dry_run=False)
    assert again.submitted_ship_ids == ()
    assert len(plugin.calls) == 1


def test_context_retries_unresolved_ui_object_when_identity_details_arrive(
    context_parts,
):
    context, plugin, _, _ = context_parts
    incomplete = replace(
        yamato_ship(50, 50, RELATION_ENEMY, name="duplicate"),
        ship_type=None,
        tier=None,
    )

    first = context.observe(battle_snapshot(incomplete), dry_run=False)
    retried = context.observe(battle_snapshot(incomplete), dry_run=False)

    assert first.submitted_ship_ids == ()
    assert retried.unresolved_reasons == {"ambiguous_alias": 1}
    assert context.stats()["observed_objects"] == 1
    assert context.stats()["unresolved_objects"] == 1
    assert plugin.calls == []

    complete = replace(incomplete, ship_type="Destroyer", tier=8)
    resolved = context.observe(battle_snapshot(complete), dry_run=False)
    observed_again = context.observe(battle_snapshot(complete), dry_run=False)

    assert resolved.submitted_ship_ids == (111,)
    assert resolved.unresolved_reasons == {}
    assert observed_again.submitted_ship_ids == ()
    assert observed_again.updated_ship_ids == ()
    assert context.stats()["unresolved_objects"] == 0
    assert len(plugin.calls) == 1


def test_context_retries_unresolved_fallback_object_when_details_arrive(
    context_parts,
):
    context, plugin, _, _ = context_parts
    incomplete = replace(
        yamato_ship(50, 50, RELATION_ENEMY, name="duplicate"),
        ui_id=None,
        player_id=None,
        ship_type=None,
        tier=None,
    )

    first = context.observe(battle_snapshot(incomplete), dry_run=False)
    complete = replace(incomplete, ship_type="Destroyer", tier=8)
    resolved = context.observe(battle_snapshot(complete), dry_run=False)
    observed_again = context.observe(battle_snapshot(complete), dry_run=False)

    assert first.unresolved_reasons == {"ambiguous_alias": 1}
    assert resolved.submitted_ship_ids == (111,)
    assert resolved.unresolved_reasons == {}
    assert observed_again.submitted_ship_ids == ()
    assert observed_again.updated_ship_ids == ()
    assert context.stats()["observed_objects"] == 1
    assert context.stats()["unresolved_objects"] == 0
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["parts"][0]["text"].count(
        "<<<WOWS_SHIP_REFERENCE>>>") == 1


def test_resolved_fallback_object_is_not_recounted_when_details_arrive(
    context_parts,
):
    context, plugin, _, _ = context_parts
    incomplete = replace(
        yamato_ship(60, 60, RELATION_ENEMY),
        ui_id=None,
        player_id=None,
        ship_type=None,
        tier=None,
    )

    first = context.observe(battle_snapshot(incomplete), dry_run=False)
    complete = replace(incomplete, ship_type="Battleship", tier=10)
    observed_again = context.observe(battle_snapshot(complete), dry_run=False)

    assert first.submitted_ship_ids == (4276041424,)
    assert observed_again.submitted_ship_ids == ()
    assert observed_again.updated_ship_ids == ()
    assert context.stats()["observed_objects"] == 1
    assert len(plugin.calls) == 1


def test_declined_batch_remains_retryable(context_parts):
    context, plugin, _, clock = context_parts
    frame = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))
    plugin.receipt = {"submitted": False, "reason": "queue_full"}

    declined = context.observe(frame, dry_run=False)
    assert declined.submitted_ship_ids == ()
    assert declined.pending_ship_ids == (4276041424,)

    clock.advance(1.0)
    plugin.receipt = {"submitted": True}
    accepted = context.observe(frame, dry_run=False)

    assert accepted.submitted_ship_ids == (4276041424,)
    assert len(plugin.calls) == 2
    assert plugin.calls[0]["coalesce_key"] != plugin.calls[1]["coalesce_key"]


def test_dry_run_previews_without_host_call_or_submission(context_parts):
    context, plugin, _, _ = context_parts
    frame = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))

    preview = context.observe(frame, dry_run=True)

    assert preview.submitted_ship_ids == ()
    assert preview.pending_ship_ids == (4276041424,)
    assert preview.preview_batches
    assert plugin.calls == []

    live = context.observe(frame, dry_run=False)
    assert live.submitted_ship_ids == (4276041424,)
    assert len(plugin.calls) == 1


def test_render_failure_is_counted_once_and_cleared_after_recovery(snapshot):
    class RecoverableRenderer:
        def __init__(self) -> None:
            self.failing = True
            self.delegate = ShipReferenceRenderer()

        def render(self, *args, **kwargs):
            if self.failing:
                raise RuntimeError("render unavailable")
            return self.delegate.render(*args, **kwargs)

    plugin = FakePlugin()
    renderer = RecoverableRenderer()
    context = BattleShipContextManager(
        plugin,
        MutableStore(snapshot),
        WowsConfig(ship_catalog_enabled=True, dry_run=False),
        renderer=renderer,
    )
    frame = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))

    first = context.observe(frame, dry_run=True)
    second = context.observe(frame, dry_run=True)

    assert first.unresolved_reasons == {"render_failed": 1}
    assert second.unresolved_reasons == {"render_failed": 1}
    assert context.stats()["observed_objects"] == 1
    assert context.stats()["unresolved_objects"] == 1

    renderer.failing = False
    recovered = context.observe(frame, dry_run=True)

    assert recovered.unresolved_reasons == {}
    assert recovered.preview_batches

    submitted = context.observe(frame, dry_run=False)
    assert submitted.submitted_ship_ids == (4276041424,)
    assert len(plugin.calls) == 1


def test_later_duplicate_ship_sends_only_count_update(context_parts):
    context, plugin, _, _ = context_parts
    first = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ENEMY),
    )
    second = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ENEMY),
        yamato_ship(3, 3, RELATION_ENEMY),
    )
    context.observe(first, dry_run=False)

    result = context.observe(second, dry_run=False)

    assert result.updated_ship_ids == (4276041424,)
    assert len(plugin.calls) == 2
    update = plugin.calls[1]["parts"][0]["text"]
    assert "<<<WOWS_SHIP_COUNT_UPDATE>>>" in update
    assert "自身1 友军0 敌军2" in update
    assert "HP 97200" not in update


def test_catalog_version_is_frozen_until_identity_changes(
    context_parts,
    catalog_meta,
    snapshot,
):
    context, _, store, _ = context_parts
    frame_one = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))
    context.observe(frame_one, dry_run=True)
    assert context.stats()["frozen_catalog_version"].endswith(":v1")

    v2_meta = replace(
        catalog_meta,
        catalog_version="15.6.0.0.12830008:c4f6ae75:v2",
    )
    v2_snapshot = FakeCatalogSnapshot(
        v2_meta, snapshot._aliases, snapshot._profiles)
    store.current = v2_snapshot

    context.observe(frame_one, dry_run=True)
    assert context.stats()["frozen_catalog_version"].endswith(":v1")
    assert context.stats()["active_catalog_version"].endswith(":v2")
    assert store.calls == 1

    frame_two = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        identity=("instance-1", "battle-2"),
    )
    context.observe(frame_two, dry_run=True)

    assert snapshot.closed is True
    assert context.stats()["frozen_catalog_version"].endswith(":v2")
    assert store.calls == 2


def test_catalog_language_is_frozen_until_identity_changes(snapshot):
    plugin = FakePlugin()
    store = MutableStore(snapshot)
    cfg = WowsConfig(
        ship_catalog_enabled=True,
        dry_run=False,
        ship_catalog_language="en",
    )
    context = BattleShipContextManager(plugin, store, cfg)
    first_battle = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))

    context.observe(first_battle, dry_run=True)
    context.apply_config(replace(cfg, ship_catalog_language="ja"))
    context.observe(first_battle, dry_run=True)

    assert store.languages == ["en"]

    next_battle = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        identity=("instance-1", "battle-2"),
    )
    context.observe(next_battle, dry_run=True)

    assert store.languages == ["en", "ja"]


def test_strict_version_mismatch_blocks_injection(snapshot):
    plugin = FakePlugin()
    cfg = WowsConfig(
        ship_catalog_enabled=True,
        ship_catalog_version_policy="strict",
    )
    context = BattleShipContextManager(plugin, MutableStore(snapshot), cfg)
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        game_version="15.7.0.0.99999999",
    )

    result = context.observe(frame, dry_run=False)

    assert result.state == "version_rejected"
    assert result.submitted_ship_ids == ()
    assert plugin.calls == []
    assert context.stats()["version_status"] == "mismatch"


def test_warn_version_mismatch_renders_status(snapshot):
    plugin = FakePlugin()
    cfg = WowsConfig(
        ship_catalog_enabled=True,
        ship_catalog_version_policy="warn",
    )
    context = BattleShipContextManager(plugin, MutableStore(snapshot), cfg)
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        game_version="15.7.0.0.99999999",
    )

    context.observe(frame, dry_run=False)

    assert "version_status=mismatch" in plugin.calls[0]["parts"][0]["text"]


def test_game_info_xml_is_read_only_fallback_for_version(snapshot, tmp_path):
    game_info = tmp_path / "game_info.xml"
    game_info.write_text(
        "<info><version>15.6.0.0.12830008</version></info>",
        encoding="utf-8",
    )
    before = game_info.read_bytes()
    plugin = FakePlugin()
    cfg = WowsConfig(
        ship_catalog_enabled=True,
        ship_catalog_version_policy="strict",
        game_dir=str(tmp_path),
    )
    context = BattleShipContextManager(plugin, MutableStore(snapshot), cfg)

    result = context.observe(
        battle_snapshot(
            yamato_ship(1, 1, RELATION_SELF), game_version=""),
        dry_run=False,
    )

    assert result.submitted_ship_ids == (4276041424,)
    assert context.stats()["client_game_version"] == "15.6.0.0.12830008"
    assert game_info.read_bytes() == before


def test_push_exception_is_isolated_and_retryable(context_parts):
    context, plugin, _, clock = context_parts
    frame = battle_snapshot(yamato_ship(1, 1, RELATION_SELF))
    plugin.error = RuntimeError("host unavailable")

    failed = context.observe(frame, dry_run=False)

    assert failed.error == "host_push_failed"
    assert failed.pending_ship_ids == (4276041424,)
    clock.advance(1.0)
    plugin.error = None
    accepted = context.observe(frame, dry_run=False)
    assert accepted.submitted_ship_ids == (4276041424,)


@pytest.mark.parametrize("failure_mode", ("declined", "exception"))
def test_context_stops_batch_fan_out_after_first_push_failure(
    failure_mode,
    catalog_meta,
    yamato,
    yamato_profile,
):
    montana = replace(
        yamato,
        ship_id=999,
        ship_index="PASB999",
        name_key="IDS_PASB999",
        display_name="Montana",
        nation="USA",
    )
    catalog = FakeCatalogSnapshot(
        catalog_meta,
        aliases={"yamato": (yamato,), "montana": (montana,)},
        profiles={
            yamato.ship_id: yamato_profile,
            montana.ship_id: replace(
                yamato_profile,
                profile_id="999:reference_top:primary",
                ship_id=999,
            ),
        },
    )
    plugin = FakePlugin()
    clock = FakeClock()
    context = BattleShipContextManager(
        plugin,
        MutableStore(catalog),
        WowsConfig(
            ship_catalog_enabled=True,
            dry_run=False,
            ship_catalog_context_batch_chars=1,
        ),
        clock=clock,
    )
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ENEMY, name="Montana"),
    )
    if failure_mode == "declined":
        plugin.receipt = {"submitted": False}
    else:
        plugin.error = RuntimeError("host unavailable")

    failed = context.observe(frame, dry_run=False)

    assert len(plugin.calls) == 1
    assert failed.pending_ship_ids == (999, 4276041424)

    clock.advance(1.0)
    plugin.receipt = {"submitted": True}
    plugin.error = None
    accepted = context.observe(frame, dry_run=False)

    assert accepted.submitted_ship_ids == (999, 4276041424)
    assert len(plugin.calls) == 3


@pytest.mark.parametrize("failure_mode", ("declined", "exception"))
def test_context_keeps_prior_batch_and_retries_only_unfinished_batches(
    failure_mode,
    catalog_meta,
    yamato,
    yamato_profile,
):
    class SequencedPlugin:
        def __init__(self, outcomes) -> None:
            self.outcomes = list(outcomes)
            self.calls: list[dict] = []

        def push_message(self, **kwargs):
            self.calls.append(kwargs)
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    shimakaze = replace(
        yamato,
        ship_id=888,
        ship_index="PJSD888",
        name_key="IDS_PJSD888",
        display_name="Shimakaze",
        ship_class="Destroyer",
    )
    des_moines = replace(
        yamato,
        ship_id=999,
        ship_index="PASC999",
        name_key="IDS_PASC999",
        display_name="Des Moines",
        nation="USA",
        ship_class="Cruiser",
    )
    catalog = FakeCatalogSnapshot(
        catalog_meta,
        aliases={
            "yamato": (yamato,),
            "shimakaze": (shimakaze,),
            "des moines": (des_moines,),
        },
        profiles={
            yamato.ship_id: yamato_profile,
            shimakaze.ship_id: replace(
                yamato_profile,
                profile_id="888:reference_top:primary",
                ship_id=888,
            ),
            des_moines.ship_id: replace(
                yamato_profile,
                profile_id="999:reference_top:primary",
                ship_id=999,
            ),
        },
    )
    second_outcome = (
        {"submitted": False}
        if failure_mode == "declined"
        else RuntimeError("host unavailable")
    )
    plugin = SequencedPlugin([
        {"submitted": True},
        second_outcome,
    ])
    clock = FakeClock()
    context = BattleShipContextManager(
        plugin,
        MutableStore(catalog),
        WowsConfig(
            ship_catalog_enabled=True,
            dry_run=False,
            ship_catalog_context_batch_chars=1,
        ),
        clock=clock,
    )
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ENEMY, name="Des Moines"),
        yamato_ship(3, 3, RELATION_ENEMY, name="Shimakaze"),
    )

    failed = context.observe(frame, dry_run=False)

    assert failed.submitted_ship_ids == (888,)
    assert failed.pending_ship_ids == (999, 4276041424)
    assert len(plugin.calls) == 2
    assert [call["metadata"]["ship_ids"] for call in plugin.calls] == [
        [888],
        [999],
    ]

    clock.advance(1.0)
    plugin.outcomes.extend((
        {"submitted": True},
        {"submitted": True},
    ))
    accepted = context.observe(frame, dry_run=False)

    assert accepted.submitted_ship_ids == (999, 4276041424)
    assert accepted.pending_ship_ids == ()
    assert [call["metadata"]["ship_ids"] for call in plugin.calls] == [
        [888],
        [999],
        [999],
        [4276041424],
    ]


def test_context_packs_complete_blocks_into_unique_soft_limited_batches(
    catalog_meta,
    yamato,
    yamato_profile,
):
    montana = replace(
        yamato,
        ship_id=999,
        ship_index="PASB999",
        name_key="IDS_PASB999",
        display_name="蒙大拿",
        nation="USA",
    )
    montana_profile = replace(
        yamato_profile,
        profile_id="999:reference_top:primary",
        ship_id=999,
    )
    catalog = FakeCatalogSnapshot(
        catalog_meta,
        aliases={"yamato": (yamato,), "montana": (montana,)},
        profiles={yamato.ship_id: yamato_profile, 999: montana_profile},
    )
    plugin = FakePlugin()
    cfg = WowsConfig(
        ship_catalog_enabled=True,
        dry_run=False,
        ship_catalog_context_batch_chars=1,
    )
    context = BattleShipContextManager(plugin, MutableStore(catalog), cfg)
    frame = battle_snapshot(
        yamato_ship(1, 1, RELATION_SELF),
        yamato_ship(2, 2, RELATION_ENEMY, name="Montana"),
    )

    result = context.observe(frame, dry_run=False)

    assert result.submitted_ship_ids == (999, 4276041424)
    assert len(plugin.calls) == 2
    assert all(
        call["parts"][0]["text"].count("<<<WOWS_SHIP_REFERENCE>>>") == 1
        for call in plugin.calls
    )
    assert len({call["coalesce_key"] for call in plugin.calls}) == 2
    assert [call["metadata"]["ship_ids"] for call in plugin.calls] == [
        [999],
        [4276041424],
    ]


def test_reset_releases_frozen_snapshot_and_state(context_parts, snapshot):
    context, _, _, _ = context_parts
    context.observe(
        battle_snapshot(yamato_ship(1, 1, RELATION_SELF)),
        dry_run=True,
    )

    context.reset("battle_end")

    assert snapshot.closed is True
    assert context.stats()["state"] == "idle"
    assert context.stats()["frozen_catalog_version"] == ""
