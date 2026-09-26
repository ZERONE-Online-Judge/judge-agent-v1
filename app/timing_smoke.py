"""Bounded production-isolate checks for CPU timing and wall-time headroom."""
from pathlib import Path
import tempfile

from app.executor import JudgeExecutor
from app.settings import settings


def run() -> None:
    if settings.isolate_box_id_base != 0 or settings.isolate_box_id_count > 32:
        raise RuntimeError("Use reserved isolate box IDs 0..31 for timing smoke checks")

    scratch_base = Path("/var/lib/zerone-judge-timing-checks")
    scratch_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-", dir=scratch_base) as scratch:
        executor = JudgeExecutor(Path(scratch) / "work", sandbox_mode="isolate", testcase_parallelism=4)
        python = "/usr/local/bin/python3.13" if Path("/usr/local/bin/python3.13").exists() else "/usr/bin/python3"

        sleeping = executor._run_command(
            [python, "-c", "import time; time.sleep(0.5)"],
            Path(scratch) / "sleep",
            timeout_seconds=0.2,
            memory_limit_mb=128,
        )
        assert sleeping.returncode == 0, sleeping.stderr
        assert sleeping.runtime_ms is not None and sleeping.runtime_ms < 200, sleeping.runtime_ms
        assert sleeping.wall_runtime_ms is not None and sleeping.wall_runtime_ms >= 400, sleeping.wall_runtime_ms
        assert sleeping.wall_runtime_ms > sleeping.runtime_ms + 250

        busy = executor._run_command(
            [python, "-c", "while True: pass"],
            Path(scratch) / "busy",
            timeout_seconds=0.2,
            memory_limit_mb=128,
        )
        assert busy.returncode == 124, (busy.returncode, busy.stderr)
        assert busy.runtime_ms is not None and 150 <= busy.runtime_ms <= 500, busy.runtime_ms

        assert executor._isolate_wall_time_seconds(1.0) == 3.0
        print(
            "Timing policy checks: PASS "
            f"sleep_cpu_ms={sleeping.runtime_ms} sleep_wall_ms={sleeping.wall_runtime_ms} "
            f"busy_cpu_ms={busy.runtime_ms}",
            flush=True,
        )


if __name__ == "__main__":
    run()
