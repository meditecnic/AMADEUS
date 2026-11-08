import logging

from app.routers import chat_ws
from app.security.log_privacy import configure_logging
from test_language_gate_publication_retire04 import _SequencedStreamProvider


def test_server_tracebacks_and_client_urls_do_not_reach_logs(caplog):
    caplog.set_level(logging.DEBUG)
    configure_logging()
    logger = logging.getLogger("uvicorn.error")
    secret = "private-216-error-content"
    try:
        raise ValueError(secret)
    except ValueError:
        logger.exception("Exception in ASGI application")
    logger.error("Traceback\n" + secret)
    logging.getLogger("httpx").info("POST https://example.invalid/" + secret)
    logging.getLogger("aiosqlite").debug("SQL parameter %s", secret)
    assert secret not in caplog.text
    assert "ValueError" in caplog.text and "startup" in caplog.text


async def test_rejected_model_response_and_memory_body_are_not_logged(monkeypatch, capsys, caplog):
    private = "The weather in my private diary is warm today."
    memory = "private-memory-body-216"
    provider = _SequencedStreamProvider([private, "今日は暖かいわね。"])
    session = chat_ws.SessionState("log-privacy-rejected-output")
    session.api_key = "test-216-fake-key"
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    await chat_ws.get_clean_text_stream(
        active_history=[{"role": "user", "content": "private-user-body-216"}],
        memory_summary=memory, session=session,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    assert "high-confidence foreign" in output
    for value in (private, private[:30], memory, session.api_key, "private-user-body-216"):
        assert value not in output
