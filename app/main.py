import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from app.backend_client import BackendClient
from app.executor import JudgeExecutor
from app.settings import settings


def judge_one(client: BackendClient, executor: JudgeExecutor, job: dict) -> None:
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

    result = executor.judge(job, progress=report_progress)
    client.report_result(
        job_id=job["judge_job_id"],
        lease_token=job["lease_token"],
        final_status=result.status,
        awarded_score=result.score,
        compile_message=result.message if result.status == "compile_error" else None,
        judge_message=result.message,
        failed_testcase_order=result.failed_testcase_order,
    )
    print(f"[judge-agent] reported {job['judge_job_id']} status={result.status}")


def main() -> None:
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
            if now - last_heartbeat >= 5:
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
