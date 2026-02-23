import shutil
import uuid
from pathlib import Path

import pytest


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
