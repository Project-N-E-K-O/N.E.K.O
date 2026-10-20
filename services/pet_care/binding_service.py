"""Local account-to-role binding and stage-6 pet-care service.

The service is a local mirror of the future community PetCare binding
contract.  OAuth supplies the account; this service owns the single local pet
record and the server-generated stable ``bound_role_id``.  A local
``character_uid`` is only a resolver key and is never used as a pet or role
primary key.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import secrets
import sqlite3
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class BindingServiceError(RuntimeError):
    """A local binding operation could not be completed."""

    def __init__(self, code: str, *, status_code: int = 503):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class BindingContext:
    account_id: str
    current_character_uid: str
    binding_status: str
    pet_id: str | None
    bound_role_id: str | None
    created: bool = False
    snapshot: dict[str, Any] | None = None
    settlement: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    care_actions: dict[str, Any] | None = None
    play_actions: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "current_character_uid": self.current_character_uid,
            "binding_status": self.binding_status,
            "pet_id": self.pet_id,
            "bound_role_id": self.bound_role_id,
            "created": self.created,
            "service": "local",
            "snapshot": deepcopy(self.snapshot) if self.snapshot is not None else None,
            "settlement": deepcopy(self.settlement) if self.settlement is not None else None,
            "reconciliation": deepcopy(self.reconciliation) if self.reconciliation is not None else None,
            "care_actions": deepcopy(self.care_actions) if self.care_actions is not None else None,
            "play_actions": deepcopy(self.play_actions) if self.play_actions is not None else None,
        }


# This is a configuration boundary, not a second product rule source.  The
# values are deliberately replaceable by local/test configuration and are
# never used as a client-side authority.
DEFAULT_PET_CARE_SNAPSHOT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "initial_status_values": {
        "satiety": 75,
        "energy": 80,
        "cleanliness": 80,
        "mood": 64,
        "progress": 0,
    },
    "initial_negative_status": {
        "sick": {"active": False},
        "sulk": {"active": False},
    },
    "initial_inventory": {},
    "initial_tasks": {"daily": [], "state": []},
    "initial_unlocks": [],
    "initial_exploration": None,
    # Stage 8 mood contract.  This is an editable local candidate, not a
    # second product-rule source: the authoritative deployment can replace
    # the weights, initial interaction value and decay rate server-side.
    "mood_formula": {
        "initial_interaction_index": 40,
        # A deliberately slow local candidate: roughly one interaction point
        # every two days. Deployments may replace this without code changes.
        "interaction_decay_per_hour": 0.02,
        "weights": {
            "satiety": 0.25,
            "energy": 0.20,
            "cleanliness": 0.25,
            "interaction": 0.30,
        },
        "jitter_range": 0,
    },
    # One local exploration location.  This is deliberately configuration,
    # not a product constant: deployments may replace the complete block and
    # the server persists the chosen location id with each activity.
    "exploration": {
        "locations": [
            {
                "id": "window-path",
                "name": "窗边小路",
                # Desktop-only away presentation asset.  It is never a
                # location thumbnail or a panel/letter fallback.
                "asset_url": "/static/assets/neko-idle/cat-bad.png",
                # Independent postcard art.  This is deliberately separate
                # from the desktop nest/away asset above and remains an
                # editable content URL for later locations.
                "letter_image_url": "/static/assets/neko-idle/exploration-window-path.svg",
                "note": "我去窗边看看，很快回来。",
                "duration_seconds": 7200,
                "costs": {"satiety": 15, "energy": 20, "cleanliness": 10},
                "letter_count": {"min": 1, "max": 3},
                "letters": [
                    {"id": "window-path-1", "text": "我在窗边发现了一条小路。"},
                    {"id": "window-path-2", "text": "这里的风很轻，别担心我。"},
                    {"id": "window-path-3", "text": "我带着一点小礼物回家。"},
                ],
                "reward": {
                    "progress_delta": 20,
                    "full_bonus_progress": 4,
                    "mood_multiplier": {
                        "tiers": [
                            {"max": 33, "value": 0.75},
                            {"max": 66, "value": 1.0},
                            {"max": 100, "value": 1.5},
                        ],
                    },
                    "drops": [
                        {"item_id": "teaser_wand", "min": 1, "max": 1, "chance": 1.0}
                    ],
                },
            }
        ],
    },
    # Stage 6 daily counters are service-owned state, not a second progress
    # field. Limits and effects remain editable configuration.
    "playing": {
        "yarn": {
            "label": "毛线球",
            "allowed_sources": ["chat-yarn", "react-chat-window", "cat-yarn-observation-adapter"],
            "progress_delta": 4,
            "daily_progress_limit": 3,
            "energy_delta": -8,
            "cleanliness_delta": -2,
            "interaction_delta": 4,
            "feedback_action": "play_yarn",
        },
        "teaser_wand": {
            "label": "逗猫棒",
            "allowed_sources": ["pet-care-toy"],
            "inventory_item": "teaser_wand",
            "progress_delta": 8,
            "daily_limit": 3,
            "energy_delta": -10,
            "cleanliness_delta": -2,
            "interaction_delta": 6,
            "feedback_action": "play_ball",
        },
    },
    # Stage 5 values are deliberately editable.  ``local`` follows the
    # service process timezone and switches at local midnight; a deployment
    # may replace it with an IANA zone without changing the stored contract.
    "service_day": {"timezone": "local", "start_hour": 0},
    # ``task_catalog`` is the canonical collection of independent task
    # records.  ``daily_tasks`` remains a read-only compatibility alias for
    # older local JSON configurations; it is not a UI grouping.
    "task_catalog": [
        {
            "id": "daily-care-placeholder",
            "title": "领取小鱼干",
            "reward": {"item_id": "small_fish", "quantity": 1},
        },
        {
            "id": "daily-yarn-play",
            "title": "毛线球",
            "progress_source": {"category": "playing", "content_id": "yarn"},
            "claimable": False,
            "reset_policy": "daily",
            "reward": {},
        },
    ],
    "daily_login": {"progress_delta": 4},
    # Stage 8 is intentionally configuration-driven.  These are editable
    # local/test defaults, not locked product values.  The service records
    # the chosen configuration version and decision result per offline segment
    # so repeated reads never re-roll a settled segment.
    "offline_settlement": {
        "config_version": "stage8-local-candidate-1",
        "negative_threshold_hours": 48,
        "rollback_after_days": 5,
        "progress_rollback_per_day": 20,
        "protected_min_status": 80,
        "sick": {
            "base_probability": 0.05,
            "offline_weight": 0.20,
            "cleanliness_deficit_weight": 0.25,
            "satiety_deficit_weight": 0.12,
            "mood_deficit_weight": 0.08,
            "energy_deficit_weight": 0.05,
            "max_probability": 0.55,
            "task_id": "recovery-sick",
            "task_title": "求药",
            "recovery_item_id": "medicine",
            "recovery_item_quantity": 1,
        },
        "sulk": {
            "base_probability": 0.05,
            "offline_weight": 0.20,
            "mood_deficit_weight": 0.25,
            "energy_deficit_weight": 0.10,
            "cleanliness_deficit_weight": 0.08,
            "satiety_deficit_weight": 0.05,
            "max_probability": 0.50,
            "task_id": "recovery-sulk",
            "task_title": "哄哄她",
            "recovery_item_id": "teaser_wand",
            "recovery_item_quantity": 1,
        },
    },
    "unlock_catalog": [
        {"id": "cat-nest-mailbox", "threshold": 40, "name": "猫窝信箱", "type": "exploration"},
        {"id": "soft-brush-glove", "threshold": 120, "name": "柔软梳毛手套", "type": "care"},
        {"id": "window-path-extension", "threshold": 260, "name": "窗边小路·延伸段", "type": "exploration"},
        {"id": "travel-album-page", "threshold": 480, "name": "旅行相册页", "type": "collection"},
        {"id": "bell-teaser", "threshold": 800, "name": "铃铛逗猫棒", "type": "play"},
    ],
    # Stage 4 rules are configuration, not product constants.  Deployments
    # may replace this block through pet_care.json; tests may inject a clock
    # and a small deterministic inventory.
    "settlement": {
        "satiety_decay_per_hour": 1,
        "cleanliness_decay_per_hour": 1,
        "energy_recovery_per_hour": 4,
    },
    "care_actions": {
        "feeding": {
            "small_fish": {
                "label": "小鱼干",
                "inventory_item": "small_fish",
                "satiety_delta": 25,
                "progress_delta": 0,
                "interaction_delta": 2,
                "feedback_action": "eat_snack",
            },
        },
        "cleaning": {
            "brushing": {
                "label": "梳毛",
                "cleanliness_delta": 30,
                "progress_delta": 4,
                "interaction_delta": 4,
                "cooldown_seconds": 3600,
                "feedback_action": "stretch",
            },
        },
    },
}


class LocalPetCareBindingService:
    """Persist one account pet and one explicitly bound local role.

    The ``bound_character_uid`` column is deliberately a local resolver field.
    It is not returned as a role identity and is not used as ``pet_id`` or
    ``bound_role_id``.  The community implementation can replace this adapter
    with its role resolver while keeping the response contract unchanged.
    """

    # This is the SQLite *storage* contract version.  It is deliberately
    # separate from the configurable snapshot schema_version: the latter
    # describes the JSON payload, while this version records physical table
    # migrations.  Keeping the two independent lets a future server adapter
    # migrate columns without changing the client snapshot contract.
    _STORAGE_SCHEMA_VERSION = 1

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_accounts (
        account_id TEXT PRIMARY KEY,
        pet_id TEXT NOT NULL UNIQUE,
        bound_role_id TEXT NOT NULL UNIQUE,
        bound_character_uid TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    CREATE_SNAPSHOT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_snapshots (
        pet_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL UNIQUE,
        bound_role_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        last_settled_at TEXT NOT NULL,
        status_values_json TEXT NOT NULL,
        negative_status_json TEXT NOT NULL,
        inventory_json TEXT NOT NULL,
        tasks_json TEXT NOT NULL,
        unlocks_json TEXT NOT NULL,
        exploration_json TEXT,
        cleaning_cooldowns_json TEXT NOT NULL DEFAULT '{}',
        activity_counters_json TEXT NOT NULL DEFAULT '{}',
        interaction_state_json TEXT NOT NULL DEFAULT '{}',
        unlock_checkpoint INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    CREATE_OPERATION_RESULTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_operation_results (
        account_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, idempotency_key)
    )
    """

    CREATE_SESSION_STATE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_session_state (
        account_id TEXT PRIMARY KEY,
        last_session_id TEXT,
        last_valid_at TEXT,
        last_invalid_at TEXT,
        invalid_session_id TEXT,
        last_reconciled_segment_id TEXT,
        updated_at TEXT NOT NULL
    )
    """

    CREATE_OFFLINE_SETTLEMENTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_offline_settlements (
        segment_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        pet_id TEXT NOT NULL,
        bound_role_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        previous_session_id TEXT,
        invalid_at TEXT,
        valid_at TEXT NOT NULL,
        duration_seconds REAL NOT NULL,
        config_version TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(account_id, segment_id)
    )
    """

    CREATE_SCHEMA_META_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pet_care_schema_meta (
        schema_name TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        migrated_at TEXT NOT NULL
    )
    """

    def __init__(
        self,
        config_manager: Any,
        snapshot_config: Mapping[str, Any] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config_manager = config_manager
        configured = snapshot_config
        if configured is None:
            configured = getattr(config_manager, "pet_care_snapshot_config", None)
        if configured is None:
            # The production ConfigManager owns user-editable JSON config. Do
            # not make the service silently depend on a private attribute that
            # most adapters do not expose; the explicit constructor override
            # remains available to tests and future server adapters.
            loader = getattr(config_manager, "load_json_config", None)
            if callable(loader):
                try:
                    configured = loader("pet_care.json", default_value={})
                except TypeError:
                    configured = loader("pet_care.json")
                except Exception as exc:
                    raise BindingServiceError("pet_care_config_unavailable") from exc
        if configured is None:
            configured = {}
        elif not isinstance(configured, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        # Migrate the old configuration key before merging so a deployment
        # that still uses ``daily_tasks`` overrides the canonical catalogue
        # instead of being shadowed by the built-in defaults.
        if "task_catalog" not in configured and "daily_tasks" in configured:
            configured = dict(configured)
            configured["task_catalog"] = configured["daily_tasks"]
        self.snapshot_config = self._merge_config(DEFAULT_PET_CARE_SNAPSHOT_CONFIG, configured)
        self._validate_snapshot_config(self.snapshot_config)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = deepcopy(dict(base))
        for key, value in override.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = LocalPetCareBindingService._merge_config(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    @staticmethod
    def _validate_snapshot_config(config: Mapping[str, Any]) -> None:
        """Reject malformed editable config before it can create a snapshot."""
        schema_version = config.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            raise BindingServiceError("pet_care_config_invalid")

        values = config.get("initial_status_values")
        required_values = ("satiety", "energy", "cleanliness", "mood", "progress")
        if not isinstance(values, Mapping) or any(key not in values for key in required_values):
            raise BindingServiceError("pet_care_config_invalid")
        for key in required_values:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise BindingServiceError("pet_care_config_invalid")
            # The four condition values are bounded states. Progress is a
            # cumulative unlock counter and therefore has no 100-point cap.
            if value < 0 or (key != "progress" and value > 100):
                raise BindingServiceError("pet_care_config_invalid")

        if not isinstance(config.get("initial_negative_status"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_inventory"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        for item_id, item in config["initial_inventory"].items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise BindingServiceError("pet_care_config_invalid")
            quantity = item
            if isinstance(item, Mapping):
                if "quantity" in item:
                    quantity = item["quantity"]
                elif "count" in item:
                    quantity = item["count"]
                else:
                    continue
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, (int, float))
                or not math.isfinite(float(quantity))
                or float(quantity) < 0
                or not float(quantity).is_integer()
            ):
                raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_tasks"), Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        if not isinstance(config.get("initial_unlocks"), list):
            raise BindingServiceError("pet_care_config_invalid")
        mood_formula = config.get("mood_formula", {})
        if not isinstance(mood_formula, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        initial_interaction = mood_formula.get("initial_interaction_index", 40)
        decay_per_hour = mood_formula.get("interaction_decay_per_hour", 0)
        jitter_range = mood_formula.get("jitter_range", 0)
        if (
            isinstance(initial_interaction, bool)
            or not isinstance(initial_interaction, (int, float))
            or not math.isfinite(float(initial_interaction))
            or not 0 <= float(initial_interaction) <= 100
            or isinstance(decay_per_hour, bool)
            or not isinstance(decay_per_hour, (int, float))
            or not math.isfinite(float(decay_per_hour))
            or float(decay_per_hour) < 0
            or isinstance(jitter_range, bool)
            or not isinstance(jitter_range, int)
            or jitter_range < 0
        ):
            raise BindingServiceError("pet_care_config_invalid")
        mood_weights = mood_formula.get("weights", {})
        if not isinstance(mood_weights, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        for key in ("satiety", "energy", "cleanliness", "interaction"):
            weight = mood_weights.get(key, 0)
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(float(weight))
                or float(weight) < 0
            ):
                raise BindingServiceError("pet_care_config_invalid")
        service_day = config.get("service_day", {})
        if not isinstance(service_day, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        start_hour = service_day.get("start_hour", 0)
        if isinstance(start_hour, bool) or not isinstance(start_hour, int) or not 0 <= start_hour <= 23:
            raise BindingServiceError("pet_care_config_invalid")
        zone_name = service_day.get("timezone", "local")
        if not isinstance(zone_name, str) or not zone_name.strip():
            raise BindingServiceError("pet_care_config_invalid")
        if zone_name != "local":
            try:
                ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError):
                raise BindingServiceError("pet_care_config_invalid")
        task_catalog = config.get("task_catalog")
        if task_catalog is None:
            task_catalog = config.get("daily_tasks", [])
        if not isinstance(task_catalog, list):
            raise BindingServiceError("pet_care_config_invalid")
        task_ids: set[str] = set()
        for task in task_catalog:
            if not isinstance(task, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            task_id = task.get("task_id", task.get("id"))
            if not isinstance(task_id, str) or not task_id.strip() or task_id in task_ids:
                raise BindingServiceError("pet_care_config_invalid")
            task_ids.add(task_id)
            for key in ("group_id", "group_label", "reset_policy"):
                value = task.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise BindingServiceError("pet_care_config_invalid")
            for key in ("progress", "target"):
                value = task.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                          not math.isfinite(float(value)) or float(value) < 0):
                    raise BindingServiceError("pet_care_config_invalid")
            progress_source = task.get("progress_source")
            if progress_source is not None:
                if not isinstance(progress_source, Mapping):
                    raise BindingServiceError("pet_care_config_invalid")
                source_category = progress_source.get("category")
                source_content_id = progress_source.get("content_id")
                if (not isinstance(source_category, str) or not source_category.strip() or
                        not isinstance(source_content_id, str) or not source_content_id.strip()):
                    raise BindingServiceError("pet_care_config_invalid")
            claimable = task.get("claimable", True)
            if not isinstance(claimable, bool):
                raise BindingServiceError("pet_care_config_invalid")
            reward = task.get("reward", {})
            if not isinstance(reward, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            item_id = reward.get("item_id")
            quantity = reward.get("quantity", 0)
            if claimable and (not isinstance(item_id, str) or not item_id.strip() or isinstance(quantity, bool) or
                              not isinstance(quantity, int) or quantity <= 0):
                raise BindingServiceError("pet_care_config_invalid")
        daily_login = config.get("daily_login", {})
        if not isinstance(daily_login, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        login_delta = daily_login.get("progress_delta", 0)
        if isinstance(login_delta, bool) or not isinstance(login_delta, int) or login_delta < 0:
            raise BindingServiceError("pet_care_config_invalid")
        offline = config.get("offline_settlement", {})
        if not isinstance(offline, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        for key in ("negative_threshold_hours", "rollback_after_days", "progress_rollback_per_day"):
            value = offline.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                raise BindingServiceError("pet_care_config_invalid")
        protected_min_status = offline.get("protected_min_status", 0)
        if (
            isinstance(protected_min_status, bool)
            or not isinstance(protected_min_status, (int, float))
            or not math.isfinite(float(protected_min_status))
            or not 0 <= float(protected_min_status) <= 100
        ):
            raise BindingServiceError("pet_care_config_invalid")
        config_version = offline.get("config_version", "")
        if not isinstance(config_version, str) or not config_version.strip():
            raise BindingServiceError("pet_care_config_invalid")
        for status_key in ("sick", "sulk"):
            status_config = offline.get(status_key, {})
            if not isinstance(status_config, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            for key in (
                "base_probability", "offline_weight", "cleanliness_deficit_weight",
                "satiety_deficit_weight", "mood_deficit_weight", "energy_deficit_weight",
                "max_probability", "probability",
            ):
                value = status_config.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                    raise BindingServiceError("pet_care_config_invalid")
            for key in ("task_id", "task_title", "recovery_item_id"):
                value = status_config.get(key, "")
                if not isinstance(value, str) or not value.strip():
                    raise BindingServiceError("pet_care_config_invalid")
            quantity = status_config.get("recovery_item_quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise BindingServiceError("pet_care_config_invalid")
        unlock_catalog = config.get("unlock_catalog", [])
        if not isinstance(unlock_catalog, list):
            raise BindingServiceError("pet_care_config_invalid")
        unlock_ids: set[str] = set()
        for unlock in unlock_catalog:
            if not isinstance(unlock, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            unlock_id = unlock.get("id")
            threshold = unlock.get("threshold")
            if not isinstance(unlock_id, str) or not unlock_id.strip() or unlock_id in unlock_ids:
                raise BindingServiceError("pet_care_config_invalid")
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)) or threshold < 0:
                raise BindingServiceError("pet_care_config_invalid")
            unlock_ids.add(unlock_id)
        exploration = config.get("initial_exploration")
        if exploration is not None and not isinstance(exploration, Mapping):
            raise BindingServiceError("pet_care_config_invalid")

        exploration_config = config.get("exploration", {})
        if not isinstance(exploration_config, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        locations = exploration_config.get("locations", [])
        if not isinstance(locations, list) or not locations:
            raise BindingServiceError("pet_care_config_invalid")
        location_ids: set[str] = set()
        for location in locations:
            if not isinstance(location, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            location_id = location.get("id", location.get("location_id"))
            if not isinstance(location_id, str) or not location_id.strip() or location_id in location_ids:
                raise BindingServiceError("pet_care_config_invalid")
            location_ids.add(location_id)
            duration = location.get("duration_seconds", location.get("duration"))
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or float(duration) <= 0:
                raise BindingServiceError("pet_care_config_invalid")
            costs = location.get("costs", location.get("cost", {}))
            if not isinstance(costs, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            for key in ("satiety", "energy", "cleanliness"):
                value = costs.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                    raise BindingServiceError("pet_care_config_invalid")
            letter_count = location.get("letter_count", {})
            if letter_count is not None and not isinstance(letter_count, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            if isinstance(letter_count, Mapping):
                minimum = letter_count.get("min", letter_count.get("minimum", 1))
                maximum = letter_count.get("max", letter_count.get("maximum", 3))
                if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (minimum, maximum)) or minimum > maximum:
                    raise BindingServiceError("pet_care_config_invalid")
                if maximum > 3:
                    raise BindingServiceError("pet_care_config_invalid")
            letters = location.get("letters", [])
            if letters is not None and (not isinstance(letters, list) or any(not isinstance(item, Mapping) for item in letters)):
                raise BindingServiceError("pet_care_config_invalid")
            note = location.get("note", location.get("away_note"))
            if note is not None and not isinstance(note, str):
                raise BindingServiceError("pet_care_config_invalid")
            reward = location.get("reward", {})
            if not isinstance(reward, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            for key in ("progress_delta", "full_bonus_progress"):
                value = reward.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                    raise BindingServiceError("pet_care_config_invalid")
            mood_multiplier = reward.get("mood_multiplier", {})
            if isinstance(mood_multiplier, Mapping):
                tiers = mood_multiplier.get("tiers")
                if tiers is not None:
                    if not isinstance(tiers, list) or not tiers:
                        raise BindingServiceError("pet_care_config_invalid")
                    for tier in tiers:
                        if not isinstance(tier, Mapping):
                            raise BindingServiceError("pet_care_config_invalid")
                        maximum = tier.get("max")
                        multiplier = tier.get("value", tier.get("multiplier"))
                        if (isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(float(maximum))
                                or not 0 <= float(maximum) <= 100 or isinstance(multiplier, bool)
                                or not isinstance(multiplier, (int, float)) or not math.isfinite(float(multiplier)) or float(multiplier) < 0):
                            raise BindingServiceError("pet_care_config_invalid")
                for key in ("base", "slope"):
                    value = mood_multiplier.get(key, 0 if key == "slope" else 1)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                        raise BindingServiceError("pet_care_config_invalid")
            elif isinstance(mood_multiplier, bool) or not isinstance(mood_multiplier, (int, float)) or not math.isfinite(float(mood_multiplier)) or float(mood_multiplier) < 0:
                raise BindingServiceError("pet_care_config_invalid")
            drops = reward.get("drops", [])
            if not isinstance(drops, list):
                raise BindingServiceError("pet_care_config_invalid")
            for drop in drops:
                if not isinstance(drop, Mapping) or not isinstance(drop.get("item_id"), str) or not str(drop.get("item_id")).strip():
                    raise BindingServiceError("pet_care_config_invalid")
                for key in ("min", "max"):
                    value = drop.get(key, 1)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise BindingServiceError("pet_care_config_invalid")
                if int(drop.get("min", 1)) > int(drop.get("max", 1)):
                    raise BindingServiceError("pet_care_config_invalid")
                chance = drop.get("chance", 1.0)
                if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not math.isfinite(float(chance)) or not 0 <= float(chance) <= 1:
                    raise BindingServiceError("pet_care_config_invalid")

        settlement = config.get("settlement", {})
        if not isinstance(settlement, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        for key in ("satiety_decay_per_hour", "cleanliness_decay_per_hour", "energy_recovery_per_hour"):
            value = settlement.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise BindingServiceError("pet_care_config_invalid")

        actions = config.get("care_actions", {})
        if not isinstance(actions, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        # Concrete operations are editable service content.  Keep the two
        # first-release categories and their baseline entries available, but
        # validate every configured item so adding a future food or cleaning
        # operation does not require a client code change.
        for category, baseline_id in (("feeding", "small_fish"), ("cleaning", "brushing")):
            category_config = actions.get(category)
            if not isinstance(category_config, Mapping) or not category_config:
                raise BindingServiceError("pet_care_config_invalid")
            if not isinstance(category_config.get(baseline_id), Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            for content_id, operation in category_config.items():
                if not isinstance(content_id, str) or not content_id.strip() or not isinstance(operation, Mapping):
                    raise BindingServiceError("pet_care_config_invalid")
                for text_key in ("label", "display_name", "inventory_item", "feedback_action"):
                    value = operation.get(text_key)
                    if value is not None and (not isinstance(value, str) or not value.strip()):
                        raise BindingServiceError("pet_care_config_invalid")
                for key in (
                    "satiety_delta", "cleanliness_delta", "progress_delta",
                    "interaction_delta", "cooldown_seconds",
                ):
                    if key not in operation:
                        continue
                    value = operation[key]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                        raise BindingServiceError("pet_care_config_invalid")
                # The locked first food rule is categorical: small fish
                # restores satiety only and cannot silently become a progress
                # reward. Other configured foods remain service-defined.
                if category == "feeding" and content_id == "small_fish" and float(operation.get("progress_delta", 0)) != 0:
                    raise BindingServiceError("pet_care_config_invalid")

        playing = config.get("playing", {})
        if not isinstance(playing, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        for content_id, operation in playing.items():
            if not isinstance(content_id, str) or not content_id.strip() or not isinstance(operation, Mapping):
                raise BindingServiceError("pet_care_config_invalid")
            for key in ("progress_delta", "daily_progress_limit", "daily_limit"):
                if key not in operation:
                    continue
                value = operation[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value < 0
                    or (key in {"progress_delta", "daily_progress_limit", "daily_limit"} and not float(value).is_integer())
                ):
                    raise BindingServiceError("pet_care_config_invalid")
            for key in ("energy_delta", "cleanliness_delta", "interaction_delta"):
                if key not in operation:
                    continue
                value = operation[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise BindingServiceError("pet_care_config_invalid")
            inventory_item = operation.get("inventory_item")
            if inventory_item is not None and (not isinstance(inventory_item, str) or not inventory_item.strip()):
                raise BindingServiceError("pet_care_config_invalid")
            allowed_sources = operation.get("allowed_sources")
            if (
                not isinstance(allowed_sources, list)
                or not allowed_sources
                or any(not isinstance(source, str) or not source.strip() for source in allowed_sources)
            ):
                raise BindingServiceError("pet_care_config_invalid")
            if content_id == "yarn" and float(operation.get("progress_delta", 0)) < 0:
                raise BindingServiceError("pet_care_config_invalid")

    @property
    def database_path(self) -> Path:
        state_dir = Path(self.config_manager.local_state_dir)
        return state_dir / "pet_care" / "pet_care.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if not self.config_manager.ensure_local_state_directory():
                raise BindingServiceError("local_state_unavailable")
            path = self.database_path
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(path), timeout=5.0, isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            # Schema creation/migration is serialized before a caller starts
            # its business transaction.  This prevents two fresh workers
            # from both observing a missing legacy column and racing an
            # ALTER TABLE during process start.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(self._SCHEMA)
            connection.execute(self.CREATE_SNAPSHOT_SCHEMA)
            connection.execute(self.CREATE_OPERATION_RESULTS_SCHEMA)
            connection.execute(self.CREATE_SESSION_STATE_SCHEMA)
            connection.execute(self.CREATE_OFFLINE_SETTLEMENTS_SCHEMA)
            connection.execute(self.CREATE_SCHEMA_META_SCHEMA)
            # Check an existing marker before any ALTER/repair work.  A
            # future schema must not be partially migrated by an older binary
            # before it reports that it cannot understand the database.
            self._assert_supported_storage_schema_version(connection)
            self._migrate_snapshot_schema(connection)
            self._migrate_session_state_schema(connection)
            self._migrate_existing_accounts(connection)
            self._ensure_storage_indexes(connection)
            self._record_storage_schema(connection)
            self._validate_storage_schema(connection)
            connection.commit()
            return connection
        except BindingServiceError:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()
            raise BindingServiceError("pet_care_storage_unavailable") from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _migrate_snapshot_schema(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(pet_care_snapshots)").fetchall()
        }
        if "cleaning_cooldowns_json" not in columns:
            connection.execute(
                "ALTER TABLE pet_care_snapshots ADD COLUMN cleaning_cooldowns_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "activity_counters_json" not in columns:
            connection.execute(
                "ALTER TABLE pet_care_snapshots ADD COLUMN activity_counters_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "interaction_state_json" not in columns:
            connection.execute(
                "ALTER TABLE pet_care_snapshots ADD COLUMN interaction_state_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "unlock_checkpoint" not in columns:
            connection.execute(
                "ALTER TABLE pet_care_snapshots ADD COLUMN unlock_checkpoint INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _migrate_session_state_schema(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(pet_care_session_state)").fetchall()
        }
        if "last_invalid_at" not in columns:
            connection.execute("ALTER TABLE pet_care_session_state ADD COLUMN last_invalid_at TEXT")
        if "invalid_session_id" not in columns:
            connection.execute("ALTER TABLE pet_care_session_state ADD COLUMN invalid_session_id TEXT")

    @staticmethod
    def _ensure_storage_indexes(connection: sqlite3.Connection) -> None:
        """Create read/retention indexes without changing business semantics."""
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_care_operations_created_at "
            "ON pet_care_operation_results (created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_care_offline_account_valid_at "
            "ON pet_care_offline_settlements (account_id, valid_at)"
        )

    def _record_storage_schema(self, connection: sqlite3.Connection) -> None:
        self._assert_supported_storage_schema_version(connection)
        existing = connection.execute(
            "SELECT schema_version FROM pet_care_schema_meta WHERE schema_name = ?",
            ("pet_care",),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO pet_care_schema_meta (schema_name, schema_version, migrated_at) "
                "VALUES (?, ?, ?)",
                ("pet_care", self._STORAGE_SCHEMA_VERSION, self._current_time()),
            )
            return
        # Keep the original migration timestamp when no migration occurred;
        # repeatedly opening a service is an access, not a schema migration.
        if int(existing[0]) < self._STORAGE_SCHEMA_VERSION:
            connection.execute(
                "UPDATE pet_care_schema_meta SET schema_version = ?, migrated_at = ? "
                "WHERE schema_name = ?",
                (self._STORAGE_SCHEMA_VERSION, self._current_time(), "pet_care"),
            )

    def _assert_supported_storage_schema_version(self, connection: sqlite3.Connection) -> None:
        try:
            existing = connection.execute(
                "SELECT schema_version FROM pet_care_schema_meta WHERE schema_name = ?",
                ("pet_care",),
            ).fetchone()
        except sqlite3.Error as exc:
            raise BindingServiceError("pet_care_storage_schema_invalid") from exc
        if existing is not None:
            try:
                existing_version = int(existing[0])
            except (TypeError, ValueError, OverflowError) as exc:
                raise BindingServiceError("pet_care_storage_schema_invalid") from exc
            if existing_version > self._STORAGE_SCHEMA_VERSION:
                # A newer deployment may have added columns or changed
                # invariants that this binary cannot safely understand.
                # Never overwrite that marker and continue on a downgraded
                # process; fail closed until the matching service is used.
                raise BindingServiceError("pet_care_storage_schema_version_unsupported")

    @classmethod
    def _validate_storage_schema(cls, connection: sqlite3.Connection) -> None:
        """Fail closed when a partially created/corrupt database is opened."""
        expected: dict[str, set[str]] = {
            "pet_care_accounts": {
                "account_id", "pet_id", "bound_role_id", "bound_character_uid",
                "created_at", "updated_at",
            },
            "pet_care_snapshots": {
                "pet_id", "account_id", "bound_role_id", "schema_version",
                "last_settled_at", "status_values_json", "negative_status_json",
                "inventory_json", "tasks_json", "unlocks_json", "exploration_json",
                "cleaning_cooldowns_json", "activity_counters_json",
                "interaction_state_json", "unlock_checkpoint", "created_at", "updated_at",
            },
            "pet_care_operation_results": {
                "account_id", "idempotency_key", "request_hash", "result_json", "created_at",
            },
            "pet_care_session_state": {
                "account_id", "last_session_id", "last_valid_at", "last_invalid_at",
                "invalid_session_id", "last_reconciled_segment_id", "updated_at",
            },
            "pet_care_offline_settlements": {
                "segment_id", "account_id", "pet_id", "bound_role_id", "session_id",
                "previous_session_id", "invalid_at", "valid_at", "duration_seconds",
                "config_version", "result_json", "created_at",
            },
            "pet_care_schema_meta": {"schema_name", "schema_version", "migrated_at"},
        }
        for table, required_columns in expected.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {str(row[1]) for row in rows}
            if not required_columns.issubset(columns):
                raise BindingServiceError("pet_care_storage_schema_invalid")
        row = connection.execute(
            "SELECT schema_version FROM pet_care_schema_meta WHERE schema_name = ?",
            ("pet_care",),
        ).fetchone()
        try:
            stored_version = int(row[0]) if row is not None else None
        except (TypeError, ValueError, OverflowError):
            stored_version = None
        if stored_version != cls._STORAGE_SCHEMA_VERSION:
            raise BindingServiceError("pet_care_storage_schema_invalid")

    def _current_time(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise BindingServiceError("pet_care_clock_invalid")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _from_json(value: str | None, fallback: Any) -> Any:
        if not value:
            return deepcopy(fallback)
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return deepcopy(fallback)

    @classmethod
    def _stored_operation_result(cls, value: str | None) -> dict[str, Any]:
        """Decode a durable idempotency receipt or fail closed.

        Returning ``{}`` for corrupt JSON would make a retried write appear to
        have completed without a usable confirmation snapshot.  A corrupt
        receipt is a storage integrity error and must be repaired/rolled back
        by the service, never interpreted as a new business result.
        """
        result = cls._from_json(value, None)
        if not isinstance(result, dict):
            raise BindingServiceError("pet_care_operation_result_invalid")
        # Exploration receipts persist the public envelope, whose confirmed
        # flag lives in the nested ``result`` object; other operations persist
        # the result directly.  Both shapes are valid durable receipts.
        if not isinstance(result.get("ok"), bool):
            nested = result.get("result")
            if not isinstance(nested, Mapping) or not isinstance(nested.get("ok"), bool):
                raise BindingServiceError("pet_care_operation_result_invalid")
        return result

    @classmethod
    def _stored_offline_settlement_result(cls, value: str | None) -> dict[str, Any]:
        """Decode one durable offline-settlement receipt.

        Offline reconciliation stores the direct ``reconciliation`` payload,
        while product operation receipts store an envelope with a top-level
        ``ok`` flag (or a nested operation result).  Reusing the latter
        decoder for this table made a valid reconciliation replay fail closed
        because the direct payload intentionally has no ``ok`` field.  Keep a
        separate shape check so a replay is both idempotent and identical to
        the first reconciliation result.
        """
        result = cls._from_json(value, None)
        if not isinstance(result, dict) or result.get("status") != "confirmed":
            raise BindingServiceError("pet_care_operation_result_invalid")
        return result

    @staticmethod
    def _scope_request_hash(request_hash: str, row: sqlite3.Row) -> str:
        """Bind an idempotency digest to the current pet/role generation."""
        return LocalPetCareBindingService._request_hash({
            "request_hash": request_hash,
            "pet_id": str(row["pet_id"]),
            "bound_role_id": str(row["bound_role_id"]),
        })

    def _initial_snapshot(self, row: sqlite3.Row, now: str | None = None) -> dict[str, Any]:
        now = now or self._current_time()
        negative_status = self._normalize_negative_status(self.snapshot_config["initial_negative_status"])
        mood_formula = self.snapshot_config.get("mood_formula", {})
        return {
            "schema_version": int(self.snapshot_config.get("schema_version", 1)),
            "pet_id": str(row["pet_id"]),
            "account_id": str(row["account_id"]),
            "bound_role_id": str(row["bound_role_id"]),
            "last_settled_at": now,
            "status_values": deepcopy(self.snapshot_config["initial_status_values"]),
            "negative_status": negative_status,
            "inventory": deepcopy(self.snapshot_config["initial_inventory"]),
            "tasks": deepcopy(self.snapshot_config["initial_tasks"]),
            "unlocks": deepcopy(self.snapshot_config["initial_unlocks"]),
            "exploration": deepcopy(self.snapshot_config["initial_exploration"]),
            "cleaning_cooldowns": {},
            "activity_counters": {},
            "interaction_index": self._clamp(float(mood_formula.get("initial_interaction_index", 40))),
            "interaction_last_settled_at": now,
            "unlock_checkpoint": 0,
        }

    def _insert_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row, now: str | None = None) -> None:
        snapshot = self._initial_snapshot(row, now)
        timestamp = now or self._current_time()
        connection.execute(
            """
            INSERT OR IGNORE INTO pet_care_snapshots
              (pet_id, account_id, bound_role_id, schema_version, last_settled_at,
               status_values_json, negative_status_json, inventory_json, tasks_json,
               unlocks_json, exploration_json, cleaning_cooldowns_json, activity_counters_json,
               interaction_state_json, unlock_checkpoint, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["pet_id"], snapshot["account_id"], snapshot["bound_role_id"],
                snapshot["schema_version"], snapshot["last_settled_at"],
                self._json(snapshot["status_values"]), self._json(snapshot["negative_status"]),
                self._json(snapshot["inventory"]), self._json(snapshot["tasks"]),
                self._json(snapshot["unlocks"]), self._json(snapshot["exploration"]),
                self._json(snapshot["cleaning_cooldowns"]),
                self._json(snapshot["activity_counters"]),
                self._json({
                    "index": snapshot.get("interaction_index", 40),
                    "last_settled_at": snapshot.get("interaction_last_settled_at", timestamp),
                }),
                int(snapshot.get("unlock_checkpoint", 0)),
                timestamp, timestamp,
            ),
        )

    def _migrate_existing_accounts(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT * FROM pet_care_accounts").fetchall()
        for row in rows:
            self._insert_snapshot(connection, row)

    def _ensure_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        exists = connection.execute(
            "SELECT 1 FROM pet_care_snapshots WHERE pet_id = ?", (row["pet_id"],)
        ).fetchone()
        if exists is None:
            self._insert_snapshot(connection, row)

    def _read_snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
        snapshot_row = connection.execute(
            "SELECT * FROM pet_care_snapshots WHERE pet_id = ?", (row["pet_id"],)
        ).fetchone()
        if snapshot_row is None:
            return None
        try:
            interaction_state = self._from_json(snapshot_row["interaction_state_json"], {})
            if not isinstance(interaction_state, Mapping):
                interaction_state = {}
            interaction_index = interaction_state.get(
                "index",
                self.snapshot_config.get("mood_formula", {}).get("initial_interaction_index", 40),
            )
            interaction_last_settled_at = interaction_state.get(
                "last_settled_at", str(snapshot_row["last_settled_at"]),
            )
            snapshot = {
                "schema_version": int(snapshot_row["schema_version"]),
                "pet_id": str(snapshot_row["pet_id"]),
                "account_id": str(snapshot_row["account_id"]),
                "bound_role_id": str(snapshot_row["bound_role_id"]),
                "last_settled_at": str(snapshot_row["last_settled_at"]),
                "status_values": self._from_json(snapshot_row["status_values_json"], {}),
                "negative_status": self._normalize_negative_status(
                    self._from_json(snapshot_row["negative_status_json"], {})
                ),
                "inventory": self._from_json(snapshot_row["inventory_json"], {}),
                "tasks": self._from_json(snapshot_row["tasks_json"], {}),
                "unlocks": self._from_json(snapshot_row["unlocks_json"], []),
                "exploration": self._from_json(snapshot_row["exploration_json"], None),
                "cleaning_cooldowns": self._from_json(snapshot_row["cleaning_cooldowns_json"], {}),
                "activity_counters": self._from_json(snapshot_row["activity_counters_json"], {}),
                "interaction_index": float(interaction_index),
                "interaction_last_settled_at": str(interaction_last_settled_at),
                "unlock_checkpoint": int(snapshot_row["unlock_checkpoint"]),
            }
        except (TypeError, ValueError, OverflowError) as exc:
            raise BindingServiceError("pet_care_snapshot_invalid") from exc
        # The JSON payload is not an identity source.  A damaged or manually
        # mixed row must never let one account read another account's pet or
        # stale role binding; the relational owner row remains authoritative.
        for key in ("pet_id", "account_id", "bound_role_id"):
            if str(snapshot.get(key, "")) != str(row[key]):
                raise BindingServiceError("pet_care_snapshot_invalid")
        self._validate_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _normalize_negative_status(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, Mapping) else {}
        normalized: dict[str, Any] = {}
        for key in ("sick", "sulk"):
            current = source.get(key)
            if isinstance(current, Mapping):
                record = deepcopy(dict(current))
                record["active"] = bool(record.get("active", False))
            else:
                record = {"active": bool(current) if current is not None else False}
            normalized[key] = record
        for key, record in source.items():
            if key not in normalized and isinstance(key, str) and isinstance(record, Mapping):
                normalized[key] = deepcopy(dict(record))
        return normalized

    @classmethod
    def _validate_snapshot(cls, snapshot: Mapping[str, Any]) -> None:
        if (
            isinstance(snapshot.get("schema_version"), bool)
            or not isinstance(snapshot.get("schema_version"), int)
            or snapshot["schema_version"] < 1
            or not all(isinstance(snapshot.get(key), str) and snapshot[key] for key in
                       ("pet_id", "account_id", "bound_role_id", "last_settled_at"))
        ):
            raise BindingServiceError("pet_care_snapshot_invalid")
        cls._parse_time(snapshot["last_settled_at"])
        values = snapshot.get("status_values")
        if not isinstance(values, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        for key in ("satiety", "energy", "cleanliness", "mood", "progress"):
            value = values.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
                or (key != "progress" and value > 100)
            ):
                raise BindingServiceError("pet_care_snapshot_invalid")
        if not isinstance(snapshot.get("negative_status"), dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        for status_key in ("sick", "sulk"):
            state = snapshot["negative_status"].get(status_key)
            if not isinstance(state, Mapping) or not isinstance(state.get("active", False), bool):
                raise BindingServiceError("pet_care_snapshot_invalid")
        inventory = snapshot.get("inventory")
        if not isinstance(inventory, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        for item_id, item in inventory.items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise BindingServiceError("pet_care_snapshot_invalid")
            quantity = item
            if isinstance(item, Mapping):
                if "quantity" in item:
                    quantity = item["quantity"]
                elif "count" in item:
                    quantity = item["count"]
                else:
                    continue
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, (int, float))
                or not math.isfinite(float(quantity))
                or float(quantity) < 0
                or not float(quantity).is_integer()
            ):
                raise BindingServiceError("pet_care_snapshot_invalid")
        if not isinstance(snapshot.get("tasks"), dict) or not isinstance(snapshot.get("unlocks"), list):
            raise BindingServiceError("pet_care_snapshot_invalid")
        exploration = snapshot.get("exploration")
        if exploration is not None and not isinstance(exploration, Mapping):
            raise BindingServiceError("pet_care_snapshot_invalid")
        if isinstance(exploration, Mapping):
            active = exploration.get("active")
            if active is not None and not isinstance(active, bool):
                raise BindingServiceError("pet_care_snapshot_invalid")
            status = exploration.get("status")
            if status is not None and (not isinstance(status, str) or not status.strip()):
                raise BindingServiceError("pet_care_snapshot_invalid")
            activity_id = exploration.get("exploration_id", exploration.get("activity_id"))
            if activity_id is not None and (not isinstance(activity_id, str) or not activity_id.strip()):
                raise BindingServiceError("pet_care_snapshot_invalid")
            duration = exploration.get("duration_seconds", exploration.get("duration"))
            if duration is not None and (
                isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration)) or float(duration) <= 0
            ):
                raise BindingServiceError("pet_care_snapshot_invalid")
            for key in ("started_at", "accepted_at", "completed_at"):
                value = exploration.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise BindingServiceError("pet_care_snapshot_invalid")
        if not isinstance(snapshot.get("cleaning_cooldowns"), dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        if not isinstance(snapshot.get("activity_counters"), dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        interaction_index = snapshot.get("interaction_index", 40)
        if (
            isinstance(interaction_index, bool)
            or not isinstance(interaction_index, (int, float))
            or not math.isfinite(float(interaction_index))
            or not 0 <= float(interaction_index) <= 100
            or not isinstance(snapshot.get("interaction_last_settled_at"), str)
        ):
            raise BindingServiceError("pet_care_snapshot_invalid")
        cls._parse_time(snapshot["interaction_last_settled_at"])
        checkpoint = snapshot.get("unlock_checkpoint", 0)
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, (int, float)) or not math.isfinite(float(checkpoint)) or checkpoint < 0:
            raise BindingServiceError("pet_care_snapshot_invalid")

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise BindingServiceError("pet_care_snapshot_invalid") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _clamp(value: float) -> int | float:
        bounded = max(0.0, min(100.0, float(value)))
        return int(math.floor(bounded + 0.5))

    @staticmethod
    def _clamp_progress(value: float) -> int | float:
        """Round cumulative progress without applying a status-value cap."""
        return int(math.floor(max(0.0, float(value)) + 0.5))

    def _mood_formula(self) -> Mapping[str, Any]:
        configured = self.snapshot_config.get("mood_formula", {})
        if not isinstance(configured, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        return configured

    def _recompute_mood(self, snapshot: dict[str, Any]) -> bool:
        """Derive mood from three care values and the service-owned interaction index.

        The formula is deliberately server-side and configuration-driven.  No
        Cat Mind values are read, and the optional jitter defaults to zero so
        repeated reads do not create a fresh random mood.
        """
        values = snapshot.get("status_values")
        if not isinstance(values, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        formula = self._mood_formula()
        weights = formula.get("weights", {})
        if not isinstance(weights, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        interaction = max(0.0, min(100.0, float(snapshot.get("interaction_index", 40))))
        raw = 0.0
        for key in ("satiety", "energy", "cleanliness"):
            raw += float(weights.get(key, 0) or 0) * max(0.0, min(100.0, float(values.get(key, 0))))
        raw += float(weights.get("interaction", 0) or 0) * interaction
        jitter_range = int(formula.get("jitter_range", 0) or 0)
        # A non-zero jitter is accepted as a future configurable extension,
        # but remains deterministic per settlement by deriving it from the
        # persisted interaction timestamp rather than re-rolling on reads.
        if jitter_range:
            seed = self._request_hash({
                "pet_id": snapshot.get("pet_id"),
                "interaction_last_settled_at": snapshot.get("interaction_last_settled_at"),
            })
            jitter = (int(seed[:8], 16) % (2 * jitter_range + 1)) - jitter_range
            raw += jitter
        mood = self._clamp(raw)
        changed = values.get("mood") != mood
        values["mood"] = mood
        return changed

    def _apply_interaction_delta(self, snapshot: dict[str, Any], delta: float, now: str) -> bool:
        """Apply a configured user interaction and refresh the derived mood."""
        try:
            amount = float(delta or 0)
        except (TypeError, ValueError):
            amount = 0.0
        before = float(snapshot.get("interaction_index", 40))
        after = self._clamp(before + amount)
        changed = after != before
        snapshot["interaction_index"] = after
        snapshot["interaction_last_settled_at"] = now
        return self._recompute_mood(snapshot) or changed

    @staticmethod
    def _exploration_active(exploration: Any) -> bool:
        if not isinstance(exploration, Mapping):
            return False
        status = str(exploration.get("status", "")).lower()
        if exploration.get("active") is False or status in {"completed", "inactive", "cancelled", "recalled", "returned"}:
            return False
        # New records always carry an explicit state and stable activity id.
        # Accept the old stage-4 ``{"active": true}`` marker for migration
        # and existing snapshots, but do not treat an arbitrary JSON object as
        # an active activity.
        if status in {"starting", "away", "active", "running", "in_progress", "completing", "recalling"}:
            return bool(exploration.get("exploration_id") or exploration.get("activity_id"))
        return exploration.get("active") is True and not status

    @staticmethod
    def _exploration_id(exploration: Mapping[str, Any]) -> str:
        value = exploration.get("exploration_id", exploration.get("activity_id"))
        return str(value) if isinstance(value, str) else ""

    def _configured_exploration_locations(self) -> list[dict[str, Any]]:
        configured = self.snapshot_config.get("exploration", {})
        if not isinstance(configured, Mapping):
            return []
        locations = configured.get("locations", [])
        if not isinstance(locations, list):
            locations = []
        result = [deepcopy(dict(item)) for item in locations if isinstance(item, Mapping)]
        # Accept the compact one-location form while keeping the persisted
        # contract normalized to ``locations[]``.  This is useful for local
        # deployments that only need to replace the first location's values.
        compact = configured.get("location")
        if isinstance(compact, Mapping) and result:
            result[0].update(deepcopy(dict(compact)))
        compact_keys = {
            "id", "location_id", "name", "description", "duration", "duration_seconds",
            "cost", "costs", "letter_count", "letters", "reward", "note", "away_note",
            "asset_url", "image", "image_url", "letter_image", "letter_image_url",
        }
        if result and any(key in configured for key in compact_keys):
            result[0].update({key: deepcopy(configured[key]) for key in compact_keys if key in configured})
        return result

    def _configured_exploration_location(self, location_id: str | None) -> dict[str, Any] | None:
        locations = self._configured_exploration_locations()
        if location_id:
            for location in locations:
                if str(location.get("id", location.get("location_id", ""))) == location_id:
                    return location
            return None
        return locations[0] if len(locations) == 1 else None

    def _public_exploration_locations(self) -> list[dict[str, Any]]:
        public: list[dict[str, Any]] = []
        for location in self._configured_exploration_locations():
            location_id = str(location.get("id", location.get("location_id", "")))
            item = {"id": location_id, "name": location.get("name", location_id)}
            for key in (
                "description", "note", "duration_seconds", "duration", "costs", "cost",
                "asset_url", "image", "image_url", "letter_image", "letter_image_url",
            ):
                if key in location:
                    item[key] = deepcopy(location[key])
            public.append(item)
        return public

    def _public_exploration_activity(self, exploration: Any) -> Any:
        """Return the client-safe view of an exploration activity.

        The selected letter pool and random seed are server-owned settlement
        inputs.  While an activity is away, only letters whose reveal ratio has
        been reached may cross the API boundary.  Terminal responses expose
        the already accepted full set so the client can render the confirmed
        result without learning private data before settlement.
        """
        if not isinstance(exploration, Mapping):
            return deepcopy(exploration)
        public = deepcopy(dict(exploration))
        active = self._exploration_active(public)
        revealed = public.get("letters_revealed", [])
        if not isinstance(revealed, list):
            revealed = []
        if active:
            public["letters"] = deepcopy(revealed)
            public["letters_revealed"] = deepcopy(revealed)
        else:
            public["letters_revealed"] = deepcopy(public.get("letters", revealed))
            public["letters_available"] = len(public["letters_revealed"])
        public.pop("random_seed", None)
        return public

    def _public_exploration_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        public = deepcopy(dict(snapshot))
        if "exploration" in public:
            public["exploration"] = self._public_exploration_activity(public.get("exploration"))
        return public

    @staticmethod
    def _exploration_duration_seconds(location: Mapping[str, Any]) -> float:
        value = location.get("duration_seconds", location.get("duration", 0))
        return max(0.001, float(value))

    @staticmethod
    def _exploration_costs(location: Mapping[str, Any]) -> dict[str, float]:
        raw = location.get("costs", location.get("cost", {}))
        raw = raw if isinstance(raw, Mapping) else {}
        return {key: max(0.0, float(raw.get(key, 0))) for key in ("satiety", "energy", "cleanliness")}

    @staticmethod
    def _exploration_ratio(exploration: Mapping[str, Any], now: str) -> float:
        started = LocalPetCareBindingService._parse_time(str(exploration.get("started_at")))
        accepted = LocalPetCareBindingService._parse_time(now)
        duration = float(exploration.get("duration_seconds", 0) or 0)
        if duration <= 0:
            return 0.0
        return max(0.0, min(1.0, (accepted - started).total_seconds() / duration))

    @staticmethod
    def _exploration_letters(location: Mapping[str, Any], seed: str) -> list[dict[str, Any]]:
        configured = location.get("letters", [])
        letters = [deepcopy(dict(item)) for item in configured if isinstance(item, Mapping)] if isinstance(configured, list) else []
        if not letters:
            return []
        count_config = location.get("letter_count", {})
        minimum = 1
        maximum = min(3, len(letters))
        if isinstance(count_config, Mapping):
            minimum = max(0, int(count_config.get("min", count_config.get("minimum", minimum))))
            maximum = min(maximum, int(count_config.get("max", count_config.get("maximum", maximum))))
        if maximum < minimum:
            minimum = maximum
        randomizer = random.Random(seed)
        count = randomizer.randint(minimum, maximum) if maximum > 0 else 0
        randomizer.shuffle(letters)
        selected = letters[:count]
        location_image = location.get("letter_image_url", location.get("letter_image"))
        if isinstance(location_image, str) and location_image.strip():
            location_image = location_image.strip()
        else:
            location_image = ""
        for index, letter in enumerate(selected):
            letter.setdefault("index", index)
            letter.setdefault("available_ratio", round((index + 1) / max(3, count), 6))
            # A location may provide one postcard image for all letters, or a
            # letter may provide its own image.  Never fall back to the
            # desktop cat/nest asset.
            if location_image and not any(
                isinstance(letter.get(key), str) and letter.get(key).strip()
                for key in ("image_url", "image", "image_src")
            ):
                letter["image_url"] = location_image
        return selected

    @staticmethod
    def _exploration_reward(
        location: Mapping[str, Any], seed: str, ratio: float, *, completed: bool,
        mood_at_departure: float | None = None,
    ) -> dict[str, Any]:
        reward_config = location.get("reward", {})
        reward_config = reward_config if isinstance(reward_config, Mapping) else {}
        progress_base = float(reward_config.get("progress_delta", 0) or 0)
        full_bonus = float(reward_config.get("full_bonus_progress", 0) or 0) if completed else 0.0
        mood_multiplier = reward_config.get("mood_multiplier", {})
        multiplier = 1.0
        if isinstance(mood_multiplier, Mapping):
            mood = max(0.0, min(100.0, float(mood_at_departure if mood_at_departure is not None else 100.0)))
            tiers = mood_multiplier.get("tiers")
            if isinstance(tiers, list) and tiers:
                selected = None
                for tier in tiers:
                    if isinstance(tier, Mapping) and mood <= float(tier.get("max", 100)):
                        selected = tier.get("value", tier.get("multiplier"))
                        break
                if selected is None and isinstance(tiers[-1], Mapping):
                    selected = tiers[-1].get("value", tiers[-1].get("multiplier"))
                multiplier = max(0.0, float(selected if selected is not None else 1.0))
            else:
                base = float(mood_multiplier.get("base", 1.0) or 0)
                slope = float(mood_multiplier.get("slope", 0.0) or 0)
                multiplier = max(0.0, base + slope * ((mood - 50.0) / 50.0))
        elif isinstance(mood_multiplier, (int, float)) and not isinstance(mood_multiplier, bool):
            multiplier = max(0.0, float(mood_multiplier))
        progress = LocalPetCareBindingService._clamp_progress(progress_base * ratio * multiplier + full_bonus * multiplier)
        randomizer = random.Random(f"{seed}:reward")
        drops: list[dict[str, Any]] = []
        configured_drops = reward_config.get("drops", [])
        if ratio > 0 and isinstance(configured_drops, list):
            for drop in configured_drops:
                if not isinstance(drop, Mapping):
                    continue
                chance = max(0.0, min(1.0, float(drop.get("chance", 1.0) or 0)))
                if randomizer.random() > chance:
                    continue
                low = max(0, int(drop.get("min", 1)))
                high = max(low, int(drop.get("max", low)))
                quantity = int(math.floor(randomizer.uniform(low, high + 1))) if high > low else low
                # Partial recall may not award a full-completion drop unless
                # the location explicitly opts in with a minimum ratio.
                minimum_ratio = float(drop.get("minimum_ratio", 0) or 0)
                if ratio < max(0.0, min(1.0, minimum_ratio)):
                    continue
                if ratio < 1.0 and drop.get("scale_with_ratio", True) is not False:
                    quantity = int(math.floor(quantity * ratio))
                if quantity > 0:
                    drops.append({"item_id": str(drop.get("item_id")), "quantity": quantity})
        return {"progress_delta": int(progress), "full_bonus": int(full_bonus * multiplier), "mood_multiplier": round(multiplier, 6), "drops": drops, "ratio": round(ratio, 6), "completed": bool(completed)}

    def _settle_snapshot(self, snapshot: dict[str, Any], now: str) -> bool:
        """Apply elapsed home-state changes once and move the settlement mark.

        This is deliberately a pure service operation: it never consults Cat
        Mind.  An active exploration record freezes home recovery/decay until
        the exploration service later supplies its own settlement rules.
        """
        previous = self._parse_time(snapshot["last_settled_at"])
        current = self._parse_time(now)
        elapsed_seconds = (current - previous).total_seconds()
        # A clock correction must not move the settlement cursor backwards;
        # otherwise the same elapsed period could be charged twice later.
        if elapsed_seconds <= 0:
            # A newly created or migrated snapshot may still carry the
            # editable initial mood value. Re-derive it once from the
            # service-owned interaction index so every read returns the same
            # authoritative formula result without advancing time.
            return self._recompute_mood(snapshot)
        elapsed_hours = elapsed_seconds / 3600.0
        interaction_last = self._parse_time(
            str(snapshot.get("interaction_last_settled_at", snapshot["last_settled_at"]))
        )
        interaction_elapsed_hours = max(0.0, (current - interaction_last).total_seconds() / 3600.0)
        mood_formula = self._mood_formula()
        decay = max(0.0, float(mood_formula.get("interaction_decay_per_hour", 0) or 0))
        interaction_before = float(snapshot.get("interaction_index", mood_formula.get("initial_interaction_index", 40)))
        interaction_after = self._clamp(interaction_before - decay * interaction_elapsed_hours)
        snapshot["interaction_index"] = interaction_after
        snapshot["interaction_last_settled_at"] = now
        exploration = snapshot.get("exploration")
        if exploration is not None and not isinstance(exploration, Mapping):
            raise BindingServiceError("pet_care_snapshot_invalid")
        active_exploration = self._exploration_active(exploration)
        if not active_exploration:
            values = snapshot.get("status_values")
            if not isinstance(values, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            settlement = self.snapshot_config.get("settlement", {})
            deltas = {
                "satiety": -float(settlement.get("satiety_decay_per_hour", 0)) * elapsed_hours,
                "cleanliness": -float(settlement.get("cleanliness_decay_per_hour", 0)) * elapsed_hours,
                "energy": float(settlement.get("energy_recovery_per_hour", 0)) * elapsed_hours,
            }
            for key, delta in deltas.items():
                if key in values:
                    values[key] = self._clamp(float(values[key]) + delta)
        snapshot["last_settled_at"] = now
        self._recompute_mood(snapshot)
        return True

    def _write_snapshot(self, connection: sqlite3.Connection, snapshot: Mapping[str, Any], now: str) -> None:
        cursor = connection.execute(
            """
            UPDATE pet_care_snapshots
            SET schema_version = ?, last_settled_at = ?, status_values_json = ?,
                negative_status_json = ?, inventory_json = ?, tasks_json = ?,
                unlocks_json = ?, exploration_json = ?,
                cleaning_cooldowns_json = ?, activity_counters_json = ?,
                interaction_state_json = ?, unlock_checkpoint = ?, updated_at = ?
            WHERE pet_id = ?
            """,
            (
                int(snapshot["schema_version"]), str(snapshot["last_settled_at"]),
                self._json(snapshot["status_values"]), self._json(snapshot["negative_status"]),
                self._json(snapshot["inventory"]), self._json(snapshot["tasks"]),
                self._json(snapshot["unlocks"]), self._json(snapshot["exploration"]),
                self._json(snapshot.get("cleaning_cooldowns", {})),
                self._json(snapshot.get("activity_counters", {})),
                self._json({
                    "index": snapshot.get("interaction_index", 40),
                    "last_settled_at": snapshot.get("interaction_last_settled_at", now),
                }),
                int(snapshot.get("unlock_checkpoint", 0)), now, str(snapshot["pet_id"]),
            ),
        )
        if cursor.rowcount != 1:
            raise BindingServiceError("pet_care_snapshot_unavailable")

    @staticmethod
    def _operation_hash(category: str, content_id: str, event: Mapping[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {"category": category, "content_id": content_id}
        if event:
            payload["event"] = dict(event)
        encoded = json.dumps(
            payload,
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _inventory_quantity(inventory: Mapping[str, Any], item_id: str) -> float:
        value = inventory.get(item_id, 0)
        if isinstance(value, Mapping):
            value = value.get("quantity", value.get("count", 0))
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return 0.0
        quantity = float(value)
        return quantity if math.isfinite(quantity) and quantity > 0 else 0.0

    @staticmethod
    def _inventory_decrement(inventory: dict[str, Any], item_id: str) -> None:
        value = inventory.get(item_id, 0)
        if isinstance(value, Mapping):
            updated = dict(value)
            key = "quantity" if "quantity" in updated else "count"
            updated[key] = max(0, int(updated.get(key, 0)) - 1)
            inventory[item_id] = updated
        else:
            inventory[item_id] = max(0, int(value) - 1)

    @staticmethod
    def _inventory_increment(inventory: dict[str, Any], item_id: str, quantity: int) -> None:
        value = inventory.get(item_id, 0)
        if isinstance(value, Mapping):
            updated = dict(value)
            key = "quantity" if "quantity" in updated else "count"
            updated[key] = max(0, int(updated.get(key, 0))) + quantity
            inventory[item_id] = updated
        else:
            inventory[item_id] = max(0, int(value) if isinstance(value, (int, float)) else 0) + quantity

    def _service_timezone(self):
        name = str(self.snapshot_config.get("service_day", {}).get("timezone", "local"))
        if name == "local":
            return datetime.now().astimezone().tzinfo or timezone.utc
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BindingServiceError("pet_care_config_invalid") from exc

    def _service_day(self, now: str) -> str:
        local_now = self._parse_time(now).astimezone(self._service_timezone())
        start_hour = int(self.snapshot_config.get("service_day", {}).get("start_hour", 0))
        if local_now.hour < start_hour:
            local_now = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            # A non-midnight start still belongs to the previous service day.
            from datetime import timedelta
            local_now -= timedelta(days=1)
        return local_now.date().isoformat()

    @staticmethod
    def _canonical_play_content(content_id: str) -> str:
        # Keep a small compatibility map for adapters that used an early
        # display-oriented identifier.  Business storage remains canonical.
        return {
            "yarn_ball": "yarn",
            "toy": "teaser_wand",
        }.get(content_id, content_id)

    @staticmethod
    def _play_source_is_autonomous(source: str) -> bool:
        normalized = source.strip().lower().replace("-", "_")
        return (
            normalized in {"cat_mind", "catmind", "autonomous", "journey", "cat1_journey_local"}
            or normalized.startswith("cat_mind_")
            or normalized.startswith("catmind_")
        )

    @staticmethod
    def _play_source_is_allowed(source: str, action_config: Mapping[str, Any]) -> bool:
        normalized = source.strip().lower().replace("-", "_")
        configured = action_config.get("allowed_sources", [])
        if not isinstance(configured, list):
            return False
        return normalized in {
            item.strip().lower().replace("-", "_")
            for item in configured
            if isinstance(item, str) and item.strip()
        }

    def _prepare_activity_counters(self, snapshot: dict[str, Any], now: str) -> tuple[dict[str, Any], bool]:
        counters = snapshot.get("activity_counters")
        if not isinstance(counters, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        service_day = self._service_day(now)
        if counters.get("service_day") != service_day:
            counters = {"service_day": service_day, "playing": {}}
            snapshot["activity_counters"] = counters
            return counters, True
        playing = counters.get("playing")
        if not isinstance(playing, dict):
            counters["playing"] = {}
        return counters, False

    @staticmethod
    def _play_count(counters: Mapping[str, Any], content_id: str) -> int:
        playing = counters.get("playing", {})
        if not isinstance(playing, Mapping):
            return 0
        value = playing.get(content_id, 0)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0

    def _play_action_summary(
        self, snapshot: Mapping[str, Any], content_id: str, action_config: Mapping[str, Any]
    ) -> dict[str, Any]:
        counters = snapshot.get("activity_counters", {})
        count = self._play_count(counters, content_id)
        progress_limit = action_config.get("daily_progress_limit")
        daily_limit = action_config.get("daily_limit")
        inventory_item = action_config.get("inventory_item")
        inventory = snapshot.get("inventory", {})
        summary = {
            "content_id": content_id,
            "label": action_config.get("label") or action_config.get("display_name"),
            "daily_count": count,
            "daily_progress_limit": int(progress_limit) if progress_limit is not None else None,
            "remaining_progress_count": max(0, int(progress_limit) - count) if progress_limit is not None else None,
            "daily_limit": int(daily_limit) if daily_limit is not None else None,
            "remaining_daily_count": max(0, int(daily_limit) - count) if daily_limit is not None else None,
            "progress_granted": progress_limit is None or count <= int(progress_limit),
            "inventory_item": inventory_item,
            "inventory_quantity": self._inventory_quantity(inventory, str(inventory_item)) if inventory_item else None,
        }
        return summary

    def _play_actions_for_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        configured = self.snapshot_config.get("playing", {})
        if not isinstance(configured, Mapping):
            return {}
        actions: dict[str, Any] = {}
        for content_id, raw in configured.items():
            if not isinstance(raw, Mapping):
                continue
            actions[str(content_id)] = self._play_action_summary(snapshot, str(content_id), raw)
            actions[str(content_id)]["feedback_action"] = raw.get("feedback_action")
            actions[str(content_id)]["progress_delta"] = int(raw.get("progress_delta", 0))
            actions[str(content_id)]["energy_delta"] = float(raw.get("energy_delta", 0))
            actions[str(content_id)]["cleanliness_delta"] = float(raw.get("cleanliness_delta", 0))
            actions[str(content_id)]["interaction_delta"] = float(raw.get("interaction_delta", 0))
        return actions

    def catalog_for_snapshot(self, snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
        """Return the public content catalog for a confirmed snapshot.

        HTTP routes use this public method instead of reaching into a local
        service helper.  A future community adapter can expose the same
        catalog contract without changing the route or the panel.
        """
        source = snapshot if isinstance(snapshot, Mapping) else {}
        care_actions = self.snapshot_config.get("care_actions", {})
        return {
            "care_actions": deepcopy(care_actions) if isinstance(care_actions, Mapping) else {},
            "play_actions": self._play_actions_for_snapshot(source),
        }

    @staticmethod
    def _task_item_from_config(configured: Mapping[str, Any]) -> dict[str, Any]:
        reward = configured.get("reward", {})
        task_id = str(configured.get("task_id", configured.get("id")))
        item = {
            "id": task_id,
            "task_id": task_id,
            "title": str(configured.get("title", task_id)),
            "description": configured.get("description"),
            "status": "available",
            "condition": deepcopy(configured.get("condition")),
            "progress_source": deepcopy(configured.get("progress_source")),
            "group_id": configured.get("group_id"),
            "group_label": configured.get("group_label"),
            "reset_policy": configured.get("reset_policy") or "daily",
            "claimable": bool(configured.get("claimable", True)),
            "reward": {
                "item_id": str(reward.get("item_id") or ""),
                "quantity": int(reward.get("quantity", 0)),
            },
        }
        # A claim-only reward task has no progress contract.  In particular,
        # do not synthesize ``progress: 0`` for a task such as领取小鱼干;
        # clients must not turn a missing target into a fake 0/0 counter.
        # Progress fields are materialized only when the service configuration
        # explicitly defines them, or when _sync_task_progress projects a
        # configured progress source (for example 毛线球).
        if "progress" in configured:
            item["progress"] = deepcopy(configured.get("progress"))
        if "target" in configured:
            item["target"] = deepcopy(configured.get("target"))
        return item

    def _configured_task_catalog(self) -> list[Any]:
        configured_tasks = self.snapshot_config.get("task_catalog")
        if not isinstance(configured_tasks, list):
            configured_tasks = self.snapshot_config.get("daily_tasks", [])
        return configured_tasks if isinstance(configured_tasks, list) else []

    def _refresh_daily_tasks(self, snapshot: dict[str, Any], now: str) -> bool:
        """Materialize the configured task list once per service day.

        Existing stage-2 snapshots intentionally keep their empty task shape
        until this stage-5 endpoint is used.  This makes the migration
        backwards compatible while ensuring every task read/claim has a
        stable service-day record.
        """
        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        _, counters_changed = self._prepare_activity_counters(snapshot, now)
        service_day = self._service_day(now)
        if tasks.get("service_day") == service_day and isinstance(tasks.get("items"), list):
            changed = counters_changed
            # Existing accounts can already have today's task snapshot from
            # before a newly configured independent task was added. Reconcile
            # the catalog within the same service day instead of waiting for
            # midnight, while preserving all existing task state.
            known_ids = {
                str(item.get("task_id", item.get("id")))
                for item in tasks["items"]
                if isinstance(item, Mapping) and str(item.get("task_id", item.get("id")))
            }
            for configured in self._configured_task_catalog():
                if not isinstance(configured, Mapping):
                    continue
                task_id = str(configured.get("task_id", configured.get("id")))
                if task_id and task_id not in known_ids:
                    tasks["items"].append(self._task_item_from_config(configured))
                    known_ids.add(task_id)
                    changed = True
                elif task_id:
                    existing = next((item for item in tasks["items"]
                                     if isinstance(item, dict) and
                                     str(item.get("task_id", item.get("id"))) == task_id), None)
                    if existing is not None:
                        configured_item = self._task_item_from_config(configured)
                        # Refresh catalog metadata in place while preserving
                        # runtime state such as progress, claim status and
                        # claimed_at. This upgrades old same-day records that
                        # predate a task's reset policy or display title.
                        for key in (
                            "id", "task_id", "title", "description", "condition", "target",
                            "progress_source", "group_id", "group_label", "reset_policy",
                            "claimable", "reward",
                        ):
                            value = deepcopy(configured_item.get(key))
                            if existing.get(key) != value:
                                existing[key] = value
                                changed = True
                        # Catalog revisions can remove a progress contract
                        # from a previously materialized reward-only task.
                        # Drop the old synthetic fields instead of preserving
                        # a stale 0/0 shape forever.
                        if "progress" not in configured_item and "progress_source" not in configured_item:
                            for key in ("progress", "target"):
                                if key in existing:
                                    del existing[key]
                                    changed = True
            if not isinstance(tasks.get("daily"), list):
                tasks["daily"] = deepcopy(tasks["items"])
                changed = True
            if not isinstance(tasks.get("daily_login"), Mapping):
                tasks["daily_login"] = {"service_day": service_day, "checked": False}
                changed = True
            changed = self._sync_task_progress(snapshot) or changed
            self._sync_task_aliases(snapshot)
            return changed
        previous_candidates: list[Any] = []
        if isinstance(tasks.get("items"), list):
            previous_candidates.extend(tasks["items"])
        # Older local snapshots stored records only under ``daily`` and
        # ``state``.  Read those aliases during materialization so a schema
        # upgrade does not silently reset an independent task's progress or
        # claimed state.
        for alias in ("daily", "state"):
            if isinstance(tasks.get(alias), list):
                previous_candidates.extend(tasks[alias])
        previous_items = {
            str(item.get("task_id", item.get("id"))): item
            for item in previous_candidates
            if isinstance(item, Mapping) and str(item.get("task_id", item.get("id")))
        }
        items: list[dict[str, Any]] = []
        for configured in self._configured_task_catalog():
            if not isinstance(configured, Mapping):
                continue
            task_id = str(configured.get("task_id", configured.get("id")))
            item = self._task_item_from_config(configured)
            previous = previous_items.get(task_id)
            reset_policy = str(item.get("reset_policy") or "daily")
            if isinstance(previous, Mapping) and reset_policy != "daily":
                for key in ("progress", "target", "status", "claimed_at"):
                    if key in previous:
                        item[key] = deepcopy(previous[key])
            items.append(item)
        # Stage 8 recovery tasks are independent records that are not part of
        # the daily catalog. Preserve them across service-day rollover until
        # their state is resolved; they remain in the canonical items list and
        # are mirrored only for legacy readers.
        known_ids = {
            str(item.get("task_id", item.get("id")))
            for item in items
            if isinstance(item, Mapping)
        }
        for previous in previous_candidates:
            if not isinstance(previous, Mapping) or str(previous.get("reset_policy", "daily")) == "daily":
                continue
            previous_id = str(previous.get("task_id", previous.get("id")))
            if previous_id and previous_id not in known_ids:
                items.append(deepcopy(dict(previous)))
                known_ids.add(previous_id)
        # ``items`` is the canonical independent task collection. Keep the
        # legacy daily/state fields only for old snapshots and clients; the
        # client must not infer fixed task types from those aliases.
        daily_items = [item for item in items if item.get("reset_policy") == "daily"]
        state_items = [item for item in items if item.get("reset_policy") != "daily"]
        snapshot["tasks"] = {
            "service_day": service_day,
            "items": items,
            "daily": daily_items,
            "state": state_items,
            "daily_login": {"service_day": service_day, "checked": False},
        }
        self._sync_task_progress(snapshot)
        self._sync_task_aliases(snapshot)
        return True

    @staticmethod
    def _sync_task_aliases(snapshot: dict[str, Any]) -> None:
        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict) or not isinstance(tasks.get("items"), list):
            return
        tasks["daily"] = [item for item in tasks["items"]
                          if isinstance(item, Mapping) and item.get("reset_policy") == "daily"]
        tasks["state"] = [item for item in tasks["items"]
                          if isinstance(item, Mapping) and item.get("reset_policy") != "daily"]

    def _sync_task_progress(self, snapshot: dict[str, Any]) -> bool:
        """Project service-owned activity counters into configured task rows."""
        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict) or not isinstance(tasks.get("items"), list):
            return False
        changed = False
        playing = snapshot.get("activity_counters", {}).get("playing", {})
        if not isinstance(playing, Mapping):
            playing = {}
        for item in tasks["items"]:
            if not isinstance(item, dict):
                continue
            source = item.get("progress_source")
            if not isinstance(source, Mapping) or source.get("category") != "playing":
                continue
            content_id = str(source.get("content_id", ""))
            if not content_id:
                continue
            configured_action = self.snapshot_config.get("playing", {}).get(content_id, {})
            configured_target = configured_action.get("daily_progress_limit") if isinstance(configured_action, Mapping) else None
            target = item.get("target")
            if target is None:
                target = configured_target
            count = playing.get(content_id, 0)
            if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
                count = 0
            progress = int(count)
            if target is not None:
                try:
                    target = max(0, int(target))
                except (TypeError, ValueError):
                    target = None
                if target is not None:
                    progress = min(progress, target)
            if item.get("progress") != progress:
                item["progress"] = progress
                changed = True
            if target is not None and item.get("target") != target:
                item["target"] = target
                changed = True
            next_status = "completed" if target is not None and progress >= target else "in_progress"
            if item.get("status") != next_status:
                item["status"] = next_status
                changed = True
        return changed

    def _apply_unlocks(self, snapshot: dict[str, Any], now: str) -> list[dict[str, Any]]:
        values = snapshot.get("status_values")
        unlocks = snapshot.get("unlocks")
        if not isinstance(values, dict) or not isinstance(unlocks, list):
            raise BindingServiceError("pet_care_snapshot_invalid")
        progress = float(values.get("progress", 0))
        checkpoint = int(snapshot.get("unlock_checkpoint", 0))
        for item in unlocks:
            if isinstance(item, Mapping):
                threshold = item.get("threshold")
                if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and math.isfinite(float(threshold)):
                    checkpoint = max(checkpoint, int(threshold))
        snapshot["unlock_checkpoint"] = checkpoint
        known = {
            str(item.get("id")) if isinstance(item, Mapping) else str(item)
            for item in unlocks
            if (isinstance(item, Mapping) and item.get("id") is not None) or isinstance(item, str)
        }
        newly_unlocked: list[dict[str, Any]] = []
        for configured in self.snapshot_config.get("unlock_catalog", []):
            if not isinstance(configured, Mapping):
                continue
            unlock_id = str(configured.get("id"))
            if unlock_id in known or progress < float(configured.get("threshold", 0)):
                continue
            record = {
                "id": unlock_id,
                "threshold": int(configured.get("threshold", 0)),
                "name": configured.get("name", unlock_id),
                "type": configured.get("type", "content"),
            }
            for key in ("resource", "content", "description"):
                if key in configured:
                    record[key] = deepcopy(configured[key])
            unlocks.append(record)
            known.add(unlock_id)
            snapshot["unlock_checkpoint"] = max(int(snapshot.get("unlock_checkpoint", 0)), int(record["threshold"]))
            newly_unlocked.append(deepcopy(record))
        return newly_unlocked

    def _offline_config(self) -> Mapping[str, Any]:
        configured = self.snapshot_config.get("offline_settlement", {})
        if not isinstance(configured, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        return configured

    @staticmethod
    def _session_context_value(session_context: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
        if isinstance(session_context, Mapping):
            return session_context.get(key, default)
        return getattr(session_context, key, default)

    def _assert_current_session(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        session_context: Mapping[str, Any] | Any | None,
    ) -> None:
        """Fence a state-changing request against a replaced/invalid session.

        Routes resolve the session immediately before dispatching an operation,
        but the operation itself may wait on the SQLite write lock. Rechecking
        the opaque session id inside that transaction prevents an old request
        from mutating the account after logout or a newer login wins the edge.
        Direct local service callers may omit the context for compatibility.
        """
        session_id = str(self._session_context_value(session_context, "session_id", "") or "").strip()
        if not session_id:
            return
        state = connection.execute(
            "SELECT last_session_id, last_invalid_at, invalid_session_id "
            "FROM pet_care_session_state WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if state is None or str(state["last_session_id"] or "") != session_id or (
            state["last_invalid_at"] and str(state["invalid_session_id"] or "") == session_id
        ):
            raise BindingServiceError("session_stale", status_code=409)

    def _assert_bind_session(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        session_context: Mapping[str, Any] | Any | None,
    ) -> None:
        """Fence an explicit bind while allowing a verified post-logout login.

        A bind request is the operation that establishes the new role after a
        user has logged out and authenticated again.  The normal write fence
        must reject a different session, but applying it here would make that
        legitimate A (invalidated) -> B transition impossible.  Only the
        explicit invalidation edge opens this one transition; every other
        session mismatch remains stale.
        """
        session_id = str(self._session_context_value(session_context, "session_id", "") or "").strip()
        if not session_id:
            return
        state = connection.execute(
            "SELECT last_session_id, invalid_session_id "
            "FROM pet_care_session_state WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if state is None:
            return
        current_session = str(state["last_session_id"] or "")
        invalid_session = str(state["invalid_session_id"] or "")
        if session_id == current_session:
            self._assert_current_session(connection, account_id, session_context)
            return
        if current_session and invalid_session == current_session and session_id != current_session:
            return
        raise BindingServiceError("session_stale", status_code=409)

    def _assert_reconciliation_session(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        session_context: Mapping[str, Any] | Any | None,
    ) -> None:
        """Reject a known old session while allowing a new replacement.

        ``resolve`` is also the entry point that establishes a *new* login
        session, so applying ``_assert_current_session`` to every different
        session would reject the legitimate A -> B transition.  Once a
        session has been reconciled, its id is retained in the settlement
        ledger; a late request carrying that old id is therefore stale and
        must not be treated as a second replacement.  Before the first B
        reconciliation, ``invalid_session_id`` covers the explicit logout
        edge for A.
        """
        session_id = str(self._session_context_value(session_context, "session_id", "") or "").strip()
        if not session_id:
            return
        state = connection.execute(
            "SELECT last_session_id, invalid_session_id FROM pet_care_session_state "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if state is None:
            return
        current_session = str(state["last_session_id"] or "")
        if session_id == current_session:
            self._assert_current_session(connection, account_id, session_context)
            return
        if session_id and session_id == str(state["invalid_session_id"] or ""):
            raise BindingServiceError("session_stale", status_code=409)
        accepted = connection.execute(
            "SELECT previous_session_id FROM pet_care_offline_settlements "
            "WHERE account_id = ? AND session_id = ? LIMIT 1",
            (account_id, session_id),
        ).fetchone()
        if accepted is not None:
            # A process crash can leave the state fence at the previous
            # session after the settlement row was committed.  Replaying
            # that exact A -> B edge is safe and returns the stored receipt;
            # the same B id is stale once a later session has won the fence.
            if current_session == str(accepted["previous_session_id"] or ""):
                return
            raise BindingServiceError("session_stale", status_code=409)
        known_previous = connection.execute(
            "SELECT 1 FROM pet_care_offline_settlements "
            "WHERE account_id = ? AND previous_session_id = ? LIMIT 1",
            (account_id, session_id),
        ).fetchone()
        if known_previous is not None:
            raise BindingServiceError("session_stale", status_code=409)

    def _recovery_task_config(self, status_key: str) -> Mapping[str, Any]:
        configured = self._offline_config().get(status_key, {})
        if not isinstance(configured, Mapping):
            raise BindingServiceError("pet_care_config_invalid")
        return configured

    def _ensure_recovery_task(self, snapshot: dict[str, Any], status_key: str) -> bool:
        """Materialize one recovery task as an ordinary independent item."""
        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        items = tasks.setdefault("items", [])
        if not isinstance(items, list):
            raise BindingServiceError("pet_care_snapshot_invalid")
        config = self._recovery_task_config(status_key)
        task_id = str(config.get("task_id", f"recovery-{status_key}")).strip()
        item_id = str(config.get("recovery_item_id", "")).strip()
        title = str(config.get("task_title", task_id)).strip() or task_id
        quantity = max(1, int(config.get("recovery_item_quantity", 1) or 1))
        existing = next(
            (item for item in items if isinstance(item, dict) and str(item.get("task_id", item.get("id"))) == task_id),
            None,
        )
        if existing is not None:
            return False
        items.append({
            "id": task_id,
            "task_id": task_id,
            "title": title,
            "description": config.get("task_description", ""),
            "reset_policy": "state",
            "claimable": True,
            "status": "in_progress",
            "recovery_status": status_key,
            "recovery_item_id": item_id,
            "reward": {"item_id": item_id, "quantity": quantity},
        })
        self._sync_task_aliases(snapshot)
        return True

    @staticmethod
    def _status_deficit(values: Mapping[str, Any], key: str) -> float:
        try:
            numeric = float(values.get(key, 0))
        except (TypeError, ValueError):
            numeric = 0.0
        return max(0.0, min(1.0, (100.0 - numeric) / 100.0))

    def _negative_probability(self, status_key: str, values: Mapping[str, Any], elapsed_hours: float) -> float:
        config = self._recovery_task_config(status_key)
        offline = self._offline_config()
        threshold = max(0.0, float(offline.get("negative_threshold_hours", 0) or 0))
        ramp = max(1.0, float(config.get("offline_ramp_hours", 120) or 120))
        time_factor = max(0.0, min(1.0, (elapsed_hours - threshold) / ramp))
        probability = float(config.get("base_probability", 0) or 0)
        probability += float(config.get("offline_weight", 0) or 0) * time_factor
        for value_key in ("cleanliness", "satiety", "mood", "energy"):
            probability += float(config.get(f"{value_key}_deficit_weight", 0) or 0) * self._status_deficit(values, value_key)
        configured_probability = config.get("probability")
        if configured_probability is not None:
            probability = float(configured_probability)
        maximum = float(config.get("max_probability", 1.0) or 1.0)
        return max(0.0, min(1.0, probability, maximum))

    def _apply_offline_reconciliation(
        self,
        snapshot: dict[str, Any],
        *,
        segment_id: str,
        elapsed_seconds: float,
        now: str,
    ) -> dict[str, Any]:
        """Apply one offline segment after normal state settlement."""
        offline = self._offline_config()
        elapsed_hours = max(0.0, elapsed_seconds / 3600.0)
        full_days = int(math.floor(elapsed_seconds / 86400.0))
        rollback_after_days = max(0, int(offline.get("rollback_after_days", 5) or 5))
        rollback_days = max(0, full_days - rollback_after_days)
        rollback_per_day = max(0, int(offline.get("progress_rollback_per_day", 0) or 0))
        values = snapshot.get("status_values")
        if not isinstance(values, dict):
            raise BindingServiceError("pet_care_snapshot_invalid")
        progress_before = self._clamp_progress(float(values.get("progress", 0)))
        checkpoint = max(0, int(snapshot.get("unlock_checkpoint", 0)))
        progress_after = max(checkpoint, progress_before - rollback_days * rollback_per_day)
        values["progress"] = progress_after
        rollback = {
            "applied": progress_after != progress_before,
            "days": rollback_days,
            "delta": int(progress_after - progress_before),
            "checkpoint": checkpoint,
            "before": progress_before,
            "after": progress_after,
        }

        minimum = float(offline.get("protected_min_status", 80) or 80)
        protected = all(float(values.get(key, 0)) >= minimum for key in ("satiety", "energy", "cleanliness", "mood"))
        negative: dict[str, Any] = {"protected": protected, "states": {}}
        random_seed = self._request_hash({
            "segment_id": segment_id,
            "config_version": offline.get("config_version", ""),
        })
        for status_key in ("sick", "sulk"):
            current = snapshot.setdefault("negative_status", {}).setdefault(status_key, {"active": False})
            if not isinstance(current, dict):
                current = {"active": bool(current)}
                snapshot["negative_status"][status_key] = current
            active = bool(current.get("active"))
            probability = 0.0
            draw = None
            triggered = False
            if not active and elapsed_hours >= float(offline.get("negative_threshold_hours", 0) or 0) and not protected:
                probability = self._negative_probability(status_key, values, elapsed_hours)
                draw = random.Random(f"{random_seed}:{status_key}").random()
                triggered = draw < probability
                if triggered:
                    config = self._recovery_task_config(status_key)
                    current.update({
                        "active": True,
                        "activated_at": now,
                        "offline_segment_id": segment_id,
                        "recovery_task_id": str(config.get("task_id", f"recovery-{status_key}")),
                        "required_item": str(config.get("recovery_item_id", "")),
                    })
                    self._ensure_recovery_task(snapshot, status_key)
            negative["states"][status_key] = {
                "already_active": active,
                "probability": round(probability, 8),
                "draw": None if draw is None else round(draw, 8),
                "triggered": triggered,
            }
        self._sync_task_aliases(snapshot)
        return {
            "status": "confirmed",
            "segment_id": segment_id,
            "duration_seconds": round(max(0.0, elapsed_seconds), 3),
            "rollback": rollback,
            "negative": negative,
            "config_version": str(offline.get("config_version", "")),
        }

    def _reconcile_session_in_transaction(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        snapshot: dict[str, Any],
        session_context: Mapping[str, Any] | Any,
        now: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Reconcile a verified login-session replacement inside a transaction."""
        session_id = str(self._session_context_value(session_context, "session_id", "") or "").strip()
        if not session_id:
            return {"status": "skipped", "reason": "session_boundary_unavailable"}, False
        state = connection.execute(
            "SELECT * FROM pet_care_session_state WHERE account_id = ?",
            (str(row["account_id"]),),
        ).fetchone()
        if state is None:
            connection.execute(
                "INSERT INTO pet_care_session_state "
                "(account_id, last_session_id, last_valid_at, last_invalid_at, invalid_session_id, updated_at) "
                "VALUES (?, ?, ?, NULL, NULL, ?)",
                (str(row["account_id"]), session_id, now, now),
            )
            return {"status": "skipped", "reason": "first_verified_session"}, False
        previous_session_id = str(state["last_session_id"] or "")
        if previous_session_id == session_id:
            connection.execute(
                "UPDATE pet_care_session_state SET last_valid_at = ?, updated_at = ? WHERE account_id = ?",
                (now, now, str(row["account_id"])),
            )
            return {"status": "skipped", "reason": "same_session"}, False
        marked_invalid_at = state["last_invalid_at"]
        marked_invalid_session = str(state["invalid_session_id"] or "")
        invalid_at = (
            marked_invalid_at
            if marked_invalid_at and marked_invalid_session == previous_session_id
            else state["last_valid_at"]
        )
        segment_id = hashlib.sha256(
            f"{row['account_id']}:{previous_session_id}:{session_id}".encode("utf-8")
        ).hexdigest()
        stored = connection.execute(
            "SELECT result_json FROM pet_care_offline_settlements WHERE segment_id = ?",
            (segment_id,),
        ).fetchone()
        if stored is not None:
            result = self._stored_offline_settlement_result(stored["result_json"])
            connection.execute(
                "UPDATE pet_care_session_state SET last_session_id = ?, last_valid_at = ?, "
                "last_invalid_at = NULL, invalid_session_id = NULL, last_reconciled_segment_id = ?, "
                "updated_at = ? WHERE account_id = ?",
                (session_id, now, segment_id, now, str(row["account_id"])),
            )
            return result, False
        try:
            elapsed_seconds = max(0.0, (self._parse_time(now) - self._parse_time(str(invalid_at))).total_seconds()) if invalid_at else 0.0
        except BindingServiceError:
            elapsed_seconds = 0.0
        settled = self._settle_snapshot(snapshot, now)
        result = self._apply_offline_reconciliation(
            snapshot,
            segment_id=segment_id,
            elapsed_seconds=elapsed_seconds,
            now=now,
        )
        result["previous_session_id"] = previous_session_id
        result["session_id"] = session_id
        result["settled"] = bool(settled)
        connection.execute(
            "INSERT INTO pet_care_offline_settlements (segment_id, account_id, pet_id, bound_role_id, session_id, previous_session_id, invalid_at, valid_at, duration_seconds, config_version, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                segment_id, str(row["account_id"]), str(row["pet_id"]), str(row["bound_role_id"]),
                session_id, previous_session_id, str(invalid_at) if invalid_at else None, now,
                float(elapsed_seconds), str(result.get("config_version", "")), self._json(result), now,
            ),
        )
        connection.execute(
            "UPDATE pet_care_session_state SET last_session_id = ?, last_valid_at = ?, "
            "last_invalid_at = NULL, invalid_session_id = NULL, last_reconciled_segment_id = ?, "
            "updated_at = ? WHERE account_id = ?",
            (session_id, now, segment_id, now, str(row["account_id"])),
        )
        return result, True

    def mark_session_invalid(
        self,
        account_id: str,
        *,
        session_id: str,
        invalid_at: str | None = None,
    ) -> bool:
        """Persist an explicit account-session invalidation edge.

        The OAuth adapter calls this before clearing local credentials.  It
        stores only the opaque session id and server-side timestamp; no token
        or client-provided offline result enters PetCare state.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 1 FROM pet_care_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            now = invalid_at or self._current_time()
            state = connection.execute(
                "SELECT last_session_id FROM pet_care_session_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if state is None:
                connection.execute(
                    "INSERT INTO pet_care_session_state "
                    "(account_id, last_session_id, last_valid_at, last_invalid_at, invalid_session_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (account_id, session_id, now, now, session_id, now),
                )
            else:
                current_session = str(state["last_session_id"] or "")
                if current_session and current_session != session_id:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE pet_care_session_state SET last_invalid_at = ?, invalid_session_id = ?, updated_at = ? "
                    "WHERE account_id = ?",
                    (now, session_id, now, account_id),
                )
            connection.commit()
            return True
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def reconcile_session_return(
        self,
        account_id: str,
        character_uid: str,
        *,
        session_context: Mapping[str, Any] | Any,
    ) -> dict[str, Any]:
        """Reconcile one trusted session replacement and return its receipt."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            # Keep this public reconciliation entry point behind the same
            # session fence as resolve(). Without the check, an old session
            # could be accepted as a fresh replacement and settle again.
            session_state = connection.execute(
                "SELECT 1 FROM pet_care_session_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if session_state is not None:
                self._assert_reconciliation_session(connection, account_id, session_context)
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            now = self._current_time()
            settled = self._settle_snapshot(snapshot, now)
            reconciliation, changed = self._reconcile_session_in_transaction(connection, row, snapshot, session_context, now)
            if changed and isinstance(reconciliation, dict):
                reconciliation.setdefault("settled", True)
            if changed or settled:
                self._write_snapshot(connection, snapshot, now)
            connection.commit()
            return {
                "ok": True,
                "reconciliation": deepcopy(reconciliation),
                "snapshot": deepcopy(snapshot),
                "tasks": deepcopy(snapshot.get("tasks")),
            }
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    @staticmethod
    def _request_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _context_from_row(
        row: sqlite3.Row | None,
        account_id: str,
        character_uid: str,
        *,
        created: bool = False,
        snapshot: dict[str, Any] | None = None,
        settlement: dict[str, Any] | None = None,
        reconciliation: dict[str, Any] | None = None,
        care_actions: dict[str, Any] | None = None,
        play_actions: dict[str, Any] | None = None,
    ) -> BindingContext:
        if row is None:
            return BindingContext(
                account_id=account_id,
                current_character_uid=character_uid,
                binding_status="needs-binding",
                pet_id=None,
                bound_role_id=None,
                created=created,
                snapshot=None,
                settlement=None,
                reconciliation=None,
                care_actions=None,
                play_actions=None,
            )
        is_bound = str(row["bound_character_uid"]) == character_uid
        return BindingContext(
            account_id=account_id,
            current_character_uid=character_uid,
            binding_status="bound" if is_bound else "unbound",
            pet_id=str(row["pet_id"]) if is_bound else None,
            bound_role_id=str(row["bound_role_id"]) if is_bound else None,
            created=created,
            snapshot=deepcopy(snapshot) if is_bound else None,
            settlement=deepcopy(settlement) if is_bound else None,
            reconciliation=deepcopy(reconciliation) if is_bound else None,
            care_actions=deepcopy(care_actions) if is_bound else None,
            play_actions=deepcopy(play_actions) if is_bound else None,
        )

    def resolve(
        self,
        account_id: str,
        character_uid: str,
        *,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> BindingContext:
        """Read context, creating the account's first pet/binding atomically.

        The first authenticated role is the account's initial binding.  This
        keeps the local stage-1 flow from dead-ending at a client-only
        confirmation screen while preserving one account -> one pet.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            created = row is None
            if row is None:
                now = self._current_time()
                connection.execute(
                    """
                    INSERT INTO pet_care_accounts
                      (account_id, pet_id, bound_role_id, bound_character_uid,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        self._new_id(),
                        self._new_id(),
                        character_uid,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
            # A read with a replaced/invalidated session must not enter
            # reconciliation and move the account's session fence backwards.
            # The first verified session has no state row yet and may
            # initialize it in _reconcile_session_in_transaction below.
            if session_context is not None and row is not None:
                session_state = connection.execute(
                    "SELECT 1 FROM pet_care_session_state WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if session_state is not None:
                    self._assert_reconciliation_session(connection, account_id, session_context)
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            reconciliation = None
            if snapshot is not None and str(row["bound_character_uid"]) == character_uid:
                now = self._current_time()
                settled = self._settle_snapshot(snapshot, now)
                _, counters_changed = self._prepare_activity_counters(snapshot, now)
                checkpoint_before = int(snapshot.get("unlock_checkpoint", 0))
                unlocked = bool(self._apply_unlocks(snapshot, now))
                reconcile_changed = False
                if session_context is not None:
                    reconciliation, reconcile_changed = self._reconcile_session_in_transaction(
                        connection, row, snapshot, session_context, now,
                    )
                if settled or counters_changed or unlocked or reconcile_changed or int(snapshot.get("unlock_checkpoint", 0)) != checkpoint_before:
                    self._write_snapshot(connection, snapshot, now)
            result = self._context_from_row(
                row, account_id, character_uid, created=created, snapshot=snapshot,
                settlement=self.snapshot_config.get("settlement", {}),
                reconciliation=reconciliation,
                care_actions=self.snapshot_config.get("care_actions", {}),
                play_actions=self._play_actions_for_snapshot(snapshot or {}),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def care_action(
        self,
        account_id: str,
        character_uid: str,
        *,
        category: str,
        content_id: str,
        idempotency_key: str,
        source: str | None = None,
        activity_id: str | None = None,
        user_initiated: bool | None = None,
        cat_responded: bool | None = None,
        terminal_status: str | None = None,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Settle one configured care or stage-6 play operation atomically.

        The local adapter intentionally mirrors the future community contract:
        the account, role, inventory, cooldown and state mutation are checked
        in one transaction, and an idempotency key replays the stored result.
        """
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 200):
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        category = category.strip() if isinstance(category, str) else ""
        content_id = content_id.strip() if isinstance(content_id, str) else ""
        if category == "play":
            category = "playing"
        is_playing = category == "playing"
        event: dict[str, Any] = {}
        if is_playing:
            source = source.strip() if isinstance(source, str) else ""
            activity_id = activity_id.strip() if isinstance(activity_id, str) else ""
            if not source:
                raise BindingServiceError("invalid_play_source", status_code=422)
            if not activity_id or len(activity_id) > 200:
                raise BindingServiceError("invalid_activity_id", status_code=422)
            if not isinstance(user_initiated, bool) or not isinstance(cat_responded, bool):
                raise BindingServiceError("invalid_play_event", status_code=422)
            terminal_status = terminal_status.strip().lower() if isinstance(terminal_status, str) else ""
            if terminal_status not in {"done", "cancelled", "no_response", "timeout", "failed"}:
                raise BindingServiceError("invalid_terminal_status", status_code=422)
            content_id = self._canonical_play_content(content_id)
            event = {
                "source": source,
                "activity_id": activity_id,
                "user_initiated": user_initiated,
                "cat_responded": cat_responded,
                "terminal_status": terminal_status,
            }
        action_hash = self._operation_hash(category, content_id, event or None)
        # A play activity is one terminal business event. Keep the caller's
        # key in the response for diagnostics, but canonicalize the storage
        # key by activity so a fresh client key cannot settle it twice.
        operation_result_key = idempotency_key
        if is_playing:
            operation_result_key = f"play:{content_id}:{activity_id}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            action_hash = self._scope_request_hash(action_hash, row)
            stored = connection.execute(
                "SELECT request_hash, result_json FROM pet_care_operation_results "
                "WHERE account_id = ? AND idempotency_key = ?",
                (account_id, operation_result_key),
            ).fetchone()
            if stored is not None:
                if str(stored["request_hash"]) != action_hash:
                    raise BindingServiceError("idempotency_key_reused", status_code=409)
                connection.commit()
                return self._stored_operation_result(stored["result_json"])

            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            now = self._current_time()
            settlement_changed = self._settle_snapshot(snapshot, now)
            task_data = snapshot.get("tasks")
            task_refresh_changed = False
            # A successful yarn/consumable play must return the same
            # service-owned task collection even when the client has not
            # opened the task panel first.  Feeding and cleaning keep the
            # stage-2 empty task shape untouched until the task endpoint is
            # requested; playing is the one stage-6 write that needs its
            # progress-backed row in the confirmation envelope.
            if is_playing or (isinstance(task_data, dict) and isinstance(task_data.get("items"), list)):
                task_refresh_changed = self._refresh_daily_tasks(snapshot, now)

            if is_playing:
                action_config = self.snapshot_config.get("playing", {}).get(content_id)
                if not isinstance(action_config, Mapping):
                    # Accept an explicitly supplied legacy location while
                    # keeping the canonical local configuration top-level.
                    action_config = self.snapshot_config.get("care_actions", {}).get("playing", {}).get(content_id)
            else:
                action_config = self.snapshot_config.get("care_actions", {}).get(category, {}).get(content_id)
            if not isinstance(action_config, Mapping):
                raise BindingServiceError("care_action_unavailable", status_code=422)
            exploration = snapshot.get("exploration")
            if exploration is not None and not isinstance(exploration, Mapping):
                raise BindingServiceError("pet_care_snapshot_invalid")
            if self._exploration_active(exploration):
                raise BindingServiceError("activity_in_progress", status_code=409)

            values = snapshot.get("status_values")
            inventory = snapshot.get("inventory")
            cooldowns = snapshot.get("cleaning_cooldowns")
            if not isinstance(values, dict) or not isinstance(inventory, dict) or not isinstance(cooldowns, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            counters, counters_changed = self._prepare_activity_counters(snapshot, now)
            result_code = None
            item_id = str(action_config.get("inventory_item", content_id))
            if category == "feeding":
                if float(values.get("satiety", 0)) >= 100:
                    result_code = "satiety_full"
                elif self._inventory_quantity(inventory, item_id) < 1:
                    result_code = "inventory_insufficient"
            elif category == "cleaning":
                if float(values.get("cleanliness", 0)) >= 100:
                    result_code = "cleanliness_full"
                else:
                    cooldown = float(action_config.get("cooldown_seconds", 0))
                    cooldown_at = cooldowns.get(content_id)
                    if cooldown and cooldown_at:
                        elapsed = (self._parse_time(now) - self._parse_time(str(cooldown_at))).total_seconds()
                        if elapsed < cooldown:
                            result_code = "cleaning_cooldown"
            elif is_playing:
                if event.get("user_initiated") is not True:
                    result_code = "play_user_not_initiated"
                elif event.get("cat_responded") is not True:
                    result_code = "play_no_response"
                elif event.get("terminal_status") != "done":
                    result_code = "play_not_completed"
                elif self._play_source_is_autonomous(str(event.get("source", ""))) or not self._play_source_is_allowed(str(event.get("source", "")), action_config):
                    result_code = "play_source_not_allowed"
                else:
                    play_count = self._play_count(counters, content_id)
                    daily_limit = action_config.get("daily_limit")
                    if daily_limit is not None and play_count >= int(daily_limit):
                        result_code = "play_daily_limit"
                    elif self._inventory_quantity(inventory, item_id) < 1 and "inventory_item" in action_config:
                        result_code = "inventory_insufficient"
                    else:
                        energy_delta = float(action_config.get("energy_delta", 0))
                        if float(values.get("energy", 0)) + energy_delta < 0:
                            result_code = "energy_insufficient"
            else:
                result_code = "care_category_unavailable"

            if result_code is not None:
                # Time settlement is persisted; the rejected care operation
                # itself never consumes inventory or changes care values.
                if counters_changed or settlement_changed or task_refresh_changed:
                    self._write_snapshot(connection, snapshot, now)
                operation = {
                    "status": "rejected", "category": category, "content_id": content_id,
                    "idempotency_key": idempotency_key,
                }
                operation.update(event)
                result = {
                    "ok": False,
                    "operation": operation,
                    "code": result_code,
                    "snapshot": deepcopy(snapshot),
                    "settlement": deepcopy(self.snapshot_config.get("settlement", {})),
                }
                if is_playing:
                    result["play"] = self._play_action_summary(snapshot, content_id, action_config)
                connection.execute(
                    "INSERT INTO pet_care_operation_results "
                    "(account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (account_id, operation_result_key, action_hash, self._json(result), now),
                )
                connection.commit()
                return result

            if category == "feeding":
                self._inventory_decrement(inventory, item_id)
                values["satiety"] = self._clamp(float(values.get("satiety", 0)) + float(action_config.get("satiety_delta", 0)))
            elif category == "cleaning":
                values["cleanliness"] = self._clamp(float(values.get("cleanliness", 0)) + float(action_config.get("cleanliness_delta", 0)))
                cooldowns = snapshot.setdefault("cleaning_cooldowns", {})
                cooldowns[content_id] = now
            else:
                if "inventory_item" in action_config:
                    self._inventory_decrement(inventory, item_id)
                values["energy"] = self._clamp(float(values.get("energy", 0)) + float(action_config.get("energy_delta", 0)))
                values["cleanliness"] = self._clamp(float(values.get("cleanliness", 0)) + float(action_config.get("cleanliness_delta", 0)))
                playing = counters.setdefault("playing", {})
                playing[content_id] = self._play_count(counters, content_id) + 1
                daily_progress_limit = action_config.get("daily_progress_limit")
                progress_granted = daily_progress_limit is None or self._play_count(counters, content_id) <= int(daily_progress_limit)
                if progress_granted:
                    values["progress"] = self._clamp_progress(
                        float(values.get("progress", 0)) + float(action_config.get("progress_delta", 0))
                    )
                # A stage-5 task catalog may already be materialized. Keep
                # its independent activity-backed rows in the same confirmed
                # transaction as the play counter.
                self._sync_task_progress(snapshot)
                self._sync_task_aliases(snapshot)
                # A confirmed play of the configured sulk recovery item clears
                # the negative state.  The item/content id is configuration,
                # so a deployment may replace teaser_wand without leaving a
                # hard-coded recovery rule in the service.
                sulk = snapshot.get("negative_status", {}).get("sulk")
                recovery_config = self._recovery_task_config("sulk")
                recovery_item_id = str(
                    (sulk.get("required_item") if isinstance(sulk, dict) else "")
                    or recovery_config.get("recovery_item_id")
                    or ""
                ).strip()
                if (
                    isinstance(sulk, dict) and sulk.get("active") and recovery_item_id and
                    (content_id == recovery_item_id or item_id == recovery_item_id)
                ):
                    sulk.update({"active": False, "cleared_at": now, "cleared_by": recovery_item_id})
                    recovery_task_id = str(sulk.get("recovery_task_id") or "")
                    for task in snapshot.get("tasks", {}).get("items", []):
                        if isinstance(task, dict) and str(task.get("task_id", task.get("id"))) == recovery_task_id:
                            task["status"] = "completed"
                            task["resolved_at"] = now
                    self._sync_task_aliases(snapshot)
            if category != "playing":
                values["progress"] = self._clamp_progress(
                    float(values.get("progress", 0)) + float(action_config.get("progress_delta", 0))
                )
            self._apply_interaction_delta(snapshot, action_config.get("interaction_delta", 0), now)
            newly_unlocked = self._apply_unlocks(snapshot, now)
            self._write_snapshot(connection, snapshot, now)
            operation = {
                "status": "confirmed", "category": category, "content_id": content_id,
                "feedback_action": action_config.get("feedback_action"),
                "idempotency_key": idempotency_key,
            }
            operation.update(event)
            result = {
                "ok": True,
                "operation": operation,
                "snapshot": deepcopy(snapshot),
                "settlement": deepcopy(self.snapshot_config.get("settlement", {})),
                "unlocked": newly_unlocked,
            }
            if is_playing:
                result["play"] = self._play_action_summary(snapshot, content_id, action_config)
            connection.execute(
                "INSERT INTO pet_care_operation_results "
                "(account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, operation_result_key, action_hash, self._json(result), now),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def debug_fill_status(
        self,
        account_id: str,
        character_uid: str,
        *,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Fill the three basic care values for an explicitly enabled dev tool.

        This is a diagnostic operation, not a care action: it does not consume
        inventory, grant progress, change mood, complete tasks, or issue a
        reward.  It still uses the same account/role transaction and
        idempotency table as product operations so the browser cannot invent a
        successful result by changing its local snapshot.
        """
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 200):
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        request_hash = self._request_hash({
            "operation": "debug_fill_status",
            "fields": ["satiety", "energy", "cleanliness"],
        })
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            request_hash = self._scope_request_hash(request_hash, row)
            stored = connection.execute(
                "SELECT request_hash, result_json FROM pet_care_operation_results "
                "WHERE account_id = ? AND idempotency_key = ?",
                (account_id, idempotency_key),
            ).fetchone()
            if stored is not None:
                if str(stored["request_hash"]) != request_hash:
                    raise BindingServiceError("idempotency_key_reused", status_code=409)
                connection.commit()
                return self._stored_operation_result(stored["result_json"])

            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            exploration = snapshot.get("exploration")
            if self._exploration_active(exploration):
                raise BindingServiceError("activity_in_progress", status_code=409)
            now = self._current_time()
            settlement_changed = self._settle_snapshot(snapshot, now)
            values = snapshot.get("status_values")
            if not isinstance(values, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            before = {
                key: values.get(key) for key in ("satiety", "energy", "cleanliness")
            }
            for key in before:
                values[key] = self._clamp(100)
            if settlement_changed or any(values[key] != before[key] for key in before):
                self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {
                    "status": "confirmed",
                    "kind": "debug_fill_status",
                    "fields": list(before),
                    "idempotency_key": idempotency_key,
                },
                "snapshot": deepcopy(snapshot),
                "settlement": deepcopy(self.snapshot_config.get("settlement", {})),
            }
            connection.execute(
                "INSERT INTO pet_care_operation_results "
                "(account_id, idempotency_key, request_hash, result_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(result), now),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def get_tasks(
        self,
        account_id: str,
        character_uid: str,
        *,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Read and materialize the configured independent task records."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            now = self._current_time()
            changed = self._settle_snapshot(snapshot, now)
            changed = self._refresh_daily_tasks(snapshot, now) or changed
            if changed:
                self._write_snapshot(connection, snapshot, now)
            connection.commit()
            return {
                "account_id": account_id,
                "current_character_uid": character_uid,
                "binding_status": "bound",
                "pet_id": snapshot["pet_id"],
                "bound_role_id": snapshot["bound_role_id"],
                "service": "local",
                "tasks": deepcopy(snapshot["tasks"]),
                "snapshot": deepcopy(snapshot),
                **self.catalog_for_snapshot(snapshot),
            }
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def claim_task(
        self,
        account_id: str,
        character_uid: str,
        *,
        task_id: str,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Manually claim one available configured task and its item reward."""
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 200):
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        task_id = task_id.strip() if isinstance(task_id, str) else ""
        if not task_id:
            raise BindingServiceError("invalid_task_id", status_code=422)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            now = self._current_time()
            service_day = self._service_day(now)
            request_hash = self._request_hash({
                "operation": "task_claim", "task_id": task_id, "service_day": service_day,
            })
            request_hash = self._scope_request_hash(request_hash, row)
            stored = connection.execute(
                "SELECT request_hash, result_json FROM pet_care_operation_results WHERE account_id = ? AND idempotency_key = ?",
                (account_id, idempotency_key),
            ).fetchone()
            if stored is not None:
                if str(stored["request_hash"]) != request_hash:
                    raise BindingServiceError("idempotency_key_reused", status_code=409)
                connection.commit()
                return self._stored_operation_result(stored["result_json"])
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            self._settle_snapshot(snapshot, now)
            self._refresh_daily_tasks(snapshot, now)
            items = snapshot["tasks"].get("items", [])
            task = next((item for item in items if isinstance(item, Mapping) and
                         str(item.get("task_id", item.get("id"))) == task_id), None)
            if task is None:
                raise BindingServiceError("task_unavailable", status_code=404)
            if task.get("claimable", True) is False:
                raise BindingServiceError("task_unavailable", status_code=409)
            if task.get("status") in {"claimed", "completed"}:
                # Persist elapsed settlement and any service-day materialization
                # even when the requested reward is rejected as already claimed.
                self._write_snapshot(connection, snapshot, now)
                result = {
                    "ok": False,
                    "code": "task_already_claimed",
                    "operation": {"status": "rejected", "task_id": task_id, "idempotency_key": idempotency_key},
                    "snapshot": deepcopy(snapshot),
                    "reward": None,
                }
            else:
                # The stage-5 server has no condition/event evaluator yet.
                # Refuse a non-empty future condition instead of silently
                # treating a configured requirement as already complete.
                if task.get("condition"):
                    raise BindingServiceError("task_condition_unavailable", status_code=422)
                progress = task.get("progress")
                target = task.get("target")
                if progress is not None and target is not None:
                    try:
                        if float(progress) < float(target):
                            raise BindingServiceError("task_not_complete", status_code=409)
                    except (TypeError, ValueError):
                        raise BindingServiceError("task_progress_unavailable", status_code=422)
                reward = task.get("reward", {})
                item_id = str(reward.get("item_id", ""))
                quantity = int(reward.get("quantity", 0))
                if not item_id or quantity <= 0:
                    raise BindingServiceError("task_reward_unavailable", status_code=422)
                self._inventory_increment(snapshot["inventory"], item_id, quantity)
                task["status"] = "claimed"
                task["claimed_at"] = now
                # ``daily`` is kept as a compatibility alias for clients
                # that used the pre-stage-5 name; keep it in sync with the
                # canonical ``items`` list after JSON round-trips.
                for mirror in snapshot["tasks"].get("daily", []):
                    if isinstance(mirror, dict) and mirror.get("id") == task_id:
                        mirror["status"] = "claimed"
                        mirror["claimed_at"] = now
                        break
                self._write_snapshot(connection, snapshot, now)
                result = {
                    "ok": True,
                    "operation": {"status": "confirmed", "task_id": task_id, "idempotency_key": idempotency_key},
                    "snapshot": deepcopy(snapshot),
                    "reward": {"item_id": item_id, "quantity": quantity},
                    "unlocked": [],
                }
            connection.execute(
                "INSERT INTO pet_care_operation_results (account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(result), now),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def daily_check_in(
        self,
        account_id: str,
        character_uid: str,
        *,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Confirm the first login of the service day and apply configured progress."""
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 200):
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            now = self._current_time()
            service_day = self._service_day(now)
            request_hash = self._request_hash({
                "operation": "daily_check_in", "service_day": service_day,
            })
            request_hash = self._scope_request_hash(request_hash, row)
            stored = connection.execute(
                "SELECT request_hash, result_json FROM pet_care_operation_results WHERE account_id = ? AND idempotency_key = ?",
                (account_id, idempotency_key),
            ).fetchone()
            if stored is not None:
                if str(stored["request_hash"]) != request_hash:
                    raise BindingServiceError("idempotency_key_reused", status_code=409)
                connection.commit()
                return self._stored_operation_result(stored["result_json"])
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            self._settle_snapshot(snapshot, now)
            self._refresh_daily_tasks(snapshot, now)
            login_state = snapshot["tasks"].setdefault("daily_login", {"service_day": service_day, "checked": False})
            if login_state.get("service_day") != service_day:
                login_state.clear()
                login_state.update({"service_day": service_day, "checked": False})
            delta = 0
            if not login_state.get("checked"):
                configured_delta = int(self.snapshot_config.get("daily_login", {}).get("progress_delta", 0))
                previous_progress = float(snapshot["status_values"].get("progress", 0))
                updated_progress = self._clamp_progress(previous_progress + configured_delta)
                snapshot["status_values"]["progress"] = updated_progress
                delta = int(updated_progress - previous_progress)
                login_state["checked"] = True
                login_state["checked_at"] = now
            newly_unlocked = self._apply_unlocks(snapshot, now)
            self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {"status": "confirmed", "service_day": service_day, "idempotency_key": idempotency_key},
                "snapshot": deepcopy(snapshot),
                "progress_delta": delta,
                "unlocked": newly_unlocked,
            }
            connection.execute(
                "INSERT INTO pet_care_operation_results (account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(result), now),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def use_recovery_item(
        self,
        account_id: str,
        character_uid: str,
        *,
        status_key: str,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Consume a task-earned recovery item and clear one negative state."""
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        status_key = status_key.strip().lower() if isinstance(status_key, str) else ""
        if status_key not in {"sick", "sulk"}:
            raise BindingServiceError("recovery_status_unavailable", status_code=422)
        request_hash = self._request_hash({"operation": "use_recovery_item", "status": status_key})
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row is None or str(row["bound_character_uid"]) != character_uid:
                raise BindingServiceError("role_not_bound", status_code=409)
            self._assert_current_session(connection, account_id, session_context)
            request_hash = self._scope_request_hash(request_hash, row)
            stored = connection.execute(
                "SELECT request_hash, result_json FROM pet_care_operation_results WHERE account_id = ? AND idempotency_key = ?",
                (account_id, idempotency_key),
            ).fetchone()
            if stored is not None:
                if str(stored["request_hash"]) != request_hash:
                    raise BindingServiceError("idempotency_key_reused", status_code=409)
                connection.commit()
                return self._stored_operation_result(stored["result_json"])
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            now = self._current_time()
            settled = self._settle_snapshot(snapshot, now)
            negative = snapshot.get("negative_status")
            inventory = snapshot.get("inventory")
            if not isinstance(negative, dict) or not isinstance(inventory, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            state = negative.setdefault(status_key, {"active": False})
            if not isinstance(state, dict) or not state.get("active"):
                raise BindingServiceError("recovery_status_not_active", status_code=409)
            config = self._recovery_task_config(status_key)
            item_id = str(state.get("required_item") or config.get("recovery_item_id") or "").strip()
            quantity = max(1, int(config.get("recovery_item_quantity", 1) or 1))
            task_id = str(state.get("recovery_task_id") or config.get("task_id", f"recovery-{status_key}"))
            recovery_task = next(
                (
                    task for task in snapshot.get("tasks", {}).get("items", [])
                    if isinstance(task, dict) and str(task.get("task_id", task.get("id"))) == task_id
                ),
                None,
            )
            if not isinstance(recovery_task, dict) or recovery_task.get("status") != "claimed":
                raise BindingServiceError("recovery_task_not_claimed", status_code=409)
            if not item_id or self._inventory_quantity(inventory, item_id) < quantity:
                raise BindingServiceError("recovery_item_insufficient", status_code=409)
            if quantity == 1:
                self._inventory_decrement(inventory, item_id)
            else:
                for _ in range(quantity):
                    self._inventory_decrement(inventory, item_id)
            state.update({"active": False, "cleared_at": now, "cleared_by": item_id})
            tasks = snapshot.get("tasks", {})
            for task in tasks.get("items", []) if isinstance(tasks, dict) else []:
                if isinstance(task, dict) and str(task.get("task_id", task.get("id"))) == task_id:
                    task["status"] = "completed"
                    task["resolved_at"] = now
            self._sync_task_aliases(snapshot)
            self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {
                    "status": "confirmed",
                    "kind": "use_recovery_item",
                    "negative_status": status_key,
                    "item_id": item_id,
                    "idempotency_key": idempotency_key,
                },
                "snapshot": deepcopy(snapshot),
                "settled": settled,
            }
            connection.execute(
                "INSERT INTO pet_care_operation_results (account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(result), now),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def _activity_result_replay(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        stored = connection.execute(
            "SELECT request_hash, result_json FROM pet_care_operation_results "
            "WHERE account_id = ? AND idempotency_key = ?",
            (account_id, idempotency_key),
        ).fetchone()
        if stored is None:
            return None
        if str(stored["request_hash"]) != request_hash:
            raise BindingServiceError("idempotency_key_reused", status_code=409)
        return self._stored_operation_result(stored["result_json"])

    def _bound_snapshot_for_activity(
        self, connection: sqlite3.Connection, account_id: str, character_uid: str,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        row = connection.execute(
            "SELECT * FROM pet_care_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is None or str(row["bound_character_uid"]) != character_uid:
            raise BindingServiceError("role_not_bound", status_code=409)
        self._ensure_snapshot(connection, row)
        snapshot = self._read_snapshot(connection, row)
        if snapshot is None:
            raise BindingServiceError("pet_care_snapshot_unavailable")
        return row, snapshot

    def _activity_response(
        self, account_id: str, character_uid: str, snapshot: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        exploration = self._public_exploration_activity(
            result.get("exploration", snapshot.get("exploration"))
        )
        public_snapshot = self._public_exploration_snapshot(snapshot)
        public_result = deepcopy(dict(result))
        if "exploration" in public_result:
            public_result["exploration"] = self._public_exploration_activity(public_result.get("exploration"))
        if "activity" in public_result:
            public_result["activity"] = self._public_exploration_activity(public_result.get("activity"))
        if "snapshot" in public_result and isinstance(public_result.get("snapshot"), Mapping):
            public_result["snapshot"] = self._public_exploration_snapshot(public_result["snapshot"])
        public_letters = public_result.get("letters")
        if isinstance(exploration, Mapping) and self._exploration_active(exploration):
            public_letters = exploration.get("letters_revealed", [])
            public_result["letters"] = deepcopy(public_letters)
        return {
            "account_id": account_id,
            "current_character_uid": character_uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id"),
            "bound_role_id": snapshot.get("bound_role_id"),
            "service": "local",
            "locations": self._public_exploration_locations(),
            "exploration": exploration,
            "activity": deepcopy(exploration),
            "snapshot": public_snapshot,
            "settlement": deepcopy(self.snapshot_config.get("settlement", {})),
            "operation": deepcopy(result.get("operation")),
            "result": public_result,
            "rewards": deepcopy(result.get("rewards")),
            "reward": deepcopy(result.get("reward", result.get("rewards"))),
            "letters": deepcopy(public_letters),
        }

    def start_exploration(
        self,
        account_id: str,
        character_uid: str,
        *,
        location_id: str | None = None,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Create one server-owned exploration activity atomically."""
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        if location_id is not None and not isinstance(location_id, str):
            raise BindingServiceError("invalid_location_id", status_code=422)
        location_id = location_id.strip() if isinstance(location_id, str) else None
        request_hash = self._request_hash({"operation": "exploration_start", "location_id": location_id})
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Validate the account's currently bound role before replaying an
            # idempotent result. Otherwise a key from a previous role could
            # return that role's snapshot to the newly bound context.
            row, snapshot = self._bound_snapshot_for_activity(connection, account_id, character_uid)
            self._assert_current_session(connection, account_id, session_context)
            request_hash = self._scope_request_hash(request_hash, row)
            replay = self._activity_result_replay(connection, account_id, idempotency_key, request_hash)
            if replay is not None:
                connection.commit()
                return replay
            now = self._current_time()
            changed = self._settle_snapshot(snapshot, now)
            current = snapshot.get("exploration")
            if self._exploration_active(current):
                raise BindingServiceError("activity_in_progress", status_code=409)
            location = self._configured_exploration_location(location_id)
            if location is None:
                raise BindingServiceError("exploration_location_unavailable", status_code=404)
            location_id = str(location.get("id", location.get("location_id")))
            negative = snapshot.get("negative_status", {})
            sick = negative.get("sick") if isinstance(negative, Mapping) else None
            sick_active = bool(sick.get("active")) if isinstance(sick, Mapping) else bool(sick)
            if sick_active:
                raise BindingServiceError("exploration_blocked_by_status", status_code=409)
            values = snapshot.get("status_values")
            if not isinstance(values, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            costs = self._exploration_costs(location)
            for key, cost in costs.items():
                if float(values.get(key, 0)) < cost:
                    raise BindingServiceError(f"{key}_insufficient", status_code=409)
            seed = secrets.token_hex(16)
            exploration_id = self._new_id()
            duration_seconds = self._exploration_duration_seconds(location)
            mood_snapshot = float(values.get("mood", 0))
            selected_letters = self._exploration_letters(location, seed)
            for key, cost in costs.items():
                values[key] = self._clamp(float(values.get(key, 0)) - cost)
            # Departure mood is captured before costs above; the persisted
            # home snapshot still reflects the post-departure status values.
            self._recompute_mood(snapshot)
            location_payload = {"id": location_id, "name": location.get("name", location_id)}
            for key in ("description", "asset_url", "image", "image_url", "letter_image", "letter_image_url"):
                if key in location:
                    location_payload[key] = deepcopy(location[key])
            activity = {
                "active": True,
                "status": "away",
                "exploration_id": exploration_id,
                "location_id": location_id,
                "location": location_payload,
                "note": location.get("note", location.get("away_note", "")),
                "started_at": now,
                "duration_seconds": duration_seconds,
                "mood_at_departure": self._clamp(mood_snapshot),
                "costs": {key: int(value) if float(value).is_integer() else value for key, value in costs.items()},
                "random_seed": seed,
                "letters": selected_letters,
                "letters_revealed": [],
                "rewards": None,
                "accepted_at": None,
            }
            snapshot["exploration"] = activity
            self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {"status": "confirmed", "kind": "start", "exploration_id": exploration_id, "idempotency_key": idempotency_key},
                "exploration": deepcopy(activity),
                "snapshot": deepcopy(snapshot),
                "rewards": None,
                "reward": None,
                "letters": [],
            }
            envelope = self._activity_response(account_id, character_uid, snapshot, result)
            connection.execute(
                "INSERT INTO pet_care_operation_results "
                "(account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(envelope), now),
            )
            connection.commit()
            return envelope
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def get_exploration(
        self,
        account_id: str,
        character_uid: str,
        *,
        exploration_id: str | None = None,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Read the current activity; no client-side settlement is performed."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row, snapshot = self._bound_snapshot_for_activity(connection, account_id, character_uid)
            self._assert_current_session(connection, account_id, session_context)
            now = self._current_time()
            changed = self._settle_snapshot(snapshot, now)
            activity = snapshot.get("exploration")
            if activity is not None and not isinstance(activity, Mapping):
                raise BindingServiceError("pet_care_snapshot_invalid")
            if exploration_id and (not isinstance(activity, Mapping) or self._exploration_id(activity) != exploration_id):
                raise BindingServiceError("exploration_not_found", status_code=404)
            if isinstance(activity, Mapping):
                activity = deepcopy(dict(activity))
                ratio = self._exploration_ratio(activity, now) if self._exploration_active(activity) else float(activity.get("completion_ratio", 0) or 0)
                activity["completion_ratio"] = round(ratio, 6)
                if self._exploration_active(activity):
                    activity["letters_revealed"] = deepcopy([
                        letter for letter in activity.get("letters", [])
                        if isinstance(letter, Mapping)
                        and float(letter.get("available_ratio", 1.0) or 0) <= ratio
                    ])
                    activity["letters_available"] = len(activity["letters_revealed"])
                else:
                    activity["letters_revealed"] = deepcopy(activity.get("letters", []))
                    activity["letters_available"] = len(activity["letters_revealed"])
                if snapshot.get("exploration") != activity:
                    snapshot["exploration"] = activity
                    changed = True
            if changed:
                self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {"status": "confirmed", "kind": "status", "exploration_id": self._exploration_id(activity) if isinstance(activity, Mapping) else None},
                "exploration": activity,
                "snapshot": deepcopy(snapshot),
                "rewards": activity.get("rewards") if isinstance(activity, Mapping) else None,
                "reward": activity.get("rewards") if isinstance(activity, Mapping) else None,
                "letters": activity.get("letters_revealed", []) if isinstance(activity, Mapping) else [],
            }
            connection.commit()
            return self._activity_response(account_id, character_uid, snapshot, result)
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def _settle_exploration(
        self,
        account_id: str,
        character_uid: str,
        *,
        exploration_id: str,
        idempotency_key: str,
        kind: str,
        require_complete: bool,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise BindingServiceError("invalid_idempotency_key", status_code=422)
        if not isinstance(exploration_id, str) or not exploration_id.strip():
            raise BindingServiceError("invalid_exploration_id", status_code=422)
        request_hash = self._request_hash({"operation": f"exploration_{kind}", "exploration_id": exploration_id})
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Bind validation must precede replay so an idempotency key cannot
            # disclose a previous role's activity envelope after rebinding.
            row, snapshot = self._bound_snapshot_for_activity(connection, account_id, character_uid)
            self._assert_current_session(connection, account_id, session_context)
            request_hash = self._scope_request_hash(request_hash, row)
            replay = self._activity_result_replay(connection, account_id, idempotency_key, request_hash)
            if replay is not None:
                connection.commit()
                return replay
            now = self._current_time()
            # Interaction recency continues to decay while the cat is away;
            # home-state value recovery remains frozen by _settle_snapshot.
            self._settle_snapshot(snapshot, now)
            activity = snapshot.get("exploration")
            if not isinstance(activity, Mapping) or self._exploration_id(activity) != exploration_id:
                raise BindingServiceError("exploration_not_found", status_code=404)
            activity = dict(activity)
            if not self._exploration_active(activity):
                # Completed/recall records remain queryable but cannot settle
                # twice with a new key. Callers can replay their original key.
                raise BindingServiceError("exploration_already_settled", status_code=409)
            ratio = self._exploration_ratio(activity, now)
            if require_complete and ratio < 1.0:
                raise BindingServiceError("exploration_not_complete", status_code=409)
            location = self._configured_exploration_location(str(activity.get("location_id"))) or {}
            completed = require_complete
            reward = self._exploration_reward(
                location,
                str(activity.get("random_seed", exploration_id)),
                ratio,
                completed=completed,
                mood_at_departure=float(activity.get("mood_at_departure", 100)),
            )
            values = snapshot.get("status_values")
            inventory = snapshot.get("inventory")
            if not isinstance(values, dict) or not isinstance(inventory, dict):
                raise BindingServiceError("pet_care_snapshot_invalid")
            values["progress"] = self._clamp_progress(float(values.get("progress", 0)) + float(reward.get("progress_delta", 0)))
            for drop in reward.get("drops", []):
                self._inventory_increment(inventory, str(drop.get("item_id")), int(drop.get("quantity", 0)))
            newly_unlocked = self._apply_unlocks(snapshot, now)
            letters = deepcopy(activity.get("letters", []))
            activity["active"] = False
            activity["status"] = "completed" if completed else "recalled"
            activity["completion_ratio"] = round(ratio, 6)
            activity["accepted_at"] = now
            activity["completed_at"] = now
            activity["rewards"] = reward
            activity["letters_revealed"] = letters
            activity["letters_available"] = len(letters)
            snapshot["exploration"] = activity
            # Moving to the terminal state before writing means later home
            # settlement resumes from this service-accepted timestamp.
            snapshot["last_settled_at"] = now
            self._write_snapshot(connection, snapshot, now)
            result = {
                "ok": True,
                "operation": {"status": "confirmed", "kind": kind, "exploration_id": exploration_id, "idempotency_key": idempotency_key},
                "exploration": deepcopy(activity),
                "snapshot": deepcopy(snapshot),
                "rewards": deepcopy(reward),
                "reward": deepcopy(reward),
                "letters": letters,
                "unlocked": newly_unlocked,
            }
            envelope = self._activity_response(account_id, character_uid, snapshot, result)
            connection.execute(
                "INSERT INTO pet_care_operation_results "
                "(account_id, idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, idempotency_key, request_hash, self._json(envelope), now),
            )
            connection.commit()
            return envelope
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()

    def complete_exploration(
        self,
        account_id: str,
        character_uid: str,
        *,
        exploration_id: str,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        return self._settle_exploration(
            account_id,
            character_uid,
            exploration_id=exploration_id,
            idempotency_key=idempotency_key,
            kind="complete",
            require_complete=True,
            session_context=session_context,
        )

    def recall_exploration(
        self,
        account_id: str,
        character_uid: str,
        *,
        exploration_id: str,
        idempotency_key: str,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> dict[str, Any]:
        return self._settle_exploration(
            account_id,
            character_uid,
            exploration_id=exploration_id,
            idempotency_key=idempotency_key,
            kind="recall",
            require_complete=False,
            session_context=session_context,
        )

    def bind(
        self,
        account_id: str,
        character_uid: str,
        *,
        session_context: Mapping[str, Any] | Any | None = None,
    ) -> BindingContext:
        """Explicitly bind the account's existing pet to this role.

        Rebinding updates the same pet row and creates a fresh server role id;
        it never inserts a second account pet. Repeating the same request is
        idempotent and returns the existing role id.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if session_context is not None and row is not None:
                self._assert_bind_session(connection, account_id, session_context)
            now = self._current_time()
            created = row is None
            if row is None:
                connection.execute(
                    """
                    INSERT INTO pet_care_accounts
                      (account_id, pet_id, bound_role_id, bound_character_uid,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        self._new_id(),
                        self._new_id(),
                        character_uid,
                        now,
                        now,
                    ),
                )
            elif str(row["bound_character_uid"]) != character_uid:
                # A role switch must never carry an in-flight activity into
                # the newly bound role.  The model/role exit coordinator
                # normally recalls first, but the service remains the final
                # authority for direct callers and races.  Rejecting the
                # rebind keeps the existing activity and binding atomic so a
                # caller can retry after the same activity has been
                # service-confirmed as recalled or completed.
                self._ensure_snapshot(connection, row)
                existing_snapshot = self._read_snapshot(connection, row)
                if existing_snapshot is None:
                    raise BindingServiceError("pet_care_snapshot_unavailable")
                if self._exploration_active(existing_snapshot.get("exploration")):
                    raise BindingServiceError(
                        "binding_blocked_by_active_exploration", status_code=409
                    )
                new_role_id = self._new_id()
                connection.execute(
                    """
                    UPDATE pet_care_accounts
                    SET bound_role_id = ?, bound_character_uid = ?, updated_at = ?
                    WHERE account_id = ?
                    """,
                    (new_role_id, character_uid, now, account_id),
                )
                connection.execute(
                    """
                    UPDATE pet_care_snapshots
                    SET bound_role_id = ?, updated_at = ?
                    WHERE pet_id = ?
                    """,
                    (new_role_id, now, row["pet_id"]),
                )
            row = connection.execute(
                "SELECT * FROM pet_care_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            self._ensure_snapshot(connection, row)
            snapshot = self._read_snapshot(connection, row)
            if snapshot is None:
                raise BindingServiceError("pet_care_snapshot_unavailable")
            settled = self._settle_snapshot(snapshot, now)
            _, counters_changed = self._prepare_activity_counters(snapshot, now)
            unlocked = bool(self._apply_unlocks(snapshot, now))
            reconciliation = None
            reconcile_changed = False
            if session_context is not None:
                reconciliation, reconcile_changed = self._reconcile_session_in_transaction(
                    connection, row, snapshot, session_context, now,
                )
            if settled or counters_changed or unlocked or reconcile_changed:
                self._write_snapshot(connection, snapshot, now)
            result = self._context_from_row(
                row, account_id, character_uid, created=created, snapshot=snapshot,
                settlement=self.snapshot_config.get("settlement", {}),
                reconciliation=reconciliation,
                care_actions=self.snapshot_config.get("care_actions", {}),
                play_actions=self._play_actions_for_snapshot(snapshot or {}),
            )
            connection.commit()
            return result
        except BindingServiceError:
            connection.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            connection.rollback()
            raise BindingServiceError("pet_care_storage_unavailable") from exc
        finally:
            connection.close()
