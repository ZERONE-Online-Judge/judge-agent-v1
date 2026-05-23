import faulthandler
import signal
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event, Thread

from app.backend_client import BackendClient
from app.executor import JudgeExecutor
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

def judge_one(client: BackendClient, executor: JudgeExecutor, job: dict) -> None:
    stop_keepalive = Event()

    def keepalive() -> None:
        interval = 30.0
        while not stop_keepalive.wait(interval):
            try:
                client.renew_lease(
                    job_id=job["judge_job_id"],
                    lease_token=job["lease_token"],
                )
            except RuntimeError as error:
                print(f"[judge-agent] lease renew failed for {job['judge_job_id']}: {error}")

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
        result = executor.judge(job, progress=report_progress)
        client.report_result(
            job_id=job["judge_job_id"],
            lease_token=job["lease_token"],
            final_status=result.status,
            awarded_score=result.score,
            compile_message=result.message if result.status == "compile_error" else None,
            judge_message=result.message,
            failed_testcase_order=result.failed_testcase_order,
            runtime_ms=result.runtime_ms,
            memory_kb=result.memory_kb,
        )
        print(f"[judge-agent] reported {job['judge_job_id']} status={result.status}")
    except Exception as error:
        message = "".join(traceback.format_exception_only(type(error), error)).strip()
        try:
            client.report_result(
                job_id=job["judge_job_id"],
                lease_token=job["lease_token"],
                final_status="system_error",
                awarded_score=0,
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
    client = BackendClient()
    executor = JudgeExecutor()
    while True:
        try:
            node_id = client.register_node()
            break
        except RuntimeError as error:
            print(f"[judge-agent] register failed: {error}")
            time.sleep(max(settings.poll_interval_seconds, 1.0))

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
                    running.add(pool.submit(judge_one, client, executor, job))

                if settings.run_once and not claimed and not running:
                    print("[judge-agent] run once completed: no pending jobs")
                    return

            if settings.run_once and not running:
                print("[judge-agent] run once completed")
                return

            time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
