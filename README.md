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

permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    uses: Developer-Geekay/hostpanel-package-nodejs/.github/workflows/node-deploy.yml@main
    with:
      app_id: <your-app-id>
      artifact_paths: dist server.js data package.json
    secrets:
      DEPLOY_URL: ${{ secrets.DEPLOY_URL }}
```

- Repo secret required: `DEPLOY_URL` (panel origin). The caller must also grant
  `permissions: id-token: write` — see Phase 4 below.

Landed in Phase 4 (GitHub OIDC — no stored credentials):

- The workflow requests a short-lived GitHub OIDC token (audience
  `hostpanel-nodejs-deploy`, a protocol constant on both sides) and sends it as the deploy
  bearer token. The plugin verifies the signature against GitHub's JWKS (cached hourly,
  stale-tolerated on GitHub outages) plus `iss`/`aud`/`exp` with clock skew, then authorizes
  the token's `repository`/`ref` claims against the app row.
- Set the authorized source per app (admin):
  `POST /apps/{app_id}/deploy-mode {"enabled": true, "repo": "owner/name", "ref": "refs/heads/main"}`
  (`ref` defaults to `refs/heads/main` when `repo` is given). A valid token from any other
  repo or branch — including forks — gets `403` + an audit row carrying both the claimed and
  expected source.
- The static deploy-token mechanism is deleted: `POST /apps/{app_id}/deploy-token` is gone,
  the `DEPLOY_TOKEN` secret is no longer read, and app API responses never include
  credential material. (The legacy `deploy_token_hash` DB column remains, unused — dropping
  SQLite columns isn't worth the risk.)

Landed in Phase 5 (health checks, auto-rollback, retention):

- After activation the deploy polls `http://127.0.0.1:<port><manifest.health>` every
  `health_interval_s` (default 2 s) up to `health_timeout_s` (default 30 s). Healthy →
  deployment ends `healthy`. Unhealthy → automatic rollback to `previous`, restart,
  re-verify, deployment ends `rolled_back`, and the CI run fails with `502` + the reason.
  If the rollback itself fails (or no previous release exists on a first deploy), the
  deployment ends `failed` and nothing loops.
- Retention: after every healthy deploy, release dirs beyond `keep_releases` (default 5)
  are pruned oldest-first — the releases behind `current` and `previous` are never pruned
  regardless of age — and each pruned release's retained tarball is deleted with it.
- Deploys report `healthy`/`rolled_back`/`failed` truthfully to CI: only `healthy`
  returns 200, so a bad build shows up as a red run even though the site self-recovered.

Landed in Phase 7 (panel Deploy tab):

- Each app gains a **Deploy** tab in the Node.js panel: enable/disable push deploys, set the
  authorized `repo`/`ref`, see current/previous release SHAs, browse deployment history
  (commit, status, duration, timestamp, failure detail), roll back — to previous or any
  retained SHA — with a confirmation modal, and copy the GitHub workflow snippet with the
  app's real id pre-filled (that snippet is the onboarding flow).
- The tab degrades gracefully: fetch failures show an inline error and the rest of the panel
  keeps working; deploys never depend on the UI.
- Deploy-mode changes and rollbacks are admin-only (enforced server-side) and audit-logged.

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

## Custom Reverse Proxy Routes

Apps sometimes front more than one local service on the same domain (e.g. `/assistant-api`
proxied to a FastAPI backend on port 16000). Hand-editing the generated nginx vhost doesn't
survive regeneration — the plugin re-syncs vhosts on config save, restart, and startup.

Instead, declare routes in the app's **Configuration** tab (Custom Reverse Proxy Routes):
path prefix → loopback port, with an optional "strip prefix". They're stored in the DB
(`nodejs_app_routes`) and the core vhost renderer (core ≥ 1.2.0) emits a `location` block per
route ahead of the app's catch-all — so saving configuration *produces* the custom block
instead of erasing it. Validation is enforced on write (plugin) **and** re-checked on read
(core renderer): path segments limited to `[A-Za-z0-9._-]`, loopback upstreams only, max 10
routes, `/.well-known` reserved.

## Operational Notes

- Allowed application port range is `31000-31999`.
- Users must pick a port explicitly.
- App files and dependencies must be uploaded or deployed before starting the app.
- Standard users only see domains and apps owned by their Linux user.
- Admins can manage all apps.
- Delete removes service and Node-owned nginx proxy config, but preserves files.
- Uninstall is blocked while apps exist unless forced.
