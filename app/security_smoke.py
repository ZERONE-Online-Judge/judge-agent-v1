"""Synthetic isolate checks; never sends jobs/results to the backend."""
from pathlib import Path
import tempfile

from app.executor import JudgeExecutor


def run() -> None:
    # /tmp is a separate default mount inside isolate; use the production layout.
    scratch_base = Path("/var/lib/zerone-judge-security-checks")
    scratch_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-", dir=scratch_base) as scratch:
        root = Path(scratch)
        executor = JudgeExecutor(root / "work", sandbox_mode="isolate", testcase_parallelism=1)
        cases = {
            "python313": "print(42)",
            "c99": '#include <stdio.h>\nint main(void) { puts("42"); return 0; }',
            "cpp17": '#include <iostream>\nint main() { std::cout << 42 << "\\n"; }',
            "java8": 'public class Main { public static void main(String[] args) { System.out.println(42); } }',
        }
        for language, code in cases.items():
            result = executor.judge({
                "judge_job_id": "smoke-" + language,
                "submission": {"language": language, "source_code": code},
                "problem": {"time_limit_ms": 3000, "memory_limit_mb": 512},
                "testcases": [{"display_order": 1, "input_text": "", "output_text": "42\n"}],
            })
            assert result.status == "accepted", (language, result)
            print("Sandbox language check:", language, "PASS", flush=True)

        # Harmless sentinel outside the compilation directory, never a real key.
        sentinel = root / "private-fixture.h"
        sentinel.write_text('#define PRIVATE_FIXTURE 42\n')
        compile_dir = root / "work" / "compile-probe"
        compile_dir.mkdir()
        result = executor._prepare_command(compile_dir, "c99", f'#include "{sentinel}"\nint main(void) {{ return PRIVATE_FIXTURE; }}')
        assert getattr(result, "status", None) == "compile_error", "Compiler read a file outside its sandbox"
        print("Compiler outside-file check: PASS", flush=True)

        job_dir = root / "work" / "answer-probe"
        answer = job_dir / "checker" / "checker-cases" / "001" / "expected.txt"
        answer.parent.mkdir(parents=True)
        answer.write_text("PRIVATE-ANSWER-FIXTURE")
        source = ('from pathlib import Path\n'
                  f'print("LEAK" if Path({str(answer)!r}).exists() else "SAFE")\n')
        command = executor._prepare_command(job_dir, "python313", source)
        result = executor._run_single_testcase(command, job_dir, {},
            {"display_order": 1, "input_text": "", "output_text": "SAFE\n"}, None, "fixture", None)
        assert result.result.status == "accepted", "Submission can see checker answer files"
        print("Checker answer isolation check: PASS", flush=True)


if __name__ == "__main__":
    run()
