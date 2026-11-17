"""Adapts scoped image owners to generic CRM resources."""

import hashlib
from io import BytesIO
import json

from PIL import Image, UnidentifiedImageError

from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.image_content import decoded_identity

TEMPORARY_SECONDS = 86400


def image_namespace(seller, data_dir):
    identity = json.dumps([seller, canonical_source_dir(str(data_dir))], ensure_ascii=False)
    return "chat:" + hashlib.sha256(identity.encode()).hexdigest()


def image_attributes(data):
    """Extracts bounded display facts without imposing send-side normalization."""
    try:
        with Image.open(BytesIO(data)) as image:
            result = {"width": image.width, "height": image.height, "frames": getattr(image, "n_frames", 1)}
        try:
            result.update(decoded_identity(data))
            result["pixel_id"] = "rgba-v1-sha256:" + result["pixel_sha256"]
        except ValueError:
            pass
        return {"image": result}
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid image data") from exc


def read_authorized_image(resource_id, context):
    """Requires a live host owner as well as an SDK reference before serving bytes."""
    from backend.app.api.envelope import AppError
    from backend.app.shared.crm.paths import default_crm_database_path
    from backend.app.shared.crm.sdk import ResourceManager, ResourceUnavailable

    resources = ResourceManager(default_crm_database_path())
    try:
        resource = resources.get(resource_id)
    except ValueError as exc:
        raise AppError("图片标识无效。", status_code=422) from exc
    if resource is None or not resource.media_type.startswith("image/"):
        raise AppError("图片不存在。", status_code=404)
    namespace = image_namespace(context.self_ali_id, context.data_dir)
    with resources.transaction() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for ref in resources.list_references(namespace=namespace, resource_id=resource_id, transaction=conn):
            valid = None
            if ref.owner_type == "attachment" and "app_image_attachment" in tables:
                valid = conn.execute("SELECT 1 FROM app_image_attachment WHERE id=? AND resource_ref_id=? AND seller=? AND data_dir=?",
                    (ref.owner_id, ref.reference_id, context.self_ali_id, canonical_source_dir(context.data_dir))).fetchone()
            elif ref.owner_type == "message" and {"app_message_image", "app_inbox_message"} <= tables:
                valid = conn.execute("""SELECT 1 FROM app_message_image m JOIN app_inbox_message i
                    ON i.seller=m.seller AND i.data_dir=m.data_dir AND i.external_mid=m.external_mid AND i.sid=m.sid
                    WHERE m.external_mid=? AND m.resource_ref_id=? AND m.seller=? AND m.data_dir=?
                    AND m.status='ready' AND m.fingerprint=m.resource_fingerprint""",
                    (ref.owner_id, ref.reference_id, context.self_ali_id, canonical_source_dir(context.data_dir))).fetchone()
            if valid:
                try:
                    return resources.read_reference(ref.reference_id, namespace=namespace, transaction=conn)
                except ResourceUnavailable:
                    continue
    raise AppError("图片不存在或已不可用。", status_code=404)
