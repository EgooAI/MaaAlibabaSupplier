"""Persists scoped image bytes atomically with metadata in an app-owned table."""

import hashlib
import json
import re
import time

from backend.app.api.envelope import AppError
from backend.app.shared.crm.outbox_store import OutboxStore, OutboxConflict, _scope


class ImageAttachmentStore:
    def __init__(self, database_path=None):
        self.store = OutboxStore(database_path)

    @staticmethod
    def validate_id(identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise AppError("图片附件标识无效。", status_code=422)

    def put(self, identifier, *, seller, data_dir, conversation_id, data, metadata):
        self.validate_id(identifier)
        scope = _scope(seller, data_dir)
        self.store.initialize()
        with self.store._connection(write=True) as conn:
            old = conn.execute("SELECT * FROM app_image_attachment WHERE id=?", (identifier,)).fetchone()
            if old is not None:
                if ((old["seller"], old["data_dir"], old["conversation_id"]) != (*scope, conversation_id)
                        or json.loads(old["metadata"])["original_sha256"] != metadata["original_sha256"]):
                    raise OutboxConflict("图片附件标识已属于其他内容或会话。")
                if old["data"] is not None:
                    return {"id": identifier, **json.loads(old["metadata"])}
                if json.loads(old["metadata"]) != metadata:
                    raise OutboxConflict("图片标准化结果已变化，请重新选择图片。")
                conn.execute("UPDATE app_image_attachment SET data=?, created_at=? WHERE id=?", (data, time.time(), identifier))
            else:
                conn.execute("INSERT INTO app_image_attachment VALUES (?,?,?,?,?,?,?)",
                             (identifier, *scope, conversation_id, json.dumps(metadata), data, time.time()))
            self.expire(conn)
        return {"id": identifier, **metadata}

    @staticmethod
    def expire(conn):
        # Keeps immutable identity tombstones and every attachment referenced by a task.
        conn.execute("UPDATE app_image_attachment SET data=NULL WHERE data IS NOT NULL AND created_at<? "
                     "AND NOT EXISTS (SELECT 1 FROM app_outbox WHERE attachment_id=app_image_attachment.id)",
                     (time.time() - 86400,))

    def get(self, identifier, *, seller, data_dir, conversation_id, include_data=False):
        self.validate_id(identifier)
        if not self.store.database_path.is_file():
            raise AppError("图片附件不存在，请重新上传。", status_code=404)
        with self.store._connection() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_image_attachment'").fetchone():
                raise AppError("图片附件不存在，请重新上传。", status_code=404)
            row = conn.execute("SELECT * FROM app_image_attachment WHERE id=? AND seller=? AND data_dir=? AND conversation_id=?",
                               (identifier, *_scope(seller, data_dir), conversation_id)).fetchone()
        if row is None:
            raise AppError("图片附件不存在或不属于当前会话。", status_code=404)
        if row["data"] is None:
            raise AppError("图片附件已过期，请重新上传。", status_code=410)
        metadata = json.loads(row["metadata"])
        if hashlib.sha256(row["data"]).hexdigest() != metadata["sha256"]:
            raise AppError("图片附件已损坏，请重新选择图片。", status_code=409)
        result = {"id": identifier, **metadata}
        return (result, bytes(row["data"])) if include_data else result
