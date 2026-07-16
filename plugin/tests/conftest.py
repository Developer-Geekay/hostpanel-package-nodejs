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

# validators.py pulls the core domain registry at import time; tests don't
# exercise domain resolution, so an empty registry is enough.
if "domain_registry" not in sys.modules:
    registry = types.ModuleType("domain_registry")
    registry._load_domains = lambda: []
    registry._load_subdomains = lambda: []
    sys.modules["domain_registry"] = registry

# apps.py imports the core auth/session modules at import time; tests only
# need the shapes, not real JWT handling.
if "auth" not in sys.modules:
    auth_mod = types.ModuleType("auth")

    class _User:
        def __init__(self, username="test", role="admin", linux_user="test", disabled=False, protected=False):
            self.username = username
            self.role = role
            self.linux_user = linux_user
            self.disabled = disabled
            self.protected = protected

    auth_mod.User = _User
    sys.modules["auth"] = auth_mod

if "deps" not in sys.modules:
    deps_mod = types.ModuleType("deps")

    def _get_current_user():
        raise RuntimeError("deps shim: override per test")

    deps_mod.get_current_user = _get_current_user
    sys.modules["deps"] = deps_mod


@pytest.fixture
def fresh_db(tmp_path):
    db_module = sys.modules["db"]
    db_module.DB_PATH = str(tmp_path / "hostpanel-test.db")
    yield db_module
    db_module.DB_PATH = None
