# Customer Portrait API

Business portrait state (stage, tags, failure info) maintained by humans on top
of the existing conversation list. Portraits are app-owned extension tables in
the CRM SQLite; the generic SDK is unchanged. All endpoints share the inbox
account boundary: they require a selected seller and canonical source directory
and return `X-Account-Epoch`; writes additionally require the current epoch
header, while reads tolerate a missing one.

## Scope and Identity

- A portrait binds to the resolved contact `Customer.cid` of the conversation,
  not to a display string. The resolver keeps the non-self participant when it
  is the positional contact of a two-party conversation; several non-self
  participants win only when they share one `cid`. Two-party conversations
  otherwise fall back to their second participant so synced rows keep working
  when `is_self` is missing. Anything else resolves to no customer.
- Portrait rows and tag relations are scoped by
  `(seller, canonical_data_dir, cid)`. Tag definitions are shared by the CRM
  database; relations, operation records and revisions stay workspace-scoped.
- Sync never silently redirects conflicting account aliases: profile
  enrichment skips the disputed item, and chat sync either keeps the committed
  session contact or rejects the snapshot. A stopped sync keeps the previous
  archive; it never moves a portrait to another customer.
- When a conversation has no resolved customer, list/detail return
  `cid: null` and `portrait_editable: false`; the UI disables maintenance and
  keeps chat and export working. Writes against such targets return 409.

## List and Detail Fields

`customer_view` in list, overview and detail DTOs adds:

```ts
{
  cid: number | null,
  business_stage: "unknown" | "new_inquiry" | "following_up" | "negotiating" | "won" | "dormant" | "lost",
  business_tags: [{tag_id: string, name: string, archived: boolean}],
  business_version: number,          // 0 = never maintained
  lost_reason: string | null,
  lost_note: string | null,
  lost_at: number | null,            // Unix seconds when marked lost
  portrait_editable: boolean
}
```

`business_stage` is the current business judgement; it is not an order state
machine and does not claim platform truth for `won`. Existing platform fields
(`tags`, `quality_tag`, `growth_level`) remain read-only data collected from the
platform. Session work state (unread, reply state, deadline) is unchanged.

`lost` maintenance requires `lost_reason` and optional `lost_note`; leaving the
lost stage clears those fields. History remains available through operations.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/portraits/tags` | Tag catalog with workspace usage counts |
| POST | `/api/portraits/tags` | Create tag `{name, description?}` |
| PATCH | `/api/portraits/tags/{tag_id}` | Rename, edit description, archive/unarchive |
| POST | `/api/portraits/batch` | Apply one change set to 1..50 sessions |
| POST | `/api/portraits/export` | CSV of selected sessions' customers (<= 100) |
| GET | `/api/portraits/operations?cid=&limit=` | Recent portrait operations |

Tag names are NFKC-normalized, trimmed, max 40 characters, and unique per CRM
database (casefolded). Tags are archived instead of deleted; archived tags can
be displayed and removed from customers but not added. Renaming a tag keeps all
relations and bumps `tag_revision` for every workspace.

## Batch Contract

Request:

```json
{
  "request_id": "one id per user action, <= 64 chars",
  "targets": [{"sid": 42, "expected_cid": 301, "expected_version": 3}],
  "changes": {
    "stage": "following_up",
    "lost_reason": "price",
    "lost_note": "free text",
    "add_tag_ids": ["..."],
    "remove_tag_ids": ["..."]
  }
}
```

Rules:

- Omitted change fields are left unchanged. `stage` must be an explicit stage
  value; `lost_reason`/`lost_note` are only valid while the effective stage is
  `lost`, either declared in the request or already stored.
- One change set applies to every target. Targets are deduplicated by resolved
  `cid`; all SIDs of one customer must submit the same `expected_version`.
- The store re-checks visibility (`app_inbox_message` in the current
  seller/directory), the resolved customer against `expected_cid`, and the
  current portrait version against `expected_version` in one
  `BEGIN IMMEDIATE` transaction. Any mismatch rejects the whole batch with 409
  and no customer changes; the client refreshes and retries.
- Successful batches commit portraits, tag relations, the operation record and
  the workspace `portrait_revision` together. Targets whose values already
  match are reported as `unchanged` without a version bump.
- `request_id` is idempotent per workspace: an identical retry returns the
  stored receipt, while reusing the id with a different payload or from another
  workspace conflicts with 409.

Response:

```json
{
  "request_id": "...",
  "portrait_revision": 5,
  "tag_revision": 2,
  "changed": [{"cid": 301, "sids": [42], "version": 4}],
  "unchanged": [{"cid": 302, "sids": [43], "version": 0}]
}
```

Validation errors (unknown stage/reason, missing tag, mixed add/remove,
malformed targets) return 422. Missing tags and archived-tag additions are
rejected before any write.

## Export

`POST /api/portraits/export` with `{conversationIds: number[]}` re-resolves
visibility server-side, de-duplicates by `cid`, and returns:

```ts
{
  filename: "customer-portraits-<timestamp>.csv",
  content: "<BOM + UTF-8 CSV text>",
  exported: number,
  missing: number[]     // no ledger visibility or no resolvable contact customer
}
```

Columns: `cid, ali_id, name, company, country, stage, business_tags,
lost_reason, lost_note, updated_at`. Values are read from committed state, not
from client drafts; cells are guarded against spreadsheet formula prefixes.

## Operations and Revisions

`GET /api/portraits/operations` returns append-only records (newest first) with
the request id, kind, creation time, the submitted changes and the affected
`{cid, sids, version}` targets. There is no bulk rollback; corrections are new
operations, which keeps the full change history.

`GET /api/conversations/revision` exposes `portrait_revision` (workspace
portrait edits) and `tag_revision` (shared tag definitions) alongside the inbox
revision. Both participate in the list pagination fingerprint: any committed
portrait change invalidates stale page tokens, and clients refresh list/detail
when either observed revision increases.

## Readiness

- Reads never create the CRM database or migrate tables; missing schema or a
  missing database yields empty defaults.
- The first write creates the app tables inside its own transaction.
- A write with no CRM database returns 503; a write before the chat baseline
  tables exist returns 409; validation errors return 422; stale targets,
  versions or receipts return 409.
