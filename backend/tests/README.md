# Backend Regression Tests

Run from the repository root on Windows, using the project's Python environment:

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
& ".portable/python/python.exe" -X utf8 tools/verify_pytest_isolation.py
& ".portable/python/python.exe" -X utf8 -m pytest -c pytest.ini -q backend/tests backend/app/crm_sdk/tests
& ".portable/python/python.exe" -X utf8 tools/validate_schema.py
```

`backend/tests/test_update_helper.ps1` additionally verifies the updater helper's
PowerShell functions (installer hash locking, installed-build identity and result
publication) without process or installer I/O. It is run manually, outside pytest,
because tests must not create real subprocesses:

```powershell
& "backend/tests/test_update_helper.ps1" -ScratchParent $env:TEMP
```

Always select the repository configuration explicitly, including SDK-only runs:

```powershell
& ".portable/python/python.exe" -X utf8 -m pytest -c pytest.ini backend/app/crm_sdk/tests
```

## Isolation

The root `pytest.ini` fixes configuration discovery and default test paths. Without
it, pytest can select the SDK's nested `pyproject.toml` even for a combined run and
skip `backend/conftest.py`. CI therefore always passes `-c pytest.ini`.

The root conftest rejects an unprotected session before collection. The application
test package also rejects imports without a process-local isolation marker, so nested
configuration, `--confcutdir` and `--noconftest` cannot silently run unisolated
application tests. The marker is installed only after the backend isolation patches succeed
and is removed on teardown; environment variables cannot enable it. Direct
`unittest` invocations of application tests without this setup are unsupported.

The generic CRM SDK contains no application-specific test guard. Its own tests
can run independently using explicit temporary databases; the host's conftest
adds isolation when the SDK is included in the combined application suite.
`tools/verify_crm_sdk_standalone.py` runs a source-only SDK copy in a temporary
directory without host conftest files and rejects imports of the host application.

`tools/verify_pytest_isolation.py` copies only test infrastructure into temporary
repositories and uses independent pytest subprocesses with synthetic modules and
stub application dependencies. It checks root discovery, collection guards and
fixture execution without importing or running the application's actual tests.

`backend/conftest.py` redirects the application root, configuration, databases,
cache and working directory into temporary directories before test collection.
Each test gets a separate data root. It disables dotenv loading and requires
mocks for desktop window discovery, process key retrieval, socket connection
helpers and subprocess creation. The fixtures drain task workers and close
database connections before removing temporary directories.

Do not start `backend.app.main` for these tests. Use the FastAPI application with
`TestClient`, temporary databases, synthetic IM records and controlled executors.
New external access paths must have explicit test replacements; the fixture is
not an operating-system sandbox.

## Background Lifecycle Coverage

`test_background.py` uses controlled events and timer wakeups to verify timer/trigger
coalescing, readiness deferral and recovery, cancellation, bounded joins including
Future callbacks, account-context isolation, and cleanup after partial startup.
`test_agent_lifecycle.py` mocks the native Agent server and verifies signal shutdown
and startup-failure cleanup. Sync, card-sweep, updater and experiment suites exercise
their domain-specific shutdown and persistence behavior through the same runtime.

`test_update_idle.py` advances a controlled clock and uses real managed workers,
queues and schedules with mocked installation I/O. It covers the 15-minute window,
short intervening work, pending tasks, callback completion, immediate manual idle
admission, the helper-start mutex and failure recovery. `test_update_notice.py`
uses temporary updater files and authenticated clients to verify cross-device and
restart-persistent acknowledgment, stale IDs and failed persistence. The helper's
PowerShell checks also verify timestamped notice publication without executing an
installer. Frontend UpdateCard/UpdateNotice tests cover restart reads, rejection
dialogs, durable-dismissal responses and stale polling results.

`test_background_status.py` and `test_status_snapshot.py` cover passive observations,
workspace-scoped queue/experiment/outbox records, persisted send uncertainty, and
partial read failures without hiding other sources. Runtime tests also cover bounded
history, queue/active counts and observation during blocked callbacks. Frontend
`StatusPage`, `StatusAndShutdown` and background-model tests cover business evidence,
normal stops, stale snapshots, account changes and visibility-aware polling. These
use isolated stores and mocked external services; they do not validate real browser
layout, client recognition or update installation.

## GUI Regression Coverage

`test_diagnostic_screenshots.py` exercises authenticated exports of saved native
error PNGs with synthetic images and temporary log directories. It covers pixel
fidelity, metadata removal, session correlation, selection/budget limits, partial
and changing files, path boundaries and downloader validation. It does not capture
the desktop or establish that a production native task saved its error image.

- `test_gui_tasks.py` checks acceptance snapshots, response preparation failures,
  queue capacity, failed navigation and diagnostic ordering relative to sending.
- `test_maafw_runner.py` checks initialization recovery, window ambiguity and
  rebinding, resource configuration and failures without automatic replay.
- Its native Maa tests use a custom controller with synthetic images built from
  repository button templates. They verify enabled/disabled/missing send buttons,
  action failure, repeated send/input/send operations and read-only diagnostics.
  They never discover or control a real desktop window. Native tests explicitly
  skip if the Maa library is unavailable; a skip is not GUI validation.

## Stage 0 Contract

Node diagnostics return an initial `pending` task snapshot with `success: null`.
The status API exposes their execution results. Stage 3 replaces the original
message-send response with a durable outbox record; GUI completion does not
establish platform acceptance or recipient delivery. Accepted receipts clear only
the original unchanged draft; newer edits and uncertain submissions retain it.

The status-page diagnostic entries map to independent recognition-only pipelines.
They share the GUI queue, perform no input or click, and do not override business
pipeline nodes. Manual node testing cannot interrupt a queued contact-navigation
and message-input operation.

## Remaining Evidence

The selected data-directory account and a matching window title do not prove the
active client account. Existing IM message rows also do not prove server acceptance:
the importer does not interpret a verified send-status field or sending ACK.
Before implementing those guards, establish the observable account/contact IDs
and compare source records for controlled successful, failed and retried sends.
Real-client DPI/input compatibility and browser screenshots are not covered by
this suite.

## Stage 1 Coverage

- `test_crm_message_migration.py` exercises backups, seller ownership, rollback,
  interrupted/repeated/concurrent migration, WAL contents and transitive references.
- `test_crm_account_scope.py` and `test_im_db_isolation.py` exercise colliding source
  message IDs, selected-self reads, fixed account/cache snapshots, old futures,
  callback lock ordering, failed submissions and key recovery.
- `test_gui_session.py` and `test_connection_api.py` exercise connected-context capture,
  same-window reconnect, stale requests/queued tasks, mutation conflicts,
  nonblocking status during long GUI operations and explicit connection recovery.
- Frontend Vitest includes React lifecycle tests for account switching, failed
  polling, stale responses, synchronization completion and persistent draft fallback.

Connection guards bind the selected seller, directory, epoch and window generation;
they do not identify the client's logged-in seller or prove platform acceptance.
Navigation, input and sending share one guarded operation without manual confirmation.
Browser layout screenshots and actual client behavior remain separate validation work.

## Isolation Incident And Guard

A combined test invocation selected the nested SDK configuration and bypassed the
initial isolation fixture. It wrote test state into the local application data.
The user authorized preserving a verified complete copy and resetting that data.
The root configuration and process-local collection guards above were added in
response. Passing individual isolated tests is not proof that another invocation
loads their fixtures; verify the entry path before changing test invocation rules.

## Stage 2 Coverage

- `test_crm_snapshot_sync.py` checks one-transaction rollback of data and revision,
  late messages, source updates, duplicate imports, unknown types and bounded SQL
  batches. A 1000-message fixture uses two message write batches and one commit;
  replaying the same payload performs no message writes.
- `test_im_sync_coordinator.py` drives controlled futures and clocks to verify
  latest-pending coalescing, retry backoff, pinned caches, target waiting, lifecycle
  shutdown and late commits to the same archive after an epoch change.
- Startup tests revalidate saved keys against synthetic source headers, restore
  synchronization without a browser, and reject stale results after settings or
  manual retry changes. Transient failures retry automatically; missing/invalid
  keys never fall back to process capture, legacy migration, GUI or model calls.
- `test_account_key_store.py` checks read-only saved-key access, malformed values
  and SQLite lock recovery. Manual validation failures without a scheduled worker
  must not advertise an automatic retry time.
- Source tests use encrypted synthetic databases and native SQLite WAL files,
  including RESTART reuse, uncommitted frames, corrupt current frames and transient
  file access failures. They never inspect the installed client database.
- API tests assert that 100 revision observations schedule zero imports, and that
  source changes cannot advance the public revision before the CRM transaction.
- Frontend lifecycle tests cover unchanged-revision recovery, delayed list/detail
  acknowledgements, manual profile refresh, translation preservation and retaining
  the workspace across temporary connection failures.

Source snapshots are still scanned in full when changed. This avoids assuming
that message time or source row IDs are reliable insertion/update cursors.

## Stage 3 Coverage

- `test_suggestion_runs.py` uses the real Agent pipeline with a static LLM client
  and temporary CRM stores to verify ordinary-Agent previews, immutable prompt
  and context snapshots, idempotent recovery, repeatable tests, partial tool-limit
  results, atomic candidate adoption, account isolation and stale preset saves.
  Frontend `Suggestions` and outbox interaction tests cover inline iteration,
  timeout/reload recovery, prompt persistence and protection of newer reply edits.

- `test_outbox_store.py` checks concurrent idempotency, immutable payloads,
  versioned state transitions, cancellation/claim races, restart recovery,
  conservative retry and unique local-message evidence claims.
- `test_outbox_service.py` checks single-pass execution, baseline failure before any
  GUI input, the durable send barrier, transient database failures after clicks, safe
  result-only retries and independent cleanup after shutdown failures.
- `test_gui_evidence.py` checks fresh screenshot jobs, full-frame lossless PNG
  encoding, stale-image rejection, and native send-only recognition using
  synthetic frames. Stopping and posting the send job share an admission boundary;
  tests pause before and after posting to verify that stop cannot admit a late send.
- `test_source_messages.py` / `test_send_verification.py` check reader pins,
  complete baselines, strict source ownership, boundary-whitespace-tolerant text
  and exact sender matching, malformed extension data, duplicate candidates and
  ambiguous attempts under the same text comparison. Late evidence can recover
  unknown tasks without another GUI input or send.
- `test_outbox_api.py` checks task versions, retired confirmation endpoints,
  idempotent receipts including accepted reply intents, and fast busy conflicts.
- Store tests recover old confirmation states without replay or artifact deletion.
- Frontend outbox tests cover inline task bubbles, exact-ID merging after CRM
  synchronization, draft revision protection and one key across timeout recovery.
  New drafts can repeat the same text. Older unknown tasks outside the recent
  100-record list continue polling for late evidence.

The tests use synthetic frames and records. They do not validate recognition of
real contacts, actual Alibaba delivery, or browser layout screenshots. The final
automated result is only a local matching-message observation.

## Stage 4 Coverage

- `test_inbox_store.py` checks one-time historical baselines, both upgrade
  initialization orders, retained CRM history missing from the current source,
  new-directory initialization, late-message unread counts and atomic rollback.
- Read tokens bind database, seller, directory, account epoch, conversation and
  sequence. Tests acknowledge a snapshot while newer messages arrive, reject
  changed/tampered scopes, and verify monotonic reads without clearing reply state.
- Reply-state tests distinguish unread, historical pending, human replies,
  automatic/system messages, ambiguous timestamps and configurable deadlines.
- `test_inbox_api.py` checks literal historical text searches, resolved country
  and tag filters, stable paging, stale page tokens and overview/list agreement.
  Directory-only messages are excluded from other directories' search, detail,
  AI inputs and ZIP exports. These reads do not mark messages read.
- Frontend tests cover explicit acknowledgement, filtered paging, deadline
  crossings, cross-tab revisions and retained details/outbox tasks when a list
  response observes a newer inbox revision before the poll does.

The response contract and initialization limits are documented in
`backend/app/api/INBOX.md`. Inbox labels describe local workspace state, not
platform unread counts or verified delivery acknowledgements. Tests use temporary
archives, synthetic messages and mocked model calls, not real customer data.

## Agent Coverage

- `test_agent_console.py` covers the console projection, preset CRUD, revision
  restore, and the runnable guards (disabled agent, unregistered tools,
  `requires_tool` without tools).
- `test_agent_playground.py` covers fork fidelity (the frozen prefix equals the
  live projection byte for byte), pinned revisions, account isolation, and the
  rule that a playground endpoint refuses ordinary test sessions.
- `test_agent_experiments.py` covers the experiment lifecycle: the three
  starting points, variant caps, asynchronous runs with per-variant status,
  cancellation, checkpoint-based retries (one buyer message per step), promote
  to the current preset, and invisibility across accounts.
- `test_reply_tool.py` covers the effect tool's validation (empty, duplicate,
  count, placeholders), the dispatcher's re-validation of directly built
  intents, and simulated runs producing no persisted rows.
- `test_intent_store.py` covers the legal state edges, batch discard on
  acceptance, and seller/data-directory isolation.
- `test_chat_history_content.py` covers schema compatibility: old sessions load
  with empty tool/intent lists, and new fields survive a save/load round trip.
- `test_system_agent_seeding.py` covers seed idempotency and the rule that a
  merchant-edited prompt is never overwritten.
- SDK tests (`backend/app/crm_sdk/tests/`) cover the pipeline's call protocol,
  cancellation boundaries, the experiment table and variant ownership. Run them
  through the standalone verifier before claiming host compatibility.

Frontend coverage lives in `frontend/test/features/`: the timeline renders one
card per call and opens failures by default; the experiment board aligns steps
across variants; the create modal maps editor drafts and pasted text into the
request shape. These run against a mock adapter, so they do not establish real
model behaviour or browser layout.

## Translation Coverage

- `test_translation_agent.py` covers the LLM input shape (full-history context
  with `text_hash=` markers for any speaker, stable blocks before the volatile
  target list), short-hash uniqueness with salted re-derivation, the three-value
  protocol (translation / NO_NEED empty sentinel / ABNORMAL uncached), cache and
  force semantics, and the output parser including fenced JSON and invalid
  payloads.
- `test_translation_api.py` covers the async job lifecycle: order-preserving
  dedupe, empty and all-cached submissions, chunking at 50 with partial
  failures, agent omissions reported as job failures, conversation context
  loading shared across chunks, epoch cancellation, account-lock-free workers,
  and the query contract distinguishing null (uncached) from the empty NO_NEED
  sentinel.
- `test_connection_api.py` verifies the epoch guard on submissions; `test_auth.py`
  uses the translation POST as a representative guarded route. The SDK's
  `test_translate_manager.py` covers the SQLite round-trip and idempotent
  upserts.
