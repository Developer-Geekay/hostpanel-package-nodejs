from __future__ import annotations

import os
import subprocess

from fastapi import HTTPException

from hostpanel_nodejs.validators import cert_exists, validate_domain_name


NGINX_BIN = "/opt/hostpanel/plugins/nginx/nginx"
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


def write_proxy(app: dict) -> bool:
    domain = validate_domain_name(app["domain"])
    port = int(app["port"])
    ssl = cert_exists(domain)
    if ssl:
        content = f"""# Managed by hostpanel-nodejs for app {app['id']}
server {{
    listen 80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {domain};

    ssl_certificate     /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""
    else:
        content = f"""# Managed by hostpanel-nodejs for app {app['id']}
server {{
    listen 80;
    server_name {domain};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""
    _sudo(["mkdir", "-p", VHOSTS_DIR], check=True)
    existing = _read_existing(domain)
    if existing and "Managed by hostpanel-nodejs" not in existing:
        raise HTTPException(status_code=409, detail="A non-Node nginx vhost already exists for this domain")
    _sudo(["tee", _vhost_path(domain)], input_data=content, check=True)
    validate_config()
    reload()
    return ssl


def _read_existing(domain: str) -> str:
    path = _vhost_path(domain)
    if not os.path.exists(path):
        return ""
    with open(path, "r") as handle:
        return handle.read()


def remove_proxy(domain: str) -> None:
    existing = _read_existing(domain)
    if existing and "Managed by hostpanel-nodejs" in existing:
        _sudo(["rm", "-f", _vhost_path(domain)], check=False)
        validate_config()
        reload()


def validate_config() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-t"], check=True)


def reload() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-s", "reload"], check=False)
