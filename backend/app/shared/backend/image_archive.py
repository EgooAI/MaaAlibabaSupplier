"""Archives platform images through the generic CRM resource store."""

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import hashlib
import json
import sqlite3
from threading import RLock
import time

from backend.app.background import get_background_runtime, WorkBlocked
from backend.app.api.envelope import AppError
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.image_fetch import fetch_image, ImageFetchError, ImageUnavailable, MAX_BYTES
from backend.app.shared.crm.image_attachment_store import ImageAttachmentStore
from backend.app.shared.crm.image_resources import image_attributes, image_namespace, TEMPORARY_SECONDS
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.crm.message_image_store import MessageImageStore
from backend.app.shared.crm.identities import session_key_prefix
from backend.app.shared.crm.sdk import ResourceUnavailable
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.crm_time import coerce_epoch


class ImageArchive:
    def __init__(self, database_path=None):
        self.store = MessageImageStore(database_path)
        self._lock = RLock()
        self._downloads = {}
        self._loads = {}
        self._jobs = {}
        self._worker = None
        self._pool = None
        self._last_clean = 0.0
        self._stopped = False
        self._migration_done = False

    def _single(self, flights, key, operation):
        with self._lock:
            leader = key not in flights
            if leader:
                try:
                    activity = get_background_runtime().activity()
                except WorkBlocked as exc:
                    raise ImageFetchError("installation_in_progress") from exc
                flights[key] = Future()
            future = flights[key]
        if not leader:
            try:
                return future.result(timeout=90)
            except FutureTimeoutError as exc:
                raise ImageFetchError("fetch_timeout") from exc
        try:
            result = operation()
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                flights.pop(key, None)
            activity.close()

    def fetch_resource(self, context, external_mid, source):
        """Coalesces verifier and display downloads for the same scoped source."""
        key = (context.self_ali_id, canonical_source_dir(context.data_dir), external_mid,
               *(source.get(name) for name in ("url", "md5", "bytes", "width", "height", "sender_id", "cid")),
               coerce_epoch(source.get("created_at")))
        owner_id = hashlib.sha256(json.dumps(key).encode()).hexdigest()
        owner = {"namespace": image_namespace(context.self_ali_id, context.data_dir), "owner_type": "image-source", "owner_id": owner_id}
        def download():
            saved = self.store.resources.find_reference(**owner)
            if saved:
                try:
                    return self.store.resources.read_reference(saved.reference_id, namespace=owner["namespace"])
                except ResourceUnavailable:
                    pass
            image = fetch_image(source["url"])
            try:
                attributes = image_attributes(image.body)
            except ValueError as exc:
                raise ImageFetchError("invalid_image") from exc
            self.store.resources.initialize()
            resource = self.store.resources.put(image.body, media_type=image.content_type, attributes=attributes,
                retain_until=time.time() + TEMPORARY_SECONDS, max_bytes=MAX_BYTES)
            self.store.resources.link(resource.resource_id, **owner, expires_at=time.time() + TEMPORARY_SECONDS,
                expected_resource_id=saved.resource_id if saved else None, retain_previous_until=time.time() + TEMPORARY_SECONDS)
            return resource, image.body
        return self._single(self._downloads, key, download)

    def _observed_attachment(self, record):
        """Reuses only a uniquely observed task with matching immutable source evidence."""
        from backend.app.shared.crm.outbox_store import _record
        from backend.app.shared.backend.send_verification import content_evidence

        with self.store.resources.transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_outbox'").fetchone():
                return None
            conn.row_factory = sqlite3.Row
            source_id = record["external_mid"].removeprefix(session_key_prefix(record["seller"]))
            row = conn.execute("""SELECT * FROM app_outbox WHERE seller=? AND data_dir=? AND conversation_id=?
                AND matched_message_id=? AND status='observed' AND message_kind='image'""",
                (record["seller"], record["data_dir"], record["sid"], source_id)).fetchone()
        if row is None:
            return None
        task = _record(row)
        source = json.loads(record["source"])
        proof = (task.get("evidence") or {}).get("message", {})
        if (any(proof.get(key) != source.get(key) for key in ("sender_id", "cid"))
                or coerce_epoch(proof.get("created_at")) != coerce_epoch(source.get("created_at"))
                or any((proof.get("image") or {}).get(key) != source.get(key) for key in ("width", "height", "md5", "bytes"))
                or not content_evidence(task, proof)):
            return None
        try:
            metadata, data = ImageAttachmentStore(self.store.database_path).get(task["attachment_id"],
                seller=record["seller"], data_dir=record["data_dir"], conversation_id=record["sid"], include_data=True)
            resource = self.store.resources.get(metadata.get("resource_id", "sha256:" + metadata["sha256"]))
            return (resource, data) if resource else None
        except (ResourceUnavailable, ValueError, AppError):
            return None

    def load(self, context, external_mid):
        record = self.store.get(context, external_mid)
        if record is None or not json.loads(record["source"]).get("url"):
            raise ImageUnavailable("missing_source")
        local = self.store.local(record)
        if local:
            return local
        key = (context.self_ali_id, context.data_dir, external_mid, record["fingerprint"])
        def resolve():
            # Rechecks after competing requests have completed their local publication.
            current = self.store.get(context, external_mid)
            if current is None or current["fingerprint"] != record["fingerprint"]:
                raise ImageFetchError("source_changed")
            saved = self.store.local(current)
            if saved:
                return saved
            if not self.store.claim(record):
                raise ImageFetchError("archive_in_progress")
            try:
                result = self._observed_attachment(record) or self.fetch_resource(context, external_mid, json.loads(record["source"]))
                if not self.store.complete(record, result[0].resource_id):
                    raise ImageFetchError("source_changed")
                return result
            except ImageUnavailable:
                self.store.failed(record, "unavailable", unavailable=True)
                raise
            except Exception as exc:
                self.store.failed(record, type(exc).__name__)
                raise
        return self._single(self._loads, key, resolve)

    def start(self):
        if self._worker:
            return self
        self.store.initialize()
        self._stopped = False
        runtime = get_background_runtime()
        self._pool = runtime.pool("image-archive-download", max_workers=2)
        with self.store.resources.transaction(write=True) as conn:
            conn.execute("UPDATE app_message_image SET status='retry_wait',lease_until=0,retry_at=0 WHERE status='fetching'")
        self._worker = runtime.schedule("image-archive", self.tick, interval=5, resident=True).start()
        runtime.on_shutdown("image-archive", self.stop, priority=15)
        return self

    def tick(self):
        if self._stopped:
            return
        context = get_account_context()
        with self._lock:
            self._jobs = {key: future for key, future in self._jobs.items() if not future.done()}
            free = 2 - len(self._jobs)
        if context.self_ali_id and context.data_dir and free:
            for mid in self.store.pending(context, limit=free):
                key = (context.self_ali_id, context.data_dir, mid)
                with self._lock:
                    if key not in self._jobs and not self._stopped:
                        self._jobs[key] = self._pool.submit(self._background_load, context, mid)
        # Batches migrations so startup need not block on every historical attachment.
        attachments = ImageAttachmentStore(self.store.database_path)
        if not self._migration_done:
            self._migration_done = attachments.migrate() == 0
        if time.monotonic() - self._last_clean >= 3600:
            with attachments.store._connection(write=True) as conn:
                attachments.expire(conn)
            self.store.resources.collect_unreferenced(now=time.time(), orphan_grace=TEMPORARY_SECONDS)
            self._last_clean = time.monotonic()

    def _background_load(self, context, mid):
        if self._stopped or get_account_context() != context:
            return
        try:
            self.load(context, mid)
        except Exception as exc:
            log_event("image.fetch_failed", reason=type(exc).__name__)

    def stop(self):
        self._stopped = True
        if self._worker:
            self._worker.stop(timeout=60)
            self._worker = None
        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None


_archives = {}
_archives_lock = RLock()


def get_image_archive(database_path=None):
    store = MessageImageStore(database_path)
    with _archives_lock:
        if store.database_path not in _archives:
            _archives[store.database_path] = ImageArchive(store.database_path)
        return _archives[store.database_path]


def trigger_image_archive():
    with _archives_lock:
        workers = [archive._worker for archive in _archives.values() if archive._worker and not archive._stopped]
    for worker in workers:
        worker.trigger()


def start_image_archive():
    return get_image_archive().start()


def stop_image_archives():
    with _archives_lock:
        archives = list(_archives.values())
        _archives.clear()
    for archive in archives:
        archive.stop()
