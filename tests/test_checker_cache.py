from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import stat
import subprocess
import threading

import pytest

from app.executor import ExecutionResult, JudgeExecutor


SOURCE = b'int main(void) { return 0; }'


def test_concurrent_checker_preparation_never_reads_a_partial_binary(tmp_path, monkeypatch):
    # Separate executor instances model jobs/processes sharing the disk cache.
    executors = [JudgeExecutor(tmp_path, sandbox_mode='local') for _ in range(2)]
    partial_written = threading.Event()
    finish_compile = threading.Event()
    builds = []

    def compile_checker(command, cwd):
        builds.append(cwd)
        binary = cwd / 'checker'
        binary.write_bytes(b'partial')
        partial_written.set()
        assert finish_compile.wait(3)
        binary.write_bytes(b'complete')
        binary.chmod(0o755)
        return subprocess.CompletedProcess(command, 0, '', '')

    for executor in executors:
        monkeypatch.setattr(executor, '_run_compiler', compile_checker)
    def prepare(executor):
        result = executor._ensure_cached_checker_binary('checker.c', '.c', SOURCE, [])
        assert isinstance(result, Path)
        return result.read_bytes()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(prepare, executors[0])
        assert partial_written.wait(3)
        second = pool.submit(prepare, executors[1])
        try:
            # An incomplete binary must not become a cache hit while linking.
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            finish_compile.set()
        assert first.result() == second.result() == b'complete'
    assert len(builds) == 1


def test_failed_checker_build_does_not_poison_a_retry(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    calls = []
    def compile_checker(command, cwd):
        calls.append(cwd)
        (cwd / 'checker').write_bytes(b'incomplete' if len(calls) == 1 else b'complete')
        (cwd / 'checker').chmod(0o755)
        return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0, '', 'link failed')
    monkeypatch.setattr(executor, '_run_compiler', compile_checker)
    result = executor._ensure_cached_checker_binary('checker.c', '.c', SOURCE, [])
    assert isinstance(result, ExecutionResult) and result.status == 'system_error'
    result = executor._ensure_cached_checker_binary('checker.c', '.c', SOURCE, [])
    assert isinstance(result, Path) and result.read_bytes() == b'complete'
    assert len(calls) == 2
    assert not list(executor.checker_cache_root.rglob('.build-*'))


def test_checker_cache_hit_is_executable_and_does_not_recompile(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    calls = []
    def compile_checker(command, cwd):
        calls.append(cwd)
        (cwd / 'checker').write_bytes(b'complete')
        (cwd / 'checker').chmod(0o755)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(executor, '_run_compiler', compile_checker)
    first = executor._ensure_cached_checker_binary('checker.c', '.c', SOURCE, [])
    second = executor._ensure_cached_checker_binary('checker.c', '.c', SOURCE, [])
    assert first == second and first.is_file()
    assert stat.S_IMODE(first.stat().st_mode) == 0o755
    assert len(calls) == 1
