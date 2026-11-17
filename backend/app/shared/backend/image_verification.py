"""Enriches image candidates outside account, source-reader and outbox locks."""

from collections import OrderedDict
import time

from backend.app.shared.backend.image_fetch import fetch_image, ImageFetchError, ImageUnavailable
from backend.app.shared.backend.send_verification import _baseline, _candidates, content_evidence
from backend.app.shared.image_content import decoded_identity


class ImageVerifier:
    def __init__(self):
        self._cache = OrderedDict()

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
                    identity = decoded_identity(fetch_image(url).body)
                except (ImageFetchError, ImageUnavailable, ValueError):
                    self._cache[key] = (None, time.monotonic() + 15)
                else:
                    self._cache[key] = (identity, 0)
                    if all(identity[k] == image[k] for k in ("width", "height")):
                        image.update(identity)
                self._cache.move_to_end(key)
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
