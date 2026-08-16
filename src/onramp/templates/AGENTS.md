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
  scaffold is absent.

## Commands

- Create projects from their parent directory. The destination may be missing
  or empty, but must not contain files.
- `onramp run` starts web development and starts Python only when `BACKEND=True`.
- `onramp ios` and `onramp android` add missing native projects, install native
  dependencies, choose a device, build, and launch.
- `onramp mobile` launches both native apps with separate Metro servers and at
  most one Python backend process.
- `--port` is the Python server port. `--metro-port` is the React Native port.
- `onramp doctor <platform>` performs a read-only toolchain check.
- `onramp repair:ios` removes Pods but preserves `Podfile.lock`.
- `onramp repair:ios --fresh` also removes the lockfile and may change versions.

## Native behavior

- The first native run is intentionally slow and mutates `package.json`,
  `app.json`, `.nvmrc`, and the relevant native directory.
- A filesystem name such as `my-app` becomes a native identifier such as
  `MyApp`. Do not rename the project root after generating native projects.
- Add native npm dependencies in `build/` with `npm install --legacy-peer-deps`,
  then rerun `onramp ios` or `onramp android` for autolinking and rebuilding.
- OnRamp selects a free Metro port instead of reusing an unidentified server.

## Routes and verification

- Routes come from files under `build/app/` and are written to
  `build/src/generated/routes.ts`.
- Metro and Webpack regenerate routes during development. For deterministic
  checks, run `npm run build:routes` in `build/`.
- Before handoff, run relevant Python tests, frontend tests/type checks, and at
  least one real platform build when native files or dependencies changed.
