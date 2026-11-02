from unittest.mock import AsyncMock

from app.services.session_manager import SessionCoordinator


async def test_same_worldline_replaces_connection_without_closing_itself():
    coordinator = SessionCoordinator()
    old, new = AsyncMock(), AsyncMock()
    await coordinator.acquire_lease("same", "steins_gate", old)
    await coordinator.acquire_lease("same", "steins_gate", old)
    old.close.assert_not_awaited()
    await coordinator.acquire_lease("same", "steins_gate", new)
    old.close.assert_awaited_once_with(code=4409, reason="session connection replaced")
    await coordinator.release_lease("same", "steins_gate", old)
    assert coordinator._leases[("same", "steins_gate")] is new
    await coordinator.acquire_lease("same", "steins_gate", new)
    new.close.assert_not_awaited()
