# onramp

OnRamp is an early-stage full-stack Python framework for building apps that
run on the web, iOS, and Android with a shared React Native frontend.

## Installation

```bash
uv tool install onramp
```

This keeps OnRamp and its CLI dependencies isolated from Poetry and other
Python tools. Do not install OnRamp with bare `pip` into a pyenv or system/base
Python. Upgrade the isolated command with `uv tool upgrade onramp`; install an
exact release with `uv tool install --force onramp==VERSION`.

Show the installed OnRamp version:

```bash
onramp --version
onramp -v
```

Inside an existing OnRamp project, prefer `uv run onramp ...` to use the
framework version pinned by that project's `pyproject.toml` and `uv.lock`.
Contributors working from this source checkout should likewise use `uv run
onramp ...` rather than globally installing the checkout.

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

Generated projects keep agent instructions in two committed files:

- Root `AGENTS.md` belongs to the project. Add application-specific context,
  constraints, and workflow instructions there.
- `.onramp/framework-guidance.md` contains OnRamp's framework defaults and is
  managed by `onramp upgrade`.

The root file tells agents to read the framework guidance before project work.
Framework defaults do not remove project-specific constraints; agents should
flag contradictory instructions instead of silently replacing them. Commit
both files, and keep customizations in root `AGENTS.md` so framework guidance
can receive updates without conflicts.

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
are stored as digests, resend and incorrect-code limits are database-atomic,
native sessions use secure storage, web can use HttpOnly cookies, and
development mail goes to the ignored `.onramp/dev-mail-outbox.jsonl`. Resend is
the default production provider.

OnRamp verifies the user's emailed code itself; Resend only delivers the
message. No separate email-verification service or inbound email webhook is
needed. Development/test uses the local outbox even if `RESEND_API_KEY` is set,
unless `AUTH.email_sender` explicitly supplies a custom sender.

Check or test delivery from the project root:

```bash
onramp secret RESEND_API_KEY                         # hidden prompt; shared value
onramp secret RESEND_API_KEY --environment staging  # optional staging override
onramp email --check
onramp email test you@your-domain.com                  # preview only
onramp email test you@your-domain.com --send           # local outbox in development
onramp email --check --environment staging
onramp email test you@your-domain.com --environment staging --send
# A real production test also requires --confirm-production.
```

The check reads the active settings/environment, never contacts a provider,
and does not connect to or change the database. It checks authentication
secrets, the public action URL, and sender/key configuration without printing
secrets. It cannot prove API-key validity, DNS verification, or inbox delivery.
The test sends one non-verification message through the normal sender only when
`--send` is explicit; it never creates an account, subscription, or remembered
email permission. A custom sender overrides the local outbox and is not invoked
by checks or previews. Passing checks does not validate custom sender code.

For hosted delivery, verify your sending domain in Resend and set a
domain-scoped sending-access `RESEND_API_KEY`, `ONRAMP_EMAIL_FROM`, a public
HTTPS `ONRAMP_PUBLIC_URL`, and separate `ONRAMP_AUTH_SECRET` and
`ONRAMP_IDENTITY_SECRET` values of at least 32 characters. Keep these server-side
and use separate staging/production configuration. The provider's accepted
message ID means accepted for delivery, not proof it reached the inbox. Confirm
receipt in your own mailbox, then run the actual app code-entry flow. See
[Resend sending domains](https://resend.com/docs/knowledge-base/how-do-I-create-an-email-address-or-sender-in-resend)
and [sending API keys](https://resend.com/docs/dashboard/api-keys/introduction).

### Secrets

OnRamp can store backend secrets in the operating system credential store,
scoped to the current project. The safe shorthand prompts for a shared value
without displaying it:

```bash
onramp secret RESEND_API_KEY
onramp secret list
onramp secret check RESEND_API_KEY
onramp secret delete RESEND_API_KEY
onramp secret copy RESEND_API_KEY
```

Never add the value as another command argument. A command such as `onramp
secret RESEND_API_KEY actual-value` is refused because command arguments can
remain in shell history and can be visible in process listings. Explicit
process environment values take precedence over locally stored values. Local
secrets are added only to the Python development server and backend maintenance,
account, email, and notification commands; they are not passed to web or native
frontend tooling.

The shared value is used in development, staging, and production. Add
`--environment staging` or `--environment production` only when that environment
needs a different value. Resolution order is: explicit process/provider value,
environment-specific override, then shared project value.

On macOS, `onramp secret copy RESEND_API_KEY` copies the effective secret for
the configured deployment environment without displaying it or putting it in
shell history. This is useful for the first Render Blueprint setup, before a
service exists for `secret push`. Paste it promptly and replace the clipboard
contents afterward: other apps, clipboard managers, and clipboard history may
still be able to read a copied secret. Use `--environment staging` or
`--environment production` to select a different override.

For a configured Render backend, push the resolved value explicitly:

```bash
onramp secret push RESEND_API_KEY
# Only if production uses a different key, set the override before pushing:
onramp secret RESEND_API_KEY --environment production
onramp secret push RESEND_API_KEY --environment production
```

The push command uses the backend service ID from `onramp.toml` or the existing
`ONRAMP_RENDER_*_SERVICE` variables. It reads `RENDER_API_KEY` from the current
process, or asks for it through a hidden prompt without saving it. The secret
value and provider token are never printed. Pushing updates the provider's
environment; deployment remains an explicit `onramp deploy` operation.

Verification messages include table-based HTML and a plain-text fallback. For
project branding, set `AUTH['verification_email_renderer']` to a synchronous
`module.callable` reference. It accepts a frozen `VerificationEmailContext`
(`purpose`, `code`, `app_name`, `expires_minutes`, `public_url`, `manage_url`)
and returns `EmailTemplate(subject=..., text=..., html=...)`, both imported from
`onramp.auth.email_templates`. Keep this function presentation-only: escape all
dynamic HTML, use safe absolute links, and do not perform I/O or send mail. It
receives no recipient address, account token, or provider secret. Returning a
template keeps the normal development outbox and hosted provider; this differs
from `AUTH.email_sender`, which replaces delivery itself. An invalid renderer
fails closed without exposing its exception or sending a partial message.

The default template distinguishes notification proof from account creation,
sign-in, and deletion, displays the configured code lifetime, and keeps the code
out of the email subject. Projects may omit the pending-request manage link in
verification templates; this does not remove signed unsubscribe links from
actual notification deliveries. Test every purpose and inspect the `html` and
`text` fields in the local outbox before testing real delivery. Mail-client
rendering should use inline styles, tables, and system fonts, not scripts,
flexbox, or framework-specific web CSS.

Notification batteries also include retry-safe transactional delivery, a
delivery ledger, signed per-subscription unsubscribe links, and privacy-safe
aggregate reporting. An application may set
`AUTH['notification_subscription_validator']` to a full
`module.callable` reference. The callable receives `payload`, `account`, a
bounded `request_context`, and `app_dir`, and returns the normalized payload;
it can reject application-specific resources or assign a
`canonical_resource_id`. Generic subscription JSON rejects unknown top-level
fields and is limited by `AUTH['notification_request_bytes']` (16 KiB by
default). Metadata has its own `notification_metadata_bytes` limit.

Anonymous subscription requests without remembered proof always return the same
`202` shape and require a fresh emailed proof, even when that address previously
requested the same resource. An authenticated account or a valid remembered
notification token can subscribe immediately. Successful verification and
immediate verified subscriptions return the signed `unsubscribe_url`; unproved
intake never exposes it. The verification email
also contains that manage link, so a pending request can be cancelled before
verification. Cancelling clears the plaintext contact and account link, removes
outstanding verification codes, and preserves only the contact digest needed
for suppression and aggregate history until a fresh verified request restores
consent. Verified responses also include a signed relative `unsubscribe_path`
so native clients can resolve the action against their platform-specific API
base instead of trying to open a desktop loopback URL.

Clients may opt in to remembering notification email proof by sending
`remember_email: true` to `POST /api/notifications/subscriptions/verify`. After
successful verification and the ready hook, its response includes
`notification_token` and `notification_token_expires_at`. This opaque 256-bit
capability has the distinct `onramp_notify_` prefix and only its digest is stored
in `NotificationContactToken`. By default it has no expiry:
`AUTH['notification_contact_token_days']` is `None`, the stored `expires_at` is
NULL, and `notification_token_expires_at` is explicitly `null` in the response.
It remains valid until revoked. Applications may opt into a fixed lifetime by
setting a positive day count; the response then includes an ISO expiry timestamp
and reuse never extends it. Clients should let the server decide validity,
including for previously issued tokens whose lifetime has since been migrated.
Store it privately on the device and never include it in logs, email, URLs, or
account authorization. It does not create an account or session.

For later subscription requests, keep the email in the JSON body and provide the
token through `X-OnRamp-Notification-Token`. It covers resources and providers
within the original resource type, normalized email, and runtime environment.
A valid token returns a verified `200` response without another email code;
an invalid, expired, revoked, or mismatched token returns `401` with code
`notification_token_invalid` before subscription persistence. Clear that token
and let the user request a new email code. A signed-in account takes precedence.
`POST /api/notifications/contact/revoke` with the same header idempotently forgets
that device capability in the current environment and returns `{"revoked": true}`.
It leaves existing notification consent in place; cancelling a subscription is
a separate action. A later explicit request with valid remembered proof can
restore a cancelled subscription's consent. Revocation is serialized with
subscription persistence so an already-authorized request can finish before
revocation, while requests authorized afterwards fail.

Applications that must react immediately after proof can configure
`AUTH['notification_subscription_ready_hook'] = 'module.callable'`. The async or
sync callable receives `subscription`, `app_dir`, and the same bounded
`request_context` as the validator. It runs after persistence for verified and
authenticated requests. Errors propagate, and the verification code remains
usable so the client can retry; therefore the hook must itself be idempotent.

Account and notification request and verification routes have configurable
client-address limits (`auth_ip_hourly_limit` and
`notification_ip_hourly_limit`). Atomic database-backed buckets share counts
across workers and restarts, scoped by environment and endpoint. Only an HMAC
client identifier, counter and expiry are stored, not a raw IP address. Keep
production edge protection as well, especially against distributed attacks.
Routes use the ASGI client address; configure `ONRAMP_FORWARDED_ALLOW_IPS` only
for explicitly trusted Uvicorn ingress proxies. Application routes never parse
untrusted forwarded headers themselves. Until proxy trust is configured,
clients behind the same ingress conservatively share a limit.

OnRamp redacts query strings from Uvicorn's standard access log, so signed
unsubscribe URLs do not disclose their capabilities there. Request routing and
query parsing are unchanged. Keep equivalent redaction at hosting proxies and
in any custom request logging; do not log email codes, headers, or request bodies.

Inspect, maintain, preview, and send notifications from a project root:

```bash
onramp notifications report --resource-type model
onramp notifications cleanup --unverified-days 30
onramp notifications dispatch model-release-42 \
  --resource-type model --subject "Your model is ready" \
  --text-file email.txt
onramp notifications dispatch model-release-42 \
  --resource-type model --subject "Your model is ready" \
  --text-file email.txt --unnotified-only --send
onramp notifications anonymize person@example.com
```

Dispatch previews by default; `--send` is required to contact recipients.
Event keys identify application-global logical events. The same recipient and
event are delivered only once per environment even when several source
resources point to it, and retries reuse the provider idempotency key. Set
`ONRAMP_PUBLIC_URL` (for example, `https://api.example.com`) in staging and
production so messages can include the confirmation-based unsubscribe link.
Hosted action URLs must use HTTPS and cannot contain credentials, a query, or a
fragment; plain HTTP is accepted only for localhost in development or tests.
Those signed links intentionally do not expire and remain replay-idempotent;
rotating `ONRAMP_AUTH_SECRET` invalidates old links. Unsubscribing withdraws
active demand, and resubscription requires an authenticated account, valid
remembered notification proof, or fresh email verification.

Use `--unnotified-only` for one-time availability requests that should not
receive later versions after their first successful notification. Subscription
identity and delivery idempotency both include the runtime environment, so an
accidentally shared database cannot merge staging and production records.

Cleanup removes expired email challenges and remembered notification tokens,
abandoned unverified requests, and
database-backed challenge counters inactive for 24 hours; verified history
remains. Challenge counters make resend and incorrect-code limits atomic across
workers. Account deletion and the anonymize command revoke all matching
remembered capabilities; anonymization removes contact data, delivery hashes,
and challenge counters while retaining anonymous aggregate history. After upgrading a
project when framework-owned account or notification models have changed, run
`onramp migrate framework_notifications` in development and commit the
generated migration before deployment.
If an existing SQLite database changes a table-level `unique_together`, inspect
the generated migration before applying it: SQLite represents that constraint
with an implicit auto-index that cannot be removed with `DROP INDEX`. Create the
new constraint in the original unapplied migration for a fresh project, or use
a reviewed table-rebuild migration for a database that already contains data.

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

When accounts are enabled, a new Render Blueprint generates independent auth
and identity secrets, derives `ONRAMP_PUBLIC_URL` from the service's external
URL, and prompts for `RESEND_API_KEY` and `ONRAMP_EMAIL_FROM`. Projects with an
`AUTH.email_sender` hook own their provider configuration instead. Deployment
preflight rejects missing or insecure AUTH configuration and wildcard hosted
CORS origins; configure exact browser origins for staging and production.

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
environment. `onramp secret` can broker a named value from the local OS
credential store to a configured Render backend without placing it in
`onramp.toml`. A combined container can be represented as one target whose
`components` are `["backend", "web"]`.

Every host starts the production process with:

```bash
onramp start
```

This command listens on `PORT` (or `ONRAMP_PORT`), honors `ONRAMP_HOST`,
supports `ONRAMP_WORKERS`, and hands platform termination signals directly to
Uvicorn for graceful shutdown. Secrets belong in the provider secret manager
or the project-scoped OS credential store managed by `onramp secret`, never in
`app/settings.py` or `onramp.toml`. An ignored local `.env` loaded by your shell
or container tool remains supported for workflows that need it.

Run a native app from the project directory:

```
onramp ios
onramp android
onramp mobile
onramp ios --production
```

Pass `--environment development|staging|production` to select the matching
runtime/API profile from `build/app.json`. Development provides emulator-safe
loopback defaults; staging conventionally uses `.beta` native identifiers.
`onramp ios --production` builds and launches the iOS **Release** configuration
on a simulator using the production profile. It bundles JavaScript into the
app and does not start Metro or the local Python backend. This is a local
production-behavior check, not an App Store archive, signing, TestFlight
upload, or public release. `onramp ios --environment production` uses the same
Release behavior; development-only port, Metro, watcher, and rebuild options
cannot be combined with it. On Xcode Device Hub, leave this command running
to keep the Mac and selected simulator clipboards synchronized. Ctrl+C stops
the private sync but leaves the app installed and running. Clipboard contents
are not read or logged by OnRamp.

After the first successful native build, OnRamp reopens the installed app
without recompiling when its native inputs are unchanged. JavaScript and
TypeScript are still served fresh by Metro. Use `onramp ios --rebuild`,
`onramp android --rebuild`, or `onramp mobile --rebuild` to force native
compilation and installation.

Use `onramp ios --force`, `onramp android --force`, or `onramp mobile --force`
to use the next available backend port when the requested port is occupied and
automatically accept compatible emulator updates for that run. Without
`--force`, OnRamp still asks before switching backend ports. OnRamp still checks
for the latest emulator version and downloads updates, which can be several GB;
the flag does not skip update checks or force an app rebuild. It covers iOS
runtime version/build updates, Android Emulator updates, and newer Android
system-image versions/revisions (creating a replacement AVD when required).
First-time installs, architecture repairs, and display-only device replacements
still require confirmation. Xcode-license acceptance, Xcode first-launch setup,
and Rosetta installation always require their own explicit confirmation;
`--force` never accepts software licenses. `onramp mobile --force` also
preapproves deletion of
verified obsolete emulator files, including eligible older simulator devices and
their saved apps, data, and snapshots. This is permanent, not a move to Trash.
The separate `ios --force` and `android --force` commands still ask before cleanup.
Compatibility checks and the cooldown after a rejected iOS download remain in
effect. Without `--force`, update prompts are unchanged.
When OnRamp selects or receives a non-default backend port, it also updates the
generated local native runtime URLs for that launch; remote profile URLs and
the app-owned `build/app.json` remain unchanged. Framework-owned entrypoints
bootstrap that generated profile before application components render, so
customized app roots still receive the selected backend port.

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

On macOS, `onramp ios` delegates the frontend launch to `onramp-js`. It checks
Xcode licensing and first-launch readiness before native generation. If setup
is incomplete, OnRamp first offers Apple's interactive privileged license
review and then separately offers the privileged first-launch component setup.
It rechecks both steps before continuing, uses the globally selected Xcode in
normal `xcode-select` configurations, and never treats
`--force` as license consent. An explicit `DEVELOPER_DIR` remains supported for
read-only checks, but OnRamp will not pass that user-selected path through
`sudo`; it reports the exact manual command instead. `onramp doctor ios`
reports the exact required command without changing the system. The run
then adds the iOS project if it is missing, checks CocoaPods, installs
Pods, and checks Apple's preferred compatible Simulator runtime build on every
launch. After reusing current Pods or completing `pod install`, OnRamp raises
any explicit generated Pod deployment targets below React Native's supported
iOS minimum. This includes resource-bundle targets and CocoaPods multi-project
layouts, without changing the app-owned Podfile or lowering newer targets.
OnRamp asks before downloading a missing or newer runtime through
Xcode, requests the exact build for the host architecture, and retries Xcode's
latest compatible runtime when necessary. A failed optional upgrade continues
with an installed usable runtime. When Xcode rejects both download forms,
OnRamp suppresses that exact failed build combination for 24 hours while
continuing to check changed Xcode or runtime metadata immediately. If Xcode
itself is absent, OnRamp can open
its Mac App Store page after permission, but Apple requires the user to
complete the Xcode installation.
Before booting or showing the selected iOS device, OnRamp configures and
verifies the host-keyboard preference for either legacy Simulator or Xcode
Device Hub. If a Device Hub default changes while the selected simulator is
active, OnRamp restarts only that simulator without wiping its apps or data so
the next connection can adopt the setting. If an existing connection cannot be
proven to have adopted it, or macOS denies the narrow preference update,
launch continues without claiming keyboard forwarding is active and prints the
exact per-device menu to use.

After a replacement iOS runtime is verified, OnRamp offers to remove older
idle runtimes, listing versions and approximate sizes. This requires separate
confirmation because runtimes are shared across projects and Mac users, except
with `mobile --force`. Ordinary prompted cleanup preserves simulator devices
and app data; older devices need their runtime downloaded again before use.
`mobile --force` also deletes shutdown devices belonging to eligible older
runtimes and unavailable shutdown devices whose older runtime is absent.
Current/newer versions, same-version builds, and
active or uncertain device states are retained, with a fresh check before
each removal. Cleanup requires a verified usable replacement; a failed optional
update can still use an already installed replacement for safe cleanup.
Xcode may finish deletion asynchronously. OnRamp reports pending removal rather
than claiming space has already been reclaimed, and warns if Apple retains an
associated downloaded runtime asset. It never bypasses macOS protections to
delete those OS-managed assets.

`onramp android` delegates the frontend launch to `onramp-js`. It checks
Google's stable package list on every launch and asks before installing or
upgrading the Android Emulator, its stable system image, or a reusable virtual
device. It can bootstrap verified current Android command-line tools when the
installed `sdkmanager` is missing or obsolete. On Apple silicon, it validates
Google's actual Android CLI executable. If an installed Intel-only CLI cannot
run because Rosetta is absent, OnRamp explains the dependency and explicitly
offers Apple's Rosetta installer, including its license acceptance and possible
administrator-password prompt. It rechecks Rosetta and the exact Android tools
after installation. Declining keeps a complete existing SDK usable while
skipping package-update checks; missing components instead produce an actionable
error. `--force` never installs Rosetta or accepts its license. When Google's
installer prints
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
from the installed system image, and prefers the sharper matching AVD. Once
a replacement is verified, OnRamp offers separate cleanup of eligible older
idle OnRamp devices, naming them and asking before permanently deleting their
apps, data, and snapshots. User-created devices are retained. Older system
images require their own cleanup confirmation (preapproved by `mobile --force`)
and are retained whenever an AVD
still references them or the inventory is uncertain. Strictly older OnRamp
command-line-tool copies are removed automatically only after their replacement
is validated; unrelated and same-version tool copies remain. App installation
explicitly targets the selected emulator even if another device remains online. Provider URL or
checksum mismatches are failures even when the provider exits with status
zero. Emulator processes that exit during startup report their own diagnostics
immediately instead of appearing to hang until the boot timeout. OnRamp selects
JDK 17, enables macOS clipboard sharing, and enables host-keyboard input on
AVDs in the reserved `OnRamp_API_*` namespace whose metadata matches OnRamp's
canonical structure. An older matching device may cold-start once when that
keyboard setting is repaired; installed apps and device data are preserved.
AVDs outside the reserved namespace and ambiguous configurations are left
unchanged with manual keyboard guidance. OnRamp cold-starts the selected AVD
without the boot animation, targets only its active CPU architecture during
native builds, and wakes it automatically. These settings apply only to the
frontend process, so no shell profile editing is required.

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

## Mobile development storage

OnRamp keeps the installed emulator, current system images, virtual-device app
data, active project build output, Pods, and dependency download caches. It does
not run a blanket `clean` after each build: those caches make repeat launches
fast and avoid downloading the toolchain again.

Before native CLI launches, bounded maintenance runs at most once per day:

- Remove verified Xcode build output for deleted **OnRamp temporary projects**
  after seven days without modifications, only when no native build is running.
  Existing projects and shared Xcode caches are kept.
- Remove marked, disposable OnRamp native-scaffold/Android-tool temporary
  directories after 24 hours only when their creating process is definitely
  gone. Successful operations still clean up immediately. Legacy unmarked
  directories, ambiguous state, symlinks, and active work are kept.

Inspect this storage without running emulators or changing project files:

```bash
onramp storage --check
onramp storage --clean
```

The first command is read-only (also the default for `onramp storage`). The
second removes only eligible disposable output and reports its allocated size.
To explicitly include old Xcode build output for other deleted projects, use
`onramp storage --check --include-other-projects`, review it, then use `--clean`
instead of `--check`. Existing workspaces, recent output, archives, credentials,
source, and project backups remain excluded. Deleted build output is not kept
in Trash; it can be regenerated by rebuilding that project, if restored.

On normal mobile preflight, older runtimes, OnRamp devices, and unreferenced
Android images are also considered when the newest replacement is already
installed. Their removal asks separately unless `mobile --force` is used: an
older device can contain valuable app data, and a shared runtime may be needed
by another project. `mobile --force` opts into deleting eligible obsolete files
and device data, while preserving active/current/newer environments, custom
Android devices, referenced images, and uncertain inventories. Known stale
Android locks with a definitely dead owner no longer prevent eligible cleanup;
live or ambiguous locks still do. Never equate an old AVD with an unused image:
multiple devices can share one installed system image. This does not purge
dependencies, editable project files, or unrelated caches.
Automatic image cleanup includes older stable Google API images across the
standard and 16 KB page-size variants on the same architecture. Preview images,
unrelated variants, and same-API extension images remain conservative exclusions;
this is not a general-purpose disk wipe or a guarantee of a minimum-size SDK.

Housekeeping is best-effort, skips uncertain or busy state, and never blocks a
native launch. Xcode-output cleanup applies on macOS; owned temporary cleanup
also applies on Windows and Linux. It does not change Gradle/npm/CocoaPods
cache policies. For provider-managed cache behavior, see
[Gradle's cache documentation](https://docs.gradle.org/current/userguide/directory_layout.html).

## Upgrade an existing project

OnRamp records the project schema, Python version, frontend version, React
Native version, and framework-managed file bases in `.onramp/project.toml`.
Inspect an upgrade before changing anything:

```bash
uv run onramp upgrade --check
```

The check prints the complete non-mutating upgrade plan and ends with a clear
verdict: it says the project is already up to date when both project and
frontend files and metadata are current. If changes are pending, it explains
whether the upgrade should be successful or which conflicts block it.

Apply the latest release, or select one explicitly:

```bash
uv run onramp upgrade
uv run onramp upgrade --to 0.5.57
```

The upgrader downloads a newer OnRamp release into a temporary environment
when necessary, runs each project-schema migration in order, updates Python
and npm metadata structurally, and saves changed files under
`.onramp/backups/`. Unchanged framework files update automatically. A managed
file edited by the application developer is never overwritten; the upgrade
stops and reports the conflict instead.

That temporary release upgrades the project; it does not imply that a separate
global executable was replaced. `uv run onramp` follows the project's updated
pin. If you also keep the isolated user-level command, update it separately
with `uv tool upgrade onramp`.

Project schema 5 separates project-owned root `AGENTS.md` from managed
`.onramp/framework-guidance.md`. When upgrading a schema 0–4 project, including
a legacy project without `.onramp/project.toml`, OnRamp preserves the existing
root instructions and adds one prefixed instruction to read the framework
guidance. It does not infer which paragraphs are custom, merge them into the
new guidance, or remove old framework paragraphs. Review any retained legacy
paragraphs manually after the upgrade, keeping project-specific constraints.

Later upgrades of schema 5 or newer projects leave an existing root `AGENTS.md`
unchanged. The framework guidance receives normal hash-based conflict checks
and backups: manual edits there remain protected and can block an upgrade.
Put custom instructions in root `AGENTS.md`, and commit it together with
`.onramp/framework-guidance.md` and the updated `.onramp/project.toml`.
`onramp upgrade --check` previews this migration without changing either
instruction file or the manifest.

Native projects remain lazy and are not rebuilt merely to upgrade project
metadata. Platform route registries are
generated separately for iOS, Android, and web so simultaneous mobile runs do
not overwrite shared route state. Route discovery and matching stay identical
across targets: web retains route-level dynamic imports, while native route
modules are included in Metro's initial graph to avoid development-bundle Fast
Refresh loops.

Generated projects depend on a compatible release line such as
`onramp~=0.5.57`. Project schema versions are tracked separately from package
versions; `onramp upgrade` applies any required schema migrations, including
those introduced by patch releases.


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
