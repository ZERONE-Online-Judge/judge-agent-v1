from unittest.mock import Mock

import pytest

from app.backend_client import BackendError
from app.executor import ExecutionResult
from app.main import judge_one


JOB = {'judge_job_id': 'job', 'lease_token': 'fixture-lease'}
CASES = [{'index': 1, 'final_status': 'accepted', 'output_base64': 'NQo='}]


def test_reconfirmation_phase_does_not_create_an_extra_case_result():
    client, executor = Mock(), Mock()
    def run(job, progress, case_progress):
        progress('judging', 0, 1, progress_phase='tle_confirmation')
        progress('judging', 0, 1)
        case_progress(CASES[0], 1, 1)
        return ExecutionResult('accepted', testcase_results=CASES)
    executor.judge.side_effect = run
    judge_one(client, executor, JOB)
    updates = client.report_progress.call_args_list
    assert updates[0].kwargs['progress_phase'] == 'tle_confirmation'
    assert updates[1].kwargs['progress_phase'] is None
    assert client.report_result.call_args.kwargs['testcase_results'] == CASES
    assert updates[2].kwargs['testcase_results'] == CASES


def failure(status=422, code='testcase_results_invalid'):
    return BackendError(status, code, '채점 결과 증거 검증에 실패했습니다.', 'request-fixture')


@pytest.mark.parametrize('status,code', [(403, 'forbidden'), (404, 'not_found'), (409, 'lease_conflict')])
@pytest.mark.parametrize('stage', ['progress', 'case', 'result'])
def test_ownership_loss_never_retries_or_reports_a_fallback_result(status, code, stage):
    client, executor = Mock(), Mock()
    error = failure(status, code)
    def run(job, progress, case_progress):
        if stage == 'progress':
            progress('judging', 0, 1)
        elif stage == 'case':
            case_progress(CASES[0], 1, 1)
        return ExecutionResult('accepted', testcase_results=CASES)
    executor.judge.side_effect = run
    if stage == 'result':
        client.report_result.side_effect = error
    else:
        client.report_progress.side_effect = error
    judge_one(client, executor, JOB)
    assert client.report_result.call_count == (1 if stage == 'result' else 0)
    assert client.report_progress.call_count == (0 if stage == 'result' else 1)


@pytest.mark.parametrize('stage', ['case', 'result'])
def test_invalid_evidence_is_not_retried_and_becomes_one_safe_system_error(stage):
    client, executor = Mock(), Mock()
    error = failure()
    def run(job, progress, case_progress):
        if stage == 'case': case_progress(CASES[0], 1, 1)
        return ExecutionResult('accepted', testcase_results=CASES)
    executor.judge.side_effect = run
    if stage == 'case': client.report_progress.side_effect = error
    else: client.report_result.side_effect = [error, None]
    with pytest.raises(BackendError): judge_one(client, executor, JOB)
    assert client.report_result.call_count == (2 if stage == 'result' else 1)
    fallback = client.report_result.call_args.kwargs
    assert fallback['final_status'] == 'system_error'
    assert '증거 검증에 실패' in fallback['judge_message'] and 'request-fixture' in fallback['judge_message']
    assert 'testcase_results' not in fallback
    assert client.report_progress.call_count == (1 if stage == 'case' else 0)


def test_other_conflicts_are_not_misclassified_as_lost_ownership():
    client, executor = Mock(), Mock()
    executor.judge.return_value = ExecutionResult('accepted', testcase_results=CASES)
    client.report_result.side_effect = [failure(409, 'different_conflict'), None]
    with pytest.raises(BackendError): judge_one(client, executor, JOB)
    assert client.report_result.call_count == 2
    assert client.report_result.call_args.kwargs['final_status'] == 'system_error'


def test_transient_typed_failure_retries_the_exact_final_evidence(monkeypatch):
    client, executor = Mock(), Mock()
    executor.judge.return_value = ExecutionResult('accepted', testcase_results=CASES)
    client.report_result.side_effect = [failure(503, 'service_unavailable'), None]
    monkeypatch.setattr('app.main.time.sleep', lambda _: None)
    judge_one(client, executor, JOB)
    assert client.report_result.call_count == 2
    assert client.report_result.call_args_list[0] == client.report_result.call_args_list[1]


@pytest.mark.parametrize('status,code', [(403, 'forbidden'), (404, 'not_found'), (409, 'lease_conflict')])
def test_editor_tasks_stop_result_reporting_after_ownership_loss(status, code):
    client, executor, editor = Mock(), Mock(), Mock()
    editor.execute.return_value = {'status': 'ok'}
    client.report_editor_result.side_effect = failure(status, code)
    judge_one(client, executor, {**JOB, 'task_type': 'editor-execute-v1'}, editor)
    assert client.report_editor_result.call_count == 1
    client.report_result.assert_not_called()


def test_editor_generation_without_testcases_keeps_its_separate_success_path():
    client, executor, editor = Mock(), Mock(), Mock()
    editor.execute.return_value = {'status': 'ok', 'output_base64': 'NQo='}
    judge_one(client, executor, {**JOB, 'task_type': 'editor-execute-v1', 'testcases': []}, editor)
    executor.judge.assert_not_called()
    client.report_editor_result.assert_called_once_with('job', 'fixture-lease', result=editor.execute.return_value)


@pytest.mark.parametrize('editor_task', [False, True])
def test_renewal_ownership_loss_stops_work_and_all_result_reporting(monkeypatch, editor_task):
    class ImmediateEvent:
        def __init__(self): self.value = False
        def wait(self, timeout): return self.value
        def is_set(self): return self.value
        def set(self): self.value = True
    class ImmediateThread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
        def join(self, **kwargs): pass
    monkeypatch.setattr('app.main.Event', ImmediateEvent)
    monkeypatch.setattr('app.main.Thread', ImmediateThread)
    client, executor, editor = Mock(), Mock(), Mock()
    client.renew_lease.side_effect = failure(409, 'lease_conflict')
    job = {**JOB, **({'task_type': 'editor-execute-v1'} if editor_task else {})}
    judge_one(client, executor, job, editor)
    client.renew_lease.assert_called_once()
    executor.judge.assert_not_called(); editor.execute.assert_not_called()
    client.report_result.assert_not_called(); client.report_editor_result.assert_not_called()
    client.report_progress.assert_not_called()


def test_editor_transient_result_failure_preserves_the_original_payload(monkeypatch):
    client, executor, editor = Mock(), Mock(), Mock()
    editor.execute.return_value = {'status': 'ok', 'output_base64': 'NQo='}
    client.report_editor_result.side_effect = [failure(503, 'service_unavailable'), None]
    monkeypatch.setattr('app.main.time.sleep', lambda _: None)
    judge_one(client, executor, {**JOB, 'task_type': 'editor-execute-v1'}, editor)
    assert client.report_editor_result.call_count == 2
    assert client.report_editor_result.call_args_list[0] == client.report_editor_result.call_args_list[1]
