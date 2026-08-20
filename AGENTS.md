# OnRamp contributor instructions

OnRamp development spans two independent repositories:

- This repository contains the Python package and `onramp` CLI.
- `onramp-js/` is a separate Git repository containing the frontend generator
  and native launch tooling. It is intentionally ignored by the parent repo.

Always inspect and commit the two Git worktrees separately. A clean parent
`git status` says nothing about `onramp-js`.

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

## Native launcher invariants

- Never attach an app to an unidentified Metro server merely because port 8081
  responds. Select a free port and pass it through to the React Native CLI.
- `--port` belongs to the Python backend; `--metro-port` belongs to Metro.
- `--watch-diagnostics` must report exact project-relative native source events.
- Default iOS repair preserves `Podfile.lock`; only `--fresh` may remove it.
- Native project names are normalized and must remain stable after generation.
- `onramp mobile` launches iOS and Android with separate Metro servers while
  sharing at most one Python backend process.
- Python-wrapper output must not suggest or describe raw npm/npx commands.
