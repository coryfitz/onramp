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
still present and can be enabled later.

`--port` controls the Python server. `--metro-port` selects a React Native
Metro port. OnRamp automatically selects a free Metro port when it is omitted.
`onramp mobile` launches both native apps with separate Metro servers; an
explicit Metro port is used for iOS and Android starts above it.
The native command remains active while Metro is running; press Ctrl+C to stop
the development process cleanly.

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
