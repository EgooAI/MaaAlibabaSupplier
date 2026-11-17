"""Exercises durable image workflows with temporary CRM databases and bounded I/O."""

from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.app.background import get_background_runtime
from backend.app.shared.backend import account_context, image_archive
from backend.app.shared.backend.image_fetch import FetchedImage, ImageFetchError
from backend.app.shared.crm import message_image_store
from backend.app.shared.crm.image_attachment_store import ImageAttachmentStore
from backend.app.shared.crm.image_resources import image_namespace
from backend.app.shared.crm.resource_backup import backup_resources, verify_resource_backup
from backend.app.shared.image_content import prepare_image
from backend.app.shared.utils import app_config
from backend.tests.test_connection_api import client, sdk
from backend.tests.test_inbox_api import archive
from backend.tests.test_message_images import image_row, PNG, SIGNED_URL
from backend.app.shared.crm.identities import message_external_id


def mid(value):
    return message_external_id("seller-a", "messages", str(value))


def test_new_images_archive_without_a_page_and_survive_restart(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    assert service.store.get(context, mid(1))["status"] == "remote"
    apply(("buyer", [image_row(1), image_row(2)]))
    assert service.store.pending(context) == [mid(2)]
    calls = []
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: (calls.append(url), FetchedImage("image/png", PNG))[1])
    service._pool = get_background_runtime().pool("test-image-download", max_workers=2)
    service.tick()
    for future in list(service._jobs.values()):
        future.result(5)
    assert service.store.get(context, mid(2))["status"] == "ready"
    assert service.store.get(context, mid(1))["status"] == "remote"
    assert calls == [SIGNED_URL]
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: pytest.fail("Archived bytes must not use the platform"))
    restarted = image_archive.ImageArchive(service.store.database_path)
    resource, body = restarted.load(context, mid(2))
    assert body == PNG
    response = client.get(f"/api/images/{resource.resource_id}")
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["X-Account-Epoch"] == context.epoch
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(f"/api/images/{resource.resource_id}", headers={"X-Account-Epoch": "stale"}).status_code == 409
    app_config.write_app_config({"self_ali_id": "other", "alibaba_data_dir": context.data_dir})
    other = account_context.get_account_context()
    assert client.get(f"/api/images/{resource.resource_id}", headers={"X-Account-Epoch": other.epoch}).status_code == 404


def test_sync_rollback_does_not_publish_an_image_source_or_pending_work(client, archive, monkeypatch):
    from backend.app.shared.crm import sync
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    def fail(*args):
        raise RuntimeError("commit boundary")
    monkeypatch.setattr(sync, "write_sync_state", fail)
    with pytest.raises(RuntimeError):
        apply(("buyer", [image_row(1), image_row(2)]))
    assert service.store.get(context, mid(2)) is None
    assert service.store.pending(context) == []


def test_foreground_requests_share_an_inflight_download(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    started, joined, finish = Event(), Event(), Event()
    class JoinedFuture(Future):
        def result(self, timeout=None):
            joined.set()
            return super().result(timeout)
    monkeypatch.setattr(image_archive, "Future", JoinedFuture)
    calls = []
    def fetch(url):
        calls.append(url)
        assert not get_background_runtime().begin_install(idle_for=0)
        started.set()
        assert finish.wait(5)
        return FetchedImage("image/png", PNG)
    monkeypatch.setattr(image_archive, "fetch_image", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.load, context, mid(1))
        try:
            assert started.wait(5)
            second = pool.submit(service.load, context, mid(1))
            assert joined.wait(5)
        finally:
            finish.set()
        assert first.result(5)[0].resource_id == second.result(5)[0].resource_id
    assert calls == [SIGNED_URL]


def test_expired_download_cannot_finish_a_new_lease(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    old = service.store.get(context, mid(1))
    clock = [1000.0]
    monkeypatch.setattr(message_image_store, "time", SimpleNamespace(time=lambda: clock[0]))
    assert service.store.claim(old)
    clock[0] += 121
    newer = service.store.get(context, mid(1))
    assert service.store.claim(newer)
    resource = service.store.resources.put(PNG, media_type="image/png")
    assert not service.store.complete(old, resource.resource_id)
    service.store.failed(old, "late_failure")
    assert service.store.get(context, mid(1))["status"] == "fetching"
    assert service.store.complete(newer, resource.resource_id)


@pytest.mark.parametrize("change", ["source", "account"])
def test_download_retains_its_original_scope_and_source_version(client, archive, monkeypatch, change):
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    started, finish = Event(), Event()
    def fetch(url):
        started.set()
        assert finish.wait(5)
        return FetchedImage("image/png", PNG)
    monkeypatch.setattr(image_archive, "fetch_image", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(service.load, context, mid(1))
        try:
            assert started.wait(5)
            if change == "source":
                apply(("buyer", [image_row(1, payload={"url": SIGNED_URL + "-new", "width": 3, "height": 2})]))
            else:
                app_config.write_app_config({"self_ali_id": "other", "alibaba_data_dir": context.data_dir})
        finally:
            finish.set()
        if change == "source":
            with pytest.raises(ImageFetchError, match="source_changed"):
                work.result(5)
            current = service.store.get(context, mid(1))
            assert current["resource_ref_id"] is None and current["status"] == "pending"
        else:
            assert work.result(5)[1] == PNG
            assert service.store.get(context, mid(1))["status"] == "ready"
            assert service.store.get(account_context.get_account_context(), mid(1)) is None


def test_fetch_failure_is_retryable_without_losing_the_message(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", []))
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    clock = [1000.0]
    monkeypatch.setattr(message_image_store, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: (_ for _ in ()).throw(ImageFetchError("empty")))
    with pytest.raises(ImageFetchError):
        service.load(context, mid(1))
    assert service.store.get(context, mid(1))["status"] == "retry_wait"
    assert service.store.pending(context) == []
    clock[0] += 16
    assert service.store.pending(context) == [mid(1)]
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: FetchedImage("image/png", PNG))
    assert service.load(context, mid(1))[1] == PNG
    assert service.store.get(context, mid(1))["status"] == "ready"


@pytest.mark.parametrize("animated", [False, True])
def test_archiving_preserves_readable_images_outside_send_rules(client, archive, monkeypatch, animated):
    stream = BytesIO()
    with Image.new("RGB", (9000, 2), "red") as first:
        if animated:
            with Image.new("RGB", first.size, "blue") as second:
                first.save(stream, format="GIF", save_all=True, append_images=[second])
        else:
            first.save(stream, format="PNG")
    raw = stream.getvalue()
    media = "image/gif" if animated else "image/png"
    _, apply = archive
    apply(("buyer", [image_row(1, payload={"url": SIGNED_URL, "width": 9000, "height": 2})]))
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: FetchedImage(media, raw))
    resource, body = image_archive.get_image_archive().load(account_context.get_account_context(), mid(1))
    assert body == raw and resource.media_type == media
    assert "pixel_sha256" not in resource.attributes["image"]


def test_attachment_migration_reference_rollback_and_backup_restore(tmp_path):
    store = ImageAttachmentStore(tmp_path / "crm.sqlite")
    store.store.initialize()
    png, metadata = prepare_image(PNG)
    identifier = "b" * 32
    scope = {"seller": "seller", "data_dir": str(tmp_path / "source"), "conversation_id": 1}
    # Builds a real pre-resource BLOB row; migration must retain its exact bytes and ID.
    import time
    with store.store._connection(write=True) as conn:
        from backend.app.shared.crm.outbox_store import _scope
        conn.execute("INSERT INTO app_image_attachment (id,seller,data_dir,conversation_id,metadata,data,created_at) VALUES (?,?,?,?,?,?,?)",
            (identifier, *_scope(scope["seller"], scope["data_dir"]), 1, json.dumps(metadata), png, time.time()))
    assert store.migrate(release_legacy=True) == 1
    assert store.migrate(release_legacy=True) == 0
    migrated, body = store.get(identifier, **scope, include_data=True)
    assert body == png and migrated["resource_id"] == "sha256:" + metadata["sha256"]
    with store.store._connection(write=True) as conn:
        conn.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON app_outbox_events BEGIN SELECT RAISE(ABORT,'audit'); END")
        ref_id, blob = conn.execute("SELECT resource_ref_id,data FROM app_image_attachment").fetchone()
        assert blob is None
    before = store.resources.get_reference(ref_id)
    with pytest.raises(sqlite3.IntegrityError):
        store.store.create(**scope, contact_ali_id="buyer", login_id="buyer", content="", action="send",
                           idempotency_key="test", message_kind="image", attachment_id=identifier)
    assert store.resources.get_reference(ref_id).expires_at == before.expires_at
    with store.store._connection(write=True) as conn:
        conn.execute("DROP TRIGGER reject_audit")
    task, _ = store.store.create(**scope, contact_ali_id="buyer", login_id="buyer", content="", action="send",
                                idempotency_key="test", message_kind="image", attachment_id=identifier)
    assert store.resources.get_reference(ref_id).expires_at is None
    backup = tmp_path / "backup"
    result = backup_resources(store.store.database_path, backup)
    assert result["resources"] == verify_resource_backup(backup) == 1
    store.resources.path(migrated["resource_id"]).unlink()
    restored = ImageAttachmentStore(result["database"])
    assert restored.get(identifier, **scope, include_data=True)[1] == png
    assert restored.store.get(task["id"], seller=scope["seller"], data_dir=scope["data_dir"])["idempotency_key"] == "test"


def test_expired_upload_does_not_remove_a_message_copy(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [image_row(1)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    png, metadata = prepare_image(PNG)
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: FetchedImage("image/png", png))
    resource, _ = service.load(context, mid(1))
    sid = service.store.get(context, mid(1))["sid"]
    attachments = ImageAttachmentStore(service.store.database_path)
    attachments.put("a" * 32, seller=context.self_ali_id, data_dir=context.data_dir, conversation_id=sid, data=png, metadata=metadata)
    ref = next(ref for ref in attachments.resources.list_references(namespace=image_namespace(context.self_ali_id, context.data_dir)) if ref.owner_type == "attachment")
    attachments.resources.collect_unreferenced(now=ref.expires_at + 1, orphan_grace=86400)
    assert service.load(context, mid(1))[1] == png
    assert client.get(f"/api/images/{resource.resource_id}").status_code == 200


def test_committed_observed_message_reuses_its_exact_send_resource(client, archive, monkeypatch):
    _, apply = archive
    png, metadata = prepare_image(PNG)
    source = {"url": SIGNED_URL, "md5": metadata["md5"], "size": metadata["bytes"],
              "width": metadata["width"], "height": metadata["height"]}
    apply(("buyer", [image_row(1, sender="seller-a", payload=source)]))
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    sid = service.store.get(context, mid(1))["sid"]
    attachments = ImageAttachmentStore(service.store.database_path)
    scope = {"seller": context.self_ali_id, "data_dir": context.data_dir}
    attachments.put("c" * 32, **scope, conversation_id=sid, data=png, metadata=metadata)
    task, _ = attachments.store.create(**scope, conversation_id=sid, contact_ali_id="buyer", login_id="buyer", content="",
        action="send", idempotency_key="send", message_kind="image", attachment_id="c" * 32)
    attachments.store.transition(task["id"], "queued", **scope, status="navigating")
    attachments.store.transition(task["id"], "navigating", **scope, status="running")
    attachments.store.transition(task["id"], "running", **scope, status="verifying", may_have_sent=True)
    proof = {"kind": "local_source_message", "message": {"id": "messages:1", "type": 7,
        "sender_id": "seller-a@icbu", "cid": "seller-a-buyer", "created_at": 1756720000, "image": {
            key: metadata[key] for key in ("width", "height", "md5", "bytes")}}}
    attachments.store.transition(task["id"], "verifying", **scope, status="observed", matched_message_id="messages:1", evidence=proof)
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: pytest.fail("Observed exact content is already available"))
    resource, result = service.load(context, mid(1))
    assert result == png and resource.resource_id == "sha256:" + metadata["sha256"]
    assert service.store.get(context, mid(1))["status"] == "ready"


def test_verifier_temporary_download_is_reused_after_commit_but_not_public_before_it(client, archive, monkeypatch):
    _, apply = archive
    context = account_context.get_account_context()
    service = image_archive.get_image_archive()
    source = {"url": SIGNED_URL, "width": 3, "height": 2, "md5": "a" * 32, "bytes": 999,
              "sender_id": "buyer@icbu", "cid": "seller-a-buyer", "created_at": 1756720000}
    calls = []
    monkeypatch.setattr(image_archive, "fetch_image", lambda url: (calls.append(url), FetchedImage("image/png", PNG))[1])
    resource, _ = service.fetch_resource(context, mid(1), source)
    assert client.get(f"/api/images/{resource.resource_id}").status_code == 404
    apply(("buyer", [image_row(1, payload={**source, "size": source["bytes"]})]))
    restarted = image_archive.ImageArchive(service.store.database_path)
    assert restarted.load(context, mid(1))[1] == PNG
    assert calls == [SIGNED_URL]
    assert client.get(f"/api/images/{resource.resource_id}").status_code == 200
