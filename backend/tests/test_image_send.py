"""Exercises image identity, durable attachment admission and uncertain sends."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from threading import Event
from types import SimpleNamespace
import hashlib
import time

import pytest
from PIL import Image

from backend.app.api.envelope import AppError
from backend.app.api.routers import conversations
from backend.app.shared import image_content
from backend.app.shared.backend import account_context, image_verification, image_archive
from backend.app.shared.backend.send_verification import match_outbox
from backend.app.shared.crm.image_attachment_store import ImageAttachmentStore
from backend.app.shared.image_clipboard import image_payload, dib_identity, CF_DIBV5
from backend.tests.test_connection_api import client, chat, sdk
from backend.tests.test_outbox_api import outbox
from backend.tests.test_outbox_service import env
from backend.tests.test_send_verification import case


def picture(format="PNG", mode="RGBA", size=(9, 5), color=(10, 20, 30, 125), **options):
    stream = BytesIO()
    with Image.new(mode, size, color) as image:
        image.putpixel((1, 2), (201, 17, 92, 0) if mode == "RGBA" else (201, 17, 92))
        image.save(stream, format=format, **options)
    return stream.getvalue()


@pytest.mark.parametrize("format,mode", [("PNG", "RGBA"), ("JPEG", "RGB"), ("WEBP", "RGBA")])
def test_normalization_preserves_pixels_and_both_file_identities(format, mode):
    raw = picture(format, mode, color=(10, 20, 30) if mode == "RGB" else (10, 20, 30, 125))
    png, metadata = image_content.prepare_image(raw)
    assert image_content.decoded_identity(png) == image_content.decoded_identity(raw)
    assert metadata["md5"] == hashlib.md5(png).hexdigest()
    assert metadata["sha256"] == hashlib.sha256(png).hexdigest()
    assert metadata["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["bytes"] == len(png)


def test_orientation_is_applied_before_preview_and_clipboard():
    raw = picture("JPEG", "RGB", color=(10, 20, 30))
    with Image.open(BytesIO(raw)) as image:
        exif = image.getexif()
        exif[274] = 6
        stream = BytesIO()
        image.save(stream, format="JPEG", exif=exif)
    png, metadata = image_content.prepare_image(stream.getvalue())
    assert (metadata["width"], metadata["height"]) == (5, 9)
    with Image.open(BytesIO(png)) as image:
        assert not image.getexif().get(274)


def test_byte_and_pixel_limits_are_independent(monkeypatch):
    raw = picture()
    padded = raw + b"\0" * (image_content.MAX_IMAGE_BYTES - len(raw))
    assert image_content.prepare_image(padded)[1]["width"] == 9
    with pytest.raises(ValueError, match="10 MB"):
        image_content.prepare_image(padded + b"\0")
    monkeypatch.setattr(image_content, "MAX_IMAGE_PIXELS", 44)
    with pytest.raises(ValueError, match="总像素"):
        image_content.prepare_image(raw)
    monkeypatch.setattr(image_content, "MAX_IMAGE_PIXELS", 45)
    monkeypatch.setattr(image_content, "MAX_IMAGE_SIDE", 8)
    with pytest.raises(ValueError, match="最长边"):
        image_content.prepare_image(raw)


def test_malformed_and_animated_images_are_not_silently_flattened():
    for raw in (b"", b"not an image", picture("BMP")):
        with pytest.raises(ValueError):
            image_content.prepare_image(raw)
    stream = BytesIO()
    with Image.new("RGBA", (2, 2), "red") as first, Image.new("RGBA", (2, 2), "blue") as second:
        first.save(stream, format="PNG", save_all=True, append_images=[second])
    with pytest.raises(ValueError, match="静态"):
        image_content.prepare_image(stream.getvalue())


def test_png_and_dibv5_roundtrip_retains_orientation_and_transparency():
    png, identity = image_content.prepare_image(picture())
    payload = image_payload(png)
    wire = dict(payload.formats)
    assert dib_identity(wire[CF_DIBV5])["pixel_sha256"] == identity["pixel_sha256"]
    assert payload.readbacks[0].matches(wire["PNG"])
    assert payload.readbacks[1].matches(wire["PNG"])
    assert payload.readbacks[2].matches(wire[CF_DIBV5])
    wrong = picture(color=(30, 20, 10, 125))
    assert not payload.readbacks[0].matches(wrong)
    corrupted = bytearray(wire[CF_DIBV5]); corrupted[-1] ^= 1
    assert not payload.readbacks[2].matches(bytes(corrupted))


def attach(env, *, identifier="a" * 32):
    png, metadata = image_content.prepare_image(picture())
    store = ImageAttachmentStore(env.service.store.database_path)
    scope = {"seller": env.context.self_ali_id, "data_dir": env.context.data_dir, "conversation_id": 1}
    store.put(identifier, **scope, data=png, metadata=metadata)
    return store, scope, metadata


def test_attachment_conflict_scope_expiry_and_atomic_binding(env):
    attachments, scope, metadata = attach(env)
    data = attachments.get("a" * 32, **scope, include_data=True)[1]
    assert attachments.put("a" * 32, **scope, data=data, metadata=metadata)["sha256"] == metadata["sha256"]
    for changes in ({"seller": "other"}, {"conversation_id": 2}, {"data_dir": scope["data_dir"] + "-other"}):
        with pytest.raises(AppError):
            attachments.get("a" * 32, **{**scope, **changes})
    with pytest.raises(AppError):
        attachments.put("a" * 32, **scope, data=data, metadata={**metadata, "original_sha256": "other"})
    task = env.service.submit(env.context, 1, "buyer", "buyer-login", "", "send", "image-key", message_kind="image", attachment_id="a" * 32)
    attach(env, identifier="b" * 32)
    with attachments.store._connection(write=True) as conn:
        conn.execute("UPDATE app_image_attachment SET created_at=0")
        from backend.app.shared.crm.image_resources import image_namespace
        attachments.resources.link("sha256:" + metadata["sha256"], namespace=image_namespace(scope["seller"], scope["data_dir"]),
            owner_type="attachment", owner_id="b" * 32, expires_at=0, transaction=conn)
        attachments.expire(conn)
    assert attachments.get("a" * 32, **scope)["sha256"] == metadata["sha256"]
    with pytest.raises(AppError) as expired:
        attachments.get("b" * 32, **scope)
    assert expired.value.status_code == 410
    assert env.service.submit(env.context, 1, "buyer", "buyer-login", "", "send", "image-key", message_kind="image", attachment_id="a" * 32)["id"] == task["id"]
    assert len(env.queue.jobs) == 1


@pytest.mark.parametrize("action", ["send", "test"])
def test_image_task_checks_barrier_and_recovers_without_replaying(env, monkeypatch, action):
    attach(env)
    task = env.service.submit(env.context, 1, "buyer", "buyer-login", "", action, "image-key", message_kind="image", attachment_id="a" * 32)
    from backend.app.shared.backend import outbox_service
    monkeypatch.setattr(outbox_service.runner, "chat_image", lambda record: (env.calls.append("image") is None, "filled"))
    def send():
        current = env.service.get(env.context, task["id"])
        assert current["may_have_sent"] and current["baseline"] and current["image"]["sha256"]
        env.calls.append("image-send")
        return env.send_job
    monkeypatch.setattr(outbox_service.runner, "submit_send", send)
    assert env.queue.run()[0]
    assert "image" in env.calls and "input" not in env.calls
    result = env.service.get(env.context, task["id"])
    assert result["status"] == ("verifying" if action == "send" else "filled")
    assert ("image-send" in env.calls) is (action == "send")
    env.service.store.recover()
    recovered = env.service.get(env.context, task["id"])
    assert recovered["status"] == ("unknown" if action == "send" else "filled")
    if action == "send":
        with pytest.raises(AppError):
            env.service.retry(env.context, task["id"], version=recovered["version"])
    assert env.queue.jobs == []


def test_missing_image_prevents_gui_but_does_not_destroy_lost_receipt(env):
    attachments, _, _ = attach(env)
    task = env.service.submit(env.context, 1, "buyer", "buyer-login", "", "send", "image-key", message_kind="image", attachment_id="a" * 32)
    metadata = env.service.get(env.context, task["id"])["image"]
    attachments.resources.path("sha256:" + metadata["sha256"]).unlink()
    assert env.service.submit(env.context, 1, "buyer", "buyer-login", "", "send", "image-key", message_kind="image", attachment_id="a" * 32)["id"] == task["id"]
    assert not env.queue.run()[0] and env.calls == []
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "failed" and not result["may_have_sent"]


def image_case(case):
    record, snapshot = deepcopy(case)
    png, identity = image_content.prepare_image(picture())
    record.update(message_kind="image", image=identity, content="", attachment_id="a" * 32)
    snapshot["messages"][0].update(type=7, image={key: identity[key] for key in ("width", "height", "md5", "bytes")}, _image_url="https://clouddisk.alibaba.com/file/redirectFileUrl.htm?secret=private")
    return record, snapshot, png


@pytest.mark.parametrize("change", [None, "md5", "bytes", "width", "alpha"])
def test_exact_image_identity_and_ambiguity(case, change):
    record, snapshot, _ = image_case(case)
    image = snapshot["messages"][0]["image"]
    if change:
        if change == "alpha":
            image.update(md5=None, pixel_sha256="wrong-alpha")
        else:
            image[change] = "different"
    result = match_outbox(record, snapshot, [])
    assert (result["status"] == "observed") is (change is None)
    if change is None:
        assert "private" not in str(result)
        competitor = {**record, "id": "other"}
        assert match_outbox(record, snapshot, [competitor])["reason"] == "ambiguous_send_attempts"
        snapshot["messages"].append({**snapshot["messages"][0], "id": "second"})
        assert match_outbox(record, snapshot, [])["reason"] == "multiple_candidate_messages"


def test_reencoded_pixels_match_after_transient_download_failure_and_are_cached(case, monkeypatch):
    record, snapshot, png = image_case(case)
    snapshot["messages"][0]["image"].update(md5="f" * 32, bytes=1234)
    clock = SimpleNamespace(monotonic=lambda: 100)
    monkeypatch.setattr(image_verification, "time", clock)
    calls = []
    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise image_verification.ImageFetchError("status_502")
        return SimpleNamespace(body=png, content_type="image/png")
    monkeypatch.setattr(image_archive, "fetch_image", fetch)
    verifier = image_verification.ImageVerifier()
    verifier.enrich(snapshot, [record])
    assert match_outbox(record, snapshot, [])["status"] == "verifying"
    verifier.enrich(snapshot, [record]); assert len(calls) == 1
    clock.monotonic = lambda: 116
    verifier.enrich(snapshot, [record])
    assert match_outbox(record, snapshot, [])["status"] == "observed"
    del snapshot["messages"][0]["image"]["pixel_sha256"]
    verifier.enrich(snapshot, [record]); assert len(calls) == 2
    assert match_outbox(record, snapshot, [])["status"] == "observed"


def test_same_pixels_with_other_file_encoding_are_competing_sends(case):
    record, snapshot, _ = image_case(case)
    other = {**record, "id": "reencoded", "image": {**record["image"], "md5": "e" * 32, "bytes": 900}}
    assert match_outbox(record, snapshot, [other])["reason"] == "ambiguous_send_attempts"
    other["baseline"] = {**other["baseline"], "ids": [snapshot["messages"][0]["id"]]}
    assert match_outbox(record, snapshot, [other])["status"] == "observed"


def test_image_api_upload_preview_and_idempotent_send(client, outbox, sdk):
    path = "/api/conversations/1/image-attachments/" + "a" * 32
    raw = picture()
    response = client.put(path, content=raw, headers={"Content-Type": "image/png"})
    assert response.status_code == 200, response.text
    attachment = response.json()["data"]
    assert client.get(path).json()["data"] == attachment
    preview = client.get(path + "/image")
    assert preview.status_code == 200 and image_content.decoded_identity(preview.content) == image_content.decoded_identity(raw)
    assert client.put(path, content=raw).json()["data"] == attachment
    assert client.put(path, content=picture(color=(90, 20, 30, 125))).status_code == 409
    payload = {"message_kind": "image", "attachment_id": "a" * 32, "action": "send", "idempotency_key": "image-key"}
    response = client.post("/api/conversations/1/messages", json=payload)
    assert response.status_code == 200, response.text
    task = response.json()["data"]["outbox"]
    assert task["image"]["sha256"] == attachment["sha256"]
    assert client.post("/api/conversations/1/messages", json=payload).json()["data"]["outbox"]["id"] == task["id"]
    assert len(outbox.queue.jobs) == 1
    assert outbox.queue.run()[0]
    override = sdk.calls[1][1]
    assert override["ChatInput_PasteImage"]["action"]["param"]["custom_action"] == "PasteChatImage"
    assert override["ChatInput_SendMessage"]["enabled"] is False
    context = account_context.get_account_context()
    metadata = ImageAttachmentStore(outbox.service.store.database_path).get("a" * 32, seller=context.self_ali_id,
        data_dir=context.data_dir, conversation_id=1)
    outbox.source.update(checked_at=time.time(), messages=[{
        "id": "msg:confirmed-image", "sender_id": f"{context.self_ali_id}@icbu", "cid": f"{context.self_ali_id}-buyer",
        "created_at": time.time(), "type": 7, "text": "", "is_system": False, "is_auto_reply": False, "extension_valid": True,
        "image": {key: metadata[key] for key in ("width", "height", "md5", "bytes")},
    }])
    observed = outbox.service.reconcile_once()
    assert len(observed) == 1 and observed[0]["matched_message_id"] == "msg:confirmed-image"
    assert client.get(f"/api/outbox/{task['id']}").json()["data"]["status"] == "observed"
    assert outbox.queue.jobs == []
    assert client.get(path.replace("/1/", "/2/")).status_code == 404
    assert client.post("/api/conversations/1/messages", json={**payload, "content": "must not be sent"}).status_code == 422


def test_image_upload_rejects_limits_and_stale_epoch_before_publication(client, outbox, monkeypatch):
    path = "/api/conversations/1/image-attachments/" + "a" * 32
    assert client.put(path, content=b"x" * (image_content.MAX_IMAGE_BYTES + 1)).status_code == 413
    assert client.put(path, content=b"invalid").status_code == 422
    started, finish = Event(), Event()
    original = conversations.prepare_image
    def blocked(raw):
        started.set()
        assert finish.wait(5)
        return original(raw)
    monkeypatch.setattr(conversations, "prepare_image", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(client.put, path, content=picture())
        try:
            assert started.wait(5)
            account_context.invalidate_account_context()
        finally:
            finish.set()
        assert work.result(5).status_code == 409
    assert not outbox.service.store.database_path.exists()


def test_reconciliation_rechecks_account_after_slow_image_download(env, monkeypatch):
    from dataclasses import replace
    from backend.app.shared.backend import outbox_service
    attach(env)
    monkeypatch.setattr(outbox_service.runner, "chat_image", lambda record: (True, "filled"))
    task = env.service.submit(env.context, 1, "buyer", "buyer-login", "", "send", "image-key", message_kind="image", attachment_id="a" * 32)
    assert env.queue.run()[0]
    before = env.context
    metadata = env.service.get(before, task["id"])["image"]
    env.source.update(checked_at=1005, messages=[{
        "id": "msg:image", "sender_id": "seller@icbu", "cid": "seller-buyer", "created_at": 1001,
        "type": 7, "text": "", "is_system": False, "is_auto_reply": False, "extension_valid": True,
        "image": {"width": metadata["width"], "height": metadata["height"], "md5": "f" * 32, "bytes": 900},
        "_image_url": "https://clouddisk.alibaba.com/image",
    }])
    def fetch(url):
        # A separate thread can acquire the outbox condition while network I/O runs.
        with ThreadPoolExecutor(max_workers=1) as pool:
            def unlocked():
                with env.service._condition:
                    env.context = replace(before, self_ali_id="other", epoch="new")
            pool.submit(unlocked).result(5)
        return SimpleNamespace(body=picture(), content_type="image/png")
    monkeypatch.setattr(image_archive, "fetch_image", fetch)
    assert env.service.reconcile_once() == []
    assert env.service.get(before, task["id"])["status"] == "verifying"
    assert env.queue.jobs == []
