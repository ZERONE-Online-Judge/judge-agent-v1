from __future__ import annotations

import sys
import time
from threading import Event, Lock, Thread
from typing import TextIO

from app.backend_client import BackendClient


class _StreamTee:
    def __init__(self, original: TextIO, level: str, emit_line) -> None:
        self._original = original
        self._level = level
        self._emit_line = emit_line
        self._buffer = ""

    def write(self, value: str) -> int:
        written = self._original.write(value)
        self._original.flush()
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit_line(self._level, line)
        return written

    def flush(self) -> None:
        self._original.flush()
        if self._buffer:
            self._emit_line(self._level, self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return self._original.isatty()

    @property
    def encoding(self) -> str | None:
        return self._original.encoding


class LogForwarder:
    def __init__(self, client: BackendClient, node_id: str) -> None:
        self._client = client
        self._node_id = node_id
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._logs: list[dict[str, str]] = []
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        sys.stdout = _StreamTee(self._original_stdout, "info", self._append)  # type: ignore[assignment]
        sys.stderr = _StreamTee(self._original_stderr, "error", self._append)  # type: ignore[assignment]
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr
        self._thread.join(timeout=2.0)
        self._flush_once()

    def _append(self, level: str, message: str) -> None:
        if not message:
            return
        with self._lock:
            self._logs.append({"level": level, "message": message})
            if len(self._logs) > 1000:
                self._logs = self._logs[-1000:]

    def _drain(self, limit: int = 200) -> list[dict[str, str]]:
        with self._lock:
            batch = self._logs[:limit]
            self._logs = self._logs[limit:]
            return batch

    def _flush_once(self) -> None:
        batch = self._drain()
        if not batch:
            return
        try:
            self._client.report_logs(self._node_id, batch)
        except Exception as error:
            self._original_stderr.write(f"[judge-agent] log forward failed: {error}\n")
            self._original_stderr.flush()

    def _run(self) -> None:
        while not self._stop.wait(2.0):
            self._flush_once()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self._drain_has_items():
                return
            self._flush_once()

    def _drain_has_items(self) -> bool:
        with self._lock:
            return bool(self._logs)
