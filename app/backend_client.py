from __future__ import annotations

import base64
import json
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import settings


class BackendError(RuntimeError):
    """A safe backend failure; response bodies and credentials are not retained."""

    def __init__(self, status: int | None, code: str, message: str, request_id: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(message + (f" (요청 ID: {request_id})" if request_id else ""))

    @property
    def lease_lost(self) -> bool:
        return self.status in {403, 404} or (self.status == 409 and self.code == "lease_conflict")

    @property
    def permanent(self) -> bool:
        return self.status is not None and 400 <= self.status < 500 and self.status not in {408, 425, 429}


def _http_failure(error: HTTPError, lease_token: str | None = None) -> BackendError:
    try:
        value = json.loads(error.read(65536).decode("utf-8", "replace"))
    except (ValueError, OSError):
        value = {}
    details = value.get("error", {}) if isinstance(value, dict) else {}
    if not isinstance(details, dict):
        details = {}
    secrets = [settings.node_secret, lease_token]
    secrets += [base64.urlsafe_b64encode(secret.encode()).decode() for secret in secrets if secret]
    def identifier(value):
        if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)
                and not any(secret and secret in value for secret in secrets)):
            return value
        return None
    code = identifier(details.get("code")) or "backend_request_failed"
    request_id = identifier(details.get("request_id"))
    messages = {
        "testcases_not_ready": "채점할 활성 테스트가 없습니다. 테스트 케이스를 등록하고 활성화한 뒤 다시 요청해 주세요.",
        "testcase_results_invalid": "채점 결과 증거 검증에 실패했습니다. 관리자에게 문의해 주세요.",
        "lease_conflict": "채점 작업의 소유권이 만료되었거나 다른 채점기로 이동했습니다.",
    }
    if error.code in {403, 404}:
        message = "채점 작업에 접근할 수 없거나 작업이 더 이상 존재하지 않습니다."
    elif code in messages:
        message = messages[code]
    elif error.code >= 500 or error.code in {408, 425, 429}:
        message = "채점 서버가 일시적으로 요청을 처리하지 못했습니다."
    else:
        message = "채점 서버가 요청을 거절했습니다. 관리자에게 문의해 주세요."
    return BackendError(error.code, code, message, request_id)


def _unavailable() -> BackendError:
    return BackendError(None, "backend_unavailable", "채점 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.")


class BackendClient:
    def __init__(self) -> None:
        self.base_url = settings.internal_api_base_url

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise _http_failure(error, (payload or {}).get("lease_token")) from None
        except (URLError, TimeoutError, socket.timeout) as error:
            raise _unavailable() from None

    def register_node(self, execution_capabilities=None, execution_revision=None) -> str:
        response = self._request(
            "POST",
            "/internal/judge/nodes/register",
            {
                "node_name": settings.node_name,
                "node_secret": settings.node_secret,
                "total_slots": settings.total_slots,
                "agent_version": settings.agent_version,
                "execution_capabilities": execution_capabilities or [],
                "execution_revision": execution_revision,
            },
        )
        return response["data"]["judge_node_id"]

    def heartbeat(self, node_id: str, running_job_count: int) -> None:
        self._request(
            "POST",
            f"/internal/judge/nodes/{node_id}/heartbeat",
            {
                "node_secret": settings.node_secret,
                "total_slots": settings.total_slots,
                "free_slots": max(settings.total_slots - running_job_count, 0),
                "running_job_count": running_job_count,
                "agent_version": settings.agent_version,
            },
        )

    def claim(self, node_id: str, max_count: int) -> list[dict[str, Any]]:
        response = self._request(
            "POST",
            f"/internal/judge/nodes/{node_id}/assignments:claim",
            {"node_secret": settings.node_secret, "max_count": max_count, "wait_seconds": settings.long_poll_seconds},
            timeout_seconds=max(settings.long_poll_seconds + 15.0, 30.0),
        )
        return response["data"]["jobs"]

    def report_result(
        self,
        job_id: str,
        lease_token: str,
        final_status: str,
        compile_message: str | None,
        judge_message: str | None,
        failed_testcase_order: int | None,
        runtime_ms: int | None = None,
        memory_kb: int | None = None,
        testcase_results: list[dict] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/internal/judge/jobs/{job_id}/result",
            {
                "node_secret": settings.node_secret,
                "lease_token": lease_token,
                "final_status": final_status,
                "compile_message": compile_message,
                "judge_message": judge_message,
                "failed_testcase_order": failed_testcase_order,
                "runtime_ms": runtime_ms,
                "memory_kb": memory_kb,
                **({"testcase_results": testcase_results} if testcase_results is not None else {}),
            },
            timeout_seconds=120.0 if testcase_results is not None else 10.0,
        )

    def report_progress(
        self,
        job_id: str,
        lease_token: str,
        status: str,
        progress_current: int | None,
        progress_total: int | None,
        testcase_result: dict | None = None,
        testcase_results: list[dict] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/internal/judge/jobs/{job_id}/progress",
            {
                "node_secret": settings.node_secret,
                "lease_token": lease_token,
                "status": status,
                "progress_current": progress_current,
                "progress_total": progress_total,
                **({"testcase_result": testcase_result} if testcase_result is not None else {}),
                **({"testcase_results": testcase_results} if testcase_results is not None else {}),
            },
            timeout_seconds=120.0 if testcase_results is not None else 10.0,
        )

    def renew_lease(self, job_id: str, lease_token: str) -> None:
        self._request(
            "POST",
            f"/internal/judge/jobs/{job_id}/lease:renew",
            {
                "node_secret": settings.node_secret,
                "lease_token": lease_token,
            },
        )

    def report_logs(self, node_id: str, logs: list[dict[str, str]]) -> None:
        self._request(
            "POST",
            f"/internal/judge/nodes/{node_id}/logs",
            {
                "node_secret": settings.node_secret,
                "logs": logs,
            },
            timeout_seconds=5.0,
        )


    def report_editor_result(self, job_id, lease_token, *, result=None, error=None):
        payload = {"result": result} if result is not None else {"error": error}
        request = Request(self.base_url + f"/internal/judge/editor-tasks/{job_id}/result", data=json.dumps(payload, allow_nan=False).encode(), method="POST",
                          headers={"Content-Type": "application/json", "X-Judge-Node-Credential": base64.urlsafe_b64encode(settings.node_secret.encode()).decode(), "X-Judge-Lease": lease_token})
        try:
            with urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            raise _http_failure(exc, lease_token) from None
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise _unavailable() from None
