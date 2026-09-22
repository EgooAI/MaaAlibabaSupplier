# Diagnostic Downloads

`GET /api/app/diagnostics/download` returns `application/zip` with attachment name
`diagnostics-recent.zip`, `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
It uses the existing `Authorization: Bearer <session token>` authentication. No seller,
account epoch, additional role, secret, login mechanism or CORS policy is required.
There are no query parameters; supplying any returns 422. Missing, invalid or revoked
tokens return 401. A concurrent download returns 429 with `Retry-After: 2`; a build
failure returns 503 with a fixed, credential-free error envelope.

## Contents And Limits

The fixed allowlist covers API and agent JSON logs in `backend/data/logs`, Yak output
in `yak_mitm.log`, current CLI output in `backend/data/logs/maafw_cli.log`, legacy CLI
output in `backend/debug/maafw.log`, startup failure events in `startup_failure.log`, and both `maa*.log` and
`maafw*.log` native families in `backend/debug`, `backend/debug/debug`,
`backend/assets/debug`, `backend/deps/bin/debug` and `backend/deps/bin/debug/debug`.
Up to four recent owned `backend/data/logs/native/session-<hex>` sessions are
also eligible, restricted to those same native log families at the session root and
its `debug` directory. A known current active session takes priority, followed by recent
open/unclosed sessions, then closed sessions. A missing closure marker alone does not
prove the writer is still active; the manifest calls this `open_or_unclosed`. The
collector never rotates, prunes, waits for GUI work or asks native writers to stop.
Ownership markers are required, read with a fixed bound, and never exported.
Recognized timestamp, numbered and `.bak.log` rotations are included. The updater's per-install staging
directory contributes `last-result.json` and `installer.log` from up to four recent
UUID stage directories. Only those exact updater filenames are eligible; bootstrap,
request, ready and other IPC files are excluded. PowerShell helper output is kept
beside its stage for local inspection but is never exported. Development preview
backend/frontend `.log` and `.log.err` files under the repository's `debug` directory
are optional.

One newest available file from each key source precedes additional rotations; optional
development output comes last. Files use their latest bounded tail, with a partial
first line discarded. Each read freezes its end offset to the discovered file size:
later appends are excluded, incomplete final records are omitted, and observed changes
are reported. UTF-8 and BOM-marked UTF-16 LE/BE are supported, including aligned UTF-16
tails for Inno installer logs. "Recent" means latest files and tails, without a wall-clock age
cutoff. Directory enumeration examines at most 256 entries per directory and 4096
entries overall. Current fixed names are checked even if enumeration reaches its cap;
the newest rotation beyond a capped scan may be omitted.

- At most 24 ZIP entries, including generated `manifest.json` and `runtime.json`.
- At most 4 MiB read/exported per source file, 32 MiB total, and 34 MiB ZIP size.
- The total read budget is shared across selected sources; tails can be smaller than
  4 MiB so one large source cannot consume the entire bundle.
- Lines over 64 KiB and unrecognized records are omitted. Archive filenames are
  generated aliases, never original filenames or absolute paths.
- A cooperative 10-second collection deadline covers runtime metadata, discovery,
  file reads and record parsing. Checks occur between filesystem operations, 64 KiB
  reads and records. A blocked operating-system call cannot be interrupted; final ZIP
  closure and response transmission are outside this collection deadline.

The manifest records source availability, scan limits, included aliases, file/session
limit omissions and unavailable/unsafe candidate attempts with a fixed category
breakdown. Pre-schema records whose text cannot be exported are deduplicated by
level/logger/function/line so one repeated line cannot consume the bundle budget. Each file reports physical
bytes read (including BOM probes), snapshot size/change observations, source encoding,
tail truncation, discarded prefix bytes, rejected-record reasons, output truncation,
unprocessed read bytes and deadline interruption. `omitted_records_exact=false` means
unexamined records remain; the reported count covers only records actually rejected.
`omitted_deadline` files have no ZIP member. Discovery may be incomplete, so candidate
and omission counts never claim to describe files beyond the scan/deadline bounds.
Missing or rotated files do not fail the whole download.

The runtime summary includes generation time, Python/platform, and validated build
version, commit SHA and Actions numeric identity from fixed `build-info.json` (16 KiB
maximum). Other build fields are omitted. Passive observations report loaded modules,
source-check/verifier progress and queue lifecycle without creating workers. Process-lifetime
HTTP counters (`requests`, `reads`, `writes`, `errors`, `slow`) and their last observation
time come from the same cached snapshot. Account
context and free-form worker errors are excluded. Native retention includes bounded observed
session/log counts, active-session knowledge, available cached retention policy/state,
and explicit `inventory_complete=false`; it is not an exhaustive disk inventory.
The collector does not initialize services or inspect account state, settings,
databases, desktop windows, clipboard, screenshots or network services.

`diagnostics.runtime_observations()` is the integration hook for other components'
noninitializing, nonblocking cached getters. Accepted values are booleans named
`api_loaded`, `gui_loaded`, `sync_loaded`, `outbox_loaded`, `gui_initialized`,
`sync_initialized`, `outbox_initialized`, `shutdown_requested`, `native_switch_uncertain`,
`native_retention_budget_exceeded`; nonnegative numeric `native_max_bytes`,
`native_retained_bytes`, `native_max_age_days`; and a validated `native_active_session`
identifier. Unknown keys/values are discarded. Process-lifetime HTTP counters are read
from the already-loaded auth module only. The collector also reports a passive
updater segment when its singleton already exists: supported, phase, handoff, a
boolean error flag, candidate run ID and a validated last-result status/version.
Free-form updater errors are excluded. The collector connects the loaded runner's
cached log-policy status and the existing sync, verifier and queue observations. Native
policy failures, uncertainty, outstanding jobs and the last observation time are included.
No getter that initializes, scans a tree,
rotates logs, probes native state or waits for a service lock should be wired here.

Application records pass through `sanitize_diagnostic_record`; free-form messages are
then removed. Native/Yak parsers additionally retain numeric task/job/node/recognition/
action/process/thread IDs, source basename, function and line, fixed error categories,
and entry/node names from the application's known pipeline allowlist. Text parsing
consumes only contiguous recognized leading metadata and stops before unknown bodies;
it never searches OCR/text/payload bodies for apparent IDs. Structured native/Yak
records are re-sanitized and typed correlation fields are validated independently.
INFO-level Yak records carry no body and are dropped; warning and error records are
retained as `yak.record`.
Installer headers contribute timestamps and fixed event labels. Updater results
contribute a recognized status and a validated version. Unknown
bodies, exception messages, stack locals, paths, payloads and IPC are never copied raw.
Structured application telemetry and sanitized stack frame metadata remain useful for
correlation. This deliberately omits detail that may exist in the original logs.

All ancestors and opened files are checked for symlinks/reparse points, non-regular
files and hard links. Opened identities are checked to detect rotation; Windows final
handle paths and POSIX directory handles guard parent replacement races.

## Producer Policies

Application and agent writers use separate JSON-line files. Each rotates at 10 MiB
or one day, keeps at most five archives and 50 MiB of archived data, and prunes
archives older than seven days at startup/rotation. Active files add at most one
file per writer. Child Yak/CLI output has the same size/count limits; first-write
age sidecars preserve its daily rotation and seven-day expiry across restarts.
Child cleanup runs on accepted writes. Idle files are not deleted by a background
timer. Disk failures can prevent cleanup and cause diagnostic records to be lost.
Successful fast HTTP reads are counted instead of written: one `http.access` record
covers writes, 4xx/5xx responses and reads slower than one second, and only abnormal
response completion adds `http.response`. Third-party HTTP client loggers are limited
to warnings so outbound calls cannot duplicate structured records. Repeating
background failures are reported on their first occurrence, on a changed failure mode
and then every tenth attempt.

Maa native logging remains enabled in owned session directories, with a 256 MiB
retention budget, seven-day age policy and 128-session limit. Only safely closed
sessions are eligible for cleanup; OS leases protect live writers and permit
recovery after process exit. Rotation occurs at quiescent operation boundaries.
Active or unresolved native writers may exceed the budget; cached observations
report this limitation and log-policy failures. Outbox confirmation images and
business audit rows are outside this cleanup policy.

Request IDs, queue IDs, outbox IDs/attempts, sync IDs and native runtime/job/session
IDs link structured records. Nullable outbox origin fields survive restart;
queue/native mappings survive only while their logs are retained. Queued logging
can lose the last records on abrupt termination. Neither log presence nor absence
changes the persisted send-uncertainty rules.

## Response Lifecycle

ZIP creation runs in a worker before response headers are sent, using an automatically
closed temporary file outside the application's data directories. Only the completed,
bounded ZIP is loaded into the response. One process-wide admission slot remains held
through build and response cleanup. Cancellation cannot free it while a worker still
builds. There are no persistent export files on the server.

The response observes the existing Starlette `BaseHTTPMiddleware` completion signal
through `receive`: that middleware reports disconnect after the outer send finishes.
Send failure/cancellation cancels the inner task and releases admission in `finally`.
Tests cover this through the actual application middleware stack. Changes to that
middleware architecture must preserve this completion boundary.

## Developer Client

From the repository root, with the server already running:

```powershell
& ".portable/python/python.exe" -X utf8 tools/pull_diagnostics.py diagnostics.zip
& ".portable/python/python.exe" -X utf8 tools/pull_diagnostics.py diagnostics.zip --base-url https://support-host.example --token-env MAA_DIAGNOSTICS_TOKEN
```

The default prompts with `getpass` for an existing API Bearer token. `--token-env`
optionally names an environment variable containing that token. There is no credential
argument or query string. HTTP is accepted only for loopback origins; remote origins
require verified HTTPS. Redirects and environment-configured proxies are disabled.

The client writes a new `.part` file, enforces the download limit, validates ZIP entry
names, sizes, CRCs and generated schema versions, then publishes the completed filename
without overwriting an existing file. Failure removes only the partial file it created.
The destination filesystem must support hard links for atomic, no-overwrite publication.
Errors never print HTTP bodies, credentials or exception details. Downloads are explicit;
the tool performs no automatic upload or external reporting.

Restart the backend to register this endpoint and its collector. No frontend build is
needed for API or CLI use. Tests use synthetic files, temporary authentication stores
and mocked client transport; they do not validate production log contents or a live
network/desktop environment.
