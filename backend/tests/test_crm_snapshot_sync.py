import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from threading import Event

import pytest
from sqlalchemy import event
from sqlmodel import Session

from backend.app.shared.backend.im_chat_db import build_conversations, open_readonly
from backend.app.shared.chat_format import conversation_transcript
from backend.app.shared.crm import sync
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.sdk import Account, Customer, Message
from backend.app.shared.crm.sync_store import canonical_source_dir, read_sync_state
from backend.app.shared.crm.views import CrmResolver, message_display_text, resolve_role
from backend.app.shared.mitm.pool import SelfInfo
from backend.tests.crm_helpers import conversations_for


def source_row(mid, text="same text", *, timestamp=1756720000, kind=0, cid="seller-buyer", sender="buyer@icbu"):
    return (cid, str(mid), sender, timestamp, kind, text, "", text.encode())


def add_source(path, rows, table="msg_table"):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" (cid TEXT, mid TEXT, sender_id TEXT, created_at INTEGER, '
            "user_content_type INTEGER, content_label TEXT, extension TEXT, content BLOB)"
        )
        conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
        conn.commit()


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    source = tmp_path / "im.sqlite"
    target = tmp_path / "crm.sqlite"
    directory = str(tmp_path / "source")
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(target))

    def apply(revision=1, info=None):
        return sync.sync_im_database(
            source, "seller", info, source_revision=revision, data_dir=directory,
        ).result(timeout=10)

    return source, target, directory, apply


def crm_rows(path):
    with closing(sqlite3.connect(path)) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in names}


def test_snapshot_delta_late_arrival_new_table_and_retained_history(snapshot):
    source, target, directory, apply = snapshot
    add_source(source, [source_row(1), source_row(2), source_row(99, cid="other-buyer")])
    first = apply(7)
    assert (first["inserted"], first["updated"], first["unchanged"]) == (2, 0, 0)
    assert first["revision"] == 1 and first["applied_source_revision"] == 7
    assert first["view_revision"] == 1 and first["source_revision"] == 7
    before = crm_rows(target)
    second = apply(8)
    after = crm_rows(target)
    assert (second["inserted"], second["updated"], second["unchanged"]) == (0, 0, 2)
    assert {key: rows for key, rows in before.items() if key != "app_crm_sync_state"} == {
        key: rows for key, rows in after.items() if key != "app_crm_sync_state"
    }
    assert second["revision"] == 2
    adapter = sync.CRMAdapter(target)
    mid = message_external_id("seller", "msg_table", "1")
    with Session(adapter.engine) as session:
        session.get(Message, mid).read = True
        session.commit()
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE msg_table SET content_label='edited', content=?, created_at=? WHERE mid='1'", (b"edited", 1756720010))
        conn.execute("DELETE FROM msg_table WHERE mid='2'")
        conn.commit()
    add_source(source, [source_row(3, timestamp=1756710000)])
    add_source(source, [source_row(1, timestamp=1756700000)], table="msg_new")
    third = apply(9)
    assert (third["inserted"], third["updated"], third["unchanged"]) == (2, 1, 0)
    assert third["revision"] == 3
    messages = {message.external_mid: message for message in adapter.messages.list_message()}
    assert len(messages) == 4
    assert messages[mid].read is True
    assert messages[mid].content["content_label"] == "edited"
    assert messages[mid].created_at == datetime.fromtimestamp(1756720010)
    adapter.engine.dispose()
    reopened = sync.CRMAdapter(target)
    assert len(conversations_for(reopened, "seller")[0].messages) == 4
    assert read_sync_state("seller", directory, target) == third
    assert third["last_success"] >= first["last_success"] > 0


@pytest.mark.parametrize("kind", [1, 10001, 99999, None])
def test_unsupported_type_keeps_activity_direction_and_safe_placeholder(snapshot, kind):
    source, target, _, apply = snapshot
    add_source(source, [source_row(1, "opaque blob label", kind=kind, sender="seller@icbu")])
    assert apply()["inserted"] == 1
    adapter = sync.CRMAdapter(target)
    conversation = conversations_for(adapter, "seller")[0]
    message = conversation.messages[0]
    assert message.user_content_type == kind
    assert message.sender_id == "seller@icbu"
    assert message.created_at == datetime.fromtimestamp(1756720000)
    assert resolve_role(message, CrmResolver("seller")) == "seller"
    label = message_display_text(message)
    assert label and "opaque" not in label
    assert str(kind if kind is not None else "unknown") in label
    assert conversation.last_content_label == label
    assert conversation_transcript(conversation.messages, CrmResolver("seller"))[0][2] == label


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure_stage", ["batch", "metadata"])
def test_failure_rolls_back_all_crm_rows_and_revision(snapshot, monkeypatch, existing, failure_stage):
    source, target, directory, apply = snapshot
    add_source(source, [source_row(1)])
    adapter = sync.CRMAdapter(target)
    if existing:
        apply(1)
    before = crm_rows(target)
    state_before = read_sync_state("seller", directory, target)
    add_source(source, [source_row(i, cid="seller-newbuyer") for i in range(2, 1003)])
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE msg_table SET content_label='changed' WHERE mid='1'")
        conn.commit()

    if failure_stage == "metadata":
        original = sync.write_sync_state

        def fail_after_metadata(*args):
            original(*args)
            # A separate polling reader cannot see even the pending state/table.
            assert read_sync_state("seller", directory, target) == state_before
            raise RuntimeError("injected after metadata")

        monkeypatch.setattr(sync, "write_sync_state", fail_after_metadata)
    else:
        batches = []

        def fail_second_batch(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO message "):
                batches.append(statement)
                if len(batches) == 2:
                    raise RuntimeError("injected second message batch")

        event.listen(adapter.engine, "before_cursor_execute", fail_second_batch)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            apply(2, SelfInfo(ali_id="seller", first_name="Changed"))
    finally:
        if failure_stage == "batch":
            event.remove(adapter.engine, "before_cursor_execute", fail_second_batch)
    assert crm_rows(target) == before
    assert read_sync_state("seller", directory, target) == state_before
    if failure_stage == "metadata":
        monkeypatch.setattr(sync, "write_sync_state", original)
    # Retry the same failed snapshot successfully; failure consumed no revision.
    result = apply(2)
    assert result["revision"] == (2 if existing else 1)
    assert result["inserted"] == (1001 if existing else 1002)


def test_thousand_messages_have_bounded_sql_and_one_commit(snapshot, monkeypatch):
    source, target, _, apply = snapshot
    add_source(source, [source_row(i) for i in range(1000)])
    adapter = sync.CRMAdapter(target)
    statements = []
    commits = []
    written_ids = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        if statement.startswith("INSERT INTO message "):
            written_ids.extend(row[0] for row in (parameters if executemany else [parameters]))

    def reject_sdk_commit(*args, **kwargs):
        pytest.fail("Snapshot called a per-row SDK upsert")

    for cls, method in (
        (sync.MessageManager, "upsert_message"), (sync.AccountManager, "upsert_account"),
        (sync.CustomerManager, "upsert_customer"), (sync.PlatformManager, "upsert_platform"),
        (sync.AccountMappingManager, "upsert_account_mapping"), (sync.SessionMetaManager, "upsert_session_meta"),
    ):
        monkeypatch.setattr(cls, method, reject_sdk_commit)
    event.listen(adapter.engine, "before_cursor_execute", record)
    event.listen(adapter.engine, "commit", lambda conn: commits.append(1))
    assert apply()["inserted"] == 1000
    assert len(commits) == 1
    assert len([sql for sql in statements if sql.startswith("SELECT")]) < 30
    assert len([sql for sql in statements if sql.startswith(("INSERT", "UPDATE"))]) < 15
    assert len([sql for sql in statements if sql.startswith("INSERT INTO message ")]) == 2
    statements.clear()
    commits.clear()
    assert apply(2)["unchanged"] == 1000
    assert len(commits) == 1
    assert not [sql for sql in statements if sql.startswith("UPDATE")]
    assert not [sql for sql in statements if sql.startswith("INSERT INTO message ")]
    assert len([sql for sql in statements if sql.startswith("SELECT")]) < 30
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE msg_table SET content_label='changed' WHERE mid IN ('1', '501', '999')")
        conn.commit()
    add_source(source, [source_row(1000, timestamp=1756710000)])
    written_ids.clear()
    statements.clear()
    commits.clear()
    result = apply(3)
    assert (result["inserted"], result["updated"], result["unchanged"]) == (1, 3, 997)
    assert set(written_ids) == {message_external_id("seller", "msg_table", str(mid)) for mid in (1, 501, 999, 1000)}
    assert len(commits) == 1
    assert len([sql for sql in statements if sql.startswith(("INSERT", "UPDATE"))]) <= 4


def test_poll_is_readonly_and_scope_is_canonical(snapshot, tmp_path):
    source, target, directory, apply = snapshot
    missing = tmp_path / "not-created" / "crm.sqlite"
    assert read_sync_state("seller", directory, missing) is None
    assert not missing.parent.exists()
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
        conn.commit()
    before = target.read_bytes()
    assert read_sync_state("seller", directory, target) is None
    assert target.read_bytes() == before
    add_source(source, [source_row(1)])
    result = apply(10)
    equivalent = directory + "/../source/."
    assert read_sync_state("seller", equivalent, target) == result
    assert result["data_dir"] == canonical_source_dir(directory)
    assert read_sync_state("other", directory, target) is None
    assert read_sync_state("seller", directory + "-other", target) is None
    assert read_sync_state("seller", "", target) is None
    assert canonical_source_dir("") == ""
    legacy = sync.sync_im_database(source, "seller", None).result(timeout=10)
    assert legacy["revision"] == 1 and legacy["source_revision"] == 0
    assert read_sync_state("seller", "", target) == legacy
    assert read_sync_state("seller", directory, target) == result


def test_submit_captures_target_and_profile_without_account_lock(snapshot, monkeypatch):
    from backend.app.shared.backend import account_context

    source, target, directory, _ = snapshot
    add_source(source, [source_row(1)])
    release = Event()
    blocker = sync._SYNC_EXECUTOR.submit(lambda: release.wait(10))
    info = SelfInfo(ali_id="seller", first_name="Original")
    try:
        with account_context.account_lock:
            future = sync.sync_im_database(source, "seller", info, source_revision=4, data_dir=directory)
            info.first_name = "Mutated"
            other = target.with_name("wrong.sqlite")
            monkeypatch.setenv("MAA_CRM_DB_PATH", str(other))
            release.set()
            result = future.result(timeout=10)
    finally:
        release.set()
        blocker.result(timeout=10)
    assert not other.exists()
    assert read_sync_state("seller", directory, target) == result
    assert sync.CRMAdapter(target).get_self_info("seller").first_name == "Original"


def test_account_customer_times_and_editable_fields_survive_repeated_snapshot(snapshot):
    source, target, _, apply = snapshot
    add_source(source, [source_row(1)])
    apply(1)
    adapter = sync.CRMAdapter(target)
    account = adapter._account_by_mapping("ali_id", "seller")
    with Session(adapter.engine) as session:
        current = session.get(Account, account.aid)
        current.sids = [17]
        customer = session.get(Customer, current.cid)
        customer.name = "User chosen name"
        customer.region = "User chosen region"
        customer.sex = "custom"
        customer.extra = {**customer.extra, "custom": True}
        session.commit()
    before = crm_rows(target)
    apply(2)
    after = crm_rows(target)
    for table in ("account", "customer", "platform", "message"):
        assert before[table] == after[table]
    apply(3, SelfInfo(ali_id="seller", first_name="New profile"))
    changed = adapter.accounts.get_account(account.aid)
    assert changed.created_time == account.created_time
    assert changed.updated_time > account.updated_time
    customer = adapter.customers.get_customer(account.cid)
    assert customer.name == "User chosen name" and customer.region == "User chosen region"
    assert customer.sex == "custom" and customer.extra["custom"] is True


def test_source_snapshot_spans_all_tables(snapshot, monkeypatch):
    from backend.app.shared.backend import im_chat_db

    source, target, _, apply = snapshot
    add_source(source, [source_row(1)], "msg_a")
    add_source(source, [source_row(2)], "msg_z")
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        original = im_chat_db.list_msg_tables

        def change_after_snapshot_starts(conn):
            tables = original(conn)
            writer.execute("UPDATE msg_z SET content_label='later snapshot'")
            writer.commit()
            return tables

        # sqlite's writer connection stays on this thread, so test the worker body directly.
        monkeypatch.setattr(im_chat_db, "list_msg_tables", change_after_snapshot_starts)
        sync._sync_im_database_now(source, "seller", SelfInfo(ali_id="seller"), target, 1, "")
    assert {message.content["content_label"] for message in sync.CRMAdapter(target).messages.list_message()} == {"same text"}
    monkeypatch.setattr(im_chat_db, "list_msg_tables", original)
    assert apply(2)["updated"] == 1


def test_source_table_order_does_not_hide_latest_activity(snapshot):
    source, _, _, _ = snapshot
    add_source(source, [source_row(1, timestamp=1756720100)], "msg_a")
    add_source(source, [source_row(2, timestamp=1756720000)], "msg_z")
    with closing(open_readonly(source)) as conn:
        conversation = build_conversations(conn, "seller")[0]
    assert [message.mid for message in conversation.messages] == ["2", "1"]
    assert conversation.last_created_at == 1756720100


def test_concurrent_snapshots_serialize_committed_revisions(snapshot):
    source, target, directory, apply = snapshot
    add_source(source, [source_row(1)])
    apply(1)
    first = sync.CRMAdapter(target)
    second = sync.CRMAdapter(target)
    with closing(open_readonly(source)) as conn:
        conversations = build_conversations(conn, "seller")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(
            adapter.sync_conversations, conversations, SelfInfo(ali_id="seller"),
            source_revision=2, data_dir=directory,
        ) for adapter in (first, second)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(result["revision"] for result in results) == [2, 3]
    assert all(result["unchanged"] == 1 for result in results)
    assert read_sync_state("seller", directory, target)["revision"] == 3
