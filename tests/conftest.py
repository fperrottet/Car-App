import pytest

from app import sync


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Aucun test ne doit sortir sur le réseau : les envois vers HA sont notés."""
    posted = []
    monkeypatch.setattr(sync, "post_json", lambda url, payload, token=None, timeout=20: posted.append((url, payload)))

    def offline(url, token=None, timeout=20, accept="*/*"):
        raise OSError(f"hors ligne : {url}")

    monkeypatch.setattr(sync, "fetch_text", offline)
    return posted
