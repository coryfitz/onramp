# __ONRAMP_APP_NAME__

An OnRamp __ONRAMP_PROJECT_KIND__ application.

## Project layout

- `app/` contains the Python backend, models, settings, and migrations.
- `build/` contains the shared React Native frontend when this is a full-stack app.
- `build/ios/` and `build/android/` are added lazily by the native commands.
- `AGENTS.md` contains project-owned instructions and a pointer to OnRamp's
  managed defaults in `.onramp/framework-guidance.md`.

Despite its name, `build/` is editable frontend source code in the current
OnRamp phase. Do not delete or regenerate it after making application changes.

Commit both instruction files. Put application context, constraints, and custom
agent instructions in root `AGENTS.md`. Read the framework guidance before
project work; its defaults do not remove project-specific constraints, and agents
should flag contradictory instructions instead of silently replacing them.

## Setup

```bash
uv sync
```

Run commands from this project root through the project environment:

```bash
uv run onramp run
uv run onramp ios
uv run onramp android
uv run onramp mobile
uv run onramp doctor ios
uv run onramp test
```

This guarantees that commands use the OnRamp version pinned in
`pyproject.toml` and `uv.lock`. If you also want a user-level command for
creating projects, install it in isolation with `uv tool install onramp`; never
install OnRamp into a shared pyenv or system/base Python with bare `pip`.

Use `--environment development`, `--environment staging`, or
`--environment production` to select one backend, web, and native profile.
Frontend profile URLs, display-name suffixes, and identifier suffixes live in
`build/app.json`.

Add `--force` to `onramp ios`, `onramp android`, or `onramp mobile` to use the
next available backend port when the requested port is occupied and accept
compatible emulator updates automatically for that run. Without `--force`,
OnRamp still asks before switching backend ports. Emulator updates may download
several GB rather than skipping the update check. Initial installs, repairs, and
display-only replacements still ask for consent.
The local native runtime URLs follow the selected backend port for that launch;
remote profile URLs and `build/app.json` remain unchanged.
Xcode-license acceptance, Xcode first-launch setup, and Rosetta installation
also require separate explicit confirmation; `--force` never accepts software
licenses. When approved, OnRamp performs those setup steps in the same native
run and verifies them before continuing.
`onramp mobile --force` also permanently deletes verified obsolete emulator files
and eligible old simulator devices with their saved app data. Active/current/newer
environments, custom Android devices, and uncertain files are preserved. The
separate `ios --force` and `android --force` commands still ask before cleanup;
compatibility checks and rejected-download cooldowns remain. Use `--rebuild`
separately if you want to force rebuilding the app itself.

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

OnRamp verifies the emailed code; Resend only delivers it. You do not need an
additional verification service. `onramp email --check` inspects the current
configuration without sending email or connecting to a database. Use
`onramp email test you@your-domain.com` for a preview, then add `--send` to send
one delivery-test message (to the local outbox in development). A production
test requires both `--send` and `--confirm-production`. No test message creates
an account, subscription, or verified-email token. Checks cannot confirm DNS,
API-key validity, or inbox delivery; test actual code entry in the app as well.
In staging/production, verify your sending domain in Resend and set
`ONRAMP_EMAIL_FROM`, `RESEND_API_KEY`, and an HTTPS `ONRAMP_PUBLIC_URL` in the
backend's secret environment. `AUTH.email_sender`, if configured, overrides
the outbox even in development; checks never import or invoke it.

Store a local backend secret through a hidden prompt instead of putting its
value in shell history:

```bash
onramp secret RESEND_API_KEY
onramp secret list
onramp secret check RESEND_API_KEY
# Only when staging needs a different value:
onramp secret RESEND_API_KEY --environment staging
```

Values are kept in the operating system credential store and scoped to this
project. A value stored without `--environment` is shared by development,
staging, and production. Add the flag only to create an environment-specific
override. Explicit process environment values take precedence, followed by an
environment override and then the shared value.
OnRamp injects local values only into the Python development server and backend
commands, never the web or native frontend toolchain. Delete a value with
`onramp secret delete RESEND_API_KEY`; adding `--environment staging` deletes
only that override. Do not append a
secret value to the command: positional values are refused because shell
history and process listings can expose them.

The same development outbox handles application notifications. Set
`ONRAMP_PUBLIC_URL` in hosted environments for signed unsubscribe links. Use
`onramp notifications report` to inspect aggregate counts,
`onramp notifications cleanup` to remove expired challenges and abandoned
unverified requests, and `onramp notifications anonymize <email>` to remove a
notification contact's identifiers while retaining anonymous history.
`onramp notifications dispatch ...` is a preview unless `--send` is explicit;
stable event keys prevent duplicate email across retries and provider-specific
subscriptions in the same environment. Account and notification request and
verification routes have configurable client-address limits. Configure a
production edge limit as well for distributed traffic: the built-in limiter
uses atomic database counters shared across workers and does not require Redis.
Its one-hour buckets contain only an environment/endpoint-scoped HMAC, count,
and expiry, never a raw client IP. Requests clean up at most 100 expired buckets;
`onramp notifications cleanup` removes at most 10,000 additional expired buckets
per run. Existing AUTH-enabled projects must create/apply the migration for
`ClientRequestRateLimit` before upgrading deployed workers.

Client addresses come only from the ASGI server. `onramp start` defaults
`ONRAMP_FORWARDED_ALLOW_IPS` to `127.0.0.1`; configure the actual trusted ingress
addresses/networks after verifying your host's proxy setup. Never use `*` unless
direct access is impossible and the ingress sanitizes forwarding headers.
Untrusted proxy traffic shares the proxy's conservative client limit. OnRamp
does not directly interpret `X-Forwarded-For`, even if a legacy project has
`trust_notification_proxy_headers` enabled.

Anonymous requests without remembered proof require a fresh email code.
Verification emails and
successful verified responses include a manage link; cancelling clears the
plaintext contact and outstanding codes while preserving the suppression
digest. Native clients should resolve the proof-gated `unsubscribe_path`
against their platform API base; the absolute email URL is HTTPS-only outside
development. Add `--unnotified-only` when a request should receive only its first
matching release. Runtime environment is part of both subscription identity and
delivery idempotency. Cleanup also removes database-backed challenge-limit rows
after 24 inactive hours; anonymization removes them immediately for that contact.

To remember notification email proof on a device, send `remember_email: true`
with the subscription verification request. A successful response includes
`notification_token` and `notification_token_expires_at` after the ready hook
finishes. Store the token privately and send it only in
`X-OnRamp-Notification-Token` on later subscription requests, keeping the email in
the JSON body. This proof spans resources and providers within the same resource
type and environment; it creates no account or session. Tokens have no expiry
by default (`AUTH['notification_contact_token_days'] = None`), and the response
explicitly includes `notification_token_expires_at: null`. A positive day count
opts into a fixed lifetime and an ISO expiry timestamp without renewal on
reuse. Let the server decide validity, including for legacy tokens whose
lifetime has since been migrated. Invalid proof returns
`401 notification_token_invalid`; clear it and
allow a fresh code request. `POST /api/notifications/contact/revoke` with that
header forgets the device proof without cancelling subscriptions. Cleanup
deletes only finite expired tokens; account deletion and contact anonymization revoke all
matching tokens. Anonymous requests without a token retain the same `202` shape
regardless of prior membership. Never put these tokens in logs, email, URLs,
account authorization, or shared application state.

Applications can configure a
`AUTH['notification_subscription_validator'] = 'module.callable'` hook to
restrict allowed resources and resolve `canonical_resource_id`. The hook sees
only a bounded request context; proxy headers remain untrusted unless explicitly
enabled. A `notification_subscription_ready_hook` receives the persisted
subscription, app directory, and bounded request context after verification;
failures remain retriable, so keep it idempotent. If an OnRamp upgrade changes
framework-owned models, generate and commit a migration with `onramp migrate
framework_notifications` before deploying. For an existing SQLite table, a
table-level unique-constraint change needs a reviewed table-rebuild migration;
SQLite's implicit auto-index cannot be removed with `DROP INDEX`.

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
in `app/settings.py`, and secret values stay in the provider environment or the
project-scoped OS credential store. For a configured Render backend, run
`onramp secret push RESEND_API_KEY`; it uses the deployment environment from
`onramp.toml`. A production override is needed only if the shared key differs.
The Render API token comes from `RENDER_API_KEY` or a hidden,
unsaved prompt. The push updates only that backend environment value and does
not replace `onramp deploy`. An ignored local `.env` loaded by your shell or
container tool remains supported.
When `AUTH.enabled` is true, the Render Blueprint generates separate signing
and identity secrets, derives the public action URL, and prompts for the Resend
key and verified sender. A custom `AUTH.email_sender` owns its own provider
configuration. Hosted CORS must list exact origins; wildcard origins fail the
deployment check.
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
an installed usable runtime. On macOS, the same preflight can offer Xcode's
interactive license and first-launch setup. On Apple silicon, it can offer
Rosetta when Google's installed Intel-only Android CLI requires it. Both flows
remain opt-in and are rechecked before native generation continues.

Native identity is declared in `build/app.json`. OnRamp synchronizes its
display name, package and bundle identifiers, versions, build numbers, and
1024×1024 PNG launcher icon on every native add or run.

## Framework upgrades

Check or preview an upgrade before applying it:

```bash
uv run onramp upgrade --check
uv run onramp upgrade
```

The check is non-mutating and ends by reporting whether the proposed upgrade
should be successful.

An older CLI can download the target OnRamp release into a temporary
environment to migrate this project. That does not update a separate global
executable. Continue using `uv run onramp` for the project-pinned version, or
update an isolated user-level installation with `uv tool upgrade onramp`.

Project version metadata is stored in `.onramp/project.toml`. OnRamp backs up
files it changes under `.onramp/backups/` and stops rather than overwriting a
modified framework-managed file.

OnRamp manages `.onramp/framework-guidance.md`, including its hashes, backups,
and conflict checks. Manual edits there are protected and can block an upgrade;
keep custom instructions in root `AGENTS.md` instead. Upgrades of schema 5 or
newer projects leave an existing root `AGENTS.md` unchanged.

When an older project upgrades to schema 5, OnRamp preserves its existing root
instructions and adds one prefixed instruction to read the new framework
guidance. Legacy paragraphs are not automatically merged or deleted; review
them manually after upgrading and retain project-specific constraints. Commit
both instruction files and the updated `.onramp/project.toml`. The upgrade
check previews these changes without modifying files.

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
