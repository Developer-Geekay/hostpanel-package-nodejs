from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional

from fastapi import HTTPException

from hostpanel_nodejs import store
from hostpanel_nodejs.validators import validate_app_id


PLUGIN_DIR = "/opt/hostpanel/plugins/nodejs"
COMMAND_TIMEOUT = 120


def node_bin(version: str) -> str:
    return f"{PLUGIN_DIR}/node-{version}"


def service_name(app_id: str) -> str:
    return f"hostpanel-nodejs-{validate_app_id(app_id)}"


def service_path(app_id: str) -> str:
    return f"/etc/systemd/system/{service_name(app_id)}.service"


def _run(command: list[str], check: bool = False, input_data: Optional[str] = None, timeout: int = COMMAND_TIMEOUT):
    try:
        return subprocess.run(
            command,
            input=input_data,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Node.js operation timed out")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Node.js operation failed").strip()
        raise HTTPException(status_code=500, detail=detail)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _sudo(command: list[str], check: bool = False, input_data: Optional[str] = None, timeout: int = COMMAND_TIMEOUT):
    return _run(["sudo"] + command, check=check, input_data=input_data, timeout=timeout)


def runtime_versions() -> dict[str, str]:
    data: dict[str, str] = {}
    for version in ("22", "24"):
        binary = node_bin(version)
        if os.path.exists(binary):
            try:
                result = _run([binary, "--version"], timeout=10)
                data[f"node-{version}"] = result.stdout.strip() or "unknown"
            except Exception:
                data[f"node-{version}"] = "unavailable"
        else:
            data[f"node-{version}"] = "missing"
    return data


HP_CHOWN = "/opt/hostpanel/bin/hp-chown"


def ensure_app_directory(app: dict) -> None:
    _sudo(["mkdir", "-p", app["app_root"]], check=True)
    _sudo([HP_CHOWN, f"{app['username']}:{app['app_root']}"], check=False)


def write_service(app: dict) -> None:
    app_id = validate_app_id(app["id"])
    env_lines = [
        f"Environment=PORT={int(app['port'])}",
        "Environment=NODE_ENV=production",
        f"Environment=PATH={PLUGIN_DIR}:/usr/local/bin:/usr/bin:/bin",
    ]
    for key, value in (app.get("env") or {}).items():
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        env_lines.append(f'Environment="{key}={escaped}"')

    command = app["start_command"].strip() or f"{node_bin(app['node_version'])} {shlex.quote(app['entrypoint'])}"
    content = f"""[Unit]
Description=HostPanel Node.js app {app_id}
After=network.target

[Service]
Type=simple
User={app['username']}
WorkingDirectory={app['app_root']}
{chr(10).join(env_lines)}
ExecStart=/bin/bash -lc {shlex.quote('exec ' + command)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    _sudo(["tee", service_path(app_id)], input_data=content, check=True)
    _sudo(["chmod", "644", service_path(app_id)], check=False)
    _sudo(["systemctl", "daemon-reload"], check=False)
    store.add_log(app_id, "info", "Systemd service written")


def remove_service(app_id: str) -> None:
    name = service_name(app_id)
    _sudo(["systemctl", "stop", name], check=False, timeout=30)
    _sudo(["systemctl", "disable", name], check=False, timeout=30)
    _sudo(["rm", "-f", service_path(app_id)], check=False)
    _sudo(["systemctl", "daemon-reload"], check=False)


def start(app_id: str) -> None:
    _sudo(["systemctl", "enable", service_name(app_id)], check=False)
    _sudo(["systemctl", "start", service_name(app_id)], check=True)
    store.update_app(app_id, {"status": "running"})
    store.add_log(app_id, "info", "Application started")


def stop(app_id: str) -> None:
    _sudo(["systemctl", "stop", service_name(app_id)], check=False)
    store.update_app(app_id, {"status": "stopped"})
    store.add_log(app_id, "info", "Application stopped")


def restart(app_id: str) -> None:
    _sudo(["systemctl", "restart", service_name(app_id)], check=True)
    store.update_app(app_id, {"status": "running"})
    store.add_log(app_id, "info", "Application restarted")


def status(app_id: str) -> str:
    result = _sudo(["systemctl", "is-active", service_name(app_id)], check=False, timeout=10)
    return "running" if result.returncode == 0 else "stopped"


def metrics(app_id: str) -> dict:
    """Live memory / uptime / restart-count for the app's systemd service.

    No CPU — per-app CPU can't be sampled reliably here. Values are None when the
    service isn't active so the UI can show a clean placeholder.
    """
    unit = service_name(app_id)
    result = _sudo(
        ["systemctl", "show", unit,
         "-p", "ActiveState", "-p", "MemoryCurrent",
         "-p", "ActiveEnterTimestampMonotonic", "-p", "NRestarts"],
        check=False, timeout=10,
    )
    data: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, _, val = line.partition("=")
        if key:
            data[key] = val

    active = data.get("ActiveState") == "active"

    mem = data.get("MemoryCurrent", "")
    memory_bytes = int(mem) if mem.isdigit() else None
    # systemd reports a huge sentinel when unset
    if memory_bytes is not None and memory_bytes > (1 << 62):
        memory_bytes = None

    uptime_seconds = None
    mono = data.get("ActiveEnterTimestampMonotonic", "")
    if active and mono.isdigit():
        try:
            with open("/proc/uptime") as f:
                boot_uptime = float(f.read().split()[0])
            uptime_seconds = max(0, int(boot_uptime - int(mono) / 1_000_000))
        except Exception:
            uptime_seconds = None

    restarts = int(data["NRestarts"]) if data.get("NRestarts", "").isdigit() else 0

    return {
        "active": active,
        "memory_bytes": memory_bytes if active else None,
        "uptime_seconds": uptime_seconds,
        "restarts": restarts,
    }

