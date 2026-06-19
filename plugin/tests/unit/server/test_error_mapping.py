from __future__ import annotations

import pytest
from fastapi import HTTPException

from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


def test_raise_http_from_domain_keeps_localized_detail_and_error_code_header() -> None:
    error = ServerDomainError(
        code="PLUGIN_NOT_RUNNING",
        message="插件未运行",
        status_code=409,
        details={"plugin_id": "demo"},
    )

    with pytest.raises(HTTPException) as exc_info:
        raise_http_from_domain(error, logger=_Logger())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "插件未运行"
    assert exc_info.value.headers == {"X-Error-Code": "PLUGIN_NOT_RUNNING"}
