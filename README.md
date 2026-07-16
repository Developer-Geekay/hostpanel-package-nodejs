# hostpanel-nodejs

HostPanel package for provisioning Node.js applications behind nginx reverse proxy.

## Features

- Bundles lean Node.js 18, 20, 22, and 24 binaries for Linux ARM64.
- Lets users provision applications for existing main domains and subdomains.
- Excludes reserved `cpanel.*` and `ftp.*` hostnames.
- Stores application metadata, env vars, and lifecycle logs in HostPanel SQLite.
- Uses one systemd service per application.
- Maps nginx reverse proxy to port 80 and to port 443 when an existing Let's Encrypt certificate is available.
- Preserves application files when an app is deleted.

## Runtime Layout

The package ships only the `node` executable for each supported major version. Applications must already contain production files and dependencies. HostPanel runs applications; it does not run `npm install`, `npm run build`, yarn, or pnpm in v1.

```text
bin/
  node-18
  node-20
  node-22
  node-24
sources/
  node-18-linux-arm64.tar.xz
  node-20-linux-arm64.tar.xz
  node-22-linux-arm64.tar.xz
  node-24-linux-arm64.tar.xz
```

Git tracks the smaller source archives under `sources/`. `build.sh` extracts only each archive's `bin/node` into generated `bin/node-*` files before building the release zip.

Generated services run the selected binary directly, for example `/opt/hostpanel/plugins/nodejs/bin/node-22 server.js`.

## API

Prefix:

```text
/cpanelapi/nodejs
```

Main routes:

```text
GET    /apps
POST   /apps
GET    /apps/{app_id}
PUT    /apps/{app_id}
DELETE /apps/{app_id}
POST   /apps/{app_id}/start
POST   /apps/{app_id}/stop
POST   /apps/{app_id}/restart
GET    /apps/{app_id}/logs
GET    /domains
GET    /ports
GET    /runtime
GET    /count
```

## Push Deploys (in progress)

The package is gaining GitHub-push deploys: GitHub Actions builds a tarball, POSTs it to a
package route, and the plugin extracts it, flips a `current` symlink, and restarts the app's
unit. See `DEPLOY_PLAN.md` for the full phased plan and `manifest.schema.json` for the frozen
tarball contract.

Landed so far (Phase 0):

- `manifest.schema.json` — the deploy manifest contract (schema v1).
- `hostpanel_nodejs/ids.py` — `dep_<ULID>` deployment ids.
- `nodejs_deployments` table and deploy columns on `nodejs_apps` (additive migration; all
  defaults leave existing apps bit-identical — `deploy_enabled` is off).
- `plugin/tests/` — pytest suite covering ids and the migration (run with
  `python3 -m pytest plugin/tests`).

Landed in Phase 1 (release layout + manual activation):

- The deploy pipeline inspects and relinks paths inside user homes, which the core sudoers
  doesn't cover. Rather than granting raw `ln`/`mv`/`test` wildcards (a root-escalation
  primitive), the plugin installs a root-owned helper — `/opt/hostpanel/bin/hp-nodejs-deploy` —
  that validates every argument and confines operations to
  `<app_root>/{releases,current,previous}` under `/home`. `sudoers/hostpanel-nodejs` grants
  only that single command; both are installed automatically on package install/update.

- `hostpanel_nodejs/releases.py` — `releases/<sha>` + atomic `current`/`previous` symlink
  layout inside the app's `app_root`, activation, and rollback. Deploy and rollback are the
  same operation: relink + restart. Nothing is mutated in place.
- Deploy-enabled apps run their unit from `<app_root>/current`; apps with deploy mode off are
  untouched.
- Admin-only interim routes (replaced by the ingest endpoint + panel UI in later phases):

```text
POST /apps/{app_id}/deploy-mode   {"enabled": true|false}
POST /apps/{app_id}/activate      {"sha": "<short-or-full-commit-sha>"}
POST /apps/{app_id}/rollback      {"sha": "..."} (optional; defaults to previous)
GET  /apps/{app_id}/releases
```

Landed in Phase 2 (tarball ingest):

- `POST /apps/{app_id}/deploy` — multipart (`tarball`, `sha256`, `commit`), bearer-token auth
  (per-app token from `POST /apps/{app_id}/deploy-token`, admin, shown once). The pipeline
  streams the tarball to disk (never RAM), verifies the checksum, safety-scans every member
  (no absolute paths, `..`, links, device nodes; size caps), validates `manifest.json` against
  the app, extracts to the plugin staging dir, installs via `hp-nodejs-deploy install-release`
  (immutable — an existing SHA is refused), and activates. Deployment rows walk
  `received → verified → extracted → activated`; health verification arrives in Phase 5.
- `GET /apps/{app_id}/deployments` — deployment history.
- One deploy per app at a time; a concurrent POST gets `409`.
- Tarballs are retained under `/opt/hostpanel/plugins/nodejs/artifacts/<app_id>/`.

Landed in Phase 3 (reusable GitHub Actions workflow):

- `.github/workflows/node-deploy.yml` — `workflow_call` workflow that builds on GitHub's
  runners and POSTs the tarball to the Phase 2 endpoint. The Pi never builds anything.
- Consumer snippet (in the app repo, e.g. `.github/workflows/deploy.yml`):

```yaml
name: Deploy to HostPanel
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    uses: Developer-Geekay/hostpanel-package-nodejs/.github/workflows/node-deploy.yml@main
    with:
      app_id: <your-app-id>
      artifact_paths: dist server.js data package.json
    secrets:
      DEPLOY_URL: ${{ secrets.DEPLOY_URL }}
      DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
```

- Repo secrets required: `DEPLOY_URL` (panel origin) and `DEPLOY_TOKEN` (minted once via
  `POST /apps/{app_id}/deploy-token`). Both disappear in Phase 4 (OIDC).

Phase 1 manual flow (until GitHub Actions takes over in Phases 2–3):

1. Back up the app's unit file (`/etc/systemd/system/hostpanel-nodejs-<app_id>.service`) —
   restoring it plus `systemctl daemon-reload` and toggling deploy-mode off is the revert.
2. `POST /apps/{app_id}/deploy-mode {"enabled": true}` — creates `releases/`, `shared/`,
   `artifacts/`; the running unit is untouched until the first activation.
3. Build locally, copy the output (including a `manifest.json` per `manifest.schema.json`)
   to `<app_root>/releases/<short-sha>/`.
4. `POST /apps/{app_id}/activate {"sha": "<short-sha>"}` — flips `current`, rewrites the
   unit to run from it, restarts.
5. Repeat with a second SHA, then `POST /apps/{app_id}/rollback` and confirm the site
   reverts in under 5 seconds.

## Build

```bash
chmod +x build.sh bin/node-18 bin/node-20 bin/node-22 bin/node-24
./build.sh
```

The output is:

```text
hostpanel-nodejs-1.0.0.zip
```

## Release

Before tagging, ensure `plugin/setup.py` version matches the tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The GitHub workflow builds and publishes the matching zip.

## Operational Notes

- Allowed application port range is `31000-31999`.
- Users must pick a port explicitly.
- App files and dependencies must be uploaded or deployed before starting the app.
- Standard users only see domains and apps owned by their Linux user.
- Admins can manage all apps.
- Delete removes service and Node-owned nginx proxy config, but preserves files.
- Uninstall is blocked while apps exist unless forced.
