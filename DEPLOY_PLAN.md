# hostpanel-nodejs — Push-Based Deploy Plan

Adapted from the standalone `deployd` specification (2026-07-16). Status: plan — not yet implemented.

## Decision record

The original spec proposed `deployd` as a standalone service with its own daemon, SQLite DB,
`apps.yaml` config, and `/data/apps` tree, with HostPanel as a read-only client. That design was
**rejected in favor of full integration into `hostpanel-package-nodejs`**, because:

1. The package already owns the app registry, systemd units, nginx vhosts, port allocation,
   env injection, and audit logging. A standalone agent would create a second registry and a
   second owner for the same units and vhosts — exactly the ambiguity the spec's own
   `app_id` section warns against.
2. Project rules forbid parallel databases; all persistence lives in the HostPanel core SQLite DB.
3. This feature belongs to Node.js apps only. It ships as new capability of this package,
   not as a generic HostPanel feature or a separate product.

Trade-off accepted: push deploys require HostPanel to be running. The spec's
"deploys survive HostPanel downtime" property is deliberately given up.

## Mental model

> GitHub builds → GitHub POSTs a tarball to a HostPanel route → the package extracts it,
> flips a symlink, restarts the app's existing unit.

The Pi never compiles anything. Extraction + `systemctl restart` only — this matches the
package's existing v1 rule (no `npm install`/build on the server).

## Reused vs. new

| Concern | Original spec | Integrated plan |
|---|---|---|
| App registry | new `apps` table + `apps.yaml` reconciler | existing `nodejs_apps` table; no yaml, no reconciler |
| App identity | `app_` + ULID minted by deployd | existing package `id` (immutable slug PK, e.g. `portfolio-consoleapi-in`). Already the key of every route; never regenerated. **No id migration.** |
| Deployment identity | `dep_` + ULID | same — `dep_` + ULID, new id module |
| Service runtime | new `deployd` daemon on :8787 | new routes on the existing plugin router `/cpanelapi/nodejs` |
| Persistence | separate `deployd.db` | new tables in HostPanel core SQLite (WAL already on core) |
| systemd restart | new polkit/sudoers rule | core sudoers covers systemctl/tee/mkdir/rm; deploy filesystem ops go through the root-owned, argument-validating `/opt/hostpanel/bin/hp-nodejs-deploy` helper (installed by the plugin lifecycle) — the package sudoers grants only that one command; `tests/test_sudoers.py` forbids raw `ln`/`mv`/`test` grants |
| nginx exposure | new `/deploy/` location → :8787 | existing panel vhost; deploy route needs `client_max_body_size` raised for `/cpanelapi/nodejs/apps/*/deploy` |
| Env / secrets | `shared/.env` symlinked into release | existing `nodejs_app_env` table injected into the unit — **no `.env` file**; `shared/` kept only for persistent data dirs |
| Audit | new `audit_log` table | existing HostPanel audit (`audit.log_action`) — every transition and rejection |
| UI | new HostPanel plugin (Phase 7) | new views inside this package's existing frontend |
| Build system | `deploy-actions` reusable workflow | unchanged — separate repo `Developer-Geekay/deploy-actions` |
| `kind: static` | optional | **dropped** — this package is Node services only |

## Filesystem layout (per deploy-enabled app)

Inside the app's existing `app_root` (which stays inside the domain's document root and keeps
the existing ownership rules):

```
<app_root>/
├── releases/
│   ├── 9f2a1c/            # immutable, short commit SHA
│   └── 3b8d04/
├── current -> releases/9f2a1c    # atomic symlink; the deploy pointer
├── previous -> releases/3b8d04   # one-command rollback
└── shared/                       # persistent data, symlinked into each release
```

Retained tarballs live centrally in the plugin dir, not in user homes — the panel user owns
them (no sudo needed to write) and Phase 5 pruning stays a plain directory walk:

```
/opt/hostpanel/plugins/nodejs/
├── staging/                      # extraction scratch; helper-validated source for install-release
└── artifacts/<app_id>/<sha>.tar.gz
```

For deploy-enabled apps the generated unit's `WorkingDirectory` points at
`<app_root>/current` instead of `<app_root>`. Deploy = extract + relink + restart.
Rollback = relink + restart. Nothing is mutated in place.

**Feature flag:** `deploy_enabled` per app, default off. Apps with it off keep today's
behavior bit-identically (files uploaded manually, `WorkingDirectory=<app_root>`).

## Contracts

### `manifest.json` (tarball root, produced by the workflow)

```json
{
  "schema": 1,
  "app_id": "portfolio-consoleapi-in",
  "runtime": "node22",
  "entrypoint": "server.js",
  "health": "/healthz",
  "commit": "9f2a1c4e...",
  "built_at": "2026-07-16T09:14:22Z"
}
```

- `app_id` is the package's existing app id and is authoritative; mismatch with the
  path `app_id` → `409` + audit row.
- `health` required; polled on `127.0.0.1:<app.port>` after restart.
- Unknown `schema` → `400`. Versioned from day one.
- `kind` and `port_env` from the original spec are dropped: everything is a service, and the
  package already injects `PORT`.

### New tables (HostPanel core SQLite, migrations in `store.py`)

```sql
CREATE TABLE nodejs_deployments (
  id          TEXT PRIMARY KEY,          -- dep_<ULID>
  app_id      TEXT NOT NULL REFERENCES nodejs_apps(id),
  commit_sha  TEXT NOT NULL,
  status      TEXT NOT NULL,             -- received|verified|extracted|activated|healthy|failed|rolled_back
  detail      TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT
);
```

New columns on `nodejs_apps`: `deploy_enabled`, `repo`, `ref`, `health_path`,
`keep_releases` (default 5), `health_timeout_s` (default 30), `health_interval_s` (default 2),
`current_sha`, `previous_sha`, `deploy_token_hash` (interim auth only, dropped at OIDC phase).
App delete keeps the existing "files preserved" behavior and additionally removes the app's
`nodejs_deployments` rows (the FK requires it). The durable record of every deploy is the core
`audit_log`, which app deletion never touches.

### State machine

```
received → verified → extracted → activated → healthy
                │          │           │          │
                └──────────┴───────────┴──► failed
                                        └──► rolled_back
```

Every transition writes a `nodejs_deployments` update **and** an audit row. A deployment that
never reaches `healthy` must auto-rollback (Phase 5) or, before Phase 5, fail loudly and leave
`current` untouched.

### API (all under the existing `/cpanelapi/nodejs` prefix)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/apps/{app_id}/deploy` | deploy token → OIDC (Phase 4) | multipart: `tarball`, `sha256`, `commit`. `202` + `deployment_id`. |
| `GET` | `/apps/{app_id}/deployments` | panel session | history |
| `GET` | `/deployments/{deployment_id}` | panel session | status, timings, detail |
| `POST` | `/apps/{app_id}/rollback` | panel session | `{to_sha?}` — defaults to `previous` |

- The deploy route is the **only** route not using `get_current_user`; it has its own auth
  dependency (token, then OIDC) and never accepts panel sessions.
- Unknown `app_id` → `404` before a single byte of the body is written to disk.
  `deploy_enabled` off → `409`.
- One deploy per app at a time, locked on `app_id`; concurrent POST → `409`.

## Hard rules carried over unchanged

- No build tools run on the Pi. If `npm` appears in a deploy path, the phase has failed.
- Stream the tarball to disk — never buffer it in RAM.
- Tarball safety: reject absolute paths, `..` traversal, symlinks, device nodes, per-member and
  total-uncompressed size caps. Extract to a temp dir on the same filesystem, then `mv -T`.
- Atomic symlink swap: `ln -sfn` to a temp name + `mv -T`.
- Audit every transition and every rejection, carrying `app_id`.
- Doc-sync: code and `/guides/docs/` updates land in the same commit.
- Browser-first testing for all UI work and for package install/update on the panel.
- Phase discipline: branch per phase off `main` (`feat/deploy-phase<N>-<slug>`), squash-merge,
  tested revert procedure in the PR description, acceptance verified on the real Pi before the
  next branch is cut. `portfolio` is the only app onboarded until Phase 5 is green.

## Phases

### Phase 0 — Groundwork (this repo)

Branch: `feat/deploy-phase-0-groundwork`

1. Commit `manifest.schema.json` (contract above).
2. `ids.py`: `new_deployment_id()` → `dep_` + ULID; unit-tested for monotonicity/uniqueness.
3. `store.py` migrations: `nodejs_deployments` + new `nodejs_apps` columns (all nullable/off by
   default — zero effect on existing rows).
4. `docs/` + `/guides/docs/` updates describing the deploy architecture.

Acceptance: migrations run clean against a copy of the live DB; existing app CRUD unaffected;
`pytest` green. Impact: none.

### Phase 1 — Release layout + manual activation

Branch: `feat/deploy-phase-1-activate`

1. `releases.py`: create layout, atomic activate (relink `previous`/`current`, restart unit,
   update `current_sha`/`previous_sha`), rollback-to-sha. Pure Python, no bash script.
2. `deploy_enabled` toggle on an app switches its unit `WorkingDirectory` to
   `<app_root>/current` (unit rewrite via existing `write_service`).
3. Admin-only interim endpoint or CLI hook to activate an already-extracted release, to prove
   the flow before ingest exists.
4. Onboard `portfolio` only: build locally, upload the extracted release via existing means,
   activate, then activate the prior SHA and confirm revert < 5 s.

Impact: **the one disruptive edit** — portfolio's unit is repointed at `current/`. Back up the
unit file; revert = restore + `daemon-reload` + toggle flag off.

### Phase 2 — Deploy ingest endpoint (interim token auth)

Branch: `feat/deploy-phase-2-ingest`

1. `POST /apps/{app_id}/deploy`: resolve app row first; stream multipart to
   `<app_root>/artifacts/`; verify sha256; tarball safety checks; extract to temp; `mv -T` into
   `releases/<sha>`; activate via Phase 1 logic; write deployment rows through the state machine.
2. Interim auth: per-app static bearer token, hash in `deploy_token_hash`, shown once in the
   panel. Documented as temporary.
3. Panel nginx: raise `client_max_body_size` for the deploy route only (config change recorded
   with revert in the PR).
4. Failure paths (bad checksum, traversal, unknown id, disabled app, oversize, concurrent
   deploy) each rejected with the right status + audit row — covered by tests.

Acceptance: `curl` deploy of portfolio end-to-end; every rejection case verified; `htop` shows
no sustained CPU during deploy; existing apps untouched.

### Phase 3 — Reusable GitHub Actions workflow

Branch: `feat/deploy-phase-3-workflow` — **hosted in this repo** at
`.github/workflows/node-deploy.yml`, not a separate `deploy-actions` repo (operator decision,
2026-07-17: one less repo; the workflow evolves in the same PRs as the endpoint it talks to;
consumers pin `@main` since this repo's version tags belong to package releases; extract to its
own repo later only if non-HostPanel consumers appear).

1. `on: workflow_call` — inputs: `app_id` (required), `node_version` (default 22 — the package
   only ships 22/24 today), `install_cmd`/`test_cmd`/`build_cmd`, `artifact_paths`,
   `include_prod_node_modules`, `entrypoint`, `health`. Secrets (interim): `DEPLOY_URL`,
   `DEPLOY_TOKEN`.
2. Steps: validate inputs → checkout → setup-node + cache → install → test → build →
   assemble + generate `manifest.json` (via `jq`; all non-command inputs reach the shell only
   as quoted env vars) → tar + sha256 → POST to the Phase 2 endpoint, fail on non-200.
3. ~8-line consumer snippet in the portfolio repo calling
   `Developer-Geekay/hostpanel-package-nodejs/.github/workflows/node-deploy.yml@main`.
4. No Node-specific logic on the Pi side — Node-ness lives entirely in this workflow.

Acceptance: push to portfolio `main` → live within ~2 min, zero Pi build load; failing tests
block the deploy.

**Resolve before this phase:** is the panel URL reachable from GitHub-hosted runners (CGNAT?).
If not, a Cloudflare Tunnel / Tailscale Funnel fronting the deploy route is needed — the
endpoint design doesn't change, only how the POST arrives.

### Phase 4 — GitHub OIDC auth

Branch: `feat/deploy-phase-4-oidc`

1. Workflow: `permissions: id-token: write`; audience = the deploy URL origin; send as Bearer.
2. Plugin: fetch + cache GitHub JWKS (hourly refresh, serve stale on fetch failure); validate
   signature, `iss`, `aud`, `exp`, skew.
3. Authorize: `repository` claim must equal `nodejs_apps.repo`, `ref` claim must equal
   `nodejs_apps.ref` (set per app in the panel UI). Mismatch → `403` + audit row with app id,
   claimed repo, expected repo.
4. Remove the token path; drop `deploy_token_hash`; delete the GitHub secret.

Acceptance: no secrets anywhere; non-`main` branch rejected; forged token rejected; **a valid
token from repo A aimed at repo B's app id rejected with 403**.

### Phase 5 — Health checks, auto-rollback, retention

Branch: `feat/deploy-phase-5-health-rollback`

1. Post-restart poll of `http://127.0.0.1:<port><health_path>` every `health_interval_s` up to
   `health_timeout_s`.
2. On failure: relink to `previous`, restart, re-verify, mark `rolled_back`, audit. If the
   rollback itself fails: mark `failed`, stop, log loudly — never loop.
3. `POST /apps/{app_id}/rollback` (panel auth) to `previous` or any retained SHA.
4. Prune to `keep_releases` (releases + artifacts); never prune `current` or `previous`.
5. Per-app deploy lock (keyed on app id); concurrent deploy → `409`.

Acceptance: deliberately broken build auto-recovers; manual rollback to arbitrary retained SHA
works; 7 deploys with `keep_releases: 5` leaves exactly 5 of each.

**Onboard the second app here** (different repo), proving the full path: enable deploy on an
existing app in the panel → copy the snippet → deploy.

### Phase 6 — Zero-downtime blue/green *(optional, feature-flagged, only after Phase 5 is stable for weeks)*

Branch: `feat/deploy-phase-6-bluegreen`

`strategy: restart | bluegreen` per app, default `restart` (existing behavior untouched).
Blue/green uses templated units `hostpanel-nodejs-<app_id>@<sha>.service`, a second port from
the 31000–31999 range, health-check on the new port before traffic, nginx upstream flip +
reload, drain, stop old. Rollback = point the upstream back; instant. `nginx -t` + config
backup in the path. Highest live-site risk in the plan — gate hard.

### Phase 7 — Panel UI (this package's frontend)

Branch: `feat/deploy-phase-7-ui`

1. Per-app Deploy tab: enable toggle, repo/ref/health settings, current + previous SHA,
   deployment history (commit, status, duration, timestamp), rollback button with confirm,
   and the copy-pasteable workflow snippet **with the real app id pre-filled** — that snippet
   is the onboarding flow.
2. Rollback from the UI audit-logs as a user action with the app id.
3. All views keyed on app id (already the package convention).
4. Browser-first testing against `https://cpanel.consoleapi.in:2083`. Hard rule; not done on
   unit tests alone.

*(UI settings needed earlier — repo/ref for Phase 4, enable toggle for Phase 1/2 — may land as
minimal fields in those phases; this phase completes the full experience.)*

### Phase 8 — PR preview deployments *(stretch — cut first if energy runs out)*

Branch: `feat/deploy-phase-8-previews`

Previews are **not** new apps — no new `nodejs_apps` rows per PR. A preview is a child:
`POST /apps/{app_id}/previews/{pr_number}/deploy`, tracked in a `nodejs_previews` table FK'd to
the parent app, own lifecycle. OIDC `environment` claim gates previews separately from prod
(previews from any branch; prod pinned to `ref`). PowerDNS wildcard `*.dev.consoleapi.in` +
wildcard cert via existing `certbot-dns-rfc2136`; ephemeral unit on an assigned port; teardown
on PR close **and** a mandatory 48 h age reaper regardless of PR state; hard caps on concurrent
previews and a disk floor check. The caps are the feature.

## Definition of done (per phase)

- [ ] Branch cut fresh from `main`, named `feat/deploy-phase<N>-<slug>`
- [ ] Acceptance verified **on the actual Pi** (operator runs Pi-side steps where required)
- [ ] Docs + `/guides/docs/` updated in the same commit
- [ ] Every transition and rejection audited with the app id
- [ ] Revert procedure written, tested once, pasted into the PR description
- [ ] No sustained CPU/RAM load during a deploy (`htop` / `systemd-cgtop`)
- [ ] Nothing previously running is degraded; deploy-disabled apps bit-identical to today
- [ ] Squash-merged to `main`, reported back before the next branch

## Open decisions

1. **Inbound reachability from GitHub runners** — must be resolved before Phase 3. If CGNAT,
   front the deploy route with a tunnel; nothing else changes.
2. **Panel body-size limit** — confirm where 2082/2083 terminate (nginx? uvicorn direct?) and
   the right place to raise the upload cap for the deploy route only.
3. **App rename** — unsupported, as in the original spec. The package id is immutable; defer.
4. ~~**Node 18/20**~~ — resolved: 18/20 were dropped from the package (commits `817fb11`,
   `ea7dcdf`); the workflow default is 22 and the manifest schema allows `node22`/`node24` only.
