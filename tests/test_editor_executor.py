import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os
import pytest
from app.editor_protocol import FORMAT, pack
from app.editor_executor import EditorExecutor
from app import editor_executor


def wire(files, command, **extra):
    config={'command':command,'cpu_ms':1000,'memory_mb':128,'output_bytes':1048576,'wall_seconds':8,
            'output_file':'stdout','compile_language':None,'export_files':[], **extra}
    files={'.input':b'', **files}
    return {'format':FORMAT,'operation_id':uuid4().hex,'job_kind':'verify','runtime':'runtime','config':config,
            'files':{name:{'base64':base64.b64encode(value[0] if isinstance(value,tuple) else value).decode(),
                          'executable':value[1] if isinstance(value,tuple) else False} for name,value in files.items()}}


@pytest.fixture
def native(tmp_path,monkeypatch):
    if os.environ.get('RUN_EDITOR_EXECUTOR_TESTS')!='1':
        pytest.skip('Actual Editor Docker runtime images required')
    monkeypatch.setattr(editor_executor,'settings',SimpleNamespace(work_root=tmp_path/'work',internal_api_base_url='http://localhost/api',object_read_timeout_seconds=10,
                         editor_runtime_image=os.getenv('JUDGE_EDITOR_RUNTIME_IMAGE','zoj-editor-runtime:agent-v1'),
                         editor_renderer_image=os.getenv('JUDGE_EDITOR_RENDERER_IMAGE','zoj-editor-renderer:agent-v1')))
    executor=EditorExecutor()
    assert executor.capabilities, 'Build both Editor execution images'
    def run(value,cancelled=lambda:False):
        archive=pack(value);path=tmp_path/(uuid4().hex+'.zip');path.write_bytes(archive)
        return executor.execute({'input_url':path.as_uri(),'input_sha256':hashlib.sha256(archive).hexdigest(),'runtime':value['runtime']},cancelled)
    return run


def test_editor_tasks_never_call_the_contest_executor(monkeypatch):
    from app.main import judge_one
    from unittest.mock import Mock
    client,judge,tools=Mock(),Mock(),Mock()
    tools.execute.return_value={'status':'ok'}
    job={'task_type':'editor-execute-v1','judge_job_id':'task','lease_token':'lease'}
    judge_one(client,judge,job,tools)
    judge.judge.assert_not_called();client.report_result.assert_not_called()
    client.report_editor_result.assert_called_once_with('task','lease',result={'status':'ok'})


def test_unready_runtime_does_not_advertise_editor_support(monkeypatch):
    monkeypatch.setattr(editor_executor.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1,stdout=''))
    assert EditorExecutor('missing','missing').capabilities==[]


def test_native_cpp_compiles_transfers_artifact_and_runs(native):
    value=wire({'main.cpp':b'#include <cstdio>\nint main(){int a,b;scanf("%d %d",&a,&b);printf("%d\\n",a+b);}'},['g++','-O2','main.cpp','-o','program'],compile_language='cpp17',cpu_ms=30000,memory_mb=2048,wall_seconds=60)
    compiled=native(value);assert compiled['status']=='ok' and compiled['exit_code']==0
    binary=base64.b64decode(compiled['artifacts']['program'])
    result=native(wire({'program':(binary,True),'.input':b'40 2\n'},['./program']))
    assert base64.b64decode(result['output_base64'])==b'42\n' and result['cpu_ms']>0


def test_native_python_limits_and_host_isolation(native):
    attack=b'''import os,socket
from pathlib import Path
assert not Path('/var/run/docker.sock').exists()
assert 'JUDGE_NODE_SECRET' not in os.environ
try: Path('../.editor-job.json').read_text(); raise AssertionError('read supervisor config')
except PermissionError: pass
try: socket.create_connection(('1.1.1.1',443),timeout=.1); raise AssertionError('network access')
except OSError: pass
print('SAFE')
'''
    result=native(wire({'safe.py':attack},['python3','safe.py']))
    assert result['exit_code']==0 and base64.b64decode(result['output_base64'])==b'SAFE\n'
    result=native(wire({'loop.py':b'while True: pass'},['python3','loop.py'],cpu_ms=250))
    assert result['status']=='time-limit' and result['cpu_ms']>=240
    result=native(wire({'memory.py':b'a=[]\nwhile True: a.append(bytearray(1024*1024))\n'},['python3','memory.py'],memory_mb=32))
    assert result['status']=='memory-limit'


def test_native_interactive_execution_keeps_actor_separate(native):
    value=wire({'solution/sum.py':b'x=int(input());print(x*2,flush=True)\n',
                'interactor/actor.py':b'import sys\nx=int(open(sys.argv[1]).read());print(x,flush=True);assert int(input())==2*x\n',
                'interactor/input':b'21\n','interactor/answer':b'42\n'},['python3','sum.py'],interactive=True,interactor_command=['python3','actor.py','input','transcript','answer'])
    result=native(value);assert result['status']=='ok' and result['exit_code']==0 and result['interactor_exit_code']==0


def test_native_pdf_export_is_bounded_and_offline(native):
    value=wire({'statement.tex':b'\\documentclass{article}\n\\begin{document}Hello $a+b$\\end{document}'},
               ['xelatex','-no-shell-escape','-halt-on-error','-interaction=nonstopmode','statement.tex'],cpu_ms=60000,memory_mb=1024,wall_seconds=90,export_files=['statement.pdf'])
    value['runtime']='renderer';value['job_kind']='render'
    result=native(value)
    assert result['status']=='ok' and result['exit_code']==0
    assert base64.b64decode(result['artifacts']['statement.pdf']).startswith(b'%PDF-')


def test_cancelled_native_task_leaves_no_work_files(native):
    with pytest.raises(RuntimeError,match='취소'):
        native(wire({'loop.py':b'while True:pass'},['python3','loop.py']),lambda:True)
    assert not list(editor_executor.settings.work_root.iterdir())


def test_editor_inputs_use_the_configured_private_object_proxy(monkeypatch):
    monkeypatch.setattr(editor_executor.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='sha256:'+'a'*64))
    monkeypatch.setenv('JUDGE_OBJECT_URL_ORIGINS','https://zoj.kr')
    monkeypatch.setattr(editor_executor,'settings',SimpleNamespace(internal_api_base_url='https://10.10.10.110:6443/api',object_read_timeout_seconds=10,editor_runtime_image='runtime',editor_renderer_image='renderer'))
    def read(target,timeout):
        assert target=='https://10.10.10.110:6443/minio/private/test?signature=unchanged'
        raise RuntimeError('private proxy checked')
    monkeypatch.setattr(editor_executor,'urlopen',read)
    with pytest.raises(RuntimeError,match='private proxy checked'):
        EditorExecutor().execute({'input_url':'https://zoj.kr/minio/private/test?signature=unchanged'})


def test_startup_cleanup_removes_only_own_labelled_editor_runs(monkeypatch):
    calls=[]
    def execute(args,**kw):
        calls.append(args)
        return SimpleNamespace(stdout='a'*12+'\ninvalid-id\n',returncode=0)
    monkeypatch.setattr(editor_executor.subprocess,'run',execute)
    editor_executor.cleanup_editor_containers('approved-node')
    assert calls[0]==['docker','ps','-aq','--filter','label=zoj.editor.node=approved-node','--filter','name=zoj-editor-task-']
    assert calls[1]==['docker','rm','-fv','a'*12]
    assert len(calls)==2

@pytest.mark.parametrize('language',['pypy3','kotlin'])
def test_native_icpc_compiler_artifact_and_execution(native,language):
    if language=='pypy3':
        name='sum.py';code=b'a,b=map(int,input().split());print(a+b)\n'
        compiler=['pypy3','-m','py_compile',name]
    else:
        name='Main.kt';code=b'fun main(){val(a,b)=readln().split(" ").map{it.toLong()};println(a+b)}'
        compiler=['kotlinc','-J-Xmx512m','-J-XX:ActiveProcessorCount=1',name,'-include-runtime','-d','program.jar']
    compiled=native(wire({name:code},compiler,compile_language=language,cpu_ms=30000,memory_mb=2048,wall_seconds=60,output_bytes=64*1024*1024))
    assert compiled['status']=='ok' and compiled['exit_code']==0,compiled
    files={name:code,'.input':b'40 2\n'}
    if language=='kotlin':
        assert set(compiled['artifacts'])=={'program.jar'}
        files['program.jar']=base64.b64decode(compiled['artifacts']['program.jar'])
        from app.runtime_policy import with_runtime_limits
        command=with_runtime_limits(['java','-jar','program.jar'],256)
    else:
        command=['pypy3',name]
    result=native(wire(files,command,memory_mb=256,cpu_ms=2000))
    assert result['status']=='ok' and result['exit_code']==0,result
    assert base64.b64decode(result['output_base64'])==b'42\n'
    assert result['cpu_ms']>0 and result['memory_kb']>0
