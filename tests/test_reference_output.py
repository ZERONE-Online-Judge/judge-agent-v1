import base64
import copy
import subprocess

import pytest

from app.executor import JudgeExecutor, reference_output


def job(source='print(3)', language='python313'):
    return {'judge_job_id': 'reference-output', 'collect_all_results': True, 'reference_output': True,
            'submission': {'submission_kind': 'editor_verification', 'language': language, 'source_code': source},
            'problem': {'time_limit_ms': 1000, 'memory_limit_mb': 128},
            'testcases': [{'display_order': 1, 'input_text': '1 2\n', 'output_text': 'wrong\n',
                           'input_storage_key': 'in', 'output_storage_key': 'out'}]}


def test_reference_preserves_large_binary_output_and_skips_only_answer_comparison(tmp_path, monkeypatch):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: pytest.fail('Reference production must not compile the checker'))
    size = 400_000
    result = executor.judge(job(f'import sys;sys.stdout.buffer.write(bytes([0,255,13,10])*{size})'))
    assert result.status == 'accepted'
    case = result.testcase_results[0]
    assert base64.b64decode(case['output_base64']) == bytes([0,255,13,10])*size
    assert case['output_total_bytes'] == size*4 and not case['output_truncated']
    monkeypatch.setattr(executor, '_prepare_checker', lambda *_: None)
    ordinary = job()
    ordinary['reference_output'] = False
    assert executor.judge(ordinary).status == 'wrong_answer'


@pytest.mark.parametrize('source,status', [('raise RuntimeError("bad")','runtime_error'), ('while True: pass','time_limit_exceeded')])
def test_reference_cannot_turn_failures_into_answers(tmp_path, source, status):
    request = job(source)
    request['problem']['time_limit_ms'] = 100
    result = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1).judge(request)
    assert result.status == status and result.testcase_results[0]['final_status'] == status


@pytest.mark.parametrize('kind', ['participant', 'operator_test', 'contest', 'verification_trial'])
def test_reference_flag_never_bypasses_checker_for_other_job_kinds(tmp_path, kind):
    request = job()
    request['submission']['submission_kind'] = kind
    assert not reference_output(request)
    assert JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1).judge(request).status == 'wrong_answer'


@pytest.mark.parametrize('language', ['c99','cpp17','python313','pypy3','java8','kotlin'])
@pytest.mark.parametrize('override', [False, True])
def test_reference_and_verification_use_identical_native_resource_limits(tmp_path, monkeypatch, language, override):
    executor = JudgeExecutor(tmp_path, sandbox_mode='local', testcase_parallelism=1)
    limits = []
    def run(command, directory, timeout_seconds, **kwargs):
        limits.append((timeout_seconds, kwargs.get('memory_limit_mb')))
        result = subprocess.CompletedProcess(command, 0, 'wrong\n', '')
        result.cpu_runtime_ms = 12
        result.peak_memory_kb = 1024
        return result
    monkeypatch.setattr(executor, '_run_command', run)
    request = job(language=language)
    if override:
        request['problem']['language_resource_limits'] = {language: {'time_limit_ms': 4321, 'memory_limit_mb': 765}}
    test = request['testcases'][0]
    executor._run_single_testcase(['program'], tmp_path/'reference', request, copy.deepcopy(test), None, 'sha', None)
    request['reference_output'] = False
    executor._run_single_testcase(['program'], tmp_path/'verify', request, copy.deepcopy(test), None, 'sha', None)
    assert limits[0] == limits[1]
    if override:
        assert limits[0] == (4.321, 765)
