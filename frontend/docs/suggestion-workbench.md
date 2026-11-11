# Inline suggestion workbench

The chat composer selects the system suggestion Agent or an enabled ordinary
Agent. AI suggestions open an inline panel: beside the conversation on wide
screens and above the composer on narrower screens. The first opening generates
when there is no previous or pending run. Reopening preserves existing results.

The panel supports editing prompts without saving, explicit retesting, saving,
loading saved/historical configurations, adding the reply tool and creating an
ordinary Agent from the current default suggestion preset. Prompt drafts and
the selected Agent are stored per seller/data-directory workspace. Drafts remain
available after collapsing or reloading the panel.

Each run pins the prompt, model level, tools and conversation projection at
submission. Retests reuse that conversation snapshot; new messages expose an
explicit update-context action. Test outputs are neither conversation history
nor a blacklist for the next test. Internal model/tool execution is simulated,
while the run and its result are durably stored in the host CRM.

## API

All operations use the existing authentication and account epoch contract.

| Request | Purpose |
| --- | --- |
| `GET /api/agent/suggestion-agents` | Preset catalog and default Agent ID, without model credentials or session histories |
| `POST /api/conversations/{sid}/suggestion-runs` | Submit `{runId, agentId, draft, contextRunId}` and return a persisted receipt |
| `GET /api/conversations/{sid}/suggestion-runs` | Read the latest two runs |
| `GET /api/conversations/{sid}/suggestion-runs/{runId}` | Recover or poll an exact run |
| `POST /api/conversations/{sid}/suggestion-runs/{runId}/adopt` | Materialize the run's candidate batch idempotently and return candidate `{index}` with its intent ID |

`draft` is null or `{prompt, level, tools, requires_tool}`. `contextRunId` is null
for current committed messages, otherwise an earlier run in the same workspace
and conversation. Every request uses a durable client-generated `runId`; repeated
submissions with the same payload return the same record and never rerun the
model. Different payloads under one ID are rejected. The client retains the exact
pending payload across edits/reloads and observes before offering recovery.

Runs transition through `queued → running → completed | failed`. Pending work
from another process lifetime is presented as interrupted after restart, without
replay. Queued work checks the captured account before execution. Already running
work finishes in its original account context and writes only its captured store.
The runtime owns a two-worker pool and includes its work in shutdown/update
admission. Read requests do not hold the account lock during model work.

Results expose the run ID, original request, pinned configuration, context summary,
candidate replies, reasoning, tool calls and any error. Full frozen transcripts
remain in the host store. The existing synchronous `GET .../suggestions` remains
available to older clients; the new panel uses the recoverable API.

## Editing and adoption

Preset responses contain a configuration fingerprint. The panel passes it as
`expected_fingerprint` to the existing preset-save endpoint. A stale save returns
409 and preserves the local draft. Loading saved configuration rereads the server.
Saving during editing cannot overwrite newer local changes. Preset history uses
the existing append-only revision implementation.

Preview generation creates no reply-intent or outbox rows. Inserting materializes
the entire candidate batch once, with `batch_id` equal to the persisted run ID.
This links each intent back to its exact prompt/context snapshot and ensures
accepting one candidate still discards its siblings. Insertion never sends.
The existing outbox submission marks an unchanged inserted suggestion accepted.
Editing the reply detaches that association. Delayed insertion cannot overwrite a
newer reply draft or a different conversation/account.

Tests use temporary databases, a real tool pipeline with a static model client,
controlled futures and mocked frontend HTTP boundaries. They do not establish
production model quality, platform delivery or browser layout.
