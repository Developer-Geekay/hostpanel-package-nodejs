# hostpanel-nodejs Development Plan

This plan defines the first implementation of `hostpanel-package-nodejs` as a HostPanel package. It follows the HostPanel Package Development Guide and Package UI Guide, and incorporates the Node.js-specific provisioning requirements.

## Goal

Provide Node.js application hosting inside HostPanel:

- Bundle lean Node.js runtime binaries: 18, 20, 22, and 24.
- Run production-ready apps only; v1 does not install dependencies or build apps.
- Let admins and hosting users provision Node.js apps against an existing main domain or subdomain.
- Exclude reserved hostnames such as `cpanel.*` and `ftp.*` from target-domain selection.
- Auto-select the application folder from the selected domain's `public_html` root.
- Let the user explicitly select the app port from an allowed range.
- Store all app data in the database for future operations.
- Audit-log every operation.
- Capture logs for each provisioned Node.js application.
- Generate nginx reverse-proxy config for port 80 and port 443 when SSL exists for the selected domain.
- Allow apps to be edited and deleted after provisioning.
- Preserve domain, DNS, SSL, FTP, database, and Linux-user ownership boundaries.

## Current State

`hostpanel-package-nodejs` is currently an empty placeholder repository:

- No package layout.
- No Python plugin.
- No frontend.
- No runtime binaries.
- No service/sudoers.
- No build or release workflow.

This is a greenfield package, not a revamp.

## Target Structure

```text
hostpanel-package-nodejs/
  README.md
  PLAN.md
  build.sh
  test.scenario
  .gitignore
  .github/
    workflows/
      release.yml
  plugin/
    setup.py
    hostpanel_nodejs/
      __init__.py
      plugin.py
      apps.py
      lifecycle.py
      process.py
      nginx.py
      store.py
      validators.py
      audit.py
      logs.py
  frontend/
    main.js
  bin/
    node-18
    node-20
    node-22
    node-24
  conf/
    .gitkeep
  service/
    .gitkeep
  sudoers/
    hostpanel-nodejs
```

`service/` can remain empty in v1 because the package will generate one systemd unit per Node.js app.

## Runtime Strategy

Bundle Linux ARM64 Node.js binaries in `bin/`:

```text
bin/node-18
bin/node-20
bin/node-22
bin/node-24
```

Installed runtime paths:

```text
/opt/hostpanel/plugins/nodejs/bin/node-18
/opt/hostpanel/plugins/nodejs/bin/node-20
/opt/hostpanel/plugins/nodejs/bin/node-22
/opt/hostpanel/plugins/nodejs/bin/node-24
```

Each app stores its selected Node.js version. The generated systemd service must use that selected binary.

Supported runtime values:

| Runtime | Binary |
|---|---|
| Node.js 18 | `node-18` |
| Node.js 20 | `node-20` |
| Node.js 22 | `node-22` |
| Node.js 24 | `node-24` |

Acceptance criteria:

- `node-18 --version`, `node-20 --version`, `node-22 --version`, and `node-24 --version` work on the target server.
- Package install fails clearly if required runtime assets are absent.
- Runtime does not depend on `apt install nodejs`.
- App files and dependencies are prepared outside HostPanel before provisioning.

## Package Metadata

`plugin/setup.py`:

```python
from setuptools import find_packages, setup

setup(
    name="hostpanel-nodejs",
    version="1.0.0",
    packages=find_packages(),
    install_requires=["fastapi", "pydantic"],
    entry_points={
        "hostpanel.modules": [
            "nodejs = hostpanel_nodejs.plugin",
        ],
        "hostpanel.setup": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_install",
        ],
        "hostpanel.lifecycle": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:pre_uninstall",
        ],
        "hostpanel.hooks.on_startup": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_startup",
        ],
        "hostpanel.hooks.user_delete": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_user_delete",
        ],
        "hostpanel.hooks.domain_delete": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_domain_delete",
        ],
    },
)
```

`PLUGIN_MANIFEST`:

```python
PLUGIN_MANIFEST = {
    "requires_core": [1, 0, 0],
    "repository": "https://github.com/Developer-Geekay/hostpanel-package-nodejs",
    "nav_items": [{
        "nav_route": "nodejs",
        "nav_label": "Node.js",
        "nav_icon": "terminal",
        "nav_section": "hosting",
        "nav_section_label": "Hosting",
        "nav_section_order": 10,
        "admin_only": False,
    }],
    "dashboard_blocks": [{
        "type": "stat",
        "label": "Node Apps",
        "icon": "terminal",
        "endpoint": "nodejs/count",
        "size": "sm",
    }],
    "service": {
        "name": "nodejs",
        "unit": "hostpanel-nodejs",
        "label": "Node.js Apps",
        "icon": "terminal",
        "can_reload": False,
    },
}
```

## Database Model

Store Node.js app data in the HostPanel database, not in JSON. This makes future search, edit, audit, migrations, and cross-module operations easier.

Recommended tables:

```sql
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
```

Initial app shape:

```json
{
  "id": "api-example-com",
  "name": "API",
  "username": "testuser",
  "domain": "api.example.com",
  "app_root": "/home/testuser/public_html/api.example.com",
  "entrypoint": "server.js",
  "start_command": "node server.js",
  "install_command": "",
  "node_version": "22",
  "port": 31022,
  "status": "running",
  "ssl_enabled": true,
  "env": {
    "NODE_ENV": "production"
  }
}
```

Rules:

- Domain selection is required.
- Domain choices come from main domains and subdomains.
- Exclude `cpanel.*` and `ftp.*`.
- Standard users only see domains and apps owned by their `linux_user`.
- Admins can see and manage all apps.
- App root is auto-selected from the selected domain's `public_html` root.
- App root may be edited only within the allowed document root.
- Port selection is explicit and must be inside the allowed range.
- Every mutating operation is audit logged.
- Every provisioned application has retrievable logs.

## Provisioning Flow

Frontend flow:

1. User clicks `Add Application`.
2. A base-theme modal opens.
3. Modal loads target domains from `/cpanelapi/nodejs/domains`.
4. Domain dropdown includes:
   - Main domains.
   - Subdomains.
   - Only domains the current user may access.
   - No `cpanel.*`.
   - No `ftp.*`.
5. User selects a domain.
6. App root auto-fills from that domain's public HTML root.
   - Example: `/home/testuser/public_html/api.example.com`.
7. User selects Node.js version: 18, 20, 22, or 24.
8. User selects a port from the allowed range.
9. User enters app name, entrypoint, optional start command, and environment variables.
10. User clicks `Create` or `Provision`.

Backend provision operation:

1. Validate domain access.
2. Validate selected domain is not `cpanel.*` or `ftp.*`.
3. Resolve and validate app root.
4. Validate selected port is free and allowed.
5. Validate selected Node.js version.
6. Create app DB records.
7. Write environment DB records.
8. Create app directory if missing.
9. Generate systemd service.
10. Start app service.
11. Capture initial logs.
12. Write nginx reverse-proxy config.
13. Reload nginx.
14. Write audit entries for each major step.

If a later step fails, return a clear error and mark the app as `failed` with logs.

## Edit Flow

After creation, users can edit an application.

Editable fields:

- App name.
- Target domain.
- App root, constrained to the allowed public HTML root.
- Node.js version.
- Port.
- Entrypoint.
- Start command.
- Environment variables.

Edit behavior:

- Port changes regenerate the systemd service and restart the app.
- Runtime version changes regenerate the systemd service and restart the app.
- Domain changes rewrite nginx reverse-proxy config and remove the old Node-owned proxy.
- Env changes regenerate the service and restart the app.
- Every edit is audit logged.

## Delete Flow

Deleting an application should:

- Stop and disable the app service.
- Remove the generated systemd service file.
- Remove Node-owned nginx proxy config.
- Delete app DB records and env records.
- Preserve app files by default.
- Write audit logs.

File deletion can be added later as an explicit danger option. It should not be default.

## Process Management

Generate one systemd service per app:

```text
hostpanel-nodejs-<app_id>.service
```

Example service:

```ini
[Unit]
Description=HostPanel Node.js app <app_id>
After=network.target

[Service]
Type=simple
User=<username>
WorkingDirectory=<app_root>
Environment=NODE_ENV=production
Environment=PORT=<port>
ExecStart=/bin/bash -lc '/opt/hostpanel/plugins/nodejs/node-<version> <entrypoint>'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Important constraints:

- Run apps as their Linux owner, not root.
- Validate `app_id`, `username`, `app_root`, commands, env keys, and port before writing services.
- Prefer controlled start commands over arbitrary shell.
- Keep generated service files owned by root in `/etc/systemd/system/`.
- Capture app output through journald.
- Store important lifecycle log events in `nodejs_app_logs`.

## Port Selection

Allowed range:

```text
31000-31999
```

The frontend should let the user select a port. This avoids hidden automatic allocation conflicts in real server environments.

Server-side validation remains authoritative:

1. Port must be inside the allowed range.
2. Port must not be used by another Node.js app in DB.
3. Port must not already be listening on the OS.
4. Editing an app can keep its existing port.

Endpoints should expose port state:

```text
GET /cpanelapi/nodejs/ports
```

Return used and available ports so the UI can disable unavailable options.

## Backend API

Prefix:

```text
/cpanelapi/nodejs
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/apps` | List visible Node apps |
| `POST` | `/apps` | Provision app, DB records, service, nginx proxy, and logs |
| `GET` | `/apps/{app_id}` | Get app details |
| `PUT` | `/apps/{app_id}` | Edit metadata, commands, env, domain, runtime, and port |
| `DELETE` | `/apps/{app_id}` | Delete app resources and DB records; preserve files |
| `POST` | `/apps/{app_id}/start` | Start app service |
| `POST` | `/apps/{app_id}/stop` | Stop app service |
| `POST` | `/apps/{app_id}/restart` | Restart app service |
| `GET` | `/apps/{app_id}/logs` | Return per-app logs |
| `GET` | `/runtime` | Return Node 18/20/22/24 versions |
| `GET` | `/domains` | List eligible main domains and subdomains |
| `GET` | `/ports` | List used/available ports |
| `GET` | `/count` | Dashboard count |

Models:

- `NodeAppCreateRequest`
- `NodeAppUpdateRequest`
- `NodeAppResponse`
- `NodeRuntimeInfo`
- `NodeDomainOption`
- `NodePortOption`
- `EnvVar`

Validation:

- App names: safe display names, max length.
- App IDs: generated slug.
- Domains: must come from eligible domain/subdomain registry records.
- Domains: reject `cpanel.*` and `ftp.*`.
- Node version: one of `18`, `20`, `22`, `24`.
- Port: explicitly selected and available.
- Env keys: `^[A-Z_][A-Z0-9_]*$`.
- Env values: max length and no NUL bytes.
- Paths: must resolve under `/home/<username>/`.

## Audit Logging

Use HostPanel audit logging for every mutating operation.

Suggested actions:

```text
nodejs.app_create
nodejs.app_update
nodejs.app_delete
nodejs.app_start
nodejs.app_stop
nodejs.app_restart
nodejs.env_update
nodejs.nginx_proxy_write
nodejs.nginx_proxy_remove
nodejs.service_write
nodejs.service_remove
```

Audit details should include:

- `app_id`.
- `username`.
- `domain`.
- `port`.
- `node_version`.
- Changed fields for edits.

Do not log secret env values.

## Application Logs

Expose logs through:

```text
GET /cpanelapi/nodejs/apps/{app_id}/logs
```

Sources:

- `journalctl -u hostpanel-nodejs-<app_id>`.
- `nodejs_app_logs` for package lifecycle events.

UI:

- Add row action: `Logs`.
- Use a base-theme modal.
- Use `log-output` class.
- Include refresh action.
- Scope access to app owner/admin.

## Nginx Integration

Node.js integrates with the nginx package when installed, but does not own nginx itself.

Provisioning target:

- For selected domain, write reverse proxy to `127.0.0.1:<port>`.
- Always include port 80 config.
- If SSL is already provisioned for the selected domain, include port 443 config using existing certificate paths.
- If SSL is not provisioned, write HTTP-only config.
- Preserve DNS zones and SSL cert ownership.
- On app delete, remove only Node-owned proxy config.

Vhost path:

```text
/opt/hostpanel/plugins/nginx/vhosts/<domain>.conf
```

HTTP-only config:

```nginx
server {
    listen 80;
    server_name <domain>;

    location / {
        proxy_pass http://127.0.0.1:<port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

HTTPS config when cert exists:

```nginx
server {
    listen 80;
    server_name <domain>;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name <domain>;

    ssl_certificate     /etc/letsencrypt/live/<domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<domain>/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:<port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Guardrails:

- Check main domain ownership via `domain_registry._load_domains()`.
- Check subdomain ownership via `domain_registry._load_subdomains()`.
- Subdomains inherit and resolve to their parent main domain's system Linux user instead of requiring a separate user.
- Standard users can only attach domains they own.
- Exclude `cpanel.*` and `ftp.*`.
- If a non-Node vhost exists, block unless admin explicitly confirms replacement.
- Audit nginx proxy writes and removals.
- Reload nginx after successful config validation.

## Lifecycle Hooks

`on_install()`:

- Create `/opt/hostpanel/plugins/nodejs`.
- Run DB migrations.
- Validate `node-18`, `node-20`, `node-22`, and `node-24`.
- Run `systemctl daemon-reload`.
- Do not create app services until apps are provisioned.

`on_startup()`:

- Load app records from DB.
- Repair missing service files for registered apps.
- Refresh status from systemd.
- Verify journald access for logs.
- Do not overwrite app files.

`pre_uninstall(force=False)`:

- If apps exist and `force` is false, raise `409`.
- If forced:
  - Stop and disable Node app services.
  - Remove generated service files.
  - Remove Node-owned nginx proxy configs.
  - Remove Node DB records.
  - Remove `/opt/hostpanel/plugins/nodejs`.
  - Remove `/etc/sudoers.d/hostpanel-nodejs`.
- Preserve Linux users, home directories, domains, DNS zones, SSL certs, databases, and FTP accounts.

`on_user_delete(username, **kwargs)`:

- Stop/remove app services owned by that user.
- Remove app metadata for that user.
- Remove Node-owned nginx proxy configs for that user's apps.
- Write audit records.
- Preserve app files if core is not removing home.

`on_domain_delete(domain_name, **kwargs)`:

- Detach the domain from Node apps.
- Remove Node-owned proxy config for that domain.
- Mark affected app as `domain_detached`.
- Write audit records.

## Sudoers

`sudoers/hostpanel-nodejs` should be narrow.

Likely required commands:

```text
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl daemon-reload
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl enable hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl disable hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl start hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl stop hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl restart hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/systemctl is-active hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /bin/journalctl -u hostpanel-nodejs-*
%hostpanel ALL=(root) NOPASSWD: /usr/bin/tee /etc/systemd/system/hostpanel-nodejs-*.service
%hostpanel ALL=(root) NOPASSWD: /bin/rm -f /etc/systemd/system/hostpanel-nodejs-*.service
```

Validate with:

```bash
sudo visudo -c -f sudoers/hostpanel-nodejs
```

## Frontend UI

Use the HostPanel Package UI Guide.

Route:

```text
/app/nodejs
```

Registered as:

```javascript
window.__hpkg_sdk.register('nodejs', NodeJsPlugin)
```

Main UI:

- Page header:
  - Title: `Node.js`
  - Description: `Manage hosted Node.js applications`
- Card: `Applications`
- Primary action: `Add Application`
- `SdkDataTable` columns:
  - Name
  - Owner
  - Domain
  - Node
  - Port
  - Status
- Row actions:
  - Start
  - Stop
  - Restart
  - Logs
  - Edit
  - Delete

Add Application modal fields:

| Field | Type | Behavior |
|---|---|---|
| App name | Text | Required |
| Target domain | Select | Main domains and subdomains; excludes `cpanel.*` and `ftp.*` |
| App root | Text | Auto-filled from selected domain public HTML root |
| Node version | Select | 18, 20, 22, 24 |
| Port | Number/select | User-selected, validated against allowed range and conflicts |
| Entrypoint | Text | Defaults to `server.js` |
| Start command | Text | Optional; defaults to selected `node-<version> <entrypoint>` |
| Environment | Key/value rows | Optional |

Provision button behavior:

- Calls `POST /cpanelapi/nodejs/apps`.
- Shows progress states for DB save, install, service write, app start, nginx proxy write, and nginx reload.
- Shows a clear failure state if any step fails.

Edit Application modal:

- Allows changing app root, runtime version, port, domain, commands, and env.
- Regenerates nginx/systemd resources when affected fields change.
- Writes audit entries.

Logs viewer:

- Shows per-app logs from journald and `nodejs_app_logs`.
- Uses `log-output` base class.
- Includes refresh action.

Delete confirmation:

- Uses `SdkConfirmModal`.
- Copy should explicitly say app files are preserved.

Do not create a custom Node.js-specific theme. Reuse:

```text
page
page-header
page-title
page-desc
card
card-title
btn
btn-primary
btn-ghost
btn-danger
modal
field
empty
badge
log-output
```

## Build Script

`build.sh` should:

1. Read version from `plugin/setup.py`.
2. Fail if required runtime assets are absent:
   - `bin/node-18`
   - `bin/node-20`
   - `bin/node-22`
  - `bin/node-24`
3. Build `hostpanel-nodejs-<version>.zip`.
4. Include `plugin/`, `bin/`, `conf/`, `service/`, `sudoers/`, and `frontend/`.
5. Exclude dotfiles, source tarballs, previous zips, `__pycache__`, and `.pyc`.

## Release Workflow

Add:

```text
.github/workflows/release.yml
```

Use the safe pattern:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Get version from tag
        id: version
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"

      - name: Build zip
        run: |
          ./build.sh
          ZIP="hostpanel-nodejs-${{ steps.version.outputs.VERSION }}.zip"
          test -f "$ZIP"
          echo "ZIP=$ZIP" >> "$GITHUB_ENV"

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          name: "hostpanel-nodejs v${{ steps.version.outputs.VERSION }}"
          files: ${{ env.ZIP }}
          generate_release_notes: true
```

Before tagging, ensure `plugin/setup.py` version matches the tag without the leading `v`.

## Documentation

`README.md` should cover:

- What the package provides.
- Runtime strategy and target architecture.
- Package layout.
- Build instructions.
- Install/upload instructions.
- API prefix and routes.
- Frontend route.
- Domain selection behavior.
- Manual port selection behavior.
- Lifecycle behavior.
- nginx/domain/SSL integration behavior.
- Audit and app log behavior.
- What uninstall preserves.

## Test Scenarios

Create `test.scenario` with:

1. Install package from zip.
2. Verify `hostpanel-nodejs` appears in installed packages.
3. Verify `/app/nodejs` frontend loads.
4. Verify runtime versions endpoint returns Node 18/20/22/24 versions.
5. Add Application modal lists eligible main domains and subdomains.
6. Confirm `cpanel.*` and `ftp.*` are excluded.
7. Selecting a domain auto-fills app root from public HTML root.
8. User-selected port is accepted when free.
9. Used/conflicting port is rejected.
10. Admin provisions a Node app for `testuser`.
11. Standard user sees only their own apps.
12. App root validation rejects paths outside the selected domain root.
13. App service starts and systemd reports active.
14. App logs are visible in the Logs modal.
15. HTTP nginx reverse proxy works on port 80.
16. HTTPS nginx reverse proxy works on port 443 when SSL cert exists.
17. Edit app runtime, port, env, and domain.
18. Delete app and verify files are preserved.
19. User delete hook removes that user's Node apps.
20. Domain delete hook detaches domain and removes proxy config.
21. Uninstall blocked when apps exist.
22. Force uninstall removes Node-owned resources and preserves Linux users, home directories, DNS, SSL, databases, FTP, and app files unless explicitly deleted.

## Security Notes

Highest-risk areas:

- Running user-provided commands.
- Writing systemd service files.
- Writing nginx vhosts.
- Managing env vars that may contain secrets.
- Serving logs that may include secrets.

Guardrails:

- Restrict app roots to selected domain public HTML roots.
- Run apps as their Linux owner, not root.
- Validate env keys and limit env value sizes.
- Prefer direct entrypoint execution over arbitrary shell commands.
- Use timeouts for subprocess calls.
- Redact known secret env keys in API responses and audit details.
- Avoid exposing logs to users who do not own the app.
- Keep port selection inside the allowed range.
- Reject domain options that are reserved or not owned by the user.

## Suggested Implementation Order

1. Scaffold package layout.
2. Add Node 18/20/22/24 runtime binaries.
3. Add `plugin/setup.py` and manifest.
4. Implement DB migrations.
5. Implement domain option and port option endpoints.
6. Implement app store and validators.
7. Implement systemd process service generation.
8. Implement per-app logs.
9. Implement audit logging.
10. Implement backend API.
11. Implement lifecycle hooks.
12. Add nginx reverse-proxy integration for 80 and SSL-backed 443.
13. Add frontend UI using base HostPanel theme.
14. Add sudoers and validate it.
15. Add build script and release workflow.
16. Write README and test scenarios.
17. Test on a real HostPanel Linux ARM64 server.
18. Tag and release `v1.0.0`.

## Open Decisions

- Allow raw start commands, or only entrypoint-based starts.
- Whether a Node app may replace an existing static nginx vhost for the same domain.
- Whether dependency installation should run synchronously or as a background task.
- Whether to support Git clone/deploy in v1 or only existing local app directories.
- Whether app file deletion should be added as a separate danger action.
