from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from db import get_conn


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# Push-deploy pipeline states (see DEPLOY_PLAN.md). Terminal states close the row.
DEPLOYMENT_STATUSES = (
    "received",
    "verified",
    "extracted",
    "activated",
    "healthy",
    "failed",
    "rolled_back",
)
DEPLOYMENT_TERMINAL_STATUSES = {"healthy", "failed", "rolled_back"}

# Deploy-related nodejs_apps columns added after the initial schema; all default
# to "deploy disabled" so existing apps keep today's behavior untouched.
_DEPLOY_APP_COLUMNS = (
    ("deploy_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("repo", "TEXT"),
    ("ref", "TEXT"),
    ("health_path", "TEXT"),
    ("keep_releases", "INTEGER NOT NULL DEFAULT 5"),
    ("health_timeout_s", "INTEGER NOT NULL DEFAULT 30"),
    ("health_interval_s", "INTEGER NOT NULL DEFAULT 2"),
    ("current_sha", "TEXT"),
    ("previous_sha", "TEXT"),
    ("deploy_token_hash", "TEXT"),
)


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

            CREATE TABLE IF NOT EXISTS nodejs_deployments (
              id TEXT PRIMARY KEY,
              app_id TEXT NOT NULL REFERENCES nodejs_apps(id),
              commit_sha TEXT NOT NULL,
              status TEXT NOT NULL,
              detail TEXT,
              started_at TEXT NOT NULL,
              finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_nodejs_deployments_app
              ON nodejs_deployments(app_id, started_at);
            """
        )
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(nodejs_apps)")}
        for name, definition in _DEPLOY_APP_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE nodejs_apps ADD COLUMN {name} {definition}")


def _row_to_app(row: Any) -> dict[str, Any]:
    app = dict(row)
    app["ssl_enabled"] = bool(app.get("ssl_enabled"))
    app["deploy_enabled"] = bool(app.get("deploy_enabled"))
    # Legacy Phase 2 column — auth is OIDC now; never expose credential
    # material (even hashes) through the API.
    app.pop("deploy_token_hash", None)
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
        "deploy_enabled",
        "repo",
        "ref",
        "health_path",
        "keep_releases",
        "health_timeout_s",
        "health_interval_s",
        "current_sha",
        "previous_sha",
    ):
        if key in data:
            fields.append(f"{key}=?")
            values.append(int(bool(data[key])) if key in ("ssl_enabled", "deploy_enabled") else data[key])
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
        # Deployment rows go with the app (FK requires it); the durable history
        # of every deploy lives in the core audit_log, which is never pruned here.
        conn.execute("DELETE FROM nodejs_deployments WHERE app_id=?", (app_id,))
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


def create_deployment(deployment_id: str, app_id: str, commit_sha: str) -> dict[str, Any]:
    migrate()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO nodejs_deployments (id, app_id, commit_sha, status, started_at) VALUES (?,?,?,?,?)",
            (deployment_id, app_id, commit_sha, "received", utc_now()),
        )
    return get_deployment(deployment_id) or {}


def set_deployment_status(
    deployment_id: str, status: str, detail: Optional[str] = None, finished: Optional[bool] = None
) -> dict[str, Any]:
    """`finished` overrides the terminal-status default — Phase 2 closes a
    deployment at `activated` because health verification only exists from
    Phase 5 on."""
    if status not in DEPLOYMENT_STATUSES:
        raise ValueError(f"Unknown deployment status: {status}")
    migrate()
    if finished is None:
        finished = status in DEPLOYMENT_TERMINAL_STATUSES
    finished_at = utc_now() if finished else None
    with get_conn() as conn:
        conn.execute(
            "UPDATE nodejs_deployments SET status=?, detail=COALESCE(?, detail), finished_at=? WHERE id=?",
            (status, detail, finished_at, deployment_id),
        )
    return get_deployment(deployment_id) or {}


def get_deployment(deployment_id: str) -> Optional[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodejs_deployments WHERE id=?", (deployment_id,)).fetchone()
    return dict(row) if row else None


def list_deployments(app_id: str, limit: int = 50) -> list[dict[str, Any]]:
    migrate()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM nodejs_deployments WHERE app_id=? ORDER BY id DESC LIMIT ?",
            (app_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
