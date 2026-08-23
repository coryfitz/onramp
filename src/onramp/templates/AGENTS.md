# OnRamp project instructions

These instructions are intended for coding agents and automated development
tools working on __ONRAMP_APP_NAME__.

## Architecture

- Work from the project root for all `onramp` commands.
- `app/` is the Python backend.
- `build/` is the editable universal React Native frontend in the current
  OnRamp phase. It is not disposable build output.
- `build/ios/` and `build/android/` are native projects generated lazily.
- `BACKEND=False` disables launching the server; it does not mean the backend
  scaffold is absent. `onramp backend` changes that setting to `True`, and
  `onramp backend off` changes it back to `False`.
- Database startup and shutdown use Starlette lifespan management.
- Automatic schema generation is allowed only when `ENVIRONMENT` or
  `ONRAMP_ENVIRONMENT` is exactly `development`; deployments use migrations.

## Commands

- Create projects from their parent directory. The destination may be missing,
  empty, or contain only an initialized `.git` entry.
- `onramp run` starts web development and starts Python only when `BACKEND=True`.
- Frontend commands with the backend enabled open the ready API route in the
  system browser.
- `onramp ios` and `onramp android` add missing native projects, install native
  dependencies, choose a device, build, and launch.
- `onramp mobile` launches both native apps with separate Metro servers and at
  most one Python backend process.
- `--port` is the Python server port. `--metro-port` is the React Native port.
- `--watch-diagnostics` logs project-relative source events that can trigger native Fast Refresh.
- `onramp doctor <platform>` performs a read-only toolchain check.
- `onramp repair:ios` removes Pods but preserves `Podfile.lock`.
- `onramp repair:ios --fresh` also removes the lockfile and may change versions.
- `onramp upgrade --check` inspects migrations without mutation and reports
  whether the upgrade should be successful.
- `onramp upgrade` backs up managed files and stops on user-modified conflicts.

## Native behavior

- The first native run is intentionally slow and mutates `package.json`,
  `app.json`, `.nvmrc`, and the relevant native directory.
- A filesystem name such as `my-app` becomes a native identifier such as
  `MyApp`. Do not rename the project root after generating native projects.
- `build/app.json` is the declarative source for native display names,
  application identifiers, versions, build numbers, and the launcher icon.
- Use `onramp-js/secure-storage` for opted-in device-only secrets after adding
  `react-native-keychain`; never substitute browser storage for credentials.
- Add native npm dependencies in `build/` with `npm install --legacy-peer-deps`,
  then rerun `onramp ios` or `onramp android` for autolinking and rebuilding.
- OnRamp selects a free Metro port instead of reusing an unidentified server.
- Metadata-only filesystem events must not reach clients as empty HMR cycles;
  real source edits must continue to trigger Fast Refresh.
- Native runs check the vendor's newest compatible simulator packages and ask
  before installing, upgrading, or creating global emulator components.

## Routes and verification

- Routes come from files under `build/app/`. The deterministic fallback is
  `build/src/generated/routes.ts`; Metro and Webpack use ignored platform
  siblings so concurrent iOS and Android runs cannot overwrite one another.
- File discovery and matching stay identical across targets. Web route modules
  may be split dynamically, while native route registries eagerly import them
  to avoid Metro development-bundle Fast Refresh loops.
- Metro and Webpack regenerate routes during development. For deterministic
  checks, run `npm run build:routes` in `build/`.
- Before handoff, run relevant Python tests, frontend tests/type checks, and at
  least one real platform build when native files or dependencies changed.

## Upgrade metadata

- `.onramp/project.toml` records the project schema and framework versions.
- `build/.onramp/project.json` records the frontend schema and managed tooling.
- Files below `.onramp/backups/` are recoverable upgrade snapshots, not source.
