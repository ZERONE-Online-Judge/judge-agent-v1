"""Timeout evidence must justify a verdict without changing scoring order."""
from collections import Counter
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.executor import ExecutionResult, ExecutionTiming, JudgeExecutor, TestcaseRunResult as Case


def timing(origin="cpu", *, cpu=None, wall=None, limit=1.0, status="TO", source="isolate"):
    return ExecutionTiming(
        cpu_seconds=(1.05 if origin == "cpu" else 0.05) if cpu is None else cpu,
        wall_seconds=(3.05 if origin == "wall" else 1.1) if wall is None else wall,
        cpu_limit_seconds=limit,
        wall_limit_seconds=3.0,
        timeout_origin=origin,
        source=source,
        status=status,
    )


def case(order, status="time_limit_exceeded", evidence=None):
    return Case(order, ExecutionResult(status, failed_testcase_order=order if status != "accepted" else None,
                                      runtime_ms=1050 if evidence and evidence.timeout_origin == "cpu" else 50),
                timing=evidence)


@pytest.fixture
def executor(tmp_path):
    return JudgeExecutor(tmp_path, sandbox_mode="isolate", testcase_parallelism=4)


def test_isolate_cpu_and_wall_measurements_remain_distinct(executor):
    evidence = executor._isolate_execution_timing(
        {"status": "TO", "time": "1.013", "time-wall": "2.45"}, 1.0, 3.0)
    assert evidence.cpu_seconds == 1.013
    assert evidence.wall_seconds == 2.45
    assert evidence.cpu_limit_seconds == 1.0
    assert evidence.wall_limit_seconds == 3.0
    assert evidence.source == "isolate" and evidence.status == "TO"
    assert evidence.timeout_origin == "cpu"


@pytest.mark.parametrize("value", [None, "", "not-a-number", "NaN", "Infinity", "-Infinity", "-0.01", "1e9999"])
def test_invalid_cpu_measurement_never_uses_elapsed_time_as_cpu(executor, value):
    meta = {"status": "TO", "time-wall": "30"}
    if value is not None:
        meta["time"] = value
    evidence = executor._isolate_execution_timing(meta, 1.0, 3.0)
    assert evidence.cpu_seconds is None
    assert evidence.wall_seconds == 30.0
    assert evidence.timeout_origin == "unknown"
    assert executor._needs_timeout_confirmation(case(1, evidence=evidence))


@pytest.mark.parametrize("value", [None, "", "invalid", "NaN", "Infinity", "-1"])
def test_wall_timeout_requires_a_real_wall_measurement(executor, value):
    meta = {"status": "TO", "time": "0.01"}
    if value is not None:
        meta["time-wall"] = value
    evidence = executor._isolate_execution_timing(meta, 1.0, 3.0)
    assert evidence.wall_seconds is None
    assert evidence.timeout_origin == "unknown"


@pytest.mark.parametrize("cpu,wall,expected", [("1.0", "3", "cpu"), ("1.01", "3.1", "cpu"),
                                              ("0", "3", "wall"), ("0.5", "3.1", "wall"),
                                              ("0.5", "2.99", "unknown")])
def test_timeout_provenance_requires_reaching_the_corresponding_limit(executor, cpu, wall, expected):
    evidence = executor._isolate_execution_timing({"status": "TO", "time": cpu, "time-wall": wall}, 1.0, 3.0)
    assert evidence.timeout_origin == expected


@pytest.mark.parametrize("status", [None, "OK", "RE", "SG", "XX"])
def test_cpu_measurement_without_isolate_timeout_is_not_cpu_timeout_evidence(executor, status):
    meta = {"time": "2", "time-wall": "4", "exitcode": "124"}
    if status is not None:
        meta["status"] = status
    evidence = executor._isolate_execution_timing(meta, 1.0, 3.0)
    assert evidence.timeout_origin != "cpu"


def test_watchdog_is_not_promoted_to_a_cpu_verdict_even_with_old_metadata(executor):
    evidence = executor._isolate_execution_timing(
        {"status": "TO", "time": "2", "time-wall": "8"}, 1.0, 3.0, watchdog=True)
    assert evidence.timeout_origin == "watchdog"
    assert executor._needs_timeout_confirmation(case(1, evidence=evidence))


def test_missing_timing_cannot_prove_cpu_exhaustion_from_runtime_display(executor):
    result = Case(1, ExecutionResult("time_limit_exceeded", runtime_ms=30_000))
    assert executor._needs_timeout_confirmation(result)


@pytest.mark.parametrize("status", ["accepted", "wrong_answer", "runtime_error", "memory_limit_exceeded",
                                   "output_limit_exceeded", "system_error"])
def test_timeout_evidence_does_not_override_other_verdicts(executor, status):
    assert not executor._needs_timeout_confirmation(case(1, status, timing()))


@pytest.mark.parametrize("parallelism", [1, 4])
def test_proven_cpu_timeout_is_not_executed_twice(tmp_path, monkeypatch, parallelism):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate", testcase_parallelism=parallelism)
    calls = Counter()
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        order = args[3]["display_order"]
        calls[order] += 1
        return case(order, evidence=timing())

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path, {},
                                    [{"display_order": i} for i in range(1, 9)], "sha")
    assert result.status == "time_limit_exceeded" and result.failed_testcase_order == 1
    assert calls == Counter({i: 1 for i in range(1, parallelism + 1)})


@pytest.mark.parametrize("parallelism", [1, 4])
@pytest.mark.parametrize("retried", ["accepted", "wrong_answer", "runtime_error", "time_limit_exceeded"])
def test_wall_timeout_is_rechecked_once_and_only_final_evidence_is_reported(
    tmp_path, monkeypatch, parallelism, retried,
):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate", testcase_parallelism=parallelism)
    calls, observations, phases = Counter(), [], []
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        order = args[3]["display_order"]
        calls[order] += 1
        if order == 1 and calls[order] == 1:
            return case(order, evidence=timing("wall"))
        return case(order, retried if order == 1 else "accepted", timing("wall") if order == 1 else None)

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path,
        {"scoring_mode": "ioi", "collect_all_results": True, "submission": {"submission_kind": "participant"}},
        [{"display_order": i} for i in range(1, 5)], "sha",
        progress=lambda *args, **kwargs: phases.append((args, kwargs)),
        case_progress=lambda value, current, total: observations.append((value, current, total)))
    assert calls == Counter({1: 2, 2: 1, 3: 1, 4: 1})
    assert result.status == retried
    assert [row[0]["index"] for row in observations] == [1, 2, 3, 4]
    assert observations[0][0]["final_status"] == retried
    assert [row[1:] for row in observations] == [(i, 4) for i in range(1, 5)]
    assert len(result.testcase_results) == 4
    confirmation = [(args, kwargs) for args, kwargs in phases
                    if kwargs.get("progress_phase") == "tle_confirmation"]
    assert len(confirmation) == 1
    assert confirmation[0][0] == ("judging", 0, 4)
    assert [args[1] for args, _ in phases] == sorted(args[1] for args, _ in phases)


@pytest.mark.parametrize("origin", ["unknown", "watchdog", None])
@pytest.mark.parametrize("parallelism", [1, 4])
def test_repeated_unmeasured_timeout_is_an_environment_error(tmp_path, monkeypatch, origin, parallelism):
    executor = JudgeExecutor(tmp_path, sandbox_mode="isolate", testcase_parallelism=parallelism)
    calls = Counter()
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        order = args[3]["display_order"]
        calls[order] += 1
        evidence = timing(origin) if origin else None
        return case(order, evidence=evidence)

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path, {},
                                    [{"display_order": i} for i in range(1, 5)], "sha")
    assert result.status == "system_error" and result.failed_testcase_order == 1
    assert calls[1] == 2 and max(calls.values()) == 2


@pytest.mark.parametrize("initial,final,expected,failed,retries", [
    (["cpu", "wall", "wall", "wall"], {}, "time_limit_exceeded", 1, []),
    (["wrong_answer", "wall", "wall", "wall"], {}, "wrong_answer", 1, []),
    (["wall", "cpu", "wall", "wall"], {1: "accepted"}, "time_limit_exceeded", 2, [1]),
    (["wall", "wall", "cpu", "wall"], {1: "accepted", 2: "wrong_answer"}, "wrong_answer", 2, [1, 2]),
])
def test_icpc_keeps_display_order_and_stops_confirming_later_failures(
    executor, tmp_path, monkeypatch, initial, final, expected, failed, retries,
):
    calls, actual_retries = Counter(), []
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        order = args[3]["display_order"]
        calls[order] += 1
        outcome = initial[order - 1]
        if calls[order] > 1:
            assert all(calls[i] >= 1 for i in range(1, 5))
            actual_retries.append(order)
            outcome = final.get(order, outcome)
        if outcome in {"cpu", "wall"}:
            return case(order, evidence=timing(outcome))
        return case(order, outcome)

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path, {},
                                    [{"display_order": i} for i in range(1, 9)], "sha")
    assert result.status == expected and result.failed_testcase_order == failed
    assert actual_retries == retries
    assert set(calls) == {1, 2, 3, 4}


def test_ioi_independent_results_survive_timeout_confirmation_and_environment_failure(executor, tmp_path, monkeypatch):
    calls, events = Counter(), []
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        order = args[3]["display_order"]
        calls[order] += 1
        if order == 1:
            return case(order, "wrong_answer")
        if order == 2:
            return case(order, evidence=timing())
        if order == 3:
            return case(order, evidence=timing("watchdog"))
        return case(order, "accepted")

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path,
        {"scoring_mode": "ioi", "collect_all_results": True, "submission": {"submission_kind": "participant"}},
        [{"display_order": i} for i in range(1, 7)], "sha",
        case_progress=lambda value, current, total: events.append((value, current, total)))
    assert calls == Counter({1: 1, 2: 1, 3: 2, 4: 1, 5: 1, 6: 1})
    assert result.status == "system_error" and result.failed_testcase_order == 3
    assert [row["final_status"] for row in result.testcase_results] == [
        "wrong_answer", "time_limit_exceeded", "system_error", "accepted", "accepted", "accepted"]
    assert [event[1:] for event in events] == [(i, 6) for i in range(1, 7)]
    assert all("output_base64" not in row for row in result.testcase_results)


def test_recheck_does_not_expose_the_superseded_output(executor, tmp_path, monkeypatch):
    attempts, events = Counter(), []
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        test = args[3]
        order = test["display_order"]
        attempts[order] += 1
        output = b"discarded" if attempts[order] == 1 else b"final"
        test["_editor_output"] = output
        test["_editor_output_total_bytes"] = len(output)
        return case(order, "time_limit_exceeded" if attempts[order] == 1 else "accepted", timing("wall"))

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path,
        {"collect_all_results": True, "submission": {"submission_kind": "editor_verification"}},
        [{"display_order": 1}], "sha",
        case_progress=lambda value, *args: events.append(value))
    assert result.status == "accepted" and attempts == Counter({1: 2})
    assert len(events) == 1 and events == result.testcase_results
    assert events[0]["output_base64"] == "ZmluYWw="
    assert events[0]["output_total_bytes"] == 5


def test_recheck_failing_before_capture_does_not_reuse_the_first_attempt_output(executor, tmp_path, monkeypatch):
    attempts, events = Counter(), []
    monkeypatch.setattr(executor, "_prepare_checker", lambda *_: None)

    def run(*args):
        test = args[3]
        order = test["display_order"]
        attempts[order] += 1
        if attempts[order] == 1:
            test["_editor_output"] = b"discarded"
            test["_editor_output_total_bytes"] = 9
            return case(order, evidence=timing("wall"))
        # Reading the testcase object failed, before the runner could capture
        # stdout. The old attempt's output is not evidence of this failure.
        return case(order, "system_error")

    monkeypatch.setattr(executor, "_run_single_testcase", run)
    result = executor._run_testcases(["fixture"], tmp_path,
        {"collect_all_results": True, "submission": {"submission_kind": "editor_verification"}},
        [{"display_order": 1}], "sha", case_progress=lambda value, *args: events.append(value))
    assert result.status == "system_error" and attempts == Counter({1: 2})
    assert len(events) == 1 and events == result.testcase_results
    assert "output_base64" not in events[0]
    assert "output_total_bytes" not in events[0]
    assert "output_truncated" not in events[0]


@pytest.mark.parametrize("language,case_limit,override,expected", [
    ("cpp20", None, None, 2.0), ("python313", None, None, 8.0),
    ("java8", None, None, 5.0), ("kotlin", None, None, 5.0),
    ("python313", 1250, None, 1.25), ("python313", None, 3500, 3.5),
])
def test_cpu_proof_uses_the_effective_language_and_testcase_limit(
    executor, language, case_limit, override, expected,
):
    job = {"problem": {"time_limit_ms": 2000}, "submission": {"language": language}}
    if override:
        job["problem"]["language_resource_limits"] = {language: {"time_limit_ms": override}}
    testcase = {"time_limit_ms_override": case_limit} if case_limit else {}
    limit = executor._testcase_time_limit_seconds(job, testcase)
    assert limit == expected
    below = executor._isolate_execution_timing(
        {"status": "TO", "time": str(expected - .01), "time-wall": "30"}, limit, limit * 3)
    reached = executor._isolate_execution_timing(
        {"status": "TO", "time": str(expected), "time-wall": "30"}, limit, limit * 3)
    assert below.timeout_origin == "wall"
    assert reached.timeout_origin == "cpu"


def test_single_case_retains_runner_timing_without_inventing_cpu_evidence(executor, tmp_path, monkeypatch):
    observed = timing("wall")
    monkeypatch.setattr(executor, "_run_command", lambda *args, **kwargs: SimpleNamespace(
        returncode=124, stdout="", stderr="", runtime_ms=50, wall_runtime_ms=3050, memory_kb=100,
        execution_timing=observed))
    result = executor._run_single_testcase(["fixture"], tmp_path,
        {"problem": {"time_limit_ms": 1000}}, {"display_order": 1, "input_text": "", "output_text": ""},
        None, "sha", None)
    assert result.result.status == "time_limit_exceeded"
    assert result.timing == observed
    assert result.result.runtime_ms == 50


@pytest.mark.parametrize("status", ["RE", "OK"])
def test_program_exit_124_is_a_runtime_error_not_a_sandbox_timeout(executor, tmp_path, monkeypatch, status):
    observed = executor._isolate_execution_timing(
        {"status": status, "exitcode": "124", "time": "1.5", "time-wall": "1.6"}, 1.0, 3.0)
    monkeypatch.setattr(executor, "_run_command", lambda *args, **kwargs: SimpleNamespace(
        args=["fixture"], returncode=124, stdout="", stderr="user exit status", runtime_ms=1500,
        wall_runtime_ms=1600, memory_kb=100, execution_timing=observed))
    result = executor._run_single_testcase(["fixture"], tmp_path,
        {"problem": {"time_limit_ms": 1000}}, {"display_order": 1, "input_text": "", "output_text": ""},
        None, "sha", None)
    assert result.result.status == "runtime_error"
    assert not executor._needs_timeout_confirmation(result)


@pytest.mark.parametrize("outer_exit", [0, 1])
def test_isolate_exit_without_metadata_is_not_success(executor, tmp_path, monkeypatch, outer_exit):
    def isolate(command, **kwargs):
        if "--run" in command:
            return subprocess.CompletedProcess(command, outer_exit, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.executor.subprocess.run", isolate)
    completed = executor._run_isolate_box(["fixture"], tmp_path, 1.0, "", tmp_path, 128, 0)
    assert completed.returncode != 0
    assert completed.execution_error
    assert completed.execution_timing.timeout_origin == "unknown"
    monkeypatch.setattr(executor, "_run_command", lambda *args, **kwargs: completed)
    result = executor._run_single_testcase(["fixture"], tmp_path, {},
        {"display_order": 1, "input_text": "", "output_text": ""}, None, "sha", None)
    assert result.result.status == "system_error"
    assert result.result.runtime_ms is None


@pytest.mark.parametrize("meta", [
    "garbage:\n",
    "status:0\ntime:0.1\ntime-wall:0.2\n",
    "status:XX\ntime:0.1\ntime-wall:0.2\n",
    "status:OK\ntime:NaN\ntime-wall:0.2\n",
    "status:RE\nexitcode:124\ntime:Infinity\ntime-wall:0.2\n",
    "status:TO\ntime:-1\ntime-wall:4\n",
    "status:TO\ntime:invalid\ntime-wall:4\n",
    "status:SG\nexitsig:9\ntime:invalid\ntime-wall:0.2\n",
    "exitcode:0\ntime-wall:0.2\n",
])
def test_nonempty_untrustworthy_isolate_metadata_cannot_fabricate_a_verdict(
    executor, tmp_path, monkeypatch, meta,
):
    def isolate(command, **kwargs):
        if "--run" in command:
            meta_file = next(part.split("=", 1)[1] for part in command if part.startswith("--meta="))
            Path(meta_file).write_text(meta, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.executor.subprocess.run", isolate)
    completed = executor._run_isolate_box(["fixture"], tmp_path, 1.0, "", tmp_path, 128, 0)
    assert completed.returncode != 0
    assert completed.execution_error
    assert completed.execution_timing.timeout_origin == "unknown"
    monkeypatch.setattr(executor, "_run_command", lambda *args, **kwargs: completed)
    result = executor._run_single_testcase(["fixture"], tmp_path, {},
        {"display_order": 1, "input_text": "", "output_text": ""}, None, "sha", None)
    assert result.result.status == "system_error"
    assert executor._needs_timeout_confirmation(result)
    if completed.execution_timing.cpu_seconds is None:
        assert result.result.runtime_ms is None


@pytest.mark.parametrize("status,cpu", [("SG", "0.1"), ("TO", "1.1")])
def test_real_oom_evidence_keeps_priority_over_cpu_timeout_evidence(
    executor, tmp_path, monkeypatch, status, cpu,
):
    def isolate(command, **kwargs):
        if "--run" in command:
            meta_file = next(part.split("=", 1)[1] for part in command if part.startswith("--meta="))
            Path(meta_file).write_text(
                f"status:{status}\nexitsig:9\ncg-oom-killed:1\ntime:{cpu}\ntime-wall:1.2\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 1 if "--run" in command else 0, "", "")

    monkeypatch.setattr("app.executor.subprocess.run", isolate)
    completed = executor._run_isolate_box(["fixture"], tmp_path, 1.0, "", tmp_path, 128, 0)
    assert completed.returncode == 137
    assert not getattr(completed, "execution_error", None)
    monkeypatch.setattr(executor, "_run_command", lambda *args, **kwargs: completed)
    result = executor._run_single_testcase(["fixture"], tmp_path, {},
        {"display_order": 1, "input_text": "", "output_text": ""}, None, "sha", None)
    assert result.result.status == "memory_limit_exceeded"
    assert not executor._needs_timeout_confirmation(result)


def test_successful_isolate_metadata_can_omit_status_when_cpu_is_measured(executor, tmp_path, monkeypatch):
    def isolate(command, **kwargs):
        if "--run" in command:
            meta_file = next(part.split("=", 1)[1] for part in command if part.startswith("--meta="))
            Path(meta_file).write_text("exitcode:0\ntime:0.01\ntime-wall:0.02\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.executor.subprocess.run", isolate)
    completed = executor._run_isolate_box(["fixture"], tmp_path, 1.0, "", tmp_path, 128, 0)
    assert completed.returncode == 0
    assert not getattr(completed, "execution_error", None)
    assert completed.execution_timing.cpu_seconds == .01
    assert completed.execution_timing.timeout_origin is None


@pytest.mark.parametrize('language', ['cpp17', 'java8'])
def test_missing_compiler_measurement_is_not_a_participant_compile_error(tmp_path, monkeypatch, language):
    executor = JudgeExecutor(tmp_path, sandbox_mode='isolate')
    completed = subprocess.CompletedProcess(['compiler'], 127, '', '')
    completed.execution_error = '채점 실행의 측정 정보가 없습니다.'
    monkeypatch.setattr(executor, '_run_compiler', lambda *args: completed)
    result = executor._prepare_command(tmp_path, language, 'fixture')
    assert result.status == 'system_error'
    assert result.message == completed.execution_error
