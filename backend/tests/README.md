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

Sending and node diagnostics return an initial `pending` task snapshot with
`success: null`. The existing status API exposes subsequent execution results.
GUI completion does not establish platform acceptance or recipient delivery.
Drafts remain intact. Chat-level persistent task tracking and result reconciliation
belong to the later sending stage.

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
