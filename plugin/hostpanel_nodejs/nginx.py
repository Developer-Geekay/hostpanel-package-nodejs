from __future__ import annotations

import os
import subprocess

from fastapi import HTTPException

from hostpanel_nodejs.validators import _find_cert_paths, validate_domain_name


NGINX_BIN  = "/opt/hostpanel/plugins/nginx/nginx"
VHOSTS_DIR = "/opt/hostpanel/plugins/nginx/vhosts"


def _sudo(command: list[str], input_data: str | None = None, check: bool = False, timeout: int = 30):
    try:
        return subprocess.run(
            ["sudo"] + command,
            input=input_data,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="nginx operation timed out")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "nginx operation failed").strip()
        raise HTTPException(status_code=500, detail=detail)


def _vhost_path(domain: str) -> str:
    return f"{VHOSTS_DIR}/{validate_domain_name(domain)}.conf"


def _read_existing(domain: str) -> str:
    path = _vhost_path(domain)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def sync_vhost(domain: str, linux_user: str) -> bool:
    """Render and write the correct vhost for domain based on current DB state.

    Returns True if SSL is active on the written config.
    Raises 409 if a non-HostPanel vhost already exists for the domain.
    """
    from nginx_vhost import render_domain_vhost, is_hostpanel_vhost

    domain = validate_domain_name(domain)
    existing = _read_existing(domain)
    if existing and not is_hostpanel_vhost(existing):
        raise HTTPException(status_code=409, detail="A non-HostPanel nginx vhost already exists for this domain")

    cert_path, key_path = _find_cert_paths(domain)
    content = render_domain_vhost(domain, linux_user, cert_path, key_path)

    _sudo(["mkdir", "-p", VHOSTS_DIR], check=True)
    _sudo(["tee", _vhost_path(domain)], input_data=content, check=True)
    validate_config()
    reload()
    return bool(cert_path)


def validate_config() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-t"], check=True)


def reload() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-s", "reload"], check=False)
