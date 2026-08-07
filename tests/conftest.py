from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from property_hunter.config import Settings
from property_hunter.db import Repository, connect, init_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture()
def repo(db_path: Path) -> Repository:
    conn = connect(db_path)
    try:
        yield Repository(conn)
    finally:
        conn.close()


@pytest.fixture()
def conn(db_path: Path) -> sqlite3.Connection:
    c = connect(db_path)
    try:
        yield c
    finally:
        c.close()


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")
