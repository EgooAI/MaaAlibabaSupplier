"""Pull an authenticated diagnostic ZIP without putting credentials in arguments."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
import warnings
import zipfile

MAX_ARCHIVE_BYTES = 34 * 1024**2
MAX_TOTAL_BYTES = 32 * 1024**2
MAX_FILE_BYTES = 4 * 1024**2


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(base_url: str) -> str:
    value = urllib.parse.urlsplit(base_url)
    host = value.hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if (value.scheme not in ("http", "https") or not host or value.username is not None
            or value.password is not None or value.query or value.fragment or value.path not in ("", "/")
            or (value.scheme == "http" and not loopback)):
        raise ValueError("Use an HTTPS origin or a loopback HTTP origin, without credentials or query parameters.")
    return base_url.rstrip("/") + "/api/app/diagnostics/download"


def validate_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (not 2 <= len(entries) <= 24 or len(set(names)) != len(names)
                or not {"manifest.json", "runtime.json"}.issubset(names)
                or sum(entry.file_size for entry in entries) > MAX_TOTAL_BYTES):
            raise ValueError("Invalid diagnostic ZIP")
        for entry in entries:
            if (entry.file_size > MAX_FILE_BYTES or entry.flag_bits & 1 or entry.is_dir()
                    or (entry.filename not in ("manifest.json", "runtime.json")
                        and re.fullmatch(r"logs/\d{2}-[a-z-]+\.jsonl", entry.filename) is None)):
                raise ValueError("Invalid diagnostic ZIP entry")
        if archive.testzip() is not None:
            raise ValueError("Diagnostic ZIP integrity check failed")
        manifest = json.loads(archive.read("manifest.json"))
        runtime = json.loads(archive.read("runtime.json"))
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or not isinstance(runtime, dict) or runtime.get("schema_version") != 1):
            raise ValueError("Unsupported diagnostic ZIP")


def download(base_url: str, token: str, destination: Path) -> None:
    url = endpoint(base_url)
    if re.fullmatch(r"maa_[A-Za-z0-9_-]{43}", token) is None:
        raise ValueError("Invalid session token format")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Choose a new output file in an existing directory")
    partial = destination.with_name(destination.name + ".part")
    # Exclusive creation preserves another download's partial file on every failure.
    with partial.open("xb") as stream:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/zip"})
            with opener.open(request, timeout=60) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/zip":
                    raise ValueError("Server did not return a diagnostic ZIP")
                size = 0
                while block := response.read(min(64 * 1024, MAX_ARCHIVE_BYTES + 1 - size)):
                    size += len(block)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError("Diagnostic ZIP exceeds the size limit")
                    stream.write(block)
                expected = response.headers.get("Content-Length")
                if expected is not None and int(expected) != size:
                    raise ValueError("Diagnostic ZIP download was incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            partial.unlink(missing_ok=True)
            raise
    try:
        validate_archive(partial)
        # Link publication is atomic and refuses to overwrite an existing target.
        os.link(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New ZIP filename (parent directory must exist)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API origin; HTTPS required outside loopback")
    parser.add_argument("--token-env", help="Read the existing Bearer token from this environment variable instead of prompting")
    args = parser.parse_args()
    try:
        endpoint(args.base_url)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = os.environ.get(args.token_env, "") if args.token_env else getpass.getpass("Existing API Bearer token: ")
        download(args.base_url, token.strip(), args.output)
    except (Exception, KeyboardInterrupt):
        # HTTP errors, URLs and exception locals can contain credentials. Never echo them.
        parser.exit(1, "Diagnostic download failed; check authentication, server availability and output location.\n")
    print("Diagnostic ZIP downloaded and validated.")


if __name__ == "__main__":
    main()
