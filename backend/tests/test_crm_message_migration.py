import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace

import pytest

from backend.app.shared.crm import migrations
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.sdk import ChatHistory, ChatHistoryManager, Translate, TranslateManager
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo
from backend.tests.test_crm_account_scope import conversation


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "crm.sqlite"
    adapter = CRMAdapter(path)
    adapter.sync_conversations([conversation("A")], SelfInfo(ali_id="A"))
    TranslateManager(database_path=path).upsert_translate(Translate(text_hash="text-hash", translation="msg_table:1"))
    ChatHistoryManager(database_path=path).add_chat_history(
        ChatHistory(name="history", content=[{"role": "user", "content": "msg_table:1"}])
    )
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM app_crm_migrations")
        conn.execute("UPDATE message SET external_mid='msg_table:1'")
        conn.execute('CREATE TABLE "message notes" (id INTEGER PRIMARY KEY, old_id TEXT REFERENCES message(external_mid), note TEXT)')
        conn.execute('INSERT INTO "message notes" VALUES (1, ?, ?)', ("msg_table:1", "preserved note"))
        conn.execute("CREATE TABLE legacy_pointer (external_mid TEXT PRIMARY KEY, data BLOB)")
        conn.execute("INSERT INTO legacy_pointer VALUES (?, ?)", ("msg_table:1", b"untouched"))
        conn.commit()
    return path


def snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall() for table in tables}


def backups(path):
    return list((path.parent / "backups").glob("*.sqlite"))


def test_first_adapter_migrates_preserving_rows_references_and_actual_translate(legacy_db):
    before = snapshot(legacy_db)
    adapter = CRMAdapter(legacy_db)
    after = snapshot(legacy_db)
    scoped = message_external_id("A", "msg_table", "1")
    assert after["message"] == [(scoped, *before["message"][0][1:])]
    assert after["message notes"] == [(1, scoped, "preserved note")]
    assert after["legacy_pointer"] == [(scoped, b"untouched")]
    for table in before.keys() - {"message", "message notes", "legacy_pointer", "app_crm_migrations"}:
        assert after[table] == before[table]
    assert TranslateManager(database_path=legacy_db).get_translate("text-hash").translation == "msg_table:1"
    assert len(backups(legacy_db)) == 1
    assert snapshot(backups(legacy_db)[0]) == before
    assert after["app_crm_migrations"][0][0] == migrations.MESSAGE_SCOPE_MIGRATION
    assert after["app_crm_migrations"][0][2] == str(backups(legacy_db)[0])
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    adapter.sync_conversations([conversation("A", "resynced")], SelfInfo(ali_id="A"))
    adapter.sync_conversations([conversation("B", "second seller")], SelfInfo(ali_id="B"))
    assert len(adapter.messages.list_message()) == 2
    assert len(backups(legacy_db)) == 1


def test_completed_migration_does_not_rescan_or_backup(legacy_db, monkeypatch):
    CRMAdapter(legacy_db)
    before = snapshot(legacy_db)
    monkeypatch.setattr(migrations, "_migrate_rows", lambda *args: pytest.fail("Unnecessary full scan"))
    monkeypatch.setattr(migrations, "_backup_database", lambda *args: pytest.fail("Unnecessary backup"))
    CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == before


@pytest.mark.parametrize("invalid", ["no_session", "bad_cid", "collision", "broken_reference", "update_error"])
def test_migration_failure_rolls_back_and_blocks_adapter(legacy_db, invalid):
    with closing(sqlite3.connect(legacy_db)) as conn:
        if invalid == "no_session":
            conn.execute("UPDATE message SET sid=98765")
        elif invalid == "bad_cid":
            conn.execute("UPDATE message SET content=json_set(content, '$.cid', 'B-buyer')")
        elif invalid == "collision":
            conn.execute(
                "INSERT INTO message SELECT ?, sid, sender, read, content, type, created_at FROM message",
                (message_external_id("A", "msg_table", "1"),),
            )
        elif invalid == "broken_reference":
            conn.execute('UPDATE "message notes" SET old_id=\'missing\'')
        else:
            conn.execute("CREATE TRIGGER stop_update BEFORE UPDATE ON message BEGIN SELECT RAISE(ABORT, 'stop'); END")
        conn.commit()
    before = snapshot(legacy_db)
    with pytest.raises(migrations.CRMMigrationError):
        CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == before
    assert snapshot(backups(legacy_db)[0]) == before
    with pytest.raises(migrations.CRMMigrationError):
        CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == before


def test_backup_failure_prevents_any_mutation(legacy_db, monkeypatch):
    before = snapshot(legacy_db)

    def fail_backup(path):
        raise OSError("backup unavailable")

    monkeypatch.setattr(migrations, "_backup_database", fail_backup)
    with pytest.raises(migrations.CRMMigrationError, match="backup unavailable"):
        CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == before


def test_interruption_rolls_back_and_retry_uses_independent_backup(legacy_db, monkeypatch):
    before = snapshot(legacy_db)
    migrate_rows = migrations._migrate_rows

    def interrupt(conn, tables):
        migrate_rows(conn, tables)
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_migrate_rows", interrupt)
        with pytest.raises(KeyboardInterrupt):
            CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == before
    CRMAdapter(legacy_db)
    assert len(backups(legacy_db)) == 2
    assert all(snapshot(backup) == before for backup in backups(legacy_db))


def test_concurrent_first_access_serializes_and_backs_up_once(legacy_db):
    before = snapshot(legacy_db)
    with ThreadPoolExecutor(max_workers=4) as executor:
        adapters = list(executor.map(CRMAdapter, [legacy_db] * 4))
    assert len(backups(legacy_db)) == 1
    assert snapshot(backups(legacy_db)[0]) == before
    assert all(len(adapter.messages.list_message()) == 1 for adapter in adapters)


def test_wal_backup_contains_committed_wal_rows(legacy_db):
    with closing(sqlite3.connect(legacy_db)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE translate SET translation='committed in WAL'")
        writer.commit()
        before = snapshot(legacy_db)
        CRMAdapter(legacy_db)
        assert snapshot(backups(legacy_db)[0]) == before


def test_migration_uses_each_session_owner_and_updates_multiple_references(tmp_path):
    path = tmp_path / "crm.sqlite"
    adapter = CRMAdapter(path)
    for seller, mid in (("A", "1"), ("B", "2")):
        conv = conversation(seller)
        conv = replace(conv, messages=[replace(conv.messages[0], mid=mid)])
        adapter.sync_conversations([conv], SelfInfo(ali_id=seller))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM app_crm_migrations")
        conn.execute("CREATE TABLE refs (id INTEGER PRIMARY KEY, old_id TEXT REFERENCES message(external_mid) ON UPDATE CASCADE)")
        for seller, mid in (("A", "1"), ("B", "2")):
            conn.execute("UPDATE message SET external_mid=? WHERE external_mid=?", (
                f"msg_table:{mid}", message_external_id(seller, "msg_table", mid),
            ))
            conn.execute("INSERT INTO refs VALUES (?, ?)", (int(mid), f"msg_table:{mid}"))
        conn.commit()
    CRMAdapter(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT old_id FROM refs ORDER BY id").fetchall() == [
            (message_external_id("A", "msg_table", "1"),), (message_external_id("B", "msg_table", "2"),),
        ]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_preserves_two_level_reference_chain(legacy_db):
    with closing(sqlite3.connect(legacy_db)) as conn:
        conn.execute(
            "CREATE TABLE pointer (external_mid TEXT PRIMARY KEY "
            "REFERENCES message(external_mid) ON UPDATE CASCADE, payload BLOB)"
        )
        conn.execute(
            "CREATE TABLE annotation (id INTEGER PRIMARY KEY, ref TEXT "
            "REFERENCES POINTER(EXTERNAL_MID) ON UPDATE CASCADE, note TEXT)"
        )
        conn.execute("INSERT INTO pointer VALUES (?, ?)", ("msg_table:1", b"pointer data"))
        conn.execute("INSERT INTO annotation VALUES (1, ?, ?)", ("msg_table:1", "msg_table:1"))
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    before = snapshot(legacy_db)
    CRMAdapter(legacy_db)
    scoped = message_external_id("A", "msg_table", "1")
    after = snapshot(legacy_db)
    assert after["pointer"] == [(scoped, b"pointer data")]
    assert after["annotation"] == [(1, scoped, "msg_table:1")]
    assert after["message"] == [(scoped, *before["message"][0][1:])]
    assert snapshot(backups(legacy_db)[0]) == before
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    CRMAdapter(legacy_db)
    assert snapshot(legacy_db) == after
    assert len(backups(legacy_db)) == 1


@pytest.mark.parametrize("implicit_target", [False, True])
def test_migration_reference_chain_maps_only_matching_composite_column(legacy_db, implicit_target):
    with closing(sqlite3.connect(legacy_db)) as conn:
        conn.execute(
            "CREATE TABLE pointer (message_ref TEXT REFERENCES message ON UPDATE CASCADE, "
            "tenant TEXT, PRIMARY KEY (tenant, message_ref))"
        )
        target = "pointer" if implicit_target else "pointer(tenant, message_ref)"
        conn.execute(
            "CREATE TABLE annotation (tenant TEXT, ref TEXT, note TEXT, "
            f"FOREIGN KEY (tenant, ref) REFERENCES {target} ON UPDATE CASCADE)"
        )
        # Equal text in the other FK component must not make it a message ID.
        conn.execute("INSERT INTO pointer VALUES ('msg_table:1', 'msg_table:1')")
        conn.execute("INSERT INTO annotation VALUES ('msg_table:1', 'msg_table:1', 'untouched')")
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    CRMAdapter(legacy_db)
    scoped = message_external_id("A", "msg_table", "1")
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("SELECT * FROM pointer").fetchall() == [(scoped, "msg_table:1")]
        assert conn.execute("SELECT * FROM annotation").fetchall() == [("msg_table:1", scoped, "untouched")]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_reference_chain_handles_cycles_from_non_fk_seed(legacy_db):
    with closing(sqlite3.connect(legacy_db)) as conn:
        # legacy_pointer.external_mid is a rewrite seed without a FK to message.
        conn.execute(
            "CREATE TABLE pointer (message_ref TEXT PRIMARY KEY "
            "REFERENCES legacy_pointer ON UPDATE CASCADE "
            "REFERENCES annotation(ref) ON UPDATE CASCADE)"
        )
        conn.execute("CREATE TABLE annotation (ref TEXT PRIMARY KEY REFERENCES pointer ON UPDATE CASCADE)")
        conn.execute("INSERT INTO pointer VALUES ('msg_table:1')")
        conn.execute("INSERT INTO annotation VALUES ('msg_table:1')")
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    CRMAdapter(legacy_db)
    scoped = message_external_id("A", "msg_table", "1")
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("SELECT message_ref FROM pointer").fetchall() == [(scoped,)]
        assert conn.execute("SELECT ref FROM annotation").fetchall() == [(scoped,)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
