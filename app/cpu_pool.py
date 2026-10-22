"""Bound concurrent ZOJ work to distinct guest CPUs and Isolate box IDs."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import threading
import time

_config_lock = threading.Lock()


class CpuPool:
    def __init__(self, base, count, lock_root, cpus=None):
        available = sorted(os.sched_getaffinity(0)) if cpus is None else list(cpus)
        # Keep one guest CPU available to the agent and operating system.
        self.cpus = available[1:] if len(available) > 1 else available
        self.cpus = self.cpus[:count]
        if not self.cpus or base < 0:
            raise RuntimeError("Invalid ZOJ CPU pool")
        self.base = base
        self.lock_root = Path(lock_root)
        self.lock_root.mkdir(parents=True, exist_ok=True)

    def configure_isolate(self, path):
        """Isolate enforces these masks with cgroups; taskset alone is insufficient."""
        path = Path(path)
        with _config_lock:
            before = path.read_text()
            match = re.search(r"(?m)^num_boxes\s*=\s*(\d+)", before)
            if match and self.base + len(self.cpus) > int(match.group(1)):
                raise RuntimeError("ZOJ CPU pool exceeds Isolate box ID range")
            ids = {self.base + i for i in range(len(self.cpus))}
            lines = [line for line in before.splitlines()
                     if not ((m := re.match(r"box(\d+)\.cpus\s*=", line)) and int(m.group(1)) in ids)]
            lines += [f"box{self.base+i}.cpus = {cpu}" for i, cpu in enumerate(self.cpus)]
            after = "\n".join(lines) + "\n"
            if after != before:
                temporary = path.with_suffix(".zoj-tmp")
                temporary.write_text(after)
                temporary.chmod(path.stat().st_mode & 0o777)
                temporary.replace(path)

    @contextmanager
    def lease(self):
        # flock also coordinates compiler, checker, Editor and agent processes.
        while True:
            for index, cpu in enumerate(self.cpus):
                lock = (self.lock_root / f"box-{self.base+index}.lock").open("a")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    continue
                try:
                    yield self.base + index, cpu
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    lock.close()
                return
            time.sleep(0.02)


def production_pool(settings):
    if not getattr(settings, "cpu_pinning", False) or not Path("/usr/bin/isolate").exists():
        return None
    return CpuPool(settings.isolate_box_id_base, settings.isolate_box_id_count,
                   "/run/isolate/zoj-cpu-leases")
