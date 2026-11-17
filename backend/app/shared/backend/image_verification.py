"""Enriches image candidates outside account, source-reader and outbox locks."""

from collections import OrderedDict
import json
import sqlite3
import time

from backend.app.shared.backend.image_fetch import ImageFetchError, ImageUnavailable
from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.backend.image_archive import get_image_archive
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.backend.send_verification import _baseline, _candidates, content_evidence
from backend.app.shared.image_content import decoded_identity


class ImageVerifier:
    def __init__(self, database_path=None):
        self._cache = OrderedDict()
        self.database_path = database_path

    def enrich(self, snapshot, records):
        if not snapshot.get("valid"):
            return
        budget = 2
        for record in records:
            if record.get("message_kind") != "image" or record.get("status") not in ("verifying", "unknown"):
                continue
            baseline = _baseline(record)
            if baseline is None or baseline["origin"] != snapshot.get("origin"):
                continue
            for message in _candidates(record, baseline, snapshot["messages"], match_content=False):
                image = message.get("image")
                if not image or content_evidence(record, message):
                    continue
                if any(image.get(key) != record["image"].get(key) for key in ("width", "height")):
                    continue
                url = message.get("_image_url")
                if not url:
                    continue
                key = (record["seller"], record["data_dir"], baseline["origin"]["source_path"], message["id"], url,
                       image.get("md5"), image.get("bytes"), image.get("width"), image.get("height"))
                cached = self._cache.get(key)
                if cached is not None:
                    identity, retry_at = cached
                    if identity is not None:
                        if all(identity[k] == image[k] for k in ("width", "height")):
                            image.update(identity)
                        continue
                    if time.monotonic() < retry_at:
                        continue
                if budget <= 0:
                    continue
                budget -= 1
                try:
                    archive = get_image_archive(self.database_path)
                    context = AccountContext(record["seller"], record["data_dir"], "verification")
                    table, mid = message["id"].split(":", 1)
                    external_mid = message_external_id(record["seller"], table, mid)
                    stored = archive.store.get(context, external_mid)
                    local = None
                    if stored:
                        source = json.loads(stored["source"])
                        if source.get("url") == url and all(source.get(k) == image.get(k) for k in ("width", "height", "md5", "bytes")):
                            local = archive.store.local(stored)
                    resource, data = local or archive.fetch_resource(context, external_mid, {
                        **image, "url": url, **{key: message.get(key) for key in ("sender_id", "cid", "created_at")},
                    })
                    cached_pixels = resource.attributes.get("image", {})
                    identity = ({key: cached_pixels[key] for key in ("width", "height", "pixel_sha256")}
                                if cached_pixels.get("pixel_id", "").startswith("rgba-v1-sha256:") else decoded_identity(data))
                except (ImageFetchError, ImageUnavailable, ValueError, OSError, sqlite3.Error):
                    self._cache[key] = (None, time.monotonic() + 15)
                else:
                    self._cache[key] = (identity, 0)
                    if all(identity[k] == image[k] for k in ("width", "height")):
                        image.update(identity)
                self._cache.move_to_end(key)
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
