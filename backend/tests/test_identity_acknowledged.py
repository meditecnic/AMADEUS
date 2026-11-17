"""Compatibility of the persisted identity flag for existing databases."""

import pytest

from app.services.conversations import conversation_service
from test_working_summary import isolated_store  # noqa: F401


@pytest.mark.asyncio
async def test_new_conversation_starts_false_and_mark_is_persistent(isolated_store):
    session_id = "ack-persist"
    created = await conversation_service.create_and_select(
        session_id, "steins_gate"
    )
    assert created["identity_acknowledged"] is False

    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(created["id"])
    )
    reloaded = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    assert reloaded["identity_acknowledged"] is True
    selected = await conversation_service.get_selected(session_id, "steins_gate")
    assert selected["identity_acknowledged"] is True


@pytest.mark.asyncio
async def test_mark_only_touches_the_flag_column(isolated_store):
    session_id = "ack-column"
    created = await conversation_service.create_and_select(session_id, "steins_gate")
    before = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(created["id"])
    )
    after = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    changed = {
        key for key in before
        if before[key] != after[key]
    }
    assert changed == {"identity_acknowledged"}


@pytest.mark.asyncio
async def test_state_never_leaks_across_conversations_or_worldlines(isolated_store):
    session_id = "ack-isolation"
    sg_one = await conversation_service.create_and_select(session_id, "steins_gate")
    sg_two = await conversation_service.create(session_id, "steins_gate")
    beta_one = await conversation_service.create_and_select(session_id, "beta")

    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(sg_one["id"])
    )

    assert (await conversation_service.require_owned(
        str(sg_one["id"]), session_id, "steins_gate"
    ))["identity_acknowledged"] is True
    assert (await conversation_service.require_owned(
        str(sg_two["id"]), session_id, "steins_gate"
    ))["identity_acknowledged"] is False
    assert (await conversation_service.require_owned(
        str(beta_one["id"]), session_id, "beta"
    ))["identity_acknowledged"] is False
