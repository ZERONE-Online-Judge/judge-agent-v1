import base64
from collections import Counter
from pathlib import Path

import pytest

from app.executor import ExecutionResult, JudgeExecutor, TestcaseRunResult as Case


@pytest.mark.parametrize('kind', ['editor_verification', 'operator_test'])
@pytest.mark.parametrize('parallelism', [1, 4])
def test_editor_runs_every_case_after_failures_and_reports_confirmed_verdicts(tmp_path, monkeypatch, parallelism, kind):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=parallelism)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    statuses = ['accepted', 'wrong_answer', 'time_limit_exceeded', 'memory_limit_exceeded', 'runtime_error', 'accepted']
    calls = Counter()
    events = []
    def run(command, directory, job, test, *args):
        index = test['display_order']
        calls[index] += 1
        status = statuses[index - 1]
        test['_editor_output'] = str(index).encode()
        test['_editor_output_total_bytes'] = len(str(index))
        return Case(index, ExecutionResult(status, failed_testcase_order=index if status != 'accepted' else None, runtime_ms=index, memory_kb=index * 10))
    monkeypatch.setattr(executor, '_run_single_testcase', run)
    result = executor._run_testcases(['code'], tmp_path, {'collect_all_results': True, 'submission': {'submission_kind': kind}},
                                    [{'display_order': index} for index in range(1, 7)], 'sha',
                                    case_progress=lambda case, current, total: events.append((case, current, total)))
    assert result.status == 'wrong_answer' and result.failed_testcase_order == 2
    assert [row['final_status'] for row in result.testcase_results] == statuses
    assert [row[1] for row in events] == list(range(1, 7))
    assert all(row[2] == 6 for row in events)
    assert {index: count for index, count in calls.items() if index != 3} == {1: 1, 2: 1, 4: 1, 5: 1, 6: 1}
    assert calls[3] == (2 if parallelism > 1 else 1)  # Existing serial TLE confirmation.
    assert result.runtime_ms == 6 and result.memory_kb == 60
    assert [base64.b64decode(row['output_base64']) for row in result.testcase_results] == [str(index).encode() for index in range(1, 7)]


@pytest.mark.parametrize('kind', ['participant', 'contest', 'participant_preview', 'mock_judging', 'verification_trial'])
def test_contest_jobs_keep_first_failure_semantics_even_with_an_untrusted_flag(tmp_path, monkeypatch, kind):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    calls = []
    def run(command, directory, job, test, *args):
        calls.append(test['display_order'])
        return Case(test['display_order'], ExecutionResult('wrong_answer', failed_testcase_order=test['display_order']))
    monkeypatch.setattr(executor, '_run_single_testcase', run)
    result = executor._run_testcases(['code'], tmp_path, {'collect_all_results': True, 'submission': {'submission_kind': kind}},
                                    [{'display_order': index} for index in range(1, 4)], 'sha')
    assert calls == [1] and result.testcase_results is None


@pytest.mark.parametrize('kind', ['editor_verification', 'operator_test'])
def test_real_stdout_bytes_are_retained_before_sandbox_cleanup(tmp_path, kind):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    result = executor.judge({
        'judge_job_id': 'full-output', 'collect_all_results': True,
        'submission': {'submission_kind': kind, 'language': 'python313',
                       'source_code': 'import sys;sys.stdout.buffer.write(bytes([0,255,13,10]))'},
        'testcases': [{'display_order': index, 'input_text': '', 'output_text': '', 'input_storage_key': 'in', 'output_storage_key': 'out'} for index in [1, 2]],
    })
    assert len(result.testcase_results) == 2
    assert all(base64.b64decode(case['output_base64']) == bytes([0, 255, 13, 10]) for case in result.testcase_results)
    assert all(case['output_total_bytes'] == 4 and not case['output_truncated'] for case in result.testcase_results)
    assert not (tmp_path / 'full-output').exists()


def test_run_output_budget_marks_every_truncated_capture(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    monkeypatch.setattr('app.executor.MAX_RUN_OUTPUT_BYTES', 3)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    def run(command, directory, job, test, *args):
        test['_editor_output'], test['_editor_output_total_bytes'] = b'1234', 4
        return Case(test['display_order'], ExecutionResult('accepted'))
    monkeypatch.setattr(executor, '_run_single_testcase', run)
    result = executor._run_testcases(['code'], tmp_path, {'collect_all_results': True, 'submission': {'submission_kind': 'editor_verification'}},
                                    [{'display_order': index} for index in [1, 2]], 'sha')
    assert [base64.b64decode(case['output_base64']) for case in result.testcase_results] == [b'123', b'']
    assert all(case['output_truncated'] and case['output_total_bytes'] == 4 for case in result.testcase_results)


def test_progress_upload_failure_and_final_retry_preserve_all_evidence(monkeypatch):
    from unittest.mock import Mock
    from app.main import judge_one
    client, executor = Mock(), Mock()
    cases = [{'index': index, 'final_status': 'accepted', 'output_base64': 'NQo=',
              'output_total_bytes': 2, 'output_truncated': False} for index in range(1, 20)]
    def execute(job, progress, case_progress):
        for current, case in enumerate(cases, 1):
            case_progress(case, current, len(cases))
        return ExecutionResult('accepted', testcase_results=cases)
    executor.judge.side_effect = execute
    client.report_progress.side_effect = [RuntimeError('temporary outage'), None, None]
    client.report_result.side_effect = [RuntimeError('reply lost after commit'), None]
    monkeypatch.setattr('app.main.time.sleep', lambda _: None)
    judge_one(client, executor, {'judge_job_id': 'run', 'lease_token': 'lease'})
    batches = [call.kwargs['testcase_results'] for call in client.report_progress.call_args_list]
    assert [len(batch) for batch in batches] == [1, 16, 3]
    assert batches[1] + batches[2] == cases
    assert client.report_result.call_count == 2
    assert all(call.kwargs['testcase_results'] == cases for call in client.report_result.call_args_list)
    assert all(call.kwargs['final_status'] == 'accepted' for call in client.report_result.call_args_list)


def test_real_operator_code_continues_after_wa_re_and_timeout(tmp_path):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    source = """n=int(input())
if n == 1:
    print('wrong')
elif n == 2:
    raise RuntimeError('intentional fixture failure')
elif n == 3:
    while True: pass
else:
    print('ok')
"""
    result = executor.judge({
        'judge_job_id': 'operator-mixed-real', 'collect_all_results': True,
        'problem': {'time_limit_ms': 100, 'memory_limit_mb': 128},
        'submission': {'submission_kind': 'operator_test', 'language': 'python313', 'source_code': source},
        'testcases': [{'display_order': i+1, 'input_text': str(i), 'output_text': 'ok\n',
                       'input_storage_key': 'in', 'output_storage_key': 'out'} for i in range(5)]})
    assert [case['final_status'] for case in result.testcase_results] == [
        'accepted','wrong_answer','runtime_error','time_limit_exceeded','accepted']
    assert result.status == 'wrong_answer' and result.failed_testcase_order == 2
    assert base64.b64decode(result.testcase_results[-1]['output_base64']) == b'ok\n'


def test_large_editor_stdout_does_not_change_contest_output_limit(tmp_path):
    size = 17 * 1024**2
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1, output_limit_bytes=10*1024**2)
    def job(kind):
        return {'judge_job_id': 'large-output-'+kind, 'collect_all_results': True,
                'submission': {'submission_kind': kind, 'language':'python313',
                               'source_code': f'import sys;sys.stdout.write("x"*{size})'},
                'testcases': [{'display_order':1,'input_text':'','output_text':'x'*size,
                               'input_storage_key':'in','output_storage_key':'out'}]}
    editor = executor.judge(job('editor_verification'))
    assert editor.status == 'accepted'
    assert editor.testcase_results[0]['output_total_bytes'] == size
    assert editor.testcase_results[0]['output_truncated']
    assert executor.output_limit_bytes == 10*1024**2
    participant = executor.judge(job('participant'))
    assert participant.status == 'output_limit_exceeded'
    assert executor.output_limit_bytes == 10*1024**2
