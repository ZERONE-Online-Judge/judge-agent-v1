"""Patch only a recognized Java heap method, preserving node-specific code."""
import ast
from pathlib import Path
import sys


OLD = '''def _java_heap_mb(self, memory_limit_mb: int) -> int:
    if memory_limit_mb <= 128:
        return 64
    if memory_limit_mb <= 256:
        return 128
    if memory_limit_mb <= 512:
        return 256
    return max(256, min(512, int(memory_limit_mb * 0.6)))
'''


def method(source):
    classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "JudgeExecutor"]
    methods = [node for cls in classes for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_java_heap_mb"]
    if len(classes) != 1 or len(methods) != 1:
        raise ValueError("Unrecognized JudgeExecutor structure")
    return methods[0]


def patch_executor(current, release):
    original, replacement = method(current), method(release)
    if ast.dump(original) not in {ast.dump(ast.parse(OLD).body[0]), ast.dump(replacement)}:
        raise ValueError("Custom Java heap policy found; refusing to overwrite it")
    lines, new_lines = current.splitlines(keepends=True), release.splitlines(keepends=True)
    updated = ''.join(lines[:original.lineno - 1] + new_lines[replacement.lineno - 1:replacement.end_lineno] + lines[original.end_lineno:])
    ast.parse(updated)
    return updated


def patch_settings(current):
    old = 'agent_version: str = "0.2.18"'
    new = 'agent_version: str = "0.2.19"'
    if current.count(old) + current.count(new) != 1:
        raise ValueError("Unrecognized agent version; expected 0.2.18 or 0.2.19")
    return current.replace(old, new, 1)


if __name__ == "__main__":
    source, release, destination = map(Path, sys.argv[1:])
    executor = patch_executor((source / "executor.py").read_text(), (release / "executor.py").read_text())
    settings = patch_settings((source / "settings.py").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "executor.py").write_text(executor)
    (destination / "settings.py").write_text(settings)
    (destination / "java_heap_smoke.py").write_bytes((release / "java_heap_smoke.py").read_bytes())
