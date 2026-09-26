"""Bounded Java memory checks in the real isolate runtime; no backend writes.

Run with reserved isolate box IDs (0..31) while the selected node is idle.
Every array is filled and retained, so heap reservation alone cannot pass.
"""
from dataclasses import asdict
from pathlib import Path
import json
import tempfile

from app.executor import JudgeExecutor


SOURCE = r'''import java.util.*;
public class Main {
  static Object retained;
  public static void main(String[] args) {
    Scanner in = new Scanner(System.in);
    String mode = in.next();
    int amount = in.nextInt();
    long sum = 0;
    if (mode.equals("chunks")) {
      byte[][] blocks = new byte[amount][];
      for (int i = 0; i < amount; i++) {
        blocks[i] = new byte[1024 * 1024];
        Arrays.fill(blocks[i], (byte)1);
      }
      retained = blocks;
      for (byte[] block : blocks) for (byte value : block) sum += value;
    } else if (mode.equals("array")) {
      int[] values = new int[amount];
      for (int i = 0; i < amount; i++) values[i] = i;
      retained = values;
      for (int value : values) sum += value;
    } else if (mode.equals("objects")) {
      ArrayList<String> values = new ArrayList<>();
      for (int i = 0; i < amount; i++) values.add("value-" + i);
      Collections.sort(values);
      retained = values;
      sum = values.size();
    } else throw new IllegalArgumentException(mode);
    System.out.println(sum);
  }
}
'''


def cases():
    # Positive cases exercise >512 MiB live data, contiguous arrays and GC.
    for limit, amount in [(128, 40), (256, 150), (512, 300), (1024, 600), (2048, 1200)]:
        yield (f"chunks-{limit}", limit, f"chunks {amount}\n", f"{amount * 1024 * 1024}\n", "accepted")
    # SerialGC divides the heap into generations; one array cannot use all Xmx.
    yield ("array", 2048, "array 140000000\n", "9799999930000000\n", "accepted")
    yield ("objects", 512, "objects 1000000\n", "1000000\n", "accepted")
    # Leave the 128 MiB policy unchanged, and verify OOM remains MLE at >512 MiB.
    yield ("oom-small", 128, "array 20000000\n", "199999990000000\n", "memory_limit_exceeded")
    yield ("oom-large", 1024, "chunks 900\n", "943718400\n", "memory_limit_exceeded")


def run():
    from app.settings import settings
    if settings.isolate_box_id_base != 0 or settings.isolate_box_id_count > 32:
        raise RuntimeError("Use reserved isolate box IDs: base=0, count=32")
    base = Path("/var/lib/zerone-judge-java-checks")
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as scratch:
        executor = JudgeExecutor(Path(scratch), sandbox_mode="isolate", testcase_parallelism=1)
        for name, limit, input_text, output_text, expected in cases():
            result = executor.judge({
                "judge_job_id": name,
                "submission": {"language": "java8", "source_code": SOURCE},
                "problem": {"language_resource_limits": {"java8": {
                    "time_limit_ms": 30000, "memory_limit_mb": limit,
                }}},
                "testcases": [{"display_order": 1, "input_text": input_text, "output_text": output_text}],
            })
            print(json.dumps({"case": name, "limit_mib": limit, "heap_mib": executor._java_heap_mb(limit), **asdict(result)}), flush=True)
            if result.status != expected:
                raise RuntimeError(f"{name}: expected {expected}, got {result.status}")
    print("Java heap isolate checks: PASS", flush=True)


if __name__ == "__main__":
    run()
