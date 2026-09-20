# AGENTS.md

## Resources

There're four ways you can get useful information about this project. Ordered by their efficiency:

1. Read `backend/app/README.md`, `backend/assets/README.md` for a primary brief view of the backend; the web UI lives in `frontend/` (Next.js, started with `pnpm dev`).
2. Use the skills at `.agents/skills/` directory for specific knowledge.
3. Explore `docs/` for detailed business knowledge.
4. Read the existing code.

## Rules

1. *Don't* do these dangerous actions *without* the user's command:
    - *Don't* commit or delete untracked files or irrelevant files *without* the user's command.
    - *Don't* modify or drop current data directory or database *without* the user's command.
    - *Don't* push to remote or modify remote branches *without* the user's command.
2. *Don't* do these useless works:
    - *Don't* explicitly run code formatting (as the user's editor can do them automatically).
    - *Don't* write or run tests that cannot detect a meaningful regression; follow the testing principles below.

## Development And Environment

### Portable Environment

Use *the repository's* Python, Node and pnpm under `.portable/` instead of silently substituting system installations.

Put `.portable/node` on PATH when invoking its pnpm so child processes also use the intended Node runtime.

Run backend checks from the repository root. Always pass the root `-c pytest.ini`, including SDK-only runs; automatic discovery can select the SDK's nested configuration and bypass host test isolation.

```powershell
& ".portable/python/python.exe" -X utf8 -m pytest -c pytest.ini backend/tests backend/app/crm_sdk/tests
& ".portable/python/python.exe" -X utf8 tools/verify_pytest_isolation.py
& ".portable/python/python.exe" -X utf8 tools/verify_crm_sdk_standalone.py
```

Run the relevant frontend checks from `frontend/` using the portable toolchain: `pnpm test`, `pnpm typecheck`, and `pnpm lint`. See `backend/tests/README.md` for test entry points and isolation details; select checks appropriate to the change rather than running every command for every edit.

### Deployment

Development uses two processes: the Next.js development server serves the UI, and the Python backend serves the API. `MAA_API_PORT` controls the backend; the development frontend defaults to port 3000. From the repository root, `pwsh -NoProfile -File tools/restart-dev-preview.ps1` restarts the preview and sets `BACKEND_ORIGIN` to the resolved backend address. When starting `pnpm dev` directly, supply the correct `BACKEND_ORIGIN`; Next.js otherwise defaults to port 8000. The installed build serves the static frontend and API from the same backend origin.

### Hot Diagnostics

If you want to diagnose a deployed server, you can run the following command to fetch online log data. The token env should be safely obtained from user's env file.

```powershell
& ".portable/python/python.exe" -X utf8 tools/pull_diagnostics.py diagnostics.zip --base-url https://support-host.example --token-env MAA_DIAGNOSTICS_TOKEN
```

## Development Details

### Module Ownership

- `backend/app/crm_sdk` is an *independent Git submodule*. Inspect its working tree and diff separately as the parent repository does not show its file changes as ordinary local files.
- *Keep the SDK generic.* Any information about the business details of this repository should not be exposed to that SDK.
- After changing the SDK, verify both affected host integration and standalone SDK operation with `tools/verify_crm_sdk_standalone.py`. The SDK must remain usable without importing the host application.

### Business Invariants

- The *selected seller* comes from settings page.
- Account-scoped asynchronous work must retain its original context. After an account change, stale results must not affect the new workspace; continuations must not acquire the new account's token to finish old work.
- Source decryption, task submission and committed CRM synchronization are different milestones. Do not report synchronization as complete or advance the readable revision before the CRM commit.
- A completed GUI send action, a locally matched message and platform delivery are different evidence. Do not equate GUI success with delivery or automatically resend after an uncertain outcome. Outbox recipient, content and screenshot confirmation remains part of the send flow.
- Translation cache queries distinguish `null` (not cached), an empty string (cached, no translation needed), and a nonempty translation. Truthiness checks must not turn the empty-string sentinel into a cache miss.

## Style Conventions

## Writing Tests

1. **Target a concrete failure.** Before writing a test, identify a plausible incorrect implementation that it would reject. Prefer observable outcomes and meaningful consumer contracts. Mock external boundaries, not the behavior being tested; avoid asserting only that a mock returns its configured value. Existence, defaults, internal calls and styling warrant assertions only when they protect an actual requirement. A simple assertion can be valuable: `archiveName`, for example, determines the TXT/ZIP download branch.
2. **Maximize independent fault detection per line of test code.** Check existing coverage first. Parameterize different inputs to the same behavior and share setup within a coherent workflow. Merge or remove thin-wrapper checks already covered by stronger persistence or interaction tests. Keep unrelated behaviors separate, avoid oversized tests and excessive test abstractions, and do not optimize for test count or coverage percentage alone. Before deleting a case, identify where its independent failure remains covered or explain why that constraint is not worth maintaining.
3. **Control inputs, completion and side effects.** Use controlled clocks, deterministic data and isolated resources where needed. Establish concurrency ordering with events, controlled futures and bounded waits; do not infer completion from fixed sleeps, machine speed or successful submission. Check the actual completed state and that failures, cancellation or stale results cannot incorrectly take effect. Use temporary databases and isolate network, GUI and real model calls. Ensure cleanup also completes on failure.

### Reporting Verification

Completion reports should state what changed, which checks actually ran and their results. Distinguish mocks and temporary databases from real-environment validation, identify material gaps such as browser layout or real-client recognition, and say whether restart, rebuild or refresh is needed for the change to take effect. Passing unit tests does not establish real GUI behavior, and passing local checks does not establish that CI passed.
