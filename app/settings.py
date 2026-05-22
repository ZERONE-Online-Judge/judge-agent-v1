from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    internal_api_base_url: str = os.getenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:8000/api").rstrip("/")
    node_name: str = os.getenv("JUDGE_NODE_NAME", "judge-demo")
    node_secret: str = os.getenv("JUDGE_NODE_SECRET", "demo")
    total_slots: int = int(os.getenv("JUDGE_TOTAL_SLOTS", "10"))
    testcase_parallelism: int = int(os.getenv("JUDGE_TESTCASE_PARALLELISM", "1"))
    work_root: Path = Path(os.getenv("JUDGE_WORK_ROOT", "/tmp/zerone-judge"))
    agent_version: str = os.getenv("JUDGE_AGENT_VERSION", "0.1.0")
    poll_interval_seconds: float = float(os.getenv("JUDGE_POLL_INTERVAL_SECONDS", "1"))
    long_poll_seconds: float = float(os.getenv("JUDGE_LONG_POLL_SECONDS", "20"))
    heartbeat_interval_seconds: float = float(os.getenv("JUDGE_HEARTBEAT_INTERVAL_SECONDS", "2"))
    default_time_limit_seconds: float = float(os.getenv("JUDGE_DEFAULT_TIME_LIMIT_SECONDS", "3"))
    sandbox_mode: str = os.getenv("JUDGE_SANDBOX_MODE", "local")
    checker_sandbox_mode: str = os.getenv("JUDGE_CHECKER_SANDBOX_MODE", os.getenv("JUDGE_SANDBOX_MODE", "local"))
    sandbox_image: str = os.getenv("JUDGE_SANDBOX_IMAGE", "zerone-judge-agent:latest")
    sandbox_cpus: float = float(os.getenv("JUDGE_SANDBOX_CPUS", "1"))
    sandbox_memory_mb: int = int(os.getenv("JUDGE_SANDBOX_MEMORY_MB", "512"))
    sandbox_pids_limit: int = int(os.getenv("JUDGE_SANDBOX_PIDS_LIMIT", "128"))
    output_limit_bytes: int = int(os.getenv("JUDGE_OUTPUT_LIMIT_BYTES", str(1024 * 1024)))
    run_once: bool = os.getenv("JUDGE_RUN_ONCE", "false").lower() == "true"


settings = Settings()
