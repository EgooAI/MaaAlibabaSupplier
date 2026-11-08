# Application Updates

The backend checks GitHub every 5 minutes and downloads and verifies a newer build
on its own. A verified candidate installs automatically after 15 continuous minutes
without nonresident background work. Manual **Install and restart** uses the same
idle check without the 15-minute delay; busy applications reject the request.
Settings also provides **Check for updates**, which runs the same check and
download immediately. Opening Settings reads only local updater state. The UI
reads that state every 30 seconds, every 1.5 seconds while a check or download
runs or installation is in progress. It keeps reading through a restart and shows
the result after reconnecting; an ambiguous manual submission is never retried.

The scheduler lives in the backend process, so it runs without a browser tab. It
starts only in a supported installation, waits 60 seconds after startup for the
first check, and then repeats every 5 minutes. After a failed cycle the wait
doubles, up to 6 hours, and returns to 5 minutes after a success. A cycle never
starts while another check, download or installation is active. Once a candidate
is ready, scheduled remote checks pause so they cannot continually reset the idle
clock. Manual checks can still replace the candidate. Set `MAA_UPDATE_AUTO=0` to
disable both automatic checking and installation; manual operations still work.

Idle time uses a monotonic clock and starts no earlier than candidate readiness.
Workers, GUI/translation queues, CRM pool calls, experiments, card sweeps and
update checks reserve work until completion, including queued work and pool
callbacks. API writes also reserve admission through response completion. Even a
short operation between timer checks restarts the full 15-minute wait. Resident
listeners and log readers do not count. Resident IM/Outbox polling does not reset
the clock, but an executing poll still prevents installation at that instant.

## Configuration

Set these in the installation's `.env` or process environment, then restart:

```dotenv
MAA_UPDATE_REPOSITORY=EgooAI/MaaAlibabaSupplier
MAA_UPDATE_BRANCH=main
MAA_UPDATE_WORKFLOW=install.yml
MAA_UPDATE_ARTIFACT=MaaAlibabaSupplier-Setup
# MAA_UPDATE_AUTO=0
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
packaging when CI's `DOTENV` secret is missing. Each artifact must contain
exactly one installer named `MaaAlibabaSupplier-v<version>-Setup.exe`, with no
additional JSON or other files. The installed payload must still include
`build-info.json` for verification after installation; an old installer lacking
that identity cannot complete this check.

## Supported Installation

Updating requires Windows, valid `build-info.json` in the installation root,
the bundled interpreter under `backend/python`, and startup through
`python -m backend.app.main`. Source checkouts and direct ASGI launches cannot
install updates.

The runtime locates `pwsh` first, then `powershell`, then the standard Windows
PowerShell executable under `%SystemRoot%`. The selected runtime must be outside
the application installation. The helper supports PowerShell 7 and Windows
PowerShell 5.1 and does not depend on the bundled Python being replaced.
Availability is re-resolved for every installation attempt. A runtime that
vanished since startup (a Windows Store package auto update can replace the
resolved app-execution alias) fails only that attempt with a fixed error; the
next attempt re-resolves the runtime or falls back to Windows PowerShell 5.1
without a backend restart. Startup still validates the runtime once, so an
unavailable shell keeps updates disabled with a stated reason. On unattended
servers, prefer the MSI PowerShell distribution so the resolved executable
cannot be replaced silently.

Before launching application children, the main process creates and joins a
Windows Job Object. Its MaaFW, agent, Yak, and later descendants inherit
containment. The job allows breakaway children, and each confirmed installation
starts one single-use helper outside containment; the helper never outlives its
attempt and is never a member of the job it terminates. No helper runs between
updates; the 5-minute check is a backend thread and does not start a helper. If
Windows containment or the external shell is unavailable, updating fails closed.

Use the same logged-in Windows desktop user as the application. This updater
does not add a Windows service, request elevation, or bypass GUI confirmation.

## API and Lifecycle

All endpoints require the existing Bearer authentication and use `{code,msg,data}`
envelopes. No seller epoch is required. Both installation paths require idle work
admission and acquire the same process-wide installation mutex.

| Endpoint | Request | Behavior |
| --- | --- | --- |
| `GET /api/app/update` | None | Local build, source, `auto` flag, progress, candidate and previous result |
| `POST /api/app/update/check` | `{}` | Starts one asynchronous GitHub check, then downloads a newer build |
| `POST /api/app/update/install` | `{"candidate_id":"...","confirm":true}` | Starts the single-use helper for an already verified candidate |
| `GET /api/app/update/notice` | None | Latest unacknowledged successful installation, or null |
| `POST /api/app/update/notice/ack` | `{"installation_id":"..."}` | Acknowledges that installation for every device; returns any newer notice or null |

Candidates have opaque server-generated IDs. The browser cannot submit an
installer URL, executable path, process ID, or command. Duplicate active
operations receive 409. The phases are `idle`, `checking`, `downloading`,
`ready`, `installing`, and `error`.

Download and verification run while the application remains usable. The updater
checks GitHub's ZIP digest before extracting a bounded, flat, single-file archive.
It rejects unsafe entries, empty installers, unexpected filenames, and extra files.
The updater computes the extracted installer's SHA-256 locally. No helper is
started and no file lock is held between download and install; a 100% download is
a ready candidate.

A build whose installer is already downloading or ready is not downloaded again,
whether the check was scheduled or manual. A newer build replaces the ready one
only after it is verified; if that download fails, the earlier verified installer
stays installable. When the response reports the artifact size, the download
requires free disk space of twice that size plus 512 MiB, because the archive and
the extracted installer coexist until unpacking ends. Installation re-checks that the installer file
still exists and returns 409 when security software or a cleanup removed it.

Installation atomically checks idle work and blocks new submissions, triggers and
API writes before starting its single-use helper. Busy manual attempts return 409
and the UI explains the rejection in a normal dialog. Timer actions pause while
installation owns admission. The helper validates the application identity, the installer location and
the commit files, then acknowledges; the outer ASGI layer commits termination
only after the successful acceptance response has finished sending. An automatic
attempt commits directly after helper acceptance. A response
failure, cancelled request, helper failure, or missing commit releases the
handoff without requiring a status-page visit. A dead helper fails only its own
attempt: the next confirmation starts a new one without downloading again. Failed
or cancelled handoffs reopen task admission; automatic retries wait another full
15 minutes. This
cannot guarantee the browser received the response over the network; an ambiguous
network failure is shown as uncertain rather than automatically retried.

The helper verifies the installer hash under a retained read-only file lock,
then terminates the owned Windows Job immediately and waits for the application
process itself to exit. Work was already idle and admission closed before handoff;
the helper does not perform a second business-task drain. If the
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
`build-info.json` inside the installation payload before compiling Inno Setup,
and uploads `dist/*.exe` inside `MaaAlibabaSupplier-Setup`. CI's own check that
the installer is a nonempty regular file is an output check, not a checksum or
executable-format validation; the updater still requires exactly one installer.

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
    installation-notice.json
    acknowledged-installation.json
    <download-stage>/<installer>.exe
    <download-stage>/installer.log
    <broker-stage>/update-helper.ps1, bootstrap.json
    <broker-stage>/request.json, accepted.json, go.json, cancel.json
    <broker-stage>/helper.log
```

The backend exposes the last terminal result when it next runs; an interrupted
`installing` record is ignored rather than shown as active. Staging is bounded:
at most one stage holds a downloaded installer. Startup and the end of every
check or download delete installers and archives in every other stage, except
while an installation is in progress. A failed download deletes its own stage.
A ready candidate is not persisted, so a restart discards it and the next check
downloads it again. Logs remain, and each download prunes uuid stage directories
beyond the four most recent, not counting the one in use. An
attempt that could not prepare staging or start its helper deletes its own stage
directory; failures after the helper started keep their logs for inspection,
while the next startup or completed operation deletes the stray installer. The helper
replaces `last-result.json` atomically, clearing a read-only attribute first; a
destination that stays locked still loses the terminal record and leaves only
the `.tmp`. The backend records an `installed` result that does not describe the
running build as stale, so a lost write is visible in diagnostics.
Result, notification and acknowledgment files are never pruned.

After successful installation and restarted-process verification, the helper writes
`installation-notice.json` with a unique installation ID, version, installer start
time (`executed_at`) and result time (`completed_at`), both Unix seconds. Failures
do not replace the last success notice. Older result files without timestamps still
appear in Settings but do not fabricate an execution time or a completion notice.
The global authenticated UI shows the notice on every page and polls every five
seconds while visible. It has no automatic timeout or local dismiss button.
**知道了** persists the exact ID to `acknowledged-installation.json`; refreshing,
restarting or using another device does not restore it. A stale acknowledgment
cannot dismiss a later installation. Failed persistence leaves the notice visible.

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

Installation does not establish platform delivery for earlier sends. Verify
uncertain tasks manually and never automatically resend them. After restart,
reconnect and confirm the GUI account as required by the existing business safeguards.
