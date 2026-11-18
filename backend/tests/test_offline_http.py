from unittest.mock import AsyncMock, Mock

import httpx
import pytest


def test_external_sync_http_is_blocked_before_connection(monkeypatch):
    with httpx.HTTPTransport() as transport:
        network = Mock(side_effect=AssertionError("network reached"))
        monkeypatch.setattr(transport._pool, "handle_request", network)
        with pytest.raises(pytest.fail.Exception, match="external HTTP forbidden"):
            transport.handle_request(httpx.Request("GET", "https://offline-check.invalid"))
        network.assert_not_called()


async def test_external_async_http_requires_a_mock_transport(monkeypatch):
    url = "https://offline-check.invalid"
    async with httpx.AsyncHTTPTransport() as transport:
        network = AsyncMock(side_effect=AssertionError("network reached"))
        monkeypatch.setattr(transport._pool, "handle_async_request", network)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(pytest.fail.Exception, match="external HTTP forbidden"):
                await client.get(url)
        network.assert_not_awaited()

    mock = httpx.MockTransport(lambda request: httpx.Response(204))
    async with httpx.AsyncClient(transport=mock) as client:
        assert (await client.get(url)).status_code == 204
