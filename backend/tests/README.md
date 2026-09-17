# Backend Regression Tests

Run from the repository root on Windows, using the project's Python environment:

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
& ".portable/python/python.exe" -X utf8 tools/verify_pytest_isolation.py
& ".portable/python/python.exe" -X utf8 -m pytest -c pytest.ini -q backend/tests backend/app/crm_sdk/tests
& ".portable/python/python.exe" -X utf8 tools/validate_schema.py
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

## GUI Regression Coverage

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
establish platform acceptance or recipient delivery. Drafts remain intact.

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
- `test_gui_session.py` and `test_connection_api.py` exercise manual confirmation,
  same-window reconnect, stale dialogs/requests/queued tasks, mutation conflicts,
  nonblocking status during long GUI operations and explicit connection recovery.
- Frontend Vitest includes React lifecycle tests for account switching, failed
  polling, stale responses, synchronization completion and persistent draft fallback.

Manual confirmation only asserts the operator's selected seller/window pairing.
It does not establish the current recipient or platform acceptance. Browser
layout screenshots and actual client behavior remain separate validation work.

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

- `test_outbox_store.py` checks concurrent idempotency, immutable payloads,
  versioned state transitions, cancellation/claim races, restart recovery,
  conservative retry and unique local-message evidence claims.
- `test_outbox_service.py` checks screenshot confirmation, expiry/change handling,
  the durable send barrier, transient database failures after clicks, safe
  result-only retries and independent cleanup after shutdown failures.
- `test_gui_evidence.py` checks fresh screenshot jobs, full-frame lossless PNG
  encoding, stale-image rejection, and native send-only recognition using
  synthetic frames. Stopping and posting the send job share an admission boundary;
  tests pause before and after posting to verify that stop cannot admit a late send.
- `test_source_messages.py` / `test_send_verification.py` check reader pins,
  complete baselines, strict source ownership, exact text and sender matching,
  malformed extension data, duplicate candidates and ambiguous attempts.
- `test_outbox_api.py` checks scoped images, no-store headers, task versions,
  idempotent receipts and fast busy conflicts rather than delayed GUI admission.
- Frontend outbox tests require PNG load before confirmation and preserve one
  intent key across timeout/retry. They recover known tasks outside the recent
  100-record list and revoke blob URLs across task/account lifetimes.

The tests use synthetic frames and records. They do not validate recognition of
real contacts, actual Alibaba delivery, or browser layout screenshots. The GUI
flow deliberately requires the operator to inspect the recipient screenshot;
the final automated result is only a local matching-message observation.

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
  crossings, cross-tab revisions and retained details/outbox dialogs when a list
  response observes a newer inbox revision before the poll does.

The response contract and initialization limits are documented in
`backend/app/api/INBOX.md`. Inbox labels describe local workspace state, not
platform unread counts or verified delivery acknowledgements. Tests use temporary
archives, synthetic messages and mocked model calls, not real customer data.
