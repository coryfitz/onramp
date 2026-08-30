# onramp

OnRamp is an early-stage full-stack Python framework for building apps that
run on the web, iOS, and Android with a shared React Native frontend.

## Installation

```bash
pip install onramp
```

## Project architecture

Generated full-stack projects have two source areas:

- `app/` is the Python backend, settings, models, and migrations.
- `build/` is the editable universal React Native frontend in the current
  OnRamp phase. Despite the directory name, it is not disposable output.

Native projects under `build/ios/` and `build/android/` are added lazily.
`BACKEND=False` disables launching Python alongside the frontend but preserves
the backend scaffold for later use. From the generated project root, run
`onramp backend` to change that setting to `BACKEND=True`, or run
`onramp backend off` to change it back to `BACKEND=False`.

## Create an app

Start a new OnRamp app:

```bash
onramp new <app_name>
```

Run this command from the new app's parent directory. The destination may be
missing, empty, or an initialized Git repository containing only `.git`;
OnRamp refuses other non-empty destinations. Generation is staged and only
published after the backend and frontend both succeed.

The default is web-first: it creates the shared universal frontend without
creating iOS or Android projects. Native projects are added automatically when
you first run `onramp ios` or `onramp android`.

Create the app with both mobile projects immediately:

```
onramp new <app_name> --mobile
```

Create every currently supported frontend platform:

```
onramp new <app_name> --all
```

Just create an API (backend)
```
onramp new <app_name> --api
```

Run the development server

```
cd <app_name>
onramp run
```
If you have only created an API (there is no frontend build folder) then onramp run will only start the dev server for the backend.

If you created a fullstack app, then onramp run will start the dev server for the frontend app and will also start the dev server for the backend app if in your settings you have BACKEND = True.

Whenever a web, iOS, Android, or combined mobile frontend starts with the
backend enabled, OnRamp opens the default API route at
`http://127.0.0.1:<port>/api` after the backend is ready. Browser visits show
the built-in API explorer, where routes can be filtered, expanded, and called
interactively. The generated OpenAPI document is available at
`/api/openapi.json`. Programmatic requests to `/api` continue to reach the
handler in `app/api/index.py`; add `?raw=1` when opening that response in a
browser.

Enable the backend for a generated project:

```
onramp backend
```

Disable it again without removing the backend scaffold:

```
onramp backend off
```

OnRamp manages database startup and shutdown through Starlette's lifespan API.
New projects default to `ENVIRONMENT="development"` and
`AUTO_GENERATE_SCHEMAS=False`; committed migrations are authoritative in every
environment. `DATABASE_URL`
takes precedence over `DATABASE` in `app/settings.py`, keeping production
credentials out of source control. Structured settings and the
`ONRAMP_DATABASE_*` variables can additionally configure pool size, connection
timeout, and TLS. Production refuses the local SQLite fallback unless
`ONRAMP_ALLOW_PRODUCTION_SQLITE=true` explicitly confirms that SQLite uses
intentional persistent storage.

Use the convenient combined migration command during development:

```bash
onramp migrate add_model_requests
```

For explicit stages, use `onramp db make [name]`, `onramp db upgrade`, and
`onramp db check`. Migration generation is blocked outside development;
deployments apply only committed migrations with `onramp db upgrade`.
OnRamp uses Tortoise ORM's native, operation-based migration format, so the
same committed migration chain can be generated with SQLite in development and
applied to PostgreSQL in production. New projects create and apply their
portable initial migration during setup.

OnRamp exposes `/health/live` for process health and `/health/ready` for a real
database readiness check. Browser access can be constrained with
`ONRAMP_ALLOWED_HOSTS` and `ONRAMP_CORS_ALLOWED_ORIGINS`.

Backend routes can be nested: `app/api/account/index.py` maps to
`/api/account`, while `app/api/items/[item_id].py` maps to
`/api/items/{item_id}`. `onramp.api` supplies structured JSON errors, body
validation, bearer-token parsing, and bounded pagination. `onramp test` runs
the configured backend and frontend checks together.

Set `AUTH['enabled'] = True` in `app/settings.py` to opt into passwordless,
email-only accounts and generic verified notification subscriptions, then run
`onramp migrate enable_accounts`. Built-in routes live under `/api/auth`,
`/api/account`, and `/api/notifications/subscriptions`. Signup is always
explicit; verifying a notification never creates an account. Codes and tokens
are stored as digests, attempts are rate-limited, native sessions use secure
storage, web can use HttpOnly cookies, and development mail goes to the ignored
`.onramp/dev-mail-outbox.jsonl`. Resend is the default production provider.

## Production deployment

Prepare the project's production targets and Render configuration:

```bash
onramp deploy init
onramp deploy --check
onramp deploy
```

The same deployment flow supports `--environment staging` and
`--environment production`. Environment-specific service IDs may use names
such as `ONRAMP_RENDER_STAGING_BACKEND_SERVICE`.

`onramp deploy init render` detects the Python backend and web frontend and
records them as separate targets in `onramp.toml`. It creates the necessary
portable container files and a `render.yaml` Blueprint without overwriting
existing files. The Blueprint provisions the API, PostgreSQL, and a static web
site when those components exist. `onramp deploy init container` prepares only
provider-neutral artifacts.

When both targets are configured, interactive `onramp deploy` and `onramp
deploy --check` ask whether to operate on the backend, web frontend, or both.
The previous deployment choice becomes the suggested default without changing
tracked files. A single target or combined full-application container proceeds
without an unnecessary question. The check remains read-only.

Before changing production, `onramp deploy` validates and builds every selected
target. When both services are selected, it deploys the healthy backend before
the frontend. Noninteractive environments use `[deploy].default_targets` from
`onramp.toml` instead of prompting. Multi-service Render automation can set
`render_service` on each target or use `ONRAMP_RENDER_BACKEND_SERVICE` and
`ONRAMP_RENDER_WEB_SERVICE`. The legacy `ONRAMP_RENDER_SERVICE` setting remains
available for a single selected service.

Deployment topology and nonsecret build settings belong in `onramp.toml`.
Backend runtime behavior remains in `app/settings.py`, while passwords, API
tokens, database URLs, and deploy hooks remain in the provider's secret
environment. A combined container can be represented as one target whose
`components` are `["backend", "web"]`.

Every host starts the production process with:

```bash
onramp start
```

This command listens on `PORT` (or `ONRAMP_PORT`), honors `ONRAMP_HOST`,
supports `ONRAMP_WORKERS`, and hands platform termination signals directly to
Uvicorn for graceful shutdown. Secrets belong in the provider secret manager
or an ignored local `.env` loaded by your shell or container tool, never in
`app/settings.py` or `onramp.toml`.

Run a native app from the project directory:

```
onramp ios
onramp android
onramp mobile
```

Pass `--environment development|staging|production` to select the matching
runtime/API profile from `build/app.json`. Development provides emulator-safe
loopback defaults; staging conventionally uses `.beta` native identifiers.

After the first successful native build, OnRamp reopens the installed app
without recompiling when its native inputs are unchanged. JavaScript and
TypeScript are still served fresh by Metro. Use `onramp ios --rebuild`,
`onramp android --rebuild`, or `onramp mobile --rebuild` to force native
compilation and installation.

`onramp mobile` prepares both native apps and launches Android before iOS so the
faster emulator is available first. Each platform gets its own project-owned
Metro server, and a backend-enabled project starts only one Python server for
both apps.

Check a toolchain without changing the generated app:

```
onramp doctor web
onramp doctor ios
onramp doctor android
```

`--port` controls the Python backend. Native commands independently select a
free Metro port so they never attach to an unidentified bundler on port 8081.
Use `--metro-port <port>` to request a specific free port. For `onramp mobile`,
that is the Android port and iOS selects the next available port above it.
The selected Metro process remains attached to the command; press Ctrl+C to
stop it and any backend process OnRamp started for that run.
If a coordinated frontend or native preflight fails, OnRamp stops the backend
and returns the failure instead of leaving a backend-only watcher running. The
backend watcher reloads for Python source changes while ignoring SQLite,
bytecode, static-file, and directory activity. OnRamp also owns the backend
worker's signal lifecycle, so one Ctrl+C shuts down and reaps both the frontend
and backend processes.

`onramp mobile` completes all interactive iOS and Android checks before either
Metro server starts. It then opens both emulator applications, keeps terminal
input in the coordinating OnRamp process, and prefixes concurrent Metro output
with `[iOS]` and `[Android]`. This prevents one platform's development server
from hiding or consuming the other platform's installation prompt.
Full native builds report elapsed activity while Xcode or Gradle is quiet, so
Xcode's final build-settings and installation work no longer looks stuck.

Native doctor checks validate an installed Watchman binary. On macOS, Metro
uses its native filesystem watcher; on other hosts it uses a healthy Watchman
installation and explicitly falls back when Watchman is missing or broken.
Cloud sync and indexing can still emit dependency metadata events without
changing module contents, so OnRamp suppresses only HMR cycles whose calculated
delta has no added, modified, or deleted modules. Real source edits continue to
use Fast Refresh normally. If refresh behavior remains unexpected, run
`onramp ios --watch-diagnostics`; OnRamp will print each relevant source event
with its exact project-relative path.

On macOS, `onramp ios` delegates the frontend launch to `onramp-js`. It
adds the iOS project if it is missing, checks Xcode and CocoaPods, installs
Pods, and checks Apple's preferred compatible Simulator runtime build on every
launch. OnRamp asks before downloading a missing or newer runtime through
Xcode, requests the exact build for the host architecture, and retries Xcode's
latest compatible runtime when necessary. A failed optional upgrade continues
with an installed usable runtime. When Xcode rejects both download forms,
OnRamp suppresses that exact failed build combination for 24 hours while
continuing to check changed Xcode or runtime metadata immediately. If Xcode
itself is absent, OnRamp can open
its Mac App Store page after permission, but Apple requires the user to
complete the Xcode installation.

`onramp android` delegates the frontend launch to `onramp-js`. It checks
Google's stable package list on every launch and asks before installing or
upgrading the Android Emulator, its stable system image, or a reusable virtual
device. It can bootstrap verified current Android command-line tools when the
installed `sdkmanager` is missing or obsolete. When Google's installer prints
only a download URL, OnRamp displays byte progress and then reports extraction
while the official Android CLI retains responsibility for installation and
verification. OnRamp gives that CLI an explicit native host platform,
preventing a translated CLI executable from installing an Emulator for the
wrong CPU architecture. On macOS it checks the installed Emulator binary and
offers to replace a mismatched copy for the native architecture. After
permission, it removes only the incompatible Emulator package, installs the
native package, and verifies the resulting executable; AVDs and system images
remain untouched. New AVDs use an explicit modern Pixel profile. OnRamp detects
generic low-resolution devices, asks before creating a sharper replacement
from the installed system image, preserves the old device and its app data,
and prefers the sharper matching AVD. App installation explicitly targets the
selected emulator even if another device remains online. Provider URL or
checksum mismatches are failures even when the provider exits with status
zero. Emulator processes that exit during startup report their own diagnostics
immediately instead of appearing to hang until the boot timeout. OnRamp selects
JDK 17, enables macOS clipboard sharing, cold-starts the selected AVD without
the boot animation, targets only its active CPU architecture during native
builds, and wakes it automatically. These settings apply only to the frontend
process, so no shell profile editing is required.

Native Home navigation resets the route stack to the generated root. Home
controls on ordinary and not-found screens use the shared navigation layer and
do not depend on browser globals.

Native application identity is configured once in `build/app.json`. On every
native add or run, OnRamp synchronizes the human display name, Android
application ID and version, iOS bundle ID and version, and a validated
1024×1024 PNG launcher icon. Native identifiers remain stable and separate
from human-facing names.

Apps that need device-only secret storage can opt into
`onramp-js/secure-storage` by installing `react-native-keychain` inside
`build/`. The adapter selects non-cloud, device-only iOS Keychain protection
and Android Keystore-backed storage and refuses to fall back to browser
storage.

Repair iOS dependencies while preserving the resolved versions:

```
onramp repair:ios
```

Use `onramp repair:ios --fresh` only when you deliberately want to remove
`Podfile.lock` and resolve native dependency versions again.

## Upgrade an existing project

OnRamp records the project schema, Python version, frontend version, React
Native version, and framework-managed file bases in `.onramp/project.toml`.
Inspect an upgrade before changing anything:

```bash
onramp upgrade --check
```

The check prints the complete non-mutating upgrade plan and ends with a clear
verdict explaining whether the upgrade should be successful.

Apply the latest release, or select one explicitly:

```bash
onramp upgrade
onramp upgrade --to 0.5.27
```

The upgrader downloads a newer OnRamp release into a temporary environment
when necessary, runs each project-schema migration in order, updates Python
and npm metadata structurally, and saves changed files under
`.onramp/backups/`. Unchanged framework files update automatically. A managed
file edited by the application developer is never overwritten; the upgrade
stops and reports the conflict instead. Native projects remain lazy and are
not rebuilt merely to upgrade project metadata. Platform route registries are
generated separately for iOS, Android, and web so simultaneous mobile runs do
not overwrite shared route state. Route discovery and matching stay identical
across targets: web retains route-level dynamic imports, while native route
modules are included in Metro's initial graph to avoid development-bundle Fast
Refresh loops.

Generated projects depend on a compatible release line such as
`onramp~=0.5.27`. Patch releases remain compatible with that project schema;
minor releases may introduce a schema migration handled by `onramp upgrade`.


The OnRamp App Framework Philosophy

Goal
Enable one person, with one Python codebase, to create an app that can run on any platform and scale from a startup to an enterprise

Design Considerations
Python on Everything
Python is a general-purpose programming language – therefore you should be able to use it to do anything a computer can do without needing to know another general-purpose programming language. There is no reason a Python developer should have to learn JavaScript to interact with web technologies – this is the perfect job for a compiler. OnRamp will allow programmers to create apps that run anywhere knowing only Python.

HTML and CSS are the Universal UI Primitives
With the advent of React Native, web technologies (HTML and CSS) are the universal primitives of UI design. Therefore, a Python web framework should not abstract away HTML and CSS.
As much as possible, syntax should be the same, no matter which platform you are writing for. One possibility would be to use React Native for Web, which uses native mobile primitives (such as <View>) and does create a unified syntax. However, this would separate OnRamp developers from web developers too much and make it difficult to use web tutorials. Therefore, React Strict DOM is a better choice for OnRamp. The only downside of this choice is that it uses a subset of DOM elements, and not all DOM elements, but the upside of still using HTML elements, plus the fact that Meta itself is putting more development effort into Strict DOM, make this the right choice for OnRamp.

Write Once, Run Anywhere
React Native allows us to use a popular web framework to create apps that will run on the web, as mobile apps, and even as TV or virtual reality apps. The Python ecosystem should take advantage of this technology.

Client-First
Client-first patterns provide the maximum amount of responsiveness, flexibility, and privacy necessary for the kinds of modern applications that the OnRamp project seeks to enable. Projects such as htmx have breathed new life into server-first patterns (which are similar to, but not the same as, hypermedia-driven apps), and naturally languages like Python, which due to the nature of the web and mobile platforms are more at home on the server than the client, work well with server-first frameworks. However, for the goal of OnRamp, a client-first approach is more appropriate (the only exception being if the user wants to only create an API). The challenge of getting Python into browsers and into phones is merely a technical one: Python can be compiled into JavaScript, React Native can handle the compilation into native code.

Don’t Hide Inherent Complexity
Creating server-client apps involves some inherent complexity. The programmer should never be unaware about whether their code will be running on the server or the client – that level of magic leads to confusion. Although the OnRamp programmer will not need to create two parallel apps themself, the server-client divide will be clear to the experience OnRamp programmer.

Gradual Typing
OnRamp should take full advantage of Python’s type hints, but the user should not need to use types to create a fully functional OnRamp app.

Async by Default
Modern applications are async by default and so is OnRamp. OnRamp uses Starlette as the backend API webserver and Tortoise as the ORM, both of which are async libraries.

Batteries-Included
OnRamp should include everything that you need to build a universal app, including auth. OnRamp apps should be easy to customize, but there should be an obvious OnRamp way of doing things, so that the beginning programmer can focus on the business logic of their app, not architectural decisions.

New Programmers are First-Class Citizens
The developer experience of new OnRamp programmers takes precedence over power users.

Specific Design Considerations

The long-term goal is for the `onramp-js` React Native frontend to be written
in Python and transpiled into React Native code. This is why the frontend lives
in a `build` directory. In the current phase, however, `build/` is frontend
source and must be edited and committed directly. `app/` remains Python
backend source; there is not yet a Python-to-frontend compiler or an
`app/components` frontend source tree.

Frontend Generator

The React Native frontend generator lives in the `onramp-js` directory and is published as the `onramp-js` npm package. The Python `onramp new` command invokes the compatible pinned version of that package to create the completed React Native app in the project's `build` directory.

When developing OnRamp from this repository, the Python CLI automatically uses
the local `onramp-js` source. `onramp-js/` is a separate Git repository and is
ignored by the parent Python repository, so inspect and commit both worktrees
separately. An installed OnRamp Python package uses the `onramp-js` version
specified in `src/onramp/config.toml`.

For a release, publish and verify `onramp-js` first, then update the Python
version and its `src/onramp/config.toml` pin. Test a disposable generated app
outside both repositories before publishing. For an unpublished frontend
version, install `npm pack` output through `ONRAMP_JS_PACKAGE_SPEC`; a `file:`
dependency can preserve source-repository module resolution and is not the same
as an installed package. Repeat the scaffold test using the exact public npm
version. After PyPI publishes, generate one final clean app with the exact
public Python version and run its frontend tests, typecheck, and production web
build. A local scaffold alone is insufficient because this source checkout
deliberately uses the nested JavaScript repository instead of the registry
package.

The generator can also be invoked directly. This creates a web-ready app in
`myapp/`:

```
npx onramp-js create myapp
```

Native projects can be added later:

```
cd myapp
npx onramp-js add ios
npx onramp-js add android
npx onramp-js add mobile
```

The standalone package also checks and runs each platform:

```
npx onramp-js doctor ios
npx onramp-js doctor android
npx onramp-js run web
npx onramp-js run ios
npx onramp-js run android
```

When invoked by the Python CLI, the generator prints the corresponding
`onramp` commands instead of suggesting or describing internal npm/npx
commands.

## Contributing

The repository-level `AGENTS.md` documents the two-repository workflow and the
generated-project invariants for humans and coding agents.

Run the Python tests with:

```
uv sync --extra dev
uv run --extra dev pytest
```

Run the frontend-generator tests separately:

```
cd onramp-js
npm test
```
