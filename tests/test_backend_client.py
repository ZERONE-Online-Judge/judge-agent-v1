import json
from urllib.error import URLError

import pytest

from app.backend_client import BackendClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_claim_uses_timeout_longer_than_long_poll(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, float] = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        return _FakeResponse({"data": {"jobs": []}})

    monkeypatch.setattr("app.backend_client.urlopen", fake_urlopen)

    client = BackendClient()
    jobs = client.claim("node-1", 2)

    assert jobs == []
    assert captured["timeout"] == 35.0


def test_request_wraps_timeout_as_runtime_error(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("read timed out")

    monkeypatch.setattr("app.backend_client.urlopen", fake_urlopen)

    client = BackendClient()

    with pytest.raises(RuntimeError, match="backend unavailable"):
        client.claim("node-1", 1)


def test_request_wraps_url_error_as_runtime_error(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(request, timeout):
        raise URLError("temporary failure")

    monkeypatch.setattr("app.backend_client.urlopen", fake_urlopen)

    client = BackendClient()

    with pytest.raises(RuntimeError, match="backend unavailable"):
        client.register_node()
