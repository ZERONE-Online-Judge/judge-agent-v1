import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("java_heap_patch", ROOT / "deploy/java_heap_patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
RELEASE = (ROOT / "app/executor.py").read_text()


def test_patch_retains_unrelated_node_customizations_and_is_idempotent():
    current = "# local adjustment\nclass JudgeExecutor:\n" + ''.join("    " + line + "\n" for line in patch.OLD.splitlines()) + "\n    def custom(self):\n        return 42\n"
    updated = patch.patch_executor(current, RELEASE)
    assert updated.startswith("# local adjustment\n")
    assert "def custom(self):\n        return 42" in updated
    assert "min(512" not in updated
    assert patch.patch_executor(updated, RELEASE) == updated


def test_patch_rejects_unknown_policy_without_overwriting():
    with pytest.raises(ValueError, match="Custom Java heap"):
        patch.patch_executor("class JudgeExecutor:\n    def _java_heap_mb(self, memory_limit_mb: int) -> int:\n        return 42\n", RELEASE)


def test_settings_keeps_everything_except_release_version():
    before = 'node_name = "custom-node"\nagent_version: str = "0.2.18"\n'
    after = patch.patch_settings(before)
    assert after == before.replace('"0.2.18"', '"0.2.19"')
    assert patch.patch_settings(after) == after
    with pytest.raises(ValueError, match="Unrecognized agent version"):
        patch.patch_settings('agent_version: str = "custom"')
