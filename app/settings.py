from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    internal_api_base_url: str = os.getenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:8000/api").rstrip("/")
    node_name: str = os.getenv("JUDGE_NODE_NAME", "judge-demo")
    node_secret: str = os.getenv("JUDGE_NODE_SECRET", "demo")
    total_slots: int = int(os.getenv("JUDGE_TOTAL_SLOTS", "2"))
    testcase_parallelism: int = int(os.getenv("JUDGE_TESTCASE_PARALLELISM", "4"))
    work_root: Path = Path(os.getenv("JUDGE_WORK_ROOT", "/tmp/zerone-judge"))
    agent_version: str = "0.2.7"
    poll_interval_seconds: float = float(os.getenv("JUDGE_POLL_INTERVAL_SECONDS", "1"))
    long_poll_seconds: float = float(os.getenv("JUDGE_LONG_POLL_SECONDS", "20"))
    heartbeat_interval_seconds: float = float(os.getenv("JUDGE_HEARTBEAT_INTERVAL_SECONDS", "2"))
    default_time_limit_seconds: float = float(os.getenv("JUDGE_DEFAULT_TIME_LIMIT_SECONDS", "3"))
    object_read_timeout_seconds: float = float(os.getenv("JUDGE_OBJECT_READ_TIMEOUT_SECONDS", "30"))
    sandbox_mode: str = os.getenv("JUDGE_SANDBOX_MODE", "isolate")
    checker_sandbox_mode: str = os.getenv("JUDGE_CHECKER_SANDBOX_MODE", os.getenv("JUDGE_SANDBOX_MODE", "isolate"))
    sandbox_memory_mb: int = int(os.getenv("JUDGE_SANDBOX_MEMORY_MB", "1024"))
    sandbox_pids_limit: int = int(os.getenv("JUDGE_SANDBOX_PIDS_LIMIT", "128"))
    isolate_box_id_base: int = int(os.getenv("JUDGE_ISOLATE_BOX_ID_BASE", "100"))
    isolate_box_id_count: int = int(os.getenv("JUDGE_ISOLATE_BOX_ID_COUNT", "1000"))
    output_limit_bytes: int = int(os.getenv("JUDGE_OUTPUT_LIMIT_BYTES", str(10 * 1024 * 1024)))
    run_once: bool = os.getenv("JUDGE_RUN_ONCE", "false").lower() == "true"


settings = Settings()
