# Manual Application Updates

Settings provides **Check for updates**, **Download**, and **Install and restart**.
There are no scheduled GitHub checks. Opening Settings reads only local updater
state. The UI polls local status while a user-requested check or download runs;
after installation handoff it offers a manual reconnect button.

## Configuration

Set these in the installation's `.env` or process environment, then restart:

```dotenv
MAA_UPDATE_REPOSITORY=EgooAI/MaaAlibabaSupplier
MAA_UPDATE_BRANCH=main
MAA_UPDATE_WORKFLOW=install.yml
MAA_UPDATE_ARTIFACT=MaaAlibabaSupplier-Setup
# MAA_UPDATE_GITHUB_TOKEN=
```

Choose the branch explicitly if it is not `main`. The token, when required, should
be a fine-grained PAT restricted to the configured repository with **Actions:
Read** permission. It stays on the backend, is not returned to the browser, and
is not forwarded to artifact-storage redirect hosts. Public artifact downloads
can also require authentication; configure the token if access is rejected.

Only successful, completed **push** runs from the configured repository, branch,
and workflow are eligible. Pull requests, fork builds, and workflow-dispatch runs
are not selected in this version. The newest eligible run is selected by run
number and attempt. A newer rerun can be installed even if its commit SHA is
unchanged. Do not change workflows within one installation expecting run numbers
to remain comparable: GitHub run numbers are scoped to a workflow.

A successful workflow without the expected unexpired artifact is an error,
not permission to stop the application. The existing workflow skips installer
packaging when CI's `DOTENV` secret is missing. Artifacts currently expire after
seven days. Each artifact must contain exactly one installer named
`MaaAlibabaSupplier-v<version>-Setup.exe`, with no additional JSON or other files.
The installed payload must still include `build-info.json` for verification after
installation; an old installer lacking that identity cannot complete this check.

## Supported Installation

Updating requires Windows, valid `build-info.json` in the installation root,
the bundled interpreter under `backend/python`, and startup through
`python -m backend.app.main`. Source checkouts and direct ASGI launches cannot
install updates. The initial version containing this updater must be installed
normally before in-app updates become available.

The runtime locates `pwsh` first, then `powershell`, then the standard Windows
PowerShell executable under `%SystemRoot%`. The selected runtime must be outside
the application installation. The helper supports PowerShell 7 and Windows
PowerShell 5.1 and does not depend on the bundled Python being replaced.

Before launching application children, the main process creates and joins a
Windows Job Object. Its MaaFW, agent, Yak, and later descendants inherit
containment. The job allows breakaway children, and each confirmed installation
starts one single-use helper outside containment; the helper never outlives its
attempt and is never a member of the job it terminates. No helper runs between
updates, and the updater never checks GitHub on a timer. If Windows containment
or the external shell is unavailable, updating fails closed.

Use the same logged-in Windows desktop user as the application. This updater
does not add a Windows service, request elevation, or bypass GUI confirmation.

## API and Lifecycle

All endpoints require the existing Bearer authentication and use `{code,msg,data}`
envelopes. No seller epoch or idle-task gate is required.

| Endpoint | Request | Behavior |
| --- | --- | --- |
| `GET /api/app/update` | None | Local build, source, progress, candidate and previous result |
| `POST /api/app/update/check` | `{}` | Starts one asynchronous GitHub check |
| `POST /api/app/update/download` | `{"candidate_id":"..."}` | Starts download and local verification |
| `POST /api/app/update/install` | `{"candidate_id":"...","confirm":true}` | Starts the single-use helper for an already verified candidate |

Candidates have opaque server-generated IDs. The browser cannot submit an
installer URL, executable path, process ID, or command. Duplicate active
operations receive 409. The phases are `idle`, `checking`, `available`,
`downloading`, `ready`, `installing`, and `error`.

Download and verification run while the application remains usable. The updater
checks GitHub's ZIP digest before extracting a bounded, flat, single-file archive.
It rejects unsafe entries, empty installers, unexpected filenames, and extra files.
The updater computes the extracted installer's SHA-256 locally. No helper is
started and no file lock is held between download and install; a 100% download is
a ready candidate.

Confirmation starts one single-use helper for that attempt and blocks new API
writes. The helper validates the application identity, the installer location and
the commit files, then acknowledges; the outer ASGI layer commits termination
only after the successful acceptance response has finished sending. A response
failure, cancelled request, helper failure, or missing commit releases the
handoff without requiring a status-page visit. A dead helper fails only its own
attempt: the next confirmation starts a new one without downloading again. This
cannot guarantee the browser received the response over the network; an ambiguous
network failure is shown as uncertain rather than automatically retried.

The helper verifies the installer hash under a retained read-only file lock,
then terminates the owned Windows Job immediately and waits for the application
process itself to exit. It **does not wait for business tasks to finish**. If the
application does not stop, the helper records an error and does not run the
installer. No global process-name kill is used, and the separately running
Alibaba Supplier client is not targeted.

The helper runs the installer with:

```text
/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /NORESTARTAPPLICATIONS
/NOCLOSEAPPLICATIONS /RESTARTEXITCODE=3010 /DIR="<existing install>"
/LOG="<staged installer.log>"
```

Only exit code 0 permits restarting. Code 3010 records that Windows needs a
restart and does not restart the application or the machine. Other failures
record an error. A timed-out installer is not assumed to have completed; inspect
its process and log before retrying.

After installation, the helper verifies the installed `build-info.json` against
the expected identity, starts `backend/python/pythonw.exe -m backend.app.main`
with the installation root as working directory, and checks that it remains
running for ten seconds. This is **process and build verification**, not API or
GUI readiness. No automatic message replay or restoration of GUI authorization
is performed.

## Artifact Verification

No separate update manifest is generated, uploaded, or required. CI writes
`build-info.json` inside the installation payload before compiling Inno Setup.
The `Compile installer` step checks in Bash that the expected installer is a
regular file and is nonempty after ISCC succeeds. No separate Python validation
script or CI validation step is used. This is a basic output check, not a checksum
or executable-format validation. The workflow uploads `dist/*.exe` inside
`MaaAlibabaSupplier-Setup`; the updater still requires exactly one installer.

Expected repository, commit SHA, run ID, run number, and attempt come directly
from the selected GitHub Actions run. The application ID and metadata schema are
fixed by the updater protocol. After download, the filename supplies the display
version; it does not independently prove the contents of the installer.

GitHub's artifact digest verifies the ZIP before extraction. The locally computed
installer digest is passed through internal helper IPC, not published as a second
artifact file. It detects changes after extraction and before the helper locks
the executable; it is not an independent publisher-provided checksum.

After installation, `build-info.json` must match the expected application and
GitHub build identity before restarting. Its version must match the filename
version, ignoring at most one lowercase leading `v` on each side. Thus the actual
installed identity is checked after installer execution, not before it.

This version trusts the configured repository/workflow and GitHub metadata
received over HTTPS. Checksums alone do not establish publisher identity, and
Authenticode verification is not implemented. Restrict write access to the update
source accordingly.

## Preservation and Failure Recovery

Staging and results live outside the installation:

```text
%LOCALAPPDATA%/MaaAlibabaSupplierUpdater/<installation-path-hash>/
    last-result.json
    <download-stage>/artifact.zip
    <download-stage>/<installer>.exe
    <download-stage>/installer.log
    <broker-stage>/update-helper.ps1
    <broker-stage>/request.json, accepted.json, go.json, cancel.json
```

The backend exposes the last terminal result when it next runs; an interrupted
`installing` record is ignored rather than shown as active. Downloads and logs are
retained for diagnosis; this version does not automatically purge staging data.

Inno Setup performs an in-place upgrade. It preserves an existing `.env` and
excludes runtime data, debug files, and MaaFW config/data directories from the
payload copy. Its post-install launch is skipped during silent installation;
only the helper restarts the application. **Never uninstall as an update step**:
the existing uninstaller intentionally removes runtime data.

This version does not manually delete obsolete source files before installation,
because doing so bypasses Inno's file-replacement recovery. Deleted upstream
files can remain locally; releases that require their removal need a specific,
tested migration. There is no automatic full application/database rollback.
Keep your normal backups, and inspect the staged installer log if an upgrade
fails after the application stops.

Immediate termination can interrupt a send after it reached the client but
before the result was recorded. Verify interrupted tasks manually and never
automatically resend them. After restart, reconnect and confirm the GUI account
as required by the existing business safeguards.
