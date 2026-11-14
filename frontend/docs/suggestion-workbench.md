# Inline suggestion workbench

The chat composer selects an enabled ordinary Agent with the saved `reply_customer`
tool. The former system suggestion Agent is now ordinary, retaining its ID,
configuration and history. New installations seed one reply Agent once; deleting
it does not recreate it on startup. AI suggestions open an inline panel: beside the conversation on wide
screens and above the composer on narrower screens. The first opening generates
when there is no previous or pending run. Reopening preserves existing results.

The panel supports editing prompts without saving, explicit retesting, saving,
loading saved/historical configurations and creating an ordinary Agent from the
built-in reply template, including when no eligible Agents remain. Prompt drafts and
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
| `GET /api/agent/suggestion-agents` | Eligible ordinary presets, first available default ID (empty if none), and built-in `template` |
| `POST /api/conversations/{sid}/suggestion-runs` | Submit `{runId, agentId, draft, contextRunId}` and return a persisted receipt |
| `GET /api/conversations/{sid}/suggestion-runs` | Read the latest two runs |
| `GET /api/conversations/{sid}/suggestion-runs/{runId}` | Recover or poll an exact run |
| `POST /api/conversations/{sid}/suggestion-runs/{runId}/adopt` | Submit `{}` to materialize the single reply idempotently and return `{content, zh, tone, intentId, status}` |

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

Each run exposes `legacy`, its ID, original request, pinned configuration, context
summary and any error. New runs have `legacy: false` and a nullable `result` shaped
as `{reply: {content, zh, tone}, reasoning, toolCalls}`. Each successful run contains
one reply and a brief explanation. The backend ends generation after a successful
reply tool call; the frontend displays the persisted result and tool trace. Full frozen transcripts
remain in the host store. The synchronous `GET .../suggestions` endpoint has been
removed. All suggestion generation uses the recoverable API, with an explicit
Agent ID. Tool-budget handling depends on the reply-suggestion run purpose rather
than a special Agent identity. Drafts cannot bypass saved Agent eligibility.

Older runs retain their original status and expose `legacy: true, result: null`.
The panel marks them read-only, hides their old candidate lists and invites the
user to regenerate. They are not polled or resubmitted, even if their original
status was queued or running. A retained pending receipt is cleared after lookup
identifies a legacy run. Regeneration can still reuse its context with
`contextRunId`. The previous run stays collapsed, including legacy runs.

## Editing and adoption

Preset responses contain a configuration fingerprint. The panel passes it as
`expected_fingerprint` to the existing preset-save endpoint. A stale save returns
409 and preserves the local draft. Loading saved configuration rereads the server.
Saving during editing cannot overwrite newer local changes. Preset history uses
the existing append-only revision implementation.

Preview generation creates no reply-intent or outbox rows. The main card shows one
reply and its Chinese reference, with one insert button. Brief reasoning, prompt
snapshots and tool calls remain in expandable details. Inserting materializes one reply
intent idempotently, linked to its exact run and prompt/context snapshot. The HTTP
adapter uses `intentId` as the insertion object's `id`; previews have no IDs,
titles or candidate indexes. Insertion never sends. Ordinary Agent history retains
its audit `intents` arrays.
The existing outbox submission marks an unchanged inserted suggestion accepted.
Editing the reply detaches that association. Delayed insertion cannot overwrite a
newer reply draft or a different conversation/account.

Frontend tests use controlled promises, mocked HTTP/account boundaries and a DOM
test environment to cover the single reply contract, legacy read-only runs,
pending recovery, account switches and delayed insertion. They do not establish
production model quality, platform delivery or browser layout.
