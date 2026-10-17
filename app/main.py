import faulthandler
import signal
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event, Thread

from app.backend_client import BackendClient
from app.executor import FULL_VERIFICATION, JudgeExecutor
from app.editor_executor import EditorExecutor, cleanup_editor_containers
from app.editor_protocol import PROTOCOL
from app.log_forwarder import LogForwarder
from app.settings import settings

import subprocess

def cleanup_isolate_boxes() -> None:
    start = settings.isolate_box_id_base
    end = start + settings.isolate_box_id_count

    print(f"[judge-agent] cleaning isolate boxes {start}~{end - 1}")

    for box_id in range(start, end):
        try:
            subprocess.run(
                [
                    "isolate",
                    "--cg",
                    f"--box-id={box_id}",
                    "--cleanup",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except Exception:
            pass

def judge_one(client: BackendClient, executor: JudgeExecutor, job: dict, editor_executor=None) -> None:
    stop_keepalive = Event()

    def keepalive() -> None:
        interval = 3.0 if job.get("task_type") == PROTOCOL else 30.0
        while not stop_keepalive.wait(interval):
            try:
                client.renew_lease(
                    job_id=job["judge_job_id"],
                    lease_token=job["lease_token"],
                )
            except RuntimeError as error:
                print(f"[judge-agent] lease renew failed for {job['judge_job_id']}: {error}")
                if job.get("task_type") == PROTOCOL and any(f"http {code}:" in str(error) for code in [403, 404, 409]):
                    stop_keepalive.set()

    def report_progress(status: str, progress_current: int | None, progress_total: int | None) -> None:
        try:
            client.report_progress(
                job_id=job["judge_job_id"],
                lease_token=job["lease_token"],
                status=status,
                progress_current=progress_current,
                progress_total=progress_total,
            )
        except RuntimeError as error:
            print(f"[judge-agent] progress report failed for {job['judge_job_id']}: {error}")

    keepalive_thread = Thread(target=keepalive, daemon=True)
    keepalive_thread.start()
    try:
        if job.get("task_type") == PROTOCOL:
            if not editor_executor:
                raise RuntimeError("Editor executor is missing")
            value = editor_executor.execute(job, cancelled=stop_keepalive.is_set)
            for attempt in range(3):
                try:
                    client.report_editor_result(job["judge_job_id"], job["lease_token"], result=value)
                    break
                except RuntimeError:
                    if attempt == 2:
                        raise
                    time.sleep(1)
            print(f"[judge-agent] reported Editor task {job['judge_job_id']} status={value['status']}")
            return
        pending_cases = []
        case_last_sent = time.monotonic()
        def report_case(value, current, total):
            nonlocal case_last_sent
            pending_cases.append(value)
            if current != 1 and current != total and len(pending_cases) < 16 and time.monotonic() - case_last_sent < .5:
                return
            try:
                client.report_progress(job["judge_job_id"], job["lease_token"], "judging", current, total,
                                       testcase_results=list(pending_cases))
                pending_cases.clear()
                case_last_sent = time.monotonic()
            except RuntimeError as error:
                print(f"[judge-agent] testcase progress report failed: {error}")
        result = executor.judge(job, progress=report_progress, case_progress=report_case)
        report = dict(
            job_id=job["judge_job_id"],
            lease_token=job["lease_token"],
            final_status=result.status,
            compile_message=result.message if result.status == "compile_error" else None,
            judge_message=result.message,
            failed_testcase_order=result.failed_testcase_order,
            runtime_ms=result.runtime_ms,
            memory_kb=result.memory_kb,
            **({"testcase_results": result.testcase_results} if result.testcase_results is not None else {}),
        )
        attempts = 3 if result.testcase_results is not None else 1
        for attempt in range(attempts):
            try:
                client.report_result(**report)
                break
            except RuntimeError:
                if attempt == attempts - 1:
                    raise
                time.sleep(1)
        print(f"[judge-agent] reported {job['judge_job_id']} status={result.status}")
    except Exception as error:
        message = "".join(traceback.format_exception_only(type(error), error)).strip()
        try:
            if job.get("task_type") == PROTOCOL:
                client.report_editor_result(job["judge_job_id"], job["lease_token"], error=message[-4000:])
                print(f"[judge-agent] reported Editor task {job['judge_job_id']} failed")
                return
            client.report_result(
                job_id=job["judge_job_id"],
                lease_token=job["lease_token"],
                final_status="system_error",
                compile_message=None,
                judge_message=message[-4000:],
                failed_testcase_order=None,
                runtime_ms=None,
                memory_kb=None,
            )
            print(f"[judge-agent] reported {job['judge_job_id']} status=system_error error={message}")
        except Exception as report_error:
            print(f"[judge-agent] result report failed for {job['judge_job_id']}: {report_error}")
        raise
    finally:
        stop_keepalive.set()
        keepalive_thread.join(timeout=1)


def main() -> None:
    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    cleanup_isolate_boxes()
    cleanup_editor_containers(settings.node_name)
    client = BackendClient()
    executor = JudgeExecutor()
    editor_executor = EditorExecutor()
    while True:
        try:
            node_id = client.register_node([*editor_executor.capabilities, FULL_VERIFICATION, "judge-icpc-languages-v1"],
                                           editor_executor.revision or FULL_VERIFICATION)
            break
        except RuntimeError as error:
            print(f"[judge-agent] register failed: {error}")
            time.sleep(max(settings.poll_interval_seconds, 1.0))

    log_forwarder = LogForwarder(client, node_id)
    log_forwarder.start()
    try:
        print(f"[judge-agent] registered node={settings.node_name} id={node_id} slots={settings.total_slots}")

        with ThreadPoolExecutor(max_workers=settings.total_slots) as pool:
            running = set()
            last_heartbeat = 0.0
            while True:
                now = time.monotonic()
                if now - last_heartbeat >= max(settings.heartbeat_interval_seconds, 0.5):
                    try:
                        client.heartbeat(node_id, len(running))
                        last_heartbeat = now
                    except RuntimeError as error:
                        print(f"[judge-agent] heartbeat failed: {error}")

                done = set()
                if running:
                    done, running = wait(running, timeout=0, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        future.result()
                    except Exception as error:
                        print(f"[judge-agent] job worker failed: {error}")

                free_slots = settings.total_slots - len(running)
                if free_slots > 0:
                    try:
                        claimed = client.claim(node_id, free_slots)
                    except RuntimeError as error:
                        print(f"[judge-agent] claim failed: {error}")
                        time.sleep(settings.poll_interval_seconds)
                        continue
                    for job in claimed:
                        print(f"[judge-agent] claimed {job['judge_job_id']} submission={job['submission']['submission_id']}")
                        running.add(pool.submit(judge_one, client, executor, job, editor_executor))

                    if settings.run_once and not claimed and not running:
                        print("[judge-agent] run once completed: no pending jobs")
                        return

                if settings.run_once and not running:
                    print("[judge-agent] run once completed")
                    return

                time.sleep(settings.poll_interval_seconds)
    finally:
        log_forwarder.close()


if __name__ == "__main__":
    main()
