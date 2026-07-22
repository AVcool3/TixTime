"""DuckDB warehouse connection helpers."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("TIXTIME_DB", REPO_ROOT / "data" / "tixtime.duckdb"))
SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_SQL.read_text())
