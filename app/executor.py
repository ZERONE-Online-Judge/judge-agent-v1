from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import signal
import shutil
import subprocess
import tempfile
import time
import hashlib
from urllib.parse import urljoin
from urllib.request import urlopen
from collections.abc import Callable

from app.settings import settings


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    score: int | None
    message: str | None = None
    failed_testcase_order: int | None = None


@dataclass(frozen=True)
class PreparedChecker:
    command: list[str]
    cwd: Path


ProgressCallback = Callable[[str, int | None, int | None], None]


class JudgeExecutor:
    def __init__(self, work_root: Path | None = None, sandbox_mode: str | None = None, output_limit_bytes: int | None = None) -> None:
        self.work_root = work_root or settings.work_root
        self.sandbox_mode = sandbox_mode or settings.sandbox_mode
        self.output_limit_bytes = output_limit_bytes or settings.output_limit_bytes
        self.work_root.mkdir(parents=True, exist_ok=True)

    def judge(self, job: dict, progress: ProgressCallback | None = None) -> ExecutionResult:
        submission = job["submission"]
        language = submission["language"]
        source_code = submission["source_code"]
        job_dir = self.work_root / job["judge_job_id"]
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True)

        try:
            testcase_total = len(job.get("testcases") or []) or None
            if progress:
                progress("preparing", 0 if testcase_total is not None else None, testcase_total)
            prepared = self._prepare_command(job_dir, language, source_code)
            if isinstance(prepared, ExecutionResult):
                return prepared
            testcases = job.get("testcases") or []
            if testcases:
                source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
                return self._run_testcases(prepared, job_dir, job, testcases, source_hash, progress)
            return self._run_final(prepared, job_dir, job)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _prepare_command(self, job_dir: Path, language: str, source_code: str) -> list[str] | ExecutionResult:
        if language == "python313":
            return self._prepare_python(job_dir, source_code)
        if language == "cpp17":
            return self._compile(job_dir, "main.cpp", source_code, ["g++", "-std=c++17", "-O2", "main.cpp", "-o", "main"], [str(job_dir / "main")])
        if language == "c99":
            return self._compile(job_dir, "main.c", source_code, ["gcc", "-std=c99", "-O2", "main.c", "-o", "main"], [str(job_dir / "main")])
        if language == "java8":
            return self._prepare_java(job_dir, source_code)
        return ExecutionResult("system_error", None, f"unsupported language: {language}")

    def _prepare_python(self, job_dir: Path, source_code: str) -> list[str]:
        source = job_dir / "main.py"
        source.write_text(source_code, encoding="utf-8")
        return ["python3.13" if shutil.which("python3.13") else "python3", str(source)]

    def _compile(self, job_dir: Path, filename: str, source_code: str, compile_command: list[str], run_command: list[str]) -> list[str] | ExecutionResult:
        (job_dir / filename).write_text(source_code, encoding="utf-8")
        compiled = self._run_command(compile_command, job_dir, timeout_seconds=20)

        if compiled.returncode != 0:
            return ExecutionResult("compile_error", None, compiled.stderr[-4000:] or compiled.stdout[-4000:])
        return run_command

    def _prepare_java(self, job_dir: Path, source_code: str) -> list[str] | ExecutionResult:
        (job_dir / "Main.java").write_text(source_code, encoding="utf-8")
        compiled = self._run_command(["javac", "--release", "8", "Main.java"], job_dir, timeout_seconds=20)
        if compiled.returncode != 0:
            return ExecutionResult("compile_error", None, compiled.stderr[-4000:] or compiled.stdout[-4000:])
        return ["java", "-cp", str(job_dir), "Main"]

    def _run_final(self, command: list[str], job_dir: Path, job: dict) -> ExecutionResult:
        completed = self._run_command(command, job_dir, timeout_seconds=self._problem_time_limit_seconds(job), stdin="")
        if completed.returncode == 0:
            return ExecutionResult("accepted", self._problem_max_score(job), None)
        if completed.returncode == 124:
            return ExecutionResult("time_limit_exceeded", 0, "time limit exceeded")
        if completed.returncode == 125:
            return ExecutionResult("output_limit_exceeded", 0, "output limit exceeded")
        if completed.returncode in {137, -signal.SIGKILL}:
            return ExecutionResult("memory_limit_exceeded", 0, "memory limit exceeded")
        return ExecutionResult("runtime_error", 0, completed.stderr[-4000:] or completed.stdout[-4000:])

    def _run_testcases(
        self,
        command: list[str],
        job_dir: Path,
        job: dict,
        testcases: list[dict],
        source_hash: str,
        progress: ProgressCallback | None = None,
    ) -> ExecutionResult:
        checker = self._prepare_checker(job_dir, job.get("package_files") or [])
        if isinstance(checker, ExecutionResult):
            return checker
        sandbox_container_id: str | None = None
        if self.sandbox_mode == "docker":
            started = self._start_sandbox_container(job_dir)
            if isinstance(started, ExecutionResult):
                return started
            sandbox_container_id = started
        total = len(testcases)
        if progress:
            progress("judging", 0, total)
        try:
            for index, testcase in enumerate(testcases, start=1):
                input_text = testcase.get("input_text") if isinstance(testcase.get("input_text"), str) else self._read_object(testcase.get("input_url"), testcase["input_storage_key"])
                expected_text = testcase.get("output_text") if isinstance(testcase.get("output_text"), str) else self._read_object(testcase.get("output_url"), testcase["output_storage_key"])
                normalized_input = self._normalize_input_text(input_text)
                completed = self._run_command(
                    command,
                    job_dir,
                    timeout_seconds=self._testcase_time_limit_seconds(job, testcase),
                    stdin=normalized_input,
                    sandbox_container_id=sandbox_container_id,
                )
                if completed.returncode == 124:
                    return ExecutionResult("time_limit_exceeded", 0, f"time limit exceeded on testcase {testcase['display_order']}", int(testcase["display_order"]))
                if completed.returncode == 125:
                    return ExecutionResult("output_limit_exceeded", 0, f"output limit exceeded on testcase {testcase['display_order']}", int(testcase["display_order"]))
                if completed.returncode in {137, -signal.SIGKILL}:
                    return ExecutionResult("memory_limit_exceeded", 0, f"memory limit exceeded on testcase {testcase['display_order']}", int(testcase["display_order"]))
                if completed.returncode != 0:
                    return ExecutionResult("runtime_error", 0, (completed.stderr or completed.stdout)[-4000:], int(testcase["display_order"]))
                case_label = self._testcase_label(testcase)
                if checker:
                    checker_result = self._run_checker(
                        checker,
                        job_dir,
                        testcase["display_order"],
                        normalized_input,
                        expected_text,
                        completed.stdout,
                        case_label,
                        source_hash,
                        sandbox_container_id=sandbox_container_id if settings.checker_sandbox_mode == "docker" else None,
                    )
                    if checker_result:
                        return checker_result
                    if progress:
                        progress("judging", index, total)
                    continue
                expected = self._normalize_output(expected_text)
                actual = self._normalize_output(completed.stdout)
                if actual != expected:
                    return ExecutionResult(
                        "wrong_answer",
                        0,
                        self._build_wrong_answer_message(
                            int(testcase["display_order"]),
                            case_label,
                            "wrong answer",
                            input_text,
                            expected_text,
                            completed.stdout,
                            str(testcase.get("input_storage_key") or ""),
                            str(testcase.get("output_storage_key") or ""),
                            source_hash,
                        ),
                        int(testcase["display_order"]),
                    )
                if progress:
                    progress("judging", index, total)
            return ExecutionResult("accepted", self._problem_max_score(job), None)
        finally:
            if sandbox_container_id:
                self._stop_sandbox_container(sandbox_container_id)

    def _prepare_checker(self, job_dir: Path, package_files: list[dict]) -> PreparedChecker | ExecutionResult | None:
        checker_files = [item for item in package_files if item.get("role") == "checker"]
        if not checker_files:
            return None
        checker_file = checker_files[-1]
        checker_dir = job_dir / "checker"
        checker_dir.mkdir(parents=True, exist_ok=True)
        for resource in [item for item in package_files if item.get("role") == "package-resource"]:
            resource_name = Path(resource.get("original_filename") or resource["storage_key"]).name
            (checker_dir / resource_name).write_bytes(self._read_object_bytes(resource.get("url"), resource["storage_key"]))

        filename = Path(checker_file.get("original_filename") or checker_file["storage_key"]).name
        source_path = checker_dir / filename
        source_path.write_bytes(self._read_object_bytes(checker_file.get("url"), checker_file["storage_key"]))
        suffix = source_path.suffix.lower()
        if suffix == ".py":
            return PreparedChecker(["python3.13" if shutil.which("python3.13") else "python3", str(source_path)], checker_dir)
        if suffix in {".cpp", ".cc", ".cxx"}:
            compiled = self._run_command(
                ["g++", "-std=c++17", "-O2", filename, "-o", "checker"],
                checker_dir,
                timeout_seconds=20,
                sandbox_mode_override=settings.checker_sandbox_mode,
            )
            if compiled.returncode != 0:
                return ExecutionResult("system_error", None, "checker compile failed: " + (compiled.stderr or compiled.stdout)[-4000:])
            return PreparedChecker([str(checker_dir / "checker")], checker_dir)
        if suffix == ".c":
            compiled = self._run_command(
                ["gcc", "-std=c99", "-O2", filename, "-o", "checker"],
                checker_dir,
                timeout_seconds=20,
                sandbox_mode_override=settings.checker_sandbox_mode,
            )
            if compiled.returncode != 0:
                return ExecutionResult("system_error", None, "checker compile failed: " + (compiled.stderr or compiled.stdout)[-4000:])
            return PreparedChecker([str(checker_dir / "checker")], checker_dir)
        return ExecutionResult("system_error", None, f"unsupported checker file: {filename}")

    def _run_checker(
        self,
        checker: PreparedChecker,
        job_dir: Path,
        order: int,
        input_text: str,
        expected: str,
        actual: str,
        case_label: str,
        source_hash: str | None = None,
        sandbox_container_id: str | None = None,
    ) -> ExecutionResult | None:
        case_dir = checker.cwd / "checker-cases" / f"{order:03d}"
        case_dir.mkdir(parents=True, exist_ok=True)
        input_path = case_dir / "input.txt"
        expected_path = case_dir / "expected.txt"
        actual_path = case_dir / "actual.txt"
        input_path.write_text(input_text, encoding="utf-8")
        expected_path.write_text(expected, encoding="utf-8")
        actual_path.write_text(actual, encoding="utf-8")
        completed = self._run_command(
            # testlib checker convention:
            # argv[1]=input, argv[2]=participant output, argv[3]=jury(answer) output
            checker.command + [str(input_path), str(actual_path), str(expected_path)],
            checker.cwd,
            timeout_seconds=10,
            sandbox_mode_override=settings.checker_sandbox_mode,
            sandbox_container_id=sandbox_container_id,
        )
        if completed.returncode == 0:
            return None
        detail = (completed.stderr or completed.stdout or f"checker rejected testcase {order}")[-4000:]
        return ExecutionResult(
            "wrong_answer",
            0,
            self._build_wrong_answer_message(order, case_label, detail, input_text, expected, actual, source_hash=source_hash),
            order,
        )

    def _testcase_label(self, testcase: dict) -> str:
        input_name = Path(str(testcase.get("input_storage_key") or "")).name
        output_name = Path(str(testcase.get("output_storage_key") or "")).name
        return f"{input_name} / {output_name}"

    def _read_object(self, url: str | None, storage_key: str) -> str:
        return self._read_object_bytes(url, storage_key).decode("utf-8-sig")

    def _normalize_input_text(self, text: str) -> str:
        # Some uploaded testcase files include invisible characters (BOM/NBSP)
        # that can break tokenized numeric parsing in native languages.
        return text.replace("\ufeff", "").replace("\u00a0", " ")

    def _build_wrong_answer_message(
        self,
        order: int,
        case_label: str,
        summary: str,
        input_text: str,
        expected_text: str,
        actual_text: str,
        input_storage_key: str | None = None,
        output_storage_key: str | None = None,
        source_hash: str | None = None,
    ) -> str:
        storage_hint = ""
        if input_storage_key or output_storage_key:
            storage_hint = (
                f"\n[input_storage_key] {input_storage_key or '-'}"
                f"\n[output_storage_key] {output_storage_key or '-'}"
            )
        source_hint = f"\n[source_sha256] {source_hash}" if source_hash else ""
        return (
            f"testcase #{order} ({case_label}): {summary}\n"
            f"{storage_hint}\n"
            f"{source_hint}\n"
            f"[input]\n{self._snippet(input_text)}\n"
            f"[expected]\n{self._snippet(expected_text)}\n"
            f"[actual]\n{self._snippet(actual_text)}"
        )

    def _snippet(self, text: str, limit: int = 2000) -> str:
        normalized = text.replace("\r\n", "\n")
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit]}\n...(truncated {len(normalized) - limit} chars)"

    def _read_object_bytes(self, url: str | None, storage_key: str) -> bytes:
        if url:
            with urlopen(self._absolute_url(url), timeout=20) as response:
                return response.read()
        path = Path(storage_key)
        if not path.is_absolute():
            path = self.work_root / "objects" / storage_key
        return path.read_bytes()

    def _absolute_url(self, url: str) -> str:
        if url.startswith("/"):
            return urljoin(settings.internal_api_base_url.rstrip("/") + "/", url)
        return url

    def _problem_max_score(self, job: dict) -> int:
        problem = job.get("problem") or {}
        return int(problem.get("max_score") or 100)

    def _problem_time_limit_seconds(self, job: dict) -> float:
        problem = job.get("problem") or {}
        value = problem.get("time_limit_ms")
        if value:
            return max(float(value) / 1000, 0.1)
        return settings.default_time_limit_seconds

    def _testcase_time_limit_seconds(self, job: dict, testcase: dict) -> float:
        value = testcase.get("time_limit_ms_override")
        if value:
            return max(float(value) / 1000, 0.1)
        return self._problem_time_limit_seconds(job)

    def _normalize_output(self, value: str) -> str:
        return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").strip().split("\n"))

    def _run_command(
        self,
        command: list[str],
        cwd: Path,
        timeout_seconds: float,
        stdin: str = "",
        sandbox_mode_override: str | None = None,
        sandbox_container_id: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        mode = sandbox_mode_override or self.sandbox_mode
        if mode == "docker":
            effective_command = self._sandbox_exec_command(command, cwd, sandbox_container_id) if sandbox_container_id else self._sandbox_command(command, cwd)
        else:
            effective_command = command
        cwd.mkdir(parents=True, exist_ok=True)
        stdin_file = tempfile.TemporaryFile()
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        stdin_file.write(stdin.encode("utf-8"))
        stdin_file.seek(0)
        deadline = time.monotonic() + timeout_seconds
        try:
            apply_local_limits = mode == "local" and not (command and command[0] == "docker")
            process = subprocess.Popen(
                effective_command,
                cwd=cwd,
                stdin=stdin_file,
                stdout=stdout_file,
                stderr=stderr_file,
                preexec_fn=self._local_resource_limits() if apply_local_limits else None,
            )
            while True:
                returncode = process.poll()
                if self._file_size(stdout_file) > self.output_limit_bytes or self._file_size(stderr_file) > self.output_limit_bytes:
                    if returncode is None:
                        self._terminate_process(process)
                    return self._completed_process(command, 125, stdout_file, stderr_file)
                if returncode is not None:
                    return self._completed_process(command, returncode, stdout_file, stderr_file)
                if time.monotonic() >= deadline:
                    self._terminate_process(process)
                    return self._completed_process(command, 124, stdout_file, stderr_file, fallback_stderr="time limit exceeded")
                time.sleep(0.01)
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))
        finally:
            stdin_file.close()
            stdout_file.close()
            stderr_file.close()

    def _sandbox_command(self, command: list[str], cwd: Path) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--cpus",
            str(settings.sandbox_cpus),
            "--memory",
            f"{settings.sandbox_memory_mb}m",
            "--memory-swap",
            f"{settings.sandbox_memory_mb}m",
            "--pids-limit",
            str(settings.sandbox_pids_limit),
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--volume",
            f"{cwd}:{cwd}:rw",
            "--workdir",
            str(cwd),
            settings.sandbox_image,
            *command,
        ]

    def _sandbox_exec_command(self, command: list[str], cwd: Path, container_id: str) -> list[str]:
        return [
            "docker",
            "exec",
            "-i",
            "--workdir",
            str(cwd),
            container_id,
            *command,
        ]

    def _start_sandbox_container(self, cwd: Path) -> str | ExecutionResult:
        run_command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "-i",
            "--network",
            "none",
            "--cpus",
            str(settings.sandbox_cpus),
            "--memory",
            f"{settings.sandbox_memory_mb}m",
            "--memory-swap",
            f"{settings.sandbox_memory_mb}m",
            "--pids-limit",
            str(settings.sandbox_pids_limit),
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--volume",
            f"{cwd}:{cwd}:rw",
            "--workdir",
            str(cwd),
            settings.sandbox_image,
            "sh",
            "-lc",
            "sleep 3600",
        ]
        completed = self._run_command(run_command, cwd, timeout_seconds=10, sandbox_mode_override="local")
        if completed.returncode != 0:
            return ExecutionResult("system_error", None, "sandbox start failed: " + (completed.stderr or completed.stdout)[-4000:])
        container_id = (completed.stdout or "").strip()
        if not container_id:
            return ExecutionResult("system_error", None, "sandbox start failed: empty container id")
        return container_id

    def _stop_sandbox_container(self, container_id: str) -> None:
        self._run_command(["docker", "rm", "-f", container_id], self.work_root, timeout_seconds=5, sandbox_mode_override="local")

    def _local_resource_limits(self):
        try:
            import resource
        except ImportError:
            return None

        def apply_limits() -> None:
            memory_bytes = settings.sandbox_memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            except (ValueError, OSError):
                pass
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            except (ValueError, OSError):
                pass

        return apply_limits

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass

    def _completed_process(
        self,
        command: list[str],
        returncode: int,
        stdout_file,
        stderr_file,
        fallback_stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        stdout = self._read_limited_text(stdout_file)
        stderr = self._read_limited_text(stderr_file) or fallback_stderr
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def _read_limited_text(self, file) -> str:
        file.seek(0)
        data = file.read(self.output_limit_bytes)
        if isinstance(data, str):
            return data
        return data.decode("utf-8", errors="replace")

    def _file_size(self, file) -> int:
        return os.fstat(file.fileno()).st_size
