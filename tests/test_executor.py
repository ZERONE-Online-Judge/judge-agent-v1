from pathlib import Path
import subprocess
import time

from app.executor import JudgeExecutor


def test_python_submission_accepts(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-python",
            "submission": {
                "submission_id": "submission-python",
                "language": "python313",
                "source_code": "print(42)",
            },
        }
    )

    assert result.status == "accepted"
    assert result.runtime_ms is not None


def test_python_runtime_error(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-runtime",
            "submission": {
                "submission_id": "submission-runtime",
                "language": "python313",
                "source_code": "raise RuntimeError('fail')",
            },
        }
    )

    assert result.status == "runtime_error"


def test_python_testcase_accepts(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("40 2\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-testcase-accepted",
            "submission": {
                "submission_id": "submission-testcase-accepted",
                "language": "python313",
                "source_code": "a,b=map(int,input().split()); print(a+b)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
        }
    )

    assert result.status == "accepted"
    assert result.runtime_ms is not None


def test_python_testcase_wrong_answer(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("40 2\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-testcase-wrong-answer",
            "submission": {
                "submission_id": "submission-testcase-wrong-answer",
                "language": "python313",
                "source_code": "print(41)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
        }
    )

    assert result.status == "wrong_answer"


def test_python_testcase_reads_file_url(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("21\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-testcase-file-url",
            "submission": {
                "submission_id": "submission-testcase-file-url",
                "language": "python313",
                "source_code": "print(int(input()) * 2)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": "unused-input",
                    "output_storage_key": "unused-output",
                    "input_url": input_path.as_uri(),
                    "output_url": output_path.as_uri(),
                }
            ],
        }
    )

    assert result.status == "accepted"


def test_testcase_uses_problem_time_limit(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("", encoding="utf-8")
    output_path.write_text("", encoding="utf-8")

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-timeout",
            "problem": {"time_limit_ms": 100},
            "submission": {
                "submission_id": "submission-timeout",
                "language": "python313",
                "source_code": "while True:\n    pass\n",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
        }
    )

    assert result.status == "time_limit_exceeded"


def test_testcase_progress_callback_reports_completed_cases(tmp_path: Path):
    input_one = tmp_path / "input1.txt"
    output_one = tmp_path / "output1.txt"
    input_two = tmp_path / "input2.txt"
    output_two = tmp_path / "output2.txt"
    input_one.write_text("20 22\n", encoding="utf-8")
    output_one.write_text("42\n", encoding="utf-8")
    input_two.write_text("19 23\n", encoding="utf-8")
    output_two.write_text("42\n", encoding="utf-8")

    progress_events: list[tuple[str, int | None, int | None]] = []
    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-progress",
            "problem": {"time_limit_ms": 1000},
            "submission": {
                "submission_id": "submission-progress",
                "language": "python313",
                "source_code": "a,b=map(int,input().split()); print(a+b)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_one),
                    "output_storage_key": str(output_one),
                },
                {
                    "display_order": 2,
                    "input_storage_key": str(input_two),
                    "output_storage_key": str(output_two),
                },
            ],
        },
        progress=lambda status, current, total: progress_events.append((status, current, total)),
    )

    assert result.status == "accepted"
    assert progress_events == [
        ("preparing", 0, 2),
        ("judging", 0, 2),
        ("judging", 1, 2),
        ("judging", 2, 2),
    ]


def test_parallel_testcases_report_progress_by_completed_count(tmp_path: Path):
    input_paths = []
    output_paths = []
    for index, value in enumerate(["10 32\n", "20 22\n", "30 12\n"], start=1):
        input_path = tmp_path / f"input{index}.txt"
        output_path = tmp_path / f"output{index}.txt"
        input_path.write_text(value, encoding="utf-8")
        output_path.write_text("42\n", encoding="utf-8")
        input_paths.append(input_path)
        output_paths.append(output_path)

    progress_events: list[tuple[str, int | None, int | None]] = []
    executor = JudgeExecutor(tmp_path, sandbox_mode="local", testcase_parallelism=2)
    result = executor.judge(
        {
            "judge_job_id": "job-parallel-progress",
            "problem": {"time_limit_ms": 1000},
            "submission": {
                "submission_id": "submission-parallel-progress",
                "language": "python313",
                "source_code": "a,b=map(int,input().split()); print(a+b)",
            },
            "testcases": [
                {
                    "display_order": index,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
                for index, (input_path, output_path) in enumerate(zip(input_paths, output_paths), start=1)
            ],
        },
        progress=lambda status, current, total: progress_events.append((status, current, total)),
    )

    assert result.status == "accepted"
    assert progress_events[0:2] == [
        ("preparing", 0, 3),
        ("judging", 0, 3),
    ]
    assert sorted(progress_events[2:]) == [
        ("judging", 1, 3),
        ("judging", 2, 3),
        ("judging", 3, 3),
    ]


def test_parallel_testcases_return_first_failing_display_order(tmp_path: Path):
    testcases = []
    expected_values = ["42\n", "42\n", "42\n"]
    input_values = ["42\n", "41\n", "40\n"]
    for index, (input_value, expected_value) in enumerate(zip(input_values, expected_values), start=1):
        input_path = tmp_path / f"input{index}.txt"
        output_path = tmp_path / f"output{index}.txt"
        input_path.write_text(input_value, encoding="utf-8")
        output_path.write_text(expected_value, encoding="utf-8")
        testcases.append(
            {
                "display_order": index,
                "input_storage_key": str(input_path),
                "output_storage_key": str(output_path),
            }
        )

    executor = JudgeExecutor(tmp_path, sandbox_mode="local", testcase_parallelism=3)
    result = executor.judge(
        {
            "judge_job_id": "job-parallel-first-failure",
            "problem": {"time_limit_ms": 1000},
            "submission": {
                "submission_id": "submission-parallel-first-failure",
                "language": "python313",
                "source_code": "print(input().strip())",
            },
            "testcases": testcases,
        }
    )

    assert result.status == "wrong_answer"
    assert result.failed_testcase_order == 2


def test_testcase_accepts_with_custom_checker(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    checker_path = tmp_path / "checker.py"
    input_path.write_text("21\n", encoding="utf-8")
    output_path.write_text("unused official output\n", encoding="utf-8")
    checker_path.write_text(
        "import pathlib, sys\n"
        "actual = pathlib.Path(sys.argv[2]).read_text().strip()\n"
        "raise SystemExit(0 if actual == '42' else 1)\n",
        encoding="utf-8",
    )

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-custom-checker",
            "problem": {"time_limit_ms": 1000},
            "submission": {
                "submission_id": "submission-custom-checker",
                "language": "python313",
                "source_code": "print(int(input()) * 2)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
            "package_files": [
                {
                    "role": "checker",
                    "original_filename": "checker.py",
                    "storage_key": str(checker_path),
                }
            ],
        }
    )

    assert result.status == "accepted"


def test_output_limit_exceeded(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="local", output_limit_bytes=1024)
    result = executor.judge(
        {
            "judge_job_id": "job-output-limit",
            "submission": {
                "submission_id": "submission-output-limit",
                "language": "python313",
                "source_code": "print('x' * 5000)",
            },
        }
    )

    assert result.status == "output_limit_exceeded"


def test_isolate_meta_maps_timeout_to_tle(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")

    assert executor._isolate_returncode({"status": "TO"}) == 124
    assert executor._isolate_message({"status": "TO"}, 124) == "time limit exceeded"
    assert executor._isolate_returncode({"cg-oom-killed": "1"}) == 137
    assert executor._isolate_message({"cg-oom-killed": "1"}, 137) == "memory limit exceeded"


def test_isolate_meta_maps_signal_to_runtime_style_code(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")

    assert executor._isolate_returncode({"status": "SG", "exitsig": "11"}) == -11
    assert executor._isolate_returncode({"status": "SG", "exitsig": "9"}) == 137
    assert executor._isolate_returncode({"status": "SG", "exitsig": "25"}) == 125


def test_isolate_meta_parses_runtime_and_memory(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")

    assert executor._isolate_runtime_ms({"time": "0.017"}) == 17
    assert executor._isolate_memory_kb({"max-rss": "2048"}) == 2048
    assert executor._isolate_memory_kb({"cg-mem": "4096", "max-rss": "2048"}) == 4096


def test_isolate_wall_time_matches_problem_limit(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")

    assert executor._isolate_wall_time_seconds(1.0) == 1.0
    assert executor._isolate_wall_time_seconds(0.05) == 0.1


def test_completed_process_reports_wall_runtime(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    stdout_path = tmp_path / "stdout.txt"
    stderr_path = tmp_path / "stderr.txt"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    with stdout_path.open("rb") as stdout_file, stderr_path.open("rb") as stderr_file:
        completed = executor._completed_process(
            ["sleep"],
            0,
            stdout_file,
            stderr_file,
            started_at=time.monotonic() - 1.0,
        )

    assert completed.runtime_ms is not None
    assert completed.runtime_ms >= 900


def test_memory_limit_uses_problem_and_testcase_values(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    job = {"problem": {"memory_limit_mb": 1024}}

    assert executor._problem_memory_limit_mb(job) == 1024
    assert executor._testcase_memory_limit_mb(job, {}) == 1024
    assert (
        executor._testcase_memory_limit_mb(
            job,
            {"memory_limit_mb_override": 768},
        )
        == 768
    )


def test_problem_limits_use_language_overrides(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    job = {
        "problem": {
            "time_limit_ms": 1000,
            "memory_limit_mb": 256,
            "language_resource_limits": {
                "python313": {"time_limit_ms": 2500, "memory_limit_mb": 512}
            },
        },
        "submission": {"language": "python313"},
    }

    assert executor._problem_time_limit_seconds(job) == 2.5
    assert executor._problem_memory_limit_mb(job) == 512
    assert executor._testcase_time_limit_seconds(job, {}) == 2.5
    assert executor._testcase_memory_limit_mb(job, {}) == 512
    assert executor._testcase_time_limit_seconds(job, {"time_limit_ms_override": 300}) == 0.3
    assert executor._testcase_memory_limit_mb(job, {"memory_limit_mb_override": 128}) == 128


def test_language_time_limit_override_is_used_during_judging(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("", encoding="utf-8")
    output_path.write_text("", encoding="utf-8")

    executor = JudgeExecutor(tmp_path, sandbox_mode="local")
    result = executor.judge(
        {
            "judge_job_id": "job-language-timeout",
            "problem": {
                "time_limit_ms": 2000,
                "language_resource_limits": {
                    "python313": {"time_limit_ms": 100},
                },
            },
            "submission": {
                "submission_id": "submission-language-timeout",
                "language": "python313",
                "source_code": "while True:\n    pass\n",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
        }
    )

    assert result.status == "time_limit_exceeded"


def test_language_resource_overrides_are_passed_to_testcase_runner(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    class CapturingExecutor(JudgeExecutor):
        def __init__(self, work_root: Path) -> None:
            super().__init__(work_root, sandbox_mode="local")
            self.run_calls: list[tuple[float, int | None]] = []

        def _run_command(
            self,
            command: list[str],
            cwd: Path,
            timeout_seconds: float,
            stdin: str = "",
            sandbox_mode_override: str | None = None,
            sandbox_container_id: str | None = None,
            sandbox_mount_root: Path | None = None,
            memory_limit_mb: int | None = None,
        ) -> subprocess.CompletedProcess[str]:
            self.run_calls.append((timeout_seconds, memory_limit_mb))
            completed = subprocess.CompletedProcess(command, 0, "42\n", "")
            completed.runtime_ms = 1
            completed.memory_kb = 1024
            return completed

    executor = CapturingExecutor(tmp_path)
    result = executor.judge(
        {
            "judge_job_id": "job-language-resource-capture",
            "problem": {
                "time_limit_ms": 2000,
                "memory_limit_mb": 256,
                "language_resource_limits": {
                    "python313": {"time_limit_ms": 750, "memory_limit_mb": 384},
                    "java8": {"time_limit_ms": 3000, "memory_limit_mb": 512},
                },
            },
            "submission": {
                "submission_id": "submission-language-resource-capture",
                "language": "python313",
                "source_code": "print(42)",
            },
            "testcases": [
                {
                    "display_order": 1,
                    "input_storage_key": str(input_path),
                    "output_storage_key": str(output_path),
                }
            ],
        }
    )

    assert result.status == "accepted"
    assert executor.run_calls == [(0.75, 384)]


def test_other_language_resource_overrides_do_not_apply(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    job = {
        "problem": {
            "time_limit_ms": 1000,
            "memory_limit_mb": 256,
            "language_resource_limits": {
                "java8": {"time_limit_ms": 3000, "memory_limit_mb": 512},
            },
        },
        "submission": {"language": "python313"},
    }

    assert executor._problem_time_limit_seconds(job) == 1.0
    assert executor._problem_memory_limit_mb(job) == 256


def test_java_command_gets_conservative_heap_limit(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    command = executor._command_with_runtime_limits(
        ["/usr/bin/java", "-cp", "/work/job", "Main"],
        512,
    )

    assert command[1:7] == [
        "-Xmx256m",
        "-Xss256k",
        "-XX:+UseSerialGC",
        "-XX:ReservedCodeCacheSize=32m",
        "-XX:CompressedClassSpaceSize=16m",
        "-XX:MaxMetaspaceSize=64m",
    ]


def test_java_heap_oom_maps_to_memory_limit(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    completed = subprocess.CompletedProcess(
        ["/usr/bin/java", "-cp", "/work/job", "Main"],
        1,
        "",
        "Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space",
    )

    assert executor._is_memory_limit_result(completed)


def test_java_heap_scales_with_problem_memory(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")

    assert executor._java_heap_mb(128) == 64
    assert executor._java_heap_mb(256) == 128
    assert executor._java_heap_mb(512) == 256
    assert executor._java_heap_mb(1024) == 512
    assert executor._java_heap_mb(2048) == 512


def test_isolate_mounts_java_symlink_chain(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate")
    args = executor._isolate_system_dir_args(["/usr/bin/java", "-version"])

    assert "--dir=/usr=/usr" in args
    if Path("/etc/alternatives").exists():
        assert "--dir=/etc/alternatives=/etc/alternatives" in args
