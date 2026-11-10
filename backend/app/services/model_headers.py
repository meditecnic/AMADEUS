"""AMADEUS identification for outbound model HTTP requests."""
import os


def model_headers() -> dict[str, str]:
    return {"User-Agent": f"AMADEUS/{os.environ.get('AMADEUS_APP_VERSION') or 'dev'}"}
