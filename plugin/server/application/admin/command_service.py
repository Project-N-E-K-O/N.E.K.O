from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from plugin._types.models import RunCreateRequest
from plugin.logging_config import get_logger
from plugin.server.application.plugins import PluginLifecycleService
from plugin.server.application.runs import RunService
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.admin.command")


class AdminCommandService:
    def __init__(
        self,
        *,
        run_service: RunService | None = None,
        lifecycle_service: PluginLifecycleService | None = None,
    ) -> None:
        self._run_service = run_service if run_service is not None else RunService()
        self._lifecycle_service = lifecycle_service if lifecycle_service is not None else PluginLifecycleService()

    @staticmethod
    def _to_domain_error(*, code: str, message: str, status_code: int) -> ServerDomainError:
        return ServerDomainError(code=code, message=message, status_code=status_code, details={})

    def _bad_request(self, message: str, *, code: str = "INVALID_PARAMS") -> ServerDomainError:
        return self._to_domain_error(code=code, message=message, status_code=400)

    def _require_non_empty_str(self, params: dict[str, object], key: str) -> str:
        raw_value = params.get(key)
        if not isinstance(raw_value, str):
            raise self._bad_request(f"{key} required")
        value = raw_value.strip()
        if not value:
            raise self._bad_request(f"{key} required")
        return value

    @staticmethod
    def _optional_non_empty_str(params: dict[str, object], key: str) -> str | None:
        raw_value = params.get(key)
        if not isinstance(raw_value, str):
            return None
        value = raw_value.strip()
        if not value:
            return None
        return value

    def _coerce_limit(self, value: object, *, default: int, min_value: int, max_value: int) -> int:
        if value is None:
            return default

        if isinstance(value, bool):
            raise self._bad_request("limit must be integer")

        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise self._bad_request("limit must be integer") from exc

        if parsed < min_value:
            return min_value
        if parsed > max_value:
            return max_value
        return parsed

    def _read_args(self, params: dict[str, object]) -> dict[str, object]:
        raw_args = params.get("args")
        if raw_args is None:
            return {}
        if not isinstance(raw_args, Mapping):
            raise self._bad_request("args must be object")

        normalized: dict[str, object] = {}
        for key, value in raw_args.items():
            if not isinstance(key, str):
                raise self._bad_request("args keys must be string")
            normalized[key] = value
        return normalized

    def _read_params_mapping(self, raw_params: object) -> dict[str, object]:
        if raw_params is None:
            return {}
        if not isinstance(raw_params, Mapping):
            raise self._bad_request("invalid params")

        normalized: dict[str, object] = {}
        for key, value in raw_params.items():
            if not isinstance(key, str):
                raise self._bad_request("params keys must be string")
            normalized[key] = value
        return normalized

    async def execute(self, *, method: str, raw_params: object) -> object:
        params = self._read_params_mapping(raw_params)

        if method == "runs.list":
            plugin_id = self._optional_non_empty_str(params, "plugin_id")
            runs = self._run_service.list_runs(plugin_id=plugin_id)
            return [run.model_dump() for run in runs]

        if method == "run.get":
            run_id = self._require_non_empty_str(params, "run_id")
            run_record = self._run_service.get_run(run_id)
            return run_record.model_dump()

        if method == "export.list":
            run_id = self._require_non_empty_str(params, "run_id")
            after = self._optional_non_empty_str(params, "after")
            limit = self._coerce_limit(params.get("limit", 200), default=200, min_value=1, max_value=500)
            response = self._run_service.list_export_for_run(run_id=run_id, after=after, limit=limit)
            return response.model_dump(by_alias=True)

        if method == "run.create":
            plugin_id = self._require_non_empty_str(params, "plugin_id")
            entry_id = self._require_non_empty_str(params, "entry_id")
            args = self._read_args(params)
            try:
                request = RunCreateRequest(plugin_id=plugin_id, entry_id=entry_id, args=args)
            except ValidationError as exc:
                logger.warning(
                    "invalid run.create payload: plugin_id={}, entry_id={}, err={}",
                    plugin_id,
                    entry_id,
                    str(exc),
                )
                raise self._bad_request("invalid run create payload") from exc

            response = await self._run_service.create_run(request, client_host=None)
            return response.model_dump()

        if method == "run.cancel":
            run_id = self._require_non_empty_str(params, "run_id")
            reason = self._optional_non_empty_str(params, "reason")
            canceled = self._run_service.cancel_run(run_id, reason=reason)
            return canceled.model_dump()

        if method == "plugin.stop":
            plugin_id = self._require_non_empty_str(params, "plugin_id")
            return await self._lifecycle_service.stop_plugin(plugin_id)

        raise self._bad_request("unknown method", code="UNKNOWN_METHOD")
