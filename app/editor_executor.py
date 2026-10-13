"""Run Editor tools in fixed, network-free containers on this judge VM."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4
from .editor_protocol import PROTOCOL, MAX_WIRE_BYTES, unpack, validate_result
from .object_urls import resolve_object_url
from .settings import settings


class EditorExecutor:
    def __init__(self, runtime_image=None, renderer_image=None):
        self.images = {}
        for kind, label in [("runtime", runtime_image or settings.editor_runtime_image), ("renderer", renderer_image or settings.editor_renderer_image)]:
            if not label:
                continue
            try:
                result = subprocess.run(["docker", "image", "inspect", label, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=10)
                identifier = result.stdout.strip()
                if result.returncode == 0 and identifier.startswith("sha256:") and len(identifier) == 71:
                    self.images[kind] = identifier
            except (OSError, subprocess.SubprocessError):
                pass
        self.capabilities = [PROTOCOL] if set(self.images) == {"runtime", "renderer"} else []
        self.revision = hashlib.sha256(json.dumps(self.images, sort_keys=True).encode()).hexdigest() if self.capabilities else None

    def execute(self, job, cancelled=lambda: False):
        if not self.capabilities:
            raise RuntimeError("Editor 실행 이미지가 준비되지 않았습니다.")
        target = resolve_object_url(job["input_url"], settings.internal_api_base_url, os.getenv("JUDGE_OBJECT_URL_ORIGINS", ""))
        with urlopen(target, timeout=settings.object_read_timeout_seconds) as response:
            archive = response.read(MAX_WIRE_BYTES + 1)
        if len(archive) > MAX_WIRE_BYTES or hashlib.sha256(archive).hexdigest() != job["input_sha256"]:
            raise RuntimeError("Editor 실행 자료의 무결성 검사가 실패했습니다.")
        payload = unpack(archive)
        if payload["runtime"] != job["runtime"]:
            raise RuntimeError("Editor 실행 환경이 일치하지 않습니다.")
        config = payload["config"]
        name = "zoj-editor-task-" + uuid4().hex
        settings.work_root.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="editor-task-", dir=settings.work_root))
        args = ["docker", "run", "-i", "--name", name, "--label", "zoj.editor.node=" + getattr(settings,"node_name","judge-demo"), "--network=none", "--read-only", "--cap-drop=ALL",
                "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=CHOWN", "--cap-add=KILL", "--cap-add=DAC_OVERRIDE",
                "--security-opt=no-new-privileges", "--pids-limit=128", "--cpus=1",
                f"--memory={config['memory_mb'] + 256}m", f"--memory-swap={config['memory_mb'] + 256}m",
                "--ulimit", "nofile=256:256", "--ulimit", "core=0:0", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                "--volume", "/work", "--workdir=/work", self.images[payload["runtime"]]]
        proc = None
        try:
            (folder / "input.json").write_text(json.dumps({"config": config, "files": payload["files"]}))
            with (folder / "input.json").open("rb") as inp, (folder / "result.json").open("wb") as out, (folder / "error.txt").open("wb") as err:
                proc = subprocess.Popen(args, stdin=inp, stdout=out, stderr=err)
                deadline = time.monotonic() + config["wall_seconds"] + 15
                while proc.poll() is None:
                    if cancelled():
                        raise RuntimeError("Editor 작업이 취소되거나 실행 권한이 해제되었습니다.")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Editor 격리 감독 프로세스 응답 시간이 초과되었습니다.")
                    if (folder / "result.json").stat().st_size > MAX_WIRE_BYTES or (folder / "error.txt").stat().st_size > 1024**2:
                        raise RuntimeError("Editor 실행 응답의 용량 한도를 초과했습니다.")
                    time.sleep(0.1)
            if cancelled():
                raise RuntimeError("Editor 작업이 취소되었습니다.")
            if (folder / "result.json").stat().st_size > MAX_WIRE_BYTES:
                raise RuntimeError("Editor 실행 응답의 용량 한도를 초과했습니다.")
            try:
                result = json.loads((folder / "result.json").read_bytes())
            except (ValueError, UnicodeError):
                inspected = subprocess.run(["docker", "inspect", name, "--format", "{{.State.OOMKilled}}"], capture_output=True, text=True, timeout=10)
                if inspected.stdout.strip() == "true":
                    result = {"status": "memory-limit", "cpu_ms": None, "wall_ms": None, "memory_kb": None, "exit_code": 137,
                              "stderr": "컨테이너 OOM으로 자원 계측을 완료하지 못했습니다.", "metrics_available": False}
                else:
                    raise RuntimeError("Editor 격리 실행 환경 오류: " + (folder / "error.txt").read_bytes()[:4000].decode("utf-8", "replace"))
            return validate_result(result, config)
        finally:
            subprocess.run(["docker", "rm", "-fv", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if proc:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            shutil.rmtree(folder, ignore_errors=True)



def cleanup_editor_containers(node_name):
    """Remove this agent's orphaned runs after a crash, not other VM services."""
    import re
    try:
        result = subprocess.run(['docker', 'ps', '-aq', '--filter', 'label=zoj.editor.node='+node_name,
                                 '--filter', 'name=zoj-editor-task-'], capture_output=True, text=True, timeout=10)
        for identifier in result.stdout.split():
            if re.fullmatch(r'[a-f0-9]{12,64}', identifier):
                subprocess.run(['docker','rm','-fv',identifier], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass  # Legacy ZOJ judging can continue without an Editor Docker runtime.
