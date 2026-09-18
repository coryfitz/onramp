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

## Generated agent guidance invariants

- Root `AGENTS.md` belongs to the generated project. Its small starter template
  points agents to `.onramp/framework-guidance.md`; application instructions and
  customizations belong in the root file. Commit both files.
- `.onramp/framework-guidance.md` contains managed framework defaults. Retain
  hash checks, backups, and conflict protection for manual edits to this file.
  Framework defaults do not erase project-specific constraints; contradictory
  instructions must be flagged instead of silently overwritten.
- The schema 5 migration preserves all existing schema 0–4 root `AGENTS.md`
  content and adds one prefixed instruction to read the framework guidance.
  Never infer ownership of legacy paragraphs, merge or delete them automatically,
  or reject the migration merely because legacy root instructions were edited.
  Users may review retained legacy paragraphs manually after upgrading.
- Upgrades from schema 5 onward leave an existing root `AGENTS.md` unchanged.
  Do not add root `AGENTS.md` back to managed-file hashes or conflict checks.
  Keep `onramp upgrade --check` read-only, including for instruction migration.

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
- Put flexbox sizing and alignment on a layout element such as `html.div`.
  `html.span` becomes React Native `Text`, where `alignItems` and
  `justifyContent` do not center the element's own glyph. Centered badges need
  a layout container with a nested text-bearing element.
- Do not rely on CSS text inheritance through `html.div`: native `View`
  boundaries do not inherit typography, so keep box and layout styles on the
  div and put color, font, and text alignment on its nested text element.
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
- Local backend secrets managed by `onramp secret` belong in the operating
  system credential store, scoped by project and shared across environments by
  default. Environment-specific values are overrides. Never accept secret values
  as CLI arguments, print them, or inject them into frontend processes. Resolve
  explicit process/provider values first, environment overrides second, and the
  shared local value last.
- Provider secret handoff must be explicit and update one named backend value;
  it must not silently deploy, restart, or replace unrelated provider values.
- AUTH-enabled deployment scaffolds must provision distinct auth/identity
  secrets, a secure public action URL, and a configured email sender. Hosted
  backend checks reject wildcard CORS origins.
- Preserve legacy backend-only `onramp.toml` files and represent a combined
  frontend/backend container as one full-application target without prompting.

## Account and notification invariants

- Verification email customization is presentation-only through
  `AUTH.verification_email_renderer`: preserve the normal outbox/provider,
  escape dynamic HTML, include plain text, keep codes out of subjects, and
  distinguish notification proof from account creation, sign-in, and deletion.
  Never drop the signed unsubscribe footer from actual notification deliveries.
- `onramp email --check` and email-test previews must not contact a provider,
  invoke custom senders, connect to the database, or repair project files.
  Sending requires `--send` and, in production, `--confirm-production`.
  Provider acceptance is not inbox delivery or verification of the recipient;
  never print provider response bodies, secrets, or codes in hosted diagnostics.
- Notification intake must reject unknown or oversized payloads before
  persistence and pass only bounded request context to optional application
  validators. Client-address limits must use database-atomic, environment-scoped
  HMAC buckets shared across workers; never store raw addresses or fall back to
  process memory. Keep expiry cleanup bounded. Only the ASGI server may resolve
  forwarding headers using explicitly trusted proxy addresses; never parse
  client-supplied `X-Forwarded-For` inside application routes. These application
  limits do not replace production edge throttling against distributed traffic.
- Transactional event keys are application-global. Deduplicate delivery by
  recipient digest, event key, and environment; preserve provider idempotency
  across retries and preview dispatch unless an operator explicitly requests a
  send.
- Never deliver to unverified, suppressed, or anonymized subscriptions.
  Unsubscribe links show confirmation before mutation, are signed and
  replay-idempotent, and withdrawing consent removes active demand eligibility.
- Post-verification notification hooks are retriable after persistence and must
  be idempotent. Anonymous intake without valid remembered notification proof
  must require fresh proof and never reveal prior membership, suppression state,
  or a signed management capability.
- Remembered email proof is notification-only: issue an opaque, digest-stored
  capability only after successful code verification and the ready hook, bind it
  to email, resource type, and environment, and never use it as an account
  session. Proof has no expiry by default and remains revocable; an explicitly
  configured positive lifetime has a fixed expiry that reuse never extends.
  Validate its separate header atomically with persistence;
  revocation must either win first or wait for the authorized write. Account
  deletion and contact anonymization revoke matching remembered capabilities.
- Account deletion and notification-contact anonymization must clear recipient
  identifiers from subscriptions and delivery ledgers while retaining only
  privacy-safe aggregate history.
- Challenge resend and attempt limits must use database-atomic claims so
  concurrent workers cannot bypass them. Cancellation invalidates pending proof
  before consent can be restored, and verification is also client-rate-limited.

## Native launcher invariants

- Never attach an app to an unidentified Metro server merely because port 8081
  responds. Select a free port and pass it through to the React Native CLI.
- `--port` belongs to the Python backend; `--metro-port` belongs to Metro.
- `--watch-diagnostics` must report exact project-relative native source events.
- `--force` on `ios`, `android`, and `mobile` preapproves emulator updates.
  It also preapproves selecting the next available Python backend port when the
  requested port is occupied; without `--force`, changing ports still asks.
  The selected backend port must flow into generated local native runtime URLs
  without changing remote profile URLs or the app-owned `build/app.json`.
  On `mobile` only, it also preapproves verified obsolete emulator cleanup,
  including eligible old devices and their saved apps/data. Direct `ios` and
  `android` still ask before cleanup. Never treat it as blanket consent for
  first installs, architecture repairs, display-only replacements, or broad
  deletion outside the validated obsolete-emulator inventory.
  Preserve compatibility checks, rejected-download cooldowns, and `--rebuild`
  as the separate app-rebuild flag.
- Unchanged native inputs may reuse an app already installed on the same
  simulator or AVD. Keep that cache project-local and disposable, verify the
  installed app and target identity before reuse, and retain `--rebuild` as an
  explicit full-build escape hatch. Application source remains Metro-served.
  During an active native run, watch the same fingerprint inputs and warn once
  when they change that Fast Refresh cannot link native modules; a normal rerun
  must rebuild only the affected platform, with `--rebuild` reserved as a
  fallback.
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
- Xcode-license acceptance, Xcode first-launch setup, and Rosetta installation
  must be detected before native generation, explicitly explained and confirmed,
  and rechecked afterward. `--force` never accepts those software licenses.
  Automated privileged Xcode setup uses only fixed Apple system commands and the
  global `xcode-select` configuration; an explicit `DEVELOPER_DIR` is read-only
  for OnRamp and must never be passed through an automatically spawned `sudo`.
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
  old AVD and installed system image unless separately approved for cleanup,
  prefer the sharper matching device, and explicitly install and launch the
  app on that device when others are online.
- Native simulator launches must make the host keyboard type into a focused
  application text field without requiring clicks on the software keyboard.
  Enable and verify hardware-keyboard simulation for both legacy iOS Simulator
  and Xcode Device Hub before booting or opening the selected device. Ensure
  `hw.keyboard=yes` for Android AVDs in OnRamp's reserved canonical namespace
  before boot; when a running matching AVD needs that one-time repair, stop
  only that exact verified AVD and cold-start it without wiping its apps or
  data. Never rewrite AVDs outside the reserved namespace or ambiguous,
  malformed, symlinked, or noncanonical AVD metadata. If macOS denies narrow
  access to the iOS preference or a current Device Hub connection cannot be
  proven to have adopted it, continue with exact per-device UI guidance and
  never report keyboard forwarding as enabled for that device.
- Xcode Device Hub launches must keep the host and exact selected simulator's
  general pasteboards synchronized through Apple's supported `devicectl`
  session for as long as Metro is active. Bind cleanup to Metro shutdown,
  tolerate a sync failure without abandoning the app launch, and never read,
  persist, or log clipboard contents in OnRamp.
- Offer storage cleanup only after verifying a replacement runtime or AVD.
  Ask before removing shared iOS runtimes, Android system images, or virtual
  devices unless preapproved by `mobile --force`; explain effects on saved app
  data and other projects. Keep
  current/newer runtimes and active devices, recheck immediately before each
  removal, and skip cleanup if inventory is uncertain. Normal prompted iOS
  runtime cleanup preserves device data; `mobile --force` may remove shutdown
  devices for verified older runtimes and unavailable shutdown devices whose
  strictly older runtime is absent. Never delete referenced Android images or
  custom Android devices through automatic cleanup. Only known Android lock
  formats with definitely dead owners may be treated as stale; preserve live
  or uncertain locks. Obsolete OnRamp command-line-tool copies may be pruned
  automatically after validating their replacement; retain unrelated tools.
- Native CLI launches perform bounded, once-daily disposable-storage maintenance.
  Automatically prune only marked OnRamp temporary work owned by a definitely
  dead process after 24 hours, and verified Xcode DerivedData for deleted
  OnRamp temporary workspaces after seven days. Preserve symlinked, recent,
  active, ambiguous, and unmarked directories. Never delete the editable
  project `build/`, current DerivedData, dependency downloads, Pods, backups,
  simulators, or user data through this maintenance path.
- `onramp storage` and `onramp storage --check` are read-only storage inventories;
  `--clean` explicitly applies disposable-output cleanup. Other deleted projects'
  Xcode output requires `--include-other-projects`; it never broadens cleanup to
  existing projects or device data. Shared-runtime/image/AVD cleanup stays a
  separate native-preflight action, confirmed or preapproved by `mobile --force`,
  including when the newest
  replacement is already installed. Keep housekeeping optional and nonfatal.
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
- Generated iOS apps must use UIKit's scene lifecycle. Native synchronization
  may migrate the known React Native 0.86 legacy `AppDelegate` while preserving
  its initial properties, but must fail closed before modifying native files
  when a customized legacy bootstrap cannot be recognized safely. The normal
  native fingerprint must make that migration rebuild an older installed app.
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
