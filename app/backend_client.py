from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import settings


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
            detail = error.read().decode("utf-8")
            raise RuntimeError(f"backend http {error.code}: {detail}") from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise RuntimeError(f"backend unavailable: {error}") from error

    def register_node(self) -> str:
        response = self._request(
            "POST",
            "/internal/judge/nodes/register",
            {
                "node_name": settings.node_name,
                "node_secret": settings.node_secret,
                "total_slots": settings.total_slots,
                "agent_version": settings.agent_version,
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
        awarded_score: int | None,
        compile_message: str | None,
        judge_message: str | None,
        failed_testcase_order: int | None,
        runtime_ms: int | None = None,
        memory_kb: int | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/internal/judge/jobs/{job_id}/result",
            {
                "node_secret": settings.node_secret,
                "lease_token": lease_token,
                "final_status": final_status,
                "awarded_score": awarded_score,
                "compile_message": compile_message,
                "judge_message": judge_message,
                "failed_testcase_order": failed_testcase_order,
                "runtime_ms": runtime_ms,
                "memory_kb": memory_kb,
            },
        )

    def report_progress(
        self,
        job_id: str,
        lease_token: str,
        status: str,
        progress_current: int | None,
        progress_total: int | None,
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
            },
        )
