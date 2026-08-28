# __ONRAMP_APP_NAME__

An OnRamp __ONRAMP_PROJECT_KIND__ application.

## Project layout

- `app/` contains the Python backend, models, settings, and migrations.
- `build/` contains the shared React Native frontend when this is a full-stack app.
- `build/ios/` and `build/android/` are added lazily by the native commands.

Despite its name, `build/` is editable frontend source code in the current
OnRamp phase. Do not delete or regenerate it after making application changes.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run commands from this project root:

```bash
onramp run
onramp ios
onramp android
onramp mobile
onramp doctor ios
```

`BACKEND` in `app/settings.py` controls whether frontend commands also start
the Python server. The generated default is `False`; the backend scaffold is
still present and can be enabled later by running `onramp backend` from the
project root. Run `onramp backend off` to set it back to `False`. When a
frontend starts with the backend enabled, OnRamp opens
`http://127.0.0.1:<port>/api` in the system browser after the API is ready. That
page is an interactive explorer for the file-based routes in `app/api/`. Its
OpenAPI document is at `/api/openapi.json`. API clients still reach the default
`app/api/index.py` handler at `/api`; use `/api?raw=1` to view its raw response
in a browser.

Database connections use Starlette lifespan startup and shutdown. During local
development, `ENVIRONMENT="development"` and `AUTO_GENERATE_SCHEMAS=True` can
create missing tables automatically. OnRamp never generates schemas in other
environments. `DATABASE_URL` overrides the safe local defaults in
`app/settings.py`, so production credentials stay in the hosting provider's
secret environment. Use `onramp migrate [name]` to create and apply a migration
during development. Deployments use `onramp db upgrade`; `onramp db make`
creates a migration explicitly, and `onramp db check` reports pending work.
Production refuses the local SQLite fallback unless persistent SQLite was
deliberately selected with `ONRAMP_ALLOW_PRODUCTION_SQLITE=true`.
Generate migrations using the same database engine as production. New projects
wait until the first `onramp migrate` to create their initial migration, and
deployment checks reject migrations that are visibly for a different engine.

Prepare and deploy the production backend with:

```bash
onramp deploy init
onramp deploy check
onramp deploy
```

The default provider is Render. Use `onramp deploy init container` for only the
portable Docker files. Provider configuration is stored in `onramp.toml`, while
secret values stay in the provider environment or an ignored local `.env`
loaded by your shell or container tool. The first Render deployment requires a
one-time connection of the generated `render.yaml` Blueprint in the Render
dashboard; after that, `onramp deploy` validates, builds, deploys, waits for the
health check, and reports success or failure.
Production hosts run `onramp start`, which reads `PORT`, serves liveness at
`/health/live`, checks the database at `/health/ready`, and shuts down
gracefully.

`--port` controls the Python server. `--metro-port` selects a React Native
Metro port. OnRamp automatically selects a free Metro port when it is omitted.
If Fast Refresh repeats unexpectedly, run `onramp ios --watch-diagnostics` to
print the exact source paths Metro may be reacting to.
`onramp mobile` launches both native apps with separate Metro servers; an
explicit Metro port is used for iOS and Android starts above it.
The native command remains active while Metro is running; press Ctrl+C to stop
the development process cleanly.
Before launching, OnRamp checks the newest compatible iOS runtime or stable
Android Emulator and system image. It asks before downloading, upgrading, or
creating any global simulator components. iOS downloads select the host
architecture explicitly, and a failed optional runtime upgrade continues with
an installed usable runtime.

Native identity is declared in `build/app.json`. OnRamp synchronizes its
display name, package and bundle identifiers, versions, build numbers, and
1024×1024 PNG launcher icon on every native add or run.

## Framework upgrades

Check or preview an upgrade before applying it:

```bash
onramp upgrade --check
onramp upgrade
```

The check is non-mutating and ends by reporting whether the proposed upgrade
should be successful.

Project version metadata is stored in `.onramp/project.toml`. OnRamp backs up
files it changes under `.onramp/backups/` and stops rather than overwriting a
modified framework-managed file.

## Native dependencies

When adding a React Native package with native code, install it inside `build/`
using the project's peer-dependency strategy, then rerun the platform command:

```bash
cd build
npm install --legacy-peer-deps <package>
cd ..
onramp ios
```

`onramp repair:ios` preserves `Podfile.lock`. Use
`onramp repair:ios --fresh` only when you deliberately want to resolve a new
native dependency lockfile.

For device-only credentials, install `react-native-keychain@^10.0.0` inside
`build/` and import the secure value or JSON helpers from
`onramp-js/secure-storage`. The optional adapter uses device-only iOS Keychain
protection and Android Keystore-backed storage and rejects web use.
