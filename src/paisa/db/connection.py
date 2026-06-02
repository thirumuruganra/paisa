from __future__ import annotations

from pathlib import Path

import aiosqlite


async def connect(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(db_path)
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA journal_mode = WAL")
    connection.row_factory = aiosqlite.Row
    return connection
