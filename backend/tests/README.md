# Backend Regression Tests

Run from the repository root on Windows, using the project's Python environment:

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
& ".portable/python/python.exe" -X utf8 -m pytest -q -p no:cacheprovider backend/tests backend/app/crm_sdk/tests
& ".portable/python/python.exe" -X utf8 tools/validate_schema.py
```

For an SDK-only run, include `--confcutdir=backend` so its nested pytest configuration
does not exclude the application isolation fixtures:

```powershell
& ".portable/python/python.exe" -X utf8 -m pytest --confcutdir=backend backend/app/crm_sdk/tests
```

## Isolation

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
