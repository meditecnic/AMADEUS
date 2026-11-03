from uuid import uuid4

import pytest


@pytest.mark.timeout(10)
def test_non_object_json_recovers(app_client):
    with app_client.websocket_connect(f"/ws/chat?session_id=json182-{uuid4()}") as ws:
        ws.send_json({"type": "auth", "enable_tts": False})
        assert ws.receive_json()["type"] == "session.ready"
        for value in ([], 1, "x"):
            ws.send_json(value)
            assert ws.receive_json() == {"type": "error", "message": "数据格式无效"}
        ws.send_json({"type": "auth"})
        assert ws.receive_json() == {"type": "error", "message": "已完成认证，请勿重复操作"}


