from pathlib import Path
from types import SimpleNamespace

import pytest

from app.executor import ExecutionResult, JudgeExecutor, PreparedChecker, TestcaseRunResult as Case, ZOJ_SUBTASKS


@pytest.mark.parametrize('kind', ['participant', 'participant_preview', 'mock_judging', 'operator_test'])
@pytest.mark.parametrize('parallelism', [1, 4])
def test_ioi_assignment_collects_every_independent_subtask(tmp_path, monkeypatch, kind, parallelism):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=parallelism)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    calls, progress = [], []
    outcomes = ['wrong_answer', 'accepted', 'runtime_error', 'accepted', 'system_error', 'accepted']

    def run(command, directory, job, test, *args):
        order = test['display_order']
        calls.append(order)
        return Case(order, ExecutionResult(outcomes[order - 1], failed_testcase_order=order,
                                           runtime_ms=order, memory_kb=order * 10))

    monkeypatch.setattr(executor, '_run_single_testcase', run)
    result = executor._run_testcases(['code'], tmp_path,
        {'scoring_mode': 'ioi', 'collect_all_results': True, 'submission': {'submission_kind': kind}},
        [{'display_order': i} for i in range(1, 7)], 'sha',
        case_progress=lambda value, current, total: progress.append((value, current, total)))
    assert sorted(calls) == list(range(1, 7))
    assert [case['final_status'] for case in result.testcase_results] == outcomes
    assert result.status == 'system_error' and result.failed_testcase_order == 5
    assert result.runtime_ms == 6 and result.memory_kb == 60
    assert [event[1:] for event in progress] == [(i, 6) for i in range(1, 7)]
    assert all('score' not in case and 'output_base64' not in case for case in result.testcase_results)
    assert ZOJ_SUBTASKS == 'zoj-judge-subtasks-v1'


@pytest.mark.parametrize('returncode,expected', [(0, None), (1, 'wrong_answer'), (2, 'wrong_answer'),
                                               (3, 'system_error'), (7, 'system_error'),
                                               (124, 'system_error'), (-9, 'system_error')])
def test_ioi_checker_fault_is_not_a_scored_wrong_answer(tmp_path, monkeypatch, returncode, expected):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    monkeypatch.setattr(executor, '_run_command', lambda *a, **kw:
        SimpleNamespace(returncode=returncode, stdout='', stderr='fixture checker result'))
    checker = PreparedChecker(['checker'], tmp_path)
    result = executor._run_checker(checker, tmp_path, 1, '', '', '', 'test 1', strict_errors=True)
    assert (result.status if result else None) == expected
    if result and expected == 'system_error':
        assert result.failed_testcase_order == 1 and f'exit {returncode}' in result.message


def test_icpc_checker_fault_behavior_is_unchanged(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    monkeypatch.setattr(executor, '_run_command', lambda *a, **kw:
        SimpleNamespace(returncode=3, stdout='', stderr='fixture checker result'))
    result = executor._run_checker(PreparedChecker(['checker'], tmp_path), tmp_path, 1, '', '', '', 'test 1')
    assert result.status == 'wrong_answer'


def test_real_ioi_participant_keeps_later_points_and_does_not_capture_hidden_outputs(tmp_path):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    result = executor.judge({
        'judge_job_id': 'ioi-independent-groups', 'scoring_mode': 'ioi', 'collect_all_results': True,
        'problem': {'time_limit_ms': 100, 'memory_limit_mb': 128},
        'submission': {'submission_kind': 'participant', 'language': 'python313',
                       'source_code': 'n=int(input());print("wrong" if n == 1 else "ok")'},
        'testcases': [{'display_order': i, 'input_text': str(i), 'output_text': 'ok\n',
                       'input_storage_key': 'in', 'output_storage_key': 'out'} for i in range(1, 4)]})
    assert result.status == 'wrong_answer'
    assert [row['final_status'] for row in result.testcase_results] == ['wrong_answer', 'accepted', 'accepted']
    assert all('output_base64' not in row for row in result.testcase_results)
    assert not (tmp_path / 'ioi-independent-groups').exists()


@pytest.mark.parametrize('failure', [3, 7])
def test_real_ioi_checker_failure_is_evidence_of_a_judge_error_not_zero_points(tmp_path, monkeypatch, failure):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    directory = tmp_path / 'prepared-checker'
    directory.mkdir()
    script = directory / 'check.py'
    script.write_text(f'import pathlib,sys;sys.exit({failure} if pathlib.Path(sys.argv[1]).read_text().strip() == "1" else 0)')
    import sys
    checker = PreparedChecker([sys.executable, str(script)], directory)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: checker)
    result = executor.judge({
        'judge_job_id': 'ioi-checker-fault', 'scoring_mode': 'ioi', 'collect_all_results': True,
        'problem': {'time_limit_ms': 100, 'memory_limit_mb': 128},
        'submission': {'submission_kind': 'participant', 'language': 'python313', 'source_code': 'print("ok")'},
        'testcases': [{'display_order': i, 'input_text': str(i), 'output_text': 'ok\n',
                       'input_storage_key': 'in', 'output_storage_key': 'out'} for i in range(1, 3)]})
    assert result.status == 'system_error'
    assert [row['final_status'] for row in result.testcase_results] == ['system_error', 'accepted']


@pytest.mark.parametrize('mode,expected,failed', [('ioi', 'system_error', 2), ('icpc', 'wrong_answer', 1)])
@pytest.mark.parametrize('parallelism', [1, 4])
def test_ioi_judge_error_after_a_wrong_answer_preserves_error_for_retry(tmp_path, monkeypatch, mode, expected, failed, parallelism):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=parallelism)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    outcomes = [('wrong_answer', 'ordinary wrong answer'), ('system_error', 'checker crashed'),
                ('accepted', None)]
    def run(command, directory, job, test, *args):
        index = test['display_order']
        status, message = outcomes[index - 1]
        return Case(index, ExecutionResult(status, message, index if status != 'accepted' else None))
    monkeypatch.setattr(executor, '_run_single_testcase', run)
    result = executor._run_testcases(['code'], tmp_path,
        {'scoring_mode': mode, 'collect_all_results': True, 'submission': {'submission_kind': 'operator_test'}},
        [{'display_order': i} for i in range(1, 4)], 'sha')
    assert result.status == expected and result.failed_testcase_order == failed
    assert result.message == outcomes[failed - 1][1]
    assert len(result.testcase_results) == 3
