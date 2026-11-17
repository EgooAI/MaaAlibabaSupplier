"""Binds scoped image attachments to generic CRM resources and migrates old BLOBs."""

import hashlib
import json
import re
import time

from backend.app.api.envelope import AppError
from backend.app.shared.crm.outbox_store import OutboxStore, OutboxConflict, _scope
from backend.app.shared.crm.sdk import ResourceManager, ResourceUnavailable
from backend.app.shared.crm.image_resources import image_namespace, TEMPORARY_SECONDS


class ImageAttachmentStore:
    def __init__(self, database_path=None):
        self.store = OutboxStore(database_path)
        self.resources = ResourceManager(self.store.database_path)

    @staticmethod
    def validate_id(identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise AppError("图片附件标识无效。", status_code=422)

    def put(self, identifier, *, seller, data_dir, conversation_id, data, metadata):
        self.validate_id(identifier)
        scope = _scope(seller, data_dir)
        self.store.initialize()
        resource = self.resources.put(data, media_type="image/png", attributes={"image": {
            **{key: metadata[key] for key in ("width", "height", "pixel_sha256")},
            "frames": 1, "pixel_id": "rgba-v1-sha256:" + metadata["pixel_sha256"],
        }}, retain_until=time.time() + TEMPORARY_SECONDS)
        with self.store._connection(write=True) as conn:
            old = conn.execute("SELECT * FROM app_image_attachment WHERE id=?", (identifier,)).fetchone()
            if old is not None:
                if ((old["seller"], old["data_dir"], old["conversation_id"]) != (*scope, conversation_id)
                        or json.loads(old["metadata"])["original_sha256"] != metadata["original_sha256"]):
                    raise OutboxConflict("图片附件标识已属于其他内容或会话。")
                if json.loads(old["metadata"]) != metadata:
                    raise OutboxConflict("图片标准化结果已变化，请重新选择图片。")
                if old["resource_ref_id"]:
                    try:
                        resource, _ = self.resources.read_reference(old["resource_ref_id"], namespace=image_namespace(*scope), transaction=conn)
                    except ResourceUnavailable:
                        pass
                    else:
                        return {"id": identifier, **metadata, "resource_id": resource.resource_id}
                conn.execute("UPDATE app_image_attachment SET created_at=? WHERE id=?", (time.time(), identifier))
            else:
                conn.execute("INSERT INTO app_image_attachment (id,seller,data_dir,conversation_id,metadata,data,created_at) VALUES (?,?,?,?,?,NULL,?)",
                             (identifier, *scope, conversation_id, json.dumps(metadata), time.time()))
            self._bind(identifier, resource.resource_id, conn)
            self.expire(conn)
        return {"id": identifier, **metadata, "resource_id": resource.resource_id}

    def _bind(self, identifier, resource_id, conn):
        row = conn.execute("SELECT * FROM app_image_attachment WHERE id=?", (identifier,)).fetchone()
        pinned = conn.execute("SELECT 1 FROM app_outbox WHERE attachment_id=?", (identifier,)).fetchone()
        ref = self.resources.link(resource_id, namespace=image_namespace(row["seller"], row["data_dir"]),
            owner_type="attachment", owner_id=identifier,
            expires_at=None if pinned else row["created_at"] + TEMPORARY_SECONDS, transaction=conn)
        conn.execute("UPDATE app_image_attachment SET resource_ref_id=? WHERE id=?", (ref.reference_id, identifier))
        return ref

    def _migrate_row(self, row, conn):
        if row["resource_ref_id"] or row["data"] is None:
            return
        metadata = json.loads(row["metadata"])
        if hashlib.sha256(row["data"]).hexdigest() != metadata["sha256"]:
            raise AppError("图片附件已损坏，无法迁移。", status_code=409)
        resource = self.resources.put(bytes(row["data"]), media_type="image/png", attributes={"image": {
            **{key: metadata[key] for key in ("width", "height", "pixel_sha256")},
            "frames": 1, "pixel_id": "rgba-v1-sha256:" + metadata["pixel_sha256"],
        }}, retain_until=time.time() + TEMPORARY_SECONDS, transaction=conn)
        self._bind(row["id"], resource.resource_id, conn)

    def migrate(self, *, limit=50, release_legacy=False):
        """Migrates one restartable batch, releasing legacy bytes only on request."""
        self.store.initialize()
        with self.store._connection(write=True) as conn:
            rows = conn.execute("SELECT * FROM app_image_attachment WHERE data IS NOT NULL" +
                ("" if release_legacy else " AND resource_ref_id IS NULL") + " LIMIT ?", (limit,)).fetchall()
            for row in rows:
                self._migrate_row(row, conn)
                if release_legacy:
                    current = conn.execute("SELECT resource_ref_id FROM app_image_attachment WHERE id=?", (row["id"],)).fetchone()
                    ref = self.resources.get_reference(current[0], transaction=conn)
                    with self.resources.open(ref.resource_id, transaction=conn):
                        pass
                    conn.execute("UPDATE app_image_attachment SET data=NULL WHERE id=?", (row["id"],))
            return len(rows)

    def pin(self, identifier, *, seller, data_dir, conversation_id, conn):
        """Promotes an attachment lease in the caller's Outbox creation transaction."""
        row = conn.execute("SELECT * FROM app_image_attachment WHERE id=? AND seller=? AND data_dir=? AND conversation_id=?",
                           (identifier, *_scope(seller, data_dir), conversation_id)).fetchone()
        if row is None:
            raise AppError("图片附件不存在，请重新上传。", status_code=422)
        self._migrate_row(row, conn)
        row = conn.execute("SELECT * FROM app_image_attachment WHERE id=?", (identifier,)).fetchone()
        try:
            resource, _ = self.resources.read_reference(row["resource_ref_id"], namespace=image_namespace(seller, data_dir), transaction=conn)
        except ResourceUnavailable as exc:
            raise AppError("图片附件不存在、已过期或已损坏，请重新上传。", status_code=422) from exc
        metadata = json.loads(row["metadata"])
        if resource.resource_id != "sha256:" + metadata["sha256"]:
            raise AppError("图片附件摘要不一致。", status_code=409)
        self.resources.link(resource.resource_id, namespace=image_namespace(seller, data_dir),
                            owner_type="attachment", owner_id=identifier, transaction=conn)
        return metadata

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
        metadata = json.loads(row["metadata"])
        resource_id = None
        if "resource_ref_id" in row.keys() and row["resource_ref_id"]:
            try:
                resource, data = self.resources.read_reference(row["resource_ref_id"], namespace=image_namespace(seller, data_dir))
            except ResourceUnavailable as exc:
                raise AppError("图片附件不可用，请重新上传。", status_code=410) from exc
            resource_id = resource.resource_id
        else:
            data = row["data"]
        if data is None:
            raise AppError("图片附件已过期，请重新上传。", status_code=410)
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise AppError("图片附件已损坏，请重新选择图片。", status_code=409)
        result = {"id": identifier, **metadata, **({"resource_id": resource_id} if resource_id else {})}
        return (result, bytes(data)) if include_data else result
