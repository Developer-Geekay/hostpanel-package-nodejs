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
