import json
from unittest.mock import Mock

import pytest

from app.executor import JudgeExecutor, ExecutionResult


@pytest.mark.parametrize('job_fields', [
    {'testcases': []}, {'testcases': None}, {'bundle_url': 'fixture://empty'},
    {'submission_kind': 'participant'}, {'submission_kind': 'operator_test'},
    {'submission_kind': 'editor_verification'},
])
def test_empty_test_based_job_fails_before_compile_or_execution(tmp_path, monkeypatch, job_fields):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    job = {'judge_job_id': 'empty', 'submission': {'language': 'python313', 'source_code': 'print(42)'}}
    if 'submission_kind' in job_fields:
        job['submission'].update(job_fields)
    else:
        job.update(job_fields)
    monkeypatch.setattr(executor, '_read_object_bytes', lambda *a, **kw: json.dumps({'testcases': []}).encode())
    prepare, run = Mock(), Mock()
    monkeypatch.setattr(executor, '_prepare_command', prepare)
    monkeypatch.setattr(executor, '_run_final', run)
    result = executor.judge(job)
    assert result.status == 'system_error' and '채점할 활성 테스트가 없습니다' in result.message
    assert result.testcase_results is None
    prepare.assert_not_called(); run.assert_not_called()
    assert not (tmp_path/'empty').exists()


def test_bundle_hydration_can_supply_a_nonempty_suite(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    case = {'display_order': 1, 'input_text': '', 'output_text': '42\n'}
    monkeypatch.setattr(executor, '_read_object_bytes', lambda *a, **kw: json.dumps({'testcases': [case]}).encode())
    monkeypatch.setattr(executor, '_prepare_command', lambda *a: ['program'])
    run = Mock(return_value=ExecutionResult('accepted'))
    monkeypatch.setattr(executor, '_run_testcases', run)
    result = executor.judge({'judge_job_id': 'hydrated', 'bundle_url': 'fixture://suite', 'testcases': [],
                             'submission': {'language': 'python313', 'source_code': 'print(42)'}})
    assert result.status == 'accepted' and run.call_args.args[3] == [case]


def test_failed_bundle_without_testcases_never_runs_empty_stdin(tmp_path, monkeypatch, capsys):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local')
    monkeypatch.setattr(executor, '_read_object_bytes', Mock(side_effect=OSError('fixture unavailable token=PRIVATE_TOKEN')))
    result = executor.judge({'judge_job_id': 'missing-bundle', 'bundle_url': 'fixture://missing',
                             'submission': {'language': 'python313', 'source_code': 'print(42)'}})
    assert result.status == 'system_error' and '활성 테스트' in result.message
    assert 'PRIVATE_TOKEN' not in capsys.readouterr().out
