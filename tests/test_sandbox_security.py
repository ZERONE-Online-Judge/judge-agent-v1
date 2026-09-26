import os
import subprocess
from pathlib import Path

import pytest

from app.executor import JudgeExecutor


def test_compiler_failure_never_falls_back_to_host(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    calls = []

    def failed_isolate(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args[0], 127, "", "isolate unavailable")

    monkeypatch.setattr(executor, "_run_isolate_command", failed_isolate)
    before = tmp_path.stat().st_mode
    result = executor._prepare_command(tmp_path, "c99", "int main(void) {return 0;}")
    assert result.status == "compile_error"
    assert len(calls) == 1
    assert tmp_path.stat().st_mode == before


def test_metadata_is_outside_the_writable_sandbox(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    metadata = []
    run_timeouts = []

    def fake_isolate(command, **kwargs):
        for arg in command:
            if arg.startswith("--meta="):
                path = Path(arg.split("=", 1)[1])
                assert not path.is_relative_to(tmp_path)
                metadata.append(path)
                path.write_text("status:OK\nexitcode:0\ntime:0.123\ntime-wall:0.900\n")
                assert "--time=1" in command
                assert "--wall-time=3" in command
                run_timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_isolate)
    result = executor._run_command(["/usr/bin/true"], tmp_path, 1)
    assert result.returncode == 0
    assert result.runtime_ms == 123
    assert run_timeouts == [8]
    assert len(metadata) == 1
    assert not metadata[0].exists()


@pytest.mark.skipif(os.getenv("ZOJ_RUN_ISOLATE_TESTS") != "1", reason="Requires Linux isolate and delegated cgroups")
def test_real_isolate_languages_and_private_files():
    from app.security_smoke import run
    run()
