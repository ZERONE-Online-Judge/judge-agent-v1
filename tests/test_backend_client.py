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


def test_editor_result_uses_encoded_credentials_and_a_separate_endpoint(monkeypatch):
    import base64
    from app.settings import settings
    def respond(request,timeout):
        assert request.full_url.endswith('/internal/judge/editor-tasks/task/result')
        credential=request.get_header('X-judge-node-credential')
        assert base64.urlsafe_b64decode(credential).decode()==settings.node_secret
        assert request.get_header('X-judge-lease')=='lease'
        assert json.loads(request.data)=={'result':{'status':'ok'}}
        return _FakeResponse({'data':{'accepted':True}})
    monkeypatch.setattr('app.backend_client.urlopen',respond)
    assert BackendClient().report_editor_result('task','lease',result={'status':'ok'})['data']['accepted']


def test_full_verification_case_batches_and_final_outputs_use_bounded_upload_timeouts(monkeypatch):
    requests = []
    def respond(request, timeout):
        requests.append((request.full_url, json.loads(request.data), timeout))
        return _FakeResponse({'data': {'accepted': True}})
    monkeypatch.setattr('app.backend_client.urlopen', respond)
    client = BackendClient()
    cases = [{'index': 1, 'final_status': 'wrong_answer', 'output_base64': 'NQo='}]
    client.report_progress('run', 'lease', 'judging', 1, 2, testcase_results=cases)
    client.report_result('run', 'lease', 'wrong_answer', None, None, 1, testcase_results=cases)
    assert all(body['testcase_results'] == cases and timeout == 120 for _, body, timeout in requests)
    client.report_progress('contest-run', 'lease', 'judging', 1, 2)
    assert 'testcase_results' not in requests[-1][1] and requests[-1][2] == 10
