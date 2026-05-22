from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import itertools
import json
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


_isolate_box_counter = itertools.count()


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    score: int | None
    message: str | None = None
    failed_testcase_order: int | None = None
    runtime_ms: int | None = None
    memory_kb: int | None = None


@dataclass(frozen=True)
class PreparedChecker:
    command: list[str]
    cwd: Path
    cached_binary: Path | None = None


@dataclass(frozen=True)
class TestcaseRunResult:
    order: int
    result: ExecutionResult


ProgressCallback = Callable[[str, int | None, int | None], None]


class JudgeExecutor:
    def __init__(
        self,
        work_root: Path | None = None,
        sandbox_mode: str | None = None,
        checker_sandbox_mode: str | None = None,
        output_limit_bytes: int | None = None,
        testcase_parallelism: int | None = None,
    ) -> None:
        self.work_root = work_root or settings.work_root
        self.sandbox_mode = sandbox_mode or settings.sandbox_mode
        self.checker_sandbox_mode = checker_sandbox_mode or sandbox_mode or settings.checker_sandbox_mode
        self.output_limit_bytes = output_limit_bytes or settings.output_limit_bytes
        self.testcase_parallelism = max(1, testcase_parallelism or settings.testcase_parallelism)
        self.checker_cache_root = self.work_root / "checker-cache"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.checker_cache_root.mkdir(parents=True, exist_ok=True)

    def judge(self, job: dict, progress: ProgressCallback | None = None) -> ExecutionResult:
        submission = job["submission"]
        language = submission["language"]
        source_code = submission["source_code"]
        self._hydrate_job_bundle(job)
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

    def _hydrate_job_bundle(self, job: dict) -> None:
        bundle_url = job.get("bundle_url")
        if not isinstance(bundle_url, str) or not bundle_url:
            return
        try:
            raw = self._read_object_bytes(bundle_url, "")
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload.get("testcases"), list):
                job["testcases"] = payload["testcases"]
            if isinstance(payload.get("package_files"), list):
                job["package_files"] = payload["package_files"]
        except Exception:
            # fall back to per-object flow
            return

    def _prepare_command(self, job_dir: Path, language: str, source_code: str) -> list[str] | ExecutionResult:
        if language == "python313":
            return self._prepare_python(job_dir, source_code)
        if language == "cpp17":
            return self._compile(
                job_dir,
                "main.cpp",
                source_code,
                ["/usr/bin/g++", "-B/usr/bin", "-std=c++17", "-O2", "main.cpp", "-o", "main"],
                [str(job_dir / "main")]
            )
        if language == "c99":
            return self._compile(
                job_dir,
                "main.c",
                source_code,
                ["/usr/bin/gcc", "-B/usr/bin", "-std=c99", "-O2", "main.c", "-o", "main"],
                [str(job_dir / "main")]
            )
        if language == "java8":
            return self._prepare_java(job_dir, source_code)
        return ExecutionResult("system_error", None, f"unsupported language: {language}")

    def _prepare_python(self, job_dir: Path, source_code: str) -> list[str]:
        source = job_dir / "main.py"
        source.write_text(source_code, encoding="utf-8")
        return ["/usr/local/bin/python3.13" if Path("/usr/local/bin/python3.13").exists() else "/usr/bin/python3", str(source)]

    def _compile(self, job_dir: Path, filename: str, source_code: str, compile_command: list[str], run_command: list[str]) -> list[str] | ExecutionResult:
        (job_dir / filename).write_text(source_code, encoding="utf-8")
        compiled = self._run_command(compile_command, job_dir, timeout_seconds=20, sandbox_mode_override="local")

        if compiled.returncode != 0:
            return ExecutionResult("compile_error", None, compiled.stderr[-4000:] or compiled.stdout[-4000:])
        return run_command

    def _prepare_java(self, job_dir: Path, source_code: str) -> list[str] | ExecutionResult:
        (job_dir / "Main.java").write_text(source_code, encoding="utf-8")
        compiled = self._run_command(
            [
                "/usr/bin/javac",
                "-J-Xmx96m",
                "-J-Xss256k",
                "-J-XX:ReservedCodeCacheSize=32m",
                "-J-XX:CompressedClassSpaceSize=16m",
                "-J-XX:MaxMetaspaceSize=64m",
                "--release",
                "8",
                "Main.java",
            ],
            job_dir,
            timeout_seconds=20,
            sandbox_mode_override="local",
        )
        if compiled.returncode != 0:
            return ExecutionResult("compile_error", None, compiled.stderr[-4000:] or compiled.stdout[-4000:])
        return ["/usr/bin/java", "-cp", str(job_dir), "Main"]

    def _run_final(self, command: list[str], job_dir: Path, job: dict) -> ExecutionResult:
        completed = self._run_command(
            command,
            job_dir,
            timeout_seconds=self._problem_time_limit_seconds(job),
            stdin="",
            memory_limit_mb=self._problem_memory_limit_mb(job),
        )
        if completed.returncode == 0:
            return self._execution_result("accepted", self._problem_max_score(job), None, completed=completed)
        if completed.returncode == 124:
            return self._execution_result("time_limit_exceeded", 0, "time limit exceeded", completed=completed)
        if completed.returncode == 125:
            return self._execution_result("output_limit_exceeded", 0, "output limit exceeded", completed=completed)
        if completed.returncode in {137, -signal.SIGKILL}:
            return self._execution_result("memory_limit_exceeded", 0, "memory limit exceeded", completed=completed)
        return self._execution_result("runtime_error", 0, completed.stderr[-4000:] or completed.stdout[-4000:], completed=completed)

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
        total = len(testcases)
        max_runtime_ms: int | None = None
        max_memory_kb: int | None = None
        worker_count = min(self.testcase_parallelism, total)
        sandbox_container_id: str | None = None
        if progress:
            progress("judging", 0, total)
        if worker_count == 1:
            for index, testcase in enumerate(testcases, start=1):
                result = self._run_single_testcase(
                    command,
                    job_dir,
                    job,
                    testcase,
                    checker,
                    source_hash,
                    sandbox_container_id,
                )
                max_runtime_ms = self._max_metric(max_runtime_ms, result.result.runtime_ms)
                max_memory_kb = self._max_metric(max_memory_kb, result.result.memory_kb)
                if result.result.status != "accepted":
                    return self._execution_result(
                        result.result.status,
                        result.result.score,
                        result.result.message,
                        result.result.failed_testcase_order,
                        runtime_ms=max_runtime_ms,
                        memory_kb=max_memory_kb,
                    )
                if progress:
                    progress("judging", index, total)
            return self._execution_result("accepted", self._problem_max_score(job), None, runtime_ms=max_runtime_ms, memory_kb=max_memory_kb)

        completed_count = 0
        results: list[TestcaseRunResult] = []
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(
                    self._run_single_testcase,
                    command,
                    job_dir,
                    job,
                    testcase,
                    checker,
                    source_hash,
                    sandbox_container_id,
                )
                for testcase in testcases
            ]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                max_runtime_ms = self._max_metric(max_runtime_ms, result.result.runtime_ms)
                max_memory_kb = self._max_metric(max_memory_kb, result.result.memory_kb)
                completed_count += 1
                if progress:
                    progress("judging", completed_count, total)
        for item in sorted(results, key=lambda value: value.order):
            if item.result.status != "accepted":
                return self._execution_result(
                    item.result.status,
                    item.result.score,
                    item.result.message,
                    item.result.failed_testcase_order,
                    runtime_ms=max_runtime_ms,
                    memory_kb=max_memory_kb,
                )
        return self._execution_result("accepted", self._problem_max_score(job), None, runtime_ms=max_runtime_ms, memory_kb=max_memory_kb)

    def _run_single_testcase(
        self,
        command: list[str],
        job_dir: Path,
        job: dict,
        testcase: dict,
        checker: PreparedChecker | None,
        source_hash: str,
        sandbox_container_id: str | None,
    ) -> TestcaseRunResult:
        order = int(testcase["display_order"])
        case_dir = job_dir / "cases" / f"{order:03d}"
        case_dir.mkdir(parents=True, exist_ok=True)
        input_text = testcase.get("input_text") if isinstance(testcase.get("input_text"), str) else self._read_object(testcase.get("input_url"), testcase["input_storage_key"])
        expected_text = testcase.get("output_text") if isinstance(testcase.get("output_text"), str) else self._read_object(testcase.get("output_url"), testcase["output_storage_key"])
        normalized_input = self._normalize_input_text(input_text)
        completed = self._run_command(
            command,
            case_dir,
            timeout_seconds=self._testcase_time_limit_seconds(job, testcase),
            stdin=normalized_input,
            sandbox_container_id=sandbox_container_id,
            sandbox_mount_root=job_dir if self.sandbox_mode == "isolate" else None,
            memory_limit_mb=self._testcase_memory_limit_mb(job, testcase),
        )
        runtime_ms = getattr(completed, "runtime_ms", None)
        memory_kb = getattr(completed, "memory_kb", None)

        if completed.returncode == 124:
            return TestcaseRunResult(order, self._execution_result("time_limit_exceeded", 0, f"time limit exceeded on testcase {order}", order, runtime_ms=runtime_ms, memory_kb=memory_kb))
        if completed.returncode == 125:
            return TestcaseRunResult(order, self._execution_result("output_limit_exceeded", 0, f"output limit exceeded on testcase {order}", order, runtime_ms=runtime_ms, memory_kb=memory_kb))
        if completed.returncode in {137, -signal.SIGKILL}:
            return TestcaseRunResult(order, self._execution_result("memory_limit_exceeded", 0, f"memory limit exceeded on testcase {order}", order, runtime_ms=runtime_ms, memory_kb=memory_kb))
        if completed.returncode != 0:
            return TestcaseRunResult(order, self._execution_result("runtime_error", 0, (completed.stderr or completed.stdout)[-4000:], order, runtime_ms=runtime_ms, memory_kb=memory_kb))
        case_label = self._testcase_label(testcase)
        if checker:
            checker_result = self._run_checker(
                checker,
                case_dir,
                order,
                normalized_input,
                expected_text,
                completed.stdout,
                case_label,
                source_hash,
                sandbox_container_id=None,
            )
            if checker_result:
                return TestcaseRunResult(order, self._execution_result(checker_result.status, checker_result.score, checker_result.message, checker_result.failed_testcase_order, runtime_ms=runtime_ms, memory_kb=memory_kb))
            return TestcaseRunResult(order, self._execution_result("accepted", self._problem_max_score(job), None, runtime_ms=runtime_ms, memory_kb=memory_kb))
        expected = self._normalize_output(expected_text)
        actual = self._normalize_output(completed.stdout)
        if actual != expected:
            return TestcaseRunResult(
                order,
                self._execution_result(
                    "wrong_answer",
                    0,
                    self._build_wrong_answer_message(
                        order,
                        case_label,
                        "wrong answer",
                        input_text,
                        expected_text,
                        completed.stdout,
                        str(testcase.get("input_storage_key") or ""),
                        str(testcase.get("output_storage_key") or ""),
                        source_hash,
                    ),
                    order,
                    runtime_ms=runtime_ms,
                    memory_kb=memory_kb,
                ),
            )
        return TestcaseRunResult(order, self._execution_result("accepted", self._problem_max_score(job), None, runtime_ms=runtime_ms, memory_kb=memory_kb))

    def _prepare_checker(self, job_dir: Path, package_files: list[dict]) -> PreparedChecker | ExecutionResult | None:
        checker_files = [item for item in package_files if item.get("role") == "checker"]
        if not checker_files:
            return None
        checker_file = checker_files[-1]
        checker_dir = job_dir / "checker"
        checker_dir.mkdir(parents=True, exist_ok=True)

        # validator is only used in testcase/package verification stage.
        # judge runtime uses checker only (no validator re-run here).
        checker_storage_key = str(checker_file.get("storage_key") or "")
        resource_storage_keys = sorted(
            str(item.get("storage_key") or "")
            for item in package_files
            if item.get("role") == "package-resource"
        )
        if checker_storage_key:
            fast_cache_key = self._checker_cache_key_from_storage(checker_storage_key, resource_storage_keys)
            fast_binary = self.checker_cache_root / fast_cache_key / "checker"
            if fast_binary.exists():
                shutil.copy2(fast_binary, checker_dir / "checker")
                return PreparedChecker([str(checker_dir / "checker")], checker_dir, cached_binary=fast_binary)

        resources = [item for item in package_files if item.get("role") == "package-resource"]
        resource_blobs: list[tuple[str, bytes]] = []
        for resource in resources:
            resource_name = Path(resource.get("original_filename") or resource["storage_key"]).name
            resource_bytes = self._package_file_bytes(resource)
            resource_blobs.append((resource_name, resource_bytes))
            (checker_dir / resource_name).write_bytes(resource_bytes)

        filename = Path(checker_file.get("original_filename") or checker_file["storage_key"]).name
        checker_bytes = self._package_file_bytes(checker_file)
        source_path = checker_dir / filename
        source_path.write_bytes(checker_bytes)
        suffix = source_path.suffix.lower()
        if suffix == ".py":
            return PreparedChecker(
                [
                    "/usr/local/bin/python3.13"
                    if Path("/usr/local/bin/python3.13").exists()
                    else "/usr/bin/python3",
                    str(source_path),
                ],
                checker_dir,
            )
        if suffix in {".cpp", ".cc", ".cxx"}:
            cached = self._ensure_cached_checker_binary(filename, suffix, checker_bytes, resource_blobs)
            if isinstance(cached, ExecutionResult):
                return cached
            shutil.copy2(cached, checker_dir / "checker")
            return PreparedChecker([str(checker_dir / "checker")], checker_dir, cached_binary=cached)
        if suffix == ".c":
            cached = self._ensure_cached_checker_binary(filename, suffix, checker_bytes, resource_blobs)
            if isinstance(cached, ExecutionResult):
                return cached
            shutil.copy2(cached, checker_dir / "checker")
            return PreparedChecker([str(checker_dir / "checker")], checker_dir, cached_binary=cached)
        return ExecutionResult("system_error", None, f"unsupported checker file: {filename}")

    def _checker_cache_key_from_storage(self, checker_storage_key: str, resource_storage_keys: list[str]) -> str:
        digest = hashlib.sha256()
        digest.update(checker_storage_key.encode("utf-8"))
        for item in resource_storage_keys:
            digest.update(item.encode("utf-8"))
        return digest.hexdigest()

    def _package_file_bytes(self, package_file: dict) -> bytes:
        inline = package_file.get("inline_bytes_b64")
        if isinstance(inline, str) and inline:
            return base64.b64decode(inline.encode("ascii"))
        return self._read_object_bytes(package_file.get("url"), package_file["storage_key"])

    def _ensure_cached_checker_binary(
        self,
        filename: str,
        suffix: str,
        checker_bytes: bytes,
        resource_blobs: list[tuple[str, bytes]],
    ) -> Path | ExecutionResult:
        digest = hashlib.sha256()
        digest.update(filename.encode("utf-8"))
        digest.update(suffix.encode("utf-8"))
        digest.update(checker_bytes)
        for resource_name, resource_bytes in sorted(resource_blobs, key=lambda item: item[0]):
            digest.update(resource_name.encode("utf-8"))
            digest.update(resource_bytes)
        cache_key = digest.hexdigest()
        cache_dir = self.checker_cache_root / cache_key
        binary_path = cache_dir / "checker"
        if binary_path.exists():
            return binary_path

        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / filename).write_bytes(checker_bytes)
        for resource_name, resource_bytes in resource_blobs:
            (cache_dir / resource_name).write_bytes(resource_bytes)

        if suffix in {".cpp", ".cc", ".cxx"}:
            compile_cmd = ["/usr/bin/g++", "-B/usr/bin", "-std=c++17", "-O2", filename, "-o", "checker"]
        elif suffix == ".c":
            compile_cmd = ["/usr/bin/gcc", "-B/usr/bin", "-std=c99", "-O2", filename, "-o", "checker"]
        else:
            return ExecutionResult("system_error", None, f"unsupported checker file: {filename}")

        compiled = self._run_command(
            compile_cmd,
            cache_dir,
            timeout_seconds=20,
            sandbox_mode_override="local"
        )
        if compiled.returncode != 0:
            return ExecutionResult("system_error", None, "checker compile failed: " + (compiled.stderr or compiled.stdout)[-4000:])
        if not binary_path.exists():
            return ExecutionResult("system_error", None, "checker compile failed: checker binary missing")
        return binary_path

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
            sandbox_mode_override=self.checker_sandbox_mode,
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

    def _problem_memory_limit_mb(self, job: dict) -> int:
        problem = job.get("problem") or {}
        value = problem.get("memory_limit_mb")
        if value:
            return max(int(value), 16)
        return settings.sandbox_memory_mb

    def _testcase_memory_limit_mb(self, job: dict, testcase: dict) -> int:
        value = testcase.get("memory_limit_mb_override")
        if value:
            return max(int(value), 16)
        return self._problem_memory_limit_mb(job)

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
        sandbox_mount_root: Path | None = None,
        memory_limit_mb: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        mode = sandbox_mode_override or self.sandbox_mode
        if mode == "isolate":
            return self._run_isolate_command(
                command,
                cwd,
                timeout_seconds,
                stdin,
                sandbox_mount_root or cwd,
                memory_limit_mb or settings.sandbox_memory_mb,
            )
        effective_command = command
        watchdog_timeout_seconds = timeout_seconds
        cwd.mkdir(parents=True, exist_ok=True)
        stdin_file = tempfile.TemporaryFile()
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        stdin_file.write(stdin.encode("utf-8"))
        stdin_file.seek(0)
        deadline = time.monotonic() + watchdog_timeout_seconds
        started_at = time.monotonic()
        peak_memory_kb: int | None = None
        last_memory_sample_at = 0.0
        baseline_child_rss_kb = self._children_maxrss_kb()
        try:
            apply_local_limits = mode == "local"
            process = subprocess.Popen(
                effective_command,
                cwd=cwd,
                stdin=stdin_file,
                stdout=stdout_file,
                stderr=stderr_file,
                preexec_fn=self._local_resource_limits() if apply_local_limits else None,
            )
            peak_memory_kb = self._max_metric(peak_memory_kb, self._memory_sample_kb(process.pid, mode, sandbox_container_id))
            while True:
                returncode = process.poll()
                now = time.monotonic()
                if now - last_memory_sample_at >= 0.05:
                    last_memory_sample_at = now
                    sample = self._memory_sample_kb(process.pid, mode, sandbox_container_id)
                    peak_memory_kb = self._max_metric(peak_memory_kb, sample)
                if self._file_size(stdout_file) > self.output_limit_bytes or self._file_size(stderr_file) > self.output_limit_bytes:
                    if returncode is None:
                        self._terminate_process(process)
                    return self._completed_process(command, 125, stdout_file, stderr_file, started_at, peak_memory_kb, baseline_child_rss_kb)
                if returncode is not None:
                    sample = self._memory_sample_kb(process.pid, mode, sandbox_container_id)
                    peak_memory_kb = self._max_metric(peak_memory_kb, sample)
                    return self._completed_process(command, returncode, stdout_file, stderr_file, started_at, peak_memory_kb, baseline_child_rss_kb)
                if now >= deadline:
                    self._terminate_process(process)
                    sample = self._memory_sample_kb(process.pid, mode, sandbox_container_id)
                    peak_memory_kb = self._max_metric(peak_memory_kb, sample)
                    return self._completed_process(command, 124, stdout_file, stderr_file, started_at, peak_memory_kb, baseline_child_rss_kb, fallback_stderr="time limit exceeded")
                time.sleep(0.01)
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))
        finally:
            stdin_file.close()
            stdout_file.close()
            stderr_file.close()

    def _run_isolate_command(
        self,
        command: list[str],
        cwd: Path,
        timeout_seconds: float,
        stdin: str,
        mount_root: Path,
        memory_limit_mb: int,
    ) -> subprocess.CompletedProcess[str]:
        cwd.mkdir(parents=True, exist_ok=True)
        mount_root.mkdir(parents=True, exist_ok=True)
        box_id = self._next_isolate_box_id()
        meta_path = cwd / f".isolate-meta-{box_id}.txt"
        stdin_file = tempfile.TemporaryFile()
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        stdin_file.write(stdin.encode("utf-8"))
        stdin_file.seek(0)
        started_at = time.monotonic()
        try:
            init = subprocess.run(
                ["/usr/bin/isolate", "--cg", f"--box-id={box_id}", "--init"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            if init.returncode != 0:
                return subprocess.CompletedProcess(command, init.returncode, init.stdout, init.stderr)

            effective_command = self._command_with_runtime_limits(command, memory_limit_mb)
            run_command = [
                "/usr/bin/isolate",
                "--cg",
                f"--box-id={box_id}",
                f"--meta={meta_path}",
                f"--time={timeout_seconds}",
                f"--wall-time={timeout_seconds + 1}",
                "--extra-time=0",
                f"--cg-mem={memory_limit_mb * 1024}",
                f"--fsize={max(1, self.output_limit_bytes // 1024)}",
                f"--processes={settings.sandbox_pids_limit}",
                f"--dir={mount_root}={mount_root}:rw",
                f"--chdir={cwd}",
                "--env=PATH=/usr/bin:/bin",
                "--run",
                "--",
                *effective_command,
            ]
            isolated = subprocess.run(
                run_command,
                stdin=stdin_file,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout_seconds + 5,
                check=False,
            )
            meta = self._read_isolate_meta(meta_path)
            if not meta and isolated.returncode != 0:
                return self._completed_process(
                    command,
                    127,
                    stdout_file,
                    stderr_file,
                    started_at,
                    fallback_stderr="isolate failed without execution metadata",
                )
            returncode = self._isolate_returncode(meta)
            if self._file_size(stdout_file) > self.output_limit_bytes or self._file_size(stderr_file) > self.output_limit_bytes:
                returncode = 125
            completed = self._completed_process(
                command,
                returncode,
                stdout_file,
                stderr_file,
                started_at,
                memory_kb=self._isolate_memory_kb(meta),
                fallback_stderr=self._isolate_message(meta, returncode),
            )
            completed.runtime_ms = self._isolate_runtime_ms(meta) or completed.runtime_ms
            return completed
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))
        except subprocess.TimeoutExpired:
            return self._completed_process(
                command,
                124,
                stdout_file,
                stderr_file,
                started_at,
                fallback_stderr="time limit exceeded",
            )
        finally:
            try:
                subprocess.run(
                    ["/usr/bin/isolate", "--cg", f"--box-id={box_id}", "--cleanup"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass
            stdin_file.close()
            stdout_file.close()
            stderr_file.close()
            meta_path.unlink(missing_ok=True)

    def _next_isolate_box_id(self) -> int:
        count = max(1, settings.isolate_box_id_count)
        seed = os.getpid() + next(_isolate_box_counter)
        return settings.isolate_box_id_base + (seed % count)

    def _read_isolate_meta(self, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        meta: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key:
                meta[key.strip()] = value.strip()
        return meta

    def _isolate_returncode(self, meta: dict[str, str]) -> int:
        if "cg-oom-killed" in meta:
            return 137
        status = meta.get("status", "")
        if status == "TO":
            return 124
        if status == "SG":
            signal_number = int(meta.get("exitsig") or signal.SIGKILL)
            return 137 if signal_number == signal.SIGKILL else -signal_number
        if status and status not in {"OK", "RE"}:
            return 127
        try:
            return int(meta.get("exitcode") or 0)
        except ValueError:
            return 127

    def _isolate_runtime_ms(self, meta: dict[str, str]) -> int | None:
        try:
            return max(0, int(float(meta.get("time") or "0") * 1000))
        except ValueError:
            return None

    def _isolate_memory_kb(self, meta: dict[str, str]) -> int | None:
        try:
            return int(meta.get("cg-mem") or meta.get("max-rss") or "0") or None
        except ValueError:
            return None

    def _isolate_message(self, meta: dict[str, str], returncode: int) -> str:
        if returncode == 124:
            return "time limit exceeded"
        if returncode == 137 or "cg-oom-killed" in meta:
            return "memory limit exceeded"
        return meta.get("message", "")

    def _command_with_runtime_limits(self, command: list[str], memory_limit_mb: int) -> list[str]:
        if not command:
            return command
        executable = Path(command[0]).name
        if executable != "java":
            return command

        heap_mb = self._java_heap_mb(memory_limit_mb)
        return [
            command[0],
            f"-Xmx{heap_mb}m",
            "-Xss256k",
            "-XX:ReservedCodeCacheSize=32m",
            "-XX:CompressedClassSpaceSize=16m",
            "-XX:MaxMetaspaceSize=64m",
            *command[1:],
        ]

    def _java_heap_mb(self, memory_limit_mb: int) -> int:
        if memory_limit_mb <= 128:
            return 32
        if memory_limit_mb <= 512:
            return max(48, min(96, memory_limit_mb // 4))
        return max(96, min(192, memory_limit_mb // 3))

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
        started_at: float | None = None,
        memory_kb: int | None = None,
        baseline_child_rss_kb: int | None = None,
        fallback_stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        stdout = self._read_limited_text(stdout_file)
        stderr = self._read_limited_text(stderr_file) or fallback_stderr
        completed = subprocess.CompletedProcess(command, returncode, stdout, stderr)
        wall_runtime_ms = max(0, int((time.monotonic() - started_at) * 1000)) if started_at is not None else None
        completed.runtime_ms = wall_runtime_ms
        fallback_memory_kb = self._children_maxrss_kb()
        if baseline_child_rss_kb is not None and fallback_memory_kb is not None and fallback_memory_kb <= baseline_child_rss_kb:
            fallback_memory_kb = None
        completed.memory_kb = memory_kb if memory_kb is not None else fallback_memory_kb
        return completed

    def _children_maxrss_kb(self) -> int | None:
        try:
            import resource

            value = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
            # macOS reports bytes, Linux reports kilobytes.
            return value // 1024 if value > 1024 * 1024 else value
        except Exception:
            return None

    def _memory_sample_kb(self, pid: int, mode: str, sandbox_container_id: str | None) -> int | None:
        return self._process_rss_kb(pid)

    def _process_rss_kb(self, pid: int) -> int | None:
        try:
            completed = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=0.2, check=False)
            value = completed.stdout.strip().splitlines()[-1].strip() if completed.stdout.strip() else ""
            return int(value) if value else None
        except Exception:
            return None

    def _max_metric(self, current: int | None, candidate: int | None) -> int | None:
        if candidate is None:
            return current
        if current is None:
            return candidate
        return max(current, candidate)

    def _execution_result(
        self,
        status: str,
        score: int | None,
        message: str | None = None,
        failed_testcase_order: int | None = None,
        completed: subprocess.CompletedProcess[str] | None = None,
        runtime_ms: int | None = None,
        memory_kb: int | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            status,
            score,
            message,
            failed_testcase_order,
            runtime_ms=self._max_metric(runtime_ms, getattr(completed, "runtime_ms", None) if completed else None),
            memory_kb=self._max_metric(memory_kb, getattr(completed, "memory_kb", None) if completed else None),
        )

    def _read_limited_text(self, file) -> str:
        file.seek(0)
        data = file.read(self.output_limit_bytes)
        if isinstance(data, str):
            return data
        return data.decode("utf-8", errors="replace")

    def _file_size(self, file) -> int:
        return os.fstat(file.fileno()).st_size
