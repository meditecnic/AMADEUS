"""AMADEUS identification for outbound model HTTP requests."""
import os
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit


def model_headers(base_url: str = "") -> dict[str, str]:
    headers = {"User-Agent": f"AMADEUS/{os.environ.get('AMADEUS_APP_VERSION') or 'dev'}"}
    target = urlsplit(base_url)
    path = target.path.rstrip("/")
    if target.hostname == "opencode.ai" and (path == "/inference/go" or path.startswith("/inference/go/")):
        from app.services.model_usage import current_conversation_id
        conversation = current_conversation_id()
        if conversation:
            from app.db import get_data_root
            salt_path = get_data_root() / "model-session.salt"
            salt_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with salt_path.open("xb") as handle:
                    handle.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
            value = hmac.new(salt_path.read_bytes(), conversation.encode("utf-8"), hashlib.sha256).hexdigest()
        else:
            value = secrets.token_hex(32)
        headers["x-opencode-session"] = value
    return headers
