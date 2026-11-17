"""Persists image-source versions and archive work alongside committed messages."""

import hashlib
import json
import time

from backend.app.shared.crm.paths import default_crm_database_path
from backend.app.shared.crm.sdk import ResourceManager, ResourceUnavailable, sqlite_connection
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.crm.image_resources import image_namespace
from backend.app.shared.crm.image_resources import TEMPORARY_SECONDS
from backend.app.shared.images import parse_image_blob


def _row(cursor):
    row = cursor.fetchone()
    return dict(zip((field[0] for field in cursor.description), row)) if row is not None else None


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS app_message_image (
        seller TEXT NOT NULL, data_dir TEXT NOT NULL, external_mid TEXT NOT NULL, sid INTEGER NOT NULL,
        source TEXT NOT NULL, fingerprint TEXT NOT NULL, resource_ref_id TEXT, resource_fingerprint TEXT,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        lease_until REAL NOT NULL DEFAULT 0, error TEXT, updated_at REAL NOT NULL,
        PRIMARY KEY(seller,data_dir,external_mid)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS app_message_image_pending ON app_message_image(seller,data_dir,status,retry_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_image_archive_scope (
        seller TEXT NOT NULL, data_dir TEXT NOT NULL, PRIMARY KEY(seller,data_dir)
    )""")


def _register(conn, seller, directory, sid, external_mid, source, *, automatic):
    if source.get("md5"):
        source = {**source, "platform_md5": "md5:" + source["md5"]}
    encoded = json.dumps(source, sort_keys=True, allow_nan=False)
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    old = _row(conn.execute("SELECT * FROM app_message_image WHERE seller=? AND data_dir=? AND external_mid=?",
                           (seller, directory, external_mid)))
    if old and old["fingerprint"] == fingerprint and old["sid"] == sid:
        return
    status = "pending" if automatic or (old and old["status"] != "remote") else "remote"
    if not source.get("url"):
        status = "unavailable"
    conn.execute("""INSERT INTO app_message_image
        (seller,data_dir,external_mid,sid,source,fingerprint,status,updated_at) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(seller,data_dir,external_mid) DO UPDATE SET sid=excluded.sid,source=excluded.source,
        fingerprint=excluded.fingerprint,status=excluded.status,attempts=0,retry_at=0,lease_until=0,error=NULL,
        updated_at=excluded.updated_at""", (seller, directory, external_mid, sid, encoded, fingerprint, status, time.time()))


def write_message_images(session, seller, data_dir, messages):
    """Joins the source-sync transaction; its first snapshot is a history baseline."""
    conn = sqlite_connection(session)
    _schema(conn)
    directory = canonical_source_dir(str(data_dir))
    automatic = conn.execute("SELECT 1 FROM app_image_archive_scope WHERE seller=? AND data_dir=?", (seller, directory)).fetchone() is not None
    for message in messages:
        content = message.content
        if content.get("user_content_type") != 7:
            # A changed type must not retain an accessible old image mapping.
            if conn.execute("SELECT 1 FROM app_message_image WHERE seller=? AND data_dir=? AND external_mid=?",
                            (seller, directory, message.external_mid)).fetchone():
                _register(conn, seller, directory, message.sid, message.external_mid, {}, automatic=False)
            continue
        raw = content.get("content_latin1")
        image = parse_image_blob(raw.encode("latin1") if isinstance(raw, str) else None)
        source = {"url": image.url, **image.identity()} if image else {}
        source.update({key: content.get(key) for key in ("sender_id", "cid", "created_at")})
        _register(conn, seller, directory, message.sid, message.external_mid, source, automatic=automatic)
    conn.execute("INSERT OR IGNORE INTO app_image_archive_scope VALUES (?,?)", (seller, directory))


class MessageImageStore:
    def __init__(self, database_path=None):
        self.resources = ResourceManager(database_path if database_path is not None else default_crm_database_path())
        self.database_path = self.resources.database_path

    def initialize(self):
        self.resources.initialize()
        with self.resources.transaction(write=True) as conn:
            _schema(conn)

    @staticmethod
    def _key(context, external_mid):
        return (context.self_ali_id, canonical_source_dir(context.data_dir), external_mid)

    def get(self, context, external_mid, *, conn=None):
        if not self.database_path.is_file():
            return None
        with self.resources.transaction(conn) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='app_message_image'").fetchone():
                return None
            return _row(db.execute("SELECT * FROM app_message_image WHERE seller=? AND data_dir=? AND external_mid=?", self._key(context, external_mid)))

    def adopt_legacy(self, context, sid, external_mid, image):
        """Adopts a legacy source only when its directory provenance is unambiguous."""
        self.initialize()
        with self.resources.transaction(write=True) as conn:
            rows = conn.execute("SELECT data_dir,sid FROM app_inbox_message WHERE seller=? AND external_mid=?",
                                (context.self_ali_id, external_mid)).fetchall()
            if len(rows) != 1 or tuple(rows[0]) != (canonical_source_dir(context.data_dir), sid):
                return None
            if self.get(context, external_mid, conn=conn) is None:
                _register(conn, *self._key(context, external_mid)[:2], sid, external_mid,
                          {"url": image.url, **image.identity()}, automatic=False)
            return self.get(context, external_mid, conn=conn)

    def local(self, record):
        if not record["resource_ref_id"] or record["resource_fingerprint"] != record["fingerprint"]:
            return None
        try:
            return self.resources.read_reference(record["resource_ref_id"], namespace=image_namespace(record["seller"], record["data_dir"]))
        except ResourceUnavailable:
            return None

    def claim(self, record):
        with self.resources.transaction(write=True) as conn:
            now = time.time()
            lease = now + 120
            claimed = conn.execute("""UPDATE app_message_image SET status='fetching',lease_until=?,updated_at=?
                WHERE seller=? AND data_dir=? AND external_mid=? AND fingerprint=? AND lease_until<=?""",
                (lease, now, record["seller"], record["data_dir"], record["external_mid"], record["fingerprint"], now)).rowcount == 1
            if claimed:
                record["lease_until"] = lease
            return claimed

    def complete(self, record, resource_id):
        with self.resources.transaction(write=True) as conn:
            current = _row(conn.execute("SELECT * FROM app_message_image WHERE seller=? AND data_dir=? AND external_mid=?",
                                       (record["seller"], record["data_dir"], record["external_mid"])))
            if (current is None or current["fingerprint"] != record["fingerprint"]
                    or current["lease_until"] != record["lease_until"]):
                return False
            old = self.resources.get_reference(current["resource_ref_id"], transaction=conn)
            ref = self.resources.link(resource_id, namespace=image_namespace(record["seller"], record["data_dir"]),
                owner_type="message", owner_id=record["external_mid"],
                expected_resource_id=old.resource_id if old else None,
                retain_previous_until=time.time() + TEMPORARY_SECONDS, transaction=conn)
            conn.execute("""UPDATE app_message_image SET resource_ref_id=?,resource_fingerprint=fingerprint,
                status='ready',attempts=0,retry_at=0,lease_until=0,error=NULL,updated_at=?
                WHERE seller=? AND data_dir=? AND external_mid=?""",
                (ref.reference_id, time.time(), record["seller"], record["data_dir"], record["external_mid"]))
            return True

    def failed(self, record, reason, *, unavailable=False):
        with self.resources.transaction(write=True) as conn:
            conn.execute("""UPDATE app_message_image SET status=?,attempts=attempts+1,retry_at=?,lease_until=0,
                error=?,updated_at=? WHERE seller=? AND data_dir=? AND external_mid=? AND fingerprint=? AND lease_until=?""",
                ("unavailable" if unavailable else "retry_wait", time.time() + min(3600, 15 * 2 ** min(record["attempts"], 8)),
                 reason, time.time(), record["seller"], record["data_dir"], record["external_mid"], record["fingerprint"], record["lease_until"]))

    def pending(self, context, *, limit=2):
        if not self.database_path.is_file():
            return []
        with self.resources.transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_message_image'").fetchone():
                return []
            return [row[0] for row in conn.execute("""SELECT external_mid FROM app_message_image
                WHERE seller=? AND data_dir=? AND status IN ('pending','retry_wait','fetching')
                AND retry_at<=? AND lease_until<=? ORDER BY updated_at LIMIT ?""",
                (context.self_ali_id, canonical_source_dir(context.data_dir), time.time(), time.time(), limit))]

    def observation(self, context):
        if not self.database_path.is_file():
            return {"counts": {}}
        with self.resources.transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_message_image'").fetchone():
                return {"counts": {}}
            return {"counts": dict(conn.execute("SELECT status,count(*) FROM app_message_image WHERE seller=? AND data_dir=? GROUP BY status",
                (context.self_ali_id, canonical_source_dir(context.data_dir))))}
