import os
import shutil
import uuid
from pathlib import Path

import pytest


os.environ.setdefault("GOOGLE_API_KEY", "test-key")
_TEST_RUNTIME_ROOT = (Path("tests") / "_tmp" / "runtime_project").resolve()
os.environ.setdefault("AGENT_PROJECT_ID", "test_runtime")
os.environ.setdefault("AGENT_PROJECT_ROOT", str(_TEST_RUNTIME_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_runtime_root():
    try:
        yield
    finally:
        shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)
        shutil.rmtree(_TEST_RUNTIME_ROOT.parent, ignore_errors=True)


@pytest.fixture
def tmp_path():
    root = Path("tests") / "_tmp"
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"af-test-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base.resolve()
    finally:
        shutil.rmtree(base, ignore_errors=True)
