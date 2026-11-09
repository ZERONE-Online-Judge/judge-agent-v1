import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from app.backend_client import BackendClient, BackendError


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

    with pytest.raises(BackendError, match="채점 서버에 연결할 수 없습니다"):
        client.claim("node-1", 1)


def test_request_wraps_url_error_as_runtime_error(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(request, timeout):
        raise URLError("temporary failure")

    monkeypatch.setattr("app.backend_client.urlopen", fake_urlopen)

    client = BackendClient()

    with pytest.raises(BackendError, match="채점 서버에 연결할 수 없습니다"):
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


@pytest.mark.parametrize('editor', [False, True])
def test_structured_errors_keep_safe_diagnostics_without_raw_response_or_credentials(monkeypatch, editor):
    from app.settings import settings
    body = {'error': {'code': 'testcase_results_invalid', 'request_id': 'request-123',
                      'message': 'raw ' + settings.node_secret + ' secret-lease',
                      'details': {'source_code': 'PRIVATE_SOURCE', 'node_secret': settings.node_secret}}}
    def fail(*args, **kwargs):
        raise HTTPError('https://backend/result', 422, 'Unprocessable', {}, BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr('app.backend_client.urlopen', fail)
    with pytest.raises(BackendError) as caught:
        if editor:
            BackendClient().report_editor_result('task', 'secret-lease', result={'status': 'ok'})
        else:
            BackendClient().report_result('job', 'secret-lease', 'accepted', None, None, None)
    error = caught.value
    assert (error.status, error.code, error.request_id) == (422, 'testcase_results_invalid', 'request-123')
    assert error.permanent and not error.lease_lost
    assert '증거 검증에 실패' in error.message and 'request-123' in str(error)
    assert all(secret not in str(vars(error)) for secret in [settings.node_secret, 'secret-lease', 'PRIVATE_SOURCE'])


@pytest.mark.parametrize('body', [b'<html>PRIVATE_SOURCE secret-lease</html>', b'not json', b'[]', b'{"error": "PRIVATE_SOURCE"}'])
def test_unstructured_http_errors_do_not_leak_the_response(monkeypatch, body):
    monkeypatch.setattr('app.backend_client.urlopen', lambda *a, **kw: (_ for _ in ()).throw(
        HTTPError('https://backend/result', 503, 'Unavailable', {}, BytesIO(body))))
    with pytest.raises(BackendError) as caught:
        BackendClient().report_result('job', 'secret-lease', 'accepted', None, None, None)
    assert caught.value.status == 503 and not caught.value.permanent
    assert 'PRIVATE_SOURCE' not in str(caught.value) and 'secret-lease' not in str(caught.value)


def test_transport_failure_does_not_echo_urls_or_tokens(monkeypatch):
    monkeypatch.setattr('app.backend_client.urlopen', lambda *a, **kw: (_ for _ in ()).throw(
        URLError('https://backend/?token=PRIVATE_TOKEN')))
    with pytest.raises(BackendError) as caught:
        BackendClient().claim('node', 1)
    assert caught.value.code == 'backend_unavailable' and not caught.value.permanent
    assert 'PRIVATE_TOKEN' not in str(caught.value)
