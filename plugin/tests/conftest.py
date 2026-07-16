"""Test scaffolding for the hostpanel_nodejs plugin.

The plugin imports `db` from the HostPanel core, which isn't available in this
repo. The shim below mirrors the core contract exactly (see
hostpanel/backend/db.py): sqlite3.Row rows, WAL journal, foreign_keys ON,
commit-on-success / rollback-on-error. Tests point it at a temp file per test.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _make_db_shim() -> types.ModuleType:
    mod = types.ModuleType("db")
    mod.DB_PATH = None

    @contextlib.contextmanager
    def get_conn():
        if not mod.DB_PATH:
            raise RuntimeError("db shim used outside the fresh_db fixture")
        conn = sqlite3.connect(mod.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    mod.get_conn = get_conn
    return mod


if "db" not in sys.modules:
    sys.modules["db"] = _make_db_shim()


@pytest.fixture
def fresh_db(tmp_path):
    db_module = sys.modules["db"]
    db_module.DB_PATH = str(tmp_path / "hostpanel-test.db")
    yield db_module
    db_module.DB_PATH = None
