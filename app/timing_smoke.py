"""Bounded production-isolate checks for CPU timing and wall-time headroom."""
from pathlib import Path
import tempfile

from app.executor import ExecutionResult, ExecutionTiming, JudgeExecutor, TestcaseRunResult
from app.runtime_policy import PYTHON
from app.settings import settings


def run() -> None:
    if settings.isolate_box_id_base != 0 or settings.isolate_box_id_count > 32:
        raise RuntimeError("Use reserved isolate box IDs 0..31 for timing smoke checks")

    scratch_base = Path("/var/lib/zerone-judge-timing-checks")
    scratch_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-", dir=scratch_base) as scratch:
        executor = JudgeExecutor(Path(scratch) / "work", sandbox_mode="isolate", testcase_parallelism=4)
        # Use the same pinned runtime and visible-library policy as real jobs.
        python = PYTHON

        sleeping = executor._run_command(
            [python, "-c", "import time; time.sleep(0.5)"],
            Path(scratch) / "sleep",
            timeout_seconds=0.2,
            memory_limit_mb=128,
        )
        assert sleeping.returncode == 0, sleeping.stderr
        assert sleeping.runtime_ms is not None and sleeping.runtime_ms < 200, sleeping.runtime_ms
        assert sleeping.wall_runtime_ms is not None and sleeping.wall_runtime_ms >= 400, sleeping.wall_runtime_ms
        assert sleeping.wall_runtime_ms > sleeping.runtime_ms + 250
        assert sleeping.execution_timing.cpu_seconds is not None
        assert sleeping.execution_timing.wall_seconds is not None
        assert sleeping.execution_timing.timeout_origin is None

        busy = executor._run_command(
            [python, "-c", "while True: pass"],
            Path(scratch) / "busy",
            timeout_seconds=0.2,
            memory_limit_mb=128,
        )
        assert busy.returncode == 124, (busy.returncode, busy.stderr)
        assert busy.runtime_ms is not None and 150 <= busy.runtime_ms <= 500, busy.runtime_ms
        assert busy.execution_timing.timeout_origin == "cpu", busy.execution_timing
        assert busy.execution_timing.cpu_seconds >= .2, busy.execution_timing

        # Confirmed CPU exhaustion must not double the expensive case work.
        case_job = Path(scratch) / "confirmation"
        case_job.mkdir()
        calls: dict[int, int] = {}
        original_run = executor._run_single_testcase

        def counted_run(*args, **kwargs):
            order = int(args[3]["display_order"])
            calls[order] = calls.get(order, 0) + 1
            return original_run(*args, **kwargs)

        executor._run_single_testcase = counted_run
        confirmed = executor._run_testcases(
            [python, "-c", "while True: pass"], case_job,
            {"judge_job_id": "confirmation-smoke", "problem": {
                "time_limit_ms": 200, "memory_limit_mb": 128,
            }},
            [{"display_order": i, "input_text": "", "output_text": ""}
             for i in range(1, 5)], "smoke",
        )
        assert confirmed.status == "time_limit_exceeded", confirmed
        assert confirmed.failed_testcase_order == 1, confirmed
        assert calls == {1: 1, 2: 1, 3: 1, 4: 1}, calls
        print("CPU TLE skips redundant confirmation: PASS", flush=True)

        # A process sleeping past the wall guard still gets one clean replay.
        # This is local diagnostic work only: no backend job or result is made.
        wall_dir = Path(scratch) / "wall-confirmation"
        wall_dir.mkdir()
        wall_calls: dict[int, int] = {}
        wall_evidence = []

        def counted_wall(*args, **kwargs):
            order = int(args[3]["display_order"])
            wall_calls[order] = wall_calls.get(order, 0) + 1
            result = original_run(*args, **kwargs)
            wall_evidence.append(result.timing)
            return result

        executor._run_single_testcase = counted_wall
        wall_result = executor._run_testcases(
            [python, "-c", "import time; time.sleep(30)"], wall_dir,
            {"judge_job_id": "wall-confirmation-smoke", "problem": {
                "time_limit_ms": 100, "memory_limit_mb": 128,
            }},
            [{"display_order": i, "input_text": "", "output_text": ""}
             for i in (1, 2)], "smoke",
        )
        assert wall_result.status == "time_limit_exceeded", wall_result
        assert wall_result.failed_testcase_order == 1, wall_result
        assert wall_calls == {1: 2, 2: 1}, wall_calls
        assert all(item.timeout_origin == "wall" for item in wall_evidence), wall_evidence
        print("Wall TLE gets exactly one clean confirmation: PASS", flush=True)

        # CPU-bound accepted code remains accepted in serial and parallel.
        # 75% of the official CPU budget leaves 100ms startup/reporting headroom.
        accepted_source = "import time; start=time.process_time()\nwhile time.process_time()-start < .3: pass"
        for parallelism in (1, 4):
            executor._run_single_testcase = original_run
            executor.testcase_parallelism = parallelism
            accepted_dir = Path(scratch) / f"accepted-{parallelism}"
            accepted_dir.mkdir()
            accepted = executor._run_testcases(
                [python, "-c", accepted_source], accepted_dir,
                {"judge_job_id": f"accepted-timing-smoke-{parallelism}", "problem": {
                    "time_limit_ms": 400, "memory_limit_mb": 128,
                }},
                [{"display_order": i, "input_text": "", "output_text": ""}
                 for i in range(1, 5)], "smoke",
            )
            assert accepted.status == "accepted", (parallelism, accepted)
        print("Accepted CPU-bound code stays accepted at 1/4 workers: PASS", flush=True)

        # IOI collection must retain every independent CPU-TLE observation.
        executor.testcase_parallelism = 4
        executor._run_single_testcase = counted_run
        calls.clear()
        all_dir = Path(scratch) / "ioi-cpu-timeouts"
        all_dir.mkdir()
        independent = executor._run_testcases(
            [python, "-c", "while True: pass"], all_dir,
            {"judge_job_id": "ioi-cpu-timeout-smoke", "scoring_mode": "ioi",
             "collect_all_results": True, "submission": {"submission_kind": "participant"},
             "problem": {"time_limit_ms": 100, "memory_limit_mb": 128}},
            [{"display_order": i, "input_text": "", "output_text": ""}
             for i in range(1, 5)], "smoke",
        )
        assert independent.status == "time_limit_exceeded", independent
        assert calls == {1: 1, 2: 1, 3: 1, 4: 1}, calls
        assert [row["final_status"] for row in independent.testcase_results] == ["time_limit_exceeded"] * 4
        print("IOI keeps all independent timeout evidence without replay: PASS", flush=True)

        # Exercise recovery and persistent infrastructure uncertainty without
        # breaking an actual Isolate process or publishing a backend result.
        # These four checks inject runner evidence; the CPU/wall checks above
        # execute real sandboxed programs.
        executor.testcase_parallelism = 1
        for origin in ("unknown", "watchdog"):
            for recovers in (True, False):
                diagnostic_dir = Path(scratch) / f"injected-{origin}-{recovers}"
                diagnostic_dir.mkdir()
                diagnostic_calls = []

                def injected_run(*args, **kwargs):
                    order = int(args[3]["display_order"])
                    diagnostic_calls.append(order)
                    if recovers and len(diagnostic_calls) == 2:
                        return TestcaseRunResult(order, ExecutionResult("accepted", runtime_ms=10),
                            timing=ExecutionTiming(.01, .02, .1, 2.1))
                    return TestcaseRunResult(order, ExecutionResult("time_limit_exceeded",
                        failed_testcase_order=order),
                        timing=ExecutionTiming(None, None, .1, 2.1, origin))

                executor._run_single_testcase = injected_run
                diagnostic = executor._run_testcases(
                    [python, "-c", ""], diagnostic_dir,
                    {"judge_job_id": f"injected-{origin}-{recovers}"},
                    [{"display_order": 1}], "smoke",
                )
                expected = "accepted" if recovers else "system_error"
                assert diagnostic.status == expected, (origin, recovers, diagnostic)
                assert diagnostic_calls == [1, 1], (origin, recovers, diagnostic_calls)
                if not recovers:
                    assert diagnostic.runtime_ms is None, diagnostic
        executor._run_single_testcase = original_run
        print("Injected missing-metadata/watchdog recovery policy: PASS (4 cases)", flush=True)

        assert executor._isolate_wall_time_seconds(1.0) == 3.0
        print(
            "Timing policy checks: PASS "
            f"sleep_cpu_ms={sleeping.runtime_ms} sleep_wall_ms={sleeping.wall_runtime_ms} "
            f"busy_cpu_ms={busy.runtime_ms}",
            flush=True,
        )


if __name__ == "__main__":
    run()
