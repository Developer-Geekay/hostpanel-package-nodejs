from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from db import get_conn


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def migrate() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodejs_apps (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              username TEXT NOT NULL,
              domain TEXT NOT NULL,
              app_root TEXT NOT NULL,
              entrypoint TEXT NOT NULL,
              start_command TEXT NOT NULL,
              install_command TEXT NOT NULL,
              node_version TEXT NOT NULL,
              port INTEGER NOT NULL UNIQUE,
              status TEXT NOT NULL DEFAULT 'stopped',
              ssl_enabled INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nodejs_app_env (
              app_id TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY (app_id, key)
            );

            CREATE TABLE IF NOT EXISTS nodejs_app_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              app_id TEXT NOT NULL,
              level TEXT NOT NULL,
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )


def _row_to_app(row: Any) -> dict[str, Any]:
    app = dict(row)
    app["ssl_enabled"] = bool(app.get("ssl_enabled"))
    app["env"] = get_env(app["id"])
    return app


def list_apps(username: Optional[str] = None) -> list[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        if username:
            rows = conn.execute("SELECT * FROM nodejs_apps WHERE username=? ORDER BY created_at DESC", (username,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM nodejs_apps ORDER BY created_at DESC").fetchall()
    return [_row_to_app(row) for row in rows]


def get_app(app_id: str) -> Optional[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodejs_apps WHERE id=?", (app_id,)).fetchone()
    return _row_to_app(row) if row else None


def get_app_by_domain(domain: str) -> Optional[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodejs_apps WHERE domain=?", (domain,)).fetchone()
    return _row_to_app(row) if row else None


def port_owner(port: int) -> Optional[str]:
    migrate()
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM nodejs_apps WHERE port=?", (port,)).fetchone()
    return row["id"] if row else None


def create_app(data: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    migrate()
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nodejs_apps (
              id, name, username, domain, app_root, entrypoint, start_command,
              install_command, node_version, port, status, ssl_enabled, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["id"],
                data["name"],
                data["username"],
                data["domain"],
                data["app_root"],
                data["entrypoint"],
                data["start_command"],
                data["install_command"],
                data["node_version"],
                data["port"],
                data.get("status", "provisioning"),
                int(bool(data.get("ssl_enabled", False))),
                now,
                now,
            ),
        )
        for key, value in env.items():
            conn.execute("INSERT INTO nodejs_app_env (app_id, key, value) VALUES (?,?,?)", (data["id"], key, value))
    return get_app(data["id"]) or data


def update_app(app_id: str, data: dict[str, Any], env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    migrate()
    fields = []
    values: list[Any] = []
    for key in (
        "name",
        "username",
        "domain",
        "app_root",
        "entrypoint",
        "start_command",
        "install_command",
        "node_version",
        "port",
        "status",
        "ssl_enabled",
    ):
        if key in data:
            fields.append(f"{key}=?")
            values.append(int(bool(data[key])) if key == "ssl_enabled" else data[key])
    fields.append("updated_at=?")
    values.append(utc_now())
    values.append(app_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE nodejs_apps SET {', '.join(fields)} WHERE id=?", values)
        if env is not None:
            conn.execute("DELETE FROM nodejs_app_env WHERE app_id=?", (app_id,))
            for key, value in env.items():
                conn.execute("INSERT INTO nodejs_app_env (app_id, key, value) VALUES (?,?,?)", (app_id, key, value))
    return get_app(app_id) or data


def delete_app(app_id: str) -> None:
    migrate()
    with get_conn() as conn:
        conn.execute("DELETE FROM nodejs_app_env WHERE app_id=?", (app_id,))
        conn.execute("DELETE FROM nodejs_app_logs WHERE app_id=?", (app_id,))
        conn.execute("DELETE FROM nodejs_apps WHERE id=?", (app_id,))


def delete_apps(app_ids: Iterable[str]) -> None:
    for app_id in app_ids:
        delete_app(app_id)


def get_env(app_id: str) -> dict[str, str]:
    migrate()
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM nodejs_app_env WHERE app_id=? ORDER BY key", (app_id,)).fetchall()
    return {row["key"]: row["value"] for row in rows}


def add_log(app_id: str, level: str, message: str) -> None:
    migrate()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO nodejs_app_logs (app_id, level, message, created_at) VALUES (?,?,?,?)",
            (app_id, level, message[:8000], utc_now()),
        )


def list_lifecycle_logs(app_id: str, limit: int = 100) -> list[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT level, message, created_at FROM nodejs_app_logs WHERE app_id=? ORDER BY id DESC LIMIT ?",
            (app_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
