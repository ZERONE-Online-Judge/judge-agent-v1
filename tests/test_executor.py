from pathlib import Path

from app.executor import JudgeExecutor


def test_python_submission_accepts(tmp_path: Path):
    executor = JudgeExecutor(tmp_path)
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
    assert result.score == 100
    assert result.runtime_ms is not None


def test_python_runtime_error(tmp_path: Path):
    executor = JudgeExecutor(tmp_path)
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
    assert result.score == 0


def test_python_testcase_accepts(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("40 2\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path)
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
    assert result.score == 100
    assert result.runtime_ms is not None


def test_python_testcase_wrong_answer(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("40 2\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path)
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
    assert result.score == 0


def test_python_testcase_reads_file_url(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("21\n", encoding="utf-8")
    output_path.write_text("42\n", encoding="utf-8")

    executor = JudgeExecutor(tmp_path)
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


def test_testcase_uses_problem_score_and_time_limit(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("", encoding="utf-8")
    output_path.write_text("", encoding="utf-8")

    executor = JudgeExecutor(tmp_path)
    result = executor.judge(
        {
            "judge_job_id": "job-timeout",
            "problem": {"max_score": 70, "time_limit_ms": 100},
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
    assert result.score == 0


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
    executor = JudgeExecutor(tmp_path)
    result = executor.judge(
        {
            "judge_job_id": "job-progress",
            "problem": {"max_score": 100, "time_limit_ms": 1000},
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
    executor = JudgeExecutor(tmp_path, testcase_parallelism=2)
    result = executor.judge(
        {
            "judge_job_id": "job-parallel-progress",
            "problem": {"max_score": 100, "time_limit_ms": 1000},
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

    executor = JudgeExecutor(tmp_path, testcase_parallelism=3)
    result = executor.judge(
        {
            "judge_job_id": "job-parallel-first-failure",
            "problem": {"max_score": 100, "time_limit_ms": 1000},
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

    executor = JudgeExecutor(tmp_path)
    result = executor.judge(
        {
            "judge_job_id": "job-custom-checker",
            "problem": {"max_score": 80, "time_limit_ms": 1000},
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
    assert result.score == 80


def test_output_limit_exceeded(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, output_limit_bytes=1024)
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
    assert result.score == 0


def test_docker_sandbox_command_uses_isolation_flags(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="docker")
    command = executor._sandbox_command(["python3", "main.py"], tmp_path)

    assert "--network" in command
    assert "none" in command
    assert "--memory" in command
    assert "--pids-limit" in command
    assert "--cap-drop" in command
    assert "ALL" in command


def test_docker_sandbox_command_can_mount_job_root_for_case_workdir(tmp_path: Path):
    executor = JudgeExecutor(tmp_path, sandbox_mode="docker")
    case_dir = tmp_path / "cases" / "001"
    command = executor._sandbox_command(["/work/job/main"], case_dir, tmp_path)

    assert f"{tmp_path}:{tmp_path}:rw" in command
    assert "--workdir" in command
    assert str(case_dir) in command
