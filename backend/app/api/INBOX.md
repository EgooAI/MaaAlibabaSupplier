# Stage 4 Inbox API

All responses retain the existing `{code, msg, data}` envelope. Account
responses carry `X-Account-Epoch`. POST/PUT require the current epoch header;
missing/stale epochs return 409. Inbox writes do not require GUI confirmation.

## List and Overview

`GET /api/conversations` now returns an object in `data`, never an array:

```ts
{
  items: ConversationSummary[],
  total: number,
  offset: number,
  limit: number,
  inbox_revision: number,
  pagination_revision: string
}
```

Supported query parameters:

| Parameter | Contract |
| --- | --- |
| q | Literal search, max 200 characters, default empty |
| search_scope | `all` (default), `customer`, `messages` |
| country | Exact resolved country, Unicode casefold comparison |
| tag | Exact quality/growth tag, case sensitive |
| reply_state | `needs_reply`, `waiting_customer`, `history_pending`, `none`, `unknown` |
| unread | Optional boolean, matches conversations with unread_count > 0 |
| overdue | Optional boolean |
| offset | Integer >= 0, default 0 |
| limit | Integer 1..100, default 50 |
| pagination_revision | Required when offset > 0 |

Missing or changed pagination_revision on a later page returns 409: discard
loaded pages and request offset=0. Reuse the same filters with the token.
Tokens exclude offset/limit and wall-clock seconds; they include scoped message
projections, resolved profiles, inbox metadata/states and overdue flags. A
deadline crossing can invalidate pagination without changing inbox_revision.
New arrivals, visible historical edits, profile edits, reads and timeout changes
invalidate affected dataset tokens. Order is latest timestamp descending, then
numeric SID descending. Total is measured before pagination.

Messages search covers all stored visible content_label values using SQLite
instr/lower, not raw message/card JSON. Chinese, `%` and `_` are literals. Customer
search uses the validated displayed name/company/identifiers/email/phone profile;
country prefers the resolved UserInfo country and falls back to Customer.region.

`GET /api/inbox/overview` accepts the same search/filter parameters (no pagination)
and returns:

```ts
{
  counts: {total, unread, needs_reply, overdue, history_pending, waiting_customer},
  inbox_revision: number,
  timeout_seconds: number,
  updated_at: number // Unix seconds, observation time
}
```

Counts are conversations, not messages. List/overview each use one SQLite BEGIN
snapshot for metadata, message projections, profiles and states. Only requested
page summary DTOs are assembled; filtering may inspect all lightweight scoped
projections. No full message ORM objects are loaded on these paths.

Search, latest message, dialogue count, message fingerprints, detail messages,
AI suggestions/analysis inputs and ZIP exports are restricted to the selected
seller/canonical source directory's ledger, joined
by both external_mid and SID. A shared SID does not grant visibility to messages
that only belong to another directory. Filtering never deletes SDK records.
The SDK stores one current body per external_mid: if two directories reuse the
same source message ID with different bodies, historical body provenance is not
available. Directory-exclusive message IDs remain isolated.

Detail, suggestions, analysis and export use the same scoped conversation loader.
Suggestions/analysis return 404 if a SID has no messages in the current ledger,
without calling the model. Export reports such SIDs in `missing` when other
requested conversations are available, or returns 404 if none are available.
AI requests and export do not capture read snapshots or mark conversations read.

## State and Explicit Read

List items and detail aggregates expose these flat fields:

```ts
{
  unread_count: number,
  reply_state: "needs_reply" | "waiting_customer" | "history_pending" | "none" | "unknown",
  pending_since: number | null,
  due_at: number | null,
  is_overdue: boolean,
  history_pending: boolean,
  uncertain: boolean
}
```

The previous fake status/priority fields are removed. Unread is workspace state,
independent of platform read receipts. Baseline history starts with zero unread;
historical unanswered conversations are history_pending and have no deadline.

`GET /api/conversations/{sid}` additionally returns `read_snapshot: string`.
GET never marks read. POST `/api/conversations/{sid}/read` with
`{read_snapshot}` explicitly marks through that boundary and returns
`{state: InboxState, inbox_revision: number}`. This InboxState includes the seven
fields above plus the store's numeric `read_seq` and `snapshot_seq`. Those
sequences are informational; only the opaque read_snapshot authorizes a read.

The API passes the current epoch to InboxStore.snapshot/mark_read and returns the
store token directly, without an API signature wrapper or legacy token fallback.
The opaque signed token binds the database, seller, directory, SID, sequence and
account epoch. It expires on restart/account change, including A -> B -> A.
Invalid/tampered/cross-session tokens return 409. Capture happens BEFORE loading
detail messages, so concurrent new arrivals can appear in detail while remaining
unread after the POST. Detail messages and state are separate reads; they do not
promise a single transaction. Only the read boundary is guaranteed safe.

## Settings and Polling

GET `/api/inbox/settings` returns `{timeout_seconds, inbox_revision}`.
PUT with `{timeout_seconds}` returns the same shape. The value must be an integer
3600..604800 inclusive, default 86400, stored per seller/source directory.
PUT returns 409 immediately when the account is busy.

GET `/api/conversations/revision` retains existing fields and adds
`inbox_revision: number` and `next_due_at: number | null` (earliest future pending
deadline in Unix seconds). This endpoint is observation only: it does not create
tables, initialize a baseline, migrate SDK data, read/decrypt the IM source or
schedule sync. Polling revision is not a snapshot shared with later requests.
Reload list/overview when a known deadline crosses, or periodically (for example
once a minute); inbox_revision alone cannot signal a time threshold crossing.

The first list/detail/overview/settings access may initialize an existing selected
archive once through InboxStore.initialize. This adds application-owned inbox
tables/rows only and never rewrites existing CRM data. An empty new account with
an unready source still returns 503 and is not assigned a fabricated baseline.
This also applies when the same seller has an archive in another directory: a
GET cannot finish an empty baseline for the newly selected directory. Its first
successful source sync establishes history with zero unread.

## Integration Boundary

The new shared/crm/inbox_queries.py confines read-transaction access to
InboxStore._connection/_metadata/_states. No InboxStore, SDK, sync.py or
sync_store.py changes are required by this API implementation. A future public
read-session exposure can replace these private calls without changing HTTP
contracts. Stage 3 send/outbox/confirmation endpoints are unchanged.
