"""Persists independent suggestion runs and immutable inputs in the host CRM."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from backend.app.api.envelope import AppError
from backend.app.shared.crm.paths import default_crm_database_path

PROCESS_ID = uuid4().hex


class SuggestionStore:
    def __init__(self, database_path=None, *, owner=PROCESS_ID):
        self.database_path = Path(database_path or default_crm_database_path()).resolve()
        self.owner = owner

    @contextmanager
    def connection(self, *, write=False):
        if write:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "rwc" if write else "ro"
        with closing(sqlite3.connect(self.database_path.as_uri() + f"?mode={mode}", uri=True,
                                     timeout=30, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            if write:
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                if write:
                    conn.commit()
            except BaseException:
                if write:
                    conn.rollback()
                raise

    @staticmethod
    def scope(context):
        return context.self_ali_id, os.path.normcase(str(Path(context.data_dir).resolve()))

    def decode(self, row):
        record = dict(row)
        for field in ("request", "snapshot", "result"):
            record[field] = json.loads(record[field]) if record[field] is not None else None
        # Old work is observable after restart, but never replayed by a GET or retry.
        if record["owner"] != self.owner and record["status"] in ("queued", "running"):
            record.update(status="failed", error="程序已重启，本次生成已中断，请重新测试。")
        return record

    def read(self, context, conversation_id, run_id=None, *, limit=2):
        if not self.database_path.is_file():
            return []
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_suggestion_run'").fetchone():
                return []
            sql = "SELECT * FROM app_suggestion_run WHERE seller=? AND data_dir=? AND conversation_id=?"
            params = (*self.scope(context), conversation_id)
            if run_id is not None:
                sql += " AND id=?"
                params += (run_id,)
            rows = conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limit))
            return [self.decode(row) for row in rows]

    def get(self, context, conversation_id, run_id):
        records = self.read(context, conversation_id, run_id)
        return records[0] if records else None

    def create(self, context, conversation_id, run_id, request, snapshot):
        now = time.time()
        with self.connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS app_suggestion_run (
                id TEXT PRIMARY KEY, seller TEXT NOT NULL, data_dir TEXT NOT NULL,
                conversation_id INTEGER NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL,
                request TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT, error TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS app_suggestion_run_scope ON app_suggestion_run "
                         "(seller, data_dir, conversation_id, created_at)")
            record = dict(id=run_id, seller=self.scope(context)[0], data_dir=self.scope(context)[1],
                          conversation_id=conversation_id, owner=self.owner, status="queued",
                          request=json.dumps(request, ensure_ascii=False), snapshot=json.dumps(snapshot, ensure_ascii=False),
                          result=None, error=None, created_at=now, updated_at=now)
            try:
                conn.execute(f"INSERT INTO app_suggestion_run ({','.join(record)}) VALUES ({','.join('?' for _ in record)})",
                             tuple(record.values()))
            except sqlite3.IntegrityError:
                raise AppError("生成请求 ID 已被使用，请恢复原请求。", status_code=409)
        return self.get(context, conversation_id, run_id)

    def finish(self, context, conversation_id, run_id, status, *, result=None, error=None):
        with self.connection(write=True) as conn:
            conn.execute("UPDATE app_suggestion_run SET status=?, result=?, error=?, updated_at=? "
                         "WHERE id=? AND seller=? AND data_dir=? AND conversation_id=? AND owner=? "
                         "AND status IN ('queued','running')",
                         (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                          error, time.time(), run_id, *self.scope(context), conversation_id, self.owner))
