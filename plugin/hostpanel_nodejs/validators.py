from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from domain_registry import _load_domains, _load_subdomains

from hostpanel_nodejs import store


NODE_VERSIONS = {"22", "24"}
PORT_MIN = 31000
PORT_MAX = 31999
APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]$")


def is_reserved_domain(domain: str) -> bool:
    value = domain.lower().strip(".")
    return value.startswith("cpanel.") or value.startswith("ftp.")


def current_username(current_user: Any) -> str:
    username = getattr(current_user, "linux_user", None) or getattr(current_user, "username", None)
    if not username:
        raise HTTPException(status_code=403, detail="Current user has no Linux user")
    return username


def is_admin(current_user: Any) -> bool:
    return getattr(current_user, "role", None) == "admin"


def slugify(value: str, fallback: str = "node-app") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:63]
    return slug or fallback


def make_app_id(name: str, domain: str) -> str:
    base = slugify(f"{name}-{domain}")
    app_id = base
    index = 2
    while store.get_app(app_id):
        suffix = f"-{index}"
        app_id = f"{base[:63 - len(suffix)]}{suffix}"
        index += 1
    return app_id


def validate_app_id(app_id: str) -> str:
    if not APP_ID_RE.fullmatch(app_id):
        raise HTTPException(status_code=400, detail="Invalid application id")
    return app_id


def validate_domain_name(domain: str) -> str:
    value = domain.lower().strip()
    if not DOMAIN_RE.fullmatch(value) or ".." in value:
        raise HTTPException(status_code=400, detail="Invalid domain")
    if is_reserved_domain(value):
        raise HTTPException(status_code=400, detail="Reserved domains cannot host Node.js apps")
    return value


def resolve_domain_user(domain: str, default_user: str = "") -> str:
    """Find the Linux user for a main domain or subdomain.
    Subdomains use the main domain's user instead of a separate user."""
    domain_val = (domain or "").lower().strip()
    if not domain_val:
        return default_user

    main_domains = _load_domains()
    domain_user_map = {d.get("domain_name", "").lower(): d.get("username", "") for d in main_domains if d.get("domain_name")}

    # 1. Direct match in main domains
    if domain_val in domain_user_map and domain_user_map[domain_val]:
        return domain_user_map[domain_val]

    # 2. Check subdomains registry for parent_domain match
    for record in _load_subdomains():
        if record.get("fqdn", "").lower() == domain_val:
            parent_domain = record.get("parent_domain", "").lower()
            if parent_domain in domain_user_map and domain_user_map[parent_domain]:
                return domain_user_map[parent_domain]

    # 3. Suffix match against main domains for multi-level or unlisted subdomains
    best_match_len = 0
    matched_user = ""
    for m_domain, m_user in domain_user_map.items():
        if m_domain and m_user and (domain_val == m_domain or domain_val.endswith("." + m_domain)):
            if len(m_domain) > best_match_len:
                best_match_len = len(m_domain)
                matched_user = m_user

    if matched_user:
        return matched_user

    return default_user


def eligible_domains(current_user: Any) -> list[dict[str, str]]:
    username = current_username(current_user)
    options: list[dict[str, str]] = []
    for record in _load_domains():
        domain = record.get("domain_name", "")
        owner = record.get("username", "")
        if is_reserved_domain(domain):
            continue
        if not is_admin(current_user) and owner != username:
            continue
        options.append(
            {
                "domain": domain,
                "username": owner,
                "document_root": record.get("document_root") or f"/home/{owner}/public_html",
                "type": "main",
            }
        )
    for record in _load_subdomains():
        domain = record.get("fqdn", "")
        owner = resolve_domain_user(domain, default_user=record.get("username", ""))
        if is_reserved_domain(domain):
            continue
        if not is_admin(current_user) and owner != username:
            continue
        options.append(
            {
                "domain": domain,
                "username": owner,
                "document_root": record.get("document_root") or f"/home/{owner}/public_html/{domain}",
                "type": "subdomain",
            }
        )
    return sorted(options, key=lambda item: item["domain"])


def resolve_domain(domain: str, current_user: Any) -> dict[str, str]:
    value = validate_domain_name(domain)
    for option in eligible_domains(current_user):
        if option["domain"] == value:
            return option
    raise HTTPException(status_code=404, detail="Domain is not available for this user")



def validate_node_version(version: str) -> str:
    value = str(version).strip()
    if value not in NODE_VERSIONS:
        raise HTTPException(status_code=400, detail="Unsupported Node.js version")
    return value


def default_app_root(domain_option: dict[str, str]) -> str:
    return str(Path(domain_option["document_root"]).resolve(strict=False))


def validate_app_root(app_root: str, domain_option: dict[str, str]) -> str:
    base = Path(domain_option["document_root"]).resolve(strict=False)
    requested = Path(app_root or str(base)).expanduser()
    if not requested.is_absolute():
        raise HTTPException(status_code=400, detail="Application root must be an absolute path")
    resolved = requested.resolve(strict=False)
    if resolved != base and base not in resolved.parents:
        raise HTTPException(status_code=400, detail="Application root must stay inside the selected domain root")
    return str(resolved)


def validate_port(port: int, current_app_id: Optional[str] = None) -> int:
    try:
        value = int(port)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid port")
    if value < PORT_MIN or value > PORT_MAX:
        raise HTTPException(status_code=400, detail=f"Port must be between {PORT_MIN} and {PORT_MAX}")
    owner = store.port_owner(value)
    if owner and owner != current_app_id:
        raise HTTPException(status_code=409, detail=f"Port {value} is already assigned")
    # Only probe the live socket for a port this app doesn't already own — otherwise
    # the app's own running process makes its unchanged port look "in use" and blocks
    # every config save.
    if owner != current_app_id and _port_listening(value):
        raise HTTPException(status_code=409, detail=f"Port {value} is already in use")
    return value


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def validate_env(env: Optional[dict[str, str]]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in (env or {}).items():
        key = str(key).strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise HTTPException(status_code=400, detail=f"Invalid environment key: {key}")
        text = "" if value is None else str(value)
        if "\x00" in text or len(text) > 4096:
            raise HTTPException(status_code=400, detail=f"Invalid environment value for {key}")
        clean[key] = text
    return clean


ROUTE_PATH_RE = re.compile(r"^(/[A-Za-z0-9._-]+)+$")
ROUTE_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
MAX_ROUTES = 10
# Prefixes the app itself must keep serving.
RESERVED_ROUTE_PREFIXES = ("/.well-known",)


def validate_routes(routes: Optional[list[dict]]) -> list[dict]:
    """Custom reverse-proxy routes: path prefix -> loopback port. Keep in
    sync with the core renderer's read-side whitelist (nginx_vhost) — the
    shape must be un-injectable into nginx config by construction."""
    clean: list[dict] = []
    seen: set[str] = set()
    for route in routes or []:
        path = str(route.get("path") or "").strip().rstrip("/")
        if not ROUTE_PATH_RE.fullmatch(path) or ".." in path or len(path) > 128:
            raise HTTPException(status_code=400, detail=f"Invalid route path: {path or '(empty)'} — use segments of letters, digits, dot, dash, underscore")
        if any(path == p or path.startswith(p + "/") for p in RESERVED_ROUTE_PREFIXES):
            raise HTTPException(status_code=400, detail=f"Route path {path} is reserved")
        if path in seen:
            raise HTTPException(status_code=409, detail=f"Duplicate route path: {path}")
        seen.add(path)
        host = str(route.get("host") or "127.0.0.1").strip()
        if not ROUTE_HOST_RE.fullmatch(host) or ".." in host:
            raise HTTPException(status_code=400, detail=f"Invalid upstream host for route {path}: use a hostname or IP")
        try:
            port = int(route.get("port"))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid port for route {path}")
        if not (1 <= port <= 65535):
            raise HTTPException(status_code=400, detail=f"Route port must be 1-65535 (got {port})")
        clean.append({"path": path, "host": host, "port": port, "strip_prefix": bool(route.get("strip_prefix", True))})
    if len(clean) > MAX_ROUTES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_ROUTES} custom routes per application")
    return clean


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^refs/[A-Za-z0-9_./-]+$")


def validate_repo(repo: str) -> str:
    value = (repo or "").strip()
    if not REPO_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Repository must be owner/name")
    return value


def validate_ref(ref: str) -> str:
    value = (ref or "").strip()
    if not REF_RE.fullmatch(value) or ".." in value:
        raise HTTPException(status_code=400, detail="Ref must be a fully qualified git ref, e.g. refs/heads/main")
    return value


def validate_command(command: str, field: str) -> str:
    value = (command or "").strip()
    if "\x00" in value or len(value) > 512:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return value


def _find_cert_paths(domain: str) -> tuple[str, str]:
    """Return (cert_path, key_path) checking all known HostPanel cert locations.

    For subdomains, falls back to the parent domain cert when no dedicated cert
    exists — parent certs issued by HostPanel include subdomains as SANs.
    """
    candidates = [domain]
    parts = domain.split(".")
    if len(parts) > 2:
        candidates.append(".".join(parts[-2:]))

    try:
        from modules.ssl.db import get_cert
        for candidate in candidates:
            cert = get_cert(candidate)
            if cert and cert.get("cert_path") and os.path.exists(cert["cert_path"]):
                key = cert["cert_path"].replace("fullchain.pem", "privkey.pem")
                return cert["cert_path"], key
    except Exception:
        pass

    for candidate in candidates:
        for base in (
            f"/opt/hostpanel/custom-certs/{candidate}",
            f"/opt/hostpanel/certs/live/{candidate}",
            f"/etc/letsencrypt/live/{candidate}",
        ):
            if os.path.exists(f"{base}/fullchain.pem") and os.path.exists(f"{base}/privkey.pem"):
                return f"{base}/fullchain.pem", f"{base}/privkey.pem"
    return "", ""


def cert_exists(domain: str) -> bool:
    cert_path, _ = _find_cert_paths(domain)
    return bool(cert_path)
