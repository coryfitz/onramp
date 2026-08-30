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
onramp test
```

Use `--environment development`, `--environment staging`, or
`--environment production` to select one backend, web, and native profile.
Frontend profile URLs, display-name suffixes, and identifier suffixes live in
`build/app.json`.

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

Database connections use Starlette lifespan startup and shutdown. New projects
set `AUTO_GENERATE_SCHEMAS=False`; committed Tortoise migrations are
authoritative in every environment. `DATABASE_URL` overrides the safe local defaults in
`app/settings.py`, so production credentials stay in the hosting provider's
secret environment. Use `onramp migrate [name]` to create and apply a migration
during development. Deployments use `onramp db upgrade`; `onramp db make`
creates a migration explicitly, and `onramp db check` reports pending work.
Production refuses the local SQLite fallback unless persistent SQLite was
deliberately selected with `ONRAMP_ALLOW_PRODUCTION_SQLITE=true`. Native
Tortoise migrations describe schema operations rather than database-specific
SQL, so the same committed chain works with SQLite in development and
PostgreSQL in production. Review any explicit `RunSQL` operation for backend
portability.

Set `AUTH['enabled'] = True` in `app/settings.py` to add OnRamp's explicit
email-only signup/signin, revocable sessions, roles, deletion hooks, and
verified notification subscriptions. Run `onramp migrate enable_accounts`
after enabling it. Development verification messages go to the ignored
`.onramp/dev-mail-outbox.jsonl`; staging and production need separate
`ONRAMP_AUTH_SECRET`, `ONRAMP_IDENTITY_SECRET`, and `RESEND_API_KEY` values.
Notification verification never creates an account. Manage classifications and
roles with `onramp account classify` and `onramp account role`.

Prepare and deploy the configured production targets with:

```bash
onramp deploy init
onramp deploy --check
onramp deploy
```

The default provider is Render. `onramp deploy init` detects the backend and web
frontend and records them as separate targets in `onramp.toml`; use `onramp
deploy init container` for provider-neutral artifacts only. When both targets
exist, interactive checks and deployments ask whether to operate on the
backend, frontend, or both. Noninteractive environments use the committed
`default_targets`. OnRamp validates and builds every selection before changing
production, then deploys the backend before the frontend. The last interactive
choice is remembered in ignored local state.

The first Render deployment requires a one-time connection of the generated
`render.yaml` Blueprint in the Render dashboard. Set each target's
`render_service`, or `ONRAMP_RENDER_BACKEND_SERVICE` and
`ONRAMP_RENDER_WEB_SERVICE`, for noninteractive multi-service deployments.
Environment-specific automation may instead use variables such as
`ONRAMP_RENDER_STAGING_BACKEND_SERVICE`.
Deployment topology belongs in `onramp.toml`; backend runtime behavior remains
in `app/settings.py`, and secret values stay in the provider environment or an
ignored local `.env` loaded by your shell or container tool.
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
