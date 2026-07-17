from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from auth import User
from deps import get_current_user

from hostpanel_nodejs import audit, deploy, logs, nginx, oidc, process, releases, store, validators

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cpanelapi/nodejs", tags=["Node.js"])

# Machine-to-machine routes. Core mounts this via `public_routers`, i.e.
# WITHOUT the panel-session wrapper — every route here must do its own
# credential check and audit rejections (deploy tokens now, OIDC in Phase 4).
ci_router = APIRouter(prefix="/cpanelapi/nodejs", tags=["Node.js CI"])


class EnvVar(BaseModel):
    key: str
    value: str = ""


class NodeAppCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    domain: str
    app_root: Optional[str] = Field(default=None, max_length=4096)
    entrypoint: str = Field(default="server.js", max_length=256)
    start_command: str = Field(default="", max_length=512)
    node_version: str = Field(default="22")
    port: int
    env: dict[str, str] = Field(default_factory=dict)
    routes: list[dict] = Field(default_factory=list, description="custom reverse-proxy routes: {path, port, strip_prefix}")


class NodeAppUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    domain: Optional[str] = None
    app_root: Optional[str] = Field(default=None, max_length=4096)
    entrypoint: Optional[str] = Field(default=None, max_length=256)
    start_command: Optional[str] = Field(default=None, max_length=512)
    node_version: Optional[str] = None
    port: Optional[int] = None
    env: Optional[dict[str, str]] = None
    routes: Optional[list[dict]] = None


def _ensure_app_access(app: dict, current_user: User) -> None:
    if not validators.is_admin(current_user) and app["username"] != validators.current_username(current_user):
        raise HTTPException(status_code=403, detail="Access denied")


def _visible_apps(current_user: User) -> list[dict]:
    username = None if validators.is_admin(current_user) else validators.current_username(current_user)
    apps = store.list_apps(username=username)
    for app in apps:
        try:
            app["status"] = process.status(app["id"])
            store.update_app(app["id"], {"status": app["status"]})
        except Exception:
            pass
    return apps


def _build_app_data(request: NodeAppCreateRequest, current_user: User) -> tuple[dict, dict[str, str]]:
    domain_option = validators.resolve_domain(request.domain, current_user)
    app_root = validators.validate_app_root(request.app_root or validators.default_app_root(domain_option), domain_option)
    node_version = validators.validate_node_version(request.node_version)
    port = validators.validate_port(request.port)
    env = validators.validate_env(request.env)
    entrypoint = validators.validate_command(request.entrypoint, "entrypoint") or "server.js"
    start_command = validators.validate_command(request.start_command, "start command") or f"{process.node_bin(node_version)} {entrypoint}"
    data = {
        "id": validators.make_app_id(request.name, domain_option["domain"]),
        "name": request.name.strip(),
        "username": domain_option["username"],
        "domain": domain_option["domain"],
        "app_root": app_root,
        "entrypoint": entrypoint,
        "start_command": start_command,
        "install_command": "",
        "node_version": node_version,
        "port": port,
        "status": "provisioning",
        "ssl_enabled": validators.cert_exists(domain_option["domain"]),
    }
    return data, env


@router.get("/domains")
async def list_domains(current_user: User = Depends(get_current_user)):
    return validators.eligible_domains(current_user)


@router.get("/ports")
async def list_ports(current_user: User = Depends(get_current_user)):
    used = {app["port"]: app["id"] for app in store.list_apps()}
    return {
        "min": validators.PORT_MIN,
        "max": validators.PORT_MAX,
        "ports": [
            {"port": port, "available": port not in used, "app_id": used.get(port)}
            for port in range(validators.PORT_MIN, validators.PORT_MAX + 1)
        ],
    }


@router.get("/runtime")
async def runtime_info(current_user: User = Depends(get_current_user)):
    return process.runtime_versions()


@router.get("/apps")
async def list_apps(current_user: User = Depends(get_current_user)):
    return _visible_apps(current_user)


@router.get("/apps/{app_id}")
async def get_app(app_id: str, current_user: User = Depends(get_current_user)):
    app = store.get_app(validators.validate_app_id(app_id))
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    _ensure_app_access(app, current_user)
    return app


@router.post("/apps")
async def create_app(request: NodeAppCreateRequest, current_user: User = Depends(get_current_user)):
    data, env = _build_app_data(request, current_user)
    routes = validators.validate_routes(request.routes)
    app = store.create_app(data, env)
    if routes:
        store.set_routes(app["id"], routes)
        app = store.get_app(app["id"]) or app
    audit.log_action(current_user, "nodejs.app_create", app["id"], {"domain": app["domain"], "port": app["port"]})
    try:
        process.ensure_app_directory(app)
        process.write_service(app)
        process.start(app["id"])
        ssl_enabled = nginx.sync_vhost(app["domain"], app["username"])
        app = store.update_app(app["id"], {"status": "running", "ssl_enabled": ssl_enabled})
        audit.log_action(current_user, "nodejs.nginx_proxy_write", app["id"], {"domain": app["domain"], "ssl": ssl_enabled})
        return app
    except Exception as exc:
        process.remove_service(app["id"])
        store.delete_app(app["id"])
        audit.log_action(current_user, "nodejs.app_create", app["id"], {"error": str(exc)}, status="failed")
        raise


@router.put("/apps/{app_id}")
async def update_app(app_id: str, request: NodeAppUpdateRequest, current_user: User = Depends(get_current_user)):
    app_id = validators.validate_app_id(app_id)
    existing = store.get_app(app_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Application not found")
    _ensure_app_access(existing, current_user)

    domain_option = validators.resolve_domain(request.domain or existing["domain"], current_user)
    app_root = validators.validate_app_root(request.app_root or existing["app_root"], domain_option)
    next_version = validators.validate_node_version(request.node_version or existing["node_version"])
    next_entrypoint = validators.validate_command(request.entrypoint or existing["entrypoint"], "entrypoint")
    if request.start_command is None and next_version != existing["node_version"]:
        next_start_command = f"{process.node_bin(next_version)} {next_entrypoint}"
    else:
        next_start_command = validators.validate_command(request.start_command or existing["start_command"], "start command")
    patch = {
        "name": (request.name or existing["name"]).strip(),
        "domain": domain_option["domain"],
        "username": domain_option["username"],
        "app_root": app_root,
        "entrypoint": next_entrypoint,
        "start_command": next_start_command,
        "install_command": "",
        "node_version": next_version,
        "port": validators.validate_port(request.port if request.port is not None else existing["port"], current_app_id=app_id),
        "ssl_enabled": validators.cert_exists(domain_option["domain"]),
    }
    env = validators.validate_env(request.env) if request.env is not None else None
    updated = store.update_app(app_id, patch, env=env)
    if request.routes is not None:
        store.set_routes(app_id, validators.validate_routes(request.routes))
        updated = store.get_app(app_id) or updated
    process.write_service(updated)
    if existing["domain"] != updated["domain"]:
        nginx.sync_vhost(existing["domain"], existing["username"])
    ssl_enabled = nginx.sync_vhost(updated["domain"], updated["username"])
    updated = store.update_app(app_id, {"ssl_enabled": ssl_enabled})
    process.restart(app_id)
    audit.log_action(current_user, "nodejs.app_update", app_id,
                     {"domain": updated["domain"], "port": updated["port"], "routes": len(updated.get("routes") or [])})
    return updated


@router.delete("/apps/{app_id}")
async def delete_app(app_id: str, current_user: User = Depends(get_current_user)):
    app_id = validators.validate_app_id(app_id)
    app = store.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    _ensure_app_access(app, current_user)
    process.remove_service(app_id)
    store.delete_app(app_id)
    nginx.sync_vhost(app["domain"], app["username"])
    audit.log_action(current_user, "nodejs.app_delete", app_id, {"domain": app["domain"], "files_preserved": True})
    return {"status": "success", "message": "Application deleted; files preserved"}


@router.post("/apps/{app_id}/start")
async def start_app(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    process.start(app["id"])
    audit.log_action(current_user, "nodejs.app_start", app_id, {"domain": app["domain"]})
    return store.get_app(app["id"])


@router.post("/apps/{app_id}/stop")
async def stop_app(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    process.stop(app["id"])
    audit.log_action(current_user, "nodejs.app_stop", app_id, {"domain": app["domain"]})
    return store.get_app(app["id"])


@router.post("/apps/{app_id}/restart")
async def restart_app(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    process.restart(app["id"])
    # Re-assert the proxy vhost so Restart also repairs a clobbered/static vhost
    # (matches what users expect "Restart" to fix).
    try:
        nginx.sync_vhost(app["domain"], app["username"])
    except Exception as exc:
        logger.warning("vhost re-sync on restart failed for %s: %s", app.get("domain"), exc)
    audit.log_action(current_user, "nodejs.app_restart", app_id, {"domain": app["domain"]})
    return store.get_app(app["id"])


@router.get("/apps/{app_id}/metrics")
async def app_metrics(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    return process.metrics(app["id"])


@router.get("/apps/{app_id}/logs")
async def get_logs(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    return logs.app_logs(app["id"])


class DeployModeRequest(BaseModel):
    enabled: bool
    repo: Optional[str] = Field(default=None, max_length=140, description="owner/name authorized to deploy (OIDC repository claim)")
    ref: Optional[str] = Field(default=None, max_length=255, description="git ref authorized to deploy; defaults to refs/heads/main when repo is set")


class ActivateRequest(BaseModel):
    sha: str = Field(..., min_length=7, max_length=40)


def _ensure_admin(current_user: User) -> None:
    if not validators.is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.post("/apps/{app_id}/deploy-mode")
async def set_deploy_mode(app_id: str, request: DeployModeRequest, current_user: User = Depends(get_current_user)):
    """Toggle push-deploy mode (admin only, interim — panel UI arrives in Phase 7).

    Enabling creates the releases/ layout but leaves the running unit untouched
    until the first activation flips WorkingDirectory to current/. Disabling
    repoints the unit back at app_root and restarts.
    """
    _ensure_admin(current_user)
    app = await get_app(app_id, current_user)
    patch: dict = {"deploy_enabled": request.enabled}
    if request.repo is not None:
        patch["repo"] = validators.validate_repo(request.repo)
        patch["ref"] = validators.validate_ref(request.ref or "refs/heads/main")
    elif request.ref is not None:
        patch["ref"] = validators.validate_ref(request.ref)
    if request.enabled:
        releases.ensure_layout(app)
        updated = store.update_app(app["id"], patch)
        process.write_service(updated)
    else:
        updated = store.update_app(app["id"], patch)
        process.write_service(updated)
        process.restart(app["id"])
    audit.log_action(
        current_user, "nodejs.deploy_mode_set", app["id"],
        {"enabled": request.enabled, "repo": updated.get("repo"), "ref": updated.get("ref")},
    )
    return updated


@router.post("/apps/{app_id}/activate")
async def activate_release(app_id: str, request: ActivateRequest, current_user: User = Depends(get_current_user)):
    """Activate an already-extracted release (admin only). Phase 1 manual flow;
    the deploy ingest endpoint takes over extraction from Phase 2."""
    _ensure_admin(current_user)
    app = await get_app(app_id, current_user)
    if not app.get("deploy_enabled"):
        raise HTTPException(status_code=409, detail="Deploy mode is not enabled for this application")
    try:
        updated = releases.activate(app, request.sha)
    except HTTPException as exc:
        audit.log_action(current_user, "nodejs.release_activate", app["id"], {"sha": request.sha, "error": exc.detail}, status="failed")
        raise
    audit.log_action(current_user, "nodejs.release_activate", app["id"], {"sha": updated["current_sha"], "previous": updated["previous_sha"]})
    return updated


@router.post("/apps/{app_id}/rollback")
async def rollback_release(app_id: str, request: Optional[ActivateRequest] = None, current_user: User = Depends(get_current_user)):
    """Roll back to `previous` (default) or any retained SHA (admin only)."""
    _ensure_admin(current_user)
    app = await get_app(app_id, current_user)
    if not app.get("deploy_enabled"):
        raise HTTPException(status_code=409, detail="Deploy mode is not enabled for this application")
    to_sha = request.sha if request else None
    try:
        updated = releases.rollback(app, to_sha)
    except HTTPException as exc:
        audit.log_action(current_user, "nodejs.release_rollback", app["id"], {"to_sha": to_sha, "error": exc.detail}, status="failed")
        raise
    audit.log_action(current_user, "nodejs.release_rollback", app["id"], {"sha": updated["current_sha"]})
    return updated


@router.get("/apps/{app_id}/releases")
async def get_releases(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    return {
        "current_sha": app.get("current_sha"),
        "previous_sha": app.get("previous_sha"),
        "releases": releases.list_releases(app) if app.get("deploy_enabled") else [],
    }


@router.get("/apps/{app_id}/deployments")
async def list_deployments(app_id: str, current_user: User = Depends(get_current_user)):
    app = await get_app(app_id, current_user)
    return store.list_deployments(app["id"])


@ci_router.post("/apps/{app_id}/deploy")
def deploy_app(
    app_id: str,
    tarball: UploadFile = File(...),
    sha256: str = Form(...),
    commit: str = Form(...),
    authorization: Optional[str] = Header(default=None),
):
    """Tarball ingest for CI (GitHub Actions). GitHub OIDC auth — this is the
    one route with no panel session. Sync def on purpose: FastAPI runs it in
    the threadpool, where the streaming/extraction work belongs."""
    app = store.get_app(validators.validate_app_id(app_id))
    if not app:
        audit.log_action(deploy.CI_ACTOR, "nodejs.deploy", app_id, {"error": "unknown app_id"}, status="failed")
        raise HTTPException(status_code=404, detail="Application not found")
    if not app.get("deploy_enabled"):
        audit.log_action(deploy.CI_ACTOR, "nodejs.deploy", app["id"], {"error": "deploy mode disabled"}, status="failed")
        raise HTTPException(status_code=409, detail="Deploy mode is not enabled for this application")
    claims = None
    try:
        claims = oidc.verify(authorization)
        oidc.authorize(app, claims)
    except HTTPException as exc:
        detail = {"error": exc.detail}
        if claims:
            detail["repository"] = claims.get("repository")
            detail["ref"] = claims.get("ref")
        audit.log_action(deploy.CI_ACTOR, "nodejs.deploy_auth", app["id"], detail, status="failed")
        raise
    return deploy.run_deploy(app, tarball.file, sha256, commit)


@router.get("/count")
async def count_apps(current_user: User = Depends(get_current_user)):
    return {"count": len(_visible_apps(current_user))}
