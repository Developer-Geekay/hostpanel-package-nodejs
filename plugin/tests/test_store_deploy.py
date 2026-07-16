from __future__ import annotations

import sqlite3

import pytest

from hostpanel_nodejs import ids, store


def _make_app(app_id: str = "portfolio-example-com", port: int = 31000) -> dict:
    return store.create_app(
        {
            "id": app_id,
            "name": "Portfolio",
            "username": "geekay",
            "domain": "example.com",
            "app_root": "/home/geekay/public_html",
            "entrypoint": "server.js",
            "start_command": "node server.js",
            "install_command": "",
            "node_version": "22",
            "port": port,
        },
        env={},
    )


def _columns(db, table: str) -> set[str]:
    with db.get_conn() as conn:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migrate_creates_deploy_schema(fresh_db):
    store.migrate()
    with fresh_db.get_conn() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "nodejs_deployments" in tables
    expected = {name for name, _ in store._DEPLOY_APP_COLUMNS}
    assert expected <= _columns(fresh_db, "nodejs_apps")


def test_migrate_is_additive_on_pre_deploy_schema(fresh_db):
    # Simulate a DB created before the deploy feature: original table, one live app.
    with fresh_db.get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE nodejs_apps (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL,
              domain TEXT NOT NULL, app_root TEXT NOT NULL, entrypoint TEXT NOT NULL,
              start_command TEXT NOT NULL, install_command TEXT NOT NULL,
              node_version TEXT NOT NULL, port INTEGER NOT NULL UNIQUE,
              status TEXT NOT NULL DEFAULT 'stopped', ssl_enabled INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO nodejs_apps VALUES ('old-app','Old','geekay','old.example.com','/home/geekay/public_html',"
            "'server.js','node server.js','','22',31001,'running',0,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
    store.migrate()
    app = store.get_app("old-app")
    assert app is not None
    assert app["deploy_enabled"] is False
    assert app["keep_releases"] == 5
    assert app["health_timeout_s"] == 30
    assert app["current_sha"] is None
    assert app["status"] == "running"


def test_migrate_is_idempotent(fresh_db):
    store.migrate()
    store.migrate()
    assert store.list_apps() == []


def test_deployment_lifecycle(fresh_db):
    app = _make_app()
    dep_id = ids.new_deployment_id()
    dep = store.create_deployment(dep_id, app["id"], "9f2a1c4e")
    assert dep["status"] == "received"
    assert dep["finished_at"] is None

    dep = store.set_deployment_status(dep_id, "verified")
    assert dep["status"] == "verified"
    assert dep["finished_at"] is None

    dep = store.set_deployment_status(dep_id, "healthy", detail="200 on /healthz")
    assert dep["status"] == "healthy"
    assert dep["detail"] == "200 on /healthz"
    assert dep["finished_at"] is not None


def test_deployment_rejects_unknown_status(fresh_db):
    app = _make_app()
    dep_id = ids.new_deployment_id()
    store.create_deployment(dep_id, app["id"], "9f2a1c4e")
    with pytest.raises(ValueError):
        store.set_deployment_status(dep_id, "shipped")


def test_deployment_requires_existing_app(fresh_db):
    store.migrate()
    with pytest.raises(sqlite3.IntegrityError):
        store.create_deployment(ids.new_deployment_id(), "no-such-app", "9f2a1c4e")


def test_list_deployments_newest_first(fresh_db):
    app = _make_app()
    first = ids.new_deployment_id()
    second = ids.new_deployment_id()
    store.create_deployment(first, app["id"], "aaaaaaa")
    store.create_deployment(second, app["id"], "bbbbbbb")
    listed = store.list_deployments(app["id"])
    assert [d["id"] for d in listed] == [second, first]


def test_delete_app_removes_deployments(fresh_db):
    app = _make_app()
    dep_id = ids.new_deployment_id()
    store.create_deployment(dep_id, app["id"], "9f2a1c4e")
    store.delete_app(app["id"])
    assert store.get_deployment(dep_id) is None
