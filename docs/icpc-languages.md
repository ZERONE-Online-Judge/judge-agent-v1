# PyPy and Kotlin execution profile

The added toolchains follow the language versions listed in the ICPC World
Finals programming environment (2026 page, checked 2026-10-17):
https://icpc.global/worldfinals/programming-environment

- `pypy3`: PyPy 7.3.15, Python 3.9.18, source extension `.py`.
- `kotlin`: Kotlin/JVM 1.9.24, Temurin OpenJDK 21.0.4+7, `.kt`.
- Existing CPython 3.13 and Java 8-compatible submissions are unchanged.

Official PyPy binaries and Kotlin compiler archives are SHA-256 checked at
image build time. Architectures: Linux x86_64 and aarch64. The production
judge uses x86_64; local Apple Silicon checks use the aarch64 build.

PyPy syntax is checked with `pypy3 -m py_compile`. Kotlin is compiled with
`kotlinc SOURCE -include-runtime -d program.jar`. The self-contained jar
preserves top-level/package main entrypoints without renaming user classes.
It is run with the pinned Java 21 executable and the ICPC flags:
`-Dfile.encoding=UTF-8 -XX:+UseSerialGC -Xss64m -Xms{limit}m -Xmx{limit}m`.
We additionally set `-XX:ActiveProcessorCount=1` for the single-CPU sandbox.
The jar packaging differs from ICPC's class-directory + kotlin launcher;
the compiler version, JVM, standard library and runtime flags are fixed.

Language toolchain versions do not determine language resource allowances.
ZOJ uses these defaults when there is no explicit per-language limit:

- CPython: CPU base * 3 + 2000 ms; memory base * 2 + 32 MiB.
- PyPy: CPU base * 3 + 2000 ms; memory base * 2 + 128 MiB.
- Java and Kotlin: CPU base * 2 + 1000 ms; memory base * 2 + 16 MiB.
- C and C++: unchanged base limits.

Explicit time and memory fields take precedence independently. The backend
resolves the problem's disable_auto_adjustment flag to explicit numeric
limits before assignment, so existing agents also honor the checkbox.
Testcase overrides retain the highest priority. Measurements remain actual
CPU time and peak memory; displayed values are never divided by a multiplier.
JVM/PyPy/JIT memory counts toward the sandbox total; heap is not extra memory.
Editor online verification and stress comparisons use the same judge queue.
Generator, Validator and reference-answer preparation tasks remain distinct
Editor tool executions and their timings are not solution judging results.

Only updated agents advertising `judge-icpc-languages-v1` claim new-language
judging jobs. Editor tools require `editor-icpc-languages-v1`, advertised only
when the pinned runtime image has the expected capability label. This keeps
mixed-version rollouts from turning new-language jobs into system errors.

Before enabling the public UI, build the agent and Editor runtime images,
drain one node at a time, retain its current CPU/memory/slot settings, and run
`python -m app.security_smoke` inside the replacement agent's isolate setup.
Recreate the normal agent after this local-only smoke check. Never run the
smoke script as a replacement for the agent service's normal command.
