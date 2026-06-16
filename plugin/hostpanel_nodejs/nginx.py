from __future__ import annotations

import os
import subprocess

from fastapi import HTTPException

from hostpanel_nodejs.validators import _find_cert_paths, cert_exists, validate_domain_name


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
    cert_path, key_path = _find_cert_paths(domain)
    ssl = bool(cert_path)
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

    ssl_certificate     {cert_path};
    ssl_certificate_key {key_path};

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


def remove_proxy(app: dict) -> None:
    domain = validate_domain_name(app["domain"])
    existing = _read_existing(domain)
    if not (existing and "Managed by hostpanel-nodejs" in existing):
        return
    linux_user = app["username"]
    doc_root = f"/home/{linux_user}/public_html"
    cert_path, key_path = _find_cert_paths(domain)
    log_dir = "/opt/hostpanel/plugins/nginx/logs"
    if cert_path:
        content = f"""# Restored by hostpanel-nodejs after app removal
server {{
    listen 80;
    server_name {domain} www.{domain};

    location ^~ /.well-known/acme-challenge/ {{
        root {doc_root};
        default_type "text/plain";
        try_files $uri =404;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl;
    server_name {domain} www.{domain};
    root {doc_root};
    index index.php index.html index.htm;

    ssl_certificate     {cert_path};
    ssl_certificate_key {key_path};

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    add_header Strict-Transport-Security "max-age=31536000" always;

    access_log {log_dir}/{domain}.access.log;
    error_log  {log_dir}/{domain}.error.log;

    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
"""
    else:
        content = f"""# Restored by hostpanel-nodejs after app removal
server {{
    listen 80;
    server_name {domain} www.{domain};
    root {doc_root};
    index index.php index.html index.htm;

    access_log {log_dir}/{domain}.access.log;
    error_log  {log_dir}/{domain}.error.log;

    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
"""
    _sudo(["tee", _vhost_path(domain)], input_data=content, check=True)
    validate_config()
    reload()


def validate_config() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-t"], check=True)


def reload() -> None:
    if os.path.exists(NGINX_BIN):
        _sudo([NGINX_BIN, "-s", "reload"], check=False)
