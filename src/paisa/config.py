from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "paisa"


def project_root() -> Path:
    return Path.cwd()


def default_statements_dir() -> Path:
    return project_root() / "statements"


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def default_db_path() -> Path:
    return default_data_dir() / "paisa.db"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    statements_dir: Path


def load_settings(db_path: Path | None = None, statements_dir: Path | None = None) -> Settings:
    return Settings(
        db_path=db_path or default_db_path(),
        statements_dir=statements_dir or default_statements_dir(),
    )
