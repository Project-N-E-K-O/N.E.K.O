"""Test log-export route security"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from plugin.server.routes.logs import export_plugin_log_endpoint


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_export_rejects_newline_in_plugin_id():
    """Reject plugin_id values containing newlines to prevent HTTP response-header injection"""
    # Test \n
    with pytest.raises(HTTPException) as exc_info:
        await export_plugin_log_endpoint("foo\n", _="test-admin")
    assert exc_info.value.status_code == 400
    assert "newline" in str(exc_info.value.detail).lower()

    # Test \r
    with pytest.raises(HTTPException) as exc_info:
        await export_plugin_log_endpoint("foo\r", _="test-admin")
    assert exc_info.value.status_code == 400
    assert "newline" in str(exc_info.value.detail).lower()

    # Test \r\n
    with pytest.raises(HTTPException) as exc_info:
        await export_plugin_log_endpoint("foo\r\n", _="test-admin")
    assert exc_info.value.status_code == 400
    assert "newline" in str(exc_info.value.detail).lower()
