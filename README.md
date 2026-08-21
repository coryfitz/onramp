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
the backend scaffold for later use.

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

OnRamp manages database startup and shutdown through Starlette's lifespan API.
New projects default to `ENVIRONMENT="development"` and
`AUTO_GENERATE_SCHEMAS=True`, which creates missing tables for local
development. Automatic schema creation is always disabled outside the
`development` environment, even if the flag is left enabled. Set
`ONRAMP_ENVIRONMENT=production` (or update `app/settings.py`) and apply Aerich
migrations before starting a deployed backend.

Run a native app from the project directory:

```
onramp ios
onramp android
onramp mobile
```

`onramp mobile` prepares and launches both native apps. Each platform gets its
own project-owned Metro server, and a backend-enabled project starts only one
Python server for both apps.

Check a toolchain without changing the generated app:

```
onramp doctor web
onramp doctor ios
onramp doctor android
```

`--port` controls the Python backend. Native commands independently select a
free Metro port so they never attach to an unidentified bundler on port 8081.
Use `--metro-port <port>` to request a specific free port. For `onramp mobile`,
that is the iOS port and Android selects the next available port above it.
The selected Metro process remains attached to the command; press Ctrl+C to
stop it and any backend process OnRamp started for that run.

Native doctor checks validate an installed Watchman binary. Metro uses
Watchman when it is healthy and explicitly falls back to the native filesystem
watcher when Watchman is missing or broken. If Fast Refresh repeats
unexpectedly, run `onramp ios --watch-diagnostics`; OnRamp will print each
relevant source event with its exact project-relative path.

On macOS, `onramp ios` delegates the frontend launch to `onramp-js`. It
adds the iOS project if it is missing, checks Xcode and CocoaPods, installs
Pods, and checks Apple's preferred compatible Simulator runtime build on every
launch. OnRamp asks before downloading a missing or newer runtime through
Xcode, then selects a device on the newest installed runtime. If Xcode itself
is absent, OnRamp can open its Mac App Store page after permission, but Apple
requires the user to complete the Xcode installation.

`onramp android` delegates the frontend launch to `onramp-js`. It checks
Google's stable package list on every launch and asks before installing or
upgrading the Android Emulator, its stable system image, or a reusable virtual
device. It can bootstrap verified current Android command-line tools when the
installed `sdkmanager` is missing or obsolete. OnRamp selects JDK 17, enables
macOS clipboard sharing, cold-starts the selected AVD, and wakes it
automatically. These settings apply only to the frontend process, so no shell
profile editing is required.

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
onramp upgrade --to 0.5.2
```

The upgrader downloads a newer OnRamp release into a temporary environment
when necessary, runs each project-schema migration in order, updates Python
and npm metadata structurally, and saves changed files under
`.onramp/backups/`. Unchanged framework files update automatically. A managed
file edited by the application developer is never overwritten; the upgrade
stops and reports the conflict instead. Native projects remain lazy and are
not rebuilt merely to upgrade project metadata. Platform route registries are
generated separately for iOS, Android, and web so simultaneous mobile runs do
not overwrite shared route state.

Generated projects depend on a compatible release line such as
`onramp~=0.5.2`. Patch releases remain compatible with that project schema;
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
