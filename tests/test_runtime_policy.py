from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import threading
import time

import pytest
from app.cpu_pool import CpuPool
from app.executor import JudgeExecutor
from app.runtime_policy import JAVA, with_runtime_limits


def test_java_and_kotlin_share_budget_and_preserve_entrypoints():
    for memory, heap in [(16, 8), (128, 64), (256, 192), (1024, 768)]:
        for entry in [["-cp", "/work", "Main"], ["-jar", "/work/program.jar"]]:
            args = with_runtime_limits(["java", *entry], memory)
            assert args[0] == JAVA and args[-len(entry):] == entry
            assert f"-Xmx{heap}m" in args
            assert f"-Xms{min(32, heap)}m" in args
            assert "-Xss8m" in args and "-XX:ActiveProcessorCount=1" in args
            assert args == with_runtime_limits(args, memory)


def test_cached_legacy_jvm_flags_are_replaced():
    old = ["/usr/local/lib/icpc-jdk/bin/java", "-Xmx1024m", "-Xms1024m",
           "-Xss64m", "-XX:ActiveProcessorCount=5", "-jar", "program.jar"]
    args = with_runtime_limits(old, 128)
    assert "-Xmx64m" in args and "-Xms32m" in args
    assert "-Xmx1024m" not in args and "-Xss64m" not in args


@pytest.mark.parametrize("language,binary", [("python311", "python3.11"),
    ("python313", "python3.13"), ("pypy3", "pypy3")])
def test_python_variants_check_syntax_before_execution(tmp_path, monkeypatch, language, binary):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    commands = []
    def compiler(command, cwd):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "SyntaxError: invalid syntax")
    monkeypatch.setattr(executor, "_run_compiler", compiler)
    result = executor._prepare_command(tmp_path, language, "def broken(:")
    assert result.status == "compile_error"
    assert Path(commands[0][0]).name == binary


def test_cpu_leases_serialize_excess_work_without_collisions(tmp_path):
    pool = CpuPool(100, 1000, tmp_path, cpus=[0, 1, 2])
    active = set()
    lock = threading.Lock()
    def run(index):
        with pool.lease() as (box, cpu):
            with lock:
                assert cpu not in active
                active.add(cpu)
            assert box == 99 + cpu
            time.sleep(0.01)
            with lock:
                active.remove(cpu)
            if index == 3:
                raise ValueError("Release the lease on failure")
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(lambda i: workers_run(run, i), range(12)))
    assert results.count(False) == 1 and not active
    with pool.lease():
        pass


def workers_run(run, index):
    try:
        run(index)
        return True
    except ValueError:
        return False


def test_isolate_cpu_config_is_idempotent_and_checks_box_range(tmp_path):
    path = tmp_path / "isolate"
    path.write_text("num_boxes = 102\nbox100.cpus = 9\n")
    pool = CpuPool(100, 2, tmp_path / "locks", cpus=[0, 1, 2])
    pool.configure_isolate(path)
    before = path.read_text()
    pool.configure_isolate(path)
    assert before == path.read_text()
    assert "box100.cpus = 1\n" in before and "box101.cpus = 2\n" in before
    path.write_text("num_boxes = 101\n")
    with pytest.raises(RuntimeError, match="box ID range"):
        pool.configure_isolate(path)
