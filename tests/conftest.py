from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "paisa.db"
