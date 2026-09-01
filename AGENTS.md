# OnRamp contributor instructions

OnRamp development spans two independent repositories:

- This repository contains the Python package and `onramp` CLI.
- `onramp-js/` is a separate Git repository containing the frontend generator
  and native launch tooling. It is intentionally ignored by the parent repo.

Always inspect and commit the two Git worktrees separately. A clean parent
`git status` says nothing about `onramp-js`.

## Release line

- Keep both packages on the `0.5.x` release line. Do not publish `0.6.0` or
  later unless the user explicitly authorizes that version change.

## Local versus published behavior

When this Python source checkout contains `onramp-js/bin/onramp-js.js`, the
Python bridge executes that local file directly. An installed Python package
instead invokes the `onramp-js` npm version pinned in
`src/onramp/config.toml`. Test both integration paths before publishing.

## Generated project model

- `app/` is Python backend source.
- `build/` is editable React Native frontend source in the current framework
  phase, despite the directory name.
- Native directories are generated lazily by `onramp ios` and
  `onramp android`.
- `BACKEND=False` keeps the scaffold while disabling backend launch.

Run Python `onramp` commands from a generated project root. Run standalone
`onramp-js` or npm commands from that project's `build/` directory.

## Development checks

For Python changes:

```bash
uv sync --extra dev
uv run --extra dev pytest
```

For frontend-generator changes:

```bash
cd onramp-js
npm test
```

For end-to-end scaffold work, create a disposable app outside both source
trees, verify its root metadata, then run its relevant platform command.

For upgrade changes, verify both a new manifest-bearing project and a legacy
project without `.onramp/project.toml`. Upgrade checks must not mutate either
tree, and a modified managed file must stop the upgrade before other files
change.

## Universal frontend styling invariants

- Shared modules that can render on web or native must import `css` and `html`
  from `react-strict-dom`. Never import `css` directly from
  `@stylexjs/stylex` in universal code; direct StyleX output is web-only and
  React Strict DOM's native renderer discards it. Direct StyleX imports belong
  only in explicitly web-only `.web.*` modules.
- Preserve the web Babel plugin order: the React Strict DOM transform, then
  StyleX, then the cleanup that removes only an unreferenced compiled
  React Strict DOM `css` import. The cleanup must remain web-only.
- When changing React Strict DOM, StyleX, Babel, or shared starter styles, run
  the `onramp-js` suite and test a freshly packed generated project. Its native
  tests must assert resolved style values on both starter routes; also run
  typechecking and a production web build with warnings treated as failures.

## Deployment invariants

- Keep `onramp deploy` as the single interactive deployment entry point. When
  separate backend and web targets exist, ask for backend, frontend, or both.
- `onramp deploy --check` uses the same target selection but remains read-only.
- Noninteractive runs use committed `default_targets`; never guess between
  multiple services when no default is configured.
- Validate and build every selected artifact before changing production. When
  deploying separate services together, deploy and health-check the backend
  before the web frontend.
- Deployment topology belongs in `onramp.toml`, runtime application behavior in
  `app/settings.py`, and secrets in the provider environment.
- Preserve legacy backend-only `onramp.toml` files and represent a combined
  frontend/backend container as one full-application target without prompting.

## Native launcher invariants

- Never attach an app to an unidentified Metro server merely because port 8081
  responds. Select a free port and pass it through to the React Native CLI.
- `--port` belongs to the Python backend; `--metro-port` belongs to Metro.
- `--watch-diagnostics` must report exact project-relative native source events.
- Unchanged native inputs may reuse an app already installed on the same
  simulator or AVD. Keep that cache project-local and disposable, verify the
  installed app and target identity before reuse, and retain `--rebuild` as an
  explicit full-build escape hatch. Application source remains Metro-served.
- Default iOS repair preserves `Podfile.lock`; only `--fresh` may remove it.
- Native project names are normalized and must remain stable after generation.
- `onramp mobile` launches iOS and Android with separate Metro servers while
  sharing at most one Python backend process.
- When a coordinated frontend process exits, stop its backend process and
  return the frontend's failure instead of leaving a backend-only watcher.
- Keep the backend worker outside the terminal foreground process group. The
  Python wrapper owns its shutdown, reaps it on Ctrl+C, and must not bypass
  `finally` cleanup with a hard process exit.
- Backend development reloads must respond to Python source changes, not
  SQLite writes, bytecode, static files, or directory metadata.
- `onramp mobile` completes every interactive native prerequisite check before
  starting either Metro server. Its Metro children must not read terminal input.
- After those preflights, `onramp mobile` launches Android before iOS and gives
  Android the requested Metro port so the faster emulator is available first.
- Long native component installs must surface byte progress when the provider
  exposes enough information and an elapsed activity state otherwise.
- Native compilation and installation must surface elapsed activity while
  Xcode or Gradle is otherwise silent.
- Android SDK installs must select the native host platform explicitly. On
  macOS, detect a non-native Emulator executable before launch, ask before
  repairing it, remove only the incompatible Emulator package before its
  native reinstall, verify the result, and preserve AVDs and system images.
- Treat provider URL or checksum mismatch output as a package-install failure
  even when the provider process exits successfully.
- Create Android AVDs with an explicit modern phone profile. Detect generic
  low-resolution AVDs, ask before creating a sharper replacement, preserve the
  old AVD and installed system image, prefer the sharper matching device, and
  explicitly install and launch the app on that device when others are online.
- Treat Android's namespace, base application ID, and variant application ID
  as distinct values. Debug `applicationIdSuffix` values must be included when
  checking, caching, and launching an installed app, while the activity class
  remains resolved from its namespace.
- Every Android launch must try to foreground the exact selected AVD on each
  supported desktop, including reused and cold-started emulators and cached or
  rebuilt apps. Resolve the host process from the selected emulator serial;
  never guess by process name or an unverified window title. Preserve that
  exact serial through coordinated launch stages, and return Android to the
  front after iOS opens on macOS. Windows focus refusal and generic Wayland
  focus restrictions remain nonfatal, must not be reported as guaranteed
  activation, and must provide accurate taskbar or task-switcher guidance.
- Native navigation to the initial route is a stack reset. Generated Home
  controls, including error screens, must work without browser globals.
- Bind iOS simulators to Metro through numeric IPv4 loopback. Avoid
  `localhost`, whose dual-stack resolution can repeatedly disconnect Fast
  Refresh on iOS 26 simulator runtimes.
- Use Metro's native file watcher on macOS. Suppress only HMR cycles whose
  calculated delta has no added, modified, or deleted modules; metadata-only
  dependency events must not show a refresh banner, while real edits must pass.
- Preserve the same file-based route discovery and matching across targets.
  Web routes may use dynamic imports for code splitting; native route registries
  must eagerly import their modules so Metro bundle registration cannot create
  an idle Fast Refresh loop.
- An optional native component that its provider rejects must not be presented
  as certainly downloadable or repeatedly offered without changed metadata or
  an explicit retry interval.
- Python-wrapper output must not suggest or describe raw npm/npx commands.
